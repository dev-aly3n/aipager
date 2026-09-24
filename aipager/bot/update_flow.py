"""Admin self-update from Telegram and the Mini App (roadmap 8.36).

``/update`` shows the running and latest versions of aipager and Claude
Code and offers Update Claude Code / Update aipager / Both / Cancel. The
Mini App's Updates block drives the SAME :class:`UpdateManager` methods, so
the two surfaces cannot drift. All real work (spawning, fetching, the lock,
the restart gate, the restart plan, the marker) lives in
:mod:`aipager.self_update`; this module is orchestration and text.

Flood discipline: one status message per job, edited in place. Outcome
edits are ESSENTIAL; heartbeat and blocker edits are skippable ORNAMENTs,
sent only when their content changed and at most once per
``PROGRESS_EDIT_MIN_SECONDS``. Everything goes through the transport
seams, so a flood-muted chat receives nothing.

Callback data (short-callback family ``_``): ``_:up:cc``, ``_:up:ap``,
``_:up:both``, ``_:up:x``, ``_:up:now:<job>``, ``_:up:wait:<job>``,
``_:up:stop:<job>``.
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
# A bound on each status lookup, beyond the per-request HTTP timeout.
STATUS_STEP_TIMEOUT_SECONDS = 20.0
# If we are still alive this long after scheduling a restart, it did not
# happen: release the lock and say so.
RESTART_WATCHDOG_SECONDS = 120.0

DENIED_TEXT = "🚫 Only the admin can update aipager."
STALE_TEXT = "That update already finished"


def _esc(value) -> str:
    return html_mod.escape(str(value))


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
    event: asyncio.Event | None = None
    reporter: "_Reporter | None" = None

    @property
    def terminal(self) -> bool:
        return self.phase in TERMINAL_PHASES

    def snapshot(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "phase": self.phase,
            "blockers": list(self.blockers), "summary": self.summary,
            "started_at": self.started_at,
        }

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


def render_status(status: dict, *, show_paths: bool) -> tuple[str, InlineKeyboardMarkup | None]:
    ap = status["aipager"]
    cl = status["claude"]
    rs = status["restart"]
    src = ap["source"]

    lines = ["⬆️ <b>Updates</b>", ""]
    latest = ap.get("latest")
    tag = ""
    if latest and ap.get("update_available"):
        tag = " — update available"
    elif latest:
        tag = " — up to date"
    lines.append(f"<b>aipager</b> {_esc(ap['running'])} · latest "
                 f"{_esc(latest) if latest else 'unknown'}{tag}")
    lines.append(f"<i>{_esc(src['describe'] if show_paths else src['describe_short'])}</i>")
    installed = ap.get("installed")
    if installed and installed != ap["running"]:
        lines.append(f"installed on disk {_esc(installed)} (not running yet)")
    if not src["upgradable"]:
        lines.append(f"can't update from here: {_esc(src['reason'] or 'unknown install')}")
    lines.append("")

    cur = cl.get("current")
    cl_latest = cl.get("latest")
    tag = ""
    if cur and cl_latest and cl.get("update_available"):
        tag = " — update available"
    elif cur and cl_latest:
        tag = " — up to date"
    lines.append(f"<b>Claude Code</b> {_esc(cur) if cur else 'unknown'} · latest "
                 f"{_esc(cl_latest) if cl_latest else 'unknown'}{tag}")
    lines.append(f"<i>{_esc(cl['method'])} install, channel {_esc(cl['channel'])}</i>")
    lines.append("")
    if rs["automatic"]:
        lines.append("<b>Restart:</b> automatic (systemd), once no turn is running")
    else:
        lines.append(f"<b>Restart:</b> manual — {_esc(rs.get('reason') or 'unknown')}")

    can_ap = bool(src["upgradable"])
    can_cc = bool(cur)
    row1 = []
    if can_cc:
        row1.append(InlineKeyboardButton("⬆️ Update Claude Code", callback_data="_:up:cc"))
    if can_ap:
        row1.append(InlineKeyboardButton("⬆️ Update aipager", callback_data="_:up:ap"))
    row2 = []
    if can_cc and can_ap:
        row2.append(InlineKeyboardButton("Both", callback_data="_:up:both"))
    row2.append(InlineKeyboardButton("Cancel", callback_data="_:up:x"))
    rows = [r for r in (row1, row2) if r]
    return "\n".join(lines), InlineKeyboardMarkup(rows)


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
        self._tasks: set[asyncio.Task] = set()
        self._job_task: asyncio.Task | None = None
        self._last_job_id = 0

    # ---- read side -------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._job is not None and not self._job.terminal

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

    async def start(self, kind: str, *, chat_id: int, user_id: int | None,
                    origin: str, status_message=None) -> StartResult:
        if kind not in KINDS:
            raise ValueError(f"unknown update kind {kind!r}")
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
            log.info("update.lock_busy user=%s chat=%s (held by another process)",
                     user_id, chat_id)
            return StartResult(False, "update_in_progress", self.snapshot())
        job_id = max(int(time.time()), self._last_job_id + 1)
        self._last_job_id = job_id
        job = _Job(id=job_id, kind=kind, chat_id=chat_id, user_id=user_id,
                   origin=origin, started_at=time.time(), event=asyncio.Event())
        job.reporter = _Reporter(self.bot, chat_id, status_message)
        self._job = job
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

    def _finish(self, job: _Job, phase: str, section: str | None) -> None:
        if section:
            job.sections.append(section)
            job.summary = _plain(section)
        job.live = None
        job.phase = phase

    async def _run_job(self, job: _Job) -> None:
        restart_scheduled = False
        try:
            await self._report(job)
            if job.kind in ("claude", "both"):
                await self._claude_step(job)
            if job.kind in ("aipager", "both") and not job.terminal:
                restart_scheduled = await self._aipager_step(job)
            if not job.terminal:
                job.phase = "done"
                job.live = None
        except asyncio.CancelledError:
            self._finish(job, "cancelled", None)
            raise
        except Exception:
            log.exception("update.failed job=%s", job.id)
            self._finish(job, "failed", "❌ The update stopped on an unexpected error "
                                        "(see the daemon log).")
        finally:
            if not restart_scheduled:
                self._lock.release()
            self._status_cache = None
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
        if res.error and res.before is None:
            section = f"❌ <b>Claude Code</b>: {_esc(res.error)}"
        else:
            if res.timed_out:
                head = (f"⏱ <b>Claude Code</b> update timed out after "
                        f"{self_update.CLAUDE_UPDATE_TIMEOUT_SECONDS}s")
            elif res.returncode != 0 or res.error:
                head = (f"❌ <b>Claude Code</b> update failed "
                        f"(exit {res.returncode if res.returncode is not None else '?'})")
            elif res.after and res.before and res.after != res.before:
                head = f"✅ <b>Claude Code</b> {_esc(res.before)} → {_esc(res.after)}"
            else:
                head = (f"✅ <b>Claude Code</b> is already up to date "
                        f"({_esc(res.after or res.before)})")
            if not res.ok and res.output_tail:
                head += f"\n<pre>{_esc(res.output_tail)}</pre>"
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
        if res.timed_out:
            log.info("update.aipager.timeout job=%s", job.id)
            tail = f"\n<pre>{_esc(res.output_tail)}</pre>" if res.output_tail else ""
            self._finish(job, "failed",
                         f"⏱ <b>aipager</b> upgrade timed out after "
                         f"{self_update.UPGRADE_TIMEOUT_SECONDS}s — nothing restarted, "
                         f"still running {_esc(running)}.{tail}")
            return False
        if res.returncode != 0 or res.error:
            log.info("update.aipager.failed job=%s rc=%s", job.id, res.returncode)
            detail = res.output_tail or res.error or ""
            tail = f"\n<pre>{_esc(detail)}</pre>" if detail else ""
            self._finish(job, "failed",
                         f"❌ <b>aipager</b> upgrade failed (exit "
                         f"{res.returncode if res.returncode is not None else '?'}) — "
                         f"nothing restarted.{tail}")
            return False

        new, importable, err = await asyncio.to_thread(
            self_update.probe_installed_version, source.python)
        if not importable:
            log.info("update.aipager.smoke_failed job=%s error=%s", job.id, err)
            hint = install_source.reinstall_hint(source.kind)
            self._finish(job, "failed",
                         f"❌ The new version failed to import — still running "
                         f"{_esc(running)}, nothing restarted. Reinstall with "
                         f"<code>{_esc(hint)}</code>"
                         + (f"\n<pre>{_esc(err)}</pre>" if err else ""))
            return False
        if new == running:
            log.info("update.aipager.unchanged job=%s version=%s origin=%s",
                     job.id, running, source.origin)
            self._finish(job, "done", f"✅ <b>aipager</b> is already at {_esc(running)}"
                                      f"{_origin_explanation(source, job.chat_id)}")
            return False
        log.info("update.aipager.done job=%s from=%s to=%s", job.id, running, new)

        if not plan.automatic:
            log.info("update.restart.refused job=%s mode=%s", job.id, plan.mode)
            self._finish(job, "done",
                         f"✅ <b>aipager</b> {_esc(running)} → {_esc(new)} installed.\n"
                         + self._manual_restart_text(plan))
            return False

        if not job.restart_now:
            job.sections.append(f"✅ <b>aipager</b> {_esc(running)} → {_esc(new)} "
                                "installed; restarting once no turn is running.")
            if await self._wait_gate(job) == "cancel":
                job.sections.pop()
                self._finish(job, "cancelled",
                             f"✅ <b>aipager</b> {_esc(running)} → {_esc(new)} installed, "
                             "but the restart was cancelled. Restart it yourself: "
                             f"<code>{_esc(plan.manual_command)}</code>")
                return False
            job.sections.pop()

        plan = await asyncio.to_thread(self_update.restart_plan, registry)
        if not plan.automatic:
            log.info("update.restart.refused job=%s mode=%s (re-read)", job.id, plan.mode)
            self._finish(job, "done",
                         f"✅ <b>aipager</b> {_esc(running)} → {_esc(new)} installed.\n"
                         + self._manual_restart_text(plan))
            return False

        marker = {
            "from": running, "to": new, "chat_id": job.chat_id,
            "user_id": job.user_id, "job_id": job.id,
            "sessions": [
                {"name": s.name, "label": s.label}
                for s in registry.all_sessions().values()
                if s.status.name != "GONE"
            ],
            "scheduled_at": time.time(),
        }
        try:
            self_update.write_marker(marker)
        except OSError as e:
            log.info("update.restart.failed job=%s marker: %s", job.id, e)
            self._finish(job, "failed",
                         f"✅ <b>aipager</b> {_esc(running)} → {_esc(new)} installed, but "
                         f"the restart could not be prepared ({_esc(e)}). Restart it "
                         f"yourself: <code>{_esc(plan.manual_command)}</code>")
            return False
        ok, detail = await asyncio.to_thread(self_update.schedule_restart, plan)
        if not ok:
            self_update.clear_marker()
            self._finish(job, "failed",
                         f"✅ <b>aipager</b> {_esc(running)} → {_esc(new)} installed, but "
                         f"scheduling the restart failed: {_esc(detail)}. Restart it "
                         f"yourself: <code>{_esc(plan.manual_command)}</code>")
            return False
        self._finish(job, "restart_scheduled",
                     f"✅ <b>aipager</b> {_esc(running)} → {_esc(new)} installed. "
                     f"Restarting in {self_update.RESTART_DELAY_SECONDS} s…")
        return True

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
        # Still alive: the scheduled restart never happened.
        log.warning("update.restart.failed job=%s reason=still-running-after-schedule",
                    job.id)
        self_update.clear_marker()
        self._lock.release()
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

async def _show_status(bot: "TelegramBot", message, chat_id) -> None:
    try:
        status = await bot.updates.status(force=True)
        text, kb = render_status(status, show_paths=isinstance(chat_id, int) and chat_id > 0)
    except Exception:
        log.warning("update status failed", exc_info=True)
        text, kb = "⚠️ Couldn't read the versions right now — try /update again.", None
    try:
        await edit_message(message, text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        log.debug("update status edit failed", exc_info=True)


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
        snap = mgr.snapshot() or {}
        try:
            await reply_text(update.message,
                             f"⏳ An update is already running ({snap.get('phase', '…')}). "
                             "Its status message shows the progress.")
        except Exception:
            log.debug("update busy reply failed", exc_info=True)
        return
    try:
        msg = await reply_text(update.message, "🔎 Checking versions…")
    except Exception:
        log.debug("update reply failed", exc_info=True)
        return
    if msg is MUTED or msg is SKIPPED or msg is None:
        return
    # PTB handles updates one at a time: never block it on the lookups.
    mgr._spawn(_show_status(bot, msg, chat_id))


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
        kind = {"cc": "claude", "ap": "aipager", "both": "both"}[verb]
        res = await mgr.start(kind, chat_id=chat_id, user_id=user_id, origin="chat",
                              status_message=message)
        if not res.ok:
            if res.error == "not_upgradable":
                src = install_source.detect_install_source()
                text = (f"🚫 aipager can't be updated from here: "
                        f"{_esc(src.reason or 'unknown install')}")
            else:
                phase = (res.job or {}).get("phase", "…")
                text = f"⏳ An update is already running ({_esc(phase)})."
            try:
                await edit_text(query, text, parse_mode="HTML")
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
    """Post "aipager updated A → B, N sessions re-adopted" once. Never raises."""
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
        if running != to:
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
            text = f"✅ aipager updated {frm} → {to}, {back} sessions re-adopted"
            if missing:
                text += "\n⚠️ Not back: " + ", ".join(missing)
        app = getattr(bot, "_app", None)
        if app is None:
            return
        await _send_with_retry(app.bot, chat_id=chat_id, text=text)
        log.info("update.marker.delivered chat=%s from=%s to=%s", chat_id, frm, to)
    except FloodMuted as e:
        log.info("update.marker.skipped_muted retry_after=%ss", e.retry_after)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.warning("update marker delivery failed", exc_info=True)


__all__ = [
    "ControlResult", "StartResult", "UpdateManager", "deliver_update_marker",
    "handle_callback", "handle_update_cmd", "render_status",
]
