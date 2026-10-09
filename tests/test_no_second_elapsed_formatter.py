"""One clock: no module formats elapsed time by hand any more.

Every elapsed surface (the busy card, agent and shell rows, Finished,
/status, the Mini App) goes through ``aipager/clockfmt.py``. This scans
for the shape a hand-rolled formatter always has: an f-string that puts a
``// 60`` (or ``% 60``, ``// 3600``...) value right before a unit letter,
``f"{secs // 60}m {secs % 60}s"``. Relative ages ("5m ago"), countdowns
("2h 5m left"), prose ("in the last 10 minutes") and the /update heartbeat
("(2 min 15s)") are not elapsed clocks: an f-string that says "ago",
"left" or "minutes" is skipped, and the helpers that build an age or the
heartbeat without saying so are listed.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "aipager"

#: (file, enclosing function): helpers that build a relative age or the
#: heartbeat, whose "ago" is added by their caller. Not elapsed clocks.
ALLOWED = {
    ("aipager/miniapp/sessions.py", "_short_duration"),  # "Last active 2m ago"
    ("aipager/bot/update_flow.py", "_elapsed_label"),   # "(2 min 15s)", by ruling
}
_NOT_A_CLOCK = (" ago", " left", "minutes")

_UNIT_AFTER = ("m", "s", "h", "d", " min")


def _has_division(node) -> bool:
    for sub in ast.walk(node):
        if (isinstance(sub, ast.BinOp) and isinstance(sub.op, (ast.FloorDiv, ast.Mod))
                and isinstance(sub.right, ast.Constant)
                and sub.right.value in (60, 3600, 86400)):
            return True
    return False


def hand_rolled(source: str, rel: str) -> list[tuple[str, str, int]]:
    tree = ast.parse(source)
    hits = []
    parents: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                parents.setdefault(id(sub), node.name)
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        words = "".join(v.value for v in node.values
                        if isinstance(v, ast.Constant) and isinstance(v.value, str))
        if any(w in words for w in _NOT_A_CLOCK):
            continue
        values = node.values
        for i, part in enumerate(values[:-1]):
            nxt = values[i + 1]
            if (isinstance(part, ast.FormattedValue) and _has_division(part.value)
                    and isinstance(nxt, ast.Constant) and isinstance(nxt.value, str)
                    and nxt.value.startswith(_UNIT_AFTER)):
                hits.append((rel, parents.get(id(node), "<module>"), node.lineno))
    return hits


def scan(package: Path = PACKAGE) -> list[tuple[str, str, int]]:
    out = []
    for path in sorted(package.rglob("*.py")):
        rel = path.relative_to(package.parent).as_posix()
        out += hand_rolled(path.read_text(encoding="utf-8"), rel)
    return out


def test_no_second_elapsed_formatter():
    bad = [h for h in scan() if (h[0], h[1]) not in ALLOWED]
    assert not bad, f"hand-rolled elapsed formatters (use aipager.clockfmt): {bad}"


def test_every_allow_list_entry_still_matches():
    found = {(h[0], h[1]) for h in scan()}
    assert ALLOWED <= found, ALLOWED - found


def test_the_scan_sees_the_old_shape():
    src = 'def f(s):\n    return f"{s // 60}m {s % 60}s"\n'
    assert set(hand_rolled(src, "x.py")) == {("x.py", "f", 2)}
    assert hand_rolled('def f(s):\n    return f"{s}s"\n', "x.py") == []
