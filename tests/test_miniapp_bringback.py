"""The Mini App and a session parked on a Claude Code background job.

A session whose conversation moved into a Claude Code background job
(``←`` on an empty prompt) is ``"background"`` on the grid and its page
(T5.1). Its menu offers Bring it back, Stop, Rename, Kill and Restart, in
the canonical order the page renders (T5.2). ``POST
/api/sessions/{label}/bringback`` brings it back through the one bring-back
core, with Restart's permission check; a busy job answers 409 ``bg_busy``
with ``can_force``, and the page asks in the app and asks again with
``{"force": true}``; Restart and Resume take the same body (T5.3). On
success the chat reads "↩️ <session> is back from Claude Code's
background." once (T5.4).

The real core runs against tests/test_bring_back_core.py's fake world
(Claude's files under tmp_path, ``claude stop`` through the runner seam,
the dtach calls through ``inject``'s attributes). Nothing real is spawned.
"""

from __future__ import annotations

import re
import time
from unittest.mock import AsyncMock

import pytest

from aipager import i18n, preferences
from aipager.bg_text import BG_BACK
from aipager.bot.session_ops import RestartOutcome, ResumeOutcome
from aipager.miniapp import sessions as ma
from aipager.miniapp.sessions import (
    NO_PERMISSION_REASON,
    NOT_BUSY_REASON,
    grid_totals,
    session_actions,
    session_detail,
    session_summary,
    sort_for_display,
)
from aipager.state import Status, TrackedSession
from tests.test_bring_back_core import JOB, JS, NAME, WORKER, X, World
from tests.test_miniapp_session_actions_api import (  # noqa: F401 - fixtures
    ADMIN_ID,
    DEVELOPER_ID,
    READONLY_ID,
    SCOPE_CHAT_ID,
    _client_for,
    _configured_bot_token,
    _hdr,
    _mk_session,
    server,
)

LABEL = "x"


def _parked(status=Status.IDLE, since=None) -> TrackedSession:
    sess = TrackedSession(name="claude-p1", label="p1")
    sess.status = status
    sess.parked_job = JOB
    sess.parked_job_session_id = JS
    sess.parked_since = time.time() - 90 if since is None else since
    return sess


# ---- T5.1: rows, rank and totals ---------------------------------------------------

@pytest.mark.parametrize("status", [Status.IDLE, Status.BUSY, Status.INTERACTIVE,
                                    Status.UNKNOWN])
def test_a_parked_live_session_is_background(status):
    row = session_summary(_parked(status), time.monotonic())
    assert row["status"] == "background"
    assert row["parked"] is True
    assert row["waiting_summary"] is None and row["waiting_kind"] is None


def test_a_session_not_parked_keeps_its_own_status():
    sess = _parked(Status.BUSY)
    sess.parked_job = ""
    row = session_summary(sess, time.monotonic())
    assert row["status"] == "busy" and row["parked"] is False


def test_a_parked_session_that_ended_is_gone():
    """GONE wins: a finished session is never shown as in the background."""
    row = session_summary(_parked(Status.GONE), time.monotonic())
    assert row["status"] == "gone" and row["parked"] is False


def _row(label, status, age):
    return {"label": label, "status": status, "last_active_seconds_ago": age}


def test_background_sorts_with_the_resting_sessions():
    rows = [_row("g", "gone", 1), _row("u", "unknown", 1), _row("bg", "background", 5),
            _row("i", "idle", 9), _row("b", "busy", 50), _row("w", "waiting", 99)]
    order = [r["label"] for r in sort_for_display(rows)]
    assert order == ["w", "b", "bg", "i", "u", "g"]


def test_background_is_live_in_the_totals():
    totals = grid_totals([_row("bg", "background", 1), _row("g", "gone", 1)])
    assert totals["live"] == 1 and totals["gone"] == 1 and totals["waiting"] == 0


def test_the_detail_says_parked_and_since_when():
    d = session_detail(_parked(Status.IDLE, since=time.time() - 90), time.monotonic())
    assert d["status"] == "background" and d["parked"] is True
    assert 89 <= d["parked_seconds_ago"] <= 92


@pytest.mark.parametrize("since", [0.0, -5.0])
def test_an_unknown_park_time_is_none(since):
    assert session_detail(_parked(since=since), time.monotonic())["parked_seconds_ago"] is None


def test_a_session_not_parked_has_no_park_age():
    sess = _parked(Status.IDLE)
    sess.parked_job = ""
    d = session_detail(sess, time.monotonic())
    assert d["parked"] is False and d["parked_seconds_ago"] is None


# ---- T5.2: the action matrix and the page's order ---------------------------------

def test_the_background_menu():
    actions = session_actions("background", resumable=True, can_act=True, is_admin=True,
                              queue_depth=3, context_pct=40)
    assert list(actions) == ["bringback", "stop", "rename", "kill", "restart"]
    assert all(a == {"available": True, "reason": None} for a in actions.values())


def test_the_background_menu_for_someone_who_may_not_prompt():
    actions = session_actions("background", resumable=True, can_act=False)
    assert list(actions) == ["bringback", "stop", "rename", "kill", "restart"]
    assert all(a == {"available": False, "reason": NO_PERMISSION_REASON}
               for a in actions.values())


def test_stop_needs_a_running_turn():
    actions = session_actions("background", resumable=True, can_act=True, turn_running=False)
    assert actions["stop"] == {"available": False, "reason": NOT_BUSY_REASON}
    assert actions["bringback"]["available"] is True


@pytest.mark.parametrize("status, stop", [(Status.IDLE, False), (Status.BUSY, True),
                                          (Status.INTERACTIVE, True)])
def test_the_detail_offers_stop_only_while_a_turn_runs(status, stop):
    d = session_detail(_parked(status), time.monotonic())
    assert list(d["actions"]) == ["bringback", "stop", "rename", "kill", "restart"]
    assert d["actions"]["stop"]["available"] is stop
    # Nothing that types into the terminal: no model picker, no answer.
    assert d["model_switch"] is None and d["answer"] is None


def test_only_background_offers_bring_it_back():
    for status in ("busy", "waiting", "idle", "gone", "unknown"):
        assert "bringback" not in session_actions(status, resumable=True, can_act=True,
                                                  context_pct=10, queue_depth=1)


def test_the_page_order_is_the_canonical_order():
    from aipager.miniapp.static import INDEX_HTML, SOURCE_INDEX_HTML
    for page in (INDEX_HTML, SOURCE_INDEX_HTML):
        order = re.search(r"var ACTION_ORDER = \[([^\]]*)\]", page)
        assert tuple(re.findall(r'"(\w+)"', order.group(1))) == ma._CANONICAL_ACTION_ORDER
    assert ma._CANONICAL_ACTION_ORDER[0] == "bringback"
    for keys in ma._STATUS_ACTION_KEYS.values():
        assert keys <= set(ma._CANONICAL_ACTION_ORDER)


def test_the_page_knows_the_bring_back_action():
    from aipager.miniapp.static import SOURCE_INDEX_HTML
    for table in ("ACTION_TITLES", "ACTION_PATHS", "ACTION_METHODS",
                  "ACTION_SUCCESS_NOTICE"):
        body = re.search(r"var " + table + r" = \{([^}]*)\}", SOURCE_INDEX_HTML).group(1)
        assert re.search(r"\bbringback:", body), table
    assert '<symbol id="i-bringback"' in SOURCE_INDEX_HTML


# ---- T5.3/T5.4: the routes -----------------------------------------------------

@pytest.fixture
def world(server, monkeypatch, tmp_path):  # noqa: F811
    """tests/test_bring_back_core.py's parked session, on the Mini App's bot,
    in the scope the app's people belong to."""
    w = World(lambda *a, **k: server.bot, monkeypatch, tmp_path)
    w.sess.scope_chat_id = SCOPE_CHAT_ID
    w.sess.cwd = "/tmp/proj"
    w.server = server
    yield w
    w.close()


def _post(world, user, route="bringback", body=None, label=LABEL):
    async def _run():
        client = await _client_for(world.server)
        try:
            kwargs = {"headers": _hdr(user)}
            if body is not None:
                kwargs["json"] = body
            resp = await client.post(f"/api/sessions/{label}/{route}", **kwargs)
            return resp.status, await resp.json()
        finally:
            await client.close()
    return _run()


def _sent(world) -> list[str]:
    return [c.kwargs.get("text") for c in world.bot._app.bot.send_message.await_args_list]


def _busy(world):
    world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="busy")


def test_bring_it_back_runs_the_core_and_says_so_once(world, run_async):
    status, body = run_async(_post(world, ADMIN_ID))
    assert (status, body) == (200, {"status": "brought_back", "label": LABEL})
    assert world.kinds() == ["run", "kill", "launch"]
    assert world.runs() == [["stop", JOB]]
    assert world.launches()[0]["resume_id"] == JS
    assert not world.sess.parked_job
    # T5.4: one line in the session's chat, the bring-back's own.
    assert _sent(world) == [i18n.for_chat(SCOPE_CHAT_ID).html(BG_BACK, session=LABEL)]
    assert world.bot._app.bot.send_message.await_args.kwargs["chat_id"] == SCOPE_CHAT_ID


def test_a_busy_job_asks_with_can_force(world, run_async):
    _busy(world)
    status, body = run_async(_post(world, ADMIN_ID))
    assert status == 409
    assert body == {"error": "bg_busy", "can_force": True,
                    "detail": "x is still working in Claude Code's background: stop that "
                              "work and bring the conversation back here?"}
    assert world.calls == [] and world.sess.parked_job == JOB
    assert _sent(world) == [], "never a chat card"


def test_force_stops_a_busy_job(world, run_async):
    _busy(world)
    status, body = run_async(_post(world, ADMIN_ID, body={"force": True}))
    assert (status, body["status"]) == (200, "brought_back")
    assert world.runs() == [["stop", JOB]] and world.kinds()[-2:] == ["kill", "launch"]


def test_force_reaches_the_core(world, run_async):
    core = AsyncMock(return_value=RestartOutcome(ok=True, reason="done", label=LABEL))
    world.bot._bring_back_core = core
    run_async(_post(world, ADMIN_ID))
    run_async(_post(world, ADMIN_ID, body={"force": True}))
    run_async(_post(world, ADMIN_ID, body={"force": False}))
    assert [c.kwargs for c in core.await_args_list] == [
        {"force": False}, {"force": True}, {"force": False}]
    assert all(c.args == (world.sess,) for c in core.await_args_list)


@pytest.mark.parametrize("route", ["bringback", "restart", "resume"])
@pytest.mark.parametrize("force", ["yes", 1, None, [True], {"x": 1}],
                         ids=["string", "one", "null", "list", "object"])
def test_a_force_that_is_not_a_boolean_is_400(world, run_async, route, force):
    for core in ("_bring_back_core", "_restart_session_core", "_do_resume_core"):
        setattr(world.bot, core, AsyncMock(side_effect=AssertionError(core)))
    status, body = run_async(_post(world, ADMIN_ID, route, body={"force": force}))
    assert (status, body) == (400, {"error": "bad_request"})


@pytest.mark.parametrize("body", [None, [], "force", {"other": True}],
                         ids=["none", "list", "string", "no-force"])
def test_no_force_in_the_body_is_not_forced(world, run_async, body):
    """The page sent no body before; anything without ``force`` stays
    unforced."""
    core = AsyncMock(return_value=RestartOutcome(ok=True, reason="done", label=LABEL))
    world.bot._bring_back_core = core
    status, _ = run_async(_post(world, ADMIN_ID, body=body))
    assert status == 200 and core.await_args.kwargs == {"force": False}


def test_someone_who_may_not_prompt_is_403(world, run_async):
    world.bot._bring_back_core = AsyncMock(side_effect=AssertionError("core"))
    assert run_async(_post(world, READONLY_ID)) == (403, {"error": "forbidden"})


def test_another_chats_session_is_403(world, run_async):
    world.bot._bring_back_core = AsyncMock(side_effect=AssertionError("core"))
    world.bot._bring_back_refusal = lambda sess, user, chat: "foreign"
    assert run_async(_post(world, ADMIN_ID)) == (403, {"error": "forbidden"})


def test_a_session_outside_the_callers_root_is_403(world, run_async):
    world.sess.cwd = "/home/alice/app"
    world.bot._bring_back_core = AsyncMock(side_effect=AssertionError("core"))
    status, body = run_async(_post(world, DEVELOPER_ID))
    assert status == 403 and body["error"] == "forbidden" and body["detail"]
    # The same session is the admin's to bring back.
    world.bot._bring_back_core = AsyncMock(
        return_value=RestartOutcome(ok=True, reason="done", label=LABEL))
    assert run_async(_post(world, ADMIN_ID))[0] == 200


def test_a_rate_limited_caller_is_429(world, run_async):
    world.bot._bring_back_core = AsyncMock(side_effect=AssertionError("core"))
    world.server._allow_write = lambda user_id: False
    assert run_async(_post(world, ADMIN_ID)) == (429, {"error": "too_many_requests"})


def test_a_session_not_parked_is_409_not_parked(world, run_async):
    world.sess.parked_job = ""
    status, body = run_async(_post(world, ADMIN_ID))
    assert (status, body) == (409, {"error": "not_parked",
                                    "detail": "x is not in Claude Code's background."})
    assert world.calls == [] and _sent(world) == []


def test_a_failed_stop_says_why(world, run_async):
    world.stop_rc = 1
    status, body = run_async(_post(world, ADMIN_ID))
    assert (status, body) == (409, {
        "error": "bg_stop_failed",
        "detail": "Could not bring x back from Claude Code's background: claude stop "
                  "ended with exit code 1"})
    assert world.kinds() == ["run"] and world.sess.parked_job == JOB
    assert _sent(world) == []


def test_no_saved_conversation_says_so(world, run_async):
    world.drop_transcript(JS)
    world.drop_transcript(X)
    status, body = run_async(_post(world, ADMIN_ID))
    assert (status, body) == (409, {
        "error": "bg_no_conversation",
        "detail": "Claude Code has no saved conversation to bring x back with."})
    assert world.calls == []


def test_a_bring_back_in_flight_is_409(world, run_async):
    world.sess.relaunch_in_flight = True
    status, body = run_async(_post(world, ADMIN_ID))
    assert status == 409 and body["error"] == "already_restarting"
    assert world.calls == []


def test_the_answer_is_in_the_viewers_language(world, run_async):
    preferences.set_ui_language(ADMIN_ID, "de")
    _busy(world)
    status, body = run_async(_post(world, ADMIN_ID))
    want = i18n.get("de").text(ma_bg_busy(), session=LABEL)
    assert status == 409 and body["detail"] == want
    assert want != i18n.get("en").text(ma_bg_busy(), session=LABEL)


def ma_bg_busy():
    from aipager.miniapp import server as srv
    return srv._BG_BUSY


# ---- Restart and Resume take the same body -------------------------------------

def test_restart_of_a_busy_parked_session_asks_then_forces(world, run_async):
    _busy(world)
    status, body = run_async(_post(world, ADMIN_ID, "restart"))
    assert status == 409 and body["error"] == "bg_busy" and body["can_force"] is True
    assert world.calls == []
    status, body = run_async(_post(world, ADMIN_ID, "restart", {"force": True}))
    assert (status, body) == (200, {"status": "restarted", "label": LABEL})
    assert world.runs() == [["stop", JOB]] and not world.sess.parked_job
    # The bring-back said it in the chat; no second "restarted" line.
    assert _sent(world) == [i18n.for_chat(SCOPE_CHAT_ID).html(BG_BACK, session=LABEL)]


def test_restart_passes_force_and_nothing_else(world, run_async):
    core = AsyncMock(return_value=RestartOutcome(ok=True, reason="done", label=LABEL))
    world.bot._restart_session_core = core
    run_async(_post(world, ADMIN_ID, "restart"))
    run_async(_post(world, ADMIN_ID, "restart", {"force": True}))
    assert [c.kwargs for c in core.await_args_list] == [{}, {"force": True}]


def _held_and_gone(world, status="idle"):
    """A finished session whose conversation (JS) a live background job holds."""
    sess = world.sess
    sess.parked_job = ""
    sess.parked_job_session_id = ""
    sess.claude_session_id = JS
    world.unreg(4100)
    world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status=status)
    world.bot.registry.transition(NAME, Status.GONE)
    return sess


def test_resume_of_a_held_conversation_asks_then_forces(world, run_async):
    _held_and_gone(world, "busy")
    status, body = run_async(_post(world, ADMIN_ID, "resume"))
    assert status == 409 and body["error"] == "bg_busy" and body["can_force"] is True
    assert world.calls == []
    status, body = run_async(_post(world, ADMIN_ID, "resume", {"force": True}))
    assert (status, body) == (200, {"status": "resumed", "label": LABEL})
    assert world.runs() == [["stop", JOB]] and world.kinds()[-1] == "launch"


def test_resume_passes_force(world, run_async):
    _held_and_gone(world)
    core = AsyncMock(return_value=ResumeOutcome(ok=True, reason="resumed"))
    world.bot._do_resume_core = core
    run_async(_post(world, ADMIN_ID, "resume"))
    run_async(_post(world, ADMIN_ID, "resume", {"force": True}))
    assert [c.kwargs.get("force", False) for c in core.await_args_list] == [False, True]


def test_bring_it_back_on_a_finished_session_resumes_the_held_conversation(world, run_async):
    _held_and_gone(world)
    status, body = run_async(_post(world, ADMIN_ID))
    assert (status, body) == (200, {"status": "brought_back", "label": LABEL})
    assert world.runs() == [["stop", JOB]]
    assert world.launches()[0]["resume_id"] == JS
    # T5.4: one bg.back, from the app's mirror this time.
    assert _sent(world) == [i18n.for_chat(SCOPE_CHAT_ID).html(BG_BACK, session=LABEL)]


def test_bring_it_back_on_a_finished_session_asks_when_busy(world, run_async):
    _held_and_gone(world, "busy")
    status, body = run_async(_post(world, ADMIN_ID))
    assert status == 409 and body["error"] == "bg_busy" and body["can_force"] is True
    assert world.calls == [] and _sent(world) == []


def test_bring_it_back_on_a_finished_session_forces_a_busy_job(world, run_async):
    _held_and_gone(world, "busy")
    status, body = run_async(_post(world, ADMIN_ID, body={"force": True}))
    assert (status, body["status"]) == (200, "brought_back")
    assert world.runs() == [["stop", JOB]] and world.kinds()[-1] == "launch"


def test_a_finished_session_nothing_holds_is_not_parked(world, run_async):
    _held_and_gone(world)
    world.unreg(WORKER)
    world.alive.discard(WORKER)
    world.bot._do_resume_core = AsyncMock(side_effect=AssertionError("resume"))
    status, body = run_async(_post(world, ADMIN_ID))
    assert (status, body["error"]) == (409, "not_parked")
    assert _sent(world) == []


def test_a_failed_mirror_does_not_fail_the_bring_back(world, run_async):
    _held_and_gone(world)
    world.bot._app.bot.send_message = AsyncMock(side_effect=RuntimeError("telegram"))
    assert run_async(_post(world, ADMIN_ID))[0] == 200


# ---- Stop of a parked session says why it failed ---------------------------------

def test_a_parked_stop_that_fails_says_why(world, run_async):
    world.bot.registry.transition(NAME, Status.BUSY)
    world.stop_rc = 1
    status, body = run_async(_post(world, ADMIN_ID, "stop"))
    assert (status, body["error"]) == (409, "bg_stop_failed")
    assert "claude stop ended with exit code 1" in body["detail"]

