"""Load ``tools/i18n.py`` by path (a plain ``import tools.i18n`` would race
the ``tools/i18n/`` directory beside it). One copy per test session."""

from __future__ import annotations

import importlib.util
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
