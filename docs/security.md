# Security model

aipager runs Claude Code on your behalf and is driven from Telegram.
Two questions follow:

1. Who can drive the bot?
2. What can the bot do once driven?

## Trust boundary

Every handler the bot exposes — message, file, voice, callback —
is gated by `python-telegram-bot`'s `filters.Chat(int(CHAT_ID))`.
The chat ID is read from `~/.config/aipager/config.env`. **Only the
configured chat can interact with the bot.** Messages from any
other chat are silently ignored at the framework layer.

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
| Bot token | `~/.config/aipager/config.env` (`AIPAGER_BOT_TOKEN=`) | 600 by default |
| Chat ID | same file (`AIPAGER_CHAT_ID=`) | 600 by default |

Neither value is ever logged. Neither is committed — `config.env`
is in the user's `~/.config`, not the repo. The Trusted Publisher
PyPI release flow never touches secrets either; OIDC handles auth.

If you suspect the token is compromised, revoke it from
[@BotFather](https://t.me/BotFather) (`/revoke`), generate a new
one, and re-run `aipager config`.

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

## Audit log

Every Allow / Deny / Continue tap, plus every `AskUserQuestion`
answer, appends one JSON line to `~/.claude/aipager-audit.jsonl`:

```json
{
  "ts": "2026-05-18T15:42:11+00:00",
  "session": "claude-jim",
  "label": "jim",
  "action": "allow",
  "tool": "Bash",
  "summary": "ls -la /tmp"
}
```

Fields:

- `ts` — ISO 8601 UTC timestamp, second precision.
- `session` — internal Claude session id (`claude-<label>`).
- `label` — the friendly session label.
- `action` — `allow`, `deny`, `continue`, or `answer`.
- `tool` — empty for `answer`; otherwise the tool name.
- `summary` — first 500 chars of the tool input or the question
  body.

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

aipager listens on **zero TCP ports**. Outbound:

- HTTPS long-poll to `api.telegram.org` (Telegram bot polling).
- HTTPS to `pypi.org` and friends, only when the user taps the
  voice install button.

Inbound:

- Unix datagram socket at `/tmp/aipager.sock`. Bound and chmod'd
  by the daemon at startup
  (`aipager/hook_receiver.py:192-197`). Mode `0o666` so any local
  process can send hook events to it — same trust as
  `~/.claude/settings.json`, which already controls what runs
  hooks.

This means: the daemon is not a remote attack surface. A network-
level attacker cannot reach it without a foothold on the host.

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
| Audit log tampering | Append-only; out of scope to prevent without a separate signing daemon |
| Network attacker | No inbound port, not directly reachable |
| Local privilege escalation | No sudo / setuid; daemon stays in user space |
| Voice audio leaking to cloud | Transcription is local |

## See also

- [Architecture](architecture.md) — process model.
- [Hook events](hooks.md) — what the daemon actually sees from claude.
- [Bot commands](commands.md) — the user-driven side.
