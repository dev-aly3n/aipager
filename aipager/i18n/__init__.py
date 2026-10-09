"""aipager's own text in the reader's language (the multilingual design).

``Msg``/``Plural`` hold a key with its English inline; a ``Tr`` renders
them for one language and one output kind. English is the source and the
fallback; nothing here ever touches text Claude reads.

The hook process must never import this package (it has to start fast,
and its import closure is checked by tests/test_i18n_hook_isolation.py):
modules in that closure use :mod:`aipager.clockfmt` for the clock.
"""

from __future__ import annotations

from aipager.i18n.core import (
    ISOLATE_COMMANDS,
    NEVER_TRANSLATE,
    Command,
    Msg,
    Plural,
    Safe,
    Span,
    Tr,
    get,
)
from aipager.i18n.resolve import (
    default_code,
    for_chat,
    for_person,
    for_sess,
    for_stranger,
    for_update,
    for_viewer,
)

__all__ = ["ISOLATE_COMMANDS", "NEVER_TRANSLATE", "Command", "Msg", "Plural", "Safe", "Span",
           "Tr", "default_code", "for_chat", "for_person", "for_sess", "for_stranger",
           "for_update", "for_viewer", "get"]
