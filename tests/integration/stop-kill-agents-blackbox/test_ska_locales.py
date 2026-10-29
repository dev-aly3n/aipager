"""design-6 section 6 in a non-English chat: every new Stop text renders
from the chat language's catalog, every placeholder filled, no em dash.

Partitions: each shipped non-English language; plural forms for 2 agents
and 1 shell; the card, the pop-up, the refusal and an agent's closed
prompt. Expected words come from the shipped catalogs
(``aipager.i18n.core._catalog``, as the other black-box suites read them)
with the language's own digits, never from the code under test. Direction
marks are left out of the comparison (where a right-to-left language puts
them is the renderer's business, tests/test_bidi_guard.py and the RTL
suites hold it).
"""

from __future__ import annotations

import importlib
import re

import pytest

from aipager import preferences
from aipager.i18n import core, plurals
from aipager.i18n.locales import LOCALES

kit = importlib.import_module("tests.integration.stop-kill-agents-blackbox.conftest")
CODES = [c for c in LOCALES if c != "en"]
_DIGITS = {"latn": "0123456789", "arab": "٠١٢٣٤٥٦٧٨٩", "arabext": "۰۱۲۳۴۵۶۷۸۹"}
_MARKS = re.compile("[\u200e\u200f\u061c\u2066-\u2069]")


def _clean(text: str) -> str:
    return _MARKS.sub("", text)


def _num(code: str, n: int) -> str:
    return "".join(_DIGITS[LOCALES[code].digits][int(d)] for d in str(n))


def _form(code: str, key: str, n: int | None = None, **values) -> str:
    entry = core._catalog(code)[key]
    if isinstance(entry, dict):
        entry = entry[plurals.category(code, n)]
        values["n"] = _num(code, n)
    for k, v in values.items():
        entry = entry.replace("{" + k + "}", v)
    return _clean(entry)


def _set(h, code):
    preferences.set_ui_language(kit.DM, code)


def _stopped(h, code, entry="card"):
    _set(h, code)

    async def go():
        await h.waiting(shells=(kit.SHELL,))
        await h.stop(entry)
    h.run(go())


@pytest.mark.parametrize("code", CODES)
def test_card_first_line_is_the_catalogs(dm, code):
    _stopped(dm, code)
    assert _clean(dm.card_texts()[-1]).split("\n")[0] == _form(
        code, "card.stopped_agents", 2, session="x1")


@pytest.mark.parametrize("code", CODES)
def test_card_shell_line_is_the_catalogs(dm, code):
    _stopped(dm, code)
    assert _clean(dm.card_texts()[-1]).split("\n")[1] == _form(
        code, "card.shells_keep_running", 1)


@pytest.mark.parametrize("code", CODES)
def test_popup_is_the_catalogs(dm, code):
    _stopped(dm, code)
    assert [_clean(t) for t in dm.toasts] == [
        _form(code, "ans.stopped_agents", 2, session="x1") + "\n"
        + _form(code, "ans.shells_keep_running", 1)]


@pytest.mark.parametrize("code", CODES)
def test_miniapp_notice_is_the_catalogs(dm, code):
    _stopped(dm, code, "miniapp")
    assert _clean(dm.app[-1][1]["detail"]) == (
        _form(code, "ans.stopped_agents", 2, session="x1") + "\n"
        + _form(code, "ans.shells_keep_running", 1))


@pytest.mark.parametrize("code", CODES)
def test_no_placeholder_is_left(dm, code):
    _stopped(dm, code)
    shown = dm.toasts + dm.card_texts()[-1:]
    assert not any("{" in t or "}" in t for t in shown), shown


@pytest.mark.parametrize("code", CODES)
def test_no_em_dash(dm, code):
    _stopped(dm, code)
    shown = dm.toasts + dm.card_texts()[-1:]
    assert not any("\u2014" in t for t in shown), shown


@pytest.mark.parametrize("code", CODES)
def test_the_popup_is_not_english(dm, code):
    _stopped(dm, code)
    assert not any("background agent" in t for t in dm.toasts)


@pytest.mark.parametrize("code", CODES)
def test_the_refusal_is_the_catalogs(dm, code):
    _set(dm, code)

    async def go():
        await dm.waiting()
        dm.sess.term_dialog = {"epoch": 1, "pid": 4100, "kind": "other"}
        await dm.stop("card")
    dm.run(go())
    assert [_clean(t) for t in dm.toasts] == [
        _form(code, "stop.dialog_open", session="x1")]


@pytest.mark.parametrize("code", CODES)
def test_an_agents_closed_prompt_says_so_in_the_chat_language(dm, code):
    _set(dm, code)
    seen = {}

    async def go():
        await dm.waiting()
        await dm.agent_permission(kit.A1)
        await dm.agent_permission(kit.A2, "rm -rf cache/")
        seen["mid"] = [m for m in dm.sent if m.get("reply_markup") is not None][0]["msg_id"]
        await dm.stop("card")
    dm.run(go())
    last = [t for i, t, _m in dm.edits if i == seen["mid"]][-1]
    assert _clean(last).split("\n")[-1] == _form(code, "prompt.agent_stopped")


@pytest.mark.parametrize("n", [1, 2, 5, 11, 21, 22])
def test_russian_card_plural_forms(dm, n):
    _set(dm, "ru")
    agents = tuple(f"a{i:016x}" for i in range(n))

    async def go():
        await dm.waiting(agents=agents)
        await dm.stop("card")
    dm.run(go())
    assert _clean(dm.card_texts()[-1]) == _form("ru", "card.stopped_agents", n,
                                                session="x1")
