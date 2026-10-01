"""Iteration 2: the two gate behaviours closed after review-1, tested
black-box against the frozen 166a3f6 scans.

design.md, "Orchestrator decision after review-1":
1. the sticky scan's third gate word is "ipager", because Python repr of a
   non-str tool_result piece can supply the "a" of "aipager" (``\\x9a`` +
   ``ipager``);
2. a user line whose first ``"message"`` byte run follows a backslash is
   parsed (``"x\\"message": {...}`` is a key named ``x"message``, not the
   entry's own message).
Both shapes are outside the documented divergence classes
(entrypoints.md), so each must give exactly the 166a3f6 result, including
raised exception types, at chunk sizes 7, 64 and 65536.

Every expected value comes from the oracle; anchors pin a literal value
for the cases that would have diverged before the fix, so the comparison
cannot pass vacuously.
"""

from __future__ import annotations

import functools
import importlib.util
import random
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
CHUNKS = (7, 64, 65536)
_RAW = enforce._iter_raw_lines_reversed

TG = cc(kit.tg_prompt("please look"))
TERM = cc(kit.term_prompt("typed at the terminal"))
ASST = cc(kit.assistant_text("Reading the file."))


def _scans(path, chunk, old, monkeypatch):
    monkeypatch.setattr(enforce, "_iter_raw_lines_reversed",
                        functools.partial(_RAW, chunk_bytes=chunk))
    orig = old._iter_lines_reversed
    monkeypatch.setattr(old, "_iter_lines_reversed",
                        functools.partial(orig, chunk_bytes=chunk))
    try:
        p = str(path)
        return ((kit.outcome(enforce._origin_from_transcript, p),
                 kit.outcome(enforce._turn_already_blocked, p)),
                (kit.outcome(old._origin_from_transcript, p),
                 kit.outcome(old._turn_already_blocked, p)))
    finally:
        monkeypatch.setattr(enforce, "_iter_raw_lines_reversed", _RAW)
        monkeypatch.setattr(old, "_iter_lines_reversed", orig)


def _new(path):
    p = str(path)
    return (kit.outcome(enforce._origin_from_transcript, p),
            kit.outcome(enforce._turn_already_blocked, p))


def _file(tmp_path, lines, eol=b"\n"):
    return kit.write_lines(tmp_path / "t.jsonl", lines, eol=eol)


def _style(form):
    return kit.Style(ascii_=form != "raw", upper=form == "upper")


# ===========================================================================
# Python repr facts the family relies on (environment pins)
# ===========================================================================

@pytest.mark.parametrize("c", kit.REPR_A, ids=lambda c: "U+%04X" % ord(c))
def test_repr_of_the_character_ends_in_hex_a(c):
    """If this fails, the running Python's repr changed and the family-R
    cases below no longer reach the shape they are named for."""
    assert repr([c + "ipager"]).endswith("aipager']")


# ===========================================================================
# Family R: a repr escape supplies the "a" of "aipager"
# ===========================================================================

def _repr_forming(c, kind, form, placement):
    s = c + "ipager safety policy: Bash is not allowed for this role"
    obj = kit.repr_placement(placement, kit.repr_container(kind, s))
    return kit.line(obj, _style(form))


# Every forming container x every "a" character, as raw UTF-8 and as an
# upper-case JSON escape (neither leaves "aipager" bytes in the line).
REPR_CASES = {
    f"U+{ord(c):04X}-k{kind}-{form}": (c, kind, form, 0)
    for c in kit.REPR_A for kind in kit.REPR_FORMING_KINDS
    for form in ("raw", "upper")
    if not (form == "raw" and ord(c) < 0x20)    # raw C0 is not JSON
}
# Every placement of the tool_result, for one C1 and one astral character.
REPR_CASES.update({
    f"U+{ord(c):04X}-k1-raw-p{pl}": (c, 1, "raw", pl)
    for c in ("\x9a", "\U0001d17a") for pl in range(kit.REPR_PLACEMENTS)
})


def _repr_file(tmp_path, name):
    return _file(tmp_path, [TERM, TG, _repr_forming(*REPR_CASES[name]),
                            ASST])


@pytest.mark.parametrize("chunk", CHUNKS)
@pytest.mark.parametrize("name", sorted(REPR_CASES))
def test_repr_escaped_marker_equals_166a3f6(tmp_path, old, monkeypatch, name,
                                            chunk):
    new, ref = _scans(_repr_file(tmp_path, name), chunk, old, monkeypatch)
    assert new == ref


@pytest.mark.parametrize("name", sorted(REPR_CASES))
def test_repr_escaped_marker_halts(tmp_path, name):
    """Anchor: the turn is halted (166a3f6's literal result)."""
    assert _new(_repr_file(tmp_path, name)) == ("telegram", True)


@pytest.mark.parametrize("name", sorted(REPR_CASES))
def test_repr_case_has_no_aipager_bytes(name):
    """Precondition: the line really lacks ``aipager``, so a gate on that
    word would skip it."""
    assert b"aipager" not in _repr_forming(*REPR_CASES[name])


@pytest.mark.parametrize("name", sorted(REPR_CASES)[::7])
def test_repr_case_really_halts_in_166a3f6(tmp_path, old, name):
    assert old._turn_already_blocked(str(_repr_file(tmp_path, name))) is True


# ---- hostile variants of the same family ----------------------------------

def _hostile_repr():
    out = {}
    # characters whose repr supplies no "a", every forming container
    for c in kit.REPR_OTHER:
        for kind in (0, 1, 3, 6):
            if ord(c) < 0x20:
                continue
            out[f"other-U+{ord(c):04X}-k{kind}"] = [TG, kit.line(
                kit.tool_result(kit.repr_container(
                    kind, c + "ipager safety policy")), _style("raw"))]
    # lower-case JSON escapes (they DO put "aipager" bytes in the line)
    for c in kit.REPR_A:
        out[f"lower-U+{ord(c):04X}"] = [TG, kit.line(
            kit.tool_result([[c + "ipager safety policy"]]),
            _style("lower"))]
    # a raw C0 control inside a string: JSON rejects the line
    out["raw-c0-x1a"] = [TG, cc(kit.tool_result([["X"]])).replace(
        b"X", b"\x1aipager safety policy")]
    out["raw-c0-x1a-after-tg"] = [TERM, TG, cc(kit.tool_result(
        [["X"]])).replace(b"X", b"\x1aipager safety policy"), ASST]
    # the marker's tail variants (one word short, double space, case)
    for i, tail in enumerate(kit.REPR_TAILS):
        for kind in (0, 3, 10):
            out[f"tail{i}-k{kind}"] = [TG, kit.line(kit.tool_result(
                kit.repr_container(kind, "\x9a" + tail)), _style("raw"))]
    # str pieces keep the character raw (no repr): no "a" supplied
    for kind in (9, 10, 11):
        out[f"str-piece-k{kind}"] = [TG, kit.line(kit.tool_result(
            kit.repr_container(kind, "\x9aipager safety policy")),
            _style("raw"))]
    # the escape combined with the marker split across pieces
    for kind in range(kit.REPR_SPLIT_KINDS):
        for c in ("\x9a", "\u202a", "\U0010fffa"):
            out[f"split{kind}-U+{ord(c):04X}"] = [TG, kit.line(
                kit.tool_result(kit.repr_split_content(kind, c)),
                _style("raw"))]
    # the escaped piece in the second of two tool_result blocks
    out["second-block"] = [TG, kit.line(kit.envelope("user",
        kit.tool_result_msg("aipager", blocks=[{
            "type": "tool_result", "content": [["\x9aipager safety "
                                               "policy"]]}])),
        _style("raw"))]
    # several escapes in a row, and a backslash literal before the char
    out["two-escapes"] = [TG, kit.line(kit.tool_result(
        [["\x9a\x9aipager safety policy"]]), _style("raw"))]
    out["literal-backslash-x9a"] = [TG, kit.line(kit.tool_result(
        [["\\x9aipager safety policy"]]), _style("raw"))]
    out["literal-text-x9a"] = [TG, kit.line(kit.tool_result(
        [["x9aipager safety policy"]]), _style("raw"))]
    # the repr line is OLDER than the governing prompt: not this turn
    out["before-prompt"] = [TERM, kit.line(kit.tool_result(
        [["\x9aipager safety policy"]]), _style("raw")), TG, ASST]
    # a repr line, then a continuation, then the prompt
    out["across-continuation"] = [TG, kit.line(kit.tool_result(
        [["\x9aipager safety policy"]]), _style("raw")),
        cc(kit.envelope("user", kit.prompt_msg(kit.TASK_NOTE))), ASST]
    # CRLF line ends with the escape at the very end of the piece
    out["crlf"] = [TG, kit.line(kit.tool_result(
        [["\x9aipager safety policy"]]), _style("raw"))]
    # a repr marker inside a candidate that raises (non-dict message)
    out["user-msg-str-with-repr"] = [TG, kit.line(
        {"type": "user", "message": "x", "content": [{
            "type": "tool_result", "content": [["\x9aipager safety "
                                               "policy"]]}]},
        _style("raw"))]
    out["system-msg-str-with-repr"] = [TG, kit.line(
        {"type": "system", "message": "\x9aipager safety policy",
         "content": [[1]]}, _style("raw"))]
    # non-object line carrying the words
    out["non-object-with-words"] = [TG, kit.line(
        [["\x9aipager safety policy"]], _style("raw"))]
    # invalid UTF-8 right before ipager (decodes to U+FFFD, printable)
    out["bad-utf8-before"] = [TG, cc(kit.tool_result([["X"]])).replace(
        b"X", b"\xc2ipager safety policy")]
    out["bad-utf8-lone-c2"] = [TG, cc(kit.tool_result([["X"]])).replace(
        b"X", b"\x9aipager safety policy")]
    return out


HOSTILE_REPR = _hostile_repr()


@pytest.mark.parametrize("chunk", CHUNKS)
@pytest.mark.parametrize("name", sorted(HOSTILE_REPR))
def test_hostile_repr_shape_equals_166a3f6(tmp_path, old, monkeypatch, name,
                                           chunk):
    eol = b"\r\n" if name == "crlf" else b"\n"
    new, ref = _scans(_file(tmp_path, HOSTILE_REPR[name], eol=eol), chunk,
                      old, monkeypatch)
    assert new == ref


REPR_HOSTILE_ANCHORS = {
    # the repr of U+0085 is \x85: "5ipager", no marker
    "other-U+0085-k0": ("telegram", False),
    # a lower-case escape: the bytes hold "aipager" anyway
    "lower-U+009A": ("telegram", True),
    "raw-c0-x1a-after-tg": ("telegram", False),
    "tail2-k0": ("telegram", False),
    "str-piece-k10": ("telegram", False),
    "split0-U+009A": ("telegram", False),
    "second-block": ("telegram", True),
    "two-escapes": ("telegram", True),
    # the str itself holds "x9aipager", so "aipager" is literal
    "literal-backslash-x9a": ("telegram", True),
    "literal-text-x9a": ("telegram", True),
    "before-prompt": ("telegram", False),
    "across-continuation": ("telegram", True),
    "user-msg-str-with-repr": ("RAISE:AttributeError",) * 2,
    "system-msg-str-with-repr": ("telegram", "RAISE:AttributeError"),
}


@pytest.mark.parametrize("name", sorted(REPR_HOSTILE_ANCHORS))
def test_hostile_repr_anchor(tmp_path, name):
    assert _new(_file(tmp_path, HOSTILE_REPR[name])) == (
        REPR_HOSTILE_ANCHORS[name])


# ===========================================================================
# Family Q: an escaped quote before the first "message" byte run
# ===========================================================================

def _q_before(real):
    # The line before decides the verdict if the family line is skipped,
    # so pick the one that gives a DIFFERENT origin than the real message.
    return TG if real == "term" else TERM


Q_CASES = {
    f"{fake}-{real}-b{n}-{'x' if prefix else 'bare'}": (
        fake, real, n, prefix)
    for fake in kit.Q_FAKE for real in kit.Q_REAL
    for n in range(1, 7) for prefix in ("x", "")
}


def _q_file(tmp_path, name):
    fake, real, n, prefix = Q_CASES[name]
    return _file(tmp_path, [_q_before(real), kit.escaped_key_line(
        fake, real, backslashes=n, prefix=prefix), ASST])


@pytest.mark.parametrize("chunk", CHUNKS)
@pytest.mark.parametrize("name", sorted(Q_CASES))
def test_escaped_quote_key_equals_166a3f6(tmp_path, old, monkeypatch, name,
                                          chunk):
    new, ref = _scans(_q_file(tmp_path, name), chunk, old, monkeypatch)
    assert new == ref


Q_ANCHORS = {
    # valid JSON (odd backslashes): the entry's own message governs
    "tr-tg-b1-x": ("telegram", False),
    "tr-tg-b1-bare": ("telegram", False),
    "tr-tg-b3-x": ("telegram", False),
    "tr-tg-b5-bare": ("telegram", False),
    "tr_noid-tg-b1-x": ("telegram", False),
    "tr_idlast-tg-b1-x": ("telegram", False),
    "tr-term-b1-x": ("terminal", False),
    "tg-tr-b1-x": ("terminal", False),        # the line is a tool result
    "tr-tr_mark-b1-x": ("terminal", True),
    "tr-task-b1-x": ("terminal", False),      # continuation: not a prompt
    # even backslashes: JSON rejects the line, the previous prompt governs
    "tr-tg-b2-x": ("terminal", False),
    "tr-tg-b4-bare": ("terminal", False),
    "tr-tg-b6-x": ("terminal", False),
}


@pytest.mark.parametrize("name", sorted(Q_ANCHORS))
def test_escaped_quote_key_anchor(tmp_path, name):
    assert _new(_q_file(tmp_path, name)) == Q_ANCHORS[name]


@pytest.mark.parametrize("name", ["tr-tg-b1-x", "tr-tg-b3-x",
                                  "tr_noid-tg-b1-bare"])
def test_escaped_quote_case_first_message_run_follows_a_backslash(name):
    """Precondition: the first ``"message"`` run is the fake key's, and
    it is followed by a tool-result layout (what a skip would trust)."""
    fake, real, n, prefix = Q_CASES[name]
    raw = kit.escaped_key_line(fake, real, backslashes=n, prefix=prefix)
    at = raw.find(b'"message"')
    assert (raw[at - 1:at], enforce._TOOL_RESULT_LAYOUT.match(raw, at)
            is not None) == (b"\\", True)


def _hostile_q():
    tr = kit.tool_result_msg("r")
    tgm = kit.prompt_msg(f"{kit.TG_MARKER}\nhi")
    out = {}
    for ws in ("std", "wild"):
        for seed in range(4):
            st = kit.Style(ws=ws, rng=random.Random(seed))
            out[f"ws-{ws}-{seed}"] = [TERM, kit.escaped_key_line(
                "tr", "tg", style=st)]
    out["nested-fake"] = [TERM, kit.escaped_key_line("tr", "tg",
                                                      nested=True)]
    for n in (1, 2, 3):
        out[f"extra-fakes-{n}"] = [TERM, kit.escaped_key_line(
            "tr", "tg", extra_fakes=n)]
    out["fake-after-real"] = [TERM, kit.escaped_key_line("tr", "tg",
                                                         after=True)]
    out["fake-after-real-tr"] = [TG, kit.escaped_key_line("tg", "tr",
                                                          after=True)]
    out["unicode-prefix"] = [TERM, kit.escaped_key_line("tr", "tg",
                                                        prefix="ü")]
    out["unicode-prefix-ascii"] = [TERM, kit.escaped_key_line(
        "tr", "tg", prefix="ü", style=kit.Style(ascii_=True))]
    out["prefix-is-message"] = [TERM, kit.escaped_key_line(
        "tr", "tg", prefix="message")]
    out["prefix-backslash-n"] = [TERM, kit.escaped_key_line(
        "tr", "tg", prefix="\\n")]
    # str values that carry a "message run, ahead of the real key
    for i, v in enumerate(['a "message', '"message', 'say "message": {',
                           '"message":{"role":"user","content":[{"type":'
                           '"tool_result"']):
        out[f"str-value-{i}"] = [TERM, cc(kit.envelope(
            "user", tgm, extra_before={"slug": v}))]
        out[f"str-value-{i}-tr"] = [TG, cc(kit.envelope(
            "user", tr, extra_before={"slug": v}))]
    # the quote written as a JSON escape (\u0022) or the backslash as one
    base = cc(kit.envelope("user", tgm, extra_before={"xQmessage": tr}))
    out["u0022-quote"] = [TERM, base.replace(b'xQmessage"',
                                             b'x\\u0022message"')]
    out["u005c-backslash-invalid"] = [TERM, base.replace(
        b'"xQmessage"', b'"x\\u005c"message"')]
    out["u0022-upper"] = [TERM, base.replace(b'xQmessage"',
                                             b'x\\u0022message"').replace(
        b"u0022", b"U0022")]
    # \\" before message outside any string (invalid), and a raw quote
    out["bare-backslash-outside"] = [TERM, base.replace(
        b'"xQmessage"', b'\\"message"')]
    out["no-backslash-raw-quote"] = [TERM, base.replace(
        b'"xQmessage"', b'"x"message"')]
    # escaped key and a repr-escaped marker in the same line
    out["both-shapes"] = [TG, kit.line(kit.envelope(
        "user", kit.tool_result_msg([["\x9aipager safety policy"]]),
        extra_before={'x"message': tr}), _style("raw"))]
    out["both-shapes-fake-tg"] = [TERM, kit.line(kit.envelope(
        "user", kit.tool_result_msg([["\x9aipager safety policy"]]),
        extra_before={'x"message': tgm}), _style("raw"))]
    # a non-user line with an escaped key holding a user prompt
    out["assistant-with-fake"] = [TG, cc(kit.envelope(
        "assistant", kit.assistant_msg([{"type": "text", "text": "hi"}]),
        extra_before={'x"message': tgm}))]
    # whitespace and CR inside the layout after the escaped key
    out["cr-in-layout"] = [TERM, kit.escaped_key_line(
        "tr", "tg").replace(b'"message":{"role"', b'"message"\r:\t{ "role"',
                            1)]
    return out


HOSTILE_Q = _hostile_q()


@pytest.mark.parametrize("chunk", CHUNKS)
@pytest.mark.parametrize("name", sorted(HOSTILE_Q))
def test_hostile_escaped_quote_equals_166a3f6(tmp_path, old, monkeypatch,
                                              name, chunk):
    new, ref = _scans(_file(tmp_path, HOSTILE_Q[name]), chunk, old,
                      monkeypatch)
    assert new == ref


Q_HOSTILE_ANCHORS = {
    "nested-fake": ("telegram", False),
    "extra-fakes-3": ("telegram", False),
    "unicode-prefix": ("telegram", False),
    "prefix-is-message": ("telegram", False),
    "ws-wild-0": ("telegram", False),
    "u0022-quote": ("telegram", False),
    "u005c-backslash-invalid": ("terminal", False),
    "both-shapes": ("telegram", True),
    "both-shapes-fake-tg": ("terminal", True),
    "cr-in-layout": ("telegram", False),
}


@pytest.mark.parametrize("name", sorted(Q_HOSTILE_ANCHORS))
def test_hostile_escaped_quote_anchor(tmp_path, name):
    assert _new(_file(tmp_path, HOSTILE_Q[name])) == Q_HOSTILE_ANCHORS[name]


# ===========================================================================
# The escape at a chunk boundary
# ===========================================================================

def _aligned_file(tmp_path, before, target, key_at, chunk, delta):
    """``before`` + ``target`` + a whitespace pad line, sized so that a
    reverse chunk boundary falls ``delta`` bytes after byte ``key_at`` of
    ``target`` (the escape's first byte)."""
    base = b"\n".join(before + [target]) + b"\n"
    off = len(base) - 1 - len(target) + key_at
    want = off + delta            # file offset where a chunk must start
    pad = (want - len(base) - 1) % chunk
    data = base + b" " * pad + b"\n"
    assert (len(data) - want) % chunk == 0
    p = tmp_path / "t.jsonl"
    p.write_bytes(data)
    return p


def _boundary_targets():
    raw_r = kit.line(kit.tool_result([["\x9aipager safety policy"]]),
                     _style("raw"))
    up_r = kit.line(kit.tool_result([["\u202aipager safety policy"]]),
                    _style("upper"))
    astral = kit.line(kit.tool_result([["\U0010fffaipager safety policy"]]),
                      _style("raw"))
    q1 = kit.escaped_key_line("tr", "tg")
    q2 = kit.escaped_key_line("tr", "tg", backslashes=2)
    q3 = kit.escaped_key_line("tr", "tg", backslashes=3)
    return {
        "repr-raw-c1": ([TERM, TG], raw_r, raw_r.find(b"\xc2\x9a")),
        "repr-upper-escape": ([TERM, TG], up_r, up_r.find(b"\\u202A")),
        "repr-raw-astral": ([TERM, TG], astral, astral.find(b"\xf4")),
        "q-b1": ([TERM], q1, q1.find(b'\\"message"')),
        "q-b2": ([TERM], q2, q2.find(b'\\\\"message"')),
        "q-b3": ([TERM], q3, q3.find(b'\\\\\\"message"')),
    }


BOUNDARY_TARGETS = _boundary_targets()


@pytest.mark.parametrize("delta", range(-2, 10))
@pytest.mark.parametrize("chunk", CHUNKS)
@pytest.mark.parametrize("name", sorted(BOUNDARY_TARGETS))
def test_escape_at_a_chunk_boundary_equals_166a3f6(tmp_path, old,
                                                   monkeypatch, name, chunk,
                                                   delta):
    before, target, key_at = BOUNDARY_TARGETS[name]
    assert key_at >= 0
    p = _aligned_file(tmp_path, before, target, key_at, chunk, delta)
    new, ref = _scans(p, chunk, old, monkeypatch)
    assert new == ref


BOUNDARY_ANCHORS = {
    "repr-raw-c1": ("telegram", True),
    "repr-upper-escape": ("telegram", True),
    "repr-raw-astral": ("telegram", True),
    "q-b1": ("telegram", False),
    "q-b2": ("terminal", False),
    "q-b3": ("telegram", False),
}


@pytest.mark.parametrize("delta", range(-2, 10))
@pytest.mark.parametrize("chunk", (7, 64))
@pytest.mark.parametrize("name", sorted(BOUNDARY_TARGETS))
def test_escape_at_a_chunk_boundary_anchor(tmp_path, monkeypatch, name,
                                           chunk, delta):
    before, target, key_at = BOUNDARY_TARGETS[name]
    p = _aligned_file(tmp_path, before, target, key_at, chunk, delta)
    monkeypatch.setattr(enforce, "_iter_raw_lines_reversed",
                        functools.partial(_RAW, chunk_bytes=chunk))
    assert _new(p) == BOUNDARY_ANCHORS[name]


# ===========================================================================
# decide() level: the verdicts, through the public entry point
# ===========================================================================

def _builtin(name):
    pol = policy.load_policy(Path("/nonexistent/p.yaml"),
                             Path("/nonexistent/p.d"))
    return pol.get_role(name)


def _install(snap_file, role):
    snap_file(kit.SESSION)
    ps.write_merged_snapshot(kit.SESSION,
                             ps.resolve_snapshot(_builtin(role), None, None))


DECIDE_TX = {
    "repr-halt": [TERM, TG, _repr_forming("\x9a", 0, "raw", 0), ASST],
    "repr-halt-upper": [TERM, TG, _repr_forming("\u202a", 1, "upper", 0)],
    "repr-halt-astral": [TERM, TG, _repr_forming("\U000f000a", 3, "raw", 4)],
    "q-telegram": [TERM, kit.escaped_key_line("tr", "tg"), ASST],
    "q-telegram-b3": [TERM, kit.escaped_key_line("tr_noid", "tg",
                                                 backslashes=3)],
    "q-invalid-b2": [TERM, kit.escaped_key_line("tr", "tg", backslashes=2)],
    "q-terminal": [TG, kit.escaped_key_line("tr", "term")],
}
DECIDE_CALLS = {
    "read-project": ("Read", {"file_path": "/home/u/work/proj/a.txt"}),
    "read-claude": ("Read", {"file_path": "~/.claude/settings.json"}),
    "bash": ("Bash", {"command": "ls"}),
}


@pytest.mark.parametrize("call", sorted(DECIDE_CALLS))
@pytest.mark.parametrize("tx", sorted(DECIDE_TX))
@pytest.mark.parametrize("role", ["user", "read_only"])
def test_decide_on_both_families_equals_166a3f6(tmp_path, old, snap_file,
                                                role, tx, call):
    _install(snap_file, role)
    p = _file(tmp_path, DECIDE_TX[tx])
    tool, inp = DECIDE_CALLS[call]
    d = kit.pretool(p, tool, dict(inp))
    assert enforce.decide(dict(d)) == old.decide(dict(d))


@pytest.mark.parametrize("tx", ["repr-halt", "repr-halt-upper",
                                "repr-halt-astral"])
def test_decide_halts_on_a_repr_escaped_marker(tmp_path, snap_file, tx):
    """An in-project Read the user role allows is still denied: halted."""
    _install(snap_file, "user")
    p = _file(tmp_path, DECIDE_TX[tx])
    d = kit.pretool(p, "Read", {"file_path": "/home/u/work/proj/a.txt"})
    assert (enforce.decide(d) or {}).get("reason") == kit.HALT_REASON


@pytest.mark.parametrize("tx", ["q-telegram", "q-telegram-b3"])
def test_decide_restricts_a_telegram_prompt_behind_an_escaped_key(
        tmp_path, snap_file, tx):
    """Bash is denied for the user role on a Telegram turn; skipping the
    line would make the turn terminal and allow it."""
    _install(snap_file, "user")
    p = _file(tmp_path, DECIDE_TX[tx])
    assert enforce.decide(kit.pretool(p, "Bash", {"command": "ls"})) \
        is not None


def test_decide_allows_bash_when_the_escaped_key_line_is_invalid(
        tmp_path, snap_file):
    """Control: two backslashes make the line invalid JSON, so the earlier
    terminal prompt governs and Bash is allowed (as in 166a3f6)."""
    _install(snap_file, "user")
    p = _file(tmp_path, DECIDE_TX["q-invalid-b2"])
    assert enforce.decide(kit.pretool(p, "Bash", {"command": "ls"})) is None
