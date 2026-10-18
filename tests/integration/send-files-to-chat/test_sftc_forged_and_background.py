"""Forged-call resistance and background delivery (iteration 2).

Black-box, through the daemon's control-socket entry (HookReceiver
datagrams into bot.notify) with a recording fake bot, as the ``turn``
fixture does, but able to drive several turns, hold an upload open, leave
a call out of Claude's transcript and send datagrams for an idle session.

Behaviour under test (entrypoints.md Events + Side effects, design.md
"Risks: a forged datagram", and the iteration-2 behaviour list):
- a file is sent only for a SendUserFile call that is in Claude's own
  transcript (under ~/.claude/projects) with the same files, and only
  while a turn is live;
- the delivery runs in the background after the finish, one batch at a
  time per session, in order, each turn's files replying to its own answer.
"""

from __future__ import annotations

import asyncio
import json
import sys

import pytest

from aipager import audit

kit = sys.modules["_sftc_kit"]
SESSION, SID, TOOL = kit.SESSION, kit.SID, kit.TOOL
SEND_METHODS = ("send_photo", "send_document", "send_media_group", "send_video",
                "send_audio", "send_animation")
# The transcript check waits for a lazily written call before it gives up
# (iteration-2 behaviour list: about 20 s). Tests that expect a give-up wait
# for its audit record, never longer than this.
GIVE_UP = 40.0


@pytest.fixture(autouse=True)
def _owner():
    kit.use_role("owner")


@pytest.fixture
def files(proj):
    for i in range(4):
        (proj / f"p{i}.png").write_bytes(kit.png(64 + i, 48))
    (proj / "b.pdf").write_bytes(b"%PDF-1.4 second")
    return proj


class Daemon:
    """One session's daemon side: the registry, the fake bot's call log,
    Claude's transcript, and helpers to play hook datagrams."""

    def __init__(self, mk_bot, home, chat=4242, label="frontend"):
        from unittest.mock import AsyncMock, MagicMock

        from aipager.dtach.hook_receiver import HookReceiver
        from aipager.state import SessionRegistry, Status, TrackedSession

        self.reg = SessionRegistry()
        self.sess = TrackedSession(name=SESSION, label=label, status=Status.IDLE)
        self.sess.scope_chat_id = chat
        self.reg._sessions[self.sess.name] = self.sess
        self.bot = mk_bot(self.reg)
        self.log: list = []
        self.gates: dict[str, asyncio.Event] = {}
        self.started: dict[str, int] = {}
        ids = iter(range(500, 100_000))
        b = self.bot._app.bot

        def recorder(name):
            async def _call(*a, **k):
                mid = next(ids)
                self.started[name] = self.started.get(name, 0) + 1
                gate = self.gates.get(name)
                if gate is not None:
                    await gate.wait()
                self.log.append((name, a, k, mid))
                if name == "send_media_group":
                    media = k.get("media", a[1] if len(a) > 1 else [])
                    return [MagicMock(message_id=mid + i) for i in range(len(media))]
                return MagicMock(message_id=mid)
            return AsyncMock(side_effect=_call)

        for m in ("send_message", *SEND_METHODS, "edit_message_text",
                  "delete_message", "set_message_reaction",
                  "edit_message_reply_markup", "pin_chat_message"):
            setattr(b, m, recorder(m))
        self.transcript = home / ".claude" / "projects" / "-proj" / f"{SID}.jsonl"
        self.transcript.parent.mkdir(parents=True, exist_ok=True)
        self._line({"type": "user", "message": {"content": kit.MARKERS["owner"]}})
        self.rec = HookReceiver(self.reg, self.bot.notify)
        self.cwd = str(home / "proj")
        self.n = 0

    # -- transcript ----------------------------------------------------------
    def _line(self, obj, path=None):
        with open(path or self.transcript, "a") as f:
            f.write(json.dumps(obj) + "\n")

    def write_call(self, tid, tin, *, name=TOOL, path=None, role="assistant"):
        self._line({"type": role, "message": {"role": role, "content": [{
            "type": "tool_use", "id": tid, "name": name, "input": tin}]}}, path)

    def write_answer(self, text):
        self._line({"type": "assistant", "message": {
            "role": "assistant", "content": [{"type": "text", "text": text}]}})

    # -- datagrams -------------------------------------------------------------
    async def dg(self, transcript=None, **kw):
        base = {"session": SESSION, "session_id": SID, "cwd": self.cwd,
                "transcript_path": str(transcript or self.transcript)}
        await self.rec._on_datagram(json.dumps({**base, **kw}).encode())

    async def prompt(self, text="send me the files"):
        await self.dg(hook_event_name="UserPromptSubmit", prompt=text)
        await asyncio.sleep(0.05)

    async def call(self, paths, *, in_transcript=True, transcript_files=None,
                   transcript=None, tid=None, caption=None):
        """One SendUserFile call: transcript line (unless left out), then
        PreToolUse and PostToolUse datagrams. Returns the tool_use id."""
        tid = tid or f"toolu_{self.n}"
        self.n += 1
        tin = {"files": paths, "status": "normal"}
        if caption is not None:
            tin["caption"] = caption
        if in_transcript:
            shown = dict(tin)
            if transcript_files is not None:
                shown["files"] = transcript_files
            self.write_call(tid, shown)
        await self.dg(transcript=transcript, hook_event_name="PreToolUse",
                      tool_name=TOOL, tool_use_id=tid, tool_input=tin)
        await asyncio.sleep(0.02)
        await self.dg(transcript=transcript, hook_event_name="PostToolUse",
                      tool_name=TOOL, tool_use_id=tid, tool_input=tin,
                      tool_response={"attachments": []})
        await asyncio.sleep(0.05)
        return tid

    async def stop(self, answer, *, transcript=None):
        if answer:
            self.write_answer(answer)
        await self.dg(transcript=transcript, hook_event_name="Stop",
                      last_assistant_message=answer or "")

    # -- waiting ---------------------------------------------------------------
    async def settle(self, quiet=0.6, limit=10.0):
        seen, still, waited = -1, 0.0, 0.0
        while waited < limit:
            await asyncio.sleep(0.1)
            waited += 0.1
            if len(self.log) == seen:
                still += 0.1
                if still >= quiet:
                    return
            else:
                seen, still = len(self.log), 0.0

    async def until(self, pred, limit):
        waited = 0.0
        while waited < limit:
            if pred():
                return True
            await asyncio.sleep(0.1)
            waited += 0.1
        return pred()

    # -- reading the log -------------------------------------------------------
    def sends(self):
        return [c for c in self.log if c[0] in SEND_METHODS]

    def message_id_of(self, text):
        for m, a, k, mid in self.log:
            if m == "send_message" and text in str(a[1] if len(a) > 1 else k.get("text", "")):
                return mid
        return None

    def texts(self):
        return [str(a[1] if len(a) > 1 else k.get("text", ""))
                for m, a, k, _ in self.log if m == "send_message"]


def _names(call):
    """The file names one send carries, in order."""
    m, a, k, _ = call
    if m == "send_media_group":
        items = [getattr(x, "media", None) for x in k.get("media", [])]
    else:
        items = [k.get(m.removeprefix("send_"))]
    out = []
    for f in items:
        out.append(getattr(f, "filename", None) or getattr(f, "name", None))
    return out


def _reply_target(call):
    k = call[2]
    target = k.get("reply_to_message_id")
    if target is None and k.get("reply_parameters") is not None:
        target = k["reply_parameters"].message_id
    return target


def _audit():
    p = audit.AUDIT_LOG_PATH
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def _failed(name):
    return [r for r in _audit()
            if r.get("action") == "FileFailed" and r.get("file_name") == name]


@pytest.fixture
def daemon(mk_bot, home, run_async):
    """``daemon(scenario)``: build a Daemon inside a fresh loop and run
    ``await scenario(d)``; returns the Daemon."""
    def _run(scenario):
        box = {}

        async def go():
            d = Daemon(mk_bot, home)
            box["d"] = d
            await scenario(d)
        run_async(go())
        return box["d"]
    return _run


# ---- positive control: the driver itself delivers a genuine call ------------

def test_control_a_genuine_call_is_sent(daemon, files):
    async def s(d):
        await d.prompt()
        await d.call([str(files / "b.pdf")])
        await d.stop("Here it is.")
        await d.settle()
    assert [c[0] for c in daemon(s).sends()] == ["send_document"]


def test_control_a_call_flushed_after_the_stop_is_still_sent(daemon, files):
    # Claude Code writes its transcript lazily: the call may land after Stop.
    async def s(d):
        await d.prompt()
        tin = {"files": [str(files / "b.pdf")], "status": "normal"}
        await d.dg(hook_event_name="PreToolUse", tool_name=TOOL,
                   tool_use_id="toolu_late", tool_input=tin)
        await asyncio.sleep(0.02)
        await d.dg(hook_event_name="PostToolUse", tool_name=TOOL,
                   tool_use_id="toolu_late", tool_input=tin,
                   tool_response={"attachments": []})
        await asyncio.sleep(0.05)
        await d.stop("Here it is.")
        await asyncio.sleep(1.5)
        d.write_call("toolu_late", tin)
        await d.until(lambda: d.sends(), GIVE_UP)
        await d.settle()
    assert [c[0] for c in daemon(s).sends()] == ["send_document"]


# ---- forged calls: not in Claude's transcript --------------------------------

def _forged_not_in_transcript(files):
    async def s(d):
        await d.prompt()
        await d.call([str(files / "b.pdf")], in_transcript=False)
        await d.stop("Here it is.")
        await d.until(lambda: _failed("b.pdf"), GIVE_UP)
        await d.settle()
    return s


def test_call_missing_from_the_transcript_sends_nothing(daemon, files):
    assert daemon(_forged_not_in_transcript(files)).sends() == []


def test_call_missing_from_the_transcript_is_audited_as_failed(daemon, files):
    daemon(_forged_not_in_transcript(files))
    assert len(_failed("b.pdf")) == 1


def test_call_missing_from_the_transcript_is_audited_as_unverified(daemon, files):
    daemon(_forged_not_in_transcript(files))
    assert [r.get("reason") for r in _failed("b.pdf")] == ["unverified"]


def test_call_missing_from_the_transcript_puts_no_line_in_the_chat(daemon, files):
    d = daemon(_forged_not_in_transcript(files))
    assert [x for x in d.texts() if "Couldn't send" in x] == []


def test_call_missing_from_the_transcript_leaves_no_copy(daemon, files):
    import os

    from aipager import config
    daemon(_forged_not_in_transcript(files))
    left = [f for _d, _s, fs in os.walk(config.OUTBOX_DIR) for f in fs]
    assert left == []


def test_transcript_call_naming_other_files_sends_nothing(daemon, files):
    async def s(d):
        await d.prompt()
        await d.call([str(files / "b.pdf")],
                     transcript_files=[str(files / "p0.png")])
        await d.stop("Here it is.")
        await d.until(lambda: _failed("b.pdf"), GIVE_UP)
        await d.settle()
    assert daemon(s).sends() == []


def test_transcript_call_naming_one_more_file_sends_nothing(daemon, files):
    async def s(d):
        await d.prompt()
        await d.call([str(files / "b.pdf")],
                     transcript_files=[str(files / "b.pdf"), str(files / "p0.png")])
        await d.stop("Here it is.")
        await d.until(lambda: _failed("b.pdf"), GIVE_UP)
        await d.settle()
    assert daemon(s).sends() == []


def test_transcript_call_with_another_id_sends_nothing(daemon, files):
    async def s(d):
        await d.prompt()
        d.write_call("toolu_other", {"files": [str(files / "b.pdf")],
                                     "status": "normal"})
        await d.call([str(files / "b.pdf")], in_transcript=False, tid="toolu_forged")
        await d.stop("Here it is.")
        await d.until(lambda: _failed("b.pdf"), GIVE_UP)
        await d.settle()
    assert daemon(s).sends() == []


def test_transcript_call_of_another_tool_sends_nothing(daemon, files):
    async def s(d):
        await d.prompt()
        d.write_call("toolu_0", {"files": [str(files / "b.pdf")], "status": "normal"},
                     name="Read")
        await d.call([str(files / "b.pdf")], in_transcript=False, tid="toolu_0")
        await d.stop("Here it is.")
        await d.until(lambda: _failed("b.pdf"), GIVE_UP)
        await d.settle()
    assert daemon(s).sends() == []


def test_call_written_as_a_user_line_sends_nothing(daemon, files):
    # Only Claude's own (assistant) lines count; a person can paste text.
    async def s(d):
        await d.prompt()
        d.write_call("toolu_0", {"files": [str(files / "b.pdf")], "status": "normal"},
                     role="user")
        await d.call([str(files / "b.pdf")], in_transcript=False, tid="toolu_0")
        await d.stop("Here it is.")
        await d.until(lambda: _failed("b.pdf"), GIVE_UP)
        await d.settle()
    assert daemon(s).sends() == []


def test_planted_transcript_outside_claudes_folder_sends_nothing(daemon, files, tmp_path):
    planted = tmp_path / "planted" / f"{SID}.jsonl"
    planted.parent.mkdir()

    async def s(d):
        tin = {"files": [str(files / "b.pdf")], "status": "normal"}
        d.write_call("toolu_0", tin, path=planted)
        await d.dg(transcript=planted, hook_event_name="UserPromptSubmit",
                   prompt="send me the files")
        await asyncio.sleep(0.05)
        await d.call([str(files / "b.pdf")], in_transcript=False, tid="toolu_0",
                     transcript=planted)
        await d.stop("Here it is.", transcript=planted)
        await d.until(lambda: _failed("b.pdf"), GIVE_UP)
        await d.settle()
    assert daemon(s).sends() == []


# ---- forged calls: no live turn ---------------------------------------------

def _idle_pair(files, *, with_stop):
    async def s(d):
        # No UserPromptSubmit: the session is idle. The call is even in
        # the transcript, so only the live-turn rule can stop it.
        await d.call([str(files / "b.pdf")])
        if with_stop:
            await d.stop("")
        await d.until(lambda: _failed("b.pdf"), 5.0)
        await d.settle(limit=4.0)
    return s


@pytest.mark.parametrize("with_stop", [False, True], ids=["no-stop", "with-stop"])
def test_pair_for_an_idle_session_sends_nothing(daemon, files, with_stop):
    assert daemon(_idle_pair(files, with_stop=with_stop)).sends() == []


def test_pair_for_an_idle_session_is_audited_as_failed(daemon, files):
    daemon(_idle_pair(files, with_stop=False))
    assert len(_failed("b.pdf")) == 1


def test_pair_after_the_turn_ended_sends_nothing_more(daemon, files):
    async def s(d):
        await d.prompt()
        await d.call([str(files / "b.pdf")])
        await d.stop("Here it is.")
        await d.settle()
        # The turn is over; a second, forged pair arrives.
        await d.call([str(files / "p0.png")])
        await d.stop("")
        await d.until(lambda: _failed("p0.png"), 5.0)
        await d.settle(limit=4.0)
    assert [c[0] for c in daemon(s).sends()] == ["send_document"]


def test_idle_pair_pointing_at_an_old_transcript_sends_nothing(daemon, files, home):
    # An old session's transcript, in Claude's own folder, holds a real call
    # this daemon never delivered. A forged pair for an idle session names
    # it; nothing has to be written to any transcript.
    old = home / ".claude" / "projects" / "-old" / "0000aaaa-old.jsonl"
    old.parent.mkdir(parents=True)

    async def s(d):
        tin = {"files": [str(files / "b.pdf")], "status": "normal"}
        d.write_call("toolu_old", tin, path=old)
        await d.dg(transcript=old, hook_event_name="PreToolUse", tool_name=TOOL,
                   tool_use_id="toolu_old", tool_input=tin)
        await asyncio.sleep(0.02)
        await d.dg(transcript=old, hook_event_name="PostToolUse", tool_name=TOOL,
                   tool_use_id="toolu_old", tool_input=tin,
                   tool_response={"attachments": []})
        await asyncio.sleep(0.05)
        await d.dg(transcript=old, hook_event_name="Stop",
                   last_assistant_message="")
        await d.settle(limit=6.0)
    assert daemon(s).sends() == []


def test_replay_of_a_delivered_call_sends_nothing_more(daemon, files):
    # A real call, delivered; once the turn ended, its exact datagrams (same
    # id, same files, still in the transcript) are played again.
    async def s(d):
        await d.prompt()
        tid = await d.call([str(files / "b.pdf")])
        await d.stop("Here it is.")
        await d.settle()
        tin = {"files": [str(files / "b.pdf")], "status": "normal"}
        await d.dg(hook_event_name="PreToolUse", tool_name=TOOL,
                   tool_use_id=tid, tool_input=tin)
        await asyncio.sleep(0.02)
        await d.dg(hook_event_name="PostToolUse", tool_name=TOOL,
                   tool_use_id=tid, tool_input=tin,
                   tool_response={"attachments": []})
        await asyncio.sleep(0.05)
        await d.stop("")
        await d.settle(limit=6.0)
    assert [c[0] for c in daemon(s).sends()] == ["send_document"]


# ---- background delivery ------------------------------------------------------

@pytest.mark.parametrize("first,second,order", [
    ("b.pdf", "p0.png", ["send_document", "send_photo"]),
    ("p0.png", "b.pdf", ["send_photo", "send_document"])],
    ids=["pdf-then-png", "png-then-pdf"])
def test_calls_of_one_turn_arrive_in_call_order(daemon, files, first, second, order):
    async def s(d):
        await d.prompt()
        await d.call([str(files / first)])
        await d.call([str(files / second)])
        await d.stop("Here it is.")
        await d.settle()
    assert [c[0] for c in daemon(s).sends()] == order


def test_album_items_keep_the_order_claude_gave(daemon, files):
    names = ["p2.png", "p0.png", "p3.png", "p1.png"]

    async def s(d):
        await d.prompt()
        await d.call([str(files / n) for n in names])
        await d.stop("Here it is.")
        await d.settle()
    d = daemon(s)
    assert [_names(c) for c in d.sends()] == [names]


def test_files_arrive_after_the_answer_even_while_the_upload_is_slow(daemon, files):
    async def s(d):
        d.gates["send_document"] = asyncio.Event()
        await d.prompt()
        await d.call([str(files / "b.pdf")])
        await d.stop("Here it is.")
        await d.until(lambda: d.started.get("send_document"), 5.0)
        await asyncio.sleep(0.3)
        d.gates["send_document"].set()
        await d.settle()
    d = daemon(s)
    order = [c[3] for c in d.log]
    sent = d.sends()
    assert sent and d.message_id_of("Here it is.") is not None and \
        order.index(d.message_id_of("Here it is.")) < order.index(sent[0][3])


def test_the_stop_is_not_held_by_a_slow_upload(daemon, files):
    took = {}

    async def s(d):
        d.gates["send_document"] = asyncio.Event()
        await d.prompt()
        await d.call([str(files / "b.pdf")])
        d.write_answer("Here it is.")
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await asyncio.wait_for(d.dg(hook_event_name="Stop",
                                    last_assistant_message="Here it is."), 30)
        took["stop"] = loop.time() - t0
        d.gates["send_document"].set()
        await d.settle()
    daemon(s)
    assert took["stop"] < 3.0


def test_a_later_turns_answer_is_not_held_by_an_earlier_upload(daemon, files):
    seen = {}

    async def s(d):
        d.gates["send_document"] = asyncio.Event()
        await d.prompt("first")
        await d.call([str(files / "b.pdf")])
        await d.stop("First answer.")
        await d.until(lambda: d.started.get("send_document"), 10.0)
        await d.prompt("second")
        await d.stop("Second answer.")
        await d.until(lambda: d.message_id_of("Second answer.") is not None, 10.0)
        seen["second_answered_while_upload_open"] = (
            d.message_id_of("Second answer.") is not None
            and not d.gates["send_document"].is_set())
        d.gates["send_document"].set()
        await d.settle()
    daemon(s)
    assert seen["second_answered_while_upload_open"] is True


def _two_turns_with_a_slow_first_upload(files):
    async def s(d):
        d.gates["send_photo"] = asyncio.Event()
        await d.prompt("first")
        await d.call([str(files / "p0.png")])
        await d.stop("First answer.")
        await d.until(lambda: d.started.get("send_photo"), 10.0)
        await d.prompt("second")
        await d.call([str(files / "p1.png")])
        await d.stop("Second answer.")
        await d.until(lambda: d.message_id_of("Second answer.") is not None, 10.0)
        await asyncio.sleep(0.8)
        d.peak_while_gated = d.started.get("send_photo", 0) + \
            d.started.get("send_media_group", 0)
        d.gates["send_photo"].set()
        await d.settle()
    return s


def test_one_batch_at_a_time_per_session(daemon, files):
    d = daemon(_two_turns_with_a_slow_first_upload(files))
    assert d.peak_while_gated == 1


def test_two_turns_batches_go_out_in_turn_order(daemon, files):
    d = daemon(_two_turns_with_a_slow_first_upload(files))
    assert [_names(c) for c in d.sends()] == [["p0.png"], ["p1.png"]]


def test_two_turns_batches_do_not_mix_into_one_album(daemon, files):
    d = daemon(_two_turns_with_a_slow_first_upload(files))
    assert [c[0] for c in d.sends()] == ["send_photo", "send_photo"]


def test_each_turns_files_reply_to_their_own_answer(daemon, files):
    d = daemon(_two_turns_with_a_slow_first_upload(files))
    assert [_reply_target(c) for c in d.sends()] == [
        d.message_id_of("First answer."), d.message_id_of("Second answer.")]


def test_each_turns_files_carry_their_own_caption(daemon, files):
    async def s(d):
        d.gates["send_photo"] = asyncio.Event()
        await d.prompt("first")
        await d.call([str(files / "p0.png")], caption="one")
        await d.stop("First answer.")
        await d.until(lambda: d.started.get("send_photo"), 10.0)
        await d.prompt("second")
        await d.call([str(files / "p1.png")], caption="two")
        await d.stop("Second answer.")
        await d.until(lambda: d.message_id_of("Second answer.") is not None, 10.0)
        d.gates["send_photo"].set()
        await d.settle()
    d = daemon(s)
    assert [c[2].get("caption") for c in d.sends()] == [
        "💬 frontend\none", "💬 frontend\ntwo"]


def test_two_quick_turns_each_get_their_own_files(daemon, files):
    async def s(d):
        for i, ans in enumerate(("First answer.", "Second answer.")):
            await d.prompt(f"turn {i}")
            await d.call([str(files / f"p{i}.png")])
            await d.stop(ans)
            await d.settle()
    d = daemon(s)
    assert [(_names(c), _reply_target(c)) for c in d.sends()] == [
        (["p0.png"], d.message_id_of("First answer.")),
        (["p1.png"], d.message_id_of("Second answer."))]


def test_a_failed_upload_does_not_stop_the_next_turns_files(daemon, files):
    from telegram.error import BadRequest

    async def s(d):
        b = d.bot._app.bot
        orig = b.send_document

        async def fail_once(*a, **k):
            await orig(*a, **k)
            raise BadRequest("Wrong file")
        from unittest.mock import AsyncMock
        b.send_document = AsyncMock(side_effect=fail_once)
        await d.prompt("first")
        await d.call([str(files / "b.pdf")])
        await d.stop("First answer.")
        await d.settle()
        await d.prompt("second")
        await d.call([str(files / "p1.png")])
        await d.stop("Second answer.")
        await d.settle()
    d = daemon(s)
    assert [c[0] for c in d.sends()] == ["send_document", "send_photo"]

