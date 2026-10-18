"""Files Claude sends in a turn Claude Code starts with no UserPromptSubmit
(review 2026-10-18, rev-iter3-001), driven through the daemon's control
socket entry (HookReceiver datagrams into bot.notify) with a recording bot:

- a message queued while a turn runs and popped by Claude Code as the next
  turn (a transcript ``dequeue``, no hook) can send files;
- so can the turn a Stop hook's feedback makes go on (``/goal``);
- a call after the last turn ended with no such start in the transcript is
  not sent, and gets a chat line only when Claude's own transcript shows
  the call (its names come from there);
- a batch kept back from an earlier turn replies to that turn's answer.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aipager.bot import outbox
from aipager.dtach.hook_receiver import HookReceiver
from aipager.state import Status

from test_send_files_finish import (  # noqa: I001 (the sibling test module)
    _audit_records, _bot, _default_transcript, _pic, _rich_ids,
)

CHAT = 256113222          # the chat `_pin_single_chat_config` pins
NAME = "claude-dev"
SID = "5b1d7363-0000-0000-0000-000000000001"


def _stamp(wall: float | None = None) -> str:
    when = datetime.fromtimestamp(time.time() if wall is None else wall,
                                  timezone.utc)
    return when.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Rig:
    """One idle session, its transcript, and the hook datagrams Claude Code
    would send."""

    _seq = itertools.count(1)

    def __init__(self, bot, cwd: Path):
        self.bot = bot
        self.sess = bot.registry.get_or_create(NAME)
        self.sess.label = "dev"
        self.sess.scope_kind = "dm"
        self.sess.scope_chat_id = CHAT
        self.sess.cwd = str(cwd)
        self.cwd = str(cwd)
        self.tp = _default_transcript()
        self.tp.parent.mkdir(parents=True, exist_ok=True)
        self.tp.touch()
        bot.registry.transition(NAME, Status.IDLE)
        self.rec = HookReceiver(bot.registry, bot.notify)

    def line(self, obj, wall: float | None = None) -> None:
        row = {**obj, "cwd": self.cwd, "timestamp": _stamp(wall),
               "sessionId": SID}
        with open(self.tp, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")

    def dequeue(self) -> None:
        """Claude Code pops a queued message as its next prompt."""
        self.line({"type": "queue-operation", "operation": "dequeue"})

    def feedback(self) -> None:
        """A Stop hook blocked the stop: Claude Code goes on."""
        self.line({"type": "user", "isMeta": True, "message": {
            "role": "user",
            "content": "Stop hook feedback:\n[the goal]: not met yet"}})

    async def dg(self, **kw) -> None:
        base = {"session": NAME, "session_id": SID, "cwd": self.cwd,
                "transcript_path": str(self.tp), "_seq": next(self._seq)}
        await self.rec._on_datagram(json.dumps({**base, **kw}).encode())

    async def prompt(self, text: str) -> None:
        await self.dg(hook_event_name="UserPromptSubmit", prompt=text)

    async def tool(self, tid: str, name: str = "Bash") -> None:
        tin = {"command": "sleep 1"}
        await self.dg(hook_event_name="PreToolUse", tool_name=name,
                      tool_use_id=tid, tool_input=tin)
        await self.dg(hook_event_name="PostToolUse", tool_name=name,
                      tool_use_id=tid, tool_input=tin, tool_response={})

    async def send(self, tid: str, files: list[str], *, written=True,
                   listed: list[str] | None = None) -> None:
        """Claude calls SendUserFile; *listed* is what its own transcript
        line lists (the datagrams' list when None)."""
        tin = {"files": files, "status": "normal"}
        if written:
            self.line({"type": "assistant", "message": {"role": "assistant",
                       "content": [{"type": "tool_use", "id": tid,
                                    "name": "SendUserFile",
                                    "input": {"files": listed or files,
                                              "status": "normal"}}]}})
        await self.dg(hook_event_name="PreToolUse", tool_name="SendUserFile",
                      tool_use_id=tid, tool_input=tin)
        await self.dg(hook_event_name="PostToolUse", tool_name="SendUserFile",
                      tool_use_id=tid, tool_input=tin,
                      tool_response={"attachments": []})

    async def stop(self, answer: str) -> None:
        self.line({"type": "assistant", "message": {"role": "assistant",
                   "content": [{"type": "text", "text": answer}]}})
        await self.dg(hook_event_name="Stop", last_assistant_message=answer)
        await asyncio.sleep(0.05)
        await outbox.OUTBOX.settled(NAME)
        for _ in range(40):
            if not outbox.OUTBOX.in_flight.get(NAME) and not outbox.OUTBOX.delivering(NAME):
                break
            await asyncio.sleep(0.05)
        await outbox.OUTBOX.settled(NAME)


def _rig(mk_bot, files_bot, tmp_path) -> Rig:
    return Rig(_bot(mk_bot, files_bot), tmp_path)


def _failed():
    return [r for r in _audit_records() if r["action"] == "FileFailed"]


def _lines(files_bot):
    return [c[2]["_args"][1] for c in files_bot.calls if c[0] == "send_message"
            and "Couldn't send" in str(c[2]["_args"][1])]


def test_a_queued_message_dequeued_as_the_next_turn_sends_its_file(
        mk_bot, files_bot, rich, run_async, tmp_path):
    """The reviewer's sequence: turn one runs a tool, a second message is
    queued (its UserPromptSubmit fires at submit, while the turn runs),
    Stop ends turn one, Claude Code dequeues the message as the next turn
    (no hook) and its first tool call is SendUserFile."""
    r = _rig(mk_bot, files_bot, tmp_path)
    pic = _pic(tmp_path)

    async def go():
        await r.prompt("one")
        await r.dg(hook_event_name="PreToolUse", tool_name="Bash",
                   tool_use_id="t1", tool_input={"command": "sleep 1"})
        await r.prompt("two: also send me the chart")
        await r.dg(hook_event_name="PostToolUse", tool_name="Bash",
                   tool_use_id="t1", tool_input={"command": "sleep 1"},
                   tool_response={})
        await r.stop("Turn one done.")
        assert not r.sess.files_turn_open
        r.dequeue()
        await r.send("toolu_q", [str(pic)])
        await r.stop("Here is the chart.")
    run_async(go())
    [photo] = files_bot.file_calls()
    assert photo[0] == "send_photo" and photo[1] == CHAT
    # After this turn's answer, as a reply to it.
    assert photo[2]["reply_to_message_id"] == _rich_ids(files_bot)[-1]
    assert _failed() == []


def test_a_stop_hooks_feedback_turn_sends_its_file(mk_bot, files_bot, rich,
                                                    run_async, tmp_path):
    """/goal: Stop is blocked, Claude goes on with no prompt hook."""
    r = _rig(mk_bot, files_bot, tmp_path)
    pic = _pic(tmp_path)

    async def go():
        await r.prompt("make the chart, /goal it exists")
        await r.tool("t1")
        await r.stop("Working on it.")
        r.feedback()
        await r.tool("t2")
        await r.send("toolu_g", [str(pic)])
        await r.stop("The chart is attached.")
    run_async(go())
    assert [c[0] for c in files_bot.file_calls()] == ["send_photo"]
    assert _failed() == []


def _copies_spy(monkeypatch) -> list:
    """Every copy the daemon starts (it reads the files)."""
    seen: list = []
    real = outbox.copy_batch

    def spy(*a, **kw):
        seen.append(a[2])
        return real(*a, **kw)
    monkeypatch.setattr(outbox, "copy_batch", spy)
    return seen


def test_a_send_after_the_last_turn_with_no_start_is_not_sent(
        mk_bot, files_bot, rich, run_async, tmp_path, monkeypatch):
    """Forged hooks after a real turn ended: PreToolUse makes the idle
    session BUSY, but the transcript shows no turn begun since: nothing is
    read or sent, each file is audited, and no line (the call is not
    Claude's)."""
    monkeypatch.setattr(outbox, "TRANSCRIPT_WAIT_SECONDS", 0.5)
    copies = _copies_spy(monkeypatch)
    r = _rig(mk_bot, files_bot, tmp_path)
    pic = _pic(tmp_path)

    async def go():
        await r.prompt("one")
        await r.tool("t1")
        await r.stop("Done.")
        await r.send("toolu_f", [str(pic)], written=False)
        await r.stop("")
    run_async(go())
    assert copies == []
    assert files_bot.file_calls() == []
    assert [x["file_name"] for x in _failed()] == ["chart.png"]
    assert _lines(files_bot) == []
    assert not r.sess.files_turn_open
    # Nothing shows the call is Claude's: its id is not even used up.
    assert "toolu_f" not in r.sess.files_sent_ids


def test_a_start_before_the_newest_end_does_not_count(
        mk_bot, files_bot, rich, run_async, tmp_path, monkeypatch):
    """A hookless turn with no tool call ends with a Stop on an idle
    session; its dequeue is older than that end: a send after it is not
    from a turn that start began."""
    monkeypatch.setattr(outbox, "TRANSCRIPT_WAIT_SECONDS", 0.5)
    r = _rig(mk_bot, files_bot, tmp_path)
    pic = _pic(tmp_path)

    async def go():
        await r.prompt("one")
        await r.tool("t1")
        await r.stop("Done.")
        r.dequeue()
        await r.stop("A short reply to the queued one.")
        # Older than the newest end by more than the clock slack.
        r.sess.files_turn_closed_wall += outbox.TRANSCRIPT_CLOCK_SLACK_SECONDS + 1
        await r.send("toolu_f", [str(pic)], written=False)
        await r.stop("")
    run_async(go())
    assert files_bot.file_calls() == []
    assert [x["file_name"] for x in _failed()] == ["chart.png"]


def test_a_real_call_with_no_start_found_gets_a_line_with_claudes_names(
        mk_bot, files_bot, rich, run_async, tmp_path, monkeypatch):
    """Claude's own transcript shows the call after the last turn ended,
    but no start: not sent, and the chat says so for the files that line
    lists (never the datagram's names)."""
    monkeypatch.setattr(outbox, "TRANSCRIPT_WAIT_SECONDS", 0.5)
    r = _rig(mk_bot, files_bot, tmp_path)
    pic = _pic(tmp_path)

    async def go():
        await r.prompt("one")
        await r.tool("t1")
        await r.stop("Done.")
        await r.send("toolu_n", [str(pic)], listed=[str(tmp_path / "real.png")])
        await r.stop("Sent.")
    run_async(go())
    assert files_bot.file_calls() == []
    assert _lines(files_bot) == [
        "⚠️ Couldn't send real.png: aipager could not confirm that Claude sent "
        "it in this turn."]
    assert [x["file_name"] for x in _failed()] == ["chart.png"]


def test_a_mismatched_call_in_a_prompted_turn_gets_a_line(
        mk_bot, files_bot, rich, run_async, tmp_path, monkeypatch):
    """In a turn a real prompt opened, the call is in Claude's transcript
    but lists other files than the datagram (a forger racing a live call,
    say): not sent, and a line per file Claude's own line lists."""
    monkeypatch.setattr(outbox, "TRANSCRIPT_WAIT_SECONDS", 0.5)
    r = _rig(mk_bot, files_bot, tmp_path)
    pic = _pic(tmp_path)

    async def go():
        await r.prompt("send me the chart")
        await r.send("toolu_m", [str(pic)], listed=[str(tmp_path / "real.png")])
        await r.stop("Here it is.")
    run_async(go())
    assert files_bot.file_calls() == []
    assert _lines(files_bot) == [
        "⚠️ Couldn't send real.png: aipager could not confirm that Claude sent "
        "it in this turn."]


def test_a_call_not_in_the_transcript_gets_no_line(
        mk_bot, files_bot, rich, run_async, tmp_path, monkeypatch):
    """A forged call in a real turn: dropped and audited, no line (nothing
    shows it is Claude's)."""
    monkeypatch.setattr(outbox, "TRANSCRIPT_WAIT_SECONDS", 0.5)
    r = _rig(mk_bot, files_bot, tmp_path)
    pic = _pic(tmp_path)

    async def go():
        await r.prompt("send me the chart")
        await r.send("toolu_x", [str(pic)], written=False)
        await r.stop("Here it is.")
    run_async(go())
    assert files_bot.file_calls() == []
    assert _lines(files_bot) == []
    assert [x["file_name"] for x in _failed()] == ["chart.png"]


def test_the_hookless_turns_checked_request_is_what_is_copied(
        mk_bot, files_bot, rich, run_async, tmp_path):
    """The file list is the one the PreToolUse the hook checked showed,
    in a turn begun with no hook too, never the PostToolUse's."""
    r = _rig(mk_bot, files_bot, tmp_path)
    pic = _pic(tmp_path)
    other = _pic(tmp_path, "other.png")

    async def go():
        await r.prompt("one")
        await r.tool("t1")
        await r.stop("Done.")
        r.dequeue()
        r.line({"type": "assistant", "message": {"role": "assistant",
                "content": [{"type": "tool_use", "id": "toolu_c",
                             "name": "SendUserFile",
                             "input": {"files": [str(pic)]}}]}})
        await r.dg(hook_event_name="PreToolUse", tool_name="SendUserFile",
                   tool_use_id="toolu_c", tool_input={"files": [str(pic)]})
        await r.dg(hook_event_name="PostToolUse", tool_name="SendUserFile",
                   tool_use_id="toolu_c", tool_input={"files": [str(other)]},
                   tool_response={"attachments": []})
        await r.stop("Here.")
    run_async(go())
    [photo] = files_bot.file_calls()
    assert photo[2]["photo"].filename == "chart.png"


def test_a_replayed_unconfirmed_call_gets_one_line(
        mk_bot, files_bot, rich, run_async, tmp_path, monkeypatch):
    monkeypatch.setattr(outbox, "TRANSCRIPT_WAIT_SECONDS", 0.5)
    r = _rig(mk_bot, files_bot, tmp_path)
    pic = _pic(tmp_path)

    async def go():
        await r.prompt("one")
        await r.tool("t1")
        await r.stop("Done.")
        await r.send("toolu_n", [str(pic)])
        await r.send("toolu_n", [str(pic)], written=False)
        await r.stop("Sent.")
    run_async(go())
    assert len(_lines(files_bot)) == 1


@pytest.mark.parametrize("state", ["no-turn-ended", "no-transcript", "idle"])
def test_a_send_that_cannot_be_a_hookless_turns_is_dropped_at_once(
        mk_bot, files_bot, rich, run_async, tmp_path, state):
    """No turn of the conversation ended yet, no transcript noted, or the
    session is not working: nothing to wait for, dropped on arrival."""
    r = _rig(mk_bot, files_bot, tmp_path)
    pic = _pic(tmp_path)
    sess = r.sess
    sess.files_transcript = "" if state == "no-transcript" else str(r.tp)
    sess.files_turn_closed_wall = 0.0 if state == "no-turn-ended" else time.time()
    sess.status = Status.IDLE if state == "idle" else Status.BUSY
    inp = {"files": [str(pic)], "status": "normal"}

    async def go():
        await r.bot.notify(sess, "send_files_ran", {
            "tool_use_id": "toolu_d", "tool_input": inp, "cwd": r.cwd})
        return (list(outbox.OUTBOX.in_flight.get(NAME, ())),
                [x["file_name"] for x in _failed()])
    in_flight, failed = run_async(go())
    assert in_flight == [] and failed == ["chart.png"]


def test_forged_requests_on_an_idle_session_keep_only_the_newest(
        mk_bot, files_bot, rich, run_async, tmp_path):
    """The reviewer's probe (rev-iter4-001): forged SendUserFile PreToolUse
    datagrams after a real turn ended are kept as requests (the session
    may be starting a hookless turn), but no more than
    MAX_REQUESTS_PER_SESSION of them, the newest."""
    r = _rig(mk_bot, files_bot, tmp_path)
    flood = outbox.MAX_REQUESTS_PER_SESSION + 20

    async def go():
        await r.prompt("one")
        await r.tool("t1")
        await r.stop("Done.")
        for i in range(flood):
            await r.dg(hook_event_name="PreToolUse", tool_name="SendUserFile",
                       tool_use_id=f"toolu_{i}",
                       tool_input={"files": [f"/work/{i}.png"]})
    run_async(go())
    kept = [k[1] for k in outbox.OUTBOX.requests if k[0] == NAME]
    assert len(kept) == outbox.MAX_REQUESTS_PER_SESSION
    assert kept[-1] == f"toolu_{flood - 1}" and "toolu_0" not in kept


def test_forged_reports_on_a_working_session_share_one_wait(
        mk_bot, files_bot, rich, run_async, tmp_path, monkeypatch):
    """Many forged SendUserFile reports while the session works after a
    real turn ended: one transcript re-read loop for the session, not one
    per report; every report is still dropped and audited."""
    monkeypatch.setattr(outbox, "TRANSCRIPT_WAIT_SECONDS", 0.6)
    monkeypatch.setattr(outbox, "TRANSCRIPT_POLL_SECONDS", 0.1)
    reads: list = []
    real = outbox.hookless_turn_started

    def spy(path, since):
        reads.append(since)
        return real(path, since)
    monkeypatch.setattr(outbox, "hookless_turn_started", spy)
    r = _rig(mk_bot, files_bot, tmp_path)
    pic = _pic(tmp_path)
    reports = 12

    async def go():
        await r.prompt("one")
        await r.tool("t1")
        await r.stop("Done.")
        for i in range(reports):
            await r.send(f"toolu_f{i}", [str(pic)], written=False)
        await asyncio.sleep(0.05)       # the copy tasks reach their wait
        waits = len(outbox.OUTBOX.hookless_waits)
        await r.stop("")
        return waits
    assert run_async(go()) == 1
    # One loop of 0.6 s at 0.1 s: about 7 reads (twelve loops: about 84).
    assert 0 < len(reads) <= 12
    assert files_bot.file_calls() == []
    assert len(_failed()) == reports


def test_reports_past_the_joiner_cap_are_dropped_with_no_scan(
        mk_bot, files_bot, rich, run_async, tmp_path, monkeypatch):
    """More forged reports than may join one wait (rev-iter5-001): the
    extras are dropped at once like a failed wait, audited unverified,
    and never scan the transcript for the call."""
    monkeypatch.setattr(outbox, "TRANSCRIPT_WAIT_SECONDS", 0.5)
    monkeypatch.setattr(outbox, "TRANSCRIPT_POLL_SECONDS", 0.1)
    monkeypatch.setattr(outbox, "HOOKLESS_MAX_JOINERS", 3)
    scans: list = []
    real = outbox.transcript_call_files

    def spy(*a, **kw):
        scans.append(a[2])
        return real(*a, **kw)
    monkeypatch.setattr(outbox, "transcript_call_files", spy)
    r = _rig(mk_bot, files_bot, tmp_path)
    pic = _pic(tmp_path)
    reports = 7

    async def go():
        await r.prompt("one")
        await r.tool("t1")
        await r.stop("Done.")
        for i in range(reports):
            await r.send(f"toolu_f{i}", [str(pic)], written=False)
        await asyncio.sleep(0.05)       # the copy tasks reach their wait
        await r.stop("")
    run_async(go())
    # The one that started the wait and the three that joined it.
    assert sorted(scans) == [f"toolu_f{i}" for i in range(4)]
    assert files_bot.file_calls() == []
    assert len(_failed()) == reports
