"""Roadmap 8.87: a group Telegram upgraded to a supergroup is followed.

Making a basic group public, enabling topics or passing 200 members gives
it a new chat id. Telegram says so with a service message in the old chat
(``migrate_to_chat_id``) and one in the new chat (``migrate_from_chat_id``),
and every call into the old id raises PTB's ``ChatMigrated``. Before this
change nothing handled either: the group went silent both ways, the pinned
bar retried every 60 s for good, and the group's sessions were stranded.

Groups cannot be tested live; these tests are the proof. The operator's own
install is one DM: the DM-parity tests pin that nothing changes there.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Chat, Message, Update
from telegram.error import ChatMigrated, NetworkError
from telegram.ext import ApplicationHandlerStop, MessageHandler, filters

from aipager import preferences
from aipager import scope as scope_mod
from aipager.bot import chat_migration, group_intake
from aipager.bot import rich_message as rm
from aipager.bot.dashboard import _TRANSIENT_RETRY, PinnedChat
from aipager.bot.flood import MUTE
from aipager.bot.flood_budget import BudgetRateLimiter
from aipager.bot.held import HELD
from aipager.bot.transport import send_text
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

GROUP = -1001            # the basic group (suffix g1001)
NEW = -1009999           # the supergroup Telegram made of it
OTHER = -1002            # another group scope
DM = 555
S = "claude-api__g1001"
DM_S = "claude-api__d555"
ALY, BOB = 1, 2

# The real raw-JSON seam, taken before conftest's autouse guard replaces it
# (the client under it is faked in the one test that calls it).
_REAL_POST = rm._post

POLICY = load_policy(Path("/nonexistent/aipager-policy.yaml"),
                     Path("/nonexistent/aipager-policy.d"))


# ---- builders ----------------------------------------------------------------

def _group(chat_id=GROUP, label="team"):
    return Scope(chat_id=chat_id, kind="group", label=label,
                 members=(Member(id=ALY, label="aly", role="owner"),
                          Member(id=BOB, label="bob", role="user")))


def _dm():
    return Scope(chat_id=DM, kind="dm", label="aly DM",
                 members=(Member(id=ALY, label="aly", role="owner"),))


def _cfg() -> Path:
    return scope_mod.CONFIG_PATH


def _write_config(scopes, *, extra: str = "") -> bytes:
    """aipager.yaml as the wizard writes it, plus a comment and the Mini
    App / claude_path keys a surgical edit must leave alone."""
    scope_mod.dump_scopes(scopes, "123456:FAKE", path=_cfg())
    scope_mod.dump_miniapp({"enabled": True, "port": 9999,
                            "public_url": "https://x.example"}, path=_cfg())
    scope_mod.dump_claude_path("/opt/claude/bin/claude", path=_cfg())
    text = _cfg().read_text(encoding="utf-8")
    text = text.replace("scopes:\n", "# my own note\nscopes:\n", 1) + extra
    _cfg().write_text(text, encoding="utf-8")
    return _cfg().read_bytes()


@pytest.fixture(autouse=True)
def _fixed_policy(monkeypatch):
    monkeypatch.setattr("aipager.policy.load_policy", lambda *a, **k: POLICY)


@pytest.fixture
def gbot(mk_bot):
    def _mk(scopes=None, *, config=True):
        scopes = scopes if scopes is not None else [_dm(), _group()]
        if config:
            _write_config(scopes)
        bot = mk_bot(scopes=scopes)
        bot.policy = POLICY
        bot._app.bot.id = 999
        bot._app.bot.set_my_commands = AsyncMock()
        bot._app.bot.delete_my_commands = AsyncMock()
        bot._app.bot.set_chat_menu_button = AsyncMock()
        bot._app.bot.send_message = AsyncMock(
            side_effect=_send_into(bot), return_value=None)
        bot._message_chat_gate = filters.Chat({s.chat_id for s in scopes})
        chat_migration.set_handler(bot._migrate_chat)
        return bot
    return _mk


def _send_into(bot):
    """``send_message``: the old group's id fails as Telegram fails it."""
    async def _send(chat_id=None, text=None, *a, **kw):
        if chat_id == GROUP and not getattr(bot, "_old_chat_alive", False):
            raise ChatMigrated(NEW)
        return MagicMock(message_id=4242)
    return _send


def _session(bot, name=S, chat=GROUP, kind="group"):
    s = bot.registry.get_or_create(name)
    s.scope_chat_id, s.scope_kind = chat, kind
    s.status = Status.IDLE
    return s


def _service_update(chat_id, *, to=None, frm=None):
    chat = Chat(id=chat_id, type="group" if to is not None else "supergroup")
    kw = {}
    if to is not None:
        kw["migrate_to_chat_id"] = to
    if frm is not None:
        kw["migrate_from_chat_id"] = frm
    msg = Message(message_id=1, date=_dt.datetime.now(_dt.timezone.utc),
                  chat=chat, **kw)
    return Update(update_id=1, message=msg)


def _ptb_text_update(chat_id, text="hello"):
    chat = Chat(id=chat_id, type="private" if chat_id > 0 else "supergroup")
    msg = Message(message_id=1, date=_dt.datetime.now(_dt.timezone.utc),
                  chat=chat, text=text)
    return Update(update_id=1, message=msg)


async def _settle():
    """Let every migration a ChatMigrated scheduled run to its end."""
    for _ in range(5):
        pending = [t for t in list(chat_migration._tasks) if not t.done()]
        if not pending:
            break
        await asyncio.gather(*pending, return_exceptions=True)
    await asyncio.sleep(0)


def _notices(bot) -> list:
    return [c for c in bot._app.bot.send_message.await_args_list
            if chat_migration.NOTICE in (list(c.args) + list(c.kwargs.values()))]


def _seed_everything(bot, monkeypatch, *, mute=True):
    """State keyed by the old group (and by the DM, which must stay)."""
    sess = _session(bot)
    # A second live session in the group, so "the chat's only live
    # session" cannot stand in for a member's own target.
    _session(bot, "claude-web__g1001")
    dm_sess = _session(bot, DM_S, chat=DM, kind="dm")
    sess.trigger_msg_id = 31
    sess.last_msg_id = 32
    sess.pending_card_deletes = [33]
    sess.busy_msg_id = 34
    sess.pending_queue.append(("later", 35, 1.0, "", BOB))
    sess.busy_card_trigger = 36
    sess.card_settled_msg_id = 37
    sess.prompt_sent_msg = (GROUP, 38)
    dm_sess.trigger_msg_id = 61
    bot.registry.queued_line_deletes.extend([[GROUP, 39], [DM, 62]])
    bot.registry.set_target(S, GROUP, BOB)
    bot.registry.pinned_msg_ids[GROUP] = 77
    bot.registry.pinned_msg_ids[DM] = 88
    preferences.set_preference(GROUP, "layout", "merged")
    preferences.set_preference(DM, "layout", "replace")
    HELD.hold(chat_id=GROUP, session=S, label="api", rich_text="answer",
              plain_text="answer")
    HELD.hold(chat_id=DM, session=DM_S, label="api", rich_text="dm answer",
              plain_text="dm answer")
    if mute:
        MUTE.mute(GROUP, 300)
    limiter = BudgetRateLimiter(clock=lambda: 1_000_000.0)
    monkeypatch.setattr(rm, "_rate_limiter", limiter)
    limiter._budget_for(GROUP)
    limiter._budget_for(DM)
    bot._keyboard_levels[GROUP] = "templates"
    bot._keyboard_levels[(GROUP, BOB)] = "commands"
    bot._keyboard_levels[DM] = "models"
    bot._keyboard_owed[GROUP] = GROUP
    bot._pinned[GROUP] = PinnedChat()
    bot._pinned[DM] = PinnedChat()
    return sess, dm_sess, limiter


def _assert_moved(bot, sess, dm_sess, limiter, before: bytes, caplog):
    # 1. aipager.yaml: only the chat_id line changed, plus the record.
    after = _cfg().read_text(encoding="utf-8")
    expected = before.decode().replace(
        f"chat_id: {GROUP}\n", f"chat_id: {NEW}\n", 1)
    expected += f"chat_migrations:\n  {GROUP}: {NEW}\n"
    assert after == expected
    assert os.stat(_cfg()).st_mode & 0o777 == 0o600
    loaded, _tok = scope_mod.load_scopes(_cfg())
    assert [(s.chat_id, s.kind) for s in loaded] == [(DM, "dm"), (NEW, "group")]
    assert scope_mod.load_chat_migrations() == {GROUP: NEW}
    # 2. In memory: the scope, the gate, the menus.
    assert [(s.chat_id, s.kind, s.label) for s in bot.scopes] == [
        (DM, "dm", "aly DM"), (NEW, "group", "team")]
    gate = bot._message_chat_gate
    assert gate.check_update(_ptb_text_update(NEW))
    assert not gate.check_update(_ptb_text_update(GROUP))
    assert gate.check_update(_ptb_text_update(DM))
    deleted = [c.kwargs["scope"].chat_id
               for c in bot._app.bot.delete_my_commands.await_args_list]
    assert deleted == [GROUP]
    menus = [c.kwargs["scope"].chat_id
             for c in bot._app.bot.set_my_commands.await_args_list]
    assert NEW in menus
    # The sessions, re-stamped; the name keeps its old suffix.
    assert (sess.name, sess.scope_chat_id, sess.scope_kind) == (S, NEW, "group")
    assert (dm_sess.scope_chat_id, dm_sess.scope_kind) == (DM, "dm")
    assert set(bot.registry.all_sessions(NEW)) == {S, "claude-web__g1001"}
    assert S not in bot.registry.all_sessions(GROUP)
    # Message ids of the old chat are forgotten (they are not in the new
    # one); the held prompt keeps its text and sender.
    assert sess.trigger_msg_id is None and sess.last_msg_id is None
    assert sess.pending_card_deletes == [] and sess.busy_msg_id is None
    assert sess.pending_queue == [("later", None, 1.0, "", BOB)]
    assert sess.busy_card_trigger is None and sess.card_settled_msg_id == 0
    assert sess.prompt_sent_msg is None
    assert dm_sess.trigger_msg_id == 61
    assert bot.registry.queued_line_deletes == [[DM, 62]]
    # Targets and the member's own target.
    assert bot.registry.target_for(NEW, BOB) is sess
    assert bot.registry.target_for(NEW) is sess
    assert bot.registry.target_for(GROUP, BOB) is None
    # preferences.json's key moved; the DM's is untouched.
    assert preferences.get_preferences(NEW).layout == "merged"
    assert preferences.get_preferences(DM).layout == "replace"
    assert str(GROUP) not in preferences._load_raw()
    # Dropped for the old chat, kept for the DM.
    assert GROUP not in bot.registry.pinned_msg_ids
    assert bot.registry.pinned_msg_ids[DM] == 88
    assert HELD.count(GROUP) == 0 and HELD.count(DM) == 1
    assert not MUTE.is_muted(GROUP)
    assert GROUP not in limiter._budgets and DM in limiter._budgets
    assert GROUP not in bot._keyboard_levels
    assert (GROUP, BOB) not in bot._keyboard_levels
    assert bot._keyboard_levels[DM] == "models"
    assert GROUP not in bot._keyboard_owed
    assert GROUP not in bot._pinned and DM in bot._pinned
    # Persisted.
    fresh = SessionRegistry()
    fresh.load()
    assert S in fresh.all_sessions()
    assert fresh.all_sessions()[S].scope_chat_id == NEW
    # 4. One WARNING with both ids, and one line in the new chat.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING
                and r.name == "aipager.bot.chat_migration"]
    assert len(warnings) == 1
    assert str(GROUP) in warnings[0].getMessage()
    assert str(NEW) in warnings[0].getMessage()
    notices = _notices(bot)
    assert len(notices) == 1
    assert notices[0].args[:2] == (NEW, chat_migration.NOTICE)


# ---- the service messages ----------------------------------------------------------

def test_the_service_message_in_the_old_chat_moves_everything(
        gbot, run_async, monkeypatch, caplog):
    bot = gbot()
    before = _cfg().read_bytes()
    sess, dm_sess, limiter = _seed_everything(bot, monkeypatch)
    caplog.set_level(logging.INFO)

    run_async(chat_migration.handle_migrate_message(
        bot, _service_update(GROUP, to=NEW)))

    _assert_moved(bot, sess, dm_sess, limiter, before, caplog)


def test_the_service_message_in_the_new_chat_moves_everything(
        gbot, run_async, monkeypatch, caplog):
    bot = gbot()
    before = _cfg().read_bytes()
    sess, dm_sess, limiter = _seed_everything(bot, monkeypatch)
    caplog.set_level(logging.INFO)

    run_async(chat_migration.handle_migrate_message(
        bot, _service_update(NEW, frm=GROUP)))

    _assert_moved(bot, sess, dm_sess, limiter, before, caplog)


def test_ids_from_message_reads_both_directions():
    assert chat_migration.ids_from_message(
        _service_update(GROUP, to=NEW).message) == (GROUP, NEW)
    assert chat_migration.ids_from_message(
        _service_update(NEW, frm=GROUP).message) == (GROUP, NEW)
    assert chat_migration.ids_from_message(
        _ptb_text_update(GROUP).message) is None


def _start_with_recorded_handlers(bot, run_async):
    handlers: list = []
    app = MagicMock()
    app.add_handler = lambda h, group=0: handlers.append((group, h))
    app.initialize = AsyncMock()
    app.start = AsyncMock()
    app.updater.start_polling = AsyncMock()
    app.bot = bot._app.bot
    builder = MagicMock()
    builder.build.return_value = app
    bot._make_builder = lambda: builder
    bot._update_bot_commands = AsyncMock()
    try:
        run_async(bot.start())
    finally:
        del bot._make_builder
        del bot._update_bot_commands
    return handlers


@pytest.mark.parametrize("update", [
    _service_update(GROUP, to=NEW), _service_update(NEW, frm=GROUP)])
def test_the_daemon_routes_both_service_messages_to_the_migration(
        gbot, run_async, update):
    bot = gbot()
    chat_migration.set_handler(None)
    handlers = _start_with_recorded_handlers(bot, run_async)
    first = min(g for g, _h in handlers)
    mig = [(g, h) for g, h in handlers if isinstance(h, MessageHandler)
           and h.check_update(update)]
    # Its own group, before the intake gate (-2): nothing can stop it, and
    # the new chat is not in the message chat gate yet.
    assert [g for g, _h in mig] == [first] and first < -2
    assert mig[0][1].callback.func is chat_migration.handle_migrate_message
    # The daemon reports ChatMigrated to this bot.
    assert chat_migration._handler == bot._migrate_chat
    # And a plain message in the new chat still is not taken (yet).
    text = next(h for _g, h in handlers if isinstance(h, MessageHandler)
                and h.callback == bot._handle_message)
    assert not text.check_update(_ptb_text_update(NEW))
    run_async(mig[0][1].callback(update, None))
    assert text.check_update(_ptb_text_update(NEW))


@pytest.mark.parametrize("update", [
    _service_update(GROUP, to=NEW), _service_update(NEW, frm=GROUP)])
def test_the_intake_gate_lets_the_service_message_through(
        gbot, run_async, update):
    bot = gbot()
    try:
        run_async(group_intake.intake_gate(bot, update))
    except ApplicationHandlerStop:  # pragma: no cover - the failure
        pytest.fail("the group intake gate stopped a migration notice")


def test_p7c_after_the_upgrade_the_owner_is_authorized_in_the_new_chat(
        gbot, run_async):
    """Probe p7 part c: /status from the owner in the upgraded group."""
    bot = gbot()

    def _status():
        u = MagicMock()
        u.effective_chat = MagicMock(id=NEW, type="supergroup")
        u.effective_user = MagicMock(id=ALY, username="aly",
                                     first_name="A", last_name="")
        m = MagicMock(text="/status", message_id=503, reply_to_message=None,
                      chat=u.effective_chat, reply_text=AsyncMock())
        u.message = u.effective_message = m
        u.callback_query = None
        return u

    assert run_async(bot._authorize(_status())) is False
    run_async(chat_migration.handle_migrate_message(
        bot, _service_update(NEW, frm=GROUP)))
    assert run_async(bot._authorize(_status())) is True


# ---- ChatMigrated from a call ------------------------------------------------------

def test_chat_migrated_from_a_send_moves_everything(
        gbot, run_async, monkeypatch, caplog):
    bot = gbot()
    before = _cfg().read_bytes()
    # (A muted chat makes no call, so it cannot learn of the upgrade this
    # way; the service messages above cover a muted one.)
    sess, dm_sess, limiter = _seed_everything(bot, monkeypatch, mute=False)
    caplog.set_level(logging.INFO)

    async def _go():
        with pytest.raises(ChatMigrated):
            await send_text(bot._app.bot, GROUP, "the answer")
        await _settle()

    run_async(_go())
    _assert_moved(bot, sess, dm_sess, limiter, before, caplog)


def test_the_rate_limiter_reports_chat_migrated_from_any_call(
        gbot, run_async):
    """The one place every Bot API call passes (PTB's and the rich path's)."""
    bot = gbot()
    _session(bot)
    limiter = BudgetRateLimiter(clock=lambda: 1_000_000.0)

    async def _call():
        raise ChatMigrated(NEW)

    async def _go():
        with pytest.raises(ChatMigrated):
            await limiter.process_request(
                callback=_call, args=(), kwargs={}, endpoint="editMessageText",
                data={"chat_id": GROUP}, rate_limit_args=None)
        await _settle()

    run_async(_go())
    assert [s.chat_id for s in bot.scopes] == [DM, NEW]
    assert bot.registry.get(S).scope_chat_id == NEW
    assert len(_notices(bot)) == 1


def test_a_rich_message_refusal_naming_the_new_id_is_reported(
        gbot, run_async, monkeypatch):
    bot = gbot()

    class _Resp:
        def json(self):
            return {"ok": False, "error_code": 400,
                    "description": "Bad Request: group chat was upgraded to "
                                   "a supergroup chat",
                    "parameters": {"migrate_to_chat_id": NEW}}

    client = MagicMock()
    client.post = AsyncMock(return_value=_Resp())
    monkeypatch.setattr(rm, "_get_client", lambda: client)

    async def _go():
        await _REAL_POST("sendRichMessage", {"chat_id": GROUP})
        await _settle()

    run_async(_go())
    assert [s.chat_id for s in bot.scopes] == [DM, NEW]


def test_an_edit_into_the_old_chat_is_reported(gbot, run_async):
    from aipager.bot.transport import edit_text_at
    bot = gbot()
    bot._app.bot.edit_message_text = AsyncMock(side_effect=ChatMigrated(NEW))

    async def _go():
        with pytest.raises(ChatMigrated):
            await edit_text_at(bot._app.bot, "x", GROUP, 5)
        await _settle()

    run_async(_go())
    assert [s.chat_id for s in bot.scopes] == [DM, NEW]


def test_the_busy_card_send_path_is_reported(gbot, run_async):
    """``_send_with_retry`` (answers and cards on the plain path)."""
    from aipager.bot.transport import _send_with_retry
    bot = gbot()

    async def _go():
        with pytest.raises(ChatMigrated):
            await _send_with_retry(bot._app.bot, chat_id=GROUP, text="x")
        await _settle()

    run_async(_go())
    assert [s.chat_id for s in bot.scopes] == [DM, NEW]


# ---- the pinned bar ------------------------------------------------------------------

def _pinned_bot(gbot):
    bot = gbot()
    _session(bot)
    st = bot._pinned.setdefault(GROUP, PinnedChat())
    return bot, st


def test_the_pinned_bar_treats_chat_migrated_as_permanent_on_send(
        gbot, run_async):
    bot, st = _pinned_bot(gbot)

    async def _go():
        try:
            await bot._refresh_pinned_chat(GROUP)
        except ChatMigrated:
            return "escaped"
        retry = st.trailing
        await _settle()
        return retry

    retry = run_async(_go())
    assert retry != "escaped"
    # No 60 s retry loop: the old id is never tried again.
    assert st.hold_until == 0.0
    assert retry is None or retry.cancelled()
    assert st.disabled is True
    # The daemon followed the group, and its bar starts fresh there.
    assert [s.chat_id for s in bot.scopes] == [DM, NEW]
    assert GROUP not in bot._pinned
    assert GROUP not in bot._pinned_chats() and NEW in bot._pinned_chats()


def test_the_pinned_bar_treats_chat_migrated_as_permanent_on_edit(
        gbot, run_async):
    bot, st = _pinned_bot(gbot)
    bot.registry.pinned_msg_ids[GROUP] = 77
    bot._app.bot.edit_message_text = AsyncMock(side_effect=ChatMigrated(NEW))

    async def _go():
        try:
            await bot._refresh_pinned_chat(GROUP)
        except ChatMigrated:
            return True
        await _settle()
        return False

    assert run_async(_go()) is False, "ChatMigrated escaped the pinned bar"
    assert st.disabled is True
    assert bot._app.bot.edit_message_text.await_count == 1
    assert [s.chat_id for s in bot.scopes] == [DM, NEW]
    assert GROUP not in bot.registry.pinned_msg_ids


def test_the_pinned_bar_follows_the_upgrade_on_its_own(
        gbot, run_async, monkeypatch):
    """Even with no transport or limiter report (a seam that does not
    pass them), the bar itself triggers the migration."""
    from aipager.bot import transport
    bot, st = _pinned_bot(gbot)
    monkeypatch.setattr(transport, "_note_migrated", lambda *a: None)

    async def _go():
        await bot._refresh_pinned_chat(GROUP)
        await _settle()

    run_async(_go())
    assert [s.chat_id for s in bot.scopes] == [DM, NEW]


def test_a_chat_migrated_at_the_pin_is_permanent_too(gbot, run_async):
    bot, st = _pinned_bot(gbot)
    bot._old_chat_alive = True          # the send lands, the pin finds it moved
    bot._app.bot.pin_chat_message = AsyncMock(side_effect=ChatMigrated(NEW))
    bot._app.bot.delete_message = AsyncMock()

    async def _go():
        await bot._refresh_pinned_chat(GROUP)
        await _settle()

    run_async(_go())
    assert st.disabled is True
    assert bot._app.bot.delete_message.await_count == 0
    assert [s.chat_id for s in bot.scopes] == [DM, NEW]


def test_a_transient_pinned_failure_still_retries_in_60_s(gbot, run_async):
    """The arm next to the new one is unchanged."""
    bot, st = _pinned_bot(gbot)
    bot._app.bot.send_message = AsyncMock(side_effect=NetworkError("blip"))

    async def _go():
        await bot._refresh_pinned_chat(GROUP)
        t = st.trailing
        if t is not None:
            t.cancel()
        return t

    t = run_async(_go())
    assert t is not None and st.disabled is False
    assert st.hold_until > 0 and _TRANSIENT_RETRY == 60.0
    assert [s.chat_id for s in bot.scopes] == [DM, GROUP]


# ---- once, and only for a configured group ----------------------------------------

def test_both_service_messages_and_an_exception_migrate_once(
        gbot, run_async, monkeypatch):
    bot = gbot()
    _session(bot)
    writes = []
    real = scope_mod.migrate_scope_chat_id
    monkeypatch.setattr(scope_mod, "migrate_scope_chat_id",
                        lambda *a, **k: (writes.append(a), real(*a, **k)))

    async def _go():
        results = await asyncio.gather(
            bot._migrate_chat(GROUP, NEW),
            chat_migration.handle_migrate_message(
                bot, _service_update(NEW, frm=GROUP)),
            chat_migration.handle_migrate_message(
                bot, _service_update(GROUP, to=NEW)))
        with pytest.raises(ChatMigrated):
            await send_text(bot._app.bot, GROUP, "late")
        await _settle()
        return results

    results = run_async(_go())
    assert results[0] is True
    assert writes == [(GROUP, NEW)]
    assert len(_notices(bot)) == 1
    assert [s.chat_id for s in bot.scopes] == [DM, NEW]
    assert scope_mod.load_chat_migrations() == {GROUP: NEW}


def test_an_unknown_old_chat_changes_nothing(gbot, run_async, caplog):
    bot = gbot()
    before = _cfg().read_bytes()
    caplog.set_level(logging.WARNING)
    # Through the daemon's own entry points, which never raise.
    run_async(chat_migration.handle_migrate_message(
        bot, _service_update(NEW, frm=-1007777)))
    run_async(chat_migration.handle_migrate_message(
        bot, _service_update(-1007777, to=NEW)))
    assert _cfg().read_bytes() == before
    assert [s.chat_id for s in bot.scopes] == [DM, GROUP]
    assert _notices(bot) == []
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_a_group_never_moves_to_a_private_chat_id(gbot, run_async):
    bot = gbot()
    _session(bot)
    before = _cfg().read_bytes()
    assert run_async(bot._migrate_chat(GROUP, 4242)) is False
    assert _cfg().read_bytes() == before
    assert [s.chat_id for s in bot.scopes] == [DM, GROUP]
    assert bot.registry.get(S).scope_chat_id == GROUP


def test_a_plain_move_logs_exactly_one_warning(gbot, run_async, caplog):
    """Nothing seeded (no preferences, no flood state, no targets): every
    step still runs cleanly, and the one WARNING is the move itself."""
    bot = gbot()
    _session(bot)
    caplog.set_level(logging.INFO)
    assert run_async(bot._migrate_chat(GROUP, NEW)) is True
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "upgraded group" in warnings[0].getMessage()


def test_a_group_only_install_follows_its_home_chat_id(
        gbot, run_async, monkeypatch):
    """With no private chat scope, config.CHAT_ID is the group."""
    from aipager import config
    bot = gbot(scopes=[_group()])
    monkeypatch.setattr(config, "CHAT_ID", str(GROUP))
    run_async(bot._migrate_chat(GROUP, NEW))
    assert config.CHAT_ID == str(NEW)


def test_a_dm_installs_chat_id_is_never_touched(gbot, run_async, monkeypatch):
    from aipager import config
    bot = gbot()
    monkeypatch.setattr(config, "CHAT_ID", str(DM))
    run_async(bot._migrate_chat(GROUP, NEW))
    assert config.CHAT_ID == str(DM)


def test_preferences_of_the_new_chat_are_never_overwritten():
    preferences.set_preference(GROUP, "layout", "merged")
    preferences.set_preference(NEW, "layout", "replace")
    assert preferences.move_chat(GROUP, NEW) is False
    assert preferences.get_preferences(NEW).layout == "replace"
    assert preferences.get_preferences(GROUP).layout == "merged"
    assert preferences.move_chat(-1005, NEW) is False


def test_forgetting_the_chat_keeps_a_card_claim_in_flight():
    reg = SessionRegistry()
    s = reg.get_or_create("claude-q")
    s.busy_msg_id = -1
    s.forget_chat_messages()
    assert s.busy_msg_id == -1


def test_a_new_id_that_already_is_a_scope_changes_nothing(
        gbot, run_async, caplog):
    bot = gbot(scopes=[_dm(), _group(), _group(chat_id=NEW, label="other")])
    _session(bot)
    before = _cfg().read_bytes()
    caplog.set_level(logging.WARNING)
    assert run_async(bot._migrate_chat(GROUP, NEW)) is False
    assert _cfg().read_bytes() == before
    assert [s.chat_id for s in bot.scopes] == [DM, GROUP, NEW]
    assert bot.registry.get(S).scope_chat_id == GROUP
    assert _notices(bot) == []
    assert any(str(GROUP) in r.getMessage() and str(NEW) in r.getMessage()
               and "already a scope" in r.getMessage()
               for r in caplog.records if r.levelno == logging.WARNING)


def test_personal_mode_never_migrates(gbot, run_async):
    bot = gbot(config=False)
    bot.scopes = None
    assert run_async(bot._migrate_chat(GROUP, NEW)) is False
    assert not _cfg().exists()
    assert _notices(bot) == []


# ---- DM parity -------------------------------------------------------------------------

def test_a_dm_never_migrates(gbot, run_async):
    bot = gbot(scopes=[_dm()])
    dm_sess = _session(bot, DM_S, chat=DM, kind="dm")
    before = _cfg().read_bytes()
    assert run_async(bot._migrate_chat(DM, NEW)) is False
    assert run_async(bot._migrate_chat(DM, -DM)) is False
    assert _cfg().read_bytes() == before
    assert [s.chat_id for s in bot.scopes] == [DM]
    assert dm_sess.scope_chat_id == DM
    assert _notices(bot) == []


def test_a_dm_install_stamps_and_sends_as_before(gbot, run_async):
    bot = gbot(scopes=[_dm()])
    sess = bot.registry.get_or_create("claude-x__d555")
    assert (sess.scope_chat_id, sess.scope_kind) == (DM, "dm")
    plain = bot.registry.get_or_create("claude-y")
    assert (plain.scope_chat_id, plain.scope_kind) == (DM, "dm")
    assert scope_mod.load_chat_migrations() == {}
    run_async(send_text(bot._app.bot, DM, "hi"))
    assert [s.chat_id for s in bot.scopes] == [DM]


# ---- a socket named after the old id ---------------------------------------------------

def test_a_socket_discovered_after_the_move_lands_in_the_new_chat(
        gbot, run_async):
    bot = gbot()
    run_async(bot._migrate_chat(GROUP, NEW))
    late = bot.registry.get_or_create("claude-late__g1001")
    assert (late.scope_chat_id, late.scope_kind) == (NEW, "group")
    # After a state loss: a fresh registry, the same daemon config.
    lost = SessionRegistry()
    again = lost.get_or_create("claude-other__g1001")
    assert (again.scope_chat_id, again.scope_kind) == (NEW, "group")
    # Another group's suffix is untouched.
    other = lost.get_or_create("claude-z__g1002")
    assert other.scope_chat_id == OTHER


def test_the_old_id_is_followed_only_while_it_is_not_a_scope_again(
        gbot, run_async):
    bot = gbot()
    run_async(bot._migrate_chat(GROUP, NEW))
    bot.scopes = [_dm(), _group(chat_id=NEW), _group(label="back")]
    late = bot.registry.get_or_create("claude-late__g1001")
    assert late.scope_chat_id == GROUP


def test_state_saved_before_the_move_is_followed_at_load(
        gbot, run_async, monkeypatch):
    """The daemon stopped after rewriting aipager.yaml and before saving
    its registry: the next start still finds the old id in the state."""
    bot = gbot()
    _session(bot)
    bot.registry.set_target(S, GROUP, BOB)
    bot.registry.pinned_msg_ids[GROUP] = 77
    bot.registry.save()
    scope_mod.migrate_scope_chat_id(GROUP, NEW)
    monkeypatch.setattr("aipager.state._live_scope_source", None)
    monkeypatch.setattr("aipager.config.SCOPES", [_dm(), _group(chat_id=NEW)])

    fresh = SessionRegistry()
    fresh.load()
    sess = fresh.get(S)
    assert (sess.scope_chat_id, sess.scope_kind) == (NEW, "group")
    assert fresh.target_for(NEW, BOB) is sess
    assert GROUP not in fresh.pinned_msg_ids


def test_a_typed_old_internal_name_is_accepted_only_in_the_new_chat(
        gbot, run_async):
    """Choice for 8.76 after an upgrade: ``/api__g1001`` names the old id,
    which now lives at the new id, so it is the new chat's session there
    and foreign everywhere else."""
    bot = gbot(scopes=[_dm(), _group(), _group(chat_id=OTHER, label="o")])
    name = "claude-api__g1001"
    assert bot._typed_name_foreign(name, NEW) is True      # before
    run_async(bot._migrate_chat(GROUP, NEW))
    assert bot._typed_name_foreign(name, NEW) is False
    assert bot._typed_name_foreign(name, DM) is True
    assert bot._typed_name_foreign(name, OTHER) is True
    assert bot._typed_name_foreign("claude-api__d555", NEW) is True


# ---- aipager.yaml ------------------------------------------------------------------------

def test_the_yaml_edit_is_surgical(tmp_path):
    before = _write_config([_dm(), _group()])
    scope_mod.migrate_scope_chat_id(GROUP, NEW)
    after = _cfg().read_bytes()
    assert after == before.replace(
        f"chat_id: {GROUP}\n".encode(), f"chat_id: {NEW}\n".encode()) + (
        f"chat_migrations:\n  {GROUP}: {NEW}\n".encode())
    assert b"# my own note\n" in after
    assert os.stat(_cfg()).st_mode & 0o777 == 0o600


def test_an_existing_record_falls_back_to_a_full_rewrite(tmp_path):
    _write_config([_dm(), _group(), _group(chat_id=OTHER, label="o")],
                  extra="chat_migrations:\n  -1003: -1004\n")
    raw_before = scope_mod._raw_yaml(_cfg())
    scope_mod.migrate_scope_chat_id(GROUP, NEW)
    raw_after = scope_mod._raw_yaml(_cfg())
    assert raw_after["chat_migrations"] == {-1003: -1004, GROUP: NEW}
    for key in ("bot_token", "miniapp", "claude_path", "default_mode",
                "schema_version"):
        assert raw_after[key] == raw_before[key]
    assert [s["chat_id"] for s in raw_after["scopes"]] == [DM, NEW, OTHER]
    assert raw_after["scopes"][1] == {**raw_before["scopes"][1], "chat_id": NEW}
    assert os.stat(_cfg()).st_mode & 0o777 == 0o600


def test_a_quoted_chat_id_is_rewritten_as_a_number(tmp_path):
    _write_config([_dm(), _group()])
    text = _cfg().read_text(encoding="utf-8").replace(
        f"chat_id: {GROUP}\n", f"chat_id: '{GROUP}'\n", 1)
    _cfg().write_text(text, encoding="utf-8")
    scope_mod.migrate_scope_chat_id(GROUP, NEW)
    raw = scope_mod._raw_yaml(_cfg())
    assert [s["chat_id"] for s in raw["scopes"]] == [DM, NEW]
    assert raw["chat_migrations"] == {GROUP: NEW}


def test_the_yaml_edit_refuses_a_dm_and_a_taken_id(tmp_path):
    _write_config([_dm(), _group(), _group(chat_id=OTHER, label="o")])
    before = _cfg().read_bytes()
    refused = []
    for old, new in ((DM, NEW), (GROUP, OTHER)):
        try:
            scope_mod.migrate_scope_chat_id(old, new)
        except scope_mod.ScopeConfigError:
            refused.append((old, new))
    assert refused == [(DM, NEW), (GROUP, OTHER)]
    assert _cfg().read_bytes() == before


def test_the_wizard_keeps_the_record(tmp_path):
    _write_config([_dm(), _group()])
    scope_mod.migrate_scope_chat_id(GROUP, NEW)
    scopes, token = scope_mod.load_scopes(_cfg())
    scope_mod.dump_scopes(scopes, token, path=_cfg())
    assert scope_mod.load_chat_migrations() == {GROUP: NEW}


def test_a_failed_yaml_write_still_follows_the_group_until_restart(
        gbot, run_async, monkeypatch, caplog):
    bot = gbot()
    _session(bot)

    def _boom(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr(scope_mod, "migrate_scope_chat_id", _boom)
    caplog.set_level(logging.WARNING)
    assert run_async(bot._migrate_chat(GROUP, NEW)) is True
    assert [s.chat_id for s in bot.scopes] == [DM, NEW]
    assert any("aipager config" in r.getMessage() for r in caplog.records)


# ---- the wizard and doctor -----------------------------------------------------------------

def test_the_wizard_test_send_shows_the_new_id(monkeypatch):
    import urllib.error
    from io import BytesIO

    from aipager.wizard import telegram_api

    body = (b'{"ok": false, "error_code": 400, "description": "Bad Request: '
            b'group chat was upgraded to a supergroup chat", "parameters": '
            b'{"migrate_to_chat_id": -1009999}}')

    def _fake(*a, **k):
        raise urllib.error.HTTPError(url="x", code=400, msg="x", hdrs=None,
                                     fp=BytesIO(body))

    monkeypatch.setattr(telegram_api.urllib.request, "urlopen", _fake)
    ok, msg = telegram_api._test_send("tok", GROUP)
    assert ok is False
    assert "upgraded to a supergroup chat" in msg
    assert "-1009999" in msg
    assert "moves the scope there by itself" in msg
    assert "when it starts" in msg
    assert "reopen `aipager config`" in msg
    assert "—" not in msg


def test_the_wizard_test_send_of_another_refusal_is_unchanged(monkeypatch):
    import urllib.error
    from io import BytesIO

    from aipager.wizard import telegram_api

    def _fake(*a, **k):
        raise urllib.error.HTTPError(
            url="x", code=400, msg="x", hdrs=None,
            fp=BytesIO(b'{"description": "chat not found"}'))

    monkeypatch.setattr(telegram_api.urllib.request, "urlopen", _fake)
    assert telegram_api._test_send("tok", GROUP) == (False, "chat not found")


def test_doctor_names_the_scopes_on_a_scope_install(monkeypatch, tmp_path):
    from aipager import doctor
    monkeypatch.setattr("aipager.team.TEAM_CONFIG_PATH", tmp_path / "no.yaml")
    monkeypatch.setattr("aipager.config.SCOPES", [_dm(), _group()])
    r = doctor.check_team()
    assert r.status == doctor.OK
    assert r.detail == ["scope mode: 2 scopes (1 private chat, 1 group) in "
                        "aipager.yaml"]


def test_doctor_on_the_operators_one_dm_install(monkeypatch, tmp_path):
    from aipager import doctor
    monkeypatch.setattr("aipager.team.TEAM_CONFIG_PATH", tmp_path / "no.yaml")
    monkeypatch.setattr("aipager.config.SCOPES", [_dm()])
    r = doctor.check_team()
    assert r.status == doctor.OK
    assert r.detail == ["scope mode: 1 scope (1 private chat) in aipager.yaml"]
    monkeypatch.setattr("aipager.config.SCOPES", None)
    assert doctor.check_team().detail == ["personal mode (no team.yaml)"]


# ---- review iteration 1 -----------------------------------------------------------------

def test_a_group_upgraded_while_the_daemon_was_down_is_found_at_start(
        gbot, run_async):
    """rev-iter1-001: polling drops the service messages that arrived
    while the daemon was down, so the start asks each group once."""
    bot = gbot()
    asked = []

    async def _get_chat(chat_id, *a, **k):
        asked.append(chat_id)
        if chat_id == GROUP:
            raise ChatMigrated(NEW)
        return MagicMock(id=chat_id)

    bot._app.bot.get_chat = _get_chat

    async def _go():
        await bot._probe_group_chats()
        await _settle()

    run_async(_go())
    assert asked == [GROUP]                 # a private chat is never asked
    assert [s.chat_id for s in bot.scopes] == [DM, NEW]
    assert len(_notices(bot)) == 1


def _recording_app(bot):
    app = MagicMock()
    app.add_handler = lambda h, group=0: None
    app.initialize = AsyncMock()
    app.start = AsyncMock()
    app.updater.start_polling = AsyncMock()
    app.bot = bot._app.bot
    builder = MagicMock()
    builder.build.return_value = app
    bot._make_builder = lambda: builder
    bot._update_bot_commands = AsyncMock()


def test_the_start_runs_the_group_probe_in_the_background(gbot, run_async):
    """rev-iter2-001: start() never waits on a group's lookup (the hook
    receiver, recovery and the monitor start after it)."""
    bot = gbot()
    _recording_app(bot)
    release = asyncio.Event
    asked = []

    async def _go():
        gate = release()

        async def _get_chat(chat_id, *a, **k):
            asked.append(chat_id)
            await gate.wait()           # Telegram never answers

        bot._app.bot.get_chat = _get_chat
        try:
            await asyncio.wait_for(bot.start(), 2)
            started = True
        except asyncio.TimeoutError:
            started = False
        await asyncio.sleep(0)
        task = bot._group_probe_task
        pending = task is not None and not task.done()
        bot._app.updater.stop = AsyncMock()
        bot._app.stop = AsyncMock()
        bot._app.shutdown = AsyncMock()
        await bot.stop()
        await asyncio.sleep(0)
        return started, pending, task

    started, pending, task = run_async(_go())
    assert started, "start() waited on the group lookup"
    assert pending
    assert asked == [GROUP]
    assert task.cancelled()             # stop() takes it down


def test_a_dm_install_starts_no_group_probe(gbot, run_async):
    bot = gbot(scopes=[_dm()])
    _recording_app(bot)
    bot._app.bot.get_chat = AsyncMock()
    run_async(bot.start())
    assert bot._group_probe_task is None
    assert bot._app.bot.get_chat.await_count == 0


def test_a_failing_group_lookup_never_stops_the_next_one(gbot, run_async):
    """rev-iter2-003: a stale group (bot removed) must not stop the probe,
    let alone the start."""
    from telegram.error import Forbidden
    bot = gbot(scopes=[_dm(), _group(chat_id=OTHER, label="gone"), _group()])
    asked = []

    async def _get_chat(chat_id, *a, **k):
        asked.append(chat_id)
        if chat_id == OTHER:
            raise Forbidden("bot was kicked from the group chat")
        raise ChatMigrated(NEW)

    bot._app.bot.get_chat = _get_chat

    async def _go():
        try:
            await bot._probe_group_chats()
        except Exception:
            pass                        # asserted below, not as an error
        await _settle()

    run_async(_go())
    assert asked == [OTHER, GROUP]
    assert [s.chat_id for s in bot.scopes] == [DM, OTHER, NEW]


def test_a_dm_install_asks_telegram_nothing_more_at_start(gbot, run_async):
    bot = gbot(scopes=[_dm()])
    bot._app.bot.get_chat = AsyncMock()
    run_async(bot._probe_group_chats())
    assert bot._app.bot.get_chat.await_count == 0


def test_chat_migrated_from_the_command_menu_is_followed(gbot, run_async):
    """setMyCommands names the chat only inside `scope=`: the limiter
    cannot tell which chat moved, the menu code can."""
    bot = gbot()

    async def _set(commands, scope=None, **k):
        if scope.chat_id == GROUP:
            raise ChatMigrated(NEW)

    bot._app.bot.set_my_commands = _set

    async def _go():
        await bot._update_bot_commands()
        await _settle()

    run_async(_go())
    assert [s.chat_id for s in bot.scopes] == [DM, NEW]


def test_chat_migrated_from_clearing_a_menu_is_reported(
        gbot, run_async, monkeypatch):
    bot = gbot()
    noted = []
    monkeypatch.setattr(chat_migration, "note_chat_migrated",
                        lambda old, new: noted.append((old, new)))
    bot._app.bot.delete_my_commands = AsyncMock(side_effect=ChatMigrated(-1004))
    run_async(bot._refresh_scope_menus([_dm(), _group(),
                                        _group(chat_id=-1003, label="x")]))
    assert noted == [(-1003, -1004)]


def test_a_damaged_record_never_moves_a_private_chat(tmp_path, gbot):
    _write_config([_dm(), _group()], extra=(
        "chat_migrations:\n  abc: -5\n  -7: zz\n  -8: -8\n  555: -9\n"
        "  -10: 12\n  -11: -12\n"))
    assert scope_mod.load_chat_migrations() == {-11: -12}
    bot = gbot(scopes=[_dm(), _group()], config=False)
    dm = bot.registry.get_or_create("claude-x__d555")
    assert dm.scope_chat_id == DM


def test_following_a_chain_and_a_loop_ends():
    assert scope_mod.follow_chat_migrations(-1, {-1: -2, -2: -3}) == -3
    assert scope_mod.follow_chat_migrations(-9, {-1: -2}) == -9
    assert scope_mod.follow_chat_migrations(-1, {-1: -2, -2: -1}) in (-1, -2)


def test_the_wizard_never_writes_a_followed_group_back(tmp_path):
    """rev-iter1-003: `aipager config` was open during the move and still
    holds the old id; its save writes the new one."""
    _write_config([_dm(), _group()])
    stale, token = scope_mod.load_scopes(_cfg())
    scope_mod.migrate_scope_chat_id(GROUP, NEW)
    scope_mod.dump_scopes(stale, token, path=_cfg())
    loaded, _t = scope_mod.load_scopes(_cfg())
    assert [s.chat_id for s in loaded] == [DM, NEW]
    # A DM, or a group whose new id is already a scope, is written as is.
    scope_mod.dump_scopes([_dm(), _group(), _group(chat_id=NEW, label="n")],
                          token, path=_cfg())
    raw = scope_mod._raw_yaml(_cfg())
    assert [s["chat_id"] for s in raw["scopes"]] == [DM, GROUP, NEW]


def test_the_wizard_save_never_moves_a_dm_on_a_damaged_record(tmp_path):
    """rev-iter2-002: the save reads the record through the same filter
    as everything else."""
    _write_config([_dm(), _group()],
                  extra="chat_migrations:\n  555: -9\n  -1001: 77\n")
    scopes, token = scope_mod.load_scopes(_cfg())
    scope_mod.dump_scopes(scopes, token, path=_cfg())
    raw = scope_mod._raw_yaml(_cfg())
    assert [s["chat_id"] for s in raw["scopes"]] == [DM, GROUP]


def test_the_wizard_test_send_reads_a_refusal_with_status_200(monkeypatch):
    import io
    import json as _json

    from aipager.wizard import telegram_api

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    body = {"ok": False, "description": "Bad Request: group chat was "
            "upgraded to a supergroup chat",
            "parameters": {"migrate_to_chat_id": NEW}}
    monkeypatch.setattr(telegram_api.urllib.request, "urlopen",
                        lambda *a, **k: _Resp(_json.dumps(body).encode()))
    ok, msg = telegram_api._test_send("tok", GROUP)
    assert ok is False and str(NEW) in msg


def test_old_chat_prompt_surfaces_notices_and_timers_are_dropped(
        gbot, run_async):
    bot = gbot()
    _session(bot)
    bot._resent_prompts[(GROUP, 5)] = (S, 1)
    bot._resent_prompts[(DM, 6)] = (DM_S, 2)
    bot._read_only_told.add((GROUP, BOB))
    bot._read_only_told.add((DM, ALY))

    async def _go():
        st = bot._pinned.setdefault(GROUP, PinnedChat())
        st.trailing = asyncio.get_running_loop().create_task(asyncio.sleep(60))
        await bot._migrate_chat(GROUP, NEW)
        await asyncio.sleep(0)
        return st.trailing

    trailing = run_async(_go())
    assert trailing.cancelled()
    assert list(bot._resent_prompts) == [(DM, 6)]
    assert bot._read_only_told == {(DM, ALY)}


def test_stop_unregisters_the_daemon(gbot, run_async):
    bot = gbot()
    bot._app.updater.stop = AsyncMock()
    bot._app.stop = AsyncMock()
    bot._app.shutdown = AsyncMock()
    assert chat_migration._handler is not None
    run_async(bot.stop())
    assert chat_migration._handler is None


def test_an_unreadable_yaml_still_moves_the_group_until_restart(
        gbot, run_async, caplog):
    bot = gbot()
    _session(bot)
    _cfg().write_bytes(_cfg().read_bytes() + b"# \xff\xfe\n")
    caplog.set_level(logging.WARNING)
    run_async(chat_migration.handle_migrate_message(
        bot, _service_update(NEW, frm=GROUP)))
    assert [s.chat_id for s in bot.scopes] == [DM, NEW]
    assert bot.registry.get(S).scope_chat_id == NEW
    assert any("aipager config" in r.getMessage() for r in caplog.records)


def test_a_flow_style_scope_is_rewritten_whole(gbot, run_async):
    bot = gbot()
    _cfg().write_text(
        "schema_version: 3\nbot_token: '123456:FAKE'\nscopes:\n"
        f"- {{kind: dm, chat_id: {DM}, label: me, members: [{{id: 1, "
        "label: aly, role: owner}]}\n"
        f"- {{kind: group, chat_id: {GROUP}, label: team, members: [{{id: 1, "
        "label: aly, role: owner}]}\n", encoding="utf-8")
    run_async(bot._migrate_chat(GROUP, NEW))
    loaded, _t = scope_mod.load_scopes(_cfg())
    assert [s.chat_id for s in loaded] == [DM, NEW]
    assert scope_mod.load_chat_migrations() == {GROUP: NEW}


def test_a_failing_migration_task_is_logged_not_lost(run_async, caplog):
    async def _boom(old, new):
        raise RuntimeError("boom")

    chat_migration.set_handler(_boom)
    caplog.set_level(logging.WARNING)

    async def _go():
        chat_migration.note_chat_migrated(GROUP, NEW)
        await _settle()

    run_async(_go())
    assert any("Could not follow chat" in r.getMessage()
               for r in caplog.records)


def test_a_taken_new_id_is_logged_once(gbot, run_async, caplog):
    bot = gbot(scopes=[_dm(), _group(), _group(chat_id=NEW, label="other")])
    caplog.set_level(logging.WARNING)
    run_async(bot._migrate_chat(GROUP, NEW))
    run_async(bot._migrate_chat(GROUP, NEW))
    taken = [r for r in caplog.records if "already a scope" in r.getMessage()]
    assert len(taken) == 1


def test_a_note_written_before_the_move_is_the_same_sender(gbot, run_async):
    """rev-iter1-007: no spurious mixed-sender hold right after a move."""
    from aipager.bot.transport import _same_sender
    bot = gbot()
    assert _same_sender((GROUP, BOB), (NEW, BOB)) is False
    run_async(bot._migrate_chat(GROUP, NEW))
    assert _same_sender((GROUP, BOB), (NEW, BOB)) is True
    assert _same_sender((GROUP, BOB), (NEW, ALY)) is False
    assert _same_sender((DM, ALY), (NEW, ALY)) is False
    assert _same_sender((OTHER, BOB), (NEW, BOB)) is False
