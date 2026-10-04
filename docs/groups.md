# Team / group mode

By default aipager is a private bot: you and the bot in one DM, no one
else. **Team mode** opens it to more people: a Telegram group where
several developers drive sessions, other people's own DMs with the bot,
or both. Each chat has its own members, and each member has a role that
decides what they may do there: send prompts, answer Claude's questions
and permission prompts, manage sessions, or only look.

Team mode is opt-in. An install with only your own DM works as before.

## Decide carefully

Adding a Telegram user to a chat gives them **code-execution rights on
the machine running the daemon**. Depending on their role they can:

- send prompts that Claude turns into shell commands, file edits and
  network calls;
- answer permission prompts (Allow / Deny). Your
  `~/.claude/settings.json` still decides which tools Claude asks
  about, but anyone whose role can approve can tap Allow;
- start, stop, end and switch sessions.

Treat the member list the way you treat SSH access to the machine. The
audit log ([`~/.claude/aipager-audit.jsonl`](security.md#audit-log))
records who did what, but only after the fact.

## Setup

Everything is set up with `aipager config`. You never edit a file by
hand.

**First run** (no config yet). The wizard asks, in four steps:

1. the bot token;
2. your own DM with the bot (auto-detected, or pasted);
3. whether your account is the `owner` (the default; say No and you
   are an `admin` instead, which keeps the safety floor on for you);
4. it checks the dependencies and installs Claude Code's hooks.

It writes your DM as the first chat. It does not ask for a default
mode: its summary says "New sessions start in Auto for owners and
admins; change it in Telegram under /settings → New sessions." Then it
asks "Connected. Add a team group or other people now?" with Add a
group, Add a person (DM) and I'm done.

**Later runs** open the edit menu. It shows the chats you have and
offers:

- **Add a group scope**: a new group, with its members (below).
- **Add a DM scope**: another person's own DM with the bot. A DM has
  one member; you pick their role (default `user`; pick `owner` only
  for a one-person install, such as a friend's own container). For a
  person who already has a DM set up, the wizard says so and changes
  nothing: use Edit a member to change their role.
- **Edit a scope**: for a group, **Add a member**; for any chat,
  Rename, Edit scope deny_tools and Remove this scope (the last chat
  cannot be removed). End a chat's sessions before you remove it: its
  running sessions keep posting there, and no chat can reach them from
  Telegram any more (from the machine, `aipager session ls`
  and `aipager session kill <name>` still can).
- **Edit a member**: Set role, Edit member deny_tools, Remove member
  (not in a DM, and not a group's last member). For an owner or admin
  (or a role of yours with `bypass_role_denies`), Edit member
  deny_tools only says that blocked tools do not apply to that role.
- Test bot reachability, View policy, Re-install Claude Code hooks and
  Refresh bot token.

### Adding a group

1. Add the bot to the group, then pick Add a group scope (or Add a
   group in the first run).
2. **The group's id.** Choose auto-detect and send `/start` in the
   group, or paste the id (a negative number). The wizard sends a test
   message and asks whether it arrived. A group that is already set up
   stops here with "This group is already set up. Use Edit a scope →
   Add a member." and nothing changes.
3. **A label** for the group (shown in the wizard and the Mini App).
4. **You first.** When you are the owner of your own DM, the wizard
   asks to add you as the group's first member with the `owner` role
   (default Yes; you can skip it). Without an owner or admin in the
   group, nobody there could use Auto, `/settings` or `/update`.
5. **The other members**, one at a time: how to find them (auto-detect
   or paste an id or `@handle`), a label (shown as `@label` in the
   chat), and a role (default `user`). The role list includes `owner`,
   with a warning: an owner has full control of this machine, including
   aipager's config, the bot token and Claude's credentials. Give it
   only to yourself. Give `admin` to people who should use Auto,
   `/settings` and `/update` in the group.
6. **One optional rule:** "Block file edits (Write, Edit) for `user`
   members in this group? Admins and the owner are not affected." The
   default is No: a `user` already writes only inside the session's
   project folder. With Yes, every Write and Edit in a turn of a member
   whose role does not bypass role rules (`user`, `read_only`, and
   roles you define without `bypass_role_denies`) is denied without a
   prompt, and the turn stops. An admin or the owner has to send that
   work instead.

To add someone to a group that is already set up, use Edit a scope →
the group → Add a member. Everyone already in the group keeps their
place and role.

### Finding ids (auto-detect)

Auto-detect for a member takes the newest message from someone not
added yet and asks "Is this the person to add?" before using it (answer
No and it looks past them). It suggests their Telegram username as the
label. In a group, the person has to address the bot: mention it or
send `/start` (see [what the bot reads](#what-the-bot-reads-in-a-group)).
The wizard says where it watches:

- **"Watching through the running daemon"**: while `aipager start`
  runs, the daemon receives every message for the bot, so the wizard
  reads what the daemon noted in `~/.claude/aipager-pending-users.json`.
  The daemon notes who addresses the bot in a group it does not serve
  yet (the group's id, title and type, and the person's id, username and
  name), and anyone who is not a member of a group it does serve. It
  never answers in a group it does not serve. A DM from someone who is
  not set up is answered and not noted, so to add a person's DM while
  the daemon runs, paste their id (or stop the daemon first).
- **"Watching Telegram directly"**: when the daemon is stopped, the
  wizard asks Telegram for the bot's recent messages itself.

Your own DM in the first run is always read from Telegram. A message
from an anonymous group admin, or one posted as the group or as a
channel, does not count: its sender id (1087968824, 136817688 or
777000) is shared by everyone who posts that way, so the wizard refuses
it, typed or detected. Ask the person to post as themselves.

### Where the config lives

The wizard writes `~/.config/aipager/aipager.yaml` (mode 0600): the
bot token, the chats the bot serves, and each chat's members with their
roles. What each role may do lives in `~/.config/aipager/policy.yaml`,
which is yours: the wizard never writes it (see
[How rules work](#how-rules-work)). Installs set up with the older
`team.yaml` format are migrated automatically, by the daemon at start
or by the wizard on its next run.

### Live reload

After every chat or member change made from the edit menu (add or
remove a group or DM, add or remove a member, set a role, edit
`deny_tools`, rename a chat), and after a group or person added at the
end of the first run or an unfinished group you resume (one reload for
everything you added there), `aipager config` sends the running daemon a live reload (**SIGUSR1**)
and says "Scopes reloaded live (no daemon restart needed)". The reload
re-reads `aipager.yaml` and `policy.yaml`. If the config on disk would
be refused, the wizard says "Not applied" with the reason, and the
daemon keeps its previous config until you fix it. If the wizard cannot
tell for sure which process is the daemon, it tells you to restart it
instead.

What a reload applies at once:

- who may use the bot in each chat, and with which role and rules;
- a new group or DM starts receiving messages and gets its `/` menu;
  a removed one stops (its sessions keep running until you end them);
- a held message (one waiting for a prompt to be answered or for
  someone else's turn to end) from a person who may no longer send in
  that chat is dropped: it gets a 🤷 and the bot replies "Dropped: @bob
  is no longer allowed to send here.";
- a running turn of a person who was removed or lost rights keeps only
  the strictest of its old rules and the new ones (the restricted floor
  for a removed person) for the rest of the turn. A turn typed in the
  terminal is not changed, unless a Telegram message joined it: aipager
  does not record whose, so such a turn is narrowed when any owner of
  that chat (or, in a DM, its member) loses rights. A turn that was already running
  when the daemon restarted (still working, or waiting on a question or
  permission) is narrowed for every member of its chat who lost
  rights. A promotion never widens a running turn: the new rights
  apply from that person's next turn.

Only a new **bot token** needs a restart (`aipager config` says so
after Refresh bot token). When no daemon is running, no reload is
sent: the next `aipager start` reads the new config.

To reload by hand (for example after editing `policy.yaml`), signal the
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
lock you out nor let anyone in: a reload never switches a daemon that
has chats configured back to personal mode (only a restart does).

### Bot admin rights and privacy mode

**Make the bot an admin of the group, with the right to pin
messages.** It needs that to pin the group's status bar (see
[What everyone sees](#what-everyone-sees)). Without it, the group
works but has no status bar.

A bot that is a group admin receives every message in the group,
whatever its BotFather privacy mode. That is safe: aipager acts only on
the messages listed in [what the bot reads](#what-the-bot-reads-in-a-group)
and ignores the rest, so group chatter never reaches a session. A bot
that is not an admin is in privacy mode by default (leave it on), and
Telegram then passes it only some group messages; if one of yours does
not arrive, reply to one of the bot's messages or use a command. The
privacy setting changes nothing about what aipager acts on.

Being a Telegram admin of the group gives a person nothing in aipager.
What someone may do comes only from their role in `aipager.yaml`.

### When Telegram upgrades the group

Telegram turns a basic group into a supergroup when you make it public,
turn on topics, pass 200 members, or change some admin rights, and the
upgrade gives the group a new chat id. aipager follows it by itself:
when it sees the upgrade (Telegram's notice in the group, or the first
post to the old id), it moves the group's scope in `aipager.yaml` to
the new id, moves the group's sessions, message targets and `/settings`
with it, and posts one line in the group: "This group was upgraded by
Telegram. aipager moved with it, nothing to do." The pinned status bar
starts again in the upgraded group. The daemon log has a warning with
both ids.

Session names inside aipager keep the old id (you never see them), and
`aipager.yaml` keeps a short `chat_migrations` record of the move, so a
session started before the upgrade still belongs to the upgraded group,
even after a restart. A session's internal name with the old id
(`/name__g<old id>`) works in the upgraded group and nowhere else. If
the daemon was stopped during the upgrade, it notices at its next
start. A turn that is running at the moment of the upgrade finishes and
its answer reaches the upgraded group, but its progress card is not
shown again for the rest of that turn. If the new id is already a scope
of its own, nothing moves and the log says so; remove one of the two
with `aipager config`. "Test bot reachability" in `aipager config`
shows the new id when it meets an upgraded group.

At every start aipager looks each group up once. If Telegram answers
that the group cannot be found or the bot may not use it (the group was
deleted, the bot was removed, or the group was upgraded while the
daemon was stopped and Telegram did not say to which id), the daemon log
has one warning naming the scope: check that the bot is still in the
group, and if it was upgraded, set the scope's new chat id with
`aipager config` (the old id no longer works). aipager posts nothing
and does not keep trying.

### Forum topics are not supported

Use one ordinary group (no topics) per team. In a group with topics
turned on, aipager still works, but it does not keep to a topic:

- a reply to a command or a message (for example `/status`, or the
  answer to "Which session?") stays in the topic it was sent in;
- notices, busy cards, answers, permission prompts and the pinned
  status bar are posted to the group without a topic, so they may land
  in General;
- each person has one current session for the whole group, not one
  per topic.

Turning on topics also upgrades a basic group to a supergroup, which
aipager follows (above).

## Roles

Four built-in roles (see `aipager/safety.py`). The role that counts is
the person's role **in the chat they act in**. Nobody can change
members or roles from Telegram or the Mini App: only `aipager config`
on the machine does that, which keeps it the owner's.

| | `owner` | `admin` | `user` | `read_only` |
|---|---|---|---|---|
| Send prompts (`can_prompt`) | ✅ | ✅ | ✅ | ❌ |
| Answer Claude: Allow, Deny, questions (`can_approve`) | ✅ | ✅ | ✅ | ❌ |
| Manage: Auto, a group's `/settings`, `/update`, voice install (`can_manage`) | ✅ | ✅ | ❌ | ❌ |
| Change members and roles from Telegram or the Mini App | ❌ | ❌ | ❌ | ❌ |
| Bypass role deny rules (`bypass_role_denies`) | ✅ | ✅ | ❌ | ❌ |
| Bypass the safety floor (`bypass_safety`) | ✅ | ❌ | ❌ | ❌ |
| Bash and other code-running tools | ✅ | ✅ (best-effort rules) | ❌ | ❌ |
| Any slash command | ✅ | ✅ | only the Commands keyboard's, `/compact` and model switches | same as `user` |
| Where file writes land | anywhere | anywhere the safety floor allows | the session's project folder and its scratchpad, never the home folder | same as `user` |
| Credential files (`~/.ssh`, `~/.git-credentials`, ...) | readable | readable | never (by default) | never (by default) |

- **owner**: full control, including the built-in safety floor. There
  should be exactly one: the person who runs the machine.
- **admin**: everything an owner can do in Telegram and the Mini App:
  switch sessions to Auto (new sessions start in Auto for them), resume
  a session in Auto, change a group's `/settings`, run `/update`,
  install the voice extra, send any slash command. An admin also
  bypasses role deny rules (scope and member `deny_tools` do not apply
  to them). Two things stay with the owner:
  - changing members and roles: only `aipager config` on the machine
    does that, and nothing in Telegram or the Mini App can;
  - bypassing the safety floor (aipager's config and bot token, Claude
    Code's credentials, aipager's control files).

  An admin keeps Bash, and with a shell the floor's command patterns
  are best-effort, not a boundary: only make someone an admin if you
  would give them a shell on the machine.
- **user**: deny rules apply. No Bash, and no other tool that runs code
  (PowerShell, Monitor, scheduled prompts, workflows; the list is
  `safety.CODE_EXECUTION_TOOLS`), no `SendMessage` to other sessions,
  no switching worktrees. File writes land only inside the session's
  folder and its Claude Code scratchpad, and never in the folder's
  `.claude/`, `.git/` or `.mcp.json` (each of those runs commands).
  `Grep` and `Glob` search only inside those folders, with plain
  relative globs. A session folder that is your home folder, `/` or a
  folder above your home folder does not count: there a `user` can
  write and search only in the scratchpad, so `/new` and the Mini App
  do not offer them those folders to start in (they can still make a
  new folder inside your home folder with New folder and start there).
  Reads follow the protected-path rules, and your credential files are
  never readable: `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config/gh`,
  `~/.git-credentials`, `~/.netrc`, `~/.docker/config.json`, `~/.kube`,
  `~/.config/gcloud`, `~/.azure`, `~/.password-store`, `~/.pgpass`,
  `~/.npmrc` and `~/.pypirc`. Slash commands: only the ones on the
  Commands keyboard, `/compact` and model switches (see
  [Who a message runs as](#who-a-message-runs-as)).
- **read_only**: observers. They see everything in the group, and can
  use `/status`, `/diff`, `/whoami`, `/help` and `/start`, look at
  `/settings`, and page through a `/resume` list someone opened. Their own text, voice and file
  messages are not sent anywhere. The first one they send in a chat gets
  one reply saying their role is `read_only`; after that the bot stays
  silent for them in that chat (no reply, no reaction) until the daemon
  restarts. They have the same tool and write limits as `user`, in case
  one of their messages ever reaches a session.

### Custom roles

`policy.yaml` can change any field of a built-in role, or define new
roles. A field you set replaces the built-in value. The fields:

- `can_prompt`, `can_approve`: send prompts; answer Claude.
- `can_manage`: use Auto, change a group's `/settings`, run `/update`
  and install the voice extra (built-in: `owner` and `admin` true,
  `user` and `read_only` false). It never lets anyone change members or
  roles, and never bypasses the safety floor.
- `bypass_role_denies`, `bypass_safety`: see the table.
- `deny_tools`, `allow_tools`, `deny_bash_patterns`,
  `deny_paths_no_access`, `deny_paths_no_write`: see
  [How rules work](#how-rules-work).

A role you define from nothing starts with `can_prompt` and
`can_approve` on, `can_manage` and both bypasses off, the credential
files in its `deny_paths_no_access`, and **no tools denied, so it has
Bash** until you set `deny_tools` (`aipager doctor` warns once a
member holds it).
Write and search confinement applies to every role without
`bypass_safety` or `bypass_role_denies`.

To give `user` back Bash (only for people you would give a shell), name
it in `allow_tools` (which then also becomes that role's allow-list) or
replace its `deny_tools`:

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
warning tells you. See
[security → team-mode enforcement](security.md#team-mode-enforcement)
for what these limits do not cover.

## Talking to the bot in a group

### What the bot reads in a group

In a group aipager acts only on a message that is:

- **a command**: `/status`, `/x1 fix the tests`, or the same with
  `@aipagerbot` after the command (`/x1@aipagerbot`), which Telegram
  adds when you pick it from the menu. A command for another bot
  (`/x1@otherbot`) is ignored;
- **a reply to one of the bot's messages**;
- **a message that mentions the bot**: `@aipagerbot fix the tests`;
- **a tap on the bot's keyboard** in the group. In a group every
  keyboard button starts with a small square (`▫️ stop`, `▫️ Clear`),
  and only that marked text counts as a tap. Typing the bare word is
  ordinary chatter: a member who types `stop`, `Clear`, `Opus` or a
  session's name changes nothing. To use a keyboard word on purpose
  without the keyboard, address the bot: `@aipagerbot stop`, or `stop`
  as a reply to one of its messages.

Anything else (members talking to each other, a photo or voice note
that is not a reply to the bot) gets no reaction and no reply. This
holds whether or not the bot is a group admin. Edited messages are
ignored everywhere: edit a message and nothing happens, send it again
instead.

A photo, file or voice note works the same way: send it as a reply to
the bot, or mention the bot in the caption. For an album, put the
caption on its first item: the album's items that arrive after the one
with the caption, mention or reply are taken, any before it are not.

**The mention is removed.** `@aipagerbot fix the tests` reaches Claude
as `fix the tests` (Claude Code would read `@word` as a file).
`@aipagerbot status` and `@aipagerbot x2` work like typing `status` or
`x2` in a DM, and `@aipagerbot` alone shows where your messages go. A
session command after the mention works (`@aipagerbot /x1 fix it`), but
one of aipager's own commands does not: `@aipagerbot /stop` gets "Send
/stop on its own, without mentioning the bot."

**Members only.** A person who is not a member of the group gets one
reply ("You're not on this scope's allow-list") with their Telegram id
to give you, and is noted for `aipager config`; after that the bot
ignores them until the daemon restarts. A `read_only` member is told
their role once (see [Roles](#roles)).

**Anonymous admins must post as themselves.** A group admin who has
"Remain anonymous" on, or a message posted as the group or as a
channel, does not say who wrote it: Telegram shows the same sender for
every anonymous admin. aipager cannot give such a message anyone's
role, so it answers "Post as yourself to use the bot." (once per chat
until the daemon restarts) and does nothing else. It is never noted as
a pending user, and the wizard refuses that shared id. Turn "Remain
anonymous" off for yourself in the group's admin settings, or post from
your own account.

### Talking to a session in a group

Reply to one of a session's messages (its answer, its card) and your
message goes to that session. Or send `/x1 your message`, or a caption
that starts with `/x1`.

**Each person has their own current session**: the one they last
switched to (`/x1`), sent a message to, replied to or started. Another
member's session answering does not change it, and neither do other
people's messages. A mention, a voice note, a template or a keyboard
command goes to your own current session, and a keyboard command or
template names it in its reply ("🧹 /clear sent to x1"). A bare `/stop`,
`/mode`, `/kill`, `/restart`, `/rename`, `/delete` or `/diff` acts only on
your own current session (through its usual confirm), and only when the
command makes sense for it (working for `/stop`, ended for `/delete`);
otherwise it always shows the picker, even when one session is left, since
that one may be someone else's. `/clearqueue` and `/now` act on your
current session. `/kill x1` and the other commands with a name act on that
session, as before.

- With no current session of your own and more than one session
  running, the bot asks "Which session? Reply to one of its messages,
  or send /x1 your message." with a button per session, and sends
  nothing. With only one session running, it goes there.
- If your own current session has ended, a message is not sent to the
  one session still running (it may be someone else's): the bot answers
  "x1 has ended. Which session?" with a button per running session and,
  when x1 can be resumed, "▶️ Resume x1", and sends nothing.
- Tapping a session's button on either question also sends the message
  it asked about (a text, a voice note's transcript or a file) to that
  session, as if you had sent it there, and the question changes to
  "Sent to x2.". Only the person who sent the message can use the
  buttons, and only for ten minutes: after that the button only makes
  the session your current one ("That message is too old to send; send
  it again."). ▶️ Resume brings x1 back but sends nothing: send your
  message again.
- The pinned bar has no "Messages go to" line in a group, since that
  differs per person.

**Cards that wait for an answer are per person.** The new-session card
says "Reply to this message with a name, and what to do first if you
like", the rename question "Reply to this message with the new name".
In a group each one shows whose it is ("🆕 New session (@alice)"),
only that person can answer it, and two people can start or rename
sessions at the same time. Another member's reply to someone's
new-session card gets "This card is @alice's. Send /new for your own."
and goes nowhere. A rename question stops waiting after 10 minutes, or
as soon as its person does something else. The Ready card, the reply to
`/x1`, `/start` and `/help` say to reply to a session's message or
mention the bot ("Reply to a message from x1 (or mention @aipagerbot)
to talk to it."), since a plain message does not reach the bot.

**The keyboard is per person.** Templates, Commands, Model › and « Back
change only the keyboard of the person who taps them: the bot answers
as a reply to that person's tap, and nobody else's keyboard moves.
« Back goes up from where you are, whatever other members are browsing.
The main keyboard sent by `/start` is the whole group's, and puts
everyone back on the main keyboard. After upgrading from a version
without the marked buttons, a tap on a button of the old keyboard may
be ignored: send `/start` once to give the group the new keyboard. A
private chat's keyboard has no marker and works as before.

### The Mini App

`/app` opens the Mini App only in a private chat. In a group it is not
in the `/` menu; typed there, it tells a member whose own chat with the
bot is set up "📱 The Mini App only works in a private chat - DM the
bot and send /app there.", and anyone else "The Mini App opens from
your own chat with the bot. Ask the operator to add you."

The Mini App shows one chat at a time. Someone who belongs to more than
one chat (for example their own DM and a group) gets a row of chat
names at the top to switch between them; it opens on their own DM,
else on the first chat that lists them, and remembers the last choice
on that phone. Everything in it (the sessions, their buttons, the
settings and the notices it posts) is for the chosen chat, with the
role the person has in that chat. With one chat nothing extra is shown.

## Who a message runs as

Every message runs with **the rights of the person who sent it**, in
the chat they sent it from. Someone who is `user` in the group and
`owner` of their own DM sends, taps and runs commands in the group as
`user`, whatever order the chats are in `aipager.yaml`.

- Every Telegram message carries its sender's identity and rules into
  the session (see
  [security → team-mode enforcement](security.md#team-mode-enforcement)).
  Claude sees who sent each message (`[via Telegram · @bob ·
  role:user]` in a group), never the person who drove the session
  before them. Whoever sends a message becomes the session's driver,
  whether they typed it, used `/x1`, a template or a keyboard command.
- **Held messages keep their sender.** While another person's turn is
  running (including a background agent it started), or while someone
  else's message is still waiting to be picked up, or while a
  permission prompt or question is open, a new message is held (👀) and
  sent when that clears, as its own turn, **with its own sender's
  rights**. A turn typed in the terminal counts as the owner's: only an
  owner's message (or, in a DM, its own member's) joins it. Your own
  messages never wait for your own turn. If the turn they wait for is
  stopped, or halted because a tool was blocked, held messages are
  dropped (🤷): send them again.
- **Strictest wins.** If messages from different people do end up in
  one turn, the turn runs under the most restrictive combination of
  everyone in it. A message that joins a running turn never widens it:
  the rest of the turn keeps the strictest rules of everyone who joined.
- **Slash commands from restricted roles.** A role that does not bypass
  role rules (`user`, `read_only`, roles you define without
  `bypass_role_denies`, and a sender aipager does not know) may send
  only the slash commands on the Commands keyboard (yours, if
  `keyboard.json` changes it), `/compact` and model switches
  (`/model <name>`). Anything else, typed, sent with `/x1 /deliver
  ...`, as a file caption, as `/new x1 /review ...`'s first message or
  as a template, gets "That command needs an admin." and a 🤷, and
  nothing reaches Claude. Owners and admins send any.
- **Restricted roles stay out of the home folder and off credential
  files.** A `user` or `read_only` turn never writes in a session
  folder that is your home folder, `/` or above it (only in its
  scratchpad), and never reads the credential files listed under
  [Roles](#roles).
- **A removed member loses their rights at once, not their running
  turn.** A sender aipager no longer knows (removed while their message
  waited) runs on the restricted floor. After a live reload their held
  messages are dropped, and a turn of theirs that is still running keeps
  running, held to the restricted floor until it ends; send `/stop` to
  end it now (see [Live reload](#live-reload)).
- **A blocked tool stops the turn.** When a turn tries a tool its rules
  deny, the turn stops at once with "🛑 x1 · Blocked by safety policy:
  ... (stopped)", and every later tool call in that turn is denied too.

## Who can tap what

Every button checks the role of the person who taps it, in the chat the
button is in, at the moment they tap it. The role of whoever sent the
command that made the button does not count. (Some cards also check who
asked for them: confirm cards and the `/new` cards, below.)

- **Looking is open to every member** (VIEW). `read_only` members can
  page through `/resume`, open `/status`'s Ended view, a session's ⋮
  menu, its mode or its diff, browse `/settings`, and close a menu.
  They cannot act: any other button answers "Your role can't do that
  here."
- **Answering Claude needs `can_approve`** (APPROVE): Allow, Allow
  always, Deny, a question's options, Submit, Continue and the pinned
  bar's Answer. `read_only` has it off.
- **Acting on a session needs `can_prompt`** (PROMPT): Stop, Retry,
  Compact, Send now, End, Restart, Rename, Delete, Resume, the mode
  switch, ✍️ talk, Clear all, a session's own preferences and the
  `/new` cards. A button aipager does not know needs this too.
- **Switching to Auto needs an admin** (MANAGE: `owner`, `admin`, or a
  role with `can_manage`, the same rule as `/mode auto`): Yes, switch
  and Stop task & switch on a card that switches to Auto, and Resume as
  Auto. Resuming a session that was in Auto (typed `/resume x1`, the
  picker, Resume on the `/new` name card, or the Mini App) brings it
  back in Ask for anyone else, and the reply says so: "Resumed x1 in
  Ask: Auto needs an admin."
- **Installing needs an admin** (UPDATE): the voice extra's Install
  and Restart buttons, like `/update`'s.
- **Changing a group's `/settings` needs an admin.** Anyone can look.
- **A confirm card belongs to whoever asked for it.** In a group, the
  End (`/kill`, ⋮ End session), Restart, Delete and mode-switch confirm
  cards, the session pickers `/kill`, `/restart`, `/delete`, `/mode`,
  `/stop`, `/rename` and `/diff` show, and the "Resume x1 as:" card a
  Resume tap draws (its Ask and Auto buttons) answer only the person who
  sent the command or tapped the button that drew them. Anyone else's
  tap, Cancel included, gets "This is @alice's card. Send /kill x2 for
  your own." (with that card's command) and changes nothing. The `/mode`
  card itself (which mode a session is in) is everyone's: a switch
  tapped on it draws a confirm that belongs to the tapper. The `/new`
  cards answer only the person who sent `/new`. A card's owner is
  remembered until it is used or cancelled, also across a daemon
  restart (the newest 4096 cards). The `/new` name card and the
  `/rename` name question are the exception: they expire after 10
  minutes anyway, so after a restart send the command again.
- **A button only works in its own session's chat.** One tapped
  anywhere else answers "This button belongs to another chat." and does
  nothing, and Resume always resumes the session the button names,
  never another chat's session with the same name.
- **Old buttons change nothing.** A button left over from an earlier
  task, or from before the session ended and came back, does not act on
  what is running now; it says so (see
  [commands → stale buttons](commands.md#stale-buttons)).

## What everyone sees

**Answers to Claude.** Every Allow, Deny and answer is posted in the
chat, under the session's card, with who did it:

```
✅ x1 · Allowed by @alice · Bash: ls -la /tmp
🚫 x1 · Denied by @bob · WebFetch: https://example.com
✓ x1 · Answered by @carol · Which database? → Postgres
```

**Who changed a session.** A tap on a button is seen only by the
person who taps it, so in a group every result that changes a session
names who did it:

```
⚠️ x1 · Stopped by @bob
⏹ Ended x2 by @bob
🔄 [x1] restarted by @alice.
🗑️ Deleted [x3] by @carol.
⚙️ Switched to 🤖 Auto by @alice.
Changed by @alice.          (under /settings)
```

The Ready card names who switched its mode or model ("Model switched
to Opus by @bob."). In a private chat these result lines name no one.
A change made in the Mini App is posted in the chat as "...
from the Mini App" (a Stop from it also names who on the session's
card).

**A tool blocked by a rule.** A turn stops with "🛑 x1 · Blocked by
safety policy: Write is in this role's deny_tools (stopped)". If Claude
Code asks for permission for a tool the person whose message started
the running turn may not use, the bot answers Deny itself, shows no
prompt, and posts `⛔ x1 · Edit blocked for @bob (role user)` with the
call's summary below, naming that person. A turn several people's
messages started is blocked when any one of them may not use the tool,
and names that one. A turn typed in the terminal is yours and is never
blocked by a member's rules. When aipager does not know whose turn it
is (it was already running when the daemon restarted), it uses the
rules of whoever sent the session's last message, and the group's own
list when that is not known either.

**Message states.** 👀 on a message means it was sent to the session
(or is held until it can be); 👍 means Claude took it; 🤷 means it was
dropped before Claude took it. `/whoami` shows your label, role and
rules in this chat.

**The pinned status bar.** The group gets its own pinned status bar
(see [commands → the pinned status bar](commands.md#the-pinned-status-bar))
listing only this group's sessions, if the bot is an admin allowed to
pin. Without that right the bar is skipped for the group: the message
is deleted rather than left unpinned in the history.

**On-disk audit.** `~/.claude/aipager-audit.jsonl` records every
decision with `user_id`, `username` and the chat, so you can
reconstruct later what each person did.

## Where messages go

- **Every session belongs to one chat, and everything about it goes
  there**: its cards, answers, permission prompts, files and notices.
  A message in one chat never goes to a session of another chat, and a
  session's name only works in its own chat: typing another chat's
  session name answers "Unknown session". A session started while
  aipager ran with no chats set up gets its chat as soon as
  `aipager config` adds them; until a session has a chat, no chat
  lists it or can act on it.
- **Sessions started in the terminal** (`aipager session <name>`)
  belong to the owner's DM: the DM whose member has the `owner` role (a
  role with `bypass_safety`). With several DMs and none of them the
  owner's, it is the first DM listed; with no DM at all, the first chat.
  They never go to a group on an install that has a DM.
- **A supergroup upgrade is followed automatically** (see
  [When Telegram upgrades the group](#when-telegram-upgrades-the-group)).

## `aipager.yaml` schema (team parts)

```yaml
schema_version: 3
bot_token: "…"

scopes:
  - kind: dm
    chat_id: 12345       # your own DM: its id is your user id
    label: owner DM
    members:
      - id: 12345
        label: alice
        role: owner
  - kind: group
    chat_id: -100123456789
    label: team
    deny_tools: [Write, Edit]   # optional: the "Block file edits" answer
    members:
      - id: 12345        # Telegram user ID (NOT a label, NOT a chat ID)
        label: alice     # how the person is shown in chat (@alice)
        role: owner
      - id: 67890
        label: bob
        role: user
        deny_tools: [WebFetch]  # optional, per member
      - id: 11111
        label: charlie
        role: read_only
```

A DM scope and group scopes coexist: the bot serves every chat listed,
with each chat's sessions kept apart. `aipager config` writes this
file; you never need to edit it.

## How rules work

`~/.config/aipager/policy.yaml` defines per-role rules. Validate it
with `aipager policy validate`. Supported fields per role (besides the
ones in [Custom roles](#custom-roles)):

- `deny_tools`: tool names denied without prompting (e.g. `Write`,
  `Edit`, `Bash`, `WebFetch`).
- `allow_tools`: if non-empty, an allow-list: everything else is
  denied for that role.
- `deny_bash_patterns`: patterns matched against `Bash` inputs.
- `deny_paths_no_access` / `deny_paths_no_write`: path rules. For
  `user`, `read_only` and every role you define without
  `bypass_role_denies`, `deny_paths_no_access` starts as the credential
  files listed under **user** above; setting it replaces that list, so
  copy the ones you want to keep into yours (a running turn that
  another message joins is held to the list again until it ends). A
  rule with no leading `/` or `~` (`**/.env`) matches anywhere, so while
  one is in place every `Grep`/`Glob` of a restricted role is denied
  (any search could read such a file), which halts that turn.

A chat's `deny_tools` and a member's `deny_tools` in `aipager.yaml` add
to the role's. All of these apply only to roles without
`bypass_role_denies`: an owner or admin is not affected by them.

Underneath all roles sits a built-in **safety floor** (protected paths
and command patterns) that only `owner` bypasses. The protected paths
hold for Claude Code's file tools (`Read`, `Write`, `Edit`,
`MultiEdit`, `NotebookEdit`, `LSP`, `Grep`, `Glob`); a restricted
role's `Grep`/`Glob` must also stay inside the session's folder (a
search of `~/.config` for `aipager/**` is denied). Tools such as
`WebFetch` or `SendFile` are not path-checked. A Telegram turn aipager
cannot attribute to a sender gets the floor, which is at least as
strict as `user`. Retry runs as whoever tapped it if they sent the
prompt being retried, and on the floor otherwise. The command patterns
only matter for a role that has Bash, and there they are a filter, not
a wall; see
[security → team-mode enforcement](security.md#team-mode-enforcement).

## Privacy considerations

- The bot only listens to **the configured chats**. Adding the bot to
  another group doesn't activate it there: it never answers there. It
  does note who addresses it there (their id, username and name, and
  the group's id and title) in `~/.claude/aipager-pending-users.json`,
  so `aipager config` can add that group. Only you can read that file,
  and it keeps the newest 200 people seen in the last 30 days.
- `read_only` members **can read** prompts, answers and tool inputs.
  They can't act, but they see everything in the group. If some work
  must stay hidden from someone, they don't belong in the group.
- On an install that has a DM, your terminal sessions and your DM
  sessions never show in a group (see
  [Where messages go](#where-messages-go)). A terminal session that an
  older aipager already moved to the group stays the group's: end it,
  delete it from the group's ended sessions, and start it again.
- `aipager.yaml` and `policy.yaml` are mode 0600 (owner-only).
- The audit log is owner-only (`~/.claude/aipager-audit.jsonl`).

## Revoking a user

1. Run `aipager config` → Edit a member → the chat → them → Remove
   member. To take away their right to prompt but keep them reading,
   use Set role and pick `read_only` instead. (To add someone back
   later: Edit a scope → the group → Add a member.) For a person's own
   DM, end its sessions, then Edit a scope → their DM → Remove this
   scope.
2. The wizard reloads the daemon live; it says "Scopes reloaded live".
   At once, their held messages are dropped (🤷 and a "Dropped" reply)
   and a turn of theirs that is still running is held to the restricted
   floor until it ends. Send `/stop` to end that turn now.
3. Remove them from the Telegram group too. While they stay in it they
   can still read everything posted there: cards, answers, tool inputs
   and permission prompts.

Check what the wizard says after step 1. If it says to restart the
daemon, it could not reach it: restart it (`aipager service restart`),
since until then the previous member list is still in memory. If it
says "Not applied", fix the problem it names: the daemon keeps the
previous config until then. After a hand-edit of `aipager.yaml`, send
the reload yourself (see [Live reload](#live-reload)).

## Related docs

- [Architecture](architecture.md): process model.
- [Bot commands](commands.md): interface reference.
- [Security model](security.md): trust boundary, threat list.
- [Troubleshooting](troubleshooting.md): `aipager doctor` reference.
