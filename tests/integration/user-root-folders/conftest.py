"""Shared fixtures for the members' root folder tests (black-box).

Written from spec.md, design.md and entrypoints.md only. Every test runs
against a throwaway HOME under ``tmp_path`` (``home``); nothing reads or
writes the real home folder. No real Telegram, claude or dtach: every
launch/kill seam a test can reach is replaced by a recording double.

People:
- ``OWNER`` (owner) and ``ADMIN`` (admin): DMs, and in the group.
- ``SARA`` (user): DM with NO configured root (the default applies).
- ``SARA2`` (user, label ``sara!``): DM, no root; sanitises like Sara's.
- ``HAL`` (custom ``helper``: can prompt, no can_manage): DM with a
  configured root, and in the group.
- ``BOB`` (user), ``CY`` (read_only), ``BOSS`` (custom ``boss``: the
  admin bypass of role denies, but no can_manage): in the group only.

The group's root is configured: ``~/roots/team``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import policy
from aipager import policy_snapshot as ps
from aipager.dtach import inject
from aipager.miniapp import launch
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry



def _load_kit():
    name = "_userroot_kit"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).resolve().parent / "_kit.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


kit = _load_kit()
OWNER, ADMIN, SARA, SARA2, BOB, CY, HAL, BOSS = (
    kit.OWNER, kit.ADMIN, kit.SARA, kit.SARA2, kit.BOB, kit.CY, kit.HAL, kit.BOSS)
GROUP, TOKEN, SESSION, CLAUDE_SID = kit.GROUP, kit.TOKEN, kit.SESSION, kit.CLAUDE_SID

POLICY_TEXT = (
    "roles:\n"
    "  helper:\n"
    "    can_prompt: true\n"
    "    can_manage: false\n"
    "    deny_tools: [Bash]\n"
    "  boss:\n"
    "    can_prompt: true\n"
    "    can_manage: false\n"
    "    bypass_role_denies: true\n"
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A throwaway home folder, as ``~`` for this test."""
    h = tmp_path / "home" / "op"
    h.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    assert os.path.expanduser("~") == str(h)
    return h


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch, home):
    monkeypatch.delenv("CLAUDE_CODE_TMPDIR", raising=False)
    monkeypatch.delenv("TMPDIR", raising=False)
    # The root conftest points the session files at a folder it does not
    # create; the hook tests write them, so make it.
    ps.snapshot_path("claude-x").parent.mkdir(parents=True, exist_ok=True)
    launch._created.clear()
    yield
    launch._created.clear()


@pytest.fixture
def daemon_in_home(home, monkeypatch):
    """The daemon runs in the home folder (a systemd user unit)."""
    monkeypatch.setattr(inject, "_PROJECT_DIR", str(home))
    return home


@pytest.fixture
def team_root(home):
    return home / "roots" / "team"


@pytest.fixture
def hal_root(home):
    return home / "roots" / "hal"


@pytest.fixture
def scopes(home, team_root, hal_root):
    return [
        Scope(chat_id=OWNER, kind="dm", label="alice DM",
              members=(Member(id=OWNER, label="alice", role="owner"),)),
        Scope(chat_id=ADMIN, kind="dm", label="ada DM",
              members=(Member(id=ADMIN, label="ada", role="admin"),)),
        Scope(chat_id=SARA, kind="dm", label="Sara DM",
              members=(Member(id=SARA, label="Sara", role="user"),)),
        Scope(chat_id=SARA2, kind="dm", label="sara! DM",
              members=(Member(id=SARA2, label="sara!", role="user"),)),
        Scope(chat_id=HAL, kind="dm", label="hal DM",
              members=(Member(id=HAL, label="hal", role="helper"),),
              root=str(hal_root)),
        Scope(chat_id=GROUP, kind="group", label="team", members=(
            Member(id=OWNER, label="alice", role="owner"),
            Member(id=ADMIN, label="ada", role="admin"),
            Member(id=BOB, label="bob", role="user"),
            Member(id=CY, label="cy", role="read_only"),
            Member(id=HAL, label="hal", role="helper"),
            Member(id=BOSS, label="boss", role="boss"),
        ), root=str(team_root)),
    ]


@pytest.fixture
def pol(tmp_path):
    d = tmp_path / "pol"
    d.mkdir(exist_ok=True)
    f = d / "policy.yaml"
    f.write_text(POLICY_TEXT)
    return policy.load_policy(f, d / "none.d")


@pytest.fixture
def gbot(mk_bot, scopes, pol):
    """A scope-mode bot. Card, reaction and command-menu plumbing are
    recorded, not run."""
    def _mk(registry=None, *, scope_list=None):
        bot = mk_bot(registry or SessionRegistry(),
                     scopes=scopes if scope_list is None else scope_list)
        bot.policy = pol
        bot._app.bot.edit_message_text = AsyncMock()
        bot._app.bot.username = "aipager_test_bot"
        bot._app.bot.id = 999
        for name in ("_card_for_injected", "_react", "_maybe_update_bot_name",
                     "_update_bot_commands", "_send_busy_and_animate",
                     "refresh_pinned"):
            setattr(bot, name, AsyncMock())
        return bot
    return _mk


@pytest.fixture
def seams(monkeypatch):
    """Every PTY seam a launch/kill/relaunch could reach, recorded."""
    rec = MagicMock()
    rec.launch = AsyncMock(return_value=(True, ""))
    rec.kill = AsyncMock(return_value=True)
    monkeypatch.setattr(inject, "launch_session", rec.launch)
    monkeypatch.setattr(inject, "kill_session", rec.kill)
    monkeypatch.setattr(inject, "send_keys", AsyncMock(return_value=True))
    monkeypatch.setattr(inject, "send_text_and_enter", AsyncMock(return_value=True))
    monkeypatch.setattr(inject, "discard_queued_input", AsyncMock(return_value=True))
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=False))
    monkeypatch.setattr("aipager.bot.session_ops._sleep", AsyncMock())
    return rec


# ---- Mini App ----------------------------------------------------------------



@pytest.fixture
def app(gbot, daemon_in_home, monkeypatch, seams):
    """A Mini App over the scopes above; launches are recorded."""
    from aipager.miniapp.server import MiniAppServer
    monkeypatch.setattr("aipager.config.BOT_TOKEN", TOKEN)
    reg = SessionRegistry()
    bot = gbot(reg)
    srv = MiniAppServer(bot, reg, port=8765)
    srv.launched = seams.launch
    return srv


@pytest.fixture
def call(run_async):
    def _call(srv, method, path, uid, body=None, *, chat=None):
        from aiohttp.test_utils import TestClient, TestServer
        headers = {"X-Telegram-Init-Data": kit.init_data(uid)}
        if chat is not None:
            headers["X-Aipager-Scope"] = str(chat)

        async def _run():
            client = TestClient(TestServer(srv._build_app()))
            await client.start_server()
            try:
                resp = await client.request(method, path, headers=headers, json=body)
                try:
                    payload = await resp.json()
                except Exception:
                    payload = await resp.text()
                return resp.status, payload
            finally:
                await client.close()
        return run_async(_run())
    return _call


# ---- Hook ----------------------------------------------------------------------


@pytest.fixture
def hook(tmp_path):
    """``hook(tool, tool_input, cwd)`` -> the block descriptor or None, for
    a Telegram-marked turn of ``SESSION``."""
    from aipager.dtach import enforce
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "user", "message": {
        "content": "[via Telegram · @bob · role:user]\ndo it"}}) + "\n")

    def _run(tool, tool_input, cwd, **extra):
        payload = {
            "hook_event_name": "PreToolUse",
            "session": SESSION,
            "session_id": CLAUDE_SID,
            "cwd": str(cwd),
            "tool_name": tool,
            "tool_input": tool_input,
            "transcript_path": str(t),
        }
        payload.update(extra)
        return enforce.decide(payload)
    return _run


@pytest.fixture
def snap_for(pol, scopes):
    """``snap_for(member_id, chat_id)`` -> the resolved snapshot a message
    of that member in that chat runs under (root bound included)."""
    from aipager import member_root

    def _snap(uid, chat, scope_list=None):
        sl = scopes if scope_list is None else scope_list
        scope = next(s for s in sl if s.chat_id == chat)
        member = next(m for m in scope.members if m.id == uid)
        role = pol.get_role(member.role)
        return ps.resolve_snapshot(
            role, scope, member, policy=pol,
            root_bound=member_root.snapshot_bound(role, scope, sl))
    return _snap


@pytest.fixture
def use(snap_for):
    """``use(member_id, chat_id)``: the running turn of ``SESSION`` is held
    to that member's rules."""
    def _use(uid, chat, scope_list=None):
        snap = snap_for(uid, chat, scope_list)
        ps.write_merged_snapshot(SESSION, snap)
        return snap
    return _use
