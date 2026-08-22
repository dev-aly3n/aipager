"""design.md success criteria: "`_make_cb` no longer exists anywhere in
`aipager/`."

entrypoints.md's "NOT exported" section explicitly sanctions this
check from a black-box position: "`keyboards.KeyboardMixin._make_cb` --
deleted by this ship. A missing-attribute test is fine; do not assert
an `AssertionError` from it." -- i.e. test that the assert-raising
overflow guard is simply gone, not that it still fires.
"""

from __future__ import annotations

from aipager.bot import keyboards


def test_make_cb_attribute_no_longer_exists_on_the_keyboard_mixin():
    assert not hasattr(keyboards.KeyboardMixin, "_make_cb"), (
        "keyboards.KeyboardMixin._make_cb should have been deleted by this "
        "ship (design.md success criteria), but the attribute still exists"
    )
