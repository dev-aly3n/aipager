"""Every phase 2 surface rendered in Persian (the registry,
``tests/i18n_surfaces.py``, with ``fa`` stored for its chats): right to
left with the marks in the right places, nothing invisible inside code,
commands or session names, Persian digits on numbers only, ``←`` and the
catalog's words (G75, G76, G78)."""

from __future__ import annotations

import re

from aipager import config
from tests import i18n_surfaces

RLM, FSI, PDI = "\u200f", "\u2068", "\u2069"
MARKS = (RLM, FSI, PDI)
PERSIAN = re.compile("[\u0600-\u06ff]")
_LETTER = re.compile("[A-Za-z\u0600-\u06ff\u0400-\u04ff]")
_TAG = re.compile(r"<[^>]+>")
_CODE = re.compile(r"<code\b[^>]*>.*?</code>|<pre\b[^>]*>.*?</pre>|`+[^`]*`+", re.S)
_ISOLATED = re.compile(FSI + ".*?" + PDI, re.S)
#: Data the registry feeds in that is written as it is (model names, the
#: tool rows of the plain card): never translated, never converted.
_DATA = sorted({label for label, _cmd in config.MODEL_CHOICES} | {"Opus 4.7"},
               key=len, reverse=True)


def _names():
    """Every registered surface whose migration has landed."""
    return {n for n, _f in i18n_surfaces.SURFACES} - i18n_surfaces.PENDING


def _all(mk_bot, run_async, monkeypatch):
    return i18n_surfaces.render_all(mk_bot, run_async, monkeypatch, "fa", names=_names())


def _texts(renders):
    for name, texts in renders.items():
        for text in texts:
            if name == "menu.commands":
                # "/cmd description": the command word is the menu's own.
                text = text.split(" ", 1)[1]
            yield name, text


def test_every_surface_reads_in_persian(mk_bot, run_async, monkeypatch):
    renders = _all(mk_bot, run_async, monkeypatch)
    assert set(renders) == _names()
    for name, texts in renders.items():
        assert name in i18n_surfaces.NAMES_ONLY or any(PERSIAN.search(t) for t in texts), name
        for text in texts:
            assert "—" not in text, (name, text)


def _first_strong(line: str):
    """The first character that decides a paragraph's direction (tags and
    code are not text)."""
    bare = _TAG.sub("", line)
    for ch in bare:
        if ch == RLM:
            return "R"
        if _LETTER.match(ch):
            return "R" if PERSIAN.match(ch) else "L"
    return None


def test_every_persian_line_reads_right_to_left(mk_bot, run_async, monkeypatch):
    for name, text in _texts(_all(mk_bot, run_async, monkeypatch)):
        if name in i18n_surfaces.FOLLOWS_CLAUDE:
            i18n_surfaces.assert_follows_claude(name, text)
            continue
        for line in text.split("\n"):
            if PERSIAN.search(_ISOLATED.sub("", _CODE.sub("", line))):
                assert _first_strong(line) == "R", (name, line)


def test_no_mark_inside_code_or_commands(mk_bot, run_async, monkeypatch):
    for name, text in _texts(_all(mk_bot, run_async, monkeypatch)):
        for span in _CODE.findall(text):
            assert not any(m in span for m in MARKS), (name, span)
        # A command or mention is never split from its first letter.
        assert not re.search("(?<![\\w./-])[/@][" + RLM + FSI + PDI + "]", text), (name, text)
        assert not re.search("[A-Za-z0-9_][" + RLM + FSI + PDI + "][A-Za-z0-9_]", text), (
            name, text)


def test_session_names_keep_latin_digits(mk_bot, run_async, monkeypatch):
    renders = _all(mk_bot, run_async, monkeypatch)
    named = [t for _n, t in _texts(renders) if "x" in t]
    assert any("x1" in t for t in named)
    for name, text in _texts(renders):
        assert not re.search(r"x[۰-۹]", text), (name, text)
    assert any("/x1" in t for t in renders["menu.commands"])


def _numbers_outside_names(text: str) -> list[str]:
    rest = _CODE.sub(" ", text)
    rest = _ISOLATED.sub(" ", rest)
    rest = _TAG.sub(" ", rest)
    for data in _DATA:
        rest = rest.replace(data, " ")
    rest = re.sub(r"(?<!\w)/[\w./-]+|https?://\S+|@\w+|AIPAGER_\w+=\d+", " ", rest)
    rest = re.sub(r"\b[A-Za-z_][\w.-]*\d[\w.-]*", " ", rest)   # an identifier: x1, f12
    return re.findall(r"[0-9]+", rest)


def test_numbers_are_persian_digits(mk_bot, run_async, monkeypatch):
    for name, text in _texts(_all(mk_bot, run_async, monkeypatch)):
        if not PERSIAN.search(text):
            continue                    # data only (a model name, a tool row)
        assert _numbers_outside_names(text) == [], (name, text)


def test_arrows_point_left(mk_bot, run_async, monkeypatch):
    seen_left = False
    for name, text in _texts(_all(mk_bot, run_async, monkeypatch)):
        outside = _CODE.sub(" ", text)
        assert "→" not in outside, (name, text)
        seen_left = seen_left or "←" in outside
    assert seen_left


def test_phase_3a_arrows_and_commands(mk_bot, run_async, monkeypatch):
    """The compacted line and the model switch point left; the command the
    resume-running reply names is whole inside its <code>, isolated
    outside it (phase 3a)."""
    renders = i18n_surfaces.render_all(
        mk_bot, run_async, monkeypatch, "fa",
        names={"notice.compacted", "route.model_feedback", "sess.resume"})
    compacted = renders["notice.compacted"][0]
    assert "\u2190" in compacted and "\u2192" not in compacted
    assert all("\u2190" in t and "\u2192" not in t for t in renders["route.model_feedback"])
    running = [t for t in renders["sess.resume"] if "<code>/x1</code>" in t]
    assert running == [t for t in renders["sess.resume"]
                       if FSI + "<code>/x1</code>" + PDI in t]
    assert running


def test_the_clock_is_in_persian_digits(mk_bot, run_async, monkeypatch):
    renders = i18n_surfaces.render_all(mk_bot, run_async, monkeypatch, "fa",
                                       names={"card.busy", "status.list"})
    status = renders["card.busy"][0].rsplit("\n", 1)[-1]
    assert "۴:۱۰" in status and "4:10" not in status
    assert "۴:۱۰" in renders["status.list"][0]


def test_who_stopped_or_refused_reads_by_not_with():
    """Persian makes @bob the subject ("@bob stopped it"); با means "with"
    and read "stopped with @bob" (review rev-iter1-004)."""
    from aipager import i18n
    from aipager.bot import callbacks, session_ops
    fa = i18n.get("fa")
    texts = [session_ops._stopped_card_text("x1", "@bob", fa),
             session_ops._refused_card_text("x1", "@bob", tr=fa),
             fa.text(callbacks.STOPPED_BY, session="x1", person="@bob"),
             fa.n(callbacks.STOPPED_BY_DROPPED, 2, kind="text", session="x1", person="@bob")]
    for text in texts:
        assert re.search("@bob" + r"\W* .*کرد", text) and " با " not in text, text
