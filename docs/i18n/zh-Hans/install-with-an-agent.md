# 用代理安装

<!-- i18n-source: docs/install-with-an-agent.md @ d63d62ef -->

> 本页译自英文；如果两者不一致，以英文版为准。（[English](../../install-with-an-agent.md)）

本页写给被要求为用户安装并设置 aipager 的 AI 编程代理（Claude Code，或任何能用 shell 的代理）。请一步一步照做。这里的每条命令都不会询问任何问题，也从不需要交互式终端。

aipager 让用户能在 Telegram 上操作 Claude Code 会话。完成后，用户会有一个只和自己对话的 Telegram 机器人，aipager 也会在这台电脑的后台运行。

## <a id="1-what-only-the-person-can-do"></a>1. 只有用户本人能做的事

请让用户完成这些事。你无法替用户做。

1. **创建机器人**。在 Telegram 里打开 [@BotFather](https://t.me/BotFather)，发送 `/newbot`，选一个名称和用户名。BotFather 会回复机器人的 token（形如 `123456789:AA...`）。
2. **在机器人里点 Start**。打开新机器人（BotFather 给出的链接形如 `t.me/<bot username>`），点 Start。没做过这一步的人，机器人无法给对方发消息。
3. **在这台电脑上登录 Claude Code**。如果用户已经在这里使用 Claude Code，就什么都不用做。在没有屏幕的服务器上，用户在自己的终端里运行一次 `claude` 并登录（它会显示一个在别的设备上打开的链接）。如果用户更想用长期 token，就运行 `claude setup-token`，它只会打印出 token：由用户自己在自己的终端里为 aipager 的会话保存它（粘贴 token，按 Enter，再按 Ctrl-D）：

   ```sh
   mkdir -p ~/.config/aipager
   (umask 077 && { printf 'CLAUDE_CODE_OAUTH_TOKEN='; cat; } >> ~/.config/aipager/daemon.env)
   ```

   aipager 会把这个 token 交给它启动的每个会话（见[安全](security.md#the-claude-credential-what-actually-protects-it)）。你永远不会读取它。

## <a id="2-how-the-person-gives-you-the-token"></a>2. 用户如何把 token 交给你

谁拿到机器人的 token，谁就能控制这个机器人。把下面两种方式告诉用户，让用户自己选。

- **最安全：一个你从不读取的文件**。用户在自己的终端里运行：

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token)
  chmod 600 ~/.config/aipager/bot-token
  ```

  然后粘贴 token，按 Enter，再按 Ctrl-D。只有用户自己能读这个文件（权限 600）。设置成功后可以删掉它（aipager 会在自己的配置里保留一份）。用户把路径告诉你（`~/.config/aipager/bot-token`）。token 永远不会进入你的对话，你把这个路径传给 `--token-file`。
- **最快：直接粘贴到对话里**。这样也行，但要说清楚它意味着什么：token 会留在你保存的对话里，并被发送到你的模型服务商。如果这段对话将来可能被分享，用户之后应该向 @BotFather 发送 `/revoke`，并用 `aipager setup --force` 设置新的 token（第 3.2 步，加上 `--force`）。如果用户选了这种方式，请把 token 保存到同一个文件里，并且不要打印出来：最好用你自己的写文件工具，然后执行 `chmod 600 ~/.config/aipager/bot-token`。如果你只有 shell，就用 here-document 通过 stdin 传入，这样 token 不会成为写文件那条命令的参数：

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token) <<'EOF'
  <the token>
  EOF
  chmod 600 ~/.config/aipager/bot-token
  ```

  然后按下面的方式使用 `--token-file`。

用户的 Telegram 用户 ID（也就是聊天 ID，一个像 `123456789` 的数字）不是机密。如果用户不知道，你可以查出来（第 3.3 步）。

## <a id="3-your-steps"></a>3. 你要做的步骤

### <a id="31-install-aipager"></a>3.1 安装 aipager

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

它会使用 uv 或 pipx 中已有的那个（在 macOS 上也会用 Homebrew），如果都没有，就先安装 uv。你也可以自己运行 `uv tool install aipager` 或 `pipx install aipager`。如果之后找不到 `aipager`，把 `~/.local/bin` 加到 shell 的 `PATH` 里（`export PATH="$HOME/.local/bin:$PATH"`）。

如果 aipager 之前已经装过，这些命令不会升级它。检查 `aipager setup --help` 能不能运行；如果不能，说明装的 aipager 太旧了：运行 `aipager update` 后再检查一次。如果 `aipager update` 说需要重启 aipager，请告诉用户；不要自己重启（见第 4 节）。

不要运行 `aipager config`：那是给人用的交互式设置向导。

### <a id="32-set-aipager-up"></a>3.2 设置 aipager

```sh
aipager setup --token-file ~/.config/aipager/bot-token --chat-id 123456789 --service --json
```

使用用户真实的 ID 和 token 文件的路径。除了 `--token-file`，也可以用 `--token-stdin` 通过管道传入 token（例如 `--token-stdin < path`）；管道必须关闭，setup 最多等它 30 秒。

setup 会向 Telegram 验证 token、检查这台电脑、给用户发一条测试消息（"aipager is set up for you. ..."），然后才写入配置、把 Claude Code 的 hook 加到 `~/.claude/settings.json`（或 `$CLAUDE_CONFIG_DIR/settings.json`），并在带 `--service` 时安装并启动后台服务。任何一项检查失败，都不会写入任何东西。

加上 `--dry-run` 可以先看看会改动什么。`--role admin` 会让用户成为管理员而不是所有者（默认的所有者，适合为自己的电脑做设置的人）。

### <a id="33-if-the-person-does-not-know-their-telegram-id"></a>3.3 如果用户不知道自己的 Telegram ID

```sh
aipager setup detect-chat --token-file ~/.config/aipager/bot-token --json
```

在这条命令等待期间（最多 5 分钟；用 `--timeout SECONDS` 修改），请用户在机器人里点 Start（或给它发任意一条消息）。它会在 `candidate` 里打印最新的发送者（`id`、`first_name`、`last_name`、`username`），不保存任何东西。把名字和用户名给用户看，问一句“这是你吗？”。只有用户确认后，才用 `candidate.id` 作为 `--chat-id` 运行第 3.2 步。如果 `other_candidates` 不是 0，说明还有别人给机器人发过消息：务必仔细确认。

### <a id="34-read-the-result"></a>3.4 读取结果

带 `--json` 时，setup 会在 stdout 上打印且只打印一个 JSON 对象。读取 `exit_code`（与进程退出码相同）、`status`、`error`、`message`、`fix` 和 `next_step`。如果 stdout 不是 JSON（未知参数、`setup` 后面跟了错误的词，或者 aipager 太旧还没有 `setup`），退出码是 2，原因以纯文本写在 stderr 上：读取它，如果是旧版 aipager，参见第 3.1 步。

| 退出码 | `error` | 你要做什么 |
|---|---|---|
| 0 | | `installed`、`updated` 或 `unchanged`：完成，进入第 3.5 步。`dry_run`：什么都没写；去掉 `--dry-run` 再运行一次同样的命令。detect-chat `found`：按第 3.3 步请用户确认，再运行第 3.2 步。 |
| 1 | `config_malformed`、`ambiguous_install`、`settings_invalid`、`test_send_failed`、`write_failed`、`service_failed`、`internal_error` | 把 `message` 和 `fix` 给用户看。`ambiguous_install`：由用户在 `aipager config` 里修复。不要手动编辑文件。要求绝对路径 `CLAUDE_CONFIG_DIR` 的 `settings_invalid`：`fix` 会指出要改的文件（`daemon.env` 或某个 Claude Code 设置文件）；先问对方文件夹，只有对方同意时才只改这一个值，然后重新运行 setup。 |
| 2 | `usage`、`token_malformed`、`token_stdin_timeout`、`bad_chat_id`、... | 你的输入有误；`fix` 会指出是哪个参数。`token_malformed`：文件里不是机器人 token，请用户检查。 |
| 3 | `deps_missing` | 按 `fix` 操作（每个缺失项在 `deps.items` 里也有自己的 `fix`），然后再运行 setup。 |
| 4 | `token_rejected` | Telegram 拒绝了这个 token。再向用户要一次 token（或从 @BotFather 获取一个新的）。 |
| 5 | `chat_not_started`、`bot_blocked` | 请用户打开机器人并点 Start（或解除屏蔽），然后再运行 setup。 |
| 6 | `existing_install` | aipager 已经用不同的 token、聊天或角色设置过了。告诉用户哪里不同，并在加 `--force` 之前先征得同意。 |
| 7 | `telegram_unreachable` | 网络有问题，或 Telegram 正忙。等一分钟再试。 |
| 8 | `detect_timeout` | detect-chat 没看到任何消息。请用户再点一次 Start，然后重新运行。如果有警告 `updates_backlog`，再点 Start 也没用：请向用户要数字 ID。 |
| 9 | `daemon_running` | detect-chat：这个机器人的 aipager 已经在运行，消息都被它接收了。请向用户要数字 ID（或者先问用户，再用 `aipager service stop` 停止它）。 |
| 10 | `updates_conflict` | detect-chat：有另一个程序在读取这个机器人的消息。请向用户要数字 ID。 |
| 130 | `interrupted` | 被 Ctrl-C 中止。 |

如果 `daemon.restart_needed` 为 true，说明 aipager 需要重启才能用上这次改动，或者 setup 无法判断它是否在运行（警告 `daemon_unknown`）。按 `next_step` 操作，它会说明是哪种情况（如果它建议的是会留在前台运行的 `aipager start`，请改用 `aipager service install`）。告诉用户，只有在用户同意时才重启（先 `aipager service stop`，再 `aipager service start`），而且绝不要在用户通过 aipager 操作的会话里重启（见第 4 节）。错误、警告和 JSON 键的完整列表见 [setup 命令参考](commands.md#command-line-aipager-setup-for-coding-agents)。

### <a id="35-check-the-install"></a>3.5 检查安装

```sh
aipager doctor --json
```

它只读取，不发送任何东西。退出码 0 表示没有检查失败。对每个 `status` 为 `fail`（或 `warn`）的检查，把它的 `title`、`detail` 和 `fix` 给用户看。

### <a id="36-report-back-to-the-person"></a>3.6 向用户汇报

用几行话告诉用户：

- 机器人（`@` + `bot_username`），以及测试消息是否已发送（`test_message`）；
- 设置了哪些东西（`changed`，以及 `service.result`）；
- 如何启动 Claude 会话：在 Telegram 里给机器人发送 `/new`，或在终端里运行 `aipager session <name>`；
- `doctor` 标出的任何问题，以及 `next_step`（如果它要求做什么的话）。

## <a id="4-what-you-must-not-do"></a>4. 你绝不能做的事

- 不要打印、`echo` 或 `cat` token，也绝不要把它放在命令行上（`--token` 反正会被拒绝）。只能通过文件或 stdin 传递。
- 不要手动编辑 `~/.config/aipager/`（第 2 步里的 token 文件除外）或 `~/.claude/settings.json`。它们由 `aipager setup` 和 `aipager config` 写入。唯一的例外是在比这个版本更旧的 aipager 上添加群组，这时 `aipager setup group --help` 会失败：见 [5.5](#55-older-installs)。
- 不要在没问过用户的情况下加 `--force`：它会替换用户现有的 token、聊天或角色。用在 `setup group` 上时，它会修改或去掉角色，或者授予 `owner` 角色。
- 通过 aipager，只有所有者的任务能运行 `aipager setup`（任何形式）、`aipager config` 或 `aipager uninstall`：aipager 的安全规则会对其他所有角色拦下这些命令。如果用户通过 aipager 和你交流、但不是所有者，请让对方自己在终端里运行这个命令。
- 不要重启或结束你自己所在的 Claude Code 会话。首次安装时，你所在的会话不在 aipager 之下，也不需要在：新会话来自 Telegram 里的 `/new` 或 `aipager session <name>`。如果 aipager 已经设置好，而用户正通过它与你交流（例如为了用新 token 运行 `setup --force`），不要自己停止或重启 aipager：你的任务和它的回答会被切断。等你完成后，请用户自己重启它。

## <a id="5-groups"></a>5. 群组

`aipager setup` 设置的是用户与机器人的私聊。`aipager setup group` 会把一个 Telegram 群组、它的成员和成员的角色添加到已经设置好的安装里，不提任何问题（`aipager config` 做同样的事，但会提问）。它从安装里读取机器人 token，从不重启 aipager，也不往群组里发任何东西。见[团队模式与群组](groups.md)和[这个命令的说明](commands.md#aipager-setup-group)。

### <a id="51-what-only-the-person-can-do"></a>5.1 只有用户能做的事

这些步骤你做不了；请让用户在 Telegram 里完成：

1. 把机器人加进群组。
2. 把机器人设为群组管理员，并给它置顶消息的权限（aipager 会在那里置顶一个状态栏；没有这个权限群组也能用，只是没有状态栏）。只有群组管理员能做这件事。
3. 在群组里发送 `/start`，这样你才能找到群组的 ID（5.2）。
4. 告诉你每个成员的数字 Telegram 用户 ID、一个简短的名字（aipager 显示这个人时用的名字，形如 `@label`）和一个角色：`owner`、`admin`、`user`、`read_only`，或 `policy.yaml` 里自己定义的角色。`owner` 角色能完全控制这台电脑：它属于运行这台电脑的人，不属于别人。如果用户不知道某个 ID，可以在那个人往群组里发消息时用 `aipager config` 找出来。

### <a id="52-find-the-group"></a>5.2 找到群组

```sh
aipager setup detect-group --json
```

它会等待（最多 300 秒，`--timeout SECONDS`）一个还没设置的群组里的消息，这条消息要在你运行命令前不超过 10 分钟发出，然后打印这个群组（`candidate.id`、`candidate.title`）。它不保存任何东西。把群组名称给用户看，等用户确认是对的群组后再用这个 ID。退出码 8 表示什么都没收到：请用户在群组里再发一次 `/start`。

### <a id="53-set-it-up"></a>5.3 设置群组

先加 `--dry-run` 运行，再去掉它运行同一个命令：

```sh
aipager setup group --chat-id -1001234567890 \
  --member 111111111:alice:owner --member 222222222:bob:user \
  --label team --dry-run --json
```

- 每个人用一次 `--member ID:LABEL:ROLE`。以后要加人，只带新的 `--member` 再运行一次：其他人都会保留。
- `--remove-member ID` 移除一个人。
- `--label NAME` 给群组起名，`--root PATH` 设置群组里 `user` 和 `read_only` 成员工作的文件夹（默认是 `~/aipager-users/group-<label>`），`--block-edits` 为群组的 `user` 成员拦下 Write 和 Edit。
- 再运行同一个命令不会改变任何东西（`unchanged`）。
- 退出码 6（`needs_force`）表示这个改动需要 `--force`：`force_reasons` 说明原因（角色改动、授予、降级或移除所有者、有风险的根文件夹）。把它给用户看，只有用户同意时才加 `--force`。

### <a id="54-read-the-result"></a>5.4 读取结果

| 退出码 | 怎么做 |
|---|---|
| 0 | 完成（`created`、`updated`、`unchanged` 或 `dry_run`）。告诉用户群组的 `label`、它的 `members`，以及 `daemon.outcome` 怎么说。 |
| 2 | 某个选项有误：`error`、`message` 和 `fix` 会指出是哪个。改正后再运行。 |
| 6 | `needs_force`：就 `force_reasons` 里的每一项询问用户。 |
| 11 | `not_set_up`：先设置好 aipager（[3](#3-your-steps)）。 |
| 1 | 其他情况（`config_malformed`、`policy_malformed`、`last_member`、`config_changed`……）：把 `message` 和 `fix` 给用户看。遇到 `config_changed` 时再运行一次。 |

`daemon.outcome` 说明正在运行的 aipager 做了什么：`reloaded_live`（群组现在就能用），`no_daemon`（aipager 启动后就能用），`restart_needed` 或 `not_applied`（把 `next_step` 给用户看；不要自己重启 aipager）。警告从不改变退出码，但要给用户看：`bot_not_in_group`、`bot_not_admin`、`bot_cannot_pin` 和 `group_not_found` 表示用户还有 5.1 里的某一步没做，`owner_granted` 也值得明确说出来。

### <a id="55-older-installs"></a>5.5 较旧的安装

如果 `aipager setup group --help` 失败，说明这个 aipager 比这个命令旧。请用户更新 aipager（`aipager update`），或者，如果用户现在就要这个群组，就手动添加：

1. 按照[文件结构](groups.md#aipageryaml-schema-team-parts)，在 `~/.config/aipager/aipager.yaml` 里加一个群组 scope。这个文件里有机器人 token：绝不要打印它或对它用 `cat`，并保持权限为 0600（`chmod 600 ~/.config/aipager/aipager.yaml`）。
2. 用 `aipager policy validate` 检查它，再确认 `aipager doctor` 的 `scope mode: ...` 这一行算上了新群组。
3. 按下面的方法让 aipager 不重启地重新加载。

在 Linux 上，使用后台服务时：

```sh
pid=$(systemctl --user show -p MainPID --value aipager.service)
[ "${pid:-0}" -gt 0 ] && kill -USR1 "$pid"
```

在 macOS 上：

```sh
pid=$(launchctl list | awk '$3 == "com.aipager.daemon" {print $1}')
[ "${pid:-0}" -gt 0 ] 2>/dev/null && kill -USR1 "$pid"
```

`-gt 0` 这个检查很重要：没有 aipager 在运行时，`kill -USR1 0` 会把信号发给你自己 shell 里的进程。绝不要在 aipager 会话里重启 aipager：你的任务会被打断。

## <a id="see-also"></a>另见

- [机器人命令和 setup 命令](commands.md)
- [故障排除](troubleshooting.md)
- [安全模型](security.md)
