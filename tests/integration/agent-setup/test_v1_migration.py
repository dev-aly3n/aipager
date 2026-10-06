"""design.md success criterion 6: a v1-only install (config.env, no
aipager.yaml) is migrated and then planned; refusals leave the
config.env-only state untouched."""

from __future__ import annotations

import pytest

from agent_setup_support import CHAT, OTHER_TOKEN, TOKEN

ENV_TEXT = f"CLAUDE_TG_BOT_TOKEN={TOKEN}\nCLAUDE_TG_CHAT_ID={CHAT}\n"


@pytest.fixture
def v1(env, monkeypatch):
    env.config_env.parent.mkdir(parents=True, exist_ok=True)
    env.config_env.write_text(ENV_TEXT)
    env.config_env.chmod(0o600)
    monkeypatch.setattr("aipager.config.BOT_TOKEN", TOKEN)
    monkeypatch.setattr("aipager.config.CHAT_ID", str(CHAT))
    assert not env.config_path.exists()
    return env


def test_v1_same_admin_install_exits_zero(v1):
    assert v1.setup("--role", "admin").code == 0


def test_v1_same_admin_changed_starts_with_migrated(v1):
    assert v1.setup("--role", "admin").json["changed"][0] == "migrated_v1"


def test_v1_same_admin_needs_no_force(v1):
    assert v1.setup("--role", "admin").json["error"] is None


def test_v1_migration_writes_yaml_with_admin(v1):
    v1.setup("--role", "admin")
    roles = [(m["id"], m["role"]) for s in v1.config()["scopes"]
             if s["chat_id"] == CHAT for m in s["members"]]
    assert roles == [(CHAT, "admin")]


def test_v1_migration_keeps_config_env_untouched(v1):
    v1.setup("--role", "admin")
    assert v1.config_env.read_text() == ENV_TEXT


def test_v1_migration_writes_a_config_env_backup(v1):
    v1.setup("--role", "admin")
    baks = list(v1.config_env.parent.glob("config.env.bak.*"))
    assert [b.read_text() for b in baks] == [ENV_TEXT]


def test_v1_migration_seeds_policy(v1):
    v1.setup("--role", "admin")
    assert v1.policy_path.exists()


def test_v1_migration_stores_the_token(v1):
    v1.setup("--role", "admin")
    same = v1.config()["bot_token"] == TOKEN
    assert same, "migrated aipager.yaml lacks the token"


def test_v1_default_owner_needs_force(v1):
    r = v1.setup()
    assert (r.code, r.json["error"]) == (6, "existing_install")


def test_v1_owner_refusal_leaves_v1_state_untouched(v1):
    before = v1.snapshot()
    v1.setup()
    assert v1.snapshot() == before


def test_v1_owner_with_force_makes_owner(v1):
    v1.setup("--force")
    roles = [(m["id"], m["role"]) for s in v1.config()["scopes"]
             if s["chat_id"] == CHAT for m in s["members"]]
    assert roles == [(CHAT, "owner")]


def test_v1_owner_with_force_changed(v1):
    assert v1.setup("--force").json["changed"][:2] == ["migrated_v1", "role"]


def test_v1_owner_with_force_grants_owner_audit(v1):
    v1.setup("--force")
    assert [a.get("user_id") for a in v1.audit()
            if a.get("action") == "grant-owner"] == [CHAT]


def test_v1_dry_run_reports_migration(v1):
    assert "migrated_v1" in v1.setup("--dry-run", "--role",
                                     "admin").json["changed"]


def test_v1_dry_run_writes_nothing(v1):
    before = v1.snapshot()
    v1.setup("--dry-run", "--role", "admin")
    assert v1.snapshot() == before


def test_v1_deps_missing_writes_nothing(v1):
    v1.missing.add("dtach")
    before = v1.snapshot()
    r = v1.setup("--role", "admin")
    assert (r.code, v1.snapshot() == before) == (3, True)


def test_v1_chat_not_started_writes_nothing(v1):
    from agent_setup_support import HTTP
    v1.tg.set("sendMessage", HTTP(400, "Bad Request: chat not found"))
    before = v1.snapshot()
    v1.setup("--force", chat=777)
    assert v1.snapshot() == before


def test_v1_different_token_needs_force(v1):
    r = v1.setup("--role", "admin",
                 token_file=v1.token_file(OTHER_TOKEN, name="o"))
    assert r.code == 6


def test_v1_rerun_after_migration_is_unchanged(v1):
    v1.setup("--role", "admin")
    assert v1.setup("--role", "admin").json["status"] == "unchanged"
