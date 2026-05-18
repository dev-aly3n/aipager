# Bot commands and interface

How you drive aipager from Telegram. Three input channels: slash
commands, inline-keyboard buttons, and free messages (text / files /
voice).

The bot only accepts input from the chat ID configured in
`~/.config/aipager/config.env`. See [security](security.md) for the
trust boundary.

## Slash commands

Registered in `aipager/telegram_bot.py:400-406`. Telegram autocomplete
shows these in its slash-command menu, refreshed at daemon startup
and on every session change.

| Command | Args | What it does |
|---|---|---|
| `/start`, `/help` | — | Print the welcome panel and persistent keyboard. |
| `/status` | — | One-message snapshot of every live session: model, context %, cost, queue depth. |
| `/stop` | — | Send SIGINT to the active session's current claude turn (does not destroy the session). |
| `/kill [label]` | optional session label | Destroy a session. With no arg, opens a picker. Always two-tap: shows `[💀 Kill] [Cancel]`. |
| `/new <label> [prompt]` | label (required), optional first prompt | Create a new dtach + claude session named `claude-<label>`. If a prompt follows, it's injected as the first message. |
| `/clearqueue` | — | Drop every queued prompt for the active session without interrupting the running turn (unlike `/stop`). Replies with the count cleared. |
| `/<label>` | optional message text | Switch the active session to `<label>`. If text follows the command, also inject it as the next prompt. Registered dynamically — one per live session. |

`/status` results come from the same data `aipager status` shows on
the CLI; no Telegram round-trip for the session list itself.

## Inline keyboard

A persistent four-row keyboard sits below the chat input. Rows from
top to bottom: shortcuts, submenus, action bar, kill bar.

| Row | Buttons | Customizable? |
|---|---|---|
| Shortcuts | `📋 Templates`, `🎛 Commands`, `🤖 Models` | Yes — see below |
| Sessions | one button per live session, plus `➕ New` | No (auto-built from registry) |
| Actions | `📊 Status`, `⏹ Stop` | No |
| Kill | `💀 Kill` | No |

Tapping `📋 Templates`, `🎛 Commands`, or `🤖 Models` opens a submenu
keyboard. Each entry sends a canned prompt or slash command:

- **Templates** — bulk prompts you find yourself typing repeatedly,
  e.g. `Write tests for the changes`, `Explain your plan before
  making changes`, `Update CLAUDE.md with what you learned`.
- **Commands** — slash commands claude code natively handles
  (`/init`, `/security-review`, `/compact`, etc.).
- **Models** — quick model switches (`/model sonnet`, `/model opus`,
  `/model haiku`, `/model opusplan`).

Override the default layout by writing
`~/.config/aipager/keyboard.json`:

```json
{
  "templates": [{"label": "Deploy",  "prompt": "Deploy to staging"}],
  "commands":  [{"label": "Compact", "send": "/compact"}],
  "models":    [{"label": "Sonnet",  "send": "/model sonnet"}]
}
```

Each section is independent — missing sections fall through to the
built-in defaults so you can override one without specifying the
others. Malformed JSON fails open with a logged warning. Changes
require a daemon restart.

## Per-message inline buttons

Most bot replies carry context-specific buttons:

### Permission prompts

When claude asks to run a tool that needs approval, the busy message
becomes:

```
🔐 [jim] Bash
  command: ls -la /tmp

  [✅ Allow]  [❌ Deny]  [➡️ Continue]
```

- **Allow** — write `approve` back to claude, log to audit, resume.
- **Deny** — write `deny`, log, claude blocks the tool call.
- **Continue** — write `approve` AND tell claude to keep going past
  any pending pauses (e.g. plan-mode confirmations).

Every tap is recorded in `~/.claude/aipager-audit.jsonl` and mirrored
as a one-line reply threaded under the busy message:
`✅ [jim] · Allowed · Bash: ls -la /tmp`.

### Idle responses

Once a turn ends, the busy message becomes the IDLE response. If
claude's last message is long enough to spill past Telegram's 4 KB
limit it's sent as a `.txt` attachment with a `📎 Full response
attached below ↓` footer. Buttons:

- **🔄 Retry** — re-send the last prompt to the same session.

### Kill confirmation

`/kill <label>` and the `💀 Kill` button always confirm:

```
⚠️ Kill session [jim]?
This will terminate the running claude process.

  [💀 Kill]  [Cancel]
```

### Voice install (when extra isn't installed)

When you send a voice message and `aipager[voice]` isn't installed:

```
⚠️ Voice messages need the optional voice extra
   (~200 MB install · ~74 MB model on first use).

  [📦 Install voice]  [Cancel]
```

Tapping Install runs the right install command for your installer
(`uv tool install --reinstall aipager[voice]`, `pipx install
--force`, or a `pip install faster-whisper` fallback) with a 5 s
heartbeat edit, then offers a `[🔄 Restart daemon now]` button on
success.

### Restart

`🔄 Restart daemon now` always works:

- Service-managed daemons: `systemctl --user restart aipager.service`
  on Linux, `launchctl kickstart -k` on macOS.
- Foreground / editable daemons: spawn a detached replacement that
  waits for the parent PID to die, then `exec aipager start`. The
  current daemon SIGTERMs itself once the spawn is alive.

No SSH required.

## Free messages

### Text

Treated as the next prompt for the **active session** (the one whose
slash command you last sent). If a session is BUSY, the text joins
its pending queue. Queue is capped at 50 — the 51st message replies
with a friendly `⚠️ Queue is full`.

### Files

Uploaded files are downloaded into the active session's workspace
and the path is offered to claude. The 20 MB Telegram bot file
download cap is enforced up-front; oversized files get a clear
rejection before any download attempt.

### Voice

Voice messages route through `faster-whisper` (the `aipager[voice]`
extra). The audio is transcribed locally and the transcript is
injected as if you had typed it. See [hooks → UserPromptSubmit](hooks.md#userpromptsubmit)
for what happens next.

## See also

- [Architecture](architecture.md) — where the bot fits.
- [Hook events](hooks.md) — what aipager hears back from claude.
- [Troubleshooting](troubleshooting.md) — when commands misbehave.
