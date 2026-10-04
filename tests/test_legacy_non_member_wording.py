"""Legacy team.yaml mode's reply to a non-member names only what exists
(roadmap 8.94e): no "Review pending users" menu entry, no file to edit, no
em dash. The same holds for every user-facing string in the package."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import aipager
from aipager.team import Role, Rules, Team, User as TeamUser


def test_legacy_non_member_reply_says_what_to_do(mk_bot, mk_update,
                                                 run_async, monkeypatch):
    bot = mk_bot()
    bot.team = Team(group_id=-100,
                    users={1: TeamUser(id=1, label="admin", role=Role.ADMIN)},
                    rules=Rules(deny_tools=[]))
    update = mk_update("hi", user_id=99999)
    update.effective_user.username = "stranger"
    update.effective_user.first_name = "Some"
    update.effective_user.last_name = "One"
    update.effective_message = update.message
    monkeypatch.setattr("aipager.bot.auth.record_pending_user", MagicMock())
    monkeypatch.setattr("aipager.bot.auth.remember_unauthorized",
                        lambda uid: False)
    assert run_async(bot._authorize(update)) is False
    text = update.message.reply_text.await_args.args[0]
    assert text == (
        "🚫 You're not on this bot's allow-list. Ask the operator to add "
        "your Telegram user ID (99999) with `aipager config`.")


def test_no_source_names_a_menu_entry_that_does_not_exist():
    root = Path(aipager.__file__).parent
    stale = ("Review pending users", "Change a user's role")
    hits = [f"{p.relative_to(root)}: {s}"
            for p in root.rglob("*.py")
            for s in stale if s in p.read_text(encoding="utf-8")]
    assert hits == []


def test_doctor_names_the_real_menu_path_for_a_role(monkeypatch, tmp_path):
    """The no-admin hint names the wizard's real entries (``aipager config``
    moves a team.yaml install over before its menu)."""
    from aipager import doctor
    from aipager.wizard import edit_menu
    src = Path(edit_menu.__file__).read_text(encoding="utf-8")
    assert 'questionary.Choice("Edit a member"' in src
    assert 'questionary.Choice("Set role"' in src
    p = tmp_path / "team.yaml"
    p.write_text("mode: team\ngroup_id: -100123\nusers:\n"
                 "  - {id: 1, label: a, role: developer}\n")
    monkeypatch.setattr("aipager.team.TEAM_CONFIG_PATH", p)
    monkeypatch.setattr("aipager.config.CHAT_ID", "-100123")
    r = doctor.check_team()
    assert r.fix == ("promote a user to admin with `aipager config` → "
                     "Edit a member → Set role")
