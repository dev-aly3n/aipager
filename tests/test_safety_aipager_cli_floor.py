"""The safety floor keeps aipager's own CLI away from Telegram turns that
are not the owner's: `aipager setup` (any form: it grants roles),
`aipager config`, `aipager uninstall`, killing the daemon, Python that
imports aipager's writer, and the config folders reached through globs,
quotes or a `cd` into their parent. Best effort, like every Bash
pattern (docs/security.md): the real boundary for restricted roles is
having no shell.
"""

from __future__ import annotations

import json

import pytest

from aipager import policy, safety
from aipager import policy_snapshot as ps
from aipager.safety import DENY_BASH_PATTERNS, bash_violation
from aipager.scope import Member, Scope

SETUP_GROUP = "aipager setup group --chat-id -1001 --member 5:me:owner --force"

BLOCKED = [
    SETUP_GROUP,
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
    "python3 -m aipager.cli.__main__ setup group",
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
    'uv run python -c "from aipager import scope"',
    'uvx python -c "import aipager"',
    '.venv/bin/python3.12 -c "import aipager"',
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
    "cd ${HOME}/.local/share",
    "cd /home/alice/.local/state",
    "cat ~/.local/share/aipage?/x",
    "cat ~/.config/aipage?/aipager.yaml | aipager setup --token-stdin "
    "--chat-id 5 --role owner --force",
]

ALLOWED = [
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
    "ls ~/.config/nvim*",
    "cat ~/.config/git/config",
    "cd ~/.config/nvim",
    "cd ~/projects/app",
    'python3 -c "import json"',
    'python3 -c "from aipager_tools import x"',
    "python3 -m pytest -q",
    "echo aipager",
    # Searching aipager's code stays allowed: no interpreter runs it.
    'grep -rn "from aipager import" .',
    'rg "import aipager" aipager/',
    'git log -S "from aipager"',
    "grep -rn \"__import__('aipager\" .",
]


@pytest.mark.parametrize("cmd", BLOCKED)
def test_blocked(cmd):
    assert bash_violation(cmd, DENY_BASH_PATTERNS) is not None


@pytest.mark.parametrize("cmd", ALLOWED)
def test_allowed(cmd):
    assert bash_violation(cmd, DENY_BASH_PATTERNS) is None


def test_the_deny_reason_names_no_pattern():
    assert bash_violation(SETUP_GROUP, DENY_BASH_PATTERNS) == safety.BASH_REASON


# ----- each new pattern earns its place: a command only it blocks -----

ONLY_BECAUSE_OF = [
    (safety.BASH_AIPAGER_CLI, SETUP_GROUP),
    (safety.BASH_AIPAGER_CLI, "aipager uninstall -y"),
    (safety.BASH_AIPAGER_CLI, "python3 -m aipager.cli setup group"),
    (safety.BASH_KILL_AIPAGER, "pkill -f aipager"),
    (safety.BASH_KILL_AIPAGER, "killall aipager"),
    (safety.BASH_LAUNCHCTL_AIPAGER, "launchctl kickstart -k gui/501/com.aipager.daemon"),
    (safety.BASH_PYTHON_IMPORTS_AIPAGER, 'python3 -c "from aipager.wizard import scope_io"'),
    (safety.BASH_PYTHON_IMPORTS_AIPAGER, "python3 - <<'EOF'\nfrom aipager import scope\nEOF"),
    (safety.BASH_PYTHON_IMPORTS_AIPAGER,
     "python3 -c \"__import__('aipager.wizard.scope_io')\""),
    (safety.BASH_GLOB_AIPAGER_DIR, "cat ~/.config/aipage?/aipager.yaml"),
    (safety.BASH_GLOB_AIPAGER_DIR, "cat ~/.config/{aipager,x}/aipager.yaml"),
    (safety.BASH_GLOB_AIPAGER_DIR, "cat ~/.config/[a]ipager/aipager.yaml"),
    (safety.BASH_CD_CONFIG_PARENT, "cd ~/.config && cat aipager/aipager.yaml"),
    (safety.BASH_CD_CONFIG_PARENT, 'cd "$HOME/.config"; cat aipager/policy.yaml'),
]


@pytest.mark.parametrize("pat,cmd", ONLY_BECAUSE_OF)
def test_each_new_pattern_blocks_a_command_alone(pat, cmd):
    assert pat in DENY_BASH_PATTERNS
    without = tuple(p for p in DENY_BASH_PATTERNS if p != pat)
    assert bash_violation(cmd, without) is None
    assert bash_violation(cmd, DENY_BASH_PATTERNS) is not None


DEQUOTE_ONLY = [
    '"aipager" setup group',
    'aip""ager setup',
    "a\\ipager setup",
    "sed -i s/user/owner/ $HOME/.config/aip''ager/aipager.yaml",
    'cat "$HOME"/.config/"aipager"/aipager.yaml',
    '"aipager" uninstall',
]


@pytest.mark.parametrize("cmd", DEQUOTE_ONLY)
def test_the_dequoted_match_is_what_blocks_quoting_dodges(cmd, monkeypatch):
    assert bash_violation(cmd, DENY_BASH_PATTERNS) is not None
    monkeypatch.setattr(safety, "DEQUOTED_BASH_PATTERNS", ())
    assert bash_violation(cmd, DENY_BASH_PATTERNS) is None


def test_dequoting_never_reaches_the_claude_or_flag_patterns():
    assert safety.BASH_CONFIG_DIR in safety.DEQUOTED_BASH_PATTERNS
    for pat in (r"\bclaude\b", r"--resume(?![\w-])", *safety.DENY_BASH_SEARCH_PATTERNS):
        assert pat not in safety.DEQUOTED_BASH_PATTERNS
    # "c""laude" is not caught by dequoting (it is out of scope, like before)
    assert bash_violation('echo c""laude', (r"\bclaude\b",)) is None


def test_operator_patterns_still_see_the_raw_command():
    custom = (r"aip''ager",)
    assert bash_violation("echo aip''ager", custom) is not None


def test_dequote():
    assert safety.dequote("a\\i'p'\"a\"ger") == "aipager"



# ----- iteration 2: options, program globs, xargs, case, path spellings -----

#: (command, the pattern that alone blocks it). Each entry passes when
#: that pattern is taken out, so a mutant of the pattern piece it names
#: fails here.
REACH = [
    # options between the program and the subcommand
    ("aipager -v setup group --chat-id -1001 --member 5:me:owner --force",
     safety.BASH_AIPAGER_CLI),
    ("aipager --verbose setup --token-stdin --chat-id 5 --role owner --force < f",
     safety.BASH_AIPAGER_CLI),
    ("aipager -v config", safety.BASH_AIPAGER_CLI),
    ("aipager --verbose uninstall -y", safety.BASH_AIPAGER_CLI),
    ("aipager -v service stop", safety.BASH_AIPAGER_CLI),
    ("aipager --verbose start", safety.BASH_AIPAGER_CLI),
    ("aipager --log=debug setup", safety.BASH_AIPAGER_CLI),
    ("python3 -m aipager -v setup", safety.BASH_AIPAGER_CLI),
    ("python3 -m aipager.cli -v setup group", safety.BASH_AIPAGER_CLI),
    ("python3 -m aipager.cli.__main__ --verbose config", safety.BASH_AIPAGER_CLI),
    # the program name in any case (a macOS disk runs it)
    ("AIPAGER setup group", safety.BASH_AIPAGER_CLI),
    ("Aipager config", safety.BASH_AIPAGER_CLI),
    # ANSI-C quoted subcommand (the normaliser decodes it)
    ("aipager $'setup' group", safety.BASH_AIPAGER_CLI),
    # a glob in the program name
    ("aip*r setup", safety.BASH_AIPAGER_GLOB_PROGRAM),
    ("aipage? setup", safety.BASH_AIPAGER_GLOB_PROGRAM),
    ("AIP*R setup", safety.BASH_AIPAGER_GLOB_PROGRAM),
    ("/usr/local/bin/aip* config", safety.BASH_AIPAGER_GLOB_PROGRAM),
    ("~/.local/bin/aip*r setup group --chat-id -1001 --member 5:me:owner --force",
     safety.BASH_AIPAGER_GLOB_PROGRAM),
    ("~/.local/share/pipx/venvs/aip*/bin/aip* setup",
     safety.BASH_AIPAGER_GLOB_PROGRAM),
    ("env A=1 aip* setup", safety.BASH_AIPAGER_GLOB_PROGRAM),
    ("timeout 5 aip* -v setup", safety.BASH_AIPAGER_GLOB_PROGRAM),
    ("true; aipa?er start", safety.BASH_AIPAGER_GLOB_PROGRAM),
    ("true\naipa?er start", safety.BASH_AIPAGER_GLOB_PROGRAM),
    # xargs
    ("echo setup group --chat-id -1001 --member 5:me:owner --force | xargs aipager",
     safety.BASH_XARGS_AIPAGER),
    ("xargs -n 1 aipager", safety.BASH_XARGS_AIPAGER),
    ("xargs -0 ~/.local/bin/aipager", safety.BASH_XARGS_AIPAGER),
    ("xargs aip*", safety.BASH_XARGS_AIPAGER),
    # Python
    ("python3 -c 'import sys, aipager.wizard.scope_io as s'",
     safety.BASH_PYTHON_IMPORTS_AIPAGER),
    ("python3 -c 'import os,aipager.scope'", safety.BASH_PYTHON_IMPORTS_AIPAGER),
    ("python3 -c 'import os as o, aipager'", safety.BASH_PYTHON_IMPORTS_AIPAGER),
    ("python3 -c \"import runpy; runpy.run_module('aipager.cli')\" setup",
     safety.BASH_PYTHON_IMPORTS_AIPAGER),
    ("python3 -c \"import runpy; runpy.run_path('/x/venvs/aipager/lib/cli.py')\"",
     safety.BASH_PYTHON_IMPORTS_AIPAGER),
    # a glob in the hidden folder itself or in .local's second folder
    ("sed -i s/user/owner/ ~/.c*/aipager/aipager.yaml",
     safety.BASH_GLOB_HIDDEN_PARENT),
    ("cat ~/.co?fig/aipager/aipager.yaml", safety.BASH_GLOB_HIDDEN_PARENT),
    ("cat ~/.*/aipager/aipager.yaml", safety.BASH_GLOB_HIDDEN_PARENT),
    ("cat ~/.local/*/aipager/x", safety.BASH_GLOB_HIDDEN_PARENT),
    ("cat ~/.local/s*/aipager/x", safety.BASH_GLOB_HIDDEN_PARENT),
    ("cat ~/.l*/share/aipager/x", safety.BASH_GLOB_HIDDEN_PARENT),
    ("cat ~/.c*/aipage?/aipager.yaml", safety.BASH_GLOB_HIDDEN_PARENT),
    # cd with options, or in two steps
    ("cd -P ~/.config", safety.BASH_CD_CONFIG_PARENT),
    ("cd -- ~/.config", safety.BASH_CD_CONFIG_PARENT),
    ("cd -L $HOME/.local", safety.BASH_CD_CONFIG_PARENT),
    ("pushd -n ~/.config", safety.BASH_CD_CONFIG_PARENT),
    ("cd ~ && cd .config", safety.BASH_CD_HOME_THEN_PARENT),
    ("cd $HOME; cd .config", safety.BASH_CD_HOME_THEN_PARENT),
    ("cd && cd .local/share", safety.BASH_CD_HOME_THEN_PARENT),
    ("cd ~/ && cd -P .config/", safety.BASH_CD_HOME_THEN_PARENT),
    # the whole .config or .local folder handed over
    ("cp -r ~/.config /tmp/x", safety.BASH_COPY_CONFIG_PARENT),
    ("cp -r ~/.config/ /tmp/x", safety.BASH_COPY_CONFIG_PARENT),
    ("cp -r ~/.config/. /tmp/x", safety.BASH_COPY_CONFIG_PARENT),
    ("rsync -a ~/.config/ /tmp/x/", safety.BASH_COPY_CONFIG_PARENT),
    ("rsync -a /tmp/x/ ~/.config/", safety.BASH_COPY_CONFIG_PARENT),
    ("tar cf - ~/.config", safety.BASH_COPY_CONFIG_PARENT),
    ("tar xf x.tar -C ~/.config", safety.BASH_COPY_CONFIG_PARENT),
    ("zip -r x.zip ~/.local", safety.BASH_COPY_CONFIG_PARENT),
    ("ln -s ~/.config /tmp/c", safety.BASH_COPY_CONFIG_PARENT),
    ("mv ~/.local/share /tmp/s", safety.BASH_COPY_CONFIG_PARENT),
    ("scp -r ~/.config host:", safety.BASH_COPY_CONFIG_PARENT),
    ('cp -r "$HOME/.config" /tmp/x', safety.BASH_COPY_CONFIG_PARENT),
    # the folder names in any case
    ("cat ~/.Config/aipager/aipager.yaml", safety.BASH_CONFIG_DIR),
    ("cat ~/.local/share/AIPAGER/x", safety.BASH_SHARE_DIR),
    ("cat ~/.local/State/aipager/x", safety.BASH_STATE_DIR),
]

#: The nearest neighbours of REACH that must stay allowed.
NEIGHBOURS = [
    "aipager -v status",
    "aipager --version",
    "aipager doctor --json",
    "ls ~/.config",
    "ls ~/.local/share",
    "cp -r ~/.config/nvim /tmp/x",
    "cp ~/.config/git/config /tmp",
    "rsync -a ~/.config/nvim/ /tmp/x/",
    "tar cf x.tar ~/.config/nvim",
    "ln -s ~/.local/bin/x /tmp/y",
    "cd ~/.config/nvim",
    "cd project && cd .config",
    "cd .config",
    "xargs grep",
    "xargs grep aipager",
    "find . -name '*.py' | xargs grep -l aipager",
    "git add * setup.py",
    "ls a* setup/",
    "grep -rn 'aip*' .",
    "cat ~/.cache/x",
    "cat ~/.git*/aipager",
    "ls .*/x",
    'python3 -c "import os, json"',
    "python3 -c 'import runpy; runpy.run_module(\"pytest\")'",
    "grep -rn setup_group aipager/",
    'rg "import aipager" tests/',
    "git grep -n run_module aipager/",
    "git show HEAD:aipager/setup_cmd.py",
    "curl https://example.com//x",
    "echo $'hello\\n'",
]


@pytest.mark.parametrize("cmd,pat", REACH, ids=[c for c, _ in REACH])
def test_reach_is_blocked_by_its_own_pattern(cmd, pat):
    assert pat in DENY_BASH_PATTERNS and pat in safety.DEQUOTED_BASH_PATTERNS
    assert bash_violation(cmd, DENY_BASH_PATTERNS) is not None
    without = tuple(p for p in DENY_BASH_PATTERNS if p != pat)
    assert bash_violation(cmd, without) is None


@pytest.mark.parametrize("cmd", NEIGHBOURS)
def test_the_nearest_neighbours_stay_allowed(cmd):
    assert bash_violation(cmd, DENY_BASH_PATTERNS) is None


#: Spellings only the normaliser (:func:`safety.dequote`) turns into a
#: match: each fails when that step is gone.
NORMALISED = [
    "cat ~/.config//aipager/aipager.yaml",
    "sed -i s/user/owner/ ~/.config//aipager/aipager.yaml",
    "cat ~/.config/./aipager/aipager.yaml",
    "cat ~/.local/share/././aipager/x",
    "cat ~/.config/$'aipager'/aipager.yaml",
    r"cat ~/.config/$'\x61ipager'/aipager.yaml",
    r"cat ~/.config/$'\141ipager'/aipager.yaml",
    r"cat ~/.config/$'\u0061ipager'/aipager.yaml",
    r"cat ~/.config/$'\U00000061ipager'/aipager.yaml",
    "cat ~/.config/$\"aipager\"/aipager.yaml",
    "aipager $'setup' group",
    "aip``ager setup",
    "aip` `ager setup",
    "aip$()ager setup",
    'aip$("")ager setup',
]


@pytest.mark.parametrize("cmd", NORMALISED)
def test_the_normalised_copy_is_what_blocks(cmd, monkeypatch):
    assert bash_violation(cmd, DENY_BASH_PATTERNS) is not None
    monkeypatch.setattr(safety, "DEQUOTED_BASH_PATTERNS", ())
    assert bash_violation(cmd, DENY_BASH_PATTERNS) is None


@pytest.mark.parametrize("raw,out", [
    ("~/.config//aipager", "~/.config/aipager"),
    ("~/.config///./aipager", "~/.config/aipager"),
    ("a/./././b", "a/b"),
    ("$'aipager'", "aipager"),
    (r"$'\x61ipager'", "aipager"),
    (r"$'\141ipager'", "aipager"),
    (r"$'\u0061ipager'", "aipager"),
    (r"$'\U00000061ipager'", "aipager"),
    (r"$'a\tb'", "a\tb"),
    (r"$'it\'s'", "its"),
    ('$"aipager"', "aipager"),
    ("aip``ager", "aipager"),
    ("aip` `ager", "aipager"),
    ("aip$()ager", "aipager"),
    ("aip$( )ager", "aipager"),
    ("echo `date`", "echo `date`"),
    ("echo $(date)", "echo $(date)"),
    ("https://x", "https:/x"),
])
def test_dequote_normalises(raw, out):
    assert safety.dequote(raw) == out


def test_dequote_survives_a_bad_escape():
    assert safety.dequote(r"$'\U7fffffff'x") == "x"


#: Inputs built to make the new patterns backtrack: each must still be
#: decided well under a second (a hook that times out decides nothing).
_SLOW_NEW = [
    "aipager " + "-v " * 20_000 + "x",
    "aipager" + " -" * 20_000,
    "aipager.cli" + " -x" * 20_000 + " y",
    "xargs " + "-n 1 " * 10_000 + "x",
    "xargs" + " -a" * 20_000,
    "xargs " * 10_000 + "x",
    "cd " + "-P " * 20_000 + "x",
    "cd ~ " + "&& cd ~ " * 5_000,
    "cp " + "~/.config/x " * 5_000,
    "cp" + " ~" * 20_000,
    "$'" * 20_000,
    "$'\\" * 20_000,
    "`" * 50_000,
    "$(" * 20_000,
    "/./" * 20_000,
    "/" * 50_000,
    "a*" * 20_000 + " x",
    "aip*" + "?" * 50_000 + " x",
    ".c*/" * 20_000,
    " .[" * 20_000,
    "env " * 10_000 + "x",
    "\n" * 20_000 + "aip* x",
    "timeout 1 " * 10_000 + "x",
    "python3 " + "import a, " * 10_000,
    "python3 run_path(" + "a" * 50_000,
    ";" * 50_000 + "x",
    "~/.local/" + "s*/" * 10_000,
    "{a" * 20_000,
    "[a" * 20_000,
    "kill " * 10_000,
    "launchctl " * 10_000,
    "systemctl " * 10_000,
    "rm " * 10_000,
    "python " * 10_000,
    "python3 -c " + "run_path(" * 10_000,
    "aipager" + " -aipager" * 5_000,
    "aipager" + " -a/aipager" * 5_000,
    "(cp " * 6_000,
    "xargs -a" * 10_000,
    "$'\\x" * 20_000,
    "=." * 20_000,
    ":.[" * 15_000,
    "=.{" * 15_000,
    "'." * 20_000,
    "=.c*/[" * 8_000,
    ".config/[" * 6_000,
    "x; [" * 10_000,
    "aip*[" * 10_000 + " x",
    "xargs [" + "a" * 50_000,
    "cd" + "\n" * 20_000,
    "cd" + " \n" * 20_000,
    "cd ~ " + "|| " * 10_000,
]


@pytest.mark.parametrize("cmd", _SLOW_NEW, ids=range(len(_SLOW_NEW)))
def test_the_new_patterns_never_backtrack_badly(cmd):
    import time
    start = time.monotonic()
    bash_violation(cmd, DENY_BASH_PATTERNS)
    assert time.monotonic() - start < 1.0


# ----- per role -----

def _denied(snap: dict, cmd: str) -> bool:
    if snap.get("bypass_safety") is True:
        return False
    if "Bash" in (snap.get("deny_tools") or []):
        return True
    return bash_violation(cmd, tuple(snap.get("deny_bash_patterns") or ())) is not None


@pytest.fixture
def pol(tmp_path):
    f = tmp_path / "policy.yaml"
    f.write_text("roles:\n"
                 "  helper:\n    can_prompt: true\n"
                 "  boss:\n    bypass_safety: true\n")
    return policy.load_policy(f, tmp_path / "policy.d")


def _snap(pol, role_name):
    m = Member(id=5, label="x", role=role_name)
    s = Scope(chat_id=-1001, kind="group", label="team", members=(m,))
    return ps.resolve_snapshot(pol.get_role(role_name), s, m, policy=pol)


@pytest.mark.parametrize("role,denied", [
    ("owner", False), ("boss", False),
    ("admin", True), ("helper", True), ("user", True), ("read_only", True),
])
def test_per_role(pol, role, denied):
    assert _denied(_snap(pol, role), SETUP_GROUP) is denied


@pytest.mark.parametrize("role", ["admin", "helper"])
def test_roles_with_bash_are_stopped_by_the_pattern(pol, role):
    snap = _snap(pol, role)
    assert "Bash" not in (snap.get("deny_tools") or [])
    assert bash_violation(SETUP_GROUP, tuple(snap["deny_bash_patterns"])) is not None


@pytest.mark.parametrize("role", ["user", "read_only"])
def test_restricted_roles_have_no_bash_at_all(pol, role):
    assert "Bash" in _snap(pol, role)["deny_tools"]


def test_the_unattributed_floor(pol):
    snap = ps.floor_snapshot(pol)
    assert snap["bypass_safety"] is False
    assert _denied(snap, SETUP_GROUP)


def test_an_old_snapshot_gets_the_new_patterns_back():
    from aipager.dtach import enforce
    old = [p for p in DENY_BASH_PATTERNS if p != safety.BASH_AIPAGER_CLI]
    old.append(r"\baipager\s+(service|config|start)\b")
    rules = {"bypass_safety": False, "deny_bash_patterns": old,
             "deny_paths_no_access": [], "deny_paths_no_write": []}
    assert bash_violation(SETUP_GROUP, tuple(old)) is None
    merged = enforce._with_floor(rules)
    assert bash_violation(SETUP_GROUP, tuple(merged["deny_bash_patterns"])) is not None


# ----- end to end: an admin's Telegram turn -----

GROUP = -1001
S = "claude-api__g1001"
ALICE, ADA = 1, 4


@pytest.fixture(autouse=True)
def _snapshot_dir():
    ps.snapshot_path(S).parent.mkdir(parents=True, exist_ok=True)


def test_an_admin_telegram_turn_cannot_run_setup_group(
        mk_bot, run_async, tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from aipager.dtach import enforce, inject, notify_hook
    from aipager.state import Status

    typed: list = []

    async def _send(name, text, *a, **kw):
        typed.append(text)
        return True

    monkeypatch.setattr(inject, "send_text_and_enter", _send)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    scope = Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=ALICE, label="alice", role="owner"),
        Member(id=ADA, label="ada", role="admin")))
    bot = mk_bot(scopes=[scope])
    bot.policy = policy.load_policy(tmp_path / "none.yaml", tmp_path / "none.d")
    bot._app.bot.id = 999
    for name in ("_card_for_injected", "_react", "_maybe_update_bot_name",
                 "_update_bot_commands", "_send_busy_and_animate"):
        setattr(bot, name, AsyncMock())

    def _turn(uid):
        sess = bot.registry.get_or_create(S)
        sess.label = "api"
        sess.scope_chat_id, sess.scope_kind = GROUP, "group"
        sess.status = Status.IDLE
        assert run_async(bot._inject_prompt(sess, "go", chat_id=GROUP,
                                            driver_user_id=uid))
        body = typed[-1]
        ps.clear_turn_open(S)
        notify_hook._match_and_promote(S, body)
        p = tmp_path / f"t{uid}.jsonl"
        p.write_text(json.dumps({"type": "user", "message": {
            "role": "user", "content": body}}) + "\n", encoding="utf-8")
        return str(p)

    def _decide(transcript):
        return enforce.decide({
            "hook_event_name": "PreToolUse", "session": S,
            "session_id": "5b1d7363-aaaa-bbbb-cccc-000000000897",
            "cwd": str(tmp_path), "tool_name": "Bash",
            "tool_input": {"command": SETUP_GROUP},
            "transcript_path": transcript})

    res = _decide(_turn(ADA))
    assert res is not None and res["reason"] == safety.BASH_REASON
    # The control: the owner's turn runs it.
    assert _decide(_turn(ALICE)) is None
