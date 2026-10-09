# Observer Bots

Read-only Telegram bots that mirror notifications from the primary bot. They receive summaries, warnings, and errors - but can't control sessions.

## Setup

1. Create a bot via [@BotFather](https://t.me/BotFather)
2. Start a chat with the bot and send `/start`
3. Get your chat ID (send a message, then check `https://api.telegram.org/bot<TOKEN>/getUpdates`)
4. Set `OBSERVER_BOTS` in the daemon's environment. For a foreground
   daemon, export it before `aipager start`; for the systemd service:

```sh
systemctl --user edit aipager    # add under [Service]:
# Environment=OBSERVER_BOTS=<bot_token>:<chat_id>
```

Multiple observers (comma-separated):

```
OBSERVER_BOTS=111:AAA_first:12345,222:BBB_second:67890
```

The format is `token:chat_id` - parsing uses the **last** colon as delimiter (bot tokens contain an internal colon).

5. Restart the daemon

## What observers receive

Observers get these notices from **every chat the bot serves**, every
DM and every group, with no filter: set one up only for a chat you
trust with all of it.

| Event | Example |
|-------|---------|
| Turn finished | `💬 x1 · Finished (2m 5s, +12 -3)`: the result line only, not the answer; plus the full log as an `.md` file when the answer or the card was cut |
| A background job finished | `✅ x1 · Finished (...)`, or `⚠️ x1 · Finished (...)` when an agent was lost or a background command's end was never seen |
| API error | `⚠️ x1 · Anthropic's servers are overloaded. Try again in a moment.` (no retry button) |
| Context warning | `⚠️ x1 · Context at 82% - auto-compact soon` |
| Compacting | `🔄 x1 · Compacting` |
| Compact done | `📦 x1 · Compacted: 82% → 4%` |
| A prompt still waiting | `⬆️ x1 · still waiting for your answer above`, with the permission or question it waits on |
| A quiet working session | `⏳ x1 · still working (quiet for 10 min)` |
| A message Claude Code did not take | `⚠️ x1 · Not taken by Claude Code` |
| Session ended | `🔴 x1 · Session exited` |

## What observers DON'T receive

- Busy animations / spinner
- Tool call updates
- Permission prompts and their buttons (only the "still waiting" reminder above names what a prompt asks)
- AskUserQuestion dialogs
- Any inline keyboards or buttons

Observers are completely stateless - fire-and-forget sends. A failing observer never affects the primary bot.
