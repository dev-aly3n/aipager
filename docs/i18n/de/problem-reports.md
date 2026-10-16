# Problemberichte: was ein Bericht enthält

<!-- i18n-source: docs/problem-reports.md @ 16ee949 -->

> Diese Seite ist eine Übersetzung der englischen Seite; wenn die beiden voneinander abweichen, gilt die englische Fassung. ([English](../../problem-reports.md))

Wenn aipager auf einen Bug stößt, kannst du der Person, die es pflegt, mit einem Tippen einen Problembericht schicken. Du siehst den ganzen Bericht, bevor irgendetwas gesendet wird, nichts verlässt deinen Computer, bevor du es sagst, und ein Bericht enthält nie deine Chats, Nachrichten, Namen, Pfade oder Tokens. Er besteht nur aus einer festen Liste von Feldern: Versionen, Anzahlen, Daten, Ja/Nein-Antworten, Auswahlen aus festen Listen, Stellen im eigenen Code von aipager und den `ap1-`-Namen seiner Bugs, dazu eine kurze Notiz, nur wenn du eine schreibst.

## <a id="how-to-send-one"></a>So schickst du einen

Nur der Besitzer der aipager-Installation kann aus Telegram einen Bericht schicken: die Person im eigenen privaten Chat der Installation mit dem Bot (der Betreiber; bei mehreren solchen Chats und keinem eindeutigen Besitzer gibt es weder den Button Problem melden noch ein automatisches Angebot, und `aipager report` im Terminal funktioniert weiterhin). Die Mini App hat eine eigene Berichtsseite; jeder andere Weg aus Telegram öffnet dieselbe Vorschaukarte in deinem privaten Chat mit dem Bot:

- **`/help`**: Wenn der Besitzer es sendet, kommt der Hilfetext mit einem Button „🐞 Problem melden“. In einer Gruppe wirkt nur das Tippen des Besitzers, und die Vorschau öffnet sich im privaten Chat des Besitzers, nie in der Gruppe.
- **Die Mini App**: Der Tab Einstellungen, „Problem melden“ (wenn die App auf deinem privaten Chat geöffnet ist), öffnet eine Seite in der App. Deine Notiz steht oben (optional, bis zu 500 Zeichen, mit Zähler). Darunter steht eine kurze Zusammenfassung des Berichts: Versionen, Einrichtung, die Fehler (tippe auf einen, um seine Stellen im Code zu sehen) und **Mehr Details**. **Den genauen Bericht zeigen** zeigt den genauen Text, der gesendet wird, deine Notiz eingeschlossen, und er folgt deinem Tippen. **Bericht senden** (der Button von Telegram unten) sendet genau das, einmal, und die Seite zeigt danach die Referenz mit einem Button **Kopieren**. Zurück sendet nichts. Wenn aipager deine Notiz aufräumt (Leerzeichen an den Enden, unsichtbare Zeichen, alles nach 500 Zeichen), zeigt die Seite die aufgeräumte Notiz, und es wird nichts gesendet, bis du noch einmal auf Bericht senden tippst.
- **Der Hinweis zur Speichergrenze**: Wenn dem Hook von aipager der Speicher ausgeht, hat der Hinweis im Chat einen Button „🐞 Das melden“.
- **Ein automatisches Angebot**: Selten bietet aipager an, einen Bericht über einen Bug zu schicken, den es bemerkt hat (siehe [Wann aipager einen Bericht anbietet](#when-aipager-offers-a-report)).
- **Im Terminal**: `aipager report` gibt einen Bericht aus und fragt `Send this report? [y/N]`. Er enthält dieselben Arten von Feldern, aber weniger Fakten über das laufende aipager (Modus, Chats, Funktionen, Laufzeit, Sitzungen und Details zu Claude Code bleiben unbekannt oder leer). `aipager report --note "what you were doing"` fügt eine Notiz hinzu. Ohne Terminal (ein Skript, eine Pipe, die Shell einer Claude-Code-Sitzung) gibt er den Bericht aus und sendet ihn nie.

Die Vorschaukarte zeigt den Bericht genau so, wie er gesendet wird: das JSON in einem eingeklappten Block, den du aufklappen kannst, oder, wenn er für eine Nachricht zu lang ist, als Datei `report.json`, die auf die Karte antwortet. Ihre Buttons:

- **📤 Senden**: sendet genau diesen Bericht, einmal. Die Karte sagt danach `Gesendet. Referenz ap1-...`.
- **✏️ Notiz hinzufügen**: Deine nächste Nachricht in diesem Chat innerhalb von 10 Minuten wird zur Notiz des Berichts (bei 500 Zeichen abgeschnitten), und die Karte zeigt den Bericht mit der Notiz noch einmal, bevor du sendest. Tust du stattdessen etwas anderes (ein Befehl, ein anderer Button, ein Foto, eine Aktion in der Mini App), schließt das die Frage nach der Notiz, ohne eine Notiz hinzuzufügen, und nach 10 Minuten geht deine nächste Nachricht wie gewohnt an Claude. Die Frage hat **‹ Zurück**, das ohne Notiz zur Vorschau zurückkehrt, und Abbrechen.
- **✖️ Abbrechen**: Es wird nichts gesendet.

Eine Vorschau bleibt 24 Stunden offen, und die 5 neuesten bleiben offen (eine 6. zu öffnen schließt die älteste). Nach einem Neustart von aipager lässt sich eine alte Karte nicht mehr senden: Öffne Problem melden noch einmal für eine neue. Ein in der Mini App geöffneter Bericht kann 24 Stunden lang gesendet werden (die 3 neuesten); nach einem Neustart lädt die Seite einen neuen.

## <a id="what-a-report-holds"></a>Was ein Bericht enthält

Jeder Teil, in einfachen Worten. Jeder Wert wird mit der Liste der erlaubten Werte verglichen, bevor die Vorschau gezeigt wird, und noch einmal vor dem Senden; alles, was nicht passt, wird durch `<invalid>` ersetzt oder weggelassen, nie unverändert gesendet.

**Über den Bericht**
- `schema`: das Format des Berichts (`aipager-report/1`).
- `trigger`: `manual` (du hast ihn geöffnet) oder `auto` (ein automatisches Angebot).
- `day`: das Datum, in UTC (ohne Uhrzeit).
- `note`: nur wenn du eine hinzugefügt hast: deine Worte, genau so, wie die Vorschau sie gezeigt hat (höchstens 500 Zeichen, ohne unsichtbare Zeichen).

**aipager und der Computer**
- `aipager`: die Version; wie es installiert wurde (`pipx`, `uv`, `brew`, `pip`, `editable`, `nix`, `snap`, `docker`, `system`, `foreign` oder `unknown`); woher (der Paketindex, ein lokaler Ordner, eine git-Arbeitskopie); ob `aipager update` es aktualisieren kann.
- `python`: die Python-Version und ob es CPython oder PyPy ist.
- `os`: das System (`linux`, `darwin`, `windows`, `freebsd`), der Prozessortyp, die Linux-Distribution aus einer festen Liste und ihre Versionsnummer, die Haupt.Unterversion des Kernels, die Art des Containers, falls es einen gibt (`docker`, `lxc`, `wsl`, `podman`), und ob aipager als systemd- oder launchd-Dienst eingerichtet ist.
- `deps`: die Versionen der Bibliotheken, von denen aipager abhängt (python-telegram-bot, httpx, aiohttp, PyYAML, rich, questionary, dtach-bin).
- `claude_code`: die Version von Claude Code, wie viele Installationen von Claude Code gefunden wurden und wie es sich anmeldet (`env`, `file`, `keychain` oder warum sich das nicht feststellen ließ). Nie die Anmeldedaten selbst.

**Deine Einrichtung, als Zahlen**
- `config`: der Modus (`personal`, `team`, `scope`); wie viele private Chats und wie viele Gruppen eingerichtet sind; wie viele eigene Rollen es gibt (ihre Namen sind nicht enthalten); welche Funktionen an sind (Mini App, ihre Tunnelart, Sprachnachrichten, Diff-Vorschauen, ausführliche Zusammenfassungen).
- `runtime`: wie lange aipager schon läuft, als Bereich (`<10m`, `10m-1h`, `1-24h`, `1-7d`, `>7d`); wie viele Sitzungen laufen und arbeiten; wie oft aipager in den letzten 7 Tagen unerwartet gestoppt ist und wie es zuletzt geendet hat (`clean`, `crash`, `reboot`).
- `doctor`: die Ergebnisse (`ok`, `warn`, `fail`) einer festen Liste von Gesundheitsprüfungen, wenn sie verfügbar sind, ohne die Prüfungen erneut laufen zu lassen (Berichte aus dem Bot und aus dem Terminal lassen das leer).
- `flood`: der Stand der Telegram-Limits über alle Chats zusammengezählt: Sperren in den letzten 7 Tagen, ob ein Chat gerade stummgeschaltet ist, im Minimalmodus oder gebremst, und der Anteil des Stundenbudgets des aktivsten Chats als Bereich. Keine Chat-IDs.

**Was schiefging**
- `counters_24h`: wie oft jeder Punkt einer festen Liste in den letzten 24 Stunden vorkam (zum Beispiel der Wächter der Arbeitskarte, der neu startet, eine Art von Telegram-Fehler, der Hook, der seine Speichergrenze erreicht, ein Netzwerk- oder Festplattenfehler). Nur die Anzahlen.
- `log_digest_24h`: für die 15 Stellen im Code von aipager, die in den letzten 24 Stunden am meisten Warnungen oder Fehler geloggt haben, die Stelle (`aipager/state.py:1234`), die Stufe und wie oft. Nie der Text des Logs.
- `errors`: bis zu 10 Fehler, die aipager aufgezeichnet hat, jeder mit:
  - `fingerprint`: ein `ap1-`-Name für den Bug (ein Hash aus dem Typ des Fehlers und den Namen von bis zu 8 Stellen im Code von aipager, nie aus Text oder Daten);
  - wo er passiert ist (aipager selbst, der Hook, die Statuszeile, ein Befehl, die Mini App), wie er abgefangen wurde und ob es ein Bug ist oder nur etwas Ungewöhnliches (`tier`);
  - bei einem Fehler, den aipager geloggt hat, der Name des Loggers (ein Modul von `aipager`, `asyncio` oder ein Teil von `telegram.ext`);
  - der Name des Ausnahmetyps (zum Beispiel `builtins.KeyError`, oder `<other>` für einen von außerhalb einer festen Paketliste), die Typen von bis zu drei Ursachen und der Name einer Fehlernummer wie `ENOSPC`;
  - bei einem Fehler des Hooks das Hook-Ereignis und das Tool (ein eingebautes Tool mit Namen, jedes MCP-Tool als `mcp`);
  - wie oft, an welchen Tagen und auf welchen Versionen von aipager er passiert ist;
  - bis zu 12 Stellen im eigenen Code von aipager, durch die er lief (Datei, Zeile, Funktionsname), und bis zu 8 Namen fremder Pakete aus einer festen Liste, durch die er lief.

**Nie enthalten**: deine Chats, Nachrichten, die Antworten von Claude, Namen von Sitzungen, Pfade von Ordnern oder Dateien, Tokens oder Schlüssel, Chat- oder Benutzer-IDs, Benutzernamen, der Name des Computers, der Text irgendeiner Fehlermeldung und Log-Zeilen. Die Felder oben haben keinen Platz dafür, und ein Bericht wird gegen diese Liste geprüft, bevor er gezeigt und bevor er gesendet wird.

## <a id="where-it-goes"></a>Wohin er geht

Ein Bericht geht ohne deinen Namen in den privaten Fehlereingang der Person, die aipager pflegt, bei [Sentry](https://sentry.io): kein Konto und keine Installations-ID. Wie jede Web-Anfrage kommt er von der Internetadresse deines Computers, die GitHub und Sentry sehen können; der Eingang ist so eingestellt, dass er sie nicht speichert (die Einstellung „Prevent Storing of IP Addresses“ von Sentry). aipager schickt ihn selbst in einer einzigen HTTPS-Anfrage (es nutzt nicht die eigene Bibliothek von Sentry, die viel mehr sammelt): Der Bericht, den du gesehen hast, geht als Anhang `report.json`, neben einer kurzen Zusammenfassung zum Gruppieren (die Version, wie aipager installiert wurde und ob es eine Entwickler-Installation ist, das System, die Versionen von Python und Claude Code, der Auslöser und der Fingerabdruck, der Typ und die Code-Stellen des ersten Fehlers). Die Anfrage nennt nur `aipager/<version>`.

Nach dem Senden zeigt die Karte (oder das Terminal) eine **Referenz**: den `ap1-`-Fingerabdruck des ersten Fehlers oder, bei einem Bericht ohne Fehler, seine 32-stellige Ereignis-ID. Wenn du zusätzlich ein [Issue auf GitHub](https://github.com/dev-aly3n/aipager/issues) öffnest, gib die Referenz an, damit die Person, die aipager pflegt, deinen Bericht findet. Füg keine Logs und keine Ausgabe von `aipager doctor` in ein Issue ein: Sie enthalten deine Chat-ID, die Pfade deines Home-Ordners und den Anfang deiner Nachrichten.

Kurz vor dem Senden liest aipager [report-endpoint.json](https://github.com/dev-aly3n/aipager/blob/main/report-endpoint.json) aus dem Repo von aipager auf GitHub, um zu erfahren, wohin gesendet wird (ein laufendes aipager behält eine gute Kopie einen Tag lang; ein misslungenes Lesen wird beim nächsten Senden wiederholt, und jedes `aipager report` liest die Datei neu). So kann die Person, die aipager pflegt, den Sentry-Schlüssel ändern, Berichte für alle abschalten oder Versionen unter einer bestimmten Nummer das Senden verbieten (ein Build, der seine eigene Version nicht kennt, zählt als darunter), ohne ein neues Release. Wenn GitHub nicht erreichbar ist oder die Datei keine ist, die aipager versteht, nimmt aipager den in diese Version eingebauten Schlüssel; eine lesbare Datei, die „aus“ sagt, wird immer befolgt.

## <a id="limits"></a>Grenzen

- Höchstens 5 Sendeversuche am Tag (UTC) von einem Computer, manuelle und automatische zusammen, und ein fehlgeschlagener Versuch zählt auch. Der nächste sagt `Pro Computer sind höchstens 5 Berichte am Tag möglich. Versuch es morgen noch einmal.`
- Im Hintergrund wird nichts wiederholt: Wenn ein Senden fehlschlägt, sagt die Karte `Gerade konnte nicht gesendet werden. Es ging nichts verloren; versuch es später noch einmal.` und behält ihren Button Senden.

## <a id="when-aipager-offers-a-report"></a>Wann aipager einen Bericht anbietet

aipager bemerkt seine eigenen Bugs und bietet selten an, einen Bericht darüber in deinem privaten Chat zu schicken: `aipager hatte N-mal einen internen Fehler (TypeError in der Arbeitskarte). Ein Bericht hilft, das schneller zu beheben. ...` mit **Bericht ansehen**, **Nicht jetzt** und **Nicht mehr fragen**. Bericht ansehen öffnet die Vorschaukarte mit nur diesen Fehlern; es wird nichts gesendet, bis du dort auf Senden tippst.

Ein Angebot kommt nur, wenn all das zutrifft:
- Es ist ein echter Bug in aipager: ein Fehler im eigenen Code von aipager, der mindestens zweimal passiert ist, mit mindestens 10 Minuten Abstand, oder sofort, wenn aipager als Dienst lief und abgestürzt ist (ein `aipager start` im Vordergrund, das beendet wird, zum Beispiel durch Schließen seines Terminals, wird nur gezählt). Fehler, die der Hook, das Hilfsprogramm der Statuszeile oder ein Befehl an aipager melden (auch der Hook, dem der Speicher ausgeht), müssen sich außerdem über mindestens einen Tag verteilen, ein einzelner löst also nie ein Angebot aus; dafür ist der Button Das melden im Hinweis da. Was nicht die Schuld von aipager ist (die Limits von Telegram, das Netzwerk, eine volle Festplatte), löst nie ein Angebot aus; das wird nur für einen Bericht gezählt, den du selbst schickst.
- Höchstens ein Angebot alle 3 Tage, mit allem, was offen ist, in diesem einen Angebot.
- Nicht in den ersten 48 Stunden, nachdem aipager auf diesem Computer zum ersten Mal gestartet ist (Updates setzen das nicht zurück), und nicht in der ersten Stunde nach einem Neustart von aipager.
- Nur wenn 2 Minuten lang nichts läuft oder auf das Senden wartet und du deinen privaten Chat mit dem Bot in den letzten 30 Minuten benutzt hast. Nie in einer Gruppe und nie, während Telegram diesen Chat begrenzt.
- Jeder Bug wird pro Version von aipager einmal angeboten. Nach jeder Antwort wird er auf dieser Version nicht mehr angeboten; nach einem Update kann er wiederkommen, nur wenn er wieder passiert.
- **Nicht jetzt** und **Nicht mehr fragen** zählen beide als Nein, ebenso ein Angebot, auf das du nicht innerhalb eines Tages antwortest. **Bericht ansehen** setzt die Zählung zurück, auch wenn du danach abbrichst. Zwei Nein hintereinander schalten die automatischen Angebote ab: Wenn das zweite ein Tippen ist, sagt eine Zeile das; in jedem Fall zeigt es `/settings`.
- Nie bei einer Entwickler-Installation (aipager aus einem lokalen Ordner, einer git-Arbeitskopie oder im Modus editable installiert).

Um die automatischen Angebote abzuschalten, nimm `/settings` in deinem privaten Chat: **Problemberichte: Aus**. Wenn zwei Nein sie abgeschaltet haben, hat dieselbe Seite **Angebote wieder einschalten**. Eine `reports.json`, die es gibt, die sich aber nicht lesen lässt, schaltet die automatischen Angebote ebenfalls ab (aipager kann nicht mehr wissen, was du vorher geantwortet hast); **Angebote wieder einschalten** behebt auch das. Auf einem Computer ohne Chat zum Fragen setz `AIPAGER_REPORT_PROMPTS=0` in der Umgebung von aipager. Problem melden und `aipager report` funktionieren immer, egal was diese Einstellungen sagen.

## <a id="what-is-kept-on-your-machine"></a>Was auf deinem Computer gespeichert wird

- `~/.local/state/aipager/reports.json`: die Fehler, die aipager aufgezeichnet hat (in der oben gezeigten Form: Typnamen, Versionen, Code-Stellen, höchstens 50), stündliche Zahlen für 8 Tage, die Log-Zusammenfassung für 2 Tage, wie die letzten Stopps geendet haben, und der Stand der Angebote (wann zuletzt angeboten wurde, die Nein hintereinander, welche Bugs auf welcher Version angeboten wurden). Nur du kannst sie lesen.
- `~/.local/state/aipager/report-sends.json`: wie viele Berichte heute gesendet oder versucht wurden.
- `~/.local/share/aipager/install.json`: wann aipager auf diesem Computer zum ersten Mal gestartet ist (für die 48 Stunden oben).
- `~/.local/share/aipager/running.json`: geschrieben, solange aipager läuft (seine Prozessnummer, seine Startzeit, ob es als Dienst läuft, und die ID dieses Systemstarts), um einen Absturz von einem sauberen Stopp zu unterscheiden.

Keine davon enthält einen Chat, eine Nachricht, einen Namen, einen Pfad oder einen Token. `aipager uninstall` behält sie mit den übrigen Daten von aipager, damit eine Neuinstallation dort weitermacht, wo sie aufgehört hat; es listet auf, was es behält, und gibt den Befehl aus, der es entfernt (siehe [`aipager uninstall`](commands.md#command-line-aipager-uninstall)). Um nur diese zu entfernen, lösch diese vier Dateien: Den ganzen Ordner `~/.local/state/aipager` zu löschen entfernt die ersten beiden (und mit ihnen den Stand der Sprachen, `languages.json`), aber `install.json` und `running.json` müssen mit Namen gelöscht werden (lösch nicht `~/.local/share/aipager`, das auch die Sitzungsordner von aipager enthält).

## <a id="see-also"></a>Siehe auch

- [Fehlerbehebung](troubleshooting.md): häufige Fehler und ihre Lösungen.
- [Sicherheitsmodell](security.md): was aipager anfasst und was über das Netzwerk geht.
- [Befehle des Bots](commands.md): `/help`, `/settings` und die Befehlszeile.
