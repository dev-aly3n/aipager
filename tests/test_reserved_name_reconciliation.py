"""One canonical reserved-name set, not two that drift apart.

There used to be two: `inject._RESERVED` (enforced on names created from
Telegram and the Mini App) and `launcher._RESERVED_NAMES`, now `_SUBCOMMAND_VERBS` (enforced only
on `aipager session <name>`). They disagreed in both directions, so a
session could be named after a command on the surface that did not
reserve it — and then shadow that command on the surface that did.

Measured on `18c8b60`, before this change:

    COMMANDS NOT RESERVED : app clearqueue perms resume whoami
    CLI verbs not reserved: list ls

The specific missing words were never the defect. Two hand-maintained
parallel lists were, which is why they drifted twice. Hence
`test_every_registered_bot_command_is_reserved` below: it fails the
moment someone adds a command without reserving it.
"""

from __future__ import annotations

from aipager.bot.lifecycle import LifecycleMixin
from aipager.dtach.inject import _RESERVED
from aipager.dtach.launcher import _validate_name
from aipager.miniapp.launch import validate_session_name


# ---- the anti-drift guard ---------------------------------------------

def test_every_registered_bot_command_is_reserved():
    """The guard that stops a third drift.

    Anything `_command_list` registers is typed as `/word` in a chat, so
    a session with that name shadows it. Adding a command without
    reserving the name must fail here rather than in someone's chat.
    """
    commands = {c.command for c in LifecycleMixin._command_list(set())}

    unreserved = commands - _RESERVED
    assert not unreserved, (
        "these bot commands can be shadowed by a session of the same "
        f"name: {sorted(unreserved)} — add them to inject._RESERVED")


def test_every_dispatched_command_handler_is_reserved(mk_bot, run_async):
    """The menu list is not the whole truth.

    `_command_list` builds what Telegram SHOWS; `add_handler(CommandHandler(
    ...))` is what actually answers. A command wired into the dispatcher but
    kept out of the menu would be shadowable while passing the guard above.

    Read from the REAL dispatch table, by letting `start()` register against
    a stub app and collecting every `CommandHandler.commands`. An earlier
    version scraped the source with a regex for `CommandHandler("literal"`
    and was blind to the loop at `lifecycle.py:264-271`, which registers
    `restart`/`rename`/`delete`/`diff` through a variable — the reviewer
    proved it by deleting `restart` from the reserved set and watching every
    test still pass.
    """
    from unittest.mock import AsyncMock, MagicMock

    from telegram.ext import CommandHandler

    bot = mk_bot()
    registered = []
    stub = MagicMock()
    stub.add_handler = MagicMock(side_effect=lambda h, *a, **k: registered.append(h))
    stub.bot = MagicMock()
    stub.bot.set_my_commands = AsyncMock()
    stub.initialize = AsyncMock()
    stub.start = AsyncMock()
    stub.updater = MagicMock()
    stub.updater.start_polling = AsyncMock()
    # `start()` REPLACES self._app with builder.build(), so the stub has to
    # be handed in at the builder — and this keeps the real builder (and the
    # bot token it would read) entirely out of the test.
    bot._make_builder = MagicMock(
        return_value=MagicMock(build=MagicMock(return_value=stub)))

    try:
        run_async(bot.start())
    except Exception:
        pass        # we only need the registrations, not a running bot

    dispatched = set()
    for h in registered:
        if isinstance(h, CommandHandler):
            dispatched |= set(h.commands)
    assert dispatched, "no CommandHandler registered — the stub is wrong"
    assert {"restart", "rename", "delete", "diff"} <= dispatched, (
        "the loop-registered commands were not collected — this guard is "
        "blind again")

    unreserved = dispatched - _RESERVED
    assert not unreserved, f"dispatched but shadowable: {sorted(unreserved)}"


def test_the_cli_subcommand_verbs_are_reserved():
    """`aipager session ls` dispatches to the list verb before it ever
    looks for a session, so a session named `ls` is unreachable there."""
    for verb in ("ls", "list", "kill"):
        assert verb in _RESERVED, f"{verb} is a CLI verb but not reserved"


# ---- leak 1: Telegram could create a name the CLI treats as a verb ----

def test_telegram_cannot_create_a_session_named_after_a_cli_verb():
    for verb in ("ls", "list"):
        clean, err = validate_session_name(verb)
        assert clean == "" and err, (
            f"a session named {verb!r} is unreachable via `aipager session`")


# ---- leak 2: the CLI could create a name that shadows a bot command ---

def test_the_cli_cannot_create_a_session_named_after_a_bot_command():
    for name in ("status", "settings", "diff"):
        assert _validate_name(name) is not None, (
            f"`aipager session {name}` would shadow /{name} in Telegram")


# ---- leak 3: commands added after the list was written ----------------

def test_commands_added_since_the_list_was_written_are_reserved():
    """`app`, `clearqueue`, `perms`, `resume` and `whoami` all postdate
    the original reserved set and were shadowable until now."""
    for name in ("app", "clearqueue", "perms", "resume", "whoami"):
        clean, err = validate_session_name(name)
        assert clean == "" and err, f"/{name} could be shadowed by a session"
        assert _validate_name(name) is not None, (
            f"the CLI would still create {name!r}")


# ---- interaction with normalisation (18c8b60) ------------------------

def test_a_name_reserved_only_after_normalising_is_still_caught(monkeypatch):
    """The claim is about ORDERING: normalise, then check reserved.

    Uses `STATUS` — reserved since long before this change — so the test
    fails only if that ordering regresses. An earlier version used `LS`
    and `Perms`, words this change newly reserved, so it went red under
    every mutation of the set for a reason that had nothing to do with
    ordering.

    `_resolve_dtach` is stubbed out because `launch()` is only stopped
    from spawning a REAL dtach + claude by the very gate under test: if
    a future edit weakens it, this test must fail, not fork a process
    against `/tmp/claude-dtach-status.sock`.
    """
    from aipager.dtach import launcher

    monkeypatch.setattr(launcher, "_resolve_dtach", lambda: None)

    clean, err = validate_session_name("STATUS")
    assert clean == "" and err, "uppercase reserved word slipped past chat"

    assert launcher.launch("STATUS") == 2, (
        "the CLI accepted a capitalised reserved name")


# ---- the tightening must not reach backwards -------------------------

def test_reserving_a_name_does_not_disturb_an_existing_session():
    """The operator has a live session called `ls`. Reserving the word
    must refuse NEW ones, never retroactively rename or hide theirs —
    validation runs at creation and rename only, and nothing walks the
    registry looking for now-illegal names.

    Honest note: this passes on the old code too, and stays green under
    every mutation of the reserved set — there is no code to break it
    today. It is a FORWARD guard, so that a future "clean up illegal
    names on load" idea fails here instead of in someone's chat.
    """
    from aipager.state import SessionRegistry, Status

    registry = SessionRegistry()
    sess = registry.get_or_create("claude-ls")
    sess.label = "ls"
    registry.transition("claude-ls", Status.IDLE)

    assert registry.find_by_label("ls", None) is not None, (
        "an existing session named after a now-reserved word vanished")
    assert registry.get("claude-ls").label == "ls"


def test_an_existing_session_named_after_a_reserved_word_can_still_relaunch(
        monkeypatch):
    """The regression this change originally shipped with.

    Growing the reserved set reaches further than creation: /restart,
    /perms, /resume and the replace-on-conflict flow all kill and
    re-launch an EXISTING session through `launch_session` under its own
    name. Gated on the reserved set, they stranded any session created
    before its name became reserved — the operator's live `ls` session
    could no longer be restarted at all.
    """
    import asyncio

    from aipager.dtach import inject

    # Stop the call at the "already exists" branch, which sits just past
    # the reserved gate and well before anything is spawned. Without this
    # the test only avoids forking a real dtach + claude because THIS
    # machine happens to have a live /tmp/claude-dtach-ls.sock; on CI, or
    # any box without it, it would launch a real session. The reviewer
    # proved that by intercepting create_subprocess_exec.
    monkeypatch.setattr(inject.Path, "is_socket", lambda self: True)

    _, err = asyncio.new_event_loop().run_until_complete(
        inject.launch_session("ls", resume_id="x", cwd="/tmp", is_relaunch=True))
    assert "reserved" not in err, (
        f"relaunching an existing reserved-named session was refused: {err}")
    assert "already exists" in err, (
        f"expected to stop at the existence check, got: {err!r}")

    _, err = asyncio.new_event_loop().run_until_complete(
        inject.launch_session("ls"))
    assert "reserved" in err, "creation must still refuse a reserved name"


def test_the_mini_apps_javascript_copy_matches_the_canonical_set():
    """A FOURTH copy of the list lives in the Mini App's client JS, for
    instant feedback in the rename box before the server round-trip.

    It is only a UX nicety — the server gate is what actually refuses —
    but it had already gone stale, listing seven words when the canonical
    set held eleven, so the box accepted names the server then rejected.
    Pinned here because "keep them in sync by remembering" is precisely
    the discipline that failed twice already in this file's history.
    """
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent
           / "aipager" / "miniapp" / "static" / "_app.py").read_text()
    block = re.search(r"var RENAME_RESERVED = \{(.*?)\};", src, re.S)
    assert block, "RENAME_RESERVED not found — did the JS move?"
    js_words = set(re.findall(r'"([a-z]+)"\s*:\s*true', block.group(1)))

    assert js_words == _RESERVED, (
        f"client list drifted — missing {sorted(_RESERVED - js_words)}, "
        f"extra {sorted(js_words - _RESERVED)}")
