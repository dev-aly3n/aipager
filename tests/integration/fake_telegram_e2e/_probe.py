"""Run as a subprocess: print every resolved runtime path as JSON.

Fresh interpreter, so import-time constants and the default suite's conftest
redirects cannot mask anything. Writes nothing unless argv[1] == "list", in
which case it binds one listening socket inside the instance folder and
reports what ``inject.list_sessions()`` sees.
"""

import asyncio
import inspect
import json
import os
import socket
import sys


def _s(x):
    if x is None:
        return None
    if isinstance(x, (list, tuple)):
        return [os.fspath(i) if not isinstance(i, str) else i for i in x]
    return os.fspath(x)


def main() -> None:
    from aipager import config, instance, policy_snapshot, safety, statusline_file
    from aipager.dtach import inject, notify_hook, statusline_notify

    out = {
        "instance_dir": instance.instance_dir(),
        "runtime_tmp_dir": instance.runtime_tmp_dir(),
        "control_socket_path": instance.control_socket_path(),
        "dtach_sock_prefix": instance.dtach_sock_prefix(),
        "file_download_dir": instance.file_download_dir(),
        "self_restart_log_path": instance.self_restart_log_path(),
        "protected_globs": list(instance.protected_globs()),
        "start_check": instance.start_check(),
        "config.SOCKET_PATH": _s(config.SOCKET_PATH),
        "config._default_socket_path": _s(config._default_socket_path()),
        "config.FLOOD_MUTE_FILE": _s(config.FLOOD_MUTE_FILE),
        "config.FLOOD_BACKOFF_FILE": _s(config.FLOOD_BACKOFF_FILE),
        "config.FILE_DOWNLOAD_DIR": _s(config.FILE_DOWNLOAD_DIR),
        "inject.SOCK_PREFIX": _s(inject.SOCK_PREFIX),
        "inject._sock_path": _s(inject._sock_path("ftprobe")),
        "snapshot_path": _s(policy_snapshot.snapshot_path("ftprobe")),
        "notes_dir": _s(policy_snapshot.notes_dir("ftprobe")),
        "floor_path": _s(policy_snapshot.floor_path()),
        "reply_context_path": _s(policy_snapshot.reply_context_path("ftprobe")),
        "statusline_file.STATUS_DIR": _s(statusline_file.STATUS_DIR),
        "status_file_path": _s(statusline_file.status_file_path("ftprobe")),
        "notify_hook.SOCKET_PATH": _s(notify_hook.SOCKET_PATH),
        "statusline_notify.SOCKET_PATH": _s(statusline_notify.SOCKET_PATH),
        "safety.DENY_PATHS_NO_ACCESS": _s(list(safety.DENY_PATHS_NO_ACCESS)),
        "uid": os.getuid(),
    }
    home = {}
    for mod, attr in (("aipager.config", "SESSION_STATE_FILE"),
                      ("aipager.scope", "CONFIG_PATH"),
                      ("aipager.daemon_secrets", "DAEMON_ENV_PATH")):
        try:
            m = __import__(mod, fromlist=[attr])
            home[f"{mod}.{attr}"] = _s(getattr(m, attr))
        except Exception as exc:  # noqa: BLE001 - reported, not hidden
            home[f"{mod}.{attr}"] = f"<missing: {type(exc).__name__}>"
    out["home_paths"] = home

    if len(sys.argv) > 1 and sys.argv[1] == "list":
        path = os.path.join(instance.runtime_tmp_dir(), "claude-dtach-ftlist.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(4)
        try:
            res = inject.list_sessions()
            if inspect.isawaitable(res):
                res = asyncio.run(res)
            out["list_sessions"] = [str(x) for x in res]
        finally:
            srv.close()
            os.unlink(path)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
