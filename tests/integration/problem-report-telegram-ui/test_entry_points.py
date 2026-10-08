"""SC-1..4 and SC-20: who sees "Report a problem", who may tap it, and
where the preview goes (design.md Success criteria 1-4, 20; spec.md
"/help button for owner only (not admin/user/group member)", "the
memory-cap notice's Report this button"; entrypoints.md "Telegram
commands", "Inline buttons").

Methods: equivalence partitioning over the sender's role (owner, admin,
user, read_only, personal-mode stranger) and the chat (DM, group);
boundary: right chat, wrong sender; error guessing: no owner at all, an
ambiguous owner, a non-owner forging the owner's button."""

from __future__ import annotations

import pytest

from aipager.report import builder

OPEN = "_:rp:open"


def _help_buttons(bot, update):
    """(text, data) of every inline button the /help reply carried."""
    out = []
    for e in bot.tg.events:
        if e["op"] == "send" and e["chat_id"] == update.effective_chat.id:
            out.extend(h_rows(e["markup"]))
    return out


def h_rows(markup):
    if markup is None or not hasattr(markup, "inline_keyboard"):
        return []
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


@pytest.fixture
def build_spy(monkeypatch):
    """Counts report builds (a spy: the real builder still runs)."""
    calls = []
    real = builder.build_report

    def _spy(*a, **kw):
        calls.append((a, kw))
        return real(*a, **kw)
    monkeypatch.setattr(builder, "build_report", _spy)
    return calls


# ---- SC-1: the /help button --------------------------------------------------

def test_help_from_owner_in_dm_carries_report_button(bot, drive, h, run_async):
    d = drive(bot)
    upd = run_async(d.command("/help"))
    assert ("🐞 Report a problem", OPEN) in _help_buttons(bot, upd)


def test_help_from_owner_in_group_carries_report_button(make_bot, drive, h, run_async):
    bot = make_bot("scope")
    upd = run_async(drive(bot).command("/help", user=h.OWNER, chat=h.GROUP))
    assert (("🐞 Report a problem", OPEN) in _help_buttons(bot, upd))


def test_help_from_owner_in_scope_dm_carries_report_button(make_bot, drive, h, run_async):
    bot = make_bot("scope")
    upd = run_async(drive(bot).command("/help", user=h.OWNER, chat=h.OWNER))
    assert ("🐞 Report a problem", OPEN) in _help_buttons(bot, upd)


@pytest.mark.parametrize("member", ["ADMIN", "USER", "READ_ONLY"])
def test_help_from_group_non_owner_has_no_report_button(make_bot, drive, h, run_async, member):
    bot = make_bot("scope")
    upd = run_async(drive(bot).command("/help", user=getattr(h, member), chat=h.GROUP))
    assert all(data != OPEN for _, data in _help_buttons(bot, upd))


def test_help_from_personal_mode_stranger_has_no_report_button(bot, drive, h, run_async):
    upd = run_async(drive(bot).command("/help", user=h.STRANGER, chat=h.STRANGER))
    assert all(data != OPEN for _, data in _help_buttons(bot, upd))


def test_help_from_stranger_in_owner_dm_has_no_report_button(bot, drive, h, run_async):
    """Boundary: right chat, wrong sender."""
    upd = run_async(drive(bot).command("/help", user=h.STRANGER, chat=h.OWNER))
    assert all(data != OPEN for _, data in _help_buttons(bot, upd))


# ---- SC-2: a non-owner tap is refused, nothing built or sent -----------------

@pytest.mark.parametrize("member", ["ADMIN", "USER", "READ_ONLY"])
def test_group_non_owner_tap_posts_no_card(make_bot, drive, h, run_async, member):
    bot = make_bot("scope")
    run_async(drive(bot).tap(OPEN, user=getattr(h, member), chat=h.GROUP))
    assert bot.tg.cards() == []


@pytest.mark.parametrize("member", ["ADMIN", "USER", "READ_ONLY"])
def test_group_non_owner_tap_builds_no_report(make_bot, drive, h, run_async, member, build_spy):
    bot = make_bot("scope")
    run_async(drive(bot).tap(OPEN, user=getattr(h, member), chat=h.GROUP))
    assert build_spy == []


@pytest.mark.parametrize("member", ["ADMIN", "USER", "READ_ONLY"])
def test_group_non_owner_tap_gets_a_toast(make_bot, drive, h, run_async, member):
    bot = make_bot("scope")
    run_async(drive(bot).tap(OPEN, user=getattr(h, member), chat=h.GROUP))
    assert bot.tg.toast_texts() != []


def test_group_admin_tap_toast_says_owner_only(make_bot, drive, h, run_async):
    """An admin passes the general tap gate, so the refusal is the
    report flow's own (entrypoints.md wording)."""
    bot = make_bot("scope")
    run_async(drive(bot).tap(OPEN, user=h.ADMIN, chat=h.GROUP))
    assert "Only the owner of this aipager can report a problem." in bot.tg.toast_texts()


def test_personal_stranger_tap_builds_no_report(bot, drive, h, run_async, build_spy):
    run_async(drive(bot).tap(OPEN, user=h.STRANGER, chat=h.OWNER))
    assert build_spy == []


def test_personal_stranger_tap_posts_nothing(bot, drive, h, run_async):
    run_async(drive(bot).tap(OPEN, user=h.STRANGER, chat=h.OWNER))
    assert bot.tg.sent() == []


def test_owner_tap_builds_exactly_one_report(bot, drive, h, run_async, build_spy):
    """Positive control for the build spy: it does see the opener's build."""
    run_async(drive(bot).tap(OPEN))
    assert len(build_spy) == 1


@pytest.mark.parametrize("verb", ["_:rp:send", "_:rp:note", "_:rp:cancel"])
def test_stranger_card_verbs_leave_the_owner_card_open(bot, drive, h, run_async, net, verb):
    """Any `_:rp:` verb re-checks the owner: a stranger's verb on the
    owner's card does nothing, so the owner can still send it."""
    d = drive(bot)

    async def go():
        cid, mid = await h.open_card(bot, d)
        await d.tap(verb, user=h.STRANGER, chat=cid, message_id=mid)
        before = len(net.posts)
        await d.tap("_:rp:send", chat=cid, message_id=mid)
        return before, len(net.posts)
    before, after = run_async(go())
    assert (before, after) == (0, 1)


# ---- SC-3: the owner's tap in a group opens the card in the DM ---------------

def test_owner_group_tap_opens_card_in_owner_dm(make_bot, drive, h, run_async):
    bot = make_bot("scope")
    run_async(drive(bot).tap(OPEN, user=h.OWNER, chat=h.GROUP))
    assert len(bot.tg.cards(h.OWNER)) == 1


def test_owner_group_tap_posts_no_card_in_group(make_bot, drive, h, run_async):
    bot = make_bot("scope")
    run_async(drive(bot).tap(OPEN, user=h.OWNER, chat=h.GROUP))
    assert bot.tg.cards(h.GROUP) == []


def test_owner_group_tap_posts_nothing_in_group(make_bot, drive, h, run_async):
    bot = make_bot("scope")
    run_async(drive(bot).tap(OPEN, user=h.OWNER, chat=h.GROUP))
    assert bot.tg.sent(h.GROUP) == []


def test_owner_group_tap_toast_points_to_dm(make_bot, drive, h, run_async):
    bot = make_bot("scope")
    run_async(drive(bot).tap(OPEN, user=h.OWNER, chat=h.GROUP))
    assert "The report preview is in your private chat with the bot." in bot.tg.toast_texts()


# ---- SC-4: no owner (none, ambiguous): no button, no card ---------------------

@pytest.fixture
def no_owner_personal(make_bot, monkeypatch):
    """Personal mode whose configured chat is a group: nobody owns it."""
    monkeypatch.setattr("aipager.config.CHAT_ID", str(-1001234))
    return make_bot("personal")


def test_no_owner_personal_help_has_no_button(no_owner_personal, drive, h, run_async):
    bot = no_owner_personal
    upd = run_async(drive(bot).command("/help", user=h.OWNER, chat=-1001234))
    assert all(data != OPEN for _, data in _help_buttons(bot, upd))


@pytest.mark.parametrize("mode", ["scope_no_dm", "scope_two_owners"])
def test_no_single_owner_help_in_group_has_no_button(make_bot, drive, h, run_async, mode):
    bot = make_bot(mode)
    upd = run_async(drive(bot).command("/help", user=h.OWNER, chat=h.GROUP))
    assert all(data != OPEN for _, data in _help_buttons(bot, upd))


def test_ambiguous_owner_help_in_dm_has_no_button(make_bot, drive, h, run_async):
    bot = make_bot("scope_two_owners")
    upd = run_async(drive(bot).command("/help", user=h.OWNER, chat=h.OWNER))
    assert all(data != OPEN for _, data in _help_buttons(bot, upd))


@pytest.mark.parametrize("mode", ["scope_no_dm", "scope_two_owners"])
def test_no_single_owner_stray_tap_posts_no_card(make_bot, drive, h, run_async, mode):
    bot = make_bot(mode)
    run_async(drive(bot).tap(OPEN, user=h.OWNER, chat=h.GROUP))
    assert bot.tg.cards() == []


@pytest.mark.parametrize("mode", ["scope_no_dm", "scope_two_owners"])
def test_no_single_owner_stray_tap_builds_nothing(make_bot, drive, h, run_async, mode, build_spy):
    bot = make_bot(mode)
    run_async(drive(bot).tap(OPEN, user=h.OWNER, chat=h.GROUP))
    assert build_spy == []


def test_open_preview_without_owner_says_no_owner(make_bot, h, run_async):
    from aipager.bot import report_flow
    bot = make_bot("scope_no_dm")
    result = run_async(report_flow.open_preview(bot, trigger="manual", errors=[]))
    assert result is report_flow.OpenResult.NO_OWNER


@pytest.mark.parametrize("mode, expected", [("personal", "OWNER"), ("scope", "OWNER"),
                                            ("scope_no_dm", None), ("scope_two_owners", None)])
def test_resolve_owner_per_mode(make_bot, h, mode, expected):
    from aipager.bot import report_flow
    bot = make_bot(mode)
    assert report_flow.resolve_owner(bot) == (getattr(h, expected) if expected else None)


def test_is_owner_rejects_a_bool(bot):
    """Error guessing: True == 1 in Python; an id must be an int, never a bool."""
    from aipager.bot import report_flow
    assert report_flow.is_owner(bot, True) is False


def test_admin_role_operator_self_dm_is_the_owner(mk_bot, h):
    """An operator who chose role admin for their own DM still owns the
    install (design: _operator_dm's rule)."""
    from aipager.bot import report_flow
    bot = mk_bot(scopes=[h.owner_dm_scope(role="admin"), h.group_scope(owner_role="admin")])
    bot.policy = h._policy()
    assert report_flow.resolve_owner(bot) == h.OWNER


# ---- SC-20: the memory-cap notice's Report this button ----------------------------

def _cap_notice(bot, h, run_async):
    sess = h.add_session(bot.registry, "jim")
    run_async(bot.notify(sess, "hook_memory_cap_hit", {"hook": "aipager-hook"}))
    return [e for e in bot.tg.sent() if "memory cap" in (e["text"] or "")]


def test_memory_cap_notice_carries_report_this(bot, h, run_async):
    notices = _cap_notice(bot, h, run_async)
    assert any(data == OPEN for n in notices for _, data in h_rows(n["markup"]))


def test_memory_cap_notice_is_one_message(bot, h, run_async):
    """The button is on the notice itself, not a second message."""
    _cap_notice(bot, h, run_async)
    assert len(bot.tg.sent()) == 1


def test_memory_cap_report_this_label(bot, h, run_async):
    notices = _cap_notice(bot, h, run_async)
    labels = [t for n in notices for t, data in h_rows(n["markup"]) if data == OPEN]
    assert labels and all("Report this" in t for t in labels)


def test_memory_cap_report_this_owner_tap_opens_card(bot, drive, h, run_async):
    notices = _cap_notice(bot, h, run_async)
    n = notices[0]
    run_async(drive(bot).tap(OPEN, chat=n["chat_id"], message_id=n["message_id"]))
    assert len(bot.tg.cards(h.OWNER)) == 1


def test_memory_cap_report_this_stranger_tap_opens_nothing(bot, drive, h, run_async, build_spy):
    notices = _cap_notice(bot, h, run_async)
    n = notices[0]
    run_async(drive(bot).tap(OPEN, user=h.STRANGER, chat=n["chat_id"],
                             message_id=n["message_id"]))
    assert (bot.tg.cards(), build_spy) == ([], [])


def test_memory_cap_without_owner_has_no_button(no_owner_personal, h, run_async):
    bot = no_owner_personal
    sess = h.add_session(bot.registry, "jim", chat_id=-1001234)
    run_async(bot.notify(sess, "hook_memory_cap_hit", {"hook": "aipager-hook"}))
    assert all(data != OPEN for e in bot.tg.sent() for _, data in h_rows(e["markup"]))


def test_memory_cap_group_session_report_opens_in_dm(make_bot, drive, h, run_async):
    """A notice that went to a group: the owner's tap opens the card in
    the DM, nothing in the group."""
    bot = make_bot("scope")
    sess = h.add_session(bot.registry, "jim", chat_id=h.GROUP)
    run_async(bot.notify(sess, "hook_memory_cap_hit", {"hook": "aipager-hook"}))
    n = [e for e in bot.tg.sent() if "memory cap" in (e["text"] or "")][0]
    run_async(drive(bot).tap(OPEN, user=h.OWNER, chat=n["chat_id"], message_id=n["message_id"]))
    assert (len(bot.tg.cards(h.OWNER)), bot.tg.cards(h.GROUP)) == (1, [])
