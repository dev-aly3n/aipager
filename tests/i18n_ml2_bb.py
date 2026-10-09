"""Helpers for the tester's black-box scenarios of multilingual phase 2
(``tests/integration/multilingual-phase-2/test_bb_*.py``).

Everything drives the public surface of entrypoints.md: the handlers and
``_handle_callback`` (the harness the rest of the suite uses), the exported
renderers, and the Mini App over HTTP through ``MiniAppServer._build_app``.
Every Telegram call is a recorded mock. A plain module so no hyphenated
conftest can shadow it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import Status

REPO = Path(__file__).resolve().parents[1]
CATALOGS = REPO / "aipager" / "i18n" / "locales"
SOURCE = REPO / "tools" / "i18n" / "source.jsonl"

DM_A = 555          # owner's private chat (its id is the owner's id)
DM_B = 2            # bob's private chat
DM_C = 3            # carol's private chat
GROUP = -1001
OWNER, BOB, CAROL = 555, 2, 3
BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"

RLM = "\u200f"
FSI = "\u2068"
PDI = "\u2069"
OWN_MARKS = (RLM, FSI, PDI)
BIDI_CONTROLS = re.compile("[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")
PERSIAN = re.compile("[؀-ۿ]")
CYRILLIC = re.compile("[Ѐ-ӿ]")
FA_DIGITS = re.compile("[۰-۹]")
LATIN_DIGITS = re.compile("[0-9]")
EM_DASH = "\u2014"


def scopes():
    return [
        Scope(chat_id=GROUP, kind="group", label="team", members=(
            Member(id=OWNER, label="aly", role="owner"),
            Member(id=BOB, label="bob", role="user"),
            Member(id=CAROL, label="carol", role="user"))),
        Scope(chat_id=DM_A, kind="dm", label="aly DM",
              members=(Member(id=OWNER, label="aly", role="owner"),)),
        Scope(chat_id=DM_B, kind="dm", label="bob DM",
              members=(Member(id=BOB, label="bob", role="owner"),)),
        Scope(chat_id=DM_C, kind="dm", label="carol DM",
              members=(Member(id=CAROL, label="carol", role="owner"),)),
    ]


def make_bot(mk_bot):
    bot = mk_bot(scopes=scopes())
    bot.policy = load_policy(Path("/nonexistent/policy.yaml"),
                             Path("/nonexistent/policy.d"))
    tg = bot._app.bot
    tg.id = 999
    tg.username = "aipager_test_bot"
    tg.send_message = AsyncMock(return_value=MagicMock(message_id=4242))
    tg.edit_message_text = AsyncMock()
    tg.set_my_commands = AsyncMock()
    tg.delete_my_commands = AsyncMock()
    tg.set_chat_menu_button = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._miniapp_url = "https://example.invalid/app"
    return bot


def add_session(bot, *, chat=DM_A, label="x1", status=Status.IDLE):
    kind = "dm" if chat > 0 else "group"
    s = bot.registry.get_or_create(f"claude-{label}__{kind[0]}{abs(chat)}")
    s.label = label
    s.scope_chat_id, s.scope_kind = chat, kind
    s.status = status
    return s


def _user(uid, language_code):
    names = {OWNER: "aly", BOB: "bob", CAROL: "carol"}
    user = MagicMock(id=uid, language_code=language_code, is_bot=False)
    user.username = names.get(uid, "someone")
    user.first_name = user.username
    return user


def message(text, *, chat=DM_A, user=OWNER, language_code="en"):
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.caption = None
    update.message.message_id = 900
    update.message.reply_text = AsyncMock(return_value=MagicMock(message_id=901))
    update.message.reply_to_message = None
    update.message.quote = None
    update.message.external_reply = None
    update.message.forward_origin = None
    update.message.via_bot = None
    update.message.media_group_id = None
    update.message.chat_id = chat
    update.message.chat = MagicMock(id=chat, type="private" if chat > 0 else "supergroup")
    update.effective_user = _user(user, language_code)
    update.message.from_user = update.effective_user
    update.effective_chat = update.message.chat
    return update


def callback(data, *, chat=DM_A, user=OWNER, language_code="en", message_id=42):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = MagicMock(message_id=message_id, text="", chat_id=chat)
    query.message.chat = MagicMock(id=chat, type="private" if chat > 0 else "supergroup")
    query.from_user = _user(user, language_code)
    update = MagicMock()
    update.callback_query = query
    update.message = None
    update.effective_user = query.from_user
    update.effective_chat = query.message.chat
    return update, query


def toast_texts(query):
    out = []
    for c in query.answer.await_args_list:
        text = c.args[0] if c.args else c.kwargs.get("text")
        if text:
            out.append(text)
    return out


def edited_text(query):
    call = query.edit_message_text.await_args
    assert call is not None, "the message was not edited"
    return call.args[0] if call.args else call.kwargs.get("text")


def button_texts(markup):
    rows = getattr(markup, "inline_keyboard", None) or getattr(markup, "keyboard", ())
    return [b.text for row in rows for b in row]


def reply_keyboards(bot, chat=None):
    """The reply keyboards (button text lists) sent through send_message."""
    out = []
    for call in bot._app.bot.send_message.await_args_list:
        markup = call.kwargs.get("reply_markup")
        if markup is None or not hasattr(markup, "keyboard"):
            continue
        target = call.kwargs.get("chat_id", call.args[0] if call.args else None)
        if chat is not None and target != chat:
            continue
        out.append([b.text for row in markup.keyboard for b in row])
    return out


def strip_marks(text: str) -> str:
    for ch in OWN_MARKS:
        text = text.replace(ch, "")
    return text


def code_segments(text: str) -> list[str]:
    """Contents of backtick spans, <code> and <pre> elements."""
    segs = re.findall(r"`([^`]*)`", text)
    segs += re.findall(r"<code>(.*?)</code>", text, re.S)
    segs += re.findall(r"<pre>(.*?)</pre>", text, re.S)
    return segs


def catalog(code: str) -> dict[str, object]:
    out = {}
    for line in (CATALOGS / f"{code}.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["k"]] = row
    return out


def source_rows() -> list[dict]:
    return [json.loads(x) for x in SOURCE.read_text(encoding="utf-8").splitlines()
            if x.strip()]


# ---- Mini App ------------------------------------------------------------------

def _sign(fields, token):
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def init_header(user_id, *, language_code=None):
    user = {"id": user_id, "first_name": "Test"}
    if language_code is not None:
        user["language_code"] = language_code
    fields = {"auth_date": str(int(time.time())), "user": json.dumps(user)}
    fields["hash"] = _sign(fields, BOT_TOKEN)
    return {"X-Telegram-Init-Data": urlencode(fields)}
