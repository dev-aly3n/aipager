"""Problem reports, step 1b3 (roadmap 8.112): the anomaly counters.

Each fixed counter key is bumped at the one place its event happens, by
one, and nothing else is: these tests drive each real site (or the
narrowest real function around it) and check the whole counter table.
Counters never carry text: Telegram's description is read only to pick a
class (:mod:`aipager.report.counters`).
"""

from __future__ import annotations

import ast
import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from telegram.error import BadRequest

from aipager import session_monitor as sm
from aipager.bot import rich_message as rm
from aipager.bot import transport
from aipager.dtach import hook_receiver as hr
from aipager.dtach import inject
from aipager.report import counters, store
from aipager.report import schema as sc
from aipager.state import SessionRegistry, Status, TrackedSession
from tests.test_bot_callbacks_perms import _answer_for

REPO = Path(__file__).resolve().parents[1]


def _counts() -> dict:
    return store.counters_24h()


@pytest.fixture
def mk_query():
    """A mocked Telegram CallbackQuery (as tests/test_bot_callbacks_perms)."""
    def _mk(callback_data, *, user_id=12345, message_id=42, text=""):
        query = MagicMock()
        query.data = callback_data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message = MagicMock()
        query.message.message_id = message_id
        query.message.text = text
        query.message.chat = MagicMock()
        query.message.chat.id = -100
        query.from_user = MagicMock()
        query.from_user.id = user_id
        update = MagicMock()
        update.callback_query = query
        update.effective_user = query.from_user
        update.effective_chat = MagicMock()
        update.effective_chat.id = -100
        return update, query
    return _mk


# ---- Telegram's failure classes -----------------------------------------------------

@pytest.mark.parametrize(("code", "description", "key"), [
    # The descriptions this daemon's journal actually holds (60 days):
    (400, "Bad Request: message was deleted", "tg_400_deleted"),
    (400, "Bad Request: message to be replied not found", "tg_400_deleted"),
    (400, "Bad Request: canceled by new edit message request", None),
    (400, "Bad Request: not Found", "tg_400_other"),
    (401, "Unauthorized", "tg_invalid_token"),
    (400, "Bad Request: message to edit not found", "tg_400_deleted"),
    (400, "Bad Request: MESSAGE_ID_INVALID", "tg_400_deleted"),
    (400, "Bad Request: message to delete not found", "tg_400_deleted"),
    (400, "Bad Request: can't parse entities: unsupported start tag", "tg_400_parse_entities"),
    (400, "Bad Request: message is too long", "tg_400_too_long"),
    (400, "Bad Request: text is too long", "tg_400_too_long"),
    (400, "Bad Request: message can't be edited", "tg_400_other"),
    (409, "Conflict", "tg_400_other"),
    (403, "Forbidden: bot was blocked by the user", "tg_forbidden"),
    (404, "Not Found", "tg_404_rich"),
    (400, "Bad Request: method not found", "tg_404_rich"),
    (502, "Bad Gateway", "tg_5xx"),
    (500, "Internal Server Error", "tg_5xx"),
    (400, "Bad Request: message is not modified", None),
    (429, "Too Many Requests: retry after 5", None),
    (0, "", None), ("400", "x", None)])
def test_a_failure_is_counted_by_its_class(code, description, key):
    assert counters.tg_failure_key(code, description) == key
    if key is not None:
        assert key in sc.COUNTER_KEYS


def test_a_400_key_never_raises_on_an_odd_description():
    for description in (None, 5, ["x"], b"too long"):
        assert counters.tg_400_key(description) == "tg_400_other"


@pytest.mark.parametrize(("data", "key"), [
    ({"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities"},
     "tg_400_parse_entities"),
    ({"ok": False, "error_code": 404, "description": "Not Found"}, "tg_404_rich"),
    ({"ok": False, "error_code": 502, "description": "Bad Gateway"}, "tg_5xx"),
    ({"ok": False, "error_code": 403, "description": "Forbidden"}, "tg_forbidden")])
def test_a_refused_rich_send_is_counted(data, key):
    with pytest.raises((rm.RichMessageFallbackRequired, rm.RichMessageBlocked)):
        asyncio.run(rm._handle_response(data, method="sendRichMessage",
                                        payload={"chat_id": 1}, allow_retry=False))
    assert _counts() == {key: 1}


@pytest.mark.parametrize(("data", "key"), [
    ({"ok": False, "error_code": 400, "description": "Bad Request: message is too long"},
     "tg_400_too_long"),
    ({"ok": False, "error_code": 404, "description": "Not Found"}, "tg_404_rich"),
    ({"ok": False, "error_code": 503, "description": "Service Unavailable"}, "tg_5xx"),
    ({"ok": False, "error_code": 400, "description": "Bad Request: message is not modified"},
     None),
    ({"ok": False, "error_code": 400, "description": "Bad Request: message was deleted"},
     "tg_400_deleted"),
    ({"ok": False, "error_code": 400,
      "description": "Bad Request: canceled by new edit message request"}, None)])
def test_a_refused_rich_edit_is_counted(data, key):
    asyncio.run(rm._handle_edit_response(data, payload={"chat_id": 1}))
    assert _counts() == ({key: 1} if key else {})


def test_a_gone_message_on_edit_is_counted_as_deleted():
    with pytest.raises(rm.RichMessageGone):
        asyncio.run(rm._handle_edit_response(
            {"ok": False, "error_code": 400, "description": "Bad Request: message to edit not found"},
            payload={"chat_id": 1}))
    assert _counts() == {"tg_400_deleted": 1}


def _too_many(retry_after: int) -> dict:
    return {"ok": False, "error_code": 429, "description": "Too Many Requests",
            "parameters": {"retry_after": retry_after}}


def test_a_small_429_is_counted_once_on_send_and_edit(monkeypatch):
    monkeypatch.setattr(rm, "get_rate_limiter", lambda: None)
    with pytest.raises(rm.RichMessageFallbackRequired):
        asyncio.run(rm._handle_response(_too_many(1), method="sendRichMessage",
                                        payload={"chat_id": 1}, allow_retry=False))
    asyncio.run(rm._handle_edit_response(_too_many(1), payload={"chat_id": 1},
                                         allow_retry=False))
    assert _counts() == {"tg_429_small": 2}


def test_a_ban_is_never_counted_as_a_small_429(monkeypatch):
    class Banned(Exception):
        pass

    def _ban(method, payload, retry_after):
        raise Banned

    monkeypatch.setattr(rm, "_ban_if_excessive", _ban)
    with pytest.raises(Banned):
        asyncio.run(rm._handle_response(_too_many(99_999), method="sendRichMessage",
                                        payload={"chat_id": 1}, allow_retry=False))
    with pytest.raises(Banned):
        asyncio.run(rm._handle_edit_response(_too_many(99_999), payload={"chat_id": 1}))
    assert _counts() == {}


def _network_down(monkeypatch):
    async def _post(method, payload, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(rm, "_post", _post)


def test_a_network_failure_on_a_rich_send_is_counted(monkeypatch):
    _network_down(monkeypatch)
    with pytest.raises(rm.RichMessageFallbackRequired):
        asyncio.run(rm._send_rich_message_once({"chat_id": 1}, allow_retry=True))
    assert _counts() == {"tg_network": 1}


def test_a_network_failure_on_a_rich_edit_is_counted(monkeypatch):
    _network_down(monkeypatch)
    assert asyncio.run(rm.edit_message_text_rich(1, 2, "x")) is None
    assert _counts() == {"tg_network": 1}


def test_the_edit_retry_counts_a_second_small_429_and_a_network_failure(monkeypatch):
    monkeypatch.setattr(rm, "get_rate_limiter", lambda: None)

    async def _again(method, payload, **kwargs):
        return _too_many(1)

    monkeypatch.setattr(rm, "_post", _again)
    assert asyncio.run(rm._handle_edit_response(_too_many(1), payload={"chat_id": 1})) is None
    assert _counts() == {"tg_429_small": 2}
    _network_down(monkeypatch)
    assert asyncio.run(rm._handle_edit_response(_too_many(1), payload={"chat_id": 1})) is None
    assert _counts() == {"tg_429_small": 3, "tg_network": 1}

    async def _broken(method, payload, **kwargs):
        raise RuntimeError("not the network")

    monkeypatch.setattr(rm, "_post", _broken)
    assert asyncio.run(rm._handle_edit_response(_too_many(1), payload={"chat_id": 1})) is None
    assert _counts() == {"tg_429_small": 4, "tg_network": 1}   # no network failure


def test_a_network_failure_on_the_429_retry_is_counted(monkeypatch):
    monkeypatch.setattr(rm, "get_rate_limiter", lambda: None)
    _network_down(monkeypatch)
    with pytest.raises(rm.RichMessageFallbackRequired):
        asyncio.run(rm._handle_response(_too_many(1), method="sendRichMessage",
                                        payload={"chat_id": 1}, allow_retry=True))
    assert _counts() == {"tg_429_small": 1, "tg_network": 1}


class _RefusingBot:
    """A bot whose send_message raises the next of *errors*, then returns."""

    def __init__(self, errors):
        self.errors = list(errors)

    async def send_message(self, *args, **kwargs):
        if self.errors:
            raise self.errors.pop(0)
        return "MSG"


def test_a_refused_plain_send_is_counted_by_class():
    bot = _RefusingBot([BadRequest("Bad Request: message is too long")])
    assert asyncio.run(transport._send_with_retry(bot, chat_id=1, text="x" * 5000)) == "MSG"
    assert _counts() == {"tg_400_too_long": 1}
    with pytest.raises(BadRequest):
        asyncio.run(transport._send_with_retry(
            _RefusingBot([BadRequest("Bad Request: can't parse entities")]), chat_id=1, text="x"))
    assert _counts() == {"tg_400_too_long": 1, "tg_400_parse_entities": 1}


# ---- the session monitor ------------------------------------------------------------

def _monitor(registry, notify=None) -> sm.SessionMonitor:
    return sm.SessionMonitor(registry, notify or AsyncMock())


def _scan(monitor, monkeypatch, names) -> None:
    monkeypatch.setattr("aipager.dtach.inject.list_sessions", AsyncMock(return_value=names))
    asyncio.run(monitor._scan())


def _busy(name: str, **fields) -> TrackedSession:
    sess = TrackedSession(name=name, label=name.removeprefix("claude-"), status=Status.BUSY)
    sess.last_hook_at = time.monotonic()
    for key, value in fields.items():
        setattr(sess, key, value)
    return sess


def test_an_interactive_demotion_is_counted(monkeypatch):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-cnt-int", label="cnt-int", status=Status.INTERACTIVE)
    sess.last_hook_at = time.monotonic() - sm.INTERACTIVE_TIMEOUT_SECONDS - 60
    sess.pending_permission = {"tool": "Bash"}
    registry._sessions[sess.name] = sess
    _scan(_monitor(registry), monkeypatch, [sess.name])
    assert sess.status == Status.BUSY
    assert _counts() == {"interactive_demoted": 1}


def test_a_prompt_not_taken_is_counted(monkeypatch):
    registry = SessionRegistry()
    sess = _busy("claude-cnt-pnt")
    registry._sessions[sess.name] = sess
    monkeypatch.setattr(sm, "prompt_not_taken", lambda s, now: s is sess)
    _scan(_monitor(registry), monkeypatch, [sess.name])
    assert _counts() == {"prompt_not_taken": 1}


@pytest.mark.parametrize(("action", "key"), [("restart", "watchdog_restart"),
                                             ("refresh", "watchdog_refresh")])
def test_a_card_watchdog_action_is_counted(monkeypatch, action, key):
    registry = SessionRegistry()
    sess = _busy("claude-cnt-wd")
    registry._sessions[sess.name] = sess
    monkeypatch.setattr(sm, "busy_card_watchdog_action",
                        lambda s, now, **kw: (action, 3.0) if s is sess else None)
    _scan(_monitor(registry), monkeypatch, [sess.name])
    assert _counts() == {key: 1}


def test_an_orphaned_card_is_counted(monkeypatch):
    registry = SessionRegistry()
    sess = _busy("claude-cnt-orph")
    registry._sessions[sess.name] = sess
    monkeypatch.setattr(sm, "orphan_card_due", lambda s, now: s is sess)
    _scan(_monitor(registry), monkeypatch, [sess.name])
    assert _counts() == {"orphan_card": 1}


def test_a_stale_busy_session_is_counted_once(monkeypatch):
    registry = SessionRegistry()
    sess = _busy("claude-cnt-stale")
    sess.last_hook_at = time.monotonic() - sm.STALE_BUSY_TIMEOUT - 60
    sess.pending_tool_started_at = time.monotonic() - sm.TOOL_INFLIGHT_MAX_SECONDS - 60
    registry._sessions[sess.name] = sess
    monitor = _monitor(registry)
    _scan(monitor, monkeypatch, [sess.name])
    _scan(monitor, monkeypatch, [sess.name])   # warned once: not again
    assert sess.stale_warned is True
    assert _counts() == {"stale_busy": 1}


def test_a_quiet_scan_counts_nothing(monkeypatch):
    registry = SessionRegistry()
    registry._sessions["claude-cnt-ok"] = _busy("claude-cnt-ok")
    _scan(_monitor(registry), monkeypatch, ["claude-cnt-ok"])
    assert _counts() == {}


# ---- permission answers ------------------------------------------------------------

@pytest.mark.parametrize("action", ["allow", "deny"])
def test_a_typed_answer_to_a_tool_prompt_is_a_keystroke_fallback(mk_bot, mk_query, run_async,
                                                                 action):
    pending = {"tool_summary": "Bash: x", "tool_info": {"name": "Bash"}}
    _keys, _calls, via = _answer_for(mk_bot(), mk_query, run_async, action, pending=pending)
    assert via == "keystroke_fallback"
    assert _counts() == {"keystroke_fallback": 1}


def test_a_hook_answer_is_no_fallback(mk_bot, mk_query, run_async):
    pending = {"tool_summary": "Bash: x", "tool_info": {"name": "Bash"},
               "hook_reply": {"path": "/x", "id": "y"}}
    _keys, _calls, via = _answer_for(mk_bot(), mk_query, run_async, "allow", pending=pending)
    assert via == "hook_decision" and _counts() == {}


def test_a_question_is_always_typed_and_never_counted(mk_bot, mk_query, run_async):
    pending = {"tool_summary": "AskUserQuestion (loading…)",
               "tool_info": {"name": "AskUserQuestion"}}
    _keys, _calls, via = _answer_for(mk_bot(), mk_query, run_async, "allow", pending=pending)
    assert via == "keystroke_fallback" and _counts() == {}


def test_a_degraded_allow_always_by_keys_is_counted(mk_bot, mk_query, run_async):
    pending = {"tool_summary": "Bash: x", "tool_info": {"name": "Bash", "always_available": False}}
    _answer_for(mk_bot(), mk_query, run_async, "allow_always", pending=pending)
    assert _counts() == {"allow_always_degraded": 1, "keystroke_fallback": 1}


def test_a_degraded_allow_always_by_the_hook_is_counted(mk_bot, mk_query, run_async):
    pending = {"tool_summary": "Bash: x",
               "tool_info": {"name": "Bash", "always_available": False},
               "hook_reply": {"path": "/x", "id": "y"}}
    _keys, _calls, via = _answer_for(mk_bot(), mk_query, run_async, "allow_always",
                                     pending=pending)
    assert via == "hook_decision"
    assert _counts() == {"allow_always_degraded": 1}


# ---- hook events and dtach ----------------------------------------------------------

def _deliver(message: dict) -> None:
    receiver = hr.HookReceiver(SessionRegistry(), AsyncMock())
    asyncio.run(receiver._on_datagram(json.dumps(message).encode()))


def test_a_hook_event_aipager_never_installed_is_counted():
    _deliver({"hook_event_name": "BrandNewEvent", "session": "claude-cnt-hook"})
    assert _counts() == {"unknown_hook_event": 1}


@pytest.mark.parametrize("event", ["queue_pickup", "safety_blocked", "permission_reply_timeout",
                                   "SubagentStart", "PreCompact"])
def test_a_known_or_internal_event_is_not_counted(event):
    _deliver({"hook_event_name": event, "session": "claude-cnt-hook"})
    assert "unknown_hook_event" not in _counts()


def test_the_internal_event_names_are_the_ones_the_hook_sends():
    tree = ast.parse((REPO / "aipager" / "dtach" / "notify_hook.py").read_text())
    sent = {node.values[i].value
            for node in ast.walk(tree) if isinstance(node, ast.Dict)
            for i, k in enumerate(node.keys)
            if isinstance(k, ast.Constant) and k.value == "hook_event_name"
            and isinstance(node.values[i], ast.Constant)}
    assert sent == set(hr._INTERNAL_HOOK_EVENTS)


def _fake_proc(returncode=1, communicate=None):
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = communicate or AsyncMock(return_value=(b"", b"boom"))
    return proc


def test_a_failed_or_timed_out_dtach_command_is_counted(monkeypatch):
    async def _failing(*args, **kwargs):
        return _fake_proc()

    monkeypatch.setattr(inject, "_create_subprocess_exec", _failing)
    assert asyncio.run(inject._run(["dtach"])) == (False, "")

    async def _hanging(*args, **kwargs):
        return _fake_proc(communicate=AsyncMock(side_effect=asyncio.TimeoutError))

    monkeypatch.setattr(inject, "_create_subprocess_exec", _hanging)
    assert asyncio.run(inject._run(["dtach"], timeout=0.01)) == (False, "")
    assert _counts() == {"dtach_failed": 2}


def test_a_failed_launch_is_counted(monkeypatch):
    async def _failing(*args, **kwargs):
        return _fake_proc(returncode=1)

    monkeypatch.setattr(inject, "_create_subprocess_exec", _failing)
    monkeypatch.setattr(inject.Path, "is_socket", lambda self: False)
    ok, _err = asyncio.run(inject.launch_session("cnt-launch-1b3"))
    assert ok is False
    assert _counts() == {"dtach_failed": 1}


def test_a_dtach_counter_never_raises(monkeypatch):
    monkeypatch.setattr(store, "record_counter", lambda *a, **k: 1 / 0)
    inject._count_dtach_failure()


# ---- every counter has its site ------------------------------------------------------

#: Bumped from a non-literal or a relayed path, so not found by the scan:
#: the environment counters (from a classified exception, step 1b) and the
#: hook counters (from a relayed datagram, step 1b2).
_BUMPED_ELSEWHERE = {key for key in sc.COUNTER_KEYS if key.startswith("env_")} | {
    "tg_retry_after", "tg_conflict", "tg_invalid_token", "hook_error", "hook_fail_closed"}


def _values(expr) -> set[str]:
    """The string keys an expression can evaluate to: a literal, or either
    side of a conditional (never the strings it compares against)."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return {expr.value}
    if isinstance(expr, ast.IfExp):
        return _values(expr.body) | _values(expr.orelse)
    return set()


def _literal_counter_keys() -> set[str]:
    """Every key a ``record_counter(...)`` call passes as a literal, or that
    :mod:`aipager.report.counters` returns, anywhere in aipager/."""
    keys: set[str] = set()
    for path in (REPO / "aipager").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(
                    node.func, "id", None)) == "record_counter" and node.args):
                keys |= _values(node.args[0])
            if path.name == "counters.py" and isinstance(node, ast.Return) and node.value:
                keys |= _values(node.value)
    return keys


def test_every_counter_key_is_bumped_somewhere():
    missing = set(sc.COUNTER_KEYS) - _BUMPED_ELSEWHERE - _literal_counter_keys()
    assert missing == set(), f"no site bumps {sorted(missing)}"


def test_every_literal_counter_is_a_schema_key():
    assert _literal_counter_keys() <= set(sc.COUNTER_KEYS)
