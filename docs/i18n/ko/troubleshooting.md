# 문제 해결

<!-- i18n-source: docs/troubleshooting.md @ bb690d5 -->

> 이 페이지는 영어 문서를 번역한 거예요. 내용이 서로 다르면 영어 문서가 맞아요. ([English](../../troubleshooting.md))

흔한 문제와 해결 방법이에요. 잘 모르겠으면 이것을 실행하세요:

```sh
aipager doctor
```

아래 모든 확인을 순서대로 실행하고, 실패한 것마다 표준 해결 안내를 출력해요. 소스: `aipager/doctor.py`.

## <a id="another-aipager-daemon-already-owns-the-socket"></a>“Another aipager daemon already owns the socket”

aipager 두 개가 제어 소켓 하나를 함께 쓸 수는 없어요. 이 오류는 aipager 하나가 아직 실행 중이고 그 소켓에서 응답하고 있다는 뜻이에요(소켓은 `$XDG_RUNTIME_DIR/aipager.sock`이고, `$XDG_RUNTIME_DIR`이 설정되어 있지 않으면 `/tmp/aipager.sock`이에요). 비정상 종료한 aipager가 남긴 소켓 파일은 문제가 아니에요. 다음 시작 때 지우고 새로 만들어요. 먼저 실행 중인 aipager를 멈추세요:

```sh
aipager service stop          # 서비스로 실행 중이라면
pkill -f 'aipager start'      # 그렇지 않다면
```

소켓 확인이 놓치더라도 `aipager start`는 `aipager is already running (pid=N)`으로 거부하고 잠금 파일(`~/.local/share/aipager/daemon.lock`)을 알려 줘요. 같은 방법으로 그 프로세스를 멈추세요.

## <a id="telegram-bot-doesnt-respond"></a>Telegram 봇이 응답하지 않아요

1. `aipager status`: aipager 줄에 `not running`이라고 나오면 시작하세요.
2. `aipager doctor`: `token_valid`와 `chat_reachable` 확인은 Telegram API를 처음부터 끝까지 호출해서 정확한 오류를 보여 줘요.
3. 채팅이 잘못됐어요: `aipager config`를 실행하고 **Test bot reachability**를 고르세요. 잘못된 개인 채팅을 고치려면 **Add a DM scope**로 올바른 채팅을 추가하고([ID 찾는 방법](groups.md#finding-ids-auto-detect) 참고), **Edit a scope**에서 잘못된 채팅을 지우세요.
4. 봇이 `/start`를 받은 적이 없어요: Telegram에서 봇을 열고 Start를 한 번 누르세요.

## <a id="busy-cards-got-slower"></a>작업 카드가 느려졌어요

Telegram은 채팅 하나에 대략 1초에 메시지 하나를 허용해요(그룹에는 1분에 20개). 메시지 하나하나, 편집 하나하나가 모두 세어져요. aipager는 채팅마다 약 1초에 1번 호출하는 자체 예산을 주고, 잠깐 몰리는 것은 조금 허용하며, 그 채팅의 작업 카드들은 이 예산을 **함께** 써요. “입력 중…” 표시도 이 예산 안에 있고, 채팅이 보내는 것 가운데 **가장 덜 중요한** 것이에요(아래 “입력 중 표시” 참고).

입력 중 표시의 몫이 먼저 떼어지고(채팅에서 가장 오래 진행 중인 작업이 10분이 안 됐을 때는 4.5초마다 한 번 호출, 작업이 길어질수록 더 드물게. “입력 중 표시” 참고), 남은 것을 카드들이 나눠요. 그래서 개인 채팅의 카드 하나는 약 4.8초마다 새로 고쳐지고, 한 채팅에서 세션 두 개가 작업 중이면 카드마다 약 9.7초, 세 개면 약 14.5초마다예요. **카드가 느린 건 예산이 제대로 일하는 것이지, 버그가 아니에요.** 답변, 답장, 버튼 응답은 카드에 자리를 내주려고 느려지는 일이 절대 없어요. 이것들에는 늘 예약된 몫이 있고, 몫이 없는 카드 편집은 내 답변 앞에 줄 서는 대신 그냥 건너뛰고 다음 차례에 다시 시도해요.

**그룹**에서는 세션이 몇 개든 한도가 1분에 20번 호출이고, 입력 중 표시만으로 그 3분의 2를 쓸 거예요. 그룹의 카드들은 합쳐서 10초마다 한 번 새로 고침을 유지하고(카드가 하나면 11초), 입력 중 표시는 나머지를 써요. 그래서 그룹에서는 입력 중 표시가 가끔 1~2초씩 사라질 수 있어요.

카드를 빠르게 하려면: 채팅마다 동시에 실행하는 세션을 줄이거나, 가장 바쁜 세션에 따로 채팅을 주세요(`aipager config`). `STREAM_EDIT_INTERVAL`(기본값 `1.2`, 카드에 글이 흘러나오는 동안의 속도)과 `BUSY_EDIT_INTERVAL`(기본값 `3.0`, 조용할 때)도 aipager의 환경 변수로 조정할 수 있어요([환경 변수로 하는 설정](commands.md#settings-in-the-environment) 참고). 둘 중 하나를 채팅의 하한*보다 낮게* 설정해도 해롭지 않고 아무것도 바뀌지 않아요. 하한이 이기고, 바로 그것이 내가 무엇을 설정하든 채팅을 Telegram 한도 아래로 지켜 줘요.

### <a id="a-long-turns-card-refreshes-less-often"></a>긴 작업의 카드는 덜 자주 새로 고쳐져요

위의 속도는 작업의 처음 2분 동안이에요. 그 뒤로 카드는 **작업이 길어질수록 느려지고**, 10분부터는 시계가 새로 고침에 맞춰 30초 단위로 움직여서 절대 멈춘 것처럼 보이지 않아요:

| 작업 경과 시간 | 카드 새로 고침 최소 간격 | 시계 표시 |
|---|---|---|
| 0–2분 | 위와 같음 (개인 채팅의 카드 하나라면 약 4.8초) | `0:45` |
| 2–10분 | 10초 | `4:10` |
| 10–60분 | 30초 | `23:00`, `23:30` |
| 60분 넘음 | 30초 | `1:23:00`, `1:23:30` |

시계는 30초 단위로 내림하고, 새로 고침 사이는 최소 30초, 가끔 조금 더 걸려요. 그래서 카드가 가끔 한 단계를 건너뛰어요. 23분 29초의 새로 고침은 `23:00`을 보여 주고, 다음 새로 고침이 24분 1초에 오면 `24:00`을 보여 줘서 `23:30`은 건너뛰어요.

새 도구 줄이나 새 문장은 다음 새로 고침을 기다려요. **상태 변화**(세션이 작업 중에서 백그라운드 에이전트를 기다리는 상태로, 또는 그 반대로)는 바로 보여 주고, 최대 10초에 한 번이에요. 실행 중인 에이전트 줄과 기다림 줄도 같은 단계를 써요. 끝난 카드는 작업 시간을 실제 초 단위로 보여 줘요(`23:17`).

이것이 네 시간짜리 작업이 메시지 하나를 수천 번 편집하지 않게 막아 줘요. 2026-09-23에 카드 하나가 정확히 그렇게 했고, 그 채팅은 일곱 시간 동안 차단됐어요. 작업 내내 빠른 속도를 유지하려면 **`/settings` → ⏱ 긴 작업의 카드 업데이트**를 끄세요(채팅 전체에, 또는 Mini App이나 `👤 세션별 설정`에서 세션마다).

### <a id="the-typing-bubble"></a>입력 중 표시

“입력 중…” 표시는 그 안에서 세션이 몇 개 작업하든 채팅마다 **하나**의 루프가 보내고(모든 Telegram 앱에서 이 표시는 채팅 전체의 것이에요), `TYPING_INDICATOR_INTERVAL`초마다 보내요(기본값 4.5. Telegram은 입력 상태를 5초 뒤에 지워요. `0`은 표시를 꺼요). 채팅의 예산 안에서 이것은 채팅이 보내는 것 가운데 가장 덜 중요한 것으로 세어져요:

- **작업이 길어질수록 느려져요**. 채팅에서 가장 오래 진행 중인 작업의 시간을 기준으로, 처음 10분 동안은 4.5초마다(표시가 계속 켜져 있어요), 그 뒤로는 15초마다예요. Telegram은 이것을 5초 동안 보여 주니까 오래된 작업의 표시는 계속 켜져 있지 않고 깜빡여요. 4.5초라면 이것 하나만으로 한 시간에 800번 호출이 되고 시간당 한도(아래)가 오랫동안 꺼 버릴 거예요. **⏱ 긴 작업의 카드 업데이트**를 끄면 4.5초를 유지해요;
- **모든 작업의 처음부터 보여요**. 막 시작한 카드는 자리를 내주려고 조금 더 천천히 새로 고쳐져요. 표시의 몫은 카드들이 나머지를 나누기 전에 떼어지니까(위 참고), 둘을 합쳐도 절대 채팅의 한도를 넘지 않아요;
- 카드 편집에 필요한 몫은 절대 가져가지 않아요. 카드 편집 바로 뒤에 차례가 온 표시는 0.5초(`TYPING_RETRY_WAKE`) 기다렸다가 다시 시도하니까, 늦어도 1~2초이고 절대 빠지지 않아요;
- 채팅의 시간당 전송량이 많아지면 가장 먼저 빠져요(아래);
- 표시 자체가 받은 429는 Telegram이 요청한 시간만큼 표시만 막지만(카드는 계속돼요), 채팅의 6시간 경고 기간을 시작해요(속도가 초당 0.5번 호출로 제한되니까 카드도 느려져요);
- 표시가 받은 **차단**(`retry_after`가 아래 한도를 넘는 것)은 다른 호출이 받은 차단과 똑같이 채팅의 보내기를 멈춰요. 그 채팅에는 더 이상 아무것도 보내지 않고, 작업의 답변은 보류했다가 차단이 풀리면 전달해요.

예전에는 이것이 공짜였어요(작업 중인 세션마다 루프 하나, 어떤 예산에도 들어가지 않음). 그러다 2026-09-23에 개인 채팅 하나의 세션 두 개가 세 시간 동안 이것을 약 5,400번 보냈고, Telegram이 표시 자체를 제한했어요.

## <a id="the-bot-slowed-down-a-rate-limit-429"></a>봇이 느려졌어요: 속도 제한(429)

그래도 Telegram이 채팅을 제한하면, 호출 하나에 `429`와 몇 초짜리 `retry_after`로 답해요. aipager는 정확히 그 시간 동안 그 채팅에 호출하지 않고, 그 뒤 미뤘던 보내기를 한 번 다시 하고, **그 채팅의 카드 새로 고침 간격을 두 배로** 늘려요(최대 ×8). 조용한 1분이 지날 때마다 반으로 줄여서 원래대로 돌아가요.

보이는 것:

- `aipager status`(와 `aipager doctor`의 `aipager daemon` 줄)에 **`Telegram flood backoff ×4 (chat …), last 429 12 s ago`** 문구가 나와요.
- `aipager logs`에 traceback 없이 딱 한 줄: `flood: chat … 429 retry_after=5s → cadence ×2`.
- 보내기가 멈추는 것은 없고, **잃어버리는 답변도 없어요**. 미뤄질 뿐 버려지지 않아요.
- 채팅의 **얻은 전송 속도가 반으로 줄고**, 채팅이 **6시간 경고 기간**(`FLOOD_WARNING_HOURS`)에 들어가요. 속도가 초당 0.5번 호출(`FLOOD_WARNED_CEILING`)을 넘을 수 없고, 천천히 다시 올라가요. `aipager status`가 보여 줘요: `Telegram chat 123: rate 0.25/s (ceiling 0.50/s), 212/1200 calls in the last hour (as of 18s ago), 7/30 in the last minute - warning regime, 5h 52m left`.

할 일은 없어요. 저절로 풀려요. 천천히 보내기 줄은 채팅이 1~2분 조용하면 사라져요. 속도는 조용한 구간마다(보통은 1분, 경고 기간에는 36분) 초당 0.1번 호출씩 다시 올라가고, 경고 기간이 끝나면 전체 상한이 돌아와요. 그동안 `aipager doctor`의 aipager 줄은 계속 초록색이에요. 천천히 보내는 채팅은 정상적인 동작이에요. (예전에는 429를 5분쯤 뒤에 잊었어요. 2026-09-23 차단 전에 Telegram이 보낸 유일한 경고는 3시간 23분 전이었고, 그 채팅은 경고를 받고 몇 분 만에 다시 최고 속도로 돌아갔어요.)

### <a id="the-hourly-budget"></a>시간당 예산

모든 채팅에는 움직이는 **한 시간**도 있어요. 어떤 60분 동안이든 최대 `FLOOD_HOURLY_MAX`(1200)번 호출이고, 입력 중 표시, 카드 편집, 답변이 모두 들어가요. 반응만 빠져요. 마지막 120번(`FLOOD_HOURLY_ESSENTIAL_RESERVE`)은 답변, 답장, 요청을 위해 남겨 둬요:

- 한 시간에 810번 호출이 되면 입력 중 표시가 멈춰요(648 아래로 내려가면 돌아와요);
- 1080번이 되면 채팅이 **최소 모드**(아래)에 들어가고, 그 한 시간이 864로 줄어들 때까지 이어져요;
- 답변은 시간당 한도 때문에 거부되는 일이 절대 없어요. 가끔 1200을 넘으면 로그가 한 시간에 한 번 그렇다고 적어요.

`aipager status`는 지난 한 시간 동안 채팅의 호출 수를 예산과 함께 보여 줘요. 이 숫자는 상태 파일에서 오는데, aipager는 호출이 있는 동안 이 파일을 최대 1분에 한 번 다시 쓰니까, 숫자 옆에 얼마나 지난 것인지 적혀 있어요. 바쁜 세션 두 개가 몇 시간 동안 개인 채팅 하나에 쏟아 내도 예산보다 한참 아래예요(2026-09-23 사고를 다시 재생해 보니 가장 바쁜 한 시간에 약 900번 호출이었고, 같은 작업을 예산 없이 했을 때의 모델 추정은 약 3,400번이었어요).

### <a id="the-earned-rate-and-why-your-cards-may-be-slower-than-they-were"></a>얻은 속도, 그리고 카드가 예전보다 느릴 수 있는 이유

0.7.13부터 채팅마다의 꾸준한 전송 속도는 **미리 가정하지 않고 배워서 정해요**. Telegram은 내 봇에 실제로 적용되는 한도를 공개하지 않으니까(내 계정의 최근 기록에 따라 달라져요), aipager는 채팅마다 초당 0.5번 호출로 시작해서, 조용한 1분마다 0.1을 더해 초당 1번까지 올리고, 429를 받으면 반으로 줄이고, 차단 뒤에는 0.05로 낮춰요. 그래서 방금 차단된 봇은 차단된 적 없는 봇보다 훨씬 조심스럽게 보내고, 이후 몇 시간 동안 속도를 다시 얻어요.

채팅의 속도가 초당 0.2번 호출 아래로 떨어지거나, 시간당 예산 가운데 카드와 입력 중 표시의 몫을 다 쓰면, 채팅이 **최소 모드**에 들어가요. 작업 카드가 움직이지 않고 고정된 한 줄 `⏳ x1 · 작업 중 (카드 업데이트 잠시 멈춤)`만 보여 주고, 입력 중 표시가 멈추고, 고정된 상태 표시줄에 `⏸ 카드 업데이트 잠시 멈춤 (Telegram 시간당 한도)`가 나오고(속도 때문에 들어갔다면 `(Telegram 제한)`), 풀릴 때까지 다른 변화는 없어요. **답변, 답장, 권한 요청은 계속 가요**. 바로 그게 목적이에요. 카드는 이 봇이 보내는 것의 약 95%이고 답변은 약 5%라서, 압박을 받으면 작업이 아니라 화면을 덜어 내요. `aipager doctor`는 최소 모드를 경고로 알려서 멈춘 카드가 고장 난 카드로 오해받지 않게 해요. 속도가 회복되면 저절로 풀려요.

## <a id="the-bot-went-quiet-flood-control"></a>봇이 조용해졌어요: Telegram의 보내기 차단(flood control)

`TELEGRAM_MAX_RETRY_AFTER`(기본값 90초)를 넘는 `retry_after`는 속도 제한이 아니라 **차단**이고, 몇 시간씩 갈 수 있어요. 채팅에서 보면 봇이 죽은 것 같아요. 메시지는 여전히 Claude에게 가고 세션은 계속 일하지만, 아무 답도 돌아오지 않아요.

보이는 것:

- `aipager status`(와 `aipager doctor`의 `aipager daemon` 줄)에 **`Telegram flood-muted until HH:MM (chat …)`** 문구가 나와요.
- `aipager logs`에 한 줄: `Telegram flood control — chat … muted for Ns (until HH:MM) …`, 그 뒤 그 채팅은 조용해요. 나중에 `flood mute on chat … lifted` 한 줄이 나와요.
- `aipager status`는 채팅의 상태도 모두 보여 줘요: `Telegram chat 123: rate 0.05/s (ceiling 0.50/s), 431/600 calls in the last hour (as of 12s ago) - MINIMAL MODE, card updates paused - flood-muted until 09:41 - warning regime, 5h 59m left (1 ban(s) in the last 7 days)`.

aipager는 Telegram이 요청한 정확한 시간 동안 그 채팅의 보내기를 멈추고, 그 채팅에 **어떤 종류의 호출도 하지 않아요**. 답변, 작업 카드 편집, 첨부 파일, 반응, 입력 중 표시, 버튼을 누른 뒤의 짧은 안내, 내 명령이 만들었을 답장까지 모두요. 하나하나가 차단을 늘리는 새 위반이 될 테니까요. 2026-09-15에 진행 중인 차단에 보낸 요청 하나가 21분 차단을 9.5시간으로 만들었어요. 그래서 보내기가 멈춘 동안 입력한 명령에는 아무 답도 오지 않고, **버튼을 눌러도 확인이 오지 않아요**. 0.7.12까지는 Telegram이 따로 센다고 보고 짧은 안내를 계속 보냈어요. 보내는 속도에 대해서는 맞지만, 차단에 대해서는 아니에요. 다른 채팅은 영향을 받지 않아요.

**차단 중에 봇에게 메시지를 보내도 안전하고, 잃어버리는 것도 없어요.** 채팅의 보내기가 멈춘 동안에는 내가 채팅에서 무엇을 해도 봇이 Telegram을 호출하지 않으니까 차단을 늘릴 수 없어요:

- 내가 보낸 메시지(글, 음성 메시지, 파일, 빠른 템플릿)는 여전히 Claude에게 가고 세션은 그것으로 일해요. 그 작업 카드는 버려지지 않고 보류돼요. 차단이 풀릴 때 작업이 아직 진행 중이면 그때 카드가 나타나요. 차단 뒤에도 채팅은 한동안 최소 모드에 있어서, 카드는 차단이 풀린 뒤 몇 분 더 걸릴 수 있어요. 작업이 차단 중에 끝났다면 카드 없이 답변을 받아요.
- **🔄 다시 시도**는 차단 중에 아무것도 하지 않아요. 메시지를 다시 보내지 않고 버튼은 남아 있으니, 차단이 풀린 뒤 다시 누르면 돼요. 0.7.14까지(포함)는 오류 메시지와 그 버튼이 남은 채로 메시지가 Claude에게 가서, 두 번째로 누르면 또 보내졌어요.
- 메인 키보드도 보류돼요. 재시작이 보내는 키보드도 포함이에요(0.7.14까지는 다른 무언가가 새로 고칠 때까지 사라졌어요). 차단이 풀린 뒤 보내요.

차단이 풀리면 이 순서로 도착해요. 보류된 답변, 그다음 아직 진행 중인 작업의 카드, 그다음 키보드예요. 최소 모드가 끝나기를 기다려야 하는 카드는 키보드 뒤에 와요.

**답변은 사라지지 않아요.** 채팅의 보내기가 멈춘 동안 나온 답변은 보류됐다가 차단이 풀린 뒤 몇 초 안에 전달되고, 첫 줄에 `⏳ 늦게 도착함: Telegram 제한 때문에 42분 동안 보류됨`이 적혀 있어요. 다시 물을 필요가 없어요. 다시 물었다면 두 답변이 모두 오고, 오래된 것이 먼저 와요. 몇 시간 이어지는 차단은 여러 작업에 걸치고, 작업마다의 답변은 모두 내 것이니까요. (보류된 답변은 메모리에 있어요. 답변이 기다리는 중에 aipager를 재시작하면 사라져요. 종료 로그가 몇 개인지 알려 줘요. 채팅 하나는 최대 20개를 보류하고 24시간보다 오래된 것은 보관하지 않아요. 두 이유 중 하나로 버려진 것은 무엇이었는지 로그에 경고로 남아요.)

**차단은 그 뒤로 채팅을 더 빠르게가 아니라 더 느리게 만들어요.** 차단이 걸리는 순간 채팅이 배운 속도가 하한으로 떨어지고 차단이 세어져요. 보내기가 멈춘 시간 동안은 아무것도 다시 얻지 못하고, 다시 올라가는 것은 차단이 풀린 순간부터 시작해요. 그리고 **7일 동안**(`FLOOD_BAN_MEMORY_DAYS`) 채팅의 속도 상한*과* 시간당 예산이 1 더하기 그 주의 차단 횟수로 나눠져요. 차단 한 번이면 둘 다 반으로, 두 번이면 3분의 1로 줄어요. `aipager status`가 이 모든 것을 보여 줘요. (예전에는 이 기억이 24시간뿐이었고 상한만 반으로 줄였어요. 2026-09-19에 차단된 채팅이 2026-09-23에 다시 최고 속도로 돌아갔고, 일곱 시간 동안 차단됐어요.) 한 주에 다섯 번 이상 차단되면 그 기록이 만료될 때까지 채팅이 최소 모드에 머물러요. 답변은 계속 가고 카드는 멈춘 채로 있어요.

하지 말아야 할 일:

- “고치려고” aipager를 재시작하지 마세요. 보내기 멈춤은 표시된 시각에 저절로 풀려요. 0.7.13부터는 재시작해도 차단을 잊지 않아요(마감 시각, 채팅이 얻은 속도, 차단 기록이 `~/.claude/aipager-flood-state.json`에 저장되고 시작할 때 되살아나요. 지난 한 시간의 호출과 경고 기간도 마찬가지예요). 그래서 재시작이 차단을 늘리지도 않아요. 하지만 아직 보류 중인 답변은 모두 버리고, 고치는 것도 없어요.
- 더 빨리 다시 시도하려고 `TELEGRAM_MAX_RETRY_AFTER`를 기본값 90초 아래로 낮추지 마세요. 그 한도를 넘는 것은 모두 속도 제한이 아니라 차단이에요.

피하려면: 채팅마다 동시에 실행하는 세션을 줄이거나, 가장 바쁜 세션에 따로 채팅을 주세요(`aipager config`).

## <a id="a-session-dropped-off-the-pinned-status-bar-it-is-gone"></a>세션이 고정된 상태 표시줄에서 사라졌어요 (GONE 상태)

그 세션의 dtach 프로세스가 끝났어요(컴퓨터 재시작, `pkill claude`, 누군가 연결된 dtach 세션에서 `exit`를 입력함). 세션을 다시 만드세요. 대화는 그 세션의 `~/.claude/projects/...` 폴더에 그대로 있어요.

가장 쉬운 방법은 Telegram에서 `/resume <label>`(또는 그냥 `/resume`으로 목록에서 고르기)이에요. 세션을 다시 시작하고 대화를 이어 가요. 명령줄에서는:

```sh
aipager resume <label>             # 같은 일, 터미널에서
aipager session <label>            # 또는 기록 없는 새 세션
aipager session <label> --continue # 새 dtach, 이 폴더의 가장 최근 대화
```

## <a id="a-permission-prompt-with-no-news-for-5-minutes"></a>권한 요청이 5분 동안 소식이 없어요

세션이 권한 요청이나 질문을 기다리는데 Claude Code가 5분 동안 아무 소식도 보내지 않으면, aipager는 세션의 저장된 대화를 읽어요. 거기에 요청이 끝났다고 나올 때만(터미널에서 답했는데 그 소식이 사라진 경우) aipager는 그 요청을 기다리지 않고 세션을 다시 작업 중으로 세요. 그렇지 않으면 요청은 얼마가 걸리든 열려 있어요. 카드와 고정 바는 계속 세션에 답이 필요하다고 보여 주고, "아직 작업 중" 알림은 가지 않으며, 보낸 메시지는 답할 때까지 보류돼요([텍스트](commands.md#text) 참고). 요청이 열린 채로 Claude Code가 종료되면 세션이 끝나요. 대화 상자가 아직 열려 있다면 요청의 버튼으로 여전히 답할 수 있어요([한가할 때의 응답](commands.md#idle-responses) 참고).

기다리는 시간을 바꾸려면:

```sh
AIPAGER_INTERACTIVE_TIMEOUT=300 aipager start    # 초 단위
```

## <a id="voice-extra-wont-install-via-telegram"></a>Telegram으로 음성 메시지 기능이 설치되지 않아요

`[📦 음성 메시지 기능 설치]` 버튼은 설치 프로그램을 실행하고, 설치하는 동안 몇 초마다 메시지를 고쳐요(`아직 진행 중이에요… (25초)`). 설치가 실패하면 메시지에 종료 코드와 설치 프로그램 출력의 마지막 500자가 나와요. 흔한 원인:

- 네트워크: pip이 PyPI에 연결하지 못해요. aipager를 실행하는 컴퓨터의 연결을 확인하세요.
- 디스크: `~/.cache/huggingface/`에 모델용으로 약 74 MB, pip의 wheel 캐시에 약 250 MB가 더 필요해요. 먼저 공간을 비우세요.
- Permission denied: venv가 읽기 전용이에요. `uv tool install`로 바꾸세요. 이 방법은 항상 내 사용자 소유의 venv를 써요.

설치 도중 봇의 연결이 끊겼다면 컴퓨터에서의 설치 자체는 보통 끝나 있어요. aipager를 재시작하고 음성 메시지를 다시 보내 보세요.

## <a id="open-app-shows-open-this-page-from-the-telegram-app-to-sign-in"></a>앱을 열면 “로그인하려면 Telegram 앱에서 이 페이지를 여세요.”가 나와요

페이지는 불러왔지만 Telegram의 Mini App SDK 스크립트(`initData`를 만드는 스크립트)는 불러오지 못해서, 페이지에 로그인할 수단이 없고 API를 전혀 호출하지 않아요. 이 메시지는 내 잘못처럼 들리지만, 원인은 네트워크에 있어요.

최근 버전의 aipager는 그 스크립트를 직접 받아서 페이지 자신의 출처(`/telegram-web-app.js`)에서 제공해요. 그래서 휴대폰은 페이지를 보낸 서버에만 연결되면 되고, `telegram.org`까지 연결될 필요는 없어요. 예전 버전에서 이 문제를 보면 업그레이드하세요.

그다음 순서대로:

1. 페이지 소스를 보세요. `<script>` 태그가 `https://telegram.org/js/telegram-web-app.js`를 가리키면 aipager가 아직 스크립트를 받지 못했고 페이지가 대신 Telegram에서 불러오는 중이에요. 바로 이것이 `telegram.org`에 연결되지 않는 휴대폰에서 실패하는 경우예요. aipager는 Mini App 서버가 시작할 때 스크립트를 받고 하루에 최대 한 번 다시 시도하니까, aipager를 재시작하면 바로 다시 시도해요. `journalctl --user -u aipager | grep "webapp sdk"`가 무엇이 잘못됐는지 알려 줘요(보통 `telegram.org`가 막혔거나 걸러진 경우예요).
2. 태그가 `/telegram-web-app.js`를 가리키면 aipager가 그것을 제공하는지 확인하세요. aipager를 실행하는 컴퓨터에서 `curl -sI http://127.0.0.1:8765/telegram-web-app.js`(포트를 바꿨다면 맞게 고치세요)가 `200`과 `Content-Type: application/javascript`로 답해야 해요. 사본은 `~/.local/share/aipager/webapp-sdk/` 아래에 있어요.
3. 봇의 `/app` 버튼이나 메뉴 버튼이 아니라 일반 브라우저에서 주소를 열었어요. 그렇다면 이 메시지가 맞아요. SDK는 Telegram 안에서만 `initData`를 만들어요.
4. 페이지를 5분 넘게 열어 뒀어요. `initData`가 만료돼서 페이지가 다시 열어 달라고 해요. `/app`을 다시 누르세요.
5. 예전 aipager 버전의 키보드에서 `📱 App`을 눌렀어요. Telegram은 키보드 버튼으로 연 Mini App에 `initData`를 주지 않아서, 이제 aipager 키보드에는 이 버튼이 없어요. `/start`를 보내 지금 키보드를 받고, 메뉴 버튼이나 `/app`으로 앱을 여세요.

## <a id="pyexpat-_xml_setalloctrackeractivationthreshold-on-brew-install"></a>brew로 설치할 때 `pyexpat _XML_SetAllocTrackerActivationThreshold`

Homebrew의 `python@3.12` 패키지가 내 시스템에 있는 것보다 새 libexpat으로 컴파일됐어요. 거의 항상 macOS Tahoe(26.x)에서 **Xcode와 Command Line Tools가 오래됐기** 때문이에요. 보통 brew 자체 출력도 그렇게 알려 줘요. 해결 방법 두 가지:

- 대신 **`uv tool install aipager`를 쓰세요**. uv는 자체 python을 함께 가져와서 이 문제를 완전히 피해요. macOS에서 추천하는 방법이에요. [README](README.md#install)를 보세요.
- **Xcode와 Command Line Tools를 업데이트하세요**:
  ```sh
  sudo rm -rf /Library/Developer/CommandLineTools
  sudo xcode-select --install
  ```
  또는 App Store에서 Xcode를 열고 최신 버전으로 업데이트하세요.

## <a id="modulenotfounderror-aipager"></a>“ModuleNotFoundError: aipager”

내 aipager 프로그램이 가리키는 Python에 더 이상 aipager가 설치되어 있지 않아요(보통 venv를 지운 뒤예요). 다시 설치하세요:

```sh
uv tool install --reinstall aipager
# 또는 처음에 쓴 설치 프로그램: pipx, brew, pip
```

## <a id="the-session-list-was-lost-or-damaged"></a>세션 목록을 잃어버렸거나 망가졌어요

aipager는 세션을 `~/.claude/aipager-sessions.json`에 보관하고 원자적으로 다시 써요(새 파일을 쓰고 이름을 바꿔요). 그래서 쓰는 도중 끊겨도 예전 파일은 멀쩡해요. 그런데도 파일이 없거나, 읽을 수 없거나, 올바른 JSON이 아니면, aipager는 처음부터 시작한다고 로그에 남기고(파일은 있는데 읽을 수 없으면 경고로) 빈 목록으로 시작해요. 멈추지는 않아요. 이 파일에는 백업이 없어요. 중요한 것은 잃어버리지 않아요:

- 실행 중인 세션은 첫 검사에서 dtach 소켓(`/tmp/claude-dtach-*.sock`)으로 다시 찾아요;
- 모든 대화는 Claude Code 자체의 `~/.claude/projects/...` 폴더에 그대로 있어서, 그 세션의 폴더에서 `aipager session <name> --continue`를 실행하면 이어 가요(`--resume`은 직접 고르게 해요);
- 사라지는 것은 종료된 세션 목록(`/resume`), 열려 있던 카드, 그리고 aipager가 세션마다 기억하던 나머지 내용이에요.

aipager가 시작할 때마다 실패하면 `aipager logs`(또는 `journalctl --user -u aipager`)가 이유를 보여 줘요. 이 파일을 옆으로 옮겨서(`mv ~/.claude/aipager-sessions.json ~/aipager-sessions.json.old`) 원인에서 빼 보세요.

## <a id="a-session-launch-fails"></a>세션이 시작되지 않아요

dtach가 거부한 시작은 채팅에 이유를 한 줄로 보여 줘요(소켓이 이미 있음, shell을 실행할 수 없음, 가상 터미널 없음, 이름이 너무 김, 권한 거부, 폴더 없음). 경로를 포함한 dtach 자체의 메시지는 `aipager logs`에 있어요.

## <a id="aipager-doctor-check-list"></a>`aipager doctor` 확인 목록

순서가 중요해요. 뒤의 확인은 모두 앞의 확인이 통과했다고 가정해요. 예상하지 못한 환경 때문에 비정상 종료한 확인은 확인 이름과 오류를 적은 ⚠ 한 줄로 나오고, 나머지 확인은 계속 실행돼요.

| 확인 (`--json` 키) | 무엇을 확인하나요 | 해결 안내 |
|---|---|---|
| `config_parses` | `aipager.yaml`과 `policy.yaml`을 읽을 수 있어요. 뒤의 모든 확인이 설정을 읽으니까 가장 먼저 실행해요 | 오류가 파일과 줄을 알려 줘요 |
| `config` | `~/.config/aipager/aipager.yaml`이 있고 token과 채팅이 들어 있어요 | `aipager config` |
| `token_valid` | token이 Telegram `getMe`에서 통해요 | `aipager config`를 다시 실행 |
| `chat_reachable` | Telegram이 설정된 채팅을 알아요(`getChat`, 아무것도 보내지 않아요) | 봇을 열고 Start를 누르기 |
| `team` | 채팅과 멤버(또는 예전 `team.yaml`): 개인 모드인지 팀 모드인지 알려 주고, 형식이 잘못됐거나 맞지 않는 예전 `team.yaml`을 표시해요 | `aipager config` |
| `role_shell_access` | 멤버가 가진 역할 가운데 Bash를 실행할 수 있는 역할마다 한 번 경고해요(그 안전 규칙은 최선의 노력일 뿐 경계가 아니에요. [그룹](groups.md#roles) 참고) | `policy.yaml`에서 그 역할에 Bash를 주지 않거나, 받아들이기 |
| `peer_messages` | Claude 세션 사이의 메시지: 영수증을 어디에 보관하고 그 폴더가 비공개인지, 이 시스템이 보낸 쪽의 시작 시각을 확인할 수 있는지, 그리고 규칙이 SendMessage를 허용하는 역할마다 경고해요([보안](security.md#messages-between-claude-sessions) 참고) | 함께 쓰는 영수증 폴더를 지우거나(aipager가 다시 만들어요), 그 역할에 SendMessage를 주지 않기 |
| `claude` | 모든 시작이 하는 방식 그대로 `claude` 프로그램을 찾고(`aipager.yaml`의 `claude_path`나 `$AIPAGER_CLAUDE_BIN`이 바로 이겨요. 아니면 `~/.local/bin`, PATH, Homebrew 가운데 가장 새것), 찾은 다른 설치를 모두 나열해요 | Claude Code를 설치하거나, `claude_path` / `AIPAGER_CLAUDE_BIN`을 설정 |
| `claude_auth` | 실제 세션이 받는 것과 같은 환경에서 `claude auth status`를 시험해요. **절대 FAIL이 아니에요**: “로그인 안 됨”과 “시험 자체가 실패함”을 따로 알려 주고, 둘 다 세션 시작을 막지 않아요 | `claude auth login`, 또는 API 키 / `CLAUDE_CODE_OAUTH_TOKEN` 설정 |
| `dtach` | `dtach` 프로그램을 찾았어요(aipager 자체의 `dtach-bin`, 또는 PATH에 있는 것) | `uv tool install --reinstall aipager` |
| `hook_scripts` | `aipager-hook`과 `aipager-statusline`이 PATH에 있어요 | `uv tool install --reinstall aipager` |
| `settings_json` | `~/.claude/settings.json`에 aipager hook이 연결되어 있어요 | `aipager config` |
| `daemon` | aipager가 실행 중이고 소켓이 응답해요. Telegram 속도 제한 상태도 보여 줘요([위](#the-bot-slowed-down-a-rate-limit-429) 참고) | `aipager start` |
| `service_installed` | 선택: 서비스 파일이 있어요 | `aipager service install` |
| `service_unit_path` | Linux만: *설치된* 서비스의 `Environment=PATH=`에 찾은 claude 프로그램의 폴더가 정말 들어 있어요. doctor는 봇 소유자의 대화형 shell에서 systemd 자체의 PATH를 볼 수 없으니 글로 읽어서 확인해요 | `aipager service install --yes` |
| `miniapp` | Mini App이 켜져 있을 때 시작할 수 있는지. **절대 FAIL이 아니에요**: Mini App은 선택 사항이에요 | 알려 주는 해결 방법 |

`aipager doctor --json`은 같은 확인을 이 순서대로, 이 키로 출력해요([명령](commands.md#aipager-doctor---json) 참고).

`aipager doctor --fix`를 실행하면 Claude 로그인 정보를 대화형으로 찾아서 `daemon.env`에 복사하거나(파일은 있지만 로그인 정보가 없을 때도), 설치를 여러 개 찾았을 때 `claude_path`를 고정할 수 있어요. 항상 먼저 물어본 뒤에만 행동해요. PATH에 claude 폴더가 없는 서비스라면 `aipager service install --yes`를 실행하세요.

## <a id="the-daemon-cant-find-claude-or-picks-the-wrong-install"></a>aipager가 `claude`를 찾지 못하거나 엉뚱한 설치를 골라요

예전에는 여섯 곳이 각자 `claude`를 찾았고 서로 다른 결과를 낼 수 있었어요. 이제는 모두 하나의 찾기 도구(`aipager/claude_resolve.py`)를 거쳐요. `aipager.yaml`의 `claude_path`, 그다음 `$AIPAGER_CLAUDE_BIN`이 쓸 수 있으면 바로 이겨요. 아니면 `~/.local/bin`, `$PATH`, Homebrew 폴더에서 찾은 모든 `claude`를 확인하고 가장 새 버전이 이겨요(그래서 다른 설치가 더 새것이면 `$PATH` 순서를 바꿔도 선택이 바뀌지 않아요). `aipager doctor`를 실행하면 정확히 어느 것을 골랐고 그 밖에 무엇을 찾았는지 볼 수 있어요. 엉뚱한 것이 이기면 맞는 것을 고정하세요:

```sh
aipager doctor --fix        # 찾은 설치 가운데에서 대화형으로 고르기
# 또는 직접:
echo 'claude_path: /home/you/.local/bin/claude' >> ~/.config/aipager/aipager.yaml
```

## <a id="sessions-cant-authenticate-under-the-systemd-service-but-claude-works-fine-in-a-terminal"></a>systemd 서비스에서는 세션이 로그인하지 못하는데, 터미널에서는 `claude`가 잘 돌아가요

`systemctl --user` 서비스는 `~/.bashrc` / `~/.profile`을 절대 읽지 않아서, 거기 있는 `export CLAUDE_CODE_OAUTH_TOKEN=…` 줄은 aipager에 절대 닿지 않아요. 대신 서비스는 systemd의 `LoadCredential=`로 `~/.config/aipager/daemon.env`를 읽어요. 단순한 `KEY=VALUE` 파일이고 권한은 0600이며, `aipager service install`을 처음 실행할 때 자동으로 만들어져요(예전 `config.env`에 token이 있었다면 거기서 옮겨 오고, 아니면 로그인 shell에서 한 번 찾고, 그래도 없으면 경고와 함께 비워 둬요). 여전히 비어 있다면 `aipager doctor --fix`가 내가 동의할 때 같은 곳을 다시 찾아서 찾은 줄을 추가하고, 파일의 다른 줄은 그대로 둬요. token은 절대 출력하지 않아요. 또는 직접 그 줄을 추가하세요:

```sh
cat ~/.config/aipager/daemon.env          # 안에 무엇이 있는지(또는 없는지) 보기
echo 'CLAUDE_CODE_OAUTH_TOKEN=sk-...' >> ~/.config/aipager/daemon.env
chmod 600 ~/.config/aipager/daemon.env
aipager service stop && aipager service start
```

이 파일이 무엇을 지키고 무엇을 지키지 못하는지는 [보안 모델: Claude 로그인 정보](security.md#the-claude-credential-what-actually-protects-it)를 보세요.

## <a id="the-daemons-control-socket-moved"></a>aipager의 제어 소켓 위치가 바뀌었어요

`aipager doctor` / `aipager status`는 예전에 항상 `/tmp/aipager.sock`을 봤어요. systemd 서비스에서는 이제 제어 소켓이 `$XDG_RUNTIME_DIR/aipager.sock`에 있어요(`$XDG_RUNTIME_DIR`이 설정되지 않은 경우에만 `/tmp`로 돌아가요. 컨테이너, WSL1, 최소 구성 배포판). 이 소켓 하나만 옮겨졌고, 세션마다의 dtach 소켓(`/tmp/claude-dtach-*.sock`)은 영향을 받지 않아요. 특정 위치가 필요하면 `AIPAGER_SOCKET_PATH`를 설정하세요.

## <a id="update-says-the-restart-would-kill-sessions-killmode"></a>`/update`가 재시작하면 세션이 종료된다고 해요 (KillMode)

이 버전 전에 쓴 서비스 파일에는 `KillMode=` 줄이 없어서 systemd가 `control-group`을 써요. `aipager.service`를 멈추거나 재시작하면 aipager가 시작한 모든 Claude 세션이 종료돼요. 그래서 `/update`는 새 버전을 설치하지만 aipager를 재시작하지 않고, 재시작하면 종료될 세션을 나열해요. 한 번만 고치면 돼요:

```
aipager service install
```

이 명령은 서비스 파일을 `KillMode=process`로 다시 쓰고, systemd를 다시 불러오고, aipager를 재시작해요. 그 재시작은 이미 새 설정으로 실행돼요. `systemctl --user show -p KillMode aipager.service`로 확인하세요(`KillMode=process`). 이 설정에서는 systemd가 재시작할 때마다 내 세션에 대해 “left-over process” 줄을 로그에 남겨요. 정상이에요.

## <a id="aipager-update-cant-find-uv--pipx"></a>`aipager update`가 uv / pipx를 찾지 못해요

`aipager update`는 설치 프로그램을 내 `PATH`와 `~/.local/bin`, `~/.cargo/bin`, Homebrew 폴더, `/usr/bin`, `/bin`에서 절대 경로로 찾아요. 그래도 `pipx`를 찾을 수 없다고 하면, 이 aipager를 만든 설치 프로그램이 이제 없거나 다른 곳에 있는 거예요. 가지고 있는 도구로 다시 설치하세요(`pipx install --force aipager`, `uv tool install --reinstall aipager`).

## <a id="update-refused-editable--not-your-install"></a>업데이트 거부: editable 설치 / 내 설치가 아님

`/update`와 `aipager update`는 내 사용자가 소유하고 쓸 수 있는 설치만, 그것을 만든 설치 프로그램으로 업그레이드해요. 다음 경우에는 이유를 알려 주며 거부해요:

- editable 모드의 소스 사본(개발용): `git pull`로 업데이트하세요;
- Nix, Snap, Docker, 운영 체제 패키지로 한 설치: 그 시스템 자체의 업데이트를 쓰세요;
- 다른 사용자가 소유한 설치(예: `/opt` 아래의 root 소유 venv): 그 사용자로 업데이트하세요;
- 로컬 폴더에서 한 설치(`pipx install /path/to/aipager`): `여기서 업데이트할 수 없음: it was installed from a local folder; update it from that folder`. 새 버전을 그 폴더로 받아서 거기서 다시 설치하거나, `pipx install --force aipager`로 PyPI 설치로 바꾸세요;
- 어느 설치 프로그램인지 알 수 없는 설치(`could not tell which installer owns this aipager`): `uv tool install aipager`나 `pipx install aipager`로 다시 설치하세요.

## <a id="update-failed-unexpected-argument---refresh"></a>업데이트 실패: unexpected argument '--refresh'

aipager 1.0.0 이하는 `uv tool upgrade aipager --refresh`를 실행했는데, 이것을 받아 주는 uv 버전이 없어서 uv로 설치한 aipager의 업데이트는 모두 `error: unexpected argument '--refresh' found`로 멈췄고 아무것도 재시작하지 않았어요. 더 새 버전은 `uv tool upgrade aipager`를 실행해요. 한 번 넘어가려면 그 컴퓨터의 터미널에서 업데이트하세요(그 버전들의 `aipager update`에도 같은 문제가 있어요):

```
uv tool upgrade aipager
systemctl --user restart aipager.service
```

서비스가 없다면(또는 macOS에서는) aipager를 시작했던 방식으로 재시작하세요.

## <a id="claude-code-update-installed-with-npm-into-a-folder-you-cant-write-to"></a>Claude Code 업데이트: 내가 쓸 수 없는 폴더에 npm으로 설치됨

`sudo npm install -g`로 설치한 Claude Code는 root만 바꿀 수 있는 폴더(예: `/usr/lib/node_modules`)에 있어서, 내 사용자로 실행한 `claude update`는 `global folder isn't writable`로 실패해요. `/update`와 Mini App은 더 새 버전을 `여기서 업데이트할 수 없음: installed with npm into a folder your user can't write to`와 함께 보여 주고, 그것을 위한 버튼은 없어요. aipager를 실행하는 사용자로 `claude install`을 한 번 실행하세요. 홈 폴더 아래의 Claude Code 자체 설치 프로그램으로 바꿔서 sudo 없이 업데이트할 수 있게 돼요. 그다음 aipager를 재시작하세요(`systemctl --user restart aipager.service`, 또는 시작했던 방식으로). 실행 중인 aipager는 시작할 때 찾은 Claude Code를 계속 쓰고, 재시작한 뒤에는 찾을 수 있는 가장 새 것을 써요. `aipager.yaml`의 `claude_path`(`aipager doctor --fix`가 설정할 수 있어요)나 `AIPAGER_CLAUDE_BIN`이 npm 설치를 가리키면, 먼저 `~/.local/bin/claude`를 가리키게 하거나 지우세요. 이것들이 항상 이기니까요.

## <a id="updated-but-the-version-didnt-change"></a>업데이트했는데 버전이 바뀌지 않았어요

git 주소로 만든 pipx 설치는 PyPI가 아니라 그 git 소스에서 업그레이드해요. 소스에 더 새 것이 없으면 `/update`가 `✅ aipager는 이미 A 버전이에요. 이 pipx 설치는 git 소스에서 업데이트하는데, 거기에 더 새 버전이 없어요.`라고 해요. 거기에 새 버전을 push하거나 고르세요. 또는 `pipx install --force aipager`로 PyPI 설치로 바꾸세요.

**서비스 없이** 실행되는 aipager(터미널이나 tmux의 `aipager start`, Docker, launchd처럼 서비스 관리자가 재시작해 주지 않는 경우)는 업데이트를 설치하지만 재시작할 때까지 예전 코드로 계속 실행돼요. 그러면 `/update`가 `aipager B 버전이 설치됐고 재시작을 기다리고 있어요 (실행 중: A).`라고 알려 주고, 이벤트마다 새로 시작하는 Claude Code의 hook은 이미 B를 실행하고 있을 수 있어요. 그래서 소유자는 업데이트가 절반만 끝났다는 알림을 한 번 받아요(hook이 처음 B를 실행하고 2분 뒤, 그래서 설치 바로 뒤에 이어지는 재시작에서는 절대 나오지 않아요. 그리고 소유자의 개인 채팅에서 진행 중인 작업이 없을 때). aipager가 B로 실행되면 그 알림은 버튼 없이 `✅ aipager 업데이트가 끝났어요. 이제 aipager는 B 버전으로 실행 중이에요.`로 바뀌어요. **🔄 지금 재시작**을 누르세요(업데이트 메시지, 알림, 또는 Mini App의 **설정 → 업데이트**에서). aipager는 진행 중인 작업이 없을 때까지 기다렸다가 깔끔하게 멈추고 새 버전을 제자리에서 시작해요. 같은 터미널, tmux 창, 컨테이너의 같은 프로세스가 같은 명령과 같은 환경으로 실행돼요.

제자리 시작이 실패하면 예전 프로세스가 끝난 뒤 따로 떨어진 복사본이 aipager를 시작해요. 그때 출력은 터미널이 아니라 자체 재시작 로그(인스턴스 폴더의 `aipager.log`, 또는 `/tmp/aipager.log`)로 가고, 그 복사본이 돌아오지 않으면 실행할 명령을 aipager 로그가 알려 줘요. 인터프리터 자체가 사라졌거나(환경이 지워짐) 아무것도 시작할 수 없으면 aipager는 코드 1로 종료하고, aipager 로그는 `restart.failed:`와 실행할 명령으로 끝나요. 다음에 시작할 때 업데이트를 알려 줘요.

**지금 재시작** 버튼이 없을 때는 메시지가 내 환경에 맞는 단계를 알려 줘요. 터미널이나 tmux에서는 Ctrl+C를 누르고 `aipager start`를 실행하고, macOS에서는 `launchctl kickstart -k gui/<uid>/com.aipager.daemon`, Docker에서는 호스트에서 `docker restart <container>`를 실행해요. Docker 컨테이너를 다시 만들면(`down` 뒤의 `docker compose up`, 또는 `docker run`을 다시 실행) 이미지에서 시작하기 때문에 그 이미지에 든 aipager 버전으로 돌아가요. 더 새 aipager를 유지하려면 더 새 이미지를 쓰세요.

## <a id="the-daemon-didnt-come-back-after-an-update"></a>업데이트 뒤에 aipager가 다시 실행되지 않아요

업그레이드는 무엇이든 재시작하기 전에 새 버전을 import할 수 있는지 확인하지만, 새 aipager가 그래도 시작하지 못하면 systemd가 5초마다 다시 시도해요. 이유를 보려면:

```
journalctl --user -u aipager.service -n 50
```

같은 설치 프로그램으로 이전 버전을 다시 설치하고, 재시작하세요:

```
pipx install --force aipager==<previous>        # 또는:
uv tool install --force aipager==<previous>     # 또는:
<venv>/bin/python -m pip install aipager==<previous>
systemctl --user restart aipager.service
```

로컬 경로에서 한 pipx 설치라면: 예전 commit에서 `pipx install --force /path/to/aipager`. 오래된 알림이 아직 기다리고 있다면 `rm -f ~/.local/share/aipager/update-restart.json`으로 지우세요(어차피 24시간 뒤에는 무시돼요). `~/.local/share/aipager/update.lock`은 잠금 파일일 뿐이라 업데이트가 실행 중이지 않을 때는 지워도 안전해요.

## <a id="an-update-was-interrupted-when-aipager-shut-down"></a>“aipager가 종료될 때 업데이트가 중단됐어요”

`/update`가 설치하는 동안 aipager가 멈췄고(또는 재시작했고), 설치 프로그램도 함께 멈춰서 설치가 반만 쓰였을 수 있어요. aipager나 Claude Code가 이상하게 동작하면 고치세요. aipager라면 메시지에 있는 다시 설치 명령(예: `pipx install --force aipager`)을 실행한 다음 `systemctl --user restart aipager.service`를 실행하고, Claude Code라면 `claude update`를 다시 실행하세요.

aipager를 다시 시작하지 못한 지금 재시작(서비스 없음)은 aipager 로그에 `restart.failed:`와 실행할 명령을 남겨요. 그 명령을 실행하면 새 aipager가 업데이트를 알려 줘요.

## <a id="a-chat-is-in-the-wrong-language"></a>채팅의 언어가 잘못됐어요

aipager의 언어는 채팅에 고른 언어에서 오고, 채팅 안의 메시지에서 오지 않아요. 그 채팅에서 `/language`를 열고 원하는 언어를 고르세요. 이 선택이 다른 모든 것보다 우선해요. 개인 채팅에서 **자동**은 내 Telegram 앱의 언어(Telegram의 설정 → 언어)를 따라요. aipager에 없는 언어는 영어로 보여요. 언어 기능이 생기기 전부터 있던 개인 채팅은 내가 언어를 고를 때까지 영어를 써요. 그룹은 관리자가 언어를 고를 때까지 영어를 써요. 언어는 aipager 자체의 글만 바꿔요. Claude는 aipager가 무엇을 보여 주든 내가 쓴 언어로 답해요. [언어](commands.md#languages)를 보세요.

## <a id="the-bot-description-is-not-the-one-i-wrote"></a>봇 설명이 내가 쓴 것과 달라요

aipager는 자기가 쓰는 언어마다 봇의 설명과 짧은 설명을 설정해서, Telegram 앱이 그 언어인 사람들에게 보여 줘요. Telegram은 내가 BotFather에서 쓴 글 대신 이 글을 그 사람들에게 보여 줘요. aipager는 내 설명이 없을 때만 이렇게 하니까, 이 버전을 설치하기 전에 쓴 설명은 모두에게 보여요. aipager가 자기 설명을 설정한 뒤에 썼다면, 그 언어를 쓰는 사람들은 여전히 aipager의 설명을 봐요. 내 설명을 보여 주려면 Bot API로 언어마다 aipager의 글을 지우세요(빈 글과 그 `language_code`로 `setMyDescription`과 `setMyShortDescription`). 내 설명이 있는 동안 aipager는 절대 다시 설정하지 않아요.

## <a id="aipager-says-languagesjson-could-not-be-used"></a>aipager가 languages.json을 쓸 수 없다고 해요

aipager는 언어에 대해 알게 된 것(언어 기능이 생기기 전부터 여기 있던 사람, 사람마다 마지막으로 본 Telegram 언어, 한 번만 하는 언어 제안과 첫 만남 안내 줄이 나갔는지)을 `~/.local/state/aipager/languages.json`에 보관해요. 이 파일을 읽을 수 없으면 aipager는 같은 폴더의 `languages.json.bad-<time>`으로 옮기고, 로그에 경고를 남기고(내가 결정할 때까지 시작할 때마다 다시 남겨요), 내가 결정할 때까지 모든 채팅을 영어로 두고 언어 제안도 하지 않아요. 고칠 수 있다면 예전 파일을 되돌려 놓거나, `languages.json`을 지우고 재시작하세요. 그러면 aipager는 아는 모든 사람을 다시 원래 있던 사람으로 여겨요(스스로 고를 때까지 영어). 누군가 고른 언어는 이 파일에 없고 절대 사라지지 않아요.

## <a id="right-to-left-text-looks-wrong"></a>오른쪽에서 왼쪽으로 쓰는 글이 이상해 보여요

페르시아어와 아랍어에서 aipager는 자기 글에 보이지 않는 방향 표시를 넣어서 각 줄을 오른쪽에서 왼쪽으로 읽게 하고, 세션 이름, 명령, 코드는 왼쪽에서 오른쪽으로 유지해요. 그래도 어떤 줄이 뒤죽박죽으로 보이면, 대개는 Telegram 앱이 방향이 섞인 글을 다르게 그리는 거예요. 신고하기 전에 다른 앱(휴대폰과 데스크톱 또는 웹)에서 확인해 보세요. Claude 자신의 답변은 Claude가 쓴 그대로 보이고 아무 표시도 더하지 않아요.

## <a id="a-translated-keyboard-word-does-nothing-in-another-chat"></a>번역된 키보드 단어가 다른 채팅에서는 작동하지 않아요

채팅마다 답장 키보드는 그 채팅의 언어로 나와요. 메인 키보드의 단어(상태, 중지, 신규, 종료 등)는 모든 채팅에서, 모든 언어로 알아듣고, 영어 단어는 항상 작동해요. 템플릿이나 명령 버튼의 단어는 달라요. 다른 언어로 설정된 채팅에서 입력하면 버튼이 아니라 Claude에게 가는 메시지라서, 한국어 “대화 지우기”는 페르시아어나 영어 채팅에서 절대 `/clear`를 실행하지 않아요. 버튼 자체를 쓰거나 영어 명령(`/clear`)을 쓰세요. 채팅의 언어가 바뀐 뒤 키보드가 예전 것처럼 보이면 `/start`를 보내서 지금 키보드를 받으세요. 내 세션 이름이기도 한 단어는 그 세션과 대화해요. `/mode`는 `ask`, `auto`, `full`을 항상 모드로 읽고, 번역된 모드 단어는 실행 중인 세션이 정확히 그 이름을 가졌을 때만 세션으로 읽어요.

## <a id="still-stuck"></a>아직 해결되지 않았나요?

문제 신고 보고서를 보내세요. 봇과의 내 개인 채팅에서 `/help` 아래의 **문제 신고**를 누르거나, 터미널에서 `aipager report`를 실행하세요. 무엇이든 보내기 전에 보고서 전체를 볼 수 있고, 보고서에는 채팅, 메시지, 이름, 경로, token이 들어 있지 않아요([문제 신고](problem-reports.md) 참고). 보낸 뒤에는 `ap1-7c41d09e2b3a` 같은 참조 번호를 받아요.

그다음 이야기를 나누고 싶다면 [github.com/dev-aly3n/aipager/issues](https://github.com/dev-aly3n/aipager/issues)에 이슈를 열고 그 참조 번호를 적어 주세요. `aipager doctor`나 `aipager logs`의 출력은 이슈에 붙여 넣지 마세요. 내 채팅 ID, 홈 폴더 안의 경로, 세션 이름, 메시지 앞부분이 들어 있어요. 로그 한 줄이 필요하면 aipager 개발자가 그 한 줄만 요청할 거고, 나는 먼저 개인적인 내용을 지울 수 있어요.

## <a id="see-also"></a>함께 보기

- [아키텍처](architecture.md): 각 부분이 하는 일.
- [보안 모델](security.md): aipager가 건드리는 것과 건드리지 않는 것.
