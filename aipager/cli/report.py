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

# One wording table with the Telegram preview card.
from aipager.report.wording import INTRO, OUTCOME_LINES, TRY_LATER

QUESTION = "Send this report? [y/N] "
NO_TERMINAL = "Not sent: run `aipager report` in a terminal to send it."
NOT_SENT = "Not sent."


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
