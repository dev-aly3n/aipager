"""A sentence that is its English everywhere and can still be rendered
in another language.

The Mini App's validation helpers (:mod:`aipager.miniapp.launch`) and
reason constants (:mod:`aipager.miniapp.sessions`) are shared with the
chat, whose code tests ``if err:``, compares with ``==``, glues them into
f-strings and runs them through ``html.escape``. Their values therefore
stay plain ``str`` equal to today's English. A :class:`Phrase` is such a
value: a ``str`` holding the English rendering of a message with its
parameters, which also remembers the message and the parameters, so a
surface that knows its reader's language renders it there with
:func:`say`. A bare :class:`~aipager.i18n.Msg` (also a ``str``) works the
same way for a sentence without parameters.

Imports only :mod:`aipager.i18n`, so nothing in the hook's import closure
reaches it.
"""

from __future__ import annotations

from aipager import i18n
from aipager.i18n import Msg, Plural

__all__ = ["Phrase", "say"]


class Phrase(str):
    """*msg* (a :class:`Msg` or :class:`Plural`) with its parameters,
    equal to its English rendering. A Plural takes its count as ``n``."""

    def __new__(cls, msg, /, **params):
        if isinstance(msg, Plural):
            if "n" not in params:
                raise TypeError(f"{msg.key}: n=")   # a Plural's count
            rest = {k: v for k, v in params.items() if k != "n"}
            english = i18n.get("en").n(msg, params["n"], kind="text", **rest)
        elif isinstance(msg, Msg):
            english = i18n.get("en").text(msg, **params)
        else:
            raise TypeError(type(msg).__name__)   # only a Msg or a Plural
        obj = super().__new__(cls, english)
        obj.msg = msg
        obj.params = dict(params)
        return obj

    # Immutable like any str: a copy is the same Phrase.
    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


def say(tr, value):
    """*value* for *tr*'s reader, as plain text: unchanged when *tr* is
    None (English callers), a :class:`Phrase` or :class:`Msg` rendered in
    *tr*'s language, anything else as ``str(value)`` (text that comes
    from elsewhere passes through as it is). ``None`` stays ``None``."""
    if tr is None or value is None:
        return value
    if isinstance(value, Phrase):
        if isinstance(value.msg, Plural):
            rest = {k: v for k, v in value.params.items() if k != "n"}
            return tr.n(value.msg, value.params["n"], kind="text", **rest)
        return tr.text(value.msg, **value.params)
    if isinstance(value, Msg):
        return tr.text(value)
    return str(value)
