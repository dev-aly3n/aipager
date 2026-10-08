"""Black-box plumbing for the problem report Telegram and Mini App UI
(roadmap 8.112 step 3).

Written against design.md "Success criteria", spec.md "Requirements on
tests" and entrypoints.md only. Updates are delivered the way Telegram
delivers them: the group -1 pre-handler (``new_flow.close_if_moved_on``)
first, then the registered handler (``_handle_help_cmd``,
``_handle_settings_cmd``, ``_handle_callback``, ``_handle_message``).

Fakes, all at outside boundaries:

* :class:`FakeTelegram` records every Bot API call on ``bot._app.bot``,
  on a tapped query and on a replied-to message, and keeps the current
  text and keyboard of every message it ever saw;
* :class:`FakeNet` is the GitHub key file and Sentry's envelope endpoint
  (``httpx.MockTransport``), injected through
  ``report_flow.SEND_TRANSPORT``; nothing here reaches a real network;
* the install source is the real ``detect_install_source`` with
  keyword overrides (a pipx/index install unless a test says otherwise).

No clock is patched: the offer check takes ``now``/``mono`` keyword
arguments (entrypoints.md), and every wall time here is relative to the
real ``time.time()`` read at the start of the test.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import sys
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from aipager import config, install_source
from aipager.bot import new_flow, report_flow
from aipager.policy import load_policy
from aipager.report import endpoint, markers, store
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

OWNER = 256113222             # the root conftest pins config.CHAT_ID to this
STRANGER = 999_001
GROUP = -100777
ADMIN = 555
USER = 777
READ_ONLY = 888
DAY = 86400
EM_DASH = "—"
SENDING = "Sending..."

PRE = re.compile(r"<pre>(.*?)</pre>", re.S)

# The release these scenarios run as. aipager.__version__ comes from package
# metadata: "0.0.0+unknown" when the package is not installed (an unknown
# build may not send), a stale number in a dev venv. The report builder and
# store read it at call time.
PINNED_VERSION = "0.7.20"


@pytest.fixture(autouse=True)
def _pinned_version(monkeypatch):
    """Run as a known release whether or not the package is installed."""
    monkeypatch.setattr("aipager.__version__", PINNED_VERSION)


# ---- event loop -------------------------------------------------------------

@pytest.fixture
def run_async():
    """One fresh loop per call, closed afterwards with its tasks cancelled,
    so a background edit can never leak into the next test."""
    loops: list[asyncio.AbstractEventLoop] = []

    def _run(coro):
        loop = asyncio.new_event_loop()
        loops.append(loop)
        return loop.run_until_complete(coro)

    yield _run
    for loop in loops:
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            loop.close()


async def settle(rounds: int = 5):
    """Let background tasks (closing edits, toasts) run."""
    for _ in range(rounds):
        await asyncio.sleep(0.02)


# ---- the fake Telegram ------------------------------------------------------

def _markup_rows(markup):
    if markup is None or not hasattr(markup, "inline_keyboard"):
        return []
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


class FakeTelegram:
    """Every Bot API call the bot makes, and the current state of every
    message (text, keyboard) the fake has seen."""

    def __init__(self, bot):
        self.bot = bot
        self.events: list[dict] = []
        self.messages: dict[tuple[int, int], dict] = {}
        self.toasts: list[tuple[int, str]] = []
        self.documents: list[dict] = []
        self.deleted: list[tuple[int, int]] = []
        self._ids = iter(range(5000, 10**6))
        self.fail_send = None          # an exception (or a callable) for send_message
        self.send_returns_none = False
        self.on_send = None            # a callable run inside send_message
        api = bot._app.bot
        api.username = "aipager_test_bot"
        api.send_message = AsyncMock(side_effect=self._send_message)
        api.edit_message_text = AsyncMock(side_effect=self._edit_message_text)
        api.edit_message_reply_markup = AsyncMock(side_effect=self._edit_markup)
        api.send_document = AsyncMock(side_effect=self._send_document)
        api.delete_message = AsyncMock(side_effect=self._delete)
        api.answer_callback_query = AsyncMock(side_effect=self._answer_cbq)
        api.set_message_reaction = AsyncMock()
        api.send_chat_action = AsyncMock()
        api.pin_chat_message = AsyncMock()
        api.unpin_chat_message = AsyncMock()
        bot._update_bot_commands = AsyncMock()
        bot._maybe_update_bot_name = AsyncMock()

    # -- message objects ------------------------------------------------------
    def message_obj(self, chat_id, message_id):
        msg = MagicMock()
        msg.chat = MagicMock()
        msg.chat.id = chat_id
        msg.chat_id = chat_id
        msg.message_id = message_id
        msg.text = (self.messages.get((chat_id, message_id)) or {}).get("text", "")

        async def _edit_text(*a, **kw):
            return self._apply_edit(chat_id, message_id, a, kw, via="message.edit_text")

        async def _reply_text(*a, **kw):
            return self._new(chat_id, a, kw, via="message.reply_text")

        msg.edit_text = AsyncMock(side_effect=_edit_text)
        msg.reply_text = AsyncMock(side_effect=_reply_text)
        msg.edit_reply_markup = AsyncMock(
            side_effect=lambda *a, **kw: self._set_markup(chat_id, message_id,
                                                          kw.get("reply_markup")))
        msg.delete = AsyncMock(side_effect=lambda *a, **kw: self.deleted.append(
            (chat_id, message_id)))
        return msg

    def _new(self, chat_id, args, kwargs, *, via):
        if self.fail_send is not None and via == "send_message":
            exc = self.fail_send() if callable(self.fail_send) else self.fail_send
            if exc is not None:
                raise exc
        if self.on_send is not None and via == "send_message":
            self.on_send(chat_id, args, kwargs)
        text = kwargs.get("text", args[0] if args else None)
        mid = next(self._ids)
        self.messages[(chat_id, mid)] = {"text": text, "markup": kwargs.get("reply_markup"),
                                         "kwargs": dict(kwargs)}
        self.events.append({"op": "send", "via": via, "chat_id": chat_id, "message_id": mid,
                            "text": text, "markup": kwargs.get("reply_markup"),
                            "kwargs": dict(kwargs)})
        if via == "send_message" and self.send_returns_none:
            return None
        return self.message_obj(chat_id, mid)

    async def _send_message(self, *args, **kwargs):
        chat_id = kwargs.pop("chat_id", None)
        if chat_id is None and args:
            chat_id, args = args[0], args[1:]
        return self._new(chat_id, args, kwargs, via="send_message")

    def _apply_edit(self, chat_id, message_id, args, kwargs, *, via):
        text = kwargs.get("text", args[0] if args else None)
        entry = self.messages.setdefault((chat_id, message_id), {})
        entry["text"] = text
        entry["markup"] = kwargs.get("reply_markup")
        self.events.append({"op": "edit", "via": via, "chat_id": chat_id,
                            "message_id": message_id, "text": text,
                            "markup": kwargs.get("reply_markup"), "kwargs": dict(kwargs)})
        return self.message_obj(chat_id, message_id)

    async def _edit_message_text(self, *args, **kwargs):
        chat_id = kwargs.get("chat_id")
        message_id = kwargs.get("message_id")
        return self._apply_edit(chat_id, message_id, args, kwargs, via="edit_message_text")

    def _set_markup(self, chat_id, message_id, markup):
        entry = self.messages.setdefault((chat_id, message_id), {})
        entry["markup"] = markup
        self.events.append({"op": "markup", "chat_id": chat_id, "message_id": message_id,
                            "markup": markup})
        return True

    async def _edit_markup(self, *args, **kwargs):
        return self._set_markup(kwargs.get("chat_id"), kwargs.get("message_id"),
                                kwargs.get("reply_markup"))

    async def _send_document(self, *args, **kwargs):
        chat_id = kwargs.pop("chat_id", None)
        if chat_id is None and args:
            chat_id, args = args[0], args[1:]
        doc = kwargs.get("document", args[0] if args else None)
        data = getattr(doc, "input_file_content", None)
        if data is None and isinstance(doc, (bytes, bytearray)):
            data = bytes(doc)
        if data is None and hasattr(doc, "read"):
            data = doc.read()
        mid = next(self._ids)
        record = {"chat_id": chat_id, "message_id": mid, "bytes": data,
                  "filename": getattr(doc, "filename", kwargs.get("filename")),
                  "kwargs": dict(kwargs)}
        self.documents.append(record)
        self.events.append({"op": "document", **record})
        return self.message_obj(chat_id, mid)

    async def _delete(self, *args, **kwargs):
        chat_id = kwargs.get("chat_id", args[0] if args else None)
        mid = kwargs.get("message_id", args[1] if len(args) > 1 else None)
        self.deleted.append((chat_id, mid))
        return True

    async def _answer_cbq(self, *args, **kwargs):
        self.toasts.append((0, kwargs.get("text", args[1] if len(args) > 1 else "")))
        return True

    # -- observations ---------------------------------------------------------
    def sent(self, chat_id=None):
        return [e for e in self.events if e["op"] == "send"
                and (chat_id is None or e["chat_id"] == chat_id)]

    def cards(self, chat_id=None):
        """Every message whose first text was a report preview card."""
        return [e for e in self.sent(chat_id)
                if isinstance(e["text"], str) and "Report a problem" in e["text"]
                and _has_data(e["markup"], "_:rp:send")]

    def text_of(self, chat_id, message_id):
        return (self.messages.get((chat_id, message_id)) or {}).get("text") or ""

    def buttons_of(self, chat_id, message_id):
        return _markup_rows((self.messages.get((chat_id, message_id)) or {}).get("markup"))

    def toast_texts(self):
        return [t for _, t in self.toasts if t]

    def all_strings(self) -> list[str]:
        out: list[str] = []
        for e in self.events:
            if isinstance(e.get("text"), str):
                out.append(e["text"])
            for text, data in _markup_rows(e.get("markup")):
                out.append(str(text))
        out.extend(self.toast_texts())
        return out


def _has_data(markup, data) -> bool:
    return any(d == data for _, d in _markup_rows(markup))


def preview_block(card_text: str) -> str | None:
    """The report JSON shown inline on a card, html-unescaped."""
    m = PRE.search(card_text or "")
    return html.unescape(m.group(1)) if m else None


# ---- the fake network -------------------------------------------------------

def key_doc(**changes) -> bytes:
    doc = {"v": 1, "dsn": endpoint.FALLBACK_DSN, "enabled": True, "min_version": "0.1"}
    doc.update(changes)
    return json.dumps(doc).encode()


class FakeNet:
    """GitHub's key file and Sentry's envelope endpoint, recorded, with the
    thread each request ran on."""

    def __init__(self, doc: bytes | None = None, sentry_status: int = 200,
                 sentry_error: Exception | None = None, gate: threading.Event | None = None):
        self.doc = key_doc() if doc is None else doc
        self.sentry_status = sentry_status
        self.sentry_error = sentry_error
        self.gate = gate
        self.requests: list[httpx.Request] = []
        self.threads: list[threading.Thread] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.threads.append(threading.current_thread())
        if str(request.url) == endpoint.ENDPOINT_URL:
            return httpx.Response(200, content=self.doc)
        if request.method == "POST":
            if self.gate is not None:
                self.gate.wait(5)
            if self.sentry_error is not None:
                raise self.sentry_error
            return httpx.Response(self.sentry_status, json={"id": "x"})
        return httpx.Response(404)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    @property
    def posts(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]


def parse_envelope(body: bytes):
    header_line, rest = body.split(b"\n", 1)
    items = []
    while rest:
        item_line, rest = rest.split(b"\n", 1)
        item = json.loads(item_line)
        payload, rest = rest[:item["length"]], rest[item["length"]:]
        rest = rest[1:]
        items.append((item, payload))
    return json.loads(header_line), items


def attachment_of(post: httpx.Request) -> bytes:
    _header, items = parse_envelope(post.content)
    return next(p for h, p in items if h.get("type") == "attachment")


@pytest.fixture
def net(monkeypatch):
    """A healthy fake network, injected for every Send."""
    fake = FakeNet()
    monkeypatch.setattr(report_flow, "SEND_TRANSPORT", fake.transport)
    return fake


@pytest.fixture
def use_net(monkeypatch):
    def _use(fake: FakeNet) -> FakeNet:
        monkeypatch.setattr(report_flow, "SEND_TRANSPORT", fake.transport)
        return fake
    return _use


# ---- bots --------------------------------------------------------------------

def _policy():
    return load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))


def owner_dm_scope(owner=OWNER, role="owner"):
    return Scope(chat_id=owner, kind="dm", label="me", members=(
        Member(id=owner, label="me", role=role),))


def group_scope(*, owner_role="owner"):
    return Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=OWNER, label="me", role=owner_role),
        Member(id=ADMIN, label="ada", role="admin"),
        Member(id=USER, label="bob", role="user"),
        Member(id=READ_ONLY, label="rob", role="read_only"),
    ))


@pytest.fixture
def make_bot(mk_bot):
    """``make_bot(mode)``: "personal" (owner = CHAT_ID), "scope" (an owner
    DM scope plus a team group), "scope_no_dm" (the group only: no owner),
    "scope_two_owners" (two owner DM scopes: ambiguous)."""
    def _make(mode="personal", *, registry=None):
        registry = registry or SessionRegistry()
        if mode == "personal":
            bot = mk_bot(registry)
        elif mode == "scope":
            bot = mk_bot(registry, scopes=[owner_dm_scope(), group_scope()])
        elif mode == "scope_no_dm":
            bot = mk_bot(registry, scopes=[group_scope(owner_role="admin")])
        elif mode == "scope_two_owners":
            bot = mk_bot(registry, scopes=[owner_dm_scope(), owner_dm_scope(ADMIN),
                                           group_scope()])
        else:
            raise AssertionError(mode)
        bot.policy = _policy()
        bot.tg = FakeTelegram(bot)
        return bot
    return _make


@pytest.fixture
def bot(make_bot):
    return make_bot("personal")


# ---- updates -------------------------------------------------------------------

class Driver:
    """Deliver updates the way Telegram does: the group -1 pre-handler,
    then the handler."""

    def __init__(self, bot):
        self.bot = bot
        self._ids = iter(range(100, 10**6))

    def _message_update(self, text, *, user, chat, reply_to=None):
        mid = next(self._ids)
        update = MagicMock()
        msg = MagicMock()
        msg.text = text
        msg.caption = None
        msg.message_id = mid
        msg.chat = MagicMock()
        msg.chat.id = chat
        msg.chat_id = chat
        msg.chat.type = "private" if chat > 0 else "supergroup"
        msg.reply_to_message = None
        msg.quote = None
        msg.external_reply = None
        msg.forward_origin = None
        msg.via_bot = None
        msg.media_group_id = None
        msg.photo = ()
        msg.voice = None
        msg.audio = None
        msg.video = None
        msg.video_note = None
        msg.document = None
        msg.sticker = None
        msg.animation = None
        msg.web_app_data = None
        msg.from_user = MagicMock()
        msg.from_user.id = user
        tg = self.bot.tg

        async def _reply_text(*a, **kw):
            return tg._new(chat, a, kw, via="message.reply_text")
        msg.reply_text = AsyncMock(side_effect=_reply_text)
        msg.reply_html = AsyncMock(side_effect=_reply_text)
        if reply_to is not None:
            msg.reply_to_message = MagicMock(message_id=reply_to, text="earlier", caption=None)
        update.message = msg
        update.effective_message = msg
        update.callback_query = None
        update.edited_message = None
        update.effective_user = MagicMock()
        update.effective_user.id = user
        update.effective_chat = MagicMock()
        update.effective_chat.id = chat
        update.effective_chat.type = msg.chat.type
        return update

    async def text(self, text, *, user=OWNER, chat=OWNER, reply_to=None):
        update = self._message_update(text, user=user, chat=chat, reply_to=reply_to)
        await new_flow.close_if_moved_on(self.bot, update, MagicMock())
        await self.bot._handle_message(update, MagicMock())
        await settle()
        return update

    async def command(self, name, *, user=OWNER, chat=OWNER, handler=None):
        update = self._message_update(name, user=user, chat=chat)
        await new_flow.close_if_moved_on(self.bot, update, MagicMock())
        if handler is not None:
            await handler(update, MagicMock())
        else:
            attr = {"/help": "_handle_help_cmd", "/settings": "_handle_settings_cmd"}[name]
            await getattr(self.bot, attr)(update, MagicMock())
        await settle()
        return update

    async def media(self, kind, *, user=OWNER, chat=OWNER):
        """A photo or a voice note: only the pre-handler is driven (the
        media handlers' own work is not under test)."""
        update = self._message_update(None, user=user, chat=chat)
        if kind == "photo":
            update.message.photo = [MagicMock()]
            update.message.caption = "look"
        elif kind == "voice":
            update.message.voice = MagicMock()
        else:
            raise AssertionError(kind)
        await new_flow.close_if_moved_on(self.bot, update, MagicMock())
        await settle()
        return update

    async def tap(self, data, *, user=OWNER, chat=OWNER, message_id=42, settle_rounds=5,
                  wait_outcome=None):
        """Deliver a button tap. Send runs in the background, so after a
        ``_:rp:send`` tap this waits (bounded, :data:`OUTCOME_DEADLINE`)
        until the card's outcome edit has landed, i.e. the card no longer
        reads "Sending...". ``wait_outcome=False`` skips that for a tap
        made on purpose while a send is held open; by default it follows
        ``settle_rounds`` (0 means "return as soon as the handler does")."""
        tg = self.bot.tg
        query = MagicMock()
        query.data = data
        query.id = str(next(self._ids))
        query.from_user = MagicMock()
        query.from_user.id = user
        query.message = tg.message_obj(chat, message_id)

        async def _answer(*a, **kw):
            tg.toasts.append((message_id, kw.get("text", a[0] if a else "") or ""))
            return True

        async def _edit(*a, **kw):
            return tg._apply_edit(chat, message_id, a, kw, via="query.edit_message_text")

        async def _markup(*a, **kw):
            return tg._set_markup(chat, message_id, kw.get("reply_markup",
                                                           a[0] if a else None))

        query.answer = AsyncMock(side_effect=_answer)
        query.edit_message_text = AsyncMock(side_effect=_edit)
        query.edit_message_reply_markup = AsyncMock(side_effect=_markup)
        update = MagicMock()
        update.callback_query = query
        update.message = None
        update.effective_message = query.message
        update.effective_user = query.from_user
        update.effective_chat = MagicMock()
        update.effective_chat.id = chat
        update.effective_chat.type = "private" if chat > 0 else "supergroup"
        await new_flow.close_if_moved_on(self.bot, update, MagicMock())
        await self.bot._handle_callback(update, MagicMock())
        await settle(settle_rounds)
        if wait_outcome is None:
            wait_outcome = settle_rounds > 0
        if wait_outcome and data == "_:rp:send":
            await wait_for_outcome(self.bot, chat, message_id)
        return query


OUTCOME_DEADLINE = 5.0


async def wait_for_outcome(bot, chat_id, message_id, *, deadline=OUTCOME_DEADLINE):
    """Yield to the loop in short steps until the card no longer reads
    "Sending..." (its background send has edited the outcome in), and
    fail clearly if that does not happen within *deadline* seconds."""
    loop = asyncio.get_running_loop()
    end = loop.time() + deadline
    while SENDING in bot.tg.text_of(chat_id, message_id):
        if loop.time() >= end:
            raise AssertionError(
                f"card {chat_id}/{message_id} still reads {SENDING!r} {deadline} s after "
                "the Send tap: the background send never edited its outcome in")
        await asyncio.sleep(0.01)
    await settle(2)


@pytest.fixture
def drive():
    def _drive(bot):
        return Driver(bot)
    return _drive


# ---- reports and offers -----------------------------------------------------------

async def open_card(bot, drive_, *, chat=OWNER, user=OWNER):
    """Tap the /help button's ``_:rp:open`` and return the card's
    (chat_id, message_id) in the owner DM."""
    before = len(bot.tg.cards())
    await drive_.tap("_:rp:open", user=user, chat=chat)
    cards = bot.tg.cards()
    assert len(cards) == before + 1, "no preview card was posted"
    card = cards[-1]
    return card["chat_id"], card["message_id"]


def record_bug(fn="save", *, file="aipager/state.py", now=None, occasions=2, gap=1200,
               tier="bug"):
    """A daemon bug seen on *occasions* separate occasions (store API)."""
    now = int(time.time()) if now is None else int(now)
    fp = None
    for i in range(occasions):
        fp = store.record_site("log_error", file=file, line=10 + i, fn=fn, where="daemon",
                               trigger="log_error", tier=tier,
                               now=now - (occasions - i) * gap)
    return fp


_EXC_TYPES = (KeyError, ValueError, TypeError, IndexError, ZeroDivisionError,
              AttributeError, LookupError, RuntimeError, NameError, AssertionError,
              ArithmeticError, UnicodeError)


def record_exc(kind: type[BaseException] = KeyError):
    """A caught exception recorded through the store (an error entry that
    carries a ``type``), as the daemon's log capture does."""
    try:
        raise kind("x")
    except BaseException as exc:  # noqa: BLE001 - the point is to record it
        return store.record_exception(exc, where="daemon", trigger="log_exception")


def record_many_exc(n: int):
    return [record_exc(k) for k in _EXC_TYPES[:n]]


@pytest.fixture
def released_install(tmp_path, monkeypatch):
    """The install source as a released pipx install (not a developer
    install, D4), or whatever ``set(kind)`` says: "index", "editable",
    "local"."""
    real = install_source.detect_install_source
    state = {"origin": "index"}
    prefix = tmp_path / "installs" / "pipx" / "venvs" / "aipager"
    prefix.mkdir(parents=True, exist_ok=True)
    (prefix / "pipx_metadata.json").write_text("{}")

    def _kwargs():
        if state["origin"] == "editable":
            du = {"url": "file:///src/aipager", "dir_info": {"editable": True}}
        elif state["origin"] == "local":
            du = {"url": "file:///src/checkouts/aipager-local", "dir_info": {}}
        else:
            du = None
        return {"prefix": str(prefix), "executable": str(prefix / "bin" / "python"),
                "base_prefix": "/usr", "direct_url": du, "env": {"PATH": "/usr/bin:/bin"}}

    monkeypatch.setattr(install_source, "detect_install_source",
                        lambda **kw: real(**{**_kwargs(), **kw}))

    def _set(origin):
        state["origin"] = origin
    return _set


class OfferWorld:
    """Everything the automatic offer needs open: a bug seen twice, an
    install marker 10 days old, a daemon up for 2 hours, a released
    install, the owner seen in their DM just now, and every session idle
    for several minutes of monotonic time. Each test flips one thing."""

    def __init__(self, bot, drive_, now):
        self.bot = bot
        self.drive = drive_
        self.now = now
        self.mono = 1_000_000.0

    def seed(self, *, install_age=10 * DAY, started_ago=2 * 3600, bug=True):
        if install_age is not None:
            markers.first_start(now=self.now - install_age)
        if started_ago is not None:
            markers.set_started_at(self.now - started_ago)
        if bug:
            self.fp = record_bug(now=self.now - 600)
        return self

    async def owner_acts(self, *, chat=OWNER, user=OWNER):
        """The owner does something harmless in a chat (/help)."""
        await self.drive.command("/help", user=user, chat=chat)

    def notices(self):
        return [e for e in self.bot.tg.sent(OWNER)
                if any(str(d).startswith("_:rp:op:") for _, d in _markup_rows(e["markup"]))]

    async def run_ticks(self, *, busy_during=None, seconds=360, step=20, offset=60):
        """Monitor ticks every *step* s of monotonic time for *seconds*;
        ``busy_during(i)`` may flip sessions busy/idle per tick. Wall time
        advances with it, starting *offset* s after ``self.now``."""
        from aipager.bot import report_offer
        n = int(seconds // step) + 1
        for i in range(n):
            if busy_during is not None:
                busy_during(i)
            await report_offer.tick(self.bot, now=self.now + offset + i * step,
                                    mono=self.mono + i * step)
            await settle(1)
        await settle()
        return self.notices()


@pytest.fixture
def offer_world(make_bot, drive, released_install):
    def _make(mode="personal", **seed):
        b = make_bot(mode)
        # The simulated window lies in the PAST of the real clock: a tap is
        # settled at the real time, and an offer dated in the future is
        # (rightly) re-dated by policy.settle, which would make every
        # answer stale. 900 s back leaves the last tick (+420 s) behind it.
        world = OfferWorld(b, drive(b), int(time.time()) - 900)
        return world.seed(**seed)
    return _make


def add_session(registry, label, *, status=Status.IDLE, chat_id=OWNER):
    s = registry.get_or_create(f"claude-{label}")
    s.label = label
    s.status = status
    s.scope_chat_id = chat_id
    return s


def reports_file() -> Path:
    return Path(config.REPORTS_FILE)


def install_file() -> Path:
    return Path(config.REPORT_INSTALL_FILE)


_SELF = sys.modules[__name__]


@pytest.fixture
def h():
    """This module (hyphenated scenario dirs are not importable packages)."""
    return _SELF
