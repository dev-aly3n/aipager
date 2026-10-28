"""aipager/claude_jobs.py: the readers and runners later steps build on.

The live holder of a conversation, where a conversation moved
(``continued-in``, bounded hops), a job's state and the two ``claude``
runners. Claude's files are a fake config folder under tmp_path; the /proc
lookups and the subprocess calls are the module's seams.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from aipager import claude_jobs

X = "aaaaaaaa-0000-4000-8000-000000000001"
Y = "bbbbbbbb-0000-4000-8000-000000000002"
Z = "cccccccc-0000-4000-8000-000000000003"
W = "dddddddd-0000-4000-8000-000000000004"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    root = tmp_path / "claude-config"
    (root / "sessions").mkdir(parents=True)
    (root / "projects" / "-home-alice-app").mkdir(parents=True)
    monkeypatch.setattr(claude_jobs, "config_dir", lambda: root)
    return root


@pytest.fixture
def proc(monkeypatch):
    p = SimpleNamespace(start={}, alive=set())
    monkeypatch.setattr(claude_jobs, "_proc_start", lambda pid: p.start.get(pid))
    monkeypatch.setattr(claude_jobs, "_pid_alive", lambda pid: pid in p.alive)
    return p


def reg(cfg, pid, **fields):
    (cfg / "sessions" / f"{pid}.json").write_text(json.dumps({"pid": pid, **fields}))


def moved(cfg, sid, to=None, *, then=()):
    records = [{"type": "user"}]
    if to:
        records.append({"type": "continued-in", "continuedInSessionId": to})
    records.extend(then)
    path = cfg / "projects" / "-home-alice-app" / f"{sid}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


# ---- live_holder ---------------------------------------------------------------

def test_a_live_background_process_holds_the_conversation(cfg, proc):
    reg(cfg, 300, kind="bg", sessionId=X, jobId="aaaaaaaa", procStart="55")
    proc.alive.add(300)
    proc.start[300] = 55
    holder = claude_jobs.live_holder(X)
    assert holder is not None and holder["pid"] == 300


@pytest.mark.parametrize("case", ["interactive", "dead", "reused-pid", "other-id"])
def test_what_is_no_holder(cfg, proc, case):
    kind, sid, start = "bg", X, 55
    if case == "interactive":
        kind = "interactive"
    elif case == "other-id":
        sid = Y
    elif case == "reused-pid":
        start = 56
    reg(cfg, 300, kind=kind, sessionId=sid, procStart="55")
    if case != "dead":
        proc.alive.add(300)
    proc.start[300] = start
    assert claude_jobs.live_holder(X) is None


def test_no_registry_means_no_holder(cfg, proc):
    (cfg / "sessions").rmdir()
    assert claude_jobs.live_holder(X) is None


# ---- follow_continued ----------------------------------------------------------

def test_a_moved_conversation_is_followed(cfg, proc):
    moved(cfg, X, Y)
    moved(cfg, Y)
    assert claude_jobs.follow_continued(X) == Y


def test_a_hop_needs_the_target_somewhere(cfg, proc):
    """A target with neither a transcript nor a live holder is not
    followed (a fork that never ran a prompt has no transcript yet)."""
    moved(cfg, X, Y)
    assert claude_jobs.follow_continued(X) == X
    reg(cfg, 300, kind="bg", sessionId=Y, jobId="bbbbbbbb")
    proc.alive.add(300)
    assert claude_jobs.follow_continued(X) == Y


def test_a_continued_in_that_is_not_last_is_not_followed(cfg, proc):
    moved(cfg, X, Y, then=[{"type": "user"}])
    moved(cfg, Y)
    assert claude_jobs.follow_continued(X) == X


def test_a_cycle_stops(cfg, proc):
    """X -> Y -> Z -> X: never back through a conversation seen already."""
    moved(cfg, X, Y)
    moved(cfg, Y, Z)
    moved(cfg, Z, X)
    assert claude_jobs.follow_continued(X) == Z


def test_the_hops_are_capped(cfg, proc):
    moved(cfg, X, Y)
    moved(cfg, Y, Z)
    moved(cfg, Z, W)
    moved(cfg, W, "eeeeeeee-0000-4000-8000-000000000005")
    (cfg / "projects" / "-home-alice-app" / "eeeeeeee-0000-4000-8000-000000000005.jsonl"
     ).write_text(json.dumps({"type": "user"}) + "\n")
    assert claude_jobs.follow_continued(X) == W


@pytest.mark.parametrize("bad", ["", "*", "../x", "a/b", "x" * 65])
def test_an_id_that_names_no_file_finds_nothing(cfg, bad):
    """Not a pattern and not a path: with transcripts there that a glob
    or a ``..`` would reach, nothing is found."""
    moved(cfg, X)
    (cfg / "x.jsonl").write_text(json.dumps({"type": "user"}) + "\n")
    (cfg / "projects" / "-home-alice-app" / "a").mkdir()
    (cfg / "projects" / "-home-alice-app" / "a" / "b.jsonl").write_text("{}\n")
    assert claude_jobs.find_transcript(bad) is None
    assert claude_jobs.follow_continued(bad) == bad


# ---- job_state -----------------------------------------------------------------

@pytest.fixture
def agents(monkeypatch):
    """``claude agents --json`` through the runner seam."""
    out = SimpleNamespace(rc=0, stdout="[]", calls=[])

    class Proc:
        @property
        def returncode(self):
            return out.rc

        async def communicate(self, _stdin=None):
            return out.stdout.encode(), b""

    async def fake_exec(*argv, **_kw):
        out.calls.append(argv)
        return Proc()

    monkeypatch.setattr(claude_jobs, "_create_subprocess_exec", fake_exec)
    return out


@pytest.mark.parametrize("status, state", [("busy", "busy"), ("idle", "idle"),
                                           ("weird", "unknown")])
def test_the_jobs_own_entry_says_its_state(cfg, agents, run_async, status, state):
    reg(cfg, 300, kind="bg", sessionId=X, jobId="aaaaaaaa", status=status)
    reg(cfg, 301, kind="interactive", sessionId=W, parkedJobId="aaaaaaaa",
        status="idle" if status == "busy" else "busy")
    assert run_async(claude_jobs.job_state("aaaaaaaa")) == state
    assert agents.calls == [], "the listing is not needed"


def test_without_an_entry_the_listing_decides(cfg, agents, run_async):
    agents.stdout = json.dumps([{"id": "aaaaaaaa", "status": "busy", "kind": "background"}])
    assert run_async(claude_jobs.job_state("aaaaaaaa")) == "busy"
    assert agents.calls and list(agents.calls[0][-2:]) == ["agents", "--json"]
    agents.stdout = "[]"
    assert run_async(claude_jobs.job_state("aaaaaaaa")) == "gone"


def test_a_failed_listing_with_a_readable_registry_is_gone(cfg, agents, run_async):
    agents.rc = 1
    assert run_async(claude_jobs.job_state("aaaaaaaa")) == "gone"


def test_nothing_readable_is_unknown(cfg, agents, run_async):
    (cfg / "sessions").rmdir()
    agents.rc = 1
    assert run_async(claude_jobs.job_state("aaaaaaaa")) == "unknown"
    agents.rc, agents.stdout = 0, "not json"
    assert run_async(claude_jobs.job_state("aaaaaaaa")) == "unknown"


# ---- the runners ---------------------------------------------------------------

def test_run_claude_reports_the_exit_code(agents, run_async):
    agents.rc, agents.stdout = 3, "out"
    done = run_async(claude_jobs.run_claude(["stop", "aaaaaaaa"], 5))
    assert (done.rc, done.out, done.error) == (3, "out", "")
    assert list(agents.calls[0][-2:]) == ["stop", "aaaaaaaa"]


def test_run_claude_times_out(monkeypatch, run_async):
    killed = []

    class Slow:
        returncode = None

        async def communicate(self, _stdin=None):
            await asyncio.sleep(5)
            return b"", b""

        def kill(self):
            killed.append(True)

    async def fake_exec(*_a, **_kw):
        return Slow()

    monkeypatch.setattr(claude_jobs, "_create_subprocess_exec", fake_exec)
    done = run_async(claude_jobs.run_claude(["stop", "aaaaaaaa"], 0.05))
    assert (done.rc, done.error) == (None, "timeout")
    assert killed == [True]


def test_run_claude_without_a_binary(monkeypatch, run_async):
    async def fake_exec(*_a, **_kw):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(claude_jobs, "_create_subprocess_exec", fake_exec)
    done = run_async(claude_jobs.run_claude(["agents", "--json"], 1))
    assert (done.rc, done.error) == (None, "no_claude")


@pytest.mark.parametrize("outcome", ["ok", "timeout", "missing"])
def test_run_claude_sync(monkeypatch, outcome):
    seen = []

    def fake_run(argv, **kw):
        seen.append((argv, kw))
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(argv, kw["timeout"])
        if outcome == "missing":
            raise FileNotFoundError("claude")
        return SimpleNamespace(returncode=0, stdout=b"[]", stderr=b"")

    monkeypatch.setattr(claude_jobs, "_subprocess_run", fake_run)
    done = claude_jobs.run_claude_sync(["stop", "aaaaaaaa"], 10)
    expect = {"ok": (0, ""), "timeout": (None, "timeout"),
              "missing": (None, "no_claude")}[outcome]
    assert (done.rc, done.error) == expect
    assert seen[0][0][-2:] == ["stop", "aaaaaaaa"]
    assert seen[0][1]["timeout"] == 10 and isinstance(seen[0][1]["env"], dict)


def test_a_miss_is_searched_again_unless_the_caller_caches_it(cfg):
    assert claude_jobs.find_transcript(X) is None
    moved(cfg, X)
    assert claude_jobs.find_transcript(X, cache_misses=True) is None, "the miss is kept"
    assert claude_jobs.find_transcript(X) is not None, "a plain call searches again"


def test_a_found_transcript_that_moved_is_searched_again(cfg):
    moved(cfg, X)
    first = claude_jobs.find_transcript(X)
    other = cfg / "projects" / "-home-alice-other"
    other.mkdir()
    first.rename(other / first.name)
    assert claude_jobs.find_transcript(X, cache_misses=True) == other / first.name


def test_no_test_runs_a_real_claude_command(run_async):
    """The conftest guard: without a fake of its own, a runner spawns
    nothing and reports no claude."""
    assert run_async(claude_jobs.run_claude(["agents", "--json"], 1)).error == "no_claude"
    assert claude_jobs.run_claude_sync(["agents", "--json"], 1).error == "no_claude"


# ---- review rev-iter1-005: no entry is "gone" only in a registry read whole ----------

def _aged(cfg, pid, age_s, **fields):
    """A registry file written *age_s* seconds before the newest ones."""
    import os
    reg(cfg, pid, **fields)
    path = cfg / "sessions" / f"{pid}.json"
    ns = 1_800_000_000_000_000_000 - int(age_s * 1e9)
    os.utime(path, ns=(ns, ns))


def test_every_name_is_looked_at_and_the_newest_are_parsed(cfg, monkeypatch):
    """More files than one read may parse: the job's file, written last, is
    parsed even when it sorts after all the stale ones."""
    monkeypatch.setattr(claude_jobs, "REGISTRY_SCAN_LIMIT", 5)
    for pid in range(1000, 1020):
        _aged(cfg, pid, 2000 - pid, kind="interactive", sessionId=W)
    _aged(cfg, 99999, 0, kind="bg", jobId="aaaaaaaa", sessionId=X, status="idle")
    entries = claude_jobs.read_registry()
    assert claude_jobs.job_entry("aaaaaaaa", entries) is not None
    assert entries.complete is False, "15 files were left out"


def test_a_registry_read_whole_is_complete(cfg):
    for pid in range(1000, 1010):
        reg(cfg, pid, kind="interactive", sessionId=W)
    assert claude_jobs.read_registry().complete is True


def test_too_many_names_is_not_complete(cfg, monkeypatch):
    monkeypatch.setattr(claude_jobs, "REGISTRY_NAMES_LIMIT", 3)
    for pid in range(1000, 1005):
        reg(cfg, pid, kind="interactive", sessionId=W)
    assert claude_jobs.read_registry().complete is False


def test_a_file_that_cannot_be_parsed_is_not_complete(cfg):
    reg(cfg, 1000, kind="interactive", sessionId=W)
    (cfg / "sessions" / "1001.json").write_text("{ half written")
    entries = claude_jobs.read_registry()
    assert len(entries) == 1 and entries.complete is False
    # and the cached failure is remembered as one
    assert claude_jobs.read_registry().complete is False


@pytest.mark.parametrize("how", ["parse-budget", "broken-file", "too-many-names"])
def test_a_failed_listing_with_a_partial_registry_is_unknown(cfg, agents, run_async,
                                                              monkeypatch, how):
    agents.rc = 1
    if how == "parse-budget":
        monkeypatch.setattr(claude_jobs, "REGISTRY_SCAN_LIMIT", 1)
        reg(cfg, 1000, kind="interactive", sessionId=W)
        reg(cfg, 1001, kind="interactive", sessionId=X)
    elif how == "broken-file":
        (cfg / "sessions" / "1001.json").write_text("{ half written")
    else:
        monkeypatch.setattr(claude_jobs, "REGISTRY_NAMES_LIMIT", 1)
        reg(cfg, 1000, kind="interactive", sessionId=W)
        reg(cfg, 1001, kind="interactive", sessionId=X)
    assert run_async(claude_jobs.job_state("aaaaaaaa")) == "unknown"


def test_the_listing_is_matched_by_job_id_only(cfg, agents, run_async):
    """Once Claude's daemon runs, the listing holds the interactive
    sessions too, with no ``id``: they are not the job (live check 1,
    finding 3)."""
    agents.stdout = json.dumps([
        {"kind": "interactive", "sessionId": X, "status": "busy"},
        {"id": "bbbbbbbb", "kind": "background", "status": "busy"}])
    assert run_async(claude_jobs.job_state("aaaaaaaa")) == "gone"


def test_the_command_line_says_unknown_for_a_partial_registry(cfg, monkeypatch):
    """``job_state_sync`` (``aipager session --resume``): the same rule."""
    monkeypatch.setattr(claude_jobs, "REGISTRY_NAMES_LIMIT", 1)
    reg(cfg, 1000, kind="interactive", sessionId=W)
    reg(cfg, 1001, kind="interactive", sessionId=X)
    monkeypatch.setattr(claude_jobs, "_subprocess_run",
                        lambda *a, **k: SimpleNamespace(returncode=1, stdout=b"", stderr=b""))
    assert claude_jobs.job_state_sync("aaaaaaaa") == "unknown"


# ---- a zombie is not alive (fix iteration 2) ------------------------------------------

def test_a_zombie_is_not_alive():
    """A killed process its parent has not reaped yet still answers signal
    0: ``_pid_alive`` reads its state and says no."""
    import subprocess
    import sys
    import time as _time
    if not sys.platform.startswith("linux"):
        pytest.skip("/proc only")
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        deadline = _time.monotonic() + 10
        while claude_jobs._proc_state(child.pid) != "Z":
            assert _time.monotonic() < deadline, "the child never became a zombie"
            _time.sleep(0.01)
        assert claude_jobs._pid_alive(child.pid) is False
    finally:
        child.wait()


def test_a_running_process_is_alive():
    assert claude_jobs._pid_alive(os.getpid()) is True


# ---- roadmap 8.127: a dialog in the terminal, as the record says it -------------

def _read_one(cfg, pid=300, **fields):
    reg(cfg, pid, kind="interactive", **fields)
    entries = claude_jobs.read_registry()
    assert entries is not None and len(entries) == 1
    return entries[0]


def test_entry_keeps_waiting_for_and_status_at(cfg):
    """``waitingFor`` is kept, and ``statusUpdatedAt`` (Claude Code writes
    milliseconds) becomes wall-clock seconds."""
    import time
    now_ms = int(time.time() * 1000)
    entry = _read_one(cfg, status="waiting", waitingFor="dialog open",
                      statusUpdatedAt=now_ms)
    assert entry["waitingFor"] == "dialog open"
    assert entry["statusAt"] == pytest.approx(now_ms / 1000.0)
    # Seconds are taken as seconds.
    assert claude_jobs._entry({"statusUpdatedAt": now_ms / 1000.0}, 300)[
        "statusAt"] == pytest.approx(now_ms / 1000.0)


@pytest.mark.parametrize("field,value,expect", [
    ("waitingFor", 7, ""),
    ("waitingFor", ["dialog open"], ""),
    ("statusUpdatedAt", "1761600000000", None),
    ("statusUpdatedAt", True, None),
    ("statusUpdatedAt", 1_000_000_000_000, None),        # 2001: implausible
    ("statusUpdatedAt", 99_999_999_999_999, None),       # far ahead
])
def test_entry_rejects_bad_waiting_fields(cfg, field, value, expect):
    entry = _read_one(cfg, status="waiting", **{field: value})
    key = "waitingFor" if field == "waitingFor" else "statusAt"
    assert entry[key] == expect


def test_job_state_waiting_stays_unknown():
    """A job's record that says waiting is neither busy nor idle."""
    assert claude_jobs.entry_state({"status": "waiting",
                                    "waitingFor": "dialog open"}) == "unknown"
