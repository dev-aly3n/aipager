"""Validation for the one route in aipager that can spawn a process.

`POST /api/sessions` is reachable over a public tunnel and ends in
`launch_session(cwd=...)`. These are the rules that decide *what may be
executed and where*, so they get their own adversarial tests rather than
being exercised only through a handler.
"""

import os
from pathlib import Path

import pytest

from aipager.miniapp.launch import (
    MAX_DIR_NAME_LENGTH,
    MAX_MODEL_LENGTH,
    MAX_NAME_LENGTH,
    allowed_roots,
    create_directory,
    validate_cwd,
    validate_model,
    validate_new_dir_name,
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


# ===== which directories are allowed at all ===============================

class _FakeSession:
    def __init__(self, cwd):
        self.cwd = cwd


class _FakeRegistry:
    def __init__(self, cwds):
        self._sessions = {str(i): _FakeSession(c) for i, c in enumerate(cwds)}

    def all_sessions(self, scope_chat_id):
        return self._sessions


def test_the_daemons_own_directory_is_an_allowed_root(monkeypatch, tmp_path):
    """It is already where `launch_session(cwd=None)` puts every session
    that ignores the picker, so listing it grants no new reach — and
    without it a fresh install has no roots at all and the picker, plus
    the folder button that needs a parent, are dead UI."""
    daemon = tmp_path / "daemon-dir"
    daemon.mkdir()
    monkeypatch.setattr("aipager.dtach.inject._PROJECT_DIR", str(daemon))

    assert allowed_roots(_FakeRegistry([]), 1) == [os.path.realpath(daemon)]


def test_session_directories_come_before_the_daemons_own(monkeypatch, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    daemon = tmp_path / "daemon-dir"
    daemon.mkdir()
    monkeypatch.setattr("aipager.dtach.inject._PROJECT_DIR", str(daemon))

    assert allowed_roots(_FakeRegistry([str(proj)]), 1) == [
        os.path.realpath(proj), os.path.realpath(daemon),
    ]


def test_the_daemon_directory_is_not_listed_twice(monkeypatch, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setattr("aipager.dtach.inject._PROJECT_DIR", str(proj))

    assert allowed_roots(_FakeRegistry([str(proj)]), 1) == [os.path.realpath(proj)]


def test_a_daemon_started_at_the_filesystem_root_grants_nothing(monkeypatch):
    """Allow-listing `/` would BE the free-text path box this list exists
    to refuse — every path on the machine would pass containment."""
    monkeypatch.setattr("aipager.dtach.inject._PROJECT_DIR", "/")

    assert allowed_roots(_FakeRegistry([]), 1) == []


def test_a_daemon_directory_that_no_longer_exists_is_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "aipager.dtach.inject._PROJECT_DIR", str(tmp_path / "deleted"),
    )
    assert allowed_roots(_FakeRegistry([]), 1) == []


# ===== creating a folder ==================================================

@pytest.mark.parametrize("name", ["proj", "my-app", "app_2", "a.b", "A1"])
def test_reasonable_folder_names_accepted(name):
    clean, err = validate_new_dir_name(name)
    assert err == ""
    assert clean == name


@pytest.mark.parametrize("name", [
    "",
    "   ",
    ".",                     # the parent itself
    "..",                    # the grandparent
    "../evil",               # traversal
    "a/b",                   # not one segment
    "a\\b",                  # nor on the other separator
    "-rf",                   # leading dash
    ".hidden",               # leading dot — must start alnum
    "has space",
    "evil\x00",
    "sub;rm -rf /",
    "$(whoami)",
    "café",
    123, None, [], {}, False,
])
def test_hostile_folder_names_rejected(name):
    clean, err = validate_new_dir_name(name)
    assert clean == "", f"{name!r} was accepted"
    assert err


def test_folder_name_at_the_cap_is_accepted_one_over_is_not():
    assert validate_new_dir_name("a" * MAX_DIR_NAME_LENGTH)[1] == ""
    assert validate_new_dir_name("a" * (MAX_DIR_NAME_LENGTH + 1))[0] == ""


def test_creating_a_folder_inside_a_root_works(roots):
    path, existed, err = create_directory(roots["root"], "fresh", [roots["root"]])
    assert err == ""
    assert existed is False
    assert path == os.path.join(os.path.realpath(roots["root"]), "fresh")
    assert os.path.isdir(path)


def test_creating_a_folder_inside_a_subdirectory_works(roots):
    path, _existed, err = create_directory(roots["sub"], "deeper", [roots["root"]])
    assert err == ""
    assert os.path.isdir(path)


def test_an_existing_folder_is_reused_and_reported_as_such(roots):
    first, existed, err = create_directory(roots["root"], "twice", [roots["root"]])
    assert (existed, err) == (False, "")
    again, existed, err = create_directory(roots["root"], "twice", [roots["root"]])
    assert err == ""
    assert existed is True, "an existing folder must be reported, not silently new"
    assert again == first


def test_a_file_of_that_name_is_an_error_not_a_reuse(roots):
    (Path(roots["root"]) / "afile").write_text("x")
    path, _existed, err = create_directory(roots["root"], "afile", [roots["root"]])
    assert path == ""
    assert err


def test_a_folder_cannot_be_created_outside_the_allowed_roots(roots):
    path, _existed, err = create_directory(roots["outside"], "evil", [roots["root"]])
    assert path == ""
    assert err
    assert not os.path.exists(os.path.join(roots["outside"], "evil"))


def test_a_folder_name_cannot_walk_out_of_the_parent(roots):
    path, _existed, err = create_directory(roots["root"], "../escaped", [roots["root"]])
    assert path == ""
    assert err
    assert not os.path.exists(os.path.join(roots["tmp"], "escaped"))


def test_an_existing_symlink_leaf_pointing_outside_is_refused(roots):
    """The leaf already exists, so `mkdir` raises FileExistsError and the
    reuse path takes over — which is exactly when re-resolving the
    created path matters. Without that second `validate_cwd`, the route
    would hand back a path outside the allow-list and call it created."""
    os.symlink(roots["outside"], os.path.join(roots["root"], "sneaky"))

    path, _existed, err = create_directory(roots["root"], "sneaky", [roots["root"]])
    assert path == "", "a symlink out of the root was accepted as the new folder"
    assert err


def test_no_parent_means_no_folder(roots):
    """`validate_cwd("")` means "the daemon's default" — a launch
    behaviour, not a directory to create inside."""
    path, _existed, err = create_directory("", "orphan", [roots["root"]])
    assert path == ""
    assert err


def test_a_folder_cannot_be_created_when_no_root_is_configured(roots):
    path, _existed, err = create_directory(roots["root"], "nope", [])
    assert path == ""
    assert err
    assert not os.path.exists(os.path.join(roots["root"], "nope"))


# ===== which model may reach the command line =============================

_CHOICES = [("Sonnet", "/model sonnet"), ("Opus", "/model opus")]


@pytest.mark.parametrize("value,expected", [
    (None, ""),
    ("", ""),
    ("   ", ""),
    ("Opus", "opus"),            # a listed label resolves to its alias
    ("opus", "opus"),            # ...case-insensitively
    ("claude-opus-5", "claude-opus-5"),
    ("claude-fable-5", "claude-fable-5"),
    ("us.anthropic.claude-sonnet-4-5-v1:0", "us.anthropic.claude-sonnet-4-5-v1:0"),
    ("  claude-opus-5  ", "claude-opus-5"),
])
def test_accepted_models(value, expected):
    resolved, err = validate_model(value, _CHOICES)
    assert err == ""
    assert resolved == expected


@pytest.mark.parametrize("value", [
    "opus; rm -rf /",
    "opus$(whoami)",
    "opus`id`",
    "opus sonnet",
    "opus\nsonnet",
    "opus\x00evil",
    "../../etc/passwd",
    "/etc/passwd",
    "-rf",
    "--dangerously-skip-permissions",
    "-",
    "a" * (MAX_MODEL_LENGTH + 1),
    123, [], {}, True,
])
def test_rejected_models(value):
    resolved, err = validate_model(value, _CHOICES)
    assert resolved == "", f"{value!r} was accepted"
    assert err


def test_a_leading_dash_is_rejected_because_it_would_be_read_as_a_flag():
    """`--model --dangerously-skip-permissions` is the case that matters:
    claude's own parser would take the second token as a FLAG, not as
    this flag's value, turning a model choice into a way past the admin
    gate on Auto mode. shlex.quote cannot help — the value is already
    shell-safe."""
    assert validate_model("--dangerously-skip-permissions", _CHOICES)[0] == ""
    assert validate_model("-p", _CHOICES)[0] == ""


def test_a_model_at_the_length_cap_is_accepted():
    at_cap = "a" * MAX_MODEL_LENGTH
    assert validate_model(at_cap, _CHOICES) == (at_cap, "")


def test_a_listed_label_resolves_through_its_command_not_its_label():
    """`keyboard.json` may map a label to an unrelated command; the
    shipped list hides the difference because every default label
    lowercases into its own alias."""
    choices = [("Claude 4.5 Opus", "/model claude-opus-4-5")]
    assert validate_model("Claude 4.5 Opus", choices) == ("claude-opus-4-5", "")


def test_a_label_resolving_to_something_hostile_is_still_rejected():
    """Local config is not a threat, but a mapping that cannot be a model
    name must fail loudly rather than reach the command line."""
    choices = [("Weird", "/model -rf")]
    assert validate_model("Weird", choices)[0] == ""


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


@pytest.mark.parametrize("model", [
    "-rf", "--dangerously-skip-permissions", "-p", "--print",
])
def test_launch_session_refuses_a_model_that_would_read_as_a_flag(
    monkeypatch, run_async, model,
):
    """Quoting is the wrong tool for this one: `shlex.quote("-p")` is
    `-p`, and claude's own parser then takes it as a FLAG rather than as
    `--model`'s value. `--dangerously-skip-permissions` arriving that way
    would be a way past the admin gate on Auto mode.

    The Mini App validates far more strictly before this point; this is
    the layer that holds if a future caller forgets to, so it fails
    closed — no process at all, rather than one launched without the
    flag.
    """
    from aipager.dtach import inject

    spawned = []

    async def fake_exec(*args, **kwargs):
        spawned.append(args)
        raise RuntimeError("should never be reached")

    monkeypatch.setattr(inject.asyncio, "create_subprocess_exec", fake_exec)

    ok, err = run_async(inject.launch_session("dev", model=model))
    assert ok is False
    assert err
    assert not spawned, "a flag-shaped model reached the command line"
