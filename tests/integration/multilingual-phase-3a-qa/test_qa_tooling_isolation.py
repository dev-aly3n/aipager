"""Success criteria 4, 5, 19 and 22 through their public surfaces: the i18n
CLI, the literal baseline, the hook's import closure, safety.py's imports
and the CHANGELOG. Every subprocess gets a throwaway HOME."""

from __future__ import annotations

import ast
import json
import subprocess
import sys

import pytest
import qa_p3a_scenarios as S

REPO, clean_env = S.REPO, S.clean_env


def _cli(tmp_path, *args):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return subprocess.run([sys.executable, "tools/i18n.py", *args], cwd=REPO,
                          env=clean_env(home, REPO), capture_output=True, text=True,
                          timeout=300)


# ---- criterion 4 ------------------------------------------------------------------------------

def test_extract_check_exits_zero(tmp_path):
    assert _cli(tmp_path, "extract", "--check").returncode == 0


def test_all_checks_exit_zero(tmp_path):
    assert _cli(tmp_path, "check").returncode == 0


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_nothing_left_to_translate(tmp_path, lang):
    out = _cli(tmp_path, "todo", "--lang", lang)
    assert json.loads(out.stdout)["entries"] == []


def _catalog_keys(path):
    return {json.loads(line)["k"] for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()}


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_every_source_key_has_a_translation(lang):
    source = _catalog_keys(REPO / "tools" / "i18n" / "source.jsonl")
    catalog = _catalog_keys(REPO / "aipager" / "i18n" / "locales" / f"{lang}.jsonl")
    assert source - catalog == set()


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_catalog_lines_are_sorted_and_unique(lang):
    lines = [json.loads(x)["k"] for x in (REPO / "aipager" / "i18n" / "locales" /
                                         f"{lang}.jsonl").read_text("utf-8").splitlines()
             if x.strip()]
    assert lines == sorted(set(lines))


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_no_catalog_holds_an_em_dash(lang):
    text = (REPO / "aipager" / "i18n" / "locales" / f"{lang}.jsonl").read_text("utf-8")
    assert "\u2014" not in text


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_no_catalog_holds_a_raw_bidi_control(lang):
    import re
    text = (REPO / "aipager" / "i18n" / "locales" / f"{lang}.jsonl").read_text("utf-8")
    assert not re.search("[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]", text)


# ---- criterion 5 ------------------------------------------------------------------------------

MIGRATED = ["handlers", "new_flow", "session_parity", "session_ops", "send_now", "notify",
            "callbacks", "card_owner", "tap_gate", "chat_migration", "voice", "held_message"]


def _baselines(golden):
    """The base's literal baseline (recorded in the golden) and today's."""
    after = json.loads((REPO / "tests" / "i18n_literal_baseline.json").read_text())
    return golden["literal_baseline"], after


@pytest.mark.parametrize("module", MIGRATED)
def test_the_literal_count_fell(golden, module):
    before, after = _baselines(golden)
    path = f"aipager/bot/{module}.py"
    assert after.get(path, 0) < before.get(path, 0)


#: Modules phase 3a added (counted too, though the base has no entry).
NEW_MODULES = ("aipager/bot/safety_notice.py", "aipager/bot/sibling_text.py")


def test_no_literal_count_rose(golden):
    """No module the base had, nor one phase 3a added, holds more
    hard-coded English than the base did (modules another phase adds are
    that phase's to ratchet, so a merged tree is judged the same way)."""
    before, after = _baselines(golden)
    after = {k: v for k, v in after.items() if k in before or k in NEW_MODULES}
    assert {k: (before.get(k, 0), v) for k, v in after.items() if v > before.get(k, 0)} == {}


# ---- criterion 19 -----------------------------------------------------------------------------

PROBE = ("import sys\n"
         "import aipager.dtach.notify_hook, aipager.dtach.enforce, aipager.safety\n"
         "import aipager.policy_snapshot\n"
         "print(sorted(m for m in sys.modules if m.startswith(('aipager.i18n', 'aipager.bot'))))")


def test_the_hook_never_loads_i18n_or_the_bot(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    out = subprocess.run([sys.executable, "-c", PROBE], cwd=tmp_path,
                         env=clean_env(home, REPO), capture_output=True, text=True, timeout=120)
    assert out.stdout.strip() == "[]", out.stderr


def _imports(source):
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add("." * node.level + (node.module or ""))
    return names


def test_safety_imports_nothing_new(golden):
    after = (REPO / "aipager" / "safety.py").read_text()
    assert _imports(after) - set(golden["safety_imports"]) == set()


# ---- criterion 22 -----------------------------------------------------------------------------

def _unreleased():
    """The release the languages entry shipped in (it was [Unreleased])."""
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    start = text.index("## [1.1.0]")
    end = text.find("\n## [", start + 1)
    return text[start:end if end != -1 else None]


#: Phase 5 folded the per-language entries (Persian and Russian first)
#: into one story for users.
TITLE = "**aipager speaks 15 languages.**"


def _entry():
    return [b for b in _unreleased().split("\n- ") if TITLE in b][0]


def test_one_languages_entry():
    assert _unreleased().count(TITLE) == 1
    assert "**Persian and Russian.**" not in _unreleased()


def test_the_entry_names_safety_blocks():
    assert "safety blocks" in _entry()


def test_the_entry_has_no_em_dash():
    assert "\u2014" not in _entry()


def test_the_entry_has_no_stale_sentence():
    for stale in ("does not switch a chat by itself", "every chat stays English until someone",
                  "could hide a session"):
        assert stale not in _unreleased(), stale


def test_the_entry_no_longer_says_other_messages_stay_english():
    assert "Other messages stay English for now." not in _unreleased()
