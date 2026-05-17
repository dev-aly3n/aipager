"""Tests for aipager.service — template rendering and dispatch logic.

Does NOT run systemctl or launchctl. The actual integration with the OS
service manager must be tested manually on real Linux/macOS machines.
"""

from pathlib import Path

import pytest

from aipager import service


def test_linux_unit_renders_with_resolved_path(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda name: "/fake/bin/aipager")
    out = service._render_linux_unit()
    assert "[Unit]" in out
    assert "ExecStart=/fake/bin/aipager start" in out
    assert "ExecStartPre=-/bin/rm -f /tmp/aipager.sock" in out
    assert "EnvironmentFile=-%h/.config/aipager/config.env" in out
    assert "WantedBy=default.target" in out


def test_macos_plist_renders_with_resolved_path(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda name: "/fake/bin/aipager")
    out = service._render_macos_plist()
    assert "<?xml" in out
    assert "<key>Label</key>" in out
    assert f"<string>{service.MACOS_LABEL}</string>" in out
    assert "<string>/fake/bin/aipager</string>" in out
    assert "<string>start</string>" in out
    assert "<key>RunAtLoad</key>" in out
    assert "<true/>" in out


def test_resolve_bin_raises_when_not_on_path(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError):
        service._resolve_aipager_bin()


def test_platform_detection(monkeypatch):
    monkeypatch.setattr(service.platform, "system", lambda: "Linux")
    assert service._platform() == "linux"
    monkeypatch.setattr(service.platform, "system", lambda: "Darwin")
    assert service._platform() == "macos"
    monkeypatch.setattr(service.platform, "system", lambda: "Windows")
    assert service._platform() == "windows"


def test_dispatch_table_covers_both_platforms():
    for plat in ("linux", "macos"):
        for sub in ("install", "start", "stop", "status", "logs", "uninstall"):
            assert sub in service._DISPATCH[plat], f"missing {plat}/{sub}"


def test_paths_use_home():
    home = Path.home()
    assert service.LINUX_UNIT_PATH.is_relative_to(home)
    assert service.MACOS_PLIST_PATH.is_relative_to(home)
    assert service.MACOS_LOG_PATH.is_relative_to(home)
