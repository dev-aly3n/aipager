"""The busy card's near-limit note (roadmap 8.131 follow-up): the shared
threshold, the store lookup by the session's own sign-in, the rows, and
who may see them.

The note shows plan usage, the whole account's, so it follows /usage's
rule for the card's one reader: only a private chat whose person may see
plan limits (owner or admin, the personal-mode operator), never a group,
never a plain or read-only member's chat, and only the reading of the
sign-in the session runs under."""

from __future__ import annotations

import math
import time
from pathlib import Path

import pytest

from aipager import config, i18n, usage_store
from aipager.bot import usage_cmd, usage_note
from aipager.claude_bootstrap import SessionLogin
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry
from aipager.statusline_file import Window
from aipager.team import Role as TeamRole
from aipager.team import Team, User

NOW = 1_794_000_000.0
EN = i18n.get("en")
FA = i18n.get("fa")
OWNER, ADMIN, USER, READER = 111, 555, 777, 888
GROUP = -100
A = SessionLogin("saved", "", "/home/alice/.claude")
B = SessionLogin("token", "env", "/home/alice/.claude-work")


@pytest.fixture(autouse=True)
def _gmtime(monkeypatch):
    # The seam the store and /usage read the clock zone through (never
    # `time` itself).
    monkeypatch.setattr(usage_store, "localtime", time.gmtime)


# ---- the threshold ------------------------------------------------------------------

@pytest.mark.parametrize("pct,level", [
    pytest.param(89.4, 0, id="89_4-none"),
    pytest.param(89.5, 1, id="89_5-near"),
    pytest.param(89.9, 1, id="89_9-near"),
    pytest.param(90, 1, id="90-near"),
    pytest.param(99.4, 1, id="99_4-near"),
    pytest.param(99.5, 2, id="99_5-reached"),
    pytest.param(100, 2, id="100-reached"),
    pytest.param(250, 2, id="250-reached"),
    pytest.param(1000, 2, id="1000-reached"),
    pytest.param(0, 0, id="0-none"),
    pytest.param(-5, 0, id="negative-none"),
    pytest.param(math.nan, 0, id="nan-none"),
    pytest.param(math.inf, 0, id="inf-none"),
    pytest.param(True, 0, id="bool-none"),
    pytest.param("92", 0, id="string-none"),
    pytest.param(None, 0, id="none-none"),
])
def test_limit_level_edges(pct, level):
    assert usage_store.limit_level(pct) == level


def test_the_threshold_constants():
    assert usage_store.NEAR_LIMIT_PERCENT == 90
    assert usage_store.LIMIT_REACHED_PERCENT == 100


# ---- the store lookup ----------------------------------------------------------------

def _week(pct, *, left=2 * 86400):
    return {"seven_day": Window(pct, int(NOW) + left)}


def test_plan_windows_by_own_key():
    usage_store.note_plan("frontend", _week(50), A, now=NOW - 60)
    usage_store.note_plan("backend", _week(99), B, now=NOW)   # newer, another sign-in
    assert usage_store.plan_windows(usage_store.plan_key(A), now=NOW) == _week(50)
    assert usage_store.plan_windows(usage_store.plan_key(B), now=NOW) == _week(99)


def test_plan_windows_returns_a_copy():
    usage_store.note_plan("frontend", _week(50), A, now=NOW)
    got = usage_store.plan_windows(usage_store.plan_key(A), now=NOW)
    got["five_hour"] = Window(99, int(NOW) + 60)
    assert usage_store.plan_windows(usage_store.plan_key(A), now=NOW) == _week(50)


def test_plan_windows_unknown_or_empty_key():
    usage_store.note_plan("adopted", _week(99), SessionLogin("unknown"), now=NOW)
    assert usage_store.snapshot(None, now=NOW).plan   # the unknown reading exists
    assert usage_store.plan_windows("", now=NOW) is None
    assert usage_store.plan_windows("unknown", now=NOW) is None
    assert usage_store.plan_windows(usage_store.plan_key(A), now=NOW) is None


def test_plan_windows_drops_stale_reading():
    seen = NOW - usage_store.KEEP_DAYS * 86400
    usage_store.note_plan("frontend", _week(95, left=30 * 86400), A, now=seen)
    assert usage_store.plan_windows(usage_store.plan_key(A), now=NOW) == _week(95, left=30 * 86400)
    assert usage_store.plan_windows(usage_store.plan_key(A), now=NOW + 1) is None


@pytest.mark.parametrize("home", [
    pytest.param("/home/alice/.claude", id="short"),
    pytest.param("/home/alice/" + "x" * 1100, id="hashed"),
])
def test_plan_key_matches_stored_key(home):
    login = SessionLogin("saved", "", home)
    usage_store.note_plan("frontend", _week(91), login, now=NOW)
    (reading,) = usage_store.snapshot(None, now=NOW).plan
    assert usage_store.plan_key(login) == reading.key
    assert len(reading.key) <= 1024
    assert usage_store.plan_windows(usage_store.plan_key(login), now=NOW) == _week(91)


def test_plan_key_of_unknown_is_unknown():
    assert usage_store.plan_key(SessionLogin("unknown")) == "unknown"


# ---- the rows -----------------------------------------------------------------------

def _w(pct, left):
    return Window(pct, int(NOW) + left)


@pytest.mark.parametrize("pct,want", [
    pytest.param(89.4, None, id="89_4"),
    pytest.param(89.9, "⚠️ Weekly limit almost reached: 90% used", id="89_9"),
    pytest.param(90, "⚠️ Weekly limit almost reached: 90% used", id="90"),
    pytest.param(100, "⛔ Weekly limit reached: 100% used", id="100"),
])
def test_rows_threshold_edges(pct, want):
    rows = usage_note.card_rows(EN, {"seven_day": _w(pct, 2 * 86400 + 5 * 3600)}, now=NOW)
    if want is None:
        assert rows == []
    else:
        assert rows == [f"{want} · Resets in 2 d 5 h"]


def test_spend_limit_over_100_reads_reached():
    (row,) = usage_note.card_rows(EN, {"spend_limit": _w(250, 25 * 60)}, now=NOW)
    assert row.startswith("⛔ Spend limit reached: 250% used · Resets in 25 min, at ")


def test_row_hidden_once_reset():
    assert usage_note.card_rows(EN, {"five_hour": Window(95, int(NOW))}, now=NOW) == []
    assert usage_note.card_rows(EN, {"five_hour": Window(95, int(NOW) - 1)}, now=NOW) == []
    (row,) = usage_note.card_rows(EN, {"five_hour": Window(95, int(NOW) + 60)}, now=NOW)
    assert row.startswith("⚠️ 5-hour limit almost reached: 95% used · Resets in 1 min")


def test_missing_window_not_shown():
    rows = usage_note.card_rows(EN, {"seven_day": _w(92, 86400 * 3)}, now=NOW)
    assert len(rows) == 1 and "Weekly limit" in rows[0]
    assert not any("5-hour" in r or "Spend" in r for r in rows)


def test_rows_below_threshold_are_left_out_beside_one_above():
    rows = usage_note.card_rows(EN, {"five_hour": _w(40, 3 * 3600),
                                     "seven_day": _w(92, 2 * 86400)}, now=NOW)
    assert len(rows) == 1 and rows[0].startswith("⚠️ Weekly limit almost reached: 92% used")


def test_rows_order_and_bound():
    windows = {"spend_limit": _w(95, 600), "seven_day": _w(95, 86400 * 2),
               "five_hour": _w(95, 3600 * 2)}
    rows = usage_note.card_rows(EN, windows, now=NOW)
    assert [r.split(":")[0] for r in rows] == [
        "⚠️ 5-hour limit almost reached", "⚠️ Weekly limit almost reached",
        "⚠️ Spend limit almost reached"]


def _expected_reset(tr, window, left):
    at = tr.num(time.strftime("%H:%M", time.gmtime(window.resets_at)))
    if left >= 86400:
        return tr.md(usage_cmd.RESETS_DAYS, days=2, hours=5)
    if left >= 3600:
        return tr.md(usage_cmd.RESETS_HOURS, hours=3, minutes=10, time=at)
    return tr.md(usage_cmd.RESETS_MINUTES, minutes=25, time=at)


@pytest.mark.parametrize("code", ["en", "fa"])
@pytest.mark.parametrize("left", [
    pytest.param(2 * 86400 + 5 * 3600, id="days"),
    pytest.param(3 * 3600 + 10 * 60, id="hours"),
    pytest.param(25 * 60, id="minutes"),
])
def test_reset_text_is_usage_reset_text(code, left):
    tr = i18n.get(code)
    window = _w(93, left)
    (row,) = usage_note.card_rows(tr, {"seven_day": window}, now=NOW)
    head, reset = row.split(" · ", 1)
    assert reset == usage_cmd.reset_text(window, NOW, tr, "md")
    # /usage's own sentence (its keys, its clock), for Markdown.
    assert reset == _expected_reset(tr, window, left)
    if code == "fa":
        assert "۹۳" in head and "93" not in row


def test_no_windows_no_rows():
    assert usage_note.card_rows(EN, None, now=NOW) == []
    assert usage_note.card_rows(EN, {}, now=NOW) == []


def test_reset_text_kinds():
    window = _w(50, 3 * 3600 + 10 * 60)
    for kind in ("html", "text", "md"):
        assert usage_cmd.reset_text(window, NOW, EN, kind).startswith("Resets in 3 h 10 min, at ")


# ---- who may see it -----------------------------------------------------------------

@pytest.fixture
def scoped(mk_bot):
    b = mk_bot(scopes=[
        Scope(chat_id=OWNER, kind="dm", label="owner", members=(
            Member(id=OWNER, label="alice", role="owner"),)),
        Scope(chat_id=ADMIN, kind="dm", label="dave", members=(
            Member(id=ADMIN, label="dave", role="admin"),)),
        Scope(chat_id=USER, kind="dm", label="bob", members=(
            Member(id=USER, label="bob", role="user"),)),
        Scope(chat_id=READER, kind="dm", label="carol", members=(
            Member(id=READER, label="carol", role="read_only"),)),
        Scope(chat_id=GROUP, kind="group", label="team", members=(
            Member(id=OWNER, label="alice", role="owner"),
            Member(id=USER, label="bob", role="user"))),
    ])
    b.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    return b


def _sess(chat=0, *, login=A, name="claude-frontend"):
    s = SessionRegistry().get_or_create(name)
    s.scope_chat_id = chat
    s.usage_plan_key = usage_store.plan_key(login) if login is not None else ""
    return s


@pytest.fixture
def near_a():
    usage_store.note_plan("frontend", _week(92), A, now=NOW)


def test_owner_dm_gets_windows(scoped, near_a):
    assert usage_note.windows_for(scoped, _sess(OWNER), now=NOW) == _week(92)


def test_admin_dm_gets_windows(scoped, near_a):
    assert usage_note.windows_for(scoped, _sess(ADMIN), now=NOW) == _week(92)


def test_plain_user_dm_gets_nothing(scoped, near_a):
    assert usage_note.windows_for(scoped, _sess(USER), now=NOW) is None


def test_read_only_dm_gets_nothing(scoped, near_a):
    assert usage_note.windows_for(scoped, _sess(READER), now=NOW) is None


def test_group_session_gets_nothing_even_for_owner(scoped, near_a):
    # The owner may see usage in the group (/usage there answers her with
    # this chat's tokens), yet every member reads the card.
    assert scoped._is_update_admin(OWNER, GROUP)
    assert usage_note.windows_for(scoped, _sess(GROUP), now=NOW) is None


def test_group_session_gets_nothing_even_when_its_chat_passes_access(scoped, near_a,
                                                                    monkeypatch):
    """The chat-id guard alone keeps a group out, whatever the access
    rule answers."""
    monkeypatch.setattr(usage_cmd, "usage_access",
                        lambda *a, **kw: usage_cmd.UsageAccess(None, "all", True))
    assert usage_note.windows_for(scoped, _sess(GROUP), now=NOW) is None
    assert usage_note.windows_for(scoped, _sess(OWNER), now=NOW) == _week(92)


@pytest.fixture
def personal(mk_bot, monkeypatch):
    def make(chat_id):
        monkeypatch.setattr(config, "CHAT_ID", str(chat_id))
        b = mk_bot()
        return b
    return make


def test_personal_mode_operator_dm(personal, near_a):
    bot = personal(OWNER)
    assert usage_note.windows_for(bot, _sess(0), now=NOW) == _week(92)


def test_personal_mode_group_chat_id(personal, near_a):
    bot = personal(GROUP)
    assert usage_note.windows_for(bot, _sess(0), now=NOW) is None


def test_legacy_team_group_gets_nothing(mk_bot, monkeypatch, near_a):
    monkeypatch.setattr(config, "CHAT_ID", str(GROUP))
    team = Team(group_id=GROUP, users={OWNER: User(id=OWNER, label="alice",
                                                   role=TeamRole.ADMIN)})
    bot = mk_bot(team=team)
    assert usage_note.windows_for(bot, _sess(0), now=NOW) is None


def test_other_login_session_never_shows_newest_reading(scoped):
    # The session runs under A, which has no reading; B's is newest and
    # near its limit. Nothing is shown.
    usage_store.note_plan("backend", _week(99), B, now=NOW)
    assert usage_note.windows_for(scoped, _sess(OWNER, login=A), now=NOW) is None
    assert usage_note.windows_for(scoped, _sess(OWNER, login=B), now=NOW) == _week(99)


def test_unknown_key_shows_nothing(scoped):
    usage_store.note_plan("adopted", _week(99), SessionLogin("unknown"), now=NOW)
    sess = _sess(OWNER, login=None)
    assert usage_note.windows_for(scoped, sess, now=NOW) is None
    sess.usage_plan_key = "unknown"
    assert usage_note.windows_for(scoped, sess, now=NOW) is None


def test_unknown_key_never_reaches_the_store(scoped, monkeypatch):
    """windows_for's own key guard, with a store that would answer any
    key: an empty or unknown sign-in still shows nothing."""
    asked = []

    def any_key(key, *, now=None):
        asked.append(key)
        return _week(99)
    monkeypatch.setattr(usage_store, "plan_windows", any_key)
    sess = _sess(OWNER, login=None)
    for key in ("", "unknown"):
        sess.usage_plan_key = key
        assert usage_note.windows_for(scoped, sess, now=NOW) is None
    assert asked == []
    sess.usage_plan_key = usage_store.plan_key(A)
    assert usage_note.windows_for(scoped, sess, now=NOW) == _week(99)


def test_access_error_fails_closed(scoped, near_a, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("access down")
    monkeypatch.setattr(usage_cmd, "usage_access", boom)
    assert usage_note.windows_for(scoped, _sess(OWNER), now=NOW) is None


def test_access_without_plan_shows_nothing(scoped, near_a, monkeypatch):
    monkeypatch.setattr(usage_cmd, "usage_access",
                        lambda *a, **kw: usage_cmd.UsageAccess(None, "chat", False))
    assert usage_note.windows_for(scoped, _sess(OWNER), now=NOW) is None
