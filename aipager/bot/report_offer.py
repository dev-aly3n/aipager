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
  the notice is sent, and put back as it was only when the notice
  definitely did not go out (muted, skipped, refused by Telegram), so an
  unseen offer neither spends the 3-day slot nor later counts as a
  decline; a timeout or network error may have delivered it, so the
  offer stays (never a second notice for one the owner may be reading);
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
from telegram.error import BadRequest, Forbidden, RetryAfter

from aipager import i18n
from aipager.bot.flood import MUTE
from aipager.bot.flood_budget import PRIORITY_ORNAMENT, rate_limit_args
from aipager.bot.transport import MUTED, SKIPPED, send_text
from aipager.i18n import Msg, Plural, Safe

if TYPE_CHECKING:
    from aipager.bot.core import TelegramBot

log = logging.getLogger("aipager.bot.report_offer")

#: Seconds between two offer checks (the tick itself runs every 2 s).
OFFER_CHECK_INTERVAL = 60

#: The innermost aipager file of an error, in plain words for the notice.
#: Anything else is named by its file's stem.
_DELIVERY = Msg("rep.area.notify", "message delivery", note="Where an internal error happened, in plain words")
_AREAS = {
    "aipager/bot/animation.py": Msg("rep.area.animation", "the busy card", note="Where an internal error happened, in plain words"),
    "aipager/bot/notify.py": _DELIVERY,
    "aipager/bot/handlers.py": Msg("rep.area.handlers", "message handling", note="Where an internal error happened, in plain words"),
    "aipager/bot/callbacks.py": Msg("rep.area.callbacks", "button taps", note="Where an internal error happened, in plain words"),
    "aipager/bot/dashboard.py": Msg("rep.area.dashboard", "the pinned bar", note="Where an internal error happened, in plain words"),
    "aipager/bot/session_ops.py": Msg("rep.area.session_ops", "session control",
                                      note="Where an internal error happened, in plain words"),
    "aipager/bot/flood_budget.py": Msg("rep.area.flood_budget", "the send budget",
                                       note="Where an internal error happened, in plain words"),
    "aipager/bot/rich_message.py": _DELIVERY,
    "aipager/session_monitor.py": Msg("rep.area.session_monitor", "the session monitor",
                                      note="Where an internal error happened, in plain words"),
    "aipager/state.py": Msg("rep.area.state", "session state", note="Where an internal error happened, in plain words"),
    "aipager/dtach/hook_receiver.py": Msg("rep.area.hook_receiver", "the hook receiver",
                                          note="Where an internal error happened, in plain words"),
    "aipager/dtach/notify_hook.py": Msg("rep.area.notify_hook", "the hook", note="Where an internal error happened, in plain words"),
    "aipager/dtach/inject.py": Msg("rep.area.inject", "prompt delivery", note="Where an internal error happened, in plain words"),
    "aipager/miniapp/server.py": Msg("rep.area.server", "the Mini App", note="Where an internal error happened, in plain words"),
    "aipager/cli/daemon.py": Msg("rep.area.daemon", "the daemon", note="Where an internal error happened, in plain words"),
}

NOTICE_TAIL = Msg("rep.offer.tail",
                  "A report helps get it fixed. It holds only versions and code "
                  "locations, never your chats, prompts, names or paths.",
                  note="Ends the report offer. Keep the exact meaning: only versions and "
                       "code locations, none of the listed things")
PREVIEW_BUTTON = Msg("btn.rep.preview", "Preview report", note="Report offer button")
NOT_NOW_BUTTON = Msg("btn.rep.not_now", "Not now", note="Report offer button")
DONT_ASK_BUTTON = Msg("btn.rep.dont_ask", "Don't ask for this",
                      note="Report offer button: never offer for these errors again")

# What went wrong, one whole sentence per trigger: "once" is its own
# sentence; a count of 2 or more goes through the Plural.
_STOPPED_ONCE = Msg("rep.offer.stopped_once", "aipager stopped unexpectedly once.",
                    note="Report offer: aipager crashed one time")
_STOPPED = Plural("rep.offer.stopped", one="aipager stopped unexpectedly {n} time.",
                  other="aipager stopped unexpectedly {n} times.",
                  note="Report offer: aipager crashed {n} times (2 or more)")
_MEMCAP_ONCE = Msg("rep.offer.memcap_once", "The aipager hook hit its memory cap once.",
                   note="Report offer")
_MEMCAP = Plural("rep.offer.memcap", one="The aipager hook hit its memory cap {n} time.",
                 other="The aipager hook hit its memory cap {n} times.",
                 note="Report offer ({n} is 2 or more)")
_INTERNAL_ONCE = Msg("rep.offer.internal_once",
                     "aipager hit an internal error once ({error} in {area}).",
                     note="Report offer; {error} is the error's type (KeyError), {area} "
                          "where (rep.area.*)",
                     eg={"error": "KeyError", "area": "message delivery"})
_INTERNAL = Plural("rep.offer.internal",
                   one="aipager hit an internal error {n} time ({error} in {area}).",
                   other="aipager hit an internal error {n} times ({error} in {area}).",
                   note="Report offer ({n} is 2 or more)",
                   eg={"error": "KeyError", "area": "message delivery"})
_ALSO = Plural("rep.offer.also_other", one="Also {n} other error.",
               other="Also {n} other errors.", note="Report offer: more errors offered")

#: Errors that mean Telegram refused the notice: it was never shown. (The
#: outbound gate's skip and mute come back from ``send_text`` as SKIPPED
#: and MUTED.) Any other error, a timeout or a dropped connection above
#: all, may come after Telegram delivered it.
_NOT_DELIVERED = (BadRequest, Forbidden, RetryAfter)


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
        # A plain count (this runs every 2 s): no budget decayed or earned.
        if limiter is not None and limiter.waiting_sends():
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

def area(entry: dict, tr=None) -> str:
    """The readable place of one error entry: its innermost aipager file in
    plain words (:data:`_AREAS`, in *tr*'s language, English when None),
    else that file's stem, else "aipager". Shared by the offer notice and
    the Mini App's report page."""
    file = _innermost_file(entry)
    if file is None:
        return "aipager"
    words = _AREAS.get(file)
    if words is not None:
        return (tr or i18n.get("en")).text(words)
    return PurePosixPath(file).stem or "aipager"


def _innermost_file(entry: dict) -> str | None:
    frames = entry.get("frames") or []
    file = frames[0].get("file") if frames and isinstance(frames[0], dict) else None
    return file if isinstance(file, str) and file else None


def _area_in_sentence(entry: dict, tr) -> str:
    """:func:`area` for use inside the notice's sentence: a keyed area is
    the translation itself (``Safe``: not isolated like a name, and
    without its line marks, which mid-sentence are noise); a file
    stem stays a plain value, isolated like any name."""
    file = _innermost_file(entry)
    words = _AREAS.get(file) if file is not None else None
    if words is None:
        return area(entry, tr)
    return Safe(tr.bare(tr.text(words)))


def notice_text(entries: list[dict], tr=None) -> str:
    """The offer's text, from typed record fields only: the first
    offered error in a sentence, how many others, then what a report
    holds. In *tr*'s language (English when None)."""
    tr = tr or i18n.get("en")
    first = entries[0]
    n = first.get("count")
    n = n if type(n) is int and n > 0 else 1
    trigger = first.get("trigger")
    if trigger == "crash":
        head = tr.text(_STOPPED_ONCE) if n == 1 else tr.n(_STOPPED, n, kind="text")
    elif trigger == "hook_cap":
        head = tr.text(_MEMCAP_ONCE) if n == 1 else tr.n(_MEMCAP, n, kind="text")
    else:
        kind = str(first.get("type") or "error").rpartition(".")[2] or "error"
        where = _area_in_sentence(first, tr)
        head = (tr.text(_INTERNAL_ONCE, error=kind, area=where) if n == 1
                else tr.n(_INTERNAL, n, kind="text", error=kind, area=where))
    sentences = [head]
    others = len(entries) - 1
    if others > 0:
        sentences.append(tr.n(_ALSO, others, kind="text"))
    sentences.append(tr.text(NOTICE_TAIL))
    return " ".join(sentences)


def notice_keyboard(offer_ts: int, tr=None) -> InlineKeyboardMarkup:
    tr = tr or i18n.get("en")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(tr.text(PREVIEW_BUTTON), callback_data=f"_:rp:op:{offer_ts}")],
        [InlineKeyboardButton(tr.text(NOT_NOW_BUTTON), callback_data=f"_:rp:on:{offer_ts}"),
         InlineKeyboardButton(tr.text(DONT_ASK_BUTTON), callback_data=f"_:rp:od:{offer_ts}")],
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
    # The owner's private chat, in its language.
    tr = i18n.for_chat(owner)
    try:
        sent = await send_text(
            bot._app.bot, owner, notice_text(offered_entries, tr),
            reply_markup=notice_keyboard(offered.last_offer_ts, tr),
            disable_notification=True,
            rate_limit_args=rate_limit_args(kind="skip", priority=PRIORITY_ORNAMENT))
    except _NOT_DELIVERED as e:
        log.info("problem report offer not sent (%s)", type(e).__name__)
        sent = None
    except Exception as e:  # noqa: BLE001 - a timeout, network: maybe delivered
        log.info("problem report offer may not have arrived (%s): kept as offered",
                 type(e).__name__)
        return False
    if sent is None or sent is MUTED or sent is SKIPPED:
        # Not shown: as if never offered (no slot spent, no decline later),
        # unless the state moved on meanwhile (a /settings tap during the
        # send): only the offer this check wrote is taken back.
        if store.policy_state() == offered:
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
