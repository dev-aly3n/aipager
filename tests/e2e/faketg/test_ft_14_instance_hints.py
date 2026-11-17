"""Scenario 14 (1.6.0 fix): an isolated instance (``AIPAGER_INSTANCE_DIR``)
never points at the main install's service.

Through the instance's own CLI, in its environment:

- ``aipager service status`` refuses to run (the service is the main
  install's);
- ``aipager miniapp disable`` with the instance's daemon running reminds
  to restart the instance's own ``aipager start``, naming neither
  ``aipager service`` nor ``systemctl``;
- (test_ft_15) ``aipager setup group`` with the instance's daemon stopped.

No Claude turn: runs the same with real Claude or the stand-in.
"""

from __future__ import annotations

import subprocess

from tests.e2e.fake_telegram import instance as fti

SG = fti.SUPERGROUP_ID
MAIN_SERVICE = ("aipager service", "systemctl")


def _cli(inst, *args: str) -> subprocess.CompletedProcess:
    # No user bus: even a broken instance check could not reach the
    # operator's service from here.
    env = {k: v for k, v in inst.env.items()
           if k not in ("DBUS_SESSION_BUS_ADDRESS", "PYTEST_CURRENT_TEST")}
    env.update({"COLUMNS": "200", "TERM": "dumb", "NO_COLOR": "1"})
    return subprocess.run([str(inst.bin_dir / "aipager"), *args], env=env,
                          cwd=str(inst.inst_dir), stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=120)


def test_service_refused_in_an_instance(fresh):
    p = _cli(fresh, "service", "status")
    out = p.stdout + p.stderr
    assert p.returncode == 1, out
    assert "manages the main install's background service" in out, out
    assert "aipager start" in out, out


def test_miniapp_restart_hint_names_the_instance_daemon(fresh):
    p = _cli(fresh, "miniapp", "disable")
    out = p.stdout + p.stderr
    assert p.returncode == 0, out
    assert "Restart the daemon to apply this change" in out, out
    assert "stop `aipager start` and run it again" in out, out
    named = [w for w in MAIN_SERVICE if w in out]
    assert named == [], f"the hint names the main install's service ({named}):\n{out}"
