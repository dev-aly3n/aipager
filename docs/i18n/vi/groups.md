# Chế độ làm việc nhóm và nhóm

<!-- i18n-source: docs/groups.md @ daaa753 -->

> Trang này được dịch từ tiếng Anh; nếu hai bản khác nhau, bản tiếng Anh là bản đúng. ([English](../../groups.md))

Mặc định aipager là một bot riêng: bạn và bot trong một chat riêng, không có ai khác. **Chế độ làm việc nhóm** mở nó ra cho nhiều người hơn: một nhóm Telegram nơi nhiều lập trình viên cùng điều khiển các phiên, chat riêng của những người khác với bot, hoặc cả hai. Mỗi chat có thành viên riêng, và mỗi thành viên có một vai trò quyết định họ được làm gì ở đó: gửi tin nhắn, trả lời câu hỏi và yêu cầu cấp quyền của Claude, quản lý phiên, hay chỉ xem.

Chế độ làm việc nhóm là tùy chọn. Một bản cài chỉ có chat riêng của bạn vẫn hoạt động như trước.

## <a id="decide-carefully"></a>Hãy cân nhắc kỹ

Thêm một người dùng Telegram vào một chat là cho họ **quyền chạy mã trên máy đang chạy aipager**. Tùy vai trò, họ có thể:

- gửi tin nhắn mà Claude biến thành lệnh shell, sửa file và kết nối mạng;
- trả lời yêu cầu cấp quyền (Cho phép / Từ chối). `~/.claude/settings.json` của bạn vẫn quyết định Claude hỏi về những công cụ nào, nhưng bất kỳ ai có vai trò được duyệt đều có thể chạm Cho phép;
- bắt đầu, dừng, kết thúc và chuyển đổi giữa các phiên.

Hãy đối xử với danh sách thành viên như với quyền truy cập SSH vào máy. Log kiểm tra ([`~/.claude/aipager-audit.jsonl`](security.md#audit-log)) ghi lại ai đã làm gì, nhưng chỉ sau khi chuyện đã xảy ra.

## <a id="setup"></a>Thiết lập

Mọi thứ được thiết lập bằng `aipager config`. Bạn không bao giờ phải tự sửa file.

**Lần chạy đầu tiên** (chưa có cấu hình). Trình thiết lập hỏi qua bốn bước:

1. token của bot;
2. chat riêng của chính bạn với bot (tự phát hiện, hoặc dán vào);
3. tài khoản của bạn có phải là `owner` không (mặc định là có; nếu trả lời không, bạn sẽ là `admin`, vai trò vẫn giữ mức an toàn tối thiểu với bạn);
4. nó kiểm tra các phụ thuộc và cài hook của Claude Code.

Nó ghi chat riêng của bạn làm chat đầu tiên. Nó không hỏi chế độ mặc định: phần tóm tắt của nó ghi "New sessions start in Auto mode; owners and admins can choose Full access in Telegram under /settings → New sessions." (phiên mới bắt đầu ở Chế độ Auto; chủ sở hữu và quản trị viên có thể chọn Toàn quyền trong Telegram ở /settings → Phiên mới). Sau đó nó hỏi "Connected. Add a team group or other people now?" với Add a group, Add a person (DM) và I'm done.

**Các lần chạy sau** mở menu chỉnh sửa. Menu hiện các chat bạn đang có và đưa ra:

- **Add a group scope**: một nhóm mới, cùng các thành viên của nó (bên dưới).
- **Add a DM scope**: chat riêng của một người khác với bot. Một chat riêng có một thành viên; bạn chọn vai trò của họ (mặc định `user`; chỉ chọn `owner` cho một bản cài một người, như container riêng của một người bạn). Với một vai trò không có `can_manage` (như `user`), sau đó nó hỏi [thư mục gốc](#root-folders) của họ, điền sẵn giá trị mặc định (`~/aipager-users/<label>`). Với một người đã có chat riêng được thiết lập, trình thiết lập nói rõ điều đó và không đổi gì: hãy dùng Edit a member để đổi vai trò của họ.
- **Edit a scope**: với một nhóm, **Add a member**; với mọi chat, Rename, Edit scope deny_tools, **Set the members' root folder** (xem [Thư mục gốc](#root-folders)) và Remove this scope (không xóa được chat cuối cùng). Hãy kết thúc các phiên của một chat trước khi xóa nó: các phiên đang chạy của nó vẫn tiếp tục đăng vào đó, và không chat nào chạm tới chúng từ Telegram được nữa (từ máy tính thì `aipager session ls` và `aipager session kill <name>` vẫn làm được).
- **Edit a member**: Set role, Edit member deny_tools, Remove member (không có trong chat riêng, và không áp dụng cho thành viên cuối cùng của một nhóm). Với một chủ sở hữu hay quản trị viên (hoặc một vai trò của bạn có `bypass_role_denies`), Edit member deny_tools chỉ báo rằng các công cụ bị chặn không áp dụng cho vai trò đó.
- Test bot reachability, View policy, Re-install Claude Code hooks và Refresh bot token.

### <a id="adding-a-group"></a>Thêm một nhóm

1. Thêm bot vào nhóm, rồi chọn Add a group scope (hoặc Add a group ở lần chạy đầu tiên).
2. **ID của nhóm.** Chọn tự phát hiện rồi gửi `/start` trong nhóm, hoặc dán ID vào (một số âm). Trình thiết lập gửi một tin nhắn thử và hỏi xem nó đã tới chưa. Một nhóm đã được thiết lập rồi thì dừng ở đây với "This group is already set up. Use Edit a scope → Add a member." và không có gì thay đổi.
3. **Một nhãn** cho nhóm (hiện trong trình thiết lập và Mini App).
4. **Bạn trước tiên.** Khi bạn là chủ sở hữu chat riêng của mình, trình thiết lập đề nghị thêm bạn làm thành viên đầu tiên của nhóm với vai trò `owner` (mặc định là có; bạn có thể bỏ qua). Nếu nhóm không có chủ sở hữu hay quản trị viên nào, không ai ở đó dùng được Toàn quyền, `/settings` hay `/update`.
5. **Các thành viên khác**, từng người một: cách tìm họ (tự phát hiện, hoặc dán ID hay `@handle`), một nhãn (hiện thành `@label` trong chat), và một vai trò (mặc định `user`). Danh sách vai trò có `owner`, kèm một cảnh báo: chủ sở hữu toàn quyền kiểm soát máy này, kể cả cấu hình của aipager, token của bot và thông tin đăng nhập của Claude. Chỉ giao vai trò đó cho chính bạn. Giao `admin` cho những người nên được dùng Toàn quyền, `/settings` và `/update` trong nhóm. (Trong chat riêng của chính họ với bot, bất kỳ ai được gửi tin nhắn và trả lời Claude đều có thể dùng Toàn quyền cho các phiên của riêng mình, bất kể vai trò.)
6. **Một quy tắc không bắt buộc:** "Block file edits (Write, Edit) for `user` members in this group? Admins and the owner are not affected." (Chặn sửa file (Write, Edit) với các thành viên `user` trong nhóm này? Quản trị viên và chủ sở hữu không bị ảnh hưởng.) Mặc định là không: một `user` vốn chỉ ghi được bên trong thư mục dự án của phiên. Nếu chọn có, mọi lần Write và Edit trong một việc của một thành viên có vai trò không bỏ qua được quy tắc vai trò (`user`, `read_only`, và các vai trò bạn tự tạo mà không có `bypass_role_denies`) đều bị từ chối mà không hỏi, và việc dừng lại. Khi đó quản trị viên hoặc chủ sở hữu phải gửi phần việc đó.
7. **Thư mục gốc của các thành viên**, điền sẵn `~/aipager-users/group-<label>`: nơi các thành viên `user` và `read_only` của nhóm bắt đầu phiên và làm việc (xem [Thư mục gốc](#root-folders)).

Để thêm ai đó vào một nhóm đã được thiết lập, dùng Edit a scope → nhóm đó → Add a member. Mọi người đã có trong nhóm giữ nguyên chỗ và vai trò của họ.

### <a id="finding-ids-auto-detect"></a>Tìm ID (tự phát hiện)

Tự phát hiện một thành viên sẽ lấy tin nhắn mới nhất từ một người chưa được thêm và hỏi "Is this the person to add?" (Đây có phải người cần thêm không?) trước khi dùng (trả lời không thì nó tìm tiếp qua người đó). Nó đề xuất username Telegram của người đó làm nhãn. Trong một nhóm, người đó phải gọi bot: nhắc đến bot hoặc gửi `/start` (xem [bot đọc những gì](#what-the-bot-reads-in-a-group)). Trình thiết lập cho biết nó theo dõi ở đâu:

- **"Watching through the running daemon"** (qua aipager đang chạy): trong lúc `aipager start` đang chạy, aipager nhận mọi tin nhắn gửi cho bot, nên trình thiết lập đọc những gì aipager đã ghi trong `~/.claude/aipager-pending-users.json`. aipager ghi lại ai gọi bot trong một nhóm mà nó chưa phục vụ (ID, tiêu đề và loại của nhóm, cùng ID, username và tên của người đó), và bất kỳ ai không phải thành viên của một nhóm mà nó có phục vụ. Nó không bao giờ trả lời trong một nhóm mà nó không phục vụ. Một tin nhắn riêng từ người chưa được thiết lập sẽ được trả lời và không được ghi lại, nên để thêm chat riêng của một người trong lúc aipager đang chạy, hãy dán ID của họ (hoặc dừng aipager trước).
- **"Watching Telegram directly"** (trực tiếp từ Telegram): khi aipager đã dừng, trình thiết lập tự hỏi Telegram các tin nhắn gần đây của bot.

Chat riêng của chính bạn ở lần chạy đầu tiên luôn được đọc từ Telegram. Một tin nhắn từ một quản trị viên nhóm ẩn danh, hoặc một tin được đăng dưới tên nhóm hay tên kênh, không được tính: ID người gửi của nó (1087968824, 136817688 hoặc 777000) là chung cho mọi người đăng theo cách đó, nên trình thiết lập từ chối nó, dù được gõ vào hay được phát hiện. Hãy nhờ người đó đăng bằng tên của chính họ.

### <a id="where-the-config-lives"></a>Cấu hình nằm ở đâu

Trình thiết lập ghi `~/.config/aipager/aipager.yaml` (quyền 0600): token của bot, các chat mà bot phục vụ, và thành viên của từng chat cùng vai trò của họ. Mỗi vai trò được làm gì thì nằm trong `~/.config/aipager/policy.yaml`, file này là của bạn: trình thiết lập không bao giờ ghi vào nó (xem [Quy tắc hoạt động thế nào](#how-rules-work)). Các bản cài được thiết lập theo định dạng `team.yaml` cũ được chuyển đổi tự động, do aipager làm lúc khởi động hoặc do trình thiết lập làm ở lần chạy sau.

### <a id="live-reload"></a>Tải lại không cần khởi động lại

Sau mỗi thay đổi về chat hay thành viên làm từ menu chỉnh sửa (thêm hoặc xóa một nhóm hay một chat riêng, thêm hoặc xóa một thành viên, đặt vai trò, sửa `deny_tools`, đổi tên một chat), và sau một nhóm hay một người được thêm ở cuối lần chạy đầu tiên hoặc một nhóm làm dở mà bạn tiếp tục (một lần tải lại cho mọi thứ bạn đã thêm ở đó), `aipager config` gửi cho aipager đang chạy một lệnh tải lại trực tiếp (**SIGUSR1**) và báo "Scopes reloaded live (no daemon restart needed)". Lần tải lại đọc lại `aipager.yaml` và `policy.yaml`. Nếu cấu hình trên đĩa sẽ bị từ chối, trình thiết lập báo "Not applied" kèm lý do, và aipager giữ cấu hình cũ cho tới khi bạn sửa xong. Nếu trình thiết lập không biết chắc tiến trình nào là aipager, nó bảo bạn khởi động lại aipager.

Những gì một lần tải lại áp dụng ngay:

- ai được dùng bot trong từng chat, với vai trò và quy tắc nào;
- một nhóm hay chat riêng mới bắt đầu nhận tin nhắn và có menu `/` của nó; một chat đã bị xóa thì dừng (các phiên của nó vẫn chạy cho tới khi bạn kết thúc chúng);
- một tin nhắn đang bị giữ (đang chờ một yêu cầu được trả lời hoặc chờ việc của người khác kết thúc) từ một người không còn được gửi trong chat đó sẽ bị bỏ: nó nhận một 🤷 và bot trả lời “Không gửi tới Claude: @bob không còn được phép gửi tin nhắn ở đây.”;
- một việc đang chạy của một người đã bị xóa hoặc mất quyền chỉ giữ phần nghiêm nhất giữa quy tắc cũ và quy tắc mới (với người bị xóa là mức tối thiểu bị giới hạn) cho phần còn lại của việc đó. Một việc gõ trong terminal không bị thay đổi, trừ khi có một tin nhắn Telegram đã nhập vào: aipager không ghi lại là của ai, nên một việc như vậy bị thu hẹp khi bất kỳ chủ sở hữu nào của chat đó (hoặc, trong một chat riêng, thành viên của nó) mất quyền. Một việc đã đang chạy khi aipager khởi động lại (vẫn đang làm việc, hoặc đang chờ một câu hỏi hay yêu cầu cấp quyền) bị thu hẹp theo mọi thành viên của chat đó đã mất quyền. Một lần nâng quyền không bao giờ nới rộng một việc đang chạy: quyền mới áp dụng từ việc tiếp theo của người đó.

Chỉ có **token bot** mới cần khởi động lại (`aipager config` nói vậy sau Refresh bot token). Khi không có aipager nào đang chạy, không có lệnh tải lại nào được gửi: lần `aipager start` sau sẽ đọc cấu hình mới.

Để tải lại bằng tay (ví dụ sau khi sửa `policy.yaml`), chỉ gửi tín hiệu tới tiến trình aipager. Dưới dịch vụ nền:

```sh
pid=$(systemctl --user show -p MainPID --value aipager.service)
[ "${pid:-0}" -gt 0 ] && kill -USR1 "$pid"
```

(Bước kiểm tra này quan trọng: khi dịch vụ không chạy, `MainPID` là `0`, và `kill -USR1 0` sẽ gửi tín hiệu tới các tiến trình của chính shell của bạn.)

Với một aipager bạn tự khởi động bằng `aipager start`, hãy gửi `kill -USR1 <pid>` tới đúng tiến trình đó. Đừng gửi tín hiệu tới mọi kết quả của `pgrep -f 'aipager start'` (một shell hay container chạy aipager cũng khớp, và SIGUSR1 làm dừng một tiến trình không xử lý nó), và đừng dùng `systemctl --user kill` mà không có `--kill-whom=main` (các phiên của dịch vụ dùng chung nhóm tiến trình với nó và sẽ bị dừng).

Nếu lúc tải lại `aipager.yaml` hoặc `policy.yaml` bị hỏng, hoặc `aipager.yaml` không có, aipager ghi một cảnh báo vào log và giữ cấu hình trước đó trong bộ nhớ. Một lỗi gõ nhầm hay một file bị chuyển đi chỗ khác không thể khóa bạn ở ngoài hay cho ai đó vào: một lần tải lại không bao giờ đưa một aipager đã có chat được cấu hình quay về chế độ cá nhân (chỉ một lần khởi động lại mới làm vậy).

### <a id="bot-admin-rights-and-privacy-mode"></a>Quyền quản trị của bot và chế độ riêng tư

**Hãy cho bot làm quản trị viên của nhóm, với quyền ghim tin nhắn.** Bot cần quyền đó để ghim thanh trạng thái của nhóm (xem [Mọi người thấy gì](#what-everyone-sees)). Không có quyền đó, nhóm vẫn hoạt động nhưng không có thanh trạng thái; nếu sau này bạn mới cho bot quyền đó, hãy khởi động lại aipager để thanh trạng thái xuất hiện.

Một bot là quản trị viên nhóm sẽ nhận mọi tin nhắn trong nhóm, bất kể chế độ riêng tư trong BotFather của nó. Điều đó an toàn: aipager chỉ xử lý những tin nhắn được liệt kê ở [bot đọc những gì](#what-the-bot-reads-in-a-group) và bỏ qua phần còn lại, nên chuyện trò trong nhóm không bao giờ tới được một phiên. Một bot không phải quản trị viên thì mặc định ở chế độ riêng tư (cứ để bật), và khi đó Telegram chỉ chuyển cho nó một số tin nhắn của nhóm; nếu một tin nhắn của bạn không tới, hãy trả lời một tin nhắn của bot hoặc dùng một lệnh. Cài đặt riêng tư không thay đổi gì về những thứ aipager xử lý.

Là quản trị viên Telegram của nhóm không cho một người thêm quyền gì trong aipager. Ai được làm gì chỉ đến từ vai trò của họ trong `aipager.yaml`.

### <a id="when-telegram-upgrades-the-group"></a>Khi Telegram nâng cấp nhóm

Telegram biến một nhóm thường thành supergroup khi bạn đặt nhóm ở chế độ công khai, bật chủ đề, vượt quá 200 thành viên, hoặc đổi một số quyền quản trị, và việc nâng cấp cho nhóm một ID chat mới. aipager tự đi theo: khi thấy việc nâng cấp (thông báo của Telegram trong nhóm, hoặc lần đăng đầu tiên vào ID cũ), nó chuyển scope của nhóm trong `aipager.yaml` sang ID mới, chuyển các phiên, đích gửi tin nhắn và `/settings` của nhóm theo, và đăng một dòng trong nhóm: “Telegram đã nâng cấp nhóm này. aipager đã chuyển theo, bạn không cần làm gì.” Thanh trạng thái được ghim bắt đầu lại trong nhóm đã nâng cấp. Log của aipager có một cảnh báo ghi cả hai ID.

Tên phiên bên trong aipager giữ ID cũ (bạn không bao giờ thấy chúng), và `aipager.yaml` giữ một bản ghi ngắn `chat_migrations` về lần chuyển đó, nên một phiên bắt đầu trước khi nâng cấp vẫn thuộc về nhóm đã nâng cấp, kể cả sau khi khởi động lại. Tên nội bộ của một phiên với ID cũ (`/name__g<old id>`) dùng được trong nhóm đã nâng cấp và không ở đâu khác. Nếu aipager đã dừng trong lúc nâng cấp, nó nhận ra ở lần khởi động sau. Một việc đang chạy đúng lúc nâng cấp vẫn chạy xong và câu trả lời của nó tới được nhóm đã nâng cấp, nhưng thẻ tiến độ của nó không hiện lại trong phần còn lại của việc đó. Nếu ID mới đã là một scope riêng, không có gì được chuyển và log ghi rõ điều đó; hãy xóa một trong hai bằng `aipager config`. “Test bot reachability” trong `aipager config` hiện ID mới khi nó gặp một nhóm đã được nâng cấp.

Mỗi lần khởi động, aipager tra cứu mỗi nhóm một lần. Nếu Telegram trả lời rằng không tìm thấy nhóm hoặc bot không được dùng nhóm đó (nhóm đã bị xóa, bot đã bị đưa ra, hoặc nhóm đã được nâng cấp trong lúc aipager dừng mà Telegram không nói sang ID nào), log của aipager có một cảnh báo nêu tên scope: hãy kiểm tra bot có còn trong nhóm không, và nếu nhóm đã được nâng cấp, hãy thêm lại nhóm trong `aipager config` bằng Add a group scope (nhóm nhận ID mới; hãy thêm lại các thành viên của nó), rồi xóa scope cũ bằng Edit a scope → Remove this scope (ID cũ không còn dùng được nữa). aipager không đăng gì và không thử lại liên tục.

### <a id="forum-topics-are-not-supported"></a>Không hỗ trợ chủ đề diễn đàn

Hãy dùng một nhóm thường (không có chủ đề) cho mỗi nhóm làm việc. Trong một nhóm đã bật chủ đề, aipager vẫn hoạt động, nhưng không giữ trong một chủ đề:

- câu trả lời cho một lệnh hay một tin nhắn (ví dụ `/status`, hoặc câu trả lời cho “Phiên nào?”) ở lại trong chủ đề mà nó được gửi;
- thông báo, thẻ làm việc, câu trả lời, yêu cầu cấp quyền và thanh trạng thái được ghim được đăng vào nhóm mà không có chủ đề, nên chúng có thể rơi vào General;
- mỗi người có một phiên hiện tại cho cả nhóm, không phải một phiên cho mỗi chủ đề.

Bật chủ đề cũng nâng cấp một nhóm thường thành supergroup, điều mà aipager đi theo (ở trên).

## <a id="roles"></a>Vai trò

Bốn vai trò có sẵn (xem `aipager/safety.py`). Vai trò được tính là vai trò của người đó **trong chat mà họ đang thao tác**. Không ai đổi được thành viên hay vai trò từ Telegram hoặc Mini App: chỉ `aipager config` trên máy tính mới làm được, điều này giữ việc đó trong tay chủ sở hữu.

| | `owner` | `admin` | `user` | `read_only` |
|---|---|---|---|---|
| Gửi tin nhắn (`can_prompt`) | ✅ | ✅ | ✅ | ❌ |
| Trả lời Claude: Cho phép, Từ chối, câu hỏi (`can_approve`) | ✅ | ✅ | ✅ | ❌ |
| Quản lý: Toàn quyền, `/settings` của nhóm, `/update`, cài tính năng thoại (`can_manage`) | ✅ | ✅ | ❌ (Toàn quyền: ✅ trong chat riêng của chính họ) | ❌ |
| Đổi thành viên và vai trò từ Telegram hoặc Mini App | ❌ | ❌ | ❌ | ❌ |
| Bỏ qua quy tắc cấm của vai trò (`bypass_role_denies`) | ✅ | ✅ | ❌ | ❌ |
| Bỏ qua mức an toàn tối thiểu (`bypass_safety`) | ✅ | ❌ | ❌ | ❌ |
| Bash và các công cụ chạy mã khác | ✅ | ✅ (quy tắc chỉ làm hết sức có thể) | ❌ | ❌ |
| Mọi lệnh slash | ✅ | ✅ | chỉ các lệnh trên bàn phím Lệnh, `/compact` và việc đổi mô hình | như `user` |
| Nơi bắt đầu phiên (`/new`, Tiếp tục, Khởi động lại, đổi chế độ) | mọi nơi | mọi nơi | chỉ bên trong [thư mục gốc](#root-folders) của chat | như `user` |
| Nơi file được ghi | mọi nơi | mọi nơi mà mức an toàn tối thiểu cho phép | thư mục dự án của phiên (khi nó nằm trong thư mục gốc) và scratchpad của phiên, không bao giờ là thư mục home | như `user` |
| Đọc trong thư mục home của bạn | mọi nơi mà mức an toàn tối thiểu cho phép | mọi nơi mà mức an toàn tối thiểu cho phép | chỉ bên trong thư mục gốc (các thư mục khác trên máy như trước, thư mục gốc của thành viên khác thì không bao giờ) | như `user` |
| File chứa thông tin đăng nhập (`~/.ssh`, `~/.git-credentials`, ...) | đọc được | đọc được | không bao giờ (mặc định) | không bao giờ (mặc định) |
| `Grep`/`Glob` | mọi nơi | mọi nơi trừ các thư mục được bảo vệ của aipager (không tìm trong thư mục là, nằm trong, hoặc chứa một thư mục như vậy) | thư mục dự án của phiên và scratchpad của nó | như `user` |

- **owner**: toàn quyền kiểm soát, kể cả mức an toàn tối thiểu có sẵn. Nên có đúng một người: người vận hành máy.
- **admin**: mọi thứ một chủ sở hữu làm được trong Telegram và Mini App: chuyển phiên sang Chế độ Toàn quyền (và bắt đầu phiên ở chế độ đó), tiếp tục một phiên ở Toàn quyền, đổi `/settings` của một nhóm, chạy `/update`, cài tính năng thoại, gửi mọi lệnh slash. Quản trị viên cũng bỏ qua quy tắc cấm của vai trò (`deny_tools` của scope và của thành viên không áp dụng cho họ). Có hai thứ vẫn thuộc về chủ sở hữu:
  - đổi thành viên và vai trò: chỉ `aipager config` trên máy tính làm được, và không gì trong Telegram hay Mini App làm được;
  - bỏ qua mức an toàn tối thiểu (cấu hình và token bot của aipager, thông tin đăng nhập của Claude Code, các file điều khiển của aipager). `Grep` và `Glob` của quản trị viên tìm được ở mọi nơi khác, nhưng không tìm trong một thư mục là một trong số đó, nằm bên trong hoặc chứa một thư mục như vậy: `Grep` trên `~`, `/` hoặc `/tmp` (hoặc không có thư mục, từ một phiên ở thư mục home) bị từ chối, một `Glob` có mẫu bắt đầu trong một thư mục như vậy cũng thế (như `/home/you/.config/aipager/*`). Hãy tìm bên trong một thư mục dự án. Trên các phiên bản Claude Code không có công cụ `Grep`, Claude tìm qua Bash; ở đó một lần tìm hay liệt kê đệ quy toàn bộ thư mục home hoặc `/` (`grep -rn x ~`, `rg x $HOME`, `find / -name x`) cũng bị từ chối, theo kiểu làm hết sức có thể (một lần tìm không có thư mục, hoặc với `..`, chạy từ thư mục home hoặc bên dưới nó thì không bị nhìn thấy), còn những lần tìm trong dự án thì không, kể cả một lần tìm ĐỂ tìm một đường dẫn home (`grep -rn "$HOME" src`) ([bảo mật](security.md#team-mode-enforcement)).

  Quản trị viên vẫn có Bash, và khi có shell thì các mẫu lệnh của mức tối thiểu chỉ làm hết sức có thể, không phải ranh giới: chỉ cho ai đó làm quản trị viên nếu bạn sẵn sàng cho họ một shell trên máy.
- **user**: quy tắc cấm có hiệu lực. Không có Bash, không có công cụ chạy mã nào khác (PowerShell, Monitor, tin nhắn được lên lịch, workflow; danh sách là `safety.CODE_EXECUTION_TOOLS`), không `SendMessage` tới phiên khác, không đổi worktree. Việc ghi file chỉ vào bên trong thư mục của phiên và scratchpad Claude Code của phiên, và không bao giờ vào `.claude/`, `.git/` hay `.mcp.json` của thư mục đó (mỗi thứ đó đều chạy lệnh). `Grep` và `Glob` chỉ tìm bên trong những thư mục đó, với glob tương đối đơn giản. Một `user` chỉ bắt đầu, tiếp tục, khởi động lại và đổi chế độ phiên bên trong [thư mục gốc](#root-folders) của chat, và không đọc gì trong thư mục home của bạn nằm ngoài thư mục đó. Một thư mục phiên là thư mục home của bạn, `/` hay một thư mục nằm trên nó, hoặc nằm ngoài thư mục gốc (một phiên bắt đầu trước khi có thư mục gốc, hoặc do người khác bắt đầu), thì không được tính: ở đó một `user` chỉ ghi và tìm được trong scratchpad. Việc đọc theo quy tắc đường dẫn được bảo vệ, và các file chứa thông tin đăng nhập của bạn không bao giờ đọc được: `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config/gh`, `~/.git-credentials`, `~/.netrc`, `~/.docker/config.json`, `~/.kube`, `~/.config/gcloud`, `~/.azure`, `~/.password-store`, `~/.pgpass`, `~/.npmrc` và `~/.pypirc`. Lệnh slash: chỉ những lệnh trên bàn phím Lệnh, `/compact` và việc đổi mô hình (xem [Tin nhắn chạy dưới quyền của ai](#who-a-message-runs-as)).
- **read_only**: người quan sát. Họ thấy mọi thứ trong nhóm, và có thể dùng `/status`, `/diff`, `/whoami`, `/help`, `/start` và `/app`, xem `/settings`, và lật trang một danh sách `/resume` mà người khác đã mở. Tin nhắn chữ, tin nhắn thoại và file của chính họ không được gửi đi đâu cả. Tin nhắn đầu tiên họ gửi trong một chat nhận một câu trả lời cho biết vai trò của họ là `read_only`; sau đó bot im lặng với họ trong chat đó (không trả lời, không reaction) cho tới khi aipager khởi động lại. Họ có cùng giới hạn về công cụ và việc ghi như `user`, phòng khi có tin nhắn nào của họ tới được một phiên.

### <a id="custom-roles"></a>Vai trò tự tạo

`policy.yaml` có thể đổi bất kỳ trường nào của một vai trò có sẵn, hoặc định nghĩa vai trò mới. Một trường bạn đặt sẽ thay giá trị có sẵn. Các trường:

- `can_prompt`, `can_approve`: gửi tin nhắn; trả lời Claude.
- `can_manage`: dùng Toàn quyền, đổi `/settings` của nhóm, chạy `/update` và cài tính năng thoại (có sẵn: `owner` và `admin` là true, `user` và `read_only` là false). Nó không bao giờ cho ai đổi thành viên hay vai trò, và không bao giờ bỏ qua mức an toàn tối thiểu.
- `bypass_role_denies`, `bypass_safety`: xem bảng.
- `deny_tools`, `allow_tools`, `deny_bash_patterns`, `deny_paths_no_access`, `deny_paths_no_write`: xem [Quy tắc hoạt động thế nào](#how-rules-work).

Một vai trò bạn định nghĩa từ đầu sẽ bắt đầu với `can_prompt` và `can_approve` bật, `can_manage` và cả hai quyền bỏ qua tắt, các file chứa thông tin đăng nhập nằm trong `deny_paths_no_access` của nó, và **không có công cụ nào bị cấm, nên nó có Bash** cho tới khi bạn đặt `deny_tools` (`aipager doctor` cảnh báo một lần khi có thành viên mang vai trò đó). Giới hạn ghi và tìm áp dụng cho mọi vai trò không có `bypass_safety` hay `bypass_role_denies`. [Thư mục gốc](#root-folders) áp dụng cho mọi vai trò không có `can_manage`, bất kể quyền bỏ qua của nó: một vai trò bạn định nghĩa có `bypass_role_denies` hoặc `bypass_safety` nhưng không có `can_manage` được ghi và tìm ở mọi chỗ bên trong thư mục gốc của nó (và scratchpad của nó), không đọc gì trong thư mục home của bạn nằm ngoài thư mục đó, và không ở đâu khác (với `bypass_safety`, trong một phiên chạy bên ngoài thư mục gốc của nó, nó chỉ ghi vào scratchpad). Chỉ chủ sở hữu và quản trị viên là không bị giới hạn.

### <a id="root-folders"></a>Thư mục gốc

Mỗi chat có một thư mục gốc cho thành viên: thư mục nơi các thành viên mà vai trò ở đó không có `can_manage` (`user` và `read_only` có sẵn, và các vai trò bạn định nghĩa mà không có nó) bắt đầu và giữ các phiên của họ. Chủ sở hữu và quản trị viên không bị giữ trong đó. Thư mục gốc của một chat riêng thuộc về người đó; của một nhóm thì dùng chung cho các thành viên bị giới hạn của nhóm. Hai chat không bao giờ dùng chung một thư mục, trừ khi bạn cố ý cho cả hai cùng một thư mục (trình thiết lập sẽ bảo bạn xác nhận).

- **Đặt thư mục.** `aipager config` hỏi nó khi bạn thêm một nhóm, hoặc một chat riêng cho một vai trò không có `can_manage`, với giá trị mặc định được điền sẵn, và Edit a scope → **Set the members' root folder** đổi nó (áp dụng ngay, bằng một lần tải lại trực tiếp). Cấu hình hiện tại hiện thư mục gốc của từng chat, kèm `(default)` khi chưa đặt.
- **Mặc định** là `~/aipager-users/<label>` cho một chat riêng (nhãn của người đó) và `~/aipager-users/group-<label>` cho một nhóm. Nhãn được chuyển thành chữ thường, và mọi ký tự không phải chữ cái, chữ số, `.`, `_` và `-` đều thành `-`; khi hai chat sẽ nhận cùng một thư mục, mỗi chat được thêm ID chat của mình (`sara-123456`). Thư mục (và `~/aipager-users`, khi nó mới có) được tạo với quyền 0700 để chỉ bạn mở được, vào lần đầu một thành viên mở `/new` hoặc biểu mẫu “Bắt đầu phiên” của Mini App. Trước khi một phiên bắt đầu trong đó, aipager đánh dấu nó là đáng tin với Claude Code, để câu hỏi “Do you trust this folder?” của Claude Code không kết thúc phiên đầu tiên của thành viên. Một mặc định không bao giờ dời chỗ: khi một chỉnh sửa trong `aipager config` (thư mục gốc của một chat khác, một chat bị xóa, một nhãn mới) sẽ đổi thư mục mặc định của một chat, trình thiết lập ghi thư mục mà chat đó đang có làm thư mục gốc của nó.
- **Những gì trình thiết lập từ chối**: một đường dẫn tương đối, ký tự đại diện, `/`, thư mục home của bạn hoặc một thư mục nằm trên nó, một thư mục là, nằm bên trong, hoặc chứa một trong các thư mục được bảo vệ của aipager hay các file chứa thông tin đăng nhập (`~/.claude`, `~/.config/aipager`, `~/.config`, `~/.ssh`, ...), và thư mục tạm của Claude Code (`/tmp/claude-*`, nơi scratchpad của mọi phiên nằm). Một thư mục chưa tồn tại thì không sao. Nó bảo bạn xác nhận một thư mục mà hệ thống của bạn chạy chương trình từ đó (`~/bin`, `~/.local/bin`, `~/.config/systemd`, `~/.config/autostart`, hoặc một thư mục bên trong chúng).
- **Bắt đầu phiên.** Trang Thư mục của `/new` và Mini App liệt kê thư mục gốc và các thư mục nằm ngay bên trong nó (không có thư mục ẩn, và không có link dẫn ra ngoài nó), không có dòng “Thư mục mặc định”; một phiên mới bắt đầu trong thư mục gốc trừ khi bạn chọn một trong những thư mục đó. “Thư mục mới” tạo thư mục bên trong thư mục bạn đã chọn. Mọi thứ khác nhận “Vai trò của bạn chỉ được bắt đầu phiên trong thư mục của bạn ~/... hoặc thư mục bên trong nó.”
- **Phiên nằm ngoài nó.** Tiếp tục, khởi động lại, Thay thế và đổi chế độ một phiên có thư mục nằm ngoài thư mục gốc sẽ nhận “Phiên này làm việc bên ngoài thư mục của bạn ~/..., nên vai trò của bạn không được tiếp tục, khởi động lại hay đổi chế độ của nó. Hãy nhờ quản trị viên.” Tin nhắn tới một phiên như vậy đang chạy vẫn được gửi, và hook an toàn giữ việc lại: ở đó nó chỉ ghi vào scratchpad.
- **Hook giữ một việc trong giới hạn nào.** Việc của một thành viên bị giới hạn chỉ ghi vào thư mục của phiên (khi nó nằm trong thư mục gốc) và scratchpad của nó, không đọc, không `Grep` hay `Glob` gì trong thư mục home của bạn nằm ngoài thư mục gốc (trừ scratchpad và file trả lời riêng của nó), và không bao giờ chạm vào thư mục gốc của một chat khác, dù nó nằm ở đâu. Các thư mục ngoài thư mục home của bạn (`/usr`, `/etc`, ...) vẫn đọc được, các thư mục được bảo vệ vẫn bị từ chối, và `Bash` vẫn bị cấm với `user`. Khi một việc trộn hai thành viên có thư mục gốc khác nhau, thư mục hẹp hơn thắng; hai thư mục không lồng vào nhau thì chỉ còn lại thư mục riêng của phiên.

Để trả Bash lại cho `user` (chỉ với những người bạn sẵn sàng cho một shell), hãy nêu nó trong `allow_tools` (khi đó nó cũng trở thành danh sách cho phép của vai trò đó) hoặc thay `deny_tools` của nó:

```yaml
roles:
  user:
    # danh sách có sẵn, bỏ Bash
    deny_tools: [PowerShell, Monitor, REPL, Workflow, CronCreate,
                 RemoteTrigger, AppifactRepl, self_hosted_runner_spawn_local,
                 self_hosted_runner_requeue_session, SendMessage,
                 EnterWorktree, ExitWorktree]
```

Sau đó `aipager doctor` cảnh báo: `role user has Bash: its safety rules are best-effort, not a boundary`. Nếu `policy.yaml` của bạn đã đặt `deny_tools` cho `user` trước khi có mặc định này, danh sách của bạn vẫn thay thế nó, nên vai trò đó có Bash cho tới khi bạn thêm lại; cảnh báo của doctor sẽ cho bạn biết. Xem [bảo mật → áp dụng quy tắc trong chế độ làm việc nhóm](security.md#team-mode-enforcement) để biết những giới hạn này không bao gồm những gì.

## <a id="talking-to-the-bot-in-a-group"></a>Nói chuyện với bot trong một nhóm

### <a id="what-the-bot-reads-in-a-group"></a>Bot đọc những gì trong một nhóm

Trong một nhóm, aipager chỉ xử lý một tin nhắn là:

- **một lệnh**: `/status`, `/x1 fix the tests`, hoặc như vậy kèm `@aipagerbot` sau lệnh (`/x1@aipagerbot`), thứ mà Telegram thêm vào khi bạn chọn lệnh từ menu. Một lệnh cho bot khác (`/x1@otherbot`) bị bỏ qua;
- **một tin trả lời một tin nhắn của bot**;
- **một tin nhắn nhắc đến bot**: `@aipagerbot fix the tests`;
- **một lần chạm trên bàn phím của bot** trong nhóm. Trong một nhóm, mọi nút trên bàn phím đều bắt đầu bằng một ô vuông nhỏ (`▫️ dừng`, `▫️ Xóa cuộc trò chuyện`), và chỉ chữ có dấu đó mới được tính là một lần chạm. Gõ chữ trơn chỉ là chuyện trò bình thường: một thành viên gõ `dừng`, `Xóa cuộc trò chuyện`, `Opus` hay tên một phiên thì không đổi gì. Để cố ý dùng một chữ trên bàn phím mà không cần bàn phím, hãy gọi bot: `@aipagerbot dừng`, hoặc `dừng` dưới dạng tin trả lời một tin nhắn của bot.

Mọi thứ khác (các thành viên nói chuyện với nhau, một bức ảnh hay tin nhắn thoại không phải là tin trả lời bot) không nhận reaction hay câu trả lời nào. Điều này đúng dù bot có là quản trị viên nhóm hay không. Tin nhắn đã sửa bị bỏ qua ở mọi nơi: sửa một tin nhắn thì không có gì xảy ra, hãy gửi lại nó.

Một bức ảnh, file hay tin nhắn thoại cũng như vậy: gửi nó dưới dạng tin trả lời bot, hoặc nhắc đến bot trong chú thích. Với một album, hãy đặt chú thích ở mục đầu tiên: các mục của album đến sau mục có chú thích, lời nhắc hay tin trả lời thì được nhận, các mục đến trước nó thì không.

**Lời nhắc được bỏ đi.** `@aipagerbot fix the tests` tới Claude thành `fix the tests` (Claude Code sẽ đọc `@word` là một file). `@aipagerbot status` và `@aipagerbot x2` hoạt động như gõ `status` hay `x2` trong chat riêng, và `@aipagerbot` một mình thì cho biết tin nhắn của bạn đang đi đâu. Một lệnh phiên sau lời nhắc vẫn dùng được (`@aipagerbot /x1 fix it`), nhưng một lệnh của chính aipager thì không: `@aipagerbot /stop` nhận “Hãy gửi riêng /stop, không nhắc tới bot.”

**Chỉ dành cho thành viên.** Một người không phải thành viên của nhóm nhận một câu trả lời (“🚫 Bạn không có trong danh sách được cho phép dùng bot trong chat này.”) kèm ID Telegram của họ để đưa cho bạn, và được ghi lại cho `aipager config`; sau đó bot bỏ qua họ cho tới khi aipager khởi động lại. Một thành viên `read_only` được báo vai trò của mình một lần (xem [Vai trò](#roles)).

**Quản trị viên ẩn danh phải đăng bằng tên của chính họ.** Một quản trị viên nhóm bật “Remain anonymous”, hoặc một tin nhắn được đăng dưới tên nhóm hay tên kênh, không cho biết ai đã viết: Telegram hiện cùng một người gửi cho mọi quản trị viên ẩn danh. aipager không thể gán vai trò của bất kỳ ai cho một tin nhắn như vậy, nên nó trả lời “Hãy gửi bằng tên của chính bạn để dùng bot.” (một lần mỗi chat cho tới khi aipager khởi động lại) và không làm gì khác. Nó không bao giờ được ghi lại như một người đang chờ, và trình thiết lập từ chối ID dùng chung đó. Hãy tắt “Remain anonymous” cho chính bạn trong phần cài đặt quản trị của nhóm, hoặc đăng từ tài khoản của chính bạn.

### <a id="talking-to-a-session-in-a-group"></a>Nói chuyện với một phiên trong nhóm

Trả lời một tin nhắn của một phiên (câu trả lời của nó, thẻ của nó) và tin nhắn của bạn sẽ tới phiên đó. Hoặc gửi `/x1 tin nhắn của bạn`, hoặc một chú thích bắt đầu bằng `/x1`.

**Mỗi người có phiên hiện tại của riêng mình**: phiên mà họ chuyển tới gần nhất (`/x1`), đã gửi tin nhắn tới, đã trả lời hoặc đã bắt đầu. Phiên của một thành viên khác trả lời không đổi nó, tin nhắn của người khác cũng vậy. Một lời nhắc, một tin nhắn thoại, một mẫu hay một lệnh trên bàn phím đi tới phiên hiện tại của chính bạn, và một lệnh trên bàn phím hay một mẫu nêu tên phiên đó trong câu trả lời (“🧹 Đã gửi /clear đến x1”). Một `/stop`, `/mode`, `/kill`, `/restart`, `/rename`, `/delete` hoặc `/diff` trơn chỉ tác động lên phiên hiện tại của chính bạn (qua bước xác nhận thường lệ của nó), và chỉ khi lệnh hợp lý với phiên đó (đang làm việc với `/stop`, đã kết thúc với `/delete`); nếu không, nó luôn hiện bộ chọn, kể cả khi chỉ còn một phiên, vì phiên đó có thể là của người khác. `/clearqueue` và `/now` tác động lên phiên hiện tại của bạn. `/kill x1` và các lệnh khác có tên phiên thì tác động lên phiên đó, như trước.

- Khi bạn chưa có phiên hiện tại của riêng mình và có nhiều hơn một phiên đang chạy, bot hỏi “Phiên nào? Trả lời một tin nhắn của phiên đó, hoặc gửi /x1 kèm tin nhắn của bạn.” với một nút cho mỗi phiên, và không gửi gì. Khi chỉ có một phiên đang chạy, tin nhắn đi tới đó.
- Nếu phiên hiện tại của bạn đã kết thúc, tin nhắn không được gửi tới phiên duy nhất còn đang chạy (nó có thể là của người khác): bot trả lời “x1 đã kết thúc. Phiên nào?” với một nút cho mỗi phiên đang chạy và, khi x1 có thể được tiếp tục, “▶️ Tiếp tục x1”, và không gửi gì.
- Chạm nút của một phiên trong một trong hai câu hỏi đó cũng gửi luôn tin nhắn mà câu hỏi nói tới (một tin chữ, bản chép của một tin nhắn thoại hoặc một file) tới phiên đó, như thể bạn đã gửi nó ở đó, và câu hỏi đổi thành “Đã gửi đến x2.”. Chỉ người đã gửi tin nhắn mới dùng được các nút, và chỉ trong mười phút và cho tới khi aipager khởi động lại: sau đó nút chỉ làm phiên đó thành phiên hiện tại của bạn (“Tin nhắn đó đã quá cũ nên không được gửi; hãy gửi lại.”). ▶️ Tiếp tục đưa x1 trở lại nhưng không gửi gì: hãy gửi lại tin nhắn của bạn.
- Thanh được ghim không có dòng “Tin nhắn sẽ đến” trong nhóm, vì điều đó khác nhau với mỗi người.

**Những thẻ chờ câu trả lời là của từng người.** Thẻ phiên mới ghi “Trả lời tin nhắn này bằng tên phiên, kèm việc cần làm trước nếu bạn muốn:”, câu hỏi đổi tên ghi “Trả lời tin nhắn này bằng tên mới.”. Trong nhóm, mỗi thẻ cho thấy nó là của ai (“🆕 Phiên mới (@alice)”), chỉ người đó trả lời được, và hai người có thể cùng lúc bắt đầu hay đổi tên phiên. Tin trả lời của một thành viên khác vào thẻ phiên mới của ai đó nhận “Thẻ này là của @alice. Gửi /new để có thẻ của riêng bạn.” và không đi đâu cả. Một câu hỏi đổi tên thôi chờ sau 10 phút, hoặc ngay khi người của nó làm việc khác. Thẻ “x1 đã sẵn sàng”, câu trả lời cho `/x1`, `/start` và `/help` đều nói hãy trả lời một tin nhắn của phiên hoặc nhắc đến bot (“Trả lời một tin nhắn của x1 (hoặc nhắc đến @aipagerbot) để nói chuyện với phiên đó.”), vì một tin nhắn thường không tới được bot.

**Bàn phím là của từng người.** Mẫu, Lệnh, Mô hình › và « Quay lại chỉ đổi bàn phím của người chạm vào chúng: bot trả lời dưới dạng tin trả lời lần chạm của người đó, và bàn phím của không ai khác thay đổi. « Quay lại đi lên từ chỗ bạn đang ở, bất kể các thành viên khác đang xem gì. Bàn phím chính do `/start` gửi là của cả nhóm, và đưa mọi người về bàn phím chính. Khi một phiên của nhóm bắt đầu hay kết thúc, aipager gửi lại bàn phím chính đó cho nhóm, một lần, để các nút phiên của nó luôn đúng. Sau khi nâng cấp từ một phiên bản chưa có các nút có dấu, một lần chạm vào nút của bàn phím cũ có thể bị bỏ qua: hãy gửi `/start` một lần để nhóm nhận bàn phím mới. Bàn phím của một chat riêng không có dấu và hoạt động như trước.

### <a id="the-mini-app"></a>Mini App

`/app` chỉ mở Mini App trong chat riêng. Trong nhóm, nó không có trong menu `/`; nếu gõ ở đó, nó báo cho một thành viên đã có chat riêng với bot được thiết lập “📱 Mini App chỉ dùng được trong chat riêng - hãy nhắn riêng cho bot rồi gửi /app ở đó.”, và báo cho người khác “Mini App mở từ chat riêng của bạn với bot. Hãy nhờ chủ bot thêm bạn vào.”

Mini App hiện mỗi lần một chat. Một người thuộc về nhiều chat (ví dụ chat riêng của họ và một nhóm) sẽ có một hàng tên chat ở trên cùng để chuyển qua lại; nó mở ở chat riêng của họ, nếu không thì ở chat đầu tiên có tên họ, và nhớ lựa chọn gần nhất trên điện thoại đó. Mọi thứ trong đó (các phiên, các nút của chúng, cài đặt và các thông báo nó đăng) là của chat đang chọn, với vai trò mà người đó có trong chat ấy. Với một chat thì không có gì thêm.

## <a id="who-a-message-runs-as"></a>Tin nhắn chạy dưới quyền của ai

Mọi tin nhắn đều chạy với **quyền của người đã gửi nó**, trong chat mà họ gửi. Một người là `user` trong nhóm và `owner` của chat riêng của chính họ thì gửi, chạm và chạy lệnh trong nhóm với tư cách `user`, bất kể thứ tự các chat trong `aipager.yaml`.

- Mọi tin nhắn Telegram mang danh tính và quy tắc của người gửi vào phiên (xem [bảo mật → áp dụng quy tắc trong chế độ làm việc nhóm](security.md#team-mode-enforcement)). Claude thấy ai đã gửi từng tin nhắn (`[via Telegram · @bob · role:user]` trong một nhóm), không bao giờ là người đã điều khiển phiên trước đó. Ai gửi tin nhắn thì trở thành người điều khiển phiên, dù họ gõ, dùng `/x1`, một mẫu hay một lệnh trên bàn phím.
- **Tin nhắn bị giữ vẫn giữ người gửi của nó.** Trong lúc việc của một người khác đang chạy (kể cả một agent chạy nền mà việc đó đã khởi động), hoặc trong lúc tin nhắn của người khác vẫn đang chờ được nhận, hoặc trong lúc có một yêu cầu cấp quyền hay câu hỏi đang mở, một tin nhắn mới sẽ bị giữ (👀) và được gửi khi những thứ đó xong, thành một việc riêng, **với quyền của chính người gửi nó**. Một việc gõ trong terminal được tính là của chủ sở hữu: chỉ tin nhắn của chủ sở hữu (hoặc, trong chat riêng, của chính thành viên của chat đó) mới nhập vào nó. Tin nhắn của chính bạn không bao giờ phải chờ việc của chính bạn. Nếu việc mà chúng đang chờ bị dừng, hoặc bị chặn lại vì một công cụ bị cấm, các tin nhắn bị giữ sẽ bị bỏ (🤷): hãy gửi lại chúng.
- **Nghiêm nhất thắng.** Nếu tin nhắn từ nhiều người khác nhau vẫn rơi vào cùng một việc, việc đó chạy theo tổ hợp nghiêm nhất của mọi người trong đó. Một tin nhắn nhập vào một việc đang chạy không bao giờ nới rộng nó: phần còn lại của việc giữ quy tắc nghiêm nhất của mọi người đã nhập vào.
- **Lệnh slash từ các vai trò bị giới hạn.** Một vai trò không bỏ qua được quy tắc vai trò (`user`, `read_only`, các vai trò bạn định nghĩa mà không có `bypass_role_denies`, và một người gửi mà aipager không biết) chỉ được gửi các lệnh slash trên bàn phím Lệnh (của bạn, nếu `keyboard.json` thay đổi nó), `/compact` và việc đổi mô hình (`/model <name>`). Mọi thứ khác, dù được gõ, gửi bằng `/x1 /deliver ...`, làm chú thích của file, làm tin nhắn đầu tiên của `/new x1 /review ...` hay làm một mẫu, đều nhận “Lệnh đó chỉ dành cho quản trị viên.” và một 🤷, và không có gì tới Claude. Chủ sở hữu và quản trị viên gửi được mọi lệnh.
- **Vai trò bị giới hạn tránh xa thư mục home và các file chứa thông tin đăng nhập.** Một việc của `user` hoặc `read_only` không bao giờ ghi vào một thư mục phiên là thư mục home của bạn, `/` hay thư mục nằm trên nó (chỉ ghi vào scratchpad), và không bao giờ đọc các file chứa thông tin đăng nhập liệt kê ở [Vai trò](#roles).
- **Vai trò bị giới hạn ở lại trong thư mục gốc của mình.** Một việc của một vai trò không có `can_manage` không đọc gì trong thư mục home của bạn nằm ngoài [thư mục gốc](#root-folders) của chat, và không ghi gì vào một thư mục phiên nằm ngoài nó. Thư mục gốc là thư mục của chat mà tin nhắn của thành viên được gửi từ đó, bất kể nó đi tới phiên nào.
- **Một thành viên bị xóa mất quyền ngay lập tức, nhưng không mất việc đang chạy của họ.** Một người gửi mà aipager không còn biết (bị xóa trong lúc tin nhắn của họ đang chờ) chạy theo mức tối thiểu bị giới hạn. Sau một lần tải lại trực tiếp, các tin nhắn bị giữ của họ bị bỏ, và một việc của họ vẫn đang chạy thì tiếp tục chạy, bị giữ ở mức tối thiểu bị giới hạn cho tới khi xong; gửi `/stop` để kết thúc nó ngay (xem [Tải lại không cần khởi động lại](#live-reload)).
- **Tin nhắn từ một phiên Claude khác mang quyền của người gửi.** Một tin nhắn mà một phiên gửi cho phiên khác (`SendMessage` của Claude Code) chạy với quyền của việc đã gửi nó, như aipager đã xác minh, không bao giờ với quyền trước đó của phiên nhận. Một tin nhắn aipager không xác minh được sẽ chạy với quy tắc nghiêm nhất, không được thay đổi file và không được truy cập web (xem [bảo mật → tin nhắn giữa các phiên Claude](security.md#messages-between-claude-sessions)).
- **Một công cụ bị chặn làm dừng việc.** Khi một việc thử một công cụ mà quy tắc của nó cấm, việc dừng ngay với “🛑 x1 · Bị quy tắc an toàn chặn: ... (đã dừng)”, và mọi lần gọi công cụ sau đó trong việc này cũng bị từ chối.

## <a id="who-can-tap-what"></a>Ai được chạm vào đâu

Mỗi nút đều kiểm tra vai trò của người chạm nó, trong chat có nút đó, ngay lúc họ chạm. Vai trò của người đã gửi lệnh tạo ra nút không được tính. (Một số thẻ còn kiểm tra ai đã yêu cầu chúng: thẻ xác nhận và các thẻ của `/new`, bên dưới.)

- **Mọi thành viên đều được xem** (VIEW). Thành viên `read_only` có thể lật trang `/resume`, mở phần Đã kết thúc của `/status`, menu ⋮ của một phiên, chế độ hay diff của nó, xem `/settings`, và đóng một menu. Họ không thể thao tác: mọi nút khác trả lời “Vai trò của bạn không được làm việc đó trong chat này.”
- **Trả lời Claude cần `can_approve`** (APPROVE): Cho phép, Luôn cho phép, Từ chối, các phương án của một câu hỏi, Gửi, Tiếp tục và nút Trả lời trên thanh được ghim. `read_only` không có quyền này.
- **Thao tác trên một phiên cần `can_prompt`** (PROMPT): Dừng, Thử lại, Thu gọn, Gửi ngay, Kết thúc, Khởi động lại, Đổi tên, Xóa, Tiếp tục, đổi chế độ, ✍️ nói chuyện, Xóa hết, tùy chọn riêng của một phiên và các thẻ của `/new`. Một nút mà aipager không biết cũng cần quyền này.
- **Chuyển sang Toàn quyền cần quản trị viên** (MANAGE: `owner`, `admin`, hoặc một vai trò có `can_manage`, cùng quy tắc với `/mode full`): “Có, chuyển” và “Dừng và đổi chế độ” trên một thẻ chuyển sang Toàn quyền, và tiếp tục ở Toàn quyền. Chuyển qua lại giữa Hỏi và Auto thì ai thao tác được trên phiên cũng làm được. Tiếp tục một phiên đang ở Toàn quyền (gõ `/resume x1`, bộ chọn, Tiếp tục trên thẻ đặt tên của `/new`, hoặc Mini App) sẽ đưa nó trở lại ở Chế độ Auto với bất kỳ ai khác, và câu trả lời nói rõ: “♻️ Đã mở lại x1 ở Chế độ Auto: Chế độ Toàn quyền chỉ dành cho quản trị viên.”
- **Trong chat riêng của chính mình, một người có thể dùng Toàn quyền bất kể vai trò** (kể cả `user`; không phải `read_only`, vai trò không gửi tin nhắn; một vai trò tự tạo cần `can_prompt` và `can_approve`, vì Toàn quyền trả lời mọi yêu cầu cấp quyền của Claude Code). Mọi cách vào Toàn quyền ở trên đều dùng được cho họ ở đó: `/mode full`, thẻ chế độ, `/new !x1`, các thẻ của `/new`, `/resume` và Mini App. Chat riêng của chính họ là nơi duy nhất điều này áp dụng: trong một nhóm, và trong chat riêng của người khác, Toàn quyền vẫn cần quản trị viên. Các phiên mới của họ bắt đầu ở Chế độ Auto, như của mọi người, cho tới khi họ chọn Toàn quyền trong `/settings` → Phiên mới. Không có gì khác mà quản trị viên làm được mở ra cho họ (`/update`, nút Cài tính năng thoại và `/settings` của nhóm vẫn chỉ dành cho quản trị viên). Giao Toàn quyền theo cách này là an toàn: nó khởi động Claude Code với `--dangerously-skip-permissions`, cờ này chỉ bỏ qua các yêu cầu cấp quyền của riêng Claude Code. Hook PreToolUse của aipager vẫn chạy ở mọi lần gọi công cụ và áp quy tắc vai trò của thành viên (mức an toàn tối thiểu, giới hạn thư mục, quy tắc cấm, file chứa thông tin đăng nhập) y như ở Chế độ Hỏi và Chế độ Auto.
- **Cài đặt phần mềm cần quản trị viên** (UPDATE): các nút Cài và Khởi động lại của tính năng thoại, như của `/update`.
- **Đổi `/settings` của một nhóm cần quản trị viên.** Ai cũng xem được.
- **Một thẻ xác nhận thuộc về người đã yêu cầu nó.** Trong một nhóm, các thẻ xác nhận Kết thúc (`/kill`, ⋮ Kết thúc phiên), Khởi động lại, Xóa và đổi chế độ, các bộ chọn phiên mà `/kill`, `/restart`, `/delete`, `/mode`, `/stop`, `/rename` và `/diff` hiện ra, và thẻ “Tiếp tục x1 ở:” do một lần chạm Tiếp tục tạo ra (các nút chế độ của nó) chỉ trả lời người đã gửi lệnh hoặc đã chạm nút tạo ra chúng. Lần chạm của bất kỳ ai khác, kể cả Hủy, nhận “Đây là thẻ của @alice. Gửi /kill x2 để có thẻ của riêng bạn.” (với lệnh của thẻ đó) và không đổi gì. Bản thân thẻ `/mode` (cho biết phiên đang ở chế độ nào) là của mọi người: một lần chuyển chạm trên đó tạo ra một thẻ xác nhận thuộc về người chạm. Các thẻ của `/new` chỉ trả lời người đã gửi `/new`. Chủ của một thẻ được nhớ cho tới khi thẻ được dùng hoặc bị hủy, kể cả qua một lần aipager khởi động lại (4096 thẻ mới nhất). Thẻ đặt tên của `/new` và câu hỏi đặt tên của `/rename` là ngoại lệ: dù sao chúng cũng hết hạn sau 10 phút, nên sau một lần khởi động lại hãy gửi lại lệnh.
- **Một nút chỉ dùng được trong chat của chính phiên của nó.** Chạm ở bất kỳ chỗ nào khác thì nó trả lời “Nút này thuộc về một chat khác.” và không làm gì, và Tiếp tục luôn tiếp tục đúng phiên mà nút nêu tên, không bao giờ là một phiên cùng tên của chat khác.
- **Nút cũ không thay đổi gì.** Một nút sót lại từ một việc trước, hoặc từ trước khi phiên kết thúc rồi quay lại, không tác động lên những gì đang chạy bây giờ; nó nói rõ điều đó (xem [lệnh → nút cũ](commands.md#stale-buttons)).

## <a id="what-everyone-sees"></a>Mọi người thấy gì

**Câu trả lời cho Claude.** Mọi lần Cho phép, Từ chối và trả lời đều được đăng trong chat, dưới thẻ của phiên, kèm người đã làm:

```
✅ x1 · Đã cho phép · @alice · Bash: ls -la /tmp
🚫 x1 · Đã từ chối · @bob · WebFetch: https://example.com
✓ x1 · Đã trả lời · @carol · Which database? → Postgres
```

**Ai đã thay đổi một phiên.** Một lần chạm nút chỉ người chạm nhìn thấy, nên trong một nhóm mọi kết quả làm thay đổi một phiên đều nêu tên người đã làm:

```
⚠️ x1 · @bob đã dừng
⏹ @bob đã kết thúc x2
🔄 @alice đã khởi động lại [x1].
🗑️ @carol đã xóa [x3].
⚙️ @alice đã chuyển phiên sang 🤖 Chế độ Auto.
@alice đã thay đổi.          (dưới /settings)
```

Thẻ “x1 đã sẵn sàng” nêu tên người đã đổi chế độ hay mô hình của nó (“@bob đã đổi mô hình sang Opus.”). Trong chat riêng, những dòng kết quả này không nêu tên ai. Một thay đổi làm trong Mini App được đăng trong chat thành “... từ Mini App” (một lần Dừng từ đó cũng nêu tên người làm trên thẻ của phiên).

**Một công cụ bị một quy tắc chặn.** Một việc dừng lại với “🛑 x1 · Bị quy tắc an toàn chặn: Write nằm trong deny_tools của vai trò này (đã dừng)”. Nếu Claude Code xin quyền dùng một công cụ mà người có tin nhắn đã bắt đầu việc đang chạy không được dùng, bot tự trả lời Từ chối, không hiện yêu cầu nào, và đăng `⛔ x1 · Edit bị chặn với @bob (vai trò user)` kèm tóm tắt của lần gọi bên dưới, nêu tên người đó. Một việc được bắt đầu bởi tin nhắn của nhiều người sẽ bị chặn khi bất kỳ ai trong số họ không được dùng công cụ, và nêu tên người đó. Một việc gõ trong terminal là của bạn và không bao giờ bị quy tắc của thành viên chặn, trừ khi có một tin nhắn Telegram nhập vào: khi đó nó được kiểm tra theo quy tắc của người đã gửi tin nhắn gần nhất của phiên (danh sách riêng của nhóm khi không biết người đó). Khi aipager không biết việc là của ai (nó đã đang chạy lúc aipager khởi động lại), nó dùng quy tắc của người đã gửi tin nhắn gần nhất của phiên, và dùng danh sách riêng của nhóm khi cả điều đó cũng không biết.

**Trạng thái tin nhắn.** 👀 trên một tin nhắn nghĩa là nó đã được gửi tới phiên (hoặc đang được giữ cho tới khi gửi được); 👍 nghĩa là Claude đã nhận; 🤷 nghĩa là nó đã bị bỏ trước khi Claude nhận. `/whoami` hiện nhãn, vai trò và quy tắc của bạn trong chat này.

**Thanh trạng thái được ghim.** Nhóm có thanh trạng thái được ghim của riêng mình (xem [lệnh → thanh trạng thái được ghim](commands.md#the-pinned-status-bar)), chỉ liệt kê các phiên của nhóm này, nếu bot là quản trị viên được phép ghim. Không có quyền đó thì nhóm không có thanh trạng thái: tin nhắn bị xóa chứ không bị để lại trong lịch sử mà không được ghim.

**Bản ghi kiểm tra trên đĩa.** `~/.claude/aipager-audit.jsonl` ghi lại mọi quyết định kèm `user_id`, `username` và chat, để sau này bạn dựng lại được mỗi người đã làm gì. Nó luôn được viết bằng tiếng Anh, bất kể chat dùng ngôn ngữ nào.

**Ngôn ngữ.** Một nhóm có một ngôn ngữ cho tin nhắn, thẻ và bàn phím của chính aipager, do một quản trị viên đặt bằng `/language`; cho tới khi quản trị viên chọn, ngôn ngữ đó là tiếng Anh. Nó không bao giờ đi theo ứng dụng Telegram của ai. Những gì chỉ một người thấy, thông báo nhanh sau một lần chạm, được hiện bằng ngôn ngữ của chính người đó: lựa chọn của họ trong chat riêng với bot, nếu không thì ngôn ngữ của ứng dụng Telegram của họ với người tham gia sau bản phát hành này, nếu không nữa thì ngôn ngữ của nhóm. Chỉ chữ của aipager thay đổi; Claude trả lời bằng ngôn ngữ mà mỗi người viết cho nó. Xem [Ngôn ngữ](commands.md#languages).

## <a id="where-messages-go"></a>Tin nhắn đi đâu

- **Mỗi phiên thuộc về một chat, và mọi thứ về phiên đó đều đi tới chat ấy**: thẻ, câu trả lời, yêu cầu cấp quyền, file và thông báo của nó. Một tin nhắn trong một chat không bao giờ đi tới một phiên của chat khác, và tên của một phiên chỉ dùng được trong chat của chính nó: gõ tên phiên của một chat khác sẽ nhận câu trả lời “Không có phiên nào tên”. Một phiên được bắt đầu khi aipager chạy mà chưa có chat nào được thiết lập sẽ nhận chat của nó ngay khi `aipager config` thêm chat vào; khi một phiên chưa có chat, không chat nào liệt kê hay thao tác được trên nó.
- **Các phiên bắt đầu trong terminal** (`aipager session <name>`) thuộc về chat riêng của chủ sở hữu: chat riêng có thành viên mang vai trò `owner` (một vai trò có `bypass_safety`). Khi có nhiều chat riêng mà không chat nào của chủ sở hữu, đó là chat riêng đầu tiên trong danh sách; khi hoàn toàn không có chat riêng nào, đó là chat đầu tiên. Trên một bản cài có chat riêng, chúng không bao giờ đi tới một nhóm.
- **Việc nâng cấp lên supergroup được đi theo tự động** (xem [Khi Telegram nâng cấp nhóm](#when-telegram-upgrades-the-group)).

## <a id="aipageryaml-schema-team-parts"></a>Cấu trúc `aipager.yaml` (phần dành cho làm việc nhóm)

```yaml
schema_version: 3
bot_token: "…"

scopes:
  - kind: dm
    chat_id: 12345       # chat riêng của bạn: ID của nó là ID người dùng của bạn
    label: owner DM
    members:
      - id: 12345
        label: alice
        role: owner
  - kind: group
    chat_id: -100123456789
    label: team
    deny_tools: [Write, Edit]   # không bắt buộc: câu trả lời cho “Block file edits”
    root: /home/alice/aipager-users/group-team   # thư mục gốc của các thành viên
    members:
      - id: 12345        # ID người dùng Telegram (KHÔNG phải nhãn, KHÔNG phải ID chat)
        label: alice     # cách người đó được hiện trong chat (@alice)
        role: owner
      - id: 67890
        label: bob
        role: user
        deny_tools: [WebFetch]  # không bắt buộc, cho từng thành viên
      - id: 11111
        label: charlie
        role: read_only
```

Một scope chat riêng và các scope nhóm cùng tồn tại: bot phục vụ mọi chat được liệt kê, và các phiên của mỗi chat được giữ tách biệt. `aipager config` ghi file này; bạn không bao giờ cần sửa nó.

## <a id="how-rules-work"></a>Quy tắc hoạt động thế nào

`~/.config/aipager/policy.yaml` định nghĩa quy tắc cho từng vai trò. Kiểm tra nó bằng `aipager policy validate`. Các trường được hỗ trợ cho mỗi vai trò (ngoài những trường ở [Vai trò tự tạo](#custom-roles)):

- `deny_tools`: tên các công cụ bị cấm mà không hỏi (ví dụ `Write`, `Edit`, `Bash`, `WebFetch`).
- `allow_tools`: nếu không trống, đây là một danh sách cho phép: mọi thứ khác đều bị cấm với vai trò đó.
- `deny_bash_patterns`: các mẫu được so với đầu vào của `Bash`.
- `deny_paths_no_access` / `deny_paths_no_write`: quy tắc đường dẫn. Với `user`, `read_only` và mọi vai trò bạn định nghĩa mà không có `bypass_role_denies`, `deny_paths_no_access` bắt đầu bằng các file chứa thông tin đăng nhập liệt kê ở **user** phía trên; đặt nó sẽ thay danh sách đó, nên hãy chép những file bạn muốn giữ vào danh sách của bạn (một việc đang chạy có tin nhắn khác nhập vào lại bị giữ theo danh sách đó cho tới khi xong). Một quy tắc không bắt đầu bằng `/` hoặc `~` (`**/.env`) khớp ở mọi nơi, nên khi có một quy tắc như vậy, mọi `Grep`/`Glob` của một vai trò bị giới hạn đều bị từ chối (bất kỳ lần tìm nào cũng có thể đọc một file như vậy), và điều đó dừng việc đó lại.

`deny_tools` của một chat và `deny_tools` của một thành viên trong `aipager.yaml` được cộng vào của vai trò, và `allow_tools` của một thành viên được cộng vào danh sách cho phép của vai trò. Tất cả những thứ này chỉ áp dụng cho các vai trò không có `bypass_role_denies`: chủ sở hữu hay quản trị viên không bị ảnh hưởng.

Các file trong `~/.config/aipager/policy.d/*.yaml` được đọc sau `policy.yaml`, theo thứ tự tên, cùng định dạng: một trường vai trò đặt ở file sau sẽ thay giá trị trước đó, còn một danh sách `safety:` thì chỉ cộng thêm. `aipager policy validate` kiểm tra tất cả.

Bên dưới mọi vai trò là một **mức an toàn tối thiểu** (đường dẫn được bảo vệ và mẫu lệnh) mà chỉ `owner` được bỏ qua: quản trị viên cũng phải tuân theo. Nó gồm danh sách có sẵn của aipager cộng với phần `safety:` của `policy.yaml`, phần chỉ có thể cộng thêm vào:

```yaml
safety:
  deny_paths_no_access: [/srv/secrets/**]   # không đọc, ghi hay tìm
  deny_paths_no_write: [~/notes/**]         # đọc được, nhưng không ghi
  deny_bash_patterns: ['\bcurl\b']          # các lệnh Bash cần chặn
```

Mọi việc của mọi vai trò trừ `owner` đều nhận toàn bộ mức này, các việc mà aipager không biết là của ai cũng vậy; `aipager doctor --safety-check` liệt kê những gì đang có hiệu lực. Sau một lần tải lại trực tiếp, một thay đổi trong đó áp dụng cho các tin nhắn gửi sau lần tải lại; một việc đang chạy, hoặc một tin nhắn đang chờ trong hàng đợi, vẫn giữ danh sách cũ (hãy dừng việc đó để áp dụng thay đổi ngay). Một đường dẫn `deny_paths_no_access` ở đó không bắt đầu bằng `/` hoặc `~` sẽ khớp ở mọi nơi, nên khi có một đường dẫn như vậy, mọi `Grep`/`Glob` của mọi vai trò trừ `owner` đều bị từ chối (`aipager doctor` cảnh báo về điều này). Các đường dẫn được bảo vệ có hiệu lực với các công cụ file của Claude Code (`Read`, `Write`, `Edit`, `MultiEdit`, `NotebookEdit`, `LSP`, `Grep`, `Glob`); `Grep`/`Glob` của một vai trò bị giới hạn còn phải ở bên trong thư mục của phiên (một lần tìm `aipager/**` trong `~/.config` bị từ chối). `WebFetch` không được kiểm tra theo đường dẫn; `SendUserFile` (file Claude gửi vào chat) thì có, giống một lần Read, và thành viên chỉ gửi được từ thư mục gốc của chat ([bảo mật](security.md#files-claude-sends)). Một việc từ Telegram mà aipager không gán được cho người gửi nào nhận mức tối thiểu, mức này nghiêm ít nhất bằng `user`. Thử lại chạy dưới quyền của người chạm nếu họ đã gửi tin nhắn đang được thử lại, và theo mức tối thiểu nếu không. Các mẫu lệnh chỉ quan trọng với một vai trò có Bash, và ở đó chúng là một bộ lọc chứ không phải một bức tường; xem [bảo mật → áp dụng quy tắc trong chế độ làm việc nhóm](security.md#team-mode-enforcement).

## <a id="privacy-considerations"></a>Những điều cần cân nhắc về quyền riêng tư

- Bot chỉ lắng nghe **các chat đã được cấu hình**. Thêm bot vào một nhóm khác không kích hoạt nó ở đó: nó không bao giờ trả lời ở đó. Nó có ghi lại ai gọi nó ở đó (ID, username và tên của người đó, cùng ID và tiêu đề của nhóm) vào `~/.claude/aipager-pending-users.json`, để `aipager config` thêm được nhóm đó. Chỉ bạn đọc được file đó, và nó giữ 200 người mới nhất xuất hiện trong 30 ngày qua.
- Thành viên `read_only` **đọc được** tin nhắn, câu trả lời và đầu vào của công cụ. Họ không thao tác được, nhưng họ thấy mọi thứ trong nhóm. Nếu có việc phải giữ kín với ai đó, người đó không nên ở trong nhóm.
- Trên một bản cài có chat riêng, các phiên terminal và các phiên trong chat riêng của bạn không bao giờ hiện trong một nhóm (xem [Tin nhắn đi đâu](#where-messages-go)). Một phiên terminal mà một bản aipager cũ đã chuyển sang nhóm thì vẫn ở lại với nhóm: hãy kết thúc nó, xóa nó khỏi các phiên đã kết thúc của nhóm, rồi bắt đầu lại.
- `aipager.yaml` có quyền 0600 (chỉ chủ sở hữu). aipager không đặt quyền cho `policy.yaml`: hãy tự giữ nó ở chế độ chỉ chủ sở hữu (`chmod 600 ~/.config/aipager/policy.yaml`).
- Log kiểm tra (`~/.claude/aipager-audit.jsonl`) không có quyền riêng: nó riêng tư đúng bằng thư mục `~/.claude` của bạn.

## <a id="revoking-a-user"></a>Thu hồi quyền của một người

1. Chạy `aipager config` → Edit a member → chat đó → người đó → Remove member. Để lấy đi quyền gửi tin nhắn nhưng vẫn cho họ đọc, dùng Set role và chọn `read_only`. (Để thêm lại ai đó sau này: Edit a scope → nhóm đó → Add a member.) Với chat riêng của một người, hãy kết thúc các phiên của nó, rồi Edit a scope → chat riêng của họ → Remove this scope.
2. Trình thiết lập tải lại aipager trực tiếp; nó báo "Scopes reloaded live". Ngay lập tức, các tin nhắn bị giữ của họ bị bỏ (🤷 và một câu trả lời “Không gửi tới Claude”) và một việc của họ vẫn đang chạy bị giữ ở mức tối thiểu bị giới hạn cho tới khi xong. Gửi `/stop` để kết thúc việc đó ngay.
3. Xóa họ khỏi nhóm Telegram nữa. Chừng nào còn ở trong nhóm, họ vẫn đọc được mọi thứ được đăng ở đó: thẻ, câu trả lời, đầu vào của công cụ và yêu cầu cấp quyền.

Hãy xem trình thiết lập nói gì sau bước 1. Nếu nó bảo khởi động lại aipager, nghĩa là nó không liên lạc được với aipager: hãy khởi động lại (`aipager service stop`, rồi `aipager service start`), vì cho tới lúc đó danh sách thành viên cũ vẫn còn trong bộ nhớ. Nếu nó báo "Not applied", hãy sửa vấn đề mà nó nêu: cho tới lúc đó aipager vẫn giữ cấu hình cũ. Sau khi tự sửa `aipager.yaml` bằng tay, hãy tự gửi lệnh tải lại (xem [Tải lại không cần khởi động lại](#live-reload)).

## <a id="related-docs"></a>Tài liệu liên quan

- [Kiến trúc](architecture.md): cách tiến trình được tổ chức.
- [Lệnh của bot](commands.md): tài liệu về giao diện.
- [Mô hình bảo mật](security.md): ranh giới tin cậy, danh sách mối đe dọa.
- [Xử lý sự cố](troubleshooting.md): tài liệu về `aipager doctor`.
