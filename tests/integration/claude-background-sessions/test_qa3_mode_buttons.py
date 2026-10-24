"""QA iteration 3: "Stop and bring back" after a mode switch carries the mode.

Contract (iteration-3 brief):
- when a restart-type mode switch (into or out of Full access) meets a busy
  job, the question's "Stop and bring back" carries the mode: callback data
  ends in ``bgmodeask<job8>``, ``bgmodeauto<job8>`` or ``bgmodefull<job8>``;
- tapping it stops the job and brings the session back in that mode;
- ``bgmodefull`` needs someone allowed to manage (admin) at tap time; anyone
  else gets the "needs admin" alert and nothing happens;
- Restart's own busy question keeps the plain ``bgforce<job8>``;
- a mode button on a session that ended meanwhile resumes it like a keyed
  ``bgforce``;
- every callback stays within Telegram's 64 bytes.

Buttons are taken from the card the ``/mode`` reply showed and tapped the
way Telegram delivers them (``bot._handle_callback`` with the button's own
callback data), so the tap gate runs too.

Partitions:
- the switch: Full access to Ask / Full access to Auto / Auto to Full
  access / Ask to Full access (all restart type);
- the tapper: the admin / a user inside the members' root (may Restart,
  may not manage) / the owner;
- the key: the current park's job / another job;
- the session at tap time: parked / ended (its job still holds the
  conversation, busy).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import session_parity
from aipager.scope import Member, Scope
from aipager.state import Status


def _kit():
    import importlib.util
    import sys
    from pathlib import Path
    if "_bgj_qa_kit" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "_bgj_qa_kit", Path(__file__).with_name("_qa_kit.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_bgj_qa_kit"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["_bgj_qa_kit"]


qa = _kit()

ADMIN, DEV = 555, 777
OWNER = 12345
OTHER_JOB = "ef90ab12"


@pytest.fixture
def team(stack):
    """An admin and a user whose members' root holds the session's folder:
    both may Restart it; only the admin may manage."""
    stack.bot.scopes = [Scope(
        chat_id=stack.ids.CHAT, kind="group", label="team",
        members=(Member(id=ADMIN, label="ada", role="admin"),
                 Member(id=DEV, label="bob", role="user")),
        root=str(stack.cc.work.parent))]
    return stack


SWITCHES = [
    pytest.param("full", "ask", "bgmodeask", id="full_to_ask"),
    pytest.param("full", "auto", "bgmodeauto", id="full_to_auto"),
    pytest.param("auto", "full", "bgmodefull", id="auto_to_full"),
    pytest.param("ask", "full", "bgmodefull", id="ask_to_full"),
]


def _parked_in(stack, mode, *, job_status="busy"):
    stack.sess.perm_mode = mode
    qa.park_and_tick(stack, job_status=job_status)
    stack.registry.transition(stack.ids.NAME, Status.IDLE)


def _mode(stack, mk_update, word, *, user=ADMIN):
    """``/mode <word>`` in the session's chat. In a group the command first
    asks which session; the same person picks ``frontend``. Returns the
    command's update and the picker taps."""
    u = mk_update(f"/mode {word}", message_id=50, user_id=user, chat_id=stack.ids.CHAT)
    ctx = MagicMock()
    ctx.args = [word]
    stack.run(stack.bot._handle_mode_cmd(u, ctx))
    stack.run_spawned()
    taps = []
    for text, markup in _cards(u):
        if markup is not None and text.startswith("Which session"):
            pick = [b.callback_data for row in markup.inline_keyboard for b in row
                    if b.text == stack.ids.LABEL]
            assert pick, "precondition: frontend is offered"
            taps.append(_press(stack, pick[0], user=user))
    _confirm(stack, taps, user, first=_cards(u),
             first_msg=u.message.reply_text.return_value.message_id)
    return stack, u, taps


CONFIRM_VERBS = ("perms_confirm", "restartok")


def _confirm(stack, taps, user, *, first=(), first_msg=77):
    """Press the "Yes, switch" / "Restart" confirmation the last card asks
    for, as the same person, until no card asks any more."""
    for _ in range(3):
        shown = _edits(taps[-1]) if taps else list(first)
        confirm = [b.callback_data for _t, m in shown if m is not None
                   for row in m.inline_keyboard for b in row
                   if b.callback_data.rsplit(":", 1)[-1].startswith(CONFIRM_VERBS)]
        if not confirm:
            return
        msg = taps[-1].message.message_id if taps else first_msg
        taps.append(_press(stack, confirm[-1], user=user, msg_id=msg))


def _cards(u):
    out = []
    for c in u.message.reply_text.await_args_list:
        out.append((c.args[0] if c.args else c.kwargs.get("text", ""),
                    c.kwargs.get("reply_markup")))
    for c in u.message.reply_text.return_value.edit_text.await_args_list:
        out.append((c.args[0] if c.args else c.kwargs.get("text", ""),
                    c.kwargs.get("reply_markup")))
    return out


def _edits(q):
    out = []
    for c in q.edit_message_text.await_args_list + q.message.edit_text.await_args_list:
        out.append((c.args[0] if c.args else c.kwargs.get("text", ""),
                    c.kwargs.get("reply_markup")))
    return out


def _question_buttons(src) -> list[str]:
    """The full callback data of the busy question's buttons, wherever the
    chat showed it (the reply, the picked card, a new message)."""
    stack, u, taps = src
    shown = _cards(u)
    for q in taps:
        shown += _edits(q)
    for c in qa.calls_with_text(stack, "is still working in Claude Code's background"):
        shown.append((qa.BUSY_QUESTION, c.kwargs.get("reply_markup")))
    for text, markup in shown:
        if text == qa.BUSY_QUESTION and markup is not None:
            return [b.callback_data for row in markup.inline_keyboard for b in row]
    return []


def _stop_and_bring_back(src) -> str:
    datas = _question_buttons(src)
    assert datas, "precondition: the busy question was shown"
    return datas[0]


def _press(stack, data, *, user=ADMIN, msg_id=77):
    """A tap on a button whose callback data is *data*, on message *msg_id*."""
    q = MagicMock()
    q.data = data
    q.from_user = MagicMock(id=user)
    q.message = MagicMock(text="card", message_id=msg_id)
    q.message.chat = MagicMock(id=stack.ids.CHAT)
    q.message.chat_id = stack.ids.CHAT
    q.message.edit_text = AsyncMock()
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()
    u = MagicMock()
    u.callback_query = q
    u.effective_chat = q.message.chat
    u.effective_user = q.from_user
    stack.run(stack.bot._handle_callback(u, MagicMock()))
    stack.run_spawned()
    return q


def _alerts(q) -> list:
    return [c for c in q.answer.await_args_list if c.kwargs.get("show_alert") is True]


def _untouched(stack):
    return (qa.stops(stack), stack.kinds(), stack.sess.parked_job) == (
        [], [], stack.ids.JOB)


# ---- the question carries the mode ------------------------------------------------------

@pytest.mark.parametrize("old, new, verb", SWITCHES)
def test_the_question_carries_the_mode(team, mk_update, old, new, verb):
    _parked_in(team, old)
    u = _mode(team, mk_update, new)
    assert [d.rsplit(":", 1)[-1] for d in _question_buttons(u)] == [
        verb + team.ids.JOB, "bgleave"]


@pytest.mark.parametrize("old, new, verb", SWITCHES)
def test_every_button_of_the_question_fits_64_bytes(team, mk_update, old, new, verb):
    _parked_in(team, old)
    u = _mode(team, mk_update, new)
    datas = _question_buttons(u)
    assert datas and all(len(d.encode()) <= 64 for d in datas), datas


def test_a_long_session_label_still_fits_64_bytes(team, mk_update):
    team.sess.label = "frontend-" + "x" * 50
    team.ids.LABEL = team.sess.label
    _parked_in(team, "auto")
    _st, u, taps = _mode(team, mk_update, "full")
    shown = _cards(u) + [e for q in taps for e in _edits(q)]
    datas = [b.callback_data for _t, m in shown if m is not None
             for row in m.inline_keyboard for b in row]
    assert datas and all(len(d.encode()) <= 64 for d in datas), datas


@pytest.mark.parametrize("verb", ["bgmodeask", "bgmodeauto", "bgmodefull", "bgforce",
                                  "bgback"])
def test_session_cb_for_a_keyed_verb_fits_64_bytes(stack, verb):
    data = session_parity.session_cb(stack.bot, stack.ids.CHAT, stack.sess,
                                     verb + "f" * 8)
    assert len(data.encode()) <= 64, data


# ---- tapping it -------------------------------------------------------------------------

@pytest.mark.parametrize("old, new, verb", SWITCHES)
def test_the_tap_stops_the_job(team, mk_update, old, new, verb):
    _parked_in(team, old)
    data = _stop_and_bring_back(_mode(team, mk_update, new))
    _press(team, data)
    assert qa.stops(team) == [["stop", team.ids.JOB]]


@pytest.mark.parametrize("old, new, verb", SWITCHES)
def test_the_tap_brings_it_back_in_the_new_mode(team, mk_update, old, new, verb):
    _parked_in(team, old)
    data = _stop_and_bring_back(_mode(team, mk_update, new))
    _press(team, data)
    assert [(lc.get("mode"), lc.get("resume_id")) for lc in team.launches()] == [
        (new, team.ids.JS)]


@pytest.mark.parametrize("old, new, verb", SWITCHES)
def test_the_tap_ends_the_park(team, mk_update, old, new, verb):
    _parked_in(team, old)
    data = _stop_and_bring_back(_mode(team, mk_update, new))
    _press(team, data)
    assert team.sess.parked_job == ""


def test_the_owner_brings_it_back_in_full_access(stack, mk_update):
    _parked_in(stack, "auto")
    data = _stop_and_bring_back(_mode(stack, mk_update, "full", user=OWNER))
    _press(stack, data, user=OWNER)
    assert [lc.get("mode") for lc in stack.launches()] == ["full"]


# ---- Full access needs an admin at tap time ---------------------------------------------

def _full_question(team, mk_update):
    _parked_in(team, "auto")
    return _stop_and_bring_back(_mode(team, mk_update, "full", user=ADMIN))


def test_a_user_tapping_into_full_access_is_alerted(team, mk_update):
    data = _full_question(team, mk_update)
    q = _press(team, data, user=DEV)
    assert len(_alerts(q)) == 1


def test_a_user_tapping_into_full_access_changes_nothing(team, mk_update):
    data = _full_question(team, mk_update)
    _press(team, data, user=DEV)
    assert _untouched(team)


def test_a_user_tapping_into_full_access_keeps_the_mode(team, mk_update):
    data = _full_question(team, mk_update)
    _press(team, data, user=DEV)
    assert team.sess.perm_mode == "auto"


def test_after_a_users_refused_tap_the_admin_tap_still_works(team, mk_update):
    data = _full_question(team, mk_update)
    _press(team, data, user=DEV)
    _press(team, data, user=ADMIN)
    assert [lc.get("mode") for lc in team.launches()] == ["full"]


@pytest.mark.parametrize("new, verb", [pytest.param("ask", "bgmodeask", id="ask"),
                                       pytest.param("auto", "bgmodeauto", id="auto")])
def test_a_user_may_tap_out_of_full_access(team, mk_update, new, verb):
    """Only the way into Full access needs an admin; the question was
    shown to the admin, the user taps it."""
    _parked_in(team, "full")
    data = _stop_and_bring_back(_mode(team, mk_update, new, user=ADMIN))
    _press(team, data, user=DEV)
    assert [lc.get("mode") for lc in team.launches()] == [new]


def test_a_user_tapping_a_hand_built_full_button_changes_nothing(team):
    """Error guessing: the admin check is at tap time, not only when the
    card is built."""
    _parked_in(team, "auto")
    data = session_parity.session_cb(team.bot, team.ids.CHAT, team.sess,
                                     "bgmodefull" + team.ids.JOB)
    _press(team, data, user=DEV)
    assert _untouched(team)


# ---- the key ----------------------------------------------------------------------------

@pytest.mark.parametrize("verb", ["bgmodeask", "bgmodeauto", "bgmodefull"])
def test_a_mode_button_for_another_job_does_nothing(team, verb):
    _parked_in(team, "auto")
    data = session_parity.session_cb(team.bot, team.ids.CHAT, team.sess, verb + OTHER_JOB)
    _press(team, data)
    assert _untouched(team)


@pytest.mark.parametrize("data_verb", [
    pytest.param("bgmodebypass", id="unknown_mode"),
    pytest.param("bgmodefullab12cd3", id="seven_char_key"),
    pytest.param("bgmodeautoab12cd34x", id="nine_char_key"),
])
def test_a_malformed_mode_button_does_nothing(team, data_verb):
    _parked_in(team, "auto")
    data = session_parity.session_cb(team.bot, team.ids.CHAT, team.sess, data_verb)
    _press(team, data)
    assert _untouched(team)


# ---- Restart's own question -------------------------------------------------------------

def _shown_buttons(stack, *queries) -> list[list[str]]:
    """The buttons of every busy question the chat showed (sent, or edited
    into the tapped card)."""
    out = [qa.buttons(c) for c in
           qa.calls_with_text(stack, "is still working in Claude Code's background")]
    for q in queries:
        for c in q.edit_message_text.await_args_list:
            text = c.args[0] if c.args else c.kwargs.get("text", "")
            if text == qa.BUSY_QUESTION:
                out.append(qa.buttons(c))
    return out


def test_restarts_own_busy_question_keeps_plain_bgforce(stack):
    """The Restart button, then its confirmation."""
    _parked_in(stack, "auto")
    data = session_parity.session_cb(stack.bot, stack.ids.CHAT, stack.sess, "restart")
    taps = [_press(stack, data, user=OWNER)]
    _confirm(stack, taps, OWNER)
    assert _shown_buttons(stack, *taps) == [["bgforce" + stack.ids.JOB, "bgleave"]]


# ---- the session ended meanwhile --------------------------------------------------------

def _ended_with_busy_holder(stack, *, holder_job):
    """The question was shown; then the parked client died and the session
    ended. The job (*holder_job*) still holds the conversation, busy."""
    ids = stack.ids
    stack.claude.reg(ids.WORKER, kind="bg", jobId=holder_job, sessionId=ids.JS,
                     status="busy")
    stack.claude.stop_ends[holder_job] = ids.WORKER
    stack.claude.unreg(ids.CLIENT)
    qa.vanish(stack)
    assert stack.sess.status == Status.GONE, "precondition: ended"


def test_a_mode_button_on_an_ended_session_resumes_it(team, mk_update):
    _parked_in(team, "full")
    data = _stop_and_bring_back(_mode(team, mk_update, "auto"))
    _ended_with_busy_holder(team, holder_job=team.ids.JOB)
    _press(team, data)
    assert [lc.get("resume_id") for lc in team.launches()] == [team.ids.JS]


def test_a_mode_button_on_an_ended_session_stops_its_own_job(team, mk_update):
    _parked_in(team, "full")
    data = _stop_and_bring_back(_mode(team, mk_update, "auto"))
    _ended_with_busy_holder(team, holder_job=team.ids.JOB)
    _press(team, data)
    assert qa.stops(team) == [["stop", team.ids.JOB]]


def test_a_mode_button_on_an_ended_session_never_force_stops_another_job(team, mk_update):
    _parked_in(team, "full")
    data = _stop_and_bring_back(_mode(team, mk_update, "auto"))
    _ended_with_busy_holder(team, holder_job=OTHER_JOB)
    _press(team, data)
    assert (qa.stops(team), team.launches()) == ([], [])
