"""What does not move, what the resolver may import, and the Docker image
(brief "What to build"; design success criteria 11-12, "aipager's OWN
files stay where they are").

The subprocess checks run a fresh interpreter with a throwaway HOME, so
module-level ``Path.home()`` constants are computed there and never from
the real home.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from aipager import claude_paths

REPO = Path(__file__).resolve().parents[3]
CCD = "CLAUDE_CONFIG_DIR"


def _fresh_python(cc, code: str, *, config_dir: str | None) -> str:
    home = cc.tmp / "fresh-home"
    home.mkdir(exist_ok=True)
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO),
           "LANG": "C.UTF-8"}
    if config_dir is not None:
        env[CCD] = config_dir
    r = subprocess.run([sys.executable, "-I", "-c",
                        f"import sys; sys.path.insert(0, {str(REPO)!r})\n" + code],
                       capture_output=True, text=True, timeout=60, env=env,
                       cwd=str(cc.work))
    assert r.returncode == 0, r.stderr[-2000:]
    return r.stdout


# ---- the resolver imports nothing of aipager at module level ----------------------------

def test_the_resolver_pulls_in_no_other_aipager_module(cc):
    out = _fresh_python(cc, (
        "import json, aipager.claude_paths\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('aipager'))))"),
        config_dir=None)
    assert set(json.loads(out)) <= {"aipager", "aipager.claude_paths"}


def test_the_resolver_never_loads_i18n(cc):
    out = _fresh_python(cc, (
        "import aipager.claude_paths\n"
        "print(any(m.startswith('aipager.i18n') for m in sys.modules))"),
        config_dir=None)
    assert out.strip() == "False"


# ---- aipager's own files stay in ~/.claude --------------------------------------------

OWN_FILES = {
    "sessions": "from aipager import config; print(config.SESSION_STATE_FILE)",
    "flood-state": "from aipager import config; print(config.FLOOD_STATE_FILE)",
    "tunnel": "from aipager import config; print(config.TUNNEL_STATE_FILE)",
    "audit": "from aipager import audit; print(audit.AUDIT_LOG_PATH)",
    "pending-users": "from aipager import team; print(team.PENDING_USERS_PATH)",
}


@pytest.mark.parametrize("which", list(OWN_FILES), ids=list(OWN_FILES))
def test_aipagers_own_file_stays_in_the_home_claude_folder(cc, which):
    elsewhere = cc.tmp / "elsewhere" / "cfg"
    out = _fresh_python(cc, OWN_FILES[which], config_dir=str(elsewhere))
    assert Path(out.strip()).parent == cc.tmp / "fresh-home" / ".claude"


def test_the_resolvers_default_home_is_the_home_claude_folder(cc):
    out = _fresh_python(cc, (
        "from aipager import claude_paths; print(claude_paths.DEFAULT_CONFIG_HOME)"),
        config_dir=None)
    assert Path(out.strip()) == cc.tmp / "fresh-home" / ".claude"


def test_a_process_config_dir_reaches_a_fresh_resolver(cc):
    elsewhere = cc.tmp / "elsewhere" / "cfg"
    out = _fresh_python(cc, (
        "from aipager import claude_paths; "
        "print(claude_paths.locate(__import__('os').environ).user_settings)"),
        config_dir=str(elsewhere))
    assert Path(out.strip()) == elsewhere / "settings.json"


# ---- Docker ---------------------------------------------------------------------------

def _dockerfile() -> str:
    return (REPO / "Dockerfile").read_text(encoding="utf-8")


def _env_value(text: str, key: str) -> str | None:
    for line in text.splitlines():
        m = re.match(r"\s*ENV\s+(.*)$", line)
        if not m:
            continue
        for part in m.group(1).split():
            k, _, v = part.partition("=")
            if k == key:
                return v.strip('"')
    return None


def _volumes(text: str) -> list[str]:
    vols: list[str] = []
    for line in text.splitlines():
        m = re.match(r"\s*VOLUME\s+(\[.*\])\s*$", line)
        if m:
            vols += json.loads(m.group(1))
    return vols


def test_the_image_sets_claude_config_dir():
    assert _env_value(_dockerfile(), CCD) == "/home/aipager/.claude"


def test_the_image_keeps_the_claude_folder_on_a_volume():
    assert "/home/aipager/.claude" in _volumes(_dockerfile())


def test_the_images_global_config_lives_on_a_volume():
    """The point of the ENV: .claude.json moves into the volume."""
    value = _env_value(_dockerfile(), CCD)
    if value is None:
        pytest.fail("no ENV CLAUDE_CONFIG_DIR in the Dockerfile")
    paths = claude_paths.locate({CCD: value}, cwd="/home/aipager")
    assert any(str(paths.global_config).startswith(v.rstrip("/") + "/")
               for v in _volumes(_dockerfile()))


def test_the_images_settings_and_credentials_stay_on_the_same_volume():
    value = _env_value(_dockerfile(), CCD) or ""
    paths = claude_paths.locate({CCD: value}, cwd="/home/aipager")
    assert paths.credentials == Path("/home/aipager/.claude/.credentials.json")


def test_the_env_is_set_for_the_aipager_user():
    """The ENV is in the final stage (the one that runs aipager)."""
    text = _dockerfile()
    final = text[text.rindex("\nFROM "):]
    assert _env_value(final, CCD) == "/home/aipager/.claude"

