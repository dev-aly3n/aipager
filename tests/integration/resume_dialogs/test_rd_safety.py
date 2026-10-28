"""Black-box rows for roadmap 8.127's safety rules and text (design.md
success criteria 12 and 13): no digit, arrow or Enter is ever written
into a dialog, Claude Code's global config is never read or written, the
new texts are whole sentences in all 14 catalogs, and the hook's import
closure stays free of ``aipager.i18n``."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from aipager import term_dialog
from tests.resume_dialog_kit import ESC

ROOT = Path(__file__).resolve().parents[3]
LOCALES = ROOT / "aipager" / "i18n" / "locales"
LANGS = ["ar", "de", "es", "fa", "fr", "id", "it", "ko", "pt-BR", "ru", "tr", "uk", "vi",
         "zh-Hans"]
KEYS = [
    "rdlg.unsure_question", "rdlg.unsure_hint", "rdlg.cold_question", "rdlg.btn_continue",
    "rdlg.btn_resume", "rdlg.btn_new", "rdlg.other_note", "route.held_resume_dialog",
    "rdlg.closed_continued", "rdlg.closed_new", "rdlg.closed_terminal", "rdlg.closed_ended",
    "rdlg.closed_restarted", "rdlg.new_failed", "toast.rdlg_closed", "toast.rdlg_answering",
    "toast.rdlg_failed", "toast.rdlg_not_idle",
]


# -- an observer of every file open, gated (sys.audit, no patching) ---------------------

_OPENS: list | None = None
_HOOKED = False


def _audit(event, args):
    if _OPENS is not None and event == "open" and args and isinstance(args[0], (str, bytes)):
        mode = args[1] if len(args) > 1 and isinstance(args[1], str) else "r"
        _OPENS.append((str(args[0]), mode or "r"))


@pytest.fixture
def opens():
    global _OPENS, _HOOKED
    if not _HOOKED:
        sys.addaudithook(_audit)
        _HOOKED = True
    _OPENS = []
    yield _OPENS
    _OPENS = None


def _record_status(world) -> str:
    return json.loads((world.cfg / "sessions" / "4100.json").read_text())["status"]


def _watch(world, into_dialog):
    """Note every write made while the record still says waiting."""
    def on_write(data):
        if _record_status(world) == "waiting":
            into_dialog.append(data)
        if data == ESC:
            world.idle(ago=0.0)
    world.on_write = on_write


def _all_flows(world, mk_update):
    into = []
    # resume card, Continue
    world.after_resume()
    world.scan()
    _watch(world, into)
    world.tap("rdcont")
    world.scan()
    world.clock.advance(term_dialog.ANSWER_SETTLE_SECONDS + 1)
    world.idle()
    world.scan()
    # idle cold card, Resume, with a message held
    world.on_write = None
    world.clock.advance(400)
    world.cold_idle()
    world.scan()
    world.send(mk_update, "first", msg_id=10)
    _watch(world, into)
    world.tap("rdresume")
    world.scan()
    world.clock.advance(term_dialog.ANSWER_SETTLE_SECONDS + 1)
    world.idle()
    world.scan()
    # idle cold card, New
    world.on_write = None
    world.bot.registry.transition("claude-frontend", __import__("aipager.state").state.Status.IDLE)
    world.clock.advance(400)
    world.cold_idle()
    world.scan()
    world.send(mk_update, "second", msg_id=11)
    _watch(world, into)
    world.tap("rdnew")
    world.scan()
    return into


# -- criterion 12 -----------------------------------------------------------------------

def test_only_escape_written_into_an_open_dialog(world, mk_update):
    into = _all_flows(world, mk_update)
    assert into and set(into) == {ESC}


def test_no_digit_ever_written_by_a_card_flow(world, mk_update):
    _all_flows(world, mk_update)
    assert not any(w.isdigit() for w in world.writes)


def test_no_arrow_key_ever_written_by_a_card_flow(world, mk_update):
    _all_flows(world, mk_update)
    assert not any(re.search(rb"\x1b\[[ABCD]|\x1bO[ABCD]", w) for w in world.writes)


def test_claude_global_config_never_opened(world, mk_update, opens):
    _all_flows(world, mk_update)
    assert [p for p, _ in opens if p.endswith(".claude.json")] == []


def test_claude_settings_never_opened(world, mk_update, opens):
    _all_flows(world, mk_update)
    assert [p for p, _ in opens if Path(p).name in ("settings.json", "settings.local.json")
            and ".claude" in p] == []


def test_claude_config_dir_never_written_by_aipager(world, mk_update, opens):
    world.after_resume()
    world.scan()
    del opens[:]
    world.on_write = None
    world.tap("rdcont")
    world.scan()
    cfg = str(world.cfg)
    assert [p for p, m in opens if p.startswith(cfg) and set(m) & set("wax+")] == []


def test_claude_config_dir_tree_unchanged_by_aipager(world, mk_update):
    world.after_resume()
    world.scan()
    before = sorted(str(p.relative_to(world.cfg)) for p in world.cfg.rglob("*"))
    world.tap("rdcont")
    world.scan()
    after = sorted(str(p.relative_to(world.cfg)) for p in world.cfg.rglob("*"))
    assert after == before


# -- criterion 13 -----------------------------------------------------------------------

def _catalog(lang):
    out = {}
    for line in (LOCALES / f"{lang}.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["k"]] = row["v"]
    return out


def _source():
    out = {}
    for line in (ROOT / "tools" / "i18n" / "source.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["k"]] = row
    return out


def test_source_carries_every_new_key():
    src = _source()
    assert [k for k in KEYS if k not in src] == []


@pytest.mark.parametrize("lang", LANGS)
def test_catalog_carries_every_new_key(lang):
    cat = _catalog(lang)
    assert [k for k in KEYS if not cat.get(k)] == []


@pytest.mark.parametrize("lang", LANGS)
def test_catalog_keeps_every_placeholder(lang):
    cat, src = _catalog(lang), _source()
    bad = [k for k in KEYS
           if set(re.findall(r"\{(\w+)\}", src[k]["en"])) != set(re.findall(r"\{(\w+)\}", cat[k]))]
    assert bad == []


@pytest.mark.parametrize("lang", LANGS)
def test_catalog_sentences_are_translated(lang):
    """Whole sentences are not left in English (buttons may match)."""
    cat, src = _catalog(lang), _source()
    same = [k for k in KEYS if ".btn_" not in k and cat[k] == src[k]["en"]]
    assert same == []


def test_english_has_no_em_dash():
    src = _source()
    assert [k for k in KEYS if "—" in src[k]["en"]] == []


@pytest.mark.parametrize("lang", LANGS)
def test_catalog_has_no_em_dash(lang):
    cat = _catalog(lang)
    assert [k for k in KEYS if "—" in cat[k]] == []


def test_english_keeps_html_bold_session_name():
    src = _source()
    assert "<b>{session}</b>" in src["rdlg.unsure_question"]["en"]


def _tool(*args):
    return subprocess.run([sys.executable, "tools/i18n.py", *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=300)


def test_i18n_extract_is_clean():
    assert _tool("extract", "--check").returncode == 0


def test_i18n_check_passes():
    assert _tool("check").returncode == 0


@pytest.mark.parametrize("module", [
    "aipager.dtach.notify_hook", "aipager.claude_jobs", "aipager.state", "aipager.term_dialog",
], ids=["hook", "claude_jobs", "state", "term_dialog"])
def test_module_does_not_load_i18n(module):
    code = (f"import sys, {module}; "
            "sys.exit(1 if any(m == 'aipager.i18n' or m.startswith('aipager.i18n.') "
            "for m in sys.modules) else 0)")
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]


def test_state_does_not_load_term_dialog():
    code = "import sys, aipager.state; sys.exit(1 if 'aipager.term_dialog' in sys.modules else 0)"
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]


def test_open_observer_sees_the_session_record(world, mk_update, opens):
    """The observer above is live (else the two "never opened" rows pass
    for nothing)."""
    _all_flows(world, mk_update)
    assert any(p.endswith("/sessions/4100.json") for p, _ in opens)
