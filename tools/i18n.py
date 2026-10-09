#!/usr/bin/env python3
"""aipager's translation toolchain (dev only; never shipped in the wheel).

    python tools/i18n.py extract [--check]
    python tools/i18n.py check [--write-baseline]
    python tools/i18n.py todo --lang CODE [--changed]
    python tools/i18n.py apply --lang CODE FILE
    python tools/i18n.py review-pack [--lang CODE] [--changed] [--answers FILE]
    python tools/i18n.py pseudo [--rtl] TEXT

``extract`` reads every ``Msg(...)``, ``Plural(...)`` and inline
``tr.html|text|md("key", "English", ...)`` in ``aipager/`` and writes
``tools/i18n/source.jsonl``: one key per line with its English, kind,
length budget, placeholders, examples, file, enclosing function and note.
It records no line numbers, so an unrelated edit never churns the file.

``check`` runs the catalog checks of the multilingual design (section 9):
1 every key in every enabled language, 2 no stale translation, 3 the same
placeholders, 4 the same Telegram-allowed tags, 5 every plural form, 6 the
glossary, 7 length budgets, 8 no long dash or direction control, 9 native
digits for fa/ar, 10 reply-keyboard labels, 16 catalog format, plus the
literal ratchet of check 11. The test suite imports these same functions,
so CI and this command can never disagree.

Agents never edit a catalog by hand: ``todo`` hands them a batch with
everything a translator needs, ``apply`` validates it and writes the
catalog.
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
LOCALES_DIR = core.LOCALES_DIR
BASELINE = ROOT / "tests" / "i18n_literal_baseline.json"

KEY_RE = core.KEY_RE
TRANSLATED = tuple(c for c in locales.LOCALES if c != "en")

# ---- keys: kind and length budget ------------------------------------------

#: (pattern, kind, unit, limit, fill). ``unit`` is "cells" (display width,
#: CJK counts 2) or "chars"; ``fill`` names what fills {language}.
_BUDGETS: tuple[tuple[str, str, str, int, str], ...] = (
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
    (r"app\.[a-z0-9_]+\.title", "label", "cells", 40, ""),
    (r"app\.[a-z0-9_]+\.hint", "hint", "chars", 80, ""),
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
        problems += check_digits(code, key, form)
        problems += check_budget(key, form, src=src, code=code)
        if glossary is not None:
            problems += check_glossary(code, src, form, glossary)
    return problems


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


def load_glossary(code: str, base: Path = GLOSSARY_DIR) -> dict:
    return json.loads((base / f"{code}.json").read_text(encoding="utf-8"))


def check_glossary(code: str, src: dict, form: str, glossary: dict) -> list[str]:
    """Names and commands spelled exactly; a glossary term the English
    uses appears as one of its forms."""
    problems = []
    english = " ".join(english_forms(src))
    for word in core.NEVER_TRANSLATE:
        if re.search(rf"\b{re.escape(word)}\b", english) and word not in form:
            problems.append(f"{code} {src['k']}: {word!r} must stay spelled exactly")
    for cmd in _SLASH.findall(english):
        if cmd not in form:
            problems.append(f"{code} {src['k']}: the command {cmd} must stay as it is")
    for term, forms in (glossary.get("terms") or {}).items():
        if not forms:
            continue
        options = [forms] if isinstance(forms, str) else list(forms)
        if re.search(rf"\b{re.escape(term)}s?\b", english, re.I):
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
    size = measure(filled)
    if size > budget["limit"]:
        return [f"{code} {key}: {size} {budget['unit']} > {budget['limit']}: {filled!r}"]
    return []


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
_CODE_SPAN = re.compile(r"<(code|pre)\b[^>]*>.*?</\1>|\{[A-Za-z_]\w*\}|<[^>]+>|&#?\w+;",
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
            is_log = (isinstance(node.func, ast.Attribute) and name in _LOG_METHODS)
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
    try:
        from aipager.bot import kbwords
    except ImportError:
        return []
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
    problems += check_catalogs(rows, all_catalogs(), locales.enabled_codes(),
                               glossaries=all_glossaries())
    problems += scan_files(scanned_files())
    problems += check_keyboard_labels(locales.enabled_codes() + ("qps",))
    problems += check_ratchet(literal_counts(), load_baseline())
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
    lines = [json.dumps({"k": e["k"], "v": e["v"], "src": e["src"]}, ensure_ascii=False,
                        sort_keys=True) for _, e in sorted(catalog.items())]
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return []


def review_pack(code: str | None = None, *, changed: bool = False) -> tuple[dict, dict]:
    """``(pack, answers)``: the translations without their English (for a
    back-translation pass), and the English they should mean."""
    rows = {r["k"]: r for r in load_jsonl(SOURCE)}
    pack: dict[str, list] = {}
    answers: dict[str, dict] = {}
    for lang in ([code] if code else TRANSLATED):
        catalog = load_catalog(LOCALES_DIR / f"{lang}.jsonl")
        items = []
        for key, entry in sorted(catalog.items()):
            src = rows.get(key)
            if src is None or (changed and entry.get("src") == src_hash(src)):
                continue
            items.append({"k": key, "v": entry.get("v")})
            answers.setdefault(lang, {})[key] = (
                src.get("en") if "en" in src else {"one": src["one"], "other": src["other"]})
        pack[lang] = items
    return pack, answers


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
    p = sub.add_parser("review-pack")
    p.add_argument("--lang")
    p.add_argument("--changed", action="store_true")
    p.add_argument("--answers")
    p = sub.add_parser("pseudo")
    p.add_argument("--rtl", action="store_true")
    p.add_argument("text")
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
    if args.cmd == "review-pack":
        if args.lang and args.lang not in TRANSLATED:
            print(f"{args.lang}: not a translated language", file=sys.stderr)
            return 2
        pack, answers = review_pack(args.lang, changed=args.changed)
        print(json.dumps(pack, ensure_ascii=False, indent=1))
        if args.answers:
            Path(args.answers).write_text(
                json.dumps(answers, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return 0
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
