"""aipager.install_source: which installer owns the RUNNING interpreter,
and the one installer -> command table (roadmap 8.36).

Every detection test builds a fake prefix under tmp_path and passes it in
through ``detect_install_source``'s keyword overrides, so nothing depends on
how the test runner's own venv was installed. ``_DOCKERENV`` is always
pointed at tmp: a CI runner inside a container would otherwise classify
every case as ``docker``.
"""

from __future__ import annotations

import os
import stat
import sys

import pytest

from aipager import install_source
from aipager.install_source import (
    InstallSource,
    augmented_path,
    detect_install_source,
    extra_install_argv,
    reinstall_hint,
    resolve_tool,
    spawn_env,
    uninstall_argv,
    upgrade_argv,
    upgrade_env,
)


@pytest.fixture(autouse=True)
def _no_dockerenv(tmp_path, monkeypatch):
    monkeypatch.setattr(install_source, "_DOCKERENV", str(tmp_path / "no-dockerenv"))


def _detect(prefix, *, base="/usr", direct_url=None, env=None, uid=None, **kw):
    return detect_install_source(
        prefix=str(prefix), executable=str(prefix / "bin" / "python"),
        base_prefix=base, direct_url=direct_url, env=env or {},
        uid=os.getuid() if uid is None else uid, **kw)


def _pipx_prefix(tmp_path):
    prefix = tmp_path / "pipx" / "venvs" / "aipager"
    prefix.mkdir(parents=True)
    (prefix / "pipx_metadata.json").write_text("{}")
    return prefix


def _uv_prefix(tmp_path):
    prefix = tmp_path / "uv" / "tools" / "aipager"
    prefix.mkdir(parents=True)
    (prefix / "uv-receipt.toml").write_text("")
    return prefix


def _fake_home_with_tools(tmp_path, monkeypatch, *names):
    """A HOME whose ~/.local/bin holds executable stubs, and a PATH that
    reaches none of them — the systemd-unit / ssh-shell situation."""
    home = tmp_path / "home"
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    for name in names:
        tool = local_bin / name
        tool.write_text("#!/bin/sh\nexit 0\n")
        tool.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    return local_bin


# ----- detection by prefix markers ------------------------------------------

def test_detect_pipx_by_metadata_marker(tmp_path):
    src = _detect(_pipx_prefix(tmp_path))
    assert src.kind == "pipx"
    assert src.upgradable is True
    assert src.origin == "index"


def test_detect_uv_by_receipt(tmp_path):
    src = _detect(_uv_prefix(tmp_path))
    assert src.kind == "uv"
    assert src.upgradable is True


def test_detect_brew_by_cellar(tmp_path):
    prefix = tmp_path / "homebrew" / "Cellar" / "aipager" / "0.7.13" / "libexec"
    prefix.mkdir(parents=True)
    src = _detect(prefix)
    assert src.kind == "brew"
    assert src.upgradable is True


def test_detect_pip_venv(tmp_path):
    prefix = tmp_path / "venv"
    prefix.mkdir()
    src = _detect(prefix, base="/usr")
    assert src.kind == "pip"
    assert src.upgradable is True


def test_editable_install_is_refused_even_with_pipx_marker(tmp_path):
    src = _detect(_pipx_prefix(tmp_path),
                  direct_url={"url": "file:///src/aipager",
                              "dir_info": {"editable": True}})
    assert src.kind == "editable"
    assert src.upgradable is False
    assert src.reason and "editable" in src.reason
    assert upgrade_argv(src) is None


# ----- refusals ---------------------------------------------------------------

def test_detect_refuses_nix(tmp_path):
    src = detect_install_source(
        prefix="/nix/store/abc123-python3-aipager", executable="/nix/store/x/python",
        base_prefix="/nix/store/other", direct_url=None, env={}, uid=os.getuid())
    assert (src.kind, src.upgradable) == ("nix", False)


def test_detect_refuses_snap(tmp_path):
    src = _detect(_pipx_prefix(tmp_path), env={"SNAP": "/snap/aipager/12"})
    assert (src.kind, src.upgradable) == ("snap", False)


def test_detect_refuses_docker(tmp_path, monkeypatch):
    marker = tmp_path / "dockerenv"
    marker.write_text("")
    monkeypatch.setattr(install_source, "_DOCKERENV", str(marker))
    src = _detect(_pipx_prefix(tmp_path))
    assert (src.kind, src.upgradable) == ("docker", False)


def test_detect_refuses_system():
    src = detect_install_source(prefix="/usr", executable="/usr/bin/python3",
                                base_prefix="/usr", direct_url=None, env={},
                                uid=os.getuid())
    assert (src.kind, src.upgradable) == ("system", False)
    assert "OS package manager" in src.reason


def test_detect_refuses_unknown(tmp_path):
    prefix = tmp_path / "plain"
    prefix.mkdir()
    src = _detect(prefix, base=str(prefix))
    assert (src.kind, src.upgradable) == ("unknown", False)


def test_detection_never_raises(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("broken metadata")
    monkeypatch.setattr(install_source, "_detect", _boom)
    src = detect_install_source()
    assert src.kind == "unknown" and src.upgradable is False


# ----- never touch another user's install ------------------------------------

def test_prefix_not_owned_by_user_is_refused(tmp_path):
    src = _detect(_pipx_prefix(tmp_path), uid=os.getuid() + 1)
    assert src.kind == "foreign"
    assert src.owner_kind == "pipx"
    assert src.upgradable is False
    assert "another user" in src.reason
    assert upgrade_argv(src) is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_prefix_not_writable_is_refused(tmp_path):
    prefix = tmp_path / "venv"
    prefix.mkdir()
    prefix.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        src = _detect(prefix, base="/usr")
    finally:
        prefix.chmod(0o755)
    assert src.kind == "foreign"
    assert src.upgradable is False


# ----- origin -----------------------------------------------------------------

def test_local_path_origin_is_described(tmp_path):
    src = _detect(_pipx_prefix(tmp_path),
                  direct_url={"url": "file:///home/me/aipager", "dir_info": {}})
    assert src.origin == "local"
    assert src.origin_detail == "/home/me/aipager"
    assert src.describe() == "pipx, from local path /home/me/aipager"
    # Group chats never see a filesystem path.
    assert "/home/me" not in src.describe(show_paths=False)


def test_vcs_origin(tmp_path):
    src = _detect(_uv_prefix(tmp_path),
                  direct_url={"url": "https://github.com/x/aipager",
                              "vcs_info": {"vcs": "git"}})
    assert src.origin == "vcs"


def test_index_origin_describe(tmp_path):
    assert _detect(_pipx_prefix(tmp_path)).describe() == "pipx, from PyPI"


def test_foreign_describe_hides_path_in_groups(tmp_path):
    src = _detect(_pipx_prefix(tmp_path), uid=os.getuid() + 1)
    assert str(tmp_path) not in src.describe(show_paths=False)


# ----- tool resolution and the command table ---------------------------------

def test_resolve_tool_finds_uv_in_local_bin_off_path(tmp_path, monkeypatch):
    local_bin = _fake_home_with_tools(tmp_path, monkeypatch, "uv")
    found = resolve_tool("uv")
    assert found == str(local_bin / "uv")
    assert os.path.isabs(found)
    assert str(local_bin) in augmented_path().split(os.pathsep)


def test_augmented_path_keeps_process_path_first(monkeypatch):
    monkeypatch.setenv("PATH", "/first:/second")
    parts = augmented_path().split(os.pathsep)
    assert parts[:2] == ["/first", "/second"]
    assert len(parts) == len(set(parts))
    assert "/usr/bin" in parts and "/bin" in parts


@pytest.mark.parametrize("kind", ["pipx", "uv", "brew", "pip"])
def test_upgrade_argv_is_absolute(kind, tmp_path, monkeypatch):
    local_bin = _fake_home_with_tools(tmp_path, monkeypatch, "pipx", "uv", "brew")
    src = InstallSource(kind=kind, prefix=str(tmp_path / "p"),
                        python=str(tmp_path / "p" / "bin" / "python"),
                        origin="index", upgradable=True)
    argv = upgrade_argv(src)
    assert argv is not None
    assert os.path.isabs(argv[0])
    expected = {
        "pipx": [str(local_bin / "pipx"), "upgrade", "aipager"],
        "uv": [str(local_bin / "uv"), "tool", "upgrade", "aipager", "--refresh"],
        "brew": [str(local_bin / "brew"), "upgrade", "aipager"],
        "pip": [src.python, "-m", "pip", "install", "--upgrade", "aipager"],
    }[kind]
    assert argv == expected


def test_upgrade_argv_none_when_installer_missing(tmp_path, monkeypatch):
    _fake_home_with_tools(tmp_path, monkeypatch)
    monkeypatch.setattr(install_source, "resolve_tool", lambda name: None)
    src = InstallSource(kind="pipx", prefix="/p", python="/p/bin/python",
                        upgradable=True)
    assert upgrade_argv(src) is None


def test_upgrade_env_pins_pipx_home_to_running_venv(tmp_path):
    prefix = _pipx_prefix(tmp_path)
    src = _detect(prefix)
    env = upgrade_env(src)
    assert env["PIPX_HOME"] == str(tmp_path / "pipx")
    assert env["PATH"] == augmented_path()


def test_upgrade_env_pins_uv_tool_dir(tmp_path):
    prefix = _uv_prefix(tmp_path)
    env = upgrade_env(_detect(prefix))
    assert env["UV_TOOL_DIR"] == str(tmp_path / "uv" / "tools")


def test_spawn_env_drops_bot_and_claude_tokens(monkeypatch):
    for key in ("CLAUDE_TG_BOT_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.setenv(key, "secret-value-" + key)
    monkeypatch.setenv("KEEP_ME", "1")
    env = spawn_env()
    for key in ("CLAUDE_TG_BOT_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        assert key not in env
    assert env["KEEP_ME"] == "1"


def test_uninstall_argv_table(tmp_path, monkeypatch):
    monkeypatch.setattr(install_source, "resolve_tool", lambda n: f"/abs/{n}")
    assert uninstall_argv("uv") == ["/abs/uv", "tool", "uninstall", "aipager"]
    assert uninstall_argv("pipx") == ["/abs/pipx", "uninstall", "aipager"]
    assert uninstall_argv("brew") == ["/abs/brew", "uninstall", "aipager"]
    assert uninstall_argv("pip") is None
    assert uninstall_argv(None) is None


def test_extra_install_argv_table(monkeypatch):
    monkeypatch.setattr(install_source, "resolve_tool", lambda n: f"/abs/{n}")
    assert extra_install_argv("uv", "voice") == [
        "/abs/uv", "tool", "install", "--reinstall", "aipager[voice]"]
    assert extra_install_argv("pipx", "voice") == [
        "/abs/pipx", "install", "--force", "aipager[voice]"]
    fallback = [sys.executable, "-m", "pip", "install", "--upgrade",
                "faster-whisper>=1.0"]
    assert extra_install_argv("brew", "voice") == fallback
    assert extra_install_argv(None, "voice") == fallback
    assert extra_install_argv(None, "telepathy") is None


def test_extra_install_argv_falls_back_to_bare_name(monkeypatch):
    monkeypatch.setattr(install_source, "resolve_tool", lambda n: None)
    assert extra_install_argv("uv", "voice")[0] == "uv"


def test_reinstall_hints():
    assert "uv tool install" in reinstall_hint("uv")
    assert "pipx install" in reinstall_hint("pipx")
    assert "brew reinstall" in reinstall_hint("brew")
    assert reinstall_hint("pip").startswith(sys.executable)
    assert "pip install" in reinstall_hint(None)


def test_installer_kind_is_none_for_refused(monkeypatch):
    monkeypatch.setattr(install_source, "detect_install_source",
                        lambda: InstallSource(kind="editable", prefix="/x",
                                              python="/x/python"))
    assert install_source.installer_kind() is None
    monkeypatch.setattr(install_source, "detect_install_source",
                        lambda: InstallSource(kind="pipx", prefix="/x",
                                              python="/x/python", upgradable=True))
    assert install_source.installer_kind() == "pipx"
