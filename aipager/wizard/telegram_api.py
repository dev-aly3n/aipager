"""See :mod:`aipager.wizard` for the package overview."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


from aipager.ui import err_console
from aipager.wizard._constants import (
    _TOKEN_RE,
)


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
