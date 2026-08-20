"""Tests for the interactive `/new` wizard (aipager/bot/new_flow.py).

Exercises the three exported entry points directly — `start_wizard`,
`maybe_handle_text`, `handle_callback` — exactly as a black-box Tester
would per entrypoints.md, since the shared-file integration lines that
would make `/new` (no args) reachable through `_handle_callback`/
`_handle_message` are applied by the integrator in a different worktree,
not here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import new_flow
from aipager.dtach import inject
from aipager.miniapp import launch
from aipager.state import SessionRegistry, Status, TrackedSession

# ---- local fixtures (file-scoped, per project convention — see
# test_bot_callbacks_settings.py's own local `mk_query`) -----------------

@pytest.fixture
def wbot(mk_bot):
    """A `mk_bot()` bot pre-wired with the extra AsyncMocks new_flow.py
    needs (`bot._app.bot.edit_message_text` / `.edit_message_reply_markup`)
    — `mk_bot()` itself only wires `send_message`."""
    def _mk(registry=None, **kw):
        bot = mk_bot(registry, **kw)
        bot._app.bot.edit_message_text = AsyncMock()
        bot._app.bot.edit_message_reply_markup = AsyncMock()
        return bot
    return _mk


@pytest.fixture
def mk_cb():
    """Build a (update, query) pair for a callback-query test, mirroring
    test_bot_callbacks_settings.py's local `mk_query` fixture but callable
    with new_flow.handle_callback directly (no `_authorize_callback` gate
    to satisfy — that check happens in `_handle_callback`, above this
    module's own seam)."""
    def _mk(*, user_id=111, chat_id=555, message_id=42, chat_type="private"):
        query = MagicMock()
        query.answer = AsyncMock()
        query.message = MagicMock()
        query.message.message_id = message_id
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


def _reply_text_returning(message_id: int) -> AsyncMock:
    return AsyncMock(return_value=MagicMock(message_id=message_id))


async def _create_ok(*a, **kw):
    return True, ""


def _extract_texts(kb) -> list[str]:
    return [b.text for row in kb.inline_keyboard for b in row]


def _extract_cbs(kb) -> list[str]:
    return [b.callback_data for row in kb.inline_keyboard for b in row
            if b.callback_data is not None]


# ---- start_wizard ------------------------------------------------------

def test_start_wizard_sends_name_prompt_and_seeds_pending(wbot, mk_update, run_async):
    bot = wbot()
    update = mk_update("/new", message_id=1, chat_id=555, user_id=111)
    update.message.reply_text = _reply_text_returning(900)

    run_async(new_flow.start_wizard(bot, update, MagicMock()))

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert "called" in text.lower()
    kb = update.message.reply_text.await_args.kwargs["reply_markup"]
    assert "_:nw:cancel" in _extract_cbs(kb)

    pending = bot._new_wizard_pending[555]
    assert pending["step"] == "name"
    assert pending["msg_id"] == 900
    assert pending["user_id"] == 111


def test_second_new_replaces_first_cleanly(wbot, mk_update, run_async):
    bot = wbot()
    update1 = mk_update("/new", chat_id=555, user_id=111)
    update1.message.reply_text = _reply_text_returning(900)
    run_async(new_flow.start_wizard(bot, update1, MagicMock()))

    update2 = mk_update("/new", chat_id=555, user_id=111)
    update2.message.reply_text = _reply_text_returning(901)
    run_async(new_flow.start_wizard(bot, update2, MagicMock()))

    # The FIRST wizard message was stripped in place.
    bot._app.bot.edit_message_text.assert_awaited_once()
    kwargs = bot._app.bot.edit_message_text.await_args.kwargs
    assert kwargs["message_id"] == 900
    assert "started over" in kwargs["text"].lower()
    assert kwargs["reply_markup"] is None

    # Pending now points at the SECOND message, not the first.
    pending = bot._new_wizard_pending[555]
    assert pending["msg_id"] == 901


def test_start_wizard_unauthorized_sends_nothing(wbot, mk_update, run_async):
    from aipager.team import Team
    bot = wbot()
    bot.team = Team(group_id=555, users={})
    update = mk_update("/new", chat_id=555, user_id=999)
    update.message.reply_text = AsyncMock()

    run_async(new_flow.start_wizard(bot, update, MagicMock()))

    assert 555 not in getattr(bot, "_new_wizard_pending", {})


# ---- maybe_handle_text: name step --------------------------------------

def test_name_step_valid_name_advances_to_mode(wbot, mk_update, run_async):
    bot = wbot()
    update = mk_update("dev", chat_id=555, user_id=111)
    update.message.reply_text = _reply_text_returning(900)
    run_async(new_flow.start_wizard(bot, update, MagicMock()))

    handled = run_async(new_flow.maybe_handle_text(bot, update, MagicMock(), "dev"))

    assert handled is True
    pending = bot._new_wizard_pending[555]
    assert pending["step"] == "mode"
    assert pending["name"] == "dev"
    bot._app.bot.edit_message_text.assert_awaited_once()
    text = bot._app.bot.edit_message_text.await_args.kwargs["text"]
    assert "dev" in text
    assert "mode" in text.lower()


def test_name_step_rejects_invalid_characters_and_reprompts_in_place(
    wbot, mk_update, run_async,
):
    bot = wbot()
    update = mk_update("!!!bad", chat_id=555, user_id=111)
    update.message.reply_text = _reply_text_returning(900)
    run_async(new_flow.start_wizard(bot, update, MagicMock()))

    handled = run_async(new_flow.maybe_handle_text(bot, update, MagicMock(), "!!!bad"))

    assert handled is True
    pending = bot._new_wizard_pending[555]
    assert pending["step"] == "name"          # unchanged — still waiting
    assert pending["name"] is None
    kwargs = bot._app.bot.edit_message_text.await_args.kwargs
    assert "letters" in kwargs["text"].lower() or "invalid" in kwargs["text"].lower()
    assert kwargs["message_id"] == 900


def test_name_step_rejects_reserved_command_name(wbot, mk_update, run_async):
    bot = wbot()
    update = mk_update("status", chat_id=555, user_id=111)
    update.message.reply_text = _reply_text_returning(900)
    run_async(new_flow.start_wizard(bot, update, MagicMock()))

    run_async(new_flow.maybe_handle_text(bot, update, MagicMock(), "status"))

    pending = bot._new_wizard_pending[555]
    assert pending["step"] == "name"
    assert "reserved" in bot._app.bot.edit_message_text.await_args.kwargs["text"].lower()


def test_name_step_rejects_live_session_collision(wbot, mk_update, run_async):
    registry = SessionRegistry()
    registry._sessions["claude-jim"] = TrackedSession(
        name="claude-jim", label="jim", status=Status.IDLE)
    bot = wbot(registry)
    update = mk_update("jim", chat_id=555, user_id=111)
    update.message.reply_text = _reply_text_returning(900)
    run_async(new_flow.start_wizard(bot, update, MagicMock()))

    run_async(new_flow.maybe_handle_text(bot, update, MagicMock(), "jim"))

    pending = bot._new_wizard_pending[555]
    assert pending["step"] == "name"
    assert "already in use" in bot._app.bot.edit_message_text.await_args.kwargs["text"]


def test_name_step_allows_gone_session_with_no_resume_info(wbot, mk_update, run_async):
    """A GONE session with no claude_session_id is genuinely reusable —
    same rule `_handle_new_cmd` applies for its own conflict prompt."""
    registry = SessionRegistry()
    registry._sessions["claude-old"] = TrackedSession(
        name="claude-old", label="old", status=Status.GONE)
    bot = wbot(registry)
    update = mk_update("old", chat_id=555, user_id=111)
    update.message.reply_text = _reply_text_returning(900)
    run_async(new_flow.start_wizard(bot, update, MagicMock()))

    run_async(new_flow.maybe_handle_text(bot, update, MagicMock(), "old"))

    assert bot._new_wizard_pending[555]["step"] == "mode"


def test_name_step_rejects_gone_but_resumable_collision(wbot, mk_update, run_async):
    registry = SessionRegistry()
    registry._sessions["claude-old"] = TrackedSession(
        name="claude-old", label="old", status=Status.GONE,
        claude_session_id="abc-123",
    )
    bot = wbot(registry)
    update = mk_update("old", chat_id=555, user_id=111)
    update.message.reply_text = _reply_text_returning(900)
    run_async(new_flow.start_wizard(bot, update, MagicMock()))

    run_async(new_flow.maybe_handle_text(bot, update, MagicMock(), "old"))

    assert bot._new_wizard_pending[555]["step"] == "name"


# ---- maybe_handle_text: only intercepts the two/three text-capture steps

def test_maybe_handle_text_ignored_with_no_pending_wizard(wbot, mk_update, run_async):
    bot = wbot()
    update = mk_update("hello", chat_id=555, user_id=111)
    handled = run_async(new_flow.maybe_handle_text(bot, update, MagicMock(), "hello"))
    assert handled is False


def test_maybe_handle_text_ignored_at_callback_only_step(wbot, mk_update, run_async):
    """While sitting at `mode` (callback-only), free text must NOT be
    swallowed — normal session routing has to keep working (design.md
    Risks)."""
    bot = wbot()
    update = mk_update("dev", chat_id=555, user_id=111)
    update.message.reply_text = _reply_text_returning(900)
    run_async(new_flow.start_wizard(bot, update, MagicMock()))
    run_async(new_flow.maybe_handle_text(bot, update, MagicMock(), "dev"))
    assert bot._new_wizard_pending[555]["step"] == "mode"

    handled = run_async(
        new_flow.maybe_handle_text(bot, update, MagicMock(), "unrelated text"))

    assert handled is False
    assert bot._new_wizard_pending[555]["step"] == "mode"  # untouched


# ---- mode step -----------------------------------------------------------

def _to_mode(bot, mk_update, run_async, *, chat_id=555, user_id=111, name="dev"):
    update = mk_update(name, chat_id=chat_id, user_id=user_id)
    update.message.reply_text = _reply_text_returning(900)
    run_async(new_flow.start_wizard(bot, update, MagicMock()))
    run_async(new_flow.maybe_handle_text(bot, update, MagicMock(), name))
    return update


def test_mode_ask_advances_to_summary(wbot, mk_update, run_async, mk_cb):
    bot = wbot()
    _to_mode(bot, mk_update, run_async)
    update, query = mk_cb(chat_id=555, user_id=111)

    handled = run_async(new_flow.handle_callback(bot, update, query, "_", "nw:mode:ask"))

    assert handled is True
    pending = bot._new_wizard_pending[555]
    assert pending["step"] == "summary"
    assert pending["skip_perms"] is False
    text = bot._app.bot.edit_message_text.await_args.kwargs["text"]
    assert "Ask mode" in text
    assert "Confirm" in bot._app.bot.edit_message_text.await_args.kwargs["text"]


def test_mode_auto_blocked_for_non_admin(wbot, mk_update, run_async, mk_cb):
    bot = wbot()
    bot._is_admin_user = lambda uid, cid: False
    _to_mode(bot, mk_update, run_async)
    edit_calls_before = bot._app.bot.edit_message_text.await_count
    update, query = mk_cb(chat_id=555, user_id=111)

    handled = run_async(new_flow.handle_callback(bot, update, query, "_", "nw:mode:auto"))

    assert handled is True
    query.answer.assert_awaited()
    toast = query.answer.await_args
    assert "admin" in toast.args[0].lower()
    pending = bot._new_wizard_pending[555]
    assert pending["step"] == "mode"
    assert pending["skip_perms"] is None
    # No message edit — state didn't move.
    assert bot._app.bot.edit_message_text.await_count == edit_calls_before


def test_mode_auto_allowed_for_admin(wbot, mk_update, run_async, mk_cb):
    bot = wbot()
    bot._is_admin_user = lambda uid, cid: True
    _to_mode(bot, mk_update, run_async)
    update, query = mk_cb(chat_id=555, user_id=111)

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:mode:auto"))

    pending = bot._new_wizard_pending[555]
    assert pending["step"] == "summary"
    assert pending["skip_perms"] is True


# ---- summary / optional menu navigation ----------------------------------

def _to_summary(bot, mk_update, run_async, mk_cb, **kw):
    update = _to_mode(bot, mk_update, run_async, **kw)
    cb_update, query = mk_cb(chat_id=kw.get("chat_id", 555), user_id=kw.get("user_id", 111))
    run_async(new_flow.handle_callback(bot, cb_update, query, "_", "nw:mode:ask"))
    return update


def test_opt_menu_lists_model_path_and_pref_sections(wbot, mk_update, run_async, mk_cb):
    bot = wbot()
    _to_summary(bot, mk_update, run_async, mk_cb)
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt"))

    pending = bot._new_wizard_pending[555]
    assert pending["step"] == "opt_menu"
    kb = bot._app.bot.edit_message_text.await_args.kwargs["reply_markup"]
    cbs = _extract_cbs(kb)
    assert "_:nw:opt:model" in cbs
    assert "_:nw:opt:path" in cbs
    assert "_:nw:opt:pref:layout" in cbs
    assert "_:nw:opt:pref:formatting" in cbs
    assert "_:nw:opt:pref:length" in cbs
    assert "_:nw:opt:pref:level" in cbs
    assert "_:nw:summary" in cbs
    assert "_:nw:cancel" in cbs


def test_back_from_opt_menu_returns_to_summary(wbot, mk_update, run_async, mk_cb):
    bot = wbot()
    _to_summary(bot, mk_update, run_async, mk_cb)
    update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt"))
    assert bot._new_wizard_pending[555]["step"] == "opt_menu"

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:summary"))

    assert bot._new_wizard_pending[555]["step"] == "summary"


# ---- model submenu --------------------------------------------------------

@pytest.fixture
def two_models(monkeypatch):
    choices = [("Sonnet", "/model sonnet"), ("Opus", "/model opus")]
    monkeypatch.setattr(new_flow, "MODEL_CHOICES", choices)
    return choices


def test_model_submenu_lists_choices_from_model_choices(
    wbot, mk_update, run_async, mk_cb, two_models,
):
    bot = wbot()
    _to_summary(bot, mk_update, run_async, mk_cb)
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:model"))

    assert bot._new_wizard_pending[555]["step"] == "opt_model"
    kb = bot._app.bot.edit_message_text.await_args.kwargs["reply_markup"]
    texts = _extract_texts(kb)
    assert any("Sonnet" in t for t in texts)
    assert any("Opus" in t for t in texts)
    cbs = _extract_cbs(kb)
    assert "_:nw:model:0" in cbs
    assert "_:nw:model:1" in cbs
    assert "_:nw:model:default" in cbs
    assert "_:nw:model:custom" in cbs


def test_pick_model_by_index_sets_model_and_returns_to_summary(
    wbot, mk_update, run_async, mk_cb, two_models,
):
    bot = wbot()
    _to_summary(bot, mk_update, run_async, mk_cb)
    update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:model"))

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:model:1"))

    pending = bot._new_wizard_pending[555]
    assert pending["step"] == "summary"
    assert pending["model"] == "opus"
    assert pending["model_label"] == "Opus"
    assert "Opus" in bot._app.bot.edit_message_text.await_args.kwargs["text"]


def test_model_default_clears_choice(wbot, mk_update, run_async, mk_cb, two_models):
    bot = wbot()
    _to_summary(bot, mk_update, run_async, mk_cb)
    update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:model"))
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:model:0"))
    assert bot._new_wizard_pending[555]["model"] == "sonnet"

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:model"))
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:model:default"))

    pending = bot._new_wizard_pending[555]
    assert pending["model"] is None
    assert pending["model_label"] is None


def test_model_index_out_of_range_toasts_and_reopens_model_list(
    wbot, mk_update, run_async, mk_cb, two_models,
):
    bot = wbot()
    _to_summary(bot, mk_update, run_async, mk_cb)
    update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:model"))

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:model:99"))

    assert "no longer available" in query.answer.await_args.args[0].lower()
    assert bot._new_wizard_pending[555]["step"] == "opt_model"
    assert bot._new_wizard_pending[555]["model"] is None


def test_model_custom_text_capture_valid(wbot, mk_update, run_async, mk_cb, two_models):
    bot = wbot()
    update = _to_summary(bot, mk_update, run_async, mk_cb)
    cb_update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, cb_update, query, "_", "nw:opt:model"))
    run_async(new_flow.handle_callback(bot, cb_update, query, "_", "nw:model:custom"))
    assert bot._new_wizard_pending[555]["step"] == "opt_model_custom"

    handled = run_async(
        new_flow.maybe_handle_text(bot, update, MagicMock(), "claude-opus-5"))

    assert handled is True
    pending = bot._new_wizard_pending[555]
    assert pending["step"] == "summary"
    assert pending["model"] == "claude-opus-5"


def test_model_custom_text_capture_invalid_reprompts(
    wbot, mk_update, run_async, mk_cb, two_models,
):
    bot = wbot()
    update = _to_summary(bot, mk_update, run_async, mk_cb)
    cb_update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, cb_update, query, "_", "nw:opt:model"))
    run_async(new_flow.handle_callback(bot, cb_update, query, "_", "nw:model:custom"))

    handled = run_async(
        new_flow.maybe_handle_text(bot, update, MagicMock(), "-not-valid"))

    assert handled is True
    pending = bot._new_wizard_pending[555]
    assert pending["step"] == "opt_model_custom"
    assert pending["model"] is None


# ---- path submenu ----------------------------------------------------------

def test_path_submenu_lists_allowed_roots_freshly(
    wbot, mk_update, run_async, mk_cb, monkeypatch,
):
    bot = wbot()
    _to_summary(bot, mk_update, run_async, mk_cb)
    update, query = mk_cb()
    calls = []

    def fake_roots(registry, chat_id):
        calls.append(chat_id)
        return ["/home/aly/proj-a", "/home/aly/proj-b"]

    monkeypatch.setattr(launch, "allowed_roots", fake_roots)

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:path"))

    assert calls == [555]
    pending = bot._new_wizard_pending[555]
    assert pending["step"] == "opt_path"
    assert pending["path_options"] == ["/home/aly/proj-a", "/home/aly/proj-b"]
    kb = bot._app.bot.edit_message_text.await_args.kwargs["reply_markup"]
    cbs = _extract_cbs(kb)
    assert "_:nw:path:0" in cbs
    assert "_:nw:path:1" in cbs
    assert "_:nw:path:default" in cbs
    assert "_:nw:path:new" in cbs

    # A SECOND open re-snapshots — never reuses a stale list.
    def fake_roots_2(registry, chat_id):
        return ["/only/one"]
    monkeypatch.setattr(launch, "allowed_roots", fake_roots_2)
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:path"))
    assert bot._new_wizard_pending[555]["path_options"] == ["/only/one"]


def test_pick_path_by_index_sets_cwd(wbot, mk_update, run_async, mk_cb, monkeypatch):
    bot = wbot()
    _to_summary(bot, mk_update, run_async, mk_cb)
    update, query = mk_cb()
    monkeypatch.setattr(
        launch, "allowed_roots", lambda registry, chat_id: ["/a", "/b"])
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:path"))

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:path:1"))

    pending = bot._new_wizard_pending[555]
    assert pending["step"] == "summary"
    assert pending["cwd"] == "/b"


def test_path_default_clears_cwd(wbot, mk_update, run_async, mk_cb, monkeypatch):
    bot = wbot()
    _to_summary(bot, mk_update, run_async, mk_cb)
    update, query = mk_cb()
    monkeypatch.setattr(launch, "allowed_roots", lambda registry, chat_id: ["/a"])
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:path"))
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:path:0"))
    assert bot._new_wizard_pending[555]["cwd"] == "/a"

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:path"))
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:path:default"))

    assert bot._new_wizard_pending[555]["cwd"] is None


def test_path_index_out_of_range_toasts_and_reopens(
    wbot, mk_update, run_async, mk_cb, monkeypatch,
):
    bot = wbot()
    _to_summary(bot, mk_update, run_async, mk_cb)
    update, query = mk_cb()
    monkeypatch.setattr(launch, "allowed_roots", lambda registry, chat_id: ["/a"])
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:path"))

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:path:5"))

    assert "no longer available" in query.answer.await_args.args[0].lower()
    assert bot._new_wizard_pending[555]["step"] == "opt_path"


def test_new_folder_with_no_roots_toasts_and_does_not_advance(
    wbot, mk_update, run_async, mk_cb, monkeypatch,
):
    bot = wbot()
    _to_summary(bot, mk_update, run_async, mk_cb)
    update, query = mk_cb()
    monkeypatch.setattr(launch, "allowed_roots", lambda registry, chat_id: [])
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:path"))

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:path:new"))

    assert query.answer.await_args.kwargs.get("show_alert") is True
    assert bot._new_wizard_pending[555]["step"] == "opt_path"


def test_new_folder_happy_path_creates_real_dir_under_tmp_root(
    wbot, mk_update, run_async, mk_cb, tmp_path, monkeypatch,
):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    monkeypatch.setattr(inject, "_PROJECT_DIR", str(project_dir))

    bot = wbot()
    update = _to_summary(bot, mk_update, run_async, mk_cb)
    cb_update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, cb_update, query, "_", "nw:opt:path"))
    run_async(new_flow.handle_callback(bot, cb_update, query, "_", "nw:path:new"))
    assert bot._new_wizard_pending[555]["step"] == "opt_path_newfolder"

    handled = run_async(new_flow.maybe_handle_text(bot, update, MagicMock(), "sub1"))

    assert handled is True
    pending = bot._new_wizard_pending[555]
    assert pending["step"] == "summary"
    assert pending["cwd"] == str(project_dir / "sub1")
    assert (project_dir / "sub1").is_dir()


def test_new_folder_invalid_name_reprompts_in_place(
    wbot, mk_update, run_async, mk_cb, tmp_path, monkeypatch,
):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    monkeypatch.setattr(inject, "_PROJECT_DIR", str(project_dir))

    bot = wbot()
    update = _to_summary(bot, mk_update, run_async, mk_cb)
    cb_update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, cb_update, query, "_", "nw:opt:path"))
    run_async(new_flow.handle_callback(bot, cb_update, query, "_", "nw:path:new"))

    handled = run_async(
        new_flow.maybe_handle_text(bot, update, MagicMock(), "../escape"))

    assert handled is True
    pending = bot._new_wizard_pending[555]
    assert pending["step"] == "opt_path_newfolder"
    assert pending["cwd"] is None
    assert not (project_dir / "escape").exists()


# ---- preference fields ------------------------------------------------

def test_pref_field_submenu_lists_options_with_markers(wbot, mk_update, run_async, mk_cb):
    bot = wbot()
    _to_summary(bot, mk_update, run_async, mk_cb)
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:pref:layout"))

    pending = bot._new_wizard_pending[555]
    assert pending["step"] == "opt_pref_field"
    assert pending["pref_section"] == "layout"
    kb = bot._app.bot.edit_message_text.await_args.kwargs["reply_markup"]
    cbs = _extract_cbs(kb)
    assert "_:nw:pref:layout:card" in cbs
    assert "_:nw:pref:layout:merged" in cbs
    assert "_:nw:pref:layout:replace" in cbs
    assert "_:nw:pref:layout:default" in cbs
    texts = _extract_texts(kb)
    assert any("✅" in t and "chat default" in t.lower() for t in texts)  # nothing set yet


def test_pref_field_unknown_section_toasts_invalid(wbot, mk_update, run_async, mk_cb):
    bot = wbot()
    _to_summary(bot, mk_update, run_async, mk_cb)
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:pref:bogus"))

    assert query.answer.await_args.args[0] == "Invalid callback"
    assert bot._new_wizard_pending[555]["step"] == "summary"  # unchanged


def test_pref_field_set_value_records_override(wbot, mk_update, run_async, mk_cb):
    bot = wbot()
    _to_summary(bot, mk_update, run_async, mk_cb)
    update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:pref:formatting"))

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:pref:formatting:on"))

    pending = bot._new_wizard_pending[555]
    assert pending["step"] == "summary"
    assert pending["prefs"]["simple_formatting"] is True
    text = bot._app.bot.edit_message_text.await_args.kwargs["text"]
    assert "Simple formatting" in text or "formatting" in text.lower()


def test_pref_field_use_chat_default_clears_override(wbot, mk_update, run_async, mk_cb):
    bot = wbot()
    _to_summary(bot, mk_update, run_async, mk_cb)
    update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:pref:length"))
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:pref:length:short"))
    assert bot._new_wizard_pending[555]["prefs"]["answer_length"] == "short"

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:pref:length"))
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:pref:length:default"))

    assert "answer_length" not in bot._new_wizard_pending[555]["prefs"]


def test_pref_field_invalid_value_toasts_and_does_not_mutate(wbot, mk_update, run_async, mk_cb):
    bot = wbot()
    _to_summary(bot, mk_update, run_async, mk_cb)
    update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:pref:level"))

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:pref:level:sideways"))

    assert query.answer.await_args.args[0] == "Invalid value"
    assert "language_level" not in bot._new_wizard_pending[555]["prefs"]


# ---- cancel --------------------------------------------------------------

def test_cancel_from_summary_clears_pending_and_edits_message(
    wbot, mk_update, run_async, mk_cb,
):
    bot = wbot()
    _to_summary(bot, mk_update, run_async, mk_cb)
    update, query = mk_cb()

    handled = run_async(new_flow.handle_callback(bot, update, query, "_", "nw:cancel"))

    assert handled is True
    assert 555 not in bot._new_wizard_pending
    kwargs = bot._app.bot.edit_message_text.await_args.kwargs
    assert "Cancelled" in kwargs["text"]
    assert kwargs["reply_markup"] is None


# ---- callback routing: only claims its own namespace ---------------------

def test_handle_callback_ignores_non_wizard_callbacks(wbot, mk_update, run_async, mk_cb):
    bot = wbot()
    update, query = mk_cb()
    handled = run_async(
        new_flow.handle_callback(bot, update, query, "claude-dev", "kill"))
    assert handled is False
    handled2 = run_async(
        new_flow.handle_callback(bot, update, query, "_", "set:layout"))
    assert handled2 is False


# ---- stale taps: no pending state (daemon restart) / expired -----------

def test_callback_with_no_pending_state_shows_expired(wbot, mk_update, run_async, mk_cb):
    bot = wbot()
    update, query = mk_cb(chat_id=555, message_id=123)

    handled = run_async(new_flow.handle_callback(bot, update, query, "_", "nw:confirm"))

    assert handled is True
    assert "expired" in query.answer.await_args.args[0].lower()
    kwargs = bot._app.bot.edit_message_text.await_args.kwargs
    assert kwargs["message_id"] == 123
    assert "expired" in kwargs["text"].lower()
    assert kwargs["reply_markup"] is None


def test_wizard_expires_after_ttl_on_text(wbot, mk_update, run_async):
    bot = wbot()
    update = mk_update("dev", chat_id=555, user_id=111)
    update.message.reply_text = _reply_text_returning(900)
    run_async(new_flow.start_wizard(bot, update, MagicMock()))

    bot._new_wizard_pending[555]["last_active"] -= (new_flow._WIZARD_TTL_SECONDS + 1)

    handled = run_async(new_flow.maybe_handle_text(bot, update, MagicMock(), "dev"))

    assert handled is True
    assert 555 not in bot._new_wizard_pending
    text = bot._app.bot.edit_message_text.await_args.kwargs["text"]
    assert "expired" in text.lower()


def test_wizard_expires_after_ttl_on_callback(wbot, mk_update, run_async, mk_cb):
    bot = wbot()
    _to_summary(bot, mk_update, run_async, mk_cb)
    bot._new_wizard_pending[555]["last_active"] -= (new_flow._WIZARD_TTL_SECONDS + 1)
    update, query = mk_cb()

    handled = run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt"))

    assert handled is True
    assert 555 not in bot._new_wizard_pending
    assert "expired" in query.answer.await_args.args[0].lower()
    kwargs = bot._app.bot.edit_message_text.await_args.kwargs
    assert "expired" in kwargs["text"].lower()
    assert kwargs["reply_markup"] is None


# ---- confirm: happy path -------------------------------------------------

def test_confirm_creates_session_ask_mode(wbot, mk_update, run_async, mk_cb, monkeypatch):
    monkeypatch.setattr(inject, "launch_session", _create_ok)
    registry = SessionRegistry()
    bot = wbot(registry)
    _to_summary(bot, mk_update, run_async, mk_cb, chat_id=555, user_id=111)
    update, query = mk_cb(chat_id=555, user_id=111, chat_type="private")

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:confirm"))

    assert 555 not in bot._new_wizard_pending
    kwargs = bot._app.bot.edit_message_text.await_args.kwargs
    assert "created" in kwargs["text"]
    assert "Ask mode" in kwargs["text"]
    sess = registry.find_by_label("dev", 555)
    assert sess is not None
    assert sess.skip_perms is False


def test_confirm_applies_preference_overrides(wbot, mk_update, run_async, mk_cb, monkeypatch):
    monkeypatch.setattr(inject, "launch_session", _create_ok)
    registry = SessionRegistry()
    bot = wbot(registry)
    _to_summary(bot, mk_update, run_async, mk_cb)
    update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:opt:pref:layout"))
    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:pref:layout:merged"))

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:confirm"))

    sess = registry.find_by_label("dev", 555)
    assert sess.override_layout == "merged"


def test_confirm_adds_miniapp_button_only_in_private_chat_with_url(
    wbot, mk_update, run_async, mk_cb, monkeypatch,
):
    monkeypatch.setattr(inject, "launch_session", _create_ok)
    registry = SessionRegistry()
    bot = wbot(registry)
    bot._miniapp_url = "https://example.aipager.run/app"
    _to_summary(bot, mk_update, run_async, mk_cb)
    update, query = mk_cb(chat_type="private")

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:confirm"))

    kb = bot._app.bot.edit_message_text.await_args.kwargs["reply_markup"]
    assert kb is not None
    assert any("Mini App" in b.text for row in kb.inline_keyboard for b in row)


def test_confirm_no_miniapp_button_in_group_chat(
    wbot, mk_update, run_async, mk_cb, monkeypatch,
):
    monkeypatch.setattr(inject, "launch_session", _create_ok)
    registry = SessionRegistry()
    bot = wbot(registry)
    bot._miniapp_url = "https://example.aipager.run/app"
    _to_summary(bot, mk_update, run_async, mk_cb, chat_id=-1001)
    update, query = mk_cb(chat_id=-1001, chat_type="group")

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:confirm"))

    kb = bot._app.bot.edit_message_text.await_args.kwargs["reply_markup"]
    assert kb is None


def test_confirm_failure_returns_to_summary_and_keeps_pending(
    wbot, mk_update, run_async, mk_cb, monkeypatch,
):
    async def _fail_launch(*a, **kw):
        return False, "dtach socket busy"
    monkeypatch.setattr(inject, "launch_session", _fail_launch)
    bot = wbot()
    _to_summary(bot, mk_update, run_async, mk_cb)
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:confirm"))

    pending = bot._new_wizard_pending.get(555)
    assert pending is not None                 # retry-able — not cleared
    assert pending["step"] == "summary"
    kwargs = bot._app.bot.edit_message_text.await_args.kwargs
    assert "dtach socket busy" in kwargs["text"]
    assert kwargs["reply_markup"] is not None   # Confirm/Optional still there


# ---- confirm: re-authorization ------------------------------------------

def test_confirm_denied_when_caller_can_no_longer_prompt(
    wbot, mk_update, run_async, mk_cb,
):
    bot = wbot()
    _to_summary(bot, mk_update, run_async, mk_cb)
    bot._can_prompt_user = lambda uid, cid: False
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:confirm"))

    assert 555 not in bot._new_wizard_pending
    text = bot._app.bot.edit_message_text.await_args.kwargs["text"]
    assert "not" in text.lower() or "authorized" in text.lower()


def test_confirm_reopens_mode_step_when_auto_demoted(
    wbot, mk_update, run_async, mk_cb,
):
    """Auto was chosen while admin; by Confirm time the caller has been
    demoted — must re-open `mode`, never silently create in Ask mode."""
    bot = wbot()
    bot._is_admin_user = lambda uid, cid: True
    _to_mode(bot, mk_update, run_async)
    cb_update, query = mk_cb()
    run_async(new_flow.handle_callback(bot, cb_update, query, "_", "nw:mode:auto"))
    assert bot._new_wizard_pending[555]["skip_perms"] is True

    bot._is_admin_user = lambda uid, cid: False  # demoted between steps
    run_async(new_flow.handle_callback(bot, cb_update, query, "_", "nw:confirm"))

    pending = bot._new_wizard_pending.get(555)
    assert pending is not None                  # still resumable
    assert pending["step"] == "mode"
    text = bot._app.bot.edit_message_text.await_args.kwargs["text"]
    assert "permission" in text.lower()


def test_confirm_with_no_name_set_is_invalid_callback(wbot, mk_update, run_async, mk_cb):
    """Defensive: a stray Confirm tap that somehow arrives before a name
    was ever recorded must not attempt to create a session."""
    bot = wbot()
    store = new_flow._pending_store(bot)
    store[555] = {
        "step": "name", "user_id": 111, "msg_id": 900, "name": None,
        "skip_perms": None, "model": None, "model_label": None, "cwd": None,
        "prefs": {}, "path_options": [], "pref_section": None,
        "new_folder_parent": None, "last_active": new_flow._now(),
    }
    update, query = mk_cb()

    run_async(new_flow.handle_callback(bot, update, query, "_", "nw:confirm"))

    assert query.answer.await_args.args[0] == "Invalid callback"
    assert 555 in bot._new_wizard_pending  # untouched, not popped


# ---- pure helper functions ------------------------------------------------

def test_is_expired_true_past_ttl_false_within():
    fresh = {"last_active": new_flow._now()}
    assert new_flow._is_expired(fresh) is False
    stale = {"last_active": new_flow._now() - new_flow._WIZARD_TTL_SECONDS - 1}
    assert new_flow._is_expired(stale) is True


def test_short_path_truncates_long_paths_and_keeps_short_ones():
    short = "/home/aly/proj"
    assert new_flow._short_path(short) == short
    long_path = "/" + ("x" * 80)
    out = new_flow._short_path(long_path, limit=20)
    assert len(out) == 20
    assert out.startswith("…")
    assert out.endswith(long_path[-19:])


def test_mode_icon_label():
    assert new_flow._mode_icon_label({"skip_perms": True}) == ("🤖", "Auto")
    assert new_flow._mode_icon_label({"skip_perms": False}) == ("💬", "Ask")
    assert new_flow._mode_icon_label({"skip_perms": None}) == ("💬", "Ask")
