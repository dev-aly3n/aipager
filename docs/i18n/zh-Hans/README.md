# aipager

<!-- i18n-source: README.md @ a175bc5 -->

> 本页译自英文；如果两者不一致，以英文版为准。（[English](../../../README.md)）

[![PyPI](https://img.shields.io/pypi/v/aipager?label=pypi&color=3775A9)](https://pypi.org/project/aipager/)
[![Python](https://img.shields.io/pypi/pyversions/aipager?color=3775A9)](https://pypi.org/project/aipager/)
[![License](https://img.shields.io/badge/license-MIT-black)](../../../LICENSE)

通过 Telegram 远程控制 [Claude Code](https://claude.com/claude-code) 会话。Claude 在你的电脑上的一个分离终端（`dtach`）里运行，你在手机上使用它：阅读 Claude 的回答、给它发消息、回应它的权限请求和问题、在会话之间切换，而不必一直开着 SSH 连接。

[aipager.run](https://aipager.run) · [文档](#docs) · [更新日志](../../../CHANGELOG.md) · [GitHub issue](https://github.com/dev-aly3n/aipager/issues)

**用其他语言阅读：** [English](../../../README.md) · [Español](../es/README.md) · [Русский](../ru/README.md) · [Português (Brasil)](../pt-BR/README.md) · [Français](../fr/README.md) · [Deutsch](../de/README.md) · [한국어](../ko/README.md) · [العربية](../ar/README.md) · [Bahasa Indonesia](../id/README.md) · [Українська](../uk/README.md) · [Italiano](../it/README.md) · [Türkçe](../tr/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

## <a id="install"></a>安装

你有编程代理吗（Claude Code，或任何能用 shell 的代理）？用 [@BotFather](https://t.me/BotFather) 创建一个机器人，在机器人里点 Start，并确认这台电脑上的 Claude Code 已登录你的账号。把机器人的 token 保存到一个代理永远不需要读取的文件里（粘贴 token，按 Enter，再按 Ctrl-D）：

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

然后把这条消息交给代理：

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

如果你不知道自己的 Telegram ID，直接告诉代理；它能查出来。

代理遵循的指南（包括如何安全地把 token 交给它）在[用代理安装](install-with-an-agent.md)里。想自己安装，请继续往下读。

支持 Linux 或 macOS，任何架构都可以。`dtach` 通过依赖 [`dtach-bin`](https://pypi.org/project/dtach-bin/) 自动安装：不需要单独的系统软件包。

### <a id="one-line-install-recommended"></a>一行安装（推荐）

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

有 `uv` 就用 `uv`，否则用 `pipx`，再否则（仅限 macOS）用 Homebrew。如果都没有，它会先安装 `uv`（Astral 的 Python 工具管理器），再用它安装 aipager。

### <a id="uv-recommended-on-macos"></a>uv（macOS 上推荐）

```sh
uv tool install aipager     # 如果已经装了 uv
```

或者先安装 uv：

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install aipager
```

uv 自带 Python 解释器，所以不管系统里的 Python 是什么状态，它都能在任何版本的 macOS 或 Linux 上运行；这样就避开了下面 Homebrew 部分提到的 Homebrew Python 与 Xcode 的冲突。

### <a id="pipx"></a>pipx

```sh
pipx install aipager
```

### <a id="homebrew-tap-macos-linuxbrew"></a>Homebrew tap（macOS、Linuxbrew）

> **注意**：在 macOS 上推荐使用 `uv tool install aipager`。当 Homebrew 的 `python@3.12` 与你的 Xcode / Command Line Tools 一致时，brew 配方可以正常工作，但两者偶尔会对不上；最近一次是在 **macOS Tahoe (26.x)** 上，安装因符号错误 `pyexpat _XML_SetAllocTrackerActivationThreshold` 而停止（[上游 issue](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)）。更新 Xcode 和 Command Line Tools 通常能解决，但用 uv 更简单。

```sh
brew install dev-aly3n/tap/aipager
```

从 Homebrew 的标准配方获取 `dtach`，并把 aipager 安装到一个由 Homebrew 管理的 Python 虚拟环境里。

### <a id="docker"></a>Docker

一个预先构建好的镜像，里面有 python、node、`claude` 和 `dtach`；适合不想在系统里安装 Python 或 Node 的 VPS、NAS 或树莓派。支持多架构（amd64、arm64）。

```sh
# 1. 运行一次设置向导（交互式）
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. 运行 aipager（后台，自动重启）
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

把你想让 Claude 修改的文件夹挂载到 `/workspace`。挂载 `~/.claude` 会带上你的 Claude 登录信息和对话历史；要登录，在本机运行一次 `claude`，或用 `docker exec -it aipager claude` 在容器里登录。

标签：`latest`、`1.2`、`1.2.0`（一个对应精确版本，一个对应次版本）。

### <a id="nix-flake"></a>Nix flake

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

用从 nixpkgs 固定的依赖从源码构建 aipager。`dtach` 由 Nix 提供；`claude` **不**由 Nix 提供：请另外安装（`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`，或按照 Anthropic 的文档）。

对于声明式的 NixOS 或 Home Manager 配置，把 aipager 加为 flake 的输入，并把它的软件包放进 `environment.systemPackages`：

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

之后用 `aipager service install` 设置一个 systemd 用户服务。

### <a id="snap"></a>Snap

```sh
snap install aipager
```

一个严格隔离的 snap，里面有 python、node、`claude`、`dtach` 和 aipager。由于 snap 沙箱的限制，工作文件夹必须在 `~/` 之下（例如 `~/projects/foo`）。配置清单在 [`packaging/snap/`](../../../packaging/snap/) 里。

## <a id="configure"></a>设置

```sh
aipager config
```

一个交互式设置向导：询问你的 Telegram 机器人 token（来自 [@BotFather](https://t.me/BotFather)）和聊天 ID，检查两者，然后自动把需要的 hook 加到 `~/.claude/settings.json`。你永远不需要手动编辑文件。

要通过脚本或编程代理为别人设置 aipager？`aipager setup --token-file FILE --chat-id ID` 做同样的事，但不问任何问题（见 [setup 命令](commands.md#command-line-aipager-setup-for-coding-agents)和[代理指南](install-with-an-agent.md)）。

## <a id="run"></a>运行

```sh
aipager start
```

aipager 在前台运行。在另一个终端里启动一个 Claude 会话：

```sh
aipager session dev
```

这会创建一个名为 `claude-dev`、运行 Claude Code 的 dtach 会话（如果已存在就连接上去）。aipager 几秒内就会发现它，Telegram 开始显示它。你也可以从 Telegram 启动会话：给机器人发送 `/new dev`。之后要重新连接，再运行同一条命令即可；要离开而不停止 Claude，关闭终端或 tmux 窗格就行（会话会继续运行）。

如果 dtach 会话已经关闭（例如电脑重启过），而你想从磁盘上继续 Claude 的对话，加上 `--continue`：

```sh
aipager session dev --continue    # 这个文件夹里最近一次 claude 对话
```

也可以在末尾加上 `--resume <session-id>`（或任何其他 claude 参数）：

```sh
aipager session dev -- --resume abc1234
```

### <a id="run-as-a-service-survives-logout"></a>作为服务运行（退出登录后仍然运行）

```sh
aipager service install
```

在 Linux 上，它把一个 systemd 用户服务写到 `~/.config/systemd/user/aipager.service` 并启动它。在 macOS 上，它把一个 launchd plist 写到 `~/Library/LaunchAgents/com.aipager.daemon.plist` 并启动它。子命令：`start`、`stop`、`status`、`logs`、`uninstall`。

## <a id="team-mode-groups-and-other-people"></a>团队模式（群组和其他人）

默认情况下，aipager 是个人机器人：只有你和机器人在一个私聊里。要共享，可以添加一个 Telegram 群组、其他人和机器人的私聊，或者两者都加。对于群组：把机器人加入群组（设为管理员，这样它才能置顶群组的状态栏），再次运行 `aipager config` 并选择 **Add a group scope**。设置向导会建议你（你自己私聊的所有者）担任群组的所有者，给每个成员一个角色（`owner`、`admin`、`user` 或 `read_only`），询问成员的根文件夹，然后在不重启的情况下重新加载 aipager 的设置。在群组里，机器人只回应命令、对它消息的回复和提及它的消息（`@aipagerbot fix the tests`、`/jim run the tests`），每条消息都以发送者的角色运行。`user` 或 `read_only` 成员只在其聊天的根文件夹（默认 `~/aipager-users/<label>`）里启动和保留会话。**添加一个成员，就等于让这个人能在你的电脑上运行代码**：完整的信任模型见[团队模式与群组](groups.md)。

## <a id="what-it-does"></a>它能做什么

- 在 Telegram 里显示每个 Claude Code 会话在做什么：工作中还是就绪、正在用的工具、上下文百分比、花费和改动的行数，每个任务一张卡片，最后是 Claude 的回答
- **立即**把你的消息送到 Claude，即使它正在工作：发几条都行，Claude 会自己排队，就像在终端里输入一样。👀 表示已送出，👍 表示 Claude 已经取走
- 当有权限请求或问题未回答时，先暂缓你的消息，这样它永远不会被当成对那个请求的回答，等你回答之后再发送
- 用按钮显示 Claude 的权限请求和问题（允许、拒绝、始终允许、每个选项一个按钮）；已结束任务留下的按钮会拒绝，而不会作用到正在运行的任务上
- 每个会话有三种权限模式，用 `/mode` 切换：💬 询问模式（Claude 每个操作前都会问）、🤖 自动模式，即默认模式（Claude Code 自己的安全检查会运行安全的操作，并停下有风险的操作交给你决定），以及 🔓 完全访问模式（所有操作都不经检查直接运行；供管理员使用，以及你在自己的私聊里使用）
- 在聊天里启动会话：`/new x1` 立即启动一个会话（`/new x1 fix the tests` 还会发送第一条消息），模式、模型和文件夹取自 `/settings`，并带有修改它们的按钮
- 接收照片、文件和语音消息（语音消息在你自己的电脑上转成文字，需安装语音消息功能）
- 在上下文快满时、对话被压缩时、会话结束时，以及会话沉默太久时提醒你
- 通过一个机器人同时运行多个会话；可选的团队模式，带角色和按工具划分的规则（[团队模式与群组](groups.md)）
- 会说 English、Español、Русский、Português (Brasil)、简体中文、Français、Deutsch、한국어、العربية、Bahasa Indonesia、Українська、Italiano、Türkçe、فارسی 和 Tiếng Việt：只限 aipager 自己的文字，绝不涉及你的会话或 Claude 的回答（[语言](commands.md#languages)）。本 README 和文档也已翻译（见上方的 **用其他语言阅读**）

### <a id="mini-app"></a>Mini App

`/app` 在 Telegram 里打开一个控制面板：你的会话，带停止、结束、重启、重命名和切换模式，`Write`/`Edit` 改动的 diff 查看器，设置，以及给所有者的“报告问题”。它由 aipager 自己提供，**默认开启**；每个请求都会用 Telegram 的 `initData` 签名校验。如果你在多个聊天里（你的私聊和一个群组），顶部会有一行聊天名称供你切换。用 `aipager miniapp enable|disable|status` 管理，或者让它指向你自己的地址，代替托管隧道：见[安全模型](security.md#mini-app-tunnel)。

### <a id="note-on-model-buttons-bedrock--vertex-users"></a>关于模型按钮（Bedrock 和 Vertex 用户）

固定键盘上的**模型**子菜单（以及 Mini App 的模型选择器）提供 Claude Code 的系列别名 `sonnet`、`opus`、`haiku`、`fable` 和 `opusplan`，以及固定的模型，如 `claude-opus-5-5` 和 `claude-sonnet-5-5`（完整列表见[固定键盘](commands.md#persistent-keyboard)）。在 Anthropic 的 API 上，别名总是指向该系列最新的模型。在 **Bedrock** 和 **Vertex** 上，同样的别名可能指向较旧的版本，取决于你的服务商提供什么。如果你使用这些服务并需要某个特定模型，请在聊天中发送 `/model <full-id>`。

## <a id="developing-locally"></a>在本地开发

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach` 随依赖 `dtach-bin` 一起提供，不需要别的。请像上面那样在内存上限下运行测试（macOS 上去掉 `systemd-run`）：否则一个失控的测试可能会吃光电脑的内存。

发布流程见 [CONTRIBUTING.md](../../../CONTRIBUTING.md)。

## <a id="license"></a>许可证

MIT；全文见 [LICENSE](../../../LICENSE)。

## <a id="docs"></a>文档

| 页面 | 内容 |
|---|---|
| [用代理安装](install-with-an-agent.md) | 编程代理为它的使用者安装和设置 aipager 时遵循的步骤 |
| [架构](architecture.md) | 程序如何构建、各部分示意图、文件和套接字的位置 |
| [Hook 事件](hooks.md) | aipager 从 Claude Code 收到的每个事件及其字段 |
| [机器人命令](commands.md) | 命令、按钮、Mini App、语音消息和文件、每个 `aipager` 命令，以及来自环境的设置 |
| [团队模式与群组](groups.md) | 多人设置、角色、规则、谁做了什么 |
| [故障排除](troubleshooting.md) | 常见故障和 `aipager doctor` 的修复表 |
| [问题报告](problem-reports.md) | 问题报告包含什么、发到哪里，以及 aipager 何时提议发送 |
| [安全模型](security.md) | 信任边界、机密信息、审计日志、网络连接 |
