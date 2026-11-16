"""A plan reading names the session it came from (and a gateway's host)
only to who may see that session's chat (review rev-iter1-001).

Plan limits are the whole account's, so every admin sees them; but the
session they were read in belongs to one chat, and ``/update`` already
keeps another chat's session names from an admin who does not manage it.
The same holds for ``/usage`` and ``GET /api/usage``: the owner and a
personal-mode operator see everything; another admin reads "from a session
in another chat" and no host."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager import config, i18n, usage_store
from aipager.bot import usage_cmd
from aipager.bot.usage_cmd import UsageAccess, usage_html, usage_view
from aipager.claude_bootstrap import SessionLogin
from aipager.miniapp.server import MiniAppServer
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.statusline_file import Window
from aipager.usage_store import Reading, Snapshot

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
OWNER, ADMIN, USER = 111, 555, 777
GROUP_A = -100
NOW = 1_794_000_000.0
EN = i18n.get("en")
ZERO = (0, 0, 0, 0)
SAVED = SessionLogin("saved", "", "/home/alice/.claude")
GATEWAY = SessionLogin("gateway", "gw.acme.example", "/home/alice/.claude")


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(usage_store, "localtime", time.gmtime)
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


@pytest.fixture
def bot(mk_bot):
    """The owner's DM, dave's DM (he is its admin) and group A, which dave
    manages. The owner's DM is a chat dave cannot see."""
    b = mk_bot(scopes=[
        Scope(chat_id=OWNER, kind="dm", label="owner", members=(
            Member(id=OWNER, label="alice", role="owner"),)),
        Scope(chat_id=ADMIN, kind="dm", label="dave", members=(
            Member(id=ADMIN, label="dave", role="admin"),)),
        Scope(chat_id=GROUP_A, kind="group", label="team", members=(
            Member(id=OWNER, label="alice", role="owner"),
            Member(id=ADMIN, label="dave", role="admin"),
            Member(id=USER, label="bob", role="user"))),
    ])
    b.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    b._app.bot.username = "aipager_test_bot"
    return b


def _reading(*, scope, session="acme-merger", kind="saved", host="", key=None):
    return Reading(key=key or f"{kind}|{host}|/h", kind=kind, host=host, session=session,
                   seen_at=NOW - 120, windows={"seven_day": Window(83, int(NOW) + 86400 * 2)},
                   scope=scope)


def _snap(*plan):
    return Snapshot(plan=tuple(plan), today=ZERO, week=ZERO,
                    days=tuple((f"d{i}", ZERO) for i in range(7)), models=(), behind=False)


MANAGED = UsageAccess(frozenset({ADMIN, GROUP_A}), "managed", True)
ALL = UsageAccess(None, "all", True)


# ---- the rule --------------------------------------------------------------------------

def test_a_reading_from_a_chat_the_admin_manages_names_its_session():
    text = usage_html(_snap(_reading(scope=GROUP_A)), MANAGED, EN, NOW)
    assert "from session acme-merger." in text


def test_a_reading_from_another_chat_keeps_its_session_out():
    text = usage_html(_snap(_reading(scope=OWNER)), MANAGED, EN, NOW)
    assert "acme-merger" not in text
    assert "As of 21:18 (2 minutes ago), from a session in another chat." in text
    # The limits themselves are the account's: still shown.
    assert "This week (all models): 83% used" in text


def test_a_reading_with_no_known_chat_counts_as_another_chat():
    text = usage_html(_snap(_reading(scope=0)), MANAGED, EN, NOW)
    assert "acme-merger" not in text and "another chat" in text


def test_the_owner_sees_every_session_name():
    text = usage_html(_snap(_reading(scope=OWNER)), ALL, EN, NOW)
    assert "from session acme-merger." in text


def test_a_gateway_in_another_chat_shows_no_host():
    plan = (_reading(scope=OWNER, kind="gateway", host="gw.acme.example"),
            _reading(scope=GROUP_A, session="frontend"))
    view = usage_view(_snap(*plan), MANAGED, EN, NOW)
    text = json.dumps(view)
    assert "gw.acme.example" not in text and "acme-merger" not in text
    names = [login["name"] for login in view["plan"]["logins"]]
    assert names == ["Gateway used in another chat", "Claude sign-in saved on this computer"]
    # The owner sees the host.
    owner = json.dumps(usage_view(_snap(*plan), ALL, EN, NOW))
    assert "Gateway gw.acme.example" in owner and "acme-merger" in owner


@pytest.mark.parametrize("age,expected", [
    (10, "As of {t} (just now), from a session in another chat."),
    (60, "As of {t} (1 minute ago), from a session in another chat."),
    (2 * 3600, "As of {t} (2 hours ago), from a session in another chat."),
    (86400, "As of {t} (1 day ago), from a session in another chat."),
], ids=["now", "minute", "hours", "day"])
def test_every_freshness_form_has_an_unnamed_twin(age, expected):
    reading = _reading(scope=OWNER)
    view = usage_view(_snap(reading), MANAGED, EN, reading.seen_at + age)
    t = time.strftime("%H:%M", time.gmtime(reading.seen_at))
    assert view["plan"]["logins"][0]["fresh"] == expected.format(t=t)


@pytest.mark.parametrize("code", ["ar", "de", "es", "fa", "fr", "id", "it", "ko", "pt-BR",
                                  "ru", "tr", "uk", "vi", "zh-Hans"])
def test_every_language_has_its_own_unnamed_line(code):
    tr = i18n.get(code)
    plan = (_reading(scope=OWNER, kind="gateway", host="gw.acme.example"),
            _reading(scope=OWNER, session="frontend"))
    view = usage_view(_snap(*plan), MANAGED, tr, NOW)
    english = usage_view(_snap(*plan), MANAGED, EN, NOW)
    text = json.dumps(view, ensure_ascii=False)
    assert "acme-merger" not in text and "gw.acme.example" not in text
    for mine, theirs in zip(view["plan"]["logins"], english["plan"]["logins"]):
        assert mine["fresh"] != theirs["fresh"] and mine["name"] != theirs["name"]


# ---- the reading keeps its chat --------------------------------------------------------

def test_note_plan_keeps_the_chat_and_it_survives_a_restart():
    usage_store.note_plan("acme-merger", {"seven_day": Window(83, int(NOW) + 86400)}, SAVED,
                          scope=OWNER, now=NOW)
    assert usage_store.snapshot(None, now=NOW).plan[0].scope == OWNER
    assert usage_store.save_if_dirty(force=True)
    usage_store.reset_for_tests()
    assert usage_store.snapshot(None, now=NOW).plan[0].scope == OWNER


def test_a_reading_moving_to_another_chat_is_written():
    windows = {"seven_day": Window(83, int(NOW) + 86400)}
    usage_store.note_plan("frontend", windows, SAVED, scope=GROUP_A, now=NOW)
    assert usage_store.note_plan("frontend", windows, SAVED, scope=OWNER, now=NOW + 1) is True


@pytest.mark.parametrize("bad", ["-100", 1.5, True, None, 10**20],
                         ids=["text", "float", "bool", "none", "huge"])
def test_a_stored_reading_with_a_bad_chat_is_dropped(bad):
    usage_store.note_plan("frontend", {"seven_day": Window(83, int(NOW) + 86400)}, SAVED,
                          scope=GROUP_A, now=NOW)
    usage_store.save_if_dirty(force=True)
    path = Path(config.USAGE_STATE_FILE)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["plan"][0]["scope"] = bad
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert usage_store.read()["plan"] == []


def test_a_bad_chat_given_to_note_plan_is_no_chat():
    usage_store.note_plan("frontend", {"seven_day": Window(83, int(NOW) + 86400)}, SAVED,
                          scope="-100", now=NOW)
    assert usage_store.snapshot(None, now=NOW).plan[0].scope == 0


# ---- both surfaces ---------------------------------------------------------------------

def _store_other_chat_readings():
    later = int(time.time()) + 2 * 86400
    usage_store.note_plan("acme-merger", {"seven_day": Window(83, later)}, SAVED, scope=OWNER)
    usage_store.note_plan("acme-gw", {"spend_limit": Window(40, later)}, GATEWAY, scope=OWNER)


def _update(user_id, chat_id):
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = "/usage"
    update.message.chat = MagicMock()
    update.message.chat.id = chat_id
    update.message.reply_text = AsyncMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.language_code = None
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_chat.type = "private" if chat_id > 0 else "group"
    update.effective_message = update.message
    return update


def _reply(update) -> str:
    (call,) = update.message.reply_text.await_args_list
    return str(call.args[0])


def test_usage_in_an_admins_dm_keeps_another_chats_session_out(bot, run_async):
    _store_other_chat_readings()
    update = _update(ADMIN, ADMIN)
    run_async(usage_cmd.handle_usage_cmd(bot, update, MagicMock()))
    text = _reply(update)
    assert "This week (all models): 83% used" in text
    assert "acme-merger" not in text and "acme-gw" not in text
    assert "gw.acme.example" not in text
    assert "from a session in another chat." in text


def test_usage_in_the_owners_dm_names_the_session(bot, run_async):
    _store_other_chat_readings()
    update = _update(OWNER, OWNER)
    run_async(usage_cmd.handle_usage_cmd(bot, update, MagicMock()))
    text = _reply(update)
    assert "from session acme-merger." in text and "Gateway gw.acme.example" in text


def test_personal_mode_operator_sees_the_session(mk_bot, monkeypatch, run_async):
    monkeypatch.setattr(config, "CHAT_ID", str(OWNER))
    b = mk_bot()
    _store_other_chat_readings()
    update = _update(OWNER, OWNER)
    run_async(usage_cmd.handle_usage_cmd(b, update, MagicMock()))
    assert "from session acme-merger." in _reply(update)


def _hdr(user_id, scope=None):
    user = {"id": user_id, "first_name": "T"}
    fields = {"auth_date": str(int(time.time())), "user": json.dumps(user)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    headers = {"X-Telegram-Init-Data": urlencode(fields)}
    if scope is not None:
        headers["X-Aipager-Scope"] = str(scope)
    return headers


def _api(bot, run_async, headers):
    srv = MiniAppServer(bot, bot.registry, port=8765)

    async def scenario():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            r = await client.get("/api/usage", headers=headers)
            return r.status, await r.json()
        finally:
            await client.close()
    return run_async(scenario())


def test_the_mini_app_keeps_another_chats_session_from_an_admin(bot, run_async):
    _store_other_chat_readings()
    status, body = _api(bot, run_async, _hdr(ADMIN, GROUP_A))
    assert status == 200
    text = json.dumps(body)
    assert "acme-merger" not in text and "acme-gw" not in text
    assert "gw.acme.example" not in text
    assert body["plan"]["logins"][0]["fresh"].endswith("from a session in another chat.")
    assert "Gateway used in another chat" in [x["name"] for x in body["plan"]["logins"]]


def test_the_mini_app_names_the_session_for_the_owner(bot, run_async):
    _store_other_chat_readings()
    status, body = _api(bot, run_async, _hdr(OWNER, GROUP_A))
    assert status == 200
    text = json.dumps(body)
    assert "from session acme-merger." in text and "Gateway gw.acme.example" in text
