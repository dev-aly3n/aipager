"""Adopting a session by discovery must label it with the name typed.

`/<label>` and `/<label> <prompt>` both fall back to rebuilding the
internal name as `claude-{target_label}` and adopting whatever socket is
alive under it. Neither set `.label`, so the adopted entry wore whatever
`get_or_create` derived — or, since the kill-race fix, whatever label it
revived from the recently-removed table.

Reproduced: rename `uiop` to `production`, kill it, bring `claude-uiop`
back within the 60s window, then type `/uiop`. The session came back
labelled `production` — a name the operator had deliberately moved off.
It compounds, too: `find_by_label("uiop")` misses from then on, so every
later `/uiop` re-enters this same discovery fallback.

The label is only assigned for a name the registry did not already know.
That distinction is the whole fix, and getting it wrong is worse than the
bug: `/rename` moves the label while the internal name stays put, so the
pre-rename name goes on resolving to a live socket indefinitely. An
unconditional assignment therefore undoes a rename the moment the
operator types the old name — no kill and no race window needed. Both
directions are pinned below.

The derivation rule and the remembered-label table are unchanged; they
remain correct for `session_monitor` and `hook_receiver`, which discover
names nobody typed.
"""

from __future__ import annotations

import ast
import pathlib
from unittest.mock import AsyncMock

import pytest

from aipager.state import Status

INTERNAL = "claude-uiop"        # legacy unscoped form, as the CLI creates
TYPED = "uiop"                  # what the operator types
RENAMED = "production"          # what they renamed it to


def _renamed(bot, *, label=RENAMED):
    """A session renamed via /rename. The internal name never moves."""
    sess = bot.registry.get_or_create(INTERNAL)
    sess.label = label
    bot.registry.transition(INTERNAL, Status.IDLE)
    return sess


def _then_killed(bot, run_async, monkeypatch, *, label=RENAMED):
    """Leave the registry exactly as a rename-then-kill leaves it."""
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=False))
    bot._update_bot_commands = AsyncMock()
    run_async(bot._kill_session_core(INTERNAL, label))
    assert bot.registry.get(INTERNAL) is None, "kill should drop the entry"


def _socket_is_alive(bot, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()
    bot._inject_prompt = AsyncMock(return_value=True)
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()


def _type_it(bot, mk_update, run_async, drive, label=TYPED):
    """Drive whichever fallback path `drive` names."""
    update = mk_update(f"/{label}")
    if drive == "switch":
        run_async(bot._switch_session(update, label))
    else:
        run_async(bot._direct_send(update, label, "hello"))


BOTH_PATHS = pytest.mark.parametrize("drive", ["switch", "direct"])


# ── the reported defect ────────────────────────────────────────────────

@BOTH_PATHS
def test_a_revived_session_is_labelled_with_the_name_typed(
        mk_bot, mk_update, run_async, monkeypatch, steady_clock, drive):
    """`/uiop` must hand back a session called `uiop`, not the label the
    remembered-label table revived for that name."""
    bot = mk_bot()
    _renamed(bot)
    _then_killed(bot, run_async, monkeypatch)
    _socket_is_alive(bot, monkeypatch)

    _type_it(bot, mk_update, run_async, drive)

    assert bot.registry.get(INTERNAL).label == TYPED


def test_the_typed_name_stays_findable_afterwards(
        mk_bot, mk_update, run_async, monkeypatch, steady_clock):
    """The compounding half: a wrong label makes `find_by_label` miss
    forever, so every later `/uiop` repeats the discovery fallback."""
    bot = mk_bot()
    _renamed(bot)
    _then_killed(bot, run_async, monkeypatch)
    _socket_is_alive(bot, monkeypatch)

    _type_it(bot, mk_update, run_async, "switch")

    assert bot.registry.find_by_label(TYPED, -1001) is not None


# ── the reverse defect: do not undo a rename ───────────────────────────

@BOTH_PATHS
def test_typing_the_old_name_does_not_undo_a_live_rename(
        mk_bot, mk_update, run_async, monkeypatch, drive):
    """A tracked session's label is authoritative.

    `/rename` moves the label but not the internal name, so `claude-uiop`
    keeps resolving to a live socket forever. `find_by_label("uiop")`
    correctly misses — the label really did change — and execution
    reaches the discovery fallback with the session ALREADY tracked.
    Labelling it there would silently revert the rename, with no kill and
    no race window: just rename a session and later type its old name.
    """
    bot = mk_bot()
    _renamed(bot)
    _socket_is_alive(bot, monkeypatch)

    _type_it(bot, mk_update, run_async, drive)

    assert bot.registry.get(INTERNAL).label == RENAMED


@BOTH_PATHS
def test_a_live_rename_stays_findable_by_its_new_name(
        mk_bot, mk_update, run_async, monkeypatch, drive):
    """The compounding half of the reverse defect."""
    bot = mk_bot()
    _renamed(bot)
    _socket_is_alive(bot, monkeypatch)

    _type_it(bot, mk_update, run_async, drive)

    assert bot.registry.find_by_label(RENAMED, -1001) is not None


# ── the ordinary case must not move ────────────────────────────────────

@BOTH_PATHS
def test_a_session_never_renamed_is_adopted_exactly_as_before(
        mk_bot, mk_update, run_async, monkeypatch, drive):
    """With no rename, the label assigned is the one derivation already
    produced."""
    bot = mk_bot()
    _socket_is_alive(bot, monkeypatch)

    _type_it(bot, mk_update, run_async, drive)

    assert bot.registry.get(INTERNAL).label == TYPED
    assert bot.registry.last_active_session == INTERNAL


# ── structural guard ───────────────────────────────────────────────────
#
# Three separate passes have each converted a subset of this codebase's
# repeated call sites and left the rest (the callback-length count went
# 13 -> 23 -> 28). So this does not assert on the two sites the bug was
# found at; it derives the set from the source and requires the property
# of all of them.
#
# What it covers: a function that rebuilds a session name as an
# `f"claude-{...}"` literal in its own body and adopts an entry under it.
# Such a function must either assign `.label` itself or delegate to
# `_adopt_by_typed_name`, which carries the already-tracked check.
#
# What it does NOT cover, stated plainly rather than implied: a site that
# receives an already-built name as a parameter, or builds one with `+`
# or `.format()`, or gets it back from a helper, or builds the name here
# but hands the adoption itself to some other function. `callbacks.py`'s
# `new_replace` is the first kind — its name arrives decoded from
# callback data. `_kill_session_by_label` is the last kind, and is
# excluded correctly rather than by luck: `_kill_session_core` adopts
# nothing. Neither is reachable by this matcher, and neither is claimed
# to be guarded here. The pattern is deliberately narrow: `session_monitor` and
# `hook_receiver` also call `get_or_create` on names they did not build
# from a typed label, and they are RIGHT not to assign a label, so a
# broader matcher would flag correct code.

_SEAM = "_adopt_by_typed_name"


def _own_nodes(fn):
    """Walk `fn`'s body without descending into nested definitions.

    A nested helper's `get_or_create` would otherwise be attributed to
    the enclosing function, which could mark an unrelated function as an
    adoption site (or launder a real one past the check).
    """
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                stack.append(child)


def _rebuilds_a_session_name(nodes):
    """An f-string literal of the form `claude-{...}` — a session name
    reconstructed from a variable rather than received as one."""
    for node in nodes:
        if not isinstance(node, ast.JoinedStr) or not node.values:
            continue
        head = node.values[0]
        if (isinstance(head, ast.Constant) and isinstance(head.value, str)
                and head.value.startswith("claude-")
                and any(isinstance(v, ast.FormattedValue)
                        for v in node.values)):
            return True
    return False


def _calls(nodes, name):
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == name for n in nodes)


def _assigns_label(nodes):
    return any(isinstance(t, ast.Attribute) and t.attr == "label"
               for n in nodes if isinstance(n, ast.Assign)
               for t in n.targets)


def _adoption_sites():
    """Every function that rebuilds a session name AND adopts an entry
    under it, as `(location, labels_it, delegates)`."""
    import aipager

    root = pathlib.Path(aipager.__file__).parent
    sites = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), str(path))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            nodes = list(_own_nodes(fn))
            if not _rebuilds_a_session_name(nodes):
                continue
            delegates = _calls(nodes, _SEAM)
            if not delegates and not _calls(nodes, "get_or_create"):
                continue
            loc = f"{path.relative_to(root.parent)}:{fn.lineno} {fn.name}()"
            sites.append((loc, _assigns_label(nodes), delegates))
    return sites


def test_the_adoption_site_detector_still_matches_real_code():
    """Pins the guard below against passing vacuously.

    An AST matcher that silently stops matching reports zero violations,
    which is indistinguishable from success. Both shapes it recognises
    must still be found in the tree: `create_session` has always assigned
    `.label` inline, and the two fallback paths delegate to the seam.
    """
    sites = _adoption_sites()
    assert sites, "detector matched nothing — the pattern it looks for moved"
    assert any("create_session" in loc
               for loc, labels, _ in sites if labels), (
        "no site assigns `.label` inline any more; the matcher is stale "
        f"— found: {[loc for loc, _, _ in sites]}")
    assert any(delegates for _, _, delegates in sites), (
        "no site delegates to the seam any more; the matcher is stale "
        f"— found: {[loc for loc, _, _ in sites]}")


def test_every_site_that_rebuilds_a_session_name_sets_its_label():
    """The property, over all sites rather than the two reported ones."""
    missing = [loc for loc, labels, delegates in _adoption_sites()
               if not (labels or delegates)]
    assert not missing, (
        "these rebuild a session name from a user-typed label and adopt an "
        "entry under it without assigning `.label` or going through "
        f"`{_SEAM}`, so the entry keeps a derived or revived name instead "
        "of the one typed:\n  " + "\n  ".join(missing))


def test_the_seam_skips_an_already_tracked_session(mk_bot):
    """The seam's own contract, independent of either call path."""
    bot = mk_bot()
    _renamed(bot)

    adopted = bot._adopt_by_typed_name(INTERNAL, TYPED)
    assert adopted.label == RENAMED, "must not relabel a tracked session"

    bot.registry.remove(INTERNAL)
    fresh = bot._adopt_by_typed_name(INTERNAL, TYPED)
    assert fresh.label == TYPED, "must label a session it just adopted"
