"""Roadmap 8.45: the "installed" message promises no time, and the new
daemon edits it into the outcome instead of posting a second message.

Live, on the operator's daemon (2026-09-25): "Restarting in 5 s…" was
followed ~44 s later by the restart (the timer's default AccuracySec) and
then by a SECOND message, "✅ aipager updated 0.7.17 → 0.7.18, …".
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

from telegram.error import BadRequest

from aipager import self_update
from aipager.bot import update_flow
from aipager.bot.flood import MUTE
from aipager.state import Status

INSTALLED = "⏳ <b>aipager</b> 0.7.13 → 0.7.14 installed, restarting…"
WAITING = ("⏳ <b>aipager</b> 0.7.13 → 0.7.14 installed, restarting when the "
           "current turn ends")
UPDATED = "✅ aipager updated 0.7.13 → 0.7.14, 1 session re-adopted"


def _marker(env, **over):
    data = {"from": "0.7.13", "to": "0.7.14", "chat_id": env.chat_id,
            "user_id": env.chat_id, "scheduled_at": time.time(),
            "sessions": [{"name": "claude-dev", "label": "dev"}],
            "message": {"chat_id": env.chat_id, "message_id": 4711, "prefix": ""}}
    data.update(over)
    self_update.write_marker(data)


def _deliver(env, run, edit=None):
    env.bot._app.bot.edit_message_text = edit or AsyncMock(return_value=env.message)

    async def scenario():
        await update_flow.deliver_update_marker(env.bot, env.registry)
    run(scenario)
    return env.bot._app.bot.edit_message_text


# ---- the old daemon: what it says, and what it remembers ---------------------

def test_installed_message_promises_no_countdown(env, run):
    async def scenario():
        await env.start("aipager")
        await env.finish()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "restart_scheduled"
    text = env.last_text()
    assert INSTALLED in text
    assert "5 s" not in text and "Restarting in" not in text
    assert "—" not in text.split("aipager</b>", 1)[1]


def test_marker_remembers_the_installed_message(env, run):
    async def scenario():
        await env.start("aipager")
        await env.finish()
    run(scenario)
    marker = json.loads(self_update.UPDATE_MARKER_PATH.read_text())
    assert marker["message"] == {"chat_id": env.chat_id, "message_id": 4711,
                                 "prefix": ""}


def test_marker_prefix_keeps_the_claude_code_section_of_update_both(env, run):
    async def scenario():
        await env.start("both")
        await env.finish()
    run(scenario)
    marker = json.loads(self_update.UPDATE_MARKER_PATH.read_text())
    assert "Claude Code</b> 2.1.281 → 2.1.290" in marker["message"]["prefix"]
    assert "installed" not in marker["message"]["prefix"]


def test_waiting_for_a_turn_says_so_then_says_restarting(env, run):
    """A turn that starts during the upgrade holds the restart: the message
    says it waits for that turn, and is updated once the restart is
    scheduled."""
    sess = env.add_session("claude-dev", "dev", Status.IDLE)
    env.upgrade_hook = lambda: setattr(sess, "status", Status.BUSY)

    async def scenario():
        await env.start("aipager")
        await env.until(lambda: WAITING in env.last_text())
        sess.status = Status.IDLE
        await env.finish()
    run(scenario)
    final = env.last_text()
    assert INSTALLED in final
    assert "current turn ends" not in final
    assert "no turn is running" not in final


def test_shutdown_while_waiting_for_a_turn_drops_the_waiting_line(env, run):
    """Stopped during the post-install wait: the marker (and the message)
    say B is installed and the next start runs it, not "restarting when
    the current turn ends" as well."""
    sess = env.add_session("claude-dev", "dev", Status.IDLE)
    env.upgrade_hook = lambda: setattr(sess, "status", Status.BUSY)

    async def scenario():
        await env.start("aipager")
        await env.until(lambda: WAITING in env.last_text())
        await env.manager.shutdown()
    run(scenario)
    marker = json.loads(self_update.UPDATE_MARKER_PATH.read_text())
    assert marker["message"]["prefix"] == ""
    final = env.last_text()
    assert "current turn ends" not in final
    assert "the next start runs 0.7.14" in final


# ---- the new daemon: edit, not a second message ------------------------------

def test_new_daemon_edits_the_installed_message(env, run):
    env.running = "0.7.14"
    env.add_session("claude-dev", "dev", Status.IDLE)
    _marker(env)
    edit = _deliver(env, run)
    assert env.sent == []
    edit.assert_awaited_once()
    kw = edit.await_args.kwargs
    assert kw["chat_id"] == env.chat_id and kw["message_id"] == 4711
    text = kw.get("text", edit.await_args.args[0] if edit.await_args.args else None)
    assert text == UPDATED
    assert "rate_limit_args" not in kw     # ESSENTIAL: the default class


def test_edit_keeps_the_earlier_sections(env, run):
    env.running = "0.7.14"
    env.add_session("claude-dev", "dev", Status.IDLE)
    _marker(env, message={"chat_id": env.chat_id, "message_id": 4711,
                          "prefix": "✅ <b>Claude Code</b> 2.1.281 → 2.1.290"})
    edit = _deliver(env, run)
    kw = edit.await_args.kwargs
    assert kw["text"] == "✅ <b>Claude Code</b> 2.1.281 → 2.1.290\n\n" + UPDATED
    assert kw["parse_mode"] == "HTML"


def test_edit_escapes_session_labels(env, run):
    env.running = "0.7.14"
    _marker(env, sessions=[{"name": "claude-x", "label": "a<b>"}])
    edit = _deliver(env, run)
    assert "Not back: a&lt;b&gt;" in edit.await_args.kwargs["text"]


def test_failed_edit_falls_back_to_a_send(env, run):
    env.running = "0.7.14"
    env.add_session("claude-dev", "dev", Status.IDLE)
    _marker(env)
    edit = _deliver(env, run, AsyncMock(side_effect=BadRequest("Message to edit not found")))
    edit.assert_awaited_once()
    assert [s["text"] for s in env.sent] == [UPDATED]


def test_edit_to_the_same_text_is_not_resent(env, run):
    env.running = "0.7.14"
    env.add_session("claude-dev", "dev", Status.IDLE)
    _marker(env)
    _deliver(env, run, AsyncMock(side_effect=BadRequest(
        "Message is not modified: specified new message content and reply "
        "markup are exactly the same")))
    assert env.sent == []


def test_muted_chat_gets_neither_edit_nor_send(env, run, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="aipager.bot.update_flow")
    env.running = "0.7.14"
    MUTE.mute(env.chat_id, 600, source="test")
    _marker(env)
    edit = _deliver(env, run)
    edit.assert_not_awaited()
    env.bot._app.bot.send_message.assert_not_awaited()
    assert not self_update.UPDATE_MARKER_PATH.exists()
    # Logged as skipped, never as delivered.
    assert "update.marker.skipped_muted" in caplog.text
    assert "update.marker.delivered" not in caplog.text


def test_marker_without_a_message_still_sends(env, run):
    env.running = "0.7.14"
    env.add_session("claude-dev", "dev", Status.IDLE)
    _marker(env, message=None)
    edit = _deliver(env, run)
    edit.assert_not_awaited()
    assert [s["text"] for s in env.sent] == [UPDATED]


def test_marker_with_a_bad_message_ref_still_sends(env, run):
    env.running = "0.7.14"
    env.add_session("claude-dev", "dev", Status.IDLE)
    _marker(env, message={"chat_id": env.chat_id, "message_id": "x"})
    edit = _deliver(env, run)
    edit.assert_not_awaited()
    assert [s["text"] for s in env.sent] == [UPDATED]


def test_marker_with_a_bool_message_id_still_sends(env, run):
    env.running = "0.7.14"
    env.add_session("claude-dev", "dev", Status.IDLE)
    _marker(env, message={"chat_id": env.chat_id, "message_id": True})
    edit = _deliver(env, run)
    edit.assert_not_awaited()
    assert [s["text"] for s in env.sent] == [UPDATED]


def test_message_in_another_chat_is_never_edited(env, run):
    """The marker's own chat is the one that asked; a message ref pointing
    anywhere else is not trusted."""
    env.running = "0.7.14"
    env.add_session("claude-dev", "dev", Status.IDLE)
    _marker(env, message={"chat_id": env.chat_id + 1, "message_id": 4711})
    edit = _deliver(env, run)
    edit.assert_not_awaited()
    assert [s["chat_id"] for s in env.sent] == [env.chat_id]


def test_new_daemon_remembers_the_outcome_for_the_mini_app(env, run):
    env.running = "0.7.14"
    env.add_session("claude-dev", "dev", Status.IDLE)
    _marker(env)
    _deliver(env, run)
    assert env.manager.last_restart == {"from": "0.7.13", "to": "0.7.14",
                                        "readopted": 1, "missing": []}


# ---- GET /api/update: what the Mini App's restart watch reads ----------------

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


def _hdr(user_id):
    import hashlib
    import hmac
    from urllib.parse import urlencode

    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "T"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(fields)}


def _get_update(env, run, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    from aipager.miniapp.server import MiniAppServer

    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)
    srv = MiniAppServer(env.bot, env.registry, port=8765)

    async def scenario():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            r = await client.get("/api/update", headers=_hdr(env.chat_id))
            return r.status, await r.json()
        finally:
            await client.close()
    status, body = run(scenario)
    assert status == 200
    return body


def test_get_update_reports_version_and_installed_target(env, run, monkeypatch):
    async def scenario():
        await env.start("aipager")
        await env.finish()
    run(scenario)
    env.running = "0.7.13"
    body = _get_update(env, run, monkeypatch)
    assert body["version"] == "0.7.13"
    assert body["job"]["phase"] == "restart_scheduled"
    assert body["job"]["installed"] == {"from": "0.7.13", "to": "0.7.14"}
    assert body["restarted"] is None


def test_get_update_carries_the_restart_outcome(env, run, monkeypatch):
    env.manager.last_restart = {"from": "0.7.13", "to": "0.7.14", "readopted": 2,
                                "missing": []}
    body = _get_update(env, run, monkeypatch)
    assert body["restarted"]["readopted"] == 2


def test_quick_tunnel_url_changes_on_restart(env, run, monkeypatch):
    from aipager.miniapp import tunnel

    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    monkeypatch.setattr(tunnel, "_managed_tunnel_url", "https://a-b-c.trycloudflare.com")
    assert _get_update(env, run, monkeypatch)["url_changes_on_restart"] is True


def test_a_configured_public_url_is_stable(env, run, monkeypatch):
    from aipager.miniapp import tunnel

    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "https://box.example.ts.net/")
    monkeypatch.setattr(tunnel, "_managed_tunnel_url", "https://a-b-c.trycloudflare.com")
    assert _get_update(env, run, monkeypatch)["url_changes_on_restart"] is False


def test_no_managed_tunnel_is_stable(env, run, monkeypatch):
    from aipager.miniapp import tunnel

    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    monkeypatch.setattr(tunnel, "_managed_tunnel_url", "")
    assert _get_update(env, run, monkeypatch)["url_changes_on_restart"] is False

