# Observer Bots

Read-only Telegram bots that mirror notifications from the primary bot. They receive summaries, warnings, and errors — but can't control sessions.

## Setup

1. Create a bot via [@BotFather](https://t.me/BotFather)
2. Start a chat with the bot and send `/start`
3. Get your chat ID (send a message, then check `https://api.telegram.org/bot<TOKEN>/getUpdates`)
4. Add to `.env`:

```
OBSERVER_BOTS=<bot_token>:<chat_id>
```

Multiple observers (comma-separated):

```
OBSERVER_BOTS=111:AAA_first:12345,222:BBB_second:67890
```

The format is `token:chat_id` — parsing uses the **last** colon as delimiter (bot tokens contain an internal colon).

5. Restart the daemon

## What observers receive

| Event | Example |
|-------|---------|
| Idle summary | "Finished" + response text (+ .txt file for long responses) |
| API error | "Anthropic servers overloaded" (no retry button) |
| Context warning | "Context at 82% — auto-compact soon" |
| Compacting | "Compacting" |
| Compact done | "Compacted: 82% → 4%" |

## What observers DON'T receive

- Busy animations / spinner
- Tool call updates
- Permission prompts (Allow/Deny)
- AskUserQuestion dialogs
- Any inline keyboards or buttons

Observers are completely stateless — fire-and-forget sends. A failing observer never affects the primary bot.
