"""See :mod:`aipager.wizard` for the package overview."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from aipager.errors import redact_token
from aipager.team import is_shared_sender_id
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
            return None, e.code, redact_token(str(e))
    except urllib.error.URLError as e:
        return None, None, redact_token(f"network: {e.reason}")
    except (OSError, json.JSONDecodeError) as e:
        return None, None, redact_token(str(e))


def _explain_http_error(code: int | None, err: str) -> str:
    if code == 401:
        return ("HTTP 401 - Telegram rejected the token. Generate a fresh one "
                "from @BotFather.")
    if code == 404:
        return ("HTTP 404 - the bot token URL is malformed. Double-check the "
                "token you pasted.")
    if code == 429:
        return ("HTTP 429 - Telegram is rate-limiting us. Wait a minute "
                "and retry.")
    if code and code >= 500:
        return f"HTTP {code} - Telegram API error. Probably transient; retry."
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
        # Same redaction as _http_json: this URL carries the token too.
        try:
            body = json.loads(e.read())
            desc = redact_token(body.get("description", str(e)))
        except Exception:
            return False, redact_token(str(e))
        return False, desc + _migrated_hint(body)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return False, redact_token(str(e))
    if not result.get("ok"):
        return False, (result.get("description", "unknown error")
                       + _migrated_hint(result))
    return True, ""


def _migrated_hint(body) -> str:
    """For a group Telegram upgraded to a supergroup (roadmap 8.87): the
    new id the refusal carries (``parameters.migrate_to_chat_id``), and
    what happens next. ``""`` for any other refusal."""
    params = body.get("parameters") if isinstance(body, dict) else None
    new = params.get("migrate_to_chat_id") if isinstance(params, dict) else None
    if isinstance(new, bool) or not isinstance(new, int):
        return ""
    return (f". Telegram upgraded this group to a supergroup, and its new id "
            f"is {new}. The aipager daemon moves the scope there by itself "
            f"when it starts or sees the upgrade, so there is nothing to "
            f"change here; start the daemon (or restart it), then reopen "
            f"`aipager config` to test again.")


#: Auto-detect saw only anonymous admins or channel posts (8.91e).
ANONYMOUS_SENDER_ADVISORY = (
    "Only messages sent anonymously or as a channel were seen; they do not "
    "say who sent them. Ask the person to post as themselves, then try again.")


#: Roadmap 8.88: the one line that says where auto-detect looks.
WATCH_SOURCE_LINES = {
    "daemon": "Watching through the running daemon (it notes who "
              "messages the bot).",
    "telegram": "Watching Telegram directly.",
}

#: getUpdates answered 409: another program (an aipager daemon this
#: wizard could not see, say in a container) reads this bot's updates.
UPDATES_CONFLICT_ADVISORY = (
    "Another program is reading this bot's messages (an aipager daemon "
    "running elsewhere?). Stop it and try again, or paste the id instead.")

#: The daemon notes senders only in groups: a DM from someone who is not
#: set up yet is answered, never written down.
DAEMON_DM_ADVISORY = (
    "With the daemon running, only messages in a group the bot is in are "
    "seen. Ask them to mention the bot (or send /start) in such a group, "
    "paste their id instead, or stop the daemon (aipager service stop, "
    "or Ctrl-C a foreground aipager start) and try again.")

#: Nothing new in the daemon's records (roadmap 8.88).
DAEMON_NO_USER_ADVISORY = (
    "The running daemon has noted nobody new. It notes people who message "
    "the bot in a group (a DM from someone not set up is not noted). Ask "
    "them to mention the bot in a group it is in, or paste their id.")
DAEMON_NO_GROUP_ADVISORY = (
    "The running daemon has seen no message in a group it does not serve "
    "yet. Add the bot to the group and send /start there, then try again.")

#: Auto-detect saw only groups already set up or turned down (8.88).
ONLY_KNOWN_GROUPS_ADVISORY = (
    "Only groups that are already set up (or that you turned down here) "
    "were seen. Add the bot to the new group and send /start there. To "
    "add someone to a group already set up, use Edit a scope → Add a "
    "member.")

#: Auto-detect saw only people already captured (roadmap 8.88).
ONLY_KNOWN_SENDERS_ADVISORY = (
    "Only messages from people already added were seen. Ask the new "
    "person to mention the bot, then try again.")

_WANTS = ("dm", "group", "user")


class NameOnly(str):
    """A detected person's name when they have no Telegram username: it
    reads like a plain string (the label suggestion), but must never be
    shown as an @handle (roadmap 8.88: anyone can pick any name)."""


def _sender_name(username, name) -> str:
    """``username`` if the sender has one, else their name as
    :class:`NameOnly` (``""`` when neither is known)."""
    if isinstance(username, str) and username:
        return username
    if isinstance(name, str) and name:
        return NameOnly(name)
    return ""


def _watch_source() -> str:
    """``"daemon"`` while the aipager daemon runs (its long poll takes the
    bot's updates, so the wizard reads what the daemon wrote down),
    ``"telegram"`` otherwise (the wizard calls ``getUpdates`` itself)."""
    from aipager.wizard.daemon_io import _detect_daemon_running
    try:
        running = _detect_daemon_running() is not None
    except Exception:
        running = False
    return "daemon" if running else "telegram"


def _detect_id(
    token: str, *, want: str, exclude=frozenset(), source: str = "telegram",
) -> tuple[int | None, str | None, str | None]:
    """Auto-detect from *source* (:func:`_watch_source`): the daemon's
    records or ``getUpdates``. Same contract as
    :func:`_fetch_id_from_updates`."""
    if source == "daemon":
        return _fetch_id_from_pending(want=want, exclude=exclude)
    return _fetch_id_from_updates(token, want=want, exclude=exclude)


def _fetch_id_from_pending(
    *, want: str, exclude=frozenset(),
) -> tuple[int | None, str | None, str | None]:
    """The newest matching record in the daemon's pending-users file
    (``team.PENDING_USERS_PATH``), skipping ids in *exclude*.

    The daemon writes a record (latest last) for every non-member who
    messages a configured chat and for every sender in a group it does
    not serve (roadmap 8.88). ``want="group"`` returns that group's chat
    id and title; ``want="user"`` the sender. ``want="dm"`` finds
    nothing: a DM from someone not set up is never written down.
    """
    if want not in _WANTS:
        raise ValueError(f"unknown auto-detect target: {want!r}")
    from aipager.team import list_pending_users
    if want == "dm":
        return None, None, DAEMON_DM_ADVISORY
    saw_known = False
    for r in reversed(list_pending_users()):
        if not isinstance(r, dict):
            continue
        if want == "group":
            cid = r.get("chat_id")
            if (r.get("chat_type") not in ("group", "supergroup")
                    or not isinstance(cid, int) or isinstance(cid, bool)):
                continue
            if cid in exclude:
                saw_known = True
                continue
            return cid, (r.get("chat_title") or ""), None
        uid = r.get("user_id")
        # (list_pending_users already hides Telegram's shared sender ids.)
        if not isinstance(uid, int) or isinstance(uid, bool):
            continue
        if uid in exclude:
            saw_known = True
            continue
        who = _sender_name(r.get("username"), r.get("display_name"))
        return uid, who, None
    if saw_known:
        return None, None, (ONLY_KNOWN_GROUPS_ADVISORY if want == "group"
                            else ONLY_KNOWN_SENDERS_ADVISORY)
    if want == "group":
        return None, None, DAEMON_NO_GROUP_ADVISORY
    return None, None, DAEMON_NO_USER_ADVISORY


def _fetch_id_from_updates(
    token: str, *, want: str, exclude=frozenset(),
) -> tuple[int | None, str | None, str | None]:
    """Poll ``getUpdates`` for the most recent matching id.

    ``want`` selects what we're looking for:
      - ``"dm"``    — most recent private (DM) chat id.
      - ``"group"`` — most recent group / supergroup chat id.
      - ``"user"``  — most recent ``from.user.id`` (any chat); useful
                       for capturing a new team member's Telegram id.

    Newest first (roadmap 8.88): Telegram lists updates oldest first, and
    nothing here confirms them, so the oldest match came back on every
    call. Ids in *exclude* (people already captured, chats the caller
    rules out) are skipped, so the next person is found.

    Returns ``(id, friendly_name, advisory)`` where ``advisory`` is a
    user-facing hint when the wrong kind of update was seen (so the
    wizard can nudge them in the right direction).
    """
    if want not in _WANTS:
        raise ValueError(f"unknown auto-detect target: {want!r}")
    body, code, _err = _http_json(
        f"https://api.telegram.org/bot{token}/getUpdates"
    )
    if code == 409:
        return None, None, UPDATES_CONFLICT_ADVISORY
    if not body or not body.get("ok"):
        return None, None, None

    saw_other: list[str] = []
    saw_anonymous = False
    saw_known = False
    for u in reversed(body.get("result") or []):
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
                if int(cid) in exclude:
                    saw_known = True
                    continue
                who = chat.get("title", "")
                return int(cid), who, None
            saw_other.append(ctype or "?")
        elif want == "user":
            uid = sender.get("id")
            if msg.get("sender_chat") or is_shared_sender_id(uid):
                # An anonymous admin or a channel post: its `from` is an id
                # every such message shares, never the person (8.91e).
                saw_anonymous = True
                continue
            if uid is not None:
                if int(uid) in exclude:
                    saw_known = True
                    continue
                who = _sender_name(sender.get("username"),
                                   sender.get("first_name"))
                return int(uid), who, None

    if want == "user" and saw_anonymous:
        return None, None, ANONYMOUS_SENDER_ADVISORY
    if want == "user" and saw_known:
        return None, None, ONLY_KNOWN_SENDERS_ADVISORY
    if want == "group" and saw_known:
        return None, None, ONLY_KNOWN_GROUPS_ADVISORY
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
