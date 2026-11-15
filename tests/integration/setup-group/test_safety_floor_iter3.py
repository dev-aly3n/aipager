"""Iteration 3 of the safety floor checks (design.md success criteria 15
and 16, plus the routes fixes-3.md says are now closed).

Every closed route is pinned with its nearest neighbour that must stay
allowed. The floor must stay linear: 100 KB of adversarial padding in
front of a forbidden edit is still denied, and each check takes under
0.5 s. Spellings that still get through are pinned as strict xfails
(project convention for known gaps), so closing one turns its test
red until the xfail mark is removed; each is a finding in
test-report-3.json.

Only the documented surface is used: ``safety.bash_violation`` with
``safety.DENY_BASH_PATTERNS`` (entrypoints.md "Safety check surface").
Backslashes are built at run time with ``B`` so no tool on the way into
this file can decode them.
"""

from __future__ import annotations

import time

import pytest

from aipager.safety import DENY_BASH_PATTERNS, bash_violation

B = chr(92)  # a backslash
TAIL = "; sed -i s/a/b/ ~/.config/aipager/aipager.yaml"


def _blocked(cmd: str) -> bool:
    return bash_violation(cmd, DENY_BASH_PATTERNS) is not None


def ansi(body: str) -> str:
    """``$'...'`` with ``\\`` written as ``%``: ansi('%x61') is $'\\x61'."""
    return "$'" + body.replace("%", B) + "'"


def _params(rows):
    return pytest.mark.parametrize("cmd", [r[1] for r in rows],
                                   ids=[r[0] for r in rows])


# ---- routes fixes-3.md says are closed: (id, dodge, allowed neighbour) ----

CLOSED = [
    # item 2: brace and bracket groups holding "." and friends
    ("brace-dot", "sed -i s/a/b/ ~/.config/{aipager,a.b}/x",
     "sed -i s/a/b/ ~/.config/nvim/init.lua"),
    ("bracket-not-dot", "sed -i s/a/b/ ~/.config/[!.]ipager/x",
     "ls ~/.config/*.conf"),
    ("brace-inner-dot", "sed -i s/a/b/ ~/.config/a{i,.}pager/x",
     "ls ~/.config/nvim*"),
    ("brace-eq", "sed -i s/a/b/ ~/.config/{aipager,x=y}/x",
     "cat ~/.config/a.b/x"),
    ("brace-colon", "sed -i s/a/b/ ~/.config/{aipager,a:b}/x",
     "cat ~/.config/apager/x"),
    ("brace-dotdot", "sed -i s/a/b/ ~/.config/{aipager,..}/x",
     "ls ~/.config/aipager2"),
    ("brace-empty-dot", "sed -i s/a/b/ ~/.config/{,.}aipager/x",
     "cat ~/.config/git/config"),
    ("bracket-bracket", "sed -i s/a/b/ ~/.config/[[]ipager/x",
     "ls ~/.config/[n]vim"),
    ("share-brace-dot", "sed -i s/a/b/ ~/.local/share/{aipager,a.b}/x",
     "ls ~/.local/share/nvim"),
    ("state-brace-dot", "sed -i s/a/b/ ~/.local/state/a{i,.}pager/x",
     "ls ~/.local/state/nvim"),
    ("brace-quoted-items", "cat ~/.config/{'aipager','a.b'}/x",
     "ls src/{a,b}/c.py"),
    ("brace-range", "sed -i s/a/b/ ~/.config/{a..b}ipager/x",
     "mkdir -p build/{debug,release}"),
    ("brace-after-name", "sed -i s/a/b/ ~/.config/aipag{e,x}r/aipager.yaml",
     "cp file.{txt,bak} /tmp/x"),
    # item 3: glued python -m
    ("py-glued-m", "python3 -maipager setup group --chat-id -1001 "
     "--member 5:me:owner --force", "python3 -mpytest -q"),
    ("py-glued-m-cli", "python3 -maipager.cli setup", "python3 -m pytest -q"),
    ("py-Im-glued", "python3 -Imaipager setup", "python3 -Im pip list"),
    ("py-m-dquoted", 'python3 -m"aipager" setup', "python3 -m venv .venv"),
    ("py-m-squoted", "python3 -m'aipager' setup", "python3 -m json.tool x"),
    ("py-m-dunder-main", "python3 -m aipager.__main__ setup",
     "python3 -m aipager.__main__ doctor"),
    ("py-glued-m-status-ok", "python3 -maipager config",
     "python3 -maipager status"),
    ("py-W-glued-m", "python3 -Wignore -maipager setup",
     "python3 -Wignore -mpytest"),
    ("py-m-pdb", "python3 -m pdb -m aipager setup",
     "python3 -m pdb -m pytest"),
    ("py-m-runpy", "python3 -m runpy aipager setup",
     "python3 -m runpy pytest"),
    ("py-glued-m-pkg-name", "python3 -m 'aipager'.cli setup",
     "python3 -maipagerx"),
    # item 4: name/.. in a path
    ("dotdot-sibling", "cat ~/.config/nvim/../aipager/aipager.yaml",
     "cat ~/.config/nvim/../nvim/init.lua"),
    ("dotdot-sibling-sed",
     "sed -i s/user/owner/ ~/.config/nvim/../aipager/aipager.yaml",
     "cat ~/.config/nvim/../git/config"),
    ("dotdot-share", "cat ~/.local/share/nvim/../aipager/x",
     "cat ~/.local/share/nvim/../nvim/x"),
    ("dotdot-two", "cat ~/.config/a/b/../../aipager/aipager.yaml",
     "cat ../../README.md"),
    ("dotdot-from-cache", "cat ~/.cache/../.config/aipager/aipager.yaml",
     "cd ~/.cache/x/.."),
    ("dotdot-home", "cat $HOME/x/../.config/aipager/aipager.yaml",
     "cat aipager/../README.md"),
    ("dotdot-quoted", 'cat ~/.config/nvim/".."/aipager/aipager.yaml',
     "cd src/.."),
    ("dotdot-ansi", "cat ~/.config/nvim/" + ansi("..") + "/aipager/x",
     "ls ../x"),
    ("dotdot-escaped", "cat ~/.config/nvim/" + B + ".." + B + "/aipager/x",
     "cd aipager/.. && ls"),
    ("dotdot-glob-sibling", "cat ~/.config/n*/../aipager/aipager.yaml",
     "ls ~/.config/nvim/.."),
    ("dotdot-continued", "cat ~/.config/nvim/.." + B + "\n/aipager/x",
     "cd ../.."),
    ("dotdot-to-state", "cat ~/.local/share/x/../../state/aipager/x",
     "cat ~/.config/nvim/lua/../init.lua"),
    ("dotdot-abs-home", "cat /home/alice/.config/nvim/../aipager/x",
     "cd /home/alice/projects/app/.."),
    ("dotdot-option-value", "cat --file=~/.config/nvim/../aipager/x",
     "git -C ../app log --oneline"),
    ("dotdot-scp-host", "scp localhost:.config/nvim/../aipager/aipager.yaml .",
     "scp host:app/../x ."),
    ("aipager-continued-name", "aip" + B + "\nager setup", "aipager status"),
]


@_params(CLOSED)
def test_closed_route_is_blocked(cmd):
    assert _blocked(cmd) is True


@pytest.mark.parametrize("cmd", [r[2] for r in CLOSED],
                         ids=[r[0] for r in CLOSED])
def test_closed_route_neighbour_stays_allowed(cmd):
    assert _blocked(cmd) is False


# ---- the linear floor: 100 KB of padding before a forbidden edit ----

BUDGET_S = 0.5
SIZE = 100_000

PADDING = [
    ("cd-dash", "cd -"),
    ("pushd-dash", "pushd -"),
    ("xargs-n", "xargs -n "),
    ("semi-env-dash", ";env -"),
    ("aipager-v", "aipager -v "),
    ("ansi-hex", ansi("%x61")),
    ("dot-seg", "/./"),
    ("systemctl", "systemctl "),
    ("comment-cd", "# cd -cd -"),
    # fresh units, not in fixes-3.md's table
    ("brace-slash", "{a/"),
    ("config-brace-slash", ".config/{a/b,"),
    ("posix-class", "[[:"),
    ("config-posix-class", ".config/[[:"),
    ("py-m", "python3 -m"),
    ("glued-m", "-maipager."),
    ("dotdot-word", "a/../"),
    ("eq-dotdot", "=a/../"),
    ("colon-dotdot", ":a/../"),
    ("continuation", B + "\n"),
    ("sudo", "sudo "),
    ("if", "if "),
    ("bang", "! "),
    ("open-brace", "{ "),
    ("tab", "\t"),
    ("cd-home-ls", "cd ~ && ls && "),
    ("newline-cd", "\ncd -"),
    ("newline-xargs", "\nxargs -n "),
    ("xargs-n-n", ";xargs -n -n "),
    ("env-u-env", "env -u env "),
    ("timeout-s-timeout", "timeout -s timeout "),
    ("nice-n", "nice -n 5 "),
    ("then-cd", "then cd -"),
    ("builtin-cd", "builtin cd -"),
    ("x1-xargs", "X=1 xargs -n "),
    ("import-comma", "import aipager,"),
    ("run-module", "runpy.run_module("),
]


def _padded(unit: str) -> str:
    return unit * (SIZE // len(unit)) + TAIL


def _best_of_three(cmd: str) -> float:
    best = float("inf")
    for _ in range(3):
        t0 = time.perf_counter()
        bash_violation(cmd, DENY_BASH_PATTERNS)
        best = min(best, time.perf_counter() - t0)
    return best


@pytest.mark.parametrize("unit", [u[1] for u in PADDING],
                         ids=[u[0] for u in PADDING])
def test_padded_forbidden_edit_is_still_denied(unit):
    assert _blocked(_padded(unit)) is True


@pytest.mark.parametrize("unit", [u[1] for u in PADDING],
                         ids=[u[0] for u in PADDING])
def test_padded_check_stays_under_budget(unit):
    assert _best_of_three(_padded(unit)) < BUDGET_S


def test_padding_reaches_100_kb():
    """Precondition for the timing cases: the input really is ~100 KB."""
    assert len(_padded("cd -")) >= SIZE


# ---- fresh spellings that are blocked, pinned so they stay blocked ----

FRESH_BLOCKED = [
    ("py-m-split-quotes", 'python3 -m aip""ager setup'),
    ("py-m-backslash", "python3 -m aip" + B + "ager setup"),
    ("py-BIm", "python3 -BIm aipager setup"),
    ("py-X-dev", "python3 -X dev -m aipager setup"),
    ("py-m-upper", "python3 -m AIPAGER setup"),
    ("py-cprofile", "python3 -m cProfile -m aipager setup"),
    ("py-trace", "python3 -m trace --run -m aipager setup"),
    ("py-runpy-runname",
     "python3 -c \"import runpy;runpy.run_module('aipager',"
     "run_name='__main__')\" setup"),
    ("dotdot-dot-mix", "cat ~/.config/nvim/./../aipager/aipager.yaml"),
    ("dotdot-double-slash", "cat ~/.config/nvim//../aipager/aipager.yaml"),
    ("dotdot-then-dot", "cat ~/.config/nvim/.././aipager/aipager.yaml"),
    ("dotdot-squoted", "cat ~/.config/'nvim/..'/aipager/x"),
    ("dotdot-dquoted-span", 'cat ~/.config/"nvim/../aipager"/x'),
    ("dotdot-leaf-glob", "cat ~/.config/nvim/../a*/aipager.yaml"),
    ("dotdot-star-sibling", "cat ~/.config/*/../aipager/aipager.yaml"),
    ("dotdot-back-in", "cat ~/.config/nvim/../../.config/aipager/x"),
    ("dotdot-assign", "x=~/.config/nvim/../aipager/aipager.yaml; cat $x"),
    ("dotdot-braced-home", "cat ${HOME}/.config/nvim/../aipager/x"),
    ("dotdot-tilde-user", "cat ~alice/.config/nvim/../aipager/x"),
    ("dotdot-cont-mid", "cat ~/.config/nvim/." + B + "\n./aipager/x"),
    ("dotdot-then-brace", "cat ~/.config/nvim/../{aipager,x}/x"),
    ("dotdot-empty-subst", "cat ~/.config/nvim/.$()./aipager/x"),
    ("brace-items-swapped", "sed -i s/a/b/ ~/.config/{a,aipager}/aipager.yaml"),
    ("brace-double", "sed -i s/a/b/ ~/.config/{x,aipager}{,}/aipager.yaml"),
    ("brace-split-name", "sed -i s/a/b/ ~/.config/{aipag,x}er/aipager.yaml"),
    ("brace-escaped-comma", "sed -i s/a/b/ ~/.config/{" + B + ",a}ipager/x"),
    ("brace-dquoted", 'cat ~/.config/"{aipager,x}"/aipager.yaml'),
    ("globstar", "sed -i s/a/b/ ~/.config/**/aipager.yaml"),
    ("local-brace-both", "sed -i s/a/b/ ~/.local/{share,state}/aipager/x"),
    ("bracket-caret", "sed -i s/a/b/ ~/.config/[^.]ipager/x"),
    ("bracket-range", "sed -i s/a/b/ ~/.config/[a-b]ipager/x"),
    ("cd-subshell", "(cd ~/.config && cat aipager/x)"),
    ("cd-group", "{ cd ~/.config; cat aipager/x; }"),
    ("cd-if", "if cd ~/.config; then cat aipager/x; fi"),
    ("cd-glued-and", "cd ~/.config&&cat aipager/x"),
    ("cd-command", "command cd ~/.config"),
    ("cd-dotdot-sibling", "cd ~/.config/nvim/.."),
    ("xargs-dashdash", "xargs -n 1 -- aipager"),
    ("xargs-P", "xargs -0 -n1 -P4 aipager"),
    ("glob-prog-nohup", "nohup aip* setup"),
    ("glob-prog-exec", "exec aip* setup"),
    ("glob-prog-time", "time aip* setup"),
    ("wrap-setsid", "setsid aipager setup"),
    ("wrap-script", "script -qc 'aipager setup' /dev/null"),
    ("wrap-bash-c", "bash -c 'aipager setup'"),
    ("wrap-find-exec", "find . -exec aipager setup " + B + ";"),
]


@_params(FRESH_BLOCKED)
def test_fresh_spelling_is_blocked(cmd):
    assert _blocked(cmd) is True


# ---- ordinary commands that must stay allowed ----

ALLOWED = [
    ("git-commit-m", "git commit -m fix"),
    ("git-commit-m-glued", 'git commit -m"fix typo"'),
    ("git-commit-am", "git commit -am wip"),
    ("git-commit-names-project", 'git commit -m"aipager: tidy"'),
    ("py-m-pip-install", "python3 -m pip install -e ."),
    ("py-m-http", "python3 -m http.server 8000"),
    ("py-m-mypy", "python -m mypy aipager"),
    ("py-m-ruff", "python3 -m ruff check ."),
    ("py-m-compileall-glued", "python3 -mcompileall aipager"),
    ("py-m-pip-show", "python3 -m pip show aipager"),
    ("py-m-pytest-plugin", "python -m pytest -p aipager_plugin"),
    ("py-m-other-pkg", "python3 -m aipager_tools"),
    ("py-m-status", "python3 -m aipager status"),
    ("py-glued-version", "python3 -maipager --version"),
    ("pipx-install", "pipx install aipager"),
    ("cd-up", "cd .."),
    ("cd-up-two", "cd ../.."),
    ("cd-project-up", "cd ~/projects/app/.."),
    ("cd-nvim", "cd ~/.config/nvim"),
    ("cd-nvim-lua-up", "cd ~/.config/nvim/lua/.."),
    ("cd-tmp-up", "cd /tmp/x/.."),
    ("brace-nvim-files", "cp ~/.config/{nvim/init.lua,fish/config.fish} /tmp/x"),
    ("brace-nvim-subdirs", "ls ~/.config/nvim/{lua,after}/"),
    ("brace-git-files", "cat ~/.config/git/{config,ignore}"),
    ("posix-class-nvim", "ls ~/.config/[[:lower:]]vim"),
    ("xargs-echo", "xargs -n 1 echo"),
    ("xargs-grep", "find . -name '*.py' | xargs grep -l TODO"),
    ("xargs-ruff", "find . -print0 | xargs -0 -n 50 ruff check"),
    ("xargs-I-cp", "xargs -I{} cp {} /tmp/x"),
    ("env-u-make", "env -u FOO make test"),
    ("timeout-make", "timeout 60 make test"),
    ("systemctl-user-status", "systemctl --user status pipewire"),
    ("pushd-popd", "pushd ../other && popd"),
    ("git-C-sibling", "git -C ../aipager log --oneline"),
    ("cd-sibling-repo", "cd ../aipager && git status"),
    ("cd-project-named", "cd ~/projects/aipager && pytest"),
    ("grep-module", "grep -rn setup_group aipager/"),
    ("rg-module", 'rg -n "group_rules" aipager tests'),
    ("pytest-scenario", "pytest tests/integration/setup-group -q"),
    ("vim-source", "vim aipager/setup_cmd.py"),
    ("tar-into-build", "tar xzf dist.tgz -C build"),
    ("rsync-src", "rsync -a src/ /tmp/x/"),
    ("docker-mount-nvim", "docker run -v ~/.config/nvim:/x alpine ls"),
    ("ls-config", "ls -la ~/.config"),
    ("py-c-json", "python3 -c 'import os, json; print(1)'"),
    ("py-heredoc-json", "python3 - <<'EOF'\nimport json\nEOF"),
    ("aipager-help-setup", "aipager help setup"),
]


@_params(ALLOWED)
def test_ordinary_command_stays_allowed(cmd):
    assert _blocked(cmd) is False


# ---- still getting through (findings in test-report-3.json) ----
# Each strict xfail is a route that reaches aipager's yaml or setup, was
# confirmed in real bash under a throwaway HOME, and is NOT in
# docs/security.md's "What still gets through" list (undocumented), or is
# documented and listed only for completeness (reason says which).

UNDOCUMENTED = "undocumented gap"
DOCUMENTED = "documented gap"
CLOSED_ITER4 = "closed in iteration 4"  # no xfail: must be blocked
CLOSED_ITER5 = "closed in iteration 5"  # no xfail: must be blocked

GAPS = [
    # brace group holding "/" (tester-iter3-001)
    ("brace-slash-in-item", "sed -i s/a/b/ ~/.config/{aipager,a/b}/x",
     CLOSED_ITER4),
    ("brace-whole-path", "sed -i s/a/b/ ~/.config/{aipager/aipager.yaml,x}",
     CLOSED_ITER4),
    ("brace-sibling-path",
     "sed -i s/a/b/ ~/.config/{nvim/x,aipager/aipager.yaml}", CLOSED_ITER4),
    ("brace-empty-item", "cat ~/.config/{aipager/aipager.yaml,}",
     CLOSED_ITER4),
    ("brace-before-slash", "cat ~/.config{/aipager,}/aipager.yaml",
     CLOSED_ITER4),
    ("brace-share-path", "sed -i s/a/b/ ~/.local/share/{x/y,aipager}/x",
     CLOSED_ITER4),
    ("brace-state-path", "sed -i s/a/b/ ~/.local/state/{aipager/x,y}",
     CLOSED_ITER4),
    ("brace-relative-path", "cd; cat .config/{aipager/aipager.yaml,y}",
     CLOSED_ITER4),
    # POSIX bracket expressions (tester-iter3-002)
    ("posix-alpha", "sed -i s/a/b/ ~/.config/[[:alpha:]]ipager/x",
     CLOSED_ITER4),
    ("posix-equiv", "sed -i s/a/b/ ~/.config/[[=a=]]ipager/x", CLOSED_ITER4),
    ("posix-collating", "sed -i s/a/b/ ~/.config/[[.a.]]ipager/x",
     CLOSED_ITER4),
    ("bracket-close-first", "sed -i s/a/b/ ~/.config/[]a]ipager/x",
     CLOSED_ITER4),
    ("posix-last-letter", "sed -i s/a/b/ ~/.config/aipage[[:alpha:]]/x",
     CLOSED_ITER4),
    ("posix-share", "sed -i s/a/b/ ~/.local/share/[[:alpha:]]ipager/x",
     CLOSED_ITER4),
    # extglob after shopt (tester-iter3-003)
    ("extglob-at", "shopt -s extglob\nsed -i s/a/b/ ~/.config/@(aipager)/x",
     CLOSED_ITER5),
    ("extglob-not",
     "shopt -s extglob\nsed -i s/a/b/ ~/.config/!(nvim)/aipager.yaml",
     CLOSED_ITER5),
    # ".." produced by a brace, or after a part holding = or : (iter3-004)
    ("dotdot-from-brace", "cat ~/.config/nvim/{..,x}/aipager/aipager.yaml",
     CLOSED_ITER4),
    ("dotdot-brace-twice", "cat ~/.config/nvim/..{,}/aipager/aipager.yaml",
     CLOSED_ITER4),
    ("dotdot-brace-built", "cat ~/.config/nvim/.{.,}/aipager/aipager.yaml",
     CLOSED_ITER4),
    ("dotdot-after-eq-dir",
     "mkdir ~/.config/a=b; cat ~/.config/a=b/../aipager/aipager.yaml",
     CLOSED_ITER5),
    ("dotdot-after-colon-dir",
     "mkdir ~/.config/a:b; cat ~/.config/a:b/../aipager/aipager.yaml",
     CLOSED_ITER5),
    # subcommand from a variable or substitution (tester-iter3-005)
    ("sub-from-var", "S=setup; aipager $S group --chat-id -1001 "
     "--member 5:me:owner --force", UNDOCUMENTED),
    ("sub-from-args", "set -- setup group --chat-id -1001 "
     "--member 5:me:owner --force; aipager \"$@\"", UNDOCUMENTED),
    ("sub-from-subst", "aipager $(echo setup) group", UNDOCUMENTED),
    ("sub-var-piece", "aipager s${X}etup group", UNDOCUMENTED),
    # documented classes, listed for completeness
    ("xargs-long-opt-value", "xargs --max-procs 2 aipager", DOCUMENTED),
    ("xargs-nice", "xargs nice aipager", DOCUMENTED),
    ("xargs-py-m", "xargs -n 1 python3 -m aipager", DOCUMENTED),
    ("glob-prog-setsid", "setsid aip* setup", DOCUMENTED),
    ("glob-prog-bash-c", "bash -c 'aip* setup'", DOCUMENTED),
    ("hidden-brace-and-dotted-leaf",
     "cat ~/.{config,x}/{aipager,a.b}/aipager.yaml", CLOSED_ITER5),
    ("cd-cdpath", "CDPATH=~ cd .config", DOCUMENTED),
    ("cd-sibling-then-up", "cd ~/.config/nvim && cd .. && cat aipager/x",
     DOCUMENTED),
    ("relative-from-sibling",
     "cd ~/.config/nvim; cat ../aipager/aipager.yaml", DOCUMENTED),
    ("runpy-private-alias",
     "python3 -c \"import runpy as r; r._run_module_as_main('aipager')\"",
     DOCUMENTED),
]


@pytest.mark.parametrize(
    "cmd",
    [pytest.param(g[1], marks=() if g[2] in (CLOSED_ITER4, CLOSED_ITER5)
                  else pytest.mark.xfail(strict=True, reason=g[2]))
     for g in GAPS],
    ids=[g[0] for g in GAPS])
def test_known_gap_is_blocked(cmd):
    assert _blocked(cmd) is True
