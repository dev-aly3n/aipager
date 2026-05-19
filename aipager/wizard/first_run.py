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
    _step_team_setup,
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


def _first_run_flow() -> int:
    """Walk a brand-new install through token → mode → chat → save
    config.env → (team users + rules if team) → deps → settings.

    config.env is committed RIGHT AFTER chat-id is captured (not at
    the very end), so a Ctrl+C anywhere later doesn't throw the
    admin back to step 1 on re-run — re-entry falls into the edit
    flow with the partial team.yaml + config.env intact.
    """
    if console.is_terminal:
        rule("aipager setup")
    else:
        console.print("Welcome to aipager setup.")

    try:
        # Mode determines total step count and the chat-id step's
        # prompt copy. Token always comes first because we need it to
        # auto-detect chat ids.
        token, bot_username = _step_token(step_label="[1/?]")
        wizard_mode = _step_pick_mode(step_label="[2/?]")

        total = 7 if wizard_mode == "team" else 6
        chat_id = _step_chat_id(
            token, bot_username,
            mode=wizard_mode, step_label=f"[3/{total}]",
        )

        # Commit token + chat_id to disk EARLY. Subsequent steps
        # only patch settings.json / write team.yaml — none of them
        # change these two values, and writing now means an admin
        # Ctrl+C during a later step doesn't lose their progress
        # (re-running falls into the edit flow).
        _step_write_env(
            token, chat_id,
            step_label=f"[4/{total}]",
        )

        if wizard_mode == "team":
            _step_team_setup(chat_id, step_label=f"[5/{total}]", token=token)
            deps_step = f"[6/{total}]"
            settings_step = f"[7/{total}]"
        else:
            deps_step = f"[5/{total}]"
            settings_step = f"[6/{total}]"

        deps_ok = _step_deps(step_label=deps_step)
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
        _step_settings(step_label=settings_step)
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
    return 0
