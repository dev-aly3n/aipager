"""The pinned "needs you" bar (roadmap 8.31).

One pinned message per scope chat, on v2 installs and legacy alike. Its
FIRST line (the only one Telegram shows in the bar at the top of the chat)
says what most needs the user: a session waiting on a prompt, else who is
working, else that all is idle. Nothing on it changes without a state
change: no clock, cost, context % or model. An edit goes out only when the
rendered text or keyboard changed, at most once per PINNED_MIN_EDIT_GAP per
chat, and a change inside the gap is coalesced into exactly one trailing
edit that is never lost.

Every row runs on the harness's virtual loop, and every Telegram call goes
through the REAL limiter (:class:`_PinBot` below), so "zero calls" means
zero calls on the wire. Nothing in ``asyncio`` is patched.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from unittest.mock import MagicMock

import pytest
from telegram import InlineKeyboardMarkup
from telegram.error import BadRequest

from aipager import config
from aipager.bot.flood import MUTE
from aipager.bot.flood_budget import FloodSkipped
from aipager.scope import Scope
from aipager.state import SessionRegistry, Status

CHAT = 222333444
GROUP = -1001234567890
PINNED = 9999
GAP = config.PINNED_MIN_EDIT_GAP
EPS = 1e-3


#: The bot's own user id, and the operator's.
BOT_ID = 777000
OPERATOR_ID = 4242


class _User:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _Msg:
    def __init__(self, message_id: int, from_id: int = BOT_ID) -> None:
        self.message_id = message_id
        self.from_user = _User(from_id)


class _Chat:
    def __init__(self, pinned_message) -> None:
        self.pinned_message = pinned_message


class _PinBot:
    """A PTB double: every call is metered by the real limiter, and every
    call that reached the wire is recorded as ``(endpoint, chat, msg_id,
    text, reply_markup, t)``. ``fail[endpoint]`` is a list of exception
    factories raised, one per call, AFTER the gate admitted the call
    (Telegram's answer). ``skip[endpoint]`` raises ``FloodSkipped`` BEFORE
    the wire, the way the limiter refuses a skip-class call."""

    def __init__(self, limiter, clock) -> None:
        self._limiter = limiter
        self._clock = clock
        self.calls: list[tuple] = []
        self.fail: dict[str, list] = {}
        self.skip: dict[str, int] = {}
        self._next_id = 5000
        #: kwargs of every send_message, e.g. ``disable_notification``.
        self.send_kwargs: list[dict] = []
        #: kwargs of every pin_chat_message, ``rate_limit_args`` included.
        self.pin_kwargs: list[dict] = []
        #: ``rate_limit_args`` of every get_chat.
        self.get_chat_args: list = []
        #: ``rate_limit_args`` of every edit_message_text, in call order.
        self.edit_args: list = []
        #: seconds every edit stays in flight before it reaches the gate.
        self.edit_delay = 0.0
        #: the same for every send (a send waiting in the limiter).
        self.send_delay = 0.0
        #: what getChat says is the chat's pinned message (``None``: no
        #: pinned message). A successful pin moves it, like Telegram.
        self.pinned_id: int | None = PINNED
        #: who sent the message getChat names as pinned.
        self.pinned_from = BOT_ID
        #: the bot's own id (``ExtBot.id``).
        self.id = BOT_ID

    async def _gate(self, endpoint, chat_id, rate_limit_args, result, *,
                    msg_id=None, text=None, markup=None):
        if self.skip.get(endpoint):
            self.skip[endpoint] -= 1
            raise FloodSkipped(chat_id, endpoint)

        async def _call():
            self.calls.append((endpoint, chat_id, msg_id, text, markup,
                               self._clock()))
            queue = self.fail.get(endpoint)
            if queue:
                raise queue.pop(0)()
            return result

        return await self._limiter.process_request(
            callback=_call, args=(), kwargs={}, endpoint=endpoint,
            data={"chat_id": chat_id}, rate_limit_args=rate_limit_args)

    async def send_message(self, chat_id=None, text=None, *,
                           rate_limit_args=None, reply_markup=None, **kw):
        self._next_id += 1
        self.send_kwargs.append(kw)
        if self.send_delay:
            await asyncio.sleep(self.send_delay)
        return await self._gate("sendMessage", chat_id, rate_limit_args,
                                _Msg(self._next_id), msg_id=self._next_id,
                                text=text, markup=reply_markup)

    async def edit_message_text(self, text=None, chat_id=None,
                                message_id=None, *, rate_limit_args=None,
                                reply_markup=None, **_kw):
        self.edit_args.append(rate_limit_args)
        if self.edit_delay:
            await asyncio.sleep(self.edit_delay)
        return await self._gate("editMessageText", chat_id, rate_limit_args,
                                True, msg_id=message_id, text=text,
                                markup=reply_markup)

    async def pin_chat_message(self, chat_id=None, message_id=None, *,
                               rate_limit_args=None, **kw):
        self.pin_kwargs.append(dict(kw, rate_limit_args=rate_limit_args))
        result = await self._gate("pinChatMessage", chat_id, rate_limit_args,
                                  True, msg_id=message_id)
        self.pinned_id, self.pinned_from = message_id, BOT_ID
        return result

    async def get_chat(self, chat_id=None, *, rate_limit_args=None, **_kw):
        self.get_chat_args.append(rate_limit_args)
        pinned = (None if self.pinned_id is None
                  else _Msg(self.pinned_id, self.pinned_from))
        return await self._gate("getChat", chat_id, rate_limit_args,
                                _Chat(pinned))

    async def delete_message(self, chat_id=None, message_id=None, *,
                             rate_limit_args=None, **_kw):
        return await self._gate("deleteMessage", chat_id, rate_limit_args,
                                True, msg_id=message_id)

    # -- reading the record --------------------------------------------------

    def of(self, endpoint, chat=CHAT) -> list[tuple]:
        return [c for c in self.calls if c[0] == endpoint and c[1] == chat]

    def pinned_edits(self, chat=CHAT, msg_id=PINNED) -> list[tuple]:
        return [c for c in self.of("editMessageText", chat) if c[2] == msg_id]


@pytest.fixture
def pbot(vloop, vlimiter):
    return _PinBot(vlimiter, vloop.time)


@pytest.fixture
def legacy(monkeypatch):
    """A legacy single-chat install (no aipager.yaml, CHAT_ID set)."""
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", str(CHAT))


def _bot(mk_bot, pbot, *, scopes=None, pinned=PINNED, chat=CHAT):
    bot = mk_bot(scopes=scopes)
    bot._app.bot = pbot
    if pinned:
        bot.registry.pinned_msg_ids[chat] = pinned
    return bot


def _session(bot, label, status=Status.IDLE, chat=CHAT, kind="dm"):
    sess = bot.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.status = status
    sess.scope_chat_id = chat
    sess.scope_kind = kind
    return sess


def _wait(sess, summary="Bash: rm -rf build"):
    """Put *sess* on a permission prompt, the way notify's inline branch
    records one."""
    sess.status = Status.INTERACTIVE
    sess.pending_permission = {"tool_summary": summary, "tool_info": None,
                               "wait_started_at": 0.0}


def _run(vloop, steps):
    """``steps``: ``(delay, coroutine factory)`` pairs, on the virtual loop,
    then enough virtual time for any trailing refresh to land."""
    async def main():
        for delay, step in steps:
            await asyncio.sleep(delay)
            await step()
        await asyncio.sleep(3 * GAP)
    vloop.run_until_complete(main())


def _first(text: str) -> str:
    return text.split("\n", 1)[0]


def _buttons(markup) -> list:
    if markup is None:
        return []
    return [b for row in markup.inline_keyboard for b in row]


# ── R1: one pin per scope chat, v2 included ─────────────────────────────────

def test_v2_multi_scope_install_gets_a_pin_per_chat(
    mk_bot, pbot, vloop, monkeypatch,
):
    """A v2 install (``scopes`` set, CHAT_ID empty) with a DM scope and a
    group scope, one session in each: each chat gets its own message,
    pinned silently, listing only its own session; both ids are stored.

    Before 8.31 this returned early for any ``scopes`` install."""
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", "")
    scopes = [Scope(chat_id=CHAT, kind="dm", label="me"),
              Scope(chat_id=GROUP, kind="group", label="team")]
    bot = _bot(mk_bot, pbot, scopes=scopes, pinned=0)
    _session(bot, "mine", Status.BUSY)
    _session(bot, "theirs", Status.IDLE, chat=GROUP, kind="group")

    _run(vloop, [(0.0, bot.refresh_pinned)])

    for chat, own, other in ((CHAT, "mine", "theirs"),
                             (GROUP, "theirs", "mine")):
        sends = pbot.of("sendMessage", chat)
        assert len(sends) == 1, (chat, pbot.calls)
        assert own in sends[0][3] and other not in sends[0][3]
        pins = pbot.of("pinChatMessage", chat)
        assert [p[2] for p in pins] == [sends[0][2]]
        assert bot.registry.pinned_msg_ids[chat] == sends[0][2]
    # The send is silent, not only the pin (no notification per new bar).
    assert [k.get("disable_notification") for k in pbot.send_kwargs] == [
        True, True]


def test_a_chat_is_pinned_once(mk_bot, pbot, vloop, legacy):
    """R5: the first send + pin happens once; every later change is an
    edit of that message."""
    bot = _bot(mk_bot, pbot, pinned=0)
    s0 = _session(bot, "s0", Status.BUSY)

    async def _idle():
        s0.status = Status.IDLE
        await bot.refresh_pinned()

    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 1.0, _idle)])
    assert len(pbot.of("sendMessage")) == 1
    assert len(pbot.of("pinChatMessage")) == 1
    msg = bot.registry.pinned_msg_ids[CHAT]
    assert len(pbot.pinned_edits(msg_id=msg)) == 1


def test_legacy_team_mode_keeps_its_opt_out(mk_bot, pbot, vloop, legacy):
    bot = _bot(mk_bot, pbot)
    bot.team = MagicMock()
    _session(bot, "s0", Status.BUSY)
    _run(vloop, [(0.0, bot.refresh_pinned)])
    assert pbot.calls == []


# ── R2: the first line ──────────────────────────────────────────────────────

def test_first_line_priority_waiting_then_working_then_idle(
    mk_bot, pbot, vloop, legacy,
):
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0", Status.IDLE)
    s1 = _session(bot, "s1", Status.IDLE)
    s2 = _session(bot, "s2", Status.IDLE)
    assert _first(bot._render_pinned(CHAT)[0]) == "💤 3 idle"

    s0.status = Status.BUSY
    s2.status = Status.BUSY
    assert _first(bot._render_pinned(CHAT)[0]) == "⚙️ 2 working · 1 idle"

    _wait(s1)
    assert (_first(bot._render_pinned(CHAT)[0])
            == "⏳ s1 needs you - Bash: rm -rf build")

    _wait(s2, "Edit: main.py")
    assert (_first(bot._render_pinned(CHAT)[0])
            == "⏳ s1 needs you - Bash: rm -rf build (+1 more)")


def test_many_working_sessions_are_counted_not_named(mk_bot, pbot, legacy):
    """The names are on the session lines below; the first line counts."""
    bot = _bot(mk_bot, pbot)
    for label in ("a", "b", "c", "d", "e"):
        _session(bot, label, Status.BUSY)
    assert _first(bot._render_pinned(CHAT)[0]) == "⚙️ 5 working"


def test_one_line_per_live_session_with_its_state_word(mk_bot, pbot, legacy):
    """In label order, GONE left out — and the waiting session the first
    line already names is not listed a second time."""
    bot = _bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)
    _wait(_session(bot, "s1"))
    _session(bot, "s2", Status.IDLE)
    _session(bot, "s3", Status.UNKNOWN)
    _session(bot, "gone", Status.GONE)
    lines = bot._render_pinned(CHAT)[0].split("\n")[1:]
    assert [ln for ln in lines if ln] == ["• <b>s0</b> (working)",
                                          "• <b>s2</b> (idle)",
                                          "• <b>s3</b> (starting)"]


def test_only_this_chats_sessions_are_listed(mk_bot, pbot, legacy):
    bot = _bot(mk_bot, pbot)
    _session(bot, "here", Status.BUSY)
    _session(bot, "elsewhere", Status.BUSY, chat=111222333)
    text = bot._render_pinned(CHAT)[0]
    assert "here" in text and "elsewhere" not in text
    assert _first(text) == "⚙️ here (working)"


def test_flood_lines_follow_the_warning_regime_and_hourly_minimal(
    mk_bot, pbot, vloop, vlimiter, legacy,
):
    """``🐢`` shows while the chat's 8.30 warning regime is active and
    ``⏸`` while hourly-minimal is, each on its own line below the first,
    and each goes when its regime ends."""
    bot = _bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)
    slow = "🐢 slow mode after a Telegram warning"
    paused = "⏸ card updates paused (hourly limit)"
    seen: dict = {}

    def _lines():
        return bot._render_pinned(CHAT)[0].split("\n")

    async def main():
        seen["calm"] = _lines()
        vlimiter.note_retry_after(CHAT, 5.0)
        share = vlimiter.hourly_usage(CHAT)["ornament_budget"]
        vlimiter.restore([{"chat_id": CHAT,
                           "hourly": [[vloop.wall() - 60.0, share + 1, 0]]}])
        seen["both"] = _lines()
        await asyncio.sleep(config.FLOOD_HOURLY_WINDOW + 60.0)
        seen["slow_only"] = _lines()
        await asyncio.sleep(config.FLOOD_WARNING_HOURS * 3600.0)
        seen["after"] = _lines()

    vloop.run_until_complete(main())
    assert slow not in seen["calm"] and paused not in seen["calm"]
    assert seen["both"][1:3] == [slow, paused], seen["both"]
    assert slow in seen["slow_only"] and paused not in seen["slow_only"]
    assert slow not in seen["after"] and paused not in seen["after"]


def test_the_paused_line_reaches_the_bar_in_minimal_mode(
    mk_bot, pbot, vloop, vlimiter, legacy,
):
    """Hourly-minimal refuses every skip-class ornament, the bar's own
    edits included. The ONE edit that puts the "⏸" line on the bar goes
    out ESSENTIAL (like the busy card's "updates paused" line), so the bar
    can say it; a later change inside minimal mode is an ornament again
    and waits.

    Mutation: send the regime-edge edit as an ornament and the "⏸" line
    never reaches the bar."""
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0", Status.BUSY)
    paused = "⏸ card updates paused (hourly limit)"

    async def _minimal():
        share = vlimiter.hourly_usage(CHAT)["ornament_budget"]
        vlimiter.restore([{"chat_id": CHAT,
                           "hourly": [[vloop.wall() - 60.0, share + 1, 0]]}])
        assert vlimiter.hourly_usage(CHAT)["minimal"] is True
        await bot.refresh_pinned()

    async def _idle():
        s0.status = Status.IDLE
        await bot.refresh_pinned()

    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 1.0, _minimal),
                 (GAP + 1.0, _idle)])
    edits = pbot.pinned_edits()
    assert len(edits) == 2, [(e[3], e[5]) for e in edits]
    assert paused in edits[1][3].split("\n")
    assert _first(edits[1][3]) == "⚙️ s0 (working)"


def test_no_volatile_fields_a_cost_ctx_model_change_is_zero_edits(
    mk_bot, pbot, vloop, legacy,
):
    """A hook that only moves cost, context % or model, and the refresh
    that follows it (after the gap), is zero edits: none of them is on the
    bar. The hook itself no longer asks for a refresh either.

    Mutation: put cost (or ctx %, or model) back on a session line and the
    second refresh edits."""
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0", Status.BUSY)
    s0.model_name = "opus"

    async def _drift():
        s0.last_cost_usd += 1.25
        s0.last_token_pct = 57
        s0.model_name = "sonnet"
        s0.busy_started_at = vloop.time() - 3600.0
        await bot.notify(s0, "pinned_update", {})
        await bot.refresh_pinned()

    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 1.0, _drift),
                 (GAP + 1.0, _drift)])
    edits = pbot.pinned_edits()
    assert len(edits) == 1, edits
    text = edits[0][3]
    for volatile in ("$", "%", "opus", "sonnet", "ctx"):
        assert volatile not in text, text


def test_a_hook_does_not_refresh_the_bar(mk_bot, pbot, vloop, legacy):
    """R4: the per-hook refresh in ``notify`` is gone. A status change made
    with no refresh (and no monitor tick) sends nothing."""
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0", Status.BUSY)

    async def _hook():
        s0.status = Status.IDLE
        await bot.notify(s0, "pinned_update", {})

    _run(vloop, [(0.0, _hook)])
    assert pbot.pinned_edits() == []


# ── R4: the 30 s gap, the trailing edit, skips and mutes ────────────────────

def test_three_transitions_in_five_seconds_are_one_edit_plus_one_trailing(
    mk_bot, pbot, vloop, legacy,
):
    """busy → waiting → idle inside five seconds: the first goes out at
    once, the rest coalesce into exactly one trailing edit at the end of
    the gap, and that edit shows the FINAL state.

    Mutations: drop the gap and there are three edits; drop the trailing
    refresh and the last edit shows a stale state."""
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0", Status.IDLE)

    async def _busy():
        s0.status = Status.BUSY
        await bot.refresh_pinned()

    async def _waiting():
        _wait(s0)
        await bot.refresh_pinned()

    async def _idle():
        s0.status = Status.IDLE
        s0.pending_permission = None
        await bot.refresh_pinned()

    _run(vloop, [(0.0, _busy), (2.0, _waiting), (1.5, _idle)])
    edits = pbot.pinned_edits()
    assert len(edits) == 2, [(e[3], e[5]) for e in edits]
    assert edits[1][5] - edits[0][5] >= GAP - EPS
    assert edits[1][5] - edits[0][5] <= GAP + 1.0
    assert _first(edits[0][3]) == "⚙️ s0 (working)"
    assert _first(edits[1][3]) == "💤 s0 (idle)"


def test_the_trailing_edit_carries_the_final_state(mk_bot, pbot, vloop, legacy):
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0", Status.BUSY)

    async def _waiting():
        _wait(s0)
        await bot.refresh_pinned()

    _run(vloop, [(0.0, bot.refresh_pinned), (3.0, _waiting)])
    edits = pbot.pinned_edits()
    assert len(edits) == 2, edits
    assert _first(edits[1][3]) == "⏳ s0 needs you - Bash: rm -rf build"


def test_an_unchanged_render_is_never_edited(mk_bot, pbot, vloop, legacy):
    bot = _bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)
    _run(vloop, [(0.0, bot.refresh_pinned), (GAP * 2, bot.refresh_pinned),
                 (GAP * 2, bot.refresh_pinned)])
    assert len(pbot.pinned_edits()) == 1


def test_a_skipped_edit_is_retried_by_the_trailing_refresh(
    mk_bot, pbot, vloop, legacy,
):
    """The budget refuses the skip-class edit (``FloodSkipped``). Nothing
    else calls a refresh; the trailing one still delivers it.

    Mutation: return without rescheduling on SKIPPED and the bar never
    shows the change."""
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0", Status.BUSY)
    pbot.skip["editMessageText"] = 1
    _run(vloop, [(0.0, bot.refresh_pinned)])
    edits = pbot.pinned_edits()
    assert len(edits) == 1, edits
    assert _first(edits[0][3]) == "⚙️ s0 (working)"
    assert s0.status == Status.BUSY


def test_the_edit_keeps_the_ornament_skip_class(mk_bot, vloop, legacy):
    """R4: the bar's edit is declared skip-class ORNAMENT, so it counts in
    the hourly budget as an ornament and is dropped when the budget is
    tight."""
    from aipager.bot.flood_budget import PRIORITY_ORNAMENT
    bot = mk_bot()
    bot.registry.pinned_msg_ids[CHAT] = PINNED
    _session(bot, "s0", Status.BUSY)
    seen: dict = {}

    async def _edit(*_a, rate_limit_args=None, **_kw):
        seen["args"] = rate_limit_args
        return True

    bot._app.bot.edit_message_text = _edit
    vloop.run_until_complete(bot.refresh_pinned())
    assert seen["args"]["kind"] == "skip"
    assert seen["args"]["class"] == PRIORITY_ORNAMENT


def test_no_edit_while_muted_and_a_catch_up_at_the_lift(
    mk_bot, pbot, vloop, vlimiter, legacy,
):
    """A ban is on the chat. A transition during it sends nothing; the bar
    catches up the moment the ban lifts (not up to a gap later), and with
    the state as of the lift.

    Arming the mute also drops the chat's earned rate to the floor, which
    keeps every skip-class ornament out for a long while after (8.29/8.30,
    tested there). The limiter is reset right after arming so this row
    sees only the mute.

    Mutation: drop the mute check and the catch-up waits for the next gap
    boundary after the lift (a retry every 30 s through the ban)."""
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0", Status.BUSY)
    out: dict = {}

    async def _ban_then_change():
        MUTE.mute(CHAT, 100.0)
        vlimiter.reset()
        out["from"] = vloop.time()
        s0.status = Status.IDLE
        await bot.refresh_pinned()

    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 5.0, _ban_then_change),
                 (150.0, lambda: asyncio.sleep(0))])
    edits = pbot.pinned_edits()
    assert len(edits) == 2, edits
    lift = out["from"] + 100.0
    assert not [e for e in pbot.calls if out["from"] <= e[5] < lift]
    assert lift <= edits[1][5] <= lift + 2.0, (edits[1][5] - lift)
    assert _first(edits[1][3]) == "💤 s0 (idle)"


# ── R1/R5: group pin failure, a deleted pin ─────────────────────────────────

def test_a_group_pin_failure_deletes_the_message_and_disables_the_chat(
    mk_bot, pbot, vloop, monkeypatch,
):
    """The bot is not an admin in the group: the pin fails. The status
    message it just sent is deleted (never left in the group's
    scroll-back), and the chat gets no bar for the daemon's lifetime.

    Mutation: keep the old "edit in place" fallback and the message stays
    and is edited later."""
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", "")
    scopes = [Scope(chat_id=GROUP, kind="group", label="team")]
    bot = _bot(mk_bot, pbot, scopes=scopes, pinned=0)
    s0 = _session(bot, "s0", Status.BUSY, chat=GROUP, kind="group")
    pbot.fail["pinChatMessage"] = [
        lambda: BadRequest("Not enough rights to manage pinned messages")]

    async def _later():
        s0.status = Status.IDLE
        await bot.refresh_pinned()

    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 1.0, _later),
                 (3600.0 * 2, bot.refresh_pinned)])
    sent = pbot.of("sendMessage", GROUP)
    assert len(sent) == 1, pbot.calls
    assert [d[2] for d in pbot.of("deleteMessage", GROUP)] == [sent[0][2]]
    assert GROUP not in bot.registry.pinned_msg_ids
    assert pbot.of("editMessageText", GROUP) == []


def test_a_dm_pin_failure_still_edits_in_place(mk_bot, pbot, vloop, legacy):
    """Only a GROUP deletes on a failed pin; a DM keeps the old fallback."""
    bot = _bot(mk_bot, pbot, pinned=0)
    s0 = _session(bot, "s0", Status.BUSY)
    pbot.fail["pinChatMessage"] = [lambda: BadRequest("boom")]

    async def _later():
        s0.status = Status.IDLE
        await bot.refresh_pinned()

    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 1.0, _later)])
    assert pbot.of("deleteMessage") == []
    msg = bot.registry.pinned_msg_ids[CHAT]
    assert len(pbot.pinned_edits(msg_id=msg)) == 1


def test_a_deleted_pinned_message_is_recreated_at_most_once_an_hour(
    mk_bot, pbot, vloop, legacy,
):
    """The user deletes the pinned message; the next edit fails with
    "message to edit not found". A new one is sent and pinned. Deleted
    again within the hour: nothing new until the hour is up."""
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0", Status.BUSY)
    gone = lambda: BadRequest("Message to edit not found")  # noqa: E731
    pbot.fail["editMessageText"] = [gone, gone]
    flips = {"n": 0}

    async def _flip():
        flips["n"] += 1
        s0.status = Status.IDLE if s0.status == Status.BUSY else Status.BUSY
        await bot.refresh_pinned()

    steps = [(0.0, _flip)] + [(GAP + 1.0, _flip) for _ in range(6)]
    steps += [(3600.0, _flip)]
    _run(vloop, steps)
    sends = pbot.of("sendMessage")
    assert len(sends) == 2, [(c[0], c[5]) for c in pbot.calls]
    assert sends[1][5] - sends[0][5] >= 3600.0 - EPS
    assert len(pbot.of("pinChatMessage")) == 2
    assert bot.registry.pinned_msg_ids[CHAT] == sends[1][2]


# ── R3: the buttons ─────────────────────────────────────────────────────────

def _tap(pbot, data, message_id=PINNED):
    toasts: list = []

    async def _answer(text=None, **_kw):
        toasts.append(text)

    async def _edit(*a, **k):
        toasts.append(("edited", a[0] if a else k.get("text")))
        return None

    query = MagicMock()
    query.data = data
    query.answer = _answer
    query.edit_message_text = _edit
    query.edit_message_reply_markup = _edit
    query.message = MagicMock()
    query.message.message_id = message_id
    query.message.text = ""
    query.message.chat = MagicMock()
    query.message.chat.id = CHAT
    query.from_user = MagicMock()
    query.from_user.id = CHAT
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = MagicMock()
    update.effective_chat.id = CHAT
    update.effective_chat.type = "private"
    return update, toasts


def _answer_button(bot, label):
    _text, markup = bot._render_pinned(CHAT)
    hits = [b for b in _buttons(markup) if b.text == f"Answer {label}"]
    assert len(hits) == 1, [b.text for b in _buttons(markup)]
    return hits[0]


def test_answer_buttons_one_per_waiting_session_max_three(mk_bot, pbot, legacy):
    bot = _bot(mk_bot, pbot)
    for label in ("a", "b", "c", "d"):
        _wait(_session(bot, label))
    _session(bot, "e", Status.BUSY)
    _text, markup = bot._render_pinned(CHAT)
    answers = [b for b in _buttons(markup) if b.text.startswith("Answer ")]
    assert [b.text for b in answers] == ["Answer a", "Answer b", "Answer c"]
    for b in answers:
        assert b.callback_data.startswith("_:")
        assert len(b.callback_data.encode()) <= 64


def test_no_answer_button_without_a_waiting_session(mk_bot, pbot, legacy):
    bot = _bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)
    _text, markup = bot._render_pinned(CHAT)
    assert not [b for b in _buttons(markup) if b.text.startswith("Answer")]


def test_the_answer_button_resends_the_live_prompt(
    mk_bot, pbot, vloop, legacy, monkeypatch,
):
    """Tapping "Answer s0" sends the session's pending prompt again, with
    its answer keyboard, as a fresh message at the bottom of the chat."""
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0")
    _wait(s0, "Bash: make deploy")
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        lambda *_a, **_k: _true())
    button = _answer_button(bot, "s0")
    update, toasts = _tap(pbot, button.callback_data)
    vloop.run_until_complete(bot._handle_callback(update, MagicMock()))
    sends = pbot.of("sendMessage")
    assert len(sends) == 1, pbot.calls
    _e, _c, msg_id, text, markup, _t = sends[0]
    assert "make deploy" in text
    labels = [b.text for b in _buttons(markup)]
    assert "✅ Allow" in labels and "❌ Deny" in labels
    assert "already answered" not in toasts
    # The copy is routable: a reply to it reaches s0.
    assert bot.registry.get_session_by_msg(msg_id, CHAT) is s0


def test_the_answer_button_resends_a_separate_message_prompt(
    mk_bot, pbot, vloop, legacy,
):
    """A prompt that went out as a separate message (no busy card to put
    it in) is re-sent exactly as it was rendered."""
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0")
    s0.status = Status.INTERACTIVE
    kb = InlineKeyboardMarkup([])
    s0.pending_prompt_msg = {"text": "🔐 <b>s0</b> · Permission needed",
                             "keyboard": kb, "summary": "Bash: ls"}
    assert (_first(bot._render_pinned(CHAT)[0])
            == "⏳ s0 needs you - Bash: ls")
    update, _toasts = _tap(pbot, _answer_button(bot, "s0").callback_data)
    vloop.run_until_complete(bot._handle_callback(update, MagicMock()))
    sends = pbot.of("sendMessage")
    assert [(s[3], s[4]) for s in sends] == [
        ("🔐 <b>s0</b> · Permission needed", kb)]


def test_an_already_answered_prompt_gets_the_toast(
    mk_bot, pbot, vloop, legacy,
):
    """The bar still shows "Answer s0" but the prompt was answered
    meanwhile: a toast, and nothing is sent."""
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0")
    _wait(s0)
    button = _answer_button(bot, "s0")
    s0.status = Status.BUSY
    s0.pending_permission = None
    update, toasts = _tap(pbot, button.callback_data)
    vloop.run_until_complete(bot._handle_callback(update, MagicMock()))
    assert pbot.of("sendMessage") == []
    assert "already answered" in toasts


def test_a_stale_tap_on_a_resent_copy_injects_nothing(
    mk_bot, pbot, vloop, legacy, monkeypatch,
):
    """The re-sent copy's Allow is tapped after the prompt was answered on
    the original: "already answered", and no keystroke reaches the
    session (the separate-message fallback would otherwise send Enter)."""
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0")
    _wait(s0)
    keys: list = []

    async def _send_keys(*a, **_k):
        keys.append(a)
        return True

    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        lambda *_a, **_k: _true())
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _send_keys)
    update, _t = _tap(pbot, _answer_button(bot, "s0").callback_data)
    vloop.run_until_complete(bot._handle_callback(update, MagicMock()))
    copy = pbot.of("sendMessage")[0]
    allow = [b for b in _buttons(copy[4]) if b.text == "✅ Allow"][0]
    s0.status = Status.BUSY
    s0.pending_permission = None
    update, toasts = _tap(pbot, allow.callback_data, message_id=copy[2])
    vloop.run_until_complete(bot._handle_callback(update, MagicMock()))
    assert keys == []
    assert "already answered" in toasts


async def _true():
    return True


def test_open_app_button_only_when_the_mini_app_is_configured(
    mk_bot, pbot, monkeypatch,
):
    """Present in the DM when a Mini App URL is known, absent without one,
    and never in a group (Telegram rejects a whole keyboard carrying a
    web_app button there)."""
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", "")
    scopes = [Scope(chat_id=CHAT, kind="dm", label="me"),
              Scope(chat_id=GROUP, kind="group", label="team")]
    bot = _bot(mk_bot, pbot, scopes=scopes, pinned=0)
    _session(bot, "s0", Status.BUSY)
    _session(bot, "g0", Status.BUSY, chat=GROUP, kind="group")

    def _apps(chat):
        return [b for b in _buttons(bot._render_pinned(chat)[1])
                if b.web_app is not None]

    bot._miniapp_url = ""
    assert _apps(CHAT) == [] and _apps(GROUP) == []
    bot._miniapp_url = "https://example.test/app"
    assert [b.web_app.url for b in _apps(CHAT)] == ["https://example.test/app"]
    assert _apps(GROUP) == []


# ── persistence ─────────────────────────────────────────────────────────────

def test_the_legacy_pinned_msg_id_migrates_into_the_per_chat_map(
    tmp_path, monkeypatch,
):
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({
        "version": 1, "last_active_session": "", "pinned_msg_id": 77,
        "msg_map": {}, "sessions": {}}))
    monkeypatch.setattr("aipager.state.SESSION_STATE_FILE", str(path))
    monkeypatch.setattr("aipager.config.CHAT_ID", str(CHAT))
    reg = SessionRegistry()
    reg.load()
    assert reg.pinned_msg_ids == {CHAT: 77}
    reg.pinned_msg_ids[GROUP] = 88
    reg.save()
    saved = json.loads(path.read_text())
    assert saved["pinned_msg_ids"] == {str(CHAT): 77, str(GROUP): 88}
    assert "pinned_msg_id" not in saved
    again = SessionRegistry()
    again.load()
    assert again.pinned_msg_ids == {CHAT: 77, GROUP: 88}


def test_a_legacy_pinned_msg_id_without_a_chat_is_dropped(tmp_path, monkeypatch):
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({
        "version": 1, "last_active_session": "", "pinned_msg_id": 77,
        "msg_map": {}, "sessions": {}}))
    monkeypatch.setattr("aipager.state.SESSION_STATE_FILE", str(path))
    monkeypatch.setattr("aipager.config.CHAT_ID", "")
    reg = SessionRegistry()
    reg.load()
    assert reg.pinned_msg_ids == {}


# ── the driver: the session monitor's tick ─────────────────────────────────

def test_the_monitor_tick_refreshes_the_bar_after_every_scan(
    mk_bot, pbot, vloop, legacy, monkeypatch,
):
    """``SessionMonitor.on_tick`` runs after every scan, even one that
    failed, and ``pinned_tick`` turns a status a hook set (no call site of
    its own) into an edit within a scan.

    Mutation: drop the ``on_tick`` call from ``_loop`` and the bar never
    shows the change."""
    from aipager import session_monitor as sm

    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0", Status.BUSY)
    monitor = sm.SessionMonitor(bot.registry, bot.notify)
    scans = {"n": 0}

    async def _scan():
        scans["n"] += 1
        if scans["n"] == 2:
            raise RuntimeError("a scan that failed")

    monkeypatch.setattr(monitor, "_scan", _scan)
    monitor.on_tick = bot.pinned_tick

    async def main():
        task = asyncio.ensure_future(monitor._loop())
        await asyncio.sleep(GAP + 1.0)
        s0.status = Status.IDLE          # set by a hook: no refresh call
        await asyncio.sleep(3 * sm.PANE_POLL_INTERVAL)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    vloop.run_until_complete(main())
    edits = pbot.pinned_edits()
    assert [_first(e[3]) for e in edits] == ["⚙️ s0 (working)",
                                             "💤 s0 (idle)"], edits


def test_pinned_tick_never_runs_two_refreshes_at_once(
    mk_bot, pbot, vloop, legacy,
):
    bot = _bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)

    async def main():
        await bot.pinned_tick()
        first = bot._pinned_task
        await bot.pinned_tick()
        assert bot._pinned_task is first
        await first

    vloop.run_until_complete(main())
    assert len(pbot.pinned_edits()) == 1


# ── review iteration 1 ─────────────────────────────────────────────────────

def _copy_of_prompt(bot, pbot, vloop, s0, monkeypatch, keys):
    """Tap "Answer s0" and return the re-sent copy's record."""
    async def _send_keys(*a, **_k):
        keys.append(a)
        return True

    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        lambda *_a, **_k: _true())
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _send_keys)
    update, _t = _tap(pbot, _answer_button(bot, "s0").callback_data)
    vloop.run_until_complete(bot._handle_callback(update, MagicMock()))
    return pbot.of("sendMessage")[-1]


def _allow_on(copy):
    return [b for b in _buttons(copy[4]) if b.text == "✅ Allow"][0]


def test_a_copy_of_prompt_a_never_answers_prompt_b(
    mk_bot, pbot, vloop, legacy, monkeypatch,
):
    """rev-iter1-001. The bar re-sends prompt A; A is answered elsewhere;
    the session stops on prompt B. The copy's Allow must not answer B.

    Mutation: guard on the status alone (not the prompt's identity) and
    the tap types Enter into B's dialog."""
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0")
    _wait(s0, "Bash: prompt A")
    keys: list = []
    copy = _copy_of_prompt(bot, pbot, vloop, s0, monkeypatch, keys)
    # A answered on the busy card / in the terminal, then prompt B.
    s0.status = Status.BUSY
    s0.pending_permission = None
    _wait(s0, "Bash: prompt B")
    update, toasts = _tap(pbot, _allow_on(copy).callback_data,
                          message_id=copy[2])
    vloop.run_until_complete(bot._handle_callback(update, MagicMock()))
    assert keys == []
    assert "already answered" in toasts


def test_the_copy_answers_its_own_prompt_once_and_loses_its_buttons(
    mk_bot, pbot, vloop, legacy, monkeypatch,
):
    """The copy answers the prompt it shows; it is then edited to the
    verdict, and a second tap on it is refused.

    Mutations: skip the copy's edit after an inline answer and it keeps
    its buttons; pop its registry entry and the second tap types into the
    next prompt."""
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0")
    _wait(s0)
    keys: list = []
    copy = _copy_of_prompt(bot, pbot, vloop, s0, monkeypatch, keys)
    update, toasts = _tap(pbot, _allow_on(copy).callback_data,
                          message_id=copy[2])
    vloop.run_until_complete(bot._handle_callback(update, MagicMock()))
    assert keys == [("claude-s0", "Enter")]
    assert [t for t in toasts if isinstance(t, tuple)], toasts
    assert s0.status == Status.BUSY
    _wait(s0, "Bash: the next one")
    update, toasts = _tap(pbot, _allow_on(copy).callback_data,
                          message_id=copy[2])
    vloop.run_until_complete(bot._handle_callback(update, MagicMock()))
    assert keys == [("claude-s0", "Enter")]
    assert "already answered" in toasts


def test_the_original_separate_prompt_is_refused_once_answered_elsewhere(
    mk_bot, pbot, vloop, legacy, monkeypatch,
):
    """rev-iter1-002. A separate-message prompt is answered through the
    bar's copy; the session then waits on a new prompt. A tap on the
    ORIGINAL prompt's Allow must not answer the new one."""
    from aipager.bot.dashboard import current_prompt_token
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0")
    s0.status = Status.INTERACTIVE
    kb = bot._build_permission_keyboard(s0)
    s0.pending_prompt_msg = {"text": "🔐 <b>s0</b> · Permission needed",
                             "keyboard": kb, "summary": "Bash: ls"}
    original = 4000
    bot.register_prompt_surface(CHAT, original, s0,
                                current_prompt_token(s0, create=True))
    keys: list = []
    copy = _copy_of_prompt(bot, pbot, vloop, s0, monkeypatch, keys)
    update, _t = _tap(pbot, _allow_on(copy).callback_data,
                      message_id=copy[2])
    vloop.run_until_complete(bot._handle_callback(update, MagicMock()))
    assert keys == [("claude-s0", "Enter")]
    s0.status = Status.INTERACTIVE          # the next prompt
    s0.pending_prompt_msg = {"text": "🔐 next", "keyboard": kb,
                             "summary": "Bash: rm"}
    allow = [b for b in _buttons(kb) if b.text == "✅ Allow"][0]
    update, toasts = _tap(pbot, allow.callback_data, message_id=original)
    vloop.run_until_complete(bot._handle_callback(update, MagicMock()))
    assert keys == [("claude-s0", "Enter")]
    assert "already answered" in toasts


def test_a_kicked_bot_stops_calling_the_chat(mk_bot, pbot, vloop, legacy):
    """rev-iter1-003. ``Forbidden`` (kicked, blocked) on the bar's edit:
    no further call to that chat, however the state changes.

    Mutation: let Forbidden fall through and every gap retries."""
    from telegram.error import Forbidden
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0", Status.BUSY)
    pbot.fail["editMessageText"] = [
        lambda: Forbidden("bot was kicked from the group chat")]

    async def _flip():
        s0.status = Status.IDLE if s0.status == Status.BUSY else Status.BUSY
        await bot.refresh_pinned()

    _run(vloop, [(0.0, bot.refresh_pinned)]
         + [(GAP + 1.0, _flip) for _ in range(5)])
    assert len(pbot.calls) == 1, pbot.calls
    assert CHAT not in bot.registry.pinned_msg_ids


def test_chat_not_found_is_not_a_deleted_message(mk_bot, pbot, vloop, legacy):
    """"Chat not found" disables the chat; it is not the user deleting the
    pinned message, so nothing is re-sent."""
    bot = _bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)
    pbot.fail["editMessageText"] = [lambda: BadRequest("Chat not found")]
    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 1.0, bot.refresh_pinned)])
    assert pbot.of("sendMessage") == []
    assert len(pbot.calls) == 1


def test_rate_floor_minimal_mode_shows_a_paused_line(
    mk_bot, pbot, vloop, vlimiter, legacy,
):
    """rev-iter1-004. Minimal mode's other way in, the earned rate under
    the floor, also puts a "⏸" line on the bar."""
    bot = _bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)
    vlimiter.note_ban(CHAT, 1.0)
    assert vlimiter.minimal_mode(CHAT) is True
    assert vlimiter.hourly_usage(CHAT)["minimal"] is False
    lines = bot._render_pinned(CHAT)[0].split("\n")
    assert "⏸ card updates paused (rate limit)" in lines


def test_a_change_during_an_edit_in_flight_is_not_lost(
    mk_bot, pbot, vloop, legacy,
):
    """A refresh arrives while the chat's edit is still in flight (the
    tick overlapping a call site). It is not run concurrently, and not
    dropped: the change reaches the bar as the trailing edit.

    Mutation: drop the ``again`` re-run and the change never shows."""
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0", Status.BUSY)
    pbot.edit_delay = 1.0

    async def main():
        first = asyncio.ensure_future(bot.refresh_pinned())
        await asyncio.sleep(0.5)
        s0.status = Status.IDLE
        await bot.refresh_pinned()
        await first
        await asyncio.sleep(3 * GAP)

    vloop.run_until_complete(main())
    edits = pbot.pinned_edits()
    assert [_first(e[3]) for e in edits] == ["⚙️ s0 (working)",
                                             "💤 s0 (idle)"], edits


def test_a_pin_held_back_by_the_budget_is_retried(
    mk_bot, pbot, vloop, legacy,
):
    """The pin itself is refused by the budget (not by Telegram): the
    message stays, and the next refresh that gets through pins it.

    Mutation: drop the ``pin_pending`` retry and the bar is never
    pinned."""
    bot = _bot(mk_bot, pbot, pinned=0)
    s0 = _session(bot, "s0", Status.BUSY)
    pbot.skip["pinChatMessage"] = 1

    async def _idle():
        s0.status = Status.IDLE
        await bot.refresh_pinned()

    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 1.0, _idle)])
    msg = bot.registry.pinned_msg_ids[CHAT]
    assert [p[2] for p in pbot.of("pinChatMessage")] == [msg]
    assert pbot.of("deleteMessage") == []


def test_a_separate_message_prompt_is_bound_to_its_prompt(
    mk_bot, pbot, vloop, legacy,
):
    """rev-iter1-002, through the real path: a permission prompt with no
    busy card to go into is sent as its own message, and that message is
    registered as bound to THIS prompt, so a tap on it after the prompt is
    over is refused.

    Mutation: drop the registration in notify and the message is not a
    guarded surface at all."""
    from aipager.bot.dashboard import current_prompt_token
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0")
    s0.status = Status.INTERACTIVE
    s0.busy_msg_id = None
    vloop.run_until_complete(bot.notify(s0, "permission_prompt", {
        "tool_info": {"name": "Bash", "summary": "Bash: ls", "input": {},
                      "detail": ""}}))
    sent = [c for c in pbot.of("sendMessage") if "Permission needed" in c[3]]
    assert len(sent) == 1, pbot.calls
    token = current_prompt_token(s0)
    assert token is not None
    assert bot._resent_prompts[(CHAT, sent[0][2])] == ("claude-s0", token)
    assert s0.pending_prompt_msg["summary"] == "Bash: ls"


# ── review iteration 2 ─────────────────────────────────────────────────────

def _patch_keys(monkeypatch, keys):
    async def _send_keys(*a, **_k):
        keys.append(a)
        return True

    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        lambda *_a, **_k: _true())
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _send_keys)


def _separate_prompt_raced(bot, pbot, vloop, s0, during):
    """notify sends prompt A as a separate message; the send waits 5 s in
    the limiter, and *during* runs while it waits. Returns A's record."""
    s0.status = Status.INTERACTIVE
    s0.busy_msg_id = None
    pbot.send_delay = 5.0

    async def main():
        send = asyncio.ensure_future(bot.notify(s0, "permission_prompt", {
            "tool_info": {"name": "Bash", "summary": "Bash: prompt A",
                          "input": {}, "detail": ""}}))
        await asyncio.sleep(1.0)
        during()
        await send
        pbot.send_delay = 0.0

    vloop.run_until_complete(main())
    return [c for c in pbot.of("sendMessage") if "prompt A" in (c[3] or "")][0]


def test_probe_a_prompt_b_shown_while_a_is_still_being_sent(
    mk_bot, pbot, vloop, legacy, monkeypatch,
):
    """rev-iter2-001 (a). A is answered and B shown inline while A's send
    still waits: A's message must not be bound to B. Tapping its Allow is
    refused and no keystroke reaches the PTY.

    Mutation: read the token after the send (not when A is recorded) and
    A's message is bound to B's prompt."""
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0")
    keys: list = []
    _patch_keys(monkeypatch, keys)

    def _a_answered_b_shown():
        s0.status = Status.BUSY
        _wait(s0, "Bash: prompt B")
        s0.busy_msg_id = 71

    a = _separate_prompt_raced(bot, pbot, vloop, s0, _a_answered_b_shown)
    update, toasts = _tap(pbot, _allow_on(a).callback_data, message_id=a[2])
    vloop.run_until_complete(bot._handle_callback(update, MagicMock()))
    assert keys == []
    assert any(t in ("already answered", "this prompt has expired")
               for t in toasts), toasts


def test_probe_b_a_answered_in_the_terminal_while_being_sent(
    mk_bot, pbot, vloop, legacy, monkeypatch,
):
    """rev-iter2-001 (b). A is answered in the terminal while its send
    waits; later B is shown inline and has no token stamped yet. A tap on
    A's Allow is refused, zero keystrokes.

    Mutation: let a None token match a None token and the tap types."""
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0")
    keys: list = []
    _patch_keys(monkeypatch, keys)

    def _a_answered():
        s0.status = Status.BUSY
        s0.pending_prompt_msg = None

    a = _separate_prompt_raced(bot, pbot, vloop, s0, _a_answered)
    _wait(s0, "Bash: prompt B")
    s0.busy_msg_id = 71
    update, toasts = _tap(pbot, _allow_on(a).callback_data, message_id=a[2])
    vloop.run_until_complete(bot._handle_callback(update, MagicMock()))
    assert keys == []
    assert any(t in ("already answered", "this prompt has expired")
               for t in toasts), toasts


def test_a_surface_registered_without_a_token_never_matches(
    mk_bot, pbot, vloop, legacy, monkeypatch,
):
    """Fail closed: a surface bound to no prompt identity, tapped while a
    prompt that has none stamped yet is pending, is refused — None never
    equals None.

    Mutation: drop the None checks and the tap types into the prompt."""
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0")
    keys: list = []
    _patch_keys(monkeypatch, keys)
    _wait(s0, "Bash: prompt B")
    s0.busy_msg_id = 71
    bot.register_prompt_surface(CHAT, 4001, s0, None)
    allow = [b for b in _buttons(bot._build_permission_keyboard(s0))
             if b.text == "✅ Allow"][0]
    update, toasts = _tap(pbot, allow.callback_data, message_id=4001)
    vloop.run_until_complete(bot._handle_callback(update, MagicMock()))
    assert keys == []
    assert "already answered" in toasts


def test_after_a_restart_an_old_copy_cannot_answer_the_new_prompt(
    mk_bot, pbot, vloop, legacy, monkeypatch,
):
    """Restart warning (review-2 002). The surface registry is not
    persisted, so after a restart an old copy or separate prompt is
    unknown. While a prompt is pending, a tap on any message that is
    neither the busy card it is shown in nor registered to it is refused:
    "this prompt has expired". The busy card itself still answers.

    Mutation: drop the fail-closed branch and the old copy types Enter."""
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0")
    keys: list = []
    _patch_keys(monkeypatch, keys)
    bot._resent_prompts.clear()                   # the restart
    _wait(s0, "Bash: the new prompt")
    s0.busy_msg_id = 71
    allow = [b for b in _buttons(bot._build_permission_keyboard(s0))
             if b.text == "✅ Allow"][0]
    update, toasts = _tap(pbot, allow.callback_data, message_id=4242)
    vloop.run_until_complete(bot._handle_callback(update, MagicMock()))
    assert keys == []
    assert "this prompt has expired" in toasts
    # A separate-message prompt pending after the restart: same rule.
    s0.pending_permission = None
    s0.pending_prompt_msg = {"text": "x", "keyboard": None, "summary": "y",
                             "prompt_token": 999}
    update, toasts = _tap(pbot, allow.callback_data, message_id=4243)
    vloop.run_until_complete(bot._handle_callback(update, MagicMock()))
    assert keys == []
    assert "this prompt has expired" in toasts
    # The busy card answers its own inline prompt as before.
    _wait(s0, "Bash: the new prompt")
    update, toasts = _tap(pbot, allow.callback_data, message_id=71)
    vloop.run_until_complete(bot._handle_callback(update, MagicMock()))
    assert keys == [("claude-s0", "Enter")]


def test_safe_truncate_marks_only_a_real_cut():
    """Review-2 003: "…" means something was cut. Text that fits comes
    back unchanged (it used to gain a "…" on every re-sent copy)."""
    from aipager.bot.transport import _safe_truncate
    assert _safe_truncate("short <b>x</b>", 100, True) == "short <b>x</b>"
    assert _safe_truncate("short", 100, False) == "short"
    assert _safe_truncate("abcdef", 3, False) == "abc…"
    cut = _safe_truncate("<b>abcdef</b>", 6, True)
    assert cut.endswith("…") and "</b>" in cut


def test_a_resent_inline_copy_has_no_stray_ellipsis(
    mk_bot, pbot, vloop, legacy, monkeypatch,
):
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0")
    _wait(s0, "Bash: make deploy")
    keys: list = []
    copy = _copy_of_prompt(bot, pbot, vloop, s0, monkeypatch, keys)
    assert not copy[3].endswith("…"), copy[3]


# ── review iteration 2: the create path, toasts, pruning ───────────────────

@pytest.mark.parametrize("refusal", [
    lambda: __import__("telegram.error", fromlist=["Forbidden"]).Forbidden(
        "bot was blocked by the user"),
    lambda: BadRequest("Chat not found"),
], ids=["forbidden", "chat-not-found"])
def test_a_chat_that_refuses_the_first_send_gets_no_bar(
    mk_bot, pbot, vloop, legacy, refusal,
):
    """The FIRST send (not an edit) is refused for good: the chat is
    disabled, and no later change calls it again.

    Mutation: drop the create path's disable and each gap re-sends."""
    bot = _bot(mk_bot, pbot, pinned=0)
    s0 = _session(bot, "s0", Status.BUSY)
    pbot.fail["sendMessage"] = [refusal]

    async def _flip():
        s0.status = Status.IDLE if s0.status == Status.BUSY else Status.BUSY
        await bot.refresh_pinned()

    _run(vloop, [(0.0, bot.refresh_pinned)]
         + [(GAP + 1.0, _flip) for _ in range(4)] + [(4000.0, _flip)])
    assert len(pbot.calls) == 1, pbot.calls
    assert bot._pinned[CHAT].disabled is True


def test_a_refused_first_send_backs_off_for_the_recreate_interval(
    mk_bot, pbot, vloop, legacy,
):
    """Telegram refuses the message itself (a BadRequest that is not about
    the chat): the same send would be refused again, so the next try is an
    hour later, not every gap."""
    bot = _bot(mk_bot, pbot, pinned=0)
    s0 = _session(bot, "s0", Status.BUSY)
    pbot.fail["sendMessage"] = [lambda: BadRequest("Can't parse entities")]

    async def _flip():
        s0.status = Status.IDLE if s0.status == Status.BUSY else Status.BUSY
        await bot.refresh_pinned()

    _run(vloop, [(0.0, bot.refresh_pinned)]
         + [(GAP + 1.0, _flip) for _ in range(10)] + [(3600.0, _flip)])
    sends = pbot.of("sendMessage")
    assert len(sends) == 2, [(c[0], c[5]) for c in pbot.calls]
    assert sends[1][5] - sends[0][5] >= config.PINNED_RECREATE_MIN_INTERVAL - EPS


def test_a_network_blip_on_the_first_send_retries_in_a_minute(
    mk_bot, pbot, vloop, legacy,
):
    """Review-2 004: a transient failure (network, timeout, 5xx) is tried
    again on the trailing refresh a minute later — not after the gap (the
    tick would hammer a failing network) and not after an hour.

    Mutation: treat it like a refusal and the chat has no bar for an
    hour."""
    from telegram.error import TimedOut
    bot = _bot(mk_bot, pbot, pinned=0)
    _session(bot, "s0", Status.BUSY)
    pbot.fail["sendMessage"] = [lambda: TimedOut()]

    async def _tick_for(seconds):
        end = vloop.time() + seconds
        while vloop.time() < end:
            await bot.refresh_pinned()
            await asyncio.sleep(2.0)

    _run(vloop, [(0.0, lambda: _tick_for(120.0))])
    sends = pbot.of("sendMessage")
    assert len(sends) == 2, [(c[0], c[5]) for c in pbot.calls]
    assert 60.0 - EPS <= sends[1][5] - sends[0][5] <= 62.5
    assert bot.registry.pinned_msg_ids[CHAT] == sends[1][2]


def _answer_tap(bot, pbot, vloop, message_id=PINNED, message=True):
    update, toasts = _tap(pbot, _answer_button(bot, "s0").callback_data,
                          message_id=message_id)
    if not message:
        update.callback_query.message = None
    vloop.run_until_complete(bot._handle_callback(update, MagicMock()))
    return toasts


def test_answer_toast_when_the_resend_is_skipped(mk_bot, pbot, vloop, legacy):
    bot = _bot(mk_bot, pbot)
    _wait(_session(bot, "s0"))
    pbot.skip["sendMessage"] = 1
    toasts = _answer_tap(bot, pbot, vloop)
    assert "Busy - try again in a moment" in toasts
    assert pbot.of("sendMessage") == []


def test_answer_toast_when_the_tap_has_no_message(mk_bot, pbot, vloop, legacy):
    bot = _bot(mk_bot, pbot)
    _wait(_session(bot, "s0"))
    toasts = _answer_tap(bot, pbot, vloop, message=False)
    assert "Open the chat to answer" in toasts
    assert pbot.calls == []


def test_answer_toast_when_there_is_nothing_to_resend(
    mk_bot, pbot, vloop, legacy,
):
    """INTERACTIVE with no prompt recorded (a prompt that never reached
    the chat and left nothing behind): say so, send nothing."""
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, "s0")
    _wait(s0)
    button = _answer_button(bot, "s0")
    s0.pending_permission = None
    s0.pending_prompt_msg = None
    update, toasts = _tap(pbot, button.callback_data)
    vloop.run_until_complete(bot._handle_callback(update, MagicMock()))
    assert ("The prompt can't be re-sent - answer it in the terminal"
            in toasts), toasts
    assert pbot.calls == []


def test_a_chat_removed_from_the_scopes_loses_its_bar_id(
    mk_bot, pbot, vloop, monkeypatch,
):
    """Review-2 extra: the group was dropped from ``aipager.yaml``. Its bar
    id is forgotten on the next refresh and nothing is sent there.

    Mutation: drop the pruning and the id stays forever."""
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", "")
    scopes = [Scope(chat_id=CHAT, kind="dm", label="me")]
    bot = _bot(mk_bot, pbot, scopes=scopes)
    bot.registry.pinned_msg_ids[GROUP] = 777
    _session(bot, "s0", Status.BUSY)
    _run(vloop, [(0.0, bot.refresh_pinned)])
    assert bot.registry.pinned_msg_ids == {CHAT: PINNED}
    assert [c for c in pbot.calls if c[1] == GROUP] == []


def test_a_pending_trailing_refresh_never_recreates_a_removed_chats_bar(
    mk_bot, pbot, vloop, monkeypatch,
):
    """rev-iter3-002. A group's change lands inside its gap, so a trailing
    refresh is pending; then the group is dropped from the scopes (SIGUSR1
    reloads them live). When the trailing refresh would have fired,
    nothing is sent to the group.

    Mutation: drop both the prune's cancel and ``_refresh_pinned_chat``'s
    scope check, and the trailing refresh edits the removed chat."""
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", "")
    scopes = [Scope(chat_id=CHAT, kind="dm", label="me"),
              Scope(chat_id=GROUP, kind="group", label="team")]
    bot = _bot(mk_bot, pbot, scopes=scopes)
    bot.registry.pinned_msg_ids[GROUP] = 777
    g0 = _session(bot, "g0", Status.BUSY, chat=GROUP, kind="group")

    async def _change_in_the_gap():
        g0.status = Status.IDLE
        await bot.refresh_pinned()
        assert bot._pinned[GROUP].trailing is not None

    async def _drop_the_group():
        bot.scopes = [scopes[0]]
        await bot.refresh_pinned()

    _run(vloop, [(0.0, bot.refresh_pinned), (3.0, _change_in_the_gap),
                 (1.0, _drop_the_group)])
    to_group = [c for c in pbot.calls if c[1] == GROUP]
    assert len(to_group) == 1, to_group          # the first edit only
    assert GROUP not in bot.registry.pinned_msg_ids


# ── each fact once (8.31 follow-up, 2026-09-24) ─────────────────────────────
#
# Telegram's pinned preview runs the bar's lines together. A first line
# naming the working sessions above a list that names them again read
# "⚙️ 1 working — shop_api • shop_api — working" live.

@pytest.mark.parametrize("status, expected", [
    (Status.BUSY, "⚙️ solo (working)"),
    (Status.IDLE, "💤 solo (idle)"),
    (Status.UNKNOWN, "🔄 solo (starting)"),
    (Status.INTERACTIVE, "⏳ solo needs you - Bash: rm -rf build"),
])
def test_one_live_session_is_a_single_line(mk_bot, pbot, legacy,
                                           status, expected):
    bot = _bot(mk_bot, pbot)
    sess = _session(bot, "solo", status)
    if status == Status.INTERACTIVE:
        _wait(sess)
    _session(bot, "old", Status.GONE)
    assert bot._render_pinned(CHAT)[0] == expected


def test_one_waiting_session_with_no_summary_is_still_one_line(
    mk_bot, pbot, legacy,
):
    bot = _bot(mk_bot, pbot)
    _session(bot, "solo", Status.INTERACTIVE)
    assert bot._render_pinned(CHAT)[0] == "⏳ solo needs you"


def test_two_sessions_count_on_top_and_name_below(mk_bot, pbot, legacy):
    bot = _bot(mk_bot, pbot)
    _session(bot, "a", Status.BUSY)
    _session(bot, "b", Status.IDLE)
    assert bot._render_pinned(CHAT)[0] == (
        "⚙️ 1 working · 1 idle\n"
        "• <b>a</b> (working)\n"
        "• <b>b</b> (idle)")


def test_three_sessions_count_every_state_in_a_fixed_order(
    mk_bot, pbot, legacy,
):
    bot = _bot(mk_bot, pbot)
    _session(bot, "a", Status.IDLE)
    _session(bot, "b", Status.UNKNOWN)
    _session(bot, "c", Status.BUSY)
    assert bot._render_pinned(CHAT)[0] == (
        "⚙️ 1 working · 1 idle · 1 starting\n"
        "• <b>a</b> (idle)\n"
        "• <b>b</b> (starting)\n"
        "• <b>c</b> (working)")

    bot.registry.get("claude-c").status = Status.IDLE
    assert _first(bot._render_pinned(CHAT)[0]) == "🔄 2 idle · 1 starting"

    for s in bot.registry.all_sessions().values():
        s.status = Status.IDLE
    assert _first(bot._render_pinned(CHAT)[0]) == "💤 3 idle"


def test_needs_you_with_several_sessions_names_the_waiting_one_once(
    mk_bot, pbot, legacy,
):
    bot = _bot(mk_bot, pbot)
    _wait(_session(bot, "a"))
    _wait(_session(bot, "b"), "Edit: main.py")
    _session(bot, "c", Status.BUSY)
    assert bot._render_pinned(CHAT)[0] == (
        "⏳ a needs you - Bash: rm -rf build (+1 more)\n"
        "• <b>b</b> (needs you)\n"
        "• <b>c</b> (working)")


_STATES = (Status.BUSY, Status.IDLE, Status.INTERACTIVE, Status.UNKNOWN)
_LABELS = ("alpha", "bravo", "charlie")


@pytest.mark.parametrize("agents", [0, 2])
@pytest.mark.parametrize("states", [
    combo for n in (1, 2, 3) for combo in itertools.product(_STATES, repeat=n)
])
def test_no_label_is_repeated_for_any_combination_of_states(
    mk_bot, pbot, legacy, states, agents,
):
    """Every live session is on the bar exactly once, whatever the mix —
    counted over the lines run together, as Telegram's preview shows them.
    The labels share no substring with each other, the state words or the
    summary, so a count is an exact occurrence count.

    With background agents running (roadmap 8.41) each session says its
    count once, on its own line, and no agent is ever named."""
    bot = _bot(mk_bot, pbot)
    for label, status in zip(_LABELS, states):
        sess = _session(bot, label, status)
        if status == Status.INTERACTIVE:
            _wait(sess, "Bash: ls")
        for i in range(agents):
            sess.bg_agent_started(f"{label}-{i}", "pipeline-runner", 1.0)
    text = bot._render_pinned(CHAT)[0]
    preview = text.replace("\n", " ")
    for label in _LABELS[:len(states)]:
        assert preview.count(label) == 1, preview
    if len(states) == 1:
        assert "\n" not in text
    assert "pipeline-runner" not in preview
    if agents:
        assert preview.count(f"{agents} agents running") == len(states)
        # Each count sits on its own session's line, never on another's.
        for line in text.split("\n"):
            assert line.count("agents running") <= 1, text
    else:
        assert "agents running" not in preview


def test_an_unchanged_render_of_several_sessions_is_never_edited(
    mk_bot, pbot, vloop, legacy,
):
    """The volume guard under the new first line: refreshes that render
    what the bar already shows cost nothing."""
    bot = _bot(mk_bot, pbot)
    _session(bot, "a", Status.BUSY)
    _session(bot, "b", Status.IDLE)
    _wait(_session(bot, "c"))
    _run(vloop, [(0.0, bot.refresh_pinned), (GAP * 2, bot.refresh_pinned),
                 (GAP * 2, bot.refresh_pinned)])
    assert len(pbot.pinned_edits()) == 1


# ── background agents still running (roadmap 8.41) ──────────────────────────

def test_a_waiting_lone_session_says_its_agents_on_the_one_line(
    mk_bot, pbot, legacy,
):
    bot = _bot(mk_bot, pbot)
    sess = _session(bot, "solo")
    _wait(sess)
    sess.bg_agent_started("a1", "pipeline-runner", 1.0)
    assert bot._render_pinned(CHAT)[0] == (
        "⏳ solo needs you (1 agent running) - Bash: rm -rf build")


def test_background_agents_move_the_bar_only_when_their_count_does(
    mk_bot, pbot, vloop, legacy,
):
    """0 → 1 is an edit, one agent swapped for another is none (the bar
    counts, never names), 1 → 0 is an edit. Every step is a gap apart."""
    bot = _bot(mk_bot, pbot)
    sess = _session(bot, "solo", Status.IDLE)

    def _step(start=(), stop=()):
        async def _go():
            for aid in stop:
                sess.bg_agent_stopped(aid, 1.0)
            for aid in start:
                sess.bg_agent_started(aid, aid.upper(), 1.0)
            await bot.refresh_pinned()
        return _go

    _run(vloop, [(0.0, bot.refresh_pinned),
                 (GAP * 2, _step(start=("a1",))),
                 (GAP * 2, _step(start=("a2",), stop=("a1",))),
                 (GAP * 2, _step(stop=("a2",)))])
    assert [e[3] for e in pbot.pinned_edits()] == [
        "💤 solo (idle)",
        "💤 solo (idle, 1 agent running)",
        "💤 solo (idle)",
    ]


def test_agents_starting_inside_the_gap_coalesce_into_one_trailing_edit(
    mk_bot, pbot, vloop, legacy,
):
    """Three agents launched within five seconds: the first count goes out
    at once, the rest ride ONE trailing edit showing the final count."""
    bot = _bot(mk_bot, pbot)
    sess = _session(bot, "solo", Status.IDLE)

    def _start(aid):
        async def _go():
            sess.bg_agent_started(aid, "pipeline-runner", 1.0)
            await bot.refresh_pinned()
        return _go

    _run(vloop, [(0.0, _start("a1")), (2.0, _start("a2")),
                 (1.5, _start("a3"))])
    edits = pbot.pinned_edits()
    assert [e[3] for e in edits] == [
        "💤 solo (idle, 1 agent running)",
        "💤 solo (idle, 3 agents running)",
    ]
    assert edits[1][5] - edits[0][5] >= GAP - EPS


# ── the App button after a new Mini App address (2026-10-15) ────────────────
#
# A managed quick tunnel gets a new hostname on every start. The operator's
# phone kept a pinned bar whose App button held the dead address while the
# menu button had the new one: getChat named no pinned message at all, and
# the bar's edit was a skip-class ornament.

URL_A = "https://alpha.example.test"
URL_B = "https://bravo.example.test"


def _dm_bot(mk_bot, pbot, *, url=URL_A, pinned=PINNED, group=False):
    """A v2 install with one DM scope (and a group scope when *group*):
    the DM's bar carries the App button for *url*."""
    scopes = [Scope(chat_id=CHAT, kind="dm", label="me")]
    if group:
        scopes.append(Scope(chat_id=GROUP, kind="group", label="team"))
    bot = _bot(mk_bot, pbot, scopes=scopes, pinned=pinned)
    bot._miniapp_url = url
    return bot


def _app_urls(markup) -> list[str]:
    return [b.web_app.url for b in _buttons(markup) if b.web_app is not None]


def _is_essential(args) -> bool:
    from aipager.bot.flood_budget import PRIORITY_ESSENTIAL
    return args is None or (args.get("class") == PRIORITY_ESSENTIAL
                            and args.get("kind") != "skip")


def _is_skip_ornament(args) -> bool:
    from aipager.bot.flood_budget import PRIORITY_ORNAMENT
    return (args is not None and args.get("kind") == "skip"
            and args.get("class") == PRIORITY_ORNAMENT)


@pytest.fixture
def no_chat_id(monkeypatch):
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", "")


def test_a_new_app_address_on_the_bar_is_an_essential_edit(
    mk_bot, pbot, vloop, no_chat_id,
):
    """The bar shows address A. The Mini App moves to B: the edit that
    puts B on the button is ESSENTIAL (an ornament would be refused by a
    tight budget, leaving the dead address). The next edit that changes
    only the state is a skip-class ornament again: one per change.

    Mutation: drop ``app_edge`` and the B edit is a skip-class ornament."""
    bot = _dm_bot(mk_bot, pbot)
    s0 = _session(bot, "s0", Status.BUSY)

    async def _move():
        bot._miniapp_url = URL_B
        await bot.refresh_pinned()

    async def _idle():
        s0.status = Status.IDLE
        await bot.refresh_pinned()

    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 1.0, _move),
                 (GAP + 1.0, _idle)])
    edits = pbot.pinned_edits()
    assert len(edits) == 3, edits
    assert all(u.startswith(URL_A) for u in _app_urls(edits[0][4]))
    assert [u[:len(URL_B)] for u in _app_urls(edits[1][4])] == [URL_B]
    assert _is_essential(pbot.edit_args[1]), pbot.edit_args
    assert _is_skip_ornament(pbot.edit_args[2]), pbot.edit_args


def test_the_first_edit_after_a_start_with_an_app_button_is_essential(
    mk_bot, pbot, vloop, no_chat_id,
):
    """After a restart the daemon does not know which address the bar's
    button holds (last run's tunnel, most likely dead): its first edit
    that carries an App button is ESSENTIAL.

    Mutation: treat an unknown address as unchanged and it is an
    ornament."""
    bot = _dm_bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)
    _run(vloop, [(0.0, bot.refresh_pinned)])
    assert len(pbot.pinned_edits()) == 1
    assert _is_essential(pbot.edit_args[0]), pbot.edit_args


def test_an_app_button_taken_off_the_bar_is_an_essential_edit(
    mk_bot, pbot, vloop, no_chat_id,
):
    """The Mini App goes away (its URL stops answering): the edit that
    removes the button is ESSENTIAL too, so a dead button does not stay."""
    bot = _dm_bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)

    async def _gone():
        bot._miniapp_url = ""
        await bot.refresh_pinned()

    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 1.0, _gone)])
    edits = pbot.pinned_edits()
    assert len(edits) == 2, edits
    assert _app_urls(edits[1][4]) == []
    assert _is_essential(pbot.edit_args[1]), pbot.edit_args


def test_a_new_app_address_waits_for_the_gap_and_never_goes_into_a_mute(
    mk_bot, pbot, vloop, vlimiter, no_chat_id,
):
    """ESSENTIAL does not skip the bar's rules: an address change inside
    the gap waits for the gap's end, and one during a ban goes out only
    after the lift."""
    bot = _dm_bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)
    out: dict = {}

    async def _move_in_gap():
        out["moved"] = vloop.time()
        bot._miniapp_url = URL_B
        await bot.refresh_pinned()

    async def _ban_then_move():
        MUTE.mute(CHAT, 100.0)
        vlimiter.reset()
        out["ban"] = vloop.time()
        bot._miniapp_url = URL_A
        await bot.refresh_pinned()

    async def main():
        await bot.refresh_pinned()
        await asyncio.sleep(2.0)
        await _move_in_gap()
        await asyncio.sleep(2 * GAP)
        await _ban_then_move()
        await asyncio.sleep(150.0)

    vloop.run_until_complete(main())
    edits = pbot.pinned_edits()
    assert len(edits) == 3, edits
    first_at = edits[0][5]
    assert edits[1][5] - first_at >= GAP - EPS
    assert edits[2][5] >= out["ban"] + 100.0 - EPS
    assert [u[:len(URL_A)] for u in _app_urls(edits[2][4])] == [URL_A]


def test_a_group_bar_never_gets_an_app_button_or_an_essential_edit(
    mk_bot, pbot, vloop, no_chat_id,
):
    """In a group the bar keeps today's rule: no App button (a web_app
    button there gets the whole keyboard rejected), so an address change
    is nothing to the group's bar."""
    bot = _dm_bot(mk_bot, pbot, group=True)
    bot.registry.pinned_msg_ids[GROUP] = PINNED + 1
    _session(bot, "g0", Status.BUSY, chat=GROUP, kind="group")

    async def _move():
        bot._miniapp_url = URL_B
        await bot.pinned_app_changed()
        await asyncio.sleep(0)
        await bot.refresh_pinned()

    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 1.0, _move)])
    group_edits = pbot.pinned_edits(chat=GROUP, msg_id=PINNED + 1)
    assert len(group_edits) == 1, group_edits
    assert _app_urls(group_edits[0][4]) == []
    assert pbot.of("getChat", GROUP) == []
    assert pbot.of("pinChatMessage", GROUP) == []


def test_publish_miniapp_button_refreshes_the_bar_without_waiting(
    mk_bot, pbot, vloop, no_chat_id,
):
    """A new tunnel address published: the bar gets it right away (not at
    the next state change), and the publish never waits for the bar.

    Mutations: drop the refresh from ``publish_miniapp_button`` and no
    edit carries B; await the refresh instead of starting it as a task and
    the publish returns only after the slow edit."""
    bot = _dm_bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)
    out: dict = {}

    async def _menu(**_kw):
        return True

    pbot.set_chat_menu_button = _menu

    async def main():
        await bot.refresh_pinned()
        await asyncio.sleep(GAP + 1.0)
        pbot.edit_delay = 20.0
        t0 = vloop.time()
        await bot.publish_miniapp_button(URL_B)
        out["publish_took"] = vloop.time() - t0
        await asyncio.sleep(3 * GAP)

    vloop.run_until_complete(main())
    assert out["publish_took"] < 1.0, out
    edits = pbot.pinned_edits()
    assert len(edits) == 2, edits
    assert [u[:len(URL_B)] for u in _app_urls(edits[1][4])] == [URL_B]


def test_publish_with_the_same_address_does_not_touch_the_bar(
    mk_bot, pbot, vloop, no_chat_id,
):
    """The daemon primes the URL and then publishes the same one at start:
    that is no change, so no extra getChat."""
    bot = _dm_bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)

    async def _menu(**_kw):
        return True

    pbot.set_chat_menu_button = _menu

    async def _republish():
        await bot.publish_miniapp_button(URL_A)

    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 1.0, _republish)])
    assert len(pbot.of("getChat")) == 1
    assert len(pbot.pinned_edits()) == 1


def test_an_unpinned_bar_is_pinned_again_silently_at_start(
    mk_bot, pbot, vloop, no_chat_id,
):
    """The registry holds the bar's id but Telegram has no pinned message
    in the chat (2026-10-15). The first refresh after a start asks getChat
    and pins the bar again, silently and ESSENTIAL; later refreshes ask
    nothing.

    Mutations: drop the check and nothing re-pins it; pin without asking
    and an already pinned bar gets a needless pin (next test)."""
    bot = _dm_bot(mk_bot, pbot)
    s0 = _session(bot, "s0", Status.BUSY)
    pbot.pinned_id = None

    async def _idle():
        s0.status = Status.IDLE
        await bot.refresh_pinned()

    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 1.0, _idle),
                 (GAP + 1.0, bot.refresh_pinned)])
    assert len(pbot.of("getChat")) == 1
    assert [p[2] for p in pbot.of("pinChatMessage")] == [PINNED]
    assert pbot.pin_kwargs[0].get("disable_notification") is True
    assert _is_essential(pbot.pin_kwargs[0]["rate_limit_args"])
    assert _is_essential(pbot.get_chat_args[0])
    assert pbot.pinned_id == PINNED
    assert pbot.of("sendMessage") == []


def test_a_bar_that_is_still_pinned_is_not_pinned_again(
    mk_bot, pbot, vloop, no_chat_id,
):
    bot = _dm_bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)
    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 1.0, bot.refresh_pinned)])
    assert len(pbot.of("getChat")) == 1
    assert pbot.of("pinChatMessage") == []


def test_each_app_address_change_asks_again(mk_bot, pbot, vloop, no_chat_id):
    """After a Mini App URL change the check runs once more: the pin may
    have been lost since the start.

    Mutation: drop the ``verify_pin`` mark in ``pinned_app_changed`` and
    the lost pin stays lost."""
    bot = _dm_bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)

    async def _unpin_then_move():
        pbot.pinned_id = None          # unpinned by hand meanwhile
        bot._miniapp_url = URL_B
        await bot.pinned_app_changed()

    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 1.0, _unpin_then_move)])
    assert len(pbot.of("getChat")) == 2
    assert [p[2] for p in pbot.of("pinChatMessage")] == [PINNED]


def test_a_check_with_an_unchanged_bar_still_runs(
    mk_bot, pbot, vloop, no_chat_id,
):
    """The check is not tied to an edit: with nothing to edit, a pending
    check still asks (and re-pins).

    Mutation: return early on an unchanged render even with the check
    pending and nothing is asked."""
    bot = _dm_bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)

    async def _lost_pin():
        pbot.pinned_id = None
        await bot.pinned_app_changed()     # same address: no edit

    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 1.0, _lost_pin)])
    assert len(pbot.pinned_edits()) == 1
    assert len(pbot.of("getChat")) == 2
    assert [p[2] for p in pbot.of("pinChatMessage")] == [PINNED]


def test_a_bar_that_is_gone_is_sent_and_pinned_anew(
    mk_bot, pbot, vloop, no_chat_id,
):
    """getChat says the bar is not pinned and the pin answers that the
    message is gone: a new bar is sent and pinned in its place.

    Mutation: drop the gone branch and the chat keeps no bar."""
    bot = _dm_bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)

    async def _deleted():
        pbot.pinned_id = None
        pbot.fail["pinChatMessage"] = [
            lambda: BadRequest("Message to pin not found")]
        await bot.pinned_app_changed()

    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 1.0, _deleted),
                 (GAP + 1.0, bot.refresh_pinned)])
    # The new bar was pinned as it was made: nothing more to ask.
    assert len(pbot.of("getChat")) == 2
    sent = pbot.of("sendMessage")
    assert len(sent) == 1, pbot.calls
    new_id = bot.registry.pinned_msg_ids[CHAT]
    assert new_id == sent[0][2] != PINNED
    assert [p[2] for p in pbot.of("pinChatMessage")] == [PINNED, new_id]
    assert pbot.pinned_id == new_id


def test_no_check_into_a_mute(mk_bot, pbot, vloop, vlimiter, no_chat_id):
    """A ban is on the chat at start: no getChat (or pin) goes out during
    it; the check runs after the lift."""
    bot = _dm_bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)
    pbot.pinned_id = None
    out: dict = {}

    async def main():
        MUTE.mute(CHAT, 100.0)
        vlimiter.reset()
        out["ban"] = vloop.time()
        await bot.refresh_pinned()
        await asyncio.sleep(150.0)

    vloop.run_until_complete(main())
    checks = pbot.of("getChat")
    assert len(checks) == 1
    assert checks[0][5] >= out["ban"] + 100.0 - EPS
    pins = pbot.of("pinChatMessage")
    assert len(pins) == 1 and pins[0][5] >= out["ban"] + 100.0 - EPS


def test_the_check_runs_only_in_private_chats(mk_bot, pbot, vloop, no_chat_id):
    """In a group the members pin their own messages, and getChat names
    only the most recent pin: the bar is never re-pinned there."""
    bot = _dm_bot(mk_bot, pbot, group=True, pinned=0)
    bot.registry.pinned_msg_ids[GROUP] = PINNED + 1
    _session(bot, "g0", Status.BUSY, chat=GROUP, kind="group")
    pbot.pinned_id = None
    _run(vloop, [(0.0, bot.refresh_pinned)])
    assert len(pbot.pinned_edits(chat=GROUP, msg_id=PINNED + 1)) == 1
    assert pbot.of("getChat", GROUP) == []
    assert pbot.of("pinChatMessage", GROUP) == []


def test_a_failed_check_does_not_hold_the_bar_and_is_asked_again(
    mk_bot, pbot, vloop, no_chat_id,
):
    """A network blip on getChat: the bar's own edits go on meanwhile, and
    the check is asked again a minute later.

    Mutations: back off the whole bar on the blip and the state edit
    waits a minute; drop the retry and the check is never asked again."""
    from telegram.error import NetworkError

    bot = _dm_bot(mk_bot, pbot)
    s0 = _session(bot, "s0", Status.BUSY)
    pbot.pinned_id = None
    pbot.fail["getChat"] = [lambda: NetworkError("blip")]

    async def _idle():
        s0.status = Status.IDLE
        await bot.refresh_pinned()

    async def main():
        await bot.refresh_pinned()
        await asyncio.sleep(GAP + 1.0)
        await _idle()
        for _ in range(40):            # the session monitor's 2 s tick
            await asyncio.sleep(2.0)
            await bot.refresh_pinned()

    vloop.run_until_complete(main())
    edits = pbot.pinned_edits()
    assert len(edits) == 2, edits
    assert edits[1][5] - edits[0][5] <= GAP + 2.0
    checks = pbot.of("getChat")
    assert len(checks) == 2, checks
    assert checks[1][5] - checks[0][5] >= 60.0 - EPS
    assert [p[2] for p in pbot.of("pinChatMessage")] == [PINNED]


def test_a_bar_made_by_this_daemon_is_not_asked_about(
    mk_bot, pbot, vloop, no_chat_id,
):
    """A bar this daemon sent and pinned itself needs no getChat.

    Mutation: leave the check pending after a successful pin and the next
    refresh asks."""
    bot = _dm_bot(mk_bot, pbot, pinned=0)
    s0 = _session(bot, "s0", Status.BUSY)

    async def _idle():
        s0.status = Status.IDLE
        await bot.refresh_pinned()

    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 1.0, _idle)])
    assert len(pbot.of("sendMessage")) == 1
    assert len(pbot.of("pinChatMessage")) == 1
    assert pbot.of("getChat") == []


def _ticks(bot, seconds: float):
    """The session monitor's 2 s tick for *seconds*."""
    async def _tick():
        for _ in range(int(seconds / 2.0)):
            await asyncio.sleep(2.0)
            await bot.refresh_pinned()
    return _tick


def test_a_blocked_chat_on_the_check_turns_the_bar_off(
    mk_bot, pbot, vloop, no_chat_id,
):
    """getChat answers Forbidden (the operator blocked the bot): no bar in
    that chat until restart, so no call every minute for the daemon's life.

    Mutation: drop the Forbidden arm and getChat is asked every minute."""
    from telegram.error import Forbidden

    bot = _dm_bot(mk_bot, pbot)
    s0 = _session(bot, "s0", Status.BUSY)
    pbot.fail["getChat"] = [lambda: Forbidden("bot was blocked by the user")]

    async def _idle():
        s0.status = Status.IDLE
        await bot.refresh_pinned()

    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 1.0, _idle),
                 (0.0, _ticks(bot, 180.0))])
    assert len(pbot.of("getChat")) == 1
    assert len(pbot.pinned_edits()) == 1
    assert CHAT not in bot.registry.pinned_msg_ids


@pytest.mark.parametrize("endpoint", ["getChat", "pinChatMessage"])
def test_a_refused_check_or_pin_is_not_asked_again(
    mk_bot, pbot, vloop, no_chat_id, endpoint,
):
    """Telegram refuses the getChat, or the re-pin, for good (a bad
    request that is not "message gone"): the check ends there, never a
    retry every minute.

    Mutations: drop the getChat BadRequest arm, or leave the check pending
    after a refused re-pin, and getChat is asked again."""
    bot = _dm_bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)
    pbot.pinned_id = None
    pbot.fail[endpoint] = [lambda: BadRequest("Not enough rights")]
    _run(vloop, [(0.0, bot.refresh_pinned), (0.0, _ticks(bot, 180.0))])
    assert len(pbot.of("getChat")) == 1
    assert len(pbot.of("pinChatMessage")) == (1 if endpoint == "pinChatMessage" else 0)
    assert bot.registry.pinned_msg_ids[CHAT] == PINNED


def test_a_blocked_chat_on_the_re_pin_turns_the_bar_off(
    mk_bot, pbot, vloop, no_chat_id,
):
    from telegram.error import Forbidden

    bot = _dm_bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)
    pbot.pinned_id = None
    pbot.fail["pinChatMessage"] = [lambda: Forbidden("bot was blocked by the user")]
    _run(vloop, [(0.0, bot.refresh_pinned), (0.0, _ticks(bot, 180.0))])
    assert len(pbot.of("getChat")) == 1
    assert len(pbot.of("pinChatMessage")) == 1
    assert CHAT not in bot.registry.pinned_msg_ids


@pytest.mark.parametrize("endpoint", ["getChat", "pinChatMessage"])
def test_a_ban_armed_under_the_check_waits_only_for_the_ban(
    mk_bot, pbot, vloop, vlimiter, no_chat_id, endpoint,
):
    """A 10 s ban is armed while getChat (or the re-pin) is in flight: the
    check is asked again once the ban and the gap have passed, not a whole
    minute later.

    Mutation: drop either flood arm (so the ban counts as a network blip)
    and the next ask waits 60 s."""
    from aipager.bot.flood import FloodMuted

    bot = _dm_bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)
    pbot.pinned_id = None

    def _ban():
        MUTE.mute(CHAT, 10.0)
        return FloodMuted(10.0, CHAT)

    pbot.fail[endpoint] = [_ban]
    _run(vloop, [(0.0, bot.refresh_pinned), (0.0, _ticks(bot, 120.0))])
    checks = pbot.of("getChat")
    assert len(checks) == 2, checks
    # The gap, plus the limiter's own pacing after a ban; a blip's
    # back-off would be a whole minute.
    assert checks[1][5] - checks[0][5] < 50.0
    assert pbot.pinned_id == PINNED


@pytest.mark.parametrize("sender", [OPERATOR_ID, BOT_ID],
                         ids=["operators-note", "pinned-claude-answer"])
def test_a_newer_pin_is_left_on_top(
    mk_bot, pbot, vloop, no_chat_id, sender,
):
    """getChat names a message pinned after the bar was sent (the
    operator's note, or one of Claude's answers they pinned, which the
    bot sent): the bar may still be pinned under it, and a re-pin could
    not change what getChat names, so it would repeat (with a service
    message) on every restart. Nothing is pinned.

    Mutation: re-pin whenever the pinned message is not the bar."""
    bot = _dm_bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)
    pbot.pinned_id, pbot.pinned_from = PINNED + 50, sender
    _run(vloop, [(0.0, bot.refresh_pinned)])
    assert len(pbot.of("getChat")) == 1
    assert pbot.of("pinChatMessage") == []


def test_an_older_pinned_message_gets_the_bar_pinned_again(
    mk_bot, pbot, vloop, no_chat_id,
):
    """getChat names the most recent pin by sending date. When it names a
    message OLDER than the bar, the bar is certainly not pinned: it is
    pinned again.

    Mutation: leave every other pinned message alone and it is not."""
    bot = _dm_bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)
    pbot.pinned_id, pbot.pinned_from = PINNED - 50, BOT_ID
    _run(vloop, [(0.0, bot.refresh_pinned)])
    assert [p[2] for p in pbot.of("pinChatMessage")] == [PINNED]


def test_a_refused_app_change_edit_is_not_essential_every_gap(
    mk_bot, pbot, vloop, no_chat_id,
):
    """Telegram refuses the edit that puts the new address on the button:
    it is retried, but as a skip-class ornament, never an ESSENTIAL call
    every 30 s.

    Mutation: drop ``app_tried`` and every retry is ESSENTIAL."""
    bot = _dm_bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)

    async def _move():
        pbot.fail["editMessageText"] = [
            lambda: BadRequest("Message can't be edited")] * 3
        bot._miniapp_url = URL_B
        await bot.refresh_pinned()

    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 1.0, _move),
                 (0.0, _ticks(bot, 4 * GAP))])
    assert len(pbot.edit_args) >= 4, pbot.edit_args
    assert _is_essential(pbot.edit_args[1])
    assert all(_is_skip_ornament(a) for a in pbot.edit_args[2:]), pbot.edit_args


def test_a_pending_pin_that_lands_needs_no_check(
    mk_bot, pbot, vloop, no_chat_id,
):
    """The new bar's pin was held back by the budget; the next refresh
    pins it. That pin is the answer: no getChat in the same attempt.

    Mutation: use the check flag read before the pin and getChat goes."""
    bot = _dm_bot(mk_bot, pbot, pinned=0)
    s0 = _session(bot, "s0", Status.BUSY)
    pbot.skip["pinChatMessage"] = 1

    async def _idle():
        s0.status = Status.IDLE
        await bot.refresh_pinned()

    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 1.0, _idle)])
    assert len(pbot.of("pinChatMessage")) == 1
    assert pbot.of("getChat") == []


def test_a_mini_app_url_that_cannot_be_a_button_clears_the_bars_too(
    mk_bot, pbot, vloop, no_chat_id, monkeypatch,
):
    """publish_miniapp_button fails closed when the URL cannot even be
    made into a menu button: the URL is dropped, and the bars follow at
    once (an ESSENTIAL edit taking the App button off), with a check.

    Mutation: drop the bars' refresh on that path and the bar keeps the
    button until some state changes."""
    bot = _dm_bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)

    def _boom(*_a, **_k):
        raise ValueError("no button")

    monkeypatch.setattr(bot, "_menu_button_webapp", _boom)

    async def _publish():
        await bot.publish_miniapp_button(URL_B)

    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 1.0, _publish)])
    assert bot._miniapp_url == ""
    edits = pbot.pinned_edits()
    assert len(edits) == 2, edits
    assert _app_urls(edits[1][4]) == []
    assert _is_essential(pbot.edit_args[1])
    assert len(pbot.of("getChat")) == 2


def test_a_refusal_long_ago_does_not_demote_a_later_app_change(
    mk_bot, pbot, vloop, no_chat_id,
):
    """B refused (then shown as an ornament), the Mini App gone, B back:
    that last edit is a new change and ESSENTIAL again.

    Mutation: never clear ``app_tried`` and it goes as an ornament."""
    bot = _dm_bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)

    async def _to_b_refused():
        pbot.fail["editMessageText"] = [
            lambda: BadRequest("Message can't be edited")]
        bot._miniapp_url = URL_B
        await bot.refresh_pinned()

    async def _set(url):
        bot._miniapp_url = url
        await bot.refresh_pinned()

    _run(vloop, [(0.0, bot.refresh_pinned),
                 (GAP + 1.0, _to_b_refused),
                 (GAP + 1.0, bot.refresh_pinned),        # B lands, ornament
                 (GAP + 1.0, lambda: _set("")),
                 (GAP + 1.0, lambda: _set(URL_B))])
    assert len(pbot.edit_args) == 5, pbot.edit_args
    assert _is_skip_ornament(pbot.edit_args[2])
    assert _is_essential(pbot.edit_args[3])
    assert _is_essential(pbot.edit_args[4]), pbot.edit_args


def test_a_bar_whose_edit_is_refused_is_still_checked(
    mk_bot, pbot, vloop, no_chat_id,
):
    """Telegram refuses the bar's edit (a bad request, not "gone"): the
    bar is still there, so its pin is checked, and re-pinned, anyway.

    Mutation: stop after any edit that did not land and it never is."""
    bot = _dm_bot(mk_bot, pbot)
    _session(bot, "s0", Status.BUSY)
    pbot.pinned_id = None
    pbot.fail["editMessageText"] = [
        lambda: BadRequest("Message can't be edited")]
    _run(vloop, [(0.0, bot.refresh_pinned)])
    assert len(pbot.of("getChat")) == 1
    assert [p[2] for p in pbot.of("pinChatMessage")] == [PINNED]


def test_a_dm_pin_refused_at_creation_is_not_checked_again(
    mk_bot, pbot, vloop, no_chat_id,
):
    """A new DM bar's pin is refused by Telegram (not the budget): the bar
    is edited in place, and nobody asks getChat (or pins again) on the
    next refresh; the pin was just tried.

    Mutation: leave the check pending after that refusal and the next
    refresh asks getChat and pins again."""
    bot = _dm_bot(mk_bot, pbot, pinned=0)
    s0 = _session(bot, "s0", Status.BUSY)
    pbot.fail["pinChatMessage"] = [lambda: BadRequest("Not enough rights")]

    async def _idle():
        s0.status = Status.IDLE
        await bot.refresh_pinned()

    _run(vloop, [(0.0, bot.refresh_pinned), (GAP + 1.0, _idle),
                 (0.0, _ticks(bot, 120.0))])
    assert len(pbot.of("sendMessage")) == 1
    assert len(pbot.of("pinChatMessage")) == 1
    assert pbot.of("getChat") == []
    assert len(pbot.of("editMessageText")) == 1
