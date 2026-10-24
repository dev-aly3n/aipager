"""QA iteration 3: a safety halt that could not stop the background job no
longer says "(stopped)", in every language.

Contract (iteration-3 brief): a safety halt that could not stop the
background job no longer says "(stopped)" in its notice's first line (the
line still starts with the halt prefix and still says the work may still
be running); one that stopped it still does. In every language.

The reference, per language, is the halt of the same session before it
parked (an ordinary turn, stopped with Escape): its first line is
``<halt prefix><reason>`` followed by that language's "(stopped)". Then the
session parks on a busy job and is halted again.

Partitions:
- the language: English and every catalog aipager ships;
- the parked halt's stop: worked / exited 1 / timed out.
"""

from __future__ import annotations

import asyncio

import pytest

from aipager import claude_jobs, preferences
from aipager.i18n import locales


def _kit():
    import importlib.util
    import sys
    from pathlib import Path
    if "_bgj_qa_kit" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "_bgj_qa_kit", Path(__file__).with_name("_qa_kit.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_bgj_qa_kit"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["_bgj_qa_kit"]


qa = _kit()

REASON = "qa halt"
CODES = sorted(set(locales.LOCALES) | {"en"})
LANGS = [pytest.param(code, id=code) for code in CODES]
FAILS = [pytest.param("rc", id="stop_exit_1"), pytest.param("timeout", id="stop_timeout")]


def _stop_fails(stack, monkeypatch, how):
    inner = stack.claude.exec

    async def exec_(*argv, **kw):
        if list(argv[1:2]) != ["stop"]:
            return await inner(*argv, **kw)
        stack.claude.runs.append(list(argv[1:]))

        class Proc:
            returncode = None

            async def communicate(self, _stdin=None):
                if how == "timeout":
                    raise asyncio.TimeoutError
                self.returncode = 1
                return b"", b""

            def kill(self):
                pass

            async def wait(self):
                return self.returncode
        return Proc()
    monkeypatch.setattr(claude_jobs, "_create_subprocess_exec", exec_)


def _notice(stack) -> str:
    """The one notice this halt sent (the new message naming the reason)."""
    before = len(qa.sent_texts(stack))
    stack.run(stack.bot._halt_for_safety(stack.sess, REASON))
    notices = [t for t in qa.sent_texts(stack)[before:] if REASON in t]
    assert len(notices) == 1, notices
    return notices[0]


def _first(notice: str) -> str:
    return notice.split("\n", 1)[0]


def _halts(stack, monkeypatch, lang, how=None) -> tuple[str, str]:
    """(the reference's first line, the parked halt's notice)."""
    preferences.set_ui_language(stack.ids.CHAT, lang)
    reference = _first(_notice(stack))
    assert REASON in reference, reference
    qa.park_and_tick(stack, job_status="busy")
    if how is not None:
        _stop_fails(stack, monkeypatch, how)
    return reference, _notice(stack)


def _stopped_part(reference: str) -> str:
    """The language's "(stopped)": what follows the reason."""
    return reference.split(REASON, 1)[1].strip()


def _prefix(reference: str) -> str:
    return reference.split(REASON, 1)[0] + REASON


# ---- preconditions of the per-language checks -------------------------------------------

def test_in_english_the_reference_says_stopped(stack, monkeypatch):
    reference, _notice_ = _halts(stack, monkeypatch, "en")
    assert reference.endswith("(stopped)")


@pytest.mark.parametrize("lang", LANGS)
def test_the_reference_has_a_stopped_part(stack, monkeypatch, lang):
    reference, _notice_ = _halts(stack, monkeypatch, lang)
    assert _stopped_part(reference) != ""


@pytest.mark.parametrize("lang", [p for p in LANGS if p.values[0] != "en"])
def test_the_halt_notice_is_in_the_chats_language(stack, monkeypatch, lang):
    """Otherwise the per-language checks would only test English again."""
    reference, _notice_ = _halts(stack, monkeypatch, lang)
    assert not reference.startswith("🛑 <b>frontend</b> · Blocked by safety policy")


# ---- a parked halt that stopped the job -------------------------------------------------

@pytest.mark.parametrize("lang", LANGS)
def test_a_parked_halt_that_stopped_the_job_still_says_stopped(stack, monkeypatch, lang):
    reference, notice = _halts(stack, monkeypatch, lang)
    assert _first(notice) == reference


# ---- a parked halt that could not stop it -----------------------------------------------

def test_in_english_a_failed_halt_does_not_say_stopped(stack, monkeypatch):
    _reference, notice = _halts(stack, monkeypatch, "en", "rc")
    assert "(stopped)" not in _first(notice)


def test_in_english_a_failed_halt_says_the_work_may_still_run(stack, monkeypatch):
    _reference, notice = _halts(stack, monkeypatch, "en", "rc")
    assert "running" in notice


@pytest.mark.parametrize("how", FAILS)
@pytest.mark.parametrize("lang", LANGS)
def test_a_failed_halts_first_line_has_no_stopped_part(stack, monkeypatch, lang, how):
    reference, notice = _halts(stack, monkeypatch, lang, how)
    assert _stopped_part(reference) not in _first(notice)


@pytest.mark.parametrize("how", FAILS)
@pytest.mark.parametrize("lang", LANGS)
def test_a_failed_halts_first_line_starts_with_the_halt_prefix(stack, monkeypatch, lang,
                                                               how):
    reference, notice = _halts(stack, monkeypatch, lang, how)
    assert _first(notice).startswith(_prefix(reference))


@pytest.mark.parametrize("how", FAILS)
@pytest.mark.parametrize("lang", LANGS)
def test_a_failed_halts_notice_says_more_than_the_bare_prefix(stack, monkeypatch, lang,
                                                              how):
    """It still says the work may still be running: more than the halt
    prefix and the reason."""
    reference, notice = _halts(stack, monkeypatch, lang, how)
    assert len(notice.strip()) > len(_prefix(reference))
