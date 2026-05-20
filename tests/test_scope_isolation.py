"""Phase G — cross-scope leakage suite (security model §3.8).

Layer-3 isolation: no session-enumerating surface may reveal (or let a
caller address) another scope's sessions. The acceptance gate for the
phase — a miss here is a direct cross-user leak.

Topology under test: two DM scopes (A=100, B=200) + one group (G=-300),
with the label "build" repeated across all three scopes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from telegram import BotCommandScopeChat

from aipager.bot.transport import resolve_chat_id
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status, TrackedSession

A, B, G = 100, 200, -300


def _scopes():
    return [
        Scope(chat_id=A, kind="dm", label="ana DM",
              members=(Member(id=A, label="ana", role="owner"),)),
        Scope(chat_id=B, kind="dm", label="ben DM",
              members=(Member(id=B, label="ben", role="user"),)),
        Scope(chat_id=G, kind="group", label="team",
              members=(Member(id=11, label="cara", role="user"),)),
    ]


def _sess(name, label, scope_chat_id, status=Status.IDLE):
    s = TrackedSession(name=name, label=label, status=status)
    s.scope_chat_id = scope_chat_id
    if status == Status.GONE:
        s.claude_session_id = f"uuid-{name}"
        s.gone_at = 1000.0
    return s


def _registry_with_shared_label(status=Status.IDLE):
    """One 'build' session per scope + a unique session each."""
    r = SessionRegistry()
    for chat, suffix, uniq in ((A, "d100", "ana-only"),
                               (B, "d200", "ben-only"),
                               (G, "g300", "team-only")):
        r._sessions[f"claude-build__{suffix}"] = _sess(
            f"claude-build__{suffix}", "build", chat, status)
        r._sessions[f"claude-{uniq}__{suffix}"] = _sess(
            f"claude-{uniq}__{suffix}", uniq, chat, status)
    return r


# ---- registry-level enumeration (the shared primitive) ------------------

def test_all_sessions_isolated_per_scope():
    r = _registry_with_shared_label()
    assert {s.label for s in r.all_sessions(A).values()} == {"build", "ana-only"}
    assert {s.label for s in r.all_sessions(B).values()} == {"build", "ben-only"}
    assert {s.label for s in r.all_sessions(G).values()} == {"build", "team-only"}


def test_find_by_label_cannot_cross_scopes():
    r = _registry_with_shared_label()
    in_a = r.find_by_label("build", A)
    in_b = r.find_by_label("build", B)
    assert in_a.name == "claude-build__d100"
    assert in_b.name == "claude-build__d200"
    assert in_a is not in_b
    # A's chat can never resolve B's unique session.
    assert r.find_by_label("ben-only", A) is None


# ---- /status ------------------------------------------------------------

def test_status_isolated_per_scope(mk_bot, mk_update, run_async, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    bot = mk_bot(_registry_with_shared_label(), scopes=_scopes())
    bot._authorize = AsyncMock(return_value=True)
    bot._read_status_file = MagicMock(return_value=None)

    update = mk_update("/status", chat_id=A)
    run_async(bot._handle_status(update, MagicMock()))
    body = update.message.reply_text.await_args.args[0]
    assert "ana-only" in body
    assert "ben-only" not in body
    assert "team-only" not in body


# ---- /resume picker -----------------------------------------------------

def test_resume_picker_isolated_per_scope(mk_bot):
    bot = mk_bot(_registry_with_shared_label(status=Status.GONE),
                 scopes=_scopes())
    text_b, kb_b = bot._render_resume_picker(page=0, scope_chat_id=B)
    cb = [btn.callback_data for row in kb_b.inline_keyboard for btn in row]
    assert "claude-build__d200:resume" in cb
    assert "claude-build__d100:resume" not in cb
    assert "ben-only" in text_b
    assert "ana-only" not in text_b and "team-only" not in text_b


# ---- per-scope command autocomplete -------------------------------------

def test_autocomplete_isolated_per_scope(mk_bot, run_async):
    bot = mk_bot(_registry_with_shared_label(), scopes=_scopes())
    bot._app.bot.set_my_commands = AsyncMock()
    run_async(bot._update_bot_commands())

    per_chat = {}
    for call in bot._app.bot.set_my_commands.await_args_list:
        scope_obj = call.kwargs["scope"]
        assert isinstance(scope_obj, BotCommandScopeChat)
        per_chat[scope_obj.chat_id] = {c.command for c in call.args[0]}
    assert "ana-only" in per_chat[A] and "ben-only" not in per_chat[A]
    assert "ben-only" in per_chat[B] and "team-only" not in per_chat[B]
    assert "team-only" in per_chat[G] and "ana-only" not in per_chat[G]
    # The shared "build" label appears in every scope (it's a real
    # session in each), but each maps to that scope's own session.
    assert all("build" in cmds for cmds in per_chat.values())


# ---- Layer 2 re-assertion: notify routing -------------------------------

def test_notify_routing_isolated(monkeypatch):
    monkeypatch.setattr("aipager.config.CHAT_ID", "999")
    a = _sess("claude-build__d100", "build", A)
    b = _sess("claude-build__d200", "build", B)
    assert resolve_chat_id(a) == A
    assert resolve_chat_id(b) == B
    assert resolve_chat_id(a) != resolve_chat_id(b)
