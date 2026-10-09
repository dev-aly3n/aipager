"""A Persian or Russian busy card near the ceiling still fits both limits:
8,800 characters and 32,768 UTF-8 bytes (G85). Persian and Russian letters
are two bytes each, so 8,800 of them stay under the byte limit; rows and
commentary full of emoji (four bytes each) are what make the byte limit
bind first, and the card must still fit."""

from __future__ import annotations

import pytest

from aipager import bg_shells, preferences
from aipager.bot import animation
from tests import i18n_surfaces


def _huge_session():
    """Hundreds of rows and sentences: far over both ceilings."""
    sess = i18n_surfaces.card_session()
    rows = []
    for i in range(400):
        rows.append(bg_shells.settled_row(f"npm run step-{i}", "completed", 90 + i))
        rows.append((f"🔑 Bash: echo {i} → Allowed", True))
        rows.append((f"🤖 Explore · {i} tool calls · 1:05", True))
    sess.tool_history = rows + list(sess.tool_history)
    sess.stream_commentary = [(i * 3, "پیامی طولانی از Claude " * 20) for i in range(60)]
    sess.active_subagents["a1"]["history_idx"] = len(sess.tool_history) - 3
    sess.bg_shells["t1"]["history_idx"] = len(sess.tool_history) - 2
    return sess


def _byte_heavy_session():
    """Rows and sentences of four-byte characters: 8,800 characters of them
    are about 35,000 bytes, so here the byte ceiling binds first."""
    sess = i18n_surfaces.card_session()
    # Small sections (two rows, one sentence): the fitter's steps are
    # fine enough that the character limit alone would keep too many bytes.
    sess.tool_history = [("🙂" * 150, True) for _ in range(400)]
    sess.stream_commentary = [(i * 2, "😀" * 150) for i in range(199)]
    sess.active_subagents = {}
    sess.bg_shells = {}
    return sess


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_a_long_card_fits_its_ceilings(code, mk_bot, run_async, monkeypatch):
    env = i18n_surfaces.Env(mk_bot, run_async, monkeypatch, code)
    env.store(i18n_surfaces.CHAT)
    assert preferences.get_ui_language(i18n_surfaces.CHAT) == code
    for build, final in ((_huge_session, False), (_huge_session, True),
                         (_byte_heavy_session, False), (_byte_heavy_session, True)):
        card, dropped = animation.build_stream_card_ex(build(), "Working", final=final)
        assert dropped
        assert len(card) <= animation._CARD_CHAR_BUDGET
        assert len(card.encode("utf-8")) <= animation._RICH_LIMIT
        # The status line, translated, is still the last line.
        assert card.rsplit("\n", 1)[-1].lstrip("\u200f").startswith(
            "✅" if final else "⏳")
