"""Roadmap 8.121, end to end: a background agent outlives Claude's turn.

The replay: a Telegram prompt, a background agent starts, Claude's turn
stops while it runs, the agent stops, Claude wakes with a
``<task-notification>`` prompt, and that turn stops. At each step every
surface shows the same state with the same counts: the pinned bar, the
Mini App's ``GET /api/sessions``, the CLI's rows (from the file the
registry writes on its next ``save_if_dirty()``) and the busy card.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer

from aipager import status as cli_status
from aipager.bot.animation import build_stream_card
from aipager.dtach import hook_receiver as hr
from aipager.miniapp.server import MiniAppServer
from aipager.state import Status
from tests.footer_text import as_md

# The same values as this directory's conftest (which cannot be imported).
CHAT = -100
SESSION = "frontend"

AGENT = "a0f1e2d3c4b5"
PROMPT = "[via Telegram msg=3420]\nreview the release notes in the background"
CONTINUATION = (f"<task-notification>\n<task-id>{AGENT}</task-id>\n"
                "Background agent finished.")


async def _hook(recv, **fields):
    await recv._on_datagram(json.dumps({"session": SESSION, **fields}).encode())


def _surfaces(bot, client_rows, monkeypatch) -> dict:
    sess = bot.registry.get(SESSION)
    bar = bot._render_pinned(CHAT, [sess])[0]
    api = next(r for r in client_rows if r["label"] == SESSION)
    bot.registry.save_if_dirty()
    monkeypatch.setattr(cli_status, "_live_sessions", lambda: {SESSION})
    monkeypatch.setattr(cli_status, "_read_statusline", lambda name: {})
    rows, _ = cli_status._gather_sessions()
    cli = next(r for r in rows if r["name"] == SESSION)
    card = as_md(build_stream_card(sess, "Working", waiting=True).splitlines()[-1])
    return {"shown": sess.shown_state(), "bar": bar,
            "api": (api["status"], api["job_agents"], api["job_shells"]),
            "cli": (cli["status"], cli["job_agents"], cli["job_shells"]),
            "cli_text": cli_status.tasks_text(cli), "card": card}


def test_a_background_agent_that_outlives_the_turn(bot, run_async, monkeypatch,
                                                    init_header):
    server = MiniAppServer(bot, bot.registry, port=8766)
    recv = hr.HookReceiver(bot.registry, bot.notify)
    seen: dict[str, dict] = {}

    async def _api_rows(client):
        resp = await client.get("/api/sessions", headers=init_header())
        assert resp.status == 200, await resp.text()
        return (await resp.json())["sessions"]

    async def _drive():
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            await _hook(recv, hook_event_name="UserPromptSubmit", prompt=PROMPT)
            sess = bot.registry.get(SESSION)
            sess.label = SESSION
            sess.scope_chat_id, sess.scope_kind = CHAT, "group"
            sess.cwd = "/home/alice/frontend"
            await _hook(recv, hook_event_name="SubagentStart", agent_id=AGENT,
                        agent_type="Explore")
            seen["working"] = _surfaces(bot, await _api_rows(client), monkeypatch)

            await _hook(recv, hook_event_name="Stop",
                        last_assistant_message="Started a review in the background.")
            seen["waiting"] = _surfaces(bot, await _api_rows(client), monkeypatch)

            await _hook(recv, hook_event_name="SubagentStop", agent_id=AGENT,
                        agent_type="Explore")
            seen["finishing"] = _surfaces(bot, await _api_rows(client), monkeypatch)

            await _hook(recv, hook_event_name="UserPromptSubmit", prompt=CONTINUATION)
            seen["continuation"] = _surfaces(bot, await _api_rows(client), monkeypatch)

            await _hook(recv, hook_event_name="Stop",
                        last_assistant_message="The review found two typos.")
            seen["done"] = _surfaces(bot, await _api_rows(client), monkeypatch)
        finally:
            await client.close()

    run_async(_drive())

    working = seen["working"]
    assert working["shown"].state == "busy"
    assert working["bar"].startswith(f"⚙️ {SESSION} (working")
    assert working["api"] == ("busy", 0, 0)

    waiting = seen["waiting"]
    assert tuple(waiting["shown"]) == ("tasks", 1, 0)
    assert waiting["bar"] == f"🔄 {SESSION} (waiting for 1 agent)"
    assert waiting["api"] == ("tasks", 1, 0)
    assert waiting["cli"] == ("TASKS", 1, 0)
    assert waiting["cli_text"] == "waiting for 1 agent"
    assert waiting["card"].startswith(f"🔄 **{SESSION}** · waiting for 1 agent · ")

    finishing = seen["finishing"]
    assert tuple(finishing["shown"]) == ("tasks", 0, 0)
    assert finishing["bar"] == f"🔄 {SESSION} (finishing up)"
    assert finishing["api"] == ("tasks", 0, 0)
    assert finishing["cli"] == ("TASKS", 0, 0)
    assert finishing["cli_text"] == "finishing up"
    assert finishing["card"].startswith(f"🔄 **{SESSION}** · finishing up · ")

    continuation = seen["continuation"]
    assert continuation["shown"].state == "busy"
    assert continuation["api"] == ("busy", 0, 0)
    assert continuation["cli"] == ("BUSY", 0, 0)

    done = seen["done"]
    assert tuple(done["shown"]) == ("idle", 0, 0)
    assert done["bar"] == f"💤 {SESSION} (idle)"
    assert done["api"] == ("idle", 0, 0)
    assert done["cli"] == ("IDLE", 0, 0)
    assert done["cli_text"] == ""
    assert bot.registry.get(SESSION).status is Status.IDLE


def test_the_waiting_session_offers_stop_and_stop_ends_it(bot, run_async, monkeypatch,
                                                        init_header):
    """The Mini App offers Stop while the session waits, and the stop route
    takes it: afterwards every surface reads idle."""
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock(return_value=True))

    async def _no_sleep(_):
        return None

    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
    server = MiniAppServer(bot, bot.registry, port=8766)
    recv = hr.HookReceiver(bot.registry, bot.notify)
    out: dict = {}

    async def _drive():
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            await _hook(recv, hook_event_name="UserPromptSubmit", prompt=PROMPT)
            sess = bot.registry.get(SESSION)
            sess.label = SESSION
            sess.scope_chat_id, sess.scope_kind = CHAT, "group"
            await _hook(recv, hook_event_name="SubagentStart", agent_id=AGENT,
                        agent_type="Explore")
            await _hook(recv, hook_event_name="Stop",
                        last_assistant_message="Started a review in the background.")
            resp = await client.get(f"/api/sessions/{SESSION}", headers=init_header())
            detail = await resp.json()
            out["detail"] = (detail["status"], detail["job_agents"],
                             detail["actions"].get("stop"))
            resp = await client.post(f"/api/sessions/{SESSION}/stop", headers=init_header())
            out["stop"] = resp.status
            out["after"] = tuple(bot.registry.get(SESSION).shown_state())
        finally:
            await client.close()

    run_async(_drive())
    assert out["detail"] == ("tasks", 1, {"available": True, "reason": None})
    assert out["stop"] == 200
    assert out["after"] == ("idle", 0, 0)
