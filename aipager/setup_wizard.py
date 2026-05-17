"""Interactive setup wizard for `aipager config`.

Walks the user through:
  1. Bot token + verify via getMe
  2. Chat ID via getUpdates auto-detect (or manual paste) + test send
  3. Dep check (dtach, claude)
  4. Patch ~/.claude/settings.json with hooks + statusLine (back up first)
  5. Write ~/.config/aipager/config.env (0600)

Idempotent — safe to re-run; existing aipager-hook entries are not duplicated.
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

from aipager.errors import friendly_error, friendly_warn

CONFIG_DIR = Path.home() / ".config" / "aipager"
CONFIG_ENV = CONFIG_DIR / "config.env"
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"

HOOK_CMD = "aipager-hook"
STATUSLINE_CMD = "aipager-statusline"
HOOK_EVENTS = (
    "SessionStart", "SessionEnd", "UserPromptSubmit",
    "PreToolUse", "PostToolUse", "PermissionRequest",
    "Notification", "Stop", "SubagentStop", "PreCompact",
)
TOOL_MATCHER_EVENTS = {"PreToolUse", "PostToolUse", "PermissionRequest"}

# Bot tokens are <int>:<35-50 alnum>, but we accept anything matching that
# pattern shape since BotFather has changed token lengths over time.
_TOKEN_RE = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{20,80}")
_CHAT_NOT_FOUND_RE = re.compile(r"chat\s*[\s_-]*not\s*[\s_-]*found", re.I)


def _normalize_token(raw: str) -> str:
    """Pull a clean bot token out of common paste shapes.

    Handles surrounding quotes, "Use this token: …" lead-ins from BotFather,
    embedded newlines, and trailing colons.
    """
    if not raw:
        return ""
    raw = raw.strip().strip('"').strip("'")
    # Find the first canonical token in the string.
    m = _TOKEN_RE.search(raw)
    if m:
        return m.group(0)
    # Fallback: if no canonical match, return a conservatively trimmed value.
    return raw.rstrip(":").strip()


def _http_json(url: str) -> tuple[dict | None, int | None, str]:
    """Return ``(body, http_status, error_description)``.

    Exactly one of (body, error_description) is meaningful. ``http_status``
    is set whenever the server returned a response (success OR HTTPError),
    and ``None`` for pre-HTTP errors (DNS, connect, etc).
    """
    try:
        # 30s tolerates slow VPN/proxy TLS handshakes; under direct connections
        # the call completes in well under a second.
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
    """Render a one-line explanation for a Telegram HTTP failure."""
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
    print(f"  ✗ {_explain_http_error(code, err)}")
    return None


def _test_send(token: str, chat_id: int) -> tuple[bool, str]:
    """Send a "hello" probe to verify the bot can reach the chat.

    Returns ``(True, "")`` on success or ``(False, error_description)``.
    """
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
    """Returns ``(chat_id, who, advisory)``.

    ``advisory`` is set when we saw activity but couldn't pick a private
    chat (e.g., user spoke to the bot in a group). The caller surfaces it
    so the user knows what's wrong.
    """
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


def _step_token() -> tuple[str, str]:
    """Returns (token, bot_username)."""
    while True:
        print("\n[1/5] Telegram bot")
        print("  → Get a bot token from @BotFather (https://t.me/BotFather)")
        raw = input("  Bot token: ")
        token = _normalize_token(raw)
        if not token:
            print("  (empty — try again)")
            continue
        info = _verify_token(token)
        if info is None:
            print("  Try again or Ctrl-C to exit.")
            continue
        username = info.get("username") or "your_bot"
        print(f"  ✓ Verified — @{username}")
        return token, username


def _step_chat_id(token: str, bot_username: str) -> int:
    print("\n[2/5] Your chat ID")
    print("  → DM your bot, then press Enter to auto-detect; or paste your chat ID.")
    while True:
        raw = input("  Press Enter to auto-detect, or paste chat ID: ").strip()
        if raw:
            try:
                cid = int(raw)
            except ValueError:
                print("  ✗ Not a number. Try again.")
                continue
        else:
            found_id, who, advisory = _fetch_chat_id(token)
            if found_id is None:
                if advisory:
                    print(f"  ✗ {advisory}")
                else:
                    print("  ✗ No DM detected. Send a message to your bot then press Enter.")
                continue
            cid = found_id
            print(f"  ✓ Detected chat_id={cid} (@{who})")

        # Always verify the bot can actually send to this chat. Telegram
        # silently refuses bot→user sends until the user has tapped Start.
        ok, err = _test_send(token, cid)
        if ok:
            print(f"  ✓ chat_id={cid} — test message delivered.")
            confirm = input("    Did the test message arrive in Telegram? [Y/n]: ").strip().lower()
            if confirm in ("", "y", "yes"):
                return cid
            print("  ↻ Let's try again — the message went somewhere unexpected.")
            continue

        if _CHAT_NOT_FOUND_RE.search(err):
            print(f"  ✗ Telegram says: {err}")
            print(f"     This means you haven't started a conversation with @{bot_username} yet.")
            print(f"     1. Open https://t.me/{bot_username}")
            print("     2. Tap Start (or send any message)")
            print("     3. Then press Enter here to retry.")
            input("    Press Enter once you've sent a message to the bot: ")
            ok2, err2 = _test_send(token, cid)
            if ok2:
                print(f"  ✓ chat_id={cid} — test message delivered.")
                return cid
            print(f"  ✗ Still failing: {err2}")
            print("     Restarting the chat-id prompt.")
            continue

        print(f"  ✗ Test send failed: {err}")
        print("     Try again, or paste a different chat ID.")


def _step_deps() -> bool:
    """Returns True if all required deps are present, False otherwise."""
    print("\n[3/5] System dependencies")

    dtach_p: str | None = None
    try:
        from dtach_bin import path as _dtach_path
        dtach_p = _dtach_path()
    except (ImportError, FileNotFoundError):
        dtach_p = shutil.which("dtach")
    if dtach_p:
        print(f"  ✓ dtach found at {dtach_p}")
    else:
        print("  ✗ dtach not found.")
        print("    Normally aipager bundles dtach via dtach-bin. Try:")
        print("        uv tool install --reinstall aipager")
        print("    Or install system-wide: `brew install dtach` / `sudo apt install dtach`.")

    claude_p = shutil.which("claude")
    if claude_p:
        print(f"  ✓ claude found at {claude_p}")
    else:
        print("  ✗ claude not on PATH —")
        print("    install Claude Code: https://docs.anthropic.com/claude/docs/claude-code")

    hook_p = shutil.which(HOOK_CMD)
    statusline_p = shutil.which(STATUSLINE_CMD)
    if not hook_p or not statusline_p:
        print(f"  ✗ {HOOK_CMD} or {STATUSLINE_CMD} not on PATH —")
        print("    your aipager install is incomplete. Try:")
        print("        uv tool install --reinstall aipager")
        return False

    return bool(dtach_p and claude_p)


def _resolve(cmd: str) -> str:
    """Resolve a console-script name to an absolute path. Caller must have
    pre-checked that ``shutil.which(cmd)`` is not None.
    """
    return shutil.which(cmd) or cmd


def _has_hook_cmd(entries: list, bare_name: str) -> bool:
    """Match a hook entry by bare name or by any absolute path ending in it."""
    for block in entries:
        for hook in block.get("hooks", []):
            cmd = hook.get("command", "")
            if cmd == bare_name or cmd.endswith(f"/{bare_name}"):
                return True
    return False


def _validate_settings_schema(settings: dict) -> None:
    """Raise ValueError with a user-readable message if hooks schema is bad."""
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
    print("\n[4/5] Claude Code integration")
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
            hint = ""
            if re.search(r"^\s*//|/\*", existing_text):
                hint = ("\n     Looks like the file has // or /* */ comments. "
                        "Claude Code uses strict JSON — strip the comments.")
            raise ValueError(
                f"{CLAUDE_SETTINGS} is not valid JSON ({e}).{hint}"
            ) from e
        try:
            _validate_settings_schema(settings)
        except ValueError as e:
            raise ValueError(f"{CLAUDE_SETTINGS} schema problem: {e}") from e
        # Skip backup if our merge wouldn't change anything.
        new_settings = json.loads(existing_text)
        _merge_hooks(new_settings)
        new_text = json.dumps(new_settings, indent=2) + "\n"
        if new_text == existing_text:
            print(f"  ✓ {CLAUDE_SETTINGS} already up to date")
            return
        backup = CLAUDE_SETTINGS.with_name(
            f"{CLAUDE_SETTINGS.name}.bak.{int(time.time())}"
        )
        backup.write_text(existing_text)
        print(f"  • backed up existing settings → {backup.name}")
    else:
        CLAUDE_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    _merge_hooks(settings)
    try:
        CLAUDE_SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")
    except OSError as e:
        raise OSError(f"cannot write {CLAUDE_SETTINGS}: {e}") from e
    print(f"  ✓ Patched {CLAUDE_SETTINGS} ({len(HOOK_EVENTS)} hooks + statusLine)")


def _step_write_env(token: str, chat_id: int) -> None:
    print("\n[5/5] Write config")
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
            answer = input(
                f"  ⚠ {CONFIG_ENV} already has different settings. Overwrite? [y/N]: "
            ).strip().lower()
            if answer not in ("y", "yes"):
                print("  ↷ keeping existing config; new token not written.")
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
    print(f"  ✓ Wrote {CONFIG_ENV}")


def run() -> int:
    print("Welcome to aipager setup.")
    try:
        token, bot_username = _step_token()
        chat_id = _step_chat_id(token, bot_username)
        deps_ok = _step_deps()
        if not deps_ok:
            answer = input("\n  Continue anyway (the daemon will likely fail)? [y/N]: ").strip().lower()
            if answer not in ("y", "yes"):
                friendly_warn("Setup aborted — install the missing dependencies and re-run `aipager config`.")
                return 2
        _step_settings()
        _step_write_env(token, chat_id)
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    except ValueError as e:
        friendly_error(str(e))
        return 1
    except OSError as e:
        friendly_error(f"Setup failed: {e}")
        return 1
    print("\nSetup complete.\n")
    print("  Start the daemon:    aipager start")
    print("  Launch a session:    aipager session dev")
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
