"""The permission mode's display labels go through keys (the
multilingual design: mode names are translated where shown, typed and
stored values stay English)."""

from __future__ import annotations

import ast
from pathlib import Path

from aipager.bot import mode_labels

PACKAGE = Path(__file__).resolve().parent.parent / "aipager"
LITERALS = ("\U0001f916 Auto", "\U0001f4ac Ask")


def test_labels_are_today_s_english():
    # The shown labels name the mode as a mode (operator, 2026-10-10); the
    # words typed after /mode stay the single words they were.
    assert mode_labels.MODE_AUTO == "🤖 Auto mode"
    assert mode_labels.MODE_ASK == "💬 Ask mode"
    assert mode_labels.WORD_AUTO == "Auto"
    assert mode_labels.WORD_ASK == "Ask"
    assert mode_labels.MODE_ASK_INSTEAD == "💬 Ask instead"
    assert mode_labels.MODE_AUTO_INSTEAD == "🤖 Auto instead"
    assert mode_labels.MODE_ASK_FOR_YOU == "💬 Ask for you (🤖 Auto is for admins)"
    assert mode_labels.mode_label(True) is mode_labels.MODE_AUTO
    assert mode_labels.mode_label(False) is mode_labels.MODE_ASK


def _docstrings(tree) -> set[int]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            out.add(id(node.value))
    return out


def literal_sites(package: Path = PACKAGE) -> list[str]:
    """Every string literal (f-string parts included) outside mode_labels
    and the Mini App's page script that spells a mode label."""
    hits = []
    for path in sorted(package.rglob("*.py")):
        rel = path.relative_to(package.parent).as_posix()
        if rel == "aipager/bot/mode_labels.py" or rel.startswith("aipager/miniapp/static/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = _docstrings(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in skip
                    and any(lit in node.value for lit in LITERALS)):
                hits.append(f"{rel}:{node.lineno}")
    return hits


def test_no_mode_label_literal_elsewhere():
    assert literal_sites() == []


def test_the_scan_sees_an_f_string_part(tmp_path):
    pkg = tmp_path / "aipager"
    pkg.mkdir()
    (pkg / "m.py").write_text(
        'def f(x):\n    """Says 🤖 Auto in a docstring."""\n'
        '    return f"{x} is 💬 Ask now"\n', encoding="utf-8")
    assert literal_sites(pkg) == ["aipager/m.py:3"]
