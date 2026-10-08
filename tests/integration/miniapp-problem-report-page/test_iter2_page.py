"""Iteration 2 page behaviours, black box, driven through ``report_page.js``
(the same node harness as test_page_behaviour.py, which now also records
every ``scrollIntoView`` by element id and every Telegram haptic, and
snapshots ``#rp-hero``, ``#rp-form`` and ``#rp-body``).

What is pinned (the coordinator's iteration 2 brief, read against
design.md "States"):

* every status message on the form (try later, still sending 409, too
  many taps 429, no answer, note tidied 422, note refused 422, the 410
  reload line) is scrolled into view and gives a haptic;
* the 410 line reads "That report is no longer here. Here is a fresh one:
  check it, then tap Send report again.";
* on every result and refusal screen the page title and privacy line
  (``#rp-hero``) are hidden, and they are back on the form;
* the loading caption "Preparing the report..." shows with the skeleton
  and goes once the draft is in;
* a send answer without ``line`` never shows the word "undefined"; a
  retryable one shows the try-later wording;
* the exact block has no inner scroll area and its text ends with the
  note line after typing.

Methods: equivalence partitioning over the status kinds and the screens
(form, result, refusal); boundary-value analysis on the answer shape (a
full answer vs one missing ``line``, vs an empty object); error guessing:
a network failure on send and on the draft, a long multi-line note."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from aipager.bot import report_flow
from aipager.report import wording

HARNESS = Path(__file__).parent / "report_page.js"
OPEN = [["click", "report-open"], ["wait", 30]]

STALE_LINE = ("That report is no longer here. Here is a fresh one: check it, then tap "
              "Send report again.")
STILL_SENDING = "Still sending. Wait a moment, then tap Send report again."
TOO_MANY = "Too many taps. Try again in a minute."
NO_ANSWER = ("No answer from aipager in time. Tap Send report again: if it already went, "
             "you will see its reference, and it is not sent twice.")
TIDIED = ("Your note was tidied up (invisible characters, extra spaces or anything past "
          "500 characters were removed). Check it, then tap Send report again.")
REFUSED = "That note could not be added. Try a shorter, plainer one."
CAPTION = "Preparing the report..."


@pytest.fixture(scope="module")
def node_bin():
    exe = shutil.which("node") or shutil.which("nodejs")
    if not exe:
        pytest.skip("node not available")
    return exe


def _run_node(node_bin, tmp_path: Path, page: str, cfg: dict) -> dict:
    page_file = tmp_path / "page.html"
    cfg_file = tmp_path / "cfg.json"
    page_file.write_text(page, encoding="utf-8")
    cfg_file.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run([node_bin, str(HARNESS), str(page_file), str(cfg_file)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["errors"] == [], out["errors"]
    return out


@pytest.fixture
def real(bot, h, run_async):
    h.record_exc()
    h.record_exc(ValueError)

    async def go(api):
        _, d = await api.draft()
        return d, await api.page()
    return h.serve(bot, run_async, go)


@pytest.fixture
def page(real):
    return real[1]


@pytest.fixture
def drive(node_bin, tmp_path, real):
    draft, page = real

    def _drive(steps, **cfg):
        cfg = {"mainbutton": True, "draft": draft, **cfg, "steps": steps}
        return _run_node(node_bin, tmp_path, page, cfg)
    return _drive


def _answer(outcome, *, line=True, reference=None, retry=None, repeat=False):
    body = {"outcome": outcome, "reference": reference,
            "retry": outcome in wording.RETRYABLE if retry is None else retry,
            "repeat": repeat}
    if line:
        body["line"] = (report_flow.SEND_FAILED_TEXT if outcome == "failed"
                        else report_flow.outcome_line(outcome, reference))
    return {"status": 200, "body": body}


SENT = _answer("sent", reference="ap1-0123456789ab")

# Each status kind: (send answers, text the status must read, typed note).
STATUS_KINDS = {
    "try-later": ([_answer("offline")], wording.TRY_LATER, ""),
    "still-sending-409": ([{"status": 409, "body": {"error": "sending"}}], STILL_SENDING, ""),
    "too-many-taps-429": ([{"status": 429, "body": {"error": "too_many_requests"}}],
                          TOO_MANY, ""),
    "no-answer": (["reject"], NO_ANSWER, ""),
    "note-tidied-422": ([{"status": 422, "body": {"error": "note_changed", "note": "tidied"}}],
                        TIDIED, "tidied​"),
    "note-refused-422": ([{"status": 422, "body": {"error": "note_refused"}}], REFUSED, "x"),
    "stale-410": ([{"status": 410, "body": {"error": "draft_gone"}}], STALE_LINE, ""),
}


def _status_run(drive, kind):
    answers, _, note = STATUS_KINDS[kind]
    steps = OPEN + ([["type", note]] if note else []) + [
        ["snap", "before"], ["main"], ["wait", 40], ["snap", "after"]]
    return drive(steps, sendAnswers=answers)


def _events_after(out, name="before"):
    return out["events"][out["snaps"][name]["events"]:]


# ---- every status: its words, into view, a haptic -----------------------------------------

@pytest.mark.parametrize("kind", list(STATUS_KINDS))
def test_status_text(drive, kind):
    out = _status_run(drive, kind)
    assert out["snaps"]["after"]["status"]["text"] == STATUS_KINDS[kind][1]


@pytest.mark.parametrize("kind", list(STATUS_KINDS))
def test_status_is_shown(drive, kind):
    out = _status_run(drive, kind)
    assert out["snaps"]["after"]["status"]["hidden"] is False


@pytest.mark.parametrize("kind", list(STATUS_KINDS))
def test_status_stays_on_the_form(drive, kind):
    out = _status_run(drive, kind)
    assert out["snaps"]["after"]["resultHidden"] is True


@pytest.mark.parametrize("kind", list(STATUS_KINDS))
def test_status_is_scrolled_into_view(drive, kind):
    out = _status_run(drive, kind)
    assert {"kind": "scroll", "id": "rp-status"} in _events_after(out)


@pytest.mark.parametrize("kind", list(STATUS_KINDS))
def test_status_gives_a_haptic(drive, kind):
    out = _status_run(drive, kind)
    assert [e for e in _events_after(out) if e["kind"] == "haptic"]


@pytest.mark.parametrize("kind", list(STATUS_KINDS))
def test_status_scroll_comes_after_the_tap(drive, kind):
    """The scroll belongs to the answer, not to opening the page: none of
    the status scrolls in the run happened before the tap."""
    out = _status_run(drive, kind)
    before = out["events"][:out["snaps"]["before"]["events"]]
    assert {"kind": "scroll", "id": "rp-status"} not in before


def test_no_status_scroll_on_a_plain_open(drive):
    out = drive(OPEN + [["type", "hello"], ["wait", 10]])
    assert {"kind": "scroll", "id": "rp-status"} not in out["events"]


def test_stale_line_comes_after_the_fresh_draft(drive):
    """The 410 line describes the fresh draft: by the time it shows, a
    second draft was asked for."""
    out = _status_run(drive, "stale-410")
    drafts = [p for p in out["posts"] if p["url"] == "/api/report/draft"]
    assert (len(drafts), out["snaps"]["after"]["status"]["text"]) == (2, STALE_LINE)


def test_stale_line_never_says_out_of_date(drive):
    out = _status_run(drive, "stale-410")
    assert "out of date" not in out["snaps"]["after"]["status"]["text"]


def test_a_second_status_scrolls_again(drive):
    """Error guessing: a page that scrolls only the first time the status
    appears would leave a later one off screen."""
    answers = [_answer("offline"), {"status": 429, "body": {"error": "too_many_requests"}}]
    out = drive(OPEN + [["main"], ["wait", 30], ["snap", "mid"], ["main"], ["wait", 30]],
                sendAnswers=answers)
    assert {"kind": "scroll", "id": "rp-status"} in _events_after(out, "mid")


@pytest.mark.parametrize("kind", ["try-later", "too-many-taps-429", "note-refused-422"])
def test_error_statuses_look_like_errors(drive, kind):
    out = _status_run(drive, kind)
    assert "is-err" in out["snaps"]["after"]["statusClass"].split()


@pytest.mark.parametrize("kind", ["still-sending-409", "no-answer", "note-tidied-422",
                                  "stale-410"])
def test_info_statuses_do_not_look_like_errors(drive, kind):
    out = _status_run(drive, kind)
    assert "is-err" not in out["snaps"]["after"]["statusClass"].split()


def test_info_notices_have_a_tint(page):
    """The plain status rule (the info look) paints a background of its
    own, so an info notice is tinted rather than bare."""
    m = re.search(r"\.rp-status\s*\{([^}]*)\}", page)
    assert m and re.search(r"\bbackground(-color)?\s*:", m.group(1))


# ---- the hero (title + privacy line) ---------------------------------------------------------

def _final(outcome):
    return _answer(outcome, reference="ap1-0123456789ab" if outcome == "sent" else None)


SEND_RESULTS = ["sent", "disabled", "too_old", "daily_cap", "invalid", "failed"]

DRAFT_REFUSALS = {
    "no-owner-409": {"status": 409, "body": {"error": "no_owner"}},
    "forbidden-403": {"status": 403, "body": {"error": "forbidden"}},
    "private-only-403": {"status": 403, "body": {"error": "private_only"}},
    "not-built-503": {"status": 503, "body": {"error": "not_built"}},
    "network-error": "reject",
}


def test_hero_shown_on_the_form(drive):
    out = drive(OPEN + [["snap", "s"]])
    assert out["snaps"]["s"]["heroHidden"] is False


def test_hero_shown_while_loading(drive):
    out = drive(OPEN + [["snap", "s"]], draftAnswers=["pending"])
    assert out["snaps"]["s"]["heroHidden"] is False


@pytest.mark.parametrize("outcome", SEND_RESULTS)
def test_hero_hidden_on_a_result(drive, outcome):
    out = drive(OPEN + [["main"], ["wait", 30], ["snap", "s"]], sendAnswers=[_final(outcome)])
    assert out["snaps"]["s"]["heroHidden"] is True


@pytest.mark.parametrize("outcome", SEND_RESULTS)
def test_result_panel_is_what_shows(drive, outcome):
    """Guard the guard: the hero test above is about a real result screen."""
    out = drive(OPEN + [["main"], ["wait", 30], ["snap", "s"]], sendAnswers=[_final(outcome)])
    assert out["snaps"]["s"]["resultHidden"] is False


def test_hero_hidden_on_a_repeat_result(drive):
    answer = _answer("sent", reference="ap1-0123456789ab", repeat=True)
    out = drive(OPEN + [["main"], ["wait", 30], ["snap", "s"]], sendAnswers=[answer])
    assert out["snaps"]["s"]["heroHidden"] is True


@pytest.mark.parametrize("kind", list(DRAFT_REFUSALS))
def test_hero_hidden_on_a_refusal(drive, kind):
    out = drive(OPEN + [["snap", "s"]], draftAnswers=[DRAFT_REFUSALS[kind]])
    assert out["snaps"]["s"]["heroHidden"] is True


@pytest.mark.parametrize("kind", list(DRAFT_REFUSALS))
def test_refusal_panel_is_what_shows(drive, kind):
    out = drive(OPEN + [["snap", "s"]], draftAnswers=[DRAFT_REFUSALS[kind]])
    assert out["snaps"]["s"]["resultHidden"] is False


@pytest.mark.parametrize("kind", list(STATUS_KINDS))
def test_hero_stays_with_a_status_on_the_form(drive, kind):
    out = _status_run(drive, kind)
    assert out["snaps"]["after"]["heroHidden"] is False


@pytest.mark.parametrize("kind", ["not-built-503", "network-error"])
def test_hero_back_after_try_again_loads_the_draft(drive, kind):
    out = drive(OPEN + [["main"], ["wait", 30], ["snap", "s"]],
                draftAnswers=[DRAFT_REFUSALS[kind]])
    assert out["snaps"]["s"]["heroHidden"] is False


@pytest.mark.parametrize("kind", ["not-built-503", "network-error"])
def test_form_back_after_try_again_loads_the_draft(drive, kind):
    out = drive(OPEN + [["main"], ["wait", 30], ["snap", "s"]],
                draftAnswers=[DRAFT_REFUSALS[kind]])
    assert out["snaps"]["s"]["resultHidden"] is True


def test_hero_back_when_reopened_after_sent(drive):
    out = drive(OPEN + [["main"], ["wait", 30], ["main"], ["wait", 10]] + OPEN
                + [["snap", "s"]], sendAnswers=[SENT])
    assert out["snaps"]["s"]["heroHidden"] is False


def test_hero_back_when_reopened_after_a_refusal(drive):
    out = drive(OPEN + [["back"], ["wait", 10]] + OPEN + [["snap", "s"]],
                draftAnswers=[DRAFT_REFUSALS["not-built-503"]])
    assert out["snaps"]["s"]["heroHidden"] is False


# ---- the loading caption -----------------------------------------------------------------------

def _view(page: str) -> str:
    start = page.index('id="view-report"')
    return page[start:page.index("</section>", start)]


def test_loading_caption_is_in_the_report_view(page):
    assert CAPTION in _view(page)


def test_loading_caption_sits_right_after_the_report_body(page):
    """The caption's visibility follows ``#rp-body``: it is the body's next
    sibling and the stylesheet hides it once the body shows."""
    view = _view(page)
    body_end = view.index('id="rp-body"')
    assert view.index(CAPTION) > body_end


def test_stylesheet_hides_the_caption_once_the_body_shows(page):
    assert re.search(r"#rp-body:not\(\[hidden\]\)\s*\+\s*\.rp-wait\s*\{[^}]*display:\s*none",
                     page)


def test_report_body_hidden_while_loading(drive):
    out = drive(OPEN + [["snap", "s"]], draftAnswers=["pending"])
    assert out["snaps"]["s"]["bodyHidden"] is True


def test_report_body_shown_once_loaded(drive):
    out = drive(OPEN + [["snap", "s"]])
    assert out["snaps"]["s"]["bodyHidden"] is False


def test_report_body_hidden_again_while_a_stale_draft_reloads(drive):
    """410: the fresh draft loads with the skeleton (and so the caption)."""
    out = drive(OPEN + [["main"], ["wait", 30], ["snap", "s"]],
                sendAnswers=[{"status": 410, "body": {"error": "draft_gone"}}],
                draftAnswers=[None, "pending"])
    assert out["snaps"]["s"]["bodyHidden"] is True


# ---- an answer without `line` ---------------------------------------------------------------

def _shown(snap) -> str:
    return " ".join(str(snap[k] or "") for k in ("resultTitle", "resultLine", "ref"))\
        + " " + str(snap["status"]["text"] or "")


NO_LINE = {
    "retryable": _answer("offline", line=False),
    "rate-limited": _answer("rate_limited", line=False),
    "final-disabled": _answer("disabled", line=False),
    "final-failed": _answer("failed", line=False),
    "sent": _answer("sent", line=False, reference="ap1-0123456789ab"),
    "empty-object": {"status": 200, "body": {}},
}


@pytest.mark.parametrize("kind", list(NO_LINE))
def test_answer_without_line_never_shows_undefined(drive, kind):
    out = drive(OPEN + [["main"], ["wait", 30], ["snap", "s"]], sendAnswers=[NO_LINE[kind]])
    assert "undefined" not in _shown(out["snaps"]["s"])


@pytest.mark.parametrize("kind", list(NO_LINE))
def test_answer_without_line_never_shows_null(drive, kind):
    out = drive(OPEN + [["main"], ["wait", 30], ["snap", "s"]], sendAnswers=[NO_LINE[kind]])
    assert not re.search(r"\bnull\b", _shown(out["snaps"]["s"]))


@pytest.mark.parametrize("kind", ["retryable", "rate-limited"])
def test_retryable_answer_without_line_shows_try_later(drive, kind):
    out = drive(OPEN + [["main"], ["wait", 30], ["snap", "s"]], sendAnswers=[NO_LINE[kind]])
    assert out["snaps"]["s"]["status"]["text"] == wording.TRY_LATER


@pytest.mark.parametrize("kind", ["final-disabled", "final-failed"])
def test_final_answer_without_line_shows_the_try_later_wording(drive, kind):
    out = drive(OPEN + [["main"], ["wait", 30], ["snap", "s"]], sendAnswers=[NO_LINE[kind]])
    assert wording.TRY_LATER in _shown(out["snaps"]["s"])


# ---- the exact block: no inner scroll, the note line shows -------------------------------------

def _rules_for(page: str, needle: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"([^{}]*\{[^{}]*\})", page)
            if needle in m.group(1).split("{")[0]]


@pytest.mark.parametrize("selector", [".rp-mono", "#rp-exact", "#rp-exact-wrap", ".diff-body"])
def test_exact_block_has_no_height_cap(page, selector):
    assert not [r for r in _rules_for(page, selector) if re.search(r"max-height\s*:", r)]


@pytest.mark.parametrize("selector", [".rp-mono", "#rp-exact", "#rp-exact-wrap"])
def test_exact_block_has_no_vertical_scroll(page, selector):
    assert not [r for r in _rules_for(page, selector)
                if re.search(r"overflow(-y)?\s*:\s*(auto|scroll)", r)]


def test_exact_block_lines_wrap(page):
    rules = " ".join(_rules_for(page, ".rp-mono"))
    assert re.search(r"white-space\s*:\s*(pre-wrap|pre-line|break-spaces)", rules)


def _lines(out):
    return out["snaps"]["s"]["exact"].split("\n")


NOTES = {
    "short": "it broke",
    "long": "word " * 99 + "end",
    "multi-line": "first line\nsecond line\nthird",
    "emoji": "\U0001F993 crashed",
}


@pytest.mark.parametrize("note", list(NOTES.values()), ids=list(NOTES))
def test_exact_text_ends_with_the_note_line(drive, note):
    out = drive(OPEN + [["click", "rp-exact-btn"], ["type", note], ["snap", "s"]])
    assert _lines(out)[-2:] == ["  \"note\": " + json.dumps(note, ensure_ascii=False), "}"]


def test_exact_text_note_line_follows_each_keystroke(drive):
    out = drive(OPEN + [["click", "rp-exact-btn"], ["type", "a"], ["type", "ab"],
                        ["type", "abc"], ["snap", "s"]])
    assert _lines(out)[-2] == '  "note": "abc"'


def test_exact_text_has_no_note_line_without_a_note(drive):
    out = drive(OPEN + [["click", "rp-exact-btn"], ["snap", "s"]])
    assert '"note"' not in out["snaps"]["s"]["exact"]


# ---- the harness itself ----------------------------------------------------------------------

def _broken_run(node_bin, tmp_path, real, old, new):
    draft, page = real
    assert old in page, f"{old!r} is not in the page: the control proves nothing"
    return _run_node(node_bin, tmp_path, page.replace(old, new), {
        "mainbutton": True, "draft": draft, "sendAnswers": [_answer("offline")],
        "steps": OPEN + [["snap", "before"], ["main"], ["wait", 40]]})


def test_the_harness_sees_a_page_that_never_scrolls(node_bin, tmp_path, real):
    """Guard the guard: with every scrollIntoView call renamed away, the
    scroll tests above must see no status scroll."""
    out = _broken_run(node_bin, tmp_path, real, "scrollIntoView", "noScrollIntoView")
    assert {"kind": "scroll", "id": "rp-status"} not in out["events"]


def test_the_harness_sees_a_page_that_never_buzzes(node_bin, tmp_path, real):
    """Guard the guard: with the page's haptic API renamed away, the haptic
    tests above must see no haptic."""
    out = _broken_run(node_bin, tmp_path, real, "HapticFeedback", "NoHapticFeedback")
    assert [e for e in _events_after(out) if e["kind"] == "haptic"] == []
