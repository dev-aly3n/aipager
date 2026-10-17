"""The aipager-hook binary on a SendUserFile call, the daemon's handling
of the ``send_files_refused`` datagram, the audit record and the busy
card row (design.md criteria 1, 2, 7, 8; entrypoints.md CLI and Events)."""

from __future__ import annotations

import asyncio
import json
import sys

import pytest

from aipager import audit, i18n, send_check
from aipager.bot import card_rows
from aipager.dtach import hook_receiver
from aipager.dtach.hook_receiver import HookReceiver
from aipager.state import SessionRegistry, Status, TrackedSession

kit = sys.modules["_sftc_kit"]
SESSION, TOOL, FLOOR_WHY, reason_for = kit.SESSION, kit.TOOL, kit.FLOOR_WHY, kit.reason_for


def _transcript(tmp_path, text=kit.MARKERS["owner"]):
    t = tmp_path / "hook-t.jsonl"
    t.write_text(json.dumps({"type": "user", "message": {"content": text}}) + "\n")
    return str(t)


def _pre(tmp_path, files, cwd, tool_use_id="toolu_9"):
    return {"hook_event_name": "PreToolUse", "session_id": kit.SID,
            "cwd": str(cwd), "tool_name": TOOL, "tool_input": {"files": files},
            "tool_use_id": tool_use_id, "transcript_path": _transcript(tmp_path)}


@pytest.fixture
def floor_file(home):
    (home / ".ssh").mkdir()
    p = home / ".ssh" / "config"
    p.write_text("Host x")
    return p


# ---- the hook binary ---------------------------------------------------------

def test_hook_prints_the_soft_deny_for_a_floor_file(run_hook, tmp_path, proj, floor_file):
    kit.use_role("owner")
    out, _ = run_hook(_pre(tmp_path, [str(floor_file)], proj))
    decision = json.loads(out)["hookSpecificOutput"]
    assert (decision["permissionDecision"], decision["permissionDecisionReason"]) == (
        "deny", reason_for((str(floor_file), FLOOR_WHY)))


def test_hook_deny_has_no_safety_policy_marker(run_hook, tmp_path, proj, floor_file):
    kit.use_role("owner")
    out, _ = run_hook(_pre(tmp_path, [str(floor_file)], proj))
    assert "aipager safety policy" not in out


def test_hook_sends_no_safety_blocked_datagram(run_hook, tmp_path, proj, floor_file):
    kit.use_role("owner")
    _, sent = run_hook(_pre(tmp_path, [str(floor_file)], proj))
    assert "safety_blocked" not in [d.get("hook_event_name") for d in sent]


def test_hook_reports_each_refused_file(run_hook, tmp_path, proj, floor_file):
    kit.use_role("owner")
    _, sent = run_hook(_pre(tmp_path, [str(floor_file), str(proj / "nope.png")], proj))
    refused = [d for d in sent if d.get("hook_event_name") == "send_files_refused"]
    assert [(f["path"], f["code"]) for f in refused[0]["files"]] == [
        (str(floor_file), "floor"), (str(proj / "nope.png"), "missing")]


def test_refused_datagram_names_session_and_tool_use(run_hook, tmp_path, proj, floor_file):
    kit.use_role("owner")
    _, sent = run_hook(_pre(tmp_path, [str(floor_file)], proj, tool_use_id="toolu_77"))
    d = next(d for d in sent if d.get("hook_event_name") == "send_files_refused")
    assert (d["session"], d["tool_use_id"]) == (SESSION, "toolu_77")


def test_refused_datagram_carries_the_english_reason(run_hook, tmp_path, proj, floor_file):
    kit.use_role("owner")
    _, sent = run_hook(_pre(tmp_path, [str(floor_file)], proj))
    d = next(d for d in sent if d.get("hook_event_name") == "send_files_refused")
    assert FLOOR_WHY in d["files"][0]["reason"]


def test_hook_allows_an_ordinary_file(run_hook, tmp_path, proj):
    kit.use_role("owner")
    out, _ = run_hook(_pre(tmp_path, [str(proj / "report.pdf")], proj))
    assert '"deny"' not in out


def test_hook_sends_no_refused_datagram_on_allow(run_hook, tmp_path, proj):
    kit.use_role("owner")
    _, sent = run_hook(_pre(tmp_path, [str(proj / "report.pdf")], proj))
    assert "send_files_refused" not in [d.get("hook_event_name") for d in sent]


def test_hook_refuses_for_a_terminal_turn(run_hook, tmp_path, proj, floor_file):
    payload = _pre(tmp_path, [str(floor_file)], proj)
    t = tmp_path / "term.jsonl"
    t.write_text(json.dumps({"type": "user", "message": {"content": "send it"}}) + "\n")
    payload["transcript_path"] = str(t)
    out, _ = run_hook(payload)
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


# ---- the daemon: the refused datagram and its audit record ------------------

def _datagram(files, session=SESSION):
    return json.dumps({"hook_event_name": "send_files_refused", "session": session,
                       "tool_use_id": "toolu_9", "files": files}).encode()


FILES = [{"path": "/home/alice/.ssh/config", "code": "floor", "reason": FLOOR_WHY},
         {"path": "nope.png", "code": "missing", "reason": "no such file"}]


def _registry():
    reg = SessionRegistry()
    sess = TrackedSession(name=SESSION, label="frontend", status=Status.BUSY)
    sess.scope_chat_id = 4242
    reg._sessions[sess.name] = sess
    return reg, sess


def test_refused_for_unknown_session_does_nothing():
    seen = []

    async def notify(s, event, ctx):
        seen.append(event)
    rec = HookReceiver(SessionRegistry(), notify)
    asyncio.run(rec._on_datagram(_datagram(FILES, session="claude-nobody")))
    assert seen == []


def test_refused_for_unknown_session_creates_no_session():
    reg = SessionRegistry()

    async def notify(s, event, ctx):
        pass
    asyncio.run(HookReceiver(reg, notify)._on_datagram(
        _datagram(FILES, session="claude-nobody")))
    assert reg.get("claude-nobody") is None


def test_refused_datagram_reaches_the_bot_for_a_known_session():
    reg, _ = _registry()
    seen = []

    async def notify(s, event, ctx):
        seen.append(event)
    asyncio.run(HookReceiver(reg, notify)._on_datagram(_datagram(FILES)))
    assert "send_files_refused" in seen


def _audit_records(mk_bot, monkeypatch, tmp_path):
    reg, _ = _registry()
    bot = mk_bot(reg)
    asyncio.run(HookReceiver(reg, bot.notify)._on_datagram(_datagram(FILES)))
    path = audit.AUDIT_LOG_PATH
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def test_each_refused_file_adds_one_file_refused_record(mk_bot, monkeypatch, tmp_path):
    recs = _audit_records(mk_bot, monkeypatch, tmp_path)
    assert len([r for r in recs if r.get("action") == "FileRefused"]) == 2


def test_file_refused_record_fields(mk_bot, monkeypatch, tmp_path):
    recs = [r for r in _audit_records(mk_bot, monkeypatch, tmp_path)
            if r.get("action") == "FileRefused"]
    assert [(r.get("tool"), r.get("denied"), bool(r.get("reason")),
             bool(r.get("file_name"))) for r in recs] == [(TOOL, True, True, True)] * 2


def test_file_refused_record_names_each_file(mk_bot, monkeypatch, tmp_path):
    recs = [r for r in _audit_records(mk_bot, monkeypatch, tmp_path)
            if r.get("action") == "FileRefused"]
    assert [r["file_name"].rsplit("/", 1)[-1] for r in recs] == ["config", "nope.png"]


def test_refusal_sends_nothing_to_the_chat(mk_bot, monkeypatch, tmp_path):
    reg, _ = _registry()
    bot = mk_bot(reg)
    asyncio.run(HookReceiver(reg, bot.notify)._on_datagram(_datagram(FILES)))
    assert bot._app.bot.send_message.await_count == 0


# ---- audit.append's new fields ------------------------------------------------

def _one(tmp_path, **kw):
    p = tmp_path / "a.jsonl"
    assert audit.append(session=SESSION, label="frontend", action="FileSent",
                        tool=TOOL, path=p, **kw)
    return json.loads(p.read_text().splitlines()[-1])


def test_audit_record_with_a_file_carries_its_name_and_size(tmp_path):
    rec = _one(tmp_path, file_name="report.pdf", file_size=1234)
    assert (rec["file_name"], rec["file_size"]) == ("report.pdf", 1234)


def test_audit_record_without_a_file_keeps_its_shape(tmp_path):
    rec = _one(tmp_path)
    assert "file_name" not in rec and "file_size" not in rec


# ---- the busy card row --------------------------------------------------------

@pytest.mark.parametrize("files,n", [(["a.png"], 1), (["a.png", "b.pdf"], 2),
                                     ("a.png", 1)], ids=["one", "two", "string"])
def test_summary_is_the_sending_row(files, n):
    assert hook_receiver._summarize_tool(TOOL, {"files": files}) == \
        send_check.sending_row(n)


def test_english_row_is_drawn_as_stored():
    row = send_check.sending_row(2)
    assert card_rows.render_text(i18n.get("en"), row) == row


@pytest.mark.parametrize("lang", ["fa", "de", "ru"])
def test_row_is_translated_in_a_non_english_chat(lang):
    row = send_check.sending_row(2)
    assert card_rows.render_text(i18n.get(lang), row) != row


def test_translated_row_keeps_the_count():
    shown = card_rows.render_text(i18n.get("de"), send_check.sending_row(7))
    assert "7" in shown
