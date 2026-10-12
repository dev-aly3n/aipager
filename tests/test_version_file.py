"""``aipager/_version.py`` is the release the hooks stamp on their
datagrams; a release bump that forgets it would make every hook look
older (or newer) than the daemon."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_version_constant_equals_pyproject_version():
    from aipager import _version

    m = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(),
                  re.MULTILINE)
    assert m is not None
    assert _version.__version__ == m.group(1)


def test_version_module_imports_nothing():
    # The hook reads it at every start: it must stay a bare constant.
    tree = ast.parse((ROOT / "aipager" / "_version.py").read_text())
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
