"""``tools/i18n.py prune`` and the catalogs (design.md success criteria 11
and 12; entrypoints.md "CLI commands").

``prune`` only ever runs on a COPY of the repository's ``aipager/`` and
``tools/`` trees in pytest's tmp dir, as a subprocess with a throwaway
HOME; the real catalogs are only read, and a sha check proves they were
not touched.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.i18n_surfaces import (
    MODE_LABEL_SURFACES,
    NO_KEYBOARD_APP_SURFACES,
    RESTART_NOW_NEW_SURFACES,
    THREE_MODES_NEW_SURFACES,
    THREE_MODES_SURFACES,
    with_keyboard_app_removed,
    without_mode_labels,
)

REPO = Path(__file__).resolve().parents[3]
LOCALES = REPO / "aipager" / "i18n" / "locales"
SOURCE = REPO / "tools" / "i18n" / "source.jsonl"
BASE = "7c482fa"
TRANSLATED = ("es", "pt-BR", "zh-Hans", "fr", "de", "ko", "ar", "id", "uk", "it",
              "tr", "fa", "vi", "ru")
OLD_KEYS = ("agents.running", "agents.names_more", "agents.done_one",
            "agents.done_shell", "agents.done_many")
NEW_KEYS = ("agents.head.working", "agents.head.done", "agents.head.shells_running",
            "agents.head.shells_finished", "agents.head.shells_stopped",
            "agents.head.join", "agents.row.working", "agents.row.working_bare",
            "agents.row.done", "agents.more", "agents.promise")
#: The activity phrases became Claude Code's English words, never translated
#: (operator, 2026-10-10): their keys are gone from every catalog.
OLD_ACTIVITY_KEYS = ("agents.act.reading", "agents.act.writing", "agents.act.editing",
                     "agents.act.running", "agents.act.searching", "agents.act.searching_web",
                     "agents.act.fetching", "agents.act.agent", "agents.act.tool")


def _sha_tree(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.glob("*.jsonl")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def _keys(path: Path) -> list[str]:
    return [json.loads(x)["k"] for x in path.read_text(encoding="utf-8").splitlines()
            if x.strip()]


@pytest.fixture(scope="module")
def real_sha():
    return _sha_tree(LOCALES)


@pytest.fixture
def copy(tmp_path, real_sha):
    root = tmp_path / "repo"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(REPO / "aipager", root / "aipager", ignore=ignore)
    shutil.copytree(REPO / "tools", root / "tools", ignore=ignore)
    (root / "tests").mkdir()
    for name in ("i18n_literal_baseline.json",):
        if (REPO / "tests" / name).exists():
            shutil.copy2(REPO / "tests" / name, root / "tests" / name)
    yield root
    assert _sha_tree(LOCALES) == real_sha, "a real catalog changed"


def _run(root: Path, *args: str, home: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE_TG_", "AIPAGER_"))}
    env.update(HOME=str(home), PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(root))
    return subprocess.run(
        [sys.executable, "-B", str(root / "tools" / "i18n.py"), *args],
        cwd=root, env=env, capture_output=True, text=True, timeout=300)


def _add_orphan(root: Path, code: str, key: str) -> None:
    path = root / "aipager" / "i18n" / "locales" / f"{code}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"k": key, "src": "00000000", "v": "orphan"},
                            ensure_ascii=False) + "\n")


def test_prune_on_a_clean_copy_removes_nothing(copy, tmp_path):
    before = _sha_tree(copy / "aipager" / "i18n" / "locales")
    out = _run(copy, "prune", home=tmp_path)
    assert (out.returncode, out.stdout.strip(),
            _sha_tree(copy / "aipager" / "i18n" / "locales")) == (0, "", before)


def test_prune_removes_exactly_the_orphans(copy, tmp_path):
    _add_orphan(copy, "fa", "agents.running")
    _add_orphan(copy, "ru", "agents.done_many")
    out = _run(copy, "prune", home=tmp_path)
    assert (out.returncode, sorted(x for x in out.stdout.splitlines() if x)) == (
        0, ["fa agents.running", "ru agents.done_many"])


def test_prune_leaves_every_other_entry_byte_identical(copy, tmp_path):
    loc = copy / "aipager" / "i18n" / "locales"
    before = {p.name: p.read_bytes() for p in loc.glob("*.jsonl")}
    _add_orphan(copy, "de", "agents.names_more")
    _run(copy, "prune", home=tmp_path)
    after = {p.name: p.read_bytes() for p in loc.glob("*.jsonl")}
    assert after == before


def test_prune_dry_run_prints_but_changes_nothing(copy, tmp_path):
    _add_orphan(copy, "fa", "agents.running")
    loc = copy / "aipager" / "i18n" / "locales"
    before = _sha_tree(loc)
    out = _run(copy, "prune", "--dry-run", home=tmp_path)
    assert ("fa agents.running" in out.stdout, _sha_tree(loc)) == (True, before)


def test_prune_refuses_a_stale_source(copy, tmp_path):
    src = copy / "tools" / "i18n" / "source.jsonl"
    lines = src.read_text(encoding="utf-8").splitlines(keepends=True)
    src.write_text("".join(lines[1:]), encoding="utf-8")
    out = _run(copy, "prune", home=tmp_path)
    assert out.returncode == 1


def test_prune_on_a_stale_source_touches_no_catalog(copy, tmp_path):
    src = copy / "tools" / "i18n" / "source.jsonl"
    lines = src.read_text(encoding="utf-8").splitlines(keepends=True)
    src.write_text("".join(lines[1:]), encoding="utf-8")
    loc = copy / "aipager" / "i18n" / "locales"
    before = _sha_tree(loc)
    _run(copy, "prune", home=tmp_path)
    assert _sha_tree(loc) == before


def test_check_passes_on_the_copy(copy, tmp_path):
    out = _run(copy, "check", home=tmp_path)
    assert out.returncode == 0, out.stderr[-2000:]


# ---- the shipped catalogs (read only) -------------------------------------------------

@pytest.mark.parametrize("code", TRANSLATED)
def test_every_catalog_holds_every_new_key(code):
    keys = set(_keys(LOCALES / f"{code}.jsonl"))
    assert [k for k in NEW_KEYS if k not in keys] == []


@pytest.mark.parametrize("code", TRANSLATED)
def test_no_catalog_keeps_an_old_key(code):
    keys = set(_keys(LOCALES / f"{code}.jsonl"))
    assert [k for k in OLD_KEYS + OLD_ACTIVITY_KEYS if k in keys] == []


def test_the_source_has_no_old_key():
    keys = set(_keys(SOURCE))
    assert [k for k in OLD_KEYS + OLD_ACTIVITY_KEYS if k in keys] == []


def test_the_source_has_every_new_key():
    keys = set(_keys(SOURCE))
    assert [k for k in NEW_KEYS if k not in keys] == []


@pytest.mark.parametrize("code", TRANSLATED)
def test_no_new_value_has_an_em_dash(code):
    rows = [json.loads(x) for x in (LOCALES / f"{code}.jsonl").read_text(
        encoding="utf-8").splitlines() if x.strip()]
    bad = [r["k"] for r in rows if r["k"] in NEW_KEYS and "\u2014" in json.dumps(
        r["v"], ensure_ascii=False)]
    assert bad == []


# ---- goldens: everything else byte-identical ------------------------------------------

def _git_show(path: str) -> bytes:
    """*path* at the feature's base commit. A checkout without that commit
    (CI's shallow clone, a squash-merged main, a tree copy) skips the
    comparison, as phase 5's base pins do."""
    try:
        res = subprocess.run(["git", "-C", str(REPO), "show", f"{BASE}:{path}"],
                             capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("git is not available")
    if res.returncode != 0:
        pytest.skip(f"the feature base {BASE} is not in this checkout")
    return res.stdout


def test_claude_facing_golden_is_byte_identical():
    path = "tests/golden/claude_facing.json"
    assert (REPO / path).read_bytes() == _git_show(path)


def test_english_surfaces_change_only_under_agents_line():
    path = "tests/golden/english_surfaces.json"
    old = json.loads(_git_show(path))
    new = json.loads((REPO / path).read_text(encoding="utf-8"))
    changed = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
    # agents_line is this feature's; language.list lost its "(beta)" marks
    # later on purpose (operator, 2026-10-10), and the mode labels became
    # "💬 Ask mode" / "🤖 Auto mode" the same day, changing nothing else.
    # The three permission modes (2026-10-11) changed their own surfaces
    # further, and added one.
    # The main keyboard lost its App button (2026-10-12), and only that.
    three = THREE_MODES_SURFACES | THREE_MODES_NEW_SURFACES
    # Restart now without a service (2026-10-12) added one surface.
    exempt = (MODE_LABEL_SURFACES | three | NO_KEYBOARD_APP_SURFACES
              | RESTART_NOW_NEW_SURFACES)
    assert [k for k in changed if k not in exempt] == [
        "agents_line", "language.list"]
    for k in NO_KEYBOARD_APP_SURFACES:
        assert new[k] == with_keyboard_app_removed(old[k]), k
    assert set(changed) >= MODE_LABEL_SURFACES
    for k in MODE_LABEL_SURFACES - three:
        assert without_mode_labels(new[k]) == without_mode_labels(old[k]), k


def test_the_hook_process_never_loads_i18n(tmp_path):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE_TG_", "AIPAGER_"))}
    env.update(HOME=str(tmp_path), PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(REPO))
    code = ("import sys, aipager.dtach.notify_hook, aipager.state; "
            "print(sorted(m for m in sys.modules if m.startswith('aipager.i18n')))")
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=tmp_path, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.stdout.strip() == "[]", out.stderr[-2000:]
