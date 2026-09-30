"""No em dash in text aipager shows a user (roadmap 8.54).

Operator rule 2026-09-25: a plain "-" and parentheses, never an em dash.

What is scanned: every string literal in the ``aipager`` package (plain
strings and the literal parts of f-strings), found through the AST so
comments are never seen. Two kinds of literal are skipped because no user
reads them as written:

* docstrings and other bare-expression strings;
* anything inside a logging call (``log.*``, ``logger.*``, ``logging.*``).

Everything else is treated as user-facing: Telegram sends/edits, toasts,
button and keyboard labels, cards, command descriptions, Mini App server
text and page source, CLI output, and error messages that reach a user.
The few literals that keep an em dash on purpose are in ``ALLOWED`` below,
by file and a fragment, each with its reason. A fragment matches the
literal's text or the source line the literal starts on, so a bare
separator such as " — " is pinned by the code around it rather than
exempting every such literal in the file. A stale entry (one that no
longer matches anything) fails too, so the list cannot rot.

Not covered here: a ``\u2014`` escape inside the raw ``APP_JS`` string
(the Mini App page); the page's own rule tests check the rendered page.
"""

from __future__ import annotations

import ast
from pathlib import Path

import aipager

EM = "—"
PKG = Path(aipager.__file__).parent

_LOG_NAMES = {"log", "logger", "_log", "_logger", "LOG", "logging"}

# path (relative to the package) -> fragments of literals that may keep an
# em dash. Nothing here reaches a user as aipager's own wording.
ALLOWED: dict[str, tuple[str, ...]] = {
    "bot/handlers.py": (
        # Built into a variable, then only ever logged.
        "routed by last_active fallback",
    ),
    # Log suffix built before the log call.
    "bot/lifecycle.py": ("bot blocked — stopping retries",),
    # Reply-context text injected into Claude's prompt.
    "bot/session_ops.py": (
        "message was queued while busy",
        "pointing at it for reference",
        "no text content is available to show",
        "pointing at that specific passage",
    ),
    # Style instructions sent to Claude with a prompt.
    "preferences.py": (
        "Keep the answer short",
        "Keep the answer to a medium length",
        "Use plain, professional language",
    ),
    # The session's notes file, written for Claude to read.
    "session_store.py": ("({member.role} — {note})", "respond naturally"),
    # Internal invariant (a programming error), never shown to a user.
    "claude_bootstrap.py": ("must carry a PendingAuthCheck",),
    # A comment in the systemd unit template: installs are diffed against it,
    # so editing it would report every installed unit as outdated.
    "service.py": ("not just for finding claude —",),
}


def _is_log_call(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    owner = func.value
    if isinstance(owner, ast.Name):
        return owner.id in _LOG_NAMES
    return isinstance(owner, ast.Attribute) and owner.attr in _LOG_NAMES


def em_dash_literals(source: str) -> list[tuple[int, str, str]]:
    """(line, text, source line) of every user-facing literal in
    ``source`` carrying an em dash, per the module docstring's definition."""
    tree = ast.parse(source)
    skipped: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(
                node.value, (ast.Constant, ast.JoinedStr)):
            skipped.update(id(n) for n in ast.walk(node.value))
        elif isinstance(node, ast.Call) and _is_log_call(node):
            skipped.update(id(n) for n in ast.walk(node))
    lines = source.splitlines()
    return [
        (node.lineno, node.value, lines[node.lineno - 1])
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and EM in node.value and id(node) not in skipped
    ]


def _scan() -> dict[str, list[tuple[int, str, str]]]:
    found: dict[str, list[tuple[int, str, str]]] = {}
    for path in sorted(PKG.rglob("*.py")):
        hits = em_dash_literals(path.read_text(encoding="utf-8"))
        if hits:
            found[path.relative_to(PKG).as_posix()] = hits
    return found


def test_no_em_dash_in_user_facing_text():
    offenders = [
        f"aipager/{rel}:{line}: {text!r}"
        for rel, hits in _scan().items()
        for line, text, src in hits
        if not any(frag in text or frag in src for frag in ALLOWED.get(rel, ()))
    ]
    assert not offenders, (
        "an em dash in user-facing text; use a plain '-' or parentheses "
        "(roadmap 8.54):\n" + "\n".join(offenders))


def test_every_allow_list_entry_still_matches():
    found = _scan()
    stale = [
        f"{rel}: {frag!r}"
        for rel, frags in ALLOWED.items()
        for frag in frags
        if not any(frag in text or frag in src
                   for _line, text, src in found.get(rel, ()))
    ]
    assert not stale, "ALLOWED entries that match nothing:\n" + "\n".join(stale)


def test_the_scan_sees_user_text_and_ignores_comments_docstrings_and_logs():
    """The definition itself: a toast, a label and an f-string part are
    caught; a comment, a docstring and a log line are not."""
    source = (
        '"""Module doc — fine."""\n'
        "# a comment — fine\n"
        "def f(q, log, x):\n"
        '    """Doc — fine."""\n'
        '    log.info("logged — fine %s", x)\n'
        '    q.answer("Busy — try again")\n'
        '    label = f"{x} — 5m ago"\n'
        '    return {"k": "Off — busy card only"}\n'
    )
    assert [hit[0] for hit in em_dash_literals(source)] == [6, 7, 8]
