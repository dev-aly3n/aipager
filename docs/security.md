# Security model

aipager runs Claude Code on your behalf and is driven from Telegram.
Two questions follow:

1. Who can drive the bot?
2. What can the bot do once driven?

## Trust boundary

Every handler the bot exposes - message, file, voice, callback -
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
The file is readable only by you (mode 0600), is replaced whole on
every write (never left half written), and keeps at most the newest
200 people, dropping anyone not seen for 30 days.
In team mode a per-user allow-list with roles is layered on top, and
in a group only a message addressed to the bot (a command, a reply to
it, a mention, or a tap on its keyboard) is acted on, whether or not
the bot is a group admin (see [groups](groups.md)).

This means the surface to "outside the world" is:

- The bot token (a secret).
- The chat ID (a long integer).

If both leak, an attacker can drive your daemon. If only the token
leaks, an attacker can read DMs sent to the bot from your chat but
cannot send commands the daemon will act on. If only the chat ID
leaks, nothing useful - the bot needs the token to talk to Telegram
at all.

## Secrets

| Secret | Location | Mode |
|---|---|---|
| Bot token | `~/.config/aipager/aipager.yaml` | 600 by default |
| Chat ID(s) | same file | 600 by default |

The token is kept out of the daemon's log: the HTTP request lines that
carry it are not logged, and error messages, `aipager doctor` and the
wizard redact anything shaped like a bot token. Chat ids do appear in
the daemon's log (for example in rate-limit lines), so treat `aipager
logs` output as private. Neither is committed: the config lives in the
user's `~/.config`, not the repo. The Trusted Publisher
PyPI release flow never touches secrets either; OIDC handles auth.

If you suspect the token is compromised, revoke it from
[@BotFather](https://t.me/BotFather) (`/revoke`), generate a new
one, and re-run `aipager config`.

## The Claude credential: what actually protects it

**aipager runs an autonomous agent with Bash access as your UNIX
user. Therefore any secret aipager can read, the agent it launches
can read too.** This is worth stating plainly, because it is easy to
reach for the wrong kind of control.

`~/.config/aipager/` is on `safety.py`'s deny-list, which stops a
Telegram-driven session from reading files under it. That is a
**path** control, and it does nothing here: the Claude credential
(`CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`) is not reached by
path - it is inherited into every spawned session's own **process
environment**. Inside a session, `echo $CLAUDE_CODE_OAUTH_TOKEN`
prints it, deny-list or not. A file-path rule is the wrong shape of
control for an environment variable.

### Where the credential lives

| Deployment | Source | Mode |
|---|---|---|
| systemd-user service | `LoadCredential=claude_oauth:%h/.config/aipager/daemon.env`, read from `$CREDENTIALS_DIRECTORY/claude_oauth` | 600 (daemon.env); systemd keeps the credential out of the unit's own environ |
| macOS / Docker / no systemd | `~/.config/aipager/daemon.env`, read directly | 600 |

Both paths are parsed by the same code (`aipager/daemon_secrets.py`)
and handed to the launched session via the subprocess `env=` table -
never interpolated into the `bash -c` command string. `/proc/PID/cmdline`
is world-readable (0444); `/proc/PID/environ` is 0400 (owner-only).

What `LoadCredential=` + 0600 genuinely buys: other UNIX users on the
same machine cannot read the credential, it never appears in
`systemctl show`, in a unit-file backup, or in a screenshot of
`journalctl` output an operator pastes into a bug report. **It does
not create a boundary between aipager and the agent aipager
launches** - that boundary does not exist, and no amount of file-
permission engineering can create it, because the agent's whole job
is to act as the operator inside that same environment.

### The one control that actually works

**Scope the credential itself.** Run the daemon on a separate
Anthropic account, or with an API key that carries a spend limit -
not the same token you use for your own interactive `claude` login.
Revoking the daemon's credential must never log the human out; if it
would, the two are the same credential and neither is scoped.
`aipager doctor --fix` can discover an existing token to seed
`daemon.env`, but choosing *which* token to hand it is the operator's
call, and it is the one decision in this document that matters more
than the file permissions around it.

### Diagnosing auth without guessing

`aipager doctor` (and the one-line notice sent once per daemon start)
report auth via Claude Code's own `claude auth status` - never a
hand-rolled file check. Three states are kept textually distinct
everywhere they appear:

- `auth: <method> (<source>)` - confirmed logged in.
- `auth: none (not logged in)` - confirmed *not* logged in.
- `auth: unknown (...)` - the probe itself failed (timeout, missing
  binary, unparseable output) or the binary predates the version that
  added JSON output. This is **never** reported as "not logged in":
  aipager does not refuse to launch a session on an auth check it
  isn't sure about. macOS Keychain-based auth, refresh-token-only
  credentials, and Max-plan non-file auth are all invisible to any
  file check and would otherwise look identical to "logged out".

## Who decides whether a tool call runs

Two separate checks decide whether a tool call runs, and they run in
this order:

1. **aipager's safety check.** Claude Code runs aipager's `PreToolUse`
   hook before every tool call. For a turn started from Telegram it
   holds the call to the sender's role rules and to the safety floor
   (protected paths, command patterns), and denies what they refuse:
   Claude Code then refuses the tool without asking anyone, whatever
   its own rules say. A prompt typed in the terminal is not restricted
   by this check. What it covers is below, under
   [team-mode enforcement](#team-mode-enforcement).
2. **Claude Code's own rules** (`~/.claude/settings.json`, the
   session's permission mode). If they say ask, Claude Code fires
   `PermissionRequest`, and aipager shows the prompt in Telegram (see
   [hooks](hooks.md#permissionrequest)). Your tap goes back to Claude
   Code as the hook's decision, or typed into Claude Code's own dialog
   if the 20-second wait ran out. If Claude Code's rules deny the call,
   it never reaches Telegram.

So aipager can refuse a call Claude Code would allow, and when Claude
Code asks, aipager never answers yes on its own: an allow is always a
person's tap. Three things let calls run with no prompt to a person:

- **Full access mode, for admins (and a person in their own private
  chat).** A session in Full access is started with
  `--dangerously-skip-permissions`: Claude Code asks for nothing, so
  every call aipager's safety check does not deny runs without a tap.
  Only an admin (the `owner` and `admin` roles, or a role with
  `can_manage`) can start a session in Full access, switch one to it
  (`/mode full`, after a confirm) or resume one in it, with one
  exception: in their own private chat with the bot, a person whose
  role may send prompts and answer Claude (`can_prompt` and
  `can_approve`, so a `user` but not `read_only`) may do the same for
  their own sessions (`/new !x1`, or New sessions set to Full access in
  that chat, `/mode full`, `/resume`), in Telegram and in the Mini App.
  Never in a group or in another person's private chat. Anyone else
  gets Auto mode instead, and the reply says so. Full access only skips
  Claude Code's own prompts: aipager's PreToolUse hook still applies
  that person's role rules (the safety floor, folder confinement, deny
  rules, credential files) to every call, exactly as in Ask and Auto;
  the mode Claude Code reports never loosens them. aipager's safety
  check still runs on every call in Full access, but it denies nothing
  in an owner's own turns or in a prompt typed in the terminal.
- **Auto mode, the default.** A session in Auto is started with
  `--permission-mode auto`: Claude Code's own safety check reviews each
  call, runs the safe ones and stops the risky ones, which then reach
  Telegram as a prompt like in Ask mode. Every session starts in Auto
  unless the chat chose otherwise under `/settings` → New sessions.
  Anyone who can act on a session can switch it between Ask and Auto
  (`/mode`); aipager does that in place by pressing Shift+Tab in the
  session, never with a flag that would put Full access in Claude
  Code's Shift+Tab cycle. If Claude Code ever reports Full access for a
  session aipager did not start in it, the daemon shows what was
  reported, logs a warning and never starts or restarts the session in
  Full access because of it.
  Known limit: the mode Claude Code reports comes in through the hook
  socket, which any local process can write to (see
  [network surface](#network-surface)). A forged report can change the mode a session is
  shown in, and while an Ask/Auto switch waits for its confirmation it
  can make aipager press Shift+Tab once more, which can leave the live
  session in Auto when Ask was asked for (the next real hook shows the
  real mode again). It never changes the flag a restart or resume
  passes, and it can never reach Full access: only the launch flag sets
  that. aipager does not press Shift+Tab in a session it knows can
  cycle into Full access: one started in Full access (also after it was
  switched to Ask or Auto in the terminal), or one that has reported Full
  access to the running daemon since it started. Every mode switch there
  is a restart, which launches the new mode's flag and drops the Full
  access one, and a forged report of Full access can only turn a switch
  into a restart. aipager cannot know this for a session started in a
  terminal with the flag that adds Full access to the Shift+Tab cycle
  (`--allow-` in front of the Full access flag), or with
  `--dangerously-skip-permissions` when it left Full access before the
  daemon heard from it (the daemon was not running then, or has
  restarted since). There, switching from Ask to Auto can stop in Full
  access, and the card then says the session is in Full access. Do not
  use those flags for terminal sessions that people who are not admins
  can switch from Telegram.
  So that Full access works from Telegram, the daemon sets
  `skipDangerousModePermissionPrompt` in `~/.claude/settings.json` at
  every start (Claude Code's warning before it runs in this mode would
  otherwise stop the session); that also turns the warning off for the
  sessions you start yourself. It likewise marks its default session
  folder as trusted in `~/.claude.json`, so Claude Code's "Do you trust
  this folder?" question does not stop a session started from Telegram
  in that folder.
- **Allow always**, which hands Claude Code back the "don't ask again"
  rule it offered with the prompt, exactly as that option in its own
  dialog would.

To lock a tool down for everyone, deny it in `~/.claude/settings.json`;
to lock it down for some people, use their role's rules in
`policy.yaml` (see [groups](groups.md#how-rules-work)).

**What a prompt shows is what runs.** A permission prompt shows the real
command or path under Claude's description of it. Unicode's direction
controls (U+202A-U+202E, U+2066-U+2069, U+200E, U+200F, U+061C) change the
order in which Telegram, a browser or a phone draws the text after them,
while the shell runs the characters in order, so they could make a command
read like a different one (the "Trojan Source" trick, CVE-2021-42574). In
every prompt, question, diff preview and audit record they are shown as visible markers
(`⟨U+202E⟩`), and the prompt warns that the command may not run what it
looks like. Other invisible characters are left as they are.

### Team-mode enforcement

In team mode aipager adds its own layer *underneath* the taps: every
Telegram-originated message carries a permission note - who sent it
and what their role allows - written to
`/tmp/claude-notes-<session>/`, one file per message. When Claude
picks a prompt up, the `aipager-hook` helper matches it against those
notes and installs the sender's rules where the `PreToolUse` check
reads them. These properties are load-bearing:

- **Strictest wins.** If Claude picks up messages from more than one
  contributor as a single turn (aipager holds messages to prevent
  this, but fails safe if it happens anyway), the turn runs under the
  *most restrictive* combination - an owner's message can never lend
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
  runs under the built-in floor - no bypass, all deny rules active -
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
  bypass role rules (the built-in `user` and `read_only`, roles you
  define without `bypass_role_denies`, and anyone aipager does not
  know) may send only the commands on aipager's
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
as the same OS user that owns aipager's config (the bot token in
`~/.config/aipager/aipager.yaml`, the Claude credential in
`~/.config/aipager/daemon.env`) and its per-turn policy file
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
- **Members stay in their root folder.** A role without `can_manage`
  (the built-in `user` and `read_only`) works inside the chat's
  [root folder](groups.md#root-folders), which you set in `aipager
  config` (default `~/aipager-users/<label>`; `~/aipager-users` and the
  folder are created with mode 0700).
  `/new`, Resume, Replace, Restart, the mode switch and the Mini App
  only start or relaunch such a member's session inside it. The safety
  hook enforces it whichever way a message arrives (Telegram, the Mini
  App, another session's message): the root folder travels in the
  turn's policy snapshot, in the receipts of messages between sessions
  and in pinned and scheduled rights, and when members with different
  root folders share a turn the narrower one wins (two that do not
  nest leave only the session's own folder). Writes land only in the
  session folder when it is inside the root folder (a session outside
  it, such as one started before root folders existed, keeps running,
  but its turns write only in the scratchpad, "this session runs
  outside this role's own folder"), and `Read`, `LSP`, `Grep` and
  `Glob` reach nothing in your home folder outside the root folder
  ("this role reads in the home folder only inside its own folder"),
  the session's scratchpad and its own reply file excepted. Every other
  chat's root folder is a protected path for the member, wherever it
  is. Folders outside your home folder (`/usr`, `/etc`, `/srv`) stay
  readable. A custom role with `bypass_role_denies` or `bypass_safety`
  but without `can_manage` writes and searches anywhere inside its root
  folder, and its reads in your home folder stay inside it too: only
  owners and admins are unrestricted. With `bypass_safety`, in a
  session whose folder is outside its root folder it writes and searches
  only in the scratchpad, as a `user` does, and once one of its tool
  calls is denied the rest of the turn is halted.
- **Searches stay in the project.** A `Grep` or `Glob` is allowed
  only when all of this holds: the folder it searches (its `path`, or
  the session's folder when it has none, symlinks followed) is inside
  the session's folder (not when that is your home folder, `/` or above
  your home folder, as for writes) or scratchpad; that folder is not a
  protected path and holds none (a session started in your home folder holds
  `~/.config/aipager`, so it cannot search from its top); every glob is
  a plain relative pattern - no leading `/`, `~`, `$` or drive letter,
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
  while deciding, the call is denied - unless the session's rules could
  be read and grant the owner's bypass. It used to be let through.

**Your own protected paths.** The `safety:` section of `policy.yaml`
(`deny_paths_no_access`, `deny_paths_no_write`, `deny_bash_patterns`)
adds to the built-in protected paths and command patterns. It is part
of the floor every turn of every role except `owner` gets, admins
included, and it applies to the file tools, searches and Bash commands
exactly like the built-in list. The daemon writes the combined list to
`/tmp/claude-policy-.floor-<uid>.json` (itself a protected path) for
the safety hook's fallbacks; if that file is missing, unreadable or
owned by another OS user, the hook uses the built-in list, never less.
A change made with a live reload applies to messages sent after the
reload, not to a turn already running or a message already waiting.
Up to 0.7.20 the section was read and shown by
`aipager doctor --safety-check` but not enforced.

**Turns aipager cannot attribute.** A Telegram turn with no sender to
hold it to runs under the floor, which matches the built-in `user` role
(not any extra restrictions you add to `user` in `policy.yaml`) plus
your `safety:` section: no code-running tools, writes and searches
confined, the protected paths. That happens for a message whose permission note
expired (after 24 hours) or could not be written, and for a prompt
carrying Telegram text that no note matches. The Retry button runs as
whoever tapped it when they also sent the prompt being retried, so an
owner's Retry of their own message keeps Bash; a Retry of someone
else's message, or of one whose sender aipager does not know, runs on
the floor, so nobody's text borrows the tapper's rights.

Reads outside the protected paths are allowed for a role that is not
held to a root folder (a custom role with `can_manage` but no bypass):
it can read other projects of the OS user. The built-in `user` and
`read_only` read nothing in your home folder outside their root folder
(above). Your credential files are protected
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
Admins' writes are not confined. Their `Grep`/`Glob` may search any
folder that does not reach a protected path: a search is denied when the
folder it searches (its `path`, or the session's folder when it has
none; for a `Glob`, also the folder its pattern names, as in
`/home/you/.config/aipager/*`, or the folder above a pattern with no
wildcard, which Claude Code searches for that name at any depth), with
symlinks resolved, is a protected folder, lies inside one, or holds one.
So `Grep` of `~`, `/`, the folder above your home folder or `/tmp` is
denied (they hold `~/.config/aipager`, `~/.claude` or aipager's
`/tmp/claude-*` files), while `Grep` of a project or of `/tmp/myproj` is
not. A protected-path rule without a leading `/` or `~` (such as
`**/.env`) can match in any folder, so with it in an admin's rules (or in
the `safety:` section, which every role except owner gets) every admin
search is denied, as for restricted roles; `aipager doctor` warns about
one in the `safety:` section. Admins have Bash, so
their rules stay best-effort (below).

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
- A role you define with `can_manage` but without either bypass is
  not held to a root folder: it can still start in any project folder
  its chat already works in (a folder an earlier session used),
  including one of yours, and read your other projects.
- A turn no member can be held to (an unknown sender, a removed member,
  a message from another session whose sender cannot be verified) runs
  under the restricted floor, which has no root folder. On reads that
  floor is weaker than a built-in `user`: such a turn can read any file
  in your home folder that is not a protected path (your other
  projects, for example), where a `user`'s turn reads only inside its
  root folder. Its writes stay confined to the session folder and the
  scratchpad. A verified message from a member's session carries the
  member's root folder.
- The root folder binds the file tools only. A custom role you give
  `Bash` can read outside it through the shell (the built-in `user`
  and `read_only` have no `Bash`). A custom role with `bypass_safety`
  but without `can_manage` keeps its bypass for everything but the
  file tools' paths: no tool or command pattern is denied to it.
- A deny reason that names another member's root folder shows that
  folder's path to Claude and in the chat.
- A member's turn already running when root folders arrive (an
  upgrade) has none until that member's next message.
- Inside the project a restricted user can change what you later run
  or read yourself (source, `Makefile`, `package.json` scripts,
  `conftest.py`, a `CLAUDE.md` your own turns load).

**A role that has Bash** - `admin`, which bypasses role deny rules, or
any role `policy.yaml` gives it back to (`allow_tools` naming `Bash`,
or a replaced `deny_tools`) - **is held to best-effort rules only**:
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

**Searches through Bash.** Some Claude Code versions offer no `Grep` or
`Glob` tool (2.1.289 is one), and Claude then searches with `grep`,
`rg` or `find` through Bash, where the `Grep`/`Glob` rule above does not
apply. For every role except owner, the command patterns also deny a
recursive search or listing of the whole home folder or `/`: `grep -r`
(`-R`, `--recursive`, `-d recurse`, `egrep`, `fgrep`, `rgrep`), `rg`
(and `rg --files`), `ag`, `ack` (and `ack -f`), `find`, `fd`, `tree`,
`du` and `ls -R`
whose folder is `~`, `~user`, `$HOME`, `${HOME}` or `"$HOME"`, `/home`,
`/home/<user>` (or `/home/$USER`), `/Users`, `/Users/<user>`, `/root` or
`/` (with or without a trailing `/`, `/.` or `/*`), or `~/.config` or
`~/.local`, which hold aipager's folders. This holds after `cd x &&`,
in a pipe, inside `bash -c "..."`, after `xargs`, `env` or `timeout` and
after `LC_ALL=C`-style settings.
The reply says to search inside a project folder instead. Searches of a
project folder (`grep -rn x ~/proj`, `rg x src/`, `find . -name y`),
searches inside a project FOR a home path (`grep -rn "$HOME" src`, the
first word after the options is the text to find, not a folder) and
searches that are not recursive (`grep x ~/.bashrc`, `ls ~`) are not
affected. This is best effort, like every command pattern: a pattern
sees the words, not where the shell runs them, so it cannot see a search
with no folder, or `.` or `..`, run from (or below) the home folder
(`cd ~ && grep -r x .`), a home folder reached through another variable
or a glob, nor one written out in full outside `/home`, `/Users` and
`/root`, nor `\grep`, nor a folder right after a word ending in `-e`
or `-f` (taken for the text `-e`/`-f` gives grep and rg, so
`ag x -f ~` gets through). It also does not know which options take a
separate value, so `rg -t py "$HOME" .` or `grep -rn -A 3 "/home/you"
src` reads `py` or `3` as the text and the home path as a folder, and is
denied (write `-A3`, `--include=*.py`, or put the option after the
text), and so is a home path given to `-e` inside a cluster
(`grep -rne "/home" src`; `-r -n -e` is fine); a command quoted inside
another command's text (`echo "a; rg x ~"`) is denied too, and so is
`du -sh ~` (sizes only).

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

- `ts` - ISO 8601 UTC timestamp, second precision.
- `session` / `label` - internal name and friendly label.
- `action` - what happened (`Allowed`, `Denied`, `Allowed always`,
  an `AskUserQuestion` answer, an auto-deny, …).
- `tool` / `summary` - the tool name and a truncated input or
  question body.
- `user_id` / `username` - who tapped, for team-mode attribution;
  `scope_label` / `scope_chat_id` where multiple chats are configured.
- `denied` - true for any refusal, tap-driven or rule-driven.

Write is best-effort. If the disk fills up or `~/.claude/` becomes
unwritable, the daemon logs a `WARNING` and keeps running - no
silent loss, no crash. See `aipager/audit.py`.

The audit log is append-only on disk. Pair it with the in-chat
audit reply (one Telegram message per decision, threaded under the
busy message) for two independent records.

## Privilege boundary

The daemon **never elevates**. No sudo, no setuid, no doas. Every
file it writes is under your home folder, under `/tmp` (aipager's
per-session control files, downloads and the restart log) or in the
folder of its control socket (`$XDG_RUNTIME_DIR`); the full list is in
[architecture](architecture.md#file-and-socket-layout). Every
subprocess (`claude`, `dtach`, the installers, `claude update`) runs as
the daemon's user.

Installing the voice extra from Telegram (`[📦 Install voice]`) goes
through the installer that owns aipager (`uv tool install --reinstall
aipager[voice]`, `pipx install --force aipager[voice]`), and otherwise
installs the extra's packages with the daemon's own Python
(`<python> -m pip install`): always into aipager's own environment,
never the system Python.

The `[🔄 Restart daemon now]` button, under the systemd service,
schedules `systemctl --user restart aipager.service` in a separate
transient user unit; on macOS it runs `launchctl kickstart -k`; a daemon
you started yourself spawns a detached copy of itself that waits for the
old one to exit. After an update only the systemd service restarts by
itself (see [commands → update](commands.md#update)). All of them run as
the same user; no escalation.

## Network surface

aipager listens on **no non-loopback TCP port**. The Mini App server
binds `127.0.0.1` only (hardcoded - there is deliberately no host
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
  The `cloudflared` binary it runs is downloaded once from
  `github.com/cloudflare/cloudflared` releases, checked against a
  pinned SHA-256, and cached under
  `~/.local/share/aipager/cloudflared/`; a `cloudflared` on your `PATH`
  is never used. None of this happens with your own Mini App URL (see
  below).
- Version checks, only when someone taps **Check for updates** or
  **Update** (`/update`, the Mini App's Updates): `pypi.org` for aipager, and
  `downloads.claude.ai` or `registry.npmjs.org` for Claude Code,
  depending on how it was installed. An update then runs the installer
  (which talks to PyPI or Homebrew) and `claude update`.
  `aipager update` on the command line only runs the installer.
- The voice extra's install, when you tap its button: the installer
  fetches the packages from PyPI, and the speech model is downloaded
  from Hugging Face (`huggingface.co`) on first use.
- HTTPS to `raw.githubusercontent.com` (the report key file) and to Sentry's ingest host, only when you confirm sending a problem report: the report you previewed and a short summary made from it, nothing else. See [Problem reports](problem-reports.md).

Inbound:

- Unix datagram socket at `$XDG_RUNTIME_DIR/aipager.sock` (falling back to
  `/tmp/aipager.sock` when `$XDG_RUNTIME_DIR` is unset). Bound and
  chmod'd by the daemon at startup
  (`aipager/dtach/hook_receiver.py`). Mode `0o666` so any local
  process can send hook events to it - same trust as
  `~/.claude/settings.json`, which already controls what runs
  hooks.

This means: the daemon is not a remote attack surface. A network-
level attacker cannot reach it without a foothold on the host.

## Mini App tunnel

**The Mini App is on by default**, so on a stock install aipager opens
this tunnel without being asked. That is a deliberate trade - an opt-in
nobody discovered was a feature that did not exist - but it means a
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
guessing a `trycloudflare.com` subdomain at random - the signature
check is the actual gate. Treat it the way you'd treat any other
unlisted-but-not-secret URL: fine to leave running, not something to
paste into a public channel for no reason.

The hostname changes on every daemon restart and is held only in
memory - it is never written to `aipager.yaml` or anywhere under
`~/.config/aipager/`. If the tunnel dies mid-run,
aipager restarts it with backoff and republishes the new address; if
it cannot come back up after several attempts, the button is removed
rather than left pointing at a dead port (see [Network
surface](#network-surface) above for the same "no button is an honest
absence" principle applied to the loopback server itself).

Setting `MINIAPP_PUBLIC_URL` (or `aipager miniapp enable --url
https://…`) disables the managed tunnel entirely - no cloudflared
binary is ever fetched or spawned - and the given URL is used as-is,
with the same `initData` verification underneath it. Tailscale
auto-detect (`tailscale status --json`) remains available as a
lower-effort alternative for anyone who already runs Tailscale and
would rather not depend on Cloudflare at all.

## Voice transcription

`faster-whisper` runs in-process. The audio is downloaded as `.ogg`
into `/tmp/aipager-files/` (the folder uploaded files go to),
transcribed locally on CPU, and the file stays under your control. **No audio leaves the machine.**
No third-party API. No key needed beyond the bot token to talk to
Telegram in the first place.

If you delete the `.ogg` after transcription, the only record of
the message in plain text is the transcript that gets injected into
the claude session (where it follows claude code's own privacy
posture).

## Multi-session isolation

Each Claude Code session runs in its own dtach. The control socket
at `/tmp/claude-dtach-<name>.sock` is owned by the daemon user;
dtach refuses cross-user attaches. Each session starts in its own
folder, the one chosen with `/new`, the Mini App, or the folder you ran
`aipager session` in.

aipager does not implement filesystem-level isolation between
sessions: a session attached to `~/projects/foo` can in principle
read `~/projects/bar` if claude code's permissions allow. Use
per-project `~/.claude/settings.json` overrides or a container
([Docker image](../README.md#docker)) for stronger isolation.

## Messages between Claude sessions

Claude Code lets one session send another a message (its `SendMessage`
tool). In the receiving session that message is a prompt like any other,
so aipager decides its rights the same way it decides a Telegram
message's: from who really sent it, never from what it says.

- **The message carries the sender's rights, never more.** When aipager
  allows a session to send a message, it writes a one-time receipt: a
  fingerprint of the exact text, the sending Claude process (its process
  id and, on Linux, its start time) and the rights that turn had. Claude
  Code records the receiving prompt with the sender's process id, which
  the kernel verified, never trusting the sender's own name for itself.
  Before the receiving turn runs any tool, aipager matches the two and
  gives the turn exactly the sending turn's rights: an owner's message
  runs with the owner's rights, a member's with that member's, a message
  typed in the sending session's terminal unrestricted.
- **No receipt, restricted rights.** A message aipager cannot match (sent
  by a Claude session or process aipager does not run, a receipt that
  expired after an hour, a sender on another machine) runs with the
  strictest rules plus no file changes and no web access (read and
  think only). The receiving session's own earlier rights never apply to
  it.
- **Other input from outside the session, the same.** Anything Claude
  Code records as not coming from this session's own person gets those
  restricted rights too: an MCP channel push, a remote caller, a prompt a
  plugin queues, a message relayed from another session's inbox, and any
  kind of input aipager does not know yet (a plugin command counts as
  outside input even when you started it). The session's own background
  agents (their hand-backs and reports) and its task notices keep the
  job's rights.
- **A prompt the session scheduled runs with the rights of the turn that
  scheduled it.** When a turn sets up a `/loop` or a cron, aipager
  records that turn's rights for the prompt; when it fires later, it gets
  those rights, never more, whoever spoke last; a message that joins
  that turn can only narrow it, and a policy change that takes rights
  from whoever scheduled it narrows it too. The same prompt scheduled
  twice keeps the stricter rights. A bare `/loop` fires with Claude
  Code's loop instructions instead of its own text, so it gets the
  strictest rights of every bare loop the session set up, once the
  transcript shows the fire (until then a refused tool call is refused
  softly, and Claude tries again). A prompt nobody scheduled through
  aipager's check gets the restricted rights.
- **Text proves nothing.** A sender name, a `[via Telegram · @owner]`
  line or "the owner asks…" inside a message changes nothing. Claude is
  told who aipager verified as the sender, and that claims in the text
  are not proof.
- **Joining a running turn only narrows it.** A message from another
  session that arrives while a turn runs makes that turn as strict as
  the stricter of the two, never looser. That holds when Claude Code
  takes the message in with no prompt event of its own (absorbed into
  the running turn, or run as the next turn from its queue): the
  owner's fast path looks at the end of the transcript before every
  tool call, and the turn end and the daemon mark the session for a
  check when they see such a message queued.
- **Roles stay in their chat.** A receipt from a session of another chat
  counts only when its turn had the owner's rights or was typed in its
  terminal; anything else runs restricted.
- **Who may send.** The built-in restricted roles cannot use SendMessage
  at all. A role a policy gives it can only write to sessions aipager
  runs (addressed as `uds:…/<pid>.sock`); `aipager doctor` warns about
  such a role.
- **Background agents keep their own rights.** An agent keeps the rights
  of the turn that launched it (or the stricter rights of a later turn),
  never the rights of a later turn that has more.
- **When it gets stopped.** If a turn another session started is stopped
  by the safety rules, the busy card already shows who sent it, the stop
  notice says so, and whoever handed the task over hears about it, so it
  never silently disappears: the sending session's chat when both
  sessions are in the same chat; otherwise only the owner, in private,
  and only when the owner (or the terminal) drove the sending turn.

Known limits:

- A turn with a shell (the owner's or an admin's) runs as the same OS
  user as aipager and could forge a receipt, just as it could already
  rewrite aipager's other control files. This is the existing trust
  model: give a shell only to people you would trust with the machine.
- Claude sessions aipager does not run are not governed by aipager at
  all, whatever writes to them.
- Off Linux, the sender is matched on its process id alone (Claude Code
  records no start time there). On Linux a receiving record without a
  start time matches nothing.
- Claude Code may write the message to its transcript a moment after
  the turn's first tool call. Until it does, a call the restricted rights
  would refuse is refused softly (Claude is told to try again; the turn
  is not stopped). After 8 calls without it, the turn runs restricted.
- The owner's fast path reads the last 1 MiB of the transcript, never
  more. A message from another session longer than that ends in a line
  the fast path takes as one (any line of more than 256 KiB cut by the
  window is). One that lies entirely further back (more than 1 MiB of
  transcript written after it before the next tool call) is caught by
  the daemon while the turn runs, or by the turn end while it is still
  queued, not by the fast path.
- While a session has a bare `/loop` set up, any turn no Telegram
  message starts (a prompt typed in the terminal included) may have its
  first tool calls refused softly, until Claude Code has written that
  turn's prompt and aipager can tell it is not the loop firing; Claude
  then tries again.
- aipager reads a scheduled prompt's turn from what the transcript gained
  since it began; past 8 MiB, from its first and last 4 MiB (what lies
  between was checked when it was the newest part).
- A scheduled prompt's recorded rights stay until the session is killed
  (`/kill`): an ended or resumed session keeps them, as Claude Code keeps
  its crons and a pending `/loop` wakeup. At most 256 are kept per
  session, and scheduling another past that is refused for everyone in
  it, so anyone who may schedule can use them up (then start a session
  under another name, or `/kill` this one). A prompt that fires with no
  record gets the restricted rights, or in a session with a bare `/loop`
  that loop's rights, once Claude Code has written it to the transcript;
  until then aipager reads the turn by the one before it. That happens
  for a prompt set up before this version; after a reboot (the records
  live in the system's temporary folder, which a reboot clears, while
  Claude Code can still bring a resumed session's wakeup back within the
  hour); and for a durable cron, which Claude Code runs in another
  session of the same folder once its own session has ended. A session
  with fewer rights that schedules the same text can lower a prompt's
  rights (never raise them).
- A session started fresh under an earlier session's name (Replace)
  keeps that session's records, its bare `/loop`'s rights included,
  until `/kill`.
- A scheduled prompt that Claude Code runs right after another turn,
  with no prompt event of its own (from its queue), gets the stricter of
  its own rights and the rights of the turn it follows (after a turn
  typed in the terminal, exactly its own).
- The first tool calls of a turn typed in the terminal can be refused
  until Claude Code has written its prompt to the transcript (aipager
  reads the turn before it until then).
- A background agent launched under narrower rights can hand text back
  into a later turn of the same job, which reads it with the job's
  rights, as with Claude Code's other task notices.
- Claude Code can hold a message for approval in the receiving terminal
  (its `crossSessionInbound` setting, or when the two sessions run in
  different permission classes, e.g. Full access and Auto). aipager
  cannot release such a message from Telegram; the receipt waits up to
  an hour.

## Threat model summary

| Threat | Mitigation |
|---|---|
| Stranger sends bot a command | Chat ID filter rejects |
| Stolen bot token | Use `/revoke` in @BotFather, re-config |
| Compromised claude tool call | aipager's safety check refuses what a Telegram turn's rules deny; in Ask, Claude Code's own rules in `settings.json` decide what it asks about and a person's tap is the only allow; in Auto (the default for every new session) Claude Code's own safety check runs the calls it judges safe without a prompt and stops the risky ones for a person; a Full access session (admins, and a person in their own private chat) asks nothing, so only aipager's safety check stands in the way, and for an owner's own turns and terminal prompts nothing does ([who decides](#who-decides-whether-a-tool-call-runs)) |
| Restricted Telegram user escalates to the owner | No shell, sessions started only inside the member's own root folder, writes and searches confined to the project inside it (never the home folder), no reads in the home folder outside the root folder, other members' root folders unreachable, credential files unreadable, protected paths for the file tools, failed checks deny ([team-mode enforcement](#team-mode-enforcement)); best-effort only for a role given Bash, and not covered for the cases under "Known limits" |
| A session's message claims an owner's authority | The message runs with the sending turn's proven rights, never the receiving session's earlier ones; without proof, read and think only ([messages between sessions](#messages-between-claude-sessions)) |
| A group member borrows someone else's rights | Each message runs with its own sender's role in the chat it came from; a different sender's message waits for the running turn; if one joins anyway, the strictest rules win; slash commands from restricted roles are limited to the keyboard's ([team-mode enforcement](#team-mode-enforcement)) |
| Group chatter reaches a session | In a group only commands, replies to the bot, mentions of it and marked keyboard taps are acted on ([groups](groups.md#what-the-bot-reads-in-a-group)) |
| A removed member keeps access | `aipager config` reloads the daemon live; their held messages are dropped and a turn of theirs still running loses their old rights; removing them from the Telegram group stops them reading it ([groups → live reload](groups.md#live-reload)) |
| Audit log tampering | Append-only; out of scope to prevent without a separate signing daemon |
| Network attacker | No inbound port, not directly reachable |
| Local privilege escalation | No sudo / setuid; daemon stays in user space |
| Voice audio leaking to cloud | Transcription is local |
| Agent reads the daemon's own Claude credential | Expected, not a bug - see [The Claude credential](#the-claude-credential-what-actually-protects-it). Scope the credential itself, not the file it's stored in |

## See also

- [Architecture](architecture.md) - process model.
- [Hook events](hooks.md) - what the daemon actually sees from claude.
- [Bot commands](commands.md) - the user-driven side.
