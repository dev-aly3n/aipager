<div align="center">

<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="96" height="96" alt="aipager"></a>

# aipager

<!-- i18n-source: README.md @ 2d10247 -->

<h3>이제 Claude도 채팅 하나예요.</h3>

내 [Claude Code](https://claude.com/claude-code) 세션을 Telegram에서, 가족과 친구와 팀 채팅 바로 옆에서 만나요.<br>
세션은 내 컴퓨터에서 돌아가요. 어디에 있든 진행 상황을 보고, 질문에 답하고, 다음 일을 맡길 수 있어요.

<p>
<a href="https://pypi.org/project/aipager/"><img alt="PyPI" src="https://img.shields.io/pypi/v/aipager?style=flat-square&label=pypi&labelColor=181c20&color=2aabee"></a>
<a href="https://pypi.org/project/aipager/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/aipager?style=flat-square&labelColor=181c20&color=2aabee"></a>
<a href="https://github.com/dev-aly3n/aipager/actions/workflows/test.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/dev-aly3n/aipager/test.yml?branch=main&style=flat-square&label=tests&labelColor=181c20"></a>
<img alt="Linux and macOS" src="https://img.shields.io/badge/runs%20on-Linux%20%7C%20macOS-2aabee?style=flat-square&labelColor=181c20">
<a href="../../../LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-2aabee?style=flat-square&labelColor=181c20"></a>
</p>

**[웹사이트](https://aipager.run)** · **[설치](#install)** · **[문서](#documentation)** · **[보안](security.md)** · **[변경 사항](../../../CHANGELOG.md)**

**다른 언어로 읽기:** [English](../../../README.md) · [Español](../es/README.md) · [Русский](../ru/README.md) · [Português (Brasil)](../pt-BR/README.md) · [简体中文](../zh-Hans/README.md) · [Français](../fr/README.md) · [Deutsch](../de/README.md) · [العربية](../ar/README.md) · [Bahasa Indonesia](../id/README.md) · [Українська](../uk/README.md) · [Italiano](../it/README.md) · [Türkçe](../tr/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

<sub>이 페이지는 영어 문서를 번역한 거예요. 내용이 서로 다르면 영어 문서가 맞아요. ([English](../../../README.md))</sub>

<br>

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/hero.webp" width="100%" alt="Telegram에서 입력한 메시지가 터미널의 Claude Code에 전달되고, 채팅의 카드 하나가 Claude가 일하는 동안 스스로 업데이트돼요">

<sub>Telegram에 쓰면 aipager가 내 컴퓨터의 Claude Code에 그대로 입력하고, 작업이 진행되는 동안 카드 하나가 스스로 업데이트돼요.</sub>

</div>

<br>

## <a id="why-aipager"></a>aipager를 쓰는 이유

<table>
<tr>
<td width="33%" valign="top">

**📟 작업마다 실시간 카드 하나**<br>
Claude가 지금 하고 있는 일: 실행 중인 도구, 컨텍스트, 비용, 바뀐 줄 수, 그리고 마지막에 답변까지. 카드가 스스로 업데이트되니 채팅이 어수선해지지 않아요.

</td>
<td width="33%" valign="top">

**⚡ 작업 중에도 말을 걸어요**<br>
Claude가 일하는 중이어도 내 메시지가 바로 세션에 입력돼요. 터미널에서 입력하는 것과 똑같아요. 👀는 전달됨, 👍는 Claude가 가져갔다는 뜻이에요.

</td>
<td width="33%" valign="top">

**✅ 탭 한 번으로 답해요**<br>
권한 요청과 Claude의 질문은 답마다 버튼이 붙어서 와요. 끝난 작업에 남은 버튼은 다음 작업에 작용하지 않고 거부돼요.

</td>
</tr>
<tr>
<td width="33%" valign="top">

**🛡️ 권한 모드 세 가지**<br>
💬 확인 모드, 🤖 자동 모드(새 세션의 기본값), 🔓 전체 권한 모드. 세션마다 정하고 `/mode`로 언제든 바꿔요.

</td>
<td width="33%" valign="top">

**👥 팀을 위해 만들었어요**<br>
봇을 그룹에 넣어 보세요. 메시지는 보낸 사람의 역할로 실행되고, 멤버를 각자의 폴더 안으로 제한할 수 있고, 모든 탭이 감사 로그에 남아요.

</td>
<td width="33%" valign="top">

**🔒 중간에 아무것도 없어요**<br>
aipager 서버도, 계정도, 열린 포트도 없어요. 내 봇 token, 내 컴퓨터, 내 채팅뿐이에요.

</td>
</tr>
</table>

## <a id="a-quick-tour"></a>짧게 둘러보기

### <a id="questions-come-to-you"></a>질문이 나에게 와요

Claude가 무언가를 묻거나 권한이 필요하면, 질문이 답마다 버튼이 붙은 채로 내 채팅에 오고, 고정된 표시줄이 무엇이 나를 기다리는지 알려 줘요. 내가 누른 답은 키보드 앞에 앉아 있는 것처럼 세션에 입력되고, 터미널에도 같은 답이 보여요.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/question.webp" width="100%" alt="Claude가 타임아웃을 늘릴지 쿠키를 기다릴지 묻고, Telegram에서 누른 답이 터미널에서 선택돼요">

### <a id="three-permission-modes"></a>권한 모드 세 가지

세션은 세 가지 모드 중 하나에 있고, Claude가 일하는 중에도 `/mode`로 언제든 바꿀 수 있어요.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/modes.webp" width="100%" alt="같은 작업을 확인 모드, 자동 모드, 전체 권한 모드에서 본 모습">

| 모드 | 어떻게 되나요 |
|---|---|
| 💬 **확인 모드**<br>`/mode ask` | Claude가 작업마다 먼저 물어봐요. |
| 🤖 **자동 모드**, 기본값<br>`/mode auto` | Claude Code 자체의 안전 검사가 안전한 작업은 실행하고, 위험한 작업은 멈춰서 내가 결정하게 해요. |
| 🔓 **전체 권한 모드**<br>`/mode full` | 아무 검사 없이 모든 것이 실행돼요. 관리자, 그리고 봇과의 내 개인 채팅에 있는 나를 위한 모드예요. |

확인 모드와 자동 모드 사이는 재시작 없이 그 자리에서 바뀌어요. 전체 권한 모드로 들어가거나 나오면 세션이 재시작되고 대화는 그대로 남아요. Claude의 질문은 어떤 모드에서든 나에게 와요.

### <a id="your-team-in-one-group"></a>그룹 하나에 팀 전체

봇을 그룹에 추가하고 멤버마다 역할을 줘요: `owner`, `admin`, `user`, `read_only`. 그룹에서 봇은 명령, 봇의 메시지에 단 답장, 멘션에만 반응하고(`@aipagerbot fix the tests`, `/web run the tests`), 메시지는 보낸 사람의 역할로 실행돼요. 역할이 쓸 수 없는 도구는 실행 전에 거부돼요. `user`나 `read_only` 멤버는 자기 루트 폴더 안에서만 일해요(기본값은 `~/aipager-users/<label>`). 다른 사람도 봇과 자기만의 개인 채팅을 가질 수 있어요.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/team.webp" width="100%" alt="멤버의 역할 때문에 명령이 거부되는 동안 테스트는 통과하는 팀 그룹">

> **⚠️ 중요:** 멤버를 추가하면 그 사람에게 내 컴퓨터에서 코드를 실행할 권한을 주는 거예요. 봇을 공유하기 전에 [신뢰 모델](groups.md)을 읽어 보세요.

설정하려면 봇을 그룹에 관리자로 추가하고(그룹의 상태 표시줄을 고정할 수 있게), `aipager config`를 다시 실행한 뒤 **Add a group scope**를 고르세요. 마법사가 멤버마다 역할을 붙여 추가하고, 멤버들의 루트 폴더를 묻고, 실행 중인 aipager의 설정을 다시 불러와요.

### <a id="a-dashboard-inside-telegram"></a>Telegram 안의 대시보드

`/app`을 보내면 Mini App이 열려요. 중지, 종료, 재시작, 이름 바꾸기, 모드 전환이 붙은 내 세션들, 모든 `Write`와 `Edit`의 diff 보기, 설정, 그리고 소유자에게는 문제 신고가 있어요. aipager가 직접 띄우고, 기본으로 켜져 있으며, 모든 요청은 Telegram의 서명으로 확인해요. 채팅이 여러 개라면 위쪽의 채팅 이름 줄로 오갈 수 있어요. `aipager miniapp disable`로 끄거나, 내 주소로 연결할 수 있어요([방법](security.md#mini-app-tunnel)).

### <a id="fifteen-languages"></a>15개 언어

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/languages.webp" width="420" align="right" alt="페르시아어로, 오른쪽에서 왼쪽으로, 페르시아 숫자와 함께 보이는 권한 요청">

- English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی, Tiếng Việt를 말해요.
- `/language`로 하나를 고르거나, 개인 채팅이 내 Telegram 앱의 언어를 따르게 두세요.
- 페르시아어와 아랍어는 오른쪽에서 왼쪽으로 읽고, 그 언어의 숫자를 써요.
- 바뀌는 건 aipager 자신의 글뿐이에요. 내 세션은 그대로예요. Claude는 내가 쓴 언어로 답해요.
- 이 README와 [문서](#documentation)도 번역되어 있어요.

<br clear="right">

### <a id="and-the-small-things"></a>그리고 작은 것들

- **세션은 여러 개, 봇은 하나.** 일반 메시지는 마지막으로 대화한 세션으로 가요. 다른 세션과 말하려면 그 세션의 메시지에 답장하거나 `/x1 <message>`를 보내세요.
- **섞이지 않아요.** 권한 요청이나 질문이 열려 있는 동안 보낸 메시지는 내가 답할 때까지 기다려서, 그 답으로 읽히는 일이 없어요.
- **사진, 파일, 음성 메시지.** 음성 메시지는 선택 사항인 `voice` 추가 기능으로 내 컴퓨터에서 글로 바뀌어요.
- **알려 줘요.** 컨텍스트가 거의 찼을 때, 대화가 압축됐을 때, 세션이 끝났을 때, 세션이 한동안 조용할 때요.
- **세션은 무엇이든 버텨요.** Claude는 `dtach` 안에서 돌아가서, 터미널을 닫거나 SSH가 끊기거나 aipager를 재시작해도 계속 일해요.

## <a id="how-it-works"></a>작동 방식

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/how-it-works.webp" width="100%" alt="Claude Code, 그 hook, aipager, Telegram, 그리고 내 채팅이 한 줄로 이어진 모습">

Claude Code는 `dtach` 안의 진짜 터미널에서 돌아가서, 누구도 창을 열어 둘 필요가 없어요. 단계마다 [hook](hooks.md)이 실행되어 로컬 소켓으로 aipager에 데이터그램 하나를 보내고, aipager는 내 채팅의 메시지를 고쳐요. 내 답은 같은 길로 돌아가요. 호출을 붙잡고 있는 hook에 결정을 보내거나, 세션에 키를 입력해요. 기준은 언제나 터미널이에요. 자세한 내용은 [아키텍처](architecture.md)에 있어요.

## <a id="install"></a>설치

Linux나 macOS, 어떤 아키텍처든 돼요. 이 컴퓨터에 로그인된 Claude Code와 Telegram 봇이 필요해요. [@BotFather](https://t.me/BotFather)로 봇을 만들고 그 안에서 Start를 누르세요.

```sh
curl -fsSL https://aipager.run/install | sh   # uv, pipx, Homebrew 중 있는 것으로
aipager config                                # 봇 토큰, 내 채팅, Claude Code의 hook
aipager service install                       # 로그아웃해도 aipager가 계속 돌아가게
aipager session api                           # 이 폴더의 Claude Code를 내 채팅에서도
```

설치 스크립트는 `uv`가 있으면 그걸 쓰고, 없으면 `pipx`, 그것도 없으면(macOS에서) Homebrew를 쓰고, 아무것도 없으면 `uv`를 설치해요. `dtach`는 aipager와 함께 와요([`dtach-bin`](https://pypi.org/project/dtach-bin/) 패키지). 그래서 따로 설치할 게 없어요. `aipager config`는 마법사예요. 봇 token과 내 채팅을 확인한 다음 aipager의 hook을 `~/.claude/settings.json`에 알아서 추가해요. 파일을 직접 고칠 일은 없어요.

<details>
<summary><b>코딩 에이전트에게 설치를 맡기기</b></summary>

<br>

Claude Code나 shell을 쓸 수 있는 에이전트가 있나요? 봇을 만들고 그 안에서 Start를 누른 다음, 봇 token을 에이전트가 읽을 필요가 없는 파일에 저장하세요(token을 붙여 넣고 Enter, 그다음 Ctrl-D):

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

그다음 에이전트에게 이 프롬프트를 주세요:

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

내 Telegram 사용자 ID를 모르면 그렇다고 말하면 돼요. 에이전트가 찾을 수 있어요. 에이전트가 따르는 안내서는 token을 안전하게 넘기는 방법까지 담은 [에이전트로 설치하기](install-with-an-agent.md)예요. 스크립트에서는 `aipager setup --token-file FILE --chat-id ID`가 아무것도 묻지 않고 aipager를 설정해요([setup 명령](commands.md#command-line-aipager-setup-for-coding-agents)).

</details>

### <a id="other-ways-to-install"></a>다른 설치 방법

```sh
uv tool install aipager                        # uv: macOS에서 추천, 자체 Python을 함께 가져와요
pipx install aipager                           # pipx
brew install dev-aly3n/tap/aipager             # Homebrew, macOS와 Linuxbrew에서(아래 참고)
nix profile install github:dev-aly3n/aipager   # Nix: claude는 포함되지 않아요(아래 참고)
snap install aipager                           # Snap: 엄격한 격리, claude 포함, 작업 폴더는 ~/ 안에
```

또는 Python, Node, `claude`, `dtach`가 들어 있는 [Docker](#docker)로 실행하세요.

<details>
<summary><b>macOS 26(Tahoe)의 Homebrew</b></summary>

<br>

이 formula는 Homebrew의 `python@3.12` bottle과 Xcode Command Line Tools가 서로 맞을 때 잘 동작하지만, 둘은 가끔 어긋나요. macOS 26에서는 `pyexpat _XML_SetAllocTrackerActivationThreshold` 심볼 오류로 설치가 실패해요([upstream issue](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). 보통 Xcode와 Command Line Tools를 업데이트하면 해결되지만, `uv tool install aipager`를 쓰면 이 문제가 아예 생기지 않아요. formula는 `dtach`를 Homebrew에서 가져오고, aipager를 Homebrew가 관리하는 Python 환경에 설치해요.

</details>

### <a id="docker"></a>Docker

Python, Node, `claude`, `dtach`가 모두 들어 있는 독립 이미지예요. 호스트에 Python이나 Node를 두고 싶지 않은 VPS, NAS, Raspberry Pi에 잘 맞아요. 여러 아키텍처를 지원해요(amd64, arm64).

```sh
# 1. 설정 마법사를 한 번 실행(대화형)
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. aipager 시작(백그라운드, 자동 재시작)
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

Claude가 고칠 폴더를 `/workspace` 아래에 마운트하세요. `~/.claude` 마운트로 Claude 로그인 정보와 저장된 대화가 함께 들어가요. 로그인하려면 호스트에서 `claude`를 한 번 실행하거나, 컨테이너 안에서 `docker exec -it aipager claude`로 로그인하세요.

태그: `latest`, `1.3`, `1.3.7`(정확한 버전마다 하나, 마이너 버전마다 하나).

### <a id="nix"></a>Nix

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

고정된 nixpkgs로 aipager를 소스에서 빌드해요. `dtach`는 Nix가 주지만 `claude`는 아니라서 따로 설치하세요(`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`, 또는 Anthropic 문서를 따라서).

<details>
<summary><b>NixOS와 Home Manager</b></summary>

<br>

aipager를 flake 입력으로 추가하고, 패키지를 `environment.systemPackages`에 넣으세요:

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

그다음 `aipager service install`이 systemd 사용자 유닛을 만들어요.

</details>

## <a id="everyday-use"></a>매일 쓰기

### <a id="in-telegram"></a>Telegram에서

| 보내기 | 하는 일 |
|---|---|
| `/status` | 이 채팅의 세션을 상태와 버튼과 함께 봐요. |
| `/new x1` | x1이라는 세션을 시작해요. `/new x1 fix the tests`는 첫 메시지도 보내요. |
| `/x1` | 내 메시지가 x1로 가게 해요. `/x1 <message>`는 x1에 메시지 하나를 바로 보내요. |
| `/stop` | 실행 중인 작업을 멈추고 대기열에 있는 것을 버려요. |
| `/mode` | 확인 모드, 자동 모드, 전체 권한 모드 사이를 오가요. |
| `/app` | Mini App을 열어요. |
| `/language` | 이 채팅에서 aipager가 쓸 언어를 골라요. |
| `/update` | aipager와 Claude Code를 업데이트하고 그 자리에서 재시작해요(관리자). |
| `/help` | 작업별 짧은 안내를 봐요. |

모든 명령, 버튼, 설정은 [봇 명령](commands.md)에 있어요.

### <a id="on-the-command-line"></a>명령줄에서

```sh
aipager start                             # aipager 자체를 이 터미널에서 실행
aipager session dev                       # 여기서 dev라는 세션을 시작하거나 다시 연결
aipager session dev --continue            # 이 폴더의 마지막 Claude 대화를 이어서
aipager session dev -- --resume abc1234   # 다른 claude 옵션은 그대로 전달돼요
aipager doctor                            # 설치 상태 확인
```

Claude를 멈추지 않고 세션에서 나가려면 터미널이나 tmux 창을 닫으세요. 세션은 계속 돌아가고, 같은 명령으로 다시 연결돼요.

`aipager service install`은 aipager를 백그라운드에서 계속 돌아가게 해요. Linux에서는 systemd 사용자 유닛(`~/.config/systemd/user/aipager.service`), macOS에서는 launchd 에이전트(`~/Library/LaunchAgents/com.aipager.daemon.plist`)로요. 어느 쪽이든 aipager는 홈 폴더에서 실행되고, 다른 폴더를 고르지 않으면 새 세션도 거기서 시작해요. `aipager service start`, `stop`, `status`, `logs`, `uninstall`로 관리해요.

## <a id="security"></a>보안

- **aipager 서버가 없어요.** aipager는 내 봇 token으로 Telegram Bot API와 바로 이야기해요. aipager 계정도, aipager 서비스도 없어요.
- **열린 포트가 없어요.** aipager는 루프백에서만 들어요. Mini App은 Cloudflare 터널로 밖에 나가는데, 그곳의 모든 요청은 Telegram의 서명이 있어야 하고, `aipager miniapp disable`로 끌 수 있어요.
- **모든 답이 기록돼요.** 허용, 거부, 그리고 모든 답이 `~/.claude/aipager-audit.jsonl`에 덧붙여져요. 그룹에서는 누가 눌렀는지도 함께요.
- **결정은 여전히 Claude Code가 해요.** 무엇을 물어볼지는 내 Claude Code 설정이 정해요. aipager는 질문을 전달할 뿐이고 권한을 올리는 일이 없어요. sudo도 없고, 모든 것이 내 사용자로 실행돼요.

Claude 로그인 정보를 무엇이 지키는지까지 포함한 전체 그림은 [보안 모델](security.md)에 있어요.

## <a id="faq"></a>자주 묻는 질문

<details>
<summary><b>Claude 모바일 앱과 무엇이 다른가요?</b></summary>

<br>

Claude 앱은 Anthropic이 운영하는 어시스턴트와 이야기해요. aipager는 내 컴퓨터에서 돌아가는 Claude Code 세션에 나를 연결해요. 내 저장소, 내 도구, 내 환경 그대로요.

</details>

<details>
<summary><b>내 코드가 컴퓨터 밖으로 나가나요?</b></summary>

<br>

aipager는 내 채팅에서 보이는 것만 내 봇 token으로 Telegram에 보내요. 중간에 aipager 서버는 없어요. Claude Code 자체는 늘 하던 대로 Anthropic과 이야기해요.

</details>

<details>
<summary><b>dtach가 뭔가요?</b></summary>

<br>

터미널을 떼어 내는 작은 프로그램이에요. 터미널이 닫히거나 SSH가 끊겨도 Claude Code를 살려 두니까, 한 시간 뒤에 보낸 답도 살아 있는 세션에 닿아요. aipager와 함께 와요.

</details>

<details>
<summary><b>Claude가 엉뚱한 길로 가면요?</b></summary>

<br>

요청은 내가 누를 때까지 기다리고, `/stop`은 작업을 멈추고 대기열을 비우고, `/kill`은 세션을 끝내고, 끝난 작업의 버튼은 눌러도 거부돼요.

</details>

<details>
<summary><b>어떻게 업데이트하나요?</b></summary>

<br>

`/update`를 보내세요. aipager와 Claude Code를 확인해서 새 버전을 설치하고, 🔄 지금 재시작이 서비스로 실행 중이든 아니든 aipager를 다시 띄워요. 그동안 세션은 계속 돌아가요. 명령줄에서는 `aipager update`가 aipager를 설치한 도구로 업데이트해요.

</details>

<details>
<summary><b>Bedrock이나 Vertex를 써요. 모델 버튼이 동작하나요?</b></summary>

<br>

키보드의 **모델** 메뉴와 Mini App은 Claude Code의 계열 별칭(`sonnet`, `opus`, `haiku`, `fable`, `opusplan`)과 `claude-opus-5-5`, `claude-sonnet-5-5` 같은 고정 모델을 보여 줘요. Anthropic API에서 별칭은 그 계열의 최신 모델을 가리켜요. Bedrock과 Vertex에서는 제공자가 가진 버전에 따라 같은 별칭이 더 오래된 버전을 가리킬 수 있어요. 정확한 모델을 고르려면 `/model <full-id>`를 보내세요.

</details>

<details>
<summary><b>API 키, 게이트웨이, 다른 제공자를 써요. aipager가 동작하나요?</b></summary>

<br>

네. aipager는 설정해 둔 그대로 Claude Code를 실행하고, Claude Code가 시작할 때 묻는 질문(폴더, API 키, 첫 실행 화면)에 대신 답해서 Telegram에서 시작한 세션이 거기서 멈추지 않게 해요. 설정은 `~/.claude/settings.json`의 `env` 블록이나 `~/.config/aipager/daemon.env`에 넣으세요(한 줄에 `KEY=value` 하나, 따옴표 없이). Opus, Sonnet, Haiku 버튼은 `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL`을 따르고, 다른 모델은 이름을 직접 입력하면 돼요. Anthropic은 Claude 모델에서만 Claude Code를 지원해요. [문제 해결](troubleshooting.md#using-an-api-key-a-gateway-or-another-provider)을 참고하세요.

</details>

<details>
<summary><b>비용이 드나요?</b></summary>

<br>

아니요. aipager는 MIT 라이선스이고, 봇은 내 것이며, Claude Code는 원래 쓰던 요금제나 키를 그대로 써요.

</details>

## <a id="documentation"></a>문서

| 페이지 | 내용 |
|---|---|
| [봇 명령](commands.md) | 모든 명령, 버튼, 설정, 명령줄 옵션 |
| [팀 모드와 그룹](groups.md) | 역할, 루트 폴더, 누가 무엇을 누를 수 있는지, 신뢰 모델 |
| [보안 모델](security.md) | 신뢰 경계, 비밀 정보, 감사 로그, 네트워크 |
| [Hook 이벤트](hooks.md) | Claude Code의 hook이 aipager에서 각각 하는 일 |
| [아키텍처](architecture.md) | aipager의 구성 요소, 파일, 소켓 |
| [문제 해결](troubleshooting.md) | `aipager doctor`와 흔한 문제의 해결법 |
| [문제 신고](problem-reports.md) | 신고에 무엇이 담기는지, aipager가 언제 신고를 권하는지 |
| [에이전트로 설치하기](install-with-an-agent.md) | 코딩 에이전트가 따르는 안내서 |

## <a id="contributing"></a>기여하기

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach`는 `dtach-bin` 의존성과 함께 오니 다른 건 필요 없어요. 테스트는 위처럼 메모리 제한을 걸고 실행하세요(macOS에서는 `systemd-run` 없이). 그러지 않으면 폭주한 테스트 하나가 컴퓨터의 메모리를 다 차지할 수 있어요. 릴리스 과정은 [CONTRIBUTING.md](../../../CONTRIBUTING.md)에 있어요.

## <a id="license"></a>라이선스

MIT. [LICENSE](../../../LICENSE)를 보세요.

<br>

<div align="center">
<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="32" height="32" alt=""></a>
<br>
<sub><a href="https://aipager.run">aipager.run</a></sub>
</div>
