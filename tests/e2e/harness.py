"""Shared helpers for the real-Claude E2E safety suite.

These tests reproduce a *Telegram-driven turn* without Telegram: write the
per-session policy snapshot the daemon and the ``UserPromptSubmit`` hook
would write, wire the real ``aipager-hook`` into a throwaway Claude
project, and run real Claude via ``claude -p``. The ``PreToolUse`` hook
then enforces exactly as it does in production.

The snapshot is never a hand-written dict. :func:`write_snapshot` goes
through the production pipeline: :func:`policy_snapshot.write_note` (the
daemon's per-message note, resolved by ``resolve_snapshot`` from a real
:class:`~aipager.policy.Policy`), then ``snapshot_for_prompt`` (what the
``UserPromptSubmit`` hook computes when it picks the note up) and
``write_merged_snapshot``. The policy is the built-in one
(:func:`builtin_policy`) unless a test loads its own policy.yaml through
the real loader (:func:`policy_from_yaml`); the operator's own
``~/.config/aipager/policy.yaml`` is never read.

Assertions are outcome-based (robust to Claude's nondeterminism): a
blocked scenario asserts the tool was denied by the aipager hook (its deny
reason is in the transcript) and the forbidden result never happened; an
allowed scenario asserts the tool really ran and its output appeared.

Isolation:

- Hook datagrams go to a socket path under the test's tmp dir that
  nothing listens on (``AIPAGER_SOCKET_PATH``), so these tests never reach
  the operator's running daemon (it used to create a registry entry for
  every throwaway session).
- Tests about the home folder or aipager's own files run Claude with
  ``HOME`` pointed at a fake home (:func:`make_fake_home`) holding canary
  files, so a broken guard can only ever expose fake content.
- The real-home write guard (roadmap 8.97) stays on: ``claude`` and the
  ``aipager-hook`` it spawns inherit ``PYTEST_CURRENT_TEST``. The hook's
  ``PreToolUse`` path writes nothing under the home folder (it reads the
  snapshot and the transcript, and answers on stdout), so the guard has
  nothing to refuse there.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from aipager import policy as _policy
from aipager import policy_snapshot as _snap
from aipager.scope import Member, Scope

MARKER = "[via Telegram · @e2e]"

#: ``policy_snapshot.snapshot_path`` as the hook resolves it, captured at
#: import (collection time, before any fixture runs). tests/conftest.py
#: redirects the module's ``snapshot_path`` into each test's tmp dir; the
#: hook runs in its own process and reads the real path, so an e2e test
#: must write there (``_snapshots_where_the_hook_reads`` in this folder's
#: conftest puts it back). Without that every snapshot went to the tmp
#: dir, the hook found none and held every turn to the floor: the owner
#: and terminal tests failed and the deny tests passed for the wrong
#: reason (2026-10-06, since f679e95).
PRODUCTION_SNAPSHOT_PATH = _snap.snapshot_path

#: The member and group every snapshot is written for.
E2E_USER_ID = 999
E2E_CHAT_ID = -100999

#: A policy file that never exists: :func:`builtin_policy` loads the
#: built-in roles and floor only.
_NO_POLICY = Path("/nonexistent/aipager-e2e/policy.yaml")
_NO_POLICY_D = Path("/nonexistent/aipager-e2e/policy.d")

#: Canary text in the fake home's protected files. Seeing it in Claude's
#: answer means the guard let the read through.
FAKE_CONFIG_CANARY = "E2E_FAKE_AIPAGER_CONFIG_CANARY"
FAKE_KEY_CANARY = "E2E_FAKE_SSH_KEY_CANARY"
FAKE_SEARCH_CANARY = "E2E_FAKE_SEARCH_CANARY"
#: What follows the search canary on its line: only a search that read
#: the file can show it (Claude may repeat the pattern itself).
FAKE_SEARCH_PAYLOAD = "secret-payload-7f3a"


# ---- discovery -----------------------------------------------------------

def claude_bin() -> str | None:
    """The claude binary the daemon would actually launch.

    The one caller allowed to touch a real installed claude — this
    module backs the opt-in `-m e2e` suite, not the default run, and is
    exempted from the safe-by-default `_no_real_claude_candidates`
    fixture in tests/conftest.py.
    """
    from aipager import claude_resolve
    resolved = claude_resolve.try_resolve_claude_binary()
    return resolved.chosen.path if resolved else None


def aipager_hook_bin() -> str | None:
    """The real aipager-hook console script (what production runs), or
    ``AIPAGER_E2E_HOOK`` when set: an executable to use instead, for
    checking a worktree's hook before it is installed."""
    override = os.environ.get("AIPAGER_E2E_HOOK", "").strip()
    if override:
        return override if os.access(override, os.X_OK) else None
    found = shutil.which("aipager-hook")
    if found:
        return found
    cand = Path(sys.prefix) / "bin" / "aipager-hook"
    return str(cand) if cand.exists() else None


def new_session() -> str:
    """Unique session name: its snapshot file is this test's alone."""
    return f"claude-e2e-{uuid.uuid4().hex[:8]}"


def daemon_pids() -> list[str]:
    """PIDs of this user's running daemons: argv ``aipager start`` as two
    real arguments (``/path/aipager start``, ``python -m aipager start``,
    as ``wizard.daemon_io._is_daemon_argv`` reads it), in this process's
    mount namespace (a container's daemon on the same host shows up in
    ``/proc`` too, under the same uid)."""
    from aipager.wizard.daemon_io import _is_daemon_argv
    try:
        out = subprocess.run(["pgrep", "-u", str(os.getuid()), "-f", "aipager start"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return []
    try:
        own_ns = os.readlink("/proc/self/ns/mnt")
    except OSError:
        own_ns = None
    pids = []
    for pid in out.stdout.split():
        proc = Path("/proc") / pid
        try:
            argv = [a.decode(errors="replace")
                    for a in (proc / "cmdline").read_bytes().split(b"\0") if a]
            if not _is_daemon_argv(argv):
                continue
            if own_ns is not None and os.readlink(proc / "ns" / "mnt") != own_ns:
                continue
        except OSError:
            continue
        pids.append(pid)
    return pids


def daemon_running() -> bool:
    """True if an aipager daemon process is alive (:func:`daemon_pids`),
    or the daemon socket exists (fail safe for the halt test).

    Used to skip the real-dtach halt test: a live daemon's session_monitor
    would adopt the throwaway dtach session and message the operator. We
    check the PROCESS first (``/tmp/aipager.sock`` can vanish while the
    daemon keeps running, since the daemon holds the socket bound even
    after it is unlinked from disk)."""
    return bool(daemon_pids()) or Path("/tmp/aipager.sock").exists()


# ---- project, fake home, policy ------------------------------------------

def make_project(tmp_path: Path, *, events=("PreToolUse",), root: Path | None = None,
                 commands: dict[str, str] | None = None) -> Path:
    """A temp Claude project wiring the real aipager-hook on ``events``
    (all tools). Seeds a README sentinel for benign tests. ``root`` puts
    the project in an existing folder (a fake home); ``commands`` adds
    project slash commands (``.claude/commands/<name>.md``)."""
    hook = aipager_hook_bin()
    assert hook, "aipager-hook not found"
    proj = root if root is not None else tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True, exist_ok=True)
    hooks = {ev: [{"hooks": [{"type": "command", "command": hook}]}]
             for ev in events}
    (proj / ".claude" / "settings.json").write_text(
        json.dumps({"hooks": hooks}), encoding="utf-8")
    (proj / "README.md").write_text("E2E_README_SENTINEL: hello world\n")
    for name, body in (commands or {}).items():
        d = proj / ".claude" / "commands"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.md").write_text(body, encoding="utf-8")
    return proj


def make_fake_home(tmp_path: Path) -> Path:
    """A fake home folder holding canary copies of what the guards
    protect: aipager's config (``.config/aipager``), a credential
    (``.ssh/id_test``) and Claude Code's settings. Nothing in it is real."""
    home = tmp_path / "home"
    (home / ".config" / "aipager").mkdir(parents=True)
    (home / ".config" / "aipager" / "aipager.yaml").write_text(
        f"schema_version: 3\nbot_token: {FAKE_CONFIG_CANARY}\n", encoding="utf-8")
    (home / ".config" / "aipager" / "x").write_text(
        f"{FAKE_SEARCH_CANARY} {FAKE_SEARCH_PAYLOAD}\n", encoding="utf-8")
    (home / ".ssh").mkdir(mode=0o700)
    (home / ".ssh" / "id_test").write_text(
        f"dummy test file, not a real key: {FAKE_KEY_CANARY}\n", encoding="utf-8")
    return home


def builtin_policy() -> _policy.Policy:
    """The built-in roles and safety floor, through the real loader with
    no policy file (never the operator's policy.yaml)."""
    return _policy.load_policy(_NO_POLICY, _NO_POLICY_D)


def policy_from_yaml(folder: Path, text: str) -> _policy.Policy:
    """``text`` written as ``folder/policy.yaml`` and loaded by the real
    loader (built-ins first, the file layered on top)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "policy.yaml"
    path.write_text(text, encoding="utf-8")
    return _policy.load_policy(path, folder / "policy.d")


# ---- snapshots, through the production pipeline --------------------------

def telegram_note(session: str, *, role_name: str = "user", body: str,
                  deny_tools=(), allow_tools=(), policy: _policy.Policy | None = None,
                  scope_mode: bool = True) -> dict:
    """The note the daemon writes for one Telegram message from the e2e
    member (``policy_snapshot.write_note``, the real writer), read back.
    Written into a private temp folder, not the session's notes folder:
    the pick-up below is computed here, not by a ``UserPromptSubmit``."""
    pol = policy if policy is not None else builtin_policy()
    role = pol.get_role(role_name)
    assert role is not None, f"no role {role_name!r} in the policy"
    member = Member(id=E2E_USER_ID, label="e2e", role=role_name,
                    deny_tools=tuple(deny_tools), allow_tools=tuple(allow_tools))
    scope = Scope(chat_id=E2E_CHAT_ID, kind="group", label="e2e group",
                  members=(member,))
    with tempfile.TemporaryDirectory(prefix="aipager-e2e-note-") as d:
        orig = _snap.notes_dir
        _snap.notes_dir = lambda _s: Path(d)
        try:
            path = _snap.write_note(
                session, role, scope, member, msg_id=1, chat_id=E2E_CHAT_ID,
                sender_key=(E2E_CHAT_ID, E2E_USER_ID), body=body, raw_text=body,
                scope_mode=scope_mode, policy=pol)
        finally:
            _snap.notes_dir = orig
        assert path is not None, "write_note failed"
        return json.loads(path.read_text(encoding="utf-8"))


def _promote(session: str, prompt: str, consumed: list[dict], *,
             turn_open: bool) -> dict:
    """What the ``UserPromptSubmit`` hook writes when ``prompt`` is
    submitted and picks up ``consumed`` (``snapshot_for_prompt``, then
    ``write_merged_snapshot``, as ``notify_hook._match_and_promote``)."""
    snap = _snap.snapshot_for_prompt(session, prompt, list(consumed),
                                     list(consumed), turn_open=turn_open)
    _snap.write_merged_snapshot(session, snap)
    return snap


def write_snapshot(session: str, *, role_name: str = "user", deny_tools=(),
                   allow_tools=(), policy: _policy.Policy | None = None,
                   body: str | None = None, scope_mode: bool = True) -> dict:
    """A fresh Telegram turn from the e2e member: the note for ``body``
    (default: a marked message) picked up by the prompt that carries it.
    Returns the snapshot written."""
    body = body if body is not None else f"{MARKER}\n(e2e)"
    note = telegram_note(session, role_name=role_name, body=body,
                         deny_tools=deny_tools, allow_tools=allow_tools,
                         policy=policy, scope_mode=scope_mode)
    return _promote(session, body, [note], turn_open=False)


def write_terminal_prompt(session: str, prompt: str) -> dict:
    """A prompt typed in the terminal starts a turn: no note matches it
    (what the hook writes then, whatever snapshot was there before)."""
    return _promote(session, prompt, [], turn_open=False)


def write_joined_message(session: str, *, role_name: str = "user",
                         body: str | None = None,
                         policy: _policy.Policy | None = None) -> dict:
    """A Telegram message from the e2e member joins the running turn
    (roadmap 8.77: Claude Code fires ``UserPromptSubmit`` when it queues
    the message, while the turn-open file says a turn runs)."""
    body = body if body is not None else f"{MARKER}\n(e2e joins)"
    note = telegram_note(session, role_name=role_name, body=body, policy=policy)
    return _promote(session, body, [note], turn_open=True)


def clear_snapshot(session: str) -> None:
    _snap.clear_snapshot(session)


# ---- run + parse ---------------------------------------------------------

def _entries(path: Path | None) -> list[dict]:
    if not path:
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def _blocks(entry: dict) -> list:
    content = (entry.get("message") or {}).get("content")
    return content if isinstance(content, list) else []


@dataclass
class ClaudeRun:
    raw: dict
    home: Path
    session_id: str = ""
    denials: list[str] = field(default_factory=list)
    result: str = ""

    @property
    def transcript(self) -> Path | None:
        if not self.session_id:
            return None
        return next(self.home.glob(
            f".claude/projects/*/{self.session_id}.jsonl"), None)

    def tools_used(self) -> list[str]:
        """Names of tools Claude actually *invoked* (tool_use blocks in the
        transcript) — to distinguish a real tool run from Claude answering
        from memory / narration."""
        return [b.get("name", "") for e in _entries(self.transcript)
                for b in _blocks(e)
                if isinstance(b, dict) and b.get("type") == "tool_use"]

    def tool_inputs(self, tool: str) -> list[dict]:
        """The inputs of every ``tool`` call Claude made."""
        return [b.get("input") or {} for e in _entries(self.transcript)
                for b in _blocks(e)
                if isinstance(b, dict) and b.get("type") == "tool_use"
                and b.get("name") == tool]

    def tool_result_texts(self) -> list[str]:
        """All tool_result payloads from the transcript (for deny-reason
        inspection)."""
        out: list[str] = []
        for e in _entries(self.transcript):
            for b in _blocks(e):
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    if isinstance(c, str):
                        out.append(c)
                    elif isinstance(c, list):
                        out.extend(str(p.get("text", "")) if isinstance(p, dict)
                                   else str(p) for p in c)
        return out

    def deny_reasons(self) -> list[str]:
        """The aipager hook's deny reasons recorded this run."""
        return [t for t in self.tool_result_texts() if "aipager safety policy" in t]

    def user_prompt_texts(self) -> list[str]:
        """The text of every user entry that is not a tool result (the
        prompt records Claude Code wrote, slash-command records included)."""
        out = []
        for e in _entries(self.transcript):
            if e.get("type") != "user":
                continue
            content = (e.get("message") or {}).get("content")
            if isinstance(content, str):
                out.append(content)
                continue
            if not isinstance(content, list):
                continue
            if any(isinstance(b, dict) and b.get("type") == "tool_result"
                   for b in content):
                continue
            out.append("\n".join(str(b.get("text", "")) for b in content
                                 if isinstance(b, dict) and b.get("type") == "text"))
        return out

    # ---- assertions ----
    def assert_denied(self, tool: str) -> None:
        assert tool in self.denials, (
            f"expected {tool} to be denied; denials={self.denials}\n"
            f"result={self.result[:300]!r}")

    def assert_any_denied(self) -> None:
        assert self.denials, (
            f"expected at least one denial; result={self.result[:300]!r}")

    def assert_no_denials(self) -> None:
        assert not self.denials, f"unexpected denials={self.denials}"

    def assert_not_leaked(self, *needles: str) -> None:
        for n in needles:
            assert n not in self.result, (
                f"value {n!r} leaked into result despite the safety policy:\n"
                f"{self.result[:400]!r}")

    def assert_output_contains(self, needle: str) -> None:
        assert needle in self.result, (
            f"expected {needle!r} in result; got {self.result[:400]!r}")

    def assert_ran(self, tool: str) -> None:
        """The tool was actually executed (not denied, and a real tool_use
        appears) — guards against Claude answering from memory so an
        'allowed' test can't pass without genuinely exercising the hook."""
        assert tool not in self.denials, (
            f"{tool} was denied; expected it to be allowed (denials="
            f"{self.denials}; reasons={self.deny_reasons()})")
        used = self.tools_used()
        assert tool in used, (
            f"{tool} never executed (Claude may have answered from memory); "
            f"tools_used={used}; result={self.result[:200]!r}")

    def assert_safety_block_recorded(self, *fragments: str) -> None:
        """The aipager hook denied a call this run (not Claude Code's own
        permission system), with every one of ``fragments`` in one of
        its reasons when given — naming the rule that denied it."""
        reasons = self.deny_reasons()
        assert reasons, "no 'aipager safety policy' deny recorded in transcript"
        if fragments:
            assert any(all(f in r for f in fragments) for r in reasons), (
                f"no deny reason names {fragments!r}; reasons={reasons}")

    def assert_no_regex_in_reasons(self) -> None:
        for txt in self.deny_reasons():
            assert "\\b" not in txt and "/\\" not in txt, (
                f"deny reason leaked a raw regex: {txt!r}")


def run(task: str, *, session: str, project: Path,
        marker: bool = True, timeout: int = 300,
        home: Path | None = None, resume: str | None = None) -> ClaudeRun:
    """One real ``claude -p`` turn in ``project`` (its cwd). ``marker``
    prefixes the Telegram marker line; ``home`` runs Claude (and so the
    hook) with that ``HOME``; ``resume`` continues that Claude session
    (a new turn after the earlier ones in the same transcript)."""
    prompt = (f"{MARKER}\n{task}" if marker else task)
    env = dict(os.environ, CLAUDE_DTACH_SESSION=session,
               # Nothing listens here: the hook's datagrams never reach the
               # operator's daemon (see the module docstring).
               AIPAGER_SOCKET_PATH=str(project.parent / "e2e-no-daemon.sock"))
    if home is not None:
        env["HOME"] = str(home)
        env.pop("CLAUDE_CONFIG_DIR", None)
    # --dangerously-skip-permissions disables Claude's OWN interactive
    # permission prompts (which auto-deny in -p mode and would otherwise
    # confound "allowed" cases). PreToolUse hooks still fire regardless,
    # so the aipager safety hook remains the sole gate under test — which
    # mirrors how the daemon runs sessions.
    # Project settings only (the temp project's hook): the operator's
    # user-level ~/.claude/settings.json wires aipager-hook on
    # UserPromptSubmit too, which would rewrite this session's snapshot
    # before the first tool call (review 2026-10-06).
    argv = ["claude", "-p", prompt, "--output-format", "json",
            "--dangerously-skip-permissions", "--setting-sources", "project,local"]
    if resume:
        argv += ["--resume", resume]
    snap_file = PRODUCTION_SNAPSHOT_PATH(session)
    written = _read_bytes(snap_file)
    proc = subprocess.run(
        argv,
        cwd=str(project), env=env, capture_output=True, text=True,
        timeout=timeout,
    )
    # The hook must have enforced the snapshot this test wrote: anything
    # that rewrote it during the run (a UserPromptSubmit hook from some
    # other settings source) would make the result mean something else.
    assert _read_bytes(snap_file) == written, (
        f"{snap_file} changed during the claude run: another hook rewrote "
        "the snapshot this test wrote")
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise AssertionError(
            f"claude -p produced no JSON (rc={proc.returncode}): "
            f"{e}\nstdout[:300]={proc.stdout[:300]!r}\n"
            f"stderr[:300]={proc.stderr[:300]!r}")
    return ClaudeRun(
        raw=raw,
        home=home if home is not None else Path.home(),
        session_id=raw.get("session_id", ""),
        denials=[d.get("tool_name", "") for d in raw.get("permission_denials", [])],
        result=raw.get("result") or "",
    )


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def probe_claude(home: Path, cwd: Path, timeout: int = 90) -> str | None:
    """``None`` when a one-shot ``claude -p`` works with ``HOME=home``,
    else why not (for a skip). Claude authenticates with no home of its
    own only through ``CLAUDE_CODE_OAUTH_TOKEN`` in the environment."""
    env = dict(os.environ, HOME=str(home))
    env.pop("CLAUDE_CONFIG_DIR", None)
    try:
        p = subprocess.run(["claude", "-p", "reply with exactly: OK", "--max-turns", "1",
                            "--setting-sources", "project,local"],
                           cwd=str(cwd), env=env, capture_output=True, text=True,
                           timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"claude probe with a fake HOME failed: {type(e).__name__}"
    if p.returncode != 0:
        return ("claude does not authenticate with a fake HOME (export "
                "CLAUDE_CODE_OAUTH_TOKEN, e.g. run under build_session_env): "
                f"rc={p.returncode} {p.stderr[:160]!r}")
    return None
