"""Telegram bot package.

Composed of mixin classes layered onto a façade. See ``core.TelegramBot``
for the assembly (currently still in ``aipager.telegram_bot``; moved
here in commit 6 of the restructure).

Submodules:

- ``transport`` — pure-function helpers: send-with-retry, truncation,
  diff rendering, API-error matching, "bot blocked" detection.
- (future) ``core``, ``handlers``, ``callbacks``, ``animation``,
  ``auth``, ``session_ops``, ``notify``, ``lifecycle``, ``keyboards``,
  ``dashboard``.

The re-export of ``TelegramBot`` happens in commit 6; doing it now
would create an import cycle (``telegram_bot`` imports from
``bot.transport``, which triggers ``bot/__init__`` evaluation).
"""
