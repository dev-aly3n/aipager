"""Validation for the one route in aipager that can spawn a process.

`POST /api/sessions` is reachable over a public tunnel and ends in
`launch_session(cwd=...)`. These are the rules that decide *what may be
executed and where*, so they get their own adversarial tests rather than
being exercised only through a handler.
"""

import os

import pytest

from aipager.miniapp.launch import (
    MAX_NAME_LENGTH,
    validate_cwd,
    validate_session_name,
)


# ===== session names ======================================================

@pytest.mark.parametrize("name", ["dev", "api-work", "test_2", "a", "A1"])
def test_reasonable_names_accepted(name):
    clean, err = validate_session_name(name)
    assert err == ""
    assert clean == name


def test_surrounding_whitespace_is_trimmed_not_rejected():
    assert validate_session_name("  dev  ") == ("dev", "")


@pytest.mark.parametrize("name", [
    "",                      # empty
    "   ",                   # whitespace only
    "-leading-hyphen",       # must start alnum
    "_leading_underscore",
    "has space",
    "has/slash",             # would escape the socket filename
    "has..dots",
    "../../etc/passwd",      # traversal
    "dev\x00evil",           # NUL
    "dev;rm -rf /",          # shell metacharacters
    "dev$(whoami)",
    "dev`id`",
    "dev\nnewline",
    "café",                  # non-ascii
    "🚀",
])
def test_hostile_names_rejected(name):
    clean, err = validate_session_name(name)
    assert clean == ""
    assert err, f"{name!r} was accepted with no error"


def test_overlong_name_rejected():
    clean, err = validate_session_name("a" * (MAX_NAME_LENGTH + 1))
    assert clean == ""
    assert "characters" in err


def test_name_at_the_limit_is_accepted():
    """Boundary: the cap itself must be allowed, not off by one."""
    clean, err = validate_session_name("a" * MAX_NAME_LENGTH)
    assert err == ""
    assert len(clean) == MAX_NAME_LENGTH


@pytest.mark.parametrize("name", ["status", "stop", "kill", "new", "help", "settings"])
def test_reserved_command_names_rejected(name):
    """These collide with bot commands — a session called `stop` would
    shadow /stop."""
    assert validate_session_name(name)[0] == ""
    assert validate_session_name(name.upper())[0] == ""


@pytest.mark.parametrize("value", [None, 123, [], {}, True])
def test_non_string_name_rejected(value):
    assert validate_session_name(value)[0] == ""


# ===== working directory ==================================================

@pytest.fixture
def roots(tmp_path):
    project = tmp_path / "project"
    (project / "sub").mkdir(parents=True)
    other = tmp_path / "elsewhere"
    other.mkdir()
    (tmp_path / "project-evil").mkdir()      # sibling sharing a prefix
    (tmp_path / "afile.txt").write_text("x")
    return {
        "root": str(project),
        "sub": str(project / "sub"),
        "outside": str(other),
        "prefix_twin": str(tmp_path / "project-evil"),
        "file": str(tmp_path / "afile.txt"),
        "tmp": tmp_path,
    }


def test_empty_cwd_means_daemon_default(roots):
    """Same behaviour as chat's /new, which passes cwd=None."""
    assert validate_cwd("", [roots["root"]]) == ("", "")
    assert validate_cwd(None, [roots["root"]]) == ("", "")


def test_root_itself_is_accepted(roots):
    real, err = validate_cwd(roots["root"], [roots["root"]])
    assert err == ""
    assert real == os.path.realpath(roots["root"])


def test_subdirectory_of_a_root_is_accepted(roots):
    real, err = validate_cwd(roots["sub"], [roots["root"]])
    assert err == ""
    assert real == os.path.realpath(roots["sub"])


def test_directory_outside_every_root_is_rejected(roots):
    assert validate_cwd(roots["outside"], [roots["root"]])[0] == ""


def test_sibling_sharing_a_string_prefix_is_rejected(roots):
    """`/x/project-evil` must NOT pass for the root `/x/project`.

    This is the case a `startswith` check gets wrong, which is why the
    comparison is done on path components.
    """
    real, err = validate_cwd(roots["prefix_twin"], [roots["root"]])
    assert real == "", "prefix-sharing sibling was accepted"
    assert err


def test_traversal_out_of_a_root_is_rejected(roots):
    escape = os.path.join(roots["root"], "..", "elsewhere")
    assert validate_cwd(escape, [roots["root"]])[0] == ""


def test_traversal_that_stays_inside_is_accepted(roots):
    """`root/sub/..` resolves back to root, which is legitimately allowed —
    the check is on the resolved path, not on the presence of `..`."""
    inside = os.path.join(roots["sub"], "..")
    real, err = validate_cwd(inside, [roots["root"]])
    assert err == ""
    assert real == os.path.realpath(roots["root"])


def test_symlink_escaping_a_root_is_rejected(roots):
    """realpath must be applied BEFORE the check, or a symlink inside an
    allowed root becomes a way out of it."""
    link = os.path.join(roots["root"], "escape")
    os.symlink(roots["outside"], link)
    real, err = validate_cwd(link, [roots["root"]])
    assert real == "", "symlink escaping the root was accepted"
    assert err


def test_symlink_staying_inside_is_accepted(roots):
    link = os.path.join(roots["root"], "inward")
    os.symlink(roots["sub"], link)
    real, err = validate_cwd(link, [roots["root"]])
    assert err == ""
    assert real == os.path.realpath(roots["sub"])


def test_a_file_is_not_a_directory(roots):
    assert validate_cwd(roots["file"], [str(roots["tmp"])])[0] == ""


def test_nonexistent_path_rejected(roots):
    assert validate_cwd(os.path.join(roots["root"], "nope"), [roots["root"]])[0] == ""


def test_nul_byte_rejected(roots):
    assert validate_cwd(roots["root"] + "\x00/etc", [roots["root"]])[0] == ""


@pytest.mark.parametrize("value", [123, [], {}, True])
def test_non_string_cwd_rejected(value, roots):
    assert validate_cwd(value, [roots["root"]])[0] == ""


def test_no_roots_configured_rejects_any_path(roots):
    """Fail closed: with nothing sanctioned yet, no path is acceptable."""
    real, err = validate_cwd(roots["root"], [])
    assert real == ""
    assert err


def test_no_roots_still_allows_the_daemon_default(roots):
    """…but the no-choice default must keep working, or a fresh install
    could never create a session from the Mini App at all."""
    assert validate_cwd("", []) == ("", "")


def test_multiple_roots_each_accepted(roots):
    both = [roots["root"], roots["outside"]]
    assert validate_cwd(roots["root"], both)[1] == ""
    assert validate_cwd(roots["outside"], both)[1] == ""


# ===== the model reaches the launch command, not the session ==============

def test_launch_session_puts_the_model_on_the_command_line(monkeypatch, run_async):
    """`--model` at launch is the whole point: typing `/model x` into the
    session instead produced a spurious extra turn on first IDLE."""
    from aipager.dtach import inject

    captured = {}

    async def fake_exec(*args, **kwargs):
        captured["argv"] = args
        raise RuntimeError("stop before spawning anything")

    monkeypatch.setattr(inject.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(inject, "_socket_exists", lambda name: False, raising=False)

    with pytest.raises(RuntimeError):
        run_async(inject.launch_session("dev", model="opus"))
    argv = " ".join(str(a) for a in captured.get("argv", ()))
    assert "--model opus" in argv, argv


def test_launch_session_omits_the_flag_when_no_model_is_given(monkeypatch, run_async):
    from aipager.dtach import inject

    captured = {}

    async def fake_exec(*args, **kwargs):
        captured["argv"] = args
        raise RuntimeError("stop before spawning anything")

    monkeypatch.setattr(inject.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(inject, "_socket_exists", lambda name: False, raising=False)

    with pytest.raises(RuntimeError):
        run_async(inject.launch_session("dev"))
    argv = " ".join(str(a) for a in captured.get("argv", ()))
    assert "--model" not in argv, argv


def test_a_model_with_shell_metacharacters_is_quoted(monkeypatch, run_async):
    """The alias is server-validated against MODEL_CHOICES before it gets
    here, but it lands inside a `bash -c` string — quote it anyway rather
    than relying on a caller two layers up."""
    from aipager.dtach import inject

    captured = {}

    async def fake_exec(*args, **kwargs):
        captured["argv"] = args
        raise RuntimeError("stop before spawning anything")

    monkeypatch.setattr(inject.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(inject, "_socket_exists", lambda name: False, raising=False)

    with pytest.raises(RuntimeError):
        run_async(inject.launch_session("dev", model="opus; touch /tmp/pwned"))
    argv = " ".join(str(a) for a in captured.get("argv", ()))
    # shlex.quote wraps the whole value, so the `;` is inert. Asserting the
    # QUOTED form is the check; an earlier version stripped the quotes and
    # then looked for the bare text, which can only ever fail.
    assert "--model 'opus; touch /tmp/pwned'" in argv, argv
