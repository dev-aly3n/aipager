"""``/usage`` (roadmap 8.131): who may see it, what a group sees, every
text form, and the command's place among aipager's own commands.

Plan limits are the whole account's and the token totals cover every
chat's sessions, so only owner and admins see them; a reply in a group is
read by every member there, so it carries that chat's tokens only."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import config, i18n, usage_store
from aipager.bot import usage_cmd
from aipager.bot.usage_cmd import UsageAccess, fmt_tokens, usage_access, usage_html, usage_view
from aipager.claude_bootstrap import SessionLogin
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.statusline_file import Window
from aipager.usage_store import Reading, Snapshot

OWNER, ADMIN, ADMIN_B, USER, READER, STRANGER = 111, 555, 666, 777, 888, 999999
GROUP_A, GROUP_B = -100, -200
NOW = 1_794_000_000.0
EN = i18n.get("en")
ZERO = (0, 0, 0, 0)


@pytest.fixture(autouse=True)
def _utc_clock(monkeypatch):
    monkeypatch.setattr(usage_store, "localtime", time.gmtime)


@pytest.fixture
def bot(mk_bot):
    b = mk_bot(scopes=[
        Scope(chat_id=OWNER, kind="dm", label="owner", members=(
            Member(id=OWNER, label="alice", role="owner"),)),
        Scope(chat_id=GROUP_A, kind="group", label="team", members=(
            Member(id=OWNER, label="alice", role="owner"),
            Member(id=ADMIN, label="dave", role="admin"),
            Member(id=ADMIN_B, label="erin", role="user"),
            Member(id=USER, label="bob", role="user"),
            Member(id=READER, label="carol", role="read_only"))),
        Scope(chat_id=GROUP_B, kind="group", label="other", members=(
            Member(id=OWNER, label="alice", role="owner"),
            Member(id=ADMIN_B, label="erin", role="admin"))),
    ])
    b.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    return b


# ---- who may see it ----------------------------------------------------------------

def test_the_owner_sees_every_chat_and_the_plan(bot):
    assert usage_access(bot, OWNER, OWNER) == UsageAccess(None, "all", True)
    assert usage_access(bot, OWNER, GROUP_B) == UsageAccess(None, "all", True)


def test_another_admin_sees_only_the_chats_they_manage(bot):
    assert usage_access(bot, ADMIN, GROUP_A) == UsageAccess(frozenset({GROUP_A}), "managed",
                                                            True)
    assert usage_access(bot, ADMIN_B, GROUP_B) == UsageAccess(frozenset({GROUP_B}),
                                                              "managed", True)


@pytest.mark.parametrize("user,chat", [(USER, GROUP_A), (READER, GROUP_A),
                                       (STRANGER, GROUP_A), (ADMIN_B, GROUP_A),
                                       (ADMIN, GROUP_B), (None, GROUP_A), (True, GROUP_A)],
                         ids=["user", "read-only", "stranger", "user-here-admin-elsewhere",
                              "admin-elsewhere", "no-user", "bool-user"])
def test_everyone_else_is_refused(bot, user, chat):
    assert usage_access(bot, user, chat) is None


def test_a_group_narrows_to_that_chat_and_no_plan(bot):
    assert usage_access(bot, OWNER, GROUP_A, for_group=True) == UsageAccess(
        frozenset({GROUP_A}), "chat", False)
    assert usage_access(bot, USER, GROUP_A, for_group=True) is None


def test_personal_mode_is_the_operator_only(mk_bot, monkeypatch):
    monkeypatch.setattr(config, "CHAT_ID", str(OWNER))
    b = mk_bot()
    assert usage_access(b, OWNER, OWNER) == UsageAccess(None, "all", True)
    assert usage_access(b, STRANGER, STRANGER) is None
    # One chat in personal mode: a group there is all of it.
    assert usage_access(b, OWNER, -5, for_group=True) == UsageAccess(None, "chat", False)


# ---- text ---------------------------------------------------------------------------

def _reading(windows, *, seen_at=NOW, kind="saved", host="", session="frontend", key=None):
    return Reading(key=key or f"{kind}|{host}|/h", kind=kind, host=host, session=session,
                   seen_at=seen_at, windows=windows)


def _snap(plan=(), today=ZERO, week=ZERO, days=None, models=(), behind=False):
    days = days if days is not None else tuple((f"d{i}", ZERO) for i in range(7))
    return Snapshot(plan=tuple(plan), today=today, week=week, days=days, models=tuple(models),
                    behind=behind)


ALL = UsageAccess(None, "all", True)


def _html(snap, access=ALL, now=NOW):
    return usage_html(snap, access, EN, now)


def test_window_lines_and_the_three_reset_forms():
    reading = _reading({
        "seven_day": Window(83.4, int(NOW) + 2 * 86400 + 5 * 3600 + 59),
        "five_hour": Window(7, int(NOW) + 3 * 3600 + 10 * 60 + 30),
        "spend_limit": Window(40, int(NOW) + 24 * 60 + 1),
    })
    text = _html(_snap([reading]))
    lines = text.split("\n")
    at_5h = time.strftime("%H:%M", time.gmtime(int(NOW) + 3 * 3600 + 10 * 60 + 30))
    at_spend = time.strftime("%H:%M", time.gmtime(int(NOW) + 24 * 60 + 1))
    # Order: five_hour, seven_day, spend_limit, each with its reset line.
    i = lines.index("5-hour window: 7% used")
    assert lines[i + 1] == f"Resets in 3 h 10 min, at {at_5h}"
    assert lines[i + 2] == "This week (all models): 83% used"
    assert lines[i + 3] == "Resets in 2 d 5 h"       # no clock beyond 24 hours
    assert lines[i + 4] == "Spend limit: 40% used"
    assert lines[i + 5] == f"Resets in 25 min, at {at_spend}"   # rounded up


def test_under_a_minute_left_still_says_one_minute():
    text = _html(_snap([_reading({"five_hour": Window(99, int(NOW) + 5)})]))
    assert "Resets in 1 min, at " in text


@pytest.mark.parametrize("left", [0, -1, -86400])
def test_a_passed_reset_shows_reset_never_the_old_percentage(left):
    reading = _reading({"seven_day": Window(97, int(NOW) + left),
                        "five_hour": Window(12, int(NOW) + 600)})
    text = _html(_snap([reading]))
    assert "This week (all models): reset since the last reading" in text
    assert "97" not in text
    view = usage_view(_snap([reading]), ALL, EN, NOW)
    week = [w for w in view["plan"]["logins"][0]["windows"] if w["id"] == "seven_day"][0]
    assert week["state"] == "reset" and "pct" not in week and week["reset"] == ""
    five = [w for w in view["plan"]["logins"][0]["windows"] if w["id"] == "five_hour"][0]
    assert five["state"] == "ok" and five["pct"] == 12


@pytest.mark.parametrize("age,expected", [
    (0, "(just now)"), (59, "(just now)"), (60, "(1 minute ago)"), (3599, "(59 minutes ago)"),
    (3600, "(1 hour ago)"), (7200, "(2 hours ago)"), (86400, "(1 day ago)"),
    (3 * 86400, "(3 days ago)"),
])
def test_the_four_freshness_forms(age, expected):
    seen = NOW - age
    text = _html(_snap([_reading({"seven_day": Window(1, int(NOW) + 7 * 86400)},
                                 seen_at=seen)]))
    clock = time.strftime("%H:%M", time.gmtime(seen))
    assert f"As of {clock} {expected}, from session frontend." in text


def test_no_reading_says_when_plan_limits_show():
    text = _html(_snap())
    assert ("Plan limits show here after Claude answers in a session signed in with a "
            "Claude subscription.") in text


def test_one_login_has_no_name_two_have_theirs():
    one = _reading({"seven_day": Window(1, int(NOW) + 9000)})
    view = usage_view(_snap([one]), ALL, EN, NOW)
    assert view["plan"]["logins"][0]["name"] is None
    two = _reading({"seven_day": Window(2, int(NOW) + 9000)}, kind="gateway",
                   host="gw.example", session="backend")
    view = usage_view(_snap([two, one]), ALL, EN, NOW)
    assert [lg["name"] for lg in view["plan"]["logins"]] == [
        "Gateway gw.example", "Claude sign-in saved on this computer"]
    text = _html(_snap([two, one]))
    assert "from session backend." in text and "from session frontend." in text


def test_token_lines_and_the_empty_and_behind_forms():
    snap = _snap(today=(1200, 45, 30_000, 2100), week=(5_400_000, 9_000, 120_000, 2100),
                 behind=True)
    text = _html(snap)
    assert "Today: 1.2k in, 45 out, 30.0k cache read, 2.1k cache write" in text
    assert "Last 7 days: 5.4M in, 9.0k out, 120k cache read, 2.1k cache write" in text
    assert "Still reading earlier transcripts, so these totals may grow." in text
    empty = _html(_snap())
    assert "No tokens recorded yet." in empty and "Today:" not in empty


@pytest.mark.parametrize("n,out", [
    (0, "0"), (999, "999"), (1000, "1.0k"), (1234, "1.2k"), (15_000, "15.0k"),
    (99_949, "99.9k"), (99_950, "100k"), (150_000, "150k"), (999_499, "999k"),
    (999_500, "1.0M"), (1_234_567, "1.2M"), (9_949_999, "9.9M"), (15_000_000, "15M"),
    (1_234_000_000, "1,234M"),
])
def test_fmt_tokens(n, out):
    assert fmt_tokens(n, EN) == out


def test_fmt_tokens_uses_the_languages_digits():
    assert fmt_tokens(1234, i18n.get("fa")) == "۱٫۲k"


@pytest.mark.parametrize("title,expected", [("all", "Tokens"),
                                            ("managed", "Tokens in the chats you manage"),
                                            ("chat", "Tokens in this chat")])
def test_the_token_title_follows_the_access(title, expected):
    text = _html(_snap(), UsageAccess(None, title, title != "chat"))
    assert f"<b>{expected}</b>" in text


def test_a_group_reply_has_no_plan_lines_and_the_group_note():
    reading = _reading({"seven_day": Window(83, int(NOW) + 9000)})
    text = _html(_snap([reading]), UsageAccess(frozenset({GROUP_A}), "chat", False))
    assert "used" not in text and "<b>Plan limits</b>" not in text
    assert "Plan limits are not shown in a group." in text
    assert "This week" not in text


def test_no_dollar_or_cost_anywhere_even_for_a_gpt_gateway_model():
    reading = _reading({"spend_limit": Window(40, int(NOW) + 9000)}, kind="gateway",
                       host="gw.example")
    snap = _snap([reading], today=(10, 5, 0, 0), week=(10, 5, 0, 0),
                 models=[("gpt-5", (10, 5, 0, 0))])
    text = _html(snap)
    view = usage_view(snap, ALL, EN, NOW)
    flat = repr(view)
    assert "$" not in text and "$" not in flat
    assert "cost" not in flat and "usd" not in flat.lower()


def test_the_view_has_seven_day_rows_newest_first():
    days = tuple((f"d{i}", (i, 0, 0, 0)) for i in range(7))
    view = usage_view(_snap(days=days, week=(21, 0, 0, 0)), ALL, EN, NOW)
    tokens = view["tokens"]
    assert [d["label"] for d in tokens["days"]] == [
        "Today", "Yesterday", "2 days ago", "3 days ago", "4 days ago", "5 days ago",
        "6 days ago"]
    assert tokens["columns"] == ["", "In", "Out", "Cache read", "Cache write"]
    assert [r["label"] for r in tokens["rows"]] == ["Today", "Last 7 days"]


def test_an_rtl_reply_renders():
    reading = _reading({"seven_day": Window(83, int(NOW) + 9000)})
    text = usage_html(_snap([reading], today=(1, 1, 1, 1), week=(1, 1, 1, 1)), ALL,
                      i18n.get("fa"), NOW)
    assert "۸۳" in text


# ---- the command --------------------------------------------------------------------

def _update(user_id, chat_id, text="/usage"):
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.chat = MagicMock()
    update.message.chat.id = chat_id
    update.message.reply_text = AsyncMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.language_code = None
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_chat.type = "private" if chat_id > 0 else "group"
    update.effective_message = update.message
    return update


def _reply(update) -> str:
    calls = update.message.reply_text.await_args_list
    assert len(calls) == 1
    return str(calls[0].args[0])


@pytest.fixture
def stored(bot, tmp_path):
    """A reading, and tokens in two chats."""
    usage_store.note_plan("frontend", {"seven_day": Window(42, int(time.time()) + 2 * 86400)},
                          SessionLogin("saved", "", "/home/alice/.claude"))
    import datetime as dt
    stamp = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    for chat, label, n in ((GROUP_A, "frontend", 1000), (GROUP_B, "backend", 2000)):
        sess = bot.registry.get_or_create(f"claude-{label}")
        sess.scope_chat_id = chat
        path = tmp_path / f"{label}.jsonl"
        path.write_text(
            '{"type": "assistant", "timestamp": "%s", "message": {"id": "m-%s", '
            '"model": "claude-haiku", "usage": {"input_tokens": %d, "output_tokens": 1}}}\n'
            % (stamp, label, n))
        sess.transcript_path = str(path)


def test_the_owner_in_their_dm_gets_plan_and_every_chats_tokens(bot, stored, run_async):
    update = _update(OWNER, OWNER)
    run_async(usage_cmd.handle_usage_cmd(bot, update, MagicMock()))
    text = _reply(update)
    assert "This week (all models): 42% used" in text
    assert "<b>Tokens</b>" in text and "Today: 3.0k in" in text
    assert "$" not in text


def test_an_admin_in_a_group_gets_that_chats_tokens_only(bot, stored, run_async):
    update = _update(ADMIN, GROUP_A)
    run_async(usage_cmd.handle_usage_cmd(bot, update, MagicMock()))
    text = _reply(update)
    assert "Tokens in this chat" in text and "Today: 1.0k in" in text
    assert "used" not in text
    assert "Plan limits are not shown in a group." in text


@pytest.mark.parametrize("user", [USER, READER], ids=["user", "read-only"])
def test_a_member_who_is_not_an_admin_is_refused(bot, stored, run_async, user):
    update = _update(user, GROUP_A)
    run_async(usage_cmd.handle_usage_cmd(bot, update, MagicMock()))
    assert _reply(update) == "🚫 Only an admin can see usage."


def test_an_unauthorized_sender_gets_nothing_from_usage(bot, stored, run_async):
    update = _update(STRANGER, GROUP_A)
    run_async(usage_cmd.handle_usage_cmd(bot, update, MagicMock()))
    assert not any("Usage" in str(c.args[0]) or "Tokens" in str(c.args[0])
                   for c in update.message.reply_text.await_args_list)


# ---- its place among aipager's commands ----------------------------------------------

def test_usage_is_one_of_aipagers_own_commands():
    from aipager.bot import group_intake, lifecycle, new_flow
    from aipager.dtach import inject
    names = [name for name, _msg in lifecycle._CMD_DESCRIPTIONS]
    assert names.index("usage") == names.index("help") - 1
    assert "usage" in group_intake.OWN_COMMANDS
    assert "usage" in inject._RESERVED
    assert "usage" in new_flow._LOOKING_COMMANDS


def test_the_mini_apps_rename_check_reserves_usage():
    from aipager.miniapp.static import _app
    assert '"usage": true' in _app.APP_JS


def test_claude_codes_own_usage_still_reaches_a_session():
    """``/<label> /usage`` still types Claude Code's /usage, whose note
    must keep being passed over: it stays a terminal-local command."""
    from aipager import policy_snapshot
    assert "/usage" in policy_snapshot.LOCAL_COMMANDS


def test_help_lists_usage_for_admins():
    from aipager.bot import handlers
    assert "/usage (plan limits and tokens)" in handlers.help_text(EN)
