"""Shared helpers for the hook-transcript-scan-cost black-box tests.

Not a test module (no ``test_`` prefix): loaded by path from each test
module through :func:`load`, because this directory's name is not a
valid Python identifier.

Everything here builds INPUTS (transcript lines shaped like the ones
Claude Code writes, plus hostile variants) and normalises OUTPUTS. No
expected verdict is computed here: the expected value always comes from
the frozen 166a3f6 oracle (``_oracle_enforce_166a3f6.py``) or from a
hand-written literal in the test.
"""

from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

TG_MARKER = "[via Telegram · @bob · role:user]"
BLOCK_TEXT = "aipager safety policy: Bash is not allowed for this role"
HALT_REASON = ("session halted - a prior tool call this turn was blocked "
               "by safety policy; start a new request")
FAIL_CLOSED_REASON = "this tool call could not be checked, so it was denied"
TASK_NOTE = "<task-notification>\n<task-id>ab2ae824</task-id>\nAgent done."
SESSION = "scancost-tst-q7z"   # cannot match a live aipager session name
CLAUDE_SID = "5b1d7363-aaaa-bbbb-cccc-00000000feed"


def load(name: str, filename: str):
    """Import a sibling file by path, once per process."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def oracle():
    return load("_scancost_oracle_166a3f6", "_oracle_enforce_166a3f6.py")


# --------------------------------------------------------------------------
# Outcome normalisation
# --------------------------------------------------------------------------

def outcome(fn, *args):
    """A scan's return value, or ``"RAISE:<type>"``."""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - the type IS the outcome
        return "RAISE:" + type(exc).__name__


# --------------------------------------------------------------------------
# A JSON serialiser with control over whitespace and escapes
# --------------------------------------------------------------------------

_SHORT_ESC = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r",
              "\t": "\\t", "\b": "\\b", "\f": "\\f"}


class Style:
    """How one line is serialised.

    ws: "compact" (JSON.stringify), "std" (``", "``/``": "``) or "wild"
    (random JSON whitespace, including tab and CR, at every legal spot).
    ascii: escape every non-ASCII character as ``\\uXXXX``.
    esc: probability of writing a printable ASCII character of a string
    or key as a ``\\u00XX`` escape (0 for the shapes Claude Code writes).
    slash: write ``/`` as ``\\/``.
    """

    def __init__(self, ws="compact", ascii_=False, esc=0.0, slash=False,
                 rng=None):
        self.ws = ws
        self.ascii = ascii_
        self.esc = esc
        self.slash = slash
        self.rng = rng or random.Random(0)

    @classmethod
    def random(cls, rng):
        return cls(ws=rng.choice(["compact", "compact", "std", "wild"]),
                   ascii_=rng.random() < 0.3,
                   esc=rng.choice([0.0] * 6 + [0.03, 0.4]),
                   slash=rng.random() < 0.1, rng=rng)

    def gap(self) -> str:
        if self.ws == "compact":
            return ""
        if self.ws == "std":
            return ""
        return self.rng.choice(["", "", " ", "\t", "\r", "  ", " \t\r "])

    def after_colon(self) -> str:
        return " " if self.ws == "std" else self.gap()

    def after_comma(self) -> str:
        return " " if self.ws == "std" else self.gap()


def enc_str(s: str, st: Style) -> str:
    if not st.esc and not st.slash:
        return json.dumps(s, ensure_ascii=st.ascii)
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch in _SHORT_ESC:
            out.append(_SHORT_ESC[ch])
        elif o < 0x20:
            out.append("\\u%04x" % o)
        elif ch == "/" and st.slash:
            out.append("\\/")
        elif o >= 0x7F and (st.ascii or 0xD800 <= o <= 0xDFFF):
            if o > 0xFFFF:
                v = o - 0x10000
                out.append("\\u%04x\\u%04x" % (0xD800 + (v >> 10),
                                               0xDC00 + (v & 0x3FF)))
            else:
                out.append("\\u%04x" % o)
        elif 0x20 <= o < 0x7F and st.esc and st.rng.random() < st.esc:
            out.append(st.rng.choice(["\\u%04x", "\\u%04X"]) % o)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def enc(v, st: Style) -> str:
    g = st.gap
    if isinstance(v, dict):
        if not v:
            return "{" + g() + "}"
        items = [g() + enc_str(k, st) + g() + ":" + st.after_colon()
                 + enc(x, st) + g() for k, x in v.items()]
        return "{" + ("," + st.after_comma()).join(items) + "}"
    if isinstance(v, list):
        if not v:
            return "[" + g() + "]"
        items = [g() + enc(x, st) + g() for x in v]
        return "[" + ("," + st.after_comma()).join(items) + "]"
    if isinstance(v, str):
        return enc_str(v, st)
    if isinstance(v, RawJSON):
        return v.text
    return json.dumps(v)


class RawJSON:
    """A pre-serialised JSON fragment (e.g. ``NaN``, a deep nest)."""

    def __init__(self, text: str):
        self.text = text


def line(obj, st: Style | None = None) -> bytes:
    return enc(obj, st or Style()).encode("utf-8", "surrogatepass")


def cc(obj) -> bytes:
    """A line exactly as JSON.stringify would write it."""
    return json.dumps(obj, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


# --------------------------------------------------------------------------
# Claude Code shaped entries
# --------------------------------------------------------------------------

def envelope(typ: str, message, *, extra_after=None, extra_before=None,
             rng=None, shuffle=False) -> dict:
    """A top-level entry in Claude Code's key order: parentUuid,
    isSidechain, userType, cwd, sessionId, version, gitBranch, type,
    message, uuid, timestamp (then toolUseResult and friends)."""
    rng = rng or random.Random(0)
    d = {"parentUuid": "0d1c%04x-1111-2222-3333-444455556666"
                       % rng.randrange(65536),
         "isSidechain": False, "userType": "external",
         "cwd": "/home/u/work/proj", "sessionId": CLAUDE_SID,
         "version": "2.1.285", "gitBranch": "main"}
    if extra_before:
        d.update(extra_before)
    d["type"] = typ
    if message is not _NO_MESSAGE:
        d["message"] = message
    d["uuid"] = "9f3e%04x-aaaa-bbbb-cccc-ddddeeeeffff" % rng.randrange(65536)
    d["timestamp"] = "2026-09-30T12:00:%02d.123Z" % rng.randrange(60)
    if extra_after:
        d.update(extra_after)
    if shuffle:
        keys = list(d)
        rng.shuffle(keys)
        d = {k: d[k] for k in keys}
    return d


_NO_MESSAGE = object()
NO_MESSAGE = _NO_MESSAGE


def prompt_msg(content) -> dict:
    return {"role": "user", "content": content}


def tool_result_msg(content, *, tool_use_id=True, role=True, is_error=None,
                    id_first=True, blocks=None) -> dict:
    block = {}
    if tool_use_id and id_first:
        block["tool_use_id"] = "toolu_01ABCdef234"
    block["type"] = "tool_result"
    if tool_use_id and not id_first:
        block["tool_use_id"] = "toolu_01ABCdef234"
    block["content"] = content
    if is_error is not None:
        block["is_error"] = is_error
    msg = {"role": "user"} if role else {}
    msg["content"] = [block] + list(blocks or [])
    return msg


def assistant_msg(content) -> dict:
    return {"model": "claude-opus-5-5", "id": "msg_01XyZ", "type": "message",
            "role": "assistant", "content": content,
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 3, "output_tokens": 12}}


def tg_prompt(body="do the thing", **kw) -> dict:
    return envelope("user", prompt_msg(f"{TG_MARKER}\n{body}"), **kw)


def term_prompt(body="do the thing", **kw) -> dict:
    return envelope("user", prompt_msg(body), **kw)


def tool_result(content="ok", **kw) -> dict:
    env_kw = {k: kw.pop(k) for k in ("rng", "shuffle", "extra_before")
              if k in kw}
    tur = kw.pop("tool_use_result", {"stdout": "ok", "stderr": "",
                                     "interrupted": False})
    return envelope("user", tool_result_msg(content, **kw),
                    extra_after={"toolUseResult": tur}, **env_kw)


def assistant_text(text="Working on it.", **kw) -> dict:
    return envelope("assistant",
                    assistant_msg([{"type": "text", "text": text}]), **kw)


def assistant_tool_use(inp: dict, name="Bash", **kw) -> dict:
    return envelope("assistant", assistant_msg(
        [{"type": "tool_use", "id": "toolu_01ABCdef234", "name": name,
          "input": inp}]), **kw)


def deep(depth: int, inner: str = "1", kind: str = "list") -> RawJSON:
    if kind == "list":
        return RawJSON("[" * depth + inner + "]" * depth)
    return RawJSON('{"a":' * depth + inner + "}" * depth)


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------

def write_lines(path: Path, lines, *, eol=b"\n", trailing=True) -> Path:
    data = eol.join(lines) + (eol if trailing and lines else b"")
    path.write_bytes(data)
    return path


def pretool(transcript, tool="Read", tool_input=None, *, session=SESSION,
            cwd="/home/u/work/proj") -> dict:
    return {"hook_event_name": "PreToolUse", "session": session,
            "session_id": CLAUDE_SID, "cwd": cwd, "tool_name": tool,
            "tool_input": (tool_input if tool_input is not None
                           else {"file_path": cwd + "/a.txt"}),
            "transcript_path": None if transcript is None else str(transcript)}


# --------------------------------------------------------------------------
# Seeded corpus
# --------------------------------------------------------------------------

_WORDS = ["fix", "the", "bug", "in", "src", "/etc/passwd", "safety", "policy",
          "aipager", "سلام", "🙂", "naïve", "\t", '"type":"user"',
          '"message"', "{", "}", "[", "\\u0041", "<task-notification>",
          "[via Telegram", "\r", "line\nbreak", "ok", "done", "ls -la",
          "\x1b[31mred\x1b[0m", " ", " ", "message", "user"]


def words(rng, n=None) -> str:
    n = rng.randint(0, 8) if n is None else n
    return " ".join(rng.choice(_WORDS) for _ in range(n))


def _tg_text(rng) -> str:
    v = rng.randrange(6)
    if v == 0:
        return f"{TG_MARKER}\n{words(rng)}"
    if v == 1:
        return f"   {TG_MARKER}\n{words(rng)}"
    if v == 2:
        return f"{words(rng, 3)}\n\t{TG_MARKER}\n{words(rng)}"
    if v == 3:
        return "[via Telegram]"
    if v == 4:
        return f"[via telegram · @x]\n{words(rng)}"   # wrong case: terminal
    return f" via Telegram\n{words(rng)}"             # no bracket: terminal


def _prompt_content(rng):
    v = rng.randrange(7)
    if v == 0:
        return _tg_text(rng)
    if v == 1:
        return words(rng)
    if v == 2:
        return [{"type": "text", "text": words(rng)},
                {"type": "text", "text": _tg_text(rng)}]
    if v == 3:
        return [{"type": "image", "source": {"type": "base64",
                 "media_type": "image/png", "data": "iVBORw0KGgo="}},
                {"type": "text", "text": _tg_text(rng)}]
    if v == 4:
        return rng.choice(["", " ", "\n", [], [{"type": "text", "text": ""}]])
    if v == 5:
        return rng.choice([TASK_NOTE, "\n  " + TASK_NOTE,
                           [{"type": "text", "text": TASK_NOTE}]])
    return [words(rng), _tg_text(rng)]    # bare string blocks


def _tr_content(rng):
    v = rng.randrange(9)
    if v == 0:
        return words(rng)
    if v == 1:
        return BLOCK_TEXT
    if v == 2:
        return [{"type": "text", "text": "aipager"},
                {"type": "text", "text": "safety policy"}]
    if v == 3:
        return "policy for aipager is about safety"     # scattered words
    if v == 4:
        return [{"type": "text", "text": words(rng)}, "aipager safety", 3,
                {"text": "policy"}]
    if v == 5:
        return [{"type": "text", "text": words(rng)}]
    if v == 6:
        return "x" * rng.randint(1, 3000)
    if v == 7:
        return {"weird": "content", "text": BLOCK_TEXT}  # dict content
    return [{"type": "image", "source": {"type": "base64", "data": "AAAA"}}]


def corpus_line(rng) -> bytes:
    """One random transcript line. Never one of the documented
    divergence shapes (see :func:`assert_in_contract`)."""
    st = Style.random(rng)
    shuffle = rng.random() < 0.15
    v = rng.randrange(100)
    if v < 14:
        obj = envelope("user", prompt_msg(_prompt_content(rng)), rng=rng,
                       shuffle=shuffle)
    elif v < 18:
        obj = envelope("user", prompt_msg(_prompt_content(rng)), rng=rng,
                       extra_before={rng.choice(["isMeta",
                                                 "isCompactSummary"]): True},
                       shuffle=shuffle)
    elif v < 40:
        kw = dict(tool_use_id=rng.random() < 0.85, role=rng.random() < 0.85,
                  id_first=rng.random() < 0.7,
                  is_error=rng.choice([None, True, False]))
        if rng.random() < 0.1:
            kw["blocks"] = [{"type": "tool_result", "tool_use_id": "toolu_2",
                             "content": rng.choice(["policy", words(rng)])}]
        obj = envelope("user", tool_result_msg(_tr_content(rng), **kw),
                       rng=rng, shuffle=shuffle,
                       extra_after={"toolUseResult": {
                           "stdout": words(rng),
                           "slug": rng.choice(["message", "x"])}})
    elif v < 50:
        obj = assistant_text(rng.choice([words(rng), BLOCK_TEXT,
                                         f"{TG_MARKER}\nhi"]), rng=rng,
                             shuffle=shuffle)
    elif v < 56:
        obj = assistant_tool_use({"command": words(rng),
                                  "nested": {"type": "user", "message": "x"},
                                  "deep": deep(rng.randint(1, 300))},
                                 rng=rng, shuffle=shuffle)
    elif v < 62:
        obj = rng.choice([
            {"type": "system", "subtype": "informational",
             "content": words(rng), "level": "info"},
            {"type": "summary", "summary": words(rng), "leafUuid": "u1"},
            {"type": "queue-operation", "operation": "enqueue",
             "content": _tg_text(rng)},
            {"type": "system", "message": rng.choice([None, "", 0, [], False,
                                                      {}])},
            {"type": "system", "message": {}, "content": [
                {"type": "tool_result", "content": BLOCK_TEXT}]},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_result", "content": [
                    {"text": "aipager"}, {"text": "safety policy"}]}]}},
            {"type": "file-history-snapshot", "messageId": "m1",
             "snapshot": {"trackedFileBackups": {}}},
            {"type": "attachment", "attachment": {"type": "user",
                                                  "content": words(rng)}},
        ])
    elif v < 70:
        obj = rng.choice([
            {"type": "user", "message": None},
            {"type": "user", "message": "x"},
            {"type": "user", "message": []},
            {"type": "user", "message": 0},
            {"type": "user", "message": {}},
            {"type": "user", "message": {"content": None}},
            {"type": "user", "message": {"content": 5}},
            {"type": "user", "content": _tg_text(rng)},
            {"type": "user", "message": {}, "content": [
                {"type": "tool_result", "content": BLOCK_TEXT}]},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "text", "text": _tg_text(rng)},
                {"type": "tool_result", "content": "r"}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "content": "r"},
                {"type": "text", "text": _tg_text(rng)}]}},
            {"type": "user", "n": RawJSON(rng.choice(["NaN", "-Infinity",
                                                      "1e400", "7" * 900])),
             "message": {"content": _tg_text(rng)}},
            {"type": "user", "big": deep(rng.randint(1, 600), kind="dict"),
             "message": {"content": _tg_text(rng)}},
        ])
    elif v < 76:
        return rng.choice([b"[1,2]", b'"s"', b"3", b"null", b"true",
                           b"false", b"[]", b"{}", b'[{"type":"user"}]',
                           b" [1]", b'"[via Telegram"'])
    elif v < 82:
        full = cc(tg_prompt(words(rng), rng=rng))
        return rng.choice([b"not json", b"{", b"}", b'{"type":"user"',
                           full[: rng.randint(1, len(full) - 1)],
                           full + b" trailing", full + full,
                           b"\xef\xbb\xbf" + full, b"\x00" + full])
    elif v < 88:
        return rng.choice([b"", b" ", b"\t", b"\x0c", b"\x0b", b"\r",
                           b" \t \r"])
    elif v < 94:
        pre = rng.choice([b" ", b"\t", b"\x0c", b"\x0b", b"\xc2\xa0",
                          b"\x1c", b"\xc2\x85", b"\xe2\x80\xa8",
                          b"\xe3\x80\x80", b"  \t"])
        post = rng.choice([b"", b" ", b"\xc2\xa0", b"\x1f"])
        return pre + line(rng.choice([
            tg_prompt(words(rng), rng=rng), term_prompt(words(rng), rng=rng),
            tool_result(BLOCK_TEXT, rng=rng), tool_result("r", rng=rng)]),
            st) + post
    elif v < 98:
        bad = rng.choice([b"\xff", b"\xfe\xff", b"\xe2\x82", b"\xc3",
                          b"\xed\xa0\x80"])
        base = line(rng.choice([tg_prompt("@@B@@ " + words(rng), rng=rng),
                                term_prompt("@@B@@", rng=rng),
                                tool_result("aipager @@B@@ safety policy",
                                            rng=rng),
                                tool_result("@@B@@", rng=rng)]), st)
        return base.replace(b"@@B@@", bad)
    else:
        obj = tool_result("y" * rng.randint(20_000, 120_000), rng=rng)
    return line(obj, st)


def corpus_case(rng) -> bytes:
    n = rng.randint(0, 9)
    lines = [corpus_line(rng) for _ in range(n)]
    for ln in lines:
        assert_in_contract(ln)
    eol = b"\r\n" if rng.random() < 0.15 else b"\n"
    return eol.join(lines) + (eol if lines and rng.random() < 0.8 else b"")


class _Dup(Exception):
    pass


def _no_dups(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise _Dup(keys)
    return dict(pairs)


def assert_in_contract(raw: bytes) -> None:
    """Fail if a generated line is one of entrypoints.md's documented
    divergence shapes, so a generator edit cannot silently add one."""
    text = raw.decode("utf-8", "replace").strip()
    try:
        obj = json.loads(text, object_pairs_hook=_no_dups)
    except _Dup as exc:
        raise AssertionError(f"duplicate keys generated: {exc}") from None
    except (json.JSONDecodeError, RecursionError, ValueError):
        assert raw.count(b"[") < 1000 and raw.count(b"{") < 1000
        return
    if not isinstance(obj, dict):
        return

    def walk(v, depth, inside_message):
        assert depth < 1000, "nesting of 1000+ generated"
        if isinstance(v, dict):
            for k, x in v.items():
                if k == "message" and depth > 0:
                    assert inside_message, "nested message key ahead"
                walk(x, depth + 1, inside_message)
        elif isinstance(v, list):
            for x in v:
                walk(x, depth + 1, inside_message)
        elif isinstance(v, int) and not isinstance(v, bool):
            assert len(str(abs(v))) < 1000, "1000+ digit integer"

    for k, x in obj.items():
        walk(x, 1, k == "message")
    msg = obj.get("message")
    if obj.get("type") != "user" and msg and not isinstance(msg, dict):
        raise AssertionError("truthy non-dict message on a non-user line")
