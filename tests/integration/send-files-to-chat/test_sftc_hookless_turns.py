"""Turns Claude Code starts with no prompt hook (iteration 4).

Black-box, through the daemon's control-socket entry (HookReceiver
datagrams into bot.notify), the daemon's own prompt typing (the PTY write
is the faked boundary) and a monitor tick, with a recording fake bot.
Behaviour under test, from the iteration-4 behaviour list:
- a queued message Claude Code dequeues as its next turn (no
  UserPromptSubmit) sends its file once the transcript shows the dequeue
  after the last Stop; a Telegram message the daemon typed while a turn
  ran and popped at that turn's Stop reopens the files turn on its own;
- a turn a Stop hook keeps going ("Stop hook feedback:" isMeta line)
  sends its file;
- a forged Stop/PreToolUse/PostToolUse on an idle session, with no such
  line, sends nothing;
- a slash command the daemon types (/cost) opens no files turn;
- a typed prompt that starts nothing is closed by the monitor after 15 s,
  and a forged pair after that sends nothing;
- the translated "could not confirm" chat line appears only when Claude's
  transcript shows that very call, never for a forged call.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import sys
from datetime import datetime, timedelta, timezone

import pytest

from aipager import audit, preferences

kit = sys.modules["_sftc_kit"]
SESSION, SID, TOOL = kit.SESSION, kit.SID, kit.TOOL
CHAT = 4242
SEND_METHODS = ("send_photo", "send_document", "send_media_group", "send_video",
                "send_audio", "send_animation")
# The daemon waits about 20 s for a hookless turn's start line (and for a
# lazily written call) before it gives up.
GIVE_UP = 45.0
# A typed prompt that starts nothing is closed after 15 s idle.
UNSTARTED = 15.0
UNVERIFIED_EN = ("⚠️ Couldn't send {name}: aipager could not confirm that "
                 "Claude sent it in this turn.")


def _catalog(code: str, key: str) -> str:
    """The shipped translation of *key* (the catalog is data, read as is)."""
    from pathlib import Path

    import aipager
    p = Path(aipager.__file__).parent / "i18n" / "locales" / f"{code}.jsonl"
    for line in p.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("k") == key:
            return row["v"]
    raise KeyError(key)


@pytest.fixture(autouse=True)
def _owner():
    kit.use_role("owner")


@pytest.fixture
def files(proj):
    (proj / "b.pdf").write_bytes(b"%PDF-1.4 second")
    (proj / "c.pdf").write_bytes(b"%PDF-1.4 third")
    (proj / "p0.png").write_bytes(kit.png(64, 48))
    return proj


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


class Rig:
    """One session's daemon side: fake bot log, transcript, datagrams,
    typed prompts (the PTY write recorded) and monitor ticks."""

    _seq = itertools.count(1)

    def __init__(self, mk_bot, home, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        from aipager.dtach.hook_receiver import HookReceiver
        from aipager.state import SessionRegistry, Status, TrackedSession

        reg = SessionRegistry()
        sess = TrackedSession(name=SESSION, label="frontend", status=Status.IDLE)
        sess.scope_chat_id = CHAT
        reg._sessions[sess.name] = sess
        self.reg, self.sess = reg, sess
        self.bot = mk_bot(reg)
        self.log: list = []
        self.typed: list[str] = []
        ids = iter(range(500, 100_000))
        b = self.bot._app.bot

        def recorder(name):
            async def _call(*a, **k):
                mid = next(ids)
                self.log.append((name, a, k, mid))
                if name == "send_media_group":
                    media = k.get("media", a[1] if len(a) > 1 else [])
                    return [MagicMock(message_id=mid + i) for i in range(len(media))]
                return MagicMock(message_id=mid)
            return AsyncMock(side_effect=_call)

        for m in ("send_message", *SEND_METHODS, "edit_message_text",
                  "delete_message", "set_message_reaction",
                  "edit_message_reply_markup", "pin_chat_message",
                  "send_chat_action"):
            setattr(b, m, recorder(m))

        # The PTY: what the daemon types into the session is recorded.
        async def _type(_name, body, *a, **k):
            self.typed.append(body)
            return True

        async def _keys(*_a, **_k):
            return True

        async def _names():
            return [SESSION]
        monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", _type)
        monkeypatch.setattr("aipager.dtach.inject.send_keys", _keys)
        monkeypatch.setattr("aipager.dtach.inject.is_alive",
                            AsyncMock(return_value=True))
        monkeypatch.setattr("aipager.dtach.inject.list_sessions", _names)

        # Telegram's HTTP end of the rich-message path (answers go out
        # through it): recorded in the same log, each with a fresh id.
        async def _post(method, payload, **_kw):
            mid = next(ids)
            rich = payload.get("rich_message") or {}
            text = rich.get("markdown") or payload.get("text") or ""
            self.log.append(("rich:" + method, (payload.get("chat_id"), text),
                             payload, mid))
            return {"ok": True, "result": {"message_id": mid}}
        monkeypatch.setattr("aipager.bot.rich_message._post", _post)

        # The hook binary's session and (never bound) control socket.
        from aipager.dtach import notify_hook
        monkeypatch.setenv("CLAUDE_DTACH_SESSION", SESSION)
        monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(home / "nope.sock"))

        self.home = home
        self.cwd = str(home / "proj")
        self.transcript = home / ".claude" / "projects" / "-proj" / f"{SID}.jsonl"
        self.transcript.parent.mkdir(parents=True, exist_ok=True)
        self.line({"type": "user", "message": {"content": kit.MARKERS["owner"]}})
        self.rec = HookReceiver(reg, self.bot.notify)

    # -- transcript ----------------------------------------------------------
    def line(self, obj, *, stamp=...):
        row = dict(obj)
        row.setdefault("cwd", self.cwd)
        row["timestamp"] = (_stamp(datetime.now(timezone.utc))
                            if stamp is ... else stamp)
        with open(self.transcript, "a") as f:
            f.write(json.dumps(row) + "\n")

    def write_call(self, tid, tin):
        self.line({"type": "assistant", "message": {"role": "assistant",
                   "content": [{"type": "tool_use", "id": tid, "name": TOOL,
                                "input": tin}]}})

    def write_answer(self, text):
        self.line({"type": "assistant", "message": {
            "role": "assistant", "content": [{"type": "text", "text": text}]}})

    def queue_op(self, operation, content=None, *, stamp=...):
        """A queue-operation line, as Claude Code writes one the moment it
        queues or pops a message."""
        row = {"type": "queue-operation", "operation": operation,
               "sessionId": SID}
        if content is not None:
            row["content"] = content
        row["timestamp"] = (_stamp(datetime.now(timezone.utc))
                            if stamp is ... else stamp)
        with open(self.transcript, "a") as f:
            f.write(json.dumps(row) + "\n")

    def user_text(self, text, *, meta=False):
        row = {"type": "user", "message": {"role": "user", "content": text}}
        if meta:
            row["isMeta"] = True
        self.line(row)

    # -- datagrams -------------------------------------------------------------
    async def dg(self, **kw):
        base = {"session": SESSION, "session_id": SID, "cwd": self.cwd,
                "transcript_path": str(self.transcript),
                "_seq": next(self._seq)}
        await self.rec._on_datagram(json.dumps({**base, **kw}).encode())

    async def prompt(self, text="send me the files"):
        await self.dg(hook_event_name="UserPromptSubmit", prompt=text)
        await asyncio.sleep(0.05)

    async def hook_prompt(self, text):
        """Claude Code's UserPromptSubmit through the real aipager-hook
        binary (it reads the daemon's notes); its datagrams are captured
        in place of the control socket and handed to the daemon."""
        import io
        import socket as socket_mod

        from aipager.dtach import notify_hook

        sent: list = []

        class _Sock:
            def __init__(self, *_a, **_k):
                pass

            def sendto(self, data, _addr):
                sent.append(data)

            def send(self, data):
                sent.append(data)

            def __getattr__(self, _name):
                return lambda *a, **k: None

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        payload = {"hook_event_name": "UserPromptSubmit", "session_id": SID,
                   "cwd": self.cwd, "transcript_path": str(self.transcript),
                   "prompt": text}
        saved = (socket_mod.socket, sys.stdin, sys.stdout)
        socket_mod.socket = _Sock
        sys.stdin, sys.stdout = io.StringIO(json.dumps(payload)), io.StringIO()
        try:
            notify_hook.main()
        except SystemExit:
            pass
        finally:
            socket_mod.socket, sys.stdin, sys.stdout = saved
        for data in sent:
            await self.rec._on_datagram(data)
        await asyncio.sleep(0.05)
        return sent

    async def pair(self, tid, tin):
        await self.dg(hook_event_name="PreToolUse", tool_name=TOOL,
                      tool_use_id=tid, tool_input=tin)
        await asyncio.sleep(0.02)
        await self.dg(hook_event_name="PostToolUse", tool_name=TOOL,
                      tool_use_id=tid, tool_input=tin,
                      tool_response={"attachments": []})
        await asyncio.sleep(0.05)

    async def call(self, tid, paths, *, written=True, written_files=None):
        tin = {"files": paths, "status": "normal"}
        if written:
            self.write_call(tid, {"files": written_files or paths,
                                  "status": "normal"})
        await self.pair(tid, tin)
        return tin

    async def stop(self, answer):
        if answer:
            self.write_answer(answer)
        await self.dg(hook_event_name="Stop", last_assistant_message=answer or "")

    # -- the daemon typing and ticking -----------------------------------------
    _msg_ids = itertools.count(70)

    async def type_prompt(self, text):
        """The daemon types a Telegram message (with its message id, as
        every Telegram message has one) into the session."""
        return await self.bot._inject_prompt(self.sess, text, chat_id=CHAT,
                                             msg_id=next(self._msg_ids))

    async def monitor_tick(self):
        from aipager.session_monitor import SessionMonitor

        async def _notify(*_a, **_kw):
            return None
        await asyncio.wait_for(SessionMonitor(self.reg, _notify)._scan(), timeout=5)

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

    @staticmethod
    async def until(pred, limit):
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

    def texts(self):
        """Every message sent to the chat (plain or rich), in order."""
        return [str(a[1] if len(a) > 1 else k.get("text", ""))
                for m, a, k, _ in self.log
                if m == "send_message" or m.startswith("rich:send")]

    def message_id_of(self, needle):
        for m, a, k, mid in self.log:
            if m != "send_message" and not m.startswith("rich:send"):
                continue
            text = a[1] if len(a) > 1 else k.get("text", "")
            if needle in str(text):
                return mid
        return None


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


def _sent(name):
    return [r for r in _audit()
            if r.get("action") == "FileSent" and r.get("file_name") == name]


@pytest.fixture
def rig(mk_bot, home, run_async, monkeypatch):
    """``rig(scenario)``: build a Rig in a fresh loop, ``await
    scenario(r)``, finish every task it left while the test's fixtures
    are still in place, and return the Rig."""
    def _run(scenario):
        box = {}

        async def go():
            r = Rig(mk_bot, home, monkeypatch)
            box["r"] = r
            await scenario(r)
        run_async(go())
        run_async.close()
        return box["r"]
    return _run


async def _first_turn(r):
    await r.prompt("first")
    await r.stop("First answer.")
    await r.settle(limit=3.0)


async def _done(r, name):
    await r.until(lambda: r.sends() or _failed(name), GIVE_UP)
    await r.settle()


# ---- a queued message dequeued as the next turn ------------------------------------

def _dequeued(files, *, dequeue=True, op="dequeue", dequeue_age=None):
    """Turn 1 runs and stops. Claude Code then pops a queued message as its
    next turn: a queue-operation line and the message, no prompt hook; the
    turn sends b.pdf and stops."""
    async def s(r):
        await _first_turn(r)
        if dequeue:
            stamp = ... if dequeue_age is None else _stamp(
                datetime.now(timezone.utc) - timedelta(seconds=dequeue_age))
            r.queue_op(op, stamp=stamp)
        r.user_text("send me the pdf too")
        await r.call("toolu_q", [str(files / "b.pdf")])
        await r.stop("Second answer.")
        await _done(r, "b.pdf")
    return s


def test_a_queued_message_dequeued_as_the_next_turn_sends_its_file(rig, files):
    assert [c[0] for c in rig(_dequeued(files)).sends()] == ["send_document"]


def test_a_dequeued_turns_file_replies_to_that_turns_answer(rig, files):
    r = rig(_dequeued(files))
    assert [_reply_target(c) for c in r.sends()] == [r.message_id_of("Second answer.")]


def test_a_dequeued_turns_file_is_audited_as_sent(rig, files):
    rig(_dequeued(files))
    assert len(_sent("b.pdf")) == 1


@pytest.mark.parametrize("op", ["enqueue", "remove", "popAll"])
def test_another_queue_operation_after_the_stop_is_not_a_turn_start(rig, files, op):
    assert rig(_dequeued(files, op=op)).sends() == []


def test_a_dequeue_written_well_before_the_last_stop_does_not_count(rig, files):
    # Stamped a minute before turn 1 ended: an older turn's start.
    assert rig(_dequeued(files, dequeue_age=60)).sends() == []


# ---- a Telegram message the daemon typed while a turn ran --------------------------

def _typed_while_busy(files, *, dequeue_line):
    """The daemon types a Telegram message while turn 1 runs; Claude Code
    queues it (its prompt hook fires at submit) and pops it at turn 1's
    Stop as its next turn, which fires no prompt hook."""
    async def s(r):
        await r.prompt("first")
        r.type_ok = await r.type_prompt("send me the pdf")
        body = r.typed[-1] if r.typed else "send me the pdf"
        r.queue_op("enqueue", content=body)
        r.ups = await r.hook_prompt(body)
        await r.stop("First answer.")
        await r.settle(limit=3.0)
        if dequeue_line:
            r.queue_op("dequeue")
        r.user_text(body)
        await r.call("toolu_t", [str(files / "b.pdf")])
        await r.stop("Second answer.")
        await _done(r, "b.pdf")
    return s


def test_control_the_daemon_typed_the_queued_message(rig, files):
    r = rig(_typed_while_busy(files, dequeue_line=True))
    assert bool(r.type_ok) and any("send me the pdf" in t for t in r.typed)


def test_control_the_queued_messages_prompt_hook_reached_the_daemon(rig, files):
    r = rig(_typed_while_busy(files, dequeue_line=True))
    assert any(b"UserPromptSubmit" in d for d in r.ups)


@pytest.mark.parametrize("dequeue_line", [True, False],
                         ids=["with-dequeue-line", "daemon-typed-only"])
def test_a_telegram_message_queued_behind_a_turn_sends_its_file(
        rig, files, dequeue_line):
    r = rig(_typed_while_busy(files, dequeue_line=dequeue_line))
    assert [c[0] for c in r.sends()] == ["send_document"]


def test_a_telegram_message_queued_behind_a_turn_replies_to_its_answer(rig, files):
    r = rig(_typed_while_busy(files, dequeue_line=False))
    assert [_reply_target(c) for c in r.sends()] == [r.message_id_of("Second answer.")]


# ---- a turn a Stop hook keeps going -------------------------------------------------

def _stop_hook_turn(files, *, text="Stop hook feedback:\nThe tests are not run yet.",
                    meta=True):
    async def s(r):
        await r.prompt("first")
        await r.stop("First answer.")
        await r.settle(limit=3.0)
        # A Stop hook blocked: Claude Code adds its reason as a meta line
        # and carries on with no prompt hook.
        r.user_text(text, meta=meta)
        await r.call("toolu_h", [str(files / "b.pdf")])
        await r.stop("Second answer.")
        await _done(r, "b.pdf")
    return s


def test_a_stop_hook_continuation_sends_its_file(rig, files):
    assert [c[0] for c in rig(_stop_hook_turn(files)).sends()] == ["send_document"]


def test_a_stop_hook_continuations_file_replies_to_its_answer(rig, files):
    r = rig(_stop_hook_turn(files))
    assert [_reply_target(c) for c in r.sends()] == [r.message_id_of("Second answer.")]


@pytest.mark.parametrize("kw", [
    {"meta": False},
    {"text": "Note: Stop hook feedback:\nlater in the text"},
    {"text": "PreToolUse hook feedback:\nsomething"},
], ids=["not-meta", "prefix-not-at-start", "another-hooks-feedback"])
def test_a_line_that_is_not_a_stop_hooks_feedback_is_not_a_turn_start(rig, files, kw):
    assert rig(_stop_hook_turn(files, **kw)).sends() == []


# ---- forged hooks on an idle session ---------------------------------------------

def _forged_after_a_turn(files, *, call_in_transcript, forged_stop=True):
    async def s(r):
        await _first_turn(r)
        if forged_stop:
            await r.stop("")
        await r.call("toolu_f", [str(files / "b.pdf")], written=call_in_transcript)
        await r.stop("")
        await r.until(lambda: _failed("b.pdf"), GIVE_UP)
        await r.settle()
    return s


@pytest.mark.parametrize("call_in_transcript", [False, True],
                         ids=["call-not-in-transcript", "call-in-transcript"])
@pytest.mark.parametrize("forged_stop", [True, False],
                         ids=["stop-pre-post", "pre-post"])
def test_forged_hooks_with_no_turn_start_on_an_idle_session_send_nothing(
        rig, files, call_in_transcript, forged_stop):
    r = rig(_forged_after_a_turn(files, call_in_transcript=call_in_transcript,
                                 forged_stop=forged_stop))
    assert r.sends() == []


def test_forged_hooks_with_no_turn_start_are_audited_as_failed(rig, files):
    rig(_forged_after_a_turn(files, call_in_transcript=False))
    assert len(_failed("b.pdf")) == 1


# ---- a slash command the daemon types ----------------------------------------------

def _typed_then_pair(files, text, *, wait=0.0, tick=False):
    """Turn 1 ends; the daemon types *text* into the idle session and no
    prompt hook follows (a /cost runs no turn). After *wait* seconds (and
    a monitor tick if *tick*), a PreToolUse/PostToolUse pair arrives whose
    call is even in the transcript, freshly stamped."""
    async def s(r):
        await _first_turn(r)
        r.type_ok = await r.type_prompt(text)
        await asyncio.sleep(0.1)
        if wait:
            await asyncio.sleep(wait)
        if tick:
            await r.monitor_tick()
            await asyncio.sleep(0.1)
        await r.call("toolu_s", [str(files / "b.pdf")])
        await r.stop("Here it is.")
        await r.until(lambda: r.sends() or _failed("b.pdf"), GIVE_UP)
        await r.settle()
    return s


def test_control_the_slash_command_was_typed(rig, files):
    r = rig(_typed_then_pair(files, "/cost"))
    assert bool(r.type_ok) and any("/cost" in t for t in r.typed)


def test_control_a_typed_prompt_opens_a_files_turn(rig, files):
    r = rig(_typed_then_pair(files, "send me the pdf"))
    assert [c[0] for c in r.sends()] == ["send_document"]


def test_a_typed_slash_command_opens_no_files_turn(rig, files):
    assert rig(_typed_then_pair(files, "/cost")).sends() == []


# ---- the 15 s idle close -----------------------------------------------------------

def test_control_a_typed_prompt_still_open_after_15s_without_a_tick_sends(rig, files):
    r = rig(_typed_then_pair(files, "send me the pdf", wait=UNSTARTED + 1.5))
    assert [c[0] for c in r.sends()] == ["send_document"]


def test_control_a_monitor_tick_before_15s_keeps_the_turn(rig, files):
    r = rig(_typed_then_pair(files, "send me the pdf", wait=2.0, tick=True))
    assert [c[0] for c in r.sends()] == ["send_document"]


def test_after_the_idle_close_a_forged_pair_sends_nothing(rig, files):
    r = rig(_typed_then_pair(files, "send me the pdf", wait=UNSTARTED + 1.5,
                             tick=True))
    assert r.sends() == []


# ---- the "could not confirm" line --------------------------------------------------

def _mismatched_in_a_prompted_turn(files):
    """A live prompted turn whose call Claude's transcript shows with
    another file list (c.pdf) than the hook's (b.pdf)."""
    async def s(r):
        await r.prompt()
        await r.call("toolu_m", [str(files / "b.pdf")],
                     written_files=[str(files / "c.pdf")])
        await r.stop("Here it is.")
        await r.until(lambda: _failed("b.pdf") or _failed("c.pdf") or r.sends(),
                      GIVE_UP)
        await r.settle(quiet=1.0)
    return s


def _forged_in_a_prompted_turn(files):
    async def s(r):
        await r.prompt()
        await r.call("toolu_g", [str(files / "b.pdf")], written=False)
        await r.stop("Here it is.")
        await r.until(lambda: _failed("b.pdf"), GIVE_UP)
        await r.settle(quiet=1.0)
    return s


_BIDI = dict.fromkeys(map(ord, "\u200e\u200f\u2066\u2067\u2068\u2069"))


def _unverified_lines(r):
    """The chat's warning lines, with the direction marks and isolates the
    translation layer adds around a name stripped (the words are tested)."""
    return [t.translate(_BIDI) for t in r.texts() if "⚠️" in t]


def test_a_mismatched_call_in_a_prompted_turn_sends_no_file(rig, files):
    assert rig(_mismatched_in_a_prompted_turn(files)).sends() == []


def test_a_mismatched_call_gets_the_english_line_naming_claudes_file(rig, files):
    r = rig(_mismatched_in_a_prompted_turn(files))
    assert _unverified_lines(r) == [UNVERIFIED_EN.format(name="c.pdf")]


def test_a_mismatched_call_gets_the_persian_line_in_a_persian_chat(rig, files):
    preferences.set_ui_language(CHAT, "fa")
    r = rig(_mismatched_in_a_prompted_turn(files))
    want = _catalog("fa", "outbox.failed.unverified").replace("{name}", "c.pdf")
    assert _unverified_lines(r) == [want]


def test_control_the_persian_line_is_not_the_english_one(files):
    assert _catalog("fa", "outbox.failed.unverified") != UNVERIFIED_EN


def test_a_real_hookless_call_with_no_start_gets_the_english_line(rig, files):
    r = rig(_forged_after_a_turn(files, call_in_transcript=True))
    assert _unverified_lines(r) == [UNVERIFIED_EN.format(name="b.pdf")]


def test_a_real_hookless_call_with_no_start_gets_the_persian_line(rig, files):
    preferences.set_ui_language(CHAT, "fa")
    r = rig(_forged_after_a_turn(files, call_in_transcript=True))
    want = _catalog("fa", "outbox.failed.unverified").replace("{name}", "b.pdf")
    assert _unverified_lines(r) == [want]


@pytest.mark.parametrize("lang", ["en", "fa"])
def test_a_forged_call_in_a_prompted_turn_gets_no_line(rig, files, lang):
    preferences.set_ui_language(CHAT, lang)
    assert _unverified_lines(rig(_forged_in_a_prompted_turn(files))) == []


@pytest.mark.parametrize("lang", ["en", "fa"])
def test_a_forged_hookless_call_gets_no_line(rig, files, lang):
    preferences.set_ui_language(CHAT, lang)
    r = rig(_forged_after_a_turn(files, call_in_transcript=False))
    assert _unverified_lines(r) == []


def test_a_sent_file_gets_no_unverified_line(rig, files):
    r = rig(_dequeued(files))
    assert _unverified_lines(r) == []
