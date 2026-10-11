"""conftest pins every import-time copy of the configured chat.

A module that does ``from aipager.config import CHAT_ID`` at module level
keeps the value it saw at import: the real chat on a developer's box (from
its config or ``.env``), ``""`` on a CI runner. conftest pins
``aipager.config.CHAT_ID`` and, through ``CHAT_ID_COPIES``, each such copy;
a copy it misses makes tests pass here by accident and fail on CI (the
multilingual branch's parity run found 42 of them, 2026-10-10).
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.conftest import CHAT_ID_COPIES

PACKAGE = Path(__file__).resolve().parent.parent / "aipager"


def _module_level_copies() -> set[str]:
    found = set()
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if (isinstance(node, ast.ImportFrom) and node.module == "aipager.config"
                    and any(alias.name == "CHAT_ID" for alias in node.names)):
                rel = path.relative_to(PACKAGE.parent).with_suffix("")
                found.add(".".join(rel.parts))
    return found


def test_every_import_time_copy_is_pinned():
    assert _module_level_copies() == set(CHAT_ID_COPIES)


def test_the_copies_read_the_pin_during_a_test():
    import importlib
    for name in CHAT_ID_COPIES:
        assert importlib.import_module(name).CHAT_ID == "256113222", name
