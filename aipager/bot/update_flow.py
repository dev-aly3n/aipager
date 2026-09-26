"""Admin self-update from Telegram and the Mini App (roadmap 8.36).

``/update`` offers one "Check for updates" button (roadmap 8.43). Tapping
it looks up both products and shows one line each, then ONE button that
updates only what has an update (Update aipager / Update Claude Code /
Update both). The Mini App's Updates block drives the SAME
:class:`UpdateManager` methods, and both surfaces take the offer from the
one function :func:`update_offer`, so the two cannot drift. A start always
goes through :meth:`UpdateManager.start_offer`, which re-derives the offer
from the last check rather than trusting the button. All real work
(spawning, fetching, the lock, the restart gate, the restart plan, the
marker) lives in :mod:`aipager.self_update`; this module is orchestration
and text.

Flood discipline: one status message per job, edited in place. Outcome
edits are ESSENTIAL; heartbeat and blocker edits are skippable ORNAMENTs,
sent only when their content changed and at most once per
``PROGRESS_EDIT_MIN_SECONDS``. Everything goes through the transport
seams, so a flood-muted chat receives nothing.

Callback data (short-callback family ``_``): ``_:up:chk``,
``_:up:go:<cc|ap|both>``, ``_:up:x``, ``_:up:now:<job>``,
``_:up:wait:<job>``, ``_:up:stop:<job>``. The pre-8.43 ``_:up:cc``,
``_:up:ap`` and ``_:up:both`` only answer that the menu is out of date.
"""

from __future__ import annotations

import asyncio
import html as html_mod
import logging
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from aipager import install_source, self_update
from aipager.bot.flood import FloodMuted
from aipager.bot.flood_budget import PRIORITY_ORNAMENT, rate_limit_args
from aipager.bot.transport import (
    MUTED,
    SKIPPED,
    _message_chat_id,
    _send_with_retry,
    calling_chat_id,
    edit_message,
    edit_text,
    edit_text_at,
    reply_text,
    send_text,
)

if TYPE_CHECKING:
    from aipager.bot import TelegramBot

log = logging.getLogger("aipager.bot.update_flow")

KINDS = ("claude", "aipager", "both")
CONTROLS = ("restart-now", "wait-more", "cancel")
TERMINAL_PHASES = frozenset({"done", "failed", "cancelled", "restart_scheduled"})
# Phases in which "Cancel" is still offered: nothing is being installed.
_CANCELLABLE = frozenset({"starting", "waiting_for_idle", "gate_timeout"})
# Phases in which an installer (or ``claude update``) may be writing to disk.
_INSTALLING = frozenset({"claude_updating", "upgrading"})
# A bound on each status lookup, beyond the per-request HTTP timeout.
STATUS_STEP_TIMEOUT_SECONDS = 20.0
# If we are still alive this long after scheduling a restart, it did not
# happen: release the lock and say so.
RESTART_WATCHDOG_SECONDS = 120.0
# At daemon shutdown, how long a running installer gets to finish before its
# process group is killed (systemd's TimeoutStopSec is 15 s in total).
SHUTDOWN_GRACE_SECONDS = 3.0
# The whole of UpdateManager.shutdown() — grace, kill, the job's last word,
# the helpers — fits in this, well under TimeoutStopSec=15, so the daemon's
# own registry save and bot stop still run before systemd's SIGKILL.
SHUTDOWN_DEADLINE_SECONDS = 8.0

DENIED_TEXT = "🚫 Only the admin can update aipager."
STALE_TEXT = "That update already finished"
# The pre-8.43 per-product buttons (``_:up:cc`` / ``ap`` / ``both``) and
# Mini App routes: they start nothing any more.
MENU_OUT_OF_DATE_TEXT = "This menu is out of date, send /update again"
CHECK_PROMPT_TEXT = ("⬆️ <b>Updates</b>\n\nCheck whether aipager or Claude Code "
                     "has a newer version.")
CHECK_BUTTON_TEXT = "🔄 Check for updates"
CHECKING_TEXT = "Checking…"
UP_TO_DATE_TEXT = "Everything is up to date."
CHECK_FAILED_TEXT = "Nothing newer found, but a check failed. Try again later."
OFFER_STALE_TEXT = "⚠️ This check is out of date. Check again before updating."
# How long an Update button (or the Mini App's) stays good after its check.
# Past this, or once any job has run, the tap asks for a fresh check.
OFFER_MAX_AGE_SECONDS = 600.0
# Callback verb <-> job kind for the one Update button.
_GO_CODES = {"cc": "claude", "ap": "aipager", "both": "both"}
_GO_VERBS = {kind: code for code, kind in _GO_CODES.items()}


def _esc(value) -> str:
    return html_mod.escape(str(value))


def _esc_text(value) -> str:
    """Escape for HTML message TEXT (not attributes): keeps "couldn't"."""
    return html_mod.escape(str(value), quote=False)


# ---------------------------------------------------------------------------
# Results and the job
# ---------------------------------------------------------------------------

@dataclass
class StartResult:
    ok: bool
    error: str | None = None
    job: dict | None = None


@dataclass
class ControlResult:
    ok: bool
    error: str | None = None
    job: dict | None = None


@dataclass
class _Job:
    id: int
    kind: str
    chat_id: int
    user_id: int | None
    origin: str
    started_at: float
    phase: str = "starting"
    sections: list[str] = field(default_factory=list)
    live: str | None = None
    blockers: list[str] = field(default_factory=list)
    summary: str = ""
    restart_now: bool = False
    wait_more: bool = False
    cancel_requested: bool = False
    # The transient systemd unit carrying the scheduled restart.
    restart_unit: str | None = None
    # The daemon shut down mid-install and killed the installer.
    interrupted: bool = False
    # ``(from, to)`` once the new aipager is on disk and proven, so a
    # shutdown after that point still leaves the normal A→B marker.
    installed: tuple[str, str] | None = None
    # The "installed, restarting when the current turn ends" section while
    # the post-install gate holds the restart (dropped before any marker is
    # written: the restart replaces it).
    waiting_section: str | None = None
    event: asyncio.Event | None = None
    reporter: "_Reporter | None" = None

    @property
    def terminal(self) -> bool:
        return self.phase in TERMINAL_PHASES

    def snapshot(self) -> dict:
        snap = {
            "id": self.id, "kind": self.kind, "phase": self.phase,
            "blockers": list(self.blockers), "summary": self.summary,
            "started_at": self.started_at,
        }
        if self.installed is not None:
            # The Mini App's restart watch waits for exactly this version.
            snap["installed"] = {"from": self.installed[0], "to": self.installed[1]}
        return snap

    def wake(self) -> None:
        if self.event is not None:
            self.event.set()


class _Reporter:
    """The job's ONE status message: created once, then edited in place."""

    def __init__(self, bot: "TelegramBot", chat_id: int, message=None):
        self.bot = bot
        self.chat_id = chat_id
        self.message = message
        self._send_attempted = message is not None
        self._last: tuple | None = None
        self._last_at = 0.0

    async def show(self, text: str, markup=None, *, ornament: bool = False) -> None:
        key = (text, _markup_key(markup))
        if key == self._last:
            return
        now = time.monotonic()
        if ornament and self.message is not None and \
                now - self._last_at < self_update.PROGRESS_EDIT_MIN_SECONDS:
            return
        kwargs: dict = {"parse_mode": "HTML", "reply_markup": markup}
        try:
            if self.message is None:
                # The first message of a Mini App job: ESSENTIAL, sent once.
                if self._send_attempted:
                    return
                self._send_attempted = True
                app = getattr(self.bot, "_app", None)
                if app is None:
                    return
                result = await send_text(app.bot, self.chat_id, text, **kwargs)
                if result is MUTED or result is SKIPPED or result is None:
                    return
                self.message = result
            else:
                if ornament:
                    kwargs["rate_limit_args"] = rate_limit_args(
                        kind="skip", priority=PRIORITY_ORNAMENT)
                result = await edit_message(self.message, text, **kwargs)
                if result is MUTED or result is SKIPPED:
                    return
        except Exception:
            log.debug("update status edit failed", exc_info=True)
            return
        self._last = key
        self._last_at = now


def _markup_key(markup):
    if markup is None:
        return None
    try:
        return tuple(tuple(b.callback_data for b in row)
                     for row in markup.inline_keyboard)
    except Exception:
        return id(markup)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _source_dict(source: install_source.InstallSource) -> dict:
    return {
        "kind": source.kind, "origin": source.origin,
        "detail": source.origin_detail, "upgradable": source.upgradable,
        "reason": source.reason,
        "describe": source.describe(show_paths=True),
        "describe_short": source.describe(show_paths=False),
    }


@dataclass(frozen=True)
class UpdateOffer:
    """What a check found and the ONE update it offers (roadmap 8.43).

    ``kind`` is ``"aipager"``, ``"claude"``, ``"both"`` or None (nothing
    to offer); ``lines`` holds one plain-text line per product."""

    kind: str | None
    label: str | None
    lines: list[str]
    summary: str | None
    restart: str | None
    notes: list[str]


def update_offer(status: dict) -> UpdateOffer:
    """THE decision both surfaces show: which products have an update this
    install can apply. A product is offered only when its lookup succeeded
    and found a strictly newer version (aipager also needs an install that
    can be upgraded from here)."""
    ap = status["aipager"]
    cl = status["claude"]
    rs = status["restart"]
    src = ap["source"]
    lines: list[str] = []
    notes: list[str] = []
    failed = False

    running = ap["running"]
    latest = ap.get("latest")
    offer_ap = False
    if not latest:
        failed = True
        lines.append(f"aipager {running} (couldn't check)")
    elif ap.get("update_available"):
        line = f"aipager {running} → {latest}"
        if src["upgradable"]:
            offer_ap = True
        else:
            line += f" (can't update from here: {src.get('reason') or 'unknown install'})"
        lines.append(line)
    else:
        lines.append(f"aipager {running} (up to date)")
    installed = ap.get("installed")
    if installed and installed != running:
        notes.append(f"aipager {installed} is installed but not running yet.")

    cur = cl.get("current")
    cl_latest = cl.get("latest")
    offer_cc = False
    if not cur:
        failed = True
        lines.append("Claude Code (couldn't check)")
    elif not cl_latest:
        failed = True
        lines.append(f"Claude Code {cur} (couldn't check)")
    elif cl.get("update_available"):
        offer_cc = True
        lines.append(f"Claude Code {cur} → {cl_latest}")
    else:
        lines.append(f"Claude Code {cur} (up to date)")

    if offer_ap and offer_cc:
        kind, label = "both", "Update both"
    elif offer_ap:
        kind, label = "aipager", "Update aipager"
    elif offer_cc:
        kind, label = "claude", "Update Claude Code"
    else:
        kind = label = None
    summary = None
    if kind is None:
        summary = CHECK_FAILED_TEXT if failed else UP_TO_DATE_TEXT
    restart = None
    if offer_ap:
        restart = ("Restart: automatic, once no turn is running." if rs["automatic"]
                   else f"Restart: manual ({rs.get('reason') or 'restart it yourself'})")
    return UpdateOffer(kind=kind, label=label, lines=lines, summary=summary,
                       restart=restart, notes=notes)


def _source_text(status: dict, show_paths: bool) -> str:
    src = status["aipager"]["source"]
    return str(src["describe"] if show_paths else src["describe_short"])


def check_payload(status: dict, *, show_paths: bool) -> dict:
    """The Mini App's view of a check: the same offer the chat shows."""
    offer = update_offer(status)
    return {
        "lines": list(offer.lines),
        "offer": ({"kind": offer.kind, "label": offer.label}
                  if offer.kind is not None else None),
        "summary": offer.summary,
        "restart": offer.restart,
        "notes": list(offer.notes),
        "source": _source_text(status, show_paths),
    }


def check_prompt_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(CHECK_BUTTON_TEXT, callback_data="_:up:chk")]])


def render_check(status: dict, *, show_paths: bool) -> tuple[str, InlineKeyboardMarkup]:
    """The chat's view of a check: the offer's lines, then ONE Update
    button and Cancel, or "Everything is up to date." and Check again."""
    offer = update_offer(status)
    lines = ["⬆️ <b>Updates</b>", ""]
    lines += [_esc_text(line) for line in offer.lines]
    lines.append(f"<i>{_esc_text(_source_text(status, show_paths))}</i>")
    lines += [_esc_text(n) for n in offer.notes]
    if offer.restart:
        lines.append(_esc_text(offer.restart))
    if offer.kind is None:
        lines += ["", _esc_text(offer.summary)]
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("Check again", callback_data="_:up:chk")]])
    else:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(offer.label,
                                 callback_data=f"_:up:go:{_GO_VERBS[offer.kind]}"),
            InlineKeyboardButton("Cancel", callback_data="_:up:x"),
        ]])
    return "\n".join(lines), kb


def _job_markup(job: _Job) -> InlineKeyboardMarkup | None:
    if job.phase == "waiting_for_idle":
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("Restart now", callback_data=f"_:up:now:{job.id}"),
            InlineKeyboardButton("Cancel", callback_data=f"_:up:stop:{job.id}"),
        ]])
    if job.phase == "gate_timeout":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("Wait 10 more min", callback_data=f"_:up:wait:{job.id}")],
            [InlineKeyboardButton("Restart now", callback_data=f"_:up:now:{job.id}"),
             InlineKeyboardButton("Cancel", callback_data=f"_:up:stop:{job.id}")],
        ])
    return None


def _job_text(job: _Job) -> str:
    parts = list(job.sections)
    if job.live:
        parts.append(job.live)
    if job.phase in ("waiting_for_idle", "gate_timeout") and job.blockers:
        parts.append("Still running:\n" + "\n".join(f"  • {_esc(b)}" for b in job.blockers))
    return "\n\n".join(parts) or "⏳ Starting the update…"


def _show_detail(chat_id) -> bool:
    """Paths and command output only go to a private chat (positive id)."""
    return isinstance(chat_id, int) and not isinstance(chat_id, bool) and chat_id > 0


def _safe_tail(text) -> str:
    """Defence in depth: the seam already redacts and trims output, but
    whatever reaches a chat is redacted (all of it, THEN cut) and capped
    again right here."""
    return self_update.redacted_tail(text)


def _pre_tail(text, chat_id) -> str:
    """``\n<pre>tail</pre>`` for a private chat; a pointer to the daemon log
    in a group (installer output can carry home-directory paths)."""
    tail = _safe_tail(text)
    if not tail:
        return ""
    if not _show_detail(chat_id):
        return "\n(output in the daemon log)"
    return f"\n<pre>{_esc(tail)}</pre>"


def _plain(text: str) -> str:
    import re
    return html_mod.unescape(re.sub(r"<[^>]+>", "", text))


def _elapsed_label(seconds: float) -> str:
    step = self_update.HEARTBEAT_GRANULARITY_SECONDS
    shown = int(seconds // step * step)
    if shown <= 0:
        return ""
    if shown < 60:
        return f" ({shown}s)"
    return f" ({shown // 60} min{' ' + str(shown % 60) + 's' if shown % 60 else ''})"


# ---------------------------------------------------------------------------
# The manager
# ---------------------------------------------------------------------------

class UpdateManager:
    """Owned by the bot (``bot.updates``); at most one job at a time."""

    def __init__(self, bot: "TelegramBot"):
        self.bot = bot
        self._job: _Job | None = None
        self._lock = self_update.UpdateLock()
        self._status_cache: tuple[float, dict] | None = None
        # The last "Check for updates" result, ``(monotonic, status)``: the
        # only thing an Update button may start from (see start_offer).
        self._checked: tuple[float, dict] | None = None
        # Bumped whenever a job starts or ends: a check that spans either
        # is not remembered (its versions may predate the job).
        self._job_epoch = 0
        self._tasks: set[asyncio.Task] = set()
        self._job_task: asyncio.Task | None = None
        self._last_job_id = 0
        # The job id that owns ``self._lock``: only that job may release it,
        # so a stale watchdog or a late ``finally`` cannot free a lock a
        # later job holds.
        self._lock_owner: int | None = None
        self._shutting_down = False
        # Set by deliver_update_marker in the daemon an update restarted
        # into: ``{"from", "to", "readopted", "missing"}``, shown by the
        # Mini App's restart watch.
        self.last_restart: dict | None = None

    # ---- read side -------------------------------------------------------

    @property
    def restart_pending(self) -> bool:
        """A restart is scheduled and has not happened (nor been given up on
        by the watchdog). The lock stays held throughout."""
        return self._job is not None and self._job.phase == "restart_scheduled"

    @property
    def shutting_down(self) -> bool:
        return self._shutting_down

    @property
    def busy(self) -> bool:
        """A job is running OR its restart is still pending: no second
        update, no voice restart, and no start buttons meanwhile."""
        return self._job is not None and (not self._job.terminal
                                          or self.restart_pending)

    def snapshot(self) -> dict | None:
        return self._job.snapshot() if self._job is not None else None

    def invalidate_status(self) -> None:
        self._status_cache = None

    async def status(self, *, force: bool = False) -> dict:
        now = time.monotonic()
        cached = self._status_cache
        if (not force and cached is not None
                and now - cached[0] < self_update.STATUS_CACHE_SECONDS):
            return cached[1]
        data = await self._collect_status()
        self._status_cache = (time.monotonic(), data)
        return data

    async def check(self) -> dict:
        """"Check for updates": a fresh lookup of both products, remembered
        as the basis every Update button starts from."""
        epoch = self._job_epoch
        data = await self.status(force=True)
        if epoch == self._job_epoch and not self.busy:
            self._checked = (time.monotonic(), data)
        else:
            log.info("update.check.discarded reason=job-started-or-ended")
        return data

    async def _collect_status(self) -> dict:
        async def bounded(fn, *args):
            try:
                return await asyncio.wait_for(asyncio.to_thread(fn, *args),
                                              timeout=STATUS_STEP_TIMEOUT_SECONDS)
            except Exception:
                log.debug("update status lookup %s failed", getattr(fn, "__name__", fn),
                          exc_info=True)
                return None

        async def claude_side():
            cur = await bounded(self_update.current_claude)
            realpath = os.path.realpath(cur[0]) if cur else None
            try:
                method, channel = self_update.claude_channel_info(realpath)
            except Exception:
                method, channel = "unknown", "latest"
            latest = None
            if method != "unknown":
                latest = await bounded(self_update.latest_claude_version, method, channel)
            return cur, method, channel, latest

        source = install_source.detect_install_source()
        running = self_update.running_version()
        installed = self_update.installed_version()
        latest_ap, claude, plan = await asyncio.gather(
            bounded(self_update.latest_aipager_version),
            claude_side(),
            bounded(self_update.restart_plan, self.bot.registry),
        )
        cur, method, channel, latest_cl = claude
        if plan is None:
            plan = self_update.RestartPlan(
                "foreground", False, "could not tell how this daemon is run")
        log.info("update.check running=%s latest=%s claude=%s claude_latest=%s "
                 "source=%s restart=%s", running, latest_ap,
                 cur[1] if cur else None, latest_cl, source.kind, plan.mode)
        return {
            "aipager": {
                "running": running,
                "installed": installed,
                "latest": latest_ap,
                "update_available": self_update.is_newer(latest_ap, running),
                "source": _source_dict(source),
            },
            "claude": {
                "current": cur[1] if cur else None,
                "latest": latest_cl,
                "update_available": bool(cur) and self_update.is_newer(latest_cl, cur[1]),
                "method": method,
                "channel": channel,
            },
            "restart": plan.to_dict(),
        }

    # ---- write side ------------------------------------------------------

    async def start_offer(self, kind: str, *, chat_id: int, user_id: int | None,
                          origin: str, status_message=None) -> StartResult:
        """The ONE entry point for an Update button, in chat or the Mini App.

        ``kind`` is what the tapped button said. It starts only if the last
        check, still fresh and taken since the last job, offers exactly
        that: a stale button, a hand-made request or a product with nothing
        newer starts nothing. No lookup happens here."""
        if kind not in KINDS:
            raise ValueError(f"unknown update kind {kind!r}")
        # start() repeats these two refusals; they are here too so a
        # refused tap never reads (or reports on) the stored check.
        if self._shutting_down:
            return StartResult(False, "shutting_down", self.snapshot())
        if self.busy:
            log.info("update.lock_busy user=%s chat=%s job=%s", user_id, chat_id,
                     self._job.id if self._job else None)
            return StartResult(False, "update_in_progress", self.snapshot())
        checked = self._checked
        if checked is None or time.monotonic() - checked[0] > OFFER_MAX_AGE_SECONDS:
            log.info("update.offer.expired user=%s chat=%s kind=%s", user_id, chat_id, kind)
            return StartResult(False, "check_expired", None)
        offer = update_offer(checked[1])
        if offer.kind != kind:
            log.info("update.offer.changed user=%s chat=%s asked=%s offered=%s",
                     user_id, chat_id, kind, offer.kind)
            return StartResult(False, "offer_changed", None)
        return await self.start(kind, chat_id=chat_id, user_id=user_id,
                                origin=origin, status_message=status_message)

    async def start(self, kind: str, *, chat_id: int, user_id: int | None,
                    origin: str, status_message=None) -> StartResult:
        if kind not in KINDS:
            raise ValueError(f"unknown update kind {kind!r}")
        if self._shutting_down:
            # Nothing started now could be seen through: its installer
            # would not be in shutdown()'s snapshot and would be orphaned.
            log.info("update.refused user=%s chat=%s reason=shutting-down", user_id, chat_id)
            return StartResult(False, "shutting_down", self.snapshot())
        if self.busy:
            log.info("update.lock_busy user=%s chat=%s job=%s", user_id, chat_id,
                     self._job.id if self._job else None)
            return StartResult(False, "update_in_progress", self.snapshot())
        if kind in ("aipager", "both"):
            source = install_source.detect_install_source()
            if not source.upgradable:
                log.info("update.aipager.refused user=%s chat=%s kind=%s reason=%s",
                         user_id, chat_id, source.kind, source.reason)
                return StartResult(False, "not_upgradable", None)
        if not self._lock.try_acquire():
            log.info("update.lock_busy user=%s chat=%s (held elsewhere)",
                     user_id, chat_id)
            return StartResult(False, "update_in_progress", self.snapshot())
        job_id = max(int(time.time()), self._last_job_id + 1)
        self._last_job_id = job_id
        self._lock_owner = job_id
        job = _Job(id=job_id, kind=kind, chat_id=chat_id, user_id=user_id,
                   origin=origin, started_at=time.time(), event=asyncio.Event())
        job.reporter = _Reporter(self.bot, chat_id, status_message)
        self._job = job
        self._job_epoch += 1
        log.info("update.start job=%s kind=%s user=%s chat=%s origin=%s",
                 job.id, kind, user_id, chat_id, origin)
        self._job_task = self._spawn(self._run_job(job))
        return StartResult(True, None, job.snapshot())

    def control(self, action: str, job_id) -> ControlResult:
        job = self._job
        if (job is None or job.terminal or not isinstance(job_id, int)
                or isinstance(job_id, bool) or job.id != job_id):
            return ControlResult(False, "no_matching_job", self.snapshot())
        if action == "restart-now":
            job.restart_now = True
        elif action == "wait-more":
            if job.phase != "gate_timeout":
                return ControlResult(False, "no_matching_job", job.snapshot())
            job.wait_more = True
        elif action == "cancel":
            if job.phase not in _CANCELLABLE:
                return ControlResult(False, "no_matching_job", job.snapshot())
            job.cancel_requested = True
        else:
            raise ValueError(f"unknown control {action!r}")
        log.info("update.control job=%s action=%s", job.id, action)
        job.wake()
        return ControlResult(True, None, job.snapshot())

    # ---- the job ---------------------------------------------------------

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _report(self, job: _Job, *, ornament: bool = False) -> None:
        if job.reporter is not None:
            await job.reporter.show(_job_text(job), _job_markup(job), ornament=ornament)

    def _release_lock(self, job: _Job) -> None:
        """Release the update lock iff ``job`` owns it."""
        if self._lock_owner != job.id:
            log.info("update.lock.not_owner job=%s owner=%s", job.id, self._lock_owner)
            return
        self._lock_owner = None
        self._lock.release()

    def _finish(self, job: _Job, phase: str, section: str | None) -> None:
        if section:
            job.sections.append(section)
            job.summary = _plain(section)
        job.live = None
        job.phase = phase

    async def _run_job(self, job: _Job) -> None:
        # Tags every self_update log line (spawns, claude, restart) with
        # this job; to_thread copies the context into the worker thread.
        self_update.CURRENT_JOB_ID.set(job.id)
        restart_scheduled = False
        try:
            await self._report(job)
            if job.kind in ("claude", "both"):
                await self._claude_step(job)
            if job.kind in ("aipager", "both") and not job.terminal:
                if self._shutting_down:
                    # "both": Claude Code finished inside the shutdown grace;
                    # do not begin the aipager half now.
                    self._finish(job, "cancelled", self._shutdown_section(job))
                else:
                    restart_scheduled = await self._aipager_step(job)
            if not job.terminal:
                job.phase = "done"
                job.live = None
        except asyncio.CancelledError:
            if self._shutting_down and job.installed is not None:
                # Cancelled in the post-install gate: B is on disk and the
                # next start runs it, so say so (and leave the A→B marker).
                self._installed_at_shutdown(job, *job.installed)
            else:
                self._finish(job, "cancelled",
                             self._shutdown_section(job) if self._shutting_down else None)
            raise
        except Exception:
            log.exception("update.failed job=%s", job.id)
            self._finish(job, "failed", "❌ The update stopped on an unexpected error "
                                        "(see the daemon log).")
        finally:
            if not restart_scheduled:
                self._release_lock(job)
            self._status_cache = None
            # The versions have moved: a button from before this job must
            # not start another one.
            self._checked = None
            self._job_epoch += 1
            try:
                await self._report(job)
            except Exception:
                log.debug("final update report failed", exc_info=True)
        if restart_scheduled:
            self._spawn(self._restart_watchdog(job))

    async def _await_with_heartbeat(self, job: _Job, label: str, fn, *args):
        """Run blocking ``fn`` in a thread, editing a heartbeat meanwhile."""
        started = time.monotonic()
        job.live = f"⏳ {label}…"
        await self._report(job)
        fut = asyncio.ensure_future(asyncio.to_thread(fn, *args))
        while True:
            done, _ = await asyncio.wait(
                {fut}, timeout=max(0.01, float(self_update.PROGRESS_EDIT_MIN_SECONDS)))
            if done:
                return fut.result()
            job.live = f"⏳ {label} — still working{_elapsed_label(time.monotonic() - started)}"
            await self._report(job, ornament=True)

    # ---- Claude Code -------------------------------------------------------

    def _session_note(self, job: _Job, new_version: str | None) -> str:
        from aipager.state import Status

        registry = self.bot.registry
        everyone = {n: s for n, s in registry.all_sessions().items()
                    if s.status != Status.GONE}
        mine = {n: s for n, s in registry.all_sessions(job.chat_id).items()
                if s.status != Status.GONE}
        others = len(set(everyone) - set(mine))
        lines = ["Running sessions keep the old version until they are "
                 "restarted — none was restarted."]
        if mine:
            lines.append("Still on the old version: " + ", ".join(
                _esc(s.label or s.name) for _, s in sorted(mine.items())))
        if others:
            lines.append(f"and {others} in other chats")
        if not mine and not others:
            lines.append("No sessions are running right now.")
        if new_version:
            lines.append(f"New sessions use {_esc(new_version)}.")
        return "\n".join(lines)

    async def _claude_step(self, job: _Job) -> None:
        job.phase = "claude_updating"
        res = await self._await_with_heartbeat(job, "Updating Claude Code",
                                               self_update.run_claude_update)
        job.live = None
        if job.interrupted or (self._shutting_down
                               and res.error == self_update.SHUTTING_DOWN_ERROR):
            self._finish(job, "cancelled", self._shutdown_section(job))
            return
        if res.error and res.before is None:
            section = f"❌ <b>Claude Code</b>: {_esc(_safe_tail(res.error))}"
        else:
            if res.timed_out:
                head = (f"⏱ <b>Claude Code</b> update timed out after "
                        f"{self_update.CLAUDE_UPDATE_TIMEOUT_SECONDS}s")
            elif res.returncode != 0 or res.error:
                head = (f"❌ <b>Claude Code</b> update failed "
                        f"(exit {res.returncode if res.returncode is not None else '?'})")
            elif res.after and res.before and res.after != res.before:
                head = f"✅ <b>Claude Code</b> {_esc(res.before)} → {_esc(res.after)}"
            elif res.after is None and self._shutting_down:
                # The version probe is not started during a shutdown.
                head = ("✅ <b>Claude Code</b> update finished; aipager is shutting "
                        "down, so /update shows the new version once it is back")
            else:
                head = (f"✅ <b>Claude Code</b> is already up to date "
                        f"({_esc(res.after or res.before)})")
            if not res.ok:
                head += _pre_tail(res.output_tail, job.chat_id)
            section = head + "\n" + self._session_note(job, res.after or res.before)
        job.sections.append(section)
        job.summary = _plain(section)
        if job.kind == "claude":
            job.phase = "done" if res.ok else "failed"
        await self._report(job)

    # ---- aipager -------------------------------------------------------------

    def _manual_restart_text(self, plan) -> str:
        lines = [f"Restart needed: {_esc(plan.reason or 'restart it yourself')}"]
        if plan.manual_command:
            lines.append(f"<code>{_esc(plan.manual_command)}</code>")
        if plan.at_risk:
            lines.append("Sessions a restart would kill: "
                         + ", ".join(_esc(s) for s in plan.at_risk))
        return "\n".join(lines)

    async def _aipager_step(self, job: _Job) -> bool:
        registry = self.bot.registry
        source = install_source.detect_install_source()
        running = self_update.running_version()
        if not source.upgradable:
            log.info("update.aipager.refused job=%s kind=%s", job.id, source.kind)
            self._finish(job, "failed", f"🚫 <b>aipager</b> can't be updated from here: "
                                        f"{_esc(source.reason)}")
            return False
        argv = install_source.upgrade_argv(source)
        if argv is None:
            self._finish(job, "failed",
                         f"❌ <b>aipager</b>: couldn't find <code>{_esc(source.kind)}</code> "
                         f"(searched {_esc(install_source.augmented_path())})")
            return False
        plan = await asyncio.to_thread(self_update.restart_plan, registry)

        if source.origin == "index":
            latest = await asyncio.to_thread(self_update.latest_aipager_version)
            if latest and not self_update.is_newer(latest, running):
                log.info("update.aipager.unchanged job=%s running=%s latest=%s",
                         job.id, running, latest)
                self._finish(job, "done", f"✅ <b>aipager</b> is already up to date "
                                          f"({_esc(running)}).")
                return False

        if plan.automatic:
            if await self._wait_gate(job) == "cancel":
                self._finish(job, "cancelled",
                             "↩️ <b>aipager</b> update cancelled — nothing was installed.")
                return False

        job.phase = "upgrading"
        log.info("update.aipager.start job=%s argv=%s", job.id, argv)
        res = await self._await_with_heartbeat(
            job, f"Upgrading aipager via {_esc(source.kind)}",
            lambda: self_update.run_command(
                argv, timeout=self_update.UPGRADE_TIMEOUT_SECONDS,
                env=install_source.upgrade_env(source)))
        job.live = None
        if job.interrupted or (self._shutting_down
                               and res.error == self_update.SHUTTING_DOWN_ERROR):
            self._finish(job, "cancelled", self._shutdown_section(job))
            return False
        if res.timed_out:
            log.info("update.aipager.timeout job=%s", job.id)
            hint = install_source.reinstall_hint(source.kind)
            self._finish(job, "failed",
                         f"⏱ <b>aipager</b> upgrade timed out after "
                         f"{self_update.UPGRADE_TIMEOUT_SECONDS}s and was stopped — "
                         f"nothing restarted, still running {_esc(running)}. The "
                         f"install on disk may be partial: reinstall with "
                         f"<code>{_esc(hint)}</code> before the next restart."
                         + _pre_tail(res.output_tail, job.chat_id))
            return False
        if res.returncode != 0 or res.error:
            log.info("update.aipager.failed job=%s rc=%s", job.id, res.returncode)
            self._finish(job, "failed",
                         f"❌ <b>aipager</b> upgrade failed (exit "
                         f"{res.returncode if res.returncode is not None else '?'}) — "
                         f"nothing restarted."
                         + _pre_tail(res.output_tail or res.error, job.chat_id))
            return False

        new, importable, err = await asyncio.to_thread(
            self_update.probe_installed_version, source.python)
        if job.interrupted:
            # The shutdown killed the probe: the install may be partial.
            self._finish(job, "cancelled", self._shutdown_section(job))
            return False
        if self._shutting_down and err == self_update.SHUTTING_DOWN_ERROR:
            # Installed within the shutdown grace: the seam spawned no probe
            # (nothing new starts now), and no restart follows.
            self._installed_unprobed_at_shutdown(job, running)
            return False
        if not importable:
            log.info("update.aipager.smoke_failed job=%s error=%s", job.id, err)
            hint = install_source.reinstall_hint(source.kind)
            self._finish(job, "failed",
                         f"❌ The new version failed to import — still running "
                         f"{_esc(running)}, nothing restarted. Reinstall with "
                         f"<code>{_esc(hint)}</code>"
                         + _pre_tail(err, job.chat_id))
            return False
        if new == running:
            log.info("update.aipager.unchanged job=%s version=%s origin=%s",
                     job.id, running, source.origin)
            self._finish(job, "done", f"✅ <b>aipager</b> is already at {_esc(running)}"
                                      f"{_origin_explanation(source, job.chat_id)}")
            return False
        log.info("update.aipager.done job=%s from=%s to=%s", job.id, running, new)
        job.installed = (running, new)
        if self._shutting_down:
            self._installed_at_shutdown(job, running, new)
            return False

        if not plan.automatic:
            log.info("update.restart.refused job=%s mode=%s", job.id, plan.mode)
            self._finish(job, "done",
                         f"✅ <b>aipager</b> {_esc(running)} → {_esc(new)} installed.\n"
                         + self._manual_restart_text(plan))
            return False

        if not job.restart_now:
            job.waiting_section = (f"⏳ <b>aipager</b> {_esc(running)} → {_esc(new)} "
                                   "installed, restarting when the current turn ends")
            job.sections.append(job.waiting_section)
            if await self._wait_gate(job) == "cancel":
                self._drop_waiting_section(job)
                self._finish(job, "cancelled",
                             f"✅ <b>aipager</b> {_esc(running)} → {_esc(new)} installed, "
                             "but the restart was cancelled. Restart it yourself: "
                             f"<code>{_esc(plan.manual_command)}</code>")
                return False
            self._drop_waiting_section(job)

        plan = await asyncio.to_thread(self_update.restart_plan, registry)
        if self._shutting_down:
            # The gate or the re-read can span the start of a shutdown.
            self._installed_at_shutdown(job, running, new)
            return False
        if not plan.automatic:
            log.info("update.restart.refused job=%s mode=%s (re-read)", job.id, plan.mode)
            self._finish(job, "done",
                         f"✅ <b>aipager</b> {_esc(running)} → {_esc(new)} installed.\n"
                         + self._manual_restart_text(plan))
            return False

        try:
            self_update.write_marker(self._update_marker(job, running, new))
        except OSError as e:
            log.info("update.restart.failed job=%s marker: %s", job.id, e)
            self._finish(job, "failed",
                         f"✅ <b>aipager</b> {_esc(running)} → {_esc(new)} installed, but "
                         f"the restart could not be prepared ({_esc(e)}). Restart it "
                         f"yourself: <code>{_esc(plan.manual_command)}</code>")
            return False
        ok, detail = await asyncio.to_thread(self_update.schedule_restart, plan)
        if self._shutting_down:
            # Shutdown began while systemd-run ran. A timer that did get
            # made must not bring a stopped daemon back 5 s later.
            if ok:
                await asyncio.to_thread(self_update.cancel_scheduled_restart, detail,
                                        during_shutdown=True)
            self._installed_at_shutdown(job, running, new)
            return False
        if not ok:
            self_update.clear_marker()
            self._finish(job, "failed",
                         f"✅ <b>aipager</b> {_esc(running)} → {_esc(new)} installed, but "
                         f"scheduling the restart failed: {_esc(detail)}. Restart it "
                         f"yourself: <code>{_esc(plan.manual_command)}</code>")
            return False
        job.restart_unit = detail
        # No countdown (roadmap 8.45): the new daemon edits this message
        # into "✅ aipager updated A → B, N sessions re-adopted".
        self._finish(job, "restart_scheduled",
                     f"⏳ <b>aipager</b> {_esc(running)} → {_esc(new)} installed, "
                     "restarting…")
        return True

    @staticmethod
    def _drop_waiting_section(job: _Job) -> None:
        if job.waiting_section is not None and job.waiting_section in job.sections:
            job.sections.remove(job.waiting_section)
        job.waiting_section = None

    async def _wait_gate(self, job: _Job) -> str:
        """``"open"``, ``"bypass"`` or ``"cancel"``."""
        if job.restart_now:
            log.info("update.gate.bypass job=%s", job.id)
            job.phase = "upgrading"
            return "bypass"
        loop = asyncio.get_running_loop()
        job.phase = "waiting_for_idle"
        deadline = loop.time() + self_update.GATE_MAX_WAIT_SECONDS
        decision_deadline = None
        streak = 0
        log.info("update.gate.wait job=%s", job.id)
        first = True
        while True:
            if job.restart_now:
                log.info("update.gate.bypass job=%s", job.id)
                job.blockers = []
                job.phase = "upgrading"
                return "bypass"
            if job.cancel_requested:
                log.info("update.gate.cancel job=%s", job.id)
                job.blockers = []
                return "cancel"
            blockers = self_update.restart_blockers(self.bot.registry)
            changed = blockers != job.blockers
            job.blockers = blockers
            streak = 0 if blockers else streak + 1
            if streak >= self_update.GATE_SETTLE_POLLS:
                log.info("update.gate.open job=%s", job.id)
                # Past the gate nothing may be cancelled any more: the
                # installer or the restart is about to run.
                job.phase = "upgrading"
                job.live = None
                return "open"
            now = loop.time()
            phase_changed = False
            if job.phase == "gate_timeout" and job.wait_more:
                job.wait_more = False
                job.phase = "waiting_for_idle"
                deadline = now + self_update.GATE_MAX_WAIT_SECONDS
                decision_deadline = None
                phase_changed = True
            if job.phase == "waiting_for_idle" and now >= deadline:
                log.info("update.gate.timeout job=%s blockers=%s", job.id, blockers)
                job.phase = "gate_timeout"
                decision_deadline = now + self_update.GATE_DECISION_MAX_SECONDS
                phase_changed = True
            if (job.phase == "gate_timeout" and decision_deadline is not None
                    and now >= decision_deadline):
                log.info("update.gate.cancel job=%s reason=unanswered", job.id)
                job.blockers = []
                return "cancel"
            job.live = self._gate_live(job)
            if first or phase_changed:
                await self._report(job)
            elif changed:
                await self._report(job, ornament=True)
            first = False
            job.event.clear()
            try:
                await asyncio.wait_for(job.event.wait(),
                                       timeout=self_update.GATE_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass

    def _update_marker(self, job: _Job, running: str, new: str) -> dict:
        return {
            "from": running, "to": new, "chat_id": job.chat_id,
            "user_id": job.user_id, "job_id": job.id,
            "sessions": [
                {"name": s.name, "label": s.label}
                for s in self.bot.registry.all_sessions().values()
                if s.status.name != "GONE"
            ],
            "scheduled_at": time.time(),
            "message": self._message_ref(job),
        }

    @staticmethod
    def _message_ref(job: _Job) -> dict | None:
        """The job's status message, for the next daemon to edit into the
        outcome (roadmap 8.45); ``None`` when there is none to edit (a
        Mini App job whose first send never went out)."""
        msg = job.reporter.message if job.reporter is not None else None
        # None or MUTED (no message went out) has no int ids: None below.
        chat_id = _message_chat_id(msg)
        message_id = getattr(msg, "message_id", None)
        if not all(isinstance(v, int) and not isinstance(v, bool)
                   for v in (chat_id, message_id)):
            return None
        # Every caller drops the waiting section first: the restart (or
        # the next start) replaces it.
        return {"chat_id": chat_id, "message_id": message_id,
                "prefix": "\n\n".join(job.sections)}

    def _installed_unprobed_at_shutdown(self, job: _Job, running: str) -> None:
        """The installer finished but the daemon is stopping before the
        import probe: read the version on disk in-process, leave the A→B
        marker, and let the next start run it."""
        new = self_update.installed_version()
        if not new or new == running:
            log.info("update.restart.skipped job=%s reason=shutting-down to=%s",
                     job.id, new)
            self._finish(job, "done",
                         "✅ <b>aipager</b> upgrade finished; the daemon is shutting "
                         "down, so nothing was restarted — the next start runs what "
                         "is installed.")
            return
        self._installed_at_shutdown(job, running, new)

    def _installed_at_shutdown(self, job: _Job, running: str, new: str) -> None:
        """B is installed but the daemon is stopping: never schedule a
        restart (it would undo an operator's stop). Leave the normal A→B
        marker so the next start announces B."""
        log.info("update.restart.skipped job=%s reason=shutting-down from=%s to=%s",
                 job.id, running, new)
        self._drop_waiting_section(job)
        try:
            self_update.write_marker(self._update_marker(job, running, new))
        except OSError:
            log.warning("update.shutdown.marker_failed job=%s", job.id, exc_info=True)
        self._finish(job, "done",
                     f"✅ <b>aipager</b> {_esc(running)} → {_esc(new)} installed; the "
                     f"daemon is shutting down, so nothing was restarted — the next "
                     f"start runs {_esc(new)}.")

    def _shutdown_section(self, job: _Job) -> str:
        if job.interrupted:
            if job.phase == "claude_updating":
                fix = "run <code>claude update</code> again"
            else:
                src = install_source.detect_install_source()
                fix = (f"reinstall with <code>"
                       f"{_esc(install_source.reinstall_hint(src.kind))}</code>")
            return ("⚠️ The daemon shut down while the update was installing; the "
                    "installer was stopped, so the install may be partial. If "
                    f"anything misbehaves, {fix}.")
        return "⚠️ The daemon shut down before the update finished."

    async def shutdown(self) -> None:
        """Daemon shutdown: never leave an installer orphaned, never start
        anything new, and never schedule a restart.

        Called before ``bot.stop()`` so the final status edit can still go
        out, and bounded as a whole by ``SHUTDOWN_DEADLINE_SECONDS``. From
        the first line on, :meth:`start` refuses and the spawn seam starts
        nothing. A job in a gate wait is cancelled (if B is already
        installed it leaves the normal A→B marker). A job whose installer
        is running gets ``SHUTDOWN_GRACE_SECONDS`` to finish; if it does,
        the job leaves the A→B marker and schedules no restart. Otherwise
        its process group is killed — with KillMode=process it would
        survive us and keep writing the venv while the next daemon imports
        it — the kill is logged, and an "interrupted" marker makes the next
        daemon tell the admin. A pending restart's marker is left alone:
        that restart IS this shutdown. Never raises.
        """
        self._shutting_down = True
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SHUTDOWN_DEADLINE_SECONDS
        mono_deadline = time.monotonic() + SHUTDOWN_DEADLINE_SECONDS

        def left(cap: float) -> float:
            return max(0.0, min(cap, deadline - loop.time()))

        job, task = self._job, self._job_task
        try:
            self_update.begin_shutdown(mono_deadline)
            if job is not None and task is not None and not task.done():
                if job.phase in _INSTALLING:
                    done, _ = await asyncio.wait(
                        {task}, timeout=left(SHUTDOWN_GRACE_SECONDS))
                    if not done and self_update.running_command_count():
                        phase = job.phase
                        job.interrupted = True
                        # Keep a slice of the budget for the job's last word
                        # and the cancel below.
                        kill = asyncio.ensure_future(asyncio.to_thread(
                            self_update.terminate_running_commands))
                        killed_done, _ = await asyncio.wait(
                            {kill}, timeout=left(SHUTDOWN_DEADLINE_SECONDS) * 0.75)
                        killed = kill.result() if killed_done and not kill.exception() \
                            else -1
                        log.warning("update.shutdown.interrupted job=%s phase=%s killed=%d "
                                    "— the install may be partial", job.id, phase, killed)
                        self._write_interrupted_marker(job, phase)
                        # The killed installer returns promptly: let the job
                        # post its own "interrupted" outcome.
                        await asyncio.wait({task}, timeout=left(SHUTDOWN_GRACE_SECONDS))
                if not task.done():
                    task.cancel()
                    await asyncio.wait({task}, timeout=left(SHUTDOWN_GRACE_SECONDS))
            # The watchdog and any status lookups (the job task is handled
            # above, with its own grace).
            others = [t for t in self._tasks if not t.done() and t is not task]
            for t in others:
                t.cancel()
            if others:
                await asyncio.wait(others, timeout=left(1.0))
        except Exception:
            log.warning("update shutdown failed", exc_info=True)
        if loop.time() > deadline:
            log.warning("update.shutdown.deadline_exceeded by=%.1fs",
                        loop.time() - deadline)

    def _write_interrupted_marker(self, job: _Job, phase: str) -> None:
        try:
            self_update.write_marker({
                "interrupted": phase, "kind": job.kind, "chat_id": job.chat_id,
                "user_id": job.user_id, "job_id": job.id,
                "from": self_update.running_version(),
                "source_kind": install_source.detect_install_source().kind,
                "scheduled_at": time.time(),
            })
        except Exception:
            log.warning("update.shutdown.marker_failed job=%s", job.id, exc_info=True)

    @staticmethod
    def _gate_live(job: _Job) -> str:
        if job.phase == "gate_timeout":
            mins = max(1, int(self_update.GATE_MAX_WAIT_SECONDS // 60))
            warn = "Restart now interrupts everything listed below"
            if any("held answer" in b for b in job.blockers):
                warn += ", including the undelivered held answers"
            return (f"⏰ Sessions are still busy after {mins} min. Wait {mins} more "
                    f"min, restart now, or cancel? {warn}.")
        return ("⏳ <b>aipager</b>: waiting for every session to go idle before "
                "the restart.")

    async def _restart_watchdog(self, job: _Job) -> None:
        await asyncio.sleep(self_update.RESTART_DELAY_SECONDS + RESTART_WATCHDOG_SECONDS)
        if (self._job is not job or job.phase != "restart_scheduled"
                or self._lock_owner != job.id):
            # Superseded: another job owns the lock (and maybe a marker).
            log.info("update.restart.watchdog_stale job=%s current=%s owner=%s", job.id,
                     self._job.id if self._job else None, self._lock_owner)
            return
        # Still alive: the scheduled restart never happened.
        log.warning("update.restart.failed job=%s reason=still-running-after-schedule",
                    job.id)
        # Stop the timer first, so it cannot fire in the middle of a later
        # job once the lock is free.
        try:
            await asyncio.to_thread(self_update.cancel_scheduled_restart, job.restart_unit)
        except Exception:
            log.warning("update.restart.timer_stop_failed job=%s", job.id, exc_info=True)
        self_update.clear_marker()
        self._release_lock(job)
        job.sections.append("⚠️ The scheduled restart did not happen — the new version "
                            "is installed but the old one is still running. Restart it "
                            f"yourself: <code>systemctl --user restart "
                            f"{self_update.UNIT_NAME}</code>")
        job.summary = _plain(job.sections[-1])
        job.phase = "failed"
        await self._report(job)


def _origin_explanation(source: install_source.InstallSource, chat_id) -> str:
    show = isinstance(chat_id, int) and chat_id > 0
    if source.origin == "local":
        where = f" {source.origin_detail}" if show and source.origin_detail else ""
        return (f" — this {source.kind} install upgrades from the local path{_esc(where)}, "
                "which still has the same version. Update that checkout first.")
    if source.origin == "vcs":
        return f" — this {source.kind} install upgrades from its git source, which has nothing newer."
    if source.origin == "index":
        return " — the package index has nothing newer."
    return "."


# ---------------------------------------------------------------------------
# Telegram entry points
# ---------------------------------------------------------------------------

def _busy_text(mgr: "UpdateManager") -> str:
    if mgr.restart_pending:
        return ("⏳ aipager is about to restart for an update. Try /update again "
                "once it is back.")
    snap = mgr.snapshot() or {}
    return (f"⏳ An update is already running ({_esc(snap.get('phase', '…'))}). "
            "Its status message shows the progress.")


async def _show_check(bot: "TelegramBot", message, chat_id) -> None:
    """Run the check and edit the tapped message into its result."""
    try:
        status = await bot.updates.check()
        text, kb = render_check(status, show_paths=_show_detail(chat_id))
    except Exception:
        log.warning("update check failed", exc_info=True)
        text = "⚠️ Couldn't check for updates right now. Try again."
        kb = check_prompt_markup()
    try:
        await edit_message(message, text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        log.debug("update check edit failed", exc_info=True)


async def handle_update_cmd(bot: "TelegramBot", update, ctx) -> None:
    """``/update`` — admin only (and, in personal mode, the operator only)."""
    if not await bot._authorize(update):
        return
    tg_user = update.effective_user
    user_id = tg_user.id if tg_user is not None else None
    chat_id = calling_chat_id(update)
    if not bot._is_update_admin(user_id, chat_id):
        log.info("update.denied user=%s chat=%s", user_id, chat_id)
        try:
            await reply_text(update.message, DENIED_TEXT)
        except Exception:
            log.debug("update denial reply failed", exc_info=True)
        return
    mgr = bot.updates
    if mgr.busy:
        try:
            await reply_text(update.message, _busy_text(mgr), parse_mode="HTML")
        except Exception:
            log.debug("update busy reply failed", exc_info=True)
        return
    # No lookup yet: the versions are checked only when the button is tapped.
    try:
        await reply_text(update.message, CHECK_PROMPT_TEXT, parse_mode="HTML",
                         reply_markup=check_prompt_markup())
    except Exception:
        log.debug("update reply failed", exc_info=True)


async def handle_callback(bot: "TelegramBot", update, query, session_name: str,
                          action: str) -> bool:
    """True iff this is an ``_:up:`` callback (handled here)."""
    if session_name != "_" or not action.startswith("up:"):
        return False
    tg_user = getattr(query, "from_user", None)
    user_id = tg_user.id if tg_user is not None else None
    message = getattr(query, "message", None)
    chat_id = _message_chat_id(message)
    if chat_id is None:
        chat_id = calling_chat_id(update)
    if not bot._is_update_admin(user_id, chat_id):
        log.info("update.denied user=%s chat=%s action=%s", user_id, chat_id, action)
        await bot._safe_answer(query, DENIED_TEXT, show_alert=True)
        return True

    parts = action.split(":")[1:]
    verb = parts[0] if parts else ""
    mgr = bot.updates
    if verb == "x" and len(parts) == 1:
        try:
            await edit_text(query, "↩️ Update cancelled — nothing changed.")
        except Exception:
            log.debug("update cancel edit failed", exc_info=True)
        return True
    if verb in ("cc", "ap", "both") and len(parts) == 1:
        # A pre-8.43 per-product button: never start from it.
        log.info("update.stale_menu user=%s chat=%s action=%s", user_id, chat_id, action)
        await bot._safe_answer(query, MENU_OUT_OF_DATE_TEXT, show_alert=True)
        return True
    if verb == "chk" and len(parts) == 1:
        if mgr.busy or mgr.shutting_down:
            text = ("⏳ aipager is shutting down. Try again once it is back."
                    if mgr.shutting_down and not mgr.busy else _busy_text(mgr))
            try:
                await edit_text(query, text, parse_mode="HTML")
            except Exception:
                log.debug("update check refusal edit failed", exc_info=True)
            return True
        await bot._safe_answer(query, CHECKING_TEXT)
        try:
            await edit_text(query, f"🔎 {CHECKING_TEXT}")
        except Exception:
            log.debug("update checking edit failed", exc_info=True)
        # PTB handles updates one at a time: never block it on the lookups.
        mgr._spawn(_show_check(bot, message, chat_id))
        return True
    if verb == "go" and len(parts) == 2 and parts[1] in _GO_CODES:
        kind = _GO_CODES[parts[1]]
        res = await mgr.start_offer(kind, chat_id=chat_id, user_id=user_id,
                                    origin="chat", status_message=message)
        if not res.ok:
            markup = None
            if res.error in ("check_expired", "offer_changed"):
                text, markup = OFFER_STALE_TEXT, check_prompt_markup()
            elif res.error == "not_upgradable":
                src = install_source.detect_install_source()
                text = (f"🚫 aipager can't be updated from here: "
                        f"{_esc(src.reason or 'unknown install')}")
            elif res.error == "shutting_down":
                text = "⏳ aipager is shutting down. Try again once it is back."
            else:
                phase = (res.job or {}).get("phase", "…")
                text = f"⏳ An update is already running ({_esc(phase)})."
            try:
                await edit_text(query, text, parse_mode="HTML", reply_markup=markup)
            except Exception:
                log.debug("update refusal edit failed", exc_info=True)
        return True
    if verb in ("now", "wait", "stop") and len(parts) == 2:
        try:
            job_id = int(parts[1])
        except ValueError:
            await bot._safe_answer(query, "Invalid callback")
            return True
        control = {"now": "restart-now", "wait": "wait-more", "stop": "cancel"}[verb]
        res = mgr.control(control, job_id)
        if not res.ok:
            log.info("update.control_refused user=%s action=%s job=%s", user_id, control,
                     job_id)
            await bot._safe_answer(query, STALE_TEXT)
        return True
    await bot._safe_answer(query, "Invalid callback")
    return True


# ---------------------------------------------------------------------------
# After the restart: the NEW daemon announces it
# ---------------------------------------------------------------------------

async def deliver_update_marker(bot: "TelegramBot", registry) -> None:
    """Announce "aipager updated A → B, N sessions re-adopted" once, by
    editing the old daemon's "installed, restarting…" message when the
    marker names it, else (or when that edit fails) by a new message.
    Never raises."""
    from aipager.state import Status

    try:
        await asyncio.sleep(self_update.MARKER_SETTLE_SECONDS)
        marker = self_update.read_and_clear_marker()
        if not marker:
            return
        age = time.time() - float(marker.get("scheduled_at") or 0)
        if age > self_update.MARKER_MAX_AGE_SECONDS or age < -300:
            log.info("update.marker.stale age=%.0fs", age)
            return
        try:
            chat_id = int(marker["chat_id"])
        except (KeyError, TypeError, ValueError):
            log.info("update.marker.stale reason=no-chat")
            return
        frm, to = str(marker.get("from", "?")), str(marker.get("to", "?"))
        running = self_update.running_version()
        if marker.get("interrupted"):
            if marker.get("interrupted") == "claude_updating":
                fix = "run `claude update` again"
            else:
                fix = ("reinstall with `" + install_source.reinstall_hint(
                    str(marker.get("source_kind") or "")) + "`")
            text = (f"⚠️ An update was interrupted when aipager shut down; the "
                    f"install may be partial. Now running {running}. If anything "
                    f"misbehaves, {fix}.")
        elif running != to:
            text = f"⚠️ aipager restarted but is running {running}, not {to}."
        else:
            back, missing = 0, []
            for entry in marker.get("sessions") or []:
                if not isinstance(entry, dict):
                    continue
                sess = registry.get(entry.get("name") or "")
                if sess is not None and sess.status not in (Status.GONE, Status.UNKNOWN):
                    back += 1
                else:
                    missing.append(str(entry.get("label") or entry.get("name") or "?"))
            text = (f"✅ aipager updated {frm} → {to}, {back} "
                    f"session{'' if back == 1 else 's'} re-adopted")
            if missing:
                text += "\n⚠️ Not back: " + ", ".join(missing)
            mgr = getattr(bot, "updates", None)
            if mgr is not None:
                mgr.last_restart = {"from": frm, "to": to, "readopted": back,
                                    "missing": missing}
        app = getattr(bot, "_app", None)
        if app is None:
            return
        ref = _marker_message_ref(marker, chat_id)
        if ref is not None:
            # Edit the old daemon's "installed, restarting…" message into
            # the outcome (roadmap 8.45). ESSENTIAL, like the send below.
            message_id, prefix = ref
            body = _esc_text(text)
            if prefix:
                body = f"{prefix}\n\n{body}"
            try:
                result = await edit_text_at(app.bot, text=body, chat_id=chat_id,
                                            message_id=message_id, parse_mode="HTML")
            except BadRequest as e:
                if "not modified" in str(e).lower():
                    log.info("update.marker.delivered chat=%s from=%s to=%s via=edit "
                             "(unchanged)", chat_id, frm, to)
                    return
                # Deleted, or too old to edit: post it instead, as before.
                log.info("update.marker.edit_failed chat=%s detail=%s", chat_id, e)
            else:
                if result is MUTED:
                    log.info("update.marker.skipped_muted chat=%s", chat_id)
                    return
                # SKIPPED cannot happen for this ESSENTIAL (blocking) edit
                # today; if it ever does, nothing went out, so send.
                if result is not SKIPPED:
                    log.info("update.marker.delivered chat=%s from=%s to=%s via=edit",
                             chat_id, frm, to)
                    return
        await _send_with_retry(app.bot, chat_id=chat_id, text=text)
        log.info("update.marker.delivered chat=%s from=%s to=%s via=send", chat_id, frm, to)
    except FloodMuted as e:
        log.info("update.marker.skipped_muted retry_after=%ss", e.retry_after)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.warning("update marker delivery failed", exc_info=True)


def _marker_message_ref(marker: dict, chat_id: int) -> tuple[int, str] | None:
    """``(message_id, prefix_html)`` of the message the marker says to
    edit, or ``None``. Only a message in the marker's own chat (the one
    that asked for the update) is ever edited."""
    ref = marker.get("message")
    if not isinstance(ref, dict):
        return None
    message_id, ref_chat = ref.get("message_id"), ref.get("chat_id")
    if not all(isinstance(v, int) and not isinstance(v, bool)
               for v in (message_id, ref_chat)) or ref_chat != chat_id:
        return None
    prefix = ref.get("prefix")
    return message_id, prefix if isinstance(prefix, str) else ""


__all__ = [
    "ControlResult", "StartResult", "UpdateManager", "UpdateOffer", "check_payload",
    "deliver_update_marker", "handle_callback", "handle_update_cmd", "render_check",
    "update_offer",
]
