"""Black-box tests for design.md success criterion 15: a state file
written by pre-feature code (a positive `busy_msg_id` or `null`, never
`0`/`-1` -- those are documented as runtime-only sentinels, never
persisted) must load without error under the new stack-backed property,
and downstream recovery must reconcile it exactly as before.

Follows `tests/test_state.py`'s established `tmp_state_file` fixture and
`SessionRegistry().load()` round-trip pattern (existing file, read for
framework conventions only). The on-disk shape used here is written by
hand as a raw dict, matching exactly what `state.py`'s docstring
(quoted in intent.md) says the pre-feature format is -- not derived from
reading `state.py`'s save() implementation.

The `_recover_busy_message` half mirrors `tests/test_telegram_bot_
recovery.py`'s existing, pre-feature-established seam and payload shape
(also read for conventions only) -- it is not part of this feature's own
entrypoints.md, but the design's own claim ("no new recovery logic
needed") is precisely the claim that loading a persisted session through
the property and then handing it to that unchanged function still
produces the unchanged outcomes.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from aipager.bot import TelegramBot
from aipager.state import SessionRegistry


def _write_pre_feature_state(path, sessions: dict):
    data = {"sessions": sessions, "last_active_session": None}
    path.write_text(json.dumps(data))


def test_positive_busy_msg_id_loads_without_error(tmp_state_file):
    _write_pre_feature_state(tmp_state_file, {
        "claude-old": {"label": "old", "status": "busy", "busy_msg_id": 321},
    })
    registry = SessionRegistry()
    registry.load()  # MUST NOT raise
    sess = registry.get("claude-old")
    assert sess is not None
    assert sess.busy_msg_id == 321


def test_null_busy_msg_id_loads_without_error(tmp_state_file):
    _write_pre_feature_state(tmp_state_file, {
        "claude-old2": {"label": "old2", "status": "idle", "busy_msg_id": None},
    })
    registry = SessionRegistry()
    registry.load()  # MUST NOT raise
    sess = registry.get("claude-old2")
    assert sess is not None
    assert sess.busy_msg_id is None


def test_missing_busy_msg_id_key_entirely_loads_without_error(tmp_state_file):
    """An even older state file that predates the field's introduction
    (key absent, not merely null)."""
    _write_pre_feature_state(tmp_state_file, {
        "claude-ancient": {"label": "ancient", "status": "idle"},
    })
    registry = SessionRegistry()
    registry.load()  # MUST NOT raise
    sess = registry.get("claude-ancient")
    assert sess is not None
    assert sess.busy_msg_id is None


def test_loaded_positive_busy_msg_id_reports_busy_top_kind(tmp_state_file):
    """Decision 7: after load(), a positive persisted id becomes a
    single busy-kind stack entry -- never a reconstructed compacting
    entry (compact_started_at is deliberately never persisted)."""
    _write_pre_feature_state(tmp_state_file, {
        "claude-old": {"label": "old", "status": "busy", "busy_msg_id": 321},
    })
    registry = SessionRegistry()
    registry.load()
    sess = registry.get("claude-old")
    assert sess.stack_top_kind() == "busy"


def test_loaded_null_busy_msg_id_reports_no_top_kind(tmp_state_file):
    _write_pre_feature_state(tmp_state_file, {
        "claude-old2": {"label": "old2", "status": "idle", "busy_msg_id": None},
    })
    registry = SessionRegistry()
    registry.load()
    sess = registry.get("claude-old2")
    assert sess.stack_top_kind() is None


def test_loaded_session_recovers_via_recover_busy_message_when_alive(
    tmp_state_file, run_async,
):
    """A restart-orphaned card for a session whose dtach process is
    still alive gets the unchanged 'Daemon restarted' treatment."""
    _write_pre_feature_state(tmp_state_file, {
        "claude-old": {"label": "old", "status": "busy", "busy_msg_id": 321},
    })
    registry = SessionRegistry()
    registry.load()
    sess = registry.get("claude-old")

    bot = TelegramBot(registry)
    fake_bot = MagicMock()
    fake_bot.edit_message_text = AsyncMock()
    app = MagicMock()
    app.bot = fake_bot
    bot._app = app

    outcome = run_async(bot._recover_busy_message(
        bot._app.bot, "claude-old", sess, live_names={"claude-old"},
    ))
    assert outcome == "edited"
    call = fake_bot.edit_message_text.await_args
    text = call.kwargs["text"] if "text" in call.kwargs else call.args[0]
    assert "Daemon restarted" in text


def test_loaded_session_recovers_via_recover_busy_message_when_dead(
    tmp_state_file, run_async,
):
    """Same, but the process is gone -- unchanged 'Session ended'
    treatment, and busy_msg_id is cleared synchronously."""
    _write_pre_feature_state(tmp_state_file, {
        "claude-old": {"label": "old", "status": "busy", "busy_msg_id": 321},
    })
    registry = SessionRegistry()
    registry.load()
    sess = registry.get("claude-old")

    bot = TelegramBot(registry)
    fake_bot = MagicMock()
    fake_bot.edit_message_text = AsyncMock()
    app = MagicMock()
    app.bot = fake_bot
    bot._app = app

    outcome = run_async(bot._recover_busy_message(
        bot._app.bot, "claude-old", sess, live_names=set(),
    ))
    assert outcome == "edited"
    assert sess.busy_msg_id is None
    call = fake_bot.edit_message_text.await_args
    text = call.kwargs["text"] if "text" in call.kwargs else call.args[0]
    assert "Session ended" in text
