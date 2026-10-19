"""A message sent right after a model switch, and the default Claude Code saves.

Claude Code (2.1.303, probed with a slow fake provider) first sends a typed
``/model X`` a check request, giving up after 15 s, and text typed during
that check is lost; typing right when the statusline reports the new model
is taken. So a message sent in that window is held (👀) and goes to Claude
once the statusline reports the switch, or once the 17 s window is over.

An interactive ``/model X`` also saves X as Claude Code's default for new
sessions (``model`` in its user settings). The chat confirmation says so
when that default changed.
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import claude_paths
from aipager.bot import session_ops
from aipager.state import Status, TrackedSession

NAME = "claude-jim"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """Claude Code's user settings: the config home's ``settings.json``
    (conftest points the home at a throwaway folder)."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    path = claude_paths.DEFAULT_CONFIG_HOME / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    def write(model=None, **extra):
        data = dict(extra)
        if model is not None:
            data["model"] = model
        path.write_text(json.dumps(data))
    return write


@pytest.fixture(autouse=True)
def _no_marker(monkeypatch):
    monkeypatch.setattr("aipager.dtach.model_switch_marker.write_marker",
                        lambda *a, **k: True)
    monkeypatch.setattr("aipager.dtach.model_switch_marker.clear_marker",
                        lambda *a, **k: None)


@pytest.fixture
def wired(mk_bot, monkeypatch):
    """A bot with one live idle session; what is typed is captured."""
    bot = mk_bot()
    bot._app.bot = AsyncMock()
    typed: list[str] = []
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "aipager.dtach.inject.send_text_and_enter",
        AsyncMock(side_effect=lambda n, body: typed.append(body) or True))
    bot._mark_driver = MagicMock()
    bot._react = AsyncMock()
    bot._send_busy_and_animate = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._build_reply_context = MagicMock(return_value="")
    bot._confirm_model_feedback = AsyncMock()
    sess = bot.registry.get_or_create(NAME)
    sess.label = "jim"
    sess.model_name = "Sonnet 5"
    bot.registry.transition(NAME, Status.IDLE)
    bot.registry.last_active_session = NAME
    return bot, sess, typed


def _send(bot, mk_update, run_async, text):
    update = mk_update(text, message_id=len(text) + 10, chat_id=-1001)
    run_async(bot._handle_message(update, MagicMock(bot=MagicMock(id=1))))
    return update


# ---- the check window ---------------------------------------------------------

def test_the_window_opens_when_a_switch_is_typed(settings):
    settings(model="sonnet")
    sess = TrackedSession(name=NAME, label="jim", model_name="Sonnet 5")
    before = time.monotonic()
    session_ops.mark_model_switch_sent(sess, "opus")
    assert sess.model_check_running()
    assert before + 16.9 <= sess.model_check_until <= time.monotonic() + 17.1
    assert sess.model_default_from == "sonnet"


def test_the_window_closes_when_the_statusline_reports_the_switch():
    sess = TrackedSession(name=NAME, label="jim", model_name="Sonnet 5")
    session_ops.mark_model_switch_sent(sess, "opus")
    sess.model_name = "Opus 5.5"
    assert not sess.model_check_running()


def test_the_window_closes_when_its_time_is_up():
    sess = TrackedSession(name=NAME, label="jim", model_name="Sonnet 5")
    session_ops.mark_model_switch_sent(sess, "opus")
    sess.model_check_until = time.monotonic() - 0.01
    assert not sess.model_check_running()
    # The second-switch refusal still has its own, longer window.
    assert sess.model_switch_pending()


def test_no_window_without_a_switch_or_after_one_that_was_never_typed():
    sess = TrackedSession(name=NAME, label="jim", model_name="Sonnet 5")
    assert not sess.model_check_running()
    session_ops.mark_model_switch_sent(sess, "opus")
    session_ops.clear_model_switch_pending(sess)
    assert not sess.model_check_running()


# ---- the hold -----------------------------------------------------------------

def test_a_message_right_after_a_switch_is_held_not_typed(
        wired, mk_update, run_async):
    bot, sess, typed = wired
    session_ops.mark_model_switch_sent(sess, "opus")
    _send(bot, mk_update, run_async, "now refactor the parser")
    assert typed == []
    assert [q[0] for q in sess.pending_queue] == ["now refactor the parser"]
    assert sess.model_hold is True
    bot._react.assert_awaited()


@pytest.mark.parametrize("path", ["direct", "command"])
def test_the_other_paths_hold_it_too(wired, mk_update, run_async, path):
    bot, sess, typed = wired
    session_ops.mark_model_switch_sent(sess, "opus")
    update = mk_update("hello", message_id=9, chat_id=-1001)
    if path == "direct":
        run_async(bot._direct_send(update, "jim", "hello"))
    else:
        run_async(bot._send_command(update, "/compact"))
    assert typed == []
    assert len(sess.pending_queue) == 1 and sess.model_hold is True


def test_a_message_after_the_switch_was_reported_goes_straight_in(
        wired, mk_update, run_async):
    bot, sess, typed = wired
    session_ops.mark_model_switch_sent(sess, "opus")
    sess.model_name = "Opus 5.5"
    _send(bot, mk_update, run_async, "now refactor the parser")
    assert typed == ["now refactor the parser"]
    assert sess.pending_queue == [] and sess.model_hold is False


def test_an_open_dialog_keeps_its_own_hold(wired, mk_update, run_async):
    """A dialog's hold wins: it has its own notice and its own release."""
    bot, sess, typed = wired
    session_ops.mark_model_switch_sent(sess, "opus")
    bot.registry.transition(NAME, Status.INTERACTIVE)
    sess.pending_permission = {"tool_summary": "Bash: ls"}
    _send(bot, mk_update, run_async, "hello")
    assert typed == []
    assert sess.model_hold is False and sess.dialog_hold is not None


# ---- the release --------------------------------------------------------------

def test_the_statusline_reporting_the_switch_sends_the_held_message(
        wired, mk_update, run_async):
    bot, sess, typed = wired
    session_ops.mark_model_switch_sent(sess, "opus")
    _send(bot, mk_update, run_async, "now refactor the parser")
    assert typed == []
    sess.model_name = "Opus 5.5"
    run_async(bot.notify(sess, "pinned_update", {}))
    assert typed == ["now refactor the parser"]
    assert sess.pending_queue == [] and sess.model_hold is False
    assert sess.status is Status.BUSY


def test_nothing_is_sent_while_the_check_still_runs(wired, mk_update, run_async):
    bot, sess, typed = wired
    session_ops.mark_model_switch_sent(sess, "opus")
    _send(bot, mk_update, run_async, "hello")
    run_async(bot.notify(sess, "pinned_update", {}))
    assert typed == [] and sess.model_hold is True


def test_the_window_running_out_sends_it_too(wired, mk_update, run_async):
    """A refused model, or one Claude Code calls by the same name: the
    statusline never changes, and the 17 s window still ends the hold."""
    bot, sess, typed = wired
    session_ops.mark_model_switch_sent(sess, "opus")
    _send(bot, mk_update, run_async, "hello")
    sess.model_check_until = time.monotonic() - 0.01
    run_async(bot.notify(sess, "model_hold_settle", {}))
    assert typed == ["hello"] and sess.model_hold is False


def test_one_message_goes_now_and_the_rest_at_that_turns_end(
        wired, mk_update, run_async):
    bot, sess, typed = wired
    session_ops.mark_model_switch_sent(sess, "opus")
    _send(bot, mk_update, run_async, "first")
    _send(bot, mk_update, run_async, "second one")
    sess.model_name = "Opus 5.5"
    run_async(bot.notify(sess, "pinned_update", {}))
    assert typed == ["first"]
    assert [q[0] for q in sess.pending_queue] == ["second one"]


def test_a_dialog_open_at_release_time_keeps_the_messages_held(
        wired, mk_update, run_async):
    bot, sess, typed = wired
    session_ops.mark_model_switch_sent(sess, "opus")
    _send(bot, mk_update, run_async, "hello")
    bot.registry.transition(NAME, Status.INTERACTIVE)
    sess.pending_permission = {"tool_summary": "Bash: ls"}
    sess.model_name = "Opus 5.5"
    run_async(bot._settle_model_hold(sess))
    assert typed == [] and len(sess.pending_queue) == 1
    assert sess.model_hold is False


def test_a_turn_running_at_release_time_leaves_them_to_its_end(
        wired, mk_update, run_async):
    bot, sess, typed = wired
    session_ops.mark_model_switch_sent(sess, "opus")
    _send(bot, mk_update, run_async, "hello")
    bot.registry.transition(NAME, Status.BUSY)
    sess.model_name = "Opus 5.5"
    run_async(bot._settle_model_hold(sess))
    assert typed == [] and len(sess.pending_queue) == 1
    assert sess.model_hold is False


def test_the_session_monitor_settles_a_hold_whose_window_ran_out(
        wired, mk_update, run_async, monkeypatch):
    """No statusline change ever comes (a refused model): the monitor's
    tick is what sends the held message on once the window is over."""
    from aipager.session_monitor import SessionMonitor

    bot, sess, typed = wired
    session_ops.mark_model_switch_sent(sess, "opus")
    _send(bot, mk_update, run_async, "hello")

    async def _live():
        return [NAME]
    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _live)
    events = []

    async def notify(s, event, ctx):
        events.append(event)
        await bot.notify(s, event, ctx)
    monitor = SessionMonitor(bot.registry, notify)

    run_async(monitor._scan())
    assert "model_hold_settle" not in events and typed == []

    sess.model_check_until = time.monotonic() - 0.01
    run_async(monitor._scan())
    assert "model_hold_settle" in events
    assert typed == ["hello"] and sess.model_hold is False


# ---- the default Claude Code saves ---------------------------------------------

def test_claude_default_model_reads_its_user_settings(settings):
    settings(model="  opus  ")
    assert session_ops.claude_default_model() == "opus"
    settings(model="")
    assert session_ops.claude_default_model() is None
    settings(model=5)
    assert session_ops.claude_default_model() is None
    settings()
    assert session_ops.claude_default_model() is None


def test_an_unreadable_settings_file_means_no_default(settings):
    (claude_paths.DEFAULT_CONFIG_HOME / "settings.json").write_text("{not json")
    assert session_ops.claude_default_model() is None


def test_await_default_saved_sees_the_new_default(settings, run_async):
    settings(model="sonnet")
    sess = TrackedSession(name=NAME, label="jim", model_name="Sonnet 5")
    session_ops.mark_model_switch_sent(sess, "opus")
    settings(model="opus")
    assert run_async(session_ops.await_default_saved(sess, timeout=0.5)) == "opus"


def test_await_default_saved_waits_for_the_save(settings, run_async):
    settings(model="sonnet")
    sess = TrackedSession(name=NAME, label="jim", model_name="Sonnet 5")
    session_ops.mark_model_switch_sent(sess, "opus")

    async def _run():
        asyncio.get_running_loop().call_later(0.1, settings, "opus")
        return await session_ops.await_default_saved(sess, timeout=2.0)
    assert run_async(_run()) == "opus"


@pytest.mark.parametrize("after", ["sonnet", None], ids=["unchanged", "removed"])
def test_await_default_saved_says_nothing_when_the_default_did_not_change(
        settings, run_async, after):
    settings(model="sonnet")
    sess = TrackedSession(name=NAME, label="jim", model_name="Sonnet 5")
    session_ops.mark_model_switch_sent(sess, "opus")
    settings(model=after)
    assert run_async(session_ops.await_default_saved(sess, timeout=0.2)) is None


def _feedback(bot, sess, run_async):
    msg = MagicMock()
    msg.chat_id = -1001
    msg.edit_text = AsyncMock()

    async def _run():
        asyncio.get_running_loop().call_later(0.05, setattr, sess, "model_name", "Opus 5.5")
        await bot._confirm_model_feedback(msg, sess, "Sonnet 5", "HEAD", timeout=2.0)
    run_async(_run())
    return msg.edit_text.await_args.args[0]


def test_the_chat_says_when_claude_code_made_it_the_default(
        mk_bot, settings, run_async):
    bot = mk_bot()
    settings(model="sonnet")
    sess = TrackedSession(name=NAME, label="jim", model_name="Sonnet 5")
    session_ops.mark_model_switch_sent(sess, "opus")
    settings(model="opus")
    text = _feedback(bot, sess, run_async)
    assert "Opus 5.5" in text
    assert "Claude Code made it the default model for new sessions too." in text


def test_the_chat_says_nothing_more_when_the_default_did_not_change(
        mk_bot, settings, run_async, monkeypatch):
    monkeypatch.setattr(session_ops, "MODEL_DEFAULT_SAVE_SECONDS", 0.1)
    bot = mk_bot()
    settings(model="opus")
    sess = TrackedSession(name=NAME, label="jim", model_name="Sonnet 5")
    session_ops.mark_model_switch_sent(sess, "opus")
    text = _feedback(bot, sess, run_async)
    assert "Opus 5.5" in text and "default" not in text


def test_an_agents_prompt_still_open_keeps_them_held(wired, mk_update, run_async):
    """Idle, but a background agent's prompt is still on screen (8.120):
    typed now, the message would land in that dialog."""
    bot, sess, typed = wired
    session_ops.mark_model_switch_sent(sess, "opus")
    _send(bot, mk_update, run_async, "hello")
    sess.prompt_wait_carry = True
    sess.model_name = "Opus 5.5"
    run_async(bot._settle_model_hold(sess))
    assert typed == [] and len(sess.pending_queue) == 1


# ---- review 1: order, the buttons, the Mini App's compact, the right default ----

def test_a_message_after_the_window_waits_behind_the_held_ones(
        wired, mk_update, run_async):
    """The window ended but the monitor has not sent the held messages on
    yet: a new message goes after them, not ahead."""
    bot, sess, typed = wired
    session_ops.mark_model_switch_sent(sess, "opus")
    _send(bot, mk_update, run_async, "first")
    sess.model_check_until = time.monotonic() - 0.01
    _send(bot, mk_update, run_async, "second one")
    assert typed == []
    assert [q[0] for q in sess.pending_queue] == ["first", "second one"]
    run_async(bot.notify(sess, "model_hold_settle", {}))
    assert typed == ["first"]


def _tap(bot, run_async, data):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.message = MagicMock(message_id=42, chat_id=-1001, text="")
    query.from_user = MagicMock(id=12345)
    update = MagicMock()
    update.callback_query = query
    update.effective_user = MagicMock(id=12345)
    update.effective_chat = MagicMock(id=-1001)
    update.message = None
    run_async(bot._handle_callback(update, MagicMock()))
    return [c.args[0] for c in query.answer.await_args_list if c.args]


@pytest.mark.parametrize("action", ["retry", "compact"])
def test_retry_and_compact_type_nothing_during_the_check(wired, run_async, action):
    bot, sess, typed = wired
    sess.last_prompt = "the prompt"
    session_ops.mark_model_switch_sent(sess, "opus")
    answers = _tap(bot, run_async, f"{NAME}:{action}")
    assert typed == []
    assert any("still checking the new model" in a for a in answers), answers


@pytest.mark.parametrize("action, body", [("retry", "the prompt"), ("compact", "/compact")])
def test_retry_and_compact_work_again_once_the_switch_is_reported(
        wired, run_async, action, body):
    bot, sess, typed = wired
    sess.last_prompt = "the prompt"
    bot._app.bot.delete_message = AsyncMock()
    session_ops.mark_model_switch_sent(sess, "opus")
    sess.model_name = "Opus 5.5"
    _tap(bot, run_async, f"{NAME}:{action}")
    assert typed == [body]


def test_the_mini_apps_compact_waits_with_the_held_messages(wired, run_async):
    bot, sess, typed = wired
    session_ops.mark_model_switch_sent(sess, "opus")
    outcome = run_async(bot._compact_session_core(sess))
    assert outcome.ok and outcome.reason == "queued"
    assert typed == [] and sess.model_hold is True
    sess.model_name = "Opus 5.5"
    run_async(bot.notify(sess, "pinned_update", {}))
    assert typed == ["/compact"]


def test_a_default_some_other_switch_saved_is_not_claimed(settings, run_async):
    settings(model="sonnet")
    sess = TrackedSession(name=NAME, label="jim", model_name="Sonnet 5")
    session_ops.mark_model_switch_sent(sess, "opus")
    settings(model="haiku")
    assert run_async(session_ops.await_default_saved(sess, timeout=0.2)) is None


def test_an_alias_saved_in_lower_case_is_this_switchs(settings, run_async):
    """Claude Code saves a typed alias in lower case ("Sonnet" -> "sonnet",
    probed)."""
    settings(model="opus")
    sess = TrackedSession(name=NAME, label="jim", model_name="Opus 5.5")
    session_ops.mark_model_switch_sent(sess, "Sonnet")
    settings(model="sonnet")
    assert run_async(session_ops.await_default_saved(sess, timeout=0.5)) == "sonnet"


# ---- the Ready card ------------------------------------------------------------

def _ready(mk_bot, settings):
    from aipager.bot import new_flow  # noqa: F401  (the card's module)

    bot = mk_bot()
    bot._app.bot.edit_message_text = AsyncMock()
    sess = bot.registry.get_or_create(NAME)
    sess.label = "jim"
    sess.scope_chat_id = -1001
    sess.model_name = "Sonnet 5"
    bot.registry.transition(NAME, Status.IDLE)
    settings(model="sonnet")
    return bot, sess


def _card_text(bot):
    return bot._app.bot.edit_message_text.await_args.kwargs["text"]


def test_the_ready_card_says_when_its_switch_became_the_default(
        mk_bot, settings, run_async):
    from aipager.bot import new_flow

    bot, sess = _ready(mk_bot, settings)
    session_ops.mark_model_switch_sent(sess, "opus")
    taps = new_flow._ready_taps(bot, -1001, 900)

    async def _run():
        def _switched():
            settings(model="opus")
            sess.model_name = "Opus 5.5"
        asyncio.get_running_loop().call_later(0.05, _switched)
        await new_flow._say_default_saved(bot, None, -1001, 900, sess, "Sonnet 5", "",
                                          taps)
    run_async(_run())
    text = _card_text(bot)
    assert "Claude Code made it the default model for new sessions too." in text
    assert "Opus 5.5" in text


def test_the_ready_card_is_left_alone_after_another_tap(mk_bot, settings, run_async):
    from aipager.bot import new_flow

    bot, sess = _ready(mk_bot, settings)
    session_ops.mark_model_switch_sent(sess, "opus")
    taps = new_flow._ready_taps(bot, -1001, 900)

    async def _run():
        def _switched():
            settings(model="opus")
            sess.model_name = "Opus 5.5"
            new_flow._count_ready_tap(bot, -1001, 900)   # e.g. the model list
        asyncio.get_running_loop().call_later(0.05, _switched)
        await new_flow._say_default_saved(bot, None, -1001, 900, sess, "Sonnet 5", "",
                                          taps)
    run_async(_run())
    bot._app.bot.edit_message_text.assert_not_awaited()


def test_the_ready_card_says_nothing_when_the_default_did_not_change(
        mk_bot, settings, run_async, monkeypatch):
    from aipager.bot import new_flow

    monkeypatch.setattr(session_ops, "MODEL_DEFAULT_SAVE_SECONDS", 0.1)
    bot, sess = _ready(mk_bot, settings)
    session_ops.mark_model_switch_sent(sess, "opus")

    async def _run():
        asyncio.get_running_loop().call_later(0.05, setattr, sess, "model_name", "Opus 5.5")
        await new_flow._say_default_saved(bot, None, -1001, 900, sess, "Sonnet 5", "", 0)
    run_async(_run())
    bot._app.bot.edit_message_text.assert_not_awaited()


def test_a_model_tap_on_the_ready_card_starts_the_follow_up(
        mk_bot, settings, run_async, monkeypatch):
    from aipager.bot import new_flow
    from aipager.bot.session_ops import ModelSwitchOutcome

    bot, sess = _ready(mk_bot, settings)
    bot._switch_model_core = AsyncMock(return_value=ModelSwitchOutcome(
        ok=True, reason="sent", label="jim", previous_model="Sonnet 5"))
    started = []

    async def _follow(*args):
        started.append(args)
    monkeypatch.setattr(new_flow, "_say_default_saved", _follow)
    query = MagicMock()
    query.answer = AsyncMock()
    query.message = MagicMock(message_id=900)
    query.from_user = MagicMock(id=12345)
    update = MagicMock()
    update.callback_query = query
    update.effective_user = MagicMock(id=12345)
    update.effective_chat = MagicMock(id=-1001, type="private")
    update.message = None

    async def _run():
        await new_flow.handle_callback(bot, update, query, sess.name, "rdy_m0")
        await asyncio.sleep(0)
    run_async(_run())
    assert started and started[0][5] == "Sonnet 5"


# ---- review 2 -----------------------------------------------------------------

def test_after_clearqueue_a_message_goes_straight_in(wired, mk_update, run_async):
    """The held messages were dropped (/clearqueue, /stop) and the window is
    over: nothing is left to wait behind, so nothing waits."""
    bot, sess, typed = wired
    session_ops.mark_model_switch_sent(sess, "opus")
    _send(bot, mk_update, run_async, "dropped")
    sess.pending_queue.clear()
    sess.model_check_until = time.monotonic() - 0.01
    assert sess.model_hold is True
    _send(bot, mk_update, run_async, "next")
    assert typed == ["next"]


@pytest.mark.parametrize("action", ["retry", "compact"])
def test_retry_and_compact_wait_behind_held_messages_too(wired, mk_update, run_async,
                                                         action):
    bot, sess, typed = wired
    sess.last_prompt = "the prompt"
    session_ops.mark_model_switch_sent(sess, "opus")
    _send(bot, mk_update, run_async, "held")
    sess.model_check_until = time.monotonic() - 0.01
    answers = _tap(bot, run_async, f"{NAME}:{action}")
    assert typed == []
    assert any("still checking the new model" in a for a in answers), answers


def test_the_ready_card_is_left_alone_when_the_session_ended(mk_bot, settings, run_async):
    from aipager.bot import new_flow

    bot, sess = _ready(mk_bot, settings)
    session_ops.mark_model_switch_sent(sess, "opus")

    async def _run():
        def _switched_then_ended():
            settings(model="opus")
            sess.model_name = "Opus 5.5"
            sess.status = Status.GONE
        asyncio.get_running_loop().call_later(0.05, _switched_then_ended)
        await new_flow._say_default_saved(bot, None, -1001, 900, sess, "Sonnet 5", "", 0)
    run_async(_run())
    bot._app.bot.edit_message_text.assert_not_awaited()


def test_a_refused_tap_does_not_count_as_a_change_to_the_card(mk_bot, settings, run_async):
    """A tap the card refuses (someone who may not change it) leaves the
    card as it was, so the default note may still be added."""
    from aipager.bot import new_flow

    bot, sess = _ready(mk_bot, settings)
    bot._can_prompt_user = MagicMock(return_value=False)
    query = MagicMock()
    query.answer = AsyncMock()
    query.message = MagicMock(message_id=900)
    query.from_user = MagicMock(id=999)
    update = MagicMock()
    update.callback_query = query
    update.effective_user = MagicMock(id=999)
    update.effective_chat = MagicMock(id=-1001, type="group")
    update.message = None
    run_async(new_flow.handle_callback(bot, update, query, sess.name, "rdy_model"))
    assert new_flow._ready_taps(bot, -1001, 900) == 0
    bot._can_prompt_user = MagicMock(return_value=True)
    run_async(new_flow.handle_callback(bot, update, query, sess.name, "rdy_model"))
    assert new_flow._ready_taps(bot, -1001, 900) == 1


def test_the_mini_apps_compact_waits_behind_held_messages(wired, mk_update, run_async):
    """The window is over but held messages still wait to go out: the
    compact goes after them, not ahead."""
    bot, sess, typed = wired
    session_ops.mark_model_switch_sent(sess, "opus")
    _send(bot, mk_update, run_async, "held")
    sess.model_check_until = time.monotonic() - 0.01
    outcome = run_async(bot._compact_session_core(sess))
    assert outcome.reason == "queued" and typed == []
    assert [q[0] for q in sess.pending_queue] == ["held", "/compact"]


def test_the_tap_counts_keep_only_the_newest_cards(mk_bot):
    from aipager.bot import new_flow

    bot = mk_bot()
    for msg_id in range(new_flow._READY_TAPS_KEPT + 10):
        new_flow._count_ready_tap(bot, -1001, msg_id)
    assert len(bot._ready_card_taps) == new_flow._READY_TAPS_KEPT
    assert new_flow._ready_taps(bot, -1001, 0) == 0
    assert new_flow._ready_taps(bot, -1001, new_flow._READY_TAPS_KEPT + 9) == 1


def test_the_default_is_read_from_the_config_home_the_sessions_use(
        settings, tmp_path, monkeypatch):
    """With CLAUDE_CONFIG_DIR set for the sessions, Claude Code saves the
    default in that folder's settings.json, so that is where it is read."""
    settings(model="sonnet")                      # the default home: not used
    home = tmp_path / "other-claude"
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"model": "haiku"}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    assert session_ops.claude_default_model() == "haiku"
