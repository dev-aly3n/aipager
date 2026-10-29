"""Success criterion 14: in a Persian group, every pop-up after a tap reads
in the tapper's language (Russian for bob, whose own chat is Russian),
while cards and replies stay in the group's language. Also the fallback of
decision P2-13: a tapper with no choice of their own gets the group's
language, unless their Telegram app is English."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest
import qa_p3a_scenarios as S

PERSIAN = re.compile("[؀-ۿ]")
CYRILLIC = re.compile("[Ѐ-ӿ]")


def _setup(h, *, bob_ru=True, owner_ru=False):
    bot, tg = S.world(h, "en", chats=())
    S.choose(h, bot, S.GROUP, "fa")
    if bob_ru:
        S.choose(h, bot, S.DM_B, "ru")
    if owner_ru:
        S.choose(h, bot, S.DM, "ru")
    return bot, tg


def _toasts(tg):
    return [t[len("[toast] "):] for t in tg.out if t.startswith("[toast] ")]


def _tap(h, bot, tg, data, *, user=S.BOB, chat=S.GROUP, message_id=42, language_code=None):
    update, q = S.query(tg, data, chat=chat, user=user, message_id=message_id)
    q.from_user.language_code = language_code
    h.run(bot._handle_callback(update, MagicMock()))
    return q


# ---- one tap per pop-up family -----------------------------------------------------------

def _someone_elses_card(h, bot, tg):
    x1 = S.session(bot, "x1", chat=S.GROUP)
    card = tg._next + 1
    S.cmd(h, bot, tg, "_handle_kill_cmd", "/kill x1", chat=S.GROUP)
    start = len(tg.out)
    _tap(h, bot, tg, f"{x1.name}:endok{x1.turn_key}", message_id=card)
    return tg.out[start:]


def _other_chats_button(h, bot, tg):
    d1 = S.session(bot, "x2", chat=S.DM)
    _tap(h, bot, tg, f"{d1.name}:menu")
    return tg.out


def _malformed(h, bot, tg):
    _tap(h, bot, tg, "nocolon")
    return tg.out


def _unknown_verb(h, bot, tg):
    x1 = S.session(bot, "x1", chat=S.GROUP)
    _tap(h, bot, tg, f"{x1.name}:zzz")
    return tg.out


def _compact_unknown(h, bot, tg):
    _tap(h, bot, tg, "claude-zz:compact")
    return tg.out


def _retry_nothing(h, bot, tg):
    x1 = S.session(bot, "x1", chat=S.GROUP)
    _tap(h, bot, tg, f"{x1.name}:retry")
    return tg.out


def _session_gone_talk(h, bot, tg):
    from aipager.state import Status
    gone = S.session(bot, "x2", chat=S.GROUP, status=Status.GONE)
    _tap(h, bot, tg, f"{gone.name}:talk")
    return tg.out


def _send_now_unknown(h, bot, tg):
    _tap(h, bot, tg, "claude-zz:now:900")
    return tg.out


def _send_now_bad(h, bot, tg):
    from aipager.state import Status
    x1 = S.session(bot, "x1", chat=S.GROUP, status=Status.BUSY)
    x1.queued_targets = [{"msg_id": 900, "chat_id": S.GROUP, "raw_text": "x1",
                          "driver_user_id": S.BOB}]
    _tap(h, bot, tg, f"{x1.name}:now:abc")
    return tg.out


def _new_not_yours(h, bot, tg):
    from aipager.bot import new_flow
    from aipager.miniapp import launch
    h.mp.setattr(new_flow, "_project_dir", lambda: "/work/app")
    h.mp.setattr(launch, "allowed_roots", lambda *a, **k: ["/work/app"])
    h.mp.setattr(launch, "default_folder_is_wide", lambda: False)
    S.cmd(h, bot, tg, "_handle_new_cmd", "/new", chat=S.GROUP, user=S.OWNER)
    card = tg._next
    start = len(tg.out)
    _tap(h, bot, tg, "_:nw:cancel", message_id=card)
    return tg.out[start:]


def _menu_unknown_session(h, bot, tg):
    _tap(h, bot, tg, "claude-zz:menu")
    return tg.out


FAMILIES = {
    "card_owner": _someone_elses_card,
    "gate_other_chat": _other_chats_button,
    "invalid_callback": _malformed,
    "unknown_action": _unknown_verb,
    "compact": _compact_unknown,
    "retry": _retry_nothing,
    "session_gone": _session_gone_talk,
    "sendnow_unknown": _send_now_unknown,
    "sendnow_bad_button": _send_now_bad,
    "new_not_yours": _new_not_yours,
    "menu_unknown": _menu_unknown_session,
}


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_bobs_pop_up_is_russian(h, family):
    bot, tg = _setup(h)
    out = FAMILIES[family](h, bot, tg)
    toasts = [t[len("[toast] "):] for t in out if t.startswith("[toast] ")]
    assert toasts and all(CYRILLIC.search(t) for t in toasts), out


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_bobs_pop_up_is_not_persian(h, family):
    bot, tg = _setup(h)
    out = FAMILIES[family](h, bot, tg)
    toasts = [t for t in out if t.startswith("[toast] ")]
    assert not any(PERSIAN.search(t) for t in toasts)


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_without_a_choice_of_his_own_bob_reads_the_groups_language(h, family):
    bot, tg = _setup(h, bob_ru=False)
    out = FAMILIES[family](h, bot, tg)
    toasts = [t[len("[toast] "):] for t in out if t.startswith("[toast] ")]
    assert toasts and all(PERSIAN.search(t) for t in toasts), out


def test_a_tapper_whose_telegram_is_english_reads_english(h):
    bot, tg = _setup(h, bob_ru=False)
    _tap(h, bot, tg, "nocolon", language_code="en")
    assert _toasts(tg) == ["Invalid callback"]


def test_a_tapper_whose_telegram_is_russian_without_a_choice_reads_the_group(h):
    bot, tg = _setup(h, bob_ru=False)
    _tap(h, bot, tg, "nocolon", language_code="ru")
    assert PERSIAN.search(_toasts(tg)[0])


def test_the_card_owner_pop_up_names_the_owner_and_command_verbatim(h):
    bot, tg = _setup(h)
    out = _someone_elses_card(h, bot, tg)
    toast = [t for t in out if t.startswith("[toast] ")][0]
    assert "@alice" in toast and "/kill x1" in toast


def test_the_unknown_action_pop_up_keeps_the_action_verbatim(h):
    bot, tg = _setup(h)
    assert "zzz" in _unknown_verb(h, bot, tg)[-1]


# ---- cards and replies stay in the group's language -----------------------------------------

def test_the_end_confirm_card_in_the_group_is_persian(h):
    bot, tg = _setup(h, owner_ru=True)
    S.session(bot, "x1", chat=S.GROUP)
    S.cmd(h, bot, tg, "_handle_kill_cmd", "/kill x1", chat=S.GROUP)
    assert PERSIAN.search(tg.out[0])


def test_kill_cancel_edits_the_group_card_in_persian(h):
    bot, tg = _setup(h, owner_ru=True)
    x1 = S.session(bot, "x1", chat=S.GROUP)
    _tap(h, bot, tg, f"{x1.name}:kill-cancel", user=S.OWNER)
    edits = [t for t in tg.out if not t.startswith("[")]
    assert edits and all(PERSIAN.search(t) for t in edits)


def test_pick_cancel_edits_the_group_card_in_persian(h):
    bot, tg = _setup(h, owner_ru=True)
    _tap(h, bot, tg, "_:pick:cancel", user=S.OWNER)
    edits = [t for t in tg.out if not t.startswith("[")]
    assert edits and all(PERSIAN.search(t) for t in edits)


def test_the_name_card_bob_opens_in_the_group_is_persian(h):
    from aipager.bot import new_flow
    from aipager.miniapp import launch
    bot, tg = _setup(h)
    h.mp.setattr(new_flow, "_project_dir", lambda: "/work/app")
    h.mp.setattr(launch, "allowed_roots", lambda *a, **k: ["/work/app"])
    h.mp.setattr(launch, "default_folder_is_wide", lambda: False)
    S.cmd(h, bot, tg, "_handle_new_cmd", "/new", chat=S.GROUP, user=S.BOB)
    assert PERSIAN.search(tg.out[0]) and not CYRILLIC.search(tg.out[0])


def test_a_reply_to_bob_in_the_group_is_persian(h):
    bot, tg = _setup(h)
    S.cmd(h, bot, tg, "_handle_stop_cmd", "/stop", chat=S.GROUP, user=S.BOB)
    assert tg.out and all(PERSIAN.search(t) for t in tg.out)


def test_a_mode_card_bob_asks_for_in_the_group_is_persian(h):
    bot, tg = _setup(h)
    S.session(bot, "x1", chat=S.GROUP)
    S.cmd(h, bot, tg, "_handle_mode_cmd", "/mode x1", chat=S.GROUP, user=S.BOB)
    assert tg.out and PERSIAN.search(tg.out[0]) and not CYRILLIC.search(" ".join(tg.out))


def test_the_send_now_line_in_the_group_is_persian(h):
    from aipager.bot import send_now
    from aipager.state import Status
    bot, tg = _setup(h)
    x1 = S.session(bot, "x1", chat=S.GROUP, status=Status.BUSY)
    x1.queued_targets = [{"msg_id": 900, "chat_id": S.GROUP, "raw_text": "x1",
                          "driver_user_id": S.BOB}]
    h.mp.setattr(send_now, "held_by_claude", lambda *a, **k: True)
    h.run(bot._send_queued_line_checked(x1, 900))
    assert tg.out and all(PERSIAN.search(t) for t in tg.out)


def test_bobs_send_now_outcome_pop_up_is_russian(h):
    from aipager.bot import send_now
    from aipager.state import Status
    bot, tg = _setup(h)
    x1 = S.session(bot, "x1", chat=S.GROUP, status=Status.BUSY)
    x1.queued_targets = [{"msg_id": 900, "chat_id": S.GROUP, "raw_text": "x1",
                          "driver_user_id": S.BOB}]
    bot._send_now_core = AsyncMock(return_value=send_now.SendNowOutcome("taken", "x1"))
    _tap(h, bot, tg, f"{x1.name}:now:900")
    toasts = _toasts(tg)
    assert toasts and all(CYRILLIC.search(t) for t in toasts)
