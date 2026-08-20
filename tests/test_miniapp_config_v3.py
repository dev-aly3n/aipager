"""Mini App settings must live in aipager.yaml, not config.env.

The bug these pin: `aipager miniapp enable` wrote MINIAPP_* into
config.env, but `retire_v1()` renames config.env away on every daemon
start once aipager.yaml is authoritative — so the setting survived
exactly one restart and then silently turned the Mini App off.
"""

import importlib

import pytest
import yaml

from aipager import migrate, scope
from aipager.miniapp import cli as miniapp_cli


def _write_v2_config(path, *, token="123:abc", chat_id=555):
    """A realistic v2 file: schema_version 2, a token, and one scope."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({
        "schema_version": 2,
        "bot_token": token,
        "scopes": [{
            "kind": "dm",
            "chat_id": chat_id,
            "label": "me",
            "members": [{"id": chat_id, "label": "me", "role": "owner"}],
        }],
    }), encoding="utf-8")


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# ===== the actual regression ==============================================

def test_miniapp_setting_survives_retire_v1(tmp_path, monkeypatch, capsys):
    """THE regression. Enable the Mini App, then run the daemon's
    retire_v1() path, and the setting must still be there.

    Against the pre-fix code this fails: enable wrote config.env, and
    retire_v1 renamed it away, leaving nothing behind.
    """
    _write_v2_config(scope.CONFIG_PATH)

    assert miniapp_cli._cmd_miniapp_enable(
        _Args(port=9100, url="https://example.test"),
    ) == 0

    migrate.retire_v1()

    cfg = scope.load_miniapp(scope.CONFIG_PATH)
    assert cfg["enabled"] is True
    assert cfg["port"] == 9100
    assert cfg["public_url"] == "https://example.test"


def test_daemon_config_reads_enabled_after_retire_v1(tmp_path, monkeypatch):
    """End-to-end through the module the daemon actually consults: a
    fresh `aipager.config` import must see the Mini App as enabled even
    once config.env is gone."""
    _write_v2_config(scope.CONFIG_PATH)
    miniapp_cli._cmd_miniapp_enable(_Args(port=9101, url=""))
    migrate.retire_v1()

    # Env must not be what's making this pass.
    monkeypatch.delenv("MINIAPP_ENABLED", raising=False)
    monkeypatch.delenv("MINIAPP_PORT", raising=False)

    import aipager.config as config_mod
    try:
        importlib.reload(config_mod)
        assert config_mod.MINIAPP_ENABLED is True
        assert config_mod.MINIAPP_PORT == 9101
    finally:
        importlib.reload(config_mod)


# ===== v2 -> v3 upgrade ===================================================

def test_v2_file_still_loads_without_error(tmp_path):
    """An existing v2 install must not blow up on upgrade — a hard
    version equality check would raise ScopeConfigError for every user."""
    _write_v2_config(scope.CONFIG_PATH, token="9:tok", chat_id=42)
    loaded = scope.load_scopes(scope.CONFIG_PATH)
    assert loaded is not None
    scopes, token = loaded
    assert token == "9:tok"
    assert [s.chat_id for s in scopes] == [42]


def test_upgrade_to_v3_recovers_settings_from_retired_config_env(tmp_path):
    """On a machine that already restarted the daemon, the retired copy
    is the ONLY remaining record of the operator's port and URL."""
    _write_v2_config(scope.CONFIG_PATH)
    from aipager import config as config_mod
    retired = config_mod._XDG_CONFIG.parent / "config.env.retired.1786877400"
    retired.parent.mkdir(parents=True, exist_ok=True)
    retired.write_text(
        "MINIAPP_ENABLED=1\nMINIAPP_PORT=8765\n"
        "MINIAPP_PUBLIC_URL=https://recovered.test\n",
        encoding="utf-8",
    )

    assert migrate.upgrade_to_v3() is True

    cfg = scope.load_miniapp(scope.CONFIG_PATH)
    assert cfg["enabled"] is True
    assert cfg["port"] == 8765
    assert cfg["public_url"] == "https://recovered.test"


def test_upgrade_to_v3_never_reimports_stale_values(tmp_path):
    """Idempotent: once a miniapp block exists, a stale retired file must
    not overwrite it (otherwise `miniapp disable` would be undone by the
    next daemon start)."""
    _write_v2_config(scope.CONFIG_PATH)
    scope.dump_miniapp(
        {"enabled": False, "port": 9999, "public_url": ""}, scope.CONFIG_PATH,
    )
    from aipager import config as config_mod
    retired = config_mod._XDG_CONFIG.parent / "config.env.retired.1"
    retired.parent.mkdir(parents=True, exist_ok=True)
    retired.write_text("MINIAPP_ENABLED=1\nMINIAPP_PORT=1234\n", encoding="utf-8")

    assert migrate.upgrade_to_v3() is False
    cfg = scope.load_miniapp(scope.CONFIG_PATH)
    assert cfg["enabled"] is False
    assert cfg["port"] == 9999


def test_upgrade_to_v3_never_leaks_the_bot_token(tmp_path):
    """Retired config.env files contain CLAUDE_TG_BOT_TOKEN. The
    migration reads those files — it must extract only MINIAPP_* keys."""
    _write_v2_config(scope.CONFIG_PATH)
    from aipager import config as config_mod
    retired = config_mod._XDG_CONFIG.parent / "config.env.retired.1"
    retired.parent.mkdir(parents=True, exist_ok=True)
    retired.write_text(
        "CLAUDE_TG_BOT_TOKEN=secret-token-value\n"
        "CLAUDE_TG_CHAT_ID=999\nMINIAPP_ENABLED=1\n",
        encoding="utf-8",
    )

    migrate.upgrade_to_v3()
    written = scope.CONFIG_PATH.read_text(encoding="utf-8")
    assert "secret-token-value" not in written
    assert "CLAUDE_TG_CHAT_ID" not in written


# ===== the block must survive other writers ===============================

def test_dump_scopes_preserves_the_miniapp_block(tmp_path):
    """`aipager config` adding a scope calls dump_scopes, which rebuilds
    the document — it must not drop the Mini App settings."""
    _write_v2_config(scope.CONFIG_PATH)
    scope.dump_miniapp(
        {"enabled": True, "port": 8765, "public_url": "https://keep.test"},
        scope.CONFIG_PATH,
    )
    scopes, token = scope.load_scopes(scope.CONFIG_PATH)

    scope.dump_scopes(scopes, token, scope.CONFIG_PATH)

    cfg = scope.load_miniapp(scope.CONFIG_PATH)
    assert cfg["enabled"] is True
    assert cfg["public_url"] == "https://keep.test"


# ===== CLI surface unchanged ==============================================

def test_status_reports_persisted_state(tmp_path, monkeypatch, capsys):
    _write_v2_config(scope.CONFIG_PATH)
    monkeypatch.setattr(
        "aipager.miniapp.tunnel.detect_public_url", lambda: None,
    )
    miniapp_cli._cmd_miniapp_enable(_Args(port=9200, url="https://s.test"))
    capsys.readouterr()

    assert miniapp_cli._cmd_miniapp_status(_Args()) == 0
    out = capsys.readouterr().out
    assert "True" in out
    assert "9200" in out
    assert "https://s.test" in out


def test_disable_keeps_port_and_url_for_re_enable(tmp_path):
    _write_v2_config(scope.CONFIG_PATH)
    miniapp_cli._cmd_miniapp_enable(_Args(port=9300, url="https://d.test"))
    miniapp_cli._cmd_miniapp_disable(_Args())

    cfg = scope.load_miniapp(scope.CONFIG_PATH)
    assert cfg["enabled"] is False
    assert cfg["port"] == 9300
    assert cfg["public_url"] == "https://d.test"


def test_enable_without_url_keeps_existing_override(tmp_path):
    """`enable --port N` must not silently clear a configured URL."""
    _write_v2_config(scope.CONFIG_PATH)
    miniapp_cli._cmd_miniapp_enable(_Args(port=9400, url="https://keep.test"))
    miniapp_cli._cmd_miniapp_enable(_Args(port=9401, url=None))

    cfg = scope.load_miniapp(scope.CONFIG_PATH)
    assert cfg["port"] == 9401
    assert cfg["public_url"] == "https://keep.test"


def test_enable_rejects_non_https_url(tmp_path):
    _write_v2_config(scope.CONFIG_PATH)
    assert miniapp_cli._cmd_miniapp_enable(
        _Args(port=None, url="http://insecure.test"),
    ) == 2
    # Checked via public_url, not `enabled`: the latter read False only
    # because that was the old default, so asserting it now would test
    # the default rather than the rejection.
    assert scope.load_miniapp(scope.CONFIG_PATH)["public_url"] == ""


def test_enable_on_a_v1_install_still_works_and_migrates_later(tmp_path):
    """A fresh install that has never run `aipager config` has no
    aipager.yaml. `enable` must keep working there (writing config.env,
    exactly as stage 1 did) rather than erroring — and once the yaml
    appears, the daemon's upgrade path must carry the value across
    BEFORE retire_v1() can delete it.
    """
    assert not scope.CONFIG_PATH.exists()
    assert miniapp_cli._cmd_miniapp_enable(_Args(port=9500, url=None)) == 0
    assert miniapp_cli._current_miniapp_config()["port"] == 9500

    # Now the install gains a v2 config, as `aipager config` would create.
    _write_v2_config(scope.CONFIG_PATH)
    assert migrate.upgrade_to_v3() is True
    migrate.retire_v1()

    cfg = scope.load_miniapp(scope.CONFIG_PATH)
    assert cfg["enabled"] is True
    assert cfg["port"] == 9500


# ===== defaults are fail-closed ===========================================

@pytest.mark.parametrize("block", [
    {"enabled": "yes-ish", "port": "abc"},
    # The ones that matter most: a hand-edited config that plainly says
    # "off". bool("no") is True, so a bare bool() coercion would open a
    # listening socket for a config asking for the opposite.
    {"enabled": "no", "port": 8765},
    {"enabled": "false", "port": 8765},
    {"enabled": "off", "port": 8765},
    {"enabled": 0, "port": 8765},
    {"enabled": None, "port": 8765},
])
def test_malformed_miniapp_block_never_enables(tmp_path, block):
    """An explicit non-true value never enables the server.

    Narrowed deliberately when the Mini App became on-by-default. This
    used to cover the absent/unparseable cases too, on the old contract
    that "nothing short of a real YAML boolean true may enable the
    server". That contract is gone: a missing or malformed block now
    falls back to the default, which is ON. What survives — and is what
    actually protects an operator who turned this off — is that an
    explicit ``enabled:`` value of anything other than boolean true
    still reads as OFF. The absent-and-unparseable cases have their own
    test below, asserting the new behaviour openly rather than by
    omission.
    """
    _write_v2_config(scope.CONFIG_PATH)
    raw = yaml.safe_load(scope.CONFIG_PATH.read_text(encoding="utf-8"))
    raw["miniapp"] = block
    scope.CONFIG_PATH.write_text(yaml.safe_dump(raw), encoding="utf-8")

    cfg = scope.load_miniapp(scope.CONFIG_PATH)
    assert isinstance(cfg["port"], int)
    assert cfg["enabled"] is False


@pytest.mark.parametrize("block", [None, "not-a-dict", {}, {"port": 8765}])
def test_absent_or_unparseable_block_falls_back_to_the_default(tmp_path, block):
    """...which is now ON. Stated outright, because it is a real
    widening: a config that is missing, empty or corrupt starts the Mini
    App and with it a public tunnel. That was the deliberate trade for
    an opt-in nobody discovered — but it should be visible in a test,
    not inferred from the absence of one."""
    _write_v2_config(scope.CONFIG_PATH)
    raw = yaml.safe_load(scope.CONFIG_PATH.read_text(encoding="utf-8"))
    raw["miniapp"] = block
    scope.CONFIG_PATH.write_text(yaml.safe_dump(raw), encoding="utf-8")

    assert scope.load_miniapp(scope.CONFIG_PATH)["enabled"] is True


def test_an_explicit_false_survives_the_default_flip(tmp_path):
    """The one that matters most: anyone who deliberately turned this
    off must stay off. A default flip that ignores an explicit opt-out
    would be far worse than the problem it solves."""
    _write_v2_config(scope.CONFIG_PATH)
    raw = yaml.safe_load(scope.CONFIG_PATH.read_text(encoding="utf-8"))
    raw["miniapp"] = {"enabled": False, "port": 8765}
    scope.CONFIG_PATH.write_text(yaml.safe_dump(raw), encoding="utf-8")

    assert scope.load_miniapp(scope.CONFIG_PATH)["enabled"] is False


@pytest.mark.parametrize("value", [True, "yes", "on"])
def test_real_yaml_true_still_enables(tmp_path, value):
    """The fail-closed rule must not break the honest case: YAML parses
    bare `yes`/`on`/`true` to a real boolean before we ever see them."""
    _write_v2_config(scope.CONFIG_PATH)
    raw = yaml.safe_load(scope.CONFIG_PATH.read_text(encoding="utf-8"))
    # Emit unquoted so YAML's own boolean resolution applies.
    body = yaml.safe_dump(raw) + f"miniapp:\n  enabled: {value}\n  port: 8765\n"
    scope.CONFIG_PATH.write_text(body, encoding="utf-8")

    assert scope.load_miniapp(scope.CONFIG_PATH)["enabled"] is True


def test_upgrade_to_v3_backs_up_the_config_first(tmp_path):
    """The upgrade rewrites aipager.yaml (including schema_version) — a
    timestamped backup is the only way back if it mangles bot_token or
    scopes."""
    _write_v2_config(scope.CONFIG_PATH)
    from aipager import config as config_mod
    retired = config_mod._XDG_CONFIG.parent / "config.env.retired.1"
    retired.parent.mkdir(parents=True, exist_ok=True)
    retired.write_text("MINIAPP_ENABLED=1\n", encoding="utf-8")

    assert migrate.upgrade_to_v3() is True

    backups = list(scope.CONFIG_PATH.parent.glob("aipager.yaml.bak.*"))
    assert backups, "no .bak.<ts> copy of aipager.yaml was taken"
    saved = yaml.safe_load(backups[0].read_text(encoding="utf-8"))
    assert saved["schema_version"] == 2
    assert saved["bot_token"] == "123:abc"


def test_env_var_overrides_the_file(tmp_path, monkeypatch):
    _write_v2_config(scope.CONFIG_PATH)
    scope.dump_miniapp(
        {"enabled": False, "port": 8765, "public_url": ""}, scope.CONFIG_PATH,
    )
    monkeypatch.setenv("MINIAPP_ENABLED", "1")
    monkeypatch.setenv("MINIAPP_PORT", "7777")

    import aipager.config as config_mod
    try:
        importlib.reload(config_mod)
        assert config_mod.MINIAPP_ENABLED is True
        assert config_mod.MINIAPP_PORT == 7777
    finally:
        monkeypatch.undo()
        importlib.reload(config_mod)
