# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Mini App Settings tab — every reply-style option on one screen.** Message
  layout, simple formatting, answer length and language level, each with a line
  explaining what it does, all visible at once instead of a tap per value
  through nested menus. Tapping saves immediately; a failed save puts the real
  stored value back rather than leaving a button that lies. The options, their
  order and their wording come from the same source `/settings` renders from, so
  the two surfaces cannot drift apart, and a change made in either shows up in
  the other. This is the Mini App's first write route: it re-verifies your
  Telegram signature on every request, applies the same admin rule chat applies
  to settings changes, validates the value server-side rather than trusting the
  page, posts a one-line note to the chat so the change is on the record, and is
  rate-limited. Non-admins see the settings but cannot change them.
- **Mini App sessions grid, rebuilt as a control panel.** Two columns, with a
  dashed **New session** cell always first and every session ordered by last
  activity. Finished sessions are dimmed and collapsed into a "show N finished"
  group instead of padding the grid — previously a machine with a dozen dead
  sessions opened onto a wall of names with no data behind them. Sessions
  waiting on a permission prompt or a question sort to the very top and put a
  count badge on the Sessions tab, so the one state that costs you time is
  visible without reading the grid. The header shows how many are live and what
  the run has cost in total, relative times tick on their own between refreshes,
  and an empty install now explains itself rather than showing a lone dashed
  box. A second top-level **Settings** tab is present and will hold the defaults
  for new sessions; it currently points at `/settings` in chat.
- **Self-hosted Telegram Mini App server (stage 1 — read-only).** aipager can
  now serve a small dashboard (daemon status + session list) straight from
  the machine it runs on, opened from Telegram via a new `/app` DM command.
  Off by default — no listener, no tunnel, until you run `aipager miniapp
  enable` and restart the daemon. The server binds `127.0.0.1` only,
  hardcoded, never configurable; a user-installed tunnel (Tailscale Funnel
  recommended, Cloudflare or anything else usable via a manual
  `--url` override) is the only intended way in. Every API request is
  authenticated with Telegram's `initData` HMAC signature (freshness-checked
  in both directions, ~5 min window, small clock-skew tolerance) and then
  authorized against the same scope/role rules `/status` already uses — a
  valid signature alone only proves *a* Telegram user, not an authorized
  one. In personal mode (no team/scope config), `/app` and the dashboard it
  links to additionally require the caller to be the operator (the Telegram
  user id behind `CHAT_ID`) — unlike a DM's `/status`, tapping the button
  discloses the tunnel URL itself and produces a credential usable over the
  open internet, so personal mode's usual "anyone who can DM the bot" trust
  model doesn't extend to it. Requires the new `aipager[miniapp]` install
  extra (`pip install 'aipager[miniapp]'`); the base install is unaffected.
  Mini Apps are a private-chat-only Bot API feature, so `/app` in a group
  replies with a plain-text explanation instead of a button. A failure to
  bind the server's port (e.g. already in use) logs a warning and disables
  the feature for that run rather than taking the whole daemon down. This is
  stage 1 of a multi-stage rollout — everything is read-only; settings
  editing, session control, and multi-bot management land in later releases.
- **Mini App sessions grid, drill-down and diff viewer (stage 2 — still
  read-only).** The dashboard from stage 1 grows into a live, auto-refreshing
  grid of every session in your scope (label, status, model, context %,
  cost, last-active), tapping a session opens a drill-down with its complete
  scrollable timeline (no more chat's truncated tool list) and a real diff
  viewer with syntax-colored, foldable hunks. "Waiting on permission" now
  gets one unmistakable status across every session at a glance — derived
  server-side from the same state chat already tracks, never a new status
  value. Diffs come from `git` against the session's own working directory
  (bounded: a hard per-call timeout, capped output size, capped file count —
  never a hang, never a multi-megabyte response, and a non-git/missing/empty
  repo reports cleanly instead of erroring); a file larger than the per-file
  cap still shows its first ~200 KB with a truncation marker rather than no
  content at all, and the timeline is emitted as
  plain structured JSON rather than Telegram markdown. The page adds
  Telegram-native chrome (matches your device's light/dark theme, a back
  button on the drill-down, a haptic pulse on status changes) and is honest
  about staleness — a visible live/reconnecting/offline indicator, polling
  that pauses while the app is backgrounded and refreshes immediately on
  return, and a clear "reopen from Telegram" message instead of retrying
  forever once the page's signed session expires. The API stays strictly
  GET-only — no session control, settings, or other mutating routes exist
  yet. Every request re-verifies `initData` and re-resolves scope membership,
  and a session label is only ever resolved within the caller's own scope —
  a label belonging to another scope 404s identically to one that doesn't
  exist anywhere, so no scope can probe another's session list.

### Changed
- **Mini App settings now live in `aipager.yaml` (config schema v3), not
  `config.env`.** Storing them in `config.env` was a silent-data-loss bug:
  the daemon retires that file on every start once `aipager.yaml` is
  authoritative, so `aipager miniapp enable` survived exactly one restart
  and then turned the Mini App off again with no error and no log line.
  Settings now live in a `miniapp:` block alongside the bot token and
  scopes, which is never retired. The upgrade is automatic and needs no
  action: the daemon carries any existing `MINIAPP_*` values across on its
  next start — including recovering them from an already-retired
  `config.env.retired.*` copy, which on an affected machine is the only
  remaining record of the configured port and public URL. Existing
  `schema_version: 2` files keep loading unchanged and are rewritten to
  v3 the next time the config is saved, so nothing breaks on upgrade.
  `aipager miniapp enable/disable/status` keep exactly the same flags and
  output, and the `MINIAPP_*` environment variables still override the
  file for one-off runs.

## [0.6.1] - 2026-08-15

### Fixed
- **`aipager config` now repoints hooks left behind by an earlier install.**
  An entry counted as "already wired" if its command merely ended in
  `/aipager-hook`, whatever directory it lived in, so once an event was
  written it was never updated. Installing a second way — pipx after pip, a
  venv then a system install, a moved home — left events split across builds:
  seen in the wild with 10 of 15 events on a stale editable venv while the
  rest ran the current one, which silently broke a feature while looking
  correctly configured. Our own hooks (and the `statusLine`) are now moved to
  the running install's path; third-party hooks are never touched, no entry is
  duplicated, and a working absolute path is still never replaced by a bare
  name that Claude Code could not resolve.
- **`aipager status` and `aipager doctor` no longer call a working install
  unconfigured.** Schema v2 made `aipager.yaml` authoritative for the bot
  token, which is what allowed `config.env` to be retired — but nothing
  filled `CHAT_ID`, and both commands gate on it. Every migrated install got
  "aipager isn't configured yet" from precisely the tools you reach for when
  something is wrong. `CHAT_ID` is now derived from the configured scopes
  (a lone scope wins, otherwise the group — the same rule the session
  registry already uses), and an explicitly-set `CLAUDE_TG_CHAT_ID` still
  takes precedence. It stayed hidden because a source checkout has a legacy
  `.env` that fills the gap; only real installs were affected.


## [0.6.0] - 2026-08-15

### Fixed
- **`/perms` no longer destroys the session it is restarting.** Switching
  between Ask and Auto kills the session and relaunches it under the other
  permission flag. `kill_session` sent SIGTERM, unlinked the socket and
  returned immediately, without waiting for the process to actually exit —
  so the relaunch created a new session at the same socket path, and the
  still-dying predecessor removed that socket on its way out. The switch
  reported success and the session vanished two seconds later. The kill now
  waits for the process to exit (escalating to SIGKILL if it outlives a
  short grace, and giving up rather than hanging if it survives even that)
  before unlinking, so no caller that relaunches can be clobbered.
- **A deliberate restart is no longer announced as a crash.** The same
  switch produced three messages: the mode confirmation, then "Session
  exited" from the old session's SessionEnd hook, then "Session crashed or
  killed" from the monitor noticing the socket disappear. Both alarm paths
  now stay quiet for the moment a restart is in flight, so the switch sends
  exactly one message. The quiet window is a deadline that expires on its
  own and is closed early when a relaunch fails, so a genuine crash — before,
  during or after — is still reported.
- **A session is no longer relaunched into a conversation that does not
  exist.** `/perms` relaunches with `--resume <session-id>` to keep the
  transcript, but a session that has not taken a turn yet has no conversation
  on disk, and Claude Code exits 1 with "No conversation found with session
  ID" when asked to resume one. Switching mode on a freshly-created session
  therefore killed it. The resume flag is now dropped when no transcript
  exists for that id — an id with no conversation has no history to preserve.

### Added
- **`/settings` command** — a nested inline-keyboard menu for four
  per-scope preferences: message layout (`card` / `merged` / `replace`),
  simple formatting, answer length (`extra short` / `short` /
  `medium` / `long`), and language level. Layout is pure
  presentation and applies instantly; the other three reach Claude via
  the `UserPromptSubmit` hook on the very next prompt, no session
  restart. Each option's "don't apply any rule" / off state injects
  nothing at all — it's a distinct default choice, not a synonym for
  "normal". Opening the menu is available to any scope member; changing
  a value in a group scope requires admin. Stored in
  `~/.config/aipager/preferences.json`, survives a daemon restart.

### Changed
- **`KEEP_FINISHED_CARD` is now a seed, not the last word.** It still
  supplies a scope's *default* message layout (`card` when true,
  `replace` when false), but a stored `/settings` → Message layout
  preference for that scope now overrides it — including the new
  `merged` mode, a third layout this env var predates. Existing
  `KEEP_FINISHED_CARD=0` installs see no behaviour change on upgrade
  until they tap something in `/settings`.

## [0.5.0] - 2026-08-07

### Added
- **The busy card now stays in the chat after the turn ends.** It used to be
  deleted the moment the answer arrived, throwing away the only record of
  which tools ran, in what order, and what Claude said between them. It is
  now re-rendered once as a finished timeline — settled `✅` footer, Stop
  button removed — and left above the answer. The finished render drops the
  live card's caps: every tool row and every commentary block is shown, and
  tool rows are shed oldest-first (noted as "N earlier tools") only if the
  card would otherwise exceed Telegram's 32 768-byte ceiling. Commentary is
  never shed. Set `KEEP_FINISHED_CARD=0` to restore the old delete
  behaviour. A failed final render is swallowed — the answer still goes out.
  With the card in place the separate `✅ label · Finished (23s)` header is
  redundant, so it is no longer sent: the answer body carries the reply link
  and the tracked message id instead. It still goes out when the card was
  not kept, when the turn produced no answer text, and when the answer
  overflows to a file — that note and the document's reply target live on it.

### Changed
- **Commentary now streams from Claude Code's `MessageDisplay` hook**, not
  from the JSONL transcript. The transcript is written only once a message's
  tool-result round finishes — measured at 2.5 s, 4.5 s and 11.8 s behind
  generation on one real turn — so a sentence introducing a tool could not be
  read until that tool had already completed, and it appeared under a `✅`
  instead of above the `⏳`. The hook fires as the text reaches the screen, in
  paragraph-sized chunks (a 2 768-character message arrived as five), so the
  quote now lands before or alongside the tool row it introduces. Chunks of
  one message grow a single block rather than each becoming a row. The
  transcript path remains as the fallback for a Claude Code that does not send
  the event, and switches off permanently for a session the moment the hook
  proves itself, so the two can never both deliver the same prose. A block is
  shown only once a tool row sits at or after its anchor: the hook streams the
  final answer as well, and that block introduces nothing, so it stays out of
  the card instead of being quoted directly above the message carrying it.
  Prose is anchored where the previous block left off rather than at the tool
  count when it arrives — a short preamble only reaches the hook once its
  message is complete, by which point its own tool rows have already landed.
  For the same reason a batch of tool rows waits up to 1.5 s for the sentence
  that introduced it, so the rows appear together with their quote instead of
  the quote jumping in above them a moment later; measured, that wait is
  20–515 ms. It only runs out when a message called tools without saying
  anything, and those rows then settle so the next sentence lands below them. The hook
  runs inside Claude's own display path, so it skips the statusLine read that
  the other events piggyback. `aipager config` wires the new event; existing
  installs pick it up on the next wizard run.
- **Busy message is now a chronological timeline.** The card interleaves
  Claude's mid-turn prose (`💬`) with the tool rows it already showed
  (`✅` done, `⏳` running, `❌` failed), each block placed where it actually
  arrived — so "let me check X", then the tools, then "layout is clear"
  reads in order. Placement is derived from the transcript's own byte order,
  not from when the hooks fired: Claude Code runs its PreToolUse hook before
  it flushes the assistant entry, so the tool row exists by the time its
  introducing prose is readable. A block appears whole as soon as it is read
  from the transcript; there is no character-by-character reveal. Older tool rows
  collapse into an "N earlier tools" line past 15, and commentary anchored
  to a collapsed row moves to the top of the visible window rather than
  disappearing. Only the most recent 600 characters of prose stay visible,
  dropped a whole block at a time, so the card stays glanceable on a long
  turn. Below a divider, a footer shows elapsed time, turn cost and the
  tool tally. Hook events (`tool_use`, `tool_done`, `subagent_start`, etc.)
  trigger immediate card updates (debounced at 0.9 s); the clock-only
  refresh falls back to 3 s when nothing has changed. Edits are serialised
  per session, so a hook-driven update cannot race the animation loop into
  Telegram's `400 canceled by new edit message request`. Every edit carries
  the Stop button. Groups and DMs use the same path.
- **Card layout: status back on the top line.** Elapsed time, turn cost and
  the tool tally ride on the header (`⏳ **label** · Working · 20s · $0.12 ·
  Bash ×2`) as they did before the streaming rework, so the divider and the
  separate footer are gone. Elapsed counts from `0s` instead of staying blank
  until 2 s and then jumping straight to `5s`. Claude's prose renders as a
  blockquote and tool rows as monospace code spans — a summary full of globs
  and paths is now literal, so it needs no backslash escaping, and a summary
  containing backticks gets a longer fence rather than breaking out of it.
  A tool row is still drawn the moment its hook fires, so it shows as `⏳`
  and flips to `✅` when it completes; the prose introducing it therefore
  arrives a second or two later and inserts itself above the row. Holding
  the row back until the transcript placed it would remove that shuffle, but
  it would also mean a tool finishing in under ~2 s never appeared as
  running at all — the live `⏳` was judged worth the shuffle.

### Removed
- **Removed the `sendRichMessageDraft` busy-message path.** The API itself
  works — a live probe pushed 24 updates with zero failures, rendering on
  both Desktop and Android — and it is the only way to get Telegram's native
  word-by-word typing animation, which `editMessageText` cannot reproduce.
  It was still rejected, on three grounds:

  1. **A draft is a 30-second ephemeral preview.** Per the Bot API docs, it
     "acts as a temporary 30-second preview — once the output is finalized,
     you must call `sendMessage` with the complete message to persist it",
     and clients drop it after `message_typing_draft_ttl` (30 s) or as soon
     as a real message arrives in the chat. aipager turns run for minutes,
     so keeping a draft alive means pushing updates continuously for the
     whole turn. There is no quiet keep-alive.
  2. **Streaming a draft locks the composer on Telegram Android** — the send
     button becomes a loading indicator and the user cannot reply
     (bugs.telegram.org/c/62189, closed by Telegram as intended behaviour).
     Reproduced on real hardware on 2026-08-04; Desktop is unaffected, which
     is why an earlier Desktop-only test looked clean. Combined with (1),
     the composer would stay locked for the entire turn. Queuing a follow-up
     prompt mid-turn is a core aipager feature, so this is disqualifying.
  3. **Drafts cannot carry a `reply_markup`**, so the Stop button — the one
     control the user needs mid-turn — could not be attached.

  There is no third mechanism: the native animation and mid-turn replies are
  mutually exclusive on Android. Do not reinstate drafts without new evidence
  that Telegram has changed (1) or (2).

### Fixed
- **"Deny" on a permission prompt no longer runs the tool.** The buttons
  drive Claude Code's cursor menu, and the assumed order was wrong: Deny
  stepped down one item onto "Yes, and always allow …", so refusing a
  tool executed it *and* took a session-wide grant — while the toast, the
  tool history and the audit log all said "Denied". Deny now overshoots
  past the end of the menu, which clamps on its last item, so it lands on
  the refusal whatever the prompt's shape. **Allow always** correspondingly
  drops from two Downs to one; on a prompt with no scope to widen it
  refuses rather than guessing, since a mistap must never broaden
  permissions. Present since 0.4.21.
- **"No response requested." is no longer published as the session's
  answer.** That line is not aipager's — Claude Code writes it into the
  transcript when a turn ends without producing any text, typically after
  an auto-compact. aipager read it back as the reply and streamed it into
  the live draft. A turn that produced nothing now sends the header alone
  rather than a placeholder, and deliberately does not fall back to the
  previous turn's cached answer, which would read as a plausible reply to
  the wrong prompt. Genuine API errors (rate limits, expired auth, 5xx)
  are recorded the same way but are still surfaced with their error card
  and retry button.
- **Streaming no longer stops after the first chunk.** The transcript
  reader advanced its byte offset one past the end of each line, so every
  line appended after it lost its first character, failed to parse, and
  was silently dropped.

## [0.4.28] - 2026-08-03

### Changed
- **The "still working" note no longer reads as an error.** A quiet
  session is almost always healthy — a long tool call, a heavy
  generation, a compaction — but the note announced itself with a
  warning triangle above a wall of failure causes, so people read it as
  a crash report and interrupted sessions that were fine. It now leads
  with an hourglass and a single line ("still working — quiet for N
  min"), one reassuring sentence, and the diagnostic causes collapsed
  behind an expandable quote that only opens if tapped. The **Stop**
  button is unchanged.
- **The note waits 10 minutes instead of 2.** `STALE_BUSY_TIMEOUT` rises
  from 120s to 600s, above the duration of nearly every legitimate quiet
  stretch, so the note becomes rare enough to be worth reading. Still
  overridable via the environment variable. A hung permission prompt now
  surfaces at 15 minutes rather than 7.

### Fixed
- **Transcript reads no longer guess which file belongs to a session.**
  When a session had no hook-stamped transcript path, four call sites
  fell back to scanning `~/.claude/projects` for the most recently
  modified JSONL on the machine and caching it under the session name
  for five minutes — with no check that the file had anything to do with
  that session. On a busy host the winner was routinely somebody else's
  conversation, which was then published into this session's chat. The
  fallback is gone; every path now uses only the transcript the hooks
  stamped, and reads nothing when there is none. The trade-off is
  deliberate: a session whose `Stop` hook is missed no longer
  auto-recovers from a guessed transcript and instead falls through to
  the "still working" note above.

## [0.4.27] - 2026-08-03

### Added
- **Replies now render as real Telegram rich messages.** Claude's raw
  markdown is sent through Bot API 10.1 `sendRichMessage` instead of
  being converted to a narrow HTML subset. GFM tables keep their column
  alignment, fenced code is syntax-highlighted and stays in one block,
  nested bullets keep their markers, task lists become real checkboxes,
  and `---` renders as a rule. `~~strike~~` and `***bold italic***` now
  work — the latter previously produced improperly nested HTML that
  Telegram rejected outright, silently losing the entire reply.
- **Right-to-left replies are detected and marked.** Persian and Arabic
  answers are sent with `is_rtl`, so they align and bullet correctly.
- **Live streaming in direct messages.** While a turn runs, new assistant
  text is streamed into an ephemeral draft via `sendRichMessageDraft`,
  then persisted as a normal message when the turn ends. Drafts are a
  Telegram private-chat feature, so group scopes are unaffected. The busy
  message, its **Stop** button and the token/cost stats are unchanged —
  drafts carry no keyboard, so streaming runs alongside them rather than
  replacing them.
- Any rich-message failure falls back to sending the raw markdown as
  plain text with no parse mode, which cannot fail to parse. A reply can
  no longer be lost to a formatting error.

### Changed
- The expandable-blockquote wrapper is gone. Answers render expanded
  instead of collapsing to two lines behind a chevron, and a blockquote
  now appears only where the markdown actually asked for one.
- The reply ceiling rises from 4096 to 32768 bytes, retiring most
  truncation: the head/tail split, the `TRUNCATED` banner and most
  `.txt` attachment fallbacks no longer trigger.

### Fixed
- **The bot token no longer appears in the logs.** `httpx` logs every
  request at INFO, and the Bot API embeds the token in the URL, so each
  `getUpdates` poll printed it in plaintext. Now suppressed to WARNING.
- **A session's draft can no longer stream another session's transcript.**
  Transcript discovery picks the most recently modified file across all
  projects and caches it for five minutes without checking ownership, so
  any concurrently-running session could win the race — and a byte offset
  measured against one file could be applied to another, emitting an
  arbitrary slice of it. The transcript is now resolved once per turn
  from the hook-stamped path and pinned for that turn's duration.

## [0.4.26] - 2026-08-02

### Fixed
- **A session no longer answers a new prompt with the previous turn's
  reply.** The idle-recovery fallback — which rescues a session whose
  `Stop` hook was missed — measured transcript quiet time in absolute
  terms, never against when the current turn began. A turn whose prompt
  never reached `claude` therefore looked identical to one that had
  finished and gone quiet: eight seconds in, the monitor read the
  *previous* turn's transcript tail, declared the turn complete, and
  published that turn's last message as the answer. It arrived as a
  confident "Finished", so the only symptom was a reply that did not
  match the question. Recovery now requires the transcript to have been
  written since the turn started, and a turn that never really started
  falls through to the honest "silent for 2+ min" warning instead.

## [0.4.25] - 2026-07-27

### Fixed
- **`/kill` no longer reports success while leaving a live `claude`
  process behind.** `kill_session` shelled out to `fuser` to find the
  dtach process, swallowed every failure, deleted the socket anyway,
  and returned success regardless. On images without `fuser` — which
  included the published Docker image — `/kill` was a silent no-op:
  the session kept running and burning tokens, and relaunching it
  produced two `claude` processes writing the same transcript. PID
  discovery now falls back to scanning `/proc` when `fuser` is
  unavailable, and a failed kill returns failure and keeps the socket,
  which is aipager's only handle on the process. `psmisc` was added to
  the Docker image.
- **Far fewer false "Silent for 2+ min" warnings.** The busy/idle
  state machine now subscribes to four more Claude Code lifecycle
  hooks — `StopFailure`, `PostCompact`, `SubagentStart` and
  `PostToolUseFailure` — so it observes turn, tool and compaction
  completion directly instead of inferring them. A turn that ended in
  failure, or a tool call that errored, previously left the session
  stranded in "working" until the warning fired. The new subscriptions
  are patched into `~/.claude/settings.json` automatically on the next
  `aipager start` — no config re-run needed, and hand-authored hooks
  are left untouched.

## [0.4.24] - 2026-07-26

### Fixed
- **`aipager doctor` and `aipager status` no longer crash on a
  malformed config.** A hand-edited `~/.config/aipager/aipager.yaml`
  that failed to parse took down every command, including `doctor` —
  the exact command the generic error handler tells you to run. The
  parse failure is now recorded instead of raised: `doctor` reports it
  as a failed check (`✗ Config file parses`) and completes its full
  run, and `status` prints the parse error above its normal output
  (`status --json` gains a `config_error` field). The daemon still
  refuses to start, but now names the file and the specific error
  instead of claiming aipager "isn't configured yet".
- **Running the test suite from a source checkout no longer overwrites
  your real config.** Tests derived their paths from `$HOME`, so a
  local `pytest` run could rewrite `~/.config/aipager/aipager.yaml`,
  `~/.claude/settings.json`, and the pending-users queue. Every
  home-derived path is now redirected to a temp directory, with a
  session-level guard that fails the run if anything under `$HOME` is
  touched. Only affects people running the suite from a checkout —
  installed users were never at risk.

## [0.4.23] - 2026-07-21

### Added
- **Ask / Auto permission mode UX for sessions.** Each session now has
  a persisted mode: **Ask** (💬 — Claude prompts before each tool
  call, the current default) or **Auto** (🤖 — Claude runs tools
  without prompting, corresponds to
  `--dangerously-skip-permissions`). Persisted per session in
  `~/.claude/aipager-sessions.json`, restored on daemon restart, and
  respected on `/resume` by default.
- **`/perms` Telegram command for mid-session mode switching.** Toggles
  the active session's mode via kill + resume (transcript and cwd
  preserved). Ask → Auto shows a confirmation keyboard; Auto → Ask
  fires immediately. If the session is BUSY, offers a
  `[🛑 Stop task & switch]` / `[⏳ Not now]` choice instead of
  silent-refusing. Implementation note: Claude Code's
  `bypassPermissions` mode is launch-time only, so aipager kills and
  relaunches with `--resume <id>` — you keep your full transcript.
- **Inline `/resume` picker with per-session mode choice.** Telegram
  `/resume` now shows a paginated glass-button list of previous
  sessions (each row: 💬/🤖 icon + label + relative time). Tap a
  session → a follow-up mode picker asks whether to resume as Ask or
  Auto, with `(default)` labelling the session's persisted mode. CLI
  `/resume` keeps its text picker but respects the persisted mode.
- **`!` prefix override on `/resume`.** Symmetric with `/new !name`:
  `/resume !ben` forces Auto, `/resume ben` forces Ask, `/resume`
  (picker) uses the persisted mode as the picker default.
- **Enriched `/new` reply.** After `/new ben`, aipager replies with
  mode icon + name, model, launch cwd, and a `/perms` nudge — so you
  see at a glance which mode a session is in, where it's rooted, and
  how to change modes.
- **Default mode wizard step.** `aipager config` now asks whether new
  sessions should default to Ask or Auto. Stored in
  `~/.config/aipager/aipager.yaml` as `default_mode`. `/new <name>`
  respects the default; `/new !<name>` still forces Auto explicitly.
- **"Allow always" button on the permission-prompt keyboard.** The
  keyboard for tool-call prompts is now a 2×2 grid:
  `[✅ Allow] [❌ Deny]` / `[🟢 Allow always] [⏹ Stop]`. Tapping
  "Allow always" maps to Claude Code's own "Yes, and don't ask again
  this session" option.
- **Team-mode admin gating.** In group mode, switching a session to
  Auto (via `/perms` or `/new !`) requires the caller's role to be
  `admin`. Non-admins get a polite denial.

### Changed
- **User-facing terminology scrub.** Replaced "dangerous" and "unsafe"
  wording in Telegram messages, wizard prompts, `/new` help text, and
  README with mode-neutral language (Ask / Auto / restricted). The
  underlying `--dangerously-skip-permissions` CLI flag reference is
  kept intact (it's Anthropic's flag name).

## [0.4.22] - 2026-07-21

### Fixed
- **`aipager-hook` no longer balloons to multi-GB RSS on large
  transcripts.** `_origin_from_transcript` and
  `_turn_already_blocked` in `aipager/dtach/enforce.py` used to do
  `Path(path).read_text().splitlines()` on the ENTIRE transcript on
  every PreToolUse — allocating 1–2 GB per hook subprocess for a
  500 MB transcript. dmesg had shown hook OOMs at 767 MB, 1.3 GB,
  and 5.2 GB anon-rss inside container cgroups. Both functions now
  stream the transcript from EOF backwards via a new
  `_iter_lines_reversed` helper and short-circuit at the last real
  user prompt — typical work is a few KB instead of hundreds of MB.
  Multi-byte UTF-8 characters at chunk boundaries are buffered
  correctly (Persian/Arabic content preserved).

### Added
- **Hook subprocesses now cap their own address space at 1 GB
  (`RLIMIT_AS`).** Baseline is ~34 MB VmSize, so 1 GB gives ~30×
  headroom over realistic legitimate use while still catching true
  runaways (dies with MemoryError instead of eating host RAM). Users
  who previously deployed manual `ulimit` wrappers
  (`aipager-hook-capped.sh`) no longer need them. Applies to both
  `aipager-hook` and `aipager-statusline`.
- **Telegram notification when a hook subprocess trips the RAM cap.**
  Users see "⚠️ session · memory cap hit during `<tool>`" in the
  session's chat, so a silent dropped event no longer goes unnoticed.
  Uses a zero-allocation pre-opened socket + pre-serialized payload
  so the notification path survives even when no new memory can be
  allocated. Includes the in-flight tool name when stdin was parsed
  before the balloon (typical case); falls back to a bare "cap hit"
  message otherwise.

## [0.4.21] - 2026-07-21

### Fixed
- **Duplicate Telegram replies on upgrade when settings.json points at
  a wrapper script around `aipager-hook`.** `_has_hook_cmd` now
  recognizes any command whose basename starts with `aipager-hook`
  (catches user-deployed wrappers like `aipager-hook-capped.sh` used
  for ulimit / rate-limit / logging), so bootstrap doesn't
  re-inject a duplicate hook entry alongside them. Belt-and-braces:
  the hook receiver also drops identical `(session, event, payload)`
  events within `HOOK_DEDUP_WINDOW_SECONDS` (3 s default,
  env-overridable), so any future wiring variant that slips past
  detection still cannot produce user-visible duplicates.
- **Two `aipager start` invocations can no longer both start.** The
  existing socket-probe check has a millisecond-scale race window
  where two daemons can both pass the probe and the second's
  HookReceiver silently steals the socket via unlink+rebind. Added a
  fcntl advisory lockfile at `~/.local/share/aipager/daemon.lock`
  held for the daemon's lifetime — atomic, process-associated
  (auto-released on any exit including SIGKILL), no polling. Second
  daemon exits with a clear "aipager already running (pid=X)"
  message.

## [0.4.20] - 2026-07-21

### Fixed
- **"Dead placeholder" credentials files now get stashed so
  `CLAUDE_CODE_OAUTH_TOKEN` can take over auth.** 0.4.19 protected all
  empty-token files from being renamed, but that left genuinely dead
  placeholders — files where BOTH `accessToken` and `refreshToken` are
  empty strings — sitting in place and shadowing the env token, with
  Claude Code trying to refresh via the empty refresh token and
  failing 401. Observed in production on a Max-plan container where
  the credentials file had been cleared. The stash logic now also
  fires when both tokens are empty (no material to authenticate with,
  no refresh path). Refresh-token-only files (empty `accessToken` but
  non-empty `refreshToken`) are still left alone — Claude Code may
  still refresh successfully through them.

## [0.4.19] - 2026-07-21

### Fixed
- **The 0.4.18 credentials-file stash no longer touches placeholder
  files with empty tokens.** Observed on Max-plan containers:
  `~/.claude/.credentials.json` can hold only account-level metadata
  (`accessToken=""`, `refreshToken=""`, `expiresAt=0`,
  `subscriptionType="max"`) while Claude Code manages auth through a
  non-file path (device auth / account UUID / server-side session).
  Those files LOOK expired to the stash logic but are load-bearing for
  their auth setup. The stash now additionally requires a non-empty
  `accessToken` string before renaming — placeholder / cleared files
  are treated as opaque state and left alone.

## [0.4.18] - 2026-07-21

### Fixed
- **Interactive sessions no longer 401 when a stale
  `~/.claude/.credentials.json` shadows a valid
  `CLAUDE_CODE_OAUTH_TOKEN`.** 0.4.13 taught the launcher to keep the
  env token when the credentials file is expired, but Claude Code's
  interactive mode still prefers the on-disk file over the env token
  even when the file's `expiresAt` is in the past — resulting in
  "Please run /login" for the user despite a perfectly valid env token
  in the process environ. The launcher now atomically renames the
  expired file to `.credentials.json.stale` at session launch (only
  when the env token is present as a safe fallback), letting claude
  fall back to the env token. Idempotent, reversible (`mv` the
  `.stale` back after a successful `claude auth login`), and no-op on
  every other auth configuration (env-only, creds-fresh, creds-only).

## [0.4.17] - 2026-07-17

### Fixed
- **Telegram flood-control no longer wedges the daemon for hours.**
  A single `sendMessage` 429 with a pessimistic `retry_after` (Telegram
  can return values in the multi-hour range) used to trigger a bare
  `asyncio.sleep(retry_after)`, blocking every subsequent send behind
  it. `retry_after` is now capped at `TELEGRAM_MAX_RETRY_AFTER`
  (default 90 s, env-overridable) — over the cap, the send fails
  fast so the caller sees the failure.
- **Users now get a visible signal when their reply is dropped due
  to flood-control.** On give-up, aipager sets a 🚨 reaction on the
  Telegram message that couldn't be answered. The reactions endpoint
  is in a different flood-control bucket from `sendMessage`
  (empirically verified: while `sendMessage` was returning 429,
  `setMessageReaction` continued to return 200 OK on the same bot
  token), so this signal reliably reaches the user even during a
  full send-block.

## [0.4.16] - 2026-07-17

### Fixed
- **No more false "Stuck on Working for 2+ min" warnings during
  auto-compaction, extended thinking, or heavy first-response on huge
  contexts.** 0.4.12 covered long tool calls; this release covers the
  remaining gaps. The stale-busy detector now also stands down while
  `PreCompact` has fired without a matching post-compact `SessionStart`
  (capped at 30 min via `COMPACT_INFLIGHT_MAX_SECONDS`), and when the
  session's statusLine file was updated within `STATUSLINE_ALIVE_SECONDS`
  (60 s default) — a universal heartbeat signal for "session is doing
  something" that catches extended-thinking / rate-limit-backoff cases.
  Both env-overridable.
- **The warning text itself is now neutral.** Previous copy led with
  "Anthropic subscription / credit balance ran out — check your
  dashboard" which needlessly alarmed users when the session was just
  processing. New copy leads with benign causes (long tool call, heavy
  generation, compaction) and only mentions subscription/network at the
  end. Title changed from "Stuck on Working" → "Silent for X+ min".

## [0.4.15] - 2026-07-14

### Fixed
- **The tool-XML sanitizer added in 0.4.14 no longer eats legitimate
  code examples in markdown fences.** If the assistant explained
  Claude's tool-use format with a fenced block containing
  `<invoke>` / `<parameter>` / `<function_calls>` tags, 0.4.14 stripped
  the whole block and the user saw only the surrounding prose. The
  sanitizer now walks the text in triple-backtick-aware chunks — tags
  inside fences survive verbatim, tags in prose still get stripped.
  Empirical basis: 0/116 real leaks in production had XML inside
  fences, so fence-awareness loses nothing on the strip side.

## [0.4.14] - 2026-07-14

### Fixed
- **Leaked tool-invocation XML no longer bleeds into Telegram replies.**
  Long-context degradation on newer Claude models can cause the
  assistant to type its tool-use markup (`<invoke name="Bash">`,
  `<parameter>`, `<function_calls>`) as plain-text content instead of
  using structured tool_use blocks. When that happened, aipager's
  summary forwarded the raw XML verbatim — huge blocks of unreadable
  garbage in the chat. The assistant-summary path now scrubs those
  patterns at both source points (the transcript-fallback path and
  the hook-JSON primary path), so the user-visible reply stays clean
  even if the underlying model is misbehaving.

## [0.4.13] - 2026-07-11

### Fixed
- **Headless / setup-token installs no longer break with "Not logged
  in · Please run /login".** Every spawned session used to strip
  `CLAUDE_CODE_OAUTH_TOKEN` from its environment on the assumption
  that a fresh `~/.claude/.credentials.json` was authoritative. On
  headless hosts that use `claude setup-token` — where the env var
  IS the primary credential and the credentials file is missing or
  stale — this killed auth on every new session. The strip is now
  conditional on the credentials file being present and unexpired;
  otherwise the env token is kept intact. Reminder for headless
  operators: export `CLAUDE_CODE_OAUTH_TOKEN` in whichever process
  starts the daemon (systemd unit, docker run `-e`, or the shell
  running `aipager start`) so spawned sessions inherit it.

## [0.4.12] - 2026-07-11

### Fixed
- **No more false "Stuck on Working for 2+ min" alert during long tool
  calls.** The stale-busy detector fired whenever no hook event
  arrived for 2 minutes, but no hooks fire between a `PreToolUse` and
  its matching `PostToolUse` — so any single Bash / WebSearch / large
  fetch that ran longer than the threshold triggered the alarm with
  misleading advice about credit balances. The monitor now tracks
  tool-in-flight state and stands down for up to 15 minutes; genuinely
  wedged tools still surface at the cap. Env-overridable via
  `TOOL_INFLIGHT_MAX_SECONDS`.
- **`aipager config` wizard now lets you pick `owner` when adding a
  DM scope.** For single-tenant friend deployments (one friend per
  container, they own the whole thing), the correct role is `owner`,
  but the wizard was actively hiding that option — so every friend
  was silently created as `user` and had legitimate operations blocked
  by the safety floor. Default is still `user` so multi-user daemons
  don't accidentally grant bypass.

## [0.4.11] - 2026-07-10

### Fixed
- **Sessions no longer strand in BUSY when the Stop hook is missed.**
  Newer claude-code appends bookkeeping records (`last-prompt`,
  `ai-title`, `mode`, `permission-mode`) after the final assistant
  message. The idle-recovery detector treated them as unknown tail
  entries and refused to declare the turn finished, so the Telegram
  busy bubble animated forever. The tail-walk now skips any record
  without a `message` field — real turn entries always carry one —
  while unknown message-bearing types still conservatively count as
  turn-in-progress.

## [0.4.10] - 2026-07-10

### Fixed
- **/resume picker buttons now work for scoped session names.** The
  picker's callback derived the session label by stripping the
  `claude-` prefix from the internal name, which left the scope suffix
  (`name__d<chat_id>`) attached — so the resume lookup, which matches
  on the user-facing label, always failed with "No session named X in
  history". The callback now resolves the label from the registry
  entry, the same way the /new conflict handler already did. Typing
  `/resume <name>` by hand was unaffected.

## [0.4.9] - 2026-06-25

### Fixed
- **Don't show "Rate limit hit" when Claude is just talking about rate
  limits in its reply.** The error-detection regex matched any
  occurrence of "rate limit" / "rate-limit" / "ratelimit" anywhere in
  Claude's final-turn text, so casual prose like "Waiting on the
  NearBlocks rate-limit" triggered a false-positive warning bubble on
  Telegram even though the API call had succeeded. The matcher is now
  anchored on tokens that only appear in real Anthropic errors
  (`API Error: 429`, `HTTP 429`, the structured `rate_limit_error`
  token, and the canonical body "This request would exceed your
  account's rate limit"). Reported via Telegram on 2026-06-25.

## [0.4.8] - 2026-06-21

### Fixed
- **Auto-wire `aipager-hook` + `statusLine` into `~/.claude/settings.json`
  at daemon start.** Containerized / scripted installs that skip
  `aipager config` had no hooks configured in Claude Code, which meant
  the daemon never learned each session's transcript path or session id
  — so `/resume` always returned an empty picker, the Stop-hook BUSY→IDLE
  fast path couldn't fire, and PreToolUse safety enforcement didn't run.
  The boot-time bootstrap (introduced in 0.4.7) now also writes the same
  hook entries the wizard would write, idempotently. Skips silently when
  `aipager-hook` isn't on PATH (broken install — don't poison settings
  with a command Claude can't execute).

## [0.4.7] - 2026-06-21

### Fixed
- **Bootstrap Claude Code's first-run acceptance flags on `aipager start`.**
  Sessions launched with `--dangerously-skip-permissions` (from `/new !name`)
  used to die instantly on the first prompt: Claude shows a "WARNING: Bypass
  Permissions mode" confirmation picker that the user can't see or dismiss
  over Telegram, so their first message arrives as Enter on the default
  "No, exit" option. Likewise a fresh working directory triggers the
  "Do you trust this folder?" picker the same way. The daemon now idempotently
  writes `skipDangerousModePermissionPrompt: true` to `~/.claude/settings.json`
  and trusts the daemon's working directory in `~/.claude.json` at startup,
  so both pickers are pre-accepted by the time the first session launches.
  Affects users who deploy the container image or skip the wizard.

## [0.4.6] - 2026-06-20

### Fixed
- **Stop pinning spawned sessions to an inherited setup-token.** If the
  daemon was started with `CLAUDE_CODE_OAUTH_TOKEN` in its environment
  (the long-lived setup-token used to bootstrap containers), every
  dtach-spawned claude inherited that token and used it for the whole
  session, overriding any fresh credentials written by
  `claude auth login`. When the setup-token expired or its scope was
  too narrow, sessions died on the first API call. The launch wrapper
  now unsets `CLAUDE_CODE_OAUTH_TOKEN` (alongside the existing
  `unset CLAUDECODE`) so each session reads from `~/.claude/.credentials.json`.

## [0.4.5] - 2026-05-30

### Fixed
- **Recover stranded BUSY sessions when the Stop hook is missed.** If
  Claude's Stop hook never reached the daemon (e.g. you interrupted a
  pending permission and then immediately sent a new prompt), the
  affected session used to sit in BUSY forever — the "Thinking…"
  message animated indefinitely and new prompts queued behind it,
  making the bot appear unresponsive. The session monitor now reads the
  transcript every 2s and recovers a BUSY session to IDLE once the turn
  has clearly ended (assistant entry with a non-`tool_use` stop_reason,
  or a user interrupt marker) AND the transcript has been quiet for
  ≥`AIPAGER_IDLE_RECOVERY_GRACE` seconds (default 8), firing the same
  `idle_prompt` finalisation the hook would have done. Three guards
  (turn-complete + transcript quiet + busy-duration) ensure the normal
  Stop hook always wins on fast turns; a live turn is never cut short.

## [0.4.4] - 2026-05-22

### Security
- **Blocked Bash reads of aipager's own config/state dirs.** The
  path-deny boundary only inspected the Read/Glob/Edit tools, so a
  `user`-role Telegram session could `cat ~/.config/aipager/aipager.yaml`
  (or `~/.local/{share,state}/aipager/...`) via the **Bash** tool and
  exfiltrate the bot token + scopes — the dirs contain no "claude"
  substring, so the nested-claude pattern didn't catch them. Added bash
  patterns denying any command that references those dirs.

### Added
- **Opt-in real-Claude end-to-end safety suite** (`tests/e2e/`, run with
  `pytest -m e2e`) that reproduces a Telegram-driven turn without
  Telegram and verifies the safety boundary against real Claude —
  enforcement, sticky turn-block, origin/owner/admin handling, role
  deny/allow lists, clean session halt, and `/whoami`. Excluded from the
  default suite + CI.

## [0.4.3] - 2026-05-20

### Security
- **Fixed a safety-boundary bypass for Telegram-driven sessions.** Three
  issues let a non-owner work around the hard-safety boundary:
  - The hook detected prompt origin from the *last* `type:"user"`
    transcript entry, but Claude records tool-results as `type:"user"`
    too — so only the **first** tool call per turn was enforced; every
    later call was misread as terminal/unrestricted. Origin is now
    derived from the last genuine user prompt (tool-results skipped).
  - Even with that fixed, a blocked command could be reworded to dodge
    the matcher (e.g. a `cla*-code` glob). A block is now **sticky for
    the whole turn**: once any tool call is denied, every later tool
    call that turn is denied too, until the next user prompt.
  - The session now **halts cleanly** on a block (interrupt + spinner
    cancelled + back to IDLE) instead of letting Claude keep retrying.
  - Deny reasons no longer echo the matched regex (which an agent could
    read to craft a dodge); the pattern is logged server-side only.

### Fixed
- **Test suite passes on Python 3.10 / 3.11 again** (CI was red).
  A `_read_statusline` test subclassed `pathlib.Path`, which is
  unsupported on ≤3.11 (`AttributeError: ... has no attribute
  '_flavour'`); it now uses a Path factory like its sibling tests.
  Test-only — no runtime behavior change from 0.4.1.

## [0.4.1] - 2026-05-20

### Fixed
- **Pinned status dashboard no longer crashes without a single
  configured chat.** `_maybe_update_bot_name` did `int(CHAT_ID)`
  unconditionally; under multi-scope (v2 retires `config.env`, so
  `CHAT_ID` is empty) that raised `ValueError`. The single-chat pinned
  dashboard is now skipped whenever there's no single chat (multi-scope
  or empty `CHAT_ID`), mirroring the existing team-mode skip.

## [0.4.0] - 2026-05-20

### Multi-scope mode

One daemon now serves **multiple Telegram chats at once** — any mix of
1:1 DMs and group chats — with multiple users, per-user roles, and a
hard safety boundary around Telegram-driven sessions. The old
"personal vs team" split is gone: a solo install is just one DM scope,
and you grow into groups/extra people additively, never by switching
modes. Existing installs migrate automatically (see *Changed*).

#### Added
- **Scopes.** A *scope* is a Telegram chat (DM or group) plus its
  members. The bot serves every configured scope concurrently and
  keeps them isolated — `/status`, `/resume`, `/new`, the `/` command
  menu, and the keyboard each show **only the calling chat's**
  sessions. A label like `dev` can be reused across scopes without
  collision.
- **Roles + policy.** Built-in roles `owner` / `admin` / `user` /
  `read_only`, plus arbitrary **custom roles** with their own
  allow/deny lists, path rules, and bash patterns. Roles + safety live
  in a user-owned `~/.config/aipager/policy.yaml` (and `policy.d/*.yaml`)
  that the wizard **never overwrites**.
- **Hard safety boundary** for Telegram-driven tool calls, enforced at
  `PreToolUse`: no reading other users' transcripts or aipager's
  config, no nested `claude`, no `--append-system-prompt` / `--resume`
  flags, no `sudo` / `rm` on protected paths. Blocks are surfaced in
  chat ("🛑 Blocked by safety policy"). Terminal-driven sessions stay
  unrestricted; the `owner` role bypasses everything.
- **Per-user identity.** Free-text prompts are prefixed with a
  `[via Telegram · @label · role:…]` marker so Claude knows who is
  driving, and each session gets a `SESSION.md` roster read into its
  system prompt at launch.
- **`/whoami`** — shows your resolved member, role, and **effective**
  deny/allow list (the merged scope ∪ role ∪ per-user result).
- **`aipager doctor --safety-check`** — renders the active safety
  policy (protected paths, bash patterns, per-role flags).
- **`aipager policy validate`** — lints `policy.yaml` / `policy.d`
  (unknown keys, bad regexes, undefined role references) without
  mutating anything.
- **Scope-attributed audit trail.** Each inbound action records who,
  what, in which scope, and whether it was denied (+ reason); owner
  safety-bypasses are flagged. Service logs prefix each event with its
  scope label. The audit log stays operator-only (never sent to chat).
- **Resilient wizard.** Adding a group is incremental — each member is
  drafted to `~/.config/aipager/.wizard-draft.json` as you go, so a
  crash or Ctrl-C offers a Resume/Discard on the next run and never
  loses already-committed scopes.

#### Changed
- **`aipager config` rebuilt around scopes.** First run asks **no mode
  question** — it connects you to your bot, auto-captures your DM, and
  (after an explicit confirmation) makes you `owner`. No `policy.yaml`
  is created on a solo install. Re-running opens a scope list editor:
  add a group / add a person, edit scopes + members, test reachability,
  and a read-only **View policy**.
- **Config format v2.** The wizard now writes
  `~/.config/aipager/aipager.yaml` (bot token + scopes + members).
  Existing v1 installs (`config.env` / `team.yaml`) **migrate
  automatically** on the first daemon start after upgrade — the old
  files are backed up, then retired once v2 loads cleanly. Running
  `aipager config` on an un-started v1 install upgrades it in place.
- **Session enumeration is scope-bounded** — no command surfaces
  another scope's sessions, and the `/` autocomplete is registered
  per-chat via `BotCommandScopeChat`.

#### Security
- Closes the Telegram-driven **self-modification** and **cross-user
  transcript snooping** risks: a non-owner Telegram session can no
  longer read `~/.claude/**`, edit aipager's config, resume another
  user's session, or escalate via a nested `claude` invocation. The
  `owner` grant (full god-mode from Telegram) is gated behind an
  explicit wizard confirmation and audit-logged.

#### Removed
- The **personal / team mode toggle**. Both are now expressed as
  scopes (a DM scope with one member, or a group scope); they coexist.

#### Known limitations
- **Observer bots are a global firehose** — they mirror events from
  every scope with no per-scope filtering. Configure one only if you
  trust it to see all scopes.
- Safety is **pattern-based on a shared filesystem**, not a container
  sandbox — adequate for the trust model (operators allow-list people
  they'd hand shell access), but not a hard kernel boundary.

## [0.3.19] - 2026-05-18

### Fixed
- **Multi-line `friendly_warn` panels no longer collapse off-screen.**
  Long warnings (e.g. the "couldn't resolve @handle" hint) are now
  passed to `warn_block` as separate lines so Rich renders them as a
  proper multi-row body instead of a single-line panel title that
  overflows the terminal width.
- **Manual add-user has Retry / Switch-to-auto / Cancel choices on
  failure** (mirroring the auto-detect path). Admins can bail out of
  a stuck `@handle` resolution loop without abandoning the whole
  wizard.

### Changed
- **First-run flow saves `config.env` right after the chat-id step**
  (was: at the very end). A Ctrl+C anywhere afterwards keeps token
  + chat-id intact; re-running `aipager config` falls into the edit
  flow instead of restarting from scratch.
- **`team.yaml` is now written incrementally** — after every
  successful user-add and after the deny-rules picker. Partial
  team-mode setup survives Ctrl+C; re-entry's current-config panel
  shows what's already been saved.
- **`aipager config` edit menu gains "Re-install Claude Code
  hooks"** — exposes the existing `settings.json` patch step so
  admins who bailed out before that ran can complete the wiring
  without re-doing the whole setup.

## [0.3.18] - 2026-05-18

### Changed
- **Clearer hints when `@handle` add-user fails.** The failure
  message now explains the Telegram constraint (no
  username → user_id lookup) AND tells the admin both ways the
  user can become resolvable: DM the bot (tap /start) or mention
  the bot in the group. Same updated wording in the auto-detect
  prompt.

## [0.3.17] - 2026-05-18

### Fixed
- **`@handle` add-user actually works.** 0.3.16's `@handle`
  resolution relied on Telegram's `getChat?chat_id=@username`,
  which only resolves channels / supergroups — not individual
  users. The wizard now falls back to scanning recent
  `getUpdates` for a message whose `from.username` matches.
  Works for any group member who's sent at least one message
  the bot has seen (which in a group with privacy-on, includes
  any mention of the bot or reply to one of its messages).
  Failure message now points at the real fix ("ask the user to
  send any message in the group, then retry").

## [0.3.16] - 2026-05-18

### Changed
- **`aipager config` add-user accepts `@handle` or numeric id.**
  Paste either `12345` or `@arian_hamdi` (or bare `arian_hamdi`)
  in the manual flow — the wizard resolves via Telegram's
  `getChat` and shows `✓ Resolved @arian_hamdi → id=12345`.
  Non-private chats (channels, bots) are rejected with a clear
  message.
- **Label is now optional in the add-user flow.** The prompt
  defaults to the resolved Telegram username (lowercased). Hit
  Enter to accept, or type a custom label. Same default behaviour
  applies in both the manual and auto-detect paths.

## [0.3.15] - 2026-05-18

### Fixed
- `aipager config` step `[2/?]` (Personal vs Team picker) crashed
  with `Invalid 'default' value passed` because the wizard passed
  the choice **title** as the `questionary.select` default instead
  of the matching `value`. The picker now correctly defaults to
  Personal. First-run installs on 0.3.14 hit this every time —
  upgrade to 0.3.15 to get past step 2.

### Changed
- `docs/architecture.md` mermaid diagram syntax fixed. The
  bidirectional-with-label edge (`Sock <-- datagram -- Hooks`) was
  malformed and mermaid v11 surfaced it as a "Syntax error in
  text" banner on the docs site. Cleaned up `<br/>` → `<br>` and
  removed HTML entities that don't survive react-markdown's
  passthrough.

## [0.3.14] - 2026-05-18

### Added
- **Live reload of `team.yaml` via SIGUSR1.** Add-user / remove-user
  / change-role / edit-rules / switch-to-personal all signal the
  running daemon to re-read `team.yaml` without restarting — no
  more disrupting active sessions to tweak the allow-list. Manual
  `kill -USR1 $(pgrep -f 'aipager start')` works too. Malformed
  reloads log a WARN and keep the previous in-memory team, so a
  typo can't lock you out.
- **Auto-detect Telegram user IDs** in the add-user flow. Pick
  Auto-detect → ask the new member to mention `@bot` → wizard
  captures their id + Telegram handle and suggests the handle as
  the default label. Skips the "ask your teammate to look up their
  user id" round-trip.
- **`aipager config` edit menu.** Re-running `aipager config` on
  an existing install no longer overwrites everything. The wizard
  detects existing config and opens an edit menu: add / remove a
  user, change a user's role, edit `deny_tools` rules, switch
  between Personal and Team modes (with an archived backup of the
  old `team.yaml`), refresh the bot token, or run the full setup
  again. First-run flow is mode-first now — Personal vs Team is
  picked right after token verification so the chat-id prompt asks
  for the right kind of id (group vs DM), no more double-prompting.
- **`aipager doctor` team check.** New `check_team` validates
  `team.yaml` against `CLAUDE_TG_CHAT_ID`, warns when no admin is
  present or `rules.deny_tools` is empty, and FAILs when the
  configured chat id doesn't match the team's group id (the
  daemon would otherwise filter every message away as off-chat).
- **Team / group mode.** Configure
  `~/.config/aipager/team.yaml` (via `aipager config` → Team) to
  run the bot in a Telegram group with multiple developers.
  Allow-list of Telegram user IDs gates every action; roles
  (`admin`, `developer`, `read_only`) define what each user can do;
  optional `rules.deny_tools` auto-rejects denied tools without
  prompting (unless the session's last driver is an admin). All
  permission decisions land in the audit log with the deciding
  user's identity (`user_id`, `username`, `display_name`). Setup
  wizard surfaces a hard-stop warning before team mode is enabled —
  adding a user grants them code-execution rights on the host.
  Personal-mode installs (no team.yaml) are unaffected. See
  [docs/groups.md](docs/groups.md).

## [0.3.13] - 2026-05-18

### Added
- **Docker image** at `ghcr.io/dev-aly3n/aipager` — self-contained
  workstation (python + node + `claude` + `dtach` + aipager) for
  cloud / NAS / Pi deployments. Multi-arch (amd64, arm64). Built and
  pushed on every release tag. Mount `~/.claude` + a config volume
  + your workspace; see README.
- **Reference docs** under [docs/](docs/) — architecture (with a
  Mermaid component diagram), hook event reference, bot command
  reference, troubleshooting runbook, security model. The upcoming
  `aipager.run/docs` site renders directly from these files.
- **Nix flake** at the repo root. `nix run github:dev-aly3n/aipager`
  builds aipager from source against pinned nixpkgs (Python 3.12 +
  python-telegram-bot + rich + questionary + system dtach). Suitable
  for NixOS / nix-darwin / nix-on-Ubuntu setups. `claude` and
  optional voice extras stay out-of-tree.
- **Arch User Repository** — `yay -S aipager`. PKGBUILD lives at
  [`packaging/aur/`](packaging/aur/); same file mirrors to
  `aur.archlinux.org/aipager.git` per release. System `dtach` and
  `python-telegram-bot` come from pacman; `claude` installs via npm
  separately.
- **Snap** — `snap install aipager`. Strict-confinement snap that
  bundles python + node + `claude` + `dtach` + aipager into one
  package, so the daemon and claude code share the same sandbox.
  Workspace must live under `~/`. Manifest at
  [`packaging/snap/`](packaging/snap/).

## [0.3.12] - 2026-05-18

### Added
- **Voice messages → transcript → injected prompt.** Send a voice
  message in Telegram, the daemon downloads the .ogg, runs
  `faster-whisper` locally (no cloud, no API key) and injects the
  transcript into the active session as if you'd typed it. Shipped
  behind an optional `aipager[voice]` install extra — default install
  is unchanged (~3 MB on disk). Adding the extra pulls
  `faster-whisper`, `ctranslate2`, `onnxruntime`, `numpy`, `av`,
  `tokenizers` and `huggingface-hub` (~200 MB total on disk) plus a
  one-time ~74 MB model download on first use (cached under
  `~/.cache/huggingface/hub/`). Tunable via `AIPAGER_WHISPER_MODEL`
  (default `base`).
- **Install the voice extra from Telegram.** When a user sends a
  voice message and the extra isn't installed, the bot replies with
  inline `[📦 Install voice] [Cancel]` buttons. Tapping Install runs
  `uv tool install --reinstall 'aipager[voice]'` (or the pipx
  equivalent), streams progress back as message edits, and follows
  up with a `[🔄 Restart daemon now]` button. For Homebrew,
  editable and unknown installs the button falls back to
  `python -m pip install --upgrade faster-whisper` into the daemon's
  Python interpreter. The restart button always works — service
  units use systemctl / launchctl; everyone else spawns a detached
  replacement and SIGTERMs the current daemon so it picks up the
  new module without terminal access. Lets the user enable voice
  from their phone without SSH access. Same Telegram chat-id filter
  — only the configured user can trigger.
- **Write / Edit diff preview in Telegram.** When claude calls
  `Write` or `Edit`, the daemon sends a separate message threaded
  under the busy message with a unified diff of the change
  (rendered inside `<pre><code class="language-diff">` so Telegram
  colors `+` lines green and `-` lines red on supported clients).
  Output capped at 30 lines / 2000 chars with a "…and N more lines"
  footer. Fire-and-forget — failures fall back to the existing
  tool-history summary. Disable with `AIPAGER_DIFF_VIEW=0`.
- **Customizable keyboard layout** via
  `~/.config/aipager/keyboard.json` (optional file). Each section
  (`templates`, `commands`, `models`) overrides the corresponding
  default; missing sections fall through to the built-ins so
  partial overrides work. Malformed JSON or wrong-shape entries
  fail open — daemon logs a warning and keeps using defaults so the
  keyboard never goes blank. Changes require a daemon restart.
  Schema in the README:
  ```json
  {
    "templates": [{"label": "Deploy", "prompt": "Deploy to staging"}],
    "commands":  [{"label": "Compact", "send": "/compact"}],
    "models":    [{"label": "Sonnet",  "send": "/model sonnet"}]
  }
  ```
- **Live cost delta in the busy message.** The "Working…" header
  now appends `· 💰 $0.04` (and `(N agents)` if subagents fired this
  turn) so you can see the cost of the *current* claude turn at a
  glance, refreshed via the existing busy-message edit loop. Reset
  on every BUSY transition so the number is "this turn", not
  lifetime.
- **Multi-session pinned status.** The pinned message at the top of
  the Telegram chat now shows every live session, not just the most
  recently active one. Top line = currently active (model · context%
  · cost), additional lines list the others with their status
  (idle/busy/waiting) so a power user with 3-5 sessions has a
  proper dashboard pinned at all times.
- **Subagent count rollup.** When a session spawned subagents this
  turn, `(N agent)` / `(N agents)` is appended to the cost display
  (busy message and IDLE summary). Helpful to spot expensive
  delegation patterns. Claude doesn't expose per-subagent cost
  breakdowns in the statusline payload, so we count subagents
  instead — the cost itself already includes everything they did.
- **Audit log on disk** at `~/.claude/aipager-audit.jsonl`. Every
  Allow / Deny / Continue tap and every `AskUserQuestion` submit
  appends one JSON record with ISO timestamp, session, action, tool,
  and summary. Best-effort write — if the disk is full or the path
  is unwritable the daemon logs at WARNING and keeps running. Pair
  with the in-chat audit reply added in 0.3.x for a complete trail:
  one record on disk, one message in chat per decision.
- **Audit reply in chat after Allow / Deny.** When you tap Allow,
  Deny, Continue, or answer an `AskUserQuestion`, the bot now leaves
  a small reply threaded under the busy message:
  `✅ [jim] · Allowed · Bash: ls -la /tmp`. Scrolling back tells you
  exactly which permission decisions you made on which session.
- **`/clearqueue` Telegram command.** Drops every queued prompt for
  the currently active session without interrupting the running task
  (which `/stop` would). Replies with the count cleared, or
  "Nothing to clear" when the queue is already empty.
- **Truncation hint footer.** When the IDLE response is long enough
  to spill into a `.txt` attachment, the inline summary now ends with
  `📎 Full response attached below ↓` so the user doesn't miss the
  attachment.
- **Real retry-after seconds.** `_detect_api_error` now extracts
  `retry-after`/`wait X seconds`/`X second cooldown` hints from
  Anthropic rate-limit errors. The friendly message reads
  "Rate limit hit. Wait 60s before retrying." instead of the generic
  "Wait a moment".
- `pending_queue` for each session is now capped at 50 entries. When a
  session is BUSY and the user sends a 51st message, they get back
  `⚠️ Queue is full (50 pending) for [jim]. Tap stop or wait for the
  current task to finish.` instead of the daemon silently growing the
  in-memory queue forever. Applies to text replies, file uploads,
  template injections, and `/new <name> <initial prompt>`.
- Queue entries now carry a wall-clock timestamp; entries older than
  24 h are dropped at daemon-load time (so a daemon down for days
  doesn't suddenly flush stale prompts when a session goes IDLE).
- INTERACTIVE-state watchdog: if a session sits in INTERACTIVE with no
  hook activity for >5 min (tunable via `AIPAGER_INTERACTIVE_TIMEOUT`
  env var, in seconds), the session_monitor auto-demotes it to BUSY
  and clears `pending_permission`. Catches the case where Claude Code
  crashed mid-permission-prompt and the user can never respond.
- Subagent garbage collection: entries in `active_subagents` whose
  Stop hook never arrived are dropped after 1 h
  (`AIPAGER_SUBAGENT_TTL`).
- `TruncationFailed` sentinel exception raised by `_send_with_retry`
  after 2 unsuccessful truncations on a "message too long" response;
  the IDLE-notification path catches it and falls back to a document
  send. Closes a theoretical infinite-loop where HTML entity
  expansion could make truncated text exceed the limit again.

### Changed
- **`/kill <label>` now requires a two-tap confirmation.** Sends
  `⚠️ Kill session [jim]? This will terminate the running claude
  process.` with inline `[💀 Kill]` / `[Cancel]` buttons instead of
  destroying the session immediately. One mistype on a phone no
  longer wipes a session. The implicit-confirmation flow when
  `/kill` is sent with no label (which shows a picker) is unchanged.
- **File-too-big upfront warning.** Files larger than the Telegram
  bot API's 20 MB download cap are now rejected with a friendly
  `⚠️ File is X MB. The Telegram bot API caps file downloads at
  20 MB.` message before the daemon attempts the download, instead
  of failing with a vague "Failed to download file".
- `tool_history` now caps at 200 entries per session. Older entries
  are dropped from the front on each append, and any `history_idx`
  reference stored in `active_subagents` is shifted accordingly so
  subagent bookkeeping stays correct after trimming.
- `_send_busy_and_animate` is now serialized per session via an
  `asyncio.Lock`. Closes the race window where two concurrent callers
  (e.g. a Telegram message handler and a `UserPromptSubmit` hook
  arriving within microseconds) could both pass the `busy_msg_id is
  None` check and both send. The synchronous-sentinel pattern is kept
  as a fast-path defence inside the lock.
- `_handle_callback` now eagerly acknowledges Telegram callback
  queries with an empty `query.answer()` before any async work. Long
  handlers no longer cause the inline-keyboard spinner to hang for
  seconds; all subsequent `query.answer(text)` toast calls go through
  a `_safe_answer` helper that swallows
  `BadRequest("query is too old")` if Telegram already considered the
  query answered.
- `TelegramBot.stop()` now cancels and awaits every running
  per-session animation task before tearing down the python-telegram-bot
  Application, eliminating "Task was destroyed but it is pending"
  warnings on shutdown.
- `recover_sessions` (which cleans up orphaned BUSY messages after a
  daemon restart) now distinguishes failure modes instead of
  swallowing every exception with `except Exception: pass`. Outcomes
  per session: `edited` (success), `vanished` (user deleted the
  message — Telegram says "message to edit not found"), `too_old`
  (>48 h since the message was sent — Telegram refuses edits with
  "message can't be edited"), `blocked` (bot was blocked by the
  user — stops retrying remaining sessions), `flooded` (transient
  Telegram rate-limit — skipped, next hook will refresh the BUSY
  message anyway), or `error:<short>`. A single summary line lands
  in the daemon log per startup, e.g.
  `recovered 3 sessions: 2 edited, 1 vanished`, so `aipager logs`
  shows the outcome of the most recent restart at a glance.

### Added
- New `aipager update` subcommand. Auto-detects whether aipager was
  installed via uv tool, pipx, or Homebrew (in that order) and runs
  the matching upgrade command. uv path passes `--refresh` to force
  the PyPI index cache to refresh (which has bitten users minutes
  after a new release). Friendly error when no known installer is
  in charge (e.g., `pip install --user` setups).
- New `aipager uninstall [-y|--yes]` subcommand. Stops the daemon
  (service or foreground), removes `~/.config/aipager`,
  `~/.claude/aipager-sessions.json`, `/tmp/aipager.sock`, all
  `/tmp/claude-dtach-*.sock` and `/tmp/claude-status-*.json`, then
  uninstalls the binary via the installer that owns it. Macros only:
  also removes `~/Library/LaunchAgents/com.aipager.daemon.plist`
  and `~/Library/Logs/aipager.log`. Does **not** touch Claude Code's
  `settings.json` or any `.bak.*` backups. Confirms by default; `-y`
  skips the prompt.
- New `aipager session ls` (alias `session list`) subcommand. Lists
  live dtach sessions with their status, model, context %, cost, and
  queue depth. Default hides GONE sessions; `-a` / `--all` includes
  them. `--json` for scripts. Shares its renderer with `aipager
  status`.
- New `aipager session kill <name>` subcommand. Terminates the
  matching dtach session. Confirms by default; `-y` / `--yes` skips
  the prompt. Friendly error when the named session doesn't exist.
- `aipager session` now reserves `ls`, `list`, `kill` as subcommand
  verbs — `_validate_name` rejects them with a clear message so
  collisions can't happen.
- New top-level `aipager status` subcommand. Prints a fast (<100 ms)
  snapshot of the daemon (up/down + bound chat), every known session
  (label, status, model, context %, cost, queue depth), and the
  aggregate cost. Rich table when stdout is a TTY, padded plain text
  otherwise, and `--json` for scripts. All data comes from local
  files (`/tmp/aipager.sock` probe, `/tmp/claude-dtach-*.sock`,
  `~/.claude/aipager-sessions.json`, `/tmp/claude-status-*.json`) —
  no Telegram API calls. Exit codes: 0 daemon up, 1 daemon down,
  2 config missing.
- New top-level `aipager logs [-f|--follow] [-n N|--lines N]`
  subcommand. Tails the daemon's journald entry on Linux or
  `~/Library/Logs/aipager.log` on macOS, with `tail`-style flags.
  Default shows the last 100 lines and exits; `-f` follows after the
  initial dump.
- When no log source is reachable (service not installed, daemon
  running in a foreground terminal), `aipager logs` and
  `aipager service logs` now print a friendly hint pointing at
  either `aipager service install` or a manual redirect
  (`aipager start > ~/aipager.log 2>&1 &`).

## [0.3.11] - 2026-05-17

### Added
- Two new Commands-submenu buttons:
  - **Init** (`/init`) — generates `CLAUDE.md` for a fresh repo.
  - **Security review** (`/security-review`) — scans the pending diff
    for vulnerabilities. Designed for remote one-tap review.
- Three new Templates-submenu buttons:
  - **Write tests** ("Write tests for the changes")
  - **Explain plan** ("Explain your plan before making changes")
  - **Update memory** ("Update CLAUDE.md with what you learned")
- README footnote explaining that the Model submenu's `sonnet` /
  `opus` / `haiku` / `opusplan` aliases resolve to different versions
  on Bedrock and Vertex than on the Anthropic API.

### Fixed
- `TypeError: type NoneType doesn't define __round__ method` from the
  statusLine hook when Claude Code's payload contains explicit
  `"context_pct": null` (early ticks before tokens have been counted).
  `dict.get(key, 0)` only substitutes the default when the key is
  missing — an explicit null falls through. Now guarded with
  ``msg.get("context_pct") or 0`` (same fix for `total_output`,
  `lines_added`, `lines_removed`).
- Fresh-install bots no longer show stale `/jim` / `/john` (etc.) in
  Telegram's slash-command menu. Telegram caches `setMyCommands`
  server-side per bot token, so a daemon that ran against this bot
  earlier could leave session-named slash commands behind even after
  full reinstall. The daemon now force-syncs commands on its first
  startup of each run, clearing any stale entries.
- The persistent keyboard (Templates / Commands / status / stop /
  kill) now appears immediately when the daemon starts with no
  sessions yet, instead of only after the first session is created.
  Caused by the same short-circuit — both symptoms had one fix.

## [0.3.10] - 2026-05-17

### Changed
- **Breaking:** `aipager session` no longer accepts the aipager-specific
  shortcuts `-y` and `--resume`. Pass claude's own flags through the
  REMAINDER instead — they were always supported there, the shortcuts
  were just a confusing parallel vocabulary:
  - `aipager session jim -y` → `aipager session jim --dangerously-skip-permissions`
  - `aipager session jim --resume` → `aipager session jim --continue`
  - Native claude flags like `--resume <session-id>` now work without
    colliding with aipager's own `--resume`.
- Telegram `/new` no longer defaults to `--dangerously-skip-permissions`.
  By default the new session runs with claude's normal safety checks.
  Prefix the name with `!` to opt in (e.g. `/new !dev fix the bug`).
  Matches claude's native behavior; the launch status message shows
  `(unsafe)` when the flag was used so you can tell at a glance.

### Fixed
- Replies to a session's bot message could be silently dropped or routed
  to the wrong session. Three causes, all fixed:
  - **Untracked busy/Thinking messages.** Only IDLE response messages
    were registered in the routing map; busy and dashboard messages
    weren't, so replying to them didn't find the source session.
    `_send_busy_and_animate` now calls `track_message` after sending.
  - **Text-recovery fallback for old messages.** Replies to bot
    messages that are no longer in the in-memory map (after a restart
    or after the cap evicts them) are now matched by scanning the
    message text for a known session label
    (`"⚙️ jim · Thinking…"`, `"📌 jim · …"`, `"[jim] · …"`, etc.).
    Only an unambiguous single match counts; otherwise we fall back to
    the last-active session.
  - **Silent drop.** When no session could be resolved at all, the
    daemon used to `return` without any feedback. It now sends
    `⚠️ I don't know which session this is for. Pick one with /<label>
    or the keyboard.` so the user knows the message wasn't lost in
    space.
- Bumped the persistent message-id cap (`_MAX_MSG_MAP`) from 100 to
  1000 so the lookup map survives longer conversations.

### Added
- `aipager help` subcommand. Bare `aipager help` prints the same
  top-level usage as `-h`, and `aipager help <subcommand>` (e.g.
  `aipager help session`) prints that subcommand's specific help.
  Unknown topics fail with a friendly listing of available
  subcommands. Closes a small DX gap where users typed `aipager help`
  out of habit and got an argparse parse error.

## [0.3.9] - 2026-05-17

### Added
- New `aipager.ui` module — single source of truth for console output,
  theme, and TTY/color detection. Backed by `rich`. Honors `NO_COLOR`,
  `FORCE_COLOR`, `CLICOLOR=0`, `CLICOLOR_FORCE`, and `TERM=dumb`.
  Daemon and hook scripts keep their plain logging untouched so
  journald and Claude-Code stdout stay scrapeable.
- New dependencies: `rich >= 14, < 16` and `questionary >= 2, < 3`.
  Combined disk footprint ~2.5 MB; both pure Python.

### Changed
- All user-facing errors and warnings now render as **bordered panels**
  in red/yellow when stdout is a TTY, with the issue-tracker link
  highlighted as a clickable path. Off-TTY (CI, logs, pipes) they
  degrade to the same plain-text block as before, so the existing
  test assertions and log-scraping patterns keep working.
- `aipager doctor` renders the check list as a **rich table** with
  coloured ✓/⚠/✗ markers, a "Suggested next steps" list of fixes, and
  a footer summary (`7 ok · 1 warn · 1 fail`). Falls back to padded
  plain text off-TTY.
- `aipager config` is **redesigned around `questionary`**: each prompt
  shows a cyan `?` glyph and is rewritten in place to a green `✓
  Question … Answer` line after commit, matching the
  `create-next-app` / `pnpm init` aesthetic. The chat-id step is now
  an arrow-key choice ("Auto-detect" vs "Paste manually") instead of
  the press-Enter-or-paste convention. Long-running Telegram API
  calls (`getMe`, `getUpdates`, `sendMessage`) are wrapped in dotted
  spinners so the terminal never appears frozen. Setup completes with
  a green-bordered panel showing the three next commands
  (`aipager start`, `aipager session dev`, `aipager doctor`).
- `aipager session <name>` now shows a `→ starting <session>` step
  line, a "spawning dtach + claude…" spinner during launch, a
  "waiting for socket to appear…" spinner during the post-spawn
  poll, and a green `✓ session ready` line before attach. Reattach
  prints a single dim `→ reattaching to <session>` instead of the
  prior plain text.
- `aipager service install` now prints a `Installing aipager.service
  (systemd-user)` step header, then a green ✓ line for each
  checkpoint (wrote unit, daemon-reload, enable+start). The
  post-install summary lines are dim-prefixed (`status:`, `logs:`,
  `stop:`) so the actionable command is the focal point.

### Added
- New `aipager doctor` subcommand prints a ✓ / ⚠ / ✗ health-check
  table covering: Telegram config, bot-token validity, chat
  reachability, `claude` and `dtach` binaries, hook scripts on PATH,
  `~/.claude/settings.json` schema, daemon liveness via a socket probe,
  and whether the systemd/launchd service unit is installed. Each
  failing row prints a one-line suggested fix. Idempotent — never
  sends Telegram messages or mutates configuration.
- New module `aipager.errors` centralizes user-facing error formatting:
  `friendly_error()` for ✗ blocks, `friendly_warn()` for ⚠ blocks,
  `install_excepthook()` to catch uncaught exceptions with a
  bug-report URL, and `with_friendly_errors` decorator translating
  common `PermissionError` / `OSError` flavors into actionable messages
  with the affected file path. Every unexpected error now points to
  https://github.com/dev-aly3n/aipager/issues for follow-up.
- `aipager-hook` and `aipager-statusline` honor `AIPAGER_DEBUG=1` —
  set it to log otherwise-silent socket/JSON errors to stderr for
  troubleshooting. Default behavior (silent) is unchanged.

### Changed
- `aipager start` now pre-flights Telegram connectivity (calls
  `getMe` and `getChat` over plain HTTPS with a 15 s timeout) before
  spawning the async daemon. Failures exit with code 2 and an
  actionable message: HTTP 401 → "re-run `aipager config`", "chat not
  found" → "tap Start in https://t.me/<bot>", network errors → "check
  your connection". Previously these surfaced as raw async tracebacks.
- `aipager start` detects an existing daemon on `/tmp/aipager.sock`
  (via UDP probe) and aborts with a clear message if one is already
  listening, instead of silently racing with it. Stale socket files
  with no live owner are unlinked transparently.
- `aipager start` now logs a one-line startup banner
  ("connected as @yourbot, will message chat <id>") so it's obvious
  which bot the daemon is bound to.
- `aipager session <name>` validates the session name
  (`[A-Za-z0-9_-]{1,50}`) before doing anything, so spaces, slashes,
  and 200-character names fail fast with a clear message instead of
  cryptic ENOENT from a too-long socket path. The launcher also
  probes the dtach binary (`dtach -V` style health check) and the
  socket (`AF_UNIX` connect probe) before reattaching, so stale
  sockets left by a crashed daemon are cleaned up instead of causing
  `dtach -a` to hang.
- `aipager session` captures and surfaces dtach's stderr / stdout
  on launch failure (instead of "dtach failed to start session" with
  no detail) and runs `claude --version` to diagnose the case where
  the socket never appears.
- `aipager service install` aborts cleanly when systemd-user isn't
  available (container, WSL1, minimal distro) and on macOS when
  `launchctl` isn't on PATH, suggesting `aipager start` under tmux/
  screen instead. The Linux installer also warns when
  `loginctl enable-linger` hasn't been run (service would die at
  logout), backs up existing unit/plist files before overwriting, and
  probes the daemon socket two seconds after enable to detect a
  daemon that came up but crashed.
- `aipager service start/stop/status/logs` precheck that the unit
  file exists and tell the user to run `aipager service install` if
  not, instead of relaying systemctl's "unit not found" error.
- `aipager service` now captures stderr from every `systemctl` /
  `launchctl` invocation and relays it on failure so users see *why*
  a command failed.
- `aipager config` token paste handles surrounding quotes, leading
  "Use this token: …" prefixes, trailing colons, and embedded
  whitespace via a canonical-token regex. HTTP errors from Telegram
  are categorized: 401 → "rejected the token", 404 → "URL is
  malformed", 429 → "rate-limiting", 5xx → "API error, retry";
  pre-HTTP errors (DNS, connect) read "can't reach
  api.telegram.org". `getUpdates` auto-detect distinguishes
  group-chat-only activity from no-activity and prompts the user to
  DM the bot directly. The "chat not found" retry trigger now uses a
  regex tolerant of casing and punctuation variants.
- `aipager config` validates `~/.claude/settings.json` schema before
  mutating it (rejects `hooks` of the wrong type instead of crashing
  with `AttributeError`), explains how to fix JSONC-style comments,
  resolves `aipager-hook` / `aipager-statusline` paths and aborts if
  they aren't on PATH (avoids silently writing broken absolute paths),
  prompts for confirmation before overwriting an existing config with
  a different token, and asks for a `[y/N]` to continue when `dtach`
  or `claude` is missing instead of silently completing a broken
  setup. The wizard also confirms the test-send arrived in Telegram
  before moving on, skips the settings.json backup when the merge
  would be a no-op, and tolerates filesystems that don't support
  `chmod 0600` (warns instead of crashing).
- Daemon's Telegram send paths now treat "Forbidden / bot was
  blocked" as a known failure: a friendly multi-line log explaining
  how to unblock, throttled to one entry per minute so the daemon
  log doesn't flood. The IDLE-response path uses a new
  `_send_with_retry` helper that handles `RetryAfter` and falls back
  to a 4 KB truncation when Telegram says "message is too long".
  Outgoing documents larger than 40 MB are skipped with a one-line
  warning instead of failing the send.
- `cli.py main()` installs a global `excepthook` so any uncaught
  exception is rendered as a friendly block with a link to the issue
  tracker, instead of a raw Python traceback.
- `aipager.preflight` reuses the shared `errors` module's
  formatter — same output, single source of truth.

### Note
- `aipager config` final-step hint mentions only `aipager start`
  (the service flow is still documented in the README).

## [0.3.7] - 2026-05-17

### Added
- `/start` and `/help` commands in the Telegram bot now return a
  friendly welcome message with the list of tracked sessions and
  usage hints, instead of the previous `⚠️ Unknown session: start`.
- `aipager config` now verifies the bot can actually reach your chat
  by sending a test message. If Telegram replies with "chat not
  found" (the user hasn't tapped Start on the bot yet), the wizard
  prints a precise instruction and waits while the user opens the
  bot in Telegram, then retries automatically.

### Changed
- When the daemon fails to send to the configured chat with "chat
  not found", the log now points at the fix
  (`open https://t.me/<bot>`) instead of dumping a 30-line traceback.

## [0.3.6] - 2026-05-17

### Fixed
- Bumped HTTP connect timeouts (10 s → 30 s) so the daemon's initial
  `getMe` call survives slow TLS handshakes through HTTPS proxies or
  VPN tunnels. Affected users got an unhelpful `httpx.ConnectTimeout`
  during `aipager start` on networks where curl with 30 s succeeded.
- `aipager config`'s urllib timeout also bumped 10 s → 30 s for the
  same reason — the wizard's `getMe` could spuriously fail on first
  attempt and report "Token invalid or Telegram unreachable" when the
  real issue was proxy latency.

## [0.3.5] - 2026-05-17

### Fixed
- `aipager config` no longer falsely reports `✗ dtach not on PATH` in
  pipx / uv-tool / brew-venv layouts where the bundled binary lives
  inside the venv but isn't on the shell's PATH. The check now uses
  `dtach_bin.path()` (which knows about the venv layout) before
  falling back to PATH.
- `aipager config` final-step hint corrected: was `claude-dtach dev`,
  now says `aipager session dev`. The `claude-dtach` console script was
  removed in 0.3.2 and the stale hint slipped through.

## [0.3.4] - 2026-05-17

### Added
- Pre-flight checks for `aipager start`, `aipager session`, and
  `aipager service install`. Subcommands now fail fast with friendly
  multi-line error messages (exit code 2) when:
  - Telegram bot token or chat ID is missing
    → `aipager config`
  - The `claude` binary isn't on PATH
    → install Claude Code
  - The aipager daemon isn't running (for `session` only)
    → `aipager start` or `aipager service start`
- New module `aipager.preflight` (with tests in `tests/test_preflight.py`)
  hosts the checks so adding new ones in the future is a one-liner.

## [0.3.3] - 2026-05-17

### Changed
- Renamed `aipager new <name>` to `aipager session <name>`. The behavior
  is the same (open the session, creating if it doesn't exist), but the
  verb no longer falsely implies "always create new" — the command
  reattaches transparently when the dtach session is alive.

### Added
- `aipager session <name> --resume` — when creating a fresh dtach
  session, also pass `--continue` to claude so it loads the most recent
  saved conversation in the current cwd. A no-op when reattaching to an
  existing dtach session (claude is already running there).

## [0.3.2] - 2026-05-17

### Added
- `aipager new <name>` subcommand that creates or reattaches a Claude
  Code session under dtach. Replaces the `claude-dtach` console script.

### Changed
- `claude-dtach` console script removed from `[project.scripts]` —
  the same functionality is now `aipager new` so the user-facing CLI is
  unified under a single `aipager` entry point.
- `dtach` binary discovery now goes through `dtach_bin.path()` first,
  which checks `<sys.prefix>/bin/dtach` before falling back to a PATH
  lookup. This makes `uv tool install aipager` / `pipx install aipager`
  installs work out of the box even though those layouts don't put the
  tool's private `bin/` on the shell's PATH.

### Dependencies
- Bumped `dtach-bin` floor to `>=0.9.1` for the new `path()` semantics.

## [0.3.1] - 2026-05-17

### Changed
- `install.sh` now prefers `uv tool install` over Homebrew, and
  bootstraps `uv` via Astral's installer if no Python tool manager is
  found locally. uv bundles its own Python (python-build-standalone) so
  the install path is immune to Homebrew Python bottle bugs (notably the
  `libexpat _XML_SetAllocTrackerActivationThreshold` symbol mismatch on
  macOS Tahoe).
- README now leads with the `curl … | sh` one-liner and the `uv`
  path; the Homebrew tap is documented as a secondary option with a
  call-out about the Tahoe issue.

## [0.3.0] - 2026-05-17

### Added
- `aipager service` subcommand for cross-platform service management.
  Installs aipager as a systemd-user unit on Linux or a launchd plist on
  macOS, so the daemon survives logout. Subcommands: `install`, `start`,
  `stop`, `status`, `logs`, `uninstall`. Unit/plist always references
  the absolute path of `aipager` resolved via `shutil.which`, so it
  works whether aipager came from pipx, brew, or an editable install.
- `install.sh` one-line installer script. Detects the available installer
  (Homebrew on macOS → pipx → uv tool) and uses whichever is present.
  Available via:
  `curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh`.

### Removed
- `scripts/aipager.service.example` (replaced by the template inside
  `aipager.service`, written by `aipager service install`).

## [0.2.1] - 2026-05-17

### Changed
- README now documents the live Homebrew tap install path
  (`brew install dev-aly3n/tap/aipager`). Replaces the
  earlier "coming in v0.3" placeholder.

## [0.2.0] - 2026-05-16

### Added
- Depend on [`dtach-bin`](https://pypi.org/project/dtach-bin/) so
  `pipx install aipager` pulls in a precompiled `dtach` binary for
  Linux x86_64/aarch64 and macOS x86_64/arm64. No manual system
  package install needed.
- GitHub Actions workflows: `test.yml` (ruff + pytest on Python
  3.10–3.13) and `publish.yml` (build + Trusted Publisher OIDC upload
  on tag push).
- `CONTRIBUTING.md` documenting the local dev setup and release flow.

### Changed
- README leads with `pipx install aipager` as the primary install
  path; `pip install -e .` demoted to a "Developing locally" section.

## [0.1.0] - 2026-05-16

### Added
- `pyproject.toml` with hatchling backend
- MIT license
- README and Changelog
- Console script entry points: `aipager`, `aipager-hook`,
  `aipager-statusline`, `claude-dtach`
- `aipager` CLI with `start`, `config`, `version` subcommands
- `aipager config` — interactive setup wizard that patches
  `~/.claude/settings.json` and writes `~/.config/aipager/config.env`
- XDG-compliant config path (`~/.config/aipager/config.env`) with cwd
  `.env` fallback
- Pure-Python port of the `claude-dtach` session launcher
- Test suite for state machine, markdown→HTML converter, and config loader

### Fixed
- Removed hardcoded transcript directory path that worked on only one
  machine; transcript discovery now scans all project subdirs under
  `~/.claude/projects/`.
