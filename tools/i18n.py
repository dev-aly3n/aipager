#!/usr/bin/env python3
"""aipager's translation toolchain (dev only; never shipped in the wheel).

    python tools/i18n.py extract [--check]
    python tools/i18n.py check [--write-baseline]
    python tools/i18n.py todo --lang CODE [--changed]
    python tools/i18n.py apply --lang CODE FILE
    python tools/i18n.py prune [--dry-run]
    python tools/i18n.py review-pack [--lang CODE] [--changed] [--golden] [--answers FILE]
    python tools/i18n.py pseudo [--rtl] TEXT
    python tools/i18n.py docs

``extract`` reads every ``Msg(...)``, ``Plural(...)`` and inline
``tr.html|text|md("key", "English", ...)`` in ``aipager/`` and writes
``tools/i18n/source.jsonl``: one key per line with its English, kind,
length budget, placeholders, examples, file, enclosing function and note.
It records no line numbers, so an unrelated edit never churns the file.

``check`` runs the catalog checks of the multilingual design (section 9):
1 every key in every enabled language, 2 no stale translation, 3 the same
placeholders (3b: no command or mention glued to a placeholder), 4 the
same Telegram-allowed tags (4b: no backtick, the same ``**`` and ``_``
markers), 5 every plural form, 6 the glossary, 7 length budgets (with the
invisible marks a right-to-left language adds), 8 no long dash or
direction control, 9 native digits for fa/ar (9b: ``←`` there, never
``→``), 10 reply-keyboard labels, 16 catalog format, plus the literal
ratchet of check 11. The test suite imports these same functions,
so CI and this command can never disagree.

``docs`` checks the translated docs under ``docs/i18n/<code>/`` (one page
per English page, the source comment, links and the English anchors) and
lists the pages whose English page changed after the commit they were
translated from. Only the checks fail it; a page behind its English page
is listed, never an error.

``review-pack --golden`` gives a native reviewer the golden strings
(``tools/i18n/golden.txt``, the keys people see most) with their English;
``check`` keeps that list valid.

Agents never edit a catalog by hand: ``todo`` hands them a batch with
everything a translator needs, ``apply`` validates it and writes the
catalog. ``prune`` removes the entries of keys the code no longer has
(after ``extract``), in every catalog.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import html.parser
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aipager.i18n import core, locales, plurals  # noqa: E402

PACKAGE = ROOT / "aipager"
TOOL_DIR = ROOT / "tools" / "i18n"
SOURCE = TOOL_DIR / "source.jsonl"
GLOSSARY_DIR = TOOL_DIR / "glossary"
STYLE_DIR = TOOL_DIR / "style"
#: The rules every language follows (from the reviewed Persian); todo hands
#: them over before the language's own style guide.
RULES = TOOL_DIR / "TRANSLATING.md"
LOCALES_DIR = core.LOCALES_DIR
BASELINE = ROOT / "tests" / "i18n_literal_baseline.json"
GOLDEN = TOOL_DIR / "golden.txt"
#: The sections of the golden list, in order (design 8.5).
GOLDEN_SECTIONS = ("first_contact", "busy_card", "permission", "settings", "language",
                   "errors", "keyboard")
GOLDEN_MIN, GOLDEN_MAX = 120, 180

KEY_RE = core.KEY_RE
TRANSLATED = tuple(c for c in locales.LOCALES if c != "en")

# ---- keys: kind and length budget ------------------------------------------

#: (pattern, kind, unit, limit, fill). ``unit`` is "cells" (display width,
#: CJK counts 2) or "chars"; ``fill`` names what fills {language}.
_BUDGETS: tuple[tuple[str, str, str, int, str], ...] = (
    # Specific before generic: the first match wins.
    (r"kbl\.(tpl|cmd)\.[a-z0-9_]+", "keyboard", "cells", 24, ""),
    (r"kbmsg\.[a-z0-9_]+", "message", "chars", 40, ""),
    (r"kb\.[a-z0-9_.]+", "keyboard", "cells", 14, ""),
    (r"btn\.[a-z0-9_.]+", "button", "cells", 40, ""),
    (r"mode\.[a-z0-9_.]+", "button", "cells", 40, ""),
    (r"lang\.auto", "button", "cells", 40, ""),
    (r"lang\.row", "button", "cells", 40, "endonym"),
    (r"toast\.[a-z0-9_.]+", "toast", "chars", 200, ""),
    (r"cmd\.[a-z0-9_.]+", "menu", "chars", 40, ""),
    (r"lang\.list\.[a-z0-9_.]+", "message", "chars", 240, "endonym"),
    (r"lang\.changed", "message", "chars", 240, "endonym"),
    (r"lang\.offer\.text", "message", "chars", 240, "endonym"),
    (r"lang\.offer\.keep", "button", "cells", 24, ""),
    (r"set\.[a-z0-9_]+\.title", "label", "cells", 40, ""),
    (r"set\.btn\.[a-z0-9_.]+", "button", "cells", 40, ""),
    (r"set\.opt\.[a-z0-9_.]+", "button", "cells", 40, ""),
    (r"set\.help\.[a-z0-9_.]+", "hint", "chars", 200, ""),
    (r"set\.text\.[a-z0-9_.]+", "message", "chars", 400, ""),
    (r"app\.[a-z0-9_]+\.title", "label", "cells", 40, ""),
    (r"app\.[a-z0-9_]+\.hint", "hint", "chars", 80, ""),
    (r"card\.verb\.[a-z0-9_]+", "label", "cells", 24, ""),
    (r"card\.[a-z0-9_.]+", "card", "chars", 160, ""),
    (r"log\.[a-z0-9_.]+", "message", "chars", 200, ""),
    (r"agents\.[a-z0-9_.]+", "message", "chars", 200, ""),
    # The resume card's default mode button wraps a whole mode label
    # (whose own budget is 40 cells) and has a row of its own: it gets the
    # label's budget.
    (r"perm\.btn\.default_suffix", "button", "cells", 40, ""),
    (r"perm\.btn\.[a-z0-9_]+", "button", "cells", 24, ""),
    (r"perm\.[a-z0-9_.]+", "message", "chars", 300, ""),
    (r"ans\.[a-z0-9_.]+", "message", "chars", 200, ""),
    (r"fin\.[a-z0-9_.]+", "message", "chars", 120, ""),
    # The pinned bar's first line is what Telegram's pinned preview shows
    # (design 9.7): short in every language.
    (r"pin\.first\.[a-z0-9_.]+", "message", "chars", 64, ""),
    (r"pin\.btn\.[a-z0-9_]+", "button", "cells", 40, ""),
    (r"pin\.[a-z0-9_.]+", "message", "chars", 120, ""),
    (r"flood\.[a-z0-9_.]+", "message", "chars", 120, ""),
    (r"status\.btn\.[a-z0-9_]+", "button", "cells", 40, ""),
    (r"status\.[a-z0-9_.]+", "message", "chars", 200, ""),
    (r"lang\.beta", "button", "cells", 40, "endonym"),
    (r"lang\.first_contact", "message", "chars", 240, "endonym"),
    # The bot's descriptions (5.8): Telegram's own limits.
    (r"botdesc\.description", "message", "chars", 512, ""),
    (r"botdesc\.short", "message", "chars", 120, ""),
    # phase 3b: admin, lifecycle, groups
    (r"upd\.[a-z0-9_.]+", "message", "chars", 500, ""),
    (r"rep\.area\.[a-z0-9_]+", "label", "chars", 60, ""),
    (r"rep\.[a-z0-9_.]+", "message", "chars", 500, ""),
    (r"auth\.[a-z0-9_.]+", "message", "chars", 300, ""),
    (r"grp\.[a-z0-9_.]+", "message", "chars", 300, ""),
    (r"life\.[a-z0-9_.]+", "card", "chars", 160, ""),
    (r"tx\.[a-z0-9_.]+", "message", "chars", 300, ""),
    (r"resume\.[a-z0-9_.]+", "message", "chars", 300, ""),
    (r"ago\.[a-z0-9_.]+", "label", "chars", 40, ""),
    (r"diff\.[a-z0-9_.]+", "message", "chars", 120, ""),
    # The Mini App (phase 4): the page's buttons, its other text, and the
    # server's details, facts, reasons and chat lines.
    (r"ui\.btn\.[a-z0-9_.]+", "button", "cells", 40, ""),
    (r"ui\.[a-z0-9_.]+", "message", "chars", 300, ""),
    (r"app\.[a-z0-9_.]+", "message", "chars", 300, ""),
    # phase 3a: the conversation and the sessions. Pop-ups stay in toast.
    (r"help\.[a-z0-9_.]+", "message", "chars", 300, ""),
    (r"home\.btn\.[a-z0-9_]+", "button", "cells", 40, ""),
    (r"home\.[a-z0-9_.]+", "message", "chars", 200, ""),
    (r"route\.btn\.[a-z0-9_]+", "button", "cells", 40, ""),
    (r"route\.[a-z0-9_.]+", "message", "chars", 300, ""),
    (r"appcmd\.btn\.[a-z0-9_]+", "button", "cells", 40, ""),
    (r"appcmd\.[a-z0-9_.]+", "message", "chars", 300, ""),
    (r"whoami\.[a-z0-9_.]+", "message", "chars", 200, ""),
    (r"modecard\.btn\.[a-z0-9_]+", "button", "cells", 40, ""),
    (r"modecard\.[a-z0-9_.]+", "message", "chars", 300, ""),
    (r"media\.btn\.[a-z0-9_]+", "button", "cells", 40, ""),
    (r"media\.[a-z0-9_.]+", "message", "chars", 400, ""),
    (r"newflow\.btn\.[a-z0-9_]+", "button", "cells", 40, ""),
    (r"newflow\.[a-z0-9_.]+", "message", "chars", 400, ""),
    (r"sess\.btn\.[a-z0-9_]+", "button", "cells", 40, ""),
    (r"sess\.[a-z0-9_.]+", "message", "chars", 400, ""),
    (r"safety\.[a-z0-9_.]+", "message", "chars", 400, ""),
    (r"held\.[a-z0-9_.]+", "message", "chars", 120, ""),
    (r"sendnow\.btn\.[a-z0-9_]+", "button", "cells", 40, ""),
    (r"sendnow\.[a-z0-9_.]+", "message", "chars", 200, ""),
    (r"notice\.[a-z0-9_.]+", "message", "chars", 700, ""),
    (r"chatmove\.[a-z0-9_.]+", "message", "chars", 300, ""),
)


def budget_for(key: str) -> dict | None:
    """``{"kind", "unit", "limit", "fill"}`` for *key*, or None when its
    prefix has no budget (which fails check 7)."""
    for pattern, kind, unit, limit, fill in _BUDGETS:
        if re.fullmatch(pattern, key):
            return {"kind": kind, "unit": unit, "limit": limit, "fill": fill}
    return None


def cells(text: str) -> int:
    """Display width: wide and full-width characters count 2, combining
    marks and variation selectors 0."""
    width = 0
    for ch in text:
        if unicodedata.combining(ch) or ch in "\ufe0f\u200d\u200c":
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def src_hash(entry: dict) -> str:
    """8 hex digits of the sha1 of the English; a Plural hashes its forms
    joined by U+001F, so any changed form makes every category stale."""
    if "en" in entry:
        text = entry["en"]
    else:
        text = entry["one"] + "\x1f" + entry["other"]
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def english_forms(entry: dict) -> list[str]:
    return [entry["en"]] if "en" in entry else [entry["one"], entry["other"]]


# ---- extract -----------------------------------------------------------------

class ExtractError(Exception):
    pass


def _literal(node, what: str, where: str) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    raise ExtractError(f"{where}: {what} must be a string literal")


def _call_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


class _Visitor(ast.NodeVisitor):
    def __init__(self, rel: str):
        self.rel = rel
        self.stack: list[str] = []
        self.found: list[dict] = []

    def _scoped(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scoped

    def visit_Call(self, node: ast.Call):
        name = _call_name(node)
        where = f"{self.rel}:{node.lineno}"
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        entry = None
        if name == "Msg" and isinstance(node.func, (ast.Name, ast.Attribute)):
            if len(node.args) < 2:
                raise ExtractError(f"{where}: Msg needs a key and its English")
            entry = {"k": _literal(node.args[0], "the key", where),
                     "en": _literal(node.args[1], "the English", where)}
        elif name == "Plural":
            if not node.args or "one" not in kw or "other" not in kw:
                raise ExtractError(f"{where}: Plural needs a key, one= and other=")
            entry = {"k": _literal(node.args[0], "the key", where),
                     "one": _literal(kw["one"], "one=", where),
                     "other": _literal(kw["other"], "other=", where)}
        elif (name in ("html", "text", "md") and isinstance(node.func, ast.Attribute)
              and len(node.args) >= 2 and isinstance(node.args[0], ast.Constant)
              and isinstance(node.args[0].value, str)
              and KEY_RE.match(node.args[0].value)):
            entry = {"k": node.args[0].value,
                     "en": _literal(node.args[1], "the English", where)}
        if entry is not None:
            if not KEY_RE.match(entry["k"]):
                raise ExtractError(f"{where}: bad key {entry['k']!r}")
            note = kw.get("note")
            entry["note"] = _literal(note, "note=", where) if note is not None else ""
            eg = kw.get("eg")
            try:
                entry["eg"] = ast.literal_eval(eg) if eg is not None else {}
            except ValueError as e:
                raise ExtractError(f"{where}: eg= must be a literal dict") from e
            entry["file"] = self.rel
            entry["scope"] = ".".join(self.stack) or "<module>"
            self.found.append(entry)
        self.generic_visit(node)


def extract_entries(package: Path = PACKAGE) -> list[dict]:
    """Every key in *package*, sorted, with its metadata. Raises
    :class:`ExtractError` on a non-literal, a bad key, or one key with
    two different Englishes."""
    by_key: dict[str, dict] = {}
    for path in sorted(package.rglob("*.py")):
        rel = path.relative_to(package.parent).as_posix()
        visitor = _Visitor(rel)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=rel))
        for entry in visitor.found:
            old = by_key.get(entry["k"])
            if old is None:
                by_key[entry["k"]] = entry
            elif english_forms(old) != english_forms(entry):
                raise ExtractError(
                    f"{entry['file']} ({entry['scope']}): key {entry['k']!r} has two "
                    f"different Englishes (also in {old['file']})")
    out = []
    for key in sorted(by_key):
        e = by_key[key]
        budget = budget_for(key) or {"kind": "", "unit": "", "limit": 0, "fill": ""}
        holders: list[str] = []
        for form in english_forms(e):
            for name in core.placeholders(form):
                if name not in holders:
                    holders.append(name)
        row = {"k": key}
        if "en" in e:
            row["en"] = e["en"]
        else:
            row["one"], row["other"] = e["one"], e["other"]
        row.update({"kind": budget["kind"], "budget": budget, "placeholders": holders,
                    "eg": e["eg"], "file": e["file"], "scope": e["scope"],
                    "note": e["note"]})
        out.append(row)
    return out


def dump_jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def check_source_sync(package: Path = PACKAGE, source: Path = SOURCE) -> list[str]:
    try:
        want = dump_jsonl(extract_entries(package))
    except ExtractError as e:
        return [f"extract: {e}"]
    have = source.read_text(encoding="utf-8") if source.exists() else ""
    if have != want:
        return [f"{source.name} is out of date: run python tools/i18n.py extract"]
    return []


# ---- catalogs ------------------------------------------------------------------

def check_catalog_format(path: Path) -> list[str]:
    """Check 16: sorted by key, exactly {k, v, src} per line, one object
    per line, no duplicate key, UTF-8 with a trailing newline."""
    name = path.name
    try:
        raw = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return [f"{name}: not UTF-8"]
    if raw == "":
        return []
    problems = []
    if not raw.endswith("\n"):
        problems.append(f"{name}: no trailing newline")
    keys: list[str] = []
    for n, line in enumerate(raw.split("\n")[:-1] if raw.endswith("\n") else raw.split("\n"), 1):
        try:
            obj = json.loads(line)
        except ValueError:
            problems.append(f"{name}:{n}: not one JSON object")
            continue
        if not isinstance(obj, dict) or set(obj) != {"k", "v", "src"}:
            problems.append(f"{name}:{n}: must hold exactly k, v and src")
            continue
        if not isinstance(obj["k"], str) or not isinstance(obj["src"], str):
            problems.append(f"{name}:{n}: k and src must be strings")
            continue
        if not isinstance(obj["v"], (str, dict)):
            problems.append(f"{name}:{n}: v must be a string or an object of forms")
        keys.append(obj["k"])
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        problems.append(f"{name}: duplicate keys {dupes}")
    if keys != sorted(keys):
        problems.append(f"{name}: not sorted by key")
    return problems


def load_catalog(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        if line.strip():
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict) and isinstance(obj.get("k"), str):
                out[obj["k"]] = obj
    return out


_ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "span",
                 "tg-spoiler", "a", "code", "pre", "blockquote", "tg-emoji", "tg-time"}
_ALLOWED_ENTITIES = {"&lt;", "&gt;", "&amp;", "&quot;"}


class _Tags(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.tags: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)

    def handle_endtag(self, tag):
        self.tags.append("/" + tag)


def tags_of(text: str) -> list[str]:
    parser = _Tags()
    parser.feed(text)
    parser.close()
    return parser.tags


def _entities(text: str) -> list[str]:
    return re.findall(r"&(?:[A-Za-z]+|#\d+|#x[0-9A-Fa-f]+);", text)


def _forms(value) -> list[str]:
    if isinstance(value, dict):
        return [v for v in value.values() if isinstance(v, str)]
    return [value] if isinstance(value, str) else []


def _counts_n(src: dict) -> bool:
    """True when the English 'other' form shows the count."""
    return "n" in _holders_or_empty(src["other"])


def _holders_or_empty(form: str) -> list[str]:
    try:
        return core.placeholders(form)
    except ValueError:
        return []


def _single_number(code: str, cat: str) -> bool:
    """True when plural category *cat* of *code* covers exactly one whole
    number (Arabic zero/one/two, English-style one), so its form may name
    the count in words instead of showing {n}."""
    return sum(1 for n in range(1001) if plurals.category(code, n) == cat) == 1


def check_translation(code: str, src: dict, value, *, glossary: dict | None = None
                      ) -> list[str]:
    """Checks 3-7 and 9 for one translated value of *src* (a source.jsonl
    row) in language *code*."""
    key = src["k"]
    problems: list[str] = []
    plural = "en" not in src
    if plural:
        if not isinstance(value, dict):
            return [f"{code} {key}: a plural key needs an object of forms"]
        missing = [c for c in plurals.categories(code) if not isinstance(value.get(c), str)]
        if missing:
            problems.append(f"{code} {key}: missing plural forms {missing}")
        extra = sorted(set(value) - set(plurals.categories(code)))
        if extra:
            problems.append(f"{code} {key}: forms {extra} are not used in {code}")
    elif not isinstance(value, str):
        return [f"{code} {key}: must be a string"]
    want_holders = set(src.get("placeholders", []))
    english_tags = tags_of(english_forms(src)[0])
    if plural and isinstance(value, dict) and _counts_n(src):
        for cat in plurals.categories(code):
            form = value.get(cat)
            if (isinstance(form, str) and not _single_number(code, cat)
                    and "n" not in _holders_or_empty(form)):
                problems.append(f"{code} {key}: the {cat!r} form must show {{n}}: "
                                "it covers more than one count")
    for form in _forms(value):
        try:
            got = set(core.placeholders(form))
        except ValueError as e:
            problems.append(f"{code} {key}: {e}")
            continue
        same = ((got - {"n"}) == (want_holders - {"n"}) if plural
                else got == want_holders)
        if not same:
            problems.append(f"{code} {key}: placeholders {sorted(got)} != "
                            f"{sorted(want_holders)}")
        tags = tags_of(form)
        bad = sorted({t.lstrip("/") for t in tags} - _ALLOWED_TAGS)
        if bad:
            problems.append(f"{code} {key}: tags Telegram does not allow: {bad}")
        if sorted(tags) != sorted(english_tags):
            problems.append(f"{code} {key}: tags {tags} != the English {english_tags}")
        ents = sorted(set(_entities(form)) - _ALLOWED_ENTITIES)
        if ents:
            problems.append(f"{code} {key}: entities {ents} (only &lt; &gt; &amp; &quot;)")
        problems += check_pattern(f"{code} {key}", form)
        problems += check_markers(code, src, form)
        problems += check_digits(code, key, form)
        problems += check_arrows(code, key, form)
        problems += check_budget(key, form, src=src, code=code)
        if glossary is not None:
            problems += check_glossary(code, src, form, glossary)
    return problems


# ---- checks 3b and 4b: commands and Markdown markers ------------------------------

_GLUED = re.compile(r"[/@]\{")
_HOLDER = re.compile(r"\{\{|\}\}|\{[A-Za-z_][A-Za-z0-9_]*\}")


def check_pattern(where: str, form: str) -> list[str]:
    """3b: a ``/`` or ``@`` is never glued to a placeholder (the isolate a
    right-to-left language puts around a value would split the command):
    the whole command goes in as a ``Command`` value. 4b: no backtick in a
    pattern; a code span is a ``Span`` value."""
    problems = []
    if _GLUED.search(form):
        problems.append(f"{where}: a command or mention glued to a placeholder "
                        "(/{x} or @{x}); pass the whole command as a Command value")
    if "`" in form:
        problems.append(f"{where}: a backtick in a pattern; pass a code span as a "
                        "Span value")
    return problems


def _markers(text: str) -> tuple[int, int]:
    bare = _HOLDER.sub(" ", text)
    return bare.count("**"), bare.replace("**", "").count("_")


def check_markers(code: str, src: dict, form: str) -> list[str]:
    """4b: a translation keeps the English's bold (``**``) and italic
    (``_``) markers, counted outside placeholders."""
    want = _markers(english_forms(src)[0])
    got = _markers(form)
    if got != want:
        return [f"{code} {src['k']}: ** and _ markers {got} != the English {want}"]
    return []


def check_english_patterns(rows: list[dict]) -> list[str]:
    """3b and 4b over the English of every key."""
    problems = []
    for row in rows:
        for form in english_forms(row):
            problems += check_pattern(f"en {row['k']}", form)
    return problems


# ---- check 9b: arrows -----------------------------------------------------------------

_RTL_CODES = frozenset(c for c, loc in locales.LOCALES.items() if loc.rtl)


def check_arrows(code: str, key: str, form: str) -> list[str]:
    """A right-to-left catalog points with ``←`` (rule 4), never ``→``."""
    if code in _RTL_CODES and "→" in form:
        return [f"{code} {key}: → in a right-to-left entry; use ←"]
    return []


def check_catalogs(source_rows: list[dict], catalogs: dict[str, Path],
                   enabled: tuple[str, ...], *, glossaries: dict | None = None
                   ) -> list[str]:
    """Checks 1-7, 9 and 16 over *catalogs* (code -> path). Check 1
    applies to the *enabled* codes only."""
    by_key = {r["k"]: r for r in source_rows}
    problems: list[str] = []
    for code, path in sorted(catalogs.items()):
        problems += check_catalog_format(path)
        entries = load_catalog(path)
        if code in enabled:
            missing = sorted(set(by_key) - set(entries))
            if missing:
                problems.append(f"{code}: missing keys {missing[:20]}"
                                f"{' ...' if len(missing) > 20 else ''}")
        for key, entry in sorted(entries.items()):
            src = by_key.get(key)
            if src is None:
                problems.append(f"{code} {key}: no such key in the source")
                continue
            if entry.get("src") != src_hash(src):
                problems.append(f"{code} {key}: stale (its English changed); retranslate")
            gloss = (glossaries or {}).get(code)
            problems += check_translation(code, src, entry.get("v"), glossary=gloss)
    return problems


# ---- check 6: glossary ---------------------------------------------------------

_SLASH = re.compile(r"(?<![\w/])/[A-Za-z0-9_]+")


#: key -> {word: why}: a word check 6 does not hold one key to, because
#: that key uses it in another sense than the glossary's (``"Write"`` the
#: ordinary verb, not Claude Code's tool). Exact: only these words, only
#: for these keys; :func:`check_glossary_exemptions` fails a stale entry.
GLOSSARY_EXEMPT: dict[str, dict[str, str]] = {
    "kbl.tpl.write_tests": {"Write": "the ordinary verb (write tests), not the Write tool"},
    "set.btn.reports_turn_on": {"turn": "the verb (turn offers on), not a turn of Claude's"},
    "rep.offer.off": {"turn": "the verb (turn them back on), not a turn of Claude's"},
    "ui.report.label.stops": {"stop": "how aipager last exited, not the Stop action"},
    "ui.report.stops": {"stop": "how aipager last exited, not the Stop action"},
    "media.brew_footer": {"stop": "stops working (breaks), not the Stop action"},
}


def check_glossary_exemptions(rows: list[dict]) -> list[str]:
    """Every exemption names a key that exists and a word its English
    still uses."""
    by_key = {r["k"]: r for r in rows}
    problems = []
    for key, words in sorted(GLOSSARY_EXEMPT.items()):
        row = by_key.get(key)
        if row is None:
            problems.append(f"GLOSSARY_EXEMPT: no key {key!r} in the source")
            continue
        english = " ".join(english_forms(row))
        for word in words:
            if not re.search(rf"\b{re.escape(word)}s?\b", english, re.I):
                problems.append(f"GLOSSARY_EXEMPT: {key}'s English no longer says {word!r}")
    return problems


def load_glossary(code: str, base: Path = GLOSSARY_DIR) -> dict:
    return json.loads((base / f"{code}.json").read_text(encoding="utf-8"))


def check_glossary(code: str, src: dict, form: str, glossary: dict) -> list[str]:
    """Names and commands spelled exactly; a glossary term the English
    uses appears as one of its forms."""
    problems = []
    english = " ".join(english_forms(src))
    exempt = {w.lower() for w in GLOSSARY_EXEMPT.get(src["k"], {})}
    for word in core.NEVER_TRANSLATE:
        if word.lower() in exempt:
            continue
        if re.search(rf"\b{re.escape(word)}\b", english) and word not in form:
            problems.append(f"{code} {src['k']}: {word!r} must stay spelled exactly")
    for cmd in _SLASH.findall(english):
        if cmd not in form:
            problems.append(f"{code} {src['k']}: the command {cmd} must stay as it is")
    # A placeholder's name is not text: "Talk to {session}" holds no term.
    words = _HOLDER.sub(" ", english)
    for term, forms in (glossary.get("terms") or {}).items():
        if not forms or term.lower() in exempt:
            continue
        options = [forms] if isinstance(forms, str) else list(forms)
        if re.search(rf"\b{re.escape(term)}s?\b", words, re.I):
            if not any(o and o.lower() in form.lower() for o in options):
                problems.append(f"{code} {src['k']}: {term!r} must be one of {options}")
    return problems


# ---- check 7: budgets ------------------------------------------------------------

def longest_endonym(unit: str) -> str:
    measure = cells if unit == "cells" else len
    return max((loc.endonym for loc in locales.LOCALES.values()), key=measure)


def check_budget(key: str, text: str, *, src: dict | None = None, code: str = "en"
                 ) -> list[str]:
    budget = budget_for(key)
    if budget is None:
        return [f"{key}: no length budget for this key's prefix"]
    examples = dict((src or {}).get("eg") or {})
    filled = text
    for name in core.placeholders(text):
        if name == "language" and budget["fill"] == "endonym":
            value = longest_endonym(budget["unit"])
        else:
            value = str(examples.get(name, "{" + name + "}"))
        filled = filled.replace("{" + name + "}", value)
    measure = cells if budget["unit"] == "cells" else len
    size = measure(filled) + rtl_overhead(code, text, src)
    if size > budget["limit"]:
        return [f"{code} {key}: {size} {budget['unit']} > {budget['limit']}: {filled!r}"]
    return []


def rtl_overhead(code: str, text: str, src: dict | None) -> int:
    """The invisible marks a right-to-left language renders *text* with:
    the leading U+200F, one more per line, and an isolate pair around
    each text placeholder (a count, ``{n}`` or an example that is a
    number, gets none). Telegram counts them toward its limits too (a
    toast past 200 characters is dropped), so the budget does."""
    loc = locales.LOCALES.get(code) or locales.PSEUDO.get(code)
    if loc is None or not loc.rtl:
        return 0
    examples = (src or {}).get("eg") or {}
    text_holders = [name for name in core.placeholders(text)
                    if name != "n" and not isinstance(examples.get(name), (int, float))]
    return 1 + text.count("\n") + 2 * len(text_holders)


def check_english_budgets(rows: list[dict]) -> list[str]:
    problems = []
    for row in rows:
        for form in english_forms(row):
            problems += check_budget(row["k"], form, src=row)
    return problems


# ---- check 8: long dashes and direction controls ------------------------------------

def scan_text(name: str, text: str) -> list[str]:
    from aipager.bidi_guard import has_controls
    problems = []
    for n, line in enumerate(text.splitlines(), 1):
        if "\u2014" in line:
            problems.append(f"{name}:{n}: a long dash (U+2014); use - or parentheses")
        if has_controls(line):
            problems.append(f"{name}:{n}: a direction control character")
    return problems


def scan_files(paths) -> list[str]:
    problems = []
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            problems.append(f"{path}: not UTF-8")
            continue
        problems += scan_text(str(path), text)
    return problems


def scanned_files() -> list[Path]:
    files = sorted(LOCALES_DIR.glob("*.jsonl"))
    files += sorted(p for p in TOOL_DIR.rglob("*") if p.is_file())
    files.append(Path(__file__).resolve())
    return files


# ---- check 9: digits ----------------------------------------------------------------

_NATIVE = {"fa": "۰-۹", "ar": "٠-٩"}
_ALL_FOREIGN = "۰-۹٠-٩"
_CODE_SPAN = re.compile(r"<(code|pre)\b[^>]*>.*?</\1>|\{[A-Za-z_]\w*\}|<[^>]+>|&#?\w+;"
                        # An identifier written as it is (AIPAGER_REPORT_PROMPTS=0,
                        # x1): code, never text.
                        r"|[A-Za-z_][A-Za-z0-9_.=:/-]*[0-9][A-Za-z0-9_.=:/-]*",
                        re.S)


def check_digits(code: str, key: str, text: str) -> list[str]:
    """fa entries write numbers in ۰-۹, ar in ٠-٩, the rest in 0-9.
    Placeholders, tags and code are not text and are skipped."""
    bare = _CODE_SPAN.sub(" ", text)
    native = _NATIVE.get(code)
    if native:
        wrong = re.findall(rf"[0-9{_ALL_FOREIGN.replace(native, '')}]", bare)
        if wrong:
            return [f"{code} {key}: digits {sorted(set(wrong))} must be written as {code} digits"]
        return []
    wrong = re.findall(rf"[{_ALL_FOREIGN}]", bare)
    if wrong:
        return [f"{code} {key}: Persian or Arabic digits in a {code} entry"]
    return []


# ---- check 11: the literal ratchet --------------------------------------------------

_LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
_I18N_CALLS = {"Msg", "Plural", "html", "text", "md", "n"}
_WORDISH = re.compile(r"[A-Za-z]{2,}")


def _skipped_constants(tree: ast.AST) -> set[int]:
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, (ast.Constant, ast.JoinedStr)):
            skip.update(id(n) for n in ast.walk(node.value))
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            # ``log.debug(…)`` and friends, and the hook's own debug log
            # (``notify_hook._debug``): never shown to anyone.
            is_log = ((isinstance(node.func, ast.Attribute) and name in _LOG_METHODS)
                      or (isinstance(node.func, ast.Name) and node.func.id == "_debug"))
            if is_log or name in _I18N_CALLS:
                for arg in list(node.args) + [k.value for k in node.keywords]:
                    skip.update(id(n) for n in ast.walk(arg))
    return skip


def count_literals(source: str, filename: str = "<src>") -> int:
    """English-looking string literals in *source*: not a docstring or a
    bare string, not an argument of a log call or of Msg/Plural/tr, and
    holding a word of two or more ASCII letters and a space."""
    tree = ast.parse(source, filename=filename)
    skip = _skipped_constants(tree)
    count = 0
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in skip and " " in node.value
                and _WORDISH.search(node.value)):
            count += 1
    return count


def literal_counts(package: Path = PACKAGE) -> dict[str, int]:
    out = {}
    for path in sorted(package.rglob("*.py")):
        rel = path.relative_to(package.parent).as_posix()
        n = count_literals(path.read_text(encoding="utf-8"), rel)
        if n:
            out[rel] = n
    return out


def check_ratchet(counts: dict[str, int], baseline: dict[str, int]) -> list[str]:
    """Per-file counts may only go down, and the baseline follows them
    down (``check --write-baseline``) so a later increase is caught."""
    problems = []
    for rel in sorted(set(counts) | set(baseline)):
        now, was = counts.get(rel, 0), baseline.get(rel, 0)
        if now > was:
            problems.append(f"{rel}: {now - was} new hard-coded English string(s) "
                            f"({was} -> {now}); route user-facing text through "
                            "aipager.i18n (Msg/tr)")
        elif now < was:
            problems.append(f"{rel}: {was} -> {now} literals; run "
                            "python tools/i18n.py check --write-baseline")
    return problems


def write_baseline(counts: dict[str, int], path: Path = BASELINE) -> None:
    path.write_text(json.dumps(counts, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def load_baseline(path: Path = BASELINE) -> dict[str, int]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# ---- check 10: reply-keyboard labels --------------------------------------------------

def check_keyboard_labels(codes) -> list[str]:
    """No two keyboard actions share a label in any language, and no
    Latin label could be a session name unless reserved or grandfathered."""
    from aipager.bot import kbwords
    return kbwords.label_problems(codes)


# ---- the whole check --------------------------------------------------------------------

def all_catalogs() -> dict[str, Path]:
    return {c: LOCALES_DIR / f"{c}.jsonl" for c in TRANSLATED}


def all_glossaries() -> dict[str, dict]:
    out = {}
    for code in TRANSLATED:
        try:
            out[code] = load_glossary(code)
        except (OSError, ValueError):
            out[code] = {}
    return out


def run_checks() -> list[str]:
    problems = check_source_sync()
    rows = load_jsonl(SOURCE)
    problems += check_english_budgets(rows)
    problems += check_english_patterns(rows)
    problems += check_glossary_exemptions(rows)
    problems += check_catalogs(rows, all_catalogs(), locales.enabled_codes(),
                               glossaries=all_glossaries())
    problems += scan_files(scanned_files())
    problems += check_keyboard_labels(locales.enabled_codes() + ("qps",))
    problems += check_ratchet(literal_counts(), load_baseline())
    problems += check_golden(rows)
    return problems


# ---- todo, apply, review-pack ------------------------------------------------------------

def _context(row: dict) -> list[str]:
    path = ROOT / row["file"]
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    needle = json.dumps(row["k"])
    for i, line in enumerate(lines):
        if needle in line:
            return lines[max(0, i - 1):i + 2]
    return []


def todo(code: str, *, changed: bool = False) -> dict:
    rows = load_jsonl(SOURCE)
    catalog = load_catalog(LOCALES_DIR / f"{code}.jsonl")
    entries = []
    for row in rows:
        have = catalog.get(row["k"])
        stale = have is not None and have.get("src") != src_hash(row)
        if (changed and not stale) or (not changed and have is not None and not stale):
            continue
        item = {k: row[k] for k in ("k", "kind", "budget", "placeholders", "eg", "note")}
        for k in ("en", "one", "other"):
            if k in row:
                item[k] = row[k]
        if "en" not in row:
            item["categories"] = list(plurals.categories(code))
        if stale:
            item["previous"] = have.get("v")
        item["context"] = _context(row)
        entries.append(item)
    glossary = all_glossaries().get(code, {})
    style_path = STYLE_DIR / f"{code}.md"
    style = style_path.read_text(encoding="utf-8") if style_path.exists() else ""
    if RULES.exists():
        style = RULES.read_text(encoding="utf-8") + "\n\n" + style
    return {"lang": code, "glossary": glossary, "style": style, "entries": entries}


def apply(code: str, batch, *, catalog_path: Path | None = None) -> list[str]:
    """Validate *batch* (``[{"k", "v"}]`` or ``{"entries": [...]}``) with
    checks 3-10 and, only if all pass, write the catalog sorted with
    ``src`` stamps. Returns the problems (nothing written when any)."""
    if code not in TRANSLATED:
        return [f"{code}: not a translated language"]
    items = batch.get("entries") if isinstance(batch, dict) else batch
    if not isinstance(items, list):
        return ["the batch must be a list of {k, v} objects"]
    rows = {r["k"]: r for r in load_jsonl(SOURCE)}
    glossary = all_glossaries().get(code, {})
    path = catalog_path or LOCALES_DIR / f"{code}.jsonl"
    catalog = load_catalog(path)
    problems: list[str] = []
    for item in items:
        if not isinstance(item, dict) or "k" not in item or "v" not in item:
            problems.append(f"not a {{k, v}} entry: {item!r}")
            continue
        src = rows.get(item["k"])
        if src is None:
            problems.append(f"{item['k']}: no such key in the source")
            continue
        problems += check_translation(code, src, item["v"], glossary=glossary)
        for form in _forms(item["v"]):
            problems += scan_text(f"{code} {item['k']}", form)
        catalog[item["k"]] = {"k": item["k"], "v": item["v"], "src": src_hash(src)}
    if problems:
        return problems
    write_catalog(path, catalog)
    return []


def write_catalog(path: Path, catalog: dict) -> None:
    """Write *catalog* (key -> ``{"k", "v", "src"}``) as ``apply`` always
    has: one entry per line, sorted by key, with its ``src`` stamp."""
    lines = [json.dumps({"k": e["k"], "v": e["v"], "src": e["src"]}, ensure_ascii=False,
                        sort_keys=True) for _, e in sorted(catalog.items())]
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def prune(*, dry_run: bool = False, catalogs: dict[str, Path] | None = None,
          source: Path | None = None) -> list[tuple[str, str]]:
    """Drop, from every translated catalog, the entries whose key is no
    longer in the source (a key the code stopped using). Returns
    ``[(code, key)]`` removed (or, with *dry_run*, that would be); the
    other entries keep their values, stamps and order."""
    keys = {r["k"] for r in load_jsonl(source or SOURCE)}
    removed: list[tuple[str, str]] = []
    for code, path in sorted((catalogs if catalogs is not None else all_catalogs()).items()):
        catalog = load_catalog(path)
        gone = sorted(k for k in catalog if k not in keys)
        if not gone:
            continue
        removed += [(code, k) for k in gone]
        if not dry_run:
            write_catalog(path, {k: e for k, e in catalog.items() if k in keys})
    return removed


def _english(src: dict):
    return src.get("en") if "en" in src else {"one": src["one"], "other": src["other"]}


def review_pack(code: str | None = None, *, changed: bool = False,
                golden: bool = False) -> tuple[dict, dict]:
    """``(pack, answers)``: the translations without their English (for a
    back-translation pass), and the English they should mean. With
    *golden*, only the golden strings, in the list's order, each with its
    section, English and note: a native reviewer judges them against the
    English."""
    rows = {r["k"]: r for r in load_jsonl(SOURCE)}
    order = load_golden() if golden else None
    pack: dict[str, list] = {}
    answers: dict[str, dict] = {}
    for lang in ([code] if code else TRANSLATED):
        catalog = load_catalog(LOCALES_DIR / f"{lang}.jsonl")
        keyed = ([(section, key) for section, key in order] if golden
                 else [(None, key) for key in sorted(catalog)])
        items = []
        for section, key in keyed:
            entry, src = catalog.get(key), rows.get(key)
            if src is None or entry is None or (
                    changed and entry.get("src") == src_hash(src)):
                continue
            if golden:
                item = {"k": key, "section": section}
                if "en" in src:
                    item["en"] = src["en"]
                else:
                    item["one"], item["other"] = src["one"], src["other"]
                item["note"] = src.get("note", "")
                item["v"] = entry.get("v")
            else:
                item = {"k": key, "v": entry.get("v")}
            items.append(item)
            answers.setdefault(lang, {})[key] = _english(src)
        pack[lang] = items
    return pack, answers


# ---- the golden strings ---------------------------------------------------------------------

_HEADING = re.compile(r"^#\s*([a-z_]+)\s*$")


def load_golden(path: Path | None = None) -> list[tuple[str, str]]:
    """``[(section, key)]`` in file order. A line ``# <word>`` opens a
    section; other ``#`` lines are comments; a key before any section has
    the section ``""``."""
    path = GOLDEN if path is None else path
    out: list[tuple[str, str]] = []
    section = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            m = _HEADING.match(line)
            if m:
                section = m.group(1)
            continue
        out.append((section, line))
    return out


def check_golden(rows: list[dict], golden: list[tuple[str, str]] | None = None) -> list[str]:
    """The golden list: every key real and listed once, under a known
    section, no section empty, GOLDEN_MIN to GOLDEN_MAX keys."""
    if golden is None:
        try:
            golden = load_golden()
        except OSError as e:
            return [f"golden list: {e}"]
    keys = {r["k"] for r in rows}
    problems: list[str] = []
    seen: set[str] = set()
    for section, key in golden:
        if section not in GOLDEN_SECTIONS:
            problems.append(f"golden list: {key} is under an unknown section {section!r}")
        if key not in keys:
            problems.append(f"golden list: {key} is not a key")
        if key in seen:
            problems.append(f"golden list: {key} is listed twice")
        seen.add(key)
    used = {section for section, _key in golden}
    for section in GOLDEN_SECTIONS:
        if section not in used:
            problems.append(f"golden list: the section {section} is empty")
    if not GOLDEN_MIN <= len(golden) <= GOLDEN_MAX:
        problems.append(f"golden list: {len(golden)} keys, want {GOLDEN_MIN} to {GOLDEN_MAX}")
    return problems


# ---- translated docs ---------------------------------------------------------------------

DOCS_DIR = ROOT / "docs"
DOCS_I18N = DOCS_DIR / "i18n"
#: Each translated page and the English page it is translated from.
DOC_PAGES = {
    "README.md": "README.md",
    "install-with-an-agent.md": "docs/install-with-an-agent.md",
    "architecture.md": "docs/architecture.md",
    "hooks.md": "docs/hooks.md",
    "commands.md": "docs/commands.md",
    "groups.md": "docs/groups.md",
    "observers.md": "docs/observers.md",
    "troubleshooting.md": "docs/troubleshooting.md",
    "problem-reports.md": "docs/problem-reports.md",
    "security.md": "docs/security.md",
}
_DOC_SOURCE = re.compile(r"^<!-- i18n-source: (\S+) @ ([0-9a-f]{7,40}) -->$", re.M)
_DOC_LINK = re.compile(r"\]\(([^)\s]+)\)")
_DOC_ID = re.compile(r'<a id="([^"]+)"></a>')
_DOC_FENCE = re.compile(r"^(```|~~~)")
_DOC_BIDI = re.compile("[\u202a-\u202e\u2066-\u2069]")
#: Markdown reads only ASCII digits as a list number: "۱. " is a paragraph.
_DOC_NATIVE_LIST = re.compile(r"\s*[\u06f0-\u06f9\u0660-\u0669]+[.)]\s")


def _doc_prose(text: str) -> list[str]:
    """The lines of a markdown page outside its fenced code blocks."""
    lines, fenced = [], False
    for line in text.splitlines():
        if _DOC_FENCE.match(line.lstrip()):
            fenced = not fenced
            continue
        if not fenced:
            lines.append(line)
    return lines


def doc_slug(heading: str) -> str:
    """The anchor GitHub gives a heading."""
    text = heading.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def doc_anchors(text: str, ids: bool = True, min_level: int = 1) -> set[str]:
    """Every anchor a page has: its headings' (as GitHub numbers repeats;
    only from ``min_level`` down) and, with ``ids``, its explicit
    ``<a id>``s."""
    found: set[str] = set()
    seen: dict[str, int] = {}
    for line in _doc_prose(text):
        m = re.match(r"(#{1,6}) (.+)", line)
        if m:
            slug = doc_slug(m.group(2))
            n = seen.get(slug, 0)
            seen[slug] = n + 1
            if len(m.group(1)) >= min_level:
                found.add(slug if n == 0 else f"{slug}-{n}")
        if ids:
            found.update(_DOC_ID.findall(line))
    return found


def check_docs(root: Path = ROOT, codes=None) -> list[str]:
    """Problems in the translated docs: a missing or extra page, a page
    with no source comment (or one naming the wrong English page), a link
    to a file or anchor that does not exist, an English heading's anchor
    the page lacks, a long dash or a direction control outside code."""
    problems: list[str] = []
    base = root / "docs" / "i18n"
    if not base.is_dir():
        return problems
    known = set(codes if codes is not None else TRANSLATED)
    for folder in sorted(p for p in base.iterdir()):
        rel = folder.relative_to(root)
        if not folder.is_dir() or folder.name not in known:
            problems.append(f"{rel}: not a translated language's folder")
            continue
        names = {p.name for p in folder.iterdir()}
        for name in sorted(set(DOC_PAGES) - names):
            problems.append(f"{rel}/{name}: missing")
        for name in sorted(names - set(DOC_PAGES)):
            problems.append(f"{rel}/{name}: not a page the English docs have")
        for name in sorted(set(DOC_PAGES) & names):
            page = folder / name
            text = page.read_text(encoding="utf-8")
            where = page.relative_to(root)
            m = _DOC_SOURCE.search(text)
            if not m:
                problems.append(f"{where}: no <!-- i18n-source: ... @ <commit> --> line")
            elif m.group(1) != DOC_PAGES[name]:
                problems.append(f"{where}: translated from {m.group(1)}, want {DOC_PAGES[name]}")
            english = (root / DOC_PAGES[name])
            if english.is_file():
                want = doc_anchors(english.read_text(encoding="utf-8"), ids=False, min_level=2)
                have = doc_anchors(text)
                for anchor in sorted(want - have):
                    problems.append(f"{where}: no anchor {anchor!r} (the English heading's)")
            if folder.name in _RTL_CODES:
                problems += _check_rtl_page(where, text)
            for n, line in enumerate(_doc_prose(text), 1):
                bare = re.sub(r"`[^`]*`", "", line)
                if "\u2014" in bare:
                    problems.append(f"{where}: a long dash (U+2014) outside code: {line.strip()[:60]}")
                if _DOC_BIDI.search(line):
                    problems.append(f"{where}: a direction control character: {line.strip()[:60]}")
                if _DOC_NATIVE_LIST.match(line):
                    problems.append(f"{where}: a numbered list item needs Latin digits (1.): "
                                    f"{line.strip()[:60]}")
            for target in _DOC_LINK.findall("\n".join(_doc_prose(text))):
                if re.match(r"[a-z]+:", target):
                    continue
                file, _, frag = target.partition("#")
                dest = (page.parent / file).resolve() if file else page.resolve()
                if not dest.exists():
                    problems.append(f"{where}: link to a missing file: {target}")
                elif frag and dest.suffix == ".md" and frag not in doc_anchors(
                        dest.read_text(encoding="utf-8")):
                    problems.append(f"{where}: link to a missing anchor: {target}")
    return problems


def _check_rtl_page(where, text: str) -> list[str]:
    """A right-to-left page reads right to left, its code left to right:
    the page in ``<div dir="rtl">``, each fenced block in ``<div dir="ltr">``."""
    problems: list[str] = []
    lines = text.splitlines()
    if not lines or lines[0] != '<div dir="rtl">':
        problems.append(f'{where}: does not start with <div dir="rtl">')
    if not text.rstrip().endswith("</div>"):
        problems.append(f"{where}: does not end with </div>")
    fenced = False
    for i, line in enumerate(lines):
        if not _DOC_FENCE.match(line.lstrip()):
            continue
        if not fenced:
            before = [x for x in lines[:i] if x.strip()][-1:]
            if before != ['<div dir="ltr">']:
                problems.append(f'{where}: a code block outside <div dir="ltr">: line {i + 1}')
        else:
            after = [x for x in lines[i + 1:] if x.strip()][:1]
            if after != ["</div>"]:
                problems.append(f"{where}: a code block with no </div> after it: line {i + 1}")
        fenced = not fenced
    return problems


def stale_docs(root: Path = ROOT) -> list[str]:
    """Translated pages whose English page changed after the commit the
    page names (needs git; a commit git does not know counts as changed)."""
    import subprocess
    out: list[str] = []
    base = root / "docs" / "i18n"
    if not base.is_dir():
        return out
    for page in sorted(base.glob("*/*.md")):
        m = _DOC_SOURCE.search(page.read_text(encoding="utf-8"))
        if not m:
            continue
        source, commit = m.groups()
        res = subprocess.run(["git", "-C", str(root), "diff", "--quiet", commit, "HEAD", "--", source],
                             capture_output=True)
        if res.returncode != 0:
            out.append(f"{page.relative_to(root)}: {source} changed after {commit}")
    return out


# ---- command line --------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tools/i18n.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("extract")
    p.add_argument("--check", action="store_true")
    p = sub.add_parser("check")
    p.add_argument("--write-baseline", action="store_true")
    p = sub.add_parser("todo")
    p.add_argument("--lang", required=True)
    p.add_argument("--changed", action="store_true")
    p = sub.add_parser("apply")
    p.add_argument("--lang", required=True)
    p.add_argument("file")
    p = sub.add_parser("prune")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("review-pack")
    p.add_argument("--lang")
    p.add_argument("--changed", action="store_true")
    p.add_argument("--golden", action="store_true")
    p.add_argument("--answers")
    p = sub.add_parser("pseudo")
    p.add_argument("--rtl", action="store_true")
    p.add_argument("text")
    sub.add_parser("docs")
    args = parser.parse_args(argv)

    if args.cmd == "extract":
        if args.check:
            problems = check_source_sync()
            for line in problems:
                print(line, file=sys.stderr)
            return 1 if problems else 0
        try:
            rows = extract_entries()
        except ExtractError as e:
            print(f"extract: {e}", file=sys.stderr)
            return 1
        SOURCE.parent.mkdir(parents=True, exist_ok=True)
        SOURCE.write_text(dump_jsonl(rows), encoding="utf-8")
        print(f"{len(rows)} keys -> {SOURCE.relative_to(ROOT)}")
        return 0
    if args.cmd == "check":
        if args.write_baseline:
            write_baseline(literal_counts())
            print(f"wrote {BASELINE.relative_to(ROOT)}")
        problems = run_checks()
        for line in problems:
            print(line, file=sys.stderr)
        return 1 if problems else 0
    if args.cmd == "todo":
        if args.lang not in TRANSLATED:
            print(f"{args.lang}: not a translated language", file=sys.stderr)
            return 2
        print(json.dumps(todo(args.lang, changed=args.changed), ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "apply":
        try:
            batch = json.loads(Path(args.file).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"{args.file}: {e}", file=sys.stderr)
            return 2
        problems = apply(args.lang, batch)
        for line in problems:
            print(line, file=sys.stderr)
        return 1 if problems else 0
    if args.cmd == "prune":
        stale = check_source_sync()
        if stale:
            for line in stale:
                print(line, file=sys.stderr)
            print("prune: run extract first", file=sys.stderr)
            return 1
        for code, key in prune(dry_run=args.dry_run):
            print(f"{code} {key}")
        return 0
    if args.cmd == "review-pack":
        if args.lang and args.lang not in TRANSLATED:
            print(f"{args.lang}: not a translated language", file=sys.stderr)
            return 2
        pack, answers = review_pack(args.lang, changed=args.changed, golden=args.golden)
        print(json.dumps(pack, ensure_ascii=False, indent=1))
        if args.answers:
            Path(args.answers).write_text(
                json.dumps(answers, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return 0
    if args.cmd == "docs":
        problems = check_docs()
        for line in problems:
            print(line, file=sys.stderr)
        for line in stale_docs():
            print(line)
        return 1 if problems else 0
    if args.cmd == "pseudo":
        text = core.pseudo(args.text)
        if args.rtl:
            # As the RTL pseudo locale shows it: a mark at every line start.
            text = "\n".join("\u200f" + line for line in text.split("\n"))
        print(text)
        return 0
    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
