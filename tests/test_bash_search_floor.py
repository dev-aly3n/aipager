"""A non-owner's Bash search of the whole home folder or ``/`` is denied
(roadmap 8.98).

Claude Code 2.1.289 on the operator's box offers no Grep or Glob tool,
so Claude searches with ``grep``/``rg``/``find`` through Bash. The live
e2e run of 2026-10-06 saw an admin's ``grep -rn <canary> $HOME`` read the
planted ``~/.config/aipager/x``: the Bash floor only caught commands that
NAME aipager's folders, so the 8.61 rule that an admin's search never
reaches them did not hold for the most natural search on that version.

``safety.DENY_BASH_SEARCH_PATTERNS`` (part of ``DENY_BASH_PATTERNS``, the
floor every non-owner gets) now denies a recursive search or listing
whose target is the home folder, ``/home``, ``/Users/<user>``, ``/root``,
``/``, or ``~/.config`` / ``~/.local``. Best effort, like every Bash
pattern: what a regex cannot see is pinned below as NOT caught, so a
change that starts (or stops) catching it is a visible decision.
"""

from __future__ import annotations

import json
import re
import time

import pytest

from aipager import policy, safety
from aipager import policy_snapshot as ps
from aipager.dtach import enforce
from aipager.safety import (
    BASH_REASON,
    BASH_SEARCH_REASON,
    DENY_BASH_PATTERNS,
    DENY_BASH_SEARCH_PATTERNS,
    LIST_LS_RECURSIVE,
    LIST_FILES,
    LIST_TREE_DU,
    SEARCH_FIND,
    SEARCH_GREP_RECURSIVE,
    SEARCH_TEXT_FIRST,
    bash_violation,
)

# ---- the patterns, one at a time -------------------------------------------

# Commands each pattern must catch, tested against that pattern alone
# (so a broken pattern fails here even when another would cover it).
# Folders: every home spelling, quoting, trailing slashes, pipes, chains,
# wrappers and line continuations.
TEXT_FIRST_CATCHES = [
    "rg TOKEN ~",
    "rg TOKEN ~/",
    "rg TOKEN ~/.",
    "rg TOKEN ~/*",
    "rg TOKEN $HOME",
    "rg TOKEN ${HOME}",
    'rg TOKEN "$HOME"',
    'rg TOKEN "$HOME"/',
    'rg TOKEN "$HOME/"',
    'rg TOKEN "${HOME}"',
    'rg TOKEN "$HOME/*"',
    'rg TOKEN "$HOME"/*',
    'rg TOKEN "$HOME/."',
    'rg TOKEN "$HOME"/.',
    "rg TOKEN '/home/aly/'",
    "rg TOKEN /",
    "rg TOKEN '/'",
    "rg TOKEN /home",
    "rg TOKEN /home/",
    "rg TOKEN /home/aly",
    "rg TOKEN /home/aly/",
    "rg TOKEN '/home/aly'",
    "rg TOKEN /home/$USER",
    'rg TOKEN "/home/$USER"',
    "rg TOKEN /home/${USER}/",
    "rg TOKEN /Users",
    "rg TOKEN /Users/bob",
    "rg TOKEN /Users/$LOGNAME",
    "rg TOKEN /root",
    "rg TOKEN ~aly",
    "rg TOKEN ~/.config",
    "rg TOKEN ~/.config/",
    'rg TOKEN "$HOME"/.config',
    'rg TOKEN "$HOME"/.local/share',
    "rg TOKEN $HOME/.local",
    "rg TOKEN ~/.local/share",
    "rg TOKEN /home/aly/.local/state",
    "rg -n --hidden TOKEN ~ | head -20",
    "rg TOKEN ~ 2>/dev/null && echo done",
    "rg TOKEN ~>out.txt",
    "cd /tmp && rg TOKEN ~",
    "echo start; rg TOKEN ~",
    "echo start\nrg TOKEN ~",
    "rg 'a|b' ~",
    "rg TOKEN src ~",
    "rg TOKEN ~ src",
    "(rg TOKEN ~)",
    "$(rg TOKEN ~)",
    "`rg TOKEN ~`",
    "{ rg TOKEN ~; }",
    'bash -c "rg TOKEN ~"',
    "sh -c 'rg TOKEN $HOME'",
    "bash -lc 'rg TOKEN /'",
    "/usr/bin/rg TOKEN /",
    "xargs -0 rg TOKEN ~",
    "env LC_ALL=C rg TOKEN ~",
    "timeout 30 rg TOKEN ~",
    "nice -n 5 rg TOKEN ~",
    "FOO=1 rg TOKEN ~",
    "LC_ALL=C RG_X='a b' rg TOKEN ~",
    "if rg -q TOKEN ~; then echo y; fi",
    "rg -n \\\n  TOKEN \\\n  ~",
    "rg -e TOKEN ~",
    "rg -e '/home/aly' /",
    "rg --regexp=TOKEN ~",
    "rg -f patterns.txt ~",
    "rg -e x -e y ~",
    "rg -fpatterns.txt ~",
    "rg TOKEN ~\\\n  --hidden",
    "rg TOKEN 2>/dev/null ~",
    "rg TOKEN </dev/null ~",
    "rg -t py TOKEN ~",
    "rgrep TOKEN ~",
    "ag TOKEN ~",
    "ack TOKEN ~",
    "ack-grep TOKEN ~",
    "fd aipager ~",
    "fd . /",
    "fdfind aipager /",
]

GREP_CATCHES = [
    "grep -rn TOKEN ~",
    "grep -rn TOKEN $HOME",
    'grep -rn TOKEN "$HOME"',
    "grep -r TOKEN ${HOME}/",
    "grep -R TOKEN /",
    "grep -nR TOKEN /home/aly",
    "grep -Ril TOKEN /root",
    "grep -C2r TOKEN ~",
    "grep --recursive TOKEN /home",
    "grep --dereference-recursive TOKEN ~",
    "grep -d recurse TOKEN ~",
    "grep --directories=recurse TOKEN ~",
    "grep --directories recurse TOKEN ~",
    "grep TOKEN ~ -r",
    "grep -rn TOKEN ~/.config",
    'grep -rn TOKEN "$HOME"/.config',
    "grep -rn TOKEN /Users/bob/",
    "grep -rn TOKEN /home/$USER",
    "egrep -r 'a|b' ~",
    "fgrep -r TOKEN ~",
    "grep -rE 'a|b' $HOME 2>/dev/null | head",
    "cd /tmp && grep -rn TOKEN ~ | head",
    "xargs grep -rl TOKEN /home",
    'bash -c "grep -r TOKEN ~"',
    "/bin/grep -rn TOKEN ~",
    "LC_ALL=C grep -rn TOKEN ~",
    "grep -rn -e TOKEN ~",
    "grep -rne TOKEN ~",
    "grep -rn --regexp=TOKEN ~",
    "grep -rn --regexp TOKEN ~",
    "grep -rnf pats.txt ~",
    "grep -rn -e x -f y ~",
    "grep -rfpats.txt ~",
    "grep -rn --file=pats.txt ~",
    'grep -rn --include="*.yaml" \\\n  TOKEN \\\n  "$HOME"',
]

FIND_CATCHES = [
    "find ~ -name '*.yaml'",
    "find / -type f -name aipager.yaml",
    "find $HOME/ -name x",
    'find "$HOME/.config" -name x',
    "find / -maxdepth 1",
    "find ~",
    "find -L ~ -name x",
    "find -H -O2 / -name x",
    "find -D stat ~ -name x",
    "find . ~ -name x",
    "find src /home -name x",
    "xargs -0 find /home",
    "A=1 B=2 find ~ -name x",
    "find ~ \\( -name a -o -name b \\)",
]

TREE_DU_CATCHES = [
    "tree ~",
    "tree -a -L 3 /home/aly",
    "du -ah ~",
    "du -sh ~/*",
    "du -a /",
    "du -sh ~",
]

LS_CATCHES = [
    "ls -R ~",
    "ls -laR /",
    "ls -R $HOME",
    "ls --recursive /home/aly",
    "ls -1R ~/.config",
]

FILES_CATCHES = [
    "rg --files ~",
    "rg --files /home/aly",
    "rg --hidden --files /",
    "ack -f ~",
    "ack -f $HOME",
    "ack -f /",
    "ack-grep -f /home/aly",
]

BY_PATTERN = {
    SEARCH_TEXT_FIRST: TEXT_FIRST_CATCHES,
    SEARCH_GREP_RECURSIVE: GREP_CATCHES,
    SEARCH_FIND: FIND_CATCHES,
    LIST_TREE_DU: TREE_DU_CATCHES,
    LIST_LS_RECURSIVE: LS_CATCHES,
    LIST_FILES: FILES_CATCHES,
}
ALL_CATCHES = [c for cases in BY_PATTERN.values() for c in cases]


def test_the_six_patterns_are_the_search_patterns():
    assert tuple(BY_PATTERN) == DENY_BASH_SEARCH_PATTERNS


_NAMES = {pat: name for name, pat in vars(safety).items()
          if isinstance(pat, str) and pat in BY_PATTERN}


@pytest.mark.parametrize("pat,cmd", [(p, c) for p, cs in BY_PATTERN.items()
                                     for c in cs],
                         ids=[f"{_NAMES[p]}:{c}" for p, cs in BY_PATTERN.items()
                              for c in cs])
def test_each_pattern_catches_its_own(pat, cmd):
    assert re.search(pat, cmd), cmd


def test_file_listings_are_caught_by_their_own_pattern_only():
    """``rg --files ~`` and ``ack -f ~`` have no text to find: only
    LIST_FILES sees them (rev-iter3-002: ``ack -f ~`` was let through)."""
    for cmd in FILES_CATCHES:
        assert [p for p in DENY_BASH_SEARCH_PATTERNS if re.search(p, cmd)] == [
            LIST_FILES], cmd


@pytest.mark.parametrize("cmd", ALL_CATCHES)
def test_the_floor_denies_with_the_search_reason(cmd):
    assert bash_violation(cmd, DENY_BASH_PATTERNS) == BASH_SEARCH_REASON


# ---- what stays allowed ----------------------------------------------------

ALLOWED = [
    # searches inside a project
    "grep -rn TOKEN ~/proj",
    "grep -rn TOKEN ~/proj/",
    "grep -rn TOKEN $HOME/proj",
    'grep -rn TOKEN "$HOME/proj"',
    "grep -rn TOKEN /home/aly/proj",
    "grep -rn TOKEN /Users/bob/code",
    "rg TOKEN src/",
    "rg TOKEN .",
    "rg TOKEN ./",
    "rg --files src",
    "find . -name y",
    "find ~/proj ~/other -name x",
    "find /home/aly/proj -name x",
    "fd x src",
    "tree src",
    "du -sh ~/proj",
    "ls -R src",
    "ls -R ~/proj",
    "rg TOKEN /tmp",
    "rg TOKEN /etc",
    # searching FOR a home path inside a project (rev-iter1-001)
    'grep -rn "/home/aly" src',
    'grep -rn "/home/aly/" src',
    "grep -rn '/home/aly' src",
    'grep -rn "/home" docs',
    'grep -rln "/root" .',
    "rg -n '/Users/' .",
    'grep -rn "$HOME" src',
    'grep -rnF "$HOME" .',
    "rg -n $HOME src",
    "grep -rn / src",
    "rg / src",
    "grep -rn -i ~ src",
    "find . -path '/home/*'",
    "find . -name '*' -path /home",
    "find src -newer ~ -name x",
    # the value of -e / -f / --regexp / --file is the text, not a folder
    'grep -rn -e "/home" src',
    "rg -e '/root' .",
    'grep -r --regexp "$HOME" src',
    "rg -f /root src",
    "grep -r --file /root src",
    # ... also a second one (rev-iter3-001)
    'grep -rn -e x -e "/home/aly" src',
    'grep -rn -e "/home" -e "/root" src',
    "rg -e '/home' -e '/root' src",
    "rg -e x --regexp '/root' .",
    # not recursive
    "grep TOKEN ~",
    "grep TOKEN ~/.bashrc",
    "grep -n TOKEN /etc/hosts",
    "grep -i -n TOKEN ~",
    "ls ~",
    "ls -la /",
    "ls -la ~/",
    "ls -r ~",
    "ls -lart ~",
    # other folders under the home folder's config, and look-alikes
    "grep -rn TOKEN ~/.config/gh",
    "grep -rn TOKEN /homework",
    "grep -rn TOKEN /rootfs",
    "grep -rn TOKEN /Usersx",
    "grep -rn TOKEN $HOMEDIR",
    "grep -rn TOKEN ${HOME_DIR}",
    # text the shell does not expand is what is searched FOR
    "rg -n '~' src",
    "rg TOKEN '~'",
    "rg TOKEN '~/'",
    "rg TOKEN '$HOME'",
    "grep -rn TOKEN '${HOME}'",
    "rg -n '$HOME' src",
    "grep -rn '~/' src",
    "grep -rn '/ home' src",
    'rg "a / b" src',
    "bash -c 'grep -r \"a / b\" src'",
    "find src -path '*/'",
    # a search tool's name that is not the command word
    "git log --find-renames ~",
    "git ls-tree -r HEAD",
    "cargo build --release",
    "./myfind ~",
    "scripts/find/run.sh ~",
    "tools/du/report ~",
    "dust ~",
    "treesize ~",
    "find.py ~",
    "echo grep -r x ~",
    "echo rg TOKEN ~",
    "man find /",
    "git grep -rn TOKEN /",
    # the recursive flag of another command in the chain
    "echo grep -r; ls ~",
    "cat ~/x | grep -r y",
    "grep x ~ | sort -r",
    "grep x ~ && ls -R src",
    # a quoted separator does not end the command early
    "grep -rE 'a|b' src",
    # a bare newline ends the command
    "rg TOKEN src\n~/bin/tool",
]


@pytest.mark.parametrize("cmd", ALLOWED)
def test_project_and_non_recursive_commands_stay_allowed(cmd):
    assert bash_violation(cmd, DENY_BASH_PATTERNS) is None


# ---- documented as NOT caught (best effort) ----------------------------------

NOT_CAUGHT = [
    # no folder, or ``.`` / ``..``, run from (or below) the home folder: a
    # regex cannot see the cwd
    "cd ~ && grep -r TOKEN .",
    "cd ~; rg TOKEN",
    "grep -r TOKEN",
    "grep -rn TOKEN ..",
    "rg TOKEN ../..",
    "find .. -name x",
    # the home folder through another variable or a glob
    "H=~; grep -r TOKEN $H",
    "grep -r TOKEN /hom?/aly",
    # a home folder that is not under /home, /Users or /root
    "grep -r TOKEN /var/lib/someuser",
    # a wrapper not listed (sudo is denied on its own at the start), an
    # escaped command name, a folder given by an option
    "echo x; sudo -u bob grep -r TOKEN ~",
    "\\grep -rn TOKEN ~",
    "fd --base-directory ~ TOKEN",
    # a glob or brace list over the home folder's hidden folders
    "grep -rn TOKEN ~/.[a-z]*",
    "grep -rn TOKEN ~/{.config,.local}",
    # a folder right after a word ending in -e or -f, taken for the text
    # those give grep and rg (ag's -f is --follow: rev-iter4-001)
    "ag TOKEN -f ~",
    "grep -rn TOKEN-e ~",
    "grep -rn x some-f ~",
]


@pytest.mark.parametrize("cmd", NOT_CAUGHT)
def test_known_gaps_are_not_caught(cmd):
    """Pinned: these get past the search patterns. docs/security.md says
    so."""
    assert not any(re.search(p, cmd) for p in DENY_BASH_SEARCH_PATTERNS), cmd


KNOWN_FALSE_POSITIVES = [
    # an option that takes a separate value before the text: the value
    # reads as the text, the text as a folder
    'rg -t py "$HOME" .',
    'grep -rn -A 3 "/home/aly" src',
    'grep -rn -m 5 "$HOME" src',
    'rg -C 2 "/root" .',
    "rg -g '*.py' \"$HOME\" src",
    'rg --type py "/home/aly" .',
    'grep -rn --include "*.py" "$HOME" .',
    # -e's value inside a cluster, or after two spaces
    'grep -rne "/home" src',
    'grep -rn -e  "/home" src',
    # a command quoted inside another command's text
    'echo "a; rg x ~"',
    'grep -rn "foo; grep -r x /" docs',
]


@pytest.mark.parametrize("cmd", KNOWN_FALSE_POSITIVES)
def test_known_false_positives(cmd):
    """Pinned, and named in docs/security.md and the CHANGELOG: the
    patterns do not know which options take a value, and a separator
    inside quotes may start a ``bash -c`` command."""
    assert bash_violation(cmd, DENY_BASH_PATTERNS) == BASH_SEARCH_REASON


# ---- the reason, the cost, the wiring ----------------------------------------

def test_reason_says_what_to_do_and_never_the_regex():
    assert BASH_SEARCH_REASON.startswith(BASH_REASON)
    assert "search inside a project folder instead" in BASH_SEARCH_REASON
    assert "\\" not in BASH_SEARCH_REASON and "(?" not in BASH_SEARCH_REASON


def test_other_patterns_keep_the_generic_reason():
    assert bash_violation("claude --version", DENY_BASH_PATTERNS) == BASH_REASON


def test_an_operator_copy_of_a_search_pattern_gets_the_hint_too():
    """A pattern is told apart by value: policy.yaml's own patterns are
    unioned in as strings."""
    assert bash_violation("rg x ~", (SEARCH_TEXT_FIRST,)) == BASH_SEARCH_REASON


def test_every_search_pattern_is_in_the_floor():
    for pat in DENY_BASH_SEARCH_PATTERNS:
        assert pat in DENY_BASH_PATTERNS
        assert pat in ps.FLOOR_SNAPSHOT["deny_bash_patterns"]
        re.compile(pat)


_SLOW = [
    "grep -r " + " " * 50_000 + "x",
    "grep -r " + "'" * 50_000,
    "grep -r " + '"a" ' * 15_000 + "x",
    "grep -rn " + '"a b" ' * 10_000 + "src",
    "grep -r " + "a" * 50_000 + " ~x",
    "grep -r " + "a)" * 25_000 + " src",
    "find " + "-a " * 20_000 + "x",
    "find " + ". " * 20_000,
    "find " + "a " * 20_000 + "-name",
    "rg" + " ~x" * 20_000,
    "rg " + "-a " * 20_000,
    "rg x " + "\\\n " * 20_000,
    "grep -" + "r" * 50_000,
    "ls " + "-a " * 20_000 + "-R x",
    "ls -R " + "a " * 20_000,
    ("grep -r x " + "/home/" + "a" * 20_000 + "/b ") * 3,
    # the same command word repeated (rev-iter1-003)
    "grep -r x " * 5_000,
    '"grep -r x ' * 4_000,
    "-c 'grep -r x " * 3_000,
    "; grep -r x" * 5_000,
    "xargs " * 10_000 + "grep -r x",
    "bash -c '" * 5_000 + "grep -r x",
    "(" * 20_000 + "grep -r x",
    "\n" * 20_000 + "grep -r x",
    "/" * 50_000 + "grep -r x",
    "{tree a " * 6_000,
    "(tree a " * 6_000,
    "}/du/du" * 6_000,
    "x/" * 20_000 + "grep -r x",
    "du " + "-" * 50_000,
    "LC_ALL=C " * 5_000 + "grep -r x",
    "A=" + "a" * 50_000 + " grep -r x ~",
    # commands starting inside quotes: quadratic, still well under a
    # second at 10 KB (rev-iter2-003; 40 KB takes about 3 s)
    "{ `bash -c 'grep -r bash -c '`" * 300,
]


@pytest.mark.parametrize("cmd", _SLOW, ids=range(len(_SLOW)))
def test_no_catastrophic_backtracking(cmd):
    start = time.monotonic()
    bash_violation(cmd, DENY_BASH_PATTERNS)
    assert time.monotonic() - start < 1.0


# ---- through the hook: every non-owner, owners bypass --------------------------

SESSION = "claude-proj"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path",
                        lambda n: tmp_path / f"{n}.policy.json")


def _decide(tmp_path, role_name, command):
    role = policy.load_policy(tmp_path / "none.yaml",
                              tmp_path / "none.d").get_role(role_name)
    assert role is not None
    ps.write_merged_snapshot(SESSION, ps.resolve_snapshot(role, None, None))
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "user", "message": {
        "content": f"[via Telegram · @bob · role:{role_name}]\nsearch"}}) + "\n")
    return enforce.decide({
        "hook_event_name": "PreToolUse", "session": SESSION,
        "session_id": "s-898", "cwd": str(tmp_path), "tool_name": "Bash",
        "tool_input": {"command": command}, "transcript_path": str(t),
    })


def test_admin_home_search_denied_with_the_hint(tmp_path):
    block = _decide(tmp_path, "admin", "grep -rn TOKEN $HOME")
    assert block == {"tool": "Bash", "reason": BASH_SEARCH_REASON}


def test_admin_project_search_allowed(tmp_path):
    assert _decide(tmp_path, "admin", "grep -rn TOKEN ~/proj") is None


def test_owner_bypasses(tmp_path):
    assert _decide(tmp_path, "owner", "grep -rn TOKEN $HOME") is None
    assert _decide(tmp_path, "owner", "find / -name aipager.yaml") is None


def test_the_floor_snapshot_carries_them():
    """The answer for a turn nobody can be held to (it has no Bash at
    all, but its pattern list is the floor's)."""
    assert safety.bash_violation(
        "find / -name x",
        tuple(ps.FLOOR_SNAPSHOT["deny_bash_patterns"])) == BASH_SEARCH_REASON
