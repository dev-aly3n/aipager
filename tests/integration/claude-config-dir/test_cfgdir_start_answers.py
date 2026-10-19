"""Session-start answers go to the global config Claude Code reads
(design success criteria 3-5; amendment A1.2/A3; probe P2, P3, P5).

``prepare_session_start(folder, env, *, cwd=None)`` writes folder trust
(``projects[<real folder>].hasTrustDialogAccepted``), the API key answer
(``customApiKeyResponses``, last 20 characters of the key) and onboarding
(``hasCompletedOnboarding``), and returns what it changed.
"""

from __future__ import annotations

import os

import pytest

from aipager import claude_bootstrap, daemon_secrets

from _cfgdir_kit import KEY, LOGGED_IN, ONBOARDED, TAIL, rjson, wjson

CCD = "CLAUDE_CONFIG_DIR"
OAUTH = "CLAUDE_CODE_CUSTOM_OAUTH_URL"


def _trusted(path, folder) -> bool:
    entry = rjson(path).get("projects", {}).get(os.path.realpath(str(folder)), {})
    return entry.get("hasTrustDialogAccepted") is True


def _answers(path) -> dict:
    return rjson(path).get("customApiKeyResponses", {})


# ---- CLAUDE_CONFIG_DIR=/x: the answers land in /x ------------------------------

def test_trust_goes_to_the_config_dirs_file(cc):
    claude_bootstrap.prepare_session_start(str(cc.work), {CCD: str(cc.x)}, cwd=str(cc.work))
    assert _trusted(cc.x / ".claude.json", cc.work)


def test_trust_with_a_config_dir_never_creates_the_home_file(cc):
    claude_bootstrap.prepare_session_start(str(cc.work), {CCD: str(cc.x)}, cwd=str(cc.work))
    assert not cc.home_json.exists()


def test_trust_with_a_config_dir_leaves_an_existing_home_file_alone(cc):
    raw = '{"keep": true}'
    cc.home_json.write_text(raw)
    claude_bootstrap.prepare_session_start(str(cc.work), {CCD: str(cc.x)}, cwd=str(cc.work))
    assert cc.home_json.read_text() == raw


def test_the_trust_result_is_reported(cc):
    done = claude_bootstrap.prepare_session_start(
        str(cc.work), {CCD: str(cc.x)}, cwd=str(cc.work))
    assert "trusted" in done


def test_the_key_answer_goes_to_the_config_dirs_file(cc):
    wjson(cc.x / ".claude.json", ONBOARDED)
    claude_bootstrap.prepare_session_start(None, {CCD: str(cc.x), "ANTHROPIC_API_KEY": KEY})
    assert _answers(cc.x / ".claude.json")["approved"] == [TAIL]


def test_onboarding_goes_to_the_config_dirs_file(cc):
    claude_bootstrap.prepare_session_start(None, {CCD: str(cc.x), "ANTHROPIC_API_KEY": KEY})
    assert rjson(cc.x / ".claude.json").get("hasCompletedOnboarding") is True


def test_a_config_dir_in_daemon_env_is_followed(cc):
    """The env aipager's sessions get: process env plus daemon.env."""
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    claude_bootstrap.prepare_session_start(
        str(cc.work), daemon_secrets.build_session_env(), cwd=str(cc.work))
    assert _trusted(cc.x / ".claude.json", cc.work)


def test_a_config_dir_in_daemon_env_never_writes_the_home_file(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    claude_bootstrap.prepare_session_start(
        str(cc.work), daemon_secrets.build_session_env(), cwd=str(cc.work))
    assert not cc.home_json.exists()


def test_the_custom_oauth_file_gets_the_answers(cc):
    env = {CCD: str(cc.x), OAUTH: "https://login.example.com"}
    claude_bootstrap.prepare_session_start(str(cc.work), env, cwd=str(cc.work))
    assert _trusted(cc.x / ".claude-custom-oauth.json", cc.work)


def test_the_custom_oauth_case_leaves_the_plain_file_alone(cc):
    raw = '{"plain": true}'
    (cc.x / ".claude.json").write_text(raw)
    env = {CCD: str(cc.x), OAUTH: "https://login.example.com"}
    claude_bootstrap.prepare_session_start(str(cc.work), env, cwd=str(cc.work))
    assert (cc.x / ".claude.json").read_text() == raw


def test_a_nonempty_legacy_file_gets_the_answers(cc):
    wjson(cc.x / ".config.json", {"legacy": True})
    claude_bootstrap.prepare_session_start(str(cc.work), {CCD: str(cc.x)}, cwd=str(cc.work))
    assert _trusted(cc.x / ".config.json", cc.work)


def test_a_nonempty_legacy_file_keeps_its_own_keys(cc):
    wjson(cc.x / ".config.json", {"legacy": True})
    claude_bootstrap.prepare_session_start(str(cc.work), {CCD: str(cc.x)}, cwd=str(cc.work))
    assert rjson(cc.x / ".config.json")["legacy"] is True


def test_a_nonempty_legacy_file_leaves_the_new_style_file_alone(cc):
    wjson(cc.x / ".config.json", {"legacy": True})
    raw = '{"new": true}'
    (cc.x / ".claude.json").write_text(raw)
    claude_bootstrap.prepare_session_start(str(cc.work), {CCD: str(cc.x)}, cwd=str(cc.work))
    assert (cc.x / ".claude.json").read_text() == raw


def test_an_empty_writable_legacy_file_is_written(cc):
    """P3 b: Claude Code itself writes into an empty writable .config.json
    when no .claude.json exists (entrypoints: read as {})."""
    legacy = cc.x / ".config.json"
    legacy.write_bytes(b"")
    legacy.chmod(0o644)
    claude_bootstrap.prepare_session_start(str(cc.work), {CCD: str(cc.x)}, cwd=str(cc.work))
    assert _trusted(legacy, cc.work)


def test_an_empty_writable_legacy_file_means_no_new_style_file(cc):
    legacy = cc.x / ".config.json"
    legacy.write_bytes(b"")
    legacy.chmod(0o644)
    claude_bootstrap.prepare_session_start(str(cc.work), {CCD: str(cc.x)}, cwd=str(cc.work))
    assert not (cc.x / ".claude.json").exists()


def test_a_read_only_empty_legacy_file_is_not_used(cc):
    legacy = cc.x / ".config.json"
    legacy.write_bytes(b"")
    legacy.chmod(0o444)
    claude_bootstrap.prepare_session_start(str(cc.work), {CCD: str(cc.x)}, cwd=str(cc.work))
    assert _trusted(cc.x / ".claude.json", cc.work)


def test_an_empty_new_style_file_is_left_alone(cc):
    """Only the legacy target reads 0 bytes as {} (design: empty_ok)."""
    (cc.x / ".claude.json").write_bytes(b"")
    claude_bootstrap.prepare_session_start(str(cc.work), {CCD: str(cc.x)}, cwd=str(cc.work))
    assert (cc.x / ".claude.json").read_bytes() == b""


# ---- nothing set: the home file, as before ----------------------------------------

def test_without_a_config_dir_trust_goes_to_the_home_file(cc):
    claude_bootstrap.prepare_session_start(str(cc.work), {}, cwd=str(cc.work))
    assert _trusted(cc.home_json, cc.work)


def test_without_a_config_dir_the_key_answer_goes_to_the_home_file(cc):
    wjson(cc.home_json, LOGGED_IN)
    claude_bootstrap.prepare_session_start(None, {"ANTHROPIC_API_KEY": KEY})
    assert _answers(cc.home_json) == {"approved": [], "rejected": [TAIL]}


# ---- P2 / P5: a settings-env move does not move the global config -------------------

def test_a_settings_env_move_keeps_the_answers_in_the_home_file(cc):
    wjson(cc.home_settings, {"env": {CCD: str(cc.x)}})
    claude_bootstrap.prepare_session_start(str(cc.work), {}, cwd=str(cc.work))
    assert _trusted(cc.home_json, cc.work)


def test_a_settings_env_move_never_writes_the_moved_homes_file(cc):
    wjson(cc.home_settings, {"env": {CCD: str(cc.x)}})
    claude_bootstrap.prepare_session_start(str(cc.work), {}, cwd=str(cc.work))
    assert not (cc.x / ".claude.json").exists()


def test_an_empty_config_dir_keeps_the_answers_in_the_home_file(cc):
    """P3: CLAUDE_CONFIG_DIR="" -> the global config falls back to ~."""
    claude_bootstrap.prepare_session_start(str(cc.work), {CCD: ""}, cwd=str(cc.work))
    assert _trusted(cc.home_json, cc.work)


def test_a_non_text_settings_value_keeps_the_answers_in_the_default_file(cc):
    wjson(cc.home_settings, {"env": {CCD: 7}})
    claude_bootstrap.prepare_session_start(str(cc.work), {}, cwd=str(cc.work))
    assert _trusted(cc.home_json, cc.work)


# ---- refused_key_strands_sessions reads the resolved file ----------------------------

def test_a_refusal_in_the_config_dir_strands_sessions(cc):
    wjson(cc.x / ".claude.json", {**ONBOARDED,
                                  "customApiKeyResponses": {"approved": [], "rejected": [TAIL]}})
    assert claude_bootstrap.refused_key_strands_sessions(
        {CCD: str(cc.x), "ANTHROPIC_API_KEY": KEY}) is True


def test_a_refusal_only_in_the_home_file_does_not_count_with_a_config_dir(cc):
    wjson(cc.home_json, {**ONBOARDED,
                         "customApiKeyResponses": {"approved": [], "rejected": [TAIL]}})
    assert claude_bootstrap.refused_key_strands_sessions(
        {CCD: str(cc.x), "ANTHROPIC_API_KEY": KEY}) is False


# ---- keys on purpose: project, local, managed (criterion 4) ----------------------------

def test_an_inherited_key_next_to_a_login_is_declined(cc):
    """The control for the on-purpose cases below."""
    wjson(cc.home_json, LOGGED_IN)
    done = claude_bootstrap.prepare_session_start(
        None, {"ANTHROPIC_API_KEY": KEY}, cwd=str(cc.work))
    assert done == ["key-declined"]


@pytest.mark.parametrize("local", [False, True], ids=["project", "local"])
def test_a_key_in_the_trusted_folders_settings_is_on_purpose(cc, local):
    wjson(cc.home_json, LOGGED_IN)
    cc.project({"env": {"ANTHROPIC_API_KEY": KEY}}, local=local)
    claude_bootstrap.prepare_session_start(
        str(cc.work), {"ANTHROPIC_API_KEY": KEY}, cwd=str(cc.work))
    assert _answers(cc.home_json)["approved"] == [TAIL]


@pytest.mark.parametrize("local", [False, True], ids=["project", "local"])
def test_a_key_only_in_the_trusted_folders_settings_is_approved(cc, local):
    wjson(cc.home_json, LOGGED_IN)
    cc.project({"env": {"ANTHROPIC_API_KEY": KEY}}, local=local)
    claude_bootstrap.prepare_session_start(str(cc.work), {}, cwd=str(cc.work))
    assert _answers(cc.home_json)["approved"] == [TAIL]


@pytest.mark.parametrize("local", [False, True], ids=["project", "local"])
def test_an_untrusted_folders_key_is_not_on_purpose(cc, local):
    """folder=None (not being trusted) and cwd not trusted already."""
    wjson(cc.home_json, LOGGED_IN)
    cc.project({"env": {"ANTHROPIC_API_KEY": KEY}}, local=local)
    claude_bootstrap.prepare_session_start(
        None, {"ANTHROPIC_API_KEY": KEY}, cwd=str(cc.work))
    assert _answers(cc.home_json)["rejected"] == [TAIL]


def test_an_untrusted_folders_key_alone_gets_no_answer(cc):
    """Claude Code reads no project env in an untrusted folder: no key."""
    wjson(cc.home_json, LOGGED_IN)
    cc.project({"env": {"ANTHROPIC_API_KEY": KEY}})
    claude_bootstrap.prepare_session_start(None, {}, cwd=str(cc.work))
    assert "customApiKeyResponses" not in rjson(cc.home_json)


def test_an_already_trusted_cwd_counts_its_project_key(cc):
    """design: a holds-home cwd (folder=None) counts its project settings
    only when it is already trusted."""
    wjson(cc.home_json, {**LOGGED_IN, "projects": {
        os.path.realpath(str(cc.work)): {"hasTrustDialogAccepted": True}}})
    cc.project({"env": {"ANTHROPIC_API_KEY": KEY}})
    claude_bootstrap.prepare_session_start(
        None, {"ANTHROPIC_API_KEY": KEY}, cwd=str(cc.work))
    assert _answers(cc.home_json)["approved"] == [TAIL]


def test_a_managed_key_is_on_purpose(cc):
    wjson(cc.home_json, LOGGED_IN)
    cc.managed_file({"env": {"ANTHROPIC_API_KEY": KEY}})
    claude_bootstrap.prepare_session_start(
        None, {"ANTHROPIC_API_KEY": KEY}, cwd=str(cc.work))
    assert _answers(cc.home_json)["approved"] == [TAIL]


def test_a_managed_d_key_is_on_purpose(cc):
    wjson(cc.home_json, LOGGED_IN)
    cc.managed_file({"env": {"ANTHROPIC_API_KEY": KEY}}, "50-key.json")
    claude_bootstrap.prepare_session_start(None, {}, cwd=str(cc.work))
    assert _answers(cc.home_json)["approved"] == [TAIL]


def test_a_key_in_the_config_dirs_settings_is_on_purpose(cc):
    wjson(cc.x / ".claude.json", LOGGED_IN)
    wjson(cc.x / "settings.json", {"env": {"ANTHROPIC_API_KEY": KEY}})
    claude_bootstrap.prepare_session_start(None, {CCD: str(cc.x)}, cwd=str(cc.work))
    assert _answers(cc.x / ".claude.json")["approved"] == [TAIL]


def test_a_key_in_the_moved_settings_file_is_on_purpose(cc):
    """P2/P6: the moved settings file's env applies; answers stay in ~."""
    wjson(cc.home_json, LOGGED_IN)
    wjson(cc.home_settings, {"env": {CCD: str(cc.x)}})
    wjson(cc.x / "settings.json", {"env": {"ANTHROPIC_API_KEY": KEY}})
    claude_bootstrap.prepare_session_start(None, {}, cwd=str(cc.work))
    assert _answers(cc.home_json)["approved"] == [TAIL]


@pytest.mark.parametrize("switch", ["CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"],
                         ids=["bedrock", "vertex"])
def test_a_managed_cloud_switch_suppresses_the_key_answer(cc, switch):
    wjson(cc.home_json, ONBOARDED)
    cc.managed_file({"env": {switch: "1"}})
    claude_bootstrap.prepare_session_start(
        None, {"ANTHROPIC_API_KEY": KEY}, cwd=str(cc.work))
    assert "customApiKeyResponses" not in rjson(cc.home_json)


def test_without_a_cloud_switch_the_same_key_is_answered(cc):
    """Control for the cloud-switch case."""
    wjson(cc.home_json, ONBOARDED)
    claude_bootstrap.prepare_session_start(
        None, {"ANTHROPIC_API_KEY": KEY}, cwd=str(cc.work))
    assert _answers(cc.home_json)["approved"] == [TAIL]


def test_a_managed_api_key_helper_counts_as_another_login(cc):
    wjson(cc.home_json, ONBOARDED)
    cc.managed_file({"apiKeyHelper": "/opt/company/key-helper"})
    claude_bootstrap.prepare_session_start(
        None, {"ANTHROPIC_API_KEY": KEY}, cwd=str(cc.work))
    assert _answers(cc.home_json)["rejected"] == [TAIL]


def test_a_login_file_in_the_config_dir_counts_as_another_login(cc):
    wjson(cc.x / ".claude.json", ONBOARDED)
    (cc.x / ".credentials.json").write_text("{}")
    claude_bootstrap.prepare_session_start(
        None, {CCD: str(cc.x), "ANTHROPIC_API_KEY": KEY}, cwd=str(cc.work))
    assert _answers(cc.x / ".claude.json")["rejected"] == [TAIL]


def test_a_login_file_only_in_the_home_does_not_count_with_a_config_dir(cc):
    wjson(cc.x / ".claude.json", ONBOARDED)
    (cc.dot_claude / ".credentials.json").write_text("{}")
    claude_bootstrap.prepare_session_start(
        None, {CCD: str(cc.x), "ANTHROPIC_API_KEY": KEY}, cwd=str(cc.work))
    assert _answers(cc.x / ".claude.json")["approved"] == [TAIL]


# ---- malformed files: nothing written over them (criterion 5) ----------------------

@pytest.mark.parametrize("content", [b"{not json", b"[1, 2]", b"\"text\""],
                         ids=["invalid-json", "list", "string"])
def test_a_malformed_global_config_is_not_written(cc, content):
    target = cc.x / ".claude.json"
    target.write_bytes(content)
    claude_bootstrap.prepare_session_start(
        str(cc.work), {CCD: str(cc.x), "ANTHROPIC_API_KEY": KEY}, cwd=str(cc.work))
    assert target.read_bytes() == content


def test_a_malformed_global_config_reports_no_change(cc):
    (cc.x / ".claude.json").write_bytes(b"{not json")
    done = claude_bootstrap.prepare_session_start(
        str(cc.work), {CCD: str(cc.x), "ANTHROPIC_API_KEY": KEY}, cwd=str(cc.work))
    assert done == []


def test_a_malformed_legacy_file_is_not_written(cc):
    legacy = cc.x / ".config.json"
    legacy.write_bytes(b"{legacy but broken")
    claude_bootstrap.prepare_session_start(str(cc.work), {CCD: str(cc.x)}, cwd=str(cc.work))
    assert legacy.read_bytes() == b"{legacy but broken"


def test_a_malformed_managed_file_does_not_stop_the_answers(cc):
    wjson(cc.home_json, ONBOARDED)
    cc.managed.mkdir(parents=True)
    (cc.managed / "managed-settings.json").write_text("{bad")
    claude_bootstrap.prepare_session_start(
        None, {"ANTHROPIC_API_KEY": KEY}, cwd=str(cc.work))
    assert _answers(cc.home_json)["approved"] == [TAIL]


def test_a_malformed_project_file_does_not_stop_the_answers(cc):
    wjson(cc.home_json, ONBOARDED)
    (cc.work / ".claude").mkdir()
    (cc.work / ".claude" / "settings.json").write_text("{bad")
    claude_bootstrap.prepare_session_start(
        str(cc.work), {"ANTHROPIC_API_KEY": KEY}, cwd=str(cc.work))
    assert _answers(cc.home_json)["approved"] == [TAIL]


def test_an_unwritable_config_dir_never_raises(cc):
    locked = cc.tmp / "locked-cfg"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        if os.access(locked, os.W_OK):
            pytest.skip("chmod 500 does not make a folder unwritable here (root?)")
        done = claude_bootstrap.prepare_session_start(
            str(cc.work), {CCD: str(locked)}, cwd=str(cc.work))
    finally:
        locked.chmod(0o755)
    assert done == []


# ---- ensure_folder_trusted --------------------------------------------------------

def test_ensure_folder_trusted_writes_the_config_dirs_file(cc):
    claude_bootstrap.ensure_folder_trusted(str(cc.work), {CCD: str(cc.x)})
    assert _trusted(cc.x / ".claude.json", cc.work)


def test_ensure_folder_trusted_defaults_to_the_sessions_env(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    claude_bootstrap.ensure_folder_trusted(str(cc.work))
    assert _trusted(cc.x / ".claude.json", cc.work)
