# Install with an agent

This page is for an AI coding agent (Claude Code, or any agent with a
shell) that is asked to install aipager for its person and set it up.
Follow it step by step. Every command here runs without prompts and
never needs a terminal.

aipager lets the person drive Claude Code sessions from Telegram. When
you are done, the person has a Telegram bot that talks only to them,
and aipager running in the background on this machine.

## 1. What only the person can do

Ask the person to do these. You cannot do them for them.

1. **Create the bot.** In Telegram, open [@BotFather](https://t.me/BotFather),
   send `/newbot`, pick a name and a username. BotFather answers with
   the bot token (it looks like `123456789:AA...`).
2. **Press Start in the bot.** Open the new bot (BotFather links it as
   `t.me/<bot username>`) and press Start. A bot cannot write to someone
   who has not done this.
3. **Log in to Claude Code on this machine.** If they already use
   Claude Code here, nothing to do. On a headless server, they run
   `claude` once in their own terminal and log in (it shows a link to
   open on another device). If they prefer a long-lived token instead,
   they run `claude setup-token`, which only prints the token: they save
   it for aipager's sessions themselves, in their own terminal (paste
   the token, press Enter, then Ctrl-D):

   ```sh
   mkdir -p ~/.config/aipager
   (umask 077 && { printf 'CLAUDE_CODE_OAUTH_TOKEN='; cat; } >> ~/.config/aipager/daemon.env)
   ```

   aipager hands that token to every session it starts (see
   [security](security.md#the-claude-credential-what-actually-protects-it)). You never read it.

## 2. How the person gives you the token

Anyone who has the bot token controls the bot. Offer the person these
two ways and let them choose.

- **Safest: a file you never read.** In their own terminal, they run:

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token)
  chmod 600 ~/.config/aipager/bot-token
  ```

  then paste the token, press Enter, and press Ctrl-D. Only they can
  read the file (mode 600). They may delete it once setup succeeds
  (aipager keeps its own copy in its config). They tell you the
  path (`~/.config/aipager/bot-token`). The token never enters your chat,
  and you pass the path to `--token-file`.
- **Fastest: paste it into the chat.** This works, but say plainly
  what it means: the token stays in your transcript and is sent to
  your model provider. If the transcript may ever be shared, the person
  should later send `/revoke` to @BotFather and set the new token with
  `aipager setup --force` (step 3.2, with `--force`). If they choose
  this, save the token to the same file without printing it: best with
  your own file-writing tool, then `chmod 600 ~/.config/aipager/bot-token`.
  If you only have a shell, feed it on stdin with a here-document, so
  it is not an argument of the command that writes it:

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token) <<'EOF'
  <the token>
  EOF
  chmod 600 ~/.config/aipager/bot-token
  ```

  Then use `--token-file` as below.

The person's Telegram user id (their chat id, a number like
`123456789`) is not secret. If they do not know it, you can find it
(step 3.3).

## 3. Your steps

### 3.1 Install aipager

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

It uses uv or pipx, whichever is there (Homebrew too, on macOS), and
installs uv first if none is. You can also run `uv tool install aipager` or
`pipx install aipager` yourself. If `aipager` is then not found, add
`~/.local/bin` to `PATH` for your shell (`export PATH="$HOME/.local/bin:$PATH"`).

If aipager was already installed, these do not upgrade it. Check that
`aipager setup --help` works; if it fails, the installed aipager is
too old: run `aipager update` and check again. If `aipager update`
says to restart aipager, tell the person; do not restart it yourself
(see 4).

Do not run `aipager config`: it is the interactive wizard for people.

### 3.2 Set aipager up

```sh
aipager setup --token-file ~/.config/aipager/bot-token --chat-id 123456789 --service --json
```

Use the person's real id and the token file's path. Instead of
`--token-file`, you can pipe the token in with `--token-stdin` (for
example `--token-stdin < path`); the pipe must close, and setup waits
for it at most 30 seconds.

Setup checks the token with Telegram, checks this machine, sends the
person one test message ("aipager is set up for you. ..."), and only
then writes its config, adds the Claude Code hooks to
`~/.claude/settings.json` (or `$CLAUDE_CONFIG_DIR/settings.json`), and, with `--service`, installs and starts
the background service. If any check fails, nothing is written.

Add `--dry-run` to see what would change first. `--role admin` makes
the person an admin instead of the owner (the default, owner, is
right for a person setting up their own machine).

### 3.3 If the person does not know their Telegram id

```sh
aipager setup detect-chat --token-file ~/.config/aipager/bot-token --json
```

Ask the person to press Start in the bot (or send it any message)
while this waits (up to 5 minutes; `--timeout SECONDS` changes it). It
prints the newest sender in `candidate` (`id`, `first_name`,
`last_name`, `username`) and saves nothing. Show the name and username
to the person and ask "Is this you?". Only when they say yes, run step
3.2 with `--chat-id` set to `candidate.id`. If `other_candidates` is
not 0, other people wrote to the bot too: make extra sure.

### 3.4 Read the result

With `--json`, setup prints exactly one JSON object on stdout. Read
`exit_code` (the same as the process exit code), `status`, `error`,
`message`, `fix` and `next_step`. If stdout is not JSON (an unknown
flag, a wrong word after `setup`, or an aipager too old to have
`setup`), the exit code is 2 and the reason is in plain text on
stderr: read it, and see 3.1 for an old aipager.

| Exit | `error` | What you do |
|---|---|---|
| 0 | | `installed`, `updated` or `unchanged`: done, go to 3.5. `dry_run`: nothing was written; run the same command again without `--dry-run`. detect-chat `found`: confirm the person as in 3.3, then run 3.2. |
| 1 | `config_malformed`, `ambiguous_install`, `settings_invalid`, `test_send_failed`, `write_failed`, `service_failed`, `internal_error` | Show `message` and `fix` to the person. `ambiguous_install`: the person fixes it in `aipager config`. `settings_invalid` that asks for an absolute `CLAUDE_CONFIG_DIR`: the fix names the file to change (`daemon.env` or a Claude Code settings file); ask the person for the folder, change that one value only if they say so, then run setup again. Do not edit any other file by hand. |
| 2 | `usage`, `token_malformed`, `token_stdin_timeout`, `bad_chat_id`, ... | Your input was wrong; `fix` names the flag. `token_malformed`: the file does not hold a bot token, ask the person to check it. |
| 3 | `deps_missing` | Follow `fix` (each missing item also has its own `fix` in `deps.items`), then run setup again. |
| 4 | `token_rejected` | Telegram refused the token. Ask the person for the token again (or a new one from @BotFather). |
| 5 | `chat_not_started`, `bot_blocked` | Ask the person to open the bot, press Start (or unblock it), then run setup again. |
| 6 | `existing_install` | aipager is already set up with a different token, chat or role. Tell the person what differs and ask before you add `--force`. |
| 7 | `telegram_unreachable` | Network trouble or Telegram is busy. Wait a minute and try again. |
| 8 | `detect_timeout` | detect-chat saw no message. Ask the person to press Start again and rerun. With the warning `updates_backlog`, pressing Start does not help: ask the person for their numeric id. |
| 9 | `daemon_running` | detect-chat: an aipager for this bot already runs and holds its messages. Ask the person for their numeric id (or ask them before you stop it with `aipager service stop`). |
| 10 | `updates_conflict` | detect-chat: another program reads this bot's messages. Ask the person for their numeric id. |
| 130 | `interrupted` | Stopped with Ctrl-C. |

If `daemon.restart_needed` is true, aipager needs a restart to use the
change, or setup could not tell whether it is running (the warning
`daemon_unknown`). Follow `next_step`, which says which (if it offers
`aipager start`, which stays in the foreground, prefer `aipager
service install`). Tell the person, and restart (`aipager service
stop`, then `aipager service start`) only if they say so, and never
from inside a session the person drives through aipager (see 4). The
full list of errors, warnings and JSON keys is in [the setup command reference](commands.md#command-line-aipager-setup-for-coding-agents).

### 3.5 Check the install

```sh
aipager doctor --json
```

It only reads and sends nothing. Exit 0 means no check failed. For any
check whose `status` is `fail` (or `warn`), show its `title`, `detail`
and `fix` to the person.

### 3.6 Report back to the person

Tell them, in a few lines:

- the bot (`@` + `bot_username`), and whether the test message was
  sent (`test_message`);
- what was set up (`changed`, and `service.result`);
- how to start a Claude session: send `/new` to the bot in Telegram,
  or run `aipager session <name>` in a terminal;
- anything `doctor` flagged, and `next_step` if it asks for something.

## 4. What you must not do

- Do not print, echo or `cat` the token, and never put it on a command
  line (`--token` is refused anyway). Pass it only as a file or on
  stdin.
- Do not edit `~/.config/aipager/` (apart from the token file in step
  2) or Claude Code's `settings.json` by hand. `aipager setup` and
  `aipager config` write them. The one exception is a group on an
  aipager older than this one, where `aipager setup group --help`
  fails: see [5.5](#55-older-installs).
- Do not add `--force` without asking the person: it replaces their
  existing token, chat or role, and with `setup group` it changes or
  removes roles or grants the owner role.
- Through aipager, only the owner's turns can run `aipager setup` (any
  form), `aipager config` or `aipager uninstall`: aipager's safety
  rules block them for every other role. If the person reaches you
  through aipager without being the owner, ask them to run the command
  in a terminal themselves.
- Do not restart or kill your own Claude Code session. On a first
  install, the session you are running in is not under aipager, and
  does not need to be: new sessions come from `/new` in Telegram or
  `aipager session <name>`. If aipager is already set up and the
  person reaches you through it (for example to run `setup --force`
  with a new token), do not stop or restart aipager yourself: your
  turn and its answer would be cut off. Tell the person to restart it
  when you are done.

## 5. Groups

`aipager setup` sets up the person's own DM with the bot. `aipager
setup group` adds a Telegram group, its members and their roles to an
install that is already set up, with no questions (`aipager config`
does the same with questions). It reads the bot token from the
install, never restarts the daemon and sends nothing to the group. See
[Team / group mode](groups.md) and
[the command's reference](commands.md#aipager-setup-group).

### 5.1 What only the person can do

You cannot do these steps; ask the person to do them in Telegram:

1. Add the bot to the group.
2. Make the bot an admin of the group with the right to pin messages
   (aipager pins a status bar there; without that right the group
   works, with no status bar). Only a group admin can do this.
3. Send `/start` in the group, so you can find its id (5.2).
4. Give you each member's numeric Telegram user id, a short label (how
   aipager shows them, as `@label`) and a role: `owner`, `admin`,
   `user`, `read_only`, or a role of their own from `policy.yaml`.
   The owner role has full control of this machine: it belongs to the
   person who runs it and to nobody else. If they do not know an id,
   `aipager config` can find it while that person writes in the group.

### 5.2 Find the group

```sh
aipager setup detect-group --json
```

It waits (up to 300 seconds, `--timeout SECONDS`) for a message in a
group that is not set up yet, sent at most 10 minutes before you ran
it, and prints the group (`candidate.id`, `candidate.title`). It saves
nothing. Show the title to the person and use the id only once they
confirm it is the right group. Exit 8 means nothing arrived: ask them
to send `/start` in the group again.

### 5.3 Set it up

Run it with `--dry-run` first, then the same command without it:

```sh
aipager setup group --chat-id -1001234567890 \
  --member 111111111:alice:owner --member 222222222:bob:user \
  --label team --dry-run --json
```

- `--member ID:LABEL:ROLE` once per person. To add someone later, run
  it again with only the new `--member`: the others are kept.
- `--remove-member ID` removes one person.
- `--label NAME` names the group, `--root PATH` sets the folder where
  its `user` and `read_only` members work (default
  `~/aipager-users/group-<label>`), and `--block-edits` blocks Write
  and Edit for its `user` members.
- Running the same command again changes nothing (`unchanged`).
- Exit 6 (`needs_force`) means the change needs `--force`:
  `force_reasons` says why (a role change, an owner granted, demoted
  or removed, a risky root folder). Show it to the person and add
  `--force` only if they agree.

### 5.4 Read the result

| Exit | What to do |
|---|---|
| 0 | Done (`created`, `updated`, `unchanged` or `dry_run`). Tell the person the group's `label`, its `members` and what `daemon.outcome` says. |
| 2 | A flag is wrong: `error`, `message` and `fix` name it. Fix it and run again. |
| 6 | `needs_force`: ask the person about each `force_reasons` entry. |
| 11 | `not_set_up`: set aipager up first ([3](#3-your-steps)). |
| 1 | Anything else (`config_malformed`, `policy_malformed`, `last_member`, `config_changed`...): show `message` and `fix`. After `config_changed`, run it again. |

`daemon.outcome` says what the running daemon did: `reloaded_live`
(the group works now), `no_daemon` (it works once aipager starts),
`restart_needed` or `not_applied` (show `next_step` to the person; do
not restart aipager yourself). Warnings never change the exit code,
but show them: `bot_not_in_group`, `bot_not_admin`, `bot_cannot_pin`
and `group_not_found` mean the person has a step of 5.1 left, and
`owner_granted` is worth saying out loud.

### 5.5 Older installs

If `aipager setup group --help` fails, this aipager is older than the
command. Ask the person to update aipager (`aipager update`), or, if
they want the group now, add it by hand:

1. Add a group scope to `~/.config/aipager/aipager.yaml`, following
   [the schema](groups.md#aipageryaml-schema-team-parts). The file
   holds the bot token: never print it or `cat` it, and keep it mode
   0600 (`chmod 600 ~/.config/aipager/aipager.yaml`).
2. Check it with `aipager policy validate`, then check that the
   `aipager doctor` line `scope mode: ...` counts the new group.
3. Send the daemon a live reload. On Linux, under the background
   service:

   ```sh
   pid=$(systemctl --user show -p MainPID --value aipager.service)
   [ "${pid:-0}" -gt 0 ] && kill -USR1 "$pid"
   ```

   On macOS:

   ```sh
   pid=$(launchctl list | awk '$3 == "com.aipager.daemon" {print $1}')
   [ "${pid:-0}" -gt 0 ] 2>/dev/null && kill -USR1 "$pid"
   ```

   The `-gt 0` check matters: with no daemon, `kill -USR1 0` would
   signal your own shell's processes. Never restart the daemon from
   inside an aipager session: your turn would be cut off.

## See also

- [Bot commands and the setup command](commands.md)
- [Troubleshooting](troubleshooting.md)
- [Security model](security.md)
