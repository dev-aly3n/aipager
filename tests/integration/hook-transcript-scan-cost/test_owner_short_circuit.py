"""The owner short-circuit and the snapshot paths around it (design.md
success criteria: "With a bypass snapshot, a PreToolUse reads 0
transcript bytes and is allowed"; "With a missing, corrupt, undecodable,
non-dict or no-bypass snapshot, or a failing snapshot read, the verdicts
equal the old path's"; "merge_snapshots always yields a bool
bypass_safety"; entrypoints.md ``_readable_snapshot`` and documented
difference 4).

Expected verdicts come from the frozen 166a3f6 ``decide`` (the whole
old function, not just its scans), with any ``read_snapshot`` patch
applied identically to both modules.
"""

from __future__ import annotations

import builtins
import importlib.util
import sys
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
TR = cc(kit.tool_result("contents"))
TR_BLOCK = cc(kit.tool_result(kit.BLOCK_TEXT, is_error=True))
DEEP_USER = (b'{"type":"user","message":{"content":' + b"[" * 100_000
             + b"]" * 100_000 + b"}}")


def _builtin(name):
    pol = policy.load_policy(Path("/nonexistent/p.yaml"),
                             Path("/nonexistent/p.d"))
    return pol.get_role(name)


def _transcripts(tmp_path):
    t = tmp_path / "tx"
    t.mkdir(exist_ok=True)

    def mk(name, lines):
        return str(kit.write_lines(t / name, lines))
    return {
        "terminal": mk("terminal.jsonl", [TG, TR, TERM, TR]),
        "telegram": mk("telegram.jsonl", [TERM, TG, TR, TR]),
        "telegram_halted": mk("halted.jsonl", [TERM, TG, TR_BLOCK, TR]),
        "raising_non_object": mk("raise.jsonl", [TG, TR, b"[1]"]),
        "raising_deep_user": mk("deep.jsonl", [TG, DEEP_USER]),
        "missing": str(t / "missing.jsonl"),
        "directory": str(t),
        "none": None,
    }


def _open_spy(monkeypatch, watched):
    """Count opens and bytes read of any watched path."""
    stats = {"opens": 0, "bytes": 0}
    real_open = builtins.open
    watched = {str(w) for w in watched if w}

    class _F:
        def __init__(self, f):
            self._f = f

        def read(self, *a):
            d = self._f.read(*a)
            stats["bytes"] += len(d)
            return d

        def __enter__(self):
            self._f.__enter__()
            return self

        def __exit__(self, *a):
            return self._f.__exit__(*a)

        def __getattr__(self, n):
            return getattr(self._f, n)

    def spy(path, *a, **k):
        if str(path) in watched or (isinstance(path, bytes)
                                    and path.decode() in watched):
            stats["opens"] += 1
            return _F(real_open(path, *a, **k))
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    return stats


def _forbid_iterators(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("transcript iterator called on an owner turn")
    monkeypatch.setattr(enforce, "_iter_raw_lines_reversed", boom)
    monkeypatch.setattr(enforce, "_iter_lines_reversed", boom)


# ===========================================================================
# 7(i): a bypass snapshot reads 0 transcript bytes and allows
# ===========================================================================

BYPASS_VALUES = [True, 1, "yes", [1], {"x": 1}]
TX_KINDS = ["terminal", "telegram", "telegram_halted", "raising_non_object",
            "raising_deep_user", "missing", "directory"]


@pytest.mark.parametrize("bypass", BYPASS_VALUES, ids=repr)
@pytest.mark.parametrize("kind", TX_KINDS)
def test_bypass_snapshot_allows(tmp_path, monkeypatch, kind, bypass):
    tx = _transcripts(tmp_path)[kind]
    monkeypatch.setattr(enforce, "read_snapshot",
                        lambda s: {"bypass_safety": bypass})
    assert enforce.decide(kit.pretool(tx, "Bash", {"command": "rm -rf /"})) \
        is None


@pytest.mark.parametrize("bypass", BYPASS_VALUES, ids=repr)
@pytest.mark.parametrize("kind", TX_KINDS)
def test_bypass_snapshot_reads_zero_transcript_bytes(tmp_path, monkeypatch,
                                                     kind, bypass):
    tx = _transcripts(tmp_path)[kind]
    monkeypatch.setattr(enforce, "read_snapshot",
                        lambda s: {"bypass_safety": bypass})
    stats = _open_spy(monkeypatch, [tx])
    enforce.decide(kit.pretool(tx, "Read", {"file_path": "~/.claude/x"}))
    assert stats == {"opens": 0, "bytes": 0}


@pytest.mark.parametrize("kind", TX_KINDS)
def test_bypass_snapshot_never_calls_an_iterator(tmp_path, monkeypatch, kind):
    tx = _transcripts(tmp_path)[kind]
    monkeypatch.setattr(enforce, "read_snapshot",
                        lambda s: {"bypass_safety": True})
    _forbid_iterators(monkeypatch)
    assert enforce.decide(kit.pretool(tx, "Bash", {"command": "ls"})) is None


def test_real_owner_snapshot_file_reads_zero_transcript_bytes(tmp_path,
                                                              monkeypatch,
                                                              snap_file):
    """No read_snapshot patch: the owner role written through the real
    (conftest-redirected) snapshot path."""
    _install(snap_file, "role_owner")
    tx = _transcripts(tmp_path)["telegram_halted"]
    stats = _open_spy(monkeypatch, [tx])
    verdict = enforce.decide(kit.pretool(tx, "Bash", {"command": "ls"}))
    assert (verdict, stats["bytes"]) == (None, 0)


def test_bypass_on_a_huge_transcript_reads_nothing(tmp_path, monkeypatch):
    p = tmp_path / "huge.jsonl"
    with open(p, "wb") as f:
        f.write(TG + b"\n")
        f.write((TR + b"\n") * 60_000)
    monkeypatch.setattr(enforce, "read_snapshot",
                        lambda s: {"bypass_safety": True})
    stats = _open_spy(monkeypatch, [p])
    enforce.decide(kit.pretool(p))
    assert stats["bytes"] == 0


def test_bypass_with_nul_in_transcript_path_allows(tmp_path, monkeypatch):
    monkeypatch.setattr(enforce, "read_snapshot",
                        lambda s: {"bypass_safety": True})
    assert enforce.decide(kit.pretool(str(tmp_path / "a\x00b"))) is None


def test_monkeypatched_read_snapshot_is_honoured_over_the_file(
        tmp_path, monkeypatch, snap_file):
    """The file says user role; the module-level patch says owner: the
    short-circuit follows ``enforce.read_snapshot`` at call time."""
    _install(snap_file, "role_user")
    tx = _transcripts(tmp_path)["telegram_halted"]
    monkeypatch.setattr(enforce, "read_snapshot",
                        lambda s: {"bypass_safety": True})
    stats = _open_spy(monkeypatch, [tx])
    verdict = enforce.decide(kit.pretool(tx, "Bash", {"command": "ls"}))
    assert (verdict, stats["opens"]) == (None, 0)


def test_patched_read_snapshot_returning_none_disables_the_owner_file(
        tmp_path, monkeypatch, old, snap_file):
    """The file says owner but ``enforce.read_snapshot`` is patched to
    None: no short-circuit; the verdict is the old one (floor, halted)."""
    _install(snap_file, "role_owner")
    tx = _transcripts(tmp_path)["telegram_halted"]
    monkeypatch.setattr(enforce, "read_snapshot", lambda s: None)
    monkeypatch.setattr(old, "read_snapshot", lambda s: None)
    d = kit.pretool(tx, "Bash", {"command": "ls"})
    assert (enforce.decide(dict(d)), old.decide(dict(d))) == (
        {"tool": "Bash", "reason": kit.HALT_REASON},) * 2


@pytest.mark.parametrize("event", ["PostToolUse", "UserPromptSubmit", "Stop",
                                   None, ""])
def test_other_events_allow_without_reading(tmp_path, monkeypatch, event):
    tx = _transcripts(tmp_path)["telegram_halted"]
    stats = _open_spy(monkeypatch, [tx])
    d = kit.pretool(tx, "Bash", {"command": "ls"})
    d["hook_event_name"] = event
    assert (enforce.decide(d), stats["opens"]) == (None, 0)


# ===========================================================================
# Documented difference 4: truthy non-True bypass with a raising transcript
# ===========================================================================

@pytest.mark.parametrize("bypass", [1, "yes", [1]], ids=repr)
@pytest.mark.parametrize("kind", ["raising_non_object", "raising_deep_user"])
def test_truthy_non_true_bypass_allows_where_166a3f6_denied(
        tmp_path, monkeypatch, old, kind, bypass):
    tx = _transcripts(tmp_path)[kind]
    for mod in (enforce, old):
        monkeypatch.setattr(mod, "read_snapshot",
                            lambda s: {"bypass_safety": bypass})
    d = kit.pretool(tx, "Bash", {"command": "ls"})
    assert (enforce.decide(dict(d)), old.decide(dict(d))) == (
        None, {"tool": "Bash", "reason": kit.FAIL_CLOSED_REASON})


# ===========================================================================
# 7(ii): every non-bypass snapshot shape gives the old verdict
# ===========================================================================

SNAP_FILES = {
    "missing": None,
    "corrupt_json": b"{",
    "undecodable": b"\xff\xfe",
    "json_null": b"null",
    "json_list": b"[1]",
    "json_int": b"3",
    "json_str": b'"x"',
    "json_true": b"true",
    "empty_dict": b"{}",
    "bypass_false": b'{"bypass_safety": false}',
    "bypass_zero": b'{"bypass_safety": 0}',
    "bypass_null": b'{"bypass_safety": null}',
    "bypass_empty_str": b'{"bypass_safety": ""}',
    "empty_file": b"",
    "role_user": "user",
    "role_read_only": "read_only",
    "role_owner": "owner",
}
CALLS = {
    "read_claude": ("Read", {"file_path": "~/.claude/x"}),
    "bash_ls": ("Bash", {"command": "ls"}),
    "read_in_project": ("Read", {"file_path": "/home/u/work/proj/a.txt"}),
}
CROSS_TX = ["terminal", "telegram", "telegram_halted", "raising_non_object",
            "raising_deep_user", "missing", "directory", "none"]


def _install(snap_file, kind):
    p = snap_file(kit.SESSION)
    val = SNAP_FILES[kind]
    if val is None:
        if p.exists():
            p.unlink()
    elif isinstance(val, str):
        ps.write_merged_snapshot(
            kit.SESSION, ps.resolve_snapshot(_builtin(val), None, None))
    else:
        p.write_bytes(val)


@pytest.mark.parametrize("call", sorted(CALLS))
@pytest.mark.parametrize("tx_kind", CROSS_TX)
@pytest.mark.parametrize("snap", sorted(SNAP_FILES))
def test_snapshot_shape_verdict_equals_166a3f6(tmp_path, old, snap_file, snap,
                                               tx_kind, call):
    _install(snap_file, snap)
    tx = _transcripts(tmp_path)[tx_kind]
    tool, inp = CALLS[call]
    d = kit.pretool(tx, tool, dict(inp))
    assert enforce.decide(dict(d)) == old.decide(dict(d))


READ_ERRORS = [OSError("gone"), PermissionError("no"), ValueError("bad"),
               UnicodeDecodeError("utf-8", b"\xff", 0, 1, "x"),
               RuntimeError("boom"), TypeError("t"), RecursionError("deep")]


@pytest.mark.parametrize("call", sorted(CALLS))
@pytest.mark.parametrize("tx_kind", CROSS_TX)
@pytest.mark.parametrize("exc", READ_ERRORS, ids=lambda e: type(e).__name__)
def test_failing_snapshot_read_verdict_equals_166a3f6(tmp_path, old,
                                                      monkeypatch, exc,
                                                      tx_kind, call):
    def boom(s):
        raise exc
    for mod in (enforce, old):
        monkeypatch.setattr(mod, "read_snapshot", boom)
    tx = _transcripts(tmp_path)[tx_kind]
    tool, inp = CALLS[call]
    d = kit.pretool(tx, tool, dict(inp))
    assert enforce.decide(dict(d)) == old.decide(dict(d))


# ---- anchors: the comparison above is not vacuous ------------------------

@pytest.mark.parametrize("snap,tx_kind,call,want", [
    ("undecodable", "terminal", "bash_ls", None),
    ("undecodable", "telegram", "bash_ls",
     {"tool": "Bash", "reason": kit.FAIL_CLOSED_REASON}),
    ("json_list", "terminal", "bash_ls", None),
    ("json_list", "telegram", "read_in_project",
     {"tool": "Read", "reason": kit.FAIL_CLOSED_REASON}),
    ("missing", "telegram_halted", "read_in_project",
     {"tool": "Read", "reason": kit.HALT_REASON}),
    ("role_owner", "telegram_halted", "bash_ls", None),
    ("role_owner", "raising_non_object", "bash_ls", None),
    ("role_user", "raising_non_object", "read_in_project",
     {"tool": "Read", "reason": kit.FAIL_CLOSED_REASON}),
    ("corrupt_json", "terminal", "read_claude", None),
])
def test_snapshot_shape_anchor(tmp_path, snap_file, snap, tx_kind, call, want):
    _install(snap_file, snap)
    tx = _transcripts(tmp_path)[tx_kind]
    tool, inp = CALLS[call]
    assert enforce.decide(kit.pretool(tx, tool, dict(inp))) == want


def test_role_user_denies_claude_dir_read_on_telegram(tmp_path, snap_file):
    _install(snap_file, "role_user")
    tx = _transcripts(tmp_path)["telegram"]
    verdict = enforce.decide(kit.pretool(tx, "Read",
                                         {"file_path": "~/.claude/x"}))
    assert verdict and verdict["reason"] != kit.FAIL_CLOSED_REASON


@pytest.mark.parametrize("raw_session", ["__missing__", None, "", 123,
                                         "../../etc/x", "a\x00b",
                                         kit.SESSION])
@pytest.mark.parametrize("tx_kind", ["terminal", "telegram"])
def test_odd_session_values_equal_166a3f6(tmp_path, old, snap_file,
                                          raw_session, tx_kind):
    _install(snap_file, "role_owner")
    tx = _transcripts(tmp_path)[tx_kind]
    d = kit.pretool(tx, "Bash", {"command": "ls"})
    if raw_session == "__missing__":
        del d["session"]
    else:
        d["session"] = raw_session
    assert enforce.decide(dict(d)) == old.decide(dict(d))


def test_short_circuit_reads_the_snapshot_named_by_the_session_key(
        tmp_path, monkeypatch):
    """design.md: ``data.get("session", "")``, not fail_closed's
    ``str(... or "")``: with ``session=None`` the snapshot read is asked
    for ``None``."""
    seen = []

    def rec(s):
        seen.append(s)
        return {"bypass_safety": True}
    monkeypatch.setattr(enforce, "read_snapshot", rec)
    d = kit.pretool(_transcripts(tmp_path)["telegram"])
    d["session"] = None
    enforce.decide(d)
    assert seen[:1] == [None]


def test_short_circuit_defaults_a_missing_session_to_empty(tmp_path,
                                                           monkeypatch):
    seen = []

    def rec(s):
        seen.append(s)
        return {"bypass_safety": True}
    monkeypatch.setattr(enforce, "read_snapshot", rec)
    d = kit.pretool(_transcripts(tmp_path)["telegram"])
    del d["session"]
    enforce.decide(d)
    assert seen[:1] == [""]


# ===========================================================================
# _readable_snapshot
# ===========================================================================

@pytest.mark.parametrize("value", [{"a": 1}, {}, {"bypass_safety": True}],
                         ids=repr)
def test_readable_snapshot_returns_a_dict(monkeypatch, value):
    monkeypatch.setattr(enforce, "read_snapshot", lambda s: value)
    assert enforce._readable_snapshot("x") == value


@pytest.mark.parametrize("value", [None, [1], [], 3, 0, "x", True, 1.5],
                         ids=repr)
def test_readable_snapshot_rejects_non_dicts(monkeypatch, value):
    monkeypatch.setattr(enforce, "read_snapshot", lambda s: value)
    assert enforce._readable_snapshot("x") is None


@pytest.mark.parametrize("exc", READ_ERRORS + [KeyError("k"),
                                               AttributeError("a"),
                                               MemoryError()],
                         ids=lambda e: type(e).__name__)
def test_readable_snapshot_never_raises(monkeypatch, exc):
    def boom(s):
        raise exc
    monkeypatch.setattr(enforce, "read_snapshot", boom)
    assert enforce._readable_snapshot("x") is None


@pytest.mark.parametrize("snap", sorted(k for k, v in SNAP_FILES.items()
                                        if not isinstance(v, str)))
def test_readable_snapshot_on_real_files(snap_file, snap):
    _install(snap_file, snap)
    got = enforce._readable_snapshot(kit.SESSION)
    want = {"empty_dict": {}, "bypass_false": {"bypass_safety": False},
            "bypass_zero": {"bypass_safety": 0},
            "bypass_null": {"bypass_safety": None},
            "bypass_empty_str": {"bypass_safety": ""}}.get(snap)
    assert got == want


def test_readable_snapshot_reads_the_real_owner_file(snap_file):
    _install(snap_file, "role_owner")
    assert enforce._readable_snapshot(kit.SESSION)["bypass_safety"] is True


def test_readable_snapshot_passes_the_session_through(monkeypatch):
    seen = []
    monkeypatch.setattr(enforce, "read_snapshot",
                        lambda s: seen.append(s) or None)
    enforce._readable_snapshot("claude-scancost.guard")
    assert seen == ["claude-scancost.guard"]


# ===========================================================================
# fail_closed unchanged
# ===========================================================================

@pytest.mark.parametrize("snap", [{"bypass_safety": True},
                                  {"bypass_safety": 1},
                                  {"bypass_safety": "yes"},
                                  {}, None, [1]], ids=repr)
def test_fail_closed_equals_166a3f6(monkeypatch, old, snap):
    for mod in (enforce, old):
        monkeypatch.setattr(mod, "read_snapshot", lambda s: snap)
    d = {"tool_name": "Bash", "session": kit.SESSION}
    assert enforce.fail_closed(d) == old.fail_closed(d)


def test_fail_closed_denies_a_truthy_non_true_bypass(monkeypatch):
    monkeypatch.setattr(enforce, "read_snapshot",
                        lambda s: {"bypass_safety": 1})
    assert enforce.fail_closed({"tool_name": "Bash"}) == {
        "tool": "Bash", "reason": kit.FAIL_CLOSED_REASON}


# ===========================================================================
# 7(iii): merge_snapshots always yields a bool bypass_safety
# ===========================================================================

def _note(bypass="__absent__"):
    n = {"allow_tools": [], "deny_tools": [], "deny_paths_no_access": [],
         "deny_paths_no_write": [], "deny_bash_patterns": [],
         "queued_at": 1.0, "style_text": "", "reply_context": ""}
    if bypass != "__absent__":
        n["bypass_safety"] = bypass
    return n


@pytest.mark.parametrize("notes", [
    [], [_note(True)], [_note(True), _note(False)], [_note(1)],
    [_note("yes")], [_note(None)], [_note()], [_note(True), _note(1)],
    [_note(0)], [_note([1])],
], ids=["empty", "owner", "owner+user", "one", "yes", "none", "absent",
        "owner+one", "zero", "list"])
def test_merge_snapshots_bypass_is_bool(notes):
    assert type(ps.merge_snapshots(notes)["bypass_safety"]) is bool
