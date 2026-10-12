# Xử lý sự cố

<!-- i18n-source: docs/troubleshooting.md @ 7e36944 -->

> Trang này được dịch từ tiếng Anh; nếu hai bản khác nhau, bản tiếng Anh là bản đúng. ([English](../../troubleshooting.md))

Các lỗi hay gặp và cách khắc phục. Khi không chắc, hãy chạy:

```sh
aipager doctor
```

Lệnh này chạy lần lượt mọi bước kiểm tra bên dưới và in ra gợi ý khắc phục chuẩn cho mỗi lỗi. Mã nguồn: `aipager/doctor.py`.

## <a id="another-aipager-daemon-already-owns-the-socket"></a>“Another aipager daemon already owns the socket”

Hai aipager không thể dùng chung một socket điều khiển. Lỗi này có nghĩa là một aipager vẫn đang chạy và trả lời trên socket đó (socket là `$XDG_RUNTIME_DIR/aipager.sock`, hoặc `/tmp/aipager.sock` khi không đặt `$XDG_RUNTIME_DIR`). Một file socket còn sót lại từ một aipager đã crash thì không sao: lần khởi động sau sẽ xóa nó và tạo socket mới. Trước tiên hãy dừng aipager đang chạy:

```sh
aipager service stop          # nếu nó chạy như dịch vụ
pkill -f 'aipager start'      # nếu không
```

Nếu bước kiểm tra socket bỏ sót, `aipager start` vẫn từ chối với `aipager is already running (pid=N)` và nêu tên file khóa của nó (`~/.local/share/aipager/daemon.lock`): hãy dừng tiến trình đó theo cùng cách.

## <a id="telegram-bot-doesnt-respond"></a>Bot Telegram không phản hồi

1. `aipager status`: nếu dòng aipager ghi `not running`, hãy khởi động nó.
2. `aipager doctor`: các bước kiểm tra `token_valid` và `chat_reachable` gọi API Telegram từ đầu tới cuối và cho thấy chính xác lỗi.
3. Sai chat: chạy `aipager config` và chọn **Test bot reachability**. Để sửa một chat riêng bị sai, thêm chat đúng bằng **Add a DM scope** (xem [cách tìm ID](groups.md#finding-ids-auto-detect)), rồi xóa chat sai trong **Edit a scope**.
4. Bot chưa bao giờ nhận `/start`: mở bot trong Telegram và chạm Start một lần.

## <a id="busy-cards-got-slower"></a>Thẻ làm việc chậm hơn

Telegram cho phép khoảng một tin nhắn mỗi giây vào một chat (và 20 tin mỗi phút vào một nhóm). Mỗi tin nhắn và mỗi lần sửa đều được tính. aipager cho mỗi chat một ngân sách riêng khoảng 1 lần gọi mỗi giây, có chút dư cho lúc dồn dập, và các thẻ làm việc của chat đó **dùng chung** ngân sách này. Dấu hiệu “đang nhập…” cũng nằm trong ngân sách đó, là thứ **ít quan trọng nhất** mà chat gửi đi (xem “Bong bóng đang nhập” bên dưới).

Phần của bong bóng được dành ra trước (một lần gọi mỗi 4,5 giây trong lúc việc đang chạy lâu nhất của chat còn chưa tới mười phút, thưa hơn khi việc đó kéo dài; xem “Bong bóng đang nhập”), và các thẻ chia nhau phần còn lại. Vì vậy một thẻ trong chat riêng được làm mới khoảng mỗi 4,8 giây; với hai phiên cùng làm việc trong một chat, mỗi thẻ khoảng mỗi 9,7 giây; với ba phiên, khoảng mỗi 14,5 giây. **Thẻ chậm hơn là ngân sách đang làm đúng việc của nó, không phải lỗi.** Câu trả lời, tin trả lời và phản hồi của nút bấm không bao giờ bị làm chậm để nhường chỗ cho thẻ: chúng luôn có một lượt dành riêng, còn một lần sửa thẻ không đủ lượt thì đơn giản là bị bỏ qua và thử lại ở vòng sau, thay vì xếp hàng trước câu trả lời của bạn.

Trong một **nhóm**, giới hạn là 20 lần gọi mỗi phút dù nhóm có bao nhiêu phiên, và riêng bong bóng đã dùng hết hai phần ba số đó. Các thẻ của nhóm cộng lại vẫn giữ một lần làm mới mỗi 10 giây (11 giây cho một thẻ), và bong bóng lấy phần còn lại: vì vậy trong nhóm, thỉnh thoảng bong bóng có thể biến mất một hai giây.

Để thẻ nhanh hơn: chạy ít phiên cùng lúc hơn trong mỗi chat, hoặc cho các phiên bận nhất một chat riêng (`aipager config`). Bạn cũng có thể chỉnh `STREAM_EDIT_INTERVAL` (mặc định `1.2`, nhịp khi thẻ đang chạy chữ) và `BUSY_EDIT_INTERVAL` (mặc định `3.0`, khi thẻ im ắng), dưới dạng biến môi trường của aipager (xem [cài đặt từ môi trường](commands.md#settings-in-the-environment)). Đặt một trong hai *thấp hơn* mức sàn của chat thì vô hại và không đổi gì: mức sàn thắng, và chính điều đó giữ chat dưới giới hạn của Telegram dù bạn đặt gì.

### <a id="a-long-turns-card-refreshes-less-often"></a>Thẻ của một việc kéo dài được làm mới thưa hơn

Nhịp ở trên áp dụng cho hai phút đầu của một việc. Sau đó thẻ **chậm dần khi việc kéo dài**, và từ phút thứ 10 đồng hồ của nó nhảy theo bước 30 giây, khớp với nhịp làm mới, nên nó không bao giờ trông như bị đứng:

| việc đã chạy | thẻ làm mới nhiều nhất mỗi | đồng hồ hiện |
|---|---|---|
| 0–2 phút | như ở trên (khoảng 4,8 giây cho một thẻ trong chat riêng) | `0:45` |
| 2–10 phút | 10 giây | `4:10` |
| 10–60 phút | 30 giây | `23:00`, `23:30` |
| hơn 60 phút | 30 giây | `1:23:00`, `1:23:30` |

Đồng hồ được làm tròn xuống theo bước 30 giây, và các lần làm mới cách nhau ít nhất 30 giây, đôi khi lâu hơn một chút. Vì vậy thỉnh thoảng thẻ nhảy cóc một bước: một lần làm mới lúc 23 phút 29 giây hiện `23:00`, và nếu lần tiếp theo đến lúc 24 phút 1 giây thì nó hiện `24:00`, nên `23:30` bị bỏ qua.

Một dòng công cụ mới hay một câu mới sẽ chờ lần làm mới tiếp theo; một **thay đổi trạng thái** (phiên chuyển từ đang làm việc sang chờ một agent chạy nền, và ngược lại) được hiện ngay, nhiều nhất một lần mỗi 10 giây. Các dòng agent đang chạy và dòng đang chờ dùng cùng các bước đó. Thẻ đã xong hiện thời lượng của việc theo đúng số giây (`23:17`).

Đây là điều ngăn một việc dài bốn tiếng sửa một tin nhắn hàng nghìn lần: ngày 2026-09-23 một thẻ đã làm đúng như vậy, và chat bị chặn bảy tiếng. Để giữ nhịp nhanh suốt cả việc, hãy tắt **`/settings` → ⏱ Cập nhật thẻ khi việc kéo dài** (cho cả chat, hoặc cho từng phiên từ Mini App hay `👤 Tùy chọn theo phiên`).

### <a id="the-typing-bubble"></a>Bong bóng đang nhập

Bong bóng “đang nhập…” được gửi bởi **một** vòng lặp cho mỗi chat, dù có bao nhiêu phiên đang làm việc trong đó (trong mọi ứng dụng Telegram, bong bóng là của cả chat), mỗi `TYPING_INDICATOR_INTERVAL` giây (mặc định 4,5: Telegram xóa trạng thái đang nhập sau 5 giây; `0` tắt bong bóng). Trong ngân sách của chat, nó được tính là thứ ít quan trọng nhất mà chat gửi đi:

- nó **chậm dần khi việc kéo dài**, theo thời gian của việc đang chạy lâu nhất trong chat: mỗi 4,5 giây trong mười phút đầu (bong bóng sáng liên tục), sau đó mỗi 15 giây; Telegram hiện nó trong 5 giây, nên bong bóng của một việc đã lâu sẽ nhấp nháy thay vì sáng liên tục. Ở mức 4,5 giây, riêng nó đã là 800 lần gọi mỗi giờ và giới hạn mỗi giờ (bên dưới) sẽ tắt nó trong những khoảng dài; tắt **⏱ Cập nhật thẻ khi việc kéo dài** sẽ giữ nó ở 4,5 giây;
- nó **hiện ngay từ đầu mỗi việc**; một thẻ còn mới được làm mới chậm hơn một chút để nhường chỗ: phần của bong bóng được dành ra trước khi các thẻ chia nhau phần còn lại (ở trên), nên cả hai cộng lại không bao giờ vượt giới hạn của chat;
- nó không bao giờ lấy lượt mà một lần sửa thẻ cần: một bong bóng đến hạn ngay sau một lần sửa thẻ sẽ chờ nửa giây (`TYPING_RETRY_WAKE`) rồi thử lại, nên nó trễ nhiều nhất một hai giây, không bao giờ bị mất;
- nó là thứ đầu tiên bị bỏ khi lượng gửi mỗi giờ của chat lên cao (bên dưới);
- một lỗi 429 trên chính bong bóng chỉ chặn bong bóng, trong đúng khoảng thời gian Telegram yêu cầu (các thẻ vẫn tiếp tục), nhưng nó bắt đầu giai đoạn cảnh báo sáu tiếng của chat (tốc độ bị giới hạn ở 0,5 lần gọi/giây, nên các thẻ cũng chậm lại);
- một **lệnh chặn** trên bong bóng (`retry_after` vượt quá ngưỡng bên dưới) tắt việc gửi vào chat y như lệnh chặn trên bất kỳ lần gọi nào khác: không có gì được gửi vào đó nữa, và câu trả lời của việc được giữ lại rồi gửi khi lệnh chặn hết.

Trước đây bong bóng này miễn phí (một vòng lặp cho mỗi phiên đang làm việc, nằm ngoài mọi ngân sách), cho tới ngày 2026-09-23 hai phiên trong một chat riêng đã gửi nó khoảng 5.400 lần trong ba tiếng và Telegram giới hạn tốc độ chính bong bóng.

## <a id="the-bot-slowed-down-a-rate-limit-429"></a>Bot chậm lại: giới hạn tốc độ (429)

Nếu Telegram vẫn giới hạn tốc độ một chat, nó trả lời một lần gọi bằng `429` kèm `retry_after` vài giây. aipager ngừng gọi vào chat đó đúng chừng ấy thời gian, sau đó gửi lại một lần đã bị hoãn, và **nhân đôi khoảng cách làm mới thẻ của chat đó** (tối đa ×8). Mỗi phút im ắng lại chia đôi nó cho tới khi về bình thường.

Bạn sẽ thấy:

- `aipager status` (và dòng `aipager daemon` của `aipager doctor`) hiện **`Telegram flood backoff ×4 (chat …), last 429 12 s ago`**.
- Đúng một dòng trong `aipager logs`, không có traceback: `flood: chat … 429 retry_after=5s → cadence ×2`.
- Không có gì bị tắt, và **không câu trả lời nào bị mất**: nó được hoãn lại, không bị bỏ.
- **Tốc độ gửi mà chat đã giành được giảm một nửa**, và chat bước vào **giai đoạn cảnh báo sáu tiếng** (`FLOOD_WARNING_HOURS`): tốc độ của nó không được vượt quá 0,5 lần gọi/giây (`FLOOD_WARNED_CEILING`), và nó tăng trở lại chậm. `aipager status` cho thấy điều đó: `Telegram chat 123: rate 0.25/s (ceiling 0.50/s), 212/1200 calls in the last hour (as of 18s ago), 7/30 in the last minute - warning regime, 5h 52m left`.

Bạn không cần làm gì. Nó tự hết. Dòng gửi chậm biến mất khi chat đã im ắng một hai phút; tốc độ tăng lại 0,1 lần gọi/giây sau mỗi khoảng im ắng (bình thường là một phút, trong giai đoạn cảnh báo là 36 phút), và mức trần đầy đủ trở lại khi giai đoạn đó kết thúc. Trong suốt thời gian đó `aipager doctor` vẫn giữ dòng aipager màu xanh: một chat đang gửi chậm lại là hoạt động bình thường. (Trước đây một lỗi 429 được bỏ qua sau khoảng năm phút. Lời cảnh báo duy nhất của Telegram trước lệnh chặn ngày 2026-09-23 đến sớm hơn 3 giờ 23 phút, và chỉ vài phút sau lời cảnh báo đó chat đã quay lại tốc độ tối đa.)

### <a id="the-hourly-budget"></a>Ngân sách mỗi giờ

Mỗi chat còn có một **giờ** trượt: tối đa `FLOOD_HOURLY_MAX` (1200) lần gọi trong bất kỳ 60 phút nào, tính chung bong bóng đang nhập, lần sửa thẻ và câu trả lời; chỉ có reaction là được miễn. 120 lần cuối (`FLOOD_HOURLY_ESSENTIAL_RESERVE`) được dành cho câu trả lời, tin trả lời và yêu cầu:

- ở mức 810 lần gọi trong giờ, bong bóng đang nhập tạm dừng (nó quay lại khi xuống dưới 648);
- ở mức 1080, chat bước vào **chế độ tối thiểu** (bên dưới) cho tới khi giờ đó giảm về 864;
- câu trả lời không bao giờ bị từ chối vì giới hạn mỗi giờ; nếu chúng có lúc vượt quá 1200, log sẽ ghi lại điều đó mỗi giờ một lần.

`aipager status` hiện số lần gọi của chat trong giờ vừa qua so với ngân sách của nó. Con số lấy từ file trạng thái mà aipager ghi lại nhiều nhất mỗi phút một lần trong lúc có lần gọi, nên nó được ghi kèm độ cũ. Hai phiên bận rộn gửi vào một chat riêng hàng giờ liền vẫn nằm xa dưới ngân sách (sự cố ngày 2026-09-23, khi chạy lại: khoảng 900 lần gọi trong giờ bận nhất, so với khoảng 3.400 theo mô phỏng cho cùng khối lượng việc khi không có ngân sách).

### <a id="the-earned-rate-and-why-your-cards-may-be-slower-than-they-were"></a>Tốc độ giành được, và vì sao thẻ của bạn có thể chậm hơn trước

Từ bản 0.7.13, tốc độ gửi ổn định của mỗi chat **được học ra chứ không phải đoán trước**. Telegram không công bố giới hạn thực sự áp dụng cho bot của bạn (nó phụ thuộc vào lịch sử gần đây của tài khoản bạn), nên aipager cho mỗi chat bắt đầu ở 0,5 lần gọi/giây, cộng 0,1 cho mỗi phút im ắng tới mức trần 1/giây, chia đôi khi gặp 429 và hạ xuống 0,05 sau một lệnh chặn. Vì vậy một bot vừa bị chặn sẽ gửi cẩn thận hơn nhiều so với một bot chưa từng bị, và nó giành lại tốc độ trong những giờ sau đó.

Nếu tốc độ của một chat xuống dưới 0,2 lần gọi/giây, hoặc phần ngân sách mỗi giờ dành cho thẻ và bong bóng đã dùng hết, chat bước vào **chế độ tối thiểu**: thẻ làm việc ngừng chuyển động và chỉ hiện một dòng tĩnh `⏳ x1 · đang làm việc (thẻ tạm thời không cập nhật)`, bong bóng đang nhập dừng, và thanh trạng thái được ghim hiện `⏸ thẻ tạm thời không cập nhật (giới hạn mỗi giờ của Telegram)` (hoặc `(giới hạn của Telegram)` khi chính tốc độ là thứ đưa chat vào đó), ngoài ra không thay đổi gì cho tới khi chế độ này kết thúc. **Câu trả lời, tin trả lời và yêu cầu cấp quyền vẫn tiếp tục được gửi**: đó chính là mục đích. Thẻ chiếm khoảng 95% những gì bot này gửi và câu trả lời khoảng 5%, nên khi bị ép, nó bỏ bớt phần hình ảnh chứ không bỏ công việc. `aipager doctor` báo chế độ tối thiểu như một cảnh báo để một thẻ đang tạm dừng không bao giờ bị nhầm là thẻ hỏng; nó tự kết thúc khi tốc độ hồi phục.

## <a id="the-bot-went-quiet-flood-control"></a>Bot im lặng: Telegram chặn gửi (flood control)

Một `retry_after` vượt quá `TELEGRAM_MAX_RETRY_AFTER` (mặc định 90 giây) không phải giới hạn tốc độ mà là một **lệnh chặn**, và nó có thể kéo dài hàng giờ. Nhìn từ chat thì trông như bot đã chết: tin nhắn vẫn tới Claude, các phiên vẫn làm việc, nhưng không có câu trả lời nào quay về.

Bạn sẽ thấy:

- `aipager status` (và dòng `aipager daemon` của `aipager doctor`) hiện **`Telegram flood-muted until HH:MM (chat …)`**.
- Một dòng trong `aipager logs`: `Telegram flood control — chat … muted for Ns (until HH:MM) …`, rồi im lặng với chat đó. Sau đó là một dòng `flood mute on chat … lifted`.
- `aipager status` còn hiện đầy đủ trạng thái của chat: `Telegram chat 123: rate 0.05/s (ceiling 0.50/s), 431/600 calls in the last hour (as of 12s ago) - MINIMAL MODE, card updates paused - flood-muted until 09:41 - warning regime, 5h 59m left (1 ban(s) in the last 7 days)`.

aipager tắt việc gửi vào chat đúng khoảng thời gian Telegram yêu cầu và **không gọi bất kỳ lần nào, dưới bất kỳ hình thức nào** vào đó: không câu trả lời, không sửa thẻ làm việc, không file đính kèm, không reaction, không dấu hiệu đang nhập, không thông báo nhanh sau khi chạm nút, và không cả các câu trả lời mà lệnh của bạn lẽ ra tạo ra. Mỗi thứ như vậy sẽ là một vi phạm mới làm lệnh chặn kéo dài thêm; ngày 2026-09-15, một yêu cầu gửi vào giữa một lệnh chặn đang có hiệu lực đã biến lệnh chặn 21 phút thành 9,5 tiếng. Vì vậy một lệnh gõ trong lúc bị tắt gửi sẽ không nhận được trả lời nào cả, và **một lần chạm nút cũng không được xác nhận**: cho tới bản 0.7.12 thông báo nhanh vẫn được gửi, với giả định rằng Telegram tính nó riêng. Về nhịp gửi thì đúng là vậy; về lệnh chặn thì không. Các chat khác không bị ảnh hưởng.

**Nhắn cho bot trong lúc bị chặn là an toàn, và không có gì bị mất.** Không có gì bạn làm trong chat khiến bot gọi Telegram trong lúc chat bị tắt gửi, nên nó không thể làm lệnh chặn kéo dài thêm:

- Một tin nhắn bạn gửi (chữ, tin nhắn thoại, file, mẫu nhanh) vẫn tới Claude và phiên vẫn làm việc với nó. Thẻ làm việc của nó được giữ lại, không bị bỏ: nếu việc vẫn đang chạy khi lệnh chặn hết, thẻ sẽ hiện ra lúc đó. Sau một lệnh chặn, chat còn ở chế độ tối thiểu một thời gian, nên thẻ có thể đến muộn hơn lúc hết chặn vài phút. Nếu việc đã xong trong lúc bị chặn, bạn nhận được câu trả lời và không có thẻ.
- **🔄 Thử lại** không làm gì trong lúc bị chặn. Tin nhắn không được gửi lại và nút vẫn còn, nên bạn có thể chạm lại sau khi hết chặn. Cho tới bản 0.7.14, tin nhắn vẫn đi tới Claude trong khi thông báo lỗi và nút của nó vẫn còn, nên chạm lần hai sẽ gửi nó thêm lần nữa.
- Bàn phím chính cũng được giữ lại, kể cả bàn phím mà một lần khởi động lại gửi đi (cho tới bản 0.7.14 nó bị mất cho tới khi có gì khác làm mới nó). Nó được gửi sau khi hết chặn.

Sau khi hết chặn, mọi thứ đến theo thứ tự này: các câu trả lời bị giữ, rồi thẻ của các việc vẫn đang chạy, rồi bàn phím. Một thẻ phải chờ chế độ tối thiểu kết thúc thì đến sau bàn phím.

**Câu trả lời không bị mất.** Một câu trả lời xong trong lúc chat bị tắt gửi sẽ được giữ lại và gửi đi trong vòng vài giây sau khi hết chặn, với dòng đầu tiên là `⏳ đến muộn: bị giữ 42 phút vì giới hạn của Telegram`. Bạn không cần hỏi lại, và nếu đã hỏi lại thì cả hai câu trả lời đều đến, cái cũ trước: một lệnh chặn kéo dài hàng giờ trải qua nhiều việc và câu trả lời của mỗi việc đều là của bạn. (Câu trả lời bị giữ nằm trong bộ nhớ: nếu bạn khởi động lại aipager trong lúc còn câu trả lời đang chờ, chúng sẽ mất; log lúc tắt ghi rõ bao nhiêu. Một chat giữ tối đa 20 câu trả lời, và không giữ gì cũ hơn 24 giờ; mọi thứ bị bỏ vì một trong hai lý do đó đều có một cảnh báo trong log nêu rõ đó là gì.)

**Một lệnh chặn làm chat chậm hơn sau đó, chứ không nhanh hơn.** Ngay khi lệnh chặn được ghi nhận, tốc độ học được của chat rơi xuống mức sàn và lệnh chặn được đếm; những giờ bị tắt gửi không giành lại được gì, việc tăng tốc bắt đầu lại từ lúc hết chặn, và trong **bảy ngày** (`FLOOD_BAN_MEMORY_DAYS`) mức trần tốc độ *và* ngân sách mỗi giờ của chat đều bị chia cho một cộng số lệnh chặn trong tuần đó: một lệnh chặn chia đôi cả hai, hai lệnh chặn chia ba. `aipager status` hiện tất cả những điều này. (Trước đây trí nhớ này chỉ là 24 giờ và chỉ chia đôi mức trần; một chat bị chặn ngày 2026-09-19 đã quay lại tốc độ tối đa và bị chặn bảy tiếng vào ngày 2026-09-23.) Năm lệnh chặn trở lên trong một tuần sẽ giữ chat ở chế độ tối thiểu cho tới khi chúng hết hạn: câu trả lời vẫn được gửi, thẻ vẫn tạm dừng.

Những điều KHÔNG nên làm:

- Đừng khởi động lại aipager để “sửa” nó. Lệnh tắt gửi tự hết vào thời điểm đã hiện. Từ bản 0.7.13, khởi động lại không còn quên lệnh chặn nữa (thời hạn, tốc độ giành được của chat và lịch sử lệnh chặn được lưu vào `~/.claude/aipager-flood-state.json` và được khôi phục khi khởi động, số lần gọi trong giờ vừa qua và giai đoạn cảnh báo của chat cũng vậy), nên khởi động lại cũng không còn làm nó kéo dài thêm. Nhưng nó sẽ vứt bỏ mọi câu trả lời còn đang bị giữ, và chẳng sửa được gì.
- Đừng hạ `TELEGRAM_MAX_RETRY_AFTER` xuống dưới mức mặc định 90 giây với hy vọng được thử lại sớm hơn: mọi thứ vượt qua ngưỡng đó là lệnh chặn, không phải giới hạn tốc độ.

Để tránh nó: chạy ít phiên cùng lúc hơn trong mỗi chat, hoặc cho các phiên bận nhất một chat riêng (`aipager config`).

## <a id="a-session-dropped-off-the-pinned-status-bar-it-is-gone"></a>Một phiên biến khỏi thanh trạng thái được ghim (nó đã GONE)

Tiến trình dtach của phiên đó đã thoát (máy khởi động lại, `pkill claude`, ai đó gõ `exit` trong phiên dtach đang kết nối). Hãy tạo lại phiên: cuộc trò chuyện vẫn còn trong thư mục `~/.claude/projects/...` của nó.

Cách dễ nhất là `/resume <label>` trong Telegram (hoặc chỉ `/resume` để chọn từ danh sách): nó khởi động lại phiên và tiếp tục cuộc trò chuyện. Từ dòng lệnh:

```sh
aipager resume <label>             # giống vậy, từ terminal
aipager session <label>            # hoặc một phiên mới, không có lịch sử
aipager session <label> --continue # dtach mới, cuộc trò chuyện gần nhất của thư mục
```

## <a id="a-permission-prompt-with-no-news-for-5-minutes"></a>Một yêu cầu cấp quyền không có tin gì trong 5 phút

Nếu một phiên đang chờ một yêu cầu cấp quyền hay câu hỏi và Claude Code không báo gì trong 5 phút, aipager đọc cuộc trò chuyện đã lưu của phiên. Chỉ khi nó cho thấy yêu cầu đã xong (đã trả lời trong terminal, và tin đó bị mất) thì aipager mới thôi chờ và coi phiên là đang làm việc trở lại. Nếu không, yêu cầu vẫn mở dù mất bao lâu: thẻ và thanh ghim vẫn báo phiên cần bạn trả lời, không có ghi chú "vẫn đang làm" nào được gửi, và tin nhắn của bạn được giữ lại cho đến khi bạn trả lời (xem [văn bản](commands.md#text)). Claude Code thoát khi yêu cầu còn mở thì phiên kết thúc. Các nút của yêu cầu vẫn trả lời hộp thoại nếu nó vẫn đang mở (xem [phản hồi khi rảnh](commands.md#idle-responses)).

Để đổi thời gian chờ:

```sh
AIPAGER_INTERACTIVE_TIMEOUT=300 aipager start    # tính bằng giây
```

## <a id="voice-extra-wont-install-via-telegram"></a>Không cài được tính năng thoại qua Telegram

Nút `[📦 Cài tính năng thoại]` chạy trình cài đặt và sửa tin nhắn của nó vài giây một lần trong lúc cài (`vẫn đang chạy… (25 giây)`). Nếu việc cài thất bại, tin nhắn hiện mã thoát và 500 ký tự cuối trong đầu ra của trình cài đặt. Các nguyên nhân thường gặp:

- Mạng: pip không kết nối được tới PyPI. Kiểm tra kết nối từ máy đang chạy aipager.
- Ổ đĩa: `~/.cache/huggingface/` cần khoảng 74 MB cho mô hình và bộ nhớ đệm wheel của pip cần thêm khoảng 250 MB. Hãy giải phóng dung lượng trước.
- Permission denied: venv chỉ đọc. Chuyển sang `uv tool install`, cách này luôn dùng một venv thuộc về người dùng của bạn.

Nếu bot mất kết nối giữa lúc cài, bản thân việc cài trên máy thường đã xong: hãy khởi động lại aipager và thử lại một tin nhắn thoại.

## <a id="open-app-shows-open-this-page-from-the-telegram-app-to-sign-in"></a>Mở ứng dụng thì thấy “Hãy mở trang này từ ứng dụng Telegram để đăng nhập.”

Trang đã tải, nhưng script Mini App SDK của Telegram (script tạo ra `initData`) thì không, nên trang không có gì để đăng nhập và không gọi API nào cả. Câu thông báo nghe như lỗi của bạn; thực ra nguyên nhân là ở mạng.

Các phiên bản aipager gần đây tự tải script đó và phục vụ nó từ chính nguồn của trang (`/telegram-web-app.js`), nên điện thoại chỉ cần kết nối được tới máy chủ đã gửi trang, không cần cả `telegram.org`. Nếu bạn thấy lỗi này trên một bản cũ, hãy nâng cấp.

Sau đó, theo thứ tự:

1. Xem mã nguồn của trang. Nếu thẻ `<script>` của nó trỏ tới `https://telegram.org/js/telegram-web-app.js`, aipager vẫn chưa tải được script và trang đang tạm tải nó từ Telegram, mà đó chính là trường hợp hỏng trên điện thoại không kết nối được tới `telegram.org`. aipager tải script khi máy chủ Mini App khởi động và thử lại nhiều nhất mỗi ngày một lần, nên khởi động lại aipager sẽ thử lại ngay; `journalctl --user -u aipager | grep "webapp sdk"` cho biết điều gì đã sai (thường là `telegram.org` bị chặn hoặc bị lọc).
2. Nếu thẻ trỏ tới `/telegram-web-app.js`, hãy kiểm tra rằng aipager phục vụ nó: `curl -sI http://127.0.0.1:8765/telegram-web-app.js` trên máy chạy aipager (đổi cổng nếu bạn đã đổi) phải trả lời `200` với `Content-Type: application/javascript`. Bản sao nằm trong `~/.local/share/aipager/webapp-sdk/`.
3. Bạn đã mở URL trong một trình duyệt thường chứ không phải từ nút `/app` hay nút menu của bot: khi đó thông báo này là đúng. SDK chỉ tạo ra `initData` bên trong Telegram.
4. Trang đã mở quá năm phút: `initData` hết hạn và trang yêu cầu được mở lại. Chạm `/app` lần nữa.

## <a id="pyexpat-_xml_setalloctrackeractivationthreshold-on-brew-install"></a>`pyexpat _XML_SetAllocTrackerActivationThreshold` khi cài bằng brew

Gói `python@3.12` của Homebrew được build với một libexpat mới hơn bản có trong hệ thống của bạn, gần như luôn vì **Xcode và Command Line Tools đã lỗi thời** trên macOS Tahoe (26.x). Đầu ra của brew thường cũng nói vậy. Hai cách sửa:

- **Dùng `uv tool install aipager`** thay thế. uv mang theo python riêng, tránh được hoàn toàn vấn đề này. Đây là cách được khuyên dùng trên macOS; xem [README](README.md#install).
- **Cập nhật Xcode và Command Line Tools**:
  ```sh
  sudo rm -rf /Library/Developer/CommandLineTools
  sudo xcode-select --install
  ```
  Hoặc mở Xcode trong App Store và cập nhật lên bản mới nhất.

## <a id="modulenotfounderror-aipager"></a>“ModuleNotFoundError: aipager”

Chương trình aipager của bạn trỏ tới một Python không còn cài aipager nữa (thường sau khi xóa venv). Hãy cài lại:

```sh
uv tool install --reinstall aipager
# hoặc trình cài đặt bạn đã dùng từ đầu: pipx, brew, pip
```

## <a id="the-session-list-was-lost-or-damaged"></a>Danh sách phiên bị mất hoặc bị hỏng

aipager giữ các phiên của nó trong `~/.claude/aipager-sessions.json`, ghi lại một cách nguyên tử (một file mới, rồi đổi tên), nên một lần ghi bị ngắt giữa chừng vẫn để file cũ nguyên vẹn. Nếu dù vậy file không có, không đọc được hoặc không phải JSON hợp lệ, aipager ghi vào log rằng nó bắt đầu lại từ đầu (là cảnh báo khi file có đó nhưng không đọc được) và bắt đầu với một danh sách trống; nó không dừng. File này không có bản sao lưu. Không có gì quan trọng bị mất:

- các phiên đang chạy được tìm lại ở vòng quét đầu tiên, nhờ socket dtach của chúng (`/tmp/claude-dtach-*.sock`);
- mọi cuộc trò chuyện vẫn nằm trong thư mục `~/.claude/projects/...` của chính Claude Code, nên `aipager session <name> --continue` trong thư mục của phiên đó sẽ tiếp tục nó (`--resume` cho bạn chọn);
- những gì mất là danh sách các phiên đã kết thúc (`/resume`), các thẻ đang mở, và những điều khác mà aipager nhớ về từng phiên.

Nếu aipager cứ hỏng lúc khởi động, `aipager logs` (hoặc `journalctl --user -u aipager`) cho biết vì sao; hãy chuyển file sang chỗ khác (`mv ~/.claude/aipager-sessions.json ~/aipager-sessions.json.old`) để loại trừ nó.

## <a id="a-session-launch-fails"></a>Một phiên không khởi động được

Một lần khởi động bị dtach từ chối sẽ hiện lý do trong một dòng ở chat (socket đã tồn tại, shell không chạy được, không có pseudo-terminal, tên quá dài, bị từ chối quyền truy cập, thư mục không tồn tại); thông báo riêng của dtach, kể cả các đường dẫn, nằm trong `aipager logs`.

## <a id="aipager-doctor-check-list"></a>Danh sách kiểm tra của `aipager doctor`

Thứ tự là quan trọng: mỗi bước kiểm tra sau giả định các bước trước đã qua. Một bước kiểm tra bị crash vì môi trường bất ngờ sẽ hiện thành một dòng ⚠ duy nhất nêu tên bước kiểm tra và lỗi; các bước còn lại vẫn chạy.

| Bước kiểm tra (khóa `--json`) | Kiểm tra điều gì | Gợi ý khắc phục |
|---|---|---|
| `config_parses` | `aipager.yaml` và `policy.yaml` đọc được; chạy đầu tiên vì mọi bước sau đều đọc cấu hình | lỗi nêu tên file và dòng |
| `config` | `~/.config/aipager/aipager.yaml` tồn tại và có token cùng một chat | `aipager config` |
| `token_valid` | Token dùng được với `getMe` của Telegram | chạy lại `aipager config` |
| `chat_reachable` | Telegram biết chat đã cấu hình (`getChat`; không gửi gì) | mở bot, chạm Start |
| `team` | Các chat và thành viên (hoặc một `team.yaml` kiểu cũ): cho biết chế độ cá nhân hay chế độ làm việc nhóm, và đánh dấu một `team.yaml` cũ bị hỏng hoặc không khớp | `aipager config` |
| `role_shell_access` | Cảnh báo một lần cho mỗi vai trò của thành viên có thể chạy Bash (quy tắc an toàn của nó chỉ làm hết sức có thể, không phải ranh giới; xem [nhóm](groups.md#roles)) | không cho vai trò đó Bash trong `policy.yaml`, hoặc chấp nhận điều đó |
| `peer_messages` | Tin nhắn giữa các phiên Claude: biên nhận của chúng được giữ ở đâu và thư mục đó có riêng tư không, hệ thống này có kiểm tra được thời điểm khởi động của người gửi không, và một cảnh báo cho mỗi vai trò mà một quy tắc cho dùng SendMessage (xem [bảo mật](security.md#messages-between-claude-sessions)) | xóa thư mục biên nhận dùng chung (aipager sẽ tạo lại), hoặc không cho vai trò đó SendMessage |
| `claude` | Tìm chương trình `claude` theo đúng cách mà mọi lần khởi động dùng (`claude_path` trong `aipager.yaml` hoặc `$AIPAGER_CLAUDE_BIN` thắng ngay; nếu không thì bản mới nhất trong `~/.local/bin`, PATH và Homebrew), và liệt kê mọi bản cài KHÁC tìm thấy | cài Claude Code, hoặc đặt `claude_path` / `AIPAGER_CLAUDE_BIN` |
| `claude_auth` | Thử `claude auth status` trong đúng môi trường mà một phiên thật nhận được. **Không bao giờ FAIL**: “chưa đăng nhập” và “chính lần thử bị lỗi” được báo riêng, và không cái nào ngăn một phiên khởi động | `claude auth login`, hoặc đặt khóa API / `CLAUDE_CODE_OAUTH_TOKEN` |
| `dtach` | Tìm thấy chương trình `dtach` (`dtach-bin` của chính aipager, hoặc một bản trên PATH) | `uv tool install --reinstall aipager` |
| `hook_scripts` | `aipager-hook` và `aipager-statusline` có trên PATH | `uv tool install --reinstall aipager` |
| `settings_json` | `~/.claude/settings.json` đã nối các hook của aipager | `aipager config` |
| `daemon` | aipager đang chạy và socket của nó trả lời; còn hiện trạng thái giới hạn tốc độ của Telegram (xem [ở trên](#the-bot-slowed-down-a-rate-limit-429)) | `aipager start` |
| `service_installed` | Không bắt buộc: file dịch vụ có tồn tại | `aipager service install` |
| `service_unit_path` | Chỉ Linux: `Environment=PATH=` của dịch vụ *đã cài* thực sự chứa thư mục của chương trình claude tìm được, đọc như văn bản, vì doctor không thấy được PATH riêng của systemd từ shell tương tác của người vận hành | `aipager service install --yes` |
| `miniapp` | Mini App có khởi động được không khi nó bật. **Không bao giờ FAIL**: Mini App là tùy chọn | cách sửa mà nó nêu |

`aipager doctor --json` in ra cùng các bước kiểm tra, theo thứ tự này, với các khóa này (xem [lệnh](commands.md#aipager-doctor---json)).

Chạy `aipager doctor --fix` để tìm và chép thông tin đăng nhập Claude vào `daemon.env` một cách tương tác (cả khi file có đó nhưng chưa chứa thông tin nào), hoặc để ghim `claude_path` khi tìm thấy nhiều hơn một bản cài. Nó chỉ hành động sau khi hỏi. Với một dịch vụ mà PATH thiếu thư mục của claude, chạy `aipager service install --yes`.

## <a id="the-daemon-cant-find-claude-or-picks-the-wrong-install"></a>aipager không tìm thấy `claude`, hoặc chọn sai bản cài

Trước đây có sáu chỗ tìm `claude` một cách độc lập và có thể cho kết quả khác nhau. Giờ tất cả đều đi qua một bộ tìm duy nhất (`aipager/claude_resolve.py`). `claude_path` trong `aipager.yaml`, rồi `$AIPAGER_CLAUDE_BIN`, thắng ngay nếu chúng dùng được. Nếu không, mọi `claude` tìm thấy trong `~/.local/bin`, trên `$PATH` và trong các thư mục của Homebrew đều được kiểm tra, và phiên bản mới nhất thắng (nên đổi thứ tự `$PATH` không đổi lựa chọn khi bản cài kia mới hơn). Chạy `aipager doctor` để xem chính xác nó đã chọn bản nào và còn tìm thấy gì khác; nếu bản sai thắng, hãy ghim bản đúng:

```sh
aipager doctor --fix        # chọn tương tác giữa các bản cài tìm thấy
# hoặc bằng tay:
echo 'claude_path: /home/you/.local/bin/claude' >> ~/.config/aipager/aipager.yaml
```

## <a id="sessions-cant-authenticate-under-the-systemd-service-but-claude-works-fine-in-a-terminal"></a>Các phiên không đăng nhập được dưới dịch vụ systemd, nhưng `claude` chạy tốt trong terminal

Các dịch vụ `systemctl --user` không bao giờ đọc `~/.bashrc` / `~/.profile`, nên một dòng `export CLAUDE_CODE_OAUTH_TOKEN=…` ở đó không bao giờ tới được aipager. Thay vào đó, dịch vụ dùng `LoadCredential=` của systemd, đọc `~/.config/aipager/daemon.env`: một file `KEY=VALUE` đơn giản, quyền 0600, được tạo tự động lần đầu bạn chạy `aipager service install` (chép sang từ một `config.env` kiểu cũ nếu file đó có token, nếu không thì tìm một lần trong shell đăng nhập của bạn, nếu không nữa thì để trống kèm cảnh báo). Nếu nó vẫn trống, `aipager doctor --fix` tìm lại ở cùng những chỗ đó khi bạn đồng ý và thêm dòng tìm được, giữ nguyên các dòng khác của file; nó không bao giờ in token ra. Hoặc tự thêm dòng đó:

```sh
cat ~/.config/aipager/daemon.env          # xem có gì trong đó (hoặc không có gì)
echo 'CLAUDE_CODE_OAUTH_TOKEN=sk-...' >> ~/.config/aipager/daemon.env
chmod 600 ~/.config/aipager/daemon.env
aipager service stop && aipager service start
```

Xem [Mô hình bảo mật: thông tin đăng nhập Claude](security.md#the-claude-credential-what-actually-protects-it) để biết file này bảo vệ được gì và không bảo vệ được gì.

## <a id="the-daemons-control-socket-moved"></a>Socket điều khiển của aipager đã đổi chỗ

`aipager doctor` / `aipager status` trước đây luôn tìm ở `/tmp/aipager.sock`. Dưới dịch vụ systemd, socket điều khiển giờ nằm ở `$XDG_RUNTIME_DIR/aipager.sock` (chỉ quay về `/tmp` khi không đặt `$XDG_RUNTIME_DIR`: container, WSL1, các bản phân phối tối giản). Chỉ socket này đổi chỗ; socket dtach của từng phiên (`/tmp/claude-dtach-*.sock`) không bị ảnh hưởng. Đặt `AIPAGER_SOCKET_PATH` nếu bạn cần một vị trí cụ thể.

## <a id="update-says-the-restart-would-kill-sessions-killmode"></a>`/update` báo rằng khởi động lại sẽ tắt các phiên (KillMode)

Các file dịch vụ được ghi trước phiên bản này không có dòng `KillMode=`, nên systemd dùng `control-group`: dừng hoặc khởi động lại `aipager.service` sẽ tắt mọi phiên Claude mà aipager đã khởi động. Vì vậy `/update` cài bản mới nhưng không khởi động lại aipager, và liệt kê những phiên mà một lần khởi động lại sẽ làm sập. Sửa một lần:

```
aipager service install
```

Lệnh này ghi lại file dịch vụ với `KillMode=process`, tải lại systemd và khởi động lại aipager, và lần khởi động lại đó đã chạy với cài đặt mới. Kiểm tra bằng `systemctl --user show -p KillMode aipager.service` (`KillMode=process`). Với cài đặt này, systemd ghi các dòng “left-over process” cho các phiên của bạn ở mỗi lần khởi động lại; điều đó là bình thường.

## <a id="aipager-update-cant-find-uv--pipx"></a>`aipager update` không tìm thấy uv / pipx

`aipager update` tìm trình cài đặt theo đường dẫn tuyệt đối trong `PATH` của bạn, cộng thêm `~/.local/bin`, `~/.cargo/bin`, các thư mục của Homebrew, `/usr/bin` và `/bin`. Nếu nó vẫn báo không tìm thấy `pipx`, trình cài đặt đã tạo ra bản aipager này không còn nữa hoặc nằm ở chỗ khác; hãy cài lại bằng công cụ bạn đang có (`pipx install --force aipager`, `uv tool install --reinstall aipager`).

## <a id="update-refused-editable--not-your-install"></a>Cập nhật bị từ chối: bản editable / không phải bản cài của bạn

`/update` và `aipager update` chỉ nâng cấp một bản cài thuộc về người dùng của bạn và ghi được, qua trình cài đặt đã tạo ra nó. Chúng từ chối, và nêu lý do, với:

- một bản sao mã nguồn ở chế độ editable (để phát triển): cập nhật nó bằng `git pull`;
- một bản cài qua Nix, Snap, Docker hoặc gói của hệ điều hành: dùng cách cập nhật riêng của hệ thống đó;
- một bản cài thuộc về người dùng khác (ví dụ một venv của root dưới `/opt`): cập nhật nó với tư cách người dùng đó;
- một bản cài từ một thư mục trên máy (`pipx install /path/to/aipager`): `không cập nhật được từ đây: it was installed from a local folder; update it from that folder`. Kéo bản mới vào thư mục đó rồi cài lại từ đó, hoặc chuyển sang PyPI bằng `pipx install --force aipager`;
- một bản cài mà không biết được trình cài đặt nào (`could not tell which installer owns this aipager`): cài lại bằng `uv tool install aipager` hoặc `pipx install aipager`.

## <a id="update-failed-unexpected-argument---refresh"></a>Cập nhật thất bại: unexpected argument '--refresh'

aipager 1.0.0 trở về trước chạy `uv tool upgrade aipager --refresh`, mà không bản uv nào chấp nhận, nên mọi lần cập nhật một bản cài uv đều dừng với `error: unexpected argument '--refresh' found` và không khởi động lại gì. Các phiên bản mới hơn chạy `uv tool upgrade aipager`. Để vượt qua một lần, hãy cập nhật từ một terminal trên máy đó (`aipager update` ở các phiên bản đó cũng gặp cùng vấn đề):

```
uv tool upgrade aipager
systemctl --user restart aipager.service
```

Khi không có dịch vụ (hoặc trên macOS), hãy khởi động lại aipager theo cách bạn đã khởi động nó.

## <a id="claude-code-update-installed-with-npm-into-a-folder-you-cant-write-to"></a>Cập nhật Claude Code: được cài bằng npm vào một thư mục bạn không ghi được

Một bản Claude Code cài bằng `sudo npm install -g` nằm trong thư mục mà chỉ root sửa được (như `/usr/lib/node_modules`), nên `claude update` chạy với người dùng của bạn sẽ thất bại với `global folder isn't writable`. `/update` và Mini App hiện bản mới hơn với `không cập nhật được từ đây: installed with npm into a folder your user can't write to` và không có nút nào cho nó. Hãy chạy `claude install` một lần, dưới đúng người dùng mà aipager đang chạy: nó chuyển sang trình cài đặt riêng của Claude Code trong thư mục home của bạn, thứ cập nhật được mà không cần sudo. Sau đó khởi động lại aipager (`systemctl --user restart aipager.service`, hoặc theo cách bạn đã khởi động nó): một aipager đang chạy giữ bản Claude Code nó tìm thấy lúc khởi động, và sau khi khởi động lại nó lấy bản mới nhất tìm được. Nếu `claude_path` trong `aipager.yaml` (`aipager doctor --fix` có thể đặt nó) hoặc `AIPAGER_CLAUDE_BIN` trỏ tới bản npm, hãy trỏ nó tới `~/.local/bin/claude` hoặc xóa nó trước, vì chúng luôn thắng.

## <a id="updated-but-the-version-didnt-change"></a>Đã cập nhật nhưng phiên bản không đổi

Một bản cài pipx tạo từ một URL git được nâng cấp từ nguồn git đó, không phải từ PyPI. Khi nguồn không có gì mới hơn, `/update` báo `✅ aipager đã ở bản A - bản cài pipx này được cập nhật từ nguồn git của nó, và ở đó không có bản mới hơn.`. Hãy push hoặc chọn bản mới ở đó, hoặc chuyển sang PyPI bằng `pipx install --force aipager`.

## <a id="the-daemon-didnt-come-back-after-an-update"></a>aipager không chạy lại sau khi cập nhật

Lần nâng cấp kiểm tra rằng bản mới import được trước khi khởi động lại gì, nhưng nếu aipager mới vẫn không khởi động được, systemd sẽ thử lại mỗi 5 giây. Xem nguyên nhân:

```
journalctl --user -u aipager.service -n 50
```

Cài lại bản trước đó bằng cùng trình cài đặt, rồi khởi động lại:

```
pipx install --force aipager==<previous>        # hoặc:
uv tool install --force aipager==<previous>     # hoặc:
<venv>/bin/python -m pip install aipager==<previous>
systemctl --user restart aipager.service
```

Một bản cài pipx từ đường dẫn trên máy: `pipx install --force /path/to/aipager` ở commit cũ. Nếu còn một thông báo cũ đang chờ, xóa nó bằng `rm -f ~/.local/share/aipager/update-restart.json` (dù sao nó cũng bị bỏ qua sau 24 giờ); `~/.local/share/aipager/update.lock` chỉ là một file khóa và xóa an toàn khi không có lần cập nhật nào đang chạy.

## <a id="an-update-was-interrupted-when-aipager-shut-down"></a>“Một lần cập nhật đã bị ngắt khi aipager tắt”

aipager đã dừng (hoặc được khởi động lại) trong lúc `/update` đang cài, và trình cài đặt cũng bị dừng theo, nên bản cài có thể mới được ghi một nửa. Nếu aipager hoặc Claude Code chạy không ổn, hãy sửa: với aipager, chạy lệnh cài lại trong tin nhắn (ví dụ `pipx install --force aipager`) rồi `systemctl --user restart aipager.service`; với Claude Code, chạy lại `claude update`.

## <a id="a-chat-is-in-the-wrong-language"></a>Một chat đang dùng sai ngôn ngữ

Ngôn ngữ của aipager đến từ lựa chọn của chat, không phải từ các tin nhắn trong đó. Mở `/language` trong chat đó và chọn ngôn ngữ bạn muốn: lựa chọn này thắng mọi thứ khác. Trong chat riêng, **Tự động** đi theo ngôn ngữ của ứng dụng Telegram của bạn (Cài đặt → Ngôn ngữ trong Telegram); một ngôn ngữ mà aipager không có sẽ hiện bằng tiếng Anh. Một chat riêng đã có từ trước khi có ngôn ngữ thì vẫn dùng tiếng Anh cho tới khi bạn chọn một ngôn ngữ. Một nhóm dùng tiếng Anh cho tới khi một quản trị viên chọn ngôn ngữ cho nó. Ngôn ngữ chỉ đổi chữ của chính aipager: Claude trả lời bằng ngôn ngữ bạn viết cho nó, bất kể aipager hiện gì. Xem [Ngôn ngữ](commands.md#languages).

## <a id="the-bot-description-is-not-the-one-i-wrote"></a>Mô tả của bot không phải mô tả tôi đã viết

aipager đặt mô tả và mô tả ngắn của bot bằng mỗi ngôn ngữ mà nó nói được, cho những người có ứng dụng Telegram dùng ngôn ngữ đó, và Telegram hiện chữ đó cho họ thay vì chữ bạn đã viết trong BotFather. aipager chỉ làm vậy khi bạn chưa có mô tả của riêng mình, nên một mô tả bạn viết trước khi cài bản này sẽ đến được với mọi người. Nếu bạn viết nó sau khi aipager đã đặt mô tả của nó, những người dùng các ngôn ngữ đó vẫn thấy mô tả của aipager. Để họ thấy mô tả của bạn, hãy xóa chữ của aipager cho từng ngôn ngữ qua Bot API (`setMyDescription` và `setMyShortDescription` với chữ rỗng và `language_code` đó); aipager không bao giờ đặt lại nó khi mô tả của bạn còn đó.

## <a id="aipager-says-languagesjson-could-not-be-used"></a>aipager báo không dùng được languages.json

aipager giữ những gì nó biết về ngôn ngữ (ai đã ở đây trước khi có ngôn ngữ, ngôn ngữ Telegram gần nhất nó thấy ở từng người, và lời đề nghị một lần cùng dòng chào đầu tiên đã được gửi chưa) trong `~/.local/state/aipager/languages.json`. Nếu file đó không đọc được, aipager chuyển nó sang `languages.json.bad-<time>` trong cùng thư mục, ghi một cảnh báo vào log (lặp lại ở mỗi lần khởi động cho tới khi bạn quyết định), và giữ mọi chat ở tiếng Anh, không có lời đề nghị đổi ngôn ngữ, cho tới khi bạn quyết định: đặt file cũ trở lại nếu bạn sửa được nó, hoặc xóa `languages.json` rồi khởi động lại, và aipager lại coi mọi người nó biết là người đã có từ trước (tiếng Anh cho tới khi họ chọn). Ngôn ngữ mà ai đó đã chọn không nằm trong file này và không bao giờ bị mất.

## <a id="right-to-left-text-looks-wrong"></a>Chữ viết từ phải sang trái trông bị sai

Trong tiếng Ba Tư và tiếng Ả Rập, aipager đặt các dấu chỉ hướng vô hình vào chữ của chính nó để mỗi dòng đọc từ phải sang trái, và giữ tên phiên, lệnh và mã từ trái sang phải. Nếu một dòng vẫn trông lộn xộn, thường là do ứng dụng Telegram vẽ chữ trộn hai hướng theo cách khác: hãy xem thử trên một ứng dụng khác (điện thoại so với Desktop hoặc Web) trước khi báo lỗi. Câu trả lời của chính Claude được hiện đúng như Claude viết, không thêm dấu nào.

## <a id="a-translated-keyboard-word-does-nothing-in-another-chat"></a>Một chữ đã dịch trên bàn phím không có tác dụng trong chat khác

Bàn phím trả lời của mỗi chat dùng ngôn ngữ của chat đó. Các chữ trên bàn phím chính (status, stop, new, kill và các chữ còn lại) được hiểu ở mọi chat, bằng mọi ngôn ngữ, và chữ tiếng Anh luôn dùng được. Chữ trên nút mẫu hay nút lệnh thì khác: gõ trong một chat đặt ngôn ngữ khác, nó là một tin nhắn cho Claude chứ không phải một nút bấm, nên chữ “Xóa cuộc trò chuyện” tiếng Việt không bao giờ chạy `/clear` trong một chat tiếng Ba Tư hay tiếng Anh. Hãy dùng chính nút đó, hoặc lệnh tiếng Anh (`/clear`). Nếu bàn phím trông cũ sau khi ngôn ngữ của chat đã đổi, gửi `/start` để nhận bàn phím hiện tại. Một chữ cũng là tên của một phiên của bạn sẽ nói chuyện với phiên đó. `/mode` luôn hiểu `ask`, `auto` và `full` là chế độ, và chỉ hiểu một chữ chế độ đã dịch là tên phiên khi có một phiên đang chạy mang đúng tên đó.

## <a id="still-stuck"></a>Vẫn chưa xong?

Gửi một báo cáo sự cố: chạm **Báo cáo sự cố** dưới `/help` trong chat riêng của bạn với bot, hoặc chạy `aipager report` trong terminal. Bạn thấy toàn bộ báo cáo trước khi có gì được gửi, và nó không chứa chat, tin nhắn, tên, đường dẫn hay token nào (xem [Báo cáo sự cố](problem-reports.md)). Sau khi gửi, bạn nhận được một mã tham chiếu như `ap1-7c41d09e2b3a`.

Sau đó, nếu muốn trao đổi, hãy mở một issue tại [github.com/dev-aly3n/aipager/issues](https://github.com/dev-aly3n/aipager/issues) và ghi mã tham chiếu đó. Xin đừng dán đầu ra của `aipager doctor` hay `aipager logs` vào issue: chúng chứa ID chat của bạn, đường dẫn trong thư mục home của bạn, tên phiên và phần đầu các tin nhắn của bạn. Nếu cần một dòng log, nhà phát triển sẽ xin đúng dòng đó, và bạn có thể xóa những gì riêng tư khỏi nó trước.

## <a id="see-also"></a>Xem thêm

- [Kiến trúc](architecture.md): mỗi phần làm gì.
- [Mô hình bảo mật](security.md): aipager chạm vào những gì và không chạm vào những gì.
