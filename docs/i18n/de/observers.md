# Beobachter-Bots

<!-- i18n-source: docs/observers.md @ b12e978 -->

> Diese Seite ist eine Übersetzung der englischen Seite; wenn die beiden voneinander abweichen, gilt die englische Fassung. ([English](../../observers.md))

Telegram-Bots, die nur lesen und eine Kopie der Hinweise des Haupt-Bots bekommen. Sie bekommen Zusammenfassungen, Warnungen und Fehler, können aber keine Sitzungen steuern.

## <a id="setup"></a>Einrichtung

1. Erstelle einen Bot mit [@BotFather](https://t.me/BotFather)
2. Öffne einen Chat mit dem Bot und sende `/start`
3. Finde die ID deines Chats heraus (schick eine Nachricht und sieh dir dann `https://api.telegram.org/bot<TOKEN>/getUpdates` an)
4. Setz `OBSERVER_BOTS` in der Umgebung von aipager. Für ein aipager im Vordergrund exportier die Variable vor `aipager start`; für den systemd-Dienst:

```sh
systemctl --user edit aipager    # unter [Service] hinzufügen:
# Environment=OBSERVER_BOTS=<bot_token>:<chat_id>
```

Mehrere Beobachter (durch Kommas getrennt):

```
OBSERVER_BOTS=111:AAA_first:12345,222:BBB_second:67890
```

Das Format ist `token:chat_id`: Beim Lesen gilt der **letzte** Doppelpunkt als Trenner (Bot-Tokens enthalten selbst einen Doppelpunkt).

5. Starte aipager neu

## <a id="what-observers-receive"></a>Was Beobachter bekommen

Beobachter bekommen diese Hinweise aus **jedem Chat, für den der Bot zuständig ist**, jedem privaten Chat und jeder Gruppe, ohne Filter: Richte einen nur für einen Chat ein, dem du all das anvertrauen würdest. Jeder Hinweis ist in der Sprache des Chats, aus dem er kommt.

| Ereignis | Beispiel |
|-------|---------|
| Aufgabe fertig | `💬 x1 · Fertig (2:05, +12 -3)`: nur die Ergebniszeile, nicht die Antwort; dazu das vollständige Log als `.md`-Datei, wenn die Antwort oder die Karte gekürzt wurde |
| Ein Hintergrundauftrag ist fertig | `✅ x1 · Fertig (...)`, oder `⚠️ x1 · Fertig (...)`, wenn ein Agent verloren ging oder das Ende eines Hintergrundbefehls nie gesehen wurde |
| API-Fehler | `⚠️ x1 · Die Server von Anthropic sind überlastet. Versuch es gleich noch einmal.` (ohne Button Erneut versuchen) |
| Kontextwarnung | `⚠️ x1 · Kontext bei 82% - wird bald automatisch komprimiert` |
| Komprimierung läuft | `🔄 x1 · Komprimiert die Unterhaltung` |
| Komprimierung fertig | `📦 x1 · Unterhaltung komprimiert: 82% → 4%` |
| Eine Anfrage wartet noch | `⬆️ x1 · wartet noch auf deine Antwort oben`, mit der Berechtigung oder Frage, auf die sie wartet |
| Eine arbeitende Sitzung meldet sich nicht | `⏳ x1 · arbeitet noch (120 Min. ohne Neuigkeiten)` |
| Eine Nachricht, die Claude Code nicht angenommen hat | `⚠️ x1 · Claude Code hat die Nachricht nicht angenommen` |
| Sitzung beendet | `🔴 x1 · Sitzung beendet` |

## <a id="what-observers-dont-receive"></a>Was Beobachter NICHT bekommen

- Die Arbeitsanimationen
- Die Aktualisierungen der Tool-Aufrufe
- Berechtigungsanfragen und ihre Buttons (nur die Erinnerung „wartet noch“ oben nennt, was eine Anfrage fragt)
- Dialoge von AskUserQuestion
- Tastaturen oder Buttons jeder Art

Beobachter haben keinen Zustand: Gesendet wird, ohne auf eine Rückmeldung zu warten. Ein Beobachter, der ausfällt, beeinflusst den Haupt-Bot nie.
