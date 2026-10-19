"""A settings file that lost aipager's hooks after they were wired is
wired again before the next launch, with a WARNING (entrypoints
``ensure_session_hooks``; "Side effects": WARNING ``lost aipager's hooks
since they were wired``; review-1 rev-iter1-004).

Contract under test: unchanged file -> True; changed but still wired ->
True and not rewritten; lost the hooks or removed -> WARNING and wired
again; True when the file has aipager's hooks afterwards; never raises.
"""

from __future__ import annotations

import json
import logging

import pytest

from aipager import claude_bootstrap, claude_paths
from aipager.dtach.inject import launch_session

from _cfgdir_kit import has_hooks, rjson, wjson

CCD = "CLAUDE_CONFIG_DIR"
LOGGER = "aipager.claude_bootstrap"
LOST = "lost aipager's hooks since they were wired"
NO_HOOKS = "sessions will start without aipager's hooks"


def _logged(caplog, text, level=None) -> bool:
    return any(text in r.getMessage() and (level is None or r.levelno == level)
               for r in caplog.records if r.name == LOGGER)


def _paths(cc):
    return claude_paths.locate({CCD: str(cc.x)}, cwd=str(cc.work))


def _settings(cc):
    return cc.x / "settings.json"


def _strip_hooks(path):
    """A hand edit (or another tool) that drops aipager's hooks and keeps
    the user's own keys."""
    data = rjson(path)
    data.pop("hooks", None)
    data["model"] = "opus"
    path.write_text(json.dumps(data, indent=4))


@pytest.fixture
def wired(cc):
    """The file was wired once by this process (as at daemon start)."""
    assert claude_bootstrap.ensure_session_hooks(_paths(cc)) is True
    return cc


# ---- lost the hooks --------------------------------------------------------------

def test_a_file_that_lost_the_hooks_is_wired_again(wired):
    _strip_hooks(_settings(wired))
    claude_bootstrap.ensure_session_hooks(_paths(wired))
    assert has_hooks(_settings(wired))


def test_a_file_that_lost_the_hooks_reports_true_after_rewiring(wired):
    _strip_hooks(_settings(wired))
    assert claude_bootstrap.ensure_session_hooks(_paths(wired)) is True


def test_a_file_that_lost_the_hooks_logs_a_warning(wired, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    _strip_hooks(_settings(wired))
    claude_bootstrap.ensure_session_hooks(_paths(wired))
    assert _logged(caplog, LOST, logging.WARNING)


def test_rewiring_keeps_the_users_own_keys(wired):
    _strip_hooks(_settings(wired))
    claude_bootstrap.ensure_session_hooks(_paths(wired))
    assert rjson(_settings(wired)).get("model") == "opus"


def test_a_file_that_lost_only_the_status_line_keeps_its_hooks(wired):
    """Boundary: hooks still there, statusLine gone. Not "lost the
    hooks"; the call must still report the hooks present."""
    data = rjson(_settings(wired))
    data.pop("statusLine", None)
    _settings(wired).write_text(json.dumps(data))
    assert claude_bootstrap.ensure_session_hooks(_paths(wired)) is True


def test_a_removed_file_is_wired_again(wired):
    _settings(wired).unlink()
    claude_bootstrap.ensure_session_hooks(_paths(wired))
    assert has_hooks(_settings(wired))


def test_a_removed_file_logs_a_warning(wired, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    _settings(wired).unlink()
    claude_bootstrap.ensure_session_hooks(_paths(wired))
    assert _logged(caplog, LOST, logging.WARNING)


def test_a_file_replaced_by_a_hookless_copy_is_wired_again(wired):
    """A restore from backup: a new inode without aipager's entries."""
    target = _settings(wired)
    backup = target.with_name("settings.json.restore")
    backup.write_text('{"model": "sonnet"}')
    backup.replace(target)
    claude_bootstrap.ensure_session_hooks(_paths(wired))
    assert has_hooks(target)


def test_a_file_lost_after_daemon_start_is_wired_before_the_launch(cc):
    """The review's scenario end to end: start wires daemon.env's home,
    the file loses the hooks, the next launch's check wires it again."""
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    claude_bootstrap.bootstrap_claude_settings(str(cc.work))
    _strip_hooks(_settings(cc))
    claude_bootstrap.ensure_session_hooks(claude_paths.for_sessions(cwd=str(cc.work)))
    assert has_hooks(_settings(cc))


def test_lost_hooks_are_found_with_a_second_loss(wired):
    """Every loss is caught, not only the first one."""
    _strip_hooks(_settings(wired))
    claude_bootstrap.ensure_session_hooks(_paths(wired))
    _strip_hooks(_settings(wired))
    claude_bootstrap.ensure_session_hooks(_paths(wired))
    assert has_hooks(_settings(wired))


# ---- no loss: no warning, no rewrite ------------------------------------------------

def test_an_unchanged_file_logs_no_loss(wired, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    claude_bootstrap.ensure_session_hooks(_paths(wired))
    assert not _logged(caplog, LOST)


def test_an_unchanged_file_is_not_rewritten(wired):
    before = _settings(wired).read_bytes()
    claude_bootstrap.ensure_session_hooks(_paths(wired))
    assert _settings(wired).read_bytes() == before


def test_a_changed_but_still_wired_file_logs_no_loss(wired, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    data = rjson(_settings(wired))
    data["model"] = "haiku"
    _settings(wired).write_text(json.dumps(data))
    claude_bootstrap.ensure_session_hooks(_paths(wired))
    assert not _logged(caplog, LOST)


def test_the_first_wiring_logs_no_loss(cc, caplog):
    """A file never wired before is not "lost": no loss warning."""
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    claude_bootstrap.ensure_session_hooks(_paths(cc))
    assert not _logged(caplog, LOST)


# ---- a file that lost its hooks and cannot be wired again -------------------------

def test_a_wired_file_turned_malformed_reports_false(wired):
    _settings(wired).write_text("{broken after wiring")
    assert claude_bootstrap.ensure_session_hooks(_paths(wired)) is False


def test_a_wired_file_turned_malformed_is_not_overwritten(wired):
    _settings(wired).write_text("{broken after wiring")
    claude_bootstrap.ensure_session_hooks(_paths(wired))
    assert _settings(wired).read_text() == "{broken after wiring"


def test_a_wired_file_turned_malformed_says_sessions_have_no_hooks(wired, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    _settings(wired).write_text("{broken after wiring")
    claude_bootstrap.ensure_session_hooks(_paths(wired))
    assert _logged(caplog, NO_HOOKS, logging.WARNING)


# ---- through launch_session --------------------------------------------------------

class _FakeProc:
    returncode = 1

    async def communicate(self):
        return b"", b"fake dtach: not run"

    async def wait(self):
        return 1


@pytest.fixture
def spawned(monkeypatch):
    seen: list[str] = []

    async def _fake(*args, **kwargs):
        seen.append(" ".join(str(a) for a in args))
        return _FakeProc()

    monkeypatch.setattr("aipager.dtach.inject._create_subprocess_exec", _fake)
    return seen


def _launch(cc, run_async):
    return run_async(launch_session("cfgdirlost", mode="ask", cwd=str(cc.work)))


def test_a_launch_rewires_a_file_that_lost_the_hooks(cc, run_async, spawned):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _launch(cc, run_async)
    _strip_hooks(_settings(cc))
    _launch(cc, run_async)
    assert has_hooks(_settings(cc))


def test_a_launch_that_rewires_logs_the_loss(cc, run_async, spawned, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _launch(cc, run_async)
    _strip_hooks(_settings(cc))
    _launch(cc, run_async)
    assert _logged(caplog, LOST, logging.WARNING)


def test_a_launch_rewires_the_moved_file_that_lost_the_hooks(cc, run_async, spawned):
    """P2: a settings ``env`` move; the moved file is the one that counts."""
    wjson(cc.home_settings, {"env": {CCD: str(cc.x)}})
    _launch(cc, run_async)
    _strip_hooks(_settings(cc))
    _launch(cc, run_async)
    assert has_hooks(_settings(cc))
