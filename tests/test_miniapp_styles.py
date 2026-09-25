"""Static checks for the Mini App's "Lanterns" stylesheet (`_styles.py`).

Replaces the suite that pinned the retired glass system (`--glass-*`
tokens, the GLASS ADOPTION block, backdrop-filter selectors, emoji menu
icons). Its substantive guards carry over: WCAG arithmetic in Telegram's
own default palettes, the 60 % danger-text mix, no `prefers-color-scheme`,
the `[hidden]` guard, the top-anchored toast and the solid `.primary`.

The contrast numbers are computed from the percentages and hex values
READ OUT OF THE STYLESHEET, so a token edit that breaks AA fails here,
not only an edit to this file.
"""

from __future__ import annotations

import re

import pytest

from aipager.miniapp.static import INDEX_HTML
from aipager.miniapp.static._styles import CSS


def _strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


_CODE = _strip_comments(CSS)


def _root_block() -> str:
    start = _CODE.index(":root {")
    return _CODE[start:_CODE.index("}", start)]


def _block(selector: str, css: str = _CODE) -> str:
    """Body of the FIRST rule whose selector list is exactly `selector`."""
    # The selector must start its own rule (after a newline or a brace),
    # so ".pill" never matches the descendant rule ".d-title .pill".
    m = re.search(r"(?:^|[}\n])\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"no rule for {selector}"
    return m.group(1)


def _reduced_motion_blocks() -> list[str]:
    """The bodies of every `@media (prefers-reduced-motion: reduce)` block,
    found by brace depth (they hold nested rules)."""
    out = []
    for m in re.finditer(r"@media \(prefers-reduced-motion: reduce\)\s*\{", _CODE):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(_CODE[i], 0)
            i += 1
        out.append(_CODE[m.end():i - 1])
    return out


# ===========================================================================
# WCAG 2.x contrast helpers (self-contained, so the arithmetic is auditable)
# ===========================================================================

def _hex(s: str) -> tuple[float, float, float]:
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _lin(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _lum(rgb) -> float:
    r, g, b = rgb
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _contrast(a, b) -> float:
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _mix(a, pct: float, b):
    """`color-mix(in srgb, a pct%, b)`: per-channel sRGB interpolation."""
    p = pct / 100.0
    return tuple(a[i] * p + b[i] * (1 - p) for i in range(3))


def _pct(token: str) -> float:
    """The percentage a token's color-mix gives its first colour."""
    m = re.search(re.escape(token) + r":\s*color-mix\(in srgb,[^;]*?\s(\d+)%", _root_block())
    assert m, f"{token} is not a color-mix token"
    return float(m.group(1))


def _amber(token: str, scheme: str):
    if scheme == "light":
        body = _root_block()
    else:
        body = _block('html[data-scheme="dark"]')
    m = re.search(re.escape(token) + r":\s*(#[0-9a-fA-F]{6})", body)
    assert m, f"{token} has no fixed {scheme} value"
    return _hex(m.group(1))


# Telegram's shipped DEFAULT palettes (what the SDK writes as --tg-theme-*).
_PALETTES = {
    "light": {
        "surface": _hex("#ffffff"), "canvas": _hex("#f1f1f1"),
        "ink": _hex("#000000"), "button": _hex("#2481cc"),
        "button_text": _hex("#ffffff"), "destructive": _hex("#ff3b30"),
    },
    "dark": {
        "surface": _hex("#17212b"), "canvas": _hex("#232e3c"),
        "ink": _hex("#ffffff"), "button": _hex("#5288c1"),
        "button_text": _hex("#ffffff"), "destructive": _hex("#ff595a"),
    },
}
_DESTRUCTIVE_FALLBACK = _hex("#dc2626")


def _tokens(scheme: str, destructive=None) -> dict:
    p = _PALETTES[scheme]
    ink, surface, canvas, accent = p["ink"], p["surface"], p["canvas"], p["button"]
    red = destructive or p["destructive"]
    lamp_need = _amber("--lamp-need", scheme)
    t = {
        "surface": surface, "canvas": canvas, "ink": ink, "accent": accent,
        "on_accent": p["button_text"],
        "ink2": _mix(ink, _pct("--ink-2"), surface),
        "ink3": _mix(ink, _pct("--ink-3"), surface),
        "fill": _mix(ink, _pct("--fill"), surface),
        "work_soft": _mix(accent, _pct("--lamp-work-soft"), surface),
        "lamp_need": lamp_need,
        "need_ink": _amber("--need-ink", scheme),
        "need_soft": _mix(lamp_need, _pct("--need-soft"), surface),
        "kind_chip": _mix(lamp_need, 16, surface),
        "danger_ink": _mix(red, _pct("--danger-ink"), ink),
        "danger_soft": _mix(red, _pct("--danger-soft"), surface),
    }
    return t


# (foreground, background, where it is used). Every pair must clear 4.5:1.
_TEXT_PAIRS = [
    ("ink2", "surface", "secondary text on cards"),
    ("ink2", "canvas", "secondary text on the page"),
    ("ink3", "surface", "captions, ages, notes on cards"),
    ("ink3", "canvas", "section titles and notes on the page"),
    ("ink2", "fill", "state chips, tags, activity chips"),
    ("ink", "work_soft", "a working tile's state chip"),
    ("ink2", "work_soft", "a selected chip's hint"),
    ("need_ink", "surface", "needs-you text on a card"),
    ("need_ink", "canvas", "the Needs you heading"),
    ("need_ink", "need_soft", "needs-you text on the beacon"),
    ("need_ink", "kind_chip", "the Permission / Question chip"),
    ("ink2", "need_soft", "ages and notes on the beacon"),
    ("ink", "need_soft", "the beacon's summary"),
    ("surface", "need_ink", "Answer in chat and the waiting badge"),
    ("danger_ink", "surface", "destructive menu items"),
    ("danger_ink", "danger_soft", "destructive dialog buttons"),
    ("surface", "danger_ink", "the error toast icon"),
    ("surface", "ink", "the step numbers"),
]


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("fg, bg, where", _TEXT_PAIRS)
def test_every_text_pair_clears_aa(scheme, fg, bg, where):
    t = _tokens(scheme)
    ratio = _contrast(t[fg], t[bg])
    assert ratio >= 4.5, f"{scheme}: {where} ({fg} on {bg}) is {ratio:.2f}:1"


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_danger_text_clears_aa_with_the_fallback_red_too(scheme):
    """No `--tg-theme-destructive-text-color`: the #dc2626 fallback."""
    t = _tokens(scheme, destructive=_DESTRUCTIVE_FALLBACK)
    assert _contrast(t["danger_ink"], t["surface"]) >= 4.5
    assert _contrast(t["danger_ink"], t["danger_soft"]) >= 4.5


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("lamp", ["lamp_need", "accent"])
def test_the_lamps_clear_3_to_1_as_non_text(scheme, lamp):
    """The amber beacon ring and the working ring are graphics (WCAG
    1.4.11): 3:1 against the card and the page."""
    t = _tokens(scheme)
    for bg in ("surface", "canvas"):
        ratio = _contrast(t[lamp], t[bg])
        assert ratio >= 3.0, f"{scheme}: {lamp} on {bg} is {ratio:.2f}:1"


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_primary_button_pair_is_the_one_named_exception(scheme):
    """`.primary` uses Telegram's own button_color / button_text_color, the
    pair its native MainButton draws with. About 4.1:1 on the light default
    with 16 px / 600 text: accepted (design.md Risks), gated at 3:1."""
    t = _tokens(scheme)
    assert _contrast(t["on_accent"], t["accent"]) >= 3.0


def test_the_amber_text_is_darker_than_the_amber_lamp_in_light():
    """The lamp colour (3.1:1) is never used for text; the text colour is
    its own darker token."""
    assert _amber("--need-ink", "light") != _amber("--lamp-need", "light")
    for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", _CODE):
        for decl in re.findall(r"(?<![-\w])color:\s*([^;]+)", body):
            assert "--lamp-need" not in decl, f"{sel.strip()} uses the lamp as text"


# ===========================================================================
# Tokens
# ===========================================================================

_TOKENS = (
    "--font", "--mono", "--fs-cap", "--fs-sm", "--fs-body", "--fs-title",
    "--fs-display", "--s1", "--s2", "--s3", "--s4", "--s5", "--s6",
    "--r-chip", "--r-ctl", "--r-card", "--r-pill", "--t-fast", "--t-base",
    "--t-enter", "--ease", "--canvas", "--surface", "--ink", "--ink-2",
    "--ink-3", "--line", "--fill", "--accent", "--on-accent", "--lamp-work",
    "--lamp-work-soft", "--lamp-need", "--need-ink", "--need-soft",
    "--lamp-rest", "--lamp-out", "--danger-ink",
)


def test_every_lantern_token_is_declared_in_root():
    root = _root_block()
    missing = [t for t in _TOKENS if f"{t}:" not in root]
    assert not missing, missing


@pytest.mark.parametrize("token", [
    "--canvas", "--surface", "--ink", "--line", "--accent", "--on-accent",
    "--danger-ink",
])
def test_colour_tokens_derive_from_telegrams_theme(token):
    m = re.search(re.escape(token) + r":\s*([^;]+);", _root_block())
    assert m and "var(--tg-theme-" in m.group(1), f"{token} is not theme-derived"


def test_the_dark_scheme_only_swaps_the_amber_pair():
    body = _block('html[data-scheme="dark"]')
    assert "--lamp-need:" in body and "--need-ink:" in body


def test_a_webview_without_color_mix_gets_a_fallback():
    m = re.search(
        r"@supports not \(color: color-mix\(in srgb, red 50%, blue\)\)\s*\{(.*?)\}\s*\}",
        _CODE, re.DOTALL,
    )
    assert m, "no color-mix fallback"
    for token in ("--ink-2", "--ink-3", "--fill", "--line", "--need-soft", "--danger-ink"):
        assert f"{token}:" in m.group(1), f"{token} has no fallback"
        assert "color-mix" not in m.group(1)


def test_telegrams_theme_always_wins():
    """No OS-level scheme query: the Telegram theme is the only source."""
    assert "prefers-color-scheme" not in _CODE


def test_no_backdrop_filter_anywhere():
    """Flat surfaces, and no containing-block trap for fixed children."""
    assert "backdrop-filter" not in _CODE


def test_no_web_fonts_imports_or_remote_urls():
    assert "@font-face" not in _CODE
    assert "@import" not in _CODE
    for m in re.finditer(r"url\(", _CODE):
        assert _CODE[m.end()] == "#", "a url() that is not a local fragment"


def test_the_stylesheet_has_no_backslash():
    """A NON-raw Python string: a CSS escape would be eaten by Python."""
    assert "\\" not in CSS


# ===========================================================================
# Components
# ===========================================================================

def test_primary_is_a_solid_telegram_button():
    body = _block(".primary")
    assert "background: var(--tg-theme-button-color" in body
    assert "color: var(--tg-theme-button-text-color" in body


def test_the_danger_text_mix_is_sixty_percent_and_used_by_both_danger_buttons():
    assert _pct("--danger-ink") == 60
    assert "var(--danger-ink)" in _block(".menu-item.is-danger, .menu-item.is-danger .ic")
    assert "var(--danger-ink)" in _block(".modal-btn.is-danger")


def test_no_selector_paints_text_or_a_border_in_a_raw_red():
    raw = re.compile(r"#dc2626|#ff3b30|#ff595a|destructive-text-color", re.I)
    for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", _CODE):
        if sel.strip().startswith(":root") or "@supports" in sel:
            continue
        for prop, value in re.findall(r"(?<![-\w])(color|border[\w-]*):\s*([^;]+)", body):
            assert not raw.search(value), f"{sel.strip()} {prop}: {value}"


def test_the_toast_is_fixed_top_anchored_and_above_the_dialog():
    body = _block("#notice")
    assert "position: fixed" in body
    assert "top:" in body and "bottom:" not in body
    assert "display: none" not in body
    z = int(re.search(r"z-index:\s*(\d+)", body).group(1))
    overlay_z = int(re.search(r"z-index:\s*(\d+)", _block(".overlay")).group(1))
    menu_z = int(re.search(r"z-index:\s*(\d+)", _block(".menu")).group(1))
    assert z > overlay_z and z > menu_z


def test_the_kebab_wrap_creates_no_stacking_context():
    """The menu (z 60) must compete with the scrim (z 50) at the root."""
    body = _block(".kebab-wrap")
    for prop in ("transform", "filter", "z-index", "opacity", "isolation"):
        assert prop not in body


_TAP_TARGETS = (
    ".tabbar-btn", ".chip", ".choice", ".kebab", ".menu-item", ".modal-btn",
    ".gone-toggle", ".sect-toggle", ".pill", ".icon-btn", ".primary",
    ".grp-head", ".shelf-row", ".beacon-body", ".disclosure", ".link-btn",
    ".updates-again", ".activity",
)


@pytest.mark.parametrize("selector", _TAP_TARGETS)
def test_every_control_is_at_least_44px_tall(selector):
    body = _block(selector)
    m = re.search(r"min-height:\s*(\d+)px", body)
    assert m, f"{selector} has no min-height"
    assert int(m.group(1)) >= 44, f"{selector} is {m.group(1)}px"


@pytest.mark.parametrize("selector", [".tile", ".fab", ".prefix-go", ".kebab", ".icon-btn"])
def test_fixed_size_controls_are_at_least_44px_on_both_axes(selector):
    body = _block(selector)
    h = re.search(r"(?<![-\w])(?:min-)?height:\s*(\d+)px", body)
    w = re.search(r"(?<![-\w])(?:min-)?width:\s*(\d+)px", body)
    assert h and int(h.group(1)) >= 44, f"{selector} height"
    if selector != ".tile":          # the tile is a grid column wide
        assert w and int(w.group(1)) >= 44, f"{selector} width"


def test_the_tile_has_a_fixed_height_so_polls_never_change_geometry():
    assert re.search(r"(?<![-\w])height:\s*132px", _block(".tile"))


def test_a_focus_ring_exists():
    assert re.search(r":focus-visible\s*\{[^}]*outline:\s*2px solid", _CODE)


def test_numbers_use_tabular_figures():
    m = re.search(r"([^{}]+)\{\s*font-variant-numeric: tabular-nums;\s*\}", _CODE)
    assert m, "no tabular-nums rule"
    sel = m.group(1)
    for cls in (".tile-meta", ".lamp-num", ".pulse-sub", ".d-state", ".facts dd",
                ".badge", ".act-chip", ".beacon-age"):
        assert cls in sel, f"{cls} is not tabular"


def test_the_ring_is_one_custom_property():
    body = _block(".lamp::before")
    assert "conic-gradient(var(--lamp-c) calc(var(--pct, 0) * 1%)" in body
    assert "mask:" in body


def test_mainbutton_hides_the_in_page_start_button_without_important():
    body = _block("html.has-mainbutton #new-create")
    assert "display: none" in body and "!important" not in body


# ===========================================================================
# Motion
# ===========================================================================

def test_every_infinite_animation_stops_under_reduced_motion():
    """Each selector that runs an infinite keyframe loop is set to
    `animation: none` inside a reduced-motion block, on a static state
    that still shows the state (steady ring, steady halo)."""
    reduced = "\n".join(_reduced_motion_blocks())
    assert reduced, "no reduced-motion block"
    looping = []
    for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", _CODE):
        if re.search(r"animation:[^;]*infinite", body):
            looping.extend(s.strip() for s in sel.split(","))
    assert looping, "found no infinite animation, the extraction is blind"
    stopped = set()
    for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", reduced):
        if "animation: none" in body:
            stopped.update(s.strip() for s in sel.split(","))
    missing = [s for s in looping if s not in stopped]
    assert not missing, f"still looping under reduced motion: {missing}"


def test_the_only_continuous_loops_are_state_signals():
    names = set(re.findall(r"animation:\s*(\S+)[^;]*infinite", _CODE))
    assert names <= {"breathe", "beacon", "swell", "skel"}, names


def test_reduced_motion_keeps_the_beacon_and_halo_visible():
    reduced = "\n".join(_reduced_motion_blocks())
    assert re.search(r"\.beacon::before[^{]*\{[^}]*opacity:\s*1", reduced)
    assert re.search(r"\.lamp-work::after\s*\{[^}]*opacity:\s*0\.8", reduced)


def test_loops_animate_only_opacity_and_transform():
    """Composited properties only: no layout and no animated shadow."""
    for name in ("breathe", "beacon", "swell", "skel"):
        m = re.search(r"@keyframes " + name + r"\s*\{(.*?)\}\s*\}", _CODE, re.DOTALL)
        assert m, name
        props = set(re.findall(r"([a-z-]+):", m.group(1)))
        assert props <= {"opacity", "transform"}, (name, props)


def test_the_view_entry_leaves_no_transform_behind():
    """No fill mode: a lingering transform on a section would trap the
    action menu in the section's stacking context."""
    m = re.search(r"section:not\(\[hidden\]\)\s*\{([^}]*)\}", _CODE)
    assert m and "view-in" in m.group(1)
    assert "forwards" not in m.group(1) and "both" not in m.group(1)


def test_the_page_carries_the_stylesheet():
    assert CSS in INDEX_HTML


def test_every_pulse_clause_keeps_its_count_and_noun_on_one_line():
    """The pulse sentence may wrap only at its commas: "2 need you, 2
    working" must never split into "2" / "working" (rev-iter1-005)."""
    from aipager.miniapp.static._app import APP_JS

    classes = set(re.findall(r'clauses\.push\(\["(pulse-[\w-]+)"', APP_JS))
    assert classes >= {"pulse-need", "pulse-work", "pulse-rest"}, classes
    rules = re.findall(r"([^{}]+)\{([^}]*)\}", _CODE)
    nowrap = {sel.strip().lstrip(".") for sels, body in rules
              if "white-space: nowrap" in body for sel in sels.split(",")}
    assert classes <= nowrap, classes - nowrap
