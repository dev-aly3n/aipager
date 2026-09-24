"""Iteration-3 black-box rows for 8.30.

1. **A ban lands while ESSENTIAL calls are queued** (R7: zero requests into
   a ban). The ban can arrive on any call: the typing bubble, a card edit,
   an answer, the pinned dashboard, or a mute armed from anywhere
   (``MUTE.mute``). Calls already queued in the limiter at that moment must
   never reach the wire once the mute is armed. PTB callers get
   ``FloodMuted`` and an answer is held.
2. **The pinned dashboard** (amended for 8.31's "needs you" bar): a hook
   never refreshes it; a session status change, or a session appearing or
   going, is shown within one monitor tick plus the bar's 30 s gap, once.
3. **Two sessions stream for 2 h with the bar live**: the bubble never
   goes dark, the chat never enters minimal mode, and every rolling hour
   stays within 1080 ornament calls.
4. **Iteration 4**: a ban on any chat-scoped call mutes the chat (section
   1 also covers a button tap's edit and a PTB ``sendMessage``); the same
   ban armed twice logs one warning; a shorter later ban (a 30 s 429 or a
   600 s ban) never shortens a longer mute.

Everything runs on the harness's virtual loop. PTB calls go through the
real limiter (a ``_GatedBot`` subclass that also records message ids).
Rich calls go through the REAL ``rich_message._post`` over an
``httpx.MockTransport``. Nothing in ``asyncio`` is patched.

These rows run a legacy single-chat install (``scopes`` None), where the
bar's chat is ``aipager.bot.dashboard.CHAT_ID``, so they set it. That is
configuration, not a mock of the code under test.
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
from unittest.mock import MagicMock

import httpx
import pytest
from telegram.error import RetryAfter

import aipager.bot.rich_message as rm
from aipager import config
from aipager.bot.flood import MUTE, FloodMuted
from aipager.bot.held import HELD
from aipager.state import Status

_h = sys.modules["_lrv_qa_harness"]

CHAT = 256113222
PINNED = 9999
BAN = 25429
MIN = 60.0
HOUR = 3600.0
EPS = 1e-3
SLOW = config.TYPING_AGE_TIER2_INTERVAL                       # 15
SHARE = config.FLOOD_HOURLY_MAX - config.FLOOD_HOURLY_ESSENTIAL_RESERVE  # 1080
PIN_GAP = config.PINNED_MIN_EDIT_GAP                         # 30
TICK = 2.0                                  # the session monitor's scan
ANSWER = "queued answer the ban must not eat"


# ── transports ───────────────────────────────────────────────────────────────

class _Bot(_h._GatedBot):
    """The harness's gated PTB double, plus:

    * ``log``: ``(method, chat, message_id, t)`` for every call that REACHED
      the wire (its callback ran);
    * ``slow_ban``: ``endpoint -> seconds``. The first such call is
      admitted, stays in flight for *seconds*, and is then answered with
      vm3's 25,429 s ban. ``ban_at`` records when that answer came back;
    * ``script``: several in-flight calls answered with chosen bans at
      chosen times (two 429s for one chat, as Telegram sends when two
      calls are in flight when the ban starts).
    """

    def __init__(self, limiter, clock):
        super().__init__(limiter, clock)
        self.log: list[tuple] = []
        self.slow_ban: dict[str, float] = {}
        self.ban_at: float | None = None
        #: ``[(answer_at, retry_after)]``: the next calls to CHAT, in the
        #: order they reach the wire, stay in flight until *answer_at* and
        #: are then answered with ``RetryAfter(retry_after)``.
        self.script: list[tuple[float, int]] = []
        self.bans: list[tuple[float, int]] = []

    def __getattr__(self, name):
        if name not in self._ENDPOINTS:
            raise AttributeError(name)
        endpoint, position = self._ENDPOINTS[name]

        async def _method(*args, rate_limit_args=None, **kwargs):
            chat_id = kwargs.get("chat_id")
            if chat_id is None and len(args) > position:
                chat_id = args[position]
            msg = kwargs.get("message_id")
            if msg is None and endpoint == "editMessageText" and len(args) > 2:
                msg = args[2]

            async def _call():
                t = self._clock()
                self.calls.append((endpoint, chat_id, t))
                self.log.append((endpoint, chat_id, msg, t))
                delay = self.slow_ban.pop(endpoint, None)
                if delay is not None:
                    await asyncio.sleep(delay)
                    self.ban_at = self._clock()
                    raise RetryAfter(BAN)
                if self.script and chat_id == CHAT:
                    answer_at, retry = self.script.pop(0)
                    await asyncio.sleep(max(0.0, answer_at - self._clock()))
                    self.bans.append((self._clock(), retry))
                    raise RetryAfter(retry)
                return _h._Sent()

            return await self._limiter.process_request(
                callback=_call, args=(), kwargs={}, endpoint=endpoint,
                data={"chat_id": chat_id}, rate_limit_args=rate_limit_args)

        return _method

    def dashboard(self, chat=CHAT) -> list[float]:
        return [t for e, c, m, t in self.log
                if e == "editMessageText" and c == chat and m == PINNED]


class _Http:
    """The rich path's wire. ``slow_ban``: ``endpoint -> seconds``, as on
    :class:`_Bot`, but answered as Telegram's HTTP 429 body."""

    def __init__(self, clock):
        self.clock = clock
        self.requests: list[tuple] = []          # (endpoint, chat, t, payload)
        self.slow_ban: dict[str, float] = {}
        self.ban_at: float | None = None

    async def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        endpoint = request.url.path.rsplit("/", 1)[-1]
        self.requests.append((endpoint, payload.get("chat_id"),
                              self.clock(), payload))
        delay = self.slow_ban.pop(endpoint, None)
        if delay is not None:
            await asyncio.sleep(delay)
            self.ban_at = self.clock()
            return httpx.Response(200, json={
                "ok": False, "error_code": 429,
                "description": f"Too Many Requests: retry after {BAN}",
                "parameters": {"retry_after": BAN}})
        return httpx.Response(200, json={
            "ok": True, "result": {"message_id": payload.get("message_id", 4242)}})


@pytest.fixture
def http(vloop, monkeypatch):
    rec = _Http(vloop.time)
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "TESTTOKEN")
    monkeypatch.setattr(rm, "_post", _h._REAL_POST)
    monkeypatch.setattr(rm, "_client", httpx.AsyncClient(
        transport=httpx.MockTransport(rec.handler)))
    yield rec
    monkeypatch.setattr(rm, "_client", None)


@pytest.fixture
def pbot(vloop, vlimiter):
    return _Bot(vlimiter, vloop.time)


@pytest.fixture
def dash_on(monkeypatch):
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", str(CHAT))


def _bot(mk_bot, pbot):
    bot = mk_bot()
    bot._app.bot = pbot
    bot.registry.pinned_msg_ids[CHAT] = PINNED
    return bot


def _session(bot, vloop, label, *, age=0.0, msg_id=71, status=Status.BUSY):
    sess = bot.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.status = status
    sess.scope_chat_id = CHAT
    sess.scope_kind = "dm"
    sess.busy_msg_id = msg_id
    sess.busy_started_at = vloop.time() - age
    sess.last_tool_edit_at = 0.0
    return sess


def _wire(pbot, http, chat=CHAT) -> list[float]:
    out = [t for _e, c, _m, t in pbot.log if c == chat]
    out += [t for _e, c, t, _p in http.requests if c == chat]
    return sorted(out)


def _tool(n):
    return {"tool_summary": f"Bash: step {n}", "tool_name": "Bash"}


# ── 1. a ban lands while ESSENTIAL calls are queued ──────────────────────────

CARRIERS = ["typing", "card_edit", "answer", "dashboard", "mute_armed",
            "callback_edit", "send_message"]
CALLBACK_MSG = 500


async def _noop_async(*_a, **_k):
    return None


def _callback_update(pbot, data):
    """A button tap in the DM. ``query.edit_message_text`` is routed to the
    gated bot's ``edit_message_text``, as PTB's ``CallbackQuery`` does
    (``get_bot().edit_message_text(chat_id=..., message_id=...)``)."""
    async def _edit(*args, **kwargs):
        text = args[0] if args else kwargs.get("text", "")
        kwargs.pop("text", None)
        return await pbot.edit_message_text(
            chat_id=CHAT, message_id=CALLBACK_MSG, text=text, **kwargs)

    query = MagicMock()
    query.data = data
    query.answer = _noop_async
    query.edit_message_text = _edit
    query.edit_message_reply_markup = _noop_async
    query.message = MagicMock()
    query.message.message_id = CALLBACK_MSG
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
    return update


def _queued_ban(mk_bot, pbot, http, vloop, monkeypatch, carrier, *,
                lift=False):
    """A DM with two BUSY sessions. The chosen *carrier* goes out and stays
    in flight for a second. While it is in flight, four ESSENTIAL PTB
    ``sendMessage`` calls and session b's answer (the real ``notify`` over
    the real rich ``_post``) are started, and most of them queue in the
    limiter. Then Telegram answers the carrier with a 25,429 s ban (or,
    for ``mute_armed``, ``MUTE.mute`` is called directly).

    Carriers: the typing bubble, a card edit (rich), an answer (rich), the
    pinned dashboard's edit (PTB), a button tap's in-place edit (PTB,
    ``_handle_callback``), a PTB ``sendMessage`` such as a command reply,
    and a mute armed directly.

    The pinned dashboard is pinned ON for the ``dashboard`` carrier and OFF
    for every other one, so no row depends on the host's ``.env`` (an
    empty CI env leaves ``dashboard.CHAT_ID`` blank)."""
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID",
                        str(CHAT) if carrier == "dashboard" else "")
    bot = _bot(mk_bot, pbot)
    a = _session(bot, vloop, "a", msg_id=71)
    b = _session(bot, vloop, "b", msg_id=72)
    out: dict = {"refused": 0, "sent": 0, "other": []}

    async def _message(i):
        try:
            await pbot.send_message(chat_id=CHAT, text=f"essential {i}")
            out["sent"] += 1
        except FloodMuted:
            out["refused"] += 1
        except Exception as exc:                 # noqa: BLE001
            out["other"].append(type(exc).__name__)

    async def _answer():
        b.status = Status.IDLE
        bot._stop_animation(b)
        await bot.notify(b, "idle_prompt", {"summary": ANSWER})

    async def _carrier():
        if carrier == "typing":
            pbot.slow_ban["sendChatAction"] = 1.0
            return asyncio.ensure_future(bot._animate_typing(a))
        if carrier == "card_edit":
            http.slow_ban["editMessageText"] = 1.0
            return asyncio.ensure_future(bot._animate_busy(a))
        if carrier == "answer":
            http.slow_ban["sendRichMessage"] = 1.0

            async def _a_answers():
                a.status = Status.IDLE
                await bot.notify(a, "idle_prompt", {"summary": "a's answer"})
            return asyncio.ensure_future(_a_answers())
        if carrier == "dashboard":
            # 8.31: the bar is refreshed at a state transition (or on the
            # monitor's tick), no longer by a hook.
            pbot.slow_ban["editMessageText"] = 1.0
            return asyncio.ensure_future(bot.refresh_pinned())
        if carrier == "callback_edit":
            pbot.slow_ban["editMessageText"] = 1.0
            context = MagicMock()
            context.bot = pbot
            return asyncio.ensure_future(bot._handle_callback(
                _callback_update(pbot, "_:set"), context))
        if carrier == "send_message":
            pbot.slow_ban["sendMessage"] = 1.0

            async def _command_reply():
                try:
                    await pbot.send_message(chat_id=CHAT, text="reply")
                except RetryAfter:
                    pass
            return asyncio.ensure_future(_command_reply())
        # mute_armed: some other call site learnt of a ban.
        async def _arm():
            await asyncio.sleep(1.0)
            MUTE.mute(CHAT, BAN)
            out["mute_at"] = vloop.time()
        return asyncio.ensure_future(_arm())

    def _in_flight():
        if carrier == "mute_armed":
            return True
        return not pbot.slow_ban and not http.slow_ban

    async def main():
        start = vloop.time()
        task = await _carrier()
        # Queue the moment the carrier is on the wire (a card's first edit
        # may come a second or two after its loop starts).
        waited = 0.0
        while not _in_flight() and waited < 30.0:
            await asyncio.sleep(0.02)
            waited += 0.02
        out["carrier_on_wire"] = _in_flight() and bool(_wire(pbot, http))
        queued = [asyncio.ensure_future(_message(i)) for i in range(4)]
        queued.append(asyncio.ensure_future(_answer()))
        await asyncio.sleep(0.4)
        out["pending"] = sum(not q.done() for q in queued)
        await asyncio.sleep(1.0)                 # the ban answer is back
        ban_at = out.get("mute_at") or pbot.ban_at or http.ban_at
        out["ban_at"] = ban_at
        out["muted"] = MUTE.is_muted(CHAT)
        await asyncio.gather(*queued, return_exceptions=True)
        await asyncio.sleep(120.0)               # far past any floor-rate wait
        a.status = Status.IDLE
        bot._stop_animation(a)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        out["held"] = HELD.count(CHAT)
        # No ban observed means the row set nothing up: fail, never pass
        # vacuously (the CI-env hole of iteration 3).
        out["into_ban"] = ([-1.0] if ban_at is None else
                           [t - ban_at for t in _wire(pbot, http)
                            if t > ban_at])
        out["start"] = start
        if lift:
            await asyncio.sleep(BAN + 5.0 - (vloop.time() - ban_at))
            await bot.notify(b, "held_answer_flush", {})
            await asyncio.sleep(1.0)
            out["answers_after"] = [
                p for (e, c, _t, p) in http.requests
                if c == CHAT and ANSWER in json.dumps(p)]

    vloop.run_until_complete(main())
    return out


@pytest.mark.parametrize("carrier", CARRIERS)
def test_q_the_ban_carrier_really_went_out_before_the_queue(
    mk_bot, pbot, http, vloop, vlimiter, monkeypatch, carrier,
):
    """Setup check: something was on the wire before the queue started
    (for ``mute_armed``, which sends nothing, this is vacuous and
    skipped)."""
    if carrier == "mute_armed":
        pytest.skip("no carrier call for a directly armed mute")
    out = _queued_ban(mk_bot, pbot, http, vloop, monkeypatch, carrier)
    assert out["carrier_on_wire"] is True


@pytest.mark.parametrize("carrier", CARRIERS)
def test_q_calls_were_really_queued_when_the_ban_landed(
    mk_bot, pbot, http, vloop, vlimiter, monkeypatch, carrier,
):
    out = _queued_ban(mk_bot, pbot, http, vloop, monkeypatch, carrier)
    assert out["pending"] >= 2, out["pending"]


@pytest.mark.parametrize("carrier", CARRIERS)
def test_q_the_ban_arms_the_mute(mk_bot, pbot, http, vloop, vlimiter,
                                 monkeypatch, carrier):
    out = _queued_ban(mk_bot, pbot, http, vloop, monkeypatch, carrier)
    assert out["muted"] is True


@pytest.mark.parametrize("carrier", CARRIERS)
def test_q_zero_wire_calls_after_the_mute(
    mk_bot, pbot, http, vloop, vlimiter, monkeypatch, carrier,
):
    """The row the operator asked for: whatever the ban arrived on, no
    queued call (PTB or rich, essential or not) reaches Telegram after
    it."""
    out = _queued_ban(mk_bot, pbot, http, vloop, monkeypatch, carrier)
    assert out["into_ban"] == [], out["into_ban"]


@pytest.mark.parametrize("carrier", CARRIERS)
def test_q_queued_ptb_essentials_are_refused_with_flood_muted(
    mk_bot, pbot, http, vloop, vlimiter, monkeypatch, carrier,
):
    """Every PTB ``sendMessage`` that was still queued ends in
    ``FloodMuted``: none is sent and none fails some other way."""
    out = _queued_ban(mk_bot, pbot, http, vloop, monkeypatch, carrier)
    assert (out["sent"] + out["refused"] == 4 and out["refused"] >= 1
            and out["other"] == []), out


@pytest.mark.parametrize("carrier", CARRIERS)
def test_q_the_queued_answer_is_held(mk_bot, pbot, http, vloop, vlimiter,
                                     monkeypatch, carrier):
    out = _queued_ban(mk_bot, pbot, http, vloop, monkeypatch, carrier)
    assert out["held"] >= 1


@pytest.mark.parametrize("carrier", ["card_edit", "dashboard",
                                     "callback_edit", "send_message"])
def test_q_the_queued_answer_is_delivered_once_after_the_lift(
    mk_bot, pbot, http, vloop, vlimiter, monkeypatch, carrier,
):
    out = _queued_ban(mk_bot, pbot, http, vloop, monkeypatch, carrier, lift=True)
    assert len(out["answers_after"]) == 1, len(out["answers_after"])


def test_q_a_mute_before_any_queue_refuses_an_essential_at_the_gate(
    vloop, vlimiter, pbot,
):
    """Boundary: the classic case (the mute is armed BEFORE the call). It
    still gets ``FloodMuted`` and makes no wire call."""
    MUTE.mute(CHAT, BAN)

    async def _go():
        with pytest.raises(FloodMuted):
            await pbot.send_message(chat_id=CHAT, text="late")
    vloop.run_until_complete(_go())
    assert pbot.log == []


def test_q_a_ban_in_one_chat_does_not_refuse_anothers_queue(
    vloop, vlimiter, pbot,
):
    """Error guessing: the take-time mute check must be per chat. Calls
    queued for another chat are still sent when this one is muted."""
    other = 111222333
    sent: list = []

    async def _one():
        await pbot.send_message(chat_id=other, text="x")
        sent.append(1)

    async def _go():
        tasks = [asyncio.ensure_future(_one()) for _ in range(5)]
        await asyncio.sleep(0.5)
        MUTE.mute(CHAT, BAN)
        await asyncio.gather(*tasks, return_exceptions=True)
    vloop.run_until_complete(_go())
    assert len(sent) == 5


# ── 2. the pinned bar: hooks never refresh it, state changes do ─────────────
#
# Amended for 8.31. The 8.30 rows here pinned a debounce of the per-hook
# refresh (60 s idle, 600 s busy, a yield to the bubble's resume mark).
# 8.31 removed the per-hook refresh: the bar is re-rendered on the
# monitor's 2 s tick and edited only when what it shows changed, at most
# once per PINNED_MIN_EDIT_GAP. `_ticking` runs that tick.

def _dash(mk_bot, pbot, vloop, *, status=Status.BUSY):
    bot = _bot(mk_bot, pbot)
    s0 = _session(bot, vloop, "s0", msg_id=71, status=status)
    s1 = _session(bot, vloop, "s1", msg_id=72, status=status)
    return bot, s0, s1


def _steps(vloop, steps, bot=None):
    """Run ``(delay, coroutine factory)`` steps; return each step's start
    time. With *bot*, the session monitor's tick runs alongside."""
    marks: list[float] = []

    async def _tick():
        while True:
            await bot.pinned_tick()
            await asyncio.sleep(TICK)

    async def main():
        ticker = asyncio.ensure_future(_tick()) if bot is not None else None
        for delay, step in steps:
            await asyncio.sleep(delay)
            marks.append(vloop.time())
            await step()
        await asyncio.sleep(PIN_GAP + TICK)
        if ticker is not None:
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)
    vloop.run_until_complete(main())
    return marks


def _flips(bot, s0, s1, n, every):
    """*n* ``tool_use`` hooks alternating s0/s1, *every* seconds apart."""
    return [(every, (lambda s=(s0, s1)[i % 2], i=i: bot.notify(s, "tool_use",
                                                               _tool(i))))
            for i in range(n)]


def test_d_the_first_tick_shows_the_bar(
    mk_bot, pbot, vloop, vlimiter, rich_http, dash_on,
):
    """Setup check: the bar is live in these rows."""
    bot, s0, _s1 = _dash(mk_bot, pbot, vloop)
    _steps(vloop, [(0.0, lambda: bot.notify(s0, "tool_use", _tool(0)))], bot)
    assert len(pbot.dashboard()) == 1


def test_d_busy_hooks_never_refresh_the_bar(
    mk_bot, pbot, vloop, vlimiter, rich_http, dash_on,
):
    """Both sessions BUSY. 118 hooks alternate s0/s1 every 5 s with the
    tick running: nothing on the bar changes, so nothing goes out after
    the first edit."""
    bot, s0, s1 = _dash(mk_bot, pbot, vloop)
    _steps(vloop, [(0.0, lambda: bot.notify(s0, "tool_use", _tool(0)))]
           + _flips(bot, s0, s1, 118, 5.0), bot)
    assert len(pbot.dashboard()) == 1, pbot.dashboard()


def test_d_waiting_hooks_never_refresh_the_bar(
    mk_bot, pbot, vloop, vlimiter, rich_http, dash_on,
):
    bot, s0, s1 = _dash(mk_bot, pbot, vloop, status=Status.INTERACTIVE)
    _steps(vloop, [(0.0, lambda: bot.notify(s0, "tool_use", _tool(0)))]
           + _flips(bot, s0, s1, 30, 5.0), bot)
    assert len(pbot.dashboard()) == 1, pbot.dashboard()


def test_d_phantom_subagent_stops_do_not_refresh(
    mk_bot, pbot, vloop, vlimiter, rich_http, dash_on,
):
    """Error guessing: phantom SubagentStops (empty type, unknown id)
    arrive constantly. Every second for 5 min, from both BUSY sessions,
    they refresh nothing after the first."""
    bot, s0, s1 = _dash(mk_bot, pbot, vloop)
    phantom = {"agent_type": "", "agent_id": "unknown", "duration": 0.0}
    steps = [(0.0, lambda: bot.notify(s0, "tool_use", _tool(0)))]
    steps += [(1.0, (lambda s=(s0, s1)[i % 2]: bot.notify(
        s, "subagent_stop", dict(phantom)))) for i in range(300)]
    _steps(vloop, steps, bot)
    assert len(pbot.dashboard()) == 1, pbot.dashboard()


def _status_change_then_hook(mk_bot, pbot, vloop, change):
    """Both BUSY, the tick running. The first edit at 0; 30 s of hooks;
    at 30 s *change* is applied; then the sessions keep notifying, one
    ``tool_use`` a second for 5 s. Returns ``(bar stamps, time of the
    change)``."""
    bot, s0, s1 = _dash(mk_bot, pbot, vloop)

    async def _change():
        change(bot, s0, s1, vloop)

    marks = _steps(vloop,
                   [(0.0, lambda: bot.notify(s0, "tool_use", _tool(0)))]
                   + _flips(bot, s0, s1, 6, 5.0)
                   + [(0.0, _change)]
                   + [(1.0, (lambda i=i: bot.notify(s0 if i % 2 else s1,
                                                    "tool_use", _tool(90 + i))))
                      for i in range(5)], bot)
    return pbot.dashboard(), marks[-6]


def _s1_idle(bot, s0, s1, vloop):
    s1.status = Status.IDLE
    bot._stop_animation(s1)


def _s1_interactive(bot, s0, s1, vloop):
    s1.status = Status.INTERACTIVE


def _s1_gone(bot, s0, s1, vloop):
    s1.status = Status.GONE


def _s2_arrives(bot, s0, s1, vloop):
    _session(bot, vloop, "s2", msg_id=73, status=Status.IDLE)


def _s1_removed(bot, s0, s1, vloop):
    bot.registry.remove("claude-s1")


@pytest.mark.parametrize("change", [_s1_idle, _s1_interactive, _s1_gone,
                                    _s2_arrives, _s1_removed],
                         ids=["busy-to-idle", "busy-to-interactive",
                              "busy-to-gone", "session-appears",
                              "session-removed"])
def test_d_a_state_change_is_shown_within_a_tick(
    mk_bot, pbot, vloop, vlimiter, rich_http, dash_on, change,
):
    """A status change (or a session appearing or going) 30 s after the
    first edit — the gap has passed — is shown on the next tick."""
    stamps, change_at = _status_change_then_hook(mk_bot, pbot, vloop, change)
    assert [t for t in stamps if change_at <= t <= change_at + TICK + EPS], \
        [t - change_at for t in stamps]


def test_d_a_state_change_is_shown_once_not_on_every_later_hook(
    mk_bot, pbot, vloop, vlimiter, rich_http, dash_on,
):
    """After the state change is shown, 2 min of further hooks add
    nothing."""
    bot, s0, s1 = _dash(mk_bot, pbot, vloop)
    steps = [(0.0, lambda: bot.notify(s0, "tool_use", _tool(0)))]
    steps += [(10.0, lambda: _as_coro(_s1_interactive(bot, s0, s1, vloop)))]
    steps += _flips(bot, s0, s1, 25, 5.0)
    _steps(vloop, steps, bot)
    assert len(pbot.dashboard()) == 2, pbot.dashboard()


async def _noop():
    return None


def _as_coro(_value):
    return _noop()


def test_d_a_status_change_goes_out_even_with_the_hour_near_the_shed(
    mk_bot, pbot, vloop, vlimiter, rich_http, dash_on,
):
    """The chat's hour stands at 700 ornament calls (between the 60 %
    resume and 75 % shed marks). A status change still reaches the bar
    within the gap."""
    bot, s0, s1 = _dash(mk_bot, pbot, vloop)

    async def _fill():
        vlimiter.restore([{"chat_id": CHAT,
                           "hourly": [[vloop.wall() - 60.0, 700, 0]]}])

    marks = _steps(vloop, [
        (0.0, lambda: bot.notify(s0, "tool_use", _tool(0))),
        (1.0, _fill),
        (20.0, lambda: _as_coro(_s1_interactive(bot, s0, s1, vloop)))]
        + [(1.0, (lambda i=i: bot.notify(s0 if i % 2 else s1, "tool_use",
                                         _tool(10 + i)))) for i in range(5)],
        bot)
    assert [t for t in pbot.dashboard()
            if marks[2] <= t <= marks[2] + PIN_GAP + TICK + EPS], \
        ([t - marks[2] for t in pbot.dashboard()],
         vlimiter.hourly_usage(CHAT))


# ── 3. two sessions stream for 2 h with the bar live ─────────────────────────

def _two_hour_dash(mk_bot, pbot, vloop, vlimiter, rich_http):
    """Two sessions, both BUSY from turn start, in one DM with the pinned
    bar live (the monitor's tick refreshing it every 2 s). Each gets a
    ``tool_use`` hook every 2 to 8 s (seeded). The cards and the chat's
    one bubble run for two hours. Sampled once a minute: ``minimal_mode``
    and ``hourly_usage``."""
    rich_http.clock = vloop.time
    bot = _bot(mk_bot, pbot)
    ss = [_session(bot, vloop, f"s{i}", msg_id=71 + i) for i in range(2)]
    samples: list[dict] = []

    async def _hooks(sess):
        rng = random.Random(f"{sess.label}-20260923")
        n = 0
        while True:
            await asyncio.sleep(rng.uniform(2.0, 8.0))
            n += 1
            await bot.notify(sess, "tool_use", _tool(n))

    async def _sampler():
        while True:
            await asyncio.sleep(60.0)
            u = vlimiter.hourly_usage(CHAT)
            samples.append({"minimal": vlimiter.minimal_mode(CHAT), **u})

    async def _tick():
        while True:
            await bot.pinned_tick()
            await asyncio.sleep(TICK)

    async def main():
        for s in ss:
            bot._start_animation(s)
        feeders = [asyncio.ensure_future(_hooks(s)) for s in ss]
        feeders.append(asyncio.ensure_future(_sampler()))
        feeders.append(asyncio.ensure_future(_tick()))
        await asyncio.sleep(2 * HOUR)
        for f in feeders:
            f.cancel()
        await asyncio.gather(*feeders, return_exceptions=True)
        for s in ss:
            s.status = Status.IDLE
            bot._stop_animation(s)
        await asyncio.sleep(0)

    start = vloop.time()
    vloop.run_until_complete(main())
    wire = sorted([t for _e, c, _m, t in pbot.log if c == CHAT]
                  + [t for (_e, c, _p), t in zip(rich_http.requests,
                                                 rich_http.stamps)
                     if c == CHAT])
    return {
        "start": start, "end": start + 2 * HOUR,
        "bubbles": pbot.stamps("sendChatAction"),
        "dashboard": pbot.dashboard(), "wire": wire, "samples": samples,
    }


@pytest.fixture
def two_hours(mk_bot, pbot, vloop, vlimiter, rich_http, dash_on):
    return _two_hour_dash(mk_bot, pbot, vloop, vlimiter, rich_http)


def test_s_the_bar_is_live_in_the_run(two_hours):
    """Setup check: the pinned bar really is refreshed during the run."""
    assert len(two_hours["dashboard"]) >= 1, two_hours["dashboard"]


def test_s_the_bar_is_edited_once_in_two_hours_of_hooks(two_hours):
    """Both sessions stay BUSY for the whole run, so nothing the bar shows
    changes after its first edit: two hours of hooks and 3,600 ticks are
    one edit. (8.30's debounce allowed one per 600 s here.)"""
    assert len(two_hours["dashboard"]) == 1, len(two_hours["dashboard"])


def test_s_the_bubble_never_goes_dark(two_hours):
    """"Flicker, never dark": no stretch without a bubble longer than the
    15 s tier plus a second, the run's ends included."""
    b = [two_hours["start"]] + two_hours["bubbles"] + [two_hours["end"]]
    assert max(y - x for x, y in zip(b, b[1:])) <= SLOW + 1.0 + EPS


def test_s_the_bubble_is_never_shed(two_hours):
    shed = [s["typing_shed"] for s in two_hours["samples"]]
    assert len(shed) >= 100 and not any(shed), shed.count(True)


def test_s_the_chat_never_enters_minimal_mode(two_hours):
    minimal = [s["minimal"] for s in two_hours["samples"]]
    assert len(minimal) >= 100 and not any(minimal), minimal.count(True)


def test_s_every_rolling_hour_on_the_wire_is_within_the_ornament_share(
    two_hours,
):
    """Every call in this run is an ornament (cards, bubbles, dashboard),
    so the wire's own count in every rolling 3600 s is held to 1080."""
    w = two_hours["wire"]
    worst = max(sum(1 for t in w if s <= t < s + HOUR) for s in w)
    assert worst <= SHARE, worst


def test_s_the_limiters_ornament_count_never_passes_the_share(two_hours):
    worst = max(s["ornament_used"] for s in two_hours["samples"])
    assert worst <= SHARE, worst


def test_s_no_essential_overflow_is_logged(two_hours):
    assert max(s["essential_overflow"] for s in two_hours["samples"]) == 0


# ── 4. iteration 4: any ban mutes, one warning, never shortened ─────────────
#
# The claim under test: every ban answered to a chat-scoped call mutes the
# chat, whatever call carried it (section 1's carriers, now including a
# button tap's in-place edit and a PTB ``sendMessage``). Arming the same
# ban twice logs ONE warning. A shorter ban arriving later never shortens
# a longer mute.

def _mute_warnings(caplog) -> list[str]:
    """The operator-facing mute line, as the daemon logs it (``Telegram
    flood control — chat <id> muted for <s>s ...``). Observed, not read
    from source: a WARNING on ``aipager.bot.flood`` that says ``muted``."""
    return [r.getMessage() for r in caplog.records
            if r.name == "aipager.bot.flood" and r.levelno >= 30
            and "muted" in r.getMessage()]


@pytest.mark.parametrize("carrier", CARRIERS)
def test_m_each_ban_carrier_logs_exactly_one_mute_warning(
    mk_bot, pbot, http, vloop, vlimiter, monkeypatch, carrier, caplog,
):
    """Whatever carried the ban (and however many call sites learn of it,
    the limiter and the caller alike), the operator sees one mute line."""
    caplog.set_level("INFO")
    _queued_ban(mk_bot, pbot, http, vloop, monkeypatch, carrier)
    assert len(_mute_warnings(caplog)) == 1, _mute_warnings(caplog)


def test_m_the_same_ban_armed_twice_directly_logs_one_warning(
    vloop, vlimiter, caplog,
):
    caplog.set_level("INFO")
    MUTE.mute(CHAT, BAN)
    MUTE.mute(CHAT, BAN)
    assert len(_mute_warnings(caplog)) == 1, _mute_warnings(caplog)


def test_m_a_direct_mute_warns_at_all(vloop, vlimiter, caplog):
    """Setup check for the rows above: the matcher finds the line."""
    caplog.set_level("INFO")
    MUTE.mute(CHAT, BAN)
    assert len(_mute_warnings(caplog)) == 1


# The shorter later answer: a small 429 (30 s, under
# ``TELEGRAM_MAX_RETRY_AFTER`` = 90 s) and a real but shorter ban (600 s).
SHORTER = [30, 600]


def _two_bans(pbot, vloop, first, second, *, second_after=1.0,
              bot=None, http=None):
    """Two PTB ``sendMessage`` calls to CHAT are in flight; Telegram
    answers the first with *first* seconds at +6 s and the second with
    *second* seconds *second_after* later. Six more ESSENTIAL calls are
    started at +5.9 s, so some are still queued in the limiter when the
    first ban lands (``pending_at_ban``).

    ``LAPSE`` = ``min(first, second) + 60`` s after the first ban (the
    shorter one has lapsed by then): with *bot*/*http*, session b ends its
    turn (its answer must be held); 40 s later the mute is sampled."""
    t0 = vloop.time()
    pbot.script = [(t0 + 6.0, first), (t0 + 6.0 + second_after, second)]
    lapse = min(first, second) + 60.0
    out: dict = {}

    async def _send(tag):
        try:
            await pbot.send_message(chat_id=CHAT, text=tag)
            return "sent"
        except FloodMuted:
            return "muted"
        except RetryAfter:
            return "banned"

    async def main():
        heads = [asyncio.ensure_future(_send("first")),
                 asyncio.ensure_future(_send("second"))]
        await asyncio.sleep(5.9)
        out["on_wire_before_ban"] = len(pbot.log)
        queued = [asyncio.ensure_future(_send(f"q{i}")) for i in range(6)]
        await asyncio.sleep(0.1 + 0.05)
        out["pending_at_ban"] = sum(not q.done() for q in queued)
        out["heads"] = await asyncio.gather(*heads)
        ban_at = pbot.bans[0][0] if pbot.bans else None
        out["ban_at"] = ban_at
        out["queued"] = await asyncio.gather(*queued)
        await asyncio.sleep(max(0.0, ban_at + lapse - vloop.time()))
        if bot is not None:
            b = _session(bot, vloop, "b", msg_id=72)
            b.status = Status.IDLE
            await bot.notify(b, "idle_prompt", {"summary": ANSWER})
            out["held"] = HELD.count(CHAT)
        await asyncio.sleep(max(0.0, ban_at + lapse + 40.0 - vloop.time()))
        out["sampled_after"] = vloop.time() - ban_at
        out["muted_after_lapse"] = MUTE.is_muted(CHAT)
        out["remaining_after_lapse"] = MUTE.remaining(CHAT)
        wire = [t for _e, c, _m, t in pbot.log if c == CHAT]
        if http is not None:
            wire += [t for _e, c, t, _p in http.requests if c == CHAT]
        out["into_ban"] = ([-1.0] if ban_at is None else
                           [t - ban_at for t in wire if t > ban_at])

    vloop.run_until_complete(main())
    return out


@pytest.mark.parametrize("second", SHORTER)
def test_m_setup_both_calls_are_in_flight_before_the_first_ban(
    vloop, vlimiter, pbot, second,
):
    """Both calls reached the wire before the first ban, the first one's
    caller saw the ban, and calls were queued when it landed. (The second
    call's caller may see its own 429 or ``FloodMuted``; either is fine.)"""
    out = _two_bans(pbot, vloop, BAN, second)
    assert (out["on_wire_before_ban"] == 2 and out["heads"][0] == "banned"
            and out["pending_at_ban"] >= 1), out


def test_m_the_same_ban_on_two_in_flight_calls_logs_one_warning(
    vloop, vlimiter, pbot, caplog,
):
    """Telegram answers both in-flight calls with the one ban, the second
    a second later counting down (``BAN - 1``): same deadline."""
    caplog.set_level("INFO")
    _two_bans(pbot, vloop, BAN, BAN - 1)
    assert len(_mute_warnings(caplog)) == 1, _mute_warnings(caplog)


@pytest.mark.parametrize("second", SHORTER)
def test_m_a_shorter_later_ban_does_not_lift_the_mute_early(
    vloop, vlimiter, pbot, second,
):
    """25,429 s, then *second* one second later: 40 s after the shorter
    one would have lapsed, the chat is still muted."""
    out = _two_bans(pbot, vloop, BAN, second)
    assert out["muted_after_lapse"] is True, out["sampled_after"]


@pytest.mark.parametrize("second", SHORTER)
def test_m_a_shorter_later_ban_keeps_the_long_deadline(
    vloop, vlimiter, pbot, second,
):
    out = _two_bans(pbot, vloop, BAN, second)
    assert (out["remaining_after_lapse"]
            >= BAN - out["sampled_after"] - 2.0), out


@pytest.mark.parametrize("second", SHORTER)
def test_m_a_shorter_later_ban_lets_no_call_onto_the_wire(
    vloop, vlimiter, pbot, second,
):
    out = _two_bans(pbot, vloop, BAN, second)
    assert out["into_ban"] == [], out["into_ban"]


@pytest.mark.parametrize("second", SHORTER)
def test_m_a_shorter_later_ban_still_refuses_the_queued_calls(
    vloop, vlimiter, pbot, second,
):
    """Every call still queued when the first ban landed ends in
    ``FloodMuted``."""
    out = _two_bans(pbot, vloop, BAN, second)
    assert (out["queued"].count("muted") >= out["pending_at_ban"] >= 1
            and "banned" not in out["queued"]), out


@pytest.mark.parametrize("second", SHORTER)
def test_m_a_shorter_later_ban_still_holds_an_answer_after_it_would_lapse(
    mk_bot, pbot, http, vloop, vlimiter, monkeypatch, second,
):
    """60 s after the shorter ban would have lapsed, a session ends its
    turn: its answer is held, and nothing reaches the wire."""
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", "")
    bot = _bot(mk_bot, pbot)
    out = _two_bans(pbot, vloop, BAN, second, bot=bot, http=http)
    assert out["held"] >= 1 and out["into_ban"] == [], out


def test_m_a_longer_later_ban_extends_a_shorter_mute(
    vloop, vlimiter, pbot,
):
    """Boundary, the other order: 600 s first, then 25,429 s. The longer
    deadline wins: 100 s after the 600 s one lapsed, still muted."""
    out = _two_bans(pbot, vloop, 600, BAN)
    assert out["muted_after_lapse"] is True, out["sampled_after"]


@pytest.mark.parametrize("second", SHORTER)
def test_m_a_shorter_direct_mute_does_not_shorten_a_longer_one(
    vloop, vlimiter, second,
):
    """The same rule when both mutes are armed directly: 25,429 s, then
    *second* ten seconds later; 60 s after the shorter one would have
    lapsed, the chat is still muted."""
    async def _go():
        MUTE.mute(CHAT, BAN)
        await asyncio.sleep(10.0)
        MUTE.mute(CHAT, second)
        await asyncio.sleep(second + 60.0)
        return MUTE.is_muted(CHAT)
    assert vloop.run_until_complete(_go()) is True


@pytest.mark.parametrize("seconds", SHORTER)
def test_m_a_mute_lapses_on_its_own_deadline(vloop, vlimiter, seconds):
    """Control for the rows above: a lone shorter mute HAS lapsed 60 s
    after its deadline, so "still muted" there is the longer deadline,
    not a stuck mute."""
    async def _go():
        MUTE.mute(CHAT, seconds)
        await asyncio.sleep(seconds + 60.0)
        return MUTE.is_muted(CHAT)
    assert vloop.run_until_complete(_go()) is False
