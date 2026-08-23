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

- **No config yet** → first-run wizard. Picks mode upfront, then
  walks token → mode → chat (group or DM) → members + roles (if
  team) → deps → settings → write.
- **Config exists** → edit menu. Opens a current-state panel and
  offers focused actions: add a user, remove a user, change a
  user's role, edit deny rules, switch mode, refresh the bot token,
  or run the full setup again.

The wizard writes everything to `~/.config/aipager/aipager.yaml`
(mode 0600): the bot token, the chat(s) the bot serves, and each
chat's members with their roles. What each *role may do* lives in
the user-owned `~/.config/aipager/policy.yaml` — see
[Rules](#how-rules-work) below. Installs configured with the older
`team.yaml` format keep working and are migrated automatically —
by the daemon at startup, or by the wizard on its next run.

For the group chat ID you can paste it manually, or pick
"Auto-detect" and let the wizard watch for a `/start` in the group
(add the bot first). The same auto-detect works for member user IDs
— the wizard captures the next message's sender id and suggests
their Telegram username as the label.

### Live reload

The wizard signals the running daemon via **SIGUSR1** after every
config change, so add-user / remove-user / change-role / edit-rules
/ switch-mode apply **without** a daemon restart. The reload covers
`aipager.yaml`, `policy.yaml`, and legacy `team.yaml` alike.

Restart is still required when changes affect the **bot token** or
the **chat id** the bot polls. The wizard distinguishes between
hot-reloadable and restart-needed changes and prints the appropriate
hint.

To trigger a reload manually (e.g. after a hand-edit):

```sh
kill -USR1 $(pgrep -f 'aipager start')
```

If a config file is malformed at reload time, the daemon logs a
WARN and keeps the previous in-memory config — so you can't lock
yourself out by typo'ing a hand-edit.

Also, on `@BotFather`, leave **privacy mode ON** (the default).
That way the bot only sees messages that mention it or reply to
its messages — not every chat in the group.

## Roles

Four built-in roles (see `aipager/safety.py`):

| Role | Send prompts | Approve | Bypass deny rules | Bypass the safety floor |
|---|---|---|---|---|
| `owner` | ✅ | ✅ | ✅ | ✅ |
| `admin` | ✅ | ✅ | ✅ | ❌ |
| `user` | ✅ | ✅ | ❌ | ❌ |
| `read_only` | ❌ | ❌ | ❌ | ❌ |

- **owner** — full control, including the built-in safety floor.
  There should be exactly one: the person who runs the machine.
- **admin** — bypasses role deny rules (their Allow tap works on a
  restricted tool), but the safety floor still applies.
- **user** — deny rules apply; an Allow tap on a rule-denied tool is
  auto-rejected.
- **read_only** — observers. They see every message and can call
  `/status`, but their text / voice / file messages are ignored.

Custom roles can be defined in `policy.yaml`; the four above are the
defaults it layers on top of.

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
- `deny_paths_no_access` / `deny_paths_no_write` — path rules.

Underneath all roles sits a built-in **safety floor** (protected
paths and command patterns) that only `owner` bypasses.

When claude asks for permission to use a rule-denied tool and the
driver's role does not bypass rules:

- The bot **does not show the permission prompt**.
- It writes a deny back to claude.
- It posts a one-line notice in the chat, e.g.
  `⛔ [jim] · Auto-denied · Write · (triggered by @bob)`.
- It writes an audit record with `denied: true`.

## Who a message runs as

Enforcement keys off **who sent each message**, not whose turn it
happens to interrupt:

- Every Telegram message carries its sender's identity and rules
  into the session (see
  [security → team-mode enforcement](security.md#team-mode-enforcement)).
  A message held back — because a permission prompt was open, or
  because someone else's message was still waiting — is delivered
  later **with the original sender's permissions**, not whoever
  drove the session most recently.
- Messages from different users are never merged into one turn:
  while one user's message is still waiting to be picked up, another
  user's message is held until it clears. If a mixed turn happens
  anyway, it runs under the *most restrictive* combination of the
  contributors — privileges never widen.
- Buttons that act on a running turn (Stop, Kill, Restart, Replace,
  perms-switch) refuse when tapped from a card belonging to an
  earlier task — `That task already finished — …` — so a stale tap in a
  busy group can't destroy someone else's current work.

## What everyone sees

**Permission audits.** Every Allow / Deny is replied to in chat:

```
✅ [jim] · Allowed by @alice · Bash: ls -la /tmp
🚫 [jim] · Denied by @bob · WebFetch: https://example.com
⛔ [jim] · Auto-denied · Edit · (triggered by @bob)
```

So even if you weren't watching live, scrolling back tells you
exactly who decided what.

**Message states.** 👀 on a message means it was sent to the
session; 👍 means Claude picked it up. `/whoami` shows your own id
and role.

**On-disk audit.** `~/.claude/aipager-audit.jsonl` records every
decision with `user_id`, `username` and scope fields so admins can
post-hoc reconstruct what each user did.

## Privacy considerations

- The chat filter still applies. Even with team mode, the bot only
  listens to **the configured chat(s)**. Adding the bot to a second
  group doesn't activate it there.
- Read-only users **can read** prompts and tool inputs. They can't
  act, but they see everything. If you need to hide some
  conversations from an observer, that observer doesn't belong in
  the group.
- `aipager.yaml` and `policy.yaml` are mode 0600 (owner-only).
- The audit log is owner-only (`~/.claude/aipager-audit.jsonl`).

## Revoking a user

1. Run `aipager config` → Remove a user (or hand-edit
   `aipager.yaml` and delete their member entry).
2. The wizard reloads the daemon live (SIGUSR1) — no restart needed.
   After a hand-edit, send the signal yourself:
   ```sh
   kill -USR1 $(pgrep -f 'aipager start')
   ```
3. Optionally also kick them from the Telegram group.

Step 2 is the security-critical one — until the reload lands, the
previous allow-list is still in memory.

## Related docs

- [Architecture](architecture.md) — process model.
- [Bot commands](commands.md) — interface reference.
- [Security model](security.md) — trust boundary, threat list.
- [Troubleshooting](troubleshooting.md) — `aipager doctor` reference.
