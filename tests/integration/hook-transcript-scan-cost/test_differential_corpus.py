"""Seeded differential property test against the frozen 166a3f6 scans
(design.md success criterion 1: identical verdicts "for the whole seeded
corpus (>= 8000 cases) ... at chunk sizes 7, 64 and 65536, including
raised exception types, with 0 mismatches").

This corpus is the Tester's own (independent of the Developer's): lines
shaped like Claude Code's plus hostile variants (random JSON whitespace
including CR and tab, ``\\u00XX``-escaped keys and words, ``\\/``,
ensure_ascii, BOM, NBSP / U+2028 / ``\\x1c`` prefixes, invalid UTF-8,
truncated lines, non-object JSON, NaN, deep-but-legal nesting, odd
``message`` values, split and scattered markers). It never generates
entrypoints.md's documented divergence shapes; ``_scan_kit.
assert_in_contract`` checks every generated line.
"""

from __future__ import annotations

import functools
import random

import pytest

from aipager.dtach import enforce
from aipager import policy, policy_snapshot as ps

import importlib.util
import sys
from pathlib import Path


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

SEED = 20261001
TRIALS = 3600
CHUNKS = (7, 64, 65536)
_RAW = enforce._iter_raw_lines_reversed


def _chunks_for(size: int):
    out = [65536]
    if size <= 40_000:
        out.append(64)
    if size <= 8_000:
        out.append(7)
    return out


def _run_case(monkeypatch, old, path, data, chunk, mismatches):
    old_iter = old._iter_lines_reversed
    monkeypatch.setattr(enforce, "_iter_raw_lines_reversed",
                        functools.partial(_RAW, chunk_bytes=chunk))
    monkeypatch.setattr(old, "_iter_lines_reversed",
                        functools.partial(old_iter, chunk_bytes=chunk))
    try:
        for name in ("_origin_from_transcript", "_turn_already_blocked"):
            a = kit.outcome(getattr(enforce, name), str(path))
            b = kit.outcome(getattr(old, name), str(path))
            if a != b:
                mismatches.append((name, chunk, a, b, data[:400]))
        raw = list(_RAW(path, chunk))
        if raw != (data.split(b"\n")[::-1] if data else []):
            mismatches.append(("raw_iter", chunk, None, None, data[:400]))
        s_new = list(enforce._iter_lines_reversed(path, chunk))
        s_old = list(old_iter(path, chunk))
        if s_new != s_old:
            mismatches.append(("str_iter", chunk, None, None, data[:400]))
    finally:
        monkeypatch.setattr(enforce, "_iter_raw_lines_reversed", _RAW)
        monkeypatch.setattr(old, "_iter_lines_reversed", old_iter)


def test_seeded_corpus_scans_equal_166a3f6(tmp_path, monkeypatch, old):
    rng = random.Random(SEED)
    path = tmp_path / "t.jsonl"
    mismatches: list = []
    compared = 0
    outcomes = {"_origin": set(), "_sticky": set()}
    for _ in range(TRIALS):
        data = kit.corpus_case(rng)
        path.write_bytes(data)
        outcomes["_origin"].add(
            kit.outcome(old._origin_from_transcript, str(path)))
        outcomes["_sticky"].add(
            kit.outcome(old._turn_already_blocked, str(path)))
        for chunk in _chunks_for(len(data)):
            _run_case(monkeypatch, old, path, data, chunk, mismatches)
            compared += 1
    print(f"scan differential: {TRIALS} files, {compared} file-chunk "
          f"cases, {4 * compared} comparisons, {len(mismatches)} mismatches")
    # Non-vacuity: the corpus must reach every verdict and the raising
    # paths of both scans, or "0 mismatches" proves little.
    assert (mismatches[:5], compared >= 8000,
            {"telegram", "terminal", "RAISE:AttributeError"}
            <= outcomes["_origin"],
            {True, False, "RAISE:AttributeError"} <= outcomes["_sticky"]
            ) == ([], True, True, True), (len(mismatches), compared, outcomes)


def test_the_comparison_detects_a_known_divergence(tmp_path, monkeypatch,
                                                  old):
    """Harness self-check: a documented class-2 line (duplicate message
    keys, old: telegram, new: skipped) must register as a mismatch, so
    a silently broken comparison cannot pass the property test."""
    path = tmp_path / "t.jsonl"
    data = (kit.cc(kit.term_prompt("earlier")) + b"\n"
            + b'{"type":"user","message":{"role":"user","content":[{"type":'
              b'"tool_result","content":"r"}]},"message":{"content":"'
            + kit.TG_MARKER.encode() + b'\\nhi"}}\n')
    path.write_bytes(data)
    mismatches: list = []
    _run_case(monkeypatch, old, path, data, 65536, mismatches)
    assert [m[0] for m in mismatches] == ["_origin_from_transcript"]


def test_every_generated_line_is_in_contract():
    """The generator guard itself: run it over a fresh slice of the
    corpus (it raises AssertionError on a documented-divergence shape)."""
    rng = random.Random(SEED + 1)
    n = 0
    for _ in range(500):
        line = kit.corpus_line(rng)
        kit.assert_in_contract(line)
        n += 1
    assert n == 500


def test_the_contract_guard_rejects_a_duplicate_key():
    with pytest.raises(AssertionError):
        kit.assert_in_contract(
            b'{"type":"user","message":{"content":"a"},'
            b'"message":{"content":"b"}}')


def test_the_contract_guard_rejects_a_nested_message_ahead():
    with pytest.raises(AssertionError):
        kit.assert_in_contract(
            b'{"type":"user","x":{"message":{}},"message":{"content":"b"}}')


def test_the_contract_guard_rejects_truthy_non_dict_message_off_user():
    with pytest.raises(AssertionError):
        kit.assert_in_contract(b'{"type":"system","message":"x"}')


# ---- decide()-level differential ----------------------------------------

_SNAPS = ("missing", "user", "read_only", "owner")
_CALLS = (
    ("Read", {"file_path": "/home/u/work/proj/a.txt"}),
    ("Read", {"file_path": "~/.claude/settings.json"}),
    ("Bash", {"command": "ls"}),
    ("Write", {"file_path": "/etc/hosts", "content": "x"}),
)


def _builtin(name):
    pol = policy.load_policy(Path("/nonexistent/p.yaml"),
                             Path("/nonexistent/p.d"))
    return pol.get_role(name)


def _install_snapshot(kind, snap_file):
    p = snap_file(kit.SESSION)
    if kind == "missing":
        if p.exists():
            p.unlink()
        return
    ps.write_merged_snapshot(
        kit.SESSION, ps.resolve_snapshot(_builtin(kind), None, None))


def test_seeded_corpus_decide_equals_166a3f6(tmp_path, old, snap_file):
    rng = random.Random(SEED + 7)
    path = tmp_path / "t.jsonl"
    mismatches = []
    verdicts = set()
    compared = 0
    cases = [kit.corpus_case(rng) for _ in range(250)]
    for kind in _SNAPS:
        _install_snapshot(kind, snap_file)
        for data in cases:
            path.write_bytes(data)
            for tool, inp in _CALLS:
                d = kit.pretool(path, tool, dict(inp))
                a, b = enforce.decide(dict(d)), old.decide(dict(d))
                verdicts.add(None if b is None else b["reason"][:40])
                compared += 1
                if a != b:
                    mismatches.append((kind, tool, a, b, data[:300]))
    assert (mismatches[:5], compared, len(verdicts) >= 4) == (
        [], len(_SNAPS) * 250 * len(_CALLS), True), (len(mismatches),
                                                     verdicts)


def test_the_oracle_is_the_166a3f6_file_byte_for_byte():
    """``git show 166a3f6:aipager/dtach/enforce.py | sha256sum`` taken
    when the oracle was frozen; the oracle file is that text behind a
    3-line banner."""
    import hashlib
    text = (Path(__file__).resolve().parent
            / "_oracle_enforce_166a3f6.py").read_bytes()
    body = b"".join(text.splitlines(keepends=True)[3:])
    assert hashlib.sha256(body).hexdigest() == (
        "7e6f2c0041bae192162761ed12185a0f5be7ef2c0880c7166763654a545530b8")


# ---- iteration 2: families R (repr escape) and Q (escaped quote) ----------

FAMILY_TRIALS = 2400


def _family_case(rng):
    """[a prompt, the family line, 0-3 ordinary lines], in a random order
    of the trailing part, so the family line usually decides a verdict."""
    first = rng.choice([kit.cc(kit.term_prompt("typed")),
                        kit.cc(kit.tg_prompt("from the phone"))])
    fam = kit.family_line(rng)
    tail = [kit.corpus_line(rng) for _ in range(rng.choice([0, 0, 1, 3]))]
    lines = [first, fam] + tail
    for ln in lines:
        kit.assert_in_contract(ln)
    eol = b"\r\n" if rng.random() < 0.1 else b"\n"
    data = eol.join(lines) + (eol if rng.random() < 0.8 else b"")
    return data, fam, [first] + tail, eol


def test_family_corpus_scans_equal_166a3f6(tmp_path, monkeypatch, old):
    """>= 2400 files built around a family-R or family-Q line, compared at
    every chunk size, with witnesses proving the families reached the two
    shapes the iteration-2 fixes are about."""
    rng = random.Random(SEED + 2)
    path = tmp_path / "t.jsonl"
    alt = tmp_path / "alt.jsonl"
    mismatches: list = []
    compared = 0
    witness = {"repr_halt_without_aipager": 0, "escaped_key_decides": 0,
               "escaped_key_multi_backslash": 0}
    for _ in range(FAMILY_TRIALS):
        data, fam, rest, eol = _family_case(rng)
        path.write_bytes(data)
        alt.write_bytes(eol.join(rest) + eol)
        o_old = kit.outcome(old._origin_from_transcript, str(path))
        s_old = kit.outcome(old._turn_already_blocked, str(path))
        o_alt = kit.outcome(old._origin_from_transcript, str(alt))
        s_alt = kit.outcome(old._turn_already_blocked, str(alt))
        if s_old is True and s_alt is not True and b"aipager" not in fam:
            witness["repr_halt_without_aipager"] += 1
        at = fam.find(b'"message"')
        if at > 0 and fam[at - 1:at] == b"\\":
            if o_old != o_alt:
                witness["escaped_key_decides"] += 1
            if fam.count(b"\\\\\"message\"") and o_old == o_alt:
                witness["escaped_key_multi_backslash"] += 1
        for chunk in _chunks_for(len(data)):
            _run_case(monkeypatch, old, path, data, chunk, mismatches)
            compared += 1
    print(f"family differential: {FAMILY_TRIALS} files, {compared} "
          f"file-chunk cases, {len(mismatches)} mismatches, {witness}")
    assert (mismatches[:5], compared >= 6000,
            min(witness.values()) >= 25) == ([], True, True), (
        len(mismatches), compared, witness)


def test_main_corpus_carries_the_iteration_2_families():
    """The general corpus generator now emits both families too."""
    rng = random.Random(SEED)
    seen = {"repr": 0, "escaped_key": 0}
    for _ in range(3000):
        ln = kit.corpus_line(rng)
        if b"ipager" in ln and b"aipager" not in ln:
            seen["repr"] += 1
        at = ln.find(b'"message"')
        if at > 0 and ln[at - 1:at] == b"\\":
            seen["escaped_key"] += 1
    assert min(seen.values()) >= 30, seen
