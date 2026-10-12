# aipager

<!-- i18n-source: README.md @ 6fc084f -->

> Trang này được dịch từ tiếng Anh; nếu hai bản khác nhau, bản tiếng Anh là bản đúng. ([English](../../../README.md))

[![PyPI](https://img.shields.io/pypi/v/aipager?label=pypi&color=3775A9)](https://pypi.org/project/aipager/)
[![Python](https://img.shields.io/pypi/pyversions/aipager?color=3775A9)](https://pypi.org/project/aipager/)
[![License](https://img.shields.io/badge/license-MIT-black)](../../../LICENSE)

Điều khiển từ xa qua Telegram cho các phiên [Claude Code](https://claude.com/claude-code). Claude chạy trên máy tính của bạn trong một terminal tách rời (`dtach`), còn bạn dùng nó từ điện thoại: đọc câu trả lời của Claude, gửi tin nhắn cho nó, trả lời các yêu cầu cấp quyền và câu hỏi của nó, và chuyển qua lại giữa các phiên, mà không cần giữ kết nối SSH luôn mở.

[aipager.run](https://aipager.run) · [Tài liệu](#docs) · [Nhật ký thay đổi](../../../CHANGELOG.md) · [Issue trên GitHub](https://github.com/dev-aly3n/aipager/issues)

## <a id="install"></a>Cài đặt

Bạn có một agent lập trình (Claude Code, hoặc bất kỳ agent nào dùng được shell)? Tạo một bot với [@BotFather](https://t.me/BotFather), chạm Start trong bot đó, và chắc chắn rằng Claude Code trên máy tính này đã đăng nhập vào tài khoản của bạn. Lưu token của bot vào một file mà agent không bao giờ cần đọc (dán token, nhấn Enter, rồi Ctrl-D):

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

Sau đó đưa cho agent tin nhắn này:

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

Nếu bạn không biết ID Telegram của mình, cứ nói với agent; nó có thể tìm ra.

Hướng dẫn mà agent làm theo, kể cả cách đưa token cho nó một cách an toàn, nằm ở [Cài đặt bằng agent](install-with-an-agent.md). Để tự cài, hãy đọc tiếp.

Linux hoặc macOS, kiến trúc nào cũng được. `dtach` được cài tự động qua phụ thuộc [`dtach-bin`](https://pypi.org/project/dtach-bin/): không cần gói hệ thống riêng.

### <a id="one-line-install-recommended"></a>Cài bằng một dòng (khuyên dùng)

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

Nó dùng `uv` nếu có, nếu không thì `pipx`, nếu không nữa thì (chỉ trên macOS) Homebrew. Nếu không có cái nào, nó cài `uv` trước (trình quản lý công cụ Python của Astral) rồi cài aipager bằng nó.

### <a id="uv-recommended-on-macos"></a>uv (khuyên dùng trên macOS)

```sh
uv tool install aipager     # nếu đã có uv
```

Hoặc, để cài uv trước:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install aipager
```

uv mang theo trình thông dịch Python riêng, nên chạy được trên mọi phiên bản macOS hay Linux, bất kể Python của hệ thống đang ra sao; nhờ vậy bạn tránh được xung đột giữa Python của Homebrew và Xcode được mô tả ở phần Homebrew bên dưới.

### <a id="pipx"></a>pipx

```sh
pipx install aipager
```

### <a id="homebrew-tap-macos-linuxbrew"></a>Homebrew tap (macOS, Linuxbrew)

> **Lưu ý:** trên macOS, cách được khuyên dùng là `uv tool install aipager`. Công thức brew chạy được khi gói `python@3.12` của Homebrew và Xcode / Command Line Tools của bạn khớp nhau, nhưng thỉnh thoảng chúng lệch nhau; lần gần nhất là trên **macOS Tahoe (26.x)**, việc cài đặt dừng với lỗi ký hiệu `pyexpat _XML_SetAllocTrackerActivationThreshold` ([issue upstream](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). Cập nhật Xcode và Command Line Tools thường giải quyết được, nhưng dùng uv thì đơn giản hơn.

```sh
brew install dev-aly3n/tap/aipager
```

Lấy `dtach` từ công thức chuẩn của Homebrew và cài aipager vào một venv Python do Homebrew quản lý.

### <a id="docker"></a>Docker

Một image dựng sẵn đã có python, node, `claude` và `dtach`; tiện cho VPS, NAS hay Raspberry Pi, nơi bạn không muốn cài Python hay Node vào hệ thống. Hỗ trợ nhiều kiến trúc (amd64, arm64).

```sh
# 1. Chạy trình thiết lập một lần (tương tác)
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. Chạy aipager (nền, tự khởi động lại)
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

Gắn (mount) các thư mục bạn muốn Claude sửa vào `/workspace`. Gắn `~/.claude` sẽ mang theo thông tin đăng nhập Claude và lịch sử cuộc trò chuyện của bạn; để đăng nhập, chạy `claude` một lần trên máy, hoặc `docker exec -it aipager claude` để đăng nhập bên trong container.

Tag: `latest`, `1.0`, `1.0.1` (một cho phiên bản chính xác và một cho phiên bản minor).

### <a id="nix-flake"></a>Nix flake

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

Build aipager từ mã nguồn với các phụ thuộc được ghim từ nixpkgs. `dtach` do Nix cung cấp; `claude` thì **không**: hãy cài riêng (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`, hoặc theo tài liệu của Anthropic).

Với cấu hình NixOS hay Home Manager kiểu khai báo, thêm aipager làm một input của flake và lấy gói của nó vào `environment.systemPackages`:

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

Sau đó `aipager service install` sẽ thiết lập một dịch vụ người dùng của systemd.

### <a id="snap"></a>Snap

```sh
snap install aipager
```

Một snap cô lập chặt chẽ có sẵn python, node, `claude`, `dtach` và aipager. Do sandbox của snap, các thư mục làm việc phải nằm trong `~/` (ví dụ `~/projects/foo`). File manifest nằm trong [`packaging/snap/`](../../../packaging/snap/).

## <a id="configure"></a>Thiết lập

```sh
aipager config
```

Một trình thiết lập tương tác: hỏi token bot Telegram của bạn (từ [@BotFather](https://t.me/BotFather)) và ID chat, kiểm tra cả hai, rồi tự thêm các hook cần thiết vào `~/.claude/settings.json`. Bạn không bao giờ phải tự sửa file.

Bạn đang thiết lập aipager cho người khác từ một script, hoặc qua một agent lập trình? `aipager setup --token-file FILE --chat-id ID` làm điều tương tự mà không hỏi gì (xem [lệnh setup](commands.md#command-line-aipager-setup-for-coding-agents) và [hướng dẫn cho agent](install-with-an-agent.md)).

## <a id="run"></a>Chạy

```sh
aipager start
```

aipager chạy ở tiền cảnh. Bắt đầu một phiên Claude ở terminal khác:

```sh
aipager session dev
```

Lệnh này tạo một phiên dtach tên `claude-dev` chạy Claude Code (hoặc kết nối vào phiên đó nếu đã có). aipager phát hiện nó trong vài giây và Telegram bắt đầu hiển thị nó. Bạn cũng có thể bắt đầu phiên từ Telegram: gửi `/new dev` cho bot. Để kết nối lại sau, chạy lại đúng lệnh đó; để thoát mà không dừng Claude, đóng terminal hoặc ô tmux (phiên vẫn tiếp tục chạy).

Nếu phiên dtach đã đóng (ví dụ máy tính đã khởi động lại) và bạn muốn tiếp tục cuộc trò chuyện của Claude từ ổ đĩa, thêm `--continue`:

```sh
aipager session dev --continue    # cuộc trò chuyện claude gần nhất trong thư mục này
```

Bạn cũng có thể thêm `--resume <session-id>` (hoặc bất kỳ cờ claude nào khác) ở cuối:

```sh
aipager session dev -- --resume abc1234
```

### <a id="run-as-a-service-survives-logout"></a>Chạy như một dịch vụ (vẫn chạy sau khi đăng xuất)

```sh
aipager service install
```

Trên Linux, lệnh này ghi một dịch vụ người dùng systemd vào `~/.config/systemd/user/aipager.service` rồi khởi động nó. Trên macOS, nó ghi một plist launchd vào `~/Library/LaunchAgents/com.aipager.daemon.plist` rồi khởi động nó. Các lệnh con: `start`, `stop`, `status`, `logs`, `uninstall`.

## <a id="team-mode-groups-and-other-people"></a>Chế độ làm việc nhóm (nhóm và người khác)

Mặc định aipager là một bot cá nhân: bạn và bot trong một chat riêng. Để chia sẻ, hãy thêm một nhóm Telegram, chat riêng của người khác với bot, hoặc cả hai. Với một nhóm: thêm bot vào nhóm (làm quản trị viên, để bot có thể ghim thanh trạng thái của nhóm), chạy lại `aipager config` và chọn **Add a group scope**. Trình thiết lập đề xuất bạn (chủ sở hữu chat riêng của chính bạn) làm chủ sở hữu nhóm, thêm từng thành viên với một vai trò (`owner`, `admin`, `user` hoặc `read_only`), hỏi thư mục gốc của các thành viên, rồi tải lại cài đặt của aipager mà không cần khởi động lại. Trong nhóm, bot chỉ phản hồi lệnh, tin nhắn trả lời tin của nó và tin nhắn nhắc đến nó (`@aipagerbot fix the tests`, `/jim run the tests`), và mỗi tin nhắn chạy với vai trò của người gửi. Một thành viên `user` hoặc `read_only` chỉ bắt đầu và giữ phiên trong thư mục gốc của chat mình (mặc định `~/aipager-users/<label>`). **Thêm một thành viên là cho người đó quyền chạy mã trên máy tính của bạn**: toàn bộ mô hình tin cậy được mô tả ở [Chế độ làm việc nhóm và nhóm](groups.md).

## <a id="what-it-does"></a>Nó làm được gì

- Cho bạn thấy trên Telegram mỗi phiên Claude Code đang làm gì: đang làm việc hay sẵn sàng, đang dùng công cụ nào, % ngữ cảnh, chi phí và số dòng đã đổi, trên một thẻ cho mỗi việc, rồi tới câu trả lời của Claude
- Gửi tin nhắn của bạn tới Claude **ngay lập tức**, kể cả khi nó đang làm việc: gửi vài tin, Claude sẽ tự xếp hàng chúng, giống như khi gõ trong terminal. 👀 nghĩa là đã gửi, 👍 nghĩa là Claude đã nhận
- Giữ lại tin nhắn khi đang có yêu cầu cấp quyền hoặc câu hỏi mở, để tin nhắn đó không bao giờ bị đọc thành câu trả lời cho nó, rồi gửi đi khi bạn đã trả lời
- Hiện các yêu cầu cấp quyền và câu hỏi của Claude kèm nút bấm (Cho phép, Từ chối, Luôn cho phép, một nút cho mỗi phương án); một nút còn sót lại từ việc đã xong sẽ từ chối, thay vì tác động lên việc đang chạy
- Ba chế độ quyền cho mỗi phiên, đổi bằng `/mode`: 💬 Chế độ Hỏi (Claude hỏi trước mỗi thao tác), 🤖 Chế độ Auto, mặc định (bước kiểm tra an toàn của chính Claude Code chạy các thao tác an toàn và dừng các thao tác rủi ro để bạn quyết định), và 🔓 Chế độ Toàn quyền (mọi thứ chạy mà không có kiểm tra nào; cho quản trị viên và cho bạn trong chat riêng của chính bạn)
- Bắt đầu phiên từ chat: `/new x1` bắt đầu một phiên ngay (`/new x1 fix the tests` còn gửi luôn tin nhắn đầu tiên), với chế độ, mô hình và thư mục lấy từ `/settings` cùng các nút để đổi chúng
- Nhận ảnh, file và tin nhắn thoại (tin nhắn thoại được chuyển thành chữ ngay trên máy tính của bạn, với gói tính năng thoại)
- Báo cho bạn khi ngữ cảnh gần đầy, khi cuộc trò chuyện được thu gọn, khi một phiên kết thúc, và khi một phiên im lặng quá lâu
- Chạy nhiều phiên cùng lúc qua một bot; tùy chọn chế độ làm việc nhóm với vai trò và quy tắc cho từng công cụ ([Chế độ làm việc nhóm và nhóm](groups.md))
- Bot quan sát chỉ đọc, tùy chọn
- Nói được English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی và Tiếng Việt: chỉ là chữ của chính aipager, không bao giờ là các phiên của bạn hay câu trả lời của Claude ([Ngôn ngữ](commands.md#languages)). README này và tài liệu cũng đã được dịch

### <a id="mini-app"></a>Mini App

`/app` mở một bảng điều khiển ngay trong Telegram: các phiên của bạn với Dừng, Kết thúc, Khởi động lại, Đổi tên và đổi chế độ, xem diff cho các thay đổi `Write`/`Edit`, cài đặt, và với chủ sở hữu thì có “Báo cáo sự cố”. Nó do chính aipager cung cấp và **bật sẵn mặc định**; mọi yêu cầu đều được kiểm tra bằng chữ ký `initData` của Telegram. Nếu bạn ở trong nhiều chat (chat riêng của bạn và một nhóm), một hàng tên chat ở trên cùng giúp chuyển qua lại giữa chúng. Quản lý bằng `aipager miniapp enable|disable|status`, hoặc trỏ nó tới địa chỉ của riêng bạn thay cho tunnel được quản lý: xem [Mô hình bảo mật](security.md#mini-app-tunnel).

### <a id="note-on-model-buttons-bedrock--vertex-users"></a>Về các nút mô hình (người dùng Bedrock và Vertex)

Menu con **Mô hình** trên bàn phím cố định (và bộ chọn mô hình trong Mini App) đưa ra các bí danh theo dòng của Claude Code, `sonnet`, `opus`, `haiku`, `fable` và `opusplan`, cùng các mô hình được ghim như `claude-opus-5-5` và `claude-sonnet-5-5` (danh sách đầy đủ ở [Bàn phím cố định](commands.md#persistent-keyboard)). Trên API của Anthropic, một bí danh luôn trỏ tới mô hình mới nhất trong dòng của nó. Trên **Bedrock** và **Vertex**, cùng các bí danh đó có thể trỏ tới phiên bản cũ hơn, tùy những gì nhà cung cấp của bạn có. Nếu bạn dùng các dịch vụ đó và cần một mô hình cụ thể, hãy gửi `/model <full-id>` từ chat.

## <a id="developing-locally"></a>Phát triển trên máy của bạn

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach` đi kèm phụ thuộc `dtach-bin`, không cần gì thêm. Hãy chạy test với giới hạn bộ nhớ như trên (trên macOS thì bỏ `systemd-run`): nếu không, một test chạy mất kiểm soát có thể ngốn hết bộ nhớ của máy.

Quy trình phát hành được mô tả trong [CONTRIBUTING.md](../../../CONTRIBUTING.md).

## <a id="license"></a>Giấy phép

MIT; toàn văn trong [LICENSE](../../../LICENSE).

## <a id="docs"></a>Tài liệu

| Trang | Nội dung |
|---|---|
| [Cài đặt bằng agent](install-with-an-agent.md) | Các bước một agent lập trình làm theo để cài và thiết lập aipager cho người của nó |
| [Kiến trúc](architecture.md) | Chương trình được dựng thế nào, sơ đồ các phần, file và socket nằm ở đâu |
| [Sự kiện hook](hooks.md) | Mọi sự kiện aipager nhận từ Claude Code, kèm các trường |
| [Lệnh của bot](commands.md) | Lệnh, nút, Mini App, tin nhắn thoại và file, mọi lệnh `aipager`, và cài đặt từ môi trường |
| [Chế độ làm việc nhóm và nhóm](groups.md) | Thiết lập cho nhiều người, vai trò, quy tắc, ai đã làm gì |
| [Bot quan sát](observers.md) | Các bot chỉ đọc nhận bản sao thông báo |
| [Xử lý sự cố](troubleshooting.md) | Các lỗi hay gặp và bảng cách khắc phục của `aipager doctor` |
| [Báo cáo sự cố](problem-reports.md) | Một báo cáo sự cố chứa gì, gửi đi đâu, và khi nào aipager đề nghị gửi |
| [Mô hình bảo mật](security.md) | Ranh giới tin cậy, thông tin bí mật, log kiểm tra, kết nối mạng |
