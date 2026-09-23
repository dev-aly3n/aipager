"""Row S (roadmap 8.30 R4): the ``card_age_decay`` preference — "⏱
Long-turn card updates" in ``/settings`` and in the Mini App.

On by default. Off gives a card 0.7.13's cadence and seconds counter for
its whole turn, however long. A per-session override beats the scope's
value in both directions, exactly as every other preference does.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import time
from unittest.mock import MagicMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager import preferences as prefs
from aipager.bot import settings_menu
from aipager.state import Status

CHAT = 256113222
HOUR = 3600.0
EPS = 1e-3


# ── the card ─────────────────────────────────────────────────────────────────

def _long_card(mk_bot, vbot, vloop, rich_http, *, override=None):
    rich_http.clock = vloop.time
    bot = mk_bot()
    bot._app.bot = vbot
    sess = bot.registry.get_or_create("claude-dev")
    sess.label = "dev"
    sess.status = Status.BUSY
    sess.scope_chat_id = CHAT
    sess.scope_kind = "dm"
    sess.busy_msg_id = 77
    sess.busy_started_at = vloop.time() - 2 * HOUR
    sess.override_card_age_decay = override

    async def _go():
        task = asyncio.ensure_future(bot._animate_busy(sess))
        await asyncio.sleep(60.0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    vloop.run_until_complete(_go())
    edits = rich_http.edits()
    gaps = [b - a for (a, _), (b, _) in zip(edits, edits[1:])]
    return edits, gaps


def _is_today(edits, gaps) -> bool:
    """0.7.13 at 2 h: a quiet card every 4.4 s, counting ``120m 13s``."""
    return (len(edits) >= 10
            and all(abs(g - 4.4) < EPS for g in gaps)
            and all(re.search(r"· \d+m \d+s$", m.rsplit("\n", 1)[-1])
                    for _t, m in edits))


def _is_decayed(edits, gaps) -> bool:
    return (1 <= len(edits) <= 2
            and all(g >= 60.0 - EPS for g in gaps)
            and all(re.search(r"· 2h \d+m$", m.rsplit("\n", 1)[-1])
                    for _t, m in edits))


def test_s_off_for_the_chat_is_todays_card_at_two_hours(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """Row S. The scope's ``card_age_decay`` off: a 2 h card animates and
    counts exactly as 0.7.13 did. Mutation: ignore the preference in
    ``card_age_decay_enabled`` and the card decays anyway."""
    prefs.set_preference(CHAT, "card_age_decay", False)
    edits, gaps = _long_card(mk_bot, vbot, vloop, rich_http)
    assert _is_today(edits, gaps), (gaps, edits[:2])


def test_s_on_by_default(mk_bot, vbot, vloop, vlimiter, rich_http):
    """The control: nothing set, the same card decays."""
    assert prefs.get_preferences(CHAT).card_age_decay is True
    edits, gaps = _long_card(mk_bot, vbot, vloop, rich_http)
    assert _is_decayed(edits, gaps), (gaps, edits[:2])


def test_s_a_session_override_beats_the_scope_off(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """A per-session ``off`` wins over the scope's default ``on``.
    Mutation: read ``get_preferences`` (the scope only) and the session's
    own choice does nothing."""
    edits, gaps = _long_card(mk_bot, vbot, vloop, rich_http, override=False)
    assert _is_today(edits, gaps), (gaps, edits[:2])


def test_s_a_session_override_beats_the_scope_on(
    mk_bot, vbot, vloop, vlimiter, rich_http,
):
    """...and a per-session ``on`` wins over a scope that switched it off."""
    prefs.set_preference(CHAT, "card_age_decay", False)
    edits, gaps = _long_card(mk_bot, vbot, vloop, rich_http, override=True)
    assert _is_decayed(edits, gaps), (gaps, edits[:2])


def test_s_the_override_survives_a_registry_round_trip(tmp_state_file):
    """Persisted with the other overrides, like ``override_diff_preview``.
    Mutation: leave it out of the persist list and a restart forgets it."""
    from aipager.state import SessionRegistry

    registry = SessionRegistry()
    sess = registry.get_or_create("claude-dev")
    sess.override_card_age_decay = False
    registry.save()
    again = SessionRegistry()
    again.load()
    assert again.get("claude-dev").override_card_age_decay is False
    assert again.get("claude-dev").preference_overrides() == {
        "card_age_decay": False}


# ── /settings ────────────────────────────────────────────────────────────────

def _query(callback_data, *, chat_id=CHAT, user_id=12345):
    from unittest.mock import AsyncMock

    query = MagicMock()
    query.data = callback_data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = MagicMock()
    query.message.message_id = 42
    query.message.chat = MagicMock()
    query.message.chat.id = chat_id
    query.from_user = MagicMock()
    query.from_user.id = user_id
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    return update, query


def test_s_settings_shows_the_section_and_persists_off_and_on(
    mk_bot, run_async,
):
    """``/settings`` has a "⏱ Long-turn card updates" row; its section
    offers on/off; ``_:set:cadence:off`` persists and ``:on`` puts it back.
    Mutation: leave ``cadence`` out of the callback's field map and the tap
    is refused."""
    _text, kb = settings_menu.render_settings_root(CHAT)
    rows = [b for row in kb.inline_keyboard for b in row]
    cadence = [b for b in rows if b.callback_data == "_:set:cadence"]
    assert cadence and "Long-turn card updates" in cadence[0].text
    assert "✅" not in cadence[0].text                  # default: not customized

    _text, section = settings_menu.render_settings_section(CHAT, "cadence")
    data = [b.callback_data for row in section.inline_keyboard for b in row]
    assert "_:set:cadence:on" in data and "_:set:cadence:off" in data

    bot = mk_bot()
    update, _q = _query("_:set:cadence:off")
    run_async(bot._handle_callback(update, MagicMock()))
    assert prefs.get_preferences(CHAT).card_age_decay is False
    _text, kb = settings_menu.render_settings_root(CHAT)
    row = [b for r in kb.inline_keyboard for b in r
           if b.callback_data == "_:set:cadence"][0]
    assert "✅" in row.text                             # customized: off

    update, _q = _query("_:set:cadence:on")
    run_async(bot._handle_callback(update, MagicMock()))
    assert prefs.get_preferences(CHAT).card_age_decay is True


# ── the Mini App ─────────────────────────────────────────────────────────────

_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
_ADMIN = 555


def _hdr(user_id=_ADMIN):
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "Test"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", _TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(fields)}


class _Role:
    bypass_safety = True
    can_prompt = True


class _Policy:
    def get_role(self, name):
        return _Role()


@pytest.fixture
def miniapp(mk_bot, monkeypatch):
    from aipager.miniapp.server import MiniAppServer
    from aipager.scope import Member, Scope
    from aipager.state import SessionRegistry

    monkeypatch.setattr("aipager.config.BOT_TOKEN", _TOKEN)
    registry = SessionRegistry()
    scope = Scope(chat_id=-100, kind="group", label="team",
                  members=(Member(id=_ADMIN, label="ada", role="admin"),))
    bot = mk_bot(registry, scopes=[scope])
    bot.policy = _Policy()
    bot._app.bot.username = "aipager_test_bot"
    sess = registry.get_or_create("claude-dev")
    sess.label = "dev"
    sess.scope_chat_id = -100
    return MiniAppServer(bot, registry, port=8767)


def test_s_the_mini_app_round_trips_the_field(miniapp, run_async):
    """GET shows ``card_age_decay`` (true by default), PUT false persists
    it, a non-boolean is a 400; the per-session route sets and clears the
    session's own override. Mutation: leave it out of the preference
    payloads and the GET has no such field."""
    async def _run():
        client = TestClient(TestServer(miniapp._build_app()))
        await client.start_server()
        try:
            body = await (await client.get("/api/preferences", headers=_hdr())).json()
            assert body["values"]["card_age_decay"] is True
            assert "card_age_decay" in {g["field"] for g in body["schema"]}
            resp = await client.put("/api/preferences/card_age_decay",
                                    headers=_hdr(), json={"value": False})
            assert resp.status == 200
            assert (await resp.json())["values"]["card_age_decay"] is False
            again = await (await client.get("/api/preferences", headers=_hdr())).json()
            assert again["values"]["card_age_decay"] is False
            bad = await client.put("/api/preferences/card_age_decay",
                                   headers=_hdr(), json={"value": "no"})
            assert bad.status == 400

            put = await client.put("/api/sessions/dev/preferences/card_age_decay",
                                   headers=_hdr(), json={"value": True})
            assert put.status == 200
            assert miniapp.registry.get("claude-dev").override_card_age_decay is True
            gone = await client.delete(
                "/api/sessions/dev/preferences/card_age_decay", headers=_hdr())
            assert gone.status == 200
            assert miniapp.registry.get("claude-dev").override_card_age_decay is None
        finally:
            await client.close()
    run_async(_run())
