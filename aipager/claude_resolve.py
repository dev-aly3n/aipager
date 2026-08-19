"""Single source of truth for resolving the `claude` binary + auth shape.

Six call sites used to resolve the binary independently
(``inject.py``, ``preflight.py``, ``doctor.py``, ``dtach/launcher.py``,
``wizard/settings_patch.py``, ``tests/e2e/harness.py``) and could
disagree with each other, because nothing forced agreement. This module
is the one place that walks the precedence chain, verifies each
candidate, and dedups by realpath — every caller gets the same answer
for the same ``PATH``/``claude_path``/``AIPAGER_CLAUDE_BIN``.

Deliberately **not** folded into :mod:`aipager.preflight` (whose whole
contract is "print a friendly error and exit 2") or
:mod:`aipager.claude_bootstrap` (which must never raise). This module is
policy-free: it returns a result or raises :class:`ClaudeNotFoundError`,
and each caller decides what to do about it.

Precedence (tiers 1-2 are absolute — they win outright when they
verify; tiers 3-5 are compared by version):

1. ``claude_path`` from ``aipager.yaml`` (:func:`aipager.scope.load_claude_path`)
2. ``$AIPAGER_CLAUDE_BIN``
3. ``~/.local/bin/claude``
4. every ``claude`` found by walking ``$PATH`` directory-by-directory
5. fixed Homebrew prefixes (existence-filtered, not a discovery
   mechanism): ``/opt/homebrew/bin``, ``/usr/local/bin``,
   ``/home/linuxbrew/.linuxbrew/bin``

A tier-1/2 override that does not verify logs a warning and falls
through to the lower tiers — a stale override degrades the diagnostic,
never breaks a launch.

No filename/suffix filtering, ever: ``/usr/bin/claude`` resolving to
something named ``…/claude.exe`` is a real ELF Linux binary shipped by
npm on some installs, and a ``.exe`` rule would wrongly reject it.

Verification runs ``<candidate> --version`` with a 3s timeout and
accepts only exit 0 with output starting with ``<major>.<minor>.<patch>
(Claude Code)``. Candidates are then realpath-deduped: the
highest-precedence path per unique realpath survives, so a machine
where ``/bin`` symlinks to ``/usr/bin`` never reports two installs for
one binary.

Auth detection (:func:`detect_auth`) shells out to ``claude auth
status``, which emits JSON regardless of exit code on Claude Code
2.1.41+. **It must always be called with the same environment the real
session launch uses** (:func:`aipager.daemon_secrets.build_session_env`)
— under a systemd unit's ``LoadCredential=``, the daemon's own
``os.environ`` never contains the token, so probing against it would
falsely report "not logged in" while sessions authenticate fine. A
probe failure (timeout, missing binary, malformed JSON) is reported as
``auth_method="unknown"`` — **never** ``"none"``. Conflating "the probe
failed" with "confirmed no auth" is exactly the fail-open-reused-as-
fail-closed mistake this module exists to avoid; see
:mod:`aipager.dtach.inject`'s ``_credentials_file_is_fresh`` for the
history of that mistake, and the project's rule that the feature this
module supports must **never refuse to launch on "no auth"**.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import secrets
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)\s*\(Claude Code\)")
_AUTH_MIN_VERSION = (2, 1, 41)
_AUTH_METHODS = frozenset({"none", "oauth_token", "api_key", "third_party"})
_ENV_AUTH_VARS = (
    "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK",
    "AWS_BEARER_TOKEN_BEDROCK", "ANTHROPIC_AUTH_TOKEN",
)
_HOMEBREW_PREFIXES = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/home/linuxbrew/.linuxbrew/bin",
)
_VERSIONS_DIR_MARKER = str(
    Path.home() / ".local" / "share" / "claude" / "versions"
)


@dataclass(frozen=True)
class ClaudeInstall:
    path: str
    realpath: str
    version: str


@dataclass(frozen=True)
class ResolvedClaude:
    chosen: ClaudeInstall
    others: tuple[ClaudeInstall, ...] = ()


class ClaudeNotFoundError(RuntimeError):
    """No candidate verified. The message lists every candidate tried
    and why each one failed (missing, not executable, ``--version``
    non-zero, unparseable output)."""


@dataclass(frozen=True)
class AuthStatus:
    logged_in: bool
    auth_method: str   # none | oauth_token | api_key | third_party | unknown
    source: str         # env | file | keychain | unknown | probe-failed | version-gated
    error: str | None = None


# Process-level memo. Cold CLI processes are fresh per invocation, so
# "once per process" already means "fresh every command"; the daemon
# picks up a binary retarget on its next restart, matching today's
# frozen-module-constant behaviour (not a regression).
_memo: ResolvedClaude | None = None
_memo_error: ClaudeNotFoundError | None = None


def _semver_tuple(version: str) -> tuple[int, int, int]:
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", version or "")
    if not m:
        return (0, 0, 0)
    a, b, c = m.groups()
    return (int(a), int(b), int(c))


def _candidate_paths() -> list[tuple[str, int]]:
    """Return ``(path, tier)`` pairs in precedence order (tiers 1-5).

    Existence is not checked here — verification handles that. Literal
    duplicate paths (e.g. the same directory appearing twice on
    ``$PATH``) are collapsed to their first (highest-precedence)
    occurrence; realpath-level dedup happens after verification.
    """
    from aipager import scope as _scope

    candidates: list[tuple[str, int]] = []
    seen: set[str] = set()

    def _add(path: str, tier: int) -> None:
        if path and path not in seen:
            seen.add(path)
            candidates.append((path, tier))

    try:
        # `path=` passed explicitly, read from the module at call time —
        # `load_claude_path`'s `path: Path = CONFIG_PATH` default is
        # bound once at aipager.scope's import time, so relying on it
        # would silently ignore a later `_scope.CONFIG_PATH` repoint
        # (every test in this suite does this via the autouse
        # `_isolate_wizard_config` fixture) and read the operator's real
        # config instead. Same trap `config._load_miniapp()` documents.
        configured = _scope.load_claude_path(path=_scope.CONFIG_PATH)
    except Exception:
        configured = ""
    if configured:
        _add(configured, 1)

    env_override = os.environ.get("AIPAGER_CLAUDE_BIN", "").strip()
    if env_override:
        _add(env_override, 2)

    _add(str(Path.home() / ".local" / "bin" / "claude"), 3)

    path_env = os.environ.get("PATH", "")
    for directory in path_env.split(os.pathsep):
        if not directory:
            continue
        _add(str(Path(directory) / "claude"), 4)

    for prefix in _HOMEBREW_PREFIXES:
        _add(str(Path(prefix) / "claude"), 5)

    return candidates


def _verify_candidate(path: str) -> tuple[ClaudeInstall | None, str]:
    """Verify one candidate path. Returns ``(install_or_None, why_not)``."""
    p = Path(path)
    if p.is_dir():
        return None, "is a directory"
    if not p.exists():
        return None, "not found"
    try:
        r = subprocess.run(
            [path, "--version"],
            capture_output=True, text=True, timeout=3.0,
        )
    except FileNotFoundError:
        return None, "not found"
    except PermissionError:
        return None, "not executable"
    except subprocess.TimeoutExpired:
        return None, "--version timed out"
    except OSError as e:
        return None, f"exec failed: {e}"
    if r.returncode != 0:
        return None, f"--version exited {r.returncode}"
    out = (r.stdout or "").strip()
    m = _VERSION_RE.match(out)
    if not m:
        return None, f"unparseable --version output: {out[:80]!r}"
    version = f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
    return ClaudeInstall(path=path, realpath=os.path.realpath(path),
                         version=version), ""


def _rank_key(item: tuple[ClaudeInstall, int, int]) -> tuple:
    """Sort key for choosing among tier 3-5 candidates: highest semver
    wins; tie → prefer a path under ~/.local/share/claude/versions/;
    further tie → first discovered (smaller original order)."""
    install, _tier, order = item
    under_versions_dir = _VERSIONS_DIR_MARKER in install.path
    return (_semver_tuple(install.version), under_versions_dir, -order)


def resolve_claude_binary(*, force: bool = False) -> ResolvedClaude:
    """Resolve the `claude` binary. Raises :class:`ClaudeNotFoundError`
    if nothing verifies. Memoized per process; ``force=True`` bypasses
    (and refreshes) the memo."""
    global _memo, _memo_error
    if not force:
        if _memo is not None:
            return _memo
        if _memo_error is not None:
            raise _memo_error

    candidates = _candidate_paths()
    verified: list[tuple[ClaudeInstall, int, int]] = []
    failures: list[str] = []
    for order, (path, tier) in enumerate(candidates):
        install, why = _verify_candidate(path)
        if install is None:
            failures.append(f"{path}: {why}")
            if tier in (1, 2):
                kind = "claude_path" if tier == 1 else "AIPAGER_CLAUDE_BIN"
                log.warning(
                    "configured %s=%s does not verify (%s) — falling "
                    "through to the next candidate", kind, path, why,
                )
            continue
        verified.append((install, tier, order))

    if not verified:
        err = ClaudeNotFoundError(
            "no working `claude` binary found. Tried:\n"
            + "\n".join(f"  - {f}" for f in failures)
        )
        if not force:
            _memo_error = err
            _memo = None
        raise err

    # Realpath-dedup: keep the highest-precedence (lowest tier, then
    # first-discovered) entry per unique realpath.
    best_by_realpath: dict[str, tuple[ClaudeInstall, int, int]] = {}
    for item in verified:
        _install, tier, order = item
        existing = best_by_realpath.get(_install.realpath)
        if existing is None or (tier, order) < (existing[1], existing[2]):
            best_by_realpath[_install.realpath] = item
    deduped = list(best_by_realpath.values())

    tier1 = [d for d in deduped if d[1] == 1]
    tier2 = [d for d in deduped if d[1] == 2]
    if tier1:
        winner = tier1[0]
    elif tier2:
        winner = tier2[0]
    else:
        winner = max(deduped, key=_rank_key)

    winner_install = winner[0]
    others = tuple(sorted(
        (d[0] for d in deduped if d[0].realpath != winner_install.realpath),
        key=lambda i: _semver_tuple(i.version), reverse=True,
    ))
    result = ResolvedClaude(chosen=winner_install, others=others)
    if not force:
        _memo = result
        _memo_error = None
    return result


def try_resolve_claude_binary(*, force: bool = False) -> ResolvedClaude | None:
    """Same as :func:`resolve_claude_binary` but returns ``None`` instead
    of raising."""
    try:
        return resolve_claude_binary(force=force)
    except ClaudeNotFoundError:
        return None


def _attribute_source(env: dict[str, str]) -> str:
    """Cosmetic-only best-effort guess at *where* a confirmed login lives.

    Never gates a decision — see the module docstring. Honours
    ``CLAUDE_CONFIG_DIR`` (unlike the legacy, deliberately-not-extended
    ``_credentials_file_is_fresh``).
    """
    if any(env.get(v) for v in _ENV_AUTH_VARS):
        return "env"
    config_dir = env.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    if (Path(config_dir) / ".credentials.json").exists():
        return "file"
    if platform.system().lower() == "darwin":
        return "keychain"
    return "unknown"


def detect_auth(
    claude_path: str, version: str, env: dict[str, str], *, timeout: float = 5.0,
) -> AuthStatus:
    """Run ``claude auth status`` in *env* and classify the result.

    Never raises. A probe failure (timeout, missing binary, malformed
    JSON, any OSError) is reported as ``auth_method="unknown"``,
    ``source="probe-failed"`` — never ``"none"``. Below Claude Code
    2.1.41 the probe is skipped entirely (that version is when ``auth
    status`` started emitting JSON) and the result is
    ``source="version-gated"``.
    """
    if _semver_tuple(version) < _AUTH_MIN_VERSION:
        return AuthStatus(
            logged_in=False, auth_method="unknown", source="version-gated",
            error="binary predates 2.1.41 — upgrade to check",
        )
    try:
        r = subprocess.run(
            [claude_path, "auth", "status"],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return AuthStatus(False, "unknown", "probe-failed", error="probe timed out")
    except FileNotFoundError:
        return AuthStatus(False, "unknown", "probe-failed", error="binary not found")
    except OSError as e:
        return AuthStatus(False, "unknown", "probe-failed", error=str(e))

    try:
        data = json.loads(r.stdout)
    except (json.JSONDecodeError, TypeError):
        return AuthStatus(
            False, "unknown", "probe-failed",
            error=f"unparseable output (exit {r.returncode})",
        )
    if not isinstance(data, dict):
        return AuthStatus(False, "unknown", "probe-failed",
                          error="malformed JSON (not an object)")

    logged_in = data.get("loggedIn") is True
    method = data.get("authMethod")
    if method not in _AUTH_METHODS:
        method = "unknown"
    source = _attribute_source(env) if logged_in else "unknown"
    return AuthStatus(logged_in=logged_in, auth_method=method, source=source)


def format_provenance(resolved: ResolvedClaude, auth: AuthStatus) -> list[str]:
    """Render the daemon-startup provenance line(s).

    Main line first (``claude: <path> (<version>) · auth: ...``), then
    one ``also found: ...`` line per other distinct install,
    version-descending. Empty when there is only one install.
    """
    chosen = resolved.chosen
    if auth.source == "version-gated":
        auth_str = f"auth: unknown ({auth.error})"
    elif auth.source == "probe-failed":
        auth_str = f"auth: unknown (status probe failed: {auth.error})"
    elif not auth.logged_in:
        auth_str = "auth: none (not logged in)"
    else:
        auth_str = f"auth: {auth.auth_method} ({auth.source})"

    lines = [f"claude: {chosen.path} ({chosen.version}) · {auth_str}"]
    for other in resolved.others:
        lines.append(
            f"also found: {other.path} ({other.version}) "
            "— set claude_path to override"
        )
    return lines


@dataclass(frozen=True)
class CredentialCheck:
    """Result of actually *using* the credential, not just finding one.

    ``state`` is one of:

    ``valid``     the credential works.
    ``rejected``  a credential was present and the API refused it —
                  expired, revoked, or malformed. This is the case
                  ``claude auth status`` cannot see at all.
    ``absent``    Claude reports no credential configured.
    ``unknown``   we could not tell: the probe timed out, the machine is
                  offline, the binary vanished, or Claude said something
                  we do not recognise. **Always treated as silence.**
    """

    state: str
    detail: str | None = None


# Matched against the probe's combined output. Deliberately narrow: an
# unrecognised failure must fall through to "unknown" and stay silent
# rather than be guessed at as a credential problem.
_REJECTED_RE = re.compile(
    r"(\b401\b|Failed to authenticate|OAuth (access )?token is invalid"
    r"|Invalid API key|authentication_error|invalid_api_key)",
    re.IGNORECASE,
)
_ABSENT_RE = re.compile(r"(Not logged in|Please run /login)", re.IGNORECASE)

# The happy path measured ~3s. 20s leaves generous headroom without
# letting an unreachable API (which does not fail fast — it hangs and
# retries) hold the check open indefinitely.
_PROBE_TIMEOUT = 20.0

# Cheapest real round-trip: print mode, smallest model, one word out.
# The `haiku` ALIAS rather than a pinned model id, so a model retirement
# cannot silently turn every probe into "unknown".
_PROBE_ARGS = ("-p", "say ok", "--model", "haiku")


_PROBE_DIR_PREFIX = "aipager-authprobe-"


def _make_probe_dir() -> str:
    """Create the throwaway cwd the probe runs in.

    Hex-only suffix on purpose: ``tempfile.mkdtemp``'s own alphabet
    includes ``_``, which Claude's project slugification turns into
    ``-``, so a raw mkdtemp name would make the transcript directory
    ambiguous and cleanup would miss it. Hex survives slugification
    unchanged, so the directory we compute is the directory Claude
    writes.

    Separate function so a test can make the probe run somewhere
    predictable and prove the cleanup guards are load-bearing.
    """
    path = os.path.join(
        tempfile.gettempdir(), f"{_PROBE_DIR_PREFIX}{secrets.token_hex(8)}")
    os.mkdir(path, 0o700)
    return path


def _probe_project_dir(cwd: str) -> Path:
    """Where Claude Code will write this probe's transcript.

    Claude slugifies the working directory into ``~/.claude/projects``.
    The rule is "every character that is not alphanumeric becomes a
    hyphen" — measured, not assumed: cwd
    ``/tmp/aipager-slug.test_dir-x.y`` produces
    ``-tmp-aipager-slug-test-dir-x-y``. An earlier version of this
    function replaced only ``/``, which silently computed the wrong
    directory whenever the path contained ``.`` or ``_`` — including
    ``tempfile.mkdtemp``'s own random suffix, which draws from an
    alphabet that includes ``_``. Cleanup then no-opped and transcripts
    accumulated forever, ~1 start in 8.
    """
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    return Path.home() / ".claude" / "projects" / slug


def _run_probe(claude_path: str, env: dict[str, str], cwd: str,
               timeout: float) -> tuple[int, str]:
    """Spawn the probe. Isolated as the single seam every test patches —
    nothing in the suite may ever really run this (it costs money)."""
    r = subprocess.run(
        [claude_path, *_PROBE_ARGS],
        capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd,
    )
    return r.returncode, f"{r.stdout}\n{r.stderr}"


def validate_credential(
    claude_path: str, env: dict[str, str], *, timeout: float = _PROBE_TIMEOUT,
) -> CredentialCheck:
    """Actually use the credential once, and classify what happened.

    ``claude auth status`` only reports that a credential *exists*: a
    revoked token still answers ``{"loggedIn": true}``. That blind spot
    is the expired-token case, where every session silently parks on the
    login screen and hangs BUSY forever — so this spends one tiny
    round-trip to learn the truth.

    Runs ``claude`` itself rather than a hand-rolled HTTP call, because
    only Claude knows how to present whichever credential is in play:
    env token, credentials file, macOS Keychain, subscription login,
    Bedrock/Vertex. No single endpoint guess covers those.

    Never raises. Anything ambiguous is ``unknown``, which callers must
    treat as "say nothing" — warning an offline operator that their
    credential is bad, on every restart, would be worse than silence.
    """
    try:
        tmpdir = _make_probe_dir()
    except OSError as e:
        # Creating the throwaway cwd can fail on its own: a full disk, a
        # read-only or sandboxed /tmp, a hostile umask. This is OUTSIDE
        # the try/finally below (there is nothing to clean up yet), so
        # without this it would escape a function whose contract is
        # "never raises" — and `doctor.check_claude_auth` has no wrapper
        # anywhere up to `cmd_doctor`, so it would take down the whole
        # `aipager doctor` command instead of degrading to one grey row.
        return CredentialCheck("unknown", f"could not create probe dir: {e}")
    try:
        try:
            code, output = _run_probe(claude_path, env, tmpdir, timeout)
        except subprocess.TimeoutExpired:
            return CredentialCheck("unknown", "probe timed out")
        except FileNotFoundError:
            return CredentialCheck("unknown", "binary not found")
        except OSError as e:
            return CredentialCheck("unknown", str(e))
        except AssertionError:
            # tests/conftest.py's _no_real_credential_probe raises this
            # so an unmocked test fails loudly instead of quietly
            # spending the operator's money. Swallowing it into
            # "unknown" would disarm that guard completely. Production
            # never raises AssertionError from a subprocess call.
            raise
        except Exception as e:                      # never raise
            return CredentialCheck("unknown", str(e))

        if code == 0:
            return CredentialCheck("valid")
        # Rejection is checked FIRST: Claude's "Invalid API key" message
        # also invites you to /login, so matching "absent" first would
        # file an expired credential under "nothing configured" and send
        # the operator hunting for a file that is sitting right there.
        if _REJECTED_RE.search(output):
            return CredentialCheck("rejected", "credential refused by the API")
        if _ABSENT_RE.search(output):
            return CredentialCheck("absent", "claude reports no credential")
        return CredentialCheck("unknown", f"exit {code}")
    finally:
        _cleanup_probe_artifacts(tmpdir)


def _cleanup_probe_artifacts(tmpdir: str) -> None:
    """Remove the temp cwd and the transcript Claude wrote for it.

    Guarded on the path actually living under ``~/.claude/projects`` and
    carrying this probe's own unique temp-directory name, so a bug here
    can never delete one of the operator's real project histories.
    """
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass
    try:
        project = _probe_project_dir(tmpdir)
        projects_root = Path.home() / ".claude" / "projects"
        if (project.parent == projects_root
                and _PROBE_DIR_PREFIX in project.name
                and project.is_dir()):
            shutil.rmtree(project, ignore_errors=True)
    except Exception:
        pass


def _with_article(kind: str) -> str:
    return f"{'an' if kind[:1].lower() in 'aeiou' else 'a'} {kind}"


def format_auth_notice(found_kinds: list[str], *, rejected: bool = False) -> str:
    """Render the ONLY thing this daemon ever says to Telegram about auth.

    Deliberately says nothing about the machine. ``format_provenance``
    above — absolute binary paths, versions, every install found — is for
    the local journal and ``aipager doctor``, both of which stay on the
    operator's own machine. A Telegram message does not: it is stored on
    Telegram's servers, forwardable, screenshottable, and in a team scope
    it reaches every member of every configured group. Naming the home
    directory there leaks the OS username and the machine's layout to an
    audience that has no use for either.

    So this reports only the *kind* of credential found — enough for the
    operator to know which thing to go fix — and never where it lives,
    which version anything is, or what it is called on disk. Anything
    added here must keep that property; ``tests/test_auth_notice.py``
    asserts it against the rendered text.

    *found_kinds* is the de-duplicated list of human-readable credential
    kinds the recovery sweep turned up, in discovery order.
    """
    fix = "Run `claude auth login` on the machine running aipager."
    if rejected:
        # Distinct from "nothing found": something IS configured, it just
        # doesn't work any more. Expiry is by far the likeliest cause and
        # naming it saves the operator hunting for a missing file that is
        # sitting right where it should be.
        what = _with_article(found_kinds[0]) if found_kinds else "a credential"
        return (
            f"\u26a0\ufe0f Claude rejected {what} — it has probably expired "
            "or been revoked.\n\n" + fix
        )
    if not found_kinds:
        return (
            "\u26a0\ufe0f Claude isn't logged in, and I couldn't find a "
            "credential anywhere I know to look.\n\n" + fix
        )
    phrases = [_with_article(k) for k in found_kinds]
    if len(phrases) == 1:
        what = phrases[0]
    else:
        what = ", ".join(phrases[:-1]) + f" and {phrases[-1]}"
    return (
        f"\u26a0\ufe0f Claude isn't logged in, though I did find {what} on "
        "this machine — Claude isn't picking it up.\n\n" + fix
    )


__all__ = [
    "AuthStatus", "ClaudeInstall", "ClaudeNotFoundError", "ResolvedClaude",
    "CredentialCheck", "detect_auth", "format_auth_notice",
    "format_provenance", "validate_credential",
    "resolve_claude_binary",
    "try_resolve_claude_binary",
]
