"""The shared model list and the Telegram side of switching a running
session's model (roadmap 8.35).

One list feeds three pickers: the Telegram Models keyboard, the Mini App
launch picker and the Mini App running-session picker. These tests pin
what is on it, that the chat keyboard is built from it, that
``keyboard.json`` still overrides it, and that a ``/model`` sent from chat
follows the same busy rule as the Mini App.
"""

from __future__ import annotations

import asyncio
import json
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import config
from aipager.miniapp.launch import validate_model
from aipager.miniapp.sessions import MODEL_SWITCH_BUSY_REASON
from aipager.state import Status, TrackedSession

# ===== the shared list ======================================================

_REQUIRED_MODELS = {
    "opus", "sonnet", "haiku", "fable",
    "claude-opus-5-5", "claude-opus-5-5[1m]", "claude-sonnet-5",
    "claude-fable-5-1", "claude-haiku-4-5",
}


def test_the_catalog_carries_every_current_model():
    models = {model for _label, model, _hint in config.MODEL_CATALOG}
    missing = _REQUIRED_MODELS - models
    assert not missing, f"missing from the shared model list: {sorted(missing)}"


def test_the_telegram_default_keyboard_is_built_from_the_catalog():
    assert config._DEFAULT_MODELS == [
        (label, f"/model {model}") for label, model, _hint in config.MODEL_CATALOG
    ]


def test_labels_are_unique_case_insensitively():
    """A label is how the chat keyboard AND the Mini App pickers resolve a
    row back to its model; two rows answering to one label would make one
    of them unreachable."""
    labels = [label.lower() for label, _m, _h in config.MODEL_CATALOG]
    assert len(labels) == len(set(labels))


def test_no_hint_carries_a_version_number():
    """An alias resolves to the latest of its family, so a version in a
    hint goes stale on the next release. Versions live in the pinned
    rows' LABELS only."""
    for label, model, hint in config.MODEL_CATALOG:
        assert not re.search(r"\d", hint), f"{label!r} hint has a digit: {hint!r}"
        if model.startswith("claude-"):
            assert hint == "", f"pinned row {label!r} should be a label only"


def test_model_hint_is_looked_up_by_label():
    assert config.model_hint("Opus") == dict(
        (lbl, h) for lbl, _m, h in config.MODEL_CATALOG)["Opus"]
    assert config.model_hint("opus") == config.model_hint("Opus")
    assert config.model_hint("Something an operator made up") == ""


def test_every_catalog_row_passes_the_launch_picker_validation():
    """The launch picker and the running picker validate the same way; a
    shipped row the validator refuses would be a dead button."""
    for label, model, _hint in config.MODEL_CATALOG:
        assert validate_model(label, config._DEFAULT_MODELS) == (model, ""), label


def test_a_keyboard_json_models_override_still_wins(tmp_path, monkeypatch):
    path = tmp_path / "keyboard.json"
    path.write_text(json.dumps({
        "models": [{"label": "Mine", "send": "/model claude-opus-4-5"}],
    }))
    monkeypatch.setattr(config, "_KEYBOARD_CONFIG_PATH", path)
    _t, _c, models = config._load_keyboard_overrides()
    assert models == [("Mine", "/model claude-opus-4-5")]
    assert validate_model("Mine", models) == ("claude-opus-4-5", "")


@pytest.mark.parametrize("value,expected", [
    ("claude-opus-5-5[1m]", "claude-opus-5-5[1m]"),
    ("opus[1m]", "opus[1m]"),
    ("sonnet[1m]", "sonnet[1m]"),
])
def test_the_one_million_context_suffix_is_accepted(value, expected):
    assert validate_model(value, []) == (expected, "")


@pytest.mark.parametrize("value", [
    "opus[2m]", "opus[1m", "opus1m]", "[1m]", "opus[1m][1m]", "opus[1m]x",
    "-x[1m]",
])
def test_only_the_exact_one_million_suffix_is_accepted(value):
    resolved, err = validate_model(value, [])
    assert resolved == "" and err, value


# ===== Telegram =============================================================

def _session(bot, status, model_name="Sonnet 5"):
    sess = TrackedSession(name="claude-jim", label="jim", status=status,
                          model_name=model_name)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    return sess


def test_telegram_model_switch_while_busy_is_refused(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """R2, the chat half: the same refusal, the same words."""
    bot = mk_bot()
    _session(bot, Status.BUSY)
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    send = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", send)
    update = mk_update("Opus")
    run_async(bot._send_command(update, "/model opus"))
    send.assert_not_awaited()
    text = update.message.reply_text.await_args.args[0]
    assert MODEL_SWITCH_BUSY_REASON in text


def test_telegram_other_commands_still_go_through_while_busy(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """The busy refusal is for /model only — /compact while busy is fine."""
    bot = mk_bot()
    _session(bot, Status.BUSY)
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    send = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", send)
    bot._react = AsyncMock()
    run_async(bot._send_command(mk_update("Compact"), "/compact"))
    send.assert_awaited_once()


def test_telegram_model_switch_schedules_the_confirmation(
    mk_bot, mk_update, run_async, monkeypatch,
):
    bot = mk_bot()
    sess = _session(bot, Status.IDLE, model_name="Sonnet 5")
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    send = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", send)
    bot._react = AsyncMock()
    bot._confirm_model_feedback = AsyncMock()
    update = mk_update("Opus 5.5")
    run_async(bot._send_command(update, "/model claude-opus-5-5"))
    assert send.await_args.args == ("claude-jim", "/model claude-opus-5-5")
    bot._react.assert_awaited()          # the reaction stays
    bot._confirm_model_feedback.assert_called_once()
    args = bot._confirm_model_feedback.call_args.args
    assert args[1] is sess and args[2] == "Sonnet 5"


def test_telegram_feedback_shows_the_new_model_once_it_changes(run_async, mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", model_name="Sonnet 5")
    msg = MagicMock()
    msg.chat_id = -1001
    msg.edit_text = AsyncMock()

    async def _run():
        asyncio.get_running_loop().call_later(0.05, setattr, sess, "model_name", "Opus 5.5")
        await bot._confirm_model_feedback(msg, sess, "Sonnet 5", "HEAD", timeout=2.0)
    run_async(_run())
    text = msg.edit_text.await_args.args[0]
    assert text.startswith("HEAD")
    assert "Opus 5.5" in text


def test_telegram_feedback_says_unconfirmed_on_timeout(run_async, mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", model_name="Sonnet 5")
    msg = MagicMock()
    msg.chat_id = -1001
    msg.edit_text = AsyncMock()
    run_async(bot._confirm_model_feedback(msg, sess, "Sonnet 5", "HEAD", timeout=0.1))
    text = msg.edit_text.await_args.args[0]
    assert "not confirmed — check the session" in text


def _stub_pty(monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    send = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", send)
    return send


def test_telegram_model_switch_while_a_prompt_is_open_is_refused_not_held(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """Before 8.35 a /model sent while a prompt was open was HELD and typed
    in after the answer. It is now refused, like the Mini App."""
    from aipager.miniapp.sessions import MODEL_SWITCH_PROMPT_OPEN_REASON

    bot = mk_bot()
    sess = _session(bot, Status.INTERACTIVE)
    send = _stub_pty(monkeypatch)
    update = mk_update("Opus")
    run_async(bot._send_command(update, "/model opus"))
    send.assert_not_awaited()
    assert sess.pending_queue == []
    assert MODEL_SWITCH_PROMPT_OPEN_REASON in update.message.reply_text.await_args.args[0]


def test_telegram_model_switch_on_an_unknown_session_is_refused(
    mk_bot, mk_update, run_async, monkeypatch,
):
    from aipager.miniapp.sessions import MODEL_SWITCH_UNKNOWN_REASON

    bot = mk_bot()
    _session(bot, Status.UNKNOWN)
    send = _stub_pty(monkeypatch)
    update = mk_update("Opus")
    run_async(bot._send_command(update, "/model opus"))
    send.assert_not_awaited()
    assert MODEL_SWITCH_UNKNOWN_REASON in update.message.reply_text.await_args.args[0]


def test_telegram_second_switch_is_refused_while_the_first_is_pending(
    mk_bot, mk_update, run_async, monkeypatch,
):
    from aipager.miniapp.sessions import MODEL_SWITCH_PENDING_REASON

    bot = mk_bot()
    _session(bot, Status.IDLE)
    send = _stub_pty(monkeypatch)
    bot._react = AsyncMock()
    bot._confirm_model_feedback = AsyncMock()
    run_async(bot._send_command(mk_update("Opus"), "/model opus"))
    assert send.await_count == 1
    update = mk_update("Sonnet")
    run_async(bot._send_command(update, "/model sonnet"))
    assert send.await_count == 1
    assert MODEL_SWITCH_PENDING_REASON in update.message.reply_text.await_args.args[0]


def test_telegram_feedback_with_no_baseline_is_unconfirmed(run_async, mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", model_name="Sonnet 5")
    msg = MagicMock()
    msg.chat_id = -1001
    msg.edit_text = AsyncMock()
    run_async(bot._confirm_model_feedback(msg, sess, "", "HEAD", timeout=2.0))
    assert "not confirmed" in msg.edit_text.await_args.args[0]


# ===== review iteration 2 ===================================================

def test_telegram_failed_send_leaves_nothing_pending(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """A /model that never reached the terminal cannot have opened Claude
    Code's dialog, so it must not block the next switch."""
    bot = mk_bot()
    sess = _session(bot, Status.IDLE)
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=False))
    run_async(bot._send_command(mk_update("Opus"), "/model opus"))
    assert sess.model_switch_pending_until == 0.0
    assert not sess.model_switch_pending()


def test_telegram_model_switch_is_refused_not_held_behind_another_sender(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """Team mode: another member's note is outstanding. A /model must not
    be queued — the drain would type it later with none of the checks."""
    from aipager.bot.handlers import MODEL_SWITCH_OTHER_SENDER_REASON

    bot = mk_bot()
    sess = _session(bot, Status.IDLE)
    send = _stub_pty(monkeypatch)
    monkeypatch.setattr("aipager.bot.handlers.mixed_sender_note_outstanding",
                        lambda _s, _u: True)
    update = mk_update("Opus")
    run_async(bot._send_command(update, "/model opus"))
    send.assert_not_awaited()
    assert sess.pending_queue == []
    assert not sess.model_switch_pending()
    assert MODEL_SWITCH_OTHER_SENDER_REASON in update.message.reply_text.await_args.args[0]


def test_a_pending_switch_stops_blocking_once_the_model_really_changes():
    """The confirmation wait may never see the change (the operator
    answered Claude Code's dialog after it gave up, or the chat reply it
    would edit was muted). The session's own report still counts."""
    from aipager.bot.session_ops import mark_model_switch_sent, model_switch_refusal
    from aipager.miniapp.sessions import session_detail
    import time

    sess = TrackedSession(name="claude-x", label="x", status=Status.IDLE,
                          model_name="Sonnet 5")
    mark_model_switch_sent(sess, "opus")
    assert model_switch_refusal(sess)[0] == "switch_pending"
    assert session_detail(sess, time.monotonic())["model_switch"]["available"] is False

    sess.model_name = "Opus 5.5"          # a later statusline tick
    assert model_switch_refusal(sess) is None
    assert session_detail(sess, time.monotonic())["model_switch"] == {
        "available": True, "reason": None}
