"""A resume is never swept out from under itself (roadmap 8.12 follow-up).

`expire_gone` drops a GONE session the instant it crosses
`GONE_SESSION_MAX_AGE_DAYS`, and `_do_resume_core` leaves the session
GONE — carrying its original `gone_at` — for the whole of
`launch_session` and the state restore that follows. Reproduced
2026-09-07: a sweep landing in that window removed the entry, the
later `transition()` re-created a blank one, and the resume's restored
cwd, chat scope, permission mode and message routing were lost on the
orphan while the operator was told "Resumed".

`resuming_until` / `is_resuming()` close it, armed before the launch and
released in a `finally` so a failed or crashed resume cannot pin a dead
entry in the registry — which is exactly what the ageing sweep exists to
prevent.

No real Telegram, dtach or claude.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import config
from aipager.dtach import inject
from aipager.state import (
    GONE_SESSION_MAX_AGE_DAYS,
    SessionRegistry,
    Status,
    TrackedSession,
)

DAY = 86400.0
NAME = "claude-proj__d123"
CHAT = 123
MSG = 555


def _aged_gone_session(registry: SessionRegistry, *, past_cutoff_by: float) -> TrackedSession:
    """A GONE session that crossed the age cutoff ``past_cutoff_by``
    seconds ago, with everything a resume has to restore."""
    sess = TrackedSession(name=NAME, label="proj", status=Status.GONE)
    sess.gone_at = time.time() - GONE_SESSION_MAX_AGE_DAYS * DAY - past_cutoff_by
    sess.claude_session_id = "uuid-1234"
    sess.cwd = "/home/aly/aipager"
    sess.scope_chat_id = CHAT
    sess.skip_perms = True
    registry._sessions[NAME] = sess
    registry.track_message(MSG, NAME, CHAT)
    return sess


def _bot(mk_bot, registry):
    bot = mk_bot(registry)
    bot._app = MagicMock()
    bot._app.bot = MagicMock()
    bot._app.bot.send_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()
    bot._session_system_prompt = lambda *a, **kw: ""
    return bot


# ===== the reproduction ==================================================

def test_a_sweep_during_the_launch_cannot_strand_the_resume(
    mk_bot, run_async, monkeypatch,
):
    """The 2026-09-07 reproduction: the sweep fires inside the launch.
    It must drop nothing, and the resumed session must still be the
    registry's own object with every field intact."""
    registry = SessionRegistry()
    sess = _aged_gone_session(registry, past_cutoff_by=1.0)
    bot = _bot(mk_bot, registry)
    swept: list[list[str]] = []

    async def _launch_while_the_sweep_runs(*a, **kw):
        swept.append(registry.expire_gone())
        return True, ""
    monkeypatch.setattr(inject, "launch_session", _launch_while_the_sweep_runs)

    outcome = run_async(bot._do_resume_core(sess))

    assert outcome.ok and outcome.reason == "resumed"
    assert swept == [[]], "the sweep took the session mid-resume"
    assert registry.get(NAME) is sess, "the registry holds a replacement, not the resumed session"
    assert sess.status == Status.IDLE
    assert sess.cwd == "/home/aly/aipager"
    assert sess.scope_chat_id == CHAT
    assert sess.skip_perms is True
    assert sess.gone_at is None
    # `claude_session_id` is deliberately left empty by a SUCCESSFUL resume
    # (cleared before the launch so a repeat failure cannot loop; the hook
    # repopulates it) — the corruption showed up in the fields above.
    assert sess.claude_session_id == ""
    assert registry.get_session_by_msg(MSG, CHAT) is sess


# ===== the guard in isolation ===========================================

def test_expire_gone_skips_a_session_that_is_resuming():
    registry = SessionRegistry()
    sess = _aged_gone_session(registry, past_cutoff_by=DAY)
    sess.resuming_until = time.monotonic() + 30

    assert registry.expire_gone() == []
    assert NAME in registry.all_sessions()


@pytest.mark.parametrize("until", [0.0, -1.0])
def test_expire_gone_drops_it_once_the_guard_has_lapsed(until):
    registry = SessionRegistry()
    sess = _aged_gone_session(registry, past_cutoff_by=DAY)
    sess.resuming_until = 0.0 if until == 0.0 else time.monotonic() - 1

    assert registry.expire_gone() == [NAME]


def test_is_resuming_reads_the_deadline():
    sess = TrackedSession(name=NAME, label="proj", status=Status.GONE)
    assert sess.is_resuming() is False
    sess.resuming_until = time.monotonic() + 5
    assert sess.is_resuming() is True
    sess.resuming_until = time.monotonic() - 5
    assert sess.is_resuming() is False


def test_guard_bound_has_a_default():
    assert config.RESUME_GUARD_SECONDS == 60


# ===== release on every path ============================================

def test_the_guard_is_released_after_a_successful_resume(mk_bot, run_async, monkeypatch):
    registry = SessionRegistry()
    sess = _aged_gone_session(registry, past_cutoff_by=1.0)
    bot = _bot(mk_bot, registry)
    monkeypatch.setattr(inject, "launch_session", AsyncMock(return_value=(True, "")))

    run_async(bot._do_resume_core(sess))

    assert sess.is_resuming() is False
    assert sess.resuming_until == 0.0


def test_a_failed_launch_releases_the_guard_and_the_session_ages_out_normally(
    mk_bot, run_async, monkeypatch,
):
    """A resume that could not start must not pin a dead entry in the
    registry — that is the very thing the ageing sweep exists to stop."""
    registry = SessionRegistry()
    sess = _aged_gone_session(registry, past_cutoff_by=DAY)
    bot = _bot(mk_bot, registry)
    monkeypatch.setattr(inject, "launch_session",
                        AsyncMock(return_value=(False, "dtach failed: no pty")))

    outcome = run_async(bot._do_resume_core(sess))

    assert outcome.ok is False and outcome.reason == "launch_failed"
    assert sess.is_resuming() is False
    assert sess.status == Status.GONE
    assert registry.expire_gone() == [NAME]


def test_a_launch_that_raises_still_releases_the_guard(mk_bot, run_async, monkeypatch):
    registry = SessionRegistry()
    sess = _aged_gone_session(registry, past_cutoff_by=DAY)
    bot = _bot(mk_bot, registry)

    async def _boom(*a, **kw):
        raise RuntimeError("launch exploded")
    monkeypatch.setattr(inject, "launch_session", _boom)

    with pytest.raises(RuntimeError):
        run_async(bot._do_resume_core(sess))

    assert sess.is_resuming() is False


# ===== transient =========================================================

def test_resuming_until_is_not_persisted(tmp_state_file):
    registry = SessionRegistry()
    # Recently gone: an entry past the cutoff would (correctly) be swept by
    # load()'s own expire_gone, and this test is about the field, not ageing.
    sess = _aged_gone_session(registry, past_cutoff_by=-2 * DAY)
    sess.resuming_until = time.monotonic() + 30
    registry.save()

    loaded = SessionRegistry()
    loaded.load()

    back = loaded.get(NAME)
    assert back is not None, "the guard must not have been persisted into a swept entry"
    assert back.resuming_until == 0.0
    assert back.is_resuming() is False
