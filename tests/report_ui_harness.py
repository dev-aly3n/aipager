"""Shared doubles for the problem report UI unit tests (roadmap 8.112
step 3): a recording fake of the Telegram calls report_flow makes, tap
and message builders, and the fake Sentry/GitHub network."""

from __future__ import annotations

import html
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx

from aipager.report import endpoint

OWNER = 256113222          # tests/conftest.py pins config.CHAT_ID to this
STRANGER = 4242
GROUP = -1001234


class FakeTg:
    """The bot-level Telegram calls report_flow and report_offer make."""

    def __init__(self):
        self.next_id = 1000
        self.sent: list[SimpleNamespace] = []
        self.edits: list[dict] = []
        self.docs: list[SimpleNamespace] = []
        self.deleted: list[tuple] = []
        self.send_error: Exception | None = None
        self.send_result = None          # a sentinel to return instead

    def _id(self) -> int:
        self.next_id += 1
        return self.next_id

    async def send_message(self, chat_id, text, **kw):
        if self.send_error is not None:
            raise self.send_error
        if self.send_result is not None:
            return self.send_result
        msg = SimpleNamespace(message_id=self._id(), chat_id=chat_id, text=text, kw=kw)
        self.sent.append(msg)
        return msg

    async def edit_message_text(self, text=None, chat_id=None, message_id=None, **kw):
        self.edits.append({"text": text, "chat_id": chat_id, "message_id": message_id, **kw})
        return True

    async def send_document(self, chat_id, document=None, filename=None, **kw):
        doc = SimpleNamespace(message_id=self._id(), chat_id=chat_id, filename=filename,
                              data=document.input_file_content, kw=kw)
        self.docs.append(doc)
        return doc

    async def delete_message(self, chat_id, message_id, **kw):
        self.deleted.append((chat_id, message_id))
        return True

    def last_edit_of(self, message_id) -> dict | None:
        mine = [e for e in self.edits if e["message_id"] == message_id]
        return mine[-1] if mine else None


def make_bot(mk_bot, **kw):
    bot = mk_bot(**kw)
    tg = FakeTg()
    bot._app.bot = tg
    return bot, tg


def tap(data, *, user_id=OWNER, chat_id=OWNER, message_id=1):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = MagicMock()
    query.message.message_id = message_id
    query.message.chat = MagicMock()
    query.message.chat.id = chat_id
    query.message.text = ""
    query.from_user = MagicMock()
    query.from_user.id = user_id
    update = MagicMock()
    update.callback_query = query
    update.message = None
    update.effective_user = query.from_user
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    return update, query


def text_update(text, *, user_id=OWNER, chat_id=OWNER, reply_to=None, message_id=5000):
    update = MagicMock()
    update.callback_query = None
    msg = MagicMock()
    msg.text = text
    msg.message_id = message_id
    msg.reply_to_message = (SimpleNamespace(message_id=reply_to)
                            if reply_to is not None else None)
    msg.external_reply = None
    msg.forward_origin = None
    msg.via_bot = None
    msg.quote = None
    msg.media_group_id = None
    msg.photo = None
    msg.document = None
    msg.reply_text = AsyncMock()
    update.message = msg
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    return update


def toasts(query) -> list[str]:
    out = []
    for call in query.answer.await_args_list:
        if call.args:
            out.append(call.args[0])
        elif call.kwargs.get("text"):
            out.append(call.kwargs["text"])
    return [t for t in out if t]


def inline_block(card_text: str) -> str | None:
    """The report as the card shows it, HTML entities decoded."""
    m = re.search(r"<pre>(.*)</pre>", card_text, re.S)
    return html.unescape(m.group(1)) if m else None


# ---- the fake network ----------------------------------------------------------

def key_doc(**changes) -> bytes:
    doc = {"v": 1, "dsn": endpoint.FALLBACK_DSN, "enabled": True, "min_version": "0.1.0"}
    doc.update(changes)
    return json.dumps(doc).encode()


class FakeNet:
    """GitHub's key file and Sentry's envelope endpoint, recorded."""

    def __init__(self, doc: bytes | None = None, sentry_status: int = 200):
        self.doc = key_doc() if doc is None else doc
        self.sentry_status = sentry_status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == endpoint.ENDPOINT_URL:
            return httpx.Response(200, content=self.doc)
        if request.method == "POST":
            return httpx.Response(self.sentry_status, json={"id": "x"})
        return httpx.Response(404)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    @property
    def posts(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]


def attachment(body: bytes) -> bytes:
    """The ``report.json`` payload of a Sentry envelope."""
    _header, rest = body.split(b"\n", 1)
    while rest:
        item_line, rest = rest.split(b"\n", 1)
        item = json.loads(item_line)
        payload, rest = rest[:item["length"]], rest[item["length"] + 1:]
        if item.get("type") == "attachment":
            return payload
    raise AssertionError("no attachment in the envelope")
