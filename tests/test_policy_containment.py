"""Restricted Telegram turns are contained by rules that are boundaries,
not by regex patterns (roadmap 8.50 rework, 8.52, 8.53).

Found 2026-09-26 by an independent review of 8.50:

- A restricted turn with Bash can rewrite aipager's own policy file,
  ``/tmp/claude-policy-<session>.json`` (same OS user), through a shell
  glob no deny pattern can anticipate (``sed -i … /tmp/*-policy-*.json``,
  a ``for`` loop, python ``glob``), and grant itself the owner's bypass.
  Operator decision 2026-09-26: "No Bash for restricted" — the built-in
  ``user`` and ``read_only`` roles lose Bash and every other tool Claude
  Code runs code with.
- A restricted turn could Write anywhere the OS user can: ``~/.bashrc``,
  ``~/.ssh``, other projects. Writes are now confined to the session's
  folder (from the hook payload's ``cwd``) and its scratchpad.
- 8.52: Grep/Glob checked only their ``path``: ``Grep ~/.config`` with
  ``glob: aipager/**`` read the bot token, ``Grep /tmp`` with
  ``glob: claude-policy-*`` read other sessions' policy files.
- The 8.50 path rule denied the reply-to-an-older-message flow, which
  tells Claude to Read ``/tmp/claude-reply-<session>.txt``.
- 8.53: on macOS ``/tmp`` is ``/private/tmp`` and the disk is
  case-insensitive, so ``/private/tmp/claude-policy-x.json``,
  ``/TMP/…`` and ``/tmp/CLAUDE-POLICY-…`` passed the anchored globs.

Everything goes through ``enforce.decide`` with snapshots resolved from
the real built-in roles.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from aipager import doctor, policy, safety
from aipager import policy_snapshot as ps
from aipager.dtach import enforce

SESSION = "claude-proj"
HOME = os.path.expanduser("~")
UID = os.getuid()
CLAUDE_SID = "5b1d7363-aaaa-bbbb-cccc-000000000001"


@pytest.fixture(autouse=True)
def _isolate_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path",
                        lambda n: tmp_path / f"{n}.policy.json")
    # Claude Code's scratchpad follows these; the tests assume /tmp.
    monkeypatch.delenv("CLAUDE_CODE_TMPDIR", raising=False)
    monkeypatch.delenv("TMPDIR", raising=False)


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "work" / "proj"
    (p / "src").mkdir(parents=True)
    return p


def _builtin(name):
    pol = policy.load_policy(Path("/nonexistent/p.yaml"),
                             Path("/nonexistent/p.d"))
    return pol.get_role(name)


# Captured at import, before any fixture redirects it.
_REAL_REPLY_PATH = ps.reply_context_path


@pytest.fixture(autouse=True)
def _real_reply_path(monkeypatch):
    """These rows ask the policy about the REAL reply-file path, which the
    protected-path list names (``/tmp/claude-reply-*``): a pure decision,
    and no row may read, write or clear through it (``claude-proj`` is a
    valid session name). The root conftest redirects the
    function for every test (``_isolate_session_tmp_files``); this module
    puts the real one back for its decisions only. Its snapshot writes
    stay redirected."""
    monkeypatch.setattr(ps, "reply_context_path", _REAL_REPLY_PATH)
    monkeypatch.setattr(enforce, "reply_context_path", _REAL_REPLY_PATH)


def _use_role(role):
    ps.write_merged_snapshot(SESSION, ps.resolve_snapshot(role, None, None))


def _transcript(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"type": "user", "message": {
        "content": "[via Telegram · @bob · role:user]\ndo it"}}) + "\n")
    return str(p)


def _decide(tmp_path, tool, tool_input, *, cwd, session=SESSION):
    return enforce.decide({
        "hook_event_name": "PreToolUse",
        "session": session,
        "session_id": CLAUDE_SID,
        "cwd": str(cwd),
        "tool_name": tool,
        "tool_input": tool_input,
        "transcript_path": _transcript(tmp_path),
    })


# ===========================================================================
# R1 — no Bash (or any other code-running tool) for the built-in
# restricted roles
# ===========================================================================

@pytest.mark.parametrize("role", ["user", "read_only"])
def test_restricted_role_cannot_run_bash(tmp_path, project, role):
    _use_role(_builtin(role))
    block = _decide(tmp_path, "Bash", {"command": "ls"}, cwd=project)
    assert block and "deny_tools" in block["reason"]


# Claude Code 2.1.283's own code-execution set: every tool flagged
# ``enablesCodeExecution``, plus PowerShell, which it adds by name.
CODE_TOOLS = ("Bash", "PowerShell", "Monitor", "REPL", "Workflow", "CronCreate",
              "RemoteTrigger", "AppifactRepl", "self_hosted_runner_spawn_local",
              "self_hosted_runner_requeue_session")


@pytest.mark.parametrize("tool", CODE_TOOLS)
def test_user_role_cannot_run_any_code_execution_tool(tmp_path, project, tool):
    _use_role(_builtin("user"))
    assert _decide(tmp_path, tool, {"command": "ls"}, cwd=project)


def test_the_shipped_code_execution_list_covers_them_all():
    assert set(CODE_TOOLS) <= set(safety.CODE_EXECUTION_TOOLS)


# Not code execution, but each gets a restricted turn out of its box:
# SendMessage delivers a marker-less prompt to another local session
# (terminal origin there, i.e. unrestricted); EnterWorktree/ExitWorktree
# move the session's cwd, which is where writes are confined to.
@pytest.mark.parametrize("tool", ["SendMessage", "EnterWorktree", "ExitWorktree"])
@pytest.mark.parametrize("role", ["user", "read_only"])
def test_restricted_roles_cannot_leave_their_session(tmp_path, project, role, tool):
    _use_role(_builtin(role))
    assert _decide(tmp_path, tool, {"to": "uds:/x", "message": "hi"}, cwd=project)


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_owner_and_admin_keep_bash(tmp_path, project, role):
    _use_role(_builtin(role))
    assert _decide(tmp_path, "Bash", {"command": "ls"}, cwd=project) is None


def test_the_policy_glob_rewrite_that_motivated_this_is_stopped(tmp_path, project):
    _use_role(_builtin("user"))
    cmd = "sed -i 's/false/true/' /tmp/*-policy-*.json"
    assert safety.bash_violation(cmd, safety.DENY_BASH_PATTERNS) is None  # why
    assert _decide(tmp_path, "Bash", {"command": cmd}, cwd=project)


def _load(tmp_path, text):
    f = tmp_path / "policy.yaml"
    f.write_text(text)
    return policy.load_policy(f, tmp_path / "none.d")


def test_policy_yaml_can_give_a_role_bash_through_allow_tools(tmp_path, project):
    pol = _load(tmp_path, "roles:\n  user:\n    allow_tools: [Bash, Read, Edit]\n")
    role = pol.get_role("user")
    assert "Bash" not in role.deny_tools
    assert "PowerShell" in role.deny_tools  # only what was named
    _use_role(role)
    assert _decide(tmp_path, "Bash", {"command": "ls"}, cwd=project) is None


def test_policy_yaml_can_give_a_role_bash_by_replacing_deny_tools(tmp_path, project):
    pol = _load(tmp_path, "roles:\n  user:\n    deny_tools: [WebFetch]\n")
    _use_role(pol.get_role("user"))
    assert _decide(tmp_path, "Bash", {"command": "ls"}, cwd=project) is None


def test_an_explicit_deny_in_the_same_layer_still_wins(tmp_path, project):
    pol = _load(tmp_path, "roles:\n  user:\n    allow_tools: [Bash]\n"
                          "    deny_tools: [Bash]\n")
    _use_role(pol.get_role("user"))
    assert _decide(tmp_path, "Bash", {"command": "ls"}, cwd=project)


def test_a_policy_d_allow_does_not_undo_a_later_explicit_deny(tmp_path):
    f = tmp_path / "policy.yaml"
    f.write_text("roles:\n  user:\n    deny_tools: [Bash]\n")
    d = tmp_path / "policy.d"
    d.mkdir()
    (d / "10.yaml").write_text("roles:\n  user:\n    can_prompt: true\n")
    assert "Bash" in policy.load_policy(f, d).get_role("user").deny_tools


def _scopes_with(*roles):
    from aipager.scope import Member, Scope
    return [Scope(chat_id=-1, kind="group", label="g", members=tuple(
        Member(id=i + 1, label=f"m{i}", role=r) for i, r in enumerate(roles)))]


def test_doctor_warns_once_per_role_that_has_bash_without_bypass(tmp_path, monkeypatch):
    import aipager.config as cfg
    pol = _load(tmp_path, "roles:\n  user:\n    allow_tools: [Bash, Read]\n")
    monkeypatch.setattr(cfg, "POLICY", pol)
    monkeypatch.setattr(cfg, "SCOPES", _scopes_with(
        "owner", "admin", "user", "user", "read_only"))
    res = doctor.check_role_shell_access()
    assert res.status == doctor.WARN
    lines = [d for d in res.detail if "has Bash" in d]
    assert lines == [
        "role admin has Bash: its safety rules are best-effort, not a boundary",
        "role user has Bash: its safety rules are best-effort, not a boundary",
    ]


def test_doctor_warns_when_a_role_deny_on_bash_is_bypassed(tmp_path, monkeypatch):
    import aipager.config as cfg
    pol = _load(tmp_path, "roles:\n  lead:\n    bypass_role_denies: true\n"
                          "    deny_tools: [Bash]\n")
    monkeypatch.setattr(cfg, "POLICY", pol)
    monkeypatch.setattr(cfg, "SCOPES", _scopes_with("lead"))
    res = doctor.check_role_shell_access()
    assert res.status == doctor.WARN
    assert res.detail == [
        "role lead has Bash: its safety rules are best-effort, not a boundary"]


def test_doctor_names_other_code_tools_a_role_kept(tmp_path, monkeypatch):
    import aipager.config as cfg
    pol = _load(tmp_path, "roles:\n  user:\n    deny_tools: [Bash, WebFetch, REPL]\n")
    monkeypatch.setattr(cfg, "POLICY", pol)
    monkeypatch.setattr(cfg, "SCOPES", _scopes_with("user"))
    res = doctor.check_role_shell_access()
    assert res.status == doctor.WARN
    kept = [t for t in safety.CODE_EXECUTION_TOOLS if t not in ("Bash", "REPL")]
    assert res.detail == [
        f"role user has {', '.join(kept)}: its safety rules are best-effort, "
        "not a boundary"]


def test_safety_check_lists_what_each_role_can_do(tmp_path, monkeypatch, capsys):
    import aipager.config as cfg
    pol = _load(tmp_path, "roles:\n  lead:\n    deny_tools: [Bash]\n")
    monkeypatch.setattr(cfg, "POLICY", pol)
    doctor._print_safety_policy()
    out = " ".join(capsys.readouterr().out.split())
    assert "• user (no code tools, writes confined to the session folder)" in out
    assert "• owner (bypass_safety, bypass_role_denies, can_manage)" in out
    assert ("• admin (bypass_role_denies, can_manage, runs code ("
            + ", ".join(safety.CODE_EXECUTION_TOOLS) + "): best-effort rules)") in out
    assert "• lead (runs code (PowerShell," in out
    assert "not a boundary" in out


def test_doctor_is_quiet_with_the_built_in_restricted_roles(monkeypatch):
    import aipager.config as cfg
    monkeypatch.setattr(cfg, "POLICY", policy.load_policy(
        Path("/nonexistent/p.yaml"), Path("/nonexistent/p.d")))
    monkeypatch.setattr(cfg, "SCOPES", _scopes_with("owner", "user", "read_only"))
    assert doctor.check_role_shell_access().status == doctor.OK
    assert doctor.check_role_shell_access in doctor.CHECKS


# ===========================================================================
# R2 — restricted writes stay inside the project and the scratchpad
# ===========================================================================

def _scratch(project):
    enc = "".join(c if c.isalnum() else "-" for c in str(project))
    return f"/tmp/claude-{UID}/{enc}/{CLAUDE_SID}/scratchpad"


@pytest.mark.parametrize("path", [
    "~/.bashrc",
    "~/.ssh/authorized_keys",
    "~/.profile",
    "/etc/cron.d/x",
    "{tmp}/work/other-project/src/a.py",
    "{tmp}/work/proj-evil/a.py",          # a sibling sharing the prefix
    "/tmp/claude-policy-claude-proj.json",
    "/tmp/notes.txt",
    "{proj}/../other-project/a.py",
    "{proj}/.claude/settings.json",       # project hooks run shell commands
    "{proj}/.claude/settings.local.json",
    "{proj}/.mcp.json",                   # an MCP server is a command
    "{proj}/.git/config",                 # core.fsmonitor runs on git status
    "{proj}/.git/hooks/pre-commit",
    f"/tmp/claude-{UID}/-some-other-project/abc/scratchpad/x",
])
@pytest.mark.parametrize("tool", ["Write", "Edit", "NotebookEdit", "MultiEdit"])
def test_restricted_writes_outside_the_project_are_denied(tmp_path, project, tool, path):
    _use_role(_builtin("user"))
    p = path.format(tmp=tmp_path, proj=project)
    key = safety._PATH_KEYS[tool]
    assert _decide(tmp_path, tool, {key: p}, cwd=project)


@pytest.mark.parametrize("path", [
    "{proj}/src/a.py",
    "{proj}/README.md",
    "{proj}/new/dir/file.txt",
    "{proj}/docs/.claude-notes.md",
    "{scratch}/out.txt",
])
@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_restricted_writes_inside_the_project_are_allowed(tmp_path, project, tool, path):
    _use_role(_builtin("user"))
    p = path.format(proj=project, scratch=_scratch(project))
    assert _decide(tmp_path, tool, {"file_path": p}, cwd=project) is None


def test_the_scratchpad_follows_claude_codes_temp_dir(tmp_path, project, monkeypatch):
    base = tmp_path / "ctmp"
    monkeypatch.setenv("CLAUDE_CODE_TMPDIR", str(base))
    _use_role(_builtin("user"))
    enc = "".join(c if c.isalnum() else "-" for c in str(project))
    own = base / f"claude-{UID}" / enc / CLAUDE_SID / "scratchpad" / "x"
    assert _decide(tmp_path, "Write", {"file_path": str(own)}, cwd=project) is None
    assert _decide(tmp_path, "Write", {"file_path": _scratch(project) + "/x"},
                   cwd=project)
    monkeypatch.delenv("CLAUDE_CODE_TMPDIR")
    monkeypatch.setenv("TMPDIR", str(base) + "/")
    assert _decide(tmp_path, "Write", {"file_path": str(own)}, cwd=project) is None


def test_a_symlink_in_the_project_to_a_file_outside_is_denied(tmp_path, project):
    _use_role(_builtin("user"))
    outside = tmp_path / "rc"
    outside.write_text("x")
    (project / "rc").symlink_to(outside)
    assert _decide(tmp_path, "Write", {"file_path": str(project / "rc")},
                   cwd=project)


def test_the_write_root_comes_from_the_payload_not_the_tool_input(tmp_path, project):
    _use_role(_builtin("user"))
    assert _decide(tmp_path, "Write",
                   {"file_path": f"{HOME}/.bashrc", "cwd": HOME}, cwd=project)


def test_no_cwd_in_the_payload_fails_closed(tmp_path, project):
    _use_role(_builtin("user"))
    # not even the hook process's own working directory
    assert enforce.decide({
        "hook_event_name": "PreToolUse", "session": SESSION,
        "tool_name": "Write",
        "tool_input": {"file_path": os.path.join(os.getcwd(), "a.txt")},
        "transcript_path": _transcript(tmp_path),
    })


def test_a_snapshot_without_the_field_is_confined(tmp_path, project):
    """A snapshot written before ``confine_writes`` existed."""
    snap = ps.resolve_snapshot(_builtin("admin"), None, None)
    del snap["confine_writes"]
    ps.write_merged_snapshot(SESSION, snap)
    assert _decide(tmp_path, "Write", {"file_path": f"{HOME}/.bashrc"},
                   cwd=project)


def test_relative_paths_resolve_against_the_payload_cwd(tmp_path, project):
    _use_role(_builtin("user"))
    assert _decide(tmp_path, "Write", {"file_path": "src/a.py"},
                   cwd=project) is None
    assert _decide(tmp_path, "Write", {"file_path": "../other/a.py"},
                   cwd=project)


def test_lsp_honours_the_protected_paths(tmp_path, project):
    _use_role(_builtin("user"))
    assert _decide(tmp_path, "LSP", {"operation": "documentSymbol",
                                     "filePath": "~/.config/aipager/daemon.env"},
                   cwd=project)
    # review 4: a leading space must not hide the protected path
    assert _decide(tmp_path, "LSP", {"operation": "documentSymbol",
                                     "filePath": " ~/.config/aipager/daemon.env"},
                   cwd=project)
    assert _decide(tmp_path, "Read", {"file_path": "\ufeff~/.config/aipager/daemon.env"},
                   cwd=project)


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_owner_and_admin_writes_are_not_confined(tmp_path, project, role):
    _use_role(_builtin(role))
    target = str(tmp_path / "work" / "other-project" / "a.py")
    assert _decide(tmp_path, "Write", {"file_path": target}, cwd=project) is None


@pytest.mark.parametrize("value", [["~/.bashrc"], {"p": 1}, 7])
def test_a_malformed_path_fails_closed(tmp_path, project, value):
    _use_role(_builtin("user"))
    assert _decide(tmp_path, "Write", {"file_path": value}, cwd=project)


def test_restricted_reads_keep_the_deny_list_model(tmp_path, project):
    _use_role(_builtin("user"))
    other = str(tmp_path / "work" / "other-project" / "a.py")
    assert _decide(tmp_path, "Read", {"file_path": other}, cwd=project) is None
    assert _decide(tmp_path, "Read", {"file_path": "~/.claude/x"}, cwd=project)


def test_the_floor_confines_writes(tmp_path, project):
    # no snapshot on disk at all
    assert _decide(tmp_path, "Write", {"file_path": f"{HOME}/.bashrc"},
                   cwd=project)
    assert _decide(tmp_path, "Write", {"file_path": str(project / "a")},
                   cwd=project) is None


def test_the_unattributed_prompt_keeps_confinement(tmp_path, project):
    _use_role(_builtin("user"))
    kept = ps.snapshot_for_unattributed_prompt(
        ps.read_snapshot(SESSION), [], "typed in the terminal")
    assert kept["confine_writes"] is True


def test_merge_confines_when_any_note_confines():
    owner = ps.resolve_snapshot(_builtin("owner"), None, None)
    user = ps.resolve_snapshot(_builtin("user"), None, None)
    assert owner["confine_writes"] is False
    assert user["confine_writes"] is True
    assert ps.merge_snapshots([owner, user])["confine_writes"] is True
    assert ps.merge_snapshots([owner])["confine_writes"] is False
    legacy = {k: v for k, v in owner.items() if k != "confine_writes"}
    legacy["bypass_safety"] = False
    assert ps.merge_snapshots([legacy])["confine_writes"] is True


# ===========================================================================
# R3 (8.52) — a restricted Grep/Glob is an allow-list, not a model of
# ripgrep. Operator decision 2026-09-27, after three review rounds each
# found a new glob spelling (a list, a leading ``/``, ``\\`` escapes,
# U+FEFF, a ``#`` comment, Glob's path-tail rewrite) that reached the bot
# token: the search root must be inside the session's folder or
# scratchpad, and every glob must be plain and relative.
# ===========================================================================

DENIED_SEARCHES = [
    # the original 8.52 finds
    ("Grep", {"path": "~/.config", "glob": "aipager/**", "pattern": "TOKEN"}),
    ("Grep", {"path": "~/.config", "glob": "daemon.env", "pattern": "TOKEN"}),
    ("Grep", {"path": "~/.config", "pattern": "TOKEN"}),
    ("Grep", {"path": "/tmp", "glob": "claude-policy-*", "pattern": "."}),
    ("Grep", {"path": "/tmp", "pattern": "bypass_safety"}),
    ("Grep", {"path": "~", "glob": "**/.claude/**", "pattern": "."}),
    ("Grep", {"path": "/", "glob": "**", "pattern": "."}),
    ("Grep", {"path": "/", "glob": "*.env", "pattern": "."}),
    ("Grep", {"path": "~/.config/aipager", "pattern": "."}),
    ("Grep", {"path": "{proj}", "glob": "../../../../../../../../tmp/claude-policy-*",
              "pattern": "."}),
    ("Glob", {"path": "~/.config", "pattern": "aipager/*"}),
    ("Glob", {"path": "/tmp", "pattern": "claude-policy-*"}),
    ("Glob", {"pattern": "~/.config/aipager/*"}),
    ("Glob", {"pattern": "/tmp/claude-notes-*/*.json"}),
    ("Glob", {"path": "~", "pattern": "**/daemon.env"}),
    # review-1: glob lists
    ("Grep", {"path": "~", "glob": "projects/** .config/aipager/**", "pattern": "."}),
    ("Grep", {"path": "~", "glob": "projects,.config/aipager/**", "pattern": "."}),
    ("Grep", {"path": "/", "glob": "tmp/x/*,tmp/claude-policy-*", "pattern": "."}),
    ("Grep", {"path": "~", "glob": "!projects/**", "pattern": "."}),
    ("Glob", {"path": "~", "pattern": "!projects/**"}),
    # review-2: leading /, escapes, U+FEFF
    ("Grep", {"path": "~", "glob": "/.config/aipager/**", "pattern": "."}),
    ("Grep", {"path": "~", "glob": "\\.config/aipager/**", "pattern": "."}),
    ("Grep", {"path": "~", "glob": "projects/**\ufeff.config/aipager/**",
              "pattern": "."}),
    ("Glob", {"path": "~", "pattern": "\\.config/aipager/*"}),
    # review-3: a # comment, Glob's path-tail rewrite
    ("Grep", {"path": "~", "glob": "#x/y", "pattern": "TOKEN"}),
    ("Grep", {"path": "/tmp", "glob": "#x/y", "pattern": "."}),
    ("Glob", {"pattern": "#proj/**"}),
    ("Glob", {"path": "~/.config", "pattern": ".config/aipager/**"}),
    ("Glob", {"path": "/tmp", "pattern": "tmp/claude-policy-*"}),
    ("Glob", {"path": HOME, "pattern": os.path.basename(HOME) + "/.config/aipager/**"}),
    # the allow-list itself: roots outside the session, non-plain globs
    ("Grep", {"path": "{tmp}/work/other-project", "pattern": "x"}),
    ("Grep", {"path": "{proj}/..", "pattern": "x"}),
    ("Glob", {"path": "{tmp}/work", "pattern": "**/*.py"}),
    ("Grep", {"path": "{proj}", "glob": "/src/**", "pattern": "x"}),
    ("Grep", {"path": "{proj}", "glob": "src/../../other/**", "pattern": "x"}),
    ("Grep", {"path": "{proj}", "glob": "{{src,..}}/**", "pattern": "x"}),
    ("Grep", {"path": "{proj}", "glob": "~/x", "pattern": "x"}),
    ("Grep", {"path": "{proj}", "glob": "$HOME/x", "pattern": "x"}),
    ("Grep", {"path": "{proj}", "glob": "C:/x/**", "pattern": "x"}),
    ("Grep", {"path": "{proj}", "glob": "src/** !src/gen/**", "pattern": "x"}),
    ("Grep", {"path": "{proj}", "glob": "*.py #x", "pattern": "x"}),
    ("Grep", {"path": "{proj}", "glob": "src\\*.py", "pattern": "x"}),
    ("Grep", {"path": "{proj}", "glob": "*.py\ufeff/x/**", "pattern": "x"}),
    ("Grep", {"path": "{proj}", "glob": "*.py\u3000!x", "pattern": "x"}),
    ("Glob", {"path": "{proj}", "pattern": "/etc/*"}),
    ("Glob", {"path": "{proj}", "pattern": "../*"}),
    ("Grep", {"path": 7, "pattern": "x"}),
    ("Grep", {"path": "{proj}", "glob": ["*.py"], "pattern": "x"}),
    ("Glob", {"path": "{proj}", "pattern": 7}),
    # review 4: Claude Code trims the path first (JavaScript trim)
    ("Grep", {"path": " ~/.config", "glob": "aipager/**", "pattern": "TOKEN"}),
    ("Grep", {"path": "\t~/.config", "glob": "aipager/**", "pattern": "TOKEN"}),
    ("Grep", {"path": "\ufeff~/.config", "glob": "aipager/**", "pattern": "TOKEN"}),
    ("Glob", {"path": " /tmp", "pattern": "claude-policy-*"}),
    ("Grep", {"path": "~/.config ", "glob": "aipager/**", "pattern": "."}),
]


def _fmt(inp, tmp_path, project):
    return {k: v.format(tmp=tmp_path, proj=project) if isinstance(v, str) else v
            for k, v in inp.items()}


@pytest.mark.parametrize("tool,inp", DENIED_SEARCHES)
def test_restricted_searches_outside_the_allow_list_are_denied(
        tmp_path, project, tool, inp):
    _use_role(_builtin("user"))
    assert _decide(tmp_path, tool, _fmt(inp, tmp_path, project), cwd=project)


ALLOWED_SEARCHES = [
    ("Grep", {"path": "{proj}", "glob": "**/*.py", "pattern": "def "}),
    ("Grep", {"path": "{proj}", "pattern": "def "}),
    ("Grep", {"pattern": "def "}),                     # defaults to cwd
    ("Grep", {"path": "{proj}/src", "glob": "*.py", "pattern": "x"}),
    ("Grep", {"path": "src", "glob": "*.py", "pattern": "x"}),  # relative to cwd
    ("Grep", {"path": "{proj}", "glob": "*.py *.ts", "pattern": "x"}),
    ("Grep", {"path": "{proj}", "glob": "*.{{ts,tsx}}", "pattern": "x"}),
    ("Grep", {"path": "{proj}", "glob": "src/**,tests/**", "pattern": "x"}),
    ("Grep", {"path": "{proj}", "glob": "backup~", "pattern": "x"}),
    ("Grep", {"path": "{proj}/src/a.py", "pattern": "x"}),
    ("Glob", {"pattern": "**/*.py"}),
    ("Glob", {"path": "{proj}", "pattern": "src/**/*.ts"}),
    ("Glob", {"path": "{scratch}", "pattern": "*.txt"}),
]


@pytest.mark.parametrize("tool,inp", ALLOWED_SEARCHES)
def test_ordinary_project_searches_stay_allowed(tmp_path, project, tool, inp):
    _use_role(_builtin("user"))
    (project / "src" / "a.py").write_text("x")
    inp = {k: v.format(proj=project, scratch=_scratch(project))
           for k, v in inp.items()}
    assert _decide(tmp_path, tool, inp, cwd=project) is None


def test_a_session_folder_holding_a_protected_path_cannot_be_searched_whole(
        tmp_path, project):
    """A session started in $HOME: its folder contains ~/.config/aipager."""
    _use_role(_builtin("user"))
    assert _decide(tmp_path, "Grep", {"path": HOME, "glob": ".config/aipager/**",
                                      "pattern": "TOKEN"}, cwd=HOME)
    assert _decide(tmp_path, "Grep", {"pattern": "TOKEN"}, cwd=HOME)
    assert _decide(tmp_path, "Grep", {"path": f"{HOME}/.config/aipager",
                                      "pattern": "TOKEN"}, cwd=HOME)


def test_a_protected_folder_inside_the_session_folder_is_denied(tmp_path):
    """A session started in /tmp: aipager's notes dirs sit in its folder."""
    _use_role(_builtin("user"))
    assert _decide(tmp_path, "Grep", {"path": "/tmp/claude-notes-other",
                                      "pattern": "x"}, cwd="/tmp")
    assert _decide(tmp_path, "Glob", {"path": "/tmp/claude-notes-other/sub",
                                      "pattern": "*.json"}, cwd="/tmp")


def test_a_symlinked_root_that_leads_out_of_the_project_is_denied(tmp_path, project):
    _use_role(_builtin("user"))
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (project / "link").symlink_to(outside)
    assert _decide(tmp_path, "Grep", {"path": str(project / "link"),
                                      "pattern": "x"}, cwd=project)


def test_an_operator_unanchored_rule_denies_restricted_searches(tmp_path, project):
    """``**/*.pem`` can sit anywhere in the project: no search is safe."""
    snap = ps.resolve_snapshot(_builtin("user"), None, None)
    snap["deny_paths_no_access"].append("**/*.pem")
    ps.write_merged_snapshot(SESSION, snap)
    assert _decide(tmp_path, "Grep", {"path": str(project), "glob": "*.py",
                                      "pattern": "x"}, cwd=project)
    assert _decide(tmp_path, "Read", {"file_path": str(project / "a.py")},
                   cwd=project) is None


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_owner_and_admin_searches_are_not_allow_listed(tmp_path, project, role):
    _use_role(_builtin(role))
    assert _decide(tmp_path, "Grep", {"path": str(tmp_path), "glob": "!x",
                                      "pattern": "x"}, cwd=project) is None


def test_admin_searches_keep_the_start_folder_check(tmp_path, project):
    _use_role(_builtin("admin"))
    assert _decide(tmp_path, "Grep", {"path": "~/.config/aipager",
                                      "pattern": "TOKEN"}, cwd=project)


# ===========================================================================
# Fail closed (2026-09-27): an exception inside the PreToolUse decision
# used to ALLOW the call (notify_hook: "enforcement error (allowing)").
# ===========================================================================

@pytest.mark.parametrize("tool,inp", [
    ("Read", {"file_path": "~\x00a"}),
    ("Write", {"file_path": "~\x00a"}),
    ("Grep", {"path": "~\x00a", "pattern": "x"}),
    ("Grep", {"glob": "~\x00/x/**", "pattern": "x"}),
    ("Glob", {"pattern": "~\x00/x"}),
])
def test_a_nul_byte_denies_instead_of_crashing(tmp_path, project, tool, inp):
    _use_role(_builtin("user"))
    block = _decide(tmp_path, tool, inp, cwd=project)
    assert block


def test_an_exception_in_the_decision_denies_a_restricted_turn(
        tmp_path, project, monkeypatch):
    _use_role(_builtin("user"))

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(safety, "path_violation", boom)
    block = _decide(tmp_path, "Read", {"file_path": str(project / "a")},
                    cwd=project)
    assert block and "could not be checked" in block["reason"]


def test_an_exception_in_the_decision_still_allows_the_owner(
        tmp_path, project, monkeypatch):
    _use_role(_builtin("owner"))
    monkeypatch.setattr(enforce, "_origin_from_transcript",
                        lambda p, *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _decide(tmp_path, "Bash", {"command": "ls"}, cwd=project) is None


def test_an_exception_with_an_unreadable_snapshot_denies(
        tmp_path, project, monkeypatch):
    _use_role(_builtin("owner"))
    monkeypatch.setattr(enforce, "_origin_from_transcript",
                        lambda p, *a: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(enforce, "read_snapshot",
                        lambda s: (_ for _ in ()).throw(OSError("gone")))
    assert _decide(tmp_path, "Bash", {"command": "ls"}, cwd=project)


def _hook(monkeypatch, tmp_path, payload):
    import io
    import sys
    from aipager.dtach import notify_hook
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    notify_hook._run(SESSION, [b""])


def _pre(tmp_path, project, tool="Read", tool_input=None):
    return {"hook_event_name": "PreToolUse", "session_id": CLAUDE_SID,
            "cwd": str(project), "tool_name": tool,
            "tool_input": tool_input or {"file_path": str(project / "a")},
            "transcript_path": _transcript(tmp_path)}


def test_the_hook_denies_when_the_enforcer_crashes(tmp_path, project, monkeypatch,
                                                   capsys):
    _use_role(_builtin("user"))
    monkeypatch.setattr(enforce, "decide",
                        lambda d: (_ for _ in ()).throw(RuntimeError("boom")))
    _hook(monkeypatch, tmp_path, _pre(tmp_path, project))
    out = json.loads(capsys.readouterr().out.strip().splitlines()[0])
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_the_hook_still_allows_an_owner_when_the_enforcer_crashes(
        tmp_path, project, monkeypatch, capsys):
    _use_role(_builtin("owner"))
    monkeypatch.setattr(enforce, "decide",
                        lambda d: (_ for _ in ()).throw(RuntimeError("boom")))
    _hook(monkeypatch, tmp_path, _pre(tmp_path, project))
    assert "deny" not in capsys.readouterr().out


def test_the_hook_denies_a_nul_path_end_to_end(tmp_path, project, monkeypatch,
                                               capsys):
    _use_role(_builtin("user"))
    _hook(monkeypatch, tmp_path, _pre(tmp_path, project, "Read",
                                      {"file_path": "~\x00a"}))
    assert '"deny"' in capsys.readouterr().out


# ===========================================================================
# The floor (2026-09-27): the answer for a turn aipager cannot attribute
# must be at least as strict as the ``user`` role.
# ===========================================================================

def test_the_floor_is_no_looser_than_the_user_role():
    user = ps.resolve_snapshot(_builtin("user"), None, None)
    floor = ps.FLOOR_SNAPSHOT
    assert set(user["deny_tools"]) <= set(floor["deny_tools"])
    assert floor["confine_writes"] is True and floor["bypass_safety"] is False
    assert set(user["deny_paths_no_access"]) <= set(floor["deny_paths_no_access"])


@pytest.mark.parametrize("tool", ["Bash", "PowerShell", "Monitor", "SendMessage"])
def test_an_unattributable_turn_cannot_run_code(tmp_path, project, tool):
    # no snapshot at all, and the floor written by an unmatched prompt
    assert _decide(tmp_path, tool, {"command": "ls"}, cwd=project)
    ps.write_merged_snapshot(SESSION, ps.merge_snapshots([]))
    assert _decide(tmp_path, tool, {"command": "ls"}, cwd=project)


def test_a_sender_with_no_role_gets_the_floor_tools(tmp_path, project):
    ps.write_merged_snapshot(SESSION, ps.resolve_snapshot(None, None, None))
    assert _decide(tmp_path, "Bash", {"command": "ls"}, cwd=project)


def test_an_expired_note_leaves_the_turn_on_the_floor(tmp_path, project):
    from aipager.dtach import notify_hook
    body = "[via Telegram · @bob · role:user]\nrun ls"
    path = ps.write_note(SESSION, _builtin("owner"), None, None, msg_id=1,
                         chat_id=1, sender_key=(1, 1), body=body, raw_text="run ls")
    note = json.loads(path.read_text())
    note["queued_at"] = 0.0          # older than the 24h TTL
    path.write_text(json.dumps(note))
    notify_hook._match_and_promote(SESSION, body)
    assert ps.read_snapshot(SESSION)["bypass_safety"] is False
    assert _decide(tmp_path, "Bash", {"command": "ls"}, cwd=project)


def test_an_owner_note_still_gets_bash(tmp_path, project):
    from aipager.dtach import notify_hook
    body = "[via Telegram · @aly · role:owner]\nrun ls"
    ps.write_note(SESSION, _builtin("owner"), None, None, msg_id=1, chat_id=1,
                  sender_key=(1, 1), body=body, raw_text="run ls")
    notify_hook._match_and_promote(SESSION, body)
    assert _decide(tmp_path, "Bash", {"command": "ls"}, cwd=project) is None


def test_the_pickup_datagram_carries_the_sender_but_no_permission():
    from aipager.dtach import notify_hook
    note = ps.resolve_snapshot(_builtin("owner"), None, None)
    note.update(msg_id=1, chat_id=-100, raw_text="hi", sender_key=[-100, 42])
    wire = notify_hook._note_wire(note)
    assert wire["sender_key"] == [-100, 42]
    assert "bypass_safety" not in wire and "deny_tools" not in wire


def test_the_author_of_last_prompt_is_recorded_from_its_note():
    """Only ``author_user_id`` names the author (review rev-iter4-002):
    ``sender_key`` may hold the last driver as a fallback."""
    assert ps.note_driver_id({"author_user_id": 42, "sender_key": [-100, 42]}) == 42
    for bad in ({"sender_key": [-100, 42]},                 # fallback only
                {"author_user_id": None, "sender_key": [-100, 42]},
                {"author_user_id": 0}, {"author_user_id": True},
                {"author_user_id": "42"}, {}, None):
        assert ps.note_driver_id(bad) is None


def test_write_note_records_no_author_when_none_is_given(tmp_path, monkeypatch):
    """A note written with author_user_id=None has no author even though
    sender_key carries a fallback id. (The real Retry path is covered by
    test_retry_privilege_attribution.py::test_a_prompt_sent_with_no_sender_records_no_author.)"""
    monkeypatch.setattr(ps, "notes_dir", lambda name: tmp_path / "notes")
    # Retry sends with no explicit sender; sender_key carries the fallback.
    path = ps.write_note(SESSION, None, None, None, msg_id=1, chat_id=1,
                         sender_key=(1, 777), body="member text",
                         raw_text="member text", author_user_id=None)
    import json
    note = json.loads(path.read_text())
    assert note["sender_key"] == [1, 777]
    assert ps.note_driver_id(note) is None


# ===========================================================================
# R4 — the reply file: own session's, read-only
# ===========================================================================

def test_restricted_turn_reads_its_own_reply_file(tmp_path, project):
    _use_role(_builtin("user"))
    own = str(ps.reply_context_path(SESSION))
    assert _decide(tmp_path, "Read", {"file_path": own}, cwd=project) is None


@pytest.mark.parametrize("tool,path", [
    ("Read", "/tmp/claude-reply-claude-other.txt"),
    ("Read", "/tmp/claude-reply-claude-proj.txt.tmp"),
    ("Read", "/tmp/claude-reply-claude-pro.txt"),
    ("Write", "/tmp/claude-reply-claude-proj.txt"),
    ("Edit", "/tmp/claude-reply-claude-proj.txt"),
])
def test_other_reply_files_and_writes_stay_denied(tmp_path, project, tool, path):
    _use_role(_builtin("user"))
    assert _decide(tmp_path, tool, {"file_path": path}, cwd=project)


def test_reply_file_is_not_readable_without_a_session(tmp_path, project):
    _use_role(_builtin("user"))
    nameless = str(ps.reply_context_path(""))
    assert _decide(tmp_path, "Read", {"file_path": nameless}, cwd=project,
                   session="")


def test_the_reply_exception_is_for_reading_only(tmp_path, project):
    # an admin's writes are not confined, so only the protected path stops this
    _use_role(_builtin("admin"))
    own = str(ps.reply_context_path(SESSION))
    assert _decide(tmp_path, "Write", {"file_path": own}, cwd=project)
    assert _decide(tmp_path, "Read", {"file_path": own}, cwd=project) is None


def test_reply_to_old_message_flow_works_for_a_restricted_sender(tmp_path, project):
    """End to end: the text ``session_ops`` hands Claude names the file
    by ``reply_context_path``; the Read of exactly that path passes."""
    from aipager.bot import session_ops
    _use_role(_builtin("user"))
    path = ps.reply_context_path(SESSION)
    text = session_ops._whole_message_context(
        "an excerpt", None, None, with_file=True, file_path=path)
    named = text.split("Full text: ", 1)[1].split(" (read only", 1)[0]
    assert _decide(tmp_path, "Read", {"file_path": named}, cwd=project) is None


# ===========================================================================
# R5 (8.53) — macOS: /tmp is /private/tmp and the disk ignores case
# ===========================================================================

@pytest.fixture
def macos(monkeypatch):
    real = os.path.realpath

    def fake_realpath(p, *a, **k):
        p = str(p)
        low = p.lower()
        if low == "/tmp" or low.startswith("/tmp/"):
            p = "/private/tmp" + p[4:]
        return real(p, *a, **k) if not p.startswith("/private/tmp") else p

    monkeypatch.setattr(safety.os.path, "realpath", fake_realpath)
    monkeypatch.setattr(safety.sys, "platform", "darwin")


@pytest.mark.parametrize("path", [
    "/private/tmp/claude-policy-x.json",
    "/TMP/claude-policy-x.json",
    "/tmp/CLAUDE-POLICY-x.json",
    "/Private/Tmp/Claude-Policy-x.json",
])
@pytest.mark.parametrize("tool", ["Write", "Read"])
def test_macos_spellings_of_the_control_files_are_protected(macos, tool, path):
    NA = safety.DENY_PATHS_NO_ACCESS
    assert safety.path_violation(tool, {"file_path": path}, NA, ())


def test_macos_case_folds_home_paths_too(macos):
    assert safety.path_violation(
        "Read", {"file_path": f"{HOME}/.CONFIG/AIPAGER/daemon.env"},
        safety.DENY_PATHS_NO_ACCESS, ())


def test_linux_does_not_case_fold(monkeypatch):
    monkeypatch.setattr(safety.sys, "platform", "linux")
    assert safety.path_violation(
        "Read", {"file_path": "/tmp/CLAUDE-POLICY-x.json"},
        safety.DENY_PATHS_NO_ACCESS, ()) is None


def test_macos_write_confinement_folds_case(macos, tmp_path):
    root = str(tmp_path / "Proj")
    assert safety.path_violation(
        "Write", {"file_path": str(tmp_path / "PROJ" / "a.py")}, (), (),
        cwd=root, write_roots=(root,)) is None
    assert safety.path_violation(
        "Write", {"file_path": str(tmp_path / "PROJ" / ".GIT" / "config")}, (), (),
        cwd=root, write_roots=(root,))


def test_macos_search_roots_are_compared_folded(macos, tmp_path):
    root = str(tmp_path / "Proj")
    assert safety.path_violation(
        "Grep", {"path": str(tmp_path / "PROJ" / "src"), "pattern": "."}, (), (),
        cwd=root, write_roots=(root,)) is None
    assert safety.path_violation(
        "Grep", {"path": "/private/tmp", "glob": "claude-policy-*", "pattern": "."},
        safety.DENY_PATHS_NO_ACCESS, (), cwd=root, write_roots=(root,))


def test_realpath_of_the_target_is_computed_once_per_call(monkeypatch):
    calls = []
    real = os.path.realpath

    def counting(p, *a, **k):
        calls.append(str(p))
        return real(p, *a, **k)

    monkeypatch.setattr(safety.os.path, "realpath", counting)
    target = "/srv/some/file.txt"
    safety.path_violation("Read", {"file_path": target},
                          safety.DENY_PATHS_NO_ACCESS, ())
    assert calls.count(target) == 1


# ===========================================================================
# ~name is not a home folder to Claude Code (review 2026-09-27)
# ===========================================================================
# Claude Code expands only "~" and "~/" in a tool's path; "~root/…" is a
# relative name under the cwd. Python's expanduser turned it into /root, so
# from a project directly under $HOME, "~root/../../.config/aipager/…" was
# checked as /.config/aipager/… while Claude Code read $HOME/.config/aipager/….

TILDE_NAME_ESCAPES = [
    "~root/../../.config/aipager/config.yaml",
    "~root/../../.config/aipager/daemon.env",
    "~root/../../.claude/.credentials.json",
    "~nobody/../../.config/aipager/config.yaml",
    " ~root/../../.config/aipager/config.yaml",
]


@pytest.mark.parametrize("path", TILDE_NAME_ESCAPES)
@pytest.mark.parametrize("tool,key", [("Read", "file_path"), ("LSP", "filePath")])
def test_tilde_name_cannot_escape_to_protected_files(tmp_path, tool, key, path):
    _use_role(_builtin("user"))
    tool_input = {key: path}
    if tool == "LSP":
        tool_input["operation"] = "documentSymbol"
    assert _decide(tmp_path, tool, tool_input, cwd=os.path.join(HOME, "proj"))


def test_tilde_name_is_read_the_way_claude_code_reads_it():
    """Only a bare ~ and a leading ~/ expand; ~name stays literal, so it
    resolves under the cwd like any relative name."""
    cwd = os.path.join(HOME, "proj")
    assert safety._norm("~root/x", cwd) == os.path.join(cwd, "~root", "x")
    assert safety._norm("~", cwd) == HOME
    assert safety._norm("~/.config", cwd) == os.path.join(HOME, ".config")
    assert safety._norm("~/", cwd) == HOME


def test_plain_home_spellings_are_still_protected(tmp_path):
    _use_role(_builtin("user"))
    for path in ("~/.config/aipager/config.yaml", "../.config/aipager/config.yaml"):
        assert _decide(tmp_path, "Read", {"file_path": path},
                       cwd=os.path.join(HOME, "proj"))


# ~// keeps the home folder (Node's path.join), review 2026-09-27: a first
# version of the ~name fix used os.path.join, which drops home when the
# rest starts with "/", so "~//.config/aipager/x" was checked as
# "/.config/aipager/x" and allowed.

@pytest.mark.parametrize("path", [
    "~//.config/aipager/config.yaml",
    "~///.config/aipager/config.yaml",
    "~//.claude/.credentials.json",
])
@pytest.mark.parametrize("tool,key", [("Read", "file_path"), ("LSP", "filePath")])
def test_double_slash_after_tilde_keeps_home(tmp_path, tool, key, path):
    _use_role(_builtin("user"))
    tool_input = {key: path}
    if tool == "LSP":
        tool_input["operation"] = "documentSymbol"
    assert _decide(tmp_path, tool, tool_input, cwd=os.path.join(HOME, "proj"))


def test_double_slash_search_root_is_protected_for_an_admin(tmp_path):
    _use_role(_builtin("admin"))
    assert _decide(tmp_path, "Grep", {"path": "~//.config/aipager", "pattern": "TOKEN"},
                   cwd=os.path.join(HOME, "proj"))


def test_double_slash_write_lands_outside_the_project(tmp_path):
    """Claude Code writes ~//<cwd>/x to <home>/<cwd>/x, which is outside
    the project, so confinement must refuse it."""
    _use_role(_builtin("user"))
    cwd = os.path.join(HOME, "proj")
    assert _decide(tmp_path, "Write", {"file_path": "~/" + cwd + "/x"}, cwd=cwd)


def test_tool_paths_keep_their_exact_code_points_like_claude_code():
    """Claude Code does not Unicode-normalise a tool's path (its expandPath
    wrapper is the identity), so neither may the check: a normalised path
    names a different file than the one the tool opens (review 3,
    2026-09-27)."""
    cwd = os.path.join(HOME, "proj")
    assert safety._norm("~//.config", cwd) == os.path.join(HOME, ".config")
    assert safety._norm("\u212a", cwd) == os.path.join(cwd, "\u212a")      # Kelvin sign kept
    assert safety._norm("e\u0301", cwd) == os.path.join(cwd, "e\u0301")   # NFD kept


def test_a_nfd_protected_folder_is_still_protected(tmp_path):
    """review 3 P1: an operator rule naming an NFD folder must match the NFD
    path the tool opens."""
    base = tmp_path / "Re\u0301sume\u0301"
    rule = str(base) + "/**"
    assert safety.path_violation("Read", {"file_path": str(base / "salary.txt")},
                                 (rule,), ())
