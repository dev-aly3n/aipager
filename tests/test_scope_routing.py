"""Phase B: outbound notifications route to session.scope_chat_id (Layer 2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot.transport import resolve_chat_id, resolve_chat_id_int
from aipager.state import Status, TrackedSession


def _sess(scope_chat_id=0):
    s = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    s.scope_chat_id = scope_chat_id
    return s


# ---- resolve_chat_id ----------------------------------------------------

def test_resolve_returns_scope_chat_when_set(monkeypatch):
    from aipager import config
    monkeypatch.setattr(config, "CHAT_ID", "111")
    assert resolve_chat_id(_sess(scope_chat_id=999)) == 999


def test_resolve_falls_back_to_chat_id_str(monkeypatch):
    from aipager import config
    monkeypatch.setattr(config, "CHAT_ID", "111")
    # Unstamped session → original CHAT_ID string (preserves old behavior)
    assert resolve_chat_id(_sess(scope_chat_id=0)) == "111"


# ---- resolve_chat_id_int --------------------------------------------------

def test_resolve_int_casts_a_stamped_scope(monkeypatch):
    from aipager import config
    monkeypatch.setattr(config, "CHAT_ID", "111")
    assert resolve_chat_id_int(_sess(scope_chat_id=999)) == 999


def test_resolve_int_casts_a_numeric_chat_id_fallback(monkeypatch):
    from aipager import config
    monkeypatch.setattr(config, "CHAT_ID", "111")
    assert resolve_chat_id_int(_sess(scope_chat_id=0)) == 111


def test_resolve_int_returns_none_instead_of_raising_when_unresolvable(monkeypatch):
    """Unscoped session, no global CHAT_ID configured — a perfectly normal
    state (pre-wizard install, scope-only install that never set the
    legacy env var). Must degrade to ``None``, never raise."""
    from aipager import config
    monkeypatch.setattr(config, "CHAT_ID", "")
    assert resolve_chat_id_int(_sess(scope_chat_id=0)) is None


def test_resolve_int_returns_none_for_a_non_numeric_chat_id(monkeypatch):
    from aipager import config
    monkeypatch.setattr(config, "CHAT_ID", "not-a-number")
    assert resolve_chat_id_int(_sess(scope_chat_id=0)) is None


# ---- notify routing -----------------------------------------------------

def test_context_warning_routes_to_scope(mk_bot, run_async):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    sess = _sess(scope_chat_id=999)
    run_async(bot.notify(sess, "context_warning", {"context_pct": 90}))
    bot._app.bot.send_message.assert_awaited_once()
    assert bot._app.bot.send_message.await_args.args[0] == 999


def test_send_busy_routes_to_scope(mk_bot, run_async):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=7))
    sess = _sess(scope_chat_id=-4152307515)
    run_async(bot.send_busy(sess))
    assert bot._app.bot.send_message.await_args.args[0] == -4152307515


def test_cross_scope_no_bleed(mk_bot, run_async):
    """Two sessions in different scopes each notify only their own chat."""
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    a = _sess(scope_chat_id=111)
    a.name = "claude-a"
    b = _sess(scope_chat_id=222)
    b.name = "claude-b"
    run_async(bot.notify(a, "context_warning", {"context_pct": 90}))
    run_async(bot.notify(b, "context_warning", {"context_pct": 90}))
    chats = [c.args[0] for c in bot._app.bot.send_message.await_args_list]
    assert chats == [111, 222]


def test_unstamped_session_uses_chat_id(mk_bot, run_async, monkeypatch):
    from aipager import config
    monkeypatch.setattr(config, "CHAT_ID", "555")
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    sess = _sess(scope_chat_id=0)
    run_async(bot.notify(sess, "context_warning", {"context_pct": 90}))
    assert bot._app.bot.send_message.await_args.args[0] == "555"


# ---- CHAT_ID is derived from scopes once config.env is retired -----------

@pytest.fixture
def restore_config():
    """Reload aipager.config AFTER monkeypatch has undone its patches.

    A `finally: importlib.reload(...)` inside the test body runs while the
    patches are still live, so it reloads the fixture's view rather than the
    real one and leaves module-level config holding test values for the rest
    of the session. Fixture teardown is LIFO, so requesting this one before
    `monkeypatch` puts its restore last.
    """
    yield
    import importlib
    import aipager.config
    importlib.reload(aipager.config)


def _reload_config_with(monkeypatch, tmp_path, yaml_text):
    """Reload aipager.config against a throwaway aipager.yaml.

    `load_scopes(path=CONFIG_PATH)` binds its default at definition time, so
    patching the module attribute is not enough — the loader itself has to be
    redirected before config.py re-imports it.
    """
    import importlib
    from aipager import scope as scope_mod

    cfg = tmp_path / "aipager.yaml"
    cfg.write_text(yaml_text)
    real = scope_mod.load_scopes
    monkeypatch.setattr(scope_mod, "load_scopes",
                        lambda path=cfg: real(cfg))
    # Empty, not deleted: `_load_env_file` only fills keys that are ABSENT,
    # so deleting it lets a developer checkout's legacy `.env` repopulate the
    # value on reload — the very thing that hid this bug from real installs.
    monkeypatch.setenv("CLAUDE_TG_CHAT_ID", "")
    import aipager.config as config_mod
    return importlib.reload(config_mod), config_mod


def _yaml(*scopes: tuple[int, str]) -> str:
    out = "schema_version: 2\nbot_token: 123:abc\nscopes:\n"
    for chat_id, kind in scopes:
        out += (f"  - chat_id: {chat_id}\n"
                f"    kind: {kind}\n"
                f"    label: scope {chat_id}\n"
                f"    members:\n"
                f"      - id: 1\n"
                f"        label: owner\n"
                f"        role: owner\n")
    return out


def test_chat_id_derives_from_a_single_scope(restore_config, monkeypatch, tmp_path):
    """Found 2026-08-15: schema v2 made aipager.yaml authoritative for the
    bot token but nothing filled CHAT_ID, so `aipager status` and `doctor`
    reported a working install as unconfigured on every migrated setup.
    The id here is deliberately NOT this machine's, so the test cannot pass
    by accidentally reading the real config."""
    reloaded, _mod = _reload_config_with(monkeypatch, tmp_path, _yaml((424242, "dm")))
    assert reloaded.CHAT_ID == "424242"
    assert isinstance(reloaded.CHAT_ID, str), "callers do int(CHAT_ID)"


def test_chat_id_prefers_the_group_when_several_scopes(restore_config, monkeypatch, tmp_path):
    """Matches state._default_scope()'s rule so there is one definition of
    "the default chat", not two that can drift apart."""
    reloaded, _mod = _reload_config_with(
        monkeypatch, tmp_path, _yaml((111, "dm"), (-100222, "group")))
    assert reloaded.CHAT_ID == "-100222", "should prefer the group scope"


def test_explicit_chat_id_is_not_overridden(restore_config, monkeypatch, tmp_path):
    """A legacy install that still sets the env var keeps its value."""
    import importlib
    from aipager import scope as scope_mod

    cfg = tmp_path / "aipager.yaml"
    cfg.write_text(_yaml((999, "dm")))
    real = scope_mod.load_scopes
    monkeypatch.setattr(scope_mod, "load_scopes", lambda path=cfg: real(cfg))
    monkeypatch.setenv("CLAUDE_TG_CHAT_ID", "555")
    import aipager.config as config_mod
    reloaded = importlib.reload(config_mod)
    assert reloaded.CHAT_ID == "555"
