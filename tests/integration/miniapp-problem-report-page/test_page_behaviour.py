"""The report page's behaviour, driven in node from the outside (design.md
Success criteria 1-4, 6-8; entrypoints.md "Page surface"): ``report_page.js``
runs the page GET / serves in a small DOM shim and drives it only through
element ids, input events, clicks and the Telegram MainButton/BackButton
callbacks. Where it matters, the draft comes from the real
``POST /api/report/draft`` and the page's send body goes back to the real
``POST /api/report/send``, so "you see exactly what is sent" is checked end
to end against the bytes the fake network received.

Methods: equivalence partitioning over the page states (loading, ready,
sending, sent, try later, final failure, note changed, stale draft, not the
owner); boundary-value analysis on the counter (500 vs 501 code points,
emoji as one); error guessing: hostile report values (markup in an area, a
type, an install name), a note with markup, Back while sending, a server
line with backticks."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aipager.report import schema, wording

HARNESS = Path(__file__).parent / "report_page.js"
ZEBRA = "\U0001F993"


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
    """A real draft (with errors) and the page, from the real server."""
    h.record_exc()
    h.record_exc(ValueError)

    async def go(api):
        _, d = await api.draft()
        return d, await api.page()
    return h.serve(bot, run_async, go)


@pytest.fixture
def drive(node_bin, tmp_path, real):
    """``drive(steps, **cfg)``: run the page with the real draft unless the
    config names another."""
    draft, page = real

    def _drive(steps, **cfg):
        cfg = {"mainbutton": True, "draft": draft, **cfg, "steps": steps}
        return _run_node(node_bin, tmp_path, page, cfg)
    return _drive


OPEN = [["click", "report-open"], ["wait", 30]]


def _sends(out):
    return [p for p in out["posts"] if p["url"] == "/api/report/send"]


def _drafts(out):
    return [p for p in out["posts"] if p["url"] == "/api/report/draft"]


def sent_answer(ref="ap1-0123456789ab"):
    return {"status": 200, "body": {"outcome": "sent", "reference": ref,
                                    "line": wording.OUTCOME_LINES["sent"].format(reference=ref),
                                    "retry": False, "repeat": False}}


# ---- opening the page ----------------------------------------------------------------

def test_report_a_problem_opens_the_page(drive):
    out = drive(OPEN + [["snap", "s"]])
    assert out["snaps"]["s"]["view"] is True


def test_opening_the_page_shows_the_back_button(drive):
    out = drive(OPEN + [["snap", "s"]])
    assert out["snaps"]["s"]["back"] is True


def test_opening_the_page_asks_for_a_draft(drive):
    out = drive(OPEN)
    assert len(_drafts(out)) == 1


def test_main_button_reads_send_report(drive):
    out = drive(OPEN + [["snap", "s"]])
    assert (out["snaps"]["s"]["mb"]["text"], out["snaps"]["s"]["mb"]["is_visible"]) == (
        "Send report", True)


def test_main_button_inactive_while_the_draft_loads(drive):
    out = drive(OPEN + [["snap", "s"]], draftAnswers=["pending"])
    assert out["snaps"]["s"]["mb"]["is_active"] is False


def test_main_button_active_once_the_draft_loaded(drive):
    out = drive(OPEN + [["snap", "s"]])
    assert out["snaps"]["s"]["mb"]["is_active"] is True


def test_main_button_tap_while_loading_sends_nothing(drive):
    out = drive(OPEN + [["main"], ["wait", 20]], draftAnswers=["pending"])
    assert _sends(out) == []


def test_counter_starts_at_0_of_500(drive):
    out = drive(OPEN + [["snap", "s"]])
    assert out["snaps"]["s"]["count"] == "0 / 500"


@pytest.mark.parametrize("snap_field, label", [("moreBtn", "More details"),
                                                ("exactBtn", "Show the exact report")])
def test_toggle_labels(drive, snap_field, label):
    out = drive(OPEN + [["snap", "s"]])
    assert label in out["snaps"]["s"][snap_field]


# ---- the exact block --------------------------------------------------------------------

def test_exact_block_without_note_equals_the_server_preview(drive, real):
    out = drive(OPEN + [["click", "rp-exact-btn"], ["wait", 10], ["snap", "s"]])
    assert out["snaps"]["s"]["exact"] == real[0]["preview"]


def _stable(note: str) -> bool:
    return (schema.normalize_note(note) or "") == note


# The tricky notes the server accepts as they are (the client cannot apply
# the server's full normalization; those go through note_changed instead).
TRICKY = {k: v for k, v in {
    "quotes": 'He said "it broke" and \'left\'',
    "backslashes": "C:\\Users\\x and \\n literally",
    "rtl": "\u05e9\u05dc\u05d5\u05dd \u0645\u0631\u062d\u0628\u0627 mixed",
    "flag": "flag \U0001F1EE\U0001F1F9 ok",
    "zwj-family": "family \U0001F468\u200d\U0001F469\u200d\U0001F467 here",
    "emoji": ZEBRA + "\U0001F525 broken",
    "newline": "line one\nline two",
    "markup": "</script><b>x</b> & <img src=x>",
    "lrm": "abc\u200edef",
    "astral-cjk": "\U00020000\u58ca",
}.items() if _stable(v)}


@pytest.mark.parametrize("note", list(TRICKY.values()), ids=list(TRICKY))
def test_exact_block_updates_while_typing(drive, real, h, note):
    out = drive(OPEN + [["click", "rp-exact-btn"], ["type", note], ["snap", "s"]])
    assert out["snaps"]["s"]["exact"] == h.exact(real[0]["report"], note).decode("utf-8")


def test_exact_block_shows_the_note_without_surrounding_space(drive, real, h):
    out = drive(OPEN + [["click", "rp-exact-btn"], ["type", "  it broke \n"], ["snap", "s"]])
    assert out["snaps"]["s"]["exact"] == h.exact(real[0]["report"], "it broke").decode()


def test_exact_block_opened_after_typing_shows_the_note(drive, real, h):
    out = drive(OPEN + [["type", "late"], ["click", "rp-exact-btn"], ["wait", 10],
                        ["snap", "s"]])
    assert out["snaps"]["s"]["exact"] == h.exact(real[0]["report"], "late").decode()


def test_exact_block_drops_the_note_when_cleared(drive, real):
    out = drive(OPEN + [["click", "rp-exact-btn"], ["type", "x"], ["type", "   "],
                        ["snap", "s"]])
    assert out["snaps"]["s"]["exact"] == real[0]["preview"]


# ---- the end to end promise: what the page shows is what the network gets -------------------

@pytest.mark.parametrize("typed", ["", "  plain note  "] + list(TRICKY.values()),
                         ids=["no-note", "padded"] + list(TRICKY))
def test_page_exact_block_equals_the_bytes_sent(node_bin, tmp_path, bot, h, run_async, net,
                                                typed):
    h.record_exc()

    async def go(api):
        _, d = await api.draft()
        page = await api.page()
        out = await asyncio.to_thread(_run_node, node_bin, tmp_path, page, {
            "mainbutton": True, "draft": d, "sendAnswers": ["pending"],
            "steps": OPEN + [["click", "rp-exact-btn"], ["type", typed], ["snap", "s"],
                             ["main"], ["wait", 20]]})
        body = _sends(out)[0]["body"]
        status, _ = await api.post("/api/report/send", json_body=body)
        return out["snaps"]["s"]["exact"], status
    shown, status = h.serve(bot, run_async, go)
    assert (status, h.attachment_of(net.posts[0])) == (200, shown.encode("utf-8"))


# ---- the send body ---------------------------------------------------------------------------

def test_send_body_is_exactly_draft_and_note(drive):
    out = drive(OPEN + [["type", "hello"], ["main"], ["wait", 20]], sendAnswers=[sent_answer()])
    assert sorted(_sends(out)[0]["body"]) == ["draft", "note"]


def test_send_body_names_the_draft(drive, real):
    out = drive(OPEN + [["main"], ["wait", 20]], sendAnswers=[sent_answer()])
    assert _sends(out)[0]["body"]["draft"] == real[0]["draft"]


def test_send_body_note_is_trimmed(drive):
    out = drive(OPEN + [["type", "\n  it broke  \t"], ["main"], ["wait", 20]],
                sendAnswers=[sent_answer()])
    assert _sends(out)[0]["body"]["note"] == "it broke"


def test_send_body_carries_no_report(drive):
    out = drive(OPEN + [["main"], ["wait", 20]], sendAnswers=[sent_answer()])
    assert "report" not in _sends(out)[0]["body"]


def test_one_tap_sends_once(drive):
    out = drive(OPEN + [["main"], ["wait", 20]], sendAnswers=[sent_answer()])
    assert len(_sends(out)) == 1


def test_second_tap_while_sending_sends_nothing_more(drive):
    out = drive(OPEN + [["main"], ["main"], ["wait", 20]], sendAnswers=["pending"])
    assert len(_sends(out)) == 1


def test_in_page_send_button_works_without_main_button(drive):
    out = drive(OPEN + [["click", "rp-send"], ["wait", 20]], mainbutton=False,
                sendAnswers=[sent_answer()])
    assert len(_sends(out)) == 1


# ---- the counter (code points, no maxlength) ---------------------------------------------------

@pytest.mark.parametrize("note, shown", [
    (ZEBRA * 300, "300 / 500"),
    ("a" * 500, "500 / 500"),
    ("\U0001F468\u200d\U0001F469\u200d\U0001F467", "5 / 500"),
    ("\U0001F1EE\U0001F1F9", "2 / 500"),
], ids=["300-emoji", "500-ascii", "zwj-family", "flag"])
def test_counter_counts_code_points(drive, note, shown):
    out = drive(OPEN + [["type", note], ["snap", "s"]])
    assert out["snaps"]["s"]["count"] == shown


def test_counter_ignores_surrounding_space(drive):
    out = drive(OPEN + [["type", "   abc   "], ["snap", "s"]])
    assert out["snaps"]["s"]["count"] == "3 / 500"


@pytest.mark.parametrize("note", [ZEBRA * 300, ZEBRA * 500, "a" * 500],
                         ids=["300-emoji", "500-emoji", "500-ascii"])
def test_main_button_active_up_to_500_code_points(drive, note):
    out = drive(OPEN + [["type", note], ["snap", "s"]])
    assert out["snaps"]["s"]["mb"]["is_active"] is True


@pytest.mark.parametrize("note", [ZEBRA * 501, "a" * 501], ids=["501-emoji", "501-ascii"])
def test_main_button_inactive_over_500_code_points(drive, note):
    out = drive(OPEN + [["type", note], ["snap", "s"]])
    assert out["snaps"]["s"]["mb"]["is_active"] is False


def test_in_page_send_disabled_over_500(drive):
    out = drive(OPEN + [["type", "a" * 501], ["snap", "s"]], mainbutton=False)
    assert out["snaps"]["s"]["sendDisabled"] is True


def test_over_500_says_too_long_by(drive):
    out = drive(OPEN + [["type", "a" * 503], ["snap", "s"]])
    assert out["snaps"]["s"]["hint"] == "Too long by 3 characters."


def test_main_button_tap_over_500_sends_nothing(drive):
    out = drive(OPEN + [["type", "a" * 501], ["main"], ["wait", 20]])
    assert _sends(out) == []


# ---- report values never become elements --------------------------------------------------------

def _hostile(real_draft: dict) -> dict:
    d = json.loads(json.dumps(real_draft))
    r = d["report"]
    r["errors"][0]["type"] = "builtins.<b>bold</b>"
    r["errors"][0]["last_day"] = "<i>day</i>"
    r["errors"][0]["frames"] = [{"file": "<img src=x onerror=1>.py", "line": 1,
                                 "fn": "<script>alert(1)</script>"}]
    r["errors"][0]["external"] = ["<svg onload=1>"]
    r["aipager"]["install"] = "<i>pipx</i>"
    d["areas"] = ["<img src=x onerror=2>"] + ["a&b"] * (len(r["errors"]) - 1)
    return d


HOSTILE_STEPS = OPEN + [["clickChildren", "rp-errors", 2], ["click", "rp-more-btn"],
                        ["click", "rp-exact-btn"], ["type", "<script>n()</script>"],
                        ["wait", 10]]

MARKUP = ["<img", "<script", "<b>bold", "<i>", "<svg onload"]


@pytest.mark.parametrize("tag", ["IMG", "SCRIPT", "B"])
def test_report_values_never_become_elements(drive, real, tag):
    out = drive(HOSTILE_STEPS, draft=_hostile(real[0]))
    assert tag not in out["createdTags"]


@pytest.mark.parametrize("markup", MARKUP)
def test_report_values_never_enter_markup_raw(drive, real, markup):
    out = drive(HOSTILE_STEPS, draft=_hostile(real[0]))
    assert not [x for x in out["html"] if markup in x]


def test_hostile_values_are_still_shown_as_text(drive, real):
    out = drive(HOSTILE_STEPS + [["snap", "s"]], draft=_hostile(real[0]))
    assert "<script>n()</script>" in out["snaps"]["s"]["exact"]


# ---- Back -------------------------------------------------------------------------------------------

def test_back_sends_nothing(drive):
    out = drive(OPEN + [["type", "never mind"], ["back"], ["wait", 20]])
    assert _sends(out) == []


def test_back_leaves_the_page(drive):
    out = drive(OPEN + [["back"], ["wait", 10], ["snap", "s"]])
    assert out["snaps"]["s"]["view"] is False


def test_back_while_sending_stays_on_the_page(drive):
    out = drive(OPEN + [["main"], ["wait", 10], ["back"], ["wait", 10], ["snap", "s"]],
                sendAnswers=["pending"])
    assert out["snaps"]["s"]["view"] is True


def test_sending_makes_the_note_read_only(drive):
    out = drive(OPEN + [["main"], ["wait", 10], ["snap", "s"]], sendAnswers=["pending"])
    assert out["snaps"]["s"]["noteReadOnly"] is True


def test_sending_shows_main_button_progress(drive):
    out = drive(OPEN + [["main"], ["wait", 10], ["snap", "s"]], sendAnswers=["pending"])
    assert out["snaps"]["s"]["mbProgress"] is True


# ---- outcomes ---------------------------------------------------------------------------------------

def test_sent_shows_the_reference(drive):
    out = drive(OPEN + [["main"], ["wait", 20], ["snap", "s"]],
                sendAnswers=[sent_answer("ap1-00ff00ff00ff")])
    assert out["snaps"]["s"]["ref"] == "ap1-00ff00ff00ff"


def test_sent_title(drive):
    out = drive(OPEN + [["main"], ["wait", 20], ["snap", "s"]], sendAnswers=[sent_answer()])
    assert out["snaps"]["s"]["resultTitle"] == "Report sent"


def test_sent_hint_mentions_github(drive):
    out = drive(OPEN + [["main"], ["wait", 20], ["snap", "s"]], sendAnswers=[sent_answer()])
    assert "GitHub issue" in out["snaps"]["s"]["resultLine"]


def test_sent_main_button_reads_done(drive):
    out = drive(OPEN + [["main"], ["wait", 20], ["snap", "s"]], sendAnswers=[sent_answer()])
    assert out["snaps"]["s"]["mb"]["text"] == "Done"


def test_done_leaves_the_page(drive):
    out = drive(OPEN + [["main"], ["wait", 20], ["main"], ["wait", 10], ["snap", "s"]],
                sendAnswers=[sent_answer()])
    assert out["snaps"]["s"]["view"] is False


def test_done_after_sent_sends_nothing_more(drive):
    out = drive(OPEN + [["main"], ["wait", 20], ["main"], ["wait", 10]],
                sendAnswers=[sent_answer()])
    assert len(_sends(out)) == 1


def test_copy_puts_the_reference_on_the_clipboard(drive):
    out = drive(OPEN + [["main"], ["wait", 20], ["click", "rp-copy"], ["wait", 10]],
                sendAnswers=[sent_answer("ap1-abcdefabcdef")], clipboard=True)
    assert out["copied"] == "ap1-abcdefabcdef"


def _try_later():
    return {"status": 200, "body": {"outcome": "offline", "reference": None,
                                    "line": wording.TRY_LATER, "retry": True,
                                    "repeat": False}}


def test_try_later_shows_the_line_on_the_form(drive):
    out = drive(OPEN + [["main"], ["wait", 20], ["snap", "s"]], sendAnswers=[_try_later()])
    assert (out["snaps"]["s"]["status"]["text"], out["snaps"]["s"]["resultHidden"]) == (
        wording.TRY_LATER, True)


def test_try_later_keeps_send_active(drive):
    out = drive(OPEN + [["main"], ["wait", 20], ["snap", "s"]], sendAnswers=[_try_later()])
    assert out["snaps"]["s"]["mb"]["is_active"] is True


def test_try_later_then_retry_sends_the_same_draft(drive, real):
    out = drive(OPEN + [["main"], ["wait", 20], ["main"], ["wait", 20]],
                sendAnswers=[_try_later(), sent_answer()])
    assert [s["body"]["draft"] for s in _sends(out)] == [real[0]["draft"]] * 2


@pytest.mark.parametrize("outcome, title", [
    ("disabled", "Not sent"), ("too_old", "Update needed"),
    ("daily_cap", "Daily limit reached"), ("invalid", "Not sent"),
])
def test_final_outcome_title(drive, outcome, title):
    answer = {"status": 200, "body": {"outcome": outcome, "reference": None,
                                      "line": wording.OUTCOME_LINES[outcome],
                                      "retry": False, "repeat": False}}
    out = drive(OPEN + [["main"], ["wait", 20], ["snap", "s"]], sendAnswers=[answer])
    assert out["snaps"]["s"]["resultTitle"] == title


def test_failed_outcome_title(drive):
    from aipager.bot import report_flow
    answer = {"status": 200, "body": {"outcome": "failed", "reference": None,
                                      "line": report_flow.SEND_FAILED_TEXT,
                                      "retry": False, "repeat": False}}
    out = drive(OPEN + [["main"], ["wait", 20], ["snap", "s"]], sendAnswers=[answer])
    assert out["snaps"]["s"]["resultLine"] == report_flow.SEND_FAILED_TEXT


def test_too_old_line_shows_no_raw_backticks(drive):
    answer = {"status": 200, "body": {"outcome": "too_old", "reference": None,
                                      "line": wording.OUTCOME_LINES["too_old"],
                                      "retry": False, "repeat": False}}
    out = drive(OPEN + [["main"], ["wait", 20], ["snap", "s"]], sendAnswers=[answer])
    line = out["snaps"]["s"]["resultLine"]
    assert "`" not in line and "aipager update" in line


def test_repeat_sent_shows_the_same_reference(drive):
    answer = sent_answer("ap1-111111111111")
    answer["body"]["repeat"] = True
    out = drive(OPEN + [["main"], ["wait", 20], ["snap", "s"]], sendAnswers=[answer])
    assert out["snaps"]["s"]["ref"] == "ap1-111111111111"


def test_note_changed_puts_the_normalized_note_in_the_field(drive):
    answer = {"status": 422, "body": {"error": "note_changed", "note": "tidied"}}
    out = drive(OPEN + [["type", "tidied\u200b"], ["main"], ["wait", 20], ["snap", "s"]],
                sendAnswers=[answer])
    assert out["snaps"]["s"]["note"] == "tidied"


def test_note_changed_stays_on_the_form(drive):
    answer = {"status": 422, "body": {"error": "note_changed", "note": "tidied"}}
    out = drive(OPEN + [["type", "tidied\u200b"], ["main"], ["wait", 20], ["snap", "s"]],
                sendAnswers=[answer])
    assert out["snaps"]["s"]["resultHidden"] is True


def test_note_changed_then_send_posts_the_normalized_note(drive):
    answer = {"status": 422, "body": {"error": "note_changed", "note": "tidied"}}
    out = drive(OPEN + [["type", "tidied\u200b"], ["main"], ["wait", 20], ["main"],
                        ["wait", 20]], sendAnswers=[answer, sent_answer()])
    assert _sends(out)[-1]["body"]["note"] == "tidied"


def test_stale_draft_loads_a_fresh_one(drive):
    out = drive(OPEN + [["main"], ["wait", 30]],
                sendAnswers=[{"status": 410, "body": {"error": "draft_gone"}}])
    assert (len(_drafts(out)), len(_sends(out))) == (2, 1)


def test_stale_draft_keeps_the_note(drive):
    out = drive(OPEN + [["type", "keep me"], ["main"], ["wait", 30], ["snap", "s"]],
                sendAnswers=[{"status": 410, "body": {"error": "draft_gone"}}])
    assert out["snaps"]["s"]["note"] == "keep me"


def test_forbidden_draft_shows_owner_only(drive):
    out = drive(OPEN + [["snap", "s"]],
                draftAnswers=[{"status": 403, "body": {"error": "forbidden"}}])
    assert out["snaps"]["s"]["resultTitle"] == "Owner only"


def test_private_only_draft_says_open_your_private_chat(drive):
    out = drive(OPEN + [["snap", "s"]],
                draftAnswers=[{"status": 403, "body": {"error": "private_only"}}])
    assert out["snaps"]["s"]["resultTitle"] == "Open your private chat"


def test_build_failed_offers_try_again(drive):
    out = drive(OPEN + [["snap", "s"]],
                draftAnswers=[{"status": 503, "body": {"error": "not_built"}}])
    assert out["snaps"]["s"]["mb"]["text"] == "Try again"


def test_build_failed_try_again_asks_for_a_new_draft(drive):
    out = drive(OPEN + [["main"], ["wait", 20]],
                draftAnswers=[{"status": 503, "body": {"error": "not_built"}}])
    assert len(_drafts(out)) == 2


# ---- the harness itself ------------------------------------------------------------------------

def test_the_harness_sees_a_page_that_never_sends(node_bin, tmp_path, real):
    """Guard the guard: with the send route renamed in the page, the
    harness must record no send, so the send tests above cannot pass on a
    page that does nothing."""
    draft, page = real
    broken = page.replace("/api/report/send", "/api/report/nowhere")
    out = _run_node(node_bin, tmp_path, broken, {
        "mainbutton": True, "draft": draft, "steps": OPEN + [["main"], ["wait", 20]]})
    assert _sends(out) == []
