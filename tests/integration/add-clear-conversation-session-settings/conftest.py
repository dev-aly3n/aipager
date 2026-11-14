"""Shared plumbing for the black-box Clear conversation tests (design.md
"Success criteria" 1-16). No assertions, no production logic.

Everything here follows entrypoints.md: the PTY seams are
``aipager.dtach.inject.is_alive`` / ``send_text_and_enter`` (never a real
dtach), Telegram is the mocked ``bot._app``, and the group fixture is the
one tests/test_group_attribution_senders.py uses (copied, not imported, as
this repo does between test directories).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aipager.dtach import inject
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status, TrackedSession

# ---- group mode (scope) ------------------------------------------------------

G = -1001234
DM = 11
ALICE, BOB, DAVE, ERIN = 11, 22, 44, 55
X1, X2 = "claude-x1__g1001234", "claude-x2__g1001234"
D1 = "claude-d1"


def group_scopes():
    return [
        Scope(chat_id=G, kind="group", label="team", members=(
            Member(id=ALICE, label="alice", role="owner"),
            Member(id=BOB, label="bob", role="user"),
            Member(id=DAVE, label="dave", role="admin"),
            Member(id=ERIN, label="erin", role="read_only"),
        ), root="/srv/team"),
        Scope(chat_id=DM, kind="dm", label="alice-dm", members=(
            Member(id=ALICE, label="alice", role="owner"),
        )),
    ]


def _group_registry() -> SessionRegistry:
    r = SessionRegistry()
    for name, label, chat in ((X1, "x1", G), (X2, "x2", G), (D1, "d1", DM)):
        s = TrackedSession(name=name, label=label, status=Status.IDLE)
        s.scope_chat_id = chat
        s.scope_kind = "group" if chat < 0 else "dm"
        if chat < 0:
            s.cwd = f"/srv/team/{label}"
        r._sessions[name] = s
    return r


@pytest.fixture
def gbot(mk_bot, monkeypatch):
    from aipager.policy import load_policy
    r = _group_registry()
    bot = mk_bot(r, scopes=group_scopes())
    bot.policy = load_policy()
    monkeypatch.setattr(inject, "is_alive",
                        AsyncMock(side_effect=lambda name: name in r._sessions))
    bot._inject_prompt = AsyncMock(return_value=True)
    bot._card_for_injected = AsyncMock()
    bot._react = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()
    bot.refresh_pinned = AsyncMock()
    bot._command_map = {"Clear": "/clear", "Compact": "/compact"}
    return bot


# ---- personal mode (DM, no scopes) ------------------------------------------

@pytest.fixture
def dm(mk_bot, monkeypatch):
    """One idle, alive session ``dev`` in a personal-mode bot; what Claude
    would receive is recorded on ``typed``."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.IDLE)
    bot.registry._sessions[sess.name] = sess
    alive = AsyncMock(return_value=True)
    typed = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.is_alive", alive)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", typed)
    monkeypatch.setattr("aipager.bot.session_ops._spawn", lambda coro: coro.close())
    bot._react = AsyncMock()
    bot._update_bot_commands = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._card_for_injected = AsyncMock()
    return SimpleNamespace(bot=bot, sess=sess, alive=alive, typed=typed)
