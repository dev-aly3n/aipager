"""The Mini App's server texts in the viewer's language, its chat lines in
the chat's (multilingual phase 4).

What the viewer reads (a refusal's ``detail``, the settings schema, the
session's facts and reasons, a chat's fallback name) follows their own
private chat's language, whichever chat they look at. A line the Mini App
posts into a chat follows that chat's language, whoever acted (check 12).
English stays byte for byte what it was.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager import i18n, preferences
from aipager.i18n import core, plurals
from aipager.miniapp import sessions as ma_sessions
from aipager.miniapp.server import MiniAppServer
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
GROUP, NAMELESS, ADMIN, VIEWER = -100, -300, 555, 777
RLM, FSI, PDI = "\u200f", "\u2068", "\u2069"
PERSIAN = re.compile("[\u0600-\u06ff]")
CYRILLIC = re.compile("[\u0400-\u04ff]")


@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _hdr(user_id):
    user = {"id": user_id, "first_name": "Test"}
    fields = {"auth_date": str(int(time.time())), "user": json.dumps(user)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(fields), "X-Aipager-Scope": str(GROUP)}


@pytest.fixture
def server(mk_bot):
    registry = SessionRegistry()
    scopes = [Scope(chat_id=GROUP, kind="group", label="team", members=(
                  Member(id=ADMIN, label="ada", role="admin"),
                  Member(id=VIEWER, label="bob", role="admin"))),
              Scope(chat_id=NAMELESS, kind="group", label="",
                    members=(Member(id=VIEWER, label="bob", role="admin"),))]
    bot = mk_bot(registry, scopes=scopes)
    bot.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    bot._app.bot.username = "aipager_test_bot"
    bot._app.bot.send_message = AsyncMock()
    bot._update_bot_commands = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    srv = MiniAppServer(bot, registry, port=8765)
    srv._allow_write = lambda user_id: True
    return srv


def _sess(srv, label="x1", **fields):
    sess = srv.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.scope_chat_id = GROUP
    sess.status = fields.pop("status", Status.IDLE)
    for key, value in fields.items():
        setattr(sess, key, value)
    return sess


def _call(srv, method, path, body=None, user=VIEWER):
    async def go():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            kwargs = {"headers": _hdr(user)}
            if body is not None:
                kwargs["data"] = json.dumps(body)
            resp = await client.request(method, path, **kwargs)
            return resp.status, await resp.json()
        finally:
            await client.close()
    return asyncio.new_event_loop().run_until_complete(go())


def _sent(srv) -> list[str]:
    return [c.kwargs["text"] for c in srv.bot._app.bot.send_message.await_args_list]


def _ok(**fields):
    return AsyncMock(return_value=SimpleNamespace(**fields))


# ---- details ---------------------------------------------------------------------------

def _details(srv) -> list[str]:
    """A spread of refusals, each through its route."""
    _sess(srv)
    out = []
    for body in ({"name": "status"}, {"name": "a" * 70}, {"name": "x1"},
                 {"name": "n1", "model": "-x"}):
        out.append(_call(srv, "POST", "/api/sessions", body)[1]["detail"])
    srv.bot._stop_session_core = _ok(ok=False, label="x1", dropped=0)
    out.append(_call(srv, "POST", "/api/sessions/x1/stop")[1]["detail"])
    srv.bot._compact_session_core = _ok(reason="queue_full", label="x1")
    out.append(_call(srv, "POST", "/api/sessions/x1/compact")[1]["detail"])
    srv.bot._compact_session_core = _ok(reason="send_failed", label="x1")
    out.append(_call(srv, "POST", "/api/sessions/x1/compact")[1]["detail"])
    srv.bot._restart_session_core = _ok(ok=False, reason="still_stopping", label="x1", err="")
    out.append(_call(srv, "POST", "/api/sessions/x1/restart")[1]["detail"])
    return out


ENGLISH_DETAILS = [
    "'status' is a reserved command name (/status). Pick another name.",
    "Session name must be 64 characters or fewer.",
    "A session named x1 already exists in this chat.",
    "Use one of the listed models, or a full model name like claude-opus-5.",
    "This session isn't busy right now - nothing to stop.",
    "Queue is full (50 pending) - clear it or wait for it to drain.",
    "Couldn't send /compact.",
    "x1 is still stopping. Try again in a moment.",
]


def test_details_are_todays_english_for_an_english_viewer(server):
    assert _details(server) == ENGLISH_DETAILS


def test_details_follow_the_viewer(server):
    preferences.set_ui_language(VIEWER, "fa")
    fa = _details(server)
    assert all(PERSIAN.search(d) for d in fa), fa
    assert all(d.startswith(RLM) for d in fa), fa
    # The command stays whole and clean, isolated as one unit.
    assert FSI + "/status" + PDI in fa[0] and FSI + "/compact" + PDI in fa[6]
    assert not re.search("/[" + RLM + FSI + PDI + "]", "".join(fa))
    # Numbers in Persian digits; the session name stays Latin, isolated.
    assert "۶۴" in fa[1] and "64" not in fa[1]
    assert "۵۰" in fa[5]
    assert FSI + "x1" + PDI in fa[2] and FSI + "x1" + PDI in fa[7]
    assert "claude-opus-5" in fa[3]
    preferences.set_ui_language(VIEWER, "ru")
    server.registry._sessions.clear()
    ru = _details(server)
    assert all(CYRILLIC.search(d) for d in ru), ru
    assert not any(m in "".join(ru) for m in (RLM, FSI, PDI))
    assert "/status" in ru[0] and "/compact" in ru[6] and "50" in ru[5]


def test_the_answer_bodies_keep_their_names_and_english(server):
    from aipager.miniapp import server as server_mod
    assert server_mod._ANSWER_NOT_WAITING_BODY == {
        "error": "not_waiting", "detail": "This session isn't waiting on you any more."}
    preferences.set_ui_language(VIEWER, "ru")
    _sess(server)
    status, data = _call(server, "POST", "/api/sessions/x1/answer")
    assert status == 409 and data["error"] == "not_waiting"
    assert data["detail"] == core._catalog("ru")["app.err.answer_not_waiting"]


def test_resumed_in_auto_gives_its_owners_reason_in_the_viewers_language(server):
    preferences.set_ui_language(VIEWER, "ru")
    _sess(server, status=Status.GONE)
    server.bot._resume_full_gate = lambda sess, override, actor, chat: ("auto", True)
    server.bot._do_resume_core = _ok(reason=None, err="")
    status, data = _call(server, "POST", "/api/sessions/x1/resume")
    assert status == 200 and data["mode"] == "auto"
    from aipager.bot.transport import RESUME_FULL_NEEDS_ADMIN
    # The reason is transport's key since phase 3b, so it arrives in the
    # viewer's language too (phase 4 decision 5).
    ru = i18n.get("ru")
    reason = ru.text(RESUME_FULL_NEEDS_ADMIN)
    assert reason != str(RESUME_FULL_NEEDS_ADMIN)
    assert data["detail"] == ru.text(
        "app.err.resumed_in_auto", "Resumed {session} in Auto mode: {reason}",
        session="x1", reason=reason)
    preferences.set_ui_language(VIEWER, "")
    status, data = _call(server, "POST", "/api/sessions/x1/resume")
    assert data["detail"] == f"Resumed x1 in Auto mode: {RESUME_FULL_NEEDS_ADMIN}"


@pytest.mark.parametrize("code, constant", [
    ("not_live", "MODEL_SWITCH_NOT_LIVE_REASON"), ("busy", "MODEL_SWITCH_BUSY_REASON"),
    ("prompt_open", "MODEL_SWITCH_PROMPT_OPEN_REASON"),
    ("unknown", "MODEL_SWITCH_UNKNOWN_REASON"), ("switch_pending", "MODEL_SWITCH_PENDING_REASON"),
])
def test_model_refusal_by_code(server, code, constant):
    """The sentence is chosen by the refusal's code, never passed through
    from the words the bot gave."""
    _sess(server)
    server.bot._switch_model_core = _ok(ok=False, reason=code, detail="words from elsewhere",
                                        label="x1")
    status, data = _call(server, "POST", "/api/sessions/x1/model", {"model": "opus"})
    assert (status, data["error"]) == (409, code)
    assert data["detail"] == getattr(ma_sessions, constant)
    preferences.set_ui_language(VIEWER, "fa")
    status, data = _call(server, "POST", "/api/sessions/x1/model", {"model": "opus"})
    assert data["detail"] == i18n.get("fa").text(getattr(ma_sessions, constant))
    assert PERSIAN.search(data["detail"])


def test_an_unknown_refusal_code_passes_its_words(server):
    _sess(server)
    server.bot._switch_model_core = _ok(ok=False, reason="other", detail="as given", label="x1")
    preferences.set_ui_language(VIEWER, "fa")
    status, data = _call(server, "POST", "/api/sessions/x1/model", {"model": "opus"})
    assert (status, data) == (409, {"error": "other", "detail": "as given"})


# ---- the schema, facts, reasons, chat names -------------------------------------------------

def test_schema_follows_the_viewer(server):
    from aipager.bot.settings_menu import settings_schema
    _sess(server)
    paths = ("/api/preferences", "/api/session-options", "/api/sessions/x1/preferences")
    for path in paths:
        assert _call(server, "GET", path)[1]["schema"] == settings_schema(), path
    preferences.set_ui_language(VIEWER, "fa")
    # The group's own language is not the viewer's.
    preferences.set_ui_language(GROUP, "ru")
    for path in paths:
        schema = _call(server, "GET", path)[1]["schema"]
        assert schema == settings_schema(tr=i18n.get("fa")), path
        assert PERSIAN.search(schema[0]["title"])


def test_facts_and_reasons_follow_the_viewer(server, monkeypatch):
    monkeypatch.setattr(ma_sessions, "latest_stats",
                        lambda sess: {"model": "Opus 4.6", "context_pct": 45, "cost_usd": 1.5})
    monkeypatch.setattr(ma_sessions, "combined_queue_depth", lambda sess: 0)
    monkeypatch.setattr(ma_sessions, "cached_last_assistant_preview", lambda path: "")
    now = time.monotonic()
    _sess(server, status=Status.BUSY, cwd="/home/op/proj", last_hook_at=now - 125,
          busy_started_at=now - 192)
    data = _call(server, "GET", "/api/sessions/x1")[1]
    assert [(f["id"], f["label"], f["value"]) for f in data["facts"]] == [
        ("model", "Model", "Opus 4.6"), ("context", "Context", "45%"), ("cost", "Cost", "$1.50"),
        ("working", "Working for", "3:12"), ("last_active", "Last active", "2m ago"),
        ("mode", "Mode", "💬 Ask mode"), ("cwd", "Directory", "/home/op/proj")]
    preferences.set_ui_language(VIEWER, "fa")
    data = _call(server, "GET", "/api/sessions/x1")[1]
    facts = {f["id"]: f for f in data["facts"]}
    assert [f["id"] for f in data["facts"]] == ["model", "context", "cost", "working",
                                               "last_active", "mode", "cwd"]
    assert facts["mode"]["value"].removeprefix(RLM) == "💬 مود پرسش"
    assert all(PERSIAN.search(f["label"]) for f in data["facts"])
    assert facts["context"]["value"] == "۴۵٪"
    assert facts["cost"]["value"] == "$۱٫۵۰"
    assert facts["working"]["value"] == "۳:۱۲"
    assert "۲" in facts["last_active"]["value"] and "2" not in facts["last_active"]["value"]
    # Names stay as they are.
    assert facts["model"]["value"] == "Opus 4.6" and facts["cwd"]["value"] == "/home/op/proj"
    reasons = [a["reason"] for a in data["actions"].values() if a["reason"]]
    reasons.append(data["model_switch"]["reason"])
    assert reasons and all(PERSIAN.search(r) for r in reasons), reasons


def test_chat_names_follow_the_viewer(server):
    chats = _call(server, "GET", "/api/chats")[1]["chats"]
    assert [c["label"] for c in chats] == ["team", "Group"]
    preferences.set_ui_language(VIEWER, "ru")
    chats = _call(server, "GET", "/api/chats")[1]["chats"]
    # A name from aipager.yaml is a name: never translated.
    assert [c["label"] for c in chats] == ["team", core._catalog("ru")["app.chat.group"]]


# ---- the lines posted into the chat ---------------------------------------------------------

def _mirror_all(srv) -> list[str]:
    _sess(srv)
    srv.bot._clear_queue_core = _ok(ok=True, label="x1", dropped=3)
    _call(srv, "POST", "/api/sessions/x1/clearqueue")
    srv.bot._compact_session_core = _ok(reason="sent", label="x1")
    _call(srv, "POST", "/api/sessions/x1/compact")
    _call(srv, "PUT", "/api/preferences/layout", {"value": "merged"})
    _call(srv, "PUT", "/api/sessions/x1/preferences/answer_length", {"value": "short"})
    srv.bot._mode_switch_core = _ok(ok=True, reason="done", label="x1", err="",
                                    mode="full")
    _call(srv, "POST", "/api/sessions/x1/perms", {"mode": "full"})
    return _sent(srv)


def test_mirrors_are_todays_english(server):
    assert _mirror_all(server) == [
        "🧹 Cleared 3 queued messages for [x1] from the Mini App",
        "📦 Compact triggered for [x1] from the Mini App",
        "⚙️ 🖼 Message layout → Merged into busy message (changed from the Mini App)",
        "⚙️ 📏 Answer length → Short for session x1 (changed from the Mini App)",
        "🔓 x1 switched to Full access mode from the Mini App",
    ]


def test_check_12_viewer_and_chat_never_cross(server):
    """A Russian viewer acts in a Persian group: what they read is
    Russian, what the group reads is Persian."""
    preferences.set_ui_language(VIEWER, "ru")
    preferences.set_ui_language(GROUP, "fa")
    lines = _mirror_all(server)
    assert len(lines) == 5
    for line in lines:
        assert PERSIAN.search(line) and not CYRILLIC.search(line), line
        assert line.startswith(RLM), line
        assert "→" not in line
    assert FSI + "x1" + PDI in lines[0]
    assert "←" in lines[2] and "←" in lines[3]
    assert "Mini App" in lines[0]
    # The same viewer's own refusal, in the same group: Russian.
    server.bot._stop_session_core = _ok(ok=False, label="x1", dropped=0)
    detail = _call(server, "POST", "/api/sessions/x1/stop")[1]["detail"]
    assert CYRILLIC.search(detail) and not PERSIAN.search(detail)


@pytest.mark.parametrize("dropped", [1, 2, 5, 11, 21, 22, 25])
def test_mirror_plurals(server, dropped):
    preferences.set_ui_language(GROUP, "ru")
    _sess(server)
    server.bot._clear_queue_core = _ok(ok=True, label="x1", dropped=dropped)
    _call(server, "POST", "/api/sessions/x1/clearqueue")
    server.bot._stop_session_core = _ok(ok=True, label="x1", dropped=dropped)
    _call(server, "POST", "/api/sessions/x1/stop")
    cleared, stopped = _sent(server)
    cat = plurals.category("ru", dropped)
    want = core._catalog("ru")["app.mirror.cleared"][cat]
    assert cleared == want.replace("{n}", str(dropped)).replace("{session}", "x1")
    want = core._catalog("ru")["app.mirror.stopped_dropped"][cat]
    assert stopped == want.replace("{n}", str(dropped)).replace("{session}", "x1")


def test_the_model_and_commands_stay_clean_in_persian(server, monkeypatch):
    preferences.set_ui_language(GROUP, "fa")
    _sess(server)
    server.bot._switch_model_core = _ok(ok=True, label="x1", previous_model="Sonnet 5")
    monkeypatch.setattr("aipager.bot.session_ops.await_model_change",
                        AsyncMock(return_value="claude-opus-5"))
    _call(server, "POST", "/api/sessions/x1/model", {"model": "claude-opus-5"})
    (line,) = _sent(server)
    assert FSI + "claude-opus-5" + PDI in line and "←" in line and "→" not in line
    assert not re.search("[A-Za-z0-9_][" + RLM + FSI + PDI + "][A-Za-z0-9_]", line)


@pytest.mark.parametrize("line", ["stopped", "resumed", "session_setting_changed"])
def test_a_line_that_cannot_be_written_never_fails_the_action(server, monkeypatch, line):
    """The stop, resume or session setting already happened: a chat line that fails to
    render (a broken catalog form, say) is dropped like a failed send,
    and the route still answers that it worked."""
    from aipager.miniapp import chat_lines

    def broken(*args, **kwargs):
        raise KeyError("session")

    monkeypatch.setattr(chat_lines, line, broken)
    if line == "stopped":
        _sess(server, status=Status.BUSY)
        server.bot._stop_session_core = _ok(ok=True, label="x1", dropped=0)
        status, data = _call(server, "POST", "/api/sessions/x1/stop")
        assert (status, data["status"]) == (200, "stopped")
    elif line == "session_setting_changed":
        sess = _sess(server)
        status, data = _call(server, "PUT", "/api/sessions/x1/preferences/answer_length",
                             {"value": "short"})
        assert (status, data["changed"]) == (200, True)
        assert sess.preference_overrides().get("answer_length") == "short"
    else:
        _sess(server, status=Status.GONE)
        server.bot._resume_full_gate = lambda sess, override, actor, chat: (True, False)
        server.bot._do_resume_core = _ok(reason=None, err="")
        status, data = _call(server, "POST", "/api/sessions/x1/resume")
        assert (status, data["status"]) == (200, "resumed")
    assert _sent(server) == []
