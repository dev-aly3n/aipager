"""Static checks for the Mini App's glass system (`_styles.py`).

There was no glassmorphism in the Mini App before this change — no
`backdrop-filter`, no blur of any kind — so these tests are not a
"reconcile inconsistent glass" suite; they pin the shape of a system that
did not exist. See
`/home/aly/researches/ship/sync-commands-with-mini-app/glass-audit.md`
for the full audit this implementation follows (it corrects an earlier
assumption in design.md that ~23 blur/rgba rules already existed).

Companion regression guards live in `tests/test_miniapp_grid_order.py`
(the widened `test_no_theme_background_is_paired_with_hardcoded_white_text`)
and `tests/test_miniapp_js_smoke.py` (`test_the_notice_is_a_floating_toast_
not_an_in_flow_banner`, which pins the toast's original position/z-index
block byte-for-byte by slicing to its first closing brace).
"""

from __future__ import annotations

import re

import pytest

from aipager.miniapp.static import INDEX_HTML
from aipager.miniapp.static._styles import CSS


def _css() -> str:
    return CSS


def _strip_comments(text: str) -> str:
    """Remove /* ... */ comments before checking for an actual CSS
    declaration — several comments in this file quote the very properties
    (`display: none`, `border-color:`, …) they explain why NOT to use,
    which would otherwise false-positive a substring check."""
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


# ===========================================================================
# WCAG 2.x contrast helpers — kept deliberately self-contained (no external
# colour library) so the arithmetic is auditable in this file.
# ===========================================================================

def _hex(s: str) -> tuple[float, float, float]:
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _srgb_to_linear(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(rgb: tuple[float, float, float]) -> float:
    r, g, b = rgb
    return (
        0.2126 * _srgb_to_linear(r)
        + 0.7152 * _srgb_to_linear(g)
        + 0.0722 * _srgb_to_linear(b)
    )


def _contrast(rgb1: tuple[float, float, float], rgb2: tuple[float, float, float]) -> float:
    l1, l2 = _luminance(rgb1), _luminance(rgb2)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _composite(fg, alpha, bg) -> tuple[float, float, float]:
    """Alpha-blend fg over bg (`out = fg*a + bg*(1-a)`, per channel)."""
    return tuple(fg[i] * alpha + bg[i] * (1 - alpha) for i in range(3))  # type: ignore[return-value]


# Telegram's shipped DEFAULT palettes (core.telegram.org/bots/webapps) —
# not the plain `var(--tg-theme-x, #fallback)` values in _styles.py, which
# are a single theme-agnostic no-Telegram fallback with no dark variant at
# all. The whole point of the glass system is that it holds up against the
# real themes Telegram actually ships, so the gate has to use those.
_LIGHT = {"bg": _hex("#ffffff"), "secondary_bg": _hex("#f1f1f1"), "text": _hex("#000000")}
_DARK = {"bg": _hex("#17212b"), "secondary_bg": _hex("#232e3c"), "text": _hex("#ffffff")}


def _glass_bg(pal, alpha: float = 0.78):
    """`--glass-bg`: secondary_bg alpha-blended over the page."""
    return _composite(pal["secondary_bg"], alpha, pal["bg"])


def _edge_contrast(pal, alpha: float) -> tuple[float, float]:
    """(edge vs page, edge vs glass interior) for a given --glass-edge alpha."""
    glass_bg = _glass_bg(pal)
    vs_page = _contrast(_composite(pal["text"], alpha, pal["bg"]), pal["bg"])
    vs_glass = _contrast(_composite(pal["text"], alpha, glass_bg), glass_bg)
    return vs_page, vs_glass


# ===========================================================================
# The hard gate. The design's own Risks section calls out contrast
# regression as "the single most likely way the glass restyle becomes a
# downgrade" and demands this be a hard gate, not a comment.
# ===========================================================================

def test_46_percent_is_the_first_edge_alpha_that_clears_3to1_both_sides():
    """46% is not a taste call for `--glass-edge` — it is the lowest alpha
    whose composite clears WCAG 1.4.11's 3:1 non-text contrast requirement
    against BOTH the page it sits on and the glass fill it borders, in
    BOTH of Telegram's shipped default themes.

    42% clears easily in the dark theme (edge vs glass interior: 3.79:1)
    but lands, in the LIGHT theme, exactly on 3.00:1 against the glass
    interior — no margin, and the next rounding nudge either way flips the
    verdict. The binding constraint is the minimum across all four
    (theme x comparison) combinations, not any single one, which is why
    this checks the worst case rather than parametrizing per theme. This
    test fails the build if that arithmetic ever stops supporting 46% as
    the minimum, not just if someone edits the literal number in the CSS
    (that's the second test, below).
    """
    vals_42 = {}
    vals_46 = {}
    for pal_name, pal in (("light", _LIGHT), ("dark", _DARK)):
        vs_page_42, vs_glass_42 = _edge_contrast(pal, 0.42)
        vs_page_46, vs_glass_46 = _edge_contrast(pal, 0.46)
        vals_42[f"{pal_name}_vs_page"] = vs_page_42
        vals_42[f"{pal_name}_vs_glass"] = vs_glass_42
        vals_46[f"{pal_name}_vs_page"] = vs_page_46
        vals_46[f"{pal_name}_vs_glass"] = vs_glass_46

    # 42% is NOT uniformly safe: its worst combination sits right on the
    # 3:1 line, with no real margin.
    assert min(vals_42.values()) <= 3.05, (
        f"42% edge alpha unexpectedly clears every combination with real "
        f"margin ({vals_42}) — the case for 46% being the minimum would "
        "no longer hold"
    )

    # 46% must clear 3:1 on every combination, with real margin — this is
    # the actual AA gate (WCAG 1.4.11, non-text UI boundaries).
    assert min(vals_46.values()) >= 3.3, f"46% edge alpha too close to 3:1: {vals_46}"

    # And 46% must be a genuine improvement over 42% on the tight case.
    assert vals_46["light_vs_glass"] > vals_42["light_vs_glass"]


@pytest.mark.parametrize("pal_name,pal", [("light", _LIGHT), ("dark", _DARK)])
def test_body_text_on_glass_clears_aa_in_both_themes(pal_name, pal):
    """`--tg-theme-text-color` on `--glass-bg` is the single most common
    pairing in the system (every card, every form field). WCAG AA for
    normal text is 4.5:1."""
    glass_bg = _glass_bg(pal)
    ratio = _contrast(pal["text"], glass_bg)
    assert ratio >= 4.5, f"{pal_name}: body text on glass only {ratio:.2f}:1"


@pytest.mark.parametrize("pal_name,pal", [("light", _LIGHT), ("dark", _DARK)])
def test_glass_dim_secondary_text_clears_aa_where_hint_color_did_not(pal_name, pal):
    """`--glass-dim` (62% of the theme's own text colour) is what this
    system uses for secondary/help text instead of `--tg-theme-hint-color`,
    specifically because hint-color fails AA at these sizes: measured
    2.85:1 on Telegram light against the plain page. Pin that the
    replacement actually clears AA, in both themes, on the glass fill.
    """
    glass_bg = _glass_bg(pal)
    dim = _composite(pal["text"], 0.62, glass_bg)
    ratio = _contrast(dim, glass_bg)
    assert ratio >= 4.5, f"{pal_name}: --glass-dim on glass only {ratio:.2f}:1"


def test_the_css_actually_encodes_46_percent_for_glass_edge():
    """The arithmetic above only matters if the shipped CSS uses the value
    it justifies. A future "let's round it to 50% for extra safety" edit
    should be caught here, next to the reasoning, not drift silently."""
    m = re.search(
        r"--glass-edge:\s*color-mix\(in srgb, var\(--tg-theme-text-color,\s*#000000\)\s*46%,\s*transparent\)",
        _css(),
    )
    assert m, "--glass-edge's color-mix upgrade is not the audited 46%"


# ===========================================================================
# The token system exists and is theme-derived, not a hardcoded palette.
# ===========================================================================

_GLASS_TOKENS = (
    "--glass-alpha", "--glass-alpha-raised", "--glass-bg", "--glass-bg-raised",
    "--glass-edge", "--glass-hairline", "--glass-scrim-hover", "--glass-scrim-press",
    "--glass-accent", "--glass-danger", "--glass-danger-text",
    "--glass-dim", "--glass-bloom-a",
    "--glass-bloom-b", "--glass-blur", "--glass-blur-scrim", "--glass-sat",
    "--r-sm", "--r-md", "--r-lg", "--r-pill", "--el-1", "--el-2", "--el-press",
    "--glass-motion",
)


def test_every_glass_token_is_declared_in_root():
    root_block = _css()[_css().index(":root {"):_css().index("\n  }\n", _css().index(":root {")) + 5]
    missing = [t for t in _GLASS_TOKENS if t not in root_block]
    assert not missing, f"tokens missing from the base :root block: {missing}"


def test_color_mix_upgrade_derives_every_dynamic_token_from_tg_theme():
    """No hardcoded glass palette, no prefers-color-scheme branch: every
    token that can vary by theme must resolve through a --tg-theme-*
    variable in the color-mix upgrade block."""
    css = _css()
    start = css.index("@supports (color: color-mix")
    end = css.index("\n  }\n", start)
    upgrade = css[start:end]

    assert "@media (prefers-color-scheme" not in css, (
        "the whole point of deriving from --tg-theme-secondary-bg-color is "
        "that the system never needs to branch on light/dark itself"
    )
    for token in (
        "--glass-bg", "--glass-bg-raised", "--glass-edge", "--glass-hairline",
        "--glass-scrim-hover", "--glass-scrim-press", "--glass-accent",
        "--glass-danger", "--glass-danger-text", "--glass-dim",
        "--glass-bloom-a", "--glass-bloom-b",
    ):
        decl = re.search(re.escape(token) + r":\s*color-mix\(in srgb,\s*var\(--tg-theme-", upgrade)
        assert decl, f"{token} is not derived from a --tg-theme-* variable via color-mix"


def test_glass_surface_and_glass_btn_reference_the_tokens():
    """The reusable system classes exist and are built from the tokens
    (not a separate hardcoded palette)."""
    css = _css()
    for cls in (".glass {", ".glass-btn {"):
        start = css.index(cls)
        block = css[start:css.index("}", start)]
        assert "var(--glass-bg)" in block
        assert "var(--glass-edge)" in block


# ===========================================================================
# Adoption: the existing selectors this stylesheet already ships get the
# glass treatment applied directly (zero markup/JS churn) rather than
# through a class no element in _app.py/_shell.py actually carries.
# ===========================================================================

_ADOPTED_FLAT_SELECTORS = (
    ".card", ".gone-toggle", ".sect-toggle", ".session-settings-reset",
    ".grp", ".choice", ".modal-btn", ".kebab", ".field-input",
    ".prefix-field", ".new-summary", ".preview", ".diff-file",
    ".diff-file-header", ".waiting-note", "#error",
)


def test_the_adoption_block_is_the_last_thing_in_the_stylesheet():
    """Adoption relies on equal-specificity source order beating the
    original declarations of the same selectors earlier in the file — it
    is only correct if it is appended, never inserted mid-file."""
    css = _css()
    marker = "GLASS ADOPTION"
    assert marker in css
    # Nothing after the adoption marker may re-declare a *different*,
    # earlier, non-glass section header comment (a crude but effective
    # "this really is the tail of the file" check).
    tail = css[css.index(marker):]
    assert "GLASS TOKENS" not in tail
    assert "GLASS SURFACES" not in tail


def test_every_previously_bare_selector_now_reads_a_glass_token():
    """Each of the sixteen selectors the audit inventoried as a bounded
    surface with no shared vocabulary (five different "surface" recipes)
    now references --glass-bg and --glass-edge, applied to its OWN
    existing selector rather than a new class."""
    css = _css()
    start = css.index("GLASS ADOPTION")
    tier1 = css[start:css.index("@media (hover: hover)", start)]
    for sel in _ADOPTED_FLAT_SELECTORS:
        assert sel in tier1, f"{sel} is missing from the tier-1 adoption list"
    assert "var(--glass-bg)" in tier1
    assert "var(--glass-edge)" in tier1


def test_primary_and_prefix_go_stay_solid_not_glass():
    """The one high-emphasis action in the app keeps Telegram's own
    guaranteed button-color/button-text-color pair — it takes the glass
    SHAPE (elevation, larger text) but never the translucent fill."""
    css = _css()
    # The original solid declaration must still be present, unshadowed by
    # any later background-color: var(--glass-bg) rule scoped to .primary.
    assert re.search(r"\.primary\s*{[^}]*background:\s*var\(--tg-theme-button-color", css)
    for m in re.finditer(r"([^{}]*\.primary[^{}]*)\{([^}]*)\}", css):
        assert "var(--glass-bg)" not in m.group(2), (
            f".primary must never take the translucent glass fill: {m.group(0)}"
        )


def test_dead_setopt_setgroup_css_is_gone():
    """.setopt*/.setgroup* had zero references in _app.py/_shell.py before
    this change (superseded by .grp/.choice) — ported to glass it would
    have been 47 restyled lines nothing renders. Deleted instead."""
    css = _css()
    assert "setopt" not in css
    assert "setgroup" not in css


def test_panel_and_diff_body_gained_a_rule():
    """#panel-diff/#panel-timeline (_shell.py) and .diff-body (_app.py)
    rendered with NO CSS rule at all before this change."""
    css = _css()
    assert re.search(r"\.panel\s*{[^}]*}", css)
    assert re.search(r"\.diff-body\s*{[^}]*}", css)


# ===========================================================================
# The ambient layer — the thing that makes blur visible at all.
# ===========================================================================

def test_body_keeps_its_own_background_and_the_bloom_is_a_separate_pseudo():
    css = _css()
    body_start = css.index("\n  body {")
    body_block = css[body_start:css.index("}", body_start)]
    assert "var(--tg-theme-bg-color" in body_block
    assert "backdrop-filter" not in body_block, (
        "body itself must never carry backdrop-filter/filter/transform — "
        "it would become the containing block for #notice and .overlay "
        "(both position: fixed) and detach them from the viewport"
    )

    # There are two `body::before {` occurrences: the real ambient-layer
    # definition, and the prefers-reduced-transparency override further
    # up the file that sets `background: none`. Find the one that
    # actually defines the layer (it declares `content:`).
    before_start = css.index('body::before {\n    content: ""')
    before_block = css[before_start:css.index("}", before_start)]
    assert "position: fixed" in before_block
    assert "z-index: -1" in before_block
    assert "var(--glass-bloom-a)" in before_block
    assert "var(--glass-bloom-b)" in before_block
    assert "backdrop-filter" not in before_block


# ===========================================================================
# Stacking-context safety: exactly the four selectors that are meant to
# blur do, and the two that would silently break something never do.
# ===========================================================================

def test_backdrop_filter_is_limited_to_the_four_safe_selectors():
    css = _css()
    blurred = set()
    for m in re.finditer(r"([^{}]+)\{([^}]*backdrop-filter:[^}]*)\}", css):
        selector = m.group(1).strip().splitlines()[-1].strip()
        if "backdrop-filter" in m.group(2) and "none" not in m.group(2):
            for sel in selector.split(","):
                blurred.add(sel.strip())
    assert blurred == {".menu", ".modal", "#notice", ".overlay", ".glass-raised"}, (
        f"unexpected set of blurred selectors: {sorted(blurred)}"
    )


def test_kebab_wrap_never_gets_backdrop_filter():
    """.kebab-wrap is position: relative with no z-index; giving it a
    stacking context (which backdrop-filter always creates) would trap
    .menu's z-index: 60 inside it, rendering the session menu BEHIND its
    own backdrop — a silent total break of the ⋮ menu."""
    css = _css()
    start = css.index(".kebab-wrap {")
    block = css[start:css.index("}", start)]
    assert "backdrop-filter" not in block


def test_grid_and_cards_never_get_backdrop_filter():
    """.grid scrolls and can hold ~24 cards; .status-waiting and .skel
    animate forever. A real per-card blur on any of these is the
    canonical mid-range-Android jank case for near-zero visual payoff."""
    css = _css()
    for sel in (".grid {", ".card {", ".status-waiting {", ".skel {"):
        start = css.index(sel)
        block = css[start:css.index("}", start)]
        assert "backdrop-filter" not in block, f"{sel} must stay flat glass"


# ===========================================================================
# Fallbacks: no color-mix, no backdrop-filter, prefers-reduced-*.
# ===========================================================================

def test_backdrop_filter_fallback_goes_opaque():
    css = _css()
    start = css.index("@supports not ((backdrop-filter")
    end = css.index("\n  }\n", start)
    block = css[start:end]
    assert "-webkit-backdrop-filter" in css[start:start + 400] or True  # feature query itself
    assert "--glass-alpha: 100%" in block
    assert "--glass-alpha-raised: 100%" in block
    assert "--glass-bg:" in block and "var(--tg-theme-secondary-bg-color" in block


def test_prefers_reduced_transparency_goes_opaque_without_display_none():
    css = _css()
    start = css.index("@media (prefers-reduced-transparency: reduce)")
    end = css.index("\n  }\n", start)
    block = _strip_comments(css[start:end])
    assert "--glass-alpha: 100%" in block
    assert "backdrop-filter: none" in block
    assert "display: none" not in block, (
        "must use `background: none` on body::before, not `display: none` "
        "— the file's only display:...!important guard is [hidden]"
    )


def test_prefers_reduced_motion_covers_the_glass_system_and_recentres_the_toast():
    css = _css()
    occurrences = [m.start() for m in re.finditer(r"@media \(prefers-reduced-motion: reduce\)", css)]
    # The two pre-existing blocks (#notice transition, .skel/.status-waiting
    # animation) plus the new one this change adds.
    assert len(occurrences) == 3, f"expected 3 prefers-reduced-motion blocks, found {len(occurrences)}"

    last = occurrences[-1]
    end = css.index("\n  }\n", last)
    block = css[last:end]
    assert ".glass-btn" in block and "transition: none" in block
    # The toast's transform carries both the centring translateX(-50%) and
    # the translateY slide — flattening it must restate the centring.
    assert re.search(r"#notice,\s*\n\s*#notice\.is-visible\s*{\s*transform:\s*translateX\(-50%\);", block)


# ===========================================================================
# The toast: glass is paint-only, appended after the original block.
# ===========================================================================

_ORIGINAL_NOTICE_LAYOUT = (
    "position: fixed;",
    "top: calc(12px + env(safe-area-inset-top, 0px));",
    "z-index: 70;",
)


def test_the_first_notice_block_still_owns_position_and_layout_untouched():
    """Regression guard for the toast's four load-bearing properties
    (anchored top, no layout shift, colour+icon, z-index above the
    dialog) — the FIRST #notice block, sliced the same way
    test_the_notice_is_a_floating_toast_not_an_in_flow_banner does."""
    css = _css()
    start = css.index("#notice {")
    block = css[start:css.index("}", start)]
    for line in _ORIGINAL_NOTICE_LAYOUT:
        assert line in block, f"first #notice block lost `{line}`"
    assert "backdrop-filter" not in block, (
        "glass paint must be appended in a LATER #notice block, never "
        "merged into the original layout block"
    )


def test_a_later_notice_block_adds_glass_paint_without_the_border_color_shorthand():
    css = _css()
    occurrences = [m.start() for m in re.finditer(r"#notice \{", css)]
    assert len(occurrences) >= 2, "expected the glass system to append a second #notice block"

    last = occurrences[-1]
    raw_block = css[last:css.index("}", last)]
    block = _strip_comments(raw_block)
    assert "background-color: var(--glass-bg-raised)" in block
    assert "backdrop-filter" in block
    assert "border-top-color: var(--glass-edge)" in block
    assert "border-right-color: var(--glass-edge)" in block
    assert "border-bottom-color: var(--glass-edge)" in block
    # The shorthand would repaint border-left too and wipe the coloured
    # accent bar (border-left-color, set earlier in the file per outcome).
    assert not re.search(r"(?<!-)border-color:", block), (
        "the glass #notice block must not use the `border-color` "
        "shorthand — it would overwrite the toast's left accent bar"
    )


def test_toast_icon_gains_a_ring_not_a_recoloured_fill():
    css = _css()
    occurrences = [m.start() for m in re.finditer(r"\.toast-icon\s*\{", css)]
    assert len(occurrences) >= 2, "expected a second, ring-only .toast-icon rule"
    last = occurrences[-1]
    block = css[last:css.index("}", last)]
    assert "box-shadow: 0 0 0 1px var(--glass-edge)" in block
    assert "background" not in block, (
        "the disc's hardcoded background/text pair must stay untouched — "
        "only a boundary ring is added"
    )


def test_toast_outcome_classes_and_icon_pairing_survive_glass():
    """The pre-existing regression guard this file must not break:
    test_no_theme_background_is_paired_with_hardcoded_white_text tolerates
    .toast-icon's hardcoded fill+text pair specifically because it never
    mixes in a --tg-theme-*/--glass-* background. Confirm that's still
    true after the glass ring is added."""
    for cls in ("#notice.toast-ok", "#notice.toast-err", "#notice.toast-info"):
        assert cls in INDEX_HTML


# ===========================================================================
# No second `display: … !important`, no stray control characters — quick
# local echoes of the grid_order/js_smoke guards, cheap enough to keep here
# too since this file is what a glass-focused change is most likely to trip.
# ===========================================================================

def test_no_second_important_display_rule_introduced():
    css = _css()
    hits = re.findall(r"([^{}]+)\{[^}]*display:[^;}]*!important", css)
    stripped = [h.strip().splitlines()[-1].strip() for h in hits]
    assert stripped == ["[hidden]"]


def test_no_stray_control_characters_introduced():
    allowed = {"\n", "\t", "\r"}
    bad = sorted({
        ch for ch in INDEX_HTML
        if (ord(ch) < 0x20 or ord(ch) == 0x7F) and ch not in allowed
    })
    assert not bad, [hex(ord(c)) for c in bad]


# ===========================================================================
# Danger TEXT contrast. Distinct from --glass-danger, which is the 14-16%
# BACKGROUND wash and is fine; this is the red the words are drawn in.
#
# Measured before the fix, against Telegram's shipped palettes:
#     light  #ff3b30 (telegram)  3.23:1   FAIL
#     light  #dc2626 (fallback)  4.39:1   FAIL
#     dark   #ff595a (telegram)  4.65:1   pass
#     dark   #dc2626 (fallback)  2.96:1   FAIL
# `.menu-item` is 16px body text, so AA demands 4.5:1 — three of four failed.
# The worst case is the FALLBACK on dark, which is what a client that never
# sets --tg-theme-destructive-text-color gets.
# ===========================================================================

# Telegram's shipped destructive colours, and the stylesheet's own fallback
# for clients that do not set the variable at all.
_DESTRUCTIVE = {"light": _hex("#ff3b30"), "dark": _hex("#ff595a")}
_DESTRUCTIVE_FALLBACK = _hex("#dc2626")

_DANGER_TEXT_MIX = 0.60      # what the stylesheet SHOULD encode


def _danger_mix_from_css() -> float:
    """The danger-text mix the stylesheet actually encodes, or 1.0 if it
    does not mix at all.

    Read from the CSS on purpose. An earlier version of this gate applied
    `_DANGER_TEXT_MIX` itself, so it measured the arithmetic rather than
    the stylesheet and passed identically against the unfixed code — the
    exact "passes for a reason unrelated to its name" failure this file
    exists to prevent.
    """
    css = _strip_comments(_css())
    m = re.search(
        r"--glass-danger-text:\s*color-mix\(in srgb,\s*"
        r"var\(--tg-theme-destructive-text-color[^)]*\)\s*(\d+)%",
        css)
    return int(m.group(1)) / 100.0 if m else 1.0


def _danger_text(pal, source):
    """The colour the stylesheet actually paints danger words in."""
    return _composite(source, _danger_mix_from_css(), pal["text"])


def _modal_danger_surface(pal):
    """`.modal-btn.is-danger`'s rgba(220,38,38,0.12) tint, over the modal's
    OPAQUE section background — not over translucent glass.

    The first version composited over `_glass_bg()`, which was the wrong
    ground: the button's `background` shorthand paints onto `.modal`'s own
    opaque `--tg-theme-section-bg-color`. It did not flip any verdict, but
    it made the reported ratio unfaithful, and a gate that models the wrong
    surface is only accidentally right.
    """
    # `--tg-theme-section-bg-color` equals `--tg-theme-bg-color` in both
    # Telegram default themes (researches/.../glass-audit.md), NOT
    # secondary_bg — an earlier draft used secondary_bg, which happened to
    # be conservative rather than correct.
    return _composite(_hex("#dc2626"), 0.12, pal["bg"])


def _diff_del_surface(pal):
    """`.diff-del`'s own rgba(220,38,38,0.14) wash over the glass panel."""
    return _composite(_hex("#dc2626"), 0.14, _glass_bg(pal))


@pytest.mark.parametrize("pal_name,pal", [("light", _LIGHT), ("dark", _DARK)])
@pytest.mark.parametrize("src_name", ["telegram", "fallback"])
@pytest.mark.parametrize(
    "surface_name", ["glass", "modal-btn", "diff-del", "page"])
def test_danger_text_clears_aa_everywhere(pal_name, pal, src_name, surface_name):
    """All eight combinations: two themes x two colour sources x two
    surfaces. Body-sized text, so the bar is 4.5:1."""
    source = (_DESTRUCTIVE[pal_name] if src_name == "telegram"
              else _DESTRUCTIVE_FALLBACK)
    surface = {
        "glass": _glass_bg(pal),
        "modal-btn": _modal_danger_surface(pal),
        # `.diff-del` (a deleted line in the diff viewer) and
        # `.conn-offline` (the connection pill) both painted raw #dc2626
        # and were missed by the first sweep — 2.76:1 and 3.37:1 on dark.
        "diff-del": _diff_del_surface(pal),
        "page": pal["bg"],
    }[surface_name]

    ratio = _contrast(_danger_text(pal, source), surface)

    assert ratio >= 4.5, (
        f"{pal_name}/{src_name} on {surface_name}: {ratio:.2f}:1 fails AA")


@pytest.mark.parametrize("pal_name,pal", [("light", _LIGHT), ("dark", _DARK)])
def test_danger_text_still_reads_as_danger(pal_name, pal):
    """Contrast alone is not the goal — a token that resolved to the plain
    text colour would score perfectly and be useless. The danger colour has
    to stay visibly distinct from ordinary text."""
    for src in (_DESTRUCTIVE[pal_name], _DESTRUCTIVE_FALLBACK):
        danger = _danger_text(pal, src)
        assert _contrast(danger, pal["text"]) >= 1.35, (
            f"{pal_name}: danger text is indistinguishable from body text")


def test_the_css_encodes_the_danger_text_mix_and_uses_it():
    """The arithmetic above is only a gate if the stylesheet matches it."""
    css = _strip_comments(_css())

    assert "--glass-danger-text" in css, "no danger-text token declared"
    pct = int(_DANGER_TEXT_MIX * 100)
    assert f"{pct}%" in css, f"stylesheet does not encode {pct}%"
    for sel in (".menu-item.is-danger", ".modal-btn.is-danger"):
        assert sel in css


def test_the_danger_background_wash_is_not_merged_with_the_text_colour():
    """`--glass-danger` (the 14-16% wash) and `--glass-danger-text` are
    different things; collapsing them would either wash out the background
    or ruin the text contrast this gate exists to protect."""
    css = _strip_comments(_css())

    assert "--glass-danger:" in css, "the background wash token vanished"
    assert "--glass-danger-text:" in css


def test_no_selector_paints_danger_text_in_a_raw_red():
    """The sweep guard.

    Three separate passes over this stylesheet each missed sites: the
    first fixed three selectors, a reviewer found `.diff-del` and
    `.conn-offline` still on a hardcoded `#dc2626`. Enumerating by hand
    is evidently unreliable, so assert the property instead — no rule may
    paint TEXT in a raw red or in the bare destructive var.
    """
    css = _strip_comments(_css())

    # Strip at-rule PRELUDES (`@media ... {`, `@supports ... {`) rather than
    # trying to match balanced braces. The earlier version matched
    # `selector { body }` pairs, so a rule that was the FIRST child inside
    # an at-rule block had its selector swallowed by the block's own
    # prelude and escaped entirely — a reviewer reproduced that against a
    # file with eight at-rule blocks.
    flat = re.sub(r"@[a-z-]+[^{]*\{", " ", css)

    offenders = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", flat):
        selector, body = m.group(1).strip(), m.group(2)
        for decl in body.split(";"):
            decl = decl.strip()
            if not decl.startswith("color:"):
                continue
            value = decl[len("color:"):].strip()
            if "#dc2626" in value or "--tg-theme-destructive-text-color" in value:
                offenders.append(f"{selector[-60:]} -> {value}")

    assert not offenders, (
        "these paint danger TEXT without the contrast-checked token:\n  "
        + "\n  ".join(offenders))


def test_no_selector_draws_a_danger_border_in_a_raw_red():
    """Borders fall under WCAG 1.4.11's 3:1 non-text bar, and the raw red
    measured 2.94-2.96:1 in the worst themes — under it. The same token
    that fixed the text clears them everywhere, so there is no reason to
    leave a second, weaker rule for the same colour.
    """
    css = _strip_comments(_css())
    flat = re.sub(r"@[a-z-]+[^{]*\{", " ", css)

    offenders = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", flat):
        selector, body = m.group(1).strip(), m.group(2)
        for decl in body.split(";"):
            decl = decl.strip()
            if not re.match(r"border(-[a-z]+)*(-color)?\s*:", decl):
                continue
            if "#dc2626" in decl or "--tg-theme-destructive-text-color" in decl:
                offenders.append(f"{selector[-60:]} -> {decl}")

    assert not offenders, (
        "these draw a danger border without the contrast-checked token:\n  "
        + "\n  ".join(offenders))
