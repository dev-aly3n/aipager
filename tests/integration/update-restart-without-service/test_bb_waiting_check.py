"""Black box: "Check for updates" knows an installed-but-not-running
aipager (spec part B, design.md section 6), and the update job's ending
in foreground (design.md section 1, the first success criterion)."""

from __future__ import annotations

import os

import pytest

from aipager.bot import update_flow

from bb_helpers import BBEnv, markup_data, run_scenario


@pytest.fixture
def bb(monkeypatch, tmp_path, mk_bot):
    return BBEnv(monkeypatch, tmp_path, mk_bot)

RS = "_:up:rs"


def _check(bb):
    bb.launch_as_daemon()
    return run_scenario(bb, bb.manager.check)


def _buttons(bb, status):
    _html, kb = update_flow.render_check(status, show_paths=True)
    return markup_data(kb)


def _html(bb, status):
    return update_flow.render_check(status, show_paths=True)[0]


# ----- installed_waiting: the pure rule ----------------------------------------------------

@pytest.mark.parametrize("running,installed,ok,latest,expected", [
    ("1.0.1", "1.1.0", True, "1.1.0", True),     # disk == latest
    ("1.0.1", "1.2.0", True, "1.1.0", True),     # disk newer than latest
    ("1.0.1", "1.1.0", True, None, True),        # latest unknown
    ("1.0.1", "1.1.0", True, "1.2.0", False),    # disk older than latest
    ("1.0.1", "1.0.1", True, "1.1.0", False),    # disk == running
    ("1.0.1", "1.0.0", True, "1.1.0", False),    # disk older than running
    ("1.0.1", "1.1.0", False, "1.1.0", False),   # does not load
    ("1.0.1", None, False, "1.1.0", False),      # unknown disk version
    ("1.0.1", "1.0.2", True, "1.0.2", True),     # smallest newer patch
])
def test_installed_waiting_rule(running, installed, ok, latest, expected):
    assert update_flow.installed_waiting(running, installed, ok, latest) is expected


# ----- waiting: disk 1.1.0 == latest, running 1.0.1 ---------------------------------------

def test_waiting_check_offers_restart_now(bb):
    status = _check(bb)
    assert RS in _buttons(bb, status)


def test_waiting_check_offers_no_update_button(bb):
    status = _check(bb)
    assert not any(d and d.startswith("_:up:go:") for d in _buttons(bb, status))


def test_waiting_check_says_installed_and_waiting(bb):
    status = _check(bb)
    assert "aipager 1.1.0 is installed and waiting for a restart" in _html(bb, status)


def test_waiting_check_has_no_check_again_button(bb):
    status = _check(bb)
    assert "_:up:chk" not in _buttons(bb, status)


def test_waiting_offer_kind_is_none(bb):
    status = _check(bb)
    assert update_flow.update_offer(status).kind is None


def test_waiting_offer_restart_offer_true(bb):
    status = _check(bb)
    assert update_flow.update_offer(status).restart_offer is True


def test_waiting_mini_app_payload_has_no_offer(bb):
    status = _check(bb)
    assert update_flow.check_payload(status, show_paths=True).get("offer") is None


def test_waiting_mini_app_payload_restart_offer(bb):
    status = _check(bb)
    assert update_flow.check_payload(status, show_paths=True).get("restart_offer") is True


def test_waiting_mini_app_payload_steps_null_when_restartable(bb):
    status = _check(bb)
    assert update_flow.check_payload(status, show_paths=True).get("steps") is None


def test_waiting_status_field(bb):
    status = _check(bb)
    assert status["aipager"]["waiting"] is True


def test_waiting_check_never_runs_an_installer(bb):
    _check(bb)
    assert not [c for c in bb.calls if c[0].startswith("/abs/bin/")]


def test_waiting_start_offer_aipager_is_refused(bb):
    bb.launch_as_daemon()

    async def body():
        await bb.manager.check()
        return await bb.manager.start_offer("aipager", chat_id=bb.chat_id,
                                            user_id=bb.chat_id, origin="chat",
                                            status_message=bb.message)
    res = run_scenario(bb, body)
    assert (res.ok, res.error) == (False, "offer_changed")


def test_waiting_start_offer_both_is_refused(bb):
    bb.claude_found = True
    bb.claude_latest = "2.1.290"
    bb.launch_as_daemon()

    async def body():
        await bb.manager.check()
        return await bb.manager.start_offer("both", chat_id=bb.chat_id,
                                            user_id=bb.chat_id, origin="chat",
                                            status_message=bb.message)
    res = run_scenario(bb, body)
    assert (res.ok, res.error) == (False, "offer_changed")


def test_waiting_with_claude_update_offers_claude_and_restart(bb):
    bb.claude_found = True
    bb.claude_latest = "2.1.290"
    status = _check(bb)
    data = _buttons(bb, status)
    assert "_:up:go:cc" in data and RS in data


def test_waiting_start_offer_starts_no_job(bb):
    bb.launch_as_daemon()

    async def body():
        await bb.manager.check()
        await bb.manager.start_offer("aipager", chat_id=bb.chat_id, user_id=bb.chat_id,
                                     origin="chat", status_message=bb.message)
        return bb.manager.snapshot()
    assert run_scenario(bb, body) is None


def test_disk_newer_than_latest_is_waiting(bb):
    bb.restart_probe = ("1.2.0", True)
    status = _check(bb)
    assert RS in _buttons(bb, status)


def test_latest_unknown_disk_newer_is_waiting(bb):
    bb.pypi_latest = None
    status = _check(bb)
    assert RS in _buttons(bb, status)


def test_waiting_info_after_check(bb):
    _check(bb)
    assert bb.manager.waiting_info() == {"version": "1.1.0", "running": "1.0.1",
                                         "source": "installed"}


def test_waiting_check_logs_waiting(bb, caplog):
    import logging
    with caplog.at_level(logging.INFO):
        _check(bb)
    lines = [r.getMessage() for r in caplog.records if "update.check" in r.getMessage()]
    assert any("waiting=True" in m or "waiting=1" in m or "waiting=true" in m
               for m in lines), lines


# ----- not waiting ------------------------------------------------------------------------

def test_disk_equals_running_offers_update(bb):
    bb.restart_probe = ("1.0.1", True)
    status = _check(bb)
    assert "_:up:go:ap" in _buttons(bb, status)


def test_disk_equals_running_offers_no_restart(bb):
    bb.restart_probe = ("1.0.1", True)
    status = _check(bb)
    assert RS not in _buttons(bb, status)


def test_disk_equals_running_says_nothing_about_waiting(bb):
    bb.restart_probe = ("1.0.1", True)
    status = _check(bb)
    assert "waiting for a restart" not in _html(bb, status)


def test_disk_equals_running_waiting_info_none(bb):
    bb.restart_probe = ("1.0.1", True)
    _check(bb)
    assert bb.manager.waiting_info() is None


def test_disk_older_than_latest_offers_update(bb):
    bb.pypi_latest = "1.2.0"
    status = _check(bb)
    assert "_:up:go:ap" in _buttons(bb, status)


def test_disk_older_than_latest_offers_no_restart(bb):
    bb.pypi_latest = "1.2.0"
    status = _check(bb)
    assert RS not in _buttons(bb, status)


def test_broken_install_is_not_waiting(bb):
    bb.restart_probe = (None, False)
    bb.disk_metadata = "1.1.0"
    status = _check(bb)
    assert RS not in _buttons(bb, status)


def test_broken_install_still_offers_update(bb):
    bb.restart_probe = (None, False)
    bb.disk_metadata = "1.1.0"
    status = _check(bb)
    assert "_:up:go:ap" in _buttons(bb, status)


def test_everything_current_offers_check_again(bb):
    bb.restart_probe = ("1.0.1", True)
    bb.pypi_latest = "1.0.1"
    status = _check(bb)
    assert _buttons(bb, status) == ["_:up:chk"]


# ----- waiting but this daemon cannot restart itself ------------------------------------------

def _mismatch(bb):
    bb.set_under_unit(True)
    bb.main_pid = os.getpid() + 1


def test_waiting_unrestartable_has_no_restart_button(bb):
    _mismatch(bb)
    status = _check(bb)
    assert RS not in _buttons(bb, status)


def test_waiting_unrestartable_has_no_update_button(bb):
    _mismatch(bb)
    status = _check(bb)
    assert not any(d and d.startswith("_:up:go:") for d in _buttons(bb, status))


def test_waiting_unrestartable_chat_shows_terminal_steps(bb):
    _mismatch(bb)
    status = _check(bb)
    assert "Ctrl+C" in _html(bb, status)


def test_waiting_unrestartable_payload_steps_text(bb):
    _mismatch(bb)
    status = _check(bb)
    steps = update_flow.check_payload(status, show_paths=True).get("steps") or ""
    assert steps.startswith("To run 1.1.0, restart aipager yourself:")


def test_waiting_unrestartable_payload_restart_offer_false(bb):
    _mismatch(bb)
    status = _check(bb)
    assert update_flow.check_payload(status, show_paths=True).get("restart_offer") is False


def test_waiting_systemd_automatic_offers_restart_now(bb):
    """restart_offer = in_place or automatic."""
    bb.set_under_unit(True)
    status = _check(bb)
    assert RS in _buttons(bb, status)


def test_restart_auto_true_under_systemd(bb):
    bb.set_under_unit(True)
    status = _check(bb)
    assert update_flow.check_payload(status, show_paths=True).get("restart_auto") is True


def test_restart_auto_false_in_foreground(bb):
    status = _check(bb)
    assert update_flow.check_payload(status, show_paths=True).get("restart_auto") is False


# ----- the update job's end in foreground ---------------------------------------------------

async def _update(bb):
    await bb.manager.check()
    res = await bb.manager.start("aipager", chat_id=bb.chat_id, user_id=bb.chat_id,
                                 origin="chat", status_message=bb.message)
    assert res.ok, res
    await bb.finish()
    return res


def _update_setup(bb):
    bb.restart_probe = ("1.0.1", True)   # nothing installed yet when checking
    bb.install_probe = ("1.1.0", True)
    bb.launch_as_daemon()
    bb.attach_stop()


def test_foreground_update_ends_done(bb):
    _update_setup(bb)
    run_scenario(bb, lambda: _update(bb))
    assert bb.manager.snapshot()["phase"] == "done"


def test_foreground_update_offers_restart_now_button(bb):
    _update_setup(bb)
    run_scenario(bb, lambda: _update(bb))
    assert RS in bb.last_markup_data()


def test_foreground_update_snapshot_can_restart(bb):
    _update_setup(bb)
    run_scenario(bb, lambda: _update(bb))
    assert bb.manager.snapshot().get("can_restart") is True


def test_foreground_update_never_stops_by_itself(bb):
    _update_setup(bb)
    run_scenario(bb, lambda: _update(bb))
    assert (bb.stops, bb.schedule_calls()) == ([], [])


def test_foreground_update_card_says_tap_restart_now(bb):
    _update_setup(bb)
    run_scenario(bb, lambda: _update(bb))
    assert "Tap Restart now to run it" in bb.last_text()


def test_foreground_update_card_no_longer_says_restart_it_yourself(bb):
    _update_setup(bb)
    run_scenario(bb, lambda: _update(bb))
    assert "restart it yourself" not in bb.last_text()


def test_foreground_update_releases_the_busy_state(bb):
    _update_setup(bb)
    run_scenario(bb, lambda: _update(bb))
    assert bb.manager.busy is False


def test_launchd_update_offers_restart_now(bb, monkeypatch):
    from aipager import self_update
    monkeypatch.setattr(self_update, "_system", lambda: "Darwin")
    monkeypatch.setenv("XPC_SERVICE_NAME", "com.aipager.daemon")
    _update_setup(bb)
    run_scenario(bb, lambda: _update(bb))
    assert RS in bb.last_markup_data()


def test_mainpid_mismatch_update_shows_steps_not_button(bb):
    _mismatch(bb)
    _update_setup(bb)
    run_scenario(bb, lambda: _update(bb))
    assert RS not in bb.last_markup_data() and "Ctrl+C" in bb.last_text()


def test_mainpid_mismatch_update_snapshot_cannot_restart(bb):
    _mismatch(bb)
    _update_setup(bb)
    run_scenario(bb, lambda: _update(bb))
    assert not bb.manager.snapshot().get("can_restart")


def test_systemd_update_still_schedules_its_restart(bb):
    bb.set_under_unit(True)
    _update_setup(bb)
    run_scenario(bb, lambda: _update(bb))
    assert (len(bb.schedule_calls()), bb.manager.snapshot()["phase"]) == (
        1, "restart_scheduled")


def test_systemd_update_offers_no_restart_now_button(bb):
    bb.set_under_unit(True)
    _update_setup(bb)
    run_scenario(bb, lambda: _update(bb))
    assert RS not in bb.last_markup_data()


def test_waiting_info_after_foreground_update(bb):
    _update_setup(bb)
    run_scenario(bb, lambda: _update(bb))
    assert (bb.manager.waiting_info() or {}).get("version") == "1.1.0"
