# Vietnamese (vi) style guide

The translator (Claude) reads TRANSLATING.md first, then this, then the glossary.

## Tone
Plain, direct and friendly, the way Telegram's own Vietnamese interface talks. Address the reader as bạn. Everyday Vietnamese, not a word-for-word copy of the English: short sentences, no officialese ("được thực hiện", "tiến hành" piled up), no slang. Vietnamese has no grammatical gender, so "bạn" and {person} need no care there. Prefer the active voice with the person first ("{person} đã dừng {session}"), not a passive "bị/được … bởi".

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
- A navigation button that opens another keyboard reads as a short label (Mẫu, Lệnh, Mô hình ›).
- A mode word (mode.word.auto, mode.word.ask, mode.word.full: Auto, Hỏi, Toàn) is ONE word with no emoji: people type it after /mode, so it never changes once chosen. A mode label (mode.auto, mode.ask, mode.full) is what people see: the emoji, the words "Chế độ" and the mode's name (🤖 Chế độ Auto, 💬 Chế độ Hỏi, 🔓 Chế độ Toàn quyền); a sentence around {mode} must not add its own word for mode. Auto stays Auto ("Tự động" is two words, and Auto is understood). Never pick an ASCII-only mode word other than Auto: /mode reads it before a session name, so it could hide a session called that.
- A button or sentence that names a permission mode names it as a mode (operator, 2026-10-11): the switch buttons (modecard.btn.to_*, ui.btn.switch_to_*), the "instead" buttons (mode.*_instead) and the busy confirms (ui.confirm.perms_busy_*) say "Chế độ" and the mode's name, as mode.auto and mode.ask do. The typed words stay one word.
- The "instead" buttons on /new (mode.*_instead) say exactly what the switch buttons say ("💬 Chuyển sang Chế độ Hỏi"), not a word for "instead" (operator, 2026-10-16: a literal "instead" read oddly on a button).
- A default button label (Continue, Run tests...) stays short: it is a button. Its payload stays English (Claude receives the English).

## Words people actually say (use these)
| English idea | write | not |
|---|---|---|
| session | phiên | |
| agent, background agent | agent, agent chạy nền | tác tử |
| background shell command | lệnh chạy nền (the first line under an answer adds "(shell)") | shell alone |
| your message to Claude (prompt) | tin nhắn | prompt (only where the text is about prompts as such) |
| a permission or question prompt | yêu cầu (yêu cầu cấp quyền) | |
| compact (Claude Code's /compact) | thu gọn (thu gọn cuộc trò chuyện) | nén |
| context window | ngữ cảnh | cửa sổ ngữ cảnh |
| Claude's conversation | cuộc trò chuyện | hội thoại |
| a Telegram chat | chat (chat này, chat riêng, nhóm) | cuộc trò chuyện (that is Claude's) |
| update | cập nhật | |
| restart | khởi động lại | |
| link | link | liên kết |
| repository | repo | kho mã |
| hung, frozen | bị treo | đóng băng |
| crashed | bị crash | sụp đổ |
| voice message | tin nhắn thoại | voice |
| computer, machine | máy tính | máy, host |
| default | mặc định | |
| private chat (DM) | chat riêng | DM |
| tool (Claude's tools) | công cụ | tool |
| idle (a session waiting for a message) | sẵn sàng | nhàn rỗi |
| busy, working | đang làm việc | bận |
| waiting on you | đang chờ bạn | tương tác |
| ended (a session no longer running) | đã kết thúc | chết |
| end a session (Kill, End) | kết thúc (the keyboard word stays tắt) | giết |
| the aipager program (daemon, service) | aipager | daemon, dịch vụ nền |
| saved transcript | cuộc trò chuyện đã lưu | bản ghi |
| working directory | thư mục | thư mục làm việc |
| error | lỗi | |

## Vocabulary (one word per idea)
- session phiên; turn: lượt where people take turns ("đang là lượt của người khác"), việc for Claude's work on one message ("khi việc này xong"); card thẻ; permission quyền; a permission request yêu cầu cấp quyền; question câu hỏi.
- allow cho phép (answered: đã cho phép); deny từ chối (answered: đã từ chối); stop dừng (stopped: đã dừng); resume tiếp tục; queue hàng đợi; background chạy nền.
- owner chủ sở hữu; admin quản trị viên; member thành viên; group nhóm; settings cài đặt; folder thư mục; model mô hình; role vai trò; mode chế độ; "Mini App".
- Kept in English: commit, token, diff, log, ctx, crash, link, repo. A diff is "thay đổi (diff)" where it is a label.
- Buttons: Cho phép, Từ chối, Dừng, Tiếp tục, Khởi động lại, Xóa, Đổi tên, Hủy, Đóng, Thử lại, Kết thúc, Gửi, Gửi ngay.
- Same situation, same words: "Couldn't …" is "Không … được" ("Không tải được tệp"); "Couldn't reach the server" is "Không kết nối được máy chủ"; a refused restart is "Chưa khởi động lại: …"; a stale button or card is "đã cũ"; an expired one "đã hết hạn"; a running session "đang chạy".
- {clock} is a duration: "Xong ({clock})", "Đang làm việc ({clock})", never "sau {clock}" (reads like a time of day).
- Use "bạn", and the plain imperative for requests ("Gửi lại /new").

## Plurals
other only: Vietnamese does not mark the plural after a number (2 phiên).
