<div align="center">

<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="96" height="96" alt="aipager"></a>

# aipager

<!-- i18n-source: README.md @ 0746c0a -->

<h3>Claude giờ chỉ là thêm một cuộc chat.</h3>

Các phiên [Claude Code](https://claude.com/claude-code) của bạn ngay trong Telegram, cạnh các chat với gia đình, bạn bè và nhóm của bạn.<br>
Chúng chạy trên chính máy tính của bạn. Ở đâu bạn cũng theo dõi được, trả lời được và giao việc tiếp theo cho chúng.

<p>
<a href="https://pypi.org/project/aipager/"><img alt="PyPI" src="https://img.shields.io/pypi/v/aipager?style=flat-square&label=pypi&labelColor=181c20&color=2aabee"></a>
<a href="https://pypi.org/project/aipager/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/aipager?style=flat-square&labelColor=181c20&color=2aabee"></a>
<a href="https://github.com/dev-aly3n/aipager/actions/workflows/test.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/dev-aly3n/aipager/test.yml?branch=main&style=flat-square&label=tests&labelColor=181c20"></a>
<img alt="Linux and macOS" src="https://img.shields.io/badge/runs%20on-Linux%20%7C%20macOS-2aabee?style=flat-square&labelColor=181c20">
<a href="../../../LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-2aabee?style=flat-square&labelColor=181c20"></a>
</p>

**[Trang web](https://aipager.run)** · **[Cài đặt](#install)** · **[Tài liệu](#documentation)** · **[Bảo mật](security.md)** · **[Nhật ký thay đổi](../../../CHANGELOG.md)**

**Đọc bằng ngôn ngữ khác:** [English](../../../README.md) · [Español](../es/README.md) · [Русский](../ru/README.md) · [Português (Brasil)](../pt-BR/README.md) · [简体中文](../zh-Hans/README.md) · [Français](../fr/README.md) · [Deutsch](../de/README.md) · [한국어](../ko/README.md) · [العربية](../ar/README.md) · [Bahasa Indonesia](../id/README.md) · [Українська](../uk/README.md) · [Italiano](../it/README.md) · [Türkçe](../tr/README.md) · [فارسی](../fa/README.md)

<sub>Trang này được dịch từ tiếng Anh; nếu hai bản khác nhau, bản tiếng Anh là bản đúng. ([English](../../../README.md))</sub>

<br>

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/hero.webp" width="100%" alt="Tin nhắn gõ trong Telegram tới được Claude Code trong terminal, và một thẻ trong chat tự cập nhật khi Claude làm việc">

<sub>Bạn viết trong Telegram. aipager gõ nó vào Claude Code trên máy tính của bạn, và một thẻ tự cập nhật khi việc đang chạy.</sub>

</div>

<br>

## <a id="why-aipager"></a>Vì sao chọn aipager

<table>
<tr>
<td width="33%" valign="top">

**📟 Một thẻ cập nhật liên tục cho mỗi việc**<br>
Claude đang làm gì lúc này: các công cụ nó chạy, ngữ cảnh, chi phí và số dòng đã đổi, rồi tới câu trả lời. Thẻ tự cập nhật nên chat luôn gọn gàng.

</td>
<td width="33%" valign="top">

**⚡ Nhắn cả khi nó đang làm**<br>
Tin nhắn của bạn được gõ vào phiên ngay lập tức, kể cả khi Claude đang làm việc, y như gõ trong terminal. 👀 nghĩa là đã gửi, 👍 nghĩa là Claude đã nhận.

</td>
<td width="33%" valign="top">

**✅ Trả lời bằng một chạm**<br>
Yêu cầu cấp quyền và câu hỏi của Claude đến kèm một nút cho mỗi câu trả lời. Nút còn sót lại từ một việc đã xong sẽ từ chối, thay vì tác động lên việc tiếp theo.

</td>
</tr>
<tr>
<td width="33%" valign="top">

**🛡️ Ba chế độ quyền**<br>
💬 Chế độ Hỏi, 🤖 Chế độ Auto (mặc định cho phiên mới) và 🔓 Chế độ Toàn quyền, đặt cho từng phiên và đổi bất cứ lúc nào bằng `/mode`.

</td>
<td width="33%" valign="top">

**👥 Làm cho cả nhóm**<br>
Thêm bot vào một nhóm. Mỗi tin nhắn chạy với vai trò của người gửi, thành viên có thể bị giới hạn trong thư mục riêng, và mọi lần chạm đều nằm trong log kiểm tra.

</td>
<td width="33%" valign="top">

**🔒 Không có ai ở giữa**<br>
Không máy chủ aipager, không tài khoản, không cổng mở. Token bot của bạn, máy tính của bạn, chat của bạn.

</td>
</tr>
</table>

## <a id="a-quick-tour"></a>Một vòng ngắn

### <a id="questions-come-to-you"></a>Câu hỏi tìm đến bạn

Khi Claude hỏi bạn điều gì hoặc cần cấp quyền, câu hỏi tới chat của bạn kèm một nút cho mỗi câu trả lời, và thanh được ghim cho biết điều gì đang chờ bạn. Lần chạm của bạn được gõ vào phiên như thể bạn đang ngồi trước bàn phím, và terminal hiện đúng câu trả lời đó.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/question.webp" width="100%" alt="Claude hỏi nên tăng thời gian chờ hay chờ cookie; câu trả lời chạm trong Telegram được chọn trong terminal">

### <a id="three-permission-modes"></a>Ba chế độ quyền

Mỗi phiên ở một trong ba chế độ, đổi bất cứ lúc nào bằng `/mode`, kể cả khi Claude đang làm việc.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/modes.webp" width="100%" alt="Cùng một việc ở Chế độ Hỏi, Chế độ Auto và Chế độ Toàn quyền">

| Chế độ | Điều gì xảy ra |
|---|---|
| 💬 **Chế độ Hỏi**<br>`/mode ask` | Claude hỏi trước mỗi thao tác. |
| 🤖 **Chế độ Auto**, mặc định<br>`/mode auto` | Bước kiểm tra an toàn của chính Claude Code chạy các thao tác an toàn và dừng các thao tác rủi ro để bạn quyết định. |
| 🔓 **Chế độ Toàn quyền**<br>`/mode full` | Mọi thứ chạy mà không có bất kỳ kiểm tra nào. Dành cho quản trị viên, và cho bạn trong chat riêng của chính bạn với bot. |

Chuyển giữa Chế độ Hỏi và Chế độ Auto diễn ra ngay tại chỗ, không khởi động lại. Vào hoặc ra khỏi Chế độ Toàn quyền sẽ khởi động lại phiên và giữ nguyên cuộc trò chuyện. Câu hỏi của Claude vẫn tới bạn ở mọi chế độ.

### <a id="your-team-in-one-group"></a>Nhóm của bạn, trong một nhóm chat

Thêm bot vào một nhóm và giao cho mỗi thành viên một vai trò: `owner`, `admin`, `user` hoặc `read_only`. Trong nhóm, bot chỉ phản hồi lệnh, câu trả lời cho tin nhắn của nó và lần nhắc đến bot (`@aipagerbot fix the tests`, `/web run the tests`), và mỗi tin nhắn chạy với vai trò của người gửi: công cụ mà vai trò đó không được dùng sẽ bị từ chối trước khi chạy. Thành viên `user` hoặc `read_only` chỉ làm việc trong thư mục gốc của riêng họ (mặc định `~/aipager-users/<label>`). Người khác cũng có thể có chat riêng của họ với bot.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/team.webp" width="100%" alt="Một nhóm chat của đội, nơi một lệnh bị từ chối vì vai trò của một thành viên trong khi các bài test vẫn qua">

> **⚠️ Quan trọng:** Thêm một thành viên là cho họ quyền chạy mã trên máy tính của bạn. Hãy đọc [mô hình tin cậy](groups.md) trước khi chia sẻ bot.

Để thiết lập, thêm bot vào nhóm với quyền quản trị viên (để nó ghim được thanh trạng thái của nhóm), chạy lại `aipager config` và chọn **Add a group scope**. Trình hướng dẫn sẽ thêm từng thành viên kèm vai trò, hỏi thư mục gốc của các thành viên và nạp lại cấu hình của aipager đang chạy.

### <a id="a-dashboard-inside-telegram"></a>Một bảng điều khiển ngay trong Telegram

`/app` mở Mini App: các phiên của bạn với Dừng, Kết thúc, Khởi động lại, Đổi tên và nút đổi chế độ, trình xem diff cho mỗi `Write` và `Edit`, phần cài đặt và, với chủ sở hữu, Báo cáo sự cố. Chính aipager phục vụ nó, nó bật sẵn theo mặc định, và mỗi yêu cầu đều được kiểm tra bằng chữ ký của Telegram. Nếu bạn ở nhiều hơn một chat, một hàng tên chat ở trên cùng giúp bạn chuyển qua lại. Tắt nó bằng `aipager miniapp disable`, hoặc trỏ nó về địa chỉ của riêng bạn ([cách làm](security.md#mini-app-tunnel)).

### <a id="fifteen-languages"></a>Mười lăm ngôn ngữ

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/languages.webp" width="420" align="right" alt="Một yêu cầu cấp quyền bằng tiếng Ba Tư, từ phải sang trái, với chữ số Ba Tư">

- Nói English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی và Tiếng Việt.
- Chọn một ngôn ngữ bằng `/language`, hoặc để chat riêng theo ngôn ngữ của ứng dụng Telegram của bạn.
- Tiếng Ba Tư và tiếng Ả Rập đọc từ phải sang trái, với chữ số riêng của chúng.
- Chỉ chữ của chính aipager thay đổi. Phiên của bạn thì không: Claude trả lời bằng ngôn ngữ bạn viết cho nó.
- README này và [tài liệu](#documentation) cũng đã được dịch.

<br clear="right">

### <a id="and-the-small-things"></a>Và những điều nhỏ

- **Nhiều phiên, một bot.** Tin nhắn thường sẽ tới phiên bạn nói chuyện gần nhất; trả lời một tin nhắn của một phiên để nói với đúng phiên đó, hoặc gửi `/x1 <message>`.
- **Không có gì bị lẫn.** Tin nhắn gửi khi đang có yêu cầu cấp quyền hoặc câu hỏi mở sẽ chờ tới khi bạn trả lời, nên không bao giờ bị đọc thành câu trả lời.
- **Ảnh, file và tin nhắn thoại.** Tin nhắn thoại được chuyển thành chữ ngay trên máy tính của bạn, với phần bổ sung tùy chọn `voice`.
- **Nó báo cho bạn.** Ngữ cảnh sắp đầy, cuộc trò chuyện được thu gọn, một phiên đã kết thúc và một phiên im lặng lâu.
- **Phiên sống qua mọi thứ.** Claude chạy bên trong `dtach`, nên vẫn tiếp tục làm việc khi bạn đóng terminal, mất kết nối SSH hoặc khởi động lại aipager.

## <a id="how-it-works"></a>Cách hoạt động

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/how-it-works.webp" width="100%" alt="Claude Code, hook của nó, aipager, Telegram và các chat của bạn, xếp thành hàng">

Claude Code chạy trong một terminal thật, bên trong `dtach`, nên không ai phải giữ cửa sổ mở. Mỗi bước kích hoạt một [hook](hooks.md) gửi một datagram tới aipager qua socket cục bộ, và aipager cập nhật một tin nhắn trong chat của bạn. Câu trả lời của bạn quay về theo đúng đường đó: một quyết định gửi cho hook đang giữ lần gọi công cụ, hoặc các phím gõ vào phiên. Terminal vẫn là nơi mọi thứ thật sự diễn ra. Xem thêm ở [kiến trúc](architecture.md).

## <a id="install"></a>Cài đặt

Linux hoặc macOS, mọi kiến trúc. Bạn cần Claude Code đã đăng nhập trên máy tính này, và một bot Telegram: tạo một bot với [@BotFather](https://t.me/BotFather) rồi bấm Start trong đó.

```sh
curl -fsSL https://aipager.run/install | sh   # uv, pipx hoặc Homebrew: cái nào bạn có
aipager config                                # token bot, chat của bạn, hook của Claude Code
aipager service install                       # giữ aipager chạy cả khi bạn đăng xuất
aipager session api                           # Claude Code trong thư mục này, và trong chat của bạn
```

Trình cài đặt dùng `uv` nếu bạn có, nếu không thì `pipx`, nếu không nữa thì (trên macOS) Homebrew, và cài `uv` khi không có cái nào. `dtach` đi kèm aipager (gói [`dtach-bin`](https://pypi.org/project/dtach-bin/)), nên không cần cài gì thêm. `aipager config` là một trình hướng dẫn: nó kiểm tra token bot và chat của bạn, rồi tự thêm các hook của aipager vào `~/.claude/settings.json`. Bạn không bao giờ phải sửa file bằng tay.

<details>
<summary><b>Để một agent lập trình cài giúp bạn</b></summary>

<br>

Bạn có Claude Code, hoặc bất kỳ agent nào có shell? Tạo bot, bấm Start trong đó, và lưu token bot vào một file mà agent không bao giờ phải đọc (dán token, nhấn Enter, rồi Ctrl-D):

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

Sau đó đưa cho agent prompt này:

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

Nếu bạn không biết ID người dùng Telegram của mình, cứ nói vậy; agent có thể tự tìm. Hướng dẫn nó làm theo, gồm cả cách giao token an toàn, là [Cài đặt bằng agent](install-with-an-agent.md). Với script, `aipager setup --token-file FILE --chat-id ID` thiết lập aipager mà không hỏi gì cả ([lệnh setup](commands.md#command-line-aipager-setup-for-coding-agents)).

</details>

### <a id="other-ways-to-install"></a>Các cách cài đặt khác

```sh
uv tool install aipager                        # uv: nên dùng trên macOS, mang theo Python riêng
pipx install aipager                           # pipx
brew install dev-aly3n/tap/aipager             # Homebrew, trên macOS và Linuxbrew (xem ghi chú bên dưới)
nix profile install github:dev-aly3n/aipager   # Nix: không kèm claude (xem bên dưới)
snap install aipager                           # Snap: cô lập chặt, có sẵn claude, thư mục làm việc nằm trong ~/
```

Hoặc chạy nó trong [Docker](#docker), với Python, Node, `claude` và `dtach` có sẵn bên trong.

<details>
<summary><b>Homebrew trên macOS 26 (Tahoe)</b></summary>

<br>

Formula hoạt động khi bottle `python@3.12` của Homebrew và Command Line Tools của Xcode khớp nhau, nhưng thỉnh thoảng hai thứ này lệch nhau. Trên macOS 26, cài đặt thất bại với lỗi ký hiệu `pyexpat _XML_SetAllocTrackerActivationThreshold` ([issue ở upstream](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). Cập nhật Xcode và Command Line Tools thường sửa được, nhưng với `uv tool install aipager` thì lỗi này không hề xảy ra. Formula lấy `dtach` từ Homebrew và cài aipager vào một môi trường Python do Homebrew quản lý.

</details>

### <a id="docker"></a>Docker

Một image tự đủ với Python, Node, `claude` và `dtach`, hợp cho VPS, NAS hoặc Raspberry Pi khi bạn không muốn cài Python hay Node trên máy tính của mình. Hỗ trợ nhiều kiến trúc (amd64, arm64).

```sh
# 1. Chạy trình hướng dẫn thiết lập một lần (tương tác)
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. Khởi động aipager (chạy nền, tự khởi động lại)
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

Gắn (mount) các thư mục bạn muốn Claude sửa vào dưới `/workspace`. Mount `~/.claude` mang theo thông tin đăng nhập Claude và lịch sử trò chuyện của bạn: chạy `claude` một lần trên máy tính (ngoài container) để đăng nhập, hoặc `docker exec -it aipager claude` để đăng nhập bên trong container.

Tag: `latest`, `1.3`, `1.3.10` (một cho phiên bản chính xác và một cho phiên bản minor).

### <a id="nix"></a>Nix

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

Build aipager từ mã nguồn với nixpkgs đã ghim. `dtach` do Nix cung cấp; `claude` thì không, nên hãy cài riêng (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`, hoặc theo tài liệu của Anthropic).

<details>
<summary><b>NixOS và Home Manager</b></summary>

<br>

Thêm aipager làm một input của flake và đưa gói của nó vào `environment.systemPackages`:

```nix
{
  inputs.aipager.url = "github:dev-aly3n/aipager";

  outputs = { self, nixpkgs, aipager, ... }: {
    nixosConfigurations.myhost = nixpkgs.lib.nixosSystem {
      modules = [({ pkgs, ... }: {
        environment.systemPackages = [
          aipager.packages.${pkgs.system}.default
        ];
      })];
    };
  };
}
```

Sau đó `aipager service install` sẽ tạo một unit người dùng của systemd.

</details>

## <a id="everyday-use"></a>Dùng hằng ngày

### <a id="in-telegram"></a>Trong Telegram

| Gửi | Để |
|---|---|
| `/status` | Xem các phiên của chat này, mỗi phiên kèm trạng thái và các nút. |
| `/new x1` | Bắt đầu một phiên tên x1. `/new x1 fix the tests` còn gửi luôn tin nhắn đầu tiên. |
| `/x1` | Đặt x1 làm phiên nhận tin nhắn của bạn. `/x1 <message>` gửi thẳng một tin nhắn tới nó. |
| `/stop` | Ngắt việc đang chạy và bỏ những gì đang xếp hàng. |
| `/mode` | Chuyển giữa Chế độ Hỏi, Chế độ Auto và Chế độ Toàn quyền. |
| `/app` | Mở Mini App. |
| `/language` | Chọn ngôn ngữ aipager dùng trong chat này. |
| `/update` | Cập nhật aipager và Claude Code, rồi khởi động lại tại chỗ (quản trị viên). |
| `/help` | Hướng dẫn ngắn theo từng việc. |

Mọi lệnh, nút và cài đặt đều có trong [Lệnh của bot](commands.md).

### <a id="on-the-command-line"></a>Trên dòng lệnh

```sh
aipager start                             # chính aipager, chạy ở tiền cảnh
aipager session dev                       # bắt đầu ở đây một phiên tên dev, hoặc kết nối lại với nó
aipager session dev --continue            # tiếp tục cuộc trò chuyện Claude gần nhất trong thư mục này
aipager session dev -- --resume abc1234   # mọi tùy chọn khác của claude được chuyển nguyên vẹn
aipager doctor                            # kiểm tra bản cài đặt
```

Để rời một phiên mà không dừng Claude, hãy đóng terminal hoặc ô tmux: phiên vẫn chạy tiếp, và cùng lệnh đó sẽ kết nối bạn lại.

`aipager service install` giữ aipager chạy nền: một unit người dùng của systemd trên Linux (`~/.config/systemd/user/aipager.service`) hoặc một agent của launchd trên macOS (`~/Library/LaunchAgents/com.aipager.daemon.plist`). Cách nào thì aipager cũng chạy trong thư mục home của bạn, nơi một phiên mới bắt đầu nếu bạn không chọn thư mục khác. `aipager service start`, `stop`, `status`, `logs` và `uninstall` dùng để quản lý nó.

## <a id="security"></a>Bảo mật

- **Không có máy chủ aipager.** aipager nói chuyện với Bot API của Telegram bằng chính token bot của bạn. Không có tài khoản aipager, cũng không có dịch vụ aipager.
- **Không có cổng mở.** aipager chỉ lắng nghe trên loopback. Mini App đi ra ngoài qua một tunnel Cloudflare, nơi mọi yêu cầu phải mang chữ ký của Telegram, và `aipager miniapp disable` sẽ tắt nó.
- **Mọi câu trả lời đều được ghi lại.** Mỗi lần Cho phép, Từ chối và mỗi câu trả lời được ghi thêm vào `~/.claude/aipager-audit.jsonl`, kèm người đã chạm trong nhóm.
- **Claude Code vẫn là bên quyết định.** Cài đặt Claude Code của bạn quy định điều gì cần hỏi. aipager chỉ chuyển câu hỏi đi và không bao giờ nâng quyền: không sudo, mọi thứ chạy bằng tài khoản người dùng của bạn.

Toàn cảnh, gồm cả những gì bảo vệ thông tin đăng nhập Claude của bạn, có trong [mô hình bảo mật](security.md).

## <a id="faq"></a>Câu hỏi thường gặp

<details>
<summary><b>Khác gì so với ứng dụng di động của Claude?</b></summary>

<br>

Ứng dụng của Claude nói chuyện với trợ lý do Anthropic lưu trữ. aipager kết nối bạn với một phiên Claude Code chạy trên chính máy tính của bạn: repo của bạn, công cụ của bạn, môi trường của bạn.

</details>

<details>
<summary><b>Mã của tôi có rời khỏi máy tính không?</b></summary>

<br>

aipager chỉ gửi tới Telegram những gì bạn thấy trong chat của mình, bằng chính token bot của bạn; không có máy chủ aipager nào ở giữa. Còn Claude Code thì vẫn nói chuyện với Anthropic như mọi khi.

</details>

<details>
<summary><b>dtach là gì?</b></summary>

<br>

Một chương trình nhỏ giúp tách terminal ra. Nó giữ Claude Code tiếp tục chạy khi terminal đóng hoặc SSH bị ngắt, nên câu trả lời bạn gửi một giờ sau vẫn tới được một phiên đang chạy. Nó đi kèm aipager.

</details>

<details>
<summary><b>Nếu Claude đi chệch hướng thì sao?</b></summary>

<br>

Các yêu cầu chờ bạn chạm, `/stop` ngắt việc đang chạy và bỏ mọi thứ đang xếp hàng, `/kill` kết thúc phiên, và nút của một việc đã xong sẽ từ chối.

</details>

<details>
<summary><b>Cập nhật thế nào?</b></summary>

<br>

Gửi `/update`: nó kiểm tra aipager và Claude Code rồi cài bản mới, và 🔄 Khởi động lại ngay sẽ đưa aipager chạy lại, dù có chạy dưới dạng dịch vụ hay không. Các phiên của bạn vẫn chạy trong lúc đó. Trên dòng lệnh, `aipager update` cập nhật qua trình cài đặt đã cài aipager.

</details>

<details>
<summary><b>Tôi dùng Bedrock hoặc Vertex. Các nút chọn mô hình có dùng được không?</b></summary>

<br>

Menu **Mô hình** trên bàn phím và Mini App đưa ra các bí danh theo họ của Claude Code (`sonnet`, `opus`, `haiku`, `fable`, `opusplan`) và các mô hình cố định như `claude-opus-5-5` và `claude-sonnet-5-5`. Trên API của Anthropic, một bí danh trỏ tới mô hình mới nhất trong họ của nó. Trên Bedrock và Vertex, cùng bí danh đó có thể trỏ tới một bản cũ hơn, tùy vào những gì nhà cung cấp của bạn có; hãy gửi `/model <full-id>` để chọn đúng một mô hình.

</details>

<details>
<summary><b>Tôi dùng khóa API, gateway hoặc một nhà cung cấp khác. aipager có chạy được không?</b></summary>

<br>

Có. aipager chạy Claude Code đúng như bạn đã thiết lập, và trả lời các câu hỏi Claude Code đặt ra lúc khởi động (thư mục, khóa API, các màn hình chạy lần đầu), để phiên bắt đầu từ Telegram không bị dừng ở đó. Đặt thiết lập trong khối `env` của `~/.claude/settings.json`, hoặc trong `~/.config/aipager/daemon.env` (mỗi dòng một `KEY=value`, không có dấu ngoặc kép). Các nút Opus, Sonnet và Haiku theo `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL` và `ANTHROPIC_DEFAULT_HAIKU_MODEL`, còn mô hình khác thì gõ tên vào là được. Anthropic chỉ hỗ trợ Claude Code với các mô hình Claude. Xem [Xử lý sự cố](troubleshooting.md#using-an-api-key-a-gateway-or-another-provider).

</details>

<details>
<summary><b>Tốn bao nhiêu tiền?</b></summary>

<br>

Không tốn gì. aipager dùng giấy phép MIT, bot là của bạn, và Claude Code vẫn dùng gói hoặc khóa mà nó đang dùng.

</details>

## <a id="documentation"></a>Tài liệu

| Trang | Nội dung |
|---|---|
| [Lệnh của bot](commands.md) | Mọi lệnh, nút, cài đặt và tùy chọn dòng lệnh |
| [Chế độ làm việc nhóm và nhóm](groups.md) | Vai trò, thư mục gốc, ai được chạm vào gì, mô hình tin cậy |
| [Mô hình bảo mật](security.md) | Ranh giới tin cậy, bí mật, log kiểm tra, mạng |
| [Sự kiện hook](hooks.md) | Mỗi hook của Claude Code làm gì trong aipager |
| [Kiến trúc](architecture.md) | Các phần của aipager, file và socket của nó |
| [Xử lý sự cố](troubleshooting.md) | `aipager doctor` và cách sửa các lỗi thường gặp |
| [Báo cáo sự cố](problem-reports.md) | Một báo cáo chứa gì, và khi nào aipager đề nghị gửi nó |
| [Cài đặt bằng agent](install-with-an-agent.md) | Hướng dẫn mà agent lập trình làm theo |

## <a id="contributing"></a>Đóng góp

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach` đi kèm phụ thuộc `dtach-bin`, nên không cần gì thêm. Hãy chạy test với giới hạn bộ nhớ như trên (trên macOS thì không có `systemd-run`): nếu không, một bài test mất kiểm soát có thể chiếm hết bộ nhớ của máy. Quy trình phát hành nằm trong [CONTRIBUTING.md](../../../CONTRIBUTING.md).

## <a id="license"></a>Giấy phép

MIT. Xem [LICENSE](../../../LICENSE).

<br>

<div align="center">
<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="32" height="32" alt=""></a>
<br>
<sub><a href="https://aipager.run">aipager.run</a></sub>
</div>
