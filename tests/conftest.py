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
        # migrate.upgrade_to_v3() reads this (and its .retired.* siblings)
        # to recover Mini App settings — without a redirect it would read
        # the operator's real config.env.
        "aipager.config._XDG_CONFIG": config_env,
        "aipager.preferences._PREFERENCES_PATH": cfg / "preferences.json",
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


@pytest.fixture(autouse=True)
def _reset_preferences_cache(_isolate_home_paths):
    """Reset ``preferences``'s in-memory cache for every test.

    Unlike ``keyboard.json``'s load-once-at-import constants,
    ``preferences.py`` deliberately keeps a mutable module-level cache
    (see its docstring) so a running daemon never has to restart to pick
    up a `/settings` change. That same design means the cache would leak
    across tests — one test's ``set_preference`` call would silently be
    visible to the next — without an explicit reset. Depends on
    ``_isolate_home_paths`` so it always runs after the path redirect is
    in place, not before.
    """
    import aipager.preferences as _prefs
    _prefs._cache = None
    yield
    _prefs._cache = None


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


@pytest.fixture(autouse=True)
def _block_real_telegram_http(monkeypatch):
    """Fail loudly rather than POST to api.telegram.org.

    ``rich_message._post`` is the single transport for every raw Telegram
    call, so blocking it here covers send_rich_message, edit_message_text_rich
    and anything added later. Tests that exercise the transport patch ``_post``
    themselves; a function-scoped patch inside the test body wins over this one.
    """
    async def _refuse(method, payload):
        raise AssertionError(
            f"test attempted a real Telegram API call: {method}. "
            "Mock aipager.bot.rich_message._post (or the calling helper)."
        )

    monkeypatch.setattr("aipager.bot.rich_message._post", _refuse)


def _snapshot_live_sockets() -> set[str]:
    tmp = Path("/tmp")
    socks = {str(p) for p in tmp.glob("claude-dtach-*.sock")}
    aipager_sock = tmp / "aipager.sock"
    if aipager_sock.exists():
        socks.add(str(aipager_sock))
    return socks


@pytest.fixture(scope="session", autouse=True)
def _guard_live_sockets():
    """Fail the run if the suite unlinked a live daemon or session socket.

    The sibling ``_guard_real_home`` covers ``$HOME`` only, which is why
    this hole stayed open: ``updater._remove_tmp_sockets`` deletes
    ``/tmp/aipager.sock`` and every ``/tmp/claude-dtach-*.sock``, so a
    test invoking it without redirecting ``updater.Path`` runs it
    against the real /tmp. That happened — the host daemon's hook socket
    was unlinked mid-suite and hooks then stayed silently dead, because
    the daemon goes on serving the now-unreachable bound socket. The
    dtach sockets are worse: their sessions keep running but can never
    be reattached.

    Guarded for existence rather than mtime: a session starting mid-run
    legitimately adds a socket, but nothing the suite does may remove
    one. ``/tmp/claude-status-*.json`` is deliberately excluded — live
    sessions rewrite it every few seconds, so it would false-positive.
    """
    before = _snapshot_live_sockets()
    yield
    gone = sorted(before - _snapshot_live_sockets())
    if gone:
        pytest.fail(
            "tests unlinked live sockets under /tmp:\n  "
            + "\n  ".join(gone)
            + "\n\nSandbox the responsible test by redirecting that "
              "module's Path to tmp_path. (If you stopped the daemon or "
              "a session while the suite ran, this is a false positive.)"
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
        # Default to no highlighted-fragment quote (design.md "reply
        # context" feature). Without this default, the auto-generated
        # MagicMock is truthy and `.text` unsliceable, which would trip
        # _build_reply_context's `quote.text[:1000]` in every test that
        # doesn't care about highlighting.
        update.message.quote = None
        update.effective_user = MagicMock()
        update.effective_user.id = user_id
        update.effective_chat = MagicMock()
        update.effective_chat.id = chat_id
        return update
    return _mk
