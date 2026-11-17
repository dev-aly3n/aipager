"""The Mini App Usage page, driven in node from the outside (design.md
Success criterion 17; entrypoints.md "Mini App (page)").

``usage_page.js`` runs the page GET / serves in a small DOM shim; the
/api/sessions and /api/usage answers are the real server's bodies for the
caller. Partitions: a caller with ``can_usage`` true vs false; a reading
with an ok window vs a reset window; the Usage tab and back to Sessions. Error guessing: markup in a
model id must stay text; a failed fetch must not throw."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from aipager import usage_store
from aipager.claude_bootstrap import SessionLogin
from aipager.statusline_file import Window

# Token days must not straddle midnight between import and run.
pytestmark = pytest.mark.usefixtures("local_day_centred")

HARNESS = Path(__file__).parent / "usage_page.js"
NOW = time.time()
OPEN = [["wait", 40], ["snap", "home"], ["click", "maintab-usage"], ["wait", 40],
        ["snap", "open"]]
TO_SESSIONS = [["click", "maintab-sessions"], ["wait", 20], ["snap", "sessions"]]


@pytest.fixture(scope="module")
def node_bin():
    exe = shutil.which("node") or shutil.which("nodejs")
    if not exe:
        pytest.skip("node not available")
    return exe


@pytest.fixture
def world(make_bot, tmp_path, h):
    bot = make_bot("scope")
    p = tmp_path / "t.jsonl"
    h.append(p, h.entry("msg_1", NOW - 30, 1234, 56, model="<b>claude-x</b>"))
    h.idle(h.add_session(bot.registry, "claude-api__g", scope=h.GROUP, transcript=p))
    usage_store.note_plan("frontend", {"seven_day": Window(83.0, int(NOW + 2 * 86400)),
                                       "five_hour": Window(7.0, int(NOW - 10))},
                          SessionLogin("saved", "", "/home/alice/.claude"), now=NOW)
    return bot


def _run(node_bin, tmp_path, h, bot, run_async, user, scope, steps, usage_answer=None):
    hdrs = h.headers(user, scope=scope)
    _s, page = h.get(bot, run_async, "/", hdrs)
    s1, sessions = h.get(bot, run_async, "/api/sessions", hdrs)
    s2, usage = h.get(bot, run_async, "/api/usage", hdrs)
    cfg = {"answers": {"/api/sessions": {"status": s1, "body": sessions},
                       "/api/usage": usage_answer or {"status": s2, "body": usage}},
           "steps": steps}
    page_file = tmp_path / "page.html"
    cfg_file = tmp_path / "cfg.json"
    page_file.write_text(page, encoding="utf-8")
    cfg_file.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run([node_bin, str(HARNESS), str(page_file), str(cfg_file)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture
def owner_run(node_bin, tmp_path, h, world, run_async):
    def _go(steps=OPEN, **kw):
        return _run(node_bin, tmp_path, h, world, run_async, h.OWNER, h.OWNER, steps, **kw)
    return _go


def test_the_page_runs_without_errors(owner_run):
    assert owner_run()["errors"] == []


def test_the_usage_tab_shows_for_an_admin(owner_run):
    assert owner_run()["snaps"]["home"]["usageTabShown"] is True


def test_the_usage_tab_is_hidden_for_a_plain_member(node_bin, tmp_path, h, world, run_async):
    out = _run(node_bin, tmp_path, h, world, run_async, h.USER, h.GROUP, OPEN[:2])
    assert out["snaps"]["home"]["usageTabShown"] is False


def test_the_page_is_hidden_before_opening(owner_run):
    assert owner_run()["snaps"]["home"]["usageView"] is False


def test_tapping_the_tab_opens_the_usage_page(owner_run):
    assert owner_run()["snaps"]["open"]["usageView"] is True


def test_opening_hides_the_grid(owner_run):
    assert owner_run()["snaps"]["open"]["gridView"] is False


def test_opening_fetches_api_usage(owner_run):
    assert any(f.startswith("GET /api/usage") for f in owner_run()["fetched"])


def test_the_usage_tab_has_no_telegram_back_button(owner_run):
    assert owner_run()["snaps"]["open"]["back"] is False


def test_the_usage_tab_keeps_the_tab_bar_and_shows_active(owner_run):
    out = owner_run()["snaps"]["open"]
    assert (out["tabbar"], out["usageTabActive"], out["sessionsTabActive"]) == (True, True, False)


def test_the_page_shows_the_window_line(owner_run):
    assert "This week (all models): 83% used" in owner_run()["snaps"]["open"]["body"]


def test_the_page_shows_the_reset_window(owner_run):
    assert "5-hour window: reset since the last reading" in owner_run()["snaps"]["open"]["body"]


def test_the_page_shows_the_freshness_line(owner_run):
    assert "from session frontend." in owner_run()["snaps"]["open"]["body"]


def test_only_the_ok_window_gets_a_bar(owner_run):
    assert owner_run()["snaps"]["open"]["fills"] == ["83"]


def test_the_page_shows_the_token_table(owner_run):
    body = owner_run()["snaps"]["open"]["body"]
    assert ("Last 7 days" in body, "1.2k" in body, "Cache write" in body) == (True, True, True)


def test_the_page_shows_by_day_and_by_model(owner_run):
    body = owner_run()["snaps"]["open"]["body"]
    assert ("By day" in body, "By model, last 7 days" in body) == (True, True)


def test_a_model_id_with_markup_stays_text(owner_run):
    out = owner_run()["snaps"]["open"]
    assert ("<b>claude-x</b>" in out["body"], "<b>claude-x" in out["html"]) == (True, False)


def test_no_dollar_on_the_page(owner_run):
    assert "$" not in owner_run()["snaps"]["open"]["body"]


def test_the_sessions_tab_returns_to_the_grid(owner_run):
    out = owner_run(OPEN + TO_SESSIONS)["snaps"]["sessions"]
    assert (out["gridView"], out["usageView"], out["sessionsTabActive"],
            out["usageTabActive"]) == (True, False, True, False)


def test_reopening_the_tab_fetches_usage_again(owner_run):
    out = owner_run(OPEN + TO_SESSIONS + [["click", "maintab-usage"], ["wait", 40]])
    assert sum(f.startswith("GET /api/usage") for f in out["fetched"]) == 2


def test_a_bar_over_100_is_capped(node_bin, tmp_path, h, world, run_async):
    usage_store.note_plan("gw", {"spend_limit": Window(150.0, int(time.time() + 3600))},
                          SessionLogin("gateway", "gw.example.com", "/home/alice/.claude"),
                          now=NOW)
    out = _run(node_bin, tmp_path, h, world, run_async, h.OWNER, h.OWNER, OPEN)
    assert "100" in out["snaps"]["open"]["fills"] and "150" not in out["snaps"]["open"]["fills"]


def test_a_failed_fetch_never_throws(owner_run):
    out = owner_run(usage_answer="reject")
    assert out["errors"] == []


def test_a_403_never_throws(owner_run):
    out = owner_run(usage_answer={"status": 403, "body": {"error": "forbidden"}})
    assert out["errors"] == []


def test_a_403_hides_the_tab_and_returns_to_sessions(owner_run):
    out = owner_run(usage_answer={"status": 403, "body": {"error": "forbidden"}})["snaps"]["open"]
    assert (out["usageTabShown"], out["gridView"], out["usageView"],
            out["sessionsTabActive"]) == (False, True, False, True)


def test_a_failed_fetch_returns_to_sessions_and_keeps_the_tab(owner_run):
    out = owner_run(usage_answer="reject")["snaps"]["open"]
    assert (out["usageTabShown"], out["gridView"], out["sessionsTabActive"]) == (True, True, True)
