"""A Stop button must only stop the turn it was built for.

The callback encodes which session, never which task. Tapping a Stop
button on an earlier card interrupted whatever was running at that moment
and silently discarded the queue behind it:

    before: BUSY, busy card = 900, queued = 2
    tap Stop on the OLD card (message 700)
    after : IDLE, busy card = None, queued = 0
            keys sent to the terminal: ['Escape', 'Escape']

Stale buttons are not a clearing bug. The final render omits
`reply_markup` and is deliberately exempted from the skip-if-unchanged
dedupe so the keyboard always comes off a finished turn. They are what is
left when that one edit fails — a rate limit, or a message older than 48h
that Telegram refuses to edit — and nothing retries it. So the fix is to
make a surviving button fail safely, not to clear harder.

**Ordering, not identity.** Telegram message ids increase within a chat,
and every card that can carry a Stop button for the live turn — the busy
card, plus any permission or AskUserQuestion card raised during it — is
sent at or after the busy card. A tap on an older message came from an
earlier turn.

Two alternatives were tried and rejected:

- Comparing the tapped id to `busy_msg_id` for **equality** refuses a
  legitimate Stop on a permission card, which is a different message.
- Stamping a turn ordinal into the callback (`stop@7`) changes the wire
  format that a dozen existing tests pin as a per-verb reachability
  contract, and breaks the documented promise that a long-form button
  already sitting in chat history still fires. It was implemented, run,
  and reverted when those tests failed — the contract is deliberate
  (`tests/integration/short-callbacks-for-session-buttons/`), and
  rewriting them to fit would have been the wrong way round.
"""

from __future__ import annotations

import ast
import pathlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.state import Status

NAME = "claude-x"
CHAT_ID = -1001
BUSY_CARD = 900


@pytest.fixture
def running(mk_bot, monkeypatch):
    """A session mid-turn, busy card 900, two messages queued behind it."""
    bot = mk_bot()
    bot._app.bot = AsyncMock()
    bot._mark_driver = MagicMock()
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock(return_value=True)
    keys: list[str] = []
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr(
        "aipager.dtach.inject.send_keys",
        AsyncMock(side_effect=lambda n, k: keys.append(k) or True))
    sess = bot.registry.get_or_create(NAME)
    sess.label = "x"
    bot.registry.transition(NAME, Status.BUSY)
    sess.busy_msg_id = BUSY_CARD
    sess.queue_prompt("queued one", 11)
    sess.queue_prompt("queued two", 12)
    return bot, sess, keys


def _tap_stop(bot, run_async, *, message_id):
    """Tap the Stop button living on `message_id`."""
    answers: list[str] = []
    bot._safe_answer = AsyncMock(
        side_effect=lambda q, text="", **kw: answers.append(text))
    query = MagicMock()
    query.data = f"{NAME}:stop"
    query.message = MagicMock(text="card", message_id=message_id)
    query.message.chat = MagicMock(id=CHAT_ID)
    query.message.edit_text = AsyncMock()
    query.from_user = MagicMock(id=1)
    query.answer = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    update.effective_chat = MagicMock(id=CHAT_ID)
    update.effective_user = MagicMock(id=1)
    run_async(bot._handle_callback(update, MagicMock()))
    return answers


# ── the defect ─────────────────────────────────────────────────────────

def test_a_stop_button_from_an_earlier_turn_does_not_stop_the_running_one(
        running, run_async):
    """The reported defect: an old button killed current work."""
    bot, sess, keys = running

    _tap_stop(bot, run_async, message_id=BUSY_CARD - 200)

    assert sess.status is Status.BUSY, "an old button stopped the running turn"
    assert len(sess.pending_queue) == 2, "an old button discarded the queue"
    assert keys == [], "Escape reached the terminal from a stale button"


def test_a_stale_tap_says_something(running, run_async):
    """Silence is the worst outcome — the operator tapped Stop and needs to
    know why nothing happened."""
    bot, _sess, _keys = running

    answers = _tap_stop(bot, run_async, message_id=BUSY_CARD - 200)

    assert answers, "a stale tap produced no feedback at all"
    assert "finished" in answers[-1].lower() or "current" in answers[-1].lower(), (
        f"feedback does not explain the refusal: {answers[-1]!r}")


# ── the live path must be untouched ────────────────────────────────────

def test_the_running_turns_own_button_still_stops_it(running, run_async):
    """The whole point is that this stays exactly as reliable as before."""
    bot, sess, keys = running

    _tap_stop(bot, run_async, message_id=BUSY_CARD)

    assert sess.status is Status.IDLE
    assert keys == ["Escape", "Escape"]
    assert sess.pending_queue == [], "stopping still discards the queue, as before"


@pytest.mark.parametrize("offset", [1, 5, 40])
def test_stop_on_a_card_raised_later_in_the_same_turn_still_works(
        running, run_async, offset):
    """Stop also appears on permission and AskUserQuestion cards. Those are
    sent *after* the busy card, in the same turn, so they must still stop —
    this is exactly what an equality check against `busy_msg_id` would have
    got wrong."""
    bot, sess, keys = running

    _tap_stop(bot, run_async, message_id=BUSY_CARD + offset)

    assert sess.status is Status.IDLE, (
        "a permission/ask card's Stop was refused as stale")
    assert keys == ["Escape", "Escape"]


# ── nothing to compare against: fail open, never silent ────────────────

@pytest.mark.parametrize("busy_card", [None, 0, -1])
def test_an_unverifiable_tap_still_acts(running, run_async, busy_card):
    """No busy card, or the -1 sentinel while one is being created.

    Fails OPEN deliberately. The project's documented promise is that a
    button already in chat history fires or explains itself, never nothing
    (`tests/integration/short-callbacks-for-session-buttons/`), and a
    refusal we cannot justify is worse than a stop the operator asked for.
    """
    bot, sess, keys = running
    sess.busy_msg_id = busy_card

    _tap_stop(bot, run_async, message_id=BUSY_CARD - 200)

    assert sess.status is Status.IDLE
    assert keys == ["Escape", "Escape"]


def test_the_predicate_is_pure_and_ordering_based(mk_bot):
    """The rule itself, independent of the callback machinery."""
    sess = mk_bot().registry.get_or_create(NAME)
    sess.busy_msg_id = 900

    assert sess.tap_is_for_this_turn(900) is True     # the card itself
    assert sess.tap_is_for_this_turn(901) is True     # raised later
    assert sess.tap_is_for_this_turn(899) is False    # earlier turn
    assert sess.tap_is_for_this_turn(None) is True    # unverifiable
    sess.busy_msg_id = None
    assert sess.tap_is_for_this_turn(1) is True       # unverifiable


# ── guard ──────────────────────────────────────────────────────────────
#
# This guard has been wrong twice, in the same way each time: it claimed to
# cover more than it scanned. Review found `perms_stop_switch` when it only
# knew the Stop verb, then `restart-confirm` when it only scanned
# `callbacks.py`. The count of destructive button paths went 1 -> 2 -> 3 -> 4.
#
# So it now scans every module under `aipager/bot/`, and the rule is:
#
#   a function reachable from a BUTTON that calls anything which
#   interrupts, kills or relaunches a session must have the turn check
#   dominate that call.
#
# "Reachable from a button" is structural, not a name list: the function
# either references `query` (the callback surface) or takes a
# `tapped_msg_id` seam threaded from one. Freshly typed commands — `/stop`,
# `/perms`, `/restart` — are excluded by that rule rather than by name,
# because a command the operator just typed cannot be stale.

# The raw primitives only. `_stop_session` is deliberately absent: it is a
# seam that performs the check itself, so its callers do not repeat it — and
# the guard still verifies the check dominates the primitive INSIDE it.
_DESTRUCTIVE = ("_stop_session_core", "_perms_switch_core",
                "_kill_and_relaunch_core", "_restart_session_core")
_CHECK = "tap_is_for_this_turn"


def _calls_named(nodes, name):
    return any(isinstance(n, ast.Call) and (
        (isinstance(n.func, ast.Attribute) and n.func.attr == name)
        or (isinstance(n.func, ast.Name) and n.func.id == name))
        for n in nodes)


def _own_nodes(fn):
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                stack.append(child)


def _gate_dominates(fn, call):
    """True if the turn check is guaranteed to run before `call`.

    Deliberately strict: the check must appear in the **test of an `if`**
    that precedes the call, or of one the call sits inside. A call nested
    in a `for`, `while` or `try` body is NOT accepted as dominating, even
    though `ast.walk` would find it there — review demonstrated that a
    check inside a possibly-empty loop satisfied the looser version while
    providing no protection at runtime.

    The cost is that `ok = check(...)` followed by `if not ok: return`
    would be rejected. That is the safe direction: a false alarm is
    visible, a false pass is not.
    """
    parent = {}
    for node in ast.walk(fn):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    tests = []
    node = call
    while node is not fn:
        up = parent.get(node)
        if up is None:
            break
        for _field, value in ast.iter_fields(up):
            if isinstance(value, list) and node in value:
                tests += [st.test for st in value[:value.index(node)]
                          if isinstance(st, ast.If)]
        if isinstance(up, ast.If) and node in up.body + up.orelse:
            tests.append(up.test)
        node = up

    return any(_calls_named(list(ast.walk(t)), _CHECK) for t in tests)


def _button_reachable(fn):
    """A function the operator can reach by TAPPING something.

    Either it handles a callback query directly, or it is a seam threaded
    with the tapped message id from one. A command handler has neither.
    """
    if any(a.arg == "tapped_msg_id"
           for a in list(fn.args.args) + list(fn.args.kwonlyargs)):
        return True
    return any(isinstance(n, ast.Name) and n.id == "query"
               for n in ast.walk(fn))


def _destructive_button_paths():
    """`(location, check_dominates)` for every button-reachable call that
    can interrupt, kill or relaunch a running session."""
    import aipager

    root = pathlib.Path(aipager.__file__).parent
    found = []
    for path in sorted((root / "bot").glob("*.py")):
        tree = ast.parse(path.read_text(), str(path))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _button_reachable(fn):
                continue
            for node in _own_nodes(fn):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in _DESTRUCTIVE):
                    continue
                found.append(
                    (f"bot/{path.name}:{node.lineno} {node.func.attr}()",
                     _gate_dominates(fn, node)))
    return found


def test_the_destructive_path_detector_still_matches_real_code():
    """Pins the guard against passing vacuously — a matcher that stops
    matching reports zero violations, which looks exactly like success."""
    paths = _destructive_button_paths()
    assert len(paths) >= 4, (
        "detector found fewer than the four known destructive button paths "
        f"(stop, perms_stop_switch, perms_confirm, restart-confirm) — the "
        f"matcher is stale: {paths}")
    files = {loc.split(":")[0] for loc, _ in paths}
    assert len(files) >= 2, (
        f"detector only sees one module again; it has narrowed: {files}")


def test_every_destructive_button_checks_the_turn_first():
    """The property, per-branch, across every module under aipager/bot."""
    missing = [loc for loc, ok in _destructive_button_paths() if not ok]
    assert not missing, (
        "these interrupt, kill or relaunch a running turn from a button "
        "without first checking the tap belongs to that turn, so a button "
        "from an earlier turn would act on current work:\n  "
        + "\n  ".join(missing))


def test_the_dominance_check_rejects_a_conditionally_run_guard():
    """The guard's own soundness, found by review.

    A check nested in a possibly-empty loop was accepted by the looser
    version while providing no runtime protection at all.
    """
    src = ("async def f(self, query, sess):\n"
           "    for _ in maybe_empty:\n"
           "        if not sess.tap_is_for_this_turn(1):\n"
           "            return\n"
           "    await self._stop_session(sess)\n")
    fn = ast.parse(src).body[0]
    call = next(n for n in ast.walk(fn)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "_stop_session")
    assert _gate_dominates(fn, call) is False, (
        "a guard that may never run was accepted as dominating")

    ok_src = ("async def f(self, query, sess):\n"
              "    if not sess.tap_is_for_this_turn(1):\n"
              "        return\n"
              "    await self._stop_session(sess)\n")
    ok_fn = ast.parse(ok_src).body[0]
    ok_call = next(n for n in ast.walk(ok_fn)
                   if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "_stop_session")
    assert _gate_dominates(ok_fn, ok_call) is True, (
        "the real pattern every call site uses was rejected")


def test_a_stale_restart_confirm_does_not_kill_the_running_turn(
        running, run_async, monkeypatch):
    """Found by review, after the guard had already been widened once.

    The ⋮ menu's Restart confirm card hard-kills and relaunches — without
    even the Ctrl-C courtesy the perms path gives — and that module's own
    docstring notes such a card "can sit on screen for days". The only
    re-check between offering it and acting on it was authorization.
    """
    from aipager.bot import session_parity

    bot, sess, _keys = running
    restarted = AsyncMock()
    bot._restart_session_core = restarted
    bot._can_prompt_user = MagicMock(return_value=True)
    answers: list[str] = []
    bot._safe_answer = AsyncMock(
        side_effect=lambda q, text="", **kw: answers.append(text))

    query = MagicMock()
    query.message = MagicMock(text="menu", message_id=BUSY_CARD - 500)
    query.message.chat = MagicMock(id=CHAT_ID)
    query.from_user = MagicMock(id=1)
    update = MagicMock()
    update.callback_query = query
    update.effective_chat = MagicMock(id=CHAT_ID)
    update.effective_user = MagicMock(id=1)
    monkeypatch.setattr(session_parity, "_resolve_pref_index",
                        lambda *a, **kw: sess)

    run_async(session_parity.handle_callback(
        bot, update, query, sess.name, "restart-confirm"))

    restarted.assert_not_awaited()
    assert answers, "a stale restart tap produced no feedback"


# ── the transitive guard ───────────────────────────────────────────────
#
# The guard above is function-local, and review proved that is not enough:
# `_kill_session_by_label` takes `source`/`target_label`, references neither
# `query` nor `tapped_msg_id`, and is one hop from the dispatcher — so a
# destructive path routed through it is invisible by construction, no
# matter which names are in `_DESTRUCTIVE`.
#
# That blindness is why the count of vulnerable sites went 1 -> 2 -> 3 -> 4
# -> 6 across three review rounds: each round widened the matcher against
# whichever counter-example had just surfaced, and the next hop hid the
# next one.
#
# This check follows the call graph instead. It enumerates every
# `action == "<verb>"` branch in the bot, computes the transitive closure
# of what that branch calls, and requires: if the closure reaches a
# primitive that destroys or interrupts a live session, it must also reach
# the turn check.
#
# Known limit, stated rather than implied: this asserts the check is
# PRESENT in the closure, not that it DOMINATES the primitive.
# `test_every_destructive_button_checks_the_turn_first` above still carries
# ordering for the direct call sites. Presence is what catches the wrapper
# class; ordering is what catches a check that never runs. Neither
# subsumes the other, so both are kept.

_PRIMITIVES = frozenset({
    "kill_session", "_kill_session_core", "_stop_session_core",
    "_kill_and_relaunch_core", "_perms_switch_core", "_restart_session_core",
})


def _call_graph():
    """`name -> set of names it calls`, across the whole package."""
    import aipager

    root = pathlib.Path(aipager.__file__).parent
    graph: dict[str, set[str]] = {}
    for path in sorted(root.rglob("*.py")):
        for fn in ast.walk(ast.parse(path.read_text(), str(path))):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            edges = graph.setdefault(fn.name, set())
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (func.attr if isinstance(func, ast.Attribute)
                        else func.id if isinstance(func, ast.Name) else None)
                if name:
                    edges.add(name)
    return graph


def _closure(graph, start, seen=None):
    seen = seen if seen is not None else set()
    if start in seen:
        return seen
    seen.add(start)
    for callee in graph.get(start, ()):
        _closure(graph, callee, seen)
    return seen


def _destructive_button_verbs():
    """`(verb, primitive, protected, site)` for every callback verb whose
    call graph reaches something that destroys a live session."""
    import aipager

    root = pathlib.Path(aipager.__file__).parent
    graph = _call_graph()
    rows = []
    for path in sorted((root / "bot").glob("*.py")):
        tree = ast.parse(path.read_text(), str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.If)
                    and isinstance(node.test, ast.Compare)
                    and isinstance(node.test.left, ast.Name)
                    and node.test.left.id == "action"
                    and node.test.comparators
                    and isinstance(node.test.comparators[0], ast.Constant)):
                continue
            verb = node.test.comparators[0].value
            if not isinstance(verb, str):
                continue
            reached: set[str] = set()
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                func = inner.func
                name = (func.attr if isinstance(func, ast.Attribute)
                        else func.id if isinstance(func, ast.Name) else None)
                if name:
                    reached |= _closure(graph, name)
            hit = sorted(reached & _PRIMITIVES)
            if hit:
                rows.append((verb, hit[0], _CHECK in reached,
                             f"bot/{path.name}:{node.lineno}"))
    return rows


def test_the_transitive_detector_still_matches_real_code():
    """Pins it against passing vacuously, and against narrowing back to one
    module the way its predecessor did."""
    rows = _destructive_button_verbs()
    assert len(rows) >= 7, (
        "detector found fewer than the seven known destructive button verbs "
        f"— the matcher is stale: {[r[0] for r in rows]}")
    assert len({r[3].split(':')[0] for r in rows}) >= 2, (
        "detector only sees one module again; it has narrowed")


def test_no_button_can_destroy_a_session_without_checking_the_turn():
    """The property, over the call graph rather than one function's body."""
    unprotected = [f"{verb} ({site}) reaches {prim}"
                   for verb, prim, ok, site in _destructive_button_verbs()
                   if not ok]
    assert not unprotected, (
        "these callback verbs can destroy or interrupt a live session, but "
        "nothing on the way there checks the tap belongs to the running "
        "turn — so a button from an earlier turn would act on current "
        "work:\n  " + "\n  ".join(unprotected))
