"""Multi-scope: each chat's reply keyboard follows its sessions.

Seen live on 2026-10-11 ("why mona is not in this keyboard?"): with more
than one chat set up, ``_update_bot_commands`` re-registered each chat's
``/`` menu but never sent a keyboard, so a chat kept the keyboard of its
last /start or menu tap and a new session had no button. Now the labels a
chat's shared main keyboard carried are recorded
(``registry.keyboard_labels``), and a refresh sends that chat's keyboard
again when its sessions no longer match, exactly once per change.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import group_intake
from aipager.bot.flood import MUTE
from aipager.bot.flood import _key as chat_key
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

DM, OTHER_DM, GROUP = 555, 777, -1001
KB = "⌨️"
OWNER = Member(id=DM, label="aly", role="owner")
SCOPES = [
    Scope(chat_id=DM, kind="dm", label="aly", members=(OWNER,)),
    Scope(chat_id=OTHER_DM, kind="dm", label="bo",
          members=(Member(id=OTHER_DM, label="bo", role="owner"),)),
    Scope(chat_id=GROUP, kind="group", label="g", members=(OWNER,)),
]


def _add(registry, label: str, chat: int, status: Status = Status.IDLE):
    name = f"claude-{label}"
    sess = registry.get_or_create(name)
    sess.label, sess.scope_chat_id = label, chat
    sess.scope_kind = "group" if chat < 0 else "dm"
    registry.transition(name, status)
    return sess


@pytest.fixture
def bot(mk_bot):
    b = mk_bot(scopes=list(SCOPES))
    b._app.bot.set_my_commands = AsyncMock()
    _add(b.registry, "jim", DM)
    _add(b.registry, "bo1", OTHER_DM)
    _add(b.registry, "gx", GROUP)
    # What each chat's keyboard shows now: its sessions, as recorded.
    for chat in (DM, OTHER_DM, GROUP):
        b.registry.set_keyboard_labels(chat, b.registry.live_labels(chat))
    return b


def _keyboards(b, chat=None):
    """The shared keyboards sent: (chat, button texts) per send."""
    out = []
    for c in b._app.bot.send_message.await_args_list:
        target = c.args[0] if c.args else c.kwargs.get("chat_id")
        text = c.args[1] if len(c.args) > 1 else c.kwargs.get("text")
        if text != KB:
            continue
        markup = c.kwargs["reply_markup"]
        buttons = [btn.text for row in markup.keyboard for btn in row]
        if chat is None or target == chat:
            out.append((target, buttons))
    return out


def _labels_in(buttons, chat):
    return {b for b in buttons if b in {
        group_intake.mark_label(lbl, chat) for lbl in ("jim", "mona", "bo1", "gx")}}


# ── a change sends one keyboard, to its chat only ───────────────────────────

def test_a_new_session_sends_its_chat_one_keyboard_with_it(bot, run_async):
    _add(bot.registry, "mona", DM)
    run_async(bot._update_bot_commands())
    sent = _keyboards(bot)
    assert [c for c, _ in sent] == [DM]
    assert _labels_in(sent[0][1], DM) == {"jim", "mona"}
    assert bot.registry.keyboard_labels_for(DM) == {"jim", "mona"}


def test_a_session_ending_sends_one_keyboard_without_it(bot, run_async):
    _add(bot.registry, "mona", DM)
    bot.registry.set_keyboard_labels(DM, {"jim", "mona"})
    bot.registry.transition("claude-mona", Status.GONE)
    run_async(bot._update_bot_commands())
    sent = _keyboards(bot)
    assert [c for c, _ in sent] == [DM]
    assert _labels_in(sent[0][1], DM) == {"jim"}


def test_no_change_sends_nothing_however_often_it_runs(bot, run_async):
    for _ in range(3):  # /status, a scan, a reload...
        run_async(bot._update_bot_commands())
    assert _keyboards(bot) == []
    _add(bot.registry, "mona", DM)
    for _ in range(3):
        run_async(bot._update_bot_commands())
    assert len(_keyboards(bot)) == 1


def test_a_group_gets_the_shared_keyboard(bot, run_async):
    _add(bot.registry, "mona", GROUP)
    run_async(bot._update_bot_commands())
    calls = [c for c in bot._app.bot.send_message.await_args_list
             if (c.args[0] if c.args else c.kwargs.get("chat_id")) == GROUP]
    assert len(calls) == 1
    markup = calls[0].kwargs["reply_markup"]
    assert not markup.selective  # everyone's keyboard, not a member's own
    assert "reply_to_message_id" not in calls[0].kwargs
    buttons = [b.text for row in markup.keyboard for b in row]
    assert group_intake.mark_label("mona", GROUP) in buttons
    assert bot.registry.keyboard_labels_for(GROUP) == {"gx", "mona"}


def test_single_scope_is_unchanged_one_keyboard_per_refresh(mk_bot, run_async):
    b = mk_bot()
    b._app.bot.set_my_commands = AsyncMock()
    _add(b.registry, "jim", DM)
    run_async(b._update_bot_commands())
    run_async(b._update_bot_commands())
    assert len(_keyboards(b)) == 2


# ── the record: first run, startup, persistence ─────────────────────────────

def test_no_record_sends_nothing_and_records(bot, run_async):
    bot.registry.keyboard_labels.clear()
    _add(bot.registry, "mona", DM)
    run_async(bot._update_bot_commands())
    assert _keyboards(bot) == []
    assert bot.registry.keyboard_labels_for(DM) == {"jim", "mona"}
    assert bot.registry.keyboard_labels_for(GROUP) == {"gx"}
    # And from then on a change is sent.
    bot.registry.transition("claude-mona", Status.GONE)
    run_async(bot._update_bot_commands())
    assert [c for c, _ in _keyboards(bot)] == [DM]


def _restarted(mk_bot, tmp_state_file, record: dict):
    """A daemon start: the saved state (sessions + keyboard record) loaded
    into a fresh registry, every session's socket alive."""
    sessions = {
        f"claude-{lbl}": {"name": f"claude-{lbl}", "label": lbl,
                          "scope_chat_id": chat,
                          "scope_kind": "group" if chat < 0 else "dm"}
        for lbl, chat in (("jim", DM), ("bo1", OTHER_DM), ("gx", GROUP))}
    tmp_state_file.write_text(json.dumps({
        "sessions": sessions, "msg_map": {}, "keyboard_labels": record}))
    r = SessionRegistry()
    r.load()
    b = mk_bot(r, scopes=list(SCOPES))
    b._app.bot.set_my_commands = AsyncMock()
    return b


def test_startup_with_nothing_changed_sends_nothing(mk_bot, tmp_state_file, run_async):
    b = _restarted(mk_bot, tmp_state_file, {
        str(DM): ["jim"], str(OTHER_DM): ["bo1"], str(GROUP): ["gx"]})
    run_async(b._update_bot_commands())
    assert _keyboards(b) == []


def test_startup_after_a_session_died_meanwhile_sends_that_chat_one(
        mk_bot, tmp_state_file, run_async):
    b = _restarted(mk_bot, tmp_state_file, {
        str(DM): ["jim", "mona"], str(OTHER_DM): ["bo1"], str(GROUP): ["gx"]})
    run_async(b._update_bot_commands())
    sent = _keyboards(b)
    assert [c for c, _ in sent] == [DM]
    assert _labels_in(sent[0][1], DM) == {"jim"}


def test_first_start_after_the_update_sends_nothing(mk_bot, tmp_state_file, run_async):
    b = _restarted(mk_bot, tmp_state_file, {})
    run_async(b._update_bot_commands())
    assert _keyboards(b) == []
    assert b.registry.keyboard_labels_for(DM) == {"jim"}
    assert b.registry._dirty


def test_the_record_survives_a_save_and_load(tmp_state_file):
    r = SessionRegistry()
    r.set_keyboard_labels(DM, {"b", "a"})
    r.set_keyboard_labels(GROUP, set())
    r.save()
    r2 = SessionRegistry()
    r2.load()
    assert r2.keyboard_labels == {DM: ["a", "b"], GROUP: []}


def test_junk_record_entries_are_dropped(tmp_state_file):
    tmp_state_file.write_text(json.dumps({"sessions": {}, "msg_map": {},
        "keyboard_labels": {str(DM): ["a"], "nope": ["b"], "1": "a",
                            "2": [1, "x"], "3": None, "4": [""]}}))
    r = SessionRegistry()
    r.load()
    assert r.keyboard_labels == {DM: ["a"]}
    tmp_state_file.write_text(json.dumps({"sessions": {}, "keyboard_labels": [1]}))
    r = SessionRegistry()
    r.load()
    assert r.keyboard_labels == {}


def test_the_record_follows_a_chat_migration():
    r = SessionRegistry()
    r.set_keyboard_labels(GROUP, {"gx"})
    r._dirty = False
    r.migrate_chat(GROUP, -100777)
    assert r.keyboard_labels == {-100777: ["gx"]}
    assert r._dirty


# ── what records and what does not ──────────────────────────────────────────

def test_sub_levels_and_a_members_own_keyboard_never_touch_the_record(
        bot, run_async, mk_update):
    _add(bot.registry, "mona", DM)
    _add(bot.registry, "mona2", GROUP)
    for level in ("templates", "commands", "models"):
        run_async(bot._send_keyboard(level=level, chat_id=DM))
    tap = mk_update("x", user_id=DM, chat_id=GROUP)
    run_async(bot._send_keyboard(level="main", chat_id=GROUP, tap=tap))
    assert tap.message.reply_text.await_count == 1  # the member's own one
    assert bot.registry.keyboard_labels_for(DM) == {"jim"}
    assert bot.registry.keyboard_labels_for(GROUP) == {"gx"}


def test_a_failed_send_does_not_record(bot, run_async):
    _add(bot.registry, "mona", DM)
    bot._app.bot.send_message = AsyncMock(side_effect=RuntimeError("down"))
    run_async(bot._send_keyboard(level="main", chat_id=DM))
    assert bot.registry.keyboard_labels_for(DM) == {"jim"}
    assert bot._keyboard_inflight == {}


def test_a_muted_chat_owes_the_keyboard_and_the_flush_records_it(bot, run_async):
    _add(bot.registry, "mona", DM)
    MUTE.mute(DM, 3600)
    run_async(bot._update_bot_commands())
    assert _keyboards(bot) == []
    assert chat_key(DM) in bot._keyboard_owed
    assert bot.registry.keyboard_labels_for(DM) == {"jim"}
    MUTE.clear()  # the ban lifted
    # A refresh before the monitor's flush leaves the owed one to it
    # (the flush waits for the chat's held answers; nothing overtakes
    # one), also for labels that changed again meanwhile.
    run_async(bot._update_bot_commands())
    _add(bot.registry, "tim", DM)
    run_async(bot._update_bot_commands())
    assert _keyboards(bot) == []
    assert run_async(bot.flush_owed_keyboards()) == 1
    assert [c for c, _ in _keyboards(bot)] == [DM]
    assert bot.registry.keyboard_labels_for(DM) == {"jim", "mona", "tim"}
    run_async(bot._update_bot_commands())
    assert len(_keyboards(bot)) == 1


def test_a_group_the_bot_cannot_reach_is_not_sent_one(bot, run_async):
    bot._unreachable_warned = {GROUP}
    _add(bot.registry, "mona", GROUP)
    run_async(bot._update_bot_commands())
    assert _keyboards(bot) == []


# ── overlapping triggers ────────────────────────────────────────────────────

def _slow_send(bot, gate: asyncio.Event | None = None):
    """send_message that yields before returning (a send to DM also waits
    for *gate*), counting how many sends are in flight at once."""
    state = {"now": 0, "most": 0}

    async def send(*args, **kwargs):
        state["now"] += 1
        state["most"] = max(state["most"], state["now"])
        try:
            target = args[0] if args else kwargs.get("chat_id")
            if gate is not None and target == DM:
                await gate.wait()
            for _ in range(5):
                await asyncio.sleep(0)
        finally:
            state["now"] -= 1
        return MagicMock()

    bot._app.bot.send_message = AsyncMock(side_effect=send)
    return state


def test_two_overlapping_refreshes_send_one_keyboard(bot, run_async):
    _slow_send(bot)
    _add(bot.registry, "mona", DM)

    async def go():
        await asyncio.gather(bot._update_bot_commands(),
                             bot._update_bot_commands())

    run_async(go())
    assert len(_keyboards(bot)) == 1


def test_quick_changes_end_on_the_final_set_one_send_at_a_time(bot, run_async):
    state = _slow_send(bot)

    async def go():
        _add(bot.registry, "mona", DM)
        first = asyncio.ensure_future(bot._update_bot_commands())
        for _ in range(2):
            await asyncio.sleep(0)  # the first send is on its way
        bot.registry.transition("claude-jim", Status.GONE)
        await asyncio.gather(first, bot._update_bot_commands())

    run_async(go())
    sent = _keyboards(bot, DM)
    assert state["most"] == 1  # never two keyboards racing to one chat
    assert _labels_in(sent[-1][1], DM) == {"mona"}
    assert bot.registry.keyboard_labels_for(DM) == {"mona"}


def test_a_keyboard_another_path_is_sending_counts_as_sent(bot, run_async):
    gate = asyncio.Event()
    _slow_send(bot, gate)
    _add(bot.registry, "mona", DM)

    async def go():
        # /start (or a Back tap) is sending this chat's main keyboard.
        start = asyncio.ensure_future(bot._send_keyboard(level="main", chat_id=DM))
        await asyncio.sleep(0)
        try:
            # Bounded: a refresh that waited on the held send would hang.
            await asyncio.wait_for(bot._update_bot_commands(), 5)
        finally:
            gate.set()
        await start

    run_async(go())
    assert len(_keyboards(bot)) == 1


def test_a_language_change_still_sends_exactly_one_keyboard(bot, run_async):
    _add(bot.registry, "mona", DM)  # changed, no refresh yet
    run_async(bot._on_language_changed(DM))
    run_async(bot._update_bot_commands())
    sent = _keyboards(bot)
    assert [c for c, _ in sent] == [DM]
    assert _labels_in(sent[0][1], DM) == {"jim", "mona"}


def test_a_send_the_flood_budget_skipped_does_not_record(bot, run_async):
    from aipager.bot.flood_budget import FloodSkipped
    _add(bot.registry, "mona", DM)
    bot._app.bot.send_message = AsyncMock(side_effect=FloodSkipped(DM, "sendMessage"))
    run_async(bot._send_keyboard(level="main", chat_id=DM))
    assert bot.registry.keyboard_labels_for(DM) == {"jim"}


def test_a_failed_keyboard_is_not_retried_until_the_labels_change(bot, run_async):
    _add(bot.registry, "mona", DM)
    bot._app.bot.send_message = AsyncMock(side_effect=RuntimeError("blocked"))
    for _ in range(3):  # /status, /status, a scan...
        run_async(bot._update_bot_commands())
    assert bot._app.bot.send_message.await_count == 1
    _add(bot.registry, "tim", DM)  # a real change: one more try
    run_async(bot._update_bot_commands())
    assert bot._app.bot.send_message.await_count == 2
    bot._app.bot.send_message = AsyncMock()
    _add(bot.registry, "zed", DM)
    run_async(bot._update_bot_commands())
    assert bot.registry.keyboard_labels_for(DM) == {"jim", "mona", "tim", "zed"}
    assert bot._keyboard_sync_failed == {}


def test_an_earlier_send_ending_keeps_a_later_ones_in_flight_mark(bot, run_async):
    """Two shared main keyboards on their way to one chat: the first one
    finishing must not clear the second's mark, or a refresh would send
    the second's labels again while it is still on its way."""
    first_gate, second_gate = asyncio.Event(), asyncio.Event()
    gates = [first_gate, second_gate]

    async def send(*args, **kwargs):
        await gates.pop(0).wait()
        return MagicMock()

    bot._app.bot.send_message = AsyncMock(side_effect=send)

    async def go():
        _add(bot.registry, "mona", DM)
        first = asyncio.ensure_future(bot._send_keyboard(level="main", chat_id=DM))
        await asyncio.sleep(0)
        _add(bot.registry, "tim", DM)
        second = asyncio.ensure_future(bot._send_keyboard(level="main", chat_id=DM))
        await asyncio.sleep(0)
        first_gate.set()
        await first
        await bot._update_bot_commands()  # labels {jim, mona, tim}: on their way
        second_gate.set()
        await second

    run_async(go())
    assert len(_keyboards(bot, DM)) == 2



def test_a_failed_mark_ends_when_another_path_brings_the_chat_up_to_date(bot, run_async):
    _add(bot.registry, "mona", DM)
    bot._app.bot.send_message = AsyncMock(side_effect=RuntimeError("transient"))
    run_async(bot._update_bot_commands())  # {jim, mona}: failed
    bot._app.bot.send_message = AsyncMock()
    _add(bot.registry, "tim", DM)
    run_async(bot._send_keyboard(level="main", chat_id=DM))  # /start: {jim, mona, tim}
    bot.registry.transition("claude-tim", Status.GONE)  # back to {jim, mona}
    run_async(bot._update_bot_commands())
    sent = _keyboards(bot, DM)
    assert len(sent) == 2
    assert _labels_in(sent[-1][1], DM) == {"jim", "mona"}


def test_a_muted_keyboard_is_sent_by_the_flush_and_then_follows_changes(bot, run_async):
    _add(bot.registry, "mona", DM)
    MUTE.mute(DM, 3600)
    run_async(bot._update_bot_commands())  # owed
    MUTE.clear()
    assert run_async(bot.flush_owed_keyboards()) == 1
    bot.registry.transition("claude-mona", Status.GONE)  # back to {jim}
    run_async(bot._update_bot_commands())
    sent = _keyboards(bot, DM)
    assert len(sent) == 2
    assert _labels_in(sent[-1][1], DM) == {"jim"}
