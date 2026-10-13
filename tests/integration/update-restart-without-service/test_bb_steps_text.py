"""Black box: the manual restart steps per setup (entrypoints.md "Manual
steps text", design.md "UI"): exact English, no em dash, every new key
translated in all 15 languages with its placeholders kept."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from aipager import i18n
from aipager.bot import update_flow
from aipager.i18n import locales

EM_DASH = "—"
LANGS = locales.enabled_codes()
ROOT = Path(__file__).resolve().parents[3]

NEW_KEYS = {
    "btn.upd.restart", "upd.line.ap_waiting", "upd.summary.waiting", "upd.restart.button",
    "upd.note.ap_broken", "upd.ap.restart_offer", "upd.rs.checking", "upd.rs.nothing",
    "upd.rs.broken", "upd.rs.cancelled", "upd.rs.not_possible", "upd.steps.lead",
    "upd.steps.terminal", "upd.steps.launchd", "upd.steps.docker",
    "upd.steps.docker_image", "upd.hooks.newer", "toast.upd.restart_locked",
    "ui.update.restart_confirm", "ui.update.waiting_banner", "ui.update.hooks_banner",
    "ui.update.job.verifying", "ui.update.offer_confirm_manual",
    "ui.update.offer_confirm_steps",
}


def _plan(mode, steps, *, container=None, automatic=False, in_place=False,
          manual_command=None, reason=None):
    return {"mode": mode, "automatic": automatic, "reason": reason,
            "manual_command": manual_command, "in_place": in_place,
            "steps": steps, "container": container}


TERMINAL = _plan("foreground", "terminal")
LAUNCHD = _plan("launchd", "launchd",
                manual_command="launchctl kickstart -k gui/501/com.aipager.daemon")
DOCKER_ID = _plan("docker", "docker", container="0123456789ab")
DOCKER = _plan("docker", "docker")
SYSTEMD = _plan("systemd", "systemd", reason="KillMode=control-group would kill sessions",
                manual_command="systemctl --user restart aipager.service")

ALL_PLANS = {"terminal": TERMINAL, "launchd": LAUNCHD, "docker_id": DOCKER_ID,
             "docker": DOCKER, "systemd": SYSTEMD}


def _en(plan, html=False):
    return update_flow.restart_steps_text(plan, i18n.get("en"), to="1.2.0", html=html)


# ----- English, exact ---------------------------------------------------------------------

def test_terminal_lead_line():
    assert _en(TERMINAL).splitlines()[0] == "To run 1.2.0, restart aipager yourself:"


def test_terminal_step_line():
    assert ("In the terminal or tmux window where aipager runs, press Ctrl+C, "
            "then run aipager start.") in _en(TERMINAL)


def test_launchd_step_line():
    assert ("On the Mac, run launchctl kickstart -k gui/<uid>/com.aipager.daemon."
            in _en(LAUNCHD)) or re.search(
        r"On the Mac, run launchctl kickstart -k gui/\S+/com\.aipager\.daemon\.",
        _en(LAUNCHD)) is not None


def test_docker_step_uses_container_id():
    assert "On the Docker host, run docker restart 0123456789ab." in _en(DOCKER_ID)


def test_docker_step_without_id_uses_placeholder():
    assert "On the Docker host, run docker restart <container>." in _en(DOCKER)


def test_docker_image_note():
    assert ("Recreating the container starts it from its image again, which brings "
            "back the aipager version in that image.") in _en(DOCKER)


def test_terminal_has_no_docker_note():
    assert "Recreating the container" not in _en(TERMINAL)


def test_systemd_keeps_the_systemctl_command():
    assert "systemctl --user restart aipager.service" in _en(SYSTEMD)


def test_systemd_has_no_terminal_step():
    assert "Ctrl+C" not in _en(SYSTEMD)


def test_no_restart_it_yourself_wording_for_terminal():
    assert "restart it yourself" not in _en(TERMINAL)


@pytest.mark.parametrize("name", sorted(ALL_PLANS))
@pytest.mark.parametrize("html", [False, True])
def test_no_em_dash_in_english(name, html):
    assert EM_DASH not in _en(ALL_PLANS[name], html=html)


def test_html_wraps_the_command_in_code():
    assert "<code>aipager start</code>" in _en(TERMINAL, html=True)


def test_html_escapes_the_placeholder_container():
    assert "&lt;container&gt;" in _en(DOCKER, html=True)


def test_plain_text_has_no_tags():
    assert "<code>" not in _en(TERMINAL, html=False)


# ----- all 15 languages -------------------------------------------------------------------

def test_fifteen_languages():
    assert len(LANGS) == 15


@pytest.mark.parametrize("code", LANGS)
@pytest.mark.parametrize("name", sorted(ALL_PLANS))
def test_steps_render_in_every_language(code, name):
    text = update_flow.restart_steps_text(ALL_PLANS[name], i18n.get(code), to="1.2.0",
                                          html=False)
    # systemd keeps the unchanged "Restart needed: {reason}" + command text
    needle = "systemctl --user restart aipager.service" if name == "systemd" else "1.2.0"
    assert needle in text


@pytest.mark.parametrize("code", LANGS)
def test_terminal_steps_keep_the_command_in_every_language(code):
    text = update_flow.restart_steps_text(TERMINAL, i18n.get(code), to="1.2.0", html=False)
    assert "aipager start" in text and "Ctrl+C" in text


@pytest.mark.parametrize("code", LANGS)
def test_docker_steps_keep_the_command_in_every_language(code):
    text = update_flow.restart_steps_text(DOCKER_ID, i18n.get(code), to="1.2.0", html=False)
    assert "docker restart 0123456789ab" in text


@pytest.mark.parametrize("code", LANGS)
def test_launchd_steps_keep_the_command_in_every_language(code):
    text = update_flow.restart_steps_text(LAUNCHD, i18n.get(code), to="1.2.0", html=False)
    assert "launchctl kickstart -k" in text


@pytest.mark.parametrize("code", [c for c in LANGS if c != "en"])
def test_terminal_steps_are_translated(code):
    text = update_flow.restart_steps_text(TERMINAL, i18n.get(code), to="1.2.0", html=False)
    assert text != _en(TERMINAL)


@pytest.mark.parametrize("code", [c for c in LANGS if c != "en"])
@pytest.mark.parametrize("name", sorted(ALL_PLANS))
def test_no_em_dash_in_translations(code, name):
    text = update_flow.restart_steps_text(ALL_PLANS[name], i18n.get(code), to="1.2.0",
                                          html=False)
    assert EM_DASH not in text


def _catalog(code):
    path = ROOT / "aipager" / "i18n" / "locales" / f"{code}.jsonl"
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            if "k" in rec:
                out[rec["k"]] = rec.get("v")
    return out


def _source():
    out = {}
    for line in (ROOT / "tools" / "i18n" / "source.jsonl").read_text(
            encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            out[rec["k"]] = rec.get("en")
    return out


def test_every_new_key_is_in_the_source_catalog():
    assert NEW_KEYS - set(_source()) == set()


def test_no_new_english_text_has_an_em_dash():
    src = _source()
    assert [k for k in NEW_KEYS if EM_DASH in (src.get(k) or "")] == []


@pytest.mark.parametrize("code", [c for c in LANGS if c != "en"])
def test_every_new_key_is_translated(code):
    cat = _catalog(code)
    assert sorted(k for k in NEW_KEYS if not cat.get(k)) == []


@pytest.mark.parametrize("code", [c for c in LANGS if c != "en"])
def test_translations_keep_placeholders(code):
    cat, src = _catalog(code), _source()
    bad = []
    for k in NEW_KEYS:
        want = set(re.findall(r"\{(\w+)\}", src.get(k) or ""))
        have = set(re.findall(r"\{(\w+)\}", cat.get(k) or ""))
        if want != have:
            bad.append((k, sorted(want), sorted(have)))
    assert bad == []


@pytest.mark.parametrize("code", [c for c in LANGS if c != "en"])
def test_translations_have_no_em_dash(code):
    cat = _catalog(code)
    assert [k for k in NEW_KEYS if EM_DASH in (cat.get(k) or "")] == []


def test_restart_button_text():
    assert _source()["btn.upd.restart"] == "🔄 Restart now"


def test_hooks_notice_english():
    assert _source()["upd.hooks.newer"] == (
        "⚠️ The aipager update is only half done: Claude Code's hooks already run "
        "{hook}, but aipager itself still runs {running}. Restart aipager to finish it.")
