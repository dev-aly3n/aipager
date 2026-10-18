# Security policy

aipager lets a Telegram chat drive Claude Code on your machine, so a security bug in it can matter a lot. Thank you for reporting one privately.

## Reporting a vulnerability

Please do not open a public issue, discussion or pull request for a vulnerability.

Report it privately on GitHub instead: open the repository's [Security tab](https://github.com/dev-aly3n/aipager/security) and choose **Report a vulnerability**, or go straight to [the private report form](https://github.com/dev-aly3n/aipager/security/advisories/new). Only you and the maintainer can see the report.

Please include:

- the aipager version (`aipager version`) and the Claude Code version (`claude --version`);
- your system and how aipager is installed (pipx, uv, Homebrew, Docker, ...);
- what an attacker can do, and who the attacker is: someone outside the allowed chats, a member with a given role, another user on the same machine, or a file or web page Claude reads (a prompt injection);
- the steps to reproduce it, as small as you can make them.

Never send your bot token, your Claude credentials or a real chat's content. If the report needs a log line, remove chat ids, paths and prompts from it first.

## What happens next

- You get a first answer within 7 days.
- The maintainer confirms the problem, or explains why it is not one, and agrees with you on when it is made public.
- The fix ships in a release. The [changelog](CHANGELOG.md) lists it under Security, and a GitHub security advisory describes it, crediting you unless you would rather not be named.

## Supported versions

Security fixes go into the newest release only. Update with `aipager update` (or the installer you used) to get them.

| Version | Security fixes |
|---|---|
| The newest release | Yes |
| Anything older | No, please update |

## What to report

What aipager protects, and what it does not, is described in [the security model](docs/security.md), with a [summary of the threats](docs/security.md#threat-model-summary) it is built against. A way around anything described there is worth reporting, a known limit included. For example:

- someone outside the allowed chats can drive a session, or a member gets more than their role allows;
- a member's session reads or writes outside its root folder, or a tool a role blocks runs anyway;
- Claude's text, a file or a message makes aipager send, fetch or show something it should not, such as a token, another chat's uploads or aipager's own settings;
- the Mini App accepts a request that Telegram did not sign.

A problem in Claude Code itself or in Telegram belongs to Anthropic or Telegram. If you are not sure where it belongs, report it here, and the maintainer will help pass it on.
