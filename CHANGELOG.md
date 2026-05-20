# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
