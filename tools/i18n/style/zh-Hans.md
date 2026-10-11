# Chinese (Simplified) (zh-Hans) style guide

The translator (Claude) reads this with the glossary before every batch.

## Tone
Plain, direct and friendly. Address the reader as 你. Sound like the English: plain words, short sentences, no slang.

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
- A mode word (mode.word.auto, mode.word.ask: 自动, 询问) is ONE word with no emoji: people type it after /mode, so it never changes once chosen. A mode label (mode.auto, mode.ask) is what people see: the emoji, that same word and the word for mode (🤖 自动模式, 💬 询问模式); a sentence around {mode} must not add its own word for mode.
- A default button label (Continue, Run tests...) stays short: it is a button. Its payload stays English (Claude receives the English).

## Width and spacing
- Button and label budgets count display cells: every Chinese character and full-width mark counts 2. Keep buttons short (two to six characters is typical).
- Full-width punctuation inside Chinese sentences: ，。：；？（）“ ”. No space before or after a full-width mark.
- One half-width space between Chinese and a Latin word, a number or a {placeholder} (“会话 {session} 已停止”), never next to a full-width mark. Inside a button, an emoji is followed by one space as in the English.
- Keep the English's half-width marks where they belong to a format: "·" separators, "→", "›", "«", the "-" between a label and its explanation in an option row, and the parentheses around a clock or a count in a status line may stay half-width when they wrap Latin text only.
- Lists take 、 between Chinese items.

## Terms
- session 会话; turn 轮 (这一轮, 本轮); card 卡片; permission 权限; prompt 提示词 (a message to Claude: 消息); question 问题; allow 允许; deny 拒绝; stop 停止; resume 恢复; queue 队列 (排队 as a verb); agent 代理 (a subagent: 子代理); background 后台; owner 所有者; admin 管理员; member 成员; chat 聊天; group 群组; settings 设置; update 更新; folder 文件夹; model 模型.
- Kill (end a session's process) 结束; Restart 重启; Delete 删除; Rename 重命名; Compact 压缩; Retry 重试; Cancel 取消; Back 返回; role 角色; transcript 对话记录; daemon 守护进程; install 安装.
- Kept in English: commit, token, diff, log, ctx, shell (a background shell command).
