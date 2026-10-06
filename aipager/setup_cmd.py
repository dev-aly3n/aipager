"""`aipager setup`: set aipager up with no prompts (for scripts and
coding agents).

Produces the same install as the first-run wizard (``aipager config``):
``bot_token`` plus one DM scope whose only member is the person (role
owner, or admin) in ``aipager.yaml`` (mode 0600, through ``scope_io``),
the Claude Code hooks and statusLine in ``~/.claude/settings.json``
(through ``settings_patch``), the same dependency check, and with
``--service`` the background service. It reuses the wizard's code
through its non-printing cores and never reaches a prompt.

Order (see the design for the reasons): validate flags, read the token
(``--token-file`` or ``--token-stdin`` only), getMe, compute every local
check without writing, send a test message (only when ``aipager.yaml``
changes), then write. Every refusal before the write phase leaves every
file as it was. Each outcome has its own exit code (``EXIT_*``) and, with
``--json``, one JSON object on stdout (everything else goes to stderr).

The token is never printed, logged, put in the environment or in an
exception message: every line setup emits goes through :func:`_scrub`.
``aipager setup detect-chat`` lives in :mod:`aipager.setup_detect`.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass, field, replace

# ----- exit codes (stable, documented in docs/commands.md) -----
EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_DEPS_MISSING = 3
EXIT_TOKEN_REJECTED = 4
EXIT_CHAT_NOT_STARTED = 5
EXIT_EXISTING_INSTALL = 6
EXIT_TELEGRAM_UNREACHABLE = 7
EXIT_DETECT_TIMEOUT = 8
EXIT_DAEMON_RUNNING = 9
EXIT_UPDATES_CONFLICT = 10
EXIT_INTERRUPTED = 130

#: Sent to the person's DM once, before anything is written.
SETUP_TEST_TEXT = ("aipager is set up for you. Messages from your Claude Code "
                   "sessions will arrive in this chat.")

#: The token file / stdin is read up to this many bytes (characters).
TOKEN_INPUT_MAX = 4096
#: Seconds ``--token-stdin`` waits for the pipe to close.
STDIN_READ_TIMEOUT = 30.0

#: `changed` values, in the order they are reported.
CHANGE_ORDER = ("migrated_v1", "bot_token", "owner_dm", "role",
                "settings_json", "service")
_YAML_CHANGES = ("migrated_v1", "bot_token", "owner_dm", "role")

_CHAT_ID_RE = re.compile(r"\d{1,15}")
_CANT_INITIATE_RE = re.compile(r"can[’']?t\s+initiate\s+conversation", re.I)
_BLOCKED_RE = re.compile(r"blocked\s+by\s+the\s+user", re.I)

ROLES = ("owner", "admin")


class SetupError(Exception):
    """A refusal with its exit code, error code and the fix to show.
    Never carries the token (messages are built from fixed text)."""

    def __init__(self, code: int, error: str, message: str,
                 fix: str | None = None) -> None:
        super().__init__(error)
        self.code = code
        self.error = error
        self.message = message
        self.fix = fix


@dataclass
class _Run:
    """One invocation's state. ``token`` is kept out of ``repr``."""

    command: str
    as_json: bool
    doc: dict
    token: str = field(default="", repr=False)
    token_hint: str = "--token-file PATH"
    plain_out: list[str] = field(default_factory=list)
    plain_err: list[str] = field(default_factory=list)


@dataclass
class _Plan:
    """What :func:`_plan_config` decided for ``aipager.yaml``."""

    scopes: list
    changes: list[str]
    fresh: bool
    needs_force: bool = False
    grants_owner: bool = False
    replaced_chat: int | None = None
    error: str | None = None


# ----- redaction -----

def _scrub(text: str, token: str = "") -> str:
    """Remove the token (whole, its secret half, and anything shaped like
    a token) from *text*."""
    from aipager.errors import redact_bare_token
    if token:
        text = text.replace(token, "<redacted>")
        secret = token.partition(":")[2]
        if secret:
            text = text.replace(secret, "<redacted>")
    return redact_bare_token(text)


# ----- token input -----

def _read_token(args: argparse.Namespace) -> tuple[str, list[dict]]:
    """The bot token from ``--token-file`` or ``--token-stdin``, and any
    warnings. Raises :class:`SetupError` (exit 2). The content is never
    echoed: a malformed file is reported by its path only."""
    from aipager.wizard._constants import _TOKEN_RE
    from aipager.wizard.telegram_api import _normalize_token

    path = getattr(args, "token_file", None)
    warnings: list[dict] = []
    if path is not None:
        unreadable = f"Cannot read the token file {path}."
        fix = "Pass --token-file PATH with a readable file that holds the bot token."
        try:
            st = os.stat(path)
        except (OSError, ValueError):
            raise SetupError(EXIT_USAGE, "token_file_unreadable", unreadable, fix)
        if not stat.S_ISREG(st.st_mode):
            raise SetupError(EXIT_USAGE, "token_file_unreadable",
                             f"The token file {path} is not a regular file.", fix)
        try:
            with open(path, "rb") as f:
                data = f.read(TOKEN_INPUT_MAX + 1)
        except OSError:
            raise SetupError(EXIT_USAGE, "token_file_unreadable", unreadable, fix)
        if len(data) > TOKEN_INPUT_MAX:
            raise SetupError(
                EXIT_USAGE, "token_malformed",
                f"The token file {path} is larger than {TOKEN_INPUT_MAX} bytes; "
                "it should hold only the bot token.", fix)
        try:
            raw = data.decode("utf-8")
        except UnicodeError:
            raise SetupError(EXIT_USAGE, "token_file_unreadable",
                             f"The token file {path} is not UTF-8 text.", fix)
        if st.st_mode & 0o077:
            warnings.append({
                "code": "token_file_shared",
                "message": (f"The token file {path} is readable by other users; "
                            "`chmod 600` it, or delete it once setup succeeds."),
            })
        where = f"The token file {path}"
        fix = "Pass --token-file PATH with a file that holds only the bot token."
    else:
        stdin = getattr(sys, "stdin", None)
        try:
            is_tty = bool(stdin is not None and stdin.isatty())
        except Exception:
            is_tty = False
        if is_tty:
            raise SetupError(
                EXIT_USAGE, "token_stdin_is_tty",
                "--token-stdin reads the token from a pipe, but stdin is a terminal.",
                "Pipe the token in (for example `aipager setup --token-stdin ... "
                "< token.txt`), or use --token-file PATH.")
        try:
            raw = _read_stdin(stdin) if stdin is not None else ""
        except (OSError, UnicodeError, ValueError):
            raw = ""
        raw = raw or ""
        where = "stdin"
        fix = "Pipe only the bot token into --token-stdin."
        if len(raw) > TOKEN_INPUT_MAX:
            raise SetupError(EXIT_USAGE, "token_malformed",
                             f"stdin held more than {TOKEN_INPUT_MAX} characters; "
                             "it should hold only the bot token.", fix)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
    token = _normalize_token(raw)
    if not _TOKEN_RE.fullmatch(token):
        raise SetupError(
            EXIT_USAGE, "token_malformed",
            f"{where} does not hold a bot token (it looks like "
            "123456789:AA... and comes from @BotFather).", fix)
    return token, warnings


def _read_stdin(stdin) -> str | bytes:
    """At most ``TOKEN_INPUT_MAX + 1`` bytes of stdin, up to its end.

    A real pipe is read through its file descriptor with a deadline of
    :data:`STDIN_READ_TIMEOUT` seconds: some agent harnesses leave stdin
    open without ever closing it, and a plain ``read()`` would then wait
    forever. Raises ``SetupError(token_stdin_timeout)`` (exit 2) when the
    pipe has not closed by then. A stdin with no descriptor (a test's
    ``StringIO``) is read directly."""
    import select
    import time
    try:
        fd = stdin.fileno()
    except (AttributeError, OSError, ValueError):
        return stdin.read(TOKEN_INPUT_MAX + 1)
    deadline = time.monotonic() + STDIN_READ_TIMEOUT
    chunks: list[bytes] = []
    total = 0
    while total <= TOKEN_INPUT_MAX:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SetupError(
                EXIT_USAGE, "token_stdin_timeout",
                f"--token-stdin waited {STDIN_READ_TIMEOUT:g} seconds for stdin "
                "to close, and it did not.",
                "Pipe the token in and let the pipe close (for example `printf "
                "'%s\\n' \"$TOKEN\" | aipager setup --token-stdin ...`), or use "
                "--token-file PATH.")
        ready, _w, _x = select.select([fd], [], [], remaining)
        if not ready:
            continue
        chunk = os.read(fd, TOKEN_INPUT_MAX + 1 - total)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


# ----- flag validation -----

def _validate_flags(args: argparse.Namespace, run: _Run) -> None:
    """Refuse bad or misplaced flags (exit 2) before anything is read.
    Sets ``chat_id``/``role`` in the document once they are valid."""
    if getattr(args, "token_flag", None) is not None:
        raise SetupError(
            EXIT_USAGE, "token_on_command_line",
            "The bot token cannot be given on the command line (it would "
            "show in the process list and the shell history).",
            "Put the token in a file and pass --token-file PATH, or pipe it "
            "in with --token-stdin.")
    detect = run.command == "detect-chat"
    if detect:
        wrong = [flag for flag, attr in (
            ("--chat-id", "chat_id"), ("--role", "role"))
            if getattr(args, attr, None) is not None]
        wrong += [flag for flag, attr in (
            ("--service", "service"), ("--force", "force"),
            ("--dry-run", "dry_run")) if getattr(args, attr, False)]
        if wrong:
            raise SetupError(
                EXIT_USAGE, "usage",
                f"{', '.join(wrong)} cannot be used with `aipager setup detect-chat`.",
                f"Remove {', '.join(wrong)}; detect-chat only takes --token-file "
                "PATH or --token-stdin, --timeout SECONDS and --json.")
    elif getattr(args, "timeout", None) is not None:
        raise SetupError(
            EXIT_USAGE, "usage",
            "--timeout only applies to `aipager setup detect-chat`.",
            "Remove --timeout, or run `aipager setup detect-chat --timeout SECONDS`.")

    has_file = getattr(args, "token_file", None) is not None
    has_stdin = bool(getattr(args, "token_stdin", False))
    if has_file and has_stdin:
        raise SetupError(EXIT_USAGE, "token_source_conflict",
                         "Give the token one way only.",
                         "Use either --token-file PATH or --token-stdin, not both.")
    if not has_file and not has_stdin:
        raise SetupError(EXIT_USAGE, "missing_token_source",
                         "No bot token given.",
                         "Add --token-file PATH or --token-stdin.")
    run.token_hint = (f"--token-file {args.token_file}" if has_file
                      else "--token-stdin")
    if detect:
        return

    raw_chat = getattr(args, "chat_id", None)
    if raw_chat is None:
        raise SetupError(
            EXIT_USAGE, "usage", "No chat id given.",
            "Add --chat-id N (the person's numeric Telegram user id; "
            "`aipager setup detect-chat` finds it).")
    raw_chat = raw_chat.strip()
    if raw_chat.startswith("-") and _CHAT_ID_RE.fullmatch(raw_chat[1:]):
        raise SetupError(
            EXIT_USAGE, "bad_chat_id",
            "--chat-id must be a person's id (a positive number); groups "
            "are added with `aipager config`.",
            "Pass --chat-id N with the person's numeric Telegram user id.")
    if not _CHAT_ID_RE.fullmatch(raw_chat) or int(raw_chat) <= 0:
        raise SetupError(
            EXIT_USAGE, "bad_chat_id",
            "--chat-id must be a positive whole number.",
            "Pass --chat-id N with the person's numeric Telegram user id "
            "(`aipager setup detect-chat` finds it).")
    run.doc["chat_id"] = int(raw_chat)
    # `--role ''` is a given-but-empty value, not "use the default".
    role = getattr(args, "role", None)
    if role is None:
        role = "owner"
    if role not in ROLES:
        raise SetupError(EXIT_USAGE, "bad_role",
                         "--role must be owner or admin.",
                         "Pass --role owner or --role admin (the default is owner).")
    run.doc["role"] = role


# ----- Telegram -----

def _get_me_or_raise(run: _Run) -> str | None:
    """Validate the token with getMe; the bot's username (or ``None``).
    401/404 -> exit 4, anything else that fails -> exit 7."""
    from aipager.wizard import telegram_api
    info, code, explained = telegram_api._get_me(run.token)
    if info is None:
        if code in (401, 404):
            raise SetupError(
                EXIT_TOKEN_REJECTED, "token_rejected",
                "Telegram rejected the bot token.",
                "Check the token (copy it again from @BotFather, or create a "
                "new one with /token), then run this again.")
        raise SetupError(
            EXIT_TELEGRAM_UNREACHABLE, "telegram_unreachable",
            f"Could not reach Telegram: {explained}",
            "Check the network connection, then run this again.")
    username = info.get("username")
    username = username if isinstance(username, str) and username else None
    run.doc["bot_username"] = username
    return username


def _bot_link(username: str | None) -> str:
    return f"t.me/{username}" if username else "the bot in Telegram"


# ----- planning (pure) -----

def _member(scope, user_id: int):
    return next((m for m in scope.members if m.id == user_id), None)


def _operator_dm(scopes: list) -> tuple[object | None, bool]:
    """The install's operator DM and whether that is ambiguous.

    Among DM scopes holding a member whose id is the chat id, the single
    one whose member is ``owner``; else the single such scope. ``(None,
    False)`` when there is none, ``(None, True)`` when more than one fits.
    """
    candidates = [s for s in scopes
                  if s.kind == "dm" and _member(s, s.chat_id) is not None]
    owners = [s for s in candidates if _member(s, s.chat_id).role == "owner"]
    if len(owners) == 1:
        return owners[0], False
    if len(owners) > 1:
        return None, True
    if len(candidates) == 1:
        return candidates[0], False
    if not candidates:
        return None, False
    return None, True


def _plan_config(existing: list | None, existing_token: str, token: str,
                 chat_id: int, role: str, *, from_v1: bool = False) -> _Plan:
    """Decide the new scope list and what changes. Pure: no I/O."""
    from aipager.wizard.first_run import _owner_dm_scope

    if not existing:
        return _Plan(scopes=[_owner_dm_scope(chat_id, role)],
                     changes=["bot_token", "owner_dm"], fresh=True,
                     grants_owner=(role == "owner"))
    changes: list[str] = ["migrated_v1"] if from_v1 else []
    if token != existing_token:
        changes.append("bot_token")
    scopes = list(existing)
    target = next((s for s in scopes if s.chat_id == chat_id), None)
    plan = _Plan(scopes=scopes, changes=changes, fresh=False)
    if (target is not None and target.kind == "dm"
            and _member(target, chat_id) is not None):
        if _member(target, chat_id).role != role:
            changes.append("role")
            new_target = replace(target, members=tuple(
                replace(m, role=role) if m.id == chat_id else m
                for m in target.members))
            plan.scopes = [new_target if s is target else s for s in scopes]
            plan.grants_owner = role == "owner"
    else:
        operator, ambiguous = _operator_dm(scopes)
        if ambiguous:
            plan.error = "ambiguous_install"
            return plan
        changes.append("owner_dm")
        if operator is not None and operator is not target:
            plan.replaced_chat = operator.chat_id
        plan.scopes = [s for s in scopes
                       if s is not target and s is not operator]
        plan.scopes.append(_owner_dm_scope(chat_id, role))
        plan.grants_owner = role == "owner"
    plan.needs_force = any(c in changes for c in ("bot_token", "owner_dm", "role"))
    return plan


def _force_fix(plan: _Plan) -> tuple[str, str]:
    """The ``existing_install`` message and fix, naming what --force does."""
    what: list[str] = []
    if "bot_token" in plan.changes:
        what.append("replace the bot token")
    if "owner_dm" in plan.changes:
        what.append(f"replace the owner DM of chat {plan.replaced_chat}"
                    if plan.replaced_chat is not None
                    else "add an owner DM for this chat")
    if "role" in plan.changes:
        what.append("change the role of this chat's member")
    return ("aipager is already set up with a different bot token, chat or "
            "role, and setup never replaces them silently.",
            f"Add --force to {' and '.join(what)}.")


# ----- the run -----

def _base_doc(command: str, args: argparse.Namespace) -> dict:
    if command == "detect-chat":
        return {
            "command": "detect-chat", "status": None, "ok": False,
            "exit_code": None, "error": None, "message": None, "fix": None,
            "bot_username": None, "source": None, "candidate": None,
            "other_candidates": 0, "warnings": [], "next_step": None,
        }
    from aipager.wizard import settings_patch
    return {
        "command": "setup", "status": None, "ok": False, "exit_code": None,
        "error": None, "message": None, "fix": None,
        "dry_run": bool(getattr(args, "dry_run", False)),
        "bot_username": None, "chat_id": None, "role": None,
        "changed": [], "test_message": "not_attempted", "deps": None,
        "settings_json": {"path": str(settings_patch.CLAUDE_SETTINGS),
                          "status": "not_checked", "backup": None,
                          "repointed": 0},
        "daemon": None,
        "service": {"requested": bool(getattr(args, "service", False)),
                    "result": (None if getattr(args, "service", False)
                               else "not_requested")},
        "warnings": [], "next_step": None,
    }


def _deps_doc(deps: list) -> dict:
    return {
        "ok": all(d.path for d in deps),
        "items": [{"name": d.name, "found": bool(d.path), "path": d.path,
                   "fix": None if d.path else d.fix} for d in deps],
    }


def _ordered(changed) -> list[str]:
    return [c for c in CHANGE_ORDER if c in changed]


def _daemon_running() -> bool | None:
    """``True``/``False``, or ``None`` when detection itself failed.
    Callers treat ``None`` as running: ``--service`` must never restart a
    live daemon because the check could not tell."""
    from aipager.wizard import daemon_io
    try:
        return daemon_io._detect_daemon_running() is not None
    except Exception:
        return None


def _load_existing():
    """``(scopes, token, from_v1)`` of the current install, without
    writing. Raises ``SetupError(config_malformed)``."""
    from aipager import scope as scope_mod
    from aipager.wizard import _constants
    from aipager.wizard import scope_io

    fix = ("Fix the file by hand or run `aipager config`; setup never "
           "overwrites a file it cannot read.")
    try:
        scopes, token = scope_io.read_config()
    except scope_mod.ScopeConfigError as e:
        raise SetupError(EXIT_FAILURE, "config_malformed",
                         f"aipager.yaml cannot be read: {e}", fix)
    if scopes or scope_mod.CONFIG_PATH.exists():
        return scopes, token, False
    if not _constants.CONFIG_ENV.exists():
        return [], "", False
    from aipager import migrate
    try:
        derived = migrate._scopes_from_current()
    except Exception as e:
        raise SetupError(EXIT_FAILURE, "config_malformed",
                         f"The old config (config.env / team.yaml) cannot be "
                         f"read: {e}", fix)
    if derived is None:
        return [], "", False
    return derived[0], derived[1], True


def _setup(args: argparse.Namespace, run: _Run) -> int:
    from aipager.wizard import settings_patch

    doc = run.doc
    chat_id: int = doc["chat_id"]
    role: str = doc["role"]
    dry_run = doc["dry_run"]
    want_service = doc["service"]["requested"]
    username = _get_me_or_raise(run)

    # -- local checks: all computed, nothing written --
    errors: dict[str, SetupError] = {}
    plan: _Plan | None = None
    from_v1 = False
    try:
        existing, existing_token, from_v1 = _load_existing()
    except SetupError as e:
        errors["config_malformed"] = e
    else:
        plan = _plan_config(existing, existing_token, run.token, chat_id,
                            role, from_v1=from_v1)
        if plan.error == "ambiguous_install":
            errors["ambiguous_install"] = SetupError(
                EXIT_FAILURE, "ambiguous_install",
                "aipager.yaml has more than one DM that could be the owner's, "
                "so setup cannot tell which one to replace (even with --force).",
                "Run `aipager config` to choose, or remove the extra DM scope there.")
        elif plan.needs_force and not getattr(args, "force", False):
            message, fix = _force_fix(plan)
            errors["existing_install"] = SetupError(
                EXIT_EXISTING_INSTALL, "existing_install", message, fix)
    deps = settings_patch.check_deps()
    doc["deps"] = _deps_doc(deps)
    settings_plan = None
    try:
        settings_plan = settings_patch.plan_settings()
    except (ValueError, OSError) as e:
        errors["settings_invalid"] = SetupError(
            EXIT_FAILURE, "settings_invalid", str(e),
            f"Fix {settings_patch.CLAUDE_SETTINGS} (it must be valid JSON), "
            "then run this again.")
    else:
        doc["settings_json"]["repointed"] = settings_plan.repointed
        doc["settings_json"]["status"] = ("would_change" if settings_plan.changed
                                          else "unchanged")
    missing = [d for d in deps if not d.path]
    if missing:
        errors["deps_missing"] = SetupError(
            EXIT_DEPS_MISSING, "deps_missing",
            "Missing: " + ", ".join(d.name for d in missing)
            + ". Nothing was written.",
            "Install them, then run this again: "
            + "; ".join(f"{d.name}: {d.fix}" for d in missing))
    detected = _daemon_running()
    running = detected is not False
    doc["daemon"] = {"running": running, "reload": "not_needed",
                     "restart_needed": False}
    if detected is None:
        doc["warnings"].append({
            "code": "daemon_unknown",
            "message": ("Could not tell whether an aipager daemon is running, "
                        "so setup treats it as running (it installs no "
                        "service and asks for a restart where one would be "
                        "needed). Check with `aipager doctor --json`."),
        })

    first_error = next((errors[k] for k in (
        "config_malformed", "ambiguous_install", "existing_install",
        "settings_invalid", "deps_missing") if k in errors), None)

    if dry_run:
        doc["test_message"] = "skipped_dry_run"
        would = list(plan.changes) if plan is not None else []
        if settings_plan is not None and settings_plan.changed:
            would.append("settings_json")
        if want_service:
            if running:
                doc["service"]["result"] = "skipped_daemon_running"
            else:
                doc["service"]["result"] = "would_install"
                from aipager import service
                up = service.unit_path()
                if up is None or not up.exists():
                    would.append("service")
        doc["changed"] = _ordered(would)
        if first_error is not None:
            raise first_error
        doc["status"] = "dry_run"
        doc["message"] = ("Dry run: nothing was sent or written. "
                          + (f"Would change: {', '.join(doc['changed'])}."
                             if doc["changed"] else "Nothing would change."))
        doc["next_step"] = "Run the same command without --dry-run to apply it."
        return EXIT_OK
    if first_error is not None:
        raise first_error
    assert plan is not None and settings_plan is not None

    # -- test send, before anything is written --
    yaml_changes = [c for c in plan.changes if c in _YAML_CHANGES]
    if yaml_changes:
        _test_send_or_raise(run, chat_id, username)
        doc["test_message"] = "sent"
    else:
        doc["test_message"] = "not_needed"

    # -- writes --
    # From the first write on, whatever fails (an error setup raises, or
    # an exception nobody expected) must still report what was written:
    # `changed: []` after a write would tell an agent nothing happened.
    changed: list[str] = []
    try:
        try:
            _write_config(run, plan, from_v1, chat_id, role, changed)
            if plan.grants_owner and any(c in changed
                                         for c in ("owner_dm", "role")):
                from aipager.wizard.first_run import _record_owner_grant
                _record_owner_grant(chat_id)
            if settings_plan.changed:
                backup = settings_patch.apply_settings(settings_plan)
                changed.append("settings_json")
                doc["settings_json"]["status"] = (
                    "patched" if settings_plan.exists else "created")
                doc["settings_json"]["backup"] = backup
        except OSError as e:
            raise SetupError(EXIT_FAILURE, "write_failed",
                             f"Writing failed: {e}",
                             "Fix the cause above, then run this again "
                             "(setup picks up where it stopped).")

        _after_yaml_change(run, plan, changed, running,
                           unknown=detected is None)

        if want_service:
            _install_service_step(run, running, changed)
    except BaseException:
        doc["changed"] = _ordered(changed)
        raise

    doc["changed"] = _ordered(changed)
    if plan.fresh:
        doc["status"] = "installed"
    elif changed:
        doc["status"] = "updated"
    else:
        doc["status"] = "unchanged"
    who = f"@{username}" if username else "the bot"
    if doc["status"] == "unchanged":
        doc["message"] = (f"Nothing to change: aipager is already set up for "
                          f"{who} (chat {chat_id}, role {role}).")
    else:
        doc["message"] = (f"aipager is set up for {who} (chat {chat_id}, "
                          f"role {role}). Changed: {', '.join(doc['changed'])}.")
    doc["next_step"] = _next_step(doc)
    return EXIT_OK


def _test_send_or_raise(run: _Run, chat_id: int, username: str | None) -> None:
    from aipager.wizard import telegram_api
    from aipager.wizard._constants import _CHAT_NOT_FOUND_RE

    sent, desc, code = telegram_api._send_message(run.token, chat_id,
                                                  SETUP_TEST_TEXT)
    if sent:
        return
    run.doc["test_message"] = "failed"
    desc = desc or ""
    link = _bot_link(username)
    if _CHAT_NOT_FOUND_RE.search(desc) or _CANT_INITIATE_RE.search(desc):
        raise SetupError(
            EXIT_CHAT_NOT_STARTED, "chat_not_started",
            f"Telegram will not let the bot message chat {chat_id} yet: that "
            "person has not pressed Start in the bot (or the id is not theirs). "
            "Nothing was written.",
            f"Open {link} and press Start, then run this again. Also check "
            "that --chat-id is the person's own numeric Telegram id.")
    if _BLOCKED_RE.search(desc):
        who = f"@{username}" if username else "the bot"
        raise SetupError(
            EXIT_CHAT_NOT_STARTED, "bot_blocked",
            f"Chat {chat_id} has blocked the bot. Nothing was written.",
            f"Unblock {who} in Telegram (open {link}, unblock it) and press "
            "Start, then run this again.")
    if code is None or code == 429 or code >= 500:
        raise SetupError(
            EXIT_TELEGRAM_UNREACHABLE, "telegram_unreachable",
            f"Could not send the test message: {desc or 'no answer'}. "
            "Nothing was written.",
            "Check the network connection, then run this again.")
    raise SetupError(
        EXIT_FAILURE, "test_send_failed",
        f"Telegram refused the test message: {desc or 'unknown error'}. "
        "Nothing was written.",
        "Check --chat-id (it must be the person's own numeric Telegram id), "
        "then run this again.")


def _write_config(run: _Run, plan: _Plan, from_v1: bool, chat_id: int,
                  role: str, changed: list[str]) -> None:
    """The ``aipager.yaml`` write (after a v1 migration when needed)."""
    from aipager.wizard import scope_io

    if from_v1:
        from aipager import migrate
        migrate.migrate_to_v2()
        changed.append("migrated_v1")
        scopes, token = scope_io.read_config()
        again = _plan_config(scopes, token, run.token, chat_id, role)
        same = (again.error is None and again.scopes == plan.scopes
                and again.changes == [c for c in plan.changes
                                      if c != "migrated_v1"])
        if not same:
            raise SetupError(
                EXIT_FAILURE, "write_failed",
                "aipager.yaml changed while setup ran (after migrating the "
                "old config.env), so nothing more was written.",
                "Run this again.")
    rest = [c for c in plan.changes if c in ("bot_token", "owner_dm", "role")]
    if not rest:
        return
    if plan.fresh:
        from aipager.wizard.first_run import _owner_dm_scope
        scope_io.commit_scope(_owner_dm_scope(chat_id, role), run.token)
    else:
        scope_io.replace_scopes(plan.scopes, run.token)
    changed.extend(rest)


def _after_yaml_change(run: _Run, plan: _Plan, changed: list[str],
                       running: bool, *, unknown: bool = False) -> None:
    """Live-reload a running daemon after a scope change; a new token
    needs a restart, which setup never does itself.

    *unknown*: daemon detection failed. No reload is tried (it would
    detect again, and signal a PID nobody could confirm); a restart is
    asked for instead. A reload that raises is reported the same way:
    the yaml is already written, so the run must not fail over it."""
    daemon = run.doc["daemon"]
    if not any(c in changed for c in _YAML_CHANGES):
        return
    if "bot_token" in changed or unknown:
        daemon["reload"] = "not_reloaded" if running else "not_needed"
        daemon["restart_needed"] = running
        return
    from aipager.wizard import daemon_io
    try:
        outcome, problem = daemon_io._live_reload()
    except Exception:
        daemon["reload"] = "not_reloaded"
        daemon["restart_needed"] = running
        return
    daemon["reload"] = outcome
    if outcome == "refused":
        run.doc["warnings"].append({
            "code": "reload_refused",
            "message": f"The running daemon would refuse the new config: {problem}",
        })
    elif outcome == "not_reloaded":
        daemon["restart_needed"] = running


def _install_service_step(run: _Run, running: bool, changed: list[str]) -> None:
    from aipager import scope as scope_mod
    from aipager import service

    svc = run.doc["service"]
    if running:
        svc["result"] = "skipped_daemon_running"
        return
    fail_fix = ("Run `aipager service install --yes` to see the error, "
                "then `aipager doctor --json`.")
    try:
        loaded = scope_mod.load_scopes(scope_mod.CONFIG_PATH)
    except scope_mod.ScopeConfigError:
        loaded = None
    if loaded is None:
        svc["result"] = "failed"
        raise SetupError(EXIT_FAILURE, "service_failed",
                         "aipager.yaml did not load, so the service was not "
                         "installed.", fail_fix)
    path = service.unit_path()

    def _bytes():
        try:
            return path.read_bytes() if path is not None else None
        except OSError:
            return None

    before = _bytes()
    rc = service.install_service(yes=True)
    if rc != 0:
        svc["result"] = "failed"
        run.doc["changed"] = _ordered(changed)
        raise SetupError(EXIT_FAILURE, "service_failed",
                         f"The service install failed (exit {rc}). The config "
                         "is already written.", fail_fix)
    if before is not None and before == _bytes():
        svc["result"] = "already_installed"
    else:
        svc["result"] = "installed"
        changed.append("service")


def _next_step(doc: dict) -> str:
    daemon = doc.get("daemon") or {}
    if daemon.get("restart_needed"):
        why = ("to use the new bot token" if "bot_token" in doc.get("changed", [])
               else "to apply the change")
        how = ("run `aipager service stop` and then `aipager service start` "
               "(or stop a foreground `aipager start` and run it again).")
        if any(w.get("code") == "daemon_unknown" for w in doc["warnings"]):
            return (f"If an aipager daemon is running, restart it {why}: "
                    f"{how}")
        return f"Restart the daemon {why}: {how}"
    if any(w.get("code") == "reload_refused" for w in doc["warnings"]):
        return ("Fix the problem in the warning (run `aipager config`), then "
                "check with `aipager doctor --json`.")
    if doc["service"]["result"] in ("installed", "already_installed"):
        return ("aipager runs as a background service. Check the install with "
                "`aipager doctor --json`.")
    if daemon.get("running"):
        return ("aipager is running. Check the install with "
                "`aipager doctor --json`.")
    return ("Start aipager with `aipager service install` (background "
            "service) or `aipager start` (foreground), then check it with "
            "`aipager doctor --json`.")


# ----- output -----

def _fail(run: _Run, code: int, error: str, message: str,
          fix: str | None) -> int:
    doc = run.doc
    doc.update(status="error", ok=False, exit_code=code, error=error,
               message=message, fix=fix, next_step=fix)
    return code


def _emit(run: _Run, out) -> None:
    """Write the result: one scrubbed JSON object to *out*, or plain lines
    (results to *out*, warnings and errors to stderr)."""
    doc = run.doc
    if run.as_json:
        out.write(_scrub(json.dumps(doc, indent=2), run.token) + "\n")
        out.flush()
        return
    err = sys.stderr
    for w in doc.get("warnings") or []:
        err.write(_scrub(f"! {w['message']}", run.token) + "\n")
    if doc.get("status") == "error":
        err.write(_scrub(f"✗ {doc['message']}", run.token) + "\n")
        if doc.get("fix"):
            err.write(_scrub(f"  {doc['fix']}", run.token) + "\n")
        err.flush()
        return
    lines = [f"✓ {doc['message']}" if doc.get("status") != "unchanged"
             else doc["message"]]
    lines += run.plain_out
    if doc.get("next_step"):
        lines.append(f"Next: {doc['next_step']}")
    for line in lines:
        out.write(_scrub(line, run.token) + "\n")
    out.flush()
    err.flush()


def cmd_setup(args: argparse.Namespace) -> int:
    """`aipager setup [detect-chat]`. Returns the exit code; never raises
    (every exception is reported, scrubbed, as ``internal_error``)."""
    command = ("detect-chat" if getattr(args, "setup_action", None) == "detect-chat"
               else "setup")
    as_json = bool(getattr(args, "as_json", False))
    out = sys.stdout
    run = _Run(command=command, as_json=as_json, doc={})
    quarantine = (contextlib.redirect_stdout(sys.stderr) if as_json
                  else contextlib.nullcontext())
    try:
        with quarantine:
            # Inside the quarantine: building the document imports modules,
            # and import-time output must not reach the JSON stdout.
            run.doc = _base_doc(command, args)
            code = _dispatch(args, run)
    except SetupError as e:
        code = _fail(run, e.code, e.error, e.message, e.fix)
    except KeyboardInterrupt:
        code = _fail(run, EXIT_INTERRUPTED, "interrupted", "Interrupted.",
                     "Run the command again.")
    except BaseException as e:  # noqa: BLE001 (the boundary: scrub and report)
        # Defense in depth only: _emit scrubs every line it writes, and is
        # the guard of record (its tests cover this path).
        code = _fail(run, EXIT_FAILURE, "internal_error",
                     _scrub(f"{type(e).__name__}: {e}", run.token),
                     "Run `aipager doctor --json`; if this keeps happening, "
                     "report it at https://github.com/dev-aly3n/aipager/issues.")
    else:
        run.doc.update(ok=(code == EXIT_OK), exit_code=code)
    try:
        _emit(run, out)
    except Exception:
        pass
    return code


def _dispatch(args: argparse.Namespace, run: _Run) -> int:
    _validate_flags(args, run)
    run.token, warnings = _read_token(args)
    run.doc["warnings"].extend(warnings)
    if run.command == "detect-chat":
        from aipager.setup_detect import cmd_detect_chat
        return cmd_detect_chat(args, run)
    return _setup(args, run)


__all__ = ["cmd_setup", "SETUP_TEST_TEXT"]
