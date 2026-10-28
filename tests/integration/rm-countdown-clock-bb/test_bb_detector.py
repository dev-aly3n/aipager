"""``rm_countdown.runs_removal`` from the outside (design.md success
criteria 8 and 9, decision 1 "Removal detector"): equivalence classes of
commands that run rm/rmdir (wrappers, paths, case, separators, find -exec,
shell -c scripts, eval) against look-alikes that do not, the 16 KiB and
script-depth boundaries, and inputs that must never raise."""

from __future__ import annotations

import pytest

from aipager.rm_countdown import runs_removal

# design.md criterion 8's table, verbatim, plus one per rule of decision 1.
REMOVES = [
    "rm -rf x", "/bin/rm x", "\\rm x", "RM x", "rmdir d", "sudo rm x",
    "env A=1 rm x", "timeout 5 rm x", "nice -n 10 rm x", "xargs rm",
    "ls | xargs -0 rm -f", "find . -name '*.o' -exec rm {} +",
    "sh -c 'rm -rf \"$D\"'", 'bash -lc "cd /tmp && rm -r x"', "eval 'rm x'",
    "echo a; rm x", "(rm x)", "x=$(rm y)",
    # one more per class
    "rm", "RMDIR d", "/usr/bin/rmdir d", "./rm x",
    "A=1 B=2 rm x", ">/dev/null rm x", "2>&1 rm x",
    "echo a && rm x", "echo a || rm x", "echo a\nrm x", "echo a & rm x",
    "{ rm x; }", "`rm x`", "if true; then rm x; fi",
    "while read f; do rm \"$f\"; done", "! rm x",
    "doas rm x", "exec rm x", "command rm x", "nohup rm x", "busybox rm x",
    "time rm x", "stdbuf -oL rm x", "setsid rm x", "ionice -c3 rm x",
    "pkexec rm x", "runuser -u bob rm x", "sudo -u bob rm x", "env -i rm x",
    "sudo env A=1 timeout 5 rm x",
    "find . -exec /bin/rm {} \\;", "find . -execdir rm {} \\;",
    "find . -ok rm {} \\;", "find . -okdir rmdir {} \\;",
    "bash -ec 'rm x'", "dash -c 'rm x'", "zsh -c 'rm x'", "ksh -c 'rm x'",
    "busybox sh -c 'rm x'", "bash -c \"sh -c 'rm x'\"",
    "rm \\\n -rf x",
]

KEEPS = [
    # design.md criterion 9's table, verbatim
    "ls", "echo rm", "grep -r rm .", 'git commit -m "rm old"', "npm run rm-cache",
    "rmx", "perm", "cat rm.txt",
    # one more per class
    "", "   ", "git rm x", "echo 'a; rm x'", 'echo "a && rm x"', "find . -delete",
    "find . -name rm", "python -c 'rm x'", "sh 'rm x'", "bash -x 'rm x'",
    "sh -c 'echo rm'", "rmdirx d", "/bin/rmx", "echo rm -rf /",
    "printf '%s' rm", "mv rm x",
]


@pytest.mark.parametrize("command", REMOVES)
def test_a_removal_is_detected(command):
    assert runs_removal(command) is True


@pytest.mark.parametrize("command", KEEPS)
def test_a_look_alike_is_not_a_removal(command):
    assert runs_removal(command) is False


# -- boundaries ---------------------------------------------------------------------

LIMIT = 16 * 1024


def test_a_removal_of_exactly_16_kib_is_detected():
    command = "rm x " + "a" * (LIMIT - 5)
    assert len(command) == LIMIT and runs_removal(command) is True


def test_a_removal_over_16_kib_is_not():
    command = "rm x " + "a" * (LIMIT - 4)
    assert len(command) == LIMIT + 1 and runs_removal(command) is False


def test_a_script_two_levels_deep_is_read():
    assert runs_removal("bash -c \"sh -c 'rm x'\"") is True


def test_a_script_three_levels_deep_is_not_followed():
    """Decision 1: scripts are re-read at most 2 levels deep, and any doubt
    means False."""
    assert runs_removal("sh -c \"sh -c 'sh -c rm'\"") is False


# -- error guessing: never raises, False on doubt -------------------------------------

class Boom(str):
    def __getattribute__(self, name):
        raise RuntimeError("boom")


@pytest.mark.parametrize("value", [None, 0, 1.5, b"rm x", ["rm", "x"], {"command": "rm x"},
                                   object()], ids=lambda v: type(v).__name__)
def test_a_non_str_is_false(value):
    assert runs_removal(value) is False


def test_a_str_that_raises_on_access_never_raises():
    try:
        out = runs_removal(Boom("rm x"))
    except Exception as exc:  # noqa: BLE001 - the contract is "never raises"
        pytest.fail(f"raised {exc!r}")
    assert out in (True, False)


@pytest.mark.parametrize("command", ["rm 'x", 'rm "x', "sh -c 'rm", "$(", "`", "\\",
                                     "((((((", "rm x\x00y", "\u202Erm x"],
                         ids=["open-single", "open-double", "open-script", "open-subst",
                              "open-backtick", "trailing-backslash", "parens", "nul",
                              "bidi"])
def test_torn_quoting_never_raises(command):
    try:
        runs_removal(command)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"raised {exc!r}")
