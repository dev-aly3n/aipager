"""design.md success criteria "Store lookup": ``plan_key`` is the stored key
of a sign-in, ``plan_windows`` returns only that key's newest reading."""

from __future__ import annotations

from aipager import usage_store
from aipager.claude_bootstrap import SessionLogin
from aipager.statusline_file import Window

from ._kit import DAY, LOGIN_A, LOGIN_B, NOW, OWNER, UNKNOWN

FAR = int(NOW) + 30 * DAY


def _store(login, pct, *, now=NOW, session="frontend"):
    usage_store.note_plan(session, {"seven_day": Window(pct, FAR)}, login, scope=OWNER,
                          now=now)


def test_two_sign_ins_have_different_keys():
    assert usage_store.plan_key(LOGIN_A) != usage_store.plan_key(LOGIN_B)


def test_plan_windows_by_own_key():
    _store(LOGIN_A, 50, now=NOW - 60)
    _store(LOGIN_B, 99, now=NOW, session="backend")
    assert usage_store.plan_windows(usage_store.plan_key(LOGIN_A), now=NOW) == {
        "seven_day": Window(50, FAR)}


def test_plan_windows_other_key_gets_its_own():
    _store(LOGIN_A, 50, now=NOW - 60)
    _store(LOGIN_B, 99, now=NOW, session="backend")
    assert usage_store.plan_windows(usage_store.plan_key(LOGIN_B), now=NOW) == {
        "seven_day": Window(99, FAR)}


def test_plan_windows_newest_reading_of_the_key_wins():
    _store(LOGIN_A, 50, now=NOW - 600)
    _store(LOGIN_A, 93, now=NOW - 60)
    assert usage_store.plan_windows(usage_store.plan_key(LOGIN_A), now=NOW) == {
        "seven_day": Window(93, FAR)}


def test_plan_windows_key_without_reading_is_none():
    _store(LOGIN_B, 99)
    assert usage_store.plan_windows(usage_store.plan_key(LOGIN_A), now=NOW) is None


def test_plan_windows_empty_key_is_none():
    _store(UNKNOWN, 99)
    assert usage_store.plan_windows("", now=NOW) is None


def test_plan_windows_unknown_key_is_none_even_with_unknown_reading():
    _store(UNKNOWN, 99)
    assert usage_store.plan_windows("unknown", now=NOW) is None


def test_plan_windows_unknown_login_key_is_none():
    _store(UNKNOWN, 99)
    assert usage_store.plan_windows(usage_store.plan_key(UNKNOWN), now=NOW) is None


def test_plan_windows_garbage_key_is_none():
    _store(LOGIN_A, 99)
    assert usage_store.plan_windows("no-such-key|x|y", now=NOW) is None


def test_plan_windows_empty_store_is_none():
    assert usage_store.plan_windows(usage_store.plan_key(LOGIN_A), now=NOW) is None


def test_plan_windows_drops_stale_reading():
    _store(LOGIN_A, 99, now=NOW - 9 * DAY)
    assert usage_store.plan_windows(usage_store.plan_key(LOGIN_A), now=NOW) is None


def test_plan_windows_just_over_8_days_is_dropped():
    _store(LOGIN_A, 99, now=NOW - 8 * DAY - 60)
    assert usage_store.plan_windows(usage_store.plan_key(LOGIN_A), now=NOW) is None


def test_plan_windows_just_under_8_days_is_kept():
    _store(LOGIN_A, 99, now=NOW - 8 * DAY + 60)
    assert usage_store.plan_windows(usage_store.plan_key(LOGIN_A), now=NOW) is not None


def test_plan_windows_returns_a_copy():
    _store(LOGIN_A, 99)
    key = usage_store.plan_key(LOGIN_A)
    usage_store.plan_windows(key, now=NOW).clear()
    assert usage_store.plan_windows(key, now=NOW) == {"seven_day": Window(99, FAR)}


def test_plan_key_matches_stored_key_for_long_config_home():
    login = SessionLogin("saved", "", "/home/alice/" + "x" * 1100)
    _store(login, 97)
    assert usage_store.plan_windows(usage_store.plan_key(login), now=NOW) == {
        "seven_day": Window(97, FAR)}


def test_plan_key_long_config_home_differs_from_short_one():
    login = SessionLogin("saved", "", "/home/alice/" + "x" * 1100)
    assert usage_store.plan_key(login) != usage_store.plan_key(LOGIN_A)


def test_plan_key_matches_stored_key_for_gateway():
    login = SessionLogin("gateway", "gw.acme.example", "/home/alice/.claude")
    _store(login, 97)
    assert usage_store.plan_windows(usage_store.plan_key(login), now=NOW) == {
        "seven_day": Window(97, FAR)}


def test_plan_windows_survives_a_restart():
    """Error guessing: the stored key is the same after usage.json reloads."""
    _store(LOGIN_A, 96)
    usage_store.save_if_dirty(force=True)
    usage_store.reset_for_tests()
    assert usage_store.plan_windows(usage_store.plan_key(LOGIN_A), now=NOW) == {
        "seven_day": Window(96, FAR)}
