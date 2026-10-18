<!-- Thanks for the pull request. For a security fix, please report the problem privately first (see SECURITY.md). -->

## What and why

<!-- What this changes, and the problem it solves. Link the issue if there is one, e.g. "Fixes #123". -->

## How it was checked

<!-- The tests you added or ran, and anything you tried by hand in Telegram (which app). -->

## Checklist

- [ ] The tests pass under a memory cap (see CONTRIBUTING.md), and `ruff check aipager tests` is clean.
- [ ] Every new guard has a test that fails when the guard is removed.
- [ ] A change people will notice has a line in `CHANGELOG.md` under `[Unreleased]`, and `docs/` says the same.
- [ ] Text people read in Telegram or the Mini App goes through `aipager.i18n`, and `python tools/i18n.py extract` was run.
- [ ] No tokens, chat ids, real names or personal paths in code, tests, docs or screenshots.
