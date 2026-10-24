"""QA: the Mini App and a parked session (U2), over HTTP on the wired daemon.

entrypoints.md routes: ``GET /api/sessions`` and ``/api/sessions/{label}``
(``parked``, ``status: "background"``, ``parked_seconds_ago``, the
``background`` actions in order), ``POST /api/sessions/{label}/bringback``
(200, 400 ``bad_request``, 403, 429, 409 ``not_parked`` / ``bg_busy`` with
``can_force`` / ``bg_stop_failed`` / ``bg_no_conversation`` /
``already_restarting``), and ``restart``/``resume`` with ``force``.

The server runs on the stack's bot, so the real bring-back core, Claude's
fake files and the chat all take part. Held messages from the chat go out
after a Mini App bring-back.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from aipager import claude_jobs
from aipager.miniapp.server import MiniAppServer
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import Status
from tests.test_miniapp_session_actions_api import (  # noqa: F401 - autouse fixture
    _client_for,
    _configured_bot_token,
    _hdr,
)


def _kit():
    import importlib.util
    import sys
    if "_bgj_qa_kit" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "_bgj_qa_kit", Path(__file__).with_name("_qa_kit.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_bgj_qa_kit"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["_bgj_qa_kit"]


qa = _kit()

ADMIN, DEV, READONLY, OUTSIDER = 555, 777, 888, 999
LABEL = "frontend"


@pytest.fixture
def app(stack, tmp_path):
    """The Mini App on the stack's bot. The session's chat is a team: an
    admin, a user whose members' root is elsewhere, a read-only member."""
    root = tmp_path / "team-root"
    root.mkdir()
    stack.bot.scopes = [Scope(
        chat_id=stack.ids.CHAT, kind="group", label="team",
        members=(Member(id=ADMIN, label="ada", role="admin"),
                 Member(id=DEV, label="bob", role="user"),
                 Member(id=READONLY, label="cleo", role="read_only")),
        root=str(root))]
    stack.bot.policy = load_policy(Path("/nonexistent/policy.yaml"),
                                   Path("/nonexistent/policy.d"))
    stack.bot._app.bot.username = "aipager_test_bot"
    stack.server = MiniAppServer(stack.bot, stack.registry, port=8768)
    return stack


def _call(stack, user, method, path, body=None):
    async def run():
        client = await _client_for(stack.server)
        try:
            kw = {"headers": _hdr(user)}
            if body is not None:
                kw["json"] = body
            resp = await getattr(client, method)(path, **kw)
            try:
                data = await resp.json()
            except Exception:  # noqa: BLE001 - a non-JSON body is part of the answer
                data = None
            return resp.status, data
        finally:
            await client.close()
    return stack.run(run())


def _post(stack, user=ADMIN, route="bringback", body=None, label=LABEL):
    return _call(stack, user, "post", f"/api/sessions/{label}/{route}", body)


def _row(stack, user=ADMIN):
    status, data = _call(stack, user, "get", "/api/sessions")
    assert status == 200, data
    rows = data["sessions"] if isinstance(data, dict) and "sessions" in data else data
    return next(r for r in rows if r.get("label") == LABEL)


def _detail(stack, user=ADMIN):
    status, data = _call(stack, user, "get", f"/api/sessions/{LABEL}")
    assert status == 200, data
    return data


# ---- GET: rows and detail -------------------------------------------------------------------

def test_a_parked_session_is_background_in_the_list(app):
    qa.park_and_tick(app)
    assert _row(app)["status"] == "background"


def test_a_parked_session_is_parked_in_the_list(app):
    qa.park_and_tick(app)
    assert _row(app)["parked"] is True


def test_a_session_that_is_not_parked_is_not_parked_in_the_list(app):
    row = _row(app)
    assert (row["parked"], row["status"] != "background") == (False, True)


def test_the_detail_says_parked(app):
    qa.park_and_tick(app)
    assert _detail(app)["parked"] is True


def test_the_detail_says_how_long_ago(app):
    qa.park_and_tick(app)
    app.sess.parked_since = time.time() - 30
    assert 29 <= _detail(app)["parked_seconds_ago"] <= 32


def test_the_detail_of_a_session_not_parked_has_no_park_age(app):
    assert _detail(app)["parked_seconds_ago"] is None


def test_the_background_actions_in_order(app):
    qa.park_and_tick(app)
    assert list(_detail(app)["actions"]) == ["bringback", "stop", "rename", "kill",
                                              "restart"]


def test_a_gone_parked_session_is_not_background(app):
    qa.park_and_tick(app)
    qa.vanish(app)
    assert _row(app)["status"] != "background"


# ---- POST bringback ------------------------------------------------------------------------

def test_bring_it_back_is_200(app):
    qa.park_and_tick(app)
    assert _post(app) == (200, {"status": "brought_back", "label": LABEL})


def test_bring_it_back_stops_the_job_and_resumes_its_conversation(app):
    qa.park_and_tick(app)
    _post(app)
    assert (qa.stops(app), [lc.get("resume_id") for lc in app.launches()]) == (
        [["stop", app.ids.JOB]], [app.ids.JS])


def test_bring_it_back_says_so_in_the_chat_once(app):
    qa.park_and_tick(app)
    _post(app)
    assert qa.count(qa.sent_texts(app), qa.BACK) == 1


def test_a_busy_job_is_409_bg_busy_with_can_force(app):
    qa.park_and_tick(app, job_status="busy")
    status, body = _post(app)
    assert (status, body.get("error"), body.get("can_force")) == (409, "bg_busy", True)


def test_a_busy_job_answer_carries_the_question(app):
    qa.park_and_tick(app, job_status="busy")
    _, body = _post(app)
    assert body.get("detail") == ("frontend is still working in Claude Code's background: "
                                  "stop that work and bring the conversation back here?")


def test_a_busy_job_is_left_alone(app):
    qa.park_and_tick(app, job_status="busy")
    _post(app)
    assert (qa.stops(app), app.kinds(), app.sess.parked_job) == ([], [], app.ids.JOB)


def test_a_busy_job_sends_no_chat_card(app):
    qa.park_and_tick(app, job_status="busy")
    before = len(app.sent())
    _post(app)
    assert len(app.sent()) == before


def test_force_brings_back_a_busy_job(app):
    qa.park_and_tick(app, job_status="busy")
    status, _ = _post(app, body={"force": True})
    assert (status, qa.stops(app)) == (200, [["stop", app.ids.JOB]])


def test_force_false_is_not_a_force(app):
    qa.park_and_tick(app, job_status="busy")
    status, body = _post(app, body={"force": False})
    assert (status, body.get("error")) == (409, "bg_busy")


@pytest.mark.parametrize("force", ["true", 1, 0, None, [True]],
                         ids=["string", "one", "zero", "null", "list"])
def test_a_force_that_is_not_a_boolean_is_400(app, force):
    qa.park_and_tick(app, job_status="busy")
    status, body = _post(app, body={"force": force})
    assert (status, body, qa.stops(app)) == (400, {"error": "bad_request"}, [])


def test_a_read_only_member_is_403(app):
    qa.park_and_tick(app)
    status, body = _post(app, user=READONLY)
    assert (status, body.get("error"), qa.stops(app)) == (403, "forbidden", [])


def test_a_user_outside_the_members_root_is_403(app):
    qa.park_and_tick(app)
    status, body = _post(app, user=DEV)
    assert (status, body.get("error"), qa.stops(app)) == (403, "forbidden", [])


def test_someone_from_no_scope_brings_nothing_back(app):
    qa.park_and_tick(app)
    status, _ = _post(app, user=OUTSIDER)
    assert (status in (401, 403, 404), qa.stops(app)) == (True, [])


def test_a_session_not_parked_is_409_not_parked(app):
    status, body = _post(app)
    assert (status, body.get("error"), app.kinds()) == (409, "not_parked", [])


def test_an_unknown_label_is_404(app):
    status, _ = _post(app, label="nosuch")
    assert status == 404


def test_a_failed_stop_is_409_bg_stop_failed(app, monkeypatch):
    qa.park_and_tick(app)
    inner = app.claude.exec

    async def exec_(*argv, **kw):
        if list(argv[1:2]) == ["stop"]:
            class Proc:
                returncode = None

                async def communicate(self, _stdin=None):
                    self.returncode = 1
                    return b"", b""

                def kill(self):
                    pass
            return Proc()
        return await inner(*argv, **kw)
    monkeypatch.setattr(claude_jobs, "_create_subprocess_exec", exec_)
    status, body = _post(app)
    assert (status, body.get("error"), bool(body.get("detail")), app.kinds()) == (
        409, "bg_stop_failed", True, [])


def test_no_saved_conversation_is_409_bg_no_conversation(app):
    qa.park_and_tick(app)
    (app.claude.projects / f"{app.ids.JS}.jsonl").unlink()
    (app.claude.projects / f"{app.ids.X}.jsonl").unlink()
    status, body = _post(app)
    assert (status, body.get("error"), app.kinds()) == (409, "bg_no_conversation", [])


def test_a_bring_back_in_flight_is_409_already_restarting(app):
    qa.park_and_tick(app)
    app.sess.relaunch_in_flight = True
    status, body = _post(app)
    assert (status, body.get("error"), app.kinds()) == (409, "already_restarting", [])


def test_hammering_the_route_is_rate_limited(app):
    """429 ``too_many_requests``: a stream of writes is cut off."""
    statuses = []
    for _ in range(120):
        status, _ = _post(app)
        statuses.append(status)
        if status == 429:
            break
    assert 429 in statuses


def test_a_gone_session_with_a_held_conversation_is_not_not_parked(app):
    """design.md: a GONE session whose id has a live holder runs the R2
    resume instead of answering ``not_parked``."""
    ids = app.ids
    app.claude.unreg(ids.CLIENT)
    app.live.discard(ids.NAME)
    app.sess.claude_session_id = ids.JS
    app.claude.transcript(ids.JS, {"type": "user"})
    app.claude.reg(ids.WORKER, kind="bg", jobId=ids.JOB, sessionId=ids.JS, status="idle")
    app.claude.stop_ends[ids.JOB] = ids.WORKER
    app.registry.transition(ids.NAME, Status.GONE)
    status, body = _post(app)
    assert (status, qa.stops(app)) == (200, [["stop", ids.JOB]]), body


# ---- restart and resume take the same body --------------------------------------------------

def test_restart_of_a_busy_parked_session_is_409_bg_busy(app):
    qa.park_and_tick(app, job_status="busy")
    status, body = _post(app, route="restart")
    assert (status, body.get("error"), body.get("can_force")) == (409, "bg_busy", True)


def test_forced_restart_of_a_busy_parked_session_is_200(app):
    qa.park_and_tick(app, job_status="busy")
    status, body = _post(app, route="restart", body={"force": True})
    assert (status, body) == (200, {"status": "restarted", "label": LABEL})


def test_resume_of_a_busily_held_gone_session_is_409_then_force_resumes(app):
    ids = app.ids
    app.claude.unreg(ids.CLIENT)
    app.live.discard(ids.NAME)
    app.sess.claude_session_id = ids.JS
    app.claude.transcript(ids.JS, {"type": "user"})
    app.claude.reg(ids.WORKER, kind="bg", jobId=ids.JOB, sessionId=ids.JS, status="busy")
    app.claude.stop_ends[ids.JOB] = ids.WORKER
    app.registry.transition(ids.NAME, Status.GONE)
    first = _post(app, route="resume")[0]
    second = _post(app, route="resume", body={"force": True})[0]
    assert (first, second, qa.stops(app)) == (409, 200, [["stop", ids.JOB]])


# ---- held chat messages go out after a Mini App bring-back ----------------------------------

def test_chat_messages_held_on_a_busy_job_go_out_after_a_forced_bring_back(app, mk_update):
    qa.park_and_tick(app, job_status="busy")
    app.message(mk_update, "first", msg_id=41, user_id=ADMIN)
    app.message(mk_update, "second", msg_id=42, user_id=ADMIN)
    app.run_spawned()
    assert app.typed == [], "precondition: held"
    assert _post(app, body={"force": True})[0] == 200
    qa.relaunched_start(app)
    qa.own_turns(app, 2)
    assert [t.rsplit("\n", 1)[-1] for t in app.typed] == ["first", "second"]
