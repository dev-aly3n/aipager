"""Black-box: refusals for strangers in their Telegram language (en, fa
and ru only, a stored DM choice first), read-only notices and the
auto-deny notice in the chat's language, audit values untouched (phase 3b
design, success criteria 6, 9, 11, 12)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, preferences
from aipager import team as team_mod
from aipager.bot import auth, language_offer
from aipager.i18n import locales
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import Status, TrackedSession
from tests import i18n_bb3b as L
from tests import i18n_ml2
from tests.i18n_ml2 import BOB, DM, GROUP, OWNER, callback, message

STRANGER = 7770123
RO = 3


@pytest.fixture(autouse=True)
def _fresh_seen():
    team_mod.reset_unauthorized_seen()
    yield
    team_mod.reset_unauthorized_seen()


@pytest.fixture
def bot(mk_bot):
    bot = i18n_ml2.make_bot(mk_bot)
    bot.scopes = [
        Scope(chat_id=GROUP, kind="group", label="team", members=(
            Member(id=OWNER, label="aly", role="owner"),
            Member(id=BOB, label="bob", role="user"),
            Member(id=RO, label="ro", role="read_only"))),
        Scope(chat_id=DM, kind="dm", label="aly DM",
              members=(Member(id=OWNER, label="aly", role="owner"),)),
    ]
    bot.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    return bot


def _write(bot, run_async, *, chat, user, language_code, text="hello"):
    update = message(text, chat=chat, user=user, language_code=language_code)
    update.message.sender_chat = None
    update.message.from_user = update.effective_user
    update.effective_message = update.message
    run_async(bot._handle_message(update, MagicMock()))
    return [c.args[0] if c.args else c.kwargs.get("text")
            for c in update.message.reply_text.await_args_list]


def _refusal(bot, run_async, *, chat=STRANGER, user=STRANGER, language_code="en"):
    replies = _write(bot, run_async, chat=chat, user=user, language_code=language_code)
    assert len(replies) == 1, replies
    return replies[0]


# ---- a stranger's DM: their Telegram language ------------------------------------

@pytest.mark.parametrize("code,check", [("fa", L.fa_problems), ("ru", L.ru_problems)])
def test_a_stranger_is_refused_in_their_telegram_language(bot, run_async, code, check):
    assert check(_refusal(bot, run_async, language_code=code)) == []


@pytest.mark.parametrize("code", ["ja", "pl", "ja-JP", None, ""])
def test_a_stranger_with_another_language_gets_the_english_refusal(bot, run_async, code):
    english = _refusal(bot, run_async, language_code="en")
    team_mod.reset_unauthorized_seen()
    assert _refusal(bot, run_async, language_code=code) == english


def test_the_english_refusal_names_the_user_id_and_the_command(bot, run_async):
    text = _refusal(bot, run_async, language_code="en")
    assert (text.startswith("🚫 "), f"({STRANGER})" in text, "`aipager config`" in text) == \
        (True, True, True)


def test_the_persian_refusal_keeps_the_user_id_in_latin_digits(bot, run_async):
    text = _refusal(bot, run_async, language_code="fa")
    assert (str(STRANGER) in L.bare(text), L.fa_digits(STRANGER) in text) == (True, False)


def test_the_persian_refusal_keeps_the_user_id_whole(bot, run_async):
    """Copyable: no invisible mark between its digits."""
    assert str(STRANGER) in _refusal(bot, run_async, language_code="fa")


def test_the_persian_refusal_keeps_aipager_config_whole(bot, run_async):
    assert "`aipager config`" in _refusal(bot, run_async, language_code="fa")


def test_the_russian_refusal_keeps_the_user_id_and_command(bot, run_async):
    text = _refusal(bot, run_async, language_code="ru")
    assert (str(STRANGER) in text, "`aipager config`" in text) == (True, True)


def test_a_stored_dm_choice_wins_over_the_telegram_language(bot, run_async):
    preferences.set_ui_language(STRANGER, "ru")
    assert L.ru_problems(_refusal(bot, run_async, language_code="fa")) == []


def test_a_stored_english_choice_wins_over_a_persian_app(bot, run_async):
    english = _refusal(bot, run_async, language_code="en")
    team_mod.reset_unauthorized_seen()
    preferences.set_ui_language(STRANGER, "en")
    assert _refusal(bot, run_async, language_code="fa") == english


def test_the_switch_off_gives_english(bot, run_async, monkeypatch):
    english = _refusal(bot, run_async, language_code="en")
    team_mod.reset_unauthorized_seen()
    monkeypatch.setattr(auth, "STRANGER_LANGUAGE", False)
    assert _refusal(bot, run_async, language_code="fa") == english


def test_the_switch_is_on_by_default():
    assert auth.STRANGER_LANGUAGE is True


def test_a_stranger_is_answered_once_per_run_in_any_language(bot, run_async):
    _refusal(bot, run_async, language_code="fa")
    assert _write(bot, run_async, chat=STRANGER, user=STRANGER, language_code="fa") == []


def test_a_group_non_member_is_refused_in_their_telegram_language(bot, run_async):
    preferences.set_ui_language(GROUP, "fa")
    text = _refusal(bot, run_async, chat=GROUP, language_code="ru")
    assert L.ru_problems(text) == []


def test_a_group_non_member_with_a_german_app_is_refused_in_english(bot, run_async):
    preferences.set_ui_language(GROUP, "fa")
    text = _refusal(bot, run_async, chat=GROUP, language_code="de")
    assert (L.PERSIAN.search(text), L.CYRILLIC.search(text), "`aipager config`" in text) == \
        (None, None, True)


def test_general_detection_is_still_off():
    """for_stranger and AUTO_DETECT are untouched by the narrow path."""
    update = message("hi", chat=STRANGER, user=STRANGER, language_code="fa")
    assert (i18n.for_stranger(update).code, locales.AUTO_DETECT) == ("en", False)


def test_the_language_offer_stays_off():
    assert language_offer.OFFER_ENABLED is False


# ---- a non-member's tap -------------------------------------------------------------

@pytest.mark.parametrize("code,check", [("fa", L.fa_problems), ("ru", L.ru_problems)])
def test_a_non_members_tap_toast_is_in_their_telegram_language(bot, run_async, code, check):
    update, query = callback("_:up:chk", chat=GROUP, user=STRANGER)
    query.from_user.language_code = code
    run_async(bot._handle_callback(update, MagicMock()))
    shown = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert shown and [check(t) for t in shown] == [[] for _ in shown]


def test_a_non_members_tap_with_a_german_app_is_english(bot, run_async):
    update, query = callback("_:up:chk", chat=GROUP, user=STRANGER)
    query.from_user.language_code = "de"
    run_async(bot._handle_callback(update, MagicMock()))
    shown = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert shown and [t for t in shown if L.PERSIAN.search(t) or L.CYRILLIC.search(t)] == []


# ---- a read-only member: the chat's language ----------------------------------------

def test_a_read_only_members_notice_is_in_the_groups_language(bot, run_async):
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(RO, "ru")
    replies = _write(bot, run_async, chat=GROUP, user=RO, language_code="ru")
    assert replies and [L.fa_problems(r) for r in replies] == [[] for _ in replies]


def test_a_read_only_notice_in_persian_keeps_slash_status_whole(bot, run_async):
    preferences.set_ui_language(GROUP, "fa")
    replies = _write(bot, run_async, chat=GROUP, user=RO, language_code="en")
    assert any("/status" in r for r in replies)


def test_a_read_only_members_notice_is_in_russian_for_a_russian_group(bot, run_async):
    preferences.set_ui_language(GROUP, "ru")
    replies = _write(bot, run_async, chat=GROUP, user=RO, language_code="fa")
    assert replies and [L.ru_problems(r) for r in replies] == [[] for _ in replies]


# ---- the auto-deny notice -------------------------------------------------------------

@pytest.fixture
def denied(bot, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock(return_value=True))

    async def _no_sleep(_):
        pass
    monkeypatch.setattr("aipager.bot.auth._sleep", _no_sleep)
    audits = []
    monkeypatch.setattr("aipager.audit.append", lambda **kw: audits.append(kw))
    sess = TrackedSession(name="claude-x1__g1001", label="x1", status=Status.INTERACTIVE)
    sess.scope_chat_id, sess.scope_kind = GROUP, "group"
    bot.registry._sessions[sess.name] = sess
    return sess, audits


def _auto_deny(bot, run_async, sess, who=None):
    bot._app.bot.send_message.reset_mock()
    run_async(bot._auto_deny(sess, {"name": "Bash", "summary": "rm -rf /srv/x"}, who))
    call = bot._app.bot.send_message.await_args
    return call.args[1] if len(call.args) > 1 else call.kwargs["text"]


def test_the_auto_deny_notice_is_in_the_session_chats_language(bot, run_async, denied):
    sess, _audits = denied
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(BOB, "ru")
    who = Member(id=BOB, label="bob", role="user")
    assert L.fa_problems(_auto_deny(bot, run_async, sess, who)) == []


def test_the_persian_auto_deny_notice_keeps_tool_summary_and_mention(bot, run_async, denied):
    sess, _audits = denied
    preferences.set_ui_language(GROUP, "fa")
    text = _auto_deny(bot, run_async, sess, Member(id=BOB, label="bob", role="user"))
    assert ("Bash" in text, "rm -rf /srv/x" in text, "@bob" in text, "x1" in text) == \
        (True, True, True, True)


def test_the_russian_auto_deny_notice(bot, run_async, denied):
    sess, _audits = denied
    preferences.set_ui_language(GROUP, "ru")
    assert L.ru_problems(_auto_deny(bot, run_async, sess, None)) == []


def test_the_english_auto_deny_notice_is_unchanged(bot, run_async, denied):
    sess, _audits = denied
    text = _auto_deny(bot, run_async, sess, Member(id=BOB, label="bob", role="user"))
    assert "Bash blocked for @bob (role user)" in text


def test_the_auto_deny_audit_action_stays_english(bot, run_async, denied):
    sess, audits = denied
    preferences.set_ui_language(GROUP, "fa")
    _auto_deny(bot, run_async, sess, Member(id=BOB, label="bob", role="user"))
    assert [a.get("action") for a in audits] == ["Auto-denied"]


def test_the_auto_deny_audit_is_the_same_in_every_language(bot, run_async, denied):
    sess, audits = denied
    who = Member(id=BOB, label="bob", role="user")
    _auto_deny(bot, run_async, sess, who)
    preferences.set_ui_language(GROUP, "ru")
    _auto_deny(bot, run_async, sess, who)
    english, russian = audits
    assert {k: v for k, v in english.items() if k not in ("ts", "time")} == \
        {k: v for k, v in russian.items() if k not in ("ts", "time")}


# ---- iteration 2: language_code boundaries for strangers ---------------------------------

@pytest.mark.parametrize("variant,base", [("fa-IR", "fa"), ("FA", "fa"), ("ru-RU", "ru"),
                                          ("RU", "ru"), ("EN-us", "en")])
def test_a_regional_or_capitalised_code_refuses_like_its_language(bot, run_async, variant, base):
    plain = _refusal(bot, run_async, language_code=base)
    team_mod.reset_unauthorized_seen()
    assert _refusal(bot, run_async, language_code=variant) == plain


@pytest.mark.parametrize("code", ["be", "gl", "ca", "tg"])
def test_a_stranger_with_a_language_close_to_an_enabled_one_gets_english(bot, run_async, code):
    english = _refusal(bot, run_async, language_code="en")
    team_mod.reset_unauthorized_seen()
    assert _refusal(bot, run_async, language_code=code) == english


def test_a_cleared_stored_choice_falls_back_to_the_telegram_language(bot, run_async):
    preferences.set_ui_language(STRANGER, "ru")
    preferences.set_ui_language(STRANGER, "")
    assert L.fa_problems(_refusal(bot, run_async, language_code="fa")) == []


@pytest.mark.parametrize("tag,code", [("es", "es"), ("es-MX", "es"), ("pt-br", "pt-BR"),
                                      ("pt", "pt-BR"), ("fr", "fr"), ("fr-CA", "fr"),
                                      ("it", "it")])
def test_a_stranger_is_refused_in_a_phase_5_language(bot, run_async, tag, code):
    """Phase 5 run 1: Spanish, Portuguese (Brazil), French and Italian are
    enabled, so a stranger whose Telegram app speaks one of them (a
    regional tag included; Portugal's "pt" reads Brazilian) is refused in
    it, through the same normalize() as Persian and Russian."""
    from aipager.i18n import core
    english = _refusal(bot, run_async, language_code="en")
    team_mod.reset_unauthorized_seen()
    text = _refusal(bot, run_async, language_code=tag)
    assert text != english
    cat = core._catalog(code)
    heads = [cat[k].split("{", 1)[0] for k in
             ("auth.refuse.unknown_dm", "auth.refuse.team", "auth.refuse.scope")]
    assert any(head and head in text for head in heads), (tag, text)
