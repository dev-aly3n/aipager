"""Interactive setup wizard for `aipager config`.

Walks the user through:
  1. Bot token + verify via getMe
  2. Chat ID via getUpdates auto-detect (or manual paste)
  3. Dep check (dtach, claude)
  4. Patch ~/.claude/settings.json with hooks + statusLine (back up first)
  5. Write ~/.config/aipager/config.env (0600)

Idempotent — safe to re-run; existing aipager-hook entries are not duplicated.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

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


def _http_json(url: str) -> dict:
    # 30s tolerates slow VPN/proxy TLS handshakes; under direct connections
    # the call completes in well under a second.
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def _verify_token(token: str) -> dict | None:
    try:
        result = _http_json(f"https://api.telegram.org/bot{token}/getMe")
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None
    return result.get("result") if result.get("ok") else None


def _test_send(token: str, chat_id: int) -> tuple[bool, str]:
    """Send a "hello" probe to verify the bot can reach the chat.

    Returns ``(True, "")`` on success or ``(False, error_description)``
    on any failure. The common failure is Telegram's
    ``Bad Request: chat not found`` which means the user hasn't opened
    the bot in their Telegram client yet (Telegram refuses bot-→-user
    messages until the user initiates).
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


def _fetch_chat_id(token: str) -> tuple[int, str] | None:
    try:
        result = _http_json(f"https://api.telegram.org/bot{token}/getUpdates")
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None
    if not result.get("ok"):
        return None
    for u in result.get("result", []):
        msg = u.get("message") or u.get("edited_message") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is not None and chat.get("type") == "private":
            who = chat.get("username") or chat.get("first_name", "")
            return int(cid), who
    return None


def _step_token() -> tuple[str, str]:
    """Returns (token, bot_username)."""
    while True:
        print("\n[1/5] Telegram bot")
        print("  → Get a bot token from @BotFather (https://t.me/BotFather)")
        token = input("  Bot token: ").strip().rstrip(":")
        if not token:
            print("  (empty — try again)")
            continue
        info = _verify_token(token)
        if info is None:
            print("  ✗ Token invalid or Telegram unreachable. Try again or Ctrl-C to exit.")
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
            found = _fetch_chat_id(token)
            if found is None:
                print("  ✗ No DM detected. Send a message to your bot then press Enter again.")
                continue
            cid, who = found
            print(f"  ✓ Detected chat_id={cid} (@{who})")
            cid = int(cid)

        # Always verify the bot can actually send to this chat. Telegram
        # silently refuses bot-→-user sends until the user has tapped Start.
        ok, err = _test_send(token, cid)
        if ok:
            if raw:
                print(f"  ✓ Using chat_id={cid} — test message delivered.")
            return cid

        if "chat not found" in err.lower():
            print(f"  ✗ Telegram says: {err}")
            print(f"     This means you haven't started a conversation with @{bot_username} yet.")
            print(f"     1. Open https://t.me/{bot_username}")
            print("     2. Tap Start (or send any message)")
            print("     3. Then press Enter here to retry.")
            input("    Press Enter once you've sent a message to the bot: ")
            # Retry the same chat_id once.
            ok2, err2 = _test_send(token, cid)
            if ok2:
                print(f"  ✓ chat_id={cid} — test message delivered.")
                return cid
            print(f"  ✗ Still failing: {err2}")
            print("     Restarting the chat-id prompt.")
            continue

        print(f"  ✗ Test send failed: {err}")
        print("     Try again, or paste a different chat ID.")


def _step_deps() -> None:
    print("\n[3/5] System dependencies")

    # dtach: aipager bundles it via dtach-bin so it normally won't be on the
    # shell's PATH, but it's reachable via dtach_bin.path(). Only complain
    # if neither the bundled binary nor PATH has it.
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


def _resolve(cmd: str) -> str:
    """Resolve a console-script name to an absolute path if found on PATH.

    Editable installs and pipx installs put scripts in different locations,
    and Claude Code subprocesses don't always inherit a useful PATH. Using
    an absolute path in settings.json avoids the whole class of problems.
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
    if CLAUDE_SETTINGS.exists():
        try:
            settings = json.loads(CLAUDE_SETTINGS.read_text())
        except json.JSONDecodeError:
            print(f"  ✗ {CLAUDE_SETTINGS} is not valid JSON — aborting.")
            raise
        backup = CLAUDE_SETTINGS.with_name(
            f"{CLAUDE_SETTINGS.name}.bak.{int(time.time())}"
        )
        backup.write_text(CLAUDE_SETTINGS.read_text())
        print(f"  • backed up existing settings → {backup.name}")
    else:
        CLAUDE_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    _merge_hooks(settings)
    CLAUDE_SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"  ✓ Patched {CLAUDE_SETTINGS} ({len(HOOK_EVENTS)} hooks + statusLine)")


def _step_write_env(token: str, chat_id: int) -> None:
    print("\n[5/5] Write config")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_ENV.write_text(
        f"CLAUDE_TG_BOT_TOKEN={token}\nCLAUDE_TG_CHAT_ID={chat_id}\n"
    )
    os.chmod(CONFIG_ENV, 0o600)
    print(f"  ✓ Wrote {CONFIG_ENV} (0600)")


def run() -> int:
    print("Welcome to aipager setup.")
    try:
        token, bot_username = _step_token()
        chat_id = _step_chat_id(token, bot_username)
        _step_deps()
        _step_settings()
        _step_write_env(token, chat_id)
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    except (OSError, json.JSONDecodeError) as e:
        print(f"\n✗ Setup failed: {e}", file=sys.stderr)
        return 1
    print("\nSetup complete.\n")
    print("  Start the daemon:    aipager start")
    print("  Launch a session:    aipager session dev")
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
