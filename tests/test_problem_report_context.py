"""``report_flow.report_context``: the daemon's facts for a problem report
(roadmap 8.112 step 3), built from live objects in one function.

No subprocess and no network at tap time: the Claude auth source is the
one the daemon found at startup, and doctor checks are left out. Nothing
that names a chat, a person or a session reaches the report.
"""

from __future__ import annotations

import json
import time

import pytest

from aipager.bot import report_flow
from aipager.report import builder, markers, store
from aipager.scope import Member, Scope
from aipager.state import Status, TrackedSession
from aipager.team import Role as TeamRole, Team, User as TeamUser

SECRET_LABEL = "zebra-canary-label"
SECRET_SESSION = "claude-okapi-canary"
GROUP_ID = -1009876543210
OWNER = 256113222


def _sess(name, status):
    sess = TrackedSession(name=name, label=SECRET_LABEL, status=status)
    return sess


def _scopes():
    return [
        Scope(chat_id=OWNER, kind="dm", label=SECRET_LABEL,
              members=(Member(id=OWNER, label=SECRET_LABEL, role="owner"),)),
        Scope(chat_id=GROUP_ID, kind="group", label=SECRET_LABEL, members=(
            Member(id=OWNER, label=SECRET_LABEL, role="owner"),
            Member(id=7, label="bob-canary", role="admin"))),
    ]


def test_personal_mode_facts(mk_bot):
    bot = mk_bot()
    for i, status in enumerate((Status.BUSY, Status.IDLE, Status.GONE, Status.UNKNOWN,
                                Status.INTERACTIVE)):
        bot.registry._sessions[f"{SECRET_SESSION}{i}"] = _sess(f"{SECRET_SESSION}{i}", status)
    markers.set_started_at(time.time() - 7200)
    bot.claude_auth_source = "file"
    ctx = report_flow.report_context(bot)
    assert ctx.mode == "personal"
    assert ctx.scopes is None
    assert ctx.sessions_live == 3 and ctx.sessions_busy == 1
    assert 7000 < ctx.uptime_seconds < 7400
    assert ctx.doctor == []
    assert ctx.claude_auth_source == "file"
    assert (ctx.unclean_exits_7d, ctx.last_exit) == store.exit_facts()
    report = builder.build_report("manual", context=ctx)
    assert report["config"]["mode"] == "personal"
    assert report["runtime"]["uptime"] == "1-24h"
    assert report["runtime"]["sessions_live"] == 3
    assert report["claude_code"]["auth"] == "file"
    assert report["doctor"] == {}


def test_team_mode(mk_bot):
    team = Team(group_id=-1001, users={OWNER: TeamUser(id=OWNER, label="a", role=TeamRole.ADMIN)})
    assert report_flow.report_context(mk_bot(team=team)).mode == "team"


def test_scope_mode_reaches_the_report_without_names_or_ids(mk_bot):
    bot = mk_bot(scopes=_scopes())
    bot.registry._sessions[SECRET_SESSION] = _sess(SECRET_SESSION, Status.BUSY)
    ctx = report_flow.report_context(bot)
    assert ctx.mode == "scope"
    report = builder.build_report("manual", context=ctx)
    assert report["config"]["scopes_dm"] == 1 and report["config"]["scopes_group"] == 1
    text = builder.render_preview(report)
    for canary in (SECRET_LABEL, SECRET_SESSION, "bob-canary", str(OWNER), str(GROUP_ID)[1:]):
        assert canary not in text


def test_unknown_auth_source_and_no_start(mk_bot):
    bot = mk_bot()
    bot.claude_auth_source = "something-else"
    ctx = report_flow.report_context(bot)
    assert ctx.claude_auth_source == "unknown"
    assert ctx.uptime_seconds is None
    assert mk_bot().claude_auth_source == "unknown"   # the bot's default


def test_features(mk_bot, monkeypatch):
    from aipager import preferences
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS", [("x", "y")])
    monkeypatch.setattr("aipager.config.RICH_SUMMARIES", True)
    preferences.set_preference(OWNER, "diff_preview", True)
    features = report_flow.report_context(mk_bot()).features
    assert {"miniapp", "tunnel_managed", "observers", "rich_summaries",
            "diff_preview_any"} <= set(features)
    assert "tunnel_override" not in features
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "https://x.example")
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", False)
    preferences.set_preference(OWNER, "diff_preview", False)
    features = report_flow.report_context(mk_bot()).features
    assert not {"miniapp", "tunnel_managed", "tunnel_override", "diff_preview_any"} & set(features)


def test_no_probe_at_tap(mk_bot, monkeypatch):
    """Neither the Claude auth probe nor a doctor check may run: both
    spawn processes and may reach the network."""
    from aipager import claude_resolve, doctor

    def _boom(*a, **k):
        raise AssertionError("probed at tap time")

    monkeypatch.setattr(claude_resolve, "detect_auth", _boom)
    monkeypatch.setattr(doctor, "run_all_keyed", _boom)
    monkeypatch.setattr("subprocess.run", _boom)
    monkeypatch.setattr("subprocess.Popen", _boom)
    ctx = report_flow.report_context(mk_bot())
    assert ctx.doctor == []


def test_a_broken_flood_file_or_registry_never_raises(mk_bot, monkeypatch):
    def _boom(*a, **k):
        raise OSError("broken")

    monkeypatch.setattr("aipager.status.read_flood_chats", _boom)
    bot = mk_bot()
    bot.registry.all_sessions = _boom
    ctx = report_flow.report_context(bot)
    assert ctx.flood_rows == [] and ctx.sessions_live is None


@pytest.mark.parametrize("source", ["env", "file", "keychain", "probe-failed",
                                    "version-gated", "unknown"])
def test_every_schema_auth_source_passes_through(mk_bot, source):
    bot = mk_bot()
    bot.claude_auth_source = source
    report = builder.build_report("manual", context=report_flow.report_context(bot))
    assert report["claude_code"]["auth"] == source
    json.dumps(report)
