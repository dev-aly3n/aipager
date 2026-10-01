"""Hand cases for the two scans, each compared with the frozen 166a3f6
oracle at chunk sizes 7, 64 and 65536 (design.md success criteria:
"The three closed divergences (non-object line, ``\\u``-escaped key,
split marker) give the old result"; "The documented classes give the
new result with no exception"; identical results "including raised
exception types").

Groups:
- closed divergences (must equal the oracle);
- prompt-candidate lines that raise (must raise the same type);
- documented divergence classes 1-3 (pin the NEW behaviour, and show
  the oracle differs, so the pin is not vacuous);
- boundary values of the parse gate (one word short of the marker,
  escapes just inside/outside ``\\u00[2-7]X``);
- hostile shapes Claude Code could plausibly write (error guessing).
"""

from __future__ import annotations

import builtins
import functools
import importlib.util
import json
import sys
from pathlib import Path

import pytest

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
TR = cc(kit.tool_result("file contents"))
TR_BLOCK = cc(kit.tool_result(kit.BLOCK_TEXT, is_error=True))
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


def _file(tmp_path, lines, eol=b"\n"):
    return kit.write_lines(tmp_path / "t.jsonl", lines, eol=eol)


# ===========================================================================
# Closed divergences: new == old, and the old value is anchored literally
# ===========================================================================

_SPLIT_LIST = cc(kit.tool_result([{"type": "text", "text": "aipager"},
                                  {"type": "text", "text": "safety policy"}]))
_SPLIT_BLOCKS = cc(kit.envelope("user", kit.tool_result_msg(
    "aipager safety", blocks=[{"tool_use_id": "toolu_2",
                               "type": "tool_result", "content": "policy"}])))

CLOSED = {
    # A non-object line after a Telegram prompt: old raised AttributeError.
    "non_object_list": ([TG, b"[1,2]"], ("RAISE:AttributeError",) * 2),
    "non_object_str": ([TG, b'"s"'], ("RAISE:AttributeError",) * 2),
    "non_object_int": ([TG, b"3"], ("RAISE:AttributeError",) * 2),
    "non_object_true": ([TG, b"true"], ("RAISE:AttributeError",) * 2),
    "non_object_null": ([TG, b"null"], ("RAISE:AttributeError",) * 2),
    "non_object_spaced": ([TG, b"  [ ]"], ("RAISE:AttributeError",) * 2),
    # A \\u-escaped "type" key on the newest prompt: a terminal prompt.
    "escaped_type_key": (
        [TG, b'{"typ\\u0065":"user","message":{"role":"user",'
             b'"content":"from the terminal"}}'], ("terminal", False)),
    "escaped_user_value": (
        [TG, b'{"type":"\\u0075ser","message":{"role":"user",'
             b'"content":"from the terminal"}}'], ("terminal", False)),
    "escaped_uppercase_hex": (
        [TG, b'{"type":"us\\u0045r","message":{"content":"x"}}'],
        ("telegram", False)),
    "escaped_marker_letter": (
        [TG, cc(kit.tool_result("x")).replace(b'"x"',
         b'"\\u0061ipager safety policy: no"')], ("telegram", True)),
    "escaped_marker_space": (
        [TG, cc(kit.tool_result("x")).replace(b'"x"',
         b'"aipager\\u0020safety policy"')], ("telegram", True)),
    # The marker split across pieces, both shapes.
    "split_marker_list": ([TG, _SPLIT_LIST], ("telegram", True)),
    "split_marker_blocks": ([TG, _SPLIT_BLOCKS], ("telegram", True)),
    "split_marker_bare_pieces": (
        [TG, cc(kit.tool_result(["aipager", "safety", "policy"]))],
        ("telegram", True)),
    "split_marker_text_list_value": (
        [TG, cc(kit.tool_result([{"text": ["aipager safety policy"]}]))],
        ("telegram", True)),
}


@pytest.mark.parametrize("chunk", CHUNKS)
@pytest.mark.parametrize("name", sorted(CLOSED))
def test_closed_divergence_equals_166a3f6(tmp_path, old, monkeypatch, name,
                                          chunk):
    lines, _anchor = CLOSED[name]
    new, ref = _scans(_file(tmp_path, lines), chunk, old, monkeypatch)
    assert new == ref


@pytest.mark.parametrize("name", sorted(CLOSED))
def test_closed_divergence_anchor(tmp_path, monkeypatch, name):
    """The literal expected values, so the comparison above cannot be
    satisfied by a broken oracle."""
    lines, anchor = CLOSED[name]
    p = str(_file(tmp_path, lines))
    got = (kit.outcome(enforce._origin_from_transcript, p),
           kit.outcome(enforce._turn_already_blocked, p))
    assert got == anchor


@pytest.mark.parametrize("name", ["non_object_list", "escaped_type_key",
                                  "split_marker_list", "split_marker_blocks"])
def test_closed_divergence_decide_equals_166a3f6(tmp_path, old, name):
    lines, _ = CLOSED[name]
    p = _file(tmp_path, lines)
    d = kit.pretool(p, "Read", {"file_path": "/home/u/work/proj/a"})
    assert enforce.decide(dict(d)) == old.decide(dict(d))


# ===========================================================================
# Prompt-candidate lines that raise: the same exception type as before
# ===========================================================================

def _deep_user(depth):
    return (b'{"type":"user","message":{"role":"user","content":'
            + b"[" * depth + b'"x"' + b"]" * depth + b"}}")


RAISING = {
    "user_nested_100k": [TG, _deep_user(100_000)],
    "user_message_str": [TG, b'{"type":"user","message":"x"}'],
    "user_message_null": [TG, b'{"type":"user","message":null}'],
    "user_message_list": [TG, b'{"type":"user","message":[1]}'],
    "system_message_str_with_marker":
        [TG, b'{"type":"system","message":"aipager safety policy"}'],
    "assistant_nested_100k_escaped":
        [TG, b'{"type":"assistant","k":"\\u0041","v":'
             + b"[" * 100_000 + b"]" * 100_000 + b"}"],
    "non_object_after_tool_results": [TG, TR, ASST, TR, b"[]"],
}


@pytest.mark.parametrize("chunk", CHUNKS)
@pytest.mark.parametrize("name", sorted(RAISING))
def test_candidate_line_raises_like_166a3f6(tmp_path, old, monkeypatch, name,
                                            chunk):
    lines = RAISING[name]
    if chunk == 7 and sum(map(len, lines)) > 20_000:
        pytest.skip("old reader is quadratic at 7-byte chunks")
    new, ref = _scans(_file(tmp_path, lines), chunk, old, monkeypatch)
    assert new == ref


@pytest.mark.parametrize("name", sorted(RAISING))
def test_candidate_line_really_raises(tmp_path, name):
    """Anchor: at least one scan raises for each candidate line."""
    p = str(_file(tmp_path, RAISING[name]))
    got = (kit.outcome(enforce._origin_from_transcript, p),
           kit.outcome(enforce._turn_already_blocked, p))
    assert any(isinstance(g, str) and g.startswith("RAISE:") for g in got)


@pytest.mark.skipif(not hasattr(sys, "get_int_max_str_digits"),
                    reason="no integer digit limit on this Python")
@pytest.mark.parametrize("chunk", CHUNKS)
def test_user_prompt_with_5000_digit_int_raises_like_166a3f6(
        tmp_path, old, monkeypatch, chunk):
    line = (b'{"type":"user","n":' + b"7" * 5000
            + b',"message":{"content":"x"}}')
    new, ref = _scans(_file(tmp_path, [TG, line]), chunk, old, monkeypatch)
    assert (new, ref[0]) == (ref, "RAISE:ValueError")


def test_candidate_raise_denies_through_fail_closed_like_166a3f6(tmp_path,
                                                                 old):
    p = _file(tmp_path, [TG, b'{"type":"user","message":"x"}'])
    d = kit.pretool(p)
    assert (enforce.decide(dict(d)), old.decide(dict(d))) == (
        {"tool": "Read", "reason": kit.FAIL_CLOSED_REASON},) * 2


# ===========================================================================
# Documented divergence classes: pin the NEW behaviour (no exception)
# ===========================================================================

_NESTED_TR_FIRST = (
    b'{"type":"user","toolUseResult":{"message":{"role":"user","content":'
    b'[{"type":"tool_result","content":"r"}]}},"message":{"role":"user",'
    b'"content":"' + kit.TG_MARKER.encode() + b'\\nnested"}}')
_DUP_MESSAGE = (
    b'{"type":"user","message":{"role":"user","content":[{"tool_use_id":'
    b'"t1","type":"tool_result","content":"r"}]},"message":{"role":"user",'
    b'"content":"' + kit.TG_MARKER.encode() + b'\\ndup"}}')
_DUP_CONTENT = (
    b'{"type":"user","message":{"role":"user","content":[{"type":'
    b'"tool_result","content":"r"}],"content":"'
    + kit.TG_MARKER.encode() + b'\\ndup"}}')
_DUP_BLOCK_TYPE = (
    b'{"type":"user","message":{"role":"user","content":[{"type":'
    b'"tool_result","type":"text","text":"'
    + kit.TG_MARKER.encode() + b'\\ndup"}]}}')
_ASST_DEEP = (b'{"type":"assistant","v":' + b"[" * 100_000 + b"]" * 100_000
              + b"}")
_ASST_BIGINT = b'{"type":"assistant","n":' + b"9" * 5000 + b"}"
_SYS_MSG_X = b'{"type":"system","message":"x"}'
_ASST_DEEP_WORDS = (b'{"type":"assistant","k":"aipager safety policy","v":'
                    + b"[" * 100_000 + b"]" * 100_000 + b"}")

DOCUMENTED = {
    # name: (lines, scan, new value, old value)
    "class1_nested_tool_result_first": (
        [TERM, _NESTED_TR_FIRST], "origin", "terminal", "telegram"),
    "class2_duplicate_message": (
        [TERM, _DUP_MESSAGE], "origin", "terminal", "telegram"),
    "class2_duplicate_content": (
        [TERM, _DUP_CONTENT], "origin", "terminal", "telegram"),
    "class2_duplicate_block_type": (
        [TERM, _DUP_BLOCK_TYPE], "origin", "terminal", "telegram"),
    "class3_assistant_nested_origin": (
        [TG, _ASST_DEEP], "origin", "telegram", "RAISE:RecursionError"),
    "class3_assistant_nested_sticky": (
        [TG, _ASST_DEEP], "sticky", False, "RAISE:RecursionError"),
    # Rule (d) is sticky-only (design 4.3), so in the ORIGIN scan the three
    # marker words do not make a non-user line a candidate. entrypoints.md
    # class 3 says "carries none of the words", which is narrower than
    # this; see test-report issue tester-iter1-001.
    "class3_assistant_nested_with_words_origin": (
        [TG, _ASST_DEEP_WORDS], "origin", "telegram", "RAISE:RecursionError"),
    "class3_system_message_str_sticky": (
        [TG, _SYS_MSG_X], "sticky", False, "RAISE:AttributeError"),
    "class3_system_message_two_words": (
        [TG, b'{"type":"system","message":"aipager safety"}'], "sticky",
        False, "RAISE:AttributeError"),
}
if hasattr(sys, "get_int_max_str_digits"):
    DOCUMENTED["class3_assistant_bigint_origin"] = (
        [TG, _ASST_BIGINT], "origin", "telegram", "RAISE:ValueError")
    DOCUMENTED["class3_assistant_bigint_sticky"] = (
        [TG, _ASST_BIGINT], "sticky", False, "RAISE:ValueError")


def _scan_fn(mod, which):
    return (mod._origin_from_transcript if which == "origin"
            else mod._turn_already_blocked)


@pytest.mark.parametrize("chunk", [64, 65536])
@pytest.mark.parametrize("name", sorted(DOCUMENTED))
def test_documented_class_gives_the_new_result(tmp_path, monkeypatch, name,
                                               chunk):
    lines, which, new_value, _old = DOCUMENTED[name]
    p = _file(tmp_path, lines)
    monkeypatch.setattr(enforce, "_iter_raw_lines_reversed",
                        functools.partial(_RAW, chunk_bytes=chunk))
    assert kit.outcome(_scan_fn(enforce, which), str(p)) == new_value


@pytest.mark.parametrize("name", sorted(DOCUMENTED))
def test_documented_class_really_differs_from_166a3f6(tmp_path, old, name):
    lines, which, _new, old_value = DOCUMENTED[name]
    p = _file(tmp_path, lines)
    assert kit.outcome(_scan_fn(old, which), str(p)) == old_value


def test_class3_at_decide_level_allows_where_166a3f6_denied(tmp_path, old,
                                                            snap_file):
    """An owner-less restricted turn (no snapshot, the floor) on a
    Telegram prompt whose current turn holds a deeply nested assistant
    line: new allows the Read inside the project; old denied it through
    fail_closed."""
    p = _file(tmp_path, [TG, _ASST_DEEP])
    proj = tmp_path / "proj"
    proj.mkdir()
    d = kit.pretool(p, "Read", {"file_path": str(proj / "a.txt")},
                    cwd=str(proj))
    assert (enforce.decide(dict(d)), old.decide(dict(d))) == (
        None, {"tool": "Read", "reason": kit.FAIL_CLOSED_REASON})


@pytest.mark.parametrize("chunk", [64, 65536])
def test_marker_words_make_a_deep_line_a_sticky_candidate(tmp_path, old,
                                                          monkeypatch, chunk):
    """Rule (d): with all three words the sticky scan parses the line and
    raises exactly as 166a3f6 did."""
    p = _file(tmp_path, [TG, _ASST_DEEP_WORDS])
    monkeypatch.setattr(enforce, "_iter_raw_lines_reversed",
                        functools.partial(_RAW, chunk_bytes=chunk))
    assert (kit.outcome(enforce._turn_already_blocked, str(p))
            == kit.outcome(old._turn_already_blocked, str(p))
            == "RAISE:RecursionError")


def test_deep_line_with_words_on_a_telegram_turn_still_denies(tmp_path, old):
    """At decide level the origin skip is caught by the sticky scan on a
    Telegram turn: both versions deny through fail_closed."""
    p = _file(tmp_path, [TG, _ASST_DEEP_WORDS])
    d = kit.pretool(p, "Read", {"file_path": "/home/u/work/proj/a"})
    assert (enforce.decide(dict(d)), old.decide(dict(d))) == (
        {"tool": "Read", "reason": kit.FAIL_CLOSED_REASON},) * 2


def test_deep_line_with_words_on_a_terminal_turn_new_allows(tmp_path, old):
    """The decide-level consequence of the origin-scan skip: a terminal
    prompt followed by a >10,000-deep non-user line holding the three
    marker words. 166a3f6 denied (RecursionError -> fail_closed); the new
    code allows, as the terminal turn it is. Pinned as the design 4.3
    behaviour; it falls under class 3's nesting shape but not under
    entrypoints.md's "none of the words" wording (issue tester-iter1-001).
    """
    p = _file(tmp_path, [TERM, _ASST_DEEP_WORDS])
    d = kit.pretool(p, "Bash", {"command": "ls"})
    assert (enforce.decide(dict(d)), old.decide(dict(d))) == (
        None, {"tool": "Bash", "reason": kit.FAIL_CLOSED_REASON})


# ===========================================================================
# Boundary values of the parse gate
# ===========================================================================

BOUNDARY = {
    # Two of the three marker words on a raising non-user line: skipped
    # (class 3, pinned above); all three: parsed, raises like before.
    "three_words_scattered_non_user_raises": (
        [TG, b'{"type":"system","message":"policy aipager safety"}'],),
    # \\u00XX escapes at the edges of [2-7]X.
    "escape_u001f_in_prompt": (
        [TG, b'{"type":"user","message":{"content":"\\u001fhi"}}'],),
    "escape_u0020_in_tool_result": (
        [TG, cc(kit.tool_result("q")).replace(b'"q"', b'"a\\u0020b"')],),
    "escape_u007f_in_tool_result": (
        [TG, cc(kit.tool_result("q")).replace(b'"q"', b'"a\\u007fb"')],),
    "escape_u0080_in_prompt": (
        [TG, b'{"type":"user","message":{"content":"\\u0080"}}'],),
    "escaped_backslash_then_u": (
        [TG, cc(kit.tool_result("q")).replace(b'"q"',
                                              b'"\\\\u0061ipager"')],),
    # Words of the marker in the toolUseResult only: old never halts.
    "marker_only_in_tool_use_result": (
        [TG, cc(kit.tool_result("ok", tool_use_result={
            "stderr": kit.BLOCK_TEXT}))],),
    # Marker in assistant text: not a tool result.
    "marker_in_assistant_text": ([TG, cc(kit.assistant_text(
        kit.BLOCK_TEXT))],),
    # Marker on a meta / compact entry carrying the Telegram marker.
    "meta_then_terminal": ([TERM, cc(kit.envelope(
        "user", kit.prompt_msg(kit.TG_MARKER + "\nmeta"),
        extra_before={"isMeta": True}))],),
    "compact_with_marker": ([TG, TR_BLOCK, cc(kit.envelope(
        "user", kit.prompt_msg("This session is being continued"),
        extra_before={"isCompactSummary": True}))],),
    "task_notification_keeps_halt": ([TG, TR_BLOCK, cc(kit.envelope(
        "user", kit.prompt_msg(kit.TASK_NOTE)))],),
    "task_notification_indented": ([TERM, cc(kit.envelope(
        "user", kit.prompt_msg("\n \t" + kit.TASK_NOTE)))],),
    "halt_before_prompt_is_prior_turn": ([TG, TR_BLOCK, TERM, TR],),
    "halt_after_prompt": ([TERM, TG, TR, TR_BLOCK, ASST, TR],),
    "tool_result_without_role": ([TG, cc(kit.envelope(
        "user", kit.tool_result_msg(kit.BLOCK_TEXT, role=False)))],),
    "tool_result_type_before_id": ([TERM, TG, cc(kit.envelope(
        "user", kit.tool_result_msg("r", id_first=False)))],),
    "tool_result_is_error_first": ([TERM, TG,
        b'{"type":"user","message":{"role":"user","content":[{"is_error":'
        b'true,"type":"tool_result","content":"r"}]}}'],),
    "tool_use_id_with_escape": ([TERM, TG,
        b'{"type":"user","message":{"role":"user","content":[{"tool_use_id"'
        b':"a\\"b","type":"tool_result","content":"r"}]}}'],),
    "text_block_then_tool_result": ([TERM,
        b'{"type":"user","message":{"role":"user","content":[{"type":"text",'
        b'"text":"' + kit.TG_MARKER.encode() + b'"},{"type":"tool_result",'
        b'"content":"r"}]}}'],),
    "message_value_string_before_key": ([TERM,
        b'{"slug":"message","type":"user","message":{"role":"user",'
        b'"content":"' + kit.TG_MARKER.encode() + b'\\nhi"}}'],),
    "message_empty_object_content_top": ([TG,
        b'{"type":"user","message":{},"content":[{"type":"tool_result",'
        b'"content":"aipager safety policy"}]}'],),
    "non_user_empty_message_top_content_marker": ([TG,
        b'{"type":"system","message":{},"content":[{"type":"tool_result",'
        b'"content":"aipager safety policy"}]}'],),
}


@pytest.mark.parametrize("chunk", CHUNKS)
@pytest.mark.parametrize("name", sorted(BOUNDARY))
def test_parse_gate_boundary_equals_166a3f6(tmp_path, old, monkeypatch, name,
                                            chunk):
    (lines,) = BOUNDARY[name]
    new, ref = _scans(_file(tmp_path, lines), chunk, old, monkeypatch)
    assert new == ref


# ===========================================================================
# Error guessing: shapes Claude Code could plausibly write
# ===========================================================================

def _ws_variants(obj):
    """The same entry with whitespace Claude Code never writes but JSON
    allows, at every token: spaces, tabs, CR."""
    out = []
    for ws in (" ", "\t", "\r", " \t\r "):
        out.append(kit.line(obj, _FixedWS(ws)))
    return out


class _FixedWS(kit.Style):
    def __init__(self, ws):
        super().__init__(ws="wild")
        self._w = ws

    def gap(self):
        return self._w


HOSTILE = {
    "crlf_transcript": ([TG, TR, TR_BLOCK, ASST], b"\r\n"),
    "bom_on_prompt": ([TERM, b"\xef\xbb\xbf" + TG], b"\n"),
    "nbsp_before_prompt": ([TERM, b"\xc2\xa0" + TG], b"\n"),
    "x1c_before_tool_result": ([TG, b"\x1c" + TR_BLOCK], b"\n"),
    "u2028_after_prompt": ([TERM, TG + b"\xe2\x80\xa8"], b"\n"),
    "vt_ff_before_prompt": ([TERM, b"\x0b\x0c" + TG], b"\n"),
    "nul_before_prompt": ([TERM, b"\x00" + TG], b"\n"),
    "invalid_utf8_in_prompt": (
        [TERM, TG.replace(b"please", b"pl\xffease")], b"\n"),
    "invalid_utf8_inside_marker_word": (
        [TG, TR_BLOCK.replace(b"aipager", b"aip\xffager")], b"\n"),
    "truncated_prompt_at_eof": ([TG, TR, TERM[: len(TERM) // 2]], b"\n"),
    "truncated_halt_at_eof": ([TG, TR_BLOCK[:-3]], b"\n"),
    "two_entries_on_one_line": ([TERM, TG + TR], b"\n"),
    "nan_in_prompt": ([TERM, TG[:-1] + b',"n":NaN}'], b"\n"),
    "ansi_escapes_in_tool_result": ([TG, cc(kit.tool_result(
        "\x1b[31maipager safety policy\x1b[0m"))], b"\n"),
    "surrogate_pair_escapes": ([TERM, TG.replace(
        b"please", b"\\ud83d\\ude42")], b"\n"),
    "lone_surrogate_escape": ([TERM, TG.replace(b"please", b"\\ud800")],
                              b"\n"),
    "escaped_slash_in_prompt": ([TERM, TG.replace(b"please",
                                                  b"a\\/b")], b"\n"),
    "marker_with_escaped_slash_words": ([TG, cc(kit.tool_result(
        "aipager safety policy")).replace(b"safety", b"saf\\u0065ty")],
        b"\n"),
    "prompt_with_ensure_ascii": ([TERM, json.dumps(
        kit.tg_prompt("سلام 🙂"), ensure_ascii=True).encode()], b"\n"),
    "prompt_text_quotes_type_user": ([TERM, cc(kit.tool_result(
        '{"type":"user","message":{"content":"[via Telegram"}}'))], b"\n"),
    "prompt_text_mentions_tool_result": ([TERM, cc(kit.tg_prompt(
        '"type":"tool_result" "role":"user" "content":[{'))], b"\n"),
    "empty_lines_everywhere": ([b"", TG, b"", b"   ", TR, b"\t", b""], b"\n"),
    "only_blank_lines": ([b"", b" ", b"\t"], b"\n"),
    "only_tool_results": ([TR, TR_BLOCK, TR], b"\n"),
    "empty_prompt_content": ([TG, cc(kit.term_prompt(""))], b"\n"),
    "list_prompt_marker_in_second_block": ([TERM, cc(kit.envelope(
        "user", kit.prompt_msg([{"type": "text", "text": "first"},
                                {"type": "text", "text": kit.TG_MARKER}])))],
        b"\n"),
    "image_then_text_prompt": ([TERM, cc(kit.envelope("user", kit.prompt_msg(
        [{"type": "image", "source": {"type": "base64", "data": "AA=="}},
         {"type": "text", "text": kit.TG_MARKER + "\nsee pic"}])))], b"\n"),
    "queue_operation_line_with_marker": ([TERM, cc(
        {"type": "queue-operation", "operation": "enqueue",
         "content": kit.TG_MARKER + "\nqueued"})], b"\n"),
    "subagent_sidechain_tool_result": ([TG, cc(kit.tool_result(
        kit.BLOCK_TEXT, extra_before={"isSidechain": True,
                                      "agentId": "a1b2"}))], b"\n"),
}
for _i, _v in enumerate(_ws_variants(kit.tg_prompt("ws"))):
    HOSTILE[f"wild_ws_prompt_{_i}"] = ([TERM, _v], b"\n")
for _i, _v in enumerate(_ws_variants(kit.tool_result(kit.BLOCK_TEXT))):
    HOSTILE[f"wild_ws_halt_{_i}"] = ([TG, _v], b"\n")
for _i, _v in enumerate(_ws_variants(kit.tool_result("plain"))):
    HOSTILE[f"wild_ws_tool_result_{_i}"] = ([TERM, TG, _v], b"\n")


@pytest.mark.parametrize("chunk", CHUNKS)
@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_hostile_shape_equals_166a3f6(tmp_path, old, monkeypatch, name, chunk):
    lines, eol = HOSTILE[name]
    new, ref = _scans(_file(tmp_path, lines, eol), chunk, old, monkeypatch)
    assert new == ref


def test_wild_whitespace_tool_result_is_still_skipped(tmp_path):
    """Anchor for the wild-whitespace cases: a tool result written with
    tabs and CRs between every token after a Telegram prompt still reads
    as telegram (the prompt is found behind it)."""
    p = _file(tmp_path, [TERM, TG, _ws_variants(kit.tool_result("p"))[3]])
    assert enforce._origin_from_transcript(str(p)) == "telegram"


# ---- huge lines --------------------------------------------------------

@pytest.mark.parametrize("which", ["origin", "sticky"])
def test_huge_tool_result_line_equals_166a3f6(tmp_path, old, which):
    big = cc(kit.tool_result("w" * (3 << 20)))
    p = _file(tmp_path, [TERM, TG, big, ASST])
    assert (kit.outcome(_scan_fn(enforce, which), str(p))
            == kit.outcome(_scan_fn(old, which), str(p)))


@pytest.mark.parametrize("which", ["origin", "sticky"])
def test_huge_prompt_line_equals_166a3f6(tmp_path, old, which):
    big = cc(kit.tg_prompt("w" * (3 << 20)))
    p = _file(tmp_path, [TERM, big, TR])
    assert (kit.outcome(_scan_fn(enforce, which), str(p))
            == kit.outcome(_scan_fn(old, which), str(p)))


def test_huge_line_halt_marker_at_the_far_end_is_found(tmp_path):
    big = cc(kit.tool_result("w" * (3 << 20) + " " + kit.BLOCK_TEXT))
    p = _file(tmp_path, [TG, big])
    assert enforce._turn_already_blocked(str(p)) is True


# ---- paths ---------------------------------------------------------------

def _paths(tmp_path):
    d = tmp_path / "adir"
    d.mkdir()
    real = _file(tmp_path, [TG, TR_BLOCK])
    return {
        "none": None, "empty": "", "missing": str(tmp_path / "nope.jsonl"),
        "directory": str(d), "dev_null": "/dev/null",
        "bytes_path": str(real).encode(), "pathlib": real,
        "nul_byte": str(tmp_path / "a\x00b"),
    }


@pytest.mark.parametrize("which", ["origin", "sticky"])
@pytest.mark.parametrize("kind", ["none", "empty", "missing", "directory",
                                  "dev_null", "bytes_path", "pathlib",
                                  "nul_byte"])
def test_path_kind_equals_166a3f6(tmp_path, old, kind, which):
    path = _paths(tmp_path)[kind]
    assert (kit.outcome(_scan_fn(enforce, which), path)
            == kit.outcome(_scan_fn(old, which), path))


@pytest.mark.parametrize("kind,want", [
    ("none", ("telegram", False)), ("empty", ("telegram", False)),
    ("missing", ("telegram", False)), ("directory", ("telegram", False)),
    ("dev_null", ("telegram", False))])
def test_path_kind_anchor(tmp_path, kind, want):
    path = _paths(tmp_path)[kind]
    assert (enforce._origin_from_transcript(path),
            enforce._turn_already_blocked(path)) == want


def test_unreadable_file_is_telegram_and_not_blocked(tmp_path):
    import os
    p = _file(tmp_path, [TERM])
    p.chmod(0)
    try:
        if os.access(p, os.R_OK):
            pytest.skip("running as a user that ignores permissions")
        got = (enforce._origin_from_transcript(str(p)),
               enforce._turn_already_blocked(str(p)))
    finally:
        p.chmod(0o600)
    assert got == ("telegram", False)


# ---- the transcript grows while it is scanned ---------------------------

def _append_after_first_read(monkeypatch, target, extra):
    real_open = builtins.open
    state = {"done": False}

    class _F:
        def __init__(self, f):
            self._f = f

        def read(self, *a):
            d = self._f.read(*a)
            if not state["done"]:
                state["done"] = True
                with real_open(target, "ab") as g:
                    g.write(extra)
            return d

        def __enter__(self):
            self._f.__enter__()
            return self

        def __exit__(self, *a):
            return self._f.__exit__(*a)

        def __getattr__(self, n):
            return getattr(self._f, n)

    def spy(path, *a, **k):
        f = real_open(path, *a, **k)
        return _F(f) if str(path) == str(target) else f

    monkeypatch.setattr(builtins, "open", spy)


@pytest.mark.parametrize("which", ["origin", "sticky"])
@pytest.mark.parametrize("chunk", [64, 65536])
def test_append_during_scan_equals_166a3f6(tmp_path, old, monkeypatch, which,
                                           chunk):
    base = [TG] + [TR, ASST] * 30
    extra = TERM + b"\n" + TR_BLOCK + b"\n"
    results = []
    for mod in (enforce, old):
        p = _file(tmp_path, base)
        with monkeypatch.context() as m:
            _append_after_first_read(m, p, extra)
            if mod is enforce:
                m.setattr(enforce, "_iter_raw_lines_reversed",
                          functools.partial(_RAW, chunk_bytes=chunk))
            else:
                m.setattr(old, "_iter_lines_reversed", functools.partial(
                    old._iter_lines_reversed, chunk_bytes=chunk))
            results.append(kit.outcome(_scan_fn(mod, which), str(p)))
    assert results[0] == results[1]
