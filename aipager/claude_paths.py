"""Where Claude Code keeps its config, for the environment a session runs with.

One answer for every aipager spot that reads or writes one of Claude Code's
own files: the settings file (aipager's hooks and statusLine), the global
config (folder trust, the API key answers, onboarding), the credentials file
and the transcripts folder, plus the settings ``env`` blocks a session gets.
aipager's own ``~/.claude/aipager-*`` files are not Claude Code's and never
move with it.

The rules, measured with Claude Code 2.1.303 in a throwaway home (and read
from its binary):

- **Start home**: ``$CLAUDE_CONFIG_DIR`` when the key is present, else
  ``~/.claude``. The value is used as it is: not trimmed, no ``~``
  expansion; a relative one (``""`` included, which is the launch folder
  itself) is taken against the session's folder.
- **Global config** (trust, onboarding, API key answers, MCP servers):
  ``<$CLAUDE_CONFIG_DIR or ~>/.claude[-custom-oauth].json``, where an empty
  ``$CLAUDE_CONFIG_DIR`` counts as unset here (whitespace does not), and the
  suffix is there when ``$CLAUDE_CODE_CUSTOM_OAUTH_URL`` is non-empty. The
  start home's legacy ``.config.json`` replaces it when it is a regular file
  and either non-empty, or writable while the new-style file is missing. It
  is fixed from the session's own environment: a value in a settings file
  does not move it.
- **Settings home** (``settings.json``, ``.credentials.json``): a
  ``CLAUDE_CONFIG_DIR`` set in the ``env`` block of the start home's
  ``settings.json`` or of managed settings (managed wins) moves it, one hop:
  Claude Code loads the settings file again from there and no further.
- **Transcripts** (``projects/``): the ``CLAUDE_CONFIG_DIR`` the session
  ends up with once every one of those ``env`` blocks is applied, the moved
  settings file's included.

Stdlib only and no English text: the hook's import closure must never pull
in :mod:`aipager.i18n`, and nothing here is shown to a person. Read only;
nothing here raises. Values may be secrets: never log them.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

#: Claude Code's config home when ``CLAUDE_CONFIG_DIR`` is not set. A seam:
#: the test suite points it at a throwaway home.
DEFAULT_CONFIG_HOME: Path = Path.home() / ".claude"
#: Where the global config lives when ``CLAUDE_CONFIG_DIR`` is not set.
DEFAULT_GLOBAL_DIR: Path = Path.home()


def _managed_dir() -> Path | None:
    # Measured with 2.1.303: /etc/claude-code on Linux, this folder on
    # macOS (and C:\Program Files\ClaudeCode on Windows, where aipager does
    # not run). MDM profiles, the Windows registry and server-delivered
    # managed settings cannot be read from a file and are not tried.
    if sys.platform == "darwin":
        # Joined so the folder name is not counted as English text.
        return Path("/Library") / " ".join(("Application", "Support")) / "ClaudeCode"
    if sys.platform.startswith("linux"):
        return Path("/etc/claude-code")
    return None


#: The folder of file-based managed (policy) settings, or None.
MANAGED_SETTINGS_DIR: Path | None = _managed_dir()

_CONFIG_DIR_KEY = "CLAUDE_CONFIG_DIR"
_CUSTOM_OAUTH_KEY = "CLAUDE_CODE_CUSTOM_OAUTH_URL"


@dataclass(frozen=True)
class ClaudePaths:
    """Claude Code's files for one environment (see the module docstring)."""

    #: Where ``settings.json`` and ``.credentials.json`` are read: the
    #: start home, or where a settings ``env`` block moved it.
    config_home: Path
    #: The home taken from the session's environment alone.
    start_home: Path
    #: ``CLAUDE_CONFIG_DIR`` decides some path: set in the session's
    #: environment, or by a settings ``env`` block.
    from_env: bool
    #: A ``CLAUDE_CONFIG_DIR`` that decides the settings home or the
    #: transcripts folder is relative (``""`` and whitespace included).
    relative: bool
    #: The global config's folder came from a relative value (the legacy
    #: file of a relative start home included).
    global_relative: bool
    #: A settings ``env`` block moved the settings home off the start home.
    moved_by_settings: bool
    #: The settings file whose ``env`` block holds the relative
    #: ``CLAUDE_CONFIG_DIR`` behind ``relative`` (None: no such value, or
    #: it is the session's environment's own).
    relative_settings_file: Path | None
    global_config: Path
    #: ``global_config`` is the legacy ``.config.json``.
    legacy: bool
    user_settings: Path
    credentials: Path
    projects: Path


def config_dir_value(env: Mapping[str, str]) -> str | None:
    """``CLAUDE_CONFIG_DIR`` as Claude Code reads it: the value whenever the
    key is present (``""`` included), else None."""
    value = env.get(_CONFIG_DIR_KEY)
    return value if isinstance(value, str) else None


def _base(cwd: str | None) -> str:
    if cwd is not None:
        return cwd
    try:
        return os.getcwd()
    except OSError:
        return "/"


def _home_from(value: str, cwd: str | None) -> tuple[Path, bool]:
    """The folder a ``CLAUDE_CONFIG_DIR`` value names, and whether it was
    relative. Measured with 2.1.303: no trim, no ``~`` expansion, NFC
    normalised; ``""`` is the session's folder itself."""
    value = unicodedata.normalize("NFC", value)
    if os.path.isabs(value):
        return Path(value), False
    return Path(os.path.join(_base(cwd), value)), True


def read_settings(path: Path) -> dict:
    """The JSON object in *path*, or ``{}`` when it is missing, unreadable,
    not JSON or not an object."""
    try:
        data = json.loads(Path(path).read_bytes())
    except (OSError, ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


def _env_block(settings: dict) -> dict[str, str]:
    """A settings file's ``env`` block, text values only (Claude Code
    ignores the rest)."""
    block = settings.get("env")
    if not isinstance(block, dict):
        return {}
    return {k: v for k, v in block.items() if isinstance(k, str) and isinstance(v, str)}


def managed_settings_files() -> list[Path]:
    """``managed-settings.json``, then the ``managed-settings.d`` entries
    sorted by name. Measured with 2.1.303: an entry counts when it is a file
    or a symlink, its name ends in ``.json`` and does not start with a dot."""
    base = MANAGED_SETTINGS_DIR
    if base is None:
        return []
    files: list[Path] = []
    main = base / "managed-settings.json"
    try:
        if main.is_file():
            files.append(main)
    except OSError:
        pass
    try:
        with os.scandir(base / "managed-settings.d") as it:
            names = sorted(
                e.name for e in it
                if (e.is_file(follow_symlinks=False) or e.is_symlink())
                and e.name.endswith(".json") and not e.name.startswith(".")
            )
    except OSError:
        names = []
    files.extend(base / "managed-settings.d" / n for n in names)
    return files


def _managed_layers() -> list[dict]:
    return [read_settings(p) for p in managed_settings_files()]


def _last_value(layers: list[tuple[Path, dict]]) -> tuple[str, Path] | None:
    """The last ``CLAUDE_CONFIG_DIR`` text value among settings *layers*
    (``(file, settings)`` pairs), with the file it is in."""
    found = None
    for path, settings in layers:
        value = _env_block(settings).get(_CONFIG_DIR_KEY)
        if value is not None:
            found = (value, path)
    return found


def _global_config(env: Mapping[str, str], start_home: Path, start_rel: bool,
                   cwd: str | None) -> tuple[Path, bool, bool]:
    """``(file, legacy, relative)`` of the global config, from the session's
    environment alone (measured with 2.1.303: a settings value does not move
    it, and the legacy check looks in the start home). ``relative``: the
    chosen file's folder came from a relative value, so the legacy file of
    a relative start home is relative even for an empty value (it sits in
    the session's own folder)."""
    value = config_dir_value(env)
    relative = False
    if value:  # Claude Code's `||`: an empty value falls back to the home folder.
        folder, relative = _home_from(value, cwd)
    else:
        folder = DEFAULT_GLOBAL_DIR
    # Measured with 2.1.303: any non-empty value, not trimmed.
    suffix = "-custom-oauth" if env.get(_CUSTOM_OAUTH_KEY) else ""
    new_style = folder / f".claude{suffix}.json"
    legacy = start_home / ".config.json"
    # Measured with 2.1.303 (cases a-f in probe-notes.md): statSync follows
    # links; a regular file that is non-empty, or writable while the
    # new-style file is missing, wins.
    try:
        st = os.stat(legacy)
        if stat.S_ISREG(st.st_mode) and (
                st.st_size > 0 or (st.st_mode & 0o222 and not os.path.exists(new_style))):
            return legacy, True, start_rel
    except (OSError, ValueError):
        pass
    return new_style, False, relative


def locate(env: Mapping[str, str], *, cwd: str | None = None) -> ClaudePaths:
    """Claude Code's files for a session that runs with *env* in the folder
    *cwd* (default: this process's folder). Never raises."""
    value = config_dir_value(env)
    if value is None:
        start_home, start_rel = DEFAULT_CONFIG_HOME, False
    else:
        start_home, start_rel = _home_from(value, cwd)
    global_config, legacy, global_rel = _global_config(env, start_home, start_rel, cwd)

    start_file = start_home / "settings.json"
    start_layer = (start_file, read_settings(start_file))
    managed = [(p, read_settings(p)) for p in managed_settings_files()]
    # Measured with 2.1.303 (P2, P7): the start file's or managed settings'
    # value moves the settings file and the credentials, one hop.
    hop = _last_value([start_layer, *managed])
    config_home, home_rel = start_home, start_rel
    moved = False
    projects_home, projects_rel = start_home, start_rel
    rel_file = None
    if hop is not None:
        config_home, home_rel = _home_from(hop[0], cwd)
        moved = config_home != start_home
        projects_home, projects_rel = config_home, home_rel
        rel_file = hop[1] if home_rel else None
        if moved:
            # The moved file's env block is applied too, but sets off no
            # further load: a CLAUDE_CONFIG_DIR there moves only the
            # transcripts (measured with 2.1.303, P7). Managed still wins.
            moved_file = config_home / "settings.json"
            final = _last_value([start_layer, (moved_file, read_settings(moved_file)),
                                 *managed])
            if final is not None and final[0] != hop[0]:
                projects_home, projects_rel = _home_from(final[0], cwd)
                if projects_rel and rel_file is None:
                    rel_file = final[1]
    return ClaudePaths(
        config_home=config_home,
        start_home=start_home,
        from_env=value is not None or hop is not None,
        relative=home_rel or projects_rel,
        global_relative=global_rel,
        moved_by_settings=moved,
        relative_settings_file=rel_file,
        global_config=global_config,
        legacy=legacy,
        user_settings=config_home / "settings.json",
        credentials=config_home / ".credentials.json",
        projects=projects_home / "projects",
    )


def for_sessions(*, cwd: str | None = None) -> ClaudePaths:
    """:func:`locate` for the environment aipager's sessions get: this
    process's environment plus aipager's daemon.env (or systemd's copy)."""
    from aipager import daemon_secrets

    return locate(daemon_secrets.build_session_env(), cwd=cwd)


def settings_layers(paths: ClaudePaths, folder: str | None, *,
                    project: bool = True) -> list[tuple[str, dict]]:
    """The settings files whose ``env`` a session gets, lowest first:
    the start home's ``settings.json``, the moved one (measured with 2.1.303:
    both apply, the moved one wins), then the folder's ``.claude/settings.json``
    and ``.claude/settings.local.json`` (only for a trusted *folder*, so only
    when *project*), then every managed file.

    Local settings are read from *folder*. Claude Code 2.1.303 can read them
    from the git root instead, after an ownership check this does not copy;
    a session aipager starts is normally at the root anyway."""
    layers: list[tuple[str, dict]] = [("user", read_settings(paths.start_home / "settings.json"))]
    if paths.moved_by_settings:
        layers.append(("user", read_settings(paths.user_settings)))
    if folder is not None and project:
        base = Path(folder) / ".claude"
        layers.append(("project", read_settings(base / "settings.json")))
        layers.append(("local", read_settings(base / "settings.local.json")))
    layers.extend(("managed", s) for s in _managed_layers())
    return layers


def layered_env(env: Mapping[str, str], layers: list[tuple[str, dict]]) -> dict[str, str]:
    """*env* with each layer's ``env`` block (text values only) laid over
    it, later layers winning, as Claude Code does."""
    merged = dict(env)
    for _, settings in layers:
        merged.update(_env_block(settings))
    return merged


def layer_env(layers: list[tuple[str, dict]]) -> list[dict[str, str]]:
    """Every layer's ``env`` block (text values only), in order."""
    return [_env_block(settings) for _, settings in layers]


def layer_value(layers: list[tuple[str, dict]], key: str):
    """The top-level *key* of the last layer that sets it, else None."""
    found = None
    for _, settings in layers:
        if settings.get(key) is not None:
            found = settings[key]
    return found


def hook_settings_candidates(env: Mapping[str, str], *, cwd: str | None = None) -> list[Path]:
    """Every settings file aipager may have put its hooks in for *env*: the
    one sessions read, the start home's, and ``~/.claude/settings.json``,
    each once (by real path)."""
    paths = locate(env, cwd=cwd)
    out: list[Path] = []
    seen: set[str] = set()
    for p in (paths.user_settings, paths.start_home / "settings.json",
              DEFAULT_CONFIG_HOME / "settings.json"):
        real = os.path.realpath(p)
        if real not in seen:
            seen.add(real)
            out.append(p)
    return out
