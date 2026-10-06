"""Where this aipager instance keeps its shared runtime files.

One place decides every path that is NOT under ``$HOME`` and that two
daemons on one machine would otherwise share: the control socket (and,
derived from its folder, the reply sockets, the flood signal files and
the model-switch marker), the dtach session sockets, the per-session
policy / notes / status / reply files, the policy floor file, the
download folder and the self-restart log.

With ``AIPAGER_INSTANCE_DIR`` unset (every normal install) each function
returns exactly the path aipager has always used. With it set, all of
them move under that one folder, so a second daemon (a test instance)
shares nothing with the first. HOME-based files are not moved here: the
caller points ``HOME`` at a separate folder instead, because Claude Code
itself reads ``~/.claude``, and :func:`start_check` refuses to start an
instance whose HOME is the real home.

Stdlib-only (``os`` and ``pathlib``) and cheap: the hook binaries' lazily imported helpers go
through it. The hooks themselves (``dtach/notify_hook.py`` and
``dtach/statusline_notify.py``) inline the control-socket and status
folder rules instead of importing this; mirror any change there.

Every function reads the environment at call time.
"""

from __future__ import annotations

import os
from pathlib import Path

INSTANCE_ENV = "AIPAGER_INSTANCE_DIR"

#: The longest fixed-name socket an instance creates (``hook_reply``'s
#: ``aipager-reply-<32 hex>.sock``). Used to check the folder is short
#: enough for a Unix socket path.
_LONGEST_SOCKET_NAME = "aipager-reply-" + "0" * 32 + ".sock"

#: ``sun_path`` holds 108 bytes including the trailing NUL.
_SUN_PATH_MAX = 107


def instance_dir() -> str | None:
    """The normalised ``AIPAGER_INSTANCE_DIR``, or ``None`` when unset or blank."""
    raw = os.environ.get(INSTANCE_ENV, "").strip()
    if not raw:
        return None
    return os.path.normpath(raw)


def runtime_tmp_dir() -> str:
    """The folder holding the per-session files and dtach sockets."""
    return instance_dir() or "/tmp"


def control_socket_path() -> str:
    """The daemon's control socket.

    Instance set: ``<dir>/aipager.sock`` (it wins over both other
    settings). Otherwise ``$AIPAGER_SOCKET_PATH`` wins outright; else
    ``$XDG_RUNTIME_DIR/aipager.sock`` (what the systemd unit's ``%t/``
    expands to); else ``/tmp/aipager.sock``.
    """
    inst = instance_dir()
    if inst:
        return os.path.join(inst, "aipager.sock")
    override = os.environ.get("AIPAGER_SOCKET_PATH", "").strip()
    if override:
        return override
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if runtime_dir:
        return str(Path(runtime_dir) / "aipager.sock")
    return "/tmp/aipager.sock"


def dtach_sock_prefix() -> str:
    """The dtach socket path up to the session label."""
    return os.path.join(runtime_tmp_dir(), "claude-dtach-")


def file_download_dir() -> str:
    """Where files and voice notes sent from Telegram are downloaded."""
    inst = instance_dir()
    if inst:
        return os.path.join(inst, "aipager-files")
    return "/tmp/aipager-files"


def self_restart_log_path() -> str:
    """The log a self-restart without a service unit writes to."""
    inst = instance_dir()
    if inst:
        return os.path.join(inst, "aipager.log")
    return "/tmp/aipager.log"


def protected_globs() -> tuple[str, ...]:
    """The relocated control-file globs a restricted turn may not touch.

    Empty when the instance is unset: the ``/tmp`` globs are already in
    ``safety.DENY_PATHS_NO_ACCESS``."""
    inst = instance_dir()
    if not inst:
        return ()
    return tuple(
        os.path.join(inst, f"claude-{kind}-*")
        for kind in ("policy", "notes", "status", "reply", "dtach")
    )


def _real_home() -> str:
    """The home folder of the current OS user from the password database.
    A seam: tests point it at a tmp dir."""
    import pwd
    return pwd.getpwuid(os.getuid()).pw_dir


def start_check() -> list[str]:
    """Reasons ``aipager start`` must refuse this instance, as lines for
    ``friendly_error``. Empty when the instance is unset or fine."""
    raw = os.environ.get(INSTANCE_ENV, "").strip()
    if not raw:
        return []
    inst = os.path.normpath(raw)
    if not os.path.isabs(inst) or not os.path.isdir(inst):
        return [f"{INSTANCE_ENV} must be an absolute path to an existing folder you own."]
    try:
        owner = os.stat(inst).st_uid
    except OSError:
        owner = -1
    if owner != os.getuid():
        return [f"{INSTANCE_ENV} must be an absolute path to an existing folder you own."]
    home = os.environ.get("HOME", "").strip()
    real_home = os.path.realpath(_real_home())
    if not home or os.path.realpath(home) == real_home:
        return [
            f"{INSTANCE_ENV} is set but HOME is your real home folder ({real_home}).",
            "Point HOME at a separate folder so this instance cannot touch your real settings.",
        ]
    longest = len(os.fsencode(os.path.join(inst, _LONGEST_SOCKET_NAME)))
    if longest > _SUN_PATH_MAX:
        return [
            f"{INSTANCE_ENV} is too long for a socket path ({longest} bytes, "
            f"the limit is {_SUN_PATH_MAX}). Use a shorter folder."
        ]
    return []
