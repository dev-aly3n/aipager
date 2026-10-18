# Báo cáo sự cố: một báo cáo chứa gì

<!-- i18n-source: docs/problem-reports.md @ 16ee949 -->

> Trang này được dịch từ tiếng Anh; nếu hai bản khác nhau, bản tiếng Anh là bản đúng. ([English](../../problem-reports.md))

Khi aipager gặp lỗi, bạn có thể gửi cho nhà phát triển aipager một báo cáo sự cố chỉ với một lần chạm. Bạn thấy toàn bộ báo cáo trước khi có gì được gửi, không có gì rời khỏi máy tính của bạn cho tới khi bạn đồng ý, và một báo cáo không bao giờ chứa chat, tin nhắn, tên, đường dẫn hay token của bạn. Nó chỉ được tạo từ một danh sách trường cố định: phiên bản, số đếm, ngày tháng, câu trả lời có/không, lựa chọn từ các danh sách cố định, vị trí trong mã của chính aipager và tên `ap1-` của các lỗi, cộng thêm một ghi chú ngắn chỉ khi bạn tự viết.

## <a id="how-to-send-one"></a>Cách gửi

Chỉ chủ sở hữu bản cài aipager mới gửi được báo cáo từ Telegram: người ở trong chat riêng của chính bản cài đó với bot (người vận hành; nếu có nhiều chat như vậy và không có một chủ sở hữu duy nhất thì không có nút “Báo cáo sự cố” và không có đề nghị tự động, còn `aipager report` trong terminal vẫn dùng được). Mini App có trang báo cáo riêng; mọi cách khác từ Telegram đều mở cùng một thẻ xem trước trong chat riêng của bạn với bot:

- **`/help`**: khi chủ sở hữu gửi lệnh này, phần trợ giúp đi kèm nút “🐞 Báo cáo sự cố”. Trong một nhóm, chỉ lần chạm của chủ sở hữu mới có tác dụng, và bản xem trước mở trong chat riêng của chủ sở hữu, không bao giờ trong nhóm.
- **Mini App**: tab Cài đặt, “Báo cáo sự cố” (khi ứng dụng đang mở trên chat riêng của bạn) mở một trang ngay trong ứng dụng. Ghi chú của bạn nằm ở trên cùng (không bắt buộc, tối đa 500 ký tự, có bộ đếm). Bên dưới là phần tóm tắt ngắn của báo cáo: phiên bản, thiết lập, các lỗi (chạm vào một lỗi để xem vị trí của nó trong mã) và **Thêm chi tiết**. **Hiện đúng nội dung báo cáo** cho thấy chính xác văn bản sẽ được gửi, kể cả ghi chú của bạn, và nó thay đổi theo những gì bạn gõ. **Gửi báo cáo** (nút của Telegram ở dưới cùng) gửi đúng nội dung đó, một lần, rồi trang hiện mã tham chiếu kèm nút **Sao chép**. Quay lại thì không gửi gì. Nếu aipager chỉnh lại ghi chú của bạn (khoảng trắng ở hai đầu, ký tự ẩn, phần vượt quá 500 ký tự), trang sẽ hiện ghi chú đã chỉnh và không có gì được gửi cho tới khi bạn chạm “Gửi báo cáo” lần nữa.
- **Thông báo giới hạn bộ nhớ**: khi hook của aipager hết bộ nhớ, thông báo trong chat có nút “🐞 Báo cáo sự cố này”.
- **Một đề nghị tự động**: thỉnh thoảng, aipager đề nghị gửi báo cáo về một lỗi mà nó nhận thấy (xem [Khi nào aipager đề nghị gửi báo cáo](#when-aipager-offers-a-report)).
- **Trong terminal**: `aipager report` in ra một báo cáo và hỏi `Send this report? [y/N]`. Nó chứa cùng các loại trường, nhưng ít thông tin hơn về aipager đang chạy (chế độ, các chat, tính năng, thời gian chạy, các phiên và chi tiết Claude Code được để trống hoặc ghi là không biết). `aipager report --note "what you were doing"` thêm một ghi chú. Khi không có terminal (một script, một pipe, shell của một phiên Claude Code) nó chỉ in báo cáo ra và không bao giờ gửi.

Thẻ xem trước hiện báo cáo đúng như khi nó sẽ được gửi: JSON trong một khối thu gọn mà bạn có thể mở ra, hoặc, khi quá dài cho một tin nhắn, dưới dạng file `report.json` trả lời vào thẻ. Các nút của thẻ:

- **📤 Gửi**: gửi đúng báo cáo đó, một lần. Sau đó thẻ hiện `Đã gửi. Mã tham chiếu ap1-...`.
- **✏️ Thêm ghi chú**: tin nhắn tiếp theo của bạn trong chat đó trong vòng 10 phút sẽ trở thành ghi chú của báo cáo (cắt ở 500 ký tự), và thẻ hiện lại báo cáo có ghi chú trong đó trước khi bạn gửi. Nếu bạn làm việc khác (một lệnh, một nút khác, một bức ảnh, một thao tác trong Mini App), câu hỏi về ghi chú sẽ đóng lại mà không thêm ghi chú nào, và sau 10 phút tin nhắn tiếp theo của bạn đi tới Claude như thường lệ. Câu hỏi có nút **‹ Quay lại**, trở về bản xem trước không có ghi chú, và nút Hủy.
- **✖️ Hủy**: không có gì được gửi.

Một bản xem trước còn mở trong 24 giờ, và 5 bản mới nhất được giữ mở (mở bản thứ 6 sẽ đóng bản cũ nhất). Sau khi aipager khởi động lại, một thẻ cũ không gửi được nữa: hãy mở lại “Báo cáo sự cố” để có bản mới. Một báo cáo mở trong Mini App có thể được gửi trong 24 giờ (3 bản mới nhất); sau khi khởi động lại, trang sẽ tải một bản mới.

## <a id="what-a-report-holds"></a>Một báo cáo chứa gì

Từng phần, bằng lời dễ hiểu. Mọi giá trị đều được so với danh sách giá trị được phép trước khi bản xem trước hiện ra và một lần nữa trước khi gửi; giá trị nào không khớp sẽ bị thay bằng `<invalid>` hoặc bỏ đi, không bao giờ được gửi nguyên như cũ.

**Về báo cáo**
- `schema`: định dạng báo cáo (`aipager-report/1`).
- `trigger`: `manual` (bạn tự mở) hoặc `auto` (một đề nghị tự động).
- `day`: ngày, theo UTC (không có giờ).
- `note`: chỉ khi bạn thêm vào: lời của bạn, đúng như bản xem trước đã hiện (tối đa 500 ký tự, không có ký tự ẩn).

**aipager và máy tính**
- `aipager`: phiên bản; cách nó được cài (`pipx`, `uv`, `brew`, `pip`, `editable`, `nix`, `snap`, `docker`, `system`, `foreign` hoặc `unknown`); từ đâu (kho gói, một thư mục trên máy, một bản sao git); `aipager update` có nâng cấp được nó không.
- `python`: phiên bản Python và đó là CPython hay PyPy.
- `os`: hệ điều hành (`linux`, `darwin`, `windows`, `freebsd`), loại bộ xử lý, bản phân phối Linux từ một danh sách cố định cùng số phiên bản của nó, phiên bản major.minor của kernel, loại container nếu có (`docker`, `lxc`, `wsl`, `podman`), và aipager có được thiết lập như một dịch vụ systemd hay launchd không.
- `deps`: phiên bản các thư viện mà aipager dùng (python-telegram-bot, httpx, aiohttp, PyYAML, rich, questionary, dtach-bin).
- `claude_code`: phiên bản Claude Code, tìm thấy bao nhiêu bản cài Claude Code, và nó đăng nhập bằng cách nào (`env`, `file`, `keychain`, hoặc lý do không biết được). Không bao giờ có chính thông tin đăng nhập.

**Thiết lập của bạn, dưới dạng số đếm**
- `config`: chế độ (`personal`, `team`, `scope`); có bao nhiêu chat riêng và bao nhiêu nhóm được cấu hình; có bao nhiêu vai trò tự tạo (không kèm tên của chúng); tính năng nào đang bật (Mini App, loại tunnel của nó, tin nhắn thoại, xem trước thay đổi, tóm tắt có định dạng).
- `runtime`: aipager đã chạy được bao lâu, theo khoảng (`<10m`, `10m-1h`, `1-24h`, `1-7d`, `>7d`); bao nhiêu phiên đang chạy và đang bận; aipager đã dừng bất ngờ bao nhiêu lần trong 7 ngày qua và lần gần nhất nó kết thúc ra sao (`clean`, `crash`, `reboot`).
- `doctor`: kết quả (`ok`, `warn`, `fail`) của một danh sách kiểm tra sức khỏe cố định, khi có sẵn mà không cần chạy lại các bước kiểm tra (báo cáo từ bot và từ terminal để trống phần này).
- `flood`: trạng thái giới hạn tốc độ của Telegram cộng dồn trên mọi chat: số lần bị chặn trong 7 ngày qua, ngay lúc này có chat nào đang bị tắt gửi, đang ở chế độ tối thiểu hay đang gửi chậm lại không, và phần ngân sách mỗi giờ mà chat bận nhất đã dùng, theo khoảng. Không có ID chat.

**Điều gì đã sai**
- `counters_24h`: mỗi việc trong một danh sách cố định đã xảy ra bao nhiêu lần trong 24 giờ qua (ví dụ bộ canh chừng thẻ làm việc khởi động lại, một loại lỗi Telegram, hook chạm giới hạn bộ nhớ, một lỗi mạng hay ổ đĩa). Chỉ có số đếm.
- `log_digest_24h`: với 15 vị trí trong mã của aipager ghi cảnh báo hoặc lỗi vào log nhiều nhất trong 24 giờ qua, vị trí đó (`aipager/state.py:1234`), mức độ và số lần. Không bao giờ có nội dung log.
- `errors`: tối đa 10 lỗi mà aipager đã ghi nhận, mỗi lỗi gồm:
  - `fingerprint`: một tên `ap1-` cho lỗi (mã băm của loại lỗi và tên của tối đa 8 vị trí trong mã của aipager, không bao giờ của văn bản hay dữ liệu nào);
  - lỗi xảy ra ở đâu (aipager, hook, dòng trạng thái, một lệnh, Mini App), nó bị bắt ra sao, và đó là lỗi thật hay chỉ là điều bất thường (`tier`);
  - với một lỗi do aipager ghi vào log, tên của logger (một module `aipager`, `asyncio` hoặc một phần của `telegram.ext`);
  - tên loại ngoại lệ (ví dụ `builtins.KeyError`, hoặc `<other>` cho ngoại lệ đến từ ngoài một danh sách gói cố định), loại của tối đa ba nguyên nhân, và tên mã lỗi như `ENOSPC`;
  - với lỗi của hook, sự kiện hook và công cụ (công cụ có sẵn thì theo tên, mọi công cụ MCP thì ghi là `mcp`);
  - bao nhiêu lần, vào những ngày nào và trên những phiên bản aipager nào lỗi đã xảy ra;
  - tối đa 12 vị trí trong mã của chính aipager mà nó đi qua (file, dòng, tên hàm), và tối đa 8 tên gói bên ngoài mà nó đi qua, từ một danh sách cố định.

**Không bao giờ có**: chat, tin nhắn, câu trả lời của Claude, tên phiên, đường dẫn thư mục hay file, token hay khóa, ID chat hay ID người dùng, username, tên máy tính, nội dung của bất kỳ thông báo lỗi nào, và các dòng log. Các trường ở trên không có chỗ cho chúng, và báo cáo được so với danh sách đó trước khi hiện ra và trước khi gửi.

## <a id="where-it-goes"></a>Báo cáo được gửi đi đâu

Báo cáo được gửi tới hộp thư lỗi riêng của nhà phát triển aipager trên [Sentry](https://sentry.io) mà không kèm tên bạn: không tài khoản, không ID bản cài. Như mọi yêu cầu web khác, nó đi từ địa chỉ internet của máy tính bạn, địa chỉ mà GitHub và Sentry nhìn thấy được; hộp thư được đặt để không lưu địa chỉ đó (tùy chọn “Prevent Storing of IP Addresses” của Sentry). aipager tự gửi báo cáo bằng một yêu cầu HTTPS (nó không dùng thư viện riêng của Sentry, thứ thu thập nhiều hơn rất nhiều): báo cáo bạn đã xem được gửi thành file đính kèm `report.json`, cạnh một bản tóm tắt ngắn dùng để gom nhóm (phiên bản, cách cài và đó có phải bản cài để phát triển không, hệ điều hành, phiên bản Python và Claude Code, cách kích hoạt, và fingerprint, loại cùng vị trí trong mã của lỗi đầu tiên). Yêu cầu chỉ ghi `aipager/<version>`.

Sau khi gửi, thẻ (hoặc terminal) hiện một **mã tham chiếu**: fingerprint `ap1-` của lỗi đầu tiên, hoặc, với một báo cáo không có lỗi, ID sự kiện dài 32 ký tự của nó. Nếu bạn cũng mở một [issue trên GitHub](https://github.com/dev-aly3n/aipager/issues), hãy ghi mã tham chiếu để nhà phát triển tìm được báo cáo của bạn. Đừng dán log hay đầu ra của `aipager doctor` vào issue: chúng chứa ID chat của bạn, đường dẫn trong thư mục home của bạn và phần đầu các tin nhắn của bạn.

Ngay trước khi gửi, aipager đọc [report-endpoint.json](https://github.com/dev-aly3n/aipager/blob/main/report-endpoint.json) từ repo aipager trên GitHub để biết phải gửi tới đâu (một aipager đang chạy giữ một bản tốt trong một ngày; một lần đọc thất bại sẽ được thử lại ở lần gửi sau, và mỗi lần `aipager report` đều đọc mới). Nhờ đó nhà phát triển có thể đổi khóa Sentry, tắt báo cáo cho mọi người, hoặc chặn các phiên bản thấp hơn một số nhất định gửi báo cáo (một bản build không biết phiên bản của chính nó được tính là thấp hơn), mà không cần ra bản mới. Nếu không kết nối được tới GitHub, hoặc file không thuộc dạng aipager hiểu được, aipager dùng khóa có sẵn trong phiên bản này; một file đọc được mà ghi là tắt thì luôn được tuân theo.

## <a id="limits"></a>Giới hạn

- Tối đa 5 lần thử gửi mỗi ngày (UTC) từ một máy tính, tính chung cả gửi tay và tự động, và một lần thử thất bại cũng được tính. Lần tiếp theo sẽ báo `Mỗi máy tính chỉ gửi được tối đa 5 báo cáo mỗi ngày. Hãy thử lại vào ngày mai.`
- Không có gì được thử lại ngầm: nếu một lần gửi thất bại, thẻ báo `Chưa gửi được lúc này. Không có gì bị mất; hãy thử lại sau.` và vẫn giữ nút Gửi.

## <a id="when-aipager-offers-a-report"></a>Khi nào aipager đề nghị gửi báo cáo

aipager tự nhận ra lỗi của chính nó và, thỉnh thoảng, đề nghị gửi báo cáo về chúng trong chat riêng của bạn: `aipager gặp lỗi nội bộ N lần (TypeError ở thẻ làm việc). Báo cáo giúp lỗi này được sửa sớm hơn. ...` kèm **Xem báo cáo**, **Để sau** và **Đừng hỏi lại**. “Xem báo cáo” mở thẻ xem trước chỉ với những lỗi đó; không có gì được gửi cho tới khi bạn chạm Gửi ở đó.

Một đề nghị chỉ đến khi tất cả những điều sau đều đúng:
- Đó là một lỗi thật của aipager: một lỗi trong mã của chính aipager đã xảy ra ít nhất hai lần, cách nhau ít nhất 10 phút, hoặc ngay lập tức khi aipager bị crash trong lúc chạy như một dịch vụ (một `aipager start` ở tiền cảnh bị tắt, ví dụ do đóng terminal của nó, thì chỉ được đếm). Những lỗi mà hook, chương trình dòng trạng thái hoặc một lệnh báo cho aipager (kể cả việc hook hết bộ nhớ) còn phải xảy ra rải rác trong ít nhất một ngày, nên một lỗi đơn lẻ không bao giờ gây ra đề nghị; với những lỗi đó đã có nút “Báo cáo sự cố này” trên thông báo. Những thứ không phải lỗi của aipager (giới hạn của Telegram, mạng, ổ đĩa đầy) không bao giờ gây ra đề nghị; chúng chỉ được đếm cho báo cáo mà bạn tự gửi.
- Tối đa một đề nghị mỗi 3 ngày, với mọi thứ đang chờ gộp trong đề nghị đó.
- Không trong 48 giờ đầu sau khi aipager khởi động lần đầu trên máy tính này (cập nhật không làm mốc này bắt đầu lại), và không trong một giờ đầu sau khi aipager khởi động lại.
- Chỉ khi không có gì đang chạy hay đang chờ gửi trong 2 phút, và bạn đã dùng chat riêng với bot trong 30 phút qua. Không bao giờ trong một nhóm, và không bao giờ trong lúc Telegram đang giới hạn chat đó.
- Mỗi lỗi chỉ được đề nghị một lần cho mỗi phiên bản aipager. Sau bất kỳ câu trả lời nào, nó không được đề nghị lại trên phiên bản đó; nó có thể quay lại sau khi bạn cập nhật, chỉ khi nó xảy ra lần nữa.
- **Để sau** và **Đừng hỏi lại** đều được tính là “không”, một đề nghị bạn không trả lời trong một ngày cũng vậy. **Xem báo cáo** đặt lại số đếm, kể cả khi sau đó bạn hủy. Hai lần “không” liên tiếp sẽ tắt đề nghị tự động: khi lần thứ hai là một lần chạm, một dòng thông báo sẽ cho bạn biết; dù thế nào thì `/settings` cũng hiện điều đó.
- Không bao giờ với một bản cài để phát triển (aipager được cài từ một thư mục trên máy, một bản sao git, hoặc ở chế độ editable).

Để tắt đề nghị tự động, dùng `/settings` trong chat riêng của bạn: **Báo cáo sự cố: Tắt**. Khi hai lần “không” đã tắt chúng, cùng trang đó có **Bật lại đề nghị**. Một file `reports.json` có tồn tại nhưng không đọc được cũng tắt đề nghị tự động (aipager không còn biết trước đó bạn đã trả lời gì); **Bật lại đề nghị** cũng xóa tình trạng đó. Trên một máy tính không có chat nào để hỏi, đặt `AIPAGER_REPORT_PROMPTS=0` trong môi trường của aipager. “Báo cáo sự cố” và `aipager report` luôn dùng được, bất kể các cài đặt này nói gì.

## <a id="what-is-kept-on-your-machine"></a>Những gì được giữ trên máy tính của bạn

- `~/.local/state/aipager/reports.json`: các lỗi aipager đã ghi nhận (theo dạng ở trên: tên loại, phiên bản, vị trí trong mã, tối đa 50), số đếm theo giờ trong 8 ngày, bản tóm tắt log trong 2 ngày, những lần dừng gần đây đã kết thúc ra sao, và trạng thái đề nghị (lần đề nghị gần nhất, số lần “không” liên tiếp, lỗi nào đã được đề nghị trên phiên bản nào). Chỉ bạn đọc được.
- `~/.local/state/aipager/report-sends.json`: hôm nay đã gửi hoặc thử gửi bao nhiêu báo cáo.
- `~/.local/share/aipager/install.json`: aipager khởi động lần đầu trên máy tính này khi nào (cho mốc 48 giờ ở trên).
- `~/.local/share/aipager/running.json`: được ghi trong lúc aipager chạy (số tiến trình, thời điểm bắt đầu, nó có chạy như một dịch vụ không, và ID của lần khởi động máy này), để phân biệt crash với dừng bình thường.

Không file nào chứa chat, tin nhắn, tên, đường dẫn hay token. `aipager uninstall` giữ lại chúng, cùng với phần còn lại của dữ liệu aipager, để lần cài lại tiếp tục từ chỗ cũ; nó liệt kê những gì nó giữ và in ra lệnh để xóa (xem [`aipager uninstall`](commands.md#command-line-aipager-uninstall)). Để chỉ xóa những file này, hãy xóa bốn file đó: xóa cả thư mục `~/.local/state/aipager` sẽ xóa hai file đầu (và cả trạng thái ngôn ngữ, `languages.json`), nhưng `install.json` và `running.json` phải xóa theo tên (đừng xóa `~/.local/share/aipager`, nơi còn chứa các thư mục phiên của aipager).

## <a id="see-also"></a>Xem thêm

- [Xử lý sự cố](troubleshooting.md): các lỗi hay gặp và cách khắc phục.
- [Mô hình bảo mật](security.md): aipager chạm vào những gì và những gì đi qua mạng.
- [Lệnh của bot](commands.md): `/help`, `/settings` và dòng lệnh.
