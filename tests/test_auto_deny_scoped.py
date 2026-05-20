"""Phase C: tool auto-deny re-sourced from scope + policy."""

from __future__ import annotations

from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import Status, TrackedSession


def _bot(mk_bot, *, scope_deny=(), member_role="user", member_deny=()):
    scopes = [Scope(
        chat_id=-100, kind="group", label="dev",
        members=(Member(id=2, label="bob", role=member_role,
                        deny_tools=tuple(member_deny)),),
        deny_tools=tuple(scope_deny),
    )]
    bot = mk_bot(scopes=scopes)
    bot.policy = load_policy()
    return bot


def _sess():
    s = TrackedSession(name="claude-x__g100", label="x", status=Status.BUSY)
    s.scope_chat_id = -100
    s.last_driver_user_id = 2
    return s


def test_scope_deny_blocks_user(mk_bot):
    bot = _bot(mk_bot, scope_deny=["Bash"])
    assert bot._tool_auto_denied(_sess(), "Bash") is True
    assert bot._tool_auto_denied(_sess(), "Read") is False


def test_per_user_deny_adds(mk_bot):
    bot = _bot(mk_bot, member_deny=["WebFetch"])
    assert bot._tool_auto_denied(_sess(), "WebFetch") is True


def test_owner_bypasses(mk_bot):
    bot = _bot(mk_bot, scope_deny=["Bash"], member_role="owner")
    assert bot._tool_auto_denied(_sess(), "Bash") is False


def test_admin_bypasses(mk_bot):
    bot = _bot(mk_bot, scope_deny=["Bash"], member_role="admin")
    assert bot._tool_auto_denied(_sess(), "Bash") is False


def test_legacy_mode_no_scope(mk_bot):
    bot = mk_bot()  # scopes=None
    assert bot._tool_auto_denied(_sess(), "Bash") is False


def test_unknown_driver_uses_scope_deny(mk_bot):
    bot = _bot(mk_bot, scope_deny=["Bash"])
    s = _sess()
    s.last_driver_user_id = 999  # not a member
    assert bot._tool_auto_denied(s, "Bash") is True
