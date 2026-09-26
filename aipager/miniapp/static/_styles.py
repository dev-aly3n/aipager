"""The Mini App stylesheet: "Lanterns" (roadmap 8.44).

Every session reads as a light source. A session that needs you is a
beacon (amber, a slow swell), a working one a lit lantern (the accent
colour, a soft breath), a resting one a steady lantern, and a finished one
an unlit lantern on a shelf. The context ring is the lantern's fuel gauge.

Flat Telegram-native surfaces: the canvas is Telegram's `secondary_bg`,
cards sit on `section_bg` with a hairline, and every colour derives from
the `--tg-theme-*` variables the SDK writes on <html>. Only the amber pair
is fixed (Telegram has no warning colour), picked by `html[data-scheme]`,
which the page mirrors from `WebApp.colorScheme`. There is deliberately no
`prefers-color-scheme` query: Telegram's theme always wins.

A NON-raw Python string. Never write a backslash in it: Python eats CSS
escapes (a `content:` escape once shipped as the literal text "F480").
Icons are inline SVG in the page, never CSS `content:` glyphs.
"""

from __future__ import annotations

CSS = """
  /* ---- tokens --------------------------------------------------------
     Type 12/14/16/20/28, a 4-pt spacing scale, four radii, three
     durations and one easing. Colours derive from Telegram's theme. */
  :root {
    --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    --mono: ui-monospace, SFMono-Regular, Menlo, monospace;
    --fs-cap: 12px; --lh-cap: 16px;
    --fs-sm: 14px; --lh-sm: 20px;
    --fs-body: 16px; --lh-body: 22px;
    --fs-title: 20px; --lh-title: 26px;
    --fs-display: 28px; --lh-display: 34px;
    --s1: 4px; --s2: 8px; --s3: 12px; --s4: 16px; --s5: 24px; --s6: 32px;
    --r-chip: 10px; --r-ctl: 14px; --r-card: 20px; --r-pill: 999px;
    --t-fast: 120ms; --t-base: 200ms; --t-enter: 240ms;
    --ease: cubic-bezier(.2, .8, .2, 1);
    --shadow-sheet: 0 16px 48px rgba(0, 0, 0, 0.22), 0 2px 8px rgba(0, 0, 0, 0.10);
    --safe-top: max(env(safe-area-inset-top, 0px), calc(var(--tg-safe-area-inset-top, 0px) + var(--tg-content-safe-area-inset-top, 0px)));
    --safe-bottom: max(env(safe-area-inset-bottom, 0px), calc(var(--tg-safe-area-inset-bottom, 0px) + var(--tg-content-safe-area-inset-bottom, 0px)));

    --canvas: var(--tg-theme-secondary-bg-color, #f1f1f1);
    --surface: var(--tg-theme-section-bg-color, var(--tg-theme-bg-color, #ffffff));
    --ink: var(--tg-theme-text-color, #000000);
    --ink-2: color-mix(in srgb, var(--ink) 72%, var(--surface));
    --ink-3: color-mix(in srgb, var(--ink) 58%, var(--surface));
    --line: var(--tg-theme-section-separator-color, color-mix(in srgb, var(--ink) 12%, var(--surface)));
    --fill: color-mix(in srgb, var(--ink) 6%, var(--surface));
    --track: color-mix(in srgb, var(--ink) 8%, var(--canvas));
    --accent: var(--tg-theme-button-color, #2481cc);
    --on-accent: var(--tg-theme-button-text-color, #ffffff);
    --lamp-work: var(--accent);
    --lamp-work-soft: color-mix(in srgb, var(--accent) 16%, var(--surface));
    --lamp-need: #cc7000;
    --need-ink: #9a4a00;
    --need-soft: color-mix(in srgb, var(--lamp-need) 14%, var(--surface));
    --lamp-rest: var(--ink-3);
    --lamp-out: color-mix(in srgb, var(--ink) 30%, var(--surface));
    --danger-ink: color-mix(in srgb, var(--tg-theme-destructive-text-color, #dc2626) 60%, var(--ink));
    --danger-soft: color-mix(in srgb, var(--tg-theme-destructive-text-color, #dc2626) 12%, var(--surface));
    --scrim: rgba(0, 0, 0, 0.45);
  }
  /* The amber pair for a dark Telegram theme. */
  html[data-scheme="dark"] {
    --lamp-need: #ffb347;
    --need-ink: #ffb347;
    --scrim: rgba(0, 0, 0, 0.6);
    color-scheme: dark;
  }
  html[data-scheme="light"] { color-scheme: light; }
  /* A WebView without color-mix loses the hierarchy, never legibility. */
  @supports not (color: color-mix(in srgb, red 50%, blue)) {
    :root {
      --ink-2: var(--ink);
      --ink-3: var(--ink);
      --line: var(--tg-theme-section-separator-color, #d9d9d9);
      --fill: var(--tg-theme-secondary-bg-color, #f1f1f1);
      --track: var(--tg-theme-section-separator-color, #d9d9d9);
      --lamp-work-soft: var(--surface);
      --need-soft: var(--surface);
      --lamp-out: var(--tg-theme-hint-color, #999999);
      --danger-ink: var(--ink);
      --danger-soft: var(--surface);
    }
  }

  /* ---- base ------------------------------------------------------------ */
  * { box-sizing: border-box; }
  [hidden] { display: none !important; }
  html {
    background: var(--canvas);
    -webkit-text-size-adjust: 100%;
  }
  body {
    margin: 0;
    min-height: 100vh;
    padding: 0 max(var(--s4), env(safe-area-inset-right, 0px)) calc(var(--s6) + var(--safe-bottom)) max(var(--s4), env(safe-area-inset-left, 0px));
    background: var(--canvas);
    color: var(--ink);
    font: 400 var(--fs-body)/var(--lh-body) var(--font);
    -webkit-font-smoothing: antialiased;
    -webkit-tap-highlight-color: transparent;
  }
  button {
    margin: 0;
    padding: 0;
    border: 0;
    background: none;
    color: inherit;
    font: inherit;
    text-align: inherit;
    cursor: pointer;
    -webkit-appearance: none;
    appearance: none;
  }
  button:disabled { cursor: default; }
  :focus:not(:focus-visible) { outline: none; }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .sprite { position: absolute; width: 0; height: 0; overflow: hidden; }
  .ic {
    flex: 0 0 auto;
    width: 20px;
    height: 20px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.75;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .num, .tile-meta, .tile-age, .beacon-age, .shelf-age, .pulse-sub, .lamp-num,
  .d-state, .facts dd, .act-chip, .daemon-line, .badge, .updates-row {
    font-variant-numeric: tabular-nums;
  }

  /* Views fade and rise in. A CSS animation on the section itself, so it
     replays whenever showView unhides one, with no script. No fill mode:
     once it ends the section carries no transform, so nothing inside it
     is trapped in a stacking context (the action menu relies on that). */
  section:not([hidden]) { animation: view-in 220ms var(--ease); }

  /* ---- header ---------------------------------------------------------- */
  .app-head {
    display: flex;
    align-items: center;
    gap: var(--s3);
    padding: calc(var(--s4) + var(--safe-top)) 0 var(--s4);
  }
  .brand { display: flex; align-items: center; gap: var(--s3); flex: 1 1 auto; min-width: 0; }
  .brand-mark {
    flex: 0 0 auto;
    width: 40px;
    height: 40px;
    padding: 8px;
    border-radius: 13px;
    color: var(--accent);
    background: var(--lamp-work-soft);
    border: 1px solid var(--line);
  }
  .brand-text { min-width: 0; }
  h1 {
    margin: 0;
    font-size: var(--fs-title);
    line-height: var(--lh-title);
    font-weight: 700;
    letter-spacing: -0.01em;
  }
  .daemon-line {
    font-size: var(--fs-cap);
    line-height: var(--lh-cap);
    color: var(--ink-3);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .conn {
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    min-height: 28px;
    padding: 0 10px;
    border-radius: var(--r-pill);
    font-size: var(--fs-cap);
    line-height: var(--lh-cap);
    font-weight: 600;
    color: var(--ink-2);
    background: var(--surface);
    border: 1px solid var(--line);
  }
  .conn::before {
    content: "";
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: currentColor;
  }
  .conn-reconnecting { color: var(--need-ink); background: var(--need-soft); }
  .conn-offline { color: var(--danger-ink); background: var(--danger-soft); }

  /* ---- top-level tabs: a segmented control ------------------------------ */
  .tabbar {
    display: flex;
    gap: 4px;
    margin: 0 0 var(--s4);
    padding: 4px;
    border-radius: 17px;
    background: var(--track);
  }
  .tabbar-btn {
    flex: 1 1 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: var(--s2);
    min-height: 44px;
    border-radius: 13px;
    font-size: 15px;
    font-weight: 600;
    color: var(--ink-2);
    transition: background var(--t-base) var(--ease), color var(--t-base) var(--ease);
  }
  .tabbar-btn.is-active {
    color: var(--ink);
    background: var(--surface);
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08), 0 0 0 1px var(--line);
  }
  .badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 20px;
    height: 20px;
    padding: 0 6px;
    border-radius: var(--r-pill);
    font-size: var(--fs-cap);
    font-weight: 700;
    color: var(--surface);
    background: var(--need-ink);
  }

  /* ---- fatal line and toast ------------------------------------------ */
  #error {
    display: none;
    position: relative;
    margin: 0 0 var(--s4);
    padding: var(--s3) var(--s4) var(--s3) 22px;
    border-radius: var(--r-ctl);
    font-size: var(--fs-sm);
    line-height: var(--lh-sm);
    color: var(--ink);
    background: var(--surface);
    border: 1px solid var(--line);
  }
  /* An inset bar, not a border or inset shadow: either follows the
     corner radius and draws a crescent. */
  #error::before {
    content: "";
    position: absolute;
    left: 8px;
    top: 10px;
    bottom: 10px;
    width: 4px;
    border-radius: 2px;
    background: var(--lamp-need);
  }
  /* A floating toast, never an in-flow banner (an in-flow one shoved the
     page down and back under the finger). TOP-anchored: a bottom-anchored
     fixed element can render below the fold while the sheet is not fully
     expanded. z-index 70 sits above the dialog (60) and its scrim (50). */
  #notice {
    position: fixed;
    left: 50%;
    top: calc(var(--s3) + var(--safe-top));
    z-index: 70;
    display: flex;
    align-items: center;
    gap: 10px;
    width: max-content;
    max-width: min(92vw, 26rem);
    padding: 8px 16px 8px 8px;
    border-radius: var(--r-pill);
    font-size: var(--fs-sm);
    line-height: var(--lh-sm);
    color: var(--ink);
    background: var(--surface);
    border: 1px solid var(--line);
    box-shadow: var(--shadow-sheet);
    opacity: 0;
    pointer-events: none;
    transform: translate(-50%, -12px);
    transition: opacity 180ms var(--ease), transform 180ms var(--ease);
  }
  #notice.is-visible { opacity: 1; transform: translate(-50%, 0); }
  .toast-icon {
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 26px;
    height: 26px;
    border-radius: 50%;
    font-size: 14px;
    font-weight: 700;
    line-height: 1;
  }
  #notice.toast-ok .toast-icon { color: var(--on-accent); background: var(--accent); }
  #notice.toast-err .toast-icon { color: var(--surface); background: var(--danger-ink); }
  #notice.toast-info .toast-icon { color: var(--ink-2); background: var(--fill); }
  .toast-text { min-width: 0; }

  /* ---- shared pieces ---------------------------------------------------- */
  .sect-title {
    display: flex;
    align-items: center;
    gap: var(--s2);
    margin: var(--s5) var(--s1) var(--s2);
    font-size: 13px;
    line-height: 18px;
    font-weight: 600;
    letter-spacing: 0.01em;
    color: var(--ink-3);
  }
  .sect-note, .note {
    margin: var(--s2) var(--s1) 0;
    font-size: 13px;
    line-height: 18px;
    color: var(--ink-3);
  }
  .muted { padding: var(--s3) var(--s4); font-size: var(--fs-sm); line-height: var(--lh-sm); color: var(--ink-3); }
  .card {
    margin-top: var(--s3);
    padding: var(--s4);
    border-radius: var(--r-card);
    background: var(--surface);
    border: 1px solid var(--line);
  }
  .card-head { display: flex; align-items: center; gap: var(--s3); margin-bottom: var(--s3); }
  .card-icon {
    display: grid;
    place-items: center;
    width: 34px;
    height: 34px;
    border-radius: 11px;
    color: var(--accent);
    background: var(--lamp-work-soft);
  }
  .card-title { margin: 0; font-size: var(--fs-body); line-height: var(--lh-body); font-weight: 600; }
  .primary {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: var(--s2);
    min-height: 48px;
    padding: 0 var(--s5);
    border-radius: var(--r-ctl);
    font-size: var(--fs-body);
    font-weight: 600;
    background: var(--tg-theme-button-color, #2481cc);
    color: var(--tg-theme-button-text-color, #ffffff);
    transition: transform var(--t-fast) var(--ease), opacity var(--t-base) var(--ease);
  }
  .primary:disabled { opacity: 0.45; }
  .primary.block { display: flex; width: 100%; }
  /* The beacon's own call to action, in the beacon's own colour. */
  .primary.btn-need { color: var(--surface); background: var(--need-ink); }
  .icon-btn {
    flex: 0 0 auto;
    display: grid;
    place-items: center;
    width: 48px;
    min-height: 48px;
    border-radius: var(--r-ctl);
    color: var(--ink);
    background: var(--surface);
    border: 1px solid var(--line);
  }
  .fab {
    flex: 0 0 auto;
    display: grid;
    place-items: center;
    width: 48px;
    height: 48px;
    border-radius: 16px;
    color: var(--on-accent);
    background: var(--accent);
    box-shadow: 0 6px 18px color-mix(in srgb, var(--accent) 35%, transparent);
    transition: transform var(--t-fast) var(--ease);
  }
  .fab .ic { width: 24px; height: 24px; stroke-width: 2.2; }
  .tile:active, .primary:active, .fab:active, .pill:active, .chip:active,
  .beacon-body:active, .icon-btn:active, .shelf-row:active, .kebab:active {
    transform: scale(0.98);
  }

  /* ---- the lamp: a context ring around a number or an icon ------------
     The ring is a conic gradient masked to a 4px band on ::before, so the
     percentage is ONE custom property (--pct, 0-100) and the contents
     stay unmasked. ::after is the working lantern's halo. */
  .lamp {
    --lamp-c: var(--lamp-rest);
    --ring: 4px;
    position: relative;
    isolation: isolate;
    flex: 0 0 auto;
    display: grid;
    place-items: center;
    width: 44px;
    height: 44px;
    border-radius: 50%;
    color: var(--ink);
    background: var(--fill);
  }
  .lamp::before {
    content: "";
    position: absolute;
    inset: 0;
    border-radius: 50%;
    background: conic-gradient(var(--lamp-c) calc(var(--pct, 0) * 1%), var(--track) 0);
    -webkit-mask: radial-gradient(farthest-side, transparent calc(100% - var(--ring)), #000 calc(100% - var(--ring) + 0.5px));
    mask: radial-gradient(farthest-side, transparent calc(100% - var(--ring)), #000 calc(100% - var(--ring) + 0.5px));
  }
  .lamp .ic { position: relative; width: 20px; height: 20px; }
  .lamp-num {
    position: relative;
    font-size: 13px;
    line-height: 1;
    font-weight: 700;
    letter-spacing: -0.02em;
  }
  .lamp-num::after { content: "%"; font-size: 0.72em; font-weight: 600; opacity: 0.75; }
  .lamp-work { --lamp-c: var(--lamp-work); background: var(--lamp-work-soft); }
  .lamp-work::after {
    content: "";
    position: absolute;
    inset: -7px;
    z-index: -1;
    border-radius: 50%;
    background: radial-gradient(closest-side, color-mix(in srgb, var(--lamp-work) 34%, transparent) 55%, transparent 100%);
    opacity: 0.35;
    animation: breathe 2.4s ease-in-out infinite alternate;
  }
  .lamp-need { --lamp-c: var(--lamp-need); --pct: 100; color: var(--need-ink); background: var(--surface); }
  .lamp-out { --lamp-c: var(--lamp-out); --pct: 0; color: var(--ink-3); background: transparent; }
  .lamp-lg { --ring: 6px; width: 68px; height: 68px; }
  .lamp-lg .lamp-num { font-size: 20px; }
  .lamp-lg .ic { width: 26px; height: 26px; }
  .lamp-sm { --ring: 3px; width: 32px; height: 32px; }
  .lamp-sm .ic { width: 16px; height: 16px; }
  @supports not ((mask: radial-gradient(red, blue)) or (-webkit-mask: radial-gradient(red, blue))) {
    .lamp::before { background: none; border: var(--ring) solid var(--lamp-c); }
  }

  /* ---- sessions: the pulse sentence ------------------------------------- */
  .pulse-row { display: flex; align-items: center; gap: var(--s3); margin: 0 0 var(--s4); }
  .pulse { flex: 1 1 auto; min-width: 0; }
  .pulse-main {
    display: block;
    font-size: var(--fs-title);
    line-height: var(--lh-title);
    font-weight: 700;
    letter-spacing: -0.01em;
    text-wrap: balance;
  }
  .pulse-need { color: var(--need-ink); }
  .pulse-rest { color: var(--ink-2); }
  /* a line breaks only at a comma, never between "2" and "working" */
  .pulse-need, .pulse-work, .pulse-rest { white-space: nowrap; }
  .pulse-sub {
    display: block;
    margin-top: 2px;
    font-size: var(--fs-sm);
    line-height: var(--lh-sm);
    color: var(--ink-3);
  }

  /* ---- needs you: the beacon tray ------------------------------------ */
  .needs-you { margin: 0 0 var(--s5); }
  .sect-need { margin-top: 0; color: var(--need-ink); }
  .beacon-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--lamp-need);
    box-shadow: 0 0 0 3px var(--need-soft);
    animation: beacon 1.6s ease-in-out infinite alternate;
  }
  .beacon-list { display: flex; flex-direction: column; gap: var(--s3); }
  .beacon {
    position: relative;
    isolation: isolate;
    padding: var(--s3) var(--s3) var(--s3) var(--s4);
    border-radius: var(--r-card);
    background: var(--need-soft);
    border: 1px solid color-mix(in srgb, var(--lamp-need) 45%, var(--surface));
  }
  /* The swell is a pseudo-element's opacity, never an animated shadow. */
  .beacon::before {
    content: "";
    position: absolute;
    inset: -1px;
    z-index: -1;
    border-radius: inherit;
    box-shadow: 0 0 0 1px var(--lamp-need), 0 8px 28px color-mix(in srgb, var(--lamp-need) 38%, transparent);
    opacity: 0.55;
    pointer-events: none;
    animation: beacon 1.6s ease-in-out infinite alternate;
  }
  .beacon-body {
    display: flex;
    align-items: flex-start;
    gap: var(--s3);
    width: 100%;
    min-height: 44px;
    padding: var(--s1) 0;
    border-radius: var(--r-ctl);
    text-align: left;
  }
  .beacon-text { display: flex; flex-direction: column; gap: 3px; flex: 1 1 auto; min-width: 0; }
  .beacon-top { display: flex; align-items: center; gap: var(--s2); min-width: 0; }
  .beacon-label {
    min-width: 0;
    font-size: var(--fs-body);
    line-height: var(--lh-body);
    font-weight: 700;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .kind-chip {
    flex: 0 0 auto;
    padding: 2px 8px;
    border-radius: var(--r-pill);
    font-size: var(--fs-cap);
    line-height: var(--lh-cap);
    font-weight: 600;
    color: var(--need-ink);
    background: color-mix(in srgb, var(--lamp-need) 16%, var(--surface));
  }
  .beacon-age {
    flex: 0 0 auto;
    margin-left: auto;
    font-size: var(--fs-cap);
    line-height: var(--lh-cap);
    color: var(--ink-2);
  }
  .beacon-summary {
    margin: 0;
    font-size: var(--fs-sm);
    line-height: var(--lh-sm);
    color: var(--ink);
    overflow-wrap: anywhere;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .beacon-actions { display: flex; gap: var(--s2); margin-top: var(--s3); }
  .beacon-actions .primary { flex: 1 1 auto; }
  .beacon .note { margin: var(--s2) 0 0; color: var(--ink-2); }

  /* ---- sessions: the lantern grid ------------------------------------- */
  .lanterns { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--s3); }
  .tile {
    position: relative;
    display: flex;
    flex-direction: column;
    height: 132px;
    min-height: 44px;
    padding: var(--s3) 14px;
    border-radius: var(--r-card);
    text-align: left;
    overflow: hidden;
    background: var(--surface);
    border: 1px solid var(--line);
    transition: transform 260ms var(--ease), border-color var(--t-base) var(--ease);
  }
  .tile:active { transition-duration: var(--t-fast); }
  /* A working lantern spills a little light into its own tile. */
  .tile-busy {
    border-color: color-mix(in srgb, var(--lamp-work) 32%, var(--line));
    background: radial-gradient(130% 95% at 0% 0%, var(--lamp-work-soft) 0%, transparent 62%), var(--surface);
  }
  .tile-top { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--s2); }
  .tile-side { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; min-width: 0; }
  .tile-state {
    padding: 2px 8px;
    border-radius: var(--r-pill);
    font-size: var(--fs-cap);
    line-height: var(--lh-cap);
    font-weight: 600;
    color: var(--ink-2);
    background: var(--fill);
    white-space: nowrap;
  }
  .tile-busy .tile-state { color: var(--ink); background: var(--lamp-work-soft); }
  .tile-age { font-size: var(--fs-cap); line-height: var(--lh-cap); color: var(--ink-3); white-space: nowrap; }
  .tile-label, .tile-project, .tile-meta {
    display: block;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .tile-label { margin-top: auto; font-size: var(--fs-body); line-height: var(--lh-body); font-weight: 700; }
  .tile-project { font-size: var(--fs-sm); line-height: 18px; color: var(--ink-2); }
  .tile-meta { margin-top: 2px; font-size: var(--fs-cap); line-height: var(--lh-cap); color: var(--ink-3); }
  .is-new { animation: rise-in var(--t-enter) var(--ease); }
  .skel.skel-tile {
    display: flex;
    flex-direction: column;
    gap: 8px;
    height: 132px;
    padding: var(--s3) 14px;
    border-radius: var(--r-card);
    background: var(--surface);
    border: 1px solid var(--line);
  }
  .skel-ring { width: 44px; height: 44px; margin-bottom: auto; border-radius: 50%; background: var(--fill); }
  .skel-bar { height: 12px; border-radius: 6px; background: var(--fill); }
  .pulse .skel { height: 22px; background: var(--track); }

  /* ---- finished: the shelf --------------------------------------------- */
  .shelf { margin-top: var(--s5); }
  .gone-toggle {
    display: flex;
    align-items: center;
    gap: var(--s2);
    width: 100%;
    min-height: 44px;
    padding: 0 var(--s1);
    font-size: var(--fs-sm);
    line-height: var(--lh-sm);
    font-weight: 600;
    color: var(--ink-2);
  }
  .gone-toggle .chev { margin-left: auto; }
  .shelf-list {
    display: flex;
    flex-direction: column;
    margin-top: var(--s1);
    border-radius: var(--r-card);
    overflow: hidden;
    background: var(--surface);
    border: 1px solid var(--line);
  }
  .shelf-row {
    display: flex;
    align-items: center;
    gap: var(--s3);
    width: 100%;
    min-height: 56px;
    padding: var(--s2) 14px;
    text-align: left;
  }
  .shelf-row + .shelf-row { border-top: 1px solid var(--line); }
  .shelf-text { flex: 1 1 auto; min-width: 0; }
  .shelf-label, .shelf-meta { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .shelf-label { font-size: 15px; line-height: 20px; font-weight: 600; color: var(--ink-2); }
  .shelf-meta { font-size: var(--fs-cap); line-height: var(--lh-cap); color: var(--ink-3); }
  .shelf-age { flex: 0 0 auto; font-size: var(--fs-cap); color: var(--ink-3); }

  /* ---- empty ------------------------------------------------------------ */
  .empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: var(--s6) var(--s4);
    text-align: center;
  }
  .empty-lamp {
    display: grid;
    place-items: center;
    width: 96px;
    height: 96px;
    margin-bottom: var(--s5);
    border-radius: 50%;
    color: var(--ink-3);
    border: 2px dashed var(--lamp-out);
  }
  .empty-lamp .ic { width: 40px; height: 40px; stroke-width: 1.5; }
  .empty-title { margin: 0; font-size: var(--fs-title); line-height: var(--lh-title); font-weight: 700; }
  .empty-text {
    max-width: 260px;
    margin: 6px 0 var(--s5);
    font-size: var(--fs-sm);
    line-height: var(--lh-sm);
    color: var(--ink-2);
    text-wrap: balance;
  }

  /* ---- skeletons: the shape of what is coming ------------------------ */
  .skel { position: relative; overflow: hidden; border-radius: 8px; background: var(--fill); }
  .skel::after {
    content: "";
    position: absolute;
    inset: 0;
    background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--surface) 70%, transparent), transparent);
    transform: translateX(-100%);
    animation: skel 1.4s ease-in-out infinite;
  }
  .skel-line { height: 14px; margin: 10px 0; }
  .w90 { width: 90%; }
  .w70 { width: 70%; }
  .w40 { width: 40%; }
  .skel-row { height: 52px; border-radius: 0; background: var(--surface); }
  .skel-row + .skel-row { border-top: 1px solid var(--line); }

  /* ---- session page ---------------------------------------------------- */
  .d-head { display: flex; align-items: flex-start; gap: var(--s4); padding: var(--s2) 0 var(--s4); }
  .d-head .kebab-wrap { margin-left: auto; }
  .d-title { flex: 1 1 auto; min-width: 0; }
  .d-title-row { display: flex; align-items: center; gap: var(--s2); min-width: 0; }
  .detail-label {
    min-width: 0;
    font-size: var(--fs-title);
    line-height: var(--lh-title);
    font-weight: 700;
    letter-spacing: -0.01em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .status {
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 2px 8px;
    border-radius: var(--r-pill);
    font-size: var(--fs-cap);
    line-height: var(--lh-cap);
    font-weight: 600;
    color: var(--ink-2);
    background: var(--fill);
  }
  .status:empty { display: none; }
  .status::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
  .status-busy { color: var(--ink); background: var(--lamp-work-soft); }
  .status-busy::before { background: var(--lamp-work); }
  .status-waiting { color: var(--need-ink); background: var(--need-soft); }
  .status-gone { color: var(--ink-3); }
  .d-state-row { display: flex; align-items: center; gap: var(--s2); margin-top: 2px; min-width: 0; }
  .d-state { min-width: 0; font-size: var(--fs-sm); line-height: var(--lh-sm); color: var(--ink-2); }
  /* In the header the status is a dot leading the state sentence; its
     word stays in the DOM for screen readers. */
  .d-state-row .status { gap: 0; padding: 0; font-size: 0; line-height: 0; background: none; }
  .d-state-row .status::before { width: 8px; height: 8px; background: var(--lamp-rest); }
  .d-state-row .status-busy::before { background: var(--lamp-work); box-shadow: 0 0 0 3px var(--lamp-work-soft); }
  .d-state-row .status-waiting::before { background: var(--lamp-need); box-shadow: 0 0 0 3px var(--need-soft); }
  .d-state-row .status-gone::before { background: var(--lamp-out); }
  .d-title .pill { margin-top: var(--s2); }
  .pill {
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    min-height: 44px;
    padding: 0 14px 0 12px;
    border-radius: var(--r-pill);
    font-size: var(--fs-sm);
    font-weight: 600;
    color: var(--ink);
    background: var(--surface);
    border: 1px solid var(--line);
  }
  .pill .ic { width: 18px; height: 18px; }
  .pill-resume { background: var(--lamp-work-soft); }
  /* Positioning context for the menu, which hangs off the button. No
     filter, transform or backdrop-filter here: any of them would trap the
     menu's z-index under its own scrim. */
  .kebab-wrap { position: relative; flex: 0 0 auto; display: flex; }
  .kebab {
    display: grid;
    place-items: center;
    width: 44px;
    min-height: 44px;
    border-radius: 50%;
    color: var(--ink);
    background: var(--surface);
    border: 1px solid var(--line);
  }
  .kebab[aria-expanded="true"] { background: var(--fill); }
  .menu {
    position: absolute;
    top: calc(100% + 8px);
    right: 0;
    z-index: 60;
    min-width: 228px;
    max-width: min(300px, calc(100vw - 32px));
    padding: 6px;
    border-radius: 18px;
    background: var(--surface);
    box-shadow: var(--shadow-sheet);
    transform-origin: top right;
    animation: pop-in 180ms var(--ease);
  }
  .menu-item {
    display: flex;
    align-items: center;
    gap: var(--s3);
    width: 100%;
    min-height: 44px;
    padding: 10px 12px;
    border-radius: 12px;
    font-weight: 500;
    text-align: left;
    color: var(--ink);
  }
  .menu-item .ic { color: var(--ink-2); }
  .menu-item:active { background: var(--fill); }
  .menu-item.is-danger, .menu-item.is-danger .ic { color: var(--danger-ink); }
  .menu-item[disabled] { opacity: 0.45; }
  .menu-note { padding: 0 12px 8px 44px; font-size: var(--fs-cap); line-height: var(--lh-cap); color: var(--ink-3); }
  .menu-divider { height: 1px; margin: 6px 10px; background: var(--line); }

  /* The session is waiting: the same beacon, on its own page. */
  .beacon-solo { margin: 0 0 var(--s4); padding: var(--s4); }
  .beacon-head { display: flex; align-items: center; gap: var(--s3); }
  .beacon-head .beacon-summary { flex: 1 1 auto; min-width: 0; -webkit-line-clamp: 4; }
  .beacon-solo .beacon-kicker { font-size: var(--fs-cap); line-height: var(--lh-cap); font-weight: 600; color: var(--need-ink); }

  /* Activity: the last few tool calls as lanterns on a string. */
  .activity {
    display: block;
    width: 100%;
    min-height: 44px;
    margin: 0 0 var(--s4);
    padding: 14px var(--s4);
    border-radius: var(--r-card);
    text-align: left;
    background: var(--surface);
    border: 1px solid var(--line);
  }
  .act-head { display: flex; align-items: center; justify-content: space-between; gap: var(--s2); margin-bottom: 12px; }
  .act-title { font-size: 13px; line-height: 18px; font-weight: 600; color: var(--ink-3); }
  .act-chips { display: flex; gap: 6px; }
  .act-chip {
    padding: 2px 8px;
    border-radius: var(--r-pill);
    font-size: var(--fs-cap);
    line-height: var(--lh-cap);
    font-weight: 600;
    color: var(--ink-2);
    background: var(--fill);
  }
  .beads { display: flex; align-items: center; gap: 18px; min-height: 16px; padding-left: 1px; }
  .bead {
    position: relative;
    flex: 0 0 auto;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: var(--lamp-work);
    border: 2px solid var(--lamp-work);
  }
  .bead + .bead::before {
    content: "";
    position: absolute;
    top: 50%;
    right: calc(100% + 2px);
    width: 18px;
    height: 2px;
    margin-top: -1px;
    border-radius: 2px;
    background: var(--line);
  }
  .bead-failed { background: var(--surface); border-color: var(--danger-ink); }
  .bead-running { background: var(--surface); animation: swell 1.6s ease-in-out infinite alternate; }
  .act-last {
    display: block;
    margin-top: 12px;
    font-family: var(--mono);
    font-size: 13px;
    line-height: 18px;
    color: var(--ink-2);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .preview {
    position: relative;
    padding: 14px var(--s4) 14px 20px;
    border-radius: var(--r-card);
    font-size: 15px;
    line-height: 22px;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    background: var(--surface);
    border: 1px solid var(--line);
  }
  .preview::before {
    content: "";
    position: absolute;
    left: 8px;
    top: 14px;
    bottom: 14px;
    width: 3px;
    border-radius: 2px;
    background: var(--accent);
  }
  .preview.is-empty { color: var(--ink-3); }
  .preview.is-empty::before { background: var(--lamp-out); }
  .preview code {
    padding: 1px 5px;
    border-radius: 6px;
    font-family: var(--mono);
    font-size: 0.87em;
    background: var(--fill);
  }
  .facts {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 1px;
    margin: var(--s3) 0 0;
    border-radius: var(--r-card);
    overflow: hidden;
    background: var(--line);
    border: 1px solid var(--line);
  }
  .fact { min-width: 0; padding: 10px 14px; background: var(--surface); }
  .fact-wide { grid-column: 1 / -1; }
  .facts dt { font-size: var(--fs-cap); line-height: var(--lh-cap); color: var(--ink-3); }
  .facts dd {
    margin: 2px 0 0;
    font-size: 15px;
    line-height: 20px;
    font-weight: 600;
    overflow-wrap: anywhere;
  }
  .fact-mono dd { font-family: var(--mono); font-size: 13px; font-weight: 500; }
  #detail-model { margin-top: var(--s3); }
  .link-btn {
    min-height: 44px;
    margin-top: var(--s1);
    padding: 0 var(--s1);
    font-size: var(--fs-sm);
    font-weight: 600;
    color: var(--danger-ink);
  }

  /* ---- grouped choices (settings, the model control, the new form) ---- */
  .groups { border-radius: var(--r-card); overflow: hidden; background: var(--surface); border: 1px solid var(--line); }
  .groups:empty { display: none; }
  .grp + .grp { border-top: 1px solid var(--line); }
  .grp-head {
    display: flex;
    align-items: center;
    gap: var(--s3);
    width: 100%;
    min-height: 52px;
    padding: 0 var(--s4);
    text-align: left;
  }
  .grp-title { flex: 0 0 auto; font-weight: 500; }
  .grp-value {
    flex: 1 1 auto;
    min-width: 0;
    text-align: right;
    padding: 4px 0;
    font-size: 15px;
    line-height: 20px;
    color: var(--ink-2);
    /* Real labels ("Don't apply any rule") outgrow the narrow form
       column: two balanced lines, then an ellipsis. */
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    overflow: hidden;
    text-wrap: balance;
  }
  .grp-caret { flex: 0 0 auto; display: grid; place-items: center; width: 16px; height: 16px; }
  .grp-caret::before {
    content: "";
    width: 7px;
    height: 7px;
    margin-left: -3px;
    border-right: 2px solid var(--ink-3);
    border-bottom: 2px solid var(--ink-3);
    transform: rotate(-45deg);
    transition: transform var(--t-base) var(--ease);
  }
  .grp.is-open .grp-caret::before { margin: -3px 0 0; transform: rotate(45deg); }
  .grp-body { padding: var(--s1) 0 var(--s2); border-top: 1px solid var(--line); animation: fade-in 180ms var(--ease); }
  .choice {
    display: grid;
    grid-template-columns: 22px minmax(0, 1fr) auto;
    column-gap: var(--s3);
    align-items: center;
    width: 100%;
    min-height: 48px;
    padding: 10px var(--s4);
    text-align: left;
  }
  .choice::before {
    content: "";
    grid-column: 1;
    grid-row: 1 / span 2;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    border: 2px solid var(--ink-3);
    transition: border-color var(--t-base) var(--ease);
  }
  .choice.is-active::before {
    border-color: var(--accent);
    background: radial-gradient(circle, var(--accent) 0 4.5px, transparent 5px);
  }
  .choice-new::before {
    content: "+";
    display: grid;
    place-items: center;
    font-size: 15px;
    font-weight: 700;
    line-height: 1;
    color: var(--accent);
    border-style: dashed;
    border-color: var(--accent);
  }
  .choice:disabled { opacity: 0.5; }
  .choice-main { grid-column: 2; grid-row: 1; }
  .choice.is-active .choice-main { font-weight: 600; }
  .tag {
    grid-column: 3;
    grid-row: 1;
    padding: 2px 8px;
    border-radius: var(--r-pill);
    font-size: var(--fs-cap);
    line-height: var(--lh-cap);
    font-weight: 600;
    color: var(--ink-2);
    background: var(--fill);
  }
  .choice-help {
    grid-column: 2 / span 2;
    grid-row: 2;
    margin-top: 1px;
    font-size: 13px;
    line-height: 18px;
    color: var(--ink-3);
    overflow-wrap: anywhere;
  }
  .reveal { padding: 2px var(--s4) var(--s3) 50px; }
  .reveal-label { display: block; margin: 0 0 6px; font-size: var(--fs-cap); line-height: var(--lh-cap); font-weight: 600; color: var(--ink-3); }
  .reveal-note { margin-top: 6px; font-size: 13px; line-height: 18px; color: var(--ink-3); }
  .reveal-note:empty { display: none; }
  .reveal-note.is-error { color: var(--danger-ink); }
  .field-input {
    display: block;
    width: 100%;
    min-height: 48px;
    padding: 0 14px;
    border-radius: var(--r-ctl);
    font: 400 16px/22px var(--font);
    color: var(--ink);
    background: var(--surface);
    border: 1px solid var(--line);
    -webkit-appearance: none;
    appearance: none;
    transition: border-color var(--t-base) var(--ease), box-shadow var(--t-base) var(--ease);
  }
  .field-input:focus, .prefix-field:focus-within {
    outline: none;
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--lamp-work-soft);
  }
  .field-input::placeholder, .prefix-input::placeholder { color: var(--ink-3); }
  .field-error { margin: 6px var(--s1) 0; font-size: 13px; line-height: 18px; color: var(--danger-ink); }
  .prefix-field {
    display: flex;
    align-items: center;
    min-height: 48px;
    border-radius: var(--r-ctl);
    overflow: hidden;
    background: var(--fill);
    border: 1px solid var(--line);
  }
  .prefix-text { padding-left: 12px; font-family: var(--mono); font-size: 13px; color: var(--ink-3); white-space: nowrap; }
  .prefix-input {
    flex: 1 1 auto;
    min-width: 0;
    min-height: 46px;
    padding: 0 6px;
    border: 0;
    outline: none;
    font: 400 16px/22px var(--mono);
    color: var(--ink);
    background: transparent;
  }
  .prefix-go {
    display: grid;
    place-items: center;
    width: 44px;
    height: 44px;
    margin: 1px;
    border-radius: 12px;
    color: var(--on-accent);
    background: var(--accent);
  }
  .prefix-go:disabled { opacity: 0.4; }

  /* ---- disclosures: changed files and the timeline ------------------ */
  .disclosures { margin-top: var(--s5); }
  .sect-toggle {
    display: flex;
    align-items: center;
    gap: var(--s2);
    width: 100%;
    min-height: 52px;
    padding: 0 var(--s4);
    font-weight: 500;
    text-align: left;
  }
  .disclosures > * + .sect-toggle { border-top: 1px solid var(--line); }
  .dis-title { flex: 1 1 auto; }
  .chev { width: 16px; height: 16px; color: var(--ink-3); transition: transform var(--t-base) var(--ease); }
  .chev.is-open, .is-open > .chev { transform: rotate(90deg); }
  .panel { max-height: 380px; overflow: auto; padding: var(--s1) 0; border-top: 1px solid var(--line); }
  .disclosures .sect-note { margin: 0; padding: var(--s3) var(--s4) 0; border-top: 1px solid var(--line); }
  .timeline-row { position: relative; padding: 7px var(--s4) 7px 40px; font-size: 13px; line-height: 18px; overflow-wrap: anywhere; }
  .timeline-row::after { content: ""; position: absolute; left: 21px; top: 0; bottom: 0; width: 2px; background: var(--line); }
  .timeline-row::before {
    content: "";
    position: absolute;
    z-index: 1;
    left: 17px;
    top: 12px;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--surface);
    border: 2px solid var(--lamp-out);
  }
  .timeline-tool { font-family: var(--mono); font-size: 12.5px; }
  .timeline-tool::before { background: var(--lamp-work); border-color: var(--lamp-work); }
  .timeline-tool.state-failed::before { background: var(--surface); border-color: var(--danger-ink); }
  .timeline-tool.state-running::before { background: var(--surface); border-color: var(--lamp-work); }
  .timeline-commentary { color: var(--ink-2); }
  .diff-file + .diff-file { border-top: 1px solid var(--line); }
  .diff-file-header {
    display: flex;
    justify-content: space-between;
    gap: var(--s2);
    padding: 10px var(--s4);
    font-family: var(--mono);
    font-size: 13px;
    line-height: 18px;
    font-weight: 600;
    overflow-wrap: anywhere;
    cursor: pointer;
  }
  .diff-file-header .muted { padding: 0; font-weight: 400; }
  .diff-body { overflow-x: auto; font-family: var(--mono); font-size: 12px; line-height: 18px; background: var(--fill); }
  .diff-line { min-width: max-content; padding: 0 var(--s4); white-space: pre; }
  .diff-hunk { color: var(--ink-3); }
  .diff-add { background: color-mix(in srgb, #2da44e 18%, var(--surface)); }
  .diff-del { background: var(--danger-soft); }
  .diff-binary, .diff-truncated { padding: 10px var(--s4); font-size: 13px; line-height: 18px; color: var(--ink-3); }

  /* ---- new session: a guided card ------------------------------------ */
  .view-hero { display: flex; align-items: center; gap: var(--s4); padding: var(--s1) 0 var(--s5); }
  .view-title { margin: 0; font-size: var(--fs-title); line-height: var(--lh-title); font-weight: 700; }
  .view-sub { margin: 2px 0 0; font-size: var(--fs-sm); line-height: var(--lh-sm); color: var(--ink-2); }
  .steps { display: flex; flex-direction: column; gap: var(--s5); margin: 0; padding: 0; list-style: none; }
  .step { position: relative; padding-left: 38px; }
  .step::after {
    content: "";
    position: absolute;
    left: 12px;
    top: 34px;
    bottom: -16px;
    width: 2px;
    border-radius: 2px;
    background: var(--line);
  }
  .step:last-child::after { display: none; }
  .step-num {
    position: absolute;
    left: 0;
    top: 0;
    display: grid;
    place-items: center;
    width: 26px;
    height: 26px;
    border-radius: 50%;
    font-size: 13px;
    font-weight: 700;
    color: var(--surface);
    background: var(--ink);
  }
  .step-more .step-num { color: var(--ink-2); background: var(--track); }
  .step-head { display: flex; align-items: center; min-height: 26px; margin: 0 0 10px; }
  .step-title { font-size: var(--fs-body); line-height: var(--lh-body); font-weight: 600; }
  .chips {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--s2);
    margin: 0 0 10px;
  }
  .chips-cap { flex: 0 0 auto; margin-right: 2px; font-size: var(--fs-cap); font-weight: 600; color: var(--ink-3); }
  .chip {
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    min-height: 44px;
    max-width: 240px;
    padding: 0 14px 0 12px;
    border-radius: 12px;
    font-size: var(--fs-sm);
    font-weight: 600;
    white-space: nowrap;
    color: var(--ink);
    background: var(--surface);
    border: 1px solid var(--line);
    transition: border-color var(--t-base) var(--ease), background var(--t-base) var(--ease);
  }
  .chip .ic { width: 16px; height: 16px; color: var(--ink-3); }
  .chip-sub { min-width: 0; font-weight: 400; color: var(--ink-2); overflow: hidden; text-overflow: ellipsis; }
  .chip.is-active { border-color: var(--accent); background: var(--lamp-work-soft); box-shadow: inset 0 0 0 1px var(--accent); }
  .chip.is-active .ic { color: var(--accent); }
  .disclosure {
    display: flex;
    align-items: center;
    gap: var(--s2);
    width: 100%;
    min-height: 44px;
    margin-top: -9px;
    font-weight: 600;
    color: var(--ink-2);
  }
  #new-advanced { display: flex; flex-direction: column; gap: var(--s3); margin-top: var(--s2); }
  #new-advanced .note { margin-top: -4px; }
  .new-summary {
    margin: var(--s5) 0 var(--s3);
    padding: var(--s3) 14px;
    border-radius: var(--r-ctl);
    font-size: 13px;
    line-height: 19px;
    color: var(--ink-2);
    background: var(--surface);
    border: 1px dashed var(--line);
  }
  /* Telegram's MainButton carries the primary action when it exists. */
  html.has-mainbutton #new-create { display: none; }

  /* ---- settings ------------------------------------------------------- */
  #settings-groups { margin-top: var(--s2); }
  .updates { margin-top: var(--s5); }
  .updates-lines { display: flex; flex-direction: column; }
  .updates-row { padding: var(--s2) 0; font-size: var(--fs-sm); line-height: var(--lh-sm); }
  .updates-row + .updates-row { border-top: 1px solid var(--line); }
  .updates-small { margin-top: var(--s2); font-size: 13px; line-height: 18px; color: var(--ink-3); white-space: pre-line; }
  .updates-job {
    margin: var(--s2) 0;
    padding: 10px var(--s3);
    border-radius: 12px;
    font-size: var(--fs-sm);
    line-height: var(--lh-sm);
    white-space: pre-line;
    background: var(--fill);
  }
  /* The restart after an update: a working lantern whose ring turns. */
  .restart-row { display: flex; align-items: center; gap: var(--s3); }
  .restart-text { min-width: 0; }
  .lamp-spin { --pct: 28; }
  .lamp-spin::before { animation: spin 1.1s linear infinite; }
  .updates-actions { display: flex; flex-wrap: wrap; gap: var(--s2); margin-top: var(--s3); }
  .updates-actions .primary { flex: 1 1 auto; }
  .updates-again {
    min-height: 44px;
    padding: 0 var(--s4);
    border-radius: var(--r-pill);
    font-size: var(--fs-sm);
    font-weight: 600;
    color: var(--ink);
    background: var(--fill);
  }

  /* ---- the scrim, the confirm dialog ------------------------------- */
  .overlay {
    position: fixed;
    inset: 0;
    z-index: 50;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--s5);
    background: var(--scrim);
    animation: fade-in var(--t-base) ease;
  }
  /* Centred, so the confirm is nowhere near the menu row that opened it. */
  .modal {
    width: 100%;
    max-width: 340px;
    padding: var(--s5) var(--s5) var(--s4);
    border-radius: 26px;
    text-align: center;
    background: var(--surface);
    box-shadow: var(--shadow-sheet);
    animation: sheet-in var(--t-enter) var(--ease);
  }
  .modal-title { margin: 0 0 var(--s2); font-size: 18px; line-height: 24px; font-weight: 700; }
  .modal-body { margin: 0 0 var(--s5); font-size: var(--fs-sm); line-height: var(--lh-sm); color: var(--ink-2); }
  #confirm-rename-input { margin: 0 0 6px; text-align: left; }
  #confirm-rename-error { margin: 0 0 var(--s4); text-align: left; }
  .modal-actions { display: flex; gap: 10px; }
  .modal-btn {
    flex: 1 1 0;
    min-height: 48px;
    padding: 0 var(--s3);
    border-radius: var(--r-ctl);
    font-weight: 600;
    text-align: center;
    color: var(--ink);
    background: var(--fill);
  }
  .modal-btn.is-danger { color: var(--danger-ink); background: var(--danger-soft); }
  #confirm-ok:not(.is-danger) { color: var(--on-accent); background: var(--accent); }
  .modal-btn:disabled { opacity: 0.45; }

  /* ---- motion: only state moves ---------------------------------------- */
  @keyframes breathe { from { opacity: 0.35; transform: scale(0.94); } to { opacity: 0.8; transform: scale(1.04); } }
  @keyframes beacon { from { opacity: 0.55; } to { opacity: 1; } }
  @keyframes swell { from { transform: scale(0.85); } to { transform: scale(1.15); } }
  @keyframes skel { to { transform: translateX(100%); } }
  @keyframes spin { to { transform: rotate(1turn); } }
  @keyframes view-in { from { opacity: 0; transform: translateY(8px); } }
  @keyframes rise-in { from { opacity: 0; transform: translateY(6px) scale(0.98); } }
  @keyframes sheet-in { from { opacity: 0; transform: translateY(16px) scale(0.98); } }
  @keyframes pop-in { from { opacity: 0; transform: scale(0.94); } }
  @keyframes fade-in { from { opacity: 0; } }

  @media (hover: hover) {
    .tile:hover, .shelf-row:hover, .chip:hover { border-color: color-mix(in srgb, var(--ink) 22%, var(--surface)); }
    .menu-item:hover, .grp-head:hover, .choice:hover, .sect-toggle:hover { background: var(--fill); }
  }

  /* Reduced motion: every loop stops on a state that still reads (a
     steady amber ring, a steady halo), and entries are instant. */
  @media (prefers-reduced-motion: reduce) {
    .lamp-work::after { animation: none; opacity: 0.8; }
    .beacon::before, .beacon-dot { animation: none; opacity: 1; }
    .bead-running { animation: none; transform: none; }
    .skel::after { animation: none; display: none; }
    .lamp-spin::before { animation: none; }
    section:not([hidden]), .is-new, .menu, .modal, .overlay, .grp-body { animation: none; }
    #notice, .tile, .fab, .primary, .chip, .grp-caret::before, .chev { transition: none; }
    .tile:active, .primary:active, .fab:active, .pill:active, .chip:active,
    .beacon-body:active, .icon-btn:active, .shelf-row:active, .kebab:active { transform: none; }
  }
"""

__all__ = ["CSS"]
