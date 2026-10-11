"""A restart restores running sessions as running, and the ``/`` menu
follows a session coming back or ending under the same name.

Seen live on 2026-10-11: every session running across a restart loaded as
ended (the load's "orphan" backfill stamped ``gone_at`` on any entry saved
without one, and a running session is always saved without one), so the
startup ``/`` menu listed only the one session that had no transcript yet.
The first scan revived them, but a revival changes no NAME, and the scan
refreshed the menu only on a name change: the menu stayed short until a
/status five minutes later.
"""

from __future__ import annotations

import json
import logging
import os
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.dtach import inject
from aipager.scope import Member, Scope
from aipager.session_monitor import SessionMonitor
from aipager.state import SessionRegistry, Status, TrackedSession

DM = 555
# Taken at import, before the conftest's autouse patch replaces it.
_REAL_LIVE_SESSION_NAMES = inject.live_session_names
MTIME = float(int(time.time()) - 3600)  # recent: not aged out at load


def _saved(tmp_path, labels, **extra):
    """State-file entries as the previous run saved live sessions: a
    Claude session id, a transcript on disk, no ``gone_at``."""
    sessions = {}
    for lbl in labels:
        tp = tmp_path / f"{lbl}.jsonl"
        tp.write_text(json.dumps({"type": "user", "message": {
            "role": "user", "content": "do the thing"}}) + "\n")
        os.utime(tp, (MTIME, MTIME))
        sessions[f"claude-{lbl}"] = {
            "name": f"claude-{lbl}", "label": lbl, "claude_session_id": f"id-{lbl}",
            "transcript_path": str(tp), "scope_chat_id": DM, "scope_kind": "dm",
            **extra.get(lbl, {})}
    return {"sessions": sessions, "msg_map": {}}


def _load(tmp_state_file, state, monkeypatch, live):
    monkeypatch.setattr(inject, "live_session_names", lambda: set(live))
    tmp_state_file.write_text(json.dumps(state))
    r = SessionRegistry()
    r.load()
    return r


# ── B1: load ────────────────────────────────────────────────────────────────

def test_a_session_whose_socket_is_live_loads_running(
        tmp_state_file, tmp_path, monkeypatch, caplog):
    state = _saved(tmp_path, ["mona"])
    with caplog.at_level(logging.INFO, logger="aipager.state"):
        r = _load(tmp_state_file, state, monkeypatch, {"claude-mona"})
    s = r.get("claude-mona")
    assert s.status is Status.UNKNOWN
    assert s.gone_at is None
    assert "backfilled gone_at" not in caplog.text
    assert r.live_labels(DM) == {"mona"}


def test_an_orphan_without_a_socket_is_backfilled_as_today(
        tmp_state_file, tmp_path, monkeypatch, caplog):
    state = _saved(tmp_path, ["mona", "tim"])
    with caplog.at_level(logging.INFO, logger="aipager.state"):
        r = _load(tmp_state_file, state, monkeypatch, {"claude-tim"})
    s = r.get("claude-mona")
    assert s.status is Status.GONE
    assert s.gone_at == MTIME
    assert "[mona] backfilled gone_at" in caplog.text
    assert r.get("claude-tim").status is Status.UNKNOWN


def test_a_saved_end_stamp_wins_over_a_socket(tmp_state_file, tmp_path, monkeypatch):
    """An entry saved GONE stays GONE at load (the first scan revives it
    if its socket really is back, and refreshes the menu: B2)."""
    state = _saved(tmp_path, ["mona"], mona={"gone_at": MTIME - 60,
                                             "last_assistant_preview": "bye"})
    r = _load(tmp_state_file, state, monkeypatch, {"claude-mona"})
    s = r.get("claude-mona")
    assert s.status is Status.GONE and s.gone_at == MTIME - 60
    assert s.last_assistant_preview == "bye"


def test_an_unreadable_socket_folder_loads_as_before(tmp_state_file, tmp_path, monkeypatch):
    def broken():
        raise OSError("no folder")
    monkeypatch.setattr(inject, "live_session_names", broken)
    tmp_state_file.write_text(json.dumps(_saved(tmp_path, ["mona"])))
    r = SessionRegistry()
    r.load()
    assert r.get("claude-mona").status is Status.GONE


def test_live_session_names_reads_what_list_sessions_reads(monkeypatch, run_async):
    monkeypatch.setattr(inject, "_scan_session_sockets", lambda: ["claude-a", "claude-b"])
    # The conftest replaces the seam for every test; this is the real one.
    assert _REAL_LIVE_SESSION_NAMES() == {"claude-a", "claude-b"}
    assert run_async(inject.list_sessions()) == ["claude-a", "claude-b"]


def test_startup_menu_lists_every_running_session(
        tmp_state_file, tmp_path, monkeypatch, mk_bot, run_async):
    r = _load(tmp_state_file, _saved(tmp_path, ["mona", "jim", "dead"]),
              monkeypatch, {"claude-mona", "claude-jim"})
    b = mk_bot(r, scopes=[Scope(chat_id=DM, kind="dm", label="aly",
                                members=(Member(id=DM, label="aly", role="owner"),))])
    b._app.bot.set_my_commands = AsyncMock()
    run_async(b._update_bot_commands())
    commands = {c.command for c in b._app.bot.set_my_commands.await_args.args[0]}
    assert {"mona", "jim"} <= commands
    assert "dead" not in commands


def test_a_running_turn_is_still_adopted_after_the_change(
        tmp_state_file, tmp_path, monkeypatch, mk_bot, run_async, caplog):
    r = _load(tmp_state_file,
              _saved(tmp_path, ["mona"], mona={"busy_msg_id": 6553, "trigger_msg_id": 1}),
              monkeypatch, {"claude-mona"})
    b = mk_bot(r)
    b._app.bot.edit_message_text = AsyncMock()
    b._resume_animation_if_dead = MagicMock()
    monkeypatch.setattr(inject, "list_sessions", AsyncMock(return_value=["claude-mona"]))
    with caplog.at_level(logging.INFO):
        run_async(b.recover_sessions())
    s = r.get("claude-mona")
    assert s.status is Status.BUSY
    assert s.busy_msg_id == 6553
    assert s.gone_at is None
    assert "busy card 6553 adopted" in caplog.text
    b._app.bot.edit_message_text.assert_not_awaited()


# ── B2: the scan refreshes the menu on a liveness change ────────────────────

def _monitor(registry):
    async def _noop(*a, **k):
        return None
    m = SessionMonitor(registry, _noop)
    m.on_sessions_changed = AsyncMock()
    return m


def _registry_with(name, status, gone_at=None):
    r = SessionRegistry()
    sess = TrackedSession(name=name, label=name.removeprefix("claude-"), status=status)
    sess.gone_at = gone_at
    sess.transcript_path = "/nope"
    r._sessions[name] = sess
    return r


@pytest.mark.parametrize("sockets,status,gone_at,called", [
    (["claude-mona"], Status.GONE, 1234.0, True),   # revived, same name
    ([], Status.IDLE, None, True),                  # ended on its own
    (["claude-mona"], Status.IDLE, None, False),    # nothing changed
    (["claude-mona"], Status.UNKNOWN, None, False),  # restored live: still live
    ([], Status.GONE, 1234.0, False),               # still gone
], ids=["revived", "ended", "unchanged", "unknown-to-idle", "still-gone"])
def test_scan_refreshes_the_menu_when_liveness_changes(
        monkeypatch, run_async, sockets, status, gone_at, called):
    r = _registry_with("claude-mona", status, gone_at)
    m = _monitor(r)
    monkeypatch.setattr(inject, "list_sessions", AsyncMock(return_value=sockets))
    run_async(m._scan())
    assert m.on_sessions_changed.await_count == (1 if called else 0)


def test_a_session_ended_by_its_hook_between_scans_refreshes_once(monkeypatch, run_async):
    """The SessionEnd hook marks the session GONE before its socket goes,
    so the scan that sees the socket missing changes nothing itself: the
    change since the previous scan is what counts."""
    r = _registry_with("claude-mona", Status.IDLE)
    m = _monitor(r)
    monkeypatch.setattr(inject, "list_sessions", AsyncMock(return_value=["claude-mona"]))
    run_async(m._scan())
    assert m.on_sessions_changed.await_count == 0
    r.transition("claude-mona", Status.GONE)  # the SessionEnd hook
    monkeypatch.setattr(inject, "list_sessions", AsyncMock(return_value=[]))
    run_async(m._scan())
    assert m.on_sessions_changed.await_count == 1
    run_async(m._scan())
    assert m.on_sessions_changed.await_count == 1



def test_a_clear_between_scans_is_no_change(monkeypatch, run_async):
    """`/clear`: SessionEnd marks the session GONE and the process goes
    on; the next scan brings it back. Both scans saw it live: no refresh,
    so single-scope mode (which sends its keyboard on every refresh)
    posts nothing for a /clear, as before."""
    r = _registry_with("claude-mona", Status.IDLE)
    m = _monitor(r)
    monkeypatch.setattr(inject, "list_sessions", AsyncMock(return_value=["claude-mona"]))
    run_async(m._scan())
    r.transition("claude-mona", Status.GONE)  # SessionEnd(reason=clear)
    run_async(m._scan())
    assert r.get("claude-mona").status is Status.IDLE
    assert m.on_sessions_changed.await_count == 0


def test_single_scope_posts_no_keyboard_for_a_clear(monkeypatch, run_async, mk_bot):
    r = _registry_with("claude-mona", Status.IDLE)
    b = mk_bot(r)
    b._app.bot.set_my_commands = AsyncMock()
    m = _monitor(r)
    m.on_sessions_changed = b._update_bot_commands
    monkeypatch.setattr(inject, "list_sessions", AsyncMock(return_value=["claude-mona"]))
    run_async(m._scan())  # names unchanged from the registry: nothing
    sent = b._app.bot.send_message.await_count
    r.transition("claude-mona", Status.GONE)  # SessionEnd(reason=clear)
    run_async(m._scan())
    assert b._app.bot.send_message.await_count == sent


# ── single-scope: a command's own refresh is not repeated by the scan ───────

def _wired(mk_bot, names, status=Status.IDLE):
    """A single-scope bot (a keyboard on every refresh) wired to a real
    monitor, as `aipager/cli/daemon.py` wires them."""
    r = SessionRegistry()
    for n in names:
        s = TrackedSession(name=n, label=n.removeprefix("claude-"), status=status)
        s.transcript_path = "/nope"
        if status is Status.GONE:
            s.gone_at = time.time()
        r._sessions[n] = s
    b = mk_bot(r)
    b._app.bot.set_my_commands = AsyncMock()
    m = _monitor(r)
    m.on_sessions_changed = b._update_bot_commands
    return r, b, m


def _new(r):
    r.get_or_create("claude-tim")
    r.transition("claude-tim", Status.IDLE)


def _resume(r):
    r.get("claude-mona").gone_at = None
    r.transition("claude-mona", Status.IDLE)


def _kill(r):
    r.remove("claude-tim", remember_label=True)


@pytest.mark.parametrize("names,status,before,act,after", [
    (["claude-mona"], Status.IDLE, ["claude-mona"], _new, ["claude-mona", "claude-tim"]),
    (["claude-mona"], Status.GONE, [], _resume, ["claude-mona"]),
    (["claude-mona", "claude-tim"], Status.IDLE, ["claude-mona", "claude-tim"], _kill,
     ["claude-mona"]),
], ids=["new", "resume", "kill"])
def test_a_command_that_refreshed_itself_posts_one_keyboard(
        monkeypatch, run_async, mk_bot, names, status, before, act, after):
    r, b, m = _wired(mk_bot, names, status)
    monkeypatch.setattr(inject, "list_sessions", AsyncMock(return_value=before))
    run_async(m._scan())
    sent = b._app.bot.send_message.await_count
    act(r)
    run_async(b._update_bot_commands())  # what the command spawns itself
    monkeypatch.setattr(inject, "list_sessions", AsyncMock(return_value=after))
    run_async(m._scan())
    run_async(m._scan())
    assert b._app.bot.send_message.await_count == sent + 1


def test_a_refresh_inside_a_clear_window_is_corrected_by_the_scan(
        monkeypatch, run_async, mk_bot):
    """`/clear`'s SessionEnd marks the session GONE for under a scan; a
    refresh in that window (a /status) renders the menu without it. The
    scan that brings it back refreshes again, so the menu lists it."""
    r, b, m = _wired(mk_bot, ["claude-mona"])
    monkeypatch.setattr(inject, "list_sessions", AsyncMock(return_value=["claude-mona"]))
    run_async(m._scan())
    r.transition("claude-mona", Status.GONE)  # SessionEnd(reason=clear)
    run_async(b._update_bot_commands())       # a /status in the window
    run_async(m._scan())
    commands = {c.command for c in b._app.bot.set_my_commands.await_args.args[0]}
    assert "mona" in commands
