"""Print every path an aipager process would use, as JSON.

Run as a SUBPROCESS by ``tests/test_instance_paths.py`` with a chosen
environment (``AIPAGER_INSTANCE_DIR``, ``HOME``, ...), so neither the
test suite's conftest redirects nor values computed at import time in
the parent process can mask what a fresh daemon or hook would resolve.

Reads only. It writes nothing, binds nothing and starts nothing. The
one directory listing it does (``status._live_sessions`` and
``inject.list_sessions``) only globs; ``is_socket`` is answered for
paths inside the instance folder so a plain file stands in for a socket
there and nothing is ever bound.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys

PROBE_SESSION = "claude-probe__d1"


def main() -> None:
    inst = os.environ.get("AIPAGER_INSTANCE_DIR", "").strip()
    if inst:
        real_is_socket = pathlib.Path.is_socket
        inst_norm = os.path.normpath(inst)

        def _is_socket(self):
            if str(self).startswith(inst_norm + os.sep):
                return self.exists()
            return real_is_socket(self)

        pathlib.Path.is_socket = _is_socket

    from aipager import (
        claude_bootstrap,
        config,
        daemon_secrets,
        instance,
        policy_snapshot,
        safety,
        scope,
        statusline_file,
        status,
    )
    from aipager.bot import session_ops
    from aipager.dtach import hook_reply, inject, model_switch_marker
    from aipager.dtach import notify_hook, statusline_notify

    session = PROBE_SESSION
    hook_runtime = os.path.dirname(notify_hook.SOCKET_PATH) or "/tmp"
    restricted_write = {}
    for kind, target in (
        ("policy", "claude-policy-x.json"),
        ("notes", "claude-notes-x/1.json"),
        ("status", "claude-status-x.json"),
        ("reply", "claude-reply-x.txt"),
        ("dtach", "claude-dtach-x.sock"),
    ):
        path = os.path.join(instance.runtime_tmp_dir(), target)
        restricted_write[kind] = bool(safety.path_violation(
            "Write", {"file_path": path},
            safety.DENY_PATHS_NO_ACCESS, safety.DENY_PATHS_NO_WRITE))

    out = {
        "runtime": {
            "control_socket": config.SOCKET_PATH,
            "default_socket_path": config._default_socket_path(),
            "flood_mute": config.FLOOD_MUTE_FILE,
            "flood_backoff": config.FLOOD_BACKOFF_FILE,
            "file_download_dir": str(config.FILE_DOWNLOAD_DIR),
            "sock_prefix": inject.SOCK_PREFIX,
            "sock_path": inject._sock_path(session),
            "snapshot": str(policy_snapshot.snapshot_path(session)),
            "notes": str(policy_snapshot.notes_dir(session)),
            "floor": str(policy_snapshot.floor_path()),
            "reply_context": str(policy_snapshot.reply_context_path(session)),
            "status_file": str(statusline_file.status_file_path(session)),
            "notify_hook_socket": notify_hook.SOCKET_PATH,
            "statusline_hook_socket": statusline_notify.SOCKET_PATH,
            "notify_hook_status_dir": notify_hook._STATUS_DIR,
            "statusline_hook_status_dir": statusline_notify._STATUS_DIR,
            "marker_dir": session_ops._marker_dir(),
            "marker": str(model_switch_marker.marker_path(
                session_ops._marker_dir(), session)),
            "reply_socket": hook_reply.build_reply_path(hook_runtime, "0" * 32),
            "self_restart_log": instance.self_restart_log_path(),
        },
        # Only for an instance: without one these would list the real /tmp.
        "scans": {
            "list_sessions": sorted(asyncio.run(inject.list_sessions())),
            "status_live": sorted(status._live_sessions()),
        } if inst else {},
        "deny_no_access": list(safety.DENY_PATHS_NO_ACCESS),
        "restricted_write_denied": restricted_write,
        "home": {
            "session_state": str(config.SESSION_STATE_FILE),
            "flood_state": str(config.FLOOD_STATE_FILE),
            "config_path": str(scope.CONFIG_PATH),
            "daemon_env": str(daemon_secrets.DAEMON_ENV_PATH),
            "claude_settings": str(claude_bootstrap._SETTINGS),
            "claude_json": str(claude_bootstrap._CLAUDE_JSON),
            "xdg_config_env": str(config._XDG_CONFIG),
            "lock": str(pathlib.Path.home() / ".local" / "share" / "aipager"
                        / "daemon.lock"),
        },
        "uid": os.getuid(),
    }
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main()
