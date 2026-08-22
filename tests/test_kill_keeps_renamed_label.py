"""Killing a session must not undo its rename.

Reproduced live: rename a session to `shortname`, kill it, and it returns
to the gone list under its ORIGINAL name — resume by the new name 404s
while the old one works.

`inject.kill_session` SIGTERMs the dtach pids and then unlinks the socket.
The monitor's 2s scan can land in that window, still see the socket, and
call `get_or_create()` for a name whose entry the kill just removed. With
no entry present the label is derived afresh from the internal NAME, and
the rename is gone.

The kill contract is unchanged — kill still removes the entry, and
`delete` is still the action that forgets a session. What changed is that
a kill leaves the chosen label behind so the re-creation can restore it.
"""

from __future__ import annotations

import types
from unittest.mock import AsyncMock

from aipager.state import Status

INTERNAL = "claude-original_long_name__d100"


def _clock_at(when: float):
    """A stand-in for the `time` module pinned to `when`.

    Carries `time` and `sleep` as well as `monotonic`, matching
    `conftest.steady_clock`: a namespace with only the one attribute
    happens to work while nothing on the path calls the others, and
    breaks confusingly the day something does.
    """
    import time as _real

    return types.SimpleNamespace(
        monotonic=lambda: when, time=_real.time, sleep=_real.sleep)


def _renamed_then_killed(bot, run_async, monkeypatch, new_label="shortname"):
    sess = bot.registry.get_or_create(INTERNAL)
    sess.label = new_label                       # what /rename does
    sess.claude_session_id = "uuid-keepme"
    bot.registry.transition(INTERNAL, Status.IDLE)
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=False))
    bot._update_bot_commands = AsyncMock()
    run_async(bot._kill_session_core(INTERNAL, new_label))


def test_a_killed_session_returns_under_the_name_you_gave_it(
        mk_bot, run_async, monkeypatch):
    """The reported defect: the gone list showed a name the user never
    picked."""
    bot = mk_bot()
    _renamed_then_killed(bot, run_async, monkeypatch)

    # The monitor's next scan still sees the socket and re-creates the entry.
    revived = bot.registry.get_or_create(INTERNAL)

    assert revived.label == "shortname", (
        f"the rename was discarded — label came back as {revived.label!r}")


def test_a_killed_session_is_findable_by_its_new_name(mk_bot, run_async,
                                                      monkeypatch):
    """Resume by the renamed label 404'd because the re-created entry
    carried the derived name instead."""
    bot = mk_bot()
    _renamed_then_killed(bot, run_async, monkeypatch)
    bot.registry.get_or_create(INTERNAL)

    assert bot.registry.find_by_label("shortname", None, include_gone=True), (
        "the killed session cannot be found by the name the user chose")


def test_kill_still_removes_the_entry(mk_bot, run_async, monkeypatch):
    """The contract this fix must NOT change. `delete` forgets a session;
    kill ends the process, and a dozen existing tests pin that killing
    drops the registry entry (`..._subsequent_get_404s`,
    `..._is_not_found_not_not_gone`). Remembering the label is a side
    table, not a change to that rule."""
    bot = mk_bot()
    _renamed_then_killed(bot, run_async, monkeypatch)

    assert bot.registry.get(INTERNAL) is None


def test_a_session_killed_without_a_rename_derives_its_name_as_before(
        mk_bot, run_async, monkeypatch):
    """No change for the ordinary case: the remembered label equals what
    derivation would have produced anyway."""
    bot = mk_bot()
    name = "claude-plain__d100"
    sess = bot.registry.get_or_create(name)
    assert sess.label == "plain"                 # derived on first sight
    bot.registry.transition(name, Status.IDLE)
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=False))
    bot._update_bot_commands = AsyncMock()
    run_async(bot._kill_session_core(name, "plain"))

    assert bot.registry.get_or_create(name).label == "plain"


def test_delete_does_not_leave_a_label_behind(mk_bot):
    """`delete` is the explicit forget. A remembered label would later
    attach itself to a different session that reused the internal name."""
    bot = mk_bot()
    sess = bot.registry.get_or_create(INTERNAL)
    sess.label = "shortname"

    bot.registry.remove(INTERNAL)                # the delete path

    assert bot.registry.get_or_create(INTERNAL).label == "original_long_name", (
        "a deleted session's label was resurrected onto a new entry")


def test_the_remembered_label_is_consumed_once(mk_bot):
    """It restores the entry that raced the kill, then stops applying —
    otherwise a genuinely new session reusing the name inherits it."""
    bot = mk_bot()
    sess = bot.registry.get_or_create(INTERNAL)
    sess.label = "shortname"
    bot.registry.remove(INTERNAL, remember_label=True)

    assert bot.registry.get_or_create(INTERNAL).label == "shortname"
    bot.registry.remove(INTERNAL)                # forget it again
    assert bot.registry.get_or_create(INTERNAL).label == "original_long_name"


def test_the_label_memory_cannot_grow_with_uptime(mk_bot):
    """Nothing prunes this table by time, so it has to be bounded by size."""
    from aipager.state import _MAX_REMEMBERED_LABELS

    bot = mk_bot()
    for i in range(_MAX_REMEMBERED_LABELS * 3):
        n = f"claude-s{i}__d100"
        s = bot.registry.get_or_create(n)
        s.label = f"renamed{i}"
        bot.registry.remove(n, remember_label=True)

    assert len(bot.registry._remembered_labels) <= _MAX_REMEMBERED_LABELS


# ---- the leak a reviewer found in the first version of this fix ---------

def test_an_unrenamed_session_leaves_nothing_to_remember(mk_bot, run_async,
                                                         monkeypatch):
    """Nothing is stored when the label matches what derivation gives.

    This also rescues the test above it from being a coincidence: for an
    unrenamed session the remembered and derived labels are the SAME
    string, so asserting the label survives proves nothing about which
    one produced it. Asserting the table stays empty does.
    """
    bot = mk_bot()
    name = "claude-plain__d100"
    bot.registry.get_or_create(name)
    bot.registry.transition(name, Status.IDLE)
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=False))
    bot._update_bot_commands = AsyncMock()

    run_async(bot._kill_session_core(name, "plain"))

    assert bot.registry._remembered_labels == {}, (
        "stored a label identical to the derived one — pure leak surface")


def test_a_remembered_label_expires_and_cannot_hijack_a_later_session(
        mk_bot, monkeypatch):
    """The reviewer's finding: the memory was consumed once but never
    expired, so a kill whose re-creation never happened left the name
    lying in wait. A genuinely different session reusing that internal
    dtach name later inherited it.

    Patches the module's own `time` reference, the technique
    `conftest.steady_clock` uses — never the global clock, which asyncio
    reads for every timer.
    """
    from aipager import state as state_mod

    bot = mk_bot()
    name = "claude-shared__d100"
    sess = bot.registry.get_or_create(name)
    sess.label = "my_important_work"
    bot.registry.remove(name, remember_label=True)
    assert bot.registry._remembered_labels, "nothing was remembered to expire"

    # Far past the TTL — a different session now reuses the internal name.
    monkeypatch.setattr(state_mod, "time", _clock_at(
        state_mod.time.monotonic() + state_mod.REMEMBERED_LABEL_TTL_SECONDS + 1))

    later = bot.registry.get_or_create(name)

    assert later.label == "shared", (
        f"a dead session's name hijacked a later one: {later.label!r}")


def test_a_remembered_label_still_survives_the_race_it_exists_for(
        mk_bot, monkeypatch):
    """The TTL must not be so eager it breaks the actual fix: a
    re-creation moments after the kill still recovers the name."""
    from aipager import state as state_mod

    bot = mk_bot()
    name = "claude-shared2__d100"
    sess = bot.registry.get_or_create(name)
    sess.label = "my_important_work"
    bot.registry.remove(name, remember_label=True)

    # the real race is ~2s
    monkeypatch.setattr(state_mod, "time", _clock_at(
        state_mod.time.monotonic() + 2.0))

    assert bot.registry.get_or_create(name).label == "my_important_work"
