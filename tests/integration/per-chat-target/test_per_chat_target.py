"""The message target is per chat (F11, P2 of the command redesign,
2026-09-30).

It used to be one value for the whole install (`last_active_session`), set
by every outbound message of any session. On an install with more than one
chat, a plain message, voice note or file in chat A that was not a reply
was typed into chat B's session whenever B's session had spoken last, and a
bare /stop, /perms, /clearqueue, /diff or /now acted on it.

Now a session becomes the target of its OWN chat, and every fallback reads
`registry.target_for(<calling chat>)`.
"""

from __future__ import annotations

import ast
import json
import pathlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status, TrackedSession

ANA_CHAT, BOB_CHAT = 100, 200
ANA, BOB = "claude-ana__d100", "claude-bob__d200"


def _sess(name, label, chat, status=Status.IDLE):
    s = TrackedSession(name=name, label=label, status=status)
    s.scope_chat_id = chat
    return s


# ---- the registry --------------------------------------------------------------

def _name(sess):
    return getattr(sess, "name", None)


def _registry(*sessions):
    reg = SessionRegistry()
    for s in sessions:
        reg._sessions[s.name] = s
    return reg


def test_a_session_becomes_the_target_of_its_own_chat_only():
    reg = _registry(_sess(ANA, "ana", ANA_CHAT), _sess(BOB, "bob", BOB_CHAT))
    reg.last_active_session = ANA
    reg.last_active_session = BOB          # bob spoke last, anywhere

    assert _name(reg.target_for(ANA_CHAT)) == ANA
    assert _name(reg.target_for(BOB_CHAT)) == BOB
    assert reg.target_for(999) is None


def test_track_message_moves_only_the_sessions_own_chat():
    reg = _registry(_sess(ANA, "ana", ANA_CHAT), _sess(BOB, "bob", BOB_CHAT))
    reg.last_active_session = ANA
    reg.track_message(55, BOB, BOB_CHAT)

    assert _name(reg.target_for(ANA_CHAT)) == ANA


def test_a_session_with_no_chat_stamped_is_everyones_as_before():
    """Personal/legacy mode (no scopes, one chat), the rule `all_sessions`
    and `find_by_label` apply there: scope 0 matches any chat. The newest
    of the chat's own target and such a session wins. In scope mode an
    unstamped session is no chat's (roadmap 8.72,
    tests/test_session_no_any_chat.py)."""
    legacy = _sess("claude-old", "old", 0)
    reg = _registry(_sess(ANA, "ana", ANA_CHAT), legacy)
    reg.last_active_session = ANA
    reg.last_active_session = "claude-old"

    assert reg.target_for(ANA_CHAT) is legacy
    assert reg.target_for(BOB_CHAT) is legacy
    reg.last_active_session = ANA
    assert _name(reg.target_for(ANA_CHAT)) == ANA


def test_a_newer_target_that_left_the_chat_falls_back_to_the_chats_own():
    """Review-1 (001): the newest candidate became another chat's session;
    the chat's own target still stands."""
    u = _sess("claude-u", "u", 0)
    reg = _registry(_sess(ANA, "ana", ANA_CHAT), u)
    reg.last_active_session = ANA
    reg.last_active_session = "claude-u"      # no chat known: key 0
    u.scope_chat_id = 555                     # stamped into another chat

    assert _name(reg.target_for(ANA_CHAT)) == ANA


def test_a_session_is_the_target_of_one_chat_only():
    u = _sess("claude-u", "u", 0)
    reg = _registry(u)
    reg.last_active_session = "claude-u"      # key 0
    u.scope_chat_id = ANA_CHAT
    reg.last_active_session = "claude-u"      # now its own chat

    assert [n for n, _o in reg._targets.values()] == ["claude-u"]
    assert reg.target_for(BOB_CHAT) is None


def test_a_session_since_stamped_into_another_chat_is_not_this_chats():
    sess = _sess("claude-x", "x", 0)
    reg = _registry(sess)
    reg.last_active_session = "claude-x"      # recorded with no chat
    sess.scope_chat_id = BOB_CHAT             # the backfill stamps it later

    assert reg.target_for(ANA_CHAT) is None
    assert reg.target_for(BOB_CHAT) is sess


def test_no_chat_falls_back_to_the_install_wide_latest():
    reg = _registry(_sess(ANA, "ana", ANA_CHAT), _sess(BOB, "bob", BOB_CHAT))
    reg.last_active_session = BOB

    assert _name(reg.target_for(None)) == BOB


def test_clearing_the_target_clears_every_chat():
    reg = _registry(_sess(ANA, "ana", ANA_CHAT), _sess(BOB, "bob", BOB_CHAT))
    reg.last_active_session = ANA
    reg.last_active_session = BOB
    reg.last_active_session = ""

    assert reg.target_for(ANA_CHAT) is None and reg.target_for(BOB_CHAT) is None


def test_a_removed_session_is_no_ones_target():
    reg = _registry(_sess(ANA, "ana", ANA_CHAT), _sess(BOB, "bob", BOB_CHAT))
    reg.last_active_session = ANA
    reg.last_active_session = BOB
    reg.remove(ANA)

    assert reg.target_for(ANA_CHAT) is None
    assert _name(reg.target_for(BOB_CHAT)) == BOB
    assert reg.last_active_session == BOB
    # A new process under the same name (the monitor re-adopting a socket
    # after /kill) is not the target nobody chose.
    reg._sessions[ANA] = _sess(ANA, "ana", ANA_CHAT)
    assert reg.target_for(ANA_CHAT) is None


def test_the_targets_survive_a_restart(tmp_state_file):
    reg = SessionRegistry()
    for name, label, chat in ((ANA, "ana", ANA_CHAT), (BOB, "bob", BOB_CHAT)):
        reg.transition(name, Status.IDLE)
        reg.get(name).label, reg.get(name).scope_chat_id = label, chat
    reg.last_active_session = ANA
    reg.last_active_session = BOB
    reg.save()

    back = SessionRegistry()
    back.load()

    assert _name(back.target_for(ANA_CHAT)) == ANA
    assert _name(back.target_for(BOB_CHAT)) == BOB
    assert back.last_active_session == BOB
    back.last_active_session = ANA              # the order carries on
    assert _name(back.target_for(ANA_CHAT)) == ANA


def test_state_saved_before_per_chat_targets_still_routes(tmp_state_file):
    """An older state file has only `last_active_session`: it becomes the
    target of its own chat, where a single-chat install sent everything."""
    reg = SessionRegistry()
    reg.transition(ANA, Status.IDLE)
    reg.get(ANA).label, reg.get(ANA).scope_chat_id = "ana", ANA_CHAT
    reg.last_active_session = ANA
    reg.save()
    path = pathlib.Path(tmp_state_file)
    data = json.loads(path.read_text())
    data.pop("targets")
    path.write_text(json.dumps(data))

    back = SessionRegistry()
    back.load()

    assert _name(back.target_for(ANA_CHAT)) == ANA


def test_a_damaged_target_entry_is_skipped(tmp_state_file):
    reg = SessionRegistry()
    reg.transition(ANA, Status.IDLE)
    reg.get(ANA).label, reg.get(ANA).scope_chat_id = "ana", ANA_CHAT
    reg.last_active_session = ANA
    reg.save()
    path = pathlib.Path(tmp_state_file)
    data = json.loads(path.read_text())
    data["targets"].update({"x": ["claude-y", 1], "300": ["claude-gone", 2],
                            "400": "not-a-pair"})
    path.write_text(json.dumps(data))

    back = SessionRegistry()
    try:
        back.load()
    except Exception as exc:    # a damaged file must never stop the daemon
        pytest.fail(f"load raised on a damaged target entry: {exc!r}")

    assert _name(back.target_for(ANA_CHAT)) == ANA
    assert back.target_for(300) is None and back.target_for(400) is None


def test_the_order_carries_on_after_a_restart(tmp_state_file):
    """A chat's own target and a no-chat session's compete by order: a
    target made after the restart must count as newer than any saved."""
    reg = SessionRegistry()
    reg.transition(ANA, Status.IDLE)
    reg.get(ANA).label, reg.get(ANA).scope_chat_id = "ana", ANA_CHAT
    reg.transition("claude-old", Status.IDLE)
    reg.get("claude-old").label = "old"            # no chat stamped
    reg.last_active_session = ANA
    reg.last_active_session = "claude-old"
    reg.save()

    back = SessionRegistry()
    back.load()
    back.get("claude-old").scope_chat_id = 0       # load may stamp it; keep it chatless
    back.last_active_session = ANA

    assert _name(back.target_for(ANA_CHAT)) == ANA


def test_a_target_saved_before_its_session_was_stamped_follows_it(tmp_state_file):
    """Review-1 (001): the load's backfill stamps a session that had no
    chat; its saved target moves to that chat instead of shadowing
    another chat's own."""
    reg = SessionRegistry()
    reg.transition(ANA, Status.IDLE)
    reg.get(ANA).label, reg.get(ANA).scope_chat_id = "ana", ANA_CHAT
    reg.transition("claude-u", Status.IDLE)
    reg.get("claude-u").label = "u"
    reg.last_active_session = ANA
    reg.last_active_session = "claude-u"      # saved under key 0
    reg.save()

    back = SessionRegistry()
    back.load()
    back.get("claude-u").scope_chat_id = 555  # what a backfill does ...
    back._load_targets(json.loads(pathlib.Path(tmp_state_file).read_text())["targets"],
                       "claude-u")            # ... before the targets load

    assert _name(back.target_for(ANA_CHAT)) == ANA
    assert _name(back.target_for(555)) == "claude-u"
    assert 0 not in back._targets


def test_removing_the_newest_target_leaves_the_next_for_updates_with_no_chat():
    reg = _registry(_sess(ANA, "ana", ANA_CHAT), _sess(BOB, "bob", BOB_CHAT))
    reg.last_active_session = ANA
    reg.last_active_session = BOB
    reg.remove(BOB)

    assert _name(reg.target_for(None)) == ANA


def test_nothing_outside_the_registry_reads_the_install_wide_target():
    """Every routing fallback must ask for its own chat's target. Reading
    `last_active_session` anywhere else is the leak this fixed."""
    import aipager

    root = pathlib.Path(aipager.__file__).parent
    readers = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "state.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if (isinstance(node, ast.Attribute) and node.attr == "last_active_session"
                    and isinstance(node.ctx, ast.Load)):
                readers.append(f"{path.relative_to(root)}:{node.lineno}")
    assert readers == []


def test_the_reader_check_still_sees_real_code():
    """Pins the check above against passing vacuously: writers of the
    target exist in the bot, so the walk does reach it. (The bot writes
    it through `registry.set_target` since 8.90; the setter is the one
    store left, inside the registry.)"""
    import aipager

    root = pathlib.Path(aipager.__file__).parent
    setters = 0
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if (isinstance(node, ast.Attribute) and node.attr == "last_active_session"
                    and isinstance(node.ctx, ast.Store)):
                setters += 1
            elif (isinstance(node, ast.Attribute) and node.attr == "set_target"
                    and isinstance(node.ctx, ast.Load) and path.name != "state.py"):
                setters += 1
    assert setters >= 5


# ---- every fallback, from chat A with chat B's session spoken last --------------

@pytest.fixture
def two_chats(mk_bot, monkeypatch):
    scopes = [
        Scope(chat_id=ANA_CHAT, kind="dm", label="a",
              members=(Member(id=ANA_CHAT, label="ana", role="owner"),)),
        Scope(chat_id=BOB_CHAT, kind="dm", label="b",
              members=(Member(id=BOB_CHAT, label="bob", role="owner"),)),
    ]
    bot = mk_bot(scopes=scopes)
    bot.policy = load_policy()
    bot.registry._sessions[ANA] = _sess(ANA, "ana", ANA_CHAT)
    bot.registry._sessions[BOB] = _sess(BOB, "bob", BOB_CHAT)
    bot.registry.last_active_session = BOB
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot._inject_prompt = AsyncMock(return_value=True)
    bot._card_for_injected = AsyncMock()
    bot._react = AsyncMock()
    return bot


def _from_ana(mk_update, text=""):
    update = mk_update(text, chat_id=ANA_CHAT, user_id=ANA_CHAT)
    update.effective_chat.type = "private"
    return update


def _typed_into(bot):
    return [c.args[0].name for c in bot._inject_prompt.await_args_list]


def test_a_plain_message_never_goes_to_another_chats_session(two_chats, mk_update, run_async):
    update = _from_ana(mk_update, "hello")
    run_async(two_chats._handle_message(update, MagicMock()))

    assert BOB not in _typed_into(two_chats)
    assert "don't know which session" in update.message.reply_text.await_args.args[0]


def test_a_plain_message_goes_to_its_own_chats_target(two_chats, mk_update, run_async):
    two_chats.registry.last_active_session = ANA
    two_chats.registry.last_active_session = BOB     # bob spoke after

    run_async(two_chats._handle_message(_from_ana(mk_update, "hello"), MagicMock()))

    assert _typed_into(two_chats) == [ANA]


# Each row runs twice: with only chat B's session as a target (it must not
# be touched), and with chat A's own session made the target first (it must
# be the one acted on). The second proves the handler really ran.

@pytest.fixture(params=[False, True], ids=["only-b-is-a-target", "a-has-its-own"])
def ana_target(request, two_chats):
    if request.param:
        two_chats.registry.last_active_session = ANA
        two_chats.registry.last_active_session = BOB     # bob spoke after
    return [ANA] if request.param else []


def test_a_voice_note_goes_only_to_its_own_chats_target(two_chats, ana_target, mk_update,
                                                        run_async):
    run_async(two_chats._dispatch_voice_transcript(_from_ana(mk_update), "hello"))

    assert _typed_into(two_chats) == ana_target


def test_a_file_goes_only_to_its_own_chats_target(two_chats, ana_target, mk_update,
                                                  run_async, tmp_path):
    run_async(two_chats._inject_file_prompt(
        _from_ana(mk_update), MagicMock(), "look", [tmp_path / "a.png"],
        all_photos=True, log_name="a.png"))

    assert _typed_into(two_chats) == ana_target


def test_bare_stop_stops_only_its_own_chats_session(two_chats, ana_target, mk_update,
                                                    run_async):
    """Since P4 (4.4) a bare /stop stops this chat's one working session,
    target or not; never another chat's."""
    for name in (ANA, BOB):
        two_chats.registry.get(name).status = Status.BUSY
    two_chats._stop_session = AsyncMock(return_value=MagicMock(ok=True))
    run_async(two_chats._handle_stop_cmd(_from_ana(mk_update, "/stop"), MagicMock()))

    assert [c.args[0].name for c in two_chats._stop_session.await_args_list] == [ANA]


def test_bare_clearqueue_clears_only_its_own_chats_target(two_chats, ana_target, mk_update,
                                                          run_async):
    two_chats._clear_queue_core = AsyncMock(return_value=MagicMock(ok=False))
    run_async(two_chats._handle_clearqueue_cmd(_from_ana(mk_update, "/clearqueue"),
                                                MagicMock()))

    assert [c.args[0].name for c in two_chats._clear_queue_core.await_args_list] == ana_target


def test_bare_perms_switches_only_its_own_chats_target(two_chats, ana_target, mk_update,
                                                       run_async):
    two_chats._kill_and_relaunch_core = AsyncMock(return_value=MagicMock(ok=True))
    update = _from_ana(mk_update, "/perms")
    run_async(two_chats._handle_perms_cmd(update, MagicMock()))

    # Since P4 a bare /perms (= /mode) shows the mode of this chat's target,
    # or of its one live session: ana's either way, never bob's.
    acted = [c.args[0].name for c in two_chats._kill_and_relaunch_core.await_args_list]
    replies = " ".join(str(c.args[0]) for c in update.message.reply_text.await_args_list
                       if c.args)
    assert acted == []
    assert "<b>ana</b>" in replies and "bob" not in replies


def test_bare_diff_shows_only_its_own_chats_target(two_chats, ana_target, mk_update,
                                                   run_async, monkeypatch):
    from aipager.bot import session_parity

    shown = []

    async def _diff(bot, sess, **k):
        shown.append(sess.name)
    monkeypatch.setattr(session_parity, "_run_diff", _diff)
    update = _from_ana(mk_update, "/diff")
    run_async(session_parity.handle_diff_cmd(two_chats, update, MagicMock()))

    # Since P4 a bare /diff with no target shows this chat's one live
    # session: ana's either way, never bob's.
    assert shown == [ANA]


def test_now_sends_only_its_own_chats_queue(two_chats, ana_target, mk_update, run_async):
    two_chats._send_now_core = AsyncMock(return_value=MagicMock(result="nothing"))
    run_async(two_chats._handle_now_cmd(_from_ana(mk_update, "/now"), MagicMock()))

    assert [c.args[0].name for c in two_chats._send_now_core.await_args_list] == ana_target


def test_a_template_or_claude_command_goes_only_to_its_own_chats_target(
        two_chats, ana_target, mk_update, run_async):
    two_chats._confirm_model_feedback = AsyncMock()
    run_async(two_chats._send_template(_from_ana(mk_update, "Continue"), "continue"))
    run_async(two_chats._send_command(_from_ana(mk_update, "Compact"), "/compact"))

    assert _typed_into(two_chats) == ana_target * 2
