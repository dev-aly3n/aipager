"""Shared pytest fixtures."""

import asyncio
from importlib import import_module
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _isolate_audit_log(tmp_path, monkeypatch):
    """Redirect the audit log to tmp for every test, so exercising the
    bot's audit path never appends to the operator's real
    ``~/.claude/aipager-audit.jsonl``. Tests that pass ``path=``
    explicitly are unaffected."""
    monkeypatch.setattr("aipager.audit.AUDIT_LOG_PATH",
                        tmp_path / "audit.jsonl")


@pytest.fixture(autouse=True)
def _isolate_wizard_config(tmp_path, monkeypatch):
    """Redirect wizard/scope config paths to tmp for every test, so any
    code path that writes ``aipager.yaml`` / ``policy.yaml`` (e.g.
    ``first_run._commit_owner_dm``, ``scope.dump_scopes``,
    ``scope_io.commit_scope``) never touches the operator's real
    ``~/.config/aipager/``.

    Why this is autouse rather than opt-in: prior to this fixture the
    contract was per-test manual ``monkeypatch.setattr(_scope,
    "CONFIG_PATH", ...)``. One forgotten redirect during a /ship
    pipeline run wrote fixture values (``bot_token="TOK"``,
    ``chat_id=42``, ``label="owner DM"``) to a live user config and
    broke ``aipager start`` (Telegram 404). Autouse makes it
    structurally impossible for a future test to skip the redirect.
    """
    monkeypatch.setattr("aipager.scope.CONFIG_PATH",
                        tmp_path / "aipager.yaml")
    monkeypatch.setattr("aipager.policy.POLICY_PATH",
                        tmp_path / "policy.yaml")


@pytest.fixture(autouse=True)
def _isolate_home_paths(tmp_path, monkeypatch):
    """Redirect every module-level ``Path.home()`` write target to tmp.

    ``_isolate_wizard_config`` closed the ``aipager.yaml`` hole; this
    closes the rest, most importantly the ``~/.claude/`` surface, which
    holds Claude Code's OWN config rather than aipager's. Before this
    fixture a suite run rewrote the operator's real
    ``~/.claude/settings.json``, marked this repo trusted in
    ``~/.claude.json``, and appended fixture users (``user_id: 999``)
    to the live team approval queue.

    Constants that a consumer module re-imports by value (``from x
    import CONST``) need their own entry — patching the defining module
    does not reach the consumer's copy. Function-local imports resolve
    at call time and are covered by the defining module alone.

    ``updater._USER_PATHS_TO_REMOVE`` is the sharpest edge here: it
    feeds an ``rmtree`` of the whole ``~/.config/aipager`` directory,
    so a future test reaching ``cmd_uninstall(force=True)`` without a
    redirect would delete a live install outright.
    """
    home = tmp_path / "home"
    cfg = home / ".config" / "aipager"
    claude = home / ".claude"
    settings = claude / "settings.json"
    config_env = cfg / "config.env"
    team_yaml = cfg / "team.yaml"
    sessions_json = claude / "aipager-sessions.json"

    targets = {
        "aipager.claude_bootstrap._SETTINGS": settings,
        "aipager.claude_bootstrap._CLAUDE_JSON": home / ".claude.json",
        "aipager.team.PENDING_USERS_PATH":
            claude / "aipager-pending-users.json",
        "aipager.team.TEAM_CONFIG_PATH": team_yaml,
        "aipager.policy.POLICY_D_DIR": cfg / "policy.d",
        "aipager.config.SESSION_STATE_FILE": sessions_json,
        "aipager.state.SESSION_STATE_FILE": sessions_json,
        "aipager.status.SESSION_STATE_FILE": sessions_json,
        "aipager.config._KEYBOARD_CONFIG_PATH": cfg / "keyboard.json",
        "aipager.session_store.SESSIONS_ROOT":
            home / ".local" / "share" / "aipager" / "sessions",
        "aipager.service.LINUX_UNIT_PATH":
            home / ".config" / "systemd" / "user" / "aipager.service",
        "aipager.service.MACOS_PLIST_PATH":
            home / "Library" / "LaunchAgents" / "com.aipager.daemon.plist",
        "aipager.service.MACOS_LOG_PATH":
            home / "Library" / "Logs" / "aipager.log",
        # Wizard constants + every by-value re-import of them.
        "aipager.wizard._constants.CLAUDE_SETTINGS": settings,
        "aipager.wizard.settings_patch.CLAUDE_SETTINGS": settings,
        "aipager.wizard._constants.CONFIG_DIR": cfg,
        "aipager.wizard.daemon_io.CONFIG_DIR": cfg,
        "aipager.wizard.draft.CONFIG_DIR": cfg,
        "aipager.wizard._constants.CONFIG_ENV": config_env,
        "aipager.wizard.daemon_io.CONFIG_ENV": config_env,
        "aipager.wizard.CONFIG_ENV": config_env,
        "aipager.wizard._constants.TEAM_YAML": team_yaml,
        "aipager.wizard.draft.DRAFT_PATH": cfg / ".wizard-draft.json",
        # Deletion targets — see docstring.
        "aipager.updater._USER_PATHS_TO_REMOVE": [cfg, sessions_json],
        "aipager.updater._MACOS_PATHS_TO_REMOVE": [
            home / "Library" / "LaunchAgents" / "com.aipager.daemon.plist",
            home / "Library" / "Logs" / "aipager.log",
        ],
    }
    originals = {}
    for dotted, value in targets.items():
        module_name, _, attr = dotted.rpartition(".")
        originals[dotted] = getattr(import_module(module_name), attr)
        monkeypatch.setattr(dotted, value)
    return originals


@pytest.fixture
def real_home_paths(_isolate_home_paths):
    """Pre-redirect values of the constants ``_isolate_home_paths``
    patches, keyed by dotted name.

    For tests asserting the production contract itself (e.g. "the
    service unit path lives under $HOME") — those would otherwise see
    only the tmp redirect and pass vacuously.
    """
    return _isolate_home_paths


# Files under the real home that aipager itself owns and that no test
# may touch. Deliberately excludes paths a live aipager daemon or a
# running Claude Code session rewrites on its own (``~/.claude.json``,
# ``~/.claude/aipager-sessions.json``, ``~/.claude/projects/``,
# ``~/.claude/aipager-audit.jsonl``) — those would false-positive. They
# are still covered by the per-test redirects above.
_GUARDED_HOME_PATHS = (
    Path.home() / ".config" / "aipager",
    Path.home() / ".claude" / "settings.json",
    Path.home() / ".claude" / "aipager-pending-users.json",
    Path.home() / ".local" / "share" / "aipager",
)


def _snapshot_guarded() -> dict[str, tuple[int, int]]:
    snap: dict[str, tuple[int, int]] = {}
    for root in _GUARDED_HOME_PATHS:
        paths = [root, *root.rglob("*")] if root.is_dir() else [root]
        for p in paths:
            try:
                st = p.stat()
            except OSError:
                continue
            snap[str(p)] = (st.st_mtime_ns, st.st_size)
    return snap


@pytest.fixture(scope="session", autouse=True)
def _guard_real_home():
    """Fail the run loudly if the suite mutated the operator's real config.

    The per-test redirects above enumerate known constants, which is
    inherently incomplete — the next ``Path.home()`` constant someone
    adds re-opens the hole silently. This turns that silence into a
    failure. Paths computed inline rather than as module constants
    (e.g. the daemon lock in ``cli/daemon.py``) are only caught here.
    """
    before = _snapshot_guarded()
    yield
    after = _snapshot_guarded()
    changed = sorted(k for k, v in after.items() if before.get(k) != v)
    changed += sorted(set(before) - set(after))
    if changed:
        pytest.fail(
            "tests mutated the operator's real config under $HOME:\n  "
            + "\n  ".join(changed)
            + "\n\nAdd the responsible path to _isolate_home_paths in "
              "tests/conftest.py. (If you edited Claude Code settings "
              "while the suite ran, this is a false positive.)"
        )


@pytest.fixture
def tmp_state_file(tmp_path, monkeypatch):
    """Redirect SESSION_STATE_FILE so tests never touch the real one."""
    target = tmp_path / "sessions.json"
    monkeypatch.setattr("aipager.state.SESSION_STATE_FILE", target)
    monkeypatch.setattr("aipager.status.SESSION_STATE_FILE", target)
    return target


@pytest.fixture
def run_async():
    """Run a coroutine to completion in a fresh event loop.

    Tests use this instead of `asyncio.run(...)` so that a single
    test file can interleave sync setup with `await` calls without
    inheriting an event loop from another fixture.
    """
    def _run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)
    return _run


@pytest.fixture
def mk_bot():
    """Build a TelegramBot with mocked `_app` and `team=None` by default.

    `team=None` defeats the team-mode authz gate so handlers can be
    exercised directly without seeding an allow-list. Pass
    ``team=<Team>`` to test the team-mode path.
    """
    from aipager.bot import TelegramBot
    from aipager.state import SessionRegistry

    def _mk(registry=None, *, team=None, scopes=None):
        if registry is None:
            registry = SessionRegistry()
        bot = TelegramBot(registry)
        bot._app = MagicMock()
        bot._app.bot = MagicMock()
        bot._app.bot.send_message = AsyncMock()
        bot.team = team
        # Default to legacy mode in tests — the constructor may have
        # picked up a real ~/.config/aipager/aipager.yaml. Tests opt into
        # multi-scope by passing scopes=[...].
        bot.scopes = scopes
        return bot
    return _mk


@pytest.fixture
def mk_update():
    """Build a mocked Telegram Update with sensible defaults.

    `text` becomes ``update.message.text``. `user_id` / `chat_id`
    populate ``effective_user`` / ``effective_chat`` so handlers that
    re-derive identity (team auth, mark_driver) work without extra
    wiring.
    """
    def _mk(text, *, message_id=999, user_id=12345, chat_id=-1001):
        update = MagicMock()
        update.message = MagicMock()
        update.message.text = text
        update.message.message_id = message_id
        update.message.reply_text = AsyncMock()
        # Default to no reply target; tests that want one can set this
        # explicitly. Without this default, the auto-generated MagicMock
        # has non-string `.text` / `.caption` attributes that break any
        # handler that runs regex against them.
        update.message.reply_to_message = None
        update.effective_user = MagicMock()
        update.effective_user.id = user_id
        update.effective_chat = MagicMock()
        update.effective_chat.id = chat_id
        return update
    return _mk
