"""Shared fixtures for the "collapse busy card timeline" black-box
integration tests.

Independent of the Developer's own adapted unit tests
(``tests/test_stream_card.py``, ``tests/test_bot_animation.py``) — this
suite drives only the surface documented in
``.ship/collapse-busy-card-timeline/entrypoints.md``:
``build_stream_card``/``build_stream_card_ex``/``build_full_log`` in
``aipager/bot/animation.py``, ``TelegramBot._build_busy_text`` (the
legacy HTML path), ``AnimationMixin._edit_busy_rich``, and
``NotifyMixin._send_merged_final`` via the public ``bot.notify`` event
contract.

This directory's name (``collapse-busy-card-timeline``) is not a valid
Python identifier, so test modules here cannot ``from .conftest import
...`` — pytest still auto-discovers this file as a conftest regardless,
matching ``tests/integration/busy-card-agent-rows/conftest.py``'s own
documented convention (itself citing ``tests/integration/queue-handoff/
conftest.py`` and ``tests/integration/model-background-agent-jobs/
conftest.py`` as precedent).

No fixtures are defined here: every test in this directory either calls
the pure renderers directly (``build_stream_card_ex``/
``_build_busy_text``) or patches ``aipager.bot.rich_message._post``
itself at the exact HTTP-transport seam, matching
``tests/integration/stream_busy_message/test_layout_modes.py``'s own
convention — a directory-wide ``send_rich_message`` mock would silently
short-circuit that lower-level patch for tests that need to observe the
transport calls themselves (e.g. the `_send_merged_final` regression
checks). ``mk_bot``/``run_async`` come from the top-level
``tests/conftest.py`` fixtures, shared by the whole suite.
"""

from __future__ import annotations
