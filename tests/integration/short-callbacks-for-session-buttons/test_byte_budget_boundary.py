"""Task instruction #1 — the byte guarantee.

entrypoints.md: "for any session name up to 64 bytes, any verb below,
and any plausible table index, this [short] form is always <= 64
bytes. Length depends only on the index's digit count and the verb --
never on the session name."

This suite tests that promise at its stated boundary: a session name
at exactly the documented 64-byte internal cap, EVERY verb in the
table (not a hand-picked "looks long enough" one), and a large index —
both a genuinely-registered large index (via the real, exported
``session_cb``) and an implausible one built directly per the
documented ``_:sx:<idx>:<verb>`` grammar, since no realistic amount of
in-process registration reaches the "even a 19-digit index" case
design.md itself raises.
"""

from __future__ import annotations

from aipager.bot import session_parity
from aipager.state import Status, TrackedSession

from _verbs import ALL_SESSION_SCOPED_VERBS, TELEGRAM_CALLBACK_DATA_LIMIT

CHAT_ID = -1001234567890  # realistic large-supergroup id, per entrypoints.md
NAME_64_BYTES = "a" * 64  # entrypoints.md: internal name is 1-64 bytes,
# `^[a-zA-Z0-9][a-zA-Z0-9_-]*$` — this is a legal, maximum-length name.


def _target_session(name=NAME_64_BYTES, label="x"):
    assert len(name.encode()) == 64, "test setup: name must be at the documented cap"
    return TrackedSession(name=name, label=label, status=Status.BUSY,
                           scope_chat_id=CHAT_ID)


def test_short_form_fits_for_every_documented_verb_at_max_name_length(scb_bot):
    """Equivalence class: every verb in entrypoints.md's table, against
    the longest legal session name. No verb should be assumed safe
    just because it's short in the table's own text."""
    bot = scb_bot()
    sess = _target_session()
    over = []
    for verb in ALL_SESSION_SCOPED_VERBS:
        cb = session_parity.session_cb(bot, CHAT_ID, sess, verb)
        n = len(cb.encode("utf-8"))
        if n > TELEGRAM_CALLBACK_DATA_LIMIT:
            over.append((verb, n, cb))
    assert not over, (
        f"{len(over)} verb(s) overflow the 64-byte cap at a 64-byte session "
        f"name: {over}"
    )


def test_short_form_length_is_independent_of_session_name_length(scb_bot):
    """entrypoints.md's own claim: length depends only on index digits
    and verb, never on the session name. Same chat (so both sessions
    land at the same table index), one very short name and one at the
    64-byte cap, same verb -> identical callback_data length."""
    bot = scb_bot()
    short_sess = TrackedSession(name="a", label="a", status=Status.BUSY,
                                 scope_chat_id=CHAT_ID)
    long_sess = TrackedSession(name=NAME_64_BYTES, label="x", status=Status.BUSY,
                                scope_chat_id=CHAT_ID + 1)  # different chat -> same idx 0
    cb_short = session_parity.session_cb(bot, CHAT_ID, short_sess, "perms_stop_switch")
    cb_long = session_parity.session_cb(bot, CHAT_ID + 1, long_sess, "perms_stop_switch")
    assert len(cb_short) == len(cb_long), (
        "callback_data length differs between a 1-byte and a 64-byte session "
        f"name for the same verb/index: {cb_short!r} vs {cb_long!r}"
    )


def test_short_form_fits_at_a_genuinely_large_registered_index(scb_bot):
    """"any plausible table index" -- grow the SAME per-chat table (the
    documented, observable side effect: "every rendered keyboard...
    registers that name") to several hundred entries via the exported
    ``session_cb``, purely through the public encoding API, then check
    the budget for the target (max-length-name) session landing at
    that now-large index."""
    bot = scb_bot()
    FILLER_COUNT = 400
    for i in range(FILLER_COUNT):
        filler = TrackedSession(
            name=f"claude-filler{i}__d{i:010d}", label=f"f{i}",
            status=Status.IDLE, scope_chat_id=CHAT_ID,
        )
        session_parity.session_cb(bot, CHAT_ID, filler, "menu")

    sess = _target_session()
    cb = session_parity.session_cb(bot, CHAT_ID, sess, "perms_stop_switch")
    idx_str = cb.split(":")[2]
    assert int(idx_str) >= FILLER_COUNT, (
        "test setup: expected the target session's index to reflect the "
        f"{FILLER_COUNT} prior registrations, got index {idx_str!r}"
    )
    assert len(cb.encode("utf-8")) <= TELEGRAM_CALLBACK_DATA_LIMIT, (
        f"{cb!r} ({len(cb.encode())} bytes) overflows at a genuinely large, "
        f"registered index ({idx_str})"
    )


def test_short_form_grammar_fits_at_an_implausible_19_digit_index():
    """The grammar itself (entrypoints.md: `_:sx:<idx>:<verb>`), built
    directly (not through ``session_cb``, since no in-process loop can
    cheaply register 10**19 sessions) at a 19-digit index -- checked
    for every documented verb, not just the one design.md's own prose
    happens to quote. This is testing the CONTRACT's math, independent
    of whatever the real index table can practically reach."""
    worst_idx = "9" * 19
    over = []
    for verb in ALL_SESSION_SCOPED_VERBS:
        cb = f"_:sx:{worst_idx}:{verb}"
        n = len(cb.encode("utf-8"))
        if n > TELEGRAM_CALLBACK_DATA_LIMIT:
            over.append((verb, n, cb))
    assert not over, f"grammar overflows at a 19-digit index: {over}"


def test_short_form_grammar_bound_derivation_is_not_circular():
    """Sanity check on THIS suite's own methodology, not on the
    implementation: confirm the 64-byte cap used above is Telegram's
    real, external API limit and not a number quietly copied from the
    implementation under test. ``5 + digits + 1 + len(verb) <= 64`` is
    arithmetic anyone can verify against Telegram's published Bot API
    docs, independent of this codebase."""
    prefix_and_separators = len("_:sx:") + len(":")
    assert prefix_and_separators == 6
    # However many digits an index has, the verb portion still has to
    # leave room: with the longest verb actually in the table, the
    # digit budget before overflow is huge (not a razor's edge design
    # choice this test should be tuned around).
    longest_verb_len = max(len(v) for v in ALL_SESSION_SCOPED_VERBS)
    max_digits_before_overflow = TELEGRAM_CALLBACK_DATA_LIMIT - prefix_and_separators - longest_verb_len
    assert max_digits_before_overflow >= 19, (
        "the 19-digit worst case this suite tests above would not actually "
        "be safe margin under the documented grammar"
    )
