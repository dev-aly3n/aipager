# Korean (ko) style guide

The translator (Claude) reads this with the glossary before every batch.

## Tone
Plain, direct and friendly, in the polite everyday style (해요체). Sound like the English: plain words, short sentences, no slang.

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
- A default button label (Continue, Run tests...) stays short: it is a button. Its payload stays English (Claude receives the English).

## Width and spacing
- Button and label budgets count display cells: every Hangul syllable counts 2. Keep buttons short.
- Korean spacing (띄어쓰기) between words; half-width punctuation (. , ? : ( )) as in normal Korean text, with no space before a mark.
- Sentences end in 해요체 (-해요, -어요, -세요 for a request). Buttons and labels are nouns or short verb stems (중지, 허용, 다시 시도), not full sentences.
- A Korean particle after a {placeholder} or a Latin name can't follow its last sound: prefer a form that needs no particle (“{session} 세션이”, “{session}: ...”) or the neutral one.

## Terms
- session 세션; turn 턴; card 카드; permission 권한; prompt 프롬프트 (a message to Claude: 메시지); question 질문; allow 허용; deny 거부; stop 중지 (멈추다 in sentences); resume 재개; queue 대기열; agent 에이전트 (a subagent: 하위 에이전트); background 백그라운드; owner 소유자; admin 관리자; member 멤버; chat 채팅; group 그룹; settings 설정; update 업데이트; folder 폴더; model 모델.
- Kill (end a session's process) 종료; Restart 다시 시작; Delete 삭제; Rename 이름 바꾸기; Compact 압축; Retry 다시 시도; Cancel 취소; Back 뒤로; role 역할; transcript 대화 기록; daemon 데몬; install 설치.
- Kept in English: commit, token, diff, log, ctx.
