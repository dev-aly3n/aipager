"""An inbound prompt must never be typed into an open dialog.

While a session is `INTERACTIVE` the terminal is displaying a permission
or AskUserQuestion prompt and waiting for an answer. Keystrokes sent then
are read as input to that dialog, not as a new turn — so a Telegram
message arriving at that moment was being typed into the prompt the user
was being asked to approve, Enter included.

The queue gate only tested for `BUSY`. `INTERACTIVE` is a different
status, so every inbound path fell through to immediate injection:

    BUSY (mid-turn)                        injected=0  queued=1
    INTERACTIVE (permission prompt open)   injected=1  queued=0

The project had already made this call elsewhere — the Mini App withholds
`/compact` for `waiting` sessions, commenting that typing there "risks
being read as input to that prompt rather than a new turn". This is the
same rule, applied to the paths that actually carry prompts.

Held messages go into `pending_queue`, which brings the cap, the 24h
expiry, persistence and `/clearqueue` with it — all of which an
indefinitely-unanswered prompt needs. No new release path was required:
of the six places that clear `pending_permission`, five transition
straight to `BUSY` and the sixth is the turn-end handler that drains the
queue itself.
"""

from __future__ import annotations

import ast
import pathlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.state import Status

CHAT_ID = -1001
NAME = "claude-x"
PERMISSION_OPEN = {"tool_summary": "Bash: rm -rf /important"}


@pytest.fixture
def wired(mk_bot, monkeypatch):
    """A bot whose session is alive, with injection captured not performed."""
    bot = mk_bot()
    bot._app.bot = AsyncMock()
    injected: list[str] = []
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr(
        "aipager.dtach.inject.send_text_and_enter",
        AsyncMock(side_effect=lambda n, body: injected.append(body) or True))
    bot._mark_driver = MagicMock()
    bot._react = AsyncMock()
    bot._send_busy_and_animate = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._build_reply_context = MagicMock(return_value="")
    sess = bot.registry.get_or_create(NAME)
    sess.label = "x"
    bot.registry.last_active_session = NAME
    return bot, sess, injected


def _in_state(bot, sess, status, *, permission=None):
    bot.registry.transition(NAME, status)
    sess.pending_permission = permission


def _text_update(mk_update, text="please stop and do something else"):
    u = mk_update(text, message_id=9, chat_id=CHAT_ID)
    return u


def _drive(bot, path, update, run_async):
    """Drive one inbound prompt path. Signatures differ, so this is the
    one place that knows how each is called."""
    ctx = MagicMock()
    ctx.bot = MagicMock(id=87654321)
    if path == "text":
        run_async(bot._handle_message(update, ctx))
    elif path == "direct":
        run_async(bot._direct_send(update, "x", update.message.text))
    elif path == "command":
        run_async(bot._send_command(update, "/model sonnet"))
    else:                                            # pragma: no cover
        raise AssertionError(f"unknown path {path}")


PATHS = pytest.mark.parametrize("path", ["text", "direct", "command"])


# ── the defect ─────────────────────────────────────────────────────────

@PATHS
def test_a_prompt_is_not_typed_into_an_open_permission_prompt(
        wired, mk_update, run_async, path):
    """The reported defect, on every inbound path that carries a prompt."""
    bot, sess, injected = wired
    _in_state(bot, sess, Status.INTERACTIVE, permission=PERMISSION_OPEN)

    _drive(bot, path, _text_update(mk_update), run_async)

    assert injected == [], "text reached a session showing an open dialog"
    assert len(sess.pending_queue) == 1, "the message was dropped, not held"


def test_a_held_message_is_delivered_once_the_prompt_is_answered(
        wired, mk_update, run_async, monkeypatch):
    """Holding is only correct if the message actually arrives later.

    Release rides on the existing drain: answering the prompt transitions
    the session to BUSY, and the turn-end handler flushes the queue.
    """
    bot, sess, injected = wired
    _in_state(bot, sess, Status.INTERACTIVE, permission=PERMISSION_OPEN)
    _drive(bot, "text", _text_update(mk_update, "the held message"), run_async)
    assert injected == []

    # The user answers: permission cleared, work resumes, then the turn
    # ends. The drain lives inside the turn-end handler's `status is IDLE`
    # block, so both transitions matter — this mirrors the real sequence
    # rather than jumping straight to the drain.
    sess.pending_permission = None
    bot.registry.transition(NAME, Status.BUSY)
    bot.registry.transition(NAME, Status.IDLE)
    monkeypatch.setattr("aipager.bot.notify.send_rich_message",
                        AsyncMock(return_value={}))
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))

    assert any("the held message" in body for body in injected), (
        f"held message never delivered after the prompt was answered; "
        f"injected={injected}")


# ── the states that must not move ──────────────────────────────────────

@PATHS
def test_an_idle_session_still_injects_immediately(
        wired, mk_update, run_async, path):
    """No prompt open — nothing changes."""
    bot, sess, injected = wired
    _in_state(bot, sess, Status.IDLE)

    _drive(bot, path, _text_update(mk_update), run_async)

    assert len(injected) == 1
    assert sess.pending_queue == []


@PATHS
def test_busy_behaviour_is_unchanged(wired, mk_update, run_async, path):
    """BUSY is no longer a hold condition at all (design.md "queue
    handoff") — every inbound path injects immediately regardless of
    Status.BUSY, exactly like `direct`/`command` already did before this
    change. Only Status.INTERACTIVE (open dialog) and a mixed-sender
    outstanding note still hold — both covered by the other tests in
    this file."""
    bot, sess, injected = wired
    _in_state(bot, sess, Status.BUSY)

    _drive(bot, path, _text_update(mk_update), run_async)

    assert len(injected) == 1 and sess.pending_queue == []


# ── mixed sender (design.md "queue handoff") ────────────────────────────

@PATHS
def test_a_message_from_a_different_sender_is_held(
        wired, mk_update, run_async, monkeypatch, path):
    """The second hold condition `_hold_for_open_dialog` gained: an
    outstanding note from a Telegram user OTHER than whoever sent this
    message is held, even with no dialog open at all."""
    from aipager.policy_snapshot import write_note

    bot, sess, injected = wired
    _in_state(bot, sess, Status.IDLE)
    write_note(NAME, None, None, None, msg_id=1, chat_id=CHAT_ID,
              sender_key=(sess.scope_chat_id or 0, 999999),  # a DIFFERENT user
              body="someone else's message", raw_text="someone else's message")

    _drive(bot, path, _text_update(mk_update), run_async)  # mk_update's default user_id=12345

    assert injected == [], "a mixed-sender message was injected instead of held"
    assert len(sess.pending_queue) == 1


@PATHS
def test_a_message_from_the_same_sender_is_not_held(
        wired, mk_update, run_async, monkeypatch, path):
    """Positive control: an outstanding note from the SAME sender must
    not trip the mixed-sender hold."""
    from aipager.policy_snapshot import write_note

    bot, sess, injected = wired
    _in_state(bot, sess, Status.IDLE)
    write_note(NAME, None, None, None, msg_id=1, chat_id=CHAT_ID,
              sender_key=(sess.scope_chat_id or 0, 12345),  # SAME user as the update below
              body="my own earlier message", raw_text="my own earlier message")

    _drive(bot, path, _text_update(mk_update), run_async)

    assert len(injected) == 1
    assert sess.pending_queue == []


# ── structural guard ───────────────────────────────────────────────────
#
# Six inbound paths carried their own copy of the status check, and two of
# them had no check at all — which is how the hole survived. Enumerating
# them here would reproduce the same failure the next time a seventh is
# added, so the rule is derived from the source instead: in the module
# that owns inbound prompts, a function that injects one must consult the
# gate.
#
# Scope is `bot/handlers.py` AND `bot/callbacks.py` — the two modules that
# turn operator input into a prompt. The first version of this guard covered
# only handlers.py and therefore missed the Retry button, which re-injects
# `sess.last_prompt` from a callback; a reviewer found it by hand, which is
# precisely the failure mode this guard exists to prevent.
#
# Injections of a LITERAL string are excluded structurally, not by name:
# `_inject_prompt(sess, "/compact")` is a session operation, not operator
# text, and has no dialog to be typed into. notify.py stays out of scope
# because its call IS the drain that releases held messages — gating it
# would deadlock the release — and session_ops.py's only call is `/compact`.

_INBOUND_MODULES = ("aipager/bot/handlers.py", "aipager/bot/callbacks.py")
_GATES = ("_hold_for_open_dialog", "dialog_is_open")


def _own_nodes(fn):
    """Walk `fn`'s body without descending into nested definitions."""
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                stack.append(child)


def _calls(nodes, name):
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == name for n in nodes)


def _gate_dominates(fn, call):
    """True if a gate call is guaranteed to run before `call`.

    Function-level checking is not enough. `_handle_callback` is one
    ~850-line dispatcher holding every button action, and it now
    permanently contains one `dialog_is_open()` call in its retry branch —
    so "the function mentions a gate somewhere" would vacuously bless any
    future action branch added beside it. That is the exact shape of
    failure this guard exists to prevent, so it is checked per-branch:
    walk from the call up to the function root and look for a gate only in
    the statements that actually precede it on that path.
    """
    parent = {}
    for node in ast.walk(fn):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    # Ascend, collecting every statement that runs before `call` on this path.
    preceding = []
    node = call
    while node is not fn:
        up = parent.get(node)
        if up is None:
            break
        for field, value in ast.iter_fields(up):
            if isinstance(value, list) and node in value:
                preceding.extend(value[:value.index(node)])
        if isinstance(up, ast.If) and node in up.body + up.orelse:
            preceding.append(up.test)      # `if sess.dialog_is_open(): return`
        node = up

    return any(_calls(list(ast.walk(stmt)), g) for stmt in preceding
               for g in _GATES)


def _operator_text_calls(nodes):
    """Every `_inject_prompt` call carrying something other than a literal.

    `_inject_prompt(sess, "/compact")` is a session operation with no
    dialog to be typed into; anything passing a variable, an f-string or a
    concatenation is operator text and must be gated.
    """
    out = []
    for n in nodes:
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "_inject_prompt"):
            continue
        args = n.args[1:2]
        if not args or not isinstance(args[0], ast.Constant):
            out.append(n)
    return out


def _inbound_paths():
    """`(location, consults_a_gate)` for every function that injects
    operator text."""
    import aipager

    root = pathlib.Path(aipager.__file__).parent.parent
    found = []
    for rel in _INBOUND_MODULES:
        path = root / rel
        tree = ast.parse(path.read_text(), str(path))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            nodes = list(_own_nodes(fn))
            for call in _operator_text_calls(nodes):
                found.append(
                    (f"{rel}:{getattr(call, 'lineno', fn.lineno)} "
                     f"{fn.name}()", _gate_dominates(fn, call)))
    return found


def test_the_inbound_path_detector_still_matches_real_code():
    """Pins the guard below against passing vacuously — a matcher that
    silently stops matching reports zero violations, which looks exactly
    like success."""
    paths = _inbound_paths()
    assert len(paths) >= 7, (
        "detector found fewer inbound prompt paths than the seven that "
        f"exist; the pattern it looks for has moved — found {paths}")
    assert any("callbacks.py" in loc for loc, _ in paths), (
        "the callbacks.py path (Retry) is no longer detected — the guard "
        f"has silently narrowed back to handlers.py: {paths}")


def test_every_inbound_prompt_path_consults_the_gate():
    """The property, over all paths rather than the six known ones."""
    missing = [loc for loc, ok in _inbound_paths() if not ok]
    assert not missing, (
        "these inject operator text without consulting any of "
        f"{_GATES}, so a prompt sent or replayed while a permission or "
        "question dialog is open would be typed into that dialog:\n  "
        + "\n  ".join(missing))


def test_the_retry_button_refuses_while_a_dialog_is_open(
        wired, run_async, monkeypatch):
    """A Retry button outlives the error it was attached to.

    Found by review, not by the first version of this file: the button
    re-injects `sess.last_prompt`, and nothing stopped it firing into a
    dialog the session had since entered. It refuses rather than queues —
    the operator is looking at the prompt, and re-tapping after answering
    is one tap.
    """
    bot, sess, injected = wired
    sess.last_prompt = "the earlier prompt"
    _in_state(bot, sess, Status.INTERACTIVE, permission=PERMISSION_OPEN)
    bot.team = None
    bot.scopes = None
    answers: list[str] = []
    bot._safe_answer = AsyncMock(
        side_effect=lambda q, text="", **kw: answers.append(text))

    edits: list[str] = []
    query = MagicMock()
    query.data = f"{NAME}:retry"
    query.message = MagicMock(text="error", message_id=700)
    query.message.edit_text = AsyncMock(
        side_effect=lambda text, **kw: edits.append(text))
    query.message.chat = MagicMock(id=CHAT_ID)
    query.from_user = MagicMock(id=1)
    query.answer = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    update.effective_chat = MagicMock(id=CHAT_ID)
    update.effective_user = MagicMock(id=1)

    run_async(bot._handle_callback(update, MagicMock()))

    assert injected == [], "Retry typed the old prompt into an open dialog"
    assert any("retry" in a.lower() or "prompt" in a.lower() for a in answers), (
        f"no explanation offered to the operator; answers={answers}")
    # A toast vanishes; tapping Retry is exactly what gets done on the way
    # out the door. The reason has to survive on the card.
    assert edits and "Not retried" in edits[-1], (
        f"refusal left no durable trace on the card; edits={edits}")
    assert "error" in edits[-1], "the card's original text was discarded"


def test_the_retry_button_also_refuses_on_a_mixed_sender_note(
        wired, run_async, monkeypatch):
    """Design.md: "Retry gains the same mixed-sender check beside its
    dialog_is_open() check." — a dialog need not be open at all; an
    outstanding note from a different Telegram user is enough."""
    from aipager.policy_snapshot import write_note

    bot, sess, injected = wired
    sess.last_prompt = "the earlier prompt"
    _in_state(bot, sess, Status.IDLE)  # no dialog open
    bot.team = None
    bot.scopes = None
    write_note(NAME, None, None, None, msg_id=1, chat_id=CHAT_ID,
              sender_key=(sess.scope_chat_id or 0, 999999),  # a DIFFERENT user
              body="someone else's message", raw_text="someone else's message")
    answers: list[str] = []
    bot._safe_answer = AsyncMock(
        side_effect=lambda q, text="", **kw: answers.append(text))

    query = MagicMock()
    query.data = f"{NAME}:retry"
    query.message = MagicMock(text="error", message_id=700)
    query.message.edit_text = AsyncMock()
    query.message.chat = MagicMock(id=CHAT_ID)
    query.from_user = MagicMock(id=1)
    query.answer = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    update.effective_chat = MagicMock(id=CHAT_ID)
    update.effective_user = MagicMock(id=1)  # the tapper — not the note's sender

    run_async(bot._handle_callback(update, MagicMock()))

    assert injected == [], "Retry typed the old prompt despite a mixed-sender note"
    assert answers, "no explanation offered to the operator"
