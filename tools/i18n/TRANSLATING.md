# How aipager is translated

These rules come from the Persian translation, which the operator reviewed
line by line on 2026-10-11 and took as the model for every language. The
translator (Claude or a person) reads this file, then `style/<code>.md` for
the language's own decisions, then the glossary, before every batch.
`python tools/i18n.py todo` hands all three over together.

The goal: a person who has never seen aipager understands every line the
first time, in their own language, as if a good app made in their country
had written it.

## 1. Say what the line means where it appears

Read the key's note, its examples and the code around it before you write.
The English is a hint, not the text to copy. Ask "what is this line for?":

- A **status** says the state, so the person knows what is going on.
- A **button** says what happens when it is tapped, as the person's own
  command (or the short noun the language's apps use on buttons).
- A **notice** says what happened and, if the person has to act, what to do.
- A **question** asks one clear thing.

Examples from the Persian (written back in English):

| English | Persian says | Why |
|---|---|---|
| Waiting | Waiting for your answer | the card waits for the person; say so |
| +{n} more | +{n} more agents | name the thing being counted |
| Results from the agents will follow here. | When the agents finish, the result is sent right here. | say when and where |
| ↩️ Switched to {session} | ↩️ Now you're talking to {session} | what the switch means for the person |
| ♻️ Resumed {session} | ♻️ {session} is open again | what the person sees |
| 🆕 Replace (fresh) | 🆕 Replace with a new session | "fresh" says nothing on its own |
| ↩️ Switch to it | ↩️ Go to it | |
| ♻️ {session} was previously used. | ♻️ The name {session} was used before. | it is the name that is taken |
| 📝 Prompt queued | 📝 The message stays in the queue | |
| ⚠️ … Not taken by Claude Code | ⚠️ … Claude Code didn't take the message | say who did not do what |
| ⚠️ Couldn't make out any speech in that. | ⚠️ I didn't hear any words in this message. | |

## 2. Say the word people say, even when it is English

Do not translate every word. In every language, people who use computers say
some English words every day, and a "pure" native word in their place reads
like a dictionary, not like an app. The test for every word: is this what a
person says out loud to a friend about their phone or computer? If people
say the English word, write it the way the language writes loanwords (its
own script and spelling where it has one). If people say a native word, use
the native word.

Do not overdo it: no English word that most people would not say, and no
Latin letters inside a non-Latin script except names, commands and the
glossary's `keep_english` words.

Each `style/<code>.md` keeps the list for its language: "words people
actually say" and "native words people really use". The Persian list, for
the idea: session → سشن (said, not the formal نشست); update → آپدیت;
restart → ری‌استارت; hung → هنگ کرده (not "frozen"); load → لود; repo → ریپو;
but send → ارسال, settings → تنظیمات, delete → حذف, cancel → لغو, because
those native words are the ones people say.

## 3. Plain words, no jargon

The reader may not be a programmer. Replace aipager's inside words with what
the person knows:

| English (inside word) | say |
|---|---|
| daemon, service, the bot process | aipager |
| inject, spawn | send, start |
| scope | this chat (in aipager's settings) |
| working directory | folder |
| machine, host | computer |
| DM | private chat (the word Telegram uses in the language) |
| transcript (saved) | the saved conversation |
| prompt (what a person sends) | message |
| tool call | a tool run ("{n} tools ran") |
| idle | ready |
| interactive (waiting on a dialog) | waiting for you |
| gone (a session) | closed |
| alive / not alive | running / not running |
| pending | waiting |

Keep a technical word when it is the only word people know (Claude Code's
tool names, `diff`, `commit`, `token`), and where a plain word needs it, add
the technical word once in parentheses: Persian "تغییرات (diff)",
"دستور پس‌زمینه (shell)".

## 4. Say who did what

Active voice, and the person first when a person did it:

| English | write |
|---|---|
| Denied by {person} | {person} denied it |
| ⏹ Stopped {session} by {person} | ⏹ {person} stopped {session}'s work |
| 🗑️ Deleted [{session}] by {person} | 🗑️ {person} deleted [{session}] |
| ⚙️ Switched to {mode} by {person} | ⚙️ {person} switched the session to {mode} |

The reader can be anyone, so never give them a gender. Where the language
marks gender on a verb or adjective about "you" (French "vous êtes passé",
Italian "sei passato", Arabic and Russian past tenses), choose a phrasing
that does not: "you did something else", not "you moved on". A person named
by a placeholder ({person}, {who}) gets the same care where the language
allows it.

## 5. Be honest about what aipager knows

aipager often knows less than the English seems to say. Do not make it sound
more certain, or more alarming, than it is:

- A background agent aipager lost track of: "the background agent is gone"
  (Persian: از بین رفت). It is the operator's chosen wording.
- A background shell whose end aipager never saw: "no news from the
  background command" (Persian: از دستور پس‌زمینه خبری نشد). It may still be
  running (a dev server), so never say it died or failed.
- "Claude may still process it" stays a maybe.

## 6. Short, and no word twice

Card lines, toasts (Telegram drops one past 200 characters), keyboard labels
and buttons are small. Say it once:

- 🔴 {session} · Session cleared → "{session} · session cleared", not "the
  session's conversation was cleared".
- A status line has no verb where the English has none, unless the language
  needs one to read naturally.
- Do not add politeness words the English does not have, except where the
  language would sound rude without them.
- No exclamation marks: aipager states things calmly.

## 7. Times and numbers

- `{clock}` is a duration (4:10 is four minutes ten seconds; 2:13:05 has
  hours). Never write it where it reads like a time of day ("after 4:10").
  Put it where the finished card puts it: "Finished (4:10)", "Finished
  (4:10, the background agent is gone)".
- The language's own digits only where its style guide says so (fa, ar).
  Never inside a placeholder: aipager converts numbers itself.
- Plurals: give every form the language uses. Every form that covers more
  than one number shows `{n}`.

## 8. One word for one idea

Use the style guide's vocabulary table everywhere: the same thing has the
same name on every card, button and page, and the same situation is said the
same way ("Which session to …?", "Couldn't … - try again"). The glossary
enforces the core terms.

Fixed shapes:

- A **mode word** (`mode.word.*`) is one word, typed after /mode. It never
  changes once chosen.
- A **mode label** (`mode.ask`, `mode.auto`, `mode.full`) is the emoji, that
  word and the language's word for "mode" (Persian: 💬 مود پرسش, 🤖 مود خودکار,
  🔓 مود دسترسی کامل). A button or sentence that names a mode names it as a
  mode. A sentence around `{mode}` must not add its own word for mode.
- A **keyboard word** is one word with no space; a Latin keyboard label must
  not be a valid session name (the style guide lists the decided labels;
  `check` enforces it).
- The **background shell** is "background command" in the language, never a
  bare "command" (it would read as an order); the first line under an answer
  adds the English word once: "🖥 2 background commands (shell) running".

## 9. Never translated

Product names (Claude, Claude Code, Anthropic, aipager, Telegram, Mini App,
GitHub, PyPI), Claude Code's tool names (Bash, Read, Write, Edit, Grep, Glob,
WebFetch, Agent, AskUserQuestion), model names (opus, sonnet, haiku, fable,
opusplan), commands (/new, /status, /mode auto), session names, @usernames,
paths, code, and anything inside a placeholder.

## 10. The rules `check` enforces

- Every `{placeholder}` and every HTML tag of the English, exactly once each;
  never translate inside `<code>` or `<pre>`.
- The same number of `**` and `_` markers; never a backtick.
- No long dash (U+2014) and no direction control characters: use "-" or
  parentheses, as the English does.
- Right-to-left languages write ← where the English has →.
- Length budgets per key (the invisible marks a right-to-left language adds
  count too).
- The glossary's terms, names and commands.

Then `python tools/i18n.py check` must print nothing.

## 11. Translating the docs

The docs follow the same rules as the strings: the meaning, the words
people say, plain words, who did what, honest about what aipager knows.
A doc is longer and read slowly, so it may explain a little more than a
button can, but never by adding facts the English does not have.

**Where they live.** `docs/i18n/<code>/` holds one file per English page:
`README.md` (the top-level README, ending with the list of docs that
`docs/README.md` has), and `install-with-an-agent.md`, `architecture.md`,
`hooks.md`, `commands.md`, `groups.md`, `troubleshooting.md`,
`problem-reports.md` and `security.md`, the same
names as in `docs/`. The English pages are the reference.

**The first lines.** Every translated page starts with its title, then a
comment naming the English page and the commit it was translated from,
then one line in the language saying it is a translation and the English
page wins when the two disagree, with a link to it:

```
# <title in the language>

<!-- i18n-source: docs/commands.md @ <short commit> -->

> <"This page is a translation of the English page; if they differ, the English page is right."> ([English](../../commands.md))
```

`python tools/i18n.py docs` lists every translated page whose English page
changed after that commit.

**Headings and links.** Translate every heading, and put the English
heading's anchor at its start, so a link works in every language:

```
## <a id="settings-in-the-environment"></a><the heading in the language>
```

Links keep their targets: a link to another doc points at the same page in
this folder (`commands.md#languages`), a link to the top-level README at
this folder's `README.md`, and a link to a file outside `docs/`
(`CHANGELOG.md`, `LICENSE`, `CONTRIBUTING.md`, `packaging/`) at the English
file (`../../../CHANGELOG.md`). Links to other sites stay as they are.

**What stays as it is.** Everything inside a code block or backticks that
a person types or a program prints: commands, flags, paths, file names,
environment variables, YAML and JSON keys, log lines, and the CLI's own
output (the command line speaks English). Product, tool and model names,
role names (`owner`, `admin`, `user`, `read_only`) and the session states
the CLI prints (`IDLE`, `BUSY`, `WAITING`, `TASKS`, `GONE`).

**Text aipager shows in Telegram or the Mini App.** Quote it the way the
reader sees it: the catalog's translation in this language, exactly, with
its emoji (look it up by its English text in `tools/i18n/source.jsonl` and
the language's catalog). A quote with no catalog entry (Claude Code's own
text, a log line) stays English. A Telegram or Mini App button is named in
the language's own words when the catalog has them.

**Words.** The vocabulary of `style/<code>.md` and the glossary: the same
word for the same idea as in the app. "The daemon" is aipager (or "the
aipager program" where it must be told apart from the `aipager` command);
"DM" is the private chat; "the machine" is the computer; "prompt" is the
message a person sends (a permission prompt stays a permission request).

**Right to left.** A Persian or Arabic page starts with `<div dir="rtl">`
and a blank line and ends with `</div>`, and every fenced code block in it
sits in `<div dir="ltr">` ... `</div>` with a blank line on each side of
the fence, so text reads right to left and code, commands and JSON keep
their order. Digits follow the
style guide in prose; numbers inside code, commands, versions and
settings stay Latin.

**Order inside a line.** GitHub lays out each paragraph, list and
heading by its first letter, and mixes of English and Persian or Arabic
can read out of order: a sentence ending on an English word before one
starting with one ("... Telegram. Claude ..." reads backwards), or a
command starting with a symbol after Persian text (`/kill` shows as
`kill/`). Invisible marks fix them without changing a word: `&rlm;` after
such punctuation or at the start of such a block, `&lrm;` before a word
starting with a symbol or after one ending with it. `python tools/i18n.py
docs --fix-rtl` puts them in, and `docs` and CI name any place still
missing one. To keep an English phrase together on purpose ("e.g. foo"),
put `&lrm;` right after its punctuation instead. Keep lists tight: a list
with a blank line inside it lays out left to right on GitHub, bullets on
the left, so a paragraph that belongs after an item goes after the list.

**Shape.** Keep the English page's sections, lists, tables and examples in
the same order, so a reader can hold the two side by side. Tables keep
their columns. Write each paragraph as one line or wrap it, as you like;
never break a line inside a link.
