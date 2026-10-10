"""Multilingual phase 3b: refusals in the stranger's own language.

Someone not on the allow-list, or writing privately to a bot not set up
for them, is refused in their stored DM choice, else their Telegram app's
language when aipager has it (en, fa, ru), else English: a narrow path
(``auth.STRANGER_LANGUAGE``) that leaves ``i18n.for_stranger`` and the one
detection switch alone. Read-only notices are the chat's; the auto-deny
notice is the session chat's while its audit stays English."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, preferences
from aipager import team as team_mod
from aipager.bot import auth
from aipager.policy import load_policy
from aipager.scope import Member, Scope

GROUP, OWNER, VIEWER, STRANGER = -1001, 555, 3, 123456789
RLM, FSI, PDI = "\u200f", "\u2068", "\u2069"
PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"


@pytest.fixture(autouse=True)
def _fresh():
    team_mod.reset_unauthorized_seen()
    yield
    team_mod.reset_unauthorized_seen()


@pytest.fixture
def bot(mk_bot):
    b = mk_bot(scopes=[Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=OWNER, label="aly", role="owner"),
        Member(id=VIEWER, label="rob", role="read_only")))])
    b.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    return b


def _update(user, chat, language_code=None):
    update = MagicMock()
    update.effective_user = MagicMock(id=user, language_code=language_code, username="who",
                                      first_name="Who", last_name=None, is_bot=False)
    update.effective_chat = MagicMock(id=chat, type="private" if chat > 0 else "supergroup")
    msg = MagicMock(chat_id=chat, sender_chat=None, from_user=update.effective_user)
    msg.chat = update.effective_chat
    msg.reply_text = AsyncMock(return_value=MagicMock(message_id=1))
    update.message = update.effective_message = msg
    return update


def _reply(update):
    call = update.message.reply_text.await_args
    return call.args[0] if call is not None else None


def _refuse(bot, run_async, user=STRANGER, chat=None, language_code=None):
    update = _update(user, STRANGER if chat is None else chat, language_code)
    assert run_async(bot._authorize(update)) is False
    return _reply(update)


def test_a_persian_stranger_is_refused_in_persian(bot, run_async):
    text = _refuse(bot, run_async, language_code="fa")
    assert text.startswith(RLM + "🚫 این ربات")
    assert f"({FSI}{STRANGER}{PDI})" in text
    assert f"{FSI}`aipager config`{PDI}" in text


def test_the_user_id_keeps_latin_digits(bot, run_async):
    text = _refuse(bot, run_async, language_code="fa")
    assert str(STRANGER) in text
    assert not any(d in text for d in PERSIAN_DIGITS)


def test_a_disabled_language_gets_english(bot, run_async):
    for code in ("ja", "pl"):
        team_mod.reset_unauthorized_seen()
        text = _refuse(bot, run_async, language_code=code)
        assert text == ("🚫 This bot isn't configured to talk to you. Ask the operator to add "
                        f"your Telegram user ID ({STRANGER}) via `aipager config`.")
        user = SimpleNamespace(id=STRANGER, language_code=code)
        assert auth._stranger_tr(user).code == "en"


def test_a_russian_non_member_in_a_group_is_refused_in_russian(bot, run_async):
    text = _refuse(bot, run_async, chat=GROUP, language_code="ru")
    assert text == ("🚫 Тебя нет в списке разрешённых пользователей этой области. Попроси "
                    f"оператора добавить твой Telegram ID ({STRANGER}) через `aipager config`.")


def test_a_stored_choice_wins(bot, run_async):
    preferences.set_ui_language(STRANGER, "ru")
    text = _refuse(bot, run_async, language_code="fa")
    assert text.startswith("🚫 Этот бот не настроен")


def test_the_switch_turns_it_off(bot, run_async, monkeypatch):
    monkeypatch.setattr(auth, "STRANGER_LANGUAGE", False)
    text = _refuse(bot, run_async, language_code="fa")
    assert text.startswith("🚫 This bot isn't configured to talk to you.")


def test_general_detection_stays_off():
    """G45 is untouched: ``for_stranger`` still reads English for a Persian
    app."""
    update = SimpleNamespace(effective_user=SimpleNamespace(id=9, language_code="fa"),
                             effective_chat=SimpleNamespace(id=9))
    assert i18n.for_stranger(update).code == "en"


def test_the_legacy_team_refusal(mk_bot, run_async):
    team = team_mod.Team(group_id=GROUP, users={
        OWNER: team_mod.User(OWNER, "aly", team_mod.Role.ADMIN)})
    bot = mk_bot(team=team)
    text = _refuse(bot, run_async, chat=GROUP, language_code="fa")
    assert text.startswith(RLM + "🚫 شما در فهرست")


def test_the_non_member_tap_toast_is_theirs(bot, run_async):
    query = MagicMock()
    query.answer = AsyncMock()
    query.from_user = MagicMock(id=STRANGER, language_code="ru")
    query.message = MagicMock(chat_id=GROUP)
    query.message.chat = MagicMock(id=GROUP)
    assert run_async(bot._authorize_callback(query)) is None
    assert query.answer.await_args.args[0] == "Тебя нет в списке разрешённых"


def test_the_read_only_scope_notice_keeps_the_label_bytes(mk_bot, run_async):
    """English bytes as before keys: the label goes in as it is, never
    escaped (``'`` stays ``'``); in Persian it is isolated as one unit."""
    b = mk_bot(scopes=[Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=OWNER, label="aly", role="owner"),
        Member(id=VIEWER, label="o'neil", role="read_only")))])
    b.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    update = _update(VIEWER, GROUP)
    assert run_async(b._authorize(update)) is False
    assert _reply(update) == ("👀 @o'neil - your role is <i>read_only</i>; you can use "
                              "<code>/status</code> but can't drive sessions.")
    b._read_only_told.clear()   # told once per chat: ask again
    preferences.set_ui_language(GROUP, "fa")
    update = _update(VIEWER, GROUP)
    assert run_async(b._authorize(update)) is False
    assert f"{FSI}@o'neil{PDI}" in _reply(update)


def test_the_read_only_team_notice_keeps_the_label_bytes(mk_bot, run_async):
    """The legacy team path, like the scope one: raw label bytes in
    English, isolated as one unit in Persian."""
    team = team_mod.Team(group_id=GROUP, users={
        OWNER: team_mod.User(OWNER, "aly", team_mod.Role.ADMIN),
        VIEWER: team_mod.User(VIEWER, "o'neil", team_mod.Role.READ_ONLY)})
    b = mk_bot(team=team)
    update = _update(VIEWER, GROUP)
    assert run_async(b._authorize(update)) is False
    assert _reply(update) == ("👀 @o'neil - your role is <i>read_only</i>; you can use "
                              "<code>/status</code> but can't drive sessions.")
    b._read_only_told.clear()   # told once per chat: ask again
    preferences.set_ui_language(GROUP, "fa")
    update = _update(VIEWER, GROUP)
    assert run_async(b._authorize(update)) is False
    assert f"{FSI}@o'neil{PDI}" in _reply(update)


def test_the_read_only_notice_uses_the_chats_language(bot, run_async):
    preferences.set_ui_language(GROUP, "ru")
    preferences.set_ui_language(VIEWER, "fa")
    update = _update(VIEWER, GROUP, "fa")
    assert run_async(bot._authorize(update)) is False
    assert _reply(update) == ("👀 @rob - твоя роль <i>read_only</i>; ты можешь использовать "
                              "<code>/status</code>, но не можешь управлять сессиями.")


def test_auto_deny_audit_stays_english(mk_bot, run_async, monkeypatch):
    from aipager import audit
    from aipager.dtach import inject
    from aipager.state import Status, TrackedSession

    async def _nap(_s):
        return None
    preferences.set_ui_language(GROUP, "fa")
    monkeypatch.setattr(inject, "send_keys", AsyncMock(return_value=True))
    monkeypatch.setattr(auth, "_sleep", _nap)
    records = []
    monkeypatch.setattr(audit, "append", lambda **kw: records.append(kw))
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=8))
    sess = TrackedSession(name="claude-x1", label="x1", status=Status.INTERACTIVE)
    sess.scope_chat_id, sess.scope_kind = GROUP, "group"
    bot.registry._sessions[sess.name] = sess
    triggerer = team_mod.User(2, "bob", team_mod.Role.DEVELOPER)
    run_async(bot._auto_deny(sess, {"name": "Bash", "summary": "Bash: rm -rf build/"},
                             triggerer))
    text = bot._app.bot.send_message.await_args.args[1]
    assert text == (f"{RLM}⛔ <b>{FSI}x1{PDI}</b> · {FSI}Bash{PDI} برای {FSI}@bob{PDI} "
                    f"(نقش {FSI}developer{PDI}) مسدود شد\n<i>Bash: rm -rf build/</i>")
    assert records and records[0]["action"] == "Auto-denied"
    assert records[0]["reason"] == "deny_tools"
