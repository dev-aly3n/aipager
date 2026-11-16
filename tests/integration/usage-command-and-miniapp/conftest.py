"""Black-box plumbing for /usage and the Mini App Usage page (roadmap 8.131).

Written against design.md ("Success criteria"), spec.md, decisions.md and
entrypoints.md only. Everything is driven through the exported surface:
``usage_store`` (note_plan, scan, snapshot, load, read, save_if_dirty,
the ``localtime`` seam), ``usage_cmd`` (usage_access, usage_view,
usage_html, fmt_tokens, handle_usage_cmd), ``statusline_file`` (the status
file and ``parse_rate_limits``), ``claude_bootstrap.session_login``,
``HookReceiver._on_datagram`` and the real Mini App app on aiohttp's test
server.

Fakes, all at outside boundaries: transcripts are files under tmp_path;
the local zone is the documented ``usage_store.localtime`` seam; Telegram
is a MagicMock update. No socket, no network, no real home.
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager import i18n, usage_store
from aipager.miniapp.server import SCOPE_HEADER, MiniAppServer
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

OWNER = 222333444            # the root conftest pins config.CHAT_ID to this
ADMIN = 555
USER = 777
RO = 888
STRANGER = 999_001
GROUP = -100777
GROUP2 = -100888
BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
HHMM = r"\b\d{1,2}:\d{2}\b"


@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def en():
    return i18n.get("en")


# ---- the local zone (documented seam) ---------------------------------------

@pytest.fixture
def zone(monkeypatch):
    """``zone(hours)``: the daemon's local zone becomes UTC+hours, through
    ``usage_store.localtime`` only (never by patching ``time``)."""
    def _set(hours: float):
        off = hours * 3600.0

        def fake(t=None):
            return time.gmtime((time.time() if t is None else t) + off)
        monkeypatch.setattr(usage_store, "localtime", fake)
        return off
    return _set


def local_midnight(now: float, off: float) -> float:
    """Epoch of the local midnight that starts the local day holding *now*."""
    return (int(now + off) // 86400) * 86400 - off


# ---- transcripts --------------------------------------------------------------

def iso(ts: float) -> str:
    d = datetime.fromtimestamp(ts, timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def entry(msg_id, ts, inp=0, out=0, cr=0, cw=0, *, model="claude-opus-test",
          typ="assistant", with_id=True) -> dict:
    message = {"model": model, "role": "assistant",
               "content": [{"type": "text", "text": "ok"}],
               "usage": {"input_tokens": inp, "output_tokens": out,
                         "cache_read_input_tokens": cr,
                         "cache_creation_input_tokens": cw}}
    if with_id:
        message["id"] = msg_id
    return {"type": typ, "timestamp": iso(ts), "uuid": f"u-{msg_id}-{out}",
            "message": message}


def user_line(ts) -> dict:
    return {"type": "user", "timestamp": iso(ts),
            "message": {"role": "user", "content": "hi"}}


def append(path: Path, *entries, partial: str | None = None) -> None:
    with open(path, "a", encoding="utf-8") as f:
        for e in entries:
            f.write((e if isinstance(e, str) else json.dumps(e)) + "\n")
        if partial is not None:
            f.write(partial)


def add_session(registry, name, *, scope, transcript, label=None, cwd=""):
    s = registry.get_or_create(name)
    s.label = label or name.removeprefix("claude-")
    s.scope_chat_id = scope
    s.transcript_path = str(transcript)
    s.cwd = cwd
    return s


def counts(t) -> tuple:
    """The four token counts of a snapshot total. A day entry may come as
    ``(day, counts)``; its counts are taken."""
    if len(t) == 2 and not isinstance(t[0], int):
        t = t[1]
    return tuple(int(x) for x in t)


def scan(registry, now=None):
    usage_store.scan(registry, now=time.time() if now is None else now, force=True)


def snap(scopes=None, now=None):
    return usage_store.snapshot(scopes, now=time.time() if now is None else now)


# ---- bots ---------------------------------------------------------------------

def owner_dm():
    return Scope(chat_id=OWNER, kind="dm", label="me",
                 members=(Member(id=OWNER, label="me", role="owner"),))


def team_group():
    return Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=OWNER, label="me", role="owner"),
        Member(id=ADMIN, label="ada", role="admin"),
        Member(id=USER, label="bob", role="user"),
        Member(id=RO, label="ro", role="read_only"),
    ))


def other_group():
    return Scope(chat_id=GROUP2, kind="group", label="frontend", members=(
        Member(id=OWNER, label="me", role="owner"),
        Member(id=ADMIN, label="ada", role="user"),
    ))


@pytest.fixture
def make_bot(mk_bot):
    """``make_bot("personal")`` or ``make_bot("scope")`` (owner DM, team
    group, frontend group where ADMIN is only a user)."""
    def _make(mode="scope"):
        registry = SessionRegistry()
        if mode == "personal":
            bot = mk_bot(registry)
        else:
            bot = mk_bot(registry, scopes=[owner_dm(), team_group(), other_group()])
        bot.policy = load_policy(Path("/nonexistent/policy.yaml"),
                                 Path("/nonexistent/policy.d"))
        bot._app.bot.id = 999
        bot._app.bot.username = "aipager_test_bot"
        for name in ("_card_for_injected", "_react", "_maybe_update_bot_name",
                     "_update_bot_commands", "_send_busy_and_animate"):
            setattr(bot, name, AsyncMock())
        return bot
    return _make


def tg_update(text, *, user_id, chat_id, message_id=400):
    u = MagicMock()
    u.effective_chat = MagicMock(id=chat_id,
                                 type="private" if chat_id > 0 else "supergroup")
    u.effective_user = MagicMock(id=user_id, username=f"u{user_id}",
                                 first_name="U", last_name="", language_code="en",
                                 is_bot=False)
    m = MagicMock()
    m.text = text
    m.caption = None
    m.message_id = message_id
    m.reply_to_message = None
    m.quote = None
    m.external_reply = None
    m.forward_origin = None
    m.via_bot = None
    m.media_group_id = None
    m.message_thread_id = None
    m.chat = u.effective_chat
    m.chat_id = chat_id
    m.from_user = u.effective_user
    m.reply_text = AsyncMock(return_value=MagicMock(message_id=901))
    m.reply_html = AsyncMock(return_value=MagicMock(message_id=902))
    u.message = m
    u.effective_message = m
    u.callback_query = None
    return u


def ctx():
    return MagicMock(bot=MagicMock(id=999), args=[])


def sent_texts(bot, update) -> list[str]:
    """Every text the bot sent in answer, whatever the send path."""
    out = []
    for mock in (update.message.reply_text, update.message.reply_html,
                 bot._app.bot.send_message):
        for c in mock.await_args_list:
            t = c.kwargs.get("text")
            if t is None:
                t = next((a for a in c.args if isinstance(a, str)), None)
            if t is not None:
                out.append(t)
    return out


def run_usage(bot, run_async, *, user_id, chat_id, text="/usage") -> str:
    from aipager.bot import usage_cmd
    u = tg_update(text, user_id=user_id, chat_id=chat_id)
    run_async(usage_cmd.handle_usage_cmd(bot, u, ctx()))
    return "\n".join(sent_texts(bot, u))


# ---- the Mini App -------------------------------------------------------------

_PORTS = itertools.count(8800)   # never bound: the test server picks its own port


def headers(user_id, *, scope=None, token=BOT_TOKEN) -> dict:
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "T"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    out = {"X-Telegram-Init-Data": urlencode(fields)}
    if scope is not None:
        out[SCOPE_HEADER] = str(scope)
    return out


def get(bot, run_async, path, hdrs) -> tuple[int, object]:
    async def go():
        srv = MiniAppServer(bot, bot.registry, port=next(_PORTS))
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            resp = await client.get(path, headers=hdrs)
            text = await resp.text()
            try:
                body = json.loads(text)
            except ValueError:
                body = text
            return resp.status, body
        finally:
            await client.close()
    return run_async(go())


def idle(sess):
    sess.status = Status.IDLE
    return sess


_SELF = sys.modules[__name__]


@pytest.fixture
def h():
    """This module (hyphenated scenario dirs are not importable packages)."""
    return _SELF
