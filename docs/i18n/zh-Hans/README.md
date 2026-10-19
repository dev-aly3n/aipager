<div align="center">

<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="96" height="96" alt="aipager"></a>

# aipager

<!-- i18n-source: README.md @ 2d10247 -->

<h3>Claude 只是又多了一个聊天。</h3>

你的 [Claude Code](https://claude.com/claude-code) 会话就在 Telegram 里，和家人、朋友、团队的聊天挨在一起。<br>
它们在你自己的电脑上运行。无论你在哪里，都能看着它们、回答它们、给它们指方向。

<p>
<a href="https://pypi.org/project/aipager/"><img alt="PyPI" src="https://img.shields.io/pypi/v/aipager?style=flat-square&label=pypi&labelColor=181c20&color=2aabee"></a>
<a href="https://pypi.org/project/aipager/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/aipager?style=flat-square&labelColor=181c20&color=2aabee"></a>
<a href="https://github.com/dev-aly3n/aipager/actions/workflows/test.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/dev-aly3n/aipager/test.yml?branch=main&style=flat-square&label=tests&labelColor=181c20"></a>
<img alt="Linux and macOS" src="https://img.shields.io/badge/runs%20on-Linux%20%7C%20macOS-2aabee?style=flat-square&labelColor=181c20">
<a href="../../../LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-2aabee?style=flat-square&labelColor=181c20"></a>
</p>

**[网站](https://aipager.run)** · **[安装](#install)** · **[文档](#documentation)** · **[安全](security.md)** · **[更新日志](../../../CHANGELOG.md)**

**用其他语言阅读：** [English](../../../README.md) · [Español](../es/README.md) · [Русский](../ru/README.md) · [Português (Brasil)](../pt-BR/README.md) · [Français](../fr/README.md) · [Deutsch](../de/README.md) · [한국어](../ko/README.md) · [العربية](../ar/README.md) · [Bahasa Indonesia](../id/README.md) · [Українська](../uk/README.md) · [Italiano](../it/README.md) · [Türkçe](../tr/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

<sub>本页译自英文；如果两者不一致，以英文版为准。（[English](../../../README.md)）</sub>

<br>

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/hero.webp" width="100%" alt="在 Telegram 里输入的消息到达终端里的 Claude Code，聊天里的一张卡片随着 Claude 工作自动更新">

<sub>你在 Telegram 里写字，aipager 把它输入到你电脑上的 Claude Code，任务进行时一张卡片会自动更新。</sub>

</div>

<br>

## <a id="why-aipager"></a>为什么用 aipager

<table>
<tr>
<td width="33%" valign="top">

**📟 每个任务一张实时卡片**<br>
Claude 此刻在做什么：它运行的工具、上下文、花费和改动的行数，最后是回答。卡片自己更新，聊天保持清爽。

</td>
<td width="33%" valign="top">

**⚡ 工作中也能说话**<br>
即使 Claude 正在工作，你的消息也会立即输入到会话里，就像在终端里打字一样。👀 表示已送出，👍 表示 Claude 已经收下。

</td>
<td width="33%" valign="top">

**✅ 点一下就能回答**<br>
权限请求和 Claude 的问题都带着每个答案一个按钮。已结束任务留下的按钮会拒绝，而不会作用到下一个任务上。

</td>
</tr>
<tr>
<td width="33%" valign="top">

**🛡️ 三种权限模式**<br>
💬 询问模式、🤖 自动模式（新会话的默认模式）和 🔓 完全访问模式，按会话设置，随时用 `/mode` 切换。

</td>
<td width="33%" valign="top">

**👥 为团队而做**<br>
把机器人拉进群组。每条消息都以发送者的角色运行，成员可以被限制在自己的文件夹里，每一次点按都记在审计日志里。

</td>
<td width="33%" valign="top">

**🔒 中间没有任何人**<br>
没有 aipager 服务器，没有账号，没有开放端口。你的机器人 token，你的电脑，你的聊天。

</td>
</tr>
</table>

## <a id="a-quick-tour"></a>快速一览

### <a id="questions-come-to-you"></a>问题会来找你

当 Claude 问你问题或需要权限时，问题会带着每个答案一个按钮出现在你的聊天里，置顶栏会告诉你什么在等你。你点的答案会像你坐在键盘前一样输入到会话里，终端里显示的也是同一个答案。

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/question.webp" width="100%" alt="Claude 问是调大超时还是等待 cookie；在 Telegram 里点的答案在终端里被选中">

### <a id="three-permission-modes"></a>三种权限模式

每个会话处于三种模式之一，随时用 `/mode` 切换，即使 Claude 正在工作也可以。

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/modes.webp" width="100%" alt="同一个任务在询问模式、自动模式和完全访问模式下的样子">

| 模式 | 会发生什么 |
|---|---|
| 💬 **询问模式**<br>`/mode ask` | Claude 每个操作前都会先问。 |
| 🤖 **自动模式**，默认<br>`/mode auto` | Claude Code 自己的安全检查会运行安全的操作，并拦下有风险的操作交给你决定。 |
| 🔓 **完全访问模式**<br>`/mode full` | 一切都不经任何检查直接运行。给管理员用，也给你在自己与机器人的私聊里用。 |

询问模式和自动模式之间就地切换，无需重启。进入或离开完全访问模式会重启会话，并保留对话。无论哪种模式，Claude 的问题都会来找你。

### <a id="your-team-in-one-group"></a>你的团队，在一个群组里

把机器人加进群组，给每个成员一个角色：`owner`、`admin`、`user` 或 `read_only`。在群组里，机器人只响应命令、对它消息的回复和提及（`@aipagerbot fix the tests`、`/web run the tests`），每条消息都以发送者的角色运行：角色无权使用的工具会在运行前被拒绝。`user` 或 `read_only` 成员只能在自己的根文件夹里工作（默认是 `~/aipager-users/<label>`）。其他人也可以和机器人有自己的私聊。

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/team.webp" width="100%" alt="一个团队群组：一条命令因为成员的角色被拒绝，同时测试顺利通过">

> **⚠️ 重要：** 添加成员就等于给了他们在你电脑上运行代码的权利。分享机器人之前，请先读一读[信任模型](groups.md)。

设置方法：把机器人以管理员身份加进群组（这样它能置顶群组的状态栏），再次运行 `aipager config`，选择 **Add a group scope**。向导会为每个成员添加角色，询问成员的根文件夹，并让正在运行的 aipager 重新加载配置。

### <a id="a-dashboard-inside-telegram"></a>Telegram 里的控制面板

`/app` 会打开 Mini App：你的会话，带停止、结束、重启、重命名和模式切换；每个 `Write` 和 `Edit` 的 diff 查看器；设置；对所有者还有“报告问题”。它由 aipager 自己提供，默认开启，每个请求都会用 Telegram 的签名校验。如果你在多个聊天里，顶部的一行聊天名称可以在它们之间切换。用 `aipager miniapp disable` 关闭它，或者让它指向你自己的地址（[方法](security.md#mini-app-tunnel)）。

### <a id="fifteen-languages"></a>十五种语言

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/languages.webp" width="420" align="right" alt="一条波斯语的权限请求，从右到左，使用波斯数字">

- 会说 English、Español、Русский、Português (Brasil)、简体中文、Français、Deutsch、한국어、العربية、Bahasa Indonesia、Українська、Italiano、Türkçe、فارسی 和 Tiếng Việt。
- 用 `/language` 选一种，或者让私聊跟随你的 Telegram 应用的语言。
- 波斯语和阿拉伯语从右到左阅读，并使用各自的数字。
- 只有 aipager 自己的文字会变。你的会话不会：你用什么语言写，Claude 就用什么语言回答。
- 这份 README 和[文档](#documentation)也都有翻译。

<br clear="right">

### <a id="and-the-small-things"></a>还有这些细节

- **多个会话，一个机器人。** 普通消息会发给你最后对话的那个会话；要和另一个会话说话，回复它的某条消息，或者发送 `/x1 <message>`。
- **不会搞混。** 有权限请求或问题未回答时发出的消息会等你回答之后再送出，所以永远不会被当成那个回答。
- **照片、文件和语音消息。** 语音消息在你自己的电脑上转成文字，需要可选的 `voice` 附加组件。
- **它会提醒你。** 上下文快满了、对话被压缩了、会话结束了、某个会话很久没动静了。
- **会话什么都扛得住。** Claude 运行在 `dtach` 里，所以你关掉终端、SSH 断开或者重启 aipager 之后，它都会继续工作。

## <a id="how-it-works"></a>工作原理

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/how-it-works.webp" width="100%" alt="Claude Code、它的 hook、aipager、Telegram 和你的聊天，排成一行">

Claude Code 在 `dtach` 里的真实终端中运行，所以没人需要一直开着窗口。每一步都会触发一个 [hook](hooks.md)，它通过本地套接字向 aipager 发送一个数据报，aipager 再编辑你聊天里的一条消息。你的回答沿同一条路回去：要么是交给扣着这次调用的 hook 的决定，要么是输入到会话里的按键。终端始终是唯一的事实来源。详见[架构](architecture.md)。

## <a id="install"></a>安装

Linux 或 macOS，任何架构。你需要在这台电脑上登录好的 Claude Code，以及一个 Telegram 机器人：用 [@BotFather](https://t.me/BotFather) 创建一个，然后在里面点 Start。

```sh
curl -fsSL https://aipager.run/install | sh   # uv、pipx 或 Homebrew：有哪个用哪个
aipager config                                # 你的机器人令牌、你的聊天、Claude Code 的 hook
aipager service install                       # 注销后也让 aipager 继续运行
aipager session api                           # 这个文件夹里的 Claude Code，也在你的聊天里
```

安装脚本有 `uv` 就用 `uv`，否则用 `pipx`，再否则（在 macOS 上）用 Homebrew，都没有时就安装 `uv`。`dtach` 随 aipager 一起提供（[`dtach-bin`](https://pypi.org/project/dtach-bin/) 包），所以不需要再装别的。`aipager config` 是一个向导：它检查机器人 token 和你的聊天，然后替你把 aipager 的 hook 加到 `~/.claude/settings.json` 里。你永远不需要手动编辑文件。

<details>
<summary><b>让编程代理替你安装</b></summary>

<br>

有 Claude Code，或者任何能用 shell 的代理吗？创建机器人，在里面点 Start，然后把机器人 token 保存到一个代理永远不需要读取的文件里（粘贴 token，按 Enter，再按 Ctrl-D）：

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

然后把这段话交给你的代理：

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

如果你不知道自己的 Telegram 用户 ID，就直接这么说；代理能找到它。代理遵循的指南（包括如何安全地交出 token）是[用代理安装](install-with-an-agent.md)。在脚本里，`aipager setup --token-file FILE --chat-id ID` 会不问任何问题地完成设置（[setup 命令](commands.md#command-line-aipager-setup-for-coding-agents)）。

</details>

### <a id="other-ways-to-install"></a>其他安装方式

```sh
uv tool install aipager                        # uv：macOS 上推荐，自带 Python
pipx install aipager                           # pipx
brew install dev-aly3n/tap/aipager             # Homebrew，适用于 macOS 和 Linuxbrew（见下方说明）
nix profile install github:dev-aly3n/aipager   # Nix：不包含 claude（见下文）
snap install aipager                           # Snap：严格隔离，内含 claude，工作文件夹需在 ~/ 下
```

或者在 [Docker](#docker) 里运行，里面已经有 Python、Node、`claude` 和 `dtach`。

<details>
<summary><b>macOS 26（Tahoe）上的 Homebrew</b></summary>

<br>

当 Homebrew 的 `python@3.12` bottle 和你的 Xcode Command Line Tools 匹配时，这个 formula 可以正常工作，但两者时不时会错开。在 macOS 26 上，安装会因 `pyexpat _XML_SetAllocTrackerActivationThreshold` 符号错误而失败（[上游 issue](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)）。更新 Xcode 和 Command Line Tools 通常能解决，但用 `uv tool install aipager` 根本不会遇到这个问题。formula 从 Homebrew 获取 `dtach`，并把 aipager 安装到由 Homebrew 管理的 Python 环境中。

</details>

### <a id="docker"></a>Docker

一个自带 Python、Node、`claude` 和 `dtach` 的独立镜像，适合不想在主机上装 Python 或 Node 的 VPS、NAS 或树莓派。支持多架构（amd64、arm64）。

```sh
# 1. 运行一次设置向导（交互式）
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. 启动 aipager（后台运行，自动重启）
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

把希望 Claude 编辑的文件夹挂载到 `/workspace` 下。挂载 `~/.claude` 会带上你的 Claude 凭据和保存的对话：在主机上运行一次 `claude` 登录，或者用 `docker exec -it aipager claude` 在容器里登录。

标签：`latest`、`1.3`、`1.3.7`（一个对应精确版本，一个对应次版本）。

### <a id="nix"></a>Nix

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

用固定版本的 nixpkgs 从源码构建 aipager。`dtach` 由 Nix 提供，`claude` 不是，所以请单独安装（`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`，或参照 Anthropic 的文档）。

<details>
<summary><b>NixOS 和 Home Manager</b></summary>

<br>

把 aipager 作为 flake 输入加入，并把它的包放进 `environment.systemPackages`：

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

之后 `aipager service install` 会创建一个 systemd 用户单元。

</details>

## <a id="everyday-use"></a>日常使用

### <a id="in-telegram"></a>在 Telegram 里

| 发送 | 作用 |
|---|---|
| `/status` | 查看这个聊天的会话，每个都带状态和按钮。 |
| `/new x1` | 启动一个名为 x1 的会话。`/new x1 fix the tests` 还会发送第一条消息。 |
| `/x1` | 让你的消息发给 x1。`/x1 <message>` 直接给它发一条消息。 |
| `/stop` | 中断正在运行的任务，并丢弃排队中的内容。 |
| `/mode` | 在询问模式、自动模式和完全访问模式之间切换。 |
| `/app` | 打开 Mini App。 |
| `/language` | 选择 aipager 在这个聊天里使用的语言。 |
| `/update` | 更新 aipager 和 Claude Code，然后就地重启（管理员）。 |
| `/help` | 按任务分类的简短指南。 |

所有命令、按钮和设置都在[机器人命令](commands.md)里。

### <a id="on-the-command-line"></a>在命令行里

```sh
aipager start                             # aipager 本身，在前台运行
aipager session dev                       # 在这里启动名为 dev 的会话，或重新连上它
aipager session dev --continue            # 继续这个文件夹里最近一次 Claude 对话
aipager session dev -- --resume abc1234   # 其他 claude 参数原样传递
aipager doctor                            # 检查安装
```

想离开会话又不停下 Claude，就关掉终端或 tmux 窗格：会话会继续运行，同一条命令能再连回去。

`aipager service install` 让 aipager 在后台持续运行：Linux 上是 systemd 用户单元（`~/.config/systemd/user/aipager.service`），macOS 上是 launchd 代理（`~/Library/LaunchAgents/com.aipager.daemon.plist`）。无论哪种，aipager 都在你的主文件夹里运行，除非你选了别的文件夹，新会话也从那里开始。用 `aipager service start`、`stop`、`status`、`logs` 和 `uninstall` 管理它。

## <a id="security"></a>安全

- **没有 aipager 服务器。** aipager 用你自己的机器人 token 直接和 Telegram Bot API 通信。没有 aipager 账号，也没有 aipager 服务。
- **没有开放端口。** aipager 只监听回环地址。Mini App 通过 Cloudflare 隧道对外，其中每个请求都必须带有 Telegram 的签名，`aipager miniapp disable` 可以关闭它。
- **每个回答都有记录。** 每一次允许、拒绝和回答都会追加到 `~/.claude/aipager-audit.jsonl`，在群组里还会记下是谁点的。
- **仍由 Claude Code 决定。** 需要问什么，由你的 Claude Code 设置决定。aipager 只负责转达问题，从不提升权限：没有 sudo，一切都以你的用户身份运行。

完整的情况，包括是什么在保护你的 Claude 凭据，都在[安全模型](security.md)里。

## <a id="faq"></a>常见问题

<details>
<summary><b>这和 Claude 的手机应用有什么不同？</b></summary>

<br>

Claude 的应用和 Anthropic 托管的助手对话。aipager 把你连到在你自己电脑上运行的 Claude Code 会话：你的代码仓库、你的工具、你的环境。

</details>

<details>
<summary><b>我的代码会离开我的电脑吗？</b></summary>

<br>

aipager 只把你在聊天里看到的内容，用你自己的机器人 token 发给 Telegram；中间没有任何 aipager 服务器。Claude Code 本身照常和 Anthropic 通信。

</details>

<details>
<summary><b>dtach 是什么？</b></summary>

<br>

一个让程序脱离终端的小工具。终端关闭或 SSH 断开时，它让 Claude Code 继续运行，所以一小时后发出的回答仍然能到达还在运行的会话。它随 aipager 一起提供。

</details>

<details>
<summary><b>要是 Claude 跑偏了怎么办？</b></summary>

<br>

请求会等你点，`/stop` 中断任务并丢弃所有排队内容，`/kill` 结束会话，已结束任务的按钮会拒绝。

</details>

<details>
<summary><b>怎么更新？</b></summary>

<br>

发送 `/update`：它会检查 aipager 和 Claude Code 并安装新版本，然后“🔄 立即重启”会把 aipager 重新拉起来，无论是否作为服务运行。期间你的会话照常运行。在命令行里，`aipager update` 会通过安装 aipager 的那个安装程序来更新。

</details>

<details>
<summary><b>我用 Bedrock 或 Vertex，模型按钮能用吗？</b></summary>

<br>

键盘上的**模型**菜单和 Mini App 提供 Claude Code 的系列别名（`sonnet`、`opus`、`haiku`、`fable`、`opusplan`）以及 `claude-opus-5-5`、`claude-sonnet-5-5` 这样的固定模型。在 Anthropic API 上，别名指向该系列的最新模型。在 Bedrock 和 Vertex 上，同一个别名可能指向较旧的版本，取决于你的服务商提供什么；要选定确切的模型，请发送 `/model <full-id>`。

</details>

<details>
<summary><b>我用的是 API 密钥、网关或其他服务商。aipager 能用吗？</b></summary>

<br>

能。aipager 按你的设置运行 Claude Code，并替你回答 Claude Code 启动时问的问题（文件夹、API 密钥、首次运行的界面），这样从 Telegram 启动的会话不会卡在那里。把设置写进 `~/.claude/settings.json` 的 `env` 块，或写进 `~/.config/aipager/daemon.env`（每行一个 `KEY=value`，不加引号）。Opus、Sonnet 和 Haiku 按钮跟随 `ANTHROPIC_DEFAULT_OPUS_MODEL`、`ANTHROPIC_DEFAULT_SONNET_MODEL` 和 `ANTHROPIC_DEFAULT_HAIKU_MODEL`，其他模型直接输入名称即可。Anthropic 只支持 Claude Code 搭配 Claude 模型使用。详见[故障排除](troubleshooting.md#using-an-api-key-a-gateway-or-another-provider)。

</details>

<details>
<summary><b>要花钱吗？</b></summary>

<br>

不花钱。aipager 采用 MIT 许可，机器人是你的，Claude Code 继续用它原本在用的套餐或密钥。

</details>

## <a id="documentation"></a>文档

| 页面 | 内容 |
|---|---|
| [机器人命令](commands.md) | 所有命令、按钮、设置和命令行选项 |
| [团队模式与群组](groups.md) | 角色、根文件夹、谁能点什么、信任模型 |
| [安全模型](security.md) | 信任边界、机密、审计日志、网络 |
| [Hook 事件](hooks.md) | Claude Code 的每个 hook 在 aipager 里做什么 |
| [架构](architecture.md) | aipager 的组成部分、文件和套接字 |
| [故障排除](troubleshooting.md) | `aipager doctor` 和常见问题的解决办法 |
| [问题报告](problem-reports.md) | 报告里有什么，以及 aipager 何时建议发送 |
| [用代理安装](install-with-an-agent.md) | 编程代理遵循的指南 |

## <a id="contributing"></a>参与贡献

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach` 随 `dtach-bin` 依赖一起提供，不需要别的。请像上面那样在内存上限下运行测试（macOS 上不用 `systemd-run`）：否则一个失控的测试可能占满整台电脑的内存。发布流程见 [CONTRIBUTING.md](../../../CONTRIBUTING.md)。

## <a id="license"></a>许可证

MIT，见 [LICENSE](../../../LICENSE)。

<br>

<div align="center">
<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="32" height="32" alt=""></a>
<br>
<sub><a href="https://aipager.run">aipager.run</a></sub>
</div>
