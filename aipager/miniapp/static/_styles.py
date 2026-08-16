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
    border-bottom-color: var(--tg-theme-link-color, #2481cc);
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
     never wipe a fatal message that must stay on screen. */
  #notice {
    margin-top: 12px;
    padding: 8px 10px;
    border: 1px solid var(--tg-theme-hint-color, #e0e0e0);
    border-radius: 8px;
    font-size: 0.9rem;
    display: none;
  }

  /* Settings groups: one screen, every option visible, no nested menus —
     the whole point of doing this here instead of in chat. */
  .setgroup { margin-top: 20px; }
  .setgroup-title { font-weight: 700; margin-bottom: 8px; }
  .setopt {
    display: block;
    width: 100%;
    text-align: left;
    margin-top: 6px;
    padding: 9px 12px;
    font: inherit;
    color: var(--tg-theme-text-color, #000000);
    background: transparent;
    border: 1px solid var(--tg-theme-hint-color, #e0e0e0);
    border-radius: 8px;
    cursor: pointer;
  }
  .setopt.is-active {
    border-color: var(--tg-theme-link-color, #2481cc);
    background: var(--tg-theme-link-color, #2481cc);
    color: #ffffff;
  }
  .setopt[disabled] { opacity: 0.5; cursor: default; }
  .setopt-help { display: block; font-size: 0.78rem; opacity: 0.75; margin-top: 2px; }
  .setopt.is-saving { opacity: 0.6; }
  /* The "default" tag (design §4): marks the SCOPE's value, independent of
     which option is filled/selected. Deliberately understated relative to
     .is-active's solid fill — it is a note about where an override
     diverged from, not the thing currently in effect. */
  .setopt-default-tag {
    display: inline-block;
    margin-left: 6px;
    padding: 1px 7px;
    font-size: 0.7rem;
    font-weight: 600;
    color: var(--tg-theme-hint-color, #888888);
    border: 1px solid var(--tg-theme-hint-color, #e0e0e0);
    border-radius: 999px;
    vertical-align: middle;
  }
  .setopt.is-active .setopt-default-tag {
    color: rgba(255, 255, 255, 0.85);
    border-color: rgba(255, 255, 255, 0.55);
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
  .optrow { display: flex; flex-wrap: wrap; gap: 6px; }
  .optrow .opt {
    padding: 8px 12px;
    font: inherit;
    font-size: 0.9rem;
    color: var(--tg-theme-text-color, #000000);
    background: transparent;
    border: 1px solid var(--tg-theme-hint-color, #e0e0e0);
    border-radius: 999px;
    cursor: pointer;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .optrow .opt.is-active {
    border-color: var(--tg-theme-link-color, #2481cc);
    background: var(--tg-theme-link-color, #2481cc);
    color: #ffffff;
  }
  .optrow .opt[disabled] { opacity: 0.45; cursor: default; }
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
    color: #ffffff;
    background: var(--tg-theme-button-color, #2481cc);
    border: 0;
    border-radius: 10px;
    cursor: pointer;
  }
  .primary[disabled] { opacity: 0.5; cursor: default; }

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

  #view-grid[hidden], #view-detail[hidden], #view-settings[hidden] { display: none; }

  .detail-label { font-weight: 700; font-size: 1.05rem; margin-right: 8px; }

  .panel[hidden] { display: none; }

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
  .diff-body[hidden] { display: none; }
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
