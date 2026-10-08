"""Problem report drafts for the Mini App's report page (roadmap 8.112).

The page shows a report the daemon built and sends exactly that report
plus the note the page displayed:

- **One build path.** A draft is built by
  :func:`aipager.bot.report_flow.build_checked`, the same call that builds
  the Telegram preview card (``trigger="manual"``).
- **Kept here, bound to its owner.** Drafts live in daemon memory in
  ``bot._report_drafts`` (never the Telegram cards' dict, so neither can
  evict the other), keyed by an unguessable id, at most
  :data:`MAX_DRAFTS` at once, each for :data:`DRAFT_TTL`. A draft built
  for one person is invisible to everyone else.
- **What you see is what is sent.** The page sends only the draft id and
  its note. The note is taken only when it is already in its normalized
  form (``schema.normalize_note``); otherwise the caller hands the
  normalized note back for the person to check.
- **Once.** A send claims the draft before anything awaits; a try-later
  outcome opens it again, any other outcome is final and keeps only the
  outcome and reference, so a repeat tap gets the same reference.

Report and note content never reach a log line here.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aipager.report import wording

if TYPE_CHECKING:
    from aipager.bot.core import TelegramBot

log = logging.getLogger("aipager.bot.report_drafts")

#: Drafts kept at once (all people together; the oldest goes first, never
#: one being sent) and for how long.
MAX_DRAFTS = 3
DRAFT_TTL = 24 * 3600
#: The longest note a send request may carry, in code points (the note
#: itself is at most ``schema.NOTE_MAX``; this only bounds the input).
NOTE_INPUT_MAX = 2000
#: The outcome of a send that broke inside aipager (``send.send`` itself
#: never names it).
FAILED = "failed"


@dataclass
class Draft:
    """One report the Mini App shows, and what became of it."""
    id: str
    user_id: int
    report: dict | None
    preview: bytes
    created: float                 # report_flow._mono()
    state: str = "open"            # "open" | "sending" | "done"
    #: (outcome, reference) once final.
    result: tuple[str, str | None] | None = None
    send_task: "asyncio.Task | None" = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class SendOutcome:
    outcome: str
    reference: str | None
    line: str
    retry: bool


# ---- the store ---------------------------------------------------------------

def _drafts(bot: "TelegramBot") -> dict:
    drafts = getattr(bot, "_report_drafts", None)
    if drafts is None:
        drafts = {}
        bot._report_drafts = drafts
    return drafts


def _prune(bot: "TelegramBot") -> None:
    """Drop expired drafts, then the oldest beyond :data:`MAX_DRAFTS`;
    never a draft being sent (its outcome is still to be recorded)."""
    from aipager.bot import report_flow

    drafts = _drafts(bot)
    now = report_flow._mono()
    for key in [k for k, d in drafts.items()
                if d.state != "sending" and now - d.created > DRAFT_TTL]:
        del drafts[key]
    while len(drafts) > MAX_DRAFTS:
        idle = [k for k, d in drafts.items() if d.state != "sending"]
        if not idle:
            break
        del drafts[min(idle, key=lambda k: drafts[k].created)]


def _keep(bot: "TelegramBot", draft: Draft) -> None:
    _drafts(bot)[draft.id] = draft
    _prune(bot)


def lookup(bot: "TelegramBot", user_id, draft_id) -> Draft | None:
    """*user_id*'s draft *draft_id*; None when it is unknown, expired,
    evicted, or someone else's (all alike to the caller)."""
    _prune(bot)
    if not isinstance(draft_id, str):
        return None
    draft = _drafts(bot).get(draft_id)
    if draft is None or type(user_id) is not int or draft.user_id != user_id:
        return None
    return draft


async def open_draft(bot: "TelegramBot", user_id: int) -> Draft | None:
    """Build a manual report for *user_id* and keep it; None when it
    could not be built (nothing is kept)."""
    from aipager.bot import report_flow

    built = await report_flow.build_checked(bot, "manual")
    if built is None:
        return None
    report, preview = built
    draft = Draft(id=secrets.token_urlsafe(16), user_id=user_id, report=report,
                  preview=preview, created=report_flow._mono())
    _keep(bot, draft)
    return draft


def areas(report: dict) -> list[str]:
    """The readable place of each of *report*'s errors, in order (the
    offer notice's own words)."""
    from aipager.bot import report_offer

    return [report_offer.area(e) if isinstance(e, dict) else "aipager"
            for e in report.get("errors") or []]


# ---- the note ------------------------------------------------------------------

def check_note(draft: Draft, note: str) -> tuple[str, str, dict | None]:
    """(status, normalized note, the report to send) for *note* on an
    open *draft*:

    - ``"changed"``: *note* is not in its normalized form (the normalized
      one is returned, "" for none); nothing to send;
    - ``"refused"``: the report with that note fails its own checks;
    - ``"ok"``: the draft plus the note (absent when empty), validated.
    """
    from aipager.report import schema as sc
    from aipager.report import send

    expected = sc.normalize_note(note) or ""
    if expected != note:
        return "changed", expected, None
    candidate = copy.deepcopy(draft.report)
    if expected:
        candidate["note"] = expected
    clean, _problems = sc.validate(candidate)
    preview = send.checked_preview(clean) if clean == candidate else None
    if preview is None:
        return "refused", expected, None
    return "ok", expected, candidate


# ---- sending ---------------------------------------------------------------------

def reply_for(outcome: str, reference: str | None) -> SendOutcome:
    """The page's answer for one outcome: the shared wording, and whether
    the same draft may be sent again."""
    from aipager.bot import report_flow

    if outcome == FAILED:
        return SendOutcome(FAILED, None, report_flow.SEND_FAILED_TEXT, False)
    return SendOutcome(outcome, reference, report_flow.outcome_line(outcome, reference),
                       outcome in wording.RETRYABLE)


async def _run_send(draft: Draft, candidate: dict) -> SendOutcome:
    """Send *candidate* once, in a worker thread, and settle *draft*.
    Never raises."""
    from aipager.bot import report_flow
    from aipager.report import send

    try:
        # Read at send time: tests put a fake network here.
        result = await asyncio.to_thread(send.send, candidate,
                                         transport=report_flow.SEND_TRANSPORT)
    except Exception as e:  # noqa: BLE001 - send.send never raises; belt and braces
        log.warning("problem report send failed (%s)", type(e).__name__)
        result = None
    if result is None:
        outcome, reference = FAILED, None
    else:
        outcome, reference = result.outcome, result.reference
        log.info("problem report send: %s", outcome)
    reply = reply_for(outcome, reference)
    if reply.retry:
        draft.state = "open"
        return reply
    # Final: the report is let go; the outcome stays so a repeat tap gets
    # the same answer instead of a second send.
    draft.state = "done"
    draft.report = None
    draft.result = (outcome, reference)
    return reply


async def send_draft(bot: "TelegramBot", draft: Draft, candidate: dict) -> SendOutcome:
    """Send *candidate* (the open *draft* plus its checked note) once.

    The draft is claimed before anything awaits, so a second request sees
    it sending. The send runs in its own task and is awaited through a
    shield: a cancelled request still settles the draft."""
    from aipager.bot import report_flow

    draft.state = "sending"
    draft.send_task = report_flow._spawn(_run_send(draft, candidate))
    return await asyncio.shield(draft.send_task)


__all__ = ["DRAFT_TTL", "MAX_DRAFTS", "NOTE_INPUT_MAX", "Draft", "SendOutcome",
           "areas", "check_note", "lookup", "open_draft", "reply_for", "send_draft"]
