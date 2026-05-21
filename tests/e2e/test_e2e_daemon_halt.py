"""E2E: a real dtach session is cleanly halted on a safety block.

Regression for the wedge: raw Escape without cancelling the spinner /
resetting state left the session "thinking" forever. ``_halt_for_safety``
must interrupt Claude, cancel the animation, and return to IDLE — verified
here against a REAL dtach + Claude session.

Requires the live daemon to be **stopped** (it would otherwise adopt the
throwaway session and message the operator). Skips if it's running.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aipager.dtach import inject
from aipager.state import Status, TrackedSession
from tests.e2e import harness


def test_halt_for_safety_interrupts_real_dtach(
    claude_available, project, mk_bot, run_async,
):
    if Path("/tmp/aipager.sock").exists():
        pytest.skip("live daemon running — stop it to run this isolated test")
    name = harness.new_session().replace("claude-", "e2ehalt-")

    ok, err = run_async(inject.launch_session(
        name, skip_perms=True, cwd=str(project)))
    if not ok:
        pytest.skip(f"could not launch dtach/claude session: {err}")
    try:
        # Give Claude a moment + a long task so it's genuinely busy.
        import time as _t
        _t.sleep(3)
        run_async(inject.send_text_and_enter(
            name, "Count slowly from 1 to 50, one number per line."))
        _t.sleep(2)
        assert run_async(inject.is_alive(name)) is True

        bot = mk_bot()
        sess = TrackedSession(name=name, label="halt", status=Status.BUSY)
        sess.scope_chat_id = 999
        sess.busy_msg_id = None
        anim = MagicMock()
        anim.done.return_value = False
        sess.animate_task = anim
        bot.registry._sessions[name] = sess

        run_async(bot._halt_for_safety(sess, "blocked by safety policy"))

        # Interrupted (not killed) + cleanly reset.
        assert run_async(inject.is_alive(name)) is True
        assert sess.status == Status.IDLE
        assert sess.animate_task is None       # spinner cancelled
        anim.cancel.assert_called_once()        # the wedge fix
    finally:
        run_async(inject.kill_session(name))
