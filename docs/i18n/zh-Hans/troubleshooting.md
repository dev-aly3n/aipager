# 故障排除

<!-- i18n-source: docs/troubleshooting.md @ 33da7624 -->

> 本页译自英文；如果两者不一致，以英文版为准。（[English](../../troubleshooting.md)）

常见故障和修复方法。拿不准的时候，运行：

```sh
aipager doctor
```

它会按顺序运行下面的每一项检查，并为每个失败打印标准的修复提示。源码：`aipager/doctor.py`。

## <a id="another-aipager-daemon-already-owns-the-socket"></a>“Another aipager daemon already owns the socket”

两个 aipager 不能共用一个控制套接字。这个错误表示有一个 aipager 仍在运行，并在这个套接字上应答（套接字是 `$XDG_RUNTIME_DIR/aipager.sock`，没有设置 `$XDG_RUNTIME_DIR` 时是 `/tmp/aipager.sock`）。崩溃的 aipager 留下的套接字文件不是问题：下次启动会删掉它并创建新的。先停止正在运行的 aipager：

```sh
aipager service stop          # 如果它作为服务运行
pkill -f 'aipager start'      # 否则
```

如果套接字检查漏掉了它，`aipager start` 仍会以 `aipager is already running (pid=N)` 拒绝启动，并指出它的锁文件（`~/.local/share/aipager/daemon.lock`）：用同样的方法停止那个进程。

## <a id="telegram-bot-doesnt-respond"></a>Telegram 机器人没有回应

1. `aipager status`：如果 aipager 那一行显示 `not running`，就启动它。
2. `aipager doctor`：`token_valid` 和 `chat_reachable` 检查会完整地调用一次 Telegram API，并显示确切的错误。
3. 聊天不对：运行 `aipager config` 并选择 **Test bot reachability**。要修正一个错误的私聊，用 **Add a DM scope** 添加正确的私聊（见[如何找到 ID](groups.md#finding-ids-auto-detect)），然后在 **Edit a scope** 里删除错误的那个。
4. 机器人从未收到过 `/start`：在 Telegram 里打开机器人，点一次 Start。

## <a id="busy-cards-got-slower"></a>工作卡片变慢了

Telegram 大约允许每秒向一个聊天发一条消息（向群组则是每分钟 20 条）。每条消息和每次编辑都算在内。aipager 给每个聊天一份自己的额度，大约每秒 1 次调用，允许少量突发，这个聊天的工作卡片**共用**这份额度。“正在输入…”提示也在这份额度里，是这个聊天发送的东西里**最不重要的**（见下面的“输入提示”）。

输入提示的份额会先被留出来（在聊天里运行最久的任务不满十分钟时，每 4.5 秒一次调用，任务越久越少；见“输入提示”），卡片再分剩下的部分。所以私聊里的一张卡片大约每 4.8 秒刷新一次；同一个聊天里有两个会话在工作时，每张卡片大约每 9.7 秒刷新一次；三个时大约每 14.5 秒。**卡片变慢是额度在正常工作，不是 bug。** 回答、回复和按钮的响应绝不会为了给卡片腾地方而变慢：它们始终有预留的名额，而一次没有名额的卡片编辑会直接跳过，在下一轮重试，而不会排在你的回答前面。

在**群组**里，无论有多少会话，上限都是每分钟 20 次调用，光是输入提示就会用掉其中的三分之二。群组的所有卡片加起来仍保持每 10 秒刷新一次（只有一张卡片时为 11 秒），输入提示用剩下的：所以在群组里，它偶尔会消失一两秒。

要让卡片更快：每个聊天里同时运行更少的会话，或者给最忙的会话一个单独的聊天（`aipager config`）。你也可以调整 `STREAM_EDIT_INTERVAL`（默认 `1.2`，卡片正在输出文字时的节奏）和 `BUSY_EDIT_INTERVAL`（默认 `3.0`，卡片安静时的节奏），它们是 aipager 的环境变量（见[环境中的设置](commands.md#settings-in-the-environment)）。把其中任何一个设得*低于*聊天的下限是无害的，也不会改变任何东西：以下限为准，正是这一点让聊天无论你怎么设置都保持在 Telegram 的限制之内。

### <a id="a-long-turns-card-refreshes-less-often"></a>长任务的卡片刷新得更少

上面的节奏适用于任务的前两分钟。之后卡片会**随着任务变久而变慢**，从第 10 分钟起，它的计时以 30 秒为一步跳动，与刷新节奏一致，所以它从不会看起来像卡住了：

| 任务已进行 | 卡片最多每隔多久刷新 | 计时显示 |
|---|---|---|
| 0–2 分钟 | 同上（私聊里一张卡片约 4.8 秒） | `0:45` |
| 2–10 分钟 | 10 秒 | `4:10` |
| 10–60 分钟 | 30 秒 | `23:00`、`23:30` |
| 超过 60 分钟 | 30 秒 | `1:23:00`、`1:23:30` |

计时向下取整到 30 秒的一步，刷新之间至少间隔 30 秒，有时稍长一些。所以卡片偶尔会跳过一步：在 23 分 29 秒的一次刷新显示 `23:00`，如果下一次在 24 分 1 秒到来，它显示 `24:00`，于是 `23:30` 被跳过了。

新的工具行或新的句子会等下一次刷新；**状态变化**（会话从工作中变为等待后台代理，或者反过来）会立即显示，最多每 10 秒一次。正在运行的代理行和等待行也用同样的步长。已完成的卡片会按实际秒数显示任务的时长（`23:17`）。

正是这一点让一个四小时的任务不会把一条消息编辑成千上万次：2026-09-23 曾有一张卡片正是这样做的，结果那个聊天被封禁了七个小时。如果想在整个任务期间保持快速节奏，关闭 **`/settings` → ⏱ 长任务的卡片更新**（对整个聊天，或者在 Mini App 或 `👤 各会话的设置` 里对单个会话）。

### <a id="the-typing-bubble"></a>输入提示

“正在输入…”提示由每个聊天**一个**循环发送，无论有多少会话在里面工作（在每个 Telegram 客户端里，这个提示都是属于整个聊天的），每 `TYPING_INDICATOR_INTERVAL` 秒一次（默认 4.5：Telegram 会在 5 秒后清除输入状态；`0` 关闭输入提示）。在聊天的额度里，它算作这个聊天发送的最不重要的东西：

- 它**随着任务变久而变慢**，依据是聊天里运行最久的任务的时长：前十分钟每 4.5 秒一次（提示一直亮着），之后每 15 秒一次；Telegram 显示它 5 秒，所以较久的任务的提示会闪烁，而不是一直亮着。按 4.5 秒算，光是它一项每小时就有 800 次调用，每小时上限（见下文）会让它长时间关闭；关闭 **⏱ 长任务的卡片更新** 会让它保持 4.5 秒；
- 它**在每个任务开始时就显示**；刚开始的卡片会刷新得稍慢一点来腾出位置：输入提示的份额在卡片分配剩余额度之前就留出来了（见上文），所以两者加起来绝不会超过聊天的上限；
- 它绝不会占用卡片编辑需要的名额：在卡片编辑之后紧接着到点的输入提示会等半秒（`TYPING_RETRY_WAKE`）再试，所以它最多晚一两秒，绝不会丢失；
- 当聊天每小时的发送量变高时（见下文），它是第一个被舍弃的；
- 输入提示本身遇到 429 只会挡住输入提示，时长正是 Telegram 要求的那么久（卡片照常继续），但它会让聊天进入六小时的警告期（速率上限为每秒 0.5 次调用，所以卡片也会变慢）；
- 输入提示遇到**封禁**（`retry_after` 超过下面的上限）时，会像任何其他调用遇到封禁一样让聊天停止发送：不再向那里发送任何东西，任务的回答会被暂缓，在封禁解除时送达。

它以前是免费的（每个工作中的会话一个循环，不计入任何额度），直到 2026-09-23 一个私聊里的两个会话在三个小时里发送了约 5,400 次，Telegram 对输入提示本身限流了。

## <a id="the-bot-slowed-down-a-rate-limit-429"></a>机器人变慢了：限流（429）

如果 Telegram 仍然对某个聊天限流，它会对一次调用回复 `429` 和几秒的 `retry_after`。aipager 会在正好这么长的时间里停止调用这个聊天，之后把被推迟的那一次重新发送，并且**把这个聊天的卡片刷新间隔加倍**（最多 ×8）。每过一分钟安静时间就减半，直到恢复正常。

你会看到：

- `aipager status`（以及 `aipager doctor` 的 `aipager daemon` 一行）显示 **`Telegram flood backoff ×4 (chat …), last 429 12 s ago`**。
- `aipager logs` 里恰好一行，没有 traceback：`flood: chat … 429 retry_after=5s → cadence ×2`。
- 没有任何东西停止发送，**也没有回答丢失**：它只是被推迟，没有被丢弃。
- 聊天**挣来的发送速率减半**，并进入**六小时的警告期**（`FLOOD_WARNING_HOURS`）：它的速率不能超过每秒 0.5 次调用（`FLOOD_WARNED_CEILING`），并且回升得很慢。`aipager status` 会显示：`Telegram chat 123: rate 0.25/s (ceiling 0.50/s), 212/1200 calls in the last hour (as of 18s ago), 7/30 in the last minute - warning regime, 5h 52m left`。

你什么都不用做。它会自己恢复。聊天安静一两分钟后，放慢发送的那一行就会消失；每个安静时段（平常是一分钟，警告期内是 36 分钟）速率回升每秒 0.1 次调用，警告期结束时恢复完整的上限。在此期间，`aipager doctor` 的 aipager 那一行一直是绿色的：一个聊天在放慢发送是正常运行。（以前一次 429 大约五分钟后就被忽略了。2026-09-23 那次封禁之前，Telegram 唯一的一次警告早了 3 小时 23 分钟，而那个聊天在警告之后几分钟就又回到了全速。）

### <a id="the-hourly-budget"></a>每小时额度

每个聊天还有一个滚动的**小时**：任何 60 分钟内最多 `FLOOD_HOURLY_MAX`（1200）次调用，输入提示、卡片编辑和回答都算在内；只有表情回应不算。最后的 120 次（`FLOOD_HOURLY_ESSENTIAL_RESERVE`）留给回答、回复和请求：

- 一小时内到 810 次调用时，输入提示暂停（降到 648 以下时恢复）；
- 到 1080 次时，聊天进入**最小模式**（见下文），直到这一小时降到 864；
- 回答绝不会因为每小时上限而被拒绝；如果它们有时超过 1200，日志会每小时记录一次。

`aipager status` 显示聊天在过去一小时里的调用次数和它的额度。这个数字来自状态文件，aipager 在有调用时最多每分钟重写一次这个文件，所以数字旁会注明它是多久以前的。两个一直在工作的会话连续几小时向一个私聊输出，也远低于这个额度（重放 2026-09-23 那次事件：最忙的一小时约 900 次调用，而按模型估算，同样的工作在没有额度时约为 3,400 次）。

### <a id="the-earned-rate-and-why-your-cards-may-be-slower-than-they-were"></a>挣来的速率，以及你的卡片为什么可能比以前慢

从 0.7.13 起，每个聊天的持续发送速率是**学出来的，而不是预先假定的**。Telegram 不公开实际适用于你的机器人的上限（它取决于你账号最近的历史），所以 aipager 让每个聊天从每秒 0.5 次调用开始，每安静一分钟加 0.1，最高到每秒 1 次，遇到 429 时减半，遭到封禁后降到 0.05。因此，一个刚被封禁的机器人发送得会比从未被封禁的机器人谨慎得多，并在之后的几个小时里把速度挣回来。

如果一个聊天的速率降到每秒 0.2 次调用以下，或者每小时额度中给卡片和输入提示的部分已经用完，聊天就会进入**最小模式**：工作卡片停止更新，只显示一行固定的 `⏳ x1 · 工作中（卡片暂时不更新）`，输入提示停止，置顶状态栏显示 `⏸ 卡片暂时不更新（Telegram 每小时上限）`（如果是速率让聊天进入这种状态，则显示 `（Telegram 限制）`），在恢复之前不再有其他变化。**回答、回复和权限请求照常发送**：这正是它的目的。卡片约占这个机器人发送内容的 95%，回答约占 5%，所以在压力下它舍弃的是画面，而不是工作。`aipager doctor` 会把最小模式作为警告报告，这样暂停的卡片就绝不会被误认为坏掉了；速率恢复后它会自行解除。

## <a id="the-bot-went-quiet-flood-control"></a>机器人没有声音了：Telegram 封禁发送（flood control）

超过 `TELEGRAM_MAX_RETRY_AFTER`（默认 90 秒）的 `retry_after` 不是限流，而是**封禁**，它可能持续几个小时。从聊天里看，就像机器人死掉了：消息仍然送到 Claude，会话继续工作，但没有任何回复回来。

你会看到：

- `aipager status`（以及 `aipager doctor` 的 `aipager daemon` 一行）显示 **`Telegram flood-muted until HH:MM (chat …)`**。
- `aipager logs` 里有一行：`Telegram flood control — chat … muted for Ns (until HH:MM) …`，然后那个聊天就没有动静了。之后会有一行 `flood mute on chat … lifted`。
- `aipager status` 还会完整显示聊天的状态：`Telegram chat 123: rate 0.05/s (ceiling 0.50/s), 431/600 calls in the last hour (as of 12s ago) - MINIMAL MODE, card updates paused - flood-muted until 09:41 - warning regime, 5h 59m left (1 ban(s) in the last 7 days)`。

aipager 会在正好 Telegram 要求的时间里停止向这个聊天发送，并且**不向它发起任何形式的调用**：没有回答，没有工作卡片的编辑，没有附件，没有表情回应，没有输入提示，没有点按按钮后的提示，也没有你的命令本该产生的回复。每一个都会是一次新的违规，让封禁延长；2026-09-15，正是一次在封禁生效期间发出的请求，把 21 分钟的封禁变成了 9.5 小时。因此，在停止发送期间输入的命令不会得到任何回复，**点按按钮也不会得到确认**：直到 0.7.12，点按后的提示仍会发送，因为当时认为 Telegram 会单独计算它。就发送节奏而言确实如此；就封禁而言却不是。其他聊天不受影响。

**在封禁期间给机器人发消息是安全的，也不会丢失任何东西。** 你在聊天里做的任何事都不会让机器人在聊天停止发送期间调用 Telegram，所以不会延长封禁：

- 你发送的消息（文字、语音消息、文件、快捷模板）仍然送到 Claude，会话照常处理它。它的工作卡片会被暂缓，而不是被丢弃：如果封禁解除时任务仍在运行，卡片会在那时出现。封禁之后，聊天还会在最小模式里待一段时间，所以卡片可能比封禁解除晚几分钟才出现。如果任务在封禁期间就完成了，你会收到它的回答，而没有卡片。
- **🔄 重试** 在封禁期间不做任何事。消息不会被重新发送，按钮也会保留，所以封禁解除后你可以再点一次。直到 0.7.14（含），消息会发给 Claude，而错误消息和它的按钮仍然保留，所以第二次点按会把它再发一次。
- 主键盘也会被暂缓，包括重启时发送的那个（在 0.7.14 及之前，它会一直丢失，直到别的东西刷新它）。它会在封禁解除后发送。

封禁解除后，东西会按这个顺序到达：暂缓的回答，然后是仍在运行的任务的卡片，然后是键盘。需要等最小模式结束的卡片会在键盘之后到达。

**回答不会丢失。** 在聊天停止发送期间产生的回答会被暂缓，并在封禁解除后的几秒内送达，第一行写着 `⏳ 延迟送达：因 Telegram 限制暂缓了 42 分钟`。你不需要再问一次；如果你又问了，两个回答都会到达，旧的在前：一次持续几个小时的封禁会跨越好几个任务，每个任务的回答都是你的。（暂缓的回答保存在内存里：如果你在还有回答等待时重启 aipager，它们就会丢失；关闭时的日志会写明有几个。一个聊天最多暂缓 20 个回答，并且不保留超过 24 小时的；因为任何一个原因而被丢弃的内容，日志里都会有一条警告写明是什么。）

**封禁会让聊天在之后变得更慢，而不是更快。** 封禁一被记录，聊天学到的速率就降到下限，并计入这次封禁；停止发送的那几个小时不会挣回任何速度，回升从封禁解除时重新开始，并且在**七天**内（`FLOOD_BAN_MEMORY_DAYS`），聊天的速率上限*和*每小时额度都要除以一加上那一周内的封禁次数：一次封禁让两者减半，两次封禁让两者变为三分之一。`aipager status` 会显示这一切。（以前这个记忆只有 24 小时，并且只让上限减半；一个在 2026-09-19 被封禁的聊天，在 2026-09-23 又回到了全速，结果被封禁了七个小时。）一周内五次或更多次封禁会让聊天一直处于最小模式，直到它们过期：回答仍然发送，卡片保持暂停。

不要做的事：

- 不要为了“修好”它而重启 aipager。停止发送会在显示的时间自行解除。从 0.7.13 起，重启不再会忘记封禁（截止时间、聊天挣来的速率和封禁历史都保存在 `~/.claude/aipager-flood-state.json` 里，并在启动时恢复，聊天过去一小时的调用和警告期也是如此），所以重启也不再会延长它。但重启会丢掉所有仍在暂缓的回答，而且什么也修不好。
- 不要把 `TELEGRAM_MAX_RETRY_AFTER` 调到默认的 90 秒以下，指望能更早重试：超过这个上限的都是封禁，不是限流。

要避免它：每个聊天里同时运行更少的会话，或者给最忙的会话一个单独的聊天（`aipager config`）。

## <a id="a-session-dropped-off-the-pinned-status-bar-it-is-gone"></a>一个会话从置顶状态栏消失了（它已 GONE）

那个会话的 dtach 进程已经退出（电脑重启、`pkill claude`、有人在连接着的 dtach 会话里输入了 `exit`）。重新创建会话即可：对话仍在它的 `~/.claude/projects/...` 文件夹里。

最简单的办法是在 Telegram 里发送 `/resume <label>`（或者只发 `/resume`，从列表里选）：它会重新启动会话，并接着之前的对话继续。在命令行里：

```sh
aipager resume <label>             # 同样的事，在终端里做
aipager session <label>            # 或者一个全新的会话，没有历史
aipager session <label> --continue # 新的 dtach，接着这个文件夹最近的对话
```

聊天里的通知会根据 `claude` 留下的退出码说明会话为什么结束（`claude` 退出时，aipager 把它记录在 `claude-exit-<session>.json` 里）：

- `🔴 x1 · 会话崩溃或被结束`：没有记录到退出码（电脑重启了，或者会话的进程被一起结束了）。
- `🔴 x1 · 会话已关闭`：`claude` 正常结束了（退出码 0）。
- `🔴 x1 · 会话以错误码 2 退出`：`claude` 以这个代码失败了。
- `🔴 x1 · 会话被信号 SIGKILL 结束`：有东西强行结束了 `claude`（大于 128 的退出码对应信号）。
- `🔴 x1 · Claude Code 刚启动就退出了（退出码 1）`：`claude` 启动后 10 秒内就放弃了，通常是因为它自己的参数或设置。用 `aipager session x1` 连接上去看它的消息，或者在终端里手动启动它。
- `🔴 x1 · Claude Code 刚启动就退出了，因为它的对话正在 Claude Code 后台运行`：Claude Code 拒绝恢复仍被后台任务占着的对话。点 `↩️ 现在带回来`：如果那个任务空闲，aipager 就停止它（如果它在工作，会先询问），然后在这里恢复对话。见下一节。

## <a id="a-session-moved-to-claude-codes-background"></a>会话移到了 Claude Code 后台

`🌙 x1 已转到 Claude Code 后台，你的下一条消息会把它带回这里。`

在 Claude Code 的空输入框里按 ← 会把会话的对话移到一个 Claude Code 后台任务里，终端显示的不再是对话，而是 Claude Code 的代理视图。aipager 会在几秒内发现，并停止向那个终端输入：现在在那里输入的任何东西都可能启动另一个后台任务。这个任务会继续工作，它的卡片、回答和权限请求仍会作为该会话的内容到达聊天。在 Mini App 里，这个会话显示为“在后台”；它的任务在等一个你可以在聊天里回答的权限请求时，显示为“等你回复”。

把它带回来：

- 给它发一条消息。消息会等着（👀），aipager 停止后台任务（`claude stop <job>`），用 aipager 自己的设置在该任务的对话上重启会话，然后发送消息。如果任务还在工作，或者随 ← 移过去的那个任务还在运行，会先问你：`停止并带回来` 停止它正在做的事，`让它继续` 让它做完（之后再把会话带回来）。按 ← 后的头几秒里，新的后台任务在继续那个任务之前可能看起来是空闲的，所以这时发来的消息会先等一会儿，再由 aipager 决定。角色不能重启该会话的人会收到 `你发给 x1 的消息会一直等着，直到有人把这个会话从 Claude Code 后台带回来。`
- 或者点通知下面的 `↩️ 现在带回来`、发送 `/restart x1`，或在 Mini App 里点“带回来”。任务在工作时发送 `/stop` 会停止那项工作，并把会话带回来。之前移到后台时留下的按钮会回复 `x1 不在 Claude Code 后台。`，什么也不做。在任务工作时进行需要重启会话的模式切换（切换到完全访问模式或离开完全访问模式），这时出现的 `停止并带回来` 会以你选择的模式把会话带回来。

会话在后台时，如果某条安全规则让会话的工作停下，aipager 会停止后台任务。如果停不下来，停止通知会说明被拦下的工作可能仍在那里运行，而且它的第一行末尾没有 `（已停止）`；这时请在终端里用 `claude stop <job>` 停止该任务。

在此之前，需要向会话输入的命令（`/compact`、`/model`、切换到询问模式或自动模式、`/now`、重试）会回复 `请先把 x1 从 Claude Code 后台带回来。`，需要 aipager 输入的请求回答会收到 `要回答这个，请先把 x1 从 Claude Code 后台带回来。`通过 hook 的回答（大多数权限请求）仍然有效。

`/resume`、Mini App 里的“恢复”和 `aipager resume` 也会同样地跟随转到别处的对话：恢复的是最新的对话，仍占着它的后台任务如果空闲，会先被停止。`aipager session x1 --resume <id>` 在终端里也是这样；任务在工作时，它会打印 `claude stop <job>` 和 `claude attach <job>`，并以代码 1 退出，不启动任何东西。

## <a id="a-permission-prompt-with-no-news-for-5-minutes"></a>一个权限请求 5 分钟没有消息

如果一个会话在等一个权限请求或问题，而 Claude Code 5 分钟没有任何消息，aipager 会读取这个会话保存的对话。只算 Claude 自己和提出请求的代理的消息：一直在工作的后台代理不会让这项检查无法进行。只有对话显示请求已经结束（在终端里回答了，而这个消息丢了），aipager 才不再等它，并重新把会话算作工作中。否则请求会一直开着，不管多久：卡片和置顶栏会一直显示会话需要你回答，不会发出“仍在工作”的提示，你的消息会被暂存，直到你回答（见[文本](commands.md#text)）。Claude Code 在请求打开时退出，会话就结束了。如果对话框仍然开着，请求上的按钮仍然可以回答它（见[任务结束后的回答](commands.md#idle-responses)）。

要修改等待时间：

```sh
AIPAGER_INTERACTIVE_TIMEOUT=300 aipager start    # 以秒为单位
```

## <a id="voice-extra-wont-install-via-telegram"></a>无法通过 Telegram 安装语音消息功能

`[📦 安装语音消息功能]` 按钮会运行安装程序，并在安装期间每隔几秒更新一次它的消息（`仍在进行中…（25 秒）`）。如果安装失败，消息会显示退出码和安装程序输出的最后 500 个字符。常见原因：

- 网络：pip 连不上 PyPI。请检查运行 aipager 的电脑的网络连接。
- 磁盘：`~/.cache/huggingface/` 的模型需要约 74 MB，pip 的 wheel 缓存还需要约 250 MB。请先腾出空间。
- Permission denied：venv 是只读的。改用 `uv tool install`，它总是使用属于你的用户的 venv。

如果机器人在安装中途断开了连接，电脑上的安装本身通常已经完成：重启 aipager，再试一次语音消息。

## <a id="open-app-shows-open-this-page-from-the-telegram-app-to-sign-in"></a>打开应用时显示“请从 Telegram 应用中打开此页面来登录。”

页面加载了，但 Telegram 的 Mini App SDK 脚本（生成 `initData` 的那个脚本）没有加载，所以页面没有任何东西可以用来登录，也完全不会调用 API。这条消息听起来像是你的错；实际原因出在网络上。

较新版本的 aipager 会自己下载那个脚本，并从页面自己的来源（`/telegram-web-app.js`）提供它，所以手机只需要能连上发送页面的那台主机，而不必同时连上 `telegram.org`。如果你在旧版本上看到这个问题，请升级。

然后，按顺序检查：

1. 查看页面源码。如果它的 `<script>` 标签指向 `https://telegram.org/js/telegram-web-app.js`，说明 aipager 还没能下载到脚本，页面正在退而从 Telegram 加载它，而这正是在连不上 `telegram.org` 的手机上会失败的情况。aipager 在 Mini App 服务器启动时下载脚本，每天最多重试一次，所以重启 aipager 会立即重试；`journalctl --user -u aipager | grep "webapp sdk"` 会告诉你哪里出了问题（通常是 `telegram.org` 被屏蔽或被过滤）。
2. 如果标签指向 `/telegram-web-app.js`，检查 aipager 是否在提供它：在运行 aipager 的电脑上执行 `curl -sI http://127.0.0.1:8765/telegram-web-app.js`（如果你改过端口，请相应修改），应该返回 `200` 和 `Content-Type: application/javascript`。副本保存在 `~/.local/share/aipager/webapp-sdk/` 下。
3. 你是在普通浏览器里打开的网址，而不是通过机器人的 `/app` 按钮或菜单按钮：那么这条消息就是正常的。SDK 只在 Telegram 内部生成 `initData`。
4. 页面开着超过了五分钟：`initData` 过期了，页面要求重新打开。再点一次 `/app`。
5. 你点的是旧版 aipager 键盘上的 `📱 App`。Telegram 不会给从键盘按钮打开的 Mini App 提供 `initData`，所以 aipager 的键盘已经去掉了这个按钮。发送 `/start` 拿到现在的键盘，再从菜单按钮或 `/app` 打开应用。

## <a id="pyexpat-_xml_setalloctrackeractivationthreshold-on-brew-install"></a>用 brew 安装时出现 `pyexpat _XML_SetAllocTrackerActivationThreshold`

Homebrew 的 `python@3.12` 包是针对比你系统里更新的 libexpat 编译的，几乎总是因为 macOS Tahoe（26.x）上的 **Xcode 和 Command Line Tools 已经过时**。brew 自己的输出通常也会这么说。两种修复方法：

- **改用 `uv tool install aipager`**。uv 自带 python，完全绕开了这个问题。这是 macOS 上推荐的方式；见 [README](README.md#install)。
- **更新 Xcode 和 Command Line Tools**：
  ```sh
  sudo rm -rf /Library/Developer/CommandLineTools
  sudo xcode-select --install
  ```
  或者在 App Store 里打开 Xcode，更新到最新版本。

## <a id="modulenotfounderror-aipager"></a>“ModuleNotFoundError: aipager”

你的 aipager 程序指向的 Python 里已经没有安装 aipager 了（通常是在删除 venv 之后）。重新安装：

```sh
uv tool install --reinstall aipager
# 或者你最初用的安装程序：pipx、brew、pip
```

## <a id="the-session-list-was-lost-or-damaged"></a>会话列表丢失或损坏

aipager 把它的会话保存在 `~/.claude/aipager-sessions.json` 里，以原子方式重写（先写一个新文件，再重命名），所以写到一半被中断时，旧文件仍然完好。如果这个文件仍然不存在、无法读取或不是有效的 JSON，aipager 会在日志里记录它将重新开始（如果文件存在但无法读取，则记为警告），并从一个空列表开始；它不会停止。这个文件没有备份。不会丢失任何重要的东西：

- 正在运行的会话会在第一次扫描时通过它们的 dtach 套接字（`/tmp/claude-dtach-*.sock`）重新被找到；
- 每段对话都仍在 Claude Code 自己的 `~/.claude/projects/...` 文件夹里，所以在那个会话的文件夹里运行 `aipager session <name> --continue` 就能接着它（`--resume` 让你自己选）；
- 丢失的是已结束会话的列表（`/resume`）、当时打开着的卡片，以及 aipager 记住的关于每个会话的其他内容。

如果 aipager 在启动时一直失败，`aipager logs`（或 `journalctl --user -u aipager`）会说明原因；把这个文件移到别处（`mv ~/.claude/aipager-sessions.json ~/aipager-sessions.json.old`）来排除它。

## <a id="a-session-launch-fails"></a>会话启动失败

被 dtach 拒绝的启动会在聊天里用一行说明原因（套接字已存在、shell 无法执行、没有伪终端、名称太长、权限被拒绝、文件夹不存在）；dtach 自己的消息（包括路径）在 `aipager logs` 里。

## <a id="aipager-doctor-check-list"></a>`aipager doctor` 检查列表

顺序很重要：后面的每项检查都假定前面的检查已经通过。一项因为意外的环境而崩溃的检查会显示为一行 ⚠，写明是哪项检查和什么错误；其余的检查仍会运行。

| 检查（`--json` 键） | 检查什么 | 修复提示 |
|---|---|---|
| `config_parses` | `aipager.yaml` 和 `policy.yaml` 可以读取；最先运行，因为之后的每项检查都要读取配置 | 错误会指出文件和行 |
| `config` | `~/.config/aipager/aipager.yaml` 存在，并且有 token 和一个聊天 | `aipager config` |
| `token_valid` | token 能通过 Telegram 的 `getMe` | 重新运行 `aipager config` |
| `chat_reachable` | Telegram 认得配置的聊天（`getChat`；不发送任何东西） | 打开机器人，点 Start |
| `team` | 聊天和成员（或旧式的 `team.yaml`）：说明是个人模式还是团队模式，并标出格式错误或不一致的旧 `team.yaml` | `aipager config` |
| `role_shell_access` | 对成员拥有的每个能运行 Bash 的角色警告一次（它的安全规则只是尽力而为，不是边界；见[群组](groups.md#roles)） | 在 `policy.yaml` 里不给那个角色 Bash，或者接受这一点 |
| `peer_messages` | Claude 会话之间的消息：它们的回执保存在哪里、那个文件夹是否私密、这个系统能否核对发送者的启动时间，并对每个被规则允许使用 SendMessage 的角色发出警告（见[安全](security.md#messages-between-claude-sessions)） | 删除共用的回执文件夹（aipager 会重新创建），或者不给那个角色 SendMessage |
| `claude` | 用每次启动时相同的方式找到 `claude` 程序（`aipager.yaml` 里的 `claude_path` 或 `$AIPAGER_CLAUDE_BIN` 直接优先；否则在 `~/.local/bin`、PATH 和 Homebrew 中选最新的），并列出找到的所有其他安装 | 安装 Claude Code，或者设置 `claude_path` / `AIPAGER_CLAUDE_BIN` |
| `claude_auth` | 在真实会话得到的同样环境里试运行 `claude auth status`。**从不 FAIL**：“未登录”和“试运行本身失败”会分别报告，两者都不会阻止会话启动。当 Claude Code 被告知不要使用会话的 `ANTHROPIC_API_KEY`、而又没有别的方式让会话登录时，它也会警告。 | `claude auth login`，或者设置 API 密钥 / `CLAUDE_CODE_OAUTH_TOKEN`；设置了 `ANTHROPIC_BASE_URL` 时，是那个地址的密钥（[服务商](#using-an-api-key-a-gateway-or-another-provider)） |
| `dtach` | 找到了 `dtach` 程序（aipager 自带的 `dtach-bin`，或 PATH 上的） | `uv tool install --reinstall aipager` |
| `hook_scripts` | `aipager-hook` 和 `aipager-statusline` 在 PATH 上 | `uv tool install --reinstall aipager` |
| `settings_json` | `~/.claude/settings.json`（或 `$CLAUDE_CONFIG_DIR/settings.json`） 已接入 aipager 的 hook；设置了 `CLAUDE_CONFIG_DIR` 时会指出该文件以及这个值的来源 | `aipager config` |
| `daemon` | aipager 正在运行，它的套接字有应答；同时显示 Telegram 的限流状态（见[上文](#the-bot-slowed-down-a-rate-limit-429)） | `aipager start` |
| `service_installed` | 可选：服务文件存在 | `aipager service install` |
| `service_unit_path` | 仅 Linux：*已安装的*服务的 `Environment=PATH=` 确实包含找到的 claude 程序所在的文件夹，按文本解析，因为 doctor 从机器人主人的交互式 shell 里看不到 systemd 自己的 PATH | `aipager service install --yes` |
| `miniapp` | Mini App 开启时能否启动。**从不 FAIL**：Mini App 是可选的 | 它给出的修复方法 |

`aipager doctor --json` 以同样的顺序、用这些键打印同样的检查（见[命令](commands.md#aipager-doctor---json)）。

运行 `aipager doctor --fix`，可以交互式地查找 Claude 的登录信息并复制到 `daemon.env`（文件存在但还没有登录信息时也可以），或者在找到多个安装时固定 `claude_path`。它只会在询问之后才行动。对于 PATH 缺少 claude 所在文件夹的服务，运行 `aipager service install --yes`。

## <a id="the-daemon-cant-find-claude-or-picks-the-wrong-install"></a>aipager 找不到 `claude`，或者选错了安装

以前有六个地方各自查找 `claude`，结果可能互相矛盾。现在它们都经过同一个查找器（`aipager/claude_resolve.py`）。`aipager.yaml` 里的 `claude_path`，然后是 `$AIPAGER_CLAUDE_BIN`，只要可用就直接优先。否则，会检查在 `~/.local/bin`、`$PATH` 上和 Homebrew 文件夹里找到的每个 `claude`，版本最新的胜出（所以当另一个安装更新时，调整 `$PATH` 的顺序不会改变选择）。运行 `aipager doctor` 可以看到它确切地选了哪一个，以及还找到了什么；如果选错了，就固定正确的那个：

```sh
aipager doctor --fix        # 在找到的安装之间交互式地选择
# 或者手动：
echo 'claude_path: /home/you/.local/bin/claude' >> ~/.config/aipager/aipager.yaml
```

## <a id="sessions-cant-authenticate-under-the-systemd-service-but-claude-works-fine-in-a-terminal"></a>在 systemd 服务下会话无法登录，但 `claude` 在终端里运行正常

`systemctl --user` 服务从不读取 `~/.bashrc` / `~/.profile`，所以那里的 `export CLAUDE_CODE_OAUTH_TOKEN=…` 永远到不了 aipager。服务改用 systemd 的 `LoadCredential=`，读取 `~/.config/aipager/daemon.env`：一个简单的 `KEY=VALUE` 文件，权限 0600，在你第一次运行 `aipager service install` 时自动创建（如果旧式的 `config.env` 里有 token，就从那里复制过来；否则从你的登录 shell 里查找一次；再找不到就留空并发出警告）。如果它仍然是空的，`aipager doctor --fix` 会在你同意后再到同样的地方查找，并添加找到的那一行，保留文件里的其他行；它从不打印 token。或者自己添加那一行：

```sh
cat ~/.config/aipager/daemon.env          # 看看里面有什么（或者什么都没有）
echo 'CLAUDE_CODE_OAUTH_TOKEN=sk-...' >> ~/.config/aipager/daemon.env
chmod 600 ~/.config/aipager/daemon.env
aipager service stop && aipager service start
```

这个文件能防护什么、不能防护什么，见[安全模型：Claude 的登录信息](security.md#the-claude-credential-what-actually-protects-it)。

## <a id="using-an-api-key-a-gateway-or-another-provider"></a>使用 API 密钥、网关或其他服务商

aipager 按你的设置运行 Claude Code，所以从 Telegram 启动的会话和你终端里的 `claude` 用同样的方式登录：claude.ai 登录、Anthropic 的 API 密钥、网关（LiteLLM、Vercel、Cloudflare 等），或写在 `ANTHROPIC_BASE_URL` 里的其他公司的 Anthropic 格式地址。Anthropic 只支持 Claude Code 搭配 Claude 模型使用；其他模型的表现可能不同。

- **设置放在哪里。**放进 `~/.claude/settings.json`（或 `$CLAUDE_CONFIG_DIR/settings.json`） 的 `env` 块（每个 Claude Code 会话都会读取），或放进 `~/.config/aipager/daemon.env`（只有 aipager 启动的会话会拿到）：每行一个 `KEY=value`，不加引号（引号会变成值的一部分）。服务（`aipager service install`）从不读取你 shell 的配置文件，所以写在那里的 `export` 到不了服务。改完 `daemon.env` 后重启 aipager。受信任的启动文件夹中 `.claude/settings.json` 和 `.claude/settings.local.json` 的 `env` 块，以及托管设置（Linux 上 `/etc/claude-code`、macOS 上 `/Library/Application Support/ClaudeCode` 中的 `managed-settings.json` 和 `managed-settings.d/`）的 `env` 块也同样算数，和 Claude Code 一样。
- **用哪个密钥设置。**对网关或其他服务商，Claude Code 的文档建议用 `ANTHROPIC_AUTH_TOKEN`，Claude Code 从不为它提问。对 `ANTHROPIC_API_KEY`，它会问一次“Do you want to use this API key?”，而从 Telegram 启动的会话无法回答，所以 aipager 会在会话启动前一刻在 `~/.claude.json`（或 `CLAUDE_CONFIG_DIR` 把它放进的文件） 里替你回答：
  - 是：密钥写在 `settings.json` 或 `daemon.env` 里，或者没有别的方式能让会话登录。
  - 否：密钥只来自启动 aipager 的环境，而你已经用别的方式登录了 Claude；你的会话继续用那个登录和它的套餐。要用这个密钥，在带有这个密钥的终端会话里用 `/config` 的“Use custom API key”打开它。
  - 你自己给过的回答会保留，只有一种例外：没有别的方式能让会话登录时，对写在 `settings.json` 或 `daemon.env` 里的密钥的“否”。当“否”让会话无法登录时，`aipager doctor` 会提示；在终端里用 `/config` 的“Use custom API key”重新打开这个密钥。
- **首次运行的界面。**在 Claude Code 从没完成首次运行的地方（新机器或新容器），只要会话有登录凭据，aipager 就把首次运行标记为已完成，会话就不会停在主题选择界面。如果完全没有凭据，就在终端里运行一次 `claude` 并登录。
- **模型。**Opus、Sonnet 和 Haiku 按钮发送 Claude Code 的 `opus`、`sonnet` 和 `haiku`；设置了 `ANTHROPIC_DEFAULT_OPUS_MODEL`、`ANTHROPIC_DEFAULT_SONNET_MODEL` 和 `ANTHROPIC_DEFAULT_HAIKU_MODEL` 后，Claude Code 会把它们对应到你服务商的模型。`claude-...` 按钮需要服务商有这些模型。其他模型在 `/new` 的模型步骤或 Mini App 里按服务商的写法输入：最多 128 个字符，由字母、数字和 `. _ - : / @ ~ ,` 组成，可以带 Claude Code 的 `[1m]` 后缀。`keyboard.json` 可以改按钮（见[固定键盘](commands.md#persistent-keyboard)）。
- **没有密钥的 `ANTHROPIC_BASE_URL`。**这时 Claude Code 会把你的 claude.ai 登录发到那个地址。除非那个地址是专门把你的登录转给 Anthropic 的网关，否则也要设置服务商的密钥。
- **不支持：**通过本地代理、用另一家公司的聊天订阅（比如 ChatGPT 套餐）运行 Claude Code 的工具。那家公司没有批准 Claude Code 用于它的套餐，这样用可能让那个账号面临风险。

**Claude Code 的配置在另一个文件夹（`CLAUDE_CONFIG_DIR`）。** Claude Code 把设置、登录、对话记录和全局配置放在 `~/.claude` 和 `~/.claude.json` 中，或放在 `CLAUDE_CONFIG_DIR` 指定的文件夹中。aipager 遵循同一个变量，所以它的钩子和状态行会写进你的会话读取的 `settings.json`，它的应答会写进 Claude Code 读取的文件（已用 Claude Code 2.1.303 验证）：

- **在 `~/.config/aipager/daemon.env` 中设置它**，然后重启 aipager。服务从不读取你的 shell 配置文件，所以 `export` 只能影响那个 shell 中的 `aipager start`。启动时，守护进程会把钩子写入 `$CLAUDE_CONFIG_DIR/settings.json`，并在日志中记录 `sessions read Claude Code's config from ...`；如果在它运行时修改这个值，它启动的下一个会话会得到钩子，此后丢失了钩子的设置文件也一样。钩子无法写入时，日志会显示 `sessions will start without aipager's hooks`，`aipager doctor` 也会发出警告。
- **使用完整路径。** Claude Code 会原样使用这个值：不会展开 `~`，相对路径（空值也一样）表示每个会话自己文件夹里的一个文件夹。aipager 不会往那里写任何东西：日志和 `aipager doctor` 会警告，`aipager setup` 会停下并给出修复方法，`aipager config` 会跳过它的 Claude Code 步骤，显示带修复方法的警告后继续。修复方法会指出存放这个值的文件：`daemon.env`，或在 `env` 块中设置它的设置文件。
- **在 Claude Code 自己的设置中设置。** `~/.claude/settings.json`（或托管设置）的 `env` 块中的 `CLAUDE_CONFIG_DIR` 会移动设置文件、登录和对话记录，但不会移动 `~/.claude.json`。aipager 也会跟着这样做；`aipager doctor` 会告诉你位置。
- **`aipager config` 和 `aipager setup`** 修改的是守护进程的会话读取的文件：你的 shell 环境加上 `daemon.env`。在另一个值的 shell 中启动的 `claude` 会读取另一个文件，没有 aipager 的钩子；`aipager doctor` 会指出它。
- **旧版 `.config.json`。** 当 `<config folder>/.config.json` 正在使用时（例如它存在且不为空），Claude Code 会读取它而不是 `.claude.json`，aipager 也一样。`CLAUDE_CONFIG_DIR` 为空时，这个文件会在会话自己的文件夹中，aipager 不会往那里写任何东西。
- **aipager 读不到的内容。** 通过 MDM 配置描述文件（macOS）、Windows 注册表或 Claude 服务器下发的托管设置，以及 `--settings` 参数。只在那里设置的密钥或服务商开关，aipager 回答启动问题时看不到；这时 Claude Code 会询问，就像没有 aipager 时一样。
- **拒绝列表。** 让受限角色和发送的文件远离 Claude 凭据的默认规则写的是 `~/.claude/**` 和 `~/.claude.json*`。放在别处的 `CLAUDE_CONFIG_DIR` 不在它们的范围内，所以当非管理员使用你的会话时，请把它放在 `~/.claude` 里面（Docker 镜像就是这样）（[安全](security.md#who-decides-whether-a-tool-call-runs)）。

## <a id="the-daemons-control-socket-moved"></a>aipager 的控制套接字换了位置

`aipager doctor` / `aipager status` 以前总是在 `/tmp/aipager.sock` 查找。在 systemd 服务下，控制套接字现在位于 `$XDG_RUNTIME_DIR/aipager.sock`（只有在没有设置 `$XDG_RUNTIME_DIR` 时才退回到 `/tmp`：容器、WSL1、精简的发行版）。只有这一个套接字换了位置；每个会话的 dtach 套接字（`/tmp/claude-dtach-*.sock`）不受影响。如果你需要指定位置，设置 `AIPAGER_SOCKET_PATH`。

## <a id="update-says-the-restart-would-kill-sessions-killmode"></a>`/update` 说重启会关闭会话（KillMode）

在这个版本之前写入的服务文件没有 `KillMode=` 这一行，所以 systemd 使用 `control-group`：停止或重启 `aipager.service` 会关闭 aipager 启动的每个 Claude 会话。因此 `/update` 会安装新版本，但不会重启 aipager，并列出重启会关闭的会话。修复一次即可：

```
aipager service install
```

它会用 `KillMode=process` 重写服务文件，重新加载 systemd 并重启 aipager，而这次重启已经在新设置下运行。用 `systemctl --user show -p KillMode aipager.service` 检查（`KillMode=process`）。有了这个设置，systemd 每次重启时都会为你的会话记录“left-over process”行；这是正常的。

## <a id="aipager-update-cant-find-uv--pipx"></a>`aipager update` 找不到 uv / pipx

`aipager update` 会按绝对路径在你的 `PATH` 里查找安装程序，另外还会查找 `~/.local/bin`、`~/.cargo/bin`、Homebrew 的文件夹、`/usr/bin` 和 `/bin`。如果它仍然说找不到 `pipx`，说明创建这个 aipager 的安装程序已经不在了，或者在别的位置；用你现有的工具重新安装（`pipx install --force aipager`、`uv tool install --reinstall aipager`）。

## <a id="update-refused-editable--not-your-install"></a>更新被拒绝：editable 安装 / 不是你的安装

`/update` 和 `aipager update` 只升级属于你的用户、并且可写的安装，并且通过创建它的那个安装程序来升级。对于以下情况，它们会拒绝并说明原因：

- editable 模式的源码副本（用于开发）：用 `git pull` 更新它；
- 通过 Nix、Snap、Docker 或系统软件包安装的：使用那个系统自己的更新方式；
- 属于另一个用户的安装（例如 `/opt` 下属于 root 的 venv）：以那个用户的身份更新它；
- 从本地文件夹安装的（`pipx install /path/to/aipager`）：`无法从这里更新：it was installed from a local folder; update it from that folder`。把新版本拉取到那个文件夹，再从那里重新安装，或者用 `pipx install --force aipager` 改为从 PyPI 安装；
- 无法判断由哪个安装程序安装的（`could not tell which installer owns this aipager`）：用 `uv tool install aipager` 或 `pipx install aipager` 重新安装。

## <a id="update-failed-unexpected-argument---refresh"></a>更新失败：unexpected argument '--refresh'

aipager 1.0.0 及更早的版本会运行 `uv tool upgrade aipager --refresh`，而没有任何 uv 版本接受这个参数，所以每次更新用 uv 安装的 aipager 都会以 `error: unexpected argument '--refresh' found` 停止，并且什么都不会重启。较新的版本运行 `uv tool upgrade aipager`。要一次性越过这个问题，在那台电脑的终端里更新（这些版本的 `aipager update` 也有同样的问题）：

```
uv tool upgrade aipager
systemctl --user restart aipager.service
```

没有服务时（或者在 macOS 上），按你启动 aipager 的方式重启它。

## <a id="claude-code-update-installed-with-npm-into-a-folder-you-cant-write-to"></a>Claude Code 更新：用 npm 安装到了你无法写入的文件夹

用 `sudo npm install -g` 安装的 Claude Code 位于只有 root 能修改的文件夹里（例如 `/usr/lib/node_modules`），所以以你的用户身份运行 `claude update` 会以 `global folder isn't writable` 失败。`/update` 和 Mini App 会显示较新的版本，并附上 `无法从这里更新：installed with npm into a folder your user can't write to`，没有对应的按钮。以运行 aipager 的那个用户身份运行一次 `claude install`：它会改用 Claude Code 自己的安装程序，安装在你的主文件夹下，这样无需 sudo 就能更新。然后重启 aipager（`systemctl --user restart aipager.service`，或者按你启动它的方式）：正在运行的 aipager 会一直使用它启动时找到的 Claude Code，重启后它会使用找到的最新版本。如果 `aipager.yaml` 里的 `claude_path`（`aipager doctor --fix` 可以设置它）或 `AIPAGER_CLAUDE_BIN` 指向 npm 的那个，请先把它改为指向 `~/.local/bin/claude` 或删除它，因为它们总是优先。

## <a id="updated-but-the-version-didnt-change"></a>更新了，但版本没有变

从 git 网址安装的 pipx 会从那个 git 源升级，而不是从 PyPI。当源里没有更新的版本时，`/update` 会说 `✅ aipager 已是 A：这个 pipx 安装从它的 git 源更新，那里没有更新的版本。`。在那里推送或选择新版本，或者用 `pipx install --force aipager` 改为从 PyPI 安装。

**没有服务时**，一个不由任何服务管理器重启的 aipager（在终端或 tmux 里运行 `aipager start`、Docker、launchd）会安装更新，但在重启之前仍运行旧代码。这时 `/update` 会说 `aipager B 已安装，正在等待重启（当前运行 A）。`，而 Claude Code 的 hook 每个事件都会重新启动，可能已经在运行 B：所有者会收到一条更新只完成了一半的通知（在 hook 第一次运行 B 两分钟之后，所以安装后立刻重启的话永远不会出现；并且要等所有者的私聊里没有任务在运行）。aipager 运行 B 之后，这条通知会变成 `✅ aipager 更新已完成：aipager 现在运行的是 B。`，不再带按钮。点 **🔄 立即重启**（在更新消息里、通知里，或 Mini App 的 **设置 → 更新** 里）。aipager 会等到没有任务在运行，干净地停下，然后原地启动新版本：同一个终端、tmux 窗口或容器里的同一个进程，用同样的命令和环境。

如果原地启动失败，旧进程退出后会由一个分离的副本启动 aipager。这时它的输出会写入自重启日志（实例文件夹里的 `aipager.log`，或 `/tmp/aipager.log`），而不是你的终端；如果这个副本没有回来，aipager 的日志会写出要运行的命令。如果解释器本身已经没了（它的环境被删除了），或者什么都启动不了，aipager 会以代码 1 退出，aipager 的日志以 `restart.failed:` 和要运行的命令结尾，下一次启动会宣布这次更新。

没有 **立即重启** 按钮时，消息会给出适合你的环境的步骤：在终端或 tmux 里，按 Ctrl+C 再运行 `aipager start`；在 macOS 上，运行 `launchctl kickstart -k gui/<uid>/com.aipager.daemon`；在 Docker 里，在主机上运行 `docker restart <container>`。重新创建 Docker 容器（在 `down` 之后运行 `docker compose up`，或再次运行 `docker run`）会从它的镜像启动，这会带回该镜像里的 aipager 版本：要保留更新的 aipager，请使用更新的镜像。

镜像设置了 `CLAUDE_CONFIG_DIR=/home/aipager/.claude`，因此 Claude Code 的全局配置（`.claude.json`：文件夹信任、首次运行状态、API 密钥的应答、你的账号和用户级 MCP 服务器）会和其他内容一起保存在 `.claude` 卷中，重建容器后依然保留。之前的镜像把它放在容器内部的 `/home/aipager/.claude.json`。新镜像第一次启动时不会读取旧文件：卷中 `.credentials.json` 里的登录仍然有效，会话启动时 aipager 会重新回答文件夹信任和首次运行的问题，但用 `claude mcp add --scope user` 添加的用户级 MCP 服务器需要重新添加。如果你绑定挂载主机的 `~/.claude`（就像 README 中的命令那样），这个文件会以 `~/.claude/.claude.json` 出现在那里；主机上的 Claude Code 不会读取它（它使用 `~/.claude.json`）。使用命名卷可以把两者分开。

## <a id="the-daemon-didnt-come-back-after-an-update"></a>更新后 aipager 没有重新运行

升级会在重启任何东西之前检查新版本能否导入，但如果新的 aipager 仍然无法启动，systemd 会每 5 秒重试一次。查看原因：

```
journalctl --user -u aipager.service -n 50
```

用同一个安装程序重新安装之前的版本，然后重启：

```
pipx install --force aipager==<previous>        # 或者：
uv tool install --force aipager==<previous>     # 或者：
<venv>/bin/python -m pip install aipager==<previous>
systemctl --user restart aipager.service
```

从本地路径安装的 pipx：在旧的 commit 上运行 `pipx install --force /path/to/aipager`。如果还有一条过时的通知在等待，用 `rm -f ~/.local/share/aipager/update-restart.json` 删除它（反正 24 小时后它会被忽略）；`~/.local/share/aipager/update.lock` 只是一个锁文件，在没有更新运行时可以放心删除。

## <a id="an-update-was-interrupted-when-aipager-shut-down"></a>“aipager 关闭时中断了一次更新”

在 `/update` 正在安装时 aipager 停止了（或被重启了），安装程序也随之停止，所以安装可能只写了一半。如果 aipager 或 Claude Code 运行不正常，修复它：对于 aipager，运行消息里的重新安装命令（例如 `pipx install --force aipager`），然后运行 `systemctl --user restart aipager.service`；对于 Claude Code，再运行一次 `claude update`。

没能再次启动 aipager 的立即重启（没有服务时）会在 aipager 的日志里留下 `restart.failed:` 和要运行的命令；运行它，新的 aipager 就会宣布这次更新。

## <a id="a-chat-is-in-the-wrong-language"></a>聊天的语言不对

aipager 的语言来自为这个聊天选择的语言，而不是聊天里的消息。在那个聊天里打开 `/language`，选择你想要的语言：这个选择优先于其他一切。在私聊里，**自动**会跟随你的 Telegram 应用的语言（Telegram 里的“设置 → 语言”）；aipager 没有的语言会显示为英文。在加入多语言之前就已存在的私聊会一直使用英文，直到你选择一种语言。群组会一直使用英文，直到管理员为它选择语言。语言只改变 aipager 自己的文字：Claude 用你写给它的语言回答，无论 aipager 显示的是什么语言。见[语言](commands.md#languages)。

## <a id="the-bot-description-is-not-the-one-i-wrote"></a>机器人的简介不是我写的那个

aipager 会用它支持的每种语言设置机器人的简介和短简介，给 Telegram 应用使用那种语言的人看，Telegram 会向他们显示这些文字，而不是你在 BotFather 里写的那些。aipager 只在你还没有自己的简介时这样做，所以你在安装这个版本之前写的简介会被所有人看到。如果你是在 aipager 设置了它的简介之后才写的，使用那些语言的人仍会看到 aipager 的简介。要让他们看到你的，通过 Bot API 清除 aipager 为每种语言设置的文字（用空文字和那个 `language_code` 调用 `setMyDescription` 和 `setMyShortDescription`）；只要你的简介还在，aipager 就绝不会再设置它。

## <a id="aipager-says-languagesjson-could-not-be-used"></a>aipager 说无法使用 languages.json

aipager 把它了解到的关于语言的信息（谁在加入多语言之前就在这里、它最近在每个人身上看到的 Telegram 语言，以及一次性的建议和第一次接触时的那一行是否已经发出）保存在 `~/.local/state/aipager/languages.json` 里。如果这个文件无法读取，aipager 会把它移到同一个文件夹里的 `languages.json.bad-<time>`，在日志里写一条警告（在你做出决定之前，每次启动都会再写一次），并让每个聊天都保持英文、不提出切换语言的建议，直到你做出决定：如果你能修好旧文件，就把它放回去；或者删除 `languages.json` 并重启，aipager 会再次把它认识的每个人都算作之前就在的人（在他们自己选择之前使用英文）。任何人已经选择的语言不在这个文件里，绝不会丢失。

## <a id="right-to-left-text-looks-wrong"></a>从右到左的文字看起来不对

在波斯语和阿拉伯语里，aipager 会在自己的文字里加入看不见的方向标记，让每一行从右往左读，并让会话名称、命令和代码保持从左到右。如果某一行看起来仍然是乱的，最常见的原因是 Telegram 客户端绘制混合方向文字的方式不同：在报告之前，先在另一个客户端（手机与桌面版或网页版）里看看。Claude 自己的回答会按 Claude 写的样子显示，不添加任何标记。

## <a id="a-translated-keyboard-word-does-nothing-in-another-chat"></a>翻译后的键盘词在另一个聊天里不起作用

每个聊天的回复键盘使用那个聊天的语言。主键盘上的词（状态、停止、新建、结束和其他词）在每个聊天里、用每种语言都能被理解，而英文词总是有效的。模板按钮或命令按钮上的词则不同：在设置了另一种语言的聊天里输入它，它就是发给 Claude 的消息，而不是按钮，所以中文的“清空对话”绝不会在波斯语或英文的聊天里运行 `/clear`。请使用按钮本身，或者英文命令（`/clear`）。如果聊天的语言改变后键盘看起来是旧的，发送 `/start` 获取当前的键盘。一个同时也是你某个会话名称的词，会与那个会话对话。`/mode` 总是把 `ask`、`auto` 和 `full` 当作模式，只有在有一个正在运行的会话恰好叫那个名字时，才把翻译后的模式词当作会话名称。

## <a id="still-stuck"></a>还是没解决？

发送一份问题报告：在你和机器人的私聊里，点按 `/help` 下的**报告问题**，或者在终端里运行 `aipager report`。在发送任何东西之前你都能看到完整的报告，它不包含任何聊天、消息、名称、路径或 token（见[问题报告](problem-reports.md)）。发送后，你会得到一个参考编号，例如 `ap1-7c41d09e2b3a`。

之后，如果你想讨论这个问题，在 [github.com/dev-aly3n/aipager/issues](https://github.com/dev-aly3n/aipager/issues) 提一个 issue，并附上那个参考编号。请不要把 `aipager doctor` 或 `aipager logs` 的输出粘贴到 issue 里：其中有你的聊天 ID、你主文件夹里的路径、会话名称以及你消息的开头。如果需要某一行日志，aipager 开发者会只要那一行，而你可以先删掉其中任何私人内容。

## <a id="see-also"></a>另见

- [架构](architecture.md)：每个部分做什么。
- [安全模型](security.md)：aipager 会接触什么、不会接触什么。
