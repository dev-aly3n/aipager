"""Exactly one daemon: the update path only ever restarts the unit (8.36).

The single-daemon guarantee itself is the existing fcntl lock
(tests/test_cli_daemon.py::test_acquire_daemon_lock_second_call_exits);
this pins that the update path adds no second starter.
"""

from __future__ import annotations


def test_scheduled_restart_never_starts_a_second_daemon(env, run):
    async def scenario():
        await env.start("both")
        await env.finish()
    run(scenario)
    scheduled = env.schedule_calls()
    assert len(scheduled) == 1
    argv = scheduled[0]
    assert argv[-4:] == ["/abs/bin/systemctl", "--user", "restart", "aipager.service"]
    for call in env.calls:
        assert "start" not in call, call
        assert not any(a.endswith("aipager") and "start" in call for a in call)
        assert call[:1] != ["sh"]
