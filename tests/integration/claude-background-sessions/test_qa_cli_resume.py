"""QA: ``aipager session <name> --resume <id>`` (R3, E1).

entrypoints.md: the CLI follows ``continued-in`` (prints a line, resumes
the newest id), stops an idle holder (says so), refuses a busy holder with
exit 1 and the exact ``claude stop``/``claude attach`` commands, and exits
1 on a failed stop; the launch records claude's exit through a shell that
keeps the user's arguments positional.

Partitions beyond the developer's: the hop count (2, 3, 4), a cycle, a
holder that is interactive or dead, an unreadable registry, ``--resume``
with no id, and a user argument a shell would re-parse.

Claude's files are under tmp_path; ``claude stop`` runs through
``claude_jobs._subprocess_run`` and the dtach spawn through the launcher's
spawn seam (the developer's ``Cli`` fake, which refuses the spawn).
"""

from __future__ import annotations

import pytest

from aipager.dtach import launcher
from tests.test_cli_session_resume import HOLDER, JOB, X, Y, Cli

A = "a1a1a1a1-0000-4000-8000-00000000000a"
B = "b2b2b2b2-0000-4000-8000-00000000000b"
C = "c3c3c3c3-0000-4000-8000-00000000000c"


@pytest.fixture
def cli(tmp_path, monkeypatch):
    return Cli(tmp_path, monkeypatch)


def _resumed(cli) -> list[str]:
    out = []
    for argv in cli.spawns:
        for i, a in enumerate(argv):
            if a in ("--resume", "-r") and i + 1 < len(argv):
                out.append(argv[i + 1])
            elif a.startswith("--resume="):
                out.append(a.split("=", 1)[1])
    return out


def _flat(text):
    return " ".join(text.split())


def _stops(cli):
    return [c for c in cli.claude_calls if c[:1] == ["stop"]]


# ---- hops ----------------------------------------------------------------------------------

def test_two_hops_are_followed(cli):
    cli.moved(X, A)
    cli.moved(A, B)
    cli.transcript(B, {"type": "user"})
    launcher.launch("dev", ["--resume", X])
    assert _resumed(cli) == [B]


def test_three_hops_are_followed(cli):
    cli.moved(X, A)
    cli.moved(A, B)
    cli.moved(B, C)
    cli.transcript(C, {"type": "user"})
    launcher.launch("dev", ["--resume", X])
    assert _resumed(cli) == [C]


def test_a_fourth_hop_is_not_followed(cli):
    cli.moved(X, A)
    cli.moved(A, B)
    cli.moved(B, C)
    cli.moved(C, Y)
    cli.transcript(Y, {"type": "user"})
    launcher.launch("dev", ["--resume", X])
    assert _resumed(cli) == [C]


def test_a_cycle_ends(cli):
    cli.moved(X, A)
    cli.moved(A, X)
    launcher.launch("dev", ["-r", X])
    assert len(_resumed(cli)) == 1 and _resumed(cli)[0] in (X, A)


def test_a_followed_resume_says_where_it_went(cli, capsys):
    cli.moved(X, A)
    cli.transcript(A, {"type": "user"})
    launcher.launch("dev", ["-r", X])
    assert (f"The conversation moved to {A}, so this session continues there."
            in _flat(capsys.readouterr().out))


# ---- holders -----------------------------------------------------------------------------------

def test_a_busy_holder_with_the_short_flag_exits_1(cli):
    cli.transcript(Y, {"type": "user"})
    cli.holder("busy")
    assert launcher.launch("dev", ["-r", Y]) == 1


def test_a_busy_holder_starts_nothing(cli):
    cli.transcript(Y, {"type": "user"})
    cli.holder("busy")
    launcher.launch("dev", ["-r", Y])
    assert (cli.spawns, _stops(cli)) == ([], [])


def test_a_busy_holder_prints_both_commands(cli, capsys):
    cli.transcript(Y, {"type": "user"})
    cli.holder("busy")
    launcher.launch("dev", ["--resume", Y])
    cap = capsys.readouterr()
    text = _flat(cap.out + " " + cap.err)
    assert (f"claude stop {JOB}" in text, f"claude attach {JOB}" in text) == (True, True)


def test_a_move_into_a_busily_held_conversation_is_refused(cli):
    """R1 then R2: X moved to Y, which a busy job holds."""
    cli.moved(X, Y)
    cli.transcript(Y, {"type": "user"})
    cli.holder("busy")
    assert (launcher.launch("dev", ["--resume", X]), cli.spawns) == (1, [])


def test_an_interactive_holder_is_ignored(cli):
    cli.transcript(Y, {"type": "user"})
    cli.reg(HOLDER, kind="interactive", sessionId=Y, status="busy")
    cli.alive.add(HOLDER)
    launcher.launch("dev", ["--resume", Y])
    assert (_stops(cli), _resumed(cli)) == ([], [Y])


def test_a_dead_holder_is_ignored(cli):
    cli.transcript(Y, {"type": "user"})
    cli.reg(HOLDER, kind="bg", jobId=JOB, sessionId=Y, status="busy")
    launcher.launch("dev", ["--resume", Y])
    assert (_stops(cli), _resumed(cli)) == ([], [Y])


def test_a_failed_stop_exits_1_and_starts_nothing(cli, capsys):
    cli.transcript(Y, {"type": "user"})
    cli.holder("idle")
    cli.stop_rc = 2
    rc = launcher.launch("dev", ["--resume", Y])
    cap = capsys.readouterr()
    assert (rc, cli.spawns, f"Could not stop Claude Code's background job {JOB}" in
            _flat(cap.out + " " + cap.err)) == (1, [], True)


def test_an_unreadable_registry_launches_as_today(cli):
    cli.transcript(Y, {"type": "user"})
    cli.holder("busy")
    (cli.cfg / "sessions").chmod(0)
    try:
        launcher.launch("dev", ["--resume", Y])
    finally:
        (cli.cfg / "sessions").chmod(0o755)
    assert (_stops(cli), _resumed(cli)) == ([], [Y])


# ---- error guessing: odd arguments ------------------------------------------------------------

def test_a_bare_resume_flag_runs_no_claude_command(cli):
    """``--resume`` with no id opens Claude's own picker: nothing to follow."""
    launcher.launch("dev", ["--resume"])
    assert cli.claude_calls == []


def test_a_shell_hostile_argument_stays_one_argument(cli):
    hostile = "a; touch /nonexistent/qa $(id) `id` \"q\" 'q'"
    launcher.launch("dev", ["--append-system-prompt", hostile])
    assert any(hostile in argv for argv in cli.spawns)


def test_the_spawn_records_the_exit_through_a_shell_wrapper(cli):
    """E1: claude is not the last command the dtach runs, so its exit is
    recorded (``sh -c SCRIPT sh <exit file> ...``)."""
    launcher.launch("dev", ["--model", "opus"])
    argv = cli.spawns[0]
    i = argv.index("-c")
    assert (argv[i - 1].endswith("sh"), argv[i + 2], argv[i + 3].endswith(".json")) == (
        True, "sh", True)
