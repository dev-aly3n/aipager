"""One unusable session label must not cost a chat its whole command menu.

Telegram requires a bot command to match ``[a-z0-9_]{1,32}`` and rejects
the ENTIRE ``setMyCommands`` payload if any single entry violates it —
it does not drop the bad one. Session labels are validated by a looser
rule (``inject._VALID_NAME`` allows uppercase, hyphens and up to 64
characters), so a perfectly legal session name can be an illegal
command.

Observed in production on 2026-08-20: sessions named ``Helle`` and
``oIo`` produced ``telegram.error.BadRequest: Bot_command_invalid`` from
``_update_bot_commands_per_scope``, and because that call site swallows
exceptions the only symptom was an empty ``/`` menu.
"""

from __future__ import annotations

import re

from aipager.bot.lifecycle import LifecycleMixin

# Telegram's documented rule for BotCommand.command.
TELEGRAM_COMMAND = re.compile(r"^[a-z0-9_]{1,32}$")

# Every static command the menu must keep offering no matter how broken
# the session labels are.
STATIC = {"status", "stop", "kill", "new", "resume", "perms", "settings",
          "clearqueue", "whoami", "restart", "rename", "delete", "diff"}


def _commands(labels):
    return LifecycleMixin._command_list(set(labels))


def test_every_emitted_command_is_one_telegram_will_accept():
    """The core property. `Helle` is the label that actually broke it."""
    cmds = _commands({"Helle", "oIo", "has-a-hyphen", "w" * 40, "fine"})

    bad = [c.command for c in cmds if not TELEGRAM_COMMAND.match(c.command)]
    assert not bad, (
        f"these would make Telegram reject the whole payload: {bad}")


def test_command_list_keeps_the_static_menu_when_a_label_is_unusable():
    """Narrow guard: an implementation that bails on the whole list when
    it meets a bad label would still satisfy the regex test above.

    This does NOT reproduce the production failure — that happens inside
    Telegram, on the whole payload, and is covered by
    ``test_a_capitalised_session_no_longer_costs_the_chat_its_menu``
    below. Named for what it actually pins.
    """
    cmds = {c.command for c in _commands({"Helle"})}

    assert STATIC <= cmds, f"static commands lost: {sorted(STATIC - cmds)}"


def test_a_usable_label_still_gets_its_shortcut():
    """The filter must not throw away the good ones too — a guard that
    drops everything would pass the test above for the wrong reason."""
    cmds = {c.command for c in _commands({"Helle", "goodname"})}

    assert "goodname" in cmds, "a valid label lost its /shortcut"
    assert "Helle" not in cmds and "helle" not in cmds, (
        "an unusable label must be skipped, not silently rewritten — "
        "registry.find_by_label matches the label exactly, so /helle "
        "would autocomplete and then resolve to nothing")


def test_a_label_of_only_invalid_characters_never_becomes_an_empty_command():
    """An empty `command` is itself a Bot_command_invalid, so a
    sanitising implementation must not turn `---` into ``""``."""
    cmds = _commands({"---"})

    assert all(c.command for c in cmds), "emitted an empty command string"
    bad = [c.command for c in cmds if not TELEGRAM_COMMAND.match(c.command)]
    assert not bad, bad


def test_no_labels_at_all_still_yields_the_static_menu():
    cmds = {c.command for c in _commands(set())}

    assert STATIC <= cmds, f"static commands lost: {sorted(STATIC - cmds)}"


def test_the_description_still_names_the_session_as_the_user_wrote_it():
    """The command is constrained; the human-readable half is not, and
    should keep the label's real spelling."""
    cmds = _commands({"goodname"})

    entry = next(c for c in cmds if c.command == "goodname")
    assert "goodname" in entry.description


# ---- the seam where it actually broke ----------------------------------
#
# The tests above examine `_command_list`'s return value, which cannot
# fail the way production failed: the rejection happens inside Telegram,
# on the whole payload. These drive `_update_bot_commands_per_scope`
# against a stand-in that enforces Telegram's documented rule, so the
# reproduction is of the real failure and not of a local approximation.

def test_a_capitalised_session_no_longer_costs_the_chat_its_menu(
        mk_bot, run_async):
    """End-to-end at the failing seam, with Telegram's rule enforced.

    Before the fix this raised `BadRequest: Bot_command_invalid`, the
    call site swallowed it, and the chat was left with whatever menu it
    had before — for the operator, none at all.
    """
    from unittest.mock import AsyncMock

    from telegram.error import BadRequest

    from aipager.scope import Member, Scope
    from aipager.state import Status

    CHAT = -100
    bot = mk_bot(scopes=[Scope(chat_id=CHAT, kind="group", label="s",
                               members=(Member(id=1, label="a", role="owner"),))])

    sess = bot.registry.get_or_create("claude-Helle")
    sess.label = "Helle"
    sess.scope_chat_id = CHAT
    bot.registry.transition("claude-Helle", Status.IDLE)

    accepted: list[list[str]] = []

    async def strict_set_my_commands(commands, **kwargs):
        for c in commands:
            if not TELEGRAM_COMMAND.match(c.command):
                # Telegram rejects the PAYLOAD, not the entry.
                raise BadRequest("Bot_command_invalid")
        accepted.append([c.command for c in commands])

    bot._app.bot.set_my_commands = AsyncMock(side_effect=strict_set_my_commands)

    run_async(bot._update_bot_commands_per_scope())

    assert accepted, (
        "Telegram rejected the whole payload — the chat got no commands "
        "at all because one session was named 'Helle'")
    assert STATIC <= set(accepted[-1]), (
        f"static commands lost: {sorted(STATIC - set(accepted[-1]))}")


def test_a_successful_registration_records_the_scope_label_set(mk_bot,
                                                               run_async):
    """`_update_bot_commands_per_scope` caches the label set it just
    registered so unchanged scans skip the API call.

    Worth pinning here because that cache is also written on the FIRST
    failure (`if prev is None`), so a rejected menu was never retried for
    that label set — the operator's chat stayed empty rather than
    recovering on the next scan. With the label filtered, registration
    succeeds and the cache records a menu that really was accepted.
    """
    from unittest.mock import AsyncMock

    from aipager.scope import Member, Scope
    from aipager.state import Status

    CHAT = -100
    bot = mk_bot(scopes=[Scope(chat_id=CHAT, kind="group", label="s",
                               members=(Member(id=1, label="a", role="owner"),))])
    sess = bot.registry.get_or_create("claude-Helle")
    sess.label = "Helle"
    sess.scope_chat_id = CHAT
    bot.registry.transition("claude-Helle", Status.IDLE)
    bot._app.bot.set_my_commands = AsyncMock()

    run_async(bot._update_bot_commands_per_scope())

    bot._app.bot.set_my_commands.assert_awaited_once()
    assert bot._registered_scope_labels.get(CHAT) == {"Helle"}, (
        "the scope's label set should be recorded after a SUCCESSFUL "
        "registration, so unchanged scans skip the API call")
