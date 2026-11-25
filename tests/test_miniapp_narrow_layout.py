"""The Sessions view at narrow phone widths, in every language, in a real
browser (headless Chromium through playwright-core).

Text-level tests cannot see a pill clipped by its tile or a badge drawn
past its tab button: both shipped (Vietnamese "working" cut at 320 px, the
tile state words and the tab bar's waiting count past their box in
several languages). ``tests/js/miniapp_narrow_layout.js`` serves the page
GET / returns, with the real catalogs and a real grid row shape, by
request interception (no socket, no network), and measures at 320, 360
and 390 px: no sideways page scroll, no clipped text, nothing drawn past
its tile, tab button, beacon or the header row.

Skips cleanly where node, playwright-core or Chromium is missing (CI has
none of them). Point it at a copy with ``AIPAGER_PLAYWRIGHT_CORE`` (the
package directory) and ``AIPAGER_CHROMIUM`` (the browser binary).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from aipager.i18n import locales
from aipager.miniapp import ui_text
from aipager.miniapp.fonts import FONTS_DIR
from aipager.miniapp.sessions import grid_totals, session_summary
from aipager.miniapp.static import index_html
from aipager.state import Status, TrackedSession

HARNESS = Path(__file__).parent / "js" / "miniapp_narrow_layout.js"
WIDTHS = [320, 360, 390]

# One-line fields that end in an ellipsis by design, with the full text on
# the session's own page: a folder name, the model and cost line. The
# daemon line under the brand and a chat chip too. Never the page's own
# words ("no folder", each language's ``never_cut``), and never a session name that fits its row (the
# names here are short): ``.tile-label`` and ``.beacon-label`` stay out.
ELLIPSIS_OK = [".tile-project", ".tile-meta", ".daemon-line", ".chat-chip"]
NO_FOLDER = "ui.tile.no_folder"


@pytest.fixture(scope="module")
def node_bin():
    exe = shutil.which("node") or shutil.which("nodejs")
    if not exe:
        pytest.skip("node not available")
    return exe


def _rows() -> dict:
    now = time.monotonic()

    def row(label, status, **kw):
        s = TrackedSession(name="claude-" + label, label=label, status=status,
                           cwd=kw.pop("cwd", "/home/alice/" + label), **kw)
        s.last_hook_at = now - 75
        return session_summary(s, now)

    busy = row("frontend", Status.BUSY, model_name="claude-opus-4-6", last_token_pct=88,
               last_cost_usd=12.3456)
    idle = row("api", Status.IDLE, model_name="claude-sonnet-4-5", last_token_pct=100,
               last_cost_usd=0.5)
    tasks = dict(row("docs", Status.IDLE, last_token_pct=7), status="tasks", job_agents=2)
    unknown = row("scratch", Status.UNKNOWN, cwd="")
    waiting = dict(row("backend", Status.IDLE, last_token_pct=40), status="waiting",
                   waiting_kind="permission", waiting_summary="Bash: npm run build")
    gone = row("old", Status.GONE)
    rows = [waiting, busy, tasks, idle, unknown, gone]
    totals = grid_totals(rows)
    # Two digits in the Sessions tab's badge and the header sentence.
    totals["waiting"] = 12
    return {"daemon": {"version": "1.6.0", "bot_username": "alice_bot", "uptime_seconds": 7200},
            "totals": totals, "sessions": rows, "can_act": True,
            "can_usage": True}


def _langs(only=None, page_fn=index_html) -> list[dict]:
    out = []
    for code in locales.LOCALES:
        if only and code not in only:
            continue
        if code == "en":
            out.append({"code": "en", "page": page_fn(), "ui": None, "catalog": None,
                        "never_cut": [str(ui_text.TILE_NO_FOLDER)]})
            continue
        cat = ui_text.catalog(code)
        version = ui_text.catalog_version(code)
        assert cat is not None and version is not None, code
        out.append({"code": code, "page": page_fn(lang=code), "catalog": cat,
                    "ui": {"code": code, "rtl": bool(cat["rtl"]), "v": version},
                    "never_cut": [cat["strings"].get(NO_FOLDER, str(ui_text.TILE_NO_FOLDER))]})
    return out


def run_layout(node_bin, tmp_path, *, langs=None, schemes=("light",), shots="",
               page_fn=index_html):
    """The harness's result for *langs* (all when None)."""
    cfg = {"langs": _langs(langs, page_fn), "widths": WIDTHS, "schemes": list(schemes),
           "sessions": _rows(), "fonts_dir": str(FONTS_DIR), "ellipsis_ok": ELLIPSIS_OK}
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    env = dict(os.environ, TMPDIR=str(tmp_path))
    if shots:
        env["SHOTS_DIR"] = shots
    proc = subprocess.run([node_bin, str(HARNESS), str(path)], capture_output=True,
                          text=True, timeout=600, env=env)
    assert proc.returncode == 0, proc.stderr[-3000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    if "skip" in out:
        pytest.skip(out["skip"])
    return out


def test_sessions_view_fits_every_language_at_phone_widths(node_bin, tmp_path):
    out = run_layout(node_bin, tmp_path, shots=os.environ.get("AIPAGER_LAYOUT_SHOTS", ""))
    assert out["errors"] == []
    assert len(out["runs"]) == len(locales.LOCALES) * len(WIDTHS)
    bad = {f"{r['lang']}@{r['width']}": r["problems"] for r in out["runs"] if r["problems"]}
    assert bad == {}, json.dumps(bad, ensure_ascii=False, indent=1)


def test_the_harness_sees_a_clipped_pill(node_bin, tmp_path):
    """Guard the guard: a tile state word forced wider than its tile must
    be reported, so a green run above means the measuring works."""
    page = index_html(lang="vi")
    broken = page.replace("</style>", ".tile-state{white-space:nowrap!important;"
                          "max-width:none!important;min-width:400px}</style>", 1)
    assert broken != page
    out = run_layout(node_bin, tmp_path, langs={"vi"}, page_fn=lambda **kw: broken)
    assert out["errors"] == []
    assert all(any("tile-state" in p for p in r["problems"]) for r in out["runs"]), out["runs"]
