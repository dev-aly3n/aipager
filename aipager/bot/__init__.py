"""Telegram bot package.

Composed of mixin classes layered onto a façade in ``core.TelegramBot``.

    from aipager.bot import TelegramBot

Submodules:

- ``core`` — façade class; owns ``__init__`` and the mixin composition.
- ``handlers`` — ``CommandHandlersMixin`` (every ``/foo`` command +
  ``_handle_message`` + ``_handle_voice`` + ``_handle_file``).
- ``callbacks`` — ``CallbackDispatchMixin`` (the inline-button switchboard).
- ``animation`` — ``AnimationMixin`` (busy-message edit loop, spinner).
- ``auth`` — ``AuthMixin`` (team-mode allow-list, role checks, driver attribution).
- ``session_ops`` — ``SessionOpsMixin`` (stop / kill / switch / resume).
- ``notify`` — ``NotifyMixin`` (hook-receiver event dispatcher).
- ``lifecycle`` — ``LifecycleMixin`` (start / stop / recover_sessions / reload_team).
- ``keyboards`` — ``KeyboardMixin`` (inline + reply keyboard builders).
- ``dashboard`` — ``DashboardMixin`` (pinned text, session dashboard, resume picker).
- ``transport`` — pure-function helpers (send-with-retry, truncation,
  diff rendering, API-error matching, "bot blocked" detection). No mixin
  here — these are stateless and called by methods on the façade.
"""

from aipager.bot.core import TelegramBot

__all__ = ["TelegramBot"]
