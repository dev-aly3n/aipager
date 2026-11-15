"""Iteration 7 of the safety floor checks (design.md success criteria 15
and 16; docs/security.md "aipager's own commands and folders").

Whole commands. Every reaching row was run by real bash 5.2 in a throwaway
HOME with PATH empty: each bare command bash cannot find goes to a
recorder (``command_not_found_handle``) that logs its working folder and
full argv, ``bash``/``sh`` run the real bash so ``bash -c`` / ``sh -c``
text runs too, and strace logs every file bash opens and every program it
tries to start. A row "reaches" when it reads or writes a file in
~/.config/aipager or ~/.local/{share,state}/aipager, or runs a refused
aipager form (``aipager setup|config|...``, ``python3 -m aipager.cli``,
``python3 -c "import aipager..."``, ``pkill``/``killall``/``systemctl``
naming aipager). The tester's harness lives in the ship artifacts
(tester7-tools/oracle.py, opsfuzz.py, opdesync.py, hp.py).

Territory covered here for the first time: the text of non-default
``${...}`` operations, ``eval``, ``source`` of a file the command wrote,
``bash -c`` / ``sh -c``, ``printf -v``, ``read <<<``, declarations with
values, aliases, ``$'...'`` inside every quote kind and nesting, and the
separators ``|&``, ``;&``, ``;;&``, ``&>``, ``>|``, ``<>``, ``3<`` and
``{fd}<``.

OP_REPLACEMENT: the replacement text of ``${x/pat/rep}`` (and a
``${v:-default}`` nested in it, or a replacement nested in a default) is
not read as a possible value, so a path spelled only for the matcher
(``[a]ipager``, ``[[:alpha:]]ipager``) reaches. docs/security.md promises
the default of ``${x:-...}``/``${x:+...}`` counts as a possible value and
names no route for a replacement whose text is in the command, so each
row is a strict xfail (project convention for known gaps).

FUNCTION_KEYWORD: docs/security.md now names a glob program name in a
function body among the refused forms (``f(){ aip* setup; }; f``), but a
body opened as ``function g { ... }`` (no parentheses) is still allowed
while ``function g() { ... }`` and ``g() { ... }`` are refused: strict
xfail.

DOCUMENTED_OPEN_ITER7: misses this round found inside routes
docs/security.md names as open (``eval``'d commands, ``bash -c 'aip*
setup'``, a script written then run, aliases, a ``case`` branch, a word
built from a variable, a prefix the patterns do not read such as
``coproc``): strict xfail with reason "documented gap", plus a check that
the docs page still names each route.

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


# ---- gaps: the replacement text of ${x/pat/rep} ----

UNDOCUMENTED = "undocumented gap (tester iteration 7: ${x/pat/rep} text)"

# (id, command). x is set (x=a in the probe shell), unset_v is unset.
OP_REPLACEMENT = [
    ("rep-bracket", "cat ${x/a/~/.config/[a]ipager/aipager.yaml}"),
    ("rep-posix-class", "cat ${x/a/~/.config/[[:alpha:]]ipager/x}"),
    ("rep-all", "cat ${x//?/~/.config/[a]ipager/x}"),
    ("rep-prefix", "cat ${x/#a/~/.config/[a]ipager/x}"),
    ("rep-suffix", "cat ${x/%a/~/.config/[a]ipager/x}"),
    ("rep-local-share", "cat ${x/a/~/.local/share/[a]ipager/x}"),
    ("rep-ansi-config-bracket", "cat ${x/a/~/.con$'" + B + "x66'ig/[a]ipager/x}"),
    ("rep-sed-write", "sed -i s/user/owner/ ${x/a/~/.config/[a]ipager/aipager.yaml}"),
    ("rep-home-var", "cp /dev/null ${x/a/$HOME/.config/[a]ipager/aipager.yaml}"),
    ("rep-after-empty-trim", "cat ${x#a}${x/a/~/.config/[a]ipager/x}"),
    ("default-inside-rep", "cat ${x/a/${unset_v:-~/.config/[a]ipager/x}}"),
    ("rep-inside-default", "cat ${unset_v:-${x/a/~/.config/[a]ipager/x}}"),
    ("rep-inside-alt", "cat ${x:+${x/a/~/.config/[a]ipager/x}}"),
]


# Rows the developer closed in iteration 8 (fixes-8.md): their strict
# xfail marks are removed.
CLOSED_ITER8 = {
    "function-keyword-brace", "function-keyword-question",
    "function-keyword-bracket", "function-keyword-tab",
    "function-keyword-nested-group"}


@pytest.mark.parametrize(
    "cmd",
    [pytest.param(r[1], marks=() if r[0] in CLOSED_ITER8
                  else pytest.mark.xfail(strict=True, reason=UNDOCUMENTED))
     for r in OP_REPLACEMENT],
    ids=[r[0] for r in OP_REPLACEMENT])
def test_replacement_text_is_read_as_a_possible_value(cmd):
    assert _blocked(cmd) is True


# Controls: the same targets are refused on their own and as a default,
# so each gap row above fails only because of the replacement.
OP_CONTROLS = [
    ("target-alone", "cat ~/.config/[a]ipager/aipager.yaml"),
    ("target-posix-alone", "cat ~/.config/[[:alpha:]]ipager/x"),
    ("target-as-default", "cat ${unset_v:-~/.config/[a]ipager/x}"),
    ("target-as-alt", "cat ${x:+~/.config/[a]ipager/x}"),
    ("target-local-share-alone", "cat ~/.local/share/[a]ipager/x"),
    ("cli-bracket-alone", "[a]ipager config"),
    ("rep-literal-name", "cat ${x/a/~/.config/aipager/x}"),
    ("rep-literal-quoted", 'cat "${x/a/~/.config/aipager/x}"'),
    ("rep-star", "cat ${x/a/~/.config/a*/aipager.yaml}"),
    ("rep-question", "cat ${x/a/~/.config/aipage?/x}"),
    ("rep-ansi-split", "cat ${x/a/~/.config/aip$'" + B + "x61'ger/x}"),
    ("rep-quote-split", "cat ${x/a/~/.config/aip''ager/x}"),
    ("rep-whole-cli", "${x/a/aip*r setup}"),
    ("rep-cli-in-default", "${unset_v:-${x/a/aip*r setup}}"),
    ("rep-python-m", "${x/a/python3 -m aipager.cli setup}"),
]


@_params(OP_CONTROLS)
def test_replacement_control_is_blocked(cmd):
    assert _blocked(cmd) is True


# ---- gap: a function body opened with the ``function NAME {`` form ----

UNDOCUMENTED_FN = ("undocumented gap (tester iteration 7: docs/security.md "
                   "says a glob program name in a function body is refused)")

FUNCTION_KEYWORD = [
    ("function-keyword-brace", "function g { aip*r setup; }; g"),
    ("function-keyword-question", "function g { aipage? config; }; g"),
    ("function-keyword-bracket", "function g { [a]ipager uninstall -y; }; g"),
    ("function-keyword-tab", "function g {\taip*r setup; }; g"),
    ("function-keyword-nested-group", "function g { { aip*r setup; }; }; g"),
]

FUNCTION_CONTROLS = [
    ("name-parens-brace", "g() { aip*r setup; }; g"),
    ("name-space-parens-brace", "g () { aip*r setup; }; g"),
    ("function-keyword-parens-brace", "function g() { aip*r setup; }; g"),
    ("function-keyword-subshell", "function g ( aip*r setup ); g"),
    ("name-parens-subshell", "g() ( aip*r setup ); g"),
    ("name-parens-newline-brace", "g()\n{\n  aip*r setup\n}\ng"),
    ("name-parens-if-body", "g() if true; then aip*r setup; fi; g"),
    ("name-parens-nested-group", "g() { { aip*r setup; }; }; g"),
    ("function-keyword-multiline", "function g {\n  aipage? config\n}\ng"),
]


@pytest.mark.parametrize(
    "cmd",
    [pytest.param(r[1], marks=() if r[0] in CLOSED_ITER8
                  else pytest.mark.xfail(strict=True, reason=UNDOCUMENTED_FN))
     for r in FUNCTION_KEYWORD],
    ids=[r[0] for r in FUNCTION_KEYWORD])
def test_function_keyword_body_glob_program_is_blocked(cmd):
    assert _blocked(cmd) is True


@_params(FUNCTION_CONTROLS)
def test_function_body_glob_program_control_is_blocked(cmd):
    assert _blocked(cmd) is True


# ---- documented open routes met this round (warnings, not errors) ----

DOC_GAP = "documented gap (docs/security.md 'What still gets through')"

# (id, command, example the docs page names for that route)
DOCUMENTED_OPEN_ITER7 = [
    ("eval-glob-program", "eval 'aip*r setup'", "`eval`'d command"),
    ("bash-c-glob-program", "bash -c 'aip*r setup'", "`bash -c 'aip* setup'`"),
    ("sh-c-glob-program", "sh -c 'aipage? config'", "`bash -c 'aip* setup'`"),
    ("script-written-then-sourced", "echo 'aip*r setup' > s.sh; . ./s.sh",
     "a path built inside a script"),
    ("alias-glob-program",
     "shopt -s expand_aliases\nalias q='aip*r setup'\nq", "a shell function or alias"),
    ("case-fallthrough-glob-program", "case a in a) :;& b) aip*r setup;; esac",
     "`case $x in *) aip* setup;; esac`"),
    ("declared-command-in-variable", "declare c='aipage? config'; $c",
     "`A=aipager; $A setup`"),
    ("program-name-from-replacement", "${x/a/aip*r} setup",
     "`A=aipager; $A setup`"),
    ("program-name-bracket-from-replacement", "${x/a/[a]ipager} config",
     "`A=aipager; $A setup`"),
    ("cd-config-from-replacement", "cd ${x/a/~/.config} && cat aipager/x",
     "relative paths after reaching a folder by another route"),
    ("coproc-glob-program", "coproc aip*r setup",
     "a glob program name after a prefix the patterns do not read"),
    ("coproc-named-group-glob-program", "coproc X { aip*r setup; }",
     "a glob program name after a prefix the patterns do not read"),
]


@pytest.mark.parametrize(
    "cmd",
    [pytest.param(r[1], marks=pytest.mark.xfail(strict=True, reason=DOC_GAP))
     for r in DOCUMENTED_OPEN_ITER7],
    ids=[r[0] for r in DOCUMENTED_OPEN_ITER7])
def test_documented_open_route_met_in_iteration_7(cmd):
    assert _blocked(cmd) is True


def _docs_text() -> str:
    return re.sub(r"\s+", " ", SECURITY_MD.read_text(encoding="utf-8"))


def _docs_section(start: str, end: str) -> str:
    text = _docs_text()
    i = text.index(start)
    return text[i:text.index(end, i)]


@pytest.mark.parametrize(
    "example", sorted({r[2] for r in DOCUMENTED_OPEN_ITER7}))
def test_open_route_met_in_iteration_7_is_named_as_open(example):
    open_list = _docs_section("What still gets through", "The real boundary")
    assert example in open_list


def test_function_body_glob_program_is_named_among_refused_forms():
    refused = _docs_section("aipager's own commands and folders",
                            "What still gets through")
    assert "`f(){ aip* setup; }; f`" in refused


def test_function_body_glob_program_is_no_longer_named_as_open():
    open_list = _docs_section("What still gets through", "The real boundary")
    assert "f(){ aip* setup; }" not in open_list


def test_case_branch_glob_program_stays_named_as_open():
    open_list = _docs_section("What still gets through", "The real boundary")
    assert "`case $x in *) aip* setup;; esac`" in open_list


def test_docs_say_set_aside_text_naming_aipager_through_a_glob_is_refused():
    assert ("a comment or here-document that names aipager's folders "
            "through a glob") in _docs_text()


def test_function_body_glob_program_is_blocked():
    assert _blocked("f(){ aip* setup; }; f") is True


# ---- new territory: reaching whole commands that must stay refused ----

# Every row reaches in bash (read/write in aipager's folders or a refused
# aipager CLI form) and is refused at abe7ead3.
NEW_TERRITORY = [
    # non-default ${...} operations
    ("rep-cli-whole-word", "${x/a/aip*r setup}"),
    ("op-trim-quote-then-target", "cat ${x#$'}'}; cat ~/.config/[a]ipager/x"),
    ("op-trim-dq-ansi-then-target", 'echo "${x#$\'}\'}"; cat ~/.config/[a]ipager/x'),
    ("op-default-dq-ansi-then-target", 'echo "${x:-$\'}\'}"; cat ~/.config/[a]ipager/x'),
    ("op-rep-ansi-quote-then-target",
     'echo "${x/$\'' + B + "''/}\"; cat ~/.config/[a]ipager/x"),
    ("op-rep-dq-sq-then-target", "echo ${x/\"'\"/}; cat ~/.config/[a]ipager/x"),
    ("op-rep-sq-dq-in-dq-then-target", "echo \"${x/'\"'/}\"; cat ~/.config/[a]ipager/x"),
    ("op-P-of-glob-var", "v=~/.config/[a]ipager/x; cat ${v@P}"),
    ("op-rep-empty-of-glob-var", "v=~/.config/[a]ipager/x; cat ${v/#/}"),
    # printf -v, read <<<, declarations
    ("printf-v-arg", "printf -v p '%s' ~/.config/[a]ipager/x; cat $p"),
    ("printf-v-format", "printf -v p ~/.config/[a]ipager/x; cat $p"),
    ("read-herestring", "read -r p <<< ~/.config/[a]ipager/x; cat $p"),
    ("mapfile-herestring", "mapfile -t a <<< ~/.config/[a]ipager/x; cat ${a[0]}"),
    ("declare-value", "declare p=~/.config/[a]ipager/x; cat $p"),
    ("declare-x-glob", "declare -x p=~/.config/a*/aipager.yaml; cat $p"),
    ("export-value", "export p=~/.config/[a]ipager/x; cat $p"),
    ("readonly-value", "readonly p=~/.config/[a]ipager/x; cat $p"),
    ("local-in-function", "f() { local p=~/.config/[a]ipager/x; cat $p; }; f"),
    ("declare-array", "declare -a a=(~/.config/[a]ipager/x); cat ${a[0]}"),
    # eval / source / bash -c / sh -c / alias with a path
    ("eval-sq-path", "eval 'cat ~/.config/[a]ipager/x'"),
    ("eval-bare-path", "eval cat ~/.config/[a]ipager/x"),
    ("eval-dq-glob", 'eval "cat ~/.config/a*/x"'),
    ("eval-ansi-bracket", "eval $'cat ~/.config/" + B + "x5ba]ipager/x'"),
    ("source-written-file", "echo 'cat ~/.config/[a]ipager/x' > s.sh; . ./s.sh"),
    ("source-procsub", "source <(echo 'cat ~/.config/[a]ipager/x')"),
    ("source-stdin-heredoc", ". /dev/stdin <<'E'\ncat ~/.config/[a]ipager/x\nE"),
    ("bash-c-path", "bash -c 'cat ~/.config/[a]ipager/x'"),
    ("sh-c-glob", 'sh -c "cat ~/.config/a*/x"'),
    ("bash-heredoc-path", "bash <<'E'\ncat ~/.config/[a]ipager/x\nE"),
    ("bash-herestring-path", "bash <<< 'cat ~/.config/[a]ipager/x'"),
    ("bash-heredoc-cli", "bash <<'E'\naip*r setup\nE"),
    ("alias-path", "shopt -s expand_aliases\nalias q='cat ~/.config/[a]ipager/x'\nq"),
    ("eval-literal-cli", "eval 'aipager setup'"),
    ("bash-c-quote-split-cli", "bash -c \"aip''ager config\""),
    ("eval-ansi-cli", 'eval "$\'' + B + "x61'ipager setup\""),
    # separators
    ("pipe-amp-path", "echo |& cat ~/.config/[a]ipager/x"),
    ("pipe-amp-cli", "echo |& aip*r setup"),
    ("case-fallthrough-path", "case a in a) :;& b) cat ~/.config/[a]ipager/x;; esac"),
    ("case-continue-path", "case a in a) :;;& *) cat ~/.config/[a]ipager/x;; esac"),
    ("amp-gt-write", "echo x &> ~/.config/[a]ipager/x"),
    ("clobber-write", "echo x >| ~/.config/[a]ipager/x"),
    ("read-write-open", "cat <> ~/.config/[a]ipager/x"),
    ("fd3-read", "cat 3< ~/.config/[a]ipager/x"),
    ("fdvar-read", "exec {fd}< ~/.config/[a]ipager/x; cat <&$fd"),
    ("exec-fd3-glued", "exec 3<~/.config/[a]ipager/x"),
    ("redir-before-command", "<~/.config/[a]ipager/x cat"),
    ("clobber-alone", ">|~/.config/[a]ipager/x"),
    ("cli-amp-gt", "aip*r setup &> /dev/null"),
    ("cli-clobber", "aip*r setup >| out.txt"),
    ("cli-after-fdvar", "exec {fd}</dev/null; aip*r setup"),
    # $' mixed with quotes and nesting
    ("locale-string-home", 'cat $"$HOME/.config/aipager/x"'),
    ("ansi-quote-in-dq-cmdsub", "cat \"$(echo $'" + B + "x27'; cat ~/.config/[a]ipager/x)\""),
    ("ansi-quote-in-heredoc", "cat <<E\n$'" + B + "''\n$(cat ~/.config/[a]ipager/x)\nE"),
    ("ansi-backtick-in-backticks", "echo `echo $'" + B + "x60'; cat ~/.config/[a]ipager/x`"),
    ("dq-dollar-quote-then-target", 'echo "$\'"; cat ~/.config/[a]ipager/x'),
    ("locale-dollar-quote-then-target", 'echo $"$\'"; cat ~/.config/[a]ipager/x'),
    # $' with every quote kind, nesting and separator, before a target
    ("ansi-heredoc-then-glob-cli", "cat <<E\n$'\nE\naip*r setup"),
    ("ansi-hex-quote-in-dq-op-then-cli", 'echo "${x/$\'\\x27\'/}"; aip*r setup'),
    ("ansi-quote-then-dq-then-cli", 'echo $\'\\\'\' "\'"; aip*r setup'),
    ("ansi-in-trim-then-cli", ": ${x#$'\\''}; aipage? config"),
    ("ansi-backtick-then-cli", "echo `echo $'\\x60'`; aip*r setup"),
    ("ansi-paren-in-cmdsub-then-cli", "echo $(echo $')'); aip*r setup"),
    ("ansi-brace-in-op-then-ansi-cli", "echo ${x/$'}'/}; $'\\x61'ipager setup"),
    ("dq-dollar-quote-then-ansi-cli", 'echo "$\'"; $\'\\x61\'ipager setup'),
    ("ansi-assign-then-split-cli", "x=$'\\''; aip$'\\x61'ger config"),
    ("at-q-then-python-m-ansi", "echo ${x@Q}$'\\''; python3 -m $'\\x61'ipager.cli setup"),
    ("heredoc-op-ansi-then-escaped-cli", "cat <<E\n${x/$'}'/}\nE\na\\ipager setup"),
    ("escaped-dq-op-then-pkill-ansi", 'echo ${x/\\"/}; pkill -f $\'\\x61\'ipager'),
    ("pipe-amp-ansi-cli", "echo |& $'\\x61'ipager setup"),
    ("case-fall-ansi-cli", "case a in a) :;& b) $'\\x61'ipager setup;; esac"),
    ("case-cont-ansi-path", "case a in a) :;;& *) cat ~/.config/$'\\x61'ipager/x;; esac"),
    ("amp-gt-then-ansi-path", "echo x &> /dev/null; cat ~/.config/$'\\x61'ipager/x"),
    ("fdvar-then-ansi-local-share", "exec {fd}< /dev/null; cat <&$fd ~/.local/share/$'\\x61'ipager/x"),
    ("rw-fd3-ansi-path", "cat 3<> ~/.config/$'\\x61'ipager/x"),
    ("printf-v-ansi-bracket", "printf -v p $'%s' ~/.config/$'\\x5b'a]ipager/x; cat $p"),
    ("declare-ansi-bracket", "declare p=~/.config/$'\\x5b'a]ipager/x; cat $p"),
    # multi-line commands mixing all of the above
    ("huge-mixed-1", '#!/bin/bash\n# deploy helper - it\'s ${x#\'}\'} safe\nshopt -s extglob\ndeclare -A cfg=([k]=v)\nf() { local z=${x/\'}\'/} ; echo "$z" |& cat ; }\ncase $x in a) echo $\'it\\\'s\' ;& b) echo "$\'" ;;& *) : ;; esac\n[[ x =~ (a|#b) ]] && echo ok\ncat <<\'EOF\' | sed s/a/b/ >| out.txt\n$\'  ${x/"}"/} `echo\nEOF\nexec {fd}< /dev/null\nread -r q <<< "$\'"\nprintf -v w \'%s\' "${x@Q}"\neval \'echo "#"\'\ncat ~/.config/[a]ipager/aipager.yaml\necho done # \''),
    ("huge-mixed-2", 'echo \'$\' ; echo ${x/$\'\\\'\'/} ; cat <<E\n$(echo \')\')\nE\na=( [0]=x \'}\' ) ; if(true) then :; fi\nx=1 a[0 #]=1 true\necho "${x#${y:-\'}\'}}" |& cat 3< /dev/null\necho ${x//\\}/a} &> /dev/null; aip*r setup group --chat-id -1 --member 5:me:owner --force'),
    ("huge-mixed-3", "g() ( echo ${x^^} )\ntime(true)\necho `echo ${x#a}`\necho $(echo ${x%')'})\n: <<''\nx\n\n[[ ${x/'}'/} =~ (a|#) ]] && :\ncd ~/$'\\x2e'config && cat aipager/aipager.yaml"),
    ("huge-mixed-4", 'echo ${x@P}$\'\\\'\'; echo "${x/\\"}\\"/}"\necho $"$\'" && read -r z <<< \'$\'\nalias z=\'echo #\'\ndeclare -n nr=x\n(( ${#x} > 0 )) && echo $(( ${#x} ))\ncat <<E\n${x#\'}\'}\nE\npython3 -m aipager.cli setup'),
]


@_params(NEW_TERRITORY)
def test_new_territory_command_is_blocked(cmd):
    assert _blocked(cmd) is True


# ---- ordinary dev commands stay allowed (comments, here-docs, $'...', =~,
# arrays, functions, ${...} operations and the new separators) ----

ALLOWED_DEV = [
    "# back up nvim config first: ~/.config/nvim\ncp -r ~/.config/nvim ~/.config/nvim.bak # see ~/.config/nvim/init.lua\nls ~/.config/nvim  # it's the editor's folder, not aipager's\necho done # TODO: also check ~/.config/git/config and ~/.local/share/nvim",
    'cat > ~/.config/nvim/lua/plugins.lua <<\'EOF\'\n-- lazy.nvim spec, lives in ~/.config/nvim/lua\nreturn { "folke/lazy.nvim" }\nEOF',
    'cat <<EOF > ~/.config/fish/conf.d/env.fish\nset -x XDG_CONFIG_HOME $HOME/.config\nset -x NVIM_DIR ~/.config/nvim\nEOF',
    'python3 - <<\'PY\'\nimport os, pathlib\np = pathlib.Path.home() / ".config" / "nvim" / "init.lua"\nprint(p.read_text()[:200])\nPY',
    "printf $'line1\\nline2\\n' > notes.txt",
    "echo $'it\\'s ok' && ls ~/.config/nvim",
    "grep -n $'\\t' src/*.py",
    "sed -i $'s/\\r$//' scripts/build.sh",
    "IFS=$'\\n' read -r -d '' -a lines < <(git ls-files) || true",
    'if [[ $branch =~ ^feature/(.+)$ ]]; then echo "${BASH_REMATCH[1]}"; fi',
    '[[ "$f" =~ \\.lua$ ]] && cp "$f" ~/.config/nvim/lua/',
    '[[ $(uname) =~ (Linux|Darwin) ]] && ls ~/.config/nvim',
    "re='^[0-9]+$'; [[ $n =~ $re ]] && echo num",
    '[[ $line =~ ^#\\ (.*)$ ]] && echo "${BASH_REMATCH[1]}" # markdown heading',
    'files=(~/.config/nvim/lua/*.lua); echo "${#files[@]}"',
    'declare -A cfg=([nvim]=~/.config/nvim [git]=~/.config/git); echo "${cfg[nvim]}"',
    'arr=(); arr+=("$HOME/.config/nvim"); arr[1]=~/.config/fish; printf \'%s\\n\' "${arr[@]}"',
    'a[0]=1; a[1+1]=2; echo "${a[@]}"',
    'backup() { cp -r ~/.config/nvim "/tmp/nvim-$(date +%s)"; }; backup',
    'nv() {\n  # open the nvim config\n  cd ~/.config/nvim && ls\n}\nnv',
    'function lint { ruff check "$@"; } ; lint src',
    'f() ( cd ~/.config/nvim && git status )',
    'case $1 in nvim) ls ~/.config/nvim ;; fish) ls ~/.config/fish ;; esac',
    'echo ${x#*/} ${x%.*} ${x//a/b} ${x^^} ${x,,} ${#x}',
    'name=${file##*/}; base=${name%.*}; echo "$base"',
    'cp "${src/#\\~/$HOME}" ~/.config/nvim/',
    'echo "${PATH//:/$\'\\n\'}"',
    'eval "$(direnv hook bash)"',
    'source ~/.config/fish/env.sh 2>/dev/null || . ./venv/bin/activate',
    "bash -c 'cd ~/.config/nvim && git pull'",
    'printf -v ts \'%(%F)T\' -1; echo "$ts"',
    'read -r ver <<< "$(python3 --version)"; echo "$ver"',
    'export EDITOR=nvim NVIM_APPNAME=nvim; local_dir=~/.config/nvim',
    'make test 2>&1 |& tee build.log',
    'npm test &> test.log; echo $?',
    'exec 3< ~/.config/nvim/init.lua; read -r first <&3; exec 3<&-',
    'exec {fd}> build.log; echo hi >&$fd',
    'echo hi >| out.txt; cat <> out.txt',
    'case $x in a) echo a ;& b) echo b ;;& *) echo any ;; esac',
    'git log --oneline | head -5 # recent: see ~/.config/git/config for aliases',
    "cat ~/.config/nvim/init.lua | grep -v '^--' # it's lua",
    ": <<'NOTE'\nremember ~/.config/nvim/lua and ~/.local/share/nvim/lazy\nNOTE",
    'tar czf nvim.tgz -C ~/.config/nvim .  # pack nvim only',
    'x=1 a[0]=2 true; echo $(( a[0] << 1 ))',
    'echo $(( 1 << 3 )) # shift, not a here-doc',
    'cat ~/.config/[n]vim/init.lua',
    'ls ~/.config/nvim*/   # nvim and nvim.bak',
    'echo "aipager docs say: see ~/.config/nvim" # mentions aipager',
    "alias ll='ls -la ~/.config/nvim'; ll",
    "shopt -s expand_aliases\nalias gs='git status'\ngs",
    "cat <<< $'a\\tb' | column -t",
    'echo `printf \'%s\' $\'x\'` "$(printf $\'\\x41\')"',
    'declare -r CONF=~/.config/nvim; ls "$CONF"',
    'local_conf="${XDG_CONFIG_HOME:-$HOME/.config}/nvim"; ls "$local_conf"',
    '# comment naming ~/.config/nvim*/lua with a glob that cannot be aipager\nls ~/.config/nvim/lua',
    "cat <<'EOF'\nsee ~/.config/nvim/lua/*.lua and ~/.config/*.conf\nEOF",
    'git commit -m "docs: explain ~/.config/nvim layout" # it\'s ~/.config/nvim/lua',
    'sql=$\'SELECT *\\nFROM t\'; psql -c "$sql"',
    "grep -E $'^\\\\s*#' ~/.config/nvim/init.lua",
    "echo ${x/#$'\\t'/} ${x//$'\\n'/ }",
    "python3 -c $'import sys\\nprint(sys.version)'",
    'mkdir -p ~/.config/myapp && cat > ~/.config/myapp/config.toml <<TOML\n# generated $(date)\nname = "app"\nTOML',
    "jq -r '.scripts | keys[]' package.json # list npm scripts",
]


@pytest.mark.parametrize("cmd", ALLOWED_DEV,
                         ids=["dev-%02d" % i for i in range(len(ALLOWED_DEV))])
def test_ordinary_dev_command_is_allowed(cmd):
    assert _blocked(cmd) is False


NEW_TERRITORY_ALLOWED = [
    ("alias-nvim", "alias q='cat ~/.config/nvim/init.lua'; q"),
    ("eval-nvim", 'eval "ls ~/.config/nvim"'),
    ("bash-c-nvim", 'bash -c "ls ~/.config/nvim/lua"'),
    ("declare-nvim", "declare p=~/.config/nvim; ls $p"),
    ("printf-v-nvim", "printf -v p %s ~/.config/nvim; ls $p"),
    ("read-herestring-nvim", "read -r p <<< ~/.config/nvim; ls $p"),
    ("op-rep-nvim", "echo ${x/a/~/.config/nvim}"),
    ("op-rep-nvim-glob", "ls ${x/a/~/.config/nvim/*.lua}"),
    ("coproc-nvim", "coproc cat ~/.config/nvim/init.lua"),
    ("function-keyword-nvim", "function g { ls ~/.config/nvim; }; g"),
    ("pipe-amp-nvim", "ls ~/.config/nvim |& head"),
    ("fdvar-nvim", "exec {fd}< ~/.config/nvim/init.lua"),
    ("case-fallthrough-nvim", "case a in a) :;& b) ls ~/.config/nvim;; esac"),
    ("clobber-nvim", "echo x >| ~/.config/nvim/x.lua"),
    ("rw-nvim", "cat <> ~/.config/nvim/init.lua"),
]


@_params(NEW_TERRITORY_ALLOWED)
def test_new_territory_dev_command_is_allowed(cmd):
    assert _blocked(cmd) is False


# ---- text the reader sets aside: refused only when it names aipager's
# folders through a glob (or literally, as before) ----

SET_ASIDE_ALLOWED = [
    ("comment-literal-nvim", "ls ~/.config/nvim # see ~/.config/nvim/init.lua"),
    ("comment-only-config", "ls # look in ~/.config for it"),
    ("comment-nvim-glob", "ls ~/.config/nvim # also ~/.config/nvim*/lua"),
    ("comment-conf-glob", "ls # ~/.config/*.conf files"),
    ("comment-short-glob", "ls # ~/.config/?vim and ~/.config/n*"),
    ("comment-local-share-nvim", "echo hi # ~/.local/share/nvim/lazy/*.lua"),
    ("comment-apostrophe-then-path", "echo it # it's in ~/.config/git/config"),
    ("heredoc-literal-nvim", "cat <<'E'\nedit ~/.config/nvim/init.lua\nE"),
    ("heredoc-nvim-glob", "cat <<E\n~/.config/nvim/lua/*.lua\nE"),
    ("heredoc-conf-glob", ": <<'N'\n~/.config/*.conf\nN"),
    ("heredoc-delimiter-config", "cat <<.config\nx\n.config"),
    ("subscript-shift", "a[1<<2]=x; echo ${a[4]}"),
    ("regex-group-nvim", "[[ $p =~ (nvim|vim)$ ]] && ls ~/.config/nvim"),
    ("ansi-nvim", "ls ~/.config/$'nvim'"),
    ("ansi-escape-nvim", "ls ~/.config/$'" + B + "x6evim'"),
]
SET_ASIDE_REFUSED = [
    ("comment-bracket-aipager", "echo x # cat ~/.config/[a]ipager/x"),
    ("comment-star-folder", "ls ~/.config/nvim # and ~/.config/*/init.lua"),
    ("comment-literal-aipager", "echo ok # see ~/.config/aipager/aipager.yaml"),
    ("heredoc-star-aipager", "cat <<'EOF'\n~/.config/a*/aipager.yaml\nEOF"),
    ("heredoc-question-local-share", ": <<'NOTE'\n~/.local/share/aipage?/x\nNOTE"),
    ("comment-in-comment", "echo x # a # ~/.config/[a]ipager/x"),
    ("subscript-bracket-aipager", "a[1 #~/.config/[a]ipager/x]=1"),
]


@_params(SET_ASIDE_ALLOWED)
def test_set_aside_text_without_a_glob_naming_aipager_is_allowed(cmd):
    assert _blocked(cmd) is False


@_params(SET_ASIDE_REFUSED)
def test_set_aside_text_naming_aipager_is_refused(cmd):
    assert _blocked(cmd) is True


# ---- timing: whole floor under 0.5 s at 100 KB, uncached best of 3 ----

BUDGET_S = 0.5
SIZE = 100_000
SMALL = 25_000
HARMLESS_TAIL = " ; ls ~/.config/nvim"
FORBIDDEN_TAIL = "\ncat ~/.config/[a]ipager/aipager.yaml"

# (id, prefix, repeated unit, closing unit repeated as often or "")
SHAPES7 = [
    ("comments-naming-config", "", "# see ~/.config/nvim/x it's ${\n", ""),
    ("comments-nvim-glob", "ls\n", "# ~/.config/nvim*/lua\n", ""),
    ("one-comment-many-config", "echo a # ", "~/.config/nvim/x ", ""),
    ("heredocs-naming-config", "", "cat <<E\n~/.config/nvim/x $(ls)\nE\n", ""),
    ("quoted-heredocs-naming-config", "", "cat <<'E'\n~/.config/nvim ${ $( `\nE\n", ""),
    ("quoted-heredocs-specials", "", "cat <<'E'\n~/.config ${ $( ` ' \" $' #\nE\n", ""),
    ("unquoted-heredocs-escaped-specials", "",
     "cat <<E\n~/.config/nvim " + B + "${ " + B + "$( " + B + "`\nE\n", ""),
    ("one-heredoc-many-config", "cat <<'E'\n", "~/.config/nvim/x\n", ""),
    ("heredoc-delims-config", "", "cat <<.config\nx\n.config\n", ""),
    ("subscripts-naming-config", "", "a[1 #~/.config/nvim]=1\n", ""),
    ("regex-groups-config", "", "[[ x =~ (~/.config/nvim #) ]]; ", ""),
    ("regex-bars-config", "", "[[ x =~ a|~/.config/nvim|b ]]; ", ""),
    ("ansi-config", "echo ", "$'~/.config/nvim" + B + "x2e' ", ""),
    ("ansi-desync-config", "", "echo '$'; ls ~/.config/nvim; ", ""),
    ("escaped-dots-config", "echo ", "~/" + B + "x2econfig/nvim ", ""),
    ("op-rep-config", "echo ", "${x/a/~/.config/nvim} ", ""),
    ("op-trim-config", "echo ", "${x#~/.config/nvim} ", ""),
    ("op-nested-config", "echo ", "${x/${y#", "~/.config/nvim}/}"),
    ("eval-config", "", "eval 'ls ~/.config/nvim'; ", ""),
    ("dollar-bracket-config", "echo ", "$[1+", "]"),
    ("function-bodies-config", "", "f() { ls ~/.config/nvim; }\n", ""),
    ("cr-hash-config", "", "echo x\r# ~/.config/nvim\n", ""),
    ("pipe-amp-config", "", "ls ~/.config/nvim |& ", ""),
    ("declare-config", "", "declare p=~/.config/nvim/x; ", ""),
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


_shape7 = pytest.mark.parametrize(
    "prefix,unit,close", [s[1:] for s in SHAPES7], ids=[s[0] for s in SHAPES7])


@_shape7
def test_reread_shape_before_a_forbidden_read_is_denied(prefix, unit, close):
    assert _blocked(_shape(prefix, unit, close, SIZE, FORBIDDEN_TAIL)) is True


# A shape nested thousands deep is refused as unreadable by design
# (docs/security.md: nesting caps), so it is left out of the allow check.
_NESTED = {"op-nested-config"}


@pytest.mark.parametrize(
    "prefix,unit,close", [s[1:] for s in SHAPES7 if s[0] not in _NESTED],
    ids=[s[0] for s in SHAPES7 if s[0] not in _NESTED])
def test_reread_shape_before_a_harmless_tail_is_allowed(prefix, unit, close):
    assert _blocked(_shape(prefix, unit, close, SIZE, HARMLESS_TAIL)) is False


@_shape7
def test_reread_shape_before_a_forbidden_read_is_under_budget(prefix, unit, close):
    cmd = _shape(prefix, unit, close, SIZE, FORBIDDEN_TAIL)
    assert _best_of_three(cmd) < BUDGET_S


# Over the budget at abe7ead3 on an idle machine (tester-iter7-004): a
# quoted here-document body holding ``${ $( ` ' " $' #``, best of 9 0.54 s
# at 100 KB (0.23 s at bcb93100); the ``${ $( `` body 0.50-0.51 s. Not
# strict: a faster runner may pass them.
OVER_BUDGET = {"quoted-heredocs-naming-config", "quoted-heredocs-specials",
               "unquoted-heredocs-escaped-specials"}


@pytest.mark.parametrize(
    "prefix,unit,close",
    [pytest.param(*s[1:], marks=pytest.mark.xfail(
        strict=False, reason="over budget at abe7ead3 (tester-iter7-004)"))
     if s[0] in OVER_BUDGET else s[1:] for s in SHAPES7],
    ids=[s[0] for s in SHAPES7])
def test_reread_shape_before_a_harmless_tail_is_under_budget(prefix, unit, close):
    cmd = _shape(prefix, unit, close, SIZE, HARMLESS_TAIL)
    assert _best_of_three(cmd) < BUDGET_S


@_shape7
def test_reread_shape_grows_linearly(prefix, unit, close):
    """100 KB costs at most 8x 25 KB (linear is 4x); under 50 ms counts
    as fast whatever the ratio. Three rounds for noise."""
    small_cmd = _shape(prefix, unit, close, SMALL, HARMLESS_TAIL)
    big_cmd = _shape(prefix, unit, close, SIZE, HARMLESS_TAIL)
    ok = False
    for _ in range(3):
        ok = _best_of_three(big_cmd) < max(8 * _best_of_three(small_cmd), 0.05)
        if ok:
            break
    assert ok is True


def test_reread_shapes_reach_100_kb():
    """Precondition for the timing cases: the inputs really are ~100 KB."""
    sizes = [len(_shape(s[1], s[2], s[3], SIZE, "")) for s in SHAPES7]
    assert min(sizes) >= SIZE - 200


def test_each_timing_run_checks_a_different_command():
    """Precondition: the uncached best-of-3 really varies the command."""
    seen = set()
    for _ in range(3):
        seen.add(": r%d;" % next(_run))
    assert len(seen) == 3
