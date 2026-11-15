"""Iteration 4 of the safety floor checks (design.md success criteria 15
and 16, plus the routes fixes-4.md says are now closed or documented).

- Fresh spellings of the three route families fixes-4.md closes (a brace
  holding ``/``, POSIX brackets and ``]``-first brackets, a ``..`` a brace
  makes) must be blocked.
- Every example docs/security.md gives in its "blocked" sentence must be
  blocked, every example it gives as fine must stay allowed, and every
  route fixes-4.md leaves open must be named in "What still gets
  through" (checked against the page text).
- Bracket and brace shapes (nested, unbalanced, ``[!]``, ``[]]``,
  ``[[:``, ``{``, ``{,}``, long runs) at 25 KB and 100 KB must be decided
  in under 0.5 s and grow linearly, both before a forbidden edit (which
  must still be denied) and before a harmless tail (so no early match
  cuts the search short).
- Ordinary developer commands with braces and brackets stay allowed.

Spellings that still get through are strict xfails (project convention
for known gaps): undocumented ones are findings in test-report-4.json.

Only the documented surface is used: ``safety.bash_violation`` with
``safety.DENY_BASH_PATTERNS`` (entrypoints.md "Safety check surface")
and the public docs page.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from aipager.safety import DENY_BASH_PATTERNS, bash_violation

FORBIDDEN_TAIL = "; sed -i s/a/b/ ~/.config/aipager/aipager.yaml"
HARMLESS_TAIL = "; ls ~/.config/nvim x"
SECURITY_MD = Path(__file__).resolve().parents[3] / "docs" / "security.md"


def _blocked(cmd: str) -> bool:
    return bash_violation(cmd, DENY_BASH_PATTERNS) is not None


def _params(rows):
    return pytest.mark.parametrize("cmd", [r[1] for r in rows],
                                   ids=[r[0] for r in rows])


# ---- fresh spellings of the routes fixes-4.md closes ----
# Each one reaches ~/.config/aipager/aipager.yaml in real bash (checked
# under a throwaway HOME for the ones that are not obvious).

CLOSED_FRESH = [
    # a brace that holds "/"
    ("brace-glob-item-path", "cat ~/.config/{nvim/x,a*/aipager.yaml}"),
    ("brace-star-item-path", "cat ~/.config/{*/aipager.yaml,y}"),
    ("brace-bracket-item-path",
     "cat ~/.config/{nvim/x,[a]ipager/aipager.yaml}"),
    ("brace-dquoted-item-path",
     'cat ~/.config/{nvim/x,"aipager"/aipager.yaml}'),
    ("brace-squoted-item-path",
     "cat ~/.config/{nvim/x,'aipager/aipager.yaml'}"),
    ("brace-split-item-path",
     'cat ~/.config/{nvim/x,aip""ager/aipager.yaml}'),
    ("brace-after-dot-step", "cat ~/.config/./{nvim/x,aipager/aipager.yaml}"),
    ("brace-after-double-slash",
     "cat ~/.config//{nvim/x,aipager/aipager.yaml}"),
    ("brace-item-leading-slash",
     "cat ~/.config/{nvim/x,/aipager/aipager.yaml}"),
    ("brace-config-empty-first", "cat ~/.config{,/aipager/aipager.yaml}"),
    ("brace-from-home", "cat ~/{.config/aipager/aipager.yaml,x}"),
    ("brace-whole-word", "cat {~/.config/aipager/aipager.yaml,x}"),
    ("brace-local-state-list", "cat ~/.local/state/{x/y,aipager/x}"),
    ("brace-item-dotdot-back", "cat ~/.config/{x/y,..}/.config/aipager/x"),
    ("brace-item-star-dotdot",
     "cat ~/.config/{x/y,*/../aipager/aipager.yaml}"),
    ("brace-item-dot", "cat ~/.config/{x,.}/aipager/aipager.yaml"),
    ("brace-sed-sibling-path",
     "sed -i s/user/owner/ ~/.config/{git/config,aipager/aipager.yaml}"),
    # POSIX brackets and "]" first
    ("posix-two-classes", "cat ~/.config/[[:alpha:][:digit:]]ipager/x"),
    ("posix-class-then-char", "cat ~/.config/[[:alpha:]x]ipager/x"),
    ("posix-char-then-class", "cat ~/.config/[x[:alpha:]]ipager/x"),
    ("posix-negated-bang", "cat ~/.config/[![:digit:]]ipager/x"),
    ("posix-negated-caret", "cat ~/.config/[^[:digit:]]ipager/x"),
    ("posix-two-equivs", "cat ~/.config/[[=a=][=b=]]ipager/x"),
    ("posix-then-brace", "cat ~/.config/[[:alpha:]]{i,x}pager/x"),
    ("posix-inside-brace-path", "cat ~/.config/{[[:alpha:]]ipager/x,y}"),
    ("close-first-inside-brace-path", "cat ~/.config/{[]a]ipager/x,y}"),
    ("posix-second-brace-item", "cat ~/.config/{x,[[:alpha:]]ipager}/x"),
    ("posix-state", "sed -i s/a/b/ ~/.local/state/[[:alpha:]]ipager/x"),
    ("close-first-negated", "sed -i s/a/b/ ~/.config/[!]x]ipager/x"),
    ("posix-upper-case-name", "cat ~/.config/[[:upper:]]IPAGER/x"),
    # ".." a brace makes
    ("dotdot-brace-dot-suffix", "cat ~/.config/nvim/{.,x}./aipager/x"),
    ("dotdot-brace-open-item",
     "cat ~/.config/nvim/{x,../aipager}/aipager.yaml"),
    ("dotdot-brace-share", "cat ~/.local/share/nvim/{..,x}/aipager/x"),
    ("dotdot-brace-second-item", "cat ~/.config/nvim/{x,..}/aipager/x"),
    ("dotdot-brace-sed",
     "sed -i s/a/b/ ~/.config/systemd/{..,x}/aipager/aipager.yaml"),
]


@_params(CLOSED_FRESH)
def test_fresh_spelling_of_a_closed_route_is_blocked(cmd):
    assert _blocked(cmd) is True


# ---- docs/security.md "blocked" sentence: each example ----

DOC_BLOCKED = [
    ("doc-option-before-sub", "aipager -v setup"),
    ("doc-glued-m", "python3 -maipager setup"),
    ("doc-glob-name", "aip*r setup"),
    ("doc-xargs", "echo setup | xargs aipager"),
    ("doc-import", 'python3 -c "import os, aipager.scope"'),
    ("doc-dquoted", 'cat ~/.config/"aipager"/aipager.yaml'),
    ("doc-split", 'cat ~/.config/aip""ager/aipager.yaml'),
    ("doc-ansi", "cat ~/.config/$'" + chr(92) + "x61ipager'/aipager.yaml"),
    ("doc-empty-subst", "cat ~/.config/aip$()ager/aipager.yaml"),
    ("doc-name-dotdot", "cat ~/.config/nvim/../aipager/aipager.yaml"),
    ("doc-brace-dotdot", "cat ~/.config/nvim/{..,x}/aipager/aipager.yaml"),
    ("doc-question", "cat ~/.config/aipage?/aipager.yaml"),
    ("doc-star", "cat ~/.config/*/aipager.yaml"),
    ("doc-posix", "cat ~/.config/[[:alpha:]]ipager/aipager.yaml"),
    ("doc-brace-dotted", "cat ~/.config/{aipager,a.b}/aipager.yaml"),
    ("doc-brace-whole-path", "cat ~/.config/{aipager/aipager.yaml,x}"),
    ("doc-hidden-glob", "cat ~/.c*/aipager/aipager.yaml"),
    ("doc-local-glob", "cat ~/.local/*/aipager/x"),
    ("doc-cd-two-steps", "cd ~ && cd .config"),
    ("doc-copy-whole", "cp -r ~/.config /tmp/x"),
    ("doc-brace-list-under-config", "ls ~/.config/{nvim,fish}"),
]

DOC_ALLOWED = [
    ("doc-conf-glob", "ls ~/.config/*.conf"),
    ("doc-nvim-glob", "ls ~/.config/nvim*"),
    ("doc-copy-inner-folder", "cp -r ~/.config/nvim /tmp/x"),
    ("doc-brace-deeper", "ls ~/.config/nvim/{lua,after}"),
    ("doc-grep-module", 'grep -rn "from aipager import" .'),
    ("doc-rg-module", "rg setup_group"),
]


@_params(DOC_BLOCKED)
def test_docs_blocked_example_is_blocked(cmd):
    assert _blocked(cmd) is True


@_params(DOC_ALLOWED)
def test_docs_fine_example_stays_allowed(cmd):
    assert _blocked(cmd) is False


# ---- routes fixes-4.md leaves open: each named on the docs page ----
# (id, command, the example text the page must carry)

DOCUMENTED_OPEN = [
    ("extglob", "shopt -s extglob\ncat ~/.config/@(aipager)/aipager.yaml",
     "`~/.config/@(aipager)`"),
    ("dotdot-after-eq",
     "mkdir ~/.config/a=b; cat ~/.config/a=b/../aipager/aipager.yaml",
     "`~/.config/a=b/../aipager`"),
    ("relative-from-systemd",
     "cd ~/.config/systemd && sed -i s/a/b/ ../aipager/aipager.yaml",
     "`cd ~/.config/systemd && sed -i s/a/b/ ../aipager/aipager.yaml`"),
    ("sub-from-var", "S=setup; aipager $S group", "`aipager $S group`"),
    ("sub-from-args", 'aipager "$@"', '`aipager "$@"`'),
    ("sub-from-subst", "aipager $(echo setup) group",
     "`aipager $(echo setup)`"),
    ("nested-brace", "cat ~/.config/{x{a,b},aipager/aipager.yaml}",
     "`~/.config/{x{a,b},aipager/x}`"),
    ("braces-side-by-side",
     "cat ~/.config/nvim/{.,x}{.,y}/aipager/aipager.yaml",
     "`nvim/{.,x}{.,y}/`"),
    ("brace-across-local", "cat ~/.local{/share/aipager,}/x",
     "`~/.local{/share/aipager,}`"),
    ("posix-after-hidden-glob", "cat ~/.c*/[[:alpha:]]ipager/aipager.yaml",
     "`~/.c*/[[:alpha:]]ipager`"),
    ("xargs-long-option", "xargs --max-args 1 aipager",
     "`xargs --max-args 1 aipager`"),
    ("xargs-wrapper", "xargs nice aipager", "`xargs nice aipager`"),
    ("glob-prog-setsid", "setsid aip* setup", "`setsid aip* setup`"),
    ("glob-prog-function", "f(){ aip* setup; }; f", "`f(){ aip* setup; }; f`"),
]


def _docs_text() -> str:
    return re.sub(r"\s+", " ", SECURITY_MD.read_text(encoding="utf-8"))


@pytest.mark.parametrize("example", [r[2] for r in DOCUMENTED_OPEN],
                         ids=[r[0] for r in DOCUMENTED_OPEN])
def test_open_route_is_named_on_the_docs_page(example):
    assert example in _docs_text()


#: Rows closed in iteration 5: no xfail, must be blocked.
CLOSED_ITER5 = {
    "extglob", "dotdot-after-eq", "nested-brace", "braces-side-by-side",
    "brace-across-local", "posix-after-hidden-glob",
    "posix-unclosed-colon", "posix-unclosed-equals", "posix-unclosed-name",
    "posix-unclosed-last", "posix-unclosed-two", "posix-unclosed-end-letter",
    "posix-unclosed-share", "posix-unclosed-in-brace",
    "brace-item-dot-slash", "brace-item-dot-slash-first",
    "brace-item-dot-slash-name", "brace-item-dot-then-name",
    "brace-item-dot-slash-glued", "brace-item-dot-double-slash",
    "brace-item-name-dotdot", "brace-item-name-dot-dotdot",
    "brace-item-dot-slash-bracket", "brace-item-dot-slash-share",
    "brace-item-dot-slash-state",
}


@pytest.mark.parametrize(
    "cmd",
    [pytest.param(r[1], marks=() if r[0] in CLOSED_ITER5 else pytest.mark.xfail(
        strict=True, reason="documented gap")) for r in DOCUMENTED_OPEN],
    ids=[r[0] for r in DOCUMENTED_OPEN])
def test_documented_open_route_is_blocked(cmd):
    assert _blocked(cmd) is True


# ---- undocumented routes found in iteration 4 (findings) ----
# Each confirmed in real bash under a throwaway HOME (cat prints the yaml).

UNDOCUMENTED = "undocumented gap (test-report-4.json)"

NEW_GAPS = [
    # tester-iter4-001: a "[:", "[=" or "[." with no closing ":]"/"=]"/".]"
    # is read by bash as plain characters of the bracket, so "[[:a]" is the
    # set { "[", ":", "a" } and matches "a".
    ("posix-unclosed-colon", "cat ~/.config/[[:a]ipager/aipager.yaml"),
    ("posix-unclosed-equals", "cat ~/.config/[[=a]ipager/aipager.yaml"),
    ("posix-unclosed-name", "cat ~/.config/[[:alpha]ipager/aipager.yaml"),
    ("posix-unclosed-last", "cat ~/.config/[a[:]ipager/aipager.yaml"),
    ("posix-unclosed-two", "cat ~/.config/[[:ab]ipager/aipager.yaml"),
    ("posix-unclosed-end-letter", "cat ~/.config/aipage[[:r]/aipager.yaml"),
    ("posix-unclosed-share", "sed -i s/a/b/ ~/.local/share/[[:a]ipager/x"),
    ("posix-unclosed-in-brace",
     "sed -i s/a/b/ ~/.config/{[[:a]ipager/aipager.yaml,y}"),
    # tester-iter4-002: a brace item that starts with "./" (or holds
    # "name/./..") right under .config / .local/share|state
    ("brace-item-dot-slash",
     "sed -i s/a/b/ ~/.config/{nvim/x,./aipager/aipager.yaml}"),
    ("brace-item-dot-slash-first", "cat ~/.config/{./aipager/aipager.yaml,y}"),
    ("brace-item-dot-slash-name", "cat ~/.config/{x,./aipager}/aipager.yaml"),
    ("brace-item-dot-then-name", "cat ~/.config/{x/y,.}/aipager/aipager.yaml"),
    ("brace-item-dot-slash-glued", "cat ~/.config/{x/y,./}aipager/aipager.yaml"),
    ("brace-item-dot-double-slash",
     "cat ~/.config/{nvim/x,.//aipager/aipager.yaml}"),
    ("brace-item-name-dotdot",
     "cat ~/.config/{nvim/x,nvim/../aipager/aipager.yaml}"),
    ("brace-item-name-dot-dotdot",
     "cat ~/.config/{nvim/x,nvim/./../aipager/aipager.yaml}"),
    ("brace-item-dot-slash-bracket",
     "cat ~/.config/{nvim/x,./[a]ipager/aipager.yaml}"),
    ("brace-item-dot-slash-share", "cat ~/.local/share/{x/y,./aipager/x}"),
    ("brace-item-dot-slash-state", "cat ~/.local/state/{x/y,./aipager/x}"),
]


@pytest.mark.parametrize(
    "cmd",
    [pytest.param(r[1], marks=() if r[0] in CLOSED_ITER5
                  else pytest.mark.xfail(strict=True, reason=UNDOCUMENTED))
     for r in NEW_GAPS],
    ids=[r[0] for r in NEW_GAPS])
def test_new_gap_is_blocked(cmd):
    assert _blocked(cmd) is True


# ---- ordinary commands with braces and brackets stay allowed ----

ALLOWED = [
    ("brace-config-files", "cat ~/.config{/nvim/init.lua,/fish/config.fish}"),
    ("brace-git-nvim-files", "cat ~/.config/{git/config,nvim/x}"),
    ("brace-nvim-subdirs-file", "cat ~/.config/nvim/{lua,after}/x"),
    ("brace-two-subfolders", "ls ~/.config/{nvim/lua,fish/functions}"),
    ("brace-nested-deeper", "ls ~/.config/nvim/{lua/{a,b},after}"),
    ("brace-name-tail", "ls ~/.config/n{vim,ano}/x"),
    ("brace-backup-copy", "cp -r ~/.config/nvim{,.bak}"),
    ("brace-diff-backup", "diff ~/.config/nvim/{init.lua,init.lua.bak}"),
    ("brace-dot-item-deeper", "cat ~/.config/nvim/{.,x}/init.lua"),
    ("brace-then-dotdot-deeper", "cat ~/.config/nvim/{x,y}/../init.lua"),
    ("brace-cd-projects-up", "cd ~/projects/{app,lib}/.."),
    ("brace-share-deeper", "ls ~/.local/share/nvim/{site,lazy}"),
    ("brace-state-deeper", "ls ~/.local/state/nvim/{log,shada}"),
    ("brace-cache", "cat ~/.cache/{pip,uv}/x"),
    ("brace-fish-files", "cat ~/.config/fish/conf.d/{a,b}.fish"),
    ("brace-git-diff", "git diff HEAD~1 -- src/{a,b}.py"),
    ("brace-git-checkout", "git checkout -- src/{a,b}.py"),
    ("brace-pytest-dirs", "pytest tests/{unit,integration} -q"),
    ("brace-mkdir-nested", "mkdir -p build/{debug,release}/{bin,lib}"),
    ("brace-sequence", "echo {1..10}"),
    ("brace-for-loop", "for f in src/{a,b,c}.py; do echo $f; done"),
    ("brace-tar", "tar czf x.tgz src/{a,b}"),
    ("param-expansion", "echo ${HOME}"),
    ("posix-lower-nvim", "ls ~/.config/[[:lower:]]vim"),
    ("close-first-negated-nvim", "ls ~/.config/[!]x]vim"),
    ("posix-deeper", "ls ~/.config/nvim/lua/[[:alpha:]]*.lua"),
    ("posix-git-config", "cat ~/.config/git/[[:alpha:]]onfig"),
    ("posix-mid-name", "cat ~/.config/g[[:alpha:]]t/config"),
    ("bracket-range-git", "cat ~/.config/[gp]it/config"),
    ("bracket-range-src", "ls src/[a-z]*.py"),
    ("posix-find", "find . -name '[[:upper:]]*'"),
    ("posix-grep", "grep -E '[[:space:]]+$' -r src"),
    ("posix-sed-strip", "sed -i 's/[[:space:]]*$//' src/x.py"),
    ("posix-tr", "tr '[:lower:]' '[:upper:]' < x"),
    ("posix-awk", "awk '/[[:digit:]]/' file"),
    ("test-double-bracket", "[[ -f ~/.config/nvim/init.lua ]] && echo ok"),
    ("test-pattern", "if [[ $x == [ab]* ]]; then echo y; fi"),
    ("systemd-units", "ls ~/.config/systemd/user/[a-z]*.service"),
    ("nvim-dotdot-fish", "ls ~/.config/nvim/../fish"),
    ("nvim-dot-step", "cat ~/.config/nvim/./init.lua"),
    ("git-log", "git log --oneline -5"),
    ("git-rebase-continue", "git rebase --continue"),
    ("cd-up-ls", "cd .. && ls"),
    ("ruff", "ruff check aipager tests"),
]


@_params(ALLOWED)
def test_ordinary_command_stays_allowed(cmd):
    assert _blocked(cmd) is False


# ---- timing: bracket and brace shapes at 25 KB and 100 KB ----

BUDGET_S = 0.5
SMALL, LARGE = 25_000, 100_000

PREFIXES = [
    ("none", ""),
    ("config", "cat ~/.config/"),
    ("config-brace", "cat ~/.config/{"),
    ("config-glued-brace", "cat ~/.config{"),
    ("share", "cat ~/.local/share/"),
    ("nvim", "cat ~/.config/nvim/"),
]

SHAPES = [
    ("open-brace", "{"),
    ("close-brace", "}"),
    ("double-open-brace", "{{"),
    ("nested-open", "{a{b,"),
    ("empty-brace", "{,}"),
    ("brace-comma", "{,"),
    ("brace-slash", "{a/"),
    ("brace-dot-slash", "{./"),
    ("brace-dotdot", "{..,"),
    ("brace-path-dot", "{x/y,.}"),
    ("open-bracket", "["),
    ("close-bracket", "]"),
    ("empty-bracket", "[]"),
    ("bracket-close-first", "[]]"),
    ("bracket-bang", "[!]"),
    ("bracket-caret", "[^]"),
    ("bracket-bang-close", "[!]]"),
    ("posix-open", "[[:"),
    ("posix-equiv-open", "[[="),
    ("posix-unclosed", "[[:a]"),
    ("posix-full", "[[:alpha:]]"),
    ("bracket-in-brace", "{[!]"),
    ("brace-in-bracket", "[{"),
    ("bang-close-x", "[!]x]"),
    ("comma", ","),
    ("dot-brace-dot", "a/.{.,}/"),
]

TIMING = [(p[0] + "-" + s[0], p[1], s[1]) for p in PREFIXES for s in SHAPES]
TIMING_IDS = [t[0] for t in TIMING]
TIMING_ARGS = [(t[1], t[2]) for t in TIMING]


def _build(prefix: str, unit: str, size: int, tail: str) -> str:
    return prefix + unit * (size // len(unit)) + tail


def _best_of_three(cmd: str) -> float:
    best = float("inf")
    for _ in range(3):
        t0 = time.perf_counter()
        bash_violation(cmd, DENY_BASH_PATTERNS)
        best = min(best, time.perf_counter() - t0)
    return best


@pytest.mark.parametrize(("prefix", "unit"), TIMING_ARGS, ids=TIMING_IDS)
def test_shape_before_a_forbidden_edit_is_denied(prefix, unit):
    assert _blocked(_build(prefix, unit, LARGE, FORBIDDEN_TAIL)) is True


@pytest.mark.parametrize(("prefix", "unit"), TIMING_ARGS, ids=TIMING_IDS)
def test_shape_before_a_forbidden_edit_is_under_budget(prefix, unit):
    assert _best_of_three(_build(prefix, unit, LARGE, FORBIDDEN_TAIL)) < BUDGET_S


@pytest.mark.parametrize(("prefix", "unit"), TIMING_ARGS, ids=TIMING_IDS)
def test_shape_before_a_harmless_tail_is_under_budget(prefix, unit):
    assert _best_of_three(_build(prefix, unit, LARGE, HARMLESS_TAIL)) < BUDGET_S


@pytest.mark.parametrize(("prefix", "unit"), TIMING_ARGS, ids=TIMING_IDS)
def test_shape_grows_linearly(prefix, unit):
    """4x the input may take at most 8x the time (quadratic would be 16x);
    a 20 ms floor keeps timer noise on tiny times out of it."""
    small = _best_of_three(_build(prefix, unit, SMALL, HARMLESS_TAIL))
    large = _best_of_three(_build(prefix, unit, LARGE, HARMLESS_TAIL))
    assert large < max(8 * small, 0.02)


# Single-start long runs that end on a letter no pattern takes, so no early
# match cuts the search short (the shape fixes-4.md says blew up first).
LONG_RUNS = [
    ("config-empty-brackets", "cat ~/.config/" + "[]" * 40 + "x"),
    ("config-bang-brackets", "cat ~/.config/" + "[!]" * 34 + "x"),
    ("config-caret-brackets", "cat ~/.config/" + "[^]" * 34 + "x"),
    ("config-close-first-runs", "cat ~/.config/a" + "[]]" * 34 + "x"),
    ("config-unclosed-posix", "cat ~/.config/" + "[[:" * 34 + "x"),
    ("config-unclosed-posix-a", "cat ~/.config/" + "[[:a]" * 34 + "x"),
    ("brace-bang-brackets", "cat ~/.config/{" + "[!]" * 34 + "x/y"),
    ("brace-nested-open", "cat ~/.config/" + "{" * 34 + "a/x"),
    ("brace-empty-items", "cat ~/.config/{" + "," * 34 + "a/x"),
    ("brace-glued-runs", "cat ~/.config{" + "{,}" * 34 + "/x"),
    ("brace-dot-slash-runs", "cat ~/.config/{" + "./" * 34 + "x,y/z}"),
    ("dotdot-brace-runs", "cat a/" + "{..,x" * 34 + "/b"),
    ("bracket-in-brace-runs", "cat ~/.config/{" + "[[:a]/" * 34 + ",y}"),
]


@_params(LONG_RUNS)
def test_long_single_start_run_is_fast(cmd):
    assert _best_of_three(cmd) < BUDGET_S
