"""One wording table for ``aipager report`` and the Telegram preview card
(roadmap 8.112 step 3), and no em dash in any of it."""

from __future__ import annotations

from aipager.bot import report_flow, report_offer
from aipager.cli import report as cli_report
from aipager.report import send, wording

EM_DASH = "—"


def test_the_cli_and_the_bot_share_the_same_table():
    assert cli_report.INTRO is wording.INTRO
    assert cli_report.OUTCOME_LINES is wording.OUTCOME_LINES
    assert cli_report.TRY_LATER is wording.TRY_LATER
    assert set(wording.OUTCOME_LINES) == set(send.OUTCOMES)


def test_the_intro_is_the_heading_and_the_explanation():
    assert wording.INTRO.splitlines() == [wording.HEADING, *wording.EXPLANATION]
    card = report_flow.card_text(report_flow.KeptReport(report={}, preview=b"{}", chat_id=1))
    for line in wording.EXPLANATION:
        assert line in card


def test_retryable_outcomes_are_the_try_later_ones():
    assert wording.RETRYABLE == {k for k, v in wording.OUTCOME_LINES.items()
                                 if v == wording.TRY_LATER}
    assert wording.outcome_line("sent", "ap1-x").startswith("Sent. Reference ap1-x ")
    assert wording.outcome_line("no-such-outcome") == wording.TRY_LATER


def _strings(module):
    return [v for k, v in vars(module).items()
            if not k.startswith("__") and isinstance(v, str)]


def test_no_em_dash():
    texts = (_strings(wording) + list(wording.OUTCOME_LINES.values())
             + list(wording.EXPLANATION) + _strings(report_flow) + _strings(report_offer)
             + list(report_flow.SETTINGS_ROW_LABELS.values()))
    assert len(texts) > 40
    assert [t for t in texts if EM_DASH in t] == []
