"""The Mini App is on by default, and its button is there at first glance.

Two changes, both operator decisions:

1. `_MINIAPP_DEFAULTS["enabled"]` flipped to True. An opt-in nobody
   discovers is a feature that does not exist — but this is a real
   security-posture change, because enabling the Mini App starts a
   public Cloudflare tunnel. An explicit `enabled: false` must therefore
   still win, absolutely.
2. The first keyboard is held until the tunnel URL lands, so the App
   button is on it. Previously the URL was resolved *before* `start()`,
   when no tunnel existed yet, so the first keyboard went out buttonless
   and only gained the button on some later unrelated refresh — which is
   why the Mini App felt like something you had to find by typing /app.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aipager import scope


def _cfg(tmp_path, block=None):
    p = tmp_path / "aipager.yaml"
    body = "schema_version: 3\nbot_token: x\nscopes: []\n"
    if block is not None:
        import yaml
        body += yaml.safe_dump({"miniapp": block})
    p.write_text(body)
    return p


# ---- change 1: the default ---------------------------------------------

def test_on_by_default_with_no_block(tmp_path):
    assert scope.load_miniapp(_cfg(tmp_path))["enabled"] is True


def test_an_explicit_false_still_wins(tmp_path):
    """The one that matters. A default flip that overrode a deliberate
    opt-out would be far worse than the problem it solves."""
    cfg = _cfg(tmp_path, {"enabled": False, "port": 8765})
    assert scope.load_miniapp(cfg)["enabled"] is False


@pytest.mark.parametrize("value", ["no", "false", "off", 0, None])
def test_explicit_non_true_values_still_read_as_off(tmp_path, value):
    """`bool("no")` is True, so a bare coercion would open a listening
    socket for a config plainly asking for the opposite."""
    cfg = _cfg(tmp_path, {"enabled": value, "port": 8765})
    assert scope.load_miniapp(cfg)["enabled"] is False


# ---- change 2: the button is there without asking -----------------------

def _bot(mk_bot, monkeypatch):
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "555")
    monkeypatch.setattr("aipager.bot.keyboards.CHAT_ID", "555")
    b = mk_bot()
    b._app.bot.set_chat_menu_button = AsyncMock()
    return b


def _keyboard_texts(bot):
    kb = bot._app.bot.send_message.await_args.kwargs["reply_markup"]
    return {btn.text for row in kb.keyboard for btn in row}


def test_the_held_keyboard_is_not_sent_before_the_url_arrives(
        mk_bot, run_async, monkeypatch):
    b = _bot(mk_bot, monkeypatch)
    b.defer_first_keyboard()
    run_async(b._update_bot_commands())
    assert b._app.bot.send_message.await_count == 0, (
        "sent a buttonless keyboard while still waiting for the tunnel")


def test_the_url_arriving_sends_the_keyboard_with_the_button(
        mk_bot, run_async, monkeypatch):
    b = _bot(mk_bot, monkeypatch)
    b.defer_first_keyboard()
    run_async(b._update_bot_commands())
    run_async(b.publish_miniapp_button("https://tunnel.example/"))
    assert "📱 App" in _keyboard_texts(b), "no App button at first glance"


def test_an_empty_publish_does_not_release_the_hold(
        mk_bot, run_async, monkeypatch):
    """The daemon publishes "" once, early, right after starting the
    tunnel manager. Releasing there would send exactly the buttonless
    keyboard this deferral exists to prevent."""
    b = _bot(mk_bot, monkeypatch)
    b.defer_first_keyboard()
    run_async(b._update_bot_commands())
    run_async(b.publish_miniapp_button(""))
    assert b._app.bot.send_message.await_count == 0


def test_the_timeout_releases_a_keyboard_no_tunnel_ever_claimed(
        mk_bot, run_async, monkeypatch):
    """A Mini App that never comes up may cost a button, never the whole
    keyboard."""
    b = _bot(mk_bot, monkeypatch)
    b.defer_first_keyboard()
    run_async(b._update_bot_commands())
    assert b._app.bot.send_message.await_count == 0

    run_async(b.release_deferred_keyboard())

    assert b._app.bot.send_message.await_count == 1
    assert "📱 App" not in _keyboard_texts(b)


def test_releasing_twice_sends_one_keyboard(mk_bot, run_async, monkeypatch):
    """Both the publish path and the timeout can fire; the user must not
    get two keyboards for it."""
    b = _bot(mk_bot, monkeypatch)
    b.defer_first_keyboard()
    run_async(b._update_bot_commands())
    run_async(b.release_deferred_keyboard())
    run_async(b.release_deferred_keyboard())
    assert b._app.bot.send_message.await_count == 1


def test_nothing_is_held_when_no_deferral_was_asked_for(
        mk_bot, run_async, monkeypatch):
    """The ordinary path — a configured public_url, or the Mini App off —
    must still send its keyboard immediately."""
    b = _bot(mk_bot, monkeypatch)
    run_async(b._update_bot_commands())
    assert b._app.bot.send_message.await_count == 1


def test_the_keyboard_timer_is_cancelled_at_shutdown(monkeypatch):
    """A 45s sleep must not outlive the daemon that started it.

    Not just tidiness: while this task was untracked, every daemon test
    that drains pending tasks sat for the full hold, and the suite went
    from 100s to 413s. Caught by watching the clock, not by any
    assertion — hence this one.
    """
    import asyncio

    from aipager.cli import daemon

    async def _drive():
        bot = type("B", (), {"release_deferred_keyboard": None})()
        task = asyncio.create_task(daemon._release_keyboard_after(bot, 45.0))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(asyncio.wait_for(_drive(), timeout=5))


def test_start_during_the_hold_does_not_produce_two_keyboards(
        mk_bot, run_async, monkeypatch):
    """`/start` reaches `_send_keyboard` directly, bypassing the hold
    check in `_update_bot_commands`. Without clearing the flag there,
    the user got a buttonless keyboard immediately AND a second one when
    the tunnel landed — the exact double-message the hold exists to
    avoid. Found in review, with a reproduction; none of my own tests
    covered this path.
    """
    b = _bot(mk_bot, monkeypatch)
    b.defer_first_keyboard()
    run_async(b._update_bot_commands())
    assert b._app.bot.send_message.await_count == 0

    run_async(b._send_keyboard(level="main"))       # what /start does
    assert b._app.bot.send_message.await_count == 1

    run_async(b.publish_miniapp_button("https://tunnel.example/"))
    assert b._app.bot.send_message.await_count == 1, (
        "a second keyboard followed the one /start already sent")


def test_a_group_target_is_never_held(monkeypatch):
    """A web_app button is private-chat-only, so holding a group's
    keyboard buys nothing and costs up to 45s of having none."""
    from aipager.cli import daemon

    monkeypatch.setattr("aipager.config.CHAT_ID", "-100123456")
    assert daemon._is_private_target() is False
    monkeypatch.setattr("aipager.config.CHAT_ID", "256113222")
    assert daemon._is_private_target() is True
    monkeypatch.setattr("aipager.config.CHAT_ID", "")
    assert daemon._is_private_target() is False


def test_dump_scopes_preserves_an_explicit_disable(tmp_path, monkeypatch):
    """`aipager config` rebuilds aipager.yaml from scratch and hand-carries
    only a few keys. The pre-existing preservation test pins the
    `enabled: True` case; this pins the one that actually matters — an
    operator who turned the Mini App OFF must not have it resurrected by
    re-running the wizard.
    """
    import yaml

    from aipager import scope

    cfg = tmp_path / "aipager.yaml"
    monkeypatch.setattr(scope, "CONFIG_PATH", cfg)
    cfg.write_text(yaml.safe_dump({
        "schema_version": 3,
        "bot_token": "x",
        "scopes": [],
        "miniapp": {"enabled": False, "port": 8765, "public_url": ""},
    }))

    scope.dump_scopes([], bot_token="x", path=cfg)

    assert scope.load_miniapp(cfg)["enabled"] is False, (
        "re-running the wizard turned the Mini App back on")


@pytest.mark.parametrize("level", ["templates", "commands", "models"])
def test_a_stale_sub_keyboard_tap_does_not_consume_the_hold(
        mk_bot, run_async, monkeypatch, level):
    """Tapping an old Templates/Commands/Models button after a restart
    lands in `_send_keyboard` too. Clearing the hold there would consume
    it without the user ever getting a main keyboard carrying the App
    button — a quieter version of the very bug this feature fixes.

    Found in review, after my first fix for the `/start` case cleared
    the flag on every level.
    """
    b = _bot(mk_bot, monkeypatch)
    b.defer_first_keyboard()
    run_async(b._update_bot_commands())

    run_async(b._send_keyboard(level=level))
    assert b._app.bot.send_message.await_count == 1

    run_async(b.publish_miniapp_button("https://tunnel.example/"))

    assert b._app.bot.send_message.await_count == 2, (
        "the hold was consumed by a sub-keyboard, so the main keyboard "
        "with the App button never arrived")
    assert "📱 App" in _keyboard_texts(b)


@pytest.mark.parametrize("from_level,lands_on_main", [
    ("templates", True),    # KEYBOARD_PARENTS: templates -> main
    ("commands", True),     #                   commands  -> main
    ("models", False),      #                   models    -> commands
])
def test_back_satisfies_the_hold_only_when_it_lands_on_main(
        mk_bot, run_async, monkeypatch, from_level, lands_on_main):
    """`« Back` goes to the *parent* of the current level, so whether it
    counts depends on where it lands: models -> commands is still a
    sub-keyboard and must NOT consume the hold.

    Routed through the real KEYBOARD_PARENTS mapping rather than calling
    `level="main"` directly — the first version of this test did the
    latter, which merely duplicated the /start test and passed even with
    the bug reverted.
    """
    from aipager.config import KEYBOARD_PARENTS

    b = _bot(mk_bot, monkeypatch)
    b.defer_first_keyboard()
    run_async(b._update_bot_commands())

    b._keyboard_level = from_level
    parent = KEYBOARD_PARENTS.get(b._keyboard_level, "main")
    assert (parent == "main") is lands_on_main, "fixture disagrees with config"
    run_async(b._send_keyboard(level=parent))       # what « Back does
    sent_by_back = b._app.bot.send_message.await_count

    run_async(b.publish_miniapp_button("https://tunnel.example/"))

    if lands_on_main:
        assert b._app.bot.send_message.await_count == sent_by_back, (
            "Back landed on main, so the release sent a second keyboard")
    else:
        assert b._app.bot.send_message.await_count == sent_by_back + 1, (
            "Back landed on a sub-keyboard, which must not consume the hold")
