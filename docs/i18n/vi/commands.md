# Lệnh và giao diện của bot

<!-- i18n-source: docs/commands.md @ a236da1 -->

> Trang này được dịch từ tiếng Anh; nếu hai bản khác nhau, bản tiếng Anh là bản đúng. ([English](../../commands.md))

Cách bạn điều khiển aipager từ Telegram. Có bốn cách đưa đầu vào: lệnh slash, nút bàn phím, bảng điều khiển Mini App, và tin nhắn tự do (chữ, file, tin nhắn thoại).

Bot chỉ nhận đầu vào từ các chat đã được cấu hình; về ranh giới tin cậy, xem [Mô hình bảo mật](security.md).

## <a id="slash-commands"></a>Lệnh slash

Các lệnh này được đăng ký trong `aipager/bot/lifecycle.py`. Menu lệnh của Telegram hiện chúng, được làm mới khi aipager khởi động và mỗi khi các phiên thay đổi.

| Lệnh | Đối số | Làm gì |
|---|---|---|
| `/start` | - | Màn hình chính: các phiên của chat này, nơi một tin nhắn thường sẽ đến (`✍️ Tin nhắn sẽ đến x1.`), và `🆕 Phiên mới`, `⚙️ Cài đặt` cùng, khi chat này có một phiên đã kết thúc có thể đưa trở lại, `↩️ Tiếp tục`. Cũng hiện bàn phím cố định. |
| `/help` | - | Một hướng dẫn ngắn theo việc cần làm (bắt đầu, trò chuyện, điều khiển, quản lý, cài đặt, và với quản trị viên là `/update`), kèm mẹo rằng chạm vào một lệnh sẽ gửi nó ngay: nhấn giữ (điện thoại) hoặc nhấn Tab (máy tính) để thêm chữ trước. Với chủ sở hữu bản cài (người ở trong chat riêng của chính bản cài đó với bot), nó còn có nút `🐞 Báo cáo sự cố`: nút này mở, trong chat riêng đó, bản xem trước đúng nội dung báo cáo sự cố, với Gửi, Thêm ghi chú và Hủy. Không có gì được gửi cho tới khi bạn chạm Gửi ([một báo cáo chứa gì](problem-reports.md)). |
| `/app` | - | Mở bảng điều khiển Mini App (phiên, xem diff, cài đặt). |
| `/status` | - | Các phiên của chat này dưới dạng một danh sách bạn thao tác được. Mỗi phiên nói trạng thái của nó bằng lời (`⚙️ đang làm việc 3:12 · Bash: run tests`, `⏳ đang chờ bạn · Bash: make deploy`, `💤 sẵn sàng`), với `✍️` trên phiên mà tin nhắn thường sẽ đến, và một dòng thứ hai gồm mô hình, ngữ cảnh, chi phí, những gì đang xếp hàng (`hàng đợi 3 (1 trong hàng đợi, 2 ghi chú)`), cùng các agent và lệnh chạy nền đang chạy. Mỗi phiên có một hàng nút: `✍️ x1` gửi các tin nhắn tiếp theo của bạn tới đó, `⏹ Dừng` khi nó đang làm việc, `Trả lời` khi nó đang chờ bạn, và `⋮` cho phần còn lại. Bên dưới: `🆕 Mới`, và `⚫ Đã kết thúc (n)` cho các phiên đã kết thúc (tiếp tục, xóa, hoặc xóa hết khỏi danh sách; chúng vẫn ở trong `/resume`). |
| `/stop [label]` | không bắt buộc | Ngắt việc hiện tại của một phiên. Không kèm tên, nó dừng phiên duy nhất của chat này đang làm việc (kể cả khi tin nhắn của bạn đang đi tới một phiên khác; một phiên có agent chạy nền hay lệnh chạy nền vẫn đang chạy được tính là đang làm việc), hỏi phiên nào bằng một bộ chọn khi có nhiều phiên, và báo `Hiện không có việc gì đang chạy.` khi không có phiên nào. `/stop x1` (hoặc `/x1 stop`) dừng x1 (hoặc trả lời `x1 hiện không làm gì.` khi nó không làm việc). Nó cũng bỏ các tin nhắn đang xếp hàng và trả lời đã bỏ bao nhiêu tin. |
| `/now` | - | Gửi ngay bây giờ các tin nhắn mà Claude đang giữ trong hàng đợi của phiên đang chọn, thay vì sau bước hiện tại (tổ hợp phím gửi ngay của chính Claude Code, Ctrl+X Ctrl+S). Dùng được bất cứ lúc nào, kể cả khi chat đang bị tắt gửi hoặc ở chế độ tối thiểu; trả lời `Không có tin nhắn nào đang chờ trong hàng đợi` khi Claude không giữ gì. Xem [Gửi ngay một tin nhắn đang xếp hàng](#send-a-queued-message-now). |
| `/new [name] [first message]` | đều không bắt buộc | Bắt đầu một phiên. `/new x1` bắt đầu nó ngay, và `/new x1 fix the tests` còn gửi “fix the tests” làm tin nhắn đầu tiên. Gửi không kèm gì (ví dụ chạm từ menu), `/new` sẽ hỏi tên, và tin nhắn tiếp theo bạn gửi được dùng y như đối số, nên `/new` rồi `x1 fix the tests` cho cùng kết quả. Trong một nhóm, hãy trả lời thẻ bằng tên (hoặc nhắc đến bot: `@aipagerbot x1`), vì ở đó một tin nhắn thường không tới được bot. Chỉ tin nhắn tiếp theo đó thôi: nếu bạn làm việc khác trước (một bức ảnh, một lệnh khác, một tin trả lời tin nhắn khác, một mẫu trên bàn phím, một nút khác), nó được xử lý như thường và thẻ đóng lại; `/status`, `/settings` và các menu của bàn phím thì để thẻ mở. Thẻ hỏi tên còn hiện chế độ, mô hình và thư mục mà phiên sẽ nhận, kèm các nút để đổi chúng trước. Cách nào cũng kết thúc ở cùng một thẻ “x1 đã sẵn sàng”: phiên đã nhận gì, tin nhắn tiếp theo của bạn sẽ đến nó, và các nút một chạm để chuyển sang chế độ khác hay đổi mô hình. Phiên mới của mọi người đều bắt đầu ở Chế độ Auto; đổi những gì /new dùng trong `/settings` → Phiên mới. Một dấu `!` trước tên sẽ bắt đầu phiên ở Chế độ Toàn quyền, cho một quản trị viên (vai trò `owner` và `admin`) hoặc một người trong chat riêng của chính họ với bot mà vai trò được gửi tin nhắn và trả lời Claude (`user`, không phải `read_only`); với bất kỳ ai khác, phiên bắt đầu ở Chế độ Auto và thẻ nói rõ vì sao. Một tên đang được dùng sẽ đưa ra `↩️ Đi tới phiên đó` (khi phiên đó đang chạy) hoặc `♻️ Tiếp tục` (khi nó đã kết thúc), `🆕 Thay bằng phiên mới` và `↩️ Hủy`, chỉ cho người đã gửi `/new`. Thẻ hỏi tên hết hạn sau 10 phút không dùng tới (`⏱ Thẻ phiên mới này đã hết hạn. Gửi lại /new.`). Một thành viên có vai trò thiếu `can_manage` (`user`, `read_only`) chỉ bắt đầu phiên bên trong [thư mục gốc](groups.md#root-folders) của chat: thư mục trên thẻ là thư mục gốc, trang Thư mục liệt kê nó và các thư mục nằm ngay bên trong (không có dòng “Thư mục mặc định”), “Thư mục mới” tạo thư mục bên trong thư mục đã chọn, và “Đi tới phiên đó”, “Tiếp tục” và “Thay thế” trên một phiên đang làm việc bên ngoài thư mục gốc đều bị từ chối (“Phiên này làm việc bên ngoài thư mục của bạn ~/..., nên vai trò của bạn không được tiếp tục, khởi động lại hay đổi chế độ của nó. Hãy nhờ quản trị viên.”). |
| `/resume [label]` | không bắt buộc | Tiếp tục một phiên đã kết thúc theo tên, hoặc mở một bộ chọn. Một phiên quay lại ở chế độ nó đang dùng. Một phiên từng ở Chế độ Toàn quyền chỉ quay lại ở Chế độ Toàn quyền với một quản trị viên hoặc một người trong chat riêng của chính họ; với bất kỳ ai khác, nó quay lại ở Chế độ Auto và câu trả lời nói rõ (`♻️ Đã mở lại x1 ở Chế độ Auto: Chế độ Toàn quyền chỉ dành cho quản trị viên.`). Một phiên được một bản aipager cũ lưu ở chế độ Auto cũ của nó (chạy không có kiểm tra nào) sẽ quay lại ở Chế độ Toàn quyền, và một phiên được lưu ở chế độ Hỏi thì vẫn quay lại ở Chế độ Hỏi. Một phiên đã kết thúc còn được liệt kê trong `GONE_SESSION_MAX_AGE_DAYS` ngày (mặc định 14) rồi rời khỏi danh sách phiên; cuộc trò chuyện đã lưu của Claude không bị đụng tới. Một thành viên có vai trò thiếu `can_manage` không tiếp tục được một phiên có thư mục nằm ngoài [thư mục gốc](groups.md#root-folders) của chat (chủ sở hữu và quản trị viên thì được); điều tương tự áp dụng cho Khởi động lại và mọi lần đổi chế độ, kể cả lần đổi tại chỗ giữa Chế độ Hỏi và Chế độ Auto. |
| `/kill [label]` | không bắt buộc | Kết thúc một phiên, luôn sau một bước xác nhận: `⏹ Kết thúc x1? Claude sẽ dừng làm việc và phiên sẽ đóng.` với `[⏹ Kết thúc] [Hủy]`. Không kèm tên, nó đi thẳng tới bước xác nhận khi chat này có một phiên đang chạy, nếu không thì hiện một bộ chọn các phiên đang chạy của chat này. Cũng có trong `/status` → `⋮` → `⏹ Kết thúc phiên`. |
| `/restart [label]` | không bắt buộc | Khởi động lại một phiên sau một bước xác nhận: Claude dừng rồi chạy lại, giữ nguyên cuộc trò chuyện, ở chế độ mà aipager khởi động hay chuyển nó sang gần nhất. Một phiên bắt đầu trong terminal bằng `aipager session` quay lại ở Chế độ Hỏi trừ khi từ đó tới giờ nó đã được đổi chế độ từ Telegram (aipager không biết terminal đã khởi động nó ở chế độ nào, và không bao giờ đoán một chế độ rộng hơn); tiếp tục nó từ Telegram cũng vậy. |
| `/rename [label] [new]` | không bắt buộc | Đặt tên mới cho một phiên (cả phiên đã kết thúc). `/rename x1 newname` đổi tên ngay. Với ít từ hơn, nó sẽ hỏi: hỏi thẳng tên mới khi lệnh chỉ có thể nói về một phiên, nếu không thì hiện một bộ chọn (một từ duy nhất sau `/rename` không chọn phiên, vì nó có thể là tên cũ hoặc tên mới). Trong một nhóm, hãy trả lời câu hỏi bằng tên. |
| `/delete [label]` | không bắt buộc | Bỏ một phiên đã kết thúc khỏi danh sách, sau một bước xác nhận. |
| `/diff [label]` | không bắt buộc | Hiện git diff của thư mục của phiên. Không kèm tên, nó hiện phiên mà tin nhắn của bạn đang đến. |
| `/clearqueue` | - | Bỏ mọi tin nhắn chưa được nhận của phiên đang chọn (cả tin nhắn aipager đang giữ lẫn tin nhắn đã xếp hàng bên trong Claude) mà không ngắt việc đang chạy. Trả lời số tin đã xóa. |
| `/mode [label] [ask\|auto\|full]` | không bắt buộc | Hiện chế độ quyền của một phiên với một nút để chuyển sang từng chế độ khác: `✍️ x1 đang ở 🤖 Chế độ Auto.` `[💬 Chuyển sang Chế độ Hỏi]` `[🔓 Chuyển sang Chế độ Toàn quyền]`. Có ba chế độ. **💬 Chế độ Hỏi**: Claude hỏi trước mỗi thao tác. **🤖 Chế độ Auto** (mặc định): bước kiểm tra an toàn của Claude Code chạy các thao tác an toàn và dừng các thao tác rủi ro để bạn quyết định. **🔓 Chế độ Toàn quyền**: mọi thứ chạy mà không có kiểm tra nào (`--dangerously-skip-permissions`); chỉ một quản trị viên, hoặc một người trong chat riêng của chính họ với bot mà vai trò được gửi tin nhắn và trả lời Claude (`user`, không phải `read_only`), mới chọn được nó. Chế độ Auto và Chế độ Toàn quyền chỉ thay đổi các yêu cầu cấp quyền của riêng Claude Code: hook của aipager vẫn áp quy tắc vai trò của thành viên. `/mode ask`, `/mode auto`, `/mode full` hoặc `/mode x1 ask` chuyển ngay; chính từ của chế độ đó bằng ngôn ngữ của chat cũng dùng được, không cần từ “chế độ” (`/mode x1 Hỏi` cho 💬 Chế độ Hỏi, `/mode x1 Toàn` cho 🔓 Chế độ Toàn quyền). Khi một phiên đang chạy trong chat này có đúng tên đó (giả sử một phiên tên `demander`), từ đó là tên phiên: `/mode demander` hiện chế độ của phiên đó, còn `/mode x1 demander` vẫn chuyển x1. `ask`, `auto` và `full` luôn là chế độ, theo bất kỳ thứ tự nào (`/mode x1 ask` hoặc `/mode ask x1`); chỉ khi cả hai từ đều là từ chỉ chế độ và đúng một từ là tên của một phiên đang chạy thì từ đó mới là tên phiên, nên với một phiên tên `full`, `/mode full ask` và `/mode ask full` đều chuyển nó sang Chế độ Hỏi, còn `/mode full` một mình vẫn là chế độ. Chuyển giữa Chế độ Hỏi và Chế độ Auto diễn ra tại chỗ, không khởi động lại và vào bất cứ lúc nào (kể cả khi Claude đang làm việc): aipager nhấn Shift+Tab trong phiên như cách bạn sẽ làm, thẻ ghi `⚙️ Đang chuyển x1 sang 🤖 Chế độ Auto mà không khởi động lại.` cho tới khi Claude Code báo chế độ mới ở bước tiếp theo, rồi hiện kết quả. Nếu Chế độ Auto không dùng được cho phiên đó (do mô hình hoặc gói của nó), thẻ nói rõ và phiên ở lại Chế độ Hỏi. Không làm được khi phiên đang hiện một câu hỏi hay yêu cầu cấp quyền: hãy trả lời trước. Chuyển sang Chế độ Toàn quyền cần một quản trị viên (hoặc một người trong chat riêng của chính họ) và một bước xác nhận, và sẽ khởi động lại phiên (cuộc trò chuyện được giữ); rời Chế độ Toàn quyền cũng khởi động lại nó, ngay lập tức. Một phiên bắt đầu ở Chế độ Toàn quyền sẽ khởi động lại ở mọi lần chuyển, kể cả sau khi nó đã được chuyển sang Chế độ Hỏi hay Chế độ Auto trong terminal, một phiên đã báo Chế độ Toàn quyền kể từ khi bắt đầu cũng vậy (một phiên terminal khởi động bằng `--dangerously-skip-permissions`): ở đó Shift+Tab có thể xoay nó trở lại Chế độ Toàn quyền. Với một phiên đang làm việc, việc khởi động lại đưa ra `🛑 Dừng và đổi chế độ` / `⏳ Để sau`. Một thành viên có vai trò thiếu `can_manage` không đổi được chế độ của một phiên có thư mục nằm ngoài [thư mục gốc](groups.md#root-folders) của chat, dù tại chỗ hay có khởi động lại. Chạm một nút chuyển sẽ đổi chính thẻ đó thay vì gửi thêm một tin nhắn. `/perms` là tên cũ và vẫn dùng được như vậy. |
| `/settings` | - | Các phiên của chat này trông và trả lời ra sao: ngôn ngữ aipager dùng, bố cục tin nhắn, xem trước thay đổi, cập nhật thẻ khi việc kéo dài, định dạng, độ dài câu trả lời, mức từ vựng; `/new` bắt đầu phiên với những gì; tùy chọn theo phiên; và, trong chat riêng của chủ sở hữu, các đề nghị gửi báo cáo sự cố. Xem [Cài đặt](#settings). Trong một nhóm ai cũng xem được, còn đổi một cài đặt thì cần quản trị viên. |
| `/language` | - | Ngôn ngữ mà aipager dùng cho tin nhắn và nút của chính nó trong chat này (cùng danh sách với hàng đầu tiên của `/settings`). Mỗi ngôn ngữ được liệt kê bằng tên của chính nó: English, rồi Español, Русский (tiếng Nga), Português (Brasil), 简体中文 (tiếng Trung giản thể), Français, Deutsch (tiếng Đức), 한국어 (tiếng Hàn), العربية (tiếng Ả Rập), Bahasa Indonesia, Українська (tiếng Ukraina), Italiano, Türkçe (tiếng Thổ Nhĩ Kỳ), فارسی (tiếng Ba Tư) và Tiếng Việt. Một chat riêng bắt đầu sau bản phát hành này đi theo ngôn ngữ của ứng dụng Telegram của bạn; một chat đã có từ trước vẫn dùng tiếng Anh cho tới khi bạn chọn (xem [Ngôn ngữ](#languages)). Trong tiếng Tây Ban Nha, tiếng Bồ Đào Nha, tiếng Pháp, tiếng Ý và tiếng Indonesia, các chữ trên bàn phím trả lời vẫn là `status`, `stop`, `new` và `kill`, giống như các lệnh của chúng. Tiếng Đức và tiếng Thổ Nhĩ Kỳ dùng `Status`, `Stop`, `Neu…` và `Beenden…`, và `Özet`, `Stop`, `Oluştur` và `Sonlandır`; các chữ tiếng Anh vẫn dùng được khi gõ. Trong tiếng Ba Tư và tiếng Ả Rập, chữ của aipager đọc từ phải sang trái với chữ số Ba Tư hoặc Ả Rập; tên phiên, lệnh, tên mô hình và mã vẫn giữ nguyên. Danh sách nói rõ nó thay đổi gì: chỉ chữ của chính aipager, ở đây và trong Mini App; các phiên Claude của bạn không đổi, và Claude trả lời bằng ngôn ngữ bạn viết cho nó. Trong một nhóm ai cũng xem được, còn đổi nó thì cần quản trị viên. `language` là tên phiên được giữ riêng. |
| `/whoami` | - | Cho biết bạn là ai ở đây: trong chế độ làm việc nhóm là nhãn của bạn, vai trò của bạn trong chat này và các quy tắc mà vai trò đó áp cho bạn. |
| `/update` | - | Chỉ dành cho quản trị viên (vai trò `owner` và `admin`, hoặc một vai trò có `can_manage`). Kiểm tra aipager và Claude Code có phiên bản mới hơn không, rồi cập nhật cái nào có bằng một nút duy nhất. Xem [Cập nhật](#update). |

Menu `/` hiện các phiên của bạn trước (`/x1 · Trò chuyện với x1`), rồi đến các lệnh hay dùng: `new`, `status`, `stop`, `now`, `resume`, `mode`, `settings`, `language`, `help` (và `app` khi Mini App bật, chỉ trong chat riêng). `kill`, `restart`, `rename`, `delete`, `diff`, `clearqueue`, `perms`, `whoami` và `update` không có trong menu nhưng gõ vào vẫn dùng được; các thao tác trên phiên cũng có trong `/status` → `⋮`.

**Các lệnh tác động lên một phiên** (`/stop`, `/mode`, `/kill`, `/restart`, `/rename`, `/delete`, `/diff`) theo một quy tắc:

- Kèm tên (`/restart x1`), chúng tác động lên phiên đó của chat này (`/rename` cần cả tên cũ lẫn tên mới cho việc này, xem ở trên).
- Không kèm tên, chúng tác động lên phiên duy nhất mà lệnh có thể nói tới (phiên duy nhất đang làm việc với `/stop`, phiên duy nhất đang chạy với `/kill` và `/restart`, phiên duy nhất đã kết thúc với `/delete`), qua bước xác nhận thường lệ nếu có.
- Nếu không, chúng hiện một bộ chọn chỉ gồm các phiên của chat này hợp với lệnh: phiên mà tin nhắn của bạn đang đến đứng đầu, đánh dấu `✍️`, rồi các phiên còn lại theo tên, và `✖️ Hủy`. Chọn một phiên dẫn tới cùng thẻ như khi gõ tên của nó.
- Trong một nhóm, không kèm tên, chúng chỉ tác động lên phiên hiện tại của chính bạn, khi lệnh hợp với phiên đó; nếu không, chúng luôn hiện bộ chọn, kể cả khi chỉ có một phiên, vì phiên đó có thể là của một thành viên khác (xem [nhóm](groups.md#talking-to-a-session-in-a-group)).

`/mode` và `/diff` xem phiên mà tin nhắn của bạn đang đến trước tiên.

### <a id="settings"></a>Cài đặt

`/settings` (và tab Cài đặt của Mini App, nơi hiện cùng các lựa chọn) đặt cách các phiên của chat này trông và trả lời. Mỗi chat có cài đặt riêng; trong một nhóm ai cũng xem được, còn đổi một cài đặt thì cần quản trị viên. Trong chat riêng của chính bạn, bạn tự đổi chúng, bất kể vai trò, trong Telegram và trong Mini App. Một dòng có dấu ✅ là dòng được đặt khác với mặc định (Bố cục tin nhắn luôn hiện lựa chọn của nó).

| Cài đặt | Lựa chọn (mặc định đứng đầu) | Làm gì |
|---|---|---|
| 🌐 Ngôn ngữ | Tự động (ngôn ngữ Telegram của bạn), trong chat riêng; English | Ngôn ngữ của tin nhắn và nút của chính aipager trong chat này và trong Mini App, hiện bằng tên của chính nó (cùng danh sách với `/language`). Nó không thay đổi các phiên Claude của bạn: Claude trả lời bằng ngôn ngữ bạn viết cho nó. Có English, Español, Русский (tiếng Nga), Português (Brasil), 简体中文 (tiếng Trung giản thể), Français, Deutsch (tiếng Đức), 한국어 (tiếng Hàn), العربية (tiếng Ả Rập), Bahasa Indonesia, Українська (tiếng Ukraina), Italiano, Türkçe (tiếng Thổ Nhĩ Kỳ), فارسی (tiếng Ba Tư) và Tiếng Việt; Tự động đi theo ngôn ngữ của ứng dụng Telegram của bạn (xem [Ngôn ngữ](#languages)). |
| 🖼 Bố cục tin nhắn | Thẻ làm việc + câu trả lời riêng; Câu trả lời nằm trong thẻ; Chỉ câu trả lời, xóa thẻ | Một việc đã xong hiện ra thế nào. “Thẻ làm việc + câu trả lời riêng” giữ thẻ phía trên câu trả lời làm bản ghi về cách đi tới câu trả lời; “Câu trả lời nằm trong thẻ” đặt câu trả lời vào thẻ; “Chỉ câu trả lời, xóa thẻ” bỏ thẻ và để câu trả lời đứng một mình. Mặc định là “Thẻ làm việc + câu trả lời riêng” (`KEEP_FINISHED_CARD=0` đặt thành “Chỉ câu trả lời, xóa thẻ” cho các chat chưa bao giờ chọn). Xem [câu trả lời khi việc kết thúc](#idle-responses). |
| 📝 Xem trước thay đổi (diff) | Tắt; Bật | Bật thì đăng mỗi `Write`/`Edit` thành một tin nhắn diff riêng dưới thẻ làm việc. Tắt thì giữ một thẻ và một câu trả lời; thẻ vẫn liệt kê mọi lần sửa, và Mini App có phần xem diff. |
| ⏱ Cập nhật thẻ khi việc kéo dài | Bật; Tắt | Bật thì thẻ làm việc làm mới mỗi 10 giây sau 2 phút của một việc và mỗi 30 giây sau 10 phút (không bao giờ chậm hơn, dù việc kéo dài bao lâu), và khi đó đồng hồ của nó nhảy theo bước 30 giây; Tắt thì giữ nhịp của những phút đầu suốt cả việc (xem [xử lý sự cố](troubleshooting.md#a-long-turns-card-refreshes-less-often)). |
| ✏️ Định dạng đơn giản | Tắt; Bật | Bật thì yêu cầu Claude chỉ viết văn xuôi đơn giản và danh sách gạch đầu dòng: không bảng, không khối mã, không tiêu đề, không chữ đậm. |
| 📏 Độ dài câu trả lời | Không có quy tắc; Rất ngắn; Ngắn; Vừa; Dài | Yêu cầu Claude trả lời với độ dài đó. |
| 🎓 Mức từ vựng | Không có quy tắc; Từ ngữ đơn giản; Bình thường; Nâng cao | Yêu cầu Claude dùng mức từ vựng đó. |
| 🎨 Giao diện | Nút Dừng, Nút Cho phép và Từ chối, Nút App, Menu cài đặt | Các nút của aipager trông thế nào trong chat này; mọi thứ bắt đầu ở Bình thường (giao diện riêng của Telegram). Telegram chỉ cho bot tô một nút màu xanh dương, xanh lá hoặc đỏ, nên: nút Dừng (nút trên thẻ làm việc và nút dưới một câu hỏi hoặc yêu cầu cấp quyền) Bình thường, Xanh dương, Xanh lá hoặc Đỏ; Cho phép và Từ chối Bình thường hoặc Có màu (Cho phép và Luôn cho phép màu xanh lá, Từ chối màu đỏ); nút App (trên tin nhắn được ghim và dưới `/app`) Bình thường, Xanh dương, Xanh lá hoặc Đỏ (nút menu cạnh ô nhập tin nhắn thì không tô màu được); menu cài đặt Bình thường (✅ đánh dấu lựa chọn hiện tại) hoặc lựa chọn hiện tại thêm màu xanh dương. Áp dụng cho cả chat, không theo từng phiên; trong nhóm chỉ quản trị viên đổi được. Các ứng dụng Telegram không hỗ trợ màu sẽ hiện mọi nút bình thường. Tab Cài đặt của Mini App có các lựa chọn y như vậy trong một phần riêng, Giao diện, sau Cài đặt chat, mỗi lựa chọn có một chấm màu. |
| 🆕 Phiên mới | Quyền (chế độ), Mô hình, Thư mục | `/new` bắt đầu phiên với những gì khi bạn không chọn: Chế độ Hỏi, Chế độ Auto (mặc định) hoặc Chế độ Toàn quyền, một mô hình trong danh sách Mô hình hoặc mô hình mặc định của Claude Code, và một thư mục. Chỉ một quản trị viên, hoặc một người trong chat riêng của chính họ với bot, mới chọn được Chế độ Toàn quyền ở đây (màn hình của một chat riêng không hiện nó cho ai khác), và nó không bao giờ áp dụng cho người không được dùng: phiên của họ bắt đầu ở Chế độ Auto. |
| 👤 Tùy chọn theo phiên | một phiên, rồi bất kỳ cài đặt nào ở trên trừ Phiên mới | Giá trị riêng cho một phiên (cũng có trong `/status` → `⋮` → `👤 Tùy chọn`); ⭐ đánh dấu một cài đặt được ghi đè, và “Dùng mặc định của chat” bỏ phần ghi đè. Trang phiên trong Mini App đặt cùng các giá trị ghi đè này. |
| 🐞 Báo cáo sự cố | Hỏi tôi; Tắt | Chỉ có trong chat riêng của chủ sở hữu bản cài. Hỏi tôi cho phép aipager thỉnh thoảng đề nghị gửi báo cáo về một lỗi nội bộ mà nó gặp nhiều hơn một lần (nhiều nhất mỗi 3 ngày một lần); Tắt thì không bao giờ đề nghị (nút “Báo cáo sự cố” trong `/help` vẫn dùng được). Hai lần liên tiếp từ chối đề nghị sẽ tắt các đề nghị, và khi đó dòng này nói rõ, kèm một nút để bật lại. Xem [báo cáo sự cố](problem-reports.md#when-aipager-offers-a-report). |

Độ dài câu trả lời, mức từ vựng và định dạng đơn giản tới Claude dưới dạng một chỉ dẫn ngắn được thêm vào mỗi tin nhắn gửi từ Telegram (do hook thêm, xem [hook](hooks.md#userpromptsubmit)), dùng giá trị ghi đè riêng của phiên nếu có.

Dù bố cục nào, mỗi thẻ làm việc đều kết thúc bằng dòng trạng thái của phiên (`⏳`/`✅ tên · …`) và mỗi câu trả lời đều bắt đầu bằng dòng kết quả của nó (`💬 tên`, thêm `· Xong (…)` khi không còn thẻ đã xong nào để hiện số liệu); bố cục “Câu trả lời nằm trong thẻ” xếp hai dòng chồng lên nhau, mỗi dòng ở phần riêng. Phía trên dòng trạng thái của thẻ làm việc có một đường kẻ phân cách (chữ nhỏ), và trong bố cục “Câu trả lời nằm trong thẻ” một đường thứ hai tách thẻ đã xong khỏi câu trả lời. Trong bố cục có thẻ, câu trả lời cố ý đến sau thẻ đã xong một chút, để bạn thấy thẻ ghi Xong trước khi câu trả lời hiện ra bên dưới; chỉnh hoặc tắt khoảng trễ đó bằng `FINISH_CARD_GRACE_SECONDS` (giây, mặc định 0,8; 0 gửi cả hai cùng lúc). Bố cục tin nhắn quyết định thẻ cho mọi việc, dù việc đó có chạy công cụ hay không.

### <a id="per-session-dynamic-commands"></a>Lệnh riêng của từng phiên

Mỗi phiên đang chạy có một lệnh, được đăng ký theo nhãn của nó:

| Dạng | Làm gì |
|---|---|
| `/<label>` | Làm `<label>` thành phiên mà tin nhắn của bạn sẽ đến. Câu trả lời nói rõ điều đó kèm trạng thái và chế độ của phiên (`✍️ Giờ bạn đang nói chuyện với x1 · 💤 sẵn sàng · 🤖 Chế độ Auto`, rồi “Gửi tin nhắn, nó sẽ đến x1.”), đưa ra **Trả lời** khi phiên đang chờ bạn, và **⋮ Thêm** cho menu của nó. Số liệu đầy đủ có trong `/status`. Trong một nhóm thì nó nói “Trả lời một tin nhắn của x1 (hoặc nhắc đến @aipagerbot) để nói chuyện với phiên đó.” thay vào đó, vì ở đó một tin nhắn thường không tới được bot (xem [nhóm](groups.md#talking-to-a-session-in-a-group)). `/x1@aipagerbot`, thứ mà Telegram gửi khi bạn chọn `/x1` từ menu của một nhóm, cũng hoạt động y như vậy. |
| `/<label> <prompt>` | Gửi `<prompt>` thẳng tới phiên đó. Phiên đó cũng trở thành phiên mà các tin nhắn thường tiếp theo của bạn sẽ đến. |
| `/<label> stop` | Ngắt việc hiện tại của phiên đó. |

Kết quả của `/status` lấy từ cùng dữ liệu mà `aipager status` hiện trên dòng lệnh; bản thân danh sách phiên không cần gọi tới Telegram.

Trên máy tính, `aipager status` và `aipager session ls` cho mỗi phiên một trạng thái: `IDLE`, `BUSY`, `WAITING` (một yêu cầu cấp quyền hay một câu hỏi đang chờ bạn, kèm nội dung khi biết, ví dụ `permission: Bash: make deploy`) hoặc `GONE`. Với `--json`, mỗi phiên còn có `waiting_kind` (`permission` hoặc `question`) và `waiting_summary`, cả hai là `null` trừ khi phiên đang chờ.

## <a id="languages"></a>Ngôn ngữ

aipager nói được English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی và Tiếng Việt, mỗi ngôn ngữ được liệt kê bằng tên của chính nó.

- **Chỉ chữ của chính aipager thay đổi.** Ngôn ngữ áp dụng cho tin nhắn, nút, thẻ và Mini App của aipager. Các phiên Claude của bạn không đổi, và Claude trả lời bằng ngôn ngữ bạn viết cho nó. Tên phiên, lệnh, tên mô hình và mã vẫn giữ nguyên.
- **`/language`** (hoặc hàng đầu tiên của `/settings`) chọn ngôn ngữ cho chat bạn đang ở. Trong chat riêng, đó cũng là ngôn ngữ của riêng bạn: các thông báo nhanh bạn thấy trong nhóm và Mini App của bạn đều dùng nó.
- **Tự động** (chỉ trong chat riêng) đi theo ngôn ngữ của ứng dụng Telegram của bạn, và lại đi theo nếu bạn đổi nó. Một ngôn ngữ mà aipager không có sẽ hiện bằng tiếng Anh.
- **Chat mới và chat cũ.** Một chat riêng bắt đầu sau bản phát hành này đi theo ngôn ngữ của ứng dụng Telegram của bạn cho tới khi bạn chọn một ngôn ngữ, và tin nhắn đầu tiên của nó bằng một ngôn ngữ không phải tiếng Anh sẽ có thêm một dòng ngắn nói điều đó và cách đổi. Một chat đã có từ trước vẫn dùng tiếng Anh. Nếu ứng dụng Telegram của bạn dùng một ngôn ngữ mà aipager nói được, bạn nhận một thẻ đề nghị ngôn ngữ đó, kèm nút `Giữ tiếng Anh`; nó chỉ đến một lần, dù bạn trả lời thế nào.
- **Nhóm** có một ngôn ngữ, do một quản trị viên đặt trong `/language`; cho tới lúc đó nhóm dùng tiếng Anh. Thông báo nhanh sau một lần chạm hiện bằng ngôn ngữ của chính người chạm: lựa chọn của họ trong chat riêng với bot, nếu không thì ngôn ngữ ứng dụng Telegram của họ với người tham gia sau bản phát hành này, nếu không nữa thì ngôn ngữ của nhóm. Xem [nhóm](groups.md).
- **Từ phải sang trái.** Trong tiếng Ba Tư và tiếng Ả Rập, chữ của aipager đọc từ phải sang trái với chữ số Ba Tư hoặc Ả Rập. Thẻ làm việc theo ngôn ngữ của chat, dù Claude viết bằng ngôn ngữ nào: trong chat tiếng Anh thẻ đọc từ trái sang phải, trong chat tiếng Ba Tư hoặc tiếng Ả Rập từ phải sang trái, và một câu của Claude trên thẻ được viết theo chiều ngược lại vẫn đọc theo chiều của nó. Câu trả lời theo chữ của Claude: khi Claude trả lời bằng tiếng Ba Tư hoặc tiếng Ả Rập trong một chat có ngôn ngữ viết từ trái sang phải, cả câu trả lời là một tin nhắn từ phải sang trái, và các dòng của chính aipager trong đó (các agent dưới câu trả lời, và ở bố cục “Câu trả lời nằm trong thẻ” là thẻ đã xong phía trên nó) nằm bên phải như phần còn lại, và vẫn đọc từ trái sang phải.
- **Từ chỉ chế độ.** `/mode` nhận từ chỉ chế độ bằng ngôn ngữ của bạn (`/mode Hỏi`), và `ask`, `auto` cùng `full` luôn dùng được. Nếu một phiên đang chạy có đúng tên đó, từ đó sẽ là tên phiên (xem `/mode` ở trên).
- **Mô tả của bot.** Khi khởi động, aipager ghi mô tả của bot (dòng chữ trên màn hình chat trống) và mô tả ngắn bằng từng ngôn ngữ này, cho những người có Telegram dùng ngôn ngữ đó. Telegram hiện chữ đó thay cho chữ bạn viết trong BotFather, nên aipager không đặt gì khi bạn đã có chữ của riêng mình (mô tả và mô tả ngắn được kiểm tra riêng). Nó không bao giờ ghi đè chữ của bạn và không bao giờ đổi mô tả tiếng Anh hay mô tả mặc định. Khi chữ hiện tại của nó đã được đặt ở mọi ngôn ngữ, một lần khởi động không hỏi Telegram gì cả. Nếu không, trước tiên nó đọc chữ của bạn trong BotFather (một lần đọc cho mô tả và một lần cho mô tả ngắn): nếu bạn có, nó dừng ở đó, nên khi có chữ của riêng bạn, mỗi lần khởi động chỉ tốn những lần đọc đó, không hơn. Nếu bạn không có, nó đọc từng ngôn ngữ chưa có chữ hiện tại của nó, nên một ngôn ngữ mà bạn đã viết chữ riêng sẽ được đọc lại ở mỗi lần khởi động. Nếu bạn viết mô tả trong BotFather sau khi aipager đã đặt mô tả của nó, người đọc bằng các ngôn ngữ này vẫn thấy mô tả của aipager (xem [xử lý sự cố](troubleshooting.md#the-bot-description-is-not-the-one-i-wrote)).

## <a id="the-mini-app"></a>Mini App

`/app` (và nút menu của Telegram) mở một bảng điều khiển do chính aipager cung cấp. Nó bật sẵn theo mặc định; quản lý nó bằng `aipager miniapp enable|disable|status`. Mọi yêu cầu đều được kiểm tra với chữ ký `initData` của Telegram (xem [bảo mật → tunnel của Mini App](security.md#mini-app-tunnel)). Trang không tải gì từ nơi khác: không phông chữ, hình ảnh hay script nào từ trang web khác.

- **Chat.** Ứng dụng hiện mỗi lần một chat. Nếu bạn thuộc về nhiều chat (giả sử chat riêng của bạn và một nhóm), một hàng tên chat ở trên cùng giúp chuyển qua lại; ứng dụng mở ở chat riêng của bạn và nhớ lựa chọn gần nhất trên điện thoại đó. Mọi thứ bên dưới là của chat đang chọn, với vai trò của bạn trong đó. Khi chỉ có một chat, hàng này không hiện. Xem [nhóm](groups.md).
- **Phiên.** Một câu tóm tắt chat, ví dụ “1 đang chờ bạn, 2 đang làm việc, 3 sẵn sàng”, với số tiền đã tiêu bên dưới. Mỗi phiên đang chạy là một ô có vòng ngữ cảnh, thư mục, mô hình và chi phí của nó; các phiên đang làm việc sáng lên bằng màu nhấn. Các phiên đã xong nằm trên kệ **Đã kết thúc** có thể thu gọn. Nút **+** bắt đầu một phiên mới.
- **Đang chờ bạn.** Một phiên đang chờ một yêu cầu cấp quyền hay một câu hỏi nằm trong một khay màu hổ phách ở trên cùng, cho thấy nó đang hỏi gì. **Trả lời trong chat** gửi lại yêu cầu đó, kèm các nút của nó, xuống cuối chat, giống như nút **Trả lời** trên thanh được ghim, và bạn trả lời ở đó. Nếu đó là phiên duy nhất đang chờ, ứng dụng đóng lại để yêu cầu ở ngay trước mặt bạn; nếu không, nó vẫn mở và báo “Đã gửi vào chat”. Việc này cần quyền gửi tin nhắn cho phiên, và một bản sao được chạm sau khi yêu cầu đã được trả lời hoặc bị thay sẽ báo “đã được trả lời rồi”.
- **Trang của một phiên.** Phần đầu hiện vòng ngữ cảnh, trạng thái (“Đang làm việc (3:12)”, “Đang chờ bạn (quyền)”, “Sẵn sàng, hoạt động lần cuối 5 phút trước”, “Đã kết thúc”) và nút **Dừng** một chạm (khi đang làm việc hoặc đang chờ) hoặc **Tiếp tục** (khi đã kết thúc). Bên dưới: yêu cầu đang chờ cùng **Trả lời trong chat**, vài lần gọi công cụ gần nhất thành một dải (chạm vào để xem dòng thời gian đầy đủ), câu trả lời mới nhất, các thông tin, ô chọn mô hình, cài đặt riêng của phiên, các file đã đổi và dòng thời gian. Menu ⋮ có mọi thao tác, với cùng các bước xác nhận như trước.
- **Phiên mới.** Một thẻ có hướng dẫn từng bước: tên, rồi nơi chạy (với các gợi ý **Gần đây** cho những thư mục mà các phiên của bạn dùng, mới nhất trước), rồi mô hình (với các gợi ý **Gợi ý**), rồi **Thêm tùy chọn** cho chế độ quyền và kiểu trả lời. Nút của chính Telegram ở dưới cùng ghi **Bắt đầu phiên**. Với một thành viên có vai trò thiếu `can_manage`, “nơi chạy” chỉ đưa ra [thư mục gốc](groups.md#root-folders) của chat và các thư mục nằm ngay bên trong nó (thư mục gốc là mặc định, và “Thư mục mới” tạo thư mục bên trong thư mục đã chọn); một thư mục nằm ngoài sẽ bị từ chối kèm lý do, và Tiếp tục, Khởi động lại cùng việc đổi chế độ của một phiên đang làm việc bên ngoài nó đều trả lời “Phiên này làm việc bên ngoài thư mục của bạn ~/..., nên vai trò của bạn không được tiếp tục, khởi động lại hay đổi chế độ của nó. Hãy nhờ quản trị viên.”
- **Cài đặt.** **Ngôn ngữ ứng dụng** đứng đầu: ngôn ngữ của riêng bạn cho chữ của aipager trong ứng dụng và trong chat riêng của bạn (tiếng Anh, tiếng Tây Ban Nha, tiếng Nga, tiếng Bồ Đào Nha (Brasil), tiếng Trung (giản thể), tiếng Pháp, tiếng Đức, tiếng Hàn, tiếng Ả Rập, tiếng Indonesia, tiếng Ukraina, tiếng Ý, tiếng Thổ Nhĩ Kỳ, tiếng Ba Tư hoặc tiếng Việt; Tự động đi theo ngôn ngữ của ứng dụng Telegram của bạn, xem [Ngôn ngữ](#languages)). Ai cũng đổi được của riêng mình, nó không bao giờ được đăng trong chat, và không thay đổi các phiên Claude của bạn. Sau đó là các tùy chọn của chat (trong một nhóm chỉ quản trị viên đổi được; trong chat riêng của chính bạn thì bạn đổi được, bất kể vai trò, giống như `/settings` ở đó) và, với một quản trị viên, **Cập nhật**, phần phản ánh [`/update`](#update). Với chủ sở hữu trong chat riêng của họ, **Báo cáo sự cố** mở một trang riêng: viết ghi chú, xem lại báo cáo, gửi đi và xem mã tham chiếu mà không cần rời ứng dụng (xem [báo cáo sự cố](problem-reports.md#how-to-send-one)).

Ứng dụng đi theo giao diện sáng hoặc tối của Telegram khi bạn đổi, và dừng các hiệu ứng chuyển động nhẹ khi điện thoại của bạn yêu cầu giảm chuyển động. Mọi thứ trong Mini App cũng làm được từ chat: menu ⋮ trên bảng của một phiên có cùng các thao tác.

### <a id="switching-a-running-sessions-model"></a>Đổi mô hình của một phiên đang chạy

Trang của một phiên đang chạy có ô chọn **Mô hình**, hiện mô hình mà phiên báo về (từ dòng trạng thái của Claude Code) và cùng danh sách mà bộ chọn lúc khởi động và bàn phím Mô hình của chat đưa ra. Chọn một mô hình sẽ gõ đúng `/model <name>` vào phiên đó, cùng cách gõ mà bàn phím Mô hình của chat dùng, với cùng quy tắc về ai được làm (bất kỳ ai được gửi tin nhắn cho phiên). Ô chọn ghi **đang đổi…** cho tới khi dòng trạng thái báo một mô hình khác, rồi hiện mô hình đó; nếu không có gì thay đổi trong 15 giây, nó ghi **chưa xác nhận (hãy kiểm tra phiên)**.

- **Chỉ khi phiên đang sẵn sàng, không phải khi Claude đang làm việc hay đang có một yêu cầu mở.** Claude Code chạy `/model` ngay lập tức, kể cả giữa một việc, chứ không phải sau khi việc xong, nên lần đổi bị từ chối cho tới khi việc kết thúc. Bàn phím trong chat cũng từ chối trong cùng những trạng thái đó, với cùng lời lẽ.
- **Câu hỏi “Switch model?” của Claude Code.** Claude Code thường hỏi câu này trước khi đổi, vì câu trả lời tiếp theo phải đọc lại cả cuộc trò chuyện. Với Claude Code 2.1.251 trở lên, hook `PreModelSwitch` của aipager chấp thuận lần đổi mà aipager vừa gõ, nên câu hỏi không xuất hiện (xem [hook](hooks.md#premodelswitch)). Mọi lần đổi khác vẫn hỏi. Với Claude Code cũ hơn, hoặc khi đổi sang hay từ `opusplan`, câu hỏi vẫn có thể xuất hiện. Khi đó lần đổi hiện là *chưa xác nhận* và câu hỏi chờ trong terminal. Hãy trả lời ở đó trước khi gửi thêm gì cho phiên, nếu không tin nhắn tiếp theo của bạn sẽ bị gõ vào câu hỏi đó. Cũng vì lý do đó, aipager từ chối thêm một lần đổi nữa cho phiên đó trong một phút sau một lần đổi chưa xác nhận, hoặc cho tới khi phiên báo một mô hình mới.

## <a id="the-pinned-status-bar"></a>Thanh trạng thái được ghim

Mỗi chat mà aipager nói chuyện (chat riêng của bạn, và mọi scope nhóm) có một tin nhắn được ghim: thanh trạng thái. Dải ghim của Telegram ở đầu chat hiện tin nhắn đó từ **dòng đầu tiên** trở đi, các dòng được nối liền, nên dòng đó nói điều cần bạn nhất:

| Dòng đầu tiên | Khi nào |
|---|---|
| `⏳ jim đang chờ bạn - Bash: make deploy` | một phiên đang chờ một yêu cầu cấp quyền, một câu hỏi hay một lời nhắc tương tác; `(+2 nữa)` khi còn phiên khác cũng đang chờ |
| `⚙️ jim (đang làm việc)` / `💤 jim (sẵn sàng)` | chat có một phiên đang chạy và nó đang làm việc / sẵn sàng (`🔄 jim (đang khởi động)` trong lúc khởi động): dòng này là toàn bộ thanh trạng thái, ngoài một dòng về giới hạn tốc độ |
| `⚙️ 2 đang làm việc · 1 sẵn sàng` | nhiều phiên, không phiên nào đang chờ: bao nhiêu phiên ở mỗi trạng thái (`đang làm việc`, `sẵn sàng`, `đang khởi động`); `🔄` khi không phiên nào đang làm việc và có một phiên đang khởi động, `💤` khi tất cả đều sẵn sàng |
| `💤 tất cả sẵn sàng` | không có phiên nào đang chạy |

Bên dưới, chỉ trong lúc nó còn áp dụng, là một dòng về giới hạn tốc độ (`🐢 gửi chậm hơn sau cảnh báo của Telegram` (sáu tiếng sau một lỗi 429) hoặc `⏸ thẻ tạm thời không cập nhật (giới hạn mỗi giờ của Telegram)` / `(giới hạn của Telegram)` (chế độ tối thiểu, xem [xử lý sự cố](troubleshooting.md#the-hourly-budget))), rồi, khi chat có nhiều hơn một phiên đang chạy, `✍️ Tin nhắn sẽ đến jim` (phiên mà một tin nhắn thường trong chat này sẽ đến, xem bên dưới; không có trong nhóm, nơi mỗi người có đích riêng) và mỗi phiên một dòng kèm trạng thái: `đang làm việc`, `đang chờ bạn`, `sẵn sàng` hoặc `đang khởi động`. Thanh trạng thái nêu tên mỗi phiên một lần: phiên đang chờ mà dòng đầu đã nêu tên không có dòng riêng, và dòng đầu không bao giờ liệt kê những tên mà các dòng bên dưới lặp lại. Một phiên vẫn còn agent chạy nền nói số lượng trên dòng của nó (hoặc trên dòng duy nhất, khi chỉ có một phiên): `💤 jim (sẵn sàng, 1 agent đang làm việc)`. Các lệnh Claude chạy nền cũng được đếm như vậy: `(sẵn sàng, 1 lệnh chạy nền đang chạy)`, hoặc `(sẵn sàng, 2 agent và 1 lệnh chạy nền đang chạy)` khi có cả hai. Đó luôn là một con số, không bao giờ là tên của chúng, nên thanh chỉ đổi khi con số đổi. Chạm vào thanh để nhảy tới tin nhắn.

**Một tin nhắn thường sẽ đến đâu.** Một tin nhắn không phải là tin trả lời sẽ đến phiên mà chat này nói chuyện gần nhất: phiên bạn chuyển tới gần nhất bằng `/<label>`, đã gửi tin nhắn tới, đã bắt đầu, hoặc phiên vừa trả lời bạn ở đây. Một `/mode`, `/clearqueue`, `/diff` hoặc `/now` không kèm tên cũng tác động lên chính phiên đó (`/stop` dừng phiên nào đang làm việc). Mỗi chat có đích riêng: một tin nhắn trong một chat không bao giờ đi tới một phiên thuộc về chat khác. Trong một nhóm, chỉ một tin trả lời bot, một tin nhắn nhắc đến bot, một lệnh hoặc một lần chạm trên bàn phím của nhóm mới được tính (xem [nhóm](groups.md#what-the-bot-reads-in-a-group)), và mỗi người có đích riêng, không bị câu trả lời của một phiên làm thay đổi (xem [nhóm](groups.md#talking-to-a-session-in-a-group)).

Các nút trên tin nhắn được ghim:

- **Trả lời &lt;label&gt;** (mỗi phiên đang chờ một nút, tối đa ba) gửi lại yêu cầu của phiên đó, kèm các nút trả lời, xuống cuối chat, để bạn trả lời mà không cần cuộn. Nếu trong lúc đó yêu cầu đã được trả lời, bạn nhận một thông báo nhanh “đã được trả lời rồi”, và một bản sao (hoặc chính yêu cầu gốc) được chạm sau khi yêu cầu đã được trả lời ở chỗ khác cũng bị từ chối như vậy: nó không bao giờ trả lời một yêu cầu đến sau. Sau khi aipager khởi động lại, yêu cầu đang chờ vẫn dùng được từ thẻ làm việc hay từ tin nhắn riêng của nó, nhưng aipager không còn biết một bản sao cũ hơn đã hiện yêu cầu nào, nên chạm vào một bản sao mà thanh đã gửi lại trước khi khởi động lại sẽ bị từ chối với “yêu cầu này không còn mở”. Ai được trả lời yêu cầu thì được dùng nút này; người khác thì không.
- **📱 App** mở Mini App, trong chat riêng của bạn, trong lúc Mini App đang chạy.

Thanh không có đồng hồ, chi phí, % ngữ cảnh hay mô hình, nên nó chỉ đổi khi trạng thái của một phiên đổi. aipager sửa nó (lặng lẽ, không thông báo) chỉ khi những gì nó hiện đã thay đổi, nhiều nhất mỗi 30 giây một lần cho mỗi chat (`PINNED_MIN_EDIT_GAP`); một thay đổi trong khoảng đó được hiện khi khoảng đó kết thúc, không bao giờ bị mất. Nó không bao giờ được sửa trong lúc chat đang bị tắt gửi, và sẽ cập nhật bù khi lệnh tắt gửi hết.

Trong một nhóm, bot cần quyền quản trị để ghim. Nếu không được phép ghim, aipager xóa tin nhắn trạng thái vừa gửi (để nó không bao giờ nằm lại không được ghim trong lịch sử của nhóm) và không hiện thanh trạng thái trong nhóm đó cho tới khi aipager khởi động lại; một chat từ chối hẳn bot (đuổi bot ra, chặn bot) cũng vậy. Nếu bạn xóa tin nhắn được ghim, aipager gửi và ghim một tin mới, nhiều nhất mỗi giờ một lần cho mỗi chat.

## <a id="persistent-keyboard"></a>Bàn phím cố định

Một bàn phím cố định nằm dưới ô nhập của chat. Các hàng, từ trên xuống:

| Hàng | Nút | Ghi chú |
|---|---|---|
| Phiên | mỗi nhãn phiên đang chạy một nút | tự tạo từ danh sách phiên |
| Thao tác | `phiên`, `dừng`, `mới` | lối tắt bằng chữ (`tắt` vẫn dùng được khi gõ; kết thúc một phiên nằm trong `/status` → `⋮`) |
| Điều hướng | `Mẫu`, `Lệnh` | Mini App mở từ nút menu cạnh ô nhập tin nhắn hoặc bằng `/app`, không mở từ bàn phím này |

`Mô hình ›` nằm bên trong menu con Lệnh. Chạm vào một mục của menu con sẽ gửi một tin nhắn soạn sẵn hoặc một lệnh slash:

- **Mẫu**: những tin nhắn bạn thấy mình gõ đi gõ lại, ví dụ “Viết test”, “Giải thích kế hoạch”, “Cập nhật bộ nhớ”.
- **Lệnh**: các lệnh slash mà Claude Code tự xử lý (`/compact`, `/clear`, v.v.), được gõ vào ngay. Một lệnh hay một mẫu nêu tên phiên mà nó đã đến (`🧹 Đã gửi /clear đến x1`). “Xóa cuộc trò chuyện” bị từ chối trong lúc phiên đang làm việc (`⚠️ Không thể xóa cuộc trò chuyện khi phiên đang làm việc`).
- **Mô hình**: đổi nhanh mô hình cho phiên đang chọn. Có các bí danh theo dòng (`sonnet`, `opus`, `haiku`, `fable`, `opusplan`), luôn chỉ mô hình mới nhất của dòng đó, và các mô hình được ghim (`claude-opus-5-5`, `claude-opus-5-5[1m]` với ngữ cảnh 1M, `claude-sonnet-5-5`, `claude-fable-5-1`, `claude-haiku-5-5`). Bộ chọn lúc khởi động và trang phiên của Mini App dùng cùng danh sách này. Một lần đổi bị từ chối trong lúc phiên đang làm việc hoặc đang có một yêu cầu mở. Sau đó câu trả lời `🔄` đổi thành mô hình mà phiên báo về, hoặc nói rằng lần đổi chưa được xác nhận (xem [Đổi mô hình của một phiên đang chạy](#switching-a-running-sessions-model)).

Để thay bố cục mặc định, hãy ghi `~/.config/aipager/keyboard.json`:

```json
{
  "templates": [{"label": "Deploy",  "prompt": "Deploy to staging"}],
  "commands":  [{"label": "Compact", "send": "/compact"}],
  "models":    [{"label": "Sonnet",  "send": "/model sonnet"}]
}
```

Mỗi phần độc lập với nhau: phần nào thiếu thì lấy theo mặc định có sẵn, nên bạn có thể thay một phần mà không cần ghi các phần khác. JSON bị hỏng sẽ bị bỏ qua kèm một cảnh báo trong log. Thay đổi cần khởi động lại aipager.

## <a id="per-message-inline-buttons"></a>Nút dưới từng tin nhắn

Đa số câu trả lời của bot đều có các nút hợp với tình huống:

### <a id="permission-prompts"></a>Yêu cầu cấp quyền

Khi Claude muốn chạy một công cụ cần được duyệt, thẻ làm việc trở thành một yêu cầu cấp quyền:

```
🔐 Bash: List the temp directory
ls -la /tmp

  [✅ Cho phép]  [❌ Từ chối]
  [🟢 Luôn cho phép]  [⏹ Dừng]
```

Thẻ hiện lệnh (hoặc đường dẫn file) thật mà Claude xin chạy, dưới phần mô tả của chính Claude: hãy duyệt những gì bạn đọc hiểu được. Một ký tự có thể thay đổi thứ tự vẽ chữ (một ký tự điều khiển hướng của Unicode như U+202E, có thể làm một lệnh trông như một lệnh khác) được hiện thành một dấu như `⟨U+202E⟩`, và khi đó thẻ thêm dòng `⚠️ Nội dung này có ký tự ẩn làm đổi hướng chữ (hiển thị là ⟨U+...⟩). Thứ thực sự chạy có thể khác với những gì bạn thấy.`

- **Cho phép**: duyệt lần gọi này.
- **Từ chối**: từ chối nó; Claude không chạy công cụ đó.
- **Luôn cho phép**: duyệt và thêm quy tắc lâu dài mà Claude đề xuất (“đừng hỏi lại về …”). Nút này **chỉ xuất hiện khi Claude đề xuất một quy tắc như vậy**; với một lệnh mà nó không tạo được quy tắc (đa số lệnh ghép), thẻ có Cho phép / Từ chối / Dừng thay vào đó, cũng như với một lần truy cập file chỉ đọc (`Read`, `Grep`, `Glob`) bên ngoài thư mục làm việc của phiên. Claude Code 2.1.259 trở lên đặt vào chỗ đó một dòng “chuyển sang chế độ auto” trong hộp thoại Bash của nó, một dòng “từ giờ chặn việc đọc bên ngoài các thư mục làm việc” trong hộp thoại đọc bên ngoài, và, với một Write/Edit mà nó không tạo được quy tắc cho từng file, một lần đổi chế độ quyền như tự động chấp nhận mọi lần sửa file (`acceptEdits`); aipager không bao giờ chọn những dòng này; hãy chủ động đổi chế độ bằng `/mode`.
- **Dừng**: ngắt việc thay vì trả lời.

Câu trả lời đi tới Claude Code qua hook xin quyền của aipager, hook này chờ nó tối đa 20 giây. Một yêu cầu đến trước thẻ làm việc (khi đó nó được gửi thành tin nhắn riêng, chỉ có Cho phép và Từ chối) cũng được trả lời theo cách đó. Khi thời gian chờ đó đã hết, các nút sẽ gõ câu trả lời vào hộp thoại riêng của Claude Code; khi đó Từ chối chọn dòng cuối cùng của hộp thoại (“No, and tell Claude what to do differently”), dòng này kết thúc việc của Claude giống như Dừng, và thẻ ghi `🚫 jim · Đã từ chối`.

Mọi lần chạm đều được ghi vào `~/.claude/aipager-audit.jsonl` và được lặp lại thành một câu trả lời một dòng nằm dưới thẻ làm việc: `✅ jim · Đã cho phép · @alice · Bash: List the temp directory` (mô tả của lần gọi, hoặc lệnh của nó khi không có mô tả; với một chat được thiết lập bằng `aipager config`; ở chế độ cá nhân thì chỉ `Đã cho phép`).

Trong lúc một yêu cầu (hoặc một AskUserQuestion) đang chờ bạn, phiên được hiện là đang chờ, không bao giờ là sẵn sàng. Nếu trong lúc chờ đó Claude Code nhắc rằng đầu vào đang im lặng, chat nhận `⬆️ jim · vẫn đang chờ bạn trả lời ở trên` dưới dạng tin trả lời yêu cầu đó, mỗi lần chờ một lần.

Các hộp thoại `AskUserQuestion` hiện theo cùng cách, với một nút cho mỗi phương án (bốn phương án đầu), và với một câu hỏi cho phép chọn nhiều đáp án thì có các ô chọn ⬜/☑ và một nút `✅ Gửi`. Câu hỏi và mô tả của từng phương án được hiện đầy đủ, như trong terminal. Chỉ một biểu mẫu quá dài so với một tin nhắn Telegram mới bị rút gọn: thẻ làm việc hiện ít dòng công cụ hơn trước, rồi những đoạn dài nhất trong câu hỏi và các mô tả được cắt ở ranh giới từ và kết thúc bằng `…`.

**Nhiều hơn một yêu cầu.** Claude Code có thể chờ hai yêu cầu cùng lúc: một agent chạy nền xin quyền trong khi Claude hỏi bạn một câu, hoặc ngược lại. aipager hiện cả hai: yêu cầu đầu tiên trên thẻ làm việc (hoặc thành tin nhắn riêng), mỗi yêu cầu đến sau thành tin nhắn riêng bên dưới thẻ, với các nút riêng. Phiên được hiện là đang chờ cho đến khi yêu cầu cuối cùng được trả lời, và tin nhắn bạn gửi trong lúc đó được giữ lại đến lúc ấy. Bạn trả lời theo thứ tự nào cũng được. Cho phép và Từ chối đi qua hook xin quyền khi hook còn chờ, và hook tới đúng yêu cầu đó, dù nó ở đâu. Một lần chạm cần gõ phím (một lựa chọn của câu hỏi, hoặc Cho phép và Từ chối khi hook đã thôi chờ) chỉ gõ vào yêu cầu mà Claude Code hiện trước: Claude Code mỗi lúc chỉ hiện một hộp thoại, và phím đi vào hộp thoại trên cùng. Nếu không, lần chạm không gõ gì, giữ nguyên các nút và báo `Claude Code đang hiện một yêu cầu khác trước, nên hãy trả lời yêu cầu đó trước rồi mới tới yêu cầu này.`, hoặc khi aipager không biết thứ tự (ví dụ một agent mà nó không thấy lúc bắt đầu) `aipager không biết Claude Code đang hiện yêu cầu nào trước, nên hãy trả lời yêu cầu này trong terminal.` Nếu yêu cầu trên thẻ được trả lời trước, thẻ cho thấy nó đang chờ, chỉ còn nút Dừng, và các yêu cầu khác vẫn giữ nút của mình. Nếu Claude kết thúc việc trong khi yêu cầu của một agent vẫn còn mở, câu trả lời vẫn đến như thường và phiên tiếp tục chờ yêu cầu đó, được gửi lại thành tin nhắn riêng. Khởi động lại khi đang mở hai yêu cầu trở lên sẽ không đưa yêu cầu nào trở lại: các nút của chúng trả lời `đã được trả lời rồi` và không gõ gì, và bạn vẫn có thể trả lời các yêu cầu trong terminal. Một yêu cầu đang mở vẫn trở lại như trước.

Khi ngữ cảnh đạt 80%, chat nhận `⚠️ name · Ngữ cảnh đã đầy 82% - sắp tự động thu gọn` kèm **📦 Thu gọn ngay**, nút này gửi `/compact` tới phiên đó. Cảnh báo này đến một lần cho mỗi cuộc trò chuyện: thu gọn không làm nó quay lại, còn một cuộc trò chuyện mới (ví dụ sau `/clear`) sẽ nhận lại nó.

### <a id="stale-buttons"></a>Nút cũ

Các nút tác động lên một việc đang chạy (Dừng, Kết thúc, Khởi động lại, nút chuyển của `/mode` cùng nút “Dừng và đổi chế độ” của nó, “Thay thế” của `/new`) gắn với đúng việc mà chúng được hiện ra cho. Một nút sót lại từ một việc trước không đổi gì, thay vì tác động lên việc đang chạy bây giờ, và một nút được hiện trước khi phiên kết thúc rồi được tiếp tục hay khởi động lại cũng vậy. Kết thúc, Khởi động lại, nút chuyển của `/mode`, và Dừng trong `/status` hay trong bộ chọn của `/stop` trả lời `x1 đã làm việc mới - …` kèm những gì cần chạm hoặc gửi lại (hoặc `Phiên đó đã kết thúc.`); Dừng trên thẻ làm việc trả lời `Việc đó đã xong - …`, và “Dừng và đổi chế độ” của `/mode` đưa thẻ của nó trở lại thành thẻ `/mode` dưới dòng `⚠️ x1 đã bắt đầu việc mới, nên không có gì thay đổi.` “Thay thế” của `/new` báo `Trong lúc đó x1 đã bắt đầu làm việc. Gửi lại /new để thay nó.` khi phiên đã bắt đầu một việc sau khi thẻ được hiện ra.

Các nút của một yêu cầu gắn với đúng yêu cầu đó. Khi nó đã được trả lời, trong Telegram hay trong terminal, các nút trả lời `đã được trả lời rồi` và không gõ gì, kể cả khi một yêu cầu khác của phiên vẫn còn mở.

Một nút `⚡ Gửi ngay` chỉ tác động lên tin nhắn mà nó nằm dưới: nếu Claude đã nhận tin nhắn đó, lần chạm trả lời `Claude đã nhận rồi`, không gửi gì và gỡ các dòng hàng đợi.

### <a id="idle-responses"></a>Câu trả lời khi việc kết thúc

Khi một việc kết thúc, câu trả lời đến theo bố cục tin nhắn của bạn (xem bên dưới và [Cài đặt](#settings)). Nếu câu trả lời của Claude dài hơn mức một tin nhắn chứa được (khoảng 32 KB), tin nhắn hiện phần vừa đủ dưới dòng `📎 Câu trả lời đầy đủ ở file bên dưới ↓`, và cả việc (các bước của nó cùng câu trả lời đầy đủ) đến sau dưới dạng `<name>_full_log.md`, file mà Telegram hiện được mà không cần ứng dụng khác. Cùng file đó được đính kèm khi thẻ làm việc phải bỏ bớt dòng.

Một việc kết thúc vì lỗi API của Anthropic (hết tín dụng, quá tải, giới hạn số yêu cầu, mạng) hiện `⚠️ name · <what went wrong>` thay cho câu trả lời, kèm **🔄 Thử lại**, nút này gửi lại tin nhắn gần nhất tới cùng phiên. Trong lúc chat [đang bị tắt gửi](troubleshooting.md#the-bot-went-quiet-flood-control), chạm vào nó không làm gì cả (tin nhắn không được gửi lại và nút vẫn còn), nên hãy chạm lại khi lệnh chặn đã hết.

Trong bố cục có thẻ (“Thẻ làm việc + câu trả lời riêng”), thẻ đã xong luôn nằm trên câu trả lời, với mọi việc, làm bản ghi về cách đi tới câu trả lời: các dòng công cụ, dòng agent và những gì Claude nói giữa chúng, hoặc chỉ `✅ name · Xong · Ns` với một việc không chạy công cụ nào. Câu trả lời đến thành một tin nhắn riêng (có thông báo). Để mỗi việc chỉ có một tin nhắn, hãy chọn “Câu trả lời nằm trong thẻ” (câu trả lời đi vào thẻ) hoặc “Chỉ câu trả lời, xóa thẻ” (thẻ bị xóa và câu trả lời đứng một mình).

Mỗi việc có đúng một thẻ làm việc. Một tin nhắn bạn gửi trong lúc một việc đang chạy không bao giờ thêm thẻ thứ hai cho việc đó, và một thẻ mà việc của nó đã kết thúc không còn giữ nút Dừng: nếu không có gì đóng nó lại (một hook Stop bị mất, một lần khởi động lại), aipager đóng nó sau 30 giây, theo bố cục của bạn. Sau khi aipager khởi động lại, một thẻ có việc vẫn đang chạy được giữ lại và chạy tiếp ngay, thời gian tiếp tục đếm từ lúc việc bắt đầu, và phiên được hiện là đang làm việc cho tới khi việc kết thúc; mọi thẻ khác còn sót từ trước lần khởi động lại đều được đóng (“aipager đã khởi động lại”, bỏ nút Dừng). Một phiên đang chờ một yêu cầu cấp quyền hay một câu hỏi đơn của Claude sẽ quay lại ở trạng thái đang chờ, không phải đang làm việc: thẻ của nó và thanh được ghim nói rằng nó đang chờ bạn và các nút của yêu cầu vẫn trả lời được, trừ khi cuộc trò chuyện đã lưu cho thấy trong lúc đó nó đã được trả lời trong terminal. Những câu hỏi có nhiều phần hay nhiều lựa chọn, và các yêu cầu do một subagent đặt ra, được để cho terminal. Một khi biết chắc một yêu cầu đã xong (được trả lời bằng một lần chạm hay trong terminal, việc của nó đã kết thúc, hoặc không được mang lại sau khi khởi động lại vì trong lúc đó đã được trả lời hay không lưu và đọc lại được), các nút trả lời cũ của nó trả lời “đã được trả lời rồi” và không gõ gì, kể cả sau khi khởi động lại. Một yêu cầu không ai trả lời vẫn mở chừng nào Claude còn chờ nó. aipager chỉ thôi chờ khi Claude không báo gì trong 5 phút và cuộc trò chuyện đã lưu cho thấy yêu cầu đã qua (tin về một câu trả lời bị mất); một yêu cầu như vậy không được coi là đã xong, nên các nút của nó vẫn trả lời hộp thoại. Tin được tính chỉ là tin từ chính Claude và từ agent đã hỏi: một agent chạy nền tự làm việc của nó không được tính, và cuộc trò chuyện đã lưu không nói gì về yêu cầu của một agent; yêu cầu đó chờ tin từ chính agent ấy.

Một việc mà Claude **tự** bắt đầu (một agent chạy nền báo kết quả về bằng `<task-notification>` khi không có công việc nào đang mở) chỉ có thẻ làm việc khi nó thực sự làm gì đó: ở lần gọi công cụ đầu tiên, hoặc sau 15 giây, tùy cái nào đến trước. Đa số những lần tự thức dậy như vậy chỉ là vài giây “không có gì mới”; giờ chúng chỉ hiện câu trả lời. Dấu hiệu “đang nhập…” vẫn hiện trong lúc nó chạy, và bản thân câu trả lời luôn được gửi: không có gì bị lọc đi vì bị coi là không quan trọng. Những việc bạn bắt đầu, từ Telegram hay từ terminal, vẫn có thẻ ngay lập tức.

Trong lúc một phiên đang bận, mỗi agent chạy nền mà Claude khởi động (qua công cụ `Agent` của Claude Code, `Task` ở các phiên bản cũ) có một dòng riêng trên thẻ làm việc: `🤖 <type> · <activity> · <elapsed>`, cho thấy loại agent và việc nó đang làm, được làm mới khi các lần gọi công cụ của chính nó đến. Khi agent đó đã gọi công cụ ba lần trở lên, các lần gọi đó được gộp vào một nút `▸ N công cụ đã chạy` riêng ngay bên dưới dòng của nó; chúng không bao giờ xuất hiện trong dòng thời gian của việc chính hay trong các số đếm `Bash ×N` của nó. Khi agent xong, dòng của nó được chốt lại thành `✅ 🤖 <type> · N công cụ đã chạy · <elapsed>` và giữ nguyên nút đó. Một agent do agent khác khởi động (một agent phụ) nêu tên agent ở đầu chuỗi của nó trước loại của chính nó, `🤖 pipeline-runner › ship-tester`, khi aipager biết ai đã khởi động nó vào lúc nó bắt đầu; các dòng vẫn theo thứ tự các agent đã bắt đầu. File đính kèm `.md` đầy đủ ở trên có thêm một phần Agent, liệt kê mọi agent đã chạy trong việc đó, thời gian chạy, số công cụ và các công cụ nó đã gọi.

Trong lúc các agent chạy nền vẫn đang chạy sau khi việc của chúng đã kết thúc, thẻ vẫn ở đó làm trạng thái của công việc. Khi Claude nhận một tin nhắn mới trong lúc đó, thẻ đang hoạt động là thẻ nằm dưới tin nhắn đó, vẫn hiện các agent đang chạy. Một tin nhắn bạn gửi tới phiên đang sẵn sàng sẽ bắt đầu một việc mới: thẻ của việc trước được chốt lại ở chỗ của nó, các bước được giữ, và việc mới có thẻ riêng. Một tin nhắn đã xếp hàng rồi được nhận sẽ nối tiếp việc của công việc: thẻ của nó được gửi lại dưới tin nhắn đó và bản cũ bị xóa. Các lần gọi công cụ bên trong một agent không bao giờ hiện thành dòng riêng của việc chính, và không bao giờ khiến aipager nghĩ rằng một việc mới đã bắt đầu.

Một lệnh Claude chạy nền (`run_in_background`, Ctrl+B, hoặc lệnh mà Claude Code chuyển xuống chạy nền khi nó chạy quá lâu) có một dòng riêng trong lúc chạy: `⏳ lệnh chạy nền: <description> (3:12)`, được đặt tên theo mô tả của lần gọi, nếu không thì theo dòng đầu tiên của lệnh. Khi nó kết thúc, dòng được chốt lại thành `✅ lệnh chạy nền: <description> - đã xong (6:12)`, `❌ ... - kết thúc với lỗi (mã thoát 1)`, hoặc `⏹ ... - đã dừng (6:12)` (Claude đã dừng nó bằng TaskStop, hoặc Claude Code dừng nó vì thiếu bộ nhớ); các dòng thất bại và bị dừng giữ dấu đó trên thẻ đã xong. Một việc kết thúc trong lúc một lệnh như vậy vẫn đang chạy thì chưa xong: thẻ chờ làm trạng thái của công việc (`🔄 name · 1 lệnh chạy nền vẫn đang làm việc · 4:10`, hoặc `1 agent (ship-reviewer), 1 lệnh chạy nền vẫn đang làm việc` khi có cả agent), và khi lệnh kết thúc, phần tiếp theo của Claude tiếp tục trên chính thẻ đó. Các lệnh được khởi động bên trong một agent là của agent đó và không được hiện. Một lệnh mà aipager không bao giờ thấy kết thúc (một lần khởi động lại, một sự kiện bị mất) thôi được đếm sau hai giờ (`AIPAGER_BG_SHELL_MAX_TRACK`, tính bằng giây): dòng của nó ghi `⏹ ... - không thấy kết thúc`, và một công việc chỉ còn chờ nó sẽ đóng lại với `⚠️ name · Xong (2:00:04, không có tin gì từ lệnh chạy nền)`.

Khi dòng thời gian của một việc dài ra, mỗi loạt lần gọi công cụ cũ hơn (ba lần trở lên liên tiếp, và không phải loạt đang chạy) được gộp vào một nút `▸ N công cụ đã chạy` riêng ngay tại chỗ của nó, thay vì chồng chất đầy đủ hay bị giới hạn độ dài tin nhắn của Telegram cắt mất; chạm vào bất kỳ nút nào để đọc ngay tại chỗ. Lời bình không bao giờ bị gộp; hoạt động mới nhất và dòng trạng thái luôn nhìn thấy được mà không cần chạm gì. Dòng riêng của một agent chạy nền vẫn đang chạy (hoặc vừa được chốt) không bao giờ tự bị gộp vào một nút, chỉ các lần gọi công cụ của nó, và không bao giờ khi nó là thứ duy nhất đứng giữa dòng thời gian và giới hạn. Chỉ khi dòng thời gian lớn tới mức gộp hết rồi vẫn không vừa, nội dung mới thực sự bị bỏ khỏi thẻ; khi đó file đính kèm `.md` ở trên chứa bản ghi đầy đủ.

#### <a id="agents-still-running-when-the-answer-goes-out"></a>Agent vẫn đang chạy khi câu trả lời được gửi đi

Claude Code có thể chạy agent ở chế độ nền, và việc đã khởi động chúng có thể kết thúc trong lúc chúng vẫn làm việc. Khi đó câu trả lời kết thúc bằng một đường kẻ phân cách và một trạng thái ngắn của phần việc nền đó, theo kiểu bảng agent của chính Claude Code, được cập nhật liên tục trong lúc nó chạy. Mọi thứ dưới đường kẻ là chữ nhỏ (phần chân trang của Telegram), để bản thân câu trả lời nổi bật:

```
⏳ 5 agent đang làm việc · 🖥 1 lệnh chạy nền (shell) đang chạy

○ 🤖 pipeline-runner · Writing final.md for the pipeline

- 🤖 ship-tester · Write and run criteria tests
- 🤖 ship-developer · Editing state.py
- 🤖 ship-developer › ship-reviewer · Collect line numbers

‖ 🤖 r2-runner · đang chờ các agent của chính nó

Khi các agent xong, kết quả sẽ được gửi ngay tại đây.
```

- **Dòng đầu tiên đếm số lượng.** Các agent đang làm việc (hoặc đã xong), và các lệnh chạy nền (shell) của công việc này đang chạy, đã xong hoặc đã dừng. Một lệnh chạy nền luôn chỉ là một con số, không bao giờ là lệnh của nó.
- **Mỗi agent một dòng,** một 🤖 và loại của nó in đậm, rồi việc nó đang làm: dòng chữ mới nhất của chính nó (lời của Claude, hiện đúng như được viết, không bao giờ được dịch), nếu không thì công cụ mới nhất của nó dưới dạng một cụm từ ngắn nêu điều nó đang làm (`Reading state.py`, `Writing final.md`, `Searching for …`, hoặc mô tả riêng của một lệnh), nếu không nữa thì nhiệm vụ mà Claude đã giao cho agent khi khởi động nó, nếu không nữa thì chỉ công cụ mới nhất của nó (`Running a command`, `Using TodoWrite`), nếu không có cả thì chỉ là loại. Vì vậy hai agent cùng loại đang chạy lệnh không có mô tả vẫn hiện nhiệm vụ riêng của mỗi agent. Một agent đã dừng để chờ các agent do chính nó khởi động thì ghi `‖ … đang chờ các agent của chính nó`.
- **Agent dạng cây.** Các agent mà phiên khởi động là cấp đầu tiên. Mọi agent do một trong số chúng khởi động, ở bất kỳ độ sâu nào, đều được liệt kê ngay bên dưới, mỗi agent ngay sau agent đã khởi động nó; agent do một agent cấp hai khởi động sẽ nêu tên agent đó trước (`ship-developer › ship-reviewer`). Thẻ làm việc thì nêu cùng agent đó sau agent ở đầu chuỗi (`pipeline-runner › ship-reviewer`). Không bao giờ có quá hai cấp. Một agent đang làm việc ở cấp hai không có dấu ○ riêng: Telegram vẽ một dấu đầu dòng ở đó. Một agent mà aipager chưa biết ai khởi động sẽ hiện ở cấp đầu tiên, không bao giờ nằm dưới một phỏng đoán, và chuyển xuống dưới agent đã khởi động nó ngay khi biết.
- **Tối đa sáu dòng,** các agent vẫn đang làm việc trước, rồi `+N agent khác` cho các agent không có dòng. Một agent không bao giờ hiện mà thiếu các agent phía trên nó.
- **Lời hứa** “Khi các agent xong, kết quả sẽ được gửi ngay tại đây.” chỉ còn trong lúc vẫn có một agent đang làm việc. Lệnh chạy nền không bao giờ có lời hứa này: một máy chủ phát triển có thể không bao giờ kết thúc.

Dòng của mỗi agent tự được chốt lại khi agent đó dừng, thành `✓ 🤖 Explore · đã xong (0:42)` (agent đó đã chạy bao lâu), và dòng đầu đếm lùi. Một agent mà một agent trong danh sách khởi động sau khi câu trả lời đã gửi sẽ vào trạng thái bên dưới agent đó ở lần cập nhật kế tiếp; agent mà chính phiên khởi động về sau thì được liệt kê dưới một câu trả lời sau. Khi mọi agent đã xong, các dòng nhường chỗ cho một dòng duy nhất, với thời gian từ câu trả lời tới lúc agent cuối cùng kết thúc:

```
✅ 2 agent đã xong (6:12) · 🖥 1 lệnh chạy nền (shell) đang chạy
```

và khi lệnh chạy nền đó kết thúc, `✅ 2 agent đã xong (6:12) · 1 lệnh chạy nền (shell) đã xong (2:13:05)` (lệnh đã chạy bao lâu; `đã dừng` khi nó bị tắt hoặc bị Claude dừng, `đã xong` khi nó tự kết thúc, dù có lỗi hay không; dòng của nó trên thẻ cho biết ra sao). Một câu trả lời mà công việc của nó chỉ có một lệnh chạy nền thì hiện `🖥 1 lệnh chạy nền (shell) đang chạy`, rồi được chốt thành `1 lệnh chạy nền (shell) đã xong (…)` hoặc `1 lệnh chạy nền (shell) đã dừng (…)`.

Chỉ các lệnh chạy nền thuộc chính công việc của câu trả lời mới được đếm: một máy chủ phát triển mà một việc trước để chạy không bị lặp lại dưới mọi câu trả lời (thanh được ghim vẫn đếm mọi lệnh chạy nền). `/stop` kết thúc công việc, nhưng Escape không kết thúc một lệnh chạy nền: một lệnh vẫn đang chạy sẽ ở lại trên thanh được ghim cho tới khi kết thúc, và không còn được đếm dưới các câu trả lời sau; phần tiếp theo của nó về sau sẽ bắt đầu một việc riêng.

Câu trả lời đó được gửi ngay khi việc kết thúc, dưới dạng một tin nhắn bình thường (có thông báo) trả lời tin nhắn của bạn: đúng phần chữ mà terminal hiện. Thẻ làm việc ở lại bên trên nó làm trạng thái trực tiếp của công việc, ở mọi bố cục: `🔄 name · 1 agent (general-purpose) vẫn đang làm việc · 1:18`, kèm nút **Dừng** của nó. Khi các agent báo kết quả về, câu trả lời của Claude cho phần đó đến thành một tin nhắn mới riêng; câu trả lời trước không bao giờ được gửi lại, kể cả khi aipager khởi động lại. Một công việc mà các agent báo về nhiều lần sẽ có một câu trả lời cho mỗi lần báo, mỗi câu trả lời được gửi ngay khi được viết xong.

Một agent đã dừng trong lúc phần việc nền của chính nó vẫn đang chạy được tính là đang làm việc, vì nó sẽ tiếp tục sau. Một agent đã khởi chạy một agent của riêng nó rồi dừng lại để chờ agent đó được nhận ra từ lần khởi chạy ấy (lần gọi của chính nó đã khởi chạy agent kia): nó vẫn nằm trong trạng thái là đang làm việc, và giữ công việc mở, cho đến khi nó dừng mà không còn agent nào của riêng nó đang chạy. Nếu nó chưa tiếp tục sau hai phút kể từ khi agent cuối cùng trong số đó kết thúc, nó không còn được tính là đang chạy và trạng thái được giữ như lần hiện cuối cùng. `<task-notification>` của Claude cho một agent nói điều tương tự trong một ghi chú; aipager đọc ghi chú này khi thông báo đó bắt đầu một việc, và cả khi Claude nhận nó ngay giữa một việc đang chạy trong lúc thẻ làm việc còn hiện. Một dòng chờ vài giây trước khi hiện là đã xong, để ghi chú đó kịp lấy lại nó trước. Trạng thái được thêm vào ở mọi bố cục, bằng ngôn ngữ của chat.

**Cách nó được cập nhật.** aipager sửa câu trả lời một cách lặng lẽ, chỉ khi những gì trạng thái hiện đã thay đổi, và nhiều nhất mỗi 30 giây một lần cho mỗi câu trả lời (chính câu trả lời được tính là lần đầu). Mỗi câu trả lời có trạng thái đều được cập nhật theo cách này (tối đa năm câu trả lời đang chờ cho mỗi phiên). Các lần sửa có mức ưu tiên thấp: một lần sửa bị ngân sách gửi từ chối, hoặc rơi vào lúc chat đang bị tắt gửi, sẽ không được thực hiện, và trạng thái chờ lần thay đổi tiếp theo. Lần sửa cuối cùng, khi mọi thứ đã kết thúc, được thử lại một lần nữa sau khi lệnh tắt gửi hết hoặc khi ngân sách từ chối, rồi bị bỏ. Không có lần sửa nào vào một câu trả lời đã bị xóa, cho một phiên đã kết thúc hay đã bị tắt, hoặc khi nó sẽ làm câu trả lời quá dài với Telegram (khi một câu trả lời dài bị cắt, chỗ cho trạng thái được chừa lại).

Trạng thái được để nguyên như lần cuối hiện, không bao giờ chuyển thành đã xong, khi aipager không thể biết công việc đã kết thúc:

- một agent mà aipager không nghe tin gì trong 30 phút (`AIPAGER_SUBAGENT_SILENCE`) không còn được đếm là đang chạy, nhưng im lặng không có nghĩa là đã xong;
- một lệnh chạy nền chạy lâu hơn hai giờ (`AIPAGER_BG_SHELL_MAX_TRACK`) mà không thấy kết thúc thì không còn được đếm;
- một lần aipager khởi động lại sẽ quên những câu trả lời nào đang chờ.

Khi aipager khởi động lại, các agent vẫn đang chạy được giữ lại, kể cả những agent đang chờ agent của riêng chúng: một câu trả lời được gửi sau khi khởi động lại sẽ liệt kê chúng, một thẻ làm việc được tiếp quản khi khởi động lại sẽ hiện chúng lại, và lúc chúng kết thúc về sau được nhận ra như thường lệ. Các agent đã được lưu hơn 30 phút trước khi khởi động lại sẽ không được đưa trở lại.

Một câu trả lời bị giữ lại vì lệnh tắt gửi, hoặc được gửi dưới dạng văn bản thuần sau khi gửi có định dạng thất bại, sẽ được gửi mà không có trạng thái, một việc kết thúc mà không có chữ trả lời (chỉ có dòng đầu) cũng vậy.

### <a id="end-confirmation"></a>Xác nhận kết thúc

`/kill`, bộ chọn của nó và `⋮` → `⏹ Kết thúc phiên` luôn hỏi xác nhận:

```
⏹ Kết thúc jim? Claude sẽ dừng làm việc và phiên sẽ đóng.

  [⏹ Kết thúc]  [Hủy]
```

Sau `⏹ Kết thúc`, tin nhắn ghi `⏹ Đã kết thúc jim` phía trên các phiên còn lại của chat này. Một lần xác nhận được chạm sau khi phiên đã bắt đầu việc mới sẽ bị từ chối (`jim đã làm việc mới - …`), và một lần xác nhận cho một phiên cùng tên được bắt đầu sau đó cũng vậy.

### <a id="voice-install-when-extra-isnt-installed"></a>Cài tính năng thoại (khi chưa cài gói thêm)

Khi bạn gửi một tin nhắn thoại mà `aipager[voice]` chưa được cài:

```
⚠️ Tin nhắn thoại cần một gói tùy chọn
   (khoảng 200 MB khi cài · khoảng 74 MB mô hình ở lần dùng đầu).

  [📦 Cài tính năng thoại]  [Hủy]
```

Chạm “📦 Cài tính năng thoại” sẽ chạy đúng lệnh cài cho trình cài đặt của bạn (`uv tool install --reinstall aipager[voice]`, `pipx install --force`, hoặc dự phòng bằng `pip install faster-whisper`), sửa tin nhắn mỗi 5 giây để báo vẫn đang chạy, rồi đưa ra một nút `[🔄 Khởi động lại aipager]` khi thành công.

### <a id="restart"></a>Khởi động lại

`🔄 Khởi động lại aipager`:

- Một aipager chạy như dịch vụ người dùng systemd lên lịch một lệnh `systemctl --user restart aipager.service` tách rời sau 5 giây (một bộ hẹn giờ chính xác tới 1 giây), trong một dịch vụ tạm thời nằm ngoài nhóm tiến trình của chính aipager, để nó vẫn chạy khi aipager thoát. Nó từ chối khi file dịch vụ sẽ tắt các phiên của bạn (`KillMode` khác `process` hay `none`), và bảo bạn chạy `aipager service install` trước.
- macOS, khi agent launchd đã được cài (`~/Library/LaunchAgents/com.aipager.daemon.plist`): `launchctl kickstart -k gui/<uid>/com.aipager.daemon`, kể cả với một aipager bạn tự khởi động.
- Nếu không, một aipager bạn tự khởi động (`aipager start`), trên Linux kể cả khi dịch vụ cũng đã được cài: nó tạo một bản thay thế tách rời, bản này chờ tiến trình cha thoát rồi chạy đúng lệnh đã dùng để khởi động aipager (đầu ra của nó đi vào log tự khởi động lại). aipager hiện tại dừng gọn gàng ngay khi bản thay thế đã chạy.

Không cần SSH. Để hoàn tất một bản cập nhật aipager, nút **🔄 Khởi động lại ngay** trên thẻ cập nhật sẽ khởi động lại aipager tại chỗ, trong cùng terminal (xem [Cập nhật](#update)).

## <a id="update"></a>Cập nhật

`/update` (chỉ dành cho quản trị viên; ở chế độ cá nhân chỉ người vận hành) trả lời bằng một nút duy nhất, **🔄 Kiểm tra cập nhật**. Chưa có gì được tra cứu cho tới khi bạn chạm vào nó. Lần chạm sửa tin nhắn đó thành mỗi sản phẩm một dòng:

- `aipager 1.0.1 → 1.1.0` khi có phiên bản mới hơn (bản mới nhất trên PyPI);
- `aipager 1.0.1 (mới nhất)` khi không có;
- `Claude Code 2.1.282 (không kiểm tra được)` khi việc tra cứu thất bại (mạng hỏng, hết 5 giây chờ, hoặc không tìm thấy `claude`).

Claude Code được so với bản mới nhất trên kênh cập nhật riêng của nó (`autoUpdatesChannel`: latest, stable hoặc rc). Dưới các dòng đó, bằng chữ nhỏ, là cách aipager đã được cài (ví dụ `pipx, from PyPI` hoặc `pipx, from local path …`; chat nhóm không bao giờ hiện đường dẫn).

Nếu có gì mới hơn, sẽ có MỘT nút chỉ cập nhật những sản phẩm có bản cập nhật: **Cập nhật aipager**, **Cập nhật Claude Code** hoặc **Cập nhật cả hai**, cùng với **Hủy**. Khi aipager được đề xuất cập nhật, tin nhắn còn cho biết việc khởi động lại sẽ diễn ra thế nào: `Khởi động lại: tự động, khi không còn việc nào đang chạy.`, `Khởi động lại: chạm “Khởi động lại ngay” khi đã cài xong.` (một aipager không có dịch vụ, xem bên dưới) hoặc `Khởi động lại: thủ công (lý do)`.

Nếu một aipager mới hơn đã được cài nhưng aipager đang chạy vẫn là bản cũ (một lần cập nhật đã xong mà không khởi động lại, hoặc một lệnh `pip install -U` chạy tay), dòng đó ghi `aipager 1.1.0 đã được cài và đang chờ khởi động lại (đang chạy 1.0.1).` và tin nhắn đề xuất **🔄 Khởi động lại ngay** thay cho một bản cập nhật: Cập nhật aipager không bao giờ được đề xuất cho một phiên bản chỉ cần khởi động lại. Phiên bản đã cài được kiểm tra bằng cách chạy trình thông dịch mà aipager sẽ khởi động lại vào, nên một phiên bản đã cài nhưng không nạp được sẽ nói rõ (`aipager 1.1.0 đã được cài nhưng không nạp được. Hãy cài lại bằng …`) và không đề xuất gì. Một bản phát hành mới hơn bản đã cài vẫn được đề xuất như một bản cập nhật. Một bản aipager mới hơn trên một bản cài không cập nhật được từ đây (editable, Nix, Snap, gói hệ thống, container, hoặc bản cài của người dùng khác) được hiện kèm lý do và không được đề xuất. Một bản Claude Code mới hơn được cài bằng npm vào một thư mục mà người dùng của bạn không ghi được cũng vậy (ví dụ `sudo npm install -g` trong `/usr/lib/node_modules`), nơi `claude update` chỉ có thể thất bại: dòng của nó ghi `không cập nhật được từ đây: installed with npm into a folder your user can't write to; run "claude install" once to switch to Claude Code's own installer, then restart aipager`. Nếu không có gì cập nhật được, tin nhắn ghi “Mọi thứ đã là bản mới nhất.”, báo một lần kiểm tra đã thất bại, hoặc, khi có bản mới hơn nhưng không cài được từ đây, “Đã có bản cập nhật, nhưng không thể cài từ đây.”, kèm một nút **Kiểm tra lại** và không có nút cập nhật nào.

Nút cập nhật chỉ chạy những gì lần kiểm tra đã đề xuất. Nếu lần kiểm tra đã cũ hơn 10 phút, đã có một lần cập nhật khác chạy từ đó tới giờ, hoặc các phiên bản không còn khớp với nút, nó sẽ bảo bạn kiểm tra lại. Các nút từ một menu `/update` cũ hơn (các nút **Cập nhật Claude Code** / **Cập nhật aipager** / **Cả hai** riêng cho từng sản phẩm) trả lời “Menu này đã cũ, hãy gửi lại /update” và không làm gì. Mỗi lần chạm đều kiểm tra lại quy tắc chỉ dành cho quản trị viên.

Cập nhật Claude Code chạy `claude update` (theo đường dẫn tuyệt đối, chờ tối đa 5 phút) và báo `Claude Code A → B`, “đã là bản mới nhất”, hoặc lỗi kèm phần cuối đầu ra của nó. Các phiên đang chạy vẫn dùng bản cũ cho tới khi bạn khởi động lại chúng (`/restart`); câu trả lời liệt kê các phiên đó. Không phiên nào được tự khởi động lại thay bạn. Các phiên mới dùng bản mới.

Cập nhật aipager (**Cập nhật aipager**, hoặc nửa sau của **Cập nhật cả hai**, nút cập nhật Claude Code trước):

1. Với một bản cài từ PyPI đã là mới nhất, nó nói vậy rồi dừng.
2. Nếu aipager tự khởi động lại được, trước tiên nó **chờ cho tới khi không còn gì đang dở dang**: không phiên nào đang chạy một việc, đang chờ một câu hỏi, đang chạy một agent chạy nền, đang hiện một thẻ làm việc còn hoạt động, đang chạy một công cụ, hay đang giữ một yêu cầu cấp quyền mở, và không có câu trả lời nào đang bị giữ chờ giới hạn tốc độ hết. Tin nhắn liệt kê những gì nó đang chờ, kèm **Khởi động lại ngay** (bỏ qua việc chờ) và **Hủy**. Sau 10 phút nó hỏi lại: **Chờ thêm 10 phút**, **Khởi động lại ngay**, **Hủy**. Không ai trả lời trong một giờ thì nó tự hủy.
3. Nó nâng cấp qua trình cài đặt sở hữu aipager đang chạy (`pipx upgrade aipager`, `uv tool upgrade aipager`, `brew upgrade aipager`, hoặc `<venv>/bin/python -m pip install --upgrade aipager`), theo đường dẫn tuyệt đối, chờ tối đa 10 phút. Trong lúc trình cài đặt chạy thì không có nút Hủy.
4. Nó kiểm tra rằng bản mới import được trong một trình thông dịch mới. Một lần nâng cấp thất bại, hết thời gian chờ hoặc không import được sẽ không khởi động lại gì. Một lần nâng cấp hết thời gian chờ đã bị dừng giữa chừng, nên tin nhắn cảnh báo rằng bản cài có thể chưa đầy đủ và đưa lệnh cài lại cần chạy trước lần khởi động lại sau. Đầu ra của trình cài đặt chỉ hiện trong chat riêng; một nhóm nhận “(đầu ra nằm trong log của aipager)”.
5. Nếu có một việc bắt đầu trong lúc nâng cấp, nó lại chờ.
6. Nó lên lịch một lệnh `systemctl --user restart aipager.service` tách rời sau 5 giây (một bộ hẹn giờ chính xác tới 1 giây), và tin nhắn trạng thái ghi `⏳ Đã cài aipager A → B, đang khởi động lại…`. Trong lúc bước 5 chờ, nó ghi `⏳ Đã cài aipager A → B; sẽ khởi động lại khi việc hiện tại xong`. Sau khi khởi động lại, aipager mới sửa chính tin nhắn đó thành `✅ Đã cập nhật aipager A → B, đã kết nối lại N phiên` (và `⚠️ Chưa quay lại: …` cho phiên nào không quay lại). Nếu tin nhắn đã bị xóa hoặc không sửa được nữa, nó đăng kết quả thành một tin nhắn mới vào chat đã yêu cầu.

Trong Mini App, **Cài đặt → Cập nhật** hiện “Đang khởi động lại aipager…” với một chiếc đèn lồng xoay cho tới khi aipager mới trả lời, rồi “Đã cập nhật lên B, đã kết nối lại N phiên.”. Nó không bao giờ hiện đồng hồ đếm ngược. Sau khoảng 2 phút không có trả lời (không bao giờ trước khi aipager cũ kịp báo một lần khởi động lại đã không diễn ra), nó ghi “Vẫn đang khởi động lại, hãy mở lại ứng dụng sau giây lát.”. Với tunnel được quản lý của aipager, lần khởi động lại sau cập nhật giữ nguyên địa chỉ của ứng dụng (aipager mới tiếp quản tunnel), nên trang đang mở vẫn trụ được và hiện “Đã cập nhật lên B”. Nếu rốt cuộc không giữ được tunnel, trang sẽ ghi “Vẫn đang khởi động lại, hãy mở lại ứng dụng sau giây lát.” sau khoảng 2 phút. Một lần cập nhật bắt đầu từ một aipager chưa có tính năng này (hoặc với `MINIAPP_KEEP_TUNNEL=0`) vẫn đổi địa chỉ: khi aipager cũ thôi trả lời, trang đang mở ghi “aipager đã khởi động lại. Hãy mở lại ứng dụng khi chat báo đã cập nhật”; hãy đóng nó và mở lại từ nút menu của chat hoặc `/app`. Lần đăng nhập của ứng dụng kéo dài 5 phút, nên một trang đã mở lâu hơn thế sẽ ghi “Mở lại ứng dụng để xem kết quả cập nhật.” thay vào đó.

Sau khi cập nhật, aipager chỉ tự khởi động lại khi nó chạy như dịch vụ người dùng systemd **và** file dịch vụ đó có `KillMode=process` (hoặc `none`). Với một `KillMode` không an toàn, aipager vẫn được nâng cấp, và tin nhắn ghi `Cần khởi động lại: …`: hãy chạy `aipager service install` trước (nó liệt kê các phiên mà một lần khởi động lại sẽ tắt).

**Khởi động lại ngay, khi không có dịch vụ.** Một aipager không được trình quản lý dịch vụ nào khởi động lại (`aipager start` trong một terminal hoặc tmux, Docker, hoặc agent launchd trên macOS) sẽ cài bản cập nhật rồi hiện `Chạm “Khởi động lại ngay” để chạy nó (aipager chờ đến khi không còn việc nào đang chạy).` với một nút **🔄 Khởi động lại ngay**. Chạm vào nó (chỉ quản trị viên, như mọi nút cập nhật):

1. kiểm tra aipager đã cài có nạp được không (`⏳ Đang kiểm tra aipager đã cài…`); một bản cài bị hỏng không khởi động lại gì cả, và cùng phiên bản thì báo không có gì để khởi động lại;
2. chờ, giống lần khởi động lại của systemd, cho tới khi không còn gì đang chạy, với **Khởi động lại ngay** (bỏ qua chờ) và **Hủy**;
3. kiểm tra lại bản cài, rồi ghi `⏳ Đã cài aipager A → B, đang khởi động lại…` và dừng aipager một cách gọn gàng (như Ctrl+C);
4. khởi động aipager mới tại chỗ: cùng tiến trình, cùng lệnh, cùng môi trường và thư mục, trong cùng cửa sổ terminal hoặc tmux (và, trong Docker, cùng tiến trình của container). Các phiên vẫn tiếp tục chạy. aipager mới sửa tin nhắn thành `✅ Đã cập nhật aipager A → B, đã kết nối lại N phiên`.

Các lần chạm gửi trong mấy giây khởi động lại sẽ bị bỏ, như với mọi lần khởi động lại. Một Ctrl+C hoặc `docker stop` trong lúc khởi động lại sẽ thắng: aipager dừng và không khởi động lại. Nếu aipager mới không khởi động được tại chỗ, một bản sao tách riêng sẽ khởi động nó khi bản cũ đã thoát (đầu ra của nó đi vào log tự khởi động lại, `aipager.log` trong thư mục của phiên bản cài hoặc `/tmp/aipager.log`, và log của aipager nêu lệnh cần chạy nếu bản sao đó không quay lại); nếu cả cách đó cũng thất bại, hoặc chính trình thông dịch đã mất, aipager thoát với mã 1 và log của aipager ghi `restart.failed:` cùng lệnh cần chạy.

Khi aipager hoàn toàn không tự khởi động lại được, tin nhắn đưa ra các bước chính xác cho cách cài của bạn thay vì một nút:

- một terminal hoặc tmux: `Trong cửa sổ terminal hoặc tmux đang chạy aipager, nhấn Ctrl+C, rồi chạy aipager start.`
- macOS: `Trên máy Mac, hãy chạy launchctl kickstart -k gui/<uid>/com.aipager.daemon.`
- Docker: `Trên máy tính chạy Docker, hãy chạy docker restart <container>.` (ID ngắn của container khi tên máy của nó là một ID), kèm lời nhắc rằng tạo lại container từ image sẽ mang lại phiên bản aipager trong image đó.

**Khởi động lại ngay** cũng xuất hiện trong kết quả của **Kiểm tra cập nhật** khi một aipager mới hơn đã được cài và đang chờ, và trong một thông báo chỉ gửi một lần: khi các hook của Claude Code (vốn khởi động mới ở mỗi sự kiện, từ aipager đã cài) đã chạy một aipager mới hơn aipager đang chạy, chủ sở hữu nhận một tin nhắn im lặng cho mỗi phiên bản mới hơn và mỗi lần chạy aipager, `⚠️ Bản cập nhật aipager mới xong một nửa: …`, kèm nút đó. Nó chờ hai phút kể từ lúc các hook chạy phiên bản mới hơn lần đầu (một lần khởi động lại ngay sau khi cài sẽ không bao giờ hiện nó), rồi chờ tới khi không còn việc nào đang chạy trong chat riêng của chủ sở hữu. Sau khi khởi động lại vào phiên bản đó, thông báo được sửa thành `✅ Bản cập nhật aipager đã xong: aipager giờ đang chạy B.`, không còn nút. Trong Mini App, **Cài đặt → Cập nhật** hiện cùng trạng thái đó như một dòng phía trên thẻ, với **Khởi động lại ngay** (nó hỏi trước).

Trong Mini App, **Cập nhật** là thẻ đầu tiên trên tab Cài đặt: tên của nó, phiên bản đang chạy, và một nút nhỏ **Kiểm tra cập nhật** ở cuối dòng. Mở tab Cài đặt sẽ tự kiểm tra (nhiều nhất mười phút một lần, không bao giờ trong lúc một lần cập nhật đang chạy hoặc aipager đang khởi động lại; một lần kiểm tra thất bại ở đó không báo gì), còn chạm vào thì luôn kiểm tra. Thẻ giữ là một dòng yên lặng cho tới khi có việc cần làm: một phiên bản mới hơn mang theo các dòng của nó và một nút cập nhật duy nhất, “Mọi thứ đã là bản mới nhất.” là một dòng duy nhất không có nút, còn một lần cập nhật đang chạy, một lần khởi động lại hoặc **Khởi động lại ngay** giữ các nút riêng của chúng.

Mỗi lúc chỉ có một lần cập nhật chạy, tính chung cả `/update`, thẻ **Cài đặt → Cập nhật** của Mini App (cùng lần kiểm tra, cùng một nút cập nhật, cùng một tác vụ) và `aipager update` trên dòng lệnh. Điều đó gồm cả mấy giây giữa “đã cài, đang khởi động lại…” và lúc khởi động lại thật: `/update` trả lời rằng aipager sắp khởi động lại, Mini App không đưa ra nút nào, và nút **Khởi động lại aipager** của tính năng thoại từ chối trong lúc một lần cập nhật đang chạy hoặc đang chờ khởi động lại. Nếu aipager vẫn còn chạy hai phút sau khi lên lịch khởi động lại, nó dừng bộ hẹn giờ khởi động lại đang chờ, nhả khóa cập nhật, và bảo bạn tự khởi động lại nó.

Nếu aipager tắt trong lúc một trình cài đặt đang chạy, trình cài đặt được cho 3 giây để xong rồi bị dừng (nếu không, nó sẽ tiếp tục ghi vào bản cài trong khi aipager tiếp theo khởi động). Tin nhắn trạng thái và, sau khi khởi động lại, một tin nhắn mới sẽ nói rằng bản cài có thể chưa đầy đủ và cách sửa.

Một khi quá trình tắt đã bắt đầu, không có gì được khởi động lại và không có gì mới bắt đầu: các nút của `/update` trả lời rằng aipager đang tắt, Mini App trả lời 503, và không trình cài đặt hay lần kiểm tra phiên bản nào được khởi chạy. Một trình cài đặt xong trong 3 giây của nó vẫn được tính. Tin nhắn trạng thái nói rằng bản mới đã được cài và không có gì được khởi động lại, và lần khởi động tiếp theo thông báo `Đã cập nhật aipager A → B`. Một aipager bạn đã dừng bằng `aipager service stop` vẫn ở trạng thái dừng. Phần của việc cập nhật trong quá trình tắt mất tổng cộng tối đa 8 giây.

## <a id="free-messages"></a>Tin nhắn tự do

### <a id="text"></a>Chữ

Được coi là tin nhắn tiếp theo cho **phiên đang chọn**: đích của chat này, phiên bạn chuyển tới gần nhất, đã gửi tin nhắn tới, đã bắt đầu, hoặc phiên vừa trả lời bạn ở đây (xem [thanh trạng thái được ghim](#the-pinned-status-bar)). Trong một nhóm, mỗi người có đích riêng, không bị câu trả lời của một phiên làm thay đổi (xem [nhóm](groups.md#talking-to-a-session-in-a-group)). Tin nhắn tới Claude **ngay lập tức**, kể cả khi việc của chính bạn đang chạy, y như gõ vào terminal (trong một nhóm, tin nhắn của người khác chờ việc đó kết thúc; xem bên dưới). Gửi nhiều tin thì chúng xếp hàng bên trong chính Claude, và Claude nhận từng tin ở một điểm dừng tự nhiên:

Reaction trên tin nhắn của bạn đi theo nó, giống như cách một tin nhắn đang xếp hàng trong Claude Code đổi từ màu xám sang màu trắng:

- 👀: đã được giao cho phiên (hoặc đang bị giữ, xem bên dưới), nhưng Claude chưa nhận. Một tin nhắn gửi trong lúc một việc đang chạy vẫn là 👀 trong lúc chờ trong hàng đợi của Claude.
- 👍: Claude đã nhận nó: bắt đầu một việc, gộp nó vào việc đang chạy, hoặc giao nó cho một agent chạy nền đang chạy.
- 🤷: nó sẽ không bao giờ được nhận: một tin nhắn bị giữ đã bị `/stop`, `/clearqueue` hoặc `/kill` bỏ, hoặc không gửi được khi được thả ra; một tin nhắn hay lệnh vẫn đang chờ khi `/stop`, `/clearqueue`, `/kill` hoặc việc phiên kết thúc (không phải `/clear` hay `/resume`) bỏ nó; hoặc một tin nhắn mà Claude Code từ chối (xem bên dưới). Những tin nhắn mà Claude đã xếp hàng chỉ được đánh dấu khi aipager còn nhìn thấy hàng đợi đó (việc theo dõi cuộc trò chuyện đã lưu theo thời gian thực đang chạy và không có công việc nền nào đang chờ); nếu không, chúng giữ 👀. Escape trong terminal kéo hàng đợi của Claude trở lại ô nhập, nơi chúng có thể được gửi lại, nên một tin nhắn bị bỏ theo cách đó cũng giữ 👀.
- 👌: một lệnh của Claude Code đã chạy (xem bên dưới), hoặc aipager xác nhận `/stop`.

Một reaction chỉ đi tới trước, nên một tin nhắn nhận tối đa ba reaction, mỗi loại một lần. Một reaction rơi vào lúc Telegram đang chặn vì giới hạn tốc độ sẽ bị bỏ qua, không được gửi bù sau, và một lần dọn dẹp chỉ đánh dấu tối đa mười tin nhắn mới nhất mà nó bỏ.

Nút lệnh (`Thu gọn cuộc trò chuyện`, `/model …`): chạm khi phiên đang sẵn sàng thì lệnh được 👌 ngay; Claude Code chạy nó khi nhấn Enter, và một lệnh cục bộ như `/model` không phát ra hook nào có thể báo lại sau đó. Chạm khi một việc đang chạy (trừ `/model`: lệnh này bị từ chối cho tới khi việc kết thúc, xem [đổi mô hình của một phiên đang chạy](#switching-a-running-sessions-model)) thì nó là 👀 cho tới khi việc đó kết thúc (bình thường, như một điểm dừng tạm của một công việc nền, hoặc vì một lỗi API) rồi mới 👌; bị `/stop`, `/clearqueue`, `/kill` hoặc việc phiên kết thúc bỏ trước lúc đó thì nó không bao giờ chạy: 🤷. Khi Claude Code xếp nó vào hàng đợi như một tin nhắn, nó đi theo vòng 👀 → 👍. Một tin nhắn thoại nhận các reaction giống như chữ, một khi bản chép của nó đã được gửi.

Thẻ làm việc và câu trả lời cuối cùng đi theo tin nhắn mà Claude thực sự dùng cho một việc (tin nhắn nó dùng để bắt đầu khi phiên đang sẵn sàng, hoặc tin nhắn nó gộp vào việc đang chạy), không bao giờ đơn giản là tin nhắn cuối cùng bạn gửi. Gửi thêm một tin giữa chừng không khiến câu trả lời “nhảy” sang tin đó: nếu Claude gộp nó vào câu trả lời đang hình thành, thẻ nhảy sang tin đó ngay lúc chuyện đó xảy ra; nếu Claude làm xong trước rồi mới nhận nó, câu trả lời đầu tiên ở lại dưới tin nhắn đầu, và tin gửi thêm có một việc riêng, với thẻ và câu trả lời riêng bên dưới.

Nếu Claude Code từ chối thẳng một tin nhắn (một lệnh slash lạ, hoặc một lệnh có sẵn chỉ mở một hộp thoại trong terminal), không có hook nào được phát, nên sẽ không có gì kết thúc việc mà aipager vừa thông báo. Sau 8 giây không có hook nào (`PROMPT_HOOK_GRACE_SECONDS`), thẻ làm việc trở thành `⚠️ name · Claude Code chưa nhận tin nhắn` kèm một dòng giải thích và phiên lại sẵn sàng; lý do nằm trên terminal, và tin nhắn nhận 🤷 (một lệnh slash thì nhận 👌: một lệnh có sẵn mở hộp thoại cũng không phát hook nào). Chỉ một tin nhắn đã bắt đầu một việc mới bị xét theo cách này; một tin nhắn xếp hàng sau một việc đang chạy giữ 👀 cho tới khi Claude nhận nó hoặc nó bị bỏ.

Ba trường hợp được giữ lại thay vì gửi đi, và tự động được gửi khi đã xong:

- Đang có một yêu cầu cấp quyền hay câu hỏi mở, suốt thời gian nó còn mở (không có giới hạn thời gian): nếu không, chữ của bạn sẽ bị đọc thành câu trả lời cho hộp thoại đó. Khi đó, tin nhắn đầu tiên của bạn nhận một lời đáp ngay dưới yêu cầu, báo rằng Claude vẫn đang chờ bạn trả lời (các tin sau chỉ nhận 👀). Ngay khi bạn trả lời, trên Telegram hay trong terminal, các tin nhắn bị giữ lại được gửi đến Claude theo đúng thứ tự bạn gửi và lời đáp đó bị xóa. Nếu phiên kết thúc khi yêu cầu vẫn còn mở, chúng bị bỏ đi kèm 🤷.
- (Chế độ làm việc nhóm) tin nhắn của một người khác vẫn đang chờ được nhận.
- (Chế độ làm việc nhóm) việc của một người khác đang chạy, kể cả một agent chạy nền mà việc đó đã khởi động. Một việc gõ trong terminal được tính là của chủ sở hữu, nên chỉ tin nhắn của chủ sở hữu (hoặc, trong chat riêng, của chính thành viên của chat đó) mới nhập vào nó.

Hai trường hợp cuối giữ tin nhắn của những người khác nhau ở ngoài cùng một việc, để mỗi tin chạy với quyền của chính người gửi. Nếu dù vậy một tin vẫn nhập vào (ví dụ một người gửi mà aipager không biết, người không bao giờ bị giữ), việc đó chạy theo quy tắc nghiêm nhất của mọi người trong đó (xem [nhóm](groups.md#who-a-message-runs-as)). Nếu việc mà một tin nhắn đang chờ bị dừng, hoặc bị chặn lại vì một công cụ bị cấm, các tin nhắn bị giữ sẽ bị bỏ kèm một 🤷.

Mỗi phiên giữ tối đa 50 tin nhắn và chúng hết hạn sau 24 giờ; `/clearqueue` bỏ chúng cùng với mọi thứ Claude đang giữ.

#### <a id="send-a-queued-message-now"></a>Gửi ngay một tin nhắn đang xếp hàng

Một tin nhắn bạn gửi trong lúc Claude đang làm việc của chính bạn sẽ đi tới Claude ngay (aipager không giữ nó lại) và chờ trong hàng đợi của Claude cho tới khi bước hiện tại kết thúc. Ngay khi hàng đợi của Claude cho thấy nó, aipager trả lời bên dưới:

```
⏳ Trong hàng đợi - Claude sẽ đọc sau bước này
[⚡ Gửi ngay]
```

Mỗi tin nhắn đang xếp hàng có một dòng riêng, và mỗi dòng biến mất, cùng với 👍 của nó, ngay khi Claude nhận tin nhắn đó. Một tin nhắn mà Claude nhận trước khi thấy bản ghi hàng đợi của nó thì không có dòng nào, chỉ 👀 rồi 👍.

Chạm `⚡ Gửi ngay` (hoặc gửi `/now`) sẽ nhấn tổ hợp phím gửi ngay của chính Claude Code (Ctrl+X, rồi Ctrl+S) trong phiên đó. Khi đó Claude đọc mọi thứ nó đã xếp hàng cùng một lúc, không chỉ tin nhắn bạn đã chạm. Chuyện gì xảy ra với phần việc đang làm là do Claude Code chọn: một lệnh hay agent đang chạy tiếp tục chạy nền; nếu Claude đang viết dở một câu trả lời, câu trả lời đó bị cắt ngang và được bắt đầu lại cùng với tin nhắn của bạn. Mọi loại bước khác (ví dụ một công cụ khác) cũng có thể bị ngắt, sau một khoảng chờ ngắn. Trong một số trường hợp (ví dụ khi tác vụ nền của Claude Code bị tắt), gửi ngay quay về thành một lần ngắt bình thường bước đang chạy. Tất cả đều là cách gửi ngay của chính Claude Code, không phải điều aipager quyết định.

Lần chạm trả lời `Đã gửi ngay cho Claude` và mọi dòng biến mất cùng lúc, vì gửi ngay giao cho Claude mọi thứ nó đang giữ; `/now` cũng gỡ chúng như vậy, và không có dòng nào cho những tin nhắn mà Gửi ngay đã gửi (một tin nhắn bạn gửi sau đó có dòng riêng). Nếu Claude hủy bước đang chạy thay vì chuyển nó xuống chạy nền, nó chỉ nhận ngay tin nhắn cũ nhất đang xếp hàng và đọc phần còn lại ở bước tiếp theo; câu trả lời nằm dưới tin nhắn nó đã nhận, và tin nhắn đó không chạy lần thứ hai. Nếu các phím mất hơn một chút mới tới được Claude (máy tính đang bận), lần chạm trả lời `Đang gửi cho Claude` thay vào đó; khi đó dòng biến mất khi các phím đã tới nơi, hoặc, nếu không gửi tới được, dòng ở lại và ghi `⏳ Trong hàng đợi - chưa tới được Claude; hãy chạm “Gửi ngay” lần nữa`, kèm nút của nó, để bạn chạm lại. Các phím chỉ được nhấn khi cuộc trò chuyện đã lưu của Claude cho thấy nó vẫn đang giữ tin nhắn; nếu không, lần chạm trả lời `Claude đã nhận rồi` và gỡ dòng đó. Nó bị từ chối, không nhấn gì, trong lúc một yêu cầu cấp quyền hay câu hỏi đang mở (`Hãy trả lời câu hỏi đang mở trước`), trong lúc Claude có thể vừa mở một yêu cầu mà aipager chưa kịp hiện (các sự kiện của việc mới bị giữ lại trong lúc câu trả lời của việc trước vẫn đang được gửi: `Chưa được lúc này, thử lại sau giây lát`), và trong lúc aipager đang gõ dở một tin nhắn khác vào phiên (`aipager đang gõ một tin nhắn, thử lại sau giây lát`). Ai được gửi tin nhắn cho phiên đều dùng được nút này và `/now`; một thành viên `read_only` thì không.

Dòng tự biến mất khi tin nhắn của nó rời hàng đợi của Claude: Claude nhận nó (gộp vào việc đang chạy, giao cho một agent chạy nền, hoặc bắt đầu thành việc tiếp theo), Escape trong terminal kéo hàng đợi trở lại ô nhập, hoặc nó bị `/stop`, `/clearqueue`, `/kill`, `/restart`, một lần dừng vì an toàn hay việc phiên kết thúc bỏ đi. Một lần aipager khởi động lại sẽ gỡ mọi dòng còn đang hiện.

Các dòng này không bao giờ phải chờ bộ quản lý giới hạn tốc độ: chúng được gửi và gỡ ngay cả trong một chat bận hay chậm, và ở chế độ tối thiểu. Chỉ khi chính Telegram đang tắt gửi chat thì không có dòng nào được gửi (`/now` khi đó vẫn dùng được), và nếu Telegram vừa yêu cầu aipager gửi chậm lại, một dòng sẽ chờ đúng vài giây mà Telegram yêu cầu. Mỗi tin nhắn đang chờ tốn một lần gọi tới chat cho dòng của nó; các dòng đi cùng nhau được gỡ trong một lần gọi.

### <a id="files"></a>File

File được gửi lên sẽ được tải về một thư mục riêng của chat mà nó được gửi từ đó, `/tmp/aipager-files/<chat>/` (đặt tên `<time>_<file name>`; `<chat>` là một cái tên tạo từ ID chat và token bot, không để lộ ID chat). Chỉ tài khoản người dùng đang chạy aipager mới đọc được các thư mục này (`0700`, file `0600`), và việc của một thành viên trong một chat không đọc, tìm hay ghi được file gửi lên của chat khác (xem [bảo mật](security.md#team-mode-enforcement)). File từ trước thay đổi này vẫn nằm nguyên chỗ cũ, ngay trong `/tmp/aipager-files/`; không có gì bị chuyển đi hay xóa, và aipager thu quyền của thư mục đó về `0700` khi khởi động, nên chỉ người dùng đó mới chạm tới được chúng. Một token bot mới cho mỗi chat một thư mục mới; file gửi lên từ trước đó vẫn nằm trong thư mục cũ, nơi việc của một thành viên không còn đọc được nữa. Một thư mục tải về là symlink, hoặc thuộc về một người dùng khác, sẽ bị từ chối và lần gửi file đó thất bại. Đường dẫn được đưa cho claude: có chú thích thì tin nhắn là chú thích rồi tới (các) đường dẫn; không có chú thích thì chỉ là `check this: <path>` (hoặc `check these: <paths>` với một album), nên claude được chỉ tới file mà không bị bảo phải làm gì với nó. Một chú thích bắt đầu bằng `/<label>` sẽ chọn phiên, y như `/<label> <prompt>` với chữ, và nhãn được bỏ khỏi tin nhắn: chỉ `/api` thì gửi `check this: <path>` tới `api`, còn `/api compare these` gửi `compare these <paths>` tới đó. Chỉ một `/<label>` ở đầu mới chọn đích; một dấu gạch chéo nằm sau trong chú thích chỉ là chữ. Một nhãn không phiên nào mang thì bị từ chối với `⚠️ Không có phiên nào tên` và không có gì được gửi. Giới hạn tải file 20 MB của bot Telegram được kiểm tra trước; file quá lớn nhận một lời từ chối rõ ràng trước khi có bất kỳ lần tải nào. Một lần tải gặp lỗi mạng tạm thời được thử tổng cộng tối đa ba lần (chờ 1 giây, rồi 2 giây) trước khi bạn thấy lỗi, và lỗi đó nêu tên file. Một album (nhiều ảnh hay tài liệu gửi thành một tin nhắn) được giao cho claude thành một tin nhắn duy nhất (chú thích, rồi lần lượt mọi đường dẫn file) khi mục cuối cùng của nó đã tới; nếu một mục không tải được, phần còn lại vẫn được gửi, kèm một ghi chú nêu tên mục bị thiếu.

### <a id="voice"></a>Tin nhắn thoại

Tin nhắn thoại đi qua `faster-whisper` (gói thêm `aipager[voice]`). Âm thanh được chuyển thành chữ ngay trên máy, được nhắc lại dưới dạng `🎙️ Nghe được: …`, và được gõ vào như thể bạn đã gõ nó, kể cả khi làm tên cho `/new` hoặc cho một lần đổi tên đang chờ. Một tin nhắn thoại vượt quá giới hạn tải 20 MB của bot Telegram sẽ bị từ chối. Xem [hook → UserPromptSubmit](hooks.md#userpromptsubmit) để biết chuyện gì xảy ra tiếp theo.

---

## <a id="command-line-every-command"></a>Dòng lệnh: mọi lệnh

Chạy `aipager <command> --help` để xem cùng danh sách này trên máy tính của bạn.

| Lệnh | Làm gì |
|---|---|
| `aipager config` | Trình thiết lập tương tác: token bot, các chat, thành viên và vai trò, hook của Claude Code. Chạy lại để thay đổi bất cứ điều gì (xem [nhóm](groups.md#setup)). |
| `aipager setup` | Cùng lần thiết lập đầu tiên đó mà không hỏi gì, cho script và agent lập trình (xem [bên dưới](#command-line-aipager-setup-for-coding-agents)). |
| `aipager start` | Chạy aipager ở tiền cảnh. Nó từ chối khởi động khi một aipager khác đang chạy. |
| `aipager service install [--yes]` | Cài aipager thành một dịch vụ nền (dịch vụ người dùng systemd trên Linux, agent launchd trên macOS) và khởi động nó. `--yes` bỏ qua câu hỏi khi file dịch vụ đã cài khác đi. |
| `aipager service start\|stop\|status\|logs\|uninstall` | Khởi động, dừng hoặc xem dịch vụ, theo dõi log của nó, hoặc gỡ nó. |
| `aipager status [--json]` | aipager và mọi phiên trong một cái nhìn: trạng thái, mô hình, ngữ cảnh, chi phí, và trạng thái giới hạn tốc độ của Telegram. |
| `aipager logs [-f] [-n LINES]` | Log của aipager (journald trên Linux, log launchd trên macOS): 100 dòng cuối, hoặc `-n` dòng; `-f` theo dõi liên tục. |
| `aipager doctor [--fix] [--safety-check] [--json]` | Kiểm tra sức khỏe (xem [xử lý sự cố](troubleshooting.md#aipager-doctor-check-list)). `--fix` đề nghị sửa thông tin đăng nhập Claude và một bản cài `claude` không rõ ràng, có hỏi trước; `--safety-check` in ra các quy tắc an toàn đang có hiệu lực; `--json` dành cho script (xem [bên dưới](#aipager-doctor---json)). |
| `aipager session <name> [claude args]` | Bắt đầu một phiên Claude Code tên `<name>` dưới dtach trong thư mục hiện tại, hoặc kết nối vào nó nếu nó đang chạy. Các đối số sau tên (hoặc sau `--`) được chuyển nguyên cho `claude`, ví dụ `--resume`. Cần một aipager đang chạy. |
| `aipager session ls [-a] [--json]` | Liệt kê các phiên; `-a` gồm cả các phiên đã kết thúc. Cũng có `aipager session list`. |
| `aipager session kill <name> [-y]` | Kết thúc một phiên, sau một câu hỏi trừ khi có `-y`. |
| `aipager resume [name]` | Đưa trở lại một phiên đã kết thúc cùng cuộc trò chuyện của nó; không kèm tên thì hiện một bộ chọn. |
| `aipager miniapp enable [--port PORT] [--url URL]` | Bật Mini App, trên `127.0.0.1:PORT` (mặc định 8765), sau tunnel được quản lý hoặc URL `https://` riêng của bạn. Có hiệu lực ở lần aipager khởi động lại sau. |
| `aipager miniapp disable\|status` | Tắt nó (ở lần khởi động lại sau), hoặc hiện cài đặt và địa chỉ của nó. |
| `aipager policy validate` | Kiểm tra `policy.yaml` (và `policy.d`); thoát với mã khác 0 khi có vấn đề. |
| `aipager update` | Nâng cấp aipager qua trình cài đặt sở hữu nó (uv, pipx, Homebrew, pip). Nó không bao giờ khởi động lại aipager; hãy tự khởi động lại, hoặc dùng `/update` trong Telegram, lệnh làm được việc đó (xem [Cập nhật](#update)). |
| `aipager report [--note TEXT]` | Hiện một báo cáo sự cố và gửi nó nếu bạn đồng ý (xem [bên dưới](#command-line-aipager-report)). |
| `aipager uninstall [-y]` | Dừng và gỡ aipager cùng cấu hình của nó, giữ lại dữ liệu (xem [bên dưới](#command-line-aipager-uninstall)). |
| `aipager version`, `aipager --version` | In phiên bản. |
| `aipager help [command]` | Trợ giúp cho aipager hoặc cho một lệnh. |

## <a id="command-line-aipager-setup-for-coding-agents"></a>Dòng lệnh: `aipager setup` (cho agent lập trình)

Các phần ở trên mô tả bot Telegram. Phần này mô tả một lệnh bạn chạy trong terminal, hoặc một script hay agent lập trình của bạn chạy thay bạn: `aipager setup` thiết lập aipager mà không hỏi gì và cho cùng kết quả như lần chạy đầu tiên của `aipager config`.

```sh
# 1. tìm ID Telegram của người dùng (họ mở bot và chạm Start)
aipager setup detect-chat --token-file ~/bot-token.txt --json
# 2. khi họ xác nhận đúng là họ, thiết lập aipager
aipager setup --token-file ~/bot-token.txt --chat-id 123456789 --service --json
# 3. kiểm tra bản cài
aipager doctor --json
```

Nó làm gì, theo thứ tự này: đọc token của bot, kiểm tra token với Telegram (getMe), kiểm tra mọi thứ trên máy tính này mà không ghi gì (cấu hình hiện có, các phụ thuộc, `~/.claude/settings.json`, có aipager nào đang chạy không), gửi một tin nhắn thử cho người dùng (“aipager is set up for you. ...”), và chỉ sau đó mới ghi. Các file được ghi, giống như trình thiết lập ghi:

- `~/.config/aipager/aipager.yaml` (quyền 0600): `bot_token` và một scope chat riêng có thành viên duy nhất là người dùng (`label: owner`, vai trò owner hoặc admin).
- `~/.claude/settings.json`: các hook của aipager và statusLine (file hiện có được sao lưu thành `settings.json.bak.<time>` khi nó thay đổi).
- Log kiểm tra nhận một bản ghi `grant-owner` khi ai đó trở thành chủ sở hữu.
- Với `--service`, dịch vụ nền (giống như `aipager service install --yes`).
- Chỉ trên một bản cài `config.env` cũ (chưa có `aipager.yaml`), việc chuyển đổi mà trình thiết lập làm: một bản sao `config.env.bak.<time>` (và `team.yaml.bak.<time>` khi có `team.yaml`), và một `~/.config/aipager/policy.yaml` mẫu khi chưa có. Bản thân `config.env` được để nguyên.

Nếu có bước kiểm tra nào thất bại, không có gì được gửi và không có gì được ghi. Khi một aipager đã đang chạy, một thay đổi về chat hay vai trò được tải lại trực tiếp; một token bot mới cần khởi động lại (`aipager service stop`, rồi `aipager service start`), việc mà setup không bao giờ tự làm, và `--service` bị bỏ qua. Nếu setup không biết được có aipager nào đang chạy không, nó làm như thể có (cảnh báo `daemon_unknown`): khi đó nó không bao giờ cài dịch vụ, nên không bao giờ có thể khởi động lại một aipager đang chạy, và sau mọi thay đổi nó không gửi lệnh tải lại mà báo `restart_needed: true`.

### <a id="aipager-setup-flags"></a>Các cờ của `aipager setup`

| Cờ | Ý nghĩa |
|---|---|
| `--token-file PATH` | Đọc token bot từ file này (tối đa 4096 byte; có chữ khác quanh một token cũng không sao). Một file mà người dùng khác đọc được vẫn dùng được nhưng có cảnh báo `token_file_shared`: hãy `chmod 600` nó, hoặc xóa nó khi setup đã thành công. |
| `--token-stdin` | Đọc token bot từ stdin (một pipe hoặc một file được chuyển hướng, không bao giờ là terminal). Pipe phải được đóng: setup chờ stdin kết thúc tối đa 30 giây, rồi thoát với mã 2 và `token_stdin_timeout`. `printf '%s\n' "$TOKEN" \| aipager setup --token-stdin ...` và `< token.txt` đều đóng nó. |
| `--chat-id N` | Bắt buộc. ID người dùng Telegram dạng số của người đó, cũng là ID chat riêng của họ. Nhóm được thêm bằng `aipager config`. |
| `--role owner\|admin` | Vai trò của người đó (mặc định `owner`). |
| `--service` | Cài và khởi động luôn dịch vụ nền (bị bỏ qua khi một aipager đang chạy). |
| `--force` | Cho phép thay token bot, chat riêng của chủ sở hữu, hoặc vai trò của một bản cài hiện có. Các scope nhóm và chat riêng khác được giữ nguyên. |
| `--dry-run` | Chạy mọi bước kiểm tra, không gửi gì, không ghi gì, và báo những gì sẽ thay đổi. |
| `--json` | In đúng một đối tượng JSON ra stdout; mọi thứ khác ra stderr. |

Hãy đưa đúng một trong hai `--token-file` và `--token-stdin`. Token không bao giờ được nhận như giá trị của một cờ: `--token` và `--bot-token` bị từ chối (`token_on_command_line`) và giá trị không được hiện ra. Cờ viết tắt (`--token-f`, `--dry`) không được chấp nhận. Setup không bao giờ hỏi gì, không bao giờ cần terminal, và không bao giờ đọc token từ biến môi trường.

Chạy lại với cùng token, chat và vai trò thì không đổi gì và nói rõ điều đó (`unchanged`, không có tin nhắn thử). Một token, chat hay vai trò khác trên một bản cài hiện có sẽ thoát với mã 6 cho tới khi bạn thêm `--force`. Một bản cài `config.env` cũ được chuyển đổi trước, như trình thiết lập làm (nó cho vai trò `admin`, nên `--role owner` trên bản cài đó cần `--force`).

### <a id="aipager-setup-detect-chat"></a>`aipager setup detect-chat`

```sh
aipager setup detect-chat (--token-file PATH | --token-stdin) [--timeout SECONDS] [--json]
```

Chờ tin nhắn riêng mới nhất gửi cho bot (chạm Start là gửi một tin) và in người gửi của nó: ID, tên và username. Nó không lưu gì, nên hãy cho người dùng xem người đó và chỉ chạy `aipager setup --chat-id` khi họ xác nhận. Chỉ những tin nhắn được gửi không quá 10 phút trước khi lệnh bắt đầu mới được tính. `--timeout` từ 0 tới 3600 giây (mặc định 300; 0 nghĩa là chỉ xem một lần). Nó đọc Telegram mà không xác nhận tin nhắn nào, nên `/start` vẫn còn cho trình thiết lập và aipager dùng.

Trong lúc một aipager cho cùng bot đang chạy, aipager đó giữ các tin nhắn của bot, và nó không ghi lại tin nhắn riêng từ những người chưa được thiết lập. Khi đó detect-chat thoát với mã 9 (`source: "daemon"`): hãy dừng aipager (`aipager service stop`) rồi chạy lại, hoặc hỏi người dùng ID dạng số của họ. Với một aipager của bot khác, nó đọc Telegram trực tiếp (`source: "telegram"`).

Giới hạn: vì detect-chat không bao giờ xác nhận tin nhắn, Telegram chỉ cho nó thấy 100 bản cập nhật chưa đọc cũ nhất trong 24 giờ qua. Khi bot có nhiều như vậy đang chờ, một `/start` mới không thể được nhìn thấy: khi đó detect-chat hết thời gian chờ (mã thoát 8) với cảnh báo `updates_backlog`, và chạm Start lần nữa cũng không giúp được. Thay vào đó hãy hỏi người dùng ID Telegram dạng số của họ (hoặc khởi động aipager một lần, nó sẽ đọc và dọn sạch phần tồn đọng).

### <a id="exit-codes"></a>Mã thoát

| Mã | `error` | Ý nghĩa |
|---|---|---|
| 0 | | Thành công (`installed`, `updated`, `unchanged`, `dry_run`, hoặc detect-chat `found`). |
| 1 | `config_malformed`, `ambiguous_install`, `settings_invalid`, `test_send_failed`, `write_failed`, `service_failed`, `internal_error` | Thất bại. `ambiguous_install`: có hơn một chat riêng có thể là của chủ sở hữu; hãy sửa trong `aipager config`. |
| 2 | `usage`, `missing_token_source`, `token_source_conflict`, `token_file_unreadable`, `token_stdin_is_tty`, `token_stdin_timeout`, `token_malformed`, `token_on_command_line`, `bad_chat_id`, `bad_role`, `bad_timeout` | Đầu vào sai hoặc thiếu; `fix` nêu tên cờ. Một cờ lạ hay một từ sai sau `setup` được bộ phân tích đối số báo (văn bản thuần, không có JSON), cũng là mã 2. |
| 3 | `deps_missing` | Thiếu dtach, claude, aipager-hook hoặc aipager-statusline; `fix` có các lệnh cần chạy. Không có gì được ghi. |
| 4 | `token_rejected` | Telegram từ chối token. |
| 5 | `chat_not_started`, `bot_blocked` | Người dùng chưa chạm Start trong bot (“Open t.me/&lt;bot&gt; and press Start, then run this again.”), hoặc đã chặn bot. Không có gì được ghi. |
| 6 | `existing_install` | Một token, chat hay vai trò khác đã được thiết lập; hãy thêm `--force`. |
| 7 | `telegram_unreachable` | Lỗi mạng, hoặc Telegram trả lời 429 hay 5xx. |
| 8 | `detect_timeout` | detect-chat không thấy tin nhắn riêng nào trước khi hết `--timeout` (kèm cảnh báo `updates_backlog` khi 100 bản cập nhật cũ chưa đọc che khuất những bản mới hơn). |
| 9 | `daemon_running` | detect-chat: một aipager đang chạy đang giữ các tin nhắn của bot. |
| 10 | `updates_conflict` | detect-chat: một chương trình khác đang đọc tin nhắn của bot này. |
| 130 | `interrupted` | Ctrl-C. |

Khi nhiều bước kiểm tra cùng thất bại, mã thoát theo thứ tự này: `config_malformed`, `ambiguous_install`, `existing_install`, `settings_invalid`, `deps_missing`. `--dry-run` thoát với mã mà lần chạy thật sẽ có từ các bước kiểm tra này.

### <a id="json-aipager-setup---json"></a>JSON: `aipager setup --json`

Mọi khóa luôn có mặt; một giá trị chưa biết là `null`.

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

- `status`: `installed`, `updated`, `unchanged`, `dry_run` hoặc `error`.
- `changed`: những gì đã thay đổi (hoặc sẽ thay đổi, khi chạy thử), theo thứ tự này: `migrated_v1`, `bot_token`, `owner_dm`, `role`, `settings_json`, `service`.
- `test_message`: `sent`, `not_needed`, `skipped_dry_run`, `failed` hoặc `not_attempted`.
- `settings_json.status`: `created`, `patched`, `unchanged`, `would_change` hoặc `not_checked`.
- `daemon.reload`: `reloaded`, `refused`, `not_reloaded` hoặc `not_needed`. `restart_needed` là true sau một token mới trong lúc một aipager đang chạy, và sau mọi thay đổi khi aipager không tải lại được hoặc setup không biết được có aipager nào đang chạy không.
- `service.result`: `not_requested`, `installed`, `already_installed`, `skipped_daemon_running`, `would_install`, `failed`, hoặc `null` khi setup đã dừng trước bước đó.
- `warnings`: các đối tượng có `code` (`token_file_shared`, `reload_refused`, `daemon_unknown`, `audit_write_failed`) và `message`. `audit_write_failed`: quyền chủ sở hữu đã được ghi, nhưng bản ghi `grant-owner` của nó không thêm được vào log kiểm tra; bản ghi chỉ làm hết sức có thể, nên mã thoát vẫn là 0.
- `next_step` sau một thay đổi trong lúc một aipager đang chạy mà không tải lại được sẽ bảo khởi động lại nó (`aipager service stop`, rồi `aipager service start`); nó chỉ nhắc tới token bot khi token đã đổi.
- Với cảnh báo `daemon_unknown`, `next_step` không bao giờ nói aipager đang chạy. Nó nói setup không biết được, chỉ tới `aipager doctor --json`, và bảo khởi động aipager bằng `aipager service install` hoặc `aipager start` nếu nó chưa chạy. Khi cần khởi động lại, nó chỉ yêu cầu khởi động lại nếu có một aipager đang chạy, nếu không thì bảo khởi động nó theo cùng cách.
- Khi có lỗi, `status` là `error` và `error`, `message` cùng `fix` cho biết điều gì sai và cần làm gì; `next_step` nhắc lại `fix`. Khi lỗi xảy ra sau một lần ghi, `changed` liệt kê những gì đã được ghi, và đầu ra thường thêm một dòng stderr `Already written: aipager.yaml, ... (changed: ...)`.

### <a id="json-aipager-setup-detect-chat---json"></a>JSON: `aipager setup detect-chat --json`

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

`candidate` là `null` trừ khi `status` là `found`. `warnings` chứa các đối tượng có `code` (`token_file_shared`, `updates_backlog`) và `message`. `other_candidates` đếm những người khác đã nhắn cho bot trong cùng 10 phút: khi nó khác 0, hãy chắc chắn rằng bạn đã chọn đúng người. `next_step` lặp lại cờ token bạn đã dùng, không bao giờ lặp lại chính token.

### <a id="aipager-doctor---json"></a>`aipager doctor --json`

Các bước kiểm tra của `aipager doctor` dưới dạng một đối tượng JSON trên stdout. Như `aipager doctor` thường, nó chỉ đọc và không gửi tin nhắn Telegram nào. Thoát với mã 1 khi có bước kiểm tra thất bại, nếu không thì 0. Nó không dùng chung được với `--fix` hay `--safety-check` (thoát với mã 2 kèm một đối tượng lỗi).

```json
{
  "command": "doctor",
  "version": "1.0.1",
  "ok": true,
  "summary": {"ok": 14, "warn": 1, "fail": 0},
  "checks": [{"key": "token_valid", "status": "ok", "title": "Telegram bot token", "detail": ["@example_bot"], "fix": null}]
}
```

`checks` theo thứ tự này, với các khóa này: `config_parses`, `config`, `token_valid`, `chat_reachable`, `team`, `role_shell_access`, `peer_messages`, `claude`, `claude_auth`, `dtach`, `hook_scripts`, `settings_json`, `daemon`, `service_installed`, `service_unit_path`, `miniapp`. Một bước kiểm tra bị crash vẫn giữ khóa của nó với trạng thái `warn`. Văn bản là văn bản thuần (không có định dạng), và mọi thứ trông giống token bot được thay bằng `<redacted>`.

## <a id="command-line-aipager-uninstall"></a>Dòng lệnh: `aipager uninstall`

`aipager uninstall` dừng aipager và gỡ dịch vụ của nó, xóa thư mục cấu hình (`~/.config/aipager`: token bot, các chat và cài đặt) và danh sách phiên (`~/.claude/aipager-sessions.json`), dọn các file của aipager trong `/tmp`, và gỡ chính aipager. Nó hỏi trước (`-y` bỏ qua câu hỏi).

Nó giữ lại dữ liệu của aipager, để lần cài lại tiếp tục từ chỗ cũ: `~/.local/share/aipager` (thư mục phiên, các chương trình phụ trợ đã tải, file khóa và file đánh dấu), `~/.local/state/aipager` (bản ghi báo cáo sự cố và trạng thái ngôn ngữ), `~/.claude/aipager-audit.jsonl` (log các câu trả lời cho yêu cầu cấp quyền), `~/.claude/aipager-flood-state.json` (lịch sử giới hạn tốc độ của Telegram), `~/.claude/aipager-pending-users.json` (những người đã thử dùng bot mà không có quyền) và `~/aipager-users` ([thư mục gốc](groups.md#root-folders) của các thành viên, cùng công việc của họ). Trước khi hỏi, nó liệt kê những thứ có trên máy tính của bạn, và cuối cùng nó in ra lệnh `rm -rf` để xóa luôn cả chúng.

Nó cũng gỡ các mục riêng của aipager khỏi `~/.claude/settings.json` của Claude Code: các hook chạy `aipager-hook` và dòng trạng thái chạy `aipager-statusline` (một dòng trạng thái của riêng bạn thì được giữ). Mọi thứ khác trong file đó giữ nguyên, và file ở trạng thái trước đó được giữ bên cạnh thành `settings.json.bak.<time>`. Nếu file là một symlink tới file khác, không đọc được, không phải JSON hợp lệ, hoặc không có dạng mà Claude Code đọc, nó được để y nguyên và uninstall nói rõ những mục nào cần gỡ bằng tay. Một hook của riêng bạn có nhắc tới `aipager-hook` hay `aipager-statusline` (ví dụ một lớp bọc `aipager-hook-capped.sh`) cũng được giữ, và uninstall nêu tên nó. Các phiên Claude Code đang mở vẫn chạy các hook mà chúng đã nạp cho tới khi bạn khởi động lại chúng. `aipager config` thêm lại các mục sau khi cài lại. Bot Telegram của bạn và các bản sao lưu `settings.json.bak.*` cũ hơn không bị đụng tới, `skipDangerousModePermissionPrompt` (cài đặt bỏ qua lời cảnh báo của Claude Code trước khi nó chạy với việc kiểm tra quyền bị tắt) cũng vậy, vì aipager có đặt nó nhưng có thể chính bạn cũng đã đặt.

## <a id="command-line-aipager-report"></a>Dòng lệnh: `aipager report`

`aipager report` tạo một báo cáo sự cố cùng loại với nút “Báo cáo sự cố” của bot, từ những gì aipager đã ghi lại trên máy tính này (với ít thông tin hơn về aipager đang chạy: chế độ, các chat, tính năng, thời gian chạy, các phiên và chi tiết Claude Code được để trống hoặc ghi là không biết), in toàn bộ ra, và hỏi `Send this report? [y/N]`. Chỉ `y` hoặc `yes` mới gửi; mọi thứ khác, hoặc không có terminal (một script, một pipe, shell của một phiên Claude Code), đều không gửi gì. `--note "text"` thêm một ghi chú tối đa 500 ký tự, hiện trong báo cáo được in ra. Nó thoát với mã 0 khi báo cáo đã được gửi, không được gửi theo lựa chọn của bạn, hoặc chỉ được in ra, và mã 1 khi một lần gửi bạn đã xác nhận không đi được. Xem [Báo cáo sự cố](problem-reports.md) để biết một báo cáo chứa gì và được gửi đi đâu.

## <a id="settings-in-the-environment"></a>Cài đặt từ môi trường

Một vài cài đặt được đọc từ môi trường của aipager thay vì từ `aipager.yaml`. Với dịch vụ systemd, hãy thêm chúng bằng `systemctl --user edit aipager` dưới `[Service]` (`Environment=STALE_BUSY_TIMEOUT=900`), rồi `aipager service stop` và `aipager service start`; với dịch vụ launchd trên macOS, hãy thêm chúng vào `EnvironmentVariables` của file plist (`~/Library/LaunchAgents/com.aipager.daemon.plist`; `aipager service install` ghi lại file đó, nên hãy thêm lại sau khi chạy nó); với một aipager bạn tự khởi động, hãy đặt chúng trước `aipager start`. Không cài đặt nào trong số này cần cho việc dùng bình thường.

| Cài đặt | Mặc định | Thay đổi gì |
|---|---|---|
| `AIPAGER_WHISPER_MODEL` | `base` | Mô hình nhận dạng giọng nói cho tin nhắn thoại (`tiny`, `base`, `small`, `medium`, ...): lớn hơn thì chính xác hơn và chậm hơn. |
| `MINIAPP_PORT` | `8765` | Cổng của Mini App trên `127.0.0.1` (`aipager miniapp enable --port` thì lưu nó lại). |
| `MINIAPP_ENABLED` | theo `aipager.yaml` | `0` tắt Mini App và `1` bật nó, bất kể `aipager.yaml` nói gì (`aipager miniapp enable\|disable` thì lưu lựa chọn lại). |
| `MINIAPP_PUBLIC_URL` | không có | Phục vụ Mini App ở URL `https://` riêng của bạn thay vì tunnel được quản lý ([bảo mật](security.md#mini-app-tunnel)). |
| `KEEP_FINISHED_CARD` | `1` | `0` đặt “Chỉ câu trả lời, xóa thẻ” làm bố cục tin nhắn cho các chat chưa bao giờ chọn bố cục trong `/settings`. |
| `STALE_BUSY_TIMEOUT` | `7200` (2 giờ) | Số giây một phiên đang làm việc có thể im lặng trước khi chat nhận một ghi chú “vẫn đang làm việc (120 phút chưa có tin)”. |
| `PROMPT_HOOK_GRACE_SECONDS` | `8` | Số giây trước khi một tin nhắn mà Claude Code không bao giờ nhận được đánh dấu là “Claude Code chưa nhận tin nhắn” ([tin nhắn tự do](#text)). |
| `FINISH_CARD_GRACE_SECONDS` | `0.8` | Thẻ đã xong đi trước câu trả lời bao lâu trong bố cục có thẻ; `0` gửi cả hai cùng lúc. |
| `GONE_SESSION_MAX_AGE_DAYS` | `14` | Số ngày một phiên đã kết thúc còn ở trong `/resume`; `0` giữ chúng mãi. |
| `STREAM_EDIT_INTERVAL`, `BUSY_EDIT_INTERVAL` | `1.2`, `3.0` | Nhịp làm mới của thẻ làm việc khi đang chạy chữ và khi im ắng, tính bằng giây; giới hạn theo từng chat của Telegram vẫn thắng ([xử lý sự cố](troubleshooting.md#busy-cards-got-slower)). |
| `TYPING_INDICATOR_INTERVAL` | `4.5` | Số giây giữa các bong bóng “đang nhập…”; `0` tắt chúng. |
| `TELEGRAM_MAX_RETRY_AFTER` | `90` | Thời gian chờ của Telegram, tính bằng giây, mà vượt quá thì aipager coi một lần giới hạn tốc độ là một lệnh chặn (đừng hạ nó; xem [xử lý sự cố](troubleshooting.md#the-bot-went-quiet-flood-control)). |
| `CLAUDE_RICH_SUMMARIES` | `1` | `0` tắt định dạng phong phú cho các câu trả lời có khối mã (một cách dự phòng giữ lại từ các phiên bản cũ). |
| `AIPAGER_INTERACTIVE_TIMEOUT` | `300` | Số giây một yêu cầu cấp quyền hay câu hỏi có thể chờ mà không có tin gì từ Claude Code trước khi aipager xem lại cuộc trò chuyện đã lưu; phiên chỉ được tính là đang làm việc trở lại nếu cuộc trò chuyện cho thấy yêu cầu đã xong ([xử lý sự cố](troubleshooting.md#a-permission-prompt-with-no-news-for-5-minutes)). |
| `AIPAGER_SUBAGENT_SILENCE` | `1800` | Số giây im lặng mà sau đó một subagent chưa thấy dừng sẽ bị bỏ ([hook](hooks.md#subagentstart--subagentstop)). |
| `AIPAGER_BG_SHELL_MAX_TRACK` | `7200` | Số giây mà sau đó một lệnh chạy nền chưa thấy kết thúc thôi được đếm. |
| `AIPAGER_REPORT_PROMPTS` | bật | `0` tắt các đề nghị gửi báo cáo sự cố tự động ([báo cáo sự cố](problem-reports.md#when-aipager-offers-a-report)). |
| `AIPAGER_CLAUDE_BIN` | không có | Chương trình `claude` cần chạy, khi có nhiều bản được cài (`claude_path` trong `aipager.yaml` thắng). |
| `AIPAGER_SOCKET_PATH` | `$XDG_RUNTIME_DIR/aipager.sock` | Socket điều khiển của aipager. Các phiên do aipager khởi động truyền nó cho hook của chúng. |
| `AIPAGER_DEBUG` | tắt | `1` trong môi trường của một phiên khiến `aipager-hook` và `aipager-statusline` in thông tin chẩn đoán ra stderr. |
| `AIPAGER_INSTANCE_DIR`, `AIPAGER_TELEGRAM_API_BASE` | không có | Một aipager thứ hai, tách biệt, để thử nghiệm ([kiến trúc](architecture.md#isolated-instance-for-testing)). |

## <a id="see-also"></a>Xem thêm

- [Kiến trúc](architecture.md): bot nằm ở đâu.
- [Sự kiện hook](hooks.md): aipager nghe được gì từ claude.
- [Xử lý sự cố](troubleshooting.md): khi các lệnh chạy không như ý.
