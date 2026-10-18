"""The daemon's side of a SendUserFile call (hook_receiver): the card row,
the permission card's detail, the outside-reads gate, and the hook's
``send_files_refused`` datagram."""

from __future__ import annotations

import json

import pytest

from aipager.dtach import hook_receiver as hr


def test_summary_is_the_sending_row():
    assert hr._summarize_tool("SendUserFile", {"files": ["/a.png"]}) == "📎 Sending 1 file"
    assert hr._summarize_tool("SendUserFile", {"files": ["/a", "/b"]}) == "📎 Sending 2 files"
    assert hr._summarize_tool("SendUserFile", {"files": "/a.png"}) == "📎 Sending 1 file"
    assert hr._summarize_tool("SendUserFile", {"files": 3}) == "SendUserFile"


def test_detail_lists_each_file():
    assert hr._tool_detail("SendUserFile", {"files": ["/a.png", "b.pdf"]}) == "/a.png\nb.pdf"
    assert hr._tool_detail("SendUserFile", {"files": None}) == ""


def test_activity_names_the_first_file():
    assert hr._activity_arg("SendUserFile", {"files": ["/x/report.pdf", "/y"]}) == "report.pdf"
    assert hr._activity_arg("SendUserFile", {"files": []}) == ""


@pytest.mark.parametrize("files, outside", [
    (["/work/a.png"], False),
    (["a.png", "sub/b.png"], False),
    (["/work/a.png", "/etc/hosts"], True),
    ("/tmp/x.png", True),
    (["~/x.png"], True),
    ([], True),
    (None, True),
], ids=["inside", "relative", "one-outside", "string-outside", "tilde", "empty", "none"])
def test_send_outside_cwd_counts_as_outside_read(files, outside, monkeypatch):
    monkeypatch.setenv("HOME", "/home/alice")
    assert hr._read_outside_working_dir(
        "SendUserFile", {"files": files}, "/work") is outside


def _receiver():
    from aipager.state import SessionRegistry
    calls: list = []

    async def notify_fn(sess, kind, payload):
        calls.append((sess.name, kind, payload))
    reg = SessionRegistry()
    recv = hr.HookReceiver(reg, notify_fn)
    return recv, reg, calls


_REFUSED = {"hook_event_name": "send_files_refused", "tool_use_id": "t",
            "files": [{"path": "/x", "code": "floor", "reason": "r"}]}


def test_refused_for_unknown_session_does_nothing(run_async):
    recv, reg, calls = _receiver()
    run_async(recv._on_datagram(json.dumps(
        {**_REFUSED, "session": "claude-ghost"}).encode()))
    assert calls == []
    assert reg.get("claude-ghost") is None


def test_refused_for_a_known_session_is_passed_on(run_async):
    recv, reg, calls = _receiver()
    reg.get_or_create("claude-known")
    run_async(recv._on_datagram(json.dumps(
        {**_REFUSED, "session": "claude-known"}).encode()))
    assert calls == [("claude-known", "send_files_refused", {
        "tool_use_id": "t", "files": _REFUSED["files"]})]


def test_pre_and_post_tool_use_pass_the_call_on(run_async):
    recv, reg, calls = _receiver()
    reg.get_or_create("claude-known")
    inp = {"files": ["/work/a.png"], "status": "normal"}
    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "PreToolUse", "session": "claude-known",
        "tool_name": "SendUserFile", "tool_input": inp, "tool_use_id": "toolu_5",
        "cwd": "/work"}).encode()))
    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "PostToolUse", "session": "claude-known",
        "tool_name": "SendUserFile", "tool_input": inp, "tool_use_id": "toolu_5",
        "cwd": "/work", "tool_response": {"attachments": []}}).encode()))
    kinds = [(c[1], c[2].get("tool_use_id")) for c in calls
             if c[1] in ("send_files_requested", "send_files_ran")]
    assert kinds == [("send_files_requested", "toolu_5"), ("send_files_ran", "toolu_5")]
    ran = [c[2] for c in calls if c[1] == "send_files_ran"][0]
    assert ran["tool_input"] == inp and ran["cwd"] == "/work"
    done = [c[2] for c in calls if c[1] == "tool_done"][0]
    assert done["tool_summary"] == "📎 Sending 1 file"
