"""The permission mode's display labels go through keys (the
multilingual design: mode names are translated where shown, typed and
stored values stay English)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from aipager.bot import mode_labels

PACKAGE = Path(__file__).resolve().parent.parent / "aipager"
LITERALS = ("\U0001f916 Auto", "\U0001f4ac Ask", "\U0001f513 Full")


def test_labels_are_today_s_english():
    # The shown labels name the mode as a mode (operator, 2026-10-10); the
    # words typed after /mode stay the single words they were.
    assert mode_labels.MODE_AUTO == "🤖 Auto mode"
    assert mode_labels.MODE_ASK == "💬 Ask mode"
    assert mode_labels.WORD_AUTO == "Auto"
    assert mode_labels.WORD_ASK == "Ask"
    assert mode_labels.MODE_ASK_INSTEAD == "💬 Ask mode instead"
    assert mode_labels.MODE_AUTO_INSTEAD == "🤖 Auto mode instead"
    assert mode_labels.MODE_FULL == "🔓 Full access mode"
    assert mode_labels.WORD_FULL == "Full"
    assert mode_labels.MODE_FULL_INSTEAD == "🔓 Full access mode instead"
    assert mode_labels.MODE_AUTO_FOR_YOU == "🤖 Auto for you (🔓 Full access is for admins)"
    assert mode_labels.MODE_ASK_NO_AUTO == "💬 Ask mode (Auto isn't available here)"
    assert mode_labels.MODE_OTHER == "⚙️ Claude Code's {name} mode"
    assert mode_labels.mode_label("auto") is mode_labels.MODE_AUTO
    assert mode_labels.mode_label("ask") is mode_labels.MODE_ASK
    assert mode_labels.mode_label("full") is mode_labels.MODE_FULL
    assert mode_labels.mode_label("ask_no_auto") is mode_labels.MODE_ASK_NO_AUTO


@pytest.mark.parametrize("stale", [True, False], ids=["true", "false"])
def test_a_caller_still_passing_skip_perms_fails_loudly(stale):
    """The old boolean is not a mode: never guessed into a label."""
    with pytest.raises(TypeError):
        mode_labels.mode_label(stale)


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
