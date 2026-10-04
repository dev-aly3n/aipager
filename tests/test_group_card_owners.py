"""Whose card each group message is, across a restart, and the cards
delivery 18 left unbound (roadmap 8.94 h, j; delivery 21).

- (h) The owner map (``bot/card_owner.py``) is saved with the session
  registry: a confirm card bound before a restart still answers only its
  requester after it. At most 4096 newest records, on disk too; a state
  file saved before the map existed loads; a chat that is no longer a
  group scope, and a group Telegram moved to a new id, lose theirs.
- (j) The ``/stop``, ``/rename`` and ``/diff`` pickers and the "Resume x1
  as:" card a Resume tap draws answer only the person who asked for them.

Groups cannot be tested live; these tests are the proof. A DM and a
personal-mode group record nothing and refuse nothing, as before.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import scope as scope_mod
from aipager import state as state_mod
from aipager.bot import card_owner, session_parity
from aipager.bot.session_ops import StopOutcome
from aipager.dtach import inject
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status, TrackedSession
from tests.test_group_attribution_senders import (
    ALICE, BOB, D1, DAVE, DM, G, OTHER_G, X1, X2,
    _cb, _edit_texts, _msg, _registry, _run_tap, _scopes, _sess, _toasts,
    _track_sent,
)

D2 = "claude-d2"


def _rec(user=ALICE, kind="end", command="/kill x1", picker=False, label="@alice"):
    return {"user_id": user, "label": label, "kind": kind,
            "command": command, "picker": picker}


def _write_config(scopes) -> None:
    scope_mod.dump_scopes(scopes, "123456:FAKE", path=scope_mod.CONFIG_PATH)


def _state_file() -> Path:
    return Path(state_mod.SESSION_STATE_FILE)


def _write_state(data: dict) -> None:
    _state_file().parent.mkdir(parents=True, exist_ok=True)
    _state_file().write_text(json.dumps(data))


def _wire(bot, r, monkeypatch):
    from aipager.policy import load_policy
    bot.policy = load_policy()
    monkeypatch.setattr(inject, "is_alive",
                        AsyncMock(side_effect=lambda name: name in r._sessions))
    bot._inject_prompt = AsyncMock(return_value=True)
    bot._card_for_injected = AsyncMock()
    bot._react = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()
    bot.refresh_pinned = AsyncMock()
    return bot


@pytest.fixture
def gbot(mk_bot, monkeypatch):
    r = _registry()
    return _wire(mk_bot(r, scopes=_scopes()), r, monkeypatch)


@pytest.fixture
def pbot(mk_bot, monkeypatch):
    """Personal mode (no scopes, no team): the group admits the operator."""
    r = _registry()
    return _wire(mk_bot(r), r, monkeypatch)


def _restart(mk_bot, monkeypatch, *, scopes=None):
    """What a daemon restart does: save, a fresh registry loads the file,
    and a fresh bot is built on it (its constructor reads aipager.yaml)."""
    r = SessionRegistry()
    r.load()
    bot = mk_bot(r, scopes=scopes if scopes is not None else _scopes())
    return _wire(bot, r, monkeypatch)


# =============================================================================
# (h) the owner map is saved with the registry
# =============================================================================

def test_kinds_on_disk_match_the_cards_card_owner_knows():
    assert set(card_owner._GUARDED) == set(state_mod.CARD_OWNER_KINDS)
    assert set(card_owner._PICKER_ROW) == set(state_mod.CARD_OWNER_KINDS)
    assert card_owner.MAX_RECORDS == state_mod.MAX_CARD_OWNERS == 4096


def test_a_kill_card_still_refuses_another_member_after_a_restart(
        gbot, run_async, mk_update, mk_bot, monkeypatch):
    _write_config(_scopes())
    u = _msg(mk_update, "/kill x1", ALICE)
    ids = _track_sent(u)
    run_async(gbot._handle_kill_cmd(u, MagicMock()))
    card = ids[-1]
    gbot.registry.save()

    bot = _restart(mk_bot, monkeypatch)
    assert (G, card) in bot.registry.card_owners
    bot._kill_session_core = AsyncMock(return_value=SimpleNamespace(result="killed"))
    bot._render_status_list = MagicMock(return_value=("LIST", None))
    turn = _sess(bot, X1).turn_key
    q = _run_tap(bot, run_async, _cb(bot, G, X1, f"endok{turn}"), BOB, message_id=card)
    assert _toasts(q) == ["This is @alice's card. Send /kill x1 for your own."]
    bot._kill_session_core.assert_not_awaited()
    q = _run_tap(bot, run_async, _cb(bot, G, X1, "kill-cancel"), BOB, message_id=card)
    assert _toasts(q) == ["This is @alice's card. Send /kill x1 for your own."]
    # The requester's own tap still ends it, and frees the card.
    _run_tap(bot, run_async, _cb(bot, G, X1, f"endok{turn}"), ALICE, message_id=card)
    bot._kill_session_core.assert_awaited_once()
    assert (G, card) not in bot.registry.card_owners


def test_a_mode_confirm_still_refuses_another_member_after_a_restart(
        gbot, run_async, mk_bot, monkeypatch):
    _write_config(_scopes())
    card_owner.claim(gbot, G, 900, ALICE, kind="mode", command="/mode x1")
    gbot.registry.save()
    bot = _restart(mk_bot, monkeypatch)
    assert card_owner.refusal(bot, G, 900, DAVE, X1, "perms_confirm") == (
        "This is @alice's card. Send /mode x1 for your own.")
    assert card_owner.refusal(bot, G, 900, ALICE, X1, "perms_confirm") is None


def test_claim_and_release_mark_the_registry_for_saving(gbot):
    gbot.registry._dirty = False
    card_owner.claim(gbot, G, 5, ALICE, kind="end", command="/kill x1")
    assert gbot.registry._dirty
    gbot.registry._dirty = False
    card_owner.release(gbot, G, 6)          # nothing there: nothing to save
    assert not gbot.registry._dirty
    card_owner.release(gbot, G, 5)
    assert gbot.registry._dirty


def test_the_saved_map_keeps_the_newest_4096(gbot):
    owners = gbot.registry.card_owners
    for mid in range(1, 4101):
        owners[(G, mid)] = _rec()
    gbot.registry.save()
    saved = json.loads(_state_file().read_text())["card_owners"]
    assert len(saved) == 4096
    assert (saved[0]["chat_id"], saved[0]["msg_id"]) == (G, 5)
    assert (saved[-1]["chat_id"], saved[-1]["msg_id"]) == (G, 4100)
    assert saved[-1] == {"chat_id": G, "msg_id": 4100, **_rec()}


def test_a_load_keeps_the_newest_4096(tmp_path):
    recs = [{"chat_id": G, "msg_id": mid, **_rec()} for mid in range(1, 4101)]
    _write_state({"version": 1, "sessions": {},
                                         "card_owners": recs})
    r = SessionRegistry()
    r.load()
    assert len(r.card_owners) == 4096
    assert list(r.card_owners)[0] == (G, 5)
    assert list(r.card_owners)[-1] == (G, 4100)


def test_claims_in_memory_stay_capped(gbot, monkeypatch):
    monkeypatch.setattr(card_owner, "MAX_RECORDS", 3)
    for mid in range(1, 6):
        card_owner.claim(gbot, G, mid, ALICE, kind="end", command="/kill x1")
    assert list(gbot.registry.card_owners) == [(G, 3), (G, 4), (G, 5)]


def test_a_state_file_from_before_the_map_loads():
    sd = {"name": X1, "label": "x1", "scope_chat_id": G, "scope_kind": "group"}
    _write_state({"version": 1, "sessions": {X1: sd}})
    r = SessionRegistry()
    r.load()
    assert r.get(X1) is not None
    assert r.card_owners == {}


@pytest.mark.parametrize("bad", [
    {"chat_id": DM, "msg_id": 5},                 # a private chat: never
    {"chat_id": G, "msg_id": 0},
    {"chat_id": G, "msg_id": "5"},
    {"chat_id": G, "msg_id": 5, "kind": "nope"},
    {"chat_id": G, "msg_id": 5, "user_id": True},
    {"chat_id": G, "msg_id": 5, "picker": 1},
    {"chat_id": G, "msg_id": 5, "label": None},
    {"chat_id": G, "msg_id": 5, "command": None},
    "junk",
])
def test_a_malformed_or_private_record_is_dropped_at_load(bad):
    rec = {**_rec(), **bad} if isinstance(bad, dict) else bad
    good = {"chat_id": G, "msg_id": 9, **_rec()}
    _write_state({"version": 1, "sessions": {},
                                         "card_owners": [rec, good]})
    r = SessionRegistry()
    r.load()
    assert list(r.card_owners) == [(G, 9)]


@pytest.mark.parametrize("value", [{"x": 1}, 5, "junk", None])
def test_a_card_owners_value_of_the_wrong_type_loads_empty(value):
    _write_state({"version": 1, "sessions": {}, "card_owners": value})
    r = SessionRegistry()
    try:
        r.load()
        crashed = None
    except Exception as e:      # the daemon would not start
        crashed = e
    assert crashed is None
    assert r.card_owners == {}


def test_a_removed_scope_loses_its_cards_at_start(mk_bot, monkeypatch):
    _write_config(_scopes())        # G and alice's DM; OTHER_G is gone
    r = SessionRegistry()
    r.card_owners[(G, 1)] = _rec()
    r.card_owners[(OTHER_G, 2)] = _rec()
    r.save()
    bot = _restart(mk_bot, monkeypatch)
    assert list(bot.registry.card_owners) == [(G, 1)]
    assert bot.registry._dirty


def test_personal_mode_drops_every_saved_card(mk_bot, monkeypatch):
    # No aipager.yaml: personal mode, where nothing is anyone's.
    r = SessionRegistry()
    r.card_owners[(G, 1)] = _rec()
    r.save()
    r2 = SessionRegistry()
    r2.load()
    assert list(r2.card_owners) == [(G, 1)]
    bot = mk_bot(r2)
    assert bot.registry.card_owners == {}


def test_legacy_team_mode_keeps_only_its_own_chat(gbot, monkeypatch):
    from aipager import config
    monkeypatch.setattr(config, "CHAT_ID", str(G))
    gbot.scopes, gbot.team = None, object()
    gbot.registry.card_owners.update({(G, 1): _rec(), (OTHER_G, 2): _rec()})
    card_owner.prune(gbot)
    assert list(gbot.registry.card_owners) == [(G, 1)]
    # No usable chat id to tell them by: kept (fails closed).
    monkeypatch.setattr(config, "CHAT_ID", "")
    gbot.registry.card_owners[(OTHER_G, 2)] = _rec()
    card_owner.prune(gbot)
    assert list(gbot.registry.card_owners) == [(G, 1), (OTHER_G, 2)]


def test_a_dm_scope_with_a_group_like_record_is_not_kept(gbot):
    """Only GROUP scopes keep records (a DM never has one)."""
    gbot.scopes = [Scope(chat_id=G, kind="dm", label="odd", members=(
        Member(id=ALICE, label="alice", role="owner"),))]
    gbot.registry.card_owners[(G, 1)] = _rec()
    card_owner.prune(gbot)
    assert gbot.registry.card_owners == {}


def test_a_live_reload_drops_a_removed_groups_cards(gbot, run_async, monkeypatch):
    other = Scope(chat_id=OTHER_G, kind="group", label="other", members=(
        Member(id=ALICE, label="alice", role="owner"),))
    gbot.scopes = _scopes() + [other]
    gbot.registry.card_owners.update({(G, 1): _rec(), (OTHER_G, 2): _rec()})
    _write_config(_scopes())
    gbot._refresh_scope_menus = AsyncMock()
    monkeypatch.setattr("aipager.bot.live_reload.reach_work_in_flight", AsyncMock())
    from aipager import team as team_mod

    def _broken_team(*a, **k):
        raise team_mod.TeamConfigError("bad team.yaml")
    # The legacy team step fails and returns early: the scope reload alone
    # must have dropped the removed group's cards.
    monkeypatch.setattr(team_mod, "load_team", _broken_team)
    run_async(gbot.reload_team())
    assert [s.chat_id for s in gbot.scopes] == [G, DM]
    assert list(gbot.registry.card_owners) == [(G, 1)]


def _legacy_reload(bot, run_async, monkeypatch, new_team):
    from aipager import team as team_mod
    monkeypatch.setattr(team_mod, "load_team", lambda *a, **k: new_team)
    run_async(bot.reload_team())        # no aipager.yaml: no scope reload


def test_a_legacy_team_reload_prunes_to_its_chat(gbot, run_async, monkeypatch):
    from aipager import config
    monkeypatch.setattr(config, "CHAT_ID", str(G))
    gbot.scopes, gbot.team = None, None
    gbot.registry.card_owners.update({(G, 1): _rec(), (OTHER_G, 2): _rec()})
    _legacy_reload(gbot, run_async, monkeypatch, MagicMock())
    assert gbot.scopes is None and gbot.team is not None
    assert list(gbot.registry.card_owners) == [(G, 1)]


def test_a_reload_back_to_personal_mode_drops_every_card(gbot, run_async, monkeypatch):
    gbot.scopes, gbot.team = None, MagicMock()
    gbot.registry.card_owners[(G, 1)] = _rec()
    _legacy_reload(gbot, run_async, monkeypatch, None)
    assert gbot.team is None
    assert gbot.registry.card_owners == {}


def test_a_migrated_chat_loses_its_cards(gbot):
    new = -1005555
    gbot.registry.card_owners.update({(G, 1): _rec(), (OTHER_G, 2): _rec()})
    gbot.registry._dirty = False
    gbot.registry.migrate_chat(G, new)
    assert list(gbot.registry.card_owners) == [(OTHER_G, 2)]
    assert gbot.registry._dirty
    # A chat with nothing else to move: the dropped cards alone are saved.
    gbot.registry._dirty = False
    gbot.registry.migrate_chat(OTHER_G, new - 1)
    assert gbot.registry.card_owners == {}
    assert gbot.registry._dirty


def test_a_migration_recorded_while_down_drops_the_old_ids_cards_at_load():
    new = -1005555
    _write_config(_scopes())
    scope_mod.migrate_scope_chat_id(G, new)
    r = SessionRegistry()
    s = TrackedSession(name=X1, label="x1", status=Status.IDLE)
    s.scope_chat_id, s.scope_kind = G, "group"
    r._sessions[X1] = s
    r.card_owners.update({(G, 1): _rec(), (OTHER_G, 2): _rec()})
    r.save()
    r2 = SessionRegistry()
    r2.load()
    assert r2.get(X1).scope_chat_id == new
    assert list(r2.card_owners) == [(OTHER_G, 2)]


def test_personal_mode_refuses_nothing_even_with_a_record(pbot):
    pbot.registry.card_owners[(G, 5)] = _rec()
    assert card_owner.refusal(pbot, G, 5, BOB, X1, "endok3") is None


def test_a_dm_kill_card_after_a_restart_is_still_nobodys(
        gbot, run_async, mk_update, mk_bot, monkeypatch):
    """The operator's own install: nothing recorded, saved or refused."""
    _write_config(_scopes())
    u = _msg(mk_update, "/kill d1", ALICE, chat=DM)
    ids = _track_sent(u)
    run_async(gbot._handle_kill_cmd(u, MagicMock()))
    gbot.registry.save()
    assert json.loads(_state_file().read_text())["card_owners"] == []
    bot = _restart(mk_bot, monkeypatch)
    assert card_owner.refusal(bot, DM, ids[-1], 999, D1, "kill-cancel") is None


# =============================================================================
# (j) the /stop, /rename and /diff pickers
# =============================================================================

def _busy(bot, *names):
    for name in names:
        _sess(bot, name).status = Status.BUSY


def _add_d2(bot):
    s = TrackedSession(name=D2, label="d2", status=Status.IDLE)
    s.scope_chat_id, s.scope_kind = DM, "dm"
    bot.registry._sessions[D2] = s


def _picker(bot, run_async, mk_update, text, user, *, chat=G, handler):
    u = _msg(mk_update, text, user, chat=chat)
    ids = _track_sent(u)
    run_async(handler(u, MagicMock()))
    call = u.message.reply_text.await_args
    return ids[-1], call.args[0], call.kwargs.get("reply_markup")


def _stop(bot):
    bot._stop_session_core = AsyncMock(
        side_effect=lambda sess, by="": StopOutcome(ok=True, label=sess.label, by=by))


def test_the_stop_picker_answers_only_its_requester(gbot, run_async, mk_update):
    _busy(gbot, X1, X2)
    _stop(gbot)
    card, text, _kb = _picker(gbot, run_async, mk_update, "/stop", ALICE,
                              handler=gbot._handle_stop_cmd)
    assert text == "Which one to stop?"
    turn = _sess(gbot, X1).turn_key
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, f"pstop{turn}"), BOB, message_id=card)
    assert _toasts(q) == ["This is @alice's card. Send /stop for your own."]
    gbot._stop_session_core.assert_not_awaited()
    q = _run_tap(gbot, run_async, "_:pick:cancel", BOB, message_id=card)
    assert _toasts(q) == ["This is @alice's card. Send /stop for your own."]
    assert _edit_texts(q) == []
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, f"pstop{turn}"), ALICE, message_id=card)
    gbot._stop_session_core.assert_awaited_once()
    assert _edit_texts(q) == ["⏹ Stopped <b>x1</b> by @alice"]
    # The picker is the result now: nobody's.
    assert (G, card) not in gbot.registry.card_owners


def test_a_refused_stop_keeps_the_picker_its_requesters(gbot, run_async, mk_update):
    _busy(gbot, X1, X2)
    gbot._stop_session_core = AsyncMock(
        return_value=StopOutcome(ok=False, label="x1", reason="not_busy"))
    card, _t, _kb = _picker(gbot, run_async, mk_update, "/stop", ALICE,
                            handler=gbot._handle_stop_cmd)
    turn = _sess(gbot, X1).turn_key
    _run_tap(gbot, run_async, _cb(gbot, G, X1, f"pstop{turn}"), ALICE, message_id=card)
    assert (G, card) in gbot.registry.card_owners


def test_the_stop_picker_in_a_dm_is_unchanged(gbot, run_async, mk_update):
    _add_d2(gbot)
    _busy(gbot, D1, D2)
    _stop(gbot)
    card, text, kb = _picker(gbot, run_async, mk_update, "/stop", ALICE, chat=DM,
                             handler=gbot._handle_stop_cmd)
    assert text == "Which one to stop?"
    assert [[b.text for b in row] for row in kb.inline_keyboard] == [
        ["⏹ d1"], ["⏹ d2"], ["✖️ Cancel"]]
    assert gbot.registry.card_owners == {}
    turn = _sess(gbot, D1).turn_key
    q = _run_tap(gbot, run_async, _cb(gbot, DM, D1, f"pstop{turn}"), ALICE,
                 chat=DM, message_id=card)
    assert _edit_texts(q) == ["⏹ Stopped <b>d1</b>"]


def test_the_stop_picker_in_a_personal_group_is_anyones(pbot, run_async, mk_update):
    _busy(pbot, X1, X2)
    _stop(pbot)
    card, _t, _kb = _picker(pbot, run_async, mk_update, "/stop", ALICE,
                            handler=pbot._handle_stop_cmd)
    assert pbot.registry.card_owners == {}
    turn = _sess(pbot, X1).turn_key
    _run_tap(pbot, run_async, _cb(pbot, G, X1, f"pstop{turn}"), BOB, message_id=card)
    pbot._stop_session_core.assert_awaited_once()


def _rename_cmd(bot):
    return lambda u, ctx: session_parity.handle_rename_cmd(bot, u, ctx)


def test_the_rename_picker_answers_only_its_requester(gbot, run_async, mk_update):
    card, text, _kb = _picker(gbot, run_async, mk_update, "/rename", ALICE,
                              handler=_rename_cmd(gbot))
    assert text == "Which session to rename?"
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, "rename"), BOB, message_id=card)
    assert _toasts(q) == ["This is @alice's card. Send /rename for your own."]
    assert _edit_texts(q) == []
    assert not session_parity._rename_pending_map(gbot)
    q = _run_tap(gbot, run_async, "_:pick:cancel", BOB, message_id=card)
    assert _toasts(q) == ["This is @alice's card. Send /rename for your own."]
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, "rename"), ALICE, message_id=card)
    assert (G, ALICE) in session_parity._rename_pending_map(gbot)
    assert len(_edit_texts(q)) == 1
    # Now the name question, which answers its asker by its own record.
    assert (G, card) not in gbot.registry.card_owners
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, "rename-cancel"), BOB, message_id=card)
    assert _toasts(q) == ["This isn't your rename."]


def test_the_rename_picker_in_a_dm_is_unchanged(gbot, run_async, mk_update):
    _add_d2(gbot)
    card, text, kb = _picker(gbot, run_async, mk_update, "/rename", ALICE, chat=DM,
                             handler=_rename_cmd(gbot))
    assert text == "Which session to rename?"
    assert [[b.text for b in row] for row in kb.inline_keyboard] == [
        ["✏️ d1"], ["✏️ d2"], ["✖️ Cancel"]]
    assert gbot.registry.card_owners == {}


def test_the_rename_picker_in_a_personal_group_is_anyones(pbot, run_async, mk_update):
    card, _t, _kb = _picker(pbot, run_async, mk_update, "/rename", ALICE,
                            handler=_rename_cmd(pbot))
    assert pbot.registry.card_owners == {}
    _run_tap(pbot, run_async, _cb(pbot, G, X1, "rename"), BOB, message_id=card)
    assert (G, BOB) in session_parity._rename_pending_map(pbot)


def _diff_cmd(bot):
    return lambda u, ctx: session_parity.handle_diff_cmd(bot, u, ctx)


def test_the_diff_picker_answers_only_its_requester(gbot, run_async, mk_update, monkeypatch):
    ran = AsyncMock()
    monkeypatch.setattr(session_parity, "_run_diff", ran)
    card, text, _kb = _picker(gbot, run_async, mk_update, "/diff", ALICE,
                              handler=_diff_cmd(gbot))
    assert text == "Which session's diff?"
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, "diff"), BOB, message_id=card)
    assert _toasts(q) == ["This is @alice's card. Send /diff for your own."]
    ran.assert_not_awaited()
    q = _run_tap(gbot, run_async, "_:pick:cancel", BOB, message_id=card)
    assert _toasts(q) == ["This is @alice's card. Send /diff for your own."]
    _run_tap(gbot, run_async, _cb(gbot, G, X2, "diff"), ALICE, message_id=card)
    assert ran.await_args.args[1] is _sess(gbot, X2)
    # The diff is replied under the picker, which stays hers.
    assert (G, card) in gbot.registry.card_owners


def test_the_diff_picker_in_a_dm_is_unchanged(gbot, run_async, mk_update, monkeypatch):
    _add_d2(gbot)
    monkeypatch.setattr(session_parity, "_run_diff", AsyncMock())
    card, text, kb = _picker(gbot, run_async, mk_update, "/diff", ALICE, chat=DM,
                             handler=_diff_cmd(gbot))
    assert text == "Which session's diff?"
    assert [[b.text for b in row] for row in kb.inline_keyboard] == [
        ["📝 d1"], ["📝 d2"], ["✖️ Cancel"]]
    assert gbot.registry.card_owners == {}


def test_the_diff_picker_in_a_personal_group_is_anyones(pbot, run_async, mk_update,
                                                        monkeypatch):
    ran = AsyncMock()
    monkeypatch.setattr(session_parity, "_run_diff", ran)
    card, _t, _kb = _picker(pbot, run_async, mk_update, "/diff", ALICE,
                            handler=_diff_cmd(pbot))
    assert pbot.registry.card_owners == {}
    _run_tap(pbot, run_async, _cb(pbot, G, X1, "diff"), BOB, message_id=card)
    ran.assert_awaited_once()


# =============================================================================
# (j) the "Resume x1 as:" card
# =============================================================================

def _gone(bot, name):
    s = _sess(bot, name)
    s.status = Status.GONE
    s.gone_at = 1.0
    s.claude_session_id = "abc"
    bot._do_resume = AsyncMock()
    return s


def test_the_resume_mode_buttons_are_the_resumers(gbot, run_async):
    _gone(gbot, X1)
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, "resume"), ALICE, message_id=800)
    assert _edit_texts(q) == ["Resume <b>x1</b> as:"]
    for user, verb in ((BOB, "resume-ask"), (DAVE, "resume-auto"), (BOB, "resume-cancel")):
        q = _run_tap(gbot, run_async, _cb(gbot, G, X1, verb), user, message_id=800)
        assert _toasts(q) == ["This is @alice's card."], verb
        assert _edit_texts(q) == []
    gbot._do_resume.assert_not_awaited()
    _run_tap(gbot, run_async, _cb(gbot, G, X1, "resume-ask"), ALICE, message_id=800)
    gbot._do_resume.assert_awaited_once()
    assert gbot._do_resume.await_args.kwargs["skip_perms_override"] is False
    assert (G, 800) not in gbot.registry.card_owners


def test_the_resumer_cancels_their_own_resume_card(gbot, run_async):
    _gone(gbot, X1)
    _run_tap(gbot, run_async, _cb(gbot, G, X1, "resume"), BOB, message_id=801)
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, "resume-cancel"), BOB, message_id=801)
    assert _edit_texts(q) == ["↩️ Cancelled."]
    assert (G, 801) not in gbot.registry.card_owners


def test_the_resume_card_after_a_which_session_card_becomes_the_resumers(gbot, run_async):
    """The ended "Which session?" card's ▶️ Resume: the card was the
    asker's ("ask"), and its mode buttons stay theirs."""
    _gone(gbot, X1)
    card_owner.claim(gbot, G, 802, BOB, kind="ask", command="", picker=True)
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, "resume"), ALICE, message_id=802)
    assert _toasts(q) == ["This is @bob's card."]
    _run_tap(gbot, run_async, _cb(gbot, G, X1, "resume"), BOB, message_id=802)
    assert gbot.registry.card_owners[(G, 802)]["kind"] == "resume"
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, "resume-ask"), ALICE, message_id=802)
    assert _toasts(q) == ["This is @bob's card."]


def test_the_resume_card_in_a_dm_is_unchanged(gbot, run_async):
    _gone(gbot, D1)
    q = _run_tap(gbot, run_async, _cb(gbot, DM, D1, "resume"), ALICE, chat=DM, message_id=803)
    assert _edit_texts(q) == ["Resume <b>d1</b> as:"]
    assert gbot.registry.card_owners == {}
    _run_tap(gbot, run_async, _cb(gbot, DM, D1, "resume-ask"), ALICE, chat=DM, message_id=803)
    gbot._do_resume.assert_awaited_once()


def test_the_resume_card_in_a_personal_group_is_anyones(pbot, run_async):
    _gone(pbot, X1)
    _run_tap(pbot, run_async, _cb(pbot, G, X1, "resume"), ALICE, message_id=804)
    assert pbot.registry.card_owners == {}
    _run_tap(pbot, run_async, _cb(pbot, G, X1, "resume-ask"), BOB, message_id=804)
    pbot._do_resume.assert_awaited_once()
