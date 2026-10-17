"""Fixtures for the black-box "files Claude sends" tests.

Written from spec.md, design.md and entrypoints.md only. Every test runs
against a throwaway HOME under ``tmp_path``; nothing reads or writes the
real home folder, the real /tmp control files or Telegram. No socket is
bound: the hook's datagrams are recorded by replacing ``socket.socket``.

People: @alice is the owner, @bob an admin, @carol a member (role
``user``) held to her root folder ``~/aipager-users/carol``.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import socket as socket_mod
import sys
from pathlib import Path

import pytest

from aipager import policy_snapshot as ps
from aipager.dtach import enforce

def _load_kit():
    name = "_sftc_kit"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).resolve().parent / "_kit.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


kit = _load_kit()
SESSION, SID, MARKERS, use_role, TOOL = (
    kit.SESSION, kit.SID, kit.MARKERS, kit.use_role, kit.TOOL)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home" / "op"
    h.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(ps, "snapshot_path",
                        lambda n: tmp_path / "rt" / f"{n}.policy.json")
    (tmp_path / "rt").mkdir()
    monkeypatch.delenv("CLAUDE_CODE_TMPDIR", raising=False)
    monkeypatch.delenv("TMPDIR", raising=False)
    assert os.path.expanduser("~") == str(h)
    return h


@pytest.fixture
def proj(home):
    """A project folder of the owner, under the home folder."""
    p = home / "proj"
    p.mkdir()
    (p / "report.pdf").write_bytes(b"%PDF-1.4 hello")
    return p


@pytest.fixture
def root(home):
    """Carol's root folder with a project and a sibling folder."""
    r = home / "aipager-users" / "carol"
    (r / "proj").mkdir(parents=True)
    (r / "other").mkdir()
    (r / "proj" / "shot.png").write_bytes(b"png-ish bytes")
    (r / "other" / "notes.md").write_text("notes")
    return r


@pytest.fixture
def outside(tmp_path):
    """A folder outside the (fake) home folder, like /srv or /tmp."""
    o = tmp_path / "srv"
    o.mkdir()
    (o / "x.png").write_bytes(b"png-ish bytes")
    return o


@pytest.fixture
def decide(tmp_path, home):
    """``decide(kind, tool, tool_input, cwd, prior=())`` -> enforce.decide's
    block descriptor. ``kind`` is owner / admin / user (a Telegram turn of
    that role; ``root`` kwarg for a member) or terminal (a turn typed in
    the terminal, with a member's rooted snapshot still on file)."""
    def _run(kind, tool, tool_input, cwd, *, root_dir=None, prior=()):
        if kind == "terminal":
            use_role("user", root_dir)
            first = "send it"
        else:
            use_role(kind, root_dir)
            first = MARKERS[kind]
        t = tmp_path / "t.jsonl"
        lines = [{"type": "user", "message": {"content": first}}, *prior]
        t.write_text("".join(json.dumps(x) + "\n" for x in lines))
        return enforce.decide({
            "hook_event_name": "PreToolUse", "session": SESSION,
            "session_id": SID, "cwd": str(cwd), "tool_name": tool,
            "tool_input": tool_input, "transcript_path": str(t),
            "tool_use_id": "toolu_01"})
    return _run


class _FakeDatagramSocket:
    def __init__(self, sink):
        self._sink = sink

    def sendto(self, data, _addr):
        try:
            self._sink.append(json.loads(data.decode()))
        except Exception:
            self._sink.append({"raw": data})

    def setblocking(self, *_a):
        pass

    def settimeout(self, *_a):
        pass

    def connect(self, *_a):
        pass

    def send(self, data):
        self.sendto(data, None)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


@pytest.fixture
def run_hook(monkeypatch, tmp_path, home):
    """Run the aipager-hook binary's main on a payload, as Claude Code
    would. Returns (stdout, datagrams)."""
    from aipager.dtach import notify_hook

    def _run(payload: dict) -> tuple[str, list[dict]]:
        sent: list[dict] = []
        monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
        monkeypatch.setattr(socket_mod, "socket",
                            lambda *_a, **_k: _FakeDatagramSocket(sent))
        monkeypatch.setenv("CLAUDE_DTACH_SESSION", SESSION)
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        out = io.StringIO()
        monkeypatch.setattr(sys, "stdout", out)
        try:
            notify_hook.main()
        except SystemExit:
            pass
        return out.getvalue(), sent
    return _run


SEND_METHODS = ("send_photo", "send_document", "send_media_group", "send_video",
                "send_audio", "send_animation")


class Turn:
    """One recorded turn: ``log`` is every Bot API call in order as
    (method, args, kwargs, returned message id); ``bot`` the fake bot."""

    def __init__(self, bot, sess, log, answer_text):
        self.bot, self.sess, self.log, self.answer_text = bot, sess, log, answer_text

    def calls(self, method):
        return [c for c in self.log if c[0] == method]

    def sends(self):
        return [c for c in self.log if c[0] in SEND_METHODS]

    def answer_id(self):
        for m, a, k, mid in self.log:
            text = a[1] if len(a) > 1 else k.get("text", "")
            if m == "send_message" and self.answer_text and self.answer_text in str(text):
                return mid
        return None

    def texts(self):
        out = []
        for m, a, k, _ in self.log:
            if m == "send_message":
                out.append(str(a[1] if len(a) > 1 else k.get("text", "")))
        return out


@pytest.fixture
def turn(mk_bot, home, tmp_path, run_async):
    """``turn(calls, answer="Here it is.", chat=4242, send_error=None)``
    drives one Telegram turn of SESSION through the daemon's control
    socket entry (HookReceiver datagrams -> bot.notify): a prompt, then
    each SendUserFile call as (files, caption, display, response_paths,
    between) with PreToolUse + PostToolUse datagrams, then the answer and
    Stop. ``between`` runs after PreToolUse (to change the file). Every
    Bot API call is recorded; nothing reaches Telegram."""
    from unittest.mock import AsyncMock, MagicMock

    from aipager.dtach.hook_receiver import HookReceiver
    from aipager.state import SessionRegistry, Status, TrackedSession

    def _run(calls, *, answer="Here it is.", chat=4242, send_error=None,
             cwd=None, label="frontend"):
        reg = SessionRegistry()
        sess = TrackedSession(name=SESSION, label=label, status=Status.IDLE)
        sess.scope_chat_id = chat
        reg._sessions[sess.name] = sess
        bot = mk_bot(reg)
        b = bot._app.bot
        log: list = []
        ids = iter(range(500, 10_000))

        def recorder(name, fail=None):
            async def _call(*a, **k):
                mid = next(ids)
                log.append((name, a, k, mid))
                if fail is not None:
                    raise fail
                if name == "send_media_group":
                    return [MagicMock(message_id=mid + i)
                            for i in range(len(k.get("media", a[1] if len(a) > 1 else [])))]
                return MagicMock(message_id=mid)
            return AsyncMock(side_effect=_call)

        b.send_message = recorder("send_message")
        for m in SEND_METHODS:
            setattr(b, m, recorder(m, send_error))
        for m in ("edit_message_text", "delete_message", "set_message_reaction",
                  "edit_message_reply_markup", "pin_chat_message"):
            setattr(b, m, recorder(m))
        # Where Claude Code keeps the session's transcript; the daemon reads
        # a SendUserFile call only from a transcript there (fix iteration 2).
        t = home / ".claude" / "projects" / "-proj" / f"{SID}.jsonl"
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text(json.dumps({"type": "user", "message": {
            "content": MARKERS["owner"]}}) + "\n")
        rec = HookReceiver(reg, bot.notify)
        where = str(cwd or home / "proj")
        base = {"session": SESSION, "session_id": SID, "cwd": where,
                "transcript_path": str(t)}

        async def dg(**kw):
            await rec._on_datagram(json.dumps({**base, **kw}).encode())

        async def go():
            import asyncio
            await dg(hook_event_name="UserPromptSubmit", prompt="send me the files")
            await asyncio.sleep(0.05)
            for i, c in enumerate(calls):
                files = c.get("files")
                tin = {"files": files, "status": "normal"}
                if c.get("caption") is not None:
                    tin["caption"] = c["caption"]
                if c.get("display") is not None:
                    tin["display"] = c["display"]
                tid = f"toolu_{i}"
                # Claude Code writes the call in the transcript.
                with open(t, "a") as f:
                    f.write(json.dumps({"type": "assistant", "message": {
                        "role": "assistant", "content": [{
                            "type": "tool_use", "id": tid, "name": TOOL,
                            "input": tin}]}}) + "\n")
                await dg(hook_event_name="PreToolUse", tool_name=TOOL,
                         tool_use_id=tid, tool_input=tin)
                await asyncio.sleep(0.02)
                if c.get("between"):
                    c["between"]()
                if c.get("refused") is not None:
                    await dg(hook_event_name="send_files_refused", tool_use_id=tid,
                             files=c["refused"])
                    await asyncio.sleep(0.05)
                    continue
                post_in = c.get("post_input", tin)
                resp = {"attachments": [{"path": p, "size": 1, "isImage": True}
                                        for p in c.get("response_paths", [])]}
                await dg(hook_event_name="PostToolUse", tool_name=TOOL,
                         tool_use_id=tid, tool_input=post_in, tool_response=resp)
                await asyncio.sleep(0.05)
                if c.get("after"):
                    res = c["after"]()
                    if asyncio.iscoroutine(res):
                        await res
            if answer:
                with open(t, "a") as f:
                    f.write(json.dumps({"type": "assistant", "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": answer}]}}) + "\n")
            await dg(hook_event_name="Stop", last_assistant_message=answer or "")
            # Wait until the turn has settled: no new Bot API call for 0.6 s.
            seen, quiet = -1, 0
            for _ in range(100):
                await asyncio.sleep(0.1)
                if len(log) == seen:
                    quiet += 1
                    if quiet >= 6:
                        break
                else:
                    seen, quiet = len(log), 0

        run_async(go())
        return Turn(bot, sess, log, answer)
    return _run
