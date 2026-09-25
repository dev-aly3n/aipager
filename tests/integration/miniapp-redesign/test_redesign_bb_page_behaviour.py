"""Behaviour of the served page, driven in node through ``bb_page.js``
(design.md success criteria 4, 5, 6, 9, 10, 11, 12, 13, 16; entrypoints.md
"Page contract", "'Answer in chat' behaviour", "Telegram WebApp
interactions" and "Side effects").

Each scenario runs the real page once against fixture API data and a fake
``Telegram.WebApp``; each test below asserts exactly one named check from
that run. A check missing from the run fails its test, so a scenario that
silently stopped reaching a step cannot pass.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aipager.miniapp.static import index_html

HARNESS = Path(__file__).parent / "bb_page.js"

# Checks every scenario reports, whatever it drives.
COMMON = ["script_ends_with_iife", "only_api_requests", "no_uncaught_errors"]

EXPECTED = {
    "grid_mixed": [
        "hooks_exported", "boot_fetches_grid", "tray_visible",
        "tray_one_card_per_waiting", "tray_answer_buttons", "tray_labels",
        "tray_kind_permission", "tray_kind_question", "tray_summary_plain",
        "tray_no_em_dash", "grid_tiles_live_only", "grid_tiles_are_buttons",
        "grid_waiting_not_in_tiles", "grid_gone_not_in_tiles", "tile_order",
        "tile_state_working", "tile_state_resting", "tile_shows_project",
        "tile_shows_model", "tile_shows_cost", "tile_ring_aria_has_pct",
        "shelf_visible", "shelf_toggle_count", "shelf_collapsed",
        "shelf_holds_gone", "pulse_need", "pulse_working", "pulse_resting",
        "waiting_badge", "empty_hidden", "grid_poll_2500",
        "no_interval_faster_than_2500", "tray_card_opens_detail",
        "shelf_toggle_opens",
    ],
    "grid_keyed": [
        "identical_poll_keeps_tile", "identical_poll_keeps_beacon",
        "changed_poll_reuses_changed_tile", "changed_poll_reuses_other_tile",
        "changed_poll_updates_ring", "waiting_moves_to_tray",
    ],
    "grid_reorder": ["reorder_follows_server", "reorder_keeps_nodes"],
    "grid_reorder_flip": ["reorder_follows_server", "reorder_keeps_nodes",
                          "flip_one_raf"],
    "grid_reorder_reduced": ["reorder_follows_server", "reorder_keeps_nodes",
                             "reduced_motion_no_raf"],
    "grid_none_waiting": ["tray_hidden", "badge_hidden", "tiles_two",
                          "shelf_hidden"],
    "grid_empty": ["empty_visible", "tray_hidden", "empty_new_opens_form"],
    "grid_new_button": ["new_button_opens_form", "new_button_fetches_options"],
    "answer_single": ["answer_button_found", "one_post", "closed",
                      "success_haptic"],
    "answer_no_close_api": ["answer_button_found", "one_post"],
    "answer_multi": ["answer_button_found", "one_post", "not_closed",
                     "toast_sent", "success_haptic", "stays_on_grid"],
    "answer_double": ["answer_button_found", "disabled_in_flight",
                      "one_post_in_flight", "one_post_hook_in_flight"],
    "answer_409": ["answer_button_found", "notice_shows_detail_plain",
                   "notice_no_em_dash", "not_closed", "button_reenabled"],
    "answer_403": ["answer_button_found", "notice_cant_answer",
                   "not_expired_badge", "not_expired_error", "not_closed",
                   "still_polls"],
    # error guessing (iteration 2): every failed answer shows a toast,
    # re-enables the button and leaves the app usable
    "answer_502": ["answer_button_found", "one_post", "notice_shown",
                   "notice_shows_detail_plain", "notice_no_em_dash",
                   "no_success_haptic", "not_closed", "button_reenabled",
                   "not_expired"],
    "answer_503": ["answer_button_found", "one_post", "notice_shown",
                   "notice_shows_detail_plain", "notice_no_em_dash",
                   "no_success_haptic", "not_closed", "button_reenabled",
                   "not_expired"],
    "answer_500_html": ["answer_button_found", "one_post", "notice_shown",
                        "notice_no_em_dash", "no_success_haptic", "not_closed",
                        "button_reenabled", "not_expired"],
    "answer_504_empty": ["answer_button_found", "one_post", "notice_shown",
                         "notice_no_em_dash", "no_success_haptic", "not_closed",
                         "button_reenabled", "not_expired"],
    "answer_network_down": ["answer_button_found", "one_post", "notice_shown",
                            "notice_no_em_dash", "no_success_haptic",
                            "not_closed", "button_reenabled", "not_expired"],
    "answer_viewer": ["buttons_present", "buttons_disabled", "no_post"],
    "detail_waiting": [
        "mb_text", "mb_visible", "waiting_card_visible",
        "detail_answer_visible", "state_line", "quick_stop",
        "mb_click_posts_answer", "mb_hidden_with_menu_open",
        "swipes_off_with_overlay", "back_closes_menu_first",
        "back_returns_to_grid", "mb_hidden_on_grid",
    ],
    "detail_answer_button": ["detail_answer_posts"],
    "detail_waiting_viewer": ["mb_not_visible", "detail_answer_not_actionable",
                              "no_post"],
    "detail_busy": [
        "mb_not_visible", "state_working", "waiting_card_hidden", "quick_stop",
        "activity_visible", "activity_at_most_8", "activity_has_latest",
        "activity_drops_oldest", "ring_pct", "label",
        "detail_poll_refetches_detail", "quick_posts_stop_once",
        "quick_no_confirm",
    ],
    "detail_gone": ["state_finished", "quick_resume", "quick_posts_resume"],
    "detail_idle": ["state_resting", "quick_hidden"],
    "tile_open": ["tile_opens_detail", "impact_light",
                  "mb_hidden_non_waiting_detail"],
    "new_form": [
        "form_visible", "mb_text", "mb_visible", "swipes_disabled",
        "no_session_poll_on_new", "mb_inactive_when_invalid",
        "mb_active_when_valid", "mb_mirrors_create_button",
        "cwd_chips_visible", "cwd_chip_for_recent_project",
        "cwd_chip_skips_unrelated", "model_chip_with_hint",
        "model_chip_skips_hintless", "chip_selection_haptic",
        "mb_click_creates", "chip_cwd_posted",
    ],
    "new_form_leave": ["left_form", "swipes_reenabled", "mb_hidden_after_leave"],
    "new_form_minimal": ["in_page_create_posts"],
    "settings_tab": ["settings_visible", "mb_hidden",
                     "no_session_poll_on_settings"],
    "theme_changed": ["header_color", "background_color", "handler_registered",
                      "scheme_dark", "scheme_light_again"],
}

CASES = [(s, c) for s, checks in EXPECTED.items() for c in COMMON + checks]


@pytest.fixture(scope="module")
def node_bin():
    exe = shutil.which("node") or shutil.which("nodejs")
    if not exe:
        pytest.skip("node not available; page behaviour tests skipped")
    return exe


def _drive(node_bin, page_path, scenario):
    proc = subprocess.run(
        [node_bin, str(HARNESS), str(page_path), scenario],
        capture_output=True, text=True, timeout=60,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT "):])
    raise AssertionError(
        f"scenario {scenario} produced no result (exit {proc.returncode})\n"
        f"stdout: {proc.stdout[-2000:]}\nstderr: {proc.stderr[-2000:]}")


@pytest.fixture(scope="module")
def page_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("bbpage") / "page.html"
    path.write_text(index_html(sdk_from_self=True), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def results(node_bin, page_path):
    cache: dict = {}

    def _get(scenario):
        if scenario not in cache:
            cache[scenario] = _drive(node_bin, page_path, scenario)
        return cache[scenario]
    return _get


@pytest.mark.parametrize("scenario, check", CASES,
                         ids=[f"{s}::{c}" for s, c in CASES])
def test_page_behaviour(results, scenario, check):
    got = results(scenario)
    assert got.get(check, {}).get("ok") is True, got.get(check, "check not run")


# ── guard the guard: the harness must notice a broken page ──────────────────

def _mutated(tmp_path, old, new):
    page = index_html(sdk_from_self=True)
    assert old in page, f"mutation target {old!r} not found"
    path = tmp_path / "mutated.html"
    path.write_text(page.replace(old, new), encoding="utf-8")
    return path


def test_harness_sees_a_missing_sent_toast(node_bin, tmp_path):
    path = _mutated(tmp_path, "Sent to the chat", "Posted")
    assert _drive(node_bin, path, "answer_multi")["toast_sent"]["ok"] is False


def test_harness_sees_a_missing_403_notice(node_bin, tmp_path):
    path = _mutated(tmp_path, "You can't answer prompts in this chat.",
                    "Nope.")
    assert _drive(node_bin, path, "answer_403")["notice_cant_answer"]["ok"] is False


def test_harness_sees_an_external_fetch(node_bin, tmp_path):
    """The "only /api/" check is live: a page that fetches elsewhere fails."""
    page = index_html(sdk_from_self=True)
    marker = "})();"
    idx = page.rfind(marker)
    broken = page[:idx] + 'fetch("/elsewhere");\n' + page[idx:]
    path = tmp_path / "external.html"
    path.write_text(broken, encoding="utf-8")
    assert _drive(node_bin, path, "grid_none_waiting")["only_api_requests"]["ok"] is False
