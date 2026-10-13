"""No leftovers in the tree: the package names "observer" only on the four
allowed lines, the Mini App label key is gone from every catalog, the
docs page and its translations are gone with every link to them, and
the CHANGELOG says what replaced the feature.

design.md success criteria 6, 7, 8 and the CHANGELOG criterion;
entrypoints.md "Side effects".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests import i18n_tool

ROOT = Path(__file__).resolve().parents[3]
PKG = ROOT / "aipager"
LOCALE_CODES = ("ar", "de", "es", "fa", "fr", "id", "it", "ko", "pt-BR", "ru",
                "tr", "uk", "vi", "zh-Hans")
FEATURE_KEY = "ui.report.feature.observers"


def _observer_lines() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(PKG.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line in text.splitlines():
            if "observer" in line.casefold():
                rel = path.relative_to(ROOT).as_posix()
                found.setdefault(rel, []).append(line.strip())
    return found


# ---- the package sweep -------------------------------------------------------

def test_observer_appears_only_in_the_three_allowed_files():
    assert set(_observer_lines()) == {
        "aipager/cli/daemon.py", "aipager/report/schema.py",
        "aipager/session_store.py"}


def test_the_daemon_names_it_on_exactly_two_lines():
    assert len(_observer_lines().get("aipager/cli/daemon.py", [])) == 2


def test_the_schema_names_it_on_exactly_one_line():
    assert len(_observer_lines().get("aipager/report/schema.py", [])) == 1


def test_session_store_keeps_only_the_read_only_note():
    assert _observer_lines().get("aipager/session_store.py") == [
        'note = "observer; cannot drive prompts"']


def test_no_observer_file_remains_in_the_package():
    assert [p.relative_to(ROOT).as_posix() for p in PKG.rglob("*observer*")
            if "__pycache__" not in p.parts] == []


# ---- the Mini App label key --------------------------------------------------

def _catalog_ids():
    return ["source"] + list(LOCALE_CODES)


def _catalog_path(name: str) -> Path:
    if name == "source":
        return ROOT / "tools" / "i18n" / "source.jsonl"
    return PKG / "i18n" / "locales" / f"{name}.jsonl"


@pytest.mark.parametrize("name", _catalog_ids(), ids=_catalog_ids())
def test_the_catalog_exists(name):
    assert _catalog_path(name).is_file()


@pytest.mark.parametrize("name", _catalog_ids(), ids=_catalog_ids())
def test_the_feature_label_key_is_absent_from_the_catalog(name):
    assert FEATURE_KEY not in _catalog_path(name).read_text(encoding="utf-8")


def test_the_catalog_set_is_the_fourteen_languages():
    """Boundary: no fifteenth catalog was left behind or added."""
    assert sorted(p.stem for p in (PKG / "i18n" / "locales").glob("*.jsonl")) \
        == sorted(LOCALE_CODES)


# ---- docs --------------------------------------------------------------------

def _doc_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "CHANGELOG.md",
             ROOT / "tools" / "i18n" / "TRANSLATING.md"]
    files += sorted((ROOT / "docs").rglob("*.md"))
    return files


def test_no_doc_links_to_the_deleted_page():
    assert [p.relative_to(ROOT).as_posix() for p in _doc_files()
            if "observers.md" in p.read_text(encoding="utf-8")] == []


def test_the_english_page_is_gone():
    assert not (ROOT / "docs" / "observers.md").exists()


@pytest.mark.parametrize("code", LOCALE_CODES, ids=list(LOCALE_CODES))
def test_the_translated_page_is_gone(code):
    assert not (ROOT / "docs" / "i18n" / code / "observers.md").exists()


def test_no_docs_folder_holds_any_observers_page():
    assert [p.relative_to(ROOT).as_posix()
            for p in (ROOT / "docs").rglob("observers*")] == []


def test_the_doc_page_table_has_no_entry():
    assert "observers.md" not in i18n_tool.load().DOC_PAGES


def test_the_docs_check_is_clean():
    assert i18n_tool.load().check_docs() == []


def test_the_readme_no_longer_mentions_observer_bots():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert re.search(r"observer[ _-]?bots?|OBSERVER_BOTS", text, re.I) is None


def test_the_env_example_no_longer_mentions_observers():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "observer" not in text.casefold()


def test_groups_doc_still_has_the_roles_anchor_the_changelog_links():
    text = (ROOT / "docs" / "groups.md").read_text(encoding="utf-8")
    assert re.search(r'^#+ .*Roles|id="roles"', text, re.M | re.I) is not None


def test_groups_doc_still_describes_the_read_only_role():
    text = (ROOT / "docs" / "groups.md").read_text(encoding="utf-8")
    assert "read_only" in text


# ---- CHANGELOG -----------------------------------------------------------------

def _unreleased() -> str:
    """The release the removal shipped in (it was [Unreleased])."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    start = text.index("## [1.2.0]")
    end = text.index("\n## [", start + 1)
    return text[start:end]


def _removed_section() -> str:
    block = _unreleased()
    start = block.index("### Removed")
    nxt = block.find("\n### ", start + 1)
    return block[start:] if nxt == -1 else block[start:nxt]


def test_unreleased_has_a_removed_section():
    assert "\n### Removed\n" in _unreleased()


def test_removed_comes_before_fixed():
    block = _unreleased()
    assert "### Fixed" not in block or block.index("### Removed") < block.index("### Fixed")


def test_removed_names_the_variable():
    assert "OBSERVER_BOTS" in _removed_section()


def test_removed_points_to_the_read_only_role():
    assert "read_only" in _removed_section()


def test_removed_links_the_groups_roles_section():
    assert "docs/groups.md#roles" in _removed_section()


def test_removed_says_a_still_set_value_is_ignored_with_a_warning():
    text = _removed_section().casefold()
    assert "ignore" in text and "warning" in text


def test_removed_has_no_em_dash():
    assert "—" not in _removed_section()
