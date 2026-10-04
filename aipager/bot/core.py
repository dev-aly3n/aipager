"""TelegramBot façade — composed from feature mixins.

Single owner of all Telegram communication. Handles:

- CallbackQuery (button taps) → ``aipager.dtach.inject.send_keys()``
- Message replies → ``aipager.dtach.inject.send_text_and_enter()``
- ``/status`` and friends → show / mutate session state
- ``/<label> <prompt>`` → direct prompt injection

Method bodies live in mixin classes (see :mod:`aipager.bot` overview).
The façade keeps the ``__init__`` (instance state) and the mixin
composition only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram.ext import Application

from aipager.bot import reactions
from aipager.bot.animation import AnimationMixin
from aipager.bot.auth import AuthMixin
from aipager.bot.callbacks import CallbackDispatchMixin
from aipager.bot.dashboard import DashboardMixin
from aipager.bot.handlers import CommandHandlersMixin
from aipager.bot.keyboards import KeyboardMixin
from aipager.bot.lifecycle import LifecycleMixin
from aipager.bot.notify import NotifyMixin
from aipager.bot.send_now import SendNowMixin
from aipager.bot.session_ops import SessionOpsMixin
from aipager.config import MODEL_CHOICES, QUICK_COMMANDS, QUICK_TEMPLATES
from aipager.state import SessionRegistry
from aipager.team import Team

if TYPE_CHECKING:
    from aipager.bot.handlers import _PendingAlbum


class TelegramBot(
    LifecycleMixin,
    AuthMixin,
    SessionOpsMixin,
    SendNowMixin,
    CommandHandlersMixin,
    CallbackDispatchMixin,
    AnimationMixin,
    NotifyMixin,
    KeyboardMixin,
    DashboardMixin,
):
    """Telegram bot façade — composed from feature mixins."""

    def __init__(self, registry: SessionRegistry):
        self.registry = registry
        self._app: Application | None = None
        # Self-update (`/update` + the Mini App's Updates block): at most one
        # job at a time, shared by both surfaces. See bot/update_flow.py.
        from aipager.bot.update_flow import UpdateManager
        self.updates = UpdateManager(self)
        self.observers = None  # ObserverBroadcaster | None, injected by __main__
        self._registered_labels: set[str] | None = None  # None = never synced this run
        # Multi-scope: per-chat command-list state (chat_id → last labels set)
        # so we only re-register a scope's `/menu` when its labels change.
        self._registered_scope_labels: dict[int, set[str]] = {}
        # The message handlers' chat gate (``filters.Chat``), set by
        # ``start`` and updated in place by a live reload (roadmap 8.80).
        self._message_chat_gate = None
        # The start-time lookup of each group scope (roadmap 8.87), run in
        # the background so start() never waits on a group's budget.
        self._group_probe_task = None
        # The keyboard level ("main", "templates", "commands", "models")
        # each chat last got, and in a group each member (roadmap 8.91b):
        # see ``KeyboardMixin._keyboard_level_key``. No entry is "main".
        self._keyboard_levels: dict = {}
        # (chat, user) pairs a read_only member was already told their
        # role in, this daemon run (roadmap 8.91a): later refusals are
        # silent.
        self._read_only_told: set[tuple] = set()
        # The Mini App's public URL for this daemon run. "" = no Mini
        # App, which is what the keyboard checks before offering its
        # launch button. Written twice at startup, in this order:
        # prime_miniapp_url() before start() (so the FIRST keyboard
        # already carries the button), then publish_miniapp_button()
        # once the Mini App server is confirmed listening — which
        # re-clears this if it turned out not to be.
        self._miniapp_url: str = ""
        # Hold the very first keyboard until the Mini App URL is known —
        # see lifecycle.defer_first_keyboard.
        self._keyboard_deferred: bool = False
        # Main keyboards a flood mute refused (roadmap 8.17c), per target
        # chat: normalised chat key -> the ``chat_id`` argument to re-send
        # with. Owed until a send succeeds; paid by
        # ``flush_owed_keyboards`` on the session monitor's tick once the
        # chat's mute has lifted and its held answers are out. A separate
        # debt from ``_keyboard_deferred`` (the Mini App URL wait), which
        # a lift must NOT release early on its own. When a keyboard is
        # ALSO owed, paying it clears the URL hold too — correctly: that
        # keyboard was already sent (into the mute) after the hold ended.
        self._keyboard_owed: dict[int | str, int | None] = {}
        self._template_map: dict[str, str] = {label: prompt for label, prompt in QUICK_TEMPLATES}
        self._command_map: dict[str, str] = {label: cmd for label, cmd in QUICK_COMMANDS}
        self._model_map: dict[str, str] = {label: cmd for label, cmd in MODEL_CHOICES}
        # The pinned "needs you" bar's per-chat state (8.31): what each
        # chat's bar last showed, when it was last attempted, its pending
        # trailing refresh. See `dashboard.PinnedChat`.
        self._pinned: dict = {}
        # The running all-chats refresh started by `pinned_tick`, if any.
        self._pinned_task = None
        # (chat_id, message_id) → (session name, prompt token) for every
        # message carrying answer buttons outside the busy card: a
        # separate-message prompt, or a copy the pinned bar's "Answer"
        # button re-sent (8.31). A tap on one whose prompt is no longer the
        # one pending is refused. See `register_prompt_surface`.
        self._resent_prompts: dict[tuple[int, int], tuple[str, int]] = {}
        # Last reaction per user message (aipager.bot.reactions): keeps each
        # message's lifecycle monotonic and its calls to at most three.
        self._reaction_ledger = reactions.ReactionLedger()
        # `/new <name>` collision state. Keyed by session_name; value is
        # {"prompt": str, "skip_perms": bool, "user_id": int, "msg_id": int}.
        # Populated when /new hits an existing name, drained when the user
        # taps Resume / Replace / Cancel. Multiple users colliding on the
        # same name race-overwrite — acceptable for a v1 single-admin tool.
        self._new_conflict_pending: dict[str, dict] = {}
        # `/perms` pending state. Keyed by session_name; value is
        # {"target_skip_perms": bool, "msg_id": int, "label": str}.
        self._perms_pending: dict[str, dict] = {}
        # `/resume` mode-picker pending state. Keyed by session_name → label.
        self._resume_mode_pending: dict[str, str] = {}
        # In-flight Telegram albums (media groups), keyed by
        # (chat_id, media_group_id). Items are parked here by
        # handlers._handle_file and injected as ONE prompt once the group
        # settles; see handlers._flush_album.
        self._albums: dict[tuple[int, str], _PendingAlbum] = {}
        # Team / allow-list — None for personal-mode installs (no team.yaml),
        # which preserves the existing one-user-one-DM behaviour.
        from aipager.config import TEAM
        self.team: Team | None = TEAM
        # v2 multi-scope config (Phase C: authoritative for authorization
        # when present). Loaded FRESH here — not via config.SCOPES — because
        # migrate_to_v2() writes aipager.yaml at daemon start, *after*
        # config was imported, so config.SCOPES would be stale (None) on the
        # first post-upgrade run. When self.scopes is None the auth methods
        # fall back to the legacy team/personal model.
        from aipager.policy import load_policy
        from aipager.scope import load_scopes
        _v2 = load_scopes()
        self.scopes = _v2[0] if _v2 else None
        self.policy = load_policy()
        # The home chat of an unstamped session (roadmap 8.82) reads this
        # bot's live scopes/policy, which reload_team swaps in place.
        from aipager.state import set_live_scope_source
        set_live_scope_source(self)
        # Whose card each group message was, as the registry saved it
        # (roadmap 8.94h): none for a chat that is no longer a group scope.
        from aipager.bot import card_owner
        card_owner.prune(self)
