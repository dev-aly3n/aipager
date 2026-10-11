"""Success criteria 7, 8, 17 and 18, swept over every scenario: the same
conversation run in an English chat and in a Persian (fa) or Russian (ru)
one, each message compared with its English twin.

fa: Persian letters, RLM at the start of every Persian line, no mark inside
<code>/<pre>, commands kept whole, session names keep Latin digits, no "→",
no em dash, no bidi control other than aipager's own (RLM, FSI, PDI).
ru: Cyrillic, no marks at all, no em dash. Both: as many messages and
buttons as in English, pop-ups within Telegram's 200 characters."""

from __future__ import annotations

import re

import pytest
import qa_p3a_scenarios as S

RLM, FSI, PDI = "\u200f", "\u2068", "\u2069"
#: aipager's own marks: U+200F and the isolates, and in a message that
#: reads left to right because Claude writes English, U+200E and U+2067
#: (multilingual design 5.7, rule 5).
OWN = re.compile("[\u200e\u200f\u2067\u2068\u2069]")
#: Scenarios whose message carries Claude's (English) text: they read
#: left to right.
FOLLOWS_CLAUDE = {"held_answers"}
#: Every bidi control: LRM, RLM, ALM, embeddings, overrides, isolates.
BIDI = re.compile("[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")
PERSIAN = re.compile("[؀-ۿ]")
CYRILLIC = re.compile("[Ѐ-ӿ]")
LATIN_WORD = re.compile(r"[A-Za-z]{3,}")
EM_DASH = "\u2014"

#: Scenarios whose output is repr() of objects or Claude-facing text only.
SKIP_TEXT = {"exported_renderers", "safety_reason_strings"}
#: Lines that are not Telegram text: Claude's bytes, the audit, the
#: observer calls (repr), compared elsewhere.
NOT_SHOWN = ("[keys]", "[audit]", "[observer]")
NAMES = sorted(n for n in S.SCENARIOS if n not in SKIP_TEXT)
CASES = [(n, lang) for n in NAMES for lang in ("fa", "ru")]


@pytest.fixture
def run_both(scenario_out):
    """The English run and the *lang* run of a scenario (each rendered
    once per session, see conftest.scenario_out)."""
    return lambda name, lang: (scenario_out(name, "en"), scenario_out(name, lang))


def _shown(texts):
    """Telegram-visible texts (not Claude's bytes, not the audit)."""
    return [t for t in texts if not t.startswith(NOT_SHOWN)]


def _body(text):
    return text.split("] ", 1)[1] if text.startswith("[") and "] " in text else text


@pytest.mark.parametrize("name,lang", CASES)
def test_the_same_number_of_messages_and_buttons_as_english(run_both, name, lang):
    en, other = run_both(name, lang)
    assert len(other) == len(en)


@pytest.mark.parametrize("name,lang", CASES)
def test_no_em_dash(run_both, name, lang):
    _, other = run_both(name, lang)
    assert [t for t in other if EM_DASH in t] == []


@pytest.mark.parametrize("name,lang", CASES)
def test_no_bidi_control_but_aipagers_own(run_both, name, lang):
    _, other = run_both(name, lang)
    stray = [t for t in other if BIDI.sub("", OWN.sub("", t)) != OWN.sub("", t)]
    assert stray == []


@pytest.mark.parametrize("name", NAMES)
def test_russian_has_no_marks_at_all(run_both, name):
    _, other = run_both(name, "ru")
    assert [t for t in other if BIDI.search(t)] == []


@pytest.mark.parametrize("name,lang", CASES)
def test_every_command_in_the_english_is_kept_whole(run_both, name, lang):
    en, other = run_both(name, lang)
    lost = []
    for a, b in zip(en, other):
        for command in re.findall(r"(?<![\w&;])/[a-z_]+(?: x\d)?", a):
            if command not in b:
                lost.append((command, b))
    assert lost == []


@pytest.mark.parametrize("name,lang", CASES)
def test_session_names_keep_latin_digits(run_both, name, lang):
    _, other = run_both(name, lang)
    assert [t for t in other if re.search("[a-z][۰-۹]", t)] == []


@pytest.mark.parametrize("name", NAMES)
def test_no_mark_inside_code_or_pre_in_persian(run_both, name):
    _, other = run_both(name, "fa")
    segs = []
    for t in other:
        segs += re.findall(r"<code>(.*?)</code>", t, re.S)
        segs += re.findall(r"<pre>(.*?)</pre>", t, re.S)
    assert [s for s in segs if BIDI.search(s)] == []


@pytest.mark.parametrize("name", NAMES)
def test_every_persian_line_opens_right_to_left(run_both, name):
    _, other = run_both(name, "fa")
    bad = []
    for t in _shown(other):
        for line in _body(t).split("\n"):
            if name in FOLLOWS_CLAUDE:
                if line.startswith(RLM):
                    bad.append(line)
            elif PERSIAN.search(line) and not line.startswith(RLM):
                bad.append(line)
    assert bad == []


@pytest.mark.parametrize("name", NAMES)
def test_persian_never_points_right(run_both, name):
    _, other = run_both(name, "fa")
    assert [t for t in other if "→" in t] == []


def _translated_messages(en, other):
    """Pairs whose English holds a sentence (three Latin words or more
    outside tags, names and code) and is not a bare name or button of one."""
    out = []
    for a, b in zip(en, other):
        if a.startswith(NOT_SHOWN):
            continue
        prose = re.sub(r"<code>.*?</code>|<pre>.*?</pre>|<[^>]+>", "", _body(a), flags=re.S)
        if len(LATIN_WORD.findall(prose)) >= 3:
            out.append((a, b))
    return out


@pytest.mark.parametrize("name,lang", CASES)
def test_every_sentence_is_in_the_chats_script(run_both, name, lang):
    en, other = run_both(name, lang)
    script = PERSIAN if lang == "fa" else CYRILLIC
    untranslated = [b for a, b in _translated_messages(en, other)
                    if not script.search(b)
                    and not a.startswith("🛑")]     # unknown safety reasons: as received
    assert untranslated == []


@pytest.mark.parametrize("name,lang", CASES)
def test_pop_ups_fit_telegrams_200_characters(run_both, name, lang):
    _, other = run_both(name, lang)
    assert [t for t in other if t.startswith("[toast]") and len(_body(t)) > 200] == []
