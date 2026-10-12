"""Black box: hooks newer than the daemon (spec part C, design.md
section 7): ``HookReceiver.on_hook_version`` and
``UpdateManager.note_hook_version``'s one owner notice per newer version
per run."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aipager import _version
from aipager.dtach import hook_receiver as hr
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

from bb_helpers import BBEnv, markup_data, owner_id, run_scenario


@pytest.fixture
def bb(monkeypatch, tmp_path, mk_bot):
    return BBEnv(monkeypatch, tmp_path, mk_bot)

RS = "_:up:rs"


def _bump(v: str, minor=1) -> str:
    major, mi, _patch = (int(x) for x in v.split(".")[:3])
    return f"{major}.{mi + minor}.0"


NEWER = _bump(_version.__version__)
NEWER2 = _bump(_version.__version__, 2)


def _logged(caplog, *needles) -> bool:
    return any(all(n in r.getMessage() for n in needles) for r in caplog.records)


def _notices(bb):
    return [m for m in bb.sent if RS in markup_data(m.get("reply_markup"))]


def _feed(bb, *versions, drain=20):
    async def body():
        for v in versions:
            bb.manager.note_hook_version(v)
            await bb.drain(drain)
    run_scenario(bb, body)


# ----- the notice ---------------------------------------------------------------------------

def test_version_file_matches_pyproject():
    import re
    root = Path(__file__).resolve().parents[3]
    text = (root / "pyproject.toml").read_text()
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    assert m is not None and _version.__version__ == m.group(1)


def test_newer_hook_sends_one_notice(bb):
    _feed(bb, NEWER)
    assert len(_notices(bb)) == 1


def test_notice_goes_to_the_owner_chat(bb):
    _feed(bb, NEWER)
    assert _notices(bb)[0]["chat_id"] == owner_id()


def test_notice_is_silent(bb):
    _feed(bb, NEWER)
    assert _notices(bb)[0].get("disable_notification") is True


def test_notice_text_names_both_versions(bb):
    _feed(bb, NEWER)
    text = _notices(bb)[0]["text"]
    assert NEWER in text and _version.__version__ in text


def test_notice_text_says_half_done(bb):
    _feed(bb, NEWER)
    assert "only half done" in _notices(bb)[0]["text"]


def test_notice_text_has_no_em_dash(bb):
    _feed(bb, NEWER)
    assert "—" not in _notices(bb)[0]["text"]


def test_same_version_twice_sends_one_notice(bb):
    _feed(bb, NEWER, NEWER)
    assert len(_notices(bb)) == 1


def test_same_version_many_times_sends_one_notice(bb):
    _feed(bb, *([NEWER] * 25), drain=2)
    assert len(_notices(bb)) == 1


def test_second_newer_version_gets_its_own_notice(bb):
    _feed(bb, NEWER, NEWER2)
    assert len(_notices(bb)) == 2


@pytest.mark.parametrize("value", [
    _version.__version__,                # equal
    "0.0.1",                             # older
    "1.0.0",                             # older
    "garbage", "", "9.x", "v", "1..2",   # unparseable
    None, 7, 1.5, ["9.9.9"], {"v": "9.9.9"},
])
def test_no_notice_for_equal_older_or_garbage(bb, value):
    _feed(bb, value)
    assert _notices(bb) == []


@pytest.mark.parametrize("value", [None, 7, ["9.9.9"], object()])
def test_note_hook_version_never_raises(bb, value):
    bb.manager.note_hook_version(value)


def test_note_hook_version_without_a_loop_never_raises(bb):
    bb.manager.note_hook_version(NEWER)


def test_no_owner_sends_nothing(bb):
    """Scope mode without an owner's private chat: no single owner."""
    bb.bot.scopes = [Scope(chat_id=-100424242, kind="group", label="team", members=(
        Member(id=555001, label="alice", role="admin"),
    ))]
    bb.bot.policy = load_policy(Path("/nonexistent/policy.yaml"),
                                Path("/nonexistent/policy.d"))
    _feed(bb, NEWER)
    assert bb.sent == []


def test_no_owner_logs_it(bb, caplog):
    bb.bot.scopes = [Scope(chat_id=-100424242, kind="group", label="team", members=(
        Member(id=555001, label="alice", role="admin"),
    ))]
    bb.bot.policy = load_policy(Path("/nonexistent/policy.yaml"),
                                Path("/nonexistent/policy.d"))
    with caplog.at_level(logging.DEBUG):
        _feed(bb, NEWER)
    assert "update.hooks_newer.no_owner" in caplog.text


def test_mismatch_is_logged(bb, caplog):
    with caplog.at_level(logging.DEBUG):
        _feed(bb, NEWER)
    assert f"update.hooks_newer hook={NEWER}" in caplog.text


def test_sent_notice_is_logged(bb, caplog):
    with caplog.at_level(logging.DEBUG):
        _feed(bb, NEWER)
    assert _logged(caplog, "update.hooks_newer.notice", "result=sent")


def test_waiting_info_reports_hooks(bb):
    _feed(bb, NEWER)
    w = bb.manager.waiting_info() or {}
    assert (w.get("version"), w.get("running"), w.get("source")) == (
        NEWER, _version.__version__, "hooks")


def test_waiting_info_reports_the_newest_hook(bb):
    _feed(bb, NEWER2, NEWER)
    assert (bb.manager.waiting_info() or {}).get("version") == NEWER2


def test_waiting_info_none_for_equal_hooks(bb):
    _feed(bb, _version.__version__)
    assert bb.manager.waiting_info() is None


# ----- busy: deferred, not lost --------------------------------------------------------------

def test_busy_manager_defers_the_notice(bb):
    """A restart job waiting for idle into a DIFFERENT version keeps the
    manager busy: the notice is deferred, not sent."""
    bb.restart_probe = ("1.0.5", True)
    bb.running = "1.0.1"
    bb.launch_as_daemon()
    bb.attach_stop()
    bb.add_session(status=Status.BUSY)

    async def body():
        res = await bb.start_restart()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        bb.manager.note_hook_version(NEWER)
        await bb.drain()
        sent = len(_notices(bb))
        bb.manager.control("cancel", res.job["id"])
        await bb.finish()
        return sent
    assert run_scenario(bb, body) == 0


def test_deferred_notice_is_sent_on_a_later_datagram(bb):
    bb.restart_probe = ("1.0.5", True)
    bb.launch_as_daemon()
    bb.attach_stop()
    sess = bb.add_session(status=Status.BUSY)

    async def body():
        res = await bb.start_restart()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        bb.manager.note_hook_version(NEWER)
        await bb.drain()
        bb.manager.control("cancel", res.job["id"])
        await bb.finish()
        # The session sits in the owner's chat: its turn must end first.
        sess.status = Status.IDLE
        bb.manager.note_hook_version(NEWER)
        await bb.drain()
    run_scenario(bb, body)
    assert len(_notices(bb)) == 1


def test_busy_deferral_is_logged(bb, caplog):
    bb.restart_probe = ("1.0.5", True)
    bb.launch_as_daemon()
    bb.attach_stop()
    bb.add_session(status=Status.BUSY)

    async def body():
        res = await bb.start_restart()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        with caplog.at_level(logging.DEBUG):
            bb.manager.note_hook_version(NEWER)
            await bb.drain()
        bb.manager.control("cancel", res.job["id"])
        await bb.finish()
    run_scenario(bb, body)
    assert _logged(caplog, "update.hooks_newer.notice", "result=deferred")


# ----- covered by a card that already offers Restart now -------------------------------------

def test_covered_by_a_restart_job_into_that_version(bb):
    bb.restart_probe = (NEWER, True)
    bb.launch_as_daemon()
    bb.attach_stop()
    bb.add_session(status=Status.BUSY)

    async def body():
        res = await bb.start_restart()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        bb.manager.note_hook_version(NEWER)
        await bb.drain()
        bb.manager.control("cancel", res.job["id"])
        await bb.finish()
        bb.manager.note_hook_version(NEWER)   # later datagram: still covered
        await bb.drain()
    run_scenario(bb, body)
    assert _notices(bb) == []


def test_covered_by_a_finished_update_card_offering_restart(bb):
    bb.restart_probe = ("1.0.1", True)
    bb.install_probe = (NEWER, True)
    bb.pypi_latest = NEWER
    bb.launch_as_daemon()
    bb.attach_stop()

    async def body():
        await bb.manager.check()
        res = await bb.manager.start("aipager", chat_id=bb.chat_id, user_id=bb.chat_id,
                                     origin="chat", status_message=bb.message)
        assert res.ok, res
        await bb.finish()
        assert RS in bb.last_markup_data()
        bb.manager.note_hook_version(NEWER)
        await bb.drain()
    run_scenario(bb, body)
    assert _notices(bb) == []


def test_covered_is_logged(bb, caplog):
    bb.restart_probe = ("1.0.1", True)
    bb.install_probe = (NEWER, True)
    bb.pypi_latest = NEWER
    bb.launch_as_daemon()
    bb.attach_stop()

    async def body():
        await bb.manager.check()
        await bb.manager.start("aipager", chat_id=bb.chat_id, user_id=bb.chat_id,
                               origin="chat", status_message=bb.message)
        await bb.finish()
        with caplog.at_level(logging.DEBUG):
            bb.manager.note_hook_version(NEWER)
            await bb.drain()
    run_scenario(bb, body)
    assert _logged(caplog, "update.hooks_newer.notice", "result=covered")


# ----- failed sends: counted, retried later, never spammed -------------------------------------

def test_failed_send_is_not_retried_on_the_next_datagram(bb):
    calls = []

    async def _fail(*a, **kw):
        calls.append(kw)
        raise RuntimeError("telegram down")
    bb.bot._app.bot.send_message = AsyncMock(side_effect=_fail)
    _feed(bb, NEWER, NEWER, NEWER)
    assert len(calls) <= 1


def test_tapping_the_notice_button_starts_a_restart(bb):
    from unittest.mock import MagicMock
    from bb_helpers import tap
    bb.restart_probe = (NEWER, True)
    bb.launch_as_daemon()
    bb.attach_stop()
    update, _q = tap(bb, RS, owner_id(), owner_id())

    async def body():
        bb.manager.note_hook_version(NEWER)
        await bb.drain()
        await bb.bot._handle_callback(update, MagicMock())
        await bb.finish()
    run_scenario(bb, body)
    assert len(bb.stops) == 1


# ----- the receiver ------------------------------------------------------------------------------

def _receiver():
    reg = SessionRegistry()
    notify = AsyncMock()
    recv = hr.HookReceiver(reg, notify)
    return reg, recv, notify


def _dgram(**fields) -> bytes:
    base = {"hook_event_name": "Notification", "session": "claude-web",
            "message": "Claude is waiting for your input"}
    base.update(fields)
    return json.dumps(base).encode()


def test_receiver_default_callback_is_none():
    _reg, recv, _n = _receiver()
    assert recv.on_hook_version is None


def test_receiver_passes_the_version(run_async):
    _reg, recv, _n = _receiver()
    got = []
    recv.on_hook_version = got.append
    run_async(recv._on_datagram(_dgram(aipager_version="9.9.9")))
    assert got == ["9.9.9"]


def test_receiver_passes_the_version_from_statusline(run_async):
    _reg, recv, _n = _receiver()
    got = []
    recv.on_hook_version = got.append
    msg = {"type": "statusline", "session": "claude-web", "aipager_version": "9.9.9",
           "payload": {}}
    run_async(recv._on_datagram(json.dumps(msg).encode()))
    assert got == ["9.9.9"]


def test_receiver_ignores_absent_field(run_async):
    _reg, recv, _n = _receiver()
    got = []
    recv.on_hook_version = got.append
    run_async(recv._on_datagram(_dgram()))
    assert got == []


@pytest.mark.parametrize("value", [123, None, ["9.9.9"], {"a": 1}, True])
def test_receiver_ignores_non_string(run_async, value):
    _reg, recv, _n = _receiver()
    got = []
    recv.on_hook_version = got.append
    run_async(recv._on_datagram(_dgram(aipager_version=value)))
    assert got == []


def test_receiver_accepts_40_characters(run_async):
    _reg, recv, _n = _receiver()
    got = []
    recv.on_hook_version = got.append
    v = "1." + "0" * 38
    assert len(v) == 40
    run_async(recv._on_datagram(_dgram(aipager_version=v)))
    assert got == [v]


def test_receiver_ignores_41_characters(run_async):
    _reg, recv, _n = _receiver()
    got = []
    recv.on_hook_version = got.append
    v = "1." + "0" * 39
    run_async(recv._on_datagram(_dgram(aipager_version=v)))
    assert got == []


def test_receiver_ignores_huge_value(run_async):
    _reg, recv, _n = _receiver()
    got = []
    recv.on_hook_version = got.append
    run_async(recv._on_datagram(_dgram(aipager_version="9" * 5000)))
    assert got == []


def test_receiver_without_session_does_not_call_back(run_async):
    _reg, recv, _n = _receiver()
    got = []
    recv.on_hook_version = got.append
    raw = json.dumps({"hook_event_name": "Notification", "aipager_version": "9.9.9"})
    run_async(recv._on_datagram(raw.encode()))
    assert got == []


def test_receiver_malformed_json_does_not_call_back(run_async):
    _reg, recv, _n = _receiver()
    got = []
    recv.on_hook_version = got.append
    run_async(recv._on_datagram(b'{"aipager_version": "9.9.9", '))
    assert got == []


def _handling(run_async, fields_seq, callback):
    reg, recv, notify = _receiver()
    reg.get_or_create("claude-web")
    if callback is not None:
        recv.on_hook_version = callback
    for fields in fields_seq:
        run_async(recv._on_datagram(_dgram(**fields)))
    sess = reg.get("claude-web")

    def norm(x):
        return getattr(x, "name", None) if hasattr(x, "last_idle_at") else repr(x)
    calls = [(tuple(norm(a) for a in c.args), sorted(c.kwargs))
             for c in notify.await_args_list]
    return calls, sess.status


SEQ_PLAIN = [dict(hook_event_name="UserPromptSubmit", prompt="hi"),
             dict(hook_event_name="Stop"),
             dict(hook_event_name="Notification", message="Claude is waiting for your input")]


def _with_version(seq, v="9.9.9"):
    return [dict(f, aipager_version=v) for f in seq]


def test_handling_identical_with_and_without_field(run_async):
    assert _handling(run_async, SEQ_PLAIN, None) == _handling(
        run_async, _with_version(SEQ_PLAIN), lambda v: None)


def test_raising_callback_does_not_change_handling(run_async):
    def boom(v):
        raise RuntimeError("callback broke")
    assert _handling(run_async, SEQ_PLAIN, None) == _handling(
        run_async, _with_version(SEQ_PLAIN), boom)


def test_garbage_field_does_not_change_handling(run_async):
    got = []
    assert _handling(run_async, SEQ_PLAIN, None) == _handling(
        run_async, _with_version(SEQ_PLAIN, "x" * 500), got.append)


def test_handling_baseline_is_not_vacuous(run_async):
    """The comparisons above compare real handling: the sequence makes
    the receiver call its notify function."""
    calls, _status = _handling(run_async, SEQ_PLAIN, None)
    assert calls != []
