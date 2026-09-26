"""Error guessing across the contract: slow networks, concurrent taps,
hostile installer output, cross-chat taps, the status cache, and env
pinning. Each test names the success criterion it defends."""

from __future__ import annotations

import os
import re
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import install_source, self_update
from aipager.bot import update_flow
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry


# ---- SC-1: the /update handler must not block on slow lookups -----------------

def test_update_handler_returns_before_slow_lookups_finish(world, personal_bot, mk_update, h, run_async, monkeypatch):
    real = world._http_get

    def slow(url, *, timeout, max_bytes):
        time.sleep(1.0)
        return real(url, timeout=timeout, max_bytes=max_bytes)
    monkeypatch.setattr(self_update, "_http_get", slow)
    upd = mk_update("/update", user_id=h.OPERATOR, chat_id=h.DM)
    upd.message.chat = MagicMock()
    upd.message.chat.id = h.DM
    msg = h.status_message(h.DM, 900)
    upd.message.reply_text.return_value = msg
    upd.effective_message = upd.message

    async def go():
        t0 = time.monotonic()
        await update_flow.handle_update_cmd(personal_bot, upd, MagicMock())
        took = time.monotonic() - t0
        await h.wait_for(lambda: msg.edit_text.await_count > 0, 5)
        return took
    assert run_async(go()) < 0.5


def test_slow_lookups_run_concurrently(world, personal_bot, h, run_async, monkeypatch):
    """Two 0.6 s lookups (PyPI + Claude channel) should not take 1.2 s."""
    real = world._http_get

    def slow(url, *, timeout, max_bytes):
        time.sleep(0.6)
        return real(url, timeout=timeout, max_bytes=max_bytes)
    monkeypatch.setattr(self_update, "_http_get", slow)

    async def go():
        t0 = time.monotonic()
        await personal_bot.updates.status(force=True)
        return time.monotonic() - t0
    assert run_async(go()) < 1.1


def test_lookups_pass_the_http_timeout(world, personal_bot, h, run_async, monkeypatch):
    seen = []
    real = world._http_get

    def spy(url, *, timeout, max_bytes):
        seen.append(timeout)
        return real(url, timeout=timeout, max_bytes=max_bytes)
    monkeypatch.setattr(self_update, "_http_get", spy)
    run_async(personal_bot.updates.status(force=True))
    assert seen and all(t == self_update.HTTP_TIMEOUT_SECONDS for t in seen)


def test_status_cache_bounds_outbound_calls(world, personal_bot, h, run_async, monkeypatch):
    monkeypatch.setattr(self_update, "STATUS_CACHE_SECONDS", 60)

    async def go():
        await personal_bot.updates.status()
        await personal_bot.updates.status()
        await personal_bot.updates.status()
    run_async(go())
    assert sum("pypi.org" in u for u in world.urls) == 1


def test_force_bypasses_the_status_cache(world, personal_bot, h, run_async, monkeypatch):
    monkeypatch.setattr(self_update, "STATUS_CACHE_SECONDS", 60)

    async def go():
        await personal_bot.updates.status()
        await personal_bot.updates.status(force=True)
    run_async(go())
    assert sum("pypi.org" in u for u in world.urls) == 2


def test_only_documented_hosts_are_fetched(world, personal_bot, h, run_async):
    run_async(personal_bot.updates.status(force=True))
    hosts = {re.sub(r"^https://([^/]+)/.*$", r"\1", u) for u in world.urls}
    assert hosts <= {"pypi.org", "downloads.claude.ai", "registry.npmjs.org"}, hosts


@pytest.mark.parametrize("channel,expected_suffix", [
    ("latest", "/latest"), ("stable", "/stable"), ("rc", "/rc"), ("bogus", "/latest"),
])
def test_native_claude_channel_is_honoured(world, personal_bot, h, run_async, channel, expected_suffix):
    from aipager import claude_bootstrap
    s = claude_bootstrap._SETTINGS
    s.parent.mkdir(parents=True, exist_ok=True)
    s.write_text('{"autoUpdatesChannel": "%s"}' % channel)
    run_async(personal_bot.updates.status(force=True))
    claude_urls = [u for u in world.urls if "downloads.claude.ai" in u]
    assert claude_urls and claude_urls[0].endswith(expected_suffix), claude_urls


def test_npm_install_uses_npm_dist_tags(world, personal_bot, h, run_async):
    from aipager import claude_bootstrap
    claude_bootstrap._CLAUDE_JSON.write_text('{"installMethod": "npm-global"}')
    run_async(personal_bot.updates.status(force=True))
    assert any("registry.npmjs.org" in u for u in world.urls)


def test_npm_rc_channel_maps_to_next_tag(world, personal_bot, h, run_async, monkeypatch):
    from aipager import claude_bootstrap
    claude_bootstrap._CLAUDE_JSON.write_text('{"installMethod": "npm-global"}')
    s = claude_bootstrap._SETTINGS
    s.parent.mkdir(parents=True, exist_ok=True)
    s.write_text('{"autoUpdatesChannel": "rc"}')
    monkeypatch.setattr(self_update, "_http_get", lambda url, *, timeout, max_bytes:
                        b'{"latest": "2.1.1", "stable": "2.1.0", "next": "2.2.0"}')
    status = run_async(personal_bot.updates.status(force=True))
    assert status["claude"]["latest"] == "2.2.0"


def test_unknown_claude_install_method_is_unknown(world, personal_bot, h, run_async):
    from aipager import claude_bootstrap
    claude_bootstrap._CLAUDE_JSON.write_text('{}')
    world.claude_path = "/opt/elsewhere/claude"
    status = run_async(personal_bot.updates.status(force=True))
    assert status["claude"]["latest"] is None


# ---- SC-9: concurrent taps --------------------------------------------------

def test_two_simultaneous_starts_yield_one_job(world, personal_bot, h, run_async):
    import asyncio

    async def go():
        a, b = await asyncio.gather(
            personal_bot.updates.start("claude", chat_id=h.DM, user_id=h.OPERATOR,
                                       origin="chat", status_message=h.status_message(h.DM, 1)),
            personal_bot.updates.start("claude", chat_id=h.DM, user_id=h.OPERATOR,
                                       origin="miniapp"))
        await h.wait_phase(personal_bot, h.TERMINAL)
        await h.wait_for(lambda: False, 0.1)
        return a, b
    a, b = run_async(go())
    assert sorted([a.ok, b.ok]) == [False, True] and len(world.claude_update_calls()) == 1


def test_double_tap_update_aipager_runs_one_installer(world, personal_bot, h, run_async):
    """8.43: the tap is now the one Update button (`_:up:go:ap`) after a
    check; was the retired per-product `_:up:ap`."""
    world.claude_latest = world.claude_version     # only aipager is newer

    def tap(data="_:up:go:ap"):
        q = MagicMock()
        q.data = data
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        q.edit_message_reply_markup = AsyncMock()
        q.message = h.status_message(h.DM, 42)
        q.message.text = ""
        q.from_user = MagicMock()
        q.from_user.id = h.OPERATOR
        u = MagicMock()
        u.callback_query = q
        u.effective_user = q.from_user
        u.effective_chat = MagicMock()
        u.effective_chat.id = h.DM
        u.effective_chat.type = "private"
        return u

    async def go():
        await personal_bot._handle_callback(tap("_:up:chk"), MagicMock())
        await h.wait_for(lambda: personal_bot.updates._checked is not None, 5)
        await personal_bot._handle_callback(tap(), MagicMock())
        await personal_bot._handle_callback(tap(), MagicMock())
        await h.wait_phase(personal_bot, h.TERMINAL)
        await h.wait_for(lambda: False, 0.1)
    run_async(go())
    assert len(world.upgrade_calls()) == 1


# ---- hostile installer output ---------------------------------------------

def test_html_in_installer_output_is_escaped(world, personal_bot, h, run_async):
    """parse_mode=HTML: a raw '<' from pip would make Telegram reject the
    outcome edit, losing the result."""
    world.upgrade_rc = 1
    world.upgrade_output = "ERROR: <urlopen error [Errno -3]> & friends"
    _, msg, _ = run_async(h.run_job(personal_bot, "aipager"))
    text = h.all_text(personal_bot, msg)
    assert "<urlopen error" not in text and "&lt;urlopen error" in text


def test_html_in_session_label_is_escaped(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "a<b>c")
    _, msg, _ = run_async(h.run_job(personal_bot, "claude"))
    text = h.all_text(personal_bot, msg)
    assert "a<b>c" not in text and "a&lt;b&gt;c" in text


def test_long_installer_output_keeps_the_tail(world, personal_bot, h, run_async):
    world.upgrade_rc = 1
    world.upgrade_output = "x" * 6000 + "TAIL-MARK"
    _, msg, _ = run_async(h.run_job(personal_bot, "aipager"))
    assert "TAIL-MARK" in h.all_text(personal_bot, msg)


# ---- group chats: paths never shown -----------------------------------------

class _Role:
    def __init__(self, admin):
        self.bypass_safety = admin
        self.can_prompt = True


class _Policy:
    def get_role(self, name):
        return _Role(name == "admin")


@pytest.fixture
def two_scope_bot(mk_bot, h):
    a = Scope(chat_id=-100, kind="group", label="A", members=(
        Member(id=555, label="ada", role="admin"),))
    b = Scope(chat_id=-200, kind="group", label="B", members=(
        Member(id=555, label="ada", role="developer"),))
    bot = mk_bot(SessionRegistry(), scopes=[a, b])
    bot.policy = _Policy()
    bot._app.bot.send_message = AsyncMock(
        side_effect=lambda *a, **kw: h.status_message(kw.get("chat_id", -100), 801))
    return bot


def test_group_outcome_omits_local_source_path(world, two_scope_bot, h, run_async):
    """8.46: a local-folder install is refused before any installer runs; the
    refusal a group sees still never names the folder."""
    world.origin = "local"
    with pytest.raises(AssertionError, match="not_upgradable"):
        run_async(h.run_job(two_scope_bot, "aipager", chat_id=-100, user_id=555,
                            msg=h.status_message(-100)))
    assert world.local_path not in " ".join(map(str, world.calls))


def test_admin_of_one_scope_is_refused_in_a_scope_where_not_admin(world, two_scope_bot, h, run_async):
    q = MagicMock()
    q.data = "_:up:go:cc"
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.message = h.status_message(-200, 42)
    q.message.text = ""
    q.from_user = MagicMock()
    q.from_user.id = 555
    u = MagicMock()
    u.callback_query = q
    u.effective_user = q.from_user
    u.effective_chat = MagicMock()
    u.effective_chat.id = -200
    u.effective_chat.type = "supergroup"

    async def go():
        await two_scope_bot._handle_callback(u, MagicMock())
        await h.wait_for(lambda: False, 0.15)
    run_async(go())
    assert world.claude_update_calls() == []


# ---- env pinning --------------------------------------------------------------

def test_uv_env_pins_tool_dir_to_this_install(world, personal_bot, h, run_async):
    world.source_kind = "uv"
    run_async(h.run_job(personal_bot, "aipager"))
    env = world.upgrade_calls()[0]["env"] or {}
    assert os.path.normpath(env.get("UV_TOOL_DIR", "")) == \
        os.path.normpath(os.path.join(world.prefix, ".."))


def test_installer_env_path_is_augmented(world, personal_bot, h, run_async, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "hh"))
    monkeypatch.setenv("PATH", "")
    run_async(h.run_job(personal_bot, "aipager"))
    env = world.upgrade_calls()[0]["env"] or {}
    assert str(tmp_path / "hh" / ".local" / "bin") in env.get("PATH", "").split(os.pathsep)


def test_installer_env_drops_anthropic_keys(world, personal_bot, h, run_async, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-fake")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-ant-auth-fake")
    run_async(h.run_job(personal_bot, "aipager"))
    env = world.upgrade_calls()[0]["env"] or {}
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_AUTH_TOKEN" not in env


def test_claude_update_env_drops_bot_token(world, personal_bot, h, run_async, monkeypatch):
    monkeypatch.setenv("CLAUDE_TG_BOT_TOKEN", "123456789:AAHfakeTokenValueForTestsOnly_abcdefghij")
    run_async(h.run_job(personal_bot, "claude"))
    env = world.claude_update_calls()[0]["env"]
    assert env is not None and "CLAUDE_TG_BOT_TOKEN" not in env


def test_real_resolve_tool_returns_absolute_or_none(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    monkeypatch.setenv("PATH", "")
    got = install_source.resolve_tool("definitely-not-a-tool-xyz")
    assert got is None


# ---- logging ---------------------------------------------------------------

def test_job_logs_stable_event_names(world, personal_bot, h, run_async, caplog):
    import logging
    with caplog.at_level(logging.INFO):
        run_async(h.run_job(personal_bot, "aipager"))
    assert "update.aipager.start" in caplog.text and "update.restart.scheduled" in caplog.text


def test_denied_attempt_is_logged(world, personal_bot, mk_update, h, run_async, caplog):
    import logging
    upd = mk_update("/update", user_id=h.STRANGER, chat_id=h.STRANGER)
    upd.message.chat = MagicMock()
    upd.message.chat.id = h.STRANGER
    upd.effective_message = upd.message
    with caplog.at_level(logging.INFO):
        run_async(update_flow.handle_update_cmd(personal_bot, upd, MagicMock()))
    assert "update.denied" in caplog.text


def test_logs_never_contain_bot_token(world, personal_bot, h, run_async, caplog, monkeypatch):
    import logging
    token = "123456789:AAHfakeTokenValueForTestsOnly_abcdefghij"
    monkeypatch.setenv("CLAUDE_TG_BOT_TOKEN", token)
    world.upgrade_rc = 1
    with caplog.at_level(logging.DEBUG):
        run_async(h.run_job(personal_bot, "aipager"))
    assert token not in caplog.text
