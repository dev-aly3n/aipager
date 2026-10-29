# Hook 事件

<!-- i18n-source: docs/hooks.md @ 92c37755 -->

> 本页译自英文；如果两者不一致，以英文版为准。（[English](../../hooks.md)）

Claude Code 会在一个任务的固定时刻运行 hook 命令：提交了一条消息、某个工具即将运行、某个工具运行完毕、任务结束，等等。aipager 在每一个时刻都挂上 hook，并把它们变成 Telegram 里的动态：工作卡片、权限请求、回答、通知和审计日志。

## <a id="how-it-is-wired"></a>如何接入

`aipager config`（或 `aipager setup`）把 aipager 的条目加到 `~/.claude/settings.json`（或 `$CLAUDE_CONFIG_DIR/settings.json`，即守护进程的会话读取的文件），aipager 每次启动时都会检查它们：

- 下面 16 个事件各有一条 `aipager-hook` 命令，工具事件（`PreToolUse`、`PostToolUse`、`PermissionRequest`）上的匹配器是 `*`；
- `PreModelSwitch` 有 5 秒超时；通过 `aipager config` 和 `aipager setup` 设置时，`PermissionRequest` 有 30 秒超时（hook 本身最多等 20 秒，见下文）；其他条目使用 Claude Code 的默认值；
- `statusLine` 命令，即 `aipager-statusline`。

`aipager uninstall` 会再把这些条目移除（见[命令](commands.md#command-line-aipager-uninstall)）。

Claude Code 为每个事件启动一个新的 `aipager-hook` 进程（`aipager.dtach.notify_hook:main`）。它从 stdin 读取事件的 JSON，加上两个字段（见下文），然后通过 Unix 套接字以一个 datagram 发给 aipager。套接字按以下顺序选取：隔离测试实例的 `$AIPAGER_INSTANCE_DIR/aipager.sock`、`$AIPAGER_SOCKET_PATH`、`$XDG_RUNTIME_DIR/aipager.sock`，或 `/tmp/aipager.sock`。如果没有 aipager 在监听，事件就被丢掉，会话监视器会从会话的文件里补上。只有当 aipager 积压了一长串未读事件时，发送才会等待（在 Linux 上）。

一个 datagram 装不下的大事件（超过 240 KiB，或超过系统自己的上限：Linux 上约 208 KiB，macOS 上默认 2 KiB）改用文件传递。hook 把它写进套接字旁边的 `aipager-spill-<uid>` 文件夹，只有自己的用户能访问，然后发一个写明文件名的短 datagram；aipager 立刻读取并删除这个文件，并按同样的顺序处理该事件，就像它是完整到达的一样。超过 4 MiB 的事件仍会被丢掉。这个文件夹上的检查见 [安全](security.md)。

大多数事件让 Claude Code 花大约 20 到 25 毫秒启动进程。有几个还会加载 aipager 的规则代码：工具调用之前（安全检查，见 [PreToolUse](#pretooluse)）约 70 毫秒，任务结束时（清除任务的打开标记）约 55 毫秒，以及 `UserPromptSubmit`（写入任务的规则）。`PermissionRequest` 会故意等你点按，最多 20 秒；aipager 往会话里输入消息时，工具调用也会等待（最多 5 秒，见 [PreToolUse](#pretooluse)）。

hook 把自己的内存限制在 1 GB。触到这个上限的 hook 会以错误退出（对 `PreToolUse` 来说，是 Claude Code 当作拒绝的退出码），并给 aipager 发一个 `hook_memory_cap_hit` datagram，这会变成聊天里的一条通知，并为安装的所有者带一个“🐞 报告这个问题”按钮（见[问题报告](problem-reports.md)）。在会话的环境里设置 `AIPAGER_DEBUG=1`，hook 和状态行就会把诊断信息打印到 stderr。

## <a id="what-the-daemon-receives"></a>aipager 收到什么

这个 datagram 是 Claude Code 自己的 hook JSON，再加上 aipager 的字段（有几个事件带有更多字段，在下面各事件里说明）：

| 字段 | 含义 |
|---|---|
| `hook_event_name` | 事件（Claude Code 的字段；aipager 按它分派）。 |
| `session_id`、`transcript_path`、`cwd`、... | Claude Code 自己的字段，原样保留。`session_id` 是 Claude Code 的对话 ID。 |
| `session` | 由 aipager 添加：它给会话起的名字 `claude-<name>`，来自 aipager 启动会话时设置的变量 `CLAUDE_DTACH_SESSION`。不是 aipager 启动的 Claude Code 会话发来的事件不带名字，aipager 会忽略它们。 |
| `sl_tokens` | 由 aipager 添加，在除 `MessageDisplay` 之外的每个事件上，当该会话的状态行文件存在时：`context_pct`、`total_output`、`total_input`、`current_output`、`lines_added` 和 `lines_removed`。 |
| `claude_pid` | 由 aipager 添加在会话自己的事件上：它的 `claude` 的 PID（Claude Code 的 `CLAUDE_PID`），aipager 用它在 Claude Code 的会话列表里找到这个 claude。 |
| `bg_job` | 由 aipager 添加在算作该会话的 Claude Code 后台任务的每个事件上（见下文）：该任务的 ID。 |

工具事件还带有 `tool_name` 和 `tool_input`。在子代理（subagent）内部触发的每个事件都带有 `agent_id`（子代理事件还带有 `agent_type`）；主循环自己的事件不带，aipager 借此把代理的工具调用放到该代理自己的那一行上。

Claude Code 的后台任务也会运行它们的 hook。在空的输入框里按 ← 会把会话的对话移到一个后台任务里（Claude Code 的代理视图），Claude Code 还会预先备好几个空闲的后台进程，留给下一个任务。它们的 hook 运行时设置了 `CLAUDE_JOB_DIR`，并且继承了启动 Claude Code 后台服务的那个 claude（不管是哪个）的会话名，所以这个名字说明不了它们的任何事。因此 hook 只把后台任务算给自己的 claude 把对话移进这个任务的那个会话：它在任务的链接文件里找这个会话（`claude-bgjob-<job>.json`，aipager 看到移动时写入，见 [架构](architecture.md#file-and-socket-layout)），找不到时就在 Claude Code 的会话列表里，结合 aipager 的 PID 列表来找。这样的任务的事件会作为那个会话的事件发给 aipager，并带上 `bg_job`，所以它的回答、卡片和请求都会到达该会话的聊天。其他任何后台进程都是外来的：它的 hook 不写任何东西，不发送任何东西，也不打印任何东西，只是 `PreToolUse` 仍会按它继承来的会话规则检查它的工具调用，并打印拒绝（它允许的绝不会比那个会话更多）。aipager 会丢弃无法对应到某个在后台的会话的 `bg_job` 事件，也不会为它创建任何东西。

## <a id="event-reference"></a>事件参考

aipager 的处理程序在 `aipager/dtach/hook_receiver.py`；hook 自己的工作在 `aipager/dtach/notify_hook.py`。

### <a id="sessionstart"></a>`SessionStart`

会话启动、被恢复或被压缩了。aipager 还没跟踪的会话会被登记。压缩之后，卡片显示 `📦 name · 对话已压缩（压缩前 82%）`（见 [PreCompact](#precompact--postcompact)）。对于新的对话，hook 还会清除上一个任务的打开标记。后台任务的第一个 `SessionStart`（来源 `fork`）只告诉 aipager 该任务的对话 ID；它不会结束随之移过去的那个未结束的任务。从 Claude Code 后台带回来的会话的第一个 `SessionStart` 会发出这期间暂缓的消息。

### <a id="sessionend"></a>`SessionEnd`

会话结束了。它被标记为 GONE：离开置顶状态栏，移到已结束的会话里，`/resume` 可以把它带回来。`aipager session <name>` 或 `/new <name>` 会启动一个新的会话。后台任务的 `SessionEnd`（任务被停止了）绝不会结束会话：它只会像 `StopFailure` 那样，结束该任务留下的未结束任务。

### <a id="userpromptsubmit"></a>`UserPromptSubmit`

Claude 取走了一条消息，可能是在终端里输入的，也可能是从 Telegram 发来的。

hook 先把这条消息与会话的权限备注（`/tmp/claude-notes-<session>/`，aipager 输入的每条 Telegram 消息一条）比对，并把任务的规则写到 `/tmp/claude-policy-<session>.json`，供 [PreToolUse](#pretooluse) 检查读取（见[安全 → 团队模式下的规则执行](security.md#team-mode-enforcement)）。在转发事件之前，它会告诉 aipager Claude 取走了哪些消息（一个 `queue_pickup` datagram）。然后在有内容时为 Claude 打印额外的上下文：在 `/settings` 里选择的回答风格，以及在回复一条较早的消息时，那条消息说了什么、是什么时候发的。`<task-notification>` 消息（后台代理或后台命令完成时 Claude 唤醒自己）是同一份工作的继续：它不与任何备注匹配，并保留任务的规则。

aipager 把会话标记为工作中，如果还没有工作卡片就打开一张（从 Telegram 发来的消息已经有一张了），并把 Claude 取走的消息上的 👀 变成 👍。任务运行期间发送的消息，会在 Claude 把它并入该任务或用它开始下一个任务时得到 👍。

### <a id="pretooluse"></a>`PreToolUse`

某个工具即将运行。aipager 自己的安全检查就在这里、在 hook 里运行，早于 Claude Code 查看它的权限规则：

- 从 Telegram 开始的任务要遵守发送者角色的规则和安全底线（受保护的路径、命令模式），只有 `owner` 角色可以绕过；aipager 无法归属的任务会得到安全底线；你在终端里输入的消息不受限制。规则见[群组](groups.md#how-rules-work)和[安全](security.md#team-mode-enforcement)。
- 当某条规则拒绝了这次调用时，hook 打印 `deny` 决定，Claude Code 不经询问就拒绝该工具，并通知 aipager（`safety_blocked`）：任务以 `🛑 x1 · 被安全规则阻止：...（已停止）` 停止，该任务之后的每一次工具调用也都会被拒绝。
- `SendUserFile` 调用（Claude 往聊天里发文件）对每个任务都会检查，owner 的任务和在终端输入的消息也包括在内：发送底线、文件检查和任务自己的规则（[安全](security.md#files-claude-sends)）。被拒绝的调用会得到一个不带 "aipager safety policy" 标记的 `deny`：Claude 读到原因并告诉你，任务继续，钩子会把列出每个文件的 `send_files_refused` 发给 aipager，用于审计日志。
- 检查本身出错时会拒绝，除非会话的规则能读取并授予所有者绕过权限。
- 最后，对于放行的调用，hook 会在 aipager 套接字旁边的一个文件夹里留下一个短期标记；如果 aipager 正在往这个会话里输入消息，它会等输入结束（最多 5 秒）。这个 hook 一返回，Claude Code 就会显示权限请求，这时输入的按键会回答这个请求：消息末尾的 Enter 会选中“Yes”。aipager 这边也会等开始输入前刚放行的调用结束；仍然碰上请求的消息不会被输入：它会等待（👀），请求有了回答后再交给 Claude。

在 aipager 这一边，这次调用会成为工作卡片上的一行（子代理的调用会放到该代理的那一行上），当 `/settings` → 改动预览（diff）打开时（默认关闭），`Write` 或 `Edit` 会显示改动预览。`AskUserQuestion` 调用会变成问题，每个选项一个按钮；在没有任务运行时由 Claude Code 的辅助代理提出的问题不会显示（那不是 Claude 在问你）。Claude 自己的 `Agent` 调用会为它启动的代理留下 `subagent_type` 和 `description`，而子代理的调用就是该代理最新的动态：在代理于后台运行期间，两者都会显示在回答下方的状态里。

### <a id="permissionrequest"></a>`PermissionRequest`

Claude Code 即将请求权限。工作卡片变成这个请求，在 Claude 对它的描述下方显示真实的命令或路径（见[命令 → 权限请求](commands.md#permission-prompts)）：允许、拒绝、停止，以及只有在 Claude Code 提供了一条真正可以记住的规则时才出现的始终允许。

hook 能回答时就自己回答这个请求。它打开一个短期的回复套接字，把地址随事件一起发出，并最多等你点按 20 秒。Claude Code 同时会在终端里显示它自己的对话框（子代理的调用要等 hook 结束后才显示）。及时的点按会作为 `PermissionRequest` 决定返回（允许、带规则的允许，或拒绝），并关闭这个对话框。如果没有及时的点按，hook 会告诉 aipager 等待结束了（`permission_reply_timeout`），并且不打印任何内容；在联系不上 aipager 或这条路径上任何环节出错时，它也什么都不打印。这时对话框会在终端里等着，之后的点按会通过输入按键来回答它。审计日志（`~/.claude/aipager-audit.jsonl`）记录每次点按是以哪种方式回答的：`"via": "hook_decision"` 或 `"via": "keystroke_fallback"`。

在另一个请求开着时到达的 `PermissionRequest` 也会显示，作为单独的消息放在工作卡片下面。同一个对话框再次到达（同一个提问者、同一个工具和同一次调用，或者同样的输入）只显示一次。Claude Code 也会为 `AskUserQuestion` 运行这个 hook；这个问题已经通过它的 `PreToolUse` 显示了，所以永远不会显示第二次。后台代理的对话框只有在它的 hook 不再等待之后才会出现在终端里，排在那里已经打开的所有对话框下面；所以点按只会把按键输入到 Claude Code 最先显示的那个请求里（见[命令 → 权限请求](commands.md#permission-prompts)）。

### <a id="posttooluse--posttoolusefailure"></a>`PostToolUse` / `PostToolUseFailure`

某个工具完成了，或失败了。它在工作卡片上的那一行会定格（失败会在那一行上标出），并且会注意到对未结请求的回答：针对它的旧的允许或拒绝按钮之后不会再输入任何东西。会话保留最新的 200 行工具记录。

被 Claude Code 移到后台的 `Bash` 调用会报告一个 `backgroundTaskId`。aipager 随后把它当作后台命令跟踪（只限主循环的：带 `agent_id` 的调用属于那个子代理），直到看到它结束：一个带 `<status>` 的 `<task-notification>`（在唤醒 Claude 的消息里，或者在任务进行中到达时出现在保存的对话里）、针对它 ID 的 `TaskStop` 或 `KillShell`、会话结束，或两个小时（`AIPAGER_BG_SHELL_MAX_TRACK`，单位秒，默认 7200）。它在卡片上的那一行见[命令](commands.md#idle-responses)。

在子代理内部发起的 `Agent`（或 `Task`）调用（它的事件带有 `agent_id`）会报告它启动的代理的 ID。aipager 记住哪个代理启动了哪个，这样就能分清一个代理是停下来等它启动的代理，还是已经完成了（见下文 `SubagentStart` / `SubagentStop`）。

已经运行的 `SendUserFile`，aipager 会立刻复制它的文件，每次调用只复制一次，而且只在以提示开始的任务期间复制，依据的是它的 `PreToolUse` 给出的列表（从不使用工具的结果），并逐个再检查一遍；副本在 aipager 为该任务记下的对话记录里找到这次调用、且它写于该任务期间之后，在任务的回答之后发送（[安全](security.md#files-claude-sends)、[命令](commands.md#files-claude-sends)）。Claude Code 在没有 hook 的情况下开始的任务（它接着处理的消息、Stop hook 的反馈）一旦对话记录显示已开始，就算数。

### <a id="notification"></a>`Notification`

aipager 读取 Claude Code 的 `notification_type`：

- `permission_prompt`：一个更慢、更旧的权限请求信号。只有在没有任何请求开着时，它才显示这个请求：如果有一个开着，那就是 `PermissionRequest` 已经显示的对话框。
- 有权限请求或问题在等待时的 `idle_prompt`：会话保持等待状态，聊天里会收到 `⬆️ jim · 仍在等你回复上面的内容`，每次等待一次。
- 其他情况下的 `idle_prompt`：按 `Stop` 处理（见下文）。它能接住 `Stop` 丢失的任务；已经送达的回答绝不会再发一次。

### <a id="stop"></a>`Stop`

任务结束了。会话变为就绪，工作卡片按你的布局定格，并发送 Claude 的回答（来自事件的 `last_assistant_message`，否则来自保存的对话）。接着发送那些一直被暂缓、等任务结束的消息。仍在运行的后台代理和后台命令会让这份工作保持打开：卡片继续作为这份工作的状态，回答末尾附上这些工作的实时状态：每个代理在做什么，以及这份工作有多少后台命令在运行（见[命令 → 发出回答时仍在运行的代理](commands.md#agents-still-running-when-the-answer-goes-out)）。hook 会先清除任务的打开标记，除非 Claude 还持有一条会立刻成为下一个任务的排队消息。

### <a id="stopfailure"></a>`StopFailure`

任务因 API 错误而结束。会话变为就绪，如果有回答，会像 `Stop` 一样送达；已知的错误（服务器过载、请求次数限制等）会用简单的话说明。

### <a id="subagentstart--subagentstop"></a>`SubagentStart` / `SubagentStop`

一个子代理启动或结束了。每个子代理在工作卡片上有自己的一行，`🤖 <type> · <activity> · <elapsed>`，它自己的工具调用折叠在下面（最新的一个总是显示在折叠部分之后），停止时定格为 `✅ 🤖 <type> · 用了 N 次工具 · <elapsed>`。`SubagentStart` 不说明是哪个代理启动了它：aipager 把它和正在等待的同类型 `Agent` 调用（`PreToolUse`）配对。如果这些调用都来自同一个代理，新代理会立刻挂到那个代理下（它在卡片上的行显示为 `🤖 pipeline-runner › ship-tester`，在回答下方的状态里也列在那个代理下面）；如果来自不同的代理，就先不挂靠，直到启动它的代理自己的 `PostToolUse` 说出它的名字。只有恰好一个该类型的调用在等待时，才会用调用的描述。一直等不到 `SubagentStop` 的子代理，在沉默 30 分钟后会被移除（`AIPAGER_SUBAGENT_SILENCE`，单位秒；期间没有任何带它 ID 的事件）。正在工作的代理每隔几秒就会发出工具事件，所以这只会收走那些停止信号被错过的代理（重启、丢失的 datagram）；单次运行 30 分钟、期间该代理 ID 下没有任何事件的工具调用，也会让它被收走。`AIPAGER_SUBAGENT_TTL` 作为同一设置的旧名称仍被接受。没有类型、ID 未知的 `SubagentStop` 事件经常出现，会被忽略。

一个后台代理在它启动的代理仍在运行时停下，也会收到 `SubagentStop`。aipager 把它当作暂停，而不是结束：这个代理仍算作正在运行（它的那一行、这项工作、回答下方的状态、置顶栏）。它下一次带同一 ID 的 `SubagentStart` 表示它继续了，不会再添加第二行；它自己的任何工具调用也算作继续。当它在自己的代理都已不在运行时停下，它才算结束。如果在其中最后一个代理结束两分钟后它仍未继续，它就不再算作正在运行，但绝不会显示为已完成。正在运行的代理，包括正在等待的那些，在 aipager 重启后都会保留。

用 Claude Code 停止后台代理的按键（ctrl+x ctrl+k，按两次）停止的代理不会触发 `SubagentStop`。aipager 会自己结束它们：它自己的停止按下了这些按键时立即结束；Claude Code 的对话记录出现 `agents_killed` 行时（在 Claude Code 随后自己开始的任务的 `UserPromptSubmit` 中读取，那个提示是一句没有包装的普通句子）；或者某个 `Stop` 事件的 `background_tasks` 不再列出之前某个 `Stop` 列出过的代理时。它们打开着的请求会关闭（没有任何 hook 告知这一点），之后才到达的、属于其中某个代理的 `SubagentStop` 会被忽略。当 aipager 自己的停止按下了这些按键，或者 Claude Code 的对话记录里已经出现这次停止时，这个任务不会被当成在终端里输入的消息。

### <a id="precompact--postcompact"></a>`PreCompact` / `PostCompact`

Claude 正在压缩它的上下文（`trigger` 为 `auto` 或 `manual`）。工作卡片显示 `🔄 name · 正在压缩对话`（没有卡片时会新发一条消息），这样这段停顿不会看起来像崩溃。`PostCompact` 结束这个状态；`📦 name · 对话已压缩（压缩前 X%）` 这一行来自随后的 `SessionStart`，如果错过了，则来自状态行。Claude Code 的状态行在下一条消息之前一直显示 0%，所以还不知道新的大小：两个任务之间的压缩，等状态行报出大小后，这一行会变成 `📦 name · 对话已压缩：X% → Y%`。状态行还会在上下文达到 80% 时给出警告 `⚠️ name · 上下文已用 82%，即将自动压缩`（带一个“立即压缩”按钮），每个对话只给一次：压缩不会让它再次出现，新的对话（例如 `/clear` 之后）会再收到一次。

### <a id="messagedisplay"></a>`MessageDisplay`

Claude 自己的文字到达屏幕的那一刻。保存的对话只在每一轮工具调用之后才写入，所以这是 Claude 此刻在说什么的唯一即时来源，也是把它的旁白送进工作卡片的东西。子代理的文字（任何带它 `agent_id` 的事件）绝不会被当作 Claude 自己的：它不会进入工作卡片的旁白、Mini App 或任务的日志，而会成为该代理在回答下方状态里的最新动态。hook 不会给这个事件附加状态行数据：Claude Code 在绘制时会等待它，而且每条消息会触发好几次。

### <a id="premodelswitch"></a>`PreModelSwitch`

Claude Code 2.1.251 及更高版本会在切换模型之前触发它。负载带有 `from_model`、`to_model`、`requested_model` 和 `source`（输入 `/model` 时为 `command`，或 `picker`、`sdk`）。Claude Code 通常会在切换前问 "Switch model?"，因为下一次回答要重新读取整个对话。回答 `permissionDecision: "allow"` 的 hook 会让切换在不问这个问题的情况下进行。

`aipager-hook` 只对 aipager 自己输入的切换回答 `allow`，来源是 Mini App 的模型控件或模型键盘。其他所有切换都得不到回答，所以 Claude Code 会像往常一样询问。这包括机器人主人自己在终端里输入的 `/model`、模型选择器，以及通过 SDK 的切换。

aipager 用一个标记文件识别自己的切换。就在输入 `/model <name>` 之前，aipager 写入 `aipager-modelswitch-<session>.json`：
- 它放在控制套接字所在的文件夹里（通常是 `$XDG_RUNTIME_DIR`）；
- 以原子方式写入，权限 0600；
- 写明模型和 Claude 会话 ID；
- 30 秒后过期。

只有以下条件全部满足时，hook 才回答 `allow`：
- 标记是属于你的文件，而且不是符号链接；
- 它还没过期；
- 它针对的是同一个 aipager 会话和同一个 Claude 会话 ID；
- 它记录的模型等于 `requested_model` 或 `to_model`，不区分大小写；
- `source` 为 `command`。

然后 hook 用一次原子重命名领取这个标记，所以它只会被用一次。这个检查只读一个本地文件，不用套接字，也不等待。这个事件不会转发给 aipager。

Claude Code 2.1.101 及更高版本会忽略它不认识的 hook 事件，所以没有 `PreModelSwitch` 的旧版本不受影响。还有一种情况仍可能出现这个问题：切换到 `opusplan` 或从它切换走，可能让 Claude Code 向 hook 询问两次，而只有第一次询问能找到标记，于是 Claude Code 会在终端里问 "Switch model?"。

## <a id="the-status-line"></a>状态行

不是 hook 事件：Claude Code 每次重绘状态行时都会运行 `statusLine` 命令 `aipager-statusline`（`aipager.dtach.statusline_notify:main`）。这个命令：

- 把 Claude Code 的状态 JSON 写到 `/tmp/claude-status-<session>.json`（hook 读它来获取 `sl_tokens`，`aipager status` 和 aipager 也会读）；
- 给 aipager 发一个 `statusline` datagram，带有模型、上下文百分比、花费、token 数，以及增加和删除的行数，这些会用于工作卡片、`/status`、Mini App、检查模型切换是否生效，以及上下文警告；
- 打印终端的状态行：`[name] Opus 5.5 | 42% ctx | $1.23`。

## <a id="aipagers-own-datagrams"></a>aipager 自己的 datagram

除了 hook 事件，这些辅助程序还会通过同一个套接字给 aipager 发几种自己的消息：

| 消息 | 由谁发送 | 何时 |
|---|---|---|
| `queue_pickup` | hook | 在 `UserPromptSubmit` 时：Claude 取走了哪些 Telegram 消息。 |
| `safety_blocked` | hook | 在 `PreToolUse` 时，安全检查拒绝了这次调用。 |
| `send_files_refused` | 钩子 | 在 `PreToolUse` 时，Claude 想发送的文件被拒绝：每个文件及原因，用于审计日志。 |
| `permission_reply_timeout` | hook | 一次 `PermissionRequest` 等待结束，没有任何点按。 |
| `statusline` | 状态行 | 每次重绘状态行时。 |
| `hook_memory_cap_hit` | hook、状态行 | 进程触到了内存上限。 |
| `report_error` | hook、`aipager` 命令 | 遇到了错误：只有错误类型和它在 aipager 代码中的位置，绝不包含事件的内容，用于[问题报告](problem-reports.md)。 |

## <a id="the-permission-flow"></a>权限请求的流程

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

如果 20 秒内没有点按，hook 什么都不打印，Claude Code 会显示它自己的对话框；之后的点按会通过会话的终端（`dtach -p`）把回答输入到那个对话框里。无论哪种方式，点按都会记录在审计日志里，并发布在卡片下方（`✅ jim · 已允许 · @alice · Bash: ls -la /tmp`）。

## <a id="see-also"></a>另见

- [架构](architecture.md)：hook 接收器在哪里。
- [机器人命令 → 权限请求](commands.md#permission-prompts)：你看到的那一面。
- [安全模型](security.md)：安全检查能覆盖什么、不能覆盖什么。
