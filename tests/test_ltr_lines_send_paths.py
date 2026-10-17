"""Every send path that puts an English chat's own lines into a
right-to-left rich message marks them; every path that sends plain text
or HTML (no ``is_rtl``) does not (an English chat, a Persian answer).

The four result lines (merged, interim, finish header, under a kept card)
each have their own test, so dropping the ``tr`` at any one site fails.
"""

from __future__ import annotations

import re
import time
from unittest.mock import AsyncMock, MagicMock

from aipager import i18n
from aipager.bot import held_message
from aipager.state import SessionRegistry, Status
from tests import i18n_surfaces as surf
from tests.footer_text import unfooter

RLM, LRM, FSI, PDI = "\u200f", "\u200e", "\u2068", "\u2069"
INVISIBLE = re.compile("[\u200e\u200f\u2066-\u2069]")
ADJACENT = re.compile("[\u200e\u200f]{2,}")
PERSIAN = "ابتدا فایل را می‌خوانم."
RESULT = "\U0001f4ac"
#: The marked result line of a session labelled x1.
MARKED_LEAD = LRM + RESULT + " **" + FSI + "x1" + PDI + "**" + LRM


def _session(bot, label="x1"):
    reg = SessionRegistry()
    s = reg.get_or_create("claude-x1")
    s.label = label
    s.status = Status.BUSY
    s.scope_chat_id, s.scope_kind = 4343, "dm"
    s.busy_started_at = time.monotonic() - 123
    s.tool_history = [("Read: /a", True)]
    bot.registry._sessions[s.name] = s
    return s


def _english(*chats):
    from aipager import preferences
    for chat in chats:
        preferences.set_ui_language(chat, "")


# ---- 1. the merged layout ---------------------------------------------------------

def test_the_merged_answers_result_line_is_marked(mk_bot, run_async, monkeypatch):
    from aipager.bot import notify
    _english(4343)
    bot = mk_bot()
    s = _session(bot)
    s.busy_msg_id = 55
    edit = AsyncMock(return_value={"message_id": 55})
    monkeypatch.setattr(notify, "edit_message_text_rich", edit)
    assert run_async(bot._send_merged_final(s, PERSIAN)) is True
    text = unfooter(edit.await_args.args[2])
    assert edit.await_args.kwargs["is_rtl"] is True
    assert f"\n\n{MARKED_LEAD}\n\n{PERSIAN}" in text
    status = text.split(f"\n\n{notify._MERGED_SEPARATOR}\n\n", 1)[0].rsplit("\n", 1)[-1]
    assert status.startswith(LRM) and status.endswith(LRM)
    assert not ADJACENT.search(text) and RLM not in text


def test_an_english_merged_answer_is_unchanged(mk_bot, run_async, monkeypatch):
    from aipager.bot import notify
    _english(4343)
    bot = mk_bot()
    s = _session(bot)
    s.busy_msg_id = 55
    edit = AsyncMock(return_value={"message_id": 55})
    monkeypatch.setattr(notify, "edit_message_text_rich", edit)
    assert run_async(bot._send_merged_final(s, "Done.")) is True
    assert edit.await_args.kwargs["is_rtl"] is False
    assert not INVISIBLE.search(edit.await_args.args[2])


# ---- 2. a background job's interim answer -------------------------------------------

def test_the_interim_answers_result_line_and_block_are_marked(mk_bot, run_async,
                                                             monkeypatch):
    from aipager.bot import notify
    _english(4343)
    bot = mk_bot()
    s = _session(bot)
    s.bg_agent_started("a1", "general-purpose", time.monotonic())
    rich = AsyncMock(return_value={"message_id": 91})
    monkeypatch.setattr(notify, "send_rich_message", rich)
    run_async(bot._deliver_job_interim(s, PERSIAN, time.time()))
    text = unfooter(rich.await_args.args[1])
    assert rich.await_args.kwargs["is_rtl"] is True
    assert text.startswith(MARKED_LEAD + "\n\n" + PERSIAN)
    block = text.rsplit("\n\n---\n\n", 1)[1]
    for line in block.split("\n"):
        if line.strip():
            body = line[2:] if line.startswith("- ") else line
            assert body.startswith(LRM) and body.endswith(LRM), line
    assert not ADJACENT.search(text) and RLM not in text
    assert s.agents_lines[0]["is_rtl"] is True


def test_the_interim_plain_fallback_is_unmarked(mk_bot, run_async, monkeypatch):
    from aipager.bot import notify
    _english(4343)
    bot = mk_bot()
    s = _session(bot)
    monkeypatch.setattr(notify, "send_rich_message",
                        AsyncMock(side_effect=RuntimeError("parse error")))
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=92))
    run_async(bot._deliver_job_interim(s, PERSIAN, time.time()))
    sent = "".join(c.args[1] for c in bot._app.bot.send_message.await_args_list)
    assert sent.startswith(RESULT + " x1\n\n" + PERSIAN)
    assert not INVISIBLE.search(sent)


# ---- 3. the finish path -------------------------------------------------------------

def _finish(env, summary, *, keep_card=False, rich_error=None):
    rich = surf._rich(env)
    if rich_error is not None:
        rich.side_effect = rich_error
    bot = env.mk_bot()
    sess = surf._registered(bot, surf._sess(status=Status.IDLE))
    sess.busy_started_at = surf.NOW - 38
    sess.busy_started_wall = 0.0
    sess.bg_agent_started("a1", "general-purpose", surf.NOW)
    if keep_card:
        sess.layout_override = "card"
        sess.busy_msg_id = 55
        bot._edit_busy_rich = AsyncMock(return_value=True)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    env.run(bot.notify(sess, "idle_prompt", {"summary": summary}))
    return rich, bot, sess


def test_the_finished_line_is_marked_and_its_name_isolated(mk_bot, run_async, monkeypatch):
    env = surf.Env(mk_bot, run_async, monkeypatch, "en")
    rich, _bot, _sess = _finish(env, PERSIAN)
    assert rich.await_args.kwargs["is_rtl"] is True
    text = unfooter(rich.await_args.args[1])
    head = text.split("\n", 1)[0]
    # The line's own start mark, the lead's isolate, the lead's own mark.
    assert head.startswith(LRM + FSI + MARKED_LEAD + PDI + " · Finished")
    assert head.endswith(LRM) and not ADJACENT.search(head)


def test_the_short_result_line_under_a_kept_card_is_marked(mk_bot, run_async, monkeypatch):
    env = surf.Env(mk_bot, run_async, monkeypatch, "en")
    rich, _bot, _sess = _finish(env, PERSIAN, keep_card=True)
    assert rich.await_args.kwargs["is_rtl"] is True
    text = unfooter(rich.await_args.args[1])
    assert text.startswith(MARKED_LEAD + "\n\n" + PERSIAN)


def test_the_plain_fallback_of_the_answer_is_unmarked(mk_bot, run_async, monkeypatch):
    env = surf.Env(mk_bot, run_async, monkeypatch, "en")
    _rich, bot, _sess = _finish(env, PERSIAN, rich_error=RuntimeError("parse error"))
    sent = "".join(c.args[1] for c in bot._app.bot.send_message.await_args_list)
    assert sent.startswith(RESULT + " x1 · Finished")
    assert PERSIAN in sent and not INVISIBLE.search(sent)


def test_the_html_header_of_a_long_answer_is_unmarked(mk_bot, run_async, monkeypatch):
    env = surf.Env(mk_bot, run_async, monkeypatch, "en")
    long_answer = (PERSIAN + "\n\n") * 3000
    rich, bot, _sess = _finish(env, long_answer)
    assert rich.await_args.kwargs["is_rtl"] is True
    headers = [c.args[1] for c in bot._app.bot.send_message.await_args_list
               if c.kwargs.get("parse_mode") == "HTML"]
    assert headers and "Full response attached below" in headers[0]
    assert not INVISIBLE.search(headers[0])


def test_the_agents_block_edit_keeps_the_sent_head_and_marks_the_block(
        mk_bot, run_async, monkeypatch):
    env = surf.Env(mk_bot, run_async, monkeypatch, "en")
    rich, _bot, sess = _finish(env, PERSIAN)
    first = rich.await_args.args[1]
    rec = sess.agents_lines[0]
    assert rec["is_rtl"] is True
    bot = env.mk_bot()
    sess.bg_agents["a1"]["tool"] = "Read"
    sess.bg_agents["a1"]["tool_arg"] = "state.py"
    edit = AsyncMock(return_value={"message_id": 77})
    monkeypatch.setattr("aipager.bot.notify.edit_message_text_rich", edit)
    run_async(bot._tick_agents_line(sess, rec, surf.NOW + 60))
    sent = edit.await_args.args[2]
    assert edit.await_args.kwargs["is_rtl"] is True
    head = first.split("\n\n---\n\n", 1)[0]
    assert sent.startswith(head + "\n\n---\n\n")
    block = unfooter(sent.rsplit("\n\n---\n\n", 1)[1])
    assert "Reading state.py" in block
    for line in block.split("\n"):
        if line.strip():
            body = line[2:] if line.startswith("- ") else line
            assert body.startswith(LRM) and body.endswith(LRM), line


# ---- 4. a held answer ------------------------------------------------------------------

def _held(env, mk_bot, monkeypatch):
    from aipager.bot import held as held_mod
    from aipager.bot.rich_message import RichMessageFloodBanned
    monkeypatch.setattr("aipager.bot.notify.send_rich_message",
                        AsyncMock(side_effect=RichMessageFloodBanned(600, surf.CHAT)))
    bot = env.mk_bot()
    sess = surf._registered(bot, surf._sess(status=Status.IDLE))
    sess.busy_started_at = surf.NOW - 38
    sess.busy_started_wall = 0.0
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    env.run(bot.notify(sess, "idle_prompt", {"summary": PERSIAN}))
    (entry,) = held_mod.HELD.pending(surf.CHAT)
    assert entry.is_rtl is True
    bot._confirm_held = MagicMock()
    return bot, sess, entry


def test_a_held_answers_late_marker_is_marked(mk_bot, run_async, monkeypatch):
    env = surf.Env(mk_bot, run_async, monkeypatch, "en")
    bot, sess, entry = _held(env, mk_bot, monkeypatch)
    sent = AsyncMock(return_value={"message_id": 5})
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", sent)
    run_async(bot.flush_held_answers(sess))
    assert sent.await_args.kwargs["is_rtl"] is True
    marker, rest = sent.await_args.args[1].split("\n\n", 1)
    assert marker.startswith(LRM + "⏳") and marker.endswith(LRM)
    assert marker == held_message.late_marker_text(i18n.get("en").directed(True),
                                                   entry.held_seconds())
    assert rest.startswith(LRM + FSI + MARKED_LEAD)


def test_a_held_answers_plain_fallback_marker_is_unmarked(mk_bot, run_async, monkeypatch):
    from aipager.bot.held import HELD_ANSWER_MAX_ATTEMPTS
    env = surf.Env(mk_bot, run_async, monkeypatch, "en")
    bot, sess, entry = _held(env, mk_bot, monkeypatch)
    monkeypatch.setattr("aipager.bot.notify.send_rich_message",
                        AsyncMock(side_effect=RuntimeError("parse error")))
    for _ in range(HELD_ANSWER_MAX_ATTEMPTS):
        run_async(bot.flush_held_answers(sess))
    sent = [c.args[1] for c in bot._app.bot.send_message.await_args_list]
    assert sent, "the plain fallback went out"
    text = "".join(sent)
    assert text.startswith(held_message.late_marker_text(i18n.get("en"),
                                                         entry.held_seconds()))
    assert RESULT + " x1" in text and not INVISIBLE.search(text)


# ---- 5. the busy card's HTML frames -------------------------------------------------

def test_the_thinking_card_is_unmarked(mk_bot, run_async):
    _english(4343)
    bot = mk_bot()
    s = _session(bot)
    s.turn_prompt = PERSIAN
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=7))
    run_async(bot.send_busy(s))
    assert not INVISIBLE.search(bot._app.bot.send_message.await_args.args[1])


def test_the_legacy_head_is_unmarked(mk_bot):
    _english(4343)
    bot = mk_bot()
    s = _session(bot)
    s.turn_prompt = PERSIAN
    text = bot._build_busy_text(s.label, "Working", s)
    assert "x1" in text and not INVISIBLE.search(text)


def test_the_paused_html_line_is_unmarked(mk_bot, run_async, monkeypatch):
    from aipager.bot import animation as anim
    _english(4343)
    bot = mk_bot()
    s = _session(bot)
    s.turn_prompt = PERSIAN
    s.busy_msg_id = 55
    limiter = MagicMock()
    limiter.minimal_mode = MagicMock(return_value=True)
    monkeypatch.setattr(anim, "get_rate_limiter", lambda: limiter)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    assert run_async(bot._render_paused_card(s)) is True
    text = bot._edit_busy_raw.await_args.args[1]
    assert "x1" in text and not INVISIBLE.search(text)


def test_a_moved_paused_card_in_a_right_to_left_message_is_marked(mk_bot, run_async,
                                                                  monkeypatch):
    from aipager.bot import animation as anim
    _english(4343)
    bot = mk_bot()
    s = _session(bot, label="سفر")
    s.turn_prompt = PERSIAN
    s.busy_msg_id = 55
    s.stream_last_rendered = anim._PAUSED_CARD_TEXT
    rich = AsyncMock(return_value={"message_id": 56})
    monkeypatch.setattr(anim, "send_rich_message", rich)
    run_async(bot._send_card_copy(s, 9, verb="Working", final=False, waiting=False))
    assert rich.await_args.kwargs["is_rtl"] is True
    text = rich.await_args.args[1]
    assert text.startswith(LRM) and text.endswith(LRM)
    assert FSI + "سفر" + PDI in text
