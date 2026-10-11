"""Success criteria 22-23: new sessions start in Auto everywhere (/new,
the Mini App form, preferences), the chat's New sessions setting
overrides it, and /settings New sessions offers three options stored under
the new key, with "Auto for you" shown to a non-admin when the chat's
default is Full access."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aipager import preferences
from aipager.state import SessionRegistry

from _tm3 import (ADA, BOB, DM_CHAT, GROUP, OWNER, dm_bot, scoped_bot, settle_tasks, tap,
                  typed)

HERE = Path(__file__).parent


def _new(bot, run_async, text, chat, user, chat_type="private"):
    u = typed(text, chat_id=chat, user_id=user, chat_type=chat_type)

    async def _go():
        await bot._handle_new_cmd(u, MagicMock())
        await settle_tasks()
    run_async(_go())
    return u


def test_fresh_chat_default_is_auto():
    assert preferences.get_new_session_defaults(DM_CHAT).mode == "auto"


@pytest.mark.parametrize("value", ["ask", "auto", "full"], ids=["ask", "auto", "full"])
def test_stored_default_reads_back(value):
    preferences.set_new_session_default(DM_CHAT, "mode", value)
    assert preferences.get_new_session_defaults(DM_CHAT).mode == value


def test_empty_stored_default_means_auto():
    preferences.set_new_session_default(DM_CHAT, "mode", "")
    assert preferences.get_new_session_defaults(DM_CHAT).mode == "auto"


@pytest.mark.parametrize("bad", ["bypass", "default", "AUTO", "skip"],
                         ids=["bypass", "claude-id", "wrong-case", "unknown"])
def test_set_default_rejects_other_values(bad):
    with pytest.raises(ValueError):
        preferences.set_new_session_default(DM_CHAT, "mode", bad)


def test_new_with_no_stored_default_launches_auto(mk_bot, run_async, pty):
    bot = dm_bot(mk_bot, SessionRegistry())
    _new(bot, run_async, "/new x1", DM_CHAT, OWNER)
    assert pty.launched_modes == ["auto"]


def test_new_records_auto_on_the_session(mk_bot, run_async, pty):
    bot = dm_bot(mk_bot, SessionRegistry())
    _new(bot, run_async, "/new x1", DM_CHAT, OWNER)
    sess = next(s for s in bot.registry.all_sessions().values() if s.label == "x1")
    assert sess.perm_mode == "auto"


@pytest.mark.parametrize("stored", ["ask", "full"], ids=["ask", "full"])
def test_new_follows_the_chat_default_for_an_admin(mk_bot, run_async, pty, stored):
    bot = dm_bot(mk_bot, SessionRegistry())
    preferences.set_new_session_default(DM_CHAT, "mode", stored)
    _new(bot, run_async, "/new x1", DM_CHAT, OWNER)
    assert pty.launched_modes == [stored]


def test_new_in_a_group_by_a_user_launches_auto(mk_bot, run_async, pty):
    bot = scoped_bot(mk_bot, SessionRegistry())
    _new(bot, run_async, "/new x1", GROUP, BOB, "group")
    assert pty.launched_modes == ["auto"]


def test_name_card_shows_auto_as_the_mode(mk_bot, run_async, pty):
    bot = dm_bot(mk_bot, SessionRegistry())
    u = _new(bot, run_async, "/new", DM_CHAT, OWNER)
    shown = "\n".join(str(c.args[0]) for c in u.message.reply_text.await_args_list if c.args)
    assert "🤖 Auto mode" in shown, shown


def test_name_card_offers_the_other_two_modes_to_an_admin(mk_bot, run_async, pty):
    bot = dm_bot(mk_bot, SessionRegistry())
    u = _new(bot, run_async, "/new", DM_CHAT, OWNER)
    cbs = [b.callback_data for c in u.message.reply_text.await_args_list
           for kb in [c.kwargs.get("reply_markup")] if kb is not None
           for row in kb.inline_keyboard for b in row]
    modes = sorted(cb for cb in cbs if cb.startswith("_:nw:mode:"))
    assert modes == ["_:nw:mode:ask", "_:nw:mode:full"], cbs


# ---- /settings -> New sessions ----------------------------------------------

def _ns_screen(bot, run_async, chat, user, data="_:set:ns"):
    u, q = tap(data, chat_id=chat, user_id=user)
    run_async(bot._handle_callback(u, MagicMock()))
    calls = (q.edit_message_text.await_args_list + q.message.edit_text.await_args_list
             + bot._app.bot.edit_message_text.await_args_list
             + bot._app.bot.send_message.await_args_list)
    c = calls[-1]
    kb = c.kwargs.get("reply_markup")
    text = str(c.args[0] if c.args else c.kwargs.get("text"))
    return text, [(b.text, b.callback_data) for row in (kb.inline_keyboard if kb else [])
                  for b in row]


def test_settings_offers_three_mode_options(mk_bot, run_async, pty):
    bot = scoped_bot(mk_bot, SessionRegistry())
    _t, btns = _ns_screen(bot, run_async, GROUP, ADA)
    assert [cb for _t2, cb in btns if cb.startswith("_:set:ns:mode:")] == [
        "_:set:ns:mode:auto", "_:set:ns:mode:ask", "_:set:ns:mode:full"]


def test_settings_marks_auto_on_a_fresh_chat(mk_bot, run_async, pty):
    bot = scoped_bot(mk_bot, SessionRegistry())
    _t, btns = _ns_screen(bot, run_async, GROUP, ADA)
    ticked = [cb for t, cb in btns if "✅" in t and cb.startswith("_:set:ns:mode:")]
    assert ticked == ["_:set:ns:mode:auto"]


def _stored(chat):
    raw = json.loads(preferences._PREFERENCES_PATH.read_text())
    found = []

    def walk(x):
        if isinstance(x, dict):
            if "new_session_perm_mode" in x or "new_session_mode" in x:
                found.append(x)
            for v in x.values():
                walk(v)
    walk(raw)
    assert len(found) == 1, raw
    return found[0]


@pytest.mark.parametrize("value", ["ask", "auto", "full"], ids=["ask", "auto", "full"])
def test_settings_tap_stores_the_new_key(mk_bot, run_async, pty, value):
    bot = scoped_bot(mk_bot, SessionRegistry())
    _ns_screen(bot, run_async, GROUP, ADA, f"_:set:ns:mode:{value}")
    assert _stored(GROUP).get("new_session_perm_mode") == value


@pytest.mark.parametrize("value", ["ask", "auto", "full"], ids=["ask", "auto", "full"])
def test_settings_tap_changes_what_new_uses(mk_bot, run_async, pty, value):
    bot = scoped_bot(mk_bot, SessionRegistry())
    _ns_screen(bot, run_async, GROUP, ADA, f"_:set:ns:mode:{value}")
    assert preferences.get_new_session_defaults(GROUP).mode == value


def test_settings_user_cannot_write_the_group_default(mk_bot, run_async, pty):
    """A write in a group needs an admin (entrypoints)."""
    bot = scoped_bot(mk_bot, SessionRegistry())
    _u, _q = tap("_:set:ns:mode:full", chat_id=GROUP, user_id=BOB)
    run_async(bot._handle_callback(_u, MagicMock()))
    assert preferences.get_new_session_defaults(GROUP).mode == "auto"


def test_settings_non_admin_sees_auto_for_you_when_default_is_full(mk_bot, run_async, pty):
    bot = scoped_bot(mk_bot, SessionRegistry())
    preferences.set_new_session_default(GROUP, "mode", "full")
    text, _b = _ns_screen(bot, run_async, GROUP, BOB)
    assert "Auto for you (🔓 Full access is for admins)" in text, text


def test_settings_non_admin_is_shown_auto_when_default_is_full(mk_bot, run_async, pty):
    """Behaviour behind the wording: the non-admin is told Auto, not Full."""
    bot = scoped_bot(mk_bot, SessionRegistry())
    preferences.set_new_session_default(GROUP, "mode", "full")
    text, _b = _ns_screen(bot, run_async, GROUP, BOB)
    line = next(ln for ln in text.splitlines() if "🤖" in ln or "🔓" in ln)
    assert line.split(":", 1)[-1].strip().startswith("🤖"), text


def test_settings_admin_sees_full_access_when_default_is_full(mk_bot, run_async, pty):
    bot = scoped_bot(mk_bot, SessionRegistry())
    preferences.set_new_session_default(GROUP, "mode", "full")
    text, _b = _ns_screen(bot, run_async, GROUP, ADA)
    assert "🔓 Full access mode" in text


# ---- the Mini App form ---------------------------------------------------------

@pytest.fixture(scope="module")
def node_bin():
    exe = shutil.which("node") or shutil.which("nodejs")
    if not exe:
        pytest.skip("node not available")
    return exe


def _form_post(node_bin, tmp_path, options):
    from aipager.miniapp.static import INDEX_HTML
    page = tmp_path / "page.html"
    page.write_text(INDEX_HTML, encoding="utf-8")
    proc = subprocess.run([node_bin, str(HERE / "tm3_form.js"), str(page), json.dumps(options)],
                          capture_output=True, text=True, timeout=60)
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("POST ")), None)
    assert line, f"rc={proc.returncode}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    return json.loads(line[5:])


@pytest.mark.parametrize("options,expected", [
    ({"default_mode": "auto", "can_use_full": True}, "auto"),
    ({"default_mode": "auto", "can_use_full": False}, "auto"),
    ({"default_mode": "ask", "can_use_full": False}, "ask"),
    ({"default_mode": "full", "can_use_full": True}, "full"),
    ({"can_use_full": True}, "auto"),
], ids=["auto-admin", "auto-member", "chat-default-ask", "chat-default-full-admin",
        "no-default-falls-back-to-auto"])
def test_form_preselects_the_viewers_default(node_bin, tmp_path, options, expected):
    assert _form_post(node_bin, tmp_path, options).get("mode") == expected


def test_form_never_sends_the_old_boolean(node_bin, tmp_path):
    body = _form_post(node_bin, tmp_path, {"default_mode": "auto", "can_use_full": True})
    assert "skip_perms" not in body
