"""Which installer owns THIS running aipager, and how to drive it.

Pure stdlib, no network, no subprocess: cheap enough for the CLI, ``doctor``
and the Mini App's repair hint. The update core (:mod:`aipager.self_update`)
and ``aipager update`` / ``uninstall`` build every installer command from
the ONE table below instead of the four parallel maps that used to live in
``updater.py`` and ``miniapp/server.py``.

Detection reads the running interpreter (``sys.prefix``) and this
interpreter's own ``aipager`` distribution metadata. The old approach asked
every installer on ``$PATH`` whether it listed a package whose name
*contained* "aipager", which failed two ways: off-PATH (``~/.local/bin`` is
missing from a non-interactive ssh shell and from pre-0.5 systemd units),
and by picking whichever installer answered first rather than the one that
owns the process actually running.

Every installer binary is resolved to an absolute path over
:func:`augmented_path`, so ``uv``/``pipx`` sitting in ``~/.local/bin`` are
found even when the caller's PATH lacks that directory.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

# Module constant so tests can point it at a tmp file.
_DOCKERENV = "/.dockerenv"

# Map each optional extra to the concrete packages pip needs when the
# installer-aware path isn't available (brew formulas don't expose pip
# extras; editable / unknown installs have no installer to go through).
# Keep in sync with [project.optional-dependencies] in pyproject.toml.
_EXTRA_PACKAGES: dict[str, list[str]] = {
    "voice": ["faster-whisper>=1.0"],
}

# The same Homebrew prefixes claude_resolve searches.
_HOMEBREW_BINS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/home/linuxbrew/.linuxbrew/bin",
)

#: Kinds this module can upgrade in place.
UPGRADABLE_KINDS = ("pipx", "uv", "brew", "pip")

#: Environment variables no installer, ``claude update`` or ``systemctl``
#: call needs. Stripped from every child's environment.
SECRET_ENV_KEYS = (
    "CLAUDE_TG_BOT_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
)

_REFUSAL_REASONS = {
    "editable": "this is an editable (development) install; update it with git",
    "nix": "this install lives in the Nix store; update it through Nix",
    "snap": "this is a Snap install; update it with snap",
    "docker": "aipager is running inside a container; rebuild the image instead",
    "system": ("this install is in the system Python and is managed by your "
               "OS package manager"),
    "unknown": "could not tell which installer owns this aipager",
}

_LOCAL_FOLDER_REASON = "it was installed from a local folder; update it from that folder"

_UNSET = object()


@dataclass(frozen=True)
class InstallSource:
    """What owns the running aipager, and whether we may upgrade it."""

    kind: str               # pipx|uv|brew|pip|editable|nix|snap|docker|system|foreign|unknown
    prefix: str
    python: str
    origin: str = "unknown"  # index|local|vcs|unknown
    origin_detail: str | None = None
    upgradable: bool = False
    reason: str | None = None
    # For ``foreign``: the installer kind the prefix otherwise matched.
    owner_kind: str | None = None

    def describe(self, show_paths: bool = True) -> str:
        """Human text, e.g. ``"pipx, from local path /home/me/aipager"``.

        ``show_paths=False`` (group chats) never prints a filesystem path.
        """
        if self.kind == "foreign":
            base = f"{self.owner_kind or 'unknown'} install owned by another user"
            if show_paths:
                base += f" ({self.prefix})"
            return base
        if not self.upgradable and self.origin != "local":
            return {
                "editable": "editable install",
                "nix": "Nix install",
                "snap": "Snap install",
                "docker": "container install",
                "system": "system package",
            }.get(self.kind, "unknown install")
        name = "pip venv" if self.kind == "pip" else self.kind
        if self.origin == "local":
            if show_paths and self.origin_detail:
                return f"{name}, from local path {self.origin_detail}"
            return f"{name}, from a local path"
        if self.origin == "vcs":
            if show_paths and self.origin_detail:
                return f"{name}, from {self.origin_detail}"
            return f"{name}, from a git checkout"
        if self.origin == "index":
            return "brew formula" if self.kind == "brew" else f"{name}, from PyPI"
        return name


def _read_direct_url() -> dict | None:
    """This interpreter's aipager ``direct_url.json`` (PEP 610), or None."""
    try:
        from importlib.metadata import distribution
        raw = distribution("aipager").read_text("direct_url.json")
    except Exception:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _origin(direct_url: dict | None) -> tuple[str, str | None]:
    if not direct_url:
        return "index", None
    url = direct_url.get("url")
    if not isinstance(url, str):
        return "unknown", None
    if "vcs_info" in direct_url:
        return "vcs", url
    if url.startswith("file://"):
        return "local", unquote(urlparse(url).path) or url
    return "unknown", url


def _owned_and_writable(prefix: str, uid: int) -> bool:
    try:
        st = os.stat(prefix)
    except OSError:
        return False
    return st.st_uid == uid and os.access(prefix, os.W_OK)


def _is_under(path: str, root: str) -> bool:
    root = root.rstrip("/") + "/"
    return (path.rstrip("/") + "/").startswith(root)


def detect_install_source(
    *,
    prefix: str | None = None,
    executable: str | None = None,
    base_prefix: str | None = None,
    direct_url=_UNSET,
    uid: int | None = None,
    env: dict | None = None,
) -> InstallSource:
    """Classify the running install. Never raises.

    The keyword overrides exist for tests; production passes none.
    Rules apply in order, first match wins (see design.md).
    """
    try:
        return _detect(prefix, executable, base_prefix, direct_url, uid, env)
    except Exception:
        return InstallSource(
            kind="unknown", prefix=prefix or sys.prefix,
            python=executable or sys.executable,
            reason=_REFUSAL_REASONS["unknown"],
        )


def _detect(prefix, executable, base_prefix, direct_url, uid, env) -> InstallSource:
    prefix = os.path.abspath(prefix if prefix is not None else sys.prefix)
    python = executable if executable is not None else sys.executable
    base = os.path.abspath(base_prefix if base_prefix is not None else sys.base_prefix)
    du = _read_direct_url() if direct_url is _UNSET else direct_url
    uid = os.getuid() if uid is None else uid
    environ = os.environ if env is None else env
    origin, detail = _origin(du)

    def refused(kind: str) -> InstallSource:
        return InstallSource(kind=kind, prefix=prefix, python=python,
                             origin=origin, origin_detail=detail,
                             upgradable=False, reason=_REFUSAL_REASONS[kind])

    real = os.path.realpath(prefix)
    if isinstance(du, dict) and isinstance(du.get("dir_info"), dict) \
            and du["dir_info"].get("editable"):
        return refused("editable")
    if _is_under(prefix, "/nix/store") or _is_under(real, "/nix/store"):
        return refused("nix")
    if environ.get("SNAP") or _is_under(prefix, "/snap") or _is_under(real, "/snap"):
        return refused("snap")
    if os.path.exists(_DOCKERENV):
        return refused("docker")

    kind: str | None = None
    if os.path.isfile(os.path.join(prefix, "pipx_metadata.json")):
        kind = "pipx"
    elif os.path.isfile(os.path.join(prefix, "uv-receipt.toml")):
        kind = "uv"
    elif "/Cellar/aipager/" in prefix + "/" or "/Cellar/aipager/" in real + "/":
        kind = "brew"
    elif prefix != base:
        kind = "pip"
    elif _is_under(prefix, "/usr") and not _is_under(prefix, "/usr/local"):
        return refused("system")
    else:
        return refused("unknown")

    if not _owned_and_writable(prefix, uid):
        return InstallSource(
            kind="foreign", prefix=prefix, python=python, origin=origin,
            origin_detail=detail, upgradable=False, owner_kind=kind,
            reason=(f"this {kind} install ({prefix}) is not owned by or not "
                    "writable by your user; aipager never touches another "
                    "user's install"),
        )
    if origin == "local":
        # Installed from a folder on disk (``pipx install /path/to/aipager``):
        # the installer's own upgrade reinstalls from that folder, not from
        # PyPI, so an update "to the latest release" could only reinstall
        # the same code, or fail once the folder is gone (roadmap 8.46,
        # seen live 2026-09-26). The folder's owner updates it there. No
        # path in the reason: it is shown in group chats too.
        return InstallSource(kind=kind, prefix=prefix, python=python,
                             origin=origin, origin_detail=detail,
                             upgradable=False, reason=_LOCAL_FOLDER_REASON)
    return InstallSource(kind=kind, prefix=prefix, python=python, origin=origin,
                         origin_detail=detail, upgradable=True, reason=None)


def augmented_path() -> str:
    """The process PATH plus the directories installers really live in.

    ``~/.local/bin`` (pipx, uv's installer), ``~/.cargo/bin`` (older uv),
    the Homebrew prefixes, ``/usr/bin`` and ``/bin``. Deduplicated, the
    process's own order first.
    """
    home = Path.home()
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    parts += [str(home / ".local" / "bin"), str(home / ".cargo" / "bin"),
              *_HOMEBREW_BINS, "/usr/bin", "/bin"]
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return os.pathsep.join(out)


def resolve_tool(name: str) -> str | None:
    """Absolute path of ``name`` over :func:`augmented_path`, or None."""
    found = shutil.which(name, path=augmented_path())
    return os.path.abspath(found) if found else None


def installer_kind() -> str | None:
    """``"uv"|"pipx"|"brew"|"pip"`` for an upgradable install, else None.

    The compat answer behind ``updater._detect_installer``.
    """
    source = detect_install_source()
    return source.kind if source.upgradable else None


def _is_secret_key(key: str) -> bool:
    """The four named secrets, plus their families: every aipager
    ``CLAUDE_TG_*`` setting, every ``ANTHROPIC_*`` credential, and any
    ``CLAUDE_CODE_*TOKEN*`` (Claude Code sets more than the OAuth one)."""
    return (key in SECRET_ENV_KEYS
            or key.startswith(("CLAUDE_TG_", "ANTHROPIC_"))
            or (key.startswith("CLAUDE_CODE_") and "TOKEN" in key))


def spawn_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """``os.environ`` minus secrets, with PATH = :func:`augmented_path`."""
    env = {k: v for k, v in os.environ.items() if not _is_secret_key(k)}
    env["PATH"] = augmented_path()
    if extra:
        env.update(extra)
    return env


# ---------------------------------------------------------------------------
# The one installer -> command table
# ---------------------------------------------------------------------------

def upgrade_argv(source: InstallSource) -> list[str] | None:
    """Upgrade argv whose first element is absolute, or None when the
    install is refused or its installer binary cannot be found."""
    if not source.upgradable:
        return None
    if source.kind == "pip":
        return [source.python, "-m", "pip", "install", "--upgrade", "aipager"]
    tool = resolve_tool(source.kind)
    if tool is None:
        return None
    if source.kind == "pipx":
        return [tool, "upgrade", "aipager"]
    if source.kind == "uv":
        # --refresh bypasses uv's index cache, which has bitten users when a
        # fresh PyPI release was minutes old.
        return [tool, "tool", "upgrade", "aipager", "--refresh"]
    if source.kind == "brew":
        return [tool, "upgrade", "aipager"]
    return None


def upgrade_env(source: InstallSource) -> dict[str, str]:
    """Child env for the upgrade: scrubbed, augmented PATH, and pinned to
    THIS install so pipx/uv upgrade the venv that is running rather than
    one under a default home."""
    extra: dict[str, str] = {}
    prefix = Path(source.prefix)
    if source.kind == "pipx" and prefix.parent.name == "venvs":
        extra["PIPX_HOME"] = str(prefix.parent.parent)
    elif source.kind == "uv":
        extra["UV_TOOL_DIR"] = str(prefix.parent)
    return spawn_env(extra)


def _tool_or_bare(name: str) -> str:
    return resolve_tool(name) or name


def uninstall_argv(kind: str | None) -> list[str] | None:
    if kind == "uv":
        return [_tool_or_bare("uv"), "tool", "uninstall", "aipager"]
    if kind == "pipx":
        return [_tool_or_bare("pipx"), "uninstall", "aipager"]
    if kind == "brew":
        return [_tool_or_bare("brew"), "uninstall", "aipager"]
    return None


def extra_install_argv(kind: str | None, extra: str) -> list[str] | None:
    """(Re)install aipager with an optional extra.

    uv / pipx go through the installer so the extra is recorded and
    survives a later upgrade. Everything else installs the extra's
    packages straight into the running interpreter. None only for an
    unknown extra.
    """
    if kind == "uv":
        return [_tool_or_bare("uv"), "tool", "install", "--reinstall",
                f"aipager[{extra}]"]
    if kind == "pipx":
        return [_tool_or_bare("pipx"), "install", "--force", f"aipager[{extra}]"]
    packages = _EXTRA_PACKAGES.get(extra)
    if packages is None:
        return None
    return [sys.executable, "-m", "pip", "install", "--upgrade", *packages]


def reinstall_hint(kind: str | None) -> str:
    """A pasteable repair command for the owning installer."""
    if kind == "uv":
        return "uv tool install --reinstall aipager"
    if kind == "pipx":
        return "pipx install --force aipager"
    if kind == "brew":
        return "brew reinstall aipager"
    if kind == "pip":
        return f"{sys.executable} -m pip install --force-reinstall aipager"
    return "pip install --force-reinstall aipager"


__all__ = [
    "InstallSource", "UPGRADABLE_KINDS", "SECRET_ENV_KEYS",
    "augmented_path", "detect_install_source", "extra_install_argv",
    "installer_kind", "reinstall_hint", "resolve_tool", "spawn_env",
    "uninstall_argv", "upgrade_argv", "upgrade_env",
]
