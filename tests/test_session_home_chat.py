"""Roadmap 8.82 + 8.72 + 8.76: every session belongs to one chat.

Group audit B3 / A2 (probes ``b_default_chat_probe`` case G and
``p3_typed_internal_name``):

- a session the daemon learns of with no chat stamped (a terminal
  ``aipager session x`` is ``claude-x``) went to ``config.CHAT_ID``,
  which preferred the GROUP on a DM + group install, and a restart's
  load backfill made it a group session for good. Its home is now the
  owner's DM (``scope.home_scope``), and it is stamped at discovery;
- a group member could type another chat's internal session name
  (``/api__d555 ...``) and drive, switch to or send a file to that
  session. A typed name that belongs to another chat is now unknown.

DM parity: the operator's own install (one DM scope) and personal mode
see exactly what they saw before.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import state as state_mod
from aipager.bot import session_parity
from aipager.bot.transport import home_chat, resolve_chat_id
from aipager.dtach import inject
from aipager.policy import load_policy
from aipager.scope import Member, Scope, chat_from_suffix, home_scope
from aipager.state import SessionRegistry, Status, TrackedSession

GROUP = -1001
DM = 555           # the owner's (aly's) DM
OTHER_DM = 777     # carl's DM (role user)
ALY, BOB, CARL = 1, 2, 7


def _group(chat=GROUP):
    return Scope(chat_id=chat, kind="group", label="team", members=(
        Member(id=ALY, label="aly", role="owner"),
        Member(id=BOB, label="bob", role="user"),
    ))


def _dm(chat=DM, uid=ALY, role="owner"):
    return Scope(chat_id=chat, kind="dm", label=f"dm {chat}",
                 members=(Member(id=uid, label=f"u{uid}", role=role),))


# ---- home_scope: the pure rule -----------------------------------------

POLICY = load_policy()


@pytest.mark.parametrize("scopes, expected", [
    pytest.param([], None, id="no scopes"),
    pytest.param([_dm()], DM, id="one DM"),
    pytest.param([_group()], GROUP, id="one group"),
    pytest.param([_dm(), _group()], DM, id="DM then group"),
    pytest.param([_group(), _dm()], DM, id="group listed first, owner DM wins"),
    pytest.param([_dm(OTHER_DM, CARL, "user"), _dm()], DM,
                 id="two DMs, owner's listed second"),
    pytest.param([_dm(), _dm(OTHER_DM, CARL, "user")], DM,
                 id="two DMs, owner's listed first"),
    pytest.param([_dm(), _dm(OTHER_DM, CARL, "owner")], DM,
                 id="two owner DMs, first in yaml order"),
    pytest.param([_group(), _group(-2002)], GROUP, id="group-only, first"),
    pytest.param([_group(), _dm(OTHER_DM, CARL, "user"), _dm(888, 8, "user")],
                 OTHER_DM, id="no owner DM, first DM"),
    pytest.param([_dm(OTHER_DM, CARL, "user")], OTHER_DM,
                 id="lone scope with no owner"),
])
def test_home_scope_table(scopes, expected):
    got = home_scope(scopes, POLICY)
    assert (got.chat_id if got else None) == expected


def test_home_scope_without_a_policy_skips_the_owner_rule():
    # No policy: nobody can be shown to be the owner; the first DM wins.
    assert home_scope([_group(), _dm(OTHER_DM, CARL, "user"), _dm()],
                      None).chat_id == OTHER_DM


@pytest.mark.parametrize("name, expected", [
    ("claude-api__d555", (555, "dm")),
    ("claude-y__g1001", (-1001, "group")),
    ("claude-my__thing", None),
    ("claude-my__dabc", None),
    ("claude-x", None),
    ("claude-a__thing__g42", (-42, "group")),
    ("claude-z__d0", None),
    ("claude-z__g0", None),
])
def test_chat_from_suffix(name, expected):
    assert chat_from_suffix(name) == expected


# ---- discovery stamps the chat; resolution falls back to home ----------

def _scope_bot(mk_bot, scopes):
    bot = mk_bot(scopes=scopes)
    bot.policy = load_policy()
    return bot


def test_terminal_session_on_dm_and_group_is_the_owner_dms(mk_bot):
    """Probe b case G: a terminal `claude-x` went to the group."""
    bot = _scope_bot(mk_bot, [_group(), _dm()])
    t = bot.registry.get_or_create("claude-x")
    assert (t.scope_chat_id, t.scope_kind) == (DM, "dm")
    assert resolve_chat_id(t) == DM


def test_suffixed_names_are_stamped_with_exactly_that_chat(mk_bot):
    bot = _scope_bot(mk_bot, [_group(), _dm()])
    g = bot.registry.get_or_create("claude-y__g1001")
    d = bot.registry.get_or_create("claude-z__d777")
    assert (g.scope_chat_id, g.scope_kind) == (GROUP, "group")
    assert (d.scope_chat_id, d.scope_kind) == (OTHER_DM, "dm")


def test_group_only_install_still_uses_the_group(mk_bot):
    bot = _scope_bot(mk_bot, [_group()])
    t = bot.registry.get_or_create("claude-x")
    assert (t.scope_chat_id, t.scope_kind) == (GROUP, "group")


def test_two_dms_use_the_owners(mk_bot):
    bot = _scope_bot(mk_bot, [_dm(OTHER_DM, CARL, "user"), _dm()])
    assert bot.registry.get_or_create("claude-x").scope_chat_id == DM


def test_unstamped_session_resolves_to_home_from_the_live_scopes(mk_bot):
    """resolve_chat_id follows a reload. The pinned bar shows a session
    with no chat stamped in no chat (roadmap 8.72)."""
    bot = _scope_bot(mk_bot, [_group(), _dm()])
    s = TrackedSession(name="claude-old", label="old")   # never stamped
    assert resolve_chat_id(s) == DM
    assert bot._pinned_chat_of(s) is None
    assert home_chat() == (DM, "dm")
    bot.scopes = [_group()]           # what reload_team does in place
    assert resolve_chat_id(s) == GROUP
    assert bot._pinned_chat_of(s) is None


def test_monitor_socket_scan_stamps_a_discovered_session(mk_bot, monkeypatch, run_async):
    from aipager.session_monitor import SessionMonitor
    bot = _scope_bot(mk_bot, [_group(), _dm()])
    monkeypatch.setattr(inject, "list_sessions",
                        AsyncMock(return_value=["claude-x"]))
    mon = SessionMonitor(bot.registry, AsyncMock())
    run_async(mon._scan())
    t = bot.registry.get("claude-x")
    assert t is not None and (t.scope_chat_id, t.scope_kind) == (DM, "dm")


def _save_unstamped(path, *names):
    path.write_text(json.dumps({"sessions": {
        n: {"name": n, "label": n.removeprefix("claude-").split("__")[0],
            "transcript_path": ""} for n in names}}))


def test_load_backfill_uses_suffix_then_home(tmp_state_file, monkeypatch):
    """Probe b case G after a restart: the backfill went to the group."""
    from aipager import config
    monkeypatch.setattr(config, "SCOPES", [_group(), _dm()])
    monkeypatch.setattr(config, "POLICY", load_policy())
    _save_unstamped(tmp_state_file, "claude-x", "claude-y__g1001",
                    "claude-z__d777")
    r = SessionRegistry()
    r.load()
    assert (r.get("claude-x").scope_chat_id, r.get("claude-x").scope_kind) \
        == (DM, "dm")
    assert (r.get("claude-y__g1001").scope_chat_id,
            r.get("claude-y__g1001").scope_kind) == (GROUP, "group")
    assert r.get("claude-z__d777").scope_chat_id == OTHER_DM


def test_load_backfill_by_suffix_even_without_config(tmp_state_file, monkeypatch):
    from aipager import config
    monkeypatch.setattr(config, "CHAT_ID", "")
    _save_unstamped(tmp_state_file, "claude-y__g1001", "claude-x")
    r = SessionRegistry()
    r.load()
    assert r.get("claude-y__g1001").scope_chat_id == GROUP
    assert r.get("claude-x").scope_chat_id == 0   # nothing to say, no crash


def test_terminal_session_stays_the_dms_across_save_and_load(
        tmp_state_file, monkeypatch, mk_bot):
    from aipager import config
    bot = _scope_bot(mk_bot, [_group(), _dm()])
    bot.registry.get_or_create("claude-x")
    bot.registry.save()
    state_mod.set_live_scope_source(None)       # a fresh daemon start
    monkeypatch.setattr(config, "SCOPES", [_group(), _dm()])
    monkeypatch.setattr(config, "POLICY", load_policy())
    r = SessionRegistry()
    r.load()
    assert (r.get("claude-x").scope_chat_id, r.get("claude-x").scope_kind) \
        == (DM, "dm")


# ---- DM parity: the operator's one-DM install and personal mode --------

def test_dm_parity_one_dm_install(tmp_state_file, monkeypatch, mk_bot):
    """The operator's install: one DM scope. A terminal session goes to the
    DM (as CHAT_ID did), labels and targets are as before, and a restart
    leaves every existing session exactly as it was."""
    from aipager import config
    bot = _scope_bot(mk_bot, [_dm()])
    t = bot.registry.get_or_create("claude-x")
    s = bot.registry.get_or_create("claude-api__d555")
    assert (t.label, s.label) == ("x", "api")
    assert resolve_chat_id(t) == DM and resolve_chat_id(s) == DM
    bot.registry.last_active_session = "claude-x"
    assert bot.registry.target_for(DM) is t
    assert bot.registry.find_by_label("x", DM) is t
    before = {n: (x.scope_chat_id, x.scope_kind, x.label)
              for n, x in bot.registry.all_sessions().items()}
    bot.registry.save()
    state_mod.set_live_scope_source(None)
    monkeypatch.setattr(config, "SCOPES", [_dm()])
    r = SessionRegistry()
    r.load()
    after = {n: (x.scope_chat_id, x.scope_kind, x.label)
             for n, x in r.all_sessions().items()}
    assert after == before


def test_dm_parity_personal_mode_leaves_new_sessions_unstamped(mk_bot):
    bot = mk_bot()                            # scopes=None: personal mode
    t = bot.registry.get_or_create("claude-x")
    assert t.scope_chat_id == 0
    assert resolve_chat_id(t) == "256113222"  # config.CHAT_ID, as before


# ---- 8.76: a typed internal name from another chat is unknown ----------

@pytest.fixture
def typed(monkeypatch):
    out: list[tuple[str, str]] = []

    async def _send(name, text, *a, **kw):
        out.append((name, text))
        return True

    monkeypatch.setattr(inject, "send_text_and_enter", _send)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    return out


@pytest.fixture
def gbot(mk_bot):
    def _mk(scopes=None):
        bot = _scope_bot(mk_bot, scopes if scopes is not None
                         else [_group(), _dm(), _dm(OTHER_DM, CARL, "user")])
        bot._app.bot.id = 999
        for name in ("_card_for_injected", "_react", "_maybe_update_bot_name",
                     "_update_bot_commands", "_send_busy_and_animate"):
            setattr(bot, name, AsyncMock())
        return bot
    return _mk


def _update(text, *, user_id, chat_id, message_id=400, caption=None):
    u = MagicMock()
    u.effective_chat = MagicMock(id=chat_id,
                                 type="private" if chat_id > 0 else "supergroup")
    u.effective_user = MagicMock(id=user_id, username=f"u{user_id}",
                                 first_name="U", last_name="")
    m = MagicMock()
    m.text = text
    m.caption = caption
    m.message_id = message_id
    m.reply_to_message = None
    m.quote = None
    m.external_reply = None
    m.forward_origin = None
    m.via_bot = None
    m.media_group_id = None
    m.photo = None
    m.chat = u.effective_chat
    m.reply_text = AsyncMock(return_value=MagicMock(message_id=901))
    u.message = m
    u.effective_message = m
    u.callback_query = None
    return u


def _dm_session(bot, name="claude-api__d555", chat=DM, kind="dm"):
    s = bot.registry.get_or_create(name)
    s.label = "api"
    s.scope_chat_id, s.scope_kind = chat, kind
    s.status = Status.IDLE
    s.last_driver_user_id = ALY
    return s


def _replies(u):
    return [c.args[0] for c in u.message.reply_text.await_args_list]


def _snapshot(bot):
    reg = bot.registry
    return (sorted(reg.all_sessions()), reg.last_active_session,
            dict(reg._targets),
            {k: list(v) for k, v in session_parity._pref_index_map(bot).items()})


def test_p3_direct_send_from_the_group_is_refused(gbot, typed, run_async):
    bot = gbot()
    s = _dm_session(bot)
    before = _snapshot(bot)
    u = _update("/api__d555 read ~/private/notes.md", user_id=BOB, chat_id=GROUP)
    run_async(bot._handle_message(u, None))
    assert typed == []
    assert s.status is Status.IDLE
    assert _replies(u) == ["⚠️ Unknown session: api__d555"]
    assert _snapshot(bot) == before


def test_p3_direct_send_refused_before_the_admin_check(gbot, typed, run_async):
    """The refusal comes first: "needs an admin" would confirm it runs."""
    bot = gbot()
    _dm_session(bot)
    u = _update("/api__d555 /secretcmd", user_id=BOB, chat_id=GROUP)
    run_async(bot._handle_message(u, None))
    assert typed == []
    assert _replies(u) == ["⚠️ Unknown session: api__d555"]


def test_p3_bare_switch_from_the_group_is_refused(gbot, typed, run_async):
    bot = gbot()
    _dm_session(bot)
    before = _snapshot(bot)
    u = _update("/api__d555", user_id=BOB, chat_id=GROUP)
    run_async(bot._handle_message(u, None))
    assert _replies(u) == ["⚠️ Unknown session: api__d555"]
    assert _snapshot(bot) == before
    assert not session_parity._pref_index_map(bot).get(GROUP)
    assert bot.registry.target_for(GROUP) is None


def test_p3_file_caption_from_the_group_is_refused(gbot, typed, tmp_path, run_async):
    bot = gbot()
    _dm_session(bot)
    before = _snapshot(bot)
    f = tmp_path / "x.txt"
    f.write_text("x")
    u = _update(None, user_id=BOB, chat_id=GROUP,
                caption="/api__d555 summarise")
    run_async(bot._inject_file_prompt(u, MagicMock(), "/api__d555 summarise", [f],
                                 all_photos=False, log_name="x.txt"))
    assert typed == []
    assert _replies(u) == ["⚠️ Unknown session: api__d555"]
    assert _snapshot(bot) == before


def test_unknown_suffixed_socket_of_another_chat_is_not_adopted(gbot, typed, run_async):
    """A live socket the registry has never seen, named for the owner's
    DM, typed in the group: refused by its suffix, nothing registered."""
    bot = gbot()
    u = _update("/api__d555 hi", user_id=BOB, chat_id=GROUP)
    run_async(bot._handle_message(u, None))
    assert typed == []
    assert bot.registry.get("claude-api__d555") is None
    assert _replies(u) == ["⚠️ Unknown session: api__d555"]


def test_another_dms_suffix_typed_in_a_dm_is_refused(gbot, typed, run_async):
    bot = gbot()
    _dm_session(bot)
    u = _update("/api__d555 hi", user_id=CARL, chat_id=OTHER_DM)
    run_async(bot._handle_message(u, None))
    assert typed == []
    assert _replies(u) == ["⚠️ Unknown session: api__d555"]


def test_known_session_stamped_with_another_chat_is_refused(gbot, typed, run_async):
    """No suffix to go by (a renamed or pre-suffix name): its stamp says
    it is the group's, so the owner's DM cannot reach it by name either."""
    bot = gbot()
    s = _dm_session(bot, name="claude-legacy", chat=GROUP, kind="group")
    s.label = "renamed"
    u = _update("/legacy hi", user_id=ALY, chat_id=DM)
    run_async(bot._handle_message(u, None))
    assert typed == []
    assert _replies(u) == ["⚠️ Unknown session: legacy"]
    assert s.label == "renamed"


def test_unscoped_socket_is_unknown_outside_its_home_chat(gbot, typed, run_async):
    """A terminal `claude-scratch` belongs to the owner's DM: typing it in
    the group adopts nothing; typing it in the DM adopts it, stamped."""
    bot = gbot()
    u = _update("/scratch hi", user_id=BOB, chat_id=GROUP)
    run_async(bot._handle_message(u, None))
    assert typed == []
    assert bot.registry.get("claude-scratch") is None
    assert _replies(u) == ["⚠️ Unknown session: scratch"]

    u = _update("/scratch hi", user_id=ALY, chat_id=DM)
    run_async(bot._handle_message(u, None))
    assert [n for n, _ in typed] == ["claude-scratch"]
    s = bot.registry.get("claude-scratch")
    assert (s.scope_chat_id, s.scope_kind, s.label) == (DM, "dm", "scratch")


def test_unstamped_known_session_is_unknown_outside_its_home_chat(gbot, typed):
    bot = gbot()
    s = TrackedSession(name="claude-old", label="old", status=Status.IDLE)
    bot.registry._sessions["claude-old"] = s          # scope_chat_id 0
    assert bot._typed_name_foreign("claude-old", GROUP) is True
    assert bot._typed_name_foreign("claude-old", DM) is False
    assert bot._adopt_by_typed_name("claude-old", "old", GROUP) is None


def test_the_same_name_works_from_its_own_chat(gbot, typed, tmp_path, run_async):
    bot = gbot()
    s = _dm_session(bot)
    u = _update("/api__d555 hello", user_id=ALY, chat_id=DM)
    run_async(bot._handle_message(u, None))
    assert typed and typed[-1][0] == "claude-api__d555"
    assert "hello" in typed[-1][1]

    s.status = Status.IDLE
    u = _update("/api__d555", user_id=ALY, chat_id=DM, message_id=401)
    run_async(bot._handle_message(u, None))
    assert "Unknown session" not in " ".join(map(str, _replies(u)))
    assert bot.registry.target_for(DM) is s

    s.status = Status.IDLE
    assert run_async(bot._session_for_typed_label(
        _update(None, user_id=ALY, chat_id=DM), "api__d555")) is s


def test_group_session_name_works_in_the_group(gbot, typed, run_async):
    bot = gbot()
    _dm_session(bot, name="claude-api__g1001", chat=GROUP, kind="group")
    u = _update("/api__g1001 hello", user_id=BOB, chat_id=GROUP)
    run_async(bot._handle_message(u, None))
    assert typed and typed[-1][0] == "claude-api__g1001"
    # A live socket named for this group that aipager has not seen yet
    # is adopted here too, stamped with the group.
    u = _update("/new1__g1001 hi", user_id=BOB, chat_id=GROUP, message_id=401)
    run_async(bot._handle_message(u, None))
    assert typed[-1][0] == "claude-new1__g1001"
    s = bot.registry.get("claude-new1__g1001")
    assert (s.scope_chat_id, s.scope_kind, s.label) == (GROUP, "group", "new1__g1001")


def test_dm_parity_personal_mode_still_adopts_a_typed_name(mk_bot, typed, run_async):
    bot = mk_bot()
    bot._app.bot.id = 999
    for name in ("_card_for_injected", "_react", "_maybe_update_bot_name",
                 "_update_bot_commands", "_send_busy_and_animate"):
        setattr(bot, name, AsyncMock())
    u = _update("/scratch hi", user_id=12345, chat_id=256113222)
    run_async(bot._handle_message(u, None))
    assert [n for n, _ in typed] == ["claude-scratch"]


def test_no_calling_chat_or_no_scopes_is_refused_in_scope_mode(gbot):
    """Fail closed: an update with no chat cannot claim any session, and
    a scope-mode bot whose scopes were emptied has no home chat."""
    bot = gbot()
    _dm_session(bot)
    assert bot._typed_name_foreign("claude-api__d555", None) is True
    assert bot._typed_name_foreign("claude-scratch", None) is True
    bot.scopes = []
    assert bot._typed_name_foreign("claude-scratch", DM) is True
