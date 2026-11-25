"""The Mini App stylesheet reads right to left in Persian and is
unchanged left to right.

The page lays itself out with logical properties (``margin-inline-start``,
``padding-inline``, ``inset-inline-end``, ``text-align: start``...), so one
declaration serves both directions. ``tests/golden/miniapp_css_ltr.json``
is the stylesheet as it was before (recorded with
``AIPAGER_RECORD_CSS=1``), every rule resolved to physical longhands: the
current stylesheet, resolved for a left-to-right page, must equal it.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from aipager.miniapp.static._styles import CSS

GOLDEN = Path(__file__).parent / "golden" / "miniapp_css_ltr.json"


def _strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def rules(css: str = CSS) -> list[tuple[str, str, list[tuple[str, str]]]]:
    """``(context, selector, [(property, value)...])`` for every rule, in
    order; ``context`` names the enclosing at-rules (``@media ...``)."""
    out: list = []

    def walk(text: str, ctx: tuple) -> None:
        pos = 0
        while True:
            brace = text.find("{", pos)
            if brace < 0:
                return
            prelude = " ".join(text[pos:brace].split())
            depth, i = 1, brace + 1
            while depth:
                depth += {"{": 1, "}": -1}.get(text[i], 0)
                i += 1
            inner = text[brace + 1:i - 1]
            if "{" in inner:
                walk(inner, ctx + (prelude,))
            else:
                decls = []
                for part in inner.split(";"):
                    if ":" in part:
                        prop, _, value = part.partition(":")
                        decls.append((prop.strip(), " ".join(value.split())))
                out.append((" / ".join(ctx), prelude, decls))
            pos = i
    walk(_strip_comments(css), ())
    return out


def _values(value: str) -> list[str]:
    """Space-separated values, parentheses kept whole."""
    out, depth, cur = [], 0, ""
    for ch in value:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == " " and depth == 0:
            if cur:
                out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def _box(prop: str, value: str) -> dict:
    v = _values(value)
    top, right, bottom, left = {1: (0, 0, 0, 0), 2: (0, 1, 0, 1), 3: (0, 1, 2, 1),
                                4: (0, 1, 2, 3)}[len(v)]
    return {f"{prop}-top": v[top], f"{prop}-right": v[right],
            f"{prop}-bottom": v[bottom], f"{prop}-left": v[left]}


_LTR = {
    "margin-inline-start": "margin-left", "margin-inline-end": "margin-right",
    "padding-inline-start": "padding-left", "padding-inline-end": "padding-right",
    "inset-inline-start": "left", "inset-inline-end": "right",
    "border-inline-start": "border-left", "border-inline-end": "border-right",
    "margin-block-start": "margin-top", "margin-block-end": "margin-bottom",
    "padding-block-start": "padding-top", "padding-block-end": "padding-bottom",
}


def resolve_ltr(decls) -> dict[str, str]:
    """The physical longhands a left-to-right page gets from *decls*."""
    out: dict[str, str] = {}
    for prop, value in decls:
        if prop in ("margin", "padding"):
            out.update(_box(prop, value))
        elif prop in ("margin-inline", "padding-inline", "margin-block", "padding-block"):
            base, axis = prop.split("-")
            v = _values(value)
            a, b = v[0], v[1] if len(v) > 1 else v[0]
            sides = ("left", "right") if axis == "inline" else ("top", "bottom")
            out[f"{base}-{sides[0]}"], out[f"{base}-{sides[1]}"] = a, b
        elif prop in _LTR:
            out[_LTR[prop]] = value
        elif prop == "text-align" and value in ("start", "end"):
            out[prop] = "left" if value == "start" else "right"
        else:
            out[prop] = value
    return out


def _rtl_only(selector: str) -> bool:
    return '[dir="rtl"]' in selector


def ltr_layout(css: str = CSS) -> list:
    """Every rule a left-to-right page applies, resolved to physical
    longhands."""
    return [[ctx, sel, sorted(resolve_ltr(decls).items())]
            for ctx, sel, decls in rules(css) if not _rtl_only(sel)]


def _as_json(layout) -> list:
    return [[ctx, sel, [list(p) for p in props]] for ctx, sel, props in layout]


#: The one rule that changed on purpose: the ring's "%" was CSS content
#: (``.lamp-num::after``); it is now a text span (``.lamp-pct``) the page
#: fills with its language's sign, drawn with the same style.
_PERCENT_BEFORE = [".lamp-num::after"]
_PERCENT_AFTER = ".lamp-pct"


#: The rules /settings → 🎨 Appearance added (2026-10-15), right after
#: the choice label's: the colour dot before a choice's label, one colour
#: per Telegram button style. (Its subheading went when Appearance became
#: a section of its own, below.)
_APPEARANCE_AFTER = ".choice.is-active .choice-main"
_APPEARANCE_ADDED = [
    ["", ".choice-main.swatch::before", [
        ["border-radius", "50%"], ["content", "\"\""], ["display", "inline-block"],
        ["height", "12px"], ["margin-right", "6px"], ["vertical-align", "-1px"],
        ["width", "12px"]]],
    ["", ".swatch-blue::before", [["background", "#3390ec"]]],
    ["", ".swatch-green::before", [["background", "#31b545"]]],
    ["", ".swatch-red::before", [["background", "#e53935"]]],
    ["", ".swatch-green-red::before", [
        ["background", "linear-gradient(90deg, #31b545 50%, #e53935 50%)"]]],
]


#: Appearance as a section of its own on the Settings tab (2026-10-15):
#: its groups sit under its heading the way the chat's preferences do,
#: right after their rule.
_APPEARANCE_SECTION_ADDED = {
    ("", "#settings-groups"): [
        ["", "#settings-appearance-groups", [["margin-top", "var(--s2)"]]],
    ],
}


#: The rules the quiet Updates card (2026-10-15) added: the card first on
#: the Settings tab, its head row (name, version, the small Check for
#: updates at the row's end, turning while it checks), no empty button row;
#: after ``.updates``, and its reduced-motion stop after the lantern's.
_UPDATES_CARD_ADDED = {
    ("", ".updates"): [
        ["", "#updates-block", [
            ["margin-bottom", "var(--s4)"], ["margin-top", "0"],
            ["padding-bottom", "var(--s3)"], ["padding-top", "var(--s3)"]]],
        ["", ".updates-head", [
            ["align-items", "center"], ["display", "flex"], ["gap", "var(--s3)"],
            ["min-height", "40px"]]],
        ["", ".updates-name", [
            ["align-items", "baseline"], ["display", "flex"], ["flex", "1 1 auto"],
            ["gap", "var(--s2)"], ["min-width", "0"]]],
        ["", ".updates-version", [
            ["color", "var(--ink-3)"], ["font-size", "13px"], ["line-height", "18px"]]],
        ["", ".updates-check", [
            ["align-items", "center"], ["border-radius", "var(--r-pill)"],
            ["color", "var(--accent)"], ["display", "inline-flex"], ["flex", "0 0 auto"],
            ["font-size", "var(--fs-sm)"], ["font-weight", "600"], ["gap", "6px"],
            ["margin-right", "calc(-1 * var(--s2))"], ["min-height", "40px"],
            ["padding-bottom", "0"], ["padding-left", "var(--s2)"],
            ["padding-right", "var(--s2)"], ["padding-top", "0"]]],
        ["", ".updates-check .ic", [["height", "18px"], ["width", "18px"]]],
        ["", ".updates-check:disabled", [["color", "var(--ink-3)"]]],
        ["", ".updates-check.is-checking .ic", [["animation", "spin 1.1s linear infinite"]]],
        ["", ".updates-actions:empty", [["display", "none"]]],
    ],
    ("@media (prefers-reduced-motion: reduce)", ".lamp-spin::before"): [
        ["@media (prefers-reduced-motion: reduce)", ".updates-check.is-checking .ic",
         [["animation", "none"]]],
    ],
}


#: A session waiting for its own background agents or shells (roadmap
#: 8.121, status ``tasks``) looks like a working one: each busy rule is
#: followed by its ``tasks`` twin (its state word wraps, _NARROW_ADDED).
_TASKS_ADDED = {
    ("", ".tile-busy"): [
        ["", ".tile-tasks", [
            ["background", "radial-gradient(130% 95% at 0% 0%, var(--lamp-work-soft) 0%, "
                           "transparent 62%), var(--surface)"],
            ["border-color", "color-mix(in srgb, var(--lamp-work) 32%, var(--line))"]]],
    ],
    ("", ".tile-busy .tile-state"): [
        ["", ".tile-tasks .tile-state", [
            ["background", "var(--lamp-work-soft)"], ["color", "var(--ink)"]]],
    ],
    ("", ".status-busy::before"): [
        ["", ".status-tasks", [["background", "var(--lamp-work-soft)"], ["color", "var(--ink)"]]],
        ["", ".status-tasks::before", [["background", "var(--lamp-work)"]]],
    ],
    ("", ".d-state-row .status-busy::before"): [
        ["", ".d-state-row .status-tasks::before", [
            ["background", "var(--lamp-work)"],
            ["box-shadow", "0 0 0 3px var(--lamp-work-soft)"]]],
    ],
}


#: The Usage page (roadmap 8.131): the window cards and bars (on a meter
#: track, amber near the limit), the 7-day chart and the token tables (one
#: column set, rows are subgrids); after ``.updates-again``.
_USAGE_ADDED = {
    ("", ".updates-again"): [
        ["", ".usage-card", [["background", "var(--surface)"], ["border", "1px solid var(--line)"], ["border-radius", "var(--r-card)"], ["margin-bottom", "var(--s3)"], ["margin-left", "0"], ["margin-right", "0"], ["margin-top", "0"], ["padding-bottom", "var(--s3)"], ["padding-left", "var(--s4)"], ["padding-right", "var(--s4)"], ["padding-top", "var(--s3)"]]],
        ["", ".usage-name", [["font-weight", "600"], ["margin-bottom", "var(--s2)"], ["margin-left", "0"], ["margin-right", "0"], ["margin-top", "0"]]],
        ["", ".usage-line", [["font-size", "var(--fs-sm)"], ["line-height", "var(--lh-sm)"], ["margin-bottom", "var(--s1)"], ["margin-left", "0"], ["margin-right", "0"], ["margin-top", "var(--s2)"]]],
        ["", ".usage-sub", [["color", "var(--ink-3)"], ["font-size", "13px"], ["line-height", "18px"], ["margin-bottom", "0"], ["margin-left", "0"], ["margin-right", "0"], ["margin-top", "var(--s1)"]]],
        ["", ".ubar", [["background", "var(--meter-track)"], ["block-size", "8px"], ["border-radius", "var(--r-pill)"], ["overflow", "hidden"]]],
        ["", ".ubar-fill", [["background", "var(--accent)"], ["block-size", "100%"], ["border-radius", "var(--r-pill)"], ["inline-size", "calc(var(--pct, 0) * 1%)"]]],
        ["", ".ubar-fill.is-near", [["background", "var(--lamp-need)"]]],
        ["", ".utable", [["background", "var(--line)"], ["border", "1px solid var(--line)"], ["border-radius", "var(--r-card)"], ["display", "grid"], ["grid-template-columns", "minmax(min(7em, 22%), max-content) repeat(4, auto)"], ["overflow", "hidden"], ["row-gap", "1px"]]],
        ["", ".utable-row", [["background", "var(--surface)"], ["column-gap", "6px"], ["display", "grid"], ["font-size", "13px"], ["grid-column", "1 / -1"], ["grid-template-columns", "subgrid"], ["line-height", "18px"], ["padding-bottom", "var(--s2)"], ["padding-left", "var(--s3)"], ["padding-right", "var(--s3)"], ["padding-top", "var(--s2)"]]],
        ["", ".utable-head", [["align-items", "end"], ["color", "var(--ink-3)"], ["font-size", "12px"], ["font-weight", "600"], ["line-height", "15px"]]],
        ["", ".utable-head .utable-num", [["hyphens", "auto"], ["justify-self", "end"], ["max-inline-size", "100%"], ["overflow-wrap", "break-word"], ["width", "min-content"]]],
        ["", ".utable-label", [["min-width", "0"], ["overflow-wrap", "break-word"]]],
        ["", ".utable-num", [["font-variant-numeric", "tabular-nums"], ["text-align", "right"]]],
        ["", ".uchart", [["background", "var(--surface)"], ["border", "1px solid var(--line)"], ["border-radius", "var(--r-card)"], ["margin-bottom", "var(--s2)"], ["margin-left", "0"], ["margin-right", "0"], ["margin-top", "0"], ["padding-bottom", "var(--s2)"], ["padding-left", "var(--s3)"], ["padding-right", "var(--s3)"], ["padding-top", "var(--s3)"]]],
        ["", ".uchart-plot", [["align-items", "flex-end"], ["border-bottom", "1px solid var(--chart-base)"], ["display", "flex"], ["gap", "2px"]]],
        ["", ".uchart-col", [["align-items", "center"], ["display", "flex"], ["flex", "1 1 0"], ["flex-direction", "column"], ["justify-content", "flex-end"], ["min-width", "0"]]],
        ["", ".uchart-val", [["color", "var(--ink-2)"], ["font-size", "11px"], ["font-variant-numeric", "tabular-nums"], ["line-height", "14px"], ["margin-bottom", "4px"], ["white-space", "nowrap"]]],
        ["", ".uchart-bar", [["background", "var(--chart-rest)"], ["border-radius", "4px 4px 0 0"], ["inline-size", "min(24px, 72%)"]]],
        ["", ".uchart-bar.is-zero", [["background", "var(--chart-base)"]]],
        ["", ".is-today > .uchart-bar", [["background", "var(--accent)"]]],
        ["", ".is-today > .uchart-val", [["color", "var(--ink)"], ["font-weight", "600"]]],
        ["", ".uchart-days", [["display", "flex"], ["gap", "2px"], ["margin-top", "6px"]]],
        ["", ".uchart-day", [["color", "var(--ink-3)"], ["flex", "1 1 0"], ["font-size", "11px"], ["line-height", "14px"], ["min-width", "0"], ["overflow", "hidden"], ["text-align", "center"], ["white-space", "nowrap"]]],
        ["", ".uchart-day.is-today", [["color", "var(--ink)"], ["font-weight", "600"]]],
        ["", ".uchart-note", [["margin-bottom", "var(--s3)"]]],
    ],
}

#: Custom properties the Usage page added to ``:root`` (and the no
#: color-mix fallback): the meter track and the chart's two tones.
_ROOT_ADDED = {
    "": [["--chart-base", "color-mix(in srgb, var(--ink) 35%, var(--surface))"],
         ["--chart-rest", "color-mix(in srgb, var(--accent) 42%, var(--surface))"],
         ["--meter-track", "color-mix(in srgb, var(--ink) 16%, var(--surface))"]],
    "@supports not (color: color-mix(in srgb, red 50%, blue))": [
        ["--chart-base", "var(--ink)"],
        ["--chart-rest", "var(--tg-theme-hint-color, #999999)"],
        ["--meter-track", "var(--tg-theme-section-separator-color, #d9d9d9)"]],
}


#: The Usage tab (roadmap 8.131): three tabs fit 320px in every language
#: (a size smaller, a long label may break); after the tab's own rule.
_USAGE_TAB_ADDED = {
    ("", ".tabbar-btn"): [
        ["", ".tabbar-btn", [["min-width", "0"], ["padding-bottom", "0"], ["padding-left", "2px"],
                             ["padding-right", "2px"], ["padding-top", "0"]]],
        ["", ".tabbar-label", [["hyphens", "auto"], ["line-height", "18px"], ["min-width", "0"],
                               ["overflow-wrap", "anywhere"], ["text-align", "center"]]],
        ["", ".tabbar:has(> #maintab-usage:not([hidden])) .tabbar-btn", [["font-size", "14px"]]],
    ],
}

#: Narrow phones (320px) in every language: a tile's state word and age
#: drop below the ring and may wrap between words (the tile grows; a
#: little less padding under 360px), "no folder" wraps, and a beacon's age
#: drops to its own line before the name is cut. A tab's count goes under
#: its label rather than break the word.
_NARROW_ADDED = {
    ("", ".tabbar-btn"): [
        ["", ".tabbar-btn", [["column-gap", "var(--s2)"], ["flex-wrap", "wrap"],
                             ["row-gap", "0"]]],
    ],
    ("", ".beacon-age"): [
        ["", ".beacon-top", [["flex-wrap", "wrap"], ["row-gap", "2px"]]],
    ],
    ("", ".tile-age"): [
        ["", ".tile", [["height", "auto"], ["min-height", "132px"]]],
        ["", ".tile-top", [["flex-wrap", "wrap"]]],
        ["", ".tile-side", [["flex", "1 1 auto"]]],
        ["", ".tile-state, .tile-age", [["max-width", "100%"], ["overflow-wrap", "break-word"],
                                        ["text-align", "right"], ["white-space", "normal"]]],
        ["", ".tile-state", [["border-radius", "var(--r-chip)"]]],
        ["", ".tile-project.tile-none", [["overflow-wrap", "break-word"],
                                         ["white-space", "normal"]]],
        ["", ":root:lang(ko) .tile-state, :root:lang(ko) .tile-age",
         [["word-break", "keep-all"]]],
        ["@media (max-width: 359px)", ".tile", [["padding-left", "12px"],
                                                ["padding-right", "12px"]]],
        ["@media (max-width: 359px)", ".tile-state", [["padding-left", "6px"],
                                                      ["padding-right", "6px"]]],
    ],
}

# The per-script fonts: the @font-face rules first (one per bundled file,
# checked in tests/test_miniapp_fonts.py), and each script's font stack
# after the light scheme rule. Fonts only; no layout changes.
_SYS = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
_FONTS_ADDED = {
    ("", 'html[data-scheme="light"]'): [
        ["", ":root:lang(fa)", [["--font", '"Vazirmatn", ' + _SYS],
                                ["--mono", 'ui-monospace, SFMono-Regular, Menlo, "Vazirmatn", monospace']]],
        ["", ":root:lang(ar)", [["--font", '"Noto Sans Arabic", ' + _SYS],
                                ["--mono", 'ui-monospace, SFMono-Regular, Menlo, "Noto Sans Arabic", '
                                           'monospace']]],
        ["", ":root:lang(vi)", [["--font", '"Be Vietnam Pro", ' + _SYS]]],
        ["", ":root:lang(ko)", [["--font", '"Apple SD Gothic Neo", "Noto Sans KR", "Malgun Gothic", '
                                           + _SYS]]],
        ["", ":root:lang(zh)", [["--font", '"PingFang SC", "Noto Sans SC", "Microsoft YaHei", '
                                           + _SYS]]],
    ],
}


def _expected_now(golden: list) -> list:
    # (the faces' own content: tests/test_miniapp_fonts.py)
    out = [r for r in _as_json(ltr_layout()) if r[1] == "@font-face"]
    assert len(out) == 10 and all(r[1] == "@font-face" for r in out)
    for ctx, sel, props in golden:
        if sel == ":root" and ctx in _ROOT_ADDED:
            props = sorted(props + _ROOT_ADDED[ctx])
        if sel in _PERCENT_BEFORE:
            out.append([ctx, _PERCENT_AFTER, [p for p in props if p[0] != "content"]])
        else:
            out.append([ctx, sel, props])
        if sel == _APPEARANCE_AFTER:
            out.extend(_APPEARANCE_ADDED)
        out.extend(_UPDATES_CARD_ADDED.get((ctx, sel), []))
        out.extend(_APPEARANCE_SECTION_ADDED.get((ctx, sel), []))
        out.extend(_TASKS_ADDED.get((ctx, sel), []))
        out.extend(_USAGE_ADDED.get((ctx, sel), []))
        out.extend(_USAGE_TAB_ADDED.get((ctx, sel), []))
        out.extend(_FONTS_ADDED.get((ctx, sel), []))
        out.extend(_NARROW_ADDED.get((ctx, sel), []))
    return out


def test_ltr_layout_is_unchanged():
    got = _as_json(ltr_layout())
    if os.environ.get("AIPAGER_RECORD_CSS") == "1":
        GOLDEN.write_text("[\n" + ",\n".join(json.dumps(r, ensure_ascii=True) for r in got)
                          + "\n]\n", encoding="utf-8")
    want = _expected_now(json.loads(GOLDEN.read_text(encoding="utf-8")))
    assert [r[1] for r in got] == [r[1] for r in want]
    for g, w in zip(got, want):
        assert g == w, g[1]


def test_the_resolver_sees_a_swapped_side():
    """Guard the guard: logical padding with its two sides swapped
    resolves differently from the original."""
    a = resolve_ltr([("padding", "1px 2px 3px 4px")])
    assert a == resolve_ltr([("padding-block", "1px 3px"), ("padding-inline", "4px 2px")])
    assert a != resolve_ltr([("padding-block", "1px 3px"), ("padding-inline", "2px 4px")])
    assert resolve_ltr([("margin-inline-start", "auto")]) == {"margin-left": "auto"}
    assert resolve_ltr([("text-align", "start")]) == {"text-align": "left"}


# ---- right to left ------------------------------------------------------------------------

_PHYSICAL = re.compile(r"^(margin|padding)-(left|right)$|^(left|right)$|^border-(left|right)")
#: Deliberately physical: the safe-area insets are physical by nature, and
#: the toast is centred symmetrically (left: 50% with a -50% translate).
_ALLOWED_PHYSICAL = {("body", "padding"), ("#notice", "left")}


def _asymmetric(prop: str, value: str) -> bool:
    v = _values(value)
    return prop in ("margin", "padding") and len(v) == 4 and v[1] != v[3]


def test_no_physical_property_left():
    bad = []
    for ctx, sel, decls in rules():
        if _rtl_only(sel):
            continue
        for prop, value in decls:
            if (sel, prop) in _ALLOWED_PHYSICAL:
                continue
            if _PHYSICAL.search(prop) or _asymmetric(prop, value) or (
                    prop == "text-align" and value in ("left", "right")):
                bad.append((ctx, sel, prop, value))
    assert not bad, bad


def _rtl_rules() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for _ctx, sel, decls in rules():
        for one in sel.split(","):
            one = one.strip()
            if one.startswith('html[dir="rtl"] '):
                out.setdefault(one[len('html[dir="rtl"] '):], {}).update(dict(decls))
    return out


def test_rtl_overrides():
    """Which way an icon points and where the menu grows from: what
    logical properties cannot say."""
    rtl = _rtl_rules()
    assert rtl[".chev"]["transform"] == "scaleX(-1)"                 # closed: points left
    assert rtl[".chev.is-open"]["transform"] == "rotate(90deg)"      # open: points down
    assert rtl[".is-open > .chev"]["transform"] == "rotate(90deg)"
    assert rtl[".grp-caret::before"]["transform"] == "rotate(45deg)"
    assert rtl[".grp.is-open .grp-caret::before"]["transform"] == "rotate(-45deg)"
    assert rtl[".icon-btn .ic"]["transform"] == "scaleX(-1)"         # the "open" arrow
    assert rtl[".menu"]["transform-origin"] == "top left"
    # The caret draws its corner on the inline end, so the override above
    # turns it the other way.
    caret = dict(next(d for _c, s, d in rules() if s == ".grp-caret::before"))
    assert "border-inline-end" in caret and "border-right" not in caret


def test_rtl_letter_spacing():
    """Tracking breaks joined Persian letters: none in right to left."""
    rtl = _rtl_rules()
    for sel in ("h1", ".sect-title", ".lamp-num", ".pulse-main", ".detail-label"):
        assert rtl[sel]["letter-spacing"] == "0", sel


def test_percent_is_text():
    """The ring's percent sign is a span the page fills with its language's
    sign, drawn exactly as the old CSS content was."""
    assert 'content: "%"' not in CSS
    pct = dict(next(d for _c, s, d in rules() if s == ".lamp-pct"))
    assert pct == {"font-size": "0.72em", "font-weight": "600", "opacity": "0.75"}


def test_the_token_rows_keep_a_layout_without_subgrid():
    """A WebView without subgrid drops ``grid-template-columns: subgrid``
    and keeps the row's own columns, declared before it in the same rule
    (the resolver above sees only the last one)."""
    rule = re.search(r"\.utable-row \{([^}]*)\}", _strip_comments(CSS)).group(1)
    own = rule.index("grid-template-columns: minmax(0, 1.4fr) repeat(4, minmax(0, 1fr));")
    assert own < rule.index("grid-template-columns: subgrid;")
