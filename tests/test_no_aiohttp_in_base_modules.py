"""Regression test for "the base install (no `[miniapp]` extra) never
imports aiohttp" — rev-iter1-004.

The property held at review time (verified by code inspection: grep
found no module-level ``import aiohttp`` outside ``aipager/miniapp/``),
but nothing protected it going forward. A future module-level ``import
aiohttp`` added to any daemon-boot-path module — most plausibly
``handlers.py`` or ``cli/daemon.py``, the two modules that reach into
``aipager.miniapp`` at all — would silently make a base install (no
``aiohttp`` present) crash at import time instead of degrading via
``MiniAppUnavailable``.

Uses the same ``builtins.__import__`` patch pattern already used in
``tests/test_miniapp_server.py::test_start_raises_unavailable_when_aiohttp_missing``,
but here the target is the *module's own top-level import statements*,
not a function-local import reached by calling something. Since these
modules are already cached in ``sys.modules`` by the time this test
runs (imported at collection time by other test files), a plain
``importlib.import_module()`` would just return the cached object
without re-running any import statement — ``importlib.reload()`` is
what forces the module body (and therefore every top-level ``import``)
to actually execute again under the patched ``__import__``.
"""

from __future__ import annotations

import builtins
import importlib

import pytest

from aipager import cli as cli_mod
from aipager.bot import handlers as handlers_mod
from aipager.bot import lifecycle as lifecycle_mod
from aipager.cli import daemon as daemon_mod


def _block_aiohttp(monkeypatch) -> None:
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "aiohttp" or name.startswith("aiohttp."):
            raise ImportError("simulated: aiohttp not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)


@pytest.mark.parametrize(
    "module",
    [daemon_mod, handlers_mod, lifecycle_mod, cli_mod],
    ids=["cli.daemon", "bot.handlers", "bot.lifecycle", "cli"],
)
def test_module_imports_cleanly_without_aiohttp(module, monkeypatch):
    _block_aiohttp(monkeypatch)
    # Must not raise ImportError — a module-level `import aiohttp`
    # anywhere in this module (or anything it imports at module level)
    # would fail here.
    importlib.reload(module)
