# 观察机器人

<!-- i18n-source: docs/observers.md @ b12e978 -->

> 本页译自英文；如果两者不一致，以英文版为准。（[English](../../observers.md)）

只读的 Telegram 机器人，接收主机器人通知的副本。它们会收到摘要、警告和错误，但不能控制任何会话。

## <a id="setup"></a>设置

1. 通过 [@BotFather](https://t.me/BotFather) 创建一个机器人
2. 和这个机器人开一个聊天，发送 `/start`
3. 找到你的聊天 ID（发一条消息，然后打开 `https://api.telegram.org/bot<TOKEN>/getUpdates`）
4. 在 aipager 的环境里设置 `OBSERVER_BOTS`。如果 aipager 在前台运行，就在 `aipager start` 之前 export 它；对于 systemd 服务：

```sh
systemctl --user edit aipager    # 在 [Service] 下添加：
# Environment=OBSERVER_BOTS=<bot_token>:<chat_id>
```

多个观察机器人（用逗号分隔）：

```
OBSERVER_BOTS=111:AAA_first:12345,222:BBB_second:67890
```

格式是 `token:chat_id`：解析时以**最后一个**冒号作为分隔符（机器人 token 本身就含有一个冒号）。

5. 重启 aipager

## <a id="what-observers-receive"></a>观察机器人会收到什么

观察机器人会收到来自**机器人服务的每个聊天**的这些通知，包括每个私聊和每个群组，没有任何过滤：只为那些你在所有这些内容上都信任的聊天设置观察机器人。每条通知都使用它所来自的聊天的语言。

| 事件 | 示例 |
|-------|---------|
| 一个任务完成 | `💬 x1 · 已完成（2:05，+12 -3）`：只有结果行，没有回答；当回答或卡片被截断时，还会附上作为 `.md` 文件的完整日志 |
| 一份后台工作完成 | `✅ x1 · 已完成（...）`，或者在某个代理消失、或一直没看到后台命令结束时为 `⚠️ x1 · 已完成（...）` |
| API 错误 | `⚠️ x1 · Anthropic 的服务器负载过高。请稍后再试。`（没有重试按钮） |
| 上下文警告 | `⚠️ x1 · 上下文已用 82%，即将自动压缩` |
| 正在压缩 | `🔄 x1 · 正在压缩对话` |
| 压缩完成 | `📦 x1 · 对话已压缩：82% → 4%` |
| 仍在等待的请求 | `⬆️ x1 · 仍在等你回复上面的内容`，附上它所等待的权限请求或问题 |
| 正在工作但没有动静的会话 | `⏳ x1 · 仍在工作（120 分钟没有消息）` |
| Claude Code 没有取走的消息 | `⚠️ x1 · Claude Code 没有收到消息` |
| 会话结束 | `🔴 x1 · 会话已关闭` |

## <a id="what-observers-dont-receive"></a>观察机器人不会收到什么

- 工作卡片的动画
- 工具调用的更新
- 权限请求及其按钮（只有上面那条“仍在等待”的提醒会说明请求在问什么）
- AskUserQuestion 对话框
- 任何键盘或按钮

观察机器人完全不保存状态：消息发出后不等待回应。出故障的观察机器人绝不会影响主机器人。
