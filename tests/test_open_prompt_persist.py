"""The open permission prompt in the state file (roadmap 8.102).

While a session waits on a permission prompt shown in Telegram, the state
file keeps a sanitized copy of it (``open_prompt``), so a restarted daemon
can bring the session back waiting on it. Pinned here: when the record is
written (only while INTERACTIVE, only for the prompt of this wait, only the
restorable kinds of question), what it holds, that it reads back through
the real file, and that anything malformed in it is ignored as a whole.
"""

from __future__ import annotations

import copy
import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import state
from aipager.state import SessionRegistry, Status

CHAT = 256113222
NAME = "claude-openprompt"
HOOK = {"addr": "/nonexistent/aipager-reply-x.sock", "request_id": "req-1"}
SUGGESTION = {"type": "addRules", "behavior": "allow",
              "destination": "localSettings",
              "rules": [{"toolName": "Bash", "ruleContent": "ls -la"}]}


def _tool_info(**over) -> dict:
    info = {"name": "Bash", "input": {"command": "ls -la" + "x" * 5000},
            "summary": "Bash: ls -la", "always_available": True,
            "standing_rule_suggestion": SUGGESTION, "detail": "in /repo",
            "tool_use_id": "toolu_1"}
    info.update(over)
    return info


def _waiting(reg: SessionRegistry, name: str = NAME):
    """A session that entered its wait (INTERACTIVE) a moment ago."""
    sess = reg.get_or_create(name)
    sess.label = "op"
    sess.scope_chat_id, sess.scope_kind = CHAT, "dm"
    reg.transition(name, Status.BUSY)
    reg.transition(name, Status.INTERACTIVE)
    return sess


def _inline(sess, card: int = 77, **info_over):
    sess.busy_msg_id = card
    sess.pending_permission = {
        "tool_summary": "Bash: ls -la", "tool_info": _tool_info(**info_over),
        "wait_started_at": time.monotonic(), "shown_wall": time.time() - 5,
        "hook_reply": dict(HOOK)}
    return sess


def _separate(sess, msg_id: int = 88, chat: int = CHAT):
    sess.busy_msg_id = None
    sess.pending_permission = None
    sess.pending_prompt_msg = {
        "text": "🔐 <b>op</b> · Permission needed", "keyboard": object(),
        "summary": "Bash: ls -la", "prompt_token": 3,
        "msg_id": msg_id, "chat_id": chat,
        "perm": {"tool_summary": "Bash: ls -la", "tool_info": _tool_info(),
                 "wait_started_at": time.monotonic(),
                 "shown_wall": time.time() - 5, "hook_reply": dict(HOOK)}}
    return sess


def _question(questions=None, **over) -> dict:
    questions = questions or [{"question": "Which way?", "multiSelect": False,
                               "options": [{"label": "Left", "description": "l"},
                                           {"label": "Right"}]}]
    perm = {"ask_question": True, "question": questions[0]["question"],
            "options": questions[0]["options"], "questions": questions,
            "current_idx": 0, "multi_select": bool(questions[0].get("multiSelect")),
            "cursor_pos": 0, "selected": set(),
            "tool_info": {"name": "AskUserQuestion",
                          "input": {"questions": questions},
                          "summary": "AskUserQuestion: Which way?",
                          "tool_use_id": "toolu_q"},
            "wait_started_at": time.monotonic(), "shown_wall": time.time() - 5}
    perm.update(over)
    return perm


def _saved(name: str = NAME):
    data = json.loads(state.SESSION_STATE_FILE.read_text())
    return data["sessions"][name].get("open_prompt")


def _reload() -> SessionRegistry:
    again = SessionRegistry()
    again.load()
    return again


# ── when the record is written ───────────────────────────────────────────

def test_an_inline_prompt_survives_a_save_and_load():
    reg = SessionRegistry()
    _inline(_waiting(reg))
    reg.save()
    rec = _reload().get(NAME).restored_open_prompt
    assert rec is not None
    assert rec["kind"] == "inline" and rec["card_msg_id"] == 77
    assert rec["tool_use_id"] == "toolu_1"
    assert rec["perm"] == {
        "tool_name": "Bash", "tool_summary": "Bash: ls -la",
        "detail": "in /repo", "always_available": True,
        "standing_rule_suggestion": SUGGESTION, "hook_reply": HOOK,
        "question": None}
    # The tool's input (megabytes of Write content, at worst) stays out.
    assert "xxxxx" not in state.SESSION_STATE_FILE.read_text()


def test_a_separate_prompt_survives_a_save_and_load():
    reg = SessionRegistry()
    _separate(_waiting(reg))
    reg.save()
    rec = _reload().get(NAME).restored_open_prompt
    assert rec is not None
    assert (rec["kind"], rec["chat_id"], rec["msg_id"]) == ("separate", CHAT, 88)
    assert rec["text"] == "🔐 <b>op</b> · Permission needed"
    assert rec["summary"] == "Bash: ls -la"
    assert rec["perm"]["hook_reply"] == HOOK


@pytest.mark.parametrize("end", [Status.BUSY, Status.IDLE, Status.GONE])
def test_no_record_once_answered(end):
    """Answered (BUSY), a Stop (IDLE), the session gone: no wait, no
    record. A separate prompt's record is never cleared on answer, so the
    status is the only thing that keeps it out."""
    reg = SessionRegistry()
    sess = _separate(_waiting(reg))
    reg.save()
    assert _saved() is not None
    sess.status = end
    reg.save()
    assert _saved() is None


def test_no_record_after_the_inline_answer():
    reg = SessionRegistry()
    sess = _inline(_waiting(reg))
    reg.save()
    assert _saved() is not None
    sess.pending_permission = None
    reg.transition(NAME, Status.BUSY)
    reg.save()
    assert _saved() is None


def test_stale_separate_prompt_not_saved():
    """A separate prompt an EARLIER wait left behind (never cleared) is not
    the prompt of the wait the session is in now."""
    reg = SessionRegistry()
    sess = _separate(_waiting(reg))
    sess.pending_prompt_msg["perm"]["wait_started_at"] = time.monotonic() - 30
    reg.transition(NAME, Status.BUSY)
    reg.transition(NAME, Status.INTERACTIVE)  # a new wait, no new prompt
    reg.save()
    assert _saved() is None


def test_the_wait_start_is_stamped_on_entering_interactive():
    reg = SessionRegistry()
    sess = reg.get_or_create(NAME)
    before = time.monotonic()
    reg.transition(NAME, Status.INTERACTIVE)
    assert before <= sess.interactive_entered_at <= time.monotonic()


def test_a_separate_prompt_that_never_reached_the_chat_is_not_saved():
    reg = SessionRegistry()
    sess = _separate(_waiting(reg))
    del sess.pending_prompt_msg["msg_id"]
    reg.save()
    assert _saved() is None


def test_an_inline_prompt_without_its_card_is_not_saved():
    reg = SessionRegistry()
    sess = _inline(_waiting(reg))
    sess.busy_msg_id = None
    reg.save()
    assert _saved() is None


def test_a_prompt_with_no_tool_name_is_not_saved():
    reg = SessionRegistry()
    sess = _inline(_waiting(reg))
    sess.pending_permission["tool_info"]["name"] = ""
    reg.save()
    assert _saved() is None


def test_a_malformed_hook_channel_is_saved_as_none():
    reg = SessionRegistry()
    sess = _inline(_waiting(reg))
    sess.pending_permission["hook_reply"] = {"addr": "/x", "request_id": "r",
                                             "extra": 1}
    reg.save()
    assert _saved()["perm"]["hook_reply"] is None


@pytest.mark.parametrize("surface", ["inline", "separate"])
def test_an_untouched_single_question_is_saved(surface):
    reg = SessionRegistry()
    sess = _waiting(reg)
    perm = _question()
    if surface == "inline":
        sess.busy_msg_id = 77
        sess.pending_permission = perm
    else:
        _separate(sess)
        sess.pending_prompt_msg["perm"] = {
            "tool_summary": "Which way?", "tool_info": perm["tool_info"],
            "wait_started_at": time.monotonic(),
            "shown_wall": time.time() - 5, "ask_question": True,
            "question": "Which way?"}
    reg.save()
    rec = _reload().get(NAME).restored_open_prompt
    assert rec is not None and rec["kind"] == surface
    assert rec["tool_use_id"] == "toolu_q"
    assert rec["perm"]["question"] == {
        "question": "Which way?",
        "options": [{"label": "Left", "description": "l"},
                    {"label": "Right", "description": ""}]}
    assert rec["perm"]["hook_reply"] is None


_TWO = [{"question": "One?", "options": [{"label": "A"}]},
        {"question": "Two?", "options": [{"label": "B"}]}]


@pytest.mark.parametrize("perm", [
    pytest.param(_question([{"question": "Pick", "multiSelect": True,
                             "options": [{"label": "A"}, {"label": "B"}]}]),
                 id="multi-select"),
    pytest.param(_question(_TWO), id="multi-question"),
    pytest.param(_question(cursor_pos=1), id="cursor-moved"),
    pytest.param(_question(current_idx=1), id="advanced"),
    pytest.param(_question(selected={0}), id="ticked"),
    pytest.param(_question([{"question": "Many", "options": [
        {"label": str(i)} for i in range(5)]}]), id="five-options"),
    pytest.param(_question([{"question": "Bare", "options": [{}]}]),
                 id="label-less-option"),
    pytest.param({"tool_summary": "AskUserQuestion (loading…)",
                  "tool_info": {"name": "AskUserQuestion", "input": {},
                                "summary": "AskUserQuestion"},
                  "wait_started_at": time.monotonic(),
                  "shown_wall": time.time() - 5}, id="loading"),
])
def test_a_question_outside_the_restorable_kind_is_not_saved(perm):
    reg = SessionRegistry()
    sess = _waiting(reg)
    perm = copy.deepcopy(perm)
    perm["wait_started_at"] = time.monotonic()
    sess.busy_msg_id = 77
    sess.pending_permission = perm
    reg.save()
    assert _saved() is None


# ── reading it back ──────────────────────────────────────────────────────

def _valid_file(kind: str = "inline") -> dict:
    reg = SessionRegistry()
    sess = _waiting(reg)
    if kind == "inline":
        _inline(sess)
    elif kind == "separate":
        _separate(sess)
    else:
        sess.busy_msg_id = 77
        sess.pending_permission = _question()
    reg.save()
    data = json.loads(state.SESSION_STATE_FILE.read_text())
    assert data["sessions"][NAME]["open_prompt"] is not None
    return data


def _set(path, value):
    def edit(rec):
        target = rec
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return edit


def _drop(path):
    def edit(rec):
        target = rec
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]
    return edit


_INVALID = [
    ("inline", "version-2", _set(["v"], 2)),
    ("inline", "version-bool", _set(["v"], True)),
    ("inline", "unknown-kind", _set(["kind"], "popup")),
    ("inline", "extra-key", _set(["extra"], 1)),
    ("inline", "missing-tool-use-id", _drop(["tool_use_id"])),
    ("inline", "tool-use-id-int", _set(["tool_use_id"], 7)),
    ("inline", "tool-use-id-too-long", _set(["tool_use_id"], "t" * 201)),
    ("inline", "future-shown-wall", _set(["shown_wall"], time.time() + 3600)),
    ("inline", "nan-shown-wall", _set(["shown_wall"], float("nan"))),
    ("inline", "inf-shown-wall", _set(["shown_wall"], float("inf"))),
    ("inline", "zero-shown-wall", _set(["shown_wall"], 0)),
    ("inline", "string-shown-wall", _set(["shown_wall"], "now")),
    ("inline", "bool-shown-wall", _set(["shown_wall"], True)),
    ("inline", "card-not-busy-card", _set(["card_msg_id"], 78)),
    ("inline", "card-bool", _set(["card_msg_id"], True)),
    ("inline", "perm-extra-key", _set(["perm", "input"], {})),
    ("inline", "perm-missing-key", _drop(["perm", "detail"])),
    ("inline", "perm-not-dict", _set(["perm"], "Bash")),
    ("inline", "empty-tool-name", _set(["perm", "tool_name"], "")),
    ("inline", "int-tool-name", _set(["perm", "tool_name"], 5)),
    ("inline", "summary-too-long", _set(["perm", "tool_summary"], "s" * 1001)),
    ("inline", "hook-extra-key", _set(["perm", "hook_reply", "pid"], 1)),
    ("inline", "hook-empty-addr", _set(["perm", "hook_reply", "addr"], "")),
    ("inline", "hook-list", _set(["perm", "hook_reply"], ["/x", "r"])),
    ("inline", "always-string", _set(["perm", "always_available"], "yes")),
    ("inline", "always-int", _set(["perm", "always_available"], 1)),
    ("inline", "set-mode-suggestion", _set(["perm", "standing_rule_suggestion"],
                                           {"type": "setMode", "mode": "auto"})),
    ("inline", "question-on-a-tool", _set(["perm", "question"], {
        "question": "?", "options": [{"label": "A", "description": ""}]})),
    ("question", "no-question", _set(["perm", "question"], None)),
    ("question", "question-with-hook", _set(["perm", "hook_reply"], dict(HOOK))),
    ("question", "five-options", _set(["perm", "question", "options"], [
        {"label": str(i), "description": ""} for i in range(5)])),
    ("question", "no-options", _set(["perm", "question", "options"], [])),
    ("question", "option-extra-key",
     _set(["perm", "question", "options", 0, "id"], 1)),
    ("question", "option-empty-label",
     _set(["perm", "question", "options", 0, "label"], "")),
    ("question", "question-extra-key", _set(["perm", "question", "multi"], 1)),
    ("separate", "msg-id-bool", _set(["msg_id"], True)),
    ("separate", "msg-id-zero", _set(["msg_id"], 0)),
    ("separate", "chat-id-string", _set(["chat_id"], str(CHAT))),
    ("separate", "chat-id-bool", _set(["chat_id"], False)),
    ("separate", "empty-text", _set(["text"], "")),
    ("separate", "text-too-long", _set(["text"], "t" * 4097)),
    ("separate", "missing-summary", _drop(["summary"])),
    ("separate", "card-key-on-separate", _set(["card_msg_id"], 77)),
    ("inline", "not-a-dict", None),
]


@pytest.mark.parametrize("kind,edit", [
    pytest.param(kind, edit, id=name) for kind, name, edit in _INVALID])
def test_invalid_record_ignored(kind, edit, caplog):
    data = _valid_file(kind)
    if edit is None:
        data["sessions"][NAME]["open_prompt"] = ["not", "a", "record"]
    else:
        edit(data["sessions"][NAME]["open_prompt"])
    state.SESSION_STATE_FILE.write_text(json.dumps(data))
    with caplog.at_level(logging.WARNING, logger="aipager.state"):
        sess = _reload().get(NAME)
    assert sess.restored_open_prompt is None
    assert any("saved permission prompt ignored - invalid record" in m
               for m in caplog.messages)
    # The session itself loads as before 8.102.
    assert sess.status in (Status.UNKNOWN, Status.GONE)


@pytest.mark.parametrize("kind", ["inline", "separate", "question"])
def test_a_valid_record_loads_unchanged(kind, caplog):
    """The control for the matrix above: the same file, untouched, loads."""
    data = _valid_file(kind)
    state.SESSION_STATE_FILE.write_text(json.dumps(data))
    with caplog.at_level(logging.WARNING, logger="aipager.state"):
        sess = _reload().get(NAME)
    assert sess.restored_open_prompt is not None
    assert not any("invalid record" in m for m in caplog.messages)


def test_a_file_without_the_key_loads_no_prompt(caplog):
    reg = SessionRegistry()
    reg.get_or_create(NAME)
    reg.save()
    with caplog.at_level(logging.WARNING, logger="aipager.state"):
        sess = _reload().get(NAME)
    assert sess.restored_open_prompt is None
    assert not any("invalid record" in m for m in caplog.messages)


# ── what notify records at prompt time ───────────────────────────────────

def _bot(mk_bot):
    bot = mk_bot()
    bot._maybe_update_bot_name = AsyncMock()
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._stop_animation = MagicMock()
    return bot


def _prompt(bot, sess, run_async):
    run_async(bot.notify(sess, "permission_prompt", {
        "tool_info": _tool_info(), "hook_reply": dict(HOOK)}))


def test_the_separate_prompts_message_is_recorded(mk_bot, run_async):
    bot = _bot(mk_bot)
    bot._app.bot.send_message = AsyncMock(
        return_value=MagicMock(message_id=91))
    sess = _waiting(bot.registry)
    _prompt(bot, sess, run_async)
    rec = sess.pending_prompt_msg
    assert (rec["msg_id"], rec["chat_id"]) == (91, CHAT)
    assert rec["perm"]["shown_wall"] <= time.time()
    bot.registry.save()
    assert _saved()["msg_id"] == 91


def test_msg_id_not_stored_on_superseded_prompt(mk_bot, run_async):
    """The send waited in the limiter while the prompt was answered and
    another one shown: the message id is not the newer prompt's."""
    bot = _bot(mk_bot)
    sess = _waiting(bot.registry)
    newer = {"text": "newer", "keyboard": None, "summary": "newer",
             "prompt_token": 999, "perm": {}}

    async def _slow_send(*args, **kwargs):
        sess.pending_prompt_msg = newer
        return MagicMock(message_id=91)

    bot._app.bot.send_message = AsyncMock(side_effect=_slow_send)
    _prompt(bot, sess, run_async)
    assert sess.pending_prompt_msg is newer
    assert "msg_id" not in newer and "chat_id" not in newer


def test_the_inline_prompt_records_when_it_was_shown(mk_bot, run_async):
    bot = _bot(mk_bot)
    sess = _waiting(bot.registry)
    sess.busy_msg_id = 77
    before = time.time()
    _prompt(bot, sess, run_async)
    perm = sess.pending_permission
    assert perm is not None and before <= perm["shown_wall"] <= time.time()
    assert perm["tool_info"]["tool_use_id"] == "toolu_1"
    bot.registry.save()
    assert _saved()["kind"] == "inline"


# ── a subagent's prompt is never saved (review rev-iter1-004) ────────────

def test_a_subagents_inline_prompt_is_not_saved():
    """Its answer lands in the subagent's own transcript, which the
    restart's check does not read: answered in the terminal while the
    daemon was down, it would come back as open."""
    reg = SessionRegistry()
    _inline(_waiting(reg), agent_id="a1agent")
    reg.save()
    assert _saved() is None


def test_a_subagents_separate_prompt_is_not_saved():
    reg = SessionRegistry()
    sess = _separate(_waiting(reg))
    sess.pending_prompt_msg["perm"]["tool_info"]["agent_id"] = "a1agent"
    reg.save()
    assert _saved() is None


def test_a_subagents_question_is_not_saved():
    reg = SessionRegistry()
    sess = _waiting(reg)
    sess.busy_msg_id = 77
    perm = _question()
    perm["tool_info"]["agent_id"] = "a1agent"
    sess.pending_permission = perm
    reg.save()
    assert _saved() is None


def test_a_failing_snapshot_does_not_stop_the_save(monkeypatch, caplog):
    """The state file is still written, the session in it, with no prompt."""
    reg = SessionRegistry()
    _inline(_waiting(reg))

    def _boom(*args, **kwargs):
        raise TypeError("unhashable type: 'list'")

    monkeypatch.setattr(state, "snapshot_open_prompt", _boom)
    try:
        reg.save()
        outcome = "saved"
    except Exception as exc:  # noqa: BLE001 - the guard under test
        outcome = f"raised {type(exc).__name__}"
    assert outcome == "saved"
    data = json.loads(state.SESSION_STATE_FILE.read_text())
    assert NAME in data["sessions"]
    assert "open_prompt" not in data["sessions"][NAME]
