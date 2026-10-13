"""Page-wide rules for the served Mini App (roadmap 8.44).

The page is self-contained: it talks to nothing but its own origin (the
one exception is the fallback variant's Telegram SDK tag, used only when
the daemon has no cached copy), carries no web fonts, imports or remote
images, stays under a byte budget, and has no em dash anywhere (operator
text rule; server strings are normalised at display time by `plain()`).
"""

from __future__ import annotations

import gzip
import re

import pytest

from aipager.miniapp.static import SDK_SRC_TELEGRAM, SOURCE_INDEX_HTML, index_html

# Roadmap 8.117: the page goes over the wire gzipped (every Telegram in-app
# browser takes gzip), so that is the budget that counts. Both budgets are
# for the page as served, without its comments and indentation, and keep
# about a tenth over the largest language (Russian, 2026-10-13: 237,623
# bytes built, 56,810 sent), so a growing page is noticed in time.
SENT_BUDGET_BYTES = 63_000
# A ceiling on the page as built: what a client that takes no gzip gets,
# and a stop for an accidental embed (an image, a data file).
PAGE_BUDGET_BYTES = 262_000

SELF = index_html(sdk_from_self=True)
FALLBACK = index_html(sdk_from_self=False)
# The rules about what the page may say hold for the page as written too:
# its comments are not served, but they are read.
PAGES = [SELF, FALLBACK, SOURCE_INDEX_HTML]
PAGE_IDS = ["self-served", "fallback", "as-written"]


@pytest.mark.parametrize("page", [SELF, FALLBACK], ids=["self-served", "fallback"])
def test_the_page_stays_under_its_byte_budget(page):
    size = len(page.encode("utf-8"))
    assert size <= PAGE_BUDGET_BYTES, f"page is {size} bytes"


@pytest.mark.parametrize("page", [SELF, FALLBACK], ids=["self-served", "fallback"])
def test_the_page_sent_stays_under_its_byte_budget(page):
    size = len(gzip.compress(page.encode("utf-8"), compresslevel=9, mtime=0))
    assert size <= SENT_BUDGET_BYTES, f"page sent is {size} bytes"


@pytest.mark.parametrize("page", [SELF, SOURCE_INDEX_HTML], ids=["self-served", "as-written"])
def test_the_self_served_page_names_no_external_url_at_all(page):
    """Not in a src or href, not in a fetch string, not in a comment."""
    assert "http://" not in page
    assert "https://" not in page


def test_the_fallback_names_exactly_the_telegram_sdk():
    urls = re.findall(r"https?://[^\s\"'<>)]+", FALLBACK)
    assert urls == [SDK_SRC_TELEGRAM]
    assert FALLBACK.replace(SDK_SRC_TELEGRAM, "/telegram-web-app.js") == SELF


@pytest.mark.parametrize("page", [SELF, FALLBACK], ids=["self-served", "fallback"])
def test_no_imports_fonts_or_remote_urls(page):
    assert "@import" not in page
    assert "@font-face" not in page
    for m in re.finditer(r"url\(", page):
        assert page[m.end()] == "#", f"url() that is not a fragment: {page[m.start():m.start() + 40]!r}"


def test_the_page_fetches_only_its_own_api():
    """Every fetch call site passes a path under /api/ (or a variable
    built from one); the JS harness asserts the same at run time."""
    literal = re.findall(r"(?:fetch|apiFetch|updatesFetch)\(\s*\"([^\"]*)\"", SELF)
    assert literal, "found no fetch call site, the extraction is blind"
    assert all(p.startswith("/api/") for p in literal), literal


@pytest.mark.parametrize("page", PAGES, ids=PAGE_IDS)
def test_the_page_carries_no_em_dash(page):
    assert "—" not in page


def test_the_sprite_needs_no_xmlns():
    """Inline SVG in HTML needs no namespace, and an xmlns would be the
    one http URL in the self-served page."""
    assert "xmlns" not in SELF


def test_every_icon_the_script_uses_is_in_the_sprite():
    symbols = set(re.findall(r'<symbol id="i-([\w-]+)"', SELF))
    used = set(re.findall(r'icon\("([\w-]+)"', SELF))
    order = re.search(r"var ACTION_ORDER = \[([^\]]*)\]", SELF)
    used |= set(re.findall(r'"(\w+)"', order.group(1)))     # icon(key) in the menu
    used |= {"stop", "resume"}                                # icon(key) on the quick pill
    missing = sorted(used - symbols)
    assert not missing, f"icons with no symbol: {missing}"


def test_every_icon_reference_is_local():
    refs = re.findall(r'<use href="([^"]*)"', SELF)
    assert refs and all(r.startswith("#i-") for r in refs), refs


def test_icon_only_buttons_have_a_label():
    """A button whose only content is an SVG must say what it does."""
    for tag, body in re.findall(r"(<button[^>]*>)(.*?)</button>", SELF, re.DOTALL):
        text = re.sub(r"<[^>]+>", "", body).strip()
        if "<svg" in body and not text:
            assert "aria-label=" in tag, f"icon-only button with no label: {tag}"
