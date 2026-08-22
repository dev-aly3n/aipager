"""Chat-side session parity: restart/rename/delete/diff commands, the
per-session "⋮" menu, and per-session preferences.

Stream B of design.md ("Sync Telegram commands with the Mini App"). Owns
this one module in full — see ``research/ship/sync-commands-with-mini-app/
entrypoints.md`` for the binding function-name / callback-data contract a
black-box Tester exercises. Everything not named in that document (every
``_``-prefixed helper below) is an internal implementation detail.

Design constraints this module is written to (see design.md Alternatives
+ Non-negotiable orchestrator rules):

- No module-level mutable state. All pending state (the per-chat rename
  capture, the per-chat session→preferences index) lives on
  lazily-initialised ``TelegramBot`` instance attributes
  (``bot._rename_pending``, ``bot._session_pref_index``), created via a
  ``getattr``/``setattr`` guard rather than a ``core.py`` edit — a module
  dict would leak across pytest tests that build a fresh ``TelegramBot``
  per test; an instance attribute dies with the instance.
- Every session-scoped write (restart, rename, delete, and a per-session
  preference set/clear) is gated by ``bot._can_prompt_user`` at the exact
  moment of the mutating tap/text — not only when the surface offering it
  was drawn. Destructive actions (restart, delete) re-check the gate at
  BOTH the "show confirm" step and the "confirm" step independently,
  since the two taps can be arbitrarily far apart in time.
- A stale ``_:spref:<idx>`` index (a session killed/deleted/renamed away
  between the index being registered and the tap arriving) fails closed
  ("no longer available") rather than silently touching whatever now
  sits at that index.
- Reachable only through the exported functions below — this module is
  never imported by any shared file in this worktree (that wiring is the
  integrator's job, applied after all three streams land; see
  ``implementation-parity.md``).
"""

from __future__ import annotations

import html as html_mod
import io
import logging
import re
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from aipager.bot import settings_menu
from aipager.bot.transport import calling_chat_id
from aipager.preferences import get_preferences, is_valid_value, resolve_preferences
from aipager.state import PREFERENCE_OVERRIDE_FIELDS, Status, TrackedSession

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from aipager.bot.core import TelegramBot

# Schema computed once at import time — settings_menu.settings_schema()
# is a pure function of module-level constants, so there is no reason to
# recompute it on every render. Keyed by section for O(1) lookup.
_SCHEMA = {entry["section"]: entry for entry in settings_menu.settings_schema()}

_SESSION_ACTIONS = frozenset({
    "menu", "menu-close",
    "restart", "restart-confirm", "restart-cancel",
    "rename", "rename-cancel",
    "delete", "delete-confirm", "delete-cancel",
    "diff",
    # Gone-session counterpart to "restart" — the ⋮ menu offers exactly
    # one of the two, never both, since each fails where the other works.
    "resume", "resume-ask", "resume-auto", "resume-cancel",
})

_DIFF_INLINE_THRESHOLD = 3500  # chars — entrypoints.md's "~3500" cutoff

_DIFF_REASON_TEXT = {
    "cwd_missing": "[{label}]'s working directory is no longer there.",
    "git_not_installed": "git isn't available on this machine.",
    "not_a_git_repo": "[{label}]'s directory isn't a git repo.",
    "no_commits_yet": "[{label}]'s repo has no commits yet.",
    "git_error": "Couldn't read the diff right now — try again.",
}

_RESTART_REASON_TEXT = {
    "not_live": "[{label}] isn't running.",
    "already_restarting": "[{label}] is already restarting.",
    "still_stopping": "[{label}] didn't stop in time — try again shortly.",
    "launch_failed": "Restart failed for [{label}]: {err}",
}


# ---- lazily-initialised TelegramBot instance state -----------------------

def _pref_index_map(bot: "TelegramBot") -> dict[int, list[str]]:
    """``bot._session_pref_index`` — created on first use, never at
    ``TelegramBot.__init__`` (that would need a core.py edit, which this
    stream does not own). See entrypoints.md's per-session-preferences
    section for the exact attribute name — it is part of the contract.
    """
    index = getattr(bot, "_session_pref_index", None)
    if index is None:
        index = {}
        bot._session_pref_index = index
    return index


def _register_pref_index(bot: "TelegramBot", chat_id: int, names) -> list[str]:
    """Give every name a STABLE index in this chat's table, appending any
    it has not seen before. Returns the whole table.

    This used to overwrite the table with just the names being rendered,
    which was wrong in the one case that matters: the ⋮ menu registers a
    single session, so opening session A's menu and then session B's left
    the table holding only B — and A's still-visible "Preferences" button
    (encoded `_:spref:0`) then resolved to B. A silent write to a session
    the user was not looking at.

    The table is never pruned, and deliberately so: indices are
    POSITIONAL, so evicting an old entry would shift every still-visible
    button onto a different session — exactly the bug the stable table
    exists to prevent. Growth is bounded by the number of DISTINCT
    sessions a chat has rendered since the daemon started, at roughly 80
    bytes each: a chat that showed ten thousand different sessions
    between restarts would hold about 780 KiB. Both halves of that claim
    are pinned by tests rather than asserted here — see
    ``tests/test_parity_integration.py``: re-rendering the same sessions
    does not grow the table, an early index survives arbitrary churn, and
    a deleted session resolves to ``None`` rather than to its neighbour
    because :func:`_resolve_pref_index` re-checks the registry.
    """
    table = _pref_index_map(bot).setdefault(chat_id, [])
    for name in names:
        if name not in table:
            table.append(name)
    return table


# entrypoints.md's grammar: "<idx> — non-negative decimal integer, no
# leading zeros". Matched strictly BEFORE parsing — int()'s own leniency
# (it happily accepts leading/trailing whitespace, a leading "+", etc.,
# e.g. int(" 0") == 0) would otherwise let a hand-crafted, non-canonical
# token resolve as if it were a real rendered index.
_STRICT_IDX_RE = re.compile(r"(?:0|[1-9][0-9]*)")


def _resolve_pref_index(
    bot: "TelegramBot", chat_id: int, idx_token: str,
) -> TrackedSession | None:
    """``None`` for a malformed token, an out-of-range index, or a
    session that no longer exists in the registry — the caller treats
    all three identically ("no longer available"), never distinguishing
    them to the user and never writing to a different session than the
    one that was actually shown."""
    if not isinstance(idx_token, str) or not _STRICT_IDX_RE.fullmatch(idx_token):
        return None
    idx = int(idx_token)
    names = _pref_index_map(bot).get(chat_id) or []
    if idx < 0 or idx >= len(names):
        return None
    return bot.registry.get(names[idx])


def _rename_pending_map(bot: "TelegramBot") -> dict[int, dict]:
    """``bot._rename_pending`` — literal attribute name from
    entrypoints.md's session-scoped callback table (``{name}:rename``'s
    "Meaning" column). Keyed by chat id, same shape as the pre-existing
    ``_new_conflict_pending`` / ``_perms_pending`` dicts this mirrors —
    lazily created here rather than in ``core.py.__init__`` for the same
    leak-across-tests reason as ``_pref_index_map``."""
    pending = getattr(bot, "_rename_pending", None)
    if pending is None:
        pending = {}
        bot._rename_pending = pending
    return pending


def _start_rename_capture(
    bot: "TelegramBot", chat_id: int, sess: TrackedSession,
) -> None:
    _rename_pending_map(bot)[chat_id] = {
        "session_name": sess.name, "label": sess.label,
    }


# ---- small pure helpers ---------------------------------------------------

def _token_for_value(value) -> str:
    """Mirrors settings_menu.render_settings_section's inline
    ``bool → "on"/"off"`` mapping — every other field's values already
    double as their own callback-data token."""
    if value is True:
        return "on"
    if value is False:
        return "off"
    return str(value)


def _value_for_token(section: str, token: str):
    """Inverse of :func:`_token_for_value`, scoped to one section's
    option list. ``None`` for a token that doesn't match any option —
    the caller treats that as "invalid value", same contract as
    ``is_valid_value`` returning ``False``."""
    entry = _SCHEMA.get(section)
    if entry is None:
        return None
    for option in entry["options"]:
        if _token_for_value(option["value"]) == token:
            return option["value"]
    return None


async def _edit(query, text: str, kb: InlineKeyboardMarkup | None) -> None:
    """Edit the tapped message in place. Swallows "message not modified"
    / "message to edit not found" the same way every other callback
    branch in this codebase already does — a toast already told the user
    what happened; a failed edit must never surface as a crash."""
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass


# ---- ⋮ session menu --------------------------------------------------

def _render_session_menu(
    bot: "TelegramBot", chat_id: int, sess: TrackedSession,
) -> tuple[str, InlineKeyboardMarkup]:
    """Restart, Rename, Preferences, Diff, and — only when GONE —
    Delete, in that order (entrypoints.md's "⋮ session menu" section).
    Every row reuses the exact same callback the standalone command's
    own picker/confirm flow uses, so there is one code path per action
    regardless of which surface reached it."""
    names = _register_pref_index(bot, chat_id, [sess.name])
    pref_cb = f"_:spref:{names.index(sess.name)}"
    gone = sess.status == Status.GONE
    # Restart kills and relaunches a RUNNING process; on a gone session
    # `_kill_and_relaunch_core` can only answer `not_live`, so the button
    # cost two taps to be told nothing happened. Resume is the gone-session
    # equivalent — and it needs a transcript id to resume FROM, without
    # which `_do_resume_core` refuses with `no_transcript`. Offering
    # either one where it cannot work is the defect being fixed here, so
    # neither is offered speculatively.
    rows = []
    if not gone:
        rows.append([InlineKeyboardButton(
            "🔄 Restart", callback_data=session_cb(bot, chat_id, sess, "restart"))])
    elif sess.claude_session_id:
        rows.append([InlineKeyboardButton(
            "▶️ Resume", callback_data=session_cb(bot, chat_id, sess, "resume"))])
    rows += [
        [InlineKeyboardButton("✏️ Rename", callback_data=session_cb(bot, chat_id, sess, "rename"))],
        [InlineKeyboardButton("👤 Preferences", callback_data=pref_cb)],
        [InlineKeyboardButton("📝 Diff", callback_data=session_cb(bot, chat_id, sess, "diff"))],
    ]
    if gone:
        rows.append([InlineKeyboardButton(
            "🗑️ Delete", callback_data=session_cb(bot, chat_id, sess, "delete"))])
    rows.append([InlineKeyboardButton(
        "✖️ Close", callback_data=session_cb(bot, chat_id, sess, "menu-close"))])
    text = f"⋮ <b>{html_mod.escape(sess.label)}</b> — choose an action:"
    if gone and not sess.claude_session_id:
        # Say why, rather than quietly showing a shorter menu — "where did
        # Resume go" is a worse question than a one-line answer.
        #
        # Worded as a statement about NOW, not about how the session ended.
        # `_do_resume_core` clears `claude_session_id` before launching (so a
        # resume that dies right after the socket appears cannot loop
        # forever), which means a session being resumed at this instant also
        # reads as GONE-with-no-id. That window is typically under a second
        # and at worst ~8s — a 5s spawn timeout plus ten 0.3s socket polls —
        # and it closes by itself either way: a failed launch restores the
        # id, a successful one flips the status to IDLE. Threading a
        # transitional status through the state machine and its persistence
        # costs far more than it buys; saying something that stays TRUE in
        # both states costs nothing.
        text += ("\n\n<i>No saved transcript to resume from.</i>")
    return text, InlineKeyboardMarkup(rows)


# ---- restart ---------------------------------------------------------

def _render_restart_confirm(
    bot: "TelegramBot", chat_id: int, sess: TrackedSession,
) -> tuple[str, InlineKeyboardMarkup]:
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Restart", callback_data=session_cb(bot, chat_id, sess, "restart-confirm")),
        InlineKeyboardButton("Cancel", callback_data=session_cb(bot, chat_id, sess, "restart-cancel")),
    ]])
    text = (
        f"🔄 Restart session [<b>{html_mod.escape(sess.label)}</b>]? "
        "This will kill and relaunch the running claude process."
    )
    return text, kb


async def handle_restart_cmd(
    bot: "TelegramBot", update: Update, ctx: ContextTypes.DEFAULT_TYPE,
) -> None:
    """``/restart [label]``. No label → picker of live sessions (each
    row itself opens the same confirm ``{name}:restart`` would). With a
    label → resolves it and shows the confirm directly. Either way,
    execution only happens on ``{name}:restart-confirm``."""
    if not await bot._authorize(update):
        return
    text = update.message.text.strip()
    parts = text.split(maxsplit=1)
    chat_id = calling_chat_id(update)

    if len(parts) < 2:
        sessions = [
            sess for sess in bot.registry.all_sessions(chat_id).values()
            if sess.status != Status.GONE and sess.label
        ]
        if not sessions:
            await update.message.reply_text("No live sessions to restart.")
            return
        buttons = [
            [InlineKeyboardButton(f"🔄 {sess.label}", callback_data=session_cb(bot, chat_id, sess, "restart"))]
            for sess in sessions
        ]
        await update.message.reply_text(
            "Which session to restart?", reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    label = parts[1].strip()
    sess = bot.registry.find_by_label(label, chat_id)
    if sess is None:
        await update.message.reply_text(
            f"⚠️ Unknown or already-gone session: {html_mod.escape(label)}",
            parse_mode="HTML",
        )
        return
    reply_text, kb = _render_restart_confirm(bot, chat_id, sess)
    await update.message.reply_text(reply_text, reply_markup=kb, parse_mode="HTML")


def _restart_outcome_text(outcome) -> str:
    if outcome.ok:
        return f"🔄 [<b>{html_mod.escape(outcome.label)}</b>] restarted."
    template = _RESTART_REASON_TEXT.get(outcome.reason, "Restart failed for [{label}].")
    return template.format(
        label=html_mod.escape(outcome.label), err=html_mod.escape(outcome.err),
    )


# ---- rename ------------------------------------------------------------

async def _apply_rename(
    bot: "TelegramBot", sess: TrackedSession, new_label: str, chat_id: int, *,
    reply_target,
) -> None:
    """Validate + collision-check + apply — the direct-rename path both
    ``/rename old new`` and the free-text capture step converge on.
    Mirrors ``server.py:_handle_session_rename``'s own validation
    exactly (same import, same collision rule) so a name chat accepts
    is always one the Mini App would accept too."""
    from aipager.miniapp.launch import validate_session_name

    clean, err = validate_session_name(new_label)
    if err:
        await reply_target.reply_text(f"⚠️ {html_mod.escape(err)}", parse_mode="HTML")
        return

    if clean != sess.label:
        existing = bot.registry.find_by_label(clean, chat_id, include_gone=True)
        if existing is not None and (
            existing.status != Status.GONE or existing.claude_session_id
        ):
            await reply_target.reply_text(
                f"⚠️ A session named {html_mod.escape(clean)} already exists in this chat.",
                parse_mode="HTML",
            )
            return

    outcome = await bot._rename_session_core(sess, clean)
    if outcome.changed:
        await reply_target.reply_text(
            f"✏️ [<b>{html_mod.escape(outcome.previous_label)}</b>] renamed to "
            f"[<b>{html_mod.escape(outcome.new_label)}</b>].",
            parse_mode="HTML",
        )
    else:
        await reply_target.reply_text(
            f"[<b>{html_mod.escape(sess.label)}</b>] name unchanged.",
            parse_mode="HTML",
        )


async def handle_rename_cmd(
    bot: "TelegramBot", update: Update, ctx: ContextTypes.DEFAULT_TYPE,
) -> None:
    """``/rename [old] [new]``. Two args → direct rename, no confirm
    (matches the Mini App, which has none either). Fewer args → picker
    of every labelled session, then a free-text prompt for the new
    name (entrypoints.md)."""
    if not await bot._authorize(update):
        return
    text = update.message.text.strip()
    parts = text.split(maxsplit=2)
    chat_id = calling_chat_id(update)

    if len(parts) >= 3:
        old_label, new_label = parts[1].strip(), parts[2].strip()
        sess = bot.registry.find_by_label(old_label, chat_id, include_gone=True)
        if sess is None:
            await update.message.reply_text(
                f"⚠️ Unknown session: {html_mod.escape(old_label)}", parse_mode="HTML",
            )
            return
        await _apply_rename(bot, sess, new_label, chat_id, reply_target=update.message)
        return

    sessions = [s for s in bot.registry.all_sessions(chat_id).values() if s.label]
    if not sessions:
        await update.message.reply_text("No sessions to rename.")
        return
    buttons = [
        [InlineKeyboardButton(sess.label, callback_data=session_cb(bot, chat_id, sess, "rename"))]
        for sess in sessions
    ]
    await update.message.reply_text(
        "Which session to rename?", reply_markup=InlineKeyboardMarkup(buttons),
    )


# ---- delete --------------------------------------------------------------

def _render_delete_confirm(
    bot: "TelegramBot", chat_id: int, sess: TrackedSession,
) -> tuple[str, InlineKeyboardMarkup]:
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🗑️ Delete", callback_data=session_cb(bot, chat_id, sess, "delete-confirm")),
        InlineKeyboardButton("Cancel", callback_data=session_cb(bot, chat_id, sess, "delete-cancel")),
    ]])
    text = (
        f"🗑️ Remove [<b>{html_mod.escape(sess.label)}</b>] from the session list? "
        "This cannot be undone."
    )
    return text, kb


async def handle_delete_cmd(
    bot: "TelegramBot", update: Update, ctx: ContextTypes.DEFAULT_TYPE,
) -> None:
    """``/delete [label]``. No label → picker of GONE sessions. With a
    label → must resolve to a GONE session, else the same 409-style
    refusal the Mini App gives. Either way ends at confirm/cancel."""
    if not await bot._authorize(update):
        return
    text = update.message.text.strip()
    parts = text.split(maxsplit=1)
    chat_id = calling_chat_id(update)

    if len(parts) < 2:
        sessions = [
            sess for sess in bot.registry.all_sessions(chat_id).values()
            if sess.status == Status.GONE and sess.label
        ]
        if not sessions:
            await update.message.reply_text("No finished sessions to delete.")
            return
        buttons = [
            [InlineKeyboardButton(f"🗑️ {sess.label}", callback_data=session_cb(bot, chat_id, sess, "delete"))]
            for sess in sessions
        ]
        await update.message.reply_text(
            "Which finished session to remove?", reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    label = parts[1].strip()
    sess = bot.registry.find_by_label(label, chat_id, include_gone=True)
    if sess is None:
        await update.message.reply_text(
            f"⚠️ Unknown session: {html_mod.escape(label)}", parse_mode="HTML",
        )
        return
    if sess.status != Status.GONE:
        await update.message.reply_text(
            f"⚠️ [<b>{html_mod.escape(sess.label)}</b>] is still running. "
            "Use /kill to stop it first.",
            parse_mode="HTML",
        )
        return
    reply_text, kb = _render_delete_confirm(bot, chat_id, sess)
    await update.message.reply_text(reply_text, reply_markup=kb, parse_mode="HTML")


# ---- diff ------------------------------------------------------------

def _diff_stats(files: list[dict]) -> tuple[int, int, int]:
    """``(files_changed, lines_added, lines_removed)`` — added/removed
    counted from lines starting with ``+``/``-`` in each file's patch,
    excluding the ``+++``/``---`` header lines (entrypoints.md's exact
    counting rule)."""
    added = removed = 0
    for f in files:
        patch = f.get("patch")
        if not patch:
            continue
        for line in patch.split("\n"):
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                removed += 1
    return len(files), added, removed


def _concat_patch(files: list[dict]) -> str:
    return "\n".join(f["patch"] for f in files if f.get("patch"))


async def _run_diff(bot: "TelegramBot", sess: TrackedSession, *, target_message) -> None:
    """Shared by ``/diff`` and ``{name}:diff`` — ``target_message`` is
    either ``Update.message`` or ``CallbackQuery.message``; both expose
    ``reply_text``/``reply_document``, so one implementation serves both
    entry points."""
    from aipager.miniapp.diff import collect_diff

    try:
        result = await collect_diff(sess.cwd)
    except Exception:
        # collect_diff documents "never raises", but a promise is not a
        # guarantee — and a stack trace rendered into a chat message is
        # the worst possible way to find out it was broken.
        log.warning("collect_diff raised for %s", sess.label, exc_info=True)
        result = {"available": False, "reason": "git_error"}

    if not result.get("available"):
        reason = result.get("reason", "git_error")
        template = _DIFF_REASON_TEXT.get(reason, _DIFF_REASON_TEXT["git_error"])
        await target_message.reply_text(
            template.format(label=html_mod.escape(sess.label)),
            parse_mode="HTML",
        )
        return

    files = result.get("files") or []
    if not files:
        await target_message.reply_text(
            f"No changes in [<b>{html_mod.escape(sess.label)}</b>]'s working directory.",
            parse_mode="HTML",
        )
        return

    n_files, added, removed = _diff_stats(files)
    stat_line = (
        f"📝 [<b>{html_mod.escape(sess.label)}</b>] "
        f"{n_files} files changed, +{added}/-{removed}"
    )
    any_unusable = any(f.get("truncated") or f.get("binary") for f in files)
    patch_text = _concat_patch(files)

    if not any_unusable and len(patch_text) < _DIFF_INLINE_THRESHOLD:
        await target_message.reply_text(
            f"{stat_line}\n<pre>{html_mod.escape(patch_text)}</pre>",
            parse_mode="HTML",
        )
        return

    await target_message.reply_text(stat_line, parse_mode="HTML")
    filename = f"{sess.label}.diff"
    doc = io.BytesIO(patch_text.encode("utf-8"))
    doc.name = filename  # never written to disk — built and sent in memory
    await target_message.reply_document(document=doc, filename=filename)


async def handle_diff_cmd(
    bot: "TelegramBot", update: Update, ctx: ContextTypes.DEFAULT_TYPE,
) -> None:
    """``/diff [label]``. No label → ``registry.last_active_session``.
    With a label → resolves via ``find_by_label``. Read-only, no
    confirm — gated with ``allow_read_only=True``."""
    if not await bot._authorize(update, allow_read_only=True):
        return
    text = update.message.text.strip()
    parts = text.split(maxsplit=1)
    chat_id = calling_chat_id(update)

    if len(parts) < 2:
        name = bot.registry.last_active_session
        sess = bot.registry.get(name) if name else None
        if sess is None:
            await update.message.reply_text("No active session to diff.")
            return
    else:
        label = parts[1].strip()
        sess = bot.registry.find_by_label(label, chat_id, include_gone=True)
        if sess is None:
            await update.message.reply_text(
                f"⚠️ Unknown session: {html_mod.escape(label)}", parse_mode="HTML",
            )
            return

    await _run_diff(bot, sess, target_message=update.message)


# ---- per-session preferences ----------------------------------------

def _render_session_pref_picker(
    bot: "TelegramBot", chat_id: int,
) -> tuple[str, InlineKeyboardMarkup]:
    """``_:spref`` — every session in scope, freshly indexed on each
    render (entrypoints.md: "populated fresh every time the picker ...
    is rendered")."""
    sessions = sorted(
        (s for s in bot.registry.all_sessions(chat_id).values() if s.label),
        key=lambda s: s.label.lower(),
    )
    table = _register_pref_index(bot, chat_id, [s.name for s in sessions])
    # Index comes from the shared table, never from this list's own
    # position — the table is append-only and stable, so the two orders
    # are not the same once a ⋮ menu has registered a session first.
    rows = [
        [InlineKeyboardButton(
            sess.label, callback_data=f"_:spref:{table.index(sess.name)}")]
        for sess in sessions
    ]
    rows.append([InlineKeyboardButton("✖️ Close", callback_data="_:spref:close")])
    if sessions:
        text = "👤 <b>Per-session preferences</b>\n\nPick a session to override its reply style."
    else:
        text = "👤 <b>Per-session preferences</b>\n\nNo sessions yet."
    return text, InlineKeyboardMarkup(rows)


def render_session_preferences_root(
    sess: TrackedSession, chat_id: int, *, cb_prefix: str,
) -> tuple[str, InlineKeyboardMarkup]:
    """Root view: one row per settable field with its current EFFECTIVE
    value and an override marker, mirroring
    ``settings_menu.render_settings_root``'s shape but scoped to one
    session. Called from exactly two sites — ``/settings → Per-session``
    and a session's ⋮ menu "Preferences" row — with a different
    ``cb_prefix`` per call (design.md decision #3): the two paths can
    never show different fields, labels, or current-value markers
    because they're the same function."""
    overrides = sess.preference_overrides()
    effective = resolve_preferences(sess.scope_chat_id or 0, overrides)
    rows = []
    for entry in settings_menu.settings_schema():
        section, field = entry["section"], entry["field"]
        value = getattr(effective, field)
        label = next(
            (o["label"] for o in entry["options"] if o["value"] == value), str(value),
        )
        # A plain star, not the pencil emoji settings_menu's own
        # "formatting" section title already carries (_SECTION_TITLES's
        # "✏️ Simple formatting") — reusing that glyph as an override
        # marker would make every row under that one section look
        # overridden even when it isn't.
        marker = " ⭐" if field in overrides else ""
        rows.append([InlineKeyboardButton(
            f"{entry['title']}: {label}{marker}",
            callback_data=f"{cb_prefix}:{section}",
        )])
    rows.append([InlineKeyboardButton("« Back", callback_data="_:spref")])
    text = (
        f"👤 <b>Per-session preferences — [{html_mod.escape(sess.label)}]</b>\n\n"
        "Overrides this session's reply style. Unset fields fall back to "
        "this chat's /settings. ⭐ marks a field this session has overridden."
    )
    return text, InlineKeyboardMarkup(rows)


def render_session_preferences_field(
    sess: TrackedSession, chat_id: int, section: str, *, cb_prefix: str,
) -> tuple[str, InlineKeyboardMarkup] | None:
    """A field's value list + "Use chat default" + Back, or ``None`` for
    an unrecognised ``section`` — same contract as
    ``settings_menu.render_settings_section``."""
    entry = _SCHEMA.get(section)
    if entry is None:
        return None
    field = entry["field"]
    overrides = sess.preference_overrides()
    overridden = field in overrides
    override_value = overrides.get(field)
    scope_value = getattr(get_preferences(sess.scope_chat_id or 0), field)

    rows = []
    for option in entry["options"]:
        value = option["value"]
        token = _token_for_value(value)
        marker = " ✅" if overridden and value == override_value else ""
        default_tag = " (chat default)" if value == scope_value else ""
        rows.append([InlineKeyboardButton(
            f"{option['label']}{default_tag}{marker}",
            callback_data=f"{cb_prefix}:{section}:{token}",
        )])
    rows.append([InlineKeyboardButton(
        "↩️ Use chat default" + ("" if overridden else " ✅"),
        callback_data=f"{cb_prefix}:{section}:default",
    )])
    rows.append([InlineKeyboardButton("« Back", callback_data=cb_prefix)])

    text = (
        f"{entry['title']} — for [<b>{html_mod.escape(sess.label)}</b>]\n\n"
        f"{entry['title']} this session uses, overriding this chat's own "
        "/settings just for it."
    )
    return text, InlineKeyboardMarkup(rows)


async def _handle_spref_callback(
    bot: "TelegramBot", query, chat_id: int, action: str,
) -> bool:
    """Dispatch for every ``_:spref...`` callback. ``action`` is the raw
    string after the leading ``_:`` sentinel — see the parts breakdown
    in the docstring of :func:`handle_callback`."""
    rest = action[len("spref"):]  # "" | ":<idx>" | ":<idx>:<section>" | ":<idx>:<section>:<token>"
    parts = [p for p in rest.split(":") if p != ""]

    if not parts:
        text, kb = _render_session_pref_picker(bot, chat_id)
        await _edit(query, text, kb)
        return True

    if len(parts) == 1:
        if parts[0] == "close":
            await _edit(query, "Closed.", None)
            return True
        sess = _resolve_pref_index(bot, chat_id, parts[0])
        if sess is None:
            await bot._safe_answer(
                query, "This session is no longer available — reopen /settings.",
            )
            return True
        text, kb = render_session_preferences_root(
            sess, chat_id, cb_prefix=f"_:spref:{parts[0]}",
        )
        await _edit(query, text, kb)
        return True

    if len(parts) == 2:
        idx_token, section = parts
        sess = _resolve_pref_index(bot, chat_id, idx_token)
        if sess is None:
            await bot._safe_answer(
                query, "This session is no longer available — reopen /settings.",
            )
            return True
        rendered = render_session_preferences_field(
            sess, chat_id, section, cb_prefix=f"_:spref:{idx_token}",
        )
        if rendered is None:
            await bot._safe_answer(query, "Invalid callback")
            return True
        text, kb = rendered
        await _edit(query, text, kb)
        return True

    if len(parts) == 3:
        idx_token, section, token = parts
        sess = _resolve_pref_index(bot, chat_id, idx_token)
        if sess is None:
            await bot._safe_answer(
                query, "This session is no longer available — reopen /settings.",
            )
            return True
        user_id = query.from_user.id if getattr(query, "from_user", None) else None
        if not bot._can_prompt_user(user_id, chat_id):
            await bot._safe_answer(query, "You can't change this session's preferences.")
            return True
        entry = _SCHEMA.get(section)
        if entry is None:
            await bot._safe_answer(query, "Invalid callback")
            return True
        field = entry["field"]
        attr = PREFERENCE_OVERRIDE_FIELDS[field]
        if token == "default":
            setattr(sess, attr, None)
            bot.registry.mark_dirty()
        else:
            value = _value_for_token(section, token)
            if value is None or not is_valid_value(field, value):
                await bot._safe_answer(query, "Invalid value")
                return True
            setattr(sess, attr, value)
            bot.registry.mark_dirty()
        rendered = render_session_preferences_field(
            sess, chat_id, section, cb_prefix=f"_:spref:{idx_token}",
        )
        if rendered is not None:
            text, kb = rendered
            await _edit(query, text, kb)
        return True

    await bot._safe_answer(query, "Invalid callback")
    return True


# ---- text capture (rename) --------------------------------------------

async def maybe_handle_text(
    bot: "TelegramBot", update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str,
) -> bool:
    """``True`` iff this text was consumed as a pending rename's new
    name. Called from ``_handle_message`` before every other branch —
    while a rename is pending in this chat, any text sent is treated as
    the candidate new name (Cancel is always one tap away via
    ``{name}:rename-cancel``)."""
    chat_id = calling_chat_id(update)
    if chat_id is None:
        return False
    pending = _rename_pending_map(bot).pop(chat_id, None)
    if pending is None:
        return False

    sess = bot.registry.get(pending["session_name"])
    if sess is None:
        await update.message.reply_text(
            "⚠️ That session is no longer available — run /rename again.",
        )
        return True

    user_id = update.effective_user.id if update.effective_user else None
    if not bot._can_prompt_user(user_id, chat_id):
        await update.message.reply_text("🚫 You can't rename this session.")
        return True

    await _apply_rename(bot, sess, text.strip(), chat_id, reply_target=update.message)
    return True


# ---- callback dispatch -------------------------------------------------

def session_cb(bot: "TelegramBot", chat_id: int, sess: TrackedSession,
               verb: str) -> str:
    """Build a session-scoped callback that always fits Telegram's cap.

    ``callback_data`` is limited to **64 bytes**. Embedding the internal
    session name blows that: names are themselves capped at 64 bytes by
    ``inject.launch_session``, so ``{name}:restart-confirm`` reached 68
    bytes for a realistic label plus its scope suffix — Telegram rejects
    the whole keyboard, so one long-named session would break every
    button in the message, not just its own.

    Indices come from the same stable per-chat table the preferences
    picker uses, so a button keeps working for as long as its session
    exists and fails closed afterwards.

    ``chat_id`` derivation asymmetry (review-1.md rev-iter1-003): every
    caller of this function computes ``chat_id`` as
    ``transport.resolve_chat_id_int(sess) or 0`` (the session's OWN
    stamped ``scope_chat_id``, since a keyboard builder is only ever
    handed ``sess`` — design.md rejected threading a new ``Update``
    parameter through nine signatures). Resolution
    (:func:`resolve_short_cb`, called from ``callbacks.py`` with the
    REAL inbound Update) instead uses ``transport.calling_chat_id(update)
    or 0`` — the chat the tap physically arrived from. These are two
    different functions computing what must be the same per-chat table
    key, and they are safe to differ only because they can never
    disagree in the one case that would matter: a genuine tap always
    arrives in the same chat the message (and therefore the button) was
    sent to, so ``calling_chat_id(update)`` at tap time equals whatever
    chat_id was live when this function registered the index at render
    time. The only way the two derivations could diverge is
    ``resolve_chat_id_int(sess)`` returning ``None`` -> ``0`` for an
    unstamped session — but that is also the case where
    ``bot.send_message(resolve_chat_id(sess), ...)`` would already fail
    to deliver the message the button lives in, so the mismatched-bucket
    case fails closed ("no longer available"), not open into another
    chat's table. Keep it this way (don't quietly make them the same
    function) unless you also thread a real ``Update``/chat id into
    every builder call site — see design.md's own reasoning for why that
    was rejected.
    """
    table = _register_pref_index(bot, chat_id, [sess.name])
    return f"_:sx:{table.index(sess.name)}:{verb}"


def resolve_short_cb(
    bot: "TelegramBot", chat_id: int, session_name: str, action: str,
) -> tuple[str, str] | None:
    """Resolve the indexed short form back to a real session, ONCE,
    centrally — ``callbacks.py._handle_callback`` calls this
    immediately after splitting ``cb_data``, before ``new_flow`` or
    :func:`handle_callback` see the pair, so every downstream branch
    (this module's own, and every ``callbacks.py`` branch below it)
    keeps working on an already-resolved ``(session_name, verb)`` pair
    exactly as it did before the short form existed.

    - not the short form (anything except ``session_name == "_"`` with
      ``action`` starting ``"sx:"``) → returns the pair UNCHANGED, so
      every other namespace (the long form, ``_:spref``, ``_:nw:...``,
      ``_:set...``, the ⋮-menu's own long-form verbs) passes through
      untouched.
    - short form, resolves → ``(resolved.name, verb)``.
    - short form, stale/malformed (out-of-range index, session gone,
      a truncated ``sx:`` token) → ``None``. The caller answers "That
      session is no longer available" and stops — it must never fall
      through to a stale or wrong session.
    """
    if session_name != "_" or not action.startswith("sx:"):
        return session_name, action
    parts = action.split(":", 2)
    if len(parts) != 3:
        return None
    resolved = _resolve_pref_index(bot, chat_id, parts[1])
    if resolved is None:
        return None
    return resolved.name, parts[2]


async def handle_callback(
    bot: "TelegramBot", update: Update, query, session_name: str, action: str,
) -> bool:
    """``True`` iff this callback was handled here — the caller
    (``callbacks.py``'s ``_handle_callback``) returns immediately in
    that case; ``False`` lets it fall through to its own existing
    branches (kill/stop/settings/etc.) or new_flow's wizard.

    PRECONDITION: ``session_name``/``action`` have already been through
    :func:`resolve_short_cb` by the time they reach here —
    ``callbacks.py._handle_callback`` does that ONCE, centrally, right
    after splitting ``cb_data`` and before either this function or
    ``new_flow.handle_callback`` is consulted. This function no longer
    decodes ``_:sx:<idx>:<verb>`` itself; a caller that skips the
    central resolution and hands this function a raw short-form pair
    will fall through to "Session not found" like any other unknown
    name, not silently misbehave.

    Two disjoint families:
    - ``_:spref...`` (session_name == "_") — per-session preferences,
      dispatched to :func:`_handle_spref_callback`.
    - ``{name}:<action>`` for every action in :data:`_SESSION_ACTIONS`.
    """
    chat_id = calling_chat_id(update)

    if session_name == "_" and action.startswith("spref"):
        return await _handle_spref_callback(bot, query, chat_id, action)

    # Sentinel namespaces are not sessions. `__voice__:restart` collides
    # with our own "restart" action verb, and claiming it broke the
    # "Restart daemon now" button that appears after installing the voice
    # extra — it answered "Session not found" instead. Decline anything
    # dunder-wrapped so a future sentinel cannot be swallowed either.
    if session_name.startswith("__") and session_name.endswith("__"):
        return False

    if action not in _SESSION_ACTIONS:
        return False

    sess = bot.registry.get(session_name)
    if sess is None:
        await bot._safe_answer(query, "Session not found")
        return True

    user_id = query.from_user.id if getattr(query, "from_user", None) else None

    if action == "menu":
        text, kb = _render_session_menu(bot, chat_id, sess)
        await _edit(query, text, kb)
        return True

    if action == "menu-close":
        await _edit(query, f"Closed menu for [<b>{html_mod.escape(sess.label)}</b>].", None)
        return True

    if action == "resume":
        # Authorization re-checked at TAP time, like every other verb here:
        # a gone session's menu can sit on screen for days.
        if not bot._can_prompt_user(user_id, chat_id):
            await bot._safe_answer(query, "You can't resume this session.")
            return True
        if sess.status != Status.GONE or not sess.claude_session_id:
            # State moved under the button — it was resumed elsewhere, or
            # came back to life. Re-render rather than fail into
            # `_do_resume_core`'s `not_gone`/`no_transcript`.
            text, kb = _render_session_menu(bot, chat_id, sess)
            await _edit(query, text, kb)
            await bot._safe_answer(query, "That session can't be resumed now.")
            return True
        # Same Ask/Auto step `/resume` uses, and the same keyboard builder
        # — one resume flow, reached from two places.
        bot._resume_mode_pending[sess.name] = sess.label
        await _edit(
            query, f"Resume <b>{html_mod.escape(sess.label)}</b> as:",
            bot._build_resume_mode_keyboard(
                sess, sess.skip_perms, chat_id=chat_id),
        )
        return True

    if action in ("resume-ask", "resume-auto", "resume-cancel"):
        if not bot._can_prompt_user(user_id, chat_id):
            await bot._safe_answer(query, "You can't resume this session.")
            return True
        bot._resume_mode_pending.pop(sess.name, None)
        if action == "resume-cancel":
            await _edit(query, "↩️ Cancelled.", None)
            return True

        async def _reply(text, **kw):
            await _edit(query, text, kw.pop("reply_markup", None))

        # `_do_resume` is the wrapper `/resume`'s own mode buttons call —
        # not a second resume implementation.
        await bot._do_resume(
            label=sess.label, reply_fn=_reply,
            skip_perms_override=(action == "resume-auto"),
        )
        return True

    if action == "restart":
        if not bot._can_prompt_user(user_id, chat_id):
            await bot._safe_answer(query, "You can't restart this session.")
            return True
        text, kb = _render_restart_confirm(bot, chat_id, sess)
        await _edit(query, text, kb)
        return True

    if action == "restart-confirm":
        if not bot._can_prompt_user(user_id, chat_id):
            await bot._safe_answer(query, "You can't restart this session.")
            return True
        await bot._safe_answer(query, f"Restarting {sess.label}...")
        outcome = await bot._restart_session_core(sess)
        await _edit(query, _restart_outcome_text(outcome), None)
        return True

    if action == "restart-cancel":
        await _edit(
            query, f"Restart cancelled for [<b>{html_mod.escape(sess.label)}</b>].", None,
        )
        return True

    if action == "rename":
        if not bot._can_prompt_user(user_id, chat_id):
            await bot._safe_answer(query, "You can't rename this session.")
            return True
        _start_rename_capture(bot, chat_id, sess)
        text = f"✏️ New name for [<b>{html_mod.escape(sess.label)}</b>]? Send it as a message."
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            "Cancel", callback_data=session_cb(bot, chat_id, sess, "rename-cancel"))]])
        await _edit(query, text, kb)
        return True

    if action == "rename-cancel":
        _rename_pending_map(bot).pop(chat_id, None)
        await _edit(query, "Rename cancelled.", None)
        return True

    if action == "delete":
        if not bot._can_prompt_user(user_id, chat_id):
            await bot._safe_answer(query, "You can't delete this session.")
            return True
        if sess.status != Status.GONE:
            await bot._safe_answer(
                query, "Session is still running. Use Kill to stop it first.",
            )
            return True
        text, kb = _render_delete_confirm(bot, chat_id, sess)
        await _edit(query, text, kb)
        return True

    if action == "delete-confirm":
        if not bot._can_prompt_user(user_id, chat_id):
            await bot._safe_answer(query, "You can't delete this session.")
            return True
        if sess.status != Status.GONE:
            await bot._safe_answer(
                query, "Session is still running. Use Kill to stop it first.",
            )
            return True
        label = sess.label
        # registry.remove() does NOT call mark_dirty() itself — must be
        # called explicitly, same footgun server.py:1233-1238 flags.
        bot.registry.remove(sess.name)
        bot.registry.mark_dirty()
        await _edit(query, f"🗑️ Deleted [<b>{html_mod.escape(label)}</b>].", None)
        return True

    if action == "delete-cancel":
        await _edit(
            query, f"Delete cancelled for [<b>{html_mod.escape(sess.label)}</b>].", None,
        )
        return True

    if action == "diff":
        await _run_diff(bot, sess, target_message=query.message)
        return True

    return False


__all__ = [
    "handle_callback",
    "handle_delete_cmd",
    "handle_diff_cmd",
    "handle_rename_cmd",
    "handle_restart_cmd",
    "maybe_handle_text",
    "render_session_preferences_field",
    "render_session_preferences_root",
]
