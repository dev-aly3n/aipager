# Cài đặt bằng agent

<!-- i18n-source: docs/install-with-an-agent.md @ fc5d6810 -->

> Trang này được dịch từ tiếng Anh; nếu hai bản khác nhau, bản tiếng Anh là bản đúng. ([English](../../install-with-an-agent.md))

Trang này dành cho một agent lập trình AI (Claude Code, hoặc bất kỳ agent nào dùng được shell) được nhờ cài aipager cho người dùng của nó và thiết lập luôn. Hãy làm theo từng bước. Mọi lệnh ở đây chạy mà không hỏi gì và không bao giờ cần một terminal tương tác.

aipager cho phép người dùng điều khiển các phiên Claude Code từ Telegram. Khi bạn làm xong, người dùng có một bot Telegram chỉ nói chuyện với họ, và aipager chạy nền trên máy tính này.

## <a id="1-what-only-the-person-can-do"></a>1. Những việc chỉ người dùng tự làm được

Hãy nhờ người dùng làm những việc này. Bạn không thể làm thay họ.

1. **Tạo bot.** Trong Telegram, mở [@BotFather](https://t.me/BotFather), gửi `/newbot`, chọn tên và username. BotFather trả lời bằng token của bot (trông giống `123456789:AA...`).
2. **Chạm Start trong bot.** Mở bot mới (BotFather gửi link `t.me/<bot username>`) và chạm Start. Bot không thể nhắn cho người chưa làm việc này.
3. **Đăng nhập Claude Code trên máy tính này.** Nếu họ đã dùng Claude Code ở đây thì không cần làm gì. Trên một máy chủ không có màn hình, họ chạy `claude` một lần trong terminal của chính họ và đăng nhập (nó hiện một link để mở trên thiết bị khác). Nếu họ muốn dùng token dài hạn, họ chạy `claude setup-token`, lệnh này chỉ in ra token: chính họ lưu token đó cho các phiên của aipager, trong terminal của họ (dán token, nhấn Enter, rồi Ctrl-D):

   ```sh
   mkdir -p ~/.config/aipager
   (umask 077 && { printf 'CLAUDE_CODE_OAUTH_TOKEN='; cat; } >> ~/.config/aipager/daemon.env)
   ```

   aipager đưa token đó cho mọi phiên mà nó bắt đầu (xem [bảo mật](security.md#the-claude-credential-what-actually-protects-it)). Bạn không bao giờ đọc nó.

## <a id="2-how-the-person-gives-you-the-token"></a>2. Người dùng đưa token cho bạn như thế nào

Ai có token của bot thì người đó điều khiển được bot. Hãy đưa ra cho người dùng hai cách này và để họ chọn.

- **An toàn nhất: một file bạn không bao giờ đọc.** Trong terminal của chính họ, họ chạy:

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token)
  chmod 600 ~/.config/aipager/bot-token
  ```

  rồi dán token, nhấn Enter và nhấn Ctrl-D. Chỉ họ đọc được file này (quyền 600). Họ có thể xóa file khi thiết lập đã thành công (aipager giữ một bản riêng trong cấu hình của nó). Họ cho bạn biết đường dẫn (`~/.config/aipager/bot-token`). Token không bao giờ đi vào cuộc trò chuyện của bạn, và bạn đưa đường dẫn đó cho `--token-file`.
- **Nhanh nhất: dán token vào cuộc trò chuyện.** Cách này dùng được, nhưng hãy nói rõ ý nghĩa của nó: token sẽ nằm lại trong lịch sử trò chuyện của bạn và được gửi tới nhà cung cấp mô hình của bạn. Nếu lịch sử đó có thể được chia sẻ, người dùng nên gửi `/revoke` cho @BotFather sau đó và đặt token mới bằng `aipager setup --force` (bước 3.2, có `--force`). Nếu họ chọn cách này, hãy lưu token vào cùng file đó mà không in nó ra: tốt nhất là bằng công cụ ghi file của chính bạn, rồi `chmod 600 ~/.config/aipager/bot-token`. Nếu bạn chỉ có shell, hãy đưa token qua stdin bằng một here-document, để nó không phải là đối số của lệnh ghi file:

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token) <<'EOF'
  <the token>
  EOF
  chmod 600 ~/.config/aipager/bot-token
  ```

  Sau đó dùng `--token-file` như bên dưới.

ID người dùng Telegram của họ (ID chat của họ, một con số như `123456789`) không phải bí mật. Nếu họ không biết, bạn có thể tìm ra (bước 3.3).

## <a id="3-your-steps"></a>3. Các bước của bạn

### <a id="31-install-aipager"></a>3.1 Cài aipager

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

Nó dùng uv hoặc pipx, cái nào có sẵn thì dùng (trên macOS thì cả Homebrew), và cài uv trước nếu không có cái nào. Bạn cũng có thể tự chạy `uv tool install aipager` hoặc `pipx install aipager`. Nếu sau đó không tìm thấy `aipager`, hãy thêm `~/.local/bin` vào `PATH` của shell (`export PATH="$HOME/.local/bin:$PATH"`).

Nếu aipager đã được cài từ trước, các lệnh này không nâng cấp nó. Kiểm tra rằng `aipager setup --help` chạy được; nếu không, bản aipager đã cài quá cũ: chạy `aipager update` rồi kiểm tra lại. Nếu `aipager update` bảo cần khởi động lại aipager, hãy báo cho người dùng; đừng tự khởi động lại (xem 4).

Đừng chạy `aipager config`: đó là trình thiết lập tương tác dành cho con người.

### <a id="32-set-aipager-up"></a>3.2 Thiết lập aipager

```sh
aipager setup --token-file ~/.config/aipager/bot-token --chat-id 123456789 --service --json
```

Dùng ID thật của người dùng và đường dẫn của file token. Thay cho `--token-file`, bạn có thể đưa token qua pipe với `--token-stdin` (ví dụ `--token-stdin < path`); pipe phải được đóng, và setup chờ nó tối đa 30 giây.

Setup kiểm tra token với Telegram, kiểm tra máy tính này, gửi cho người dùng một tin nhắn thử (“aipager is set up for you. ...”), và chỉ sau đó mới ghi cấu hình, thêm các hook của Claude Code vào `~/.claude/settings.json` (hoặc `$CLAUDE_CONFIG_DIR/settings.json`), và, với `--service`, cài và khởi động dịch vụ chạy nền. Nếu có bước kiểm tra nào thất bại, không có gì được ghi.

Thêm `--dry-run` để xem trước những gì sẽ thay đổi. `--role admin` cho người dùng vai trò quản trị viên thay vì chủ sở hữu (mặc định là chủ sở hữu, đúng cho người tự thiết lập máy tính của mình).

### <a id="33-if-the-person-does-not-know-their-telegram-id"></a>3.3 Nếu người dùng không biết ID Telegram của họ

```sh
aipager setup detect-chat --token-file ~/.config/aipager/bot-token --json
```

Nhờ người dùng chạm Start trong bot (hoặc gửi cho bot một tin nhắn bất kỳ) trong lúc lệnh này chờ (tối đa 5 phút; `--timeout SECONDS` đổi thời gian này). Nó in người gửi mới nhất trong `candidate` (`id`, `first_name`, `last_name`, `username`) và không lưu gì. Cho người dùng xem tên và username rồi hỏi “Đây có phải là bạn không?”. Chỉ khi họ nói có, mới chạy bước 3.2 với `--chat-id` bằng `candidate.id`. Nếu `other_candidates` khác 0, đã có người khác cũng nhắn cho bot: hãy kiểm tra thật kỹ.

### <a id="34-read-the-result"></a>3.4 Đọc kết quả

Với `--json`, setup in đúng một đối tượng JSON ra stdout. Đọc `exit_code` (giống mã thoát của tiến trình), `status`, `error`, `message`, `fix` và `next_step`. Nếu stdout không phải JSON (một cờ lạ, một từ sai sau `setup`, hoặc một bản aipager quá cũ chưa có `setup`), mã thoát là 2 và lý do được ghi ra stderr dưới dạng văn bản thuần: hãy đọc nó, và với aipager cũ thì xem 3.1.

| Mã thoát | `error` | Bạn làm gì |
|---|---|---|
| 0 | | `installed`, `updated` hoặc `unchanged`: xong, chuyển sang 3.5. `dry_run`: chưa có gì được ghi; chạy lại đúng lệnh đó mà không có `--dry-run`. detect-chat `found`: xác nhận người dùng như ở 3.3, rồi chạy 3.2. |
| 1 | `config_malformed`, `ambiguous_install`, `settings_invalid`, `test_send_failed`, `write_failed`, `service_failed`, `internal_error` | Cho người dùng xem `message` và `fix`. `ambiguous_install`: người dùng tự sửa trong `aipager config`. Đừng sửa file bằng tay. `settings_invalid` yêu cầu một `CLAUDE_CONFIG_DIR` tuyệt đối: `fix` cho biết tệp cần đổi (`daemon.env` hoặc một tệp cài đặt của Claude Code); hãy hỏi người đó thư mục, chỉ đổi đúng giá trị đó và chỉ khi họ đồng ý, rồi chạy lại setup. |
| 2 | `usage`, `token_malformed`, `token_stdin_timeout`, `bad_chat_id`, ... | Dữ liệu bạn đưa vào bị sai; `fix` nêu tên cờ cần sửa. `token_malformed`: file không chứa token bot, hãy nhờ người dùng kiểm tra. |
| 3 | `deps_missing` | Làm theo `fix` (mỗi mục còn thiếu cũng có `fix` riêng trong `deps.items`), rồi chạy lại setup. |
| 4 | `token_rejected` | Telegram từ chối token. Xin người dùng token lại (hoặc một token mới từ @BotFather). |
| 5 | `chat_not_started`, `bot_blocked` | Nhờ người dùng mở bot, chạm Start (hoặc bỏ chặn bot), rồi chạy lại setup. |
| 6 | `existing_install` | aipager đã được thiết lập với token, chat hoặc vai trò khác. Cho người dùng biết điểm khác nhau và hỏi họ trước khi thêm `--force`. |
| 7 | `telegram_unreachable` | Mạng có trục trặc hoặc Telegram đang bận. Chờ một phút rồi thử lại. |
| 8 | `detect_timeout` | detect-chat không thấy tin nhắn nào. Nhờ người dùng chạm Start lần nữa rồi chạy lại. Nếu có cảnh báo `updates_backlog` thì chạm Start cũng không giúp được: hãy hỏi người dùng ID dạng số của họ. |
| 9 | `daemon_running` | detect-chat: một aipager cho bot này đang chạy và nhận tin nhắn của nó. Hãy hỏi người dùng ID dạng số của họ (hoặc hỏi họ trước khi bạn dừng nó bằng `aipager service stop`). |
| 10 | `updates_conflict` | detect-chat: một chương trình khác đang đọc tin nhắn của bot này. Hãy hỏi người dùng ID dạng số của họ. |
| 130 | `interrupted` | Bị dừng bằng Ctrl-C. |

Nếu `daemon.restart_needed` là true, aipager cần khởi động lại để dùng thay đổi này, hoặc setup không biết được aipager có đang chạy hay không (cảnh báo `daemon_unknown`). Làm theo `next_step`, nó nói rõ là trường hợp nào (nếu nó đề nghị `aipager start`, lệnh chạy ở tiền cảnh, thì hãy chọn `aipager service install`). Báo cho người dùng, và chỉ khởi động lại (`aipager service stop`, rồi `aipager service start`) khi họ đồng ý, và không bao giờ làm từ bên trong một phiên mà người dùng đang điều khiển qua aipager (xem 4). Danh sách đầy đủ các lỗi, cảnh báo và khóa JSON nằm ở [tài liệu về lệnh setup](commands.md#command-line-aipager-setup-for-coding-agents).

### <a id="35-check-the-install"></a>3.5 Kiểm tra bản cài

```sh
aipager doctor --json
```

Lệnh này chỉ đọc và không gửi gì. Mã thoát 0 nghĩa là không có bước kiểm tra nào thất bại. Với mỗi bước kiểm tra có `status` là `fail` (hoặc `warn`), hãy cho người dùng xem `title`, `detail` và `fix` của nó.

### <a id="36-report-back-to-the-person"></a>3.6 Báo lại cho người dùng

Nói với họ trong vài dòng:

- bot (`@` + `bot_username`), và tin nhắn thử đã được gửi chưa (`test_message`);
- những gì đã được thiết lập (`changed`, và `service.result`);
- cách bắt đầu một phiên Claude: gửi `/new` cho bot trong Telegram, hoặc chạy `aipager session <name>` trong terminal;
- bất cứ điều gì `doctor` đã đánh dấu, và `next_step` nếu nó yêu cầu điều gì.

## <a id="4-what-you-must-not-do"></a>4. Những điều bạn không được làm

- Đừng in token ra, đừng `echo` hay `cat` nó, và không bao giờ đặt nó trên dòng lệnh (dù sao `--token` cũng bị từ chối). Chỉ đưa nó qua file hoặc stdin.
- Đừng sửa `~/.config/aipager/` (ngoài file token ở bước 2) hay `~/.claude/settings.json` bằng tay. `aipager setup` và `aipager config` sẽ ghi chúng.
- Đừng thêm `--force` mà không hỏi người dùng: nó thay token, chat hoặc vai trò hiện có của họ.
- Đừng khởi động lại hay tắt phiên Claude Code của chính bạn. Ở lần cài đầu tiên, phiên bạn đang chạy không nằm dưới aipager, và cũng không cần như vậy: phiên mới đến từ `/new` trong Telegram hoặc `aipager session <name>`. Nếu aipager đã được thiết lập và người dùng đang nói chuyện với bạn qua nó (ví dụ để chạy `setup --force` với token mới), đừng tự dừng hay khởi động lại aipager: việc của bạn và câu trả lời của nó sẽ bị cắt ngang. Hãy nhờ người dùng khởi động lại khi bạn đã xong.

## <a id="5-groups"></a>5. Nhóm

`aipager setup` thiết lập chat riêng của người dùng với bot. Để thêm một nhóm Telegram có nhiều người hơn cùng vai trò của họ, người dùng chạy `aipager config` (việc thiết lập nhóm chưa làm bằng script được). Xem [Chế độ làm việc nhóm và nhóm](groups.md).

## <a id="see-also"></a>Xem thêm

- [Lệnh của bot và lệnh setup](commands.md)
- [Xử lý sự cố](troubleshooting.md)
- [Mô hình bảo mật](security.md)
