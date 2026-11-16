"""Iteration 9 of the safety floor checks (design.md success criteria 15
and 16; docs/security.md "aipager's own commands and folders", "What
still gets through" and "Searches through Bash").

Whole commands. Every reaching row was run by real bash 5.2 in a throwaway
HOME with PATH empty (x=a, y=b, every other variable unset): each bare
command bash cannot find goes to a recorder (``command_not_found_handle``)
that logs its working folder and full argv, ``bash``/``sh`` run the real
bash so ``bash -c`` text runs too, and strace logs every file bash opens.
A row "reaches" when it reads or writes a file in ~/.config/aipager or
~/.local/{share,state}/aipager, runs a refused aipager form, or, for the
search rows, runs a recursive ``grep``/``rg``/``find``/``ls -R``/``du``/
``tree`` of the home folder, ``~/.config``, ``~/.local`` or ``/``. Each
"allowed" row reaches nothing and runs no such search in bash. The
tester's harness lives in the ship artifacts (tester9-tools/oracle.py,
hp.py, searchprobe.py, gen9.py).

Territory this round: ``function NAME`` and ``NAME()`` heads at program
positions not tried before (``select``/``elif`` bodies, ``!``/``time``
stacks, conditions, pipelines, process substitution, after a newline or a
comment, a body that is any compound command with its program after
``!``, ``time``, ``env``, a setting, ``&&``/``|`` or a nested definition);
bracket ranges from named collating symbols (``[[.hyphen.]-z]``); ``&`` in
a replacement inside double quotes and from a nested default; values next
to a glob; plain paths inside ``${v:-...}``/``${x:+...}``/replacements;
escaped separators inside ``bash -c``, ``sh -c``, ``eval``, ``$'...'``,
backticks and here-documents; the docs' list of open search routes.

Known gaps found this round (strict xfail, the project's convention):

- SLOW_SHAPES_ITER9: an escaped quote and an empty quote pair (``""``,
  ``''``, ``$'``, ``$"``) before an escaped separator and a search name
  (``\\""\\;rg x ``, ``\\''\\|rg x ``)
  repeated in one word takes time quadratic in its length since 9efe41dd
  (100 KB: 4.5 s to about 40 s; b5153bd7: 0.06 s). Measured here at 25 KB,
  where a linear reading takes about 0.02 s.

- AMP_DQ_OPEN: inside double quotes (``~/.config/"${x/a/&ipager}"/x``,
  ``"$HOME/.config/${x/a/&ipager}/x"``) bash 5.2 still turns the bare
  ``&`` into the matched text; so does a ``&`` that comes from a default
  inside the replacement (``${x/a/${z:-&}ipager}``). The floor reads the
  bare ``&`` only outside double quotes.
- SEARCH_OPEN: inside ``bash -c "..."`` an escaped ``\\$HOME`` (the usual
  way to keep the outer shell from expanding it) or a nested ``bash -c
  \\"...\\"`` whose escaped quote follows the folder word: the inner bash
  runs ``grep -rn tok ~``. Allowed on main 5c286755 as well (not a
  regression); docs/security.md says the search rule holds inside
  ``bash -c "..."`` for ``$HOME``.

Only the documented surface is used: ``safety.bash_violation`` with
``safety.DENY_BASH_PATTERNS`` (entrypoints.md "Safety check surface").
Timing cases take the best of three runs, each a different command (a
distinct leading ``: rN;``), so no cache keyed by the text helps.
"""

from __future__ import annotations

import itertools
import re
import time
from pathlib import Path

import pytest

from aipager.safety import DENY_BASH_PATTERNS, bash_violation

B = chr(92)  # a backslash
BT = "`"
DOCS = Path(__file__).resolve().parents[3] / "docs"
SECURITY_MD = DOCS / "security.md"


def _blocked(cmd: str) -> bool:
    return bash_violation(cmd, DENY_BASH_PATTERNS) is not None


def _params(rows):
    return pytest.mark.parametrize("cmd", [r[1] for r in rows],
                                   ids=[r[0] for r in rows])


def _xfail_params(rows, reason):
    return pytest.mark.parametrize(
        "cmd",
        [pytest.param(r[1], marks=pytest.mark.xfail(strict=True, reason=reason))
         for r in rows],
        ids=[r[0] for r in rows])


def _docs_text(path: Path = SECURITY_MD) -> str:
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


def _docs_section(start: str, end: str) -> str:
    text = _docs_text()
    i = text.index(start)
    return text[i:text.index(end, i)]


# ---- function heads at every program position, any compound body ----

FUNCTION_REACH_ITER9 = [
    ('fn-01', 'select i in 1; do function f { aip*r setup; }; f; break; done <<< 1'),
    ('fn-02', 'if ! function f { aip*r setup; }; then :; fi; f'),
    ('fn-03', 'while function f { aip*r setup; }; f; do break; done'),
    ('fn-04', 'until function f { aip*r setup; }; f; do :; done'),
    ('fn-05', 'time ! function f { aip*r setup; }; f'),
    ('fn-06', '! time function f { aip*r setup; }; f'),
    ('fn-07', 'time -p ! function f { aip*r setup; }; f'),
    ('fn-08', 'if false; then :; elif function f { aip*r setup; }; f; then :; fi'),
    ('fn-09', 'function f { aip*r setup; } >/dev/null; f'),
    ('fn-10', 'function f { aip*r setup; } 2>&1; f'),
    ('fn-11', ': | { function f { aip*r setup; }; f; }'),
    ('fn-12', 'cat <(function f { aip*r setup; }; f)'),
    ('fn-13', ': & function f { aip*r setup; }; f'),
    ('fn-14', '{\nfunction f {\naip*r setup\n}\nf\n}'),
    ('fn-15', 'if :; then f () { aip*r setup; }; f; fi'),
    ('fn-16', 'while :; do f() { aip*r setup; }; f; break; done'),
    ('fn-17', 'function f() while aip*r setup; do break; done; f'),
    ('fn-18', 'function f() if aip*r setup; then :; fi; f'),
    ('fn-19', 'time function f() while aip*r setup; do break; done; f'),
    ('fn-20', '! f() if aip*r setup; then :; fi; f'),
    ('fn-21', '{ f() until ! aip*r setup; do break; done; }; f'),
    ('fn-22', 'f()\nwhile aip*r setup; do break; done\nf'),
    ('fn-23', 'function f\nwhile aip*r setup; do break; done\nf'),
    ('fn-24', 'f() \\\nwhile aip*r setup; do break; done; f'),
    ('fn-25', 'f() if [[ 1 ]] && aip*r setup; then :; fi; f'),
    ('fn-26', 'f() while ! aip*r setup; do break; done; f'),
    ('fn-27', 'f() while time aip*r setup; do break; done; f'),
    ('fn-28', 'f() while { aip*r setup; }; do break; done; f'),
    ('fn-29', 'f() if (aip*r setup); then :; fi; f'),
    ('fn-30', 'function f if if aip*r setup; then :; fi; then :; fi; f'),
    ('fn-31', 'f() while g() { aip*r setup; }; g; do break; done; f'),
    ('fn-32', 'f() select i in 1; do aip*r setup; break; done <<< 1; f'),
    ('fn-33', 'f() for i in 1; do aip*r setup; done; f'),
    ('fn-34', 'f() [[ $(aip*r setup) ]]; f'),
    ('fn-35', 'f() (( $(aip*r setup) )); f'),
    ('fn-36', 'function f() until aip*r config; do break; done; f'),
    ('fn-37', '{ function f if aip*r setup; then :; fi; }; f'),
    ('fn-38', 'if :; then function f while aip*r setup; do break; done; fi; f'),
    ('fn-39', 'for i in 1; do f() if aip*r setup; then :; fi; done; f'),
    ('fn-40', 'function g { f() while aip*r setup; do break; done; }; g; f'),
    ('fn-41', 'g() { function f until aip*r setup; do break; done; }; g; f'),
    ('fn-42', 'time -p f() while aip*r setup; do break; done; f'),
    ('fn-43', 'f () while aip*r setup ; do break ; done ; f'),
    ('fn-44', 'f\t()\twhile\taip*r setup; do break; done; f'),
    ('fn-45', 'function\tf\twhile\taip*r setup; do break; done; f'),
    ('fn-46', 'function f\t{\taip*r setup; }; f'),
    ('fn-47', 'x=1 ; function f { aip*r setup; }; f'),
    ('fn-48', '[[ 1 ]] && { function f { aip*r setup; }; f; }'),
    ('fn-49', 'echo $(time function f { aip*r setup; }; f)'),
    ('fn-50', 'echo `if :; then function f { aip*r setup; }; f; fi`'),
    ('fn-51', 'x=$(! function f { aip*r setup; }; f)'),
    ('fn-52', 'function f { function g if aip*r setup; then :; fi; g; }; f'),
    ('fn-53', 'f() { g() while aip*r setup; do break; done; g; }; f'),
    ('fn-54', 'function f { aip*r setup; }; time f'),
    ('fn-55', 'f() # c\nwhile aip*r setup; do break; done\nf'),
    ('fn-56', 'function f # c\nif aip*r setup; then :; fi\nf'),
    ('fn-57', 'f()\n\nuntil ! aip*r setup; do break; done\nf'),
    ('fn-58', 'function f() \\\nif aip*r setup; then :; fi; f'),
    ('fn-59', 'f() if aip*r setup\nthen :\nfi\nf'),
    ('fn-60', 'f() while aip*r setup >/dev/null; do break; done 2>&1; f'),
    ('fn-61', 'f() if aip*r setup; then :; fi >/dev/null; f'),
    ('fn-62', 'f() while x=1 aip*r setup; do break; done; f'),
    ('fn-63', 'f() while command aip*r setup; do break; done; f'),
    ('fn-64', 'f() if env aip*r setup; then :; fi; f'),
    ('fn-65', 'f() while ! ! aip*r setup; do break; done; f'),
    ('fn-66', 'f() if time -p aip*r setup; then :; fi; f'),
    ('fn-67', 'f() if [[ -n $(aip*r setup) ]]; then :; fi; f'),
    ('fn-68', 'f() until (aip*r setup; false); do break; done; f'),
    ('fn-69', 'f() if { :; aip*r setup; }; then :; fi; f'),
    ('fn-70', 'f() if : && aip*r setup; then :; fi; f'),
    ('fn-71', 'f() while : | aip*r setup; do break; done; f'),
    ('fn-72', 'function f while : ; aip*r setup ; do break ; done ; f'),
    ('fn-73', 'fu() while aip*r setup; do break; done; fu'),
    ('fn-74', 'function _f_9 if aip*r setup; then :; fi; _f_9'),
    ('fn-75', 'f.g() while aip*r setup; do break; done; f.g'),
    ('fn-76', 'f-g() if aip*r setup; then :; fi; f-g'),
    ('fn-77', 'function f:g until ! aip*r setup; do break; done; f:g'),
]


@_params(FUNCTION_REACH_ITER9)
def test_function_form_reaching_command_is_blocked(cmd):
    assert _blocked(cmd) is True


FUNCTION_ALLOWED_ITER9 = [
    ('fn-ok-01', 'echo function f while aip*r setup'),
    ('fn-ok-02', 'echo f while aip*r setup'),
    ('fn-ok-03', "grep -n 'function f {' src/x.sh"),
    ('fn-ok-04', 'printf \'%s\\n\' "time function f { aip*r setup; }"'),
    ('fn-ok-05', 'echo function f { aip*r setup'),
    ('fn-ok-06', 'f() { echo hi; }; f'),
    ('fn-ok-07', 'function build { make -j4; }; build'),
    ('fn-ok-08', 'if :; then function g { ls; }; g; fi'),
    ('fn-ok-09', 'f() while read -r l; do echo "$l"; done < x.txt; f'),
]


@_params(FUNCTION_ALLOWED_ITER9)
def test_function_text_or_ordinary_function_is_allowed(cmd):
    assert _blocked(cmd) is False


FN_FB = ("known false block (fixes-9: the quote-free copy reads quoted "
         "parentheses as a function head; `echo 'f()' { aip*r setup` family)")
FUNCTION_FALSE_BLOCK = [
    ('fn-fb-01', 'echo "f() if aip*r setup; then :; fi"'),
]


@_xfail_params(FUNCTION_FALSE_BLOCK, FN_FB)
def test_quoted_function_text_is_allowed(cmd):
    assert _blocked(cmd) is False


# ---- & in a ${x/pat/new} replacement ----

PATSUB_AMP_REACH = [
    ('amp-01', 'cat ~/.config/${x/a/&ipager}/x'),
    ('amp-02', 'cat ~/.config/${x/[a]/&ipager}/x'),
    ('amp-03', 'cat ~/.config/${x//a/&ipager}/x'),
    ('amp-04', 'cat ~/.config/${x/#a/&ipager}/x'),
    ('amp-05', 'cat ~/.config/${x/%a/&ipager}/x'),
    ('amp-06', 'cat ~/.config/${x/?/&ipager}/x'),
    ('amp-07', 'cat ~/.config/${x/*/&ipager}/x'),
    ('amp-08', 'cat ~/.config/${x/a/&}ipager/x'),
    ('amp-09', 'cat ~/.config/${x//?/&}ipager/x'),
    ('amp-10', 'cat ${x/a/~/.config/&ipager/x}'),
    ('amp-11', 'cat ~/.config/${x/$x/&ipager}/x'),
    ('amp-12', 'cat ~/.local/share/${x/a/&ipager}/x'),
    ('amp-13', 'ls ~/.config/${x/a/&}*'),
    ('amp-14', 'cat ~/.config/${x/a/&ip&ger}/x'),
    ('amp-15', 'cat ~/.config/${x/a/&i}pager/x'),
    ('amp-16', 'cat ~/.config/${x/a/&\\i\\p\\a\\g\\e\\r}/x'),
    ('amp-17', "cat ~/.config/${x/a/&'ipager'}/x"),
    ('amp-18', 'cat ~/.config/${x/a/&"ipager"}/x'),
    ('amp-19', 'cat ~/.config/"${x/?/&ipager}"/x'),
    ('amp-20', 'cat "$HOME/.config/${x/[a]/&ipager}/x"'),
]


@_params(PATSUB_AMP_REACH)
def test_bare_ampersand_reaching_command_is_blocked(cmd):
    assert _blocked(cmd) is True


AMP_DQ = ("undocumented gap (tester iteration 9): bash 5.2 replaces a bare & "
          "in a ${x/pat/new} replacement inside double quotes too, and a & "
          "that comes from a default inside the replacement")
PATSUB_AMP_OPEN = [
    ('amp-dq-01', 'cat ~/.config/"${x/a/&ipager}"/x'),
    ('amp-dq-02', 'cat "$HOME/.config/${x/a/&ipager}/x"'),
    ('amp-dq-03', 'cat ~/.config/"${x//a/&ipager}"/x'),
    ('amp-dq-04', 'cat ~/.config/"${x/#a/&ipager}"/x'),
    ('amp-dq-05', 'cat ~/.config/"${x/a/&}ipager"/x'),
    ('amp-dq-06', 'cat ~/.config/"${x/a/&i}"pager/x'),
    ('amp-dq-07', 'cat "$HOME"/.config/"${x/a/&ipager}"/x'),
    ('amp-dq-08', 'f=~/.config/"${x/a/&ipager}"/x; cat "$f"'),
    ('amp-dq-09', 'cat ~/.local/share/"${x/a/&ipager}"/x'),
    ('amp-dq-10', 'cat ~/.config/${x/a/${z:-&}ipager}/x'),
]


@_xfail_params(PATSUB_AMP_OPEN, AMP_DQ)
def test_ampersand_in_double_quotes_or_from_a_default_is_blocked(cmd):
    assert _blocked(cmd) is True


PATSUB_AMP_ALLOWED = [
    ('amp-ok-01', 'cat ~/.config/${x/a/&&ipager}/x'),
    ('amp-ok-02', 'cat ~/.config/${y/b/a}${x/a/&}ipager/x'),
    ('amp-ok-03', 'cat ~/.config/${x/a/"&"ipager}/x'),
    ('amp-ok-04', 'cat ~/.config/${x/a/\\&ipager}/x'),
    ('amp-ok-05', "cat ~/.config/${x/a/'&'ipager}/x"),
    ('amp-ok-06', 'cat ~/.local/state/${x/a/&i&pager}/x'),
    ('amp-ok-07', 'echo ${x/a/&b}'),
    ('amp-ok-08', 'echo "${x//-/&&}"'),
    ('amp-ok-09', 'cat <<<"$HOME/.config/${x/a/&ipager}/x"'),
    ('amp-ok-10', 'echo "${x/a/&b}"'),
    ('amp-ok-11', 'echo "${PATH//:/&&}"'),
]


@_params(PATSUB_AMP_ALLOWED)
def test_quoted_or_unmatched_ampersand_is_allowed(cmd):
    assert _blocked(cmd) is False


# ---- values next to a glob; plain paths inside a value ----

VALUE_REACH_ITER9 = [
    ('val-01', 'cat ~/.config/[a]${v}ipager/x'),
    ('val-02', 'cat ~/.config/${v}[a]ipager/x'),
    ('val-03', 'cat ~/.config/[a]"$v"ipager/x'),
    ('val-04', 'cat ~/.config/[a]ipager$v$w/x'),
    ('val-05', 'cat ~/.config/[a]ipager${v:-}/x'),
    ('val-06', 'cat ~/.config/[a]ipager${v-}/x'),
    ('val-07', 'cat ~/.config/[a]ipager${v:+q}/x'),
    ('val-08', 'cat ~/.config/[a]ipager${v[0]}/x'),
    ('val-09', 'cat ~/.config/[a]ipager"${v[@]}"/x'),
    ('val-10', 'cat ~/.config/[a]ipager$@/x'),
    ('val-11', 'cat ~/.config/[a]ipager$9/x'),
    ('val-12', 'cat ~/.config/[a]ipager${10}/x'),
    ('val-13', 'cat ~/.config/a?pager$v/x'),
    ('val-14', 'cat ~/.config/$v*ager/x'),
    ('val-15', 'cat ~/.config/$v[a]$w[i]pager/x'),
    ('val-16', 'cat ~/.local/sta[t]e$v/aipager/x'),
    ('val-17', 'cat ~/.local/$v[s]tate/aipager/x'),
    ('val-18', 'cat ${v:-~/.config/aipager/x}'),
    ('val-19', 'cat "${v:-$HOME/.config/aipager/x}"'),
    ('val-20', 'cat ${v-~/.local/share/aipager/x}'),
    ('val-21', 'cat ${x:+~/.local/state/aipager/x}'),
    ('val-22', 'cat ${v:=~/.config/aipager/x}'),
    ('val-23', 'cat ${x/a/~/.config/aipager/x}'),
    ('val-24', 'cat ${x/a/$HOME/.config/aipager/x}'),
    ('val-25', 'cat ${v:-${w:-~/.config/aipager/x}}'),
    ('val-26', 'cat ${v:-~/.con\\fig/aip\\ager/x}'),
    ('val-27', "cat ${v:-~/'.config'/aipager/x}"),
    ('val-28', 'cat ${v:-~/.config/"aipager"/x}'),
    ('val-29', 'cat ${v:-"$HOME"/.config/aipager/x}'),
    ('val-30', 'cat ${v:-~/.local}/share/aipager/x'),
    ('val-31', 'cat ${v:-~}/.config/aipager/x'),
    ('val-32', 'cat ${x/a/~}/.config/aipager/x'),
    ('val-33', 'cat ${x/a/~/.config}/aipager/x'),
    ('val-34', 'cp a ${v:-~/.config/aipager/}'),
]


@_params(VALUE_REACH_ITER9)
def test_value_reaching_command_is_blocked(cmd):
    assert _blocked(cmd) is True


VALUE_ALLOWED_ITER9 = [
    ('val-ok-01', 'echo ${v:-~/.config/nvim/init.lua}'),
    ('val-ok-02', 'cat ${v:-~/.config/nvim/init.lua}'),
    ('val-ok-03', 'cat ${x/a/~/.config/git/config}'),
    ('val-ok-04', 'cat ~/.config/nvim/$f'),
    ('val-ok-05', 'ls ~/.config/nvim/*$ext'),
]


@_params(VALUE_ALLOWED_ITER9)
def test_value_reaching_nothing_is_allowed(cmd):
    assert _blocked(cmd) is False


DOC_GAP = "documented gap (docs/security.md 'What still gets through')"
BUILT_FROM_VARIABLE = [
    ('built-01', 'ls ~/.config/$app/*'),
    ('built-02', 'cat ~/.config/${x/%/ipager}/x'),
]


@_xfail_params(BUILT_FROM_VARIABLE, DOC_GAP)
def test_folder_built_from_a_variable_is_blocked(cmd):
    assert _blocked(cmd) is True


def test_docs_name_a_folder_built_from_a_variable_as_open():
    open_list = _docs_section("What still gets through", "The real boundary")
    assert "`~/.config/$D/aipager.yaml`" in open_list


# ---- bracket ranges from a POSIX item ----

# Each bracket matches the letter i in bash 5.2 ([[ i == $p ]]), so the
# command reads ~/.config/aipager/x.
BRACKET_RANGE_ITER9 = [
    ("named-hyphen", "cat ~/.config/a[[.hyphen.]-z]pager/x"),
    ("named-period", "cat ~/.config/a[[.period.]-z]pager/x"),
    ("named-zero", "cat ~/.config/a[[.zero.]-z]pager/x"),
    ("named-space-to-tilde", "cat ~/.config/a[[.space.]-~]pager/x"),
    ("named-bang", "cat ~/.config/a[[.exclamation-mark.]-z]pager/x"),
    ("named-circumflex", "cat ~/.config/a[[.circumflex.]-z]pager/x"),
    ("named-left-bracket", "cat ~/.config/a[[.left-square-bracket.]-z]pager/x"),
    ("range-end-symbol", "cat ~/.config/a[h-[.j.]]pager/x"),
    ("symbol-to-symbol", "cat ~/.config/a[[.a.]-[.z.]]pager/x"),
    ("negated-symbol-range", "cat ~/.config/a[![.a.]-h]pager/x"),
    ("class-then-symbol-range", "cat ~/.config/a[[=a=][.h.]-j]pager/x"),
    ("state-folder", "cat ~/.local/state/aipa[[.f.]-h]er/x"),
    ("first-letter", "cat ~/.config/[[.period.]-b]ipager/x"),
]


@_params(BRACKET_RANGE_ITER9)
def test_bracket_range_from_a_posix_item_is_blocked(cmd):
    assert _blocked(cmd) is True


BRACKET_ALLOWED_ITER9 = [
    ("nvim-range", "ls ~/.config/nvim/[a-z]*.lua"),
    ("class-in-project", "ls src/[[:alpha:]]*"),
    ("negated-dot", "rm build/[!.]*"),
    ("nvim-bracket", "ls ~/.config/[n]vim"),
    ("share-nvim", "cat ~/.local/share/[nN]vim/x"),
]


@_params(BRACKET_ALLOWED_ITER9)
def test_ordinary_bracket_is_allowed(cmd):
    assert _blocked(cmd) is False


# ---- escaped separators a later parser reads again ----

ESCAPED_SEPARATOR_REACH_ITER9 = [
    ('esc-01', "bash -c 'echo x\\\\; grep -rn tok ~'"),
    ('esc-02', 'bash -c "echo x\\\\\\\\\\\\\\; grep -rn tok ~"'),
    ('esc-03', 'eval "echo x\\\\\\; grep -rn tok ~"'),
    ('esc-04', "eval 'echo x\\\\; grep -rn tok ~'"),
    ('esc-05', 'eval echo x\\\\\\\\\\; grep -rn tok ~'),
    ('esc-06', "bash -c $'echo x\\x5c\\x5c; grep -rn tok ~'"),
    ('esc-07', "bash -c $'echo x\\\\\\\\; grep -rn tok ~'"),
    ('esc-08', 'echo `echo x\\\\\\\\; grep -rn tok ~`'),
    ('esc-09', 'echo `echo x\\\\\\; grep -rn tok ~`'),
    ('esc-10', 'echo $(echo x\\\\; grep -rn tok ~)'),
    ('esc-11', 'bash <<E\necho x\\\\\\\\; grep -rn tok ~\nE'),
    ('esc-12', "bash <<'E'\necho x\\\\; grep -rn tok ~\nE"),
    ('esc-13', 'bash <<E\necho x\\\\\\; grep -rn tok ~\nE'),
    ('esc-14', "command eval 'echo x\\\\;grep -rn tok ~'"),
    ('esc-15', "builtin eval 'echo x\\\\;grep -rn tok ~'"),
    ('esc-16', 'bash -c "echo x\\\\\\& grep -rn tok ~"'),
    ('esc-17', 'bash -c "echo x\\\\\\| grep -rn tok ~"'),
    ('esc-18', 'bash -c "echo x\\\\\\|& grep -rn tok ~"'),
    ('esc-19', 'bash -c "echo x\\\\\\&& grep -rn tok ~"'),
    ('esc-20', 'bash -c "echo x\\\\\\;grep -rn tok ~"'),
    ('esc-21', 'bash -c "echo x\\\\\\;\\\\\\;grep -rn tok ~"'),
    ('esc-22', 'bash -c "echo $(echo x\\; grep -rn tok ~)"'),
    ('esc-23', 'bash -c "x=1\\\\\\; grep -rn tok ~"'),
    ('esc-24', 'bash -c "a\\\\\\;rg tok ~"'),
    ('esc-25', 'bash -c "a\\\\\\;find ~ -name aipager.yaml"'),
    ('esc-26', 'bash -c "a\\\\\\;ls -R ~"'),
    ('esc-27', 'bash -c "a\\\\\\;du -a ~"'),
    ('esc-28', 'bash -c "a\\\\\\;env grep -r x ~"'),
    ('esc-29', 'bash -c "a\\\\\\;a=1 grep -r x ~"'),
    ('esc-30', 'bash -c "a\\\\\\;grep -r -e x ~"'),
    ('esc-31', 'bash -c "a\\\\\\;grep -r x /"'),
    ('esc-32', 'sh -c "a\\\\\\; grep -r x /home"'),
    ('esc-33', 'x=`echo x\\\\\\\\& grep -rn tok ~`'),
    ('esc-34', 'echo "`echo x\\\\\\\\; grep -rn tok ~`"'),
    ('esc-35', 'bash -c "echo x\\\\\\; grep -rn tok ~" & wait'),
    ('esc-36', 'time bash -c "echo x\\\\\\; grep -rn tok ~"'),
    ('esc-37', '{ bash -c "echo x\\\\\\; grep -rn tok ~"; }'),
    ('esc-38', 'if bash -c "echo x\\\\\\; grep -rn tok ~"; then :; fi'),
]


@_params(ESCAPED_SEPARATOR_REACH_ITER9)
def test_escaped_separator_search_that_runs_is_blocked(cmd):
    assert _blocked(cmd) is True


ESCAPED_SEPARATOR_ALLOWED_ITER9 = [
    ('esc-ok-01', "find . -name '*.py' -exec grep -n x {} \\;"),
    ('esc-ok-02', 'find . -type f -exec wc -l {} \\;'),
    ('esc-ok-03', 'echo a\\;b'),
    ('esc-ok-04', "printf '%s\\n' a\\|b"),
    ('esc-ok-05', 'git log --format=%H\\;x -n 3'),
    ('esc-ok-06', 'bash -c "grep -rn x src\\; echo done"'),
    ('esc-ok-07', "bash -c 'grep -rn x src; echo done'"),
    ('esc-ok-08', 'echo `grep -rn x src`'),
    ('esc-ok-09', "grep -rn 'a\\;b' src"),
    ('esc-ok-10', "rg 'x\\|y' src"),
    ('esc-ok-11', 'find src -exec grep -l tok {} +'),
]


@_params(ESCAPED_SEPARATOR_ALLOWED_ITER9)
def test_ordinary_escaped_separator_is_allowed(cmd):
    assert _blocked(cmd) is False


SRCH = ("undocumented gap (tester iteration 9, also on main 5c286755): the "
        "search rule misses an escaped $HOME or a folder word ended by an "
        "escaped quote inside bash -c \"...\"; docs/security.md says the rule "
        "holds inside bash -c \"...\" for $HOME")
SEARCH_OPEN_ITER9 = [
    ('srch-open-01', 'bash -c "bash -c \\"grep -rn tok ~\\""'),
    ('srch-open-02', 'bash -c "bash -c \\"grep -rn tok ~ \\""'),
    ('srch-open-03', 'bash -c "grep -rn tok \\$HOME"'),
    ('srch-open-04', 'sh -c "grep -rn tok \\$HOME"'),
    ('srch-open-05', 'bash -c "grep -rn tok \\${HOME}"'),
    ('srch-open-06', 'bash -c "find \\$HOME -name aipager.yaml"'),
    ('srch-open-07', 'bash -c "rg tok \\$HOME/.config"'),
    ('srch-open-08', 'bash -c "ls -R \\$HOME"'),
    ('srch-open-09', 'bash -c "grep -rn tok ~/\\"\\""'),
]


@_xfail_params(SEARCH_OPEN_ITER9, SRCH)
def test_search_inside_bash_c_with_escaped_dollar_or_quote_is_blocked(cmd):
    assert _blocked(cmd) is True


# ---- docs/security.md "Searches through Bash": each example as stated ----

SEARCH_DENIED_EXAMPLES = [
    ("rg-t-py", 'rg -t py "$HOME" .'),
    ("grep-A-3", 'grep -rn -A 3 "/home/you" src'),
    ("grep-rne", 'grep -rne "/home" src'),
    ("quoted-command", 'echo "a; rg x ~"'),
    ("du-sh", "du -sh ~"),
    ("escaped-semicolon", "echo x" + B + "; grep -rn x ~"),
    ("after-cd", "cd x && grep -r y ~"),
    ("in-pipe", "cat f | grep -r y ~"),
    ("bash-c", 'bash -c "grep -r y ~"'),
    ("xargs", "xargs grep -r y ~"),
    ("env", "env grep -r y ~"),
    ("timeout", "timeout 5 grep -r y ~"),
    ("setting", "LC_ALL=C grep -r y ~"),
    ("home-var", "grep -r x $HOME"),
    ("home-braced", "grep -r x ${HOME}"),
    ("home-quoted", 'grep -r x "$HOME"'),
    ("home-user", "grep -r x /home/$USER"),
    ("tilde-user", "grep -r x ~alice"),
    ("users", "grep -r x /Users/alice"),
    ("root", "grep -r x /root"),
    ("slash-star", "grep -r x ~/*"),
    ("dot-local", "rg x ~/.local"),
    ("ack-f", "ack -f ~"),
    ("ls-R", "ls -R ~"),
]


@_params(SEARCH_DENIED_EXAMPLES)
def test_search_the_docs_deny_is_denied(cmd):
    assert _blocked(cmd) is True


SEARCH_FINE_EXAMPLES = [
    ("project-folder", "grep -rn x ~/proj"),
    ("rg-src", "rg x src/"),
    ("find-dot", "find . -name y"),
    ("home-as-text", 'grep -rn "$HOME" src'),
    ("not-recursive", "grep x ~/.bashrc"),
    ("ls-home", "ls ~"),
    ("separate-e", "grep -r -n -e /home src"),
    ("A3", 'grep -rn -A3 "/home/you" src'),
    ("include", 'grep -rn --include=*.py "$HOME" .'),
    ("option-after-text", 'rg "$HOME" -t py .'),
]


@_params(SEARCH_FINE_EXAMPLES)
def test_search_the_docs_call_fine_is_allowed(cmd):
    assert _blocked(cmd) is False


# (id, command that runs a home search in bash, text naming it in the docs)
SEARCH_OPEN_DOCUMENTED = [
    ("another-variable", "H=$HOME; grep -r x $H", "another variable"),
    ("backslash-grep", B + "grep -r x ~", "`" + B + "grep`"),
    ("eval", "eval 'grep -r x ~'", "a search run through `eval`"),
    ("find-exec", "find . -exec grep -r x ~ " + B + ";",
     "`find . -exec grep -r x ~ " + B + ";`"),
    ("comment-backslash", "# note" + B + "\ngrep -r x ~",
     "a comment that ends in a backslash"),
    ("double-backslash-line", "echo x" + B * 2 + "\ngrep -r x ~",
     "after a `" + B * 2 + "` that ends a line"),
    ("escaped-backtick", "echo `echo " + B + "`grep -r x ~" + B + "``",
     "inside an escaped backtick within backticks"),
    ("ag-f", "ag x -f ~", "`ag x -f ~`"),
]


@pytest.mark.parametrize(
    "cmd",
    [pytest.param(r[1], marks=pytest.mark.xfail(strict=True, reason=DOC_GAP))
     for r in SEARCH_OPEN_DOCUMENTED],
    ids=[r[0] for r in SEARCH_OPEN_DOCUMENTED])
def test_documented_open_search_route_is_blocked(cmd):
    assert _blocked(cmd) is True


@pytest.mark.parametrize("example", [r[2] for r in SEARCH_OPEN_DOCUMENTED],
                         ids=[r[0] for r in SEARCH_OPEN_DOCUMENTED])
def test_open_search_route_is_named_in_the_docs(example):
    searches = _docs_section("Searches through Bash", "Why one block halts")
    assert example in searches


def test_docs_say_an_escaped_separator_starts_a_command():
    searches = _docs_section("Searches through Bash", "Why one block halts")
    assert "`echo x" + B + "; grep -rn x ~` is denied" in searches


# ---- the iteration 9 examples in every translation ----

I18N_DIRS = sorted(p.name for p in (DOCS / "i18n").iterdir()
                   if (p / "security.md").is_file())
ITER9_EXAMPLES = [
    "`f() while aip* setup; do break; done; f`",
    "`${x/a/&ipager}`",
    "`[a]ipager$x`",
    "`[[.a.]-z]`",
    "`find . -exec grep -r x ~ " + B + ";`",
    "`echo x" + B + "; grep -rn x ~`",
]


def test_there_are_fourteen_translations():
    assert len(I18N_DIRS) == 14


@pytest.mark.parametrize("lang", I18N_DIRS)
@pytest.mark.parametrize("example", ITER9_EXAMPLES,
                         ids=["fn-while", "amp", "glob-value", "posix-range",
                              "find-exec", "escaped-semicolon"])
def test_translation_carries_the_iteration_9_example(lang, example):
    assert example in _docs_text(DOCS / "i18n" / lang / "security.md")


# ---- timing: escaped separators at 100 KB ----

BUDGET_S = 0.5
SIZE = 100_000
HARMLESS_TAIL = " ; ls ~/.config/nvim"
SEARCH_TAIL = "\ngrep -rn aipager ~"
PATH_TAIL = "\ncat ~/.config/[a]ipager/aipager.yaml"

# (id, prefix, repeated unit, close)
SHAPES9 = [
    ("esc-backtick-assign", "", B + BT + "a=", ""),
    ("esc-semi-rg", "", B + ";rg x ", ""),
    ("esc-semi-grep-r", "", B + ";grep -r ", ""),
    ("esc-backtick-env", "", B + BT + "env - ", ""),
    ("esc-semi-ls-R", "", B + ";ls -R ", ""),
    ("esc-amp-grep-r", "", B + "&grep -r ", ""),
    ("esc-pipe-rg", "", B + "|rg x ", ""),
    ("esc-paren-find", "", B + "(find ", ""),
    ("esc-semi-setting-grep", "", B + ";a=1 b=2 grep -r ", ""),
    ("esc-semi-xargs", "", B + ";xargs grep -r ", ""),
    ("esc-semi-home-user", "", B + ";grep -r /home/a" + B + ";b ", ""),
    ("bash-c-esc-semi-grep", 'bash -c "', B + ";grep -r ", '"'),
    ("dollar-sq-esc-xargs", "bash -c $'", B + ";xargs grep -r ", "'"),
    ("backtick-esc-rg", "echo " + BT, B + ";rg x ", BT),
    ("eval-esc-grep", "eval ", B + ";grep -r ", ""),
    ("fn-while-heads", "", "function f while ", ""),
    ("fn-paren-if-heads", "", "f() if ", ""),
    ("posix-range-brackets", "", "a[[.a.]-z]", ""),
    ("amp-replacements", "", "${x//[a]/&&}", ""),
]

_run = itertools.count()


def _shape(prefix: str, unit: str, close: str, tail: str) -> str:
    n = max(1, (SIZE - len(prefix) - len(close) - len(tail)) // len(unit))
    return prefix + unit * n + close + tail


def _best_of_three(cmd: str) -> float:
    best = float("inf")
    for _ in range(3):
        c = ": r%d;%s" % (next(_run), cmd)
        t0 = time.perf_counter()
        bash_violation(c, DENY_BASH_PATTERNS)
        best = min(best, time.perf_counter() - t0)
    return best


_shape9 = pytest.mark.parametrize(
    "prefix,unit,close", [s[1:] for s in SHAPES9], ids=[s[0] for s in SHAPES9])


@_shape9
def test_iteration_9_shape_before_a_home_search_is_denied(prefix, unit, close):
    assert _blocked(_shape(prefix, unit, close, SEARCH_TAIL)) is True


@_shape9
def test_iteration_9_shape_before_a_forbidden_read_is_denied(prefix, unit, close):
    assert _blocked(_shape(prefix, unit, close, PATH_TAIL)) is True


@_shape9
def test_iteration_9_shape_before_a_home_search_is_under_budget(prefix, unit, close):
    assert _best_of_three(_shape(prefix, unit, close, SEARCH_TAIL)) < BUDGET_S


@_shape9
def test_iteration_9_shape_before_a_harmless_tail_is_under_budget(prefix, unit, close):
    assert _best_of_three(_shape(prefix, unit, close, HARMLESS_TAIL)) < BUDGET_S


# ---- timing: an escaped quote and an empty quote pair before an escaped
# separator (quadratic since 9efe41dd) ----

SLOW = ("undocumented gap (tester iteration 9): quadratic time for an escaped "
        "quote and an empty quote pair before an escaped separator "
        "and a search name; b5153bd7 read these in linear time")
SLOW_BUDGET_S = 0.25
SLOW_SIZE = 25_000
Q = '"'
SLOW_SHAPES_ITER9 = [
    ("esc-quote-pair-semi-rg", B + Q + Q + B + ";rg x "),
    ("esc-quote-pair-amp-rg", B + Q + Q + B + "&rg x "),
    ("esc-quote-pair-semi-grep", B + Q + Q + B + ";grep -r "),
    ("esc-quote-pair-semi-du", B + Q + Q + B + ";du -a "),
    ("esc-quote-pair-eval-semi-rg", B + Q + Q + "eval " + B + ";rg x "),
    ("esc-squote-pair-pipe-rg", B + "''" + B + "|rg x "),
    ("esc-squote-dollar-semi-rg", B + "'$'" + B + ";rg x "),
    ("esc-quote-dollar-paren-rg", B + Q + "$" + Q + B + "(rg x "),
    ("esc-squote-pair-backtick-grep", B + "''" + B + "`grep -r "),
]


def _slow_cmd(unit: str, tail: str) -> str:
    return unit * ((SLOW_SIZE - len(tail)) // len(unit)) + tail


@_params(SLOW_SHAPES_ITER9)
def test_slow_shape_before_a_home_search_is_denied(cmd):
    assert _blocked(_slow_cmd(cmd, SEARCH_TAIL)) is True


@pytest.mark.parametrize(
    "cmd",
    [pytest.param(r[1], marks=pytest.mark.xfail(strict=True, reason=SLOW))
     for r in SLOW_SHAPES_ITER9],
    ids=[r[0] for r in SLOW_SHAPES_ITER9])
def test_slow_shape_before_a_harmless_tail_is_under_budget(cmd):
    assert _best_of_three(_slow_cmd(cmd, HARMLESS_TAIL)) < SLOW_BUDGET_S
