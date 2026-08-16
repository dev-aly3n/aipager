"""Grid display order + header totals for the two-column Sessions tab.

Ordering lives server-side (`sessions.sort_for_display`) rather than in the
page's JavaScript specifically so it can be pinned here: the rule has three
interacting parts (status group, recency, null handling) and a comparator
buried in a <script> block is untestable by this suite.
"""

import pytest

from aipager.miniapp.sessions import grid_totals, sort_for_display


def _row(label, status="idle", age=10, cost=0.0):
    return {
        "label": label,
        "status": status,
        "waiting_kind": None,
        "model": "",
        "context_pct": 0,
        "cost_usd": cost,
        "last_active_seconds_ago": age,
        "project": "",
    }


def _labels(rows):
    return [r["label"] for r in sort_for_display(rows)]


# ===== status grouping ====================================================

def test_waiting_sorts_before_every_other_live_status():
    rows = [
        _row("idle-one", "idle", 1),
        _row("busy-one", "busy", 1),
        _row("waiting-one", "waiting", 999),
    ]
    # Even though it is by far the least recently active, waiting leads —
    # it is the only status costing the operator time.
    assert _labels(rows)[0] == "waiting-one"


def test_gone_sorts_last_regardless_of_recency():
    rows = [
        _row("gone-just-now", "gone", 0),
        _row("idle-ages-ago", "idle", 99999),
    ]
    assert _labels(rows) == ["idle-ages-ago", "gone-just-now"]


def test_full_status_precedence():
    rows = [
        _row("d", "gone", 1),
        _row("c", "unknown", 1),
        _row("b", "idle", 1),
        _row("a2", "busy", 1),
        _row("a1", "waiting", 1),
    ]
    assert _labels(rows) == ["a1", "a2", "b", "c", "d"]


def test_unrecognised_status_does_not_crash_and_sorts_with_unknown():
    rows = [_row("weird", "surprise", 1), _row("gone-one", "gone", 1)]
    ordered = _labels(rows)
    # Whatever bucket it lands in, it must not sort after gone.
    assert ordered.index("weird") < ordered.index("gone-one")


# ===== recency within a group ============================================

def test_most_recently_active_first_within_a_group():
    """Labels are deliberately in the REVERSE of the expected order.

    An earlier version used "old"/"fresh"/"mid", which sort alphabetically
    into exactly the expected recency order — so the test passed even with
    the age component removed from the sort key entirely, pinning nothing.
    """
    rows = [
        _row("aaa-oldest", "idle", 500),
        _row("zzz-freshest", "idle", 5),
        _row("mmm-middle", "idle", 50),
    ]
    assert _labels(rows) == ["zzz-freshest", "mmm-middle", "aaa-oldest"]


def test_null_last_active_sorts_last_within_its_group_not_first():
    """`None` means "no recorded activity" — reading it as 0 would push
    never-used sessions to the top of the grid."""
    rows = [_row("never", "idle", None), _row("recent", "idle", 300)]
    assert _labels(rows) == ["recent", "never"]


def test_null_last_active_still_respects_status_grouping():
    rows = [_row("waiting-never", "waiting", None), _row("idle-fresh", "idle", 1)]
    assert _labels(rows) == ["waiting-never", "idle-fresh"]


def test_label_breaks_ties_so_order_is_stable_across_polls():
    rows = [_row("zebra", "idle", 10), _row("alpha", "idle", 10)]
    assert _labels(rows) == ["alpha", "zebra"]
    # Same input in the other order must produce the same output.
    assert _labels(list(reversed(rows))) == ["alpha", "zebra"]


def test_sort_does_not_mutate_the_input():
    rows = [_row("b", "idle", 1), _row("a", "waiting", 1)]
    before = [r["label"] for r in rows]
    sort_for_display(rows)
    assert [r["label"] for r in rows] == before


def test_empty_input():
    assert sort_for_display([]) == []


# ===== header totals ======================================================

def test_totals_counts_and_spend():
    rows = [
        _row("a", "busy", 1, cost=1.5),
        _row("b", "waiting", 1, cost=0.25),
        _row("c", "gone", 1, cost=2.0),
    ]
    t = grid_totals(rows)
    assert t["total"] == 3
    assert t["live"] == 2
    assert t["gone"] == 1
    assert t["waiting"] == 1
    assert t["cost_usd"] == pytest.approx(3.75)


def test_totals_spend_includes_finished_sessions():
    """A finished session still cost money — excluding it would understate
    what the operator actually spent."""
    assert grid_totals([_row("g", "gone", 1, cost=4.0)])["cost_usd"] == pytest.approx(4.0)


def test_totals_tolerate_missing_cost():
    rows = [{"label": "x", "status": "idle"}]
    t = grid_totals(rows)
    assert t["cost_usd"] == 0.0
    assert t["live"] == 1


def test_totals_on_empty_input():
    t = grid_totals([])
    assert t == {"total": 0, "live": 0, "gone": 0, "waiting": 0, "cost_usd": 0.0}


# ===== the page wires up to elements that exist ===========================

def test_every_getelementbyid_reference_resolves():
    """A typo'd element id breaks the page silently at runtime — no Python
    test would notice, because the JS only runs in a browser. Cheap static
    check: every id the script asks for must exist in the markup."""
    import re

    from aipager.miniapp.static import INDEX_HTML

    defined = set(re.findall(r'id="([^"]+)"', INDEX_HTML))
    referenced = set(re.findall(r'getElementById\("([^"]+)"\)', INDEX_HTML))
    assert referenced, "no getElementById calls found — did the page change shape?"
    assert not (referenced - defined), (
        f"script references ids that do not exist: {sorted(referenced - defined)}"
    )


def test_page_loads_no_external_asset_but_the_telegram_sdk():
    """CSP-friendliness and the no-bundler rule: the only thing the page may
    fetch from another host is Telegram's own required SDK."""
    import re

    from aipager.miniapp.static import INDEX_HTML

    external = set(re.findall(r'(?:src|href)="(https?://[^"]+)"', INDEX_HTML))
    assert external == {"https://telegram.org/js/telegram-web-app.js"}


def test_page_has_no_inline_event_handlers():
    """Every listener is wired with addEventListener — inline on* attributes
    would need a laxer CSP than stage 1 established."""
    import re

    from aipager.miniapp.static import INDEX_HTML

    handlers = re.findall(r'\son(?:click|load|error|change|input)=', INDEX_HTML)
    assert handlers == []


# ===== view state machine (static guards for logic pytest can't run) ======

def _page():
    from aipager.miniapp.static import INDEX_HTML
    return INDEX_HTML


def test_every_view_section_has_a_VIEWS_entry():
    """The bug this pins: `pollTick` used to branch "grid, else detail".
    Batch 5 added a third view with no label, so the else-branch fetched
    /api/sessions/undefined, 404'd, and bounced the operator back to the
    grid ~2.5s after opening the New session form.

    A view is now only reachable through the VIEWS table, so a section
    that exists in the markup but not in the table is a half-added view.
    """
    import re

    page = _page()
    sections = set(re.findall(r'<section id="(view-[^"]+)"', page))
    tabled = set(re.findall(r'section:\s*"(view-[^"]+)"', page))
    assert sections, "no view sections found — did the page change shape?"
    assert sections == tabled, (
        f"view sections not enumerated in VIEWS: "
        f"missing={sorted(sections - tabled)} extra={sorted(tabled - sections)}"
    )


def test_poll_mode_is_declared_per_view_not_inferred():
    """Each VIEWS entry must say what it polls. `polls: null` is how the
    settings tab and the new-session form opt out — the absence of a
    branch is what caused the bounce-back."""
    import re

    page = _page()
    entries = re.findall(r"\{\s*section:\s*\"view-[^\"]+\"[^}]*\}", page)
    assert len(entries) >= 4, f"expected every view declared, found {len(entries)}"
    for entry in entries:
        assert "polls:" in entry, f"VIEWS entry without a polls declaration: {entry}"
        assert "topLevel:" in entry, f"VIEWS entry without topLevel: {entry}"


def test_no_view_section_visibility_is_set_outside_the_table():
    """Visibility must flow through showView(). A stray
    `getElementById("view-x").hidden = ...` is how the old code drifted
    into four functions each toggling a different subset."""
    import re

    page = _page()
    strays = re.findall(r'getElementById\("(view-[^"]+)"\)\.hidden', page)
    assert strays == [], f"view visibility set outside showView(): {strays}"


def test_the_tab_bar_is_driven_by_the_view_table():
    """The Sessions|Settings bar is top-level navigation; on a sub-page it
    competes with Telegram's BackButton. Its visibility must come from the
    view's own topLevel flag, not from ad-hoc calls."""
    page = _page()
    assert 'document.getElementById("tabbar").hidden = !spec.topLevel;' in page
    assert page.count('getElementById("tabbar").hidden') == 1, (
        "tab-bar visibility is set in more than one place"
    )
