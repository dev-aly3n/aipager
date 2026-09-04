"""Shared helpers for the black-box "collapse busy card timeline" suite.

Independent of both the Developer's own adapted unit tests
(``tests/test_stream_card.py``, ``tests/test_bot_animation.py``) AND of
the other black-box integration directory the Developer already created
(``tests/integration/collapse-busy-card-timeline/``) — this directory is
the Tester's own, written against ``.ship/collapse-busy-card-timeline/
design.md``'s "Rules" and "Verified" sections and ``entrypoints.md``'s
public contract only, without reading ``implementation.md`` or any
non-entry-point source.

This directory's name is not a valid Python identifier, so test modules
here cannot ``from .conftest import ...`` — pytest still auto-discovers
this file regardless, matching every other hyphenated directory under
``tests/integration/``'s own documented convention. No fixtures are
defined here (there is no shared fixture per entrypoints.md); each test
module builds its own local ``TrackedSession`` by hand and calls the
exported renderers directly. ``mk_bot``/``run_async`` (used only by the
legacy-guard and fallback-degrade tests) come from the top-level
``tests/conftest.py``.
"""

from __future__ import annotations
