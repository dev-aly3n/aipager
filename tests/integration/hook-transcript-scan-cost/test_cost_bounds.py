"""Cost bounds (design.md "Benchmark and acceptance procedure" and spec.md
requirement 5, scaled down to test size; the authoritative 500 MB / 1 GB
numbers are the Developer's vm2 benchmark).

- An owner turn decides in milliseconds without reading the transcript.
- On a realistic restricted turn (tool results that do not carry the
  marker words) both scans are several times cheaper in CPU than the
  166a3f6 scans on the same file.
- A 40 MB single line costs well under a second per scan, and the
  hook's peak RSS on it stays at or under 150 MB (measured in a child
  process so this process's own heap does not count).
- Error guessing: a ~200 MB tool-result line under the hook's 1 GiB
  address-space limit no longer becomes a MemoryError deny.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from aipager import policy, policy_snapshot as ps
from aipager.dtach import enforce


def _kit():
    name = "_scancost_kit"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).resolve().parent / "_scan_kit.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


kit = _kit()
cc = kit.cc
TG = cc(kit.tg_prompt("please look"))
TERM = cc(kit.term_prompt("typed here"))
ROOT = Path(enforce.__file__).resolve().parents[2]


def _cpu(fn, *args, repeat=2):
    best = float("inf")
    for _ in range(repeat):
        t = time.process_time()
        fn(*args)
        best = min(best, time.process_time() - t)
    return best


@pytest.fixture(scope="module")
def realistic_turn(tmp_path_factory):
    """~24 MB: a Telegram prompt, then a long turn of real-shaped tool
    results (code and logs, none of the three marker words) and
    assistant tool calls."""
    import random
    rng = random.Random(11)
    p = tmp_path_factory.mktemp("cost") / "turn.jsonl"
    body = ("def handler(event):\n    return compute(event['x'])\n" * 40
            + "INFO request served in 12ms\n" * 30)
    with open(p, "wb") as f:
        f.write(TERM + b"\n" + TG + b"\n")
        size = 0
        while size < 24 << 20:
            if rng.random() < 0.6:
                ln = cc(kit.tool_result(body[: rng.randint(200, len(body))],
                                        rng=rng))
            else:
                ln = cc(kit.assistant_tool_use({"command": "pytest -q"},
                                               rng=rng))
            f.write(ln + b"\n")
            size += len(ln) + 1
    return p


def test_origin_scan_is_at_least_2_5x_cheaper_than_166a3f6(realistic_turn, old):
    new = _cpu(enforce._origin_from_transcript, str(realistic_turn))
    ref = _cpu(old._origin_from_transcript, str(realistic_turn))
    print(f"origin new {new:.3f}s old {ref:.3f}s x{ref / new:.1f}")
    assert ref / new >= 2.5, (new, ref)


def test_sticky_scan_is_at_least_2x_cheaper_than_166a3f6(realistic_turn,
                                                         old):
    # Thresholds sit below the measured 3.3x / 2.9x on a 24 MB turn so
    # a loaded box does not flake; the acceptance numbers are vm2's.
    new = _cpu(enforce._turn_already_blocked, str(realistic_turn))
    ref = _cpu(old._turn_already_blocked, str(realistic_turn))
    print(f"sticky new {new:.3f}s old {ref:.3f}s x{ref / new:.1f}")
    assert ref / new >= 2.0, (new, ref)


def test_scans_on_the_realistic_turn_still_find_the_prompt(realistic_turn):
    assert (enforce._origin_from_transcript(str(realistic_turn)),
            enforce._turn_already_blocked(str(realistic_turn))) == (
        "telegram", False)


def test_owner_decide_on_a_long_turn_takes_milliseconds(realistic_turn,
                                                        monkeypatch):
    monkeypatch.setattr(enforce, "read_snapshot",
                        lambda s: {"bypass_safety": True})
    t = time.perf_counter()
    for _ in range(20):
        enforce.decide(kit.pretool(realistic_turn, "Bash", {"command": "ls"}))
    assert (time.perf_counter() - t) / 20 < 0.01


@pytest.fixture(scope="module")
def huge_line(tmp_path_factory):
    """A Telegram prompt then one 40 MB tool result (a big file Read)."""
    p = tmp_path_factory.mktemp("huge") / "huge.jsonl"
    big = cc(kit.tool_result("0123456789abcdef" * (40 << 16)))
    with open(p, "wb") as f:
        f.write(TG + b"\n" + big + b"\n")
    return p


@pytest.mark.parametrize("which", ["_origin_from_transcript",
                                   "_turn_already_blocked"])
def test_40mb_line_scan_under_half_a_second(huge_line, which):
    took = _cpu(getattr(enforce, which), str(huge_line))
    assert took < 0.5, took


@pytest.mark.parametrize("which", ["_origin_from_transcript",
                                   "_turn_already_blocked"])
def test_40mb_line_scan_peak_allocation_about_twice_the_line(huge_line,
                                                             which):
    import tracemalloc
    n = huge_line.stat().st_size
    tracemalloc.start()
    try:
        getattr(enforce, which)(str(huge_line))
        _cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak <= 2.5 * n + (2 << 20), (peak, n)


# ---- the hook as its own process ----------------------------------------

_CHILD = textwrap.dedent("""
    import io, json, resource, sys
    from pathlib import Path
    cfg = json.loads(sys.argv[1])
    if cfg["as_limit"]:
        resource.setrlimit(resource.RLIMIT_AS,
                           (cfg["as_limit"], cfg["as_limit"]))
    from aipager import policy_snapshot as ps
    from aipager.dtach import enforce, notify_hook
    base = Path(cfg["snapdir"])
    ps.snapshot_path = lambda n: base / f"claude-policy-{n}.json"
    ps.floor_path = lambda: base / "claude-policy-.floor.json"
    reply = lambda n: base / f"claude-reply-{n}.txt"
    ps.reply_context_path = reply
    enforce.reply_context_path = reply
    notify_hook.SOCKET_PATH = cfg["sock"]
    sys.stdin = io.StringIO(json.dumps(cfg["payload"]))
    notify_hook._run(cfg["session"], [b""])
    sys.stdout.flush()
    # VmHWM, not ru_maxrss: Linux carries ru_maxrss across fork+exec,
    # so a child of a big pytest process inherits the parent's peak.
    hwm = [ln for ln in open("/proc/self/status")
           if ln.startswith("VmHWM:")][0].split()[1]
    print("RSS_KB", hwm, file=sys.stderr)
""")


def _child_hook(tmp_path, transcript, snapdir, *, as_limit=0):
    home = tmp_path / "childhome"
    home.mkdir(exist_ok=True)
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    cfg = {"as_limit": as_limit, "snapdir": str(snapdir),
           "sock": str(tmp_path / "nope.sock"), "session": kit.SESSION,
           "payload": {"hook_event_name": "PreToolUse",
                       "session_id": kit.CLAUDE_SID, "cwd": str(project),
                       "tool_name": "Read",
                       "tool_input": {"file_path": str(project / "a.txt")},
                       "transcript_path": str(transcript)}}
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": str(home), "PYTHONPATH": str(ROOT),
           "PYTHONDONTWRITEBYTECODE": "1", "CLAUDE_TG_CHAT_ID": "",
           "CLAUDE_TG_BOT_TOKEN": "", "AIPAGER_SOCKET_PATH": cfg["sock"],
           "CLAUDE_DTACH_SESSION": kit.SESSION}
    r = subprocess.run([sys.executable, "-c", _CHILD, json.dumps(cfg)],
                       capture_output=True, text=True, env=env, timeout=120,
                       cwd=str(tmp_path))
    rss_kb = None
    for ln in r.stderr.splitlines():
        if ln.startswith("RSS_KB "):
            rss_kb = int(ln.split()[1])
    return r.returncode, r.stdout, rss_kb, r.stderr[-2000:]


def _user_role(snap_file):
    p = snap_file(kit.SESSION)
    pol = policy.load_policy(Path("/nonexistent/p.yaml"),
                             Path("/nonexistent/p.d"))
    ps.write_merged_snapshot(
        kit.SESSION, ps.resolve_snapshot(pol.get_role("user"), None, None))
    return p.parent


@pytest.mark.skipif(not Path("/proc/self/status").exists(),
                    reason="needs /proc VmHWM")
def test_hook_rss_on_a_40mb_line_is_at_most_150mb(tmp_path, huge_line,
                                                  snap_file):
    snapdir = _user_role(snap_file)
    code, out, rss_kb, err = _child_hook(tmp_path, huge_line, snapdir)
    print("hook VmHWM KB on 40 MB line:", rss_kb)
    assert (code, out, rss_kb is not None and rss_kb <= 150 * 1024) == (
        0, "", True), (rss_kb, err)


def test_hook_allows_past_a_200mb_line_under_a_1gib_address_limit(
        tmp_path, snap_file):
    """166a3f6 hit MemoryError on a line this size under RLIMIT_AS=1 GiB
    and denied through fail_closed; the change exists to remove that."""
    snapdir = _user_role(snap_file)
    p = tmp_path / "x200.jsonl"
    big = cc(kit.tool_result("q" * (200 << 20)))
    with open(p, "wb") as f:
        f.write(TG + b"\n" + big + b"\n")
    del big
    try:
        code, out, rss_kb, err = _child_hook(tmp_path, p, snapdir,
                                             as_limit=1 << 30)
    finally:
        p.unlink()
    print("hook VmHWM KB on 200 MB line:", rss_kb)
    assert (code, out) == (0, ""), (rss_kb, err)
