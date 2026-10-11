"""The elapsed clock, as the i18n package exposes it.

The implementation lives in :mod:`aipager.clockfmt` (standard library
only) because ``aipager.bg_shells`` needs it and is in the hook process's
import closure, which must never import this package. Use
``Tr.clock(seconds, unit)`` where the text has a language; it is the same
clock in that language's digits.
"""

from __future__ import annotations

from aipager.clockfmt import STEP_SECONDS, elapsed

__all__ = ["STEP_SECONDS", "elapsed"]
