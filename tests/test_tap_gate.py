"""Roadmap 8.75 + 8.78: one gate for every button tap.

Group audit A5 (probe p1): in scope mode a tap was checked only for chat
membership, so a ``read_only`` member could approve a tool call (and add a
standing allow rule), stop, retry or compact a turn, switch an admin's
session to Auto, and install the voice extra and restart the daemon.
A6 (probe p2): ``resume-auto`` needed only ``can_prompt``, and resume
looked the session up by label in every chat, so a ``user`` in the group
resumed the operator's private DM session in Auto and its last response
was posted into the group.

Pinned here:

- the table (``aipager/bot/tap_gate.py``) knows every verb a dispatcher
  compares or a keyboard builds, and an unknown verb needs PROMPT;
- a verb x role matrix in a group: what each role may tap, and that a
  refused tap reaches no handler;
- refused taps change nothing (no hook decision, no keys, no relaunch, no
  install), the p1 and p2 scenarios;
- a button of another chat's session is refused, in both callback forms;
- resume acts on exactly the tapped session, and Auto via resume needs
  an admin;
- DM parity: the operator's own install (one DM scope, an owner) and
  personal mode pass every tap exactly as before.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import (
    callbacks,
    dashboard,
    new_flow,
    session_parity,
    settings_menu,
    tap_gate,
    update_flow,
)
from aipager.bot.tap_gate import APPROVE, MANAGE, PROMPT, UPDATE, VIEW
from aipager.bot.transport import ACTION_VERBS
from aipager.dtach import hook_reply, inject
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import Status
from aipager.team import Role as TeamRole, Team, User as TeamUser

GROUP = -1001
DM = 555
S = "claude-api__g1001"
DM_S = "claude-api__d555"

ALY, BOB, RO, ADA = 1, 2, 3, 4      # owner, user, read_only, admin
ROLE_OF = {ALY: "owner", BOB: "user", RO: "read_only", ADA: "admin"}

BOT_DIR = Path(callbacks.__file__).resolve().parent
DISPATCHERS = [callbacks, session_parity, new_flow, update_flow,
               settings_menu, dashboard]

REFUSED = tap_gate.REFUSED_TEXT
OTHER_CHAT = tap_gate.OTHER_CHAT_TEXT


def _group_scope():
    return Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=ALY, label="aly", role="owner"),
        Member(id=BOB, label="bob", role="user"),
        Member(id=RO, label="ro", role="read_only"),
        Member(id=ADA, label="ada", role="admin"),
    ))


def _dm_scope():
    return Scope(chat_id=DM, kind="dm", label="aly DM",
                 members=(Member(id=ALY, label="aly", role="owner"),))


# ---------------------------------------------------------------------------
# The table knows every verb
# ---------------------------------------------------------------------------

# Literals a dispatcher compares that the gate never sees as a verb.
HANDLED_BEFORE_THE_GATE = {
    # `_:sx:<idx>:<verb>`: resolve_short_cb turns it into
    # `<session>:<verb>` before the gate, which then sees the real verb.
    "sx:",
    # `UpdateManager.control(action, job_id)`'s own verbs (update_flow):
    # an argument named `action`, not a callback; the buttons that reach
    # it are `_:up:now|wait|stop:<job>`, under the `up:` family.
    "restart-now",
    "wait-more",
}


def _known(literal: str) -> bool:
    """True when the table decides *literal* itself (never the default)."""
    if literal in HANDLED_BEFORE_THE_GATE:
        return True
    if literal in tap_gate.EXACT_CAPS or literal in tap_gate.VOICE_CAPS:
        return True
    if any(literal == p for p, _ in tap_gate.NUMBERED_CAPS):
        return True
    if any(literal.startswith(p) for p, _ in tap_gate.PREFIX_CAPS):
        return True
    # "spref" family (VIEW browse, PROMPT write) is decided explicitly.
    return literal == "spref" or literal.startswith("spref:")


def _action_literals(module) -> set[str]:
    """Every string compared against ``action`` in *module*:
    ``action == X``, ``action != X``, ``action in (X, ...)``,
    ``action not in (...)``, ``action.startswith(X)``; ``X`` a literal or
    a module-level constant."""
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    out: set[str] = set()

    def _strs(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value
        elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            for elt in node.elts:
                yield from _strs(elt)
        elif isinstance(node, ast.Name):
            value = getattr(module, node.id, None)
            if isinstance(value, str):
                yield value
            elif isinstance(value, (frozenset, set, tuple, list)):
                yield from (v for v in value if isinstance(v, str))

    for node in ast.walk(tree):
        if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)
                and node.left.id == "action"):
            for comp in node.comparators:
                out.update(_strs(comp))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "startswith"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "action"):
            for arg in node.args:
                out.update(_strs(arg))
    return out


@pytest.mark.parametrize("module", DISPATCHERS, ids=lambda m: m.__name__)
def test_every_verb_a_dispatcher_compares_is_in_the_table(module):
    literals = _action_literals(module)
    unknown = sorted(lit for lit in literals if not _known(lit))
    assert unknown == [], (
        f"{module.__name__} compares verbs the tap gate's table does not "
        f"list (they would silently need PROMPT): {unknown}. Add each to "
        "aipager/bot/tap_gate.py with the capability it needs.")


def test_the_scan_sees_the_dispatchers_verbs():
    """The scan itself works: it finds verbs we know are compared."""
    assert {"stop", "retry", "allow_always", "perms_confirm",
            "resume_mode_auto", "clear_gone", "pin_answer", "opt",
            "ststop", "now:", "resume_page:", "install", "restart",
            } <= _action_literals(callbacks)
    assert {"resume-auto", "endok", "modeauto", "menu", "talk"} <= (
        _action_literals(session_parity))
    assert {"rdy_m", "nw:", "set:ns:"} <= _action_literals(new_flow)
    assert "up:" in _action_literals(update_flow)


def test_every_session_action_and_answer_verb_is_in_the_table():
    for verb in session_parity._SESSION_ACTIONS | set(ACTION_VERBS):
        assert verb in tap_gate.EXACT_CAPS, verb


def _callback_verbs_built_in_source() -> list[tuple[str, str, str]]:
    """``(file, namespace, verb)`` for every callback the code builds:
    ``session_cb(..., "<verb>")`` / ``f"<prefix>{...}"`` and literal
    ``callback_data="_:..."`` / ``"__voice__:..."`` strings."""
    found = []
    for path in sorted(BOT_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if name == "session_cb" and len(node.args) >= 4:
                    arg = node.args[3]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        found.append((path.name, "session", arg.value))
                    elif isinstance(arg, ast.JoinedStr) and arg.values and isinstance(
                            arg.values[0], ast.Constant):
                        found.append((path.name, "session-prefix", arg.values[0].value))
                for kw in node.keywords:
                    if kw.arg != "callback_data":
                        continue
                    val = kw.value
                    lit = None
                    if isinstance(val, ast.Constant) and isinstance(val.value, str):
                        lit = val.value
                    elif isinstance(val, ast.JoinedStr) and val.values and isinstance(
                            val.values[0], ast.Constant):
                        lit = val.values[0].value
                    if lit and ":" in lit and lit.split(":", 1)[0] in (
                            tap_gate.SENTINEL_NAMESPACES):
                        ns, verb = lit.split(":", 1)
                        found.append((path.name, ns, verb))
    return found


def test_every_button_the_code_builds_has_a_table_entry():
    built = _callback_verbs_built_in_source()
    assert len(built) > 40, built          # the scan found the keyboards
    unknown = []
    for fname, ns, verb in built:
        if ns == "__voice__":
            ok = verb in tap_gate.VOICE_CAPS
        elif ns == "session-prefix":
            ok = (any(verb == p for p, _ in tap_gate.NUMBERED_CAPS)
                  or any(verb.startswith(p) for p, _ in tap_gate.PREFIX_CAPS))
        else:
            ok = _known(verb) or (verb == "" and ns == "_")
        if not ok:
            unknown.append((fname, ns, verb))
    assert unknown == []


@pytest.mark.parametrize("ns,verb", [
    ("claude-x", "zz_new_verb"), ("_", "zz_new_verb"), ("__voice__", "zz"),
    ("claude-x", "opt"), ("claude-x", "optx"), ("claude-x", "ststop"),
    ("claude-x", ""),
])
def test_an_unknown_verb_needs_prompt(ns, verb):
    assert tap_gate.required_capability(ns, verb) is PROMPT


@pytest.mark.parametrize("ns,verb,cap", [
    ("claude-x", "allow", APPROVE), ("claude-x", "allow_always", APPROVE),
    ("claude-x", "deny", APPROVE), ("claude-x", "opt3", APPROVE),
    ("claude-x", "submit", APPROVE), ("claude-x", "continue", APPROVE),
    ("claude-x", dashboard.PINNED_ANSWER_VERB, APPROVE),
    ("claude-x", "stop", PROMPT), ("claude-x", "retry", PROMPT),
    ("claude-x", "compact", PROMPT), ("claude-x", "now:12", PROMPT),
    ("claude-x", "ststop7", PROMPT), ("claude-x", "pstop7", PROMPT),
    ("claude-x", "perms_confirm", PROMPT), ("claude-x", "resume-ask", PROMPT),
    ("claude-x", "resume-auto", MANAGE), ("claude-x", "resume_mode_auto", MANAGE),
    ("claude-x", "resume_mode_ask", PROMPT), ("_", "clear_gone", PROMPT),
    ("_", "resume_page:2", VIEW), ("_", "resume_noop", VIEW),
    ("_", "st:list", VIEW), ("_", "st:ended", VIEW), ("_", "pick:cancel", VIEW),
    ("_", "set", VIEW), ("_", "set:layout", VIEW), ("_", "spref", VIEW),
    ("_", "spref:0", VIEW), ("_", "spref:0:layout", VIEW),
    ("_", "spref:0:layout:merged", PROMPT),
    ("_", "up:chk", UPDATE), ("_", "nw:open", PROMPT),
    ("claude-x", "rdy_auto", PROMPT), ("claude-x", "rdy_m2", PROMPT),
    ("__voice__", "install", UPDATE), ("__voice__", "restart", UPDATE),
    ("__voice__", "cancel", PROMPT), ("claude-x", "restart", PROMPT),
    ("claude-x", "menu", VIEW), ("claude-x", "diff", VIEW),
    ("claude-x", "menu-close", VIEW), ("claude-x", "kill-cancel", PROMPT),
    ("claude-x", "restart-cancel", PROMPT), ("claude-x", "delete-cancel", PROMPT),
])
def test_table_entries(ns, verb, cap):
    assert tap_gate.required_capability(ns, verb) is cap


def test_a_mode_card_switching_to_auto_needs_manage():
    for verb in ("perms_confirm", "perms_stop_switch"):
        assert tap_gate.required_capability(
            "claude-x", verb, perms_target_auto=True) is MANAGE
        assert tap_gate.required_capability(
            "claude-x", verb, perms_target_auto=False) is PROMPT
    # Only those two verbs read the flag.
    assert tap_gate.required_capability(
        "claude-x", "perms_cancel", perms_target_auto=True) is PROMPT


# ---------------------------------------------------------------------------
# Taps through the real handler
# ---------------------------------------------------------------------------

def _tap(chat_id, user_id, data, *, msg_id=50, text="card"):
    q = MagicMock()
    q.data = data
    q.from_user = MagicMock(id=user_id)
    q.message = MagicMock()
    q.message.chat = MagicMock(id=chat_id)
    q.message.chat_id = chat_id
    q.message.message_id = msg_id
    q.message.text = text
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
def gbot(mk_bot):
    def _mk(scopes="group"):
        if scopes == "group":
            scopes = [_group_scope(), _dm_scope()]
        elif scopes == "dm":
            scopes = [_dm_scope()]
        bot = mk_bot(scopes=scopes)
        bot.policy = load_policy()
        bot._app.bot.id = 999
        bot._app.bot.delete_message = AsyncMock()
        bot._app.bot.edit_message_text = AsyncMock()
        for name in ("_card_for_injected", "_react", "_maybe_update_bot_name",
                     "_update_bot_commands", "_edit_busy_raw",
                     "_send_busy_and_animate"):
            setattr(bot, name, AsyncMock())
        bot._start_animation = MagicMock()
        bot._stop_animation = MagicMock()
        return bot
    return _mk


def _session(bot, name=S, chat=GROUP, kind="group", label="api",
             status=Status.IDLE):
    s = bot.registry.get_or_create(name)
    s.label = label
    s.scope_chat_id, s.scope_kind = chat, kind
    s.status = status
    return s


class _Effects:
    """Everything a tap could do to the world, recorded."""

    def __init__(self, monkeypatch, bot):
        self.keys: list = []
        self.typed: list = []
        self.launched: list = []
        self.killed: list = []
        self.decisions: list = []

        async def _keys(name, key, *a, **k):
            self.keys.append((name, key))
            return True

        async def _type(name, text, *a, **k):
            self.typed.append((name, text))
            return True

        async def _launch(short, **kw):
            self.launched.append((short, kw))
            return True, ""

        async def _kill(name, *a, **k):
            self.killed.append(name)
            return True

        monkeypatch.setattr(inject, "send_keys", _keys)
        monkeypatch.setattr(inject, "send_text_and_enter", _type)
        monkeypatch.setattr(inject, "launch_session", _launch)
        monkeypatch.setattr(inject, "kill_session", _kill)
        monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
        monkeypatch.setattr(inject, "list_sessions", AsyncMock(return_value=[]))
        monkeypatch.setattr(hook_reply, "send_decision",
                            lambda reply, decision: self.decisions.append(decision) or True)
        bot._install_voice_extra = AsyncMock()
        bot._restart_daemon = AsyncMock()
        self.bot = bot

    def nothing(self) -> bool:
        b = self.bot
        return (self.keys == [] and self.typed == [] and self.launched == []
                and self.killed == [] and self.decisions == []
                and b._install_voice_extra.call_count == 0
                and b._restart_daemon.call_count == 0)


# ---- the matrix ----------------------------------------------------------

# One sample tap per verb, family and namespace (session verbs on S).
MATRIX_TAPS: list[tuple[str, str]] = (
    [(S, v) for v in sorted(tap_gate.EXACT_CAPS)
     if v not in ("st:list", "st:ended", "pick:cancel", "resume_noop",
                  "clear_gone", "set", "spref")]
    + [("_", v) for v in ("st:list", "st:ended", "pick:cancel", "resume_noop",
                          "clear_gone", "set", "spref")]
    + [(S, f"{p}7") for p, _ in tap_gate.NUMBERED_CAPS]
    + [(S, "now:12"), ("_", "resume_page:1"), (S, "rdy_auto"), ("_", "nw:open"),
       ("_", "up:chk"), ("_", "set:layout"), ("_", "set:layout:merged"),
       ("_", "spref:0"), ("_", "spref:0:layout:merged")]
    + [("__voice__", v) for v in sorted(tap_gate.VOICE_CAPS)]
    + [(S, "zz_unknown_verb")]
)

ALLOWED = {
    VIEW: {ALY, ADA, BOB, RO},
    APPROVE: {ALY, ADA, BOB},
    PROMPT: {ALY, ADA, BOB},
    # `_is_admin_user` / `_is_update_admin`: the owner (bypass_safety);
    # the admin role joins with roadmap 8.83 (D-B).
    MANAGE: {ALY},
    UPDATE: {ALY},
}


@pytest.fixture
def reached(monkeypatch):
    """Replace the first handler after the gate with a recorder: a tap
    that gets past the gate lands here, and nothing else runs."""
    seen: list = []

    async def _first(bot, update, query, session_name, action):
        seen.append((session_name, action))
        return True

    monkeypatch.setattr(update_flow, "handle_callback", _first)
    return seen


@pytest.mark.parametrize("uid", [ALY, ADA, BOB, RO], ids=lambda u: ROLE_OF[u])
@pytest.mark.parametrize("ns,verb", MATRIX_TAPS, ids=lambda x: str(x))
def test_group_matrix(gbot, reached, run_async, ns, verb, uid):
    bot = gbot()
    _session(bot)
    u, q = _tap(GROUP, uid, f"{ns}:{verb}")
    run_async(bot._handle_callback(u, MagicMock()))
    cap = tap_gate.required_capability(ns, verb)
    if uid in ALLOWED[cap]:
        assert reached == [(ns, verb)]
        assert REFUSED not in _answers(q)
    else:
        assert reached == []
        expected = update_flow.DENIED_TEXT if cap is UPDATE else REFUSED
        assert _answers(q) == [expected]
        assert q.answer.await_args_list[0].kwargs.get("show_alert") is True


@pytest.mark.parametrize("verb", ["perms_confirm", "perms_stop_switch"])
@pytest.mark.parametrize("uid", [ALY, ADA, BOB, RO], ids=lambda u: ROLE_OF[u])
def test_a_switch_to_auto_card_needs_an_admin_tapping_it(
        gbot, reached, run_async, verb, uid):
    bot = gbot()
    _session(bot)
    bot._perms_pending[S] = {"target_skip_perms": True, "msg_id": 90,
                             "label": "api", "turn": 1}
    u, q = _tap(GROUP, uid, f"{S}:{verb}", msg_id=90)
    run_async(bot._handle_callback(u, MagicMock()))
    assert (reached != []) is (uid == ALY)


@pytest.mark.parametrize("uid", [ALY, ADA, BOB], ids=lambda u: ROLE_OF[u])
def test_a_switch_to_ask_card_needs_prompt_only(gbot, reached, run_async, uid):
    bot = gbot()
    _session(bot)
    bot._perms_pending[S] = {"target_skip_perms": False, "msg_id": 90,
                             "label": "api", "turn": 1}
    u, q = _tap(GROUP, uid, f"{S}:perms_confirm", msg_id=90)
    run_async(bot._handle_callback(u, MagicMock()))
    assert reached == [(S, "perms_confirm")]


def test_an_auto_record_for_another_card_does_not_raise_the_bar(
        gbot, reached, run_async):
    """The record belongs to card 90; a tap on an older card (89) is not
    for it (the perms branch answers "out of date"), so the gate does not
    hold it to the Auto bar either."""
    bot = gbot()
    _session(bot)
    bot._perms_pending[S] = {"target_skip_perms": True, "msg_id": 90,
                             "label": "api", "turn": 1}
    u, q = _tap(GROUP, BOB, f"{S}:perms_confirm", msg_id=89)
    run_async(bot._handle_callback(u, MagicMock()))
    assert reached == [(S, "perms_confirm")]


# ---- refused taps change nothing (probe p1) ------------------------------

def _waiting_on_bash(bot):
    sess = _session(bot, status=Status.INTERACTIVE)
    sess.busy_msg_id = 50
    sess.pending_permission = {
        "tool_info": {"name": "Bash", "standing_rule_suggestion": {
            "type": "addRules",
            "rules": [{"toolName": "Bash", "ruleContent": "rm:*"}],
            "behavior": "allow", "destination": "localSettings"}},
        "tool_summary": "rm -rf build/", "hook_reply": "sock-x",
    }
    return sess


@pytest.mark.parametrize("verb", ["allow", "allow_always", "deny"])
def test_p1_read_only_cannot_answer_a_permission(gbot, run_async, monkeypatch, verb):
    bot = gbot()
    fx = _Effects(monkeypatch, bot)
    sess = _waiting_on_bash(bot)
    u, q = _tap(GROUP, RO, f"{S}:{verb}", msg_id=50)
    run_async(bot._handle_callback(u, MagicMock()))
    assert _answers(q) == [REFUSED]
    assert fx.nothing()
    assert sess.pending_permission is not None
    assert sess.status is Status.INTERACTIVE
    bot._app.bot.send_message.assert_not_called()       # no audit line


def test_p1_a_user_may_answer_a_permission(gbot, run_async, monkeypatch):
    """Control: `user` has can_approve, and the tap goes through."""
    bot = gbot()
    fx = _Effects(monkeypatch, bot)
    sess = _waiting_on_bash(bot)
    u, q = _tap(GROUP, BOB, f"{S}:allow", msg_id=50)
    run_async(bot._handle_callback(u, MagicMock()))
    assert REFUSED not in _answers(q)
    assert fx.decisions and fx.decisions[0].get("behavior") == "allow"
    assert sess.pending_permission is None


def test_p1_read_only_cannot_answer_a_question(gbot, run_async, monkeypatch):
    bot = gbot()
    fx = _Effects(monkeypatch, bot)
    sess = _session(bot, status=Status.INTERACTIVE)
    sess.busy_msg_id = 50
    sess.pending_permission = {"ask_question": True, "question": "Which?",
                               "options": [{"label": "a"}, {"label": "b"}],
                               "questions": [{"question": "Which?"}],
                               "current_idx": 0, "multi_select": False,
                               "cursor_pos": 0, "selected": set()}
    for verb in ("opt1", "submit", "continue"):
        u, q = _tap(GROUP, RO, f"{S}:{verb}", msg_id=50)
        run_async(bot._handle_callback(u, MagicMock()))
        assert _answers(q) == [REFUSED], verb
    assert fx.nothing()
    assert sess.pending_permission["current_idx"] == 0


def test_p1_read_only_cannot_stop_compact_or_retry(gbot, run_async, monkeypatch):
    bot = gbot()
    fx = _Effects(monkeypatch, bot)
    sess = _session(bot, status=Status.BUSY)
    sess.busy_msg_id = 50
    sess.last_prompt = "deploy the thing"
    sess.last_prompt_driver_user_id = ALY
    bot._stop_session_core = AsyncMock()
    bot._inject_prompt = AsyncMock(return_value=True)
    for verb in ("stop", f"ststop{sess.turn_key}", f"pstop{sess.turn_key}",
                 "compact", "retry", "now:77"):
        u, q = _tap(GROUP, RO, f"{S}:{verb}", msg_id=50)
        run_async(bot._handle_callback(u, MagicMock()))
        assert _answers(q) == [REFUSED], verb
    bot._stop_session_core.assert_not_called()
    bot._inject_prompt.assert_not_called()
    bot._app.bot.delete_message.assert_not_called()
    assert fx.nothing()
    assert sess.status is Status.BUSY


def test_p1_read_only_stop_was_reachable_before(gbot, run_async, monkeypatch):
    """Control for the test above: the owner's Stop reaches the core."""
    bot = gbot()
    _Effects(monkeypatch, bot)
    sess = _session(bot, status=Status.BUSY)
    sess.busy_msg_id = 50
    bot._stop_session = AsyncMock(return_value=MagicMock(ok=True, reason=""))
    u, q = _tap(GROUP, ALY, f"{S}:stop", msg_id=50)
    run_async(bot._handle_callback(u, MagicMock()))
    bot._stop_session.assert_awaited_once()


@pytest.mark.parametrize("uid", [RO, BOB, ADA], ids=lambda u: ROLE_OF[u])
def test_p1_stop_and_switch_to_auto_needs_an_admin(gbot, run_async, monkeypatch, uid):
    bot = gbot()
    fx = _Effects(monkeypatch, bot)
    sess = _session(bot, status=Status.BUSY)
    record = {"target_skip_perms": True, "msg_id": 90, "label": "api",
              "turn": sess.turn_key}
    bot._perms_pending[S] = record
    bot._perms_switch_core = AsyncMock()
    bot._do_perms_switch_via_fn = AsyncMock()
    for verb in ("perms_stop_switch", "perms_confirm"):
        u, q = _tap(GROUP, uid, f"{S}:{verb}", msg_id=90)
        run_async(bot._handle_callback(u, MagicMock()))
        assert _answers(q) == [REFUSED], verb
    bot._do_perms_switch_via_fn.assert_not_called()
    bot._perms_switch_core.assert_not_called()
    assert bot._perms_pending[S] is record          # the card still works
    assert sess.skip_perms is False
    assert fx.nothing()


def test_p1_owner_stop_and_switch_to_auto_goes_ahead(gbot, run_async, monkeypatch):
    bot = gbot()
    _Effects(monkeypatch, bot)
    sess = _session(bot, status=Status.BUSY)
    bot._perms_pending[S] = {"target_skip_perms": True, "msg_id": 90,
                             "label": "api", "turn": sess.turn_key}
    bot._do_perms_switch_via_fn = AsyncMock()
    u, q = _tap(GROUP, ALY, f"{S}:perms_stop_switch", msg_id=90)
    run_async(bot._handle_callback(u, MagicMock()))
    bot._do_perms_switch_via_fn.assert_awaited_once()
    assert bot._do_perms_switch_via_fn.await_args.args[1] is True


@pytest.mark.parametrize("uid", [RO, BOB, ADA], ids=lambda u: ROLE_OF[u])
def test_p1_voice_install_and_restart_need_the_update_admin(
        gbot, run_async, monkeypatch, uid):
    bot = gbot()
    fx = _Effects(monkeypatch, bot)
    for verb in ("install", "restart"):
        u, q = _tap(GROUP, uid, f"__voice__:{verb}", msg_id=91)
        run_async(bot._handle_callback(u, MagicMock()))
        assert _answers(q) == [update_flow.DENIED_TEXT], verb
    assert fx.nothing()


def test_p1_owner_voice_install_goes_ahead(gbot, run_async, monkeypatch):
    bot = gbot()
    _Effects(monkeypatch, bot)

    async def _go():
        u, q = _tap(GROUP, ALY, "__voice__:install", msg_id=91)
        await bot._handle_callback(u, MagicMock())
        await asyncio.sleep(0)     # the fire-and-forget task
    run_async(_go())
    assert bot._install_voice_extra.call_count == 1


def test_read_only_cannot_clear_ended_sessions(gbot, run_async, monkeypatch):
    bot = gbot()
    fx = _Effects(monkeypatch, bot)
    sess = _session(bot, status=Status.GONE)
    u, q = _tap(GROUP, RO, "_:clear_gone")
    run_async(bot._handle_callback(u, MagicMock()))
    assert _answers(q) == [REFUSED]
    assert sess.hidden_from_status is False
    assert fx.nothing()


def test_read_only_cannot_write_a_session_preference(gbot, run_async, monkeypatch):
    bot = gbot()
    _Effects(monkeypatch, bot)
    sess = _session(bot)
    session_parity._register_pref_index(bot, GROUP, [sess.name])
    u, q = _tap(GROUP, RO, "_:spref:0:layout:merged")
    run_async(bot._handle_callback(u, MagicMock()))
    assert _answers(q) == [REFUSED]
    assert sess.override_layout is None


def test_read_only_may_still_look(gbot, run_async, monkeypatch):
    """VIEW taps stay open to read_only: /status's Ended view re-renders."""
    bot = gbot()
    _Effects(monkeypatch, bot)
    _session(bot, status=Status.GONE)
    u, q = _tap(GROUP, RO, "_:st:ended")
    run_async(bot._handle_callback(u, MagicMock()))
    assert REFUSED not in _answers(q)
    q.edit_message_text.assert_awaited()


# ---- resume: the tapped session only; Auto needs an admin (probe p2) -----

def _p2_world(bot):
    dm = _session(bot, DM_S, DM, "dm", status=Status.GONE)      # older
    dm.claude_session_id, dm.cwd = "dm-session-id", "/home/op/private"
    dm.last_assistant_preview = "PRIVATE: the prod DB password is in vault"
    grp = _session(bot, S, GROUP, "group", status=Status.GONE)
    grp.claude_session_id, grp.cwd = "group-session-id", "/srv/team"
    grp.last_assistant_preview = "group work"
    return dm, grp


def _edited_texts(q):
    return [c.args[0] if c.args else c.kwargs.get("text", "")
            for c in q.edit_message_text.await_args_list]


@pytest.mark.parametrize("uid", [BOB, ADA, RO], ids=lambda u: ROLE_OF[u])
@pytest.mark.parametrize("form", ["long", "short"])
def test_p2_resume_auto_needs_an_admin(gbot, run_async, monkeypatch, uid, form):
    bot = gbot()
    fx = _Effects(monkeypatch, bot)
    dm, grp = _p2_world(bot)
    data = (f"{S}:resume-auto" if form == "long"
            else session_parity.session_cb(bot, GROUP, grp, "resume-auto"))
    u, q = _tap(GROUP, uid, data, msg_id=60)
    run_async(bot._handle_callback(u, MagicMock()))
    assert _answers(q) == [REFUSED]
    assert fx.launched == []
    assert dm.status is Status.GONE and grp.status is Status.GONE
    assert dm.skip_perms is False and grp.skip_perms is False
    assert not any("PRIVATE" in t for t in _edited_texts(q))


@pytest.mark.parametrize("uid", [BOB, RO], ids=lambda u: ROLE_OF[u])
def test_legacy_resume_mode_auto_needs_an_admin(gbot, run_async, monkeypatch, uid):
    bot = gbot()
    fx = _Effects(monkeypatch, bot)
    dm, grp = _p2_world(bot)
    u, q = _tap(GROUP, uid, f"{S}:resume_mode_auto", msg_id=60)
    run_async(bot._handle_callback(u, MagicMock()))
    assert _answers(q) == [REFUSED]
    assert fx.launched == []


def test_p2_user_resume_ask_resumes_the_groups_session_not_the_dm_twin(
        gbot, run_async, monkeypatch):
    bot = gbot()
    fx = _Effects(monkeypatch, bot)
    dm, grp = _p2_world(bot)
    data = session_parity.session_cb(bot, GROUP, grp, "resume-ask")
    u, q = _tap(GROUP, BOB, data, msg_id=60)
    run_async(bot._handle_callback(u, MagicMock()))
    assert [n for n, _ in fx.launched] == ["api__g1001"]
    assert fx.launched[0][1].get("resume_id") == "group-session-id"
    assert fx.launched[0][1].get("skip_perms") is False
    assert dm.status is Status.GONE
    assert not any("PRIVATE" in t for t in _edited_texts(q))


def test_p2_owner_resume_auto_resumes_exactly_the_tapped_session(
        gbot, run_async, monkeypatch):
    bot = gbot()
    fx = _Effects(monkeypatch, bot)
    dm, grp = _p2_world(bot)
    data = session_parity.session_cb(bot, GROUP, grp, "resume-auto")
    u, q = _tap(GROUP, ALY, data, msg_id=60)
    run_async(bot._handle_callback(u, MagicMock()))
    assert [n for n, _ in fx.launched] == ["api__g1001"]
    assert fx.launched[0][1].get("skip_perms") is True
    assert dm.status is Status.GONE and dm.skip_perms is False


def _unstamped_twin_world(bot):
    """An older UNSTAMPED ended `api` (scope 0 matches every chat in a
    label lookup, roadmap 8.72) beside the group's own ended `api`."""
    old = _session(bot, "claude-api", 0, "", status=Status.GONE)
    old.claude_session_id, old.cwd = "old-session-id", "/somewhere/else"
    grp = _session(bot, S, GROUP, "group", status=Status.GONE)
    grp.claude_session_id, grp.cwd = "group-session-id", "/srv/team"
    return old, grp


def test_resume_button_acts_on_the_session_it_names(gbot, run_async, monkeypatch):
    """By label, the older unstamped twin would be found first."""
    bot = gbot()
    fx = _Effects(monkeypatch, bot)
    old, grp = _unstamped_twin_world(bot)
    assert bot.registry.find_by_label("api", GROUP, include_gone=True) is old
    data = session_parity.session_cb(bot, GROUP, grp, "resume-ask")
    u, q = _tap(GROUP, BOB, data, msg_id=60)
    run_async(bot._handle_callback(u, MagicMock()))
    assert [n for n, _ in fx.launched] == ["api__g1001"]
    assert old.status is Status.GONE


def test_legacy_resume_mode_button_acts_on_the_session_it_names(
        gbot, run_async, monkeypatch):
    bot = gbot()
    fx = _Effects(monkeypatch, bot)
    old, grp = _unstamped_twin_world(bot)
    u, q = _tap(GROUP, BOB, f"{S}:resume_mode_ask", msg_id=60)
    run_async(bot._handle_callback(u, MagicMock()))
    assert [n for n, _ in fx.launched] == ["api__g1001"]
    assert old.status is Status.GONE


def test_legacy_resume_mode_owner_auto_resumes_the_named_session(
        gbot, run_async, monkeypatch):
    bot = gbot()
    fx = _Effects(monkeypatch, bot)
    dm, grp = _p2_world(bot)
    u, q = _tap(GROUP, ALY, f"{S}:resume_mode_auto", msg_id=60)
    run_async(bot._handle_callback(u, MagicMock()))
    assert [n for n, _ in fx.launched] == ["api__g1001"]
    assert fx.launched[0][1].get("skip_perms") is True
    assert dm.status is Status.GONE


# ---- another chat's buttons ----------------------------------------------

@pytest.mark.parametrize("form", ["long", "short"])
@pytest.mark.parametrize("verb", ["stop", "allow", "resume-ask", "menu", "retry"])
def test_a_dm_sessions_button_tapped_in_the_group_is_refused(
        gbot, reached, run_async, form, verb):
    bot = gbot()
    dm = _session(bot, DM_S, DM, "dm", status=Status.BUSY)
    data = (f"{DM_S}:{verb}" if form == "long"
            # The DM session's index registered in the GROUP's table, as a
            # button sent to the wrong chat would carry it.
            else session_parity.session_cb(bot, GROUP, dm, verb))
    u, q = _tap(GROUP, ALY, data)
    run_async(bot._handle_callback(u, MagicMock()))
    assert reached == []
    assert _answers(q) == [OTHER_CHAT]


def test_a_group_sessions_button_tapped_in_the_dm_is_refused(
        gbot, reached, run_async):
    bot = gbot()
    _session(bot, status=Status.BUSY)
    u, q = _tap(DM, ALY, f"{S}:stop")
    run_async(bot._handle_callback(u, MagicMock()))
    assert reached == []
    assert _answers(q) == [OTHER_CHAT]


@pytest.mark.parametrize("form", ["long", "short"])
def test_a_sessions_own_chat_is_not_refused(gbot, reached, run_async, form):
    bot = gbot()
    grp = _session(bot, status=Status.BUSY)
    data = (f"{S}:stop" if form == "long"
            else session_parity.session_cb(bot, GROUP, grp, "stop"))
    u, q = _tap(GROUP, BOB, data)
    run_async(bot._handle_callback(u, MagicMock()))
    assert reached == [(S, "stop")]


def test_an_unstamped_session_is_not_refused_here(gbot, reached, run_async):
    """Roadmap 8.72 (a later delivery) decides unstamped sessions."""
    bot = gbot()
    _session(bot, "claude-legacy", 0, "", status=Status.BUSY)
    u, q = _tap(GROUP, BOB, "claude-legacy:stop")
    run_async(bot._handle_callback(u, MagicMock()))
    assert reached == [("claude-legacy", "stop")]


def test_sentinels_are_not_sessions(gbot, reached, run_async):
    bot = gbot()
    # Even a registry entry spelled like a sentinel is not looked up.
    _session(bot, "__voice__", DM, "dm")
    _session(bot, "_", DM, "dm")
    for data in ("__voice__:install", "_:st:list"):
        u, q = _tap(GROUP, ALY, data)
        run_async(bot._handle_callback(u, MagicMock()))
        assert OTHER_CHAT not in _answers(q)
    assert reached == [("__voice__", "install"), ("_", "st:list")]


# ---- DM parity and personal mode -----------------------------------------

DM_TAPS = [(DM_S if ns == S else ns, verb) for ns, verb in MATRIX_TAPS]


@pytest.mark.parametrize("ns,verb", DM_TAPS, ids=lambda x: str(x))
def test_dm_owner_passes_every_tap(gbot, reached, run_async, ns, verb):
    """The operator's own install: one DM scope, an owner."""
    bot = gbot("dm")
    _session(bot, DM_S, DM, "dm")
    bot._perms_pending[DM_S] = {"target_skip_perms": True, "msg_id": 50,
                                "label": "api", "turn": 1}
    u, q = _tap(DM, ALY, f"{ns}:{verb}")
    run_async(bot._handle_callback(u, MagicMock()))
    assert reached == [(ns, verb)]
    assert not {REFUSED, OTHER_CHAT, update_flow.DENIED_TEXT} & set(_answers(q))


@pytest.mark.parametrize("ns,verb", MATRIX_TAPS, ids=lambda x: str(x))
def test_personal_mode_passes_every_tap(mk_bot, reached, run_async, ns, verb):
    bot = mk_bot()                       # scopes None, team None
    bot.policy = load_policy()
    s = bot.registry.get_or_create(S)
    s.scope_chat_id = GROUP              # even a stamped session elsewhere
    u, q = _tap(DM, ALY, f"{ns}:{verb}")
    run_async(bot._handle_callback(u, MagicMock()))
    assert reached == [(ns, verb)]
    assert REFUSED not in _answers(q) and OTHER_CHAT not in _answers(q)


def test_legacy_team_mode_is_unchanged(mk_bot, reached, run_async):
    """Legacy team.yaml mode keeps today's membership-only tap check (the
    gate is scope mode's); its read_only member still reaches handlers."""
    team = Team(group_id=GROUP, users={
        1: TeamUser(id=1, label="aly", role=TeamRole.ADMIN),
        3: TeamUser(id=3, label="ro", role=TeamRole.READ_ONLY),
    })
    bot = mk_bot(team=team)
    bot.policy = load_policy()
    u, q = _tap(GROUP, 3, f"{S}:allow")
    run_async(bot._handle_callback(u, MagicMock()))
    assert reached == [(S, "allow")]
    u, q = _tap(GROUP, 99, f"{S}:allow")           # not on the list
    run_async(bot._handle_callback(u, MagicMock()))
    assert reached == [(S, "allow")]
    assert "Not on the allow-list" in _answers(q)


def test_a_non_member_is_still_refused_first(gbot, reached, run_async):
    bot = gbot()
    _session(bot)
    u, q = _tap(GROUP, 42, f"{S}:menu")
    run_async(bot._handle_callback(u, MagicMock()))
    assert reached == []
    assert _answers(q) == ["Not on the allow-list"]


# ---- custom roles: APPROVE reads can_approve, PROMPT reads can_prompt -----

def _custom_bot(gbot):
    """policy.yaml roles that split the two flags: `drafter` may prompt
    but not approve, `reviewer` may approve but not prompt."""
    import dataclasses

    from aipager.policy import Role as PolicyRole

    base = load_policy()
    roles = dict(base.roles)
    roles["drafter"] = PolicyRole(name="drafter", can_prompt=True, can_approve=False)
    roles["reviewer"] = PolicyRole(name="reviewer", can_prompt=False, can_approve=True)
    scope = Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=ALY, label="aly", role="owner"),
        Member(id=7, label="dee", role="drafter"),
        Member(id=8, label="rae", role="reviewer"),
    ))
    bot = gbot([scope])
    bot.policy = dataclasses.replace(base, roles=roles)
    return bot


@pytest.mark.parametrize("uid,verb,ok", [
    (7, "allow", False), (7, "opt2", False), (7, "stop", True),
    (8, "allow", True), (8, "opt2", True), (8, "stop", False),
    (8, "menu", True), (7, "menu", True),
])
def test_custom_roles_split_approve_from_prompt(gbot, reached, run_async, uid, verb, ok):
    bot = _custom_bot(gbot)
    _session(bot)
    u, q = _tap(GROUP, uid, f"{S}:{verb}")
    run_async(bot._handle_callback(u, MagicMock()))
    assert (reached == [(S, verb)]) is ok
    assert (REFUSED in _answers(q)) is (not ok)
