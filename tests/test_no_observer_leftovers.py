"""Observer bots are gone, and nothing of them is left behind.

The feature (a second Telegram bot that got copies of the short notices
from every chat on the install, set through ``OBSERVER_BOTS``) was
removed; to let someone just watch, add them to a group with the
``read_only`` role. What may still say "observer" in the package is a
short, exact list: the start-up warning for a still-set variable, the
retired feature id old problem reports carry, and the read_only role's
note in SESSION.md (a different sense of the word).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import aipager.config
from tests import i18n_tool

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "aipager"

#: (path relative to the repo, stripped line) for every line of the package
#: allowed to contain "observer" in any case.
ALLOWED = {
    ("aipager/cli/daemon.py",
     'if os.environ.get("OBSERVER_BOTS", "").strip():'),
    ("aipager/cli/daemon.py",
     'log.warning("OBSERVER_BOTS is set but observer bots were removed; "'),
    ("aipager/report/schema.py",
     'FEATURES = ("miniapp", "tunnel_managed", "tunnel_override", "observers", "voice",'),
    ("aipager/session_store.py",
     'note = "observer; cannot drive prompts"'),
}


def _package_lines_naming_observer() -> set[tuple[str, str]]:
    found = set()
    for path in sorted(PACKAGE.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # a binary file
        rel = path.relative_to(ROOT).as_posix()
        for line in text.splitlines():
            if "observer" in line.casefold():
                found.add((rel, line.strip()))
    return found


def test_observer_appears_in_the_package_only_where_allowed():
    assert _package_lines_naming_observer() == ALLOWED


def test_every_allowed_line_is_still_there():
    """The allowlist cannot rot: an entry whose line changed or went away
    must leave the list too."""
    found = _package_lines_naming_observer()
    assert [entry for entry in sorted(ALLOWED) if entry not in found] == []


def test_the_observer_module_and_setting_are_gone():
    assert not (PACKAGE / "bot" / "observer.py").exists()
    assert importlib.util.find_spec("aipager.bot.observer") is None
    assert not hasattr(aipager.config, "OBSERVER_BOTS")
    assert not hasattr(aipager.config, "_parse_observer_bots")


def test_no_doc_links_to_the_deleted_page():
    """docs/observers.md and its translations are gone, and nothing links
    to them (the English pages have no link checker of their own)."""
    pages = [ROOT / "README.md", ROOT / "CHANGELOG.md",
             ROOT / "tools" / "i18n" / "TRANSLATING.md",
             *sorted((ROOT / "docs").rglob("*.md"))]
    assert [p.relative_to(ROOT).as_posix() for p in pages
            if "observers.md" in p.read_text(encoding="utf-8")] == []
    assert not (ROOT / "docs" / "observers.md").exists()
    assert sorted((ROOT / "docs" / "i18n").glob("*/observers.md")) == []
    assert "observers.md" not in i18n_tool.load().DOC_PAGES
