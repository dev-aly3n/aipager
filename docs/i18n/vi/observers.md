# Bot quan sát

<!-- i18n-source: docs/observers.md @ 6fc084f -->

> Trang này được dịch từ tiếng Anh; nếu hai bản khác nhau, bản tiếng Anh là bản đúng. ([English](../../observers.md))

Các bot Telegram chỉ đọc, nhận bản sao thông báo của bot chính. Chúng nhận bản tóm tắt, cảnh báo và lỗi, nhưng không điều khiển được phiên nào.

## <a id="setup"></a>Thiết lập

1. Tạo một bot qua [@BotFather](https://t.me/BotFather)
2. Mở chat với bot đó và gửi `/start`
3. Tìm ID chat của bạn (gửi một tin nhắn, rồi mở `https://api.telegram.org/bot<TOKEN>/getUpdates`)
4. Đặt `OBSERVER_BOTS` trong môi trường của aipager. Với aipager chạy ở tiền cảnh, export biến này trước `aipager start`; với dịch vụ systemd:

```sh
systemctl --user edit aipager    # thêm vào dưới [Service]:
# Environment=OBSERVER_BOTS=<bot_token>:<chat_id>
```

Nhiều bot quan sát (cách nhau bằng dấu phẩy):

```
OBSERVER_BOTS=111:AAA_first:12345,222:BBB_second:67890
```

Định dạng là `token:chat_id`: khi đọc, dấu hai chấm **cuối cùng** được dùng làm dấu phân cách (token của bot đã có sẵn một dấu hai chấm bên trong).

5. Khởi động lại aipager

## <a id="what-observers-receive"></a>Bot quan sát nhận được gì

Bot quan sát nhận các thông báo này từ **mọi chat mà bot phục vụ**, mọi chat riêng và mọi nhóm, không có bộ lọc nào: chỉ thiết lập cho một chat mà bạn tin tưởng được với tất cả những thứ đó. Mỗi thông báo đến bằng ngôn ngữ của chat mà nó xuất phát.

| Sự kiện | Ví dụ |
|-------|---------|
| Một việc đã xong | `💬 x1 · Xong (2:05, +12 -3)`: chỉ có dòng kết quả, không có câu trả lời; kèm log đầy đủ dưới dạng file `.md` khi câu trả lời hoặc thẻ bị cắt bớt |
| Một công việc chạy nền đã xong | `✅ x1 · Xong (...)`, hoặc `⚠️ x1 · Xong (...)` khi một agent bị mất hoặc không thấy một lệnh chạy nền kết thúc |
| Lỗi API | `⚠️ x1 · Máy chủ của Anthropic đang quá tải. Thử lại sau giây lát.` (không có nút Thử lại) |
| Cảnh báo ngữ cảnh | `⚠️ x1 · Ngữ cảnh đã đầy 82% - sắp tự động thu gọn` |
| Đang thu gọn | `🔄 x1 · Đang thu gọn cuộc trò chuyện` |
| Đã thu gọn xong | `📦 x1 · Đã thu gọn cuộc trò chuyện: 82% → 4%` |
| Một yêu cầu vẫn đang chờ | `⬆️ x1 · vẫn đang chờ bạn trả lời ở trên`, kèm yêu cầu cấp quyền hoặc câu hỏi đang chờ |
| Một phiên đang làm việc nhưng im lặng | `⏳ x1 · vẫn đang làm việc (10 phút chưa có tin)` |
| Một tin nhắn mà Claude Code không nhận | `⚠️ x1 · Claude Code chưa nhận tin nhắn` |
| Phiên đã kết thúc | `🔴 x1 · Phiên đã đóng` |

## <a id="what-observers-dont-receive"></a>Những gì bot quan sát KHÔNG nhận

- Hiệu ứng chuyển động của thẻ làm việc
- Cập nhật về các lần gọi công cụ
- Yêu cầu cấp quyền và các nút của chúng (chỉ có lời nhắc “vẫn đang chờ” ở trên cho biết yêu cầu đó hỏi gì)
- Hộp thoại AskUserQuestion
- Bất kỳ bàn phím hay nút bấm nào

Bot quan sát hoàn toàn không giữ trạng thái: tin nhắn được gửi đi mà không chờ phản hồi. Một bot quan sát bị lỗi không bao giờ ảnh hưởng tới bot chính.
