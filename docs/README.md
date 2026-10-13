# aipager docs

Reference documentation for the daemon, its Telegram interface, and
its security model. These markdown files are the canonical source -
the [aipager.run/docs](https://aipager.run/docs) site renders from
them.

**Read these docs in:** [Español](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/es) · [Русский](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/ru) · [Português (Brasil)](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/pt-BR) · [简体中文](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/zh-Hans) · [Français](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/fr) · [Deutsch](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/de) · [한국어](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/ko) · [العربية](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/ar) · [Bahasa Indonesia](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/id) · [Українська](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/uk) · [Italiano](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/it) · [Türkçe](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/tr) · [فارسی](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/fa) · [Tiếng Việt](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/vi). Each translation starts from the README
and links to the same pages in its language. The English pages are the
reference: when a translation and the English page disagree, the
English page is right.

| Doc | What it covers |
|---|---|
| [Install with an agent](install-with-an-agent.md) | The steps a coding agent follows to install and set up aipager for its person |
| [Architecture](architecture.md) | Process model, component diagram, file & socket layout |
| [Hook events](hooks.md) | Every event the daemon ingests from Claude Code, with payload fields |
| [Bot commands](commands.md) | Slash commands, buttons, the Mini App, voice / file routing, every `aipager` command and the settings read from the environment |
| [Team / group mode](groups.md) | Multi-user setup, roles, rules, audit attribution |
| [Troubleshooting](troubleshooting.md) | Common failures and the `aipager doctor` fix table |
| [Problem reports](problem-reports.md) | What a problem report contains, where it goes, and when aipager offers one |
| [Security model](security.md) | Trust boundary, secrets, audit log, network surface |

For getting started, see the top-level [README](../README.md). For
release history, see [CHANGELOG](../CHANGELOG.md).
