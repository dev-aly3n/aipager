"""Black-box QA rows for 8.30 (bound the long-run outbound volume).

The plumbing — the injected monotonic+wall clock, the gated PTB double, the
virtual event loop and the rich-path HTTP recorder — is the developer's
harness in ``tests/integration/long-run-volume-budget/conftest.py``. It is
LOADED here by file path under a private module name and its fixtures are
re-exported, rather than copied: one harness, so a fix to it reaches both
suites. Loading it this way does not collect that directory's rows.

Its rules hold here too: nothing in ``asyncio`` is patched, time moves only
through injected clocks or a module's OWN ``time`` reference, no real
Telegram, socket, ``claude`` or ``dtach``, and every path is the isolated
one ``tests/conftest.py`` sets up.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HARNESS = (Path(__file__).resolve().parent.parent
            / "long-run-volume-budget" / "conftest.py")
_NAME = "_lrv_qa_harness"

if _NAME in sys.modules:
    _h = sys.modules[_NAME]
else:
    _spec = importlib.util.spec_from_file_location(_NAME, _HARNESS)
    _h = importlib.util.module_from_spec(_spec)
    sys.modules[_NAME] = _h
    _spec.loader.exec_module(_h)

CHAT = _h.CHAT
FloodClock = _h.FloodClock
GatedBot = _h._GatedBot

# Fixtures, re-exported by name so pytest discovers them in this conftest.
run_async = _h.run_async
flood_clock = _h.flood_clock
limiter = _h.limiter
gated_bot = _h.gated_bot
rich_http = _h.rich_http
vloop = _h.vloop
vlimiter = _h.vlimiter
vbot = _h.vbot
