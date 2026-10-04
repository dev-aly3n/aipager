# Team / group mode

aipager runs by default as a 1:1 DM bot — you and the bot, no one
else. **Team mode** opens it up to a Telegram group with multiple
developers, the way teams use `@gitbot` or `@deploybot`: anyone on
the allow-list can mention `@aipagerbot` to inject prompts, approve
permission requests, or check status.

Team mode is opt-in. Personal-mode installs are unaffected.

## Decide carefully

Adding a Telegram user to the team gives them **code-execution
rights on the host running the daemon**. They can:

- Inject prompts that claude turns into shell commands, file
  edits, network calls.
- Approve / deny tool calls — your `~/.claude/settings.json` still
  decides which tools claude asks about, but anyone whose role can
  approve can hit Allow.
- Create / kill / switch sessions.

Treat the allow-list the same way you treat SSH access to the
machine. The audit log
([`~/.claude/aipager-audit.jsonl`](security.md#audit-log)) records
who did what so you can review later, but it's after-the-fact.

## Setup

Run `aipager config`. The wizard adapts:

- **No config yet** → first-run wizard: bot token → your own DM
  (auto-detected or pasted) → owner access for your account → deps →
  Claude Code hooks. It writes your DM as the first chat, then offers to
  add a group or another person's DM. It does not ask for a default
  mode: new sessions start in Auto for owners and admins and in Ask for
  everyone else, and you change that in Telegram under `/settings` →
  New sessions.
- **Config exists** → edit menu. It shows the current chats and offers:
  Add a group scope, Add a DM scope, Edit a scope (Add a member for a
  group, Rename, Edit scope deny_tools, Remove this scope), Edit a member
  (Set role, Edit member deny_tools, Remove member), Test bot
  reachability, View policy, Re-install Claude Code hooks and Refresh bot
  token.

To add someone to a group that is already set up, use Edit a scope →
the group → Add a member. "Add a group scope" for a group that is
already set up stops with "This group is already set up. Use Edit a
scope → Add a member." and changes nothing.

The wizard writes everything to `~/.config/aipager/aipager.yaml`
(mode 0600): the bot token, the chat(s) the bot serves, and each
chat's members with their roles. What each *role may do* lives in
the user-owned `~/.config/aipager/policy.yaml` — see
[Rules](#how-rules-work) below. Installs configured with the older
`team.yaml` format keep working and are migrated automatically —
by the daemon at startup, or by the wizard on its next run.

For the group chat ID you can paste it manually, or pick
"Auto-detect" and let the wizard watch for a `/start` in the group
(add the bot first). The same auto-detect works for member user IDs:
the wizard takes the newest message from someone not added yet and
asks "Is this the person to add?" before using it (answer No and it
looks past them), then suggests their Telegram username as the label.
It says where it watches:

- **"Watching through the running daemon"**: while `aipager start`
  runs, it receives every message for the bot, so the wizard reads what
  the daemon noted instead (`~/.claude/aipager-pending-users.json`). The
  daemon notes who sends the bot a message in a group it does not serve
  yet (the group's id, title and type, and the person's id, username and
  name), and anyone who is not a member of a group it does serve. It
  never answers in a group it does not serve. A DM from someone who is
  not set up is answered and not noted, so to add a person's DM while
  the daemon runs, paste their id (or stop the daemon first).
- **"Watching Telegram directly"**: when the daemon is stopped, the
  wizard asks Telegram for the bot's recent messages itself.

Your own DM in the first run is always read from Telegram. A message
from an anonymous admin or one posted as a channel does not count: its
sender id (1087968824, 136817688 or 777000) is shared by everyone who
posts that way, so the wizard refuses it, typed or detected. Ask the
person to post as themselves.

When a group is added, the wizard asks one optional question: "Block
file edits (Write, Edit) for `user` members in this group? Admins and
the owner are not affected." The answer defaults to No: a `user`
already writes only inside the session's folder. With Yes, every Write
and Edit from a `user` member's turn in that group is denied without a
prompt (there is nothing to tap); an admin or the owner has to send
that work instead.

When you add a group, the wizard first offers you (the member of your
own DM) as the group's first member with the `owner` role; you can
skip it. For every other member the role list includes `owner`, with a
warning: an owner has full control of the machine, including aipager's
config, the bot token and Claude's credentials. Give `admin` to people
who should use Auto, `/settings` and `/update` in the group.

### Live reload

After every scope or member change (add or remove a group or DM,
add or remove a member, set a role, edit `deny_tools`, rename a
scope), `aipager config` sends the running daemon a live reload
(**SIGUSR1**) and says "Scopes reloaded live". The reload re-reads
`aipager.yaml` and `policy.yaml` (and a legacy `team.yaml`). If the
wizard cannot find the daemon, it tells you to restart it instead.

What a reload applies at once:

- who may use the bot in each chat, and with which role and rules;
- a new group or DM starts receiving messages and gets its `/` menu;
  a removed one stops (its sessions keep running until you end them);
- a held message (one waiting for a prompt to be answered or for
  someone else's turn to end) from a person who may no longer send
  in that chat is dropped: it gets a 🤷 and the bot replies
  "Dropped: @bob is no longer allowed to send here.";
- a running turn of a person who was removed or lost rights keeps
  only the strictest of its old rules and the new ones (the
  restricted floor for a removed person) for the rest of the turn.
  A turn typed in the terminal is not changed, unless a Telegram
  message joined it: aipager does not record whose, so such a turn
  is narrowed when any owner (or, in a DM, its member) loses rights.
  A turn that was already running when the daemon restarted (still
  working, or waiting on a question or permission) is narrowed for
  everyone in its chat, and any owner, who lost rights. A promotion
  never widens a running turn: the new rights apply from that
  person's next turn.

Restart is still required for the **bot token** and the **default
mode** (`aipager config` says so after those).

To trigger a reload manually (e.g. after a hand-edit), signal the
daemon process only. Under the background service:

```sh
pid=$(systemctl --user show -p MainPID --value aipager.service)
[ "${pid:-0}" -gt 0 ] && kill -USR1 "$pid"
```

(The check matters: when the service is not running, `MainPID` is
`0`, and `kill -USR1 0` would signal your own shell's processes.)

For a daemon you started yourself with `aipager start`, send
`kill -USR1 <pid>` to that process. Do not signal every match of
`pgrep -f 'aipager start'` (a shell or container that runs aipager
matches too, and SIGUSR1 stops a process that does not handle it),
and do not use `systemctl --user kill` without `--kill-whom=main`
(the service's sessions share its group and would be stopped).

If `aipager.yaml` or `policy.yaml` is malformed at reload time, or
`aipager.yaml` is missing, the daemon logs a warning and keeps the
previous config in memory. A typo or a file moved aside can neither
lock you out nor let anyone in: a reload never switches a daemon
that has scopes back to personal mode (only a restart does).

### When Telegram upgrades the group

Telegram turns a basic group into a supergroup when you make it public,
turn on topics, pass 200 members, or change some admin rights, and the
upgrade gives the group a new chat id. aipager follows it by itself: when
it sees the upgrade (Telegram's notice in the group, or the first post to
the old id), it moves the group's scope in `aipager.yaml` to the new id,
moves the group's sessions, message targets and `/settings` with it, and
posts one line in the group: "This group was upgraded by Telegram.
aipager moved with it, nothing to do." The pinned status bar starts again
in the upgraded group. The daemon log has a warning with both ids.

Session names inside aipager keep the old id (you never see them), and
`aipager.yaml` keeps a short `chat_migrations` record of the move, so a
session started before the upgrade still belongs to the upgraded group,
even after a restart. A session's internal name with the old id
(`/name__g<old id>`) works in the upgraded group and nowhere else. If
the daemon was stopped during the upgrade, it notices at its next start.
A turn that is running at the moment of the upgrade finishes and its
answer reaches the upgraded group, but its progress card is not shown
again for the rest of that turn. If the new id is already a scope of its own,
nothing moves and the log says so; remove one of the two with
`aipager config`. "Test bot reachability" in `aipager config` shows the
new id when it meets an upgraded group.

### Privacy mode, admin rights, and what the bot reads

On `@BotFather`, leave **privacy mode ON** (the default). Telegram then
sends the bot only commands, replies to its messages and messages that
mention it. Making the bot a group **admin** (it needs that to pin the
group's status bar, see [What everyone sees](#what-everyone-sees))
changes this: Telegram sends an admin bot every message in the group,
whatever the privacy setting.

aipager ignores group chatter either way. In a group it acts only on a
message that is:

- a command (`/status`, `/x1 fix the tests`, or the same with
  `@aipagerbot` after the command, which Telegram adds when you pick it
  from the menu). A command for another bot (`/x1@otherbot`) is ignored;
- a reply to one of the bot's messages;
- a message that mentions the bot (`@aipagerbot fix the tests`);
- a tap on the keyboard the bot puts in the group (a session's name,
  status, stop, new, Templates, Commands and their buttons). In a group
  every keyboard button starts with a small square (`▫️ stop`,
  `▫️ Clear`), and only that marked text counts as a tap. Typing the
  bare word is ordinary chatter: a member who types `stop`, `Clear`,
  `Opus` or a session's name in the group changes nothing. To use a
  keyboard word on purpose without the keyboard, address the bot:
  `@aipagerbot stop`, or `stop` as a reply to one of its messages.

Anything else (team members talking to each other, a photo or voice
note that is not a reply to the bot) gets no reaction and no reply.
Edited messages are ignored everywhere: edit a message and nothing
happens, send it again instead.

**Anonymous admins must post as themselves.** A group admin who has
"Remain anonymous" on, or a message posted as the group or as a
channel, does not say who wrote it: Telegram shows the same sender for
every anonymous admin. aipager cannot give such a message anyone's
role, so it answers "Post as yourself to use the bot." (once per chat
until the daemon restarts) and does nothing else. It is never listed
as a pending user, and the wizard refuses that shared id. Turn
"Remain anonymous" off for yourself in the group's admin settings, or
post from your own account.

### Talking to a session in a group

Reply to one of a session's messages (its answer, its card) and your
message goes to that session. Or mention the bot: `@aipagerbot fix the
tests` goes to your current session as `fix the tests` (the
mention is removed, so Claude never sees it as a file mention).
`@aipagerbot status` and `@aipagerbot x2` work like typing `status` or
`x2` in a DM, and `@aipagerbot` alone shows where messages go. A photo,
file or voice note works the same way: send it as a reply to the bot, or
mention the bot in the caption. For an album, put the caption on its
first item: the album's items that arrive after the one with the
caption, mention or reply are taken, any before it are not. Commands
work as before, and so do `/x1 your message` and a caption that starts
with `/x1`. A command after the mention (`@aipagerbot /stop`) is not
run: the bot asks you to send `/stop` on its own.

The cards that wait for an answer say so in a group: the new-session
card says "Reply to this message with a name", the rename card "Reply
to this message with the new name", and the Ready card, the reply to
`/x1`, `/start` and `/help` say to reply to a session's message or
mention the bot. Each person's `/new` card and rename question are
their own: the card shows whose it is, only that person's reply answers
it, and two people can start or rename sessions at the same time.

Each person also has their own current session: the one they last
switched to, sent a message to, replied to or started. Another member's
session answering does not change it, and neither do other people's
messages, so a mention, a template or a keyboard Clear goes to your
session, and the reply names it ("🧹 /clear sent to x1"). With no current
session of your own and more than one session running, the bot asks
"Which session?" and sends nothing; with only one running, it goes
there. If your own current session has ended, a message is not sent
to the one session still running (it may be someone else's): the bot
answers "x1 has ended. Which session?" with a button per running
session and, when it can be resumed, "▶️ Resume x1", and sends nothing.
The pinned bar has no "Messages go to" line in a group.

**The keyboard is per person.** Templates, Commands, Model › and
« Back change only the keyboard of the person who taps them: the bot
answers as a reply to that person's tap, and nobody else's keyboard
moves. « Back goes up from where you are, whatever other members are
browsing. The main keyboard sent by `/start` is the whole group's, and
puts everyone back on the main keyboard. After upgrading from a
version without the marked buttons, a tap on a button of the old
keyboard may be ignored: send `/start` once to give the group the new
keyboard. A private chat's keyboard has no marker and works as before.

`/app` opens the Mini App only in a private chat. In a group it is not
in the `/` menu; typed there, it tells a member whose own chat with
the bot is set up to "DM the bot and send /app there", and anyone else
"The Mini App opens from your own chat with the bot. Ask the operator
to add you."

## Roles

Four built-in roles (see `aipager/safety.py`):

| Role | Send prompts | Approve | Manage (Auto, `/settings`, `/update`) | Bypass deny rules | Bypass the safety floor | Bash | Writes | Buttons |
|---|---|---|---|---|---|---|---|---|
| `owner` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | anywhere | all |
| `admin` | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ (best-effort rules) | anywhere the floor allows | all |
| `user` | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | the session's project folder + scratchpad (never the home folder) | all but Auto and installs |
| `read_only` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | the session's project folder + scratchpad (never the home folder) | look only |

- **owner** — full control, including the built-in safety floor.
  There should be exactly one: the person who runs the machine.
- **admin** — can do everything an owner can in Telegram and the Mini
  App: switch sessions to Auto (new sessions start in Auto for them),
  change a group's `/settings`, run `/update`, install the voice extra.
  Two things stay with the owner: changing members and roles (only
  `aipager config` on the machine does that; nothing in Telegram or the
  Mini App can), and bypassing the safety floor (aipager's config and
  bot token, Claude Code's credentials, aipager's control files). An
  admin also bypasses role deny rules (their Allow tap works on a
  restricted tool). An admin keeps Bash, and with a shell the floor's
  command patterns are best-effort, not a boundary: only make someone
  an admin if you would give them a shell on the machine.
- **user** — deny rules apply; an Allow tap on a rule-denied tool is
  auto-rejected. No Bash, and no other tool that runs code (PowerShell,
  Monitor, scheduled prompts, workflows — `safety.CODE_EXECUTION_TOOLS`),
  no `SendMessage` to other sessions, no switching worktrees.
  File writes land only inside the session's folder and its Claude Code
  scratchpad, and never in the folder's `.claude/`, `.git/` or
  `.mcp.json` (each of those runs commands). `Grep`/`Glob` search only
  inside those folders, with plain relative globs. A session folder that
  is your home folder, `/` or a folder above your home folder does not
  count: there a `user` can write and search only in the scratchpad, so
  `/new` and the Mini App do not offer them those folders to start in
  (they can still make a new folder inside your home folder with New
  folder and start there). Reads follow the protected-path rules, and
  your credential files are never readable: `~/.ssh`, `~/.gnupg`,
  `~/.aws`, `~/.config/gh`, `~/.git-credentials`, `~/.netrc`,
  `~/.docker/config.json`, `~/.kube`, `~/.config/gcloud`, `~/.azure`,
  `~/.password-store`, `~/.pgpass`, `~/.npmrc` and `~/.pypirc`.
- **read_only** — observers. They see every message and can call
  `/status`, but their text / voice / file messages are ignored. The
  first one in each chat gets one reply saying their role is
  `read_only`; after that the bot stays silent (no reply, no reaction)
  until the daemon restarts. Same
  tool and write limits as `user`, in case one of their messages ever
  reaches a session.

Custom roles can be defined in `policy.yaml`; the four above are the
defaults it layers on top of. A role field you set replaces the
built-in value. To give `user` back Bash (only for people you would
give a shell), name it in `allow_tools` (which then also becomes that
role's allow-list) or replace its `deny_tools`:

```yaml
roles:
  user:
    # the built-in list minus Bash
    deny_tools: [PowerShell, Monitor, REPL, Workflow, CronCreate,
                 RemoteTrigger, AppifactRepl, self_hosted_runner_spawn_local,
                 self_hosted_runner_requeue_session, SendMessage,
                 EnterWorktree, ExitWorktree]
```

`aipager doctor` then warns: `role user has Bash: its safety rules are
best-effort, not a boundary`. If your `policy.yaml` already set
`deny_tools` for `user` before this default existed, your list still
replaces it, so that role has Bash until you add it back; the doctor
warning tells you. A folder confined to the session only means
something if the session was started in a project folder: a session
started in your home folder can write anywhere in it. See
[security → team-mode enforcement](security.md#team-mode-enforcement)
for what these limits do not cover.

## `aipager.yaml` schema (team parts)

```yaml
schema_version: 3
bot_token: "…"

scopes:
  - kind: group
    chat_id: -100123456789
    members:
      - id: 12345        # Telegram user ID (NOT a label, NOT a chat ID)
        label: alice     # how the user is referenced in chat (@alice)
        role: owner
      - id: 67890
        label: bob
        role: user
      - id: 11111
        label: charlie
        role: read_only
```

A DM scope (`kind: dm`) and a group scope can coexist — the bot then
serves both chats, with sessions namespaced per chat.

## How rules work

`~/.config/aipager/policy.yaml` defines per-role rules. Validate it
with `aipager policy validate`. Supported fields per role:

- `deny_tools` — tool names auto-denied without prompting
  (e.g. `Write`, `Edit`, `Bash`, `WebFetch`).
- `allow_tools` — if non-empty, an allow-list: everything else is
  denied for that role.
- `deny_bash_patterns` — patterns matched against `Bash` inputs.
- `deny_paths_no_access` / `deny_paths_no_write` — path rules. For
  `user`, `read_only` and every role you define without
  `bypass_role_denies`, `deny_paths_no_access` starts as the credential
  files listed under **user** above; setting it replaces that list, so
  copy the ones you want to keep into yours (a running turn that another
  message joins is held to the list again until it ends). A
  rule with no leading `/` or `~` (`**/.env`) matches anywhere, so
  while one is in place every `Grep`/`Glob` of a restricted role is
  denied (any search could read such a file), which halts that turn.
- `can_manage` — `true` lets the role use Auto, change a group's
  `/settings`, run `/update` and install the voice extra (built-in:
  `owner` and `admin` true, `user` and `read_only` false; a role you
  define starts false). It never lets anyone change members or roles,
  and never bypasses the safety floor.

Underneath all roles sits a built-in **safety floor** (protected
paths and command patterns) that only `owner` bypasses. The protected
paths hold for Claude Code's file tools (`Read`, `Write`, `Edit`,
`MultiEdit`, `NotebookEdit`, `LSP`, `Grep`, `Glob`); a restricted
role's `Grep`/`Glob` must also stay inside the session's folder (a
search of `~/.config` for `aipager/**` is denied). Tools such as
`WebFetch` or `SendFile` are not path-checked. A Telegram turn aipager
cannot attribute to a sender gets the floor, which is at least as
strict as `user`. Retry runs as whoever tapped it if they sent the
prompt being retried, and on the floor otherwise.
The command patterns only matter for a role that has Bash, and there
they are a filter, not a wall — see
[security → team-mode enforcement](security.md#team-mode-enforcement).

When claude asks for permission to use a rule-denied tool and the
driver's role does not bypass rules:

- The bot **does not show the permission prompt**.
- It writes a deny back to claude.
- It posts a notice in the chat naming the tool, the person and their
  role, with the call's summary on the next line, e.g.
  `⛔ jim · Write blocked for @bob (role user)`.
- It writes an audit record with `denied: true`.

## Who can tap what

Every button checks the role of the person who taps it, in the chat the
button is in, at the moment they tap it. Who sent the command that made
the button does not matter.

- **Looking is open to every member.** `read_only` members can page
  through `/resume`, open `/status`'s Ended view, a session's ⋮ menu,
  its mode or its diff, browse `/settings`, and close a menu. They
  cannot act: any other button (including Cancel on someone's End,
  Restart or Delete confirm) answers "Your role can't do that here."
- **Answering Claude needs the role's `can_approve`.** Allow, Allow
  always, Deny, a question's options, Submit, Continue and the pinned
  bar's Answer. `read_only` has it off.
- **Acting on a session needs the role's `can_prompt`.** Stop, Retry,
  Compact, Send now, End, Restart, Rename, Delete, Resume, the mode
  switch, Clear all, a session's preferences and the `/new` cards.
- **Switching to Auto needs an admin** (`owner` or `admin`, the same
  rule as `/mode auto`): Yes, switch and Stop task & switch on a
  card that switches to Auto, and Resume as Auto. Resuming a session
  that was in Auto (typed `/resume x1`, the picker, Resume on the
  `/new` name card, or the Mini App) brings it back in Ask for anyone else, and the
  reply says so: "Resumed x1 in Ask: Auto needs an admin."
- **Installing needs an admin:** the voice extra's Install and
  Restart buttons, like `/update`'s.
- Some buttons narrow this further: the `/new` cards answer only the
  person who sent `/new`, and changing a group's `/settings` needs an
  admin.
- **A confirm card belongs to whoever asked for it.** In a group, the
  End (`/kill`, ⋮ End session), Restart, Delete and mode-switch confirm
  cards, and the session pickers `/kill`, `/restart`, `/delete` and
  `/mode` show, answer only the person who sent the command or tapped
  the button that drew them. Anyone else's tap, Cancel included, gets
  "This is @alice's card. Send /kill x2 for your own." (with that
  card's command) and changes nothing. The `/mode` card itself (which
  mode a session is in) is everyone's: a switch tapped on it draws a
  confirm that belongs to the tapper. A card's owner is remembered
  until it is used or cancelled, and not across a daemon restart.
- A button only works in its own session's chat. One tapped anywhere
  else answers "This button belongs to another chat." and does nothing,
  and Resume always resumes the session the button names, never another
  chat's session with the same name.

## Who a message runs as

Enforcement keys off **who sent each message**, not whose turn it
happens to interrupt. A person listed in several chats has the role of
the chat they act in: someone who is `user` in the group and `owner` of
their own DM sends, taps and runs commands in the group as `user`.

- Every Telegram message carries its sender's identity and rules
  into the session (see
  [security → team-mode enforcement](security.md#team-mode-enforcement)).
  A message held back — because a permission prompt was open, or
  because someone else's message was still waiting — is delivered
  later **with the original sender's permissions**, not whoever
  drove the session most recently.
  Claude sees the person who sent each message, never the one who
  drove the session before them, and whoever sends a message becomes
  the session's driver, whether they typed it, used `/<name>`, a
  template or a keyboard command. A sender aipager no longer knows
  (removed while their message waited) runs on the floor.
- Slash commands other than the Commands keyboard's (plus `/compact`)
  and model switches need an admin: a `user` or `read_only` member who
  sends `/x1 /deliver …`, a file captioned `/x1 /review`, or
  `/new x1 /review the diff` gets "That command needs an admin." and a
  🤷, and nothing reaches Claude. Owners and admins send any.
- Messages from different users are never merged into one turn:
  while one user's message is still waiting to be picked up, or while
  another person's turn is running, a message is held until that clears
  and then runs as its own turn with its sender's rules. A turn typed in
  the terminal counts as the owner's: only an owner's message (or, in a
  DM, its own member's) joins it. A turn whose background agent is still
  working counts as running until the agent is done.
  If a mixed turn happens anyway, it runs under the *most restrictive*
  combination of the contributors: privileges never widen, and the
  rest of a running turn keeps the strictest rules of everyone who
  joined it.
- Buttons that act on a running turn (Stop, Kill, Restart, Replace,
  perms-switch) refuse when tapped from a card belonging to an
  earlier task — `That task already finished - …` — so a stale tap in a
  busy group can't destroy someone else's current work.

## What everyone sees

**Permission audits.** Every Allow / Deny is replied to in chat:

```
✅ [jim] · Allowed by @alice · Bash: ls -la /tmp
🚫 [jim] · Denied by @bob · WebFetch: https://example.com
⛔ jim · Edit blocked for @bob (role user)
```

So even if you weren't watching live, scrolling back tells you
exactly who decided what.

**Who changed a session.** A tap on a button is seen only by the person
who taps it, so in a group every result that changes a session names
who did it:

```
⚠️ x1 · Stopped by @bob
⏹ Ended x2 by @bob
🔄 [x1] restarted by @alice.
🗑️ Deleted [x3] by @carol.
⚙️ Switched to 🤖 Auto by @alice.
🔁 Model switched to Opus by @bob.
Changed by @alice.          (under /settings)
```

A private chat has one person, so its lines are unchanged. A change
made in the Mini App is posted in the chat as "... from the Mini App"
(a Stop from it also names who on the session's card).

**Message states.** 👀 on a message means it was sent to the
session; 👍 means Claude took it; 🤷 means it was dropped before
Claude took it. `/whoami` shows your own id and role.

**The pinned status bar.** The group gets its own pinned status bar
(see [commands → the pinned status bar](commands.md#the-pinned-status-bar))
listing only this group's sessions, if the bot is an admin allowed to
pin. Without that right the bar is skipped for the group: the message is
deleted rather than left unpinned in the history.

**On-disk audit.** `~/.claude/aipager-audit.jsonl` records every
decision with `user_id`, `username` and scope fields so admins can
post-hoc reconstruct what each user did.

## Privacy considerations

- The chat filter still applies. Even with team mode, the bot only
  listens to **the configured chat(s)**. Adding the bot to a second
  group doesn't activate it there: it never answers there. It does
  note who sends it a message there (their id, username and name, and
  the group's id and title) in `~/.claude/aipager-pending-users.json`,
  so `aipager config` can add that group.
- Read-only users **can read** prompts and tool inputs. They can't
  act, but they see everything. If you need to hide some
  conversations from an observer, that observer doesn't belong in
  the group.
- Every session belongs to one chat, and everything about it goes
  there. A session started in the terminal (`aipager session <name>`)
  belongs to the owner's DM on an install with a DM and a group, never
  to the group. The owner's DM is the DM whose member has the `owner`
  role (a role with `bypass_safety`); with several DMs and none of them
  the owner's, it is the first DM listed. A session's name only works
  in its own chat: typing another chat's session name answers "Unknown
  session".
- `aipager.yaml` and `policy.yaml` are mode 0600 (owner-only).
- The audit log is owner-only (`~/.claude/aipager-audit.jsonl`).

## Revoking a user

1. Run `aipager config` → Edit a member → the chat → them → Remove
   member (or hand-edit `aipager.yaml` and delete their member
   entry). To take away their right to prompt but keep them reading,
   use Set role and pick `read_only` instead. (To add someone back
   later: Edit a scope → the group → Add a member.)
2. The wizard reloads the daemon live (SIGUSR1), no restart needed.
   After a hand-edit, send the signal yourself (see
   [Live reload](#live-reload)). At once, their held messages are dropped (🤷 and a "Dropped" reply)
   and a turn of theirs that is still running loses their old rights
   for the rest of the turn.
3. Optionally also kick them from the Telegram group.

Step 2 is the security-critical one: until the reload lands, the
previous allow-list is still in memory. If the wizard says to
restart the daemon instead, it could not reach it: restart it. If
it says "Not applied", fix the problem it names: the daemon keeps
the previous config until then.

## Related docs

- [Architecture](architecture.md) — process model.
- [Bot commands](commands.md) — interface reference.
- [Security model](security.md) — trust boundary, threat list.
- [Troubleshooting](troubleshooting.md) — `aipager doctor` reference.
