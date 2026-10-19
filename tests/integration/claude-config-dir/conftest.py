"""Fixtures for the black-box tests of "aipager follows where Claude Code
keeps its config" (ship/claude-config-dir); helpers and the reasoning live
in ``_kit.py``."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest


def _load_kit():
    name = "_cfgdir_kit"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).resolve().parent / "_kit.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


kit = _load_kit()
CC, SCRUB = kit.CC, kit.SCRUB


@pytest.fixture
def cc(tmp_path, monkeypatch) -> CC:
    from aipager import claude_paths, daemon_secrets

    for var in SCRUB:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(daemon_secrets, "_recovered", {})

    home = Path(claude_paths.DEFAULT_GLOBAL_DIR)
    dot_claude = Path(claude_paths.DEFAULT_CONFIG_HOME)
    assert tmp_path in home.parents, "conftest must redirect the home folder"
    dot_claude.mkdir(parents=True, exist_ok=True)

    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    for name in ("aipager-hook", "aipager-statusline"):
        p = bin_dir / name
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    x = tmp_path / "elsewhere" / "cfg"
    x.mkdir(parents=True)
    work = tmp_path / "work" / "proj"
    work.mkdir(parents=True)
    return CC(tmp=tmp_path, home=home, dot_claude=dot_claude,
              managed=Path(claude_paths.MANAGED_SETTINGS_DIR),
              daemon_env=Path(daemon_secrets.DAEMON_ENV_PATH),
              x=x, bin=bin_dir, work=work)
