"""See :mod:`aipager.wizard` for the package overview."""

from __future__ import annotations

import os

import questionary

from aipager.errors import friendly_error, friendly_warn
from aipager.ui import GLYPH_OK, console, err_console, hint, ok, rule, step
from aipager.wizard._constants import (
    CONFIG_DIR, CONFIG_ENV, _CHAT_NOT_FOUND_RE, _PROMPT_STYLE,
)

from aipager.wizard.display import (
    _ask,
    _spin,
)
from aipager.wizard.settings_patch import (
    _step_deps,
    _step_settings,
)
from aipager.wizard.team_setup import (
    _show_team_warning_panel,
)
from aipager.wizard.telegram_api import (
    _fetch_id_from_updates,
    _normalize_token,
    _test_send,
    _verify_token,
)


def _step_token(step_label: str = "[1/5]") -> tuple[str, str]:
    step(f"{step_label}  Telegram bot")
    hint("Get a token from @BotFather (https://t.me/BotFather)")
    while True:
        raw = _ask(questionary.text(
            "Paste your bot token:",
            qmark="?",
            style=_PROMPT_STYLE,
        ))
        token = _normalize_token(raw)
        if not token:
            err_console.print("  [err]empty — try again[/err]")
            continue
        with _spin("Verifying with Telegram…"):
            info = _verify_token(token)
        if info is None:
            err_console.print("  [hint]Try again or Ctrl-C to exit.[/hint]")
            continue
        username = info.get("username") or "your_bot"
        ok(f"Verified — @{username}")
        return token, username


def _step_chat_id(
    token: str, bot_username: str,
    *, mode: str = "personal", step_label: str = "[2/5]",
) -> int:
    """Capture the chat ID (DM for personal mode, group for team mode).

    ``mode`` decides the auto-detect target and the prompt copy:
      - ``"personal"`` — looks for a private chat; nudges the user
        to DM the bot.
      - ``"team"`` — looks for a group / supergroup; nudges the user
        to add the bot to a group and send /start there.

    ``step_label`` is the wizard's progress prefix (e.g. ``[3/7]``)
    so the caller can keep step numbers consistent regardless of mode.
    """
    if mode == "team":
        step(f"{step_label}  Group chat ID")
        auto_label = "Auto-detect (I'll add bot to the group + /start)"
        manual_label = "Paste group chat id (negative integer)"
        spinner_msg = "Checking for the group's /start…"
        detect_hint = (
            f"Add @{bot_username} to your group, then send /start there."
        )
    else:
        step(f"{step_label}  Your chat ID")
        auto_label = "Auto-detect (I'll DM the bot first)"
        manual_label = "Paste manually"
        spinner_msg = "Checking for your DM…"
        detect_hint = (
            f"DM the bot here, then continue: https://t.me/{bot_username}"
        )

    while True:
        method = _ask(questionary.select(
            "How should we find the chat id?",
            choices=[
                questionary.Choice(auto_label, value="auto"),
                questionary.Choice(manual_label, value="manual"),
            ],
            qmark="?",
            style=_PROMPT_STYLE,
        ))
        if method == "manual":
            raw = _ask(questionary.text(
                "Chat id (integer):", qmark="?", style=_PROMPT_STYLE,
            )).strip()
            try:
                cid = int(raw)
            except ValueError:
                err_console.print("  [err]not a number[/err]")
                continue
            if mode == "team" and cid >= 0:
                err_console.print(
                    "  [err]Group IDs are negative integers. Try again.[/err]",
                )
                continue
        else:
            hint(detect_hint)
            _ask(questionary.confirm(
                "Sent — continue?",
                default=True, qmark="?", style=_PROMPT_STYLE,
            ))
            with _spin(spinner_msg):
                found_id, who, advisory = _fetch_id_from_updates(
                    token, want=("group" if mode == "team" else "dm"),
                )
            if found_id is None:
                if advisory:
                    err_console.print(f"  [err]{advisory}[/err]")
                else:
                    target = "group /start" if mode == "team" else "DM"
                    err_console.print(
                        f"  [err]No {target} detected — try again.[/err]"
                    )
                continue
            cid = found_id
            who_label = f"@{who}" if who else "(no title/handle)"
            ok(f"Detected chat_id={cid} {who_label}")

        with _spin("Sending a test message…"):
            sent, err = _test_send(token, cid)

        if sent:
            ok(f"chat_id={cid} — test message delivered.")
            confirmed = _ask(questionary.confirm(
                "Did the test message arrive in your Telegram?",
                default=True, qmark="?", style=_PROMPT_STYLE,
            ))
            if confirmed:
                return cid
            hint("Let's try again — the message went somewhere unexpected.")
            continue

        if _CHAT_NOT_FOUND_RE.search(err):
            err_console.print(f"  [err]Telegram says: {err}[/err]")
            hint(f"You haven't started @{bot_username} yet — open it:")
            hint(f"  https://t.me/{bot_username}")
            _ask(questionary.confirm(
                "I've tapped Start in Telegram — retry?",
                default=True, qmark="?", style=_PROMPT_STYLE,
            ))
            with _spin("Retrying test send…"):
                sent2, err2 = _test_send(token, cid)
            if sent2:
                ok(f"chat_id={cid} — test message delivered.")
                return cid
            err_console.print(f"  [err]Still failing: {err2}[/err]")
            continue

        err_console.print(f"  [err]Test send failed: {err}[/err]")


def _step_write_env(token: str, chat_id: int, step_label: str = "[5/5]") -> None:
    step(f"{step_label}  Write config")
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise OSError(f"cannot create {CONFIG_DIR}: {e}") from e

    if CONFIG_ENV.exists():
        try:
            existing = CONFIG_ENV.read_text()
        except OSError:
            existing = ""
        if f"CLAUDE_TG_BOT_TOKEN={token}" not in existing or \
           f"CLAUDE_TG_CHAT_ID={chat_id}" not in existing:
            answer = _ask(questionary.confirm(
                f"{CONFIG_ENV} already has different settings. Overwrite?",
                default=False, qmark="?", style=_PROMPT_STYLE,
            ))
            if not answer:
                friendly_warn("Keeping existing config; new token not written.")
                return

    try:
        CONFIG_ENV.write_text(
            f"CLAUDE_TG_BOT_TOKEN={token}\nCLAUDE_TG_CHAT_ID={chat_id}\n"
        )
    except OSError as e:
        raise OSError(f"cannot write {CONFIG_ENV}: {e}") from e
    try:
        os.chmod(CONFIG_ENV, 0o600)
    except OSError:
        friendly_warn(
            f"Could not chmod 0600 on {CONFIG_ENV} — non-POSIX filesystem?",
            "  Your token file is readable by other users on this machine.",
        )
    ok(f"Wrote {CONFIG_ENV}")


def _completion_screen() -> None:
    """Show the post-setup summary in a panel (or plain text off-TTY)."""
    from rich.panel import Panel

    lines = [
        f"[ok]{GLYPH_OK}[/ok]  Setup complete.",
        "",
        "  Start the daemon:    [path]aipager start[/path]",
        "  Launch a session:    [path]aipager session dev[/path]",
        "  Health check:        [path]aipager doctor[/path]",
    ]
    body = "\n".join(lines)
    if console.is_terminal:
        console.print()
        console.print(Panel(body, border_style="ok", expand=False,
                            padding=(0, 1)))
    else:
        console.print()
        console.print(f"{GLYPH_OK} Setup complete.")
        console.print()
        console.print("  Start the daemon:    aipager start")
        console.print("  Launch a session:    aipager session dev")
        console.print("  Health check:        aipager doctor")


def _step_pick_mode(step_label: str = "[2/N]") -> str:
    """Ask Personal vs Team. Returns ``"personal"`` or ``"team"``.

    Picking Team triggers the trust-warning panel and a hard-stop
    confirm; declining the confirm collapses back to Personal.
    """
    step(f"{step_label}  Personal or Team")

    pick = _ask(questionary.select(
        "How will you use aipager?",
        choices=[
            questionary.Choice("Personal (1:1 DM with the bot — recommended)",
                               value="personal"),
            questionary.Choice("Team (group chat with multiple devs)",
                               value="team"),
        ],
        # `default` must match a Choice ``value``, not its title.
        default="personal",
        qmark="?", style=_PROMPT_STYLE,
    ))

    if pick == "personal":
        ok("Personal mode — no team.yaml needed.")
        return "personal"

    _show_team_warning_panel()
    proceed = _ask(questionary.confirm(
        "Continue with team-mode setup?",
        default=False, qmark="?", style=_PROMPT_STYLE,
    ))
    if not proceed:
        friendly_warn("Team setup cancelled. Falling back to personal mode.")
        return "personal"
    return "team"


def _grant_owner_step(chat_id: int, step_label: str = "[3/4]") -> str:
    """Ask whether to make the operator's own account ``owner``.

    Owner bypasses everything (incl. the hard-safety boundary), so the
    grant is gated behind an explicit confirmation (security §3.7c).
    Declining falls back to ``admin`` — full control, but the safety
    floor still applies. Returns the chosen role name.
    """
    step(f"{step_label}  Owner access")
    console.print()
    console.print(
        "[warn]⚠[/warn]  Granting [path]owner[/path] gives this account "
        "unrestricted control from Telegram:"
    )
    console.print(
        "[muted]   daemon manipulation · reading every file · nested "
        "claude · config edits — the safety boundary does not apply.[/muted]"
    )
    console.print(
        "[muted]   Grant only to yourself or someone you'd hand an SSH "
        "key. (Declining → [/muted][path]admin[/path][muted]: full "
        "control, but the safety floor still applies.)[/muted]"
    )
    grant = _ask(questionary.confirm(
        "Make your account owner?",
        default=True, qmark="?", style=_PROMPT_STYLE,
    ))
    return "owner" if grant else "admin"


def _commit_owner_dm(token: str, chat_id: int, role: str) -> None:
    """Write the operator's DM scope to ``aipager.yaml`` (token + scope
    hit disk together — the early-commit resilience guarantee)."""
    from aipager.scope import Member, Scope
    from aipager.wizard.scope_io import commit_scope

    scope = Scope(
        chat_id=chat_id, kind="dm", label="owner DM",
        members=(Member(id=chat_id, label="owner", role=role),),
    )
    commit_scope(scope, token)
    ok(f"Wrote aipager.yaml — your DM, role {role}.")
    if role == "owner":
        try:
            from aipager import audit
            audit.append(session="(config)", label="owner",
                         action="grant-owner", user_id=chat_id)
        except Exception:
            pass


def _first_run_flow() -> int:
    """Connect a brand-new install to its bot — no mode question.

    token → auto-capture the operator's DM chat-id → owner/admin grant
    → write one DM scope to ``aipager.yaml`` (token + scope committed
    together) → deps → settings. Then offer additive expansion (add a
    group / person), default done. No ``policy.yaml`` is written — the
    built-in roles + safety floor cover a solo install. See
    architecture §3.0.
    """
    if console.is_terminal:
        rule("aipager setup")
    else:
        console.print("Welcome to aipager setup.")

    try:
        token, bot_username = _step_token(step_label="[1/4]")
        chat_id = _step_chat_id(
            token, bot_username, mode="personal", step_label="[2/4]",
        )
        role = _grant_owner_step(chat_id, step_label="[3/4]")
        # Token + DM scope committed here — a Ctrl+C in any later step
        # leaves a working config; re-running falls into the edit flow.
        _commit_owner_dm(token, chat_id, role)

        deps_ok = _step_deps(step_label="[4/4]")
        if not deps_ok:
            cont = _ask(questionary.confirm(
                "Continue anyway? (the daemon will likely fail until you "
                "install the missing dependencies)",
                default=False, qmark="?", style=_PROMPT_STYLE,
            ))
            if not cont:
                friendly_warn(
                    "Setup aborted — install the missing dependencies and "
                    "re-run `aipager config`.",
                )
                return 2
        _step_settings(step_label="[4/4]")
    except KeyboardInterrupt:
        friendly_warn("Cancelled.")
        return 130
    except ValueError as e:
        friendly_error(str(e))
        return 1
    except OSError as e:
        friendly_error(f"Setup failed: {e}")
        return 1
    _completion_screen()
    # Additive expansion (default = done). Ctrl-C here is harmless —
    # the bootstrap is already on disk.
    try:
        from aipager.wizard.scope_flows import offer_expansion
        offer_expansion(token, bot_username)
    except KeyboardInterrupt:
        pass
    return 0
