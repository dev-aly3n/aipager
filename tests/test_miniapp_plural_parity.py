"""The Mini App's plural rules are the bot's: the page script carries a
line-for-line port of ``aipager/i18n/plurals.py`` (``pluralCategory``),
and this runs both over the same counts with node, for every code."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aipager.i18n import plurals
from tests.i18n_tool import isolated_env

HARNESS = Path(__file__).parent / "js" / "miniapp_plurals.js"

COUNTS = list(range(0, 251)) + [1000, 1001, 1011, 1021, 1000000, 2000000, 1000001,
                                 -1, -2, -5, -11, -21]
CODES = sorted(plurals._RULES) + ["xx", "en-US"]
CASES = [[code, n] for code in CODES for n in COUNTS]


@pytest.fixture(scope="module")
def node_bin():
    exe = shutil.which("node") or shutil.which("nodejs")
    if not exe:
        pytest.skip("node not available")
    return exe


def _run(node_bin, tmp_path, page_text):
    page = tmp_path / "page.html"
    page.write_text(page_text, encoding="utf-8")
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps(CASES), encoding="utf-8")
    proc = subprocess.run([node_bin, str(HARNESS), str(page), str(cases)],
                          capture_output=True, text=True, timeout=60,
                          env=isolated_env(tmp_path))
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_miniapp_plural_parity(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML
    got = _run(node_bin, tmp_path, INDEX_HTML)
    want = [plurals.category(code, n) for code, n in CASES]
    diffs = [(c, g, w) for c, g, w in zip(CASES, got, want) if g != w]
    assert not diffs, diffs[:10]
    # Every family is exercised: Russian's three, Arabic's six.
    assert {g for (code, _n), g in zip(CASES, got) if code == "ru"} == {"one", "few", "many"}
    assert {g for (code, _n), g in zip(CASES, got) if code == "ar"} == {
        "zero", "one", "two", "few", "many", "other"}


def test_the_parity_harness_detects_a_drifted_port(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML
    old = "if (mod10 >= 2 && mod10 <= 4 && !(mod100 >= 12 && mod100 <= 14)) { return \"few\"; }"
    assert INDEX_HTML.count(old) == 1
    drifted = INDEX_HTML.replace(old, old.replace("mod10 <= 4", "mod10 <= 3"))
    got = _run(node_bin, tmp_path, drifted)
    want = [plurals.category(code, n) for code, n in CASES]
    assert got != want


def test_the_port_is_marked_once():
    from aipager.miniapp.static import INDEX_HTML
    assert INDEX_HTML.count("// plurals: port of aipager/i18n/plurals.py") == 1
    assert INDEX_HTML.count("// end plurals") == 1
