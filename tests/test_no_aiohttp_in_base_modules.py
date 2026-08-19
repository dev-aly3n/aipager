"""Regression test for "boot-path modules never import aiohttp at import
time" — originally rev-iter1-004.

**The reason changed, the test did not.** It was written when aiohttp
was an opt-in extra, so a stray module-level ``import aiohttp`` would
make a base install crash outright. aiohttp is a base dependency now, so
that specific crash is gone — but the property is still worth holding,
for two reasons that outlive the packaging decision:

1. **Import cost.** aiohttp is the single largest dependency in the
   tree (~7.6 MB, plus multidict/yarl/frozenlist/attrs). ``aipager
   status``, ``aipager-hook`` and ``aipager-statusline`` run cold on
   every invocation — the hook scripts on *every Claude Code event*,
   against a <5 ms budget. Paying an aiohttp import there to print one
   line is exactly the kind of regression nobody notices until the
   whole session feels sluggish.
2. **Graceful degradation.** An install can still be missing aiohttp
   (interrupted upgrade, hand-pruned venv). Boot-path modules importing
   it at module level would turn that into a crash instead of the
   ``MiniAppUnavailable`` warning path that already handles it.

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
