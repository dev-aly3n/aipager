"""Text another pipeline owns, shown through the right Tr whether or not
it is a key yet (multilingual phase 3a).

A few texts phase 3a shows belong to modules it does not own (for example
``transport.NEEDS_ADMIN_REPLY``, or a refusal from
:mod:`aipager.miniapp.launch`). Each is a :class:`~aipager.i18n.Msg`, or
a :class:`~aipager.miniapp.phrase.Phrase` (a Msg with its parameters),
or plain English ``str`` from code that has no keys (a launch error).
Both keyed kinds are a ``str`` equal to their English, so passing one on
unrendered would silently show English: this helper is the one place
that tells them apart.
"""

from __future__ import annotations

from aipager.i18n import Msg, Plural, Safe
from aipager.miniapp.phrase import Phrase


def shown(tr, value, kind: str = "text"):
    """*value* for *tr*'s reader: a :class:`Msg` or a :class:`Phrase`
    rendered in *tr*'s language, anything else passed through unchanged.

    ``kind="text"``: a whole message (a reply, a pop-up), plain text.
    ``kind="html"``: a parameter of another HTML sentence, as a Safe
    without its own leading right-to-left mark (the sentence it goes into
    has one); a str passed through is then escaped like any value."""
    if isinstance(value, Phrase):
        msg, params = value.msg, dict(value.params)
    elif isinstance(value, Msg):
        msg, params = value, {}
    else:
        return value
    out_kind = "html" if kind == "html" else "text"
    if isinstance(msg, Plural):
        count = params.pop("n")
        out = tr.n(msg, count, kind=out_kind, **params)
    else:
        out = tr.html(msg, **params) if out_kind == "html" else tr.text(msg, **params)
    if kind == "html":
        return Safe(out.removeprefix(tr.mark))
    return out
