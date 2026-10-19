"""Idempotent first-run setup for Claude Code's user files.

Three things block a fresh container/headless aipager install from
working over Telegram, because the wizard (``aipager config``) is
normally what writes them:

Claude Code's files are wherever it keeps them for the sessions'
environment (``$CLAUDE_CONFIG_DIR``, the legacy ``.config.json``, a
settings ``env`` block that moves them): :mod:`aipager.claude_paths`
names them. Below, ``~/.claude`` and ``~/.claude.json`` are the defaults.

1. ``~/.claude/settings.json`` — without ``skipDangerousModePermissionPrompt``,
   claude shows a "WARNING: Bypass Permissions mode" picker every time
   it's launched with ``--dangerously-skip-permissions`` (which aipager
   uses for sessions started as ``/new !name``). The picker defaults to
   "No, exit" — the user's first prompt over Telegram lands as Enter
   on that, and claude exits before responding.

2. ``~/.claude.json`` — without ``hasTrustDialogAccepted`` for the
   working directory, claude shows a "Do you trust this folder?" picker
   on launch in any new cwd. Same Telegram failure mode. An
   ``ANTHROPIC_API_KEY`` it was never told about opens a "use this API
   key?" picker, and a home that never finished Claude Code's first run
   opens on its theme picker. Not written at start:
   ``inject.launch_session`` calls :func:`prepare_session_start` right
   before each session starts.

3. ``~/.claude/settings.json`` ``hooks`` + ``statusLine`` — without the
   ``aipager-hook`` wired into ``UserPromptSubmit``/``Stop``/etc.,
   the daemon never learns each session's transcript path and
   ``claude_session_id``, so ``/resume`` has nothing to resume to and
   safety/policy enforcement (PreToolUse) doesn't run.

Run on every ``aipager start`` because these are all user-state that
the wizard sets when the user accepts the prompts interactively — a
Telegram-only user (containerized friend deploy, SSH-less host) never
sees those prompts, so aipager has to write the acceptance for them.

Best-effort: failures are logged and skipped so a missing config
directory or non-writable filesystem never blocks daemon startup. A file
that cannot be read whole, or that a running Claude changes meanwhile, is
left as it is (:func:`_update_json`).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from aipager import claude_paths
from aipager._test_guard import check_write

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingAuthCheck:
    """Everything :func:`recover_auth_or_notice` needs, captured before
    ``bot.start()`` so the slow part can run after it."""

    claude_path: str
    version: str
    session_env: dict[str, str]


@dataclass(frozen=True)
class ProvenanceInfo:
    """Returned by :func:`bootstrap_claude_settings` on success.

    ``.lines`` is exactly :func:`aipager.claude_resolve.format_provenance`'s
    output — the main "claude: ... auth: ..." line, then zero or more
    "also found: ..." lines. Those lines name absolute paths and versions
    and are for the **local journal only**.

    ``.pending`` is set only when Claude reported itself logged out, and
    carries what :func:`recover_auth_or_notice` needs to go looking for a
    credential that works. What that eventually says in Telegram is an
    :class:`AuthNotice`, worded by :mod:`aipager.bot.auth_notice`, which is
    deliberately machine-free — see its docstring for why it and
    ``.lines`` are not the same string.
    """

    lines: list[str]
    auth_ok: bool
    pending: "PendingAuthCheck | None" = None
    #: Where the cheap check found Claude's credential (``AuthStatus.source``:
    #: env, file, keychain, unknown, probe-failed, version-gated). Kept so a
    #: problem report can name it without probing again at tap time.
    auth_source: str = "unknown"

    def __post_init__(self) -> None:
        # The old invariant here said a healthy-looking start must carry
        # no pending work. That was exactly backwards once we learned
        # that `claude auth status` reports a credential's *presence*
        # and not its *validity*: a revoked token answers
        # ``{"loggedIn": true}``, so the "healthy" case is precisely the
        # one that still needs checking for real.
        #
        # Replaced with the invariant that actually protects something:
        # a resolved binary ALWAYS carries deferred work, so no future
        # edit can quietly skip validation and reintroduce the silent
        # expired-token hang. ``auth_ok`` survives as the cheap check's
        # opinion, used for the log line and doctor — never as a reason
        # to skip the real one.
        if self.pending is None:
            raise ValueError(
                "every resolved binary must carry a PendingAuthCheck — "
                "the cheap auth-status check cannot see an expired token"
            )


# Mirror the wizard's hook surface so containerized deploys get the
# same coverage as `aipager config`. Kept in sync with
# ``aipager.wizard._constants.HOOK_EVENTS`` (the wizard is the canonical
# source for users who run it; this module is the fallback for users who
# don't). Both tuples must list events in the same order. They are kept
# separate rather than consolidated because ``_constants`` imports
# ``questionary`` at module level — pulling it in here would drag
# ``questionary``/``prompt_toolkit`` into every daemon start.
_HOOK_CMD = "aipager-hook"
_STATUSLINE_CMD = "aipager-statusline"
_HOOK_EVENTS = (
    "SessionStart", "SessionEnd", "UserPromptSubmit",
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest",
    "Notification", "Stop", "StopFailure", "SubagentStart", "SubagentStop",
    "PreCompact", "PostCompact", "MessageDisplay", "PreModelSwitch",
)
_TOOL_MATCHER_EVENTS = {"PreToolUse", "PostToolUse", "PermissionRequest"}
# Hard ceiling on the PreModelSwitch hook (roadmap 8.35); mirrors
# ``aipager.wizard._constants.MODEL_SWITCH_HOOK_TIMEOUT_SECONDS``.
_MODEL_SWITCH_HOOK_TIMEOUT_SECONDS = 5


#: How many times :func:`_update_json` reads a file again that changed
#: under it (every running Claude Code rewrites ~/.claude.json) before it
#: leaves the file alone.
UPDATE_TRIES = 3


def _stamp(path: Path) -> tuple[int, int, int] | None:
    """``(inode, size, mtime_ns)`` of *path*, or None when it does not
    exist (any other error raises)."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return st.st_ino, st.st_size, st.st_mtime_ns


def _read_bytes(path: Path) -> bytes:
    """*path*'s bytes (a seam: a test plays a read torn by a writer)."""
    return path.read_bytes()


def _replace(src: str, dst: Path) -> None:
    """``os.replace`` (a seam: a test makes the write fail)."""
    os.replace(src, dst)


def _before_replace(path: Path) -> None:
    """Test seam: runs after the new content is written to its temp file,
    before the check that *path* is unchanged."""


def _update_json(path: Path, change, *, empty_ok: bool = False) -> bool:
    """Apply *change* to the JSON object in *path* and write it back, only
    when that is safe. *change* edits the dict in place and returns True
    when it changed it. True when the file was written. Never raises for a
    bad or busy file: it logs and returns False.

    These are Claude Code's own files: ~/.claude.json holds its whole user
    config (the account, every project), and every running Claude
    rewrites it. So a file that cannot be read, is not JSON or is not an
    object is never written (a half-written one would otherwise be
    replaced by a near-empty one); a missing one is created; and the file
    must be unchanged (inode, size, mtime) from the read until the
    replace, else it is read again, at most :data:`UPDATE_TRIES` times,
    then left as it is: a change of Claude's is never lost to ours.
    Claude Code 2.1.301 names no lock file for it, so there is none to
    take. A symlinked file (a dotfile manager's) is written where it points,
    the link kept; the file keeps its mode.

    *empty_ok*: an existing 0-byte file reads as ``{}``. Only for the legacy
    ``.config.json``, which Claude Code uses (and writes into) even when it
    is empty (claude_paths)."""
    # A new file's mode comes from the name asked for: a link
    # ~/.claude.json -> dotfiles/claude.json still holds a dot-file.
    new_mode = 0o600 if Path(path).name.startswith(".") else 0o644
    path = Path(os.path.realpath(path))
    for _ in range(UPDATE_TRIES):
        try:
            before = _stamp(path)
            raw = b""
            if before is None:
                data = {}
                mode = new_mode
            else:
                mode = stat.S_IMODE(os.stat(path).st_mode)
                raw = _read_bytes(path)
                if _stamp(path) != before:
                    # Read while Claude wrote it: maybe torn, read again
                    # rather than give up on it as broken.
                    continue
                data = {} if empty_ok and raw == b"" else json.loads(raw.decode("utf-8"))
        except (OSError, ValueError) as e:
            log.warning("claude bootstrap: %s is not readable JSON (%s) - left as it is",
                        path, type(e).__name__)
            return False
        if not isinstance(data, dict):
            log.warning("claude bootstrap: %s is not a JSON object - left as it is", path)
            return False
        if not change(data):
            return False
        check_write(path)
        tmp = None
        try:
            # A folder aipager creates is private, as Claude Code makes its
            # own (it will hold the login and transcripts); an existing one
            # keeps its mode.
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".",
                                       suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, mode)
            _before_replace(path)
            # The bytes too: a same-size rewrite in place within one tick
            # of the file clock leaves inode, size and mtime as they were.
            if _stamp(path) != before or (before is not None and _read_bytes(path) != raw):
                os.unlink(tmp)
                continue
            _replace(tmp, path)
        except OSError as e:
            if tmp is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
            log.warning("claude bootstrap: could not write %s (%s)", path, type(e).__name__)
            return False
        return True
    log.warning("claude bootstrap: %s kept changing while aipager updated it - left as it is",
                path)
    return False


def _ensure_bypass_accepted(paths: claude_paths.ClaudePaths) -> bool:
    """Return True if the sessions' settings file was modified."""
    def change(settings: dict) -> bool:
        if settings.get("skipDangerousModePermissionPrompt") is True:
            return False
        settings["skipDangerousModePermissionPrompt"] = True
        return True
    return _update_json(paths.user_settings, change)


def _trusted(projects: dict, folder: str) -> bool:
    """*folder* or a folder above it is trusted: Claude Code asks about no
    folder under a trusted one (checked with 2.1.301)."""
    path = folder
    while True:
        entry = projects.get(path)
        if isinstance(entry, dict) and entry.get("hasTrustDialogAccepted") is True:
            return True
        parent = os.path.dirname(path)
        if parent == path:
            return False
        path = parent


def _already_trusted(folder: str, paths: claude_paths.ClaudePaths) -> bool:
    """*folder* or a folder above it is trusted in Claude Code's global
    config (read only; an unreadable file counts as not trusted)."""
    try:
        data = json.loads(_read_bytes(paths.global_config))
    except (OSError, ValueError):
        return False
    projects = data.get("projects") if isinstance(data, dict) else None
    return isinstance(projects, dict) and _trusted(projects, os.path.realpath(folder))


def ensure_folder_trusted(folder: str, env: Mapping[str, str] | None = None) -> bool:
    """Trust *folder* in Claude Code's global config for *env* (default: the
    sessions' environment), as answering Claude Code's "Do you
    trust this folder?" with yes would, unless it or a folder above it is
    trusted already. True when the file was written.

    A session started over Telegram cannot answer that dialog: the first
    message sent to it lands on the dialog as Enter on "No, exit" and the
    session ends. The key is the folder's real path, the one Claude Code
    looks up."""
    if env is None:
        from aipager import daemon_secrets
        env = daemon_secrets.build_session_env()
    paths = claude_paths.locate(env, cwd=folder)
    if paths.global_relative:
        _log_relative(paths)
        return False
    folder = os.path.realpath(folder)
    return _update_json(paths.global_config, lambda data: _trust_in(data, folder),
                        empty_ok=paths.legacy)


def _trust_in(data: dict, folder: str) -> bool:
    """Mark the real path *folder* trusted in the parsed global config
    *data*, unless it or a folder above it is. True when *data* changed."""
    projects = data.setdefault("projects", {})
    if not isinstance(projects, dict) or _trusted(projects, folder):
        return False
    entry = projects.get(folder)
    if not isinstance(entry, dict):
        entry = {}
    entry.setdefault("allowedTools", [])
    entry.setdefault("mcpContextUris", [])
    entry.setdefault("mcpServers", {})
    entry.setdefault("hasClaudeMdExternalIncludesWarningShown", False)
    entry["hasTrustDialogAccepted"] = True
    projects[folder] = entry
    return True


# ---- Claude Code's other start-up questions ------------------------------
#
# Measured with Claude Code 2.1.302 in a throwaway home, against a fake
# provider on 127.0.0.1, and read from its binary:
#
# - An ANTHROPIC_API_KEY whose last 20 characters (after trim) are in
#   neither ``customApiKeyResponses.approved`` nor ``.rejected`` opens an
#   interactive session with "Do you want to use this API key?", No
#   preselected. The first message aipager types answers it: No, recorded
#   for good. Never asked on Bedrock, Vertex or Foundry, nor in ``-p``.
# - A refused key falls back to an apiKeyHelper, a saved Console key, then
#   a claude.ai login; with none of them every prompt answers "Not logged
#   in". An approved key wins over a claude.ai login (per-token billing).
# - Without ``hasCompletedOnboarding: true`` a session opens on the
#   first-run screens (theme, security note, login). ``claude auth login``
#   sets it, ``claude setup-token`` does not.
# - ``oauthAccount`` is written at a claude.ai or Console login and removed
#   at logout.

_API_KEY = "ANTHROPIC_API_KEY"
_KEY_TAIL = 20
#: Claude Code's switches to a provider other than its own API (2.1.303's
#: list); on one of them it never asks about an ANTHROPIC_API_KEY. Its
#: "Claude apps gateway" login does the same and is not seen here: then the
#: question is answered although Claude Code would not ask it.
_CLOUD_SWITCHES = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                   "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_ANTHROPIC_AWS",
                   "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD", "CLAUDE_CODE_USE_MANTLE")
_TOKEN_VARS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN")


def claude_env(env: Mapping[str, str], *, folder: str | None = None) -> dict[str, str]:
    """The environment Claude Code runs with: *env* with the ``env`` blocks
    of its settings laid over it, as Claude Code does: the user settings
    file (and the one a ``CLAUDE_CONFIG_DIR`` there moves it to), then, for
    a *folder*, its ``.claude/settings.json`` and ``settings.local.json``,
    then managed settings (:func:`aipager.claude_paths.settings_layers`).
    MDM profiles, the registry and server-delivered settings are not read.
    Values are secrets: never log them."""
    paths = claude_paths.locate(env, cwd=folder)
    layers = claude_paths.settings_layers(paths, folder, project=folder is not None)
    return claude_paths.layered_env(env, layers)


def _switch_on(value: str | None) -> bool:
    """A provider switch is on, read as Claude Code reads it (2.1.303)."""
    return isinstance(value, str) and value.strip().lower() in ("1", "true", "yes", "on")


def _helper_set(layers: list[tuple[str, dict]]) -> bool:
    helper = claude_paths.layer_value(layers, "apiKeyHelper")
    return isinstance(helper, str) and bool(helper.strip())


def _other_login(data: dict, env: Mapping[str, str], layers: list[tuple[str, dict]],
                 paths: claude_paths.ClaudePaths) -> bool:
    """Something other than ANTHROPIC_API_KEY logs a session in: a token in
    its environment, a saved claude.ai or Console login, or an apiKeyHelper.
    The login file's existence is checked; it is never read."""
    if any((env.get(var) or "").strip() for var in _TOKEN_VARS):
        return True
    account = data.get("oauthAccount")
    if isinstance(account, dict) and account:
        return True
    saved_key = data.get("primaryApiKey")
    if isinstance(saved_key, str) and saved_key.strip():
        return True
    if _helper_set(layers):
        return True
    try:
        return paths.credentials.exists()
    except OSError:
        return False


def _key_set_on_purpose(key: str, layers: list[tuple[str, dict]]) -> bool:
    """*key* was set for Claude Code or for aipager: it is the one in one of
    Claude Code's settings files the session reads (user, a trusted folder's
    project or local, managed), or in aipager's own credential file.
    Otherwise it was only inherited from the environment aipager was started
    in."""
    from aipager import daemon_secrets

    sources = (*(block.get(_API_KEY) for block in claude_paths.layer_env(layers)),
               daemon_secrets.read_daemon_credential().get(_API_KEY),
               daemon_secrets.recovered_credential().get(_API_KEY))
    return any(isinstance(v, str) and v.strip() == key for v in sources)


def api_key_answer(state: str, on_purpose: bool, other_login: bool) -> str | None:
    """How to answer Claude Code's "use this API key?" for a session:
    ``"approve"``, ``"decline"`` or None (leave it).

    *state* is Claude Code's record of the key: ``"approved"``,
    ``"rejected"`` or ``"new"``. A key set on purpose (Claude Code's
    settings, aipager's daemon.env) is used. A key only inherited from the
    environment is used when nothing else can log the session in, and
    otherwise declined, which is Claude Code's own recommended answer: a
    subscription user who exports an API key for something else must not be
    moved to per-token billing by aipager. A refusal is kept, unless the key
    was set on purpose and nothing else can log the session in (the refusal
    may well be a first Telegram message that answered the question)."""
    if state == "new":
        return "approve" if on_purpose or not other_login else "decline"
    if state == "rejected" and on_purpose and not other_login:
        return "approve"
    return None


def _key_state(data: dict, key: str) -> str | None:
    """Claude Code's record of *key* in the parsed ~/.claude.json: its last
    20 characters in ``approved`` (checked first, as Claude Code does) or in
    ``rejected``, else ``"new"``. None when the record is not in the shape
    Claude Code writes, which is then left alone."""
    responses = data.get("customApiKeyResponses")
    if responses is None:
        responses = {}
    if not isinstance(responses, dict):
        return None
    approved = responses.get("approved")
    rejected = responses.get("rejected")
    approved = [] if approved is None else approved
    rejected = [] if rejected is None else rejected
    if not isinstance(approved, list) or not isinstance(rejected, list):
        return None
    tail = key[-_KEY_TAIL:]
    if tail in [x for x in approved if isinstance(x, str)]:
        return "approved"
    if tail in [x for x in rejected if isinstance(x, str)]:
        return "rejected"
    return "new"


def _answer_key(data: dict, key: str, on_purpose: bool, other_login: bool,
                notes: list[str]) -> tuple[str | None, bool]:
    """Record the answer for *key* in *data*. Returns what changed
    (``"key-approved"``, ``"key-declined"`` or None) and whether sessions
    will use the key; *notes* gets what the log should say
    (:func:`_log_note`)."""
    state = _key_state(data, key)
    if state is None:
        notes.append("odd-record")
        return None, False
    answer = api_key_answer(state, on_purpose, other_login)
    if answer is None:
        if state == "rejected" and not other_login:
            notes.append("refused-no-login")
        elif state == "rejected" and on_purpose:
            notes.append("refused-other-login")
        return None, state == "approved"
    responses = data.get("customApiKeyResponses") or {}
    approved = list(responses.get("approved") or [])
    rejected = list(responses.get("rejected") or [])
    tail = key[-_KEY_TAIL:]
    if answer == "approve":
        # What Claude Code's own /config toggle does: off one list, onto the other.
        responses["rejected"] = [x for x in rejected if x != tail]
        responses["approved"] = [*approved, tail]
    else:
        responses["approved"] = approved
        responses["rejected"] = [*rejected, tail]
    data["customApiKeyResponses"] = responses
    if answer == "approve":
        return "key-approved", True
    return "key-declined", False


@dataclass(frozen=True)
class _SessionCredential:
    """What decides Claude Code's start-up answers for a session, read from
    the environment it gets. Holds the key: never log it."""

    run_env: dict = field(repr=False)
    layers: list = field(repr=False)
    paths: claude_paths.ClaudePaths
    cloud: bool = False
    #: The ANTHROPIC_API_KEY Claude Code would ask about, trimmed, or "".
    key: str = field(default="", repr=False)
    on_purpose: bool = False


def _session_credential(env: Mapping[str, str], *, folder: str | None = None,
                        project: bool = False,
                        paths: claude_paths.ClaudePaths | None = None) -> _SessionCredential:
    """*folder*'s project and local settings count only when *project*:
    Claude Code reads them in a trusted folder only."""
    if paths is None:
        paths = claude_paths.locate(env, cwd=folder)
    layers = claude_paths.settings_layers(paths, folder, project=project)
    run_env = claude_paths.layered_env(env, layers)
    cloud = any(_switch_on(run_env.get(var)) for var in _CLOUD_SWITCHES)
    key = (run_env.get(_API_KEY) or "").strip() if not cloud else ""
    return _SessionCredential(run_env=run_env, layers=layers, paths=paths,
                              cloud=cloud, key=key,
                              on_purpose=bool(key) and _key_set_on_purpose(key, layers))


def refused_key_strands_sessions(env: Mapping[str, str]) -> bool:
    """Read only, for ``aipager doctor``: sessions started now would get an
    ANTHROPIC_API_KEY that Claude Code was told not to use, aipager would
    leave that answer as it is, and nothing else would log them in, so they
    answer "Not logged in". ``claude -p`` (the credential probe) uses such a
    key anyway, so the probe alone reports it as working. Never raises."""
    try:
        cred = _session_credential(env)
        if not cred.key:
            return False
        data = json.loads(_read_bytes(cred.paths.global_config))
        if not isinstance(data, dict) or _key_state(data, cred.key) != "rejected":
            return False
        other = _other_login(data, cred.run_env, cred.layers, cred.paths)
        return not other and api_key_answer("rejected", cred.on_purpose, other) is None
    except Exception:
        return False


def prepare_session_start(folder: str | None, env: Mapping[str, str], *,
                          cwd: str | None = None) -> list[str]:
    """Answer, in Claude Code's global config for *env* (``~/.claude.json``,
    ``$CLAUDE_CONFIG_DIR/.claude.json`` or the legacy ``.config.json``:
    :mod:`aipager.claude_paths`), the questions Claude Code would open a
    session with, as its owner would in a terminal. A session started from
    Telegram cannot answer them: the first message lands on one as Enter.

    - *folder* (a real path, or None for none): trusted, as
      :func:`ensure_folder_trusted` does.
    - *cwd*: the folder the session starts in, also when *folder* is None
      (a folder that holds the home folder is never trusted here). Its
      project and local settings count only when it is trusted (now or
      already), as Claude Code reads them only then.
    - The ANTHROPIC_API_KEY the session gets (*env* with the ``env`` blocks
      of Claude Code's settings over it): answered per :func:`api_key_answer`.
    - The first-run screens: marked done when the session has a credential
      that needs no login screen.

    One read and one write of the file, under :func:`_update_json`'s rules.
    Returns what changed: ``"trusted"``, ``"key-approved"``,
    ``"key-declined"``, ``"onboarded"``. Never logs a key or part of one."""
    base = cwd if cwd is not None else folder
    paths = claude_paths.locate(env, cwd=base)
    if paths.global_relative:
        _log_relative(paths)
        return []
    project = base is not None and (folder is not None or _already_trusted(base, paths))
    cred = _session_credential(env, folder=base, project=project, paths=paths)
    folder = os.path.realpath(folder) if folder is not None else None
    done: list[str] = []
    notes: list[str] = []

    def change(data: dict) -> bool:
        # Runs again when the file changed under it: start over each time.
        done.clear()
        notes.clear()
        if folder is not None and _trust_in(data, folder):
            done.append("trusted")
        other = _other_login(data, cred.run_env, cred.layers, cred.paths)
        key_used = False
        if cred.key:
            answered, key_used = _answer_key(data, cred.key, cred.on_purpose, other, notes)
            if answered:
                done.append(answered)
        if (data.get("hasCompletedOnboarding") is not True
                and (key_used or cred.cloud or other)):
            data["hasCompletedOnboarding"] = True
            done.append("onboarded")
        return bool(done)

    wrote = _update_json(paths.global_config, change, empty_ok=paths.legacy)
    for note in notes:
        _log_note(note)
    return list(done) if wrote else []


def _log_relative(paths: claude_paths.ClaudePaths) -> None:
    """A relative CLAUDE_CONFIG_DIR puts Claude Code's files inside each
    session's own folder: aipager writes none of them there."""
    log.warning("claude bootstrap: CLAUDE_CONFIG_DIR is not an absolute path, so Claude Code "
                "keeps its config inside each session's folder (%s here); aipager does not "
                "write there - set an absolute CLAUDE_CONFIG_DIR", paths.start_home)


def _log_note(note: str) -> None:
    if note == "odd-record":
        log.warning("claude bootstrap: Claude Code's record of API key answers in "
                    "its global config is not in the expected shape - left as it is")
    elif note == "refused-no-login":
        log.warning("claude bootstrap: Claude Code was told not to use the ANTHROPIC_API_KEY "
                    "sessions get, and nothing else logs them in: they will answer \"Not "
                    "logged in\". To use the key, set it in aipager's daemon.env, or turn on "
                    "\"Use custom API key\" under /config in a terminal session")
    elif note == "refused-other-login":
        log.info("claude bootstrap: Claude Code was told not to use the session's "
                 "ANTHROPIC_API_KEY; sessions use the other login")


def _resolve(cmd: str) -> str | None:
    """Resolve an aipager helper script to an absolute path.

    Tries PATH, then the bin dir next to the running Python interpreter
    (true for pip / uv tool / pipx / Docker installs). Returns None if
    we can't find it — Claude Code does NOT augment PATH when running
    hook commands, so a bare name silently breaks the hook.
    """
    found = shutil.which(cmd)
    if found:
        return found
    candidate = Path(sys.executable).parent / cmd
    if candidate.exists() and os.access(candidate, os.X_OK):
        return str(candidate)
    return None


def _has_hook_cmd(entries: list, bare_name: str) -> bool:
    """Detect whether the aipager hook (or a user's wrapper around it)
    is already wired for this event.

    Matches any of:
    - Command is literally ``bare_name`` (e.g. ``aipager-hook``).
    - Command's basename starts with ``bare_name`` — catches wrapper
      scripts like ``aipager-hook-capped.sh`` /
      ``aipager-hook.wrapped`` that users deploy for rate-limits,
      memory caps, logging, etc. Documented convention: name your
      wrapper ``aipager-hook*`` and aipager will honor it instead
      of injecting a duplicate entry.
    """
    if not bare_name:
        return False
    for block in entries:
        for hook in (block or {}).get("hooks", []):
            cmd = (hook or {}).get("command", "") or ""
            if not cmd:
                continue
            if cmd == bare_name:
                return True
            basename = Path(cmd).name
            if basename.startswith(bare_name):
                return True
    return False


def _ensure_hooks_and_statusline(paths: claude_paths.ClaudePaths) -> bool:
    """Wire aipager-hook into every hook event + statusLine of the settings
    file the sessions read. Idempotent. True when the file was written."""
    hook_path = _resolve(_HOOK_CMD)
    statusline_path = _resolve(_STATUSLINE_CMD)
    if not hook_path:
        log.debug("claude bootstrap: %s not on PATH; skipping hook wiring", _HOOK_CMD)
        return False

    return _update_json(paths.user_settings, lambda settings: _wire_hooks(
        settings, hook_path, statusline_path))


def hooks_wired(path: Path) -> bool:
    """The settings file at *path* has an ``aipager-hook`` command entry
    (read only; an unreadable file has none)."""
    hooks = claude_paths.read_settings(path).get("hooks")
    if not isinstance(hooks, dict):
        return False
    try:
        return any(isinstance(entries, list) and _has_hook_cmd(entries, _HOOK_CMD)
                   for entries in hooks.values())
    except (AttributeError, TypeError):
        return False


#: Real paths of the settings files this process found aipager's hooks
#: in, each with the file's stat when it did (:func:`_file_stamp`): a
#: launch reads the file again only when that changed.
_WIRED: dict[str, tuple[int, int, int] | None] = {}


def _file_stamp(path: str) -> tuple[int, int, int] | None:
    """``(inode, size, mtime_ns)`` of *path*, or None when it cannot be
    stat'ed: any edit, replacement or removal changes it."""
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def _wire_and_check(paths: claude_paths.ClaudePaths) -> bool:
    """Wire the hooks into *paths*' settings file and say loudly when the
    sessions will have none. True when the file has aipager's hooks."""
    if paths.relative:
        _log_relative(paths)
        log.warning("claude bootstrap: sessions will start without aipager's hooks "
                    "(Claude Code reads them from %s)", paths.user_settings)
        return False
    try:
        if _ensure_hooks_and_statusline(paths):
            log.info("claude bootstrap: wired %s hooks + statusLine into %s",
                     _HOOK_CMD, paths.user_settings)
    except Exception:
        log.debug("claude bootstrap: failed to wire hooks", exc_info=True)
    # Stamp first, then read: an edit between the two leaves a stamp that
    # no longer matches, so the next launch reads the file again.
    real = os.path.realpath(paths.user_settings)
    stamp = _file_stamp(real)
    if hooks_wired(paths.user_settings):
        _WIRED[real] = stamp
        return True
    log.warning("claude bootstrap: sessions will start without aipager's hooks: %s has no "
                "%s entry (is %s on PATH, and is the file valid JSON?) - run `aipager "
                "doctor`", paths.user_settings, _HOOK_CMD, _HOOK_CMD)
    return False


def ensure_session_hooks(paths: claude_paths.ClaudePaths) -> bool:
    """Make sure the settings file a session about to start reads has
    aipager's hooks, so a ``CLAUDE_CONFIG_DIR`` changed in daemon.env since
    start is wired at the next launch. A file this process already found
    wired costs one stat; once it changed it is read again, and when it
    lost the hooks (a hand edit, another tool rewriting it, a restore)
    they are wired again, with a WARNING. True when the file has them.
    Never raises."""
    try:
        real = os.path.realpath(paths.user_settings)
        if real in _WIRED:
            stamp = _file_stamp(real)
            if stamp is not None and stamp == _WIRED[real]:
                return True
            if hooks_wired(paths.user_settings):
                _WIRED[real] = stamp
                return True
            del _WIRED[real]
            log.warning("claude bootstrap: %s lost aipager's hooks since they were wired; "
                        "wiring them again", paths.user_settings)
        return _wire_and_check(paths)
    except Exception:
        log.debug("claude bootstrap: failed to check the sessions' hooks", exc_info=True)
        return False


def _wire_hooks(settings: dict, hook_path: str, statusline_path: str | None) -> bool:
    """:func:`_ensure_hooks_and_statusline`'s edit of *settings*, in place;
    True when it changed anything."""
    changed = False

    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        settings["hooks"] = hooks
        changed = True
    entry = {"type": "command", "command": hook_path}
    for event in _HOOK_EVENTS:
        entries = hooks.get(event)
        if not isinstance(entries, list):
            entries = []
            hooks[event] = entries
            changed = True
        if _has_hook_cmd(entries, _HOOK_CMD):
            continue
        if event == "PreModelSwitch":
            # Its own dict: `entry` is shared by reference with every
            # other event, and only this one gets a timeout.
            entries.append({"hooks": [{
                "type": "command", "command": hook_path,
                "timeout": _MODEL_SWITCH_HOOK_TIMEOUT_SECONDS,
            }]})
        elif event in _TOOL_MATCHER_EVENTS:
            entries.append({"matcher": "*", "hooks": [entry]})
        else:
            entries.append({"hooks": [entry]})
        changed = True

    # statusLine — keep an existing working entry; otherwise install ours.
    existing_sl = settings.get("statusLine") or {}
    existing_cmd = (existing_sl.get("command", "")
                    if isinstance(existing_sl, dict) else "")
    sl_good = bool(
        existing_cmd and (
            shutil.which(existing_cmd)
            or (os.path.isabs(existing_cmd)
                and os.path.exists(existing_cmd)
                and os.access(existing_cmd, os.X_OK))
        )
    )
    if not sl_good and statusline_path:
        settings["statusLine"] = {"type": "command", "command": statusline_path}
        changed = True
    return changed


def bootstrap_claude_settings(workdir: str | None = None) -> ProvenanceInfo | None:
    """Write the acceptance flags + hooks that claude-code's wizard
    would normally configure interactively, then resolve the claude
    binary + auth shape for the startup provenance line. Idempotent;
    safe to call on every daemon start. Never raises.

    ``workdir`` defaults to the daemon's cwd (which is also the default
    cwd for spawned sessions — see ``dtach/inject.py:_PROJECT_DIR``).

    Returns ``None`` if binary resolution failed (nothing to report);
    otherwise a :class:`ProvenanceInfo` whose ``.lines`` is exactly
    :func:`aipager.claude_resolve.format_provenance`'s output. One log
    line is emitted per line, at the point resolution happens — this
    function is called once per daemon lifetime, before ``bot.start()``,
    so it never sends Telegram itself; the caller (``cli/daemon.py``)
    does that after the bot is up.
    """
    if workdir is None:
        workdir = os.environ.get("AIPAGER_WORK_DIR", os.getcwd())
    from aipager import claude_resolve, daemon_secrets

    # The environment sessions get: it says where Claude Code keeps the
    # files written below, and it is what auth is probed with further down.
    session_env = daemon_secrets.build_session_env()
    paths = claude_paths.locate(session_env, cwd=workdir)
    if paths.from_env:
        log.info("claude bootstrap: sessions read Claude Code's config from %s "
                 "(CLAUDE_CONFIG_DIR)", paths.config_home)
    try:
        if not paths.relative and _ensure_bypass_accepted(paths):
            log.info("claude bootstrap: set skipDangerousModePermissionPrompt=true in %s",
                     paths.user_settings)
    except Exception:
        log.debug("claude bootstrap: failed to patch settings.json", exc_info=True)
    # The work folder is not marked trusted here: launch_session marks a
    # session's folder right before a session starts in it, never / or a
    # folder above home. A session started in one of those that is not
    # trusted yet would stop at Claude Code's trust question, so say so
    # once at start.
    try:
        from aipager import safety
        if safety.holds_home_folder(workdir) and not _already_trusted(workdir, paths):
            log.warning("claude bootstrap: the default session folder %s is / or a folder "
                        "above the home folder and Claude Code does not trust it; a session "
                        "started there stops at its \"Do you trust this folder?\" question - "
                        "start aipager in a project folder (`aipager service install` runs it "
                        "in the home folder) or set AIPAGER_WORK_DIR", workdir)
    except Exception:
        log.debug("claude bootstrap: failed to check the default session folder", exc_info=True)
    _wire_and_check(paths)

    try:
        resolved = claude_resolve.resolve_claude_binary()
    except claude_resolve.ClaudeNotFoundError as e:
        log.warning("claude bootstrap: could not resolve a claude binary: %s", e)
        return None

    try:
        # Auth is probed exactly here — once per daemon lifecycle — using
        # the SAME environment a real session launch gets
        # (daemon_secrets.build_session_env()), never the daemon's own
        # bare os.environ. Under systemd's LoadCredential=, the token
        # never enters the daemon's own environ, so probing against it
        # would falsely report "not logged in" while sessions
        # authenticate fine. See claude_resolve's module docstring.
        auth = claude_resolve.detect_auth(
            resolved.chosen.path, resolved.chosen.version, session_env,
        )
        lines = claude_resolve.format_provenance(resolved, auth)
    except Exception:
        log.debug("claude bootstrap: provenance formatting failed", exc_info=True)
        return None

    for line in lines:
        log.info("claude bootstrap: %s", line)

    if auth.source in ("probe-failed", "version-gated"):
        log.info("claude bootstrap: cheap auth check undetermined (%s)",
                 auth.source)

    # Deferred unconditionally — including when auth status claims we are
    # logged in, which is exactly the case it gets wrong. The real
    # validation costs a network round-trip and the recovery sweep can
    # spend ~20s in blocking subprocesses, and this function runs BEFORE
    # bot.start(); doing either here would delay the daemon reaching
    # Telegram in precisely the situation where remote control matters
    # most. recover_auth_or_notice() does the work, off the startup path,
    # once the bot is already live.
    return ProvenanceInfo(
        lines=lines, auth_ok=auth.logged_in,
        auth_source=auth.source if isinstance(auth.source, str) else "unknown",
        pending=PendingAuthCheck(
            claude_path=resolved.chosen.path,
            version=resolved.chosen.version,
            session_env=session_env,
        ),
    )


#: The credential kind of Claude's own saved login (its credentials file).
#: The other kinds are the environment variables that hold one.
STORED_LOGIN = "stored login"


@dataclass(frozen=True)
class AuthNotice:
    """What the startup auth notice says, for :mod:`aipager.bot.auth_notice`
    to word in each chat's language.

    Machine-free by construction: *found* holds only the names of the
    environment variables a credential was found in (:func:`_kinds_in`) and
    :data:`STORED_LOGIN`, never a value, a path, a file name or a version.
    *fix* is what to do about it (:func:`_fix_for`): ``"login"``,
    ``"gateway"``, ``"cloud"`` or ``"variable"``."""

    found: tuple[str, ...] = ()
    rejected: bool = False
    fix: str = "login"
    #: For ``fix == "variable"``: the variables holding the refused credential.
    variables: tuple[str, ...] = ()


def _fix_for(env: Mapping[str, str], *, rejected: bool = False) -> tuple[str, tuple[str, ...]]:
    """What fixes a refused or missing credential in the setup Claude Code
    runs with (*env* with Claude's settings env laid over it), and for
    ``"variable"`` the variables to look at: the cloud provider's
    credentials when a provider switch is on, the key for the address when
    ANTHROPIC_BASE_URL points at a gateway or another provider, the
    variables themselves when a token or key in the environment was
    refused (Claude Code uses it before a saved login, so logging in again
    changes nothing), else a Claude login."""
    from aipager import claude_resolve

    effective = claude_env(env)
    if any(_switch_on(effective.get(var)) for var in _CLOUD_SWITCHES):
        return "cloud", ()
    if claude_resolve.uses_custom_endpoint(effective):
        return "gateway", ()
    # The Bedrock token is read only with the Bedrock switch on (returned
    # "cloud" above): here it is one Claude Code ignored.
    held = tuple(var for var in _kinds_in(effective) if var in _ANTHROPIC_CREDENTIAL_VARS)
    if rejected and held:
        return "variable", held
    return "login", ()


def recover_auth_or_notice(pending: PendingAuthCheck) -> AuthNotice | None:
    """Run the recovery sweep; return what to tell Telegram, or ``None``.

    Blocking (subprocesses) — call it via ``asyncio.to_thread`` so it
    never stalls the event loop. ``None`` means a working credential was
    found and the operator needs to hear nothing. Never raises except
    ``AssertionError``, which only the test-suite safety net produces.
    """
    from aipager import claude_resolve

    check = claude_resolve.validate_credential(
        pending.claude_path, pending.session_env)

    if check.state == "valid":
        return None
    if check.state == "unknown":
        # Offline, a timeout, or something Claude said that we do not
        # recognise. We have not learned that the credential is bad, only
        # that we could not tell — and warning on every restart behind a
        # flaky network would train the operator to ignore this message.
        log.info("claude bootstrap: credential validation inconclusive (%s); "
                 "staying quiet", check.detail)
        return None

    log.warning("claude bootstrap: credential did not work (%s)", check.state)
    recovered, found_kinds = _recover_auth(
        pending.claude_path, pending.version, pending.session_env,
    )
    if recovered:
        log.info("claude bootstrap: recovered a working credential; staying quiet")
        return None
    rejected = check.state == "rejected"
    fix, variables = _fix_for(pending.session_env, rejected=rejected)
    return AuthNotice(found=tuple(found_kinds), rejected=rejected, fix=fix,
                      variables=variables)


# Every variable that holds a credential, plus the cloud provider switches
# (_CLOUD_SWITCHES): a wider set than claude_resolve._ENV_AUTH_VARS, which
# only attributes a *working* auth to "env". Reporting a narrower set here
# would tell a Bedrock/Vertex operator "couldn't find a credential
# anywhere" while a perfectly real, credential-shaped variable was sitting
# in the environment being rejected. Each is reported by its name.
_CREDENTIAL_VARS = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    # A gateway's or provider's bearer token, not a `claude setup-token` one.
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "AWS_BEARER_TOKEN_BEDROCK",
)
_AUTH_ENV_KEYS = frozenset((*_CREDENTIAL_VARS, *_CLOUD_SWITCHES))
#: The ones Claude Code uses without a cloud provider switch, before a saved login.
_ANTHROPIC_CREDENTIAL_VARS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN",
                              "ANTHROPIC_API_KEY")


def _kinds_in(env: Mapping[str, str]) -> list[str]:
    """The credentials present in *env*, by variable name, in a fixed order.
    Never values. A token or key counts when it is not blank: one whose
    literal value is "0" is still a token. A cloud provider switch counts
    only when Claude Code reads it as on (:func:`_switch_on`): reporting
    one it reads as off would send the operator to fix something they
    turned off on purpose."""
    kinds = [var for var in _CREDENTIAL_VARS
             if isinstance(env.get(var), str) and env[var].strip()]
    kinds += [var for var in _CLOUD_SWITCHES if _switch_on(env.get(var))]
    return kinds


def _recover_auth(
    claude_path: str, version: str, failing_env: dict[str, str],
) -> tuple[bool, list[str]]:
    """Look for a credential that actually authenticates. Never raises.

    Returns ``(recovered, found_kinds)``. Strictly **read-only**: it
    probes candidates and, on success, records the winning overlay in
    memory via :func:`aipager.daemon_secrets.set_recovered_credential`
    so real session launches get it too. It never writes, renames, or
    repairs a credential file — in particular it must never reach for
    ``inject._stash_expired_credentials_file``, which renames the user's
    own credentials file and exists only as legacy compensation for a
    Claude Code bug fixed in 2.1.225.

    *failing_env* is the environment that was just probed and rejected,
    so anything already in it is recorded as "found" but not re-probed.
    """
    from aipager import claude_resolve, daemon_secrets

    # As Claude Code runs: with its settings env laid over the environment,
    # where its docs put a cloud provider's switch.
    found: list[str] = list(_kinds_in(claude_env(failing_env)))

    def _remember(kind: str) -> None:
        if kind not in found:
            found.append(kind)

    # A stored login is claude's own credentials file. Claude reads it
    # itself, so there is no overlay to try — its presence is only worth
    # reporting, and only as a kind.
    try:
        if claude_paths.locate(failing_env).credentials.exists():
            _remember("stored login")
    except OSError:
        pass

    candidates: list[tuple[str, dict[str, str]]] = []

    # daemon.env read directly. Under systemd, $CREDENTIALS_DIRECTORY
    # shadows it entirely, so a stale credential materialised into the
    # unit's private directory can lose to a good one sitting in the
    # config file the operator actually edits.
    try:
        direct = daemon_secrets._parse_env_file(
            daemon_secrets.DAEMON_ENV_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        direct = {}
    if direct:
        for kind in _kinds_in(direct):
            _remember(kind)
        # Only an *auth* key differing is worth a fresh ~5s probe. An
        # unrelated KEY=VALUE line in daemon.env changing nothing about
        # the credential would otherwise buy a redundant re-probe of a
        # combination already known to fail.
        if any(direct.get(k) != failing_env.get(k)
               for k in direct if k in _AUTH_ENV_KEYS):
            candidates.append(("aipager config", direct))

    # The login shell. This is the shape the whole runtime-environment
    # work exists for: a token exported from ~/.bashrc, invisible to a
    # systemd unit because units never source shell startup files.
    try:
        from aipager.service import _discover_token_via_login_shell
        shell_token = _discover_token_via_login_shell()
    except AssertionError:
        # tests/conftest.py's _no_real_login_shell_probe raises this on
        # purpose, so a test that reaches a real login-shell spawn fails
        # loudly. Swallowing it here would quietly disarm that safety
        # net. Production never raises AssertionError from this call, so
        # re-raising costs the daemon nothing.
        raise
    except Exception:
        shell_token = None
    if shell_token:
        _remember("CLAUDE_CODE_OAUTH_TOKEN")
        if shell_token != failing_env.get("CLAUDE_CODE_OAUTH_TOKEN"):
            candidates.append(
                ("login shell", {"CLAUDE_CODE_OAUTH_TOKEN": shell_token}))

    for origin, overlay in candidates:
        try:
            probe_env = dict(failing_env)
            probe_env.update(overlay)
            # validate_credential, NOT detect_auth: the latter only sees
            # that a token exists, so the sweep would cheerfully
            # "recover" onto a second dead credential and go quiet.
            status = claude_resolve.validate_credential(claude_path, probe_env)
        except AssertionError:
            # Same carve-out as the login-shell call above: the suite's
            # _no_real_credential_probe guard raises this so an unmocked
            # test fails loudly instead of quietly spending the
            # operator's money. `except Exception` swallowed it, which
            # disarmed the guard on this path exactly as it would have
            # on that one.
            raise
        except Exception:
            continue
        if status.state == "valid":
            log.info("claude bootstrap: credential from %s authenticates; "
                     "using it for sessions", origin)
            try:
                daemon_secrets.set_recovered_credential(overlay)
            except Exception:
                log.debug("claude bootstrap: could not record the recovered "
                          "credential", exc_info=True)
                return False, found
            return True, found

    return False, found
