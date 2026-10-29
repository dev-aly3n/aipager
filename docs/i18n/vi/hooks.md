# Sự kiện hook

<!-- i18n-source: docs/hooks.md @ 932dbe80 -->

> Trang này được dịch từ tiếng Anh; nếu hai bản khác nhau, bản tiếng Anh là bản đúng. ([English](../../hooks.md))

Claude Code chạy các lệnh hook ở những thời điểm cố định trong một việc: một tin nhắn được gửi, một công cụ sắp chạy, một công cụ đã xong, việc kết thúc, và vân vân. aipager gắn hook vào từng thời điểm đó và biến chúng thành hoạt động trên Telegram: thẻ làm việc, yêu cầu cấp quyền, câu trả lời, thông báo và log kiểm tra.

## <a id="how-it-is-wired"></a>Cách hook được gắn vào

`aipager config` (hoặc `aipager setup`) thêm các mục của aipager vào `~/.claude/settings.json` (hoặc `$CLAUDE_CONFIG_DIR/settings.json`, tệp mà các phiên của daemon đọc), và aipager kiểm tra chúng mỗi lần khởi động:

- một lệnh `aipager-hook` cho mỗi sự kiện trong 16 sự kiện bên dưới, với bộ lọc `*` trên các sự kiện công cụ (`PreToolUse`, `PostToolUse`, `PermissionRequest`);
- thời gian chờ 5 giây cho `PreModelSwitch` và, từ `aipager config` và `aipager setup`, 30 giây cho `PermissionRequest` (bản thân hook chờ tối đa 20 giây, xem bên dưới); các mục khác dùng mặc định của Claude Code;
- lệnh `statusLine`, `aipager-statusline`.

`aipager uninstall` gỡ các mục này ra lại (xem [lệnh](commands.md#command-line-aipager-uninstall)).

Claude Code khởi động một tiến trình `aipager-hook` (`aipager.dtach.notify_hook:main`) mới cho mỗi sự kiện. Tiến trình đó đọc JSON của sự kiện từ stdin, thêm hai trường (bên dưới) rồi gửi nó cho aipager thành một datagram qua socket Unix. Socket đó, theo thứ tự: `$AIPAGER_INSTANCE_DIR/aipager.sock` cho một bản chạy thử nghiệm tách biệt, `$AIPAGER_SOCKET_PATH`, `$XDG_RUNTIME_DIR/aipager.sock`, hoặc `/tmp/aipager.sock`. Việc gửi không bao giờ chờ: nếu không có aipager nào đang lắng nghe thì sự kiện bị bỏ, và bộ theo dõi phiên sẽ bắt kịp từ các file của phiên.

Đa số sự kiện tốn của Claude Code khoảng 20 đến 25 ms để khởi động tiến trình. Một vài sự kiện còn nạp mã quy tắc của aipager: trước một lần gọi công cụ (bước kiểm tra an toàn, xem [PreToolUse](#pretooluse)) khoảng 70 ms, ở cuối một việc (để xóa dấu việc đang mở) khoảng 55 ms, và `UserPromptSubmit` (sự kiện ghi quy tắc của việc). `PermissionRequest` cố ý chờ bạn chạm, tối đa 20 giây.

Hook tự giới hạn bộ nhớ của nó ở 1 GB. Hook nào chạm giới hạn sẽ thoát với lỗi (với `PreToolUse`, mã thoát mà Claude Code hiểu là từ chối) và gửi cho aipager một datagram `hook_memory_cap_hit`, datagram này trở thành một thông báo trong chat, kèm nút “🐞 Báo cáo sự cố này” cho chủ sở hữu bản cài (xem [báo cáo sự cố](problem-reports.md)). Đặt `AIPAGER_DEBUG=1` trong môi trường của phiên để hook và dòng trạng thái in thông tin chẩn đoán ra stderr.

## <a id="what-the-daemon-receives"></a>aipager nhận được gì

Datagram là JSON hook của chính Claude Code cùng với các trường aipager thêm vào (một vài sự kiện mang nhiều hơn, được nêu dưới từng sự kiện bên dưới):

| Trường | Ý nghĩa |
|---|---|
| `hook_event_name` | Sự kiện (trường của Claude Code; aipager phân việc theo nó). |
| `session_id`, `transcript_path`, `cwd`, ... | Các trường của chính Claude Code, giữ nguyên như được gửi. `session_id` là ID cuộc trò chuyện của Claude Code. |
| `session` | Do aipager thêm: tên của nó cho phiên, `claude-<name>`, lấy từ biến `CLAUDE_DTACH_SESSION` mà aipager đặt khi bắt đầu một phiên. Sự kiện từ một phiên Claude Code mà aipager không khởi động thì không có tên, và aipager bỏ qua chúng. |
| `sl_tokens` | Do aipager thêm, ở mọi sự kiện trừ `MessageDisplay`, khi file dòng trạng thái của phiên tồn tại: `context_pct`, `total_output`, `total_input`, `current_output`, `lines_added` và `lines_removed`. |
| `claude_pid` | Do aipager thêm, ở các sự kiện của chính phiên: PID của `claude` của phiên đó (`CLAUDE_PID` của Claude Code), mà aipager dùng để tìm claude đó trong danh sách phiên của Claude Code. |
| `bg_job` | Do aipager thêm, ở mọi sự kiện của một tác vụ chạy nền của Claude Code được tính cho phiên (xem bên dưới): ID của tác vụ. |

Các sự kiện công cụ còn mang `tool_name` và `tool_input`. Mọi sự kiện xảy ra bên trong một subagent đều mang `agent_id` (còn các sự kiện của subagent thì mang thêm `agent_type`); các sự kiện của vòng chính thì không, và aipager dựa vào đó để đặt các lần gọi công cụ của một agent vào đúng dòng của agent đó.

Các tác vụ chạy nền của Claude Code cũng chạy hook của chúng. Nhấn ← khi ô nhập trống sẽ chuyển cuộc trò chuyện của một phiên sang một tác vụ chạy nền (chế độ xem agent của Claude Code), và Claude Code còn giữ sẵn vài tiến trình chạy nền dự phòng cho tác vụ tiếp theo. Hook của chúng chạy với `CLAUDE_JOB_DIR` được đặt, và thừa hưởng tên phiên của bất kỳ claude nào đã khởi động dịch vụ chạy nền của Claude Code, nên tên đó không cho biết gì về chúng. Vì vậy hook chỉ tính một tác vụ chạy nền cho phiên mà chính claude của nó đã chuyển cuộc trò chuyện sang tác vụ đó: hook tìm phiên đó trong file liên kết của tác vụ (`claude-bgjob-<job>.json`, do aipager ghi khi thấy việc chuyển, xem [kiến trúc](architecture.md#file-and-socket-layout)), nếu không thì trong danh sách phiên của Claude Code cùng với danh sách PID của aipager. Sự kiện của tác vụ như vậy được gửi tới aipager như sự kiện của phiên đó, với `bg_job` được đặt, nên câu trả lời, thẻ và các yêu cầu của nó tới được chat của phiên. Mọi tiến trình chạy nền khác đều là tiến trình lạ: hook của nó không ghi gì, không gửi gì và không in gì, chỉ trừ việc `PreToolUse` vẫn kiểm tra các lần gọi công cụ của nó theo quy tắc của phiên mà nó thừa hưởng và in ra một lời từ chối (nó không bao giờ cho phép nhiều hơn phiên đó sẽ cho phép). aipager bỏ một sự kiện `bg_job` mà nó không ghép được với phiên nào đang ở chế độ chạy nền, và không tạo gì cho sự kiện đó.

## <a id="event-reference"></a>Danh sách sự kiện

Phần xử lý của aipager nằm trong `aipager/dtach/hook_receiver.py`; việc của chính hook nằm trong `aipager/dtach/notify_hook.py`.

### <a id="sessionstart"></a>`SessionStart`

Một phiên vừa bắt đầu, được tiếp tục, hoặc vừa được thu gọn. Một phiên mà aipager chưa theo dõi sẽ được đăng ký. Sau khi thu gọn, thẻ hiện `📦 name · Đã thu gọn cuộc trò chuyện (trước đó 82%)` (xem [PreCompact](#precompact--postcompact)). Với một cuộc trò chuyện mới, hook còn xóa dấu việc đang mở của việc trước. `SessionStart` đầu tiên của một tác vụ chạy nền (nguồn `fork`) chỉ báo cho aipager ID cuộc trò chuyện của tác vụ; nó không đóng việc đang mở đã chuyển đi cùng. `SessionStart` đầu tiên của một phiên được đưa về lại từ chế độ chạy nền của Claude Code sẽ gửi các tin nhắn bị giữ trong lúc đó.

### <a id="sessionend"></a>`SessionEnd`

Phiên đã kết thúc. Nó được đánh dấu GONE: nó rời khỏi thanh trạng thái được ghim và chuyển sang các phiên đã kết thúc, nơi `/resume` có thể đưa nó trở lại. `aipager session <name>` hoặc `/new <name>` bắt đầu một phiên mới. `SessionEnd` của một tác vụ chạy nền (tác vụ đã bị dừng) không bao giờ kết thúc phiên: nó kết thúc việc mà tác vụ để ngỏ, giống như `StopFailure`.

### <a id="userpromptsubmit"></a>`UserPromptSubmit`

Claude đã nhận một tin nhắn, gõ trong terminal hoặc gửi từ Telegram.

Trước tiên hook so tin nhắn với các ghi chú quyền của phiên (`/tmp/claude-notes-<session>/`, một ghi chú cho mỗi tin nhắn Telegram mà aipager đã gõ vào) và ghi quy tắc của việc vào `/tmp/claude-policy-<session>.json`, file mà bước kiểm tra [PreToolUse](#pretooluse) đọc (xem [bảo mật → áp dụng quy tắc trong chế độ làm việc nhóm](security.md#team-mode-enforcement)). Nó báo cho aipager những tin nhắn nào Claude đã nhận (một datagram `queue_pickup`) trước khi chuyển tiếp sự kiện. Sau đó nó in thêm ngữ cảnh cho Claude khi có: kiểu trả lời đã chọn trong `/settings`, và, khi đây là câu trả lời cho một tin nhắn cũ hơn, tin nhắn đó viết gì và khi nào. Một tin nhắn `<task-notification>` (Claude tự đánh thức mình khi một agent chạy nền hoặc một lệnh chạy nền xong) là phần tiếp của cùng công việc đó: nó không khớp với ghi chú nào và giữ nguyên quy tắc của việc.

aipager đánh dấu phiên là đang làm việc, mở thẻ làm việc nếu chưa có (tin nhắn gửi từ Telegram đã có sẵn một thẻ), và đổi 👀 trên những tin nhắn Claude đã nhận thành 👍. Một tin nhắn gửi trong lúc một việc đang chạy sẽ được 👍 khi Claude gộp nó vào việc đó hoặc bắt đầu việc tiếp theo với nó.

### <a id="pretooluse"></a>`PreToolUse`

Một công cụ sắp chạy. Đây là nơi bước kiểm tra an toàn của chính aipager chạy, bên trong hook, trước khi Claude Code xem tới các quy tắc quyền của nó:

- Một việc bắt đầu từ Telegram phải theo quy tắc vai trò của người gửi và mức an toàn tối thiểu (các đường dẫn được bảo vệ, các mẫu lệnh), thứ mà chỉ vai trò `owner` được bỏ qua; một việc mà aipager không biết là của ai thì nhận mức an toàn tối thiểu; tin nhắn bạn gõ trong terminal thì không bị giới hạn. Các quy tắc được mô tả ở [nhóm](groups.md#how-rules-work) và [bảo mật](security.md#team-mode-enforcement).
- Khi một quy tắc từ chối lần gọi, hook in ra quyết định `deny`, Claude Code từ chối công cụ mà không hỏi, và aipager được báo (`safety_blocked`): việc dừng lại với `🛑 x1 · Bị quy tắc an toàn chặn: ... (đã dừng)`, và mọi lần gọi công cụ sau đó trong việc này cũng bị từ chối.
- Một lời gọi `SendUserFile` (Claude gửi file vào chat) được kiểm tra cho mọi việc, kể cả việc của owner và tin nhắn gõ trong terminal: mức sàn gửi, các kiểm tra file và quy tắc riêng của việc ([bảo mật](security.md#files-claude-sends)). Lời gọi bị từ chối nhận một `deny` không có dấu "aipager safety policy": Claude đọc lý do và nói lại với bạn, việc vẫn tiếp tục, và hook gửi cho aipager `send_files_refused` kèm từng file, để ghi vào log kiểm tra.
- Một bước kiểm tra bị hỏng sẽ từ chối, trừ khi quy tắc của phiên đọc được và cho phép chủ sở hữu bỏ qua.

Ở phía aipager, lần gọi trở thành một dòng trên thẻ làm việc (lần gọi của một subagent nằm trên dòng của agent đó), và một `Write` hoặc `Edit` hiện bản xem trước thay đổi khi `/settings` → Xem trước thay đổi (diff) đang bật (mặc định tắt). Một lần gọi `AskUserQuestion` trở thành câu hỏi với một nút cho mỗi phương án; câu hỏi do một agent phụ trợ của Claude Code đặt ra khi không có việc nào đang chạy thì không được hiện (đó không phải Claude hỏi bạn). Lần gọi `Agent` của chính Claude để lại `subagent_type` và `description` cho agent mà nó khởi động, và lần gọi của một subagent là hoạt động mới nhất của agent đó: cả hai hiện trong phần trạng thái dưới câu trả lời trong lúc agent chạy nền.

### <a id="permissionrequest"></a>`PermissionRequest`

Claude Code sắp xin quyền. Thẻ làm việc trở thành yêu cầu cấp quyền, với lệnh hoặc đường dẫn thật nằm dưới phần mô tả của Claude (xem [lệnh → yêu cầu cấp quyền](commands.md#permission-prompts)): Cho phép, Từ chối, Dừng, và Luôn cho phép chỉ khi Claude Code đưa ra một quy tắc thật để ghi nhớ.

Hook tự trả lời yêu cầu khi có thể. Nó mở một socket trả lời ngắn hạn, gửi địa chỉ của socket cùng với sự kiện, và chờ bạn chạm tối đa 20 giây. Một lần chạm kịp lúc sẽ quay về thành một quyết định `PermissionRequest` (cho phép, cho phép kèm quy tắc, hoặc từ chối), nên Claude Code không bao giờ hiện hộp thoại của nó. Nếu không có lần chạm nào kịp lúc, hook báo cho aipager rằng thời gian chờ đã hết (`permission_reply_timeout`) và không in gì; nó cũng không in gì khi không liên lạc được với aipager hoặc khi có gì đó trên đường này hỏng. Khi đó Claude Code hiện hộp thoại của nó trong terminal, và một lần chạm sau đó trả lời hộp thoại bằng cách gõ phím. Log kiểm tra (`~/.claude/aipager-audit.jsonl`) ghi lại mỗi lần chạm đã trả lời theo cách nào, `"via": "hook_decision"` hoặc `"via": "keystroke_fallback"`.

Một `PermissionRequest` đến trong khi một yêu cầu khác đang mở cũng được hiện, thành tin nhắn riêng bên dưới thẻ làm việc. Cùng một hộp thoại đến lần nữa (cùng người hỏi, cùng công cụ và lần gọi, hoặc cùng đầu vào) chỉ được hiện một lần. Claude Code cũng chạy hook này cho một `AskUserQuestion`; câu hỏi đó đã được hiện từ `PreToolUse` của nó, nên không bao giờ được hiện lần thứ hai. Hộp thoại của một agent chạy nền chỉ xuất hiện trong terminal sau khi hook của nó thôi chờ, nằm dưới mọi hộp thoại đang mở ở đó; vì vậy một lần chạm chỉ gõ phím vào yêu cầu mà Claude Code hiện trước (xem [lệnh → yêu cầu cấp quyền](commands.md#permission-prompts)).

### <a id="posttooluse--posttoolusefailure"></a>`PostToolUse` / `PostToolUseFailure`

Một công cụ đã xong, hoặc thất bại. Dòng của nó trên thẻ làm việc được chốt lại (thất bại được đánh dấu trên dòng), và câu trả lời cho một yêu cầu đang mở được ghi nhận: một nút Cho phép hoặc Từ chối cũ của yêu cầu đó không gõ gì nữa. Phiên giữ 200 dòng công cụ mới nhất.

Một lần gọi `Bash` mà Claude Code đã chuyển xuống chạy nền sẽ báo một `backgroundTaskId`. Khi đó aipager theo dõi nó như một lệnh chạy nền (chỉ của vòng chính: một lần gọi mang `agent_id` thuộc về subagent đó) cho tới khi thấy nó kết thúc: một `<task-notification>` có `<status>` (trong tin nhắn đánh thức Claude, hoặc trong cuộc trò chuyện đã lưu khi nó đến giữa chừng một việc), một `TaskStop` hoặc `KillShell` với ID của nó, phiên kết thúc, hoặc hai giờ (`AIPAGER_BG_SHELL_MAX_TRACK`, tính bằng giây, mặc định 7200). Xem [lệnh](commands.md#idle-responses) về dòng của nó trên thẻ.

Một lần gọi `Agent` (hoặc `Task`) bên trong một subagent (sự kiện của nó mang `agent_id`) cho biết ID của agent mà nó đã khởi chạy. aipager nhớ agent nào đã khởi chạy agent nào, nhờ đó phân biệt được một agent dừng lại để chờ agent của riêng nó với một agent đã xong (xem `SubagentStart` / `SubagentStop` bên dưới).

Với một `SendUserFile` đã chạy, aipager sao chép file của nó ngay, mỗi lời gọi một lần và chỉ trong một việc bắt đầu bằng một prompt, theo danh sách mà `PreToolUse` của nó cho thấy (không bao giờ theo kết quả của công cụ), kiểm tra lại từng file; các bản sao được gửi sau câu trả lời của việc, khi đã tìm thấy lời gọi trong lịch sử trò chuyện mà aipager ghi lại cho việc đó, được ghi trong chính việc đó ([bảo mật](security.md#files-claude-sends), [lệnh](commands.md#files-claude-sends)). Một việc mà Claude Code bắt đầu không có hook (một tin nhắn nó lấy tiếp theo, phản hồi của một hook Stop) được tính ngay khi lịch sử trò chuyện cho thấy việc đã bắt đầu.

### <a id="notification"></a>`Notification`

aipager đọc `notification_type` của Claude Code:

- `permission_prompt`: một tín hiệu xin quyền chậm hơn và cũ hơn. Nó chỉ hiện yêu cầu khi không có yêu cầu nào đang mở: nếu có một yêu cầu đang mở, đó là hộp thoại mà `PermissionRequest` đã hiện.
- `idle_prompt` trong lúc một yêu cầu cấp quyền hoặc một câu hỏi đang chờ: phiên vẫn ở trạng thái chờ, và chat nhận được `⬆️ jim · vẫn đang chờ bạn trả lời ở trên`, mỗi lần chờ một lần.
- `idle_prompt` trong các trường hợp khác: được xử lý như `Stop` (bên dưới). Nó bắt được một việc mà `Stop` bị mất; một câu trả lời đã được gửi thì không bao giờ gửi lại.

### <a id="stop"></a>`Stop`

Việc đã kết thúc. Phiên trở về trạng thái sẵn sàng, thẻ làm việc được chốt lại theo bố cục bạn chọn, và câu trả lời của Claude được gửi đi (từ `last_assistant_message` của sự kiện, nếu không thì từ cuộc trò chuyện đã lưu). Những tin nhắn đã được giữ lại chờ việc kết thúc sẽ được gửi tiếp theo. Các agent chạy nền và lệnh chạy nền vẫn đang chạy giữ cho công việc còn mở: thẻ vẫn là trạng thái của công việc, và câu trả lời kết thúc bằng trạng thái trực tiếp của phần việc đó: mỗi agent đang làm gì và bao nhiêu lệnh chạy nền của công việc đang chạy (xem [lệnh → agent vẫn đang chạy](commands.md#agents-still-running-when-the-answer-goes-out)). Hook xóa dấu việc đang mở trước, trừ khi Claude vẫn còn giữ một tin nhắn trong hàng đợi sẽ trở thành việc tiếp theo ngay lập tức.

### <a id="stopfailure"></a>`StopFailure`

Việc kết thúc vì một lỗi API. Phiên trở về trạng thái sẵn sàng và câu trả lời, nếu có, được gửi như với `Stop`; một lỗi đã biết (quá tải, giới hạn số yêu cầu, ...) được hiện bằng lời dễ hiểu.

### <a id="subagentstart--subagentstop"></a>`SubagentStart` / `SubagentStop`

Một subagent đã bắt đầu hoặc kết thúc. Mỗi subagent có một dòng riêng trên thẻ làm việc, `🤖 <type> · <activity> · <elapsed>`, với các lần gọi công cụ của nó được gộp bên dưới (lần mới nhất luôn hiện sau phần gộp), và được chốt lại thành `✅ 🤖 <type> · N công cụ đã chạy · <elapsed>` khi nó dừng. `SubagentStart` không cho biết agent nào đã khởi động nó: aipager ghép nó với lần gọi `Agent` (`PreToolUse`) cùng loại đang chờ. Khi mọi lần gọi như vậy đều đến từ một agent, agent mới được gắn ngay với agent đó (dòng của nó trên thẻ thành `🤖 pipeline-runner › ship-tester`, và trong trạng thái dưới một câu trả lời nó nằm dưới agent đó); khi chúng đến từ các agent khác nhau, không gì được gắn cho tới khi chính `PostToolUse` của agent đã khởi động nó nêu tên nó. Mô tả của một lần gọi chỉ được dùng khi có đúng một lần gọi loại đó đang chờ. Một subagent mà `SubagentStop` không bao giờ tới sẽ bị bỏ khi nó đã im lặng 30 phút (`AIPAGER_SUBAGENT_SILENCE`, tính bằng giây; không có sự kiện nào mang ID của nó). Một agent đang làm việc gửi sự kiện công cụ vài giây một lần, nên cách này chỉ dọn những agent mà lúc dừng bị bỏ lỡ (một lần khởi động lại, một datagram bị mất); một lần gọi công cụ duy nhất chạy 30 phút mà không có sự kiện nào dưới ID của agent đó cũng bị dọn. `AIPAGER_SUBAGENT_TTL` được chấp nhận như tên cũ của cùng cài đặt này. Các sự kiện `SubagentStop` không có loại và có ID lạ đến thường xuyên và được bỏ qua.

Một agent chạy nền dừng lại trong lúc một agent do nó khởi chạy vẫn đang chạy cũng nhận một `SubagentStop`. aipager coi đó là tạm dừng, không phải kết thúc: agent vẫn đang chạy (dòng của nó, công việc, trạng thái dưới các câu trả lời, thanh được ghim). `SubagentStart` tiếp theo của nó, cùng ID, là lúc nó tiếp tục và không thêm dòng thứ hai; bất kỳ lần gọi công cụ nào do chính nó thực hiện cũng được tính là tiếp tục. Nó kết thúc khi dừng mà không còn agent nào của riêng nó đang chạy. Nếu nó chưa tiếp tục sau hai phút kể từ khi agent cuối cùng trong số đó kết thúc, nó không còn được tính là đang chạy, nhưng không bao giờ được hiện là đã xong. Các agent đang chạy, kể cả những agent đang chờ, được giữ lại khi aipager khởi động lại.

Các agent bị dừng bằng phím của Claude Code để dừng agent chạy nền (ctrl+x ctrl+k, bấm hai lần) không kích hoạt `SubagentStop`. aipager tự kết thúc chúng: ngay lập tức khi chính lệnh Dừng của nó đã bấm các phím đó, khi bản ghi của Claude Code có dòng `agents_killed` (đọc ở `UserPromptSubmit` của lượt mà Claude Code sau đó tự bắt đầu, có nội dung là một câu thường không có lớp bọc), hoặc khi `background_tasks` của một sự kiện `Stop` không còn liệt kê các agent mà một `Stop` trước đó đã liệt kê. Các yêu cầu đang mở của chúng đóng lại (không hook nào báo điều đó), và một `SubagentStop` đến muộn cho một trong số chúng sẽ bị bỏ qua. Lượt đó không bị coi là tin nhắn gõ trong terminal khi chính lệnh Dừng của aipager đã bấm các phím đó, hoặc khi bản ghi của Claude Code đã cho thấy việc dừng.

### <a id="precompact--postcompact"></a>`PreCompact` / `PostCompact`

Claude đang thu gọn ngữ cảnh của nó (`trigger` là `auto` hoặc `manual`). Thẻ làm việc hiện `🔄 name · Đang thu gọn cuộc trò chuyện` (một tin nhắn mới khi chưa có thẻ), để lúc tạm dừng không trông giống một lần crash. `PostCompact` kết thúc trạng thái đó; dòng `📦 name · Đã thu gọn cuộc trò chuyện (trước đó X%)` đến từ `SessionStart` ngay sau đó, hoặc từ dòng trạng thái khi sự kiện kia bị lỡ. Dòng trạng thái của Claude Code hiện 0% cho tới tin nhắn tiếp theo, nên kích thước mới chưa biết: sau một lần thu gọn giữa hai việc, dòng này đổi thành `📦 name · Đã thu gọn cuộc trò chuyện: X% → Y%` ngay khi dòng trạng thái báo kích thước. Dòng trạng thái cũng đưa ra cảnh báo `⚠️ name · Ngữ cảnh đã đầy 82% - sắp tự động thu gọn` (kèm nút Thu gọn ngay) khi ngữ cảnh đạt 80%, mỗi cuộc trò chuyện một lần: thu gọn không làm nó hiện lại, còn một cuộc trò chuyện mới (ví dụ sau `/clear`) sẽ nhận lại nó.

### <a id="messagedisplay"></a>`MessageDisplay`

Chữ của chính Claude ngay khi nó hiện lên màn hình. Cuộc trò chuyện đã lưu chỉ được ghi sau mỗi lần gọi công cụ, nên đây là nguồn duy nhất cho biết Claude đang nói gì lúc này, và là thứ đưa lời bình của nó vào thẻ làm việc. Chữ của một subagent (bất kỳ sự kiện nào mang `agent_id` của nó) không bao giờ bị coi là chữ của Claude: nó không bao giờ vào phần lời bình của thẻ làm việc, Mini App hay log của việc, mà trở thành hoạt động mới nhất của agent đó trong phần trạng thái dưới câu trả lời. Hook không thêm dữ liệu dòng trạng thái vào sự kiện này: Claude Code chờ nó trong lúc vẽ màn hình, và nó xảy ra nhiều lần cho mỗi tin nhắn.

### <a id="premodelswitch"></a>`PreModelSwitch`

Claude Code 2.1.251 trở lên phát sự kiện này trước khi đổi mô hình. Nội dung mang `from_model`, `to_model`, `requested_model` và `source` (`command` cho một lệnh `/model` được gõ, hoặc `picker` hay `sdk`). Claude Code thường hỏi “Switch model?” trước khi đổi, vì câu trả lời tiếp theo phải đọc lại cả cuộc trò chuyện. Một hook trả lời `permissionDecision: "allow"` sẽ cho việc đổi diễn ra mà không có câu hỏi đó.

`aipager-hook` chỉ trả lời `allow` cho lần đổi do chính aipager gõ, từ ô chọn Mô hình trong Mini App hoặc từ bàn phím Mô hình. Mọi lần đổi khác không được trả lời, nên Claude Code hỏi như trước giờ. Điều này gồm cả lệnh `/model` mà người vận hành tự gõ trong terminal, bộ chọn mô hình, và một lần đổi qua SDK.

aipager nhận ra các lần đổi của chính nó nhờ một file đánh dấu. Ngay trước khi gõ `/model <name>`, aipager ghi `aipager-modelswitch-<session>.json`:
- file nằm trong thư mục chứa socket điều khiển (thường là `$XDG_RUNTIME_DIR`);
- được ghi một cách nguyên tử, với quyền 0600;
- ghi tên mô hình và ID phiên Claude;
- hết hạn sau 30 giây.

Hook chỉ trả lời `allow` khi tất cả những điều sau đều đúng:
- file đánh dấu là một file thuộc về bạn, và không phải symlink;
- nó chưa hết hạn;
- nó dành cho cùng phiên aipager và cùng ID phiên Claude;
- mô hình trong đó bằng `requested_model` hoặc `to_model`, không phân biệt chữ hoa chữ thường;
- `source` là `command`.

Sau đó hook nhận file đánh dấu bằng một lần đổi tên nguyên tử, để nó được dùng đúng một lần. Bước kiểm tra chỉ đọc một file trên máy, không dùng socket và không chờ. Sự kiện này không được chuyển tới aipager.

Claude Code 2.1.101 trở lên bỏ qua các sự kiện hook mà nó không biết, nên các phiên bản cũ hơn không có `PreModelSwitch` không bị ảnh hưởng. Một trường hợp vẫn có thể làm hiện câu hỏi: đổi sang hoặc từ `opusplan` có thể khiến Claude Code hỏi hook hai lần, và chỉ lần hỏi đầu tìm thấy file đánh dấu, nên Claude Code hỏi “Switch model?” trong terminal.

## <a id="the-status-line"></a>Dòng trạng thái

Không phải một sự kiện hook: Claude Code chạy lệnh `statusLine`, `aipager-statusline` (`aipager.dtach.statusline_notify:main`), mỗi lần nó vẽ lại dòng trạng thái. Lệnh này:

- ghi JSON trạng thái của Claude Code vào `/tmp/claude-status-<session>.json` (được hook đọc để lấy `sl_tokens`, được `aipager status` và aipager đọc);
- gửi cho aipager một datagram `statusline` với mô hình, % ngữ cảnh, chi phí, số token và số dòng đã thêm và đã xóa, những thứ cung cấp cho thẻ làm việc, `/status`, Mini App, bước kiểm tra rằng một lần đổi mô hình đã thành công, và cảnh báo ngữ cảnh;
- in dòng trạng thái của terminal: `[name] Opus 5.5 | 42% ctx | $1.23`.

## <a id="aipagers-own-datagrams"></a>Datagram riêng của aipager

Ngoài các sự kiện hook, các chương trình phụ trợ còn gửi cho aipager vài tin nhắn của riêng chúng qua cùng socket:

| Tin nhắn | Ai gửi | Khi nào |
|---|---|---|
| `queue_pickup` | hook | Ở `UserPromptSubmit`: những tin nhắn Telegram nào Claude đã nhận. |
| `safety_blocked` | hook | Ở `PreToolUse`, khi bước kiểm tra an toàn đã từ chối lần gọi. |
| `send_files_refused` | hook | Ở `PreToolUse`, khi file mà Claude muốn gửi bị từ chối: từng file và lý do, để ghi vào log kiểm tra. |
| `permission_reply_timeout` | hook | Một lần chờ `PermissionRequest` kết thúc mà không có lần chạm nào. |
| `statusline` | dòng trạng thái | Mỗi lần dòng trạng thái được vẽ lại. |
| `hook_memory_cap_hit` | hook, dòng trạng thái | Tiến trình đã chạm giới hạn bộ nhớ của nó. |
| `report_error` | hook, các lệnh `aipager` | Nó gặp lỗi: chỉ loại lỗi và vị trí lỗi trong mã của aipager, không bao giờ là nội dung của sự kiện, cho [báo cáo sự cố](problem-reports.md). |

## <a id="the-permission-flow"></a>Luồng xin quyền

```
claude              aipager-hook             aipager daemon          Telegram
  |                      |                         |                     |
  | PermissionRequest    |                         |                     |
  |--------------------->| opens a reply socket    |                     |
  |                      |------------------------>| busy card becomes   |
  |                      |                         | the prompt          |
  |                      |                         |-------------------->|
  |                      |                         |   you tap Allow     |
  |                      |                         |<--------------------|
  |                      |   allow (reply socket)  | audit log           |
  |                      |<------------------------|                     |
  |  decision: allow     |                         |                     |
  |<---------------------|                         |                     |
  | tool runs            |                         |                     |
```

Nếu không có lần chạm nào trong 20 giây, hook không in gì và Claude Code hiện hộp thoại của nó; một lần chạm sau đó sẽ gõ câu trả lời vào hộp thoại qua terminal của phiên (`dtach -p`). Dù theo cách nào, lần chạm cũng được ghi vào log kiểm tra và được đăng dưới thẻ (`✅ jim · Đã cho phép · @alice · Bash: ls -la /tmp`).

## <a id="see-also"></a>Xem thêm

- [Kiến trúc](architecture.md): bộ nhận hook nằm ở đâu.
- [Lệnh của bot → yêu cầu cấp quyền](commands.md#permission-prompts): phía bạn nhìn thấy.
- [Mô hình bảo mật](security.md): bước kiểm tra an toàn làm được gì và không làm được gì.
