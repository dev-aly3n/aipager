"""Tests for perms-mode callback actions and allow_always keystroke injection."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aipager.state import Status, TrackedSession


@pytest.fixture
def mk_query():
    """Build a mocked Telegram CallbackQuery."""
    def _mk(callback_data, *, user_id=12345, message_id=42, text=""):
        query = MagicMock()
        query.data = callback_data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message = MagicMock()
        query.message.message_id = message_id
        query.message.text = text
        query.message.chat = MagicMock()
        query.message.chat.id = -100
        query.from_user = MagicMock()
        query.from_user.id = user_id
        update = MagicMock()
        update.callback_query = query
        update.effective_user = query.from_user
        update.effective_chat = MagicMock()
        update.effective_chat.id = -100
        return update, query
    return _mk


# ---- permission-answer keystroke sequences ---------------------------------
#
# Claude Code's permission menu has no fixed length — a tool with a
# directory scope to widen renders "Yes / Yes-and-always / No", one without
# renders just "Yes / No" — and the daemon never sees the labels. These
# tests pin the cursor movement for each button, because an off-by-one here
# does not fail loudly: it silently selects the neighbouring option. Deny
# sending one Down used to select "Yes, and always allow", which ran the
# refused tool and widened permissions while reporting "Denied".

def _keys_for(bot, mk_query, run_async, action, *, pending=None):
    """Run a permission callback and return the keys it injected.

    ``pending`` is the session's ``pending_permission`` — what the hook
    said about the prompt (notably ``tool_info["always_available"]``).
    """
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.INTERACTIVE)
    sess.pending_permission = pending
    bot.registry._sessions["claude-dev"] = sess
    update, _query = mk_query(f"claude-dev:{action}")

    key_calls = []

    async def mock_send_keys(session_name, key):
        key_calls.append(key)
        return True

    async def mock_is_alive(name):
        return True

    with patch("aipager.dtach.inject.send_keys", side_effect=mock_send_keys), \
         patch("aipager.dtach.inject.is_alive", side_effect=mock_is_alive):
        run_async(bot._handle_callback(update, MagicMock()))
    return key_calls


def test_allow_sends_only_enter(mk_bot, mk_query, run_async):
    """Allow confirms the pre-selected first item, which is always "Yes"."""
    assert _keys_for(mk_bot(), mk_query, run_async, "allow") == ["Enter"]


_WITH_RULE = {"tool_summary": "Bash: x",
              "tool_info": {"name": "Bash", "always_available": True}}


def test_allow_always_sends_one_down_then_enter_when_a_rule_exists(mk_bot, mk_query, run_async):
    """Allow-always picks item 2, "Yes, and don't ask again for …" — which
    exists exactly when the hook carried permission suggestions."""
    assert _keys_for(mk_bot(), mk_query, run_async, "allow_always",
                     pending=_WITH_RULE) == ["Down", "Enter"]


@pytest.mark.parametrize("pending", [
    {"tool_summary": "Bash: x", "tool_info": {"name": "Bash", "always_available": False}},
    {"tool_summary": "Bash: x", "tool_info": {"name": "Bash"}},
    {"tool_summary": "Bash: x"},
    None,
])
def test_allow_always_without_a_rule_only_confirms_yes(mk_bot, mk_query, run_async, pending):
    """Claude Code 2.1.259: on a Bash prompt with no rule to widen, item 2
    is "Yes, and switch to auto mode". A blind Down+Enter would flip the
    session into auto mode while reporting "Allowed always" — so with the
    flag False or unknown the tap must confirm the pre-selected "Yes"
    and nothing else."""
    assert _keys_for(mk_bot(), mk_query, run_async, "allow_always",
                     pending=pending) == ["Enter"], pending


def test_allow_always_degraded_tap_says_so(mk_bot, mk_query, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.INTERACTIVE)
    sess.pending_permission = {"tool_summary": "Bash: x",
                               "tool_info": {"name": "Bash", "always_available": False}}
    bot.registry._sessions["claude-dev"] = sess
    update, query = mk_query("claude-dev:allow_always")
    with patch("aipager.dtach.inject.send_keys", AsyncMock(return_value=True)), \
         patch("aipager.dtach.inject.is_alive", AsyncMock(return_value=True)):
        run_async(bot._handle_callback(update, MagicMock()))
    toasts = [c.args[0] for c in query.answer.await_args_list
              if c.args and isinstance(c.args[0], str)]
    assert any("allowed once" in t for t in toasts), toasts
    assert not any("Allowed always" in t for t in toasts), toasts
    # and the record reads as a single allow, not a widened rule
    assert any("→ Allowed" in summary and "always" not in summary.lower()
               for summary, _done in sess.tool_history), sess.tool_history


def _answer_for(bot, mk_query, run_async, action, *, pending=None,
                 send_decision_result=True):
    """Like :func:`_keys_for`, but also patches
    ``aipager.dtach.hook_reply.send_decision`` and ``aipager.audit.append``.

    Returns ``(keys_sent, hook_decision_calls, via)`` — ``hook_decision_calls``
    is the list of ``(hook_reply, decision)`` tuples ``send_decision`` was
    called with, ``via`` is the last ``audit.append(..., via=...)`` kwarg
    seen (``None`` if audit.append was never called, e.g. no
    ``pending_permission`` at all)."""
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.INTERACTIVE)
    sess.pending_permission = pending
    bot.registry._sessions["claude-dev"] = sess
    update, query = mk_query(f"claude-dev:{action}")

    key_calls = []
    hook_decision_calls = []
    audit_calls = []

    async def mock_send_keys(session_name, key):
        key_calls.append(key)
        return True

    async def mock_is_alive(name):
        return True

    def mock_send_decision(hook_reply_dict, decision):
        hook_decision_calls.append((hook_reply_dict, decision))
        # Mirror the real function's own defensive contract: a missing/
        # malformed hook_reply is always False, regardless of
        # send_decision_result — only a genuinely present hook_reply's
        # outcome is under this fixture's control.
        if not isinstance(hook_reply_dict, dict):
            return False
        return send_decision_result

    def mock_audit_append(**kwargs):
        audit_calls.append(kwargs)
        return True

    with patch("aipager.dtach.inject.send_keys", side_effect=mock_send_keys), \
         patch("aipager.dtach.inject.is_alive", side_effect=mock_is_alive), \
         patch("aipager.dtach.hook_reply.send_decision", side_effect=mock_send_decision), \
         patch("aipager.audit.append", side_effect=mock_audit_append):
        run_async(bot._handle_callback(update, MagicMock()))

    via = audit_calls[-1]["via"] if audit_calls else None
    return key_calls, hook_decision_calls, via


_HOOK_REPLY = {"addr": "/tmp/aipager-reply-test.sock", "request_id": "deadbeef" * 4}
_STANDING_SUGGESTION = {
    "type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "ls"}],
    "behavior": "allow", "destination": "localSettings",
}


# ---- hook-decision-succeeds: zero keystrokes, via="hook_decision" ---------

def test_allow_uses_hook_decision_when_available(mk_bot, mk_query, run_async):
    pending = {"tool_summary": "Bash: x", "tool_info": {"name": "Bash"},
               "hook_reply": _HOOK_REPLY}
    keys, calls, via = _answer_for(mk_bot(), mk_query, run_async, "allow", pending=pending)
    assert keys == []
    assert len(calls) == 1
    hr, decision = calls[0]
    assert hr == _HOOK_REPLY
    assert decision == {"behavior": "allow"}
    assert via == "hook_decision"


def test_allow_always_uses_hook_decision_with_standing_rule(mk_bot, mk_query, run_async):
    pending = {"tool_summary": "Bash: x",
               "tool_info": {"name": "Bash", "always_available": True,
                             "standing_rule_suggestion": _STANDING_SUGGESTION},
               "hook_reply": _HOOK_REPLY}
    keys, calls, via = _answer_for(mk_bot(), mk_query, run_async, "allow_always", pending=pending)
    assert keys == []
    assert len(calls) == 1
    hr, decision = calls[0]
    assert hr == _HOOK_REPLY
    assert decision == {"behavior": "allow", "updatedPermissions": [_STANDING_SUGGESTION]}
    assert via == "hook_decision"


def test_allow_always_degraded_still_tries_a_plain_hook_decision(mk_bot, mk_query, run_async):
    """No standing rule to echo — still tries the hook path first with a
    PLAIN allow (no updatedPermissions), which needs no navigation."""
    pending = {"tool_summary": "Bash: x",
               "tool_info": {"name": "Bash", "always_available": False,
                             "standing_rule_suggestion": None},
               "hook_reply": _HOOK_REPLY}
    keys, calls, via = _answer_for(mk_bot(), mk_query, run_async, "allow_always", pending=pending)
    assert keys == []
    assert len(calls) == 1
    hr, decision = calls[0]
    assert decision == {"behavior": "allow"}
    assert via == "hook_decision"


def test_deny_uses_hook_decision_when_available(mk_bot, mk_query, run_async):
    pending = {"tool_summary": "Bash: x", "tool_info": {"name": "Bash"},
               "hook_reply": _HOOK_REPLY}
    keys, calls, via = _answer_for(mk_bot(), mk_query, run_async, "deny", pending=pending)
    assert keys == []
    assert len(calls) == 1
    hr, decision = calls[0]
    assert hr == _HOOK_REPLY
    assert via == "hook_decision"


# ---- hook-decision-fails (or absent): existing keystroke fallback ----------

def test_allow_falls_back_to_keystrokes_when_hook_reply_absent(mk_bot, mk_query, run_async):
    pending = {"tool_summary": "Bash: x", "tool_info": {"name": "Bash"}}
    keys, calls, via = _answer_for(mk_bot(), mk_query, run_async, "allow", pending=pending)
    assert keys == ["Enter"]
    assert via == "keystroke_fallback"


def test_allow_falls_back_to_keystrokes_when_send_decision_fails(mk_bot, mk_query, run_async):
    pending = {"tool_summary": "Bash: x", "tool_info": {"name": "Bash"},
               "hook_reply": _HOOK_REPLY}
    keys, calls, via = _answer_for(mk_bot(), mk_query, run_async, "allow", pending=pending,
                                    send_decision_result=False)
    assert keys == ["Enter"]
    assert len(calls) == 1
    assert via == "keystroke_fallback"


def test_allow_always_falls_back_to_existing_keystrokes_when_hook_fails(mk_bot, mk_query, run_async):
    pending = {"tool_summary": "Bash: x",
               "tool_info": {"name": "Bash", "always_available": True,
                             "standing_rule_suggestion": _STANDING_SUGGESTION},
               "hook_reply": _HOOK_REPLY}
    keys, calls, via = _answer_for(mk_bot(), mk_query, run_async, "allow_always", pending=pending,
                                    send_decision_result=False)
    assert keys == ["Down", "Enter"]
    assert via == "keystroke_fallback"


def test_deny_falls_back_to_existing_overshoot_when_hook_fails(mk_bot, mk_query, run_async):
    pending = {"tool_summary": "Bash: x", "tool_info": {"name": "Bash"},
               "hook_reply": _HOOK_REPLY}
    keys, calls, via = _answer_for(mk_bot(), mk_query, run_async, "deny", pending=pending,
                                    send_decision_result=False)
    assert keys[-1] == "Enter"
    assert set(keys[:-1]) == {"Down"}
    assert len(keys) - 1 >= 4
    assert via == "keystroke_fallback"


def test_existing_pinned_keystroke_tests_still_pass_via_answer_for(mk_bot, mk_query, run_async):
    """Regression pin: with no hook_reply at all, the answer_for harness
    reproduces exactly what _keys_for's own dedicated tests already pin —
    this is the 'existing keystroke sequences fire exactly' requirement,
    checked through the new harness too."""
    keys, calls, via = _answer_for(mk_bot(), mk_query, run_async, "allow",
                                    pending={"tool_info": {"name": "Bash"}})
    assert keys == ["Enter"]
    assert calls and calls[0][1] == {"behavior": "allow"}  # hook WAS attempted
    assert via == "keystroke_fallback"  # ...but it failed (no hook_reply), so keys fired


# ---- AskUserQuestion guard: hook_reply.send_decision never called ----------

@pytest.mark.parametrize("action", ["allow", "allow_always", "deny"])
def test_ask_user_question_never_attempts_hook_decision(mk_bot, mk_query, run_async, action):
    pending = {"tool_summary": "AskUserQuestion (loading…)",
               "tool_info": {"name": "AskUserQuestion",
                             "standing_rule_suggestion": _STANDING_SUGGESTION},
               "hook_reply": _HOOK_REPLY}
    keys, calls, via = _answer_for(mk_bot(), mk_query, run_async, action, pending=pending)
    assert calls == []
    assert via == "keystroke_fallback"


# ---- deny message shape: PermissionRequest {behavior, message, interrupt} -

def test_deny_decision_shape_is_permission_request_not_pretooluse(mk_bot, mk_query, run_async):
    pending = {"tool_summary": "Bash: x", "tool_info": {"name": "Bash"},
               "hook_reply": _HOOK_REPLY}
    _keys, calls, _via = _answer_for(mk_bot(), mk_query, run_async, "deny", pending=pending)
    assert len(calls) == 1
    _hr, decision = calls[0]
    assert decision.keys() == {"behavior", "message", "interrupt"}
    assert decision["behavior"] == "deny"
    assert isinstance(decision["message"], str) and decision["message"]
    assert decision["interrupt"] is False
    # Never the PreToolUse shape (enforce.deny_decision_json).
    assert "reason" not in decision
    assert "decision" not in decision


# ---- allow_always defense-in-depth: never echoes setMode -------------------

def test_allow_always_never_echoes_setmode_even_if_it_slips_through(mk_bot, mk_query, run_async):
    """Simulates a hypothetical future bug upstream that let a non-
    standing-rule suggestion through onto tool_info — the callback layer
    must independently refuse to echo it, not just trust the retained
    value."""
    bogus = {"type": "setMode", "mode": "acceptEdits", "destination": "session"}
    pending = {"tool_summary": "Write: x",
               "tool_info": {"name": "Write", "always_available": True,
                             "standing_rule_suggestion": bogus},
               "hook_reply": _HOOK_REPLY}
    keys, calls, via = _answer_for(mk_bot(), mk_query, run_async, "allow_always", pending=pending)
    assert keys == []
    assert len(calls) == 1
    _hr, decision = calls[0]
    assert "updatedPermissions" not in decision
    assert decision == {"behavior": "allow"}
    assert via == "hook_decision"


def test_deny_overshoots_to_the_last_option(mk_bot, mk_query, run_async):
    """Deny must overshoot, not step to a fixed index.

    The menu clamps at its last item rather than wrapping, so pushing Down
    past the end lands on the refusal whatever the menu's length. Stepping
    a fixed number of times selects whatever happens to sit at that index —
    which for one Down is "Yes, and always allow".
    """
    keys = _keys_for(mk_bot(), mk_query, run_async, "deny")

    assert keys[-1] == "Enter", f"deny must confirm a selection; got {keys}"
    downs = keys[:-1]
    assert set(downs) == {"Down"}, f"deny must only move down; got {keys}"
    # 2.1.259 menus are up to four rows (Yes / don't-ask-again / switch to
    # auto mode / No): two Downs from the top stop ON the auto-mode row,
    # which is far worse than an affirmative; four is the margin we pin.
    assert len(downs) >= 4, (
        f"deny sent {len(downs)} Down(s) — too few to clamp past a "
        f"four-row menu, so it can select 'switch to auto mode' instead of "
        f"the refusal; got {keys}"
    )


# ---- perms_confirm: executes mode switch -----------------------------------

def test_perms_confirm_calls_do_perms_switch(mk_bot, mk_query, run_async):
    """Tapping 'Yes, switch' confirms the perms switch."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.IDLE)
    sess.skip_perms = False
    bot.registry._sessions["claude-dev"] = sess
    # Set up pending state
    bot._perms_pending["claude-dev"] = {
        "target_skip_perms": True,
        "msg_id": 42,
        "label": "dev",
    }
    bot._do_perms_switch_via_fn = AsyncMock()
    update, query = mk_query("claude-dev:perms_confirm")
    run_async(bot._handle_callback(update, MagicMock()))
    bot._do_perms_switch_via_fn.assert_awaited_once()
    # Pending state should be cleared
    assert "claude-dev" not in bot._perms_pending


# ---- perms_cancel: edits message to Cancelled ------------------------------

def test_perms_cancel_edits_to_cancelled(mk_bot, mk_query, run_async):
    """Tapping Cancel edits message to cancelled notice."""
    bot = mk_bot()
    bot._perms_pending["claude-dev"] = {
        "target_skip_perms": True,
        "msg_id": 42,
        "label": "dev",
    }
    update, query = mk_query("claude-dev:perms_cancel")
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_awaited_once()
    msg = query.edit_message_text.await_args[0][0]
    assert "Cancelled" in msg
    assert "claude-dev" not in bot._perms_pending


# ---- perms_wait: cancels and shows retry hint ------------------------------

def test_perms_wait_edits_to_retry_hint(mk_bot, mk_query, run_async):
    """Tapping 'Not now' edits message to a try-again hint."""
    bot = mk_bot()
    bot._perms_pending["claude-dev"] = {
        "target_skip_perms": True,
        "msg_id": 42,
        "label": "dev",
    }
    update, query = mk_query("claude-dev:perms_wait")
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_awaited_once()
    msg = query.edit_message_text.await_args[0][0]
    # Should contain hint to retry later
    assert "perms" in msg.lower() or "Cancelled" in msg
    assert "claude-dev" not in bot._perms_pending


# ---- resume_mode_ask: calls _do_resume with skip_perms_override=False ------

def test_resume_mode_ask_calls_do_resume_false(mk_bot, mk_query, run_async):
    bot = mk_bot()
    bot._resume_mode_pending["claude-dev"] = "dev"
    bot._do_resume = AsyncMock()
    update, query = mk_query("claude-dev:resume_mode_ask")
    run_async(bot._handle_callback(update, MagicMock()))
    bot._do_resume.assert_awaited_once()
    kwargs = bot._do_resume.await_args[1]
    assert kwargs["skip_perms_override"] is False
    assert kwargs["label"] == "dev"
    assert "claude-dev" not in bot._resume_mode_pending


# ---- resume_mode_auto: calls _do_resume with skip_perms_override=True ------

def test_resume_mode_auto_calls_do_resume_true(mk_bot, mk_query, run_async):
    bot = mk_bot()
    bot._resume_mode_pending["claude-dev"] = "dev"
    bot._do_resume = AsyncMock()
    update, query = mk_query("claude-dev:resume_mode_auto")
    run_async(bot._handle_callback(update, MagicMock()))
    bot._do_resume.assert_awaited_once()
    kwargs = bot._do_resume.await_args[1]
    assert kwargs["skip_perms_override"] is True
    assert kwargs["label"] == "dev"
    assert "claude-dev" not in bot._resume_mode_pending


# ---- resume_mode_cancel: edits to Cancelled --------------------------------

def test_resume_mode_cancel_edits_to_cancelled(mk_bot, mk_query, run_async):
    bot = mk_bot()
    bot._resume_mode_pending["claude-dev"] = "dev"
    update, query = mk_query("claude-dev:resume_mode_cancel")
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_awaited_once()
    msg = query.edit_message_text.await_args[0][0]
    assert "Cancelled" in msg
    assert "claude-dev" not in bot._resume_mode_pending


# ---- perms_stop_switch: sends Ctrl-C then relaunches -----------------------

def test_perms_stop_switch_sends_ctrl_c(mk_bot, mk_query, run_async):
    """Tapping 'Stop task & switch' sends Ctrl-C and then relaunches."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.BUSY)
    sess.skip_perms = False
    sess.claude_session_id = "some-uuid"
    sess.cwd = "/home/user/project"
    bot.registry._sessions["claude-dev"] = sess
    bot._perms_pending["claude-dev"] = {
        "target_skip_perms": True,
        "msg_id": 42,
        "label": "dev",
    }

    ctrl_c_calls = []
    launch_calls = []

    async def mock_send_keys(session_name, key):
        ctrl_c_calls.append(key)
        return True

    async def mock_launch_session(short_name, *, skip_perms=False, **kw):
        launch_calls.append({"short_name": short_name, "skip_perms": skip_perms})
        return True, ""

    update, query = mk_query("claude-dev:perms_stop_switch")


    with patch("aipager.dtach.inject.send_keys", side_effect=mock_send_keys), \
         patch("aipager.dtach.inject.launch_session", side_effect=mock_launch_session), \
         patch("aipager.bot.callbacks.Path") as mock_path_cls:
        # Make the socket appear gone immediately
        mock_path_cls.return_value.is_socket.return_value = False
        run_async(bot._handle_callback(update, MagicMock()))

    # Ctrl-C should have been sent
    assert "C-c" in ctrl_c_calls
    # Launch should have been called with skip_perms=True
    assert any(c["skip_perms"] is True for c in launch_calls), launch_calls
    # Session should be updated
    assert sess.skip_perms is True
