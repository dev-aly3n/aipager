"""Black-box: group notices ("Post as yourself", the /help talk line, the
live-reload "Dropped" reply), the restart card edit, /resume, the diff
footer and transport's shared texts in English, Persian and Russian
(phase 3b design, success criteria 6, 7)."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Chat, Message, Update, User
from telegram.ext import ApplicationHandlerStop, filters

from aipager import i18n, preferences
from aipager.bot import TelegramBot, group_intake, reactions, transport
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status, TrackedSession
from tests import i18n_bb3b as L
from tests import i18n_ml2
from tests.i18n_ml2 import BOB, DM, GROUP, OWNER, callback

EN, FA, RU = i18n.get("en"), i18n.get("fa"), i18n.get("ru")
POLICY = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
ANON = 1087968824
DATE = datetime(2026, 10, 4, tzinfo=timezone.utc)


@pytest.fixture
def bot(mk_bot):
    return i18n_ml2.make_bot(mk_bot)


# ---- "Post as yourself" ----------------------------------------------------------------

@pytest.fixture
def replies(monkeypatch):
    out = []

    async def _reply(msg, text, **kw):
        out.append((msg.chat_id, text))
        return MagicMock(message_id=1)
    monkeypatch.setattr(transport, "reply_text", _reply)
    return out


def _anon(bot, run_async):
    msg = Message(1, DATE, Chat(GROUP, "supergroup"), from_user=User(ANON, "Group", True),
                  sender_chat=Chat(GROUP, "supergroup"), text="/status")
    try:
        run_async(group_intake.intake_gate(bot, Update(1, message=msg)))
    except ApplicationHandlerStop:
        pass


def test_post_as_yourself_english_is_unchanged(bot, run_async, replies):
    _anon(bot, run_async)
    assert replies == [(GROUP, "Post as yourself to use the bot.")]


@pytest.mark.parametrize("code,check", [("fa", L.fa_problems), ("ru", L.ru_problems)])
def test_post_as_yourself_is_in_the_groups_language(bot, run_async, replies, code, check):
    preferences.set_ui_language(GROUP, code)
    _anon(bot, run_async)
    assert [(c, check(t)) for c, t in replies] == [(GROUP, [])]


# ---- the /help talk line -----------------------------------------------------------------

def test_help_talk_line_without_tr_is_english(bot):
    assert group_intake.group_help_talk_line(bot) == \
        group_intake.group_help_talk_line(bot, tr=EN)


def test_help_talk_line_english_names_the_bot(bot):
    assert "@aipager_test_bot" in group_intake.group_help_talk_line(bot)


@pytest.mark.parametrize("tr,check", [(FA, L.fa_problems), (RU, L.ru_problems)],
                         ids=["fa", "ru"])
def test_help_talk_line_is_translated(bot, tr, check):
    assert check(group_intake.group_help_talk_line(bot, tr=tr)) == []


def test_persian_help_talk_line_keeps_the_mention_whole(bot):
    assert "@aipager_test_bot" in group_intake.group_help_talk_line(bot, tr=FA)


@pytest.mark.parametrize("tr,check", [(FA, L.fa_problems), (RU, L.ru_problems)],
                         ids=["fa", "ru"])
def test_help_talk_line_without_a_username_is_translated(bot, tr, check):
    bot._app.bot.username = None
    assert check(group_intake.group_help_talk_line(bot, tr=tr)) == []


# ---- live reload: "Dropped" ------------------------------------------------------------------

S = "claude-api__g1001"


def _members(bob=True):
    out = [Member(id=OWNER, label="aly", role="owner")]
    if bob:
        out.append(Member(id=BOB, label="bob", role="user"))
    return tuple(out)


def _scopes(bob=True):
    return [Scope(chat_id=GROUP, kind="group", label="team", members=_members(bob)),
            Scope(chat_id=DM, kind="dm", label="aly DM",
                  members=(Member(id=OWNER, label="aly", role="owner"),))]


@pytest.fixture
def gbot(mk_bot, monkeypatch):
    monkeypatch.setattr("aipager.policy.load_policy", lambda *a, **k: POLICY)

    async def _set(owner, chat_id, msg_id, emoji):
        return True
    monkeypatch.setattr(reactions, "set_reaction", _set)
    from aipager import policy_snapshot as ps
    ps.snapshot_path(S).parent.mkdir(parents=True, exist_ok=True)
    bot = mk_bot(scopes=_scopes())
    bot.policy = POLICY
    bot._app.bot.id = 999
    bot._app.bot.set_my_commands = AsyncMock()
    bot._app.bot.delete_my_commands = AsyncMock()
    bot._app.bot.set_chat_menu_button = AsyncMock()
    bot._message_chat_gate = filters.Chat({s.chat_id for s in _scopes()})
    for name in ("_card_for_injected", "_react", "_maybe_update_bot_name",
                 "_send_busy_and_animate"):
        setattr(bot, name, AsyncMock())
    return bot


def _dropped(gbot, run_async, n, *, sender=BOB):
    import aipager.scope as scope_mod
    scope_mod.dump_scopes(_scopes(), "123456:FAKE", path=scope_mod.CONFIG_PATH)
    run_async(gbot.reload_team())
    gbot._app.bot.send_message.reset_mock()
    sess = gbot.registry.get_or_create(S)
    sess.label, sess.scope_chat_id, sess.scope_kind = "api", GROUP, "group"
    sess.status = Status.IDLE
    sess.pending_queue.clear()
    for i in range(n):
        sess.queue_prompt("rm the deploy keys", 600 + i, "", sender)
    # The group's keyboard already lists the session (its start refreshed
    # it), so the reload below sends only the notice under test.
    gbot.registry.set_keyboard_labels(GROUP, gbot.registry.live_labels(GROUP))
    scope_mod.dump_scopes(_scopes(bob=False), "123456:FAKE", path=scope_mod.CONFIG_PATH)
    run_async(gbot.reload_team())
    return [c.kwargs.get("text") for c in gbot._app.bot.send_message.await_args_list]


def test_dropped_english_one_is_unchanged(gbot, run_async):
    assert _dropped(gbot, run_async, 1) == ["Dropped: @bob is no longer allowed to send here."]


def test_dropped_english_three_is_unchanged(gbot, run_async):
    assert _dropped(gbot, run_async, 3) == [
        "Dropped 3 messages: @bob is no longer allowed to send here."]


@pytest.mark.parametrize("n", [1, 3])
def test_dropped_in_persian(gbot, run_async, n):
    preferences.set_ui_language(GROUP, "fa")
    (text,) = _dropped(gbot, run_async, n)
    assert (L.fa_problems(text), "@bob" in text) == ([], True)


def test_dropped_in_persian_counts_with_persian_digits(gbot, run_async):
    preferences.set_ui_language(GROUP, "fa")
    (text,) = _dropped(gbot, run_async, 3)
    assert L.fa_digits(3) in text


def test_dropped_russian_plural_forms(gbot, run_async):
    preferences.set_ui_language(GROUP, "ru")

    def render(n):
        (text,) = _dropped(gbot, run_async, n)
        return text
    # 1 has its own whole sentence ("Dropped: ..."), so start at 2.
    assert L.ru_plural_problems(render, counts=L.RU_COUNTS[1:]) == []


def test_dropped_in_russian_follows_the_rules(gbot, run_async):
    preferences.set_ui_language(GROUP, "ru")
    (text,) = _dropped(gbot, run_async, 1)
    assert (L.ru_problems(text), "@bob" in text) == ([], True)


# ---- a restart's orphaned busy card ------------------------------------------------------

def _recover(run_async, chat, alive):
    registry = SessionRegistry()
    bot = TelegramBot(registry)
    fake = MagicMock()
    fake.edit_message_text = AsyncMock()
    bot._app = MagicMock(bot=fake)
    sess = TrackedSession(name="claude-jim", label="jim")
    sess.busy_msg_id, sess.status = 100, Status.BUSY
    sess.scope_chat_id, sess.scope_kind = chat, ("dm" if chat > 0 else "group")
    registry._sessions[sess.name] = sess
    run_async(bot._recover_busy_message(fake, "claude-jim", sess,
                                        live_names={"claude-jim"} if alive else set()))
    call = fake.edit_message_text.await_args
    return call.kwargs.get("text", call.args[0] if call.args else None)


@pytest.mark.parametrize("alive", [True, False], ids=["restarted", "ended"])
def test_recovered_card_english_is_unchanged(run_async, alive):
    text = _recover(run_async, DM, alive)
    assert ("Daemon restarted" if alive else "Session ended") in text


@pytest.mark.parametrize("alive", [True, False], ids=["restarted", "ended"])
@pytest.mark.parametrize("code,check", [("fa", L.fa_problems), ("ru", L.ru_problems)])
def test_recovered_card_is_in_the_session_chats_language(run_async, alive, code, check):
    preferences.set_ui_language(DM, code)
    text = _recover(run_async, DM, alive)
    assert (check(text), "jim" in text) == ([], True)


# ---- /resume ------------------------------------------------------------------------------

def _gone_bot(mk_bot, n, chat=DM):
    registry = SessionRegistry()
    now = time.time()
    for i in range(n):
        s = TrackedSession(name=f"claude-old{i:02d}", label=f"old{i:02d}", status=Status.GONE)
        s.scope_chat_id, s.scope_kind = chat, "dm"
        s.claude_session_id = f"uuid-{i}"
        s.cwd = "/srv/work"
        s.gone_at = now - 60 * (i + 1)
        s.last_assistant_preview = "" if i == 0 else "done"
        registry._sessions[s.name] = s
    return mk_bot(registry)


def _data(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row]


def test_picker_without_tr_is_english(mk_bot):
    bot = _gone_bot(mk_bot, 3)
    text, _kb = bot._render_resume_picker(0, DM)
    assert "3 total" in text


@pytest.mark.parametrize("code,check", [("fa", L.fa_problems), ("ru", L.ru_problems)])
def test_picker_is_in_the_chats_language(mk_bot, code, check):
    preferences.set_ui_language(DM, code)
    bot = _gone_bot(mk_bot, 3)
    text, _kb = bot._render_resume_picker(0, DM)
    assert check(text) == []


@pytest.mark.parametrize("code,check", [("fa", L.fa_problems), ("ru", L.ru_problems)])
def test_picker_buttons_are_translated_and_keep_callback_data(mk_bot, code, check):
    bot = _gone_bot(mk_bot, 15)
    _t, en_kb = bot._render_resume_picker(0, DM)
    preferences.set_ui_language(DM, code)
    _t, kb = bot._render_resume_picker(0, DM)
    nav = [b.text for b in kb.inline_keyboard[-1] if b.callback_data != "_:resume_noop"]
    assert (_data(kb), [n for n in nav if check(n)]) == (_data(en_kb), [])


def test_picker_session_names_stay_whole_in_persian(mk_bot):
    preferences.set_ui_language(DM, "fa")
    bot = _gone_bot(mk_bot, 3)
    text, kb = bot._render_resume_picker(0, DM)
    labels = " ".join(b.text for row in kb.inline_keyboard for b in row)
    assert all(f"old{i:02d}" in text and f"old{i:02d}" in labels for i in range(3))


def test_picker_page_numbers_in_persian_digits(mk_bot):
    preferences.set_ui_language(DM, "fa")
    bot = _gone_bot(mk_bot, 15)
    _t, kb = bot._render_resume_picker(0, DM)
    nav = " ".join(b.text for b in kb.inline_keyboard[-1])
    assert L.fa_digits(2) in nav


def test_picker_title_russian_plural_forms(mk_bot):
    preferences.set_ui_language(DM, "ru")

    def render(n):
        text, _kb = _gone_bot(mk_bot, n)._render_resume_picker(0, DM)
        return text.split("\n")[0]
    assert L.ru_plural_problems(render) == []


def test_empty_picker_in_persian(mk_bot):
    preferences.set_ui_language(DM, "fa")
    text, _kb = _gone_bot(mk_bot, 0)._render_resume_picker(0, DM)
    assert L.fa_problems(text) == []


@pytest.mark.parametrize("ago,expected", [
    (None, "earlier"), (1, "1s ago"), (59, "59s ago"), (61, "1m ago"),
    (3600, "1h ago"), (172800, "2d ago")])
def test_gone_ago_english_is_unchanged(ago, expected):
    gone_at = None if ago is None else time.time() - ago
    assert TelegramBot._fmt_gone_ago(gone_at) == expected


@pytest.mark.parametrize("ago,n", [(59, 59), (61, 1), (7200, 2), (172800, 2)])
def test_gone_ago_in_persian_digits(ago, n):
    text = TelegramBot._fmt_gone_ago(time.time() - ago, tr=FA)
    assert (L.fa_problems(text), L.fa_digits(n) in text) == ([], True)


@pytest.mark.parametrize("ago", [None, 59, 61, 3600, 172800])
def test_gone_ago_in_russian(ago):
    gone_at = None if ago is None else time.time() - ago
    assert L.ru_problems(TelegramBot._fmt_gone_ago(gone_at, tr=RU)) == []


def test_gone_ago_russian_minutes_plural_forms():
    def render(n):
        return TelegramBot._fmt_gone_ago(time.time() - 60 * n - 5, tr=RU)
    assert L.ru_plural_problems(render) == []


# ---- the resume dashboard ------------------------------------------------------------------

def _dash_bot(mk_bot, chat=DM):
    registry = SessionRegistry()
    s = TrackedSession(name="claude-x1", label="x1", status=Status.BUSY)
    s.scope_chat_id, s.scope_kind = chat, "dm"
    s.cwd = "/srv/work"
    registry._sessions[s.name] = s
    return mk_bot(registry), s


def _dash_text(out):
    return out[0] if isinstance(out, tuple) else out


def test_dashboard_one_argument_call_is_english_with_nothing_stored(mk_bot):
    bot, s = _dash_bot(mk_bot)
    assert _dash_text(bot._build_session_dashboard(s)) == \
        _dash_text(bot._build_session_dashboard(s, tr=EN))


@pytest.mark.parametrize("code,check", [("fa", L.fa_problems), ("ru", L.ru_problems)])
def test_dashboard_one_argument_call_follows_the_session_chat(mk_bot, code, check):
    preferences.set_ui_language(DM, code)
    bot, s = _dash_bot(mk_bot)
    assert check(_dash_text(bot._build_session_dashboard(s))) == []


def test_persian_dashboard_keeps_the_session_name(mk_bot):
    preferences.set_ui_language(DM, "fa")
    bot, s = _dash_bot(mk_bot)
    assert "x1" in _dash_text(bot._build_session_dashboard(s))


# ---- the diff footer ---------------------------------------------------------------------------

def _diff_footer(mk_bot, run_async, lines):
    bot = mk_bot()
    sess = TrackedSession(name="claude-x1", label="x1", status=Status.BUSY)
    sess.scope_chat_id, sess.scope_kind = DM, "dm"
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=3))
    content = "\n".join(f"line {i}" for i in range(lines))
    run_async(bot._send_diff_preview(sess, "Write", {"file_path": "/a.py", "content": content}))
    text = bot._app.bot.send_message.await_args.args[1]
    return text.rsplit("</pre>", 1)[-1]


def _shown_limit(mk_bot, run_async):
    """How many lines the preview shows: the footer's count for 100 lines."""
    import re
    footer = _diff_footer(mk_bot, run_async, 100)
    return 100 - int(re.search(r"\d+", footer).group())


def test_diff_footer_english_is_unchanged(mk_bot, run_async):
    limit = _shown_limit(mk_bot, run_async)
    assert (_diff_footer(mk_bot, run_async, limit + 1),
            _diff_footer(mk_bot, run_async, limit + 7)) == \
        ("\n<i>…and 1 more line</i>", "\n<i>…and 7 more lines</i>")


def test_diff_footer_absent_at_the_limit(mk_bot, run_async):
    limit = _shown_limit(mk_bot, run_async)
    assert _diff_footer(mk_bot, run_async, limit) == ""


def test_diff_footer_in_persian(mk_bot, run_async):
    limit = _shown_limit(mk_bot, run_async)
    preferences.set_ui_language(DM, "fa")
    footer = _diff_footer(mk_bot, run_async, limit + 7).strip("\n")
    assert (L.fa_problems(footer), L.fa_digits(7) in footer) == ([], True)


def test_diff_footer_russian_plural_forms(mk_bot, run_async):
    limit = _shown_limit(mk_bot, run_async)
    preferences.set_ui_language(DM, "ru")
    assert L.ru_plural_problems(lambda n: _diff_footer(mk_bot, run_async, limit + n)) == []


# ---- transport's shared texts -----------------------------------------------------------------

def test_tapper_tr_prefers_the_tappers_dm():
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(BOB, "ru")
    _u, query = callback("_:up:chk", chat=GROUP, user=BOB)
    assert transport.tapper_tr(query).code == "ru"


def test_tapper_tr_falls_back_to_the_tapped_chat():
    preferences.set_ui_language(GROUP, "fa")
    _u, query = callback("_:up:chk", chat=GROUP, user=BOB)
    query.from_user.language_code = None
    assert transport.tapper_tr(query).code == "fa"


def test_tapper_tr_ignores_a_persian_app_while_detection_is_off():
    _u, query = callback("_:up:chk", chat=GROUP, user=BOB)
    query.from_user.language_code = "fa"
    assert transport.tapper_tr(query).code == "en"


API_ERRORS = [
    "API Error: 529 overloaded_error",
    "API Error: 500 internal server error api_error",
    "rate_limit_error: retry after 30",
    "API Error: 402 Payment Required",
    "API Error: 429 rate_limit_error",
    "Connection error: ECONNRESET",
    "API Error: 400 something odd",
]


@pytest.mark.parametrize("text", API_ERRORS)
def test_api_error_without_tr_is_english(text):
    assert transport._detect_api_error(text) == transport._detect_api_error(text, tr=EN)


@pytest.mark.parametrize("text", API_ERRORS)
def test_api_error_keeps_retry_after_in_every_language(text):
    retries = {(transport._detect_api_error(text, tr=t) or (None, None))[1]
               for t in (EN, FA, RU)}
    assert len(retries) == 1


@pytest.mark.parametrize("text", API_ERRORS)
@pytest.mark.parametrize("tr,check", [(FA, L.fa_problems), (RU, L.ru_problems)],
                         ids=["fa", "ru"])
def test_api_error_is_translated(text, tr, check):
    found = transport._detect_api_error(text, tr=tr)
    english = transport._detect_api_error(text)
    assert (found is None) == (english is None) and (found is None or check(found[0]) == [])


def test_billing_error_keeps_its_url_whole_in_persian():
    found = transport._detect_api_error("API Error: 402 Payment Required", tr=FA)
    assert "https://console.anthropic.com" in found[0]


def test_rate_limit_wait_in_persian_digits():
    found = transport._detect_api_error("rate_limit_error: retry after 30", tr=FA)
    assert (L.fa_digits(30) in found[0], found[1]) == (True, 30)


def test_api_error_no_match_is_none_in_every_language():
    assert [transport._detect_api_error("all good", tr=t) for t in (EN, FA, RU)] == \
        [transport._detect_api_error("all good")] * 3


@pytest.mark.parametrize("name", ["NEEDS_ADMIN_REPLY", "RESUME_FULL_NEEDS_ADMIN",
                                  "RETRY_OTHERS_COMMAND_REPLY", "_TRUNC_SUFFIX"])
def test_transport_constants_are_still_english_strings(name):
    value = getattr(transport, name)
    assert isinstance(value, str) and not L.is_fa(value) and not L.is_ru(value)


@pytest.mark.parametrize("name", ["NEEDS_ADMIN_REPLY", "RESUME_FULL_NEEDS_ADMIN",
                                  "RETRY_OTHERS_COMMAND_REPLY"])
@pytest.mark.parametrize("tr,check", [(FA, L.fa_problems), (RU, L.ru_problems)],
                         ids=["fa", "ru"])
def test_transport_constants_translate(name, tr, check):
    assert check(tr.text(getattr(transport, name))) == []



# ---- /status of a session whose question is still loading ---------------------------------

def _status_with_loading(mk_bot):
    registry = SessionRegistry()
    s = TrackedSession(name="claude-x1", label="x1", status=Status.INTERACTIVE)
    s.scope_chat_id, s.scope_kind = DM, "dm"
    s.pending_permission = {"tool_summary": "AskUserQuestion (loading…)"}
    registry._sessions[s.name] = s
    bot = mk_bot(registry)
    text, _kb = bot._render_status_list(DM)
    return text


def test_status_loading_english_is_unchanged(mk_bot):
    assert "AskUserQuestion (loading…)" in _status_with_loading(mk_bot)


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_status_loading_is_translated_and_keeps_the_tool_name(mk_bot, code):
    preferences.set_ui_language(DM, code)
    text = _status_with_loading(mk_bot)
    assert ("AskUserQuestion" in text, "loading" in text) == (True, False)


# ---- iteration 2: the corrected _detect_api_error contract -------------------------------

@pytest.mark.parametrize("text", API_ERRORS)
@pytest.mark.parametrize("tr", [None, EN, FA, RU], ids=["none", "en", "fa", "ru"])
def test_api_error_match_is_a_text_and_an_optional_int(text, tr):
    found = transport._detect_api_error(text, tr=tr)
    assert found is None or (len(found) == 2 and isinstance(found[0], str)
                             and (found[1] is None or isinstance(found[1], int)))


@pytest.mark.parametrize("text", API_ERRORS)
def test_api_error_matches_the_same_texts_in_every_language(text):
    assert len({transport._detect_api_error(text, tr=t) is None for t in (None, EN, FA, RU)}) == 1


@pytest.mark.parametrize("text", ["", " ", "API", "error"])
def test_api_error_near_empty_text_is_none(text):
    assert transport._detect_api_error(text, tr=FA) is None


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_dashboard_labels_stay_outside_the_code_block(mk_bot, code):
    """Decision 5: in fa and ru no translated label sits inside <code>."""
    preferences.set_ui_language(DM, code)
    bot, s = _dash_bot(mk_bot)
    spans = L.code_spans(_dash_text(bot._build_session_dashboard(s)))
    assert [sp for sp in spans if L.PERSIAN.search(sp) or L.CYRILLIC.search(sp)] == []
