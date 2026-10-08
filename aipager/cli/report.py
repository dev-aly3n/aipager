"""``aipager report``: show a problem report and, if you say so, send it.

Roadmap 8.112. The report is built from the local store (read only: the
running daemon owns that file) and printed in full. It is sent only when
a person at a terminal answers yes: without a terminal on both stdin and
stdout (a script, a pipe, a Claude Code session's shell) it is printed
and never sent.
"""

from __future__ import annotations

import argparse
import sys

from aipager.report.send import SENDS_PER_DAY

INTRO = (
    "Report a problem\n"
    "This is everything the report contains. Nothing has been sent yet.\n"
    "It has no chats, prompts, session names, folders, tokens, chat ids or usernames.\n"
    "It goes to the aipager maintainer's error inbox (Sentry), anonymously.\n"
)
QUESTION = "Send this report? [y/N] "
NO_TERMINAL = "Not sent: run `aipager report` in a terminal to send it."
NOT_SENT = "Not sent."
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


def build_report(note: str | None) -> dict:
    """A manual report from the store and what can be read offline (no
    doctor checks: they would reach Telegram and Claude Code)."""
    from aipager.report import builder, store

    unclean, last_exit = store.exit_facts()
    try:
        from aipager.status import read_flood_chats
        flood = read_flood_chats()
    except Exception:  # noqa: BLE001 - a broken flood file still reports
        flood = []
    context = builder.ReportContext(unclean_exits_7d=unclean, last_exit=last_exit,
                                    flood_rows=flood)
    return builder.build_report("manual", errors=store.errors(),
                                counters=store.counters_24h(),
                                log_digest=store.digest_24h(), note=note, context=context)


def _at_a_terminal() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):  # a closed or replaced stream
        return False


def _confirmed() -> bool:
    try:
        answer = input(QUESTION)
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer.strip().lower() in ("y", "yes")


def cmd_report(args: argparse.Namespace, transport=None) -> int:
    """0 when sent, declined or only shown; 1 when a confirmed send did
    not go out."""
    from aipager.report import builder, send

    report = build_report(getattr(args, "note", None))
    print(INTRO)
    print(builder.render_preview(report))
    print()
    if not _at_a_terminal():
        print(NO_TERMINAL)
        return 0
    if not _confirmed():
        print(NOT_SENT)
        return 0
    result = send.send(report, transport=transport)
    print(OUTCOME_LINES.get(result.outcome, TRY_LATER).format(reference=result.reference))
    return 0 if result.outcome == send.SENT else 1
