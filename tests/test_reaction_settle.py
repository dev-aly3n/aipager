"""An idle prompt's 👀 must not blink into 👍, and 👍 must not lag.

2026-09-25: an idle prompt showed 👀 then, a moment later, 👍. The first
fix delayed every reaction, which put 👍 a second behind the busy card
(2026-09-26). Now only 👀 waits; anything else is sent at once and
cancels a 👀 still waiting.
"""
import asyncio

import pytest

from aipager.bot import reactions


class _Bot:
    def __init__(self):
        self.calls = []

    async def set_message_reaction(self, chat_id, msg_id, emoji, **kw):
        self.calls.append((chat_id, msg_id, emoji))


class _Owner:
    def __init__(self):
        self._app = type("A", (), {})()
        self._app.bot = _Bot()


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(reactions, "REACTION_SETTLE_SECONDS", 0.05)
    return _Owner()


async def _settle():
    await asyncio.sleep(0.12)


def test_thumbs_right_after_eyes_sends_only_thumbs_and_at_once(owner):
    async def run():
        assert await reactions.set_reaction(owner, 5, 10, reactions.HANDED_OFF)
        assert await reactions.set_reaction(owner, 5, 10, reactions.TAKEN)
        # 👍 went out immediately, no settle delay behind the busy card.
        assert owner._app.bot.calls == [(5, 10, reactions.TAKEN)]
        await _settle()
        # The waiting 👀 was cancelled: never sent, before or after.
        assert owner._app.bot.calls == [(5, 10, reactions.TAKEN)]
    asyncio.run(run())


def test_a_lone_eyes_waits_then_goes_out(owner):
    async def run():
        await reactions.set_reaction(owner, 5, 11, reactions.HANDED_OFF)
        await asyncio.sleep(0.01)   # well inside the 0.05 s pause
        assert owner._app.bot.calls == []
        await _settle()
        assert owner._app.bot.calls == [(5, 11, reactions.HANDED_OFF)]
    asyncio.run(run())


def test_thumbs_after_the_pause_is_its_own_immediate_call(owner):
    async def run():
        await reactions.set_reaction(owner, 5, 12, reactions.HANDED_OFF)
        await _settle()
        await reactions.set_reaction(owner, 5, 12, reactions.TAKEN)
        assert owner._app.bot.calls == [(5, 12, reactions.HANDED_OFF),
                                        (5, 12, reactions.TAKEN)]
    asyncio.run(run())


def test_other_reactions_never_wait(owner):
    async def run():
        await reactions.set_reaction(owner, 5, 13, reactions.ACK)
        await reactions.set_reaction(owner, 5, 14, reactions.NOT_DELIVERED)
        assert owner._app.bot.calls == [(5, 13, reactions.ACK),
                                        (5, 14, reactions.NOT_DELIVERED)]
    asyncio.run(run())


def test_messages_are_independent(owner):
    async def run():
        await reactions.set_reaction(owner, 5, 15, reactions.HANDED_OFF)
        await reactions.set_reaction(owner, 5, 16, reactions.HANDED_OFF)
        await reactions.set_reaction(owner, 5, 15, reactions.TAKEN)
        await _settle()
        assert sorted(owner._app.bot.calls) == [(5, 15, reactions.TAKEN),
                                                (5, 16, reactions.HANDED_OFF)]
    asyncio.run(run())


def test_a_backward_edge_changes_nothing(owner):
    async def run():
        await reactions.set_reaction(owner, 5, 17, reactions.TAKEN)
        assert not await reactions.set_reaction(owner, 5, 17, reactions.HANDED_OFF)
        await _settle()
        assert owner._app.bot.calls == [(5, 17, reactions.TAKEN)]
    asyncio.run(run())


def test_no_pending_task_is_left_behind(owner):
    async def run():
        await reactions.set_reaction(owner, 5, 18, reactions.HANDED_OFF)
        await _settle()
        await reactions.set_reaction(owner, 5, 19, reactions.HANDED_OFF)
        await reactions.set_reaction(owner, 5, 19, reactions.TAKEN)
        assert reactions.ledger_of(owner).pending == {}
        await asyncio.sleep(0)      # let the cancelled 👀 finish cancelling
    asyncio.run(run())


def test_thumbs_cancels_the_waiting_eyes_task(owner):
    """The waiting 👀 is cancelled outright, not merely skipped later."""
    async def run():
        await reactions.set_reaction(owner, 5, 20, reactions.HANDED_OFF)
        task = reactions.ledger_of(owner).pending[(5, 20)]
        await reactions.set_reaction(owner, 5, 20, reactions.TAKEN)
        await asyncio.sleep(0)
        assert task.cancelled()
    asyncio.run(run())


def test_a_late_eyes_is_skipped_if_the_message_moved_on_anyway(owner):
    """Second line of defence: even if the waiting 👀 was not cancelled,
    it checks the message is still at 👀 before sending."""
    async def run():
        await reactions.set_reaction(owner, 5, 21, reactions.HANDED_OFF)
        # The message moves on without going through set_reaction's cancel.
        reactions.ledger_of(owner).claim(5, 21, reactions.TAKEN)
        await _settle()
        assert (5, 21, reactions.HANDED_OFF) not in owner._app.bot.calls
    asyncio.run(run())


def test_the_real_window_is_about_a_second():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(reactions))
    value = next(node.value.value for node in tree.body
                 if isinstance(node, ast.Assign)
                 and getattr(node.targets[0], "id", "") == "REACTION_SETTLE_SECONDS")
    assert 0.5 <= value <= 1.5
