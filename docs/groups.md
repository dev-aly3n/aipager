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

Run `aipager config` and pick **Team** at the mode prompt. The
wizard:

1. Shows the warning panel above.
2. Asks for the Telegram **group chat ID** (negative integer; find
   it by adding the bot to the group, sending `/start`, then hitting
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and reading
   `chat.id`).
3. Walks you through adding **users** — label, Telegram user ID,
   role.
4. Optionally enables a default **deny rule** (`Write` + `Edit`),
   which the next section explains.
5. Writes `~/.config/aipager/team.yaml` (mode 0600).

You can hand-edit the file later to add / remove users — restart
the daemon after changes (`aipager service restart` or kill the
foreground daemon and re-run `aipager start`).

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
  manually approve dangerous tools when needed.
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
