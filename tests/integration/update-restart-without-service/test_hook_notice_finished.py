"""The "update is only half done" notice does not go stale (2026-10-15).

A sent notice is remembered on disk; the daemon that runs its version
edits it into "the update is finished" with no button. The first notice
for a version waits a grace, since a restart mostly follows an install
within seconds. Every file goes through the conftest home redirects
(``self_update.HOOK_NOTICES_PATH``)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest

from aipager import _version, preferences, self_update
from aipager.bot import update_flow
from aipager.bot.flood import FloodMuted
from aipager.bot.flood_budget import PRIORITY_ORNAMENT, rate_limit_args
from aipager.bot.flood_budget import FloodSkipped
from aipager.state import Status

FINISHED_101 = "✅ The aipager update is finished: aipager runs 1.0.1 now."


@pytest.fixture(autouse=True)
def _daemon_runs_101(monkeypatch):
    monkeypatch.setattr(_version, "__version__", "1.0.1")


async def _drain(env):
    for _ in range(5):
        tasks = [t for t in env.manager._tasks if not t.done()]
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)


def _note(env, run, *versions, ticks=0):
    async def scenario():
        for v in versions:
            env.manager.note_hook_version(v)
            await _drain(env)
        for _ in range(ticks):
            env.manager.hook_notice_tick()
            await _drain(env)
    run(scenario)


def _notices(env):
    return [s for s in env.sent if s["text"] and "only half done" in s["text"]]


def _stored():
    path = self_update.HOOK_NOTICES_PATH
    return json.loads(path.read_text()) if path.exists() else None


def _entry(env, message_id=901, hook="1.0.1", running="1.0.0", sent_at=None):
    return {"chat_id": env.chat_id, "message_id": message_id, "hook": hook,
            "running": running, "sent_at": time.time() if sent_at is None else sent_at}


def _edit_mock(env, side_effect=None):
    mock = AsyncMock(side_effect=side_effect, return_value=MagicMock(message_id=1))
    env.bot._app.bot.edit_message_text = mock
    return mock


def _finish(env, run):
    async def scenario():
        await update_flow.finish_hook_notices(env.bot)
    run(scenario)


# ---- remembered on send -------------------------------------------------------------------------

def test_the_path_is_redirected_into_the_test_home(tmp_path):
    assert str(self_update.HOOK_NOTICES_PATH).startswith(str(tmp_path))
    assert self_update.HOOK_NOTICES_PATH.name == "update-hook-notices.json"


def test_a_sent_notice_is_remembered(env, run):
    before = time.time()
    _note(env, run, "1.1.0")
    (sent,) = _notices(env)
    (entry,) = _stored()
    assert entry["chat_id"] == env.chat_id and entry["message_id"] == 901
    assert (entry["hook"], entry["running"]) == ("1.1.0", "1.0.1")
    assert before <= entry["sent_at"] <= time.time()


def test_two_versions_in_one_run_are_both_remembered(env, run):
    _note(env, run, "1.1.0", "1.2.0")
    assert [e["hook"] for e in _stored()] == ["1.1.0", "1.2.0"]


def test_the_list_is_capped_to_the_newest(env, run, monkeypatch):
    monkeypatch.setattr(self_update, "HOOK_NOTICES_MAX", 2)
    _note(env, run, "1.1.0", "1.2.0", "1.3.0")
    assert [e["hook"] for e in _stored()] == ["1.2.0", "1.3.0"]


def test_a_sent_message_without_an_id_is_not_remembered(env, run):
    env.bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=None))
    _note(env, run, "1.1.0")
    assert env.manager._hook_seen["1.1.0"]["delivered"] is True
    assert _stored() is None


@pytest.mark.parametrize("how", ["skipped", "muted", "failed"])
def test_a_send_that_did_not_go_out_is_not_remembered(env, run, how):
    async def refused(*a, **k):
        if how == "skipped":
            raise FloodSkipped("budget")
        if how == "muted":
            raise FloodMuted(30)
        raise RuntimeError("network")
    env.bot._app.bot.send_message = AsyncMock(side_effect=refused)
    _note(env, run, "1.1.0")
    assert _stored() is None
    assert env.manager._hook_seen["1.1.0"]["delivered"] is False


def test_restart_now_tapped_on_the_notice_forgets_it(env, run):
    _note(env, run, "1.1.0")
    (entry,) = _stored()
    other = _entry(env, message_id=5, hook="1.1.0")
    self_update.write_hook_notices([entry, other])
    env.message.message_id = entry["message_id"]

    async def scenario():
        await env.start_restart()
        await env.finish()
    run(scenario)
    # The job's card owns that message now; the other notice is kept.
    assert _stored() == [other]


# ---- finished after the restart -----------------------------------------------------------------

def test_a_notice_for_the_running_version_is_edited_finished_without_a_button(env, run):
    self_update.write_hook_notices([_entry(env, hook="1.0.1"), _entry(env, 902, hook="1.0.0")])
    edit = _edit_mock(env)
    _finish(env, run)
    assert [c.kwargs["message_id"] for c in edit.await_args_list] == [901, 902]
    for call in edit.await_args_list:
        assert call.kwargs["chat_id"] == env.chat_id
        assert call.kwargs["text"] == FINISHED_101
        assert call.kwargs.get("reply_markup") is None
        assert call.kwargs["rate_limit_args"] == rate_limit_args(
            kind="skip", priority=PRIORITY_ORNAMENT)
    assert _stored() is None            # all finished: the file is gone
    assert env.sent == []


def test_a_notice_for_a_version_still_missing_is_kept_unedited(env, run):
    newer = _entry(env, hook="1.1.0")
    self_update.write_hook_notices([newer, _entry(env, 902, hook="1.0.1")])
    edit = _edit_mock(env)
    _finish(env, run)
    assert [c.kwargs["message_id"] for c in edit.await_args_list] == [902]
    assert _stored() == [newer]


@pytest.mark.parametrize("detail", ["Message to edit not found",
                                    "Message can't be edited"])
def test_a_failed_edit_is_dropped_and_nothing_is_sent(env, run, detail):
    self_update.write_hook_notices([_entry(env)])
    _edit_mock(env, side_effect=BadRequest(detail))
    _finish(env, run)
    assert _stored() is None
    assert env.sent == []


def test_any_other_edit_error_is_dropped_too(env, run):
    self_update.write_hook_notices([_entry(env)])
    _edit_mock(env, side_effect=RuntimeError("network"))
    _finish(env, run)
    assert _stored() is None and env.sent == []


def test_an_unchanged_message_counts_as_finished(env, run):
    self_update.write_hook_notices([_entry(env)])
    _edit_mock(env, side_effect=BadRequest("Message is not modified"))
    _finish(env, run)
    assert _stored() is None and env.sent == []


@pytest.mark.parametrize("how", ["skipped", "muted"])
def test_an_edit_the_gate_keeps_holding_back_is_kept_for_the_next_start(env, run, how,
                                                                        monkeypatch):
    monkeypatch.setattr(update_flow, "HOOK_NOTICE_SKIP_RETRY_SECONDS", 0.0)
    entry = _entry(env)
    self_update.write_hook_notices([entry])
    edit = _edit_mock(env, side_effect=FloodSkipped("budget") if how == "skipped"
                      else FloodMuted(30))
    _finish(env, run)
    assert edit.await_count == update_flow.HOOK_FINISH_TRIES == 3
    assert _stored() == [entry] and env.sent == []


def test_an_edit_the_gate_held_back_is_tried_again_in_this_run(env, run, monkeypatch):
    monkeypatch.setattr(update_flow, "HOOK_NOTICE_SKIP_RETRY_SECONDS", 0.0)
    held, done = _entry(env, 901), _entry(env, 902)
    self_update.write_hook_notices([held, done])
    tries = {901: 0}

    async def gate(**kw):
        if kw["message_id"] == 901:
            tries[901] += 1
            if tries[901] == 1:
                raise FloodSkipped("budget")
        return MagicMock(message_id=kw["message_id"])
    edit = _edit_mock(env, side_effect=gate)
    _finish(env, run)
    assert [c.kwargs["message_id"] for c in edit.await_args_list] == [901, 902, 901]
    assert _stored() is None and env.sent == []


def test_a_notice_forgotten_between_tries_is_not_edited_again(env, run, monkeypatch):
    monkeypatch.setattr(update_flow, "HOOK_NOTICE_SKIP_RETRY_SECONDS", 0.0)
    self_update.write_hook_notices([_entry(env, 901)])

    async def held_then_tapped(**kw):
        # Restart now is tapped on it while the gate holds the edit back.
        update_flow._forget_hook_notice(env.chat_id, kw["message_id"])
        raise FloodSkipped("budget")
    edit = _edit_mock(env, side_effect=held_then_tapped)
    _finish(env, run)
    assert edit.await_count == 1 and _stored() is None


def test_the_retry_waits_between_tries(env, run, monkeypatch):
    monkeypatch.setattr(update_flow, "HOOK_NOTICE_SKIP_RETRY_SECONDS", 30.0)
    self_update.write_hook_notices([_entry(env)])
    edit = _edit_mock(env, side_effect=FloodSkipped("budget"))

    async def scenario():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(update_flow.finish_hook_notices(env.bot), 0.2)
    run(scenario)
    assert edit.await_count == 1


def test_a_dev_build_calls_nothing_finished(env, run, monkeypatch):
    monkeypatch.setattr(_version, "__version__", "1.1.0.dev1")
    entries = [_entry(env, hook="1.0.1"), _entry(env, 902, hook="1.2.0")]
    self_update.write_hook_notices(entries)
    edit = _edit_mock(env)
    _finish(env, run)
    assert edit.await_count == 0 and _stored() == entries


def test_old_and_malformed_entries_are_dropped_unedited(env, run):
    old = _entry(env, 1, sent_at=time.time() - update_flow.HOOK_NOTICE_MAX_AGE_SECONDS - 60)
    future = _entry(env, 2, sent_at=time.time() + 3600)
    self_update.write_hook_notices([
        old, future, _entry(env, 3, hook="garbage"),
        {**_entry(env, 4), "chat_id": "x"}, {**_entry(env, 5), "message_id": True},
        {**_entry(env, 6), "sent_at": "now"}, "not a dict"])
    edit = _edit_mock(env)
    _finish(env, run)
    assert edit.await_count == 0
    assert _stored() is None


def test_a_recent_entry_is_still_finished(env, run):
    self_update.write_hook_notices([_entry(env, sent_at=time.time() - 6 * 86400)])
    edit = _edit_mock(env)
    _finish(env, run)
    assert edit.await_count == 1


def test_the_owner_chats_language_is_used(env, run):
    preferences.set_ui_language(env.chat_id, "de")
    self_update.write_hook_notices([_entry(env)])
    edit = _edit_mock(env)
    _finish(env, run)
    (call,) = edit.await_args_list
    assert call.kwargs["text"] == ("✅ Das aipager-Update ist fertig: aipager läuft jetzt "
                                   "mit 1.0.1.")


def test_without_a_telegram_app_the_entries_are_kept(env, run, caplog):
    entries = [_entry(env), _entry(env, 902, hook="1.1.0")]
    self_update.write_hook_notices(entries)
    env.bot._app = None
    with caplog.at_level(logging.INFO, logger="aipager"):
        _finish(env, run)
    assert _stored() == entries
    assert "hook notice finishing failed" not in caplog.text


def test_it_waits_the_markers_settle_first(env, run, monkeypatch):
    monkeypatch.setattr(self_update, "MARKER_SETTLE_SECONDS", 30)
    self_update.write_hook_notices([_entry(env)])
    edit = _edit_mock(env)

    async def scenario():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(update_flow.finish_hook_notices(env.bot), 0.05)
    run(scenario)
    assert edit.await_count == 0 and len(_stored()) == 1


def test_a_notice_sent_while_finishing_is_kept(env, run):
    self_update.write_hook_notices([_entry(env)])

    async def edit_and_send_another(**kw):
        update_flow._remember_hook_notice(env.chat_id, 950, "1.2.0", "1.0.1")
        return MagicMock(message_id=kw["message_id"])
    _edit_mock(env, side_effect=edit_and_send_another)
    _finish(env, run)
    assert [(e["message_id"], e["hook"]) for e in _stored()] == [(950, "1.2.0")]


def test_no_file_no_edit(env, run):
    edit = _edit_mock(env)
    _finish(env, run)
    assert edit.await_count == 0 and _stored() is None


def test_an_unreadable_file_never_raises(env, run):
    path = self_update.HOOK_NOTICES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    edit = _edit_mock(env)
    _finish(env, run)
    assert edit.await_count == 0


def test_a_raising_write_never_raises(env, run, monkeypatch):
    self_update.write_hook_notices([_entry(env)])
    _edit_mock(env)

    def boom(entries):
        raise OSError("read-only")
    monkeypatch.setattr(self_update, "write_hook_notices", boom)
    _finish(env, run)                   # logged, not raised


def test_the_daemon_starts_and_cancels_it_beside_the_marker():
    import inspect

    from aipager.cli import daemon
    src = inspect.getsource(daemon)
    assert src.index("deliver_update_marker") < src.index("finish_hook_notices(bot)")
    assert "hook_notices_task.cancel()" in src


# ---- the grace before the first notice ----------------------------------------------------------

def test_the_grace_holds_the_first_notice(env, run, monkeypatch):
    monkeypatch.setattr(update_flow, "HOOK_NOTICE_GRACE_SECONDS", 120.0)
    _note(env, run, "1.1.0", "1.1.0", ticks=3)
    rec = env.manager._hook_seen["1.1.0"]
    assert _notices(env) == []
    assert (rec["attempts"], rec["last_try_mono"], rec["deferred"]) == (0, None, "grace")
    assert _stored() is None


def test_the_tick_sends_it_once_the_grace_is_over(env, run, monkeypatch):
    monkeypatch.setattr(update_flow, "HOOK_NOTICE_GRACE_SECONDS", 120.0)
    _note(env, run, "1.1.0", ticks=1)
    rec = env.manager._hook_seen["1.1.0"]
    assert _notices(env) == []
    rec["first_seen_mono"] -= 121.0     # two minutes passed since it was first seen
    _note(env, run, ticks=1)
    assert len(_notices(env)) == 1 and rec["delivered"] is True and rec["attempts"] == 0


def test_the_grace_counts_from_the_first_sighting_not_the_last(env, run, monkeypatch):
    monkeypatch.setattr(update_flow, "HOOK_NOTICE_GRACE_SECONDS", 120.0)
    _note(env, run, "1.1.0")
    first = env.manager._hook_seen["1.1.0"]["first_seen_mono"]
    _note(env, run, "1.1.0")
    assert env.manager._hook_seen["1.1.0"]["first_seen_mono"] == first


def test_after_the_grace_the_other_hold_backs_still_apply(env, run, monkeypatch):
    monkeypatch.setattr(update_flow, "HOOK_NOTICE_GRACE_SECONDS", 120.0)
    sess = env.add_session(status=Status.BUSY)
    _note(env, run, "1.1.0")
    rec = env.manager._hook_seen["1.1.0"]
    rec["first_seen_mono"] -= 121.0
    _note(env, run, ticks=1)
    assert _notices(env) == [] and rec["deferred"] == "owner-chat-busy"
    sess.status = Status.IDLE
    _note(env, run, ticks=1)
    assert len(_notices(env)) == 1 and rec["attempts"] == 0


def test_the_grace_is_about_two_minutes():
    # Read from the source: this folder's conftest sets it to 0.
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(update_flow))
    (value,) = [n.value.value for n in tree.body if isinstance(n, ast.Assign)
                and [t.id for t in n.targets if isinstance(t, ast.Name)]
                == ["HOOK_NOTICE_GRACE_SECONDS"]]
    assert 60 <= value <= 300


# ---- a stale Restart now stays harmless ---------------------------------------------------------

def test_a_stale_restart_now_says_nothing_to_restart(env, run):
    env.restart_probe = ("1.0.1", True)     # the running version is the installed one

    async def scenario():
        await env.start_restart()
        await env.finish()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "done"
    assert "nothing to restart" in env.last_text()
    assert env.schedule_calls() == [] and env.execs == [] and env.stops == []
