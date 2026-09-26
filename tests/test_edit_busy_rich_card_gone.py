"""A card taken away while an edit waits for the lock is "gone", not a crash.

Seen live 2026-09-26: two turns ended within a second; 8.32's tool-less
path cleared ``busy_msg_id`` while the next turn's superseded-card close
was waiting to edit it, and ``int(None)`` raised a TypeError.
"""
import asyncio
from types import SimpleNamespace

from aipager.bot.animation import AnimationMixin


class _Bot(AnimationMixin):
    pass


def test_card_cleared_while_waiting_for_the_lock_returns_gone():
    async def run():
        sess = SimpleNamespace(busy_msg_id=4720, label="s")
        lock = asyncio.Lock()
        sess._stream_edit_lock = lock
        await lock.acquire()
        edit = asyncio.ensure_future(
            _Bot()._edit_busy_rich(sess, "Done", final=True))
        await asyncio.sleep(0)          # the edit is now waiting on the lock
        sess.busy_msg_id = None         # a racing finish took the card away
        lock.release()
        return await edit
    assert asyncio.run(run()) is None
