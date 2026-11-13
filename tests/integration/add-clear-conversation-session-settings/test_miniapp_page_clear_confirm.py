"""The Mini App page asks in the app before clearing: design.md success
criterion 13.

Runs the page's real JavaScript under node with the DOM shim and the fetch
recorder of tests/js/miniapp_smoke.js. This file does not use that
harness's scenarios: it takes the shim part (everything before the
harness's own drivers) and appends its own driver, so what is asserted
here is written from the spec alone. Skips cleanly without node, as the
existing JS tests do.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[2] / "js" / "miniapp_smoke.js"
CUT = "\nfunction fail(msg)"

DETAIL = {
    "label": "dev", "status": "idle", "waiting_kind": None, "waiting_summary": None,
    "model": "", "context_pct": 0, "cost_usd": 0, "cwd": "",
    "last_active_seconds_ago": None, "busy_elapsed_seconds": None,
    "last_message": "", "timeline": [], "facts": [],
    "actions": {
        "compact": {"available": True, "reason": None},
        "rename": {"available": True, "reason": None},
        "clear": {"available": True, "reason": None},
        "kill": {"available": True, "reason": None},
        "restart": {"available": True, "reason": None},
    },
}

DRIVER = r"""
function fail(msg) { console.error("FAIL: " + msg); process.exit(1); }
const api = global.__api;
const MODE = process.argv[4] || "confirm";
FIXTURES["/api/sessions/dev"] = __DETAIL__;
FIXTURES["/api/sessions/dev/clear"] = { status: "sent", label: "dev" };
if (MODE === "refused") {
  POST_STATUS_OVERRIDE = { path: "/api/sessions/dev/clear", method: "POST", status: 409,
    body: { error: "busy",
            detail: "Claude is working - clear the conversation when this turn ends." } };
}
function items(menu) { return menu.children.filter(c => c.className.includes("menu-item")); }
function report(obj) { console.log("RESULT " + JSON.stringify(obj)); process.exit(0); }

api.openDetail("dev");
setTimeoutReal(() => {
  const kebab = byId["detail-menu-btn"];
  kebab.click();
  const menu = byId["action-menu"];
  const kids = menu.children.map(c => ({ cls: c.className, text: c.textContent }));
  const item = items(menu).find(i => i.textContent === "Clear conversation");
  const out = { kids: kids };
  if (!item) { report(out); return; }
  const before = fetchCalls.length;
  item.click();
  out.requests_after_tap = fetchCalls.length - before;
  out.modal_open = !byId["overlay"].hidden && !byId["confirm-modal"].hidden;
  out.title = byId["confirm-title"].textContent;
  out.body = byId["confirm-body"].textContent;
  out.ok_label = byId["confirm-ok"].textContent;
  out.cancel_focused = !!byId["confirm-cancel"].focused;
  if (MODE === "cancel") {
    byId["confirm-cancel"].click();
    setTimeoutReal(() => {
      out.sent = fetchCalls.slice(before).map(f => [f.method, f.url]);
      report(out);
    }, 20);
    return;
  }
  byId["confirm-ok"].click();
  out.sent = fetchCalls.slice(before).filter(f => f.method !== "GET")
    .map(f => [f.method, f.url]);
  setTimeoutReal(() => {
    out.notice = byId["notice"].textContent || "";
    report(out);
  }, 30);
}, 10);
"""


@pytest.fixture(scope="module")
def node_bin():
    exe = shutil.which("node") or shutil.which("nodejs")
    if not exe:
        pytest.skip("node not available - JS test skipped")
    return exe


def _drive(node_bin, tmp_path, mode="confirm", html=None):
    from aipager.miniapp.static import INDEX_HTML
    source = HARNESS.read_text(encoding="utf-8")
    assert CUT in source, "the harness changed shape: its drivers no longer start at fail()"
    shim = source[:source.index(CUT)]
    driver = tmp_path / "clear_driver.js"
    driver.write_text(shim + DRIVER.replace("__DETAIL__", json.dumps(DETAIL)),
                      encoding="utf-8")
    page = tmp_path / "page.html"
    page.write_text(html if html is not None else INDEX_HTML, encoding="utf-8")
    # "kill_idle" only picks the harness's idle detail fixture, replaced above.
    proc = subprocess.run([node_bin, str(driver), str(page), "kill_idle", mode],
                          capture_output=True, text=True, timeout=60)
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")), None)
    assert proc.returncode == 0 and line, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    return json.loads(line[len("RESULT "):])


@pytest.fixture(scope="module")
def confirmed(node_bin, tmp_path_factory):
    return _drive(node_bin, tmp_path_factory.mktemp("clear-confirm"))


def test_menu_offers_clear_conversation(confirmed):
    assert "Clear conversation" in [k["text"] for k in confirmed["kids"]]


def test_clear_conversation_sits_right_after_the_divider(confirmed):
    kids = confirmed["kids"]
    at = [k["text"] for k in kids].index("Clear conversation")
    assert "divider" in kids[at - 1]["cls"]


def test_clear_conversation_sits_after_rename_and_before_kill(confirmed):
    texts = [k["text"] for k in confirmed["kids"] if "menu-item" in k["cls"]]
    at = texts.index("Clear conversation")
    assert (texts[at - 1], texts[at + 1]) == ("Rename", "Kill")


def test_tapping_clear_sends_no_request(confirmed):
    assert confirmed["requests_after_tap"] == 0


def test_tapping_clear_opens_the_in_app_confirm(confirmed):
    assert confirmed["modal_open"] is True


def test_confirm_title_names_the_session(confirmed):
    assert confirmed["title"] == "Clear the conversation in dev?"


def test_confirm_body_says_what_happens(confirmed):
    assert confirmed["body"] == (
        "Claude starts a new conversation and forgets this one, and the old one stays "
        "saved on the computer but can't be resumed from here.")


def test_confirm_button_says_clear_conversation(confirmed):
    assert confirmed["ok_label"] == "Clear conversation"


def test_confirm_focuses_cancel(confirmed):
    assert confirmed["cancel_focused"] is True


def test_confirm_ok_sends_exactly_one_post_to_clear(confirmed):
    assert confirmed["sent"] == [["POST", "/api/sessions/dev/clear"]]


def test_success_notice_says_claude_starts_a_new_conversation(confirmed):
    assert "Claude is starting a new conversation." in confirmed["notice"]


def test_cancel_sends_nothing(node_bin, tmp_path):
    assert _drive(node_bin, tmp_path, mode="cancel")["sent"] == []


def test_refused_clear_shows_the_servers_detail(node_bin, tmp_path):
    out = _drive(node_bin, tmp_path, mode="refused")
    assert "clear the conversation when this turn ends" in out["notice"]


def test_driver_detects_a_clear_that_skips_the_confirm(node_bin, tmp_path):
    """Guard the guard: a page whose Clear acts at once fails the driver."""
    from aipager.miniapp.static import SOURCE_INDEX_HTML
    literal = "restart: true, clear: true }"
    assert literal in SOURCE_INDEX_HTML, "CONFIRM_ACTIONS changed shape"
    broken = SOURCE_INDEX_HTML.replace(literal, "restart: true }", 1)
    out = _drive(node_bin, tmp_path, html=broken)
    assert out["requests_after_tap"] != 0 or out["modal_open"] is False
