"""Roadmap 8.97: nothing run under pytest may write the operator's real home.

Every writer of a file under the home calls ``aipager._test_guard.
check_write`` first. These tests point the guard's real-home lookup
(``_test_guard._real_home``) at a fake home under ``tmp_path`` and aim
each writer there: the write must be refused and nothing created. No
writer here is ever pointed at the actual home of this machine; only the
pure ``check_write`` is asked about a real-home path (it writes nothing).
"""

from __future__ import annotations

import json
import os
import pwd
import subprocess
import sys
from pathlib import Path

import pytest

from aipager import _test_guard
from aipager._test_guard import RealHomeWriteError, check_write

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def fake_home(tmp_path, monkeypatch, real_home_refusals_expected):
    """A fake "real home": the guard treats it as the OS user's home.
    The refusals these tests provoke are expected (see the conftest's
    ``_no_swallowed_real_home_refusal``)."""
    home = tmp_path / "realhome"
    home.mkdir()
    monkeypatch.setattr(_test_guard, "_real_home", lambda: str(home))
    return home


def _nothing_under(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


# ----- the guard itself -----

def test_real_home_comes_from_the_password_database(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _test_guard._real_home() == pwd.getpwuid(os.getuid()).pw_dir


def test_a_real_home_path_is_refused_under_pytest(real_home_refusals_expected):
    # check_write only decides; it never writes, so asking it about the
    # real home is safe.
    real = Path(pwd.getpwuid(os.getuid()).pw_dir)
    target = real / ".config" / "aipager" / "aipager.yaml"
    with pytest.raises(RealHomeWriteError) as e:
        check_write(target)
    assert str(target) in str(e.value)
    assert isinstance(e.value, RuntimeError)


def test_the_real_home_itself_and_a_relative_path_into_it_are_refused(
        fake_home, monkeypatch):
    with pytest.raises(RealHomeWriteError):
        check_write(fake_home)
    monkeypatch.chdir(fake_home)
    with pytest.raises(RealHomeWriteError):
        check_write("aipager.yaml")


def test_a_tmp_path_outside_the_real_home_is_allowed(fake_home, tmp_path):
    other = tmp_path / "elsewhere" / "aipager.yaml"
    check_write(other)
    # A sibling whose name starts with the home's is not inside it.
    check_write(str(fake_home) + "-other/x")


def test_a_symlink_into_the_real_home_is_refused(fake_home, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(fake_home)
    with pytest.raises(RealHomeWriteError):
        check_write(link / ".claude" / "settings.json")


def test_a_registered_root_inside_the_real_home_is_allowed(
        fake_home, monkeypatch):
    monkeypatch.setattr(_test_guard, "_allowed_roots", [])
    allowed = fake_home / "pytest-base"
    _test_guard.allow_root(allowed)
    check_write(allowed / "x" / "aipager.yaml")
    with pytest.raises(RealHomeWriteError):
        check_write(fake_home / ".config" / "aipager" / "aipager.yaml")


def test_a_root_that_holds_the_real_home_does_not_allow_it(
        fake_home, monkeypatch):
    # tmp_path (and the system temp dir) hold the fake home: TMPDIR=$HOME
    # or a basetemp above the home must not open the whole home.
    monkeypatch.setattr(_test_guard, "_allowed_roots", [])
    _test_guard.allow_root(fake_home)
    _test_guard.allow_root(fake_home.parent)
    monkeypatch.setattr(_test_guard.tempfile, "gettempdir",
                        lambda: str(fake_home))
    with pytest.raises(RealHomeWriteError):
        check_write(fake_home / ".claude" / "settings.json")


@pytest.mark.parametrize("holder", [".config", ".claude", ".local/share",
                                    ".local/state", "Library"])
def test_a_root_that_holds_a_config_folder_does_not_allow_it(
        fake_home, monkeypatch, holder):
    # TMPDIR=~/.config (inside the home, not holding it) must not open
    # ~/.config/aipager.
    monkeypatch.setattr(_test_guard, "_allowed_roots", [])
    root = fake_home / holder
    monkeypatch.setattr(_test_guard.tempfile, "gettempdir", lambda: str(root))
    target = (root / "LaunchAgents" / "x.plist" if holder == "Library"
              else root / "settings.json" if holder == ".claude"
              else root / "aipager" / "x")
    with pytest.raises(RealHomeWriteError):
        check_write(target)


def test_a_root_inside_the_home_holding_no_config_folder_is_allowed(
        fake_home, monkeypatch):
    monkeypatch.setattr(_test_guard, "_allowed_roots", [])
    root = fake_home / "scratch" / "tmp"
    monkeypatch.setattr(_test_guard.tempfile, "gettempdir", lambda: str(root))
    check_write(root / "x")


def test_pytest_basetemp_is_an_allowed_root(tmp_path_factory):
    base = os.path.realpath(tmp_path_factory.getbasetemp())
    assert base in _test_guard._allowed_roots


def test_a_refusal_is_recorded(fake_home):
    _test_guard.refusals.clear()
    try:
        check_write(fake_home / "x")
    except RealHomeWriteError:
        pass
    assert len(_test_guard.refusals) == 1
    record = _test_guard.refusals[0]
    assert str(fake_home / "x") in record
    assert "[test: tests/test_real_home_guard.py::test_a_refusal_is_recorded" in record
    assert "thread: MainThread" in record


_INNER_CONFTEST = 'pytest_plugins = ["tests.conftest"]\n'
_INNER_TESTS = r"""
from aipager import _test_guard


def _provoke(tmp_path, monkeypatch):
    home = tmp_path / "h"
    home.mkdir()
    monkeypatch.setattr(_test_guard, "_real_home", lambda: str(home))
    try:
        _test_guard.check_write(home / "x")
    except RuntimeError:
        pass        # a broad except, the way the daemon's saves do it


def test_swallowed(tmp_path, monkeypatch):
    _provoke(tmp_path, monkeypatch)


def test_expected(tmp_path, monkeypatch, real_home_refusals_expected):
    _provoke(tmp_path, monkeypatch)
"""


_INNER_OUTSIDE_A_TEST = r"""
import pytest
from aipager import _test_guard


def _provoke(home):
    real = _test_guard._real_home
    _test_guard._real_home = lambda: str(home)
    try:
        _test_guard.check_write(home / "x")
    except RuntimeError:
        pass
    finally:
        _test_guard._real_home = real


@pytest.fixture(scope="module")
def swallowing_module_fixture(tmp_path_factory):
    _provoke(tmp_path_factory.mktemp("h"))


def test_after_a_module_fixture(swallowing_module_fixture):
    pass


def test_plain():
    pass
"""

_INNER_AT_SESSION_END = _INNER_OUTSIDE_A_TEST.split("@pytest.fixture")[0] + r"""
@pytest.fixture(scope="session", autouse=True)
def swallowing_at_session_end(tmp_path_factory):
    home = tmp_path_factory.mktemp("h2")
    yield
    _provoke(home)


def test_plain():
    pass
"""


def _run_inner(tmp_path, tests: str) -> tuple[int, str]:
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "conftest.py").write_text(_INNER_CONFTEST)
    (inner / "test_inner.py").write_text(tests)
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS")}
    env.update(PYTHONPATH=str(REPO_ROOT), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rfE",
         "-o", "addopts=", "--rootdir", str(inner), str(inner)],
        cwd=inner, env=env, capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout + r.stderr


def test_a_swallowed_refusal_fails_the_test(tmp_path):
    # An inner pytest run with this suite's conftest: a refusal that a
    # broad except swallowed still fails its test; an expected one passes.
    code, out = _run_inner(tmp_path, _INNER_TESTS)
    assert code != 0, out
    assert "ERROR test_inner.py::test_swallowed" in out, out
    assert "swallowed the error" in out
    assert "test_expected" not in out.split("short test summary info")[-1]
    assert "2 passed, 1 error" in out


def test_a_refusal_in_a_module_fixture_fails_the_next_test(tmp_path):
    # Swallowed in a module fixture's setup, outside any test body.
    code, out = _run_inner(tmp_path, _INNER_OUTSIDE_A_TEST)
    assert code != 0, out
    assert "ERROR test_inner.py::test_after_a_module_fixture" in out, out
    assert "before this test started" in out
    assert "1 passed, 1 error" in out


_INNER_INTERRUPTED = _INNER_OUTSIDE_A_TEST.split("@pytest.fixture")[0] + r"""
def test_interrupted(tmp_path):
    _provoke(tmp_path / "h")
    raise KeyboardInterrupt
"""


def test_an_interrupted_run_keeps_its_exit_status(tmp_path):
    # A refusal still pending when the run is interrupted is reported,
    # and the run keeps INTERRUPTED (2) rather than becoming a plain 1.
    code, out = _run_inner(tmp_path, _INNER_INTERRUPTED)
    assert code == 2, out
    assert "after the last test" in out


def test_a_refusal_after_the_last_test_fails_the_run(tmp_path):
    # Swallowed in a session fixture's teardown: every test passed, the
    # run still fails.
    code, out = _run_inner(tmp_path, _INNER_AT_SESSION_END)
    assert code == 1, out
    assert "after the last test" in out
    assert "1 passed" in out and "error" not in out.lower().split("passed")[-1]


def test_pytest_current_test_alone_turns_the_guard_on(fake_home, monkeypatch):
    monkeypatch.setattr(_test_guard, "_pytest_imported", lambda: False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    with pytest.raises(RealHomeWriteError):
        check_write(fake_home / "x")
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    assert _test_guard.under_pytest() is False
    check_write(fake_home / "x")


def test_pytest_imported_alone_turns_the_guard_on(fake_home, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert "pytest" in sys.modules
    with pytest.raises(RealHomeWriteError):
        check_write(fake_home / "x")


_NO_PYTEST_SCRIPT = r"""
import sys
from pathlib import Path
home = Path(sys.argv[1])
from aipager import _test_guard
assert "pytest" not in sys.modules
_test_guard._real_home = lambda: str(home)
target = home / ".claude" / "aipager-audit.jsonl"
from aipager import audit
assert audit.append(session="s", label="l", action="a", path=target) is True
print("WROTE" if target.exists() else "MISSING")
"""


def _run_without_pytest(fake: Path, *, env_extra=None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["HOME"] = str(fake.parent / "subhome")
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-c", _NO_PYTEST_SCRIPT, str(fake)],
                          env=env, capture_output=True, text=True, timeout=60)


def test_the_guard_is_a_no_op_without_pytest(tmp_path):
    # A process that never imports pytest writes even into "the real
    # home" (a fake one under tmp_path, never the actual home).
    fake = tmp_path / "realhome"
    fake.mkdir()
    r = _run_without_pytest(fake)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "WROTE"


def test_a_subprocess_of_a_test_is_guarded(tmp_path):
    # The same process with PYTEST_CURRENT_TEST (inherited from a test)
    # is refused: the guard reaches what a test spawns.
    fake = tmp_path / "realhome"
    fake.mkdir()
    r = _run_without_pytest(fake, env_extra={"PYTEST_CURRENT_TEST": "x"})
    assert r.returncode != 0
    assert "RealHomeWriteError" in r.stderr
    assert not (fake / ".claude").exists()


# ----- every writer calls the guard -----

def test_scope_atomic_write_yaml(fake_home):
    from aipager import scope
    with pytest.raises(RealHomeWriteError):
        scope._atomic_write_yaml({}, fake_home / ".config" / "aipager" / "aipager.yaml")
    assert _nothing_under(fake_home) == []


def test_scope_atomic_write_text(fake_home):
    from aipager import scope
    with pytest.raises(RealHomeWriteError):
        scope._atomic_write_text("x", fake_home / ".config" / "aipager" / "aipager.yaml")
    assert _nothing_under(fake_home) == []


def test_scope_dump_scopes(fake_home):
    from aipager import scope
    sc = scope.Scope(chat_id=42, kind="dm", label="owner DM",
                     members=(scope.Member(id=42, label="o", role="owner"),))
    with pytest.raises(RealHomeWriteError):
        scope.dump_scopes([sc], "1:A", fake_home / ".config" / "aipager" / "aipager.yaml")
    assert _nothing_under(fake_home) == []


def _plan(path: Path, *, exists: bool):
    from aipager.wizard import settings_patch
    return settings_patch.SettingsPlan(
        path=path, exists=exists, existing_text="{}\n" if exists else "",
        new_text='{"hooks": {}}\n', changed=True, repointed=0)


def test_settings_apply_new_file(fake_home):
    from aipager.wizard import settings_patch
    with pytest.raises(RealHomeWriteError):
        settings_patch.apply_settings(_plan(fake_home / ".claude" / "settings.json",
                                            exists=False))
    assert _nothing_under(fake_home) == []


def test_settings_apply_existing_file_makes_no_backup(fake_home):
    from aipager.wizard import settings_patch
    target = fake_home / ".claude" / "settings.json"
    target.parent.mkdir()
    target.write_text("{}\n")
    with pytest.raises(RealHomeWriteError):
        settings_patch.apply_settings(_plan(target, exists=True))
    assert _nothing_under(fake_home) == [".claude", ".claude/settings.json"]
    assert target.read_text() == "{}\n"


def test_settings_write_settings(fake_home):
    from aipager.wizard import settings_patch
    target = fake_home / "settings.json"
    with pytest.raises(RealHomeWriteError):
        settings_patch._write_settings(_plan(target, exists=False))
    assert _nothing_under(fake_home) == []


def test_audit_append(fake_home):
    from aipager import audit
    with pytest.raises(RealHomeWriteError):
        audit.append(session="s", label="l", action="a",
                     path=fake_home / ".claude" / "aipager-audit.jsonl")
    assert _nothing_under(fake_home) == []


def _team():
    from aipager import team
    return team.Team(group_id=-1001, users={
        1: team.User(id=1, label="a", role=team.Role.ADMIN)})


def test_team_dump(fake_home):
    from aipager import team
    with pytest.raises(RealHomeWriteError):
        team.dump_team(_team(), fake_home / ".config" / "aipager" / "team.yaml")
    assert _nothing_under(fake_home) == []


def test_team_archive(fake_home):
    from aipager import team
    target = fake_home / "team.yaml"
    target.write_text("x")
    with pytest.raises(RealHomeWriteError):
        team.archive_team(target)
    assert _nothing_under(fake_home) == ["team.yaml"]


def test_team_pending_users_write(fake_home, monkeypatch):
    from aipager import team
    monkeypatch.setattr(team, "PENDING_USERS_PATH",
                        fake_home / ".claude" / "aipager-pending-users.json")
    with pytest.raises(RealHomeWriteError):
        team._write_pending_users([{"user_id": 5}])
    assert _nothing_under(fake_home) == []


def test_team_pending_users_unlink(fake_home, monkeypatch):
    from aipager import team
    target = fake_home / "aipager-pending-users.json"
    target.write_text(json.dumps([{"user_id": 5, "username": "x"}]))
    monkeypatch.setattr(team, "PENDING_USERS_PATH", target)
    with pytest.raises(RealHomeWriteError):
        team.clear_pending_user(5)
    assert target.exists()


def test_service_daemon_env(fake_home, monkeypatch):
    from aipager import service
    monkeypatch.setattr(service, "DAEMON_ENV_PATH",
                        fake_home / ".config" / "aipager" / "daemon.env")
    with pytest.raises(RealHomeWriteError):
        service._write_daemon_env("X=1\n")
    assert _nothing_under(fake_home) == []


def test_service_backup_existing(fake_home):
    from aipager import service
    target = fake_home / "aipager.service"
    target.write_text("unit")
    with pytest.raises(RealHomeWriteError):
        service._backup_existing(target)
    assert _nothing_under(fake_home) == ["aipager.service"]


@pytest.fixture
def _service_seams(monkeypatch):
    from aipager import service
    monkeypatch.setattr(service, "_run", lambda *a, **k: (0, "", ""))
    monkeypatch.setattr(service, "_systemd_user_available",
                        lambda: (True, "running"))
    monkeypatch.setattr(service, "ensure_daemon_env", lambda: None)
    monkeypatch.setattr(service, "_render_linux_unit", lambda: "unit\n")
    monkeypatch.setattr(service, "_render_macos_plist", lambda: "plist\n")
    real_which = service.shutil.which
    monkeypatch.setattr(service.shutil, "which",
                        lambda name, *a, **k: "/usr/bin/launchctl"
                        if name == "launchctl" else real_which(name, *a, **k))
    return service


def test_service_linux_unit_install(fake_home, monkeypatch, _service_seams):
    service = _service_seams
    monkeypatch.setattr(service, "LINUX_UNIT_PATH",
                        fake_home / ".config" / "systemd" / "user" / "aipager.service")
    with pytest.raises(RealHomeWriteError):
        service._install_linux(yes=True)
    assert _nothing_under(fake_home) == []


def test_service_linux_unit_uninstall(fake_home, monkeypatch, _service_seams):
    service = _service_seams
    unit = fake_home / "aipager.service"
    unit.write_text("unit")
    monkeypatch.setattr(service, "LINUX_UNIT_PATH", unit)
    with pytest.raises(RealHomeWriteError):
        service._uninstall_linux()
    assert unit.exists()


def test_service_macos_plist_install(fake_home, tmp_path, monkeypatch,
                                     _service_seams):
    service = _service_seams
    monkeypatch.setattr(service, "MACOS_PLIST_PATH",
                        fake_home / "Library" / "LaunchAgents" / "x.plist")
    monkeypatch.setattr(service, "MACOS_LOG_PATH", tmp_path / "logs" / "a.log")
    with pytest.raises(RealHomeWriteError):
        service._install_macos(yes=True)
    assert _nothing_under(fake_home) == []


def test_service_macos_log_dir(fake_home, tmp_path, monkeypatch,
                               _service_seams):
    service = _service_seams
    monkeypatch.setattr(service, "MACOS_PLIST_PATH", tmp_path / "la" / "x.plist")
    monkeypatch.setattr(service, "MACOS_LOG_PATH",
                        fake_home / "Library" / "Logs" / "aipager.log")
    with pytest.raises(RealHomeWriteError):
        service._install_macos(yes=True)
    assert _nothing_under(fake_home) == []


def test_service_macos_plist_uninstall(fake_home, monkeypatch, _service_seams):
    service = _service_seams
    plist = fake_home / "x.plist"
    plist.write_text("p")
    monkeypatch.setattr(service, "MACOS_PLIST_PATH", plist)
    with pytest.raises(RealHomeWriteError):
        service._uninstall_macos()
    assert plist.exists()


def test_preferences_save(fake_home, monkeypatch):
    from aipager import preferences
    monkeypatch.setattr(preferences, "_PREFERENCES_PATH",
                        fake_home / ".config" / "aipager" / "preferences.json")
    with pytest.raises(RealHomeWriteError):
        preferences._save_raw({"a": 1})
    assert _nothing_under(fake_home) == []


def test_state_registry_save(fake_home, monkeypatch):
    from aipager import state
    monkeypatch.setattr(state, "SESSION_STATE_FILE",
                        fake_home / ".claude" / "aipager-sessions.json")
    with pytest.raises(RealHomeWriteError):
        state.SessionRegistry().save()
    assert _nothing_under(fake_home) == []


def test_migrate_backup(fake_home):
    from aipager import migrate
    src = fake_home / "config.env"
    src.write_text("X=1\n")
    with pytest.raises(RealHomeWriteError):
        migrate._backup(src)
    assert _nothing_under(fake_home) == ["config.env"]


def test_migrate_policy_seed(fake_home, monkeypatch):
    from aipager import migrate, policy, scope
    sc = scope.Scope(chat_id=42, kind="dm", label="owner DM",
                     members=(scope.Member(id=42, label="o", role="owner"),))
    monkeypatch.setattr(migrate, "_scopes_from_current", lambda: ([sc], "1:A"))
    monkeypatch.setattr(policy, "POLICY_PATH",
                        fake_home / ".config" / "aipager" / "policy.yaml")
    with pytest.raises(RealHomeWriteError):
        migrate.migrate_to_v2()
    assert _nothing_under(fake_home) == []


def test_migrate_retire_v1(fake_home, monkeypatch):
    from aipager import config, migrate
    src = fake_home / "config.env"
    src.write_text("X=1\n")
    monkeypatch.setattr(config, "_XDG_CONFIG", src)
    monkeypatch.setattr(migrate._scope, "load_scopes", lambda p: ([], "1:A"))
    with pytest.raises(RealHomeWriteError):
        migrate.retire_v1()
    assert _nothing_under(fake_home) == ["config.env"]


def test_claude_bootstrap_atomic_write(fake_home):
    from aipager import claude_bootstrap
    with pytest.raises(RealHomeWriteError):
        claude_bootstrap._atomic_write(fake_home / ".claude" / "settings.json", {})
    assert _nothing_under(fake_home) == []


def _scope42():
    from aipager import scope
    return scope.Scope(chat_id=42, kind="dm", label="owner DM",
                       members=(scope.Member(id=42, label="o", role="owner"),))


def test_session_store_folder(fake_home, monkeypatch):
    from aipager import session_store
    monkeypatch.setattr(session_store, "SESSIONS_ROOT",
                        fake_home / ".local" / "share" / "aipager" / "sessions")
    with pytest.raises(RealHomeWriteError):
        session_store.write_session_files(_scope42(), None, "x1")
    assert _nothing_under(fake_home) == []


def test_session_store_atomic_write(fake_home):
    from aipager import session_store
    with pytest.raises(RealHomeWriteError):
        session_store._atomic_write(fake_home / "SESSION.md", "x", 0o600)
    assert _nothing_under(fake_home) == []


def test_wizard_env_file(fake_home, monkeypatch):
    from aipager.wizard import daemon_io
    cfg = fake_home / ".config" / "aipager"
    monkeypatch.setattr(daemon_io, "CONFIG_DIR", cfg)
    monkeypatch.setattr(daemon_io, "CONFIG_ENV", cfg / "config.env")
    with pytest.raises(RealHomeWriteError):
        daemon_io._write_env_file("1:A", 42)
    assert _nothing_under(fake_home) == []


def test_wizard_draft_save(fake_home, monkeypatch):
    from aipager.wizard import draft
    cfg = fake_home / ".config" / "aipager"
    monkeypatch.setattr(draft, "CONFIG_DIR", cfg)
    monkeypatch.setattr(draft, "DRAFT_PATH", cfg / ".wizard-draft.json")
    with pytest.raises(RealHomeWriteError):
        draft.save_draft({"a": 1})
    assert _nothing_under(fake_home) == []


def test_wizard_draft_clear(fake_home, monkeypatch):
    from aipager.wizard import draft
    target = fake_home / ".wizard-draft.json"
    target.write_text("{}")
    monkeypatch.setattr(draft, "DRAFT_PATH", target)
    with pytest.raises(RealHomeWriteError):
        draft.clear_draft()
    assert target.exists()


def test_miniapp_config_env(fake_home, monkeypatch):
    from aipager.miniapp import cli as miniapp_cli
    from aipager.wizard import _constants
    cfg = fake_home / ".config" / "aipager"
    monkeypatch.setattr(_constants, "CONFIG_DIR", cfg)
    monkeypatch.setattr(_constants, "CONFIG_ENV", cfg / "config.env")
    with pytest.raises(RealHomeWriteError):
        miniapp_cli._write_config_env({"AIPAGER_MINIAPP": "1"})
    assert _nothing_under(fake_home) == []


def test_flood_state_save(fake_home):
    from aipager.bot import flood_state
    with pytest.raises(RealHomeWriteError):
        flood_state.save_if_dirty(
            path=fake_home / ".claude" / "aipager-flood-state.json", force=True)
    assert _nothing_under(fake_home) == []


def test_self_update_lock(fake_home):
    from aipager import self_update
    lock = self_update.UpdateLock(fake_home / ".local" / "share" / "aipager" / "update.lock")
    with pytest.raises(RealHomeWriteError):
        lock.try_acquire()
    assert _nothing_under(fake_home) == []


def test_self_update_marker_write(fake_home, monkeypatch):
    from aipager import self_update
    monkeypatch.setattr(self_update, "UPDATE_MARKER_PATH",
                        fake_home / ".local" / "share" / "aipager" / "m.json")
    with pytest.raises(RealHomeWriteError):
        self_update.write_marker({"a": 1})
    assert _nothing_under(fake_home) == []


def test_self_update_marker_clear(fake_home, monkeypatch):
    from aipager import self_update
    marker = fake_home / "m.json"
    marker.write_text("{}")
    monkeypatch.setattr(self_update, "UPDATE_MARKER_PATH", marker)
    with pytest.raises(RealHomeWriteError):
        self_update.clear_marker()
    assert marker.exists()


def test_daemon_lock(fake_home, monkeypatch):
    from aipager.cli import daemon
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(daemon, "_daemon_lock_fd", None)
    with pytest.raises(RealHomeWriteError):
        daemon._acquire_daemon_lock()
    assert _nothing_under(fake_home) == []


def test_updater_remove_path(fake_home):
    from aipager import updater
    cfg = fake_home / ".config" / "aipager"
    cfg.mkdir(parents=True)
    with pytest.raises(RealHomeWriteError):
        updater._remove_path(cfg)
    assert cfg.is_dir()


def test_updater_unlink_quietly(fake_home):
    from aipager import updater
    f = fake_home / "aipager-sessions.json"
    f.write_text("{}")
    with pytest.raises(RealHomeWriteError):
        updater._unlink_quietly(f)
    assert f.exists()


@pytest.mark.parametrize("module, env_var", [
    ("aipager.miniapp.cloudflared_fetch", "AIPAGER_CLOUDFLARED_CACHE_DIR"),
    ("aipager.miniapp.webapp_sdk", "AIPAGER_WEBAPP_SDK_CACHE_DIR"),
])
def test_cache_dirs(fake_home, monkeypatch, module, env_var):
    import importlib
    mod = importlib.import_module(module)
    monkeypatch.setenv(env_var, str(fake_home / ".local" / "share" / "aipager" / "c"))
    with pytest.raises(RealHomeWriteError):
        mod.cache_dir()
    assert _nothing_under(fake_home) == []


def test_cloudflared_install(fake_home):
    from aipager.miniapp import cloudflared_fetch
    with pytest.raises(RealHomeWriteError):
        cloudflared_fetch._atomic_install(fake_home / "cloudflared", b"x")
    assert _nothing_under(fake_home) == []


def test_webapp_sdk_write(fake_home):
    from aipager.miniapp import webapp_sdk
    with pytest.raises(RealHomeWriteError):
        webapp_sdk._atomic_write(fake_home / "telegram-web-app.js", b"x")
    assert _nothing_under(fake_home) == []


def test_claude_probe_cleanup(fake_home, monkeypatch, tmp_path):
    from aipager import claude_resolve
    monkeypatch.setenv("HOME", str(fake_home))
    probe_cwd = tmp_path / f"{claude_resolve._PROBE_DIR_PREFIX}abcdef0123456789"
    project = claude_resolve._probe_project_dir(str(probe_cwd))
    project.mkdir(parents=True)
    with pytest.raises(RealHomeWriteError):
        claude_resolve._cleanup_probe_artifacts(str(probe_cwd))
    assert project.is_dir()


def test_chat_migration_keeps_the_refusal_loud(monkeypatch):
    from aipager import scope
    from aipager.bot import chat_migration

    def _refused(old, new):
        raise RealHomeWriteError("refused")

    monkeypatch.setattr(scope, "migrate_scope_chat_id", _refused)
    with pytest.raises(RealHomeWriteError):
        chat_migration._write_config(-1, -1002)


@pytest.mark.parametrize("target", ["aipager.yaml", "settings.json", "audit"])
def test_setup_refuses_before_sending_or_writing(fake_home, tmp_path,
                                                 monkeypatch, capsys, target):
    from tests.test_setup_cmd import Env
    from aipager import audit, scope
    from aipager.wizard import settings_patch
    no_daemon = str(tmp_path / "no-daemon.sock")
    monkeypatch.setattr("aipager.config.SOCKET_PATH", no_daemon)
    monkeypatch.setattr("aipager.status.SOCKET_PATH", no_daemon)
    if target == "aipager.yaml":
        monkeypatch.setattr(scope, "CONFIG_PATH",
                            fake_home / ".config" / "aipager" / "aipager.yaml")
    elif target == "settings.json":
        monkeypatch.setattr(settings_patch, "CLAUDE_SETTINGS",
                            fake_home / ".claude" / "settings.json")
    else:
        monkeypatch.setattr(audit, "AUDIT_LOG_PATH",
                            fake_home / ".claude" / "aipager-audit.jsonl")
    env = Env(tmp_path, monkeypatch, capsys)
    code, doc, out, err = env.setup()
    assert code != 0
    assert doc["error"] == "internal_error"
    assert "RealHomeWriteError" in doc["message"]
    assert env.tg.sends == []
    assert doc["changed"] == []
    assert _nothing_under(fake_home) == []
    assert not scope.CONFIG_PATH.exists()


# ----- the wizard's restart hint -----

def _hint(monkeypatch, capsys, platform):
    from aipager.wizard import daemon_io
    monkeypatch.setattr(daemon_io, "_detect_daemon_running", lambda: 4321)
    monkeypatch.setattr(daemon_io, "_is_linux", lambda: platform == "linux")
    daemon_io._restart_hint()
    return " ".join(capsys.readouterr().out.split())


def test_restart_hint_names_commands_that_exist(monkeypatch, capsys):
    out = _hint(monkeypatch, capsys, "linux")
    assert "aipager service restart" not in out
    assert "aipager service stop, then aipager service start" in out
    assert "systemctl --user restart aipager" in out
    assert "aipager start" in out


def test_restart_hint_on_macos_has_no_systemctl(monkeypatch, capsys):
    out = _hint(monkeypatch, capsys, "darwin")
    assert "aipager service stop, then aipager service start" in out
    assert "systemctl" not in out


def test_is_linux_reads_the_platform():
    from aipager.wizard import daemon_io
    assert daemon_io._is_linux() is sys.platform.startswith("linux")


def test_team_yaml_header_names_commands_that_exist(tmp_path):
    from aipager import team
    target = tmp_path / "team.yaml"
    team.dump_team(_team(), target)
    text = target.read_text()
    assert "aipager service restart" not in text
    assert "`aipager service stop`, then `aipager service start`" in text


def test_restart_hint_commands_are_real_subcommands():
    from aipager import service
    for plat in ("linux", "macos"):
        assert {"start", "stop"} <= set(service._DISPATCH[plat])
        assert "restart" not in service._DISPATCH[plat]
