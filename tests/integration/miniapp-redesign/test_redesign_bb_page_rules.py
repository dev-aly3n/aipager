"""Page text rules for the served Mini App (design.md success criteria 1,
2, 3, 14; entrypoints.md "Exported functions" and "Page contract").

The page is only inspected as the served text, through ``index_html`` and
``GET /``.
"""

from __future__ import annotations

import re

import pytest

from aipager.miniapp import static
from aipager.miniapp.static import (
    INDEX_HTML,
    SDK_SRC_SELF,
    SDK_SRC_TELEGRAM,
    index_html,
)

from miniapp_redesign_bb import client_for  # noqa: E402 - alias set by conftest

TELEGRAM_SDK = "https://telegram.org/js/telegram-web-app.js"
BUDGET = 200_000


@pytest.fixture(scope="module")
def self_page():
    return index_html(sdk_from_self=True)


@pytest.fixture(scope="module")
def fallback_page():
    return index_html(sdk_from_self=False)


# ── criterion 1: no external URLs ──────────────────────────────────────────

def test_self_served_page_has_no_https(self_page):
    assert "https://" not in self_page


def test_self_served_page_has_no_http(self_page):
    assert "http://" not in self_page


def test_self_served_page_has_no_protocol_relative_urls(self_page):
    """Error guess: ``//host/x`` in a src/href/url() is still external."""
    hits = re.findall(r"""(?:src|href)\s*=\s*["']//|url\(\s*["']?//""",
                      self_page)
    assert hits == []


def test_fallback_page_has_exactly_one_https(fallback_page):
    assert fallback_page.count("https://") == 1


def test_fallback_page_https_is_the_telegram_sdk(fallback_page):
    assert TELEGRAM_SDK in fallback_page


def test_fallback_page_has_no_http(fallback_page):
    assert "http://" not in fallback_page


def test_fallback_sdk_url_sits_in_a_script_tag(fallback_page):
    assert re.search(r'<script[^>]*src="' + re.escape(TELEGRAM_SDK) + '"',
                     fallback_page)


def test_variants_differ_only_in_the_sdk_src(self_page, fallback_page):
    assert fallback_page.replace(TELEGRAM_SDK, SDK_SRC_SELF) == self_page


def test_index_html_constant_is_the_self_served_variant(self_page):
    assert INDEX_HTML == self_page


def test_sdk_src_constants_are_unchanged():
    assert (SDK_SRC_SELF, SDK_SRC_TELEGRAM) == ("/telegram-web-app.js",
                                                 TELEGRAM_SDK)


def test_no_web_fonts(self_page):
    assert "@font-face" not in self_page


def test_page_script_ends_with_the_iife_close(self_page):
    scripts = re.findall(r"<script>([\s\S]*?)</script>", self_page)
    assert scripts and scripts[-1].rstrip().endswith("})();")


# ── criterion 2: weight ────────────────────────────────────────────────────

@pytest.mark.parametrize("variant", [True, False])
def test_page_is_within_the_200k_byte_ceiling(variant):
    assert len(index_html(sdk_from_self=variant).encode("utf-8")) <= BUDGET


# ── criterion 3: no em dash ────────────────────────────────────────────────

@pytest.mark.parametrize("variant", [True, False])
def test_page_contains_no_em_dash(variant):
    assert "\u2014" not in index_html(sdk_from_self=variant)


def test_page_has_no_html_entity_em_dash(self_page):
    """Error guess: ``&mdash;`` / ``&#8212;`` renders as an em dash."""
    assert not re.search(r"&mdash;|&#8212;|&#x2014;", self_page, re.I)


# ── GET / ──────────────────────────────────────────────────────────────────

def _get_root(server, run_async):
    async def _run():
        client = await client_for(server)
        try:
            resp = await client.get("/")
            return resp.status, resp.headers.get("Content-Type", ""), await resp.text()
        finally:
            await client.close()
    return run_async(_run())


def test_root_returns_200(server, run_async):
    assert _get_root(server, run_async)[0] == 200


def test_root_is_html(server, run_async):
    assert _get_root(server, run_async)[1].startswith("text/html")


def test_root_serves_one_of_the_two_variants(server, run_async):
    body = _get_root(server, run_async)[2]
    assert body in (index_html(sdk_from_self=True),
                    index_html(sdk_from_self=False))


def test_root_needs_no_auth(server, run_async):
    """GET / is unchanged: no initData header, still 200."""
    assert _get_root(server, run_async)[0] == 200


# ── page contract: ids, labels ─────────────────────────────────────────────

NEW_IDS = [
    "grid-totals", "daemon-line", "conn-badge", "tabbar", "waiting-badge",
    "new-session-btn", "needs-you", "needs-you-list", "sessions", "gone-wrap",
    "gone-toggle", "sessions-gone", "empty-state", "empty-new", "error",
    "notice", "detail-label", "detail-status", "detail-ring", "detail-state",
    "detail-quick", "detail-menu-btn", "action-menu", "detail-waiting",
    "detail-answer", "detail-activity", "detail-facts", "detail-model",
    "detail-model-note", "detail-preview", "session-settings-groups",
    "session-settings-reset", "tab-diff", "panel-diff", "tab-timeline",
    "panel-timeline", "new-name", "new-cwd", "new-model", "new-create",
    "new-cwd-chips", "new-model-chips", "settings-groups", "settings-readonly",
    "updates-block", "view-grid", "view-settings", "view-detail", "view-new",
]


@pytest.mark.parametrize("element_id", NEW_IDS)
def test_contract_id_is_present_once(self_page, element_id):
    assert len(re.findall(r'\bid="' + re.escape(element_id) + '"',
                          self_page)) == 1


def _tag(page, element_id):
    m = re.search(r'<[^>]*\bid="' + re.escape(element_id) + r'"[^>]*>', page)
    assert m, element_id
    return m.group(0)


def test_new_session_icon_button_has_aria_label(self_page):
    assert 'aria-label="New session"' in _tag(self_page, "new-session-btn")


def test_session_actions_kebab_has_aria_label(self_page):
    assert 'aria-label="Session actions"' in _tag(self_page, "detail-menu-btn")


def test_needs_you_tray_starts_hidden(self_page):
    assert re.search(r"\shidden(\s|>|=)", _tag(self_page, "needs-you"))


def test_error_has_role_alert(self_page):
    assert 'role="alert"' in _tag(self_page, "error")


def test_notice_has_role_status(self_page):
    assert 'role="status"' in _tag(self_page, "notice")


def test_exactly_four_views(self_page):
    ids = re.findall(r'<section[^>]*\bid="(view-[a-z]+)"', self_page)
    assert sorted(ids) == ["view-detail", "view-grid", "view-new",
                           "view-settings"]


def test_no_inline_event_handlers(self_page):
    """Error guess: onclick= attributes break the no-inline-handler rule."""
    assert not re.search(r"<[^>]+\son[a-z]+\s*=", self_page)


def test_module_exposes_the_documented_names():
    assert all(hasattr(static, n) for n in ("index_html", "INDEX_HTML",
                                            "SDK_SRC_SELF", "SDK_SRC_TELEGRAM"))

