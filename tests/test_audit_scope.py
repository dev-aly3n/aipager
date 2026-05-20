"""Phase H — scope-attributed per-action audit + scope-labeled logs.

Auditing of inbound messages is a multi-scope observability feature
(gated on ``self.scopes is not None``). The autouse ``_isolate_audit_log``
fixture (conftest) points ``AUDIT_LOG_PATH`` at tmp, so reading it back
here observes exactly what ``_authorize`` wrote.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import aipager.audit as audit
from aipager.policy import load_policy
from aipager.scope import Member, Scope


def _scopes():
    return [
        Scope(chat_id=100, kind="dm", label="ana DM",
              members=(Member(id=100, label="ana", role="owner"),)),
        Scope(chat_id=-300, kind="group", label="dev-team",
              members=(Member(id=11, label="cara", role="user"),)),
    ]


def _bot(mk_bot):
    bot = mk_bot(scopes=_scopes())
    bot.policy = load_policy()
    return bot


def _update(chat_id, user_id, text="/status"):
    u = MagicMock()
    u.effective_chat = MagicMock()
    u.effective_chat.id = chat_id
    u.effective_user = MagicMock()
    u.effective_user.id = user_id
    u.effective_user.username = "x"
    u.effective_user.first_name = "X"
    u.effective_user.last_name = ""
    u.effective_message = MagicMock()
    u.effective_message.reply_text = AsyncMock()
    u.message = MagicMock()
    u.message.text = text
    return u


def _records():
    p = audit.AUDIT_LOG_PATH
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines()]


def test_allowed_action_audited_with_scope(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.bot.auth.record_pending_user",
                        lambda *a, **k: None)
    bot = _bot(mk_bot)
    assert run_async(bot._authorize(_update(100, 100, "/new x"))) is True
    recs = _records()
    assert len(recs) == 1
    r = recs[0]
    assert r["action"] == "/new"
    assert r["denied"] is False
    assert r["scope_label"] == "ana DM"
    assert r["scope_chat_id"] == 100
    assert r["username"] == "ana"
    assert r["bypass_safety"] is True  # owner role bypasses safety


def test_denied_non_member_audited_with_reason(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.bot.auth.record_pending_user",
                        lambda *a, **k: None)
    monkeypatch.setattr("aipager.bot.auth.remember_unauthorized",
                        lambda *a, **k: True)
    bot = _bot(mk_bot)
    assert run_async(bot._authorize(_update(100, 999, "/status"))) is False
    r = _records()[-1]
    assert r["denied"] is True
    assert r["reason"] == "not-a-member"
    assert r["scope_label"] == "ana DM"


def test_read_only_denial_audited(mk_bot, run_async):
    bot = mk_bot(scopes=[
        Scope(chat_id=-300, kind="group", label="dev-team",
              members=(Member(id=11, label="cara", role="read_only"),)),
    ])
    bot.policy = load_policy()
    assert run_async(bot._authorize(_update(-300, 11, "/new x"))) is False
    r = _records()[-1]
    assert r["denied"] is True
    assert r["reason"] == "read-only"


def test_audit_emits_scope_labeled_log(mk_bot, run_async, caplog):
    bot = _bot(mk_bot)
    with caplog.at_level(logging.INFO, logger="aipager.bot.auth"):
        run_async(bot._authorize(_update(100, 100, "/new x")))
    assert "[scope:ana DM]" in caplog.text
    assert "/new" in caplog.text


def test_legacy_mode_writes_no_per_message_audit(mk_bot, run_async):
    bot = mk_bot()  # scopes=None, team=None → legacy/personal
    assert run_async(bot._authorize(_update(100, 100, "/new x"))) is True
    assert _records() == []  # gate holds — nothing written
