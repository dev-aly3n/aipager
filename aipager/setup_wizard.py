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


def _fetch_chat_id(token: str) -> tuple[int | None, str | None, str | None]:
    body, _code, _err = _http_json(
        f"https://api.telegram.org/bot{token}/getUpdates"
    )
    if not body or not body.get("ok"):
        return None, None, None
    saw_non_private: list[str] = []
    for u in body.get("result", []):
        msg = u.get("message") or u.get("edited_message") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        ctype = chat.get("type")
        if cid is None:
            continue
        if ctype == "private":
            who = chat.get("username") or chat.get("first_name", "")
            return int(cid), who, None
        saw_non_private.append(ctype or "?")
    if saw_non_private:
        return (None, None,
                f"Saw activity in non-private chat(s): {', '.join(sorted(set(saw_non_private)))}. "
                "Please DM the bot directly (1-on-1), not in a group.")
    return None, None, None


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

def _step_token() -> tuple[str, str]:
    step("[1/5]  Telegram bot")
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


def _step_chat_id(token: str, bot_username: str) -> int:
    step("[2/5]  Your chat ID")
    while True:
        mode = _ask(questionary.select(
            "How should we find your chat id?",
            choices=[
                questionary.Choice("Auto-detect (I'll DM the bot first)",
                                   value="auto"),
                questionary.Choice("Paste manually", value="manual"),
            ],
            qmark="?",
            style=_PROMPT_STYLE,
        ))
        if mode == "manual":
            raw = _ask(questionary.text(
                "Chat id (integer):", qmark="?", style=_PROMPT_STYLE,
            )).strip()
            try:
                cid = int(raw)
            except ValueError:
                err_console.print("  [err]not a number[/err]")
                continue
        else:
            hint(f"DM the bot here, then continue: https://t.me/{bot_username}")
            _ask(questionary.confirm(
                "I've sent the bot a message in Telegram — continue?",
                default=True, qmark="?", style=_PROMPT_STYLE,
            ))
            with _spin("Checking for your DM…"):
                found_id, who, advisory = _fetch_chat_id(token)
            if found_id is None:
                if advisory:
                    err_console.print(f"  [err]{advisory}[/err]")
                else:
                    err_console.print(
                        "  [err]No DM detected — send any message to the bot in "
                        "Telegram, then retry.[/err]"
                    )
                continue
            cid = found_id
            ok(f"Detected chat_id={cid} (@{who})")

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


def _step_deps() -> bool:
    """Returns True if all required deps are present."""
    from rich.table import Table

    step("[3/5]  System dependencies")

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


def _step_settings() -> None:
    step("[4/5]  Claude Code integration")
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


def _step_write_env(token: str, chat_id: int) -> None:
    step("[5/5]  Write config")
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


def _step_team_config() -> bool:
    """Ask whether the user wants team mode and, if so, write team.yaml.

    Returns True iff team mode was configured. Skipped silently
    (returns False) when the user picks Personal.

    Team mode in this wizard step:
    - shows a hard-stop warning panel naming the trust expansion
    - takes the group chat ID
    - takes one or more users (Telegram user ID + label + role)
    - optionally enables a default ``deny_tools: [Write, Edit]`` rule
    - writes ``~/.config/aipager/team.yaml`` with mode 0600
    """
    step("[6/6]  Personal or Team mode")

    mode = _ask(questionary.select(
        "How will you use aipager?",
        choices=[
            questionary.Choice("Personal (1:1 DM with the bot — recommended)",
                               value="personal"),
            questionary.Choice("Team (group chat with multiple devs)",
                               value="team"),
        ],
        default="Personal (1:1 DM with the bot — recommended)",
        qmark="?", style=_PROMPT_STYLE,
    ))

    if mode == "personal":
        ok("Personal mode — no team.yaml needed.")
        return False

    # ----- Team mode: hard-stop warning panel ----------------------------
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

    proceed = _ask(questionary.confirm(
        "Continue with team-mode setup?",
        default=False, qmark="?", style=_PROMPT_STYLE,
    ))
    if not proceed:
        friendly_warn("Team setup cancelled. Personal mode is active.")
        return False

    # ----- Group ID ------------------------------------------------------
    hint(
        "Find your group ID by adding the bot to the group, sending /start "
        "there, then hitting "
        "https://api.telegram.org/bot<TOKEN>/getUpdates (the chat.id field).",
    )
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

    # ----- Users ---------------------------------------------------------
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

    users: list[dict] = []
    while True:
        idx = len(users) + 1
        label = _ask(questionary.text(
            f"User #{idx} label (e.g. alice):",
            qmark="?", style=_PROMPT_STYLE,
        )).strip()
        if not label:
            friendly_warn("Label must be non-empty.")
            continue
        uid_raw = _ask(questionary.text(
            f"User #{idx} Telegram user ID:",
            qmark="?", style=_PROMPT_STYLE,
        )).strip()
        try:
            uid = int(uid_raw)
        except ValueError:
            friendly_warn("Not a valid integer.")
            continue
        if any(u["id"] == uid for u in users):
            friendly_warn(f"User ID {uid} already added.")
            continue
        role = _ask(questionary.select(
            f"User #{idx} role:",
            choices=["admin", "developer", "read_only"],
            qmark="?", style=_PROMPT_STYLE,
        ))
        users.append({"id": uid, "label": label, "role": role})

        more = _ask(questionary.confirm(
            "Add another user?",
            default=False, qmark="?", style=_PROMPT_STYLE,
        ))
        if not more:
            break

    if not any(u["role"] == "admin" for u in users):
        friendly_warn(
            "No admin user added. You'll need to hand-edit team.yaml to "
            "promote one — admin is the only role that can bypass "
            "deny_tools rules.",
        )

    # ----- Deny rules ----------------------------------------------------
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
    deny_tools = ["Write", "Edit"] if enable_default_deny else []

    # ----- Write file ----------------------------------------------------
    import yaml as _yaml  # local import — pyyaml is a project dep

    data: dict = {
        "mode": "team",
        "group_id": group_id,
        "users": users,
    }
    if deny_tools:
        data["rules"] = {"deny_tools": deny_tools}

    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        TEAM_YAML.write_text(
            "# aipager team mode — managed by `aipager config`.\n"
            "# Edit by hand to add / remove users. Restart the daemon\n"
            "# after changes (`aipager service restart` or kill + start).\n"
            + _yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
        )
        os.chmod(TEAM_YAML, 0o600)
    except OSError as e:
        raise OSError(f"cannot write {TEAM_YAML}: {e}") from e

    ok(f"Wrote {TEAM_YAML}")
    return True


def run() -> int:
    if console.is_terminal:
        rule("aipager setup")
    else:
        console.print("Welcome to aipager setup.")
    try:
        token, bot_username = _step_token()
        chat_id = _step_chat_id(token, bot_username)
        deps_ok = _step_deps()
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
        _step_settings()
        _step_write_env(token, chat_id)
        _step_team_config()
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


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
