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

Enabling the Mini App (`aipager miniapp enable`, with no `--url`
override) spawns a managed [Cloudflare quick
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
memory — it is never written to `aipager.yaml`, `config.env`, or
anywhere under `~/.config/aipager/`. If the tunnel dies mid-run,
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
| Audit log tampering | Append-only; out of scope to prevent without a separate signing daemon |
| Network attacker | No inbound port, not directly reachable |
| Local privilege escalation | No sudo / setuid; daemon stays in user space |
| Voice audio leaking to cloud | Transcription is local |
| Agent reads the daemon's own Claude credential | Expected, not a bug — see [The Claude credential](#the-claude-credential--what-actually-protects-it). Scope the credential itself, not the file it's stored in |

## See also

- [Architecture](architecture.md) — process model.
- [Hook events](hooks.md) — what the daemon actually sees from claude.
- [Bot commands](commands.md) — the user-driven side.
