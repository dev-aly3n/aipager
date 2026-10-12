# 에이전트로 설치하기

<!-- i18n-source: docs/install-with-an-agent.md @ 6fc084f -->

> 이 페이지는 영어 문서를 번역한 거예요. 내용이 서로 다르면 영어 문서가 맞아요. ([English](../../install-with-an-agent.md))

이 페이지는 사용자를 위해 aipager를 설치하고 설정해 달라는 요청을 받은 AI 코딩 에이전트(Claude Code, 또는 shell을 쓸 수 있는 다른 에이전트)를 위한 거예요. 단계별로 따라 하세요. 여기 나오는 명령은 하나도 질문하지 않고, 대화형 터미널도 필요 없어요.

aipager는 사용자가 Telegram에서 Claude Code 세션을 조종하게 해 줘요. 끝나면 사용자에게는 자기하고만 대화하는 Telegram 봇이 생기고, aipager는 이 컴퓨터에서 백그라운드로 실행돼요.

## <a id="1-what-only-the-person-can-do"></a>1. 사용자만 할 수 있는 일

사용자에게 이 일들을 부탁하세요. 에이전트가 대신할 수 없어요.

1. **봇 만들기**. Telegram에서 [@BotFather](https://t.me/BotFather)를 열고 `/newbot`을 보낸 뒤 이름과 사용자 이름을 정해요. BotFather가 봇 token(`123456789:AA...` 같은 형태)으로 답해요.
2. **봇에서 Start 누르기**. 새 봇을 열고(BotFather가 주는 링크는 `t.me/<bot username>` 형태예요) Start를 눌러요. 이 단계를 하지 않은 사람에게는 봇이 메시지를 보낼 수 없어요.
3. **이 컴퓨터에서 Claude Code에 로그인하기**. 사용자가 이미 여기서 Claude Code를 쓰고 있다면 할 일이 없어요. 화면이 없는 서버라면 사용자가 자기 터미널에서 `claude`를 한 번 실행해 로그인해요(다른 기기에서 열 링크를 보여 줘요). 오래 쓰는 token이 더 좋다면 `claude setup-token` 명령을 실행해요. 이 명령은 token을 출력만 해요. 사용자가 직접 자기 터미널에서 aipager 세션용으로 저장해요(token을 붙여 넣고 Enter, 그다음 Ctrl-D):

   ```sh
   mkdir -p ~/.config/aipager
   (umask 077 && { printf 'CLAUDE_CODE_OAUTH_TOKEN='; cat; } >> ~/.config/aipager/daemon.env)
   ```

   aipager는 이 token을 자기가 시작하는 모든 세션에 넘겨줘요([보안](security.md#the-claude-credential-what-actually-protects-it) 참고). 에이전트는 이 token을 절대 읽지 않아요.

## <a id="2-how-the-person-gives-you-the-token"></a>2. 사용자가 token을 건네는 방법

봇 token을 가진 사람은 누구나 그 봇을 조종할 수 있어요. 사용자에게 아래 두 가지 방법을 알려 주고 고르게 하세요.

- **가장 안전한 방법: 에이전트가 절대 읽지 않는 파일**. 사용자가 자기 터미널에서 실행해요:

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token)
  chmod 600 ~/.config/aipager/bot-token
  ```

  그다음 token을 붙여 넣고 Enter, 그다음 Ctrl-D를 눌러요. 이 파일은 사용자만 읽을 수 있어요(권한 600). 설정이 성공하면 지워도 돼요(aipager가 자기 설정에 사본을 보관해요). 사용자가 경로(`~/.config/aipager/bot-token`)를 알려 줘요. token은 대화에 들어오지 않고, 에이전트는 그 경로를 `--token-file` 옵션에 넘겨요.
- **가장 빠른 방법: 대화에 바로 붙여 넣기**. 이것도 되지만 무슨 뜻인지 분명히 말하세요. token이 에이전트의 대화 기록에 남고 모델 제공자에게 전송돼요. 이 기록이 나중에 공유될 수 있다면, 사용자는 나중에 @BotFather에게 `/revoke`를 보내고 `aipager setup --force` 명령으로 새 token을 설정해야 해요(3.2단계에 `--force` 추가). 사용자가 이 방법을 고르면 token을 같은 파일에 저장하고 출력하지 마세요. 가능하면 에이전트 자체의 파일 쓰기 도구를 쓰고, 그다음 `chmod 600 ~/.config/aipager/bot-token` 명령을 실행하세요. shell밖에 없다면 here-document로 stdin을 통해 넘기세요. 그러면 token이 파일을 쓰는 명령의 인수가 되지 않아요:

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token) <<'EOF'
  <the token>
  EOF
  chmod 600 ~/.config/aipager/bot-token
  ```

  그다음 아래처럼 `--token-file` 옵션을 쓰세요.

사용자의 Telegram 사용자 ID(채팅 ID이기도 해요. `123456789` 같은 숫자예요)는 비밀이 아니에요. 사용자가 모르면 에이전트가 찾을 수 있어요(3.3단계).

## <a id="3-your-steps"></a>3. 에이전트가 할 단계

### <a id="31-install-aipager"></a>3.1 aipager 설치

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

uv나 pipx 중 이미 있는 것을 쓰고(macOS에서는 Homebrew도 써요), 둘 다 없으면 먼저 uv를 설치해요. `uv tool install aipager`나 `pipx install aipager`를 직접 실행해도 돼요. 그 뒤에 `aipager`를 찾을 수 없으면 shell의 `PATH`에 `~/.local/bin`을 추가하세요(`export PATH="$HOME/.local/bin:$PATH"`).

aipager가 이미 설치되어 있었다면 이 명령들은 업그레이드하지 않아요. `aipager setup --help`가 실행되는지 확인하세요. 실행되지 않으면 설치된 aipager가 너무 오래된 거예요. `aipager update`를 실행하고 다시 확인하세요. `aipager update`가 aipager를 재시작해야 한다고 하면 사용자에게 알리세요. 직접 재시작하지는 마세요(4절 참고).

`aipager config`는 실행하지 마세요. 사람이 쓰는 대화형 설정 마법사예요.

### <a id="32-set-aipager-up"></a>3.2 aipager 설정

```sh
aipager setup --token-file ~/.config/aipager/bot-token --chat-id 123456789 --service --json
```

사용자의 실제 ID와 token 파일 경로를 쓰세요. `--token-file` 대신 `--token-stdin`으로 token을 파이프로 넘길 수도 있어요(예: `--token-stdin < path`). 파이프는 닫혀야 하고, setup은 최대 30초 기다려요.

setup은 Telegram에서 token을 확인하고, 이 컴퓨터를 점검하고, 사용자에게 테스트 메시지("aipager is set up for you. ...")를 보낸 다음에야 설정을 쓰고, `~/.claude/settings.json` 파일에 Claude Code hook을 추가하고, `--service`가 있으면 백그라운드 서비스를 설치하고 시작해요. 확인 하나라도 실패하면 아무것도 쓰지 않아요.

`--dry-run`을 붙이면 무엇이 바뀔지 먼저 볼 수 있어요. `--role admin`은 사용자를 소유자 대신 관리자로 만들어요(기본값인 소유자가 자기 컴퓨터를 설정하는 사람에게 맞아요).

### <a id="33-if-the-person-does-not-know-their-telegram-id"></a>3.3 사용자가 자기 Telegram ID를 모를 때

```sh
aipager setup detect-chat --token-file ~/.config/aipager/bot-token --json
```

이 명령이 기다리는 동안(최대 5분, `--timeout SECONDS`로 바꿀 수 있어요) 사용자에게 봇에서 Start를 누르라고(또는 아무 메시지나 보내라고) 하세요. 가장 최근에 보낸 사람을 `candidate`에 출력하고(`id`, `first_name`, `last_name`, `username`), 아무것도 저장하지 않아요. 이름과 사용자 이름을 사용자에게 보여 주고 “이게 맞나요?”라고 물어보세요. 사용자가 확인한 다음에만 `candidate.id`를 `--chat-id`로 해서 3.2단계를 실행하세요. `other_candidates`가 0이 아니면 다른 사람도 봇에게 메시지를 보낸 거예요. 꼭 확실하게 확인하세요.

### <a id="34-read-the-result"></a>3.4 결과 읽기

`--json`이 있으면 setup은 stdout에 JSON 객체를 딱 하나 출력해요. `exit_code`(프로세스 종료 코드와 같아요), `status`, `error`, `message`, `fix`, `next_step`을 읽으세요. stdout이 JSON이 아니면(모르는 옵션, `setup` 뒤의 잘못된 단어, 또는 aipager가 너무 오래돼서 `setup`이 없는 경우) 종료 코드는 2이고 이유는 stderr에 일반 텍스트로 나와요. 그것을 읽고, 오래된 aipager라면 3.1단계를 보세요.

| 종료 코드 | `error` | 할 일 |
|---|---|---|
| 0 | | `installed`, `updated`, `unchanged`: 끝났어요. 3.5단계로 가세요. `dry_run`: 아무것도 쓰지 않았어요. `--dry-run` 없이 같은 명령을 다시 실행하세요. detect-chat `found`: 3.3단계처럼 사용자에게 확인받고 3.2단계를 실행하세요. |
| 1 | `config_malformed`, `ambiguous_install`, `settings_invalid`, `test_send_failed`, `write_failed`, `service_failed`, `internal_error` | `message`와 `fix`를 사용자에게 보여 주세요. `ambiguous_install`: 사용자가 `aipager config`에서 고쳐요. 파일을 직접 편집하지 마세요. |
| 2 | `usage`, `token_malformed`, `token_stdin_timeout`, `bad_chat_id`, ... | 입력이 잘못됐어요. `fix`가 어떤 옵션인지 알려 줘요. `token_malformed`: 파일에 든 것이 봇 token이 아니에요. 사용자에게 확인해 달라고 하세요. |
| 3 | `deps_missing` | `fix`를 따르고(빠진 항목마다 `deps.items`에 각자의 `fix`가 있어요) setup을 다시 실행하세요. |
| 4 | `token_rejected` | Telegram이 token을 거부했어요. 사용자에게 token을 다시 받으세요(또는 @BotFather에서 새 token을 받으세요). |
| 5 | `chat_not_started`, `bot_blocked` | 사용자에게 봇을 열어 Start를 누르라고(또는 차단을 풀라고) 한 뒤 setup을 다시 실행하세요. |
| 6 | `existing_install` | aipager가 이미 다른 token, 채팅, 역할로 설정되어 있어요. 무엇이 다른지 사용자에게 말하고, `--force`를 붙이기 전에 먼저 동의를 받으세요. |
| 7 | `telegram_unreachable` | 네트워크 문제거나 Telegram이 바빠요. 1분 기다렸다가 다시 시도하세요. |
| 8 | `detect_timeout` | detect-chat이 메시지를 하나도 보지 못했어요. 사용자에게 Start를 다시 누르라고 하고 다시 실행하세요. `updates_backlog` 경고가 있으면 Start를 다시 눌러도 소용없어요. 사용자에게 숫자 ID를 물어보세요. |
| 9 | `daemon_running` | detect-chat: 이 봇의 aipager가 이미 실행 중이고 메시지를 그쪽이 받고 있어요. 사용자에게 숫자 ID를 물어보세요(또는 사용자에게 먼저 물어본 뒤 `aipager service stop`으로 멈추세요). |
| 10 | `updates_conflict` | detect-chat: 다른 프로그램이 이 봇의 메시지를 읽고 있어요. 사용자에게 숫자 ID를 물어보세요. |
| 130 | `interrupted` | Ctrl-C로 중단됐어요. |

`daemon.restart_needed`가 true이면 aipager가 이 변경을 쓰려면 재시작해야 하거나, setup이 aipager가 실행 중인지 알 수 없었다는 뜻이에요(경고 `daemon_unknown`). 어느 경우인지 알려 주는 `next_step`을 따르세요(포그라운드에 남는 `aipager start`를 제안하면 대신 `aipager service install`을 쓰세요). 사용자에게 알리고, 사용자가 동의할 때만 재시작하세요(`aipager service stop` 다음 `aipager service start`). 그리고 사용자가 aipager를 통해 쓰고 있는 세션 안에서는 절대 재시작하지 마세요(4절 참고). 오류, 경고, JSON 키의 전체 목록은 [setup 명령 레퍼런스](commands.md#command-line-aipager-setup-for-coding-agents)에 있어요.

### <a id="35-check-the-install"></a>3.5 설치 확인

```sh
aipager doctor --json
```

읽기만 하고 아무것도 보내지 않아요. 종료 코드 0은 실패한 확인이 없다는 뜻이에요. `status`가 `fail`(또는 `warn`)인 확인마다 `title`, `detail`, `fix`를 사용자에게 보여 주세요.

### <a id="36-report-back-to-the-person"></a>3.6 사용자에게 보고하기

몇 줄로 사용자에게 알려 주세요:

- 봇(`@` + `bot_username`)과 테스트 메시지가 보내졌는지(`test_message`);
- 무엇을 설정했는지(`changed`, 그리고 `service.result`);
- Claude 세션을 시작하는 방법: Telegram에서 봇에게 `/new`를 보내거나 터미널에서 `aipager session <name>`을 실행해요;
- `doctor`가 짚은 문제와, 무언가를 요구한다면 `next_step`.

## <a id="4-what-you-must-not-do"></a>4. 절대 하면 안 되는 일

- token을 출력하거나 `echo`나 `cat`으로 보여 주지 말고, 명령줄에 절대 넣지 마세요(`--token`은 어차피 거부돼요). 파일이나 stdin으로만 넘기세요.
- `~/.config/aipager/`(2단계의 token 파일은 예외)나 `~/.claude/settings.json` 파일을 직접 편집하지 마세요. 이 파일들은 `aipager setup`과 `aipager config`가 써요.
- 사용자에게 묻지 않고 `--force`를 붙이지 마세요. 사용자의 기존 token, 채팅, 역할을 바꿔 버려요.
- 에이전트 자신이 있는 Claude Code 세션을 재시작하거나 종료하지 마세요. 처음 설치할 때 에이전트의 세션은 aipager 아래에 있지 않고, 그럴 필요도 없어요. 새 세션은 Telegram의 `/new`나 `aipager session <name>`으로 만들어요. aipager가 이미 설정되어 있고 사용자가 aipager를 통해 에이전트와 대화하고 있다면(예: 새 token으로 `setup --force`를 실행하려는 경우), aipager를 직접 멈추거나 재시작하지 마세요. 에이전트의 작업과 답변이 끊겨요. 작업을 마친 뒤 사용자에게 직접 재시작해 달라고 하세요.

## <a id="5-groups"></a>5. 그룹

`aipager setup`은 사용자와 봇의 개인 채팅을 설정해요. 여러 사람이 각자 역할을 가지는 Telegram 그룹을 추가하려면 사용자가 `aipager config`를 실행해요(그룹 설정은 아직 스크립트로 할 수 없어요). [팀 모드와 그룹](groups.md)을 보세요.

## <a id="see-also"></a>함께 보기

- [봇 명령과 setup 명령](commands.md)
- [문제 해결](troubleshooting.md)
- [보안 모델](security.md)
