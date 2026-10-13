"""The served Mini App page leaves out the comments and indentation of its
script and styles (roadmap 8.117; operator 2026-10-13: "dont remove the
comment from the source").

What the phone gets must mean exactly what the source says. For the
script, Node's own parser (the acorn inside Node, reached with
``--expose-internals``) reads both and must see the same tokens with the
same line breaks between them (automatic semicolons read those). For the
styles, an independent tokenizer here does the same. Everything else on
the page (the markup, the inlined catalogs, the SDK tag) is the page as
written, byte for byte.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from aipager.i18n import locales
from aipager.miniapp import static
from aipager.miniapp.static import SERVED_APP_JS, SERVED_CSS, index_html
from aipager.miniapp.static._app import APP_JS
from aipager.miniapp.static._strip import strip_css, strip_js
from aipager.miniapp.static._styles import CSS
from tests.i18n_tool import isolated_env

# Prints, for each file named, its tokens (type, exact text, whether a
# line break comes before it) and how many comments it holds. Parsing
# also fails on any syntax error.
TOKENS_JS = r"""
const fs = require('fs');
let acorn;
try { acorn = require('internal/deps/acorn/acorn/dist/acorn'); }
catch (e) { process.stdout.write('"no acorn"'); process.exit(0); }
const out = {};
for (const name of process.argv.slice(2)) {
  const src = fs.readFileSync(name, 'utf8');
  const tokens = [];
  let prev = 0;
  for (const t of acorn.tokenizer(src, {ecmaVersion: 'latest'})) {
    const gap = src.slice(prev, t.start);
    const lineBreak = [...gap].some(function (ch) {
      const c = ch.charCodeAt(0);
      return c === 10 || c === 13 || c === 0x2028 || c === 0x2029;
    });
    tokens.push([t.type.label, src.slice(t.start, t.end), lineBreak]);
    prev = t.end;
  }
  const comments = [];
  acorn.parse(src, {ecmaVersion: 'latest', onComment: comments});
  out[name] = {tokens: tokens, comments: comments.length};
}
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def node_bin():
    exe = shutil.which("node") or shutil.which("nodejs")
    if not exe:
        pytest.skip("node not available")
    return exe


def _read_with_node(node_bin, tmp_path, scripts: dict[str, str]) -> dict:
    """``{name: {"tokens": [...], "comments": n}}`` for each script."""
    reader = tmp_path / "tokens.js"
    reader.write_text(TOKENS_JS, encoding="utf-8")
    names = []
    for name, text in scripts.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
        names.append(str(tmp_path / name))
    proc = subprocess.run([node_bin, "--expose-internals", str(reader), *names],
                          capture_output=True, text=True, timeout=120,
                          env=isolated_env(tmp_path))
    assert proc.returncode == 0, proc.stderr[-3000:]
    out = json.loads(proc.stdout)
    if out == "no acorn":
        # Not a skip: this check is the guard on what the phone gets, and a
        # quiet skip in CI would leave the page unguarded.
        pytest.fail("Node is here but its built-in parser is not at "
                    "internal/deps/acorn/acorn/dist/acorn: find where this Node keeps it")
    return {k.rsplit("/", 1)[-1]: v for k, v in out.items()}


def _same_tokens(a: list, b: list) -> None:
    """Equal token streams; the first token's line break is moot (nothing
    comes before it to end)."""
    if a:
        a = [[a[0][0], a[0][1], False], *a[1:]]
    if b:
        b = [[b[0][0], b[0][1], False], *b[1:]]
    assert len(a) == len(b), (len(a), len(b))
    for i, (x, y) in enumerate(zip(a, b)):
        assert x == y, f"token {i}: {x} became {y}"


# ---- the page's own script and styles ----------------------------------------------

def test_the_served_script_reads_the_same_as_the_source(node_bin, tmp_path):
    read = _read_with_node(node_bin, tmp_path, {"source.js": APP_JS, "served.js": SERVED_APP_JS})
    _same_tokens(read["source.js"]["tokens"], read["served.js"]["tokens"])
    assert read["source.js"]["comments"] > 400      # the source keeps its comments
    assert read["served.js"]["comments"] == 0       # the phone gets none of them
    kinds = {t[0] for t in read["source.js"]["tokens"]}
    assert {"string", "regexp"} <= kinds            # the literals were really there


def _css_tokens(css: str) -> list[str]:
    """The style sheet as a browser reads it: strings and unquoted url()
    values whole, a comment as a break that is a blank only when blanks
    are beside it, every run of blanks as one."""
    raw = re.findall(r'"(?:[^"\\\n]|\\[\s\S])*"|\'(?:[^\'\\\n]|\\[\s\S])*\''
                     r"|url\((?:[^)\\'\"\n]|\\[^\n])*\)|/\*[\s\S]*?\*/|\s+|.", css, re.I)
    out: list[str] = []
    gap: list[str] = []
    for tok in raw:
        if tok.startswith("/*") or tok.isspace():
            gap.append(tok)
            continue
        if gap and out:  # blanks before the first rule and after the last don't count
            out.append(" " if any(g.isspace() for g in gap) else "<comment>")
        gap = []
        out.append(tok)
    return out


def test_the_served_styles_read_the_same_as_the_source():
    assert _css_tokens(SERVED_CSS) == _css_tokens(CSS)
    assert "/*" in CSS and "/*" not in SERVED_CSS


def test_the_served_copy_has_no_indentation_or_blank_lines():
    for name, text in (("script", SERVED_APP_JS), ("styles", SERVED_CSS)):
        lines = text.split("\n")
        assert all(line == line.strip(" \t") for line in lines), name
        assert "" not in lines, name
    assert len(SERVED_APP_JS) < 0.8 * len(APP_JS)
    assert len(SERVED_CSS) < 0.9 * len(CSS)


def test_the_source_keeps_its_comments():
    """The operator's rule: the comments stay in the source; only the copy
    sent to the phone leaves them out."""
    assert sum(line.lstrip().startswith("//") for line in APP_JS.split("\n")) > 400
    assert CSS.count("/*") > 40
    page = static.SOURCE_INDEX_HTML
    assert APP_JS in page and CSS in page
    assert page == index_html(sdk_from_self=True, stripped=False)


@pytest.mark.parametrize("sdk_from_self", [True, False], ids=["self-served", "fallback"])
@pytest.mark.parametrize("code", ["en", *[c for c in locales.enabled_codes() if c != "en"]])
def test_only_the_script_and_styles_change(code, sdk_from_self):
    """The markup, the inlined catalogs and the SDK tag are the page as
    written, byte for byte: only the two blocks are the served copies."""
    written = index_html(sdk_from_self=sdk_from_self, lang=code, stripped=False)
    assert written.count(APP_JS) == 1 and written.count(CSS) == 1
    served = written.replace(CSS, SERVED_CSS).replace(APP_JS, SERVED_APP_JS)
    assert index_html(sdk_from_self=sdk_from_self, lang=code) == served


def test_the_constant_and_the_route_serve_the_stripped_page():
    assert static.INDEX_HTML == index_html(sdk_from_self=True)
    assert SERVED_APP_JS in static.INDEX_HTML and APP_JS not in static.INDEX_HTML


@pytest.mark.parametrize("start, end", [
    ("// clock: port of aipager/clockfmt.py", "// end clock"),
    ("// plurals: port of aipager/i18n/plurals.py", "// end plurals"),
], ids=["clock", "plurals"])
def test_a_marked_port_is_served_whole(start, end):
    """The clock and plural ports are found by comment markers, which the
    served page leaves out; their parity tests read the page as written,
    and this is what ties that code to the served page."""
    page = static.SOURCE_INDEX_HTML
    s = page.index(start)
    port = strip_js(page[s:page.index(end, s)])
    assert port.strip() and port in static.INDEX_HTML


# ---- the scanner on hard cases ------------------------------------------------------

JS_CASES = {
    "url-in-a-string": ('var u = "http://x/y"; // note\n', 'var u = "http://x/y";'),
    "comment-in-a-string": ("  var s = '/* not a comment */';\n", "var s = '/* not a comment */';"),
    "regex-with-slashes": ("var r = /\\/\\//g; // two slashes\n", "var r = /\\/\\//g;"),
    "regex-with-quotes": ("var r = /[\"']/; var s = \"a\"; // q\n", "var r = /[\"']/; var s = \"a\";"),
    "regex-class-slash": ("var r = /[/]x/; // c\n", "var r = /[/]x/;"),
    "division": ("var a = b / c; // c\n    var d = (a) / 2 / e; // d\n",
                 "var a = b / c;\nvar d = (a) / 2 / e;"),
    "regex-after-return": ("function f(s) {\n  return /x\\/y/.test(s); // c\n}\n",
                           "function f(s) {\nreturn /x\\/y/.test(s);\n}"),
    "regex-with-a-quote-after-return": ("function f(s) { return /'/.test(s) + 'http://x'; }\n",
                                        "function f(s) { return /'/.test(s) + 'http://x'; }"),
    "escaped-quote": ("var s = 'it\\'s // fine'; // gone\n", "var s = 'it\\'s // fine';"),
    "template": ("var t = `a\n  // kept\n  ${ `in ${x}` } b`; // gone\n  var y = 1;\n",
                 "var t = `a\n  // kept\n  ${ `in ${x}` } b`;\nvar y = 1;"),
    "line-continuation": ('var s = "a\\\n   b"; // c\n  x();\n', 'var s = "a\\\n   b";\nx();'),
    "whole-line-block": ("a();\n  /* one\n     two */\n  b();\n", "a();\nb();"),
    "inline-block-is-a-blank": ("a(/* x */1);\n", "a( 1);"),
    "inline-block-beside-a-blank": ("try { f(); } catch (e) { /* old */ }\n", "try { f(); } catch (e) {  }"),
    "multi-line-block-beside-code-kept": ("a(/* x\n */1);\n", "a(/* x\n*/1);"),
    "inline-block-between-words": ("var a = typeof/* x */b;\n", "var a = typeof b;"),
    "blank-lines": ("a();\n\n\n    b();\n", "a();\nb();"),
    "comment-before-regex": ("x = y;\n// c\n/re/.test(s);\n", "x = y;\n/re/.test(s);"),
    "division-after-a-string": ("var a = 'x' / 2; // c\n", "var a = 'x' / 2;"),
    "division-after-a-template": ("var a = `x` / 2; // c\n", "var a = `x` / 2;"),
    "division-after-increment": ("i++ / 2; // c\n", "i++ / 2;"),
    "division-after-a-keyword-property": ("var a = x.in / 2; // c\n", "var a = x.in / 2;"),
    "division-after-a-long-name-ending-in-a-keyword": ("var a = aVeryLongNamereturn / 2; // c\n",
                                                        "var a = aVeryLongNamereturn / 2;"),
    "object-in-template": ("var t = `${ {a: 1}.a } // kept`; // gone\n", "var t = `${ {a: 1}.a } // kept`;"),
}


@pytest.mark.parametrize("name", sorted(JS_CASES))
def test_a_hard_script_case(name):
    src, want = JS_CASES[name]
    assert strip_js(src) == want


def test_every_hard_script_case_reads_the_same_in_node(node_bin, tmp_path):
    scripts = {}
    for i, name in enumerate(sorted(JS_CASES)):
        src, want = JS_CASES[name]
        scripts[f"{i}-src.js"] = src
        scripts[f"{i}-out.js"] = strip_js(src)
    read = _read_with_node(node_bin, tmp_path, scripts)
    for i, name in enumerate(sorted(JS_CASES)):
        _same_tokens(read[f"{i}-src.js"]["tokens"], read[f"{i}-out.js"]["tokens"])


@pytest.mark.parametrize("src", [
    "var s = 'open\n';\n",          # a string running into a line end
    "var t = `open\n",              # a template literal left open
    "a();\n}\n",                    # a brace that closes nothing
    "a(); /* open\n",               # a block comment left open
    "var r = (1) + /x;\n",          # a regular expression with no end
    "a(); // c\u2028b();\nc();\n",  # a line break other than \\n ends the comment
    "a(); // c\rb();\n",            # a lone carriage return does too
], ids=["open-string", "open-template", "stray-brace", "open-comment", "open-regex",
        "line-separator", "carriage-return"])
def test_a_script_it_cannot_place_is_served_as_written(src):
    assert strip_js(src) == src


CSS_CASES = {
    "comment-in-a-string": ('a { content: "/*"; } /* real */\n', 'a { content: "/*"; }'),
    "whole-and-trailing": ("  .x { color: red; } /* gone */\n  /* gone\n     too */\n  .y { }\n",
                           ".x { color: red; }\n.y { }"),
    "inline-kept": ("a/* kept */b { }\n", "a/* kept */b { }"),
    "url-untouched": ("a { background: url(x/a/*b*/c); }\n", "a { background: url(x/a/*b*/c); }"),
    "url-with-a-comment-start": ("a { b: url(x/*y); }\n/* real */\n.c { }\n", "a { b: url(x/*y); }\n.c { }"),
    "continuation": ("a { b: 'x\\\n  y'; }\n", "a { b: 'x\\\n  y'; }"),
}


@pytest.mark.parametrize("name", sorted(CSS_CASES))
def test_a_hard_style_case(name):
    src, want = CSS_CASES[name]
    out = strip_css(src)
    assert out == want
    assert _css_tokens(out) == _css_tokens(src)


@pytest.mark.parametrize("src", [
    'a { content: "open\n"; }\n',      # a string running into a line end
    ".a\\:b { } /* c */\n",            # an escape in a name
], ids=["open-string", "escape-in-a-name"])
def test_styles_it_cannot_place_are_served_as_written(src):
    assert strip_css(src) == src
