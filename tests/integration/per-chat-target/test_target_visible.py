"""Where a message goes is visible (P2 of the command redesign, 4.5 and 4.9,
2026-09-30).

- The pinned bar says "✍️ Messages go to x1" once a chat has more than one
  live session. Its first line is unchanged; with one session the line is
  implied and left out.
- A bare /<label> replies "✍️ Now talking to x1 · 💤 idle · 🤖 Auto", then
  what happens next, instead of a stats table. A session waiting on its
  person offers Answer; ⋮ More is the session's own menu.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aipager.bot import session_parity
from aipager.state import SessionRegistry, Status, TrackedSession

CHAT = 12345


def _sess(bot, name, label, status=Status.IDLE, *, chat=CHAT, auto=False):
    s = TrackedSession(name=name, label=label, status=status)
    s.scope_chat_id = chat
    s.skip_perms = auto
    bot.registry._sessions[name] = s
    return s


@pytest.fixture
def bar(mk_bot, monkeypatch):
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", str(CHAT))
    return mk_bot(SessionRegistry())


def _lines(bot):
    return bot._render_pinned(CHAT)[0].split("\n")


# ---- the pinned bar ------------------------------------------------------------

def test_the_bar_says_where_messages_go_with_two_sessions(bar):
    _sess(bar, "claude-dev", "dev")
    _sess(bar, "claude-jim", "jim")
    bar.registry.last_active_session = "claude-jim"

    lines = _lines(bar)
    assert lines[1] == "✍️ Messages go to <b>jim</b>"
    assert lines[0] == "💤 2 idle"                        # the first line, unchanged
    assert lines[2:] == ["• <b>dev</b> (idle)", "• <b>jim</b> (idle)"]


def test_the_line_follows_the_target(bar):
    _sess(bar, "claude-dev", "dev")
    _sess(bar, "claude-jim", "jim")
    bar.registry.last_active_session = "claude-jim"
    bar.registry.last_active_session = "claude-dev"

    assert "✍️ Messages go to <b>dev</b>" in _lines(bar)


def test_one_session_needs_no_line(bar):
    _sess(bar, "claude-jim", "jim")
    bar.registry.last_active_session = "claude-jim"

    assert not any(line.startswith("✍️") for line in _lines(bar))


def test_no_line_for_a_target_that_is_not_live(bar):
    _sess(bar, "claude-dev", "dev")
    _sess(bar, "claude-jim", "jim")
    _sess(bar, "claude-old", "old", Status.GONE)
    bar.registry.last_active_session = "claude-old"

    assert not any(line.startswith("✍️") for line in _lines(bar))


def test_no_line_for_another_chats_session(bar):
    _sess(bar, "claude-dev", "dev")
    _sess(bar, "claude-jim", "jim")
    _sess(bar, "claude-bob", "bob", chat=999)
    bar.registry.last_active_session = "claude-bob"

    assert not any(line.startswith("✍️") for line in _lines(bar))


def test_the_line_stays_under_a_waiting_session(bar):
    """The first line names the session that needs you; the target line
    still says where a plain message goes."""
    _sess(bar, "claude-dev", "dev", Status.INTERACTIVE)
    _sess(bar, "claude-jim", "jim")
    bar.registry.last_active_session = "claude-jim"

    lines = _lines(bar)
    assert lines[0].startswith("⏳ dev needs you")
    assert lines[1] == "✍️ Messages go to <b>jim</b>"


# ---- the switch reply -------------------------------------------------------------

@pytest.fixture
def switch(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot(SessionRegistry())
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()

    def _run(label, *, private=False):
        update = mk_update(f"/{label}", chat_id=CHAT)
        update.effective_chat.type = "private" if private else "group"
        run_async(bot._switch_session(update, label))
        call = update.message.reply_text.await_args
        kb = call.kwargs.get("reply_markup")
        buttons = [b for row in kb.inline_keyboard for b in row] if kb else []
        return call.args[0], buttons
    bot.switch = _run
    return bot


@pytest.mark.parametrize("status, auto, first", [
    (Status.IDLE, False, "✍️ Now talking to <b>jim</b> · 💤 idle · 💬 Ask"),
    (Status.IDLE, True, "✍️ Now talking to <b>jim</b> · 💤 idle · 🤖 Auto"),
    (Status.BUSY, True, "✍️ Now talking to <b>jim</b> · ⚙️ working · 🤖 Auto"),
    (Status.UNKNOWN, False, "✍️ Now talking to <b>jim</b> · 🔄 starting · 💬 Ask"),
])
def test_the_switch_reply_says_state_mode_and_what_next(switch, status, auto, first):
    _sess(switch, "claude-jim", "jim", status, auto=auto)

    text, buttons = switch.switch("jim")

    assert text.split("\n") == [first, "Send a message and it goes to jim."]
    assert [b.text for b in buttons] == ["⋮ More"]
    assert "—" not in text


def test_a_waiting_session_offers_answer(switch):
    sess = _sess(switch, "claude-jim", "jim", Status.INTERACTIVE)

    text, buttons = switch.switch("jim")

    assert text.split("\n") == [
        "✍️ Now talking to <b>jim</b> · ⏳ needs you · 💬 Ask",
        "jim is waiting for your answer."]
    assert [b.text for b in buttons] == ["Answer jim", "⋮ More"]
    resolved = [session_parity.resolve_short_cb(
        switch, CHAT, *b.callback_data.split(":", 1)) for b in buttons]
    assert resolved == [(sess.name, "pin_answer"), (sess.name, "menu")]


def test_the_switch_reply_offers_the_app_in_a_private_chat(switch):
    _sess(switch, "claude-jim", "jim")
    switch._miniapp_url = "https://example.invalid/app"

    _text, buttons = switch.switch("jim", private=True)

    assert [b.text for b in buttons][0] == "⋮ More"
    assert len(buttons) == 2 and buttons[1].web_app is not None


def test_a_discovered_session_gets_the_same_reply(switch, monkeypatch):
    """A live socket not yet in the registry is adopted on /<label>: the
    reply is the same shape."""
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))

    text, buttons = switch.switch("x9")

    assert text.startswith("✍️ Now talking to <b>x9</b> · ")
    assert text.split("\n")[1:] == ["Send a message and it goes to x9."]
    assert [b.text for b in buttons] == ["⋮ More"]


def test_the_switch_makes_it_this_chats_target(switch):
    sess = _sess(switch, "claude-jim", "jim")
    _sess(switch, "claude-dev", "dev")
    switch.registry.last_active_session = "claude-dev"

    switch.switch("jim")

    assert switch.registry.target_for(CHAT) is sess
