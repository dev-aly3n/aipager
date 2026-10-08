"""Supergroup-migration leftovers and the reload helper's wording
(roadmap 8.94g, delivery 20 note; delivery 21).

1. The Mini App's folders created for a group follow it to the new id.
2. A second upgrade (``chat_migrations`` already written) edits
   aipager.yaml line by line and keeps its comments; the whole-document
   rewrite is left only for shapes no aipager code writes.
3. A group scope the start-time lookup cannot reach ("chat not found",
   Forbidden, an upgrade Telegram did not report) is logged ONCE, as a
   WARNING naming the scope and what to check, and nothing is sent.
4. ``wizard/daemon_io``'s "Not applied" says the daemon keeps its config
   only when a daemon runs.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest, ChatMigrated, Forbidden, NetworkError
from telegram.ext import filters

from aipager import scope as scope_mod
from aipager.bot import chat_migration
from aipager.miniapp import launch
from tests.test_group_supergroup_migration import (
    DM, GROUP, NEW, OTHER, POLICY, _cfg, _dm, _group, _send_into, _session,
    _write_config,
)


@pytest.fixture(autouse=True)
def _fixed_policy(monkeypatch):
    monkeypatch.setattr("aipager.policy.load_policy", lambda *a, **k: POLICY)


@pytest.fixture(autouse=True)
def _fresh_created(monkeypatch):
    monkeypatch.setattr(launch, "_created", {})


@pytest.fixture
def mbot(mk_bot):
    def _mk(scopes=None):
        scopes = scopes if scopes is not None else [_dm(), _group()]
        _write_config(scopes)
        bot = mk_bot(scopes=scopes)
        bot.policy = POLICY
        bot._app.bot.id = 999
        bot._app.bot.set_my_commands = AsyncMock()
        bot._app.bot.delete_my_commands = AsyncMock()
        bot._app.bot.set_chat_menu_button = AsyncMock()
        bot._app.bot.send_message = AsyncMock(side_effect=_send_into(bot))
        bot._message_chat_gate = filters.Chat({s.chat_id for s in scopes})
        chat_migration.set_handler(bot._migrate_chat)
        return bot
    yield _mk
    chat_migration.set_handler(None)


def _ours(caplog):
    """aipager's own log records (roadmap 8.116). asyncio logs "Task was
    destroyed but it is pending!" for a task an earlier test left on an
    abandoned loop, into whichever test is running when the garbage
    collector reaches it: counting every logger's records made these
    assertions fail now and then for that reason alone."""
    return [r for r in caplog.records if r.name.split(".")[0] == "aipager"]


# ---- 1. the Mini App's created folders -----------------------------------


def test_the_created_folders_follow_the_group(mbot, run_async, tmp_path):
    bot = mbot()
    _session(bot)
    made = tmp_path / "myproject"
    made.mkdir()
    launch.remember_created(GROUP, str(made))
    launch.remember_created(DM, "/dm/own")
    assert run_async(bot._migrate_chat(GROUP, NEW)) is True
    assert launch._created == {NEW: [str(made)], DM: ["/dm/own"]}
    assert str(made) in launch.allowed_roots(bot.registry, NEW, confined=True)
    assert str(made) not in launch.allowed_roots(bot.registry, GROUP, confined=True)


def test_a_move_that_is_refused_leaves_the_folders(mbot, run_async):
    bot = mbot(scopes=[_dm(), _group(), _group(chat_id=NEW, label="other")])
    launch.remember_created(GROUP, "/g/one")
    assert run_async(bot._migrate_chat(GROUP, NEW)) is False
    assert launch._created == {GROUP: ["/g/one"]}


def test_move_created_merges_and_stays_capped(monkeypatch):
    monkeypatch.setattr(launch, "_MAX_CREATED", 4)
    launch._created.update({GROUP: ["/x", "/a", "/b", "/c"], NEW: ["/b", "/d"]})
    launch.move_created(GROUP, NEW)
    # The new chat's own are the newest; one copy of each, at most 4.
    assert launch._created == {NEW: ["/a", "/c", "/b", "/d"]}
    launch.move_created(GROUP, NEW)         # nothing left to move
    assert launch._created == {NEW: ["/a", "/c", "/b", "/d"]}


def test_move_created_into_an_empty_chat():
    launch._created[GROUP] = ["/a"]
    launch.move_created(GROUP, NEW)
    assert launch._created == {NEW: ["/a"]}


# ---- 2. a second upgrade keeps aipager.yaml's comments ---------------------

THREE = [_dm(), _group(), _group(chat_id=OTHER, label="o")]


@pytest.mark.parametrize("block,edited", [
    # The block an earlier upgrade wrote, with the owner's comments.
    ("chat_migrations:\n  -1003: -1004  # the old team\n# kept\ndefault_mode: ask\n",
     "chat_migrations:\n  -1003: -1004  # the old team\n  -1001: -1009999\n# kept\n"
     "default_mode: ask\n"),
    # Two earlier records: the new one goes after the last.
    ("chat_migrations:\n  -1003: -1004\n\n  -1005: -1006\nlast: 1\n",
     "chat_migrations:\n  -1003: -1004\n\n  -1005: -1006\n  -1001: -1009999\nlast: 1\n"),
    # At the very end of the file, with a trailing comment line.
    ("chat_migrations:\n    -1003: -1004\n# end\n",
     "chat_migrations:\n    -1003: -1004\n    -1001: -1009999\n# end\n"),
    ("chat_migrations: {-1003: -1004}  # flow\n",
     "chat_migrations: {-1003: -1004, -1001: -1009999}  # flow\n"),
    ("chat_migrations: {}\n", "chat_migrations: {-1001: -1009999}\n"),
    ("chat_migrations:\ndefault_mode: ask\n",
     "chat_migrations:\n  -1001: -1009999\ndefault_mode: ask\n"),
    ("chat_migrations: ~  # none yet\n",
     "chat_migrations:  # none yet\n  -1001: -1009999\n"),
])
def test_a_second_upgrade_edits_only_its_lines(block, edited, caplog):
    before = _write_config(THREE, extra=block).decode()
    caplog.set_level(logging.INFO)
    scope_mod.migrate_scope_chat_id(GROUP, NEW)
    after = _cfg().read_text(encoding="utf-8")
    expected = before.replace(f"chat_id: {GROUP}\n", f"chat_id: {NEW}\n", 1)
    expected = expected.replace(block, edited)
    assert after == expected
    assert "# my own note\n" in after
    assert not any("rewrote the whole" in r.getMessage() for r in caplog.records)


def test_a_chain_of_two_upgrades_keeps_the_comments():
    before = _write_config(THREE).decode()
    scope_mod.migrate_scope_chat_id(GROUP, NEW)
    scope_mod.migrate_scope_chat_id(OTHER, -1007777)
    after = _cfg().read_text(encoding="utf-8")
    expected = (before.replace(f"chat_id: {GROUP}\n", f"chat_id: {NEW}\n", 1)
                .replace(f"chat_id: {OTHER}\n", "chat_id: -1007777\n", 1)
                + f"chat_migrations:\n  {GROUP}: {NEW}\n  {OTHER}: -1007777\n")
    assert after == expected
    assert scope_mod.load_chat_migrations() == {GROUP: NEW, OTHER: -1007777}


@pytest.mark.parametrize("block", [
    # A flow mapping over two lines: no line shape handled here.
    "chat_migrations: {-1003: -1004,\n  -1005: -1006}\n",
    # The old id already has a record (a hand edit): a second key would
    # be the wrong record.
    "chat_migrations:\n  -1001: -1008\n",
])
def test_a_shape_no_aipager_code_writes_is_rewritten_whole_and_said(block, caplog):
    """The documented alternative (scope.migrate_scope_chat_id): exact
    keys and values, comments lost, a WARNING says so."""
    _write_config(THREE, extra=block)
    raw_before = scope_mod._raw_yaml(_cfg())
    caplog.set_level(logging.WARNING)
    scope_mod.migrate_scope_chat_id(GROUP, NEW)
    raw_after = scope_mod._raw_yaml(_cfg())
    want = dict(raw_before["chat_migrations"])
    want[GROUP] = NEW
    assert raw_after["chat_migrations"] == want
    assert [s["chat_id"] for s in raw_after["scopes"]] == [DM, NEW, OTHER]
    assert [r.levelno for r in caplog.records
            if "rewrote the whole" in r.getMessage()] == [logging.WARNING]


# ---- 3. a group the start-time lookup cannot reach -------------------------

@pytest.mark.parametrize("error", [
    BadRequest("Chat not found"),
    Forbidden("Forbidden: bot was kicked from the group chat"),
    BadRequest("Bad Request: group chat was upgraded to a supergroup chat"),
])
def test_an_unreachable_group_is_one_warning_and_nothing_sent(
        mbot, run_async, caplog, error):
    bot = mbot(scopes=[_dm(), _group(label="team")])
    bot._app.bot.get_chat = AsyncMock(side_effect=error)
    caplog.set_level(logging.DEBUG)
    run_async(bot._probe_group_chats())
    run_async(bot._probe_group_chats())         # a second look: still one
    warnings = [r.getMessage() for r in caplog.records
                if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "'team'" in warnings[0] and str(GROUP) in warnings[0]
    assert "aipager config" in warnings[0]
    assert "old id" in warnings[0]
    # One lookup per probe, never a retry; the DM is never asked.
    assert [c.args for c in bot._app.bot.get_chat.await_args_list] == [
        (GROUP,), (GROUP,)]
    bot._app.bot.send_message.assert_not_awaited()
    assert [s.chat_id for s in bot.scopes] == [DM, GROUP]


def test_each_unreachable_group_gets_its_own_warning(mbot, run_async, caplog):
    bot = mbot(scopes=[_dm(), _group(label="a"), _group(chat_id=OTHER, label="b")])
    bot._app.bot.get_chat = AsyncMock(side_effect=BadRequest("Chat not found"))
    caplog.set_level(logging.WARNING)
    run_async(bot._probe_group_chats())
    warnings = [r.getMessage() for r in _ours(caplog)]
    assert len(warnings) == 2
    assert "'a'" in warnings[0] and "'b'" in warnings[1]


@pytest.mark.parametrize("error", [BadRequest("Message is not modified"),
                                   NetworkError("timed out")])
def test_a_passing_lookup_failure_is_not_a_warning(mbot, run_async, caplog, error):
    bot = mbot()
    bot._app.bot.get_chat = AsyncMock(side_effect=error)
    caplog.set_level(logging.WARNING)
    run_async(bot._probe_group_chats())
    assert _ours(caplog) == []


def test_an_upgrade_the_lookup_reports_is_still_followed(mbot, run_async, caplog):
    bot = mbot()
    bot._app.bot.get_chat = AsyncMock(side_effect=ChatMigrated(NEW))
    calls = []
    chat_migration.set_handler(AsyncMock(side_effect=lambda o, n: calls.append((o, n))))

    async def _go():
        await bot._probe_group_chats()
        for t in list(chat_migration._tasks):
            await t
    caplog.set_level(logging.WARNING)
    run_async(_go())
    assert calls == [(GROUP, NEW)]
    assert not any("could not be reached" in r.getMessage() for r in caplog.records)


def test_the_menu_of_an_unreachable_group_is_not_a_second_warning(
        mbot, run_async, caplog):
    bot = mbot(scopes=[_dm(), _group(label="team")])
    bot._app.bot.get_chat = AsyncMock(side_effect=BadRequest("Chat not found"))

    async def _set(commands, scope=None):
        if scope.chat_id == GROUP:
            raise BadRequest("Chat not found")
    bot._app.bot.set_my_commands = AsyncMock(side_effect=_set)
    caplog.set_level(logging.WARNING)
    run_async(bot._update_bot_commands_per_scope())
    run_async(bot._probe_group_chats())
    assert len(_ours(caplog)) == 1
    assert "could not be reached" in _ours(caplog)[0].getMessage()


def test_a_group_added_by_a_reload_and_unreachable_is_one_warning(
        mbot, run_async, caplog):
    """A live reload never runs the start-time lookup: setting the new
    group's menu is where it shows, and it warns once too."""
    bot = mbot(scopes=[_dm(), _group(label="team")])

    async def _set(commands, scope=None):
        if scope.chat_id == GROUP:
            raise Forbidden("Forbidden: bot is not a member of the group chat")
    bot._app.bot.set_my_commands = AsyncMock(side_effect=_set)
    caplog.set_level(logging.WARNING)
    run_async(bot._update_bot_commands_per_scope())
    bot._registered_scope_labels.clear()        # labels changed: tried again
    run_async(bot._update_bot_commands_per_scope())
    warnings = [r.getMessage() for r in _ours(caplog)]
    assert len(warnings) == 1
    assert "'team'" in warnings[0] and "aipager config" in warnings[0]
    bot._app.bot.send_message.assert_not_awaited()


def test_a_dm_menu_failure_still_warns(mbot, run_async, caplog):
    """Only a group's unreachable chat defers to the lookup's warning."""
    bot = mbot()

    async def _set(commands, scope=None):
        if scope.chat_id == DM:
            raise Forbidden("Forbidden: bot was blocked by the user")
    bot._app.bot.set_my_commands = AsyncMock(side_effect=_set)
    caplog.set_level(logging.WARNING)
    run_async(bot._update_bot_commands_per_scope())
    assert [r.getMessage() for r in _ours(caplog)] == [
        f"Failed to set bot commands for scope {DM}"]


@pytest.mark.parametrize("error,unreachable", [
    (Forbidden("x"), True),
    (BadRequest("Chat not found"), True),
    (BadRequest("Bad Request: CHAT NOT FOUND"), True),
    (BadRequest("Bad Request: group chat was upgraded to a supergroup chat"), True),
    (BadRequest("Message to edit not found"), False),
    (ChatMigrated(NEW), False),
    (NetworkError("Chat not found"), False),
    (RuntimeError("Chat not found"), False),
])
def test_is_unreachable_chat_error(error, unreachable):
    assert chat_migration.is_unreachable_chat_error(error) is unreachable


# ---- 4. the reload helper's "Not applied" ---------------------------------

def _broken_config():
    _cfg().parent.mkdir(parents=True, exist_ok=True)
    _cfg().write_text("scopes: [unclosed", encoding="utf-8")


def test_not_applied_without_a_daemon_says_only_what_is_wrong(monkeypatch, capsys):
    from aipager.wizard import daemon_io
    _broken_config()
    monkeypatch.setattr(daemon_io, "_detect_daemon_running", lambda: None)
    monkeypatch.setattr(daemon_io, "_signal_reload",
                        lambda: pytest.fail("signalled"))
    daemon_io._apply_team_change_hint()
    out = capsys.readouterr().out
    assert "Not applied:" in out
    assert "daemon" not in out.lower()


@pytest.mark.parametrize("pid", [4242, -1])
def test_not_applied_with_a_daemon_keeps_todays_text(monkeypatch, capsys, pid):
    from aipager.wizard import daemon_io
    _broken_config()
    monkeypatch.setattr(daemon_io, "_detect_daemon_running", lambda: pid)
    monkeypatch.setattr(daemon_io, "_signal_reload",
                        lambda: pytest.fail("signalled"))
    daemon_io._apply_team_change_hint()
    out = capsys.readouterr().out
    assert "Not applied:" in out
    assert "The daemon keeps its previous config until this is fixed." in out


def test_only_aipagers_own_records_are_counted(caplog):
    # Roadmap 8.116: a record from another logger (asyncio's "Task was
    # destroyed but it is pending!" for an earlier test's task) never counts.
    caplog.set_level(logging.WARNING)
    logging.getLogger("asyncio").error("Task was destroyed but it is pending!")
    logging.getLogger("aipager.bot.core").warning("ours")
    assert [r.getMessage() for r in _ours(caplog)] == ["ours"]
