"""Tests for `/new` (aipager/bot/new_flow.py): one parser for every
spelling, the Name card, the Ready card, and the chat's new-session
defaults (operator, 2026-09-30: "/new x1 or /new and then x1 ... I want
same experience for both"; "default ... must be automode").

Drives the module's own entry points (`start_wizard`, `maybe_handle_text`,
`create_from_text`, `handle_callback`); `create_session` is faked so no
process is launched. The spellings through the real `_handle_new_cmd` /
`_handle_message` are compared end to end in
tests/integration/new-session-flow/.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import config, preferences
from aipager.bot import new_flow
from aipager.bot.session_ops import ModelSwitchOutcome, RestartOutcome
from aipager.config import MODEL_CHOICES
from aipager.miniapp import launch
from aipager.state import SessionRegistry, Status, TrackedSession

CHAT = 555
OWNER = 111


# ---- fixtures ------------------------------------------------------------

@pytest.fixture
def wbot(mk_bot):
    """A bot whose message edits are recorded and whose `create_session`
    registers a session without launching anything."""
    def _mk(registry=None, *, admin=True, launch_error=""):
        bot = mk_bot(registry)
        bot._app.bot.edit_message_text = AsyncMock()
        bot._app.bot.edit_message_reply_markup = AsyncMock()
        bot._is_admin_user = MagicMock(return_value=admin)
        bot.created = []

        async def _create(label, *, scope_chat_id, skip_perms=False, cwd=None,
                          driver_user_id=None, model=None, keep_card=False):
            bot.created.append({"label": label, "scope": scope_chat_id,
                                "skip_perms": skip_perms, "cwd": cwd,
                                "model": model, "driver": driver_user_id})
            if launch_error:
                return "", launch_error
            name = f"claude-{label}"
            sess = bot.registry.get_or_create(name)
            sess.label = label
            sess.skip_perms = skip_perms
            sess.scope_chat_id = scope_chat_id
            if cwd:
                sess.cwd = cwd
            bot.registry.transition(name, Status.IDLE)
            return name, ""

        bot.create_session = _create
        return bot
    return _mk


@pytest.fixture
def mk_cb():
    def _mk(*, user_id=OWNER, chat_id=CHAT, message_id=900, chat_type="private"):
        query = MagicMock()
        query.answer = AsyncMock()
        query.message = MagicMock()
        query.message.message_id = message_id
        query.from_user = MagicMock()
        query.from_user.id = user_id
        update = MagicMock()
        update.callback_query = query
        update.effective_user = MagicMock()
        update.effective_user.id = user_id
        update.effective_chat = MagicMock()
        update.effective_chat.id = chat_id
        update.effective_chat.type = chat_type
        update.message = None
        return update, query
    return _mk


def _msg(mk_update, text, *, user_id=OWNER, message_id=77):
    update = mk_update(text, message_id=message_id, chat_id=CHAT, user_id=user_id)
    update.effective_chat.type = "private"
    update.message.reply_text = AsyncMock(return_value=MagicMock(message_id=900))
    return update


def _open_card(bot, mk_update, run_async, **kw):
    update = _msg(mk_update, "/new", **kw)
    run_async(new_flow.start_wizard(bot, update, MagicMock()))
    return update


def _last_edit(bot) -> dict:
    return bot._app.bot.edit_message_text.await_args.kwargs


def _buttons(kb) -> list[str]:
    return [b.text for row in kb.inline_keyboard for b in row]


def _cbs(kb) -> list[str]:
    return [b.callback_data for row in kb.inline_keyboard for b in row
            if b.callback_data is not None]


# ---- the Name card ----------------------------------------------------------

def test_bare_new_sends_the_name_card_with_auto_by_default(wbot, mk_update, run_async):
    bot = wbot()
    update = _open_card(bot, mk_update, run_async)

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    kb = update.message.reply_text.await_args.kwargs["reply_markup"]
    assert "Send a name" in text and "x1 fix the failing tests" in text
    assert "🤖 Auto" in text
    assert _buttons(kb) == ["💬 Ask instead", "🧠 Model", "📁 Folder", "✖️ Cancel"]
    pending = bot._new_wizard_pending[(CHAT, OWNER)]
    assert (pending["step"], pending["msg_id"], pending["user_id"],
            pending["skip_perms"]) == ("name", 900, OWNER, True)


def test_a_non_admin_gets_ask_and_no_mode_toggle(wbot, mk_update, run_async):
    bot = wbot(admin=False)
    update = _open_card(bot, mk_update, run_async)

    text = update.message.reply_text.await_args.args[0]
    kb = update.message.reply_text.await_args.kwargs["reply_markup"]
    assert "💬 Ask" in text and "🤖 Auto" not in text
    assert _buttons(kb) == ["🧠 Model", "📁 Folder", "✖️ Cancel"]


def test_the_chats_default_ask_is_honoured(wbot, mk_update, run_async):
    preferences.set_new_session_default(CHAT, "mode", "ask")
    bot = wbot()
    update = _open_card(bot, mk_update, run_async)

    assert bot._new_wizard_pending[(CHAT, OWNER)]["skip_perms"] is False
    kb = update.message.reply_text.await_args.kwargs["reply_markup"]
    assert "🤖 Auto instead" in _buttons(kb)


def test_second_new_replaces_first_cleanly(wbot, mk_update, run_async):
    bot = wbot()
    _open_card(bot, mk_update, run_async)
    update2 = _msg(mk_update, "/new")
    update2.message.reply_text = AsyncMock(return_value=MagicMock(message_id=901))
    run_async(new_flow.start_wizard(bot, update2, MagicMock()))

    kwargs = _last_edit(bot)
    assert kwargs["message_id"] == 900
    assert "started over" in kwargs["text"].lower()
    assert kwargs["reply_markup"] is None
    assert bot._new_wizard_pending[(CHAT, OWNER)]["msg_id"] == 901


def test_start_wizard_unauthorized_sends_nothing(wbot, mk_update, run_async):
    from aipager.team import Team
    bot = wbot()
    bot.team = Team(group_id=CHAT, users={})
    update = _msg(mk_update, "/new", user_id=999)

    run_async(new_flow.start_wizard(bot, update, MagicMock()))

    assert CHAT not in {c for c, _u in getattr(bot, "_new_wizard_pending", {})}


def test_a_muted_chat_seeds_no_card(wbot, mk_update, run_async):
    from aipager.bot.transport import MUTED
    bot = wbot()
    update = _msg(mk_update, "/new")
    update.message.reply_text = AsyncMock(return_value=MUTED)

    run_async(new_flow.start_wizard(bot, update, MagicMock()))

    assert CHAT not in {c for c, _u in getattr(bot, "_new_wizard_pending", {})}


# ---- the name, answered ------------------------------------------------

def test_a_valid_name_creates_the_session_at_once(wbot, mk_update, run_async):
    bot = wbot()
    _open_card(bot, mk_update, run_async)
    update = _msg(mk_update, "dev")

    handled = run_async(new_flow.maybe_handle_text(bot, update, MagicMock(), "dev"))

    assert handled is True
    assert bot.created == [{"label": "dev", "scope": CHAT, "skip_perms": True,
                            "cwd": None, "model": None, "driver": OWNER}]
    assert CHAT not in {c for c, _u in bot._new_wizard_pending}
    ready = _last_edit(bot)
    assert ready["message_id"] == 900           # the Name card became it
    assert "✅ <b>dev</b> is ready" in ready["text"]
    assert "✍️ Just send a message, it goes to dev." in ready["text"]


def test_a_name_with_a_first_message_queues_it(wbot, mk_update, run_async):
    bot = wbot()
    _open_card(bot, mk_update, run_async)
    update = _msg(mk_update, "dev fix the tests\nplease")

    run_async(new_flow.maybe_handle_text(bot, update, MagicMock(),
                                         "dev fix the tests\nplease"))

    sess = bot.registry.get("claude-dev")
    assert [q[0] for q in sess.pending_queue] == ["fix the tests - please"]
    assert "▶️ Working on your first message." in _last_edit(bot)["text"]


def test_an_invalid_name_re_renders_the_card_with_the_reason(wbot, mk_update, run_async):
    bot = wbot()
    _open_card(bot, mk_update, run_async)
    update = _msg(mk_update, "b@d")

    run_async(new_flow.maybe_handle_text(bot, update, MagicMock(), "b@d"))

    assert bot.created == []
    pending = bot._new_wizard_pending[(CHAT, OWNER)]
    assert pending["step"] == "name"
    kwargs = _last_edit(bot)
    assert kwargs["message_id"] == 900
    assert "letters" in kwargs["text"].lower()


def test_a_reserved_name_is_refused(wbot, mk_update, run_async):
    bot = wbot()
    _open_card(bot, mk_update, run_async)
    run_async(new_flow.maybe_handle_text(bot, _msg(mk_update, "resume"),
                                         MagicMock(), "resume"))

    assert bot.created == []
    assert "reserved" in _last_edit(bot)["text"].lower()


def test_a_live_name_gets_the_conflict_card(wbot, mk_update, run_async):
    registry = SessionRegistry()
    registry._sessions["claude-jim"] = TrackedSession(
        name="claude-jim", label="jim", status=Status.IDLE, scope_chat_id=CHAT)
    bot = wbot(registry)
    bot._send_new_conflict_prompt = AsyncMock()
    _open_card(bot, mk_update, run_async)

    run_async(new_flow.maybe_handle_text(bot, _msg(mk_update, "jim do it"),
                                         MagicMock(), "jim do it"))

    assert bot.created == []
    kw = bot._send_new_conflict_prompt.await_args.kwargs
    assert (kw["existing"].name, kw["prompt"], kw["skip_perms"]) == (
        "claude-jim", "do it", True)
    assert CHAT not in {c for c, _u in bot._new_wizard_pending}
    # The Name card itself becomes the conflict card: one message.
    assert kw["edit_msg_id"] == 900


def test_a_gone_name_with_no_transcript_is_reused(wbot, mk_update, run_async):
    registry = SessionRegistry()
    registry._sessions["claude-jim"] = TrackedSession(
        name="claude-jim", label="jim", status=Status.GONE, scope_chat_id=CHAT)
    bot = wbot(registry)
    bot._send_new_conflict_prompt = AsyncMock()
    _open_card(bot, mk_update, run_async)

    run_async(new_flow.maybe_handle_text(bot, _msg(mk_update, "jim"), MagicMock(), "jim"))

    bot._send_new_conflict_prompt.assert_not_awaited()
    assert [c["label"] for c in bot.created] == ["jim"]


def test_a_gone_resumable_name_gets_the_conflict_card(wbot, mk_update, run_async):
    registry = SessionRegistry()
    registry._sessions["claude-jim"] = TrackedSession(
        name="claude-jim", label="jim", status=Status.GONE,
        claude_session_id="abc", scope_chat_id=CHAT)
    bot = wbot(registry)
    bot._send_new_conflict_prompt = AsyncMock()
    _open_card(bot, mk_update, run_async)

    run_async(new_flow.maybe_handle_text(bot, _msg(mk_update, "jim"), MagicMock(), "jim"))

    bot._send_new_conflict_prompt.assert_awaited_once()
    assert bot.created == []


def test_a_strangers_text_is_not_taken_as_the_name(wbot, mk_update, run_async):
    bot = wbot()
    _open_card(bot, mk_update, run_async)

    handled = run_async(new_flow.maybe_handle_text(
        bot, _msg(mk_update, "hello", user_id=222), MagicMock(), "hello"))

    assert handled is False
    assert bot.created == []
    assert (CHAT, OWNER) in bot._new_wizard_pending


def test_text_with_no_open_card_is_not_taken(wbot, mk_update, run_async):
    bot = wbot()
    assert run_async(new_flow.maybe_handle_text(
        bot, _msg(mk_update, "dev"), MagicMock(), "dev")) is False


def test_a_name_typed_while_a_picker_is_open_still_creates(wbot, mk_update, run_async, mk_cb):
    bot = wbot()
    _open_card(bot, mk_update, run_async)
    update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:model"))

    run_async(new_flow.maybe_handle_text(bot, _msg(mk_update, "dev"), MagicMock(), "dev"))

    assert [c["label"] for c in bot.created] == ["dev"]


def test_a_failed_launch_brings_the_card_back_with_its_choices(wbot, mk_update, run_async):
    bot = wbot(launch_error="dtach unavailable")
    _open_card(bot, mk_update, run_async)
    bot._new_wizard_pending[(CHAT, OWNER)]["skip_perms"] = False

    run_async(new_flow.maybe_handle_text(bot, _msg(mk_update, "dev"), MagicMock(), "dev"))

    pending = bot._new_wizard_pending.get((CHAT, OWNER))
    assert pending is not None, "the Name card must come back"
    assert (pending["step"], pending["skip_perms"], pending["msg_id"]) == (
        "name", False, 900)
    assert "dtach unavailable" in _last_edit(bot)["text"]


def test_a_failed_launch_leaves_a_newer_card_alone(wbot, mk_update, run_async):
    """A second /new opened while the first card's session was launching:
    the failure must not put the old card back over the new one."""
    bot = wbot()
    _open_card(bot, mk_update, run_async)
    newer = {"step": "name", "user_id": OWNER, "msg_id": 901,
             "last_active": new_flow._now()}

    async def _fail(label, **kw):
        bot._new_wizard_pending[(CHAT, OWNER)] = newer
        return "", "dtach unavailable"
    bot.create_session = _fail

    run_async(new_flow.maybe_handle_text(bot, _msg(mk_update, "dev"), MagicMock(), "dev"))

    assert bot._new_wizard_pending[(CHAT, OWNER)] is newer


def test_auto_is_refused_at_creation_for_a_non_admin(wbot, mk_update, run_async):
    """The card's state says Auto (a stale card, a demotion): creation
    re-checks the person creating and gives Ask, saying why."""
    bot = wbot(admin=False)
    _open_card(bot, mk_update, run_async)
    bot._new_wizard_pending[(CHAT, OWNER)]["skip_perms"] = True

    run_async(new_flow.maybe_handle_text(bot, _msg(mk_update, "dev"), MagicMock(), "dev"))

    assert bot.created[0]["skip_perms"] is False
    assert "Auto mode needs an admin" in _last_edit(bot)["text"]


# ---- the Name card's buttons --------------------------------------------

def test_the_mode_toggle_switches_to_ask_and_back(wbot, mk_update, run_async, mk_cb):
    bot = wbot()
    _open_card(bot, mk_update, run_async)
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:mode:ask"))
    assert bot._new_wizard_pending[(CHAT, OWNER)]["skip_perms"] is False
    assert "💬 Ask" in _last_edit(bot)["text"]

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:mode:auto"))
    assert bot._new_wizard_pending[(CHAT, OWNER)]["skip_perms"] is True


def test_the_mode_toggle_refuses_auto_for_a_non_admin(wbot, mk_update, run_async, mk_cb):
    bot = wbot(admin=False)
    _open_card(bot, mk_update, run_async)
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:mode:auto"))

    assert bot._new_wizard_pending[(CHAT, OWNER)]["skip_perms"] is False
    assert query.answer.await_args.kwargs.get("show_alert") is True


def test_picking_a_model_returns_to_the_name_card(wbot, mk_update, run_async, mk_cb):
    bot = wbot()
    _open_card(bot, mk_update, run_async)
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:model"))
    kb = _last_edit(bot)["reply_markup"]
    assert _buttons(kb)[:len(MODEL_CHOICES)] == [lbl for lbl, _ in MODEL_CHOICES]

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:model:0"))
    pending = bot._new_wizard_pending[(CHAT, OWNER)]
    assert pending["step"] == "name"
    assert pending["model_label"] == MODEL_CHOICES[0][0]
    assert MODEL_CHOICES[0][0] in _last_edit(bot)["text"]


def test_default_model_clears_the_choice(wbot, mk_update, run_async, mk_cb):
    bot = wbot()
    _open_card(bot, mk_update, run_async)
    update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:model:0"))
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:model:default"))

    assert bot._new_wizard_pending[(CHAT, OWNER)]["model"] is None


def test_an_out_of_range_model_reopens_the_list(wbot, mk_update, run_async, mk_cb):
    bot = wbot()
    _open_card(bot, mk_update, run_async)
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:model:99"))

    assert bot._new_wizard_pending[(CHAT, OWNER)]["step"] == "opt_model"
    assert "no longer offered" in query.answer.await_args.args[0]


def test_a_custom_model_is_typed_then_used(wbot, mk_update, run_async, mk_cb):
    bot = wbot()
    _open_card(bot, mk_update, run_async)
    update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:model:custom"))
    assert bot._new_wizard_pending[(CHAT, OWNER)]["step"] == "opt_model_custom"

    run_async(new_flow.maybe_handle_text(bot, _msg(mk_update, "claude-opus-5"),
                                         MagicMock(), "claude-opus-5"))

    pending = bot._new_wizard_pending[(CHAT, OWNER)]
    assert (pending["step"], pending["model"]) == ("name", "claude-opus-5")
    assert bot.created == []                   # a model, not a name


def test_an_invalid_custom_model_asks_again(wbot, mk_update, run_async, mk_cb):
    bot = wbot()
    _open_card(bot, mk_update, run_async)
    update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:model:custom"))

    run_async(new_flow.maybe_handle_text(bot, _msg(mk_update, "bad model!"),
                                         MagicMock(), "bad model!"))

    assert bot._new_wizard_pending[(CHAT, OWNER)]["step"] == "opt_model_custom"


def test_picking_a_folder_sets_it(wbot, mk_update, run_async, mk_cb, tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "allowed_roots", lambda reg, chat, **_kw: [str(tmp_path)])
    bot = wbot()
    _open_card(bot, mk_update, run_async)
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:path"))
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:path:0"))

    assert bot._new_wizard_pending[(CHAT, OWNER)]["cwd"] == str(tmp_path)
    run_async(new_flow.maybe_handle_text(bot, _msg(mk_update, "dev"), MagicMock(), "dev"))
    assert bot.created[0]["cwd"] == str(tmp_path)


def test_a_new_folder_is_created_and_used(wbot, mk_update, run_async, mk_cb, tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "allowed_roots", lambda reg, chat, **_kw: [str(tmp_path)])
    bot = wbot()
    _open_card(bot, mk_update, run_async)
    update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:path:new"))

    run_async(new_flow.maybe_handle_text(bot, _msg(mk_update, "proj"), MagicMock(), "proj"))

    assert (tmp_path / "proj").is_dir()
    assert bot._new_wizard_pending[(CHAT, OWNER)]["cwd"] == str(tmp_path / "proj")


def test_new_folder_with_no_roots_toasts(wbot, mk_update, run_async, mk_cb, monkeypatch):
    monkeypatch.setattr(launch, "allowed_roots", lambda reg, chat, **_kw: [])
    bot = wbot()
    _open_card(bot, mk_update, run_async)
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:path:new"))

    assert bot._new_wizard_pending[(CHAT, OWNER)]["step"] == "name"
    assert query.answer.await_args.kwargs.get("show_alert") is True


def test_an_invalid_new_folder_name_asks_again_and_creates_nothing(
        wbot, mk_update, run_async, mk_cb, tmp_path, monkeypatch):
    """A bad folder name re-asks in place: nothing is created, and the text
    is never taken as a session name."""
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(launch, "allowed_roots", lambda reg, chat, **_kw: [str(root)])
    bot = wbot()
    _open_card(bot, mk_update, run_async)
    update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:path:new"))

    run_async(new_flow.maybe_handle_text(bot, _msg(mk_update, "../x"), MagicMock(), "../x"))

    assert bot._new_wizard_pending[(CHAT, OWNER)]["step"] == "opt_path_newfolder"
    assert bot.created == []
    assert list(root.iterdir()) == [] and not (tmp_path / "x").exists()
    assert "📁" in _last_edit(bot)["text"]


def test_an_out_of_range_folder_reopens_a_fresh_list(
        wbot, mk_update, run_async, mk_cb, tmp_path, monkeypatch):
    roots = [str(tmp_path / "a")]
    monkeypatch.setattr(launch, "allowed_roots", lambda reg, chat, **_kw: list(roots))
    bot = wbot()
    _open_card(bot, mk_update, run_async)
    update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:path"))

    roots.append(str(tmp_path / "b"))
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:path:5"))

    pending = bot._new_wizard_pending[(CHAT, OWNER)]
    assert pending["cwd"] is None
    assert pending["step"] == "opt_path"
    assert "no longer available" in query.answer.await_args.args[0]
    assert pending["path_options"] == roots      # read again, not reused


def test_a_folder_index_resolves_against_the_list_that_was_shown(
        wbot, mk_update, run_async, mk_cb, tmp_path, monkeypatch):
    """Roots change between two openings of the picker: the tap resolves
    against the latest list shown, not the first."""
    roots = [str(tmp_path / "old")]
    monkeypatch.setattr(launch, "allowed_roots", lambda reg, chat, **_kw: list(roots))
    bot = wbot()
    _open_card(bot, mk_update, run_async)
    update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:path"))
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:back"))

    roots[:] = [str(tmp_path / "new")]
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:path"))
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:path:0"))

    assert bot._new_wizard_pending[(CHAT, OWNER)]["cwd"] == str(tmp_path / "new")


def test_short_path_keeps_the_end_of_a_long_path():
    assert new_flow._short_path("/srv/a") == "/srv/a"
    long = "/home/someone/" + "x" * 60 + "/project"
    short = new_flow._short_path(long)
    assert len(short) == 40 and short.startswith("…") and short.endswith("/project")


def test_cancel_clears_the_card(wbot, mk_update, run_async, mk_cb):
    bot = wbot()
    _open_card(bot, mk_update, run_async)
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:cancel"))

    assert CHAT not in {c for c, _u in bot._new_wizard_pending}
    assert "Cancelled" in _last_edit(bot)["text"]


def test_a_strangers_tap_is_refused(wbot, mk_update, run_async, mk_cb):
    bot = wbot()
    _open_card(bot, mk_update, run_async)
    update, query = mk_cb(user_id=222)

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:mode:ask"))

    assert bot._new_wizard_pending[(CHAT, OWNER)]["skip_perms"] is True
    assert query.answer.await_args.kwargs.get("show_alert") is True


def test_a_tap_with_no_open_card_says_expired(wbot, mk_cb, run_async):
    bot = wbot()
    update, query = mk_cb()

    assert run_async(new_flow.handle_callback(bot, update, query, "_", "nw:cancel")) is True
    assert "expired" in query.answer.await_args.args[0].lower()


def test_the_card_expires_after_its_ttl(wbot, mk_update, run_async, monkeypatch):
    bot = wbot()
    _open_card(bot, mk_update, run_async)
    bot._new_wizard_pending[(CHAT, OWNER)]["last_active"] -= new_flow._WIZARD_TTL_SECONDS + 1

    handled = run_async(new_flow.maybe_handle_text(bot, _msg(mk_update, "dev"),
                                                   MagicMock(), "dev"))

    assert handled is True
    assert bot.created == []
    assert CHAT not in {c for c, _u in bot._new_wizard_pending}
    assert "expired" in _last_edit(bot)["text"]


def test_foreign_callbacks_are_not_this_modules(wbot, mk_cb, run_async):
    bot = wbot()
    update, query = mk_cb()
    assert run_async(new_flow.handle_callback(bot, update, query, "_", "set:layout")) is False
    assert run_async(new_flow.handle_callback(bot, update, query, "claude-x", "stop")) is False


# ---- the one parser -------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("x1", ("x1", "", False, "")),
    ("X1 fix it", ("x1", "fix it", False, "")),
    ("!x1", ("x1", "", True, "")),
    ("x1   two\nlines", ("x1", "two - lines", False, "")),
])
def test_parse_request(text, expected):
    assert new_flow.parse_request(text) == expected


def test_parse_request_rejects_an_empty_name():
    assert new_flow.parse_request("!")[3]


def test_direct_argument_with_a_bad_name_opens_the_card_with_the_reason(
        wbot, mk_update, run_async):
    bot = wbot()
    update = _msg(mk_update, "/new b@d")

    run_async(new_flow.create_from_text(bot, update, "b@d"))

    assert bot.created == []
    text = update.message.reply_text.await_args.args[0]
    assert "letters" in text.lower() and "Send a name" in text
    assert bot._new_wizard_pending[(CHAT, OWNER)]["step"] == "name"


# ---- the Ready card and its buttons ----------------------------------------

def _ready_session(bot, *, skip_perms=True, status=Status.IDLE):
    sess = bot.registry.get_or_create("claude-dev")
    sess.label = "dev"
    sess.skip_perms = skip_perms
    sess.scope_chat_id = CHAT
    bot.registry.transition("claude-dev", status)
    return sess


def test_the_ready_card_says_where_messages_go(wbot, mk_update):
    bot = wbot()
    sess = _ready_session(bot)
    text, kb = new_flow.render_ready(bot, _msg(mk_update, "x"), sess)

    assert text.startswith("✅ <b>dev</b> is ready\n🤖 Auto · 🧠 Default model · 📁 ")
    assert "✍️ Just send a message, it goes to dev." in text
    assert "Later: tap dev on the keyboard, or reply to any dev message." in text
    assert _buttons(kb) == ["💬 Switch to Ask", "🧠 Model"]
    assert "—" not in text


def test_the_ready_card_offers_auto_when_asking(wbot, mk_update):
    bot = wbot()
    sess = _ready_session(bot, skip_perms=False)
    _text, kb = new_flow.render_ready(bot, _msg(mk_update, "x"), sess)
    assert _buttons(kb)[0] == "🤖 Switch to Auto"


def _switch_core(bot):
    async def _core(sess, target):
        sess.skip_perms = target
        return RestartOutcome(ok=True, reason="done", label=sess.label,
                              skip_perms=target)
    bot._perms_switch_core = AsyncMock(side_effect=_core)


def test_switch_to_ask_relaunches_and_re_renders(wbot, mk_cb, run_async):
    bot = wbot()
    sess = _ready_session(bot)
    _switch_core(bot)
    update, query = mk_cb()

    assert run_async(new_flow.handle_callback(bot, update, query, sess.name, "rdy_ask"))

    bot._perms_switch_core.assert_awaited_once_with(sess, False)
    assert "💬 Ask" in _last_edit(bot)["text"]
    assert _buttons(_last_edit(bot)["reply_markup"])[0] == "🤖 Switch to Auto"


def test_switch_to_auto_needs_an_admin(wbot, mk_cb, run_async):
    bot = wbot(admin=False)
    sess = _ready_session(bot, skip_perms=False)
    _switch_core(bot)
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, sess.name, "rdy_auto"))

    bot._perms_switch_core.assert_not_awaited()
    assert query.answer.await_args.kwargs.get("show_alert") is True


def test_switching_mode_is_refused_while_the_session_works(wbot, mk_cb, run_async):
    bot = wbot()
    sess = _ready_session(bot, status=Status.BUSY)
    _switch_core(bot)
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, sess.name, "rdy_ask"))

    bot._perms_switch_core.assert_not_awaited()
    assert "working" in query.answer.await_args.args[0]


def test_a_tap_from_before_the_running_turn_is_refused(wbot, mk_cb, run_async):
    """The card is older than the running turn's busy card (the status has
    not caught up yet): the switch would kill that turn, so it refuses."""
    bot = wbot()
    sess = _ready_session(bot)
    sess.busy_msg_id = 1000
    _switch_core(bot)
    update, query = mk_cb(message_id=900)

    run_async(new_flow.handle_callback(bot, update, query, sess.name, "rdy_ask"))

    bot._perms_switch_core.assert_not_awaited()
    assert "working" in query.answer.await_args.args[0]


def test_a_turn_that_starts_during_the_toast_is_left_alone(wbot, mk_cb, run_async):
    """Idle when tapped, working by the time the toast went out: the
    switch is dropped and the card says why."""
    bot = wbot()
    sess = _ready_session(bot)
    _switch_core(bot)
    update, query = mk_cb()

    async def _turn_starts(*_a, **_k):
        sess.status = Status.BUSY
    query.answer = AsyncMock(side_effect=_turn_starts)

    run_async(new_flow.handle_callback(bot, update, query, sess.name, "rdy_ask"))

    bot._perms_switch_core.assert_not_awaited()
    assert "started working" in _last_edit(bot)["text"]


def test_the_model_button_lists_models_and_switches(wbot, mk_cb, run_async):
    bot = wbot()
    sess = _ready_session(bot)
    bot._switch_model_core = AsyncMock(return_value=ModelSwitchOutcome(
        ok=True, reason="sent", label="dev"))
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, sess.name, "rdy_model"))
    listed = _buttons(_last_edit(bot)["reply_markup"])
    assert listed[:len(MODEL_CHOICES)] == [lbl for lbl, _ in MODEL_CHOICES]

    run_async(new_flow.handle_callback(bot, update, query, sess.name, "rdy_m0"))
    resolved, _err = launch.validate_model(MODEL_CHOICES[0][0], MODEL_CHOICES)
    assert bot._switch_model_core.await_args.args == (sess, resolved)
    assert "is ready" in _last_edit(bot)["text"]


def test_the_ready_card_shows_the_model_just_picked(wbot, mk_cb, run_async):
    """The statusline still reports the old model: the card shows the
    one just switched to, not the stale one."""
    bot = wbot()
    sess = _ready_session(bot)
    sess.model_name = "Old Model 1"
    bot._switch_model_core = AsyncMock(return_value=ModelSwitchOutcome(
        ok=True, reason="sent", label="dev"))
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, sess.name, "rdy_m1"))

    text = _last_edit(bot)["text"]
    assert f"🧠 {MODEL_CHOICES[1][0]}" in text and "Old Model 1" not in text, text


def test_the_ready_card_falls_back_to_the_launch_model(wbot, mk_update):
    """Before the first statusline (a mode switch right after /new), the
    card shows the model the session was started with."""
    bot = wbot()
    sess = _ready_session(bot)
    sess.launch_model, _err = launch.validate_model(MODEL_CHOICES[2][0], MODEL_CHOICES)

    text, _kb = new_flow.render_ready(bot, _msg(mk_update, "x"), sess)

    assert f"🧠 {MODEL_CHOICES[2][0]} ·" in text, text


def test_a_pending_model_switch_beats_the_stale_statusline(wbot, mk_update, monkeypatch):
    bot = wbot()
    sess = _ready_session(bot)
    sess.model_name = "Old Model 1"
    sess.launch_model, _err = launch.validate_model(MODEL_CHOICES[1][0], MODEL_CHOICES)
    monkeypatch.setattr(type(sess), "model_switch_pending", lambda self: True)

    text, _kb = new_flow.render_ready(bot, _msg(mk_update, "x"), sess)

    assert f"🧠 {MODEL_CHOICES[1][0]} ·" in text, text


def test_a_refused_model_switch_says_why(wbot, mk_cb, run_async):
    bot = wbot()
    sess = _ready_session(bot)
    bot._switch_model_core = AsyncMock(return_value=ModelSwitchOutcome(
        ok=False, reason="busy", label="dev", detail="dev is working"))
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, sess.name, "rdy_m0"))

    assert query.answer.await_args.args[0] == "dev is working"


def test_the_ready_card_refuses_an_ended_session(wbot, mk_cb, run_async):
    bot = wbot()
    sess = _ready_session(bot, status=Status.GONE)
    _switch_core(bot)
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, sess.name, "rdy_ask"))

    bot._perms_switch_core.assert_not_awaited()
    assert "ended" in query.answer.await_args.args[0]


# ---- the chat's defaults ------------------------------------------------------

def test_defaults_are_auto_for_an_admin_and_ask_otherwise(wbot):
    assert new_flow.resolve_new_session_settings(wbot(), CHAT, OWNER)["skip_perms"] is True
    assert new_flow.resolve_new_session_settings(
        wbot(admin=False), CHAT, OWNER)["skip_perms"] is False


def test_a_stored_model_is_used_and_a_withdrawn_one_ignored(wbot):
    label = MODEL_CHOICES[0][0]
    preferences.set_new_session_default(CHAT, "model", label)
    assert new_flow.resolve_new_session_settings(wbot(), CHAT, OWNER)["model_label"] == label

    preferences.set_new_session_default(CHAT, "model", "not-a-model-we-offer")
    assert new_flow.resolve_new_session_settings(wbot(), CHAT, OWNER)["model"] is None


@pytest.mark.parametrize("label, model", sorted(config.RETIRED_MODEL_LABELS.items()))
def test_a_stored_model_taken_off_the_list_still_launches_it(wbot, label, model):
    # /settings stored the label; the pinned row is gone from the list since.
    assert label not in {lbl for lbl, _cmd in MODEL_CHOICES}
    preferences.set_new_session_default(CHAT, "model", label)
    got = new_flow.resolve_new_session_settings(wbot(), CHAT, OWNER)
    assert (got["model"], got["model_label"]) == (model, label)


def test_a_stored_folder_that_is_no_longer_allowed_falls_back(wbot, tmp_path, monkeypatch):
    preferences.set_new_session_default(CHAT, "cwd", str(tmp_path))
    monkeypatch.setattr(launch, "allowed_roots", lambda reg, chat, **_kw: [str(tmp_path)])
    assert new_flow.resolve_new_session_settings(wbot(), CHAT, OWNER)["cwd"] == str(tmp_path)

    monkeypatch.setattr(launch, "allowed_roots", lambda reg, chat, **_kw: ["/elsewhere"])
    assert new_flow.resolve_new_session_settings(wbot(), CHAT, OWNER)["cwd"] is None


def test_new_session_defaults_validate_and_clear():
    with pytest.raises(ValueError):
        preferences.set_new_session_default(CHAT, "mode", "sometimes")
    with pytest.raises(ValueError):
        preferences.set_new_session_default(CHAT, "cwd", "relative/path")
    with pytest.raises(ValueError):
        preferences.set_new_session_default(CHAT, "colour", "blue")
    preferences.set_new_session_default(CHAT, "mode", "ask")
    assert preferences.get_new_session_defaults(CHAT).mode == "ask"
    preferences.set_new_session_default(CHAT, "mode", "")
    assert preferences.get_new_session_defaults(CHAT).mode == ""


def test_settings_new_sessions_screen_sets_each_default(wbot, mk_cb, run_async, tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "allowed_roots", lambda reg, chat, **_kw: [str(tmp_path)])
    bot = wbot()
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, "_", "set:ns"))
    assert "New sessions" in _last_edit(bot)["text"]

    run_async(new_flow.handle_callback(bot, update, query, "_", "set:ns:mode:ask"))
    run_async(new_flow.handle_callback(bot, update, query, "_", "set:ns:model:0"))
    run_async(new_flow.handle_callback(bot, update, query, "_", "set:ns:cwd"))
    run_async(new_flow.handle_callback(bot, update, query, "_", "set:ns:cwd:0"))

    stored = preferences.get_new_session_defaults(CHAT)
    assert (stored.mode, stored.model, stored.cwd) == (
        "ask", MODEL_CHOICES[0][0], str(tmp_path))
    run_async(new_flow.handle_callback(bot, update, query, "_", "set:ns:mode:auto"))
    assert preferences.get_new_session_defaults(CHAT).mode == ""


def test_a_default_folder_tap_saves_the_folder_that_was_shown(
        wbot, mk_cb, run_async, tmp_path, monkeypatch):
    """The allowed folders move (a session starts somewhere new): a tap
    resolves against the list it was shown, not a fresh one."""
    a, b, c = (str(tmp_path / x) for x in "abc")
    roots = [a, b]
    monkeypatch.setattr(launch, "allowed_roots", lambda reg, chat, **_kw: list(roots))
    bot = wbot()
    update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, update, query, "_", "set:ns:cwd"))

    roots[:] = [c, a, b]
    run_async(new_flow.handle_callback(bot, update, query, "_", "set:ns:cwd:0"))

    assert preferences.get_new_session_defaults(CHAT).cwd == a


def test_a_default_folder_that_is_no_longer_allowed_is_refused(
        wbot, mk_cb, run_async, tmp_path, monkeypatch):
    roots = [str(tmp_path / "a")]
    monkeypatch.setattr(launch, "allowed_roots", lambda reg, chat, **_kw: list(roots))
    bot = wbot()
    update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, update, query, "_", "set:ns:cwd"))

    roots[:] = []
    run_async(new_flow.handle_callback(bot, update, query, "_", "set:ns:cwd:0"))

    assert preferences.get_new_session_defaults(CHAT).cwd == ""
    assert "no longer available" in query.answer.await_args.args[0]


def test_the_defaults_screen_tells_a_non_admin_they_get_ask(wbot, mk_cb, run_async):
    bot = wbot(admin=False)
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, "_", "set:ns"))

    assert "Mode: 💬 Ask for you" in _last_edit(bot)["text"]


def test_a_group_member_who_is_not_admin_cannot_change_defaults(wbot, mk_cb, run_async):
    bot = wbot()
    bot._is_admin = MagicMock(return_value=False)
    update, query = mk_cb(chat_id=-100)

    run_async(new_flow.handle_callback(bot, update, query, "_", "set:ns:mode:ask"))

    assert preferences.get_new_session_defaults(-100).mode == ""
    assert query.answer.await_args.kwargs.get("show_alert") is True


def test_a_stale_default_choice_is_refused(wbot, mk_cb, run_async):
    bot = wbot()
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, "_", "set:ns:model:99"))

    assert preferences.get_new_session_defaults(CHAT).model == ""
    assert "no longer available" in query.answer.await_args.args[0]


def test_the_ready_card_refuses_someone_who_may_not_prompt(wbot, mk_cb, run_async):
    bot = wbot()
    sess = _ready_session(bot)
    _switch_core(bot)
    bot._can_prompt_user = MagicMock(return_value=False)
    update, query = mk_cb(user_id=222)

    run_async(new_flow.handle_callback(bot, update, query, sess.name, "rdy_ask"))

    bot._perms_switch_core.assert_not_awaited()
    assert query.answer.await_args.kwargs.get("show_alert") is True
