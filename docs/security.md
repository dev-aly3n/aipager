# Security model

aipager runs Claude Code on your behalf and is driven from Telegram.
Two questions follow:

1. Who can drive the bot?
2. What can the bot do once driven?

## Trust boundary

Every handler the bot exposes — message, file, voice, callback —
is gated by `python-telegram-bot`'s chat filter, built from the
chat(s) configured in `~/.config/aipager/aipager.yaml`, or (commands)
by the same check in aipager's own authorization. The one exception
only notes senders in groups that are not configured (below). **Only the
configured chat(s) can interact with the bot.** Nothing from any other
chat is acted on or routed to a session. A private chat that is not
configured gets one "This bot isn't configured to talk to you" reply.
In a group that is not configured the bot never answers, but it does
note who sent it a message there (their Telegram id, username and
name, and the group's id, title and type) in
`~/.claude/aipager-pending-users.json`, so `aipager config` can find a
new group and its people while the daemon runs. Anyone can add the bot
to a group, so anyone can add a line to that file; it grants nothing.
In team mode a per-user allow-list with roles is layered on top (see
[groups](groups.md)).

This means the surface to "outside the world" is:

- The bot token (a secret).
- The chat ID (a long integer).

If both leak, an attacker can drive your daemon. If only the token
leaks, an attacker can read DMs sent to the bot from your chat but
cannot send commands the daemon will act on. If only the chat ID
leaks, nothing useful — the bot needs the token to talk to Telegram
at all.

## Secrets

| Secret | Location | Mode |
|---|---|---|
| Bot token | `~/.config/aipager/aipager.yaml` | 600 by default |
| Chat ID(s) | same file | 600 by default |

Neither value is ever logged — error messages and the wizard redact
the token wherever it could appear. Neither is committed — the
config lives in the user's `~/.config`, not the repo. The Trusted Publisher
PyPI release flow never touches secrets either; OIDC handles auth.

If you suspect the token is compromised, revoke it from
[@BotFather](https://t.me/BotFather) (`/revoke`), generate a new
one, and re-run `aipager config`.

## The Claude credential — what actually protects it

**aipager runs an autonomous agent with Bash access as your UNIX
user. Therefore any secret aipager can read, the agent it launches
can read too.** This is worth stating plainly, because it is easy to
reach for the wrong kind of control.

`~/.config/aipager/` is on `safety.py`'s deny-list, which stops a
Telegram-driven session from reading files under it. That is a
**path** control, and it does nothing here: the Claude credential
(`CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`) is not reached by
path — it is inherited into every spawned session's own **process
environment**. Inside a session, `echo $CLAUDE_CODE_OAUTH_TOKEN`
prints it, deny-list or not. A file-path rule is the wrong shape of
control for an environment variable.

### Where the credential lives

| Deployment | Source | Mode |
|---|---|---|
| systemd-user service | `LoadCredential=claude_oauth:%h/.config/aipager/daemon.env`, read from `$CREDENTIALS_DIRECTORY/claude_oauth` | 600 (daemon.env); systemd keeps the credential out of the unit's own environ |
| macOS / Docker / no systemd | `~/.config/aipager/daemon.env`, read directly | 600 |

Both paths are parsed by the same code (`aipager/daemon_secrets.py`)
and handed to the launched session via the subprocess `env=` table —
never interpolated into the `bash -c` command string. `/proc/PID/cmdline`
is world-readable (0444); `/proc/PID/environ` is 0400 (owner-only).

What `LoadCredential=` + 0600 genuinely buys: other UNIX users on the
same machine cannot read the credential, it never appears in
`systemctl show`, in a unit-file backup, or in a screenshot of
`journalctl` output an operator pastes into a bug report. **It does
not create a boundary between aipager and the agent aipager
launches** — that boundary does not exist, and no amount of file-
permission engineering can create it, because the agent's whole job
is to act as the operator inside that same environment.

### The one control that actually works

**Scope the credential itself.** Run the daemon on a separate
Anthropic account, or with an API key that carries a spend limit —
not the same token you use for your own interactive `claude` login.
Revoking the daemon's credential must never log the human out; if it
would, the two are the same credential and neither is scoped.
`aipager doctor --fix` can discover an existing token to seed
`daemon.env`, but choosing *which* token to hand it is the operator's
call, and it is the one decision in this document that matters more
than the file permissions around it.

### Diagnosing auth without guessing

`aipager doctor` (and the one-line notice sent once per daemon start)
report auth via Claude Code's own `claude auth status` — never a
hand-rolled file check. Three states are kept textually distinct
everywhere they appear:

- `auth: <method> (<source>)` — confirmed logged in.
- `auth: none (not logged in)` — confirmed *not* logged in.
- `auth: unknown (...)` — the probe itself failed (timeout, missing
  binary, unparseable output) or the binary predates the version that
  added JSON output. This is **never** reported as "not logged in":
  aipager does not refuse to launch a session on an auth check it
  isn't sure about. macOS Keychain-based auth, refresh-token-only
  credentials, and Max-plan non-file auth are all invisible to any
  file check and would otherwise look identical to "logged out".

## claude code's own permission system

aipager is **not** the permission gate for tool calls. Claude Code's
`~/.claude/settings.json` is. The flow:

1. Claude wants to run `Bash: rm -rf /`.
2. Claude consults its settings → matches a rule that says `Ask`.
3. Claude fires `PreToolUse` (see [hooks](hooks.md#pretooluse)).
4. aipager relays the prompt to Telegram and waits for your tap.
5. You tap `[✅ Allow]` or `[❌ Deny]`.
6. aipager writes `approve` or `deny` back to claude via the hook
   protocol.
7. Claude honours the decision.

If your `settings.json` says `Deny` for that tool + input combo,
the prompt never even reaches Telegram — claude blocks the call
itself. aipager only sees `Ask` cases.

This matters because: **aipager cannot expand claude's permissions.**
It can only relay prompts claude code chose to surface. If you want
to lock down further (e.g. forbid `Bash: rm`), edit
`~/.claude/settings.json`; aipager will respect it.

### Team-mode enforcement

In team mode aipager adds its own layer *underneath* the taps: every
Telegram-originated message carries a permission note — who sent it
and what their role allows — written to
`/tmp/claude-notes-<session>/`, one file per message. When Claude
picks a prompt up, the `aipager-hook` helper matches it against those
notes and installs the sender's rules where the `PreToolUse` check
reads them. Two properties are load-bearing:

- **Strictest wins.** If Claude picks up messages from more than one
  contributor as a single turn (aipager holds messages to prevent
  this, but fails safe if it happens anyway), the turn runs under the
  *most restrictive* combination — an owner's message can never lend
  its privileges to someone else's.
- **A running turn never widens.** A message from a different person
  waits until the running turn ends, then runs as its own turn with its
  sender's rules. If one joins a running turn anyway, the turn keeps the
  strictest rules of everyone in it; a Telegram message that joins a
  turn you typed in the terminal holds the rest of that turn to its
  sender's rules (an owner's changes nothing).
- **Your role is the one in the chat you act in.** A person listed in
  several chats (say `user` in a group and `owner` of their own DM) has
  the group role for everything they send or tap in the group and the
  DM role in the DM, whatever order the chats are in `aipager.yaml`.
- **Unknown means restricted.** A prompt that cannot be attributed
  runs under the built-in floor — no bypass, all deny rules active —
  never as an unrestricted terminal prompt. Prompts you type directly
  into the terminal remain unrestricted; anything carrying the
  Telegram marker on any line is enforced. Every Telegram message
  carries that marker, labelled with the person who sent that message
  (never whoever drove the session last); a sender aipager does not
  know, for example one removed from `aipager.yaml` while their message
  waited, gets a bare `[via Telegram]` and the floor.
- **Slash commands need an admin.** A slash command is typed into
  Claude raw, with no marker, and a command or skill such as `/deliver`
  runs a whole turn from its own text. So a member whose role does not
  bypass role rules (the built-in `user` and `read_only`, and anyone
  aipager does not know) may send only the commands on aipager's
  Commands keyboard (plus `/compact`) and model switches (`/model
  <name>`); any other slash command, typed, sent to a session with
  `/<name>`, as a file caption, as `/new`'s first message or as a
  template, is refused with "That command needs an admin." and a 🤷.
  Owners and admins send any. A slash command that does reach Claude
  from Telegram is still enforced: the hook sees that the turn picked
  up a Telegram message that sent this command and holds it to that
  sender's rules. A slash command you type in the terminal stays
  unrestricted, unless the same command (with any arguments) was sent
  from Telegram earlier in that session: aipager cannot tell your copy
  from Claude Code delivering the Telegram one again, so yours then
  runs under the rules of the latest Telegram turn. This ends when the
  session ends.
- **A prompt with no new sender keeps the turn's rules.** Claude Code
  also reports prompts that no Telegram message accounts for while a
  Telegram turn is running: a message typed in the terminal and queued
  behind the turn (reported the moment it is queued), a message from
  another local Claude session, or the same Telegram message delivered
  again after a compact. Such a prompt adds no Telegram sender, so the
  turn keeps the rules it was running under, made stricter by any
  message still waiting to be picked up and never looser. A prompt that
  carries Telegram text aipager cannot attribute still falls to the
  built-in floor, and a message from a less-privileged sender still
  lowers the turn to that sender's rules.

**What actually contains a restricted user.** The Claude session runs
as the same OS user that owns aipager's config (including the bot token
in `~/.config/aipager/daemon.env`) and its per-turn policy file
(`/tmp/claude-policy-<session>.json`). A shell running as that user can
reach both through spellings no pattern anticipates
(`sed -i … /tmp/*-policy-*.json`, a `for` loop over a glob, a Python
one-liner) and rewrite its own rules to the owner's. Command patterns
are therefore a filter, not a boundary. For a turn run under the
built-in restricted roles (`user`, `read_only`), and for a turn aipager
cannot attribute, aipager enforces these rules, none of which depend on
command patterns (see the known limits below for what they do not
cover):

- **No shell.** They cannot use `Bash`, or any other tool Claude Code
  runs code with (`PowerShell`, `Monitor`, `REPL`, `Workflow`, the
  prompt schedulers `CronCreate` and `RemoteTrigger`, …; the list is
  `safety.CODE_EXECUTION_TOOLS`). A scheduled prompt would arrive
  without the Telegram marker, i.e. unrestricted, so those count too.
  For the same reason they cannot use `SendMessage` (it can hand a
  prompt to another local Claude session, which would run it as a
  terminal prompt), nor `EnterWorktree`/`ExitWorktree` (they move the
  session's working directory, which the next rule relies on).
- **Writes stay in the project.** `Write`, `Edit`, `MultiEdit` and
  `NotebookEdit` land only inside the session's folder (the working
  directory Claude Code reports in the hook payload, never a path from
  the tool call) and the session's Claude Code scratchpad
  (`/tmp/claude-<uid>/<project>/<session id>/`, or `/tmp/claude-<uid>/`
  when that cannot be worked out). Everything else is denied: your
  shell startup files, `~/.ssh`, other projects, aipager's `/tmp`
  files. Inside the folder, `.claude/`, `.git/` and `.mcp.json` are
  denied too, because each of them makes Claude Code or git run a
  command. Symlinks are followed before deciding. A session whose
  folder is your home folder, `/` or a folder above your home folder
  has no project folder: there a restricted turn writes only in its
  scratchpad, and a write anywhere else is denied with "this session
  runs in the home folder (or a folder above it), and this role can
  only write inside a project folder". So that restricted members are
  not stuck there, `/new` and the Mini App do not let them start a
  session in those folders (the daemon's own folder, the default for a
  new session, is your home folder under the background service); they
  can create a new folder inside your home folder and start in that
  (a new one: not a folder you already have there, and not `bin` or
  `node_modules`, which other programs load code from).
- **Searches stay in the project.** A `Grep` or `Glob` is allowed
  only when all of this holds: the folder it searches (its `path`, or
  the session's folder when it has none, symlinks followed) is inside
  the session's folder (not when that is your home folder, `/` or above
  your home folder, as for writes) or scratchpad; that folder is not a
  protected path and holds none (a session started in your home folder holds
  `~/.config/aipager`, so it cannot search from its top); every glob is
  a plain relative pattern — no leading `/`, `~`, `$` or drive letter,
  no `..`, no `#` (a comment to ripgrep) or `!` (a negation), no `\`
  escape; and you have no protected-path rule without a leading `/` or
  `~` (`**/.env` can sit anywhere in the project, so with one in place
  every restricted search is denied). Anything else is denied. This is
  an allow-list on purpose: three review rounds each found a new glob
  spelling that led ripgrep out of the folder a model of its parsing
  expected, and one of them read the bot token.
- **The file tools honour the protected paths.** `Read`, `LSP` and
  the write tools check where a path really leads, symlinks included.
  The one control file a turn may read is its own session's
  `/tmp/claude-reply-<session>.txt`, which aipager names in the prompt
  when you reply to an older message; other sessions' reply files, and
  any write to it, stay denied. On macOS, `/tmp` is `/private/tmp` and
  the disk ignores case, so both spellings, and any capitalisation, are
  matched.
- **A check that fails denies.** If deciding on a tool call raises an
  error (a NUL byte in a path, a bug), or the hook runs out of memory
  while deciding, the call is denied — unless the session's rules could
  be read and grant the owner's bypass. It used to be let through.

**Turns aipager cannot attribute.** A Telegram turn with no sender to
hold it to runs under the built-in floor, which matches the built-in
`user` role (not any extra restrictions you add to `user` in
`policy.yaml`): no code-running tools, writes and searches confined, the
protected paths. That happens for a message whose permission note
expired (after 24 hours) or could not be written, and for a prompt
carrying Telegram text that no note matches. The Retry button runs as
whoever tapped it when they also sent the prompt being retried, so an
owner's Retry of their own message keeps Bash; a Retry of someone
else's message, or of one whose sender aipager does not know, runs on
the floor, so nobody's text borrows the tapper's rights.

Reads outside the protected paths are allowed: a restricted user can
read other projects of the OS user. Your credential files are protected
paths for every role without `bypass_role_denies` (the built-in `user`
and `read_only`, roles you define, and the floor): `~/.ssh`,
`~/.gnupg`, `~/.aws`, `~/.config/gh`, `~/.git-credentials`, `~/.netrc`,
`~/.docker/config.json`, `~/.kube`, `~/.config/gcloud`, `~/.azure`,
`~/.password-store`, `~/.pgpass`, `~/.npmrc` and `~/.pypirc` cannot be
read, written or searched. They are a role default, so a role's own
`deny_paths_no_access` in `policy.yaml` replaces them for that role,
except while a running turn is joined by another message or a prompt
typed in the terminal: from then until the turn ends, it is held to
them again (the strictest rules win).
A credential file somewhere else (a `.env` in a project, a token in
another folder) is not covered: add it to the role's
`deny_paths_no_access`. Owners are not held to any of this.
Admins' writes are not confined, and their `Grep`/`Glob` are checked on
the folder they start in only — they have Bash, so their rules are
best-effort anyway (below).

**Known limits.** These are not covered:

- If the hook itself fails before it starts deciding (for example it
  cannot read its own status file, or runs out of memory while
  starting), Claude Code treats that as "allow". A restricted user
  cannot cause this; any error while deciding denies.
- Tools that are not file tools but can read a file or send data out
  are not path-checked: `SendFile`, `WebFetch` (anything the turn can
  read it can send to a URL), and Claude Code's other file-delivery
  tools. Deny them for a role in `policy.yaml` if that matters to you.
- A background agent a restricted turn started keeps running after the
  turn ends, and its later tool calls are judged by whichever prompt
  is newest in the session: a later owner or terminal prompt lifts its
  restrictions.
- A restricted member can still start in any project folder their
  chat already works in (a folder an earlier session used), including
  one of yours.
- Inside the project a restricted user can change what you later run
  or read yourself (source, `Makefile`, `package.json` scripts,
  `conftest.py`, a `CLAUDE.md` your own turns load).

**A role that has Bash** — `admin`, which bypasses role deny rules, or
any role `policy.yaml` gives it back to (`allow_tools` naming `Bash`,
or a replaced `deny_tools`) — **is held to best-effort rules only**:
the command patterns below, which a determined user can get around.
`aipager doctor` warns once for every such role a member holds. Only
give Bash to someone you would give a shell on the machine.

**The built-in command rules.** Among the `Bash` patterns every
non-owner Telegram turn with Bash is held to, one blocks the word `claude`: it
stops a nested `claude` (with any path, wrapper or flag) and any
`~/.claude` access in one rule, and it also catches the plain names of
aipager's own `/tmp/claude-*` control files, whose names share the
prefix (a pattern is not a real protection for those files: a glob
spelling avoids it). The one exception is the scratchpad folder Claude
Code creates, `/tmp/claude-<uid>/…`: that exact directory, as a whole
path component, is exempt, so commands run from a session's scratchpad
are not halted. It belongs to the OS user, not to one session, so a
turn with Bash can read other sessions' scratchpads and task output
there, as the `Read` tool can. A glob or look-alike
of it (`/tmp/claude-1*`, `claude-1000x`), another temp directory, and
anything reached from it (`…/../../home/you/.claude`) are still
blocked. Other mentions of the word (`git log --grep=claude`, a file
named `claude-notes.md`, `pip show claude-agent-sdk`) are also still
blocked, deliberately: the same word names the binary, its SDKs and
aipager's control files, and a pattern cannot tell them apart. Rules
you add in `policy.yaml` see the command exactly as typed, scratchpad
included.

**Why one block halts the whole turn.** After a tool call is blocked,
every later call in the same turn is denied too, until the next
prompt. Without this, the agent simply retries the same action
reworded (a glob, a different command, an encoded path) until one
spelling slips past the patterns. Halting the turn makes a first
attempt costly; it does not make a pattern a boundary, since the
first spelling tried may be the one that slips through. The cost of a
false positive is one stopped turn, which is why the rules above are
kept narrow.

See [groups → how rules work](groups.md#how-rules-work) for the
user-facing rules these notes carry.

## Audit log

Every Allow / Deny tap, plus every `AskUserQuestion`
answer, appends one JSON line to `~/.claude/aipager-audit.jsonl`:

```json
{
  "ts": "2026-05-18T15:42:11+00:00",
  "session": "claude-jim",
  "label": "jim",
  "action": "Allowed",
  "tool": "Bash",
  "summary": "ls -la /tmp",
  "user_id": 12345,
  "username": "alice",
  "denied": false
}
```

Fields (see `aipager/audit.py` for the full set):

- `ts` — ISO 8601 UTC timestamp, second precision.
- `session` / `label` — internal name and friendly label.
- `action` — what happened (`Allowed`, `Denied`, `Allowed always`,
  an `AskUserQuestion` answer, an auto-deny, …).
- `tool` / `summary` — the tool name and a truncated input or
  question body.
- `user_id` / `username` — who tapped, for team-mode attribution;
  `scope_label` / `scope_chat_id` where multiple chats are configured.
- `denied` — true for any refusal, tap-driven or rule-driven.

Write is best-effort. If the disk fills up or `~/.claude/` becomes
unwritable, the daemon logs a `WARNING` and keeps running — no
silent loss, no crash. See `aipager/audit.py`.

The audit log is append-only on disk. Pair it with the in-chat
audit reply (one Telegram message per decision, threaded under the
busy message) for two independent records.

## Privilege boundary

The daemon **never elevates**. No sudo, no setuid, no doas. Every
file written lives under `$HOME`. Every subprocess
(`claude`, `dtach`, pip installs, npm) runs as the daemon user.

The Telegram-driven extra-install flow (e.g. tapping `[📦 Install
voice]`) explicitly uses `sys.executable -m pip install`, which
writes into the daemon's own venv — never the system Python.

The Telegram-driven daemon-restart flow (`[🔄 Restart daemon now]`)
spawns a detached child with `start_new_session=True`, then SIGTERMs
the current process. Both processes run as the same user; no
escalation.

## Network surface

aipager listens on **no non-loopback TCP port**. The Mini App server
binds `127.0.0.1` only (hardcoded — there is deliberately no host
option) and is reachable from outside solely through the managed
tunnel described below. Outbound:

- HTTPS long-poll to `api.telegram.org` (Telegram bot polling).
- HTTPS to `telegram.org` for the Mini App's SDK script
  (`telegram-web-app.js`), fetched once when the Mini App server
  starts and refreshed at most once a day; cached under
  `~/.local/share/aipager/webapp-sdk/`. The daemon serves those bytes
  to the page from its own origin and never executes them. aipager
  does not redistribute the script: nothing is bundled in the
  package, and when the daemon has no copy the page loads it from
  `telegram.org` directly, as it did before.
- The Mini App tunnel to Cloudflare, while enabled (the default).
- HTTPS to `pypi.org` and friends, only when the user taps the
  voice install button.

Inbound:

- Unix datagram socket at `$XDG_RUNTIME_DIR/aipager.sock` (falling back to
  `/tmp/aipager.sock` when `$XDG_RUNTIME_DIR` is unset). Bound and
  chmod'd by the daemon at startup
  (`aipager/dtach/hook_receiver.py`). Mode `0o666` so any local
  process can send hook events to it — same trust as
  `~/.claude/settings.json`, which already controls what runs
  hooks.

This means: the daemon is not a remote attack surface. A network-
level attacker cannot reach it without a foothold on the host.

## Mini App tunnel

**The Mini App is on by default**, so on a stock install aipager opens
this tunnel without being asked. That is a deliberate trade — an opt-in
nobody discovered was a feature that did not exist — but it means a
fresh install reaches the internet. Turn it off with `aipager miniapp
disable`, or set `enabled: false` under `miniapp:` in `aipager.yaml`;
an explicit `false` is always respected. Note that a **missing or
corrupt** `miniapp:` block now falls back to ON rather than OFF.

The Mini App (unless a `--url` override is set) spawns a managed
[Cloudflare quick
tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/)
pointed at the daemon's own loopback server, and publishes whatever
public `https://*.trycloudflare.com` address it is assigned as the
Telegram menu button, the reply-keyboard button, and `/app`'s answer.

That address **is not secret, and is not meant to be**: every request
the Mini App serves is verified against Telegram's `initData` signature
(see [Trust boundary](#trust-boundary) above), the same check that
protects it regardless of how the URL was obtained. Knowing the
hostname alone gets an attacker nothing they could not already get by
guessing a `trycloudflare.com` subdomain at random — the signature
check is the actual gate. Treat it the way you'd treat any other
unlisted-but-not-secret URL: fine to leave running, not something to
paste into a public channel for no reason.

The hostname changes on every daemon restart and is held only in
memory — it is never written to `aipager.yaml` or anywhere under
`~/.config/aipager/`. If the tunnel dies mid-run,
aipager restarts it with backoff and republishes the new address; if
it cannot come back up after several attempts, the button is removed
rather than left pointing at a dead port (see [Network
surface](#network-surface) above for the same "no button is an honest
absence" principle applied to the loopback server itself).

Setting `MINIAPP_PUBLIC_URL` (or `aipager miniapp enable --url
https://…`) disables the managed tunnel entirely — no cloudflared
binary is ever fetched or spawned — and the given URL is used as-is,
with the same `initData` verification underneath it. Tailscale
auto-detect (`tailscale status --json`) remains available as a
lower-effort alternative for anyone who already runs Tailscale and
would rather not depend on Cloudflare at all.

## Voice transcription

`faster-whisper` runs in-process. The audio is downloaded as `.ogg`
into `~/.config/aipager/files/`, transcribed locally on CPU, and
the file stays under your control. **No audio leaves the machine.**
No third-party API. No key needed beyond the bot token to talk to
Telegram in the first place.

If you delete the `.ogg` after transcription, the only record of
the message in plain text is the transcript that gets injected into
the claude session (where it follows claude code's own privacy
posture).

## Multi-session isolation

Each Claude Code session runs in its own dtach. The control socket
at `/tmp/claude-dtach-<name>.sock` is owned by the daemon user;
dtach refuses cross-user attaches. Inside the session, claude code
operates with whatever `--cwd` it was launched in.

aipager does not implement filesystem-level isolation between
sessions: a session attached to `~/projects/foo` can in principle
read `~/projects/bar` if claude code's permissions allow. Use
per-project `~/.claude/settings.json` overrides or a container
([Docker image](../README.md#docker)) for stronger isolation.

## Threat model summary

| Threat | Mitigation |
|---|---|
| Stranger sends bot a command | Chat ID filter rejects |
| Stolen bot token | Use `/revoke` in @BotFather, re-config |
| Compromised claude tool call | Claude's `settings.json` is the gate; aipager respects it |
| Restricted Telegram user escalates to the owner | No shell, writes and searches confined to the project (never the home folder), credential files unreadable, protected paths for the file tools, failed checks deny ([team-mode enforcement](#team-mode-enforcement)); best-effort only for a role given Bash, and not covered for the cases under "Known limits" |
| Audit log tampering | Append-only; out of scope to prevent without a separate signing daemon |
| Network attacker | No inbound port, not directly reachable |
| Local privilege escalation | No sudo / setuid; daemon stays in user space |
| Voice audio leaking to cloud | Transcription is local |
| Agent reads the daemon's own Claude credential | Expected, not a bug — see [The Claude credential](#the-claude-credential--what-actually-protects-it). Scope the credential itself, not the file it's stored in |

## See also

- [Architecture](architecture.md) — process model.
- [Hook events](hooks.md) — what the daemon actually sees from claude.
- [Bot commands](commands.md) — the user-driven side.
