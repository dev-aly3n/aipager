"""Iteration 3: when setup cannot tell whether a daemon runs (detection
raises), a change is not reloaded: ``reload: not_reloaded``,
``restart_needed: true``, exit 0, and no signal (docs/commands.md: "after
any change it sends no reload and reports restart_needed: true"). These
run through the REAL reload seams with ``os.kill`` recorded. The
``next_step`` restart advice is conditional ("If an aipager daemon is
running, ...") because no daemon may exist."""

from __future__ import annotations

import pytest

import aipager.wizard.daemon_io as _daemon_io
from agent_setup_support import OTHER_TOKEN

REAL_LIVE_RELOAD = _daemon_io._live_reload
REAL_SIGNAL_RELOAD = _daemon_io._signal_reload
CONDITIONAL = "if an aipager daemon is running"
NEW_CHAT = 777000111


def _boom():
    raise PermissionError("cannot read the daemon socket")


@pytest.fixture
def unknown_real(env, monkeypatch):
    """Installed first with no daemon, then detection becomes unknown and
    the real reload seams are back."""
    assert env.setup().code == 0
    kills: list = []
    monkeypatch.setattr(_daemon_io, "_detect_daemon_running", _boom)
    monkeypatch.setattr(_daemon_io, "_live_reload", REAL_LIVE_RELOAD)
    monkeypatch.setattr(_daemon_io, "_signal_reload", REAL_SIGNAL_RELOAD)
    monkeypatch.setattr(_daemon_io.os, "kill",
                        lambda pid, sig: kills.append((pid, sig)))
    env.kills = kills
    return env


def _role(env, **kw):
    return env.setup("--force", "--role", "admin", **kw)


def _chat(env, **kw):
    return env.setup("--force", chat=NEW_CHAT, **kw)


def _token(env, **kw):
    return env.setup("--force", token_file=env.token_file(
        OTHER_TOKEN, name="o"), **kw)


CHANGES = {"role": (_role, ["role"]), "chat": (_chat, ["owner_dm"]),
           "token": (_token, ["bot_token"])}


@pytest.mark.parametrize("change", CHANGES)
def test_unknown_change_exits_0(unknown_real, change):
    assert CHANGES[change][0](unknown_real).code == 0


@pytest.mark.parametrize("change", CHANGES)
def test_unknown_change_changed_is_exact(unknown_real, change):
    fn, want = CHANGES[change]
    assert fn(unknown_real).json["changed"] == want


@pytest.mark.parametrize("change", CHANGES)
def test_unknown_change_daemon_block(unknown_real, change):
    assert CHANGES[change][0](unknown_real).json["daemon"] == {
        "running": True, "reload": "not_reloaded", "restart_needed": True}


@pytest.mark.parametrize("change", CHANGES)
def test_unknown_change_sends_no_signal(unknown_real, change):
    CHANGES[change][0](unknown_real)
    assert unknown_real.kills == []


@pytest.mark.parametrize("change", CHANGES)
def test_unknown_change_has_daemon_unknown_warning(unknown_real, change):
    codes = [w["code"] for w in
             CHANGES[change][0](unknown_real).json["warnings"]]
    assert "daemon_unknown" in codes


@pytest.mark.parametrize("change", CHANGES)
def test_unknown_change_next_step_is_conditional(unknown_real, change):
    assert CONDITIONAL in CHANGES[change][0](
        unknown_real).json["next_step"].lower()


@pytest.mark.parametrize("change", CHANGES)
def test_unknown_change_next_step_says_stop_and_start(unknown_real, change):
    s = CHANGES[change][0](unknown_real).json["next_step"]
    assert "aipager service stop" in s and "aipager service start" in s


@pytest.mark.parametrize("change", ["role", "chat"])
def test_unknown_scope_change_next_step_names_no_bot_token(unknown_real,
                                                           change):
    assert "bot token" not in CHANGES[change][0](
        unknown_real).json["next_step"].lower()


def test_unknown_token_change_next_step_names_the_bot_token(unknown_real):
    assert "bot token" in _token(unknown_real).json["next_step"].lower()


def test_unknown_role_change_role_is_on_disk(unknown_real):
    _role(unknown_real)
    roles = [m["role"] for s in unknown_real.config()["scopes"]
             for m in s["members"]]
    assert roles == ["admin"]


def test_unknown_chat_change_chat_is_on_disk(unknown_real):
    _chat(unknown_real)
    assert [s["chat_id"] for s in unknown_real.config()["scopes"]] == [
        NEW_CHAT]


def test_unknown_unchanged_rerun_needs_no_restart(unknown_real):
    """Boundary: no change, nothing to restart for."""
    assert unknown_real.setup().json["daemon"]["restart_needed"] is False


def test_unknown_unchanged_rerun_is_unchanged(unknown_real):
    assert unknown_real.setup().json["status"] == "unchanged"


def test_unknown_unchanged_rerun_sends_no_signal(unknown_real):
    unknown_real.setup()
    assert unknown_real.kills == []


def test_unknown_role_change_plain_exits_0(unknown_real):
    assert _role(unknown_real, json_out=False).code == 0


def test_unknown_role_change_plain_says_conditional(unknown_real):
    r = _role(unknown_real, json_out=False)
    assert CONDITIONAL in (r.out + r.err).lower()


# ── fresh install, daemon state unknown: the next_step wording ────────

@pytest.fixture
def unknown_fresh(env, monkeypatch):
    monkeypatch.setattr(_daemon_io, "_detect_daemon_running", _boom)
    return env


def test_unknown_fresh_next_step_is_conditional(unknown_fresh):
    assert CONDITIONAL in unknown_fresh.setup().json["next_step"].lower()


def test_unknown_fresh_next_step_says_stop_and_start(unknown_fresh):
    s = unknown_fresh.setup().json["next_step"]
    assert "aipager service stop" in s and "aipager service start" in s


def test_unknown_fresh_next_step_names_the_bot_token(unknown_fresh):
    assert "bot token" in unknown_fresh.setup().json["next_step"].lower()


def test_unknown_fresh_restart_needed(unknown_fresh):
    assert unknown_fresh.setup().json["daemon"]["restart_needed"] is True


def test_unknown_fresh_plain_says_conditional(unknown_fresh):
    r = unknown_fresh.setup(json_out=False)
    assert CONDITIONAL in (r.out + r.err).lower()


def test_unknown_fresh_with_service_next_step_is_conditional(unknown_fresh):
    assert CONDITIONAL in unknown_fresh.setup(
        "--service").json["next_step"].lower()


def test_known_running_fresh_next_step_is_not_conditional(env):
    """Contrast: a daemon that is known to run gets the plain advice."""
    env.daemon_pid = 4242
    assert CONDITIONAL not in env.setup().json["next_step"].lower()


def test_known_running_fresh_next_step_says_stop_and_start(env):
    env.daemon_pid = 4242
    s = env.setup().json["next_step"]
    assert "aipager service stop" in s and "aipager service start" in s


def test_no_daemon_fresh_next_step_is_not_conditional(env):
    assert CONDITIONAL not in env.setup().json["next_step"].lower()
