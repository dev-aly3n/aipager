"""Load ``tools/i18n.py`` by path (a plain ``import tools.i18n`` would race
the ``tools/i18n/`` directory beside it). One copy per test session."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_NAME = "aipager_i18n_tool"


def load():
    mod = sys.modules.get(_NAME)
    if mod is None:
        spec = importlib.util.spec_from_file_location(_NAME, ROOT / "tools" / "i18n.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[_NAME] = mod
        spec.loader.exec_module(mod)
    return mod


def isolated_env(tmp_path: Path, pythonpath: Path = ROOT) -> dict:
    """The environment for a child process: a throwaway HOME, instance
    dir and TMPDIR under ``tmp_path``, and no CLAUDE/AIPAGER/XDG variable
    from the operator's session, so the child never reads or writes the
    real ``~/.config/aipager`` or ``~/.claude``."""
    home = tmp_path / "home"
    instance = tmp_path / "instance"
    home.mkdir(exist_ok=True)
    instance.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE", "AIPAGER", "XDG_"))}
    env.update(PYTHONPATH=str(pythonpath), HOME=str(home),
               AIPAGER_INSTANCE_DIR=str(instance), TMPDIR=str(tmp_path))
    return env
