"""Iteration 2 black-box cases for 8.121.

* spec requirement 2 ("waiting for 2 agents and 1 shell" is one sentence,
  never glued from fragments): every catalog's both-counts sentence
  (``jobwait.both``, ``ui.state.tasks_both``, ``ui.state_line.tasks_both``)
  keeps both placeholders once each; the Arabic one joins them with the
  Arabic comma, main's two-count pattern (``pin.running_both``), since a
  "و" glued to ``{shells}`` puts the count's leading RLM between two word
  characters (iPhone RTL); no rendering of the phrase, in any language
  or direction, holds a direction mark between two word characters;
  ``job_wait.phrase`` names both counts in every language.
* entrypoints.md CLI: a TASKS row's "waiting for ..." text appears exactly
  once per row in ``aipager status`` and ``aipager session ls`` plain output.

Expected words come from the shipped catalogs, never from the code under
test.
"""

from __future__ import annotations

import argparse
import importlib
import re

import pytest

from aipager import cli, i18n, status
from aipager.bot import job_wait
from aipager.i18n import core
from aipager.i18n.locales import LOCALES
from aipager.state import SessionRegistry

kit = importlib.import_module("tests.integration.waiting-for-agents-blackbox.conftest")

BOTH_KEYS = ("jobwait.both", "ui.state.tasks_both", "ui.state_line.tasks_both")
TRANSLATED = [c for c in LOCALES if c != "en"]

# The digits each locale writes numbers with (locales.py ``digits``).
_DIGITS = {"latn": "0123456789", "arab": "٠١٢٣٤٥٦٧٨٩", "arabext": "۰۱۲۳۴۵۶۷۸۹"}


def _digit(code: str, n: int) -> str:
    return _DIGITS[LOCALES[code].digits][n]


# ---- the both-counts sentence in every catalog ---------------------------------

@pytest.mark.parametrize("key", BOTH_KEYS)
@pytest.mark.parametrize("code", TRANSLATED)
def test_both_sentence_keeps_the_agents_placeholder_once(code, key):
    assert core._catalog(code)[key].count("{agents}") == 1


@pytest.mark.parametrize("key", BOTH_KEYS)
@pytest.mark.parametrize("code", TRANSLATED)
def test_both_sentence_keeps_the_shells_placeholder_once(code, key):
    assert core._catalog(code)[key].count("{shells}") == 1


@pytest.mark.parametrize("key", BOTH_KEYS)
@pytest.mark.parametrize("code", TRANSLATED)
def test_both_sentence_has_no_em_dash(code, key):
    assert "—" not in core._catalog(code)[key]


@pytest.mark.parametrize("key", BOTH_KEYS)
def test_arabic_both_sentence_joins_the_counts_with_the_arabic_comma(key):
    """The regression run's failure: "{agents} و{shells}" put the shell
    count's leading RLM between "و" and the count. The sentence uses the
    two-count pattern ar ``pin.running_both`` already has."""
    text = core._catalog("ar")[key]
    assert re.search(r"\{agents\}\u060c \{shells\}", text), text


@pytest.mark.parametrize("key", BOTH_KEYS)
@pytest.mark.parametrize("code", ("ar", "fa"))
def test_right_to_left_both_sentence_glues_no_letter_to_a_placeholder(code, key):
    text = core._catalog(code)[key]
    assert not re.search(r"\w\{|\}\w", text), text


def test_arabic_phrase_joins_the_counts_with_the_arabic_comma():
    # 7 and 3: Arabic writes 1 and 2 as words (singular, dual), not digits.
    out = job_wait.phrase(i18n.get("ar"), 7, 3, "text")
    bare = out.replace("\u200f", "").replace("\u200e", "")
    assert re.search("\u060c " + _digit("ar", 3), bare), out


_SPLIT_WORD = re.compile("\\w[\u200e\u200f]+\\w")


@pytest.mark.parametrize("rtl", (None, True, False), ids=("own", "rtl", "ltr"))
@pytest.mark.parametrize("kind", ("text", "html", "md"))
@pytest.mark.parametrize("code", list(LOCALES))
def test_both_phrase_holds_no_mark_between_two_word_characters(code, kind, rtl):
    """A mark between two word characters breaks a right-to-left line on
    iPhone; zh-Hans "{agents}和" did it in its in-RTL form (the agents
    count's closing LRM before 和)."""
    tr = i18n.get(code).directed(rtl)
    bad = [_SPLIT_WORD.search(re.sub(r"<[^>]+>", "", job_wait.phrase(tr, a, s, kind)))
           for a, s in ((1, 1), (2, 3), (7, 11))]
    assert not any(bad), [m.group() for m in bad if m]


@pytest.mark.parametrize("code", list(LOCALES))
def test_phrase_names_the_agent_count_in_every_language(code):
    out = job_wait.phrase(i18n.get(code), 7, 3, "text")
    assert _digit(code, 7) in out, out


@pytest.mark.parametrize("code", list(LOCALES))
def test_phrase_names_the_shell_count_in_every_language(code):
    out = job_wait.phrase(i18n.get(code), 7, 3, "text")
    assert _digit(code, 3) in out, out


@pytest.mark.parametrize("code", list(LOCALES))
def test_both_phrase_differs_from_the_agents_only_phrase(code):
    tr = i18n.get(code)
    assert job_wait.phrase(tr, 7, 3, "text") != job_wait.phrase(tr, 7, 0, "text")


@pytest.mark.parametrize("code", list(LOCALES))
def test_both_phrase_leaves_no_placeholder(code):
    out = job_wait.phrase(i18n.get(code), 2, 1, "text")
    assert "{" not in out and "}" not in out, out


def _find(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            hit = _find(v, key)
            if hit is not None:
                return hit
    return None


@pytest.mark.parametrize("key", ["ui.state.tasks_both", "ui.state_line.tasks_both"])
def test_served_arabic_catalog_carries_the_joined_sentence(miniapp, run_async, key):
    code, body = kit.miniapp_call(miniapp, run_async, "get", "/api/i18n/ar.json")
    assert code == 200, body
    assert _find(body, key) == core._catalog("ar")[key]


# ---- CLI plain text: the waiting-for text once per TASKS row --------------------

def _save(*sessions) -> None:
    reg = SessionRegistry()
    kit.register(reg, *sessions)
    reg.save()


def _fake_dtach(monkeypatch, live):
    monkeypatch.setattr(status, "_live_sessions", lambda: set(live))
    monkeypatch.setattr(status, "_read_statusline", lambda name: {})


def _session_ls(monkeypatch, capsys, live) -> str:
    _fake_dtach(monkeypatch, live)
    cli._cmd_session(argparse.Namespace(name="ls", claude_args=[]))
    return capsys.readouterr().out


def _status_text(monkeypatch, capsys, live) -> str:
    _fake_dtach(monkeypatch, live)
    monkeypatch.setattr(status, "BOT_TOKEN", "tok")
    monkeypatch.setattr(status, "CHAT_ID", "5")
    monkeypatch.setattr(status, "_daemon_alive", lambda: True)
    status.cmd_status(argparse.Namespace(as_json=False))
    return capsys.readouterr().out


COMMANDS = {"session_ls": _session_ls, "status": _status_text}

ONE_ROW = [
    (dict(agents=1), "waiting for 1 agent"),
    (dict(agents=2), "waiting for 2 agents"),
    (dict(job_shells=1), "waiting for 1 shell"),
    (dict(agents=2, job_shells=1), "waiting for 2 agents and 1 shell"),
]


@pytest.mark.parametrize("cmd", list(COMMANDS))
@pytest.mark.parametrize("counts, phrase", ONE_ROW, ids=["1a", "2a", "1s", "2a1s"])
def test_cli_text_names_a_tasks_row_exactly_once(monkeypatch, capsys, cmd, counts, phrase):
    _save(kit.make_session(**counts))
    out = COMMANDS[cmd](monkeypatch, capsys, {"claude-x1"})
    assert out.count(phrase) == 1, out


@pytest.mark.parametrize("cmd", list(COMMANDS))
def test_cli_text_names_finishing_up_exactly_once(monkeypatch, capsys, cmd):
    import time
    sess = kit.make_session()
    sess.job_grace_until = time.monotonic() + 60
    _save(sess)
    out = COMMANDS[cmd](monkeypatch, capsys, {"claude-x1"})
    assert out.count("finishing up") == 1, out


@pytest.mark.parametrize("cmd", list(COMMANDS))
def test_cli_text_two_tasks_rows_each_named_once_first(monkeypatch, capsys, cmd):
    _save(kit.make_session(agents=2, job_shells=1),
          kit.make_session(agents=3, name="claude-x2", label="x2"))
    out = COMMANDS[cmd](monkeypatch, capsys, {"claude-x1", "claude-x2"})
    assert out.count("waiting for 2 agents and 1 shell") == 1, out


@pytest.mark.parametrize("cmd", list(COMMANDS))
def test_cli_text_two_tasks_rows_each_named_once_second(monkeypatch, capsys, cmd):
    _save(kit.make_session(agents=2, job_shells=1),
          kit.make_session(agents=3, name="claude-x2", label="x2"))
    out = COMMANDS[cmd](monkeypatch, capsys, {"claude-x1", "claude-x2"})
    assert out.count("waiting for 3 agents") == 1, out


@pytest.mark.parametrize("cmd", list(COMMANDS))
def test_cli_text_tasks_text_sits_on_the_rows_line(monkeypatch, capsys, cmd):
    _save(kit.make_session(agents=2, job_shells=1),
          kit.make_session(agents=3, name="claude-x2", label="x2"))
    out = COMMANDS[cmd](monkeypatch, capsys, {"claude-x1", "claude-x2"})
    line = next(ln for ln in out.splitlines() if "waiting for 3 agents" in ln)
    assert "x2" in line and "x1" not in line.replace("x2", ""), out


@pytest.mark.parametrize("cmd", list(COMMANDS))
def test_cli_text_word_tasks_once_per_row(monkeypatch, capsys, cmd):
    _save(kit.make_session(agents=2, job_shells=1),
          kit.make_session(agents=3, name="claude-x2", label="x2"))
    out = COMMANDS[cmd](monkeypatch, capsys, {"claude-x1", "claude-x2"})
    assert out.count("TASKS") == 2, out


@pytest.mark.parametrize("cmd", list(COMMANDS))
def test_cli_text_idle_row_carries_no_waiting_text(monkeypatch, capsys, cmd):
    _save(kit.make_session(other_shells=1))
    out = COMMANDS[cmd](monkeypatch, capsys, {"claude-x1"})
    assert "waiting for" not in out and "finishing up" not in out, out
