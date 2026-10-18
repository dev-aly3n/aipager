"""Scenario: Claude sends a file to the session's chat (SendUserFile).

The person asks in plain words, with no tool name: "send me the image at
<path>" (and the same in Persian). Claude calls SendUserFile; aipager's
hook checks the file, the daemon copies it, and after the turn's answer
the fake Bot API receives ``sendPhoto`` in the session's own chat, as a
silent reply to the answer, captioned with the session's name.

Stand-in: the stand-in plays the call with Claude Code 2.1.302's hook
shapes; a second case asks for one of Claude's own files and is refused
softly (the reason reaches the stand-in, nothing is sent, the session is
not halted).

Real Claude: the English and the Persian request, three runs each, in a
fresh session each (ids ``en-1``..``fa-3``), plus one observation run
(``outside-1``) that asks for a file outside the session's folder and
reports what Claude Code did. Each real run prints what it observed (the
session's ``CLAUDE_CODE_ATTACHMENTS_BY_PATH``, the tool call, the daemon's
``send_files_ran payload`` line) for the run log.
"""

from __future__ import annotations

import re
import struct
import time
import zlib

import pytest

from tests.e2e.fake_telegram import instance as fti
from tests.e2e.faketg import conftest as ftc
from tests.e2e.faketg import flows

DM = fti.DM_ID
ALICE = fti.ALICE

# "the picture that is at <path>, send it to me" in Persian, written with
# escapes (no raw right-to-left text in a test source).
FA_PROMPT = ("عکسی که در {path} هست "
             "رو برام بفرست")
EN_PROMPT = "send me the image at {path}"

_PAYLOAD_RE = re.compile(r"send_files_ran payload: (.*)")


def png(w: int = 32, h: int = 24) -> bytes:
    """A real, decodable PNG (Telegram and Claude both read it)."""
    raw = b"".join(b"\x00" + bytes([200, 60, 60]) * w for _ in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _reply_id(call) -> int | None:
    p = call.params
    rp = p.get("reply_parameters")
    if isinstance(rp, dict):
        return rp.get("message_id")
    return p.get("reply_to_message_id")


def _ask(inst, label: str, prompt_template: str, *, where: str = "session",
         filename: str = "pic.png", timeout: float = 240):
    """A fresh DM session; a PNG in its folder (or outside it); the plain
    request; the turn's end. Returns (name, path, since, log_since)."""
    fake = inst.fake
    name = inst.new_session(DM, ALICE, label)
    folder = inst.session_folder(name)
    path = (folder / filename) if where == "session" else (inst.root / filename)
    path.write_bytes(png())
    flows.settle(fake)
    since, log_since = fake.mark(), inst.log_mark()
    text = prompt_template.format(path=path)
    flows.prompt_turn(inst, DM, ALICE, name, text, str(path), timeout=timeout)
    return name, path, since, log_since


def _wait_photo(fake, since: int, timeout: float = 90):
    return fake.wait_for(lambda c: c.method in ("sendPhoto", "sendDocument")
                         and c.chat_id == DM and c.status == 200,
                         timeout=timeout, since=since, what="the file in the DM")


def _assert_delivered(inst, name: str, label: str, path, since: int, log_since: int):
    fake = inst.fake
    photo = _wait_photo(fake, since)
    assert photo.method == "sendPhoto", photo.summary()
    assert list(photo.files.values())[0][1] == path.read_bytes()
    assert photo.params.get("disable_notification") is True
    # The session's name first; Claude's own caption, if it gave one, on
    # the next line.
    assert photo.text.split("\n")[0] == f"💬 {label}", photo.text
    # A reply to the turn's answer: a message of the bot's, sent before the
    # file, that is not the busy card.
    target = _reply_id(photo)
    answer = [c for c in fake.calls(since=since)
              if c.chat_id == DM and c.status == 200
              and c.method in ("sendRichMessage", "sendMessage")
              and isinstance(c.response.get("result"), dict)
              and c.response["result"].get("message_id") == target]
    assert answer and answer[0].seq < photo.seq, f"no answer {target} before the file"
    cards = flows.busy_card_ids(inst.log_lines(log_since), label)
    assert target not in cards, f"the file replies to the busy card {target}"
    # Only the session's own chat got anything from this turn.
    others = [c for c in fake.calls(since=since)
              if c.method.startswith("send") and c.chat_id not in (DM, None)]
    flows.assert_no_calls(others, "sends to another chat")
    assert fake.unknown_methods == []
    return photo


# ---- stand-in ------------------------------------------------------------------------

def test_standin_send_file_arrives_after_the_answer(fresh):
    if ftc.claude_mode() != "standin":
        pytest.skip("stand-in run (real Claude has its own cases below)")
    inst = fresh
    name, path, since, _log = _ask(inst, "ft9", EN_PROMPT)
    assert [e.get("decision") for e in inst.standin_log(name)
            if e.get("event") == "decision"] == ["sent"]
    _assert_delivered(inst, name, "ft9", path, since, _log)
    assert inst.session_env_values(name, "CLAUDE_CODE_ATTACHMENTS_BY_PATH")[:1] == ["1"]


def test_standin_persian_request_sends_the_file(fresh):
    if ftc.claude_mode() != "standin":
        pytest.skip("stand-in run")
    inst = fresh
    name, path, since, _log = _ask(inst, "ft9f", FA_PROMPT)
    _assert_delivered(inst, name, "ft9f", path, since, _log)


def test_standin_refused_file_is_soft_and_sends_nothing(fresh):
    if ftc.claude_mode() != "standin":
        pytest.skip("stand-in run")
    inst = fresh
    fake = inst.fake
    name = inst.new_session(DM, ALICE, "ft9r")
    secret = inst.home / ".claude" / ".credentials.json"
    flows.settle(fake)
    since = fake.mark()
    flows.prompt_turn(inst, DM, ALICE, name, EN_PROMPT.format(path=secret), str(secret))
    [decision] = [e for e in inst.standin_log(name) if e.get("event") == "decision"]
    assert decision["decision"] == "denied"
    assert decision["reason"].startswith("SendUserFile refused, nothing was sent: ")
    assert "aipager safety policy" not in decision["reason"]
    sent = [c for c in flows.quiet(fake, 3, since=since)
            if c.method in ("sendPhoto", "sendDocument", "sendMediaGroup")]
    flows.assert_no_calls(sent, "a file was sent")
    # Not halted: the next message is a turn like any other.
    flows.prompt_turn(inst, DM, ALICE, name, "Reply with exactly: still here",
                      "still here")
    flows.wait_text(fake, DM, "still here", since=since)


@pytest.mark.parametrize("typed_in", ["telegram", "terminal"])
def test_standin_a_request_queued_while_a_turn_runs_sends_the_file(fresh, typed_in):
    """The request comes while an earlier turn still runs, and Claude Code
    takes it up as its next turn (a ``dequeue`` transcript line, no hook;
    its UserPromptSubmit fired at submit). Sent from Telegram, the daemon
    typed it itself and resumes the files turn; typed in the terminal,
    the transcript's dequeue line proves the turn began. Either way the
    file arrives after that turn's answer, as a reply to it."""
    if ftc.claude_mode() != "standin":
        pytest.skip("stand-in run")
    inst = fresh
    fake = inst.fake
    label = "ft9q" if typed_in == "telegram" else "ft9t"
    name = inst.new_session(DM, ALICE, label)
    path = inst.session_folder(name) / "queued.png"
    path.write_bytes(png())
    (inst.inst_dir / f"standin-cc-queue-{name}").write_text("")
    inst.hold_tools(name)
    try:
        flows.settle(fake)
        since, log_since = fake.mark(), inst.log_mark()
        before = len(inst.prompts_seen(name))
        fake.inject_text(DM, fti.user(ALICE), "hold this turn. Reply with exactly: first done")
        flows.wait_prompt(inst, name, "hold this turn", after=before)
        inst.wait_log(f"[{label}]", "→ BUSY", since=log_since)
        request = EN_PROMPT.format(path=path)
        if typed_in == "telegram":
            fake.inject_text(DM, fti.user(ALICE), request)
        else:
            # The text, then Enter in its own read (as a terminal sends it).
            inst.send_keys(name, request.encode())
            time.sleep(0.3)
            inst.send_keys(name, b"\r")
        fti.wait_until(lambda: any(e.get("event") == "queued"
                                   for e in inst.standin_log(name)),
                       60, "the request to be queued behind the running turn",
                       lambda: inst.log_tail(25))
    finally:
        inst.release_tools(name)
    photo = _assert_delivered(inst, name, label, path, since, log_since)
    events = [e.get("event") for e in inst.standin_log(name)]
    assert "dequeued" in events, events
    lines = inst.log_lines(log_since)
    proof = ("files turn resumed for queued message" if typed_in == "telegram"
             else "the transcript shows a turn begun with no prompt hook")
    assert any(proof in line for line in lines), inst.log_tail(40)
    assert not any("was dropped" in line or "not in the transcript" in line
                   for line in lines), inst.log_tail(40)
    print(f"[queued-{typed_in}] {photo.summary()}")


# ---- real Claude ----------------------------------------------------------------------

def _observe(inst, name: str, log_since: int, case: str) -> None:
    env = inst.session_env_values(name, "CLAUDE_CODE_ATTACHMENTS_BY_PATH")
    payloads = [m.group(1) for line in inst.log_lines(log_since)
                if (m := _PAYLOAD_RE.search(line))]
    print(f"[{case}] session env CLAUDE_CODE_ATTACHMENTS_BY_PATH={env!r}")
    print(f"[{case}] transcript tail:\n{inst.transcript_tail(name, 10)}")
    for p in payloads:
        print(f"[{case}] {p}")


@pytest.mark.parametrize("lang, run", [(lang, n) for lang in ("en", "fa") for n in (1, 2, 3)],
                         ids=[f"{lang}-{n}" for lang in ("en", "fa") for n in (1, 2, 3)])
def test_real_claude_sends_the_image_it_is_asked_for(fresh, lang, run):
    if ftc.claude_mode() == "standin":
        pytest.skip("real Claude only")
    inst = fresh
    label = f"ft9{lang}{run}"
    template = EN_PROMPT if lang == "en" else FA_PROMPT
    name, path, since, log_since = _ask(inst, label, template)
    try:
        _assert_delivered(inst, name, label, path, since, log_since)
    finally:
        _observe(inst, name, log_since, f"{lang}-{run}")
    assert inst.session_env_values(name, "CLAUDE_CODE_ATTACHMENTS_BY_PATH")[:1] == ["1"]


@pytest.mark.parametrize("case", ["outside-1"])
def test_real_claude_outside_the_session_folder_observation(fresh, case):
    """An observation, not a rule: a file outside the session's folder.
    Prints whether a permission card or the outside-reads dialog came up
    and whether the file arrived."""
    if ftc.claude_mode() == "standin":
        pytest.skip("real Claude only")
    inst = fresh
    fake = inst.fake
    label = "ft9out"
    name = inst.new_session(DM, ALICE, label)
    path = inst.root / "outside.png"
    path.write_bytes(png())
    flows.settle(fake)
    since, log_since = fake.mark(), inst.log_mark()
    fake.inject_text(DM, fti.user(ALICE), EN_PROMPT.format(path=path))
    try:
        card, allow = fake.wait_button(DM, "Allow", since=since, timeout=120)
        print(f"[{case}] permission card: {card.get('text', '')[:300]!r}")
        rows = (card.get("reply_markup") or {}).get("inline_keyboard") or []
        print(f"[{case}] its buttons: {[b.get('text') for row in rows for b in row]}")
        cb = fake.inject_callback(fti.user(ALICE), fake.message(DM, card["message_id"]),
                                  allow)
        fake.wait_answer(cb, timeout=30)
    except AssertionError:
        print(f"[{case}] no permission card within 120 s")
    try:
        flows.wait_turn_end(inst, name, label, since_log=log_since, timeout=240)
    except AssertionError:
        print(f"[{case}] the turn did not end within 240 s")
    try:
        # The files follow the answer, which goes out after the turn ends.
        _wait_photo(fake, since, timeout=60)
    except AssertionError:
        print(f"[{case}] no file within 60 s of the turn's end")
    sent = [c for c in fake.calls(since=since)
            if c.method in ("sendPhoto", "sendDocument") and c.chat_id == DM]
    print(f"[{case}] files sent: {[(c.method, c.status) for c in sent]}")
    print(f"[{case}] screen:\n{inst.screen(name)}")
    _observe(inst, name, log_since, case)
