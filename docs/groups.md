# Team / group mode

aipager runs by default as a 1:1 DM bot — you and the bot, no one
else. **Team mode** opens it up to a Telegram group with multiple
developers, the way teams use `@gitbot` or `@deploybot`: anyone in
the group can mention `@aipagerbot` to inject prompts, approve
permission requests, or check status.

Team mode is opt-in. Personal-mode installs are unaffected.

## Decide carefully

Adding a Telegram user to the team gives them **code-execution
rights on the host running the daemon**. They can:

- Inject prompts that claude turns into shell commands, file
  edits, network calls.
- Approve / deny tool calls — your `~/.claude/settings.json` still
  decides which tools claude asks about, but anyone with `admin`
  or `developer` role can hit Allow.
- Create / kill / switch sessions.

Treat the allow-list the same way you treat SSH access to the
machine. The audit log
([`~/.claude/aipager-audit.jsonl`](security.md#audit-log)) records
who did what so you can review later, but it's after-the-fact.

## Setup

Run `aipager config`. The wizard adapts:

- **No config yet** → first-run wizard. Picks mode upfront, then
  walks token → mode → chat (group or DM) → team users + rules (if
  team) → deps → settings → write.
- **Config exists** → edit menu. Opens a current-state panel and
  offers focused actions: add a user, remove a user, change a
  user's role, edit deny rules, switch mode, refresh the bot token,
  or run the full setup again.

The first-run team flow:

1. After token verification, pick **Team** at the mode prompt.
2. Wizard shows a hard-stop warning panel about the trust expansion
   (allow-listed users can run shell commands on the host) and
   asks you to confirm.
3. **Group chat ID** — paste it manually, or pick "Auto-detect"
   and let the wizard watch `getUpdates` for a `/start` in the
   group. Add the bot first.
4. Walks you through adding **users** — label, Telegram user ID
   (with manual-paste or auto-detect-via-mention), role.
5. Optionally enables a default **deny rule** (`Write` + `Edit`),
   which the next section explains.
6. Writes `~/.config/aipager/team.yaml` (mode 0600).

Edit operations (when a config already exists):

- **Add a user** — same prompts as first-run, validates against the
  current list so you can't dup a label or user id.
- **Remove a user** — picker over the current list. Refuses to
  remove the last admin (promote someone first).
- **Change a user's role** — picker → new role. Same single-admin
  guard applies.
- **Edit deny_tools rules** — checkbox over the common Claude
  tools (`Bash`, `Write`, `Edit`, `WebFetch`, `Read`, `Glob`,
  `Grep`, `Task`) with current selections pre-checked, plus a
  free-form "other tools" line for custom names.
- **Switch to Personal mode** — archives `team.yaml` to
  `team.yaml.bak.<unix-ts>` and offers to re-collect a DM chat id
  for `config.env`.
- **Switch to Team mode** (when currently personal) — reuses the
  token, walks the team setup, and updates `config.env`'s
  `CHAT_ID` to the group id.
- **Refresh bot token** — re-prompt for a new token after
  `/revoke` in `@BotFather`.

### Live reload

The wizard signals the running daemon via **SIGUSR1** after every
team.yaml change, so add-user / remove-user / change-role / edit-
rules / switch-to-personal apply **without** a daemon restart.
You'll see:

```
✓ Team config reloaded live (no daemon restart needed)
```

Restart is still required when changes affect:

- **Bot token** (Refresh bot token)
- **`CLAUDE_TG_CHAT_ID`** (Switch to Team writes a new group id)

The wizard distinguishes between hot-reloadable and restart-needed
changes and prints the appropriate hint.

To trigger a reload manually (e.g. after a hand-edit):

```sh
kill -USR1 $(pgrep -f 'aipager start')
```

If `team.yaml` is malformed at reload time, the daemon logs a
WARN and keeps the previous in-memory team — so you can't lock
yourself out by typo'ing a hand-edit.

### Auto-detect Telegram user IDs

When adding a user via the wizard you can pick **Auto-detect**
instead of pasting a numeric id. The wizard polls
`getUpdates` for the next message and captures the sender's id +
Telegram username, then suggests the username as the default
label. Removes the "ask your teammate to dig out their user id"
step.

You can still hand-edit `team.yaml` directly — the wizard just
gives you a cleaner UX.

Also, on `@BotFather`, leave **privacy mode ON** (the default).
That way the bot only sees messages that mention it or reply to
its messages — not every chat in the group.

## Roles

| Role | Send prompts | Approve | Bypass `deny_tools` | Use `/status` |
|---|---|---|---|---|
| `admin` | ✅ | ✅ | ✅ | ✅ |
| `developer` | ✅ | ✅ | ❌ | ✅ |
| `read_only` | ❌ | ❌ | ❌ | ✅ |

- **admin** — full control. Bypasses `deny_tools` rules so they can
  manually approve restricted tool calls when needed.
- **developer** — full control except `deny_tools` rules apply.
  Their Allow tap on a denied tool gets auto-rejected.
- **read_only** — observers. They see every message, can call
  `/status`, but their text / voice / file messages are ignored.
  Useful for stakeholders who want visibility without action.

## `team.yaml` schema

```yaml
mode: team
group_id: -100123456789

users:
  - id: 12345          # Telegram user ID (NOT a label, NOT a chat ID)
    label: alice       # how the user is referenced in chat (@alice)
    role: admin
  - id: 67890
    label: bob
    role: developer
  - id: 11111
    label: charlie
    role: read_only

rules:
  deny_tools:          # auto-deny these tool calls without prompting
    - Write
    - Edit
```

Missing sections take safe defaults: no `rules` block means no
auto-deny. An empty `users` list rejects every message and the
daemon refuses to start.

## How rules work

The single rule type in v1 is **`deny_tools`** — a list of Claude
Code tool names (e.g. `Write`, `Edit`, `Bash`, `WebFetch`). When
claude asks for permission to use a denied tool **and** the
session's last driver isn't an admin:

- The bot **does not show the permission prompt**.
- It writes a deny back to claude (via the same key-injection path
  the `[❌ Deny]` button uses).
- It posts a one-line notice in the chat:
  `⛔ [jim] · Auto-denied · Write · per rules.deny_tools (triggered by @bob)`
- It writes an audit record with `action: "Auto-denied"`.

Admins can manually approve denied tools because they bypass rules —
if alice is an admin and asks claude to write a file, claude's
normal prompt still appears for her tap. If bob (developer) asks
the same thing, it's auto-denied.

Future rule types are out of scope for v1 but the schema is
forward-compatible. Likely additions:

- `require_n_approvers: 2` — 2-of-N for tool approval
- `deny_bash_patterns: ["rm -rf"]` — pattern-match `Bash` inputs
- `business_hours_only: true` — no deploys overnight

## What everyone sees

**Permission audits.** Every Allow / Deny / Continue is replied to
in chat:

```
✅ [jim] · Allowed by @alice · Bash: ls -la /tmp
🚫 [jim] · Denied by @bob · WebFetch: https://example.com
⛔ [jim] · Auto-denied · Edit · per rules.deny_tools (triggered by @bob)
```

So even if you weren't watching live, scrolling back tells you
exactly who decided what.

**Driver attribution.** Inside the daemon's view of each session,
`last_driver_user_id` tracks who most recently injected a prompt.
Used to attribute auto-deny rules and (in future) for the pinned
dashboard's "🚗 @driver" line.

**On-disk audit.** `~/.claude/aipager-audit.jsonl` records every
decision with `user_id`, `username`, `display_name` fields so
admins can post-hoc reconstruct what each user did.

## Privacy considerations

- The chat-id filter still applies. Even with team mode, the bot
  only listens to **the configured group**. Adding the bot to a
  second group doesn't activate it there.
- Read-only users **can read** prompts and tool inputs. They can't
  act, but they see everything. If you need to hide some
  conversations from an observer, that observer doesn't belong in
  the group.
- `team.yaml` is mode 0600 (owner-only). Bot token + chat ID stay
  in `config.env`, also 0600.
- The audit log is owner-only (`~/.claude/aipager-audit.jsonl`).

## Revoking a user

1. Edit `~/.config/aipager/team.yaml` and delete the user's entry.
2. Restart the daemon:
   ```sh
   aipager service restart        # if running as a service
   # or
   pkill -f 'aipager start' && aipager start
   ```
3. Optionally also kick them from the Telegram group.

Step 2 is the security-critical one — the daemon caches the
allow-list at startup; until you restart, they can still act.

## Related docs

- [Architecture](architecture.md) — process model.
- [Bot commands](commands.md) — interface reference.
- [Security model](security.md) — trust boundary, threat list.
- [Troubleshooting](troubleshooting.md) — `aipager doctor` reference.
