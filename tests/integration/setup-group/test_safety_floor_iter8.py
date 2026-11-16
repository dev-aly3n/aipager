"""Iteration 8 of the safety floor checks (design.md success criteria 15
and 16; docs/security.md "aipager's own commands and folders" and
"Searches through Bash").

Whole commands. Every reaching row was run by real bash 5.2 in a throwaway
HOME with PATH empty: each bare command bash cannot find goes to a
recorder (``command_not_found_handle``) that logs its working folder and
full argv, ``bash``/``sh`` run the real bash so ``bash -c`` text runs too,
and strace logs every file bash opens and every program it tries to start.
A row "reaches" when it reads or writes a file in ~/.config/aipager or
~/.local/{share,state}/aipager, runs a refused aipager form (``aipager
setup|config|...``, ``python3 -m aipager.cli``, ``pkill aipager``), or,
for the search rows, runs ``grep -r``/``rg``/``find``/``ls -R``/``du``/
``tree`` on the home folder, ``~/.config`` or ``/``. The tester's harness
lives in the ship artifacts (tester8-tools/oracle.py, hp.py,
searchprobe.py).

Territory covered here for the first time: ``${...}`` in every position
after iteration 8's change (nested operations, ``@Q``, ``${!p*}``, array
replacements, offsets holding ``$(..)``, quotes inside operations, and
operations inside here-documents, ``=~``, subscripts, arithmetic,
function bodies and ``case`` patterns); the ``function NAME`` forms
(parentheses, a newline before ``{``, a ``(`` body, nesting, odd names,
other compound bodies); escaped separators (``\\;``, ``\\&``, ``\\|``,
backslash-newline, quoted separators) before a recursive home search.

Gaps (strict xfail, project convention for known gaps):

- FUNCTION_KEYWORD_POSITIONS: ``function NAME {`` is a program position
  only where ``function`` starts a command after ``;``/``&&``/``(``/
  start; after ``{``, ``then``, ``do``, ``!``, ``time`` or inside another
  function's body a glob program in its body runs and is allowed.
  docs/security.md names ``function f { aip* setup; }; f`` among the
  refused forms.
- FUNCTION_COMPOUND_BODY: a function whose body is an ``if``/``while``/
  ``until`` compound (``f() while aip*r setup; do ...``) with the glob
  program in the condition.
- ESCAPED_SEPARATOR_REGRESSION: since d6d18a17 the search rules start no
  command at ``\\;``/``\\&``/``\\|``; inside ``bash -c "..."``, ``sh -c
  "..."`` and backtick bodies a ``\\\\`` pair collapses to one backslash
  first, so three backslashes before a separator leave a real separator in
  the text bash finally runs. Refused at 89df8edc and on main 5c286755;
  docs/security.md says the search rule holds "inside ``bash -c "..."``".
- BRACKET_RANGES: a bracket whose range starts at a collating symbol
  (``[[.a.]-z]``) or follows the ``]`` bash reads as a member right after
  an equivalence class (``[[=a=]]h-j]``) can match ``i`` and reaches;
  docs/security.md says a bracket expression that could match is refused
  and one bash reads in more than one way is refused. Present at
  00df0503 and on main too (difffuzz seed 8901 found it).
- PATSUB_AMPERSAND: bash 5.2 turns ``&`` in a ``${x/pat/new}``
  replacement into the matched text (``patsub_replacement`` is on by
  default), so ``~/.config/${x/a/&ipager}/x`` reaches with x=a.

Only the documented surface is used: ``safety.bash_violation`` with
``safety.DENY_BASH_PATTERNS`` (entrypoints.md "Safety check surface").
Timing cases take the best of three runs, and every run gets a different
command (a distinct leading ``: rN;``), so no cache keyed by the command
text can serve the second and third runs.
"""

from __future__ import annotations

import itertools
import re
import time
from pathlib import Path

import pytest

from aipager.safety import DENY_BASH_PATTERNS, bash_violation

B = chr(92)  # a backslash
SECURITY_MD = Path(__file__).resolve().parents[3] / "docs" / "security.md"


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


def _docs_text() -> str:
    return re.sub(r"\s+", " ", SECURITY_MD.read_text(encoding="utf-8"))


def _docs_section(start: str, end: str) -> str:
    text = _docs_text()
    i = text.index(start)
    return text[i:text.index(end, i)]


# ---- ${...} in every position: reaching rows that stay refused ----

# Each row reaches in bash (x=a, y=b, z/w/unset_v/q unset) and is refused
# at 7f209945.
PARAM_REACH = [
    ('param-01', 'cat ${x/${y#b}a/${z:-~/.config/[a]ipager/x}}'),
    ('param-03', 'cat ${x/a/${z:-${w:-~/.config/[a]ipager/x}}}'),
    ('param-04', 'cat ${x/a/${y/b/~/.config/[a]ipager/x}}'),
    ('param-05', 'cat ${x/a/${y/b/${z:-~/.config/[a]ipager/x}}}'),
    ('param-06', 'cat ${x//${y:0:0}a/~/.config/[a]ipager/x}'),
    ('param-07', 'cat ~/.config/[a]ipager/x${unset_v@Q}'),
    ('param-10', 'cat ~/.config/[a]ipager/${!zzq*}x'),
    ('param-11', 'cat ~/.config/[a]ipager/x${!zzq@}'),
    ('param-12', 'a=(q); cat ${a[@]/q/~/.config/[a]ipager/x}'),
    ('param-13', 'a=(q r); cat ${a[@]//?/~/.config/[a]ipager/x}'),
    ('param-14', 'a=(q); cat ${a[0]/q/~/.config/[a]ipager/x}'),
    ('param-15', 'a=(q); cat ${a[*]/#q/~/.config/[a]ipager/x}'),
    ('param-16', 'declare -A m=([k]=q); cat ${m[k]/q/~/.config/[a]ipager/x}'),
    ('param-17', 'cat ~/.config/[a]ipager/x${x:$(echo 1):$(echo 0)}'),
    ('param-18', 'echo ${x:$(cat ~/.config/[a]ipager/x):0}'),
    ('param-19', 'echo ${x:0:$(cat ~/.config/[a]ipager/x)}'),
    ('param-20', 'echo ${x:$(aip*r setup):0}'),
    ('param-21', 'echo ${x:0:`aip*r config`}'),
    ('param-22', 'echo ${x:$((1)):$(cat ~/.local/share/[a]ipager/x)}'),
    ('param-23', 'cat ${x/a/"$HOME"/.config/[a]ipager/x}'),
    ('param-24', "cat ${x/a/~/'.config'/[a]ipager/x}"),
    ('param-25', 'cat ${x/a/~/".config"/[a]ipager/x}'),
    ('param-26', 'cat ${x/"a"/~/.config/[a]ipager/x}'),
    ('param-27', "cat ${x/'a'/~/.config/[a]ipager/x}"),
    ('param-28', 'cat ${x/a/~/.config/"[a]"ipager/x}'),
    ('param-30', 'cat ${x/a/~}/.config/[a]ipager/x'),
    ('param-31', 'cat ${x/a/~/.con}fig/[a]ipager/x'),
    ('param-32', 'cat ${x/a/~/.config/[a]}ipager/x'),
    ('param-35', 'cat ${x/%a/~/.config/[a]ipager/x}${y/b/}'),
    ('param-36', 'cat ${x/$(cat ~/.config/[a]ipager/x)/b}'),
    ('param-37', 'cat ${x#$(cat ~/.config/[a]ipager/x)}'),
    ('param-38', 'cat ${x%%`cat ~/.config/[a]ipager/x`}'),
    ('param-39', 'echo ${x^^$(cat ~/.config/[a]ipager/x)}'),
    ('param-40', 'echo ${x,,[$(cat ~/.config/[a]ipager/x)]}'),
    ('param-41', 'echo ${unset_v:?$(cat ~/.config/[a]ipager/x)}'),
    ('param-42', 'echo ${x/a/$(aip*r setup)}'),
    ('param-43', 'echo ${x/$(aip*r setup)/b}'),
    ('param-44', 'echo ${x#$(aip*r setup)}'),
    ('param-45', 'echo ${x@Q} $(aip*r setup)'),
    ('param-46', 'cat <<E\n${x/a/$(cat ~/.config/[a]ipager/x)}\nE'),
    ('param-47', 'cat <<E\n${x/a/$(aip*r setup)}\nE'),
    ('param-48', 'cat <<E\n${x#$(cat ~/.config/[a]ipager/x)}\nE'),
    ('param-49', 'cat <<E\n${x:$(cat ~/.config/[a]ipager/x):0}\nE'),
    ('param-50', 'cat <<E\n${x/a/${z:-$(cat ~/.config/[a]ipager/x)}}\nE'),
    ('param-51', '[[ a =~ ${x/a/$(cat ~/.config/[a]ipager/x)} ]]'),
    ('param-52', '[[ a =~ ${x/a/$(aip*r setup)} ]]'),
    ('param-53', '[[ ${x/a/$(aip*r setup)} == a ]]'),
    ('param-54', '[[ -n ${x#$(cat ~/.config/[a]ipager/x)} ]]'),
    ('param-55', 'a[${x/a/$(cat ~/.config/[a]ipager/x)}]=1'),
    ('param-56', 'a[${x/a/$(aip*r setup)}]=1'),
    ('param-57', 'echo ${a[${x/a/$(aip*r setup)}]}'),
    ('param-58', 'echo ${a[${x:$(cat ~/.config/[a]ipager/x):0}]}'),
    ('param-59', '(( ${x/a/$(aip*r setup)}1 ))'),
    ('param-60', 'echo $(( ${x/a/$(cat ~/.config/[a]ipager/x)}1 ))'),
    ('param-61', 'f(){ cat ${x/a/~/.config/[a]ipager/x}; }; f'),
    ('param-62', 'function f { cat ${x/a/~/.config/[a]ipager/x}; }; f'),
    ('param-63', 'function f { echo ${x/a/$(aip*r setup)}; }; f'),
    ('param-64', 'f() ( cat ${x/a/~/.local/share/[a]ipager/x} ); f'),
    ('param-65', 'case a in ${x/a/$(cat ~/.config/[a]ipager/x)}) :;; esac'),
    ('param-66', 'case a in ${x/a/$(aip*r setup)}) :;; esac'),
    ('param-67', 'case a in (${x#$(aip*r setup)}) :;; esac'),
    ('param-68', 'case $(cat ${x/a/~/.config/[a]ipager/x}) in *) :;; esac'),
    ('param-69', 'case a in a) cat ${x/a/~/.config/[a]ipager/x};; esac'),
    ('param-70', 'for f in ${x/a/~/.config/[a]ipager/x}; do cat $f; done'),
    ('param-71', 'cat ${x/a/~/.config/[a]ipager/x} # ${'),
    ('param-72', "echo '${' ; cat ${x/a/~/.config/[a]ipager/x}"),
    ('param-73', 'echo "${x/a/\'}\'}" ; cat ${x/a/~/.config/[a]ipager/x}'),
    ('param-74', 'echo ${x/a/\\}} ; cat ${x/a/~/.config/[a]ipager/x}'),
    ('param-76', 'cat ${x/a\\//b} ${x/a/~/.config/[a]ipager/x}'),
    ('param-79', 'cat ${x/a/~/.config/*/aipager.yaml}'),
    ('param-80', 'cat ${x/a/~/.config/a?pager/x}'),
    ('param-81', 'cat ${x/a/~/.c*/[a]ipager/x}'),
    ('param-83', 'shopt -s extglob; cat ${x/a/~/.config/@(aipager|q)/x}'),
    ('param-87', 'x=a; cat ${x/a/~/.config/[a]ipager/x}'),
    ('param-88', 'cat ${x:+${x/a/${unset_v:-~/.config/[a]ipager/x}}}'),
    ('param-91', 'cat ${unset_v-${x//a/~/.config/[a]ipager/x}}'),
    ('param-92', ': ${q:=~/.config/[a]ipager/x}; cat $q'),
]


@_params(PARAM_REACH)
def test_param_operation_reaching_command_is_blocked(cmd):
    assert _blocked(cmd) is True


# ---- function NAME forms: reaching rows that stay refused ----

FUNCTION_REACH = [
    ('fn-01', 'function f() { aip*r setup; }; f'),
    ('fn-02', 'function f\n{ aip*r setup; }\nf'),
    ('fn-03', 'function f\n{\naip*r setup\n}\nf'),
    ('fn-04', 'function f ( aip*r setup ); f'),
    ('fn-05', 'function f () ( aip*r setup ); f'),
    ('fn-06', 'function f() ( aip*r config ); f'),
    ('fn-08', 'function f { g() { aip*r setup; }; g; }; f'),
    ('fn-12', 'function f { [[ 1 ]] && aip*r setup; }; f'),
    ('fn-13', 'function f { if :; then aip*r setup; fi; }; f'),
    ('fn-14', 'function f if :; then aip*r setup; fi; f'),
    ('fn-15', 'function f for i in 1; do aip*r setup; done; f'),
    ('fn-19', 'function     f     { aip*r setup; }; f'),
    ('fn-20', 'function\tf\t{\taip*r setup; }; f'),
    ('fn-21', 'function f-x { aip*r setup; }; f-x'),
    ('fn-22', 'function f.x { aip*r setup; }; f.x'),
    ('fn-23', 'function f:x { aip*r setup; }; f:x'),
    ('fn-24', 'function @ { aip*r setup; }; @'),
    ('fn-25', 'function 1 { aip*r setup; }; 1'),
    ('fn-26', 'function f+ { aip*r setup; }; f+'),
    ('fn-27', 'function f% { aip*r setup; }; f%'),
    ('fn-28', 'function f, { aip*r setup; }; f,'),
    ('fn-29', 'function f^ { aip*r setup; }; f^'),
    ('fn-30', 'function é { aip*r setup; }; é'),
    ('fn-31', 'function f \\\n{ aip*r setup; }; f'),
    ('fn-32', 'function \\\nf { aip*r setup; }; f'),
    ('fn-33', 'function f # c\n{ aip*r setup; }\nf'),
    ('fn-34', 'echo $(function f { aip*r setup; }; f)'),
    ('fn-35', 'echo `function f { aip*r setup; }; f`'),
    ('fn-36', 'x=$(function f { aip*r setup; }; f)'),
    ('fn-37', '( function f { aip*r setup; }; f )'),
    ('fn-39', 'true && function f { aip*r setup; } && f'),
    ('fn-40', 'false || function f { aip*r setup; }; f'),
    ('fn-44', 'function f { aip*r setup; } >/dev/null; f'),
    ('fn-45', 'function f { aip*r setup; } 2>&1; f'),
    ('fn-46', 'function f { aipage? config; }; f'),
    ('fn-47', 'function f { [a]ipager uninstall -y; }; f'),
    ('fn-48', 'function f { python3 -m aipager.cli setup; }; f'),
    ('fn-49', 'function f { x=1 aip*r setup; }; f'),
    ('fn-50', 'function f { time aip*r setup; }; f'),
    ('fn-51', 'function f { ! aip*r setup; }; f'),
    ('fn-52', 'function f { aip*r setup & }; f'),
    ('fn-53', 'function f { :; aip*r setup; }; f'),
    ('fn-54', 'function f { : && aip*r setup; }; f'),
    ('fn-55', 'function f { : | aip*r setup; }; f'),
    ('fn-56', 'function f { { aip*r setup; }; }; f'),
    ('fn-57', 'function f { ( aip*r setup ); }; f'),
    ('fn-58', 'function f { $(aip*r setup); }; f'),
    ('fn-59', 'function f { cat ~/.config/[a]ipager/x; }; f'),
    ('fn-61', 'function f {\n  cat ~/.local/state/[a]ipager/x\n}\nf'),
    ('fn-63', 'function f() (cat <<E\n$(aip*r setup)\nE\n); f'),
    ('fn-64', 'function f()(cat <<E\n$(aip*r setup)\nE\n); f'),
    ('fn-65', 'function f()(\naip*r setup\n); f'),
    ('fn-66', 'function f()((1)); aip*r setup'),
    ('fn-70', 'f ( ) { aip*r setup; }; f'),
    ('fn-71', 'f\t(\t)\t{ aip*r setup; }; f'),
    ('fn-72', 'f( ){ aip*r setup; }; f'),
    ('fn-73', 'f()\n{\naip*r setup\n}\nf'),
    ('fn-74', 'f() if :; then aip*r setup; fi; f'),
    ('fn-75', 'f() for i in 1; do aip*r setup; done; f'),
    ('fn-76', 'f() while :; do aip*r setup; break; done; f'),
    ('fn-77', 'f() [[ 1 ]] && aip*r setup'),
    ('fn-78', 'f() (( 1 )) && aip*r setup'),
    ('fn-79', 'f() { :; } && aip*r setup'),
    ('fn-80', 'function f { :; } && aip*r setup'),
    ('fn-81', 'function f { :; }; aip*r setup'),
]


@_params(FUNCTION_REACH)
def test_function_form_reaching_command_is_blocked(cmd):
    assert _blocked(cmd) is True


# ---- gap: ``function NAME {`` after other command starts ----

FN_POSITION = ("undocumented gap (tester iteration 8: docs/security.md names "
               "`function f { aip* setup; }; f` among refused forms)")

FUNCTION_KEYWORD_POSITIONS = [
    ("in-brace-group", "{ function f { aip*r setup; }; f; }"),
    ("in-brace-group-called-after", "{ function f { aip*r setup; }; }; f"),
    ("in-brace-group-config", "{ function f { aip*r config; }; f; }"),
    ("in-brace-group-question", "{ function f { aipage? config; }; f; }"),
    ("after-then", "if :; then function f { aip*r setup; }; fi; f"),
    ("after-do-while", "while :; do function f { aip*r setup; }; break; done; f"),
    ("after-do-for", "for i in 1; do function f { aip*r setup; }; done; f"),
    ("after-bang", "! function f { aip*r setup; }; f"),
    ("after-time", "time function f { aip*r setup; }; f"),
    ("after-time-p", "time -p function f { aip*r setup; }; f"),
    ("after-do-until", "until false; do function f { aip*r setup; }; break; done; f"),
    ("in-if-condition", "if function f { aip*r setup; }; then f; fi"),
    ("in-while-condition", "while function f { aip*r setup; }; do f; break; done"),
    ("in-paren-function-group-body", "g() { function f { [a]ipager uninstall -y; }; f; }; g"),
    ("nested-in-function-keyword",
     "function f { function g { aip*r setup; }; g; }; f"),
    ("nested-in-paren-function", "f() { function g { aip*r setup; }; g; }; f"),
    ("nested-three-deep",
     "function f { function g { function h { aip*r setup; }; h; }; g; }; f"),
]


@_params(FUNCTION_KEYWORD_POSITIONS)
def test_function_keyword_after_any_command_start_is_blocked(cmd):
    assert _blocked(cmd) is True


# Controls: the same bodies are refused in the positions the developer
# covered, so each gap row fails only because of where ``function`` stands.
FUNCTION_POSITION_CONTROLS = [
    ("paren-function-in-brace-group", "{ f() { aip*r setup; }; f; }"),
    ("paren-function-after-then", "if :; then f() { aip*r setup; }; fi; f"),
    ("keyword-parens-after-then",
     "if :; then function f() { aip*r setup; }; fi; f"),
    ("keyword-parens-in-brace-group", "{ function f() { aip*r setup; }; }; f"),
    ("keyword-in-subshell", "( function f { aip*r setup; }; f )"),
    ("keyword-after-and", "true && function f { aip*r setup; } && f"),
    ("keyword-multi-line-in-brace", "{\nfunction f {\naip*r setup\n}\nf\n}"),
    ("paren-function-while-condition",
     "f(){ while aip*r setup; do break; done; }; f"),
    ("while-condition-alone", "while aip*r setup; do break; done"),
    ("keyword-while-condition-in-body",
     "function f { while aip*r setup; do break; done; }; f"),
]


@_params(FUNCTION_POSITION_CONTROLS)
def test_function_position_control_is_blocked(cmd):
    assert _blocked(cmd) is True


# ---- gap: a function body that is another compound command ----

FN_COMPOUND = ("undocumented gap (tester iteration 8: a glob program in the "
               "condition of a function's if/while/until body)")

FUNCTION_COMPOUND_BODY = [
    ("paren-while-condition", "f() while aip*r setup; do break; done; f"),
    ("paren-if-condition", "f() if aip*r setup; then :; fi; f"),
    ("keyword-while-condition", "function f while aip*r setup; do break; done; f"),
    ("keyword-if-condition", "function f if aip*r setup; then :; fi; f"),
    ("keyword-until-condition", "function f until aip*r setup; do :; done; f"),
]


@_params(FUNCTION_COMPOUND_BODY)
def test_function_compound_body_condition_is_blocked(cmd):
    assert _blocked(cmd) is True


@pytest.mark.parametrize("cmd", [
    "f() if :; then aip*r setup; fi; f",
    "function f if :; then aip*r setup; fi; f",
    "f() while :; do aip*r setup; break; done; f",
    "function f for i in 1; do aip*r setup; done; f",
], ids=["paren-if-branch", "keyword-if-branch", "paren-while-body",
        "keyword-for-body"])
def test_function_compound_body_branch_is_blocked(cmd):
    assert _blocked(cmd) is True


# ---- gap: escaped separators in text whose backslashes collapse ----

ESC_REGRESSION = ("regression (tester iteration 8): refused at 89df8edc and "
                  "on main; docs/security.md: the search rule holds inside "
                  "bash -c \"...\"")
T = B * 3  # three backslashes: after one collapse, \\ then a real ;

ESCAPED_SEPARATOR_REGRESSION = [
    ("bash-c-dq-semicolon", 'bash -c "echo x' + T + '; grep -rn aipager ~"'),
    ("sh-c-dq-semicolon", 'sh -c "echo x' + T + '; grep -rn aipager ~"'),
    ("bash-c-dq-amp", 'bash -c "echo x' + T + '& grep -rn aipager ~"'),
    ("bash-c-dq-pipe", 'bash -c "echo x' + T + '| grep -rn aipager ~"'),
    ("bash-c-dq-cd-then-grep", 'bash -c "cd /tmp' + T + '; grep -rn aipager ~"'),
    ("backticks-semicolon", "echo `echo x" + T + "; grep -rn aipager ~`"),
    ("backticks-in-dq", 'echo "`echo x' + T + '; grep -rn aipager ~`"'),
    ("backticks-assignment", "x=`echo x" + T + "; grep -rn aipager ~`"),
    ("backticks-amp", "echo `echo x" + T + "& grep -rn aipager ~`"),
    ("backticks-pipe", "echo `echo x" + T + "| grep -rn aipager ~`"),
    ("heredoc-backticks", "cat <<E\n`echo x" + T + "; grep -rn aipager ~`\nE"),
]


@_params(ESCAPED_SEPARATOR_REGRESSION)
def test_collapsed_escape_before_separator_still_starts_a_search(cmd):
    assert _blocked(cmd) is True


# Refused: a real separator runs the search (checked in bash).
ESCAPED_SEPARATOR_REFUSED = [
    ("two-backslashes", "echo x" + B * 2 + "; grep -rn aipager ~"),
    ("four-backslashes", "echo x" + B * 4 + "; grep -rn aipager ~"),
    ("two-backslashes-no-blank", "echo x" + B * 2 + ";grep -rn aipager ~"),
    ("two-backslashes-amp", "echo x" + B * 2 + "& grep -rn aipager ~"),
    ("two-backslashes-pipe", "echo x" + B * 2 + "| grep -rn aipager ~"),
    ("escaped-amp-then-amp", "echo x" + B + "&& grep -rn aipager ~"),
    ("escaped-pipe-then-pipe-amp", "echo x" + B + "|& grep -rn aipager ~"),
    ("escaped-semicolon-then-semicolon", "echo x" + B + ";; grep -rn aipager ~"),
    ("continuation-then-semicolon", "echo x" + B + "\n; grep -rn aipager ~"),
    ("single-quoted-backslash", "echo 'x" + B + "'; grep -rn aipager ~"),
    ("single-quoted-backslash-newline", "echo 'x" + B + "'\ngrep -rn aipager ~"),
    ("ansi-escaped-quote", "echo $'x" + B + "''; grep -rn aipager ~"),
    ("dq-escaped-backslash", 'echo "x' + B * 2 + '"; grep -rn aipager ~'),
    ("escaped-dq-then-semicolon", "echo x" + B + '"; grep -rn aipager ~'),
    ("quoted-semicolon-then-semicolon", "echo x';'; grep -rn aipager ~"),
    ("two-backslashes-rg", "echo x" + B * 2 + "; rg aipager ~"),
    ("two-backslashes-find", "echo x" + B * 2 + "; find ~ -name aipager"),
    ("two-backslashes-ls-R", "echo x" + B * 2 + "; ls -R ~"),
    ("two-backslashes-config", "echo x" + B * 2 + "; grep -r x ~/.config"),
    ("two-backslashes-du", "echo x" + B * 2 + "; du -sh ~"),
    ("two-backslashes-tree-root", "echo x" + B * 2 + "; tree /"),
    ("two-backslashes-env", "echo x" + B * 2 + "; env grep -rn aipager ~"),
    ("two-backslashes-xargs", "echo x" + B * 2 + "; xargs grep -rn aipager ~ </dev/null"),
    ("two-backslashes-setting", "echo x" + B * 2 + "; LC_ALL=C grep -rn aipager ~"),
    ("six-backslashes", "echo " + B * 6 + "; grep -rn aipager ~"),
    ("var-then-two-backslashes", "echo ${x}" + B * 2 + "; grep -rn aipager ~"),
    ("assignment-escaped-semicolon", "x=a" + B + "; grep -rn aipager ~"),
    ("bash-c-dq-four", 'bash -c "echo x' + B * 4 + '; grep -rn aipager ~"'),
    ("bash-c-sq-two", "bash -c 'echo x" + B * 2 + "; grep -rn aipager ~'"),
    ("backticks-four", "echo `echo x" + B * 4 + "; grep -rn aipager ~`"),
    ("cmdsub-two", "echo $(echo x" + B * 2 + "; grep -rn aipager ~)"),
    ("escaped-dollar-in-backticks", "echo `echo " + B + "$(grep -rn aipager ~)`"),
    ("find-exec-end-then-search", "find . -exec true " + B + "; ; grep -rn aipager ~"),
]


@_params(ESCAPED_SEPARATOR_REFUSED)
def test_real_separator_before_a_home_search_is_refused(cmd):
    assert _blocked(cmd) is True


# Allowed: bash never starts a command there (checked in bash: no search
# runs), so the search rules must not see one.
ESCAPED_SEPARATOR_ALLOWED = [
    ("continuation-joins-words", "echo x" + B + "\ngrep -rn aipager ~"),
    ("blank-continuation", "echo x " + B + "\ngrep -rn aipager ~"),
    ("single-quoted-semicolon", "echo ';' grep -rn aipager ~"),
    ("double-quoted-semicolon", 'echo ";" grep -rn aipager ~'),
    ("glued-quoted-semicolon", "echo x';' grep -rn aipager ~"),
    ("escaped-paren-and-backtick",
     "echo x" + B + "(grep -rn aipager ~" + B + ")"),
]


@_params(ESCAPED_SEPARATOR_ALLOWED)
def test_escaped_separator_starts_no_search(cmd):
    assert _blocked(cmd) is False


# Refused as on main (iteration 9, rev-iter8-001): bash runs no search
# here as typed, but a re-parse (``eval``, ``bash -c "..."``, backticks,
# ``$'...'``) turns an escaped separator into a real one, so the search
# rules read the command as typed and start a command after it. These
# rows were allowed at d6d18a17 only.
ESCAPED_SEPARATOR_REFUSED_AS_ON_MAIN = [
    ("escaped-semicolon", "echo x" + B + "; grep -rn aipager ~"),
    ("three-backslashes", "echo x" + B * 3 + "; grep -rn aipager ~"),
    ("escaped-amp", "echo x" + B + "& grep -rn aipager ~"),
    ("escaped-pipe", "echo x" + B + "| grep -rn aipager ~"),
    ("escaped-semicolon-no-blank", "echo x" + B + ";grep -rn aipager ~"),
    ("escaped-amp-no-blank", "echo x" + B + "&grep -rn aipager ~"),
    ("escaped-pipe-no-blank", "echo x" + B + "|grep -rn aipager ~"),
    ("lone-escaped-semicolon", "echo x " + B + "; grep -rn aipager ~"),
    ("escaped-rg", "echo x" + B + "; rg aipager ~"),
    ("escaped-find", "echo x" + B + "; find ~ -name aipager"),
    ("escaped-ls-R", "echo x" + B + "; ls -R ~"),
    ("escaped-du", "echo x" + B + "; du -sh ~"),
    ("escaped-after-escape", "echo a" + B + "b" + B + "; grep -rn aipager ~"),
    ("seven-backslashes", "echo " + B * 7 + "; grep -rn aipager ~"),
    ("var-then-escaped", "echo ${x}" + B + "; grep -rn aipager ~"),
    ("bash-c-dq-one", 'bash -c "echo x' + B + '; grep -rn aipager ~"'),
    ("bash-c-sq-one", "bash -c 'echo x" + B + "; grep -rn aipager ~'"),
    ("backticks-one", "echo `echo x" + B + "; grep -rn aipager ~`"),
    ("cmdsub-one", "echo $(echo x" + B + "; grep -rn aipager ~)"),
]


@_params(ESCAPED_SEPARATOR_REFUSED_AS_ON_MAIN)
def test_escaped_separator_is_read_as_a_search_start_as_on_main(cmd):
    assert _blocked(cmd) is True


# ---- gap: brackets bash reads with a range the floor does not see ----

BRACKET = ("undocumented gap (tester iteration 8: docs/security.md refuses "
           "a bracket that could match or that bash reads more than one way)")

BRACKET_RANGES = [
    ("eq-class-bracket-then-range", "cat ~/.config/a[[=x=]]h-j]pager/x"),
    ("eq-class-a-bracket-then-range", "cat ~/.config/a[[=a=]]h-j]pager/x"),
    ("eq-class-bracket-then-bracket", "cat ~/.config/a[[=a=]][h-j]pager/x"),
    ("eq-class-bracket-then-wide-range", "cat ~/.config/a[[=a=]]a-z]pager/x"),
    ("eq-class-then-bracket-range", "cat ~/.config/a[[=a=]]-z]pager/x"),
    ("eq-class-then-printable-range", "cat ~/.config/a[[=a=]]!-~]pager/x"),
    ("collating-symbol-range", "cat ~/.config/a[[.a.]-z]pager/x"),
    ("collating-symbol-short-range", "cat ~/.config/a[[.a.]-x]pager/x"),
    ("member-then-eq-class-range", "cat ~/.config/a[a[=a=]]-z]pager/x"),
    ("bracket-first-eq-class-range", "cat ~/.config/a[][=a=]]-z]pager/x"),
    ("eq-class-x-then-range", "cat ~/.config/a[[=x=]]-z]pager/x"),
    ("collating-dash-range", "cat ~/.config/a[[.-.]-z]pager/x"),
    ("relative-eq-class-range", "cat .config/a[[=a=]]h-j]pager/x"),
    ("local-share-collating-range", "cat ~/.local/share/a[[.a.]-z]pager/x"),
]


@_xfail_params(BRACKET_RANGES, BRACKET)
def test_bracket_range_that_can_match_is_blocked(cmd):
    assert _blocked(cmd) is True


BRACKET_CONTROLS = [
    ("eq-class-bracket-member", "cat ~/.config/a[[=x=]]i]pager/x"),
    ("eq-class-then-member", "cat ~/.config/a[[=a=]i]pager/x"),
    ("bracket-first-range", "cat ~/.config/a[]-z]pager/x"),
    ("class-then-range", "cat ~/.config/a[[:alpha:]-z]pager/x"),
    ("range-to-collating", "cat ~/.config/a[a-[.z.]]pager/x"),
    ("collating-bracket-range", "cat ~/.config/a[[.].]-z]pager/x"),
    ("dash-range", "cat ~/.config/a[--z]pager/x"),
    ("negated-dash-range", "cat ~/.config/a[!--h]pager/x"),
    ("first-bracket-range", "cat ~/.config/a[]h-j]pager/x"),
]


@_params(BRACKET_CONTROLS)
def test_bracket_control_is_blocked(cmd):
    assert _blocked(cmd) is True


# ---- gap: ``&`` in a replacement is the matched text ----

AMP = ("undocumented gap (tester iteration 8: bash 5.2 patsub_replacement "
       "turns & in ${x/pat/new} into the matched text)")

PATSUB_AMPERSAND = [
    ("amp-in-whole-path", "cat ${x/a/~/.config/&ipager/x}"),
    ("amp-in-folder", "cat ~/.config/${x/a/&ipager}/x"),
    ("amp-in-middle", "cat ~/.config/${x/a/&i}pager/x"),
    ("amp-bracket-pattern", "cat ${x/[a]/~/.config/&ipager/x}"),
]


@_xfail_params(PATSUB_AMPERSAND, AMP)
def test_replacement_ampersand_is_read_as_the_match(cmd):
    assert _blocked(cmd) is True


# ---- documented open routes met this round ----

DOC_GAP = "documented gap (docs/security.md 'What still gets through')"

# (id, command, example the docs page names for that route)
DOCUMENTED_OPEN_ITER8 = [
    ("glob-path-then-empty-var", "cat ~/.config/[a]ipager${unset_v}/x",
     "`~/.config/$D/aipager.yaml`"),
    ("glob-path-then-empty-at-q", "cat ~/.config/[a]ipager${unset_v@Q}/x",
     "`~/.config/$D/aipager.yaml`"),
    ("glob-path-then-empty-offset", "cat ~/.config/[a]ipager${x:0:0}/x",
     "`~/.config/$D/aipager.yaml`"),
    ("glob-path-then-empty-names", "cat ~/.config/[a]ipager${!zzq*}/x",
     "`~/.config/$D/aipager.yaml`"),
    ("glob-path-then-empty-cmdsub", "cat ~/.config/[a]ipager$(:)/x",
     "`~/.config/$D/aipager.yaml`"),
    ("replacement-of-assigned-var", "x=~/.config/q; cat ${x/q/[a]ipager}/x",
     "`~/.config/$D/aipager.yaml`"),
    ("case-branch-function-keyword",
     "case a in a) function f { aip*r setup; };; esac; f",
     "`case $x in *) aip* setup;; esac`"),
]


@pytest.mark.parametrize(
    "cmd",
    [pytest.param(r[1], marks=pytest.mark.xfail(strict=True, reason=DOC_GAP))
     for r in DOCUMENTED_OPEN_ITER8],
    ids=[r[0] for r in DOCUMENTED_OPEN_ITER8])
def test_documented_open_route_met_in_iteration_8(cmd):
    assert _blocked(cmd) is True


@pytest.mark.parametrize(
    "example", sorted({r[2] for r in DOCUMENTED_OPEN_ITER8}))
def test_open_route_met_in_iteration_8_is_named_as_open(example):
    open_list = _docs_section("What still gets through", "The real boundary")
    assert example in open_list


# ---- docs: what this round promises ----

def test_docs_name_the_function_keyword_among_refused_forms():
    refused = _docs_section("aipager's own commands and folders",
                            "What still gets through")
    assert "`function f { aip* setup; }; f`" in refused


def test_docs_say_a_replacement_counts_as_a_possible_value():
    assert "the replacement of `${x/pattern/new}`" in _docs_text()


def test_docs_say_text_inside_any_param_is_checked():
    assert "the text inside any `${...}`, open or closed" in _docs_text()


def test_docs_name_the_param_text_false_block():
    assert "`${f#~/.config/[a]ipager/}`" in _docs_text()


def test_docs_say_the_search_rule_holds_inside_bash_c():
    searches = _docs_section("Searches through Bash", "Why one block halts")
    assert 'inside `bash -c "..."`' in searches


# ---- ordinary dev commands stay allowed ----

# None of these reaches aipager's folders or runs a home search in bash.
DEV_ALLOWED = [
    ('dev-01', 'for f in src/*.py; do mv "$f" "${f/.py/.bak}"; done'),
    ('dev-02', 'echo "${PWD/#$HOME/~}"'),
    ('dev-03', 'cp "$f" "${f//old/new}"'),
    ('dev-04', 'ls ${dir/%\\//}'),
    ('dev-05', 'name=${branch//\\//-}; git checkout -b "$name"'),
    ('dev-06', 'sed -i "s/${old//\\//\\\\/}/${new//\\//\\\\/}/g" config.toml'),
    ('dev-07', 'cp ~/.config/nvim/init.lua "/tmp/${USER/x/y}-init.lua"'),
    ('dev-08', 'for p in "${paths[@]/#/--include=}"; do echo "$p"; done'),
    ('dev-09', 'echo "${files[@]/%/.orig}"'),
    ('dev-10', 'ls ~/.config/nvim/${x:-lua}/'),
    ('dev-11', 'cat ~/.config/nvim/lua/${mod/./\\/}.lua'),
    ('dev-12', 'v=${1:-~/.config/nvim}; ls "$v"'),
    ('dev-13', 'echo ${x@Q} ${x^} ${x,} ${!prefix*} ${x:1:2} ${#arr[@]}'),
    ('dev-14', 'echo "${line:$(( ${#line} - 3 ))}"'),
    ('dev-15', 'offset=$(wc -l < f.txt); echo "${s:$offset:5}"'),
    ('dev-16', 'function build { make -j4 "$@"; }; build all'),
    ('dev-17', 'function deploy {\n  rsync -a dist/ server:/srv/app/\n}\ndeploy'),
    ('dev-18', 'function cleanup() { rm -rf build/; }; trap cleanup EXIT'),
    ('dev-19', 'function nvimcfg { cd ~/.config/nvim && git status; }; nvimcfg'),
    ('dev-20', 'function f { function g { echo inner; }; g; }; f'),
    ('dev-21', '{ function f { ls ~/.config/nvim; }; f; }'),
    ('dev-22', 'if [ -d build ]; then function rb { rm -rf build; }; rb; fi'),
    ('dev-23', 'f() if [ -n "$1" ]; then echo "$1"; fi; f x'),
    ('dev-24', 'function wait_up while ! curl -s localhost:8000 >/dev/null; do sleep 1; done; wait_up'),
    ('dev-25', "find . -name '*.pyc' -exec rm {} \\;"),
    ('dev-26', "find ~/.config/nvim -name '*.lua' -exec grep -l require {} \\;"),
    ('dev-27', "find . -type f -name '*.sh' -exec chmod +x {} \\; && echo done"),
    ('dev-28', 'echo a\\;b > notes.txt'),
    ('dev-29', 'echo "a;b" | tr \';\' \'\\n\''),
    ('dev-30', "printf '%s\\n' x\\&y"),
    ('dev-31', "grep -n 'a\\|b' src/main.py"),
    ('dev-32', 'echo x \\\n  && ls ~/.config/nvim'),
    ('dev-33', 'make \\\n  CFLAGS=-O2 \\\n  all'),
    ('dev-34', 'grep -rn "TODO" src/ \\\n  --include=\'*.py\''),
    ('dev-35', 'echo done\\; next step: grep -rn TODO src'),
    ('dev-36', 'git commit -m "fix: handle a\\;b in parser"'),
    ('dev-37', 'echo `date +%s`\\; ls src'),
    ('dev-38', 'bash -c "make -C build && echo ok"'),
    ('dev-39', "sh -c 'cd src && grep -rn TODO .'"),
    ('dev-40', "rg -n 'x\\|y' src/"),
    ('dev-41', 'ls ~/.config/nvim # done\\; next'),
    ('dev-42', 'echo ${x/a/\\;}'),
    ('dev-43', 'echo ${x//;/\\\\;}'),
    ('dev-44', "cat <<'EOF'\nfunction f { echo ${x/a/b}; }\nEOF"),
    ('dev-45', '[[ ${x/a/b} == b ]] && echo yes'),
    ('dev-46', 'a[${#a[@]}]=${x/a/b}; echo "${a[@]}"'),
    ('dev-47', 'case ${x,,} in a) echo low;; esac'),
]


@_params(DEV_ALLOWED)
def test_ordinary_dev_command_is_allowed(cmd):
    assert _blocked(cmd) is False


# ---- timing: whole floor under 0.5 s at 100 KB, uncached best of 3 ----

BUDGET_S = 0.5
SIZE = 100_000
SMALL = 25_000
HARMLESS_TAIL = " ; ls ~/.config/nvim"
FORBIDDEN_TAIL = "\ncat ~/.config/[a]ipager/aipager.yaml"

# (id, prefix, repeated unit, closing unit repeated as often or "")
SHAPES8 = [
    ("rep-nested-ops", "echo ", "${x/${y#a}/${z:-q}} ", ""),
    ("rep-deep-replacement", "echo ", "${x/a/", "}"),
    ("rep-comsub-offsets", "echo ", "${x:$(echo 1):$(echo 0)} ", ""),
    ("at-q-names-array-rep", "echo ", "${x@Q}${!p*}${a[@]/q/r} ", ""),
    ("quotes-in-ops", "echo ", "${x/'}'/\"}\"} ", ""),
    ("ops-in-heredoc", "cat <<E\n", "${x/a/$(echo b)} ${y#c}\n", ""),
    ("ops-in-regex", "", "[[ a =~ ${x/a/b}(c) ]]; ", ""),
    ("ops-in-subscripts", "", "a[${x/a/1}]=1; ", ""),
    ("ops-in-function-bodies", "", "f() { echo ${x/a/b}; }; ", ""),
    ("ops-in-case-patterns", "", "case a in ${x/a/b}) :;; esac; ", ""),
    ("rep-config-text", "echo ", "${x/a/~/.config/nvim} ", ""),
    ("function-keyword-many", "", "function f { :; }; ", ""),
    ("function-keyword-newline", "", "function f\n{ :; }\n", ""),
    ("function-keyword-paren-body", "", "function f ( : ); ", ""),
    ("function-keyword-in-brace", "", "{ function f { :; }; }; ", ""),
    ("function-word-many", "echo ", "function ", ""),
    ("escaped-semicolons", "echo ", B + ";", ""),
    ("escaped-semicolon-words", "echo ", "x" + B + "; grep ", ""),
    ("double-escaped-semicolons", "echo ", B * 2 + ";", ""),
    ("escaped-newlines", "echo ", B + "\n", ""),
    ("escaped-backticks", "echo ", B + "`", ""),
    ("mixed-escapes", "echo ", B + ";" + B * 2 + ";" + B + "&" + B + "|", ""),
    ("bash-c-escapes", 'bash -c "', "echo x" + B * 3 + "; ", '"'),
    ("backtick-escapes", "echo `", "echo x" + B * 3 + "; ", "`"),
    ("find-exec", "", "find . -exec true {} " + B + "; ", ""),
]

_run = itertools.count()


def _shape(prefix: str, unit: str, close: str, size: int, tail: str) -> str:
    n = max(1, (size - len(prefix) - len(tail)) // (len(unit) + len(close)))
    return prefix + unit * n + close * n + tail


def _best_of_three(cmd: str) -> float:
    """Each run checks a different command (a distinct leading ``: rN;``),
    so a cache keyed by the command text cannot serve runs 2 and 3."""
    best = float("inf")
    for _ in range(3):
        c = ": r%d;%s" % (next(_run), cmd)
        t0 = time.perf_counter()
        bash_violation(c, DENY_BASH_PATTERNS)
        best = min(best, time.perf_counter() - t0)
    return best


_shape8 = pytest.mark.parametrize(
    "prefix,unit,close", [s[1:] for s in SHAPES8], ids=[s[0] for s in SHAPES8])


@_shape8
def test_iteration_8_shape_before_a_forbidden_read_is_denied(prefix, unit, close):
    assert _blocked(_shape(prefix, unit, close, SIZE, FORBIDDEN_TAIL)) is True


@_shape8
def test_iteration_8_shape_before_a_forbidden_read_is_under_budget(prefix, unit, close):
    cmd = _shape(prefix, unit, close, SIZE, FORBIDDEN_TAIL)
    assert _best_of_three(cmd) < BUDGET_S


@_shape8
def test_iteration_8_shape_before_a_harmless_tail_is_under_budget(prefix, unit, close):
    cmd = _shape(prefix, unit, close, SIZE, HARMLESS_TAIL)
    assert _best_of_three(cmd) < BUDGET_S
