"""Iteration 2 contracts, black-box: shutdown during an install, the
timed-out upgrade warning, and output tails redacted and capped when the
message is built.

SC-8 (a failed/timed-out upgrade schedules no restart and writes no
*update* marker; the install may be partial and the admin gets the
reinstall hint), SC-5 (the next daemon reports what happened), and the
spec's safety rules ("never leave an installer orphaned", secrets never
printed, flood discipline).
Sources: design.md Success criteria; entrypoints.md (`UpdateManager`,
`deliver_update_marker`, `reinstall_hint`, `run_command` tail contract,
`cmd_update`); review-1.md rev-iter1-003 / -005 and tester-iter1-001 as
restated by the orchestrator. `UpdateManager.shutdown()`,
`SHUTDOWN_GRACE_SECONDS` and `self_update.terminate_running_commands` are
public names read from the entry-point modules' signatures only.
"""

from __future__ import annotations

import re
import threading

import pytest

from aipager import install_source, self_update, updater
from aipager.bot import update_flow
from aipager.bot.flood import MUTE
from aipager.state import SessionRegistry, Status

BOT_TOKEN_IN_OUTPUT = "987654321:AAHfakeFAKEfakeFAKEfakeFAKEfake1234"
ANTHROPIC_IN_OUTPUT = "sk-ant-api03-FAKEFAKEFAKEFAKEFAKE"


def _marker():
    return self_update.UPDATE_MARKER_PATH


# ============================================================================
# Shutdown during an install
# ============================================================================

class _Installer:
    """A fake installer that blocks until "killed" (or, for the boundary,
    finishes by itself after ``finish_after`` seconds)."""

    def __init__(self, world, monkeypatch, *, target="aipager", finish_after=None):
        self.world = world
        self.target = target
        self.finish_after = finish_after
        self.started = threading.Event()
        self.release = threading.Event()
        self.killed = False
        self.done = False
        self.kill_calls = 0
        self._real = world._run_command
        monkeypatch.setattr(self_update, "_run_command", self._run)
        monkeypatch.setattr(self_update, "terminate_running_commands", self._terminate)
        # The real seam registers each child it waits on; running_command_count
        # is the public view of that registry, so the fake child shows up in it
        # while it "runs".
        monkeypatch.setattr(self_update, "running_command_count", self._live)
        monkeypatch.setattr(update_flow, "SHUTDOWN_GRACE_SECONDS", 0.2)

    def _is_target(self, argv):
        if self.target == "aipager":
            return self.world.is_upgrade([str(a) for a in argv])
        return [str(a) for a in argv][1:] == ["update"]

    def _run(self, argv, *, timeout, env=None, capture=True):
        if not self._is_target(argv):
            return self._real(argv, timeout=timeout, env=env, capture=capture)
        self.world.calls.append({"argv": [str(a) for a in argv], "timeout": timeout, "env": env})
        self.started.set()
        wait = self.finish_after if self.finish_after is not None else 5.0
        self.release.wait(wait)
        self.done = True
        if self.killed:
            return self_update.CommandResult(returncode=-9, output_tail="Killed",
                                             timed_out=False, error=None)
        if self.target == "claude":
            self.world.claude_version = self.world.claude_after
            return self_update.CommandResult(returncode=0, output_tail="ok",
                                             timed_out=False, error=None)
        return self_update.CommandResult(returncode=0, output_tail="upgraded",
                                         timed_out=False, error=None)

    def _live(self):
        return int(self.started.is_set() and not self.release.is_set() and not self.done)

    def _terminate(self):
        self.kill_calls += 1
        self.killed = True
        self.release.set()
        return 1

    def close(self):
        self.release.set()


@pytest.fixture
def installer(world, monkeypatch):
    inst = _Installer(world, monkeypatch)
    yield inst
    inst.close()


@pytest.fixture
def claude_installer(world, monkeypatch):
    inst = _Installer(world, monkeypatch, target="claude")
    yield inst
    inst.close()


async def _started(inst, h):
    await h.wait_for(inst.started.is_set, 5.0)
    assert inst.started.is_set()


async def _shutdown_mid_install(bot, h, inst, kind="aipager"):
    msg = h.status_message(h.DM)
    res = await bot.updates.start(kind, chat_id=h.DM, user_id=h.OPERATOR,
                                  origin="chat", status_message=msg)
    assert res.ok, res.error
    await _started(inst, h)
    await bot.updates.shutdown()
    await h.wait_for(lambda: False, 0.1)
    return msg


def test_shutdown_kills_a_running_installer(world, personal_bot, h, run_async, installer):
    run_async(_shutdown_mid_install(personal_bot, h, installer))
    assert installer.kill_calls >= 1


def test_shutdown_mid_install_schedules_no_restart(world, personal_bot, h, run_async, installer):
    run_async(_shutdown_mid_install(personal_bot, h, installer))
    assert world.schedule_calls() == []


def test_shutdown_mid_install_reports_possibly_partial_install(world, personal_bot, h, run_async, installer):
    async def go():
        msg = await _shutdown_mid_install(personal_bot, h, installer)
        return h.all_text(personal_bot, msg)
    assert "partial" in run_async(go()).lower()


def test_shutdown_mid_install_leaves_an_interrupted_marker(world, personal_bot, h, run_async, installer):
    run_async(_shutdown_mid_install(personal_bot, h, installer))
    assert _marker().exists()


def test_shutdown_mid_install_marker_is_owner_only(world, personal_bot, h, run_async, installer):
    import stat
    run_async(_shutdown_mid_install(personal_bot, h, installer))
    assert stat.S_IMODE(_marker().stat().st_mode) == 0o600


def test_shutdown_mid_install_ends_the_job(world, personal_bot, h, run_async, installer):
    async def go():
        await _shutdown_mid_install(personal_bot, h, installer)
        return (personal_bot.updates.snapshot() or {}).get("phase")
    assert run_async(go()) in {"failed", "cancelled", "done"}


def test_shutdown_mid_install_logs_the_kill(world, personal_bot, h, run_async, installer, caplog):
    import logging
    with caplog.at_level(logging.INFO):
        run_async(_shutdown_mid_install(personal_bot, h, installer))
    assert "interrupt" in caplog.text.lower()


def _new_daemon(mk_bot, h):
    from unittest.mock import AsyncMock
    reg = SessionRegistry()
    h.add_session(reg, "alpha", status=Status.IDLE)
    bot = mk_bot(reg)
    bot._app.bot.send_message = AsyncMock(
        side_effect=lambda *a, **kw: h.status_message(kw.get("chat_id", h.DM), 805))
    return bot, reg


def test_next_daemon_warns_about_the_interrupted_update(world, personal_bot, mk_bot, h, run_async, installer):
    run_async(_shutdown_mid_install(personal_bot, h, installer))
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert "interrupted" in "\n".join(h.texts_of(bot._app.bot)).lower()


def test_next_daemon_interrupted_warning_goes_to_the_requesting_chat(world, personal_bot, mk_bot, h, run_async, installer):
    run_async(_shutdown_mid_install(personal_bot, h, installer))
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    call = bot._app.bot.send_message.await_args
    assert call is not None and call.kwargs.get("chat_id", call.args[0] if call.args else None) == h.DM


def test_next_daemon_does_not_claim_success_after_interruption(world, personal_bot, mk_bot, h, run_async, installer):
    run_async(_shutdown_mid_install(personal_bot, h, installer))
    world.running = h.LATEST
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert "aipager updated" not in "\n".join(h.texts_of(bot._app.bot))


def test_next_daemon_interrupted_warning_gives_the_reinstall_hint(world, personal_bot, mk_bot, h, run_async, installer):
    run_async(_shutdown_mid_install(personal_bot, h, installer))
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert "pipx install" in "\n".join(h.texts_of(bot._app.bot))


def test_interrupted_marker_is_delivered_once(world, personal_bot, mk_bot, h, run_async, installer):
    run_async(_shutdown_mid_install(personal_bot, h, installer))
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert bot._app.bot.send_message.await_count == 1


def test_interrupted_marker_is_deleted_after_delivery(world, personal_bot, mk_bot, h, run_async, installer):
    run_async(_shutdown_mid_install(personal_bot, h, installer))
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert not _marker().exists()


def test_interrupted_marker_not_sent_into_a_muted_chat(world, personal_bot, mk_bot, h, run_async, installer):
    run_async(_shutdown_mid_install(personal_bot, h, installer))
    MUTE.mute(h.DM, 3600)
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert bot._app.bot.send_message.await_count == 0


def test_shutdown_kills_a_running_claude_update(world, personal_bot, h, run_async, claude_installer):
    run_async(_shutdown_mid_install(personal_bot, h, claude_installer, kind="claude"))
    assert claude_installer.kill_calls >= 1


def test_next_daemon_warns_about_an_interrupted_claude_update(world, personal_bot, mk_bot, h, run_async, claude_installer):
    run_async(_shutdown_mid_install(personal_bot, h, claude_installer, kind="claude"))
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert "interrupted" in "\n".join(h.texts_of(bot._app.bot)).lower()


def test_installer_finishing_within_the_grace_is_not_killed(world, personal_bot, h, run_async, monkeypatch):
    """Boundary, just inside: the installer ends before the bounded wait
    runs out, so its group is never killed."""
    inst = _Installer(world, monkeypatch, finish_after=0.05)
    monkeypatch.setattr(update_flow, "SHUTDOWN_GRACE_SECONDS", 2.0)
    try:
        run_async(_shutdown_mid_install(personal_bot, h, inst))
    finally:
        inst.close()
    assert inst.kill_calls == 0


def test_installer_finishing_within_the_grace_leaves_no_interrupted_warning(world, personal_bot, mk_bot, h, run_async, monkeypatch):
    inst = _Installer(world, monkeypatch, finish_after=0.05)
    monkeypatch.setattr(update_flow, "SHUTDOWN_GRACE_SECONDS", 2.0)
    try:
        run_async(_shutdown_mid_install(personal_bot, h, inst))
    finally:
        inst.close()
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert "interrupted" not in "\n".join(h.texts_of(bot._app.bot)).lower()


def test_shutdown_waits_a_bounded_time_not_forever(world, personal_bot, h, run_async, installer):
    """Error guess: an installer that never ends must not hold the
    shutdown past its grace (systemd would SIGKILL us at 15 s)."""
    import time as _time

    async def go():
        msg = h.status_message(h.DM)
        await personal_bot.updates.start("aipager", chat_id=h.DM, user_id=h.OPERATOR,
                                         origin="chat", status_message=msg)
        await _started(installer, h)
        t0 = _time.monotonic()
        await personal_bot.updates.shutdown()
        return _time.monotonic() - t0
    assert run_async(go()) < 3.0


def test_shutdown_during_gate_wait_runs_no_installer(world, personal_bot, h, run_async):
    s = h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        await personal_bot.updates.start("aipager", chat_id=h.DM, user_id=h.OPERATOR,
                                         origin="chat", status_message=h.status_message(h.DM))
        await h.wait_phase(personal_bot, "waiting_for_idle")
        await personal_bot.updates.shutdown()
        s.status = Status.IDLE                     # the turn ends after shutdown
        await h.wait_for(lambda: False, 0.3)
    run_async(go())
    assert world.upgrade_calls() == []


def test_shutdown_during_gate_wait_kills_nothing(world, personal_bot, h, run_async, monkeypatch):
    kills = []
    monkeypatch.setattr(self_update, "terminate_running_commands",
                        lambda: kills.append(1) or 0)
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        await personal_bot.updates.start("aipager", chat_id=h.DM, user_id=h.OPERATOR,
                                         origin="chat", status_message=h.status_message(h.DM))
        await h.wait_phase(personal_bot, "waiting_for_idle")
        await personal_bot.updates.shutdown()
    run_async(go())
    assert kills == []


def test_shutdown_during_gate_wait_leaves_no_marker(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        await personal_bot.updates.start("aipager", chat_id=h.DM, user_id=h.OPERATOR,
                                         origin="chat", status_message=h.status_message(h.DM))
        await h.wait_phase(personal_bot, "waiting_for_idle")
        await personal_bot.updates.shutdown()
    run_async(go())
    assert not _marker().exists()


def test_shutdown_with_pending_restart_keeps_the_update_marker(world, personal_bot, mk_bot, h, run_async):
    """That restart IS this shutdown: the next daemon must still announce
    the update, not an interruption."""
    async def go():
        _, _, phase = await h.run_job(personal_bot, "aipager")
        assert phase == "restart_scheduled"
        await personal_bot.updates.shutdown()
    run_async(go())
    world.running = h.LATEST
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert f"aipager updated {h.RUNNING} → {h.LATEST}" in "\n".join(h.texts_of(bot._app.bot))


def test_shutdown_with_no_job_does_not_raise(world, personal_bot, run_async):
    run_async(personal_bot.updates.shutdown())


def test_shutdown_twice_does_not_raise(world, personal_bot, h, run_async, installer):
    async def go():
        await _shutdown_mid_install(personal_bot, h, installer)
        await personal_bot.updates.shutdown()
    run_async(go())


def test_shutdown_when_kill_fails_still_does_not_raise(world, personal_bot, h, run_async, installer, monkeypatch):
    """Error guess: the group kill itself blows up."""
    def boom():
        installer.release.set()
        raise OSError("EPERM")
    monkeypatch.setattr(self_update, "terminate_running_commands", boom)
    run_async(_shutdown_mid_install(personal_bot, h, installer))


# ============================================================================
# A timed-out upgrade may be partial
# ============================================================================

def _ap(bot, h, run_async):
    return run_async(h.run_job(bot, "aipager"))


def test_timed_out_upgrade_warns_install_may_be_partial(world, personal_bot, h, run_async):
    world.upgrade_timed_out = True
    _, msg, _ = _ap(personal_bot, h, run_async)
    assert "partial" in h.all_text(personal_bot, msg).lower()


@pytest.mark.parametrize("kind,hint", [("pipx", "pipx install"),
                                       ("uv", "uv tool install"),
                                       ("pip", "pip install")])
def test_timed_out_upgrade_gives_the_reinstall_hint(world, personal_bot, h, run_async, kind, hint):
    world.source_kind = kind
    world.upgrade_timed_out = True
    _, msg, _ = _ap(personal_bot, h, run_async)
    assert hint in h.all_text(personal_bot, msg)


def test_timed_out_upgrade_hint_matches_reinstall_hint(world, personal_bot, h, run_async):
    world.upgrade_timed_out = True
    _, msg, _ = _ap(personal_bot, h, run_async)
    hint = install_source.reinstall_hint("pipx")
    text = h.all_text(personal_bot, msg).replace("&lt;", "<").replace("&gt;", ">")
    assert hint in text or hint.split("\n")[0] in text


def test_timed_out_upgrade_still_schedules_no_restart(world, personal_bot, h, run_async):
    world.upgrade_timed_out = True
    _ap(personal_bot, h, run_async)
    assert world.schedule_calls() == [] and not _marker().exists()


def test_successful_upgrade_does_not_warn_partial(world, personal_bot, h, run_async):
    """Equivalence partner: the warning is specific to the timeout class."""
    _, msg, _ = _ap(personal_bot, h, run_async)
    assert "partial" not in h.all_text(personal_bot, msg).lower()


def test_cli_timed_out_upgrade_warns_partial(world, capsys):
    world.upgrade_timed_out = True
    updater.cmd_update()
    out = capsys.readouterr()
    assert "partial" in (out.out + out.err).lower()


def test_cli_timed_out_upgrade_gives_the_reinstall_hint(world, capsys):
    world.upgrade_timed_out = True
    updater.cmd_update()
    out = capsys.readouterr()
    assert "pipx install" in out.out + out.err


# ============================================================================
# Output tails: redacted and capped at message build
# ============================================================================

def _longest_run(text, ch):
    runs = re.findall(re.escape(ch) + "+", text)
    return max((len(r) for r in runs), default=0)


def _claude_tail(world, monkeypatch, tail, rc=1):
    real = world._run_command

    def fake(argv, *, timeout, env=None, capture=True):
        if [str(a) for a in argv][1:] == ["update"]:
            world.calls.append({"argv": [str(a) for a in argv], "timeout": timeout, "env": env})
            return self_update.CommandResult(returncode=rc, output_tail=tail,
                                             timed_out=False, error=None)
        return real(argv, timeout=timeout, env=env, capture=capture)
    monkeypatch.setattr(self_update, "_run_command", fake)


def test_failed_upgrade_tail_redacts_bot_token(world, personal_bot, h, run_async):
    world.upgrade_rc = 1
    world.upgrade_output = f"error: auth {BOT_TOKEN_IN_OUTPUT} rejected"
    _, msg, _ = _ap(personal_bot, h, run_async)
    assert BOT_TOKEN_IN_OUTPUT not in h.all_text(personal_bot, msg)


def test_failed_upgrade_tail_redacts_anthropic_key(world, personal_bot, h, run_async):
    world.upgrade_rc = 1
    world.upgrade_output = f"env ANTHROPIC_API_KEY={ANTHROPIC_IN_OUTPUT}"
    _, msg, _ = _ap(personal_bot, h, run_async)
    assert ANTHROPIC_IN_OUTPUT not in h.all_text(personal_bot, msg)


def test_timed_out_upgrade_tail_redacts_bot_token(world, personal_bot, h, run_async, monkeypatch):
    world.upgrade_timed_out = True
    real = world._run_command

    def fake(argv, *, timeout, env=None, capture=True):
        res = real(argv, timeout=timeout, env=env, capture=capture)
        if world.is_upgrade([str(a) for a in argv]):
            return self_update.CommandResult(returncode=None, timed_out=True, error=None,
                                             output_tail=f"token {BOT_TOKEN_IN_OUTPUT}")
        return res
    monkeypatch.setattr(self_update, "_run_command", fake)
    _, msg, _ = _ap(personal_bot, h, run_async)
    assert BOT_TOKEN_IN_OUTPUT not in h.all_text(personal_bot, msg)


def test_failed_claude_update_tail_redacts_bot_token(world, personal_bot, h, run_async, monkeypatch):
    _claude_tail(world, monkeypatch, f"bad token {BOT_TOKEN_IN_OUTPUT}")
    _, msg, _ = run_async(h.run_job(personal_bot, "claude"))
    assert BOT_TOKEN_IN_OUTPUT not in h.all_text(personal_bot, msg)


def test_failed_claude_update_tail_redacts_anthropic_key(world, personal_bot, h, run_async, monkeypatch):
    _claude_tail(world, monkeypatch, f"key {ANTHROPIC_IN_OUTPUT}")
    _, msg, _ = run_async(h.run_job(personal_bot, "claude"))
    assert ANTHROPIC_IN_OUTPUT not in h.all_text(personal_bot, msg)


def test_failed_upgrade_oversize_tail_is_capped(world, personal_bot, h, run_async):
    world.upgrade_rc = 1
    world.upgrade_output = "Q" * 6000 + "END"
    _, msg, _ = _ap(personal_bot, h, run_async)
    assert _longest_run(h.all_text(personal_bot, msg), "Q") <= self_update.OUTPUT_TAIL_CHARS


def test_failed_claude_update_oversize_tail_is_capped(world, personal_bot, h, run_async, monkeypatch):
    _claude_tail(world, monkeypatch, "Z" * 6000 + "END")
    _, msg, _ = run_async(h.run_job(personal_bot, "claude"))
    assert _longest_run(h.all_text(personal_bot, msg), "Z") <= self_update.OUTPUT_TAIL_CHARS


def test_oversize_tail_keeps_its_end(world, personal_bot, h, run_async, monkeypatch):
    _claude_tail(world, monkeypatch, "Z" * 6000 + "LAST-LINE-MARK")
    _, msg, _ = run_async(h.run_job(personal_bot, "claude"))
    assert "LAST-LINE-MARK" in h.all_text(personal_bot, msg)


def test_failed_upgrade_message_fits_telegram_limit(world, personal_bot, h, run_async):
    world.upgrade_rc = 1
    world.upgrade_output = "Q" * 20000
    _, msg, _ = _ap(personal_bot, h, run_async)
    assert max(len(t) for t in h.texts_of(msg, personal_bot._app.bot)) <= 4096


def test_tail_just_inside_the_cap_is_shown_whole(world, personal_bot, h, run_async, monkeypatch):
    """Boundary: a tail of exactly OUTPUT_TAIL_CHARS is not trimmed."""
    n = self_update.OUTPUT_TAIL_CHARS
    body = "S" + "y" * (n - 2) + "E"
    assert len(body) == n
    _claude_tail(world, monkeypatch, body)
    _, msg, _ = run_async(h.run_job(personal_bot, "claude"))
    assert body in h.all_text(personal_bot, msg)


def test_token_split_by_the_cap_is_not_leaked(world, personal_bot, h, run_async, monkeypatch):
    """Error guess: redaction must happen before trimming can cut the
    token's digits off and leave a secret suffix the regex no longer
    recognises. The secret half of the token must never appear."""
    secret = BOT_TOKEN_IN_OUTPUT.split(":", 1)[1]
    n = self_update.OUTPUT_TAIL_CHARS
    tail = "p" * 6000 + BOT_TOKEN_IN_OUTPUT + "q" * (n - len(secret) - 2)
    _claude_tail(world, monkeypatch, tail)
    _, msg, _ = run_async(h.run_job(personal_bot, "claude"))
    assert secret not in h.all_text(personal_bot, msg)


def test_cli_failed_upgrade_output_redacts_bot_token(world, capsys):
    world.upgrade_rc = 1
    world.upgrade_output = f"error: auth {BOT_TOKEN_IN_OUTPUT} rejected"
    updater.cmd_update()
    out = capsys.readouterr()
    assert BOT_TOKEN_IN_OUTPUT not in out.out + out.err


def test_cli_failed_upgrade_output_is_capped(world, capsys):
    world.upgrade_rc = 1
    world.upgrade_output = "Q" * 6000
    updater.cmd_update()
    out = capsys.readouterr()
    assert _longest_run(out.out + out.err, "Q") <= self_update.OUTPUT_TAIL_CHARS
