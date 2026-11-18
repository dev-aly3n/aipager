"""Scenario 16 (1.6.0, ``aipager setup detect-group``): with the instance's
daemon running, someone sends ``/start`` in a group that is not set up
yet; the instance's own CLI finds that group through what the daemon
noted (``source: "daemon"``) and saves nothing. A group that is already
set up is never offered. Then ``aipager setup group`` with the id it found
adds the group live.

No Claude turn: runs the same with real Claude or the stand-in.
"""

from __future__ import annotations

import json
import subprocess

import yaml

from tests.e2e.fake_telegram import instance as fti

SG = fti.SUPERGROUP_ID
TITLE = "new team"


def _cli(inst, *args: str, timeout: float = 120) -> tuple[int, dict, str]:
    env = {k: v for k, v in inst.env.items()
           if k not in ("DBUS_SESSION_BUS_ADDRESS", "PYTEST_CURRENT_TEST")}
    p = subprocess.run([str(inst.bin_dir / "aipager"), "setup", *args, "--json"], env=env,
                       cwd=str(inst.inst_dir), stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=timeout)
    assert p.stdout.strip(), f"no JSON (rc={p.returncode}): {p.stderr[-600:]!r}"
    return p.returncode, json.loads(p.stdout), p.stderr


def test_detect_group_finds_the_new_group_through_the_daemon(fresh):
    inst, fake = fresh, fresh.fake
    before = inst.yaml_path.read_bytes()
    # A message in the configured group first: never a candidate.
    fake.inject_text(fti.GROUP_ID, fti.user(fti.ALICE), "/start")
    fake.add_chat(SG, "supergroup", TITLE)
    fake.inject_text(SG, fti.user(fti.ALICE), "/start")

    rc, doc, err = _cli(inst, "detect-group", "--timeout", "60")
    assert rc == 0, (doc, err[-600:])
    assert doc["status"] == "found", doc
    assert doc["source"] == "daemon", doc
    assert doc["candidate"]["id"] == SG, doc
    assert doc["candidate"]["title"] == TITLE, doc
    assert doc["candidate"]["type"] == "supergroup", doc
    assert inst.yaml_path.read_bytes() == before, "detect-group saved something"
    assert fti.FAKE_TOKEN not in json.dumps(doc) + err

    log_since = inst.log_mark()
    rc, doc, err = _cli(inst, "group", "--chat-id", str(doc["candidate"]["id"]),
                        "--label", TITLE, "--member", f"{fti.ALICE}:alice:owner")
    assert rc == 0 and doc["status"] == "created", (doc, err[-600:])
    assert doc["daemon"]["outcome"] == "reloaded_live", doc["daemon"]
    inst.wait_log("SIGUSR1 received", since=log_since, timeout=30)
    cfg = yaml.safe_load(inst.yaml_path.read_text())
    assert any(s["chat_id"] == SG for s in cfg["scopes"]), cfg["scopes"]
