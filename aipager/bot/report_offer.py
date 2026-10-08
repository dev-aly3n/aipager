"""The automatic problem report offer (roadmap 8.112 step 3, design
sections 2 and 4).

Run from the session monitor's tick (:func:`tick`, through
``TelegramBot.monitor_tick``). Every tick notes whether anything is busy
or pending (the offer needs everything idle for a while); at most every
:data:`OFFER_CHECK_INTERVAL` seconds :func:`maybe_offer` asks
``policy.due_offer`` with the live conditions, and follows the policy's
caller contract (roadmap 8.112, 1c):

- ``settle()`` results are persisted;
- the offer is recorded (``note_offer`` + ``store.save_policy``) BEFORE
  the notice is sent, and put back as it was when the notice did not go
  out (muted, skipped, refused), so an unseen offer neither spends the
  3-day slot nor later counts as a decline;
- each answer is bound to its offer through ``last_offer_ts`` in the
  button's callback data (handled in :mod:`aipager.bot.report_flow`);
- the notice is the lowest-priority send there is (ORNAMENT, skippable)
  and is never tried while the owner's chat is muted or backing off.

Nothing here raises into the tick, and no report content is logged.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from aipager.bot.flood import MUTE
from aipager.bot.flood_budget import PRIORITY_ORNAMENT, rate_limit_args
from aipager.bot.transport import MUTED, SKIPPED, send_text

if TYPE_CHECKING:
    from aipager.bot.core import TelegramBot

log = logging.getLogger("aipager.bot.report_offer")

#: Seconds between two offer checks (the tick itself runs every 2 s).
OFFER_CHECK_INTERVAL = 60

#: The innermost aipager file of an error, in plain words for the notice.
#: Anything else is named by its file's stem.
_AREAS = {
    "aipager/bot/animation.py": "the busy card",
    "aipager/bot/notify.py": "message delivery",
    "aipager/bot/handlers.py": "message handling",
    "aipager/bot/callbacks.py": "button taps",
    "aipager/bot/dashboard.py": "the pinned bar",
    "aipager/bot/session_ops.py": "session control",
    "aipager/bot/flood_budget.py": "the send budget",
    "aipager/bot/rich_message.py": "message delivery",
    "aipager/session_monitor.py": "the session monitor",
    "aipager/state.py": "session state",
    "aipager/dtach/hook_receiver.py": "the hook receiver",
    "aipager/dtach/notify_hook.py": "the hook",
    "aipager/dtach/inject.py": "prompt delivery",
    "aipager/miniapp/server.py": "the Mini App",
    "aipager/cli/daemon.py": "the daemon",
}

NOTICE_TAIL = ("A report helps get it fixed. It holds only versions and code "
               "locations, never your chats, prompts, names or paths.")
PREVIEW_BUTTON = "Preview report"
NOT_NOW_BUTTON = "Not now"
DONT_ASK_BUTTON = "Don't ask for this"


# ---- what is going on right now ---------------------------------------------

def busy_now(bot: "TelegramBot") -> bool:
    """Whether anything is busy or waiting to go out: a session working,
    asking, compacting or owed a card or a keyboard, held answers, or
    sends queued in the outbound gate. Errs towards busy (no offer)."""
    try:
        from aipager import self_update
        from aipager.state import Status

        if self_update.restart_blockers(bot.registry):
            return True
        for sess in bot.registry.all_sessions().values():
            if sess.status in (Status.GONE, Status.UNKNOWN):
                continue
            if (sess.stack_top_kind() == "compacting" or sess.busy_card_owed
                    or sess.notify_in_flight):
                return True
        if getattr(bot, "_keyboard_owed", None):
            return True
        from aipager.bot.rich_message import get_rate_limiter
        limiter = get_rate_limiter()
        if limiter is not None and any(
                row.get("waiters") for row in limiter.snapshot().get("chats", [])):
            return True
    except Exception as e:  # noqa: BLE001
        log.debug("report offer busy check failed (%s)", type(e).__name__)
        return True
    return False


def _same_chat(a, b) -> bool:
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return False


def dm_clear(owner: int) -> bool:
    """Whether the owner's chat may get an unsolicited message now: not
    muted, not in a 429 wait, a warning regime or minimal mode."""
    try:
        if MUTE.is_muted(owner):
            return False
        from aipager.bot.rich_message import get_rate_limiter
        limiter = get_rate_limiter()
        if limiter is None:
            return True
        if limiter.warning_remaining(owner) > 0 or limiter.minimal_mode(owner):
            return False
        for row in limiter.snapshot().get("chats", []):
            if _same_chat(row.get("chat_id"), owner) and row.get("retry_until_in", 0) > 0:
                return False
    except Exception as e:  # noqa: BLE001
        log.debug("report offer chat check failed (%s)", type(e).__name__)
        return False
    return True


def _developer_install() -> bool:
    """Design D4: an editable install, or one from a local folder or a
    checkout. Unknown counts as a developer's (no offer)."""
    try:
        from aipager.install_source import detect_install_source
        src = detect_install_source()
        return src.kind == "editable" or src.origin in ("local", "vcs")
    except Exception as e:  # noqa: BLE001
        log.debug("report offer install check failed (%s)", type(e).__name__)
        return True


# ---- the notice ----------------------------------------------------------------

def _area(entry: dict) -> str:
    frames = entry.get("frames") or []
    file = frames[0].get("file") if frames and isinstance(frames[0], dict) else None
    if not isinstance(file, str) or not file:
        return "aipager"
    return _AREAS.get(file, PurePosixPath(file).stem or "aipager")


def _times(n) -> str:
    n = n if type(n) is int and n > 0 else 1
    return "once" if n == 1 else f"{n} times"


def notice_text(entries: list[dict]) -> str:
    """The offer's text, from typed record fields only: the first
    offered error in a sentence, how many others, then what a report
    holds."""
    first = entries[0]
    count = _times(first.get("count"))
    trigger = first.get("trigger")
    if trigger == "crash":
        head = f"aipager stopped unexpectedly {count}."
    elif trigger == "hook_cap":
        head = f"The aipager hook hit its memory cap {count}."
    else:
        kind = str(first.get("type") or "error").rpartition(".")[2] or "error"
        head = f"aipager hit an internal error {count} ({kind} in {_area(first)})."
    others = len(entries) - 1
    if others == 1:
        head += " Also 1 other error."
    elif others > 1:
        head += f" Also {others} other errors."
    return f"{head} {NOTICE_TAIL}"


def notice_keyboard(offer_ts: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(PREVIEW_BUTTON, callback_data=f"_:rp:op:{offer_ts}")],
        [InlineKeyboardButton(NOT_NOW_BUTTON, callback_data=f"_:rp:on:{offer_ts}"),
         InlineKeyboardButton(DONT_ASK_BUTTON, callback_data=f"_:rp:od:{offer_ts}")],
    ])


# ---- the check ----------------------------------------------------------------

async def maybe_offer(bot: "TelegramBot", *, now: float | None = None,
                      mono: float | None = None) -> bool:
    """One full offer check now. Whether a notice went out."""
    from aipager import preferences
    from aipager.bot import report_flow
    from aipager.report import markers, policy, store

    now = time.time() if now is None else now
    mono = time.monotonic() if mono is None else mono
    owner = report_flow.resolve_owner(bot)
    clear = owner is not None and dm_clear(owner)
    state = store.policy_state()
    settled = policy.settle(state, int(now))
    if settled != state:
        store.save_policy(settled)
    if not clear:
        # No chat to offer in right now: nothing is chosen, no slot spent.
        return False
    last_busy = getattr(bot, "_report_last_busy_mono", None)
    if last_busy is None:
        # Never seen idle yet: idle from now, never "idle forever".
        bot._report_last_busy_mono = mono
        idle_for = 0.0
    else:
        idle_for = max(mono - last_busy, 0.0)
    seen = getattr(bot, "_report_owner_seen_at", None)
    cond = policy.Conditions(
        now=int(now),
        version=store._version(),
        first_start=markers.read_first_start(now),
        daemon_started=markers.started_at(),
        idle_for=idle_for,
        owner_active_ago=(now - seen) if seen is not None else None,
        owner_dm=clear,
        prompts_on=preferences.get_problem_reports() == "ask",
        env_off=os.environ.get("AIPAGER_REPORT_PROMPTS") == "0",
        developer_install=_developer_install(),
    )
    records = store.records()
    fingerprints = policy.due_offer(records, settled, cond)
    if not fingerprints:
        return False
    entries = {r["entry"]["fingerprint"]: r["entry"] for r in records}
    offered_entries = [entries[fp] for fp in fingerprints if fp in entries]
    if not offered_entries:
        return False
    offered = policy.note_offer(settled, fingerprints, now, cond.version)
    # Recorded BEFORE the notice goes out (the 1c contract): a crash in
    # between must not offer the same bugs again inside the gap.
    store.save_policy(offered)
    sent = None
    try:
        sent = await send_text(
            bot._app.bot, owner, notice_text(offered_entries),
            reply_markup=notice_keyboard(offered.last_offer_ts),
            disable_notification=True,
            rate_limit_args=rate_limit_args(kind="skip", priority=PRIORITY_ORNAMENT))
    except Exception as e:  # noqa: BLE001 - FloodSkipped, refusal, network
        log.info("problem report offer not sent (%s)", type(e).__name__)
        sent = None
    if sent is None or sent is MUTED or sent is SKIPPED:
        # Not shown: as if never offered (no slot spent, no decline later).
        store.save_policy(settled)
        return False
    log.info("problem report offer sent (%d errors)", len(offered_entries))
    return True


async def tick(bot: "TelegramBot", *, now: float | None = None,
               mono: float | None = None) -> None:
    """One session monitor tick: note whether anything is busy, and at
    most every :data:`OFFER_CHECK_INTERVAL` seconds check for an offer.
    Never raises."""
    mono = time.monotonic() if mono is None else mono
    try:
        if getattr(bot, "_report_last_busy_mono", None) is None or busy_now(bot):
            bot._report_last_busy_mono = mono
    except Exception as e:  # noqa: BLE001
        log.debug("report offer idle bookkeeping failed (%s)", type(e).__name__)
    last_check = getattr(bot, "_report_offer_checked_mono", None)
    if last_check is not None and mono - last_check < OFFER_CHECK_INTERVAL:
        return
    bot._report_offer_checked_mono = mono
    try:
        await maybe_offer(bot, now=now, mono=mono)
    except Exception as e:  # noqa: BLE001
        log.warning("problem report offer check failed (%s)", type(e).__name__)
        log.debug("problem report offer check failed", exc_info=True)
