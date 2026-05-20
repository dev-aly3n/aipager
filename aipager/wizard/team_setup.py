"""See :mod:`aipager.wizard` for the package overview."""

from __future__ import annotations


import questionary

from aipager.errors import friendly_warn
from aipager.ui import console, err_console, hint, ok
from aipager.wizard._constants import (
    _PROMPT_STYLE,
)

from aipager.wizard.display import (
    _ask,
    _spin,
)
from aipager.wizard.telegram_api import (
    _fetch_id_from_updates,
    _http_json,
)


def _fetch_chat_id(token: str) -> tuple[int | None, str | None, str | None]:
    """Backwards-compatible wrapper — DM (private chat) lookup."""
    return _fetch_id_from_updates(token, want="dm")


def _resolve_user(
    token: str, query: str,
) -> tuple[int, str] | None:
    """Resolve a numeric id OR ``@handle`` to ``(user_id, suggested_label)``.

    The Telegram bot API has a real constraint here: ``getChat`` with
    ``@username`` only resolves **channels and supergroups** — not
    individual users. So @handle lookup goes through two paths:

    1. **`getChat?chat_id=@handle`** — works for users only when the
       bot has been talking with them before (and Telegram's
       implementation extends private-chat lookup to known contacts).
       This is the fast path.
    2. **`getUpdates` scan** — fall back to scanning the bot's recent
       update queue for a message whose ``from.username`` matches.
       Works for any group member who's sent at least one message
       recently.

    Numeric input goes only through path 1 (no scan needed — we
    already have the id; getChat is just label enrichment).

    Returns ``None`` when neither path identifies a *private*
    Telegram user — channels / bots are rejected explicitly so
    admins can't accidentally allow-list them.
    """
    if not token:
        return None
    raw = query.strip()
    if not raw:
        return None

    # Path 0: pending-users registry. When an unauthorized user
    # mentioned the bot earlier, we persisted their numeric id +
    # handle + name. If the admin types the same handle now, we
    # already have everything — no Telegram round-trip needed.
    from aipager.team import list_pending_users
    handle_only = raw.lstrip("@").lower()
    if handle_only and not handle_only.lstrip("-").isdigit():
        for r in list_pending_users():
            if (r.get("username") or "").lower() == handle_only:
                try:
                    return int(r["user_id"]), handle_only
                except (KeyError, TypeError, ValueError):
                    pass
    elif handle_only.lstrip("-").isdigit():
        # numeric lookup — pending file might still enrich the label
        try:
            qid = int(handle_only)
            for r in list_pending_users():
                if r.get("user_id") == qid:
                    suggested = (r.get("username")
                                 or r.get("display_name")
                                 or f"user{qid}").lower()
                    return qid, suggested
        except ValueError:
            pass

    # Forgiving input: bare handle (no @) gets one prefixed; numeric
    # input goes through unchanged.
    numeric_input = False
    if raw.startswith("@"):
        chat_id = raw
        handle_lc = raw[1:].lower()
    else:
        try:
            int(raw)
            chat_id = raw
            handle_lc = ""
            numeric_input = True
        except ValueError:
            chat_id = f"@{raw}"
            handle_lc = raw.lower()

    # Path 1: getChat. Works for numeric ids the bot has seen, and
    # for @handles tied to private users when Telegram permits.
    body, _code, _err = _http_json(
        f"https://api.telegram.org/bot{token}/getChat?chat_id={chat_id}"
    )
    if body and body.get("ok"):
        result = body.get("result") or {}
        chat_type = result.get("type")
        if chat_type == "private":
            try:
                uid = int(result["id"])
                suggested = (
                    result.get("username")
                    or result.get("first_name")
                    or f"user{uid}"
                ).strip() or f"user{uid}"
                return uid, suggested.lower()
            except (KeyError, TypeError, ValueError):
                pass
        elif chat_type in ("channel", "group", "supergroup", "bot"):
            # Hard reject — admin tried to allow-list a non-user.
            return None

    # Numeric ids end here; no scan to do.
    if numeric_input or not handle_lc:
        return None

    # Path 2: scan getUpdates for a from.username match (lowercased).
    body, _code, _err = _http_json(
        f"https://api.telegram.org/bot{token}/getUpdates"
    )
    if not body or not body.get("ok"):
        return None
    for u in body.get("result", []):
        msg = u.get("message") or u.get("edited_message") or {}
        sender = msg.get("from") or {}
        their_handle = (sender.get("username") or "").lower()
        if their_handle and their_handle == handle_lc:
            try:
                uid = int(sender["id"])
                suggested = (
                    sender.get("username")
                    or sender.get("first_name")
                    or f"user{uid}"
                ).strip() or f"user{uid}"
                return uid, suggested.lower()
            except (KeyError, TypeError, ValueError):
                continue
    return None


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
            "Ask the new user to either DM the bot (tap /start) or "
            "mention the bot in the group — any message the bot can see "
            "will reveal their numeric id."
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
                # Split message into separate lines so warn_block
                # renders them on multiple rows (single multi-line
                # string gets jammed into the panel title).
                friendly_warn(
                    f"Couldn't resolve {raw!r}.",
                    "",
                    "Telegram's bot API doesn't expose username → user_id",
                    "lookups, so the bot has to have already seen them.",
                    "",
                    "Ask the user to do ONE of these, then retry:",
                    "  • DM the bot directly (open it and tap /start), OR",
                    "  • Send any message in the group (a mention of",
                    "    the bot works best — privacy-on bots only see",
                    "    those).",
                )
                next_step = _ask(questionary.select(
                    "What now?",
                    choices=[
                        questionary.Choice("Retry (paste id or @handle again)",
                                           value="retry"),
                        questionary.Choice("Switch to auto-detect",
                                           value="auto"),
                        questionary.Choice("Cancel this user "
                                           "(continue with the ones already added)",
                                           value="cancel"),
                    ],
                    qmark="?", style=_PROMPT_STYLE,
                ))
                if next_step == "retry":
                    continue
                if next_step == "auto":
                    method = "auto"
                    # Re-enter the function so the auto-detect branch
                    # runs. Simpler than refactoring the if/else.
                    return _capture_user_identity(
                        idx,
                        existing_ids=existing_ids,
                        existing_labels=existing_labels,
                        token=token,
                    )
                return None  # cancel
            uid, suggested_label = resolved
            ok(f"Resolved {raw} → id={uid} (@{suggested_label})")
        else:
            # Numeric input.
            try:
                uid = int(raw)
            except ValueError:
                friendly_warn(
                    f"{raw!r} isn't a valid integer or @handle.",
                )
                next_step = _ask(questionary.select(
                    "What now?",
                    choices=[
                        questionary.Choice("Retry", value="retry"),
                        questionary.Choice("Cancel this user "
                                           "(continue with the ones already added)",
                                           value="cancel"),
                    ],
                    qmark="?", style=_PROMPT_STYLE,
                ))
                if next_step == "cancel":
                    return None
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
