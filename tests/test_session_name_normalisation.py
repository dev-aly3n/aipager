"""Session names are normalised on the way in, so every new session can
have a working Telegram ``/shortcut``.

Telegram bot commands must match ``[a-z0-9_]{1,32}``. Session names were
validated far more loosely (uppercase and hyphens allowed, up to 64
chars), so a legal name could be an illegal command — and before
``597d739`` one such name broke the whole command menu. That commit made
the menu survive; this one removes the mismatch at source by lowercasing
and mapping ``-`` to ``_`` before the name is validated or used.

Every entry point is asserted SEPARATELY. They do not share one
validator today — there are three independent ones — so a single test on
the shared helper would prove nothing about the paths that bypass it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot.lifecycle import _label_command
from aipager.dtach.inject import normalize_session_name
from aipager.miniapp.launch import validate_session_name


# ---- the rule itself ---------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("UiOp", "uiop"),
    ("HjIo", "hjio"),
    ("my-session", "my_session"),
    ("My-Mixed-Name", "my_mixed_name"),
    ("already_fine", "already_fine"),
    ("  padded  ", "padded"),
    ("digits123", "digits123"),
])
def test_normalisation_rule(raw, expected):
    assert normalize_session_name(raw) == expected


def test_a_normalised_name_always_earns_a_telegram_shortcut():
    """The whole point: what comes out must be something Telegram will
    accept as a command, so the `_label_command` backstop stops firing."""
    for raw in ("UiOp", "My-Session", "ABC"):
        assert _label_command(normalize_session_name(raw)) is not None, raw


# ---- entry point 1: the shared validator (wizard, Mini App, /rename) ---

def test_validate_session_name_returns_the_normalised_name():
    clean, err = validate_session_name("UiOp")

    assert err == ""
    assert clean == "uiop", "the caller must receive the name it will really get"


def test_validate_session_name_maps_hyphens():
    clean, err = validate_session_name("my-session")

    assert err == "" and clean == "my_session"


def test_a_reserved_word_is_caught_after_normalisation():
    """`Status` normalises to `status`, which IS reserved.

    Honest note: this passes on the OLD code too, because both reserved
    checks already compared `name.lower()`. It is a regression guard for
    that behaviour under the new normalisation, not evidence for it —
    the mutation that proves normalisation is load-bearing is
    `test_validate_session_name_returns_the_normalised_name`.
    """
    clean, err = validate_session_name("Status")

    assert clean == "" and err, "a capitalised reserved word slipped through"
    assert "reserved" in err.lower()


def test_a_name_that_normalises_to_nothing_is_still_rejected():
    """Regression guard, not evidence: whitespace-only input was already
    rejected before normalisation existed (proved vacuous by mutation).
    Kept so normalisation cannot start letting it through."""
    clean, err = validate_session_name("   ")

    assert clean == "" and err


def test_a_name_made_invalid_only_by_normalising_is_rejected():
    """This one IS specific to the new rule. `-A` starts with a hyphen,
    which `_VALID_NAME` rejects outright; but `A-` normalises to `a_`,
    which is legal — so the check has to run on the normalised form, and
    normalising must not manufacture a name that then fails."""
    clean, err = validate_session_name("A-")
    assert err == "" and clean == "a_", (
        "a trailing hyphen normalises to a legal name and must be kept")

    clean, err = validate_session_name("-A")
    assert clean == "" and err, "a leading hyphen is still not a legal start"


# ---- entry point 2: /new !name ----------------------------------------

def test_new_command_creates_the_normalised_name(mk_bot, mk_update, run_async):
    """`/new !UiOp` must create `uiop`, not `UiOp`."""
    bot = mk_bot()
    bot.create_session = AsyncMock(return_value=("claude-uiop", ""))
    update = mk_update("/new !UiOp")

    run_async(bot._handle_new_cmd(update, MagicMock()))

    bot.create_session.assert_awaited()
    assert bot.create_session.await_args.args[0] == "uiop", (
        f"/new passed {bot.create_session.await_args.args[0]!r} through "
        "un-normalised")


def test_new_command_still_strips_the_auto_mode_prefix(mk_bot, mk_update,
                                                       run_async):
    """The leading `!` selects Auto mode — it must be removed before
    normalisation, never lowercased into the name."""
    bot = mk_bot()
    bot.create_session = AsyncMock(return_value=("claude-uiop", ""))
    update = mk_update("/new !UiOp")

    run_async(bot._handle_new_cmd(update, MagicMock()))

    name = bot.create_session.await_args.args[0]
    assert not name.startswith("!") and name == "uiop"


def test_new_command_rejects_a_capitalised_reserved_word(mk_bot, mk_update,
                                                         run_async):
    """Also passes pre-change (`_handle_new_cmd` already lowercased for
    its `_RESERVED` test) — kept as a regression guard, not as proof."""
    bot = mk_bot()
    bot.create_session = AsyncMock(return_value=("claude-status", ""))
    update = mk_update("/new !Status")

    run_async(bot._handle_new_cmd(update, MagicMock()))

    bot.create_session.assert_not_awaited()


# ---- entry point 3: the CLI (`aipager session <name>`) ----------------

def test_cli_normalises_the_name_it_creates(monkeypatch, tmp_path):
    """`aipager session UiOp` must create `uiop`."""
    from aipager.dtach import launcher

    # `launch()` validates the RAW name first (so a too-long argument
    # cannot reach a filesystem probe), then resolves, then validates the
    # resolved name. Let the first call through and stop at the second,
    # which is the one carrying the name the session will really get.
    calls = []

    def fake_validate(name):
        calls.append(name)
        return None if len(calls) == 1 else "stop here"

    monkeypatch.setattr(launcher, "_validate_name", fake_validate)
    monkeypatch.setattr(launcher, "_socket_alive", lambda sock: False)

    launcher.launch("UiOp")

    assert calls[-1] == "uiop", (
        f"the CLI passed {calls[-1]!r} through un-normalised")


def test_cli_still_reattaches_to_an_existing_capitalised_session(monkeypatch):
    """Grandfathered sessions keep their names, and the CLI both creates
    AND reattaches — so `aipager session HjIo` must still find the live
    `HjIo` session rather than normalising past it and starting a second.
    """
    from aipager.dtach import launcher

    calls = []

    def fake_validate(name):
        calls.append(name)
        return None if len(calls) == 1 else "stop here"

    monkeypatch.setattr(launcher, "_validate_name", fake_validate)
    # A live socket exists for the name exactly as typed.
    monkeypatch.setattr(launcher.Path, "exists", lambda self: True)
    monkeypatch.setattr(launcher, "_socket_alive", lambda sock: True)

    launcher.launch("HjIo")

    assert calls[-1] == "HjIo", (
        "normalising past a live session would strand it and start a "
        "second one alongside")


def test_an_absurdly_long_cli_name_fails_cleanly_not_with_a_stack_trace():
    """`_resolve_launch_name` stats a path built from the raw argument, and
    `Path.exists()` does not swallow ENAMETOOLONG — so validating after
    resolving turned a 300-character name into an uncaught OSError and a
    "aipager hit an unexpected error" bug prompt. It must stay the clean
    "1-50 chars" rejection it was before."""
    from aipager.dtach import launcher

    assert launcher.launch("a" * 300) == 2


def test_a_dead_socket_under_the_old_spelling_is_reported_not_silent(
        monkeypatch, capsys):
    """Creating `hjio` while a stale `HjIo.sock` lingers is correct, but
    doing it silently strands a file nobody can later explain."""
    from aipager.dtach import launcher

    monkeypatch.setattr(launcher, "_validate_name", lambda n: "stop here")
    monkeypatch.setattr(launcher.Path, "exists", lambda self: True)
    monkeypatch.setattr(launcher, "_socket_alive", lambda sock: False)

    assert launcher._resolve_launch_name("HjIo") == "hjio"
    out = capsys.readouterr().out
    assert "HjIo" in out and "hjio" in out, (
        f"the rename went unmentioned: {out!r}")


def test_cli_rejects_a_reserved_subcommand_reached_only_by_normalising(
        monkeypatch):
    """`inject._RESERVED` holds lowercase literals, so `LS` passes the
    first (raw) validation and is caught only by the second, on the
    normalised name. This is what makes that second check live code
    rather than the dead branch its comment once claimed.

    `_resolve_dtach` is stubbed because the ONLY thing stopping
    `launch()` from spawning a real dtach + claude here is the very
    check under test. A reviewer proved that by dropping `ls`/`kill`
    from the reserved set: this test reached a fully-formed
    `dtach -n /tmp/claude-dtach-ls.sock -Ez env … claude …` before being
    aborted. `dtach` is a real binary on any box with `dtach-bin`, so a
    weakened guard would have left live processes behind on CI instead
    of failing.
    """
    from aipager.dtach import launcher

    monkeypatch.setattr(launcher, "_resolve_dtach", lambda: None)

    assert launcher.launch("LS") == 2
    assert launcher.launch("Kill") == 2
