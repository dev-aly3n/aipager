"""Roadmap 8.72: a session with no chat stamped belongs to no chat.

``scope_chat_id == 0`` used to match ANY chat in the registry's lookups
(``all_sessions``, ``find_by_label``, ``live_labels``, the targets), the
per-session "isn't running here" checks and the tap gate, while the
pinned bar and the session's messages went to one chat. On a DM + group
install an unstamped session was therefore every chat's session and
target: the group could list it, make it its target, tap its buttons and
message it. Delivery 5 stamps every session at discovery and at load, so
in scope mode an unstamped session is now a missed stamp: it is no
chat's until stamped (a WARNING once), its output goes to the chat it
would be stamped with (suffix, else the home chat), and anything that
touches it stamps it.

Personal mode and legacy team mode have one chat, where "any chat" and
"the one chat" are the same thing: unchanged, pinned below. So is the
operator's own install (one DM scope), where every session is stamped
with the DM at load.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import state as state_mod
from aipager.bot import dashboard, session_parity, tap_gate, update_flow
from aipager.bot.transport import resolve_chat_id
from aipager.dtach import inject
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import (
    SessionRegistry,
    Status,
    TrackedSession,
    session_foreign_to,
    session_in_chat,
)
from aipager.team import Role as TeamRole, Rules, Team, User as TeamUser

GROUP = -1001
DM = 555
OTHER = 999                     # a chat that is no scope
CHAT = 256113222                # conftest's pinned single chat
TEAM_CHAT = -100                # a legacy team.yaml group
ALY, BOB = 1, 2                 # owner, user
OLD = "claude-old"              # the session that missed its stamp


def _group():
    return Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=ALY, label="aly", role="owner"),
        Member(id=BOB, label="bob", role="user"),
    ))


def _dm():
    return Scope(chat_id=DM, kind="dm", label="aly DM",
                 members=(Member(id=ALY, label="aly", role="owner"),))


def _team():
    return Team(group_id=TEAM_CHAT, users={
        ALY: TeamUser(id=ALY, label="aly", role=TeamRole.ADMIN),
        BOB: TeamUser(id=BOB, label="bob", role=TeamRole.DEVELOPER),
    }, rules=Rules(deny_tools=[]))


@pytest.fixture
def mkbot(mk_bot, monkeypatch):
    """``mkbot("scope")`` DM + group, ``"dm"`` the operator's one DM,
    ``"personal"``, ``"team"`` (legacy team.yaml, its group the chat)."""
    def _mk(mode="scope"):
        if mode == "scope":
            bot = mk_bot(scopes=[_group(), _dm()])
        elif mode == "dm":
            bot = mk_bot(scopes=[_dm()])
        elif mode == "personal":
            bot = mk_bot()
        else:
            monkeypatch.setattr("aipager.config.CHAT_ID", str(TEAM_CHAT))
            monkeypatch.setattr(dashboard, "CHAT_ID", str(TEAM_CHAT))
            bot = mk_bot(team=_team())
        if mode == "personal":
            monkeypatch.setattr(dashboard, "CHAT_ID", str(CHAT))
        bot.policy = load_policy(Path("/nonexistent/policy.yaml"),
                                 Path("/nonexistent/policy.d"))
        bot._app.bot.id = 4242
        for name in ("_card_for_injected", "_react", "_maybe_update_bot_name",
                     "_update_bot_commands", "_edit_busy_raw",
                     "_send_busy_and_animate"):
            setattr(bot, name, AsyncMock())
        return bot
    return _mk


def _missed(bot, name=OLD, label="old", status=Status.IDLE):
    """A session some path learned of without stamping it (bypasses
    ``get_or_create``, which stamps)."""
    s = TrackedSession(name=name, label=label)
    s.status = status
    s.claude_session_id = f"sid-{label}"
    bot.registry._sessions[name] = s
    return s


def _stamped(bot, name, label, chat, kind, status=Status.IDLE):
    s = bot.registry.get_or_create(name)
    s.label = label
    s.scope_chat_id, s.scope_kind = chat, kind
    s.status = status
    s.claude_session_id = f"sid-{label}"
    return s


def _scope_world(bot):
    """The missed session (live and an ended one) beside one stamped
    session per chat."""
    old = _missed(bot)
    gone = _missed(bot, "claude-oldg", "oldg", Status.GONE)
    api = _stamped(bot, "claude-api__d555", "api", DM, "dm")
    team = _stamped(bot, "claude-team__g1001", "team", GROUP, "group")
    return old, gone, api, team


def _unstamped_warnings(caplog, name=OLD):
    return [r for r in caplog.records
            if r.levelno == logging.WARNING
            and f"session {name} has no chat stamped" in r.getMessage()]


# ---- scope mode: an unstamped session is no chat's ---------------------

@pytest.mark.parametrize("chat", [DM, GROUP])
def test_scope_mode_unstamped_is_in_no_chats_lookups(mkbot, chat):
    bot = mkbot()
    old, gone, api, team = _scope_world(bot)
    reg = bot.registry
    own = {DM: api, GROUP: team}[chat]
    assert set(reg.all_sessions(chat)) == {own.name}
    assert reg.find_by_label("old", chat) is None
    assert reg.find_by_label("old", chat, include_gone=True) is None
    assert reg.find_by_label("oldg", chat, include_gone=True) is None
    assert reg.live_labels(chat) == {own.label}
    assert not session_in_chat(old, chat)
    assert session_foreign_to(old, chat)
    # No chat named: everything, as before (the monitor, saves).
    assert {OLD, "claude-oldg"} <= set(reg.all_sessions())
    assert session_foreign_to(old, None) is False


@pytest.mark.parametrize("chat", [DM, GROUP])
def test_scope_mode_unstamped_is_no_chats_target(mkbot, chat):
    bot = mkbot()
    old, *_ = _scope_world(bot)
    reg = bot.registry
    reg.last_active_session = OLD
    assert reg.target_for(chat) is None
    # A legacy unscoped update (no chat) still has the install-wide latest.
    assert reg.target_for(None) is old


def test_scope_mode_unstamped_is_no_members_target(mkbot):
    bot = mkbot()
    reg = bot.registry
    old = _missed(bot)                     # the group's only live session
    gone = _missed(bot, "claude-oldg", "oldg", Status.GONE)
    reg.set_target(OLD, GROUP, BOB)
    assert (GROUP, BOB) not in reg._user_targets
    assert reg.target_for(GROUP, BOB) is None
    # Even a recorded one (saved before) is not theirs.
    reg._user_targets[(GROUP, BOB)] = (OLD, 1)
    assert reg.target_for(GROUP, BOB) is None
    assert reg.own_target(GROUP, BOB) is None
    reg._user_targets[(GROUP, BOB)] = ("claude-oldg", 2)
    assert reg.ended_target_for(GROUP, BOB) is None
    assert reg.own_target(GROUP, BOB) is None
    assert old.status is Status.IDLE and gone.status is Status.GONE


@pytest.mark.parametrize("chat", [DM, GROUP])
def test_scope_mode_unstamped_is_in_no_status_or_picker(mkbot, chat):
    bot = mkbot()
    _scope_world(bot)
    text, _kb = bot._render_status_list(chat)
    own = {DM: "api", GROUP: "team"}[chat]
    assert own in text
    assert "old" not in text
    picker, _kb = bot._render_resume_picker(0, chat)
    assert "oldg" not in picker


@pytest.mark.parametrize("chat", [DM, GROUP])
def test_scope_mode_unstamped_is_in_no_pinned_bar(mkbot, chat):
    bot = mkbot()
    old, _gone, api, team = _scope_world(bot)
    assert bot._pinned_chat_of(old) is None
    assert [s.label for s in bot._pinned_sessions(chat)] == [
        {DM: "api", GROUP: "team"}[chat]]
    text, _kb = bot._render_pinned(chat)
    assert "old" not in text


@pytest.mark.parametrize("chat", [DM, GROUP])
def test_scope_mode_unstamped_is_in_no_miniapp_list(mkbot, chat):
    from aipager.miniapp.server import MiniAppServer
    bot = mkbot()
    _scope_world(bot)
    srv = MiniAppServer(bot, bot.registry, port=8765)
    labels = {s["label"] for s in srv._build_sessions_payload(chat)["sessions"]}
    assert labels == {{DM: "api", GROUP: "team"}[chat]}


def test_scope_mode_reply_to_an_unchatted_message_is_not_its(mkbot):
    """A message tracked with no chat (0, left from personal mode)."""
    bot = mkbot()
    old, _gone, api, _team_s = _scope_world(bot)
    reg = bot.registry
    reg._msg_map[(0, 5)] = OLD
    reg._msg_map[(0, 6)] = api.name
    assert reg.get_session_by_msg(5, DM) is None
    assert reg.get_session_by_msg(5, GROUP) is None
    assert reg.get_session_by_msg(6, DM) is api
    assert reg.get_session_by_msg(6, GROUP) is None


# ---- taps ---------------------------------------------------------------

def _tap(chat_id, user_id, data, *, msg_id=50):
    q = MagicMock()
    q.data = data
    q.from_user = MagicMock(id=user_id)
    q.message = MagicMock()
    q.message.chat = MagicMock(id=chat_id)
    q.message.chat_id = chat_id
    q.message.message_id = msg_id
    q.message.text = "card"
    q.message.edit_text = AsyncMock()
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()
    u = MagicMock()
    u.callback_query = q
    u.effective_chat = q.message.chat
    u.effective_user = q.from_user
    u.message = None
    return u, q


def _answers(q):
    return [c.args[0] for c in q.answer.await_args_list if c.args]


@pytest.fixture
def reached(monkeypatch):
    seen: list = []

    async def _first(bot, update, query, session_name, action):
        seen.append((session_name, action))
        return True

    monkeypatch.setattr(update_flow, "handle_callback", _first)
    return seen


@pytest.mark.parametrize("chat,uid", [(DM, ALY), (GROUP, BOB), (GROUP, ALY)])
@pytest.mark.parametrize("verb", ["stop", "menu", "talk", "resume-ask"])
def test_scope_mode_tap_naming_an_unstamped_session_is_refused(
        mkbot, reached, run_async, chat, uid, verb):
    bot = mkbot()
    _missed(bot, status=Status.BUSY)
    u, q = _tap(chat, uid, f"{OLD}:{verb}")
    run_async(bot._handle_callback(u, MagicMock()))
    assert reached == []
    assert _answers(q) == [tap_gate.OTHER_CHAT_TEXT]


def test_scope_mode_tap_on_a_stamped_session_in_its_chat_passes(
        mkbot, reached, run_async):
    bot = mkbot()
    _stamped(bot, "claude-team__g1001", "team", GROUP, "group")
    u, q = _tap(GROUP, BOB, "claude-team__g1001:stop")
    run_async(bot._handle_callback(u, MagicMock()))
    assert reached == [("claude-team__g1001", "stop")]


NOT_HERE = "That session isn't running here."


@pytest.mark.parametrize("action", [
    "talk", "endok1", "mode_show", "modeauto1", "restartok1"])
@pytest.mark.parametrize("chat,uid", [(DM, ALY), (GROUP, BOB)])
def test_scope_mode_session_actions_refuse_an_unstamped_session(
        mkbot, run_async, action, chat, uid):
    """Each per-session "isn't running here" check, past the gate."""
    bot = mkbot()
    old = _missed(bot)
    u, q = _tap(chat, uid, f"{OLD}:{action}")
    assert run_async(session_parity.handle_callback(bot, u, q, OLD, action))
    assert _answers(q) == [NOT_HERE]
    assert bot.registry.target_for(chat) is None
    assert old.status is Status.IDLE


@pytest.mark.parametrize("chat,uid", [(DM, ALY), (GROUP, BOB)])
def test_scope_mode_stop_button_refuses_an_unstamped_session(
        mkbot, run_async, chat, uid):
    bot = mkbot()
    _missed(bot, status=Status.BUSY)
    bot._stop_session_core = AsyncMock()
    u, q = _tap(chat, uid, f"{OLD}:ststop1")
    looked, outcome = run_async(bot._stop_the_turn_shown(
        u, q, OLD, 1, again="again"))
    assert (looked, outcome) == (False, None)
    assert _answers(q) == [NOT_HERE]
    bot._stop_session_core.assert_not_called()


@pytest.mark.parametrize("chat,uid", [(DM, ALY), (GROUP, BOB)])
def test_scope_mode_send_now_refuses_an_unstamped_session(
        mkbot, run_async, chat, uid):
    from aipager.bot.send_now import TOAST_UNAVAILABLE
    bot = mkbot()
    _missed(bot, status=Status.BUSY)
    bot._send_now_core = AsyncMock()
    u, q = _tap(chat, uid, f"{OLD}:now:7")
    run_async(bot._handle_send_now_tap(u, q, OLD, "7"))
    assert _answers(q) == [TOAST_UNAVAILABLE]
    bot._send_now_core.assert_not_called()


# ---- its output, and the one WARNING -----------------------------------

def test_scope_mode_output_goes_to_the_chat_it_would_be_stamped_with(mkbot):
    # Kept for the whole test: the live scopes are read through a WEAK
    # reference to the bot (state.set_live_scope_source), so a dropped bot
    # leaves at the next garbage collection and the home chat with it
    # (flaky on GitHub's 3.13, 2026-10-07).
    bot = mkbot()
    assert bot.scopes
    plain = TrackedSession(name="claude-x", label="x")
    grp = TrackedSession(name="claude-y__g1001", label="y")
    other = TrackedSession(name="claude-z__d777", label="z")
    assert resolve_chat_id(plain) == DM          # the home chat
    assert resolve_chat_id(grp) == GROUP         # its suffix, not home
    assert resolve_chat_id(other) == 777


def test_scope_mode_unstamped_warns_once_per_session(mkbot, caplog):
    bot = mkbot()
    old, gone, *_ = _scope_world(bot)
    caplog.set_level(logging.WARNING, logger="aipager.state")
    reg = bot.registry
    for chat in (DM, GROUP, DM):
        reg.all_sessions(chat)
        reg.find_by_label("old", chat)
        bot._pinned_chat_of(old)
        resolve_chat_id(old)
    assert len(_unstamped_warnings(caplog)) == 1
    assert len(_unstamped_warnings(caplog, "claude-oldg")) == 1
    # A stamped session never warns.
    assert _unstamped_warnings(caplog, "claude-api__d555") == []


def test_scope_mode_the_warning_is_logged_by_each_way_in(mkbot, caplog):
    """The pinned bar, resolve_chat_id and the tap gate each warn on
    their own (one way in, one warning)."""
    bot = mkbot()
    caplog.set_level(logging.WARNING, logger="aipager.state")
    a = _missed(bot, "claude-a", "a")
    b = _missed(bot, "claude-b", "b")
    bot._pinned_chat_of(a)
    resolve_chat_id(b)
    assert len(_unstamped_warnings(caplog, "claude-a")) == 1
    assert len(_unstamped_warnings(caplog, "claude-b")) == 1


def test_scope_mode_tap_gate_warns(mkbot, reached, run_async, caplog):
    bot = mkbot()
    caplog.set_level(logging.WARNING, logger="aipager.state")
    _missed(bot, status=Status.BUSY)
    u, q = _tap(GROUP, BOB, f"{OLD}:stop")
    run_async(bot._handle_callback(u, MagicMock()))
    assert len(_unstamped_warnings(caplog)) == 1


# ---- stamped at discovery and at load, then in exactly that chat --------

@pytest.mark.parametrize("name,chat", [
    (OLD, DM), ("claude-old__g1001", GROUP), ("claude-old__d555", DM)])
def test_scope_mode_discovery_stamps_a_known_unstamped_session(
        mkbot, caplog, name, chat):
    bot = mkbot()
    caplog.set_level(logging.WARNING, logger="aipager.state")
    old = _missed(bot, name)
    assert bot.registry.get_or_create(name) is old
    assert old.scope_chat_id == chat
    assert old.scope_kind == ("group" if chat < 0 else "dm")
    other = GROUP if chat == DM else DM
    assert name in bot.registry.all_sessions(chat)
    assert name not in bot.registry.all_sessions(other)
    assert bot.registry.find_by_label("old", chat) is old
    assert bot.registry.find_by_label("old", other) is None
    assert bot._pinned_chat_of(old) == chat
    assert len(_unstamped_warnings(caplog, name)) == 1
    assert bot.registry._dirty


def test_scope_mode_discovery_stamps_only_the_session_touched(mkbot):
    bot = mkbot()
    first = _missed(bot, "claude-a", "a")
    second = _missed(bot, "claude-b__g1001", "b")
    bot.registry.get_or_create("claude-b__g1001")
    assert second.scope_chat_id == GROUP
    assert first.scope_chat_id == 0


def test_scope_mode_monitor_scan_stamps_a_known_unstamped_session(
        mkbot, monkeypatch, run_async):
    from aipager.session_monitor import SessionMonitor
    bot = mkbot()
    old = _missed(bot)
    monkeypatch.setattr(inject, "list_sessions",
                        AsyncMock(return_value=[OLD]))
    run_async(SessionMonitor(bot.registry, AsyncMock())._scan())
    assert (old.scope_chat_id, old.scope_kind) == (DM, "dm")
    assert set(bot.registry.all_sessions(DM)) == {OLD}
    assert bot.registry.all_sessions(GROUP) == {}


def test_scope_mode_typed_name_adoption_stamps_a_known_unstamped_session(mkbot):
    bot = mkbot()
    old = _missed(bot)
    assert bot._adopt_by_typed_name(OLD, "old", GROUP) is None
    assert old.scope_chat_id == 0                # refused: nothing touched
    assert bot._adopt_by_typed_name(OLD, "old", DM) is old
    assert old.scope_chat_id == DM


def test_scope_mode_load_stamps_then_exactly_that_chat(
        tmp_state_file, monkeypatch):
    from aipager import config
    monkeypatch.setattr(config, "SCOPES", [_group(), _dm()])
    monkeypatch.setattr(config, "POLICY", load_policy())
    tmp_state_file.write_text(json.dumps({"sessions": {
        n: {"name": n, "label": "old", "transcript_path": "",
            "scope_chat_id": 0}
        for n in (OLD, "claude-old__g1001")}}))
    r = SessionRegistry()
    r.load()
    assert set(r.all_sessions(DM)) == {OLD}
    assert set(r.all_sessions(GROUP)) == {"claude-old__g1001"}
    assert r.find_by_label("old", DM).name == OLD
    assert r.find_by_label("old", GROUP).name == "claude-old__g1001"


def test_scope_mode_stamp_unstamped(mkbot):
    bot = mkbot()
    old = _missed(bot)
    grp = _missed(bot, "claude-y__g1001", "y")
    bot.registry._dirty = False
    assert sorted(bot.registry.stamp_unstamped()) == sorted(
        [OLD, "claude-y__g1001"])
    assert (old.scope_chat_id, grp.scope_chat_id) == (DM, GROUP)
    assert bot.registry._dirty
    assert bot.registry.stamp_unstamped() == []


def test_reload_from_personal_to_scope_mode_stamps_every_session(
        mkbot, run_async):
    """The one path that left sessions unstamped in scope mode: a daemon
    started in personal mode, then `aipager config` adds scopes and
    signals it (SIGUSR1)."""
    bot = mkbot("personal")
    x = bot.registry.get_or_create("claude-x")
    assert x.scope_chat_id == 0
    bot.scopes = [_group(), _dm()]             # what reload_team swaps in
    bot.policy = load_policy()
    assert bot.registry.all_sessions(DM) == {}
    run_async(bot._follow_scope_reload(None, None))
    assert (x.scope_chat_id, x.scope_kind) == (DM, "dm")
    assert set(bot.registry.all_sessions(DM)) == {"claude-x"}
    assert bot.registry.all_sessions(GROUP) == {}


def test_scope_mode_every_creation_path_stamps(mkbot):
    """get_or_create (socket scan, hooks, adoption, /new) never leaves a
    session unstamped in scope mode."""
    bot = mkbot()
    for name in ("claude-a", "claude-b__g1001", "claude-c__d555", "x"):
        assert bot.registry.get_or_create(name).scope_chat_id != 0


# ---- personal mode and legacy team mode: unchanged ---------------------

@pytest.mark.parametrize("mode,chat", [("personal", CHAT), ("team", TEAM_CHAT)])
def test_one_chat_modes_keep_any_chat(mkbot, mode, chat, caplog, run_async):
    bot = mkbot(mode)
    caplog.set_level(logging.WARNING, logger="aipager.state")
    reg = bot.registry
    old = reg.get_or_create(OLD)
    old.status = Status.IDLE
    assert old.scope_chat_id == 0                 # left unstamped, as before
    assert reg.stamp_unstamped() == []
    for c in (chat, OTHER):
        assert OLD in reg.all_sessions(c)
        assert reg.find_by_label("old", c) is old
        assert "old" in reg.live_labels(c)
        assert session_in_chat(old, c)
        assert not session_foreign_to(old, c)
    reg.last_active_session = OLD
    assert reg.target_for(chat) is old
    assert reg.target_for(OTHER) is old
    assert reg.target_for(None) is old
    assert bot._pinned_chat_of(old) == chat
    assert [s.name for s in bot._pinned_sessions(chat)] == [OLD]
    assert resolve_chat_id(old) == str(chat)
    reg._msg_map[(0, 5)] = OLD
    assert reg.get_session_by_msg(5, chat) is old
    # A stamped session is its own chat's only, as before.
    api = _stamped(bot, "claude-api__d555", "api", DM, "dm")
    assert session_foreign_to(api, chat) and not session_in_chat(api, chat)
    reg._msg_map[(0, 6)] = api.name
    assert reg.get_session_by_msg(6, chat) is api   # the wildcard, as before
    # The tap gate is scope-mode only.
    u, q = _tap(chat, ALY, f"{OLD}:stop")
    assert run_async(bot._tap_passes_gate(q, None, OLD, "stop")) is True
    # "talk" passes the per-session check.
    u, q = _tap(chat, ALY, f"{OLD}:talk")
    run_async(session_parity.handle_callback(bot, u, q, OLD, "talk"))
    assert NOT_HERE not in _answers(q)
    assert _unstamped_warnings(caplog) == []


def test_personal_mode_suffixed_new_session_is_stamped_as_before(mkbot):
    bot = mkbot("personal")
    assert bot.registry.get_or_create("claude-y__g1001").scope_chat_id == GROUP


def test_personal_mode_known_unstamped_session_is_not_stamped(mkbot, caplog):
    bot = mkbot("personal")
    caplog.set_level(logging.WARNING, logger="aipager.state")
    old = _missed(bot, "claude-y__g1001", "y")
    bot.registry.get_or_create("claude-y__g1001")
    assert old.scope_chat_id == 0
    assert bot.registry.stamp_unstamped() == []
    assert old.scope_chat_id == 0
    assert _unstamped_warnings(caplog, "claude-y__g1001") == []


# ---- the operator's install: one DM scope ------------------------------

def test_one_dm_install_load_stamps_a_zero_session_and_nothing_changes(
        tmp_state_file, monkeypatch, mkbot, caplog, run_async):
    from aipager import config
    from aipager.miniapp.server import MiniAppServer
    monkeypatch.setattr(config, "SCOPES", [_dm()])
    monkeypatch.setattr(config, "POLICY", load_policy())
    state_mod.set_live_scope_source(None)
    tmp_state_file.write_text(json.dumps({
        "sessions": {
            OLD: {"name": OLD, "label": "old", "transcript_path": "",
                  "scope_chat_id": 0},
            "claude-api__d555": {"name": "claude-api__d555", "label": "api",
                                 "transcript_path": "",
                                 "scope_chat_id": DM, "scope_kind": "dm"},
        },
        "last_active_session": OLD,
    }))
    r = SessionRegistry()
    r.load()
    caplog.set_level(logging.WARNING, logger="aipager.state")
    bot = mkbot("dm")
    bot.registry = r
    old, api = r.get(OLD), r.get("claude-api__d555")
    assert (old.scope_chat_id, old.scope_kind) == (DM, "dm")
    for s in (old, api):
        s.status = Status.IDLE
    assert set(r.all_sessions(DM)) == {OLD, "claude-api__d555"}
    assert r.find_by_label("old", DM) is old
    assert r.find_by_label("api", DM) is api
    assert r.live_labels(DM) == {"old", "api"}
    assert r.target_for(DM) is old
    assert resolve_chat_id(old) == DM == resolve_chat_id(api)
    assert [s.label for s in bot._pinned_sessions(DM)] == ["api", "old"]
    srv = MiniAppServer(bot, r, port=8765)
    assert {s["label"] for s in srv._build_sessions_payload(DM)["sessions"]} \
        == {"old", "api"}
    u, q = _tap(DM, ALY, f"{OLD}:talk")
    run_async(session_parity.handle_callback(bot, u, q, OLD, "talk"))
    assert NOT_HERE not in _answers(q)
    assert r.target_for(DM) is old
    assert _unstamped_warnings(caplog) == []
