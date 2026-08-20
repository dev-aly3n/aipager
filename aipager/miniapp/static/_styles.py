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
  :root { color-scheme: light dark; }
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
  #notice.toast-err { border-left-color: #dc2626; }
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
     and follows it when the page scrolls. */
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
  .menu-item.is-danger { color: var(--tg-theme-destructive-text-color, #dc2626); }
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
    color: var(--tg-theme-destructive-text-color, #dc2626);
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
  .field-error { margin-top: 6px; font-size: 0.85rem; color: #dc2626; }
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
  .reveal-note.is-error { color: #dc2626; }

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
    color: #dc2626;
    margin-top: 12px;
    padding: 8px 10px;
    border: 1px solid #dc2626;
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
  .conn-offline { color: #dc2626; }


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
  .diff-del { background: rgba(220, 38, 38, 0.14); color: #dc2626; }
  .diff-hunk { color: var(--tg-theme-link-color, #2563eb); background: rgba(37, 99, 235, 0.08); }
  .diff-context { color: var(--tg-theme-text-color, #000000); }
  .diff-binary, .diff-truncated {
    padding: 8px 10px;
    color: var(--tg-theme-hint-color, #888888);
    font-size: 0.85rem;
  }
"""

__all__ = ["CSS"]
