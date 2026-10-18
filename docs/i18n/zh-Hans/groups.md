# 团队模式与群组

<!-- i18n-source: docs/groups.md @ a236da1 -->

> 本页译自英文；如果两者不一致，以英文版为准。（[English](../../groups.md)）

默认情况下，aipager 是一个私人机器人：只有你和机器人在一个私聊里，没有别人。**团队模式**把它开放给更多人：一个由好几位开发者一起操作会话的 Telegram 群组、其他人与机器人的私聊，或者两者都有。每个聊天有自己的成员，每个成员有一个角色，决定这个成员在那里能做什么：发送消息、回答 Claude 的问题和权限请求、管理会话，或者只是查看。

团队模式是可选的。只有你自己私聊的安装会像以前一样运行。

## <a id="decide-carefully"></a>请慎重决定

把一个 Telegram 用户加入聊天，就是让这个人**能在运行 aipager 的电脑上运行代码**。根据角色，这个人可以：

- 发送消息，由 Claude 变成 shell 命令、文件修改和网络连接；
- 回答权限请求（允许 / 拒绝）。你的 `~/.claude/settings.json` 仍然决定 Claude 会对哪些工具询问，但任何角色可以批准的人都能点“允许”；
- 启动、停止、结束会话，以及在会话之间切换。

像对待这台电脑的 SSH 访问权限一样对待成员名单。审计日志（[`~/.claude/aipager-audit.jsonl`](security.md#audit-log)）会记录谁做了什么，但只能在事情发生之后。

## <a id="setup"></a>设置

一切都用 `aipager config` 设置。你永远不需要自己编辑文件。

**第一次运行**（还没有配置）。设置向导会问四个步骤：

1. 机器人的 token；
2. 你自己与机器人的私聊（自动检测，或者粘贴）；
3. 你的账号是不是 `owner`（默认是；如果回答否，你会是 `admin`，这个角色对你仍然保留安全底线）；
4. 它会检查依赖项，并安装 Claude Code 的 hook。

它把你的私聊写为第一个聊天。它不询问默认模式：它的摘要写着 "New sessions start in Auto mode; owners and admins can choose Full access in Telegram under /settings → New sessions."（新会话以自动模式启动；所有者和管理员可以在 Telegram 的 /settings → 新会话 里选择完全访问模式）。然后它会问 "Connected. Add a team group or other people now?"，选项是 Add a group、Add a person (DM) 和 I'm done。

**之后的运行**会打开编辑菜单。菜单显示你现有的聊天，并提供：

- **Add a group scope**：一个新群组，以及它的成员（见下文）。
- **Add a DM scope**：另一个人与机器人的私聊。私聊只有一个成员；你为这个成员选择角色（默认 `user`；只有一人使用的安装才选 `owner`，比如朋友自己的容器）。对于没有 `can_manage` 的角色（例如 `user`），它接着会询问这个成员的[根文件夹](#root-folders)，并预先填好默认值（`~/aipager-users/<label>`）。如果这个人已经设置过私聊，设置向导会说明这一点，不做任何更改：请用 Edit a member 修改这个人的角色。
- **Edit a scope**：对群组有 **Add a member**；对每个聊天有 Rename、Edit scope deny_tools、**Set the members' root folder**（见[根文件夹](#root-folders)）和 Remove this scope（最后一个聊天不能删除）。删除聊天之前，先结束它的会话：它正在运行的会话会继续往那里发消息，而且再也没有任何聊天能从 Telegram 操作它们（在电脑上，`aipager session ls` 和 `aipager session kill <name>` 仍然可以）。
- **Edit a member**：Set role、Edit member deny_tools、Remove member（私聊里没有，群组的最后一个成员也不适用）。对于所有者或管理员（或者你的有 `bypass_role_denies` 的角色），Edit member deny_tools 只会说明被禁止的工具对这个角色不适用。
- Test bot reachability、View policy、Re-install Claude Code hooks 和 Refresh bot token。

### <a id="adding-a-group"></a>添加群组

1. 把机器人加入群组，然后选择 Add a group scope（第一次运行时是 Add a group）。
2. **群组的 ID。** 选择自动检测，然后在群组里发送 `/start`，或者粘贴 ID（一个负数）。设置向导会发送一条测试消息，问你是否收到了。已经设置过的群组会在这里停下，显示 "This group is already set up. Use Edit a scope → Add a member."，不做任何更改。
3. 群组的**标签**（显示在设置向导和 Mini App 里）。
4. **先加你自己。** 当你是自己私聊的所有者时，设置向导会建议把你作为群组的第一个成员、以 `owner` 角色加入（默认是；你可以跳过）。如果群组里没有所有者或管理员，那里就没有人能使用完全访问模式、`/settings` 或 `/update`。
5. **其他成员**，一个一个来：如何找到这个人（自动检测，或者粘贴 ID 或 `@handle`）、一个标签（在聊天里显示为 `@label`），以及一个角色（默认 `user`）。角色列表里有 `owner`，并附有警告：所有者完全掌控这台电脑，包括 aipager 的配置、机器人的 token 和 Claude 的登录信息。只把这个角色给你自己。把 `admin` 给应该能在群组里使用完全访问模式、`/settings` 和 `/update` 的人。（在自己与机器人的私聊里，任何可以发送消息并回答 Claude 的人，都能对自己的会话使用完全访问模式，无论角色是什么。）
6. **一条可选的规则：** "Block file edits (Write, Edit) for `user` members in this group? Admins and the owner are not affected."（在这个群组里对 `user` 成员禁止修改文件（Write、Edit）？管理员和所有者不受影响。）默认是否：`user` 本来就只能在会话的项目文件夹里写入。如果选择是，角色不能绕过角色规则的成员（`user`、`read_only`，以及你创建的没有 `bypass_role_denies` 的角色）的任务里，每次 Write 和 Edit 都会不经询问地被拒绝，任务也会停止。这时那部分工作就得由管理员或所有者来发送。
7. **成员的根文件夹**，预先填好 `~/aipager-users/group-<label>`：群组的 `user` 和 `read_only` 成员在这里启动会话并工作（见[根文件夹](#root-folders)）。

要把某人加入已经设置好的群组，用 Edit a scope → 那个群组 → Add a member。群组里已有的每个人都保持原来的位置和角色。

### <a id="finding-ids-auto-detect"></a>找到 ID（自动检测）

自动检测成员时，会取一个还没被添加的人最新的消息，并在使用之前问 "Is this the person to add?"（这是要添加的人吗？）（回答否，它会跳过这个人继续找）。它会建议用这个人的 Telegram 用户名作为标签。在群组里，这个人必须呼叫机器人：提及机器人或发送 `/start`（见[机器人读取什么](#what-the-bot-reads-in-a-group)）。设置向导会说明它在哪里查看：

- **"Watching through the running daemon"**（通过正在运行的 aipager）：在 `aipager start` 运行期间，aipager 会收到发给机器人的所有消息，所以设置向导会读取 aipager 在 `~/.claude/aipager-pending-users.json` 里记录的内容。aipager 会记录在它尚未服务的群组里呼叫机器人的人（群组的 ID、标题和类型，以及这个人的 ID、用户名和名字），以及在它服务的群组里任何不是成员的人。它从不在自己不服务的群组里回复。来自尚未设置的人的私聊消息会得到回复，但不会被记录，所以要在 aipager 运行期间添加某人的私聊，请粘贴对方的 ID（或者先停止 aipager）。
- **"Watching Telegram directly"**（直接从 Telegram）：aipager 停止时，设置向导会自己向 Telegram 查询机器人最近的消息。

第一次运行时，你自己的私聊总是从 Telegram 读取。来自匿名群组管理员的消息，或者以群组名义、频道名义发布的消息不算数：它的发送者 ID（1087968824、136817688 或 777000）是所有这样发布的人共用的，所以设置向导会拒绝它，无论是输入的还是检测到的。请让这个人用自己的名义发消息。

### <a id="where-the-config-lives"></a>配置存放在哪里

设置向导写入 `~/.config/aipager/aipager.yaml`（权限 0600）：机器人的 token、机器人服务的聊天，以及每个聊天的成员和他们的角色。每个角色能做什么写在 `~/.config/aipager/policy.yaml` 里，这个文件属于你：设置向导从不写它（见[规则如何运作](#how-rules-work)）。用旧的 `team.yaml` 格式设置的安装会被自动转换，由 aipager 在启动时完成，或者由设置向导在下一次运行时完成。

### <a id="live-reload"></a>不重启地重新加载

每次在编辑菜单里修改聊天或成员之后（添加或删除群组或私聊、添加或删除成员、设置角色、编辑 `deny_tools`、重命名聊天），以及在第一次运行结束时添加了群组或某个人、或者你继续完成了一个之前没做完的群组之后（对你在那里添加的所有内容重新加载一次），`aipager config` 都会给正在运行的 aipager 发送一个重新加载的指令（**SIGUSR1**），并显示 "Scopes reloaded live (no daemon restart needed)"。重新加载会重新读取 `aipager.yaml` 和 `policy.yaml`。如果磁盘上的配置会被拒绝，设置向导会显示 "Not applied" 和原因，aipager 会保留旧配置，直到你修好为止。如果设置向导不确定哪个进程是 aipager，它会让你重启 aipager。

一次重新加载会立即应用的内容：

- 每个聊天里谁能使用机器人，以什么角色和规则；
- 新的群组或私聊开始接收消息，并得到它的 `/` 菜单；被删除的聊天会停止（它的会话会继续运行，直到你结束它们）；
- 来自一个在那个聊天里已不能再发消息的人、正被暂缓的消息（在等某个请求被回答，或者在等别人的任务结束）会被丢弃：它会得到一个 🤷，机器人会回复“没有发给 Claude：@bob 已不能再在这里发消息。”；
- 被删除或失去权限的人正在运行的任务，在剩余的部分里只保留新旧规则中更严格的那部分（对被删除的人来说是受限的最低权限）。在终端里输入的任务不会被改变，除非有一条 Telegram 消息并入了它：aipager 不记录那是谁的，所以当那个聊天的任何一个所有者（或者在私聊里，它的成员）失去权限时，这样的任务就会被收紧。aipager 重启时已经在运行的任务（仍在工作，或者在等一个问题或权限请求）会按那个聊天里所有失去权限的成员来收紧。权限提升绝不会放宽正在运行的任务：新的权限从这个人的下一个任务开始生效。

只有**机器人 token** 需要重启（`aipager config` 在 Refresh bot token 之后会这样说）。没有 aipager 在运行时，不会发送重新加载的指令：下一次 `aipager start` 会读取新配置。

要手动重新加载（例如在编辑 `policy.yaml` 之后），只给 aipager 进程发送信号。在后台服务下：

```sh
pid=$(systemctl --user show -p MainPID --value aipager.service)
[ "${pid:-0}" -gt 0 ] && kill -USR1 "$pid"
```

（这个检查很重要：服务没有运行时，`MainPID` 是 `0`，而 `kill -USR1 0` 会把信号发给你自己 shell 的进程。）

对于你自己用 `aipager start` 启动的 aipager，把 `kill -USR1 <pid>` 发给那个进程。不要给 `pgrep -f 'aipager start'` 的每个结果都发信号（运行 aipager 的 shell 或容器也会匹配，而 SIGUSR1 会停止不处理它的进程），也不要在没有 `--kill-whom=main` 的情况下使用 `systemctl --user kill`（服务的会话和它共用进程组，会被停止）。

如果重新加载时 `aipager.yaml` 或 `policy.yaml` 损坏了，或者 `aipager.yaml` 不存在，aipager 会在日志里写一条警告，并在内存里保留之前的配置。一个笔误或一个被移走的文件既不会把你锁在外面，也不会把别人放进来：重新加载绝不会让一个已经配置了聊天的 aipager 回到个人模式（只有重启才会）。

### <a id="bot-admin-rights-and-privacy-mode"></a>机器人的管理员权限和隐私模式

**把机器人设为群组的管理员，并给它置顶消息的权限。** 机器人需要这个权限来置顶群组的状态栏（见[大家看到什么](#what-everyone-sees)）。没有它，群组照常工作，只是没有状态栏；如果你之后才给机器人这个权限，请重启 aipager，让状态栏出现。

作为群组管理员的机器人会收到群组里的每条消息，无论它在 BotFather 里的隐私模式如何。这是安全的：aipager 只处理[机器人读取什么](#what-the-bot-reads-in-a-group)里列出的消息，其余的都忽略，所以群组里的闲聊绝不会到达会话。不是管理员的机器人默认处于隐私模式（保持开启即可），这时 Telegram 只会把群组里的部分消息转给它；如果你的某条消息没有到达，请回复机器人的一条消息，或者使用命令。隐私设置不会改变 aipager 处理的内容。

在 Telegram 里是群组的管理员，并不会让一个人在 aipager 里多出任何权限。谁能做什么，只取决于这个人在 `aipager.yaml` 里的角色。

### <a id="when-telegram-upgrades-the-group"></a>当 Telegram 升级群组时

当你把群组设为公开、开启话题、成员超过 200 人或修改某些管理员权限时，Telegram 会把普通群组变成超级群组，升级会给群组一个新的聊天 ID。aipager 会自动跟上：当它看到升级时（Telegram 在群组里的通知，或者第一次向旧 ID 发消息时），它会把 `aipager.yaml` 里群组的 scope 迁移到新 ID，把群组的会话、消息目标和 `/settings` 一起迁过去，并在群组里发一行：“Telegram 已升级这个群组。aipager 已随之迁移，你无需任何操作。”置顶状态栏会在升级后的群组里重新开始。aipager 的日志里会有一条写明两个 ID 的警告。

aipager 内部的会话名称保留旧 ID（你永远看不到它们），`aipager.yaml` 会保留一条关于这次迁移的简短 `chat_migrations` 记录，所以升级之前启动的会话仍然属于升级后的群组，即使重启之后也是如此。带旧 ID 的会话内部名称（`/name__g<old id>`）在升级后的群组里可以使用，在其他地方不行。如果升级时 aipager 停着，它会在下一次启动时发现。在升级那一刻正在运行的任务会照常完成，它的回答会到达升级后的群组，但它的工作卡片在这个任务剩下的时间里不会再出现。如果新 ID 已经是一个单独的 scope，就不会迁移任何东西，日志会说明这一点；请用 `aipager config` 删除其中一个。`aipager config` 里的 “Test bot reachability” 遇到已升级的群组时会显示新 ID。

每次启动时，aipager 会对每个群组查询一次。如果 Telegram 回答找不到群组，或者机器人无法使用那个群组（群组已被删除、机器人已被移出，或者群组在 aipager 停止期间升级了而 Telegram 没有说明是哪个新 ID），aipager 的日志里会有一条写明 scope 的警告：请检查机器人是否还在群组里；如果群组已经升级，请在 `aipager config` 里用 Add a group scope 重新添加群组（群组会得到新 ID；请重新添加它的成员），然后用 Edit a scope → Remove this scope 删除旧的 scope（旧 ID 已经不能再用了）。aipager 不会发任何消息，也不会不停地重试。

### <a id="forum-topics-are-not-supported"></a>不支持论坛话题

每个团队请使用一个普通群组（不开话题）。在开启了话题的群组里，aipager 仍然能工作，但不会停留在某一个话题里：

- 对命令或消息的回复（例如 `/status`，或者对“哪个会话？”的回复）会留在发送它的那个话题里；
- 通知、工作卡片、回答、权限请求和置顶状态栏都发到群组里而不带话题，所以它们可能落在 General 里；
- 每个人在整个群组里只有一个当前会话，而不是每个话题一个。

开启话题也会把普通群组升级为超级群组，aipager 会跟上（见上文）。

## <a id="roles"></a>角色

有四个内置角色（见 `aipager/safety.py`）。一个人的角色，是这个人**在当前操作的那个聊天里**的角色。没有人能从 Telegram 或 Mini App 修改成员或角色：只有电脑上的 `aipager config` 能做到，这让这件事掌握在所有者手里。

| | `owner` | `admin` | `user` | `read_only` |
|---|---|---|---|---|
| 发送消息（`can_prompt`） | ✅ | ✅ | ✅ | ❌ |
| 回答 Claude：允许、拒绝、问题（`can_approve`） | ✅ | ✅ | ✅ | ❌ |
| 管理：完全访问模式、群组的 `/settings`、`/update`、安装语音消息功能（`can_manage`） | ✅ | ✅ | ❌（完全访问模式：在自己的私聊里 ✅） | ❌ |
| 从 Telegram 或 Mini App 修改成员和角色 | ❌ | ❌ | ❌ | ❌ |
| 绕过角色的禁止规则（`bypass_role_denies`） | ✅ | ✅ | ❌ | ❌ |
| 绕过安全底线（`bypass_safety`） | ✅ | ❌ | ❌ | ❌ |
| Bash 和其他运行代码的工具 | ✅ | ✅（规则只是尽力而为） | ❌ | ❌ |
| 所有斜杠命令 | ✅ | ✅ | 只有“命令”键盘上的命令、`/compact` 和更换模型 | 同 `user` |
| 在哪里启动会话（`/new`、恢复、重启、切换模式） | 任何地方 | 任何地方 | 只在聊天的[根文件夹](#root-folders)里 | 同 `user` |
| 在哪里写入文件 | 任何地方 | 安全底线允许的任何地方 | 会话的项目文件夹（当它在根文件夹里时）和会话的 scratchpad，绝不是主文件夹 | 同 `user` |
| 在你的主文件夹里读取 | 安全底线允许的任何地方 | 安全底线允许的任何地方 | 只在根文件夹里（电脑上的其他文件夹照旧，其他成员的根文件夹则绝不可以） | 同 `user` |
| 存放登录信息的文件（`~/.ssh`、`~/.git-credentials`……） | 可以读取 | 可以读取 | 绝不（默认） | 绝不（默认） |
| `Grep`/`Glob` | 任何地方 | 除 aipager 的受保护文件夹之外的任何地方（不在是这样的文件夹、位于其中或包含它的文件夹里搜索） | 会话的项目文件夹及其 scratchpad | 同 `user` |

- **owner**：完全掌控，包括内置的安全底线。应该恰好有一个人：操作这台电脑的人。
- **admin**：所有者在 Telegram 和 Mini App 里能做的一切：把会话切换到完全访问模式（以及以这个模式启动会话）、在完全访问模式下恢复会话、修改群组的 `/settings`、运行 `/update`、安装语音消息功能、发送任何斜杠命令。管理员还会绕过角色的禁止规则（scope 和成员的 `deny_tools` 对他们不适用）。有两样东西仍然属于所有者：
  - 修改成员和角色：只有电脑上的 `aipager config` 能做到，Telegram 和 Mini App 里的任何东西都做不到；
  - 绕过安全底线（aipager 的配置和机器人 token、Claude Code 的登录信息、aipager 的控制文件）。管理员的 `Grep` 和 `Glob` 可以在其他任何地方搜索，但不能在这些文件夹之一、位于其中或包含它的文件夹里搜索：在 `~`、`/` 或 `/tmp` 上（或者在主文件夹里的会话中不指定文件夹）运行 `Grep` 会被拒绝，模式从这样的文件夹开始的 `Glob` 也一样（例如 `/home/you/.config/aipager/*`）。请在项目文件夹里搜索。在没有 `Grep` 工具的 Claude Code 版本上，Claude 会通过 Bash 搜索；在那里，对整个主文件夹或 `/` 的递归搜索或列出（`grep -rn x ~`、`rg x $HOME`、`find / -name x`）也会被拒绝，这是尽力而为的（从主文件夹或其下运行的、不指定文件夹或用 `..` 的搜索看不到），而在项目里的搜索不受影响，包括在项目里搜索主文件夹路径这个文字（`grep -rn "$HOME" src`）（[安全](security.md#team-mode-enforcement)）。

  管理员仍然有 Bash，而有了 shell，最低权限的命令模式就只是尽力而为，不是边界：只有在你愿意把这台电脑的 shell 交给某人时，才让这个人当管理员。
- **user**：禁止规则生效。没有 Bash，没有其他运行代码的工具（PowerShell、Monitor、定时消息、workflow；列表是 `safety.CODE_EXECUTION_TOOLS`），不能用 `SendMessage` 发给其他会话，不能切换 worktree。只能在会话的文件夹和会话的 Claude Code scratchpad 里写文件，并且绝不能写那个文件夹里的 `.claude/`、`.git/` 或 `.mcp.json`（它们每一个都会运行命令）。`Grep` 和 `Glob` 只在这些文件夹里搜索，并且只用简单的相对 glob。`user` 只能在聊天的[根文件夹](#root-folders)里启动、恢复、重启会话和切换模式，并且不能读取你主文件夹里根文件夹之外的任何东西。会话文件夹如果是你的主文件夹、`/` 或其上级文件夹，或者在根文件夹之外（在有根文件夹之前启动的会话，或者别人启动的会话），就不算数：在那里，`user` 只能在 scratchpad 里写入和搜索。读取遵守受保护路径的规则，存放你登录信息的文件永远不能读取：`~/.ssh`、`~/.gnupg`、`~/.aws`、`~/.config/gh`、`~/.git-credentials`、`~/.netrc`、`~/.docker/config.json`、`~/.kube`、`~/.config/gcloud`、`~/.azure`、`~/.password-store`、`~/.pgpass`、`~/.npmrc` 和 `~/.pypirc`。斜杠命令：只有“命令”键盘上的命令、`/compact` 和更换模型（见[消息以谁的身份运行](#who-a-message-runs-as)）。
- **read_only**：观察者。他们能看到群组里的一切，可以使用 `/status`、`/diff`、`/whoami`、`/help`、`/start` 和 `/app`，查看 `/settings`，并翻看别人打开的 `/resume` 列表。他们自己的文字消息、语音消息和文件不会被发到任何地方。他们在一个聊天里发的第一条消息会得到一条回复，说明他们的角色是 `read_only`；之后，机器人在那个聊天里对他们保持沉默（不回复，不加表情回应），直到 aipager 重启。他们和 `user` 有同样的工具和写入限制，以防他们的某条消息到达了会话。

### <a id="custom-roles"></a>自建角色

`policy.yaml` 可以修改内置角色的任何字段，也可以定义新角色。你设置的字段会替换内置的值。字段有：

- `can_prompt`、`can_approve`：发送消息；回答 Claude。
- `can_manage`：使用完全访问模式、修改群组的 `/settings`、运行 `/update` 和安装语音消息功能（内置：`owner` 和 `admin` 为 true，`user` 和 `read_only` 为 false）。它绝不会让任何人修改成员或角色，也绝不会绕过安全底线。
- `bypass_role_denies`、`bypass_safety`：见上表。
- `deny_tools`、`allow_tools`、`deny_bash_patterns`、`deny_paths_no_access`、`deny_paths_no_write`：见[规则如何运作](#how-rules-work)。

你从头定义的角色一开始 `can_prompt` 和 `can_approve` 开启，`can_manage` 和两个绕过权限关闭，存放登录信息的文件在它的 `deny_paths_no_access` 里，并且**没有任何工具被禁止，所以它有 Bash**，直到你设置 `deny_tools`（有成员拥有这个角色时，`aipager doctor` 会警告一次）。写入和搜索的限制适用于每个没有 `bypass_safety` 或 `bypass_role_denies` 的角色。[根文件夹](#root-folders)适用于每个没有 `can_manage` 的角色，无论它有什么绕过权限：你定义的有 `bypass_role_denies` 或 `bypass_safety`、但没有 `can_manage` 的角色，可以在它的根文件夹（以及它的 scratchpad）里的任何地方写入和搜索，其他地方都不行；它也不能读取你主文件夹里根文件夹之外的任何东西（有 `bypass_safety` 时，在根文件夹之外运行的会话里，它只能写入 scratchpad）。只有所有者和管理员不受限制。

### <a id="root-folders"></a>根文件夹

每个聊天都有一个给成员用的根文件夹：在那个聊天里角色没有 `can_manage` 的成员（内置的 `user` 和 `read_only`，以及你定义的没有它的角色）在这里启动并保留他们的会话。所有者和管理员不受它约束。私聊的根文件夹属于那个人；群组的根文件夹由群组里的受限成员共用。两个聊天绝不会共用一个文件夹，除非你有意给它们同一个文件夹（设置向导会让你确认）。

- **设置文件夹。** 当你添加一个群组，或者为没有 `can_manage` 的角色添加一个私聊时，`aipager config` 会询问它，并预先填好默认值；Edit a scope → **Set the members' root folder** 可以修改它（立即生效，通过一次不重启的重新加载）。当前配置会显示每个聊天的根文件夹，没有设置时会标出 `(default)`。
- **默认值**对私聊是 `~/aipager-users/<label>`（那个人的标签），对群组是 `~/aipager-users/group-<label>`。标签会转换为小写，除字母、数字、`.`、`_` 和 `-` 之外的字符都会变成 `-`；当两个聊天会得到同一个文件夹时，每个聊天都会加上自己的聊天 ID（`sara-123456`）。在成员第一次打开 `/new` 或 Mini App 的“启动会话”表单时，这个文件夹（以及新建的 `~/aipager-users`）会以 0700 权限创建，只有你能打开。在会话于其中启动之前，aipager 会把它标记为 Claude Code 信任的文件夹，这样 Claude Code 的“Do you trust this folder?”问题不会结束成员的第一个会话。默认值绝不会移动：当 `aipager config` 里的某次修改（另一个聊天的根文件夹、被删除的聊天、新的标签）会改变某个聊天的默认文件夹时，设置向导会把那个聊天当前的文件夹写为它的根文件夹。
- **设置向导拒绝的内容**：相对路径、通配符、`/`、你的主文件夹或它的上级文件夹、是 aipager 的受保护文件夹或存放登录信息的文件之一、位于其中或包含它们的文件夹（`~/.claude`、`~/.config/aipager`、`~/.config`、`~/.ssh`……），以及 Claude Code 的临时文件夹（`/tmp/claude-*`，所有会话的 scratchpad 都在那里）。还不存在的文件夹没有问题。对于你的系统会从中运行程序的文件夹（`~/bin`、`~/.local/bin`、`~/.config/systemd`、`~/.config/autostart`，或它们里面的文件夹），它会让你确认。
- **启动会话。** `/new` 和 Mini App 的“文件夹”页面会列出根文件夹和直接位于其中的文件夹（不含隐藏文件夹，也不含指向外面的链接），没有“默认文件夹”这一行；新会话会在根文件夹里启动，除非你选了其中一个文件夹。“新建文件夹”会在你选中的文件夹里创建文件夹。其他一切都会得到“你的角色只能在你的文件夹 ~/... 或其中的文件夹里启动会话。”
- **在它之外的会话。** 对文件夹在根文件夹之外的会话执行恢复、重启、换成新会话和切换模式，会得到“这个会话在你的文件夹 ~/... 之外工作，所以你的角色不能恢复、重启它或更改它的模式。请联系管理员。”发给这样一个正在运行的会话的消息仍会送达，安全 hook 会约束这个任务：在那里它只能写入 scratchpad。
- **hook 把任务约束在什么范围内。** 受限成员的任务只能写入会话的文件夹（当它在根文件夹里时）和它的 scratchpad，不能读取、不能 `Grep` 或 `Glob` 你主文件夹里根文件夹之外的任何东西（它的 scratchpad 和它自己的回复文件除外），也绝不能触及另一个聊天的根文件夹，无论它在哪里。你主文件夹之外的文件夹（`/usr`、`/etc`……）仍然可以读取，受保护文件夹仍然会被拒绝，`Bash` 对 `user` 仍然是禁止的。当一个任务混合了根文件夹不同的两个成员时，范围更窄的那个胜出；两个互不包含的文件夹只剩下会话自己的文件夹。

要把 Bash 还给 `user`（只给你放心交出 shell 的人），在 `allow_tools` 里写上它（这样它也会成为那个角色的允许名单），或者替换它的 `deny_tools`：

```yaml
roles:
  user:
    # 内置列表，去掉 Bash
    deny_tools: [PowerShell, Monitor, REPL, Workflow, CronCreate,
                 RemoteTrigger, AppifactRepl, self_hosted_runner_spawn_local,
                 self_hosted_runner_requeue_session, SendMessage,
                 EnterWorktree, ExitWorktree]
```

之后 `aipager doctor` 会警告：`role user has Bash: its safety rules are best-effort, not a boundary`。如果你的 `policy.yaml` 在有这个默认值之前就为 `user` 设置了 `deny_tools`，你的列表仍然会替换它，所以这个角色在你重新加上之前都有 Bash；doctor 的警告会告诉你。这些限制不涵盖什么，见[安全 → 团队模式下的规则执行](security.md#team-mode-enforcement)。

## <a id="talking-to-the-bot-in-a-group"></a>在群组里与机器人对话

### <a id="what-the-bot-reads-in-a-group"></a>机器人在群组里读取什么

在群组里，aipager 只处理以下这些消息：

- **命令**：`/status`、`/x1 fix the tests`，或者命令后面带 `@aipagerbot` 的形式（`/x1@aipagerbot`），这是你从菜单里选择命令时 Telegram 加上的。发给其他机器人的命令（`/x1@otherbot`）会被忽略；
- **对机器人消息的回复**；
- **提及机器人的消息**：`@aipagerbot fix the tests`；
- 群组里**在机器人键盘上的点按**。在群组里，键盘上的每个按钮都以一个小方块开头（`▫️ 停止`、`▫️ 清空对话`），只有带这个标记的文字才算作点按。直接输入的文字只是普通的聊天：成员输入 `停止`、`清空对话`、`Opus` 或某个会话的名称，不会改变任何东西。要不用键盘、有意使用键盘上的某个词，就呼叫机器人：`@aipagerbot 停止`，或者把 `停止` 作为对机器人某条消息的回复发送。

其他一切（成员之间的聊天、不是回复机器人的照片或语音消息）都不会得到表情回应或回复。无论机器人是不是群组管理员都是如此。编辑过的消息在任何地方都会被忽略：编辑一条消息什么都不会发生，请重新发送它。

照片、文件或语音消息也一样：把它作为对机器人的回复发送，或者在说明文字里提及机器人。对于相册，请把说明文字放在第一项上：在带说明文字、提及或回复的那一项之后到达的相册项会被接收，在它之前的不会。

**提及会被去掉。** `@aipagerbot fix the tests` 到达 Claude 时是 `fix the tests`（Claude Code 会把 `@word` 当成一个文件）。`@aipagerbot status` 和 `@aipagerbot x2` 的效果和在私聊里输入 `status` 或 `x2` 一样，只发 `@aipagerbot` 会告诉你你的消息正发往哪里。提及之后的会话命令仍然可用（`@aipagerbot /x1 fix it`），但 aipager 自己的命令不行：`@aipagerbot /stop` 会得到“请单独发送 /stop，不要同时提及机器人。”

**仅限成员。** 不是群组成员的人会得到一条回复（“🚫 你不在这个聊天的允许名单中。”），附上对方的 Telegram ID，方便对方转交给你，并会被记录下来供 `aipager config` 使用；之后机器人会忽略这个人，直到 aipager 重启。`read_only` 成员会被告知一次自己的角色（见[角色](#roles)）。

**匿名管理员必须用自己的名义发消息。** 开启了“Remain anonymous”的群组管理员，或者以群组名义、频道名义发布的消息，都不会显示是谁写的：Telegram 对所有匿名管理员显示同一个发送者。aipager 无法把任何人的角色归给这样的消息，所以它会回复“请用你自己的身份发言，才能使用机器人。”（每个聊天一次，直到 aipager 重启），不做其他事。它绝不会被记录为等待中的人，设置向导也会拒绝那个共用的 ID。请在群组的管理员设置里为自己关闭“Remain anonymous”，或者用你自己的账号发消息。

### <a id="talking-to-a-session-in-a-group"></a>在群组里与会话对话

回复某个会话的消息（它的回答、它的卡片），你的消息就会发到那个会话。或者发送 `/x1 你的消息`，或者以 `/x1` 开头的说明文字。

**每个人都有自己的当前会话**：也就是这个人最近切换到（`/x1`）、发消息给、回复过或启动的那个会话。另一个成员的会话回复不会改变它，别人的消息也不会。提及、语音消息、模板或键盘上的命令都会发到你自己的当前会话，键盘上的命令或模板会在回复里写明那个会话（“🧹 已把 /clear 发送到 x1”）。不带名称的 `/stop`、`/mode`、`/kill`、`/restart`、`/rename`、`/delete` 或 `/diff` 只作用于你自己的当前会话（经过它通常的确认步骤），并且只在命令对那个会话有意义时（`/stop` 要求正在工作，`/delete` 要求已结束）；否则它总是显示选择器，即使只剩一个会话，因为那个会话可能是别人的。`/clearqueue` 和 `/now` 作用于你的当前会话。`/kill x1` 和其他带会话名称的命令作用于那个会话，和以前一样。

- 当你还没有自己的当前会话、而且有不止一个会话在运行时，机器人会问“哪个会话？请回复它的某条消息，或发送 /x1 加上你的消息。”，每个会话一个按钮，不发送任何东西。只有一个会话在运行时，消息会发到那里。
- 如果你的当前会话已经结束，消息不会被发到唯一还在运行的会话（它可能是别人的）：机器人会回复“x1 已结束。用哪个会话？”，每个正在运行的会话一个按钮，在 x1 可以恢复时还有“▶️ 恢复 x1”，不发送任何东西。
- 在这两个问题里点某个会话的按钮，也会把问题所针对的消息（一条文字消息、语音消息的转写文字或一个文件）发到那个会话，就像你是在那里发送的一样，问题会变成“已发送到 x2。”。只有发送了那条消息的人才能使用这些按钮，并且只在十分钟内、在 aipager 重启之前有效：之后按钮只会把那个会话设为你的当前会话（“这条消息太旧，没有发送；请重新发送。”）。▶️ 恢复 会把 x1 带回来，但不发送任何东西：请重新发送你的消息。
- 在群组里，置顶栏没有“消息会发到”这一行，因为它对每个人都不一样。

**等待回答的卡片属于每个人自己。** 新会话卡片写着“回复这条消息，写上名称；如果愿意，也可以写上第一步要做什么：”，重命名的问题写着“请回复这条消息，写上新名称。”。在群组里，每张卡片都会显示它是谁的（“🆕 新建会话（@alice）”），只有那个人能回复，两个人可以同时启动或重命名会话。另一个成员对某人新会话卡片的回复会得到“这张卡片是 @alice 的。发送 /new 获取你自己的卡片。”，不会发到任何地方。重命名的问题在 10 分钟后、或者它的主人做了别的事时就不再等待。“x1 已就绪”卡片、对 `/x1`、`/start` 和 `/help` 的回复都会说明要回复会话的消息或提及机器人（“回复 x1 的消息（或提及 @aipagerbot），就能和这个会话对话。”），因为普通消息到达不了机器人。

**键盘属于每个人自己。** 模板、命令、模型 › 和 « 返回 只改变点按它们的那个人的键盘：机器人以回复那次点按的方式应答，其他人的键盘都不会变。« 返回 会从你当前的位置往上一层，与其他成员在看什么无关。`/start` 发送的主键盘属于整个群组，会把所有人带回主键盘。当群组里的会话启动或结束时，aipager 会把这个主键盘重新发给群组一次，让它的会话按钮始终正确。从还没有带标记按钮的版本升级之后，点按旧键盘上的按钮可能会被忽略：发送一次 `/start`，让群组得到新的键盘。私聊的键盘没有标记，和以前一样工作。

### <a id="the-mini-app"></a>Mini App

`/app` 只在私聊里打开 Mini App。在群组里，它不在 `/` 菜单里；如果在那里输入，对已经设置了与机器人私聊的成员，它会说“📱 Mini App 只能在私聊中使用：请私聊机器人，并在那里发送 /app。”，对其他人则说“Mini App 需要从你和机器人的私聊中打开。请让机器人主人添加你。”

Mini App 一次显示一个聊天。属于多个聊天的人（例如自己的私聊和一个群组）会在顶部看到一行聊天名称，可以来回切换；它会在这个人的私聊打开，没有私聊时就在第一个列有这个人的聊天打开，并在那部手机上记住上一次的选择。里面的一切（会话、它们的按钮、设置以及它发出的通知）都属于当前选中的聊天，使用这个人在那个聊天里的角色。只有一个聊天时，不会多出任何东西。

## <a id="who-a-message-runs-as"></a>消息以谁的身份运行

每条消息都以**发送它的人的权限**运行，在消息发出的那个聊天里。一个在群组里是 `user`、在自己私聊里是 `owner` 的人，在群组里以 `user` 的身份发送、点按和运行命令，与聊天在 `aipager.yaml` 里的顺序无关。

- 每条 Telegram 消息都把发送者的身份和规则带进会话（见[安全 → 团队模式下的规则执行](security.md#team-mode-enforcement)）。Claude 能看到每条消息是谁发的（在群组里是 `[via Telegram · @bob · role:user]`），绝不会是之前操作会话的人。发送消息的人会成为操作会话的人，无论是直接输入、用 `/x1`、模板还是键盘上的命令。
- **被暂缓的消息保留它的发送者。** 当另一个人的任务正在运行时（包括那个任务启动的后台代理），或者别人的消息仍在等待被接收时，或者有一个打开的权限请求或问题时，新消息会被暂缓（👀），等这些结束后作为单独的任务发送，**以它自己发送者的权限**。在终端里输入的任务算作所有者的：只有所有者的消息（或者在私聊里，那个聊天自己成员的消息）才会并入它。你自己的消息绝不需要等你自己的任务。如果它们等待的任务被停止了，或者因为一个被禁止的工具而被拦下，被暂缓的消息就会被丢弃（🤷）：请重新发送它们。
- **最严格的规则胜出。** 如果不同人的消息仍然落进了同一个任务，这个任务会按其中所有人最严格的组合运行。并入正在运行的任务的消息绝不会放宽它：任务的剩余部分保留所有并入者中最严格的规则。
- **受限角色的斜杠命令。** 不能绕过角色规则的角色（`user`、`read_only`、你定义的没有 `bypass_role_denies` 的角色，以及 aipager 不认识的发送者）只能发送“命令”键盘上的斜杠命令（如果 `keyboard.json` 修改了它，就是你的那份）、`/compact` 和更换模型（`/model <name>`）。其他一切，无论是直接输入、用 `/x1 /deliver ...` 发送、作为文件的说明文字、作为 `/new x1 /review ...` 的第一条消息还是作为模板，都会得到“这个命令仅限管理员。”和一个 🤷，什么都不会到达 Claude。所有者和管理员可以发送任何命令。
- **受限角色远离主文件夹和存放登录信息的文件。** `user` 或 `read_only` 的任务绝不会写入作为会话文件夹的你的主文件夹、`/` 或其上级文件夹（只会写入 scratchpad），也绝不会读取[角色](#roles)里列出的存放登录信息的文件。
- **受限角色留在自己的根文件夹里。** 没有 `can_manage` 的角色的任务，不能读取你主文件夹里聊天的[根文件夹](#root-folders)之外的任何东西，也不能写入根文件夹之外的会话文件夹。根文件夹是成员发送消息的那个聊天的根文件夹，无论消息发到哪个会话。
- **被删除的成员会立即失去权限，但不会失去正在运行的任务。** aipager 已不认识的发送者（在消息等待期间被删除）会按受限的最低权限运行。一次不重启的重新加载之后，被删除成员暂缓的消息会被丢弃，其仍在运行的任务会继续运行，在完成之前一直受限于受限的最低权限；发送 `/stop` 可以立即结束它（见[不重启地重新加载](#live-reload)）。
- **来自另一个 Claude 会话的消息带着发送者的权限。** 一个会话发给另一个会话的消息（Claude Code 的 `SendMessage`）以 aipager 核实的、发送它的那个任务的权限运行，绝不以接收会话之前的权限运行。aipager 无法核实的消息会按最严格的规则运行，不能修改文件，也不能访问网络（见[安全 → Claude 会话之间的消息](security.md#messages-between-claude-sessions)）。
- **被拦下的工具会停止任务。** 当任务尝试使用它的规则所禁止的工具时，任务会立即停止，显示“🛑 x1 · 被安全规则阻止：...（已停止）”，这个任务里之后的每次工具调用也都会被拒绝。

## <a id="who-can-tap-what"></a>谁能点什么

每个按钮都会在点按的那一刻，检查点按者在按钮所在聊天里的角色。发送产生按钮的命令的那个人的角色不算数。（有些卡片还会检查是谁要求的：确认卡片和 `/new` 的卡片，见下文。）

- **每个成员都能查看**（VIEW）。`read_only` 成员可以翻看 `/resume`，打开 `/status` 的“已结束”部分、会话的 ⋮ 菜单、它的模式或改动，查看 `/settings`，以及关闭菜单。他们不能操作：其他每个按钮都会回答“你的角色不能在这个聊天里这样做。”
- **回答 Claude 需要 `can_approve`**（APPROVE）：允许、始终允许、拒绝、问题的选项、提交、继续，以及置顶栏上的“回答”按钮。`read_only` 没有这个权限。
- **操作会话需要 `can_prompt`**（PROMPT）：停止、重试、压缩、立即发送、结束、重启、重命名、删除、恢复、切换模式、✍️ 对话、全部清除、会话自己的设置以及 `/new` 的卡片。aipager 不认识的按钮也需要这个权限。
- **切换到完全访问模式需要管理员**（MANAGE：`owner`、`admin`，或者有 `can_manage` 的角色，与 `/mode full` 规则相同）：切换到完全访问模式的卡片上的“是，切换”和“停止并切换模式”，以及以完全访问模式恢复。在询问模式和自动模式之间切换，任何能操作这个会话的人都可以。恢复一个处于完全访问模式的会话（输入 `/resume x1`、选择器、`/new` 命名卡片上的“恢复”，或者 Mini App）时，对其他任何人都会让它以自动模式回来，回复里会说明：“♻️ x1 已以自动模式重新打开：完全访问模式仅限管理员。”
- **在自己的私聊里，一个人可以使用完全访问模式，无论角色是什么**（包括 `user`；不包括不发送消息的角色 `read_only`；自建角色需要 `can_prompt` 和 `can_approve`，因为完全访问模式会回答 Claude Code 的每个权限请求）。上面进入完全访问模式的每条路在那里都可用：`/mode full`、模式卡片、`/new !x1`、`/new` 的卡片、`/resume` 和 Mini App。这只适用于这个人自己的私聊：在群组里，以及在别人的私聊里，完全访问模式仍然需要管理员。这个人的新会话和所有人一样以自动模式启动，直到在 `/settings` → 新会话 里选择完全访问模式。管理员能做的其他事情都不会因此开放（`/update`、“安装语音消息功能”按钮和群组的 `/settings` 仍然仅限管理员）。以这种方式给出完全访问模式是安全的：它以 `--dangerously-skip-permissions` 启动 Claude Code，这个参数只跳过 Claude Code 自己的权限请求。aipager 的 PreToolUse hook 仍会在每次工具调用时运行，并执行成员的角色规则（安全底线、文件夹限制、禁止规则、存放登录信息的文件），和在询问模式、自动模式下完全一样。
- **安装软件需要管理员**（UPDATE）：语音消息功能的“安装”和“重启”按钮，和 `/update` 的一样。
- **修改群组的 `/settings` 需要管理员。** 任何人都可以查看。
- **确认卡片属于要求它的人。** 在群组里，“结束”（`/kill`、⋮ 结束会话）、“重启”、“删除”和切换模式的确认卡片，`/kill`、`/restart`、`/delete`、`/mode`、`/stop`、`/rename` 和 `/diff` 显示的会话选择器，以及点按“恢复”后出现的“以哪种模式恢复 x1：”卡片（它的模式按钮），都只回应发送命令或点按产生它们的按钮的那个人。其他任何人的点按，包括“取消”，都会得到“这是 @alice 的卡片。发送 /kill x2 获取你自己的卡片。”（带有那张卡片的命令），不改变任何东西。`/mode` 卡片本身（显示会话当前处于哪种模式）属于所有人：在上面点按切换，会产生一张属于点按者的确认卡片。`/new` 的卡片只回应发送 `/new` 的人。卡片的主人会被记住，直到卡片被使用或取消，即使 aipager 重启也是如此（最新的 4096 张卡片）。`/new` 的命名卡片和 `/rename` 的命名问题是例外：它们反正会在 10 分钟后过期，所以重启之后请重新发送命令。
- **按钮只在它自己会话所在的聊天里有效。** 在其他任何地方点按，它都会回答“这个按钮属于另一个聊天。”，不做任何事；“恢复”总是恢复按钮上写的那个会话，绝不会是另一个聊天里同名的会话。
- **旧按钮不会改变任何东西。** 来自之前某个任务、或者来自会话结束又回来之前的按钮，不会作用于现在正在运行的东西；它会说明这一点（见[命令 → 过时的按钮](commands.md#stale-buttons)）。

## <a id="what-everyone-sees"></a>大家看到什么

**对 Claude 的回答。** 每次允许、拒绝和回答都会发到聊天里，在会话卡片的下方，并写明是谁做的：

```
✅ x1 · 已允许 · @alice · Bash: ls -la /tmp
🚫 x1 · 已拒绝 · @bob · WebFetch: https://example.com
✓ x1 · 已回答 · @carol · Which database? → Postgres
```

**谁修改了会话。** 点按按钮的结果只有点按的人能看到，所以在群组里，每个会改变会话的结果都会写明是谁做的：

```
⚠️ x1 · @bob 停止了任务
⏹ @bob 结束了 x2
🔄 @alice 重启了 [x1]。
🗑️ @carol 删除了 [x3]。
⚙️ @alice 把会话切换到了 🤖 自动模式。
@alice 修改了。          （在 /settings 下）
```

“x1 已就绪”卡片会写明是谁修改了它的模式或模型（“@bob 把模型换成了 Opus。”）。在私聊里，这些结果行不写任何人的名字。在 Mini App 里做的更改会以“已从 Mini App ……”的形式发到聊天里（从那里点的“停止”也会在会话卡片上写明是谁做的）。

**被规则拦下的工具。** 任务会停止，显示“🛑 x1 · 被安全规则阻止：Write 在这个角色的 deny_tools 中（已停止）”。如果 Claude Code 请求使用一个工具，而发送了启动当前任务的消息的人不能使用它，机器人会自动回答“拒绝”，不显示任何请求，并发送 `⛔ x1 · 已对 @bob 阻止 Edit（角色 user）`，下面附上这次调用的摘要，写明是哪个人。由好几个人的消息启动的任务，只要其中任何一个人不能使用这个工具就会被拦下，并写明是谁。在终端里输入的任务是你的，绝不会被成员的规则拦下，除非有一条 Telegram 消息并入了它：这时它会按会话最近一条消息的发送者的规则检查（不知道是谁时，用群组自己的列表）。当 aipager 不知道任务是谁的时候（aipager 重启时它已经在运行），它会使用会话最近一条消息的发送者的规则，连这个也不知道时，就使用群组自己的列表。

**消息状态。** 消息上的 👀 表示它已经发给会话（或者在能发送之前被暂缓）；👍 表示 Claude 已经接收；🤷 表示它在 Claude 接收之前就被丢弃了。`/whoami` 会显示你在这个聊天里的标签、角色和规则。

**置顶状态栏。** 如果机器人是有置顶权限的管理员，群组会有自己的置顶状态栏（见[命令 → 置顶状态栏](commands.md#the-pinned-status-bar)），只列出这个群组的会话。没有这个权限，群组就没有状态栏：消息会被删除，而不是不置顶地留在历史里。

**磁盘上的审计记录。** `~/.claude/aipager-audit.jsonl` 会记录每个决定，带有 `user_id`、`username` 和聊天，让你之后能还原每个人做了什么。无论聊天使用哪种语言，它总是用英文写的。

**语言。** 群组的 aipager 自己的消息、卡片和键盘使用同一种语言，由管理员用 `/language` 设置；在管理员选择之前，这种语言是英文。它从不跟随任何人的 Telegram 应用。只有一个人能看到的东西，也就是点按之后的提示，会用这个人自己的语言显示：这个人在与机器人的私聊里的选择；没有的话，对于在这个版本之后加入的人，是这个人 Telegram 应用的语言；再没有的话，就是群组的语言。只有 aipager 的文字会改变；Claude 用每个人写给它的语言回答。见[语言](commands.md#languages)。

## <a id="where-messages-go"></a>消息发到哪里

- **每个会话属于一个聊天，关于这个会话的一切都发到那个聊天**：它的卡片、回答、权限请求、文件和通知。一个聊天里的消息绝不会发到另一个聊天的会话，会话的名称也只在它自己的聊天里有效：输入另一个聊天的会话名称，会得到“这里没有名为 … 的会话”的回复。在 aipager 运行、但还没有设置任何聊天时启动的会话，会在 `aipager config` 添加聊天后立即得到它的聊天；会话还没有聊天时，没有任何聊天能列出或操作它。
- **在终端里启动的会话**（`aipager session <name>`）属于所有者的私聊：成员拥有 `owner` 角色（有 `bypass_safety` 的角色）的那个私聊。当有好几个私聊、但没有一个是所有者的时，是列表里的第一个私聊；完全没有私聊时，是第一个聊天。在有私聊的安装上，它们绝不会发到群组。
- **升级为超级群组会自动跟上**（见[当 Telegram 升级群组时](#when-telegram-upgrades-the-group)）。

## <a id="aipageryaml-schema-team-parts"></a>`aipager.yaml` 的结构（团队相关部分）

```yaml
schema_version: 3
bot_token: "…"

scopes:
  - kind: dm
    chat_id: 12345       # 你的私聊：它的 ID 就是你的用户 ID
    label: owner DM
    members:
      - id: 12345
        label: alice
        role: owner
  - kind: group
    chat_id: -100123456789
    label: team
    deny_tools: [Write, Edit]   # 可选：对 “Block file edits” 的回答
    root: /home/alice/aipager-users/group-team   # 成员的根文件夹
    members:
      - id: 12345        # Telegram 用户 ID（不是标签，也不是聊天 ID）
        label: alice     # 这个人在聊天里显示的样子（@alice）
        role: owner
      - id: 67890
        label: bob
        role: user
        deny_tools: [WebFetch]  # 可选，针对单个成员
      - id: 11111
        label: charlie
        role: read_only
```

私聊 scope 和群组 scope 可以同时存在：机器人服务列出的每个聊天，每个聊天的会话彼此分开。`aipager config` 会写这个文件；你永远不需要编辑它。

## <a id="how-rules-work"></a>规则如何运作

`~/.config/aipager/policy.yaml` 定义每个角色的规则。用 `aipager policy validate` 检查它。每个角色支持的字段（除了[自建角色](#custom-roles)里的那些）：

- `deny_tools`：不经询问就禁止的工具名称（例如 `Write`、`Edit`、`Bash`、`WebFetch`）。
- `allow_tools`：如果不为空，它就是允许名单：其他一切对那个角色都是禁止的。
- `deny_bash_patterns`：与 `Bash` 输入比对的模式。
- `deny_paths_no_access` / `deny_paths_no_write`：路径规则。对于 `user`、`read_only` 和你定义的每个没有 `bypass_role_denies` 的角色，`deny_paths_no_access` 一开始就包含上面 **user** 里列出的存放登录信息的文件；设置它会替换这份列表，所以请把你想保留的文件复制到你的列表里（并入了其他消息的正在运行的任务，在完成之前会再次受这份列表约束）。不以 `/` 或 `~` 开头的规则（`**/.env`）在任何地方都能匹配，所以有这样的规则时，受限角色的每次 `Grep`/`Glob` 都会被拒绝（任何一次搜索都可能读到这样的文件），而这会让任务停止。

`aipager.yaml` 里聊天的 `deny_tools` 和成员的 `deny_tools` 会加到角色的列表上，成员的 `allow_tools` 会加到角色的允许名单上。这些都只适用于没有 `bypass_role_denies` 的角色：所有者或管理员不受影响。

`~/.config/aipager/policy.d/*.yaml` 里的文件会在 `policy.yaml` 之后、按名称顺序读取，格式相同：后面文件里设置的角色字段会替换之前的值，而 `safety:` 列表只会累加。`aipager policy validate` 会检查所有这些文件。

在所有角色之下，还有一条只有 `owner` 能绕过的**安全底线**（受保护路径和命令模式）：管理员也要遵守。它由 aipager 的内置列表加上 `policy.yaml` 的 `safety:` 部分组成，这一部分只能往上加：

```yaml
safety:
  deny_paths_no_access: [/srv/secrets/**]   # 不能读取、写入或搜索
  deny_paths_no_write: [~/notes/**]         # 可以读取，但不能写入
  deny_bash_patterns: ['\bcurl\b']          # 要拦下的 Bash 命令
```

除 `owner` 之外每个角色的每个任务都会得到整条底线，aipager 不知道是谁的任务也一样；`aipager doctor --safety-check` 会列出当前生效的内容。一次不重启的重新加载之后，其中的更改适用于重新加载之后发送的消息；正在运行的任务，或者正在队列里等待的消息，仍然保留旧的列表（停止那个任务可以让更改立即生效）。那里不以 `/` 或 `~` 开头的 `deny_paths_no_access` 路径在任何地方都能匹配，所以有这样的路径时，除 `owner` 之外每个角色的每次 `Grep`/`Glob` 都会被拒绝（`aipager doctor` 会对此发出警告）。受保护路径对 Claude Code 的文件工具（`Read`、`Write`、`Edit`、`MultiEdit`、`NotebookEdit`、`LSP`、`Grep`、`Glob`）生效；受限角色的 `Grep`/`Glob` 还必须留在会话的文件夹里（在 `~/.config` 里搜索 `aipager/**` 会被拒绝）。像 `WebFetch` 或 `SendFile` 这样的工具不做路径检查。aipager 无法归到任何发送者身上的、来自 Telegram 的任务会得到最低权限，它至少和 `user` 一样严格。“重试”如果是由发送了被重试消息的人点按的，就以点按者的权限运行，否则按最低权限运行。命令模式只对有 Bash 的角色有意义，在那里它们是过滤器而不是墙；见[安全 → 团队模式下的规则执行](security.md#team-mode-enforcement)。

## <a id="privacy-considerations"></a>隐私方面的考虑

- 机器人只监听**配置过的聊天**。把机器人加入另一个群组不会让它在那里启用：它从不在那里回复。它会把在那里呼叫它的人（这个人的 ID、用户名和名字，以及群组的 ID 和标题）记录到 `~/.claude/aipager-pending-users.json`，这样 `aipager config` 就能添加那个群组。这个文件只有你能读，它保留最近 30 天内出现过的最新 200 人。
- `read_only` 成员**可以读到**消息、回答和工具的输入。他们不能操作，但能看到群组里的一切。如果有需要对某人保密的工作，这个人就不应该在群组里。
- 在有私聊的安装上，终端会话和你私聊里的会话绝不会出现在群组里（见[消息发到哪里](#where-messages-go)）。旧版 aipager 移到群组里的终端会话会继续留在群组里：结束它，把它从群组已结束的会话里删除，然后重新开始。
- `aipager.yaml` 的权限是 0600（只有所有者）。aipager 不会为 `policy.yaml` 设置权限：请自己让它保持只有所有者可读写（`chmod 600 ~/.config/aipager/policy.yaml`）。
- 审计日志（`~/.claude/aipager-audit.jsonl`）没有单独的权限：它和你的 `~/.claude` 文件夹一样私密。

## <a id="revoking-a-user"></a>撤销一个人的权限

1. 运行 `aipager config` → Edit a member → 那个聊天 → 那个人 → Remove member。如果要拿走发送消息的权限、但仍让对方能阅读，使用 Set role 并选择 `read_only`。（之后要重新添加某人：Edit a scope → 那个群组 → Add a member。）对于某人的私聊，先结束它的会话，然后 Edit a scope → 那个人的私聊 → Remove this scope。
2. 设置向导会不重启地重新加载 aipager；它会显示 "Scopes reloaded live"。这个人被暂缓的消息会立即被丢弃（🤷 和一条“没有发给 Claude”的回复），其仍在运行的任务在完成之前会被限制在受限的最低权限。发送 `/stop` 可以立即结束那个任务。
3. 也把这个人移出 Telegram 群组。只要还在群组里，对方就仍然能读到那里发出的一切：卡片、回答、工具的输入和权限请求。

注意看设置向导在第 1 步之后说了什么。如果它让你重启 aipager，说明它联系不上 aipager：请重启（`aipager service stop`，然后 `aipager service start`），因为在那之前，旧的成员名单仍然在内存里。如果它显示 "Not applied"，请修复它指出的问题：在那之前，aipager 仍然保留旧的配置。在手动编辑 `aipager.yaml` 之后，请自己发送重新加载的指令（见[不重启地重新加载](#live-reload)）。

## <a id="related-docs"></a>相关文档

- [架构](architecture.md)：进程是如何组织的。
- [机器人命令](commands.md)：界面的说明。
- [安全模型](security.md)：信任边界、威胁列表。
- [故障排除](troubleshooting.md)：`aipager doctor` 的说明。
