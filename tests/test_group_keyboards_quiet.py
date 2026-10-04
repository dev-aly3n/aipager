"""Group noise (roadmap 8.91 a, b, f).

(a) A read_only member is told their role once per (chat, user) per
    daemon run, then refused in silence.
(b) In a group, Templates / Commands / Model › / « Back answer as a
    selective reply to the tapper, so only their keyboard changes, and the
    keyboard level is kept per chat (per member in a group).
(f) In a group every reply-keyboard label carries a marker, and only the
    marked form is a tap: a typed "stop" or "Clear" is chatter.

Groups cannot be tested live, so these tests are the proof. A private chat
keeps its keyboards, labels, matching and refusals byte for byte (the DM
parity cases).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import (
    Chat,
    KeyboardButton,
    Message,
    MessageEntity,
    ReplyKeyboardMarkup,
    Update,
    User,
)
from telegram.ext import ApplicationHandlerStop

from aipager.bot import group_intake, session_parity
from aipager.bot.flood import MUTE
from aipager.config import (
    BACK_BUTTON,
    COMMANDS_BUTTON,
    MODEL_CHOICES,
    MODELS_BUTTON,
    QUICK_COMMANDS,
    QUICK_TEMPLATES,
    TEMPLATES_BUTTON,
)
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status, TrackedSession
from aipager.team import Role, Rules, Team, User as TeamUser, reset_unauthorized_seen

G, G2 = -1001234, -1005678
ALICE, BOB, CAROL, DAVE, EVE = 11, 22, 33, 44, 55
DM = ALICE                      # alice's own DM
CAROL_DM = CAROL                # carol's DM, where she is read_only
BOT_ID, BOT_NAME = 4242, "aipagerbot"
X1, X2 = "claude-x1__g1001234", "claude-x2__g1001234"
D1 = "claude-d1__d11"
MARK = group_intake.KEYBOARD_MARKER
DATE = datetime(2026, 10, 4, tzinfo=timezone.utc)
READ_ONLY_WORDS = "your role is"


def _scopes():
    return [
        Scope(chat_id=G, kind="group", label="team", members=(
            Member(id=ALICE, label="alice", role="owner"),
            Member(id=BOB, label="bob", role="user"),
            Member(id=CAROL, label="carol", role="read_only"),
            Member(id=DAVE, label="dave", role="read_only"))),
        Scope(chat_id=G2, kind="group", label="ops", members=(
            Member(id=ALICE, label="alice", role="owner"),
            Member(id=CAROL, label="carol", role="read_only"))),
        Scope(chat_id=DM, kind="dm", label="alice DM", members=(
            Member(id=ALICE, label="alice", role="owner"),)),
        Scope(chat_id=CAROL_DM, kind="dm", label="carol DM", members=(
            Member(id=CAROL, label="carol", role="read_only"),)),
    ]


@pytest.fixture
def kbot(mk_bot, monkeypatch):
    r = SessionRegistry()
    for name, label, chat in ((X1, "x1", G), (X2, "x2", G), (D1, "d1", DM)):
        s = TrackedSession(name=name, label=label, status=Status.IDLE)
        s.scope_chat_id = chat
        r._sessions[name] = s
    bot = mk_bot(r, scopes=_scopes())
    bot._app.bot.username = BOT_NAME
    bot._app.bot.id = BOT_ID
    from aipager.policy import load_policy
    bot.policy = load_policy()
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(side_effect=lambda n: n in bot.registry._sessions))
    bot._inject_prompt = AsyncMock(return_value=True)
    bot._card_for_injected = AsyncMock()
    bot._react = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot.refresh_pinned = AsyncMock()
    bot.registry.set_target(X1, G, ALICE)
    bot.registry.set_target(X1, G, BOB)
    bot.registry.set_target(D1, DM)
    reset_unauthorized_seen()
    yield bot
    reset_unauthorized_seen()


def _the_bot() -> User:
    return User(BOT_ID, "aipager", True, username=BOT_NAME)


def _upd(mk_update, text, user=ALICE, chat=G, *, mention=None, reply_to_bot=False,
         message_id=900):
    """A handler-ready update. ``mention`` is the bot's ``@name`` in
    ``text`` (a real entity, parsed by Telegram's own code)."""
    entities = ()
    if mention is not None:
        i = text.index(mention)
        entities = (MessageEntity(MessageEntity.MENTION, i, len(mention)),)
    real = Message(1, DATE, Chat(chat, "supergroup" if chat < 0 else "private"),
                   from_user=User(user, "u", False), text=text, entities=entities)
    u = mk_update(text, user_id=user, chat_id=chat, message_id=message_id)
    u.message.entities = real.entities
    u.message.parse_entities = real.parse_entities
    u.message.caption = None
    u.message.chat_id = chat
    u.message.chat = MagicMock(id=chat)
    u.effective_user.username = f"u{user}"
    u.effective_user.first_name = "U"
    u.effective_user.last_name = ""
    u.effective_message = u.message
    u.effective_chat.type = "supergroup" if chat < 0 else "private"
    if reply_to_bot:
        rt = MagicMock()
        rt.message_id = 5
        rt.text = "answer"
        rt.caption = None
        rt.from_user = _the_bot()
        u.message.reply_to_message = rt
    return u


def _send(bot, run_async, update):
    run_async(bot._handle_message(update, MagicMock()))


def _call(mock, what):
    call = mock.await_args
    assert call is not None, what
    return call


def _replies(update):
    return [c.args[0] for c in update.message.reply_text.await_args_list]


def _texts(markup) -> list[list[str]]:
    return [[b.text for b in row] for row in markup.keyboard]


def _rows(labels, per_row=3):
    return [list(labels[i:i + per_row]) for i in range(0, len(labels), per_row)]


def _expected_rows(level, *, sessions=(), mark=""):
    def m(label):
        return mark + label

    if level == "templates":
        rows = _rows([m(lbl) for lbl, _ in QUICK_TEMPLATES]) + [[m(BACK_BUTTON)]]
    elif level == "commands":
        rows = _rows([m(lbl) for lbl, _ in QUICK_COMMANDS]) + [
            [m(MODELS_BUTTON), m(BACK_BUTTON)]]
    elif level == "models":
        rows = _rows([m(lbl) for lbl, _ in MODEL_CHOICES]) + [[m(BACK_BUTTON)]]
    else:
        rows = (_rows([m(s) for s in sessions]) if sessions else []) + [
            [m("status"), m("stop"), m("new")],
            [m(TEMPLATES_BUTTON), m(COMMANDS_BUTTON)]]
    return rows


LEVEL_TEXT = {"templates": "\U0001f4cb Templates", "commands": "\U0001f39b Commands",
              "models": "\U0001f916 Model", "main": "⌨️"}


# ==== (a) read_only is told once ==============================================

def _read_only_replies(update):
    return [t for t in _replies(update) if READ_ONLY_WORDS in t]


def test_read_only_is_told_once_per_chat_then_silent(kbot, mk_update, run_async):
    first = _upd(mk_update, "what does this mean?", CAROL, reply_to_bot=True)
    _send(kbot, run_async, first)
    assert len(_read_only_replies(first)) == 1
    assert "read_only" in _replies(first)[0]

    for text in ("nice", "and this?", MARK + "stop"):
        later = _upd(mk_update, text, CAROL, reply_to_bot=True)
        _send(kbot, run_async, later)
        assert _replies(later) == [], text
    kbot._react.assert_not_awaited()
    kbot._inject_prompt.assert_not_awaited()


def test_read_only_another_chat_or_member_is_told_again(kbot, mk_update, run_async):
    _send(kbot, run_async, _upd(mk_update, "hi", CAROL, G))
    other_chat = _upd(mk_update, "hi", CAROL, G2)
    _send(kbot, run_async, other_chat)
    assert len(_read_only_replies(other_chat)) == 1
    other_member = _upd(mk_update, "hi", DAVE, G)
    _send(kbot, run_async, other_member)
    assert len(_read_only_replies(other_member)) == 1
    again = _upd(mk_update, "hi", DAVE, G)
    _send(kbot, run_async, again)
    assert _replies(again) == []


def test_read_only_dm_member_is_told_once(kbot, mk_update, run_async):
    first = _upd(mk_update, "hello", CAROL, CAROL_DM)
    _send(kbot, run_async, first)
    assert len(_read_only_replies(first)) == 1
    second = _upd(mk_update, "hello", CAROL, CAROL_DM)
    _send(kbot, run_async, second)
    assert _replies(second) == []


def test_read_only_still_reaches_status_after_being_told(kbot, mk_update, run_async):
    _send(kbot, run_async, _upd(mk_update, "hi", CAROL, G))
    status = _upd(mk_update, "/status", CAROL, G)
    assert run_async(kbot._authorize(status, allow_read_only=True)) is True
    assert _replies(status) == []


def test_legacy_team_read_only_is_told_once(mk_bot, mk_update, run_async):
    reader = TeamUser(id=3, label="reader", role=Role.READ_ONLY)
    bot = mk_bot(team=Team(group_id=-100, users={
        1: TeamUser(id=1, label="admin", role=Role.ADMIN), 3: reader},
        rules=Rules(deny_tools=[])))
    replies = []
    for _ in range(3):
        update = mk_update("hi", user_id=3, chat_id=-100)
        update.effective_message = update.message
        assert run_async(bot._authorize(update)) is False
        replies.append(_replies(update))
    assert len(replies[0]) == 1 and "read_only" in replies[0][0]
    assert replies[1:] == [[], []]


def test_a_non_members_one_shot_is_unchanged(kbot, mk_update, run_async):
    first = _upd(mk_update, "hi", EVE, G, reply_to_bot=True)
    _send(kbot, run_async, first)
    assert len(_replies(first)) == 1
    assert "allow-list" in _replies(first)[0]
    second = _upd(mk_update, "hi", EVE, G, reply_to_bot=True)
    _send(kbot, run_async, second)
    assert _replies(second) == []
    # Being told as a non-member does not use up a read_only notice.
    carol = _upd(mk_update, "hi", CAROL, G)
    _send(kbot, run_async, carol)
    assert len(_read_only_replies(carol)) == 1


# ==== (b) group sub-keyboards are the tapper's own ==============================

@pytest.mark.parametrize("button,level", [
    (TEMPLATES_BUTTON, "templates"), (COMMANDS_BUTTON, "commands"),
    (MODELS_BUTTON, "models")])
def test_group_sub_keyboard_is_a_selective_reply_to_the_tapper(
        kbot, mk_update, run_async, button, level):
    tap = _upd(mk_update, MARK + button, BOB, G)
    _send(kbot, run_async, tap)
    kbot._app.bot.send_message.assert_not_awaited()
    call = tap.message.reply_text.await_args
    assert call is not None, "the keyboard is not a reply to the tap"
    assert call.args[0] == LEVEL_TEXT[level]
    markup = call.kwargs["reply_markup"]
    assert isinstance(markup, ReplyKeyboardMarkup)
    assert markup.selective is True
    assert markup.resize_keyboard is True
    assert call.kwargs.get("do_quote") is True
    assert _texts(markup) == _expected_rows(level, mark=MARK)
    assert kbot._keyboard_level_for(G, BOB) == level
    assert kbot._keyboard_level_for(G, ALICE) == "main"


def _last_keyboard(update):
    call = update.message.reply_text.await_args
    assert call is not None, "no keyboard came back as a reply to this tap"
    return call.args[0], call.kwargs["reply_markup"]


def test_back_is_per_person_in_a_group(kbot, mk_update, run_async):
    for text, user in ((COMMANDS_BUTTON, ALICE), (MODELS_BUTTON, ALICE),
                       (TEMPLATES_BUTTON, BOB)):
        _send(kbot, run_async, _upd(mk_update, MARK + text, user, G))

    alice_back = _upd(mk_update, MARK + BACK_BUTTON, ALICE, G)
    _send(kbot, run_async, alice_back)
    text, markup = _last_keyboard(alice_back)
    assert text == LEVEL_TEXT["commands"] and markup.selective is True

    bob_back = _upd(mk_update, MARK + BACK_BUTTON, BOB, G)
    _send(kbot, run_async, bob_back)
    text, markup = _last_keyboard(bob_back)
    # « Back to main in a group: the main keyboard, for bob alone.
    assert text == LEVEL_TEXT["main"] and markup.selective is True
    assert _texts(markup) == _expected_rows("main", sessions=["x1", "x2"], mark=MARK)

    alice_back = _upd(mk_update, MARK + BACK_BUTTON, ALICE, G)
    _send(kbot, run_async, alice_back)
    assert _last_keyboard(alice_back)[0] == LEVEL_TEXT["main"]
    kbot._app.bot.send_message.assert_not_awaited()


def test_another_chats_navigation_does_not_move_this_chats_level(
        kbot, mk_update, run_async):
    for text in (COMMANDS_BUTTON, MODELS_BUTTON):
        _send(kbot, run_async, _upd(mk_update, text, ALICE, DM))
    # Group members browse meanwhile.
    _send(kbot, run_async, _upd(mk_update, MARK + TEMPLATES_BUTTON, BOB, G))
    _send(kbot, run_async, _upd(mk_update, MARK + COMMANDS_BUTTON, ALICE, G))
    _send(kbot, run_async, _upd(mk_update, MARK + BACK_BUTTON, ALICE, G))

    kbot._app.bot.send_message.reset_mock()
    _send(kbot, run_async, _upd(mk_update, BACK_BUTTON, ALICE, DM))
    call = kbot._app.bot.send_message.await_args
    assert call is not None, "the DM's Back sent no keyboard"
    assert call.args[1] == LEVEL_TEXT["commands"]

    # And the other way round: the DM's browsing leaves the group alone.
    _send(kbot, run_async, _upd(mk_update, MARK + COMMANDS_BUTTON, BOB, G))
    _send(kbot, run_async, _upd(mk_update, MARK + MODELS_BUTTON, BOB, G))
    _send(kbot, run_async, _upd(mk_update, TEMPLATES_BUTTON, ALICE, DM))
    bob_back = _upd(mk_update, MARK + BACK_BUTTON, BOB, G)
    _send(kbot, run_async, bob_back)
    assert _last_keyboard(bob_back)[0] == LEVEL_TEXT["commands"]


def test_the_shared_main_keyboard_is_unchanged_and_resets_members(kbot, run_async):
    kbot._keyboard_levels[kbot._keyboard_level_key(G, ALICE)] = "models"
    kbot._keyboard_levels[kbot._keyboard_level_key(DM)] = "models"
    run_async(kbot._send_keyboard(level="main", chat_id=G))
    call = kbot._app.bot.send_message.await_args
    assert call.args[:2] == (G, LEVEL_TEXT["main"])
    assert set(call.kwargs) == {"reply_markup"}
    markup = call.kwargs["reply_markup"]
    assert markup.to_dict() == ReplyKeyboardMarkup(
        [[KeyboardButton(t) for t in row]
         for row in _expected_rows("main", sessions=["x1", "x2"], mark=MARK)],
        resize_keyboard=True).to_dict()
    assert "selective" not in markup.to_dict()
    # Everyone in the group now has the main keyboard; another chat's
    # level is not touched.
    assert kbot._keyboard_level_for(G, ALICE) == "main"
    assert kbot._keyboard_level_for(DM) == "models"


def test_a_members_back_to_main_is_not_owed_and_keeps_the_hold(
        kbot, mk_update, run_async):
    kbot._keyboard_deferred = True
    kbot._keyboard_levels[kbot._keyboard_level_key(G, ALICE)] = "templates"
    _send(kbot, run_async, _upd(mk_update, MARK + BACK_BUTTON, ALICE, G))
    assert kbot._keyboard_deferred is True, (
        "a member's own main keyboard is not the group's shared one")
    assert kbot._keyboard_owed == {}


def test_a_muted_members_keyboard_is_not_owed_and_keeps_their_level(
        kbot, mk_update, run_async, monkeypatch):
    monkeypatch.setattr(MUTE, "is_muted", lambda chat: chat == G)
    kbot._keyboard_levels[kbot._keyboard_level_key(G, ALICE)] = "commands"
    back = _upd(mk_update, MARK + BACK_BUTTON, ALICE, G)
    _send(kbot, run_async, back)
    back.message.reply_text.assert_not_awaited()
    assert kbot._keyboard_owed == {}
    assert kbot._keyboard_level_for(G, ALICE) == "commands"
    # The group's shared main keyboard, refused by the mute, is owed.
    run_async(kbot._send_keyboard(level="main", chat_id=G))
    assert G in kbot._keyboard_owed


@pytest.mark.parametrize("taps,level", [
    ((TEMPLATES_BUTTON,), "templates"),
    ((COMMANDS_BUTTON,), "commands"),
    ((COMMANDS_BUTTON, MODELS_BUTTON), "models"),
    ((TEMPLATES_BUTTON, BACK_BUTTON), "main"),
    ((COMMANDS_BUTTON, MODELS_BUTTON, BACK_BUTTON), "commands"),
])
def test_dm_keyboards_are_byte_for_byte_unchanged(kbot, mk_update, run_async, taps, level):
    last = None
    for text in taps:
        last = _upd(mk_update, text, ALICE, DM)
        _send(kbot, run_async, last)
    last.message.reply_text.assert_not_awaited()
    call = kbot._app.bot.send_message.await_args
    assert call is not None, "the DM got no keyboard"
    assert call.args == (DM, LEVEL_TEXT[level])
    assert set(call.kwargs) == {"reply_markup"}
    expected = ReplyKeyboardMarkup(
        [[KeyboardButton(t) for t in row]
         for row in _expected_rows(level, sessions=["d1"])],
        resize_keyboard=True)
    assert call.kwargs["reply_markup"].to_dict() == expected.to_dict()


def test_a_dm_keyboard_ignores_a_tap_argument(kbot, mk_update, run_async):
    """A private chat's keyboard is the chat's, sent as before, even if a
    caller passes the tap."""
    tap = _upd(mk_update, TEMPLATES_BUTTON, ALICE, DM)
    run_async(kbot._send_keyboard(level="templates", chat_id=DM, tap=tap))
    tap.message.reply_text.assert_not_awaited()
    assert _call(kbot._app.bot.send_message, "no DM keyboard").args == (DM, LEVEL_TEXT["templates"])
    assert kbot._keyboard_level_for(DM) == "templates"


# ==== (f) typed words are not taps ===========================================

def _gate_passes(bot, run_async, text, *, chat=G):
    msg = Message(1, DATE, Chat(chat, "supergroup"), from_user=User(BOB, "bob", False),
                  text=text)
    try:
        run_async(group_intake.intake_gate(bot, Update(1, message=msg)))
    except ApplicationHandlerStop:
        return False
    return True


TYPED = ["stop", "status", "new", "Clear", "Opus", "Continue", "x1",
         TEMPLATES_BUTTON, COMMANDS_BUTTON, MODELS_BUTTON, BACK_BUTTON]


@pytest.mark.parametrize("word", TYPED)
def test_a_typed_keyboard_word_is_stopped_by_the_gate(kbot, run_async, word):
    assert not _gate_passes(kbot, run_async, word)


@pytest.mark.parametrize("word", TYPED)
def test_the_marked_label_passes_the_gate(kbot, run_async, word):
    assert _gate_passes(kbot, run_async, MARK + word)


def test_the_marker_without_its_selector_is_a_tap_too(kbot, run_async):
    assert _gate_passes(kbot, run_async, "▫ stop")
    assert group_intake.keyboard_tap("▫ stop") == ("stop", True)


def test_a_marked_word_that_is_no_label_is_chatter(kbot, run_async):
    assert not _gate_passes(kbot, run_async, MARK + "lunch")
    kbot.registry._sessions[X2].status = Status.GONE
    assert not _gate_passes(kbot, run_async, MARK + "x2")


@pytest.fixture
def acts(kbot):
    """Every keyboard action, mocked: which one a message triggered."""
    for name in ("_handle_stop_cmd", "_handle_status", "_handle_new_cmd",
                 "_handle_kill_cmd", "_send_command", "_send_template",
                 "_switch_session", "_send_keyboard"):
        setattr(kbot, name, AsyncMock())
    return kbot


def _acted(bot) -> dict:
    return {name: getattr(bot, name).await_args for name in (
        "_handle_stop_cmd", "_handle_status", "_handle_new_cmd", "_handle_kill_cmd",
        "_send_command", "_send_template", "_switch_session", "_send_keyboard")
        if getattr(bot, name).await_count}


TAPS = [
    ("stop", "_handle_stop_cmd", None),
    ("status", "_handle_status", None),
    ("new", "_handle_new_cmd", None),
    ("Clear", "_send_command", "/clear"),
    ("Opus", "_send_command", "/model opus"),
    ("Continue", "_send_template", "Continue"),
    ("x2", "_switch_session", "x2"),
    (TEMPLATES_BUTTON, "_send_keyboard", None),
    (COMMANDS_BUTTON, "_send_keyboard", None),
    (MODELS_BUTTON, "_send_keyboard", None),
    (BACK_BUTTON, "_send_keyboard", None),
]


@pytest.mark.parametrize("word,action,arg", TAPS)
def test_a_typed_word_past_the_gate_is_not_a_tap(acts, mk_update, run_async,
                                                 word, action, arg):
    """The handler's own check, for any path around the gate: in a group a
    bare keyboard word not addressed to the bot is ordinary text."""
    _send(acts, run_async, _upd(mk_update, word, BOB, G))
    assert _acted(acts) == {}
    assert _call(acts._inject_prompt, "not sent as text").args[1].endswith(word)


@pytest.mark.parametrize("word,action,arg", TAPS)
def test_the_marked_label_acts_in_a_group(acts, mk_update, run_async, word, action, arg):
    _send(acts, run_async, _upd(mk_update, MARK + word, BOB, G))
    acted = _acted(acts)
    assert list(acted) == [action]
    if arg is not None:
        assert acted[action].args[1] == arg
    acts._inject_prompt.assert_not_awaited()


@pytest.mark.parametrize("word,action,arg", TAPS)
def test_a_mentioned_keyboard_word_acts_as_before(acts, mk_update, run_async,
                                                  word, action, arg):
    text = f"@{BOT_NAME} {word}"
    _send(acts, run_async, _upd(mk_update, text, BOB, G, mention=f"@{BOT_NAME}"))
    acted = _acted(acts)
    assert list(acted) == [action]
    if arg is not None:
        assert acted[action].args[1] == arg


def test_a_keyboard_word_replying_to_the_bot_acts_as_before(acts, mk_update, run_async):
    _send(acts, run_async, _upd(mk_update, "Clear", BOB, G, reply_to_bot=True))
    assert _call(acts._send_command, "no command").args[1] == "/clear"


@pytest.mark.parametrize("word,action,arg", [
    t for t in TAPS if t[0] != "x2"] + [("d1", "_switch_session", "d1")])
def test_dm_bare_words_are_taps_as_before(acts, mk_update, run_async, word, action, arg):
    _send(acts, run_async, _upd(mk_update, word, ALICE, DM))
    acted = _acted(acts)
    assert list(acted) == [action]
    if arg is not None:
        assert acted[action].args[1] == arg


def test_dm_text_with_the_marker_is_not_stripped(acts, mk_update, run_async):
    """A private chat never strips the marker: its keyboard has none."""
    _send(acts, run_async, _upd(mk_update, MARK + "stop", ALICE, DM))
    assert _acted(acts) == {}
    assert _call(acts._inject_prompt, "not sent as text").args[1].endswith(MARK + "stop")


@pytest.mark.parametrize("label,payload", [
    *QUICK_TEMPLATES, *QUICK_COMMANDS, *MODEL_CHOICES])
def test_every_marked_label_reaches_its_map_entry(acts, mk_update, run_async,
                                                  label, payload):
    assert _gate_passes(acts, run_async, MARK + label)
    _send(acts, run_async, _upd(mk_update, MARK + label, ALICE, G))
    acted = _acted(acts)
    assert len(acted) == 1
    (name, call), = acted.items()
    assert name in ("_send_template", "_send_command")
    assert call.args[1] == payload


def test_the_marker_round_trips_and_never_reaches_a_dm():
    labels = ["status", "stop", "new", TEMPLATES_BUTTON, COMMANDS_BUTTON,
              MODELS_BUTTON, BACK_BUTTON, "x1",
              *[lbl for lbl, _ in QUICK_TEMPLATES + QUICK_COMMANDS + MODEL_CHOICES]]
    for label in labels:
        assert group_intake.mark_label(label, DM) == label
        marked = group_intake.mark_label(label, G)
        assert marked != label
        assert group_intake.keyboard_tap(marked) == (label, True)
        assert group_intake.keyboard_tap(label) == (label, False)


@pytest.mark.parametrize("level", ["main", "templates", "commands", "models"])
def test_every_label_on_a_group_keyboard_is_marked(kbot, run_async, level):
    run_async(kbot._send_keyboard(level=level, chat_id=G))
    markup = _call(kbot._app.bot.send_message, "no keyboard").kwargs["reply_markup"]
    texts = [t for row in _texts(markup) for t in row]
    assert texts and all(t.startswith(MARK) for t in texts)
    # And each one passes the gate as a tap.
    assert all(_gate_passes(kbot, run_async, t) for t in texts)


def test_a_marked_tap_during_a_rename_is_read_without_the_marker(
        kbot, mk_update, run_async):
    """Capture flows read the label, not the marker: a marked `status`
    only looks, so the rename keeps waiting and nothing is renamed."""
    session_parity._start_rename_capture(kbot, G, kbot.registry._sessions[X1], ALICE)
    kbot._handle_status = AsyncMock()
    kbot._rename_session_core = AsyncMock()
    _send(kbot, run_async, _upd(mk_update, MARK + "status", ALICE, G))
    kbot._handle_status.assert_awaited_once()
    kbot._rename_session_core.assert_not_awaited()
    assert (G, ALICE) in session_parity._rename_pending_map(kbot)


# ==== review iter 1: the real handlers behind a marked tap ====================

def _stoppable(bot, name=X1):
    sess = bot.registry._sessions[name]
    sess.status = Status.BUSY
    return sess


@pytest.mark.parametrize("text", [MARK + "stop", "▫ stop"])
def test_a_marked_stop_tap_stops_through_the_real_handler(kbot, mk_update, run_async,
                                                          text):
    _stoppable(kbot)
    kbot._stop_session = AsyncMock(return_value=MagicMock(ok=True))
    tap = _upd(mk_update, text, BOB, G)
    _send(kbot, run_async, tap)
    call = _call(kbot._stop_session, "the marked stop tap stopped nothing")
    assert call.args[0].name == X1
    assert not any("No session named" in r for r in _replies(tap))


@pytest.fixture
def card_spies(monkeypatch):
    from aipager.bot import new_flow
    opened, created = AsyncMock(), AsyncMock()
    monkeypatch.setattr(new_flow, "start_wizard", opened)
    monkeypatch.setattr(new_flow, "create_from_text", created)
    return opened, created


def test_a_marked_new_tap_opens_the_name_card(kbot, mk_update, run_async, card_spies):
    opened, created = card_spies
    _send(kbot, run_async, _upd(mk_update, MARK + "new", BOB, G))
    opened.assert_awaited_once()
    created.assert_not_awaited()


def test_a_marked_new_tap_with_a_name_card_open_reopens_it(kbot, mk_update, run_async,
                                                           card_spies):
    from aipager.bot import new_flow
    opened, created = card_spies
    new_flow._pending_store(kbot)[(G, BOB)] = {
        "step": "name", "user_id": BOB, "msg_id": 7000, "path_options": [],
        "new_folder_parent": "/w", "last_active": new_flow._now(),
        "skip_perms": True, "can_auto": True, "model": None, "model_label": None,
        "cwd": None}
    _send(kbot, run_async, _upd(mk_update, MARK + "new", BOB, G))
    opened.assert_awaited_once()
    created.assert_not_awaited()


def test_a_marked_kill_reads_no_argument(kbot, mk_update, run_async):
    """`kill` is not on the keyboard (the gate stops `▫️ kill`), but the
    kill handler reads its argument the same way and must not take the
    marker for a command word."""
    tap = _upd(mk_update, MARK + "kill", ALICE, G, reply_to_bot=True)
    run_async(kbot._handle_kill_cmd(tap, MagicMock()))
    # Delivery 18 (8.93): alice's own target, x1, is the one a bare /kill means.
    assert _replies(tap) == ["⏹ End <b>x1</b>? Claude stops and the session closes."]


def test_own_text_keeps_a_dm_marker(kbot, mk_update):
    assert kbot._own_text(_upd(mk_update, MARK + "stop", ALICE, DM)) == MARK + "stop"
    assert kbot._own_text(_upd(mk_update, MARK + "stop", ALICE, G)) == "stop"
    assert kbot._own_text(_upd(mk_update, "/stop x1", ALICE, G)) == "/stop x1"


def test_a_read_only_notice_withheld_by_a_mute_is_sent_later(
        kbot, mk_update, run_async, monkeypatch):
    muted = {"on": True}
    monkeypatch.setattr(MUTE, "is_muted", lambda chat: muted["on"] and chat == G)
    first = _upd(mk_update, "hi", CAROL, G)
    _send(kbot, run_async, first)
    first.message.reply_text.assert_not_awaited()
    muted["on"] = False
    second = _upd(mk_update, "hi", CAROL, G)
    _send(kbot, run_async, second)
    assert len(_read_only_replies(second)) == 1
    third = _upd(mk_update, "hi", CAROL, G)
    _send(kbot, run_async, third)
    assert _replies(third) == []


def test_a_read_only_notice_that_failed_is_tried_again(kbot, mk_update, run_async):
    first = _upd(mk_update, "hi", CAROL, G)
    first.message.reply_text = AsyncMock(side_effect=RuntimeError("network"))
    _send(kbot, run_async, first)
    second = _upd(mk_update, "hi", CAROL, G)
    _send(kbot, run_async, second)
    assert len(_read_only_replies(second)) == 1


def test_a_muted_shared_main_keeps_a_level_set_during_its_send(kbot, run_async):
    """A member's tap handled while the group's shared main keyboard was
    being sent keeps its level when that send turns out muted."""
    from aipager.bot.flood import FloodMuted
    alice = kbot._keyboard_level_key(G, ALICE)
    bob = kbot._keyboard_level_key(G, BOB)
    kbot._keyboard_levels[alice] = "templates"
    kbot._keyboard_levels[bob] = "commands"

    async def muted_send(*args, **kwargs):
        kbot._keyboard_levels[alice] = "models"     # alice's tap, meanwhile
        raise FloodMuted(30, G)

    kbot._app.bot.send_message = AsyncMock(side_effect=muted_send)
    run_async(kbot._send_keyboard(level="main", chat_id=G))
    assert kbot._keyboard_level_for(G, ALICE) == "models"
    # Untouched by anyone else: restored as the mute withheld the keyboard.
    assert kbot._keyboard_level_for(G, BOB) == "commands"
