"""Check for updates: a newer aipager installed but not running yet is
"installed and waiting for a restart", offers Restart now and never
Update; start_offer refuses that update."""

from __future__ import annotations

import asyncio

import pytest

from aipager import self_restart, self_update
from aipager.bot import update_flow


def _check(env, run):
    async def scenario():
        return await env.manager.check()
    return run(scenario)


def _offer(status):
    return update_flow.update_offer(status)


def _buttons(status):
    _text, kb = update_flow.render_check(status, show_paths=True)
    return [b.callback_data for row in kb.inline_keyboard for b in row]


def test_installed_and_waiting_offers_restart_now_not_update(env, run):
    status = _check(env, run)
    ap = status["aipager"]
    assert (ap["installed"], ap["installed_ok"], ap["waiting"]) == ("1.1.0", True, True)
    offer = _offer(status)
    assert offer.kind is None and offer.restart_offer
    assert offer.lines[0] == ("aipager 1.1.0 is installed and waiting for a restart "
                              "(running 1.0.1).")
    assert offer.summary == "Restart aipager to finish the update."
    assert _buttons(status) == ["_:up:rs"]


def test_the_mini_app_payload_says_the_same(env, run):
    payload = update_flow.check_payload(_check(env, run), show_paths=True)
    assert payload["offer"] is None
    assert payload["restart_offer"] is True and payload["steps"] is None
    assert payload["restart_auto"] is False


def test_start_offer_refuses_the_installed_update(env, run):
    async def scenario():
        await env.manager.check()
        return [await env.manager.start_offer(k, chat_id=env.chat_id, user_id=1,
                                              origin="chat") for k in ("aipager", "both")]
    assert [r.error for r in run(scenario)] == ["offer_changed", "offer_changed"]


def test_both_with_a_claude_update_offers_claude_and_restart_now(env, run, monkeypatch):
    status = _check(env, run)
    status["claude"] = {"current": "2.1.300", "latest": "2.1.310", "update_available": True,
                        "method": "native", "channel": "latest", "blocked": None}
    offer = _offer(status)
    assert offer.kind == "claude" and offer.restart_offer
    assert _buttons(status) == ["_:up:go:cc", "_:up:x", "_:up:rs"]


def test_an_unknown_latest_still_counts_as_waiting(env, run):
    env.pypi_latest = None
    status = _check(env, run)
    assert status["aipager"]["waiting"] is True and _offer(status).restart_offer


def test_an_install_older_than_the_latest_release_is_an_update(env, run):
    env.pypi_latest = "1.2.0"
    status = _check(env, run)
    assert status["aipager"]["waiting"] is False
    offer = _offer(status)
    assert offer.kind == "aipager" and not offer.restart_offer
    assert "aipager 1.1.0 is installed but not running yet." in offer.notes
    # An offered update on a daemon that restarts in place.
    assert offer.restart == "Restart: tap Restart now once it is installed."


def test_disk_equal_to_running_is_todays_behaviour(env, run):
    env.restart_probe = ("1.0.1", True)
    status = _check(env, run)
    assert status["aipager"]["waiting"] is False
    offer = _offer(status)
    assert offer.kind == "aipager" and not offer.restart_offer and offer.notes == []
    assert _buttons(status) == ["_:up:go:ap", "_:up:x"]


def test_a_broken_install_is_never_waiting(env, run):
    env.restart_probe = (None, False)
    env.mp.setattr(self_update, "installed_version", lambda: "1.1.0")
    status = _check(env, run)
    assert (status["aipager"]["installed_ok"], status["aipager"]["waiting"]) == (False, False)
    offer = _offer(status)
    assert not offer.restart_offer and offer.kind == "aipager"
    assert ("aipager 1.1.0 is installed but does not load. Reinstall it with "
            "uv tool install --reinstall aipager." in offer.notes)


def test_a_daemon_that_cannot_restart_itself_shows_the_steps(env, run, monkeypatch):
    monkeypatch.setattr(self_restart, "exec_target", lambda: None)
    status = _check(env, run)
    # No interpreter to exec: the probe could not run, the in-process
    # reading stands.
    assert status["aipager"]["installed_ok"] is None


def test_a_waiting_install_without_an_in_place_restart_shows_the_steps(env, run):
    status = _check(env, run)
    status["restart"].update(in_place=False)
    offer = _offer(status)
    assert not offer.restart_offer
    assert offer.steps == ("To run 1.1.0, restart aipager yourself:\n"
                           "In the terminal or tmux window where aipager runs, press Ctrl+C, "
                           "then run aipager start.")
    text, kb = update_flow.render_check(status, show_paths=True)
    assert "press <code>Ctrl+C</code>, then run <code>aipager start</code>." in text
    assert [b.callback_data for row in kb.inline_keyboard for b in row] == ["_:up:chk"]


def test_systemd_automatic_waiting_offers_restart_now(env, run):
    env.set_under_unit(True)
    status = _check(env, run)
    assert _offer(status).restart_offer
    assert update_flow.check_payload(status, show_paths=True)["restart_auto"] is True


@pytest.mark.parametrize("stored", [True, False])
def test_the_stored_check_decides_the_same_way(env, run, stored):
    # update_offer recomputes "waiting" from the stored fields.
    status = _check(env, run)
    status["aipager"]["waiting"] = stored          # ignored
    assert _offer(status).restart_offer is True


def test_the_probe_is_bounded_by_the_step_timeout(env, run, monkeypatch):
    monkeypatch.setattr(update_flow, "STATUS_STEP_TIMEOUT_SECONDS", 0.05)
    import threading
    release = threading.Event()

    def slow(target):
        release.wait(5)
        return ("1.1.0", True, None)
    monkeypatch.setattr(self_update, "probe_restart_target", slow)

    async def scenario():
        try:
            return await asyncio.wait_for(env.manager.check(), 3)
        finally:
            release.set()
    status = run(scenario)
    assert status["aipager"]["installed_ok"] is None and status["aipager"]["waiting"] is False


def test_the_check_is_remembered_for_the_banner(env, run):
    _check(env, run)
    assert env.manager.waiting_info()["source"] == "installed"
    env.restart_probe = ("1.0.1", True)
    _check(env, run)
    assert env.manager.waiting_info() is None
