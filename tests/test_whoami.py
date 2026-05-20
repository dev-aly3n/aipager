"""Phase H — /whoami reflects the caller's effective policy.

Verifies the resolution ladder end-to-end (scope ∪ role ∪ per-user)
via the same `resolve_snapshot` the safety hook uses.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aipager.policy import load_policy
from aipager.scope import Member, Scope


def _update(chat_id, user_id):
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
    u.message.text = "/whoami"
    u.message.reply_text = AsyncMock()
    return u


def _reply_from(bot, chat, user, run_async):
    upd = _update(chat, user)
    run_async(bot._handle_whoami(upd, MagicMock()))
    return upd.message.reply_text.await_args.args[0]


def test_whoami_user_effective_deny(mk_bot, run_async):
    scope = Scope(
        chat_id=-300, kind="group", label="dev-team",
        deny_tools=("WebFetch",),
        members=(Member(id=11, label="cara", role="user",
                        deny_tools=("Bash",)),),
    )
    bot = mk_bot(scopes=[scope])
    bot.policy = load_policy()
    body = _reply_from(bot, -300, 11, run_async)
    assert "cara" in body
    assert "role: <b>user</b>" in body
    assert "Bash" in body and "WebFetch" in body  # merged deny list
    assert "bypass_safety: no" in body


def test_whoami_owner_shows_bypass(mk_bot, run_async):
    scope = Scope(chat_id=100, kind="dm", label="ana DM",
                  members=(Member(id=100, label="ana", role="owner"),))
    bot = mk_bot(scopes=[scope])
    bot.policy = load_policy()
    body = _reply_from(bot, 100, 100, run_async)
    assert "role: <b>owner</b>" in body
    assert "bypass_safety: yes" in body


def test_whoami_non_member(mk_bot, run_async):
    # A true non-member is rejected by _authorize before reaching whoami;
    # stub auth to True so whoami's own not-a-member guard is exercised.
    scope = Scope(chat_id=100, kind="dm", label="ana DM",
                  members=(Member(id=100, label="ana", role="owner"),))
    bot = mk_bot(scopes=[scope])
    bot.policy = load_policy()
    bot._authorize = AsyncMock(return_value=True)
    body = _reply_from(bot, 100, 999, run_async)
    assert "not a member" in body.lower()


def test_whoami_personal_mode(mk_bot, run_async):
    bot = mk_bot()  # scopes=None, team=None
    body = _reply_from(bot, 100, 100, run_async)
    assert "personal mode" in body.lower()
