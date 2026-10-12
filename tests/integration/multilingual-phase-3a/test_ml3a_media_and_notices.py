"""Black-box: files and voice notes, the session notices, a safety block
and an answer delivered late after a rate limit, each in the language its
chat chose (Persian and Russian)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, safety
from tests import i18n_ml2_bb as bb
from tests import i18n_surfaces_p3a as p3a
from tests.i18n_ml2_bb import CYRILLIC, PERSIAN

SCRIPT = {"fa": PERSIAN, "ru": CYRILLIC}


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_a_file_too_big_and_a_failed_download(world, code):
    from aipager.bot import handlers
    world.choose(bb.DM_A, code)
    world.mp.setattr(handlers, "_DOWNLOAD_BACKOFF_SECONDS", (0, 0, 0))
    big = p3a.msg(world.tg, None, chat=bb.DM_A)
    big.message.document = MagicMock(file_size=31 * 1024 * 1024, file_name="x1.py")
    world.run(world.bot._handle_file(big, MagicMock()))
    failing = p3a.msg(world.tg, None, chat=bb.DM_A)
    failing.message.document = MagicMock(file_size=10, file_name="x1.py")
    failing.message.document.get_file = AsyncMock(side_effect=RuntimeError("13"))
    world.run(world.bot._handle_file(failing, MagicMock()))
    assert len(world.tg.out) == 2
    assert all(SCRIPT[code].search(t) for t in world.tg.out)
    assert "x1.py" in world.tg.out[1]


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_a_voice_note_without_the_voice_extra(world, code):
    from aipager.bot import voice
    world.choose(bb.DM_A, code)
    world.session("x1")
    world.mp.setattr(voice, "is_available", lambda: False)
    note = p3a._voice_msg(world.tg)
    world.run(world.bot._handle_voice(note, MagicMock()))
    assert world.tg.out and SCRIPT[code].search(world.tg.out[0])


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_the_session_notices(world, code):
    from aipager.state import Status
    world.choose(bb.GROUP, code)
    x1 = world.session("x1", chat=bb.GROUP, status=Status.BUSY)
    world.bot._settle_card_text = AsyncMock(return_value=False)
    for kind, ctx in (("context_warning", {"context_pct": 92}),
                      ("prompt_not_taken", {"grace": 45.0}),
                      ("session_end", {"source": "logout"})):
        world.run(world.bot.notify(x1, kind, ctx))
    texts = world.tg.out                        # three notices and the Compact button
    assert len(texts) == 4 and all(SCRIPT[code].search(t) for t in texts)


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_a_safety_block(world, code):
    """The chat reads why in its language; Claude and the audit keep the
    English reason."""
    from aipager import audit
    from aipager.state import Status
    records = []
    world.mp.setattr(audit, "append", lambda **k: records.append(k))
    world.choose(bb.GROUP, code)
    x1 = world.session("x1", chat=bb.GROUP, status=Status.BUSY)
    reason = safety.path_violation("Write", {"file_path": "/srv/keep/a.txt"}, (),
                                   ("/srv/keep/**",))
    world.run(world.bot.notify(x1, "safety_blocked", {"tool": "Write", "reason": reason}))
    shown = world.tg.out[0]
    assert SCRIPT[code].search(shown) and reason not in shown
    assert "Write" in shown and "/srv/keep/**" in shown
    assert records[0]["reason"] == reason


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_an_answer_delivered_late_after_a_rate_limit(world, code):
    from aipager.bot import held as held_mod
    from aipager.bot import held_message
    from aipager.bot import notify as notify_mod
    world.choose(bb.DM_A, code)
    x1 = world.session("x1")
    sent = []

    async def _rich(chat, text, **kw):
        sent.append(text)
        return {"message_id": 1}
    world.mp.setattr(notify_mod, "send_rich_message", AsyncMock(side_effect=_rich))
    entry = SimpleNamespace(session=x1.name, label="x1", chat_id=bb.DM_A, key="k",
                            rich_text="**done**", plain_text="done", reply_to=None,
                            attempts=0, held_seconds=lambda: 600.0)
    world.mp.setattr(held_mod.HELD, "pending", lambda chat: [entry])
    world.mp.setattr(held_mod.HELD, "drop", lambda chat, key: None)
    world.bot._confirm_held = MagicMock()
    world.run(world.bot.flush_held_answers(x1))
    marker = sent[0].split("\n", 1)[0]
    # The marker heads an English answer: it reads left to right with it
    # (multilingual design 5.7, rule 5), in the chat's language.
    assert marker == held_message.late_marker_text(i18n.get(code).directed(False), 600)
    assert SCRIPT[code].search(marker) and sent[0].endswith("**done**")
