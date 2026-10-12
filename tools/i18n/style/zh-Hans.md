# Chinese (Simplified) (zh-Hans) style guide

The translator (Claude) reads TRANSLATING.md first, then this, then the glossary.

## Tone
Plain, direct and friendly, the way Telegram's own Chinese interface talks. Address the reader as 你. Everyday Chinese, not a word-for-word copy of the English: short sentences, no officialese ("进行", "予以" piled up), no slang. Chinese has no gender in verbs, so "你" needs no care; never write 他 or 她 for {person}.

## Address
你

## Punctuation and quotation marks
Use full-width punctuation (，。：；？) and “ ” for quotations. No space between Chinese characters; one space around Latin words and placeholders is optional but be consistent.
- No long dashes (U+2014): use "-" or parentheses, as the English does.
- No exclamation marks.

## Digits
Latin digits 0-9.

## Never translated
Product names (Claude, Claude Code, Anthropic, aipager, Telegram, Mini App, GitHub, PyPI), Claude Code's tool names (Bash, Read, Write, Edit, Grep, Glob, WebFetch, Agent, AskUserQuestion), model names and aliases (opus, sonnet, haiku, fable, opusplan), commands (/new, /status, /x1, /mode auto), session names, @usernames, paths, code and anything inside a placeholder. These reach the text only as placeholders or verbatim; CI fails a translation that spells one differently.

## Placeholders and tags
Keep every {placeholder} and every HTML tag of the English, exactly once each. Never translate inside <code> or <pre>.

## Markers, keyboard words and modes
- Keep `**`, `_`, every HTML tag and every {placeholder} exactly as in the English: the same number of `**` and `_`, never a backtick.
- A keyboard word (status, stop, new, kill) is ONE word with no space (状态, 停止, 新建, 结束): it is typed as a word.
- A mode word (mode.word.auto, mode.word.ask, mode.word.full: 自动, 询问, 完全) is ONE word with no emoji: people type it after /mode, so it never changes once chosen. A mode label (mode.auto, mode.ask, mode.full) is what people see: the emoji, that same word and the word for mode (🤖 自动模式, 💬 询问模式, 🔓 完全访问模式); a sentence around {mode} must not add its own word for mode.
- A button or sentence that names a permission mode names it as a mode (operator, 2026-10-11): the switch buttons (modecard.btn.to_*, ui.btn.switch_to_*), the "instead" buttons (mode.*_instead) and the busy confirms (ui.confirm.perms_busy_*) say the mode word and the word for mode, as mode.auto and mode.ask do. The typed words stay one word.
- A default button label (Continue, Run tests...) stays short: it is a button. Its payload stays English (Claude receives the English).

## Width and spacing
- Button and label budgets count display cells: every Chinese character and full-width mark counts 2. Keep buttons short (two to six characters is typical).
- Full-width punctuation inside Chinese sentences: ，。：；？（）“ ”. No space before or after a full-width mark.
- One half-width space between Chinese and a Latin word, a number or a {placeholder} (“会话 {session} 已停止”), never next to a full-width mark. Inside a button, an emoji is followed by one space as in the English.
- Keep the English's half-width marks where they belong to a format: "·" separators, "→", "›", "«", the "-" between a label and its explanation in an option row, and the parentheses around a clock or a count in a status line may stay half-width when they wrap Latin text only.
- Lists take 、 between Chinese items.

## Words people actually say (use these)
| English idea | write | not |
|---|---|---|
| session | 会话 | |
| agent, background agent | 代理，后台代理 (Claude Code's own Chinese word) | 智能体 |
| background shell command | 后台命令 (the first line under an answer adds "(shell)") | 命令 alone |
| your message to Claude (prompt) | 消息 | 提示词 (only where the text is about prompts as such) |
| a permission or question prompt | 请求 (权限请求) | |
| compact (Claude Code's /compact) | 压缩对话 | |
| context window | 上下文 | 上下文窗口 |
| Claude's conversation | 对话 | 会话 (that is the session) |
| a Telegram chat | 聊天 (这个聊天, 私聊, 群组) | |
| update | 更新 | |
| restart | 重启 | |
| link | 链接 | |
| repository | 仓库 | 存储库 |
| hung, frozen | 卡住 | 冻结 |
| crashed | 崩溃 | |
| voice message | 语音消息 | |
| computer, machine | 电脑 | 机器, 主机 |
| default | 默认 | |
| private chat (DM) | 私聊 | DM |
| tool (Claude's tools) | 工具 | |
| idle (a session waiting for a message) | 就绪 | 空闲 |
| busy, working | 工作中 | 忙碌 |
| waiting on you | 等你回复 | 交互中 |
| ended (a session no longer running) | 已结束 | |
| end a session (Kill, End) | 结束 | 杀死 |
| the aipager program (daemon, service) | aipager | 守护进程, 服务 |
| saved transcript | 保存的对话 | 对话记录, 转录 |
| working directory | 文件夹 | 工作目录 |
| the person who makes aipager (maintainer) | aipager 开发者 | 维护者 |
| the bot's owner (operator) | 机器人主人 | 运营者 |
| bot | 机器人 | bot |

## Vocabulary (one word per idea)
- session 会话; turn: 轮 where people take turns ("现在轮到别人"), 任务 for Claude's work on one message ("这次任务结束后"); card 卡片; permission 权限; a permission request 权限请求; question 问题.
- allow 允许 (answered: 已允许); deny 拒绝 (answered: 已拒绝); stop 停止 (stopped: 已停止); resume 恢复; queue 队列 (排队 as a verb); agent 代理 / 子代理; background 后台.
- owner 所有者; admin 管理员; member 成员; group 群组; settings 设置; folder 文件夹; model 模型; role 角色; mode 模式; "Mini App".
- Kept in English: commit, token, diff, ctx, hook. A diff is "改动 (diff)" where it is a label.
- Buttons: 允许、拒绝、停止、恢复、重启、删除、重命名、取消、关闭、重试、结束、发送、立即发送.
- Same situation, same words: "Couldn't …" is "无法…" ("无法下载文件"); "Couldn't reach the server" is "无法连接服务器"; a refused restart is "未重启：…"; a stale button or card is "已过时"; an expired one "已过期"; a running session "运行中".
- {clock} is a duration: "已完成（{clock}）", "工作中（{clock}）", never "{clock} 后" (reads like a time of day). Parentheses inside a Chinese sentence are full-width （）.
- Address the reader as 你; no 他/她 for {person}: put the person first with a verb ("{person} 拒绝了").

## Plurals
other only: Chinese does not mark the plural (2 个会话). Use a measure word where Chinese needs one (个、次、条).
