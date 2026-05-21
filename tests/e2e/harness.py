"""Shared helpers for the real-Claude E2E safety suite.

These tests reproduce a *Telegram-driven turn* without Telegram: write the
per-session policy snapshot the daemon would write, wire the real
``aipager-hook`` into a throwaway Claude project, and run real Claude via
``claude -p`` with a ``[via Telegram · @e2e]``-marked prompt. The hook then
enforces exactly as it does in production.

Assertions are outcome-based (robust to Claude's nondeterminism): a blocked
scenario asserts the forbidden result never happened + the tool was denied;
an allowed scenario asserts the expected output appeared.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from aipager import policy as _policy
from aipager import policy_snapshot as _snap
from aipager.scope import Member, Scope

MARKER = "[via Telegram · @e2e]"


# ---- discovery -----------------------------------------------------------

def claude_bin() -> str | None:
    return shutil.which("claude")


def aipager_hook_bin() -> str | None:
    """The real aipager-hook console script."""
    found = shutil.which("aipager-hook")
    if found:
        return found
    cand = Path(sys.prefix) / "bin" / "aipager-hook"
    return str(cand) if cand.exists() else None


def new_session() -> str:
    """Unique session name so the live daemon's hook UDP (to a session it
    doesn't track) is a harmless no-op."""
    return f"claude-e2e-{uuid.uuid4().hex[:8]}"


# ---- project + snapshot setup -------------------------------------------

def make_project(tmp_path: Path, *, events=("PreToolUse",)) -> Path:
    """A temp Claude project wiring the real aipager-hook on ``events``
    (all tools). Seeds a couple of readable files for benign tests."""
    hook = aipager_hook_bin()
    assert hook, "aipager-hook not found"
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    hooks = {ev: [{"hooks": [{"type": "command", "command": hook}]}]
             for ev in events}
    (proj / ".claude" / "settings.json").write_text(
        json.dumps({"hooks": hooks}), encoding="utf-8")
    (proj / "README.md").write_text("E2E_README_SENTINEL: hello world\n")
    return proj


def write_snapshot(session: str, *, role_name: str = "user",
                   deny_tools=(), allow_tools=()) -> None:
    """Write the per-session policy snapshot for the given role + per-user
    overrides — exactly what the daemon writes on a Telegram prompt."""
    pol = _policy.load_policy()
    role = pol.get_role(role_name)
    member = Member(id=999, label="e2e", role=role_name,
                    deny_tools=tuple(deny_tools), allow_tools=tuple(allow_tools))
    scope = Scope(chat_id=999, kind="dm", label="e2e DM", members=(member,))
    _snap.write_snapshot(session, role, scope, member)


def clear_snapshot(session: str) -> None:
    _snap.clear_snapshot(session)


# ---- run + parse ---------------------------------------------------------

@dataclass
class ClaudeRun:
    raw: dict
    session_id: str = ""
    denials: list[str] = field(default_factory=list)
    result: str = ""

    @property
    def transcript(self) -> Path | None:
        if not self.session_id:
            return None
        return next(Path.home().glob(
            f".claude/projects/*/{self.session_id}.jsonl"), None)

    def tool_result_texts(self) -> list[str]:
        """All tool_result payloads from the transcript (for deny-reason
        inspection)."""
        t = self.transcript
        if not t:
            return []
        out: list[str] = []
        for line in t.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = (e.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    if isinstance(c, str):
                        out.append(c)
                    elif isinstance(c, list):
                        out.extend(str(p.get("text", "")) if isinstance(p, dict)
                                   else str(p) for p in c)
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
        hay = self.result
        for n in needles:
            assert n not in hay, (
                f"value {n!r} leaked into result despite the safety policy:\n"
                f"{self.result[:400]!r}")

    def assert_output_contains(self, needle: str) -> None:
        assert needle in self.result, (
            f"expected {needle!r} in result; got {self.result[:400]!r}")

    def assert_safety_block_recorded(self) -> None:
        joined = " ".join(self.tool_result_texts())
        assert "aipager safety policy" in joined, (
            "no 'aipager safety policy' deny recorded in transcript")

    def assert_no_regex_in_reasons(self) -> None:
        for txt in self.tool_result_texts():
            if "aipager safety policy" in txt:
                assert "\\b" not in txt and "/\\" not in txt, (
                    f"deny reason leaked a raw regex: {txt!r}")


def run(task: str, *, session: str, project: Path,
        marker: bool = True, timeout: int = 300) -> ClaudeRun:
    prompt = (f"{MARKER}\n{task}" if marker else task)
    env = dict(os.environ, CLAUDE_DTACH_SESSION=session)
    # --dangerously-skip-permissions disables Claude's OWN interactive
    # permission prompts (which auto-deny in -p mode and would otherwise
    # confound "allowed" cases). PreToolUse hooks still fire regardless,
    # so the aipager safety hook remains the sole gate under test — which
    # mirrors how the daemon runs sessions.
    proc = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "json",
         "--dangerously-skip-permissions"],
        cwd=str(project), env=env, capture_output=True, text=True,
        timeout=timeout,
    )
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise AssertionError(
            f"claude -p produced no JSON (rc={proc.returncode}): "
            f"{e}\nstdout[:300]={proc.stdout[:300]!r}\n"
            f"stderr[:300]={proc.stderr[:300]!r}")
    return ClaudeRun(
        raw=raw,
        session_id=raw.get("session_id", ""),
        denials=[d.get("tool_name", "") for d in raw.get("permission_denials", [])],
        result=raw.get("result") or "",
    )
