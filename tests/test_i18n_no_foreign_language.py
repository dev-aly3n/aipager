"""Check 12 across every surface: a message is never in another chat's
language (multilingual design 9, phase 5).

Every registered surface is rendered twice with its own chats stored as
``qps``: once with the decoy chats (a private chat and a group no surface
renders in, and the remembered Telegram language of people no surface
renders for) on English, once on ``qps-rtl``. A surface that took any of
their languages would differ between the two runs.
"""

from __future__ import annotations

import pytest

from aipager.i18n import locales, persist
from tests import i18n_surfaces

NAMES = sorted({name for name, _fn in i18n_surfaces.SURFACES})


def _fresh_state():
    """Each render starts from the same stored state: a surface that
    stores a setting (a tap on /settings) must not change the next run."""
    from pathlib import Path

    from aipager import config, preferences
    Path(preferences._PREFERENCES_PATH).unlink(missing_ok=True)
    preferences._cache = None
    Path(config.LANGUAGE_STATE_FILE).unlink(missing_ok=True)
    persist.reset()
    # Everyone is new since the launch, so a remembered Telegram language
    # would be followed if a surface ever read it for the wrong person.
    persist.ensure_launch_marker(people=(), prior_run=False)


def _render(mk_bot, run_async, monkeypatch, name, decoy):
    _fresh_state()
    return i18n_surfaces.render_all(mk_bot, run_async, monkeypatch, "qps",
                                    names={name}, decoy=decoy)[name]


@pytest.fixture
def pseudo(monkeypatch):
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"qps", "qps-rtl"}))
    _fresh_state()


def test_the_decoys_are_not_any_surfaces_chats():
    assert set(i18n_surfaces.DECOY_CHATS).isdisjoint(
        {i18n_surfaces.CHAT, i18n_surfaces.GROUP, i18n_surfaces.BOB})


def test_the_decoys_are_really_stored(mk_bot, run_async, monkeypatch, pseudo):
    """Guard the guard: a renderer that reads a decoy's language shows it."""
    from aipager import i18n

    def peek(env):
        return [i18n.for_chat(77).code, i18n.for_chat(-77).code, i18n.for_chat(78).code]

    monkeypatch.setattr(i18n_surfaces, "SURFACES", [("peek", peek)])
    a = i18n_surfaces.render_all(mk_bot, run_async, monkeypatch, "qps", decoy="en")
    b = i18n_surfaces.render_all(mk_bot, run_async, monkeypatch, "qps", decoy="qps-rtl")
    assert a == {"peek": ["en", "en", "en"]}
    assert b == {"peek": ["qps-rtl", "qps-rtl", "qps-rtl"]}


@pytest.mark.parametrize("name", NAMES)
def test_no_surface_shows_another_chats_language(name, mk_bot, run_async, monkeypatch,
                                                 pseudo):
    english = _render(mk_bot, run_async, monkeypatch, name, "en")
    rtl = _render(mk_bot, run_async, monkeypatch, name, "qps-rtl")
    assert english == rtl
    assert english, name
