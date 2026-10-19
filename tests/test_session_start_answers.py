"""Claude Code's start-up questions are answered before a session starts.

A session started from Telegram cannot answer a question Claude Code opens
with: the first message lands on it as Enter. Measured with real Claude
Code 2.1.302 in a throwaway home against a fake provider: an
``ANTHROPIC_API_KEY`` whose last 20 characters (trimmed) are in neither
``customApiKeyResponses.approved`` nor ``.rejected`` of ~/.claude.json
opens on "Do you want to use this API key?" with No preselected, so the
first message refused the key for good; a home without
``hasCompletedOnboarding: true`` opens on the theme picker.
``claude_bootstrap.prepare_session_start`` answers both, as the owner
would, without moving a subscription user to per-token billing for a key
they only exported for something else.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aipager import claude_bootstrap, daemon_secrets
from aipager.dtach import inject as dtach_inject
from aipager import claude_paths

KEY = "sk-ant-api03-" + "A" * 30 + "tail-of-the-key-0001"
TAIL = KEY[-20:]

_SCRUB = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
          "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
          "CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
          "CLAUDE_CODE_USE_MANTLE", "CLAUDE_CONFIG_DIR", "ANTHROPIC_BASE_URL",
          "CREDENTIALS_DIRECTORY")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """~/.claude.json, ~/.claude/settings.json and daemon.env in tmp_path,
    and none of the invoker's own credentials in the environment."""
    for var in _SCRUB:
        monkeypatch.delenv(var, raising=False)
    claude_dir = tmp_path / "home" / ".claude"
    claude_dir.mkdir(parents=True)
    paths = {
        "claude_json": tmp_path / "home" / ".claude.json",
        "settings": claude_dir / "settings.json",
        "credentials": claude_dir / ".credentials.json",
        "daemon_env": tmp_path / "cfg" / "daemon.env",
    }
    paths["daemon_env"].parent.mkdir()
    monkeypatch.setattr(claude_paths, "DEFAULT_GLOBAL_DIR", Path(paths["claude_json"]).parent)
    monkeypatch.setattr(claude_paths, "DEFAULT_CONFIG_HOME", Path(paths["settings"]).parent)
    monkeypatch.setattr(daemon_secrets, "DAEMON_ENV_PATH", paths["daemon_env"])
    monkeypatch.setattr(daemon_secrets, "_recovered", {})
    return paths


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _answers(path: Path) -> dict:
    return _read(path).get("customApiKeyResponses", {})


ONBOARDED = {"hasCompletedOnboarding": True}
LOGGED_IN = {"hasCompletedOnboarding": True, "oauthAccount": {"emailAddress": "alice@example.com"}}


# ---- the decision table ------------------------------------------------------

@pytest.mark.parametrize("state,on_purpose,other,want", [
    ("approved", True, True, None),
    ("approved", False, False, None),
    ("new", True, True, "approve"),
    ("new", True, False, "approve"),
    ("new", False, False, "approve"),
    ("new", False, True, "decline"),
    ("rejected", True, False, "approve"),
    ("rejected", True, True, None),
    ("rejected", False, False, None),
    ("rejected", False, True, None),
], ids=lambda v: str(v))
def test_api_key_answer_table(state, on_purpose, other, want):
    assert claude_bootstrap.api_key_answer(state, on_purpose, other) == want


# ---- a new key ------------------------------------------------------------------

def test_a_key_in_daemon_env_is_approved_even_next_to_a_claude_login(home):
    home["daemon_env"].write_text(f"ANTHROPIC_API_KEY={KEY}\n")
    _write(home["claude_json"], LOGGED_IN)

    done = claude_bootstrap.prepare_session_start(
        None, daemon_secrets.build_session_env({}))

    assert done == ["key-approved"]
    assert _answers(home["claude_json"]) == {"approved": [TAIL], "rejected": []}
    assert _read(home["claude_json"])["oauthAccount"] == LOGGED_IN["oauthAccount"]


def test_a_key_in_claude_settings_is_approved(home):
    _write(home["settings"], {"env": {"ANTHROPIC_API_KEY": KEY}})
    _write(home["claude_json"], LOGGED_IN)

    assert claude_bootstrap.prepare_session_start(None, {}) == ["key-approved"]
    assert _answers(home["claude_json"])["approved"] == [TAIL]


def test_an_inherited_key_is_approved_when_nothing_else_logs_in(home):
    _write(home["claude_json"], ONBOARDED)

    assert claude_bootstrap.prepare_session_start(None, {"ANTHROPIC_API_KEY": KEY}) == [
        "key-approved"]
    assert _answers(home["claude_json"])["approved"] == [TAIL]


def test_an_inherited_key_is_declined_next_to_a_claude_login(home):
    """A subscription user who exports an API key for something else stays
    on the subscription: Claude Code's own recommended answer, recorded
    before the first message could be swallowed by the question."""
    _write(home["claude_json"], LOGGED_IN)

    assert claude_bootstrap.prepare_session_start(None, {"ANTHROPIC_API_KEY": KEY}) == [
        "key-declined"]
    assert _answers(home["claude_json"]) == {"approved": [], "rejected": [TAIL]}


@pytest.mark.parametrize("signal", [
    "oauth token", "auth token", "auth token in settings", "oauthAccount",
    "saved console key", "apiKeyHelper", "login file",
])
def test_each_other_login_makes_an_inherited_key_declined(home, signal):
    env = {"ANTHROPIC_API_KEY": KEY}
    data: dict = dict(ONBOARDED)
    settings: dict = {}
    if signal == "oauth token":
        env["CLAUDE_CODE_OAUTH_TOKEN"] = "sk-ant-oat01-x"
    elif signal == "auth token":
        env["ANTHROPIC_AUTH_TOKEN"] = "gw-token"
    elif signal == "auth token in settings":
        settings = {"env": {"ANTHROPIC_AUTH_TOKEN": "gw-token"}}
    elif signal == "oauthAccount":
        data["oauthAccount"] = {"accountUuid": "u"}
    elif signal == "saved console key":
        data["primaryApiKey"] = "sk-ant-saved"
    elif signal == "apiKeyHelper":
        settings = {"apiKeyHelper": "/home/alice/bin/key"}
    elif signal == "login file":
        home["credentials"].write_text("{}")
    _write(home["claude_json"], data)
    if settings:
        _write(home["settings"], settings)

    assert claude_bootstrap.prepare_session_start(None, env) == ["key-declined"]
    assert _answers(home["claude_json"])["rejected"] == [TAIL]


@pytest.mark.parametrize("account", [{}, None, "x"], ids=["empty", "null", "text"])
def test_an_empty_or_odd_oauth_account_is_no_login(home, account):
    _write(home["claude_json"], {**ONBOARDED, "oauthAccount": account})

    assert claude_bootstrap.prepare_session_start(None, {"ANTHROPIC_API_KEY": KEY}) == [
        "key-approved"]


def test_a_key_from_the_recovered_overlay_counts_as_set_on_purpose(home, monkeypatch):
    """The startup sweep's overlay carries a key read from daemon.env."""
    monkeypatch.setattr(daemon_secrets, "_recovered", {"ANTHROPIC_API_KEY": KEY})
    _write(home["claude_json"], LOGGED_IN)

    done = claude_bootstrap.prepare_session_start(None, daemon_secrets.build_session_env({}))

    assert done == ["key-approved"]


# ---- a key answered before ---------------------------------------------------

def test_a_tail_in_both_lists_counts_as_approved(home):
    """Claude Code checks approved first, and nothing deduplicates the lists."""
    raw = json.dumps({**ONBOARDED, "customApiKeyResponses": {"approved": [TAIL],
                                                                "rejected": [TAIL]}})
    home["claude_json"].write_text(raw)

    assert claude_bootstrap.prepare_session_start(None, {"ANTHROPIC_API_KEY": KEY}) == []
    assert home["claude_json"].read_text() == raw
    assert claude_bootstrap.refused_key_strands_sessions({"ANTHROPIC_API_KEY": KEY}) is False


def test_an_approved_key_is_left_alone(home):
    raw = json.dumps({**LOGGED_IN, "customApiKeyResponses": {"approved": [TAIL],
                                                                "rejected": []}})
    home["claude_json"].write_text(raw)

    assert claude_bootstrap.prepare_session_start(None, {"ANTHROPIC_API_KEY": KEY}) == []
    assert home["claude_json"].read_text() == raw


def test_a_refused_key_set_on_purpose_is_approved_when_nothing_else_logs_in(home):
    """The refusal may be the first Telegram message having answered the
    question; without the key no session can log in at all."""
    home["daemon_env"].write_text(f"ANTHROPIC_API_KEY={KEY}\n")
    _write(home["claude_json"], {**ONBOARDED, "customApiKeyResponses": {
        "approved": ["other-key-tail-00001"], "rejected": ["x", TAIL, TAIL]}})

    done = claude_bootstrap.prepare_session_start(None, daemon_secrets.build_session_env({}))

    assert done == ["key-approved"]
    assert _answers(home["claude_json"]) == {"approved": ["other-key-tail-00001", TAIL],
                                             "rejected": ["x"]}


def test_a_refused_key_set_on_purpose_stays_refused_next_to_a_login(home, caplog):
    home["daemon_env"].write_text(f"ANTHROPIC_API_KEY={KEY}\n")
    raw = json.dumps({**LOGGED_IN, "customApiKeyResponses": {"approved": [],
                                                                "rejected": [TAIL]}})
    home["claude_json"].write_text(raw)

    with caplog.at_level(logging.INFO, logger="aipager.claude_bootstrap"):
        done = claude_bootstrap.prepare_session_start(None, daemon_secrets.build_session_env({}))

    assert done == []
    assert home["claude_json"].read_text() == raw
    assert "sessions use the other login" in caplog.text


def test_a_refused_inherited_key_next_to_a_login_is_left_quietly(home, caplog):
    """The owner's own No, and sessions log in another way: nothing to do and
    nothing to say."""
    raw = json.dumps({**LOGGED_IN, "customApiKeyResponses": {"approved": [],
                                                                "rejected": [TAIL]}})
    home["claude_json"].write_text(raw)

    with caplog.at_level(logging.INFO, logger="aipager.claude_bootstrap"):
        assert claude_bootstrap.prepare_session_start(None, {"ANTHROPIC_API_KEY": KEY}) == []

    assert home["claude_json"].read_text() == raw
    assert "ANTHROPIC_API_KEY" not in caplog.text


def test_a_refused_inherited_key_stays_refused_and_the_log_says_why(home, caplog):
    raw = json.dumps({**ONBOARDED, "customApiKeyResponses": {"approved": [],
                                                                "rejected": [TAIL]}})
    home["claude_json"].write_text(raw)

    with caplog.at_level(logging.INFO, logger="aipager.claude_bootstrap"):
        done = claude_bootstrap.prepare_session_start(None, {"ANTHROPIC_API_KEY": KEY})

    assert done == []
    assert home["claude_json"].read_text() == raw
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warned and "Not logged in" in warned[0].getMessage()


# ---- which key, and when there is no question -------------------------------

def test_the_tail_is_taken_from_the_trimmed_key(home):
    _write(home["claude_json"], ONBOARDED)

    claude_bootstrap.prepare_session_start(None, {"ANTHROPIC_API_KEY": f"  {KEY}\t\n"})

    assert _answers(home["claude_json"])["approved"] == [TAIL]


def test_a_short_key_is_recorded_whole(home):
    _write(home["claude_json"], ONBOARDED)

    claude_bootstrap.prepare_session_start(None, {"ANTHROPIC_API_KEY": "short-key"})

    assert _answers(home["claude_json"])["approved"] == ["short-key"]


def test_claude_settings_env_wins_over_the_session_env(home):
    """Claude Code lays the settings env block over its environment, so an
    empty key there (OpenRouter's and Ollama's guides set one) means no key
    and no question."""
    _write(home["settings"], {"env": {"ANTHROPIC_API_KEY": ""}})
    raw = json.dumps(ONBOARDED)
    home["claude_json"].write_text(raw)

    assert claude_bootstrap.prepare_session_start(None, {"ANTHROPIC_API_KEY": KEY}) == []
    assert home["claude_json"].read_text() == raw


@pytest.mark.parametrize("switch", ["CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                                    "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_ANTHROPIC_AWS",
                                    "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
                                    "CLAUDE_CODE_USE_MANTLE"])
def test_no_key_answer_on_a_cloud_provider(home, switch):
    raw = json.dumps(ONBOARDED)
    home["claude_json"].write_text(raw)

    assert claude_bootstrap.prepare_session_start(
        None, {"ANTHROPIC_API_KEY": KEY, switch: "1"}) == []
    assert home["claude_json"].read_text() == raw


@pytest.mark.parametrize("value", ["0", "off", "2", "enabled", "", " "])
def test_a_switch_claude_code_reads_as_off_does_not_count(home, value):
    """Claude Code 2.1.303 reads a switch as on only for 1, true, yes or on;
    for anything else it stays on its own API and asks about the key."""
    _write(home["claude_json"], ONBOARDED)

    assert claude_bootstrap.prepare_session_start(
        None, {"ANTHROPIC_API_KEY": KEY, "CLAUDE_CODE_USE_BEDROCK": value}) == ["key-approved"]


@pytest.mark.parametrize("value", ["1", "true", "YES", " on "])
def test_a_switch_claude_code_reads_as_on_counts(home, value):
    raw = json.dumps(ONBOARDED)
    home["claude_json"].write_text(raw)

    assert claude_bootstrap.prepare_session_start(
        None, {"ANTHROPIC_API_KEY": KEY, "CLAUDE_CODE_USE_VERTEX": value}) == []
    assert home["claude_json"].read_text() == raw


def test_claude_config_dir_answers_go_to_the_config_dir_file(home, tmp_path):
    """With CLAUDE_CONFIG_DIR set Claude Code reads $CLAUDE_CONFIG_DIR/.claude.json
    (measured with 2.1.303), so the answers go there and ~/.claude.json is
    left as it was."""
    folder = tmp_path / "proj"
    folder.mkdir()
    home["claude_json"].write_text("{}")
    cfgdir = tmp_path / "cfgdir"

    done = claude_bootstrap.prepare_session_start(
        str(folder), {"ANTHROPIC_API_KEY": KEY, "CLAUDE_CONFIG_DIR": str(cfgdir)})

    assert done == ["trusted", "key-approved", "onboarded"]
    assert home["claude_json"].read_text() == "{}"
    data = _read(cfgdir / ".claude.json")
    assert data["customApiKeyResponses"]["approved"] == [TAIL]
    assert data["hasCompletedOnboarding"] is True
    assert data["projects"][str(folder.resolve())]["hasTrustDialogAccepted"] is True


@pytest.mark.parametrize("record", [
    ["not", "a", "dict"], {"approved": "x", "rejected": []}, {"approved": [], "rejected": 3},
    {"approved": "", "rejected": []}, {"approved": {}, "rejected": []},
    {"approved": [], "rejected": 0}, {"approved": False},
], ids=["list", "approved-text", "rejected-number", "approved-empty-text", "approved-empty-dict",
        "rejected-zero", "approved-false"])
def test_an_odd_record_is_left_alone_and_trust_still_written(home, tmp_path, caplog, record):
    folder = tmp_path / "proj"
    folder.mkdir()
    _write(home["claude_json"], {**ONBOARDED, "customApiKeyResponses": record})

    with caplog.at_level(logging.WARNING, logger="aipager.claude_bootstrap"):
        done = claude_bootstrap.prepare_session_start(str(folder), {"ANTHROPIC_API_KEY": KEY})

    assert done == ["trusted"]
    assert _read(home["claude_json"])["customApiKeyResponses"] == record
    assert "not in the expected shape" in caplog.text


def test_other_entries_are_kept_in_order(home):
    _write(home["claude_json"], {**ONBOARDED, "customApiKeyResponses": {
        "approved": ["first-tail-000000001", 7, "second-tail-00000002"],
        "rejected": ["third-tail-000000003"], "extra": True}})

    claude_bootstrap.prepare_session_start(None, {"ANTHROPIC_API_KEY": KEY})

    assert _answers(home["claude_json"]) == {
        "approved": ["first-tail-000000001", 7, "second-tail-00000002", TAIL],
        "rejected": ["third-tail-000000003"], "extra": True}


def test_neither_the_key_nor_its_tail_reaches_the_log(home, caplog, tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    with caplog.at_level(logging.DEBUG):
        for data in ({}, LOGGED_IN, {"customApiKeyResponses": {"rejected": [TAIL]}},
                     {"customApiKeyResponses": "odd"}):
            _write(home["claude_json"], data)
            claude_bootstrap.prepare_session_start(str(folder), {"ANTHROPIC_API_KEY": KEY})
    assert caplog.records
    assert TAIL not in caplog.text and KEY not in caplog.text


# ---- first-run screens --------------------------------------------------------

@pytest.mark.parametrize("env,data,settings", [
    ({"ANTHROPIC_API_KEY": KEY}, {}, {}),
    ({"ANTHROPIC_AUTH_TOKEN": "gw-token"}, {}, {}),
    ({"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-x"}, {}, {}),
    ({"CLAUDE_CODE_USE_BEDROCK": "1"}, {}, {}),
    ({}, {"oauthAccount": {"accountUuid": "u"}}, {}),
    ({}, {}, {"apiKeyHelper": "/home/alice/bin/key"}),
    ({}, {}, {"env": {"ANTHROPIC_AUTH_TOKEN": "gw-token"}}),
], ids=["api-key", "auth-token", "oauth-token", "bedrock", "saved-login", "helper",
        "settings-token"])
def test_first_run_is_marked_done_when_the_session_has_a_credential(home, env, data,
                                                                    settings):
    _write(home["claude_json"], data)
    if settings:
        _write(home["settings"], settings)

    done = claude_bootstrap.prepare_session_start(None, env)

    assert "onboarded" in done
    assert _read(home["claude_json"])["hasCompletedOnboarding"] is True


def test_a_key_approved_before_counts_as_the_sessions_credential(home):
    """The owner said yes to the key in a terminal once, but Claude Code's
    first run never finished there (a new container): the key is what logs
    the session in, so the first run is marked done."""
    _write(home["claude_json"], {"customApiKeyResponses": {"approved": [TAIL], "rejected": []}})

    assert claude_bootstrap.prepare_session_start(None, {"ANTHROPIC_API_KEY": KEY}) == [
        "onboarded"]
    assert _read(home["claude_json"])["hasCompletedOnboarding"] is True


def test_first_run_is_left_when_nothing_logs_in(home):
    """Then the owner has to log in from a terminal, which finishes the
    first run itself."""
    home["claude_json"].write_text("{}")

    assert claude_bootstrap.prepare_session_start(None, {}) == []
    assert home["claude_json"].read_text() == "{}"


def test_first_run_is_left_when_the_only_key_stays_refused(home):
    raw = json.dumps({"customApiKeyResponses": {"approved": [], "rejected": [TAIL]}})
    home["claude_json"].write_text(raw)

    assert claude_bootstrap.prepare_session_start(None, {"ANTHROPIC_API_KEY": KEY}) == []
    assert home["claude_json"].read_text() == raw


def test_trust_key_and_first_run_go_in_one_write(home, tmp_path, monkeypatch):
    folder = tmp_path / "proj"
    folder.mkdir()
    replaced = []
    real = claude_bootstrap._replace
    monkeypatch.setattr(claude_bootstrap, "_replace",
                        lambda src, dst: (replaced.append(dst), real(src, dst)))

    done = claude_bootstrap.prepare_session_start(str(folder), {"ANTHROPIC_API_KEY": KEY})

    assert done == ["trusted", "key-approved", "onboarded"]
    assert len(replaced) == 1
    data = _read(home["claude_json"])
    assert data["projects"][str(folder)]["hasTrustDialogAccepted"] is True


def test_nothing_is_reported_when_the_file_cannot_be_written(home, tmp_path, monkeypatch):
    """The answers were worked out but the write failed: the launch log must
    not say Claude Code was told anything."""
    raw = json.dumps(ONBOARDED)
    home["claude_json"].write_text(raw)

    def _fail(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(claude_bootstrap, "_replace", _fail)

    assert claude_bootstrap.prepare_session_start(str(tmp_path), {"ANTHROPIC_API_KEY": KEY}) == []
    assert home["claude_json"].read_text() == raw


def test_a_file_that_is_not_json_is_left_and_nothing_reported(home, tmp_path):
    home["claude_json"].write_bytes(b"{ half")

    assert claude_bootstrap.prepare_session_start(str(tmp_path), {"ANTHROPIC_API_KEY": KEY}) == []
    assert home["claude_json"].read_bytes() == b"{ half"


def test_a_change_by_claude_meanwhile_is_kept_and_reported_once(home, tmp_path, monkeypatch):
    """Every running Claude rewrites the file; the write starts over on the
    newer file, and the answers are reported once, not once per try."""
    folder = tmp_path / "proj"
    folder.mkdir()
    home["claude_json"].write_text("{}")
    calls = {"n": 0}

    def _before_replace(path):
        calls["n"] += 1
        if calls["n"] == 1:
            path.write_text(json.dumps({"numStartups": 7}))

    monkeypatch.setattr(claude_bootstrap, "_before_replace", _before_replace)

    done = claude_bootstrap.prepare_session_start(str(folder), {"ANTHROPIC_API_KEY": KEY})

    assert done == ["trusted", "key-approved", "onboarded"]
    assert calls["n"] == 2
    data = _read(home["claude_json"])
    assert data["numStartups"] == 7
    assert data["customApiKeyResponses"]["approved"] == [TAIL]


# ---- doctor's read-only check ----------------------------------------------------

@pytest.mark.parametrize("case,want", [
    ("refused, inherited, no login", True),
    ("refused, inherited, logged in", False),
    ("refused, in daemon.env, no login", False),
    ("approved", False),
    ("new", False),
    ("no key", False),
    ("refused in ~/.claude.json, but CLAUDE_CONFIG_DIR moves the file", False),
    ("refused in CLAUDE_CONFIG_DIR's file", True),
])
def test_refused_key_strands_sessions(home, case, want):
    """The doctor check behind "sessions answer Not logged in"; with
    CLAUDE_CONFIG_DIR set Claude Code reads $CLAUDE_CONFIG_DIR/.claude.json
    instead of ~/.claude.json."""
    env = {} if case == "no key" else {"ANTHROPIC_API_KEY": KEY}
    cfgdir = home["daemon_env"].parent.parent / "cfgdir"
    if "CLAUDE_CONFIG_DIR" in case:
        env["CLAUDE_CONFIG_DIR"] = str(cfgdir)
        cfgdir.mkdir()
        _write(cfgdir / ".claude.json", {})
    data: dict = {"customApiKeyResponses": {"approved": [], "rejected": [TAIL]}}
    if case == "approved":
        data = {"customApiKeyResponses": {"approved": [TAIL], "rejected": []}}
    elif case == "new":
        data = {}
    elif case == "refused, inherited, logged in":
        data["oauthAccount"] = {"accountUuid": "u"}
    elif case == "refused, in daemon.env, no login":
        home["daemon_env"].write_text(f"ANTHROPIC_API_KEY={KEY}\n")
    _write(cfgdir / ".claude.json" if case.endswith("CLAUDE_CONFIG_DIR's file")
           else home["claude_json"], data)

    assert claude_bootstrap.refused_key_strands_sessions(env) is want


# ---- launch_session ---------------------------------------------------------------

def _spawn_recorder(monkeypatch, seen: dict, claude_json: Path):
    async def _fake_exec(*args, **kwargs):
        seen["env"] = kwargs.get("env")
        seen["claude_json_at_spawn"] = (claude_json.read_bytes()
                                        if claude_json.exists() else None)
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    monkeypatch.setattr(dtach_inject, "_create_subprocess_exec", _fake_exec)
    calls = {"n": 0}

    def _is_socket(self):
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(dtach_inject.Path, "is_socket", _is_socket)


def test_launch_answers_before_the_session_starts_with_the_env_it_gets(
        home, tmp_path, monkeypatch, run_async):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    folder = tmp_path / "proj"
    folder.mkdir()
    seen_env: list = []
    real = claude_bootstrap.prepare_session_start
    monkeypatch.setattr(claude_bootstrap, "prepare_session_start",
                        lambda f, env, **kw: (seen_env.append(env), real(f, env, **kw))[1])
    seen: dict = {}
    _spawn_recorder(monkeypatch, seen, home["claude_json"])

    ok, err = run_async(dtach_inject.launch_session("pk1", mode="ask", cwd=str(folder)))

    assert ok, err
    assert seen["env"] is seen_env[0]
    at_spawn = json.loads(seen["claude_json_at_spawn"])
    assert at_spawn["customApiKeyResponses"]["approved"] == [TAIL]
    assert at_spawn["hasCompletedOnboarding"] is True
    assert at_spawn["projects"][str(folder)]["hasTrustDialogAccepted"] is True


def test_launch_above_home_answers_the_key_but_trusts_nothing(
        home, tmp_path, monkeypatch, run_async):
    user_home = tmp_path / "h"
    user_home.mkdir()
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    seen: dict = {}
    _spawn_recorder(monkeypatch, seen, home["claude_json"])

    ok, err = run_async(dtach_inject.launch_session("pk2", mode="ask", cwd=str(tmp_path)))

    assert ok, err
    at_spawn = json.loads(seen["claude_json_at_spawn"])
    assert "projects" not in at_spawn
    assert at_spawn["customApiKeyResponses"]["approved"] == [TAIL]
    assert at_spawn["hasCompletedOnboarding"] is True


def test_launch_goes_on_when_the_answers_cannot_be_written(
        home, tmp_path, monkeypatch, run_async):
    def _boom(folder, env, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(claude_bootstrap, "prepare_session_start", _boom)
    seen: dict = {}
    _spawn_recorder(monkeypatch, seen, home["claude_json"])

    ok, err = run_async(dtach_inject.launch_session("pk3", mode="ask", cwd=str(tmp_path)))

    assert ok, err
    assert seen["env"] is not None


def test_after_a_decline_the_fix_aipager_gives_is_the_one_that_works(home, caplog):
    """A declined key is refused in Claude Code's record, so moving it into
    daemon.env changes nothing next to a login (the table keeps a refusal);
    the log and the docs name what does work: /config in a terminal."""
    _write(home["claude_json"], LOGGED_IN)
    assert claude_bootstrap.prepare_session_start(None, {"ANTHROPIC_API_KEY": KEY}) == [
        "key-declined"]
    home["daemon_env"].write_text(f"ANTHROPIC_API_KEY={KEY}\n")
    assert claude_bootstrap.prepare_session_start(
        None, daemon_secrets.build_session_env({})) == []
    import inspect
    source = inspect.getsource(dtach_inject.launch_session)
    declined = source[source.index('"key-declined"'):source.index('"onboarded"')]
    assert "/config" in declined and "daemon.env" not in declined
    doc = (Path(__file__).resolve().parents[1] / "docs" / "troubleshooting.md").read_text()
    section = doc[doc.index("## Using an API key"):doc.index("## The daemon's control socket")]
    no_bullet = next(line for line in section.split("\n  - ") if line.startswith("No, when"))
    assert "/config" in no_bullet and "daemon.env" not in no_bullet


# ---- values Claude Code would ignore -------------------------------------------

def test_a_key_in_claude_settings_with_spaces_around_it_counts_as_set_on_purpose(home):
    """Claude Code trims the key; a key the owner set on purpose must not be
    declined for the whitespace around it."""
    _write(home["settings"], {"env": {"ANTHROPIC_API_KEY": KEY + " \n"}})
    _write(home["claude_json"], LOGGED_IN)

    assert claude_bootstrap.prepare_session_start(None, {}) == ["key-approved"]
    assert _answers(home["claude_json"])["approved"] == [TAIL]


def test_a_value_in_claude_settings_that_is_not_text_is_ignored(home, tmp_path):
    """A number where a key belongs must not cost the folder its trust, and
    a number as CLAUDE_CONFIG_DIR moves nothing: the answers still go to
    the default file."""
    folder = tmp_path / "proj"
    folder.mkdir()
    _write(home["settings"], {"env": {"ANTHROPIC_API_KEY": 123, "CLAUDE_CONFIG_DIR": 7}})
    _write(home["claude_json"], ONBOARDED)

    assert claude_bootstrap.prepare_session_start(str(folder), {}) == ["trusted"]
    assert _read(home["claude_json"])["projects"][str(folder.resolve())][
        "hasTrustDialogAccepted"] is True


@pytest.mark.parametrize("env,data,settings", [
    ({"ANTHROPIC_AUTH_TOKEN": "  "}, {}, {}),
    ({}, {"primaryApiKey": "  "}, {}),
    ({}, {}, {"apiKeyHelper": "  "}),
    ({"CLAUDE_CONFIG_DIR": ""}, {}, {}),
], ids=["blank-token", "blank-saved-key", "blank-helper", "empty-config-dir"])
def test_a_blank_value_counts_as_none(home, env, data, settings):
    _write(home["claude_json"], {**ONBOARDED, **data})
    if settings:
        _write(home["settings"], settings)

    assert claude_bootstrap.prepare_session_start(
        None, {"ANTHROPIC_API_KEY": KEY, **env}) == ["key-approved"]


def test_a_whitespace_config_dir_writes_nothing(home, tmp_path):
    """Claude Code does not trim CLAUDE_CONFIG_DIR (measured with 2.1.303):
    "  " is a folder of that name inside the session's folder, global
    config included. aipager writes nothing inside a session's folder."""
    folder = tmp_path / "proj"
    folder.mkdir()
    _write(home["claude_json"], {})

    assert claude_bootstrap.prepare_session_start(
        str(folder), {"ANTHROPIC_API_KEY": KEY, "CLAUDE_CONFIG_DIR": "  "},
        cwd=str(folder)) == []
    assert _read(home["claude_json"]) == {}
    assert not (folder / "  ").exists()


def test_an_empty_config_dir_writes_nothing_into_the_folders_legacy_file(home, tmp_path):
    """With CLAUDE_CONFIG_DIR="" the start home is the session's folder, and
    a non-empty `.config.json` there is the global config Claude Code reads
    (measured with 2.1.303). It is the project's own file: aipager writes
    nothing into it, as into any relative config home."""
    folder = tmp_path / "proj"
    folder.mkdir()
    legacy = folder / ".config.json"
    _write(legacy, {"theirs": 1})
    before = legacy.read_bytes()
    _write(home["claude_json"], {})

    assert claude_bootstrap.prepare_session_start(
        str(folder), {"ANTHROPIC_API_KEY": KEY, "CLAUDE_CONFIG_DIR": ""},
        cwd=str(folder)) == []
    assert legacy.read_bytes() == before
    assert _read(home["claude_json"]) == {}


# ---- project, local and managed settings ------------------------------------------

def _managed(name: str, data) -> Path:
    base = claude_paths.MANAGED_SETTINGS_DIR
    path = base / name
    path.parent.mkdir(parents=True, exist_ok=True)
    _write(path, data)
    return path


@pytest.mark.parametrize("where", ["project", "local"])
def test_a_key_in_the_folders_settings_counts_as_set_on_purpose(home, tmp_path, where):
    """Claude Code reads a trusted folder's .claude/settings.json and
    settings.local.json env: a key there was put there for it."""
    folder = tmp_path / "proj"
    (folder / ".claude").mkdir(parents=True)
    name = "settings.json" if where == "project" else "settings.local.json"
    _write(folder / ".claude" / name, {"env": {"ANTHROPIC_API_KEY": KEY}})
    _write(home["claude_json"], LOGGED_IN)

    assert claude_bootstrap.prepare_session_start(str(folder), {}, cwd=str(folder)) == [
        "trusted", "key-approved"]


def test_a_managed_key_counts_as_set_on_purpose(home):
    _managed("managed-settings.json", {"env": {"ANTHROPIC_API_KEY": KEY}})
    _write(home["claude_json"], LOGGED_IN)

    assert claude_bootstrap.prepare_session_start(None, {}) == ["key-approved"]


def test_an_untrusted_folders_project_key_is_not_on_purpose(home, tmp_path):
    """A folder that is not trusted (here: one that holds the home folder,
    so it is not trusted now either): Claude Code does not read its project
    settings, so their key is neither the session's nor on purpose."""
    folder = tmp_path / "proj"
    (folder / ".claude").mkdir(parents=True)
    _write(folder / ".claude" / "settings.json", {"env": {"ANTHROPIC_API_KEY": KEY}})
    _write(home["claude_json"], LOGGED_IN)

    # The inherited key next to a login is declined; the project one is ignored.
    assert claude_bootstrap.prepare_session_start(
        None, {"ANTHROPIC_API_KEY": KEY}, cwd=str(folder)) == ["key-declined"]


def test_a_folder_trusted_before_counts_its_project_key(home, tmp_path):
    folder = tmp_path / "proj"
    (folder / ".claude").mkdir(parents=True)
    _write(folder / ".claude" / "settings.json", {"env": {"ANTHROPIC_API_KEY": KEY}})
    _write(home["claude_json"], {**LOGGED_IN, "projects": {
        str(folder.resolve()): {"hasTrustDialogAccepted": True}}})

    assert claude_bootstrap.prepare_session_start(None, {}, cwd=str(folder)) == [
        "key-approved"]


def test_managed_settings_win_over_project_settings(home, tmp_path):
    """A managed cloud switch: Claude Code never asks about a key then."""
    folder = tmp_path / "proj"
    (folder / ".claude").mkdir(parents=True)
    _write(folder / ".claude" / "settings.json", {"env": {"CLAUDE_CODE_USE_BEDROCK": "0"}})
    _managed("managed-settings.d/10-cloud.json", {"env": {"CLAUDE_CODE_USE_BEDROCK": "1"}})
    _write(home["claude_json"], ONBOARDED)

    assert claude_bootstrap.prepare_session_start(
        str(folder), {"ANTHROPIC_API_KEY": KEY}, cwd=str(folder)) == ["trusted"]
    assert "customApiKeyResponses" not in _read(home["claude_json"])


def test_a_managed_api_key_helper_is_another_login(home):
    _managed("managed-settings.json", {"apiKeyHelper": "/usr/local/bin/get-key"})
    _write(home["claude_json"], ONBOARDED)

    assert claude_bootstrap.prepare_session_start(None, {"ANTHROPIC_API_KEY": KEY}) == [
        "key-declined"]


def test_an_empty_claude_json_is_left_alone(home, tmp_path):
    """A 0-byte ~/.claude.json is not JSON for Claude Code either: never
    replaced by aipager's near-empty one."""
    folder = tmp_path / "proj"
    folder.mkdir()
    home["claude_json"].write_bytes(b"")

    assert claude_bootstrap.prepare_session_start(str(folder), {}) == []
    assert home["claude_json"].read_bytes() == b""


def test_an_empty_legacy_config_is_written(home, tmp_path):
    """An empty, writable ~/.claude/.config.json with no ~/.claude.json is
    the file Claude Code uses (measured with 2.1.303), and it writes into
    it: so do the answers."""
    folder = tmp_path / "proj"
    folder.mkdir()
    legacy = home["settings"].parent / ".config.json"
    legacy.write_bytes(b"")
    legacy.chmod(0o644)

    assert claude_bootstrap.prepare_session_start(
        str(folder), {"CLAUDE_CODE_OAUTH_TOKEN": "x"}) == ["trusted", "onboarded"]
    assert _read(legacy)["hasCompletedOnboarding"] is True
    assert not home["claude_json"].exists()


def test_the_custom_oauth_file_gets_the_answers(home, tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    _write(home["claude_json"], {})

    done = claude_bootstrap.prepare_session_start(
        str(folder), {"CLAUDE_CODE_CUSTOM_OAUTH_URL": "https://login.example.com",
                      "CLAUDE_CODE_OAUTH_TOKEN": "x"})

    assert done == ["trusted", "onboarded"]
    assert _read(home["claude_json"]) == {}
    custom = home["claude_json"].parent / ".claude-custom-oauth.json"
    assert _read(custom)["hasCompletedOnboarding"] is True
