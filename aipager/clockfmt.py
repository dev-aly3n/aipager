"""The elapsed clock: ``m:ss`` under an hour, ``h:mm:ss`` after.

Every surface that shows how long something has been running (the busy
card's status line, agent and shell rows, "Finished", ``/status``, the
Mini App) formats it here, so the bot and the app read alike. The Mini
App's script carries a line-for-line port (``clockText`` in
``miniapp/static/_app.py``), checked against this one by a test.

``unit`` is the busy card's elapsed unit (``flood_policy.elapsed_unit``):
``"s"`` shows true seconds; ``"m"`` and ``"h"`` (a turn older than 10
minutes whose card refreshes at most every 30 s) floor the value to a
multiple of :data:`STEP_SECONDS`, so the clock moves in steps that match
the card's refresh and never looks frozen or stale. Minutes never read as
seconds: 23 minutes is ``23:00``, never ``0:23``.

Standard library only: ``aipager.bg_shells`` is in the hook process's
import closure and formats settled shell rows with this, and that closure
must never import ``aipager.i18n``.

Pure: no I/O.
"""

from __future__ import annotations

import math

__all__ = ["STEP_SECONDS", "elapsed"]

#: The step of a clock shown at unit "m" or "h".
STEP_SECONDS = 30


def elapsed(seconds, unit: str = "s") -> str:
    """``0:45``, ``4:10``, ``23:30``, ``1:23:00``. Anything invalid,
    non-finite or negative is 0; fractions are dropped (never rounded
    up). An unknown unit is treated as ``"s"``."""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        value = 0.0
    if not math.isfinite(value) or value < 0:
        value = 0.0
    total = int(value)
    if unit in ("m", "h"):
        total -= total % STEP_SECONDS
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
