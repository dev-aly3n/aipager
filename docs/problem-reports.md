# Problem reports: what a report contains

When aipager hits a bug, you can send its maintainer a problem report with one tap. You see the whole report before anything is sent, nothing leaves your machine until you say so, and a report never holds your chats, prompts, names, paths or tokens. It is built only from a fixed list of fields: versions, counts, dates, yes/no answers, choices from fixed lists, places in aipager's own code and the `ap1-` names of its bugs, plus a short note only if you write one.

## How to send one

Only the owner of the aipager install can send a report from Telegram: the person in the install's own private chat with the bot (the operator; with several such chats and no single owner, there is no Report a problem button and no automatic offer, and `aipager report` in a terminal still works). The Mini App has its own report page; every other way in from Telegram opens the same preview card in your private chat with the bot:

- **`/help`**: when the owner sends it, the help text comes with a "🐞 Report a problem" button. In a group, only the owner's tap does anything, and the preview opens in the owner's private chat, never in the group.
- **The Mini App**: Settings tab, "Report a problem" (when the app is open on your private chat) opens a page inside the app. Your note goes at the top (optional, up to 500 characters, with a counter). Below it is a short summary of the report: versions, setup, the errors (tap one for its places in the code) and **More details**. **Show the exact report** shows the exact text that will be sent, your note included, and it follows your typing. **Send report** (Telegram's button at the bottom) sends exactly that, once, and the page then shows the reference with a **Copy** button. Back sends nothing. If aipager tidies your note (spaces at the ends, invisible characters, anything past 500 characters), the page shows the tidied note and nothing is sent until you tap Send report again.
- **The memory-cap notice**: when aipager's hook runs out of memory, the notice in the chat has a "🐞 Report this" button.
- **An automatic offer**: rarely, aipager offers to send a report about a bug it noticed (see [When aipager offers a report](#when-aipager-offers-a-report)).
- **In a terminal**: `aipager report` prints a report and asks `Send this report? [y/N]`. It holds the same kinds of fields, but fewer of the running daemon's facts (the mode, chats, features, uptime, sessions and Claude Code details are left unknown or empty). `aipager report --note "what you were doing"` adds a note. Without a terminal (a script, a pipe, a Claude Code session's shell) it prints the report and never sends it.

The preview card shows the report exactly as it will be sent: the JSON in a collapsed block you can expand, or, when it is too long for one message, as a `report.json` file that replies to the card. Its buttons:

- **📤 Send**: sends that exact report, once. The card then says `Sent. Reference ap1-...`.
- **✏️ Add a note**: your next message in that chat within 10 minutes becomes the report's note (cut at 500 characters), and the card shows the report again with the note in it before you send. Doing anything else instead (a command, another button, a photo, an action in the Mini App) closes the note question without adding a note, and after 10 minutes your next message goes to Claude as usual.
- **✖️ Cancel**: nothing is sent.

A preview stays open for 24 hours, and the 5 newest stay open (opening a 6th closes the oldest). After a restart of aipager, an old card can no longer be sent: open Report a problem again for a fresh one. A report opened in the Mini App can be sent for 24 hours (the 3 newest); after a restart the page loads a fresh one.

## What a report holds

Each part, in plain words. Every value is checked against the list of allowed values before the preview is shown and again before sending; anything that does not fit is replaced by `<invalid>` or left out, never sent as it was.

**About the report**
- `schema`: the report format (`aipager-report/1`).
- `trigger`: `manual` (you opened it) or `auto` (an automatic offer).
- `day`: the date, in UTC (no time of day).
- `note`: only if you added one: your words, exactly as the preview showed them (at most 500 characters, without invisible characters).

**aipager and the machine**
- `aipager`: the version; how it was installed (`pipx`, `uv`, `brew`, `pip`, `editable`, `nix`, `snap`, `docker`, `system`, `foreign` or `unknown`); where from (the package index, a local folder, a git checkout); whether `aipager update` can upgrade it.
- `python`: the Python version and whether it is CPython or PyPy.
- `os`: the system (`linux`, `darwin`, `windows`, `freebsd`), the processor type, the Linux distribution from a fixed list and its version number, the kernel's major.minor version, the container type if any (`docker`, `lxc`, `wsl`, `podman`), and whether aipager is set up as a systemd or launchd service.
- `deps`: the versions of the libraries aipager depends on (python-telegram-bot, httpx, aiohttp, PyYAML, rich, questionary, dtach-bin).
- `claude_code`: the Claude Code version, how many Claude Code installs were found, and how it logs in (`env`, `file`, `keychain`, or why that could not be told). Never the login itself.

**Your setup, as counts**
- `config`: the mode (`personal`, `team`, `scope`); how many private chats and how many groups are configured; how many custom roles exist (their names are not included); which features are on (Mini App, its tunnel kind, observers, voice, diff previews, rich summaries).
- `runtime`: how long aipager has been running, as a range (`<10m`, `10m-1h`, `1-24h`, `1-7d`, `>7d`); how many sessions are live and busy; how many times aipager stopped unexpectedly in the last 7 days and how it ended last time (`clean`, `crash`, `reboot`).
- `doctor`: the results (`ok`, `warn`, `fail`) of a fixed list of health checks, when they are available without running the checks again (reports from the bot and the terminal leave this empty).
- `flood`: Telegram rate-limit state added up over all chats: bans in the last 7 days, whether a chat is muted, in minimal mode or backing off right now, and the busiest chat's share of its hourly budget as a range. No chat ids.

**What went wrong**
- `counters_24h`: how often each of a fixed list of things happened in the last 24 hours (for example the busy-card watchdog restarting, a Telegram error class, the hook hitting its memory cap, a network or disk error). Only the counts.
- `log_digest_24h`: for the 15 busiest places in aipager's code that logged a warning or an error in the last 24 hours, the place (`aipager/state.py:1234`), the level and how many times. Never the log text.
- `errors`: up to 10 errors aipager recorded, each with:
  - `fingerprint`: an `ap1-` name for the bug (a hash of the error's type and the names of up to 8 places in aipager's code, never of any text or data);
  - where it happened (the daemon, the hook, the status line, a command, the Mini App), how it was caught, and whether it is a bug or only something unusual (`tier`);
  - for an error logged by aipager, the logger's name (an `aipager` module, `asyncio` or a `telegram.ext` part);
  - for a failed Telegram call, its kind (`message_deleted`, `not_modified`, `parse_entities`, `too_long`, `chat_not_found`, `canceled_by_edit` or `other`);
  - the exception's type name (for example `builtins.KeyError`, or `<other>` for one from outside a fixed list of packages), the types of up to three causes, and an error number name such as `ENOSPC`;
  - for a hook error, the hook event and the tool (a built-in tool by name, any MCP tool as `mcp`);
  - how many times, on which days and on which aipager versions it happened;
  - up to 12 places in aipager's own code it passed through (file, line, function name), and up to 8 names of outside packages it passed through, from a fixed list.

**Never included**: your chats, prompts, Claude's answers, session names, folder or file paths, tokens or keys, chat or user ids, usernames, the machine's name, the text of any error message, and log lines. The fields above have no place for them, and a report is checked against that list before it is shown and before it is sent.

## Where it goes

A report goes to the aipager maintainer's private error inbox on [Sentry](https://sentry.io) without your name: no account and no install id. Like any web request, it comes from your machine's internet address, which GitHub and Sentry can see; the inbox is set not to store it (Sentry's "Prevent Storing of IP Addresses"). aipager sends it itself in one HTTPS request (it does not use Sentry's own library, which collects much more): the report you saw goes as a `report.json` attachment, next to a short summary for grouping (the version, how it was installed and whether it is a developer install, the system, the Python and Claude Code versions, the trigger, and the first error's fingerprint, type and code locations). The request names only `aipager/<version>`.

After sending, the card (or the terminal) shows a **reference**: the first error's `ap1-` fingerprint, or, for a report with no error, its 32-character event id. If you also open a [GitHub issue](https://github.com/dev-aly3n/aipager/issues), quote the reference so the maintainer can find your report. Do not paste logs or `aipager doctor` output into an issue: they hold your chat id, your home folder's paths and the start of your prompts.

Just before sending, aipager reads [report-endpoint.json](https://github.com/dev-aly3n/aipager/blob/main/report-endpoint.json) from the aipager repository on GitHub to learn where to send (a running aipager keeps a good copy for a day; a failed read is tried again at the next send, and each `aipager report` reads it afresh). This lets the maintainer change the Sentry key, switch reports off for everyone, or stop versions below a given number from sending (a build that cannot tell its own version counts as below it), without a release. If GitHub cannot be reached, or the file is not one aipager understands, aipager uses the key built into this version; a readable file that says off is always obeyed.

## Limits

- At most 5 send attempts a day (UTC) from one machine, manual and automatic together, and a try that failed counts too. The next one says `Reports are limited to 5 a day from one machine. Try again tomorrow.`
- Nothing is retried in the background: if a send fails, the card says `Could not send right now. Nothing was lost; try again later.` and keeps its Send button.

## When aipager offers a report

aipager notices its own bugs and, rarely, offers to send a report about them in your private chat: `aipager hit an internal error N times (TypeError in the busy card). A report helps get it fixed. ...` with **Preview report**, **Not now** and **Don't ask for this**. Preview report opens the preview card with just those errors; nothing is sent until you tap Send there.

An offer comes only when all of this holds:
- It is a real bug in aipager: an error in aipager's own code that happened at least twice, at least 10 minutes apart, or at once for a crash of aipager. Errors that the hook, the status line helper or a command tells aipager about (the hook running out of memory included) must also have happened over at least a day, so a single one never causes an offer; the notice's Report this button is there for those. Things that are not aipager's fault (Telegram's limits, the network, a full disk) never cause an offer; they are only counted for a report you send yourself.
- At most one offer every 3 days, with everything pending in that one offer.
- Not in the first 48 hours after aipager first started on this machine (updates do not restart that), and not in the first hour after aipager restarts.
- Only when nothing is running or waiting to be sent for 2 minutes, and you used your private chat with the bot in the last 30 minutes. Never in a group, and never while Telegram is limiting that chat.
- Each bug is offered once per aipager version. After any answer it is not offered again on that version; it can come back after you update, only if it happens again.
- **Not now** and **Don't ask for this** both count as a no, and so does an offer you do not answer within a day. **Preview report** resets the count, even if you then cancel. Two no's in a row turn automatic offers off: when the second one is a tap, a line says so; either way `/settings` shows it.
- Never for a developer install (aipager installed from a local folder, a git checkout, or in editable mode).

To turn automatic offers off, use `/settings` in your private chat: **Problem reports: Off**. When two no's turned them off, the same page has **Turn offers back on**. On a machine without a chat to ask in, set `AIPAGER_REPORT_PROMPTS=0` in the daemon's environment. Report a problem and `aipager report` always work, whatever these settings say.

## What is kept on your machine

- `~/.local/state/aipager/reports.json`: the errors aipager recorded (in the shape shown above: type names, versions, code places, at most 50), hourly counts for 8 days, the log digest for 2 days, how recent stops ended, and the offer state (when it last offered, the no's in a row, which bugs it offered on which version). Readable only by you.
- `~/.local/state/aipager/report-sends.json`: how many reports were sent or tried today.
- `~/.local/share/aipager/install.json`: when aipager first started on this machine (for the 48 hours above).
- `~/.local/share/aipager/running.json`: written while aipager runs (its process number, start time, whether it runs as a service, and this boot's id), to tell a crash from a clean stop.

None of them holds a chat, a prompt, a name, a path or a token. `aipager uninstall` keeps them, with the rest of aipager's data, so a reinstall picks up where it left off; it lists what it keeps and prints the command that removes it (see [`aipager uninstall`](commands.md#command-line-aipager-uninstall)). To remove only these, delete those four files, or the whole `~/.local/state/aipager` folder (not `~/.local/share/aipager`, which also holds aipager's session folders).

## See also

- [Troubleshooting](troubleshooting.md): common failures and fixes.
- [Security model](security.md): what aipager touches and what goes over the network.
- [Bot commands](commands.md): `/help`, `/settings` and the command line.
