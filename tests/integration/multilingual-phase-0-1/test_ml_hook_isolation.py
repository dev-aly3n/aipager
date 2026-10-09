"""The hook process never loads ``aipager.i18n`` (design.md success
criterion 3; entrypoints.md "Side effects": ``aipager-hook`` and
``aipager-statusline`` never load it).

Each run is a fresh interpreter with a throwaway HOME and instance dir
under ``tmp_path``. No socket is bound: the hook's datagram goes nowhere,
which it tolerates. An ``atexit`` hook records every ``aipager`` module the
process loaded.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SESSION = "claude-i18n-iso"
EVENTS = (
    "SessionStart", "SessionEnd", "UserPromptSubmit",
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest",
    "Notification", "Stop", "StopFailure", "SubagentStart", "SubagentStop",
    "PreCompact", "PostCompact", "MessageDisplay", "PreModelSwitch",
)


def _env(tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    inst = tmp_path / "inst"
    inst.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "AIPAGER"))}
    env.update(PYTHONPATH=str(REPO), AIPAGER_INSTANCE_DIR=str(inst),
               CLAUDE_DTACH_SESSION=SESSION, HOME=str(home),
               AIPAGER_PERMISSION_REPLY_DEADLINE_SECONDS="0.3",
               PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(tmp_path))
    return env


def _payload(event):
    data = {"hook_event_name": event, "session_id": "s-iso", "cwd": str(REPO),
            "transcript_path": "/nonexistent/t.jsonl"}
    if event in ("PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest"):
        data.update(tool_name="Bash", tool_input={"command": "sleep 90"},
                    tool_use_id="toolu_1")
    if event == "PostToolUse":
        data["tool_input"]["run_in_background"] = True
        data["tool_response"] = {"backgroundTaskId": "bg1"}
    if event in ("SubagentStart", "SubagentStop"):
        data.update(agent_id="a1", agent_type="Explore")
    if event == "UserPromptSubmit":
        data["prompt"] = "hello"
    if event == "MessageDisplay":
        data["message"] = "some prose"
    return data


def _loaded(tmp_path, module, payload, tag):
    out = tmp_path / f"mods-{tag}.json"
    script = (
        "import atexit, json, sys\n"
        f"OUT = {str(out)!r}\n"
        "def _dump():\n"
        "    mods = sorted(m for m in sys.modules if m == 'aipager' or m.startswith('aipager.'))\n"
        "    open(OUT, 'w').write(json.dumps(mods))\n"
        "atexit.register(_dump)\n"
        f"from aipager.dtach.{module} import main\n"
        "try:\n"
        "    main()\n"
        "except SystemExit:\n"
        "    pass\n")
    proc = subprocess.run([sys.executable, "-c", script], input=json.dumps(payload),
                          capture_output=True, text=True, env=_env(tmp_path),
                          cwd=str(tmp_path), timeout=60)
    assert out.exists(), f"the run recorded nothing: {proc.stderr[-800:]}"
    return set(json.loads(out.read_text()))


def _i18n(mods):
    return sorted(m for m in mods if m == "aipager.i18n" or m.startswith("aipager.i18n."))


@pytest.mark.parametrize("event", EVENTS)
def test_the_hook_never_loads_i18n(tmp_path, event):
    assert _i18n(_loaded(tmp_path, "notify_hook", _payload(event), event)) == []


@pytest.mark.parametrize("event", ["PreToolUse", "Stop"])
def test_the_harness_really_ran_the_hook(tmp_path, event):
    """Guard the guard: the record holds the hook itself, so an empty
    record cannot pass as 'no i18n'."""
    assert "aipager.dtach.notify_hook" in _loaded(tmp_path, "notify_hook",
                                                  _payload(event), event)


def test_the_status_line_never_loads_i18n(tmp_path):
    payload = {"session_id": "s-iso", "model": {"display_name": "Opus"},
               "cost": {"total_cost_usd": 1.0}, "context_window": {}}
    assert _i18n(_loaded(tmp_path, "statusline_notify", payload, "sl")) == []


def test_the_clock_module_loads_no_i18n(tmp_path):
    """bg_shells (in the hook closure) needs the clock: its module must be
    stdlib only."""
    script = ("import json, sys; import aipager.clockfmt; "
              "print(json.dumps(sorted(m for m in sys.modules if m.startswith('aipager'))))")
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          env=_env(tmp_path), cwd=str(tmp_path), timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert _i18n(json.loads(proc.stdout)) == []


@pytest.mark.parametrize("module", ["aipager.bg_shells", "aipager.config", "aipager.state",
                                    "aipager.policy_snapshot",
                                    "aipager.dtach.notify_hook",
                                    "aipager.dtach.statusline_notify"])
def test_importing_a_hook_module_loads_no_i18n(tmp_path, module):
    script = (f"import json, sys; import {module}; "
              "print(json.dumps(sorted(m for m in sys.modules if m.startswith('aipager'))))")
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          env=_env(tmp_path), cwd=str(tmp_path), timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert _i18n(json.loads(proc.stdout)) == []


def test_the_record_would_show_a_loaded_i18n(tmp_path):
    """Guard the guard: a hook process that did load aipager.i18n is seen."""
    out = tmp_path / "mods-planted.json"
    script = (
        "import atexit, json, sys\n"
        f"OUT = {str(out)!r}\n"
        "atexit.register(lambda: open(OUT, 'w').write(json.dumps(sorted("
        "m for m in sys.modules if m.startswith('aipager')))))\n"
        "import aipager.i18n\n"
        "from aipager.dtach.notify_hook import main\n"
        "main()\n")
    subprocess.run([sys.executable, "-c", script], input=json.dumps(_payload("Stop")),
                   capture_output=True, text=True, env=_env(tmp_path),
                   cwd=str(tmp_path), timeout=60)
    assert "aipager.i18n" in _i18n(set(json.loads(out.read_text())))
