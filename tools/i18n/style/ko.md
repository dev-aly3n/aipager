# Korean (ko) style guide

The translator (Claude) reads TRANSLATING.md first, then this, then the glossary.

## Tone
Plain, direct and friendly, in the polite everyday style (해요체), the way Telegram's own Korean interface talks. Everyday Korean, not a word-for-word copy of the English: short sentences, no officialese ("~하시기 바랍니다", "진행" piled up), no slang. Korean has no gender in verbs; a person named by {person} takes 님 ("{person} 님이 거부했어요"), which also fixes the particle.

## Address
해요체

## Punctuation and quotation marks
Use “ ” for quotations and standard Korean spacing.
- No long dashes (U+2014): use "-" or parentheses, as the English does.
- No exclamation marks.

## Digits
Latin digits 0-9.

## Never translated
Product names (Claude, Claude Code, Anthropic, aipager, Telegram, Mini App, GitHub, PyPI), Claude Code's tool names (Bash, Read, Write, Edit, Grep, Glob, WebFetch, Agent, AskUserQuestion), model names and aliases (opus, sonnet, haiku, fable, opusplan), commands (/new, /status, /x1, /mode auto), session names, @usernames, paths, code and anything inside a placeholder. These reach the text only as placeholders or verbatim; CI fails a translation that spells one differently.

## Placeholders and tags
Keep every {placeholder} and every HTML tag of the English, exactly once each. Never translate inside <code> or <pre>.

## Markers, keyboard words and modes
- Keep `**`, `_`, every HTML tag and every {placeholder} exactly as in the English: the same number of `**` and `_`, never a backtick.
- A keyboard word (status, stop, new, kill) is ONE word with no space (상태, 중지, 신규, 종료): it is typed as a word.
- A mode word (mode.word.auto, mode.word.ask, mode.word.full: 자동, 확인, 전체) is ONE word with no emoji: people type it after /mode, so it never changes once chosen. A mode label (mode.auto, mode.ask, mode.full) is what people see: the emoji, that same word and the word for mode (🤖 자동 모드, 💬 확인 모드, 🔓 전체 권한 모드); a sentence around {mode} must not add its own word for mode.
- A button or sentence that names a permission mode names it as a mode (operator, 2026-10-11): the switch buttons (modecard.btn.to_*, ui.btn.switch_to_*), the "instead" buttons (mode.*_instead) and the busy confirms (ui.confirm.perms_busy_*) say the mode word and the word for mode, as mode.auto and mode.ask do. The typed words stay one word.
- The "instead" buttons on /new (mode.*_instead) say exactly what the switch buttons say ("💬 확인 모드로 전환"), not a word for "instead" (operator, 2026-10-16: a literal "instead" read oddly on a button).
- A default button label (Continue, Run tests...) stays short: it is a button. Its payload stays English (Claude receives the English).

## Width and spacing
- Button and label budgets count display cells: every Hangul syllable counts 2. Keep buttons short.
- Korean spacing (띄어쓰기) between words; half-width punctuation (. , ? : ( )) as in normal Korean text, with no space before a mark.
- Sentences end in 해요체 (-해요, -어요, -세요 for a request). Buttons and labels are nouns or short verb stems (중지, 허용, 다시 시도), not full sentences.
- A Korean particle after a {placeholder} or a Latin name can't follow its last sound: prefer a form that needs no particle (“{session} 세션이”, “{session}: ...”) or the neutral one.

## Words people actually say (use these)
| English idea | write | not |
|---|---|---|
| session | 세션 | |
| agent, background agent | 에이전트, 백그라운드 에이전트 | |
| background shell command | 백그라운드 명령 (the first line under an answer adds "(shell)") | 명령 alone |
| your message to Claude (prompt) | 메시지 | 프롬프트 (only where the text is about prompts as such) |
| a permission or question prompt | 요청 (권한 요청) | |
| compact (Claude Code's /compact) | 대화 압축 | |
| context window | 컨텍스트 | 컨텍스트 창 |
| Claude's conversation | 대화 | |
| a Telegram chat | 채팅 (이 채팅, 개인 채팅, 그룹) | |
| update | 업데이트 | |
| restart | 재시작 | 다시 시작 |
| link | 링크 | |
| repository | 저장소 | 리포지토리 |
| hung, frozen | 멈춤 | 정지됨 |
| crashed | 비정상 종료 | 충돌 |
| voice message | 음성 메시지 | |
| computer, machine | 컴퓨터 | 머신, 호스트 |
| default | 기본값 | |
| private chat (DM) | 개인 채팅 | DM |
| tool (Claude's tools) | 도구 | |
| idle (a session waiting for a message) | 준비됨 | 유휴 |
| busy, working | 작업 중 | 바쁨 |
| waiting on you | 답변 대기 중 | 대화형 |
| ended (a session no longer running) | 종료됨 | |
| end a session (Kill, End) | 종료 | 강제 종료 |
| the aipager program (daemon, service) | aipager | 데몬, 서비스 |
| saved transcript | 저장된 대화 | 대화 기록, 트랜스크립트 |
| working directory | 폴더 | 작업 디렉터리 |
| the person who makes aipager (maintainer) | aipager 개발자 | 관리자 (that is the admin) |
| the bot's owner (operator) | 봇 소유자 | 운영자 |
| bot | 봇 | |
| error | 오류 | 에러 |

## Vocabulary (one word per idea)
- session 세션; turn: 차례 where people take turns ("지금은 다른 사람 차례예요"), 작업 for Claude's work on one message ("이 작업이 끝나면"); card 카드; permission 권한; a permission request 권한 요청; question 질문.
- allow 허용 (answered: 허용됨); deny 거부 (answered: 거부됨); stop 중지 (멈추다 in sentences); resume 재개; queue 대기열; agent 에이전트; background 백그라운드.
- owner 소유자; admin 관리자; member 멤버; group 그룹; settings 설정; folder 폴더; model 모델; role 역할; mode 모드; "Mini App".
- Kept in English: commit, token, diff, ctx, hook. A diff is "변경 사항 (diff)" where it is a label.
- Buttons: 허용, 거부, 중지, 재개, 재시작, 삭제, 이름 변경, 취소, 닫기, 다시 시도, 종료, 보내기, 지금 보내기.
- Same situation, same words: "Couldn't …" is "…할 수 없어요" ("파일을 다운로드할 수 없어요"); "Couldn't reach the server" is "서버에 연결할 수 없어요"; a refused restart is "재시작하지 않았어요: …"; a stale button or card is "오래된"; an expired one "만료된"; a running session "실행 중".
- {clock} is a duration: "완료 ({clock})", "작업 중 ({clock})", never "{clock} 후" (reads like a time of day).

## Plurals
other only: Korean does not mark the plural after a number (세션 2개). Use a counter where Korean needs one (개, 번, 개의).
