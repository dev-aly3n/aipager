"""Which SendUserFile calls may send a file (iteration 3).

Black-box, through the daemon's control-socket entry (HookReceiver
datagrams into bot.notify) with a recording fake bot. Behaviour under test,
from the iteration-3 behaviour list:
- a call counts only in a turn a real prompt opened (a parent
  UserPromptSubmit or a typed prompt); a tool call's hook never opens one;
- each tool_use_id is copied once, and that survives a daemon restart;
- the call is looked for only in the transcript the turn's prompt named;
- the matching line is timestamped no earlier than the turn's start and
  carries the turn's cwd;
- a background agent's call written to the transcript late, while its job
  is open, is still delivered;
- during a mute, a newer turn's files never go out before an older turn's.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import sys
from datetime import datetime, timedelta, timezone

import pytest

from aipager import audit

kit = sys.modules["_sftc_kit"]
SESSION, SID, TOOL = kit.SESSION, kit.SID, kit.TOOL
CHAT = 4242
AGENT = "ab2ae82400fc97e4c"
SEND_METHODS = ("send_photo", "send_document", "send_media_group", "send_video",
                "send_audio", "send_animation")
# The transcript check waits about 20 s for a lazily written call before it
# gives up; a test that expects a give-up waits for its audit record.
GIVE_UP = 40.0


@pytest.fixture(autouse=True)
def _owner():
    kit.use_role("owner")


@pytest.fixture
def files(proj):
    for i in range(3):
        (proj / f"p{i}.png").write_bytes(kit.png(64 + i, 48))
    (proj / "b.pdf").write_bytes(b"%PDF-1.4 second")
    return proj


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


class Rig:
    """One session's daemon side over *reg* (a fresh registry, or one
    loaded from disk after a restart): fake bot log, transcript, datagrams."""

    _seq = itertools.count(1)

    def __init__(self, mk_bot, home, reg=None, label="frontend"):
        from unittest.mock import AsyncMock, MagicMock

        from aipager.dtach.hook_receiver import HookReceiver
        from aipager.state import SessionRegistry, Status, TrackedSession

        if reg is None:
            reg = SessionRegistry()
            sess = TrackedSession(name=SESSION, label=label, status=Status.IDLE)
            sess.scope_chat_id = CHAT
            reg._sessions[sess.name] = sess
        self.reg = reg
        self.sess = reg.get(SESSION)
        self.bot = mk_bot(reg)
        self.log: list = []
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
        self.home = home
        self.cwd = str(home / "proj")
        self.transcript = home / ".claude" / "projects" / "-proj" / f"{SID}.jsonl"
        self.transcript.parent.mkdir(parents=True, exist_ok=True)
        if not self.transcript.exists():
            self.line({"type": "user", "message": {
                "content": kit.MARKERS["owner"]}})
        self.rec = HookReceiver(reg, self.bot.notify)

    # -- transcript ----------------------------------------------------------
    def line(self, obj, *, path=None, cwd=..., stamp=...):
        """Append one transcript line as Claude Code writes it. ``cwd`` and
        ``stamp`` default to the session's folder and now; None leaves the
        field out."""
        row = dict(obj)
        row["cwd"] = self.cwd if cwd is ... else cwd
        row["timestamp"] = (_stamp(datetime.now(timezone.utc))
                            if stamp is ... else stamp)
        row = {k: v for k, v in row.items() if v is not None}
        with open(path or self.transcript, "a") as f:
            f.write(json.dumps(row) + "\n")

    def write_call(self, tid, tin, **kw):
        self.line({"type": "assistant", "message": {"role": "assistant",
                   "content": [{"type": "tool_use", "id": tid, "name": TOOL,
                                "input": tin}]}}, **kw)

    def write_answer(self, text):
        self.line({"type": "assistant", "message": {
            "role": "assistant", "content": [{"type": "text", "text": text}]}})

    # -- datagrams -------------------------------------------------------------
    async def dg(self, transcript=None, **kw):
        # ``_seq`` keeps two otherwise identical datagrams (a replay) from
        # being dropped by the receiver's byte-identical duplicate filter, so
        # a replay test exercises the file rules, not that filter.
        base = {"session": SESSION, "session_id": SID, "cwd": self.cwd,
                "transcript_path": str(transcript or self.transcript),
                "_seq": next(self._seq)}
        await self.rec._on_datagram(json.dumps({**base, **kw}).encode())

    async def prompt(self, text="send me the files", **kw):
        await self.dg(hook_event_name="UserPromptSubmit", prompt=text, **kw)
        await asyncio.sleep(0.05)

    async def pair(self, tid, tin, *, transcript=None, **extra):
        await self.dg(transcript=transcript, hook_event_name="PreToolUse",
                      tool_name=TOOL, tool_use_id=tid, tool_input=tin, **extra)
        await asyncio.sleep(0.02)
        await self.dg(transcript=transcript, hook_event_name="PostToolUse",
                      tool_name=TOOL, tool_use_id=tid, tool_input=tin,
                      tool_response={"attachments": []}, **extra)
        await asyncio.sleep(0.05)

    async def call(self, tid, paths, *, caption=None, written=True, **line_kw):
        tin = {"files": paths, "status": "normal"}
        if caption is not None:
            tin["caption"] = caption
        if written:
            self.write_call(tid, tin, **line_kw)
        await self.pair(tid, tin)
        return tin

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


def _names(call):
    m, a, k, _ = call
    if m == "send_media_group":
        items = [getattr(x, "media", None) for x in k.get("media", [])]
    else:
        items = [k.get(m.removeprefix("send_"))]
    return [getattr(f, "filename", None) or getattr(f, "name", None) for f in items]


def _audit():
    p = audit.AUDIT_LOG_PATH
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def _failed(name):
    return [r for r in _audit()
            if r.get("action") == "FileFailed" and r.get("file_name") == name]


@pytest.fixture
def rig(mk_bot, home, run_async):
    """``rig(scenario)``: build a Rig in a fresh loop and ``await
    scenario(r)``; returns the Rig."""
    def _run(scenario):
        box = {}

        async def go():
            r = Rig(mk_bot, home)
            box["r"] = r
            await scenario(r)
        run_async(go())
        # Whatever the scenario left running (a flush, a delivery) is
        # cancelled and run to its end NOW, while every fixture of the test
        # (rich_api's Telegram double above all) is still in place: left to
        # run_async's teardown, it ran after those were undone, and a held
        # answer's send reached the suite's real-Telegram guard.
        run_async.close()
        return box["r"]
    return _run


# ---- positive control on this driver -----------------------------------------

def test_control_a_call_in_a_prompted_turn_is_sent(rig, files):
    async def s(r):
        await r.prompt()
        await r.call("toolu_c", [str(files / "b.pdf")])
        await r.stop("Here it is.")
        await r.settle()
    assert [c[0] for c in rig(s).sends()] == ["send_document"]


# ---- no prompt, no turn ---------------------------------------------------------

def _forged_idle(files, *, other_tool_first=False, as_agent=False,
                 agent_prompt=False):
    async def s(r):
        extra = {}
        if as_agent:
            await r.dg(hook_event_name="SubagentStart", agent_id=AGENT,
                       agent_type="Explore")
            extra = {"agent_id": AGENT, "agent_type": "Explore"}
        if agent_prompt:
            # A UserPromptSubmit that carries an agent_id is a subagent's,
            # never the parent's prompt.
            await r.prompt(agent_id=AGENT, agent_type="Explore")
        if other_tool_first:
            await r.dg(hook_event_name="PreToolUse", tool_name="Bash",
                       tool_use_id="toolu_b", tool_input={"command": "ls"})
            await r.dg(hook_event_name="PostToolUse", tool_name="Bash",
                       tool_use_id="toolu_b", tool_input={"command": "ls"},
                       tool_response={"stdout": ""})
        tin = {"files": [str(files / "b.pdf")], "status": "normal"}
        # The call is even in the transcript, freshly stamped, same folder.
        r.write_call("toolu_f", tin)
        await r.pair("toolu_f", tin, **extra)
        await r.stop("Done.")
        await r.until(lambda: _failed("b.pdf"), 8.0)
        await r.settle(limit=4.0)
    return s


@pytest.mark.parametrize("kw", [
    {}, {"other_tool_first": True}, {"as_agent": True}, {"agent_prompt": True},
], ids=["pre-post-stop", "after-another-tool", "as-a-subagent",
        "after-a-subagents-prompt"])
def test_forged_hooks_on_an_idle_session_send_nothing(rig, files, kw):
    assert rig(_forged_idle(files, **kw)).sends() == []


def test_forged_hooks_on_an_idle_session_are_audited_as_failed(rig, files):
    rig(_forged_idle(files))
    assert len(_failed("b.pdf")) == 1


# ---- only the turn's own transcript ---------------------------------------------

def _other_transcript(files, home, *, stop_names_it):
    other = home / ".claude" / "projects" / "-proj" / "0000aaaa-bbbb-other.jsonl"

    async def s(r):
        await r.prompt()
        tin = {"files": [str(files / "b.pdf")], "status": "normal"}
        # Another session's transcript, in Claude's own folder, freshly
        # stamped and from the same folder: only the datagram names it.
        r.write_call("toolu_o", tin, path=other)
        await r.pair("toolu_o", tin, transcript=other)
        await r.stop("Here it is.", transcript=other if stop_names_it else None)
        await r.until(lambda: _failed("b.pdf"), GIVE_UP)
        await r.settle()
    return s


@pytest.mark.parametrize("stop_names_it", [False, True],
                         ids=["call-names-it", "call-and-stop-name-it"])
def test_a_call_naming_another_sessions_transcript_sends_nothing(
        rig, files, home, stop_names_it):
    assert rig(_other_transcript(files, home, stop_names_it=stop_names_it)
               ).sends() == []


def test_a_call_naming_another_sessions_transcript_is_audited_as_failed(
        rig, files, home):
    rig(_other_transcript(files, home, stop_names_it=False))
    assert len(_failed("b.pdf")) == 1


# ---- the line must be from this turn and this folder ----------------------------

def _line_variant(files, home, **line_kw):
    async def s(r):
        await r.prompt()
        await r.call("toolu_l", [str(files / "b.pdf")], **line_kw)
        await r.stop("Here it is.")
        await r.until(lambda: _failed("b.pdf"), GIVE_UP)
        await r.settle()
    return s


def _variants(home):
    long_ago = datetime.now(timezone.utc) - timedelta(minutes=10)
    return {
        "stamped-before-the-turn": {"stamp": _stamp(long_ago)},
        "stamped-an-hour-before": {"stamp": _stamp(long_ago - timedelta(hours=1))},
        "no-timestamp": {"stamp": None},
        "unreadable-timestamp": {"stamp": "yesterday at noon"},
        "another-cwd": {"cwd": str(home / "elsewhere")},
        "a-sub-folder-cwd": {"cwd": str(home / "proj" / "sub")},
        "no-cwd": {"cwd": None},
    }


@pytest.mark.parametrize("variant", [
    "stamped-before-the-turn", "stamped-an-hour-before", "no-timestamp",
    "unreadable-timestamp", "another-cwd", "a-sub-folder-cwd", "no-cwd"])
def test_a_call_line_not_from_this_turn_and_folder_sends_nothing(
        rig, files, home, variant):
    assert rig(_line_variant(files, home, **_variants(home)[variant])).sends() == []


def test_a_call_line_stamped_before_the_turn_is_audited_as_failed(rig, files, home):
    rig(_line_variant(files, home, **_variants(home)["stamped-before-the-turn"]))
    assert len(_failed("b.pdf")) == 1


def test_control_a_cwd_spelled_with_a_trailing_slash_is_the_same_folder(rig, files, home):
    # The folder match is on the normalised path.
    async def s(r):
        await r.prompt()
        await r.call("toolu_s", [str(files / "b.pdf")], cwd=str(home / "proj") + "/")
        await r.stop("Here it is.")
        await r.until(lambda: r.sends() or _failed("b.pdf"), GIVE_UP)
        await r.settle()
    assert [c[0] for c in rig(s).sends()] == ["send_document"]


# ---- once per tool_use_id -------------------------------------------------------

def test_the_same_call_id_twice_in_one_turn_sends_once(rig, files):
    async def s(r):
        await r.prompt()
        await r.call("toolu_dup", [str(files / "p0.png")], caption="one")
        await r.call("toolu_dup", [str(files / "p0.png")], caption="two")
        await r.stop("Here it is.")
        await r.until(lambda: _failed("p0.png"), GIVE_UP)
        await r.settle()
    assert len(rig(s).sends()) == 1


def _id_in_a_later_turn(files, *, same_id):
    async def s(r):
        await r.prompt("first")
        await r.call("toolu_1", [str(files / "p0.png")], caption="one")
        await r.stop("First answer.")
        await r.settle()
        await r.prompt("second")
        await r.call("toolu_1" if same_id else "toolu_2",
                     [str(files / "p0.png")], caption="two")
        await r.stop("Second answer.")
        await r.until(lambda: len(r.sends()) >= 2 or _failed("p0.png"), GIVE_UP)
        await r.settle()
    return s


def test_control_a_new_call_id_in_a_later_turn_is_sent(rig, files):
    assert len(rig(_id_in_a_later_turn(files, same_id=False)).sends()) == 2


def test_the_same_call_id_in_a_later_turn_sends_nothing_more(rig, files):
    assert len(rig(_id_in_a_later_turn(files, same_id=True)).sends()) == 1


def _across_restart(mk_bot, home, run_async, files, *, same_id):
    """Turn 1 on daemon A delivers toolu_1; daemon A saves and is gone;
    daemon B loads the registry from disk and runs turn 2."""
    from aipager.state import SessionRegistry
    out = {}

    async def first():
        r = Rig(mk_bot, home)
        await r.prompt("first")
        await r.call("toolu_1", [str(files / "p0.png")], caption="one")
        await r.stop("First answer.")
        await r.settle()
        r.reg.save()
        out["a"] = r.sends()
    run_async(first())

    async def second():
        reg = SessionRegistry()
        reg.load()
        r = Rig(mk_bot, home, reg=reg)
        r.sess.scope_chat_id = r.sess.scope_chat_id or CHAT
        await r.prompt("second")
        await r.call("toolu_1" if same_id else "toolu_2",
                     [str(files / "p0.png")], caption="two")
        await r.stop("Second answer.")
        await r.until(lambda: r.sends() or _failed("p0.png"), GIVE_UP)
        await r.settle()
        out["b"] = r.sends()
    run_async(second())
    return out


def test_control_the_first_daemon_sent_its_file(mk_bot, home, run_async, files):
    out = _across_restart(mk_bot, home, run_async, files, same_id=True)
    assert len(out["a"]) == 1


def test_control_a_new_call_id_after_a_restart_is_sent(mk_bot, home, run_async, files):
    out = _across_restart(mk_bot, home, run_async, files, same_id=False)
    assert len(out["b"]) == 1


def test_the_same_call_id_after_a_restart_sends_nothing(mk_bot, home, run_async, files):
    out = _across_restart(mk_bot, home, run_async, files, same_id=True)
    assert out["b"] == []


def test_a_turn_open_when_the_daemon_restarted_still_sends(mk_bot, home, run_async,
                                                          files):
    # The turn is persisted: a prompt on daemon A, the call and the Stop on
    # daemon B.
    from aipager.state import SessionRegistry
    out = {}

    async def first():
        r = Rig(mk_bot, home)
        await r.prompt()
        r.reg.save()
    run_async(first())

    async def second():
        reg = SessionRegistry()
        reg.load()
        r = Rig(mk_bot, home, reg=reg)
        r.sess.scope_chat_id = r.sess.scope_chat_id or CHAT
        await r.call("toolu_r", [str(files / "b.pdf")])
        await r.stop("Here it is.")
        await r.until(lambda: r.sends() or _failed("b.pdf"), GIVE_UP)
        await r.settle()
        out["b"] = r.sends()
    run_async(second())
    assert [c[0] for c in out["b"]] == ["send_document"]


# ---- a background agent's call written late while its job runs -------------------

def _agent_transcript(r):
    # Claude Code keeps a subagent's transcript beside the session's:
    # <projects>/<proj>/<session id>/subagents/agent-<id>.jsonl
    p = r.transcript.parent / SID / "subagents" / f"agent-{AGENT}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _background(files, *, late):
    async def s(r):
        await r.prompt("look into it in the background and send me the pdf")
        launch = {"subagent_type": "Explore", "description": "Look",
                  "prompt": "Look", "run_in_background": True}
        await r.dg(hook_event_name="PreToolUse", tool_name="Agent",
                   tool_use_id="toolu_launch", tool_input=launch)
        await r.dg(hook_event_name="SubagentStart", agent_id=AGENT,
                   agent_type="Explore")
        await r.dg(hook_event_name="PostToolUse", tool_name="Agent",
                   tool_use_id="toolu_launch", tool_input=launch,
                   tool_response={"isAsync": True, "status": "async_launched",
                                  "agentId": AGENT})
        # The parent's turn ends while the agent runs: the job stays open.
        await r.stop("Started a background agent.")
        await r.settle(limit=3.0)
        tin = {"files": [str(files / "b.pdf")], "status": "normal"}
        agent_t = _agent_transcript(r)
        if not late:
            r.write_call("toolu_bg", tin, path=agent_t)
        extra = {"agent_id": AGENT, "agent_type": "Explore"}
        await r.pair("toolu_bg", tin, **extra)
        # A stray idle while the agent still runs (as Claude Code sends).
        await r.dg(hook_event_name="Stop",
                   last_assistant_message="Started a background agent.")
        if late:
            # The lazy transcript: longer than the transcript wait.
            await asyncio.sleep(25.0)
            r.write_call("toolu_bg", tin, path=agent_t)
            await asyncio.sleep(1.0)
        r.seen_before_finish = len(r.sends())
        await r.dg(hook_event_name="SubagentStop", agent_id=AGENT,
                   agent_type="Explore")
        await r.prompt(f"<task-notification>\n<task-id>{AGENT}</task-id>\n"
                       "Background agent finished.")
        await r.stop("The agent finished and sent the pdf.")
        await r.until(lambda: r.sends() or _failed("b.pdf"), GIVE_UP)
        await r.settle()
    return s


def test_control_a_background_agents_call_is_sent(rig, files):
    assert [c[0] for c in rig(_background(files, late=False)).sends()] == [
        "send_document"]


def test_a_background_agents_late_call_is_still_sent(rig, files):
    assert [c[0] for c in rig(_background(files, late=True)).sends()] == [
        "send_document"]


def test_a_background_agents_late_call_is_not_audited_as_failed(rig, files):
    rig(_background(files, late=True))
    assert _failed("b.pdf") == []


# ---- order across turns during a mute --------------------------------------------

def _mute_then_two_turns(files):
    from aipager.bot.flood import MUTE

    async def s(r):
        MUTE.mute(CHAT, 600, source="test")
        await r.prompt("first")
        await r.call("toolu_m1", [str(files / "p0.png")])
        await r.stop("First answer.")
        await r.settle(limit=3.0)
        r.sent_while_muted = len(r.sends())
        MUTE.clear()
        # The mute is over; the second turn finishes before anything flushed
        # the first turn's held answer and files.
        await r.prompt("second")
        await r.call("toolu_m2", [str(files / "p1.png")])
        await r.stop("Second answer.")
        await r.settle(limit=3.0)
        # The monitor's next tick once the mute is over: the held answer.
        await r.bot.flush_held_answers(r.sess)
        await r.until(lambda: len(r.sends()) >= 2, 15.0)
        await r.settle()
    return s


@pytest.fixture
def rich_api(monkeypatch):
    """Telegram's HTTP end of the rich-message path (the held answer goes
    out through it): every call succeeds with a fresh message id."""
    ids = itertools.count(90_000)
    calls: list = []

    async def _post(method, payload, **_kw):
        calls.append((method, payload))
        return {"ok": True, "result": {"message_id": next(ids)}}
    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    return calls


def test_no_file_is_sent_while_muted(rig, rich_api, files):
    r = rig(_mute_then_two_turns(files))
    assert r.sent_while_muted == 0


def test_control_the_held_answer_went_out_after_the_mute(rig, rich_api, files):
    rig(_mute_then_two_turns(files))
    sent = [p.get("rich_message", {}).get("markdown", "") for m, p in rich_api
            if m.startswith("send")]
    assert any("First answer." in x for x in sent)


def test_a_newer_turns_files_never_precede_an_older_held_batch(rig, rich_api, files):
    r = rig(_mute_then_two_turns(files))
    assert [_names(c) for c in r.sends()] == [["p0.png"], ["p1.png"]]

