# aipager docs

Reference documentation for the daemon, its Telegram interface, and
its security model. These markdown files are the canonical source —
the [aipager.run/docs](https://aipager.run/docs) site renders from
them.

| Doc | What it covers |
|---|---|
| [Install with an agent](install-with-an-agent.md) | The steps a coding agent follows to install and set up aipager for its person |
| [Architecture](architecture.md) | Process model, component diagram, file & socket layout |
| [Hook events](hooks.md) | Every event the daemon ingests from Claude Code, with payload fields |
| [Bot commands](commands.md) | Slash commands, inline buttons, voice / file routing |
| [Team / group mode](groups.md) | Multi-user setup, roles, rules, audit attribution |
| [Troubleshooting](troubleshooting.md) | Common failures and the `aipager doctor` fix table |
| [Problem reports](problem-reports.md) | What a problem report contains, where it goes, and when aipager offers one |
| [Security model](security.md) | Trust boundary, secrets, audit log, network surface |

For getting started, see the top-level [README](../README.md). For
release history, see [CHANGELOG](../CHANGELOG.md).
