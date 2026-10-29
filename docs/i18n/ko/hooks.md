# Hook 이벤트

<!-- i18n-source: docs/hooks.md @ 92c37755 -->

> 이 페이지는 영어 문서를 번역한 거예요. 내용이 서로 다르면 영어 문서가 맞아요. ([English](../../hooks.md))

Claude Code는 작업의 정해진 순간에 hook 명령을 실행해요. 메시지가 제출됐을 때, 도구가 곧 실행될 때, 도구 실행이 끝났을 때, 작업이 끝났을 때 등이에요. aipager는 이 모든 순간에 hook을 걸고, 그것을 Telegram의 활동으로 바꿔요. 작업 카드, 권한 요청, 답변, 알림, 감사 로그예요.

## <a id="how-it-is-wired"></a>어떻게 연결되나요

`aipager config`(또는 `aipager setup`)는 `~/.claude/settings.json` (또는 `$CLAUDE_CONFIG_DIR/settings.json`, 데몬의 세션이 읽는 파일) 파일에 aipager 항목을 추가하고, aipager는 시작할 때마다 그 항목을 확인해요:

- 아래 16개 이벤트마다 `aipager-hook` 명령이 하나씩 있고, 도구 이벤트(`PreToolUse`, `PostToolUse`, `PermissionRequest`)의 matcher는 `*`이에요.
- `PreModelSwitch`는 5초 제한 시간이 있고, `aipager config`와 `aipager setup`으로 설정하면 `PermissionRequest`는 30초 제한 시간이 있어요(hook 자체는 최대 20초 기다려요, 아래 참고). 나머지 항목은 Claude Code의 기본값을 써요.
- `statusLine` 명령, 즉 `aipager-statusline`.

`aipager uninstall`은 이 항목들을 다시 지워요([명령](commands.md#command-line-aipager-uninstall) 참고).

Claude Code는 이벤트마다 새 `aipager-hook` 프로세스(`aipager.dtach.notify_hook:main`)를 시작해요. 이 프로세스는 stdin에서 이벤트 JSON을 읽고, 필드 두 개를 더한 다음(아래 참고), Unix 소켓으로 datagram 하나에 담아 aipager에 보내요. 소켓은 이 순서로 골라요. 격리된 테스트 인스턴스의 `$AIPAGER_INSTANCE_DIR/aipager.sock`, `$AIPAGER_SOCKET_PATH`, `$XDG_RUNTIME_DIR/aipager.sock`, 또는 `/tmp/aipager.sock`이에요. 듣고 있는 aipager가 없으면 이벤트는 버려지고, 세션 모니터가 세션의 파일에서 빈 부분을 채워요. 보내기는 aipager에 아직 읽지 않은 이벤트가 길게 쌓여 있을 때만 기다려요(Linux에서).

datagram 하나에 담기에 너무 큰 이벤트(240 KiB 초과, 또는 시스템 자체 한도 초과: Linux에서 약 208 KiB, macOS에서 기본 2 KiB)는 대신 파일로 전달돼요. hook이 그 파일을 소켓 옆 `aipager-spill-<uid>` 폴더에 자기 사용자만 접근할 수 있게 쓰고, 파일 이름을 담은 짧은 datagram을 보내요. aipager는 파일을 바로 읽고 지운 다음, 이벤트가 통째로 온 것처럼 같은 순서로 처리해요. 4 MiB가 넘는 이벤트는 여전히 버려져요. 이 폴더에 대한 검사는 [보안](security.md)을 참고하세요.

대부분의 이벤트에서 Claude Code가 프로세스를 시작하는 데 20~25밀리초쯤 걸려요. 몇 가지는 aipager의 규칙 코드도 불러와요. 도구 호출 전(안전 검사, [PreToolUse](#pretooluse) 참고) 약 70밀리초, 작업이 끝날 때(작업의 열림 표시를 지움) 약 55밀리초, 그리고 `UserPromptSubmit`(작업의 규칙을 씀)이에요. `PermissionRequest`는 일부러 내가 누를 때까지 최대 20초 기다리고, 도구 호출은 aipager가 그 세션에 메시지를 입력하는 동안 기다려요(최대 5초, [PreToolUse](#pretooluse) 참고).

hook은 자기 메모리를 1 GB로 제한해요. 이 한도에 닿은 hook은 오류로 끝나고(`PreToolUse`라면 Claude Code가 거부로 받아들이는 종료 코드예요), aipager에 `hook_memory_cap_hit` datagram을 보내요. 이것은 채팅에 알림이 되고, 설치 소유자에게는 “🐞 이 문제 신고” 버튼이 함께 와요([문제 신고](problem-reports.md) 참고). 세션 환경에 `AIPAGER_DEBUG=1`을 설정하면 hook과 상태 줄이 진단 정보를 stderr에 출력해요.

## <a id="what-the-daemon-receives"></a>aipager가 받는 것

이 datagram은 Claude Code 자체의 hook JSON에 aipager의 필드를 더한 거예요(몇몇 이벤트에는 필드가 더 있고, 아래 각 이벤트에서 설명해요):

| 필드 | 의미 |
|---|---|
| `hook_event_name` | 이벤트(Claude Code의 필드예요. aipager는 이것으로 나눠 보내요). |
| `session_id`, `transcript_path`, `cwd`, ... | Claude Code 자체 필드, 그대로 유지해요. `session_id`는 Claude Code의 대화 ID예요. |
| `session` | aipager가 추가: aipager가 세션에 붙인 이름 `claude-<name>`, aipager가 세션을 시작할 때 설정하는 변수 `CLAUDE_DTACH_SESSION`에서 와요. aipager가 시작하지 않은 Claude Code 세션의 이벤트에는 이름이 없고, aipager는 그런 이벤트를 무시해요. |
| `sl_tokens` | aipager가 추가, `MessageDisplay`를 뺀 모든 이벤트에, 그 세션의 상태 줄 파일이 있을 때: `context_pct`, `total_output`, `total_input`, `current_output`, `lines_added`, `lines_removed`. |
| `claude_pid` | aipager가 추가, 세션 자신의 이벤트에: 그 세션 `claude`의 PID(Claude Code의 `CLAUDE_PID`)예요. aipager는 이것으로 Claude Code 세션 목록에서 그 claude를 찾아요. |
| `bg_job` | aipager가 추가, 그 세션 몫으로 치는 Claude Code 백그라운드 작업의 모든 이벤트에(아래 참고): 그 작업의 ID예요. |

도구 이벤트에는 `tool_name`과 `tool_input`도 있어요. 서브에이전트(subagent) 안에서 일어난 모든 이벤트에는 `agent_id`가 있고(서브에이전트 이벤트에는 `agent_type`도 있어요), 메인 루프 자체의 이벤트에는 없어요. aipager는 이것으로 에이전트의 도구 호출을 그 에이전트 자기 줄에 넣어요.

Claude Code의 백그라운드 작업도 hook을 실행해요. 입력란이 비어 있을 때 ←를 누르면 세션의 대화가 백그라운드 작업으로 옮겨지고(Claude Code의 에이전트 보기), Claude Code는 다음 작업을 위해 여분의 백그라운드 프로세스도 준비해 둬요. 이들의 hook은 `CLAUDE_JOB_DIR`이 설정된 채로 실행되고, Claude Code 백그라운드 서비스를 시작한 claude가 무엇이든 그 세션 이름을 물려받아서, 그 이름으로는 이들에 대해 아무것도 알 수 없어요. 그래서 hook은 백그라운드 작업을, 자기 claude가 대화를 그 작업으로 옮긴 세션의 몫으로만 쳐요. 그 세션은 작업의 연결 파일(`claude-bgjob-<job>.json`, aipager가 옮겨 간 것을 보면 써요. [아키텍처](architecture.md#file-and-socket-layout) 참고)에서 찾고, 없으면 Claude Code 세션 목록과 aipager의 PID 목록을 함께 보고 찾아요. 그런 작업의 이벤트는 `bg_job`이 설정된 채 그 세션의 이벤트로 aipager에 가서, 답변, 카드, 요청이 그 세션의 채팅에 도착해요. 다른 백그라운드 프로세스는 모두 남의 것이에요. 그 hook은 아무것도 쓰지 않고, 보내지 않고, 출력하지 않아요. 다만 `PreToolUse`는 물려받은 세션 규칙으로 그 도구 호출을 계속 검사하고 거부를 출력해요(그 세션이 허용할 것보다 더 허용하는 일은 절대 없어요). aipager는 백그라운드에 있는 세션과 짝지을 수 없는 `bg_job` 이벤트를 버리고, 그것을 위해 아무것도 만들지 않아요.

## <a id="event-reference"></a>이벤트 레퍼런스

aipager의 처리기는 `aipager/dtach/hook_receiver.py`에 있고, hook 자체가 하는 일은 `aipager/dtach/notify_hook.py`에 있어요.

### <a id="sessionstart"></a>`SessionStart`

세션이 시작됐거나, 재개됐거나, 대화가 압축됐어요. aipager가 아직 추적하지 않는 세션은 등록돼요. 압축 뒤에는 카드에 `📦 name · 대화 압축됨 (압축 전 82%)`가 나와요([PreCompact](#precompact--postcompact) 참고). 새 대화라면 hook이 이전 작업의 열림 표시도 지워요. 백그라운드 작업의 첫 `SessionStart`(소스 `fork`)는 aipager에 그 작업의 대화 ID만 알려 줘요. 함께 옮겨 간 열린 작업을 끝내지는 않아요. Claude Code 백그라운드에서 다시 가져온 세션의 첫 `SessionStart`는 그동안 보류된 메시지를 보내요.

### <a id="sessionend"></a>`SessionEnd`

세션이 끝났어요. GONE으로 표시돼요. 고정된 상태 표시줄에서 빠지고, 종료된 세션으로 옮겨지고, `/resume`으로 되돌릴 수 있어요. `aipager session <name>`이나 `/new <name>`은 새 세션을 시작해요. 백그라운드 작업의 `SessionEnd`(작업이 중지됨)는 절대 세션을 끝내지 않아요. `StopFailure`처럼, 그 백그라운드 작업이 끝내지 않고 남긴 작업만 닫아요.

### <a id="userpromptsubmit"></a>`UserPromptSubmit`

Claude가 메시지를 가져갔어요. 터미널에서 입력한 것일 수도, Telegram에서 보낸 것일 수도 있어요.

hook은 먼저 이 메시지를 세션의 권한 메모(`/tmp/claude-notes-<session>/`, aipager가 입력하는 Telegram 메시지마다 하나)와 맞춰 보고, 작업의 규칙을 `/tmp/claude-policy-<session>.json`에 써서 [PreToolUse](#pretooluse) 검사가 읽게 해요([보안 → 팀 모드의 규칙 적용](security.md#team-mode-enforcement) 참고). 이벤트를 넘기기 전에 Claude가 어떤 메시지를 가져갔는지 aipager에 알려요(`queue_pickup` datagram). 그다음 덧붙일 것이 있으면 Claude를 위한 추가 맥락을 출력해요. `/settings`에서 고른 답변 스타일, 그리고 예전 메시지에 답장할 때는 그 메시지가 무슨 내용이었고 언제 보냈는지예요. `<task-notification>` 메시지(백그라운드 에이전트나 백그라운드 명령이 끝났을 때 Claude가 스스로 깨어나는 것)는 같은 일의 이어짐이에요. 어떤 메모와도 맞춰지지 않고 작업의 규칙을 유지해요.

aipager는 세션을 작업 중으로 표시하고, 작업 카드가 아직 없으면 하나 열고(Telegram에서 온 메시지에는 이미 있어요), Claude가 가져간 메시지의 👀를 👍로 바꿔요. 작업이 진행되는 동안 보낸 메시지는 Claude가 그 작업에 합치거나 그것으로 다음 작업을 시작할 때 👍를 받아요.

### <a id="pretooluse"></a>`PreToolUse`

도구가 곧 실행돼요. aipager 자체의 안전 검사가 바로 여기, hook 안에서, Claude Code가 자기 권한 규칙을 보기 전에 실행돼요:

- Telegram에서 시작한 작업은 보낸 사람 역할의 규칙과 안전 기준선(보호된 경로, 명령 패턴)을 따르고, `owner` 역할만 건너뛸 수 있어요. aipager가 누구의 것인지 알 수 없는 작업은 안전 기준선을 받아요. 터미널에서 입력한 메시지는 제한되지 않아요. 규칙은 [그룹](groups.md#how-rules-work)과 [보안](security.md#team-mode-enforcement)에 있어요.
- 규칙이 이 호출을 거부하면 hook은 `deny` 결정을 출력하고, Claude Code는 묻지 않고 그 도구를 거부하고, aipager에 알려요(`safety_blocked`). 작업은 `🛑 x1 · 안전 규칙으로 차단됨: ... (중지됨)`으로 멈추고, 그 작업의 이후 모든 도구 호출도 거부돼요.
- `SendUserFile` 호출(Claude가 채팅으로 파일을 보내는 것)은 owner의 작업과 터미널에서 입력한 메시지를 포함해 모든 작업에서 확인해요: 전송 하한선, 파일 확인, 작업 자체의 규칙([보안](security.md#files-claude-sends)). 거부된 호출은 "aipager safety policy" 표시가 없는 `deny`를 받아요. Claude는 이유를 읽고 알려 주며, 작업은 계속되고, 훅은 감사 로그를 위해 파일마다 이름을 적은 `send_files_refused`를 aipager에 보내요.
- 검사 자체에서 오류가 나면 거부해요. 세션의 규칙을 읽을 수 있고 소유자에게 건너뛰기 권한을 주는 경우는 예외예요.
- 마지막으로, 통과시키는 호출이면 hook은 aipager 소켓 옆 폴더에 잠깐 쓰는 표시를 남기고, aipager가 그 세션에 메시지를 입력하는 중이면 입력이 끝날 때까지 기다려요(최대 5초). Claude Code는 이 hook이 끝나는 순간 권한 요청을 보여 주는데, 그때 입력된 키가 있으면 그 요청에 답하게 돼요. 메시지의 Enter가 “Yes”를 고르는 거예요. aipager 쪽에서도 입력을 시작하기 직전에 통과한 호출이 끝나기를 기다리고, 그래도 요청을 만난 메시지는 입력하지 않아요. 그 메시지는 기다렸다가(👀) 요청에 답이 나면 Claude에게 가요.

aipager 쪽에서는 이 호출이 작업 카드의 한 줄이 되고(서브에이전트의 호출은 그 에이전트 줄에 들어가요), `/settings` → 변경 사항 미리 보기 (diff)가 켜져 있으면(기본은 꺼짐) `Write`나 `Edit`가 변경 미리 보기를 보여 줘요. `AskUserQuestion` 호출은 선택지마다 버튼이 하나씩 있는 질문이 돼요. 작업이 진행 중이지 않을 때 Claude Code의 보조 에이전트가 하는 질문은 보여 주지 않아요(Claude가 나에게 묻는 게 아니니까요). Claude 자신의 `Agent` 호출은 시작한 에이전트의 `subagent_type`과 `description`을 남기고, 서브에이전트의 호출은 그 에이전트의 최신 활동이에요. 에이전트가 백그라운드에서 실행되는 동안 둘 다 답변 아래 상태에 나와요.

### <a id="permissionrequest"></a>`PermissionRequest`

Claude Code가 곧 권한을 요청해요. 작업 카드가 이 요청으로 바뀌고, Claude의 설명 아래에 실제 명령이나 경로를 보여 줘요([명령 → 권한 요청](commands.md#permission-prompts) 참고). 허용, 거부, 중지, 그리고 Claude Code가 정말로 기억할 수 있는 규칙을 줄 때만 나오는 항상 허용이 있어요.

hook은 답할 수 있으면 직접 이 요청에 답해요. 짧게 쓰는 응답 소켓을 열고, 그 주소를 이벤트와 함께 보내고, 내가 누를 때까지 최대 20초 기다려요. 그동안 Claude Code도 터미널에 자기 대화 상자를 보여 줘요(서브에이전트의 호출이면 hook이 끝난 뒤에야). 제때 누르면 `PermissionRequest` 결정으로 돌아가서(허용, 규칙과 함께 허용, 또는 거부) 그 대화 상자가 닫혀요. 제때 누르지 않으면 hook은 기다림이 끝났다고 aipager에 알리고(`permission_reply_timeout`) 아무것도 출력하지 않아요. aipager에 연결할 수 없거나 이 경로에서 무엇이든 실패해도 아무것도 출력하지 않아요. 그러면 대화 상자가 터미널에서 기다리고, 나중에 누르면 키를 입력해서 답해요. 감사 로그(`~/.claude/aipager-audit.jsonl`)에는 각 누름이 어느 방식으로 답했는지 남아요. `"via": "hook_decision"` 또는 `"via": "keystroke_fallback"`이에요.

다른 요청이 열려 있는 동안 도착한 `PermissionRequest`도 작업 카드 아래 별도 메시지로 보여 줘요. 같은 대화 상자가 다시 오면(같은 요청자, 같은 도구와 같은 호출, 또는 같은 입력) 한 번만 보여 줘요. Claude Code는 `AskUserQuestion`에도 이 hook을 실행하는데, 그 질문은 이미 `PreToolUse`로 보여 줬으니 두 번 보여 주지 않아요. 백그라운드 에이전트의 대화 상자는 그 hook이 기다리기를 멈춘 뒤에야 터미널에 나타나고, 이미 열린 대화 상자들 아래에 놓여요. 그래서 탭은 Claude Code가 먼저 보여 주는 요청에만 키를 입력해요([명령 → 권한 요청](commands.md#permission-prompts) 참고).

### <a id="posttooluse--posttoolusefailure"></a>`PostToolUse` / `PostToolUseFailure`

도구가 끝났거나 실패했어요. 작업 카드의 그 줄이 고정되고(실패는 그 줄에 표시돼요), 열려 있는 요청에 대한 답을 알아채요. 그 요청을 위한 예전 허용이나 거부 버튼은 이후 아무것도 입력하지 않아요. 세션은 최신 도구 줄 200개를 보관해요.

Claude Code가 백그라운드로 옮긴 `Bash` 호출은 `backgroundTaskId`를 알려 줘요. 그러면 aipager는 그것을 백그라운드 명령으로 추적하는데(메인 루프의 것만이고, `agent_id`가 있는 호출은 그 서브에이전트의 것이에요), 끝나는 것을 볼 때까지예요. `<status>`가 있는 `<task-notification>`(Claude를 깨우는 메시지 안이나, 작업 중에 도착하면 저장된 대화 안), 그 ID에 대한 `TaskStop`이나 `KillShell`, 세션 종료, 또는 두 시간(`AIPAGER_BG_SHELL_MAX_TRACK`, 초 단위, 기본값 7200)이에요. 카드의 그 줄은 [명령](commands.md#idle-responses)에 있어요.

서브에이전트 안에서 한 `Agent`(또는 `Task`) 호출은(그 이벤트에 `agent_id`가 있어요) 자기가 시작한 에이전트의 ID를 알려 줘요. aipager는 어느 에이전트가 어느 에이전트를 시작했는지 기억해서, 자기 에이전트를 기다리려고 멈춘 에이전트와 끝난 에이전트를 구별해요(아래 `SubagentStart` / `SubagentStop` 참고).

실행된 `SendUserFile`의 파일은 aipager가 바로 복사해요. 호출마다 한 번, 그리고 프롬프트로 시작한 작업 동안에만 복사하고, 목록은 도구의 결과가 아니라 `PreToolUse`에서 본 목록을 쓰며, 파일마다 다시 확인해요. 사본은 aipager가 그 작업에 대해 적어 둔 대화 기록에서 그 작업 동안 쓰인 호출을 찾은 뒤, 작업의 답변 다음에 보내요([보안](security.md#files-claude-sends), [명령어](commands.md#files-claude-sends)). Claude Code가 hook 없이 시작한 작업(다음으로 가져간 메시지, Stop hook의 피드백)은 대화 기록에 시작이 나오면 인정돼요.

### <a id="notification"></a>`Notification`

aipager는 Claude Code의 `notification_type`을 읽어요:

- `permission_prompt`: 더 느리고 오래된 권한 요청 신호예요. 열린 요청이 없을 때만 이 요청을 보여 줘요. 하나가 열려 있으면 그건 `PermissionRequest`가 이미 보여 준 대화 상자예요.
- 권한 요청이나 질문이 기다리는 중의 `idle_prompt`: 세션은 대기 상태로 남고, 채팅에는 `⬆️ jim · 위에서 아직 답변을 기다리고 있어요`가 기다림마다 한 번 와요.
- 그 밖의 `idle_prompt`: `Stop`처럼 처리해요(아래 참고). `Stop`을 놓친 작업을 잡아 줘요. 이미 전달된 답변은 절대 다시 보내지 않아요.

### <a id="stop"></a>`Stop`

작업이 끝났어요. 세션이 준비됨이 되고, 작업 카드가 내 배치에 따라 고정되고, Claude의 답변(이벤트의 `last_assistant_message`, 없으면 저장된 대화에서)을 보내요. 그다음 작업이 끝나기를 기다리며 보류된 메시지를 보내요. 아직 실행 중인 백그라운드 에이전트와 백그라운드 명령은 그 일을 열어 둬요. 카드는 계속 그 일의 상태 역할을 하고, 답변 끝에는 그 작업들의 실시간 상태가 붙어요. 에이전트마다 무엇을 하고 있는지, 그리고 이 일의 백그라운드 명령이 몇 개 실행 중인지예요([명령 → 답변이 나갈 때 아직 실행 중인 에이전트](commands.md#agents-still-running-when-the-answer-goes-out) 참고). hook은 먼저 작업의 열림 표시를 지워요. Claude가 바로 다음 작업이 될 대기 중인 메시지를 가지고 있으면 예외예요.

### <a id="stopfailure"></a>`StopFailure`

API 오류로 작업이 끝났어요. 세션이 준비됨이 되고, 답변이 있으면 `Stop`처럼 전달돼요. 알려진 오류(서버 과부하, 요청 수 제한, …)는 쉬운 말로 설명해요.

### <a id="subagentstart--subagentstop"></a>`SubagentStart` / `SubagentStop`

서브에이전트가 시작했거나 끝났어요. 서브에이전트마다 작업 카드에 자기 줄 `🤖 <type> · <activity> · <elapsed>`이 있고, 자기 도구 호출은 그 아래에 접혀 있다가(가장 최근 호출은 접힌 부분 뒤에 항상 보여요), 멈추면 `✅ 🤖 <type> · 도구 N번 사용 · <elapsed>`로 고정돼요. `SubagentStart`는 어느 에이전트가 시작했는지 알려 주지 않아요. 그래서 aipager는 기다리고 있는 같은 유형의 `Agent` 호출(`PreToolUse`)과 짝지어요. 그런 호출이 모두 한 에이전트에서 왔다면 새 에이전트를 바로 그 에이전트에 연결해요(카드 줄이 `🤖 pipeline-runner › ship-tester`가 되고, 답변 아래 상태에서 그 에이전트 아래에 들어가요). 여러 에이전트에서 왔다면 시작한 에이전트 자신의 `PostToolUse`가 이름을 알려 줄 때까지 아무것도 연결하지 않아요. 호출의 설명은 그 유형의 호출이 정확히 하나 기다리고 있을 때만 써요. `SubagentStop`이 끝내 오지 않는 서브에이전트는 30분 동안 조용하면 지워져요(`AIPAGER_SUBAGENT_SILENCE`, 초 단위, 그동안 그 ID가 있는 이벤트가 하나도 없을 때). 일하는 에이전트는 몇 초마다 도구 이벤트를 보내니까, 이것은 멈춤 신호를 놓친 에이전트(재시작, 잃어버린 datagram)만 거둬 가요. 한 번에 30분 동안 실행되면서 그 에이전트 ID로 아무 이벤트도 없는 도구 호출도 에이전트를 거둬 가게 해요. `AIPAGER_SUBAGENT_TTL`은 같은 설정의 예전 이름으로 여전히 받아들여요. 유형이 없고 ID를 모르는 `SubagentStop` 이벤트는 자주 오고, 무시돼요.

자기가 시작한 에이전트가 아직 실행 중인데 멈춘 백그라운드 에이전트도 `SubagentStop`을 받아요. aipager는 이것을 끝이 아니라 잠깐 멈춤으로 봐요. 그 에이전트는 계속 실행 중으로 남아요(자기 줄, 그 일, 답변 아래 상태, 고정된 표시줄). 같은 ID로 오는 다음 `SubagentStart`는 이어서 하는 것이고 두 번째 줄을 만들지 않아요. 그 에이전트가 직접 하는 도구 호출도 이어서 하는 것으로 쳐요. 자기 에이전트가 하나도 실행 중이지 않은 상태로 멈추면 끝나요. 그중 마지막 에이전트가 끝나고 2분이 지나도 이어서 하지 않으면 더 이상 실행 중으로 세지 않지만, 완료로 보여 주지는 않아요. 실행 중인 에이전트는, 기다리는 에이전트까지 포함해, aipager가 재시작해도 남아요.

백그라운드 에이전트를 멈추는 Claude Code의 키(ctrl+x ctrl+k를 두 번)로 멈춘 에이전트는 `SubagentStop`을 보내지 않아요. aipager가 직접 끝내요. aipager의 중지가 그 키를 눌렀으면 바로, Claude Code의 트랜스크립트에 `agents_killed` 줄이 생기면(그 뒤 Claude Code가 스스로 시작하는 작업의 `UserPromptSubmit`에서 읽어요. 그 프롬프트는 감싸는 태그 없는 평범한 문장이에요), 또는 `Stop` 이벤트의 `background_tasks`에 이전 `Stop`이 나열했던 에이전트가 더는 없을 때예요. 그 에이전트들의 열린 요청은 닫히고(어떤 훅도 알려 주지 않아요), 그중 하나에 대해 나중에 오는 `SubagentStop`은 무시해요. aipager의 중지가 그 키를 눌렀거나 Claude Code의 트랜스크립트에 중지가 기록된 뒤라면, 그 작업은 터미널에서 입력한 메시지로 취급하지 않아요.

### <a id="precompact--postcompact"></a>`PreCompact` / `PostCompact`

Claude가 컨텍스트를 압축하고 있어요(`trigger`는 `auto` 또는 `manual`). 작업 카드에 `🔄 name · 대화 압축 중`이 나와서(카드가 없으면 새 메시지를 보내요), 이 멈춤이 비정상 종료처럼 보이지 않게 해요. `PostCompact`가 이 상태를 끝내요. `📦 name · 대화 압축됨 (압축 전 X%)` 줄은 그다음 `SessionStart`에서 오고, 그것을 놓치면 상태 줄에서 와요. Claude Code의 상태 줄은 다음 메시지 전까지 0%로 나와서 새 크기는 아직 알 수 없어요. 작업과 작업 사이에 압축됐다면, 상태 줄이 크기를 알려 주는 대로 이 줄이 `📦 name · 대화 압축됨: X% → Y%`로 바뀌어요. 상태 줄은 컨텍스트가 80%에 이르면 `⚠️ name · 컨텍스트 82% 사용 - 곧 자동으로 압축해요` 경고도 주고(“지금 압축” 버튼과 함께), 대화마다 한 번만 줘요. 압축해도 다시 나오지 않고, 새 대화(예를 들어 `/clear` 뒤)에서는 다시 줘요.

### <a id="messagedisplay"></a>`MessageDisplay`

Claude 자신의 글이 화면에 닿는 순간이에요. 저장된 대화는 도구 호출 한 바퀴가 끝난 뒤에야 쓰이니까, 이것이 Claude가 지금 무슨 말을 하는지 알 수 있는 유일한 즉시 출처이고, Claude의 설명을 작업 카드에 넣어 주는 것도 이거예요. 서브에이전트의 글(그 `agent_id`가 있는 모든 이벤트)은 절대 Claude 자신의 글로 다루지 않아요. 작업 카드의 설명, Mini App, 작업 로그에 들어가지 않고, 답변 아래 상태에서 그 에이전트의 최신 활동이 돼요. hook은 이 이벤트에 상태 줄 데이터를 붙이지 않아요. Claude Code가 그리는 동안 이것을 기다리고, 메시지마다 여러 번 일어나니까요.

### <a id="premodelswitch"></a>`PreModelSwitch`

Claude Code 2.1.251 이상은 모델을 바꾸기 전에 이것을 일으켜요. 페이로드에는 `from_model`, `to_model`, `requested_model`, `source`(`/model`을 입력했으면 `command`, 또는 `picker`, `sdk`)가 있어요. Claude Code는 보통 바꾸기 전에 "Switch model?"을 물어요. 다음 답변이 대화 전체를 다시 읽어야 하니까요. `permissionDecision: "allow"`로 답하는 hook은 이 질문 없이 바꾸게 해요.

`aipager-hook`은 aipager가 직접 입력한 변경에만 `allow`로 답해요. 출처는 Mini App의 모델 선택이나 모델 키보드예요. 다른 모든 변경은 답을 받지 못해서 Claude Code가 평소처럼 물어요. 여기에는 봇 소유자가 터미널에서 직접 입력한 `/model`, 모델 선택기, SDK를 통한 변경이 들어가요.

aipager는 표시 파일로 자기 변경을 알아봐요. `/model <name>`을 입력하기 바로 전에 aipager가 `aipager-modelswitch-<session>.json`을 써요:
- 제어 소켓이 있는 폴더(보통 `$XDG_RUNTIME_DIR`)에 두고,
- 원자적으로, 권한 0600으로 쓰고,
- 모델과 Claude 세션 ID를 적고,
- 30초 뒤에 만료돼요.

hook은 다음이 모두 맞을 때만 `allow`로 답해요:
- 표시가 내 소유의 파일이고 심볼릭 링크가 아니에요.
- 아직 만료되지 않았어요.
- 같은 aipager 세션과 같은 Claude 세션 ID를 위한 거예요.
- 기록된 모델이 대소문자 구분 없이 `requested_model`이나 `to_model`과 같아요.
- `source`가 `command`예요.

그다음 hook은 원자적인 이름 바꾸기로 표시를 가져가서 딱 한 번만 쓰이게 해요. 이 검사는 로컬 파일 하나만 읽고, 소켓도 쓰지 않고 기다리지도 않아요. 이 이벤트는 aipager로 넘기지 않아요.

Claude Code 2.1.101 이상은 모르는 hook 이벤트를 무시하니까, `PreModelSwitch`가 없는 예전 버전은 영향을 받지 않아요. 이 질문이 여전히 나올 수 있는 경우가 하나 있어요. `opusplan`으로 바꾸거나 거기서 다른 모델로 바꾸면 Claude Code가 hook에 두 번 물을 수 있는데, 첫 번째만 표시를 찾을 수 있어서 Claude Code가 터미널에서 "Switch model?"을 물어요.

## <a id="the-status-line"></a>상태 줄

hook 이벤트가 아니에요. Claude Code는 상태 줄을 다시 그릴 때마다 `statusLine` 명령 `aipager-statusline`(`aipager.dtach.statusline_notify:main`)을 실행해요. 이 명령은:

- Claude Code의 상태 JSON을 `/tmp/claude-status-<session>.json`에 써요(hook이 `sl_tokens`를 얻으려고 읽고, `aipager status`와 aipager도 읽어요).
- aipager에 `statusline` datagram을 보내요. 모델, 컨텍스트 비율, 비용, token 수, 추가하고 지운 줄 수가 들어 있고, 작업 카드, `/status`, Mini App, 모델 변경이 적용됐는지 확인, 컨텍스트 경고에 쓰여요.
- 터미널의 상태 줄을 출력해요: `[name] Opus 5.5 | 42% ctx | $1.23`.

## <a id="aipagers-own-datagrams"></a>aipager 자체의 datagram

hook 이벤트 말고도, 이 보조 프로그램들은 같은 소켓으로 aipager에 자기 메시지 몇 가지를 보내요:

| 메시지 | 보내는 쪽 | 언제 |
|---|---|---|
| `queue_pickup` | hook | `UserPromptSubmit` 때: Claude가 어떤 Telegram 메시지를 가져갔는지. |
| `safety_blocked` | hook | `PreToolUse` 때, 안전 검사가 호출을 거부했어요. |
| `send_files_refused` | 훅 | `PreToolUse`에서 Claude가 보내려던 파일이 거부되었을 때: 감사 로그를 위한 파일별 이유. |
| `permission_reply_timeout` | hook | `PermissionRequest` 기다림이 누름 없이 끝났어요. |
| `statusline` | 상태 줄 | 상태 줄을 다시 그릴 때마다. |
| `hook_memory_cap_hit` | hook, 상태 줄 | 프로세스가 메모리 한도에 닿았어요. |
| `report_error` | hook, `aipager` 명령 | 오류가 났어요: 오류 유형과 aipager 코드 안의 위치만 있고, 이벤트 내용은 절대 없어요. [문제 신고](problem-reports.md)에 쓰여요. |

## <a id="the-permission-flow"></a>권한 요청의 흐름

```
claude              aipager-hook             aipager daemon          Telegram
  |                      |                         |                     |
  | PermissionRequest    |                         |                     |
  |--------------------->| opens a reply socket    |                     |
  |                      |------------------------>| busy card becomes   |
  |                      |                         | the prompt          |
  |                      |                         |-------------------->|
  |                      |                         |   you tap Allow     |
  |                      |                         |<--------------------|
  |                      |   allow (reply socket)  | audit log           |
  |                      |<------------------------|                     |
  |  decision: allow     |                         |                     |
  |<---------------------|                         |                     |
  | tool runs            |                         |                     |
```

20초 안에 누르지 않으면 hook은 아무것도 출력하지 않고 Claude Code가 자기 대화 상자를 보여 줘요. 나중에 누르면 세션의 터미널(`dtach -p`)을 통해 그 대화 상자에 답을 입력해요. 어느 쪽이든 누름은 감사 로그에 남고 카드 아래에 올라가요(`✅ jim · 허용됨 · @alice · Bash: ls -la /tmp`).

## <a id="see-also"></a>함께 보기

- [아키텍처](architecture.md): hook 수신기가 어디에 있는지.
- [봇 명령 → 권한 요청](commands.md#permission-prompts): 내가 보는 쪽.
- [보안 모델](security.md): 안전 검사가 막을 수 있는 것과 없는 것.
