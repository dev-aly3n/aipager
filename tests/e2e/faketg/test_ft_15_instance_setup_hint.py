"""Scenario 15 (1.6.0 fix): ``aipager setup group`` from an isolated
instance (``AIPAGER_INSTANCE_DIR``) whose daemon is stopped says how to
start it with the instance's own ``aipager start``, never the main
install's ``aipager service`` (a daemon of its own: the test stops it).

No Claude turn: runs the same with real Claude or the stand-in.
"""

from __future__ import annotations

import json
import os
import signal

from tests.e2e.fake_telegram import instance as fti
from tests.e2e.faketg.test_ft_14_instance_hints import MAIN_SERVICE, _cli

SG = fti.SUPERGROUP_ID


def test_setup_group_next_step_with_the_daemon_stopped(own_faketg):
    inst = own_faketg({})
    inst.fake.add_chat(SG, "supergroup", "second team")
    # Stop this instance's daemon (its own PID only).
    assert fti.pid_references(inst.proc.pid, inst.inst_dir)
    os.kill(inst.proc.pid, signal.SIGTERM)
    inst.proc.wait(30)
    p = _cli(inst, "setup", "group", "--chat-id", str(SG), "--label", "second team",
             "--member", f"{fti.ALICE}:alice:owner", "--json")
    assert p.stdout.strip(), p.stderr[-600:]
    doc = json.loads(p.stdout)
    assert doc["ok"] is True, doc
    assert doc["daemon"]["running"] is False, doc["daemon"]
    step = doc["next_step"] or ""
    assert "aipager start" in step, step
    named = [w for w in MAIN_SERVICE if w in step]
    assert named == [], f"the next step names the main install's service ({named}): {step}"
    inst.restart_daemon()
