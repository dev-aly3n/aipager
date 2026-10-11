"""Fix iteration 3 of the three-modes ship: no Shift+Tab ever reaches a
process that has bypassPermissions in its cycle.

- rev-iter2-001: a session launched in Full access and cycled to Manual or
  Auto in the terminal still runs with the bypass flag. `/mode ask` (or
  auto) is not answered "already": it restarts into the target, so the
  flag is dropped. A following `/mode auto` never presses into the old
  process.
- rev-iter2-002: a CLI session started with the bypass flag keeps
  perm_mode "ask"; once a main-agent hook reported bypassPermissions,
  every switch restarts it instead of pressing.

The launch argv is captured at the process boundary with the real
``launch_session`` in between (test_tm3_already_narrower.py's harness)."""

from __future__ import annotations

import pytest

from _tm3 import FLAGS, quiet_restart
from test_tm3_already_narrower import Argv, World


@pytest.fixture
def argv(monkeypatch, pty, tmp_path):
    quiet_restart(monkeypatch)
    cwd = tmp_path / "proj"
    cwd.mkdir()
    return Argv(monkeypatch, pty, cwd)


def _bypass_launches(argv) -> int:
    return sum(FLAGS["full"] in cmd for cmd in argv.cmds)


def test_full_launch_reported_manual_ask_then_auto_never_presses_the_bypass_process(
        mk_bot, run_async, pty, argv):
    w = World(mk_bot, run_async, argv, launched="full", via_new=False)
    argv.sock["up"] = True                 # the Full access process is live
    w.report("default")                    # cycled to Manual in the terminal
    w.say("/mode ask")
    # Restarted into Ask: not one Shift+Tab went to the bypass process.
    assert (pty.presses, argv.mode_flags()) == ([], [FLAGS["ask"]]), argv.cmds
    w.say("/mode auto")
    # Whatever /mode auto did, it did to the Ask process, and no launch
    # ever passed the bypass flag.
    assert _bypass_launches(argv) == 0, argv.cmds
    assert w.sess.perm_mode != "full"


def test_full_launch_reported_manual_mode_auto_restarts_into_auto(
        mk_bot, run_async, pty, argv):
    w = World(mk_bot, run_async, argv, launched="full", via_new=False)
    argv.sock["up"] = True
    w.report("default")
    w.say("/mode auto")
    assert (pty.presses, argv.mode_flags()) == ([], [FLAGS["auto"]]), argv.cmds


def test_cli_bypass_session_cycled_to_manual_restarts_for_auto(mk_bot, run_async, pty, argv):
    # Started in a terminal with the bypass flag: aipager knows it only as
    # "ask". Its hooks reported bypass, then Manual after Shift+Tab.
    w = World(mk_bot, run_async, argv, launched="ask", via_new=False)
    argv.sock["up"] = True
    w.report("bypassPermissions")
    w.report("default")
    w.say("/mode auto")
    assert pty.presses == [], pty.runs
    assert argv.mode_flags() == [FLAGS["auto"]], argv.cmds
    assert _bypass_launches(argv) == 0


def test_cli_session_that_never_reported_bypass_still_switches_in_place(
        mk_bot, run_async, pty, argv):
    # The guard's other side: without a bypass report, Ask -> Auto is the
    # in-place switch it always was (three presses from Manual).
    w = World(mk_bot, run_async, argv, launched="ask", via_new=False)
    argv.sock["up"] = True
    w.report("default")
    w.say("/mode auto")
    assert len(pty.presses) == 3
    assert argv.cmds == []
