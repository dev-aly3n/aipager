"""A fake Telegram Bot API for end-to-end tests (loopback only).

``FakeBotApiThread`` runs an aiohttp app on ``127.0.0.1:<ephemeral>`` in
its own thread with its own event loop, so a synchronous test can block
and poll while the server keeps answering the daemon's long-polls.

It implements the subset of the Bot API aipager calls (see
``SUPPORTED_METHODS``), with Telegram's own semantics where the daemon
reacts to them: per-chat message ids, messages echoed back with their
text and inline keyboard, ``ok:false`` errors for an unchanged edit, a
missing message, a migrated group and an unknown chat. Every request is
recorded (method, decoded params, uploaded files) for assertions, and
tests inject updates (messages, edits, button taps, files, the
supergroup migration pair, anonymous-admin posts) that the daemon then
receives through ``getUpdates``.

The bot token never leaves this process in any message this module
produces: calls are recorded without the URL path, and failures quote
methods and params only.
"""

from __future__ import annotations

import asyncio
import html
import itertools
import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from aiohttp import web

from tests.e2e.fake_telegram import updates as U
from tests.e2e.fake_telegram.redaction import redact

SUPPORTED_METHODS = (
    "getMe", "getUpdates", "deleteWebhook", "getChat", "sendMessage",
    "sendRichMessage", "editMessageText", "editMessageReplyMarkup",
    "deleteMessage", "deleteMessages", "setMessageReaction", "pinChatMessage",
    "sendDocument", "sendChatAction", "answerCallbackQuery", "setMyCommands",
    "deleteMyCommands", "getMyCommands", "setChatMenuButton", "getFile",
)

_INT_KEYS = {"chat_id", "message_id", "reply_to_message_id", "message_thread_id",
             "offset", "limit", "timeout", "user_id", "from_chat_id"}
_BOOL_KEYS = {"disable_notification", "show_alert", "drop_pending_updates",
              "is_big", "protect_content", "allow_sending_without_reply"}
_MAX_POLL = 30.0


# ---------------------------------------------------------------------------
# Errors.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ApiError:
    status: int
    description: str
    parameters: dict | None = None

    def body(self) -> dict:
        out: dict = {"ok": False, "error_code": self.status,
                     "description": self.description}
        if self.parameters:
            out["parameters"] = dict(self.parameters)
        return out


class _Fail(Exception):
    def __init__(self, err: ApiError):
        super().__init__(err.description)
        self.err = err


class errors:  # noqa: N801 - a namespace of factories, used as server.errors.x()
    """Factories for the error shapes Telegram returns."""

    @staticmethod
    def not_modified() -> ApiError:
        return ApiError(400, "Bad Request: message is not modified: specified new "
                             "message content and reply markup are exactly the same "
                             "as a current content and reply markup of the message")

    @staticmethod
    def message_not_found() -> ApiError:
        return ApiError(400, "Bad Request: message to edit not found")

    @staticmethod
    def delete_not_found() -> ApiError:
        return ApiError(400, "Bad Request: message to delete not found")

    @staticmethod
    def blocked() -> ApiError:
        return ApiError(403, "Forbidden: bot was blocked by the user")

    @staticmethod
    def retry_after(seconds: int) -> ApiError:
        return ApiError(429, f"Too Many Requests: retry after {seconds}",
                        {"retry_after": seconds})

    @staticmethod
    def migrated(new_id: int) -> ApiError:
        return ApiError(400, "Bad Request: group chat was upgraded to a supergroup chat",
                        {"migrate_to_chat_id": new_id})

    @staticmethod
    def chat_not_found() -> ApiError:
        return ApiError(400, "Bad Request: chat not found")


# ---------------------------------------------------------------------------
# Records.
# ---------------------------------------------------------------------------

@dataclass
class Call:
    seq: int
    ts: float
    method: str
    params: dict
    files: dict = field(default_factory=dict)
    status: int = 200
    response: dict = field(default_factory=dict)

    @property
    def chat_id(self) -> int | None:
        v = self.params.get("chat_id")
        return v if isinstance(v, int) else None

    @property
    def text(self) -> str:
        """The visible text a send/edit carried (plain, rich or caption)."""
        p = self.params
        rich = p.get("rich_message")
        if isinstance(rich, dict):
            return str(rich.get("markdown", ""))
        for key in ("text", "caption"):
            if key in p:
                return _plain(str(p[key]), p.get("parse_mode"))
        return ""

    def summary(self) -> str:
        t = self.text.replace("\n", " ")
        return (f"#{self.seq} {self.method} chat={self.params.get('chat_id')} "
                f"status={self.status} {t[:80]!r}")


@dataclass
class _Msg:
    message: dict
    from_bot: bool
    raw_text: str | None = None
    parse_mode: str | None = None
    rich: str | None = None
    deleted: bool = False
    pinned: bool = False
    reactions: list = field(default_factory=list)
    seq: int = 0


@dataclass
class _Injected:
    method: str
    error: ApiError
    chat_id: int | None
    times: int


_TAG_RE = re.compile(r"<[^>]+>")
_MD2_ESCAPE_RE = re.compile(r"\\([_*\[\]()~`>#+\-=|{}.!\\])")


def _plain(text: str, parse_mode: str | None) -> str:
    """What a user would see: Telegram strips the markup of HTML and
    MarkdownV2 text."""
    mode = (parse_mode or "").lower()
    if mode == "html":
        return html.unescape(_TAG_RE.sub("", text))
    if mode == "markdownv2":
        return _MD2_ESCAPE_RE.sub(r"\1", text)
    return text


def _maybe_json(value: str) -> Any:
    s = value.strip()
    if s[:1] in ("{", "["):
        try:
            return json.loads(s)
        except ValueError:
            return value
    return value


def _normalise(params: dict) -> dict:
    out = {}
    for k, v in params.items():
        if isinstance(v, str):
            if k in _INT_KEYS and re.fullmatch(r"-?\d+", v.strip()):
                v = int(v)
            elif k in _BOOL_KEYS and v.lower() in ("true", "false"):
                v = v.lower() == "true"
            else:
                v = _maybe_json(v)
        out[k] = v
    return out


def _inline_markup(markup: Any) -> dict | None:
    """Only an inline keyboard is part of a message object."""
    if isinstance(markup, dict) and "inline_keyboard" in markup:
        return markup
    return None


# ---------------------------------------------------------------------------
# The API.
# ---------------------------------------------------------------------------

class FakeBotApi:
    """State + handlers. Thread-safe query/inject methods for tests."""

    def __init__(self, token: str, bot_id: int, bot_username: str):
        self.token = token
        self.bot_user = {
            "id": bot_id, "is_bot": True, "first_name": "aipager fake",
            "username": bot_username, "can_join_groups": True,
            "can_read_all_group_messages": False, "supports_inline_queries": False,
        }
        self._lock = threading.RLock()
        self._seq = itertools.count(1)
        self._update_ids = itertools.count(1)
        self._file_ids = itertools.count(1)
        self._cb_ids = itertools.count(1)
        self._calls: list[Call] = []
        self._updates: list[dict] = []
        self._chats: dict[int, dict] = {}
        self._migrated: dict[int, int] = {}
        self._next_mid: dict[int, int] = {}
        self._msgs: dict[tuple[int, int], _Msg] = {}
        self._files: dict[str, tuple[str, bytes]] = {}
        self._paths: dict[str, bytes] = {}
        self._file_paths: dict[str, str] = {}
        self._commands: dict[str, list] = {}
        self._menu_buttons: dict[str, Any] = {}
        self._injected: list[_Injected] = []
        self.unknown_methods: list[str] = []
        self.bad_token_calls: list[str] = []
        self.polling_started = threading.Event()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._cond: asyncio.Condition | None = None
        self._closing = False

    # -- chats and users ---------------------------------------------------

    def add_chat(self, chat_id: int, chat_type: str, title: str | None = None,
                 who: dict | None = None) -> dict:
        with self._lock:
            c = U.chat(chat_id, chat_type, title, who)
            self._chats[chat_id] = c
            return dict(c)

    @staticmethod
    def user(user_id: int, username: str, first_name: str | None = None) -> dict:
        return U.user(user_id, username, first_name)

    def chat(self, chat_id: int) -> dict:
        with self._lock:
            return dict(self._chats[chat_id])

    # -- error injection ---------------------------------------------------

    def fail_next(self, method: str, error: ApiError, *, chat_id: int | None = None,
                  times: int = 1) -> None:
        with self._lock:
            self._injected.append(_Injected(method, error, chat_id, times))

    def _take_injected(self, method: str, params: dict) -> ApiError | None:
        with self._lock:
            for inj in self._injected:
                if inj.method != method:
                    continue
                if inj.chat_id is not None and params.get("chat_id") != inj.chat_id:
                    continue
                inj.times -= 1
                if inj.times <= 0:
                    self._injected.remove(inj)
                return inj.error
        return None

    # -- message store -----------------------------------------------------

    def _new_mid(self, chat_id: int) -> int:
        n = self._next_mid.get(chat_id, 0) + 1
        self._next_mid[chat_id] = n
        return n

    def _chat_obj(self, chat_id: int) -> dict:
        c = self._chats.get(chat_id)
        if c is None:
            raise _Fail(errors.chat_not_found())
        return c

    def _check_migrated(self, params: dict) -> None:
        cid = params.get("chat_id")
        if isinstance(cid, int) and cid in self._migrated:
            raise _Fail(errors.migrated(self._migrated[cid]))

    def _store(self, msg: dict, *, from_bot: bool, **extra) -> _Msg:
        rec = _Msg(message=msg, from_bot=from_bot, seq=next(self._seq), **extra)
        self._msgs[(msg["chat"]["id"], msg["message_id"])] = rec
        return rec

    def _reply_obj(self, chat_id: int, params: dict) -> dict | None:
        rid = params.get("reply_to_message_id")
        rp = params.get("reply_parameters")
        if isinstance(rp, dict):
            rid = rp.get("message_id", rid)
        if isinstance(rid, str) and rid.lstrip("-").isdigit():
            rid = int(rid)
        rec = self._msgs.get((chat_id, rid)) if isinstance(rid, int) else None
        return U.strip_nested(rec.message) if rec else None

    def _bot_message(self, chat_id: int, params: dict, **body) -> dict:
        msg: dict = {
            "message_id": self._new_mid(chat_id),
            "date": int(time.time()),
            "chat": dict(self._chat_obj(chat_id)),
            "from": dict(self.bot_user),
        }
        msg.update(body)
        reply = self._reply_obj(chat_id, params)
        if reply is not None:
            msg["reply_to_message"] = reply
        if isinstance(params.get("message_thread_id"), int):
            msg["message_thread_id"] = params["message_thread_id"]
        markup = _inline_markup(params.get("reply_markup"))
        if markup is not None:
            msg["reply_markup"] = markup
        return msg

    def _find(self, params: dict, missing: ApiError) -> _Msg:
        cid, mid = params.get("chat_id"), params.get("message_id")
        rec = self._msgs.get((cid, mid))
        if rec is None or rec.deleted:
            raise _Fail(missing)
        return rec

    # -- method handlers (called under the lock) -----------------------------

    def _m_getMe(self, p, f):
        return dict(self.bot_user)

    def _m_deleteWebhook(self, p, f):
        if p.get("drop_pending_updates"):
            self._updates.clear()
        return True

    def _m_getChat(self, p, f):
        c = self._chat_obj(p.get("chat_id"))
        out = dict(c)
        out.update({"accent_color_id": 0, "max_reaction_count": 11,
                    "accepted_gift_types": {
                        "unlimited_gifts": False, "limited_gifts": False,
                        "unique_gifts": False, "premium_subscription": False,
                        "gifts_from_channels": False}})
        return out

    def _m_sendMessage(self, p, f):
        text = str(p.get("text", ""))
        if not text.strip():
            raise _Fail(ApiError(400, "Bad Request: message text is empty"))
        plain = _plain(text, p.get("parse_mode"))
        if len(plain) > 4096:
            raise _Fail(ApiError(400, "Bad Request: message is too long"))
        cid = p.get("chat_id")
        msg = self._bot_message(cid, p, text=plain)
        self._store(msg, from_bot=True, raw_text=text, parse_mode=p.get("parse_mode"))
        return msg

    def _m_sendRichMessage(self, p, f):
        rich = p.get("rich_message") or {}
        md = str(rich.get("markdown", "")) if isinstance(rich, dict) else ""
        if not md.strip():
            raise _Fail(ApiError(400, "Bad Request: message text is empty"))
        cid = p.get("chat_id")
        msg = self._bot_message(cid, p, text=md)
        self._store(msg, from_bot=True, rich=md)
        return msg

    def _m_editMessageText(self, p, f):
        rec = self._find(p, errors.message_not_found())
        if not rec.from_bot:
            raise _Fail(ApiError(400, "Bad Request: message can't be edited"))
        markup = _inline_markup(p.get("reply_markup"))
        rich = p.get("rich_message")
        if isinstance(rich, dict):
            new = ("rich", str(rich.get("markdown", "")), None)
            plain = new[1]
        else:
            text = str(p.get("text", ""))
            if not text.strip():
                raise _Fail(ApiError(400, "Bad Request: message text is empty"))
            new = ("text", text, p.get("parse_mode"))
            plain = _plain(text, p.get("parse_mode"))
        old = (("rich", rec.rich, None) if rec.rich is not None
               else ("text", rec.raw_text, rec.parse_mode))
        if new == old and markup == rec.message.get("reply_markup"):
            raise _Fail(errors.not_modified())
        if new[0] == "rich":
            rec.rich, rec.raw_text, rec.parse_mode = new[1], None, None
        else:
            rec.rich, rec.raw_text, rec.parse_mode = None, new[1], new[2]
        rec.message["text"] = plain
        rec.message["edit_date"] = int(time.time())
        if markup is None:
            rec.message.pop("reply_markup", None)
        else:
            rec.message["reply_markup"] = markup
        rec.seq = next(self._seq)
        return dict(rec.message)

    def _m_editMessageReplyMarkup(self, p, f):
        rec = self._find(p, errors.message_not_found())
        markup = _inline_markup(p.get("reply_markup"))
        if markup == rec.message.get("reply_markup"):
            raise _Fail(errors.not_modified())
        if markup is None:
            rec.message.pop("reply_markup", None)
        else:
            rec.message["reply_markup"] = markup
        rec.message["edit_date"] = int(time.time())
        rec.seq = next(self._seq)
        return dict(rec.message)

    def _m_deleteMessage(self, p, f):
        rec = self._find(p, errors.delete_not_found())
        rec.deleted = True
        rec.seq = next(self._seq)
        return True

    def _m_deleteMessages(self, p, f):
        cid = p.get("chat_id")
        ids = p.get("message_ids") or []
        for mid in ids:
            rec = self._msgs.get((cid, int(mid)))
            if rec is not None:
                rec.deleted = True
                rec.seq = next(self._seq)
        return True

    def _m_setMessageReaction(self, p, f):
        rec = self._msgs.get((p.get("chat_id"), p.get("message_id")))
        if rec is None or rec.deleted:
            raise _Fail(ApiError(400, "Bad Request: message to react not found"))
        reaction = p.get("reaction") or []
        rec.reactions = [r.get("emoji") for r in reaction if isinstance(r, dict)]
        return True

    def _m_pinChatMessage(self, p, f):
        rec = self._find(p, ApiError(400, "Bad Request: message to pin not found"))
        rec.pinned = True
        return True

    def _m_sendDocument(self, p, f):
        cid = p.get("chat_id")
        if "document" in f:
            filename, data = f["document"]
        else:
            ref = str(p.get("document", ""))
            if ref not in self._files:
                raise _Fail(ApiError(400, "Bad Request: wrong file identifier/HTTP URL "
                                          "specified"))
            filename, data = self._files[ref]
        file_id, uniq = self._register_file(filename, data, "documents")
        body = {"document": {"file_id": file_id, "file_unique_id": uniq,
                             "file_name": filename, "file_size": len(data)}}
        if p.get("caption"):
            body["caption"] = _plain(str(p["caption"]), p.get("parse_mode"))
        msg = self._bot_message(cid, p, **body)
        self._store(msg, from_bot=True)
        return msg

    def _m_sendChatAction(self, p, f):
        self._chat_obj(p.get("chat_id"))
        return True

    def _m_answerCallbackQuery(self, p, f):
        return True

    @staticmethod
    def _scope_key(p) -> str:
        return json.dumps([p.get("scope") or {"type": "default"},
                           p.get("language_code") or ""], sort_keys=True)

    def _m_setMyCommands(self, p, f):
        self._commands[self._scope_key(p)] = list(p.get("commands") or [])
        return True

    def _m_deleteMyCommands(self, p, f):
        self._commands.pop(self._scope_key(p), None)
        return True

    def _m_getMyCommands(self, p, f):
        return list(self._commands.get(self._scope_key(p), []))

    def _m_setChatMenuButton(self, p, f):
        self._menu_buttons[str(p.get("chat_id"))] = p.get("menu_button")
        return True

    def _m_getFile(self, p, f):
        fid = str(p.get("file_id", ""))
        entry = self._files.get(fid)
        if entry is None:
            raise _Fail(ApiError(400, "Bad Request: invalid file_id"))
        path = self._file_paths[fid]
        return {"file_id": fid, "file_unique_id": f"u{fid}",
                "file_size": len(entry[1]), "file_path": path}

    def _register_file(self, filename: str, data: bytes, folder: str) -> tuple[str, str]:
        n = next(self._file_ids)
        fid = f"FAKEFILE{n:06d}"
        ext = ""
        if "." in filename:
            ext = "." + filename.rsplit(".", 1)[1]
        path = f"{folder}/file_{n}{ext}"
        self._files[fid] = (filename, data)
        self._file_paths[fid] = path
        self._paths[path] = data
        return fid, f"u{fid}"

    # -- updates ------------------------------------------------------------

    def _enqueue(self, kind: str, obj: dict) -> dict:
        upd = {"update_id": next(self._update_ids), kind: obj}
        self._updates.append(upd)
        return upd

    def _wake(self) -> None:
        loop, cond = self.loop, self._cond
        if loop is None or cond is None:
            return

        async def _notify():
            async with cond:
                cond.notify_all()

        asyncio.run_coroutine_threadsafe(_notify(), loop)

    def _require_polling(self) -> None:
        assert self.polling_started.is_set(), (
            "inject only after the daemon's first getUpdates: updates queued "
            "earlier are dropped by deleteWebhook(drop_pending_updates)")

    def _sender_chat(self, chat_id: int, sender: dict) -> dict:
        c = self._chats.get(chat_id)
        if c is None:
            raise KeyError(f"unknown chat {chat_id}: add_chat it first")
        return c

    def inject_text(self, chat_id: int, sender: dict, text: str, *,
                    reply_to: dict | int | None = None, thread_id: int | None = None,
                    entities: list[dict] | None = None) -> dict:
        self._require_polling()
        with self._lock:
            c = self._sender_chat(chat_id, sender)
            reply = None
            if isinstance(reply_to, int):
                reply = self._msgs[(chat_id, reply_to)].message
            elif isinstance(reply_to, dict):
                rec = self._msgs.get((chat_id, reply_to["message_id"]))
                reply = rec.message if rec else reply_to
            msg = U.text_message(self._new_mid(chat_id), c, sender, text,
                                 reply_to=reply, thread_id=thread_id, entities=entities)
            self._store(msg, from_bot=False)
            self._enqueue("message", msg)
        self._wake()
        return msg

    def inject_edited(self, chat_id: int, sender: dict, message_id: int, text: str) -> dict:
        self._require_polling()
        with self._lock:
            rec = self._msgs[(chat_id, message_id)]
            msg = U.edited(rec.message, text)
            rec.message = msg
            self._enqueue("edited_message", msg)
        self._wake()
        return msg

    def inject_callback(self, sender: dict, bot_message: dict, data: str) -> str:
        self._require_polling()
        with self._lock:
            key = (bot_message["chat"]["id"], bot_message["message_id"])
            rec = self._msgs.get(key)
            current = rec.message if rec else bot_message
            cb_id = f"cb{next(self._cb_ids)}"
            self._enqueue("callback_query", U.callback_query(cb_id, sender, current, data))
        self._wake()
        return cb_id

    def wait_answer(self, callback_id: str, timeout: float = 30) -> dict:
        call = self.wait_for(
            lambda c: c.method == "answerCallbackQuery"
            and c.params.get("callback_query_id") == callback_id,
            timeout=timeout, what=f"answerCallbackQuery for {callback_id}")
        return call.params

    def inject_document(self, chat_id: int, sender: dict, filename: str, data: bytes,
                        caption: str | None = None) -> dict:
        self._require_polling()
        with self._lock:
            c = self._sender_chat(chat_id, sender)
            fid, uniq = self._register_file(filename, data, "documents")
            msg = U.document_message(self._new_mid(chat_id), c, sender, file_id=fid,
                                     file_unique_id=uniq, filename=filename,
                                     size=len(data), caption=caption)
            self._store(msg, from_bot=False)
            self._enqueue("message", msg)
        self._wake()
        return msg

    def inject_voice(self, chat_id: int, sender: dict, data: bytes, duration: int = 1) -> dict:
        self._require_polling()
        with self._lock:
            c = self._sender_chat(chat_id, sender)
            fid, uniq = self._register_file("voice.oga", data, "voice")
            msg = U.voice_message(self._new_mid(chat_id), c, sender, file_id=fid,
                                  file_unique_id=uniq, size=len(data), duration=duration)
            self._store(msg, from_bot=False)
            self._enqueue("message", msg)
        self._wake()
        return msg

    def inject_migration(self, old_chat_id: int, new_chat_id: int, *,
                         by: dict | None = None, title: str | None = None) -> tuple[dict, dict]:
        """Upgrade the basic group *old* to the supergroup *new*: from now
        on every call naming *old* fails with ``migrate_to_chat_id``."""
        self._require_polling()
        with self._lock:
            old = self._chats[old_chat_id]
            new = U.chat(new_chat_id, "supergroup", title or old.get("title"))
            self._chats[new_chat_id] = new
            sender = by or U.user(1, "telegram", "Telegram")
            old_msg, new_msg = U.migration_pair(
                old, new, sender, old_message_id=self._new_mid(old_chat_id),
                new_message_id=self._new_mid(new_chat_id))
            self._store(old_msg, from_bot=False)
            self._store(new_msg, from_bot=False)
            self._migrated[old_chat_id] = new_chat_id
            self._enqueue("message", old_msg)
            self._enqueue("message", new_msg)
        self._wake()
        return old_msg, new_msg

    def inject_sender_chat(self, chat_id: int, text: str, sender_chat: dict) -> dict:
        self._require_polling()
        with self._lock:
            c = self._sender_chat(chat_id, {})
            msg = U.sender_chat_message(self._new_mid(chat_id), c, text, sender_chat)
            self._store(msg, from_bot=False)
            self._enqueue("message", msg)
        self._wake()
        return msg

    # -- queries ------------------------------------------------------------

    def mark(self) -> int:
        with self._lock:
            return self._calls[-1].seq if self._calls else 0

    def calls(self, method: str | None = None, chat_id: int | None = None,
              since: int | None = None) -> list[Call]:
        with self._lock:
            out = list(self._calls)
        return [c for c in out
                if (method is None or c.method == method)
                and (chat_id is None or c.params.get("chat_id") == chat_id)
                and (since is None or c.seq > since)]

    def wait_for(self, predicate: Callable[[Call], bool], timeout: float = 60, *,
                 since: int | None = None, what: str = "a matching call") -> Call:
        deadline = time.monotonic() + timeout
        while True:
            for c in self.calls(since=since):
                if predicate(c):
                    return c
            if time.monotonic() >= deadline:
                raise AssertionError(f"timed out after {timeout}s waiting for {what}; "
                                     f"last calls:\n  {self._tail()}")
            time.sleep(0.05)

    def _tail(self, n: int = 20) -> str:
        """The last *n* calls, one line each, credentials redacted."""
        return redact("\n  ".join(c.summary() for c in self.calls()[-n:]))

    def visible_messages(self, chat_id: int, *, include_users: bool = False) -> list[dict]:
        with self._lock:
            recs = sorted((r for (cid, _m), r in self._msgs.items()
                           if cid == chat_id and not r.deleted
                           and (include_users or r.from_bot)),
                          key=lambda r: r.message["message_id"])
            return [dict(r.message) for r in recs]

    def message(self, chat_id: int, message_id: int) -> dict | None:
        with self._lock:
            rec = self._msgs.get((chat_id, message_id))
            return dict(rec.message) if rec else None

    def find_button(self, chat_id: int, text_contains: str, *, since: int | None = None,
                    message_text_contains: str | None = None) -> tuple[dict, str] | None:
        """Newest visible bot message in *chat_id* with an inline button
        whose label contains *text_contains*: ``(message, callback_data)``."""
        with self._lock:
            recs = sorted((r for (cid, _m), r in self._msgs.items()
                           if cid == chat_id and not r.deleted and r.from_bot
                           and (since is None or r.seq > since)),
                          key=lambda r: r.message["message_id"], reverse=True)
            for r in recs:
                if message_text_contains and message_text_contains not in \
                        r.message.get("text", ""):
                    continue
                kb = (r.message.get("reply_markup") or {}).get("inline_keyboard") or []
                for row in kb:
                    for b in row:
                        if text_contains in b.get("text", "") and "callback_data" in b:
                            return dict(r.message), b["callback_data"]
        return None

    def wait_button(self, chat_id: int, text_contains: str, timeout: float = 60, **kw
                    ) -> tuple[dict, str]:
        deadline = time.monotonic() + timeout
        while True:
            found = self.find_button(chat_id, text_contains, **kw)
            if found:
                return found
            if time.monotonic() >= deadline:
                raise AssertionError(f"no button {text_contains!r} in chat {chat_id} "
                                     f"after {timeout}s; last calls:\n  {self._tail()}")
            time.sleep(0.05)

    def reactions(self, chat_id: int, message_id: int) -> list[str]:
        with self._lock:
            rec = self._msgs.get((chat_id, message_id))
            return list(rec.reactions) if rec else []

    def commands(self, scope: dict | None = None) -> list:
        with self._lock:
            return list(self._commands.get(self._scope_key({"scope": scope}), []))

    # -- HTTP ----------------------------------------------------------------

    async def _decode(self, request: web.Request) -> tuple[dict, dict]:
        params: dict = dict(request.query)
        files: dict = {}
        ctype = request.content_type
        if request.method == "POST" and request.can_read_body:
            if ctype == "application/json":
                body = await request.json()
                if isinstance(body, dict):
                    params.update(body)
            elif ctype in ("application/x-www-form-urlencoded", "multipart/form-data"):
                form = await request.post()
                for k, v in form.items():
                    if isinstance(v, web.FileField):
                        files[k] = (v.filename, v.file.read())
                    else:
                        params[k] = v
        return _normalise(params), files

    def _record(self, method: str, params: dict, files: dict, status: int,
                response: dict) -> Call:
        with self._lock:
            call = Call(seq=next(self._seq), ts=time.monotonic(), method=method,
                        params=params, files=files, status=status, response=response)
            self._calls.append(call)
            return call

    async def handle_method(self, request: web.Request) -> web.Response:
        method = request.match_info["method"]
        token = request.match_info["token"]
        params, files = await self._decode(request)
        if token != self.token:
            body = {"ok": False, "error_code": 401, "description": "Unauthorized"}
            with self._lock:
                self.bad_token_calls.append(method)
            self._record(method, params, files, 401, body)
            return web.json_response(body, status=401)
        if method == "getUpdates":
            return await self._get_updates(params, files)
        handler = getattr(self, f"_m_{method}", None) if method in SUPPORTED_METHODS else None
        if handler is None:
            body = {"ok": False, "error_code": 404,
                    "description": "Not Found: method not found"}
            with self._lock:
                self.unknown_methods.append(method)
            self._record(method, params, files, 404, body)
            return web.json_response(body, status=404)
        err = self._take_injected(method, params)
        try:
            if err is not None:
                raise _Fail(err)
            with self._lock:
                if method not in ("getMe", "deleteWebhook", "answerCallbackQuery",
                                  "setMyCommands", "deleteMyCommands", "getMyCommands",
                                  "getFile"):
                    self._check_migrated(params)
                result = handler(params, files)
            body = {"ok": True, "result": result}
            status = 200
        except _Fail as e:
            body, status = e.err.body(), e.err.status
        self._record(method, params, files, status, body)
        return web.json_response(body, status=status)

    async def _get_updates(self, params: dict, files: dict) -> web.Response:
        self.polling_started.set()
        offset = params.get("offset")
        limit = params.get("limit") or 100
        timeout = min(float(params.get("timeout") or 0), _MAX_POLL)
        deadline = time.monotonic() + timeout

        def _ready() -> list[dict]:
            with self._lock:
                if isinstance(offset, int):
                    self._updates[:] = [u for u in self._updates
                                        if u["update_id"] >= offset]
                return list(self._updates[:limit])

        found = _ready()
        while not found and not self._closing:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            assert self._cond is not None
            async with self._cond:
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=min(left, 0.5))
                except asyncio.TimeoutError:
                    pass
            found = _ready()
        body = {"ok": True, "result": found}
        self._record("getUpdates", params, files, 200,
                     {"ok": True, "result": [u["update_id"] for u in found]})
        return web.json_response(body)

    async def handle_file(self, request: web.Request) -> web.Response:
        if request.match_info["token"] != self.token:
            with self._lock:
                self.bad_token_calls.append("file")
            return web.json_response({"ok": False, "error_code": 401,
                                      "description": "Unauthorized"}, status=401)
        path = request.match_info["file_path"]
        with self._lock:
            data = self._paths.get(path)
        self._record("file", {"file_path": path}, {}, 200 if data is not None else 404, {})
        if data is None:
            return web.json_response({"ok": False, "error_code": 404,
                                      "description": "Not Found"}, status=404)
        return web.Response(body=data, content_type="application/octet-stream")

    def app(self) -> web.Application:
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_route("*", "/bot{token}/{method}", self.handle_method)
        app.router.add_get("/file/bot{token}/{file_path:.*}", self.handle_file)
        return app


class FakeBotApiThread:
    """Run a :class:`FakeBotApi` on 127.0.0.1 in a background thread."""

    def __init__(self, token: str, bot_id: int, bot_username: str):
        self.api = FakeBotApi(token, bot_id, bot_username)
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._runner: web.AppRunner | None = None
        self._stop_event: asyncio.Event | None = None
        self.base_url: str | None = None

    def start(self, timeout: float = 15) -> str:
        self._thread = threading.Thread(target=self._run, name="fake-bot-api", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("fake Bot API did not start")
        if self._error is not None:
            raise RuntimeError(f"fake Bot API failed to start: {self._error!r}")
        assert self.base_url is not None
        return self.base_url

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve(loop))
        except BaseException as e:  # noqa: BLE001 - reported to the starter
            self._error = e
            self._ready.set()
        finally:
            loop.close()

    async def _serve(self, loop: asyncio.AbstractEventLoop) -> None:
        self.api.loop = loop
        self.api._cond = asyncio.Condition()
        self._stop_event = asyncio.Event()
        self._runner = web.AppRunner(self.api.app(), access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        server = site._server
        assert server is not None and server.sockets
        host, port = server.sockets[0].getsockname()[:2]
        assert host == "127.0.0.1", host
        self.base_url = f"http://127.0.0.1:{port}"
        self._ready.set()
        try:
            await self._stop_event.wait()
        finally:
            self.api._closing = True
            async with self.api._cond:
                self.api._cond.notify_all()
            await self._runner.cleanup()

    def stop(self, timeout: float = 15) -> None:
        """Stop serving and join the thread. Idempotent."""
        t = self._thread
        if t is None:
            return
        loop = self.api.loop
        if loop is not None and self._stop_event is not None and t.is_alive():
            loop.call_soon_threadsafe(self._stop_event.set)
        t.join(timeout)
        if t.is_alive():
            raise RuntimeError("fake Bot API thread did not stop")
        self._thread = None

    def __enter__(self) -> FakeBotApiThread:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
