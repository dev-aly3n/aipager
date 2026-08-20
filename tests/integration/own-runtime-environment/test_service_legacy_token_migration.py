"""design.md success criterion 14:

    "A legacy config.env holding a token, with no daemon.env, produces
    daemon.env at 0600 with the value copied forward, before rendering."

design.md section 7's exact mechanism: "Before overwriting, best-effort
parse the *existing* unit's EnvironmentFile= value; if that file holds
a token line and daemon.env does not exist yet, copy it forward
(0600)." This test drives that mechanism through the path it actually
documents (the OLD unit's EnvironmentFile= line), rather than guessing
at an undocumented constant for where "legacy config.env" lives on
disk -- entrypoints.md does not name that path, but it does fully
describe the unit-parsing mechanism.
"""
from __future__ import annotations

import stat

import aipager.service as service_mod


def test_legacy_token_copied_forward_to_daemon_env_before_rendering(
    tmp_path, monkeypatch,
):
    legacy_env_path = tmp_path / "legacy_config.env"
    legacy_env_path.write_text(
        "# old-style env file\n"
        "SOME_OTHER_VAR=ignored\n"
        "CLAUDE_CODE_OAUTH_TOKEN=legacy-token-xyz789\n",
        encoding="utf-8",
    )

    service_mod.LINUX_UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    service_mod.LINUX_UNIT_PATH.write_text(
        "[Unit]\n"
        "Description=old hand-written unit\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"EnvironmentFile=-{legacy_env_path}\n",
        encoding="utf-8",
    )

    assert not service_mod.DAEMON_ENV_PATH.exists(), (
        "test isolation bug: daemon.env already exists before this test"
    )

    def _explode(*a, **k):
        raise AssertionError("no prompt should be needed for --yes")
    monkeypatch.setattr("builtins.input", _explode)

    rc = service_mod._install_linux(yes=True)

    assert rc == 0
    assert service_mod.DAEMON_ENV_PATH.exists(), (
        "daemon.env was not created from the legacy EnvironmentFile= token"
    )
    content = service_mod.DAEMON_ENV_PATH.read_text()
    assert "CLAUDE_CODE_OAUTH_TOKEN=legacy-token-xyz789" in content

    mode = stat.S_IMODE(service_mod.DAEMON_ENV_PATH.stat().st_mode)
    assert mode == 0o600, f"daemon.env must be 0600, got {oct(mode)}"


def test_no_legacy_token_still_creates_an_empty_daemon_env(tmp_path, monkeypatch):
    """Design.md: LoadCredential= requires its source to exist (no '-'
    soft-fail prefix), so daemon.env must ALWAYS end up existing even
    with nothing to copy forward -- otherwise the unit fails to start,
    which is exactly the 'refuse to launch' outcome this feature exists
    to prevent.
    """
    assert not service_mod.LINUX_UNIT_PATH.exists()
    assert not service_mod.DAEMON_ENV_PATH.exists()

    rc = service_mod._install_linux(yes=True)

    assert rc == 0
    assert service_mod.DAEMON_ENV_PATH.exists(), (
        "daemon.env must be created (even empty) so LoadCredential= never "
        "fails the unit to start"
    )
    mode = stat.S_IMODE(service_mod.DAEMON_ENV_PATH.stat().st_mode)
    assert mode == 0o600
