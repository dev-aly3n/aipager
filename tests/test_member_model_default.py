"""A group member's model switch changes only that member's session.

Claude Code saves an interactive ``/model X`` as the default model for new
sessions. In a group, a member whose role cannot manage the chat must not
change the machine owner's default that way, so aipager puts the old
default back once the switch is confirmed (measured with Claude Code
2.1.303: the running session keeps the model it switched to). The owner and
admins keep Claude Code's own behaviour and are told. The restore only
writes while the file still holds the model this switch saved.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import claude_paths
from aipager.bot import session_ops
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import TrackedSession

GROUP = -1001
OWNER, MEMBER, VIEWER, ADMIN, STRANGER = 1, 2, 3, 4, 99
NAME = "claude-jim"


@pytest.fixture
def settings(monkeypatch):
    """Claude Code's user settings for the sessions (conftest points the
    home at a throwaway folder)."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    path = claude_paths.DEFAULT_CONFIG_HOME / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    class S:
        def write(self, model=None, **extra):
            data = dict(extra)
            if model is not None:
                data["model"] = model
            path.write_text(json.dumps(data))

        def read(self):
            return json.loads(path.read_text())
    s = S()
    s.path = path
    return s


@pytest.fixture(autouse=True)
def _no_marker(monkeypatch):
    monkeypatch.setattr("aipager.dtach.model_switch_marker.write_marker",
                        lambda *a, **k: True)
    monkeypatch.setattr("aipager.dtach.model_switch_marker.clear_marker",
                        lambda *a, **k: None)


def _group_bot(mk_bot):
    bot = mk_bot(scopes=[Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=OWNER, label="alice", role="owner"),
        Member(id=MEMBER, label="bob", role="user"),
        Member(id=VIEWER, label="carol", role="read_only"),
        Member(id=ADMIN, label="dave", role="admin"),
    ))])
    bot.policy = load_policy()
    return bot


def _switched(settings, before="sonnet", to="opus"):
    """A session whose `/model to` was typed while the default was *before*,
    and Claude Code then saved *to*."""
    settings.write(model=before)
    sess = TrackedSession(name=NAME, label="jim", model_name="Sonnet 5")
    session_ops.mark_model_switch_sent(sess, to)
    settings.write(model=to)
    return sess


# ---- who is held to the default -----------------------------------------------

@pytest.mark.parametrize("user, held", [
    (OWNER, False), (ADMIN, False), (MEMBER, True), (VIEWER, True), (STRANGER, True),
    (None, True),
], ids=["owner", "admin", "user-role", "read-only", "not-a-member", "nobody"])
def test_who_is_held_to_the_default_in_a_group(mk_bot, user, held):
    assert session_ops.holds_member_to_default(_group_bot(mk_bot), user, GROUP) is held


def test_personal_mode_holds_nobody(mk_bot):
    assert session_ops.holds_member_to_default(mk_bot(), 12345, 12345) is False


# ---- the restore ----------------------------------------------------------------

def test_the_old_default_is_put_back(settings):
    sess = _switched(settings, before="sonnet", to="opus")
    assert session_ops.restore_default_model(sess, "opus") == session_ops.RESTORED
    assert settings.read()["model"] == "sonnet"


def test_no_default_before_means_the_key_goes(settings):
    settings.write(theme="dark")
    sess = TrackedSession(name=NAME, label="jim", model_name="Sonnet 5")
    session_ops.mark_model_switch_sent(sess, "opus")
    settings.write(model="opus", theme="dark")
    assert session_ops.restore_default_model(sess, "opus") == session_ops.RESTORED
    assert settings.read() == {"theme": "dark"}


def test_a_default_someone_changed_meanwhile_is_kept(settings):
    sess = _switched(settings, before="sonnet", to="opus")
    settings.write(model="haiku")              # another switch, the terminal
    assert session_ops.restore_default_model(sess, "opus") == session_ops.NOT_OURS
    assert settings.read()["model"] == "haiku"


def test_the_rest_of_the_file_is_kept(settings):
    settings.write(model="sonnet", hooks={"Stop": []}, env={"A": "1"})
    sess = TrackedSession(name=NAME, label="jim", model_name="Sonnet 5")
    session_ops.mark_model_switch_sent(sess, "opus")
    settings.write(model="opus", hooks={"Stop": []}, env={"A": "1"})
    assert session_ops.restore_default_model(sess, "opus") == session_ops.RESTORED
    assert settings.read() == {"model": "sonnet", "hooks": {"Stop": []}, "env": {"A": "1"}}


def test_an_unreadable_file_is_left_alone(settings):
    sess = _switched(settings)
    settings.path.write_text("{not json")
    assert session_ops.restore_default_model(sess, "opus") == session_ops.RESTORE_FAILED
    assert settings.path.read_text() == "{not json"


def test_a_relative_config_folder_is_never_written(settings, monkeypatch, tmp_path):
    sess = _switched(settings)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "rel-claude")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "rel-claude").mkdir()
    (tmp_path / "rel-claude" / "settings.json").write_text(json.dumps({"model": "opus"}))
    assert session_ops.restore_default_model(sess, "opus") == session_ops.RESTORE_FAILED
    assert json.loads((tmp_path / "rel-claude" / "settings.json").read_text()) == {
        "model": "opus"}


def test_the_config_home_the_sessions_use_is_the_one_restored(settings, monkeypatch,
                                                            tmp_path):
    home = tmp_path / "other-claude"
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"model": "sonnet"}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    sess = TrackedSession(name=NAME, label="jim", model_name="Sonnet 5")
    session_ops.mark_model_switch_sent(sess, "opus")
    (home / "settings.json").write_text(json.dumps({"model": "opus"}))
    assert session_ops.restore_default_model(sess, "opus") == session_ops.RESTORED
    assert json.loads((home / "settings.json").read_text()) == {"model": "sonnet"}


# ---- settle_default: who is told what -------------------------------------------

def test_a_member_is_told_nothing_and_the_default_is_back(mk_bot, settings, run_async):
    bot = _group_bot(mk_bot)
    sess = _switched(settings)
    assert run_async(session_ops.settle_default(bot, sess, MEMBER, GROUP)) is None
    assert settings.read()["model"] == "sonnet"


def test_the_owner_keeps_claude_codes_behaviour_and_is_told(mk_bot, settings, run_async):
    bot = _group_bot(mk_bot)
    sess = _switched(settings)
    assert run_async(session_ops.settle_default(bot, sess, OWNER, GROUP)) == "opus"
    assert settings.read()["model"] == "opus"


def test_a_member_is_told_when_the_default_cannot_be_put_back(
        mk_bot, settings, run_async, monkeypatch, caplog):
    bot = _group_bot(mk_bot)
    sess = _switched(settings)
    monkeypatch.setattr(session_ops, "restore_default_model",
                        lambda s, saved, **kw: session_ops.RESTORE_FAILED)
    import logging
    with caplog.at_level(logging.WARNING):
        assert run_async(session_ops.settle_default(bot, sess, MEMBER, GROUP)) == "opus"
    assert any("could not be put back" in r.getMessage() for r in caplog.records)


def test_nothing_saved_means_nothing_to_restore(mk_bot, settings, run_async, monkeypatch):
    monkeypatch.setattr(session_ops, "MODEL_DEFAULT_SAVE_SECONDS", 0.05)
    bot = _group_bot(mk_bot)
    settings.write(model="opus")
    sess = TrackedSession(name=NAME, label="jim", model_name="Sonnet 5")
    session_ops.mark_model_switch_sent(sess, "opus")
    called = []
    monkeypatch.setattr(session_ops, "restore_default_model",
                        lambda s, saved, **kw: called.append(saved) or session_ops.RESTORED)
    assert run_async(session_ops.settle_default(bot, sess, MEMBER, GROUP)) is None
    assert called == []


# ---- the three surfaces ----------------------------------------------------------

def _feedback(bot, sess, run_async, **who):
    msg = MagicMock()
    msg.chat_id = GROUP
    msg.edit_text = AsyncMock()

    async def _run():
        asyncio.get_running_loop().call_later(0.05, setattr, sess, "model_name", "Opus 5.5")
        await bot._confirm_model_feedback(msg, sess, "Sonnet 5", "HEAD", timeout=2.0, **who)
    run_async(_run())
    return msg.edit_text.await_args.args[0]


def test_the_chat_reply_to_a_member_says_nothing_about_the_default(
        mk_bot, settings, run_async):
    bot = _group_bot(mk_bot)
    sess = _switched(settings)
    text = _feedback(bot, sess, run_async, user_id=MEMBER, chat_id=GROUP)
    assert "Opus 5.5" in text and "default" not in text
    assert settings.read()["model"] == "sonnet"


def test_the_chat_reply_to_the_owner_says_it_became_the_default(
        mk_bot, settings, run_async):
    bot = _group_bot(mk_bot)
    sess = _switched(settings)
    text = _feedback(bot, sess, run_async, user_id=OWNER, chat_id=GROUP)
    assert "Claude Code made it the default model for new sessions too." in text
    assert settings.read()["model"] == "opus"


def test_a_chat_model_switch_passes_who_sent_it(mk_bot, mk_update, run_async, monkeypatch):
    from aipager.state import Status

    bot = mk_bot()
    sess = TrackedSession(name=NAME, label="jim", status=Status.IDLE, model_name="Sonnet 5")
    bot.registry._sessions[NAME] = sess
    bot.registry.last_active_session = NAME
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    bot._react = AsyncMock()
    bot._confirm_model_feedback = AsyncMock()
    update = mk_update("Opus", user_id=4321, chat_id=8765)
    run_async(bot._send_command(update, "/model opus"))
    kwargs = bot._confirm_model_feedback.call_args.kwargs
    assert kwargs == {"user_id": 4321, "chat_id": 8765}


def test_the_ready_card_says_nothing_for_a_members_switch(mk_bot, settings, run_async):
    from aipager.bot import new_flow

    bot = _group_bot(mk_bot)
    bot._app.bot.edit_message_text = AsyncMock()
    sess = _switched(settings)
    sess.scope_chat_id = GROUP

    async def _run():
        asyncio.get_running_loop().call_later(0.05, setattr, sess, "model_name", "Opus 5.5")
        await new_flow._say_default_saved(bot, None, GROUP, 900, sess, "Sonnet 5", "", 0,
                                          MEMBER)
    run_async(_run())
    bot._app.bot.edit_message_text.assert_not_awaited()
    assert settings.read()["model"] == "sonnet"


def test_a_ready_card_model_tap_passes_the_tapper(mk_bot, settings, run_async, monkeypatch):
    from aipager.bot import new_flow
    from aipager.bot.session_ops import ModelSwitchOutcome
    from aipager.state import Status

    bot = _group_bot(mk_bot)
    bot._app.bot.edit_message_text = AsyncMock()
    sess = bot.registry.get_or_create(NAME)
    sess.label = "jim"
    sess.scope_chat_id = GROUP
    bot.registry.transition(NAME, Status.IDLE)
    bot._switch_model_core = AsyncMock(return_value=ModelSwitchOutcome(
        ok=True, reason="sent", label="jim", previous_model="Sonnet 5"))
    started = []

    async def _follow(*args):
        started.append(args)
    monkeypatch.setattr(new_flow, "_say_default_saved", _follow)
    query = MagicMock()
    query.answer = AsyncMock()
    query.message = MagicMock(message_id=900)
    query.from_user = MagicMock(id=MEMBER)
    update = MagicMock()
    update.callback_query = query
    update.effective_user = MagicMock(id=MEMBER)
    update.effective_chat = MagicMock(id=GROUP, type="group")
    update.message = None

    async def _run():
        await new_flow.handle_callback(bot, update, query, sess.name, "rdy_m0")
        await asyncio.sleep(0)
    run_async(_run())
    assert started and started[0][-1] == MEMBER


def test_a_saved_value_with_spaces_is_still_this_switchs(settings):
    """The default is read stripped (claude_default_model), so the file's
    value is compared stripped too."""
    sess = _switched(settings, before="sonnet", to="opus")
    settings.write(model="  opus  ")
    assert session_ops.restore_default_model(sess, "opus") == session_ops.RESTORED
    assert settings.read()["model"] == "sonnet"


# ---- review 1: a reply that was never sent, an unconfirmed switch, ----------
# ---- an unreadable default, a default someone else changed ------------------

def test_a_member_switch_whose_reply_was_never_sent_is_still_undone(
        mk_bot, settings, run_async):
    """A muted chat (or a failed reply): the switch was typed, so the
    default must be put back all the same."""
    from aipager.bot.transport import MUTED

    bot = _group_bot(mk_bot)
    for message in (None, MUTED):
        sess = _switched(settings)

        async def _run():
            asyncio.get_running_loop().call_later(0.05, setattr, sess, "model_name",
                                                  "Opus 5.5")
            await bot._confirm_model_feedback(message, sess, "Sonnet 5", "HEAD",
                                              timeout=2.0, user_id=MEMBER, chat_id=GROUP)
        run_async(_run())
        assert settings.read()["model"] == "sonnet", message


def test_an_unconfirmed_member_switch_is_undone_too(mk_bot, settings, run_async, monkeypatch):
    """The statusline never reported it (the same display name, a slow
    statusline), but Claude Code saved the default: it is put back."""
    import time

    monkeypatch.setattr(session_ops, "MODEL_DEFAULT_SAVE_SECONDS", 0.1)
    bot = _group_bot(mk_bot)
    settings.write(model="sonnet")
    sess = TrackedSession(name=NAME, label="jim", model_name="Sonnet 5")
    session_ops.mark_model_switch_sent(sess, "opus")
    # Claude Code saves late in the check's window, well after the short
    # wait a confirmed switch gets: the wait runs to the window's end.
    sess.model_check_until = time.monotonic() + 0.6
    msg = MagicMock()
    msg.chat_id = GROUP
    msg.edit_text = AsyncMock()

    async def _run():
        start = time.monotonic()
        asyncio.get_running_loop().call_later(0.4, settings.write, "opus")
        await bot._confirm_model_feedback(msg, sess, "Sonnet 5", "HEAD", timeout=0.05,
                                          user_id=MEMBER, chat_id=GROUP)
        # Past Claude Code's late save in any case, so the check below sees
        # what became of it.
        await asyncio.sleep(max(0.0, 0.5 - (time.monotonic() - start)))
    run_async(_run())
    assert settings.read()["model"] == "sonnet"
    assert "not confirmed" in msg.edit_text.await_args.args[0]


def test_an_unconfirmed_owner_switch_waits_for_nothing(mk_bot, settings, run_async):
    bot = _group_bot(mk_bot)
    sess = _switched(settings)
    assert run_async(session_ops.settle_default(bot, sess, OWNER, GROUP,
                                                confirmed=False)) is None
    assert settings.read()["model"] == "opus"


def test_an_unreadable_default_is_never_guessed(mk_bot, settings, run_async):
    """settings.json could not be read when the switch was typed: the
    owner's default is unknown, so nothing is removed or rewritten, and
    the member is told the switch became the default."""
    bot = _group_bot(mk_bot)
    settings.path.write_text("{half written")
    sess = TrackedSession(name=NAME, label="jim", model_name="Sonnet 5")
    session_ops.mark_model_switch_sent(sess, "opus")
    assert sess.model_default_known is False
    settings.write(model="opus")
    assert session_ops.restore_default_model(sess, "opus") == session_ops.RESTORE_FAILED
    assert settings.read()["model"] == "opus"
    assert run_async(session_ops.settle_default(bot, sess, MEMBER, GROUP)) == "opus"


def test_a_missing_settings_file_reads_as_no_default(settings):
    settings.path.unlink(missing_ok=True)
    sess = TrackedSession(name=NAME, label="jim", model_name="Sonnet 5")
    session_ops.mark_model_switch_sent(sess, "opus")
    assert (sess.model_default_known, sess.model_default_from) == (True, None)


@pytest.mark.parametrize("content", ["[1, 2]", "\"text\""], ids=["list", "string"])
def test_a_settings_file_that_is_not_an_object_is_unknown(settings, content):
    settings.path.write_text(content)
    sess = TrackedSession(name=NAME, label="jim", model_name="Sonnet 5")
    session_ops.mark_model_switch_sent(sess, "opus")
    assert sess.model_default_known is False


def test_a_member_is_not_told_about_a_default_someone_else_changed(
        mk_bot, settings, run_async, monkeypatch):
    """Another switch saved its own model before this one was settled: the
    file is left as it is, and the member is not told the default is
    theirs (it is not)."""
    bot = _group_bot(mk_bot)
    sess = _switched(settings)
    original = session_ops.await_default_saved

    async def _saved_then_changed(s, **kw):
        result = await original(s, **kw)
        settings.write(model="haiku")
        return result
    monkeypatch.setattr(session_ops, "await_default_saved", _saved_then_changed)
    assert run_async(session_ops.settle_default(bot, sess, MEMBER, GROUP)) is None
    assert settings.read()["model"] == "haiku"


def test_a_ready_card_switch_with_no_statusline_yet_still_follows_up(
        mk_bot, settings, run_async, monkeypatch):
    from aipager.bot import new_flow
    from aipager.bot.session_ops import ModelSwitchOutcome
    from aipager.state import Status

    bot = _group_bot(mk_bot)
    bot._app.bot.edit_message_text = AsyncMock()
    sess = bot.registry.get_or_create(NAME)
    sess.label = "jim"
    sess.scope_chat_id = GROUP
    bot.registry.transition(NAME, Status.IDLE)
    bot._switch_model_core = AsyncMock(return_value=ModelSwitchOutcome(
        ok=True, reason="sent", label="jim", previous_model=""))
    started = []

    async def _follow(*args):
        started.append(args)
    monkeypatch.setattr(new_flow, "_say_default_saved", _follow)
    query = MagicMock()
    query.answer = AsyncMock()
    query.message = MagicMock(message_id=900)
    query.from_user = MagicMock(id=MEMBER)
    update = MagicMock()
    update.callback_query = query
    update.effective_user = MagicMock(id=MEMBER)
    update.effective_chat = MagicMock(id=GROUP, type="group")
    update.message = None

    async def _run():
        await new_flow.handle_callback(bot, update, query, sess.name, "rdy_m0")
        await asyncio.sleep(0)
    run_async(_run())
    assert started and started[0][5] == ""


# ---- review 2: unconfirmed switches on the Ready card and in the Mini App ---------

def test_an_unconfirmed_ready_card_switch_by_a_member_is_undone(
        mk_bot, settings, run_async, monkeypatch):
    """The statusline never reports the switch, Claude Code saves the
    default late in the check's window: the card's follow-up still puts it
    back, and edits nothing."""
    import time

    from aipager.bot import new_flow

    monkeypatch.setattr(session_ops, "MODEL_SWITCH_CONFIRM_SECONDS", 0.05)
    monkeypatch.setattr(session_ops, "MODEL_DEFAULT_SAVE_SECONDS", 0.1)
    bot = _group_bot(mk_bot)
    bot._app.bot.edit_message_text = AsyncMock()
    settings.write(model="sonnet")
    sess = TrackedSession(name=NAME, label="jim", model_name="Sonnet 5")
    sess.scope_chat_id = GROUP
    session_ops.mark_model_switch_sent(sess, "opus")
    sess.model_check_until = time.monotonic() + 0.6

    async def _run():
        start = time.monotonic()
        asyncio.get_running_loop().call_later(0.4, settings.write, "opus")
        await new_flow._say_default_saved(bot, None, GROUP, 900, sess, "Sonnet 5", "", 0,
                                          MEMBER)
        await asyncio.sleep(max(0.0, 0.5 - (time.monotonic() - start)))
    run_async(_run())
    assert settings.read()["model"] == "sonnet"
    bot._app.bot.edit_message_text.assert_not_awaited()


def test_the_snapshot_taken_when_the_wait_starts_is_the_one_restored(
        mk_bot, settings, run_async, monkeypatch):
    """A second switch of the same session during the wait records its own
    'before': the first restore still uses the first one's."""
    bot = _group_bot(mk_bot)
    sess = _switched(settings, before="sonnet", to="opus")
    original = session_ops.await_default_saved

    async def _second_switch_meanwhile(s, **kw):
        result = await original(s, **kw)
        s.model_default_from = "opus"          # a later switch typed from opus
        return result
    monkeypatch.setattr(session_ops, "await_default_saved", _second_switch_meanwhile)
    assert run_async(session_ops.settle_default(bot, sess, MEMBER, GROUP)) is None
    assert settings.read()["model"] == "sonnet"
