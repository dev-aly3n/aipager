"""``aipager.bot.reactions.ReactionLedger`` — one key per message, bounded."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aipager.bot import reactions
from aipager.bot.reactions import ReactionLedger, held_entries, set_reaction


def _owner():
    owner = MagicMock()
    owner._reaction_ledger = None
    owner._app.bot.set_message_reaction = AsyncMock()
    return owner


def test_one_message_is_one_key_however_the_chat_is_spelled(run_async):
    """The hand-off reads the chat as an int (``effective_chat.id``); a held
    message's later edge reads ``resolve_chat_id(sess)``, which can be the
    configured chat as a string. Both must hit the same ledger entry."""
    owner = _owner()
    run_async(set_reaction(owner, -3003, 1, reactions.TAKEN))
    run_async(set_reaction(owner, "-3003", "1", reactions.TAKEN))
    run_async(set_reaction(owner, "-3003", 1, reactions.HANDED_OFF))
    assert owner._app.bot.set_message_reaction.await_count == 1


def test_the_ledger_stays_bounded():
    ledger = ReactionLedger(cap=2)
    for msg_id in range(5):
        ledger.lock(1, msg_id)
        assert ledger.claim(1, msg_id, reactions.HANDED_OFF)
    assert len(ledger._state) == 2
    assert set(ledger._locks) <= set(ledger._state)
    assert ledger.current(1, 4) == reactions.HANDED_OFF
    assert ledger.current(1, 0) is None


def test_held_entries_skip_messages_without_an_id():
    queue = [("a", 0, 1.0, "", None), ("b", None, 1.0, "", None),
             ("c", 5, 1.0, "", None), ("bad",)]
    assert held_entries(queue) == [{"msg_id": 5}]


def test_a_message_with_no_id_or_chat_is_never_reacted_to(run_async):
    owner = _owner()
    assert run_async(set_reaction(owner, -1, None, reactions.TAKEN)) is False
    assert run_async(set_reaction(owner, None, 1, reactions.TAKEN)) is False
    assert owner._app.bot.set_message_reaction.await_count == 0


def test_message_id_zero_is_no_message(run_async):
    """``/new <prompt>`` and the picker queue their prompt as message 0."""
    owner = _owner()
    assert run_async(set_reaction(owner, -1, 0, reactions.TAKEN)) is False
    assert owner._app.bot.set_message_reaction.await_count == 0


def test_id_less_entries_do_not_use_up_the_bulk_cap(run_async):
    owner = _owner()
    entries = ([{"msg_id": i} for i in range(1, reactions.BULK_CAP + 1)]
               + [{"msg_id": 0}, {"msg_id": None}, {}])
    run_async(reactions.mark_all(owner, entries, reactions.NOT_DELIVERED, -1,
                                 cap=reactions.BULK_CAP))
    marked = sorted(c.args[1] for c in
                    owner._app.bot.set_message_reaction.await_args_list)
    assert marked == list(range(1, reactions.BULK_CAP + 1))


def test_an_owner_without_a_real_ledger_gets_one(run_async):
    """A mock owner hands back a mock for any attribute; the ledger must
    still be real, or dedup silently stops working."""
    owner = MagicMock()
    owner._app.bot.set_message_reaction = AsyncMock()
    run_async(set_reaction(owner, -1, 1, reactions.TAKEN))
    run_async(set_reaction(owner, -1, 1, reactions.TAKEN))
    assert owner._app.bot.set_message_reaction.await_count == 1


def test_the_cap_keeps_the_newest_messages_by_id(run_async):
    """A teardown hands over held messages, notes and queued targets in
    no particular order; the cap keeps the newest by message id."""
    owner = _owner()
    ids = [5, 12, 1, 9, 3, 11, 7, 2, 10, 4, 8, 6]
    run_async(reactions.mark_all(owner, [{"msg_id": i} for i in ids],
                                 reactions.NOT_DELIVERED, -1, cap=4))
    marked = sorted(c.args[1] for c in
                    owner._app.bot.set_message_reaction.await_args_list)
    assert marked == [9, 10, 11, 12]
