"""Black-box plumbing for the Mini App's in-app problem report page
(roadmap 8.112 follow-up).

Written against design.md ("Success criteria", the guard table),
spec.md ("Requirements on tests") and entrypoints.md only. Every request
goes through the real aiohttp app (``MiniAppServer._build_app``) on
aiohttp's test server, signed with the Mini App initData recipe the other
Mini App tests use.

Fakes, all at outside boundaries:

* :class:`FakeNet` is the GitHub key file and Sentry's envelope endpoint
  (``httpx.MockTransport``), injected through ``report_flow.SEND_TRANSPORT``
  (read at send time). Its POST can be held on a ``threading.Event`` to
  keep a send in flight. Nothing here reaches a real network.
* the drafts' clock is ``report_flow._mono`` (the documented seam), set
  through :func:`clock`.
* the write budget is ``MiniAppServer._write_hits`` (the documented seam):
  :class:`Api` clears it before each request unless a test fills it.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import itertools
import json
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager import config
from aipager.bot import report_flow
from aipager.miniapp.server import SCOPE_HEADER, MiniAppServer
from aipager.policy import load_policy
from aipager.report import endpoint, store
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry

OWNER = 256113222             # the root conftest pins config.CHAT_ID to this
OTHER_OWNER = 31337
STRANGER = 999_001
GROUP = -100777
ADMIN = 555
USER = 777
EM_DASH = "—"
BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"

# The release these tests run as. aipager.__version__ comes from package
# metadata: "0.0.0+unknown" without an installed package (an unknown build
# may not send), a stale number in a dev venv. The builder and the sender
# read it at call time, so a per-test pin makes the suite pass on py3.10 and
# py3.11 venvs with no metadata.
PINNED_VERSION = "0.7.20"


@pytest.fixture(autouse=True)
def _pinned_version(monkeypatch):
    monkeypatch.setattr("aipager.__version__", PINNED_VERSION)


@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


# ---- event loop -------------------------------------------------------------

@pytest.fixture
def run_async():
    """One fresh loop per call, closed afterwards with its tasks cancelled."""
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


# ---- the fake network -------------------------------------------------------

def key_doc(**changes) -> bytes:
    doc = {"v": 1, "dsn": endpoint.FALLBACK_DSN, "enabled": True, "min_version": "0.1"}
    doc.update(changes)
    return json.dumps(doc).encode()


class FakeNet:
    """GitHub's key file and Sentry's envelope endpoint, recorded."""

    def __init__(self, doc: bytes | None = None, sentry_status: int = 200,
                 sentry_error: Exception | None = None,
                 gate: threading.Event | None = None):
        self.doc = key_doc() if doc is None else doc
        self.sentry_status = sentry_status
        self.sentry_error = sentry_error
        self.gate = gate
        self.requests: list[httpx.Request] = []
        self.post_started = threading.Event()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == endpoint.ENDPOINT_URL:
            return httpx.Response(200, content=self.doc)
        if request.method == "POST":
            self.post_started.set()
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


def attachment_of(post: httpx.Request) -> bytes:
    """The ``report.json`` payload of a posted Sentry envelope."""
    _header, rest = post.content.split(b"\n", 1)
    while rest:
        item_line, rest = rest.split(b"\n", 1)
        item = json.loads(item_line)
        payload, rest = rest[:item["length"]], rest[item["length"] + 1:]
        if item.get("type") == "attachment":
            return payload
    raise AssertionError("no attachment in the envelope")


@pytest.fixture
def use_net(monkeypatch):
    def _use(fake: FakeNet) -> FakeNet:
        monkeypatch.setattr(report_flow, "SEND_TRANSPORT", fake.transport)
        return fake
    return _use


@pytest.fixture
def net(use_net):
    """A healthy fake network for every send."""
    return use_net(FakeNet())


# ---- the drafts' clock -------------------------------------------------------

@pytest.fixture
def clock(monkeypatch):
    """``clock[0]`` is what ``report_flow._mono()`` answers."""
    now = [10_000.0]
    monkeypatch.setattr(report_flow, "_mono", lambda: now[0])
    return now


# ---- bots ---------------------------------------------------------------------

def _policy():
    return load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))


def owner_dm_scope(owner=OWNER):
    return Scope(chat_id=owner, kind="dm", label="me", members=(
        Member(id=owner, label="me", role="owner"),))


def group_scope(*, owner_role="owner"):
    return Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=OWNER, label="me", role=owner_role),
        Member(id=ADMIN, label="ada", role="admin"),
        Member(id=USER, label="bob", role="user"),
    ))


@pytest.fixture
def make_bot(mk_bot):
    """``make_bot(mode)``: "personal" (owner = CHAT_ID), "scope" (an owner
    DM scope plus a team group), "scope_no_dm" (the group only: no owner),
    "scope_two_owners" (two owner DM scopes: ambiguous)."""
    def _make(mode="personal"):
        registry = SessionRegistry()
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
        return bot
    return _make


@pytest.fixture
def bot(make_bot):
    return make_bot("personal")


# ---- initData -----------------------------------------------------------------

_PORTS = itertools.count(8700)   # never bound: the test server picks its own port


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


def bad_headers(user_id=OWNER) -> dict:
    return headers(user_id, token="999:WRONG-token-not-the-bot")


# ---- the API driver ------------------------------------------------------------

class Api:
    """The real Mini App app on aiohttp's test server."""

    def __init__(self, bot):
        self.bot = bot
        self.srv = MiniAppServer(bot, bot.registry, port=next(_PORTS))
        self.client: TestClient | None = None
        self.keep_budget = False      # True: do not clear _write_hits

    async def __aenter__(self):
        self.client = TestClient(TestServer(self.srv._build_app()))
        await self.client.start_server()
        return self

    async def __aexit__(self, *exc):
        await self.client.close()

    def exhaust_budget(self, user_id=OWNER):
        self.keep_budget = True
        self.srv._write_hits[user_id] = [time.monotonic()] * 100

    async def post(self, path, *, user=OWNER, scope=None, json_body=None, raw=None,
                   hdrs=None):
        if not self.keep_budget:
            self.srv._write_hits.clear()
        h = hdrs if hdrs is not None else headers(user, scope=scope)
        if raw is not None:
            resp = await self.client.post(path, data=raw,
                                          headers={**h, "Content-Type": "application/json"})
        else:
            resp = await self.client.post(path, json={} if json_body is None else json_body,
                                          headers=h)
        try:
            body = await resp.json()
        except Exception:  # noqa: BLE001 - a non-JSON answer
            body = None
        return resp.status, body

    async def draft(self, **kw):
        return await self.post("/api/report/draft", **kw)

    async def send(self, draft_id, note=None, *, extra=None, **kw):
        body = {"draft": draft_id}
        if note is not None:
            body["note"] = note
        if extra:
            body.update(extra)
        return await self.post("/api/report/send", json_body=body, **kw)

    async def draft_id(self, **kw) -> str:
        status, body = await self.draft(**kw)
        assert status == 200, (status, body)
        return body["draft"]

    async def page(self) -> str:
        resp = await self.client.get("/")
        return await resp.text()


def serve(bot, run_async, fn):
    """Run ``await fn(api)`` against one live server for *bot*."""
    async def go():
        async with Api(bot) as api:
            return await fn(api)
    return run_async(go())


async def wait_until(pred, *, deadline=5.0, what="condition"):
    loop = asyncio.get_running_loop()
    end = loop.time() + deadline
    while not pred():
        if loop.time() >= end:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.01)


# ---- reports --------------------------------------------------------------------

_EXC_TYPES = (KeyError, ValueError, TypeError, IndexError, ZeroDivisionError)


def record_exc(kind: type[BaseException] = KeyError):
    """A caught exception recorded through the store, as the daemon's log
    capture does."""
    try:
        raise kind("x")
    except BaseException as exc:  # noqa: BLE001 - the point is to record it
        return store.record_exception(exc, where="daemon", trigger="log_exception")


def record_bug(fn="save", *, file="aipager/state.py", occasions=2, gap=1200):
    now = int(time.time())
    fp = None
    for i in range(occasions):
        fp = store.record_site("log_error", file=file, line=10 + i, fn=fn, where="daemon",
                               trigger="log_error", tier="bug",
                               now=now - (occasions - i) * gap)
    return fp


def exact(report: dict, note: str = "") -> bytes:
    """The exact text entrypoints.md promises is sent: the report, plus
    ``note`` as the LAST key when non-empty, json.dumps indent 2, no ASCII
    escaping."""
    doc = dict(report)
    if note:
        doc["note"] = note
    return json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8")


def sends_file() -> Path:
    return Path(config.REPORT_SENDS_FILE)


_SELF = sys.modules[__name__]


@pytest.fixture
def h():
    """This module (hyphenated scenario dirs are not importable packages)."""
    return _SELF
