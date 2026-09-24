"""Update Claude Code: `<absolute claude> update`, never a session restart."""

from __future__ import annotations

from aipager import claude_resolve, self_update
from aipager.state import Status


def test_claude_update_runs_resolved_absolute_binary(env, run):
    async def scenario():
        await env.start("claude")
        await env.finish()
    run(scenario)
    update_calls = [c for c in env.calls if c[-1] == "update"]
    assert update_calls == [["/home/op/.local/bin/claude", "update"]]
    assert env.manager.snapshot()["phase"] == "done"
    assert "Claude Code</b> 2.1.281 → 2.1.290" in env.last_text()


def test_claude_update_timeout_reported(env, run):
    env.claude_timeout = True

    async def scenario():
        await env.start("claude")
        await env.finish()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "failed"
    assert "timed out after 300s" in env.last_text()


def test_claude_update_failure_shows_tail(env, run):
    env.claude_rc = 1

    async def scenario():
        await env.start("claude")
        await env.finish()
    run(scenario)
    assert "update failed (exit 1)" in env.last_text()
    assert "update output" in env.last_text()


def test_claude_update_refreshes_resolver_memo(env, run, monkeypatch):
    refreshed = []
    monkeypatch.setattr(claude_resolve, "refresh_claude_binary",
                        lambda: refreshed.append(True))

    async def scenario():
        await env.start("claude")
        await env.finish()
    run(scenario)
    assert refreshed == [True]


def test_refresh_claude_binary_replaces_the_memo(monkeypatch):
    first = claude_resolve.ClaudeInstall("/c", "/c", "2.1.281")
    second = claude_resolve.ClaudeInstall("/c", "/c", "2.1.290")
    claude_resolve._memo = claude_resolve.ResolvedClaude(chosen=first)
    monkeypatch.setattr(claude_resolve, "_candidate_paths", lambda: [("/c", 3)])
    monkeypatch.setattr(claude_resolve, "_verify_candidate", lambda p: (second, ""))
    got = claude_resolve.refresh_claude_binary()
    assert got.chosen.version == "2.1.290"
    assert claude_resolve.resolve_claude_binary().chosen.version == "2.1.290"


def test_claude_update_lists_sessions_and_restarts_none(env, run, monkeypatch):
    env.add_session("claude-dev", "dev", Status.BUSY, scope_chat_id=env.chat_id)
    env.add_session("claude-web", "web", Status.IDLE)
    env.add_session("claude-old", "old", Status.GONE)
    env.add_session("claude-far", "far", Status.IDLE, scope_chat_id=-100555)

    def _forbidden(*a, **k):
        raise AssertionError("claude update must never touch a session")
    for name in ("kill_session", "launch_session", "send_keys"):
        monkeypatch.setattr(f"aipager.dtach.inject.{name}", _forbidden, raising=False)

    async def scenario():
        await env.start("claude")
        await env.finish()
    run(scenario)
    text = env.last_text()
    assert "keep the old version until they are restarted" in text
    assert "dev, web" in text
    assert "old" not in text.split("Still on the old version:")[1].split("\n")[0]
    assert "and 1 in other chats" in text
    assert "New sessions use 2.1.290" in text
    assert env.registry.get("claude-dev").status == Status.BUSY
    assert all(c[0] == "/home/op/.local/bin/claude" for c in env.calls)


def test_claude_not_found(env, run):
    env.claude_found = False

    async def scenario():
        await env.start("claude")
        await env.finish()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "failed"
    assert "not found" in env.last_text()
    assert env.calls == []


def test_claude_update_timeout_constant_reaches_the_seam(env, run, monkeypatch):
    seen = []
    original = env._run

    def _spy(argv, *, timeout, env=None, capture=True):
        if argv[-1] == "update":
            seen.append(timeout)
        return original(argv, timeout=timeout, env=env, capture=capture)
    monkeypatch.setattr(self_update, "_run_command", _spy)

    async def scenario():
        await env.start("claude")
        await env.finish()
    run(scenario)
    assert seen == [self_update.CLAUDE_UPDATE_TIMEOUT_SECONDS]
