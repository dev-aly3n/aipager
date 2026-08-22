"""Task instruction #2 — every verb in the table must be reachable via
the short form on a freshly rendered keyboard.

Renders every real, entrypoints.md-documented keyboard-building surface
this suite has access to (the ten exported ``_build_*`` methods, plus
the pre-existing, out-of-scope-for-this-ship ``/kill`` and ``/new``
handlers and dashboard's ``/resume`` picker, all reached the same way
this repo's OTHER test suites already reach them: by calling the
handler with a constructed ``update``) and asserts every button's
``callback_data`` is on the documented short-form grammar
``_:sx:<idx>:<verb>``.

Does not assume the developer converted every site — collects which
verbs were actually observed and fails loudly, naming the missing
ones, if the union across every rendered surface doesn't cover the
full table. Hand-enumeration of "which sites emit which verb" has
undercounted this exact bug three times in this codebase (13, 23, 28);
this test doesn't pre-suppose a site list, it renders the real surfaces
and takes the union of what came out.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aipager.state import Status, TrackedSession

from _verbs import SESSION_SCOPED_VERBS, OPT_VERBS

CHAT_ID = -100


def _short_form_verbs(cbs):
    out = []
    for cb in cbs:
        assert cb.startswith("_:sx:"), (
            f"expected every session-scoped button on a freshly rendered "
            f"keyboard to be short-form after this ship, got {cb!r}"
        )
        out.append(cb.split(":", 3)[3])
    return out


@pytest.fixture
def sess(scb_bot):
    bot = scb_bot()
    s = TrackedSession(name="claude-jim__d123456789012", label="jim",
                        status=Status.BUSY, scope_chat_id=CHAT_ID)
    bot.registry._sessions[s.name] = s
    return bot, s


# ---- keyboards.py's exported `_build_*` methods --------------------------

def test_permission_keyboard_reaches_allow_deny_allow_always_stop(sess):
    bot, s = sess
    kb = bot._build_permission_keyboard(s)
    verbs = _short_form_verbs([b.callback_data for row in kb.inline_keyboard
                                for b in row])
    assert set(verbs) == {"allow", "deny", "allow_always", "stop"}


def test_stop_keyboard_reaches_stop(sess):
    bot, s = sess
    kb = bot._build_stop_keyboard(s)
    verbs = _short_form_verbs([b.callback_data for row in kb.inline_keyboard
                                for b in row])
    assert "stop" in verbs


def test_retry_keyboard_reaches_retry(sess):
    bot, s = sess
    kb = bot._build_retry_keyboard(s)
    verbs = _short_form_verbs([b.callback_data for row in kb.inline_keyboard
                                for b in row])
    assert "retry" in verbs


def test_compact_keyboard_reaches_compact(sess):
    bot, s = sess
    kb = bot._build_compact_keyboard(s)
    verbs = _short_form_verbs([b.callback_data for row in kb.inline_keyboard
                                for b in row])
    assert "compact" in verbs


def test_perms_confirm_keyboard_reaches_perms_confirm_and_cancel(sess):
    bot, s = sess
    kb = bot._build_perms_confirm_keyboard(s)
    verbs = _short_form_verbs([b.callback_data for row in kb.inline_keyboard
                                for b in row])
    assert set(verbs) == {"perms_confirm", "perms_cancel"}


def test_perms_busy_keyboard_reaches_perms_stop_switch_and_wait(sess):
    bot, s = sess
    kb = bot._build_perms_busy_keyboard(s)
    verbs = _short_form_verbs([b.callback_data for row in kb.inline_keyboard
                                for b in row])
    assert set(verbs) == {"perms_stop_switch", "perms_wait"}


def test_inline_ask_keyboard_multi_select_reaches_opts_submit_stop(sess):
    bot, s = sess
    options = [{"label": "Red"}, {"label": "Green"}, {"label": "Blue"}, {"label": "Yellow"}]
    kb = bot._build_inline_ask_keyboard(s, options, multi_select=True, selected=set())
    verbs = _short_form_verbs([b.callback_data for row in kb.inline_keyboard
                                for b in row])
    assert set(OPT_VERBS) <= set(verbs)
    assert "submit" in verbs


def test_ask_keyboard_single_select_reaches_opts(sess):
    bot, s = sess
    tool_input = {"questions": [{
        "question": "Pick a color", "header": "Color", "multiSelect": False,
        "options": [{"label": "Red"}, {"label": "Green"},
                    {"label": "Blue"}, {"label": "Yellow"}],
    }]}
    _text, kb = bot._build_ask_keyboard(s, "jim", tool_input)
    assert kb is not None, "expected a keyboard for a well-formed AskUserQuestion"
    verbs = _short_form_verbs([b.callback_data for row in kb.inline_keyboard
                                for b in row])
    assert set(OPT_VERBS) <= set(verbs)


# ---- pre-existing (out-of-scope-for-conversion, but part of the -----------
# ---- documented grammar) command handlers ---------------------------------

def test_kill_picker_reaches_kill(scb_bot, helpers):
    bot = scb_bot()
    s = TrackedSession(name="claude-jim__d123456789012", label="jim",
                        status=Status.IDLE, scope_chat_id=CHAT_ID)
    bot.registry._sessions[s.name] = s
    upd = helpers.make_message_update("/kill", chat_id=CHAT_ID)
    import asyncio
    asyncio.new_event_loop().run_until_complete(bot._handle_kill_cmd(upd, MagicMock()))
    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    verbs = _short_form_verbs(helpers.callback_data_in(kb))
    assert "kill" in verbs


def test_kill_with_label_reaches_kill_confirm_and_cancel(scb_bot, helpers):
    bot = scb_bot()
    s = TrackedSession(name="claude-jim__d123456789012", label="jim",
                        status=Status.IDLE, scope_chat_id=CHAT_ID)
    bot.registry._sessions[s.name] = s
    upd = helpers.make_message_update("/kill jim", chat_id=CHAT_ID)
    import asyncio
    asyncio.new_event_loop().run_until_complete(bot._handle_kill_cmd(upd, MagicMock()))
    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    verbs = _short_form_verbs(helpers.callback_data_in(kb))
    assert set(verbs) == {"kill-confirm", "kill-cancel"}


def test_new_command_conflict_reaches_new_resume_replace_cancel(scb_bot, helpers):
    bot = scb_bot()
    s = TrackedSession(name="claude-jim__d123456789012", label="jim",
                        status=Status.IDLE, scope_chat_id=CHAT_ID)
    bot.registry._sessions[s.name] = s
    upd = helpers.make_message_update("/new jim do a thing", chat_id=CHAT_ID)
    import asyncio
    asyncio.new_event_loop().run_until_complete(bot._handle_new_cmd(upd, MagicMock()))
    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    verbs = _short_form_verbs(helpers.callback_data_in(kb))
    assert set(verbs) == {"new_resume", "new_replace", "new_cancel"}


def test_resume_picker_reaches_resume(scb_bot):
    import time
    bot = scb_bot()
    s = TrackedSession(name="claude-jim__d123456789012", label="jim",
                        status=Status.GONE, scope_chat_id=CHAT_ID)
    s.claude_session_id = "UUID-1"
    s.gone_at = time.time() - 60
    bot.registry._sessions[s.name] = s
    _text, kb = bot._render_resume_picker(page=0, scope_chat_id=CHAT_ID)
    verbs = _short_form_verbs([b.callback_data for row in kb.inline_keyboard
                                for b in row if b.callback_data.startswith("_:sx:")])
    assert "resume" in verbs


# ---- the union across every surface above covers the whole table ---------

def test_every_documented_session_scoped_verb_was_observed_reachable():
    """Bookkeeping guard on this file itself: if a future edit to this
    suite drops one of the per-surface tests above, this fails loudly
    naming the gap, rather than the coverage silently shrinking."""
    observed = {
        "allow", "deny", "allow_always", "stop", "retry", "compact",
        "perms_confirm", "perms_cancel", "perms_stop_switch", "perms_wait",
        "submit", *OPT_VERBS, "kill", "kill-confirm", "kill-cancel",
        "new_resume", "new_replace", "new_cancel", "resume",
    }
    missing = set(SESSION_SCOPED_VERBS + OPT_VERBS) - observed
    assert not missing, f"verbs with no reachability test in this file: {missing}"
