"""What a live reload (SIGUSR1) reaches besides the config itself (roadmap
8.80).

``LifecycleMixin.reload_team`` swaps the bot's scopes and policy. Before
this module, nothing else followed:

- the message handlers' chat gate and the per-chat command menus were
  built once at start, so a scope added by a reload never received plain
  messages, and a removed one kept its menu;
- a removed or demoted member's held messages still drained, and their
  running turn kept the rights it started with.

Everything here runs only after a SUCCESSFUL scope reload, and only for
what the reload actually changed, so a reload of an unchanged file
changes nothing (the operator's one-DM install sees no difference).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from aipager.state import (
    TURN_SENDER_MIXED, TURN_SENDER_TERMINAL, Status, TrackedSession,
)

if TYPE_CHECKING:  # pragma: no cover
    from aipager.policy import Policy
    from aipager.scope import Scope

log = logging.getLogger(__name__)

#: The reply under a held message a reload dropped. Plain words, no
#: dashes (UI text rule).
DROPPED_REPLY = "Dropped: {who} is no longer allowed to send here."
DROPPED_REPLY_MANY = ("Dropped {n} messages: {who} is no longer allowed "
                      "to send here.")


# ---- pure helpers -----------------------------------------------------------


def _scope_for(scopes, chat_id) -> Scope | None:
    for s in scopes or ():
        if s.chat_id == chat_id:
            return s
    return None


def _member_in(scope, user_id):
    if scope is None:
        return None
    for m in scope.members:
        if m.id == user_id:
            return m
    return None


def _is_user_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def can_prompt(scopes, policy, chat_id, user_id) -> bool:
    """Whether *user_id* may send a prompt in *chat_id* under *scopes* and
    *policy*: a member of that chat whose role has ``can_prompt``. The
    same rule as ``AuthMixin._can_prompt_user`` in scope mode."""
    member = _member_in(_scope_for(scopes, chat_id), user_id)
    if member is None:
        return False
    role = policy.get_role(member.role)
    return bool(role and role.can_prompt)


def sender_rules(scopes, policy, chat_id, user_id) -> list[dict]:
    """The rules a message of *user_id* in *chat_id* runs under, as notes
    for ``policy_snapshot.merge_snapshots``: the role, scope and member
    rules (``resolve_snapshot``), plus the floor when the sender may not
    prompt there (removed from the chat, or a role without
    ``can_prompt``). Pure."""
    from aipager.policy_snapshot import FLOOR_SNAPSHOT, resolve_snapshot

    scope = _scope_for(scopes, chat_id)
    member = _member_in(scope, user_id)
    if member is None:
        return [dict(FLOOR_SNAPSHOT)]
    role = policy.get_role(member.role)
    rules = [resolve_snapshot(role, scope, member)]
    if not (role and role.can_prompt):
        rules.append(dict(FLOOR_SNAPSHOT))
    return rules


def rules_narrowed(old_scopes, old_policy, new_scopes, new_policy,
                   chat_id, user_id) -> bool:
    """Whether the reload took something away from what *user_id*'s
    messages in *chat_id* may do: removed, demoted, new member or scope
    ``deny_tools``, or a ``policy.yaml`` edit to their role. Losing the
    right to prompt counts even when the rules read the same (a removed
    ``user`` falls to a floor that is as strict as their role). A change
    that only widens (a promotion) does not count: nothing in flight is
    ever touched for it. Pure."""
    from aipager.policy_snapshot import merge_snapshots, safety_rules

    if (can_prompt(old_scopes, old_policy, chat_id, user_id)
            and not can_prompt(new_scopes, new_policy, chat_id, user_id)):
        return True
    old = merge_snapshots(sender_rules(old_scopes, old_policy, chat_id,
                                       user_id))
    new = merge_snapshots(sender_rules(new_scopes, new_policy, chat_id,
                                       user_id))
    return safety_rules(merge_snapshots([old, new])) != safety_rules(old)


def _in_any(scopes, user_id) -> bool:
    return any(_member_in(s, user_id) is not None for s in scopes or ())


def narrowed_rules(old_scopes, old_policy, new_scopes, new_policy, chat_id,
                   user_id) -> list[dict] | None:
    """The rules *user_id*'s work in flight must now be held to, or
    ``None`` when the reload took nothing from them.

    With no known chat (an unstamped session aipager cannot place), only
    one thing is certain: someone who was in a scope and is in none now
    has no rights anywhere, so the floor."""
    from aipager.policy_snapshot import FLOOR_SNAPSHOT

    if not chat_id:
        if _in_any(old_scopes, user_id) and not _in_any(new_scopes, user_id):
            return [dict(FLOOR_SNAPSHOT)]
        return None
    if rules_narrowed(old_scopes, old_policy, new_scopes, new_policy,
                      chat_id, user_id):
        return sender_rules(new_scopes, new_policy, chat_id, user_id)
    return None


def turn_authors(sess: TrackedSession) -> set[int] | None:
    """The Telegram users whose messages the running turn (and the
    messages Claude Code holds in its queue for the next one) run as, or
    ``None`` for a turn typed in the terminal (see :func:`_terminal_joiners`
    for one a Telegram message has joined).

    - one known sender (``turn_sender_id`` an int): that sender;
    - several (TURN_SENDER_MIXED): the authors the hook reported with it
      (``turn_mixed_authors``);
    - plus the author of every message Claude Code queued
      (``queued_targets``): such a message runs later with no hook of its
      own, under the snapshot the running turn leaves behind."""
    running = sess.turn_sender_id
    if running == TURN_SENDER_TERMINAL:
        return None
    authors: set[int] = set()
    if _is_user_id(running):
        authors.add(running)
    elif running == TURN_SENDER_MIXED:
        authors.update(a for a in sess.turn_mixed_authors if _is_user_id(a))
    authors |= _queued_authors(sess)
    return authors


def _queued_authors(sess) -> set[int]:
    out: set[int] = set()
    for target in sess.queued_targets:
        uid = target.get("driver_user_id") if isinstance(target, dict) else None
        if _is_user_id(uid):
            out.add(uid)
    return out


def _chat_members(scopes, chat_id) -> set[int]:
    scope = _scope_for(scopes, chat_id)
    return {m.id for m in scope.members} if scope is not None else set()


def _terminal_joiners(old_scopes, old_policy, chat_id) -> set[int]:
    """Who may have joined a terminal turn in *chat_id* under the old
    config (``session_ops._sender_differs_from``): an owner (a role with
    ``bypass_safety``) from any scope, or the member of the session's own
    DM. A terminal turn a Telegram message joined is enforced with that
    message's rules (``joined_from_telegram``), so a joiner who was
    removed or demoted must not keep them."""
    out: set[int] = set()
    for scope in old_scopes or ():
        for m in scope.members:
            role = old_policy.get_role(m.role)
            if role is not None and getattr(role, "bypass_safety", False) is True:
                out.add(m.id)
            elif scope.chat_id == chat_id and scope.kind == "dm":
                out.add(m.id)
    return out


def _who(old_scopes, new_scopes, chat_id, user_id) -> str:
    """``@label`` for a sender, from the chat as it was, else as it is,
    else any scope; a plain phrase when nobody knows them."""
    for scopes in (old_scopes, new_scopes):
        member = _member_in(_scope_for(scopes, chat_id), user_id)
        if member is not None:
            return f"@{member.label}"
    for scopes in (old_scopes, new_scopes):
        for scope in scopes or ():
            member = _member_in(scope, user_id)
            if member is not None:
                return f"@{member.label}"
    return "this sender"


# ---- the reload's reach -----------------------------------------------------


def refresh_chat_gate(gate, scopes) -> tuple[set[int], set[int]]:
    """Make the message handlers' chat gate (the ``filters.Chat`` built at
    start) admit exactly *scopes*' chats. Returns ``(added, removed)``."""
    wanted = {s.chat_id for s in scopes}
    current = set(gate.chat_ids)
    added = wanted - current
    removed = current - wanted
    if added:
        gate.add_chat_ids(sorted(added))
    if removed:
        gate.remove_chat_ids(sorted(removed))
    return added, removed


async def reach_work_in_flight(bot: Any, old_scopes: list[Scope],
                               old_policy: Policy) -> None:
    """Apply a successful scope reload to the work already under way.

    - A held message (``pending_queue``) whose sender may no longer prompt
      in its session's chat is dropped: 🤷 on it and one reply per sender
      in that chat.
    - A running turn whose author lost rights (:func:`rules_narrowed`) is
      narrowed: its snapshot becomes the strictest of itself and the
      author's new rules (the floor for one who may no longer prompt).
      Never wider. A terminal turn only when a Telegram message joined it.
    - That author's policy notes still waiting for the hook are deleted,
      so the hook cannot hand their old rules to the text Claude already
      has (it then runs at the floor, or joins the narrowed turn).

    Never raises past one session: a failure is logged and the next
    session is still reached.
    """
    from aipager.bot.transport import resolve_chat_id_int

    new_scopes, new_policy = bot.scopes, bot.policy
    for sess in list(bot.registry.all_sessions().values()):
        if sess.status is Status.GONE:
            continue
        try:
            chat_id = resolve_chat_id_int(sess)
            await _drop_held(bot, sess, chat_id, old_scopes, new_scopes,
                             new_policy)
            _narrow_turn(sess, chat_id, old_scopes, old_policy, new_scopes,
                         new_policy)
        except Exception:
            log.warning("[%s] live reload could not reach this session's "
                        "work in flight", sess.label, exc_info=True)


async def _drop_held(bot, sess, chat_id, old_scopes, new_scopes,
                     new_policy) -> None:
    from aipager.bot import reactions
    from aipager.bot.transport import send_text

    if not chat_id:
        # No chat to judge the senders by: nothing is dropped on a guess
        # (the drain still re-resolves each sender when it sends).
        return
    kept: list = []
    dropped: dict[int, list] = {}
    for item in sess.pending_queue:
        uid = item[4] if len(item) > 4 else None
        if _is_user_id(uid) and not can_prompt(new_scopes, new_policy,
                                               chat_id, uid):
            dropped.setdefault(uid, []).append(item)
        else:
            kept.append(item)
    if not dropped:
        return
    # Taken out before anything is awaited, so a drain running meanwhile
    # can never send one of them.
    sess.pending_queue[:] = kept
    bot.registry.mark_dirty()
    for uid, items in dropped.items():
        who = _who(old_scopes, new_scopes, chat_id, uid)
        for item in items:
            log.info("[%s] Live reload dropped a held message from %s (%s "
                     "may no longer send in chat %s): %s", sess.label, uid,
                     who, chat_id, str(item[0])[:80])
        await bot._mark_not_delivered(sess, reactions.held_entries(items))
        if bot._app is None:
            continue
        text = (DROPPED_REPLY.format(who=who) if len(items) == 1 else
                DROPPED_REPLY_MANY.format(n=len(items), who=who))
        last_msg = items[-1][1]
        kwargs = {"chat_id": chat_id, "text": text}
        if last_msg:
            kwargs["reply_to_message_id"] = last_msg
        try:
            await send_text(bot._app.bot, **kwargs)
        except Exception:
            # The message may be gone; say it anyway, unthreaded.
            kwargs.pop("reply_to_message_id", None)
            try:
                await send_text(bot._app.bot, **kwargs)
            except Exception:
                log.debug("[%s] could not send the dropped-message reply",
                          sess.label, exc_info=True)


def _narrow_turn(sess, chat_id, old_scopes, old_policy, new_scopes,
                 new_policy) -> None:
    from aipager.policy_snapshot import (
        delete_notes, list_outstanding_notes, narrow_snapshot,
        note_driver_id, read_snapshot,
    )

    def _rules(uid):
        return narrowed_rules(old_scopes, old_policy, new_scopes, new_policy,
                              chat_id, uid)

    authors = turn_authors(sess)
    snap = read_snapshot(sess.name)
    snap = snap if isinstance(snap, dict) else {}
    joined = snap.get("joined_from_telegram") is True
    pure_terminal = snap.get("turn_origin") == "terminal" and not joined
    if authors is None:
        # A terminal turn is the operator's, and its snapshot is left
        # alone, unless a Telegram message joined it: the hook then
        # enforces that snapshot, which carries the joiner's rules.
        authors = (_terminal_joiners(old_scopes, old_policy, chat_id)
                   | _queued_authors(sess)) if joined else set()
    elif (sess.turn_sender_id is None and not pure_terminal
            and (sess.can_be_stopped() or sess.status is Status.UNKNOWN)):
        # Running, but whose turn it is is not known (it was already
        # running when the daemon restarted: ``turn_sender_id`` is never
        # persisted). Running includes waiting on a dialog and a job
        # whose background work goes on; a session loaded at start is
        # UNKNOWN until the monitor's first scan. Fail closed: anyone in
        # the chat, and any owner who may have joined, who lost rights
        # narrows it.
        authors |= _chat_members(old_scopes, chat_id) | _terminal_joiners(
            old_scopes, old_policy, chat_id)
    rules: list[dict] = []
    narrowed: list[int] = []
    for uid in sorted(authors):
        r = _rules(uid)
        if r is not None:
            rules += r
            narrowed.append(uid)
    if rules and narrow_snapshot(sess.name, rules):
        log.info("[%s] Live reload narrowed the running turn: %s lost "
                 "rights", sess.label, ", ".join(str(u) for u in narrowed))
    # Notes still waiting for the hook (text Claude already has): an
    # author who lost rights loses them, whatever the turn is. Deleted,
    # never rewritten: the hook may be consuming the same note right now,
    # and a rewrite could bring it back. Their text then matches no note:
    # joining a running turn it is held to the strictest of that turn
    # and the waiting notes (or the floor); starting a turn, to the
    # waiting notes' merge or the floor. That is never looser than the
    # author's new rules because a different sender's message is held
    # while another sender's note waits (the mixed-sender hold,
    # ``transport.mixed_sender_note_outstanding``, on every inbound path),
    # so the waiting notes are this author's own (deleted too) or carry
    # no sender (the floor). The queue drain does not consult that hold;
    # it runs at a turn end, when the hook has already taken the notes of
    # the turn's messages (it takes each one when Claude Code queues it).
    stale = [n for n in list_outstanding_notes(sess.name)
             if _is_user_id(note_driver_id(n))
             and _rules(note_driver_id(n)) is not None]
    if stale:
        delete_notes(sess.name, stale)
        log.info("[%s] Live reload deleted %d waiting policy note(s) of "
                 "%s", sess.label, len(stale), ", ".join(sorted(
                     {str(note_driver_id(n)) for n in stale})))
