"""An install whose Mini App cannot start must say so, everywhere.

aiohttp ships with aipager now, so on an intact install it is always
there. It is still checked, because "intact" is an assumption and the
cost of being wrong is a feature that fails silently. Before these checks, all three surfaces disagreed about that:
`aipager miniapp enable` printed a green tick regardless, `/app` blamed
a missing tunnel and told the user to install Tailscale, and `doctor`
had no opinion at all — so an install whose Mini App could never start
reported "13 ok · 0 warn · 0 fail" and the only clue was a daemon log
line nobody reads.
"""
from __future__ import annotations

import argparse

import pytest

from aipager import doctor
from aipager.miniapp import cli as miniapp_cli
from aipager.miniapp.server import (
    miniapp_extra_available, reinstall_with_miniapp_hint,
)


def _enable_args(**kw):
    ns = argparse.Namespace(port=None, url=None, yes=True)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


# ---- the shared helper --------------------------------------------------

def test_extra_available_is_true_when_aiohttp_imports(monkeypatch):
    monkeypatch.setattr("importlib.util.find_spec", lambda n: object())
    assert miniapp_extra_available() is True


def test_extra_available_is_false_when_aiohttp_is_absent(monkeypatch):
    monkeypatch.setattr("importlib.util.find_spec", lambda n: None)
    assert miniapp_extra_available() is False


@pytest.mark.parametrize("exc", [
    ValueError("weird sys.path"),
    ImportError("no"),
    RuntimeError("a broken meta-path finder"),
])
def test_extra_available_never_raises(monkeypatch, exc):
    """It runs on the /app path and inside doctor — and `doctor.run_all()`
    wraps its checks in nothing, so anything escaping here takes down the
    whole command instead of greying out one row. Any exception at all
    must degrade to "not available"."""
    def _boom(name):
        raise exc

    monkeypatch.setattr("importlib.util.find_spec", _boom)
    assert miniapp_extra_available() is False


def test_doctor_survives_a_check_that_cannot_introspect_imports(monkeypatch):
    """The same failure one level up: the row degrades, doctor still runs."""
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)

    def _boom(name):
        raise RuntimeError("a broken meta-path finder")

    monkeypatch.setattr("importlib.util.find_spec", _boom)
    r = doctor.check_miniapp()          # must not raise
    assert r.status in (doctor.OK, doctor.WARN)


# ---- `aipager miniapp enable` -------------------------------------------

def test_enable_does_not_claim_success_when_the_extra_is_missing(
    monkeypatch, capsys, tmp_path,
):
    monkeypatch.setattr(miniapp_cli, "_save_miniapp_config", lambda s: None)
    monkeypatch.setattr(
        miniapp_cli, "_current_miniapp_config",
        lambda: {"enabled": False, "port": 8765, "public_url": ""})
    monkeypatch.setattr(
        "aipager.miniapp.server.miniapp_extra_available", lambda: False)

    rc = miniapp_cli._cmd_miniapp_enable(_enable_args())

    out = capsys.readouterr()
    combined = out.out + out.err
    assert "✓" not in combined or "Mini App enabled" not in combined, (
        f"printed a success tick for a Mini App that cannot start: {combined!r}")
    assert "not" in combined.lower() and "install" in combined.lower()
    # Fail-open: saving settings is fine, refusing the command is not.
    assert rc == 0


def test_enable_claims_success_when_the_extra_is_present(
    monkeypatch, capsys,
):
    monkeypatch.setattr(miniapp_cli, "_save_miniapp_config", lambda s: None)
    monkeypatch.setattr(
        miniapp_cli, "_current_miniapp_config",
        lambda: {"enabled": False, "port": 8765, "public_url": ""})
    monkeypatch.setattr(
        "aipager.miniapp.server.miniapp_extra_available", lambda: True)

    rc = miniapp_cli._cmd_miniapp_enable(_enable_args())

    assert rc == 0
    assert "Mini App enabled" in capsys.readouterr().out


def test_install_hint_matches_the_owning_installer(monkeypatch):
    for installer, expected in (
        ("uv", "uv tool install"), ("pipx", "pipx install"),
        ("brew", "brew reinstall"), (None, "pip install"),
    ):
        monkeypatch.setattr("aipager.updater._detect_installer",
                            lambda i=installer: i)
        assert expected in reinstall_with_miniapp_hint()


# ---- doctor -------------------------------------------------------------

def test_doctor_warns_when_enabled_but_not_installed(monkeypatch):
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr(
        "aipager.miniapp.server.miniapp_extra_available", lambda: False)
    r = doctor.check_miniapp()
    assert r.status == doctor.WARN, "a dead Mini App rendered as OK"
    assert r.fix


def test_doctor_never_fails_on_the_miniapp_row(monkeypatch):
    """Fail-open: the Mini App is optional and must never sink the run."""
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr(
        "aipager.miniapp.server.miniapp_extra_available", lambda: False)
    assert doctor.check_miniapp().status != doctor.FAIL


def test_doctor_is_ok_when_miniapp_disabled(monkeypatch):
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", False)
    r = doctor.check_miniapp()
    assert r.status == doctor.OK
    assert "disabled" in " ".join(r.detail)


def test_doctor_is_ok_when_enabled_and_installed(monkeypatch):
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr("aipager.config.MINIAPP_PORT", 8765)
    monkeypatch.setattr(
        "aipager.miniapp.server.miniapp_extra_available", lambda: True)
    assert doctor.check_miniapp().status == doctor.OK


def test_the_miniapp_row_is_actually_registered():
    """A check nobody runs is not a check."""
    assert doctor.check_miniapp in doctor.CHECKS
