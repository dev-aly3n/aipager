"""Interactive setup wizard for `aipager config`.

UX goals:
- Each question is asked once with arrow-key / Y-n prompts via
  :mod:`questionary`. Long-running Telegram API calls are wrapped in
  ``rich.status`` spinners so the terminal never appears frozen.
- Successful steps print a ``✓`` line; failures use the shared
  ``ui.err_block`` rendering.
- Off-TTY (pytest, scripts), questionary falls back gracefully and the
  spinner is suppressed by rich.

Walks the user through:
  1. Bot token + verify via getMe
  2. Chat ID via getUpdates auto-detect (or manual paste) + test send
  3. Dep check (dtach, claude, hook scripts)
  4. Patch ~/.claude/settings.json with hooks + statusLine (back up first)
  5. Write ~/.config/aipager/config.env (0600)

Idempotent — safe to re-run; existing aipager-hook entries are not
duplicated.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import questionary
from questionary import Style

from aipager.errors import friendly_error, friendly_warn
from aipager.ui import GLYPH_OK, console, err_console, hint, ok, rule, step

CONFIG_DIR = Path.home() / ".config" / "aipager"
CONFIG_ENV = CONFIG_DIR / "config.env"
TEAM_YAML = CONFIG_DIR / "team.yaml"
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"

HOOK_CMD = "aipager-hook"
STATUSLINE_CMD = "aipager-statusline"
HOOK_EVENTS = (
    "SessionStart", "SessionEnd", "UserPromptSubmit",
    "PreToolUse", "PostToolUse", "PermissionRequest",
    "Notification", "Stop", "SubagentStop", "PreCompact",
)
TOOL_MATCHER_EVENTS = {"PreToolUse", "PostToolUse", "PermissionRequest"}

_TOKEN_RE = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{20,80}")
_CHAT_NOT_FOUND_RE = re.compile(r"chat\s*[\s_-]*not\s*[\s_-]*found", re.I)

# Restrained Inquirer-style: cyan question mark, green checkmark after
# commit, dim instruction/default text. Matches the rest of aipager.
_PROMPT_STYLE = Style([
    ("qmark", "fg:cyan bold"),
    ("question", "bold"),
    ("answer", "fg:cyan bold"),
    ("pointer", "fg:cyan bold"),
    ("highlighted", "fg:cyan bold"),
    ("selected", "fg:cyan"),
    ("instruction", "fg:#888888"),
    ("text", ""),
    ("disabled", "fg:#888888 italic"),
])


# ----- helpers -----

def _ask(prompt) -> object:
    """Run a questionary prompt; raise KeyboardInterrupt on Ctrl-C."""
    answer = prompt.ask()
    if answer is None:
        raise KeyboardInterrupt
    return answer


def _normalize_token(raw: str) -> str:
    """Pull a clean bot token out of common paste shapes."""
    if not raw:
        return ""
    raw = raw.strip().strip('"').strip("'")
    m = _TOKEN_RE.search(raw)
    if m:
        return m.group(0)
    return raw.rstrip(":").strip()


def _http_json(url: str) -> tuple[dict | None, int | None, str]:
    """Returns ``(body, http_status, error_description)``."""
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r), r.status, ""
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
            return body, e.code, body.get("description", "")
        except Exception:
            return None, e.code, str(e)
    except urllib.error.URLError as e:
        return None, None, f"network: {e.reason}"
    except (OSError, json.JSONDecodeError) as e:
        return None, None, str(e)


def _explain_http_error(code: int | None, err: str) -> str:
    if code == 401:
        return ("HTTP 401 — Telegram rejected the token. Generate a fresh one "
                "from @BotFather.")
    if code == 404:
        return ("HTTP 404 — the bot token URL is malformed. Double-check the "
                "token you pasted.")
    if code == 429:
        return ("HTTP 429 — Telegram is rate-limiting us. Wait a minute "
                "and retry.")
    if code and code >= 500:
        return f"HTTP {code} — Telegram API error. Probably transient; retry."
    if err.startswith("network:"):
        return f"can't reach api.telegram.org ({err[len('network:'):].strip()})"
    return err or "unknown error"


def _verify_token(token: str) -> dict | None:
    body, code, err = _http_json(
        f"https://api.telegram.org/bot{token}/getMe"
    )
    if body and body.get("ok"):
        return body["result"]
    err_console.print(f"  [err]{_explain_http_error(code, err)}[/err]")
    return None


def _test_send(token: str, chat_id: int) -> tuple[bool, str]:
    """Probe sendMessage — returns (True, "") or (False, error_desc)."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": str(chat_id),
        "text": "✓ aipager linked to this chat.",
    }).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
            return False, body.get("description", str(e))
        except Exception:
            return False, str(e)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return False, str(e)
    if not result.get("ok"):
        return False, result.get("description", "unknown error")
    return True, ""


def _fetch_id_from_updates(
    token: str, *, want: str,
) -> tuple[int | None, str | None, str | None]:
    """Poll ``getUpdates`` for the most recent matching id.

    ``want`` selects what we're looking for:
      - ``"dm"``    — most recent private (DM) chat id.
      - ``"group"`` — most recent group / supergroup chat id.
      - ``"user"``  — most recent ``from.user.id`` (any chat); useful
                       for capturing a new team member's Telegram id.

    Returns ``(id, friendly_name, advisory)`` where ``advisory`` is a
    user-facing hint when the wrong kind of update was seen (so the
    wizard can nudge them in the right direction).
    """
    body, _code, _err = _http_json(
        f"https://api.telegram.org/bot{token}/getUpdates"
    )
    if not body or not body.get("ok"):
        return None, None, None

    saw_other: list[str] = []
    for u in body.get("result", []):
        msg = u.get("message") or u.get("edited_message") or {}
        chat = msg.get("chat") or {}
        sender = msg.get("from") or {}
        cid = chat.get("id")
        ctype = chat.get("type")
        if cid is None:
            continue

        if want == "dm":
            if ctype == "private":
                who = chat.get("username") or chat.get("first_name", "")
                return int(cid), who, None
            saw_other.append(ctype or "?")
        elif want == "group":
            if ctype in ("group", "supergroup"):
                who = chat.get("title", "")
                return int(cid), who, None
            saw_other.append(ctype or "?")
        elif want == "user":
            uid = sender.get("id")
            if uid is not None:
                who = sender.get("username") or sender.get("first_name", "")
                return int(uid), who, None

    if saw_other:
        if want == "dm":
            advisory = (
                f"Saw activity in non-private chat(s): "
                f"{', '.join(sorted(set(saw_other)))}. "
                "Please DM the bot directly (1-on-1), not in a group."
            )
        elif want == "group":
            advisory = (
                f"Saw activity in {', '.join(sorted(set(saw_other)))}, "
                "but no group. Add the bot to the group and send /start "
                "there."
            )
        else:
            advisory = None
        return None, None, advisory
    return None, None, None


def _fetch_chat_id(token: str) -> tuple[int | None, str | None, str | None]:
    """Backwards-compatible wrapper — DM (private chat) lookup."""
    return _fetch_id_from_updates(token, want="dm")


def _resolve_user(
    token: str, query: str,
) -> tuple[int, str] | None:
    """Resolve a numeric id OR ``@handle`` to ``(user_id, suggested_label)``.

    Uses Telegram's ``getChat`` endpoint. Returns ``None`` on:

    - non-private chat type (channel / supergroup / bot) — we
      refuse to allow-list these
    - chat-not-found / network error
    - malformed input

    Numeric input that the bot has never seen will return ``None``
    too (Telegram refuses ``getChat`` for unknown numeric chats).
    Callers fall back to treating the id as-is with a default
    label of ``user<id>``.
    """
    if not token:
        return None
    raw = query.strip()
    if not raw:
        return None

    # Forgiving input: bare handle (no @) gets one prefixed.
    if not raw.startswith("@"):
        try:
            int(raw)
            chat_id = raw  # numeric — use as-is
        except ValueError:
            chat_id = f"@{raw}"
    else:
        chat_id = raw

    body, _code, _err = _http_json(
        f"https://api.telegram.org/bot{token}/getChat?chat_id={chat_id}"
    )
    if not body or not body.get("ok"):
        return None

    result = body.get("result") or {}
    if result.get("type") != "private":
        # channel / group / bot — not eligible for the team allow-list
        return None

    try:
        uid = int(result["id"])
    except (KeyError, TypeError, ValueError):
        return None

    suggested = (
        result.get("username")
        or result.get("first_name")
        or f"user{uid}"
    ).strip() or f"user{uid}"
    # Labels are case-sensitive in team.yaml but Telegram handles are
    # canonical lowercase. Keep the suggestion lowercase to match
    # `@alice` mention conventions.
    return uid, suggested.lower()


def _spin(message: str):
    """Context manager: spinner if a TTY, plain print otherwise."""
    if console.is_terminal:
        return console.status(f"[muted]{message}[/muted]", spinner="dots")
    # Off-TTY: print a single line and return a no-op context manager.
    console.print(f"  {message}")
    return _NullCtx()


class _NullCtx:
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False


# ----- steps -----

def _step_token(step_label: str = "[1/5]") -> tuple[str, str]:
    step(f"{step_label}  Telegram bot")
    hint("Get a token from @BotFather (https://t.me/BotFather)")
    while True:
        raw = _ask(questionary.text(
            "Paste your bot token:",
            qmark="?",
            style=_PROMPT_STYLE,
        ))
        token = _normalize_token(raw)
        if not token:
            err_console.print("  [err]empty — try again[/err]")
            continue
        with _spin("Verifying with Telegram…"):
            info = _verify_token(token)
        if info is None:
            err_console.print("  [hint]Try again or Ctrl-C to exit.[/hint]")
            continue
        username = info.get("username") or "your_bot"
        ok(f"Verified — @{username}")
        return token, username


def _step_chat_id(
    token: str, bot_username: str,
    *, mode: str = "personal", step_label: str = "[2/5]",
) -> int:
    """Capture the chat ID (DM for personal mode, group for team mode).

    ``mode`` decides the auto-detect target and the prompt copy:
      - ``"personal"`` — looks for a private chat; nudges the user
        to DM the bot.
      - ``"team"`` — looks for a group / supergroup; nudges the user
        to add the bot to a group and send /start there.

    ``step_label`` is the wizard's progress prefix (e.g. ``[3/7]``)
    so the caller can keep step numbers consistent regardless of mode.
    """
    if mode == "team":
        step(f"{step_label}  Group chat ID")
        auto_label = "Auto-detect (I'll add bot to the group + /start)"
        manual_label = "Paste group chat id (negative integer)"
        spinner_msg = "Checking for the group's /start…"
        detect_hint = (
            f"Add @{bot_username} to your group, then send /start there."
        )
    else:
        step(f"{step_label}  Your chat ID")
        auto_label = "Auto-detect (I'll DM the bot first)"
        manual_label = "Paste manually"
        spinner_msg = "Checking for your DM…"
        detect_hint = (
            f"DM the bot here, then continue: https://t.me/{bot_username}"
        )

    while True:
        method = _ask(questionary.select(
            "How should we find the chat id?",
            choices=[
                questionary.Choice(auto_label, value="auto"),
                questionary.Choice(manual_label, value="manual"),
            ],
            qmark="?",
            style=_PROMPT_STYLE,
        ))
        if method == "manual":
            raw = _ask(questionary.text(
                "Chat id (integer):", qmark="?", style=_PROMPT_STYLE,
            )).strip()
            try:
                cid = int(raw)
            except ValueError:
                err_console.print("  [err]not a number[/err]")
                continue
            if mode == "team" and cid >= 0:
                err_console.print(
                    "  [err]Group IDs are negative integers. Try again.[/err]",
                )
                continue
        else:
            hint(detect_hint)
            _ask(questionary.confirm(
                "Sent — continue?",
                default=True, qmark="?", style=_PROMPT_STYLE,
            ))
            with _spin(spinner_msg):
                found_id, who, advisory = _fetch_id_from_updates(
                    token, want=("group" if mode == "team" else "dm"),
                )
            if found_id is None:
                if advisory:
                    err_console.print(f"  [err]{advisory}[/err]")
                else:
                    target = "group /start" if mode == "team" else "DM"
                    err_console.print(
                        f"  [err]No {target} detected — try again.[/err]"
                    )
                continue
            cid = found_id
            who_label = f"@{who}" if who else "(no title/handle)"
            ok(f"Detected chat_id={cid} {who_label}")

        with _spin("Sending a test message…"):
            sent, err = _test_send(token, cid)

        if sent:
            ok(f"chat_id={cid} — test message delivered.")
            confirmed = _ask(questionary.confirm(
                "Did the test message arrive in your Telegram?",
                default=True, qmark="?", style=_PROMPT_STYLE,
            ))
            if confirmed:
                return cid
            hint("Let's try again — the message went somewhere unexpected.")
            continue

        if _CHAT_NOT_FOUND_RE.search(err):
            err_console.print(f"  [err]Telegram says: {err}[/err]")
            hint(f"You haven't started @{bot_username} yet — open it:")
            hint(f"  https://t.me/{bot_username}")
            _ask(questionary.confirm(
                "I've tapped Start in Telegram — retry?",
                default=True, qmark="?", style=_PROMPT_STYLE,
            ))
            with _spin("Retrying test send…"):
                sent2, err2 = _test_send(token, cid)
            if sent2:
                ok(f"chat_id={cid} — test message delivered.")
                return cid
            err_console.print(f"  [err]Still failing: {err2}[/err]")
            continue

        err_console.print(f"  [err]Test send failed: {err}[/err]")


def _step_deps(step_label: str = "[3/5]") -> bool:
    """Returns True if all required deps are present."""
    from rich.table import Table

    step(f"{step_label}  System dependencies")

    dtach_p: str | None = None
    try:
        from dtach_bin import path as _dtach_path
        dtach_p = _dtach_path()
    except (ImportError, FileNotFoundError):
        dtach_p = shutil.which("dtach")

    claude_p = shutil.which("claude")
    hook_p = shutil.which(HOOK_CMD)
    statusline_p = shutil.which(STATUSLINE_CMD)

    rows = [
        ("dtach", dtach_p,
         "uv tool install --reinstall aipager  # or `brew install dtach`"),
        ("claude", claude_p,
         "Install Claude Code: https://docs.anthropic.com/claude/docs/claude-code"),
        ("aipager-hook", hook_p,
         "uv tool install --reinstall aipager"),
        ("aipager-statusline", statusline_p,
         "uv tool install --reinstall aipager"),
    ]

    if console.is_terminal:
        t = Table(show_header=False, box=None, pad_edge=False, padding=(0, 2))
        t.add_column(width=3, justify="center")
        t.add_column(no_wrap=True)
        t.add_column(style="hint")
        for name, path, fix in rows:
            mark = ("[ok]✓[/ok]" if path else "[err]✗[/err]")
            detail = path if path else fix
            t.add_row(mark, name, detail)
        console.print(t)
    else:
        for name, path, fix in rows:
            mark = "✓" if path else "✗"
            console.print(f"  {mark} {name}  {path or fix}")

    # Required for the daemon: dtach + claude + both hook scripts.
    return bool(dtach_p and claude_p and hook_p and statusline_p)


def _resolve(cmd: str) -> str:
    """Resolve a console-script to an absolute path; caller pre-checks."""
    return shutil.which(cmd) or cmd


def _has_hook_cmd(entries: list, bare_name: str) -> bool:
    for block in entries:
        for hook in block.get("hooks", []):
            cmd = hook.get("command", "")
            if cmd == bare_name or cmd.endswith(f"/{bare_name}"):
                return True
    return False


def _validate_settings_schema(settings: dict) -> None:
    hooks = settings.get("hooks")
    if hooks is None:
        return
    if not isinstance(hooks, dict):
        raise ValueError(
            f"settings.json has `hooks` as {type(hooks).__name__}, "
            "but Claude Code expects a dict mapping event names to hook lists."
        )
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            raise ValueError(
                f"settings.json has `hooks.{event}` as "
                f"{type(entries).__name__}, expected a list."
            )


def _merge_hooks(settings: dict) -> None:
    hook_path = _resolve(HOOK_CMD)
    statusline_path = _resolve(STATUSLINE_CMD)
    hooks = settings.setdefault("hooks", {})
    entry = {"type": "command", "command": hook_path}
    for event in HOOK_EVENTS:
        entries = hooks.setdefault(event, [])
        if _has_hook_cmd(entries, HOOK_CMD):
            continue
        if event in TOOL_MATCHER_EVENTS:
            entries.append({"matcher": "*", "hooks": [entry]})
        else:
            entries.append({"hooks": [entry]})
    settings["statusLine"] = {"type": "command", "command": statusline_path}


def _step_settings(step_label: str = "[4/5]") -> None:
    step(f"{step_label}  Claude Code integration")
    settings: dict = {}
    existing_text = ""
    if CLAUDE_SETTINGS.exists():
        try:
            existing_text = CLAUDE_SETTINGS.read_text()
        except OSError as e:
            raise OSError(f"cannot read {CLAUDE_SETTINGS}: {e}") from e
        try:
            settings = json.loads(existing_text)
        except json.JSONDecodeError as e:
            extra = ""
            if re.search(r"^\s*//|/\*", existing_text):
                extra = ("\n     Looks like the file has // or /* */ comments. "
                         "Claude Code uses strict JSON — strip them.")
            raise ValueError(
                f"{CLAUDE_SETTINGS} is not valid JSON ({e}).{extra}"
            ) from e
        try:
            _validate_settings_schema(settings)
        except ValueError as e:
            raise ValueError(f"{CLAUDE_SETTINGS} schema problem: {e}") from e
        new_settings = json.loads(existing_text)
        _merge_hooks(new_settings)
        new_text = json.dumps(new_settings, indent=2) + "\n"
        if new_text == existing_text:
            ok(f"{CLAUDE_SETTINGS} already up to date")
            return
        backup = CLAUDE_SETTINGS.with_name(
            f"{CLAUDE_SETTINGS.name}.bak.{int(time.time())}"
        )
        backup.write_text(existing_text)
        console.print(f"  [muted]• backed up existing settings → {backup.name}[/muted]")
    else:
        CLAUDE_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    _merge_hooks(settings)
    try:
        CLAUDE_SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")
    except OSError as e:
        raise OSError(f"cannot write {CLAUDE_SETTINGS}: {e}") from e
    ok(f"Patched {CLAUDE_SETTINGS} ({len(HOOK_EVENTS)} hooks + statusLine)")


def _step_write_env(token: str, chat_id: int, step_label: str = "[5/5]") -> None:
    step(f"{step_label}  Write config")
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise OSError(f"cannot create {CONFIG_DIR}: {e}") from e

    if CONFIG_ENV.exists():
        try:
            existing = CONFIG_ENV.read_text()
        except OSError:
            existing = ""
        if f"CLAUDE_TG_BOT_TOKEN={token}" not in existing or \
           f"CLAUDE_TG_CHAT_ID={chat_id}" not in existing:
            answer = _ask(questionary.confirm(
                f"{CONFIG_ENV} already has different settings. Overwrite?",
                default=False, qmark="?", style=_PROMPT_STYLE,
            ))
            if not answer:
                friendly_warn("Keeping existing config; new token not written.")
                return

    try:
        CONFIG_ENV.write_text(
            f"CLAUDE_TG_BOT_TOKEN={token}\nCLAUDE_TG_CHAT_ID={chat_id}\n"
        )
    except OSError as e:
        raise OSError(f"cannot write {CONFIG_ENV}: {e}") from e
    try:
        os.chmod(CONFIG_ENV, 0o600)
    except OSError:
        friendly_warn(
            f"Could not chmod 0600 on {CONFIG_ENV} — non-POSIX filesystem?",
            "  Your token file is readable by other users on this machine.",
        )
    ok(f"Wrote {CONFIG_ENV}")


def _completion_screen() -> None:
    """Show the post-setup summary in a panel (or plain text off-TTY)."""
    from rich.panel import Panel

    lines = [
        f"[ok]{GLYPH_OK}[/ok]  Setup complete.",
        "",
        "  Start the daemon:    [path]aipager start[/path]",
        "  Launch a session:    [path]aipager session dev[/path]",
        "  Health check:        [path]aipager doctor[/path]",
    ]
    body = "\n".join(lines)
    if console.is_terminal:
        console.print()
        console.print(Panel(body, border_style="ok", expand=False,
                            padding=(0, 1)))
    else:
        console.print()
        console.print(f"{GLYPH_OK} Setup complete.")
        console.print()
        console.print("  Start the daemon:    aipager start")
        console.print("  Launch a session:    aipager session dev")
        console.print("  Health check:        aipager doctor")


def _step_pick_mode(step_label: str = "[2/N]") -> str:
    """Ask Personal vs Team. Returns ``"personal"`` or ``"team"``.

    Picking Team triggers the trust-warning panel and a hard-stop
    confirm; declining the confirm collapses back to Personal.
    """
    step(f"{step_label}  Personal or Team")

    pick = _ask(questionary.select(
        "How will you use aipager?",
        choices=[
            questionary.Choice("Personal (1:1 DM with the bot — recommended)",
                               value="personal"),
            questionary.Choice("Team (group chat with multiple devs)",
                               value="team"),
        ],
        # `default` must match a Choice ``value``, not its title.
        default="personal",
        qmark="?", style=_PROMPT_STYLE,
    ))

    if pick == "personal":
        ok("Personal mode — no team.yaml needed.")
        return "personal"

    _show_team_warning_panel()
    proceed = _ask(questionary.confirm(
        "Continue with team-mode setup?",
        default=False, qmark="?", style=_PROMPT_STYLE,
    ))
    if not proceed:
        friendly_warn("Team setup cancelled. Falling back to personal mode.")
        return "personal"
    return "team"


def _show_team_warning_panel() -> None:
    """The "you're handing out shell access" panel. Shown before
    every Team mode commit (first-run AND Switch-to-Team edit)."""
    from rich.panel import Panel
    warning_body = (
        "[warn]⚠️  Team mode grants every allow-listed user the ability to:[/warn]\n\n"
        "   • Inject prompts into Claude Code sessions on this machine\n"
        "   • Approve / deny tool calls (run shell commands, edit files)\n"
        "   • Create new sessions, kill sessions, switch active session\n\n"
        "   This is a code-execution boundary. Only allow-list users you\n"
        "   trust the same way you trust local shell access on this\n"
        "   machine. You can clamp dangerous tools (Write, Edit, Bash)\n"
        "   via [path]~/.config/aipager/team.yaml[/path] [path]rules.deny_tools[/path]."
    )
    if console.is_terminal:
        console.print()
        console.print(Panel(warning_body, border_style="warn", expand=False,
                            padding=(0, 1)))
    else:
        console.print()
        console.print(warning_body)
    console.print()


def _collect_users(
    existing_ids: set[int] | None = None,
    existing_labels: set[str] | None = None,
    *,
    token: str = "",
) -> list[dict]:
    """Interactive loop: prompt for label / Telegram id / role, repeat
    until the user says stop. Returns a list of ``{id, label, role}``
    dicts ready to feed into a :class:`team.Team`.

    ``existing_ids`` / ``existing_labels`` are for the edit flow when
    we're appending to an existing team — duplicate-detection then
    spans both the new entries and the prior ones.

    When ``token`` is provided, each user-id prompt offers an
    auto-detect mode: the wizard polls ``getUpdates`` for a recent
    message and captures the sender's id + Telegram username, then
    pre-fills the label from the username. Saves admins from
    making members hand-look-up their numeric ids.
    """
    existing_ids = set(existing_ids or ())
    existing_labels = set(existing_labels or ())
    users: list[dict] = []

    console.print()
    console.print("Add allowed users (Telegram user IDs).")
    console.print(
        "[muted]  Each user gets a label (shown in chat as @label) and a role:[/muted]"
    )
    console.print(
        "[muted]    admin       full control; bypasses deny_tools rules[/muted]"
    )
    console.print(
        "[muted]    developer   prompt + approve, subject to deny_tools[/muted]"
    )
    console.print(
        "[muted]    read_only   /status only; messages otherwise ignored[/muted]"
    )

    while True:
        idx = len(users) + 1
        captured = _capture_user_identity(
            idx,
            existing_ids=existing_ids | {u["id"] for u in users},
            existing_labels=existing_labels | {u["label"] for u in users},
            token=token,
        )
        if captured is None:
            # Cancelled this user — break out of the add-more loop.
            break

        role = _ask(questionary.select(
            f"User #{idx} role:",
            choices=["admin", "developer", "read_only"],
            qmark="?", style=_PROMPT_STYLE,
        ))
        users.append({**captured, "role": role})

        more = _ask(questionary.confirm(
            "Add another user?",
            default=False, qmark="?", style=_PROMPT_STYLE,
        ))
        if not more:
            break

    return users


def _capture_user_identity(
    idx: int,
    *,
    existing_ids: set[int],
    existing_labels: set[str],
    token: str = "",
) -> dict | None:
    """Capture one ``{"id": int, "label": str}`` pair.

    Offers auto-detect when ``token`` is set (the wizard polls
    Telegram for a recent message from a new user). Otherwise just
    asks for label + numeric id by paste.

    Returns ``None`` if the admin cancels mid-flow.
    """
    if token:
        method = _ask(questionary.select(
            f"User #{idx} — how should we capture their identity?",
            choices=[
                questionary.Choice(
                    "Auto-detect (I'll watch for them to mention the bot)",
                    value="auto",
                ),
                questionary.Choice("Paste user id manually", value="manual"),
                questionary.Choice("Cancel", value="cancel"),
            ],
            qmark="?", style=_PROMPT_STYLE,
        ))
    else:
        method = "manual"

    if method == "cancel":
        return None

    if method == "auto":
        hint(
            "Ask the new user to mention the bot in the group "
            "(or send a DM) in the next moment."
        )
        while True:
            _ask(questionary.confirm(
                "They've sent something — continue?",
                default=True, qmark="?", style=_PROMPT_STYLE,
            ))
            with _spin("Watching for a new user…"):
                uid, who, _adv = _fetch_id_from_updates(token, want="user")
            if uid is None:
                err_console.print(
                    "  [err]No recent message detected — try again or "
                    "switch to manual.[/err]"
                )
                retry = _ask(questionary.select(
                    "What now?",
                    choices=[
                        questionary.Choice("Retry auto-detect", value="retry"),
                        questionary.Choice("Switch to manual", value="manual"),
                        questionary.Choice("Cancel this user", value="cancel"),
                    ],
                    qmark="?", style=_PROMPT_STYLE,
                ))
                if retry == "retry":
                    continue
                if retry == "manual":
                    method = "manual"
                    break
                return None
            if uid in existing_ids:
                err_console.print(
                    f"  [err]User {uid} ({who or 'no handle'}) is already "
                    "on the allow-list. Ask someone else to mention.[/err]"
                )
                continue
            ok(f"Captured user_id={uid} (@{who or 'no handle'})")
            suggested_label = (who or f"user{uid}").lower()
            finalized = _finalize_user(uid, suggested_label, existing_labels)
            if finalized is None:
                continue
            return finalized

    # Manual path: single combined prompt — accept integer OR @handle.
    while True:
        raw = _ask(questionary.text(
            f"User #{idx} id (integer) or @handle (e.g. 12345 or @alice):",
            qmark="?", style=_PROMPT_STYLE,
        )).strip()
        if not raw:
            friendly_warn("Empty — paste an id or @handle.")
            continue

        uid: int | None = None
        suggested_label = ""

        # Try resolving as @handle first (or bare handle).
        looks_like_handle = raw.startswith("@") or (
            not raw.lstrip("-").isdigit()
        )
        if looks_like_handle and token:
            with _spin(f"Resolving {raw}…"):
                resolved = _resolve_user(token, raw)
            if resolved is None:
                friendly_warn(
                    f"Couldn't resolve {raw!r} via Telegram — make sure "
                    "the handle is correct and the user has a public "
                    "username. Try again or paste the numeric id.",
                )
                continue
            uid, suggested_label = resolved
            ok(f"Resolved {raw} → id={uid} (@{suggested_label})")
        else:
            # Numeric input.
            try:
                uid = int(raw)
            except ValueError:
                friendly_warn(
                    "Not a valid integer or @handle. Try again.",
                )
                continue
            # Best-effort enrichment — getChat by id only works if the
            # bot has seen this chat before; otherwise we just keep
            # the numeric id and fall back to a generated label.
            if token:
                with _spin(f"Looking up id {uid}…"):
                    resolved = _resolve_user(token, str(uid))
                if resolved is not None:
                    _, suggested_label = resolved
            if not suggested_label:
                suggested_label = f"user{uid}"

        if uid in existing_ids:
            friendly_warn(f"User id {uid} is already on the allow-list.")
            continue

        finalized = _finalize_user(uid, suggested_label, existing_labels)
        if finalized is None:
            continue
        return finalized


def _finalize_user(
    uid: int, suggested_label: str, existing_labels: set[str],
) -> dict | None:
    """Ask for the (optional) label given a resolved user id + suggestion.

    Empty admin input → use ``suggested_label``. Duplicate label
    rejected with a re-prompt. Used by both the auto-detect and
    manual paths so they share one source of truth for the label
    dialog.
    """
    while True:
        raw = _ask(questionary.text(
            "Label (shown in chat as @label):",
            default=suggested_label,
            qmark="?", style=_PROMPT_STYLE,
        )).strip()
        label = raw or suggested_label
        if not label:
            friendly_warn("Label must be non-empty.")
            continue
        if label in existing_labels:
            friendly_warn(
                f"Label {label!r} is already in use — try a different one.",
            )
            continue
        return {"id": uid, "label": label}


def _collect_deny_tools() -> list[str]:
    """Ask whether to enable the default deny rule. Returns the list
    of tool names to put in ``rules.deny_tools`` (possibly empty)."""
    console.print()
    console.print("[title]Optional safety rule[/title]")
    console.print(
        "[muted]  Auto-deny Write and Edit tools so file changes always "
        "need an admin override.[/muted]"
    )
    enable_default_deny = _ask(questionary.confirm(
        "Enable default deny_tools = [Write, Edit]?",
        default=True, qmark="?", style=_PROMPT_STYLE,
    ))
    return ["Write", "Edit"] if enable_default_deny else []


def _step_team_setup(
    group_id: int, step_label: str = "[4/N]", *, token: str = "",
) -> None:
    """Collect users + rules, write team.yaml.

    Assumes mode was already picked (via :func:`_step_pick_mode`) and
    the group ID is in hand (via :func:`_step_chat_id` with
    ``mode="team"``). Writes ``~/.config/aipager/team.yaml`` via
    :func:`team.dump_team`.

    When ``token`` is set, the user-id prompts offer Telegram-side
    auto-detect via :func:`_fetch_id_from_updates`.
    """
    from aipager.team import Role, Team, User as TeamUser, dump_team

    step(f"{step_label}  Team allow-list")

    users_data = _collect_users(token=token)
    if not any(u["role"] == "admin" for u in users_data):
        friendly_warn(
            "No admin user added. You'll need to promote one (re-run "
            "`aipager config` → Change a user's role) before "
            "deny_tools rules can be bypassed.",
        )

    deny_tools = _collect_deny_tools()

    users = {
        u["id"]: TeamUser(id=u["id"], label=u["label"], role=Role(u["role"]))
        for u in users_data
    }
    from aipager.team import Rules as TeamRules
    new_team = Team(
        group_id=group_id,
        users=users,
        rules=TeamRules(deny_tools=tuple(deny_tools)),
    )

    try:
        dump_team(new_team, TEAM_YAML)
    except OSError as e:
        raise OSError(f"cannot write {TEAM_YAML}: {e}") from e
    ok(f"Wrote {TEAM_YAML}")


def _step_team_config() -> bool:
    """Backwards-compatible wrapper. Returns True iff team mode was
    written. Used by the legacy linear flow when team mode was tacked
    onto the end; new flows call :func:`_step_pick_mode` + (later)
    :func:`_step_team_setup` directly.
    """
    if _step_pick_mode("[6/6]") == "personal":
        return False
    # The group_id was never captured in the legacy flow — fall back
    # to a manual prompt for backwards compatibility.
    while True:
        raw = _ask(questionary.text(
            "Group chat ID (negative integer, e.g. -100123456789):",
            qmark="?", style=_PROMPT_STYLE,
        ))
        try:
            group_id = int(raw.strip())
            if group_id >= 0:
                friendly_warn("Group IDs are negative. Try again.")
                continue
            break
        except ValueError:
            friendly_warn("Not a valid integer. Try again.")
    _step_team_setup(group_id, step_label="[6/6]")
    return True


def _first_run_flow() -> int:
    """Walk a brand-new install through token → mode → chat → (team
    users + rules if team) → deps → settings → write.

    Mode is picked BEFORE chat-id so the chat-id prompt asks for a
    group id (team) or a DM id (personal) accordingly — no double
    prompting like the legacy [6/6]-tacked-on flow had.
    """
    if console.is_terminal:
        rule("aipager setup")
    else:
        console.print("Welcome to aipager setup.")

    try:
        # Mode determines total step count and the chat-id step's
        # prompt copy. Token always comes first because we need it to
        # auto-detect chat ids.
        token, bot_username = _step_token(step_label="[1/?]")
        wizard_mode = _step_pick_mode(step_label="[2/?]")

        total = 7 if wizard_mode == "team" else 6
        # Re-label step 1 retroactively isn't possible in a streaming
        # CLI; we just use the right total from step 3 onwards.
        chat_id = _step_chat_id(
            token, bot_username,
            mode=wizard_mode, step_label=f"[3/{total}]",
        )

        if wizard_mode == "team":
            _step_team_setup(chat_id, step_label=f"[4/{total}]", token=token)
            deps_step = f"[5/{total}]"
            settings_step = f"[6/{total}]"
            write_step = f"[7/{total}]"
        else:
            deps_step = f"[4/{total}]"
            settings_step = f"[5/{total}]"
            write_step = f"[6/{total}]"

        deps_ok = _step_deps(step_label=deps_step)
        if not deps_ok:
            cont = _ask(questionary.confirm(
                "Continue anyway? (the daemon will likely fail until you "
                "install the missing dependencies)",
                default=False, qmark="?", style=_PROMPT_STYLE,
            ))
            if not cont:
                friendly_warn(
                    "Setup aborted — install the missing dependencies and "
                    "re-run `aipager config`.",
                )
                return 2
        _step_settings(step_label=settings_step)
        _step_write_env(token, chat_id, step_label=write_step)
    except KeyboardInterrupt:
        friendly_warn("Cancelled.")
        return 130
    except ValueError as e:
        friendly_error(str(e))
        return 1
    except OSError as e:
        friendly_error(f"Setup failed: {e}")
        return 1
    _completion_screen()
    return 0


def _read_env_file() -> tuple[str, str]:
    """Return ``(token, chat_id)`` from CONFIG_ENV, or ``("", "")``
    if the file is missing or malformed."""
    if not CONFIG_ENV.exists():
        return "", ""
    token = ""
    chat_id = ""
    try:
        for line in CONFIG_ENV.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip("\"'")
            if k == "CLAUDE_TG_BOT_TOKEN":
                token = v
            elif k == "CLAUDE_TG_CHAT_ID":
                chat_id = v
    except OSError:
        return "", ""
    return token, chat_id


def _write_env_file(token: str, chat_id: int | str) -> None:
    """Overwrite CONFIG_ENV (mode 0600)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_ENV.write_text(
        f"CLAUDE_TG_BOT_TOKEN={token}\nCLAUDE_TG_CHAT_ID={chat_id}\n"
    )
    try:
        os.chmod(CONFIG_ENV, 0o600)
    except OSError:
        pass


def _detect_daemon_running() -> int | None:
    """Probe ``/tmp/aipager.sock`` and return the daemon's PID if
    we can find one, ``None`` otherwise. Used for the post-edit hint
    ("daemon needs a restart")."""
    import socket as _socket
    p = "/tmp/aipager.sock"
    try:
        s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.sendto(b'{"event":"_wizard_probe"}', p)
        s.close()
    except (FileNotFoundError, ConnectionRefusedError, OSError):
        return None
    # Best-effort PID lookup via pgrep
    import shutil as _shutil
    import subprocess as _subprocess
    if _shutil.which("pgrep"):
        try:
            r = _subprocess.run(
                ["pgrep", "-f", "aipager start"],
                capture_output=True, text=True, timeout=2,
            )
            if r.returncode == 0:
                first = r.stdout.strip().split("\n", 1)[0]
                if first.isdigit():
                    return int(first)
        except (OSError, _subprocess.TimeoutExpired):
            pass
    return -1  # daemon up, PID unknown


def _restart_hint() -> None:
    """Print a one-line reminder that the daemon must be restarted
    for config changes to take effect."""
    pid = _detect_daemon_running()
    if pid is None:
        # Daemon not running — nothing to restart.
        return
    console.print()
    console.print(
        "[warn]⚠[/warn]  [warn]Restart the daemon to apply this change:[/warn]"
    )
    console.print(
        "    [path]aipager service restart[/path]"
        "  [muted](or kill the foreground daemon and re-run `aipager start`)[/muted]"
    )


def _signal_reload() -> bool:
    """Send SIGUSR1 to the running daemon to live-reload team.yaml.

    Returns ``True`` iff a signal was delivered. ``False`` when the
    daemon isn't running, the PID is unknown, or the platform
    doesn't support signals (Windows). Caller handles fallback.
    """
    import signal as _signal

    pid = _detect_daemon_running()
    if pid is None or pid < 0:
        return False
    try:
        os.kill(pid, _signal.SIGUSR1)
        return True
    except (OSError, AttributeError):
        return False


def _apply_team_change_hint() -> None:
    """Post-edit feedback for changes that ONLY touched team.yaml.

    Prefers a live SIGUSR1 reload when the daemon is reachable; falls
    back to the legacy restart hint otherwise. Use the bare
    :func:`_restart_hint` for edits that also touched ``config.env``
    or the bot token (those still require a full restart).
    """
    if _signal_reload():
        console.print()
        console.print(
            "[ok]✓[/ok]  Team config reloaded live "
            "[muted](no daemon restart needed)[/muted]"
        )
        return
    _restart_hint()


def _show_current_config() -> None:
    """Print a panel summarizing config.env + team.yaml + daemon state."""
    from rich.panel import Panel
    from aipager.team import TEAM_CONFIG_PATH, TeamConfigError, load_team

    token, chat_id = _read_env_file()
    try:
        team = load_team(TEAM_CONFIG_PATH)
        team_err: str | None = None
    except TeamConfigError as e:
        team = None
        team_err = str(e)

    lines: list[str] = []
    if team is not None:
        lines.append("[title]Mode:[/title]   Team")
        lines.append(f"[title]Chat:[/title]   {chat_id}  ([path]group[/path])")
        lines.append(
            f"[title]Users:[/title]  {len(team.users)} "
            f"({team.admin_count()} admin)"
        )
        for u in team.users.values():
            lines.append(f"          • [path]{u.label}[/path] — {u.role.value}")
        deny = list(team.rules.deny_tools)
        rules_repr = f"deny_tools = {deny}" if deny else "(none)"
        lines.append(f"[title]Rules:[/title]  {rules_repr}")
    elif team_err:
        lines.append("[title]Mode:[/title]   [err]team.yaml malformed[/err]")
        lines.append(f"          [err]{team_err}[/err]")
        lines.append(f"[title]Chat:[/title]   {chat_id}")
    else:
        lines.append("[title]Mode:[/title]   Personal")
        lines.append(f"[title]Chat:[/title]   {chat_id}")

    if token:
        lines.append(f"[title]Token:[/title]  {token[:10]}…")
    else:
        lines.append("[title]Token:[/title]  [err]missing[/err]")

    daemon_pid = _detect_daemon_running()
    if daemon_pid is None:
        lines.append("[title]Daemon:[/title] [muted]not running[/muted]")
    elif daemon_pid > 0:
        lines.append(f"[title]Daemon:[/title] up (PID {daemon_pid})")
    else:
        lines.append("[title]Daemon:[/title] up")

    body = "\n".join(lines)
    if console.is_terminal:
        console.print()
        console.print(Panel(body, title="Current config", border_style="step",
                            expand=False, padding=(0, 1)))
    else:
        console.print("\nCurrent config:")
        console.print(body)


def _edit_add_user(team) -> "object | None":
    """Returns the new Team after add, or None if the user cancelled."""
    from aipager.team import Role, User as TeamUser, dump_team

    existing_ids = set(team.users.keys())
    existing_labels = {u.label for u in team.users.values()}
    # Read token so the add flow can offer Telegram auto-detect.
    token, _ = _read_env_file()
    new_entries = _collect_users(
        existing_ids=existing_ids, existing_labels=existing_labels,
        token=token,
    )
    if not new_entries:
        return None
    new_team = team
    for u in new_entries:
        new_team = new_team.with_user(
            TeamUser(id=u["id"], label=u["label"], role=Role(u["role"])),
        )
    confirm = _ask(questionary.confirm(
        f"Add {len(new_entries)} user(s) to team.yaml?",
        default=True, qmark="?", style=_PROMPT_STYLE,
    ))
    if not confirm:
        friendly_warn("Cancelled.")
        return None
    dump_team(new_team)
    ok(f"Added {len(new_entries)} user(s).")
    return new_team


def _edit_remove_user(team) -> "object | None":
    from aipager.team import dump_team
    from aipager.team import Role

    if not team.users:
        friendly_warn("No users to remove.")
        return None
    choices = [
        questionary.Choice(f"{u.label} — {u.role.value}", value=u.id)
        for u in team.users.values()
    ]
    choices.append(questionary.Choice("Cancel", value=None))
    pick = _ask(questionary.select(
        "Remove which user?",
        choices=choices, qmark="?", style=_PROMPT_STYLE,
    ))
    if pick is None:
        return None
    target = team.users[pick]
    # Refuse to leave the team without an admin.
    if (target.role == Role.ADMIN and team.admin_count() == 1):
        friendly_warn(
            f"{target.label} is the only admin — promote another member "
            "first, then come back to remove them.",
        )
        return None
    confirm = _ask(questionary.confirm(
        f"Remove {target.label} ({target.role.value})?",
        default=False, qmark="?", style=_PROMPT_STYLE,
    ))
    if not confirm:
        return None
    new_team = team.without_user(pick)
    dump_team(new_team)
    ok(f"Removed {target.label}.")
    return new_team


def _edit_change_role(team) -> "object | None":
    from aipager.team import Role, dump_team

    if not team.users:
        friendly_warn("No users to update.")
        return None
    choices = [
        questionary.Choice(f"{u.label} — currently {u.role.value}", value=u.id)
        for u in team.users.values()
    ]
    choices.append(questionary.Choice("Cancel", value=None))
    pick = _ask(questionary.select(
        "Change which user's role?",
        choices=choices, qmark="?", style=_PROMPT_STYLE,
    ))
    if pick is None:
        return None
    current = team.users[pick]
    new_role_str = _ask(questionary.select(
        f"New role for {current.label} (was {current.role.value}):",
        choices=["admin", "developer", "read_only"],
        qmark="?", style=_PROMPT_STYLE,
    ))
    new_role = Role(new_role_str)
    if new_role == current.role:
        friendly_warn("No change.")
        return None
    # Refuse to demote the only admin.
    if (current.role == Role.ADMIN and new_role != Role.ADMIN
            and team.admin_count() == 1):
        friendly_warn(
            f"{current.label} is the only admin — promote another member "
            "first.",
        )
        return None
    new_team = team.with_role(pick, new_role)
    dump_team(new_team)
    ok(f"{current.label}: {current.role.value} → {new_role.value}")
    return new_team


_COMMON_TOOLS = (
    "Bash", "Write", "Edit", "WebFetch", "Read", "Glob", "Grep", "Task",
)


def _edit_deny_tools(team) -> "object | None":
    from aipager.team import dump_team

    current = set(team.rules.deny_tools)
    choices = [
        questionary.Choice(
            t, value=t, checked=(t in current),
        ) for t in _COMMON_TOOLS
    ]
    picked = _ask(questionary.checkbox(
        "Toggle deny_tools (space to select, Enter to confirm):",
        choices=choices, qmark="?", style=_PROMPT_STYLE,
    ))
    extras_raw = _ask(questionary.text(
        "Other tools to deny (comma-separated, leave blank for none):",
        default=", ".join(sorted(current - set(_COMMON_TOOLS))),
        qmark="?", style=_PROMPT_STYLE,
    )).strip()
    extras = [t.strip() for t in extras_raw.split(",") if t.strip()]
    new_tools = sorted(set(picked) | set(extras))

    if tuple(new_tools) == team.rules.deny_tools:
        friendly_warn("No change.")
        return None
    new_team = team.with_deny_tools(new_tools)
    dump_team(new_team)
    ok(f"deny_tools = {new_tools or '(none)'}")
    return new_team


def _edit_refresh_token() -> bool:
    """Re-prompt for token, verify, rewrite config.env. Returns True
    iff the token was updated."""
    step("[~]  Refresh bot token")
    _, chat_id = _read_env_file()
    while True:
        raw = _ask(questionary.text(
            "Paste your bot token:",
            qmark="?", style=_PROMPT_STYLE,
        ))
        token = _normalize_token(raw)
        if not token:
            friendly_warn("Empty — try again or Ctrl-C to cancel.")
            continue
        info = _verify_token(token)
        if info is None:
            friendly_warn("Telegram rejected the token. Try again or Ctrl-C.")
            continue
        _write_env_file(token, chat_id)
        ok(f"Wrote new token for @{info.get('username', '?')}.")
        return True


def _edit_switch_to_personal(team) -> bool:
    """Archive team.yaml. Returns True iff the switch happened."""
    from aipager.team import TEAM_CONFIG_PATH, archive_team

    console.print()
    console.print(
        "[warn]⚠[/warn]  Switching to personal mode will archive "
        "[path]team.yaml[/path] as a timestamped backup.",
    )
    confirm = _ask(questionary.confirm(
        f"Archive {team.users and f'{len(team.users)} users + ' or ''}rules "
        "and run as a 1:1 DM bot?",
        default=False, qmark="?", style=_PROMPT_STYLE,
    ))
    if not confirm:
        return False
    backup = archive_team(TEAM_CONFIG_PATH)
    if backup is None:
        friendly_warn("No team.yaml found — nothing to archive.")
        return False
    ok(f"Archived team.yaml → {backup.name}")

    # Offer to update CHAT_ID — it's almost certainly still the group id.
    _token, current_chat_id = _read_env_file()
    update_chat = _ask(questionary.confirm(
        f"Current CHAT_ID is {current_chat_id} (the group id). "
        "Update it to a DM chat id now?",
        default=True, qmark="?", style=_PROMPT_STYLE,
    ))
    if update_chat:
        token = _token
        info = _verify_token(token) if token else None
        bot_username = (info or {}).get("username", "your_bot")
        new_chat = _step_chat_id(
            token, bot_username, mode="personal", step_label="[~]",
        )
        _write_env_file(token, new_chat)
        ok(f"Wrote CHAT_ID={new_chat} (DM).")
    return True


def _edit_switch_to_team() -> bool:
    """Run the team setup against the existing token. Returns True
    iff team.yaml was written."""
    token, _ = _read_env_file()
    if not token:
        friendly_warn("No token in config.env — run full setup first.")
        return False
    info = _verify_token(token)
    bot_username = (info or {}).get("username", "your_bot")

    _show_team_warning_panel()
    proceed = _ask(questionary.confirm(
        "Continue with team-mode setup?",
        default=False, qmark="?", style=_PROMPT_STYLE,
    ))
    if not proceed:
        return False

    group_id = _step_chat_id(token, bot_username, mode="team",
                             step_label="[~]")
    _step_team_setup(group_id, step_label="[~]", token=token)
    # CHAT_ID in config.env should now be the group id for the daemon
    # to filter correctly.
    _write_env_file(token, group_id)
    ok(f"Wrote CHAT_ID={group_id} (group).")
    return True


def _edit_flow() -> int:
    """Show the current-config panel and offer a menu of edits.

    Loops until the user picks Exit (or selects "Run full setup",
    which delegates back to :func:`_first_run_flow`).
    """
    from aipager.team import TEAM_CONFIG_PATH, TeamConfigError, load_team

    rule("aipager config")
    while True:
        _show_current_config()

        try:
            team = load_team(TEAM_CONFIG_PATH)
        except TeamConfigError:
            team = None  # malformed; only show "run full setup" action

        if team is not None:
            choices = [
                questionary.Choice("Add a user", value="add"),
                questionary.Choice("Remove a user", value="remove"),
                questionary.Choice("Change a user's role", value="role"),
                questionary.Choice("Edit deny_tools rules", value="rules"),
                questionary.Choice("Switch to Personal mode", value="to_personal"),
                questionary.Choice("Refresh bot token", value="refresh_token"),
                questionary.Choice("Run full setup (overwrites everything)",
                                   value="full"),
                questionary.Choice("Exit", value="exit"),
            ]
        else:
            choices = [
                questionary.Choice("Switch to Team mode", value="to_team"),
                questionary.Choice("Refresh bot token", value="refresh_token"),
                questionary.Choice("Run full setup (overwrites everything)",
                                   value="full"),
                questionary.Choice("Exit", value="exit"),
            ]

        try:
            choice = _ask(questionary.select(
                "What would you like to do?",
                choices=choices, qmark="?", style=_PROMPT_STYLE,
            ))
        except KeyboardInterrupt:
            return 130

        try:
            if choice == "exit":
                return 0
            # Track what kind of change happened so we can choose the
            # right post-edit hint:
            #   "team"    — only team.yaml touched → hot-reload via SIGUSR1
            #   "restart" — config.env or token changed → full restart
            #   None      — no change / cancelled
            change_kind: str | None = None
            if choice == "full":
                return _first_run_flow()
            if choice == "refresh_token":
                if _edit_refresh_token():
                    change_kind = "restart"
            elif choice == "to_personal" and team is not None:
                if _edit_switch_to_personal(team):
                    # Archives team.yaml AND may update config.env
                    # (chat-id swap). Conservatively assume restart.
                    change_kind = "restart"
            elif choice == "to_team":
                if _edit_switch_to_team():
                    # Writes team.yaml AND updates CHAT_ID in
                    # config.env — restart required.
                    change_kind = "restart"
            elif team is not None and choice == "add":
                if _edit_add_user(team) is not None:
                    change_kind = "team"
            elif team is not None and choice == "remove":
                if _edit_remove_user(team) is not None:
                    change_kind = "team"
            elif team is not None and choice == "role":
                if _edit_change_role(team) is not None:
                    change_kind = "team"
            elif team is not None and choice == "rules":
                if _edit_deny_tools(team) is not None:
                    change_kind = "team"
        except KeyboardInterrupt:
            friendly_warn("Cancelled this action.")
            continue
        except ValueError as e:
            friendly_error(str(e))
            continue
        except OSError as e:
            friendly_error(f"Write failed: {e}")
            continue

        if change_kind == "team":
            _apply_team_change_hint()
        elif change_kind == "restart":
            _restart_hint()


def run() -> int:
    """Entry point for ``aipager config``.

    - No config.env on disk → first-run wizard (full token + mode + chat
      + team-or-not + deps + settings + write).
    - config.env present → edit menu showing current state.
    """
    if not CONFIG_ENV.exists():
        return _first_run_flow()
    return _edit_flow()


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
