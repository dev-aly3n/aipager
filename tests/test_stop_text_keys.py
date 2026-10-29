"""Roadmap 8.121 F5: the words Stop uses after it ended a session's
background agents, or its job with no keys (aipager/bot/stop_text.py and
the agents block's stopped keys): every key renders in English and in the
pseudo locale, no text holds an em dash, every catalog has every plural
form its language needs, and the renderers compose whole lines joined by a
line break (never one sentence glued from parts)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aipager import i18n
from aipager.bot import agents_line, stop_text
from aipager.bot.session_ops import StopOutcome
from aipager.i18n import Msg, Plural, plurals
from aipager.state import STOP_END_JOB, STOP_KEYS, STOP_KILL_AGENTS

KEYS = [stop_text.CARD_STOPPED_AGENTS, stop_text.CARD_STOPPED_AGENTS_BY,
        stop_text.CARD_SHELLS_KEEP_RUNNING, stop_text.ANS_STOPPED_AGENTS,
        stop_text.ANS_STOPPED_AGENTS_BY, stop_text.ANS_SHELLS_KEEP_RUNNING,
        stop_text.ANS_QUEUED_DISCARDED, stop_text.STOP_DIALOG_OPEN,
        stop_text.STOP_AGENTS_NOT_STOPPED, stop_text.PROMPT_AGENT_STOPPED,
        agents_line.HEAD_STOPPED, agents_line.ROW_STOPPED]
PARAMS = {"session": "x1", "person": "@bob", "clock": "1:10", "agent": "Explore"}
LOCALES = Path(i18n.__file__).resolve().parent / "locales"
CODES = sorted(p.stem for p in LOCALES.glob("*.jsonl"))


def _render(tr, key, n=2):
    import re
    english = key.one if isinstance(key, Plural) else str(key)
    names = set(re.findall(r"\{(\w+)\}", english)) - {"n"}
    params = {k: PARAMS[k] for k in names}
    if isinstance(key, Plural):
        return str(tr.n(key, n, kind="text", **params))
    return str(tr.text(key, **params))


@pytest.mark.parametrize("code", ["en", "qps", "qps-rtl"])
@pytest.mark.parametrize("key", KEYS, ids=lambda k: k.key)
def test_every_key_renders(code, key):
    out = _render(i18n.get(code), key)
    assert out
    if code == "en":
        assert "{" not in out


def test_no_em_dash_in_any_language():
    for key in KEYS:
        texts = [key.one, key.other] if isinstance(key, Plural) else [str(key)]
        assert all("—" not in t for t in texts), key.key
    for code in CODES:
        for line in (LOCALES / f"{code}.jsonl").read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            if entry["k"] in {k.key for k in KEYS}:
                assert "—" not in json.dumps(entry["v"], ensure_ascii=False), (code, entry["k"])


@pytest.mark.parametrize("code", CODES)
def test_every_catalog_has_every_key_and_plural_form(code):
    cat = {}
    for line in (LOCALES / f"{code}.jsonl").read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        cat[entry["k"]] = entry["v"]
    for key in KEYS:
        assert key.key in cat, (code, key.key)
        if isinstance(key, Plural):
            assert set(cat[key.key]) == set(plurals.categories(code)), (code, key.key)
        else:
            assert isinstance(cat[key.key], str)
            assert isinstance(key, Msg)


def _outcome(**kw) -> StopOutcome:
    base = {"ok": True, "label": "x1"}
    base.update(kw)
    return StopOutcome(**base)


EN = i18n.get("en")


def test_card_after_the_chord_with_a_shell():
    out = stop_text.card_text(EN, _outcome(mode=STOP_KILL_AGENTS, agents=2, shells=1))
    assert out == ("⚠️ <b>x1</b> · Stopped 2 background agents\n"
                   "🖥 1 background shell keeps running.")


def test_card_after_the_chord_in_a_group():
    out = stop_text.card_text(EN, _outcome(mode=STOP_KILL_AGENTS, agents=1, by="@bob"))
    assert out == "⚠️ <b>x1</b> · @bob stopped 1 background agent"


def test_card_of_a_keyless_end_keeps_the_stopped_line():
    assert stop_text.card_text(EN, _outcome(mode=STOP_END_JOB, shells=2)) == (
        "⚠️ <b>x1</b> · Stopped\n🖥 2 background shells keep running.")
    assert stop_text.card_text(EN, _outcome(mode=STOP_END_JOB)) == "⚠️ <b>x1</b> · Stopped"


def test_card_of_an_escape_stop_is_unchanged():
    assert stop_text.card_text(EN, _outcome(mode=STOP_KEYS, shells=3)) == "⚠️ <b>x1</b> · Stopped"


def test_toast_lines():
    out = stop_text.stopped_line(EN, _outcome(mode=STOP_KILL_AGENTS, agents=2, dropped=1,
                                              shells=1), html=False)
    assert out.split("\n") == ["⏹ Stopped 2 background agents of x1",
                               "1 queued message was discarded.",
                               "1 background shell keeps running."]


def test_reply_is_html_with_the_session_in_bold():
    out = stop_text.stopped_line(EN, _outcome(mode=STOP_KILL_AGENTS, agents=1, by="@bob"),
                                 html=True)
    assert out == "⏹ @bob stopped 1 background agent of <b>x1</b>"


def test_a_keyless_end_says_stopped_and_the_shells():
    out = stop_text.stopped_line(EN, _outcome(mode=STOP_END_JOB, shells=1), html=False)
    assert out == "⏹ Stopped x1\n1 background shell keeps running."


def test_refusals():
    assert stop_text.refusal(EN, _outcome(ok=False, reason="dialog_open"), html=False) == (
        "x1 has a dialog open in the terminal. Answer it there, then try Stop again.")
    assert stop_text.refusal(EN, _outcome(ok=False, reason="agents_not_stopped"),
                             html=False) == (
        "The background agents of x1 could not be stopped. Try Stop again.")
    assert stop_text.refusal(EN, _outcome(ok=False, reason=""), html=False) is None
    assert stop_text.STOP_REFUSALS == {"dialog_open", "agents_not_stopped"}
