"""What people see about messages between Claude sessions.

A turn started by a message from another session shows who sent it on
its busy card once the hook has checked the sender (the ``peer_resolved``
datagram). When such a turn is halted by the safety rules, both chats
hear about it: the receiving session's (with the sender) and the sending
session's, so a task one session handed another never silently
disappears. Display only: nothing here decides any right.
"""

from __future__ import annotations

from aipager import peer_auth
from aipager.i18n import Msg

CARD_FROM = Msg(
    "card.peer.from", "📨 from **{session}** ({who})",
    note="Top line of a busy card whose turn another Claude session started "
         "with a message; {session} is that session's name, {who} who drove "
         "it (people's names, kept as they are)",
    eg={"session": "frontend", "who": "@alice"})
CARD_FROM_TERMINAL = Msg(
    "card.peer.from_terminal", "📨 from **{session}** (typed in its terminal)",
    note="As card.peer.from, when the sending turn was typed in that "
         "session's terminal", eg={"session": "frontend"})
CARD_FROM_PLAIN = Msg(
    "card.peer.from_plain", "📨 from **{session}**",
    note="As card.peer.from, when nobody is known to have driven it",
    eg={"session": "frontend"})
CARD_UNVERIFIED = Msg(
    "card.peer.unverified",
    "📨 from another session (not verified: no file changes, no web)",
    note="Top line of a busy card whose turn a message from another Claude "
         "session started, when aipager could not verify who sent it")
HALT_FROM = Msg(
    "notice.peer.halt_from", "This task came from <b>{session}</b>.",
    note="Under a safety stop: the turn was started by a message from "
         "another Claude session; {session} is its name",
    eg={"session": "frontend"})
HALT_UNVERIFIED = Msg(
    "notice.peer.halt_unverified",
    "This task came from another session aipager could not verify.",
    note="Under a safety stop: the turn was started by a message from "
         "another Claude session whose sender could not be verified")
HALT_NARROWED = Msg(
    "notice.peer.halt_narrowed",
    "A message from another session joined this turn and narrowed its rights.",
    note="Under a safety stop: a message from another Claude session joined "
         "the running turn, which then ran with that message's (smaller) "
         "rights")
SENDER_NOTICE = Msg(
    "notice.peer.sender",
    "⚠️ <b>{receiver}</b> did not carry out the message <b>{sender}</b> "
    "sent it: {reason}",
    note="In the chat of the session that sent a message to another Claude "
         "session: the receiving session was stopped by the safety rules; "
         "{reason} is why",
    eg={"receiver": "backend", "sender": "frontend",
        "reason": "SendMessage is in this role's deny_tools"})


def card_line(tr, peer_from) -> str:
    """The busy card's top line for a peer-started turn (Markdown), or ""."""
    if not isinstance(peer_from, dict):
        return ""
    status = peer_from.get("status")
    if status == "unverified":
        return tr.md(CARD_UNVERIFIED)
    if status != "verified":
        return ""
    sender = peer_auth.short_session(peer_from.get("sender_session"))
    if not sender:
        return tr.md(CARD_UNVERIFIED)
    driver = peer_from.get("driver") if isinstance(peer_from.get("driver"), dict) else {}
    if driver.get("class") == "terminal":
        return tr.md(CARD_FROM_TERMINAL, session=sender)
    labels = [x for x in driver.get("labels") or [] if isinstance(x, str) and x]
    if labels:
        return tr.md(CARD_FROM, session=sender, who=", ".join(labels[:3]))
    return tr.md(CARD_FROM_PLAIN, session=sender)


def halt_suffix(tr, peer_from, narrowed: bool) -> str:
    """The line under a safety stop of a peer-started or peer-narrowed
    turn (HTML), or ""."""
    parts = []
    if isinstance(peer_from, dict):
        status = peer_from.get("status")
        sender = peer_auth.short_session(peer_from.get("sender_session"))
        if status == "verified" and sender:
            parts.append(tr.html(HALT_FROM, session=sender))
        elif status in ("verified", "unverified", "pending"):
            parts.append(tr.html(HALT_UNVERIFIED))
    if narrowed:
        parts.append(tr.html(HALT_NARROWED))
    return "\n".join(parts)
