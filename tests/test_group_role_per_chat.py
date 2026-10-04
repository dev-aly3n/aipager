"""Roadmap 8.73 (D-A): a person's role comes from the chat they act in,
never from the first chat in aipager.yaml that lists them.

CARA is a ``user`` in the group and the ``owner`` of her own DM; ADA is an
``admin`` in the group and a ``user`` in her own DM. Every case runs with
the scopes in both yaml orders: the old lookup took the first scope that
listed the person, so one of the two orders always got the wrong role.

DM parity: the operator's install (one DM scope, an owner) and personal
and legacy team mode resolve exactly as before.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import policy_snapshot as ps
from aipager.bot import live_reload
from aipager.bot.transport import NEEDS_ADMIN_REPLY
from aipager.dtach import inject
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import TURN_SENDER_TERMINAL, Status
from aipager.team import Role, Rules, Team, User as TeamUser

GROUP = -1001
CARA_DM = 556
ADA_DM = 557
ALY_DM = 555
G_SESSION = "claude-api__g1001"
CARA_SESSION = "claude-notes__d556"
ADA_SESSION = "claude-pad__d557"

ALY, BOB, CARA, ADA = 1, 2, 5, 4

POLICY = load_policy()


def _group():
    return Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=ALY, label="aly", role="owner"),
        Member(id=BOB, label="bob", role="user"),
        Member(id=CARA, label="cara", role="user"),
        Member(id=ADA, label="ada", role="admin"),
    ))


def _cara_dm():
    return Scope(chat_id=CARA_DM, kind="dm", label="cara DM",
                 members=(Member(id=CARA, label="cara", role="owner"),))


def _ada_dm():
    return Scope(chat_id=ADA_DM, kind="dm", label="ada DM",
                 members=(Member(id=ADA, label="ada", role="user"),))


def _aly_dm():
    return Scope(chat_id=ALY_DM, kind="dm", label="aly DM",
                 members=(Member(id=ALY, label="aly", role="owner"),))


# The DMs first (the old bug: group prompts ran as the DM role) and the
# group first (the old bug the other way: DM prompts ran as the group role).
ORDERS = {
    "dms_first": lambda: [_cara_dm(), _ada_dm(), _group()],
    "group_first": lambda: [_group(), _cara_dm(), _ada_dm()],
}


@pytest.fixture(params=sorted(ORDERS))
def order(request):
    return request.param


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
    def _mk(scopes):
        bot = mk_bot(scopes=scopes)
        bot.policy = POLICY
        bot._app.bot.id = 999
        for name in ("_card_for_injected", "_react", "_maybe_update_bot_name",
                     "_update_bot_commands", "_send_busy_and_animate"):
            setattr(bot, name, AsyncMock())
        return bot
    return _mk


def _session(bot, name, chat, kind, *, driver=None, status=Status.IDLE,
             turn=None):
    s = bot.registry.get_or_create(name)
    s.label = name.removeprefix("claude-").split("__")[0]
    s.scope_chat_id, s.scope_kind = chat, kind
    s.status = status
    s.turn_sender_id = turn
    s.last_driver_user_id = driver
    bot.registry.last_active_session = name
    return s


def _update(text, *, user_id, chat_id, message_id=400):
    u = MagicMock()
    u.effective_chat = MagicMock(id=chat_id,
                                 type="private" if chat_id > 0 else "supergroup")
    u.effective_user = MagicMock(id=user_id, username=f"u{user_id}",
                                 first_name="U", last_name="")
    m = MagicMock()
    m.text = text
    m.caption = None
    m.message_id = message_id
    m.reply_to_message = None
    m.quote = None
    m.external_reply = None
    m.forward_origin = None
    m.via_bot = None
    m.media_group_id = None
    m.chat = u.effective_chat
    m.reply_text = AsyncMock(return_value=MagicMock(message_id=901))
    u.message = m
    u.effective_message = m
    u.callback_query = None
    return u


def _ctx():
    return MagicMock(bot=MagicMock(id=999))


def _replies(update) -> list[str]:
    return [c.args[0] if c.args else c.kwargs.get("text")
            for c in update.message.reply_text.await_args_list]


def _note(name):
    notes = ps.list_outstanding_notes(name)
    assert len(notes) == 1, notes
    return notes[0]


# ---- a prompt: marker and note ---------------------------------------------

def test_a_group_prompt_runs_with_the_group_role_not_the_dm_owner(
        gbot, typed, run_async, order):
    bot = gbot(ORDERS[order]())
    _session(bot, G_SESSION, GROUP, "group", driver=ALY)
    run_async(bot._handle_message(
        _update("run id with Bash", user_id=CARA, chat_id=GROUP), _ctx()))
    assert typed == [(G_SESSION,
                      "[via Telegram · @cara · role:user]\nrun id with Bash")]
    note = _note(G_SESSION)
    assert note["bypass_safety"] is False
    assert note["confine_writes"] is True
    assert note["author_user_id"] == CARA


def test_a_dm_prompt_runs_with_the_dm_role(gbot, typed, run_async, order):
    bot = gbot(ORDERS[order]())
    _session(bot, CARA_SESSION, CARA_DM, "dm", driver=CARA)
    run_async(bot._handle_message(
        _update("tidy the notes", user_id=CARA, chat_id=CARA_DM), _ctx()))
    assert typed == [(CARA_SESSION, "[via Telegram · @cara]\ntidy the notes")]
    note = _note(CARA_SESSION)
    assert note["bypass_safety"] is True
    assert note["confine_writes"] is False


def test_a_group_admin_is_not_cut_down_by_a_lower_dm_role(
        gbot, typed, run_async, order):
    bot = gbot(ORDERS[order]())
    _session(bot, G_SESSION, GROUP, "group", driver=ALY)
    run_async(bot._handle_message(
        _update("refactor it", user_id=ADA, chat_id=GROUP), _ctx()))
    assert typed == [(G_SESSION,
                      "[via Telegram · @ada · role:admin]\nrefactor it")]
    assert _note(G_SESSION)["confine_writes"] is False


def test_inject_prompt_reads_the_role_in_the_messages_chat(
        gbot, typed, run_async, order):
    bot = gbot(ORDERS[order]())
    sess = _session(bot, G_SESSION, GROUP, "group")
    assert run_async(bot._inject_prompt(sess, "hi", chat_id=GROUP,
                                        driver_user_id=CARA))
    assert typed == [(G_SESSION, "[via Telegram · @cara · role:user]\nhi")]
    assert _note(G_SESSION)["bypass_safety"] is False
    assert sess.last_driver_user_id == CARA


def test_with_no_message_chat_the_sessions_own_chat_decides(
        gbot, typed, run_async, order):
    """Retry with no chat, a model switch from the Mini App: the session's
    own chat."""
    bot = gbot(ORDERS[order]())
    sess = _session(bot, G_SESSION, GROUP, "group")
    assert run_async(bot._inject_prompt(sess, "hi", driver_user_id=CARA))
    assert typed == [(G_SESSION, "[via Telegram · @cara · role:user]\nhi")]
    assert _note(G_SESSION)["bypass_safety"] is False

    dm = _session(bot, CARA_SESSION, CARA_DM, "dm")
    assert run_async(bot._inject_prompt(dm, "hi", driver_user_id=CARA))
    assert _note(CARA_SESSION)["bypass_safety"] is True


def test_an_unknown_chat_is_the_floor(gbot, typed, run_async, monkeypatch,
                                      order):
    """Neither the message's chat nor the session's chat resolves: the
    sender is nobody, so the bare marker and the floor."""
    bot = gbot(ORDERS[order]())
    sess = _session(bot, G_SESSION, GROUP, "group")
    sess.scope_chat_id = 0
    monkeypatch.setattr("aipager.bot.auth.resolve_chat_id_int",
                        lambda _s: None)
    assert run_async(bot._inject_prompt(sess, "hi", driver_user_id=CARA))
    assert typed == [(G_SESSION, "[via Telegram]\nhi")]
    note = _note(G_SESSION)
    assert note["bypass_safety"] is False
    assert note["confine_writes"] is True
    assert sess.last_driver_user_id is None


def test_a_chat_that_is_no_scope_is_the_floor(gbot, typed, run_async, order):
    bot = gbot(ORDERS[order]())
    sess = _session(bot, CARA_SESSION, CARA_DM, "dm")
    assert run_async(bot._inject_prompt(sess, "hi", chat_id=4242,
                                        driver_user_id=CARA))
    assert typed == [(CARA_SESSION, "[via Telegram]\nhi")]
    assert _note(CARA_SESSION)["bypass_safety"] is False


def test_a_drained_held_message_resolves_in_its_chat(
        gbot, typed, run_async, order):
    bot = gbot(ORDERS[order]())
    sess = _session(bot, G_SESSION, GROUP, "group")
    sess.queue_prompt("delete the build dir", 610, "", CARA)
    run_async(bot._drain_next_queued(sess))
    assert typed == [(G_SESSION, "[via Telegram · @cara · role:user]\n"
                                 "delete the build dir")]
    assert _note(G_SESSION)["bypass_safety"] is False


def test_retry_by_its_author_resolves_in_the_tapped_chat(
        gbot, typed, run_async, order):
    bot = gbot(ORDERS[order]())
    sess = _session(bot, G_SESSION, GROUP, "group", driver=ALY)
    sess.last_prompt = "again please"
    sess.last_prompt_driver_user_id = CARA
    query = MagicMock()
    query.data = f"{G_SESSION}:retry"
    query.answer = AsyncMock()
    query.message = MagicMock(message_id=42, text="❌ failed")
    query.message.chat = MagicMock(id=GROUP)
    query.message.chat_id = GROUP
    query.from_user = MagicMock(id=CARA)
    u = MagicMock(callback_query=query, effective_user=query.from_user,
                  effective_chat=query.message.chat, message=None)
    bot._app.bot.delete_message = AsyncMock()
    run_async(bot._handle_callback(u, MagicMock()))
    assert typed == [(G_SESSION,
                      "[via Telegram · @cara · role:user]\nagain please")]
    assert _note(G_SESSION)["bypass_safety"] is False


# ---- slash commands (D-G) --------------------------------------------------

def test_a_group_slash_command_needs_the_group_role(
        gbot, typed, run_async, order):
    bot = gbot(ORDERS[order]())
    _session(bot, G_SESSION, GROUP, "group", driver=ALY)
    u = _update("/api /deliver ship it", user_id=CARA, chat_id=GROUP)
    run_async(bot._handle_message(u, _ctx()))
    assert typed == []
    assert NEEDS_ADMIN_REPLY in _replies(u)


def test_a_dm_slash_command_uses_the_dm_role(gbot, typed, run_async, order):
    bot = gbot(ORDERS[order]())
    _session(bot, CARA_SESSION, CARA_DM, "dm", driver=CARA)
    u = _update("/notes /deliver ship it", user_id=CARA, chat_id=CARA_DM)
    run_async(bot._handle_message(u, _ctx()))
    assert typed == [(CARA_SESSION, "/deliver ship it")]
    assert NEEDS_ADMIN_REPLY not in _replies(u)


def test_command_needs_admin_reads_the_given_chat(gbot, order):
    bot = gbot(ORDERS[order]())
    assert bot._command_needs_admin("/x", CARA, chat_id=GROUP) is True
    assert bot._command_needs_admin("/x", CARA, chat_id=CARA_DM) is False
    assert bot._command_needs_admin("/x", ADA, chat_id=GROUP) is False
    assert bot._command_needs_admin("/x", ADA, chat_id=ADA_DM) is True
    assert bot._command_needs_admin("/x", CARA, chat_id=None) is True


def test_inject_refuses_a_group_slash_command_with_no_message_chat(
        gbot, typed, run_async, order):
    """The central guard, reached with no chat (a drain, a Retry): the
    session's own chat decides, so the DM owner is a group user."""
    bot = gbot(ORDERS[order]())
    sess = _session(bot, G_SESSION, GROUP, "group")
    assert not run_async(bot._inject_prompt(sess, "/x", driver_user_id=CARA))
    assert typed == []


def test_inject_sends_a_dm_owners_slash_command_with_no_message_chat(
        gbot, typed, run_async, order):
    """The same guard in CARA's DM: with no chat, her DM decides, where
    she is the owner."""
    bot = gbot(ORDERS[order]())
    sess = _session(bot, CARA_SESSION, CARA_DM, "dm")
    assert run_async(bot._inject_prompt(sess, "/deliver x",
                                        driver_user_id=CARA))
    assert typed == [(CARA_SESSION, "/deliver x")]


def test_retry_of_anothers_slash_command_says_why_by_the_tapped_chats_role(
        gbot, typed, run_async, order):
    """Retry of someone else's slash command never sends it; the toast
    says why by the tapper's role in the tapped chat: CARA (a group user)
    is told it needs an admin, ADA (a group admin) that it is someone
    else's command."""
    from aipager.bot.callbacks import RETRY_OTHERS_COMMAND_REPLY

    bot = gbot(ORDERS[order]())
    sess = _session(bot, G_SESSION, GROUP, "group", driver=ALY)
    answers = {}
    for uid in (CARA, ADA):
        sess.last_prompt = "/deliver ship it"
        sess.last_prompt_driver_user_id = ALY
        query = MagicMock()
        query.data = f"{G_SESSION}:retry"
        query.answer = AsyncMock()
        query.message = MagicMock(message_id=42, text="failed")
        query.message.chat = MagicMock(id=GROUP)
        query.message.chat_id = GROUP
        query.from_user = MagicMock(id=uid)
        u = MagicMock(callback_query=query, effective_user=query.from_user,
                      effective_chat=query.message.chat, message=None)
        bot._app.bot.delete_message = AsyncMock()
        run_async(bot._handle_callback(u, MagicMock()))
        answers[uid] = [c.args[0] for c in query.answer.await_args_list
                        if c.args]
    assert typed == []
    assert NEEDS_ADMIN_REPLY in answers[CARA]
    assert RETRY_OTHERS_COMMAND_REPLY in answers[ADA]


# ---- the turn-sender hold (D-H) --------------------------------------------

def test_a_dm_owner_is_held_from_a_group_terminal_turn(
        gbot, typed, run_async, order):
    """Only an owner of THIS chat (or the member of the session's own DM)
    joins a terminal turn; owning another chat does not count."""
    bot = gbot(ORDERS[order]())
    sess = _session(bot, G_SESSION, GROUP, "group", status=Status.BUSY,
                    turn=TURN_SENDER_TERMINAL)
    run_async(bot._handle_message(
        _update("also do this", user_id=CARA, chat_id=GROUP), _ctx()))
    assert typed == []
    assert [e[0] for e in sess.pending_queue] == ["also do this"]


def test_a_group_owner_still_joins_a_group_terminal_turn(
        gbot, typed, run_async, order):
    bot = gbot(ORDERS[order]() + [_aly_dm()])
    sess = _session(bot, G_SESSION, GROUP, "group", status=Status.BUSY,
                    turn=TURN_SENDER_TERMINAL)
    run_async(bot._handle_message(
        _update("also do this", user_id=ALY, chat_id=GROUP), _ctx()))
    assert sess.pending_queue == []
    assert len(typed) == 1


def test_sender_is_owner_reads_the_given_chat(gbot, order):
    bot = gbot(ORDERS[order]())
    assert bot._sender_is_owner(CARA, chat_id=CARA_DM) is True
    assert bot._sender_is_owner(CARA, chat_id=GROUP) is False
    assert bot._sender_is_owner(CARA, chat_id=None) is False


def test_the_hold_reads_the_messages_chat_over_the_sessions(gbot, order):
    """A message's chat wins over the session's: CARA's message from her
    DM (where she is the owner) is not held by a group session's terminal
    turn, while the same message read in the group is. Routing never
    sends a message to another chat's session; this pins the order of
    the lookup (the message's chat first), not a path users can reach."""
    bot = gbot(ORDERS[order]())
    sess = _session(bot, G_SESSION, GROUP, "group", status=Status.BUSY,
                    turn=TURN_SENDER_TERMINAL)
    assert bot._turn_sender_differs(sess, CARA, chat_id=GROUP) is True
    assert bot._turn_sender_differs(sess, CARA) is True
    assert bot._turn_sender_differs(sess, CARA, chat_id=CARA_DM) is False
    # A drain (no chat): the session's own chat, where ALY is the owner.
    assert bot._turn_sender_differs(sess, ALY) is False


# ---- the driver fields -----------------------------------------------------

def test_mark_driver_resolves_in_the_updates_chat(gbot, order):
    bot = gbot(ORDERS[order]())
    sess = _session(bot, G_SESSION, GROUP, "group")
    member = bot._mark_driver(sess, _update("x", user_id=CARA, chat_id=GROUP))
    assert (member.label, member.role) == ("cara", "user")
    assert sess.last_driver_user_id == CARA

    dm = _session(bot, CARA_SESSION, CARA_DM, "dm")
    member = bot._mark_driver(dm, _update("x", user_id=CARA, chat_id=CARA_DM))
    assert member.role == "owner"


def test_mark_driver_refuses_a_chat_the_sender_is_not_in(gbot, order):
    bot = gbot(ORDERS[order]())
    sess = _session(bot, G_SESSION, GROUP, "group", driver=ALY)
    assert bot._mark_driver(
        sess, _update("x", user_id=CARA, chat_id=ADA_DM)) is None
    assert sess.last_driver_user_id == ALY


def test_driver_user_follows_the_sessions_chat(gbot, order):
    bot = gbot(ORDERS[order]())
    group = _session(bot, G_SESSION, GROUP, "group", driver=CARA)
    dm = _session(bot, CARA_SESSION, CARA_DM, "dm", driver=CARA)
    assert bot._driver_user(group).role == "user"
    assert bot._driver_user(dm).role == "owner"
    ada_dm = _session(bot, ADA_SESSION, ADA_DM, "dm", driver=ADA)
    ada_group = _session(bot, "claude-web__g1001", GROUP, "group", driver=ADA)
    assert bot._driver_user(ada_dm).role == "user"
    assert bot._driver_user(ada_group).role == "admin"


def test_tool_auto_deny_uses_the_sessions_chat_role(gbot, order):
    """``_tool_auto_denied`` rides ``_driver_user``: in the group, CARA's
    role is ``user`` and her DM owner rights do not exempt her."""
    bot = gbot(ORDERS[order]())
    group = _session(bot, G_SESSION, GROUP, "group", driver=CARA)
    dm = _session(bot, CARA_SESSION, CARA_DM, "dm", driver=CARA)
    user_denied = sorted(POLICY.get_role("user").deny_tools)
    if not user_denied:
        pytest.skip("the built-in user role denies no tool by name")
    tool = user_denied[0]
    assert bot._tool_auto_denied(group, tool) is True
    assert bot._tool_auto_denied(dm, tool) is False


def test_driver_user_by_id_requires_the_chat():
    from aipager.bot.auth import AuthMixin
    param = inspect.signature(AuthMixin._driver_user_by_id).parameters["chat_id"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty


def test_driver_user_by_id_reads_the_given_chat(gbot, order):
    bot = gbot(ORDERS[order]())
    assert bot._driver_user_by_id(CARA, chat_id=GROUP).role == "user"
    assert bot._driver_user_by_id(CARA, chat_id=CARA_DM).role == "owner"
    assert bot._driver_user_by_id(CARA, chat_id=ADA_DM) is None
    assert bot._driver_user_by_id(CARA, chat_id=None) is None


# ---- button taps (delivery 4), the two-scope case --------------------------

def _query(user_id, chat_id):
    q = MagicMock()
    q.from_user = MagicMock(id=user_id)
    q.message = MagicMock()
    q.message.chat = MagicMock(id=chat_id)
    q.answer = AsyncMock()
    return q


def test_a_tap_resolves_in_the_tapped_chat(gbot, run_async, order):
    bot = gbot(ORDERS[order]())
    assert run_async(bot._authorize_callback(_query(CARA, GROUP))).role == "user"
    assert run_async(bot._authorize_callback(_query(CARA, CARA_DM))).role == "owner"
    assert run_async(bot._authorize_callback(_query(ADA, GROUP))).role == "admin"
    assert run_async(bot._authorize_callback(_query(ADA, CARA_DM))) is None


# ---- live reload: who may have joined a terminal turn ----------------------

def test_terminal_joiners_are_owners_of_the_sessions_chat_only(order):
    scopes = ORDERS[order]() + [_aly_dm()]
    joiners = live_reload._terminal_joiners(scopes, POLICY, GROUP)
    assert joiners == {ALY}
    assert live_reload._terminal_joiners(scopes, POLICY, CARA_DM) == {CARA}
    assert live_reload._terminal_joiners(scopes, POLICY, None) == set()


# ---- DM parity, personal and legacy team mode ------------------------------

def test_dm_parity_the_operators_install(gbot, typed, run_async):
    bot = gbot([_aly_dm()])
    name = "claude-dev__d555"
    sess = _session(bot, name, ALY_DM, "dm", driver=ALY)
    run_async(bot._handle_message(
        _update("fix it", user_id=ALY, chat_id=ALY_DM), _ctx()))
    assert typed == [(name, "[via Telegram · @aly]\nfix it")]
    assert _note(name)["bypass_safety"] is True
    assert bot._command_needs_admin("/anything", ALY, chat_id=ALY_DM) is False
    assert bot._driver_user(sess).role == "owner"
    # A Retry or drain with no chat: the session's own (the DM).
    assert bot._sender_is_owner(ALY, chat_id=bot._attribution_chat(sess, None))
    sess.status, sess.turn_sender_id = Status.BUSY, TURN_SENDER_TERMINAL
    assert bot._turn_sender_differs(sess, ALY) is False


def test_dm_parity_an_unstamped_session_resolves_in_the_home_chat(
        gbot, typed, run_async, monkeypatch):
    bot = gbot([_aly_dm()])
    monkeypatch.setattr("aipager.state.home_chat", lambda: (ALY_DM, "dm"))
    sess = _session(bot, "claude-dev", 0, "", driver=ALY)
    assert bot._driver_user(sess).role == "owner"
    assert run_async(bot._inject_prompt(sess, "hi", driver_user_id=ALY))
    assert typed == [("claude-dev", "[via Telegram · @aly]\nhi")]


def test_personal_mode_resolves_nobody(mk_bot):
    bot = mk_bot()
    assert bot._driver_user_by_id(ALY, chat_id=None) is None
    assert bot._driver_user_by_id(ALY, chat_id=ALY_DM) is None
    assert bot._command_needs_admin("/x", ALY, chat_id=None) is False


def test_legacy_team_mode_ignores_the_chat(mk_bot):
    dev = TeamUser(id=CARA, label="cara", role=Role.DEVELOPER)
    bot = mk_bot(team=Team(group_id=-100, users={CARA: dev},
                           rules=Rules(deny_tools=[])))
    assert bot._driver_user_by_id(CARA, chat_id=None) is dev
    assert bot._driver_user_by_id(CARA, chat_id=12345) is dev
    sess = bot.registry.get_or_create("claude-x")
    assert bot._mark_driver(sess, _update("x", user_id=CARA,
                                          chat_id=-100)) is dev
    assert bot._driver_user(sess) is dev


# ---- no "first chat that lists them" left in attribution -------------------

def test_member_anywhere_is_gone():
    """The first-scope lookup is not defined and not called anywhere in
    the package; the Mini App's chat pick (delivery 17) loops the scopes
    itself, on purpose, and nothing else may."""
    from aipager.bot.auth import AuthMixin
    assert not hasattr(AuthMixin, "_member_anywhere")
    pkg = Path(__file__).resolve().parents[1] / "aipager"
    hits = [str(p) for p in pkg.rglob("*.py")
            if "_member_anywhere" in p.read_text(encoding="utf-8")]
    assert hits == []


def _calls(tree, name):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and getattr(n.func, "attr", None) == name]


def test_every_driver_user_by_id_call_passes_a_chat():
    pkg = Path(__file__).resolve().parents[1] / "aipager"
    seen = 0
    for p in pkg.rglob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for name in ("_driver_user_by_id", "_command_needs_admin",
                     "_sender_is_owner"):
            for call in _calls(tree, name):
                seen += 1
                assert any(k.arg == "chat_id" for k in call.keywords), (
                    f"{p.name}:{call.lineno} {name} without chat_id")
    assert seen >= 5


def test_every_inbound_hold_passes_the_message_chat():
    """The hold on a live message or tap reads the role in the chat it
    came from; only the queue drain (notify.py, no chat of its own) falls
    back to the session's chat."""
    pkg = Path(__file__).resolve().parents[1] / "aipager" / "bot"
    seen = 0
    for name in ("handlers.py", "callbacks.py"):
        tree = ast.parse((pkg / name).read_text(encoding="utf-8"))
        for call in _calls(tree, "_turn_sender_differs"):
            seen += 1
            assert any(k.arg == "chat_id" for k in call.keywords), (
                f"{name}:{call.lineno} _turn_sender_differs without chat_id")
    assert seen >= 2
