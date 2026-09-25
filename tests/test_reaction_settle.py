"""An idle prompt's 👀 must not blink into 👍: reactions settle first.

Reported 2026-09-25: sending a prompt to an idle session showed 👀 and
then, a moment later, 👍. Every reaction now waits a short settle window;
within it only the latest reaction for a message is sent.
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


def test_eyes_then_thumbs_within_the_window_sends_only_thumbs(owner):
    async def run():
        assert await reactions.set_reaction(owner, 5, 10, reactions.HANDED_OFF)
        assert await reactions.set_reaction(owner, 5, 10, reactions.TAKEN)
        assert owner._app.bot.calls == []
        await _settle()
        assert owner._app.bot.calls == [(5, 10, reactions.TAKEN)]

    asyncio.run(run())


def test_a_lone_reaction_is_sent_after_the_window(owner):
    async def run():
        await reactions.set_reaction(owner, 5, 11, reactions.HANDED_OFF)
        await asyncio.sleep(0.01)   # well inside the 0.05 s window
        assert owner._app.bot.calls == []
        await _settle()
        assert owner._app.bot.calls == [(5, 11, reactions.HANDED_OFF)]

    asyncio.run(run())


def test_a_later_edge_after_the_window_is_its_own_call(owner):
    async def run():
        await reactions.set_reaction(owner, 5, 12, reactions.HANDED_OFF)
        await _settle()
        await reactions.set_reaction(owner, 5, 12, reactions.TAKEN)
        await _settle()
        assert owner._app.bot.calls == [(5, 12, reactions.HANDED_OFF),
                                        (5, 12, reactions.TAKEN)]

    asyncio.run(run())


def test_messages_settle_independently(owner):
    async def run():
        await reactions.set_reaction(owner, 5, 13, reactions.HANDED_OFF)
        await reactions.set_reaction(owner, 5, 14, reactions.HANDED_OFF)
        await reactions.set_reaction(owner, 5, 13, reactions.TAKEN)
        await _settle()
        assert sorted(owner._app.bot.calls) == [(5, 13, reactions.TAKEN),
                                                (5, 14, reactions.HANDED_OFF)]

    asyncio.run(run())


def test_a_backward_edge_in_the_window_changes_nothing(owner):
    async def run():
        await reactions.set_reaction(owner, 5, 15, reactions.TAKEN)
        assert not await reactions.set_reaction(owner, 5, 15, reactions.HANDED_OFF)
        await _settle()
        assert owner._app.bot.calls == [(5, 15, reactions.TAKEN)]
        assert owner._app.bot.calls.count((5, 15, reactions.TAKEN)) == 1

    asyncio.run(run())


def test_no_pending_task_is_left_behind(owner):
    async def run():
        await reactions.set_reaction(owner, 5, 16, reactions.HANDED_OFF)
        await _settle()
        assert reactions.ledger_of(owner).pending == {}

    asyncio.run(run())


def test_the_real_window_is_about_a_second():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(reactions))
    value = next(node.value.value for node in tree.body
                 if isinstance(node, ast.Assign)
                 and getattr(node.targets[0], "id", "") == "REACTION_SETTLE_SECONDS")
    assert 0.5 <= value <= 1.5
