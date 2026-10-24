"""Claude Code background workers: the job read from the environment, the
owner-checked link file and the registry fallback (aipager.dtach.bg_jobs),
the protected runtime globs, and the suite's redirect of the new paths.

Every file lives under tmp_path; Claude's registry is a fake config folder
there. No socket, no real claude, no real home.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from aipager import instance, safety
from aipager.dtach import bg_jobs

JOB = "ab12cd34"
LINKED = "claude-frontend"
INHERITED = "claude-backend"


# ---- T1.1: the job from CLAUDE_JOB_DIR -------------------------------------------------

@pytest.mark.parametrize("env", [{}, {"CLAUDE_JOB_DIR": ""}, {"CLAUDE_JOB_DIR": "   "}],
                         ids=["unset", "empty", "blank"])
def test_no_job_dir_is_no_job(env):
    assert bg_jobs.job_from_env(env) is None


def test_a_job_dir_names_the_job_and_the_config_folder():
    env = {"CLAUDE_JOB_DIR": "/home/alice/.claude/jobs/ab12cd34"}
    assert bg_jobs.job_from_env(env) == ("ab12cd34", "/home/alice/.claude")
    long_ok = {"CLAUDE_JOB_DIR": "/home/alice/.claude/jobs/" + "a" * 64}
    assert bg_jobs.job_from_env(long_ok) == ("a" * 64, "/home/alice/.claude")


@pytest.mark.parametrize("value", [
    "/home/alice/.claude/jobs/..",
    "/home/alice/.claude/jobs/ab12cd34/",
    "/home/alice/.claude/jobs/ab.12",
    "/home/alice/.claude/jobs/" + "a" * 65,
    "home/alice/.claude/jobs/ab12cd34",
    "/home/alice/.claude/jobs/pids",
], ids=["dotdot", "trailing-slash", "dot-in-name", "65-chars", "relative", "reserved"])
def test_a_job_dir_that_names_no_job_is_none(value):
    assert bg_jobs.job_from_env({"CLAUDE_JOB_DIR": value}) is None


def test_the_process_environment_is_read_by_default(monkeypatch):
    monkeypatch.setenv("CLAUDE_JOB_DIR", "/home/alice/.claude/jobs/ab12cd34")
    assert bg_jobs.job_from_env() == ("ab12cd34", "/home/alice/.claude")


# ---- T1.2: the link file -----------------------------------------------------------------

def _link(job=JOB, session=LINKED, **extra) -> Path:
    path = bg_jobs.link_path(job)
    path.parent.mkdir(parents=True, exist_ok=True)
    assert bg_jobs.write_own_json(path, {"job": job, "session": session, **extra})
    return path


def _resolve(tmp_path, job=JOB):
    return bg_jobs.resolve_worker(job, str(tmp_path / "cfg"), INHERITED)


def test_a_matching_link_makes_the_worker_linked(tmp_path):
    _link()
    assert _resolve(tmp_path) == {"role": "linked", "job": JOB, "session": LINKED}


def test_an_ended_link_still_links_its_job(tmp_path):
    _link(ended=True)
    assert _resolve(tmp_path)["role"] == "linked"


def test_a_link_naming_another_job_is_foreign(tmp_path):
    path = bg_jobs.link_path(JOB)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"job": "ffff0000", "session": LINKED}))
    assert _resolve(tmp_path) == {"role": "foreign", "job": JOB, "session": INHERITED}


def test_a_symlinked_link_is_foreign(tmp_path):
    real = tmp_path / "elsewhere.json"
    real.write_text(json.dumps({"job": JOB, "session": LINKED}))
    path = bg_jobs.link_path(JOB)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(real)
    assert _resolve(tmp_path)["role"] == "foreign"


def test_a_link_owned_by_someone_else_is_foreign(tmp_path, monkeypatch):
    _link()
    monkeypatch.setattr(bg_jobs, "_getuid", lambda: os.getuid() + 1)
    assert _resolve(tmp_path)["role"] == "foreign"


@pytest.mark.parametrize("body", [b"{not json", b"[1, 2]", b"", b'{"job": "ab12cd34"}',
                                  b'{"job": "ab12cd34", "session": "../etc"}'],
                         ids=["malformed", "not-object", "empty", "no-session", "bad-session"])
def test_a_link_that_cannot_be_read_is_foreign(tmp_path, body):
    path = bg_jobs.link_path(JOB)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    assert _resolve(tmp_path)["role"] == "foreign"


@pytest.mark.parametrize("tail", ["inside", "padding"])
def test_an_oversized_link_is_foreign(tmp_path, tail):
    """Past the cap a link decides nothing, even when what fits in the cap
    still reads as a valid link (a valid object, then padding)."""
    path = bg_jobs.link_path(JOB)
    path.parent.mkdir(parents=True, exist_ok=True)
    if tail == "inside":
        body = json.dumps({"job": JOB, "session": LINKED, "pad": "x" * bg_jobs.MAX_BYTES})
    else:
        body = json.dumps({"job": JOB, "session": LINKED}) + " " * bg_jobs.MAX_BYTES
    path.write_text(body)
    assert _resolve(tmp_path)["role"] == "foreign"


def test_a_fifo_at_the_link_never_stalls(tmp_path):
    path = bg_jobs.link_path(JOB)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(path)
    assert bg_jobs.read_own_json(path) is None
    assert _resolve(tmp_path)["role"] == "foreign"


def test_a_fifo_holding_a_valid_link_is_still_foreign(tmp_path):
    """Only a regular file is a link: a FIFO someone keeps fed with a valid
    body decides nothing."""
    path = bg_jobs.link_path(JOB)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(path)
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    try:
        os.write(fd, json.dumps({"job": JOB, "session": LINKED}).encode())
        assert _resolve(tmp_path)["role"] == "foreign"
    finally:
        os.close(fd)


def test_a_written_file_is_private_and_atomic(tmp_path):
    folder = tmp_path / "own"
    folder.mkdir()
    path = folder / "f.json"
    assert bg_jobs.write_own_json(path, {"a": 1})
    assert (path.stat().st_mode & 0o777) == 0o600
    assert bg_jobs.read_own_json(path) == {"a": 1}
    assert [p.name for p in folder.iterdir()] == ["f.json"]  # no temp left


def test_a_write_never_follows_a_symlinked_temp(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("keep")
    path = tmp_path / "f.json"
    (tmp_path / f"f.json.tmp.{os.getpid()}").symlink_to(victim)
    assert bg_jobs.write_own_json(path, {"a": 1}) is False
    assert victim.read_text() == "keep"


def test_remove_own_leaves_another_users_file(tmp_path, monkeypatch):
    path = tmp_path / "f.json"
    path.write_text("{}")
    monkeypatch.setattr(bg_jobs, "_getuid", lambda: os.getuid() + 1)
    bg_jobs.remove_own(path)
    assert path.exists()
    monkeypatch.setattr(bg_jobs, "_getuid", os.getuid)
    bg_jobs.remove_own(path)
    assert not path.exists()
    bg_jobs.remove_own(path)  # already gone: no error


# ---- T1.3: the registry fallback -----------------------------------------------------------

def _registry(tmp_path, *entries) -> Path:
    sessions = tmp_path / "cfg" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    for e in entries:
        (sessions / f"{e['pid']}.json").write_text(json.dumps(e))
    return sessions


def _client(pid=4242, *, kind="interactive", job=JOB, start="777") -> dict:
    e = {"pid": pid, "sessionId": "0f0f0f0f-aaaa", "kind": kind, "parkedJobId": job,
         "status": "idle"}
    if start is not None:
        e["procStart"] = start
    return e


def _pidmap(**pids):
    path = bg_jobs.pidmap_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    assert bg_jobs.write_own_json(path, {"pids": pids})


def test_a_parked_client_the_pid_map_names_links_the_job(tmp_path):
    _registry(tmp_path, _client())
    _pidmap(**{"4242": {"session": LINKED, "start": 777}})
    assert _resolve(tmp_path) == {"role": "linked", "job": JOB, "session": LINKED}


def test_a_start_time_known_on_one_side_only_still_links(tmp_path):
    _registry(tmp_path, _client(start=None))
    _pidmap(**{"4242": {"session": LINKED, "start": 777}})
    assert _resolve(tmp_path)["role"] == "linked"


def test_a_reused_pid_never_links(tmp_path):
    _registry(tmp_path, _client(start="778"))
    _pidmap(**{"4242": {"session": LINKED, "start": 777}})
    assert _resolve(tmp_path)["role"] == "foreign"


def test_a_background_entry_never_links(tmp_path):
    """The job's own entry (kind "bg") holds the same id: it is the worker
    itself, never the session's client."""
    _registry(tmp_path, _client(kind="bg"))
    _pidmap(**{"4242": {"session": LINKED, "start": 777}})
    assert _resolve(tmp_path)["role"] == "foreign"


def test_a_client_parked_on_another_job_never_links(tmp_path):
    _registry(tmp_path, _client(job="ffff0000"))
    _pidmap(**{"4242": {"session": LINKED, "start": 777}})
    assert _resolve(tmp_path)["role"] == "foreign"


def test_no_pid_map_is_foreign(tmp_path):
    _registry(tmp_path, _client())
    assert _resolve(tmp_path)["role"] == "foreign"


def test_two_sessions_for_one_job_is_no_answer(tmp_path):
    _registry(tmp_path, _client(4242), _client(4343))
    _pidmap(**{"4242": {"session": LINKED, "start": 777},
               "4343": {"session": "claude-api", "start": 777}})
    assert _resolve(tmp_path)["role"] == "foreign"


def test_no_registry_folder_is_foreign(tmp_path):
    assert bg_jobs.scan_registry(str(tmp_path / "nowhere")) == []
    assert _resolve(tmp_path)["role"] == "foreign"


def test_the_registry_scan_is_bounded(tmp_path):
    sessions = _registry(tmp_path)
    for pid in range(1000, 1100):
        (sessions / f"{pid}.json").write_text(json.dumps({"pid": pid}))
    (sessions / "notes.txt").write_text("x")
    got = bg_jobs.scan_registry(str(tmp_path / "cfg"))
    assert len(got) == bg_jobs.REGISTRY_LIMIT


def test_the_link_wins_over_the_registry(tmp_path):
    _link(session="claude-api")
    _registry(tmp_path, _client())
    _pidmap(**{"4242": {"session": LINKED, "start": 777}})
    assert _resolve(tmp_path)["session"] == "claude-api"


def test_resolve_from_env_without_a_job_is_foreign(monkeypatch):
    monkeypatch.setenv("CLAUDE_JOB_DIR", "relative/jobs/x")
    assert bg_jobs.resolve_from_env(INHERITED) == {
        "role": "foreign", "job": "", "session": INHERITED}


def test_resolve_from_env_reads_the_job(tmp_path, monkeypatch):
    _link()
    monkeypatch.setenv("CLAUDE_JOB_DIR", str(tmp_path / "cfg" / "jobs" / JOB))
    assert bg_jobs.resolve_from_env(INHERITED)["session"] == LINKED


# ---- T1.19: the new runtime files are protected -----------------------------------------

def test_the_new_globs_are_no_access():
    assert "/tmp/claude-bgjob-*" in safety.DENY_PATHS_NO_ACCESS
    assert "/tmp/claude-exit-*" in safety.DENY_PATHS_NO_ACCESS


def test_the_new_globs_move_with_an_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(tmp_path))
    globs = instance.protected_globs()
    assert f"{tmp_path}/claude-bgjob-*" in globs
    assert f"{tmp_path}/claude-exit-*" in globs


@pytest.mark.parametrize("tool,inp", [
    ("Write", {"file_path": "/tmp/claude-bgjob-ab12cd34.json", "content": "{}"}),
    ("Write", {"file_path": "/tmp/claude-bgjob-pids.json", "content": "{}"}),
    ("Edit", {"file_path": "/tmp/claude-exit-claude-x.json", "old_string": "a",
              "new_string": "b"}),
    ("Read", {"file_path": "/tmp/claude-bgjob-ab12cd34.json"}),
], ids=["write-link", "write-pidmap", "edit-exit", "read-link"])
def test_a_restricted_turn_cannot_touch_the_new_files(tool, inp):
    assert safety.path_violation(tool, inp, safety.DENY_PATHS_NO_ACCESS,
                                 safety.DENY_PATHS_NO_WRITE)


def test_a_restricted_write_under_an_instance_is_denied(tmp_path, monkeypatch):
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(tmp_path))
    target = str(tmp_path / f"claude-bgjob-{JOB}.json")
    assert safety.path_violation("Write", {"file_path": target, "content": "{}"},
                                 instance.protected_globs(), ())


# ---- T1.20: the suite redirects the new paths --------------------------------------------

def test_the_new_paths_resolve_under_tmp_path(tmp_path):
    for path in (bg_jobs.link_path(JOB), bg_jobs.pidmap_path(),
                 bg_jobs.exit_path("claude-x")):
        assert Path(path).is_relative_to(tmp_path), path


def test_the_real_paths_follow_the_runtime_folder(tmp_path, monkeypatch):
    """The unredirected helpers (read off the module's source functions)
    put every file in instance.runtime_tmp_dir()."""
    import importlib
    fresh = importlib.util.spec_from_file_location(
        "_bg_jobs_unpatched", bg_jobs.__file__)
    mod = importlib.util.module_from_spec(fresh)
    fresh.loader.exec_module(mod)
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(tmp_path))
    assert mod.link_path(JOB) == tmp_path / f"claude-bgjob-{JOB}.json"
    assert mod.pidmap_path() == tmp_path / "claude-bgjob-pids.json"
    assert mod.exit_path("claude-x") == tmp_path / "claude-exit-claude-x.json"


def test_the_newest_registry_files_are_read_among_many_stale_ones(tmp_path):
    """Claude leaves old claudes' files behind: the entry ``←`` just wrote
    is read even when it sorts after more stale names than the bound
    (review nit: collect every name, then pick)."""
    import os
    sessions = _registry(tmp_path)
    for pid in range(1000, 1100):
        path = sessions / f"{pid}.json"
        path.write_text(json.dumps({"pid": pid}))
        os.utime(path, ns=(1_600_000_000_000_000_000 + pid, 1_600_000_000_000_000_000 + pid))
    newest = sessions / "99999.json"
    newest.write_text(json.dumps({"pid": 99999, "kind": "interactive"}))
    got = bg_jobs.scan_registry(str(tmp_path / "cfg"))
    assert len(got) == bg_jobs.REGISTRY_LIMIT
    assert {"pid": 99999, "kind": "interactive"} in got
