"""design.md success criteria 15, 16 and 17: the safety floor keeps
``aipager setup`` (every form), ``aipager config``, ``aipager uninstall``,
killing aipager, importing aipager's own code and reaching
``~/.config/aipager`` away from every Telegram turn without
``bypass_safety``, while ordinary commands stay allowed.

Only the documented safety check surface is used
(entrypoints.md "Safety check surface"): ``safety.bash_violation`` with
``safety.DENY_BASH_PATTERNS``, ``policy_snapshot.resolve_snapshot`` per
role, ``policy_snapshot.floor_snapshot``, and one ``enforce.decide`` per
role kind, built like tests/test_policy_safety_enforced.py.

Dodges that still get through are NOT failing tests here (design.md
says the patterns are best effort); they are listed in the test report.
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock

import pytest

from aipager import policy, safety
from aipager import policy_snapshot as ps
from aipager.safety import DENY_BASH_PATTERNS, bash_violation

# ---- criterion 15: the design's block list ----

BLOCK = [
    "aipager setup group --chat-id -1001 --member 5:me:owner --force",
    "aipager setup --token-stdin --chat-id 5 --role owner --force < f",
    "aipager setup detect-group --json",
    "aipager setup --help",
    '"aipager" setup group',
    "'aipager' config",
    'aip""ager setup',
    "a\\ipager setup",
    "/usr/local/bin/aipager setup group --chat-id -1",
    "~/.local/bin/aipager setup",
    "uvx aipager setup",
    "python3 -m aipager.cli setup group",
    "aipager uninstall -y",
    '"aipager" uninstall',
    "pkill -f aipager",
    "killall aipager",
    "kill $(pgrep -f aipager)",
    "launchctl kickstart -k gui/501/com.aipager.daemon",
    'python3 -c "from aipager.wizard import scope_io"',
    'python3 -c "import aipager.scope"',
    "python3 - <<'EOF'\nfrom aipager import scope\nEOF",
    "python3 -c \"__import__('aipager.wizard.scope_io')\"",
    "python3 -c \"import importlib; importlib.import_module('aipager.scope')\"",
    "cat ~/.config/aipage?/aipager.yaml",
    "cat ~/.config/a*/aipager.yaml",
    "cat ~/.config/*/aipager.yaml",
    "cat ~/.config/[a]ipager/aipager.yaml",
    "cat ~/.config/{aipager,x}/aipager.yaml",
    "sed -i s/user/owner/ $HOME/.config/aip''ager/aipager.yaml",
    'cat "$HOME"/.config/"aipager"/aipager.yaml',
    "cd ~/.config && cat aipager/aipager.yaml",
    'cd "$HOME/.config"; cat aipager/policy.yaml',
    "pushd ~/.config",
    "cat ~/.local/share/aipage?/x",
    "cat ~/.config/aipage?/aipager.yaml | aipager setup --token-stdin "
    "--chat-id 5 --role owner --force",
]

# Further dodges found blocked by error guessing; pinned so they stay so.
BLOCK_EXTRA = [
    "aipager  setup group",
    "aipager\tsetup group",
    "aipager    config",
    "aipager setup=x",
    'aipager "setup" group',
    "aipager 'setup'",
    'aipager se""tup group',
    "aipager s\\etup",
    "env aipager setup group",
    "env -i aipager setup",
    "command aipager setup",
    "exec aipager setup",
    "nohup aipager setup &",
    "timeout 5 aipager setup",
    "xargs aipager setup < f",
    "bash -c 'aipager setup group'",
    'sh -c "aipager setup"',
    "echo 'aipager setup group' | sh",
    'eval "aipager setup group"',
    "eval aipager setup",
    "alias s='aipager setup'; s group",
    "pipx run aipager setup",
    "uvx --from aipager aipager setup",
    "uv run aipager setup",
    "~/.local/share/pipx/venvs/aipager/bin/aipager setup",
    "/home/alice/.local/pipx/venvs/aipager/bin/python -m aipager setup",
    "python3 -m aipager setup",
    "python3 -m aipager.cli.__main__ setup",
    "python3 -m 'aipager.cli' setup",
    'python -c "import aipager"',
    "python3 -c 'from aipager.cli import main; main()' setup",
    "python3 -c \"__import__ ('aipager')\"",
    'python3 -c "import  aipager"',
    'python3 -c "from  aipager.wizard import scope_io"',
    "aipager service stop",
    "aipager service uninstall",
    "aipager start",
    "pkill aipager",
    "killall -9 aipager",
    "kill -9 $(pgrep -f aipager)",
    "kill -USR1 $(pgrep -f aipager)",
    "systemctl --user stop aipager",
    "systemctl --user kill aipager.service",
    "systemctl --user kill 'aipager*'",
    "launchctl bootout gui/501/com.aipager.daemon",
    "cat ~/.config/*ager/aipager.yaml",
    "cat ~/.config/ai{p,}ager/aipager.yaml",
    "cat ~/.config/aip[a-z]ger/aipager.yaml",
    "cat ~/.config/aip?ger/aipager.yaml",
    "cat ~/.config/**/aipager.yaml",
    "cat ~/.config/../.config/aipager/aipager.yaml",
    "cat ~/./.config/aipager/aipager.yaml",
    "cat ~//.config/aipager/aipager.yaml",
    "cat /home/alice/.config/aipager/aipager.yaml",
    "cat /Users/alice/.config/aipager/aipager.yaml",
    "cat /root/.config/aipager/aipager.yaml",
    "cat ${HOME}/.config/aipager/aipager.yaml",
    'cat "${HOME}/.config/aipager/aipager.yaml"',
    "cat ~alice/.config/aipager/aipager.yaml",
    "cat ~/.config/aip\\ager/aipager.yaml",
    'cat ~/.config/"aip"ager/aipager.yaml',
    "cd ~/.config/ && cat aipager/aipager.yaml",
    "cd ~ && cat .config/aipager/aipager.yaml",
    "cd && cat .config/aipager/aipager.yaml",
    "cd $HOME && cat .config/aipager/aipager.yaml",
    "cd ~/.local && cat share/aipager/x",
    "cd ~/.local/share && cat aipager/x",
    'cd "$HOME/.config"',
    "cd ${HOME}/.config",
    "cd /home/alice/.config",
    "find ~ -name 'aipager.yaml' -exec cat {} \\;",
    "ln -s ~/.config/aipager x; cat x/aipager.yaml",
    "mv ~/.config/aipager /tmp/x",
    "python3 -c \"open('/home/alice/.config/aipager/aipager.yaml','a')"
    ".write('x')\"",
    "perl -pi -e 's/user/owner/' ~/.config/aipager/aipager.yaml",
    "vi ~/.config/aipager/aipager.yaml",
    "cat ~/.local/state/a*/x",
    "echo group --chat-id -1001 --member 5:me:owner --force | "
    "xargs aipager setup",
]


@pytest.mark.parametrize("cmd", BLOCK)
def test_design_block_list_is_blocked(cmd):
    assert bash_violation(cmd, DENY_BASH_PATTERNS) is not None


@pytest.mark.parametrize("cmd", BLOCK_EXTRA)
def test_extra_dodges_are_blocked(cmd):
    assert bash_violation(cmd, DENY_BASH_PATTERNS) is not None


def test_deny_reason_is_the_fixed_bash_reason():
    """The reason Claude reads stays the existing one (golden text)."""
    reasons = {bash_violation(c, DENY_BASH_PATTERNS) for c in BLOCK}
    assert reasons == {bash_violation("sudo ls", DENY_BASH_PATTERNS)}


# ---- criterion 16: the design's allow list ----

ALLOW = [
    "aipager doctor --json",
    "aipager status",
    "aipager status --json",
    "aipager logs -n 50",
    "aipager version",
    "aipager session ls",
    "aipager policy validate",
    "pip install aipager",
    "ls ~/.config/nvim",
    "ls ~/.config/*.conf",
    "cat ~/.config/git/config",
    "cd ~/.config/nvim",
    "cd ~/projects/app",
    'python3 -c "import json"',
    'python3 -c "from aipager_tools import x"',
    "python3 -m pytest -q",
    "echo aipager",
]

# Further commands that must stay allowed (the brief's list: searching
# aipager's code, aipager's read-only commands, ordinary ~/.config work).
ALLOW_EXTRA = [
    "grep -rn setup_group aipager/",
    "grep -rn put_group_scope aipager/ tests/",
    'rg "import aipager" tests/',
    'grep -rn "from aipager" aipager/',
    "git show HEAD:aipager/setup_cmd.py",
    "git log --oneline -- aipager/setup_group.py",
    "git diff main -- aipager/safety.py",
    "cat aipager/setup_cmd.py",
    "less aipager/wizard/scope_io.py",
    "pytest tests/test_setup_cmd.py",
    "python3 -m pytest tests/integration/setup-group -q",
    "ruff check aipager tests",
    "aipager doctor",
    "aipager logs",
    "aipager --help",
    "aipager status --help",
    "pip show aipager",
    "pipx list",
    "ls ~/.config",
    "ls ~/.config/",
    "ls -la ~/.config/nvim/",
    "cat ~/.config/starship.toml",
    "vim ~/.config/nvim/init.lua",
    "cp ~/.config/git/config /tmp/gitconfig",
    "cd ~/.config/git && git status",
    "ls ~/.local/share/nvim",
    "cat ~/.local/state/nvim/log",
    "cd ~/.local/share/nvim",
    "mkdir -p ~/.config/myapp",
    "ls ~/.config/n*",
    "ls ~/.config/*vim",
    "ls ~/.config/[n]vim",
    "kill %1",
    "kill 1234",
    "pkill -f pytest",
    "killall node",
    'python3 -c "import aipagerx"',
    'echo "setting up the group"',
    "cd ~/aipager-users/group-team",
    "ls ~/aipager-users/",
    "cat README.md | grep -i setup",
]


@pytest.mark.parametrize("cmd", ALLOW)
def test_design_allow_list_is_allowed(cmd):
    assert bash_violation(cmd, DENY_BASH_PATTERNS) is None


@pytest.mark.parametrize("cmd", ALLOW_EXTRA)
def test_extra_ordinary_commands_are_allowed(cmd):
    assert bash_violation(cmd, DENY_BASH_PATTERNS) is None


# ---- criterion 15/16: every existing entry keeps its verdict ----

def _existing_lists():
    from tests import test_policy_false_blocks as fb
    blocked = (fb.BLOCKED_BINARY + fb.BLOCKED_CONFIG + fb.BLOCKED_AIPAGER_TMP
               + fb.BLOCKED_ROOT_LOOKALIKES + fb.BLOCKED_JUDGMENT
               + fb.BLOCKED_FLAGS + fb.BLOCKED_OTHER
               + fb.BLOCKED_PROJECT_NAMES)
    allowed = fb.ALLOWED + fb.ALLOWED_PROJECT_NAMES
    return blocked, allowed


def test_every_existing_blocked_entry_stays_blocked():
    blocked, _ = _existing_lists()
    still_allowed = [c for c in blocked
                     if bash_violation(c, DENY_BASH_PATTERNS) is None]
    assert still_allowed == []


def test_every_existing_allowed_entry_stays_allowed():
    _, allowed = _existing_lists()
    now_blocked = [c for c in allowed
                   if bash_violation(c, DENY_BASH_PATTERNS) is not None]
    assert now_blocked == []


def test_operator_patterns_still_see_the_raw_command():
    """A policy.yaml pattern written against quotes keeps matching the
    command as typed (dequoting applies to built-in patterns only)."""
    custom = (r'"secret"',)
    assert bash_violation('echo "secret"', custom) is not None


# ---- criterion 17: per role ----

POLICY = """\
roles:
  dev:
    deny_tools: []
  boss:
    bypass_safety: true
"""
SETUP_CMD = "aipager setup group --chat-id -1001 --member 5:me:owner --force"
CONFIG_CMD = "aipager config"


@pytest.fixture
def pol(tmp_path):
    cfg = tmp_path / "pcfg"
    cfg.mkdir()
    (cfg / "policy.yaml").write_text(POLICY)
    return policy.load_policy(cfg / "policy.yaml", cfg / "policy.d")


def _denied(snap, cmd):
    if snap.get("bypass_safety"):
        return False
    if "Bash" in (snap.get("deny_tools") or []):
        return True
    return bash_violation(cmd, tuple(snap.get("deny_bash_patterns") or ())) \
        is not None


def _snap(pol, role):
    return ps.resolve_snapshot(pol.get_role(role), None, None, policy=pol)


@pytest.mark.parametrize("role", ["admin", "dev"])
@pytest.mark.parametrize("cmd", [SETUP_CMD, CONFIG_CMD,
                                 "cat ~/.config/a*/aipager.yaml"])
def test_bash_roles_are_denied(pol, role, cmd):
    assert _denied(_snap(pol, role), cmd) is True


@pytest.mark.parametrize("role", ["admin", "dev"])
def test_bash_roles_actually_have_bash(pol, role):
    """Precondition: the deny above comes from the patterns, not from a
    role without Bash."""
    assert "Bash" not in (_snap(pol, role).get("deny_tools") or [])


@pytest.mark.parametrize("role", ["admin", "dev"])
def test_bash_roles_keep_ordinary_commands(pol, role):
    assert _denied(_snap(pol, role), "aipager doctor --json") is False


@pytest.mark.parametrize("role", ["user", "read_only"])
def test_restricted_roles_have_no_bash(pol, role):
    assert "Bash" in (_snap(pol, role).get("deny_tools") or [])


@pytest.mark.parametrize("role", ["owner", "boss"])
def test_bypass_roles_are_allowed(pol, role):
    assert _denied(_snap(pol, role), SETUP_CMD) is False


def test_floor_snapshot_denies_setup():
    assert _denied(ps.floor_snapshot(), SETUP_CMD) is True


def test_floor_snapshot_with_policy_denies_setup(pol):
    assert _denied(ps.floor_snapshot(policy=pol), SETUP_CMD) is True


def test_floor_snapshot_is_not_bypass():
    assert ps.floor_snapshot().get("bypass_safety") is False


def test_floor_snapshot_carries_every_builtin_pattern():
    snap = ps.floor_snapshot()
    assert set(DENY_BASH_PATTERNS) <= set(snap["deny_bash_patterns"])


def test_policy_safety_section_cannot_remove_the_floor(tmp_path):
    """An operator's safety section adds patterns, never replaces the
    built-in ones."""
    cfg = tmp_path / "pc2"
    cfg.mkdir()
    (cfg / "policy.yaml").write_text(
        "safety:\n  deny_bash_patterns: ['\\bcurl\\b']\n")
    pol = policy.load_policy(cfg / "policy.yaml", cfg / "policy.d")
    snap = ps.resolve_snapshot(pol.get_role("admin"), None, None,
                               policy=pol)
    assert _denied(snap, SETUP_CMD) is True


# ---- criterion 17: end to end, one enforce.decide per role kind ----

GROUP_ID = -1001
S = "claude-api__g1001"
ALICE, BOB, ADA, DEV, BOSS = 1, 2, 4, 6, 7


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home" / "alice"
    (h / ".config" / "aipager").mkdir(parents=True)
    (h / ".config" / "aipager" / "aipager.yaml").write_text("bot_token: X\n")
    (h / "proj").mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("CLAUDE_CODE_TMPDIR", raising=False)
    monkeypatch.delenv("TMPDIR", raising=False)
    assert os.path.expanduser("~") == str(h)
    ps.snapshot_path(S).parent.mkdir(parents=True, exist_ok=True)
    return h


@pytest.fixture
def epol(home):
    cfg = home / ".config" / "aipager"
    (cfg / "policy.yaml").write_text(POLICY)
    return policy.load_policy(cfg / "policy.yaml", cfg / "policy.d")


@pytest.fixture
def typed(monkeypatch):
    from aipager.dtach import inject
    out: list = []

    async def _send(name, text, *a, **kw):
        out.append((name, text))
        return True
    monkeypatch.setattr(inject, "send_text_and_enter", _send)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    return out


@pytest.fixture
def gbot(mk_bot, epol):
    from aipager.scope import Member, Scope
    scope = Scope(chat_id=GROUP_ID, kind="group", label="team", members=(
        Member(id=ALICE, label="alice", role="owner"),
        Member(id=BOB, label="bob", role="user"),
        Member(id=ADA, label="ada", role="admin"),
        Member(id=DEV, label="dan", role="dev"),
        Member(id=BOSS, label="bea", role="boss"),
    ))
    bot = mk_bot(scopes=[scope])
    bot.policy = epol
    bot._app.bot.id = 999
    for name in ("_card_for_injected", "_react", "_maybe_update_bot_name",
                 "_update_bot_commands", "_send_busy_and_animate"):
        setattr(bot, name, AsyncMock())
    return bot


def _turn(bot, run_async, typed, tmp_path, uid) -> str:
    from aipager.dtach import notify_hook
    from aipager.state import Status
    sess = bot.registry.get_or_create(S)
    sess.label = "api"
    sess.scope_chat_id, sess.scope_kind = GROUP_ID, "group"
    sess.status = Status.IDLE
    before = len(typed)
    assert run_async(bot._inject_prompt(sess, "go", chat_id=GROUP_ID,
                                        driver_user_id=uid))
    assert len(typed) == before + 1
    body = typed[-1][1]
    ps.clear_turn_open(S)
    notify_hook._match_and_promote(S, body)
    t = tmp_path / f"t{uid}.jsonl"
    t.write_text(json.dumps({"type": "user", "message": {
        "role": "user", "content": body}}) + "\n", encoding="utf-8")
    return str(t)


def _decide(transcript, cmd, cwd):
    from aipager.dtach import enforce
    return enforce.decide({
        "hook_event_name": "PreToolUse", "session": S,
        "session_id": "5b1d7363-aaaa-bbbb-cccc-000000000123",
        "cwd": str(cwd), "tool_name": "Bash",
        "tool_input": {"command": cmd}, "transcript_path": transcript})


@pytest.mark.parametrize("uid", [ADA, DEV, BOB], ids=["admin", "custom",
                                                       "user"])
def test_non_owner_turn_running_setup_group_is_denied(
        gbot, typed, run_async, tmp_path, home, uid):
    t = _turn(gbot, run_async, typed, tmp_path, uid)
    assert _decide(t, SETUP_CMD, home / "proj") is not None


@pytest.mark.parametrize("uid", [ADA, DEV], ids=["admin", "custom"])
def test_non_owner_turn_running_config_is_denied(
        gbot, typed, run_async, tmp_path, home, uid):
    t = _turn(gbot, run_async, typed, tmp_path, uid)
    assert _decide(t, CONFIG_CMD, home / "proj") is not None


@pytest.mark.parametrize("uid", [ADA, DEV], ids=["admin", "custom"])
def test_non_owner_turn_editing_the_config_is_denied(
        gbot, typed, run_async, tmp_path, home, uid):
    t = _turn(gbot, run_async, typed, tmp_path, uid)
    cmd = "sed -i s/user/owner/ $HOME/.config/aip''ager/aipager.yaml"
    assert _decide(t, cmd, home / "proj") is not None


def test_admin_turn_deny_reason_is_the_bash_reason(
        gbot, typed, run_async, tmp_path, home):
    t = _turn(gbot, run_async, typed, tmp_path, ADA)
    assert _decide(t, SETUP_CMD, home / "proj")["reason"] == \
        safety.BASH_REASON


def test_admin_turn_keeps_ordinary_commands(
        gbot, typed, run_async, tmp_path, home):
    t = _turn(gbot, run_async, typed, tmp_path, ADA)
    assert _decide(t, "aipager doctor --json", home / "proj") is None


@pytest.mark.parametrize("uid", [ALICE, BOSS], ids=["owner", "bypass"])
def test_bypass_turn_may_run_setup_group(
        gbot, typed, run_async, tmp_path, home, uid):
    t = _turn(gbot, run_async, typed, tmp_path, uid)
    assert _decide(t, SETUP_CMD, home / "proj") is None
