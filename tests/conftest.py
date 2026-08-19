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
        # daemon_secrets.build_session_env() reads this on every
        # launch_session() call. Without a redirect, any test reaching
        # that path (directly or via launch_session) would read the
        # operator's real Claude credential off disk into a test's env
        # dict — never written or printed here, but still a real-file
        # read this suite must never do.
        "aipager.daemon_secrets.DAEMON_ENV_PATH": cfg / "daemon.env",
        # service.py re-imports the same constant BY VALUE
        # (`from aipager.daemon_secrets import DAEMON_ENV_PATH`), so it
        # needs its own entry — see the by-value-import note above.
        # `ensure_daemon_env()` writes here at `service install` time.
        "aipager.service.DAEMON_ENV_PATH": cfg / "daemon.env",
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
def _no_real_claude_candidates(request, monkeypatch):
    """Make claude_resolve discovery return zero candidates by default.

    Before claude_resolve existed, every one of the six resolution call
    sites called ``shutil.which("claude")`` once — free, and harmless to
    leave unmocked, which is why most of the suite did. Once resolution
    instead walks real ``$PATH`` entries and verifies each with
    ``--version``, any test exercising a call site (launch_session,
    require_claude, check_claude, _claude_version_diag, the wizard deps
    table, …) without an explicit mock would probe whatever `claude`
    binaries actually exist on THIS machine — including the live
    daemon's real installed binary. That is a hard-constraint violation
    (see spec.md), not just noise.

    Safe-by-default, mirroring ``_block_real_telegram_http``'s shape: a
    test that wants real candidates monkeypatches
    ``aipager.claude_resolve._candidate_paths`` (or a fixture-binary
    seam built on it) itself, inside the test body — that patch runs
    after this fixture's setup and wins.

    The process-level memo is also reset before and after every test —
    otherwise one test's successful resolution would leak into the
    next, defeating both this fixture and any per-test override.

    Exempted: ``tests/e2e/``, the one caller allowed to touch a real
    installed claude (see entrypoints.md's fixture-binary contract) —
    those tests are opt-in only (``-m e2e``) and skip themselves when no
    real, authenticated claude is available.
    """
    import aipager.claude_resolve as _cr
    _cr._memo = None
    _cr._memo_error = None
    if "e2e" not in request.keywords:
        monkeypatch.setattr(_cr, "_candidate_paths", lambda: [])
    yield
    _cr._memo = None
    _cr._memo_error = None


@pytest.fixture(autouse=True)
def _no_real_login_shell_probe(request, monkeypatch):
    """Refuse to spawn a real login shell by default.

    ``service.ensure_daemon_env()`` falls back to
    ``$SHELL -l -i -c 'printenv CLAUDE_CODE_OAUTH_TOKEN'`` when no
    legacy config.env token is found — a real subprocess that sources
    the OPERATOR'S OWN shell rc files and could read their real token.
    Any test reaching ``service.ensure_daemon_env()`` (directly or via
    ``_install_linux``/``_install_macos``) without mocking this raises
    loudly instead of silently spawning a real shell — the same
    "execute a real binary beyond --version on a fixture" constraint
    that motivates the claude-candidates fixture above. Tests exercising
    the discovery path itself patch
    ``aipager.service._discover_token_via_login_shell`` explicitly.
    """
    def _refuse():
        raise AssertionError(
            "test reached service._discover_token_via_login_shell() "
            "without mocking it — this would spawn a real login shell "
            "against the operator's own rc files. Monkeypatch "
            "aipager.service._discover_token_via_login_shell instead."
        )

    if "e2e" not in request.keywords:
        monkeypatch.setattr("aipager.service._discover_token_via_login_shell",
                            _refuse)


@pytest.fixture(autouse=True)
def _no_real_credential_probe(monkeypatch):
    """Refuse to actually run ``claude -p`` from the test suite.

    ``claude_resolve.validate_credential`` spends a real API call on the
    operator's account every time it runs. One unmocked test is a few
    cents; one unmocked test in CI, on every push, is a standing charge —
    and it would also hit the network from a sandbox that is supposed to
    have none. ``_run_probe`` exists as a separate function purely to be
    this seam: a test that wants a particular probe outcome patches
    ``validate_credential`` (or ``_run_probe``) explicitly.
    """
    def _refuse(claude_path, env, cwd, timeout):
        raise AssertionError(
            "test tried to run the real `claude -p` credential probe "
            f"({claude_path!r}) — this costs money and hits the network. "
            "Patch aipager.claude_resolve.validate_credential (or "
            "._run_probe) in your test instead."
        )

    monkeypatch.setattr("aipager.claude_resolve._run_probe", _refuse)


@pytest.fixture(autouse=True)
def _no_real_service_manager(request, monkeypatch):
    """Refuse to drive the real systemd/launchd session by default.

    ``service._install_linux()`` ends with
    ``systemctl --user enable --now aipager.service`` — the SAME unit name
    the operator's live daemon runs under on this machine. A test reaching
    it unmocked would enable, start or restart their actual daemon, and
    ``_post_install_probe()`` would then poke the live control socket.

    The sibling ``_no_real_login_shell_probe`` above already guards the
    other real-subprocess path in this module; this closes the pair. The
    log readers (``_logs_linux`` shelling to ``journalctl``,
    ``_logs_macos`` to ``tail``) are blocked too — they are read-only so
    they cannot corrupt anything, but ``journalctl --follow`` would hang
    a future unmocked test rather than fail it. Both guards exist because
    this suite has a history of reaching past its sandbox —
    ``tests/conftest.py``'s own ``/tmp``-socket guard documents a run that
    unlinked the live daemon's hook socket and left hooks silently dead.

    Commands that are not a service manager (``_run(["x"])`` in the tests
    of ``_run`` itself) pass through untouched.
    """

    from aipager import service as _service

    real_run = _service._run          # delegate, do not replace

    def _guarded(cmd, *a, **kw):
        head = str(cmd[0]) if cmd else ""
        if head.rsplit("/", 1)[-1] in ("systemctl", "launchctl", "journalctl", "tail"):
            raise AssertionError(
                f"test invoked the real service manager: {cmd!r}. This can "
                "enable/restart the operator's live aipager.service, and "
                "`journalctl --follow` would hang the run. "
                "Monkeypatch aipager.service._run in your test instead."
            )
        # Everything else runs for real — the tests OF _run pass fake argv
        # like ["x"] and assert on its genuine exit codes, so replacing it
        # wholesale would break the thing this guard is meant to protect.
        return real_run(cmd, *a, **kw)

    if "e2e" not in request.keywords:
        monkeypatch.setattr("aipager.service._run", _guarded)


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


def _control_socket_path() -> Path:
    """Resolve the daemon's control socket once, for the whole session.

    The caller must resolve this a single time and reuse the result for
    both the before and after snapshot. Re-deriving it per call would
    read whatever ``config.SOCKET_PATH`` happens to be at that moment, so
    a test that reloads ``aipager.config`` under a different
    ``$XDG_RUNTIME_DIR`` and fails to restore it would make the two
    snapshots describe two different files — reporting a socket as
    "unlinked" that nothing ever touched.
    """
    from aipager import config

    return Path(config.SOCKET_PATH)


def _snapshot_live_sockets(control_sock: Path) -> set[str]:
    tmp = Path("/tmp")
    socks = {str(p) for p in tmp.glob("claude-dtach-*.sock")}
    # The control socket moved to $XDG_RUNTIME_DIR, so the hardcoded /tmp
    # path no longer describes where the live daemon binds. Snapshot the
    # resolved location too, or this guard silently stops covering the
    # very socket it was written to protect on any host with
    # $XDG_RUNTIME_DIR set.
    for sock in (tmp / "aipager.sock", control_sock):
        if sock.exists():
            socks.add(str(sock))
    return socks


@pytest.fixture(scope="session", autouse=True)
def _guard_live_sockets():
    """Fail the run if the suite unlinked a live daemon or session socket.

    The sibling ``_guard_real_home`` covers ``$HOME`` only, which is why
    this hole stayed open: ``updater._remove_tmp_sockets`` deletes the
    resolved ``config.SOCKET_PATH``, ``/tmp/aipager.sock`` and every
    ``/tmp/claude-dtach-*.sock``, so a test invoking it without
    redirecting both ``updater.Path`` **and** ``config.SOCKET_PATH``
    runs it against the real paths. That happened — the host daemon's
    hook socket was unlinked mid-suite and hooks then stayed silently
    dead, because
    the daemon goes on serving the now-unreachable bound socket. The
    dtach sockets are worse: their sessions keep running but can never
    be reattached.

    Guarded for existence rather than mtime: a session starting mid-run
    legitimately adds a socket, but nothing the suite does may remove
    one. ``/tmp/claude-status-*.json`` is deliberately excluded — live
    sessions rewrite it every few seconds, so it would false-positive.
    """
    # Resolved once, deliberately — see _control_socket_path.
    control_sock = _control_socket_path()
    before = _snapshot_live_sockets(control_sock)
    yield
    gone = sorted(before - _snapshot_live_sockets(control_sock))
    if gone:
        pytest.fail(
            "tests unlinked live sockets:\n  "
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
