"""Run ``python -m aipager <argv...>`` under an audit-hook tripwire.

The child dies at once (os._exit) on the first:
- file write / create / delete / rename / chmod          -> exit 97
- network call (connect, bind, sendto, DNS lookup)        -> exit 98
- process spawn (subprocess, exec, fork, posix_spawn)     -> exit 99
- read of the operator's real config, Claude dir or a .env -> exit 97

So a refusal that regressed can never go on to start a daemon, write a file
or reach the network. Usage: _guarded_start.py start  |  _guarded_start.py --selftest <kind>
"""

import os
import pwd
import sys

_REAL = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
_FORBIDDEN_READ = (f"{_REAL}/.config/aipager", f"{_REAL}/.claude", f"{_REAL}/.claude.json")
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_WRITE_EVENTS = {"os.mkdir", "os.rename", "os.remove", "os.rmdir", "os.symlink", "os.link",
                 "os.truncate", "os.chmod", "os.chown", "os.utime", "shutil.rmtree",
                 "shutil.move", "os.mkfifo", "os.mknod"}
_NET_EVENTS = {"socket.connect", "socket.bind", "socket.sendto", "socket.sendmsg",
               "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex"}
_SPAWN_EVENTS = {"subprocess.Popen", "os.exec", "os.fork", "os.forkpty", "os.posix_spawn",
                 "os.spawn", "os.system", "os.startfile", "pty.spawn"}


def _die(code: int, why: str) -> None:
    os.write(2, f"\nTRIPWIRE {code} {why}\n".encode())
    os._exit(code)


def _hook(event, args):
    if event == "open":
        path, mode, flags = (list(args) + [None, None, None])[:3]
        p = os.fsdecode(path) if isinstance(path, (str, bytes, os.PathLike)) else str(path)
        if isinstance(path, int):
            return
        if p.startswith(_FORBIDDEN_READ) or os.path.basename(p) == ".env":
            _die(97, f"read of a forbidden file ({event})")
        writes = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & _WRITE_FLAGS)
        if writes and p not in ("/dev/null", "/dev/tty"):
            _die(97, f"write {p}")
    elif event in _WRITE_EVENTS:
        _die(97, f"{event} {args[:1]}")
    elif event in _NET_EVENTS:
        _die(98, event)
    elif event in _SPAWN_EVENTS:
        _die(99, event)


def main() -> None:
    if sys.argv[1] == "--selftest":
        sys.addaudithook(_hook)
        kind = sys.argv[2]
        if kind == "write":
            open(os.path.join(os.environ["AFT_SCRATCH"], "x"), "w")
        elif kind == "net":
            import socket
            socket.create_connection(("127.0.0.1", 9), timeout=1)
        elif kind == "spawn":
            import subprocess
            subprocess.run(["true"])
        elif kind == "read":
            open(os.path.join(os.environ["AFT_SCRATCH"], ".env"))
        sys.exit(0)
    import runpy
    sys.argv = ["aipager", *sys.argv[1:]]
    sys.addaudithook(_hook)
    runpy.run_module("aipager", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
