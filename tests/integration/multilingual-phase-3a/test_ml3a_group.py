"""Black-box: a Persian group with a member whose own chat is Russian.
What the group sees is Persian; the member's pop-ups are Russian."""

from __future__ import annotations

from tests import i18n_ml2_bb as bb
from tests.i18n_ml2_bb import CYRILLIC, PERSIAN


def _toasts(query):
    return bb.toast_texts(query)


def test_a_russian_member_in_a_persian_group(world):
    world.choose(bb.GROUP, "fa")
    world.choose(bb.DM_B, "ru")
    x1 = world.session("x1", chat=bb.GROUP)
    world.bot._can_prompt_user = lambda *a, **k: False
    toasts = []
    for verb in ("rename", "delete", "restart", "talk"):
        toasts += _toasts(world.tap(f"{x1.name}:{verb}", chat=bb.GROUP, user=bb.BOB))
    assert len(toasts) == 4
    assert all(CYRILLIC.search(t) and not PERSIAN.search(t) for t in toasts)


def test_the_group_card_stays_persian_when_the_russian_member_opens_it(world):
    world.choose(bb.GROUP, "fa")
    world.choose(bb.DM_B, "ru")
    world.send("_handle_new_cmd", "/new", chat=bb.GROUP, user=bb.BOB)
    assert world.tg.out and PERSIAN.search(world.tg.out[0])
    assert not any(CYRILLIC.search(t) for t in world.tg.out)


def test_the_owner_reads_the_groups_language_in_the_group(world):
    world.choose(bb.GROUP, "fa")
    world.choose(bb.DM_A, "ru")
    world.send("_handle_stop_cmd", "/stop", chat=bb.GROUP)
    assert world.tg.out and all(PERSIAN.search(t) for t in world.tg.out)
