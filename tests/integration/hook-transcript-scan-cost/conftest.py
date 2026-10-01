"""Fixtures for the hook-transcript-scan-cost black-box tests.

The oracle is ``_oracle_enforce_166a3f6.py``: main 166a3f6's
``aipager/dtach/enforce.py`` copied byte for byte (behind a 3-line
banner) with ``git show``. It is loaded as its own module, so its scans
look up ITS ``_iter_lines_reversed`` and can be re-chunked with
``monkeypatch`` independently of the code under test.

The root conftest already redirects ``policy_snapshot.snapshot_path``
and ``reply_context_path`` under ``tmp_path``; the oracle bound
``reply_context_path`` at its own import, so it is re-pointed here to
the same redirected function ``enforce`` uses.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from aipager.dtach import enforce

_HERE = Path(__file__).resolve().parent


def _kit():
    name = "_scancost_kit"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, _HERE / "_scan_kit.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


@pytest.fixture
def old(monkeypatch):
    """The frozen 166a3f6 enforce module, with its reply path aligned."""
    mod = _kit().oracle()
    monkeypatch.setattr(mod, "reply_context_path", enforce.reply_context_path)
    return mod


@pytest.fixture
def snap_file(tmp_path):
    """Where the root conftest put this session's policy snapshot."""
    from aipager import policy_snapshot as ps

    def _path(session):
        p = Path(ps.snapshot_path(session))
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    return _path
