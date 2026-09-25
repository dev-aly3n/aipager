"""Server-owned text and the offline / expired states, through the page.

design.md success criterion 3 and entrypoints.md "Text rules": server
strings (schema labels and hints, choices, reasons, details, model hints,
waiting summaries) are displayed with " — " replaced by " - ". The payloads
fed to the page here are the REAL bodies of the public GET routes (built by
the daemon's own route handlers with the shipped settings schema and model
catalog, which carry em dashes today), with extra em dashes placed in the
fields the page renders. The harness then walks every displayed text node
and the aria-label/title/placeholder attributes of the Settings tab, the
session page (with its action menu and session settings) and the new
session form.

The offline and expired states must not offer the "+" New session action
(reviewer finding, iteration 1).
"""

from __future__ import annotations

import json

import pytest

from aipager.state import Status

from miniapp_redesign_bb import (  # noqa: E402 - alias set by conftest
    ADMIN_ID,
    READONLY_ID,
    client_for,
    drive,
    hdr,
    mk_session,
    put_on_permission,
)

EM = "\u2014"

# Checks every harness scenario reports.
COMMON = ["script_ends_with_iife", "only_api_requests", "no_uncaught_errors"]

EXPECTED = {
    "emdash_settings": ["settings_visible", "groups_expanded",
                        "schema_label_shown_plain",
                        "schema_help_shown_plain", "settings_no_em_dash"],
    "emdash_settings_save_error": ["option_found", "put_sent", "no_em_dash"],
    "emdash_detail_waiting": ["detail_visible", "groups_expanded",
        "summary_shown_plain",
                              "model_reason_shown_plain",
                              "session_schema_shown_plain", "detail_no_em_dash"],
    "emdash_detail_viewer": ["detail_visible", "groups_expanded",
        "answer_reason_shown_plain",
                             "detail_no_em_dash"],
    "emdash_detail_busy_menu": ["detail_visible", "groups_expanded",
        "menu_open",
                                "action_reason_shown_plain", "detail_no_em_dash"],
    "emdash_new": ["form_visible", "groups_expanded",
        "model_hint_shown_plain",
                   "schema_label_shown_plain", "new_no_em_dash"],
    "emdash_new_create_error": ["form_visible", "groups_expanded",
        "create_sent",
                                "detail_shown_plain", "no_em_dash"],
    "offline_boot": ["state_offline", "plus_not_offered",
                     "empty_new_not_offered", "tap_does_not_open_form"],
    "offline_boot_empty": ["empty_new_offered_when_online", "state_offline",
                           "plus_not_offered", "empty_new_not_offered",
                           "tap_does_not_open_form"],
    "offline_after_load": ["plus_offered_when_online", "state_offline",
                           "plus_not_offered", "empty_new_not_offered",
                           "tap_does_not_open_form"],
    "offline_recover": ["state_offline", "plus_offered_again"],
    "expired_boot": ["state_expired", "plus_not_offered",
                     "empty_new_not_offered", "tap_does_not_open_form"],
    "expired_empty": ["empty_new_offered_when_online", "plus_not_offered",
                      "empty_new_not_offered", "tap_does_not_open_form"],
    "expired_after_load": ["state_expired", "plus_not_offered",
                           "empty_new_not_offered", "tap_does_not_open_form"],
}

CASES = [(s, c) for s, checks in EXPECTED.items() for c in COMMON + checks]

# Which caller's view each em-dash scenario renders.
VIEWER = {"emdash_detail_viewer"}


def _real_payloads(server, run_async, user_id):
    """GET every route the page renders text from, as ``user_id``."""
    put_on_permission(mk_session(server, "alpha"),
                      f"Bash: rm -rf build {EM} then deploy")
    mk_session(server, "bravo", status=Status.BUSY)
    paths = ["/api/sessions", "/api/sessions/alpha", "/api/sessions/bravo",
             "/api/preferences", "/api/sessions/alpha/preferences",
             "/api/sessions/bravo/preferences", "/api/session-options"]

    async def _run():
        client = await client_for(server)
        out = {}
        try:
            for p in paths:
                resp = await client.get(p, headers=hdr(user_id))
                assert resp.status == 200, (p, resp.status)
                out["GET " + p] = await resp.json()
        finally:
            await client.close()
        return out
    return run_async(_run())


def _with_more_dashes(fix):
    """Put an em dash into every schema title too (labels and hints already
    carry them in the shipped schema)."""
    for key in ("GET /api/preferences", "GET /api/sessions/alpha/preferences",
                "GET /api/sessions/bravo/preferences", "GET /api/session-options"):
        for group in fix[key]["schema"]:
            group["title"] = f"{group['title']} {EM} tuned"
    return fix


def _fixture_for(scenario, server, run_async):
    user = READONLY_ID if scenario in VIEWER else ADMIN_ID
    fix = _with_more_dashes(_real_payloads(server, run_async, user))
    if scenario in VIEWER:
        fix["GET /api/sessions/alpha"]["answer"]["reason"] = (
            f"Read-only {EM} ask an admin")
    fix["PUT /api/preferences/layout"] = {
        "status": 409, "body": {"error": "conflict",
                                "detail": f"Admins only {EM} ask one"}}
    fix["POST /api/sessions"] = {
        "status": 409, "body": {"error": "name_taken",
                                "detail": f"Name taken {EM} pick another"}}
    return fix


def test_real_payloads_carry_em_dashes(server, run_async):
    """Premise check: the shipped server text does contain em dashes, so
    the page-side replacement is what keeps them off the screen."""
    fix = _real_payloads(server, run_async, ADMIN_ID)
    assert EM in json.dumps(fix["GET /api/session-options"], ensure_ascii=False)


_CACHE: dict = {}


@pytest.mark.parametrize("scenario, check", CASES,
                         ids=[f"{s}::{c}" for s, c in CASES])
def test_server_text_and_offline(server, run_async, node_bin, served_page,
                                 tmp_path, scenario, check):
    if scenario not in _CACHE:
        fix_path = None
        if scenario.startswith("emdash_"):
            fix_path = tmp_path / "fix.json"
            fix_path.write_text(json.dumps(_fixture_for(scenario, server, run_async)),
                                encoding="utf-8")
        _CACHE[scenario] = drive(node_bin, served_page, scenario, fix_path)
    got = _CACHE[scenario]
    assert got.get(check, {}).get("ok") is True, got.get(check, "check not run")
