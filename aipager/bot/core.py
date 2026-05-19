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

from telegram.ext import Application

from aipager.bot.animation import AnimationMixin
from aipager.bot.auth import AuthMixin
from aipager.bot.callbacks import CallbackDispatchMixin
from aipager.bot.dashboard import DashboardMixin
from aipager.bot.handlers import CommandHandlersMixin
from aipager.bot.keyboards import KeyboardMixin
from aipager.bot.lifecycle import LifecycleMixin
from aipager.bot.notify import NotifyMixin
from aipager.bot.session_ops import SessionOpsMixin
from aipager.config import MODEL_CHOICES, QUICK_COMMANDS, QUICK_TEMPLATES
from aipager.state import SessionRegistry
from aipager.team import Team


class TelegramBot(
    LifecycleMixin,
    AuthMixin,
    SessionOpsMixin,
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
        self.observers = None  # ObserverBroadcaster | None, injected by __main__
        self._registered_labels: set[str] | None = None  # None = never synced this run
        self._keyboard_level: str = "main"  # "main", "templates", "commands", "models"
        self._template_map: dict[str, str] = {label: prompt for label, prompt in QUICK_TEMPLATES}
        self._command_map: dict[str, str] = {label: cmd for label, cmd in QUICK_COMMANDS}
        self._model_map: dict[str, str] = {label: cmd for label, cmd in MODEL_CHOICES}
        self._last_pinned_text: str = ""  # dedup pinned message edits
        # `/new <name>` collision state. Keyed by session_name; value is
        # {"prompt": str, "skip_perms": bool, "user_id": int, "msg_id": int}.
        # Populated when /new hits an existing name, drained when the user
        # taps Resume / Replace / Cancel. Multiple users colliding on the
        # same name race-overwrite — acceptable for a v1 single-admin tool.
        self._new_conflict_pending: dict[str, dict] = {}
        # Team / allow-list — None for personal-mode installs (no team.yaml),
        # which preserves the existing one-user-one-DM behaviour.
        from aipager.config import TEAM
        self.team: Team | None = TEAM
