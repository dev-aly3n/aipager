"""Text another pipeline owns, shown through the right Tr whether or not
it is a key yet (multilingual phase 3a).

A few texts phase 3a shows belong to modules it does not own (for example
``transport.NEEDS_ADMIN_REPLY``). On this branch they are plain English
``str``; once their owner turns them into a :class:`~aipager.i18n.Msg`,
the same call site renders them in the reader's language with no further
change here. A ``Msg`` is a ``str`` equal to its English, so passing one
on unrendered would silently show English: this helper is the one place
that tells the two apart.
"""

from __future__ import annotations

from aipager.i18n import Msg, Safe


def shown(tr, value, kind: str = "text"):
    """*value* for *tr*'s reader: a :class:`Msg` rendered in *tr*'s
    language, anything else passed through unchanged.

    ``kind="text"``: a whole message (a reply, a pop-up), plain text.
    ``kind="html"``: a parameter of another HTML sentence, as a Safe
    without its own leading right-to-left mark (the sentence it goes into
    has one); a str passed through is then escaped like any value."""
    if not isinstance(value, Msg):
        return value
    if kind == "html":
        return Safe(tr.html(value).removeprefix(tr.mark))
    return tr.text(value)
