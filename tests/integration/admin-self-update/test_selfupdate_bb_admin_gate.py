"""SC-2: a non-admin, or a non-operator in personal mode, is refused by
`/update` and by every `_:up:` button, and nothing runs.
(design.md Success criteria #2; entrypoints.md "Telegram commands" and
"Telegram callback data")."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import update_flow
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

GROUP = -100777
ADMIN = 555
MEMBER = 777


class _Role:
    def __init__(self, bypass_safety):
        self.bypass_safety = bypass_safety
        self.can_prompt = True


class _Policy:
    def get_role(self, name):
        return _Role(name == "admin")


@pytest.fixture
def team_bot(mk_bot, h):
    scope = Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=ADMIN, label="ada", role="admin"),
        Member(id=MEMBER, label="bob", role="developer"),
    ))
    bot = mk_bot(SessionRegistry(), scopes=[scope])
    bot.policy = _Policy()
    bot._app.bot.send_message = AsyncMock(
        side_effect=lambda *a, **kw: h.status_message(kw.get("chat_id", GROUP), 801))
    bot._app.bot.edit_message_text = AsyncMock()
    return bot


def _cmd(mk_update, h, *, user_id, chat_id):
    upd = mk_update("/update", user_id=user_id, chat_id=chat_id)
    msg = h.status_message(chat_id, 900)
    upd.message.chat = MagicMock()
    upd.message.chat.id = chat_id
    upd.message.reply_text.return_value = msg
    upd.effective_message = upd.message
    upd.effective_chat.type = "private" if chat_id > 0 else "supergroup"
    return upd, msg


def _tap(data, *, user_id, chat_id, h):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = h.status_message(chat_id, 42)
    query.message.text = ""
    query.from_user = MagicMock()
    query.from_user.id = user_id
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_chat.type = "private" if chat_id > 0 else "supergroup"
    return update, query


def _run_cmd(bot, upd, run_async, h):
    async def go():
        await update_flow.handle_update_cmd(bot, upd, MagicMock())
        await h.wait_for(lambda: False, 0.1)   # let any background task run
    run_async(go())


# ---- /update ------------------------------------------------------------

def test_personal_mode_stranger_is_refused(world, personal_bot, mk_update, h, run_async):
    upd, msg = _cmd(mk_update, h, user_id=h.STRANGER, chat_id=h.STRANGER)
    _run_cmd(personal_bot, upd, run_async, h)
    assert "Only the admin can update aipager" in "\n".join(
        h.texts_of(upd.message, msg, personal_bot._app.bot))


def test_personal_mode_stranger_triggers_no_lookup(world, personal_bot, mk_update, h, run_async):
    upd, _ = _cmd(mk_update, h, user_id=h.STRANGER, chat_id=h.STRANGER)
    _run_cmd(personal_bot, upd, run_async, h)
    assert world.calls == [] and world.urls == []


def test_personal_mode_stranger_in_operator_dm_chat_is_refused(world, personal_bot, mk_update, h, run_async):
    """Boundary: right chat, wrong sender."""
    upd, msg = _cmd(mk_update, h, user_id=h.STRANGER, chat_id=h.DM)
    _run_cmd(personal_bot, upd, run_async, h)
    assert "Only the admin can update aipager" in "\n".join(
        h.texts_of(upd.message, msg, personal_bot._app.bot))


def test_group_non_admin_member_is_refused(world, team_bot, mk_update, h, run_async):
    upd, msg = _cmd(mk_update, h, user_id=MEMBER, chat_id=GROUP)
    _run_cmd(team_bot, upd, run_async, h)
    assert "Only the admin can update aipager" in "\n".join(
        h.texts_of(upd.message, msg, team_bot._app.bot))


def test_group_non_admin_member_triggers_no_lookup(world, team_bot, mk_update, h, run_async):
    upd, _ = _cmd(mk_update, h, user_id=MEMBER, chat_id=GROUP)
    _run_cmd(team_bot, upd, run_async, h)
    assert world.calls == [] and world.urls == []


def test_group_admin_gets_status(world, team_bot, mk_update, h, run_async):
    """Positive control for the two group refusals above."""
    upd, msg = _cmd(mk_update, h, user_id=ADMIN, chat_id=GROUP)

    async def go():
        await update_flow.handle_update_cmd(team_bot, upd, MagicMock())
        # 8.43: the versions appear once "Check for updates" is tapped.
        tap, query = _tap("_:up:chk", user_id=ADMIN, chat_id=GROUP, h=h)
        query.message = msg
        await team_bot._handle_callback(tap, MagicMock())
        await h.wait_for(lambda: msg.edit_text.await_count > 0, 5)
    run_async(go())
    assert msg.edit_text.await_count > 0
    assert h.LATEST in "\n".join(h.texts_of(msg, upd.message, team_bot._app.bot))


def test_group_status_omits_install_path(world, team_bot, mk_update, h, run_async):
    world.origin = "local"
    upd, msg = _cmd(mk_update, h, user_id=ADMIN, chat_id=GROUP)

    async def go():
        await update_flow.handle_update_cmd(team_bot, upd, MagicMock())
        # 8.43: the versions appear once "Check for updates" is tapped.
        tap, query = _tap("_:up:chk", user_id=ADMIN, chat_id=GROUP, h=h)
        query.message = msg
        await team_bot._handle_callback(tap, MagicMock())
        await h.wait_for(lambda: msg.edit_text.await_count > 0, 5)
    run_async(go())
    assert msg.edit_text.await_count > 0
    text = "\n".join(h.texts_of(msg, upd.message, team_bot._app.bot))
    assert world.local_path not in text and world.prefix not in text


def test_is_update_admin_rejects_missing_user(team_bot):
    assert team_bot._is_update_admin(None, GROUP) is False


def test_is_update_admin_accepts_operator_in_personal_mode(personal_bot, h):
    assert personal_bot._is_update_admin(h.OPERATOR, h.DM) is True


def test_is_update_admin_rejects_stranger_in_personal_mode(personal_bot, h):
    assert personal_bot._is_update_admin(h.STRANGER, h.STRANGER) is False


# ---- callbacks -----------------------------------------------------------

START_TAPS = ["_:up:cc", "_:up:ap", "_:up:both", "_:up:chk",
              "_:up:go:cc", "_:up:go:ap", "_:up:go:both"]


@pytest.mark.parametrize("data", START_TAPS)
def test_stranger_start_tap_runs_nothing(world, personal_bot, h, run_async, data):
    update, _ = _tap(data, user_id=h.STRANGER, chat_id=h.DM, h=h)

    async def go():
        await personal_bot._handle_callback(update, MagicMock())
        await h.wait_for(lambda: False, 0.15)
    run_async(go())
    assert personal_bot.updates.snapshot() is None and not world.upgrade_calls() \
        and not world.claude_update_calls()


@pytest.mark.parametrize("data", START_TAPS)
def test_group_member_start_tap_runs_nothing(world, team_bot, h, run_async, data):
    update, _ = _tap(data, user_id=MEMBER, chat_id=GROUP, h=h)

    async def go():
        await team_bot._handle_callback(update, MagicMock())
        await h.wait_for(lambda: False, 0.15)
    run_async(go())
    assert team_bot.updates.snapshot() is None and world.calls == []


def test_operator_check_then_update_starts_the_claude_update(world, personal_bot, h,
                                                            run_async):
    """Positive control for the refusals above. 8.43: was a single `_:up:cc`
    tap; the operator now checks first and taps the one Update button."""
    world.latest_pypi = world.running          # only Claude Code is newer
    check, _ = _tap("_:up:chk", user_id=h.OPERATOR, chat_id=h.DM, h=h)
    update, _ = _tap("_:up:go:cc", user_id=h.OPERATOR, chat_id=h.DM, h=h)

    async def go():
        await personal_bot._handle_callback(check, MagicMock())
        await h.wait_for(lambda: personal_bot.updates._checked is not None, 5)
        await personal_bot._handle_callback(update, MagicMock())
        await h.wait_phase(personal_bot, h.TERMINAL)
    run_async(go())
    assert len(world.claude_update_calls()) == 1


def _waiting_job(bot, world, h, registry):
    h.add_session(registry, "busy1", status=Status.BUSY)
    return bot.updates.start("aipager", chat_id=h.DM, user_id=h.OPERATOR,
                             origin="chat", status_message=h.status_message(h.DM))


@pytest.mark.parametrize("verb", ["now", "wait", "stop"])
def test_stranger_control_tap_changes_nothing(world, personal_bot, h, run_async, verb):
    async def go():
        res = await _waiting_job(personal_bot, world, h, personal_bot.registry)
        assert await h.wait_phase(personal_bot, "waiting_for_idle") == "waiting_for_idle"
        update, _ = _tap(f"_:up:{verb}:{res.job['id']}", user_id=h.STRANGER,
                         chat_id=h.DM, h=h)
        await personal_bot._handle_callback(update, MagicMock())
        await h.wait_for(lambda: False, 0.2)
        return personal_bot.updates.snapshot()["phase"]
    phase = run_async(go())
    assert phase == "waiting_for_idle" and not world.upgrade_calls() \
        and not world.schedule_calls()


def test_menu_cancel_tap_runs_nothing(world, personal_bot, h, run_async):
    update, query = _tap("_:up:x", user_id=h.OPERATOR, chat_id=h.DM, h=h)

    async def go():
        await personal_bot._handle_callback(update, MagicMock())
        await h.wait_for(lambda: False, 0.1)
    run_async(go())
    assert personal_bot.updates.snapshot() is None and world.calls == []


def test_menu_cancel_tap_marks_message_cancelled(world, personal_bot, h, run_async):
    update, query = _tap("_:up:x", user_id=h.OPERATOR, chat_id=h.DM, h=h)

    async def go():
        await personal_bot._handle_callback(update, MagicMock())
        await h.wait_for(lambda: False, 0.1)
    run_async(go())
    assert "ancel" in "\n".join(h.texts_of(query, personal_bot._app.bot))


@pytest.mark.parametrize("data", ["_:up:cc", "_:up:ap", "_:up:both", "_:up:x",
                                  "_:up:chk", "_:up:go:cc", "_:up:go:ap", "_:up:go:both",
                                  "_:up:now:9999999999", "_:up:wait:9999999999",
                                  "_:up:stop:9999999999"])
def test_callback_data_fits_telegram_limit(data):
    assert len(data.encode()) <= 64


def test_handle_callback_claims_only_up_actions(personal_bot, run_async, h):
    update, query = _tap("_:set", user_id=h.OPERATOR, chat_id=h.DM, h=h)
    claimed = run_async(update_flow.handle_callback(personal_bot, update, query, "_", "set"))
    assert claimed is False
