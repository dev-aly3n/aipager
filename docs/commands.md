# Bot commands and interface

How you drive aipager from Telegram. Four input channels: slash
commands, keyboard buttons, the Mini App dashboard, and free
messages (text / files / voice).

The bot only accepts input from the configured chat(s) - see
[security](security.md) for the trust boundary.

## Slash commands

Registered in `aipager/bot/lifecycle.py`. Telegram autocomplete
shows these in its slash-command menu, refreshed at daemon startup
and on every session change.

| Command | Args | What it does |
|---|---|---|
| `/start` | - | The home screen: this chat's sessions, where a plain message goes (`✍️ Messages go to x1.`), and `🆕 New session`, `⚙️ Settings` and, when this chat has an ended session to bring back, `↩️ Resume`. Also shows the persistent keyboard. |
| `/help` | - | A short guide by task (start, talk, control, manage, settings, and for admins `/update`), with the tip that tapping a command sends it at once: long-press it (phone) or press Tab (desktop) to add text first. For the owner of the install (the person in its own private chat with the bot) it also has a `🐞 Report a problem` button: it opens a preview of the exact problem report in that private chat, with Send, Add a note and Cancel. Nothing is sent until you tap Send ([what a report contains](problem-reports.md)). |
| `/app` | - | Open the Mini App dashboard (sessions, diff viewer, settings). |
| `/status` | - | This chat's sessions as a list you can act on. Each says its state in words (`⚙️ working 3:12 · Bash: run tests`, `⏳ needs you · Bash: make deploy`, `💤 idle`), with `✍️` on the one a plain message goes to, and a second line with the model, context, cost, what is queued (`queue 3 (1 queued, 2 notes)`), and running agents and shells. Each has a row of buttons: `✍️ x1` sends your next messages there, `⏹ Stop` while it works, `Answer` while it waits for you, and `⋮` for the rest. Below: `🆕 New`, and `⚫ Ended (n)` for the sessions that ended (resume, delete, or clear them all from the list; they stay in `/resume`). |
| `/stop [label]` | optional | Interrupt a session's current turn. Bare, it stops the one session of this chat that is working (even if your messages go to another one; a session whose background agent or shell is still running counts as working), asks which with a picker when several are, and says `Nothing is running.` when none is. `/stop x1` (or `/x1 stop`) stops x1 (or replies `x1 is not working.` when it is not). Also discards queued messages and replies with how many were discarded. |
| `/now` | - | Send the messages Claude is holding in the active session's queue right now, instead of after the current step (Claude Code's own send-now keys, Ctrl+X Ctrl+S). Works any time, including while the chat is muted or in minimal mode; replies `Nothing is waiting in the queue` when Claude holds nothing. See [Send a queued message now](#send-a-queued-message-now). |
| `/new [name] [first message]` | all optional | Start a session. `/new x1` starts it at once, and `/new x1 fix the tests` also sends "fix the tests" as its first message. Sent bare (for example tapped from the menu), `/new` asks for the name, and the next message you send is used exactly like the argument, so `/new` then `x1 fix the tests` gives the same result. In a group, reply to the card with the name (or mention the bot: `@aipagerbot x1`), since a plain message there does not reach the bot. Only that next message: if you do anything else first (a photo, another command, a reply to another message, a keyboard template, another button), it is handled as usual and the card closes; `/status`, `/settings` and the keyboard's menus leave it open. The card that asks also shows the mode, model and folder the session will get, with buttons to change them first. Every way ends in the same Ready card: what the session got, that your next message goes to it, and one-tap buttons to switch to another mode or change the model. New sessions start in Auto mode for everyone; change what /new uses in `/settings` → New sessions. A `!` before the name starts the session in Full access mode, for an admin (the `owner` and `admin` roles) or a person in their own private chat with the bot whose role may send prompts and answer Claude (`user`, not `read_only`); for anyone else it starts in Auto mode and the card says why. A name that is in use offers `↩️ Switch to it` (when that session runs) or `♻️ Resume` (when it ended), `🆕 Replace (fresh)` and `↩️ Cancel`, for the person who sent `/new` only. The card that asks for a name expires after 10 minutes unused (`⏱ This new-session card expired. Send /new again.`). A member whose role lacks `can_manage` (`user`, `read_only`) starts only inside the chat's [root folder](groups.md#root-folders): the card's folder is the root folder, the Folder page lists it and the folders directly inside it (no "Default folder" row), New folder creates inside the one picked, and Switch to it, Resume and Replace on a session working outside the root folder are refused ("This session works outside your folder ~/..., so your role can't resume, restart or switch it. Ask an admin."). |
| `/resume [label]` | optional | Resume a previously-gone session by name, or open a picker. A session comes back in the mode it was in. A session that was in Full access mode comes back in Full access only for an admin or a person in their own private chat; for anyone else it comes back in Auto mode and the reply says so (`Resumed x1 in Auto mode: Full access mode needs an admin.`). A session saved by an older aipager in its old Auto (which ran with no checks) comes back in Full access mode, and one saved in Ask comes back in Ask mode. A session that ended stays listed for `GONE_SESSION_MAX_AGE_DAYS` (default 14) and then leaves the registry; its Claude transcript is untouched. A member whose role lacks `can_manage` cannot resume a session whose folder is outside the chat's [root folder](groups.md#root-folders) (owners and admins can); the same holds for Restart and every mode switch, the in-place one between Ask and Auto included. |
| `/kill [label]` | optional | End a session, always after a confirm: `⏹ End x1? Claude stops and the session closes.` with `[⏹ End] [Cancel]`. Bare, it goes to the confirm when this chat has one live session, else a picker of this chat's live sessions. Also in `/status` → `⋮` → `⏹ End session`. |
| `/restart [label]` | optional | Restart a session after a confirm: Claude stops and starts again, keeping its conversation, in the mode aipager last started it in or switched it to. A session started in the terminal with `aipager session` comes back in Ask mode unless it was switched from Telegram since (aipager does not know the mode the terminal started it in, and never guesses a wider one); the same goes for resuming it from Telegram. |
| `/rename [label] [new]` | optional | Give a session a new name (ended sessions too). `/rename x1 newname` renames at once. With fewer words it asks: straight for the new name when the command can only mean one session, else a picker (a single word after `/rename` does not pick the session, since it could be the old name or the new one). In a group, reply to the question with the name. |
| `/delete [label]` | optional | Drop an ended session from the list, after a confirm. |
| `/diff [label]` | optional | Show the session's working-directory git diff. Bare, it shows the session your messages go to. |
| `/clearqueue` | - | Drop every not-yet-picked-up message for the active session - both messages aipager is holding and messages already queued inside Claude - without interrupting the running turn. Replies with the count cleared. |
| `/mode [label] [ask\|auto\|full]` | optional | Show a session's permission mode with a button to switch to each other mode: `✍️ x1 is in 🤖 Auto mode.` `[💬 Switch to Ask mode]` `[🔓 Switch to Full access mode]`. There are three modes. **💬 Ask mode**: Claude asks before each action. **🤖 Auto mode** (the default): Claude Code's safety check runs safe actions and stops risky ones for you to decide. **🔓 Full access mode**: everything runs with no checks (`--dangerously-skip-permissions`); only an admin, or a person in their own private chat with the bot whose role may send prompts and answer Claude (`user`, not `read_only`), may choose it. Auto and Full access only change Claude Code's own permission prompts: aipager's hook still applies the member's role rules. `/mode ask`, `/mode auto`, `/mode full` or `/mode x1 ask` switch straight away; the mode's own one word in the chat's language works too, without the word for mode (`/mode x1 خودکار` for 🤖 مود خودکار, `/mode x1 کامل` for 🔓 مود دسترسی کامل). When a live session in this chat has exactly that name (a session called `demander`, say), the word names the session: `/mode demander` shows that session's mode, and `/mode x1 demander` still switches x1. `ask`, `auto` and `full` always mean the mode, in either word order (`/mode x1 ask` or `/mode ask x1`); only when both words are mode words and just one of them is a live session's name does that one name the session, so with a session called `full`, `/mode full ask` and `/mode ask full` both switch it to Ask mode, while `/mode full` alone is still the mode. Switching between Ask and Auto happens in place, with no restart and at any time (also while Claude works): aipager presses Shift+Tab in the session the way you would, the card says `⚙️ Switching x1 to 🤖 Auto mode without a restart.` until Claude Code reports the new mode with its next step, then shows the result. If Auto mode is not available for the session (its model or plan), the card says so and the session stays in Ask mode. Not while the session shows a question or permission prompt: answer it first. Going to Full access needs an admin (or a person in their own private chat) and a confirm, and restarts the session (the conversation is kept); leaving Full access restarts it too, at once. A session started in Full access restarts for every switch, also after it was switched to Ask or Auto in the terminal, and so does one that has reported Full access since it started (a terminal session started with `--dangerously-skip-permissions`): Shift+Tab there could cycle it back into Full access. On a busy session a restart offers `Stop task & switch` / `Not now`. A member whose role lacks `can_manage` cannot switch a session whose folder is outside the chat's [root folder](groups.md#root-folders), in place or with a restart. Tapping a switch changes the card itself instead of sending a second message. `/perms` is the old name and still works the same. |
| `/settings` | - | How this chat's sessions look and reply: the language aipager uses, message layout, diff previews, long-turn card updates, formatting, answer length, vocabulary level; what `/new` starts a session with; per-session overrides; and, in the owner's private chat, problem report offers. See [Settings](#settings). In a group anyone can look, and changing a setting needs an admin. |
| `/language` | - | The language aipager uses for its own messages and buttons in this chat (the same list as the first row of `/settings`). Each language is listed in its own name: English, then Español, Русский (Russian), Português (Brasil), 简体中文 (Chinese, Simplified), Français, Deutsch (German), 한국어 (Korean), العربية (Arabic), Bahasa Indonesia, Українська (Ukrainian), Italiano, Türkçe (Turkish), فارسی (Persian) and Tiếng Việt (Vietnamese). A private chat that starts after this release follows your Telegram app's language; one that existed before stays English until you choose (see [Languages](#languages)). In Spanish, Portuguese, French, Italian and Indonesian the reply keyboard's words stay `status`, `stop`, `new` and `kill`, the same as their commands. German and Turkish use `Status`, `Stop`, `Neu…` and `Beenden…`, and `Özet`, `Stop`, `Oluştur` and `Sonlandır`; the English words keep working when typed. In Persian and Arabic, aipager's text reads right to left with Persian or Arabic digits; session names, commands, model names and code stay as they are. The list says what it changes: only aipager's own text, here and in the Mini App; your Claude sessions don't change, and Claude answers in the language you write to it. In a group anyone can look, and changing it needs an admin. `language` is a reserved session name. |
| `/whoami` | - | Show who you are here: in team mode your label, your role in this chat and the rules it gives you. |
| `/update` | - | Admin only (the `owner` and `admin` roles, or a role with `can_manage`). Check aipager and Claude Code for newer versions, then update whatever has one with a single button. See [Update](#update). |

The `/` menu shows your sessions first (`/x1 · Talk to x1`), then the
frequent commands: `new`, `status`, `stop`, `now`, `resume`, `mode`,
`settings`, `language`, `help` (and `app` with the Mini App on, in a private chat only). `kill`, `restart`,
`rename`, `delete`, `diff`, `clearqueue`, `perms`, `whoami` and `update`
are not in the menu but work when typed; the session actions are also in
`/status` → `⋮`.

**Commands that act on a session** (`/stop`, `/mode`, `/kill`, `/restart`,
`/rename`, `/delete`, `/diff`) follow one rule:

- With a name (`/restart x1`) they act on that session of this chat
  (`/rename` needs the old and the new name for that, see above).
- Bare, they act on the one session the command can mean (the only one
  working for `/stop`, the only live one for `/kill` and `/restart`, the
  only ended one for `/delete`), going through the usual confirm where
  there is one.
- Otherwise they show a picker of only this chat's sessions the command
  makes sense for: the one your messages go to first, marked `✍️`, then
  the rest by name, and `✖️ Cancel`. Picking one leads to the same card as
  typing its name.
- In a group, bare, they act only on your own current session, when the
  command makes sense for it; otherwise they always show the picker, even
  with one session, since that one may be another member's (see
  [groups](groups.md#talking-to-a-session-in-a-group)).

`/mode` and `/diff` first look at the session your messages go to.

### Settings

`/settings` (and the Mini App's Settings tab, which shows the same
choices) sets how this chat's sessions look and reply. Each chat has its
own; in a group anyone can look, and changing a setting needs an admin.
In your own private chat you change them yourself, whatever your role,
in Telegram and in the Mini App.
A row marked ✅ is set to something other than its default (Message
layout always shows its choice).

| Setting | Choices (default first) | What it does |
|---|---|---|
| 🌐 Language | Automatic (your Telegram language), in a private chat; English | The language of aipager's own messages and buttons in this chat and in the Mini App, shown in its own name (the same list as `/language`). It does not change your Claude sessions: Claude answers in the language you write to it. English, Español, Русский (Russian), Português (Brasil), 简体中文 (Chinese, Simplified), Français, Deutsch (German), 한국어 (Korean), العربية (Arabic), Bahasa Indonesia, Українська (Ukrainian), Italiano, Türkçe (Turkish), فارسی (Persian) and Tiếng Việt (Vietnamese) are offered; Automatic follows your Telegram app's language (see [Languages](#languages)). |
| 🖼 Message layout | Busy card + result; Merged into busy message; Replace with result | How a finished turn appears. Busy card + result keeps the card above the answer as the record of how it was reached; Merged puts the answer into the card; Replace removes the card and leaves the answer alone. The default is Busy card + result (`KEEP_FINISHED_CARD=0` makes it Replace for chats that never chose). See [idle responses](#idle-responses). |
| 📝 Diff previews | Off; On | On posts each `Write`/`Edit` as its own diff message under the busy card. Off keeps one card and one answer; the card still lists every edit, and the Mini App has a diff viewer. |
| ⏱ Long-turn card updates | On; Off | On, a busy card refreshes every 10 s after 2 minutes of a turn and every 30 s after 10 (never slower, however long the turn), and its clock then moves in 30 s steps; Off keeps the first-minutes pace for the whole turn (see [troubleshooting](troubleshooting.md#a-long-turns-card-refreshes-less-often)). |
| ✏️ Simple formatting | Off; On | On asks Claude for plain prose and dashed lists only: no tables, code blocks, headings or bold. |
| 📏 Answer length | Don't apply any rule; Extra short; Short; Medium; Long | Asks Claude for answers of that length. |
| 🎓 Vocabulary level | Don't apply any rule; Simple words; Normal; Advanced | Asks Claude for that vocabulary. |
| 🎨 Appearance | Stop button, Allow and Deny buttons, App button, Settings menu | How aipager's buttons look in this chat; everything starts on Normal (Telegram's own look). Telegram lets a bot colour a button blue, green or red and nothing else, so: the Stop button (the busy card's, and the one under a question or permission prompt) Normal, Blue, Green or Red; Allow and Deny Normal or Coloured (Allow and Allow always green, Deny red); the App button (on the pinned message and under `/app`) Normal, Blue, Green or Red (the menu button next to the message box cannot be coloured); the Settings menu Normal (✅ marks the current choice) or the current choice also blue. For the whole chat, not per session; in a group only an admin changes it. Telegram apps that do not support colours show every button plain. The Mini App's Settings tab has the same choices in a section of their own, Appearance, after Chat preferences, each with a coloured dot. |
| 🆕 New sessions | Permissions (the mode), Model, Folder | What `/new` starts a session with when you do not choose: Ask, Auto (the default) or Full access mode, a model from the Models list or Claude Code's default, and a folder. Only an admin, or a person in their own private chat with the bot, can choose Full access here (a private chat's screen does not show it to anyone else), and it never applies to someone who may not use it: their sessions start in Auto mode. |
| 👤 Per-session preferences | a session, then any setting above except New sessions | Overrides for one session (also in `/status` → `⋮` → `👤 Preferences`); ⭐ marks a setting it overrides, and "Use chat default" removes the override. The Mini App's session page sets the same overrides. |
| 🐞 Problem reports | Ask me; Off | Only in the install owner's private chat. Ask me lets aipager now and then offer to send a report of an internal error it hit more than once (at most once in 3 days); Off never offers (the Report a problem button in `/help` still works). Two offers turned down in a row switch the offers off, and this row then says so, with a button to turn them back on. See [problem reports](problem-reports.md#when-aipager-offers-a-report). |

Answer length, vocabulary level and simple formatting reach Claude as a
short instruction added to each prompt sent from Telegram (by the hook,
see [hooks](hooks.md#userpromptsubmit)), using the session's own
overrides where it has them.

Whatever the layout, every busy card ends with its session's status
line (`⏳`/`✅ name · …`) and every answer starts with its result line
(`💬 name`, plus `· Finished (…)` when no finished card is left to show
the stats); the merged layout stacks the two, each line in its own
section. A divider line sits above the busy card's status line (small
text), and in the merged layout a second one separates the finished card
from the answer. In the card layout the answer deliberately follows the
finished card by a moment, so the card is seen to say Finished before
the answer lands under it; tune or disable that head start with
`FINISH_CARD_GRACE_SECONDS` (seconds, default 0.8; 0 sends both at
once). The message layout decides the card for every turn, whether or
not tools ran.

### Per-session dynamic commands

One command per live session, registered from its label:

| Form | What it does |
|---|---|
| `/<label>` | Make `<label>` the session your messages go to. The reply says so with its state and mode (`✍️ Now talking to x1 · 💤 idle · 🤖 Auto mode`, then "Send a message and it goes to x1."), offers **Answer** when it is waiting for you, and **⋮ More** for its menu. The full stats are in `/status`. In a group it says "Reply to a message from x1 (or mention @aipagerbot) to talk to it." instead, since a plain message there does not reach the bot (see [groups](groups.md#talking-to-a-session-in-a-group)). `/x1@aipagerbot`, which Telegram sends when you pick `/x1` from a group's menu, works the same. |
| `/<label> <prompt>` | Send `<prompt>` straight to that session. It also becomes the session your next plain messages go to. |
| `/<label> stop` | Interrupt that session's current turn. |

`/status` results come from the same data `aipager status` shows on
the CLI; no Telegram round-trip for the session list itself.

On the machine, `aipager status` and `aipager session ls` give each session
one state: `IDLE`, `BUSY`, `WAITING` (a permission or a question waits for
you, with what it is when known, for example `permission: Bash: make
deploy`) or `GONE`. With `--json`, each session also has `waiting_kind`
(`permission` or `question`) and `waiting_summary`, both `null` unless it
is waiting.

## Languages

aipager speaks English, Español, Русский, Português (Brasil), 简体中文, Français,
Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی and
Tiếng Việt, each listed in its own name.

- **Only aipager's own text changes.** The language is for aipager's messages,
  buttons, cards and the Mini App. Your Claude sessions do not change, and Claude
  answers in the language you write to it. Session names, commands, model names
  and code stay as they are.
- **`/language`** (or the first row of `/settings`) picks the language for the chat
  you are in. In a private chat it is also your own language: the pop-ups you see
  in a group and your Mini App use it.
- **Automatic** (private chats only) follows your Telegram app's language, and
  follows it again if you change it. A language aipager does not have reads as
  English.
- **New and existing chats.** A private chat that starts after this release
  follows your Telegram app's language until you pick one, and its first message
  in another language than English is followed by one short line saying so and
  how to change it. A chat that existed before stays English. If your Telegram
  app is in a language aipager speaks, you get one card offering it, with a
  `Keep English` button; it comes once, whatever you answer.
- **Groups** have one language, set by an admin in `/language`; until then they
  are English. The pop-up after a tap is in the tapper's own language: their
  choice from their private chat with the bot, else their Telegram app's
  language for someone who joined after this release, else the group's
  language. See [groups](groups.md).
- **Right to left.** In Persian and Arabic, aipager's text reads right to left
  with Persian or Arabic digits. When Claude answers in Persian or Arabic in a
  chat whose language is written left to right, the whole answer is a
  right-to-left message: aipager's own lines in it (the agents under the
  answer, the busy card's status line) sit on the right like the rest, and
  still read left to right.
- **Mode words.** `/mode` takes the mode's word in your language (`/mode demander`),
  and `ask`, `auto` and `full` always work. If a live session has exactly that name, the
  word names the session instead (see `/mode` above).
- **Bot description.** At start, aipager writes the bot's description (the text on
  its empty chat screen) and short description in each of these languages, for
  people whose Telegram is in that language. Telegram shows such a text instead
  of the one you wrote in BotFather, so aipager sets none while you have your
  own (description and short description are checked apart). It never
  overwrites a text of yours and never changes the English or default one.
  When its current text is already set in every language, a start asks
  Telegram nothing. Otherwise it first reads your BotFather text (one read
  each for the description and the short description): if you have one, it
  stops there, so with your own text every start costs those reads and no
  more. If you have none, it reads each language that does not have its
  current text yet, so a language where you wrote your own text is read again
  at every start. If you write a description in BotFather after aipager has
  set its own, readers of these languages keep aipager's (see
  [troubleshooting](troubleshooting.md#the-bot-description-is-not-the-one-i-wrote)).

## The Mini App

`/app` (and the Telegram menu button) opens a dashboard served by the daemon itself. It is on by default; manage it with `aipager miniapp enable|disable|status`. Every request is verified against Telegram's `initData` signature (see [security → Mini App tunnel](security.md#mini-app-tunnel)). The page loads nothing from anywhere else: no fonts, images or scripts from other sites.

- **Chats.** The app shows one chat at a time. If you belong to more than one chat (your DM and a group, say), a row of chat names at the top switches between them; the app opens on your own DM and remembers your last choice on that phone. Everything below is for the chosen chat, with your role in it. With one chat the row is not shown. See [groups](groups.md).
- **Sessions.** One sentence sums up the chat, for example "1 needs you, 2 working, 3 resting", with what has been spent beneath it. Each live session is a tile with a context ring, its folder, model and cost; working ones glow in the accent colour. Finished sessions rest on a collapsible **Finished** shelf. The **+** button starts a new session.
- **Needs you.** A session waiting on a permission prompt or a question sits in an amber tray at the top, showing what it is asking. **Answer in chat** re-sends that prompt, with its buttons, to the bottom of the chat, the same as the pinned bar's **Answer** button, and you answer it there. If it was the only session waiting, the app closes so the prompt is right in front of you; otherwise it stays open and says "Sent to the chat". It needs the right to prompt the session, and a copy tapped after the prompt was answered or replaced says "already answered".
- **A session's page.** The header shows the context ring, the state ("Working for 3:12", "Needs you (permission)", "Resting, last active 5m ago", "Finished") and a one-tap **Stop** (while working or waiting) or **Resume** (once finished). Below it: the waiting prompt with **Answer in chat**, the last few tool calls as a strip (tap it for the full timeline), the latest reply, the facts, the model control, the session's own settings, and the changed files and timeline. The ⋮ menu carries every action, with the same confirmations as before.
- **New session.** A guided card: name, then where (with **Recent** chips for the folders your sessions use, newest first), then model (with **Suggested** chips), then **More options** for the permission mode and reply style. Telegram's own button at the bottom reads **Start session**. For a member whose role lacks `can_manage`, "where" offers only the chat's [root folder](groups.md#root-folders) and the folders directly inside it (the root folder is the default, and New folder creates inside the one picked); a folder outside it is refused with the reason, and Resume, Restart and the mode switch of a session working outside it answer "This session works outside your folder ~/..., so your role can't resume, restart or switch it. Ask an admin."
- **Settings.** **App language** first: your own language for aipager's text in the app and your private chat (English, Spanish, Russian, Portuguese (Brazil), Chinese (Simplified), French, German, Korean, Arabic, Indonesian, Ukrainian, Italian, Turkish, Persian or Vietnamese; Automatic follows your Telegram app's language, see [Languages](#languages)). Anyone can change their own, it is never posted in a chat, and it does not change your Claude sessions. Then the chat's preferences (in a group only an admin can change them; in your own private chat you can, whatever your role, the same as `/settings` there) and, for an admin, **Updates**, which mirrors [`/update`](#update). For the owner in their private chat, **Report a problem** opens its own page: write a note, check the report, send it and see its reference without leaving the app (see [problem reports](problem-reports.md#how-to-send-one)).

The app follows Telegram's light or dark theme as you switch it, and stops its gentle animations when your phone asks for reduced motion. Everything in the Mini App is also reachable from chat: the ⋮ menu on a session's dashboard carries the same actions.

### Switching a running session's model

A live session's page has a **Model** control showing the model the session reports (from Claude Code's statusline) and the same list the launch picker and the chat's Models keyboard offer. Picking one types exactly `/model <name>` into that session, the same injection the chat's Models keyboard uses, with the same rule on who may do it (anyone who can prompt the session). The control reads **switching…** until the statusline reports a different model, then shows it; if nothing changes within 15 seconds it reads **not confirmed (check the session)**.

- **Only while the session is idle, not while Claude is working or a
  prompt is open.**
  Claude Code runs `/model` straight away, even mid-turn, rather than
  after the turn, so the switch is refused until the turn ends. The chat
  keyboard refuses in the same states, with the same words.
- **Claude Code's "Switch model?" question.** Claude Code usually asks
  this before a switch, because the next reply has to re-read the whole
  conversation. With Claude Code 2.1.251 or later, aipager's
  `PreModelSwitch` hook approves the switch aipager just typed, so the
  question is not asked (see [hooks](hooks.md#premodelswitch)). Every
  other switch still asks. With an older Claude Code, or on a switch to
  or from `opusplan`, the question can still appear. The switch then
  shows as *not confirmed* and the question waits in the terminal.
  Answer it there before you send the session anything else, or your
  next message would be typed into it. For the same reason, aipager
  refuses another switch of that session for a minute after an
  unconfirmed one, or until the session reports a new model.

## The pinned status bar

Each chat aipager talks in (your DM, and every group scope) gets one
pinned message: the status bar. Telegram's bar at the top of the chat
shows the message from its **first line** on, with the lines run
together, so that line says what most needs you:

| First line | When |
|---|---|
| `⏳ jim needs you - Bash: make deploy` | a session is waiting on a permission prompt, a question or an interactive prompt; `(+2 more)` when others are waiting too |
| `⚙️ jim (working)` / `💤 jim (idle)` | the chat has one live session and it is working / idle (`🔄 jim (starting)` while it starts): this line is the whole bar, apart from a flood line |
| `⚙️ 2 working · 1 idle` | several sessions, none waiting: how many are in each state (`working`, `idle`, `starting`); `🔄` when none is working and one is starting, `💤` when all are idle |
| `💤 all idle` | no live session |

Below it, only while it applies, a flood line - `🐢 slow mode after a
Telegram warning` (the six hours after a 429) or `⏸ card updates paused
(hourly limit)` / `(rate limit)` (minimal mode, see
[troubleshooting](troubleshooting.md#the-hourly-budget)) - and then,
when the chat has more than one live session, `✍️ Messages go to jim`
(the session a plain message in this chat goes to, see below; not in a
group, where each person has their own) and one line
per session with its state: `working`, `needs you`, `idle` or `starting`. The bar names
each session once: the waiting session the first line names gets no
line of its own, and the first line never lists names the lines below
repeat.
A session with agents still running in the background says how many on
its own line (or on the one line, with a single session):
`💤 jim (idle, 1 agent running)`. Commands Claude runs in the background
are counted the same way: `(idle, 1 shell running)`, or
`(idle, 2 agents, 1 shell running)` with both. It is a count, never
their names, so the bar moves only when the count does.
Tap the bar to jump to the message.

**Where a plain message goes.** A message that is not a reply goes to the
session this chat last talked with: the one you last switched to with
`/<label>`, sent a message to, started, or that last answered you here.
A bare `/mode`, `/clearqueue`, `/diff` or `/now` acts on the same session
(`/stop` stops whichever session is working). Each chat has its own: a message in one chat never goes to a
session that belongs to another chat. In a group only a reply to the bot, a message that mentions it, a command
or a tap on the group's keyboard counts (see [groups](groups.md#what-the-bot-reads-in-a-group)), and each person
has their own target, which a session's answer does not move (see
[groups](groups.md#talking-to-a-session-in-a-group)).

Buttons on the pinned message:

- **Answer &lt;label&gt;** (one per waiting session, three at most) sends
  that session's prompt again, with its answer buttons, at the bottom of
  the chat, so you can answer it without scrolling. If it was answered in
  the meantime you get an "already answered" toast instead, and a copy
  (or the original prompt) tapped after its prompt was answered elsewhere
  is refused the same way - it never answers a later prompt. After a
  daemon restart, the prompt that was waiting keeps working from the
  busy card or from its own message, but aipager no longer knows which
  prompt an older copy showed, so a tap on a copy the bar re-sent before
  the restart is refused with "this prompt has expired". Anyone who
  may answer the prompt may use it; nobody else.
- **📱 App** opens the Mini App, in your DM, while the Mini App is up.

The bar carries no clock, cost, context % or model, so it changes only
when a session's state does. aipager edits it (silently, no
notification) only when what it shows changed, at most once every 30 s
per chat (`PINNED_MIN_EDIT_GAP`); a change inside that gap is shown when
the gap ends, never lost. It is never edited while the chat is
flood-muted, and catches up when the mute lifts.

In a group the bot needs admin rights to pin. If the pin is refused,
aipager deletes the status message it just sent (so it never sits
unpinned in the group's history) and shows no bar in that group until
the daemon restarts; the same goes for a chat that refuses the bot
altogether (kicked, blocked). If you delete the pinned message, aipager sends and
pins a new one, at most once an hour per chat.

## Persistent keyboard

A persistent keyboard sits below the chat input. Rows, top to bottom:

| Row | Buttons | Notes |
|---|---|---|
| Sessions | one button per live session label | auto-built from the registry |
| Actions | `status`, `stop`, `new` | plain-text shortcuts (`kill` still works typed; ending a session is in `/status` → `⋮`) |
| Nav | `Templates`, `Commands` | The Mini App opens from the menu button next to the message box or `/app`, not from this keyboard |

`Model ›` lives inside the Commands submenu. Tapping a submenu
entry sends a canned prompt or slash command:

- **Templates** - bulk prompts you find yourself typing repeatedly,
  e.g. `Write tests for the changes`, `Explain your plan before
  making changes`, `Update CLAUDE.md with what you learned`.
- **Commands** - slash commands claude code natively handles
  (`/compact`, `/clear`, etc.), injected instantly. A command or
  template names the session it went to (`🧹 /clear sent to x1`).
  Clear is refused while the session works (`⚠️ Can't clear while
  session is busy`).
- **Models** - quick model switches for the active session. There
  are the family aliases (`sonnet`, `opus`, `haiku`, `fable`,
  `opusplan`), which always mean the latest model in that family, and
  pinned models (`claude-opus-5-5`, `claude-opus-5-5[1m]` with the 1M
  context window, `claude-sonnet-5-5`, `claude-fable-5-1`,
  `claude-haiku-5-5`). The Mini App's launch and session pickers use
  the same list. A switch is refused while the session is working or a
  prompt is open. The `🔄` reply then changes to show the model the
  session reports, or says the switch was not confirmed (see
  [Switching a running session's model](#switching-a-running-sessions-model)).

Override the default layout by writing
`~/.config/aipager/keyboard.json`:

```json
{
  "templates": [{"label": "Deploy",  "prompt": "Deploy to staging"}],
  "commands":  [{"label": "Compact", "send": "/compact"}],
  "models":    [{"label": "Sonnet",  "send": "/model sonnet"}]
}
```

Each section is independent - missing sections fall through to the
built-in defaults so you can override one without specifying the
others. Malformed JSON fails open with a logged warning. Changes
require a daemon restart.

## Per-message inline buttons

Most bot replies carry context-specific buttons:

### Permission prompts

When claude asks to run a tool that needs approval, the busy message
becomes a permission prompt:

```
🔐 Bash: List the temp directory
ls -la /tmp

  [✅ Allow]  [❌ Deny]
  [🟢 Allow always]  [⏹ Stop]
```

The card shows the real command (or file path) claude is asking to
run, under its own description of it - approve what you can read. A
character that would change the order the text is drawn in (a Unicode
direction control such as U+202E, which can make a command look like a
different one) is shown as a marker like `⟨U+202E⟩`, and the card then
adds `⚠️ This contains hidden direction characters, shown as ⟨U+...⟩. It
may not run what it looks like.`

- **Allow** - approve this one call.
- **Deny** - refuse it; claude blocks the tool call.
- **Allow always** - approve and add the standing rule claude offers
  ("don't ask again for …"). The button appears **only when claude
  offers such a rule**; for a command it cannot derive one for (most
  compound commands) the card carries Allow / Deny / Stop instead - as
  it does for a read-only file access (`Read`, `Grep`, `Glob`) outside
  the session's working directory.
  Claude Code 2.1.259+ puts a "switch to auto mode" row in that slot of
  its own Bash dialog, a "block reads outside the working directories
  from now on" row in the outside-read one, and - for a Write/Edit it
  can't derive a per-file rule for - a permission-mode switch such as
  auto-accepting all file edits (`acceptEdits`); aipager never selects
  any of these; change modes deliberately with `/mode`.
- **Stop** - interrupt the turn instead of answering.

The answer goes to Claude Code through aipager's permission hook, which
waits up to 20 seconds for it. A prompt that came before the busy
message (it is then sent as its own message, with Allow and Deny only)
is answered the same way.
Once that wait is over, the buttons type the answer into Claude Code's
own dialog instead; Deny then picks the dialog's last row ("No, and tell
Claude what to do differently"), which ends Claude's turn like Stop, and
the card says `🚫 jim · Denied`.

Every tap is recorded in `~/.claude/aipager-audit.jsonl` and mirrored
as a one-line reply threaded under the busy message:
`✅ jim · Allowed · @alice · Bash: List the temp directory` (the call's
description, or its command when it has none; with a chat set up by `aipager config`; `Allowed` alone in personal mode).

While a prompt - or an AskUserQuestion - waits for you, the session
shows as waiting, never idle. If Claude Code nudges about idle input
during that wait, the chat gets `⬆️ jim · still waiting for your answer
above` as a reply to the prompt, once per wait.

`AskUserQuestion` dialogs render the same way, with one button per
option (the first four), and for a question that allows several answers
⬜/☑ toggles and a `✅ Submit` button. The question and each option's
description are shown whole, as in the terminal. Only a form too long for
one Telegram message is shortened: the busy card first shows fewer of its
tool rows, then the longest of the question and the descriptions end in
`…` at a word.

**More than one prompt.** Claude Code can wait on two prompts at once:
a background agent asks for permission while Claude asks you a question,
or the other way round. aipager shows each of them: the first on the
busy card (or as its own message), each later one as its own message
under the card, with its own buttons. The session shows as waiting until
the last one is answered, and a message you send meanwhile is held until
then. Answer them in any order. Allow and Deny go through the permission
hook while it waits, which reaches that very prompt wherever it is. A tap
that has to type keys (a question's option, or Allow and Deny once the
hook stopped waiting) types them only into the prompt Claude Code shows
first: Claude Code shows one dialog at a time, and keys reach the one on
top. Otherwise the tap types nothing, keeps its buttons and says
`Claude Code is showing another prompt first, so answer that one before
this one.`, or, when aipager can't tell the order (an agent it did not
see start, for example), `aipager can't tell which prompt Claude Code is
showing first, so answer this one in the terminal.` When the card's own
prompt is answered first, the card shows Waiting with Stop only, and the
other prompts keep their buttons. If Claude ends its turn while an
agent's prompt is still open, the answer comes as usual and the session
keeps waiting on that prompt, sent again as its own message. A restart
with two or more prompts open brings none of them back: their buttons
answer `already answered` and type nothing, and the prompts can still be
answered in the terminal. One open prompt comes back as before.

At 80% context the chat gets `⚠️ name · Context at 82% - auto-compact
soon` with **📦 Compact Now**, which sends `/compact` to that session. It
comes once per conversation: compacting does not bring it back, and a new
conversation (after `/clear`, for example) gets it again.

### Stale buttons

Buttons that act on a running turn (Stop, End, Restart, the `/mode`
switch and its Stop-and-switch, `/new`'s Replace) are tied to the task
they were shown for. One left over from an earlier task changes nothing,
instead of acting on whatever is running now, and so does one shown
before the session ended and was resumed or restarted. End, Restart, the
`/mode` switch, and the Stop in `/status` or the `/stop` picker answer
`x1 moved on to new work - …` with what to tap or send again (or `That
session has ended.`); the busy card's Stop answers `That task already
finished - …`, and `/mode`'s Stop-and-switch turns its card back into
the `/mode` card under `⚠️ x1 moved on to new work, so nothing
changed.`
`/new`'s Replace says `x1 started working since. Send /new again to
replace it.` when the session started a turn after its card was shown.

A prompt's buttons are tied to that prompt. Once it is answered, in
Telegram or in the terminal, they answer `already answered` and type
nothing, also while another prompt of the session is still open.

A `⚡ Send now` button acts only on the message it sits under: if
Claude has already taken that message, the tap answers `Already taken`,
sends nothing and removes the queued lines.

### Idle responses

Once a turn ends, the answer arrives as your message layout says (see
below and [Settings](#settings)). If Claude's answer is longer than one
message can hold (about 32 KB), the message shows as much as fits under
a `📎 Full response attached below ↓` note, and the whole turn (its
steps and the full answer) follows as `<name>_full_log.md`, which
Telegram shows without another app. The same file is attached when the
busy card had to leave rows out.

A turn that ends on an Anthropic API error (credit, overload, rate
limit, network) shows `⚠️ name · <what went wrong>` instead of an
answer, with **🔄 Retry**, which re-sends the last prompt to the same
session. While the chat is
[flood-muted](troubleshooting.md#the-bot-went-quiet-flood-control) a tap
does nothing at all - the prompt is not re-sent and the button stays -
so tap it again once the ban has lifted.

In the card layout ("Busy card + result") the finished card always stays above the answer, for every turn, as the record of how it was reached: its tool rows, agent rows and what Claude said between them, or just `✅ name · Done · Ns` for a turn that ran no tools. The answer arrives as its own (notifying) message. To have one message per turn instead, choose "Merged into busy message" (the answer goes into the card) or "Replace with result" (the card is removed and the answer stands alone).

Each turn has exactly one busy card. A message you send while a turn
runs never adds a second card for that turn, and a card whose turn has
ended no longer keeps its Stop button: if nothing closed it (a lost
Stop hook, a restart), aipager closes it after 30 seconds, as your
layout says. After a daemon restart, a card whose turn is still running
is kept and keeps ticking right away, its time counting on from when the
turn started, and the session shows as working until the turn ends; any
other card left from before the restart is closed ("Daemon restarted",
Stop removed). A session that was waiting on a permission prompt or on a
single question from Claude comes back waiting, not working: its card
and the pinned bar say it needs you and the prompt's buttons still
answer it, unless the transcript shows it was answered in the terminal
meanwhile. Questions with several parts or several choices, and prompts
asked by a subagent, are left to the terminal. Once a prompt is known
to be over (answered by a tap or in the terminal, its turn ended, or not
brought back by a restart because it was answered meanwhile or could not
be saved and read back), its old answer buttons answer "already
answered" and type nothing, after a restart too. A prompt nobody
answers stays open for as long as Claude waits on it. aipager stops
waiting on it only when Claude sends no news for 5 minutes and the
saved conversation reads as past the prompt (the news of an answer
lost); such a prompt is not known to be over, so its buttons still
answer the dialog. That news is Claude's own and the asking agent's: a
background agent working on its own does not count, and the saved
conversation says nothing about an agent's prompt, which waits for that
agent's own news.

A turn Claude starts **by itself** - a background agent reporting back
with a `<task-notification>` when no job is open - gets its busy card
only once it does something: at its first tool call, or after 15 s,
whichever comes first. Most such wake-ups are a few seconds of "nothing
new"; those now show just the answer.
The "typing…" indicator still shows while it runs, and the answer itself
is always delivered - nothing is filtered as trivial.
Turns you start, from Telegram or the terminal, still get their card at
once.

While a session is busy, each background agent Claude launches (via
Claude Code's `Agent` tool, `Task` in older versions) gets its own line on the busy card: `🤖 <type> · <activity> ·
<elapsed>`, showing the agent's type and what it's currently doing,
refreshed as its own tool calls come in. Once that agent has made three
or more tool calls, they fold into their own `▸ N tool calls` tap
directly beneath its row - never appearing in the parent's timeline or
its `Bash ×N` tallies. When the agent finishes, its row settles to `✅
🤖 <type> · N tool calls · <elapsed>` and keeps the same tap. An agent
started by another agent (a helper) names the agent at the top of its
chain before its own type, `🤖 pipeline-runner › ship-tester`, when
aipager knows who started it at the moment it starts; the rows stay in
the order the agents started. The full
play-by-play `.md` attachment above gains an Agents section listing
every agent that ran the turn, its elapsed time, tool count, and the
tools it called.

While background agents are still running after their turn ended, the
card stays up as the job's status. When Claude takes a new message
meanwhile, the live card is the one under that message, still showing
the running agents. A message you send to the idle session starts a new
turn: the earlier turn's card is settled where it stands, its steps
kept, and the new turn gets its own card. A message that was queued and
is picked up continues the job's turn: its card is re-sent under that
message and the old copy deleted. Tool calls
made inside an agent never show as the parent turn's own rows, and never
make aipager think a new turn started.

A command Claude runs in the background (`run_in_background`, Ctrl+B,
or one Claude Code moves there when it times out) gets a row of its own
while it runs: `⏳ shell: <description> (3:12)`, named by the call's
description or else the first line of the command. When it ends the row
settles to `✅ shell: <description> - done (6:12)`, `❌ ... - failed (exit
1)`, or `⏹ ... - stopped (6:12)` (Claude stopped it with TaskStop, or
Claude Code stopped it under memory pressure); failed and stopped rows
keep that mark on the finished card. A turn that ends while such a
command still runs is not finished: the card waits as the job's status
(`🔄 name · 1 shell still working · 4:10`, or `1 agent (ship-reviewer), 1
shell still working` with an agent too), and when the command ends
Claude's follow-up continues on that same card. Commands started inside
an agent are that agent's and are not shown. A command whose end
aipager never sees (a restart, a lost event) stops counting after two
hours (`AIPAGER_BG_SHELL_MAX_TRACK`, in seconds): its row reads
`⏹ ... - no end seen`, and a job that was waiting only on it closes with
`⚠️ name · Finished (no end seen for a background shell after 2:00:04)`.

Once a turn's timeline grows long, each older run of tool calls (three
or more in a row, and not the run currently in progress) folds into its
own `▸ N tool calls` tap right where it happened, instead of piling up
in full or being cut off by Telegram's own message-length limit - tap
any one to read it in place. Commentary never folds; the newest activity
and the status line are always visible without tapping anything. A
still-running (or just-settled) background agent's own row is never
folded into a tap itself, only its tool calls, and never while it's the
one thing standing between the timeline and the ceiling. Only if the
timeline is so large that even every fold together still can't fit does
content get genuinely dropped from the card - in that case the `.md`
attachment above carries the complete record.

#### Agents still running when the answer goes out

Claude Code can run agents in the background, and the turn that launched
them can end while they work. The answer then ends with a divider line
and a short status of that background work, in the style of Claude
Code's own agent panel, kept up to date while it runs. Everything under
the divider is small text (Telegram's footer), so the answer itself
stands out:

```
⏳ 5 agents working · 🖥 1 shell running

○ 🤖 pipeline-runner · Writing final.md for the pipeline

- 🤖 ship-tester · Write and run criteria tests
- 🤖 ship-developer · Editing state.py
- 🤖 ship-developer › ship-reviewer · Collect line numbers

‖ 🤖 r2-runner · waiting for its own agents

Results from the agents will follow here.
```

- **The first line counts.** Agents working (or done), and the background
  commands (shells) of this job running, finished or stopped. A shell is
  only ever a count, never its command.
- **One row per agent,** a 🤖 and its type in bold, then what it is
  doing now: its own latest line of text (Claude's words, shown as
  written, never translated), else its latest tool as a short phrase
  that names what it works on (`Reading state.py`, `Writing final.md`,
  `Searching for …`, or a command's own description), else the task
  Claude gave the agent when it launched it, else its latest tool alone
  (`Running a command`, `Using TodoWrite`), else just the type. So two
  agents of one type running commands without a description still show
  their own tasks. An agent that has stopped to wait for
  agents it started itself says `‖ … waiting for its own agents`
  instead.
- **Agents as a tree.** The agents the session started are the first
  level. Every agent one of them started, at any depth, is listed right
  under it, each right after the agent that started it; one started by
  an agent of the second level names that agent first (`ship-developer ›
  ship-reviewer`). The busy card names the same agent after the agent
  at the top instead (`pipeline-runner › ship-reviewer`). There are
  never more than two levels. A working agent on the second level has no
  ○ of its own: Telegram draws a bullet there. An agent whose starter
  aipager does not know yet is shown on the first level, never under a
  guess, and moves under its starter once that is known.
- **At most six rows,** the agents still working first, then `+N more`
  for the agents without a row. An agent never shows without the agents
  above it.
- **The promise** "Results from the agents will follow here." stays only
  while an agent is still working. Shells never carry it: a dev server
  may never end.

Each agent's row settles on its own when that agent stops, to `✓ 🤖
Explore · done (0:42)` (how long that agent ran), and the first line
counts down. An agent that a listed agent starts after the answer went
out joins the status under it at the next update; one the session
itself starts later is listed under a later answer instead. Once every
agent is done, the rows give way to one line, with the time from the
answer to the last agent's end:

```
✅ 2 agents done (6:12) · 🖥 1 shell running
```

and when that shell ends, `✅ 2 agents done (6:12) · 1 shell finished
(2:13:05)` (how long the shell ran; `stopped` when it was killed or
stopped by Claude, `finished` when it ended on its own, failing or not;
its card row says how). An answer with only a shell of its job shows
`🖥 1 shell running`, which settles to `1 shell finished (…)` or `1 shell
stopped (…)`.

Only the shells of the answer's own job are counted: a dev server an
earlier task left running is not repeated under every answer (the
pinned bar still counts every shell). `/stop` ends the job, but Escape
does not end a background command: one that is still running stays in
the pinned bar until it ends, and is no longer counted under later
answers; its later follow-up then starts a turn of its own.

That answer goes out the moment the turn ends, as a normal (notifying)
message threaded to your prompt - the same text the terminal shows. The
busy card stays above it as the job's live status, in every layout:
`🔄 name · 1 agent (general-purpose) still working · 1:18`, with its
**Stop** button. When the agents report back, Claude's answer to that
arrives as a new message of its own; the earlier answer is never sent
again, a daemon restart included. A job whose agents report back more
than once produces one answer per report, each sent as it is written.

An agent that stopped while background work of its own is still running
counts as working, since it resumes later. An agent that started an
agent of its own and then stopped to wait for it is recognised from that
start (its own call that launched the other agent): it stays on the
status as working, and keeps the job open, until it stops with none of
its own agents running. If it has not resumed two minutes after the last
of them ended, it is no longer counted as running and the status is left
as last shown. Claude's `<task-notification>` for an agent says the same
in a note; aipager reads it when the notification starts a turn, and
also when Claude takes it in the middle of a running turn while the busy
card is up. A row waits a few seconds before it shows done, so that note
can take it back first. The status is added in every layout, in the
chat's language.

**How it is kept up to date.** aipager edits the answer silently, only
when what the status shows has changed, and at most once every 30
seconds per answer (the answer itself counts as the first). Each answer
with a status is kept up to date this way (up to five pending per
session). The edits are low priority: one the flood budget refuses, or
that would come while the chat is flood-muted, is not made, and the
status waits for its next change. The final edit, once everything has
ended, is tried once more after the mute lifts or the budget refuses
it, and is then dropped. No edit is made into a deleted answer, for a
session that has ended or been killed, or when it would make the answer
too long for Telegram (room for the status is kept when a long answer
is cut).

The status is left as last shown, never turned into done, when aipager
cannot know the work finished:

- an agent aipager hears nothing from for 30 minutes
  (`AIPAGER_SUBAGENT_SILENCE`) is no longer counted as running, but
  silence is not completion;
- a background command running longer than two hours
  (`AIPAGER_BG_SHELL_MAX_TRACK`) with no end seen is no longer counted;
- a daemon restart forgets which answers are pending.

A daemon restart keeps the agents that are still running, those waiting
for their own agents included: an answer sent after the restart lists
them, a busy card the restart takes over shows them again, and their
later ends are matched as usual. Agents saved more than 30 minutes before
the restart are not brought back.

An answer held back by a flood mute, or delivered as plain text after
the formatted send failed, goes out without the status, as does a turn
that ends with no answer text (only a header).

### End confirmation

`/kill`, its picker and `⋮` → `⏹ End session` always confirm:

```
⏹ End jim? Claude stops and the session closes.

  [⏹ End]  [Cancel]
```

After `⏹ End` the message says `⏹ Ended jim` above this chat's remaining
sessions. A confirm tapped after the session started new work is refused
(`jim moved on to new work - …`), and so is one for a session of the same
name started since.

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

`🔄 Restart daemon now`:

- A daemon running as the systemd-user service schedules a detached
  `systemctl --user restart aipager.service` 5 s later (a timer accurate to 1 s), in a transient
  unit outside the daemon's own cgroup, so it survives the daemon's
  exit. It refuses while the service unit would kill your sessions
  (`KillMode` other than `process` or `none`), and tells you to run
  `aipager service install` first.
- macOS, when the launchd agent is installed
  (`~/Library/LaunchAgents/com.aipager.daemon.plist`): `launchctl
  kickstart -k gui/<uid>/com.aipager.daemon`, even for a daemon you
  started yourself.
- Otherwise a daemon you started yourself (`aipager start`), on Linux
  even when the service is also installed: spawn a detached replacement
  that waits for the parent PID to die, then runs the same command the
  daemon was started with (its output goes to the self-restart log). The
  current daemon stops cleanly once the spawn is alive.

No SSH required. To finish an aipager update, the update card's **🔄 Restart now** restarts aipager in place instead, in the same terminal (see [Update](#update)).

## Update

`/update` (admin only; in personal mode, only the operator) replies with one button, **🔄 Check for updates**. Nothing is looked up until you tap it. The tap edits that message into one line per product:

- `aipager 1.0.1 → 1.1.0` when a newer version exists (the latest on PyPI);
- `aipager 1.0.1 (up to date)` when it does not;
- `Claude Code 2.1.282 (couldn't check)` when the lookup failed (network down, 5 s timeout, or `claude` not found).

Claude Code is compared with the latest on its own update channel (`autoUpdatesChannel`: latest, stable or rc). Below the lines, in small text, is how aipager was installed (e.g. `pipx, from PyPI` or `pipx, from local path …`; group chats never show paths).

If anything is newer, there is ONE button that updates only the products that have an update: **Update aipager**, **Update Claude Code** or **Update both**, plus **Cancel**. When aipager is offered, the message also says how the restart happens: `Restart: automatic, once no turn is running.`, `Restart: tap Restart now once it is installed.` (a daemon without a service, see below) or `Restart: manual (reason)`.

If a newer aipager is already installed but the running daemon is still the old one (an update that finished without a restart, or a `pip install -U` by hand), the line reads `aipager 1.1.0 is installed and waiting for a restart (running 1.0.1).` and the message offers **🔄 Restart now** instead of an update: Update aipager is never offered for a version that only needs a restart. The installed version is checked by starting the interpreter the daemon would restart into, so a version that is installed but does not load says so (`aipager 1.1.0 is installed but does not load. Reinstall it with …`) and offers nothing. A newer release than the installed one is still offered as an update. A newer aipager on an install that cannot be updated from here (editable, Nix, Snap, a system package, a container, or another user's install) is shown with the reason and is not offered. The same goes for a newer Claude Code installed with npm into a folder your user can't write to (for example `sudo npm install -g` under `/usr/lib/node_modules`), where `claude update` could only fail: the line says `can't update from here: installed with npm into a folder your user can't write to; run "claude install" once to switch to Claude Code's own installer, then restart aipager`. If nothing can be updated, the message reads "Everything is up to date.", says a check failed, or, when something newer exists but can't be installed from here, "An update is out, but it can't be installed from here.", with a **Check again** button and no update button.

The Update button starts only what the check offered. If the check is more than 10 minutes old, another update has run since, or the versions no longer match the button, it asks you to check again instead. Buttons from an older `/update` menu (the per-product **Update Claude Code** / **Update aipager** / **Both**) answer "This menu is out of date, send /update again" and do nothing. Every tap re-checks the admin rule.

Updating Claude Code runs `claude update` (by absolute path, 5 min
timeout) and reports `Claude Code A → B`, "already up to date", or the
failure with the tail of its output. Running sessions keep the old
version until you restart them (`/restart`); the reply lists them. No
session is restarted for you. New sessions use the new version.

Updating aipager (**Update aipager**, or the second half of **Update both**, which updates Claude Code first):

1. On a PyPI install that is already current, it says so and stops.
2. If the daemon can restart itself, it first **waits until nothing is
   in flight**: no session running a turn, waiting on a question,
   running a background agent, showing a live busy card, running a
   tool, or holding an open permission prompt, and no held answers
   waiting for a rate limit to lift. The message lists what it is
   waiting for, with **Restart now** (skip the wait) and **Cancel**.
   After 10 minutes it asks again: **Wait 10 more min**, **Restart
   now**, **Cancel**. Unanswered for an hour, it cancels itself.
3. It upgrades through the installer that owns the running daemon
   (`pipx upgrade aipager`, `uv tool upgrade aipager`,
   `brew upgrade aipager`, or `<venv>/bin/python -m pip install
   --upgrade aipager`), by absolute path, with a 10 min timeout. There
   is no Cancel while the installer runs.
4. It checks the new version imports in a fresh interpreter. A failed,
   timed-out or unimportable upgrade restarts nothing. A timed-out
   upgrade was stopped part-way, so the message warns the install may be
   partial and gives the reinstall command to run before the next
   restart. Installer output is shown only in a private chat; a group
   gets "output in the daemon log".
5. If a turn started during the upgrade, it waits again.
6. It schedules a detached `systemctl --user restart aipager.service` 5 s later (a timer accurate to 1 s), and the status message reads `⏳ aipager A → B installed, restarting…`. While step 5 waits, it reads `⏳ aipager A → B installed, restarting when the current turn ends`. After the restart, the new daemon edits that same message into `✅ aipager updated A → B, N sessions re-adopted` (and `⚠️ Not back: …` for any session that did not come back). If the message was deleted or can no longer be edited, it posts the outcome as a new message to the chat that asked.

In the Mini App, **Settings → Updates** shows "Restarting aipager…" with a turning lantern until the new daemon answers, then "Updated to B" with the re-adopted count. It never shows a countdown. After about 2 minutes without an answer (never before the old daemon could have reported a restart that did not happen) it says "Still restarting, reopen the app in a moment." With aipager's managed tunnel, an update's restart keeps the app's address (the new daemon takes the tunnel over), so the open page rides through and shows "Updated to B". If the tunnel could not be kept after all, the page says "Still restarting, reopen the app in a moment." after about 2 minutes. An update started from an aipager without this (or with `MINIAPP_KEEP_TUNNEL=0`) still changes the address: once the old daemon stops answering, the open page says "aipager restarted, reopen the app once the chat says it is updated"; close it and open it again from the chat's menu button or `/app`. The app's sign-in lasts 5 minutes, so a page opened longer ago says "Reopen the app to see the update's result." instead.

The daemon restarts itself after the update only when it runs as the
systemd-user service **and** that unit has `KillMode=process` (or
`none`). With an unsafe `KillMode` aipager is still upgraded, and the
message says `Restart needed: …`: run `aipager service install` first
(it lists the sessions a restart would kill).

**Restart now, without a service.** A daemon no service manager restarts (`aipager start` in a terminal or
tmux, Docker, or the launchd agent on macOS) installs the update and
then shows `Tap Restart now to run it (aipager waits until no turn is
running).` with a **🔄 Restart now** button. Tapping it (admin only, as
every update button):

1. checks the installed aipager loads (`⏳ Checking the installed
   aipager…`); a broken install restarts nothing, and the same version
   says there is nothing to restart;
2. waits, like the systemd restart, until nothing is in flight, with
   **Restart now** (skip the wait) and **Cancel**;
3. checks the install again, then says `⏳ aipager A → B installed,
   restarting…` and stops the daemon the clean way (as Ctrl+C does);
4. starts the new aipager in place: the same process, the same command,
   the same environment and folder, in the same terminal or tmux window
   (and, in Docker, the same container process). Sessions keep running.
   The new daemon edits the message into `✅ aipager updated A → B, N
   sessions re-adopted`.

Taps sent during the few seconds of the restart are dropped, as with any
restart. A Ctrl+C or `docker stop` during the restart wins: aipager stops
and does not start again. If the new aipager cannot be started in place,
a detached copy starts it once the old one has gone (its output goes to
the self-restart log, `aipager.log` in the instance folder or
`/tmp/aipager.log`, and the daemon log names the command to run should
that copy not come back); if even that fails, or the interpreter itself
is gone, aipager exits with code 1 and the daemon log says
`restart.failed:` with the command to run.

When aipager cannot restart itself at all, the message gives the exact
steps for your setup instead of a button:

- a terminal or tmux: `In the terminal or tmux window where aipager runs,
  press Ctrl+C, then run aipager start.`
- macOS: `On the Mac, run launchctl kickstart -k
  gui/<uid>/com.aipager.daemon.`
- Docker: `On the Docker host, run docker restart <container>.` (the
  container's short id when its host name is one), with a reminder that
  recreating the container from its image brings back the aipager
  version in that image.

**Restart now** also appears on the result of **Check for updates** when
a newer aipager is installed and waiting, and on a one-time notice: when
Claude Code's hooks (which start fresh on every event, from the
installed aipager) already run a newer aipager than the daemon, the owner
gets one silent message per newer version and daemon run, `⚠️ The
aipager update is only half done: …`, with the button. It waits two
minutes from when the hooks first ran the newer version (a restart right
after an install never shows it), then until no turn is running in the
owner's private chat. After the restart into that version, the notice is
edited into `✅ The aipager update is finished: aipager runs B now.`,
without the button. In the Mini App,
**Settings → Updates** shows the same state as a line above the card,
with **Restart now** (it asks first).

In the Mini App, **Updates** is the first card on the Settings tab: its
name, the running version, and a small **Check for updates** at the end
of the row. Opening the Settings tab checks by itself (at most once every
ten minutes, never while an update runs or aipager restarts; a check that
fails there says nothing), and a tap always checks. The card stays one
quiet row until there is something to do: a newer version brings its
lines and the one Update button, "Everything is up to date." is a single
line with no button, and a running update, a restart or **Restart now**
keep their own buttons.

Only one update runs at a time, across `/update`, the Mini App's
**Settings → Updates** card (the same check, the same one Update button, the same job) and
`aipager update` on the command line. That includes the seconds between
"installed, restarting…" and the restart itself: `/update` answers that
aipager is about to restart, the Mini App offers no buttons, and the
voice extra's **Restart daemon now** refuses while an update runs or
waits to restart. If the daemon is still alive two minutes after
scheduling its restart, it stops the pending restart timer, frees the
update lock, and tells you to restart it yourself.

If the daemon shuts down while an installer is running, the installer
gets 3 s to finish and is then stopped (it would otherwise keep writing
the install while the next daemon starts). The status message and,
after the restart, a new message say the install may be partial and how
to repair it.

Once a shutdown has begun, nothing is restarted and nothing new starts:
`/update` buttons answer that aipager is shutting down, the Mini App
answers 503, and no installer or version check is spawned. An installer
that finishes within its 3 s still counts. The status message says the new
version is installed and nothing was restarted, and the next start
announces `aipager updated A → B`. A daemon you stopped with
`aipager service stop` stays stopped. The update's part of the shutdown
takes at most 8 s in total.

## Free messages

### Text

Treated as the next prompt for the **active session**: this chat's
target, the session you last switched to, sent a message to, started, or
that last answered you here (see
[the pinned status bar](#the-pinned-status-bar)). In a group each person
has their own target, which a session's answer does not move (see
[groups](groups.md#talking-to-a-session-in-a-group)). Messages reach Claude **immediately**,
even while your own turn is running, exactly like typing into the terminal
(in a group, a message from someone else waits for that turn to end; see below).
Send several and they queue inside Claude itself, which picks each up
at a natural boundary:

The reaction on your message follows it, the way Claude Code's own
queued prompt turns from grey to white:

- 👀 - handed to the session (or held, see below), but Claude has not
  taken it yet. A message sent while a turn runs stays 👀 while it
  waits in Claude's queue.
- 👍 - Claude took it: it started a turn, or Claude folded it into the
  turn already running, or handed it to a running background agent.
- 🤷 - it will never be taken: a held message dropped by `/stop`,
  `/clearqueue` or `/kill`, or one that could not be sent on release; a
  message or command still waiting when `/stop`, `/clearqueue`, `/kill`
  or the session ending (not `/clear` or `/resume`) dropped it; or a
  prompt Claude Code refused (see below). Messages Claude had already
  queued are only marked while aipager can see that queue - the live
  transcript scan is running and no background job is waiting; otherwise
  they keep 👀. Escape in the
  terminal pulls Claude's queue back into its input box, where it may be
  sent again, so a message dropped that way keeps 👀 too.
- 👌 - a Claude Code command that has run (see below), or aipager
  acknowledging `/stop`.

A reaction only ever moves forward, so a message gets at most three,
each once. A reaction that falls inside a Telegram rate-limit ban is
skipped, not replayed later, and one teardown marks at most the ten
newest messages it drops.

Command buttons (`Compact`, `/model …`): tapped while the session is
idle, the command is 👌 at once - Claude Code runs it on Enter, and a
local command such as `/model` fires no hook that could say so later.
Tapped while a turn runs (not `/model`: it is refused until the turn ends,
see [switching a running session's model](#switching-a-running-sessions-model)),
it is 👀 until that turn ends - normally, as
a background job's interim stop, or on an API error - and then 👌;
dropped before that by `/stop`, `/clearqueue`, `/kill` or the session
ending, it never ran: 🤷. When Claude Code queues it as a prompt instead,
it follows the 👀 → 👍 lifecycle. A voice note gets the same reactions as
text once its transcript is sent.

The busy card and the eventual answer follow whichever message Claude
actually consumed for a turn - the one it started on if the session
was idle, or the one it absorbed into the turn already running - never
simply the last message you sent. Sending a follow-up mid-turn does
not "jump the reply" to itself: if Claude folds it into the answer
already forming, the card jumps to it the moment that happens; if
Claude instead finishes first and then picks it up, the first answer
stays under the first message and the follow-up gets its own turn,
with its own card and answer under it.

If Claude Code refuses a message outright - an unknown slash command,
or a built-in that only opens a dialog in the terminal - no hook fires,
so nothing would ever end the turn the daemon just announced. After
8 s without any hook (`PROMPT_HOOK_GRACE_SECONDS`) the busy card
becomes `⚠️ name · Not taken by Claude Code` with a one-line
explanation and the session is idle again; the reason is on the
terminal, and the message gets 🤷 (a slash command gets 👌 instead: a
built-in that opens a dialog fires no hook either). Only a message that
started a turn
is judged this way - one queued behind a running turn keeps its 👀
until Claude takes it or it is dropped.

Three cases are held back instead of sent, and delivered automatically
once resolved:

- A permission or question prompt is open, for as long as it stays
  open (there is no time limit): your text would otherwise be read as
  an answer to that dialog. Your first message then gets one reply,
  under the prompt, saying Claude is still waiting for your answer
  (later ones only get the 👀). Once you answer, on Telegram or in the
  terminal, the held messages go to Claude in the order you sent them
  and that reply is deleted. If the session ends with the prompt still
  open, they are dropped with a 🤷.
- (Team mode) a different person's message is still waiting to be
  picked up.
- (Team mode) a different person's turn is running, including a
  background agent it started. A turn typed in the terminal counts as
  the owner's, so only an owner's message (or, in a DM, its own
  member's) joins it.

The last two keep messages from different people out of one turn, so
each runs with its own sender's rights. If one joins anyway (for
example a sender aipager does not know, which is never held), the turn
runs under the strictest rules of everyone in it (see
[groups](groups.md#who-a-message-runs-as)). If the turn a message waits
for is stopped, or halted by a blocked tool, held messages are dropped
with a 🤷.

Held messages are capped at 50 per session and expire after 24 h;
`/clearqueue` drops them along with anything Claude is holding.

#### Send a queued message now

A message you send while Claude is working on your own turn goes to
Claude at once (aipager does not hold it back) and waits in Claude's queue until the
current step ends. As soon as Claude's queue shows it, aipager replies
under it:

```
⏳ Queued - Claude will read it after the current step
[⚡ Send now]
```

Every queued message gets its own line, and each line disappears, with
its 👍, the moment Claude takes that message. A message Claude takes
before its queue record is seen gets no line, only 👀 then 👍.

Tapping `⚡ Send now` (or sending `/now`) presses Claude Code's own
send-now keys (Ctrl+X, then Ctrl+S) in that session. Claude then reads
everything it has queued at once, not only the message you tapped.
What happens to the work in progress is Claude Code's choice: a running
command or agent keeps going in the background; if Claude is in the
middle of writing a reply, that reply is cut short and restarted with
your message. Any other kind of step (another tool, for example) may be
interrupted too, after a short grace period. In some cases (for
example with Claude Code's background tasks turned off) send-now falls
back to a plain interrupt of the running step. This is all Claude
Code's own send-now behaviour, not something aipager decides.

The tap answers `Sent to Claude now` and every line disappears at once,
since send-now hands Claude everything it holds; `/now` removes them the
same way, and no line comes for the messages that Send now sent (a
message you send after it gets its own). If Claude cancels the running
step instead of moving it to the background, it takes only the oldest
queued message straight away and reads the rest at its next step; the
answer goes under the message it took, and that message is not run a
second time. If the keys take longer than a moment to reach Claude (a
busy machine), the tap answers `Sending to Claude now` instead; the line
then disappears once they are in, or, if they could not be delivered,
stays and reads `⏳ Queued - could not reach Claude, tap Send now to try
again`, with its button, so you can tap again. The keys are only pressed
when Claude's transcript shows it still holding the message; otherwise
the tap answers `Already taken` and removes the line. It is refused,
with nothing pressed, while a permission or question prompt is open
(`Answer the open question first`), while Claude may have just opened
one that aipager has not shown yet (its events for a new turn are held
while the previous turn's answer is still being delivered: `Busy, try
again in a moment`), and while aipager is in the middle of typing
another message into the session (`Busy typing a prompt, try again in a
moment`). Anyone who may send messages to the session can use the button
and `/now`; a `read_only` member cannot.

The line disappears on its own when its message leaves Claude's queue:
Claude takes it (folded into the running turn, handed to a background
agent, or started as the next turn), Escape in the terminal pulls the
queue back into the input box, or it is dropped by `/stop`,
`/clearqueue`, `/kill`, `/restart`, a safety halt or the session
ending. A daemon restart removes any line still showing.

The lines never wait for the flood manager: they go out and are removed
straight away even on a busy or slow chat, and in minimal mode. Only
while Telegram itself has the chat muted is no line sent (`/now` still
works then), and if Telegram has just asked aipager to slow down, a line
waits the few seconds it asked for. Each waiting message costs one chat
call for its line; lines that go together are removed in one call.

### Files

Uploaded files are downloaded to a private folder of the chat they came
from, `/tmp/aipager-files/<chat>/` (named `<time>_<file name>`; `<chat>`
is a name made from the chat id and the bot token, which does not give
the chat id away). The folders are readable only by the user aipager runs
as (`0700`, files `0600`), and a member's turn in one chat cannot read,
search or write another chat's uploads (see
[security](security.md#team-mode-enforcement)). Files from before this
change stay where they were, directly in `/tmp/aipager-files/`; nothing
is moved or deleted, and aipager narrows the folder to `0700` when it
starts, so only that user can reach them. A new bot token gives every
chat a new folder; uploads from before it stay in the old ones, which a
member's turn can no longer read. A download folder that is a symlink, or belongs to
another user, is refused and the upload fails. The path is offered to
claude: with a caption, the prompt is the
caption followed by the path(s); without one it is just
`check this: <path>` (or `check these: <paths>` for an album), so
claude is pointed at the file without being told what to do with it.
A caption that starts with `/<label>` picks the session, exactly as
`/<label> <prompt>` does for text, and the label is dropped from the
prompt: `/api` alone sends `check this: <path>` to `api`, and
`/api compare these` sends `compare these <paths>` there. Only a
leading `/<label>` routes; a slash later in the caption is just text.
A label no session answers to is refused with `⚠️ Unknown session`
and nothing is sent.
The 20 MB Telegram bot file
download cap is enforced up-front; oversized files get a clear
rejection before any download attempt.
A download that hits a transient network error is tried up to three
times in all (waiting 1 s, then 2 s) before you see an error, and that
error names the file. An album - several photos or documents sent as
one message - is handed to claude as a single prompt (the caption,
then every file path in order) once its last item has landed; if one
item cannot be downloaded the rest still go out, with one note naming
the missing one.

### Files Claude sends

Claude can send you files from its session: ask in your own words ("send me the screenshot", "send me the image at ~/proj/chart.png"), or it sends one when it thinks you want it. It uses Claude Code's SendUserFile tool (Claude Code 2.1.301 or newer), which every session aipager starts gets: aipager starts it with `CLAUDE_CODE_ATTACHMENTS_BY_PATH=1` and tells Claude the tool is there. A session started before your aipager had this needs a restart to get it: `/restart`, a mode switch that restarts it, or Resume.

- The files arrive after the turn's answer, as replies to it (to your message when the turn has no answer), silently, in the session's own chat only. While the turn runs, the busy card shows `📎 Sending 2 files`.
- Each file goes by its content, not its name: an image as a photo when it fits Telegram's photo limits (at most 10 MB, width plus height at most 10,000, the long side at most 20 times the short one), else as a file; a GIF as an animation; an MP4 as a video; an MP3 or M4A as audio; anything else as a file. When Claude asks for a plain attachment, everything goes as a file.
- 2 to 10 photos and videos go as one album; files go with files and audio with audio; an animation goes on its own. More are split evenly (11 go as 6 and 5), and no album is over 50 MB.
- The caption is `💬 <session>`, with Claude's own caption on the next line of the first message, at most 1,024 characters.
- Every file is checked first, for everyone, the owner included ([security](security.md#files-claude-sends)): aipager's settings, Claude's credentials, keys and secret-looking files are never sent; a file must be a regular file, not empty and at most 50 MB; a member sends only files inside the chat's [root folder](groups.md#root-folders). If a file of a request is refused, nothing of that request is sent: Claude is told why and tells you, and the turn goes on.
- aipager sends a private copy it took when Claude Code read the file, kept in `~/.local/state/aipager/outbox/` and deleted once sent, or after a day.
- aipager sets no count limit of its own: sends are paced to Telegram's rate limits, an album counting one per file. While the chat is flood-muted, the files wait and follow the answer.
- A file that cannot be sent gets one line in the chat, for example `⚠️ Couldn't send report.pdf: Telegram refused it.` A file whose request aipager cannot find in Claude's own transcript gets no line ([security](security.md#files-claude-sends)). Every file sent, refused or failed has its own record in the [audit log](security.md#audit-log).

### Voice

Voice messages route through `faster-whisper` (the `aipager[voice]`
extra). The audio is transcribed locally, echoed back as `🎙️ Heard:
…`, and injected as if you had typed it, including as the name for
`/new` or a pending rename. A voice note over Telegram's 20 MB bot
download limit is refused. See
[hooks → UserPromptSubmit](hooks.md#userpromptsubmit) for what
happens next.

---

## Command line: every command

Run `aipager <command> --help` for the same list on your machine.

| Command | What it does |
|---|---|
| `aipager config` | The interactive setup wizard: bot token, chats, members and roles, Claude Code's hooks. Run it again to change anything (see [groups](groups.md#setup)). |
| `aipager setup` | The same first setup with no questions, for scripts and coding agents (see [below](#command-line-aipager-setup-for-coding-agents)). |
| `aipager start` | Run the daemon in the foreground. It refuses to start while another aipager daemon runs. |
| `aipager service install [--yes]` | Install the daemon as a background service (systemd user unit on Linux, launchd agent on macOS) and start it. `--yes` skips the question when the installed unit differs. |
| `aipager service start\|stop\|status\|logs\|uninstall` | Start, stop or show the service, follow its log, or remove it. |
| `aipager status [--json]` | The daemon and every session at a glance: state, model, context, cost, and Telegram rate-limit state. |
| `aipager logs [-f] [-n LINES]` | The daemon's log (journald on Linux, the launchd log on macOS): the last 100 lines, or `-n` lines; `-f` follows. |
| `aipager doctor [--fix] [--safety-check] [--json]` | Health checks (see [troubleshooting](troubleshooting.md#aipager-doctor-check-list)). `--fix` offers to fix the Claude credential and an ambiguous `claude` install, asking first; `--safety-check` prints the safety rules in force; `--json` is for scripts (see [below](#aipager-doctor---json)). |
| `aipager session <name> [claude args]` | Start a Claude Code session named `<name>` under dtach in the current folder, or attach to it if it runs. Arguments after the name (or after `--`) go to `claude` as they are, for example `--resume`. Needs a running daemon. |
| `aipager session ls [-a] [--json]` | List the sessions; `-a` includes ended ones. Also `aipager session list`. |
| `aipager session kill <name> [-y]` | End a session, after a question unless `-y`. |
| `aipager resume [name]` | Bring back an ended session with its conversation; without a name, a picker. |
| `aipager miniapp enable [--port PORT] [--url URL]` | Turn the Mini App on, on `127.0.0.1:PORT` (default 8765), behind the managed tunnel or your own `https://` URL. Takes effect at the next daemon restart. |
| `aipager miniapp disable\|status` | Turn it off (at the next restart), or show its settings and address. |
| `aipager policy validate` | Check `policy.yaml` (and `policy.d`); exits non-zero on a problem. |
| `aipager update` | Upgrade aipager through the installer that owns it (uv, pipx, Homebrew, pip). It never restarts the daemon; restart it yourself, or use `/update` in Telegram, which can (see [Update](#update)). |
| `aipager report [--note TEXT]` | Show a problem report and send it if you say yes (see [below](#command-line-aipager-report)). |
| `aipager uninstall [-y]` | Stop and remove aipager and its config, keeping its data (see [below](#command-line-aipager-uninstall)). |
| `aipager version`, `aipager --version` | Print the version. |
| `aipager help [command]` | Help for aipager or one command. |

## Command line: `aipager setup` (for coding agents)

The sections above describe the Telegram bot. This one describes a
command you run in a terminal, or that a script or your coding agent
runs for you: `aipager setup` sets aipager up with no prompts and gives
the same result as the first run of `aipager config`.

```sh
# 1. find the person's Telegram id (they open the bot and press Start)
aipager setup detect-chat --token-file ~/bot-token.txt --json
# 2. once they confirm it is them, set aipager up
aipager setup --token-file ~/bot-token.txt --chat-id 123456789 --service --json
# 3. check the install
aipager doctor --json
```

What it does, in this order: reads the bot token, checks it with
Telegram (getMe), checks everything on this machine without writing
(the existing config, the dependencies, `~/.claude/settings.json`,
whether a daemon runs), sends one test message to the person ("aipager
is set up for you. ..."), and only then writes. Files written, the same
as the wizard writes them:

- `~/.config/aipager/aipager.yaml` (mode 0600): `bot_token` and one DM
  scope whose only member is the person (`label: owner`, role owner or
  admin).
- `~/.claude/settings.json`: the aipager hooks and the statusLine (the
  existing file is backed up to `settings.json.bak.<time>` when it
  changes).
- The audit log gets a `grant-owner` record when someone becomes owner.
- With `--service`, the background service (the same as `aipager
  service install --yes`).
- On an old `config.env` install only (no `aipager.yaml` yet), the
  migration the wizard does: a copy `config.env.bak.<time>` (and
  `team.yaml.bak.<time>` when there is a `team.yaml`), and a starter
  `~/.config/aipager/policy.yaml` when none exists. `config.env` itself
  is left as it is.

If any check fails, nothing is sent and nothing is written. When a
daemon is already running, a change of chat or role is reloaded live; a
new bot token needs a restart (`aipager service stop`, then `aipager
service start`), which setup never does itself, and `--service` is
skipped. If setup cannot tell whether a daemon runs, it acts as if one
does (warning `daemon_unknown`): it never installs the service then, so
it can never restart a live daemon, and after any change it sends no
reload and reports `restart_needed: true` instead.

### `aipager setup` flags

| Flag | Meaning |
|---|---|
| `--token-file PATH` | Read the bot token from this file (at most 4096 bytes; text around one token is fine). A file other users can read works but gives the warning `token_file_shared`: `chmod 600` it, or delete it once setup succeeds. |
| `--token-stdin` | Read the bot token from stdin (a pipe or a redirected file, never a terminal). The pipe must close: setup waits at most 30 seconds for the end of stdin, then exits 2 with `token_stdin_timeout`. `printf '%s\n' "$TOKEN" \| aipager setup --token-stdin ...` and `< token.txt` both close it. |
| `--chat-id N` | Required. The person's numeric Telegram user id, which is also their DM chat id. Groups are added with `aipager config`. |
| `--role owner\|admin` | The person's role (default `owner`). |
| `--service` | Also install and start the background service (skipped while a daemon runs). |
| `--force` | Allow replacing the bot token, the owner DM chat, or the role of an existing install. Group scopes and other DMs are kept. |
| `--dry-run` | Run every check, send nothing, write nothing, and report what would change. |
| `--json` | Print exactly one JSON object on stdout; everything else goes to stderr. |

Give exactly one of `--token-file` and `--token-stdin`. The token is
never accepted as a flag value: `--token` and `--bot-token` are refused
(`token_on_command_line`) and the value is not shown. Abbreviated flags
(`--token-f`, `--dry`) are not accepted. Setup never asks a question,
never needs a terminal, and never reads the token from an environment
variable.

Re-running with the same token, chat and role changes nothing and says
so (`unchanged`, no test message). A different token, chat or role on an
existing install exits 6 until you add `--force`. An old `config.env`
install is migrated first, as the wizard does (it gives the role
`admin`, so `--role owner` on it needs `--force`).

### `aipager setup detect-chat`

```sh
aipager setup detect-chat (--token-file PATH | --token-stdin) [--timeout SECONDS] [--json]
```

Waits for the newest private message to the bot (pressing Start sends
one) and prints its sender: id, name and username. It saves nothing, so
show the person to the user and run `aipager setup --chat-id` only once
they confirm. Only messages sent at most 10 minutes before the command
started count. `--timeout` is 0 to 3600 seconds (default 300; 0 means
one look). It reads Telegram without confirming any message, so the
`/start` stays available to the wizard and the daemon.

While an aipager daemon for the same bot runs, the daemon holds the
bot's messages, and it does not note private messages from people who
are not set up yet. detect-chat then exits 9 (`source: "daemon"`): stop
the daemon (`aipager service stop`) and run it again, or ask the person
for their numeric id. With a daemon for a different bot, it reads
Telegram directly (`source: "telegram"`).

Limit: since detect-chat never confirms a message, Telegram shows it
only the oldest 100 unread updates of the last 24 hours. When the bot
has that many waiting, a new `/start` cannot be seen: detect-chat then
times out (exit 8) with the warning `updates_backlog`, and pressing
Start again does not help. Ask the person for their numeric Telegram id
instead (or start the daemon once, which reads and clears the backlog).

### Exit codes

| Code | `error` | Meaning |
|---|---|---|
| 0 | | Success (`installed`, `updated`, `unchanged`, `dry_run`, or detect-chat `found`). |
| 1 | `config_malformed`, `ambiguous_install`, `settings_invalid`, `test_send_failed`, `write_failed`, `service_failed`, `internal_error` | Failure. `ambiguous_install`: more than one DM could be the owner's; fix it in `aipager config`. |
| 2 | `usage`, `missing_token_source`, `token_source_conflict`, `token_file_unreadable`, `token_stdin_is_tty`, `token_stdin_timeout`, `token_malformed`, `token_on_command_line`, `bad_chat_id`, `bad_role`, `bad_timeout` | Bad or missing input; `fix` names the flag. An unknown flag or a wrong word after `setup` is reported by the argument parser (plain text, no JSON), also exit 2. |
| 3 | `deps_missing` | dtach, claude, aipager-hook or aipager-statusline is missing; `fix` has the commands. Nothing was written. |
| 4 | `token_rejected` | Telegram rejected the token. |
| 5 | `chat_not_started`, `bot_blocked` | The person has not pressed Start in the bot ("Open t.me/<bot> and press Start, then run this again."), or has blocked it. Nothing was written. |
| 6 | `existing_install` | A different token, chat or role is already set up; add `--force`. |
| 7 | `telegram_unreachable` | Network error, or Telegram answered 429 or 5xx. |
| 8 | `detect_timeout` | detect-chat saw no private message before `--timeout` (with the warning `updates_backlog` when 100 old unread updates hide newer ones). |
| 9 | `daemon_running` | detect-chat: a running daemon holds the bot's messages. |
| 10 | `updates_conflict` | detect-chat: another program reads this bot's messages. |
| 130 | `interrupted` | Ctrl-C. |

When several checks fail at once, the code follows this order:
`config_malformed`, `ambiguous_install`, `existing_install`,
`settings_invalid`, `deps_missing`. `--dry-run` exits with the code the
real run would have from these checks.

### JSON: `aipager setup --json`

Every key is always present; a value not known yet is `null`.

```json
{
  "command": "setup",
  "status": "installed",
  "ok": true,
  "exit_code": 0,
  "error": null,
  "message": "aipager is set up for @example_bot (chat 123456789, role owner). ...",
  "fix": null,
  "dry_run": false,
  "bot_username": "example_bot",
  "chat_id": 123456789,
  "role": "owner",
  "changed": ["bot_token", "owner_dm", "settings_json"],
  "test_message": "sent",
  "deps": {"ok": true, "items": [{"name": "dtach", "found": true, "path": "/usr/bin/dtach", "fix": null}]},
  "settings_json": {"path": "/home/you/.claude/settings.json", "status": "created", "backup": null, "repointed": 0},
  "daemon": {"running": false, "reload": "not_needed", "restart_needed": false},
  "service": {"requested": false, "result": "not_requested"},
  "warnings": [],
  "next_step": "Start aipager with `aipager service install` ..."
}
```

- `status`: `installed`, `updated`, `unchanged`, `dry_run` or `error`.
- `changed`: what changed (or would change, in a dry run), in this
  order: `migrated_v1`, `bot_token`, `owner_dm`, `role`,
  `settings_json`, `service`.
- `test_message`: `sent`, `not_needed`, `skipped_dry_run`, `failed` or
  `not_attempted`.
- `settings_json.status`: `created`, `patched`, `unchanged`,
  `would_change` or `not_checked`.
- `daemon.reload`: `reloaded`, `refused`, `not_reloaded` or
  `not_needed`. `restart_needed` is true after a new token while a
  daemon runs, and after any change when the daemon could not be
  reloaded or setup could not tell whether one runs.
- `service.result`: `not_requested`, `installed`, `already_installed`,
  `skipped_daemon_running`, `would_install`, `failed`, or `null` when
  setup stopped before it.
- `warnings`: objects with `code` (`token_file_shared`,
  `reload_refused`, `daemon_unknown`, `audit_write_failed`) and
  `message`. `audit_write_failed`: the owner grant is written, but its
  `grant-owner` record could not be added to the audit log; the record
  is best effort, so the exit code stays 0.
- `next_step` after a change while a daemon runs that could not be
  reloaded says to restart it (`aipager service stop`, then `aipager
  service start`); it names the bot token only when the token changed.
- With the warning `daemon_unknown`, `next_step` never says aipager is
  running. It says setup could not tell, points at `aipager doctor
  --json`, and says to start aipager with `aipager service install` or
  `aipager start` if it is not running. When a restart would be needed,
  it asks for one only if a daemon is running, and otherwise says to
  start it the same way.
- On an error, `status` is `error` and `error`, `message` and `fix` say
  what went wrong and what to do; `next_step` repeats `fix`. When the
  error came after a write, `changed` lists what was written, and the
  plain output adds a stderr line `Already written: aipager.yaml, ...
  (changed: ...)`.

### JSON: `aipager setup detect-chat --json`

```json
{
  "command": "detect-chat",
  "status": "found",
  "ok": true,
  "exit_code": 0,
  "error": null,
  "message": "Found Ada L (@ada), Telegram id 123456789.",
  "fix": null,
  "bot_username": "example_bot",
  "source": "telegram",
  "candidate": {"id": 123456789, "first_name": "Ada", "last_name": "L", "username": "ada", "date": 1700000000},
  "other_candidates": 0,
  "warnings": [],
  "next_step": "Show this person to the user; once they confirm, run: aipager setup --token-file <path> --chat-id 123456789"
}
```

`candidate` is `null` unless `status` is `found`. `warnings` holds
objects with `code` (`token_file_shared`, `updates_backlog`) and
`message`. `other_candidates`
counts other people who messaged the bot in the same 10 minutes: when
it is not 0, make sure you have the right person. `next_step` repeats
the token flag you used, never the token.

### `aipager doctor --json`

The checks of `aipager doctor` as one JSON object on stdout. Like plain
`aipager doctor` it only reads, and it sends no Telegram message. Exit
1 when any check fails, else 0. It cannot be combined with `--fix` or
`--safety-check` (exit 2 with an error object).

```json
{
  "command": "doctor",
  "version": "1.0.1",
  "ok": true,
  "summary": {"ok": 14, "warn": 1, "fail": 0},
  "checks": [{"key": "token_valid", "status": "ok", "title": "Telegram bot token", "detail": ["@example_bot"], "fix": null}]
}
```

`checks` are in this order, with these keys: `config_parses`,
`config`, `token_valid`, `chat_reachable`, `team`, `role_shell_access`,
`peer_messages`, `claude`, `claude_auth`, `dtach`, `hook_scripts`, `settings_json`,
`daemon`, `service_installed`, `service_unit_path`, `miniapp`. A check
that crashes keeps its key with status `warn`. Texts are plain (no
markup), and anything shaped like a bot token is replaced by
`<redacted>`.

## Command line: `aipager uninstall`

`aipager uninstall` stops the daemon and removes its service, removes the config folder (`~/.config/aipager`: the bot token, chats and settings) and the session list (`~/.claude/aipager-sessions.json`), cleans up aipager's files in `/tmp`, and uninstalls aipager itself. It asks first (`-y` skips the question).

It keeps aipager's data, so a reinstall picks up where it left off: `~/.local/share/aipager` (session folders, downloaded helpers, lock and marker files), `~/.local/state/aipager` (problem report records and the language state), `~/.claude/aipager-audit.jsonl` (the log of permission answers), `~/.claude/aipager-flood-state.json` (Telegram rate-limit history), `~/.claude/aipager-pending-users.json` (people who tried the bot without access) and `~/aipager-users` (members' [root folders](groups.md#root-folders), with their work). Before asking, it lists the ones that exist on your machine, and at the end it prints the `rm -rf` command that removes them too.

It also takes aipager's own entries out of Claude Code's `~/.claude/settings.json`: the hooks that run `aipager-hook` and the status line that runs `aipager-statusline` (a status line of your own stays). Everything else in that file stays as it was, and the file as it was is kept beside it as `settings.json.bak.<time>`. If the file is a link to another file, cannot be read, is not valid JSON, or is not in the shape Claude Code reads, it is left exactly as it is and uninstall says which entries to remove by hand. A hook of your own that still mentions `aipager-hook` or `aipager-statusline` (for example an `aipager-hook-capped.sh` wrapper) stays too, and uninstall names it. Claude Code sessions already open keep running the hooks they loaded until you restart them. `aipager config` adds the entries again after a reinstall. Your Telegram bot and any older `settings.json.bak.*` backups are not touched, and neither is `skipDangerousModePermissionPrompt` (the setting that skips Claude Code's warning before it runs with permission checks off), which aipager sets but you may have set yourself.

## Command line: `aipager report`

`aipager report` builds a problem report of the same kind as the bot's Report a problem button, from what aipager recorded on this machine (with fewer of the running daemon's facts: the mode, chats, features, uptime, sessions and Claude Code details are left unknown or empty), prints all of it, and asks `Send this report? [y/N]`. Only `y` or `yes` sends it; anything else, or no terminal at all (a script, a pipe, a Claude Code session's shell), sends nothing. `--note "text"` adds a note of at most 500 characters, shown in the printed report. It exits 0 when the report was sent, not sent by your choice, or only printed, and 1 when a send you confirmed did not go out. See [Problem reports](problem-reports.md) for what a report holds and where it goes.

## Settings in the environment

A few settings are read from the daemon's environment rather than
`aipager.yaml`. For the systemd service, add them with
`systemctl --user edit aipager` under `[Service]`
(`Environment=STALE_BUSY_TIMEOUT=900`), then `aipager service stop` and
`aipager service start`; for the launchd service on macOS, add them to
the plist's `EnvironmentVariables`
(`~/Library/LaunchAgents/com.aipager.daemon.plist`; `aipager service
install` writes that file again, so add them back after it); for a
daemon you start yourself, set them before `aipager start`. None of
them is needed for normal use.

| Setting | Default | What it changes |
|---|---|---|
| `AIPAGER_WHISPER_MODEL` | `base` | The speech model for voice messages (`tiny`, `base`, `small`, `medium`, ...): bigger is more accurate and slower. |
| `MINIAPP_PORT` | `8765` | The Mini App's port on `127.0.0.1` (`aipager miniapp enable --port` saves it instead). |
| `MINIAPP_ENABLED` | from `aipager.yaml` | `0` turns the Mini App off and `1` on, whatever `aipager.yaml` says (`aipager miniapp enable\|disable` saves the choice instead). |
| `MINIAPP_PUBLIC_URL` | none | Serve the Mini App at your own `https://` URL instead of the managed tunnel ([security](security.md#mini-app-tunnel)). |
| `KEEP_FINISHED_CARD` | `1` | `0` makes "Replace with result" the message layout for chats that never chose one in `/settings`. |
| `STALE_BUSY_TIMEOUT` | `7200` (2 hours) | Seconds a working session may go with no news before the chat gets a "still working (quiet for 120 min)" note. |
| `PROMPT_HOOK_GRACE_SECONDS` | `8` | Seconds before a message Claude Code never took is marked "Not taken by Claude Code" ([free messages](#text)). |
| `FINISH_CARD_GRACE_SECONDS` | `0.8` | How long the finished card leads the answer in the card layout; `0` sends both at once. |
| `GONE_SESSION_MAX_AGE_DAYS` | `14` | Days an ended session stays in `/resume`; `0` keeps them. |
| `STREAM_EDIT_INTERVAL`, `BUSY_EDIT_INTERVAL` | `1.2`, `3.0` | The busy card's refresh while streaming text and while quiet, in seconds; Telegram's per-chat limit still wins ([troubleshooting](troubleshooting.md#busy-cards-got-slower)). |
| `TYPING_INDICATOR_INTERVAL` | `4.5` | Seconds between "typing…" bubbles; `0` turns them off. |
| `TELEGRAM_MAX_RETRY_AFTER` | `90` | Telegram's wait, in seconds, beyond which aipager treats a rate limit as a ban (do not lower it; see [troubleshooting](troubleshooting.md#the-bot-went-quiet-flood-control)). |
| `CLAUDE_RICH_SUMMARIES` | `1` | `0` turns off the rich formatting of answers that contain code blocks (a fallback kept from older versions). |
| `AIPAGER_INTERACTIVE_TIMEOUT` | `300` | Seconds a permission prompt or question may wait with no news from Claude Code before aipager checks the saved conversation; only if it shows the prompt is over does the session count as working again ([troubleshooting](troubleshooting.md#a-permission-prompt-with-no-news-for-5-minutes)). |
| `AIPAGER_SUBAGENT_SILENCE` | `1800` | Seconds of silence after which a subagent with no stop seen is dropped ([hooks](hooks.md#subagentstart--subagentstop)). |
| `AIPAGER_BG_SHELL_MAX_TRACK` | `7200` | Seconds after which a background command with no end seen stops counting. |
| `AIPAGER_REPORT_PROMPTS` | on | `0` switches off the automatic problem-report offers ([problem reports](problem-reports.md#when-aipager-offers-a-report)). |
| `AIPAGER_CLAUDE_BIN` | none | The `claude` to run, when several are installed (`claude_path` in `aipager.yaml` wins). |
| `AIPAGER_SOCKET_PATH` | `$XDG_RUNTIME_DIR/aipager.sock` | The daemon's control socket. Sessions started by aipager pass it on to their hooks. |
| `AIPAGER_DEBUG` | off | `1` in a session's environment makes `aipager-hook` and `aipager-statusline` print their diagnostics on stderr. |
| `AIPAGER_INSTANCE_DIR`, `AIPAGER_TELEGRAM_API_BASE` | none | A second, isolated aipager for testing ([architecture](architecture.md#isolated-instance-for-testing)). |

## See also

- [Architecture](architecture.md) - where the bot fits.
- [Hook events](hooks.md) - what aipager hears back from claude.
- [Troubleshooting](troubleshooting.md) - when commands misbehave.
