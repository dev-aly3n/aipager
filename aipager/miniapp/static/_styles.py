"""CSS for the Mini App page.

Extends stage 1's ``--tg-theme-*`` palette (Telegram's WebApp JS SDK
sets these as CSS custom properties on the document once ``tg.ready()``
runs — no manual theme-syncing JS needed, same as stage 1) with the
grid/card/status-badge/tab/diff-line styles stage 2 adds.
``.status-waiting`` deliberately gets the most visually urgent
treatment (solid fill + a slow pulse) of any status — surfacing
"waiting on permission" across every session at a glance is this
stage's single biggest win (spec.md). Stage 1's ``.status-interactive``
class is gone: the new UI never renders the raw ``"interactive"`` name.
"""

from __future__ import annotations

CSS = """\
  /* ==========================================================
     GLASS TOKENS
     There is no hardcoded glass palette and no prefers-color-scheme
     branch anywhere in this file. Every --glass-* property below
     resolves through a --tg-theme-* variable Telegram itself sets, so
     the whole system re-tints for whatever palette (or one of the ~40
     custom themes) the client hands us. The literal hex fallbacks are
     the NO-TELEGRAM case (page opened in a plain browser) only, never
     a design decision.

     Built from --tg-theme-secondary-bg-color, not white or black:
     --tg-theme-bg-color and --tg-theme-section-bg-color are IDENTICAL
     in Telegram's default light theme, so glass derived from either of
     those would be invisible. secondary-bg is the one variable that
     differs from the page background in both shipped themes. */
  :root {
    color-scheme: light dark;

    /* Opacity of the glass fill. The two @supports blocks below (one
       for missing color-mix, one for missing backdrop-filter) flip
       these to 100% so every surface in the system goes opaque from
       this one place. */
    --glass-alpha: 78%;
    --glass-alpha-raised: 88%;

    /* Non-color-mix baseline. A custom property whose value fails at
       SUBSTITUTION time makes the consuming declaration `unset`
       (background-color -> transparent); it does NOT fall back to an
       earlier declaration of the same property. So the plain value has
       to be the base and color-mix has to be the @supports upgrade,
       never the other way round. */
    --glass-bg:           rgba(127, 127, 127, 0.10);
    --glass-bg-raised:    rgba(127, 127, 127, 0.16);
    --glass-edge:         rgba(127, 127, 127, 0.46);
    --glass-hairline:     rgba(127, 127, 127, 0.16);
    --glass-scrim-hover:  rgba(127, 127, 127, 0.07);
    --glass-scrim-press:  rgba(127, 127, 127, 0.14);
    --glass-accent:       rgba(36, 129, 204, 0.16);
    --glass-danger:       rgba(220, 38, 38, 0.14);
    --glass-danger-text:  var(--tg-theme-destructive-text-color, #dc2626);
    --glass-dim:          var(--tg-theme-hint-color, #888888);
    --glass-bloom-a:      transparent;
    --glass-bloom-b:      transparent;

    /* Blur, used by exactly four selectors: .menu, .modal, #notice,
       .overlay. Every scrolling or repeating surface (the grid, the
       kebab, form controls, …) gets flat glass instead — see the
       ADOPTION block below for why. */
    --glass-blur:         22px;
    --glass-blur-scrim:   10px;
    --glass-sat:          150%;

    /* One radius scale, replacing seven ad-hoc values. */
    --r-sm:   9px;
    --r-md:  12px;
    --r-lg:  16px;
    --r-pill: 999px;

    /* One elevation scale, replacing three unrelated shadows. */
    --el-1: 0 1px 2px rgba(0, 0, 0, 0.06), 0 4px 14px rgba(0, 0, 0, 0.10);
    --el-2: 0 2px 8px rgba(0, 0, 0, 0.16), 0 12px 34px rgba(0, 0, 0, 0.30);
    --el-press: 0 1px 2px rgba(0, 0, 0, 0.10);

    --glass-motion: 0.16s cubic-bezier(0.2, 0, 0.2, 1);
  }

  /* Real tokens. color-mix landed in Chrome 111 (Mar 2023) and Safari
     16.2 (Dec 2022) — every Telegram client shipped since mid-2023 —
     and this file already bet on it twice before this change (the
     rgba-then-color-mix fallback pair at .choice.is-active's `background`
     was the same bet in a plain-property form; this is that bet extended
     to custom properties). */
  @supports (color: color-mix(in srgb, red 50%, transparent)) {
    :root {
      --glass-bg: color-mix(in srgb,
        var(--tg-theme-secondary-bg-color, rgba(127, 127, 127, 0.14))
        var(--glass-alpha), transparent);
      --glass-bg-raised: color-mix(in srgb,
        var(--tg-theme-secondary-bg-color, rgba(127, 127, 127, 0.20))
        var(--glass-alpha-raised), transparent);
      /* 46%: the lowest alpha whose composite clears 3:1 against the
         page AND against the glass interior, in BOTH default themes.
         42% lands exactly on 3.00:1 against the light glass interior —
         tests/test_miniapp_styles.py encodes this arithmetic as a hard
         gate, not a comment. */
      --glass-edge:        color-mix(in srgb, var(--tg-theme-text-color, #000000) 46%, transparent);
      --glass-hairline:    color-mix(in srgb, var(--tg-theme-text-color, #000000) 16%, transparent);
      --glass-scrim-hover: color-mix(in srgb, var(--tg-theme-text-color, #000000)  7%, transparent);
      --glass-scrim-press: color-mix(in srgb, var(--tg-theme-text-color, #000000) 14%, transparent);
      --glass-accent:      color-mix(in srgb, var(--tg-theme-button-color, #2481cc) 16%, transparent);
      --glass-danger:      color-mix(in srgb, var(--tg-theme-destructive-text-color, #dc2626) 16%, transparent);
      /* Danger TEXT, as opposed to --glass-danger's background wash.
         The theme's own destructive colour is not legible enough on
         glass at body size: 3.23:1 on Telegram light, and 2.96:1 on dark
         for clients that never set the variable and fall back to
         #dc2626 — against AA's 4.5:1. Mixing 60% of it with the theme's
         own text colour lifts every combination to 4.79:1 or better
         while keeping the hue unmistakably red. Same technique
         --glass-dim uses, and tests/test_miniapp_styles.py reads this
         percentage back out of the stylesheet as a hard gate. */
      --glass-danger-text: color-mix(in srgb, var(--tg-theme-destructive-text-color, #dc2626) 60%, var(--tg-theme-text-color, #000000));
      /* Secondary text. --tg-theme-hint-color measures 2.85:1 on
         Telegram light and 4.23:1 on Telegram dark against the page —
         it fails AA for body-sized text in light. 62% of the theme's
         own text colour measures 6.01:1 / 6.45:1 on glass instead.
         hint-color stays correct for BORDERS and large text; it was
         wrong for 0.78rem help copy. */
      --glass-dim:         color-mix(in srgb, var(--tg-theme-text-color, #000000) 62%, transparent);
      --glass-bloom-a:     color-mix(in srgb, var(--tg-theme-button-color, #2481cc) 8%, transparent);
      --glass-bloom-b:     color-mix(in srgb, var(--tg-theme-link-color,   #3390ec) 6%, transparent);
    }
  }

  /* Must be tested with BOTH the prefixed and unprefixed form: iOS
     15/16 WKWebView, which a large share of Telegram iOS users are on,
     supports only -webkit-backdrop-filter, and an unprefixed-only
     @supports test would wrongly send those clients down this fallback
     too. Source order matters: this comes AFTER the color-mix upgrade
     above, so a browser with color-mix but no backdrop-filter still
     lands on real theme-derived (opaque) surfaces here, not the plain
     rgba baseline. */
  @supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
    :root {
      --glass-alpha: 100%;
      --glass-alpha-raised: 100%;
      --glass-bg:        var(--tg-theme-secondary-bg-color, #f1f1f1);
      --glass-bg-raised: var(--tg-theme-section-bg-color, var(--tg-theme-bg-color, #ffffff));
    }
    .overlay { background: rgba(0, 0, 0, 0.55); }
  }

  /* iOS "Reduce Transparency" / Android "Remove animations and
     transparency". One block turns the whole system opaque, because
     every surface reads its alpha from these two tokens. */
  @media (prefers-reduced-transparency: reduce) {
    :root {
      --glass-alpha: 100%;
      --glass-alpha-raised: 100%;
    }
    .menu, .modal, #notice, .overlay {
      -webkit-backdrop-filter: none;
              backdrop-filter: none;
    }
    .overlay { background: rgba(0, 0, 0, 0.62); }
    /* `background: none`, not `display: none` — this file has exactly
       one `display: … !important` and it stays that way
       (test_no_id_rule_can_outrank_the_hidden_guard). */
    body::before { background: none; }
  }

  * { box-sizing: border-box; }
  /* The browser's own `[hidden] { display: none }` is a USER-AGENT rule, so
     any author `display:` beats it. Every element the script hides carries a
     class with an explicit display (.tabbar is flex, .badge is inline-block,
     .grid is grid), which silently defeated `el.hidden = true` — the tab bar
     stayed on sub-pages, the waiting badge never cleared, and the finished
     list never collapsed. One author-level rule with !important settles it
     for every element, present and future, instead of another per-selector
     patch each time someone notices. */
  [hidden] { display: none !important; }
  body {
    margin: 0;
    padding: 16px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--tg-theme-bg-color, #ffffff);
    color: var(--tg-theme-text-color, #000000);
  }
  /* The page is one flat colour, and backdrop-filter: blur() over a flat
     colour is a visual no-op — blurring a solid #ffffff returns solid
     #ffffff. Two very soft accent blooms give the glass something behind
     it, at the cost of one paint, once.

     Deliberately on body::before and NOT on body or html: an ancestor
     carrying filter / backdrop-filter / transform becomes the containing
     block for position: fixed descendants, which would silently
     reposition #notice (z-index 70) and .overlay (z-index 50) relative to
     body instead of the viewport — the exact "toast below the fold" bug
     the comment further down this file was written to fix. A fixed
     PSEUDO-element does not do that to its own parent, so body keeps
     nothing that would trap its fixed children.

     body must keep its own `background:` above: with html background-less,
     body's background is what paints the canvas, and this layer sits above
     it at z-index -1. */
  body::before {
    content: "";
    position: fixed;
    inset: -20vmax;
    z-index: -1;
    pointer-events: none;
    background:
      radial-gradient(38vmax 38vmax at 12% -4%, var(--glass-bloom-a), transparent 70%),
      radial-gradient(46vmax 46vmax at 96% 20%, var(--glass-bloom-b), transparent 72%);
  }

  /* ==========================================================
     GLASS SURFACES — the reusable system.
     .glass / .glass-btn / .glass-raised are the general-purpose classes
     any future control can opt into directly. The sixteen selectors this
     stylesheet already ships (.card, .kebab, .modal-btn, …) get the SAME
     declarations applied to their own existing selectors in the ADOPTION
     block near the end of this file instead of gaining a class here — that
     keeps this a zero-markup-churn change (no _app.py / _shell.py edit),
     while still leaving one designed vocabulary for the next surface that
     is added straight into the markup.
     ========================================================== */
  .glass {
    background-color: var(--glass-bg);
    border: 1px solid var(--glass-edge);
    border-radius: var(--r-md);
    box-shadow: var(--el-1);
    color: var(--tg-theme-text-color, #000000);
  }

  /* The press/hover wash is a background-IMAGE layer so it stacks on top
     of the translucent background-COLOR instead of replacing it. Every
     rule in this system therefore sets `background-color:`, never the
     `background:` shorthand, which would reset the image layer. */
  .glass-btn {
    background-color: var(--glass-bg);
    border: 1px solid var(--glass-edge);
    border-radius: var(--r-md);
    box-shadow: var(--el-1);
    color: var(--tg-theme-text-color, #000000);
    font: inherit;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    transition:
      background-color var(--glass-motion),
      border-color     var(--glass-motion),
      box-shadow       var(--glass-motion),
      transform        var(--glass-motion);
  }

  /* Hover only where a pointer exists — on a phone :hover sticks after a
     tap and leaves the last-tapped control lit. */
  @media (hover: hover) {
    .glass-btn:hover {
      background-image: linear-gradient(var(--glass-scrim-hover), var(--glass-scrim-hover));
      border-color: var(--glass-edge);
    }
  }

  .glass-btn:active,
  .glass-btn[aria-expanded="true"] {
    background-image: linear-gradient(var(--glass-scrim-press), var(--glass-scrim-press));
    box-shadow: var(--el-press);
    transform: translateY(1px);
  }

  /* The first focus indicator this stylesheet has ever had:
     grep -c ':focus' was 0 before this change, and .prefix-input actively
     removed the user-agent ring with no replacement. :focus-visible, so a
     tap never draws a ring but a keyboard / switch / Telegram-Desktop user
     always gets one. `outline` rather than `box-shadow` because `outline`
     is not clipped by an ancestor's `overflow: hidden` — .grp, .prefix-field,
     .diff-file and .menu all clip, and a box-shadow ring would vanish on
     exactly the controls that most need it. */
  .glass-btn:focus-visible,
  .field-input:focus-visible,
  .prefix-input:focus-visible,
  .card:focus-visible {
    outline: 2px solid var(--tg-theme-button-color, #2481cc);
    outline-offset: 2px;
  }

  .glass-btn[disabled],
  .glass-btn[aria-disabled="true"] {
    opacity: 0.45;
    box-shadow: none;
    transform: none;
    background-image: none;
    cursor: default;
  }

  .glass-btn.is-active,
  .glass.is-active {
    background-image: linear-gradient(var(--glass-accent), var(--glass-accent));
    border-color: var(--tg-theme-button-color, #2481cc);
  }

  /* The only tier of this system that blurs — see the ADOPTION block for
     why just four selectors (.menu, .modal, #notice, .overlay) use this
     and everything else stays flat. */
  .glass-raised {
    background-color: var(--glass-bg-raised);
    border: 1px solid var(--glass-edge);
    border-radius: var(--r-lg);
    box-shadow: var(--el-2);
    -webkit-backdrop-filter: blur(var(--glass-blur)) saturate(var(--glass-sat));
            backdrop-filter: blur(var(--glass-blur)) saturate(var(--glass-sat));
  }

  header { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
  h1 { font-size: 1.1rem; margin: 0 0 4px; }
  .muted { color: var(--tg-theme-hint-color, #888888); font-size: 0.85rem; }

  /* Top-level tab bar (design §2). Two tabs, Sessions is the landing one. */
  .tabbar { display: flex; gap: 8px; margin: 14px 0 4px; }
  .tabbar-btn {
    flex: 1;
    padding: 9px 10px;
    font: inherit;
    font-weight: 600;
    color: var(--tg-theme-hint-color, #888888);
    background: transparent;
    border: 0;
    border-bottom: 2px solid var(--tg-theme-hint-color, #e0e0e0);
    cursor: pointer;
  }
  .tabbar-btn.is-active {
    color: var(--tg-theme-text-color, #000000);
    border-bottom-color: var(--tg-theme-button-color, #2481cc);
  }
  /* Waiting count rides the tab so the state is visible without reading
     the grid — it is the only status that costs the operator time. */
  .badge {
    display: inline-block;
    min-width: 18px;
    margin-left: 6px;
    padding: 0 5px;
    font-size: 0.75rem;
    line-height: 18px;
    color: #ffffff;
    background: #dc2626;
    border-radius: 999px;
  }

  .totals { margin: 10px 0 2px; }

  /* Exactly two columns — a phone held in one hand, not a responsive
     many-column grid (design §2). */
  .grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    margin-top: 10px;
  }

  .card {
    border: 1px solid var(--tg-theme-hint-color, #e0e0e0);
    border-radius: 10px;
    padding: 12px 14px;
    cursor: pointer;
    min-height: 84px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 6px;
    overflow: hidden;
  }
  .card:active { opacity: 0.7; }
  /* The name is the thing the operator navigates by, so it is the
     primary element on the card. */
  .card-name {
    font-weight: 700;
    font-size: 1rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .card-foot { display: flex; justify-content: space-between; align-items: center; gap: 6px; }
  .card-age { font-size: 0.78rem; color: var(--tg-theme-hint-color, #888888); }
  /* Finished sessions stay reachable but must never dominate the grid. */
  .card-gone { opacity: 0.55; }

  /* Reads as an affordance, not a session. Always the first cell. */
  .card-new {
    border: 2px dashed var(--tg-theme-link-color, #2481cc);
    color: var(--tg-theme-link-color, #2481cc);
    align-items: center;
    justify-content: center;
    text-align: center;
    font-weight: 700;
  }
  .card-new .plus { font-size: 1.5rem; line-height: 1; }

  .gone-toggle {
    width: 100%;
    margin-top: 14px;
    padding: 8px;
    font: inherit;
    color: var(--tg-theme-hint-color, #888888);
    background: transparent;
    border: 1px solid var(--tg-theme-hint-color, #e0e0e0);
    border-radius: 8px;
    cursor: pointer;
  }

  /* Transient notices sit apart from #error so a self-clearing note can
     never wipe a fatal message that must stay on screen.

     A floating toast, NOT an in-flow banner: as an in-flow element it
     appeared and vanished 3.5s later, shoving the whole page down and
     then yanking it back up — so tapping "Kill" made the list jump under
     the finger just as you were reading the result. Fixed positioning
     takes it out of flow entirely, so nothing below it ever moves.

     z-index 70 puts it above the confirm dialog (60) and its backdrop
     (50): "Session killed." is the answer to an action taken IN that
     dialog, so it has to be readable while the dialog is still closing.

     Toggled by opacity rather than `display`, so it can transition, and
     so role="status" keeps announcing to screen readers instead of the
     element popping in and out of the a11y tree. */
  #notice {
    position: fixed;
    left: 50%;
    /* TOP, not bottom. A bottom-anchored fixed element is not reliably
       visible in a Telegram Mini App: the webview's layout viewport can
       extend BELOW the visible sheet when the app is not fully expanded,
       so the toast renders past the fold and the operator sees nothing at
       all — which is exactly what happened with `bottom: 16px`. Telegram
       exposes viewportStableHeight for this reason. The top edge is always
       on screen, whatever height the sheet is at. */
    top: calc(12px + env(safe-area-inset-top, 0px));
    transform: translateX(-50%) translateY(-8px);
    z-index: 70;
    max-width: min(92vw, 26rem);
    padding: 10px 14px;
    border-radius: 10px;
    background: var(--tg-theme-section-bg-color, #ffffff);
    color: var(--tg-theme-text-color, #000000);
    border: 1px solid var(--tg-theme-section-separator-color, #e0e0e0);
    box-shadow: 0 6px 24px rgba(0, 0, 0, 0.28);
    font-size: 0.9rem;
    text-align: center;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.18s ease, transform 0.18s ease;
  }
  #notice.is-visible {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }

  /* Toast card: an icon disc plus the message, on a coloured accent so the
     outcome reads before the words do. Literal characters throughout — a
     CSS `content:` escape in this same non-raw Python string was once
     mangled into the text "F480" on screen, so icons live in the DOM. */
  #notice {
    display: flex;
    align-items: center;
    gap: 10px;
    text-align: left;
    border-left: 4px solid var(--tg-theme-hint-color, #8e8e93);
  }
  .toast-icon {
    flex: 0 0 auto;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 0.8rem;
    font-weight: 700;
    line-height: 1;
    /* Hardcoded background paired with hardcoded text, deliberately. A
       theme-derived background here would be unreadable against #ffffff
       on any theme with a light accent — the exact fault
       test_no_theme_background_is_paired_with_hardcoded_white_text
       exists to catch, and which this rule tripped on first writing. */
    color: #ffffff;
    background: #8e8e93;
  }
  .toast-text { flex: 1 1 auto; }

  #notice.toast-ok { border-left-color: #16a34a; }
  #notice.toast-ok .toast-icon { background: #16a34a; }
  #notice.toast-err { border-left-color: var(--glass-danger-text); }
  #notice.toast-err .toast-icon { background: #dc2626; }
  #notice.toast-info {
    border-left-color: var(--tg-theme-link-color, #3390ec);
  }
  #notice.toast-info .toast-icon { background: #3390ec; }
  /* Nothing below it moves — the whole point — but it must also not sit
     under the sticky header it now overlaps. */
  #notice { pointer-events: none; }
  @media (prefers-reduced-motion: reduce) {
    #notice { transition: none; }
  }

  /* "Reset to default" (design §4) — a one-way door without it, so it
     gets its own always-visible control rather than living inside the
     collapsed sections below. */
  .session-settings-reset {
    display: block;
    width: 100%;
    margin-top: 14px;
    padding: 9px 10px;
    font: inherit;
    color: var(--tg-theme-link-color, #2481cc);
    background: transparent;
    border: 1px solid var(--tg-theme-link-color, #2481cc);
    border-radius: 8px;
    cursor: pointer;
  }
  .session-settings-reset[disabled] { opacity: 0.5; cursor: default; }

  /* Session page. A label/value grid rather than a comma-separated run,
     so the info line stays scannable on a narrow screen. */
  .facts { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; margin: 10px 0 0; }
  .facts dt { color: var(--tg-theme-hint-color, #888888); font-size: 0.85rem; }
  .facts dd {
    margin: 0;
    font-size: 0.85rem;
    overflow-wrap: anywhere;   /* a long cwd must wrap, not widen the page */
  }
  .waiting-note {
    margin-top: 10px;
    padding: 8px 10px;
    border-radius: 8px;
    background: rgba(220, 38, 38, 0.12);
    font-size: 0.9rem;
  }

  /* Session detail-page write actions (Stop/Kill/Resume/Delete). At most
     one row for busy/idle, at most two for gone — never a wall of
     buttons. Reuses .choice's block/full-width/padding/border-radius
     shape (same family of control as the settings rows above it), with
     an explicit min-height the way .choice-new already sets one. */
  /* The kebab rides the header row, pushed to the far edge. */
  #detail-header { display: flex; align-items: center; gap: 8px; }

  /* Occasional actions live behind a kebab, not on the page: the
     session page's content is the last message, and a rank of buttons
     under it read as the point of the screen. ⋮ is where Telegram puts
     the same idea, so it needs no explaining.

     Everything below leans on the theme variables Telegram publishes for
     exactly this (core.telegram.org/bots/webapps): section-bg for a
     surface that sits ABOVE the page rather than beside it,
     section-separator for hairlines, destructive-text for danger, and
     subtitle-text for secondary lines. Hardcoding a red here would look
     wrong in half the themes Telegram ships. */
  /* Positioning context for the menu, so the menu hangs off the BUTTON
     and follows it when the page scrolls.

     Deliberately no backdrop-filter (and none on `body` either): this
     element is `position: relative` with no z-index, so it creates no
     stacking context today and .menu's `z-index: 60` competes directly
     with .overlay's `z-index: 50` in the ROOT stacking context. Giving
     .kebab-wrap a stacking context (which backdrop-filter, like filter
     and transform, always does) would trap .menu inside it at the
     wrapper's own z-index: auto — the menu would render BEHIND its own
     backdrop, a silent total break of the (open) session menu. */
  .kebab-wrap { position: relative; flex: 0 0 auto; margin-left: auto; display: flex; }
  .kebab {
    flex: 0 0 auto;
    display: flex;
    align-items: center;
    justify-content: center;
    min-width: 40px;
    min-height: 40px;
    padding: 0;
    font: inherit;
    font-size: 1.35rem;
    font-weight: 700;
    line-height: 1;
    letter-spacing: 0.06em;
    /* Full-strength text colour on a filled pill: at hint-grey it read
       as decoration rather than a control. */
    color: var(--tg-theme-text-color, #000000);
    background: var(--tg-theme-secondary-bg-color, rgba(127, 127, 127, 0.12));
    border: 0;
    border-radius: 999px;
    cursor: pointer;
  }
  .kebab:active,
  .kebab[aria-expanded="true"] {
    background: var(--tg-theme-section-separator-color, rgba(127, 127, 127, 0.28));
  }

  /* Backdrop for both layers. Dismisses on tap; the layers stop the
     event so a tap inside never closes them. */
  .overlay {
    position: fixed;
    inset: 0;
    z-index: 50;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
    background: rgba(0, 0, 0, 0.4);
  }

  /* Anchored under the kebab it came from, like Telegram's own context
     menus: one surface, items edge to edge, hairline between them, an
     icon leading each row. */
  .menu {
    position: absolute;
    top: calc(100% + 8px);
    right: 0;
    left: auto;
    /* Above the backdrop (z-index 50), which is now behind it rather
       than its parent. */
    z-index: 60;
    min-width: 208px;
    max-width: min(300px, calc(100vw - 32px));
    padding: 0;
    overflow: hidden;
    background: var(--tg-theme-section-bg-color, var(--tg-theme-bg-color, #ffffff));
    border-radius: 14px;
    box-shadow: 0 10px 32px rgba(0, 0, 0, 0.3), 0 2px 8px rgba(0, 0, 0, 0.16);
  }
  .menu-item {
    display: flex;
    align-items: center;
    gap: 12px;
    width: 100%;
    min-height: 48px;
    padding: 12px 16px;
    font: inherit;
    font-size: 1rem;
    font-weight: 500;
    text-align: left;
    color: var(--tg-theme-text-color, #000000);
    background: transparent;
    border: 0;
    border-radius: 0;
    cursor: pointer;
  }
  /* Hairline between rows, never above the first — the same treatment
     Telegram uses inside a grouped section. */
  .menu-item + .menu-item,
  .menu-note + .menu-item {
    box-shadow: inset 0 1px 0 var(--tg-theme-section-separator-color, rgba(127, 127, 127, 0.22));
  }
  .menu-item:active { background: var(--tg-theme-secondary-bg-color, rgba(127, 127, 127, 0.12)); }
  .menu-item[disabled] { opacity: 0.4; cursor: default; }
  /* An icon per action, via ::before so the button's textContent stays
     exactly the action name. */
  /* Literal emoji, NOT CSS unicode escapes. This CSS lives in a plain
     (non-raw) Python triple-quoted string, so a backslash-1F480 style
     escape is consumed by PYTHON as an octal escape before CSS ever
     sees it, shipping U+0001 plus the visible text "F480". The file
     and the page are both UTF-8, so the characters themselves are
     simplest and cannot be misread by either layer. (This very
     comment originally demonstrated the bug it warns about.) */
  .menu-item::before { font-size: 1.05rem; line-height: 1; }
  .menu-item.act-stop::before { content: "🛑"; }
  .menu-item.act-kill::before { content: "💀"; }
  .menu-item.act-resume::before { content: "▶️"; }
  .menu-item.act-delete::before { content: "🗑️"; }
  .menu-item.act-clearqueue::before { content: "🧹"; }
  .menu-item.act-compact::before { content: "📦"; }
  .menu-item.act-perms::before { content: "🔐"; }
  .menu-item.act-restart::before { content: "🔁"; }
  .menu-item.act-rename::before { content: "✏️"; }
  .menu-item.is-danger { color: var(--glass-danger-text); }
  .menu-note {
    padding: 0 16px 12px;
    font-size: 0.82rem;
    line-height: 1.4;
    color: var(--tg-theme-subtitle-text-color, var(--tg-theme-hint-color, #888888));
  }
  /* Separates the "session control" group from the "destructive" one
     (design.md: menu order and grouping) — the same hairline treatment
     .menu-item + .menu-item already uses, with a little extra vertical
     space so it reads as a section break rather than just another row
     boundary. Non-interactive: no padding-left icon gutter, no hover
     state, nothing to tap. */
  .menu-divider {
    height: 1px;
    margin: 6px 0;
    box-shadow: inset 0 1px 0 var(--tg-theme-section-separator-color, rgba(127, 127, 127, 0.22));
  }

  /* Centred, so the confirm button is nowhere near the menu row that
     opened it — a double-tap on "Delete" must not land on "Delete". */
  .modal {
    width: 100%;
    max-width: 320px;
    padding: 20px;
    text-align: center;
    background: var(--tg-theme-section-bg-color, var(--tg-theme-bg-color, #ffffff));
    border-radius: 14px;
    box-shadow: 0 10px 34px rgba(0, 0, 0, 0.32);
  }
  .modal-title { margin: 0 0 8px; font-size: 1.05rem; font-weight: 600; }
  .modal-body {
    margin: 0 0 18px;
    font-size: 0.92rem;
    line-height: 1.45;
    color: var(--tg-theme-subtitle-text-color, var(--tg-theme-hint-color, #888888));
  }
  /* Rename's field, inside the confirm modal (design.md "Rename input
     UX") — reuses .field-input's own look, just left-aligned against
     the otherwise centred modal text since a text field reads oddly
     centred. */
  #confirm-rename-input { margin: 0 0 6px; text-align: left; }
  #confirm-rename-error { margin: 0 0 14px; text-align: left; }
  .modal-actions { display: flex; gap: 10px; }
  .modal-btn {
    flex: 1 1 0;
    min-height: 46px;
    padding: 12px;
    font: inherit;
    font-weight: 600;
    color: var(--tg-theme-link-color, #2481cc);
    background: var(--tg-theme-secondary-bg-color, rgba(127, 127, 127, 0.10));
    border: 0;
    border-radius: 10px;
    cursor: pointer;
  }
  /* Danger as the theme's own destructive colour on a tinted ground —
     never white-on-accent, per .choice.is-active's note. */
  .modal-btn.is-danger {
    color: var(--glass-danger-text);
    background: rgba(220, 38, 38, 0.12);
    font-weight: 700;
  }
  .modal-btn[disabled] { opacity: 0.5; cursor: default; }

  .sect-title { font-size: 0.95rem; margin: 20px 0 6px; }
  .preview {
    white-space: pre-wrap;
    line-height: 1.45;
    padding: 10px 12px;
    border-left: 3px solid var(--tg-theme-hint-color, #e0e0e0);
    background: var(--tg-theme-secondary-bg-color, rgba(127, 127, 127, 0.08));
    border-radius: 0 8px 8px 0;
  }
  .preview.is-empty { color: var(--tg-theme-hint-color, #888888); font-style: italic; }
  .sect-toggle {
    display: block;
    width: 100%;
    text-align: left;
    margin-top: 18px;
    padding: 9px 10px;
    font: inherit;
    font-weight: 600;
    color: var(--tg-theme-text-color, #000000);
    background: transparent;
    border: 1px solid var(--tg-theme-hint-color, #e0e0e0);
    border-radius: 8px;
    cursor: pointer;
  }
  .sect-note { margin-top: 10px; font-size: 0.9rem; color: var(--tg-theme-hint-color, #888888); }

  /* New-session form. Name and model are visible; everything else is
     behind Advanced so the common case is two fields, not a wall. */
  .field-label { display: block; font-weight: 600; margin: 16px 0 6px; }
  .field-input {
    width: 100%;
    padding: 10px 12px;
    font: inherit;
    color: var(--tg-theme-text-color, #000000);
    background: var(--tg-theme-secondary-bg-color, rgba(127, 127, 127, 0.08));
    border: 1px solid var(--tg-theme-hint-color, #e0e0e0);
    border-radius: 8px;
  }
  .field-error { margin-top: 6px; font-size: 0.85rem; color: var(--glass-danger-text); }
  /* Settings groups: collapsed to heading + current value, expanding to
     the choices. Four groups x five options all on screen at once is what
     made the page feel like a wall of pills. */
  .grp {
    border: 1px solid var(--tg-theme-hint-color, #e0e0e0);
    border-radius: 12px;
    margin-top: 10px;
    overflow: hidden;
  }
  .grp-head {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    padding: 12px 14px;
    font: inherit;
    text-align: left;
    color: var(--tg-theme-text-color, #000000);
    background: transparent;
    border: 0;
    cursor: pointer;
  }
  /* Hierarchy: the heading is the label, the value is the answer, the
     caret is the affordance — three distinct weights, not three equals. */
  .grp-title { font-weight: 600; flex: 0 0 auto; }
  .grp-value {
    flex: 1 1 auto;
    text-align: right;
    color: var(--tg-theme-hint-color, #888888);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .grp-caret { flex: 0 0 auto; color: var(--tg-theme-hint-color, #888888); }
  .grp-body {
    border-top: 1px solid var(--tg-theme-hint-color, #e0e0e0);
    padding: 6px;
  }
  .choice {
    display: block;
    width: 100%;
    text-align: left;
    padding: 10px 12px;
    margin: 4px 0;
    font: inherit;
    color: var(--tg-theme-text-color, #000000);
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    cursor: pointer;
  }
  /* Unmistakable rather than a subtle tint: a filled bar with a check. */
  /* Selection never inverts the text. Painting the accent behind
     white text depends on the accent being dark, and a theme may pick a
     pale blue — which is exactly what the operator kept seeing. Text
     stays the theme's own on the theme's own background (readable by
     construction); the accent shows as a left bar, a faint tint and a
     check. colour-mix keeps the tint proportional to whatever accent the
     theme supplies, with a plain rgba fallback for older webviews. */
  .choice.is-active {
    background: rgba(36, 129, 204, 0.12);
    background: color-mix(in srgb, var(--tg-theme-button-color, #2481cc) 14%, transparent);
    color: var(--tg-theme-text-color, #000000);
    border-color: var(--tg-theme-button-color, #2481cc);
    box-shadow: inset 3px 0 0 var(--tg-theme-button-color, #2481cc);
    font-weight: 600;
  }
  .choice.is-active .choice-main::after {
    content: "  ✓";
    font-weight: 700;
    color: var(--tg-theme-button-color, #2481cc);
  }
  .choice.is-active .choice-help { color: currentColor; opacity: 0.75; }
  .choice[disabled] { opacity: 0.45; cursor: default; }
  .choice-main { display: block; font-weight: 600; }
  .choice-help { display: block; font-size: 0.8rem; margin-top: 2px; opacity: 0.75; }
  /* Legible pill, not a cramped suffix on the label. */
  .tag {
    display: inline-block;
    margin-left: 8px;
    padding: 1px 7px;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    vertical-align: 1px;
    color: var(--tg-theme-hint-color, #888888);
    border: 1px solid currentColor;
    border-radius: 999px;
  }
  .choice.is-active .tag { color: var(--tg-theme-hint-color, #888888); }

  /* "Make something new" rows — the dashed ＋ of the New session card, so
     the same intent looks the same wherever it appears (Telegram's own
     guidance: mimic components that already exist). An action, not a
     value: it never takes the selected-choice check. */
  .choice-new {
    min-height: 44px;
    border: 1px dashed var(--tg-theme-link-color, #2481cc);
    color: var(--tg-theme-link-color, #2481cc);
  }
  .choice-new .choice-main::before { content: "＋ "; font-weight: 700; }
  .choice-new.is-active {
    border-style: solid;
    box-shadow: none;
    background: transparent;
    color: var(--tg-theme-link-color, #2481cc);
  }
  .choice-new.is-active .choice-main::after { content: ""; }

  /* A conditional reveal: exactly one input, directly beneath the row
     that revealed it, tied to it by an indent and a rule. GOV.UK's
     research is explicit that reveals test well when they hold a single
     input and nothing more — a panel of fields belongs in its own step. */
  .reveal {
    margin: 2px 4px 8px 14px;
    padding: 8px 0 2px 12px;
    border-left: 2px solid var(--tg-theme-button-color, #2481cc);
  }
  .reveal-label {
    display: block;
    margin-bottom: 6px;
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.02em;
    text-transform: uppercase;
    color: var(--tg-theme-hint-color, #888888);
  }
  .reveal-note {
    margin-top: 6px;
    font-size: 0.82rem;
    line-height: 1.4;
    color: var(--tg-theme-hint-color, #888888);
  }
  .reveal-note.is-error { color: var(--glass-danger-text); }

  /* Material's prefix pattern: the parent path is "input given in
     advance", shown inside the field, so where the folder lands needs no
     caption above it and cannot drift out of sync with the picker. */
  .prefix-field {
    display: flex;
    align-items: stretch;
    background: var(--tg-theme-secondary-bg-color, rgba(127, 127, 127, 0.08));
    border: 1px solid var(--tg-theme-hint-color, #e0e0e0);
    border-radius: 8px;
    overflow: hidden;
  }
  .prefix-text {
    flex: 0 1 auto;
    align-self: center;
    max-width: 48%;
    padding-left: 10px;
    font-size: 0.85rem;
    white-space: nowrap;
    color: var(--tg-theme-hint-color, #888888);
  }
  .prefix-input {
    flex: 1 1 auto;
    min-width: 0;
    padding: 12px 8px;
    font: inherit;
    color: var(--tg-theme-text-color, #000000);
    background: transparent;
    border: 0;
    outline: none;
  }
  .prefix-go {
    flex: 0 0 auto;
    min-width: 44px;
    padding: 0 12px;
    font: inherit;
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--tg-theme-button-text-color, #ffffff);
    background: var(--tg-theme-button-color, #2481cc);
    border: 0;
    cursor: pointer;
  }
  .prefix-go[disabled] { opacity: 0.45; cursor: default; }
  /* Launching a process should never be a surprise — say what will
     happen, in words, directly above the button that does it. */
  .new-summary {
    margin: 20px 0 10px;
    padding: 10px 12px;
    border-radius: 8px;
    background: var(--tg-theme-secondary-bg-color, rgba(127, 127, 127, 0.08));
    font-size: 0.9rem;
    line-height: 1.45;
  }
  .primary {
    width: 100%;
    padding: 12px;
    font: inherit;
    font-weight: 700;
    color: var(--tg-theme-button-text-color, #ffffff);
    background: var(--tg-theme-button-color, #2481cc);
    border: 0;
    border-radius: 10px;
    cursor: pointer;
  }
  .primary[disabled] { opacity: 0.5; cursor: default; }

  /* Loading placeholders. A blank panel that fills in a moment later
     reads as broken; a shaped skeleton reads as "coming". */
  .skel {
    background: var(--tg-theme-secondary-bg-color, rgba(127, 127, 127, 0.10));
    border-radius: 8px;
    animation: skel-pulse 1.2s ease-in-out infinite;
  }
  .skel-line { height: 12px; margin: 8px 0; }
  .skel-line.w40 { width: 40%; }
  .skel-line.w70 { width: 70%; }
  .skel-line.w90 { width: 90%; }
  .skel-row { height: 46px; margin-top: 10px; }
  @keyframes skel-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.45; }
  }
  @media (prefers-reduced-motion: reduce) {
    .skel { animation: none; }
    .status-waiting { animation: none; }
  }

  .empty, .placeholder { margin-top: 18px; line-height: 1.5; }
  .empty p, .placeholder p { margin: 0 0 6px; }
  .row { display: flex; justify-content: space-between; align-items: center; padding: 4px 0; gap: 8px; }
  .row .label { font-weight: 600; }

  .status { text-transform: capitalize; font-weight: 600; }
  .status-busy { color: #d97706; }
  .status-idle { color: #16a34a; }
  .status-gone { color: #9ca3af; }
  .status-unknown { color: var(--tg-theme-hint-color, #888888); }
  .status-waiting {
    color: #ffffff;
    background: #dc2626;
    padding: 1px 8px;
    border-radius: 999px;
    animation: pulse-waiting 1.4s ease-in-out infinite;
  }
  @keyframes pulse-waiting {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.55; }
  }

  #error {
    color: var(--glass-danger-text);
    margin-top: 12px;
    padding: 8px 10px;
    border: 1px solid var(--glass-danger-text);
    border-radius: 8px;
    display: none;
  }

  .conn {
    font-size: 0.75rem;
    padding: 2px 8px;
    border-radius: 999px;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .conn-live { color: #16a34a; }
  .conn-reconnecting { color: #d97706; }
  .conn-offline { color: var(--glass-danger-text); }


  .detail-label { font-weight: 700; font-size: 1.05rem; margin-right: 8px; }


  .timeline-row {
    padding: 6px 0;
    border-bottom: 1px solid var(--tg-theme-hint-color, #f0f0f0);
    font-size: 0.9rem;
  }
  .timeline-row:last-child { border-bottom: none; }
  .timeline-commentary { white-space: pre-wrap; }
  .timeline-tool { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  .timeline-tool.state-done::before { content: "✅ "; }
  .timeline-tool.state-failed::before { content: "❌ "; }
  .timeline-tool.state-running::before { content: "⏳ "; }

  .diff-file {
    border: 1px solid var(--tg-theme-hint-color, #e0e0e0);
    border-radius: 8px;
    margin-top: 10px;
    overflow: hidden;
  }
  .diff-file-header {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    padding: 8px 10px;
    cursor: pointer;
    background: var(--tg-theme-secondary-bg-color, rgba(127, 127, 127, 0.08));
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.85rem;
  }
  .diff-line {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.8rem;
    white-space: pre;
    overflow-x: auto;
    padding: 0 10px;
  }
  .diff-add { background: rgba(22, 163, 74, 0.14); color: #16a34a; }
  .diff-del { background: rgba(220, 38, 38, 0.14); color: var(--glass-danger-text); }
  .diff-hunk { color: var(--tg-theme-link-color, #2563eb); background: rgba(37, 99, 235, 0.08); }
  .diff-context { color: var(--tg-theme-text-color, #000000); }
  .diff-binary, .diff-truncated {
    padding: 8px 10px;
    color: var(--tg-theme-hint-color, #888888);
    font-size: 0.85rem;
  }

  /* ==========================================================
     GLASS ADOPTION
     Must stay at the END of this stylesheet: every rule below is the
     SAME specificity as the rule it supersedes, so it is source order
     alone that makes it win. Applying the glass system this way — to
     the selectors that already exist, rather than adding a class at
     each of the ~30 `className =` sites in _app.py — is what keeps this
     a CSS-only change: zero markup or JS edits, and zero chances to
     miss one of those sites.
     ========================================================== */

  /* tier 1: flat glass, no blur. See "why only four elements blur"
     below tier 2 — a 24-session grid that scrolls, plus two elements
     that animate forever (.status-waiting, .skel), make a per-card
     backdrop-filter a guaranteed jank source for near-zero visual
     payoff on an already-smooth ambient gradient. */
  .card,
  .gone-toggle,
  .sect-toggle,
  .session-settings-reset,
  .grp,
  .choice,
  .modal-btn,
  .kebab,
  .field-input,
  .prefix-field,
  .new-summary,
  .preview,
  .diff-file,
  .diff-file-header,
  .waiting-note,
  #error {
    background-color: var(--glass-bg);
    border: 1px solid var(--glass-edge);
    box-shadow: var(--el-1);
    transition:
      background-color var(--glass-motion),
      border-color     var(--glass-motion),
      box-shadow       var(--glass-motion),
      transform        var(--glass-motion);
  }

  /* one radius scale, replacing seven ad-hoc values */
  .choice, .tag                                { border-radius: var(--r-sm); }
  .card, .gone-toggle, .sect-toggle,
  .session-settings-reset, .grp, .modal-btn,
  .field-input, .prefix-field, .new-summary,
  .diff-file, .waiting-note, #error            { border-radius: var(--r-md); }
  .menu, .modal, #notice                       { border-radius: var(--r-lg); }
  .kebab, .badge, .conn, .status-waiting        { border-radius: var(--r-pill); }
  /* the only asymmetric radius in the file: .preview has a 3px quote bar
     on the left (border-left, above), so its right corners round and its
     left corners stay square. */
  .preview                                     { border-radius: 0 var(--r-md) var(--r-md) 0; }

  /* press feedback for the controls that had none before this change */
  .card:active,
  .gone-toggle:active,
  .sect-toggle:active,
  .session-settings-reset:active,
  .choice:not([disabled]):active,
  .modal-btn:not([disabled]):active,
  .primary:not([disabled]):active,
  .prefix-go:not([disabled]):active,
  .diff-file-header:active,
  .tabbar-btn:active,
  .kebab:active,
  .kebab[aria-expanded="true"] {
    background-image: linear-gradient(var(--glass-scrim-press), var(--glass-scrim-press));
    box-shadow: var(--el-press);
    transform: translateY(1px);
    /* kills the earlier `.card:active { opacity: 0.7 }` — fading an
       element fades its border and shadow too, and creates a stacking
       context that would fight backdrop-filter elsewhere on the page. */
    opacity: 1;
  }

  @media (hover: hover) {
    .card:hover, .gone-toggle:hover, .sect-toggle:hover,
    .session-settings-reset:hover, .choice:not([disabled]):hover,
    .modal-btn:not([disabled]):hover, .menu-item:not([disabled]):hover,
    .diff-file-header:hover, .kebab:hover {
      background-image: linear-gradient(var(--glass-scrim-hover), var(--glass-scrim-hover));
    }
  }

  /* one disabled alpha, replacing four different values (0.40 / 0.45 /
     0.50 / 0.55) that all meant the same thing */
  .choice[disabled], .menu-item[disabled], .modal-btn[disabled],
  .primary[disabled], .prefix-go[disabled],
  .session-settings-reset[disabled] {
    opacity: 0.45;
    box-shadow: none;
    background-image: none;
    transform: none;
    cursor: default;
  }

  /* secondary text: --tg-theme-hint-color fails AA at these sizes in
     both default themes (2.85:1 light / 4.23:1 dark). --glass-dim is
     the theme's own text colour at 62%, which clears AA in both. */
  .muted, .card-age, .grp-value, .grp-caret, .menu-note,
  .modal-body, .choice-help, .reveal-note,
  .diff-binary, .diff-truncated, .facts dt, .sect-note,
  .preview.is-empty, .prefix-text, .reveal-label {
    color: var(--glass-dim);
  }

  /* internal dividers stay hairline — they are decorative, the rows are
     identified by their own text and tap target, not by the rule
     between them. */
  .menu-item + .menu-item,
  .menu-note + .menu-item,
  .menu-divider          { box-shadow: inset 0 1px 0 var(--glass-hairline); }
  .grp-body              { border-top: 1px solid var(--glass-hairline); }
  .timeline-row          { border-bottom: 1px solid var(--glass-hairline); }

  /* selected state, unified — one tint recipe instead of two */
  .choice.is-active {
    background-color: var(--glass-bg);
    background-image: linear-gradient(var(--glass-accent), var(--glass-accent));
    border-color: var(--tg-theme-button-color, #2481cc);
  }

  /* "create new" affordance, now the same stroke weight everywhere
     (was 2px on .card-new, 1px on .choice-new, despite a comment
     claiming they already matched) */
  .card-new, .choice-new {
    background-color: transparent;
    border: 1.5px dashed var(--tg-theme-link-color, #2481cc);
    box-shadow: none;
  }

  /* the one solid control in the app stays solid: it is the single
     high-emphasis action, and Telegram guarantees the button-color /
     button-text-color pair is readable. It takes the glass SHAPE
     (elevation, larger text) but not the glass fill. 1.2rem bold =
     19.2px, which is WCAG "large text" (>=18.66px bold) — so the
     4.13:1 (light) / 3.72:1 (dark) of white-on-accent is judged
     against 3:1 and passes; at the previous 1rem it was judged against
     4.5:1 and failed in both themes. */
  .primary, .prefix-go {
    box-shadow: var(--el-1);
    font-size: 1.2rem;
  }

  /* the danger wash, theme-derived instead of a hardcoded red */
  .modal-btn.is-danger, .waiting-note {
    background-image: linear-gradient(var(--glass-danger), var(--glass-danger));
    border-color: var(--glass-danger-text);
  }
  #error, .field-error, .reveal-note.is-error {
    color: var(--glass-danger-text);
  }

  /* the two classes the markup renders with no CSS rule at all:
     #panel-diff / #panel-timeline (_shell.py) and .diff-body
     (_app.py). */
  .panel { margin-top: 10px; }
  .diff-body { border-top: 1px solid var(--glass-hairline); }

  /* tier 2: the only other blurred surfaces. Bounded at <=2 on screen
     at once, and the page is not scrolling while either is open — the
     conditions under which backdrop-filter is cheap. */
  .menu, .modal {
    background-color: var(--glass-bg-raised);
    border: 1px solid var(--glass-edge);
    box-shadow: var(--el-2);
    -webkit-backdrop-filter: blur(var(--glass-blur)) saturate(var(--glass-sat));
            backdrop-filter: blur(var(--glass-blur)) saturate(var(--glass-sat));
  }

  /* tier 3: the scrim. One full-viewport blur, only while a layer is
     open, at a smaller radius than tier 2 because it covers the whole
     screen. Safe here specifically because .overlay is not an ancestor
     of .menu (which hangs off .kebab-wrap instead, a sibling structure —
     see .kebab-wrap's own comment above for the placement that WOULD
     break the menu). .modal IS inside .overlay, but .modal has no
     descendant with its own fixed/absolute z-index, so nothing is
     trapped there either. */
  .overlay {
    -webkit-backdrop-filter: blur(var(--glass-blur-scrim)) saturate(120%);
            backdrop-filter: blur(var(--glass-blur-scrim)) saturate(120%);
  }

  /* Glass on the toast — PAINT ONLY, appended (never prepended) after
     every rule already targeting #notice above. The very first #notice
     block still owns position / top / transform / z-index / opacity /
     transition / pointer-events untouched, which is what
     test_the_notice_is_a_floating_toast_not_an_in_flow_banner in
     tests/test_miniapp_js_smoke.py pins by slicing from the FIRST
     `#notice {` to the first matching `}`. Nothing here may move
     earlier in the file. */
  #notice {
    background-color: var(--glass-bg-raised);
    /* NOT `border-color:` — that shorthand repaints border-LEFT too and
       would wipe the 4px accent bar set earlier in this file. The
       .toast-ok / .toast-err / .toast-info rules are more specific
       (0,1,1,0 via the .toast-* class) and would survive a shorthand
       here regardless, but the neutral default border-left would not. */
    border-top-color: var(--glass-edge);
    border-right-color: var(--glass-edge);
    border-bottom-color: var(--glass-edge);
    box-shadow: var(--el-2);
    -webkit-backdrop-filter: blur(var(--glass-blur)) saturate(var(--glass-sat));
            backdrop-filter: blur(var(--glass-blur)) saturate(var(--glass-sat));
  }
  /* The coloured discs keep their hardcoded fill+glyph pair — that
     pairing is deliberate and enforced by
     test_no_theme_background_is_paired_with_hardcoded_white_text. They
     gain only a ring: on a translucent ground the disc's own boundary
     drops below 3:1 in one theme each (measured: #dc2626 on dark glass
     is 2.91:1, #16a34a / #3390ec on light glass are 2.96:1 / 2.98:1).
     The ring restores each to >=3.4:1 without touching the disc's
     colour. */
  .toast-icon { box-shadow: 0 0 0 1px var(--glass-edge); }

  /* backdrop-filter is not motion and carries no vestibular risk, so it
     is deliberately NOT disabled here — prefers-reduced-transparency
     (near the top of this file) is the channel for opting out of it.
     This block only flattens the transitions and press displacements
     the glass system itself introduced, extending the two
     prefers-reduced-motion blocks already in this file rather than
     replacing them. */
  @media (prefers-reduced-motion: reduce) {
    .glass-btn, .card, .gone-toggle, .sect-toggle, .session-settings-reset,
    .grp, .choice, .modal-btn, .kebab, .field-input, .prefix-field,
    .new-summary, .preview, .diff-file, .diff-file-header, .waiting-note,
    .primary, .prefix-go, .tabbar-btn, .menu-item, #error {
      transition: none;
    }
    .glass-btn:active, .card:active, .gone-toggle:active, .sect-toggle:active,
    .session-settings-reset:active, .choice:active, .modal-btn:active,
    .primary:active, .prefix-go:active, .diff-file-header:active,
    .tabbar-btn:active, .kebab:active, .kebab[aria-expanded="true"] {
      transform: none;
    }
    /* The toast's slide, flattened WITHOUT losing the centring: its
       `transform` carries both translateX(-50%) (centring) and the
       translateY animation. Setting `transform: none` here would throw
       the toast to the left edge of the screen, so the centring is
       restated rather than dropped. */
    #notice,
    #notice.is-visible { transform: translateX(-50%); }
  }
"""

__all__ = ["CSS"]
