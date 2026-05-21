"""E2E (fast, no Claude): /whoami reflects a real policy.yaml file.

Exercises the resolution ladder end-to-end through actual file parsing:
policy.yaml (custom role + safety) → load_policy → resolve_snapshot →
the /whoami reply's effective deny list. Runs under `-m e2e` but needs no
Claude auth, so `pytest tests/e2e/test_e2e_whoami.py -m e2e` is instant.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aipager import policy as _policy
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


def test_whoami_reads_real_policy_file(mk_bot, run_async, tmp_path, monkeypatch):
    # A real, hand-written policy.yaml with a custom role + safety tightening.
    pol_file = tmp_path / "policy.yaml"
    pol_file.write_text(
        "roles:\n"
        "  ranger:\n"
        "    deny_tools: [WebFetch]\n"
        "safety:\n"
        "  deny_bash_patterns: ['\\\\bcurl\\\\b']\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_policy, "POLICY_PATH", pol_file)
    monkeypatch.setattr(_policy, "POLICY_D_DIR", tmp_path / "policy.d")

    scope = Scope(
        chat_id=-100, kind="group", label="rangers",
        deny_tools=("Bash",),                      # scope-level deny
        members=(Member(id=7, label="ned", role="ranger",
                        deny_tools=("Write",)),),  # per-user deny
    )
    bot = mk_bot(scopes=[scope])
    bot.policy = _policy.load_policy(_policy.POLICY_PATH, _policy.POLICY_D_DIR)

    upd = _update(-100, 7)
    run_async(bot._handle_whoami(upd, MagicMock()))
    body = upd.message.reply_text.await_args.args[0]
    assert "ned" in body and "ranger" in body
    # Effective deny = role(WebFetch) ∪ scope(Bash) ∪ member(Write).
    for tool in ("WebFetch", "Bash", "Write"):
        assert tool in body, f"{tool} missing from effective deny: {body!r}"
