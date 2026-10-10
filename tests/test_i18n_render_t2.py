"""Every registered surface rendered in German, Turkish, Ukrainian and
Arabic (phase 5, translation run 2): no English left (a word check against
the English source with the never-translate list), placeholders filled,
the plural form each count needs, digits per the language's rule, no long
dash, the right-to-left marks in Arabic only (where Persian has them), and
the golden surfaces called out by name: the first-contact line, the busy
card, the permission prompt and its answered line, /settings, /language,
/status, errors and refusals."""

from __future__ import annotations

import json
import re

import pytest

from aipager import config, i18n, preferences
from aipager.bot import agents_line, animation, card_rows, dashboard, held_message
from aipager.bot.notify import settled_agent_row
from aipager.i18n import core, locales, plurals
from tests import i18n_surfaces, i18n_tool

tool = i18n_tool.load()

CODES = ("de", "tr", "uk", "ar")
RLM, FSI, PDI, ALM = "\u200f", "\u2068", "\u2069", "\u061c"
MARKS = (RLM, FSI, PDI)
ARABIC = re.compile("[؀-ۿ]")
CYRILLIC = re.compile("[Ѐ-ӿ]")
AR_DIGIT = re.compile("[٠-٩]")
FA_DIGIT = re.compile("[۰-۹]")
_WORD = re.compile(r"[^\W\d_]+")
_HOLDER = re.compile(r"\{[A-Za-z_]\w*\}")
_TAG = re.compile(r"<[^>]+>")
_CODE = re.compile(r"<code\b[^>]*>.*?</code>|<pre\b[^>]*>.*?</pre>|`+[^`]*`+", re.S)
_ISOLATED = re.compile(FSI + ".*?" + PDI, re.S)
_LETTER = re.compile("[A-Za-z؀-ۿЀ-ӿ]")
#: Data the registry feeds in as it is (model names): never converted.
_DATA = sorted({label for label, _cmd in config.MODEL_CHOICES} | {"Opus 4.7"},
               key=len, reverse=True)

#: Every count the plural checks use (0 only where the language has a
#: zero form).
COUNTS = (1, 2, 3, 5, 11, 21, 22, 25, 100)
WANT_CATEGORIES = {
    "de": ["one"] + ["other"] * 8,
    "tr": ["one"] + ["other"] * 8,
    "uk": ["one", "few", "few", "many", "many", "one", "few", "many", "many"],
    "ar": ["one", "two", "few", "few", "many", "many", "many", "many", "other"],
}


def _counts(code):
    return ((0,) if "zero" in plurals.categories(code) else ()) + COUNTS


def _words(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(_HOLDER.sub(" ", _TAG.sub(" ", text)))}


def _source_rows():
    return tool.load_jsonl(tool.SOURCE)


def _english_vocabulary() -> set[str]:
    words: set[str] = set()
    for row in _source_rows():
        for form in tool.english_forms(row):
            words |= _words(form)
    return words


def _vocabulary(code: str) -> set[str]:
    """Every word the language's own catalog writes, in entries that are
    not simply the English left as it is (so an untranslated entry cannot
    vouch for its own English words)."""
    english = {r["k"]: set(tool.english_forms(r)) for r in _source_rows()}
    words: set[str] = set()
    for key, value in core._catalog(code).items():
        for form in (value.values() if isinstance(value, dict) else [value]):
            if form not in english.get(key, ()):
                words |= _words(form)
    words |= _SHARED_WORDS.get(code, set())
    gloss = tool.load_glossary(code)
    for word in gloss.get("keep_english", []):
        words.add(word.lower())
    return words


_ENGLISH = _english_vocabulary()
#: Data the registry feeds into a surface, written as it is: a tool call's
#: command, an update's internal step id, the voice extra's install
#: commands, a /mode example.
_SURFACE_DATA = {
    "pinned": ("everything",),
    "update.prompt": ("waiting_for_idle", "waiting", "for", "idle"),
    "update.taps": ("upgrading",),
    "media.voice": ("tool", "install", "reinstall", "voice", "pip"),
    "modecard.cmd_replies": ("ask", "auto"),
    "whoami.replies": ("bypass_safety", "bypass_role_denies", "can_manage"),
}
#: Words a language writes exactly as English does, reviewed: Turkish
#: "Normal" (a vocabulary level) and "(beta)".
_SHARED_WORDS = {"tr": {"normal", "beta"}}
#: Surfaces whose whole text is a word the language shares with English
#: (German's "📱 App" menu button).
_SAME_AS_ENGLISH = {("de", "menu.button")}
#: Names that pass through as they are (products, tools, models, commands
#: in sentences, the registry's data).
_NAMES = {w.lower() for name in core.NEVER_TRANSLATE + i18n_surfaces.KNOWN
          for w in _WORD.findall(name)}


def english_left(text: str, code: str, name: str = "", vocabulary=None) -> list[str]:
    """English words *text* still shows in *code*: a word of the English
    source that the language's catalog never writes, outside code, tags,
    commands, paths, mentions, numbers and names."""
    vocab = vocabulary if vocabulary is not None else _vocabulary(code)
    # An identifier written as it is (x1, g11, f12) is a name, not a word.
    rest = re.sub(r"\b[A-Za-z_][\w.-]*\d[\w.-]*", " ", text)
    rest = i18n_surfaces._NOT_TEXT.sub(" ", rest)
    known = _NAMES | {w.lower() for v in i18n_surfaces.KNOWN_EXTRA.get(name, ())
                      + _SURFACE_DATA.get(name, ()) for w in _WORD.findall(v)}
    return [w for w in _WORD.findall(rest)
            if len(w) > 1 and w.lower() in _ENGLISH and w.lower() not in vocab and w.lower() not in known]


def _texts(renders):
    for name, texts in renders.items():
        for text in texts:
            if name == "menu.commands":
                text = text.split(" ", 1)[1]      # the command word is the menu's own
            yield name, text


def _numbers_outside_names(text: str) -> list[str]:
    rest = _CODE.sub(" ", text)
    rest = _ISOLATED.sub(" ", rest)
    rest = _TAG.sub(" ", rest)
    for data in _DATA:
        rest = rest.replace(data, " ")
    rest = re.sub(r"(?<!\w)/[\w./-]+|https?://\S+|@\w+|AIPAGER_\w+=\d+", " ", rest)
    rest = re.sub(r"\b[A-Za-z_][\w.-]*\d[\w.-]*", " ", rest)
    return re.findall(r"[0-9]+", rest)


def _first_strong(line: str):
    for ch in _TAG.sub("", line):
        if ch == RLM:
            return "R"
        if _LETTER.match(ch):
            return "R" if ARABIC.match(ch) else "L"
    return None


# ---- every surface ------------------------------------------------------------------------

def test_the_word_check_sees_english():
    """Guard the guard: an English sentence fails it in every language; a
    translated one and the names it carries pass."""
    for code in CODES:
        assert english_left("Waiting for your answer", code)
        assert english_left(i18n.get(code).text(i18n.Msg(
            "card.verb.waiting", "Waiting")), code) == []
    assert english_left("Bash · x1 · Opus", "de") == []


def test_an_untranslated_entry_cannot_vouch_for_its_own_words(monkeypatch):
    """An entry left exactly as the English adds none of its words to the
    language's vocabulary; a translated entry adds its own."""
    key, english = "sess.resumed_in_ask", "♻️ Resumed <b>{session}</b> in Ask: Auto needs an admin."
    assert english in tool.english_forms(next(r for r in _source_rows() if r["k"] == key))
    monkeypatch.setattr(core, "_catalog", lambda code: {key: english})
    assert "resumed" not in _vocabulary("de")
    monkeypatch.setattr(core, "_catalog", lambda code: {key: "Resumed fortgesetzt {session}"})
    assert {"resumed", "fortgesetzt"} <= _vocabulary("de")


@pytest.mark.parametrize("code", CODES)
def test_every_surface_reads_in_the_language(code, mk_bot, run_async, monkeypatch):
    names = {n for n, _f in i18n_surfaces.SURFACES} - i18n_surfaces.PENDING
    renders = i18n_surfaces.render_all(mk_bot, run_async, monkeypatch, code, names=names)
    assert set(renders) == names
    vocab = _vocabulary(code)
    own = vocab - _ENGLISH
    leaks, untranslated = {}, []
    for name, texts in renders.items():
        if not any(_words(t) & own for t in texts) and (code, name) not in _SAME_AS_ENGLISH:
            untranslated.append(name)
        for text in texts:
            left = english_left(text, code, name, vocab)
            if left:
                leaks.setdefault(name, []).append((text, left))
            assert "—" not in text, (name, text)
            assert "{" not in _CODE.sub("", text) or "{{" in text, (name, text)
            assert not FA_DIGIT.search(text), (name, text)
            if code != "ar":
                assert not any(m in text for m in MARKS + (ALM,)), (name, text)
                assert not AR_DIGIT.search(text), (name, text)
    assert not leaks, leaks
    assert not untranslated, untranslated
    if code in ("uk", "ar"):
        script = CYRILLIC if code == "uk" else ARABIC
        missing = [n for n, texts in renders.items() if not any(script.search(t) for t in texts)]
        assert not missing, missing


def test_arabic_reads_right_to_left_with_its_own_digits(mk_bot, run_async, monkeypatch):
    names = {n for n, _f in i18n_surfaces.SURFACES} - i18n_surfaces.PENDING
    renders = i18n_surfaces.render_all(mk_bot, run_async, monkeypatch, "ar", names=names)
    seen_left = False
    for name, text in _texts(renders):
        for line in text.split("\n"):
            if ARABIC.search(_ISOLATED.sub("", _CODE.sub("", line))):
                assert _first_strong(line) == "R", (name, line)
        for span in _CODE.findall(text):
            assert not any(m in span for m in MARKS), (name, span)
        assert not re.search("(?<![\\w./-])[/@][" + RLM + FSI + PDI + "]", text), (name, text)
        assert not re.search("[A-Za-z0-9_][" + RLM + FSI + PDI + "][A-Za-z0-9_]", text), (
            name, text)
        assert not re.search(r"x[٠-٩]", text), (name, text)
        outside = _CODE.sub(" ", text)
        assert "→" not in outside, (name, text)
        seen_left = seen_left or "←" in outside
        if ARABIC.search(text):
            assert _numbers_outside_names(text) == [], (name, text)
    assert seen_left
    assert any("/x1" in t for t in renders["menu.commands"])
    status = renders["card.busy"][0].rsplit("\n", 1)[-1]
    assert "٤:١٠" in status and "4:10" not in status


# ---- the golden surfaces, by name -----------------------------------------------------------

@pytest.mark.parametrize("code", CODES)
def test_the_first_contact_line(code, monkeypatch):
    from aipager.bot import language_menu
    line = language_menu.first_contact_text(i18n.get(code))
    endonym = locales.LOCALES[code].endonym
    assert endonym in line and "/language" in line and "aipager" in line
    assert english_left(line, code) == []
    assert line.startswith(RLM) == (code == "ar")
    if code == "ar":
        assert FSI + endonym + PDI in line


@pytest.mark.parametrize("code", CODES)
def test_the_golden_surfaces(code, mk_bot, run_async, monkeypatch):
    golden = ("card.busy", "card.final", "prompt.separate", "prompt.inline", "answer.headers",
              "settings.root", "language.list", "status.list", "route.no_session",
              "auth.refusals", "taps.gate", "route.stop")
    renders = i18n_surfaces.render_all(mk_bot, run_async, monkeypatch, code, names=set(golden))
    cat = core._catalog(code)
    flat = "\n".join(t for texts in renders.values() for t in texts)
    plain = flat
    for mark in MARKS:
        plain = plain.replace(mark, "")
    # The permission prompt's buttons and the answered line's verb.
    for key in ("perm.btn.allow", "perm.btn.deny", "ans.verb.allow"):
        assert cat[key] in plain, key
    # /language lists the language by its own name, with no "(beta)" mark.
    lang = _strip("\n".join(renders["language.list"]))
    assert locales.LOCALES[code].endonym in lang and "(beta)" not in lang
    # /settings opens on the language row and the catalog's title.
    assert cat["lang.row"].split("{")[0].strip() in "\n".join(renders["settings.root"])
    for name in golden:
        for text in renders[name]:
            assert english_left(text, code, name) == [], (name, text)


# ---- plural forms ----------------------------------------------------------------------------

def _strip(text: str) -> str:
    for mark in MARKS:
        text = text.replace(mark, "")
    return text


def _form(code: str, key: str, n: int, **params) -> str:
    tr = i18n.get(code)
    forms = core._catalog(code)[key]
    text = forms[plurals.category(code, n)].replace("{n}", tr.num(n))
    for name, value in params.items():
        text = text.replace("{" + name + "}", value)
    return text


def _working_header(n: int, tr) -> str:
    """The first line of an answer's status block with *n* agents working."""
    view = agents_line.BlockView(agents=(agents_line.AgentRow("a", False),) * n)
    return agents_line.render_block(view, tr).split("\n\n")[0]


@pytest.mark.parametrize("code", CODES)
def test_every_plural_entry_has_exactly_the_languages_forms(code):
    cats = set(plurals.categories(code))
    entries = {k: v for k, v in core._catalog(code).items() if isinstance(v, dict)}
    assert len(entries) == sum(1 for r in _source_rows() if "en" not in r)
    for key, value in entries.items():
        assert set(value) == cats, key
    assert [plurals.category(code, n) for n in COUNTS] == WANT_CATEGORIES[code]
    if code == "ar":
        assert plurals.category("ar", 0) == "zero"
        fold = core._catalog("ar")["card.fold"]
        # Every form but "many" and "other" (11-99 and 100+ both take the
        # singular noun) reads differently.
        assert len({fold[c] for c in cats}) == 5
        assert len({fold[c] for c in cats - {"other"}}) == 5
    if code == "uk":
        fold = core._catalog("uk")["card.fold"]
        assert len({fold["one"], fold["few"], fold["many"]}) == 3


@pytest.mark.parametrize("code", CODES)
def test_plural_forms_through_the_code(code, mk_bot, run_async, monkeypatch):
    preferences.set_ui_language(i18n_surfaces.CHAT, code)
    tr = i18n.get(code)
    clock = tr.clock(65)
    for n in _counts(code):
        assert _strip(animation._tool_fold_summary(n, tr)) == _form(code, "card.fold", n), n
        row = card_rows.render_md(tr, settled_agent_row("Explore", n, 65))
        assert _strip(row) == _form(code, "card.row.agent_done", n, agent="`Explore`",
                                    clock=clock), n
        if n:
            line = _strip(_working_header(n, tr))
            assert line == _form(code, "agents.head.working", n), (n, line)
        if n:   # a held answer is late by a minute at least
            assert _strip(held_message.late_marker_text(tr, n * 60)) == _form(
                code, "held.late_marker", n), n
        log = animation.build_full_log("x1", [("Read: /a", True)] * n, [], "", tr=tr)
        assert _strip(log.split("\n")[2]) == _form(code, "log.memory", n), n
        if code == "ar":
            assert not re.search("[0-9]", _form(code, "card.fold", n))


@pytest.mark.parametrize("code", CODES)
def test_status_counts_sessions_in_the_language(code, mk_bot, run_async, monkeypatch):
    from aipager.state import Status
    monkeypatch.setattr(dashboard, "CHAT_ID", str(i18n_surfaces.CHAT))
    monkeypatch.setattr(dashboard, "get_rate_limiter", lambda: None)
    preferences.set_ui_language(i18n_surfaces.CHAT, code)
    for n in (1, 2, 3, 5, 11, 21, 22):
        bot = mk_bot()
        bot._read_status_file = lambda name: None
        sessions = [i18n_surfaces._sess(name=f"claude-s{i}", label=f"s{i}",
                                        status=Status.IDLE) for i in range(n)]
        i18n_surfaces._registered(bot, *sessions)
        text, _kb = bot._render_status_list(i18n_surfaces.CHAT)
        assert _strip(text).startswith(_form(code, "status.sessions", n)), (n, text)
        if n > 1:
            first = bot._render_pinned(i18n_surfaces.CHAT, sessions)[0].split("\n")[0]
            assert _strip(first) == "💤 " + _form(code, "pin.count.idle", n), n


# ---- length budgets ----------------------------------------------------------------------

@pytest.mark.parametrize("code", CODES)
def test_rendered_labels_fit_their_budgets(code, mk_bot, run_async, monkeypatch):
    """The / menu descriptions, the reply keyboard and the toasts as
    rendered, against the budgets of check 7 (with the marks Arabic
    adds)."""
    renders = i18n_surfaces.render_all(
        mk_bot, run_async, monkeypatch, code,
        names={"menu.commands", "keyboard.main", "keyboard.templates", "keyboard.commands"})
    for text in renders["menu.commands"]:
        assert len(text.split(" ", 1)[1]) <= 40 + 4, text
    for name in ("keyboard.main", "keyboard.templates", "keyboard.commands"):
        for label in renders[name]:
            assert tool.cells(label) <= 24 + 2, (name, label)
    for row in _source_rows():
        if row["kind"] != "toast":
            continue
        value = core._catalog(code)[row["k"]]
        for form in (value.values() if isinstance(value, dict) else [value]):
            assert tool.check_budget(row["k"], form, src=row, code=code) == [], row["k"]


def test_the_catalogs_are_complete_and_check_clean():
    rows = _source_rows()
    for code in CODES:
        assert locales.LOCALES[code].enabled and locales.LOCALES[code].quality == "beta"
        assert len(core._catalog(code)) == len(rows), code
    assert locales.AUTO_DETECT is True    # phase 5: detection on
    problems = tool.check_catalogs(rows, tool.all_catalogs(), CODES,
                                   glossaries=tool.all_glossaries())
    assert problems == [], problems[:10]
    assert json.dumps(core._catalog("ar"), ensure_ascii=False).count("→") == 0


@pytest.mark.parametrize("code", CODES)
def test_a_stranger_is_refused_in_their_telegram_language(code):
    """Like Persian and Russian: a stranger's refusal follows their Telegram
    language once it is offered, with the user id and the command intact."""
    from types import SimpleNamespace

    from aipager.bot import auth
    tr = auth._stranger_tr(SimpleNamespace(id=123456789, language_code=code))
    assert tr.code == code
    text = tr.text(auth._REFUSE_UNKNOWN_DM, user_id="123456789",
                   command=i18n.Span("`aipager config`"))
    assert english_left(text, code) == []
    assert "123456789" in text and "`aipager config`" in text
