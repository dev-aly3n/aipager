"""Telegram /new for restricted members, and owner/admin unchanged
(design.md success criteria 4-6, plus /settings -> New sessions -> Folder;
entrypoints.md "Telegram").

Driven through the /new card's entry points (``new_flow._open_card``,
``new_flow.handle_callback``, ``new_flow.maybe_handle_text``) and
``bot.create_session``. ``inject.launch_session`` is recorded, never run."""

from __future__ import annotations

import os
import stat
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import member_root, preferences
from aipager.bot import new_flow
from aipager.state import SessionRegistry

kit = sys.modules["_userroot_kit"]
OWNER, ADMIN, SARA, BOB, CY, HAL = kit.OWNER, kit.ADMIN, kit.SARA, kit.BOB, kit.CY, kit.HAL
GROUP = kit.GROUP


def _registry(*cwds, chat=GROUP):
    reg = SessionRegistry()
    for i, cwd in enumerate(cwds):
        s = reg.get_or_create(f"claude-s{i}")
        s.label = f"s{i}"
        s.scope_chat_id = chat
        s.cwd = str(cwd)
    return reg


def _open(bot, run_async, uid, chat):
    send = AsyncMock(return_value=MagicMock(message_id=900))
    run_async(new_flow._open_card(bot, chat, uid, send))
    text, kb = send.await_args.args[:2]
    return text, kb


def _tap(bot, run_async, uid, action, chat):
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock(message_id=900)
    query.message.chat = MagicMock(id=chat, type="supergroup" if chat < 0 else "private")
    query.from_user = MagicMock(id=uid)
    update = MagicMock()
    update.callback_query = query
    update.effective_user = MagicMock(id=uid)
    update.effective_chat = MagicMock(id=chat, type="supergroup" if chat < 0 else "private")
    update.message = None
    run_async(new_flow.handle_callback(bot, update, query, "_", action))
    return query


def _typed(bot, run_async, mk_update, uid, chat, text):
    update = mk_update(text, chat_id=chat, user_id=uid)
    update.effective_chat.type = "supergroup" if chat < 0 else "private"
    run_async(new_flow.maybe_handle_text(bot, update, MagicMock(), text))
    return update


def _last_edit(bot):
    kw = bot._app.bot.edit_message_text.await_args.kwargs
    return kw["text"], kw.get("reply_markup")


def _labels(kb):
    return [b.text.removesuffix(" ✅") for row in kb.inline_keyboard for b in row]


def _folder_page(bot, run_async, uid, chat):
    _open(bot, run_async, uid, chat)
    _tap(bot, run_async, uid, "nw:opt:path", chat)
    return _last_edit(bot)


def _options(bot, uid, chat):
    return bot._new_wizard_pending[(chat, uid)]["path_options"]


def _root_of(scopes, chat):
    s = next(x for x in scopes if x.chat_id == chat)
    return os.path.realpath(member_root.effective_root(s, scopes))


# The restricted people: (who, chat) for a user and the read_only-with-
# prompt custom role, in a DM and in the group.
RESTRICTED = [(SARA, SARA), (HAL, HAL), (BOB, GROUP), (HAL, GROUP)]
RIDS = ["user-dm", "helper-dm", "user-group", "helper-group"]


@pytest.fixture
def outside(home):
    """A folder outside every root, used by an earlier session."""
    p = home / "aipager"
    p.mkdir()
    return p


@pytest.fixture
def made(scopes):
    """Create the restricted person's root with a few subfolders."""
    def _made(chat):
        root = _root_of(scopes, chat)
        os.makedirs(root, exist_ok=True)
        for sub in ("alpha", "Beta", ".hidden"):
            os.makedirs(os.path.join(root, sub), exist_ok=True)
        with open(os.path.join(root, "notes.txt"), "w") as f:
            f.write("a file, not a folder\n")
        return root
    return _made


# ---- criterion 4: the default root is made on first use ------------------------------

def test_default_root_created_0700_on_first_use(gbot, run_async, scopes, daemon_in_home):
    root = _root_of(scopes, SARA)
    _open(gbot(), run_async, SARA, SARA)
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o700


def test_aipager_users_parent_created_0700(gbot, run_async, scopes, daemon_in_home, home):
    _open(gbot(), run_async, SARA, SARA)
    assert stat.S_IMODE(os.stat(home / "aipager-users").st_mode) == 0o700


def test_owner_opening_new_creates_no_root(gbot, run_async, scopes, daemon_in_home, home):
    _open(gbot(), run_async, OWNER, OWNER)
    assert not (home / "aipager-users").exists()


def test_existing_root_mode_is_not_changed(gbot, run_async, scopes, daemon_in_home):
    root = _root_of(scopes, SARA)
    os.makedirs(root)
    os.chmod(root, 0o755)
    _open(gbot(), run_async, SARA, SARA)
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o755


# ---- criterion 5: the folder page, New folder, start ------------------------------------

@pytest.mark.parametrize("who,chat", RESTRICTED, ids=RIDS)
def test_user_picker_lists_only_root(gbot, run_async, scopes, daemon_in_home, outside,
                                     made, who, chat):
    root = made(chat)
    bot = gbot(_registry(outside, chat=chat))
    _folder_page(bot, run_async, who, chat)
    assert sorted(_options(bot, who, chat)) == sorted(
        [root, os.path.join(root, "alpha"), os.path.join(root, "Beta")])


@pytest.mark.parametrize("who,chat", RESTRICTED, ids=RIDS)
def test_user_folder_page_never_shows_an_outside_folder(gbot, run_async, scopes,
                                                         daemon_in_home, outside, made,
                                                         who, chat):
    made(chat)
    bot = gbot(_registry(outside, chat=chat))
    _text, kb = _folder_page(bot, run_async, who, chat)
    assert new_flow._short_path(str(outside)) not in _labels(kb)


def test_picker_hides_dotfolders(gbot, run_async, scopes, daemon_in_home, made):
    root = made(GROUP)
    bot = gbot()
    _folder_page(bot, run_async, BOB, GROUP)
    assert os.path.join(root, ".hidden") not in _options(bot, BOB, GROUP)


def test_picker_lists_no_files(gbot, run_async, scopes, daemon_in_home, made):
    root = made(GROUP)
    bot = gbot()
    _folder_page(bot, run_async, BOB, GROUP)
    assert os.path.join(root, "notes.txt") not in _options(bot, BOB, GROUP)


def test_picker_drops_symlink_out_of_root(gbot, run_async, scopes, daemon_in_home, made,
                                          outside):
    root = made(GROUP)
    os.symlink(outside, os.path.join(root, "escape"))
    bot = gbot()
    _folder_page(bot, run_async, BOB, GROUP)
    opts = [os.path.realpath(p) for p in _options(bot, BOB, GROUP)]
    assert str(outside) not in opts


def test_picker_keeps_symlink_inside_root(gbot, run_async, scopes, daemon_in_home, made):
    root = made(GROUP)
    os.symlink(os.path.join(root, "alpha"), os.path.join(root, "alias"))
    bot = gbot()
    _folder_page(bot, run_async, BOB, GROUP)
    opts = [os.path.realpath(p) for p in _options(bot, BOB, GROUP)]
    assert all(o == root or o.startswith(root + os.sep) for o in opts)


def test_picker_does_not_list_a_sibling_sharing_the_roots_prefix(gbot, run_async, scopes,
                                                                 daemon_in_home, made,
                                                                 team_root):
    made(GROUP)
    sib = str(team_root) + "2"
    os.makedirs(sib)
    bot = gbot(_registry(sib))
    _folder_page(bot, run_async, BOB, GROUP)
    assert sib not in _options(bot, BOB, GROUP)


def test_picker_is_capped(gbot, run_async, scopes, daemon_in_home):
    root = _root_of(scopes, GROUP)
    for i in range(40):
        os.makedirs(os.path.join(root, f"p{i:02d}"))
    bot = gbot()
    _folder_page(bot, run_async, BOB, GROUP)
    assert len(_options(bot, BOB, GROUP)) <= 25


@pytest.mark.parametrize("who,chat", RESTRICTED, ids=RIDS)
def test_user_folder_page_has_no_default_row(gbot, run_async, scopes, daemon_in_home,
                                             made, who, chat):
    made(chat)
    bot = gbot()
    _text, kb = _folder_page(bot, run_async, who, chat)
    assert not any(b.startswith("Default folder") for b in _labels(kb))


@pytest.mark.parametrize("who,chat", RESTRICTED, ids=RIDS)
def test_user_folder_page_offers_new_folder(gbot, run_async, scopes, daemon_in_home,
                                            made, who, chat):
    made(chat)
    bot = gbot()
    _text, kb = _folder_page(bot, run_async, who, chat)
    assert "➕ New folder" in _labels(kb)


@pytest.mark.parametrize("who,chat", RESTRICTED, ids=RIDS)
def test_new_card_defaults_to_root(gbot, run_async, scopes, daemon_in_home, who, chat):
    bot = gbot()
    _open(bot, run_async, who, chat)
    cwd = bot._new_wizard_pending[(chat, who)]["cwd"]
    assert os.path.realpath(cwd) == _root_of(scopes, chat)


def test_stored_default_outside_root_is_ignored(gbot, run_async, scopes, daemon_in_home,
                                                outside):
    preferences.set_new_session_default(GROUP, "cwd", str(outside))
    bot = gbot(_registry(outside))
    assert os.path.realpath(new_flow.resolve_new_session_settings(bot, GROUP, BOB)["cwd"]) \
        == _root_of(scopes, GROUP)


def test_stored_default_outside_root_still_applies_to_owner(gbot, scopes, daemon_in_home,
                                                            outside):
    preferences.set_new_session_default(GROUP, "cwd", str(outside))
    bot = gbot(_registry(outside))
    assert new_flow.resolve_new_session_settings(bot, GROUP, OWNER)["cwd"] == str(outside)


def test_stored_default_inside_root_is_kept(gbot, scopes, daemon_in_home, made):
    root = made(GROUP)
    preferences.set_new_session_default(GROUP, "cwd", os.path.join(root, "alpha"))
    bot = gbot(_registry(os.path.join(root, "alpha")))
    assert new_flow.resolve_new_session_settings(bot, GROUP, BOB)["cwd"] == os.path.join(root, "alpha")


@pytest.mark.parametrize("who,chat", RESTRICTED, ids=RIDS)
def test_new_folder_is_made_inside_the_root(gbot, run_async, mk_update, scopes,
                                            daemon_in_home, who, chat):
    root = _root_of(scopes, chat)
    bot = gbot()
    _open(bot, run_async, who, chat)
    _tap(bot, run_async, who, "nw:path:new", chat)
    _typed(bot, run_async, mk_update, who, chat, "myproj")
    assert os.path.isdir(os.path.join(root, "myproj"))


def test_new_folder_becomes_the_cards_folder(gbot, run_async, mk_update, scopes,
                                             daemon_in_home):
    root = _root_of(scopes, GROUP)
    bot = gbot()
    _open(bot, run_async, BOB, GROUP)
    _tap(bot, run_async, BOB, "nw:path:new", GROUP)
    _typed(bot, run_async, mk_update, BOB, GROUP, "myproj")
    assert bot._new_wizard_pending[(GROUP, BOB)]["cwd"] == os.path.join(root, "myproj")


def test_new_folder_with_dotdot_never_leaves_the_root(gbot, run_async, mk_update, scopes,
                                                      daemon_in_home, home):
    bot = gbot()
    _open(bot, run_async, BOB, GROUP)
    _tap(bot, run_async, BOB, "nw:path:new", GROUP)
    _typed(bot, run_async, mk_update, BOB, GROUP, "../escaped")
    assert not (home / "roots" / "escaped").exists()


def test_new_folder_with_a_slash_never_leaves_the_root(gbot, run_async, mk_update, scopes,
                                                       daemon_in_home, home):
    bot = gbot()
    _open(bot, run_async, BOB, GROUP)
    _tap(bot, run_async, BOB, "nw:path:new", GROUP)
    _typed(bot, run_async, mk_update, BOB, GROUP, str(home / "abs"))
    assert not (home / "abs").exists()


def test_new_folder_outside_root_refused(gbot, run_async, mk_update, scopes,
                                         daemon_in_home, outside):
    """A New folder whose parent was forged outside the root makes nothing
    there."""
    bot = gbot(_registry(outside))
    _open(bot, run_async, BOB, GROUP)
    _tap(bot, run_async, BOB, "nw:path:new", GROUP)
    bot._new_wizard_pending[(GROUP, BOB)]["new_folder_parent"] = str(outside)
    bot._new_wizard_pending[(GROUP, BOB)]["cwd"] = str(outside)
    _typed(bot, run_async, mk_update, BOB, GROUP, "sneaky")
    assert not (outside / "sneaky").exists()


def test_new_folder_in_a_chosen_subfolder(gbot, run_async, mk_update, scopes,
                                          daemon_in_home, made):
    root = made(GROUP)
    bot = gbot()
    _folder_page(bot, run_async, BOB, GROUP)
    idx = _options(bot, BOB, GROUP).index(os.path.join(root, "alpha"))
    _tap(bot, run_async, BOB, f"nw:path:{idx}", GROUP)
    _tap(bot, run_async, BOB, "nw:path:new", GROUP)
    _typed(bot, run_async, mk_update, BOB, GROUP, "deep")
    assert os.path.isdir(os.path.join(root, "alpha", "deep"))


@pytest.mark.parametrize("who,chat", RESTRICTED, ids=RIDS)
def test_starting_in_the_root_works(gbot, run_async, mk_update, scopes, daemon_in_home,
                                    seams, who, chat):
    bot = gbot()
    _open(bot, run_async, who, chat)
    _typed(bot, run_async, mk_update, who, chat, "dev")
    assert os.path.realpath(seams.launch.await_args.kwargs["cwd"]) == _root_of(scopes, chat)


def test_starting_in_a_chosen_subfolder_works(gbot, run_async, mk_update, scopes,
                                              daemon_in_home, made, seams):
    root = made(GROUP)
    bot = gbot()
    _folder_page(bot, run_async, BOB, GROUP)
    idx = _options(bot, BOB, GROUP).index(os.path.join(root, "Beta"))
    _tap(bot, run_async, BOB, f"nw:path:{idx}", GROUP)
    _typed(bot, run_async, mk_update, BOB, GROUP, "dev")
    assert seams.launch.await_args.kwargs["cwd"] == os.path.join(root, "Beta")


def test_a_forged_card_folder_outside_root_is_refused(gbot, run_async, mk_update, scopes,
                                                      daemon_in_home, outside, seams):
    bot = gbot(_registry(outside))
    _open(bot, run_async, BOB, GROUP)
    bot._new_wizard_pending[(GROUP, BOB)]["cwd"] = str(outside)
    _typed(bot, run_async, mk_update, BOB, GROUP, "dev")
    seams.launch.assert_not_awaited()


def test_a_forged_card_folder_shows_the_root_message(gbot, run_async, mk_update, scopes,
                                                     daemon_in_home, outside, seams):
    bot = gbot(_registry(outside))
    _open(bot, run_async, BOB, GROUP)
    bot._new_wizard_pending[(GROUP, BOB)]["cwd"] = str(outside)
    update = _typed(bot, run_async, mk_update, BOB, GROUP, "dev")
    shown = kit.texts_of(bot._app.bot.edit_message_text, bot._app.bot.send_message,
                         update.message.reply_text)
    assert any(kit.outside_root_msg(_root_of(scopes, GROUP)) in t for t in shown)


def test_a_stale_folder_index_is_not_outside_the_root(gbot, run_async, scopes,
                                                      daemon_in_home, made, outside):
    made(GROUP)
    bot = gbot(_registry(outside))
    _folder_page(bot, run_async, BOB, GROUP)
    _tap(bot, run_async, BOB, "nw:path:99", GROUP)
    cwd = bot._new_wizard_pending[(GROUP, BOB)]["cwd"]
    assert cwd is None or os.path.realpath(cwd).startswith(_root_of(scopes, GROUP))


def test_default_token_for_a_user_means_the_root(gbot, run_async, scopes, daemon_in_home):
    bot = gbot()
    _open(bot, run_async, BOB, GROUP)
    _tap(bot, run_async, BOB, "nw:path:default", GROUP)
    assert os.path.realpath(bot._new_wizard_pending[(GROUP, BOB)]["cwd"] or "") == \
        _root_of(scopes, GROUP)


# ---- create_session, the one launch seam --------------------------------------------

@pytest.mark.parametrize("who,chat", RESTRICTED, ids=RIDS)
def test_create_session_outside_root_refused(gbot, run_async, scopes, daemon_in_home,
                                             outside, seams, who, chat):
    bot = gbot()
    name, err = run_async(bot.create_session("x", scope_chat_id=chat, cwd=str(outside),
                                             driver_user_id=who))
    assert (name, err) == ("", kit.outside_root_msg(_root_of(scopes, chat)))


def test_create_session_outside_root_launches_nothing(gbot, run_async, scopes,
                                                      daemon_in_home, outside, seams):
    bot = gbot()
    run_async(bot.create_session("x", scope_chat_id=GROUP, cwd=str(outside),
                                 driver_user_id=BOB))
    seams.launch.assert_not_awaited()


@pytest.mark.parametrize("where", ["home", "slash", "sibling-prefix", "dotdot", "other-root"])
def test_create_session_refuses_every_outside_shape(gbot, run_async, scopes, daemon_in_home,
                                                     seams, home, team_root, hal_root, where):
    folder = {"home": str(home), "slash": "/", "sibling-prefix": str(team_root) + "2",
              "dotdot": str(team_root / ".." / "elsewhere"),
              "other-root": str(hal_root)}[where]
    bot = gbot()
    _name, err = run_async(bot.create_session("x", scope_chat_id=GROUP, cwd=folder,
                                              driver_user_id=BOB))
    assert err == kit.outside_root_msg(_root_of(scopes, GROUP))


def test_create_session_refuses_a_symlink_out_of_the_root(gbot, run_async, scopes,
                                                          daemon_in_home, seams, made,
                                                          outside):
    root = made(GROUP)
    link = os.path.join(root, "escape")
    os.symlink(outside, link)
    bot = gbot()
    run_async(bot.create_session("x", scope_chat_id=GROUP, cwd=link, driver_user_id=BOB))
    seams.launch.assert_not_awaited()


def test_create_session_with_no_folder_is_refused_when_daemon_is_outside_root(
        gbot, run_async, scopes, daemon_in_home, seams):
    """No folder means the daemon's folder (home here): outside the root."""
    bot = gbot()
    _n, err = run_async(bot.create_session("x", scope_chat_id=GROUP, cwd=None,
                                           driver_user_id=BOB))
    assert err == kit.outside_root_msg(_root_of(scopes, GROUP))


@pytest.mark.parametrize("sub", ["", "alpha"])
def test_create_session_inside_root_launches(gbot, run_async, scopes, daemon_in_home,
                                             made, seams, sub):
    root = made(GROUP)
    bot = gbot()
    run_async(bot.create_session("x", scope_chat_id=GROUP,
                                 cwd=os.path.join(root, sub) if sub else root,
                                 driver_user_id=BOB))
    seams.launch.assert_awaited_once()


def test_create_session_root_with_trailing_slash_launches(gbot, run_async, scopes,
                                                          daemon_in_home, made, seams):
    root = made(GROUP)
    bot = gbot()
    run_async(bot.create_session("x", scope_chat_id=GROUP, cwd=root + "/",
                                 driver_user_id=BOB))
    seams.launch.assert_awaited_once()


def test_a_group_user_is_held_to_the_groups_root_not_their_dm_root(
        gbot, run_async, scopes, daemon_in_home, seams, hal_root):
    """HAL's DM root is not the group's root: in the group, the group's
    root applies."""
    os.makedirs(hal_root)
    bot = gbot()
    _n, err = run_async(bot.create_session("x", scope_chat_id=GROUP, cwd=str(hal_root),
                                           driver_user_id=HAL))
    assert err == kit.outside_root_msg(_root_of(scopes, GROUP))


def test_an_unknown_person_starts_nothing(gbot, run_async, daemon_in_home, seams, made):
    root = made(GROUP)
    bot = gbot()
    run_async(bot.create_session("x", scope_chat_id=GROUP, cwd=root, driver_user_id=4242))
    seams.launch.assert_not_awaited()


# ---- criterion 6: owner and admin ----------------------------------------------------

@pytest.mark.parametrize("who,chat", [(OWNER, GROUP), (ADMIN, GROUP), (OWNER, OWNER),
                                      (ADMIN, ADMIN)],
                         ids=["owner-group", "admin-group", "owner-dm", "admin-dm"])
def test_admin_picker_unchanged(gbot, run_async, scopes, daemon_in_home, outside, who, chat):
    bot = gbot(_registry(outside, chat=chat))
    _folder_page(bot, run_async, who, chat)
    assert str(outside) in _options(bot, who, chat)


@pytest.mark.parametrize("who", [OWNER, ADMIN])
def test_admin_folder_page_keeps_the_default_row(gbot, run_async, scopes, daemon_in_home,
                                                 outside, who):
    bot = gbot(_registry(outside))
    _text, kb = _folder_page(bot, run_async, who, GROUP)
    assert any(b.startswith("Default folder") for b in _labels(kb))


@pytest.mark.parametrize("who", [OWNER, ADMIN])
def test_admin_new_card_defaults_to_the_daemon_folder(gbot, run_async, scopes,
                                                      daemon_in_home, who):
    bot = gbot()
    _open(bot, run_async, who, GROUP)
    cwd = bot._new_wizard_pending[(GROUP, who)]["cwd"]
    assert cwd in (None, "", str(daemon_in_home))


@pytest.mark.parametrize("who,chat", [(OWNER, GROUP), (ADMIN, GROUP), (OWNER, OWNER)],
                         ids=["owner-group", "admin-group", "owner-dm"])
def test_admin_starts_outside_any_root(gbot, run_async, scopes, daemon_in_home, outside,
                                       seams, who, chat):
    bot = gbot()
    run_async(bot.create_session("x", scope_chat_id=chat, cwd=str(outside),
                                 driver_user_id=who))
    seams.launch.assert_awaited_once()


def test_personal_mode_is_unchanged(mk_bot, run_async, daemon_in_home, outside, seams):
    bot = mk_bot(SessionRegistry())
    run_async(bot.create_session("x", scope_chat_id=12345, cwd=str(outside),
                                 driver_user_id=None))
    seams.launch.assert_awaited_once()


# ---- /settings -> New sessions -> Folder ------------------------------------------------

def test_defaults_screen_lists_root_for_user(gbot, scopes, daemon_in_home, made, outside):
    root = made(GROUP)
    bot = gbot(_registry(outside))
    _text, kb = new_flow.render_new_session_defaults(bot, GROUP, view="cwd", viewer=BOB)
    assert new_flow._short_path(root) in _labels(kb)


def test_defaults_screen_hides_outside_folders_from_a_user(gbot, scopes, daemon_in_home,
                                                           made, outside):
    made(GROUP)
    bot = gbot(_registry(outside))
    _text, kb = new_flow.render_new_session_defaults(bot, GROUP, view="cwd", viewer=BOB)
    assert new_flow._short_path(str(outside)) not in _labels(kb)


def test_defaults_screen_unchanged_for_owner(gbot, scopes, daemon_in_home, made, outside):
    made(GROUP)
    bot = gbot(_registry(outside))
    _text, kb = new_flow.render_new_session_defaults(bot, GROUP, view="cwd", viewer=OWNER)
    assert new_flow._short_path(str(outside)) in _labels(kb)


# ---- odd but valid root spellings -------------------------------------------------------

def _with_group_root(scopes, root):
    from dataclasses import replace
    return [replace(s, root=str(root)) if s.chat_id == GROUP else s for s in scopes]


def test_a_root_with_a_trailing_slash_starts_inside(gbot, run_async, scopes, daemon_in_home,
                                                    seams, team_root):
    (team_root / "p").mkdir(parents=True)
    bot = gbot(scope_list=_with_group_root(scopes, str(team_root) + "/"))
    run_async(bot.create_session("x", scope_chat_id=GROUP, cwd=str(team_root / "p"),
                                 driver_user_id=BOB))
    seams.launch.assert_awaited_once()


def test_a_root_with_a_trailing_slash_still_refuses_a_prefix_sibling(
        gbot, run_async, scopes, daemon_in_home, seams, team_root):
    sib = str(team_root) + "2"
    os.makedirs(sib)
    bot = gbot(scope_list=_with_group_root(scopes, str(team_root) + "/"))
    run_async(bot.create_session("x", scope_chat_id=GROUP, cwd=sib, driver_user_id=BOB))
    seams.launch.assert_not_awaited()


def test_a_symlinked_root_accepts_its_real_folder(gbot, run_async, scopes, daemon_in_home,
                                                  seams, home):
    real = home / "real-team"
    (real / "p").mkdir(parents=True)
    link = home / "team-link"
    os.symlink(real, link)
    bot = gbot(scope_list=_with_group_root(scopes, link))
    run_async(bot.create_session("x", scope_chat_id=GROUP, cwd=str(real / "p"),
                                 driver_user_id=BOB))
    seams.launch.assert_awaited_once()


def test_a_root_that_is_a_file_starts_nothing(gbot, run_async, scopes, daemon_in_home,
                                              seams, team_root):
    team_root.parent.mkdir(parents=True)
    team_root.write_text("not a folder\n")
    bot = gbot()
    run_async(bot.create_session("x", scope_chat_id=GROUP, cwd=str(team_root),
                                 driver_user_id=BOB))
    seams.launch.assert_not_awaited()


def test_a_root_that_cannot_be_made_says_so(gbot, run_async, mk_update, scopes,
                                            daemon_in_home, team_root, seams):
    """Starting from the card when the root is a file: the documented
    "could not be prepared" sentence is shown."""
    team_root.parent.mkdir(parents=True)
    team_root.write_text("not a folder\n")
    bot = gbot()
    _open(bot, run_async, BOB, GROUP)
    update = _typed(bot, run_async, mk_update, BOB, GROUP, "dev")
    shown = kit.texts_of(bot._app.bot.edit_message_text, bot._app.bot.send_message,
                         update.message.reply_text)
    assert any(f"Your folder {kit.short(team_root)} could not be prepared." in t for t in shown)
