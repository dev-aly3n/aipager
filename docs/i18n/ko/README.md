# aipager

<!-- i18n-source: README.md @ 6fc084f -->

> 이 페이지는 영어 문서를 번역한 거예요. 내용이 서로 다르면 영어 문서가 맞아요. ([English](../../../README.md))

[![PyPI](https://img.shields.io/pypi/v/aipager?label=pypi&color=3775A9)](https://pypi.org/project/aipager/)
[![Python](https://img.shields.io/pypi/pyversions/aipager?color=3775A9)](https://pypi.org/project/aipager/)
[![License](https://img.shields.io/badge/license-MIT-black)](../../../LICENSE)

Telegram으로 [Claude Code](https://claude.com/claude-code) 세션을 원격으로 조종해요. Claude는 내 컴퓨터의 분리된 터미널(`dtach`)에서 실행되고, 나는 휴대폰에서 Claude를 써요. Claude의 답변을 읽고, 메시지를 보내고, 권한 요청과 질문에 답하고, 세션 사이를 오갈 수 있어요. SSH 연결을 계속 열어 둘 필요가 없어요.

[aipager.run](https://aipager.run) · [문서](#docs) · [변경 기록](../../../CHANGELOG.md) · [GitHub 이슈](https://github.com/dev-aly3n/aipager/issues)

**다른 언어로 읽기:** [English](../../../README.md) · [Español](../es/README.md) · [Русский](../ru/README.md) · [Português (Brasil)](../pt-BR/README.md) · [简体中文](../zh-Hans/README.md) · [Français](../fr/README.md) · [Deutsch](../de/README.md) · [العربية](../ar/README.md) · [Bahasa Indonesia](../id/README.md) · [Українська](../uk/README.md) · [Italiano](../it/README.md) · [Türkçe](../tr/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

## <a id="install"></a>설치

코딩 에이전트(Claude Code, 또는 shell을 쓸 수 있는 다른 에이전트)가 있나요? [@BotFather](https://t.me/BotFather)에서 봇을 만들고, 봇에서 Start를 누르고, 이 컴퓨터의 Claude Code가 내 계정으로 로그인되어 있는지 확인하세요. 봇 token은 에이전트가 읽을 필요가 없는 파일에 저장하세요(token을 붙여 넣고 Enter, 그다음 Ctrl-D):

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

그다음 에이전트에게 이 메시지를 주세요:

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

내 Telegram ID를 모르면 에이전트에게 그렇게 말하세요. 에이전트가 찾아낼 수 있어요.

에이전트가 따르는 안내(token을 안전하게 건네는 방법 포함)는 [에이전트로 설치하기](install-with-an-agent.md)에 있어요. 직접 설치하려면 계속 읽으세요.

Linux나 macOS에서, 어떤 아키텍처든 돌아가요. `dtach`는 의존성인 [`dtach-bin`](https://pypi.org/project/dtach-bin/)으로 자동 설치돼요. 시스템 패키지를 따로 설치할 필요가 없어요.

### <a id="one-line-install-recommended"></a>한 줄 설치 (추천)

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

`uv`가 있으면 `uv`를, 없으면 `pipx`를, 그것도 없으면(macOS에서만) Homebrew를 써요. 아무것도 없으면 먼저 `uv`(Astral의 Python 도구 관리자)를 설치하고 그것으로 aipager를 설치해요.

### <a id="uv-recommended-on-macos"></a>uv (macOS에서 추천)

```sh
uv tool install aipager     # uv가 이미 있다면
```

또는 먼저 uv를 설치하세요:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install aipager
```

uv는 자체 Python 인터프리터를 함께 가져와서, 시스템 Python이 어떤 상태든 모든 버전의 macOS와 Linux에서 돌아가요. 그래서 아래 Homebrew 부분에서 설명하는 Homebrew Python과 Xcode의 충돌을 피할 수 있어요.

### <a id="pipx"></a>pipx

```sh
pipx install aipager
```

### <a id="homebrew-tap-macos-linuxbrew"></a>Homebrew tap (macOS, Linuxbrew)

> **참고**: macOS에서는 `uv tool install aipager`를 추천해요. brew formula는 Homebrew의 `python@3.12`가 내 Xcode / Command Line Tools와 맞을 때는 잘 돌아가지만, 둘이 가끔 어긋나요. 가장 최근에는 **macOS Tahoe (26.x)** 환경에서 설치가 심볼 오류 `pyexpat _XML_SetAllocTrackerActivationThreshold`로 멈췄어요([업스트림 이슈](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). 보통 Xcode와 Command Line Tools를 업데이트하면 해결되지만, uv를 쓰는 편이 더 간단해요.

```sh
brew install dev-aly3n/tap/aipager
```

`dtach`는 Homebrew의 기본 formula에서 가져오고, aipager는 Homebrew가 관리하는 Python 가상 환경에 설치해요.

### <a id="docker"></a>Docker

python, node, `claude`, `dtach`가 들어 있는 미리 만든 이미지예요. 시스템에 Python이나 Node를 설치하고 싶지 않은 VPS, NAS, 라즈베리 파이에 알맞아요. 여러 아키텍처(amd64, arm64)를 지원해요.

```sh
# 1. 설정 마법사를 한 번 실행 (대화형)
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. aipager 실행 (백그라운드, 자동 재시작)
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

Claude가 수정할 폴더를 `/workspace`에 마운트하세요. `~/.claude`를 마운트하면 Claude 로그인 정보와 대화 기록이 함께 들어가요. 로그인하려면 컴퓨터에서 `claude`를 한 번 실행하거나, `docker exec -it aipager claude` 명령으로 컨테이너 안에서 로그인하세요.

태그: `latest`, `1.1`, `1.1.0` (정확한 버전 하나, 마이너 버전 하나).

### <a id="nix-flake"></a>Nix flake

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

nixpkgs에서 고정한 의존성으로 aipager를 소스에서 빌드해요. `dtach`는 Nix가 제공해요. `claude`는 Nix가 제공하지 **않아요**. 따로 설치하세요(`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`, 또는 Anthropic 문서를 따르세요).

선언적인 NixOS나 Home Manager 설정에서는 aipager를 flake 입력으로 추가하고, 그 패키지를 `environment.systemPackages`에 넣으세요:

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

그다음 `aipager service install` 명령으로 systemd 사용자 서비스를 설정하세요.

### <a id="snap"></a>Snap

```sh
snap install aipager
```

python, node, `claude`, `dtach`, aipager가 들어 있는 엄격하게 격리된 snap이에요. snap 샌드박스 때문에 작업 폴더는 `~/` 아래에 있어야 해요(예: `~/projects/foo`). 매니페스트는 [`packaging/snap/`](../../../packaging/snap/)에 있어요.

## <a id="configure"></a>설정

```sh
aipager config
```

대화형 설정 마법사예요. Telegram 봇 token([@BotFather](https://t.me/BotFather)에서 받은 것)과 채팅 ID를 묻고, 둘을 확인한 다음, 필요한 hook을 `~/.claude/settings.json` 파일에 자동으로 추가해요. 파일을 직접 편집할 일은 없어요.

스크립트나 코딩 에이전트로 다른 사람에게 aipager를 설정해 주나요? `aipager setup --token-file FILE --chat-id ID` 명령은 같은 일을 질문 없이 해요([setup 명령](commands.md#command-line-aipager-setup-for-coding-agents)과 [에이전트 안내](install-with-an-agent.md)를 보세요).

## <a id="run"></a>실행

```sh
aipager start
```

aipager가 포그라운드에서 실행돼요. 다른 터미널에서 Claude 세션을 시작하세요:

```sh
aipager session dev
```

Claude Code를 실행하는 `claude-dev`라는 dtach 세션을 만들어요(이미 있으면 거기에 연결해요). aipager가 몇 초 안에 알아채고, Telegram에 세션이 나타나기 시작해요. Telegram에서 세션을 시작할 수도 있어요. 봇에게 `/new dev`를 보내세요. 나중에 다시 연결하려면 같은 명령을 다시 실행하세요. Claude를 멈추지 않고 나가려면 터미널이나 tmux 창을 닫으면 돼요(세션은 계속 실행돼요).

dtach 세션이 이미 닫혔고(예: 컴퓨터를 재시작한 경우) 디스크에 있는 Claude 대화를 이어 가고 싶다면 `--continue` 옵션을 붙이세요:

```sh
aipager session dev --continue    # 이 폴더의 가장 최근 claude 대화
```

끝에 `--resume <session-id>`(또는 다른 claude 인수)를 붙일 수도 있어요:

```sh
aipager session dev -- --resume abc1234
```

### <a id="run-as-a-service-survives-logout"></a>서비스로 실행 (로그아웃해도 계속 실행)

```sh
aipager service install
```

Linux에서는 systemd 사용자 서비스를 `~/.config/systemd/user/aipager.service`에 쓰고 시작해요. macOS에서는 launchd plist를 `~/Library/LaunchAgents/com.aipager.daemon.plist`에 쓰고 시작해요. 하위 명령: `start`, `stop`, `status`, `logs`, `uninstall`.

## <a id="team-mode-groups-and-other-people"></a>팀 모드 (그룹과 다른 사람)

기본적으로 aipager는 개인 봇이에요. 나와 봇만 있는 개인 채팅 하나예요. 함께 쓰려면 Telegram 그룹, 다른 사람과 봇의 개인 채팅, 또는 둘 다를 추가하세요. 그룹이라면: 봇을 그룹에 추가하고(그룹의 상태 표시줄을 고정할 수 있도록 관리자로), `aipager config` 명령을 다시 실행해서 **Add a group scope**를 고르세요. 설정 마법사가 나(내 개인 채팅의 소유자)를 그룹의 소유자로 제안하고, 멤버마다 역할(`owner`, `admin`, `user`, `read_only`)을 정하고, 멤버의 루트 폴더를 물은 다음, 재시작 없이 aipager 설정을 다시 불러와요. 그룹에서 봇은 명령, 자기 메시지에 대한 답장, 자기를 멘션한 메시지(`@aipagerbot fix the tests`, `/jim run the tests`)에만 반응하고, 모든 메시지는 보낸 사람의 역할로 실행돼요. `user`나 `read_only` 멤버는 자기 채팅의 루트 폴더(기본값 `~/aipager-users/<label>`) 안에서만 세션을 시작하고 유지해요. **멤버를 추가하면 그 사람이 내 컴퓨터에서 코드를 실행할 수 있게 돼요**. 전체 신뢰 모델은 [팀 모드와 그룹](groups.md)에 있어요.

## <a id="what-it-does"></a>할 수 있는 일

- 각 Claude Code 세션이 무엇을 하는지 Telegram에서 보여 줘요. 작업 중인지 준비됨인지, 쓰고 있는 도구, 컨텍스트 비율, 비용, 바뀐 줄 수를 작업마다 카드 하나로 보여 주고, 마지막에 Claude의 답변이 와요
- Claude가 작업 중이어도 내 메시지를 **바로** Claude에게 전달해요. 여러 개를 보내도 돼요. 터미널에서 입력할 때처럼 Claude가 직접 대기열에 넣어요. 👀는 전달됨, 👍는 Claude가 가져갔다는 뜻이에요
- 답하지 않은 권한 요청이나 질문이 있으면 내 메시지를 잠시 보류해서, 메시지가 그 요청에 대한 답으로 읽히는 일이 없게 해요. 답하고 나면 보내요
- Claude의 권한 요청과 질문을 버튼으로 보여 줘요(허용, 거부, 항상 허용, 선택지마다 버튼 하나). 끝난 작업에 남은 버튼은 지금 실행 중인 작업에 작용하지 않고 거절해요
- 세션마다 권한 모드가 세 가지 있고 `/mode` 명령으로 바꿔요. 💬 확인 모드(Claude가 작업마다 먼저 물어봐요), 기본값인 🤖 자동 모드(Claude Code 자체의 안전 검사가 안전한 작업은 실행하고 위험한 작업은 멈춰서 내가 결정하게 해요), 🔓 전체 권한 모드(모든 작업이 확인 없이 실행돼요. 관리자용이고, 내 개인 채팅에서는 나도 쓸 수 있어요)
- 채팅에서 세션을 시작해요. `/new x1`은 세션을 바로 시작하고(`/new x1 fix the tests`는 첫 메시지도 보내요), 모드, 모델, 폴더는 `/settings`에서 가져오고, 바꿀 수 있는 버튼이 함께 와요
- 사진, 파일, 음성 메시지를 받아요(음성 메시지는 내 컴퓨터에서 글로 바꿔요. 음성 메시지 기능을 설치해야 해요)
- 컨텍스트가 거의 찼을 때, 대화를 압축할 때, 세션이 끝났을 때, 세션이 너무 오래 조용할 때 알려 줘요
- 봇 하나로 여러 세션을 동시에 실행해요. 역할과 도구별 규칙이 있는 팀 모드도 선택할 수 있어요([팀 모드와 그룹](groups.md))
- 읽기 전용 관찰 봇도 선택할 수 있어요
- English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی, Tiếng Việt로 말해요. aipager 자체의 글에만 적용되고, 내 세션이나 Claude의 답변은 절대 건드리지 않아요([언어](commands.md#languages)). 이 README와 문서도 번역되어 있어요(위의 **다른 언어로 읽기** 참고)

### <a id="mini-app"></a>Mini App

`/app` 명령은 Telegram 안에서 대시보드를 열어요. 중지, 종료, 재시작, 이름 바꾸기, 모드 전환이 있는 내 세션, `Write`/`Edit` 변경 사항을 보는 diff 뷰어, 설정, 그리고 소유자에게는 “문제 신고”가 있어요. aipager가 직접 제공하고 **기본으로 켜져 있어요**. 모든 요청은 Telegram의 `initData` 서명으로 확인해요. 여러 채팅(내 개인 채팅과 그룹)에 속해 있으면 맨 위에 채팅 이름 줄이 나와서 서로 오갈 수 있어요. `aipager miniapp enable|disable|status` 명령으로 관리하거나, 관리형 터널 대신 내 주소를 가리키게 할 수 있어요. [보안 모델](security.md#mini-app-tunnel)을 보세요.

### <a id="note-on-model-buttons-bedrock--vertex-users"></a>모델 버튼에 대해 (Bedrock과 Vertex 사용자)

고정 키보드의 **모델** 하위 메뉴(와 Mini App의 모델 선택기)는 Claude Code의 계열 별칭 `sonnet`, `opus`, `haiku`, `fable`, `opusplan`과 `claude-opus-5-5`, `claude-sonnet-5-5` 같은 고정 모델을 보여 줘요(전체 목록은 [고정 키보드](commands.md#persistent-keyboard)에 있어요). Anthropic API에서는 별칭이 항상 그 계열의 최신 모델을 가리켜요. **Bedrock**과 **Vertex**에서는 같은 별칭이 서비스 제공자가 제공하는 것에 따라 더 오래된 버전을 가리킬 수 있어요. 이런 서비스를 쓰면서 특정 모델이 필요하다면 채팅에서 `/model <full-id>`를 보내세요.

## <a id="developing-locally"></a>로컬에서 개발하기

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach`는 의존성인 `dtach-bin`과 함께 오니까 다른 건 필요 없어요. 테스트는 위처럼 메모리 한도 아래에서 실행하세요(macOS에서는 `systemd-run`을 빼세요). 그렇지 않으면 통제를 벗어난 테스트 하나가 컴퓨터 메모리를 다 써 버릴 수 있어요.

릴리스 절차는 [CONTRIBUTING.md](../../../CONTRIBUTING.md)에 있어요.

## <a id="license"></a>라이선스

MIT. 전문은 [LICENSE](../../../LICENSE)에 있어요.

## <a id="docs"></a>문서

| 페이지 | 내용 |
|---|---|
| [에이전트로 설치하기](install-with-an-agent.md) | 코딩 에이전트가 사용자를 위해 aipager를 설치하고 설정할 때 따르는 단계 |
| [아키텍처](architecture.md) | 프로그램이 어떻게 짜여 있는지, 구성 요소 그림, 파일과 소켓의 위치 |
| [Hook 이벤트](hooks.md) | aipager가 Claude Code에서 받는 모든 이벤트와 그 필드 |
| [봇 명령](commands.md) | 명령, 버튼, Mini App, 음성 메시지와 파일, 모든 `aipager` 명령, 환경 변수로 하는 설정 |
| [팀 모드와 그룹](groups.md) | 여러 사람 설정, 역할, 규칙, 누가 무엇을 했는지 |
| [관찰 봇](observers.md) | 알림 사본을 받는 읽기 전용 봇 |
| [문제 해결](troubleshooting.md) | 흔한 문제와 `aipager doctor` 해결표 |
| [문제 신고](problem-reports.md) | 문제 신고 보고서에 무엇이 들어 있고, 어디로 가고, aipager가 언제 보내자고 제안하는지 |
| [보안 모델](security.md) | 신뢰 경계, 비밀 정보, 감사 로그, 네트워크 연결 |
