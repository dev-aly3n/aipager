"""Telegram /usage, black box (design.md Success criteria 14-16;
decisions.md 4 and 9; entrypoints.md "Telegram commands").

Equivalence partitions over the caller's role (owner, admin of this chat,
admin of only some chats, user, read_only, stranger), the chat kind
(private vs group) and the mode (scope vs personal). Boundaries on the
compact token counts (999 / 1,000, 99,9xx / 100,000, 999,xxx / 1,000,000,
9.9M / 10M). Error guessing: trailing text after the command,
``/usage@bot``, a session named ``usage``, ``/usage`` typed to Claude Code,
a gpt-5 gateway with a dollar cost, every catalog language."""

from __future__ import annotations

import logging
import re
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, usage_store
from aipager.i18n import locales
from aipager.bot import group_intake, usage_cmd
from aipager.claude_bootstrap import SessionLogin
from aipager.statusline_file import Window

NOW = time.time()
DENIED = "🚫 Only an admin can see usage."
GROUP_NOTE = ("Plan limits are not shown in a group. Send /usage in your private chat "
              "with the bot, or open the Mini App.")


@pytest.fixture
def world(make_bot, tmp_path, h):
    """Scope mode: tokens in the team group (100 in), the frontend group
    (1,000 in) and the owner's DM (10,000 in); one plan reading."""
    bot = make_bot("scope")
    for name, scope, n in (("claude-api__g", h.GROUP, 100), ("claude-web__g2", h.GROUP2, 1000),
                           ("claude-dev__d", h.OWNER, 10_000)):
        p = tmp_path / f"{name}.jsonl"
        h.append(p, h.entry(f"msg_{name}", NOW - 30, n, 0))
        h.idle(h.add_session(bot.registry, name, scope=scope, transcript=p))
    usage_store.note_plan("frontend", {"seven_day": Window(83.0, int(NOW + 2 * 86400))},
                          SessionLogin("saved", "", "/home/alice/.claude"), now=NOW)
    return bot


def _today_line(n_in: str) -> str:
    return f"Today: {n_in} in, 0 out, 0 cache read, 0 cache write"


def _usage(h, bot, run_async, user, chat, text="/usage"):
    return h.run_usage(bot, run_async, user_id=user, chat_id=chat, text=text)


# ---- the owner in private -----------------------------------------------------------

def test_owner_in_private_gets_the_title(h, world, run_async):
    assert "📊 <b>Usage</b>" in _usage(h, world, run_async, h.OWNER, h.OWNER)


def test_owner_in_private_gets_the_plan_line(h, world, run_async):
    assert "This week (all models): 83% used" in _usage(h, world, run_async, h.OWNER, h.OWNER)


def test_owner_in_private_gets_the_freshness_line(h, world, run_async):
    assert "from session frontend." in _usage(h, world, run_async, h.OWNER, h.OWNER)


def test_owner_tokens_title_is_tokens(h, world, run_async):
    out = _usage(h, world, run_async, h.OWNER, h.OWNER)
    assert re.search(r"(^|>)Tokens(<|$)", out, re.M) is not None


def test_owner_sees_every_chats_tokens(h, world, run_async):
    assert _today_line("11.1k") in _usage(h, world, run_async, h.OWNER, h.OWNER)


def test_owner_sees_the_week_line(h, world, run_async):
    assert "Last 7 days: 11.1k in, 0 out, 0 cache read, 0 cache write" in _usage(
        h, world, run_async, h.OWNER, h.OWNER)


def test_trailing_text_is_ignored(h, world, run_async):
    assert _today_line("11.1k") in _usage(h, world, run_async, h.OWNER, h.OWNER,
                                          "/usage please now")


def test_the_bot_name_suffix_is_accepted(h, world, run_async):
    assert _today_line("11.1k") in _usage(h, world, run_async, h.OWNER, h.OWNER,
                                          "/usage@aipager_test_bot")


def test_a_new_line_changes_the_reply(h, world, run_async, tmp_path):
    h.append(tmp_path / "claude-api__g.jsonl", h.entry("msg_new", NOW - 1, 900, 0))
    assert _today_line("12.0k") in _usage(h, world, run_async, h.OWNER, h.OWNER)


# ---- groups ----------------------------------------------------------------------------

@pytest.mark.parametrize("who", ["OWNER", "ADMIN"])
def test_in_a_group_the_title_is_tokens_in_this_chat(h, world, run_async, who):
    assert "Tokens in this chat" in _usage(h, world, run_async, getattr(h, who), h.GROUP)


@pytest.mark.parametrize("who", ["OWNER", "ADMIN"])
def test_in_a_group_only_that_chats_tokens(h, world, run_async, who):
    assert _today_line("100") in _usage(h, world, run_async, getattr(h, who), h.GROUP)


@pytest.mark.parametrize("who", ["OWNER", "ADMIN"])
def test_in_a_group_no_plan_lines(h, world, run_async, who):
    assert "used" not in _usage(h, world, run_async, getattr(h, who), h.GROUP)


@pytest.mark.parametrize("who", ["OWNER", "ADMIN"])
def test_in_a_group_no_freshness_line(h, world, run_async, who):
    assert "from session" not in _usage(h, world, run_async, getattr(h, who), h.GROUP)


@pytest.mark.parametrize("who", ["OWNER", "ADMIN"])
def test_in_a_group_the_note_says_where_plan_limits_are(h, world, run_async, who):
    assert GROUP_NOTE in _usage(h, world, run_async, getattr(h, who), h.GROUP)


def test_in_a_group_with_no_reading_no_plan_none_line(h, make_bot, run_async):
    bot = make_bot("scope")
    assert "Plan limits show here" not in _usage(h, bot, run_async, h.OWNER, h.GROUP)


def test_owner_of_the_other_group_sees_only_that_group(h, world, run_async):
    assert _today_line("1.0k") in _usage(h, world, run_async, h.OWNER, h.GROUP2)


# ---- refusals ------------------------------------------------------------------------------

@pytest.mark.parametrize("who", ["USER", "RO"], ids=["user", "read_only"])
def test_a_plain_member_is_refused(h, world, run_async, who):
    assert DENIED in _usage(h, world, run_async, getattr(h, who), h.GROUP)


@pytest.mark.parametrize("who", ["USER", "RO"], ids=["user", "read_only"])
def test_a_plain_member_sees_no_tokens(h, world, run_async, who):
    assert "Today:" not in _usage(h, world, run_async, getattr(h, who), h.GROUP)


@pytest.mark.parametrize("who", ["USER", "RO"], ids=["user", "read_only"])
def test_a_plain_member_sees_no_plan(h, world, run_async, who):
    assert "83%" not in _usage(h, world, run_async, getattr(h, who), h.GROUP)


def test_an_admin_of_one_chat_who_is_a_user_in_another_is_refused_there(h, world, run_async):
    assert DENIED in _usage(h, world, run_async, h.ADMIN, h.GROUP2)


def test_a_refusal_is_logged(h, world, run_async, caplog):
    caplog.set_level(logging.INFO)
    _usage(h, world, run_async, h.USER, h.GROUP)
    assert f"usage.denied user={h.USER} chat={h.GROUP}" in caplog.text


def test_a_stranger_sees_no_usage(h, world, run_async):
    assert "Today:" not in _usage(h, world, run_async, h.STRANGER, h.GROUP)


# ---- personal mode -----------------------------------------------------------------------------

@pytest.fixture
def personal(make_bot, tmp_path, h):
    bot = make_bot("personal")
    p = tmp_path / "solo.jsonl"
    h.append(p, h.entry("msg_solo", NOW - 30, 42, 7))
    h.idle(h.add_session(bot.registry, "claude-dev", scope=h.OWNER, transcript=p))
    usage_store.note_plan("dev", {"five_hour": Window(7.0, int(NOW + 3 * 3600))},
                          SessionLogin("token", "env", "/home/alice/.claude"), now=NOW)
    return bot


def test_personal_mode_operator_gets_the_plan(h, personal, run_async):
    assert "5-hour window: 7% used" in _usage(h, personal, run_async, h.OWNER, h.OWNER)


def test_personal_mode_operator_gets_tokens(h, personal, run_async):
    assert "Today: 42 in, 7 out, 0 cache read, 0 cache write" in _usage(
        h, personal, run_async, h.OWNER, h.OWNER)


def test_personal_mode_title_is_tokens(h, personal, run_async):
    out = _usage(h, personal, run_async, h.OWNER, h.OWNER)
    assert "Tokens in the chats you manage" not in out and "Tokens" in out


def test_personal_mode_stranger_gets_nothing(h, personal, run_async):
    assert "Today:" not in _usage(h, personal, run_async, h.STRANGER, h.STRANGER)


def test_personal_mode_stranger_has_no_access(h, personal):
    assert usage_cmd.usage_access(personal, h.STRANGER, h.STRANGER) is None


def test_personal_mode_operator_access_is_all(h, personal):
    a = usage_cmd.usage_access(personal, h.OWNER, h.OWNER)
    assert (a.title, a.plan) == ("all", True)


# ---- usage_access (one rule for both surfaces) ---------------------------------------------------

def test_owner_access_title_is_all(h, world):
    assert usage_cmd.usage_access(world, h.OWNER, h.OWNER).title == "all"


def test_owner_access_has_the_plan(h, world):
    assert usage_cmd.usage_access(world, h.OWNER, h.GROUP).plan is True


def test_admin_access_title_is_managed(h, world):
    assert usage_cmd.usage_access(world, h.ADMIN, h.GROUP).title == "managed"


def test_admin_access_is_only_the_managed_chats(h, world):
    assert usage_cmd.usage_access(world, h.ADMIN, h.GROUP).scopes == frozenset({h.GROUP})


def test_admin_access_has_the_plan(h, world):
    assert usage_cmd.usage_access(world, h.ADMIN, h.GROUP).plan is True


@pytest.mark.parametrize("who", ["USER", "RO", "STRANGER"])
def test_non_admins_have_no_access(h, world, who):
    assert usage_cmd.usage_access(world, getattr(h, who), h.GROUP) is None


def test_group_access_is_narrowed_to_the_chat(h, world):
    a = usage_cmd.usage_access(world, h.OWNER, h.GROUP, for_group=True)
    assert (a.scopes, a.title, a.plan) == (frozenset({h.GROUP}), "chat", False)


def test_an_admin_sees_only_managed_tokens_under_the_managed_title(h, world):
    world_snap = (usage_store.scan(world.registry, now=NOW, force=True),
                  usage_store.snapshot(usage_cmd.usage_access(world, h.ADMIN, h.GROUP).scopes,
                                       now=NOW))[1]
    out = usage_cmd.usage_html(world_snap, usage_cmd.usage_access(world, h.ADMIN, h.GROUP),
                               h.en(), NOW)
    assert ("Tokens in the chats you manage" in out, _today_line("100") in out) == (True, True)


# ---- no dollars ----------------------------------------------------------------------------------

def test_no_dollar_in_the_owner_reply_with_a_gateway_and_gpt5(h, world, run_async, tmp_path):
    p = tmp_path / "gw.jsonl"
    h.append(p, h.entry("msg_gpt", NOW - 30, 12_345_678, 9_999, 1_000_000, 5,
                        model="gpt-5"))
    h.add_session(world.registry, "claude-gw__d", scope=h.OWNER, transcript=p)
    usage_store.note_plan("gw", {"spend_limit": Window(30.0, int(NOW + 3600))},
                          SessionLogin("gateway", "gw.example.com", "/home/alice/.claude"),
                          now=NOW)
    assert "$" not in _usage(h, world, run_async, h.OWNER, h.OWNER)


LANGS = list(locales.LOCALES)


@pytest.mark.parametrize("code", LANGS)
def test_no_dollar_in_any_language(h, world, code):
    usage_store.scan(world.registry, now=NOW, force=True)
    access = usage_cmd.usage_access(world, h.OWNER, h.OWNER)
    out = usage_cmd.usage_html(usage_store.snapshot(access.scopes, now=NOW), access,
                               i18n.get(code), NOW)
    assert "$" not in out


@pytest.mark.parametrize("code", [c for c in LANGS if c != "en"])
def test_every_language_translates_the_no_reading_line(h, make_bot, code):
    """Every catalog carries the new keys: no English sentence leaks."""
    bot = make_bot("scope")
    access = usage_cmd.usage_access(bot, h.OWNER, h.OWNER)
    out = usage_cmd.usage_html(usage_store.snapshot(access.scopes, now=NOW), access,
                               i18n.get(code), NOW)
    assert "Plan limits show here after Claude answers" not in out


# ---- compact counts -------------------------------------------------------------------------------

@pytest.mark.parametrize("n, text", [
    (0, "0"), (999, "999"), (1000, "1.0k"), (1234, "1.2k"), (15_000, "15.0k"),
    (99_900, "99.9k"), (100_000, "100k"), (150_000, "150k"), (999_000, "999k"),
    (1_000_000, "1.0M"), (1_234_567, "1.2M"), (9_900_000, "9.9M"), (10_000_000, "10M"),
    (15_000_000, "15M"), (1_234_000_000, "1,234M"),
])
def test_compact_token_counts(h, n, text):
    assert usage_cmd.fmt_tokens(n, h.en()) == text


# ---- no tokens ---------------------------------------------------------------------------------------

def test_no_tokens_says_so(h, make_bot, run_async):
    bot = make_bot("scope")
    assert "No tokens recorded yet." in _usage(h, bot, run_async, h.OWNER, h.OWNER)


def test_no_tokens_and_no_reading_says_how_plan_limits_arrive(h, make_bot, run_async):
    bot = make_bot("scope")
    assert "Plan limits show here after Claude answers" in _usage(h, bot, run_async,
                                                                 h.OWNER, h.OWNER)


# ---- plumbing: menu, help, registration, reserved names, pass-through -------------------------------

@pytest.fixture
def menu(monkeypatch):
    from aipager.bot.lifecycle import LifecycleMixin
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    return LifecycleMixin._command_list


@pytest.mark.parametrize("group", [False, True], ids=["private", "group"])
def test_usage_is_in_the_menu(menu, group):
    assert "usage" in [c.command for c in menu({"frontend"}, group=group)]


def test_usage_comes_right_before_help(menu):
    names = [c.command for c in menu(set())]
    assert names.index("usage") == names.index("help") - 1


def test_the_menu_describes_usage(menu):
    (desc,) = [c.description for c in menu(set()) if c.command == "usage"]
    assert desc == "Plan limits and tokens"


def test_help_names_usage_for_admins(h, make_bot, run_async):
    bot = make_bot("personal")
    bot._authorize = AsyncMock(return_value=True)
    u = h.tg_update("/help", user_id=h.OWNER, chat_id=h.OWNER)
    run_async(bot._handle_help_cmd(u, MagicMock()))
    assert ("<b>Admin:</b> /update (aipager and Claude Code) · /usage (plan limits and tokens)"
            in "\n".join(h.sent_texts(bot, u)))


def _registered(make_bot, run_async):
    """Every (handler, group) ``start()`` registers, captured from the outside
    (same capture as the /language precedent's test)."""
    b = make_bot("personal")
    registered = []

    def _add(hd, *a, **k):
        registered.append((hd, k.get("group", a[0] if a else 0)))

    stub = MagicMock()
    stub.add_handler = MagicMock(side_effect=_add)
    stub.bot = MagicMock()
    stub.bot.set_my_commands = AsyncMock()
    stub.initialize = AsyncMock()
    stub.start = AsyncMock()
    stub.updater = MagicMock()
    stub.updater.start_polling = AsyncMock()
    b._make_builder = MagicMock(return_value=MagicMock(build=MagicMock(return_value=stub)))
    b._update_bot_commands = AsyncMock()
    try:
        run_async(b.start())
    except Exception:  # noqa: BLE001 - only the registration matters here
        pass
    return registered


def test_usage_is_a_registered_command(make_bot, run_async):
    from telegram.ext import CommandHandler
    (handler,) = [hd for hd, _g in _registered(make_bot, run_async)
                  if isinstance(hd, CommandHandler) and "usage" in hd.commands]
    assert getattr(handler.callback, "func", handler.callback) is usage_cmd.handle_usage_cmd


def test_usage_is_handled_before_any_message_goes_to_a_session(make_bot, run_async):
    """In its group, the /usage handler comes before every text handler, so
    a bare /usage never reaches the path that types into Claude Code."""
    from telegram.ext import CommandHandler, MessageHandler
    reg = _registered(make_bot, run_async)
    (i, group), = [(n, g) for n, (hd, g) in enumerate(reg)
                   if isinstance(hd, CommandHandler) and "usage" in hd.commands]
    later = [n for n, (hd, g) in enumerate(reg) if isinstance(hd, MessageHandler) and g == group]
    assert later and min(later) > i


def test_usage_is_one_of_the_groups_own_commands():
    assert "usage" in group_intake.OWN_COMMANDS


def test_usage_is_reserved_in_dtach():
    from aipager.dtach import inject
    assert "usage" in inject._RESERVED


def test_usage_is_a_looking_command_in_new():
    from aipager.bot import new_flow
    assert "usage" in new_flow._LOOKING_COMMANDS


@pytest.mark.parametrize("name", ["usage", "USAGE", "Usage"])
def test_usage_is_a_reserved_session_name(name):
    from aipager.miniapp.launch import validate_session_name
    assert validate_session_name(name)[0] == ""


@pytest.mark.parametrize("name", ["usages", "usage2", "my_usage", "use"])
def test_neighbouring_names_are_still_allowed(name):
    from aipager.miniapp.launch import validate_session_name
    assert validate_session_name(name) == (name, "")


def test_launching_a_session_named_usage_is_refused(run_async):
    from aipager.dtach import inject
    ok, err = run_async(inject.launch_session("usage", mode="ask"))
    assert ok is False


def test_the_page_refuses_renaming_to_usage():
    from aipager.miniapp.static import SOURCE_INDEX_HTML
    m = re.search(r"RENAME_RESERVED\s*=\s*\{(.*?)\}", SOURCE_INDEX_HTML, re.S)
    assert "usage" in set(re.findall(r'"([^"]+)"\s*:\s*true', m.group(1)))


@pytest.fixture
def typed(monkeypatch):
    from aipager.dtach import inject
    out: list[tuple[str, str]] = []

    async def _send(name, text, *a, **kw):
        out.append((name, text))
        return True

    monkeypatch.setattr(inject, "send_text_and_enter", _send)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    return out


def test_label_slash_usage_still_reaches_claude_code(h, make_bot, run_async, typed):
    """``/<label> /usage`` types Claude Code's own /usage into that session."""
    bot = make_bot("scope")
    s = h.idle(bot.registry.get_or_create("claude-dev__d"))
    s.label, s.scope_chat_id, s.scope_kind = "dev", h.OWNER, "dm"
    bot.registry.last_active_session = "claude-dev__d"
    u = h.tg_update("/dev /usage", user_id=h.OWNER, chat_id=h.OWNER)
    run_async(bot._handle_message(u, h.ctx()))
    assert typed == [("claude-dev__d", "/usage")]


@pytest.mark.parametrize("cmd", ["/usage", "/usage@aipager_test_bot", "/usage now"])
def test_a_bare_usage_in_a_group_is_not_typed_to_claude(h, make_bot, run_async, typed, cmd):
    bot = make_bot("scope")
    s = h.idle(bot.registry.get_or_create("claude-api__g"))
    s.label, s.scope_chat_id, s.scope_kind = "api", h.GROUP, "group"
    s.last_driver_user_id = h.OWNER
    bot.registry.last_active_session = "claude-api__g"
    u = h.tg_update(cmd, user_id=h.OWNER, chat_id=h.GROUP)
    run_async(bot._handle_message(u, h.ctx()))
    assert typed == []
