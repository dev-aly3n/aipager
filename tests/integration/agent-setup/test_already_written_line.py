"""Iteration 4: on an error after a write, plain stderr adds
``  Already written: <files> (changed: <codes>).`` (entrypoints.md, the
setup JSON section; docs/commands.md "When the error came after a
write ... the plain output adds a stderr line"). It is never printed in a
dry run, before the first write, or when nothing was written."""

from __future__ import annotations

import os

import pytest

from agent_setup_support import (
    HTTP,
    NET,
    OTHER_TOKEN,
    TOKEN,
    assert_no_secret,
    make_unwritable,
)

PREFIX = "Already written:"
NEW_CHAT = 777000111


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines()
            if ln.strip().startswith(PREFIX)]


def _plain(env, *extra, **kw):
    return env.setup(*extra, json_out=False, **kw)


@pytest.fixture
def ro_settings(env):
    d = env.settings_path.parent
    d.mkdir(parents=True, exist_ok=True)
    make_unwritable(d)
    yield env
    os.chmod(d, 0o700)


def _ro_settings_on_role_change(env):
    assert env.setup().code == 0
    env.settings_path.write_text("{}\n")
    d = env.settings_path.parent
    make_unwritable(d)
    try:
        return _plain(env, "--force", "--role", "admin")
    finally:
        os.chmod(d, 0o700)


# ── settings.json write fails after the yaml (write_failed) ───────────

def test_settings_failure_plain_has_one_already_written_line(ro_settings):
    assert len(_lines(_plain(ro_settings).err)) == 1


def test_settings_failure_line_is_exact(ro_settings):
    assert _lines(_plain(ro_settings).err) == [
        "Already written: aipager.yaml (changed: bot_token, owner_dm)."]


def test_settings_failure_line_is_indented_two_spaces(ro_settings):
    err = _plain(ro_settings).err
    assert any(ln.startswith("  " + PREFIX) for ln in err.splitlines())


def test_settings_failure_line_not_on_stdout(ro_settings):
    assert PREFIX not in _plain(ro_settings).out


def test_settings_failure_line_names_no_settings_json(ro_settings):
    """settings.json was not written, so the line must not claim it."""
    assert all("settings.json" not in ln
               for ln in _lines(_plain(ro_settings).err))


def test_settings_failure_plain_leaks_no_token(ro_settings):
    r = _plain(ro_settings)
    assert_no_secret(r.out + r.err + r.log, "plain write_failed output")


def test_settings_failure_on_role_change_line_is_role_only(env):
    assert _lines(_ro_settings_on_role_change(env).err) == [
        "Already written: aipager.yaml (changed: role)."]


# ── service installer fails after yaml + settings (service_failed) ────

def test_service_rc_failure_line_names_both_files(env):
    env.service_rc = 1
    ln = _lines(_plain(env, "--service").err)
    assert len(ln) == 1 and "aipager.yaml" in ln[0] \
        and "settings.json" in ln[0]


def test_service_rc_failure_line_lists_the_codes(env):
    env.service_rc = 1
    ln = _lines(_plain(env, "--service").err)
    assert len(ln) == 1 and ln[0].endswith(
        "(changed: bot_token, owner_dm, settings_json).")


def test_service_rc_failure_line_does_not_claim_the_service(env):
    """The installer failed: `service` is not in what was written."""
    env.service_rc = 1
    ln = _lines(_plain(env, "--service").err)
    assert len(ln) == 1 and "service" not in ln[0].split("(changed:")[1]


def test_service_raising_line_lists_the_codes(env, monkeypatch):
    import aipager.service as svc

    def _boom(*, yes):
        raise OSError(f"cannot write the unit near {TOKEN}")
    monkeypatch.setattr(svc, "install_service", _boom)
    if "linux" in svc._DISPATCH:
        monkeypatch.setitem(svc._DISPATCH["linux"], "install", _boom)
    r = _plain(env, "--service")
    ln = _lines(r.err)
    assert len(ln) == 1 and ln[0].endswith(
        "(changed: bot_token, owner_dm, settings_json).")


def test_service_raising_plain_leaks_no_token(env, monkeypatch):
    import aipager.service as svc

    def _boom(*, yes):
        raise OSError(f"cannot write the unit near {TOKEN}")
    monkeypatch.setattr(svc, "install_service", _boom)
    if "linux" in svc._DISPATCH:
        monkeypatch.setitem(svc._DISPATCH["linux"], "install", _boom)
    r = _plain(env, "--service")
    assert_no_secret(r.out + r.err + r.log, "plain service_failed output")


def test_service_failure_on_role_change_line_is_role_only(env):
    assert env.setup().code == 0
    env.service_rc = 1
    assert _lines(_plain(env, "--service", "--force", "--role",
                         "admin").err) == [
        "Already written: aipager.yaml (changed: role)."]


def test_service_failure_on_chat_change_line_is_owner_dm_only(env):
    assert env.setup().code == 0
    env.service_rc = 1
    assert _lines(_plain(env, "--service", "--force",
                         chat=NEW_CHAT).err) == [
        "Already written: aipager.yaml (changed: owner_dm)."]


def test_service_failure_with_nothing_written_has_no_line(env):
    """Boundary: an unchanged install, then the installer fails. The
    error comes after the write phase but nothing was written."""
    assert env.setup().code == 0
    env.service_rc = 1
    r = _plain(env, "--service")
    assert (r.code, _lines(r.err)) == (1, [])


def test_service_failure_json_mode_stdout_has_no_line(env):
    env.service_rc = 1
    assert PREFIX not in env.setup("--service").out


# ── never in a dry run ────────────────────────────────────────────────

def test_dry_run_existing_install_refusal_has_no_line(env):
    """A dry-run refusal still fills `changed` with what would change; it
    must not be described as written."""
    assert env.setup().code == 0
    r = _plain(env, "--dry-run", chat=NEW_CHAT)
    assert (r.code, _lines(r.err)) == (6, [])


def test_dry_run_existing_install_refusal_token_has_no_line(env):
    assert env.setup().code == 0
    r = _plain(env, "--dry-run", token_file=env.token_file(
        OTHER_TOKEN, name="o"))
    assert (r.code, _lines(r.err)) == (6, [])


def test_dry_run_missing_deps_has_no_line(env):
    env.missing.add("dtach")
    r = _plain(env, "--dry-run")
    assert (r.code, _lines(r.err)) == (3, [])


def test_dry_run_with_service_failing_has_no_line(env):
    env.service_rc = 1
    r = _plain(env, "--dry-run", "--service")
    assert _lines(r.err + r.out) == []


def test_dry_run_with_ro_settings_has_no_line(ro_settings):
    r = _plain(ro_settings, "--dry-run")
    assert _lines(r.err + r.out) == []


# ── never before the first write ──────────────────────────────────────

def _deps_missing(env):
    env.missing.add("aipager-hook")
    return _plain(env)


def _not_started(env):
    env.tg.set("sendMessage", HTTP(400, "Bad Request: chat not found"))
    return _plain(env)


def _blocked(env):
    env.tg.set("sendMessage", HTTP(403, "Forbidden: bot was blocked by "
                                        "the user"))
    return _plain(env)


def _rejected(env):
    return _plain(env, token_file=env.token_file(
        "555555555:" + "Z" * 35, name="bad"))


def _unreachable(env):
    env.tg.set("getMe", NET())
    return _plain(env)


def _existing(env):
    assert env.setup().code == 0
    return _plain(env, chat=NEW_CHAT)


def _malformed(env):
    env.config_path.parent.mkdir(parents=True, exist_ok=True)
    env.config_path.write_text("scopes: [unclosed\n")
    os.chmod(env.config_path, 0o600)
    return _plain(env)


def _bad_chat(env):
    return _plain(env, chat="-5")


PRE_WRITE = {
    "deps_missing": (_deps_missing, 3),
    "chat_not_started": (_not_started, 5),
    "bot_blocked": (_blocked, 5),
    "token_rejected": (_rejected, 4),
    "telegram_unreachable": (_unreachable, 7),
    "existing_install": (_existing, 6),
    "config_malformed": (_malformed, 1),
    "bad_chat_id": (_bad_chat, 2),
}


@pytest.mark.parametrize("case", PRE_WRITE)
def test_pre_write_failure_exit_code(env, case):
    fn, code = PRE_WRITE[case]
    assert fn(env).code == code


@pytest.mark.parametrize("case", PRE_WRITE)
def test_pre_write_failure_has_no_already_written_line(env, case):
    r = PRE_WRITE[case][0](env)
    assert _lines(r.err + r.out) == []


# ── never on success ──────────────────────────────────────────────────

def test_fresh_install_success_has_no_line(env):
    r = _plain(env)
    assert (r.code, _lines(r.err + r.out)) == (0, [])


def test_unchanged_rerun_has_no_line(env):
    assert env.setup().code == 0
    r = _plain(env)
    assert (r.code, _lines(r.err + r.out)) == (0, [])


def test_role_change_success_has_no_line(env):
    assert env.setup().code == 0
    r = _plain(env, "--force", "--role", "admin")
    assert (r.code, _lines(r.err + r.out)) == (0, [])


def test_reload_not_reloaded_is_not_an_error_so_no_line(env):
    """A failed reload is exit 0, not an error after a write."""
    assert env.setup().code == 0
    env.daemon_pid = 4242
    env.reload_outcome = ("not_reloaded", "the socket is gone")
    r = _plain(env, "--force", "--role", "admin")
    assert (r.code, _lines(r.err + r.out)) == (0, [])

