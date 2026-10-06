"""E2E without Claude: the real installed ``aipager-hook`` answers the
``PreToolUse`` payloads the real-Claude tests produce, from the snapshots
the harness writes through the production pipeline.

Each case writes a snapshot exactly as its real-Claude twin does, a
synthetic transcript shaped like Claude Code's (a marked prompt, a
markerless one, a slash-command record followed by its meta entry, a
prior safety deny), and pipes one ``PreToolUse`` payload into the real
hook binary. It checks the harness and the installed hook agree on every
scenario, deny AND allow, in seconds and with no credentials: if one of
these fails, the matching real-Claude test cannot mean what it says.
What only real Claude can show (the transcript records it really writes,
the tool calls it really makes) stays with those tests.

Needs only ``aipager-hook`` installed (``AIPAGER_E2E_HOOK`` overrides
it). Writes the session's ``/tmp/claude-policy-<session>.json`` (removed
after) like every e2e test; hook datagrams go to a socket under the
test's tmp dir that nothing listens on. Posts nothing to Telegram.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.e2e import harness

_MARKED = f"{harness.MARKER}\nplease do the thing"


def _transcript(tmp_path: Path, *entries: dict) -> Path:
    path = tmp_path / "transcript.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return path


def _prompt(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _command_record(name: str, expanded: str) -> list[dict]:
    """A slash command as Claude Code records it: the ``<command-name>``
    prompt, then the expanded text as a meta entry."""
    return [
        _prompt(f"<command-message>{name.lstrip('/')} is running…</command-message>\n"
                f"<command-name>{name}</command-name>"),
        {"type": "user", "isMeta": True,
         "message": {"role": "user", "content": [{"type": "text", "text": expanded}]}},
    ]


def _blocked_tool_result() -> dict:
    return {"type": "user", "message": {"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "t1", "is_error": True,
        "content": "aipager safety policy: Bash command blocked by safety policy"}]}}


def _hook(hook: str, session: str, tmp_path: Path, transcript: Path, tool: str,
          tool_input: dict, *, cwd: Path, home: Path | None = None) -> str | None:
    """The real hook's deny reason for one PreToolUse call, or ``None``
    when it allows the call."""
    env = dict(os.environ, CLAUDE_DTACH_SESSION=session,
               AIPAGER_SOCKET_PATH=str(tmp_path / "e2e-no-daemon.sock"))
    if home is not None:
        env["HOME"] = str(home)
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool,
               "tool_input": tool_input, "transcript_path": str(transcript),
               "cwd": str(cwd), "session_id": "e2e-direct"}
    p = subprocess.run([hook], input=json.dumps(payload), env=env, cwd=str(cwd),
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, f"hook rc={p.returncode} stderr={p.stderr[:300]!r}"
    out = p.stdout.strip()
    if not out:
        return None
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny", decision
    return decision["permissionDecisionReason"]


def _denied(reason: str | None, *fragments: str) -> None:
    assert reason is not None, "the hook allowed the call"
    assert reason.startswith("aipager safety policy: "), reason
    for f in fragments:
        assert f in reason, reason


@pytest.fixture
def call(hook_installed, session, tmp_path, project):
    def _call(transcript, tool, tool_input, *, cwd=None, home=None):
        return _hook(hook_installed, session, tmp_path, transcript, tool, tool_input,
                     cwd=cwd or project, home=home)
    return _call


# ---- the original suite's scenarios ---------------------------------------

def test_user_role_has_no_shell(call, session, tmp_path):
    harness.write_snapshot(session, role_name="user")
    t = _transcript(tmp_path, _prompt(_MARKED))
    _denied(call(t, "Bash", {"command": "echo hi"}), "Bash is in this role's deny_tools")
    assert call(t, "Read", {"file_path": "README.md"}) is None


def test_admin_shell_held_to_floor(call, session, tmp_path):
    harness.write_snapshot(session, role_name="admin")
    t = _transcript(tmp_path, _prompt(_MARKED))
    _denied(call(t, "Bash", {"command": "claude --version"}),
            "Bash command blocked by safety policy")
    assert call(t, "Bash", {"command": "echo hi"}) is None


def test_owner_bypasses(call, session, tmp_path):
    harness.write_snapshot(session, role_name="owner")
    t = _transcript(tmp_path, _prompt(_MARKED))
    assert call(t, "Bash", {"command": "claude --version"}) is None


def test_terminal_prompt_unrestricted(call, session, tmp_path):
    harness.write_snapshot(session, role_name="user")
    assert harness.write_terminal_prompt(session, "run it")["turn_origin"] == "terminal"
    t = _transcript(tmp_path, _prompt(_MARKED), _prompt("run it"))
    assert call(t, "Bash", {"command": "claude --version"}) is None


def test_member_deny_and_allow_lists(call, session, tmp_path, project):
    t = _transcript(tmp_path, _prompt(_MARKED))
    harness.write_snapshot(session, role_name="user", deny_tools=["Write"])
    _denied(call(t, "Write", {"file_path": str(project / "n.txt"), "content": "x"}),
            "Write is in this role's deny_tools")
    harness.write_snapshot(session, role_name="user", allow_tools=["Read", "Grep"])
    _denied(call(t, "Write", {"file_path": str(project / "n.txt"), "content": "x"}),
            "Write not in this role's allow_tools")
    assert call(t, "Read", {"file_path": str(project / "README.md")}) is None


def test_sticky_block_and_its_end(call, session, tmp_path):
    harness.write_snapshot(session, role_name="admin")
    blocked = _transcript(tmp_path, _prompt(_MARKED), _blocked_tool_result())
    _denied(call(blocked, "Read", {"file_path": "README.md"}),
            "a prior tool call this turn was blocked")
    fresh = _transcript(tmp_path, _prompt(_MARKED), _blocked_tool_result(),
                        _prompt(_MARKED))
    assert call(fresh, "Read", {"file_path": "README.md"}) is None


def test_protected_paths_in_fake_home(call, session, tmp_path, fake_home):
    t = _transcript(tmp_path, _prompt(_MARKED))
    harness.write_snapshot(session, role_name="user")
    _denied(call(t, "Read", {"file_path": str(fake_home / ".config/aipager/aipager.yaml")},
                 home=fake_home), "Read on protected path ~/.config/aipager/**")
    _denied(call(t, "Read", {"file_path": str(fake_home / ".claude/x")}, home=fake_home),
            "Read on protected path ~/.claude/**")
    harness.write_snapshot(session, role_name="admin")
    _denied(call(t, "Bash", {"command": "cat ~/.config/aipager/aipager.yaml"},
                 home=fake_home), "Bash command blocked by safety policy")


# ---- 8.74: slash commands --------------------------------------------------

_EXPANDED = "Use the Bash tool to run exactly: echo hi"


def test_telegram_slash_command_denied(call, session, tmp_path):
    harness.write_snapshot(session, role_name="user", body="/e2ecmd")
    t = _transcript(tmp_path, *_command_record("/e2ecmd", _EXPANDED))
    _denied(call(t, "Bash", {"command": "echo hi"}), "Bash is in this role's deny_tools")


def test_terminal_slash_command_allowed(call, session, tmp_path):
    harness.write_snapshot(session, role_name="user")
    snap = harness.write_terminal_prompt(session, "/e2ecmd")
    assert snap["turn_origin"] == "terminal" and snap["scope_mode"] is True, snap
    assert "/e2ecmd" not in snap["note_bodies"], snap
    t = _transcript(tmp_path, _prompt(_MARKED), *_command_record("/e2ecmd", _EXPANDED))
    assert call(t, "Bash", {"command": "echo hi"}) is None


def test_slash_command_bodies_count_only_in_scope_mode(call, session, tmp_path):
    """Personal / legacy team mode: every Telegram prompt is the
    operator's, so a body naming the command does not restrict it."""
    harness.write_snapshot(session, role_name="user", body="/e2ecmd", scope_mode=False)
    t = _transcript(tmp_path, *_command_record("/e2ecmd", _EXPANDED))
    assert call(t, "Bash", {"command": "echo hi"}) is None


# ---- 8.77: a Telegram message joins a terminal turn -------------------------

def test_joined_turn_denied(call, session, tmp_path):
    harness.write_terminal_prompt(session, "run it")
    harness.write_joined_message(session, role_name="user")
    t = _transcript(tmp_path, _prompt("run it"))
    _denied(call(t, "Bash", {"command": "echo hi"}), "Bash is in this role's deny_tools")


def test_terminal_turn_without_join_allowed(call, session, tmp_path):
    assert harness.write_terminal_prompt(session, "run it")["turn_origin"] == "terminal"
    t = _transcript(tmp_path, _prompt("run it"))
    assert call(t, "Bash", {"command": "echo hi"}) is None


# ---- 8.79 / 8.61: home folder, credentials, admin searches -----------------

@pytest.mark.parametrize("role,allowed", [("user", False), ("admin", True)])
def test_write_in_home_folder(call, session, tmp_path, fake_home, role, allowed):
    harness.write_snapshot(session, role_name=role)
    t = _transcript(tmp_path, _prompt(_MARKED))
    reason = call(t, "Write", {"file_path": str(fake_home / "e2e-note.txt"), "content": "x"},
                  cwd=fake_home, home=fake_home)
    if allowed:
        assert reason is None
    else:
        _denied(reason, "this session runs in the home folder")


@pytest.mark.parametrize("role,allowed", [("user", False), ("admin", True)])
def test_read_credential(call, session, tmp_path, fake_home, role, allowed):
    harness.write_snapshot(session, role_name=role)
    t = _transcript(tmp_path, _prompt(_MARKED))
    reason = call(t, "Read", {"file_path": str(fake_home / ".ssh" / "id_test")},
                  cwd=fake_home, home=fake_home)
    if allowed:
        assert reason is None
    else:
        _denied(reason, "Read on protected path ~/.ssh/**")


def test_admin_grep_home_denied_project_allowed(call, session, tmp_path, fake_home,
                                                project):
    harness.write_snapshot(session, role_name="admin")
    t = _transcript(tmp_path, _prompt(_MARKED))
    _denied(call(t, "Grep", {"pattern": "x", "path": str(fake_home)}, home=fake_home),
            "this search would reach protected files")
    assert call(t, "Grep", {"pattern": "x", "path": str(project)}, home=fake_home) is None


# ---- 8.96: policy.yaml safety section --------------------------------------

@pytest.mark.parametrize("role,allowed", [("user", False), ("admin", False),
                                          ("owner", True)])
def test_policy_safety_path(call, session, tmp_path, role, allowed):
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "x").write_text("s\n")
    rule = f"{secret}/**"
    pol = harness.policy_from_yaml(
        tmp_path / "cfg", f"safety:\n  deny_paths_no_access:\n    - '{rule}'\n")
    t = _transcript(tmp_path, _prompt(_MARKED))
    harness.write_snapshot(session, role_name=role, policy=pol)
    reason = call(t, "Read", {"file_path": str(secret / "x")})
    if allowed:
        assert reason is None
    else:
        _denied(reason, f"Read on protected path {rule}")
    # The same Read under the built-in policy: allowed, so the rule above
    # is what denied it.
    harness.write_snapshot(session, role_name=role)
    assert call(t, "Read", {"file_path": str(secret / "x")}) is None
