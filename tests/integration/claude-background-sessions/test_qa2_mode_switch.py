"""QA iteration 2: a mode switch that needs a restart, on a parked session.

Contract (iteration-2 brief; design.md B1 "a /mode restart-switch ... bring
it back"): a restart-type mode switch on a parked session with a busy job
shows the busy question with its buttons; the Mini App perms route takes
``{"mode", "force"}``.

A switch out of Full access restarts the session (docs/commands.md
``/mode``), so a Full access session switched to Auto is the restart type.
The session itself is IDLE: the job's work was started from the terminal.

Partitions:
- the surface: the chat's ``/mode`` / the Mini App's perms route;
- the job: busy (asks) / idle and settled (brings it back in the new mode);
- ``force`` (Mini App): absent / false / true / not a boolean;
- the switch: restart type (Full access to Auto) / in place (Ask to Auto).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aipager.state import Status
from tests.test_miniapp_session_actions_api import (  # noqa: F401 - autouse fixture
    _configured_bot_token,
)


def _load(name, filename):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


qa = _load("_bgj_qa_kit", "_qa_kit.py")
_ma = _load("_bgj_qa_miniapp", "test_qa_miniapp.py")
app = _ma.app                          # the Mini App fixture of the iteration-1 file


def _full_and_parked(stack, *, job_status):
    stack.sess.perm_mode = "full"
    qa.park_and_tick(stack, job_status=job_status)
    stack.registry.transition(stack.ids.NAME, Status.IDLE)


def _mode(stack, mk_update, word="auto"):
    """``/mode <word>`` in the session's chat; returns the reply's message
    (the card the switch edits)."""
    u = mk_update(f"/mode {word}", message_id=50, user_id=12345, chat_id=stack.ids.CHAT)
    ctx = MagicMock()
    ctx.args = [word]
    stack.run(stack.bot._handle_mode_cmd(u, ctx))
    stack.run_spawned()
    return u


def _card(u):
    """Every text the ``/mode`` reply showed, with its buttons."""
    out = []
    for c in u.message.reply_text.await_args_list:
        out.append((c.args[0] if c.args else c.kwargs.get("text", ""),
                    c.kwargs.get("reply_markup")))
    for c in u.message.reply_text.return_value.edit_text.await_args_list:
        out.append((c.args[0] if c.args else c.kwargs.get("text", ""),
                    c.kwargs.get("reply_markup")))
    return out


def _datas(markup):
    if markup is None:
        return []
    return [b.callback_data.rsplit(":", 1)[1] for row in markup.inline_keyboard for b in row]


# ---- the chat's /mode ---------------------------------------------------------------

def test_chat_a_restart_switch_into_a_busy_job_shows_the_question(stack, mk_update):
    _full_and_parked(stack, job_status="busy")
    u = _mode(stack, mk_update)
    assert qa.BUSY_QUESTION in [t for t, _ in _card(u)]


def test_chat_the_question_has_stop_and_bring_back_and_leave_it(stack, mk_update):
    _full_and_parked(stack, job_status="busy")
    u = _mode(stack, mk_update)
    markups = [m for t, m in _card(u) if t == qa.BUSY_QUESTION]
    assert [_datas(m) for m in markups] == [["bgforce" + stack.ids.JOB, "bgleave"]]


def test_chat_a_restart_switch_into_a_busy_job_touches_nothing(stack, mk_update):
    _full_and_parked(stack, job_status="busy")
    _mode(stack, mk_update)
    assert (qa.stops(stack), stack.kinds(), stack.sess.parked_job) == (
        [], [], stack.ids.JOB)


def test_chat_the_questions_stop_and_bring_back_stops_the_job(stack, mk_update):
    _full_and_parked(stack, job_status="busy")
    _mode(stack, mk_update)
    stack.tap("bgforce")
    assert qa.stops(stack) == [["stop", stack.ids.JOB]]


def test_chat_a_restart_switch_on_an_idle_job_comes_back_in_the_new_mode(stack, mk_update):
    _full_and_parked(stack, job_status="idle")
    _mode(stack, mk_update)
    assert [(lc.get("mode"), lc.get("resume_id")) for lc in stack.launches()] == [
        ("auto", stack.ids.JS)]


def test_chat_an_in_place_switch_on_a_parked_session_writes_nothing(stack, mk_update):
    """Ask to Auto happens in place (Shift+Tab): a parked client gets no key."""
    stack.sess.perm_mode = "ask"
    qa.park_and_tick(stack)
    u = _mode(stack, mk_update)
    texts = [t for t, _ in _card(u)] + qa.sent_texts(stack)
    assert (stack.kinds(), any("back from Claude Code's background first" in t
                               for t in texts)) == ([], True)


# ---- the Mini App's perms route ------------------------------------------------------

def _perms(stack, body):
    return _ma._post(stack, route="perms", body=body)


def test_app_a_restart_switch_into_a_busy_job_is_409_bg_busy(app):
    _full_and_parked(app, job_status="busy")
    status, body = _perms(app, {"mode": "auto"})
    assert (status, body.get("error"), body.get("can_force")) == (409, "bg_busy", True)


def test_app_the_bg_busy_answer_carries_the_question(app):
    _full_and_parked(app, job_status="busy")
    _status, body = _perms(app, {"mode": "auto"})
    assert body.get("detail") == (
        "frontend is still working in Claude Code's background: stop that work and "
        "bring the conversation back here?")


def test_app_a_restart_switch_into_a_busy_job_touches_nothing(app):
    _full_and_parked(app, job_status="busy")
    _perms(app, {"mode": "auto"})
    assert (qa.stops(app), app.kinds()) == ([], [])


def test_app_force_false_still_asks(app):
    _full_and_parked(app, job_status="busy")
    status, body = _perms(app, {"mode": "auto", "force": False})
    assert (status, body.get("error"), qa.stops(app)) == (409, "bg_busy", [])


def test_app_force_true_switches(app):
    _full_and_parked(app, job_status="busy")
    status, body = _perms(app, {"mode": "auto", "force": True})
    assert (status, body.get("status"), body.get("mode")) == (200, "switched", "auto")


def test_app_force_true_stops_the_job_before_the_relaunch(app):
    _full_and_parked(app, job_status="busy")
    _perms(app, {"mode": "auto", "force": True})
    assert (qa.stops(app), app.kinds()) == ([["stop", app.ids.JOB]], ["kill", "launch"])


def test_app_force_true_comes_back_in_the_new_mode(app):
    _full_and_parked(app, job_status="busy")
    _perms(app, {"mode": "auto", "force": True})
    assert [lc.get("mode") for lc in app.launches()] == ["auto"]


def test_app_an_idle_settled_job_switches_without_force(app):
    _full_and_parked(app, job_status="idle")
    status, _body = _perms(app, {"mode": "auto"})
    assert (status, qa.stops(app)) == (200, [["stop", app.ids.JOB]])


@pytest.mark.parametrize("force", [pytest.param("true", id="string"),
                                   pytest.param(1, id="one"),
                                   pytest.param([True], id="list")])
def test_app_a_force_that_is_not_a_boolean_forces_nothing(app, force):
    _full_and_parked(app, job_status="busy")
    status, _body = _perms(app, {"mode": "auto", "force": force})
    assert (status != 200, qa.stops(app), app.kinds()) == (True, [], [])
