"""Iteration 5 of the safety floor checks (design.md success criteria 15
and 16; fixes-5.md's bash-faithful glob and brace reading).

Every spelling in GAPS was found by the tester's own differential fuzz
against real bash 5.2 under a throwaway HOME: bash expands the word to
a file inside ~/.config/aipager or ~/.local/{share,state}/aipager, and
the floor allows it. None is in a route docs/security.md names as open,
so each is a strict xfail (project convention for known gaps): closing
one turns its test red until the mark is removed.

Only the documented surface is used: ``safety.bash_violation`` with
``safety.DENY_BASH_PATTERNS`` (entrypoints.md "Safety check surface").
Backslashes are built at run time with ``B``. Timing cases take the
best of three runs so a loaded machine does not fail them.
"""

from __future__ import annotations

import time

import pytest

from aipager.safety import DENY_BASH_PATTERNS, bash_violation

B = chr(92)  # a backslash
EXT = "shopt -s extglob\n"  # extglob has to be on before the line is parsed


def _blocked(cmd: str) -> bool:
    return bash_violation(cmd, DENY_BASH_PATTERNS) is not None


def ansi_x(hex2: str) -> str:
    """``$'\\xHH'``."""
    return "$'" + B + "x" + hex2 + "'"


def _params(rows):
    return pytest.mark.parametrize("cmd", [r[1] for r in rows],
                                   ids=[r[0] for r in rows])


# ---- gaps: bash reaches aipager's folders, the floor allows ----

UNDOCUMENTED = "undocumented gap (tester iteration 5 fuzz)"

GAPS = [
    # an extended glob group that ends a folder name (shopt -s extglob on
    # an earlier line); docs say an extended glob is refused
    ("ext-config-optional",
     EXT + "sed -i s/a/b/ ~/.config?(x)/aipager/aipager.yaml"),
    ("ext-config-not", EXT + "cat ~/.config!(x)/aipager/aipager.yaml"),
    ("ext-config-empty-alt", EXT + "cat ~/.config@(|x)/aipager/aipager.yaml"),
    ("ext-config-home-var", EXT + "cat $HOME/.config?(x)/aipager/aipager.yaml"),
    ("ext-leaf-in-brace",
     EXT + "cat ~/.config/{aipager@(|x)/aipager.yaml,y}"),
    ("ext-leaf-after-star-config", EXT + "cat ~/.c*/aipager?(x)/aipager.yaml"),
    ("ext-leaf-after-star-local", EXT + "cat ~/.local/s*/aipager?(x)/x"),
    ("ext-leaf-after-glob-state", EXT + "cat ~/.local/st?te/aipager@(|x)/x"),
    ("ext-star-group-and-leaf",
     EXT + "cat ~/*(.config)/aipager!(x)/aipager.yaml"),
    ("ext-local-brace", EXT + "cat ~/.local/{share/aipager?(x)/x,nvim/..}"),
    # a POSIX item whose own character is "[": bash reads [=[=] and [:[:]
    # so that the bracket still matches the letter beside it
    ("posix-equiv-of-bracket",
     "sed -i s/a/b/ ~/.config/aip[a[=[=]ger/aipager.yaml"),
    ("posix-class-of-bracket",
     "sed -i s/a/b/ ~/.config/aip[a[:[:]ger/aipager.yaml"),
    ("posix-equiv-of-bracket-close-first",
     "cat ~/.config/aip[]a[=[=]ger/aipager.yaml"),
    ("posix-equiv-of-bracket-share", "cat ~/.local/share/aip[]a[=[=]ger/x"),
    ("posix-equiv-of-bracket-state", "cat ~/.local/state/aip[a[=[=]ger/x"),
    ("posix-equiv-then-equiv-of-bracket",
     "cat ~/.config/aip[a[=a=][=[=]ger/aipager.yaml"),
    ("posix-class-of-bracket-in-config",
     "cat ~/.co[n[:[:]fig/aipager/aipager.yaml"),
    ("posix-equiv-of-bracket-in-config",
     "cat ~/.conf[]i[=[=]g/aipager/aipager.yaml"),
    ("posix-equiv-of-bracket-ansi",
     "cat ~/.config/a[" + ansi_x("69") + "][[=p=][=[=]ager/aipager.yaml"),
    ("posix-class-of-bracket-braced-home",
     "cat ${HOME}/.config/aip[a[:[:]ger/aipager.yaml"),
]


#: Rows closed in the developer's iteration 6 (their xfail mark removed).
CLOSED_ITER6 = {
    "ext-config-optional", "ext-config-not", "ext-config-empty-alt",
    "ext-config-home-var", "ext-leaf-in-brace", "ext-leaf-after-star-config",
    "ext-leaf-after-star-local", "ext-leaf-after-glob-state",
    "ext-star-group-and-leaf", "ext-local-brace",
}


@pytest.mark.parametrize(
    "cmd",
    [pytest.param(g[1], marks=() if g[0] in CLOSED_ITER6 else
                  pytest.mark.xfail(strict=True, reason=UNDOCUMENTED))
     for g in GAPS],
    ids=[g[0] for g in GAPS])
def test_gap_is_blocked(cmd):
    assert _blocked(cmd) is True


# ---- fresh spellings that are blocked today and must stay blocked ----
# Each was checked in real bash: it reaches one of aipager's folders.

FRESH_BLOCKED = [
    ("bracket-holding-bracket-and-a", "sed -i s/a/b/ ~/.config/[[a]ipager/x"),
    ("ext-group-inside-name", EXT + "cat ~/.co@(n)fig/aipager/aipager.yaml"),
    ("ext-group-is-config", EXT + "cat ~/@(.config)/aipager/x"),
    ("ext-group-local-end", EXT + "cat ~/.local?(x)/share/aipager/x"),
    ("ext-group-share-end", EXT + "cat ~/.local/share@(|x)/aipager/x"),
    ("ext-group-leaf-literal-config",
     EXT + "cat ~/.config/aipager?(x)/aipager.yaml"),
    ("posix-unclosed-colon", "cat ~/.config/aip[a[:]ger/x"),
    ("posix-unclosed-equals-letter", "cat ~/.config/aip[a[=x]ger/x"),
    ("list-of-1000-last", "cat ~/.config/{" + ",".join(
        "x%d" % i for i in range(999)) + ",aipager}/x"),
    ("list-of-257-last", "cat ~/.config/{" + ",".join(
        "x%d" % i for i in range(256)) + ",aipager}/x"),
    ("depth-40", "cat ~/.config/" + "{" * 40 + "aipager" + ",x}" * 40 + "/x"),
    ("long-item-20k", "cat ~/.config/{aipager," + "y" * 20000 + "}/x"),
    ("seq-letters", "cat ~/.config/{a..z}ipager/x"),
    ("seq-letters-step", "cat ~/.config/{a..z..26}ipager/x"),
    ("seq-two", "cat ~/.config/{a..z}{a..z}pager/x"),
    ("seq-reversed", "cat ~/.config/{c..a}ipager/x"),
    ("ansi-letter", "cat ~/.config/aip" + ansi_x("61") + "ger/aipager.yaml"),
    ("ansi-star", "cat ~/.config/" + ansi_x("61") + "*/aipager.yaml"),
    ("ansi-config-letter", "cat ~/." + ansi_x("63") + "onfig/a*/aipager.yaml"),
    ("home-var-glob", "cat $HOME/.config/aipage?/aipager.yaml"),
    ("home-braced-brace", "cat ${HOME}/.config/{aipager,x}/aipager.yaml"),
    ("home-quoted-config-glob", 'cat "$HOME"/.c*/aipager/aipager.yaml'),
    ("home-trim-posix", "cat ${HOME%/}/.config/[[:alpha:]]ipager/x"),
    ("tilde-plus", "cat ~+/.config/a*/aipager.yaml"),
    ("tilde-user", "cat ~alice/.config/aipage?/aipager.yaml"),
    ("abs-home-brace", "cat /home/alice/.config/{x,aipager}/aipager.yaml"),
    ("abs-users-glob", "cat /Users/alice/.local/share/a*/x"),
    ("dotglob-star", "shopt -s dotglob; cat ~/*/aipager/aipager.yaml"),
    ("dotglob-question", "shopt -s dotglob; cat ~/?config/aipager/aipager.yaml"),
    ("globstar-local", "shopt -s globstar; cat ~/.local/**/x"),
    ("nested-brace-two-levels", "cat ~/.config/{{a,b},c}ipager/aipager.yaml"),
    ("quoted-items-brace", 'cat ~/.config/{"aipager","x"}/aipager.yaml'),
]


@_params(FRESH_BLOCKED)
def test_fresh_spelling_is_blocked(cmd):
    assert _blocked(cmd) is True


# ---- ordinary developer commands stay allowed ----

ALLOWED = [
    "git log --oneline -20 -- src/{app,lib}",
    "git checkout -b feature/{x,y}",
    "python3 -m pytest -q tests/test_{api,db}.py",
    'pytest -k "not slow" tests/',
    "cp src/config{,.bak}.py",
    "mv src/{old,new}_name.py",
    "mkdir -p build/{debug,release}/{bin,lib}",
    "mkdir -p {a,b}{c,d}{e,f}{g,h}{i,j}{k,l}{m,n}{o,p}{q,r}",
    "echo {1..10}",
    "for i in {1..5}; do echo $i; done",
    "ls ~/.config/nvim/lua/*.lua",
    "vim ~/.config/nvim/lua/{plugins,keys}.lua",
    "cat ~/.config/[n]vim/init.lua",
    "ls ~/.config/[[:lower:]]ip",
    "cp ~/.config/{git/config,nvim/init.lua} /tmp/x",
    "ls ~/.local/state/nvim/*.log",
    "find src -name '[a-z]*.py'",
    "ls src/[!_]*.py",
    "ls tests/test_[a-m]*.py",
    "ls tests/test_[[:alpha:]]*.py",
    "[[ -f setup.py ]] && echo yes",
    'echo "${HOME}/projects"',
    "cd ~/projects/{app,lib}/..",
    "rsync -a {src,lib}/ ../backup/",
    "sed -i 's/foo/bar/g' src/*.py",
    "echo $'tab" + B + "there'",
    "du -sh ~/projects/*",
    "ls .??*",
    "cat docs/{intro,usage}.md",
]


@pytest.mark.parametrize("cmd", ALLOWED)
def test_ordinary_command_stays_allowed(cmd):
    assert _blocked(cmd) is False


# ---- timing: shapes aimed at the brace expander and the glob matcher ----

BUDGET_S = 0.5
SIZE = 100_000
SMALL = 25_000
FORBIDDEN_TAIL = "; sed -i s/a/b/ ~/.config/aipager/aipager.yaml"
HARMLESS_TAIL = "; sed -i s/a/b/ ~/notes.txt"

UNITS = [
    ("brace-open", "{"),
    ("brace-item", "{a,"),
    ("item-close", "a,}"),
    ("brace-nest", "{a{b,"),
    ("brace-empty-item", "{,"),
    ("brace-pair", "{a,b}"),
    ("brace-dot-items", "{.,..}/"),
    ("brace-slash-item", "{x/y,."),
    ("brace-quoted-comma", "{a',',"),
    ("brace-escaped-comma", "{a" + B + ","),
    ("commas", ","),
    ("comma-letter", "a,"),
    ("seq-letters", "{a..c}"),
    ("seq-open", "{a.."),
    ("dots", ".."),
    ("bracket", "[a]"),
    ("bracket-open", "["),
    ("posix-unclosed", "[[:a]"),
    ("posix-equiv-of-bracket", "[a[=[=]"),
    ("close-first", "[]a"),
    ("negated", "[!x]"),
    ("class", "[[:alpha:]]"),
    ("quoted-close", '[a"]"'),
    ("single-quoted", "'aipager'"),
    ("double-quoted", '"a"'),
    ("ansi", "$'" + B + "x61'"),
    ("empty-quotes", "''"),
    ("escaped-brace", B + "{"),
    ("escaped-bracket", B + "["),
    ("escaped-dot", B + "."),
    ("ext-open", "@(a|"),
    ("ext-optional-end", "?(x)/"),
    ("star-slash", "*/"),
    ("dot-star-slash", ".*/"),
    ("name-dotdot", "a/../"),
    ("dot-slash", "./"),
    ("double-slash", "//"),
    ("var-slash", "$X/"),
]
PREFIXES = [
    ("config", "cat ~/.config/"),
    ("share", "cat ~/.local/share/"),
    ("plain", "cat "),
    ("config-brace", "cat ~/.config/{"),
]
SHAPES = [(f"{p[0]}-{u[0]}", p[1], u[1]) for p in PREFIXES for u in UNITS]


def _shape(prefix: str, unit: str, size: int, tail: str) -> str:
    return prefix + unit * (size // len(unit)) + tail


def _best_of_three(cmd: str) -> float:
    best = float("inf")
    for _ in range(3):
        t0 = time.perf_counter()
        bash_violation(cmd, DENY_BASH_PATTERNS)
        best = min(best, time.perf_counter() - t0)
    return best


_shape_params = pytest.mark.parametrize(
    "prefix,unit", [(s[1], s[2]) for s in SHAPES], ids=[s[0] for s in SHAPES])


@_shape_params
def test_shape_before_a_forbidden_edit_is_denied(prefix, unit):
    assert _blocked(_shape(prefix, unit, SIZE, FORBIDDEN_TAIL)) is True


@_shape_params
def test_shape_before_a_forbidden_edit_is_under_budget(prefix, unit):
    assert _best_of_three(_shape(prefix, unit, SIZE, FORBIDDEN_TAIL)) < BUDGET_S


@_shape_params
def test_shape_before_a_harmless_tail_is_under_budget(prefix, unit):
    assert _best_of_three(_shape(prefix, unit, SIZE, HARMLESS_TAIL)) < BUDGET_S


@_shape_params
def test_shape_grows_linearly(prefix, unit):
    """100 KB costs at most 8x 25 KB (linear is 4x); under 50 ms counts
    as fast whatever the ratio, so timer noise on tiny runs is ignored.
    A quadratic shape fails every round; noise gets three rounds."""
    small_cmd = _shape(prefix, unit, SMALL, HARMLESS_TAIL)
    big_cmd = _shape(prefix, unit, SIZE, HARMLESS_TAIL)
    ok = False
    for _ in range(3):
        ok = _best_of_three(big_cmd) < max(8 * _best_of_three(small_cmd), 0.05)
        if ok:
            break
    assert ok is True


def test_shapes_reach_100_kb():
    """Precondition for the timing cases: the inputs really are ~100 KB."""
    assert min(len(_shape(s[1], s[2], SIZE, "")) for s in SHAPES) >= SIZE - 10
