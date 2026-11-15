"""Iteration 6 of the safety floor checks (design.md success criteria 15
and 16; docs/security.md "aipager's own commands and folders").

Whole commands, not single words. Every row was run by real bash 5.2 in
a throwaway HOME with PATH empty and every external command replaced by
a recorder, plus strace for the files bash itself opens (redirections,
``source``, ``$(<f)``): each command in CONTEXT_REACHING and ANSI_DESYNC
reads or writes a file in ~/.config/aipager or ~/.local/{share,state}/
aipager (or, for the ``cli-*`` rows, runs aipager / python -m aipager /
pkill aipager ... with the decoded name). The tester's harness lives in
the ship artifacts (tester6-tools/cmdfuzz.py).

ANSI_DESYNC: docs/security.md says a name split by ``$'...'`` is still
refused (``$'\\x61ipager'``), and each command's own target is refused
on its own. A ``$'`` that bash does NOT read as an ANSI-C quote earlier
in the command (inside single or double quotes, a comment or a
here-document body, e.g. ``grep -n 'TODO$' notes.txt;``) makes the floor
allow it. Not a route docs/security.md names as open, so each row is a
strict xfail (project convention for known gaps).

Only the documented surface is used: ``safety.bash_violation`` with
``safety.DENY_BASH_PATTERNS`` (entrypoints.md "Safety check surface").
Timing cases take the best of three runs so a loaded machine does not
fail them.
"""

from __future__ import annotations

import time

import pytest

from aipager.safety import DENY_BASH_PATTERNS, bash_violation

B = chr(92)  # a backslash


def _blocked(cmd: str) -> bool:
    return bash_violation(cmd, DENY_BASH_PATTERNS) is not None


def _params(rows):
    return pytest.mark.parametrize("cmd", [r[1] for r in rows],
                                   ids=[r[0] for r in rows])


# ---- gaps: a non-ANSI-C ``$'`` earlier in the command ----

UNDOCUMENTED = "undocumented gap (tester iteration 6 whole-command fuzz)"

# (id, prefix, target): the command is prefix + target
ANSI_DESYNC = [
    ('path-after-quoted-dollar', "echo '$'; ",
     "cat ~/.config/$'\\x61'ipager/aipager.yaml"),
    ('path-after-grep-anchor', "grep -n 'TODO$' notes.txt; ",
     "sed -i s/user/owner/ ~/.config/$'\\x61'ipager/aipager.yaml"),
    ('path-after-ere-anchor', "grep -E '^a|b$' f && ",
     "cat ~/.config/$'\\x61ipager'/aipager.yaml"),
    ('path-after-quoted-dollar-newline', "echo 'cost: 5$'\n",
     "cat ~/.config/$'\\x61'ipager/policy.yaml"),
    ('path-octal', "echo '$'; ",
     "cat ~/.config/$'\\141'ipager/x"),
    ('path-local-share', "echo '$'; ",
     "cat ~/.local/share/$'\\x61'ipager/x"),
    ('path-local-state', "echo '$'; ",
     "cat ~/.local/state/aipag$'\\x65'r/x"),
    ('path-dot-config', "echo '$'; ",
     "cat ~/$'\\x2e'config/aipager/x"),
    ('path-two-ansi-parts', "echo '$'; ",
     "cat ~/.con$'\\x66'ig/$'\\x61'ipager/aipager.yaml"),
    ('path-quoted-home', "echo '$'; ",
     'cat "$HOME"/.config/$\'\\x61\'ipager/x'),
    ('path-after-dq-dollar-quote', 'echo "$\'"; ',
     "cat ~/.config/$'\\x61'ipager/x"),
    ('path-after-dq-escaped-dollar', 'echo "\\$\'"; ',
     "cat ~/.config/$'\\x61'ipager/x"),
    ('path-after-comment', "echo # it's 5$'\n",
     "cat ~/.config/$'\\x61'ipager/x"),
    ('path-after-quoted-heredoc', "cat <<'E'\n5$'\nE\n",
     "cat ~/.config/$'\\x61'ipager/x"),
    ('path-after-heredoc', "cat <<E\n5\\$'\nE\n",
     "cat ~/.config/$'\\x61'ipager/x"),
    ('cd-config-then-relative', "echo '$'; ",
     "cd ~/$'\\x2e'config && cat aipager/x"),
    ('path-in-quoted-cmdsub', '\'echo $\'; echo "{"; ',
     'cat "$(echo ~/.config/$\'\\x61\'ipager/x)"'),
    ('path-in-arith-cmdsub', "echo ${x:-'}$'};\n",
     "echo $(( $(cat ~/.config/$'\\x61'ipager/x) + 0 )); echo done"),
    ('path-mid-name-after-dq', 'echo "\'$\'"; ',
     "cat ~/.config/aip$'\\x61'ger/x"),
    ('cli-setup-group', "grep -n 'TODO$' notes.txt; ",
     "$'\\x61'ipager setup group --chat-id -1 --member 5:me:owner --force"),
    ('cli-setup-token-stdin', "echo '$'; ",
     "$'\\x61'ipager setup --token-stdin --chat-id 5 --role owner --force < f"),
    ('cli-config', "echo '$'; ",
     "aip$'\\x61'ger config"),
    ('cli-uninstall', "echo '$'; ",
     "$'\\x61ipager' uninstall -y"),
    ('cli-python-m', "echo '$'; ",
     "python3 -m $'\\x61'ipager.cli setup"),
    ('cli-python-c-import', "echo '$'; ",
     "python3 -c $'import \\x61ipager.scope'"),
    ('cli-pkill', "grep -n 'x$' f; ",
     "pkill -f $'\\x61'ipager"),
    ('cli-killall', "echo '$'; ",
     "killall $'\\x61'ipager"),
    ('cli-systemctl', "echo '$'; ",
     "systemctl --user stop $'\\x61'ipager"),
    ('cli-launchctl', "echo '$'; ",
     "launchctl kickstart -k gui/501/com.$'\\x61'ipager.daemon"),
    ('cli-cp-config', "echo '$'; ",
     "cp -r ~/$'\\x2e'config /tmp/x"),
]


@pytest.mark.parametrize(
    "cmd",
    [pytest.param(g[1] + g[2], marks=pytest.mark.xfail(strict=True,
                                                       reason=UNDOCUMENTED))
     for g in ANSI_DESYNC],
    ids=[g[0] for g in ANSI_DESYNC])
def test_ansi_c_desync_is_blocked(cmd):
    assert _blocked(cmd) is True


@pytest.mark.parametrize("target", [g[2] for g in ANSI_DESYNC],
                         ids=[g[0] for g in ANSI_DESYNC])
def test_ansi_c_target_alone_is_blocked(target):
    """Control: the target is refused on its own, so each gap row fails
    only because of the prefix before it."""
    assert _blocked(target) is True


# ---- review-5's five commands (all reach in bash except the ${#{} one,
# where "bad substitution" ends the shell; still refused by design) ----

REVIEW5 = [
    ("hash-brace", "x=a; echo ${x#{} ; cat ~/.config/[a]ipager/aipager.yaml"),
    ("length-brace", "echo ${#{} ; cat ~/.config/[a]ipager/aipager.yaml"),
    ("replace-brace", 'echo "${x/{/}" ; cat ~/.local/share/[a]ipager/x'),
    ("default-brace",
     "echo ${x:-{} && cat ~/.config/aip[a" + B + "]]ger/aipager.yaml"),
    ("comment-quote-brace",
     "echo hi # \"it's\" ${\ncat ~/.config/[[:a]ipager/aipager.yaml"),
]


@_params(REVIEW5)
def test_review_5_command_is_blocked(cmd):
    assert _blocked(cmd) is True


# ---- the target inside a construct, after a desync prefix ----

CONTEXT_REACHING = [
    ('ctx-000', 'cat 3< ~/.config/[a]ipager/x'),
    ('ctx-001', 'echo x &> ~/.config/[a]ipager/x'),
    ('ctx-002', 'cat <> ~/.config/[a]ipager/x'),
    ('ctx-003', 'echo x >| ~/.config/a?pager/x'),
    ('ctx-004', 'echo x 2>> ~/.local/share/[a]ipager/x'),
    ('ctx-005', 'exec 3< ~/.config/[a]ipager/x'),
    ('ctx-006', 'a=([0]=~/.config/[a]ipager/x); cat ${a[0]}'),
    ('ctx-007', 'select f in ~/.config/[a]ipager/x; do cat $f; break; done <<< 1'),
    ('ctx-008', 'coproc cat ~/.config/[a]ipager/x'),
    ('ctx-009', 'function f { cat ~/.config/[a]ipager/x; }; f'),
    ('ctx-010', 'cat ${unset_v-~/.config/[a]ipager/x}'),
    ('ctx-011', 'cat ${unset_v:=~/.config/[a]ipager/x}'),
    ('ctx-012', 'cat ${x:+~/.config/[a]ipager/x}'),
    ('ctx-013', 'cat ${unset_v:-~/.config/[a]ipager/x}'),
    ('ctx-014', 'cat "${unset_v:-$(echo ~/.config/[a]ipager/x)}"'),
    ('ctx-015', 'cat $(echo $(echo ~/.config/[a]ipager/x))'),
    ('ctx-016', 'cat `echo \\`echo ~/.config/[a]ipager/x\\``'),
    ('ctx-017', 'cat <<E\n$(cat ~/.config/[a]ipager/x)\nE'),
    ('ctx-018', 'cat <<-E\n\t`cat ~/.config/[a]ipager/x`\n\tE'),
    ('ctx-019', 'cat <<E\n${unset_v:-$(cat ~/.config/[a]ipager/x)}\nE'),
    ('ctx-020', 'cat <<<$(cat ~/.config/[a]ipager/x)'),
    ('ctx-021', 'cat <<<"$(cat ~/.config/[a]ipager/x)"'),
    ('ctx-022', 'x=$(<~/.config/[a]ipager/x)'),
    ('ctx-023', 'echo $(( $(cat ~/.config/[a]ipager/x | wc -l) ))'),
    ('ctx-024', '[[ $(cat ~/.config/[a]ipager/x) ]]'),
    ('ctx-025', '(( $(cat ~/.config/[a]ipager/x) ))'),
    ('ctx-026', 'case $(cat ~/.config/[a]ipager/x) in *) :;; esac'),
    ('ctx-027', 'for ((i=0; i<$(cat ~/.config/[a]ipager/x); i++)); do :; done'),
    ('ctx-028', 'export V=$(cat ~/.config/[a]ipager/x)'),
    ('ctx-029', 'local_v=`cat ~/.config/a?pager/x`'),
    ('ctx-030', 'declare -a A=(~/.config/[a]ipager/*)\ncat "${A[@]}"'),
    ('ctx-031', 'cat ~/.config/[a]ip\\\nager/x'),
    ('ctx-032', 'cat ~/.con\\\nfig/[a]ipager/x'),
    ('ctx-033', 'echo ${x#{} ; cat <<E\n$(cat ~/.config/[a]ipager/x)\nE'),
    ('ctx-034', "echo 'it'\\''s' ; cat ~/.config/[a]ipager/x"),
    ('ctx-035', 'echo "a\\"b" ; cat ~/.config/[a]ipager/x'),
    ('ctx-036', "echo $'it\\'s' ; cat ~/.config/[a]ipager/x"),
    ('ctx-037', "echo $'\\'' ; cat ~/.config/[a]ipager/x"),
    ('ctx-038', "echo a#'b' ; cat ~/.config/[a]ipager/x"),
    ('ctx-039', "echo ${x:-'}'} ; cat ~/.config/[a]ipager/x"),
    ('ctx-040', 'echo "${x:-\'}\'}" ; cat ~/.config/[a]ipager/x \'}\''),
    ('ctx-041', 'echo ${x:-"}"} ; cat ~/.config/[a]ipager/x'),
    ('ctx-042', "echo ${x/'}'/a} ; cat ~/.config/[a]ipager/x"),
    ('ctx-043', 'echo "${x/\'}\'/a}" ; cat ~/.config/[a]ipager/x'),
    ('ctx-044', 'echo "${x/"}"/a}" ; cat ~/.config/[a]ipager/x'),
    ('ctx-045', "echo $(echo ')' ) ; cat ~/.config/[a]ipager/x"),
    ('ctx-046', 'echo $(case x in x) echo;; esac) ; cat ~/.config/[a]ipager/x'),
    ('ctx-047', 'echo $(echo "${x:-)}") ; cat ~/.config/[a]ipager/x'),
    ('ctx-048', 'echo $(( 1 )) ; cat ~/.config/[a]ipager/x'),
    ('ctx-049', 'echo $(( (1) )) ; cat ~/.config/[a]ipager/x'),
    ('ctx-050', 'echo $( (echo) ) ; cat ~/.config/[a]ipager/x'),
    ('ctx-051', 'echo $((echo a) ) ; cat ~/.config/[a]ipager/x'),
    ('ctx-052', "cat <<'E' ; cat ~/.config/[a]ipager/x\n$(\nE"),
    ('ctx-053', 'cat <<E ; cat ~/.config/[a]ipager/x\n${\nE'),
    ('ctx-054', 'cat <<"E" ; cat ~/.config/[a]ipager/x\n\'\nE'),
    ('ctx-055', "cat <<E\\\nX\n'\nEX\ncat ~/.config/[a]ipager/x"),
    ('ctx-056', "cat <<\\E\n'\nE\ncat ~/.config/[a]ipager/x"),
    ('ctx-057', "cat <<E'X'\n'\nEX\ncat ~/.config/[a]ipager/x"),
    ('ctx-058', "cat <<-E\n\t'\n\tE\ncat ~/.config/[a]ipager/x"),
    ('ctx-059', "cat << E\n'\n E\nE\ncat ~/.config/[a]ipager/x"),
    ('ctx-060', "cat <<E\n'\nE \nE\ncat ~/.config/[a]ipager/x"),
    ('ctx-061', "cat <<E <<'F'\na\nE\n'\nF\ncat ~/.config/[a]ipager/x"),
    ('ctx-062', "cat <<E; echo '\nx\nE\n' ; cat ~/.config/[a]ipager/x"),
    ('ctx-063', 'echo $(cat <<E\n)\nE\n) ; cat ~/.config/[a]ipager/x'),
    ('ctx-064', 'echo "$(cat <<\'E\'\n"\nE\n)" ; cat ~/.config/[a]ipager/x'),
    ('ctx-065', 'case x in x) echo;; esac ; cat ~/.config/[a]ipager/x'),
    ('ctx-066', 'case x in (x) cat ~/.config/[a]ipager/x;; esac'),
    ('ctx-067', "case x in x|')') : ;; esac ; cat ~/.config/[a]ipager/x"),
    ('ctx-068', '[[ a =~ ( ) ]] ; cat ~/.config/[a]ipager/x'),
    ('ctx-069', '[[ x =~ ^\\{ ]] || cat ~/.config/[a]ipager/x'),
    ('ctx-070', "[[ x =~ '#' ]] || cat ~/.config/[a]ipager/x"),
    ('ctx-071', '[[ x =~ [#] ]] || cat ~/.config/[a]ipager/x'),
    ('ctx-072', 'echo x#y ; cat ~/.config/[a]ipager/x'),
    ('ctx-073', "echo ${x}#y'z' ; cat ~/.config/[a]ipager/x"),
    ('ctx-074', "echo $#'x' ; cat ~/.config/[a]ipager/x"),
    ('ctx-075', 'echo ${#} ; cat ~/.config/[a]ipager/x'),
    ('ctx-076', 'echo $# ; cat ~/.config/[a]ipager/x'),
    ('ctx-077', 'echo $(echo #)\n) ; cat ~/.config/[a]ipager/x'),
    ('ctx-078', 'echo `echo #`; cat ~/.config/[a]ipager/x'),
    ('ctx-079', "f() { echo '}'; } ; cat ~/.config/[a]ipager/x"),
    ('ctx-080', "f() ( echo ')' ) ; cat ~/.config/[a]ipager/x"),
    ('ctx-081', "a=( ')' '(' ) ; cat ~/.config/[a]ipager/x"),
    ('ctx-082', 'a=( # )\n) ; cat ~/.config/[a]ipager/x'),
    ('ctx-083', "cat <(echo ')') ~/.config/[a]ipager/x"),
    ('ctx-084', 'cat <(cat ~/.config/[a]ipager/x)'),
    ('ctx-085', 'cat >(cat ~/.config/[a]ipager/x)'),
    ('ctx-086', "echo {a,'}'} ; cat ~/.config/[a]ipager/x"),
    ('ctx-087', 'echo $[ 1 ] ; cat ~/.config/[a]ipager/x'),
    ('ctx-088', 'echo $"loc\'" ; cat ~/.config/[a]ipager/x'),
    ('ctx-089', 'echo "$(echo \'"\')" ; cat ~/.config/[a]ipager/x'),
    ('ctx-090', 'echo "`echo \'"\'`" ; cat ~/.config/[a]ipager/x'),
    ('ctx-091', 'echo ${x:-`echo }`} ; cat ~/.config/[a]ipager/x'),
    ('ctx-092', 'echo "${x:-`echo }`}" ; cat ~/.config/[a]ipager/x'),
    ('ctx-093', 'echo ${x:-$(echo })} ; cat ~/.config/[a]ipager/x'),
    ('ctx-094', 'echo ${x:-${y:-}}} ; cat ~/.config/[a]ipager/x'),
    ('ctx-095', 'echo ${x%%}*} ; cat ~/.config/[a]ipager/x'),
    ('ctx-096', 'echo ${x//\\}/} ; cat ~/.config/[a]ipager/x'),
    ('ctx-097', 'echo "${x//\\"/}" ; cat ~/.config/[a]ipager/x'),
    ('ctx-098', 'echo ${x/#a/{} ; cat ~/.config/[a]ipager/x'),
    ('ctx-099', 'echo ${x@Q} ; cat ~/.config/[a]ipager/x'),
    ('ctx-100', 'echo ${!x*} ; cat ~/.config/[a]ipager/x'),
    ('ctx-101', "echo $'\\x27' ; cat ~/.config/[a]ipager/x"),
    ('ctx-102', "echo \\' ; cat ~/.config/[a]ipager/x"),
    ('ctx-103', 'echo \\" ; cat ~/.config/[a]ipager/x'),
    ('ctx-104', 'echo \\${ ; cat ~/.config/[a]ipager/x'),
    ('ctx-105', 'echo \\$\\( ; cat ~/.config/[a]ipager/x'),
    ('ctx-106', 'echo \\` ; cat ~/.config/[a]ipager/x'),
    ('ctx-107', "echo '\\' ; cat ~/.config/[a]ipager/x"),
    ('ctx-108', 'echo "\\\\" ; cat ~/.config/[a]ipager/x'),
    ('ctx-109', 'echo "\\\'" ; cat ~/.config/[a]ipager/x'),
    ('ctx-110', 'time echo ; cat ~/.config/[a]ipager/x'),
    ('ctx-111', '! echo ; cat ~/.config/[a]ipager/x'),
    ('ctx-112', 'coproc cat ; cat ~/.config/[a]ipager/x'),
    ('ctx-113', 'echo a & cat ~/.config/[a]ipager/x'),
    ('ctx-114', 'echo a |& cat ~/.config/[a]ipager/x'),
    ('ctx-115', '{ echo a; } ; cat ~/.config/[a]ipager/x'),
    ('ctx-116', "{ echo '}'; } ; cat ~/.config/[a]ipager/x"),
    ('ctx-117', 'echo hi # "it\'s" ${\ncat ~/.config/[a]ipager/x'),
    ('ctx-118', ": <<'#'\n'\n#\ncat ~/.config/[a]ipager/x"),
    ('ctx-119', "cat <<E$x\n'\nE$x\ncat ~/.config/[a]ipager/x"),
    ('ctx-120', 'cat <<"E"x\n\'\nEx\ncat ~/.config/[a]ipager/x'),
    ('ctx-121', "cat <<$'E'\n'\nE\ncat ~/.config/[a]ipager/x"),
    ('ctx-122', "cat <<E\n'\nE\ncat ~/.config/[a]ipager/x"),
]


@_params(CONTEXT_REACHING)
def test_command_reaching_aipager_folders_is_blocked(cmd):
    assert _blocked(cmd) is True


# ---- ordinary dev commands with ${, $(, quotes, here-docs, .config ----

ALLOWED = [
    ('dev-000', "grep '${' ~/.config/nvim/*.lua"),
    ('dev-001', 'cat <<EOF > ~/.config/nvim/x'),
    ('dev-002', 'echo "$(date)" >> ~/.config/fish/log'),
    ('dev-003', "grep -rn '\\${' ~/projects/app/src"),
    ('dev-004', "sed -i 's/${OLD}/${NEW}/g' ~/.config/nvim/init.lua"),
    ('dev-005', 'echo "${HOME}/.config/nvim" > /tmp/x'),
    ('dev-006', "cat > ~/.config/git/ignore <<'EOF'"),
    ('dev-007', 'for f in ~/.config/nvim/lua/*.lua; do echo "$f"; done'),
    ('dev-008', 'ls "$(dirname ~/.config/nvim/init.lua)"'),
    ('dev-009', 'export PATH="$HOME/.local/bin:$PATH"'),
    ('dev-010', 'x=$(git rev-parse --show-toplevel) && cd "$x"'),
    ('dev-011', 'echo $((1 << 4))'),
    ('dev-012', 'echo "$(( $(date +%s) - 60 ))"'),
    ('dev-013', 'files=(src/*.py); echo "${#files[@]}"'),
    ('dev-014', 'echo ${files[@]:0:2}'),
    ('dev-015', 'diff <(sort a.txt) <(sort b.txt)'),
    ('dev-016', "cat ~/.config/nvim/init.lua | grep -v '^--'"),
    ('dev-017', '[[ -f ~/.config/fish/config.fish ]] && echo yes'),
    ('dev-018', 'case "$1" in start) echo s;; *) echo x;; esac'),
    ('dev-019', 'echo `date`'),
    ('dev-020', "git log --format='%h %s' -n 5"),
    ('dev-021', 'printf \'%s\\n\' "${PATH//:/ }"'),
    ('dev-022', 'echo "${VAR:-default}"'),
    ('dev-023', 'echo "${VAR:-~/.config/nvim}"'),
    ('dev-024', 'cat ${XDG_CONFIG_HOME:-~/.config}/nvim/init.lua'),
    ('dev-025', 'cp ~/.config/nvim/init.lua ~/.config/nvim/init.lua.bak'),
    ('dev-026', 'ls ~/.config/nvim/{lua,after}'),
    ('dev-027', "python3 - <<'EOF'"),
    ('dev-028', 'python3 -c \'print("${x}")\''),
    ('dev-029', "awk '{print $1}' ~/.config/git/config"),
    ('dev-030', 'jq \'.["a"]\' package.json'),
    ('dev-031', "rg -n '\\$\\{' src/"),
    ('dev-032', 'echo \'it\'"\'"\'s\' # comment with "quotes"'),
    ('dev-033', 'echo "a#b" # it\'s fine'),
    ('dev-034', 'make -C build -j4 2>&1 | tail -n 20'),
    ('dev-035', 'npm run build && npm test'),
    ('dev-036', 'docker run --rm -v "$PWD":/w -w /w node:20 npm ci'),
    ('dev-037', "find . -name '*.py' -newer setup.py"),
    ('dev-038', 'tar czf /tmp/src.tgz src/'),
    ('dev-039', 'mkdir -p ~/.config/myapp && cp config.toml ~/.config/myapp/'),
    ('dev-040', 'cat ~/.local/share/nvim/lazy/x.lua'),
    ('dev-041', 'ls ~/.local/state/nvim'),
    ('dev-042', 'read -r line < ~/.config/fish/fish_variables'),
    ('dev-043', 'while IFS= read -r l; do echo "$l"; done < ~/.config/git/config'),
    ('dev-044', 'echo "${x#{}"'),
    ('dev-045', 'echo ${x#\\{}'),
    ('dev-046', 'tee -a ~/.config/fish/log <<< "$(date)"'),
    ('dev-047', 'cat <<-EOF >> ~/.config/fish/config.fish'),
    ('dev-048', '\tset -x EDITOR nvim'),
    ('dev-049', '\tEOF'),
    ('dev-050', '{ echo a; echo b; } > ~/.config/myapp/out'),
    ('dev-051', '( cd src && pytest -q )'),
    ('dev-052', 'git commit -m "fix: handle \\${var} in config"'),
    ('dev-053', "sed -n '/^\\[core\\]/,/^\\[/p' ~/.config/git/config"),
    ('dev-054', 'ls ~/.config/'),
    ('dev-055', 'cat ~/.config/starship.toml'),
    ('dev-056', "grep -c '$(' scripts/*.sh"),
    ('dev-057', 'echo ~/.config/nvim/*.lua'),
    ('dev-058', 'vim ~/.config/nvim/lua/plugins.lua'),
    ('dev-059', 'code ~/.config/Code/User/settings.json'),
    ('dev-060', 'ln -s ~/dotfiles/nvim ~/.config/nvim'),
    ('dev-061', 'chmod 600 ~/.config/myapp/secret'),
    ('dev-062', "echo $'line1\\nline2' > ~/.config/myapp/x"),
    ('dev-063', 'f() { echo "$1"; }; f a'),
    ('dev-064', 'arr=(a b c); for i in "${arr[@]}"; do echo $i; done'),
    ('dev-065', 'echo "${arr[*]}"'),
    ('dev-066', 'VAR=$(cat ~/.config/myapp/version)'),
    ('dev-067', 'echo "$(<~/.config/myapp/version)"'),
    ('dev-068', "cat ~/.config/nvim/init.lua; echo '}'"),
    ('dev-069', "echo '$(' && ls ~/.config/nvim"),
    ('dev-070', 'echo "it\'s" && cat ~/.config/nvim/init.lua'),
    ('dev-072', 'cat <<EOF > ~/.config/nvim/x\nlet ${x} = 1\nEOF'),
    ('dev-073', 'cat <<\'EOF\' > ~/.config/myapp/run.sh\n#!/bin/sh\necho "$(date)" ${HOME}\nEOF'),
    ('dev-074', "python3 - <<'EOF'\nimport json\nprint(json.dumps({'a': '${x}'}))\nEOF"),
    ('dev-075', 'cat <<EOF\n$(git status)\nEOF'),
    ('dev-076', 'for f in ~/.config/nvim/*.lua; do\n  echo "$f"\ndone'),
    ('dev-077', 'if [ -f ~/.config/fish/config.fish ]; then\n  cat ~/.config/fish/config.fish\nfi'),
    ('dev-078', 'git commit -m "$(cat <<\'EOF\'\nfix the ${x} parser\nEOF\n)"'),
    ('dev-079', "echo hi # don't\ncat ~/.config/nvim/init.lua"),
    ('dev-080', 'make \\\n  -j4 \\\n  all'),
    ('dev-081', 'echo "${x#{}" ; cat ~/.config/nvim/init.lua'),
    ('dev-082', "awk '/{/{c++} END{print c}' ~/.config/nvim/init.lua"),
    ('dev-083', "echo $'it\\'s' ; cat ~/.config/nvim/x"),
    ('dev-084', 'grep "`" ~/.config/nvim/x'),
    ('dev-085', "echo ')' ; ls ~/.config/nvim"),
    ('dev-086', 'cat <<E\nhello\nE\nls ~/.config/nvim'),
    ('dev-087', 'x=$(case a in a) echo b;; esac); ls ~/.config/nvim'),
    ('dev-088', '[[ $x =~ ^\\{ ]] && cat ~/.config/nvim/x'),
    ('dev-089', 'cp ~/.config/nvim/init.lua{,.bak}'),
    ('dev-090', 'ls ~/.config/nvim/lua/**/*.lua'),
    ('dev-091', 'echo ${x:-{}} ; ls ~/.config/fish'),
    ('dev-092', 'printf \'%s\' "${x/\\{/(}" > ~/.config/myapp/out'),
    ('dev-093', "echo '# ${' ; cat ~/.config/nvim/init.lua"),
    ('dev-094', 'echo a # ${\ncat ~/.config/nvim/init.lua'),
    ('dev-095', "echo a # it's\ncat ~/.config/nvim/init.lua"),
    ('dev-096', 'cat ~/.config/nvim/init.lua <<< "$(echo \'}\')"'),
    ('dev-097', 'echo $(( (1+2) * 3 )) > ~/.config/myapp/n'),
    ('dev-098', 'a=( ~/.config/nvim/lua/*.lua ); echo ${#a[@]}'),
    ('dev-099', 'cat ~/.config/nvim/init.lua > >(wc -l)'),
    ('dev-100', '(cd ~/.config/nvim && git status)'),
    ('dev-101', 'cd ~/.config/nvim && git pull'),
    ('dev-102', 'echo `echo \\`date\\``; ls ~/.config/nvim'),
    ('dev-103', "cat << 'END' | tee ~/.config/myapp/notes\nit's ${not} $(expanded)\nEND"),
    ('dev-104', "sed -e 's/\\$(/(/' ~/.config/nvim/init.lua"),
    ('dev-105', 'test -d ~/.config/nvim || mkdir ~/.config/nvim'),
    ('dev-106', 'cat ${XDG_CONFIG_HOME:-$HOME/.config}/git/config'),
    ('dev-107', 'grep -l "${PATTERN:-TODO}" ~/.config/nvim/lua/*.lua'),
    ('dev-108', 'for d in ~/.config/{nvim/lua,fish/functions}; do ls $d; done'),
    ('dev-109', 'rsync -a ~/.config/nvim/ /tmp/nvim-backup/'),
    ('dev-110', 'cp -r ~/.config/nvim /tmp/x'),
    ('dev-111', 'ls -la ~/.local/share/'),
    ('dev-112', 'du -sh ~/.local/share/nvim'),
    ('dev-113', 'echo $HOME/.config/nvim/init.lua'),
    ('dev-114', 'cat "$HOME/.config/nvim/init.lua"'),
    ('dev-115', "cat '/home/alice/.config/nvim/init.lua'"),
    ('dev-116', "cat ~/.config/nvim/init.lua # it's the config ${"),
    ('dev-117', 'echo "${x:-$(date)}" >> ~/.config/fish/log'),
    ('dev-118', 'echo "$(echo "$(date)")" >> ~/.config/fish/log'),
]


@_params(ALLOWED)
def test_ordinary_dev_command_stays_allowed(cmd):
    assert _blocked(cmd) is False


# ---- timing: shapes aimed at the word reader ----

BUDGET_S = 0.5
SIZE = 100_000
SMALL = 25_000
HARMLESS_TAIL = " ; ls ~/.config/nvim"
FORBIDDEN_TAIL = "\ncat ~/.config/[a]ipager/aipager.yaml"

# (id, prefix, repeated unit, closing unit repeated as often or "")
SHAPES = [
    ("deep-open-dollar-brace", "echo ", "${", ""),
    ("deep-closed-default", "echo ", "${x:-", "}"),
    ("dollar-brace-hash-brace", "echo ", "${x#{} ", ""),
    ("nested-brace-in-dollar", "echo ", "${x:-{", "}"),
    ("many-heredocs", "", "cat <<E\na\nE\n", ""),
    ("many-heredocs-dash", "", "cat <<-E\n\ta '\n\tE\n", ""),
    ("many-herestrings", "", "cat <<<\"'\" ; ", ""),
    ("heredoc-unterminated-body", "cat <<E\n", "${ $( ` ' \" \n", ""),
    ("long-comment-quotes", "echo a # ", "'\"${$(`", ""),
    ("many-comment-lines", "", "# it's ${ $( `\n", ""),
    ("many-dollar-paren-open", "echo ", "$(", ""),
    ("many-dollar-paren-closed", "echo ", "$(echo ", ")"),
    ("many-arith", "echo ", "$((1+", "))"),
    ("many-arith-shift", "echo ", "$((1<<2)) ", ""),
    ("many-backticks-escaped", "echo `", B + "`echo ", ""),
    ("many-backtick-pairs", "", "echo `echo a`; ", ""),
    ("alternating-quotes", "echo ", "'a'\"b\"$'c'", ""),
    ("many-ansi-c", "echo ", "$'" + B + "x61" + B + "'' ", ""),
    ("many-quoted-dollar", "echo ", "'$' ", ""),
    ("many-case", "", "case x in x) echo ')';; esac; ", ""),
    ("many-continuations", "echo ", "a " + B + "\n", ""),
    ("many-procsub", "cat ", "<(echo a) ", ""),
    ("many-dbl-bracket", "", "[[ a < b ]] && ", ""),
    ("many-arrays", "", "a=(1 ')' 2); ", ""),
    ("many-open-brace", "echo ", "{", ""),
    ("many-close-brace", "echo ", "}", ""),
    ("many-subshells", "", "(", ")"),
    ("default-with-cmdsub", "cat ", "${x:-$(echo ", ")}"),
    ("brace-dollar-mix", "echo ", "${x:-$(echo ${y:-`echo ", ""),
    ("glob-words-near-config", "ls ", "~/.config/n?im/*.lua ", ""),
]


def _shape(prefix: str, unit: str, close: str, size: int, tail: str) -> str:
    n = max(1, (size - len(prefix) - len(tail)) // (len(unit) + len(close)))
    return prefix + unit * n + close * n + tail


def _best_of_three(cmd: str) -> float:
    best = float("inf")
    for _ in range(3):
        t0 = time.perf_counter()
        bash_violation(cmd, DENY_BASH_PATTERNS)
        best = min(best, time.perf_counter() - t0)
    return best


_shape_params = pytest.mark.parametrize(
    "prefix,unit,close", [s[1:] for s in SHAPES], ids=[s[0] for s in SHAPES])


@_shape_params
def test_reader_shape_before_a_forbidden_read_is_denied(prefix, unit, close):
    assert _blocked(_shape(prefix, unit, close, SIZE, FORBIDDEN_TAIL)) is True


@_shape_params
def test_reader_shape_before_a_forbidden_read_is_under_budget(prefix, unit, close):
    cmd = _shape(prefix, unit, close, SIZE, FORBIDDEN_TAIL)
    assert _best_of_three(cmd) < BUDGET_S


@_shape_params
def test_reader_shape_before_a_harmless_tail_is_under_budget(prefix, unit, close):
    cmd = _shape(prefix, unit, close, SIZE, HARMLESS_TAIL)
    assert _best_of_three(cmd) < BUDGET_S


@_shape_params
def test_reader_shape_grows_linearly(prefix, unit, close):
    """100 KB costs at most 8x 25 KB (linear is 4x); under 50 ms counts
    as fast whatever the ratio. A quadratic shape fails every round;
    noise gets three rounds."""
    small_cmd = _shape(prefix, unit, close, SMALL, HARMLESS_TAIL)
    big_cmd = _shape(prefix, unit, close, SIZE, HARMLESS_TAIL)
    ok = False
    for _ in range(3):
        ok = _best_of_three(big_cmd) < max(8 * _best_of_three(small_cmd), 0.05)
        if ok:
            break
    assert ok is True


def test_reader_shapes_reach_100_kb():
    """Precondition for the timing cases: the inputs really are ~100 KB."""
    sizes = [len(_shape(s[1], s[2], s[3], SIZE, "")) for s in SHAPES]
    assert min(sizes) >= SIZE - 200
