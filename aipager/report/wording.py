"""What a problem report says to the person sending it (roadmap 8.112).

One table for ``aipager report`` in a terminal and for the preview card
in Telegram, so the two never drift: the explanation of what a report
holds, and one line per send outcome (:data:`aipager.report.send.OUTCOMES`).
Plain words, no em dashes.
"""

from __future__ import annotations

from aipager.report.send import SENDS_PER_DAY

#: The heading of the preview, in a terminal and on the card.
HEADING = "Report a problem"
#: What the report is, line by line, under the heading.
EXPLANATION = (
    "This is everything the report contains. Nothing has been sent yet.",
    "It has no chats, prompts, session names, folders, tokens, chat ids or usernames.",
    "It goes to the aipager maintainer's error inbox (Sentry), anonymously.",
)
INTRO = HEADING + "\n" + "".join(line + "\n" for line in EXPLANATION)
TRY_LATER = "Could not send right now. Nothing was lost; try again later."
OUTCOME_LINES = {
    "sent": "Sent. Reference {reference} (quote it if you open a GitHub issue).",
    "rate_limited": TRY_LATER,
    "rejected": TRY_LATER,
    "offline": TRY_LATER,
    "disabled": "Problem reports are switched off by the maintainer right now. Nothing was sent.",
    "too_old": ("This aipager version can no longer send reports. Update it "
                "(`aipager update`) and try again."),
    "daily_cap": (f"Reports are limited to {SENDS_PER_DAY} a day from one machine. "
                  "Try again tomorrow."),
    "invalid": "This report did not pass its own checks, so it was not sent.",
}
#: Outcomes after which the same report may be sent again later: the card
#: keeps its buttons.
RETRYABLE = frozenset({"rate_limited", "rejected", "offline"})


def outcome_line(outcome: str, reference: str | None = None) -> str:
    """The line for one send outcome; an unknown one reads as try later."""
    return OUTCOME_LINES.get(outcome, TRY_LATER).format(reference=reference)
