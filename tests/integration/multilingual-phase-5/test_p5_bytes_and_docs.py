"""Black-box: the hard requirements on bytes, and the docs (phase 5 items 6 and 7;
design.md "Success criteria").

- English output byte-identical for existing English chats: every entry of
  ``tests/golden/english_surfaces.json`` present at the phase base (6a11b78)
  is unchanged and only the three new surfaces are added;
  ``miniapp_page_english.json`` and ``miniapp_css_ltr.json`` are unchanged.
- Everything Claude reads byte-identical: ``claude_facing.json`` and
  ``safety_reasons.json`` are unchanged.
- Item 6: the docs sections exist; CHANGELOG [Unreleased] has one
  multilingual Added bullet, plain words, no em dash, no stale sentence, and
  the phrases tests pin.

The golden comparisons need the phase base commit; they skip where the
checkout does not have it (a shallow clone).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from aipager.i18n import locales
from tests.i18n_surfaces import (
    MODE_LABEL_SURFACES,
    THREE_MODES_NEW_SURFACES,
    THREE_MODES_PAGE_SCREENS,
    THREE_MODES_SURFACES,
    without_mode_labels,
)

REPO = Path(__file__).resolve().parents[3]
BASE = "6a11b78"
NEW_SURFACES = {"language.offer", "language.first_contact", "bot.descriptions"}
LAUNCHED = ("es", "ru", "pt-BR", "zh-Hans", "fr", "de", "ko", "ar", "id", "uk", "it", "tr",
            "fa", "vi")
EM_DASH = "\u2014"
BIDI = re.compile("[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def at_base(rel):
    try:
        res = subprocess.run(["git", "-C", str(REPO), "show", f"{BASE}:{rel}"],
                             capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("git is not available")
    if res.returncode != 0:
        pytest.skip(f"the phase base {BASE} is not in this checkout")
    return res.stdout


def now(rel):
    return (REPO / rel).read_bytes()


# ---- Claude-facing and English goldens ---------------------------------------------------

@pytest.mark.parametrize("rel", ["tests/golden/claude_facing.json",
                                 "tests/golden/safety_reasons.json"])
def test_what_claude_reads_is_byte_identical(rel):
    assert now(rel) == at_base(rel)


@pytest.mark.parametrize("rel", ["tests/golden/miniapp_css_ltr.json",
                                 "tests/integration/multilingual-phase-4/base_english.json",
                                 "tests/integration/multilingual-phase-4/base_page_english.json"])
def test_the_english_mini_app_goldens_are_byte_identical(rel):
    assert now(rel) == at_base(rel)


def test_the_english_page_changed_only_where_three_modes_did():
    """The page golden: the three permission modes (2026-10-11) changed
    the screens with a mode on them, and nothing else."""
    rel = "tests/golden/miniapp_page_english.json"
    base, cur = json.loads(at_base(rel)), json.loads(now(rel))
    old = {s[0]: s for s in base["screens"]}
    new = {s[0]: s for s in cur["screens"]}
    changed = {k for k in set(old) | set(new) if old.get(k) != new.get(k)}
    assert changed <= THREE_MODES_PAGE_SCREENS, sorted(changed - THREE_MODES_PAGE_SCREENS)
    assert {k: v for k, v in base.items() if k not in ("screens", "calls")} == \
        {k: v for k, v in cur.items() if k not in ("screens", "calls")}


def _surfaces(raw):
    return json.loads(raw.decode("utf-8"))


#: Surfaces a later feature changed on purpose: the line under an answer
#: became a live status block (Claude Code style), and the language list
#: dropped its "(beta)" marks (operator, 2026-10-10).
CHANGED_SINCE = {"agents_line", "language.list"}


def test_every_english_surface_of_the_base_is_unchanged():
    rel = "tests/golden/english_surfaces.json"
    base, cur = _surfaces(at_base(rel)), _surfaces(now(rel))
    kept = {k: v for k, v in base.items()
            if k not in CHANGED_SINCE | MODE_LABEL_SURFACES | THREE_MODES_SURFACES}
    assert {k: cur.get(k) for k in kept} == kept
    # Each exemption is real: the surface did change, and still exists.
    assert all(k in base and k in cur and cur[k] != base[k] for k in CHANGED_SINCE)
    # The mode label surfaces changed by the mode labels (2026-10-10) and
    # by nothing else.
    for k in MODE_LABEL_SURFACES - THREE_MODES_SURFACES:
        assert cur[k] != base[k], k
        assert without_mode_labels(cur[k]) == without_mode_labels(base[k]), k


def test_the_english_surfaces_only_gain_the_three_new_ones():
    rel = "tests/golden/english_surfaces.json"
    base, cur = _surfaces(at_base(rel)), _surfaces(now(rel))
    assert set(cur) - set(base) == NEW_SURFACES | THREE_MODES_NEW_SURFACES


def test_no_english_surface_was_removed():
    rel = "tests/golden/english_surfaces.json"
    base, cur = _surfaces(at_base(rel)), _surfaces(now(rel))
    assert set(base) - set(cur) == set()


# ---- CHANGELOG -------------------------------------------------------------------------------

def unreleased():
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    start = text.index("## [Unreleased]")
    end = text.find("\n## [", start + 1)
    return text[start:end if end != -1 else None]


def added_section():
    u = unreleased()
    start = u.index("### Added")
    end = u.find("\n### ", start + 1)
    return u[start:end if end != -1 else None]


def multilingual_bullets():
    tops = re.split(r"\n(?=- )", added_section())
    def about_languages(bullet):
        title = re.match(r"- \*\*(.*?)\*\*", bullet)
        return "فارسی" in bullet or bool(title and "language" in title.group(1).lower())
    return [b for b in tops if b.startswith("- ") and about_languages(b)]


def test_the_changelog_has_one_multilingual_added_bullet():
    assert len(multilingual_bullets()) == 1


def test_the_multilingual_bullet_has_no_em_dash():
    assert EM_DASH not in multilingual_bullets()[0]


@pytest.mark.parametrize("stale", [
    "does not switch a chat by itself",
    "every chat stays English until someone chooses",
    "a plain-ASCII word other than Auto and Ask could hide a session",
])
def test_the_changelog_has_no_stale_sentence(stale):
    assert stale.lower() not in unreleased().lower()


@pytest.mark.parametrize("phrase", ["safety blocks", "Keep English", "/language",
                                    "Automatic"])
def test_the_multilingual_bullet_keeps_the_pinned_facts(phrase):
    assert phrase in multilingual_bullets()[0]


@pytest.mark.parametrize("code", LAUNCHED)
def test_the_changelog_names_every_language_in_its_own_name(code):
    assert locales.LOCALES[code].endonym in multilingual_bullets()[0]


def test_the_changelog_mentions_the_mode_word_rule():
    assert "/mode demander" in multilingual_bullets()[0]


def test_the_changelog_mentions_the_bot_description():
    assert "BotFather" in multilingual_bullets()[0]


# ---- docs --------------------------------------------------------------------------------------

def doc(name):
    return (REPO / name).read_text(encoding="utf-8")


def section(text, heading):
    start = text.index(heading)
    nxt = re.search(r"\n#{1,2} ", text[start + len(heading):])
    return text[start:start + len(heading) + nxt.start()] if nxt else text[start:]


def test_commands_has_a_languages_section():
    assert re.search(r"^## Languages\s*$", doc("docs/commands.md"), re.M)


@pytest.mark.parametrize("needle", ["/language", "Automatic", "admin", "Claude",
                                    "/mode"])
def test_the_languages_section_covers(needle):
    assert needle in section(doc("docs/commands.md"), "## Languages")


@pytest.mark.parametrize("stale", ["does not switch", "never switches a chat by itself"])
def test_commands_no_longer_says_aipager_does_not_switch(stale):
    assert stale not in doc("docs/commands.md")


def test_groups_has_a_language_paragraph_naming_the_admin():
    text = doc("docs/groups.md")
    i = text.index("Language")
    assert "admin" in text[i:i + 1500]


@pytest.mark.parametrize("heading", ["A chat is in the wrong language",
                                     "Right-to-left text looks wrong",
                                     "A translated keyboard word does nothing in another chat"])
def test_troubleshooting_has_the_language_entries(heading):
    assert re.search(rf"^#+ {re.escape(heading)}\s*$", doc("docs/troubleshooting.md"), re.M)


def _readme_language_lines():
    return [ln for ln in doc("README.md").splitlines()
            if all(locales.LOCALES[c].endonym in ln for c in LAUNCHED)]


def test_the_readme_lists_all_languages_on_one_line():
    # The feature line names every language on one line, and the "Read this
    # in" row under the title links every translated README; nothing else
    # repeats the list.
    lines = _readme_language_lines()
    assert len(lines) == 2
    (row,) = [ln for ln in lines if ln.startswith("**Read this in:**")]
    assert [ln for ln in lines if ln.startswith("- Speaks English")]
    assert [c for c in LAUNCHED if f"/docs/i18n/{c})" not in row] == []


def test_the_readme_languages_line_says_no_beta():
    # No "(beta)" anywhere a user reads the language list (operator, 2026-10-10).
    lines = _readme_language_lines()
    assert lines
    assert [ln for ln in lines if "beta" in ln.lower()] == []


@pytest.mark.parametrize("rel", ["README.md", "docs/commands.md", "CHANGELOG.md"])
def test_no_doc_promises_a_beta_mark(rel):
    assert "(beta)" not in doc(rel)


def test_every_doc_listing_the_state_files_lists_languages_json():
    pages = [p for p in (REPO / "docs").glob("*.md")
             if "~/.local/state/aipager/reports.json" in p.read_text(encoding="utf-8")
             and "|" in p.read_text(encoding="utf-8")]
    missing = [p.name for p in pages
               if "languages.json" not in p.read_text(encoding="utf-8")]
    assert missing == []


@pytest.mark.parametrize("name", ["CHANGELOG.md", "README.md", "docs/commands.md",
                                  "docs/groups.md", "docs/troubleshooting.md"])
def test_no_raw_bidi_control_in_the_docs(name):
    assert not BIDI.search(doc(name))


@pytest.mark.parametrize("name", ["docs/commands.md", "docs/groups.md"])
def test_no_em_dash_in_the_new_language_docs(name):
    text = doc(name)
    part = section(text, "## Languages") if name.endswith("commands.md") else \
        text[text.index("Language"):text.index("Language") + 1500]
    assert EM_DASH not in part


def test_no_raw_bidi_control_in_these_tests():
    here = Path(__file__).parent
    hits = [p.name for p in here.glob("*.py") if BIDI.search(p.read_text(encoding="utf-8"))]
    assert hits == []
