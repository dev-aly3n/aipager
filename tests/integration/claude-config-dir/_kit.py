"""Helpers for the black-box tests of "aipager follows where Claude Code
keeps its config" (ship/claude-config-dir).

Written from design.md, design-amendment-1.md, entrypoints.md and
probe-notes.md (Claude Code 2.1.303 behaviour measured in a throwaway
home), never from the implementation. Everything lives under the test's
tmp folder: ``tests/conftest.py`` already points
``claude_paths.DEFAULT_CONFIG_HOME``/``DEFAULT_GLOBAL_DIR`` at a tmp home
and ``MANAGED_SETTINGS_DIR`` at an absent tmp folder, scrubs
``CLAUDE_CONFIG_DIR``/``CLAUDE_CODE_CUSTOM_OAUTH_URL`` and redirects
daemon.env. ``cc.x`` is a config folder OUTSIDE that home, so a write that
still goes to the default home is visible.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


KEY = "sk-ant-api03-" + "B" * 30 + "tail-of-the-key-0042"
TAIL = KEY[-20:]

ONBOARDED = {"hasCompletedOnboarding": True}
LOGGED_IN = {"hasCompletedOnboarding": True,
             "oauthAccount": {"emailAddress": "alice@example.com"}}

#: Credentials and switches a session reads; none may leak in from the
#: machine running the suite.
SCRUB = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
         "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
         "CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
         "CLAUDE_CODE_USE_MANTLE", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_CUSTOM_OAUTH_URL",
         "ANTHROPIC_BASE_URL", "CREDENTIALS_DIRECTORY", "AIPAGER_WORK_DIR")


def wjson(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def rjson(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class CC:
    tmp: Path
    home: Path          # the redirected home folder (~)
    dot_claude: Path    # ~/.claude, the default config home
    managed: Path       # the managed settings folder (absent until written)
    daemon_env: Path    # ~/.config/aipager/daemon.env
    x: Path             # a config folder outside the home
    bin: Path           # fake aipager-hook / aipager-statusline on PATH
    work: Path          # a launch folder

    @property
    def home_json(self) -> Path:
        return self.home / ".claude.json"

    @property
    def home_settings(self) -> Path:
        return self.dot_claude / "settings.json"

    def set_daemon_env(self, **values: str) -> None:
        self.daemon_env.parent.mkdir(parents=True, exist_ok=True)
        self.daemon_env.write_text(
            "".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
        os.chmod(self.daemon_env, 0o600)

    def managed_file(self, data, name: str | None = None) -> Path:
        if name is None:
            return wjson(self.managed / "managed-settings.json", data)
        return wjson(self.managed / "managed-settings.d" / name, data)

    def project(self, data, *, local: bool = False) -> Path:
        name = "settings.local.json" if local else "settings.json"
        return wjson(self.work / ".claude" / name, data)


def wire(path: Path) -> Path:
    """Put aipager's hooks and statusLine into *path* the way
    ``aipager config`` does (public plan/apply API)."""
    from aipager.wizard import settings_patch
    settings_patch.apply_settings(settings_patch.plan_settings(path))
    return path


def has_hooks(path: Path) -> bool:
    from aipager import claude_bootstrap
    return claude_bootstrap.hooks_wired(path)
