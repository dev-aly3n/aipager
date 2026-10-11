# Vietnamese (vi) style guide

The translator (Claude) reads this with the glossary before every batch.

## Tone
Plain, direct and friendly. Address the reader as bạn. Sound like the English: plain words, short sentences, no slang.

## Address
bạn

## Punctuation and quotation marks
Use “ ” for quotations.
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
- A keyboard word (status, stop, new, kill) is ONE word with no space, because the text router reads only a text with no space: phiên (shows the sessions), dừng, mới, tắt. Each carries a diacritic, so none can be a session name (check 10).
- A navigation button that opens another keyboard reads as a short label (Mẫu ›, Lệnh ›, Mô hình ›).
- A mode word (mode.word.auto, mode.word.ask, mode.word.full: Auto, Hỏi, Toàn) is ONE word with no emoji: people type it after /mode, so it never changes once chosen. A mode label (mode.auto, mode.ask, mode.full) is what people see: the emoji, that same word and the word for mode (🤖 Chế độ Auto, 💬 Chế độ Hỏi, 🔓 Chế độ Toàn quyền); a sentence around {mode} must not add its own word for mode. Auto stays Auto ("Tự động" is two words, and Auto is understood). Never pick an ASCII-only mode word other than Auto: /mode reads it before a session name, so it could hide a session called that.
- A button or sentence that names a permission mode names it as a mode (operator, 2026-10-11): the switch buttons (modecard.btn.to_*, ui.btn.switch_to_*), the "instead" buttons (mode.*_instead) and the busy confirms (ui.confirm.perms_busy_*) say the mode word and the word for mode, as mode.auto and mode.ask do. The typed words stay one word.
- A default button label (Continue, Run tests...) stays short: it is a button. Its payload stays English (Claude receives the English).

## Terms
- session phiên; turn lượt; card thẻ; permission quyền; prompt prompt (a message to Claude: tin nhắn; a request waiting for an answer: yêu cầu); question câu hỏi; allow cho phép; deny từ chối; stop dừng; resume tiếp tục; queue hàng đợi; agent tác tử (a subagent: tác tử con); background nền (chạy nền); owner chủ sở hữu; admin quản trị viên; member thành viên; chat cuộc trò chuyện; group nhóm; settings cài đặt; update cập nhật; folder thư mục; model mô hình.
- Kill (end a session's process) tắt; Restart khởi động lại; Delete xóa; Rename đổi tên; Compact thu gọn; Retry thử lại; Cancel hủy; Back quay lại; role vai trò; transcript bản ghi hội thoại; daemon daemon (dịch vụ nền); install cài đặt.
- Kept in English: commit, token, diff, log, ctx, prompt, shell (a background shell command).
