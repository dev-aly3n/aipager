"""Scenario 17 (1.6.0 security): a Bash command that takes the safety check
more than 3 seconds to read is denied in time.

dave (admin) runs a real turn in the group, so the session's hook writes
the rights of his Telegram turn. Then the instance's own ``aipager-hook``
gets two PreToolUse calls of that session and turn, as Claude Code would
send them (a real Claude will not type 100 KB on request):

- about 100 KB of ``rg~/`` (the search rules alone take minutes on it):
  denied as "could not be checked" within seconds, never left to Claude
  Code's own hook timeout (which runs the call unchecked);
- a 100 KB heredoc of plain notes: still checked and allowed, in seconds.

Runs with real Claude or the stand-in.
"""

from __future__ import annotations

import json
import subprocess
import time

from tests.e2e.fake_telegram import instance as fti
from tests.e2e.faketg import flows

G = fti.GROUP_ID
BOT = f"@{fti.BOT_USERNAME}"
LABEL = "ft44"
UNCHECKED = "this tool call could not be checked, so it was denied"


def _pre(inst, name: str, command: str) -> tuple[str, float]:
    env = dict(inst.env)
    env.pop("PYTEST_CURRENT_TEST", None)
    env["CLAUDE_DTACH_SESSION"] = name
    payload = {"hook_event_name": "PreToolUse", "session_id": "s-ft44",
               "transcript_path": inst._registry_transcript(name),
               "cwd": str(inst.session_folder(name)), "permission_mode": "default",
               "tool_name": "Bash", "tool_input": {"command": command},
               "tool_use_id": "toolu_ft44_1"}
    t0 = time.monotonic()
    p = subprocess.run([str(inst.bin_dir / "aipager-hook")], input=json.dumps(payload),
                       env=env, capture_output=True, text=True, timeout=180)
    return p.stdout.strip(), time.monotonic() - t0


def _reason(out: str) -> str | None:
    if not out:
        return None
    hso = json.loads(out).get("hookSpecificOutput") or {}
    return hso.get("permissionDecisionReason") if hso.get("permissionDecision") == "deny" \
        else None


def test_a_command_built_to_slow_the_check_is_denied_in_time(fresh):
    inst = fresh
    name = inst.new_session(G, fti.DAVE, LABEL)
    flows.prompt_turn(inst, G, fti.DAVE, name, f"{BOT} ft44 turn: Reply with exactly: OK",
                      "ft44 turn")
    assert inst._registry_transcript(name), "no transcript for the session"

    out, took = _pre(inst, name, "rg~/" * 25_000 + " ~")
    reason = _reason(out)
    assert reason is not None and UNCHECKED in reason, out[:500]
    assert took < 10, f"the hook took {took:.1f}s"

    notes = "cat > notes.txt <<'EOF'\n" + "a plain line of notes\n" * 5_000 + "EOF"
    out, took = _pre(inst, name, notes)
    assert _reason(out) is None, out[:500]
    assert took < 10, f"the hook took {took:.1f}s"
