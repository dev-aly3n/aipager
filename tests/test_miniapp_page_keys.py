"""The Mini App page holds keys, and every key is real (multilingual
phase 4).

The page's English lives in ``aipager/miniapp/ui_text.py`` (so the
extract, the catalog checks and the translation workflow see it) and
reaches the page as ``AIPAGER_UI_EN``. These tests keep the three in
step: every ``data-i18n*`` key in the markup and every key the script
names exists, the markup's inline English is that key's English, no
declared string is unused, and no English sentence is left hard-coded in
the script or the markup.
"""

from __future__ import annotations

import html.parser
import json
import re
from pathlib import Path

from aipager import i18n
from aipager.i18n import Plural
from aipager.miniapp import ui_text
from aipager.miniapp.static._app import APP_JS
from aipager.miniapp.static._shell import HTML_BODY

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "i18n" / "source.jsonl"

#: Static strings that hold a command (mirrors the script's STATIC_PARAMS).
STATIC_PARAMS = {"ui.empty.text": {"command": "/new"}}


def _keys() -> dict:
    return {m.key: m for m in ui_text.PAGE_STRINGS}


class _Markup(html.parser.HTMLParser):
    """Every element's attributes and its own text, in order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[dict] = []
        self.elements: list[dict] = []
        self.texts: list[tuple[str, list[dict]]] = []

    _VOID = {"input", "meta", "br", "img", "hr", "link", "use"}

    def handle_starttag(self, tag, attrs):
        el = {"tag": tag, "attrs": dict(attrs), "text": ""}
        self.elements.append(el)
        if tag not in self._VOID:
            self.stack.append(el)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.stack:
            self.stack[-1]["text"] += data
        if data.strip():
            self.texts.append((data, list(self.stack)))


def _markup() -> _Markup:
    parser = _Markup()
    parser.feed(HTML_BODY)
    parser.close()
    return parser


def _english(key: str) -> str:
    msg = _keys()[key]
    return i18n.get("en").text(msg, **STATIC_PARAMS.get(key, {}))


def test_static_keys_exist_and_match():
    source = {json.loads(line)["k"] for line in SOURCE.read_text(encoding="utf-8").splitlines()}
    keys = _keys()
    seen = 0
    for el in _markup().elements:
        for attr, part in (("data-i18n", "text"), ("data-i18n-label", "aria-label"),
                           ("data-i18n-placeholder", "placeholder")):
            key = el["attrs"].get(attr)
            if key is None:
                continue
            seen += 1
            assert key in keys, (attr, key)
            assert key in source, key
            assert not isinstance(keys[key], Plural), key
            shown = el["text"] if part == "text" else el["attrs"].get(part)
            assert shown == _english(key), (key, shown)
    assert seen >= 50


def test_the_static_params_are_the_scripts():
    assert 'var STATIC_PARAMS = { "ui.empty.text": { command: "/new" } };' in APP_JS


def _script_keys() -> set[str]:
    return set(re.findall(r'"((?:ui|app)\.[a-z0-9_.]+)"', APP_JS))


def test_script_keys_exist():
    keys = _keys()
    missing = sorted(k for k in _script_keys() if k not in keys)
    assert not missing, missing
    assert len(_script_keys()) > 250


def _named_elsewhere() -> set[str]:
    """Keys of page strings other modules use by name (``ui_text.AGO_S``)."""
    names = set()
    for path in (ROOT / "aipager" / "miniapp").glob("*.py"):
        if path.name != "ui_text.py":
            names |= set(re.findall(r"ui_text\.([A-Z][A-Z0-9_]+)",
                                    path.read_text(encoding="utf-8")))
    return {getattr(ui_text, n).key for n in names
            if hasattr(getattr(ui_text, n, None), "key")}


def test_no_unused_key():
    markup = set()
    for el in _markup().elements:
        for attr in ("data-i18n", "data-i18n-label", "data-i18n-placeholder"):
            if attr in el["attrs"]:
                markup.add(el["attrs"][attr])
    used = _script_keys() | markup | _named_elsewhere()
    unused = sorted(set(_keys()) - used)
    assert not unused, unused


# ---- no English left hard-coded ---------------------------------------------------------

_REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^")


def js_strings(source: str) -> list[tuple[int, str]]:
    """Every string literal in *source* (its start offset and its text),
    with comments and regular expression literals skipped."""
    out = []
    i, n = 0, len(source)
    last = ""            # the last significant character before here
    word = ""            # the last identifier, for "return /re/"
    while i < n:
        c = source[i]
        if c in " \t\r\n":
            i += 1
            continue
        if source.startswith("//", i):
            i = source.find("\n", i)
            i = n if i < 0 else i
            continue
        if source.startswith("/*", i):
            i = source.index("*/", i) + 2
            continue
        if c in "\"'`":
            j = i + 1
            buf = []
            while source[j] != c:
                if source[j] == "\\":
                    buf.append(source[j:j + 2])
                    j += 2
                    continue
                buf.append(source[j])
                j += 1
            out.append((i, "".join(buf)))
            i = j + 1
            last, word = c, ""
            continue
        if c == "/" and (last in _REGEX_BEFORE or last == "" or word in ("return", "typeof")):
            j = i + 1
            in_class = False
            while True:
                ch = source[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == "[":
                    in_class = True
                elif ch == "]":
                    in_class = False
                elif ch == "/" and not in_class:
                    break
                j += 1
            i = j + 1
            while i < n and source[i].isalpha():
                i += 1
            last, word = "/", ""
            continue
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (source[j].isalnum() or source[j] in "_$"):
                j += 1
            word = source[i:j]
            last = "a"
            i = j
            continue
        last, word = c, ""
        i += 1
    return out


#: Literals that are not page text: an internal error message and a
#: product's name (never translated).
NOT_TEXT = {"HTTP ", "Claude Code"}


def prose(text: str) -> bool:
    """An English sentence: a space, a word of three letters or more, and
    a capital first letter or a sentence's last character."""
    if " " not in text or not re.search(r"[A-Za-z]{3,}", text):
        return False
    if text.lstrip().startswith("<"):
        return False
    stripped = text.strip()
    return bool(re.match(r"[A-Z]", stripped)) or stripped.endswith((".", "…", "?", ":"))


def hard_coded_english(source: str = APP_JS) -> list[str]:
    return [text for _at, text in js_strings(source) if prose(text) and text not in NOT_TEXT]


def test_no_hard_coded_english():
    assert hard_coded_english() == []


def test_the_scanner_sees_a_sentence():
    assert hard_coded_english('var x = "No changes.";') == ["No changes."]
    assert hard_coded_english("showNotice('Saved now.', \"ok\");") == ["Saved now."]
    assert hard_coded_english('x.replace(/"Not a string. /g, "")') == []
    assert hard_coded_english('// "Old comment."\nvar a = t("ui.diff.no_changes");') == []
    assert hard_coded_english('return /a "b c." /.test(s) ? "Nope, it is." : "";') == [
        "Nope, it is."]


#: Markup text that is a name or a number, never translated.
NAMES = {"aipager", "1", "2", "3", "+", "0 / 500"}
#: Placeholders that are examples of a name.
EXAMPLES = {"frontend", "claude-opus-5", "my-project"}
#: aria-labels the script writes itself.
SCRIPT_LABELS = {"chat-switch"}


def test_no_hard_coded_english_in_the_markup():
    for text, stack in _markup().texts:
        if text.strip() in NAMES:
            continue
        holder = stack[-1] if stack else {"attrs": {}}
        assert "data-i18n" in holder["attrs"], text
    for el in _markup().elements:
        attrs = el["attrs"]
        if "placeholder" in attrs and attrs["placeholder"] not in EXAMPLES:
            assert "data-i18n-placeholder" in attrs, attrs
        if "aria-label" in attrs and attrs.get("id") not in SCRIPT_LABELS:
            assert "data-i18n-label" in attrs, attrs


# ---- titles from the server, in any script ---------------------------------------------

_GROUP_TITLE_JS = r"""
const fs = require("fs");
const js = fs.readFileSync(process.argv[2], "utf8");
function grab(name) {
  const at = js.indexOf("  function " + name + "(");
  const end = js.indexOf("\n  }\n", at);
  const line = js.indexOf("\n", at);
  return js.slice(at, (js.slice(at, line).trim().endsWith("}") ? line : end + 4));
}
const groupTitle = new Function(grab("plain") + "\n" + grab("groupTitle") +
                                "\nreturn groupTitle;")();
const titles = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
console.log(JSON.stringify(titles.map(groupTitle)));
"""


def test_group_titles_keep_every_word(tmp_path):
    """The page drops a settings title's leading emoji (the chat's /settings
    shows it, the page has its own icons) and nothing else, in any script:
    an English title reads as it always did, a Russian or Persian one
    keeps every word."""
    import shutil
    import subprocess

    from aipager.bot.settings_menu import settings_schema
    from tests.i18n_tool import isolated_env

    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        import pytest
        pytest.skip("node not available")
    titles = {code: [g["title"] for g in settings_schema(tr=i18n.get(code))]
              for code in ("en", "ru", "fa")}
    (tmp_path / "app.js").write_text(APP_JS, encoding="utf-8")
    (tmp_path / "g.js").write_text(_GROUP_TITLE_JS, encoding="utf-8")
    flat = titles["en"] + titles["ru"] + titles["fa"]
    (tmp_path / "t.json").write_text(json.dumps(flat), encoding="utf-8")
    proc = subprocess.run([node, str(tmp_path / "g.js"), str(tmp_path / "app.js"),
                           str(tmp_path / "t.json")], capture_output=True, text=True,
                          timeout=60, env=isolated_env(tmp_path))
    assert proc.returncode == 0, proc.stderr
    got = dict(zip(flat, json.loads(proc.stdout)))
    for title in titles["en"]:
        # What the old Latin-only rule gave, byte for byte.
        assert got[title] == re.sub(r"^[^A-Za-z0-9]+\s+", "", title), title
    for title in titles["ru"] + titles["fa"]:
        words = title.replace("\u200f", "").split()[1:]
        assert got[title] == " ".join(words), (title, got[title])


def test_option_labels_keep_their_lead_separator():
    """The page's collapsed settings header shows an option's label up to
    its " - " (labelLead): every translation keeps that separator where the
    English has it, so Persian and Russian headers show the short lead too."""
    from aipager.i18n import core

    rows = [json.loads(line) for line in SOURCE.read_text(encoding="utf-8").splitlines()]
    leads = [r["k"] for r in rows if r["k"].startswith("set.opt.") and " - " in r.get("en", "")]
    assert len(leads) >= 6
    for code in ("fa", "ru"):
        for key in leads:
            assert " - " in core._catalog(code)[key], (code, key)
    assert "var cut = t.indexOf(\" - \");" in APP_JS
