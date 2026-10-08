"""The served page's report surface, black box (entrypoints.md "Page
surface (served by GET /)", design.md Success criteria 1-2 and the
"No em dash" guard). The page is read as GET / serves it.

Methods: equivalence partitioning over the page's parts (Settings card,
report view, result panel); error guessing: an em dash slipping into new
copy, a ``maxlength`` on the note (it counts UTF-16 units, not code
points), the removed route or note field still named."""

from __future__ import annotations

import re

import pytest


@pytest.fixture
def page(bot, h, run_async) -> str:
    return h.serve(bot, run_async, lambda api: api.page())


def _tag(page: str, el_id: str) -> str:
    m = re.search(r'<[a-z0-9]+\b[^>]*\bid="' + re.escape(el_id) + r'"[^>]*>', page)
    assert m, f"#{el_id} is not in the page"
    return m.group(0)


def _view(page: str) -> str:
    """The report view's markup: from its opening tag to its closing
    ``</section>``."""
    start = page.index('id="view-report"')
    return page[start:page.index("</section>", start)]


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment))


# ---- routes ----------------------------------------------------------------------

def test_page_names_the_draft_route(page):
    assert "/api/report/draft" in page


def test_page_names_the_send_route(page):
    assert "/api/report/send" in page


def test_page_no_longer_names_the_preview_route(page):
    assert "/api/report/preview" not in page


# ---- the Settings card ---------------------------------------------------------------

def test_settings_card_exists(page):
    assert _tag(page, "report-block")


def test_settings_button_reads_report_a_problem(page):
    m = re.search(r'id="report-open"[^>]*>(.*?)</button>', page, re.S)
    assert m and "Report a problem" in _text(m.group(1))


def test_settings_card_sub_line(page):
    assert ("Tell the maintainer what went wrong. You see the whole report before "
            "anything is sent.") in page


def test_old_report_note_is_gone(page):
    assert 'id="report-note"' not in page


# ---- the report view -------------------------------------------------------------------

def test_report_view_is_a_section(page):
    assert _tag(page, "view-report").startswith("<section")


def test_report_view_starts_hidden(page):
    assert re.search(r"\bhidden\b", _tag(page, "view-report"))


REQUIRED_IDS = [
    "rp-note", "rp-hint", "rp-count", "rp-status", "rp-facts", "rp-err-title",
    "rp-errors", "rp-noerr", "rp-more-btn", "rp-exact-btn", "rp-more-facts", "rp-exact",
    "rp-send", "rp-result", "rp-result-title", "rp-result-line", "rp-ref-row", "rp-ref",
    "rp-copy", "rp-copy-hint", "rp-done",
]


@pytest.mark.parametrize("el_id", REQUIRED_IDS)
def test_report_view_carries_id(page, el_id):
    assert f'id="{el_id}"' in _view(page)


@pytest.mark.parametrize("el_id", REQUIRED_IDS)
def test_every_report_id_is_unique(page, el_id):
    assert page.count(f'id="{el_id}"') == 1


def test_note_is_a_textarea(page):
    assert _tag(page, "rp-note").startswith("<textarea")


def test_note_has_no_maxlength(page):
    assert "maxlength" not in _tag(page, "rp-note").lower()


def test_note_placeholder(page):
    assert 'placeholder="What were you doing when it went wrong?"' in _tag(page, "rp-note")


def test_exact_block_is_a_pre(page):
    assert _tag(page, "rp-exact").startswith("<pre")


@pytest.mark.parametrize("el_id", ["rp-send", "rp-copy", "rp-done"])
def test_actions_are_buttons(page, el_id):
    assert _tag(page, el_id).startswith("<button")


@pytest.mark.parametrize("el_id, label", [
    ("rp-send", "Send report"),
    ("rp-copy", "Copy"),
    ("rp-done", "Done"),
])
def test_button_labels(page, el_id, label):
    m = re.search(r'id="' + el_id + r'"[^>]*>(.*?)</button>', page, re.S)
    assert m and _text(m.group(1)).strip() == label


def test_counter_starts_at_0_of_500(page):
    m = re.search(r'id="rp-count"[^>]*>(.*?)<', page, re.S)
    assert m and m.group(1).strip() == "0 / 500"


def test_hint_reads_sent_exactly_as_written(page):
    m = re.search(r'id="rp-hint"[^>]*>(.*?)<', page, re.S)
    assert m and m.group(1).strip() == "Sent exactly as written."


PRIVACY = ("It holds versions, counts and places in aipager's code. Never your chats, "
           "prompts, names or paths.")
FOOT = ("Nothing is sent until you tap Send report. It goes to the maintainer's error "
        "inbox, without your name.")


@pytest.mark.parametrize("copy", [
    "Report a problem", PRIVACY, "Your note (optional)", "What gets sent",
    FOOT,
], ids=["title", "privacy", "label", "what-gets-sent", "foot"])
def test_report_view_copy(page, copy):
    assert copy in _text(_view(page)).replace("&#39;", "'").replace("&#x27;", "'")


@pytest.mark.parametrize("label", ["More details", "Show the exact report"])
def test_toggle_labels_are_in_the_page(page, label):
    """The toggles get their titles at run time (the node harness reads
    them off the buttons); the words must be in the page."""
    assert label in page


# ---- order (design.md: the note is the first control under the title and the one
# privacy line) -----------------------------------------------------------------------------

ORDER = [
    ("title", "Report a problem"), ("privacy", "Never your chats"),
    ("label", "Your note (optional)"), ("note", 'id="rp-note"'), ("hint", 'id="rp-hint"'),
    ("count", 'id="rp-count"'), ("status", 'id="rp-status"'),
    ("what", "What gets sent"), ("facts", 'id="rp-facts"'),
    ("err-title", 'id="rp-err-title"'), ("errors", 'id="rp-errors"'),
    ("more", 'id="rp-more-btn"'), ("exact-btn", 'id="rp-exact-btn"'),
    ("exact", 'id="rp-exact"'), ("send", 'id="rp-send"'), ("foot", "Nothing is sent until"),
    ("result", 'id="rp-result"'),
]


@pytest.mark.parametrize("i", range(len(ORDER) - 1),
                         ids=[f"{a[0]}<{b[0]}" for a, b in zip(ORDER, ORDER[1:])])
def test_report_view_order(page, i):
    view = _view(page)
    (_, a), (_, b) = ORDER[i], ORDER[i + 1]
    assert view.index(a) < view.index(b)


def test_no_control_between_title_and_note(page):
    view = _view(page)
    head = view[:view.index(_tag(page, "rp-note"))]
    assert not re.search(r"<(button|input|select|textarea)\b", head)


# ---- em dashes -----------------------------------------------------------------------

def test_no_em_dash_anywhere_in_the_page(page, h):
    assert h.EM_DASH not in page


def test_no_em_dash_entity_in_the_page(page):
    assert "&mdash;" not in page and "&#8212;" not in page


def test_no_em_dash_in_report_page_strings(page, h):
    assert h.EM_DASH not in _view(page)
