"""The Name card takes only a name, and only while the person is still on it
(reviews 1-3, 2026-09-30).

A name starts a session at once, with no Confirm step. So a card that stays
open after its person moved on, or that takes a keyboard tap as a name,
would launch a session nobody asked for. Closed BY DEFAULT: one handler that
runs before every other one (`new_flow.close_if_moved_on`, group -1) closes
the person's card on anything they do except answer it or look around, and
the card's own text rules (`_not_for_the_card`) sort the plain texts.

Driven the way Telegram delivers an update: the group -1 handler first,
then the real handler (`_handle_new_cmd`, `_handle_message`, a command
handler), with only the dtach launch and the PTY faked. A test below pins
that registration.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import group_intake, new_flow, session_parity
from aipager.state import SessionRegistry, Status, TrackedSession

CHAT = 555
OWNER = 111
OTHER = 222


async def _launch_ok(*a, **kw):
    return True, ""


@pytest.fixture
def chat(mk_bot, mk_update, run_async, monkeypatch):
    """A bot plus `send(text)`, `send_photo()`, `send_voice()` and
    `tap(data)`, each one update from a person in CHAT, delivered in
    Telegram's order."""
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(side_effect=_launch_ok))
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    bot = mk_bot(SessionRegistry())
    bot._app.bot.edit_message_text = AsyncMock()
    bot._app.bot.set_my_commands = AsyncMock()
    bot._app.bot.username = "aipager_test_bot"
    bot._is_admin_user = MagicMock(return_value=True)
    bot._is_admin = MagicMock(return_value=True)
    bot._inject_prompt = AsyncMock(return_value=True)
    bot._card_for_injected = AsyncMock()
    bot._react = AsyncMock()
    counter = iter(range(10, 10_000))

    def _message(text, user):
        mid = next(counter)
        update = mk_update(text, message_id=mid, chat_id=CHAT, user_id=user)
        update.effective_chat.type = "private"
        update.callback_query = None
        update.message.voice = None
        update.message.forward_origin = None
        update.message.via_bot = None
        update.message.photo = ()
        update.message.document = None
        update.message.reply_text = AsyncMock(
            return_value=MagicMock(message_id=900 + mid))
        return update

    def send(text, *, user=OWNER, reply_to=None, handler=None, forwarded=False,
             via_bot=False, external=False):
        update = _message(text, user)
        if external:
            from telegram import ExternalReplyInfo
            update.message.external_reply = MagicMock(spec=ExternalReplyInfo)
        if forwarded:
            update.message.forward_origin = MagicMock()
        if via_bot:
            update.message.via_bot = MagicMock()
        if reply_to is not None:
            update.message.reply_to_message = MagicMock(
                message_id=reply_to, text="", caption=None)
        run_async(new_flow.close_if_moved_on(bot, update, MagicMock()))
        if handler is not None:                     # a registered command
            run_async(handler(update, MagicMock()))
        elif text.startswith("/new"):
            run_async(bot._handle_new_cmd(update, MagicMock()))
        else:
            run_async(bot._handle_message(update, MagicMock()))
        return update

    def send_photo(*, user=OWNER):
        update = _message(None, user)
        update.message.photo = [MagicMock()]
        update.message.caption = "look at this error"
        run_async(new_flow.close_if_moved_on(bot, update, MagicMock()))
        return update

    def send_sticker(*, user=OWNER):
        update = _message(None, user)
        update.message.sticker = MagicMock()
        run_async(new_flow.close_if_moved_on(bot, update, MagicMock()))
        return update

    def send_voice(*, user=OWNER):
        update = _message(None, user)
        update.message.voice = MagicMock()
        run_async(new_flow.close_if_moved_on(bot, update, MagicMock()))
        return update

    def tap(data, *, user=OWNER):
        # From the open card's own message, as a real tap on it would be.
        card = (getattr(bot, "_new_wizard_pending", {}).get((CHAT, OWNER)) or {}).get("msg_id")
        query = MagicMock(data=data)
        query.answer = AsyncMock()
        query.from_user = MagicMock(id=user)
        query.message = MagicMock(message_id=card or 42)
        update = MagicMock(callback_query=query, message=None,
                           effective_user=query.from_user)
        update.effective_chat = MagicMock(id=CHAT, type="private")
        run_async(new_flow.close_if_moved_on(bot, update, MagicMock()))
        return update

    bot.send, bot.send_photo, bot.send_voice, bot.tap = send, send_photo, send_voice, tap
    bot.send_sticker = send_sticker
    return bot


def _labels(bot):
    return sorted(s.label for s in bot.registry.all_sessions().values() if s.label)


def _injected(bot):
    return [(c.args[0].label, c.args[1]) for c in bot._inject_prompt.await_args_list]


def _live(bot, label):
    sess = TrackedSession(name=f"claude-{label}", label=label,
                          status=Status.IDLE, scope_chat_id=CHAT)
    bot.registry._sessions[sess.name] = sess
    bot.registry.last_active_session = sess.name
    return sess


def _open(bot):
    return (CHAT, OWNER) in bot._new_wizard_pending


# ---- the registration this all depends on ------------------------------------

def test_the_close_runs_before_every_other_handler(mk_bot, run_async, monkeypatch):
    """`close_if_moved_on` is a TypeHandler for every Update in group -1,
    so it sees a photo, a command or a tap before the handler that acts
    on it. Read from the real registration in `start()`."""
    import functools

    from telegram import Update
    from telegram.ext import TypeHandler

    # start() builds the message handlers' chat gate from CHAT_ID; CI runs
    # with it blank, and every registration must happen for the last check.
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", str(CHAT))
    bot = mk_bot()
    registered = []
    stub = MagicMock()
    stub.add_handler = MagicMock(side_effect=lambda h, *a, **k: registered.append(
        (h, k.get("group", a[0] if a else 0))))
    stub.bot = MagicMock()
    stub.bot.set_my_commands = AsyncMock()
    stub.initialize = AsyncMock()
    stub.start = AsyncMock()
    stub.updater = MagicMock()
    stub.updater.start_polling = AsyncMock()
    bot._make_builder = MagicMock(
        return_value=MagicMock(build=MagicMock(return_value=stub)))
    bot._update_bot_commands = AsyncMock()

    run_async(bot.start())

    # The group intake gate (group -2, tests/test_group_intake.py) runs
    # before it; it lets a DM's every message and a group's addressed ones
    # through, so the close still sees them.
    first = [(h, g) for h, g in registered if isinstance(h, TypeHandler)
             and getattr(h.callback, "func", None) is not group_intake.intake_gate]
    assert len(first) == 1
    handler, group = first[0]
    assert group == -1 and handler.type is Update
    assert isinstance(handler.callback, functools.partial)
    assert handler.callback.func is new_flow.close_if_moved_on
    assert handler.callback.args == (bot,)
    assert all(g >= 0 for h, g in registered
               if h is not handler
               and getattr(h.callback, "func", None) is not group_intake.intake_gate)


# ---- anything else the person does closes the card ---------------------------

def test_a_photo_to_a_session_closes_the_card(chat):
    """Review-3's case A: a screenshot for x1, then "fix it please". Both
    are for x1; no session "fix" starts."""
    _live(chat, "x1")
    chat.send("/new")
    chat.send_photo()
    chat.send("fix it please")

    assert not _open(chat)
    assert _labels(chat) == ["x1"]
    assert ("x1", "fix it please") in _injected(chat)


@pytest.mark.parametrize("how", ["forwarded", "via_bot"])
def test_a_forwarded_message_goes_to_the_session_and_closes_the_card(chat, how):
    """Review-4: a forwarded error text (or one posted through another
    bot) is for x1, never a session named after its first word."""
    _live(chat, "x1")
    chat.send("/new")
    chat.send("Error: connection refused at db.connect line 42", **{how: True})
    chat.send("fix that")

    assert _labels(chat) == ["x1"]
    assert not _open(chat)
    assert ("x1", "fix that") in _injected(chat)


def test_a_sticker_is_not_talking_to_a_session(chat):
    """Nothing here acts on a sticker, so the card keeps waiting."""
    chat.send("/new")
    chat.send_sticker()
    chat.send("x2")

    assert _labels(chat) == ["x2"]


def test_another_bots_command_is_not_ours(chat):
    chat.send("/new")
    chat.send("/stop@some_other_bot", handler=AsyncMock())

    assert _open(chat)


def test_the_stop_command_closes_the_card(chat):
    """Review-3's case B: `/stop` from the menu, then a message."""
    _live(chat, "x1")
    stop = AsyncMock()
    chat.send("/new")
    chat.send("/stop", handler=stop)
    chat.send("actually do the docs")

    stop.assert_awaited_once()
    assert _labels(chat) == ["x1"]
    assert ("x1", "actually do the docs") in _injected(chat)


@pytest.mark.parametrize("command", [
    "/stop", "/kill", "/now", "/perms", "/restart", "/clearqueue",
    "/resume x3", "/rename", "/delete", "/diff", "/update",
    "/x1", "/x1 look at the logs", "/x1 stop", "/stop@aipager_test_bot"])
def test_a_command_that_acts_on_a_session_closes_the_card(chat, command):
    _live(chat, "x1")
    chat.send("/new")
    chat.send(command, handler=AsyncMock())

    assert not _open(chat)


@pytest.mark.parametrize("command", [
    "/status", "/start", "/help", "/whoami", "/settings", "/app",
    "/status@aipager_test_bot"])
def test_a_command_that_only_looks_keeps_the_card(chat, command):
    chat.send("/new")
    chat.send(command, handler=AsyncMock())

    assert _open(chat)


@pytest.mark.parametrize("data", [
    "_:sx:0:rdy_ask", "_:sx:1:pin_answer", "claude-x1:allow", "_:sx:0:rename",
    "_:sx:0:resume", "claude-x1:new_resume"])
def test_a_button_that_acts_on_a_session_closes_the_card(chat, data):
    chat.send("/new")
    chat.tap(data)

    assert not _open(chat)


@pytest.mark.parametrize("data", ["_:nw:mode:ask", "_:nw:opt:model", "_:set:ns",
                                  "_:set:layout", "_:spref", "_:spref:0",
                                  "_:resume_page:1", "_:resume_noop",
                                  "_:st:ended", "_:st:list"])
def test_the_cards_own_buttons_and_settings_keep_it(chat, data):
    chat.send("/new")
    chat.tap(data)

    assert _open(chat)


def test_a_voice_message_is_left_to_the_card(chat):
    """The transcript may be the name: `_handle_voice` passes it through
    the same rules as typed text."""
    chat.send("/new")
    chat.send_voice()

    assert _open(chat)


def test_someone_elses_update_never_closes_the_card(chat):
    chat.send("/new")
    chat.send_photo(user=OTHER)
    chat.send("/stop", user=OTHER, handler=AsyncMock())
    chat.tap("_:sx:0:rdy_ask", user=OTHER)

    assert _open(chat)


def test_an_update_with_no_message_or_button_keeps_the_card(chat, run_async):
    """An edit or a reaction is not someone talking to a session."""
    chat.send("/new")
    update = MagicMock(callback_query=None, message=None,
                       effective_user=MagicMock(id=OWNER))
    update.effective_chat = MagicMock(id=CHAT)
    run_async(new_flow.close_if_moved_on(chat, update, MagicMock()))

    assert _open(chat)


def test_the_closed_card_says_so(mk_bot, run_async):
    bot = mk_bot(SessionRegistry())
    bot._app.bot.edit_message_text = AsyncMock()
    bot._new_wizard_pending = {(CHAT, OWNER): {"user_id": OWNER, "msg_id": 900, "step": "name"}}

    async def _go():
        new_flow.close_open_card(bot, CHAT, OWNER)
        await asyncio.sleep(0)
    run_async(_go())

    kw = bot._app.bot.edit_message_text.await_args.kwargs
    assert kw["message_id"] == 900 and "Closed" in kw["text"]


# ---- /new itself ---------------------------------------------------------------

def test_new_with_a_name_closes_the_open_card(chat):
    """`/new`, then `/new x1 ...`, then a message meant for x1. It goes to
    x1, and no session "please" starts."""
    chat.send("/new")
    chat.send("/new x1 fix the tests")
    chat.send("please update the docs")

    assert _labels(chat) == ["x1"]
    assert not _open(chat)
    assert ("x1", "please update the docs") in _injected(chat)


def test_new_with_a_taken_name_closes_the_open_card(chat):
    """`/new jim ...` on a name in use shows the conflict card and starts
    nothing, so only its own close keeps the old card from taking the
    next message."""
    _live(chat, "jim")
    chat._send_new_conflict_prompt = AsyncMock()
    chat.send("/new")
    chat.send("/new jim do it")
    chat.send("hello there")

    assert not _open(chat)
    assert _labels(chat) == ["jim"]


def test_a_card_opened_during_its_own_launch_stays(chat, monkeypatch):
    """A bare /new sent while the card's session launches opens a newer
    card: that launch must not close it."""
    newer = {"step": "name", "user_id": OWNER, "msg_id": 7777,
             "last_active": new_flow._now()}

    async def _launch(*a, **kw):
        chat._new_wizard_pending[(CHAT, OWNER)] = newer
        return True, ""
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(side_effect=_launch))
    chat.send("/new")
    chat.send("x1")

    assert _labels(chat) == ["x1"]
    assert chat._new_wizard_pending.get((CHAT, OWNER)) is newer


def test_a_new_card_ends_the_same_persons_waiting_rename(chat):
    """Their newest question wins: a rename left waiting would otherwise
    take the message after the card's name."""
    sess = _live(chat, "x1")
    session_parity._start_rename_capture(chat, CHAT, sess, OWNER)
    chat.send("/new")

    assert chat._rename_pending == {}


def test_a_new_card_leaves_someone_elses_rename(chat):
    sess = _live(chat, "x1")
    session_parity._start_rename_capture(chat, CHAT, sess, OTHER)
    chat.send("/new")

    assert chat._rename_pending[(CHAT, OTHER)]["user_id"] == OTHER


def test_a_rename_remembers_who_asked(chat, run_async, mk_update):

    sess = _live(chat, "x1")
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.from_user = MagicMock(id=OWNER)
    query.message = MagicMock(message_id=42)
    update = mk_update("", chat_id=CHAT, user_id=OWNER)
    update.callback_query = query
    update.effective_chat.type = "private"

    run_async(session_parity.handle_callback(chat, update, query, sess.name, "rename"))

    assert chat._rename_pending[(CHAT, OWNER)]["user_id"] == OWNER


# ---- plain texts: the card's own rules ------------------------------------------
#
# Talking to a session (a template, a Claude command or model tap, stop,
# kill, a reply to another message) is routed as usual and CLOSES the
# card. Only looking around (navigation buttons, status) or reopening it
# (new) keeps it waiting.

def test_a_template_tap_goes_to_the_session_and_closes_the_card(chat):
    _live(chat, "x1")
    template = next(iter(chat._template_map))
    chat._send_template = AsyncMock()
    chat.send("/new")
    chat.send(template)
    chat.send("and the docs too")

    chat._send_template.assert_awaited_once()
    assert _labels(chat) == ["x1"]
    assert not _open(chat)
    assert ("x1", "and the docs too") in _injected(chat)


@pytest.mark.parametrize("word, handler, stays_open", [
    ("status", "_handle_status", True),
    ("stop", "_handle_stop_cmd", False),
    ("kill", "_handle_kill_cmd", False)])
def test_a_keyboard_word_is_not_a_name(chat, word, handler, stays_open):
    """The tap does what the button does. Looking (status) keeps the card
    waiting; acting on a session (stop, kill) closes it."""
    setattr(chat, handler, AsyncMock())
    chat.send("/new")
    chat.send(word)

    getattr(chat, handler).assert_awaited_once()
    assert _labels(chat) == []
    assert _open(chat) is stays_open


def test_a_model_or_claude_command_button_is_not_a_name(chat):
    model_label = next(iter(chat._model_map))
    command_label = next(lbl for lbl in chat._command_map if not lbl.startswith("/"))
    chat._send_command = AsyncMock()
    chat.send("/new")
    chat.send(model_label)

    assert _labels(chat) == []
    assert not _open(chat)
    chat.send("/new")
    chat.send(command_label)

    assert chat._send_command.await_count == 2
    assert _labels(chat) == []
    assert not _open(chat)


@pytest.mark.parametrize("button", ["TEMPLATES_BUTTON", "COMMANDS_BUTTON",
                                    "MODELS_BUTTON", "BACK_BUTTON", "APP_BUTTON"])
def test_a_navigation_button_is_not_a_name(chat, button):
    from aipager import config

    chat._send_keyboard = AsyncMock()
    chat._handle_app_cmd = AsyncMock()
    chat.send("/new")
    chat.send(getattr(config, button))

    # The button did its own thing (a keyboard level, or the App).
    assert chat._send_keyboard.await_count + chat._handle_app_cmd.await_count == 1
    assert _labels(chat) == []
    assert _open(chat)


def test_a_reply_to_another_message_goes_there_and_closes_the_card(chat):
    """Review-2's case: a reply to an x1 answer, then a plain follow-up.
    Both go to x1; no session "and" starts."""
    _live(chat, "x1")
    chat.registry.track_message(4242, "claude-x1", CHAT)
    chat.send("/new")
    chat.send("fix the bug", reply_to=4242)
    chat.send("and the docs too")

    assert _labels(chat) == ["x1"]
    assert ("x1", "fix the bug") in _injected(chat)
    assert ("x1", "and the docs too") in _injected(chat)
    assert not _open(chat)


def test_a_quote_from_another_chat_goes_to_the_session_and_closes_the_card(chat):
    """A reply quoting a message in another chat carries `external_reply`
    instead of `reply_to_message`: still a reply, never a name."""
    _live(chat, "x1")
    chat.send("/new")
    chat.send("look at this", external=True)
    chat.send("and fix it")

    assert _labels(chat) == ["x1"]
    assert not _open(chat)
    assert ("x1", "and fix it") in _injected(chat)


def test_a_reply_to_the_card_itself_is_the_name(chat):
    chat.send("/new")
    card = chat._new_wizard_pending[(CHAT, OWNER)]["msg_id"]
    chat.send("x1", reply_to=card)

    assert _labels(chat) == ["x1"]


def test_the_word_new_opens_a_fresh_card(chat):
    chat.send("/new")
    first = chat._new_wizard_pending[(CHAT, OWNER)]["msg_id"]
    chat.send("new")

    assert _labels(chat) == []
    assert chat._new_wizard_pending[(CHAT, OWNER)]["msg_id"] != first


def test_after_looking_around_the_name_still_works(chat):
    from aipager import config

    chat._send_keyboard = AsyncMock()
    chat._handle_status = AsyncMock()
    chat.send("/new")
    chat.send(config.TEMPLATES_BUTTON)
    chat.send("status")
    chat.send("x1")

    assert _labels(chat) == ["x1"]


def test_a_model_label_is_still_a_custom_model(chat, run_async):
    """The custom-model step takes its own answer: a model label there is
    the model, shown by its label."""
    from aipager.config import MODEL_CHOICES

    chat.send("/new")
    update = chat.tap("_:nw:model:custom")
    run_async(new_flow.handle_callback(
        chat, update, update.callback_query, "_", "nw:model:custom"))
    assert chat._new_wizard_pending[(CHAT, OWNER)]["step"] == "opt_model_custom"

    chat.send(MODEL_CHOICES[0][0])

    pending = chat._new_wizard_pending[(CHAT, OWNER)]
    assert pending["model"] and pending["step"] == "name"
    assert pending["model_label"] == MODEL_CHOICES[0][0]


def test_a_keyboard_tap_at_the_new_folder_step_is_not_a_folder(chat, run_async, tmp_path,
                                                                monkeypatch):
    """Review-3 nit: `stop` or a template at "Type the new folder's name"
    is that button, not a folder to create."""
    from aipager.miniapp import launch

    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(launch, "allowed_roots", lambda reg, chat_id, **_kw: [str(root)])
    chat._handle_stop_cmd = AsyncMock()
    chat.send("/new")
    update = chat.tap("_:nw:path:new")
    run_async(new_flow.handle_callback(
        chat, update, update.callback_query, "_", "nw:path:new"))

    assert chat._new_wizard_pending[(CHAT, OWNER)]["step"] == "opt_path_newfolder"
    chat.send("stop")

    chat._handle_stop_cmd.assert_awaited_once()
    assert list(root.iterdir()) == []


# ---- names people naturally type -------------------------------------------

@pytest.mark.parametrize("text, name, first", [
    ("x1: fix the tests", "x1", "fix the tests"),
    ("x1, fix the tests", "x1", "fix the tests"),
    ("X1.", "x1", ""),
])
def test_punctuation_after_the_name_is_dropped(text, name, first):
    got = new_flow.parse_request(text)
    assert (got[0].lower(), got[1], got[3]) == (name, first, "")
