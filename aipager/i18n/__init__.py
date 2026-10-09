"""aipager's own text in the reader's language (the multilingual design).

``Msg``/``Plural`` hold a key with its English inline; a ``Tr`` renders
them for one language and one output kind. English is the source and the
fallback; nothing here ever touches text Claude reads.

The hook process must never import this package (it has to start fast,
and its import closure is checked by tests/test_i18n_hook_isolation.py):
modules in that closure use :mod:`aipager.clockfmt` for the clock.
"""

from __future__ import annotations

from aipager.i18n.core import NEVER_TRANSLATE, Msg, Plural, Safe, Tr, get

__all__ = ["NEVER_TRANSLATE", "Msg", "Plural", "Safe", "Tr", "get"]
