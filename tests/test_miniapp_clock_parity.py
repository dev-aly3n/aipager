"""The Mini App's clock is the bot's clock: the page script carries a
line-for-line port of ``aipager/clockfmt.py`` (``clockText``), and this
runs both over the same inputs with node."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aipager.clockfmt import elapsed

HARNESS = Path(__file__).parent / "js" / "miniapp_clock.js"

CASES = [
    [s, u]
    for u in ("s", "m", "h", "x", None)
    for s in (0, 1, 9, 10, 29, 30, 31, 45, 59, 59.99, 60, 61, 250, 599, 599.9, 600, 1397,
              1410, 1439, 1440, 3599, 3600, 3601, 4980, 5010, 5039, 7200, 86399, 86400,
              360000, 1234567, -1, -0.5, "75", "x", "", None, True, False)
]


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
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_miniapp_clock_parity(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML
    got = _run(node_bin, tmp_path, INDEX_HTML)
    want = [elapsed(s, u) for s, u in CASES]
    diffs = [(c, g, w) for c, g, w in zip(CASES, got, want) if g != w]
    assert not diffs, diffs[:10]


def test_the_parity_harness_detects_a_drifted_port(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML
    assert "var CLOCK_STEP_SECONDS = 30;" in INDEX_HTML
    drifted = INDEX_HTML.replace("var CLOCK_STEP_SECONDS = 30;", "var CLOCK_STEP_SECONDS = 60;")
    got = _run(node_bin, tmp_path, drifted)
    assert got != [elapsed(s, u) for s, u in CASES]


def test_the_old_duration_helper_is_gone():
    from aipager.miniapp.static import INDEX_HTML
    assert "function duration(" not in INDEX_HTML
    assert INDEX_HTML.count("// clock: port of aipager/clockfmt.py") == 1
