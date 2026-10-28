"""Which Bash commands carry Claude Code's risky-removal countdown
(roadmap 8.128; design.md criteria 8 and 9): ``rm_countdown.runs_removal``
reads a command the way Claude Code's dangerous-removal check does (rm and
rmdir, behind wrappers, in ``find -exec``, in ``sh -c`` / ``eval``
scripts) and errs to no clock on any doubt."""

from __future__ import annotations

import pytest

from aipager import rm_countdown

REMOVALS = [
    "rm -rf x", "/bin/rm x", "\\rm x", "RM x", "rmdir d", "sudo rm x", "env A=1 rm x",
    "timeout 5 rm x", "nice -n 10 rm x", "xargs rm", "ls | xargs -0 rm -f",
    "find . -name '*.o' -exec rm {} +", "sh -c 'rm -rf \"$D\"'",
    'bash -lc "cd /tmp && rm -r x"', "eval 'rm x'", "echo a; rm x", "(rm x)",
    "x=$(rm y)",
    # More shapes of the same rules.
    "sudo -u root rm -rf /home/alice/app", "A=1 B=2 rm x", "cd /tmp && rm -rf build",
    "true || rm x", "make & rm x", "{ rm x; }", "if true; then rm x; fi",
    r"find . -execdir /bin/rm {} \;", "busybox rm x", "busybox sh -c 'rm x'",
    "sh -c \"sh -c 'rm x'\"", "rm \\\n -rf x", "2>/dev/null rm x", "> out rm x",
    "echo `rm x`", "rm x 2>&1 | tee log", "sudo -E rm x", "nohup rm -rf x &",
    # time is a wrapper: its option is skipped.
    "time -p rm x", "time rm x",
    # One < is a redirection; << inside quotes is text, not a heredoc.
    "rm x < /dev/null", "rm 'a<<b'", 'rm "x<<y"', "rm x\\<<y",
    # A heredoc's body is left out; an rm outside it still counts.
    "rm x; cat <<EOF\nhi\nEOF", "rm x <<EOF\nyes\nEOF",
]
NOT_REMOVALS = [
    "ls", "echo rm", "grep -r rm .", 'git commit -m "rm old"', "npm run rm-cache",
    "rmx", "perm", "cat rm.txt",
    # More shapes.
    "", "   ", "echo 'rm -rf x'", "# rm x", "echo hi # rm x", '"rm -rf x"',
    "command -v rm", "sh -c 'echo rm'", "python -c 'import os; os.remove(1)'",
    "find . -delete", "git rm x", "echo x | rmate",
    # A separator inside quotes is text, not a new command.
    'echo "done; rm -rf x"', "git commit -m 'fix; rm old'", 'echo "$(date) && rm x"',
    # Deeper than Claude Code reads (two levels of scripts).
    "sh -c \"sh -c 'sh -c rm'\"",
    # A heredoc's body and a here-string are data, not commands (Claude
    # Code's parser reads them so).
    "cat <<EOF > f.sh\nrm -rf x\nEOF", "cat > f <<'EOF'\nrm x\nEOF",
    "cat <<-EOF\n\trm x\n\tEOF", "bash <<< 'rm x'",
    "sh -c 'cat <<EOF\nrm x\nEOF'",
]


@pytest.mark.parametrize("command", REMOVALS, ids=[f"rm{i}" for i in range(len(REMOVALS))])
def test_a_removal_is_seen(command):
    assert rm_countdown.runs_removal(command) is True


@pytest.mark.parametrize("command", NOT_REMOVALS,
                         ids=[f"no{i}" for i in range(len(NOT_REMOVALS))])
def test_no_removal_is_seen(command):
    assert rm_countdown.runs_removal(command) is False


def test_an_over_long_command_is_not_read():
    command = "rm x; " + "x" * rm_countdown.MAX_COMMAND_CHARS
    assert rm_countdown.runs_removal(command) is False


def test_a_command_at_the_limit_is_read():
    command = "rm x " + "y" * (rm_countdown.MAX_COMMAND_CHARS - 5)
    assert len(command) == rm_countdown.MAX_COMMAND_CHARS
    assert rm_countdown.runs_removal(command) is True


@pytest.mark.parametrize("command", [None, 5, b"rm x", ["rm", "x"], {"command": "rm x"}],
                         ids=["none", "int", "bytes", "list", "dict"])
def test_a_non_str_is_no_removal(command):
    assert rm_countdown.runs_removal(command) is False


class _Raising(str):
    def replace(self, *a, **k):  # noqa: ARG002 - shape of str.replace
        raise RuntimeError("boom")


def test_a_command_that_raises_is_no_removal():
    assert rm_countdown.runs_removal(_Raising("rm x")) is False


@pytest.mark.parametrize("command", ["'", '"', "\\", "$(", "`", "rm '", 'sh -c "'],
                         ids=["sq", "dq", "bs", "subst", "bt", "rm-open-quote", "c-open"])
def test_torn_quoting_never_raises(command):
    assert rm_countdown.runs_removal(command) in (True, False)
