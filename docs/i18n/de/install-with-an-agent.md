# Mit einem Agenten installieren

<!-- i18n-source: docs/install-with-an-agent.md @ fc5d6810 -->

> Diese Seite ist eine Übersetzung der englischen Seite; wenn die beiden voneinander abweichen, gilt die englische Fassung. ([English](../../install-with-an-agent.md))

Diese Seite ist für einen KI-Coding-Agenten (Claude Code oder irgendeinen Agenten mit Shell), der aipager für seine Person installieren und einrichten soll. Folge ihr Schritt für Schritt. Jeder Befehl hier läuft ohne Rückfragen und braucht nie ein interaktives Terminal.

Mit aipager steuert diese Person Claude-Code-Sitzungen aus Telegram. Wenn du fertig bist, hat die Person einen Telegram-Bot, der nur mit ihr spricht, und aipager läuft auf diesem Computer im Hintergrund.

## <a id="1-what-only-the-person-can-do"></a>1. Was nur die Person selbst tun kann

Bitte die Person, das zu tun. Du kannst es nicht für sie erledigen.

1. **Den Bot erstellen.** In Telegram [@BotFather](https://t.me/BotFather) öffnen, `/newbot` senden, einen Namen und einen Benutzernamen wählen. BotFather antwortet mit dem Token des Bots (er sieht aus wie `123456789:AA...`).
2. **Im Bot auf „Starten“ tippen.** Den neuen Bot öffnen (BotFather verlinkt ihn als `t.me/<bot username>`) und auf „Starten“ tippen. Ein Bot kann niemandem schreiben, der das nicht getan hat.
3. **Claude Code auf diesem Computer anmelden.** Wenn die Person Claude Code hier schon nutzt, ist nichts zu tun. Auf einem Server ohne Bildschirm führt sie einmal `claude` in ihrem eigenen Terminal aus und meldet sich an (es zeigt einen Link, den man auf einem anderen Gerät öffnet). Wenn sie lieber einen langlebigen Token will, führt sie `claude setup-token` aus, das den Token nur ausgibt: Sie speichert ihn selbst für die Sitzungen von aipager, in ihrem eigenen Terminal (Token einfügen, Enter drücken, dann Ctrl-D):

   ```sh
   mkdir -p ~/.config/aipager
   (umask 077 && { printf 'CLAUDE_CODE_OAUTH_TOKEN='; cat; } >> ~/.config/aipager/daemon.env)
   ```

   aipager gibt diesen Token an jede Sitzung weiter, die es startet (siehe [Sicherheit](security.md#the-claude-credential-what-actually-protects-it)). Du liest ihn nie.

## <a id="2-how-the-person-gives-you-the-token"></a>2. Wie die Person dir den Token gibt

Wer den Token des Bots hat, steuert den Bot. Biete der Person diese zwei Wege an und lass sie wählen.

- **Am sichersten: eine Datei, die du nie liest.** In ihrem eigenen Terminal führt sie aus:

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token)
  chmod 600 ~/.config/aipager/bot-token
  ```

  dann fügt sie den Token ein, drückt Enter und drückt Ctrl-D. Nur sie kann die Datei lesen (Rechte 600). Sie kann sie löschen, sobald die Einrichtung geklappt hat (aipager behält seine eigene Kopie in seiner Konfiguration). Sie sagt dir den Pfad (`~/.config/aipager/bot-token`). Der Token kommt nie in deinen Chat, und du gibst den Pfad an `--token-file`.
- **Am schnellsten: in den Chat einfügen.** Das geht, aber sag klar, was es bedeutet: Der Token bleibt in deinem Gesprächsverlauf und wird an den Anbieter deines Modells geschickt. Falls dieser Verlauf jemals geteilt werden könnte, sollte die Person später `/revoke` an @BotFather senden und den neuen Token mit `aipager setup --force` setzen (Schritt 3.2, mit `--force`). Wenn sie diesen Weg wählt, speichere den Token in derselben Datei, ohne ihn auszugeben: am besten mit deinem eigenen Tool zum Schreiben von Dateien, danach `chmod 600 ~/.config/aipager/bot-token`. Wenn du nur eine Shell hast, gib ihn über stdin mit einem Here-Document weiter, damit er kein Argument des Befehls ist, der ihn schreibt:

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token) <<'EOF'
  <the token>
  EOF
  chmod 600 ~/.config/aipager/bot-token
  ```

  Dann nimm `--token-file` wie unten.

Die Telegram-ID der Person (die ID ihres Chats, eine Zahl wie `123456789`) ist nicht geheim. Wenn sie sie nicht kennt, kannst du sie herausfinden (Schritt 3.3).

## <a id="3-your-steps"></a>3. Deine Schritte

### <a id="31-install-aipager"></a>3.1 aipager installieren

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

Es nimmt uv oder pipx, was eben da ist (auf macOS auch Homebrew), und installiert zuerst uv, wenn nichts davon da ist. Du kannst auch selbst `uv tool install aipager` oder `pipx install aipager` ausführen. Wenn `aipager` danach nicht gefunden wird, füg `~/.local/bin` zum `PATH` deiner Shell hinzu (`export PATH="$HOME/.local/bin:$PATH"`).

War aipager schon installiert, aktualisieren diese Befehle es nicht. Prüfe, ob `aipager setup --help` funktioniert; wenn nicht, ist das installierte aipager zu alt: Führ `aipager update` aus und prüfe noch einmal. Wenn `aipager update` sagt, aipager neu zu starten, sag es der Person; starte es nicht selbst neu (siehe 4).

Führ `aipager config` nicht aus: Das ist der interaktive Assistent für Menschen.

### <a id="32-set-aipager-up"></a>3.2 aipager einrichten

```sh
aipager setup --token-file ~/.config/aipager/bot-token --chat-id 123456789 --service --json
```

Nimm die echte ID der Person und den Pfad der Token-Datei. Statt `--token-file` kannst du den Token mit `--token-stdin` über eine Pipe hineingeben (zum Beispiel `--token-stdin < path`); die Pipe muss sich schließen, und setup wartet höchstens 30 Sekunden darauf.

setup prüft den Token bei Telegram, prüft diesen Computer, schickt der Person eine Testnachricht ("aipager is set up for you. ...") und schreibt erst dann seine Konfiguration, trägt die Hooks von Claude Code in `~/.claude/settings.json` (oder `$CLAUDE_CONFIG_DIR/settings.json`) ein und installiert und startet mit `--service` den Hintergrunddienst. Wenn eine Prüfung fehlschlägt, wird nichts geschrieben.

Füg `--dry-run` hinzu, um zuerst zu sehen, was sich ändern würde. `--role admin` gibt der Person die Rolle admin statt der Rolle owner (der Standard, owner, ist richtig für eine Person, die ihren eigenen Computer einrichtet).

### <a id="33-if-the-person-does-not-know-their-telegram-id"></a>3.3 Wenn die Person ihre Telegram-ID nicht kennt

```sh
aipager setup detect-chat --token-file ~/.config/aipager/bot-token --json
```

Bitte die Person, im Bot auf „Starten“ zu tippen (oder ihm irgendeine Nachricht zu schicken), während der Befehl wartet (bis zu 5 Minuten; `--timeout SECONDS` ändert das). Er gibt den neuesten Absender in `candidate` aus (`id`, `first_name`, `last_name`, `username`) und speichert nichts. Zeig der Person den Namen und den Benutzernamen und frag „Bist du das?“. Nur wenn sie ja sagt, führ Schritt 3.2 mit `--chat-id` gleich `candidate.id` aus. Wenn `other_candidates` nicht 0 ist, haben auch andere Leute dem Bot geschrieben: Sei dann besonders sorgfältig.

### <a id="34-read-the-result"></a>3.4 Das Ergebnis lesen

Mit `--json` gibt setup genau ein JSON-Objekt auf stdout aus. Lies `exit_code` (derselbe wie der Exit-Code des Prozesses), `status`, `error`, `message`, `fix` und `next_step`. Wenn stdout kein JSON ist (eine unbekannte Option, ein falsches Wort nach `setup` oder ein aipager, das zu alt ist, um `setup` zu kennen), ist der Exit-Code 2, und der Grund steht als einfacher Text auf stderr: Lies ihn, und für ein altes aipager siehe 3.1.

| Exit | `error` | Was du tust |
|---|---|---|
| 0 | | `installed`, `updated` oder `unchanged`: fertig, weiter mit 3.5. `dry_run`: Es wurde nichts geschrieben; führ denselben Befehl ohne `--dry-run` noch einmal aus. detect-chat `found`: Lass die Person wie in 3.3 bestätigen, dann führ 3.2 aus. |
| 1 | `config_malformed`, `ambiguous_install`, `settings_invalid`, `test_send_failed`, `write_failed`, `service_failed`, `internal_error` | Zeig der Person `message` und `fix`. `ambiguous_install`: Die Person behebt das in `aipager config`. Bearbeite keine Dateien von Hand. `settings_invalid`, das ein absolutes `CLAUDE_CONFIG_DIR` verlangt: `fix` nennt die zu ändernde Datei (`daemon.env` oder eine Einstellungsdatei von Claude Code); frag die Person nach dem Ordner, ändere nur diesen einen Wert und nur, wenn sie es sagt, und führe setup dann noch einmal aus. |
| 2 | `usage`, `token_malformed`, `token_stdin_timeout`, `bad_chat_id`, ... | Deine Eingabe war falsch; `fix` nennt die Option. `token_malformed`: Die Datei enthält keinen Bot-Token, bitte die Person, sie zu prüfen. |
| 3 | `deps_missing` | Folge `fix` (jedes fehlende Teil hat auch sein eigenes `fix` in `deps.items`), dann führ setup noch einmal aus. |
| 4 | `token_rejected` | Telegram hat den Token abgelehnt. Bitte die Person noch einmal um den Token (oder um einen neuen von @BotFather). |
| 5 | `chat_not_started`, `bot_blocked` | Bitte die Person, den Bot zu öffnen, auf „Starten“ zu tippen (oder ihn zu entsperren), dann führ setup noch einmal aus. |
| 6 | `existing_install` | aipager ist schon mit einem anderen Token, Chat oder einer anderen Rolle eingerichtet. Sag der Person, was sich unterscheidet, und frag, bevor du `--force` hinzufügst. |
| 7 | `telegram_unreachable` | Netzwerkprobleme oder Telegram ist ausgelastet. Warte eine Minute und versuch es noch einmal. |
| 8 | `detect_timeout` | detect-chat hat keine Nachricht gesehen. Bitte die Person, noch einmal auf „Starten“ zu tippen, und führ den Befehl erneut aus. Mit der Warnung `updates_backlog` hilft „Starten“ nicht: Frag die Person nach ihrer numerischen ID. |
| 9 | `daemon_running` | detect-chat: Ein aipager für diesen Bot läuft schon und bekommt seine Nachrichten. Frag die Person nach ihrer numerischen ID (oder frag sie, bevor du es mit `aipager service stop` stoppst). |
| 10 | `updates_conflict` | detect-chat: Ein anderes Programm liest die Nachrichten dieses Bots. Frag die Person nach ihrer numerischen ID. |
| 130 | `interrupted` | Mit Ctrl-C gestoppt. |

Wenn `daemon.restart_needed` true ist, braucht aipager einen Neustart, um die Änderung zu übernehmen, oder setup konnte nicht feststellen, ob es läuft (die Warnung `daemon_unknown`). Folge `next_step`, das sagt, welches von beiden (wenn es `aipager start` anbietet, das im Vordergrund bleibt, nimm lieber `aipager service install`). Sag es der Person, und starte nur dann neu (`aipager service stop`, dann `aipager service start`), wenn sie es will, und nie aus einer Sitzung heraus, die die Person über aipager steuert (siehe 4). Die vollständige Liste der Fehler, Warnungen und JSON-Schlüssel steht in [der Referenz zum Befehl setup](commands.md#command-line-aipager-setup-for-coding-agents).

### <a id="35-check-the-install"></a>3.5 Die Installation prüfen

```sh
aipager doctor --json
```

Der Befehl liest nur und schickt nichts. Exit 0 heißt, dass keine Prüfung fehlgeschlagen ist. Zeig der Person für jede Prüfung mit `status` gleich `fail` (oder `warn`) ihren `title`, ihr `detail` und ihr `fix`.

### <a id="36-report-back-to-the-person"></a>3.6 Der Person Bescheid geben

Sag ihr in ein paar Zeilen:

- den Bot (`@` + `bot_username`) und ob die Testnachricht gesendet wurde (`test_message`);
- was eingerichtet wurde (`changed` und `service.result`);
- wie man eine Claude-Sitzung startet: `/new` an den Bot in Telegram senden, oder `aipager session <name>` in einem Terminal ausführen;
- alles, was `doctor` gemeldet hat, und `next_step`, wenn es etwas verlangt.

## <a id="4-what-you-must-not-do"></a>4. Was du nicht tun darfst

- Gib den Token nicht aus, nicht mit `echo` und nicht mit `cat`, und schreib ihn nie in eine Befehlszeile (`--token` wird ohnehin abgelehnt). Gib ihn nur als Datei oder über stdin weiter.
- Bearbeite `~/.config/aipager/` (außer der Token-Datei aus Schritt 2) und `~/.claude/settings.json` nicht von Hand. `aipager setup` und `aipager config` schreiben sie.
- Füg `--force` nicht hinzu, ohne die Person zu fragen: Es ersetzt ihren bestehenden Token, Chat oder ihre Rolle.
- Starte deine eigene Claude-Code-Sitzung nicht neu und beende sie nicht. Bei einer ersten Installation läuft die Sitzung, in der du arbeitest, nicht unter aipager und muss es auch nicht: Neue Sitzungen kommen aus `/new` in Telegram oder aus `aipager session <name>`. Wenn aipager schon eingerichtet ist und die Person dich darüber erreicht (zum Beispiel, um `setup --force` mit einem neuen Token auszuführen), stoppe aipager nicht und starte es nicht selbst neu: Deine Aufgabe und ihre Antwort würden abgeschnitten. Sag der Person, dass sie es neu starten soll, wenn du fertig bist.

## <a id="5-groups"></a>5. Gruppen

`aipager setup` richtet den privaten Chat der Person mit dem Bot ein. Um eine Telegram-Gruppe mit weiteren Personen und ihren Rollen hinzuzufügen, führt die Person `aipager config` aus (die Einrichtung von Gruppen geht noch nicht per Skript). Siehe [Teambetrieb und Gruppen](groups.md).

## <a id="see-also"></a>Siehe auch

- [Befehle des Bots und der Befehl setup](commands.md)
- [Fehlerbehebung](troubleshooting.md)
- [Sicherheitsmodell](security.md)
