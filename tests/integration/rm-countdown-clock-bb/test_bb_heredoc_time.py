"""The removal detector's iteration-2 rules, from the outside (design.md
decision 1 "any doubt means False", criteria 8 and 9; review-1
rev-iter1-003 and its nit):

- A command with an unquoted heredoc (``<<WORD``, ``<<'WORD'``,
  ``<<"WORD"``, ``<<-WORD``) gets no clock: its body is data, and whether
  Claude Code's check reads it is not established, so it is doubt. A
  here-string's quoted text stays one opaque word.
- ``<<`` inside quotes is plain text, so the rest of the command is read
  as usual.
- ``time`` skips its options as a wrapper does: ``time -p rm x`` gets one.

Then through real PermissionRequest datagrams: a heredoc prompt shows no
clock line, has no refusal-window gate on its taps, is never closed as
refused, and is still a countdown candidate (the guard covers it)."""

from __future__ import annotations

import pytest

from aipager.rm_countdown import runs_removal
from tests.rm_clock_bb import CLOCK_HEAD, REFUSE_AT
from tests.two_prompts_rig import answers

HEREDOCS = [
    "cat <<EOF > f.sh\nrm -rf x\nEOF",
    "cat > f <<'EOF'\nrm x\nEOF",
    'cat > f <<"EOF"\nrm x\nEOF',
    "cat <<-EOF > f.sh\n\trm -rf x\n\tEOF",
    "cat <<EOF>f.sh\nrmdir d\nEOF",
    "tee f.sh <<EOF\nsudo rm -rf /var/x\nEOF",
    "python3 - <<'PY'\nimport os\nos.system('rm x')\nPY",
    "git commit -F - <<EOF\nrm old files\nEOF",
    "cat <<EOF | sh\nrm x\nEOF",
    "bash <<EOF\nrm x\nEOF",
    "rm x <<EOF\nyes\nEOF",
    "echo start && cat <<EOF > f\nrm x\nEOF",
    "sh -c 'cat <<EOF\nrm x\nEOF'",
]

HERE_STRINGS = [
    'cat <<< "rm x"',
    "cat <<<'rm -rf x'",
    'grep -c rm <<< "rm x"',
]

QUOTED_SHIFT_PLUS_RM = [
    'echo "a << b"; rm x',
    "echo 'x<<y' && rm -rf build",
    'printf "%s\\n" "<<EOF" ; rmdir d',
    "echo '<<<' | xargs rm",
]

QUOTED_SHIFT_NO_RM = [
    'echo "a << b"',
    'git commit -m "fix << op"',
    "echo '<<EOF rm x'",
]

TIME_REMOVES = ["time -p rm x", "time -p rmdir d", "time -p rm -rf build/",
                "time rm x", "time -p sudo rm x", "time -p -- rm x"]
TIME_KEEPS = ["time -p ls", "time -p make", "time -p rmx", "time -p"]


@pytest.mark.parametrize("command", HEREDOCS)
def test_a_heredoc_command_is_not_a_removal(command):
    assert runs_removal(command) is False


@pytest.mark.parametrize("command", HERE_STRINGS)
def test_a_here_string_text_is_not_a_removal(command):
    assert runs_removal(command) is False


@pytest.mark.parametrize("command", QUOTED_SHIFT_PLUS_RM)
def test_quoted_shift_does_not_hide_a_real_rm(command):
    assert runs_removal(command) is True


@pytest.mark.parametrize("command", QUOTED_SHIFT_NO_RM)
def test_quoted_shift_with_no_rm_is_not_a_removal(command):
    assert runs_removal(command) is False


@pytest.mark.parametrize("command", TIME_REMOVES)
def test_time_with_options_then_rm_is_a_removal(command):
    assert runs_removal(command) is True


@pytest.mark.parametrize("command", TIME_KEEPS)
def test_time_with_options_then_no_rm_is_not_a_removal(command):
    assert runs_removal(command) is False


def test_a_long_heredoc_never_raises():
    command = "cat <<EOF > f\n" + "rm x\n" * 3000 + "EOF"
    assert runs_removal(command) is False


@pytest.mark.parametrize("command", ["cat <<", "cat <<'", 'cat <<"EOF', "<<EOF", "<<<", "<<-"])
def test_torn_heredoc_never_raises(command):
    assert runs_removal(command) is False


# -- through the hook ------------------------------------------------------------------------

HEREDOC_RM = "cat <<EOF > f.sh\nrm -rf x\nEOF"


@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
def test_a_heredoc_prompt_shows_no_clock(env, mode):
    env.rm(HEREDOC_RM, mode=mode)
    assert CLOCK_HEAD not in env.rig.prompt_messages()[0]["text"]


def test_a_heredoc_prompt_shows_no_clock_inline(cenv):
    cenv.rm(HEREDOC_RM)
    assert CLOCK_HEAD not in cenv.last_card_text()


@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
def test_time_p_rm_prompt_shows_the_clock(env, mode):
    env.rm("time -p rm -rf build/", mode=mode)
    assert CLOCK_HEAD in env.rig.prompt_messages()[0]["text"]


@pytest.mark.parametrize("hook", [True, False], ids=["hook-parked", "typed"])
def test_a_heredoc_prompt_tap_long_after_two_minutes_is_answered(env, hook):
    """No clock, so no refusal-window gate: a tap far past arrival + 2 min
    is answered as today (hook decision or keys), not refused."""
    env.rm(HEREDOC_RM, hook=hook)
    env.wall.t = REFUSE_AT + 600.0
    env.rig.tap(env.separate_ids()[0], "allow")
    assert (env.rig.decisions != []) or (env.rig.keys != [])


def test_a_heredoc_prompt_tap_long_after_two_minutes_gets_no_refusal_toast(env):
    env.rm(HEREDOC_RM)
    env.wall.t = REFUSE_AT + 600.0
    q = env.rig.tap(env.separate_ids()[0], "allow")
    assert not any("Claude Code refuses this by itself" in a for a in answers(q))


def test_a_heredoc_prompt_is_never_closed_as_refused(env):
    env.rm(HEREDOC_RM)
    env.call(HEREDOC_RM)
    env.result(is_error=True)
    env.scan(REFUSE_AT + 1.0)
    env.hook("Stop")
    assert env.refused_audit() == []


def test_a_heredoc_prompt_beside_another_is_still_guarded(cenv):
    cenv.rig.question()
    cenv.rm(HEREDOC_RM)
    sess = cenv.rig.sess
    assert {sess.keys_verdict(p, 0.0) for p in sess.open_prompts()} == {"unknown"}
