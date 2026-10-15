"""The exact steps to restart aipager by hand, per setup, in every
language (design.md "UI")."""

from __future__ import annotations

import os

import pytest

from aipager import i18n
from aipager.bot import update_flow
from aipager.i18n.locales import LOCALES

LEAD = "To run 1.1.0, restart aipager yourself:"


def _plan(steps, **kw):
    return {"mode": steps, "automatic": False, "reason": None, "manual_command": None,
            "in_place": False, "steps": steps, "container": None, **kw}


def _text(plan, html=False, code="en"):
    return update_flow.restart_steps_text(plan, i18n.get(code), to="1.1.0", html=html)


def test_terminal_steps():
    assert _text(_plan("terminal")) == (
        LEAD + "\nIn the terminal or tmux window where aipager runs, press Ctrl+C, then run "
               "aipager start.")
    assert _text(_plan("terminal"), html=True) == (
        LEAD + "\nIn the terminal or tmux window where aipager runs, press "
               "<code>Ctrl+C</code>, then run <code>aipager start</code>.")


def test_launchd_steps_use_the_plans_kickstart_command():
    cmd = f"launchctl kickstart -k gui/{os.getuid()}/com.aipager.daemon"
    assert _text(_plan("launchd", manual_command=cmd)) == f"{LEAD}\nOn the Mac, run {cmd}."
    # Without the plan's command: the same command, built here.
    assert _text(_plan("launchd")) == f"{LEAD}\nOn the Mac, run {cmd}."


def test_docker_steps_name_the_container_when_known():
    image = ("Recreating the container starts it from its image again, which brings back "
             "the aipager version in that image.")
    assert _text(_plan("docker", container="0123456789ab")) == (
        f"{LEAD}\nOn the Docker host, run docker restart 0123456789ab.\n{image}")
    assert _text(_plan("docker")) == (
        f"{LEAD}\nOn the Docker host, run docker restart <container>.\n{image}")
    assert ("<code>docker restart &lt;container&gt;</code>"
            in _text(_plan("docker"), html=True))


def test_systemd_keeps_its_restart_needed_text():
    plan = _plan("systemd", reason="KillMode=mixed: fix it",
                 manual_command="systemctl --user restart aipager.service")
    assert _text(plan) == ("Restart needed: KillMode=mixed: fix it\n"
                           "systemctl --user restart aipager.service")


@pytest.mark.parametrize("steps", ["terminal", "launchd", "docker", "systemd"])
@pytest.mark.parametrize("code", sorted(LOCALES))
def test_every_language_renders_every_step(code, steps):
    for html in (False, True):
        text = _text(_plan(steps, container="0123456789ab"), html=html, code=code)
        assert "—" not in text and "{" not in text
        assert "1.1.0" in text or steps == "systemd"
        if steps == "terminal":
            assert "Ctrl+C" in text and "aipager start" in text
        if steps == "docker":
            assert "docker restart 0123456789ab" in text


def test_no_new_text_has_a_long_dash():
    from aipager.miniapp import ui_text
    keys = [m for m in vars(update_flow).values()
            if isinstance(m, i18n.Msg) and m.key.startswith(
                ("upd.steps.", "upd.rs.", "upd.hooks.", "btn.upd.restart", "upd.line.ap_waiting",
                 "upd.summary.waiting", "upd.restart.button", "upd.note.ap_broken",
                 "upd.ap.restart_offer", "toast.upd.restart_locked"))
            and m.key != "btn.upd.restart_now"]
    keys += [m for m in vars(ui_text).values() if isinstance(m, i18n.Msg) and m.key in (
        "ui.update.restart_confirm", "ui.update.waiting_banner", "ui.update.hooks_banner",
        "ui.update.job.verifying", "ui.update.offer_confirm_manual")]
    assert len(keys) == 24
    assert not [m.key for m in keys if "—" in str(m)]
