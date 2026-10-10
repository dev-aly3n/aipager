"""The dev-only ``tools/i18n.py`` CLI (entrypoints.md "CLI commands";
design.md success criterion 8: ``extract --check`` passes).

Read-only subcommands run against the worktree and must leave it
byte-identical. Anything that could write (``extract`` without --check,
``apply``) or needs a broken tree runs against a throwaway copy of
``aipager/``, ``tools/`` and the ratchet baseline under ``tmp_path``.
Every subprocess gets a throwaway HOME.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "tools" / "i18n.py"
CATALOGS = REPO / "aipager" / "i18n" / "locales"
CODES = ("es", "ru", "pt-BR", "zh-Hans", "fr", "de", "ko", "ar", "id", "uk",
         "it", "tr", "fa", "vi")


def _env(tmp_path, root):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (tmp_path / "instance").mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "AIPAGER", "XDG_"))}
    env.update(PYTHONPATH=str(root), HOME=str(home), PYTHONDONTWRITEBYTECODE="1",
               AIPAGER_INSTANCE_DIR=str(tmp_path / "instance"), TMPDIR=str(tmp_path))
    return env


def _run(tmp_path, *args, root=REPO):
    return subprocess.run(
        [sys.executable, str(root / "tools" / "i18n.py"), *args],
        capture_output=True, text=True, env=_env(tmp_path, root), cwd=str(root),
        timeout=120)


def _tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for sub in ("aipager/i18n/locales", "tools/i18n"):
        for p in sorted((root / sub).rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                h.update(str(p.relative_to(root)).encode())
                h.update(p.read_bytes())
    baseline = root / "tests" / "i18n_literal_baseline.json"
    if baseline.exists():
        h.update(baseline.read_bytes())
    return h.hexdigest()


@pytest.fixture
def copy_root(tmp_path):
    root = tmp_path / "repo"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(REPO / "aipager", root / "aipager", ignore=ignore)
    shutil.copytree(REPO / "tools", root / "tools", ignore=ignore)
    (root / "tests").mkdir()
    shutil.copy2(REPO / "tests" / "i18n_literal_baseline.json",
                 root / "tests" / "i18n_literal_baseline.json")
    for name in ("pyproject.toml", "CLAUDE.md"):
        if (REPO / name).exists():
            shutil.copy2(REPO / name, root / name)
    return root


# ---- the shipped files ----------------------------------------------------------------

@pytest.mark.parametrize("code", CODES)
def test_each_language_has_a_catalog_file(code):
    assert (CATALOGS / f"{code}.jsonl").is_file()


# Phase 2 translated Persian and Russian, phase 5 run 1 Spanish, Portuguese
# (Brazil), French and Italian; the other eight stay empty.
@pytest.mark.parametrize("code", [c for c in CODES
                                  if c not in ("fa", "ru", "es", "pt-BR", "fr", "it")])
def test_no_translation_is_written_in_this_run(code):
    assert (CATALOGS / f"{code}.jsonl").read_text(encoding="utf-8").strip() == ""


def test_there_is_no_english_catalog():
    """English is inline next to each key; it never comes from a catalog."""
    assert not (CATALOGS / "en.jsonl").exists()


@pytest.mark.parametrize("code", CODES)
def test_each_language_has_a_glossary(code):
    data = json.loads((REPO / "tools" / "i18n" / "glossary" / f"{code}.json")
                      .read_text(encoding="utf-8"))
    assert {"address", "terms", "keep_english"} <= set(data)


@pytest.mark.parametrize("code,address", [("fa", "شما"), ("fr", "vous"),
                                          ("de", "du"), ("id", "kamu")])
def test_the_decided_forms_of_address(code, address):
    data = json.loads((REPO / "tools" / "i18n" / "glossary" / f"{code}.json")
                      .read_text(encoding="utf-8"))
    assert address in json.dumps(data["address"], ensure_ascii=False)


@pytest.mark.parametrize("code", CODES)
def test_each_language_has_a_style_guide(code):
    assert (REPO / "tools" / "i18n" / "style" / f"{code}.md").read_text(
        encoding="utf-8").strip()


def test_catalogs_merge_by_union():
    lines = (REPO / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "aipager/i18n/locales/*.jsonl merge=union" in [ln.strip() for ln in lines]


def _toml_list(text, header, name):
    """The items of ``name = [...]`` under ``[header]`` (no tomllib on 3.10)."""
    section = text.split(f"[{header}]", 1)[1].split("\n[", 1)[0]
    match = re.search(rf"^{re.escape(name)}\s*=\s*\[(.*?)\]", section, re.S | re.M)
    return re.findall(r'"([^"]+)"', match.group(1)) if match else []


def test_babel_is_a_dev_extra_only():
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    runtime = " ".join(_toml_list(text, "project", "dependencies")).lower()
    dev = " ".join(_toml_list(text, "project.optional-dependencies", "dev")).lower()
    assert ("babel" in dev, "babel" in runtime) == (True, False)


def test_the_tool_is_not_inside_the_package():
    """Dev only, not in the wheel: it lives outside aipager/."""
    assert TOOL.is_file() and not (REPO / "aipager" / "tools").exists()


# ---- extract -------------------------------------------------------------------------

def test_extract_check_passes_on_the_tree(tmp_path):
    proc = _run(tmp_path, "extract", "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_extract_check_writes_nothing(tmp_path):
    before = _tree_digest(REPO)
    _run(tmp_path, "extract", "--check")
    assert _tree_digest(REPO) == before


def test_the_source_file_is_sorted_one_object_per_line():
    lines = (REPO / "tools" / "i18n" / "source.jsonl").read_text(
        encoding="utf-8").splitlines()
    keys = [json.loads(ln)["k"] for ln in lines]
    assert keys == sorted(keys) and len(keys) == len(set(keys))


def test_the_source_file_has_the_language_keys():
    keys = {json.loads(ln)["k"] for ln in (REPO / "tools" / "i18n" / "source.jsonl")
            .read_text(encoding="utf-8").splitlines()}
    assert {"lang.row", "lang.changed", "lang.list.private", "lang.list.group",
            "lang.offer.text", "lang.offer.keep", "lang.auto"} <= keys


def test_extract_on_a_copy_reproduces_the_source_file(tmp_path, copy_root):
    before = (copy_root / "tools" / "i18n" / "source.jsonl").read_bytes()
    proc = _run(tmp_path, "extract", root=copy_root)
    assert proc.returncode == 0, proc.stderr
    assert (copy_root / "tools" / "i18n" / "source.jsonl").read_bytes() == before


def _add_module(root, body):
    (root / "aipager" / "zz_probe_strings.py").write_text(
        "from aipager.i18n import Msg\n\n" + body, encoding="utf-8")


def test_extract_check_fails_when_a_new_key_is_not_extracted(tmp_path, copy_root):
    _add_module(copy_root, 'X = Msg("toast.zz_probe", "A probe line")\n')
    proc = _run(tmp_path, "extract", "--check", root=copy_root)
    assert proc.returncode != 0


def test_extract_picks_up_a_new_key(tmp_path, copy_root):
    _add_module(copy_root, 'X = Msg("toast.zz_probe", "A probe line")\n')
    _run(tmp_path, "extract", root=copy_root)
    text = (copy_root / "tools" / "i18n" / "source.jsonl").read_text(encoding="utf-8")
    assert '"toast.zz_probe"' in text


@pytest.mark.parametrize("body", [
    'KEY = "toast.zz_probe"\nX = Msg(KEY, "A probe line")\n',          # non-literal key
    'EN = "A probe line"\nX = Msg("toast.zz_probe", EN)\n',            # non-literal English
    'X = Msg("toast.zz_probe", "A probe " + "line")\n',                 # expression
    'X = Msg("Bad Key", "A probe line")\n',                              # bad key
    'X = Msg("toast.zz_probe", "One English")\n'
    'Y = Msg("toast.zz_probe", "Another English")\n',                    # two Englishes
])
def test_extract_refuses_a_bad_message(tmp_path, copy_root, body):
    _add_module(copy_root, body)
    proc = _run(tmp_path, "extract", root=copy_root)
    assert proc.returncode != 0


# ---- check ---------------------------------------------------------------------------

def test_check_passes_on_the_tree(tmp_path):
    proc = _run(tmp_path, "check")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_check_writes_nothing(tmp_path):
    before = _tree_digest(REPO)
    _run(tmp_path, "check")
    assert _tree_digest(REPO) == before


def test_check_fails_on_an_unsorted_catalog(tmp_path, copy_root):
    (copy_root / "aipager" / "i18n" / "locales" / "fa.jsonl").write_text(
        '{"k": "lang.row", "v": "x {language}", "src": "00000000"}\n'
        '{"k": "lang.auto", "v": "y", "src": "00000000"}\n', encoding="utf-8")
    proc = _run(tmp_path, "check", root=copy_root)
    assert proc.returncode != 0


def test_check_fails_on_an_em_dash_in_a_glossary(tmp_path, copy_root):
    path = copy_root / "tools" / "i18n" / "glossary" / "fr.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["keep_english"] = list(data.get("keep_english", [])) + ["a\u2014b"]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    proc = _run(tmp_path, "check", root=copy_root)
    assert proc.returncode != 0


# ---- todo / apply / review-pack ---------------------------------------------------------

def test_todo_prints_a_json_batch(tmp_path):
    proc = _run(tmp_path, "todo", "--lang", "fa")
    assert proc.returncode == 0, proc.stderr
    json.loads(proc.stdout)


def test_todo_lists_the_missing_language_keys(tmp_path):
    # German: Persian is complete since phase 2, Spanish since phase 5.
    proc = _run(tmp_path, "todo", "--lang", "de")
    assert "lang.row" in proc.stdout


def test_todo_writes_nothing(tmp_path):
    before = _tree_digest(REPO)
    _run(tmp_path, "todo", "--lang", "fa")
    assert _tree_digest(REPO) == before


def test_review_pack_runs(tmp_path):
    proc = _run(tmp_path, "review-pack", "--lang", "fa")
    assert proc.returncode == 0, proc.stderr


def test_review_pack_writes_nothing_in_the_tree(tmp_path):
    before = _tree_digest(REPO)
    _run(tmp_path, "review-pack", "--lang", "fa")
    assert _tree_digest(REPO) == before


@pytest.mark.parametrize("payload", [b"{not json", b"[]garbage", b"\xff\xfe"])
def test_apply_refuses_a_file_that_is_not_json(tmp_path, copy_root, payload):
    batch = tmp_path / "batch.json"
    batch.write_bytes(payload)
    proc = _run(tmp_path, "apply", "--lang", "fa", str(batch), root=copy_root)
    assert proc.returncode != 0


def test_apply_refuses_a_translation_that_drops_a_placeholder(tmp_path, copy_root):
    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps({"lang.row": "زبان"}, ensure_ascii=False),
                     encoding="utf-8")
    catalog = copy_root / "aipager" / "i18n" / "locales" / "fa.jsonl"
    before = catalog.read_bytes()
    _run(tmp_path, "apply", "--lang", "fa", str(batch), root=copy_root)
    assert catalog.read_bytes() == before


def test_apply_refuses_an_unknown_language(tmp_path, copy_root):
    batch = tmp_path / "batch.json"
    batch.write_text("{}", encoding="utf-8")
    proc = _run(tmp_path, "apply", "--lang", "xx", str(batch), root=copy_root)
    assert proc.returncode != 0


def test_a_refused_apply_leaves_the_catalog_alone(tmp_path, copy_root):
    catalog = copy_root / "aipager" / "i18n" / "locales" / "fa.jsonl"
    before = catalog.read_bytes()
    batch = tmp_path / "batch.json"
    batch.write_bytes(b"{not json")
    _run(tmp_path, "apply", "--lang", "fa", str(batch), root=copy_root)
    assert catalog.read_bytes() == before


# ---- pseudo ---------------------------------------------------------------------------

def _pseudo(tmp_path, *args):
    proc = _run(tmp_path, "pseudo", *args)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.rstrip("\n")


def test_pseudo_changes_the_letters(tmp_path):
    assert "Hello" not in _pseudo(tmp_path, "Hello world")


def test_pseudo_wraps_the_text(tmp_path):
    out = _pseudo(tmp_path, "Hello world").replace("\u200f", "")
    assert out.startswith("⟦") and out.endswith("⟧")


def test_pseudo_pads_the_text(tmp_path):
    assert len(_pseudo(tmp_path, "Hello world, this is a sentence")) > len(
        "Hello world, this is a sentence") * 1.2


@pytest.mark.parametrize("token", ["{name}", "/status", "<b>", "</b>", "Claude", "aipager",
                                   "&amp;"])
def test_pseudo_keeps_placeholders_tags_commands_and_names(tmp_path, token):
    text = "Hello {name}, run /status in <b>Claude</b> via aipager &amp; more"
    assert token in _pseudo(tmp_path, text)


def test_pseudo_keeps_code_contents(tmp_path):
    assert "<code>ls -la</code>" in _pseudo(tmp_path, "Run <code>ls -la</code> now")


def test_pseudo_rtl_differs_from_ltr(tmp_path):
    assert _pseudo(tmp_path, "--rtl", "Hello world") != _pseudo(tmp_path, "Hello world")


def test_an_unknown_subcommand_fails(tmp_path):
    assert _run(tmp_path, "translate-everything").returncode != 0
