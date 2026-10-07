"""The short session buttons name the same session across a restart
(roadmap 8.103).

``_:sx:<idx>:<verb>`` and ``_:spref:<idx>`` resolve ``<idx>`` through a
per-chat positional table. It used to live only in memory, so a restart
handed indices out again in render order and a button on an old message
(Stop on an old card, a menu) could name another session of the chat.
"""

from __future__ import annotations

import json
import os

import pytest

from aipager import state
from aipager.bot import session_parity
from aipager.state import SessionRegistry, Status, TrackedSession

CHAT = 555


def _add(reg: SessionRegistry, name: str) -> TrackedSession:
    sess = TrackedSession(name=name, label=name.removeprefix("claude-"),
                          status=Status.IDLE)
    sess.scope_chat_id = CHAT
    reg._sessions[name] = sess
    return sess


def _idx(token: str) -> str:
    """``_:sx:<idx>:<verb>`` -> ``<idx>``."""
    return token.split(":")[2]


def _restart(mk_bot) -> tuple[SessionRegistry, object]:
    reg = SessionRegistry()
    reg.load()
    return reg, mk_bot(reg)


def test_an_old_button_names_its_own_session_after_a_restart(mk_bot):
    reg = SessionRegistry()
    alpha, bravo = _add(reg, "claude-alpha"), _add(reg, "claude-bravo")
    bot = mk_bot(reg)
    old_stop = session_parity.session_cb(bot, CHAT, alpha, "stop")
    session_parity.session_cb(bot, CHAT, bravo, "stop")
    reg.save()

    reg2, bot2 = _restart(mk_bot)
    # The other session's card is the first to render after the restart.
    session_parity.session_cb(bot2, CHAT, reg2.get("claude-bravo"), "stop")
    resolved = session_parity.resolve_short_cb(
        bot2, CHAT, "_", old_stop.removeprefix("_:"))
    assert resolved == ("claude-alpha", "stop")


def test_a_session_gone_by_the_restart_keeps_its_slot_empty(mk_bot):
    reg = SessionRegistry()
    alpha, bravo = _add(reg, "claude-alpha"), _add(reg, "claude-bravo")
    bot = mk_bot(reg)
    alpha_stop = session_parity.session_cb(bot, CHAT, alpha, "stop")
    bravo_stop = session_parity.session_cb(bot, CHAT, bravo, "stop")
    reg.save()
    data = json.loads(state.SESSION_STATE_FILE.read_text())
    del data["sessions"]["claude-alpha"]  # aged out while the daemon was down
    state.SESSION_STATE_FILE.write_text(json.dumps(data))

    reg2, bot2 = _restart(mk_bot)
    assert reg2.button_index[CHAT] == [None, "claude-bravo"]
    charlie = _add(reg2, "claude-charlie")
    charlie_stop = session_parity.session_cb(bot2, CHAT, charlie, "stop")
    # The new session takes a new slot, never the gone one's.
    assert _idx(charlie_stop) not in (_idx(alpha_stop), _idx(bravo_stop))
    assert session_parity.resolve_short_cb(
        bot2, CHAT, "_", alpha_stop.removeprefix("_:")) is None
    assert session_parity.resolve_short_cb(
        bot2, CHAT, "_", bravo_stop.removeprefix("_:")) == ("claude-bravo", "stop")


def test_a_state_file_without_tables_starts_where_no_old_button_points(mk_bot):
    """A file from before 8.103: the old indices are unknown, so the new
    table counts from far above them and an old button fails closed."""
    reg = SessionRegistry()
    _add(reg, "claude-alpha")
    reg.save()
    data = json.loads(state.SESSION_STATE_FILE.read_text())
    del data["button_index"]
    state.SESSION_STATE_FILE.write_text(json.dumps(data))

    reg2, bot2 = _restart(mk_bot)
    assert reg2.button_tables_lost is True
    token = session_parity.session_cb(bot2, CHAT, reg2.get("claude-alpha"), "stop")
    assert int(_idx(token)) >= session_parity._LOST_TABLE_BASE_MIN
    assert session_parity.resolve_short_cb(bot2, CHAT, "_", "sx:0:stop") is None
    assert session_parity.resolve_short_cb(
        bot2, CHAT, "_", token.removeprefix("_:")) == ("claude-alpha", "stop")
    # And that base is kept, so its own buttons survive the next restart.
    reg2.save()
    reg3, bot3 = _restart(mk_bot)
    assert reg3.button_tables_lost is False
    assert session_parity.resolve_short_cb(
        bot3, CHAT, "_", token.removeprefix("_:")) == ("claude-alpha", "stop")


def test_no_state_file_also_starts_where_no_old_button_points(mk_bot):
    """A first start looks like a deleted state file (a reinstall, a new
    machine on the same bot), and only the second has old cards in the
    chat: both count as lost."""
    reg, bot = _restart(mk_bot)  # no state file at all
    assert reg.button_tables_lost is True
    token = session_parity.session_cb(bot, CHAT, _add(reg, "claude-alpha"), "stop")
    assert int(_idx(token)) >= session_parity._LOST_TABLE_BASE_MIN
    assert session_parity.resolve_short_cb(bot, CHAT, "_", "sx:0:stop") is None


def test_a_removed_sessions_buttons_never_name_its_namesake(mk_bot):
    """Within one run too: a new session that reuses a removed one's
    internal name gets a slot of its own."""
    reg = SessionRegistry()
    bot = mk_bot(reg)
    old_stop = session_parity.session_cb(bot, CHAT, _add(reg, "claude-alpha"), "stop")
    reg.remove("claude-alpha")
    new_stop = session_parity.session_cb(bot, CHAT, _add(reg, "claude-alpha"), "stop")
    assert _idx(new_stop) != _idx(old_stop)
    assert session_parity.resolve_short_cb(
        bot, CHAT, "_", old_stop.removeprefix("_:")) is None


def test_a_malformed_table_is_dropped_and_counts_as_lost():
    reg = SessionRegistry()
    _add(reg, "claude-alpha")
    reg.save()
    data = json.loads(state.SESSION_STATE_FILE.read_text())
    good = {"base": 0, "names": ["claude-alpha"]}
    for bad_chat, bad_entry in (
            ("not-a-chat", good),
            (str(CHAT), {"base": -1, "names": ["claude-alpha"]}),
            (str(CHAT), {"base": True, "names": ["claude-alpha"]}),
            (str(CHAT), {"base": 0, "names": "claude-alpha"}),
            (str(CHAT), {"base": 0, "names": [7]}),
            (str(CHAT), ["claude-alpha"])):
        data["button_index"] = {bad_chat: bad_entry}
        state.SESSION_STATE_FILE.write_text(json.dumps(data))
        reg2 = SessionRegistry()
        reg2.load()
        assert reg2.button_tables_lost is True, (bad_chat, bad_entry)
        assert reg2.button_index == {}, (bad_chat, bad_entry)


def test_an_unreadable_state_file_counts_as_lost():
    state.SESSION_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    for raw in ("{not json", "[1, 2]"):
        state.SESSION_STATE_FILE.write_text(raw)
        reg = SessionRegistry()
        reg.load()
        assert reg.button_tables_lost is True, raw


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 file")
def test_a_state_file_that_cannot_be_read_counts_as_lost():
    reg = SessionRegistry()
    _add(reg, "claude-alpha")
    reg.button_index[CHAT] = ["claude-alpha"]
    reg.save()  # a complete saved state, tables included
    state.SESSION_STATE_FILE.chmod(0)
    try:
        again = SessionRegistry()
        again.load()
        assert again.button_tables_lost is True
    finally:
        state.SESSION_STATE_FILE.chmod(0o600)


def test_an_index_below_the_tables_base_names_nothing(mk_bot):
    """Not the table's last slot, as a negative position would read."""
    reg = SessionRegistry()
    alpha = _add(reg, "claude-alpha")
    reg.button_base[CHAT] = 5
    bot = mk_bot(reg)
    assert _idx(session_parity.session_cb(bot, CHAT, alpha, "stop")) == "5"
    assert session_parity.resolve_short_cb(bot, CHAT, "_", "sx:4:stop") is None


def test_a_new_slot_is_on_disk_at_once_and_a_known_one_costs_nothing(
        mk_bot, monkeypatch):
    reg = SessionRegistry()
    alpha = _add(reg, "claude-alpha")
    bot = mk_bot(reg)
    session_parity.session_cb(bot, CHAT, alpha, "stop")
    saved = json.loads(state.SESSION_STATE_FILE.read_text())
    assert saved["button_index"][str(CHAT)]["names"] == ["claude-alpha"]
    saves = []
    monkeypatch.setattr(reg, "save", lambda: saves.append(1))
    session_parity.session_cb(bot, CHAT, alpha, "allow")
    assert saves == []


def test_a_failed_save_does_not_stop_the_button(mk_bot, monkeypatch):
    reg = SessionRegistry()
    alpha = _add(reg, "claude-alpha")
    bot = mk_bot(reg)

    def _fail():
        raise TypeError("not serialisable")

    monkeypatch.setattr(reg, "save", _fail)
    reg._dirty = False
    token = session_parity.session_cb(bot, CHAT, alpha, "stop")
    assert session_parity.resolve_short_cb(
        bot, CHAT, "_", token.removeprefix("_:")) == ("claude-alpha", "stop")
    assert reg._dirty is True  # the next save retries the slot


def test_a_session_trimmed_from_the_gone_history_frees_no_slot(mk_bot):
    """The other way a session leaves the registry: the GONE history cap."""
    reg = SessionRegistry()
    bot = mk_bot(reg)
    old = _add(reg, "claude-alpha")
    old_stop = session_parity.session_cb(bot, CHAT, old, "stop")
    old.status = Status.GONE
    old.gone_at = 1.0  # the oldest
    for i in range(state.MAX_GONE_HISTORY):
        newer = _add(reg, f"claude-g{i}")
        newer.status = Status.GONE
        newer.gone_at = 100.0 + i
    reg._evict_gone_overflow()
    assert reg.get("claude-alpha") is None
    again = session_parity.session_cb(bot, CHAT, _add(reg, "claude-alpha"), "stop")
    assert _idx(again) != _idx(old_stop)
    assert session_parity.resolve_short_cb(
        bot, CHAT, "_", old_stop.removeprefix("_:")) is None
