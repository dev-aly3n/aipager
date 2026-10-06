"""design.md success criterion 4 (second half): --dry-run writes nothing
(full tree snapshot of bytes and modes), sends nothing, and reports deps
and the plan."""

from __future__ import annotations

import json

from agent_setup_support import CHAT, OTHER_TOKEN


def test_dry_run_fresh_exits_zero(env):
    assert env.setup("--dry-run").code == 0


def test_dry_run_fresh_writes_nothing(env):
    before = env.snapshot()
    env.setup("--dry-run")
    assert env.snapshot() == before


def test_dry_run_with_service_writes_nothing(env):
    before = env.snapshot()
    env.setup("--dry-run", "--service")
    assert env.snapshot() == before


def test_dry_run_plain_writes_nothing(env):
    before = env.snapshot()
    r = env.setup("--dry-run", json_out=False)
    assert r.code == 0 and env.snapshot() == before


def test_dry_run_status(env):
    assert env.setup("--dry-run").json["status"] == "dry_run"


def test_dry_run_flag_is_true(env):
    assert env.setup("--dry-run").json["dry_run"] is True


def test_dry_run_reports_what_would_change(env):
    assert env.setup("--dry-run").json["changed"] == [
        "bot_token", "owner_dm", "settings_json"]


def test_dry_run_sends_no_message(env):
    env.setup("--dry-run")
    assert env.tg.sent == []


def test_dry_run_still_validates_the_token(env):
    env.setup("--dry-run")
    assert len(env.tg.calls_to("getMe")) == 1


def test_dry_run_test_message_skipped(env):
    assert env.setup("--dry-run").json["test_message"] == "skipped_dry_run"


def test_dry_run_reports_deps(env):
    items = env.setup("--dry-run").json["deps"]["items"]
    assert {i["name"] for i in items} >= {"dtach", "claude", "aipager-hook",
                                          "aipager-statusline"}


def test_dry_run_with_missing_deps_exits_3(env):
    env.missing.add("dtach")
    assert env.setup("--dry-run").code == 3


def test_dry_run_with_missing_deps_writes_nothing(env):
    env.missing.add("dtach")
    before = env.snapshot()
    env.setup("--dry-run")
    assert env.snapshot() == before


def test_dry_run_settings_would_change(env):
    st = env.setup("--dry-run").json["settings_json"]["status"]
    assert st == "would_change"


def test_dry_run_existing_settings_get_no_backup(env):
    env.settings_path.parent.mkdir(parents=True, exist_ok=True)
    env.settings_path.write_text(json.dumps({"model": "opus"}) + "\n")
    before = env.snapshot()
    env.setup("--dry-run")
    assert env.snapshot() == before


def test_dry_run_does_not_call_the_installer(env):
    env.setup("--dry-run", "--service")
    assert env.installs == []


def test_dry_run_service_would_install(env):
    assert env.setup("--dry-run", "--service").json["service"][
        "result"] == "would_install"


def test_dry_run_service_with_daemon_reports_skip(env):
    env.daemon_pid = 4242
    assert env.setup("--dry-run", "--service").json["service"][
        "result"] == "skipped_daemon_running"


def test_dry_run_with_daemon_does_not_reload(env):
    env.setup()
    env.daemon_pid = 4242
    env.reloads.clear()
    env.setup("--dry-run", "--force", chat=777)
    assert env.reloads == [] and env.signals == []


def test_dry_run_on_existing_install_change_exits_6(env):
    env.setup()
    assert env.setup("--dry-run", chat=777).code == 6


def test_dry_run_with_force_on_existing_writes_nothing(env):
    env.setup()
    before = env.snapshot()
    r = env.setup("--dry-run", "--force", chat=777)
    assert (r.code, env.snapshot() == before) == (0, True)


def test_dry_run_with_force_token_writes_nothing(env):
    env.setup()
    before = env.snapshot()
    env.setup("--dry-run", "--force",
              token_file=env.token_file(OTHER_TOKEN, name="o.txt"))
    assert env.snapshot() == before


def test_dry_run_with_force_reports_the_change(env):
    env.setup()
    assert env.setup("--dry-run", "--force", chat=777).json["changed"] == [
        "owner_dm"]


def test_dry_run_unchanged_install_reports_nothing_to_change(env):
    env.setup()
    assert env.setup("--dry-run").json["changed"] == []


def test_dry_run_writes_no_audit(env):
    env.setup("--dry-run")
    assert env.audit() == []


def test_dry_run_from_stdin_writes_nothing(env):
    from agent_setup_support import TOKEN
    before = env.snapshot()
    env.run("setup", "--token-stdin", "--chat-id", CHAT, "--dry-run",
            "--json", stdin=TOKEN)
    assert env.snapshot() == before
