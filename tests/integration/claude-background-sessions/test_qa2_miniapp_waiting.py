"""QA iteration 2: a parked session whose job waits on a permission prompt
the chat can answer.

Contract (iteration-2 brief): a parked session whose job waits on a
permission prompt answerable through the hook shows "waiting" (needs you)
in the Mini App, keeps ``parked: true`` and offers Bring it back.
docs/commands.md: "A session in Claude Code's background whose job waits
on a permission prompt sits there too, since you can answer it from the
chat; its page also offers Bring it back."

The prompt comes from the job's worker as a linked ``PermissionRequest``
datagram (``bg_job``). Answerable through the hook means it carries a
``hook_reply`` (the datagram's ``aipager_reply_addr`` and
``aipager_request_id``); without one only keys could answer it, and aipager never
types into a parked client.

Partitions:
- parked: yes / no (control);
- the prompt: a PermissionRequest with ``hook_reply`` / without one / the
  hook gave up waiting (``permission_reply_timeout`` clears it) / none;
- the view: the list row / the detail.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

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

REQUEST_ID = "feedface" * 4


def _permission(stack, tmp_path, *, hook_reply=True, linked=True):
    """The prompt arrives while the turn's card (message 900) is up, as in a
    turn the chat started."""
    stack.sess.busy_msg_id = stack.sess.busy_msg_id or 900
    fields = dict(hook_event_name="PermissionRequest", tool_name="Bash",
                  tool_input={"command": "make deploy", "description": "Deploy"})
    if linked:
        fields.update(bg_job=stack.ids.JOB, session_id=stack.ids.JS,
                      transcript_path=str(stack.claude.projects / f"{stack.ids.JS}.jsonl"))
    if hook_reply:
        # Only a path name: nothing listens on it and nothing is sent in here.
        fields.update(aipager_reply_addr=str(tmp_path / "reply.sock"),
                      aipager_request_id=REQUEST_ID)
    stack.hook(**fields)


def _waiting_parked(stack, tmp_path, **kw):
    qa.park_and_tick(stack, job_status="busy")
    _permission(stack, tmp_path, **kw)
    assert stack.sess.parked_job == stack.ids.JOB, "precondition: still parked"


# ---- answerable through the hook -----------------------------------------------------

def test_the_row_is_waiting(app, tmp_path):
    _waiting_parked(app, tmp_path)
    assert _ma._row(app)["status"] == "waiting"


def test_the_row_is_still_parked(app, tmp_path):
    _waiting_parked(app, tmp_path)
    assert _ma._row(app)["parked"] is True


def test_the_detail_is_waiting(app, tmp_path):
    _waiting_parked(app, tmp_path)
    assert _ma._detail(app)["status"] == "waiting"


def test_the_detail_is_still_parked(app, tmp_path):
    _waiting_parked(app, tmp_path)
    assert _ma._detail(app)["parked"] is True


def test_the_detail_offers_bring_it_back(app, tmp_path):
    _waiting_parked(app, tmp_path)
    assert "bringback" in _ma._detail(app)["actions"]


def test_bring_it_back_from_the_waiting_page_asks_while_the_job_waits(app, tmp_path):
    """The job is mid-turn on a prompt: an unforced bring-back asks."""
    _waiting_parked(app, tmp_path)
    status, body = _ma._post(app)
    assert (status, body.get("error"), qa.stops(app)) == (409, "bg_busy", [])


# ---- not answerable through the hook -------------------------------------------------

def test_a_prompt_only_keys_could_answer_stays_background(app, tmp_path):
    _waiting_parked(app, tmp_path, hook_reply=False)
    assert _ma._row(app)["status"] == "background"


def test_a_prompt_whose_hook_gave_up_goes_back_to_background(app, tmp_path):
    _waiting_parked(app, tmp_path)
    assert _ma._row(app)["status"] == "waiting", "precondition"
    app.hook(hook_event_name="permission_reply_timeout", aipager_request_id=REQUEST_ID,
             bg_job=app.ids.JOB, session_id=app.ids.JS)
    assert _ma._row(app)["status"] == "background"


def test_without_a_prompt_a_parked_session_is_background(app):
    qa.park_and_tick(app, job_status="busy")
    assert _ma._row(app)["status"] == "background"


# ---- control: not parked -------------------------------------------------------------

@pytest.mark.parametrize("key", ["status", "parked"])
def test_control_an_ordinary_session_waiting_on_a_prompt(app, tmp_path, key):
    _permission(app, tmp_path, linked=False)
    row = _ma._row(app)
    assert row[key] == {"status": "waiting", "parked": False}[key]


def test_control_an_ordinary_waiting_session_offers_no_bring_back(app, tmp_path):
    _permission(app, tmp_path, linked=False)
    assert "bringback" not in _ma._detail(app)["actions"]
