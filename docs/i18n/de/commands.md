# Befehle und Oberfläche des Bots

<!-- i18n-source: docs/commands.md @ a236da1 -->

> Diese Seite ist eine Übersetzung der englischen Seite; wenn die beiden voneinander abweichen, gilt die englische Fassung. ([English](../../commands.md))

Wie du aipager aus Telegram steuerst. Vier Wege der Eingabe: Slash-Befehle, Buttons der Tastatur, das Dashboard der Mini App und freie Nachrichten (Text, Dateien, Sprachnachrichten).

Der Bot nimmt Eingaben nur aus den eingerichteten Chats an; siehe [Sicherheitsmodell](security.md) für die Vertrauensgrenze.

## <a id="slash-commands"></a>Slash-Befehle

Sie werden in `aipager/bot/lifecycle.py` registriert. Das Befehlsmenü von Telegram zeigt sie an und wird beim Start von aipager und bei jeder Änderung der Sitzungen aktualisiert.

| Befehl | Argumente | Was er tut |
|---|---|---|
| `/start` | - | Der Startbildschirm: die Sitzungen dieses Chats, wohin eine normale Nachricht geht (`✍️ Nachrichten gehen an x1.`), und `🆕 Neue Sitzung`, `⚙️ Einstellungen` und, wenn dieser Chat eine beendete Sitzung zum Zurückholen hat, `↩️ Fortsetzen`. Zeigt auch die feste Tastatur. |
| `/help` | - | Eine kurze Anleitung nach Aufgaben (starten, sprechen, steuern, verwalten, Einstellungen und für Admins `/update`), mit dem Tipp, dass ein angetippter Befehl sofort gesendet wird: Halte ihn gedrückt (Handy) oder drück Tab (Computer), um vorher Text hinzuzufügen. Für den Besitzer der Installation (die Person im eigenen privaten Chat der Installation mit dem Bot) hat sie außerdem einen Button `🐞 Problem melden`: Er öffnet in diesem privaten Chat eine Vorschau des genauen Problemberichts, mit Senden, Notiz hinzufügen und Abbrechen. Es wird nichts gesendet, bis du auf Senden tippst ([was ein Bericht enthält](problem-reports.md)). |
| `/app` | - | Öffnet das Dashboard der Mini App (Sitzungen, Diff-Ansicht, Einstellungen). |
| `/status` | - | Die Sitzungen dieses Chats als Liste, mit der du arbeiten kannst. Jede sagt ihren Zustand in Worten (`⚙️ arbeitet 3:12 · Bash: run tests`, `⏳ braucht dich · Bash: make deploy`, `💤 bereit`), mit `✍️` an der, an die normale Nachrichten gehen, und einer zweiten Zeile mit dem Modell, dem Kontext, den Kosten, dem, was in der Warteschlange ist (`Warteschlange 3 (1 wartend, 2 Notizen)`), und den laufenden Agenten und Hintergrundbefehlen. Jede hat eine Reihe Buttons: `✍️ x1` schickt deine nächsten Nachrichten dorthin, `⏹ Stoppen`, solange sie arbeitet, `Antworten`, solange sie auf dich wartet, und `⋮` für den Rest. Darunter: `🆕 Neu` und `⚫ Beendet (n)` für die Sitzungen, die beendet sind (fortsetzen, löschen oder alle aus der Liste entfernen; sie bleiben in `/resume`). |
| `/stop [label]` | optional | Unterbricht die laufende Aufgabe einer Sitzung. Ohne Namen stoppt er die eine Sitzung dieses Chats, die arbeitet (auch wenn deine Nachrichten an eine andere gehen; eine Sitzung, deren Hintergrund-Agent oder Hintergrundbefehl noch läuft, zählt als arbeitend), fragt mit einer Auswahl, welche, wenn es mehrere sind, und sagt `Gerade läuft nichts.`, wenn keine arbeitet. `/stop x1` (oder `/x1 stop`) stoppt x1 (oder antwortet `x1 macht gerade nichts.`, wenn sie nicht arbeitet). Verwirft außerdem die wartenden Nachrichten und sagt, wie viele verworfen wurden. |
| `/now` | - | Schickt die Nachrichten, die Claude in der Warteschlange der aktiven Sitzung hält, sofort statt nach dem aktuellen Schritt (die eigenen Sofort-senden-Tasten von Claude Code, Ctrl+X Ctrl+S). Funktioniert jederzeit, auch wenn der Chat stummgeschaltet oder im Minimalmodus ist; antwortet `In der Warteschlange ist nichts`, wenn Claude nichts hält. Siehe [Eine wartende Nachricht sofort senden](#send-a-queued-message-now). |
| `/new [name] [first message]` | alles optional | Startet eine Sitzung. `/new x1` startet sie sofort, und `/new x1 fix the tests` schickt außerdem „fix the tests“ als erste Nachricht. Allein gesendet (zum Beispiel aus dem Menü angetippt), fragt `/new` nach dem Namen, und die nächste Nachricht, die du schickst, wird genau wie das Argument benutzt; `/new` und dann `x1 fix the tests` ergibt also dasselbe. In einer Gruppe antworte mit dem Namen auf die Karte (oder erwähn den Bot: `@aipagerbot x1`), weil eine normale Nachricht dort den Bot nicht erreicht. Nur diese nächste Nachricht: Tust du vorher etwas anderes (ein Foto, ein anderer Befehl, eine Antwort auf eine andere Nachricht, eine Vorlage der Tastatur, ein anderer Button), wird das wie gewohnt behandelt, und die Karte schließt sich; `/status`, `/settings` und die Menüs der Tastatur lassen sie offen. Die Karte, die fragt, zeigt auch den Modus, das Modell und den Ordner, den die Sitzung bekommt, mit Buttons, um sie vorher zu ändern. Jeder Weg endet bei derselben Karte „x1 ist bereit“: was die Sitzung bekommen hat, dass deine nächste Nachricht an sie geht, und Buttons mit einem Tippen, um in einen anderen Modus zu wechseln oder das Modell zu ändern. Neue Sitzungen starten für alle im Auto-Modus; ändere, was /new nimmt, unter `/settings` → Neue Sitzungen. Ein `!` vor dem Namen startet die Sitzung im Vollzugriff-Modus, für einen Admin (die Rollen `owner` und `admin`) oder eine Person in ihrem eigenen privaten Chat mit dem Bot, deren Rolle Nachrichten schicken und Claude antworten darf (`user`, nicht `read_only`); für alle anderen startet sie im Auto-Modus, und die Karte sagt, warum. Ein Name, der schon vergeben ist, bietet `↩️ Dorthin wechseln` (wenn diese Sitzung läuft) oder `♻️ Fortsetzen` (wenn sie beendet ist), `🆕 Durch eine neue ersetzen` und `↩️ Abbrechen` an, nur für die Person, die `/new` geschickt hat. Die Karte, die nach einem Namen fragt, läuft nach 10 Minuten ohne Nutzung ab (`⏱ Diese Karte für eine neue Sitzung ist abgelaufen. Sende /new noch einmal.`). Ein Mitglied, dessen Rolle kein `can_manage` hat (`user`, `read_only`), startet nur im [Stammordner](groups.md#root-folders) des Chats: Der Ordner der Karte ist der Stammordner, die Seite Ordner listet ihn und die Ordner direkt darin (ohne die Zeile „Standardordner“), „Neuer Ordner“ legt einen im gewählten an, und Dorthin wechseln, Fortsetzen und Ersetzen bei einer Sitzung, die außerhalb des Stammordners arbeitet, werden abgelehnt („Diese Sitzung arbeitet außerhalb deines Ordners ~/..., daher kann deine Rolle sie nicht fortsetzen, neu starten oder ihren Modus wechseln. Frag einen Admin.“). |
| `/resume [label]` | optional | Setzt eine schon beendete Sitzung über ihren Namen fort oder öffnet eine Auswahl. Eine Sitzung kommt in dem Modus zurück, in dem sie war. Eine Sitzung, die im Vollzugriff-Modus war, kommt nur für einen Admin oder eine Person in ihrem eigenen privaten Chat im Vollzugriff-Modus zurück; für alle anderen kommt sie im Auto-Modus zurück, und die Antwort sagt das (`♻️ x1 läuft wieder, im Auto-Modus: Der Vollzugriff-Modus ist nur für Admins.`). Eine Sitzung, die ein älteres aipager in seinem alten Auto-Modus (der ohne Prüfungen lief) gespeichert hat, kommt im Vollzugriff-Modus zurück, und eine, die im Fragen-Modus gespeichert wurde, auch im Fragen-Modus. Eine beendete Sitzung bleibt `GONE_SESSION_MAX_AGE_DAYS` Tage lang in der Liste (Standard 14) und verlässt dann die Sitzungsliste; die gespeicherte Unterhaltung von Claude wird nicht angerührt. Ein Mitglied, dessen Rolle kein `can_manage` hat, kann keine Sitzung fortsetzen, deren Ordner außerhalb des [Stammordners](groups.md#root-folders) des Chats liegt (Besitzer und Admins können das); dasselbe gilt für Neu starten und jeden Moduswechsel, auch den ohne Neustart zwischen Fragen- und Auto-Modus. |
| `/kill [label]` | optional | Beendet eine Sitzung, immer nach einer Bestätigung: `⏹ x1 beenden? Claude stoppt und die Sitzung endet.` mit `[⏹ Beenden] [Abbrechen]`. Ohne Namen geht er zur Bestätigung, wenn dieser Chat genau eine laufende Sitzung hat, sonst zu einer Auswahl der laufenden Sitzungen dieses Chats. Auch unter `/status` → `⋮` → `⏹ Sitzung beenden`. |
| `/restart [label]` | optional | Startet eine Sitzung nach einer Bestätigung neu: Claude stoppt und startet wieder, mit erhaltener Unterhaltung, in dem Modus, in dem aipager sie zuletzt gestartet oder in den es sie gewechselt hat. Eine im Terminal mit `aipager session` gestartete Sitzung kommt im Fragen-Modus zurück, außer ihr Modus wurde seitdem aus Telegram gewechselt (aipager weiß nicht, in welchem Modus das Terminal sie gestartet hat, und rät nie einen offeneren); dasselbe gilt, wenn man sie aus Telegram fortsetzt. |
| `/rename [label] [new]` | optional | Gibt einer Sitzung einen neuen Namen (auch beendeten). `/rename x1 newname` benennt sofort um. Mit weniger Wörtern fragt er: direkt nach dem neuen Namen, wenn der Befehl nur eine Sitzung meinen kann, sonst mit einer Auswahl (ein einzelnes Wort nach `/rename` wählt die Sitzung nicht, weil es der alte oder der neue Name sein könnte). In einer Gruppe antworte mit dem Namen auf die Frage. |
| `/delete [label]` | optional | Entfernt eine beendete Sitzung nach einer Bestätigung aus der Liste. |
| `/diff [label]` | optional | Zeigt den git diff des Arbeitsordners der Sitzung. Ohne Namen zeigt er die Sitzung, an die deine Nachrichten gehen. |
| `/clearqueue` | - | Verwirft jede Nachricht, die die aktive Sitzung noch nicht abgeholt hat (die, die aipager zurückhält, und die, die schon in der Warteschlange in Claude stehen), ohne die laufende Aufgabe zu unterbrechen. Antwortet mit der Zahl der verworfenen Nachrichten. |
| `/mode [label] [ask\|auto\|full]` | optional | Zeigt den Berechtigungsmodus einer Sitzung mit einem Button für den Wechsel in jeden anderen Modus: `✍️ x1 ist im 🤖 Auto-Modus.` `[💬 Zum Fragen-Modus wechseln]` `[🔓 Zum Vollzugriff-Modus wechseln]`. Es gibt drei Modi. **💬 Fragen-Modus**: Claude fragt vor jeder Aktion. **🤖 Auto-Modus** (der Standard): Die eigene Sicherheitsprüfung von Claude Code führt sichere Aktionen aus und stoppt riskante, damit du entscheidest. **🔓 Vollzugriff-Modus**: Alles läuft ohne jede Prüfung (`--dangerously-skip-permissions`); nur ein Admin oder eine Person in ihrem eigenen privaten Chat mit dem Bot, deren Rolle Nachrichten schicken und Claude antworten darf (`user`, nicht `read_only`), kann ihn wählen. Auto- und Vollzugriff-Modus ändern nur die eigenen Berechtigungsanfragen von Claude Code: Der Hook von aipager wendet weiterhin die Regeln der Rolle des Mitglieds an. `/mode ask`, `/mode auto`, `/mode full` oder `/mode x1 ask` wechseln sofort; das eigene Wort des Modus in der Sprache des Chats funktioniert auch, ohne das Wort für Modus (`/mode x1 Fragen` für 💬 Fragen-Modus, `/mode x1 Vollzugriff` für 🔓 Vollzugriff-Modus). Hat eine laufende Sitzung in diesem Chat genau diesen Namen (eine Sitzung namens `demander`, sagen wir), nennt das Wort die Sitzung: `/mode demander` zeigt den Modus dieser Sitzung, und `/mode x1 demander` wechselt weiterhin x1. `ask`, `auto` und `full` meinen immer den Modus, in jeder Reihenfolge (`/mode x1 ask` oder `/mode ask x1`); nur wenn beide Wörter Moduswörter sind und genau eines davon der Name einer laufenden Sitzung ist, nennt dieses die Sitzung; bei einer Sitzung namens `full` wechseln `/mode full ask` und `/mode ask full` sie also beide in den Fragen-Modus, während `/mode full` allein weiterhin der Modus ist. Der Wechsel zwischen Fragen- und Auto-Modus passiert ohne Neustart und jederzeit (auch während Claude arbeitet): aipager drückt in der Sitzung Shift+Tab, so wie du es tun würdest, die Karte sagt `⚙️ x1 wechselt ohne Neustart in den 🤖 Auto-Modus.`, bis Claude Code den neuen Modus beim nächsten Schritt meldet, und zeigt dann das Ergebnis. Ist der Auto-Modus für die Sitzung nicht verfügbar (wegen ihres Modells oder Tarifs), sagt die Karte das, und die Sitzung bleibt im Fragen-Modus. Nicht, während die Sitzung eine Frage oder eine Berechtigungsanfrage zeigt: Beantworte sie zuerst. In den Vollzugriff-Modus zu wechseln braucht einen Admin (oder eine Person in ihrem eigenen privaten Chat) und eine Bestätigung und startet die Sitzung neu (die Unterhaltung bleibt erhalten); den Vollzugriff-Modus zu verlassen startet sie ebenfalls neu, sofort. Eine Sitzung, die im Vollzugriff-Modus gestartet wurde, startet bei jedem Wechsel neu, auch nachdem sie im Terminal in den Fragen- oder Auto-Modus gewechselt wurde, und ebenso eine, die seit ihrem Start den Vollzugriff-Modus gemeldet hat (eine Terminal-Sitzung, die mit `--dangerously-skip-permissions` gestartet wurde): Shift+Tab könnte sie dort im Zyklus zurück in den Vollzugriff-Modus bringen. Bei einer arbeitenden Sitzung bietet ein Neustart `🛑 Stoppen & wechseln` / `⏳ Nicht jetzt` an. Ein Mitglied, dessen Rolle kein `can_manage` hat, kann den Modus einer Sitzung, deren Ordner außerhalb des [Stammordners](groups.md#root-folders) des Chats liegt, nicht wechseln, weder ohne noch mit Neustart. Ein Tippen auf einen Wechsel ändert die Karte selbst, statt eine zweite Nachricht zu schicken. `/perms` ist der alte Name und funktioniert weiterhin gleich. |
| `/settings` | - | Wie die Sitzungen dieses Chats aussehen und antworten: die Sprache von aipager, wie die Antwort aussieht, Diff-Vorschauen, Karten-Updates bei langen Aufgaben, Formatierung, Antwortlänge, Wortschatz; womit `/new` eine Sitzung startet; Einstellungen pro Sitzung; und im privaten Chat des Besitzers die Angebote für Problemberichte. Siehe [Einstellungen](#settings). In einer Gruppe dürfen alle ansehen, und eine Einstellung zu ändern braucht einen Admin. |
| `/language` | - | Die Sprache, die aipager für seine eigenen Nachrichten und Buttons in diesem Chat nutzt (dieselbe Liste wie die erste Zeile von `/settings`). Jede Sprache steht unter ihrem eigenen Namen: English, dann Español, Русский (Russisch), Português (Brasil), 简体中文 (Chinesisch, vereinfacht), Français, Deutsch, 한국어 (Koreanisch), العربية (Arabisch), Bahasa Indonesia, Українська (Ukrainisch), Italiano, Türkçe (Türkisch), فارسی (Persisch) und Tiếng Việt (Vietnamesisch). Ein privater Chat, der nach dieser Version beginnt, folgt der Sprache deiner Telegram-App; einer, den es schon gab, bleibt Englisch, bis du wählst (siehe [Sprachen](#languages)). Auf Spanisch, Portugiesisch, Französisch, Italienisch und Indonesisch bleiben die Wörter der unteren Tastatur `status`, `stop`, `new` und `kill`, genau wie ihre Befehle. Deutsch und Türkisch nutzen `Status`, `Stop`, `Neu…` und `Beenden…` sowie `Özet`, `Stop`, `Oluştur` und `Sonlandır`; die englischen Wörter funktionieren getippt weiterhin. Auf Persisch und Arabisch läuft der Text von aipager von rechts nach links, mit persischen oder arabischen Ziffern; Sitzungsnamen, Befehle, Modellnamen und Code bleiben, wie sie sind. Die Liste sagt, was sie ändert: nur die Texte von aipager, hier und in der Mini App; deine Claude-Sitzungen ändern sich nicht, und Claude antwortet in der Sprache, in der du ihm schreibst. In einer Gruppe dürfen alle ansehen, und sie zu ändern braucht einen Admin. `language` ist ein reservierter Sitzungsname. |
| `/whoami` | - | Zeigt, wer du hier bist: im Teambetrieb deinen Namen, deine Rolle in diesem Chat und die Regeln, die sie dir gibt. |
| `/update` | - | Nur für Admins (die Rollen `owner` und `admin` oder eine Rolle mit `can_manage`). Sucht nach neueren Versionen von aipager und Claude Code und aktualisiert dann mit einem einzigen Button, was eine hat. Siehe [Update](#update). |

Das Menü `/` zeigt zuerst deine Sitzungen (`/x1 · Mit x1 sprechen`), dann die häufigen Befehle: `new`, `status`, `stop`, `now`, `resume`, `mode`, `settings`, `language`, `help` (und `app`, wenn die Mini App an ist, nur in einem privaten Chat). `kill`, `restart`, `rename`, `delete`, `diff`, `clearqueue`, `perms`, `whoami` und `update` stehen nicht im Menü, funktionieren aber getippt; die Aktionen für Sitzungen gibt es auch unter `/status` → `⋮`.

**Befehle, die auf eine Sitzung wirken** (`/stop`, `/mode`, `/kill`, `/restart`, `/rename`, `/delete`, `/diff`), folgen einer Regel:

- Mit einem Namen (`/restart x1`) wirken sie auf diese Sitzung dieses Chats (`/rename` braucht dafür den alten und den neuen Namen, siehe oben).
- Ohne Namen wirken sie auf die eine Sitzung, die der Befehl meinen kann (die einzige arbeitende bei `/stop`, die einzige laufende bei `/kill` und `/restart`, die einzige beendete bei `/delete`), mit der üblichen Bestätigung, wo es eine gibt.
- Sonst zeigen sie eine Auswahl nur mit den Sitzungen dieses Chats, für die der Befehl Sinn ergibt: zuerst die, an die deine Nachrichten gehen, mit `✍️` markiert, dann die übrigen nach Namen, und `✖️ Abbrechen`. Eine davon zu wählen führt zur selben Karte wie das Tippen ihres Namens.
- In einer Gruppe wirken sie ohne Namen nur auf deine eigene aktuelle Sitzung, wenn der Befehl für sie Sinn ergibt; sonst zeigen sie immer die Auswahl, auch bei nur einer Sitzung, weil sie einem anderen Mitglied gehören kann (siehe [Teambetrieb](groups.md#talking-to-a-session-in-a-group)).

`/mode` und `/diff` sehen zuerst die Sitzung an, an die deine Nachrichten gehen.

### <a id="settings"></a>Einstellungen

`/settings` (und der Tab Einstellungen der Mini App, der dieselben Möglichkeiten zeigt) legt fest, wie die Sitzungen dieses Chats aussehen und antworten. Jeder Chat hat seine eigenen; in einer Gruppe dürfen alle ansehen, und eine Einstellung zu ändern braucht einen Admin. In deinem eigenen privaten Chat änderst du sie selbst, egal welche Rolle du hast, in Telegram und in der Mini App. Eine Zeile mit ✅ ist auf etwas anderes als ihren Standard gesetzt („So sieht die Antwort aus“ zeigt immer die gewählte Möglichkeit).

| Einstellung | Möglichkeiten (der Standard zuerst) | Was sie tut |
|---|---|---|
| 🌐 Sprache | Automatisch (deine Telegram-Sprache), in einem privaten Chat; English | Die Sprache der eigenen Nachrichten und Buttons von aipager in diesem Chat und in der Mini App, unter ihrem eigenen Namen (dieselbe Liste wie `/language`). Sie ändert deine Claude-Sitzungen nicht: Claude antwortet in der Sprache, in der du ihm schreibst. Angeboten werden English, Español, Русский (Russisch), Português (Brasil), 简体中文 (Chinesisch, vereinfacht), Français, Deutsch, 한국어 (Koreanisch), العربية (Arabisch), Bahasa Indonesia, Українська (Ukrainisch), Italiano, Türkçe (Türkisch), فارسی (Persisch) und Tiếng Việt (Vietnamesisch); Automatisch folgt der Sprache deiner Telegram-App (siehe [Sprachen](#languages)). |
| 🖼 So sieht die Antwort aus | Arbeitskarte + eigene Antwort; Antwort in der Karte; Nur die Antwort (ohne Karte) | Wie eine fertige Aufgabe erscheint. Arbeitskarte + eigene Antwort lässt die Karte über der Antwort stehen, als Nachweis, wie sie entstanden ist; Antwort in der Karte setzt die Antwort in die Karte; Nur die Antwort entfernt die Karte und lässt die Antwort allein stehen. Standard ist Arbeitskarte + eigene Antwort (`KEEP_FINISHED_CARD=0` macht daraus Nur die Antwort für Chats, die nie gewählt haben). Siehe [Antworten](#idle-responses). |
| 📝 Diff-Vorschauen | Aus; An | An schickt jedes `Write`/`Edit` als eigene Diff-Nachricht unter die Arbeitskarte. Aus behält eine Karte und eine Antwort; die Karte listet weiterhin jede Änderung, und die Mini App hat eine Diff-Ansicht. |
| ⏱ Karten-Updates bei langen Aufgaben | An; Aus | An: Eine Arbeitskarte wird nach 2 Minuten einer Aufgabe alle 10 s aktualisiert und nach 10 alle 30 s (nie langsamer, egal wie lange die Aufgabe dauert), und ihre Uhr springt dann in 30-Sekunden-Schritten; Aus behält das Tempo der ersten Minuten für die ganze Aufgabe (siehe [Fehlerbehebung](troubleshooting.md#a-long-turns-card-refreshes-less-often)). |
| ✏️ Einfache Formatierung | Aus; An | An bittet Claude nur um einfachen Text und Listen mit Strichen: keine Tabellen, Codeblöcke, Überschriften oder Fettdruck. |
| 📏 Antwortlänge | Keine Regel; Sehr kurz; Kurz; Mittel; Lang | Bittet Claude um Antworten dieser Länge. |
| 🎓 Wortschatz | Keine Regel; Einfache Wörter; Normal; Fortgeschritten | Bittet Claude um diesen Wortschatz. |
| 🎨 Aussehen | Stoppen-Button, Erlauben- und Ablehnen-Buttons, App-Button, Einstellungsmenü | Wie die Buttons von aipager in diesem Chat aussehen; alles beginnt bei Normal (das eigene Aussehen von Telegram). Telegram lässt einen Bot einen Button nur blau, grün oder rot färben, also: der Stoppen-Button (der auf der Arbeitskarte und der unter einer Frage oder Berechtigungsanfrage) Normal, Blau, Grün oder Rot; Erlauben und Ablehnen Normal oder Farbig (Erlauben und Immer erlauben grün, Ablehnen rot); der App-Button (in der angehefteten Nachricht und unter `/app`) Normal, Blau, Grün oder Rot (der Menü-Button neben dem Eingabefeld lässt sich nicht färben); das Einstellungsmenü Normal (✅ markiert die aktuelle Auswahl) oder die aktuelle Auswahl zusätzlich in Blau. Gilt für den ganzen Chat, nicht pro Sitzung; in einer Gruppe ändert das nur ein Admin. Telegram-Apps, die keine Farben können, zeigen alle Buttons normal. Der Tab Einstellungen der Mini App hat dieselbe Auswahl in einem eigenen Abschnitt, Aussehen, nach den Chat-Einstellungen, jede mit einem farbigen Punkt. |
| 🆕 Neue Sitzungen | Berechtigungen (der Modus), Modell, Ordner | Womit `/new` eine Sitzung startet, wenn du nicht wählst: Fragen-, Auto- (der Standard) oder Vollzugriff-Modus, ein Modell aus der Liste der Modelle oder der Standard von Claude Code, und ein Ordner. Nur ein Admin oder eine Person in ihrem eigenen privaten Chat mit dem Bot kann hier den Vollzugriff-Modus wählen (der Bildschirm eines privaten Chats zeigt ihn niemand anderem), und er gilt nie für Personen, die ihn nicht nutzen dürfen: Deren Sitzungen starten im Auto-Modus. |
| 👤 Einstellungen pro Sitzung | eine Sitzung, dann jede Einstellung oben außer Neue Sitzungen | Werte nur für eine Sitzung (auch unter `/status` → `⋮` → `👤 Einstellungen`); ⭐ markiert eine Einstellung, die sie überschreibt, und „Chat-Standard verwenden“ entfernt das Überschreiben. Die Seite der Sitzung in der Mini App setzt dieselben Werte. |
| 🐞 Problemberichte | Mich fragen; Aus | Nur im privaten Chat des Besitzers der Installation. Mich fragen lässt aipager ab und zu anbieten, einen Bericht über einen internen Fehler zu schicken, der mehr als einmal passiert ist (höchstens einmal in 3 Tagen); Aus bietet nie an (der Button Problem melden in `/help` funktioniert weiterhin). Zwei abgelehnte Angebote hintereinander schalten die Angebote ab, und diese Zeile sagt das dann, mit einem Button, um sie wieder einzuschalten. Siehe [Problemberichte](problem-reports.md#when-aipager-offers-a-report). |

Antwortlänge, Wortschatz und einfache Formatierung erreichen Claude als kurze Anweisung, die an jede aus Telegram geschickte Nachricht angehängt wird (durch den Hook, siehe [Hooks](hooks.md#userpromptsubmit)), mit den eigenen Werten der Sitzung, wo sie welche hat.

Egal welches Layout: Jede Arbeitskarte endet mit der Statuszeile ihrer Sitzung (`⏳`/`✅ Name · …`), und jede Antwort beginnt mit ihrer Ergebniszeile (`💬 Name`, plus `· Fertig (…)`, wenn keine fertige Karte übrig ist, die die Zahlen zeigt); Antwort in der Karte stapelt beide, jede Zeile in ihrem eigenen Abschnitt. Über der Statuszeile der Arbeitskarte steht eine Trennlinie (kleine Schrift), und bei Antwort in der Karte trennt eine zweite die fertige Karte von der Antwort. Beim Layout mit Karte folgt die Antwort der fertigen Karte absichtlich einen Moment später, damit man sieht, wie die Karte Fertig sagt, bevor die Antwort darunter erscheint; stell diesen Vorsprung mit `FINISH_CARD_GRACE_SECONDS` ein oder schalte ihn ab (Sekunden, Standard 0,8; 0 schickt beides gleichzeitig). Das Layout entscheidet über die Karte jeder Aufgabe, ob Tools gelaufen sind oder nicht.

### <a id="per-session-dynamic-commands"></a>Eigene Befehle jeder Sitzung

Ein Befehl pro laufender Sitzung, aus ihrem Namen registriert:

| Form | Was er tut |
|---|---|
| `/<label>` | Macht `<label>` zu der Sitzung, an die deine Nachrichten gehen. Die Antwort sagt das mit ihrem Zustand und Modus (`✍️ Du sprichst jetzt mit x1 · 💤 bereit · 🤖 Auto-Modus`, dann „Schreib eine Nachricht, sie geht an x1.“), bietet **Antworten** an, wenn sie auf dich wartet, und **⋮ Mehr** für ihr Menü. Die vollständigen Zahlen stehen in `/status`. In einer Gruppe sagt sie stattdessen „Antworte auf eine Nachricht von x1 (oder erwähne @aipagerbot), um mit ihr zu sprechen.“, weil eine normale Nachricht dort den Bot nicht erreicht (siehe [Teambetrieb](groups.md#talking-to-a-session-in-a-group)). `/x1@aipagerbot`, das Telegram schickt, wenn du `/x1` im Menü einer Gruppe wählst, funktioniert genauso. |
| `/<label> <prompt>` | Schickt `<prompt>` direkt an diese Sitzung. Sie wird außerdem die Sitzung, an die deine nächsten normalen Nachrichten gehen. |
| `/<label> stop` | Unterbricht die laufende Aufgabe dieser Sitzung. |

Die Ergebnisse von `/status` kommen aus denselben Daten, die `aipager status` im Terminal zeigt; für die Sitzungsliste selbst braucht es keinen Weg über Telegram.

Auf dem Computer geben `aipager status` und `aipager session ls` jeder Sitzung einen Zustand: `IDLE`, `BUSY`, `WAITING` (eine Berechtigung oder eine Frage wartet auf dich, mit dem, worum es geht, wenn bekannt, zum Beispiel `permission: Bash: make deploy`) oder `GONE`. Mit `--json` hat jede Sitzung außerdem `waiting_kind` (`permission` oder `question`) und `waiting_summary`, beide `null`, außer sie wartet.

## <a id="languages"></a>Sprachen

aipager spricht English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی und Tiếng Việt, jede unter ihrem eigenen Namen.

- **Nur die Texte von aipager ändern sich.** Die Sprache gilt für die Nachrichten, Buttons, Karten und die Mini App von aipager. Deine Claude-Sitzungen ändern sich nicht, und Claude antwortet in der Sprache, in der du ihm schreibst. Sitzungsnamen, Befehle, Modellnamen und Code bleiben, wie sie sind.
- **`/language`** (oder die erste Zeile von `/settings`) wählt die Sprache des Chats, in dem du bist. In einem privaten Chat ist sie auch deine eigene Sprache: Die kurzen Hinweise, die du in einer Gruppe siehst, und deine Mini App nutzen sie.
- **Automatisch** (nur in privaten Chats) folgt der Sprache deiner Telegram-App, und folgt ihr wieder, wenn du sie änderst. Eine Sprache, die aipager nicht hat, erscheint auf Englisch.
- **Neue und bestehende Chats.** Ein privater Chat, der nach dieser Version beginnt, folgt der Sprache deiner Telegram-App, bis du eine wählst, und seine erste Nachricht in einer anderen Sprache als Englisch bekommt eine kurze Zeile dazu, die das sagt und erklärt, wie man es ändert. Ein Chat, den es schon gab, bleibt Englisch. Ist deine Telegram-App in einer Sprache, die aipager spricht, bekommst du eine Karte, die sie anbietet, mit einem Button „Englisch behalten“; sie kommt einmal, egal was du antwortest.
- **Gruppen** haben eine Sprache, die ein Admin in `/language` festlegt; bis dahin sind sie Englisch. Der kurze Hinweis nach einem Tippen ist in der eigenen Sprache der Person, die getippt hat: ihre Wahl aus ihrem privaten Chat mit dem Bot, sonst die Sprache ihrer Telegram-App, wenn die Person nach dieser Version dazukam, sonst die Sprache der Gruppe. Siehe [Teambetrieb](groups.md).
- **Von rechts nach links.** Auf Persisch und Arabisch läuft der Text von aipager von rechts nach links, mit persischen oder arabischen Ziffern. Die Arbeitskarte folgt der Sprache des Chats, egal in welcher Sprache Claude schreibt: In einem englischen Chat läuft sie von links nach rechts, in einem persischen oder arabischen von rechts nach links, und ein Satz von Claude darauf, der in die andere Richtung geschrieben ist, liest sich trotzdem in seiner eigenen Richtung. Die Antwort folgt Claudes Text: Antwortet Claude auf Persisch oder Arabisch in einem Chat, dessen Sprache von links nach rechts geschrieben wird, ist die ganze Antwort eine Nachricht von rechts nach links, und die eigenen Zeilen von aipager darin (die Agenten unter der Antwort und bei „Antwort in der Karte“ die fertige Karte darüber) stehen wie der Rest rechts und lesen sich trotzdem von links nach rechts.
- **Moduswörter.** `/mode` nimmt das Wort des Modus in deiner Sprache an (`/mode Fragen`), und `ask`, `auto` und `full` funktionieren immer. Hat eine laufende Sitzung genau diesen Namen, nennt das Wort stattdessen die Sitzung (siehe `/mode` oben).
- **Beschreibung des Bots.** Beim Start schreibt aipager die Beschreibung des Bots (der Text auf dem leeren Chat-Bildschirm) und die Kurzbeschreibung in jeder dieser Sprachen, für Personen, deren Telegram in dieser Sprache ist. Telegram zeigt ihnen diesen Text statt dessen, den du in BotFather geschrieben hast, deshalb setzt aipager keinen, solange du einen eigenen hast (Beschreibung und Kurzbeschreibung werden getrennt geprüft). Es überschreibt nie einen Text von dir und ändert nie den englischen oder den Standardtext. Ist sein aktueller Text schon in jeder Sprache gesetzt, fragt ein Start Telegram nichts. Sonst liest es zuerst deinen Text aus BotFather (ein Lesen für die Beschreibung und eins für die Kurzbeschreibung): Hast du einen, hört es dort auf; mit eigenem Text kostet jeder Start also diese Lesevorgänge und nicht mehr. Hast du keinen, liest es jede Sprache, die seinen aktuellen Text noch nicht hat; eine Sprache, in der du einen eigenen Text geschrieben hast, wird also bei jedem Start erneut gelesen. Schreibst du eine Beschreibung in BotFather, nachdem aipager seine gesetzt hat, behalten Personen in diesen Sprachen die von aipager (siehe [Fehlerbehebung](troubleshooting.md#the-bot-description-is-not-the-one-i-wrote)).

## <a id="the-mini-app"></a>Die Mini App

`/app` (und der Menü-Button von Telegram) öffnet ein Dashboard, das aipager selbst bereitstellt. Es ist standardmäßig an; verwalte es mit `aipager miniapp enable|disable|status`. Jede Anfrage wird gegen die `initData`-Signatur von Telegram geprüft (siehe [Sicherheit → Tunnel der Mini App](security.md#mini-app-tunnel)). Die Seite lädt nichts von anderswo: keine Schriften, Bilder oder Skripte von anderen Seiten.

- **Chats.** Die App zeigt einen Chat auf einmal. Bist du in mehr als einem Chat (dein privater Chat und eine Gruppe, sagen wir), wechselt eine Zeile mit Chatnamen oben zwischen ihnen; die App öffnet sich in deinem eigenen privaten Chat und merkt sich deine letzte Wahl auf diesem Handy. Alles darunter gilt für den gewählten Chat, mit deiner Rolle darin. Mit nur einem Chat wird die Zeile nicht gezeigt. Siehe [Teambetrieb](groups.md).
- **Sitzungen.** Ein Satz fasst den Chat zusammen, zum Beispiel „1 braucht dich, 2 arbeiten, 3 bereit“, mit dem, was ausgegeben wurde, darunter. Jede laufende Sitzung ist eine Kachel mit einem Kontextring, ihrem Ordner, Modell und ihren Kosten; arbeitende leuchten in der Akzentfarbe. Fertige Sitzungen liegen in einem einklappbaren Regal **Beendet**. Der Button **+** startet eine neue Sitzung.
- **Brauchen dich.** Eine Sitzung, die auf eine Berechtigungsanfrage oder eine Frage wartet, steht oben in einem bernsteinfarbenen Feld und zeigt, worum sie bittet. **Im Chat antworten** schickt diese Anfrage mit ihren Buttons noch einmal ans Ende des Chats, genau wie der Button **Antworten** der angehefteten Leiste, und du beantwortest sie dort. War es die einzige wartende Sitzung, schließt sich die App, damit die Anfrage direkt vor dir ist; sonst bleibt sie offen und sagt „In den Chat gesendet“. Das braucht das Recht, der Sitzung Nachrichten zu schicken, und eine Kopie, auf die getippt wird, nachdem die Anfrage beantwortet oder ersetzt wurde, sagt „schon beantwortet“.
- **Die Seite einer Sitzung.** Der Kopf zeigt den Kontextring, den Zustand („Arbeitet (3:12)“, „Braucht dich (Berechtigung)“, „Bereit, zuletzt aktiv vor 5 Min.“, „Beendet“) und ein **Stoppen** mit einem Tippen (während sie arbeitet oder wartet) oder **Fortsetzen** (wenn sie beendet ist). Darunter: die wartende Anfrage mit **Im Chat antworten**, die letzten Tool-Aufrufe als Streifen (tippe darauf für den ganzen Verlauf), die neueste Antwort, die Fakten, die Modellsteuerung, die eigenen Einstellungen der Sitzung und die geänderten Dateien und der Verlauf. Das Menü ⋮ enthält jede Aktion, mit denselben Bestätigungen wie bisher.
- **Neue Sitzung.** Eine geführte Karte: der Name, dann wo (mit Kurzwahlen **Zuletzt** für die Ordner, die deine Sitzungen nutzen, die neuesten zuerst), dann das Modell (mit Kurzwahlen **Vorgeschlagen**), dann **Weitere Optionen** für den Berechtigungsmodus und den Antwortstil. Der eigene Button von Telegram unten heißt **Sitzung starten**. Für ein Mitglied, dessen Rolle kein `can_manage` hat, bietet „wo“ nur den [Stammordner](groups.md#root-folders) des Chats und die Ordner direkt darin an (der Stammordner ist der Standard, und Neuer Ordner legt im gewählten an); ein Ordner außerhalb wird mit Grund abgelehnt, und Fortsetzen, Neu starten und der Moduswechsel einer Sitzung, die außerhalb arbeitet, antworten „Diese Sitzung arbeitet außerhalb deines Ordners ~/..., daher kann deine Rolle sie nicht fortsetzen, neu starten oder ihren Modus wechseln. Frag einen Admin.“
- **Einstellungen.** Zuerst die **App-Sprache**: deine eigene Sprache für die Texte von aipager in der App und in deinem privaten Chat (Englisch, Spanisch, Russisch, Portugiesisch (Brasilien), Chinesisch (vereinfacht), Französisch, Deutsch, Koreanisch, Arabisch, Indonesisch, Ukrainisch, Italienisch, Türkisch, Persisch oder Vietnamesisch; Automatisch folgt der Sprache deiner Telegram-App, siehe [Sprachen](#languages)). Alle können ihre eigene ändern, sie wird nie in einem Chat veröffentlicht, und sie ändert deine Claude-Sitzungen nicht. Dann die Einstellungen des Chats (in einer Gruppe kann nur ein Admin sie ändern; in deinem eigenen privaten Chat kannst du es, egal welche Rolle du hast, genau wie mit `/settings` dort) und, für einen Admin, **Updates**, das [`/update`](#update) widerspiegelt. Für den Besitzer in seinem privaten Chat öffnet **Problem melden** eine eigene Seite: Notiz schreiben, den Bericht prüfen, ihn senden und seine Referenz sehen, ohne die App zu verlassen (siehe [Problemberichte](problem-reports.md#how-to-send-one)).

Die App folgt dem hellen oder dunklen Design von Telegram, wenn du es wechselst, und stoppt ihre sanften Animationen, wenn dein Handy um weniger Bewegung bittet. Alles in der Mini App erreichst du auch aus dem Chat: Das Menü ⋮ im Dashboard einer Sitzung enthält dieselben Aktionen.

### <a id="switching-a-running-sessions-model"></a>Das Modell einer laufenden Sitzung wechseln

Die Seite einer laufenden Sitzung hat eine Steuerung **Modell**, die das Modell zeigt, das die Sitzung meldet (aus der Statuszeile von Claude Code), und dieselbe Liste, die die Startauswahl und die Modell-Tastatur des Chats anbieten. Eines zu wählen tippt genau `/model <name>` in diese Sitzung, dieselbe Eingabe, die die Modell-Tastatur des Chats nutzt, mit derselben Regel, wer das darf (alle, die der Sitzung Nachrichten schicken dürfen). Die Steuerung zeigt **wird gewechselt…**, bis die Statuszeile ein anderes Modell meldet, und zeigt es dann; ändert sich innerhalb von 15 Sekunden nichts, zeigt sie **nicht bestätigt (prüfe die Sitzung)**.

- **Nur, wenn die Sitzung bereit ist, nicht während Claude arbeitet oder eine Anfrage offen ist.** Claude Code führt `/model` sofort aus, auch mitten in einer Aufgabe, statt danach, deshalb wird der Wechsel abgelehnt, bis die Aufgabe endet. Die Tastatur des Chats lehnt in denselben Zuständen ab, mit denselben Worten.
- **Die Frage „Switch model?“ von Claude Code.** Claude Code stellt diese Frage meistens vor einem Wechsel, weil die nächste Antwort die ganze Unterhaltung neu lesen muss. Mit Claude Code 2.1.251 oder neuer genehmigt der Hook `PreModelSwitch` von aipager den Wechsel, den aipager gerade getippt hat, die Frage wird also nicht gestellt (siehe [Hooks](hooks.md#premodelswitch)). Jeder andere Wechsel fragt weiterhin. Mit einem älteren Claude Code, oder bei einem Wechsel zu oder von `opusplan`, kann die Frage noch kommen. Der Wechsel erscheint dann als *nicht bestätigt*, und die Frage wartet im Terminal. Beantworte sie dort, bevor du der Sitzung etwas anderes schickst, sonst würde deine nächste Nachricht hineingetippt. Aus demselben Grund lehnt aipager einen weiteren Wechsel dieser Sitzung eine Minute lang nach einem nicht bestätigten ab, oder bis die Sitzung ein neues Modell meldet.

## <a id="the-pinned-status-bar"></a>Die angeheftete Statusleiste

Jeder Chat, in dem aipager spricht (dein privater Chat und jede Gruppe), bekommt eine angeheftete Nachricht: die Statusleiste. Die Leiste von Telegram oben im Chat zeigt die Nachricht ab ihrer **ersten Zeile**, mit aneinandergereihten Zeilen, diese Zeile sagt also, was dich am meisten braucht:

| Erste Zeile | Wann |
|---|---|
| `⏳ jim braucht dich - Bash: make deploy` | eine Sitzung wartet auf eine Berechtigungsanfrage, eine Frage oder eine interaktive Anfrage; `(+2 weitere Sitzungen)`, wenn auch andere warten |
| `⚙️ jim (arbeitet)` / `💤 jim (bereit)` | der Chat hat eine laufende Sitzung, und sie arbeitet / ist bereit (`🔄 jim (startet)`, während sie startet): Diese Zeile ist die ganze Leiste, abgesehen von einer Zeile zu den Limits |
| `⚙️ 2 arbeiten · 1 bereit` | mehrere Sitzungen, keine wartet: wie viele in jedem Zustand sind (`arbeitet`, `bereit`, `startet`); `🔄`, wenn keine arbeitet und eine startet, `💤`, wenn alle bereit sind |
| `💤 alle bereit` | keine laufende Sitzung |

Darunter, nur solange sie zutrifft, eine Zeile zu den Limits (`🐢 sendet langsamer nach einer Warnung von Telegram` (die sechs Stunden nach einem 429) oder `⏸ Karten werden gerade nicht aktualisiert (Stundenlimit von Telegram)` / `(Limit von Telegram)` (Minimalmodus, siehe [Fehlerbehebung](troubleshooting.md#the-hourly-budget))) und dann, wenn der Chat mehr als eine laufende Sitzung hat, `✍️ Nachrichten gehen an jim` (die Sitzung, an die eine normale Nachricht in diesem Chat geht, siehe unten; nicht in einer Gruppe, wo jede Person ihre eigene hat) und eine Zeile pro Sitzung mit ihrem Zustand: `arbeitet`, `braucht dich`, `bereit` oder `startet`. Die Leiste nennt jede Sitzung einmal: Die wartende Sitzung, die die erste Zeile nennt, bekommt keine eigene Zeile, und die erste Zeile listet nie Namen auf, die die Zeilen darunter wiederholen. Eine Sitzung, bei der noch Agenten im Hintergrund arbeiten, sagt auf ihrer eigenen Zeile (oder auf der einen Zeile, bei einer einzigen Sitzung), wie viele: `💤 jim (bereit, 1 Agent arbeitet)`. Befehle, die Claude im Hintergrund ausführt, werden genauso gezählt: `(bereit, 1 Hintergrundbefehl läuft)`, oder `(bereit, 2 Agenten und 1 Hintergrundbefehl laufen)` mit beidem. Es ist eine Zahl, nie ihre Namen, die Leiste bewegt sich also nur, wenn sich die Zahl ändert. Tippe auf die Leiste, um zur Nachricht zu springen.

**Wohin eine normale Nachricht geht.** Eine Nachricht, die keine Antwort ist, geht an die Sitzung, mit der dieser Chat zuletzt gesprochen hat: die, zu der du zuletzt mit `/<label>` gewechselt hast, der du eine Nachricht geschickt hast, die du gestartet hast oder die dir hier zuletzt geantwortet hat. Ein bloßes `/mode`, `/clearqueue`, `/diff` oder `/now` wirkt auf dieselbe Sitzung (`/stop` stoppt die, die gerade arbeitet). Jeder Chat hat seine eigene: Eine Nachricht in einem Chat geht nie an eine Sitzung, die zu einem anderen Chat gehört. In einer Gruppe zählen nur eine Antwort an den Bot, eine Nachricht, die ihn erwähnt, ein Befehl oder ein Tippen auf die Tastatur der Gruppe (siehe [Teambetrieb](groups.md#what-the-bot-reads-in-a-group)), und jede Person hat ihr eigenes Ziel, das die Antwort einer Sitzung nicht verschiebt (siehe [Teambetrieb](groups.md#talking-to-a-session-in-a-group)).

Buttons der angehefteten Nachricht:

- **&lt;label&gt; beantworten** (einer pro wartender Sitzung, höchstens drei) schickt die Anfrage dieser Sitzung mit ihren Antwort-Buttons noch einmal ans Ende des Chats, damit du sie ohne Scrollen beantworten kannst. Wurde sie inzwischen beantwortet, bekommst du stattdessen den kurzen Hinweis „schon beantwortet“, und eine Kopie (oder die ursprüngliche Anfrage), auf die getippt wird, nachdem die Anfrage anderswo beantwortet wurde, wird genauso abgelehnt: Sie beantwortet nie eine spätere Anfrage. Nach einem Neustart von aipager funktioniert die wartende Anfrage weiter über die Arbeitskarte oder ihre eigene Nachricht, aber aipager weiß nicht mehr, welche Anfrage eine ältere Kopie gezeigt hat; ein Tippen auf eine Kopie, die die Leiste vor dem Neustart geschickt hat, wird also mit „diese Anfrage ist nicht mehr offen“ abgelehnt. Alle, die die Anfrage beantworten dürfen, dürfen ihn nutzen; sonst niemand.
- **📱 App** öffnet die Mini App, in deinem privaten Chat, solange die Mini App an ist.

Die Leiste trägt keine Uhr, keine Kosten, keinen Kontext in % und kein Modell, sie ändert sich also nur, wenn sich der Zustand einer Sitzung ändert. aipager bearbeitet sie (still, ohne Benachrichtigung) nur, wenn sich das Angezeigte geändert hat, höchstens einmal alle 30 s pro Chat (`PINNED_MIN_EDIT_GAP`); eine Änderung innerhalb dieses Abstands wird gezeigt, wenn der Abstand vorbei ist, sie geht nie verloren. Sie wird nie bearbeitet, solange der Chat wegen einer Sperre stummgeschaltet ist, und holt nach, wenn die Stummschaltung endet.

In einer Gruppe braucht der Bot Admin-Rechte zum Anheften. Wird das Anheften abgelehnt, löscht aipager die Statusnachricht, die es gerade geschickt hat (damit sie nie unangeheftet im Verlauf der Gruppe liegt), und zeigt in dieser Gruppe keine Leiste, bis aipager neu startet; dasselbe gilt für einen Chat, der den Bot ganz ablehnt (hinausgeworfen, blockiert). Löschst du die angeheftete Nachricht, schickt aipager eine neue und heftet sie an, höchstens einmal pro Stunde und Chat.

## <a id="persistent-keyboard"></a>Feste Tastatur

Unter dem Eingabefeld des Chats sitzt eine feste Tastatur. Reihen, von oben nach unten:

| Reihe | Buttons | Hinweise |
|---|---|---|
| Sitzungen | ein Button pro Name einer laufenden Sitzung | automatisch aus der Sitzungsliste gebaut |
| Aktionen | `Status`, `Stop`, `Neu…` | Abkürzungen als Text (`kill` funktioniert getippt weiterhin; eine Sitzung beenden geht unter `/status` → `⋮`) |
| Navigation | `Vorlagen ›`, `Befehle ›` | Die Mini App öffnest du über den Menü-Button neben dem Eingabefeld oder mit `/app`, nicht über diese Tastatur |

`Modell ›` steckt im Untermenü Befehle. Ein Tippen auf einen Eintrag eines Untermenüs schickt eine fertige Nachricht oder einen Slash-Befehl:

- **Vorlagen**: Nachrichten, die du immer wieder tippst, zum Beispiel „Tests schreiben“, „Plan erklären“, „Notizen aktualisieren“.
- **Befehle**: Slash-Befehle, die Claude Code selbst versteht (`/compact`, `/clear` usw.), sofort eingetippt. Ein Befehl oder eine Vorlage nennt die Sitzung, an die er gegangen ist (`🧹 /clear an x1 gesendet`). Die Unterhaltung zu leeren wird abgelehnt, solange die Sitzung arbeitet (`⚠️ Die Unterhaltung lässt sich nicht leeren, solange die Sitzung arbeitet`).
- **Modelle**: schnelle Modellwechsel für die aktive Sitzung. Es gibt die Familien-Aliase (`sonnet`, `opus`, `haiku`, `fable`, `opusplan`), die immer das neueste Modell dieser Familie meinen, und feste Modelle (`claude-opus-5-5`, `claude-opus-5-5[1m]` mit 1M Kontext, `claude-sonnet-5-5`, `claude-fable-5-1`, `claude-haiku-5-5`). Die Auswahl beim Start und auf der Sitzungsseite der Mini App nutzen dieselbe Liste. Ein Wechsel wird abgelehnt, solange die Sitzung arbeitet oder eine Anfrage offen ist. Die Antwort mit `🔄` ändert sich danach, um das Modell zu zeigen, das die Sitzung meldet, oder sagt, dass der Wechsel nicht bestätigt wurde (siehe [Das Modell einer laufenden Sitzung wechseln](#switching-a-running-sessions-model)).

Um das Standardlayout zu ändern, schreib `~/.config/aipager/keyboard.json`:

```json
{
  "templates": [{"label": "Deploy",  "prompt": "Deploy to staging"}],
  "commands":  [{"label": "Compact", "send": "/compact"}],
  "models":    [{"label": "Sonnet",  "send": "/model sonnet"}]
}
```

Jeder Abschnitt ist unabhängig: Fehlende Abschnitte fallen auf die eingebauten Standards zurück, du kannst also einen ändern, ohne die anderen anzugeben. Fehlerhaftes JSON wird mit einer Warnung im Log ignoriert. Änderungen brauchen einen Neustart von aipager.

## <a id="per-message-inline-buttons"></a>Buttons unter jeder Nachricht

Die meisten Antworten des Bots tragen Buttons, die zur Lage passen:

### <a id="permission-prompts"></a>Berechtigungsanfragen

Wenn Claude ein Tool ausführen will, das eine Zustimmung braucht, wird die Arbeitskarte zu einer Berechtigungsanfrage:

```
🔐 Bash: List the temp directory
ls -la /tmp

  [✅ Erlauben]  [❌ Ablehnen]
  [🟢 Immer erlauben]  [⏹ Stoppen]
```

Die Karte zeigt den echten Befehl (oder Dateipfad), den Claude ausführen will, unter seiner eigenen Beschreibung davon: Erlaube, was du lesen kannst. Ein Zeichen, das die Reihenfolge ändern würde, in der der Text gezeichnet wird (ein Richtungssteuerzeichen von Unicode wie U+202E, das einen Befehl wie einen anderen aussehen lassen kann), wird als Markierung wie `⟨U+202E⟩` gezeigt, und die Karte ergänzt dann `⚠️ Hier stecken versteckte Zeichen, die die Schreibrichtung ändern (angezeigt als ⟨U+...⟩). Ausgeführt wird vielleicht etwas anderes, als du siehst.`

- **Erlauben**: genehmigt diesen einen Aufruf.
- **Ablehnen**: lehnt ihn ab; Claude führt das Tool nicht aus.
- **Immer erlauben**: genehmigt und fügt die dauerhafte Regel hinzu, die Claude anbietet („nicht mehr fragen bei …“). Der Button erscheint **nur, wenn Claude eine solche Regel anbietet**; bei einem Befehl, für den es keine ableiten kann (die meisten zusammengesetzten Befehle), trägt die Karte stattdessen Erlauben / Ablehnen / Stoppen, wie auch bei einem Dateizugriff, der nur liest (`Read`, `Grep`, `Glob`), außerhalb des Arbeitsordners der Sitzung. Claude Code 2.1.259 und neuer setzt an diese Stelle seines eigenen Bash-Dialogs eine Zeile „in den Auto-Modus wechseln“, in den Dialog zum Lesen außerhalb eine Zeile „ab jetzt Lesen außerhalb der Arbeitsordner blockieren“ und, bei einem Write/Edit, für das es keine Regel pro Datei ableiten kann, einen Wechsel des Berechtigungsmodus wie das automatische Annehmen aller Dateiänderungen (`acceptEdits`); aipager wählt nie eine davon; wechsle Modi bewusst mit `/mode`.
- **Stoppen**: unterbricht die Aufgabe, statt zu antworten.

Die Antwort geht über den Berechtigungs-Hook von aipager an Claude Code, der bis zu 20 Sekunden darauf wartet. Eine Anfrage, die vor der Arbeitskarte kam (sie wird dann als eigene Nachricht geschickt, nur mit Erlauben und Ablehnen), wird genauso beantwortet. Ist diese Wartezeit vorbei, tippen die Buttons die Antwort stattdessen in den eigenen Dialog von Claude Code; Ablehnen wählt dann die letzte Zeile des Dialogs („No, and tell Claude what to do differently“), die die Aufgabe von Claude wie Stoppen beendet, und die Karte sagt `🚫 jim · Abgelehnt`.

Jedes Tippen wird in `~/.claude/aipager-audit.jsonl` festgehalten und als einzeilige Antwort unter der Arbeitskarte gespiegelt: `✅ jim · Erlaubt · @alice · Bash: List the temp directory` (die Beschreibung des Aufrufs, oder sein Befehl, wenn er keine hat; bei einem mit `aipager config` eingerichteten Chat; im persönlichen Betrieb nur `Erlaubt`).

Solange eine Anfrage (oder ein AskUserQuestion) auf dich wartet, gilt die Sitzung als wartend, nie als bereit. Meldet Claude Code in dieser Zeit, dass die Eingabe ruht, bekommt der Chat `⬆️ jim · wartet noch auf deine Antwort oben` als Antwort auf die Anfrage, einmal pro Wartezeit.

Dialoge von `AskUserQuestion` erscheinen genauso, mit einem Button pro Option (die ersten vier), und bei einer Frage, die mehrere Antworten erlaubt, mit Kästchen ⬜/☑ und einem Button `✅ Senden`. Die Frage und die Beschreibung jeder Option werden ganz gezeigt, wie im Terminal. Gekürzt wird nur ein Formular, das für eine Telegram-Nachricht zu lang ist: Die Arbeitskarte zeigt zuerst weniger ihrer Tool-Zeilen, dann werden die längsten Texte unter Frage und Beschreibungen an einer Wortgrenze mit `…` gekürzt.

**Mehr als eine Anfrage.** Claude Code kann auf zwei Anfragen zugleich warten: Ein Hintergrund-Agent bittet um eine Berechtigung, während Claude dir eine Frage stellt, oder umgekehrt. aipager zeigt jede davon: die erste auf der Arbeitskarte (oder als eigene Nachricht), jede spätere als eigene Nachricht unter der Karte, mit eigenen Buttons. Die Sitzung gilt als wartend, bis die letzte beantwortet ist, und eine Nachricht, die du in der Zeit schickst, wird bis dahin zurückgehalten. Beantworte sie in beliebiger Reihenfolge. Erlauben und Ablehnen gehen über den Berechtigungs-Hook, solange er wartet, und der erreicht genau diese Anfrage, wo auch immer sie steht. Ein Tippen, das Tasten eingeben muss (eine Option einer Frage, oder Erlauben und Ablehnen, wenn der Hook nicht mehr wartet), gibt sie nur in die Anfrage ein, die Claude Code zuerst zeigt: Claude Code zeigt immer nur einen Dialog, und Tasten landen im obersten. Sonst gibt das Tippen nichts ein, behält seine Buttons und sagt `Claude Code zeigt gerade zuerst eine andere Anfrage. Beantworte erst die andere, dann diese.` oder, wenn aipager die Reihenfolge nicht kennt (etwa bei einem Agenten, dessen Start es nicht gesehen hat), `aipager kann nicht erkennen, welche Anfrage Claude Code zuerst zeigt. Beantworte diese deshalb im Terminal.` Wird die Anfrage der Karte zuerst beantwortet, zeigt die Karte, dass sie wartet, nur noch mit Stoppen, und die anderen Anfragen behalten ihre Buttons. Beendet Claude seine Aufgabe, während die Anfrage eines Agenten noch offen ist, kommt die Antwort wie gewohnt, und die Sitzung wartet weiter auf diese Anfrage, die noch einmal als eigene Nachricht kommt. Ein Neustart mit zwei oder mehr offenen Anfragen bringt keine davon zurück: Ihre Buttons antworten `schon beantwortet` und geben nichts ein, und die Anfragen lassen sich weiter im Terminal beantworten. Eine einzelne offene Anfrage kommt wie bisher zurück.

Bei 80 % Kontext bekommt der Chat `⚠️ name · Kontext bei 82% - wird bald automatisch komprimiert` mit **📦 Jetzt komprimieren**, das `/compact` an diese Sitzung schickt. Der Hinweis kommt einmal pro Unterhaltung: Komprimieren bringt ihn nicht zurück, und eine neue Unterhaltung (zum Beispiel nach `/clear`) bekommt ihn wieder.

### <a id="stale-buttons"></a>Alte Buttons

Buttons, die auf eine laufende Aufgabe wirken (Stoppen, Beenden, Neu starten, der Wechsel von `/mode` und sein Stoppen & wechseln, Ersetzen bei `/new`), sind an die Aufgabe gebunden, für die sie gezeigt wurden. Einer, der von einer früheren Aufgabe übrig ist, ändert nichts, statt auf das zu wirken, was gerade läuft, und ebenso einer, der gezeigt wurde, bevor die Sitzung endete und fortgesetzt oder neu gestartet wurde. Beenden, Neu starten, der Wechsel von `/mode` und das Stoppen in `/status` oder in der Auswahl von `/stop` antworten `x1 hat eine neue Aufgabe angefangen: …` mit dem, was du noch einmal antippen oder senden sollst (oder `Diese Sitzung ist beendet.`); das Stoppen der Arbeitskarte antwortet `Diese Aufgabe ist schon fertig: …`, und Stoppen & wechseln von `/mode` macht seine Karte wieder zur Karte von `/mode`, unter `⚠️ x1 hat eine neue Aufgabe angefangen, daher hat sich nichts geändert.` Ersetzen bei `/new` sagt `x1 hat inzwischen angefangen zu arbeiten. Sende /new noch einmal, um sie zu ersetzen.`, wenn die Sitzung eine Aufgabe begonnen hat, nachdem ihre Karte gezeigt wurde.

Die Buttons einer Anfrage gehören zu dieser Anfrage. Ist sie beantwortet, in Telegram oder im Terminal, antworten sie `schon beantwortet` und geben nichts ein, auch solange eine andere Anfrage der Sitzung noch offen ist.

Ein Button `⚡ Jetzt senden` wirkt nur auf die Nachricht, unter der er steht: Hat Claude diese Nachricht schon abgeholt, antwortet das Tippen `Claude hat sie schon abgeholt`, schickt nichts und entfernt die Zeilen der Warteschlange.

### <a id="idle-responses"></a>Antworten

Wenn eine Aufgabe endet, kommt die Antwort so, wie dein Layout es vorgibt (siehe unten und [Einstellungen](#settings)). Ist die Antwort von Claude länger, als eine Nachricht fassen kann (etwa 32 KB), zeigt die Nachricht so viel, wie unter einen Hinweis `📎 Die ganze Antwort ist unten angehängt ↓` passt, und die ganze Aufgabe (ihre Schritte und die vollständige Antwort) folgt als `<name>_full_log.md`, das Telegram ohne andere App anzeigt. Dieselbe Datei wird angehängt, wenn die Arbeitskarte Zeilen weglassen musste.

Eine Aufgabe, die mit einem API-Fehler von Anthropic endet (Guthaben, Überlastung, Ratenlimit, Netzwerk), zeigt statt einer Antwort `⚠️ name · <what went wrong>`, mit **🔄 Erneut versuchen**, das die letzte Nachricht noch einmal an dieselbe Sitzung schickt. Solange der Chat [wegen einer Sperre stummgeschaltet](troubleshooting.md#the-bot-went-quiet-flood-control) ist, tut ein Tippen gar nichts (die Nachricht wird nicht noch einmal geschickt, und der Button bleibt), tippe also noch einmal darauf, wenn die Sperre vorbei ist.

Beim Layout mit Karte („Arbeitskarte + eigene Antwort“) bleibt die fertige Karte bei jeder Aufgabe über der Antwort stehen, als Nachweis, wie sie entstanden ist: ihre Tool-Zeilen, ihre Agenten-Zeilen und was Claude dazwischen gesagt hat, oder nur `✅ name · Fertig · Ns` bei einer Aufgabe ohne Tools. Die Antwort kommt als eigene Nachricht (mit Benachrichtigung). Für eine Nachricht pro Aufgabe wähl stattdessen „Antwort in der Karte“ (die Antwort kommt in die Karte) oder „Nur die Antwort (ohne Karte)“ (die Karte wird entfernt, und die Antwort steht allein).

Jede Aufgabe hat genau eine Arbeitskarte. Eine Nachricht, die du schickst, während eine Aufgabe läuft, fügt dieser Aufgabe nie eine zweite Karte hinzu, und eine Karte, deren Aufgabe beendet ist, behält ihren Button Stoppen nicht mehr: Hat nichts sie geschlossen (ein verlorener Stop-Hook, ein Neustart), schließt aipager sie nach 30 Sekunden, so wie dein Layout es vorgibt. Nach einem Neustart von aipager bleibt eine Karte, deren Aufgabe noch läuft, erhalten und tickt sofort weiter, mit der Zeit ab dem Start der Aufgabe gezählt, und die Sitzung gilt als arbeitend, bis die Aufgabe endet; jede andere Karte von vor dem Neustart wird geschlossen („aipager wurde neu gestartet“, ohne Stoppen). Eine Sitzung, die auf eine Berechtigungsanfrage oder eine einzelne Frage von Claude gewartet hat, kommt wartend zurück, nicht arbeitend: Ihre Karte und die angeheftete Leiste sagen, dass sie dich braucht, und die Buttons der Anfrage beantworten sie weiterhin, außer die gespeicherte Unterhaltung zeigt, dass sie inzwischen im Terminal beantwortet wurde. Fragen mit mehreren Teilen oder mehreren Auswahlen und Anfragen eines Subagenten bleiben dem Terminal überlassen. Steht fest, dass eine Anfrage vorbei ist (per Tippen oder im Terminal beantwortet, ihre Aufgabe beendet, oder durch einen Neustart nicht zurückgeholt, weil sie inzwischen beantwortet wurde oder nicht gespeichert und zurückgelesen werden konnte), antworten ihre alten Antwort-Buttons „schon beantwortet“ und tippen nichts, auch nach einem Neustart. Eine Anfrage, die niemand beantwortet, bleibt offen, solange Claude auf sie wartet. aipager hört nur dann auf, auf sie zu warten, wenn Claude 5 Minuten lang nichts meldet und die gespeicherte Unterhaltung zeigt, dass die Anfrage vorbei ist (die Nachricht von einer Antwort ging verloren); eine solche Anfrage gilt nicht als vorbei, ihre Buttons beantworten also weiterhin den Dialog. Dabei zählt nur, was Claude selbst und der fragende Agent melden: Ein Hintergrund-Agent, der für sich arbeitet, zählt nicht, und die gespeicherte Unterhaltung sagt nichts über die Anfrage eines Agenten, die auf dessen eigene Meldungen wartet.

Eine Aufgabe, die Claude **von selbst** beginnt (ein Hintergrund-Agent, der sich mit einer `<task-notification>` meldet, wenn kein Auftrag offen ist), bekommt ihre Arbeitskarte erst, wenn sie etwas tut: bei ihrem ersten Tool-Aufruf oder nach 15 s, was zuerst kommt. Die meisten dieser Weckrufe sind ein paar Sekunden „nichts Neues“; die zeigen jetzt nur die Antwort. Die Anzeige „schreibt…“ erscheint weiterhin, während sie läuft, und die Antwort selbst wird immer zugestellt: Nichts wird als unwichtig herausgefiltert. Aufgaben, die du beginnst, aus Telegram oder im Terminal, bekommen ihre Karte weiterhin sofort.

Während eine Sitzung arbeitet, bekommt jeder Hintergrund-Agent, den Claude startet (über das Tool `Agent` von Claude Code, `Task` in älteren Versionen), seine eigene Zeile auf der Arbeitskarte: `🤖 <type> · <activity> · <elapsed>`, die den Typ des Agenten zeigt und was er gerade tut, aktualisiert, wenn seine eigenen Tool-Aufrufe ankommen. Hat dieser Agent drei oder mehr Tool-Aufrufe gemacht, klappen sie direkt unter seiner Zeile in ein eigenes Antippfeld `▸ N Tools genutzt` ein; sie erscheinen nie im Verlauf der Hauptaufgabe oder in deren Zählungen `Bash ×N`. Ist der Agent fertig, kommt seine Zeile zu `✅ 🤖 <type> · N Tools genutzt · <elapsed>` zur Ruhe und behält dasselbe Antippfeld. Ein Agent, den ein anderer Agent gestartet hat (ein Helfer), nennt vor seinem eigenen Typ den Agenten an der Spitze seiner Kette, `🤖 pipeline-runner › ship-tester`, wenn aipager beim Start weiß, wer ihn gestartet hat; die Zeilen bleiben in der Reihenfolge, in der die Agenten gestartet sind. Die `.md`-Datei oben bekommt einen Abschnitt Agenten mit jedem Agenten, der in der Aufgabe lief, seiner Dauer, seiner Zahl an Tools und den Tools, die er genutzt hat.

Solange Hintergrund-Agenten nach dem Ende ihrer Aufgabe noch laufen, bleibt die Karte als Status des Auftrags stehen. Holt Claude in der Zwischenzeit eine neue Nachricht ab, ist die Live-Karte die unter dieser Nachricht und zeigt weiterhin die laufenden Agenten. Eine Nachricht, die du an die bereite Sitzung schickst, beginnt eine neue Aufgabe: Die Karte der vorigen Aufgabe kommt dort zur Ruhe, wo sie steht, mit ihren Schritten, und die neue Aufgabe bekommt ihre eigene Karte. Eine Nachricht, die in der Warteschlange stand und abgeholt wird, setzt die Aufgabe des Auftrags fort: Ihre Karte wird unter dieser Nachricht noch einmal geschickt und die alte Kopie gelöscht. Tool-Aufrufe innerhalb eines Agenten erscheinen nie als eigene Zeilen der Hauptaufgabe und lassen aipager nie glauben, dass eine neue Aufgabe begonnen hat.

Ein Befehl, den Claude im Hintergrund ausführt (`run_in_background`, Ctrl+B, oder einer, den Claude Code dorthin verschiebt, wenn er zu lange dauert), bekommt eine eigene Zeile, solange er läuft: `⏳ Hintergrundbefehl: <description> (3:12)`, benannt nach der Beschreibung des Aufrufs oder sonst der ersten Zeile des Befehls. Endet er, kommt die Zeile zu `✅ Hintergrundbefehl: <description> - fertig (6:12)`, `❌ ... - mit Fehler beendet (Exit-Code 1)` oder `⏹ ... - gestoppt (6:12)` zur Ruhe (Claude hat ihn mit TaskStop gestoppt, oder Claude Code hat ihn wegen Speichermangels gestoppt); fehlgeschlagene und gestoppte Zeilen behalten diese Markierung auf der fertigen Karte. Eine Aufgabe, die endet, während ein solcher Befehl noch läuft, ist nicht fertig: Die Karte wartet als Status des Auftrags (`🔄 name · 1 Hintergrundbefehl noch bei der Arbeit · 4:10`, oder `1 Agent (ship-reviewer) und 1 Hintergrundbefehl noch bei der Arbeit` mit einem Agenten dazu), und wenn der Befehl endet, geht die Fortsetzung von Claude auf derselben Karte weiter. Befehle, die in einem Agenten gestartet wurden, gehören diesem Agenten und werden nicht gezeigt. Ein Befehl, dessen Ende aipager nie sieht (ein Neustart, ein verlorenes Ereignis), wird nach zwei Stunden nicht mehr gezählt (`AIPAGER_BG_SHELL_MAX_TRACK`, in Sekunden): Seine Zeile sagt `⏹ ... - Ende nicht gesehen`, und ein Auftrag, der nur noch auf ihn gewartet hat, schließt mit `⚠️ name · Fertig (2:00:04, keine Rückmeldung vom Hintergrundbefehl)`.

Wird der Verlauf einer Aufgabe lang, klappt jede ältere Folge von Tool-Aufrufen (drei oder mehr hintereinander, und nicht die gerade laufende) genau dort, wo sie passiert ist, in ein eigenes Antippfeld `▸ N Tools genutzt` ein, statt sich vollständig zu stapeln oder von der Längengrenze für Nachrichten von Telegram abgeschnitten zu werden; tippe auf eines, um es an Ort und Stelle zu lesen. Kommentare werden nie eingeklappt; die neueste Aktivität und die Statuszeile sind immer sichtbar, ohne auf etwas zu tippen. Die eigene Zeile eines Hintergrund-Agenten, der noch läuft (oder gerade zur Ruhe gekommen ist), wird nie selbst eingeklappt, nur seine Tool-Aufrufe, und nie, solange sie das Einzige zwischen dem Verlauf und der Grenze ist. Nur wenn der Verlauf so groß ist, dass selbst alles eingeklappt nicht passt, wird wirklich Inhalt aus der Karte entfernt; dann enthält die `.md`-Datei oben die vollständige Aufzeichnung.

#### <a id="agents-still-running-when-the-answer-goes-out"></a>Agenten, die noch arbeiten, wenn die Antwort rausgeht

Claude Code kann Agenten im Hintergrund laufen lassen, und die Aufgabe, die sie gestartet hat, kann enden, während sie arbeiten. Die Antwort endet dann mit einer Trennlinie und einem kurzen Status dieser Hintergrundarbeit, im Stil des eigenen Agenten-Panels von Claude Code, und wird aktuell gehalten, solange sie läuft. Alles unter der Trennlinie steht in kleiner Schrift (der Fußzeile von Telegram), damit die Antwort selbst hervorsticht:

```
⏳ 5 Agenten arbeiten · 🖥 1 Hintergrundbefehl (shell) läuft

○ 🤖 pipeline-runner · Writing final.md for the pipeline

- 🤖 ship-tester · Write and run criteria tests
- 🤖 ship-developer · Editing state.py
- 🤖 ship-developer › ship-reviewer · Collect line numbers

‖ 🤖 r2-runner · wartet auf seine eigenen Agenten

Wenn die Agenten fertig sind, kommt das Ergebnis genau hier.
```

- **Die erste Zeile zählt.** Arbeitende (oder fertige) Agenten und die Hintergrundbefehle dieses Auftrags, die laufen, fertig oder gestoppt sind. Ein Hintergrundbefehl ist immer nur eine Zahl, nie sein Befehl.
- **Eine Zeile pro Agent,** ein 🤖 und sein Typ fett, dann was er gerade tut: seine eigene neueste Textzeile (die Worte von Claude, so wie sie geschrieben sind, nie übersetzt), sonst sein neuestes Tool als kurzer Ausdruck, der nennt, woran er arbeitet (`Reading state.py`, `Writing final.md`, `Searching for …` oder die eigene Beschreibung eines Befehls), sonst die Aufgabe, die Claude dem Agenten beim Start gegeben hat, sonst nur sein neuestes Tool (`Running a command`, `Using TodoWrite`), sonst nur der Typ. So zeigen zwei Agenten desselben Typs, die Befehle ohne Beschreibung ausführen, trotzdem jeweils ihre eigene Aufgabe. Ein Agent, der angehalten hat, um auf Agenten zu warten, die er selbst gestartet hat, sagt stattdessen `‖ … wartet auf seine eigenen Agenten`.
- **Agenten als Baum.** Die Agenten, die die Sitzung gestartet hat, sind die erste Ebene. Jeder Agent, den einer von ihnen gestartet hat, egal wie tief, steht direkt darunter, jeder direkt nach dem Agenten, der ihn gestartet hat; einer, den ein Agent der zweiten Ebene gestartet hat, nennt diesen Agenten zuerst (`ship-developer › ship-reviewer`). Die Arbeitskarte nennt denselben Agenten stattdessen nach dem Agenten an der Spitze (`pipeline-runner › ship-reviewer`). Es gibt nie mehr als zwei Ebenen. Ein arbeitender Agent auf der zweiten Ebene hat kein eigenes ○: Telegram zeichnet dort einen Aufzählungspunkt. Ein Agent, von dem aipager noch nicht weiß, wer ihn gestartet hat, steht auf der ersten Ebene, nie unter einer Vermutung, und rückt unter seinen Starter, sobald der bekannt ist.
- **Höchstens sechs Zeilen,** zuerst die Agenten, die noch arbeiten, dann `+N weitere Agenten` für die Agenten ohne Zeile. Ein Agent erscheint nie ohne die Agenten über ihm.
- **Das Versprechen** „Wenn die Agenten fertig sind, kommt das Ergebnis genau hier.“ bleibt nur, solange ein Agent noch arbeitet. Hintergrundbefehle tragen es nie: Ein Entwicklungsserver endet vielleicht nie.

Die Zeile jedes Agenten kommt von selbst zur Ruhe, wenn dieser Agent stoppt, zu `✓ 🤖 Explore · fertig (0:42)` (wie lange dieser Agent lief), und die erste Zeile zählt herunter. Ein Agent, den ein aufgeführter Agent startet, nachdem die Antwort raus ist, kommt beim nächsten Update unter ihm in den Status; einen, den die Sitzung selbst später startet, führt stattdessen eine spätere Antwort auf. Sind alle Agenten fertig, weichen die Zeilen einer einzigen Zeile, mit der Zeit von der Antwort bis zum Ende des letzten Agenten:

```
✅ 2 Agenten fertig (6:12) · 🖥 1 Hintergrundbefehl (shell) läuft
```

und wenn dieser Befehl endet, `✅ 2 Agenten fertig (6:12) · 1 Hintergrundbefehl (shell) fertig (2:13:05)` (wie lange der Befehl lief; `gestoppt`, wenn er beendet oder von Claude gestoppt wurde, `fertig`, wenn er von selbst endete, mit Fehler oder nicht; seine Zeile auf der Karte sagt, wie). Eine Antwort, zu deren Auftrag nur ein Hintergrundbefehl gehört, zeigt `🖥 1 Hintergrundbefehl (shell) läuft`, was zu `1 Hintergrundbefehl (shell) fertig (…)` oder `1 Hintergrundbefehl (shell) gestoppt (…)` zur Ruhe kommt.

Gezählt werden nur die Hintergrundbefehle des eigenen Auftrags der Antwort: Ein Entwicklungsserver, den eine frühere Aufgabe laufen ließ, wird nicht unter jeder Antwort wiederholt (die angeheftete Leiste zählt weiterhin jeden). `/stop` beendet den Auftrag, aber Escape beendet keinen Hintergrundbefehl: Einer, der noch läuft, bleibt in der angehefteten Leiste, bis er endet, und wird unter späteren Antworten nicht mehr gezählt; seine spätere Fortsetzung beginnt dann eine eigene Aufgabe.

Diese Antwort geht raus, sobald die Aufgabe endet, als normale Nachricht (mit Benachrichtigung) als Antwort auf deine Nachricht: derselbe Text, den das Terminal zeigt. Die Arbeitskarte bleibt bei jedem Layout darüber als Live-Status des Auftrags stehen: `🔄 name · 1 Agent (general-purpose) noch bei der Arbeit · 1:18`, mit ihrem Button **Stoppen**. Melden sich die Agenten, kommt die Antwort von Claude darauf als eigene Nachricht; die frühere Antwort wird nie noch einmal geschickt, auch nicht nach einem Neustart von aipager. Ein Auftrag, dessen Agenten sich mehr als einmal melden, erzeugt eine Antwort pro Meldung, jede so geschickt, wie sie geschrieben wird.

Ein Agent, der gestoppt hat, während eigene Hintergrundarbeit noch läuft, zählt als arbeitend, weil er später weitermacht. Ein Agent, der einen eigenen Agenten gestartet und dann gestoppt hat, um auf ihn zu warten, wird an diesem Start erkannt (an seinem eigenen Aufruf, der den anderen Agenten gestartet hat): Er bleibt im Status als arbeitend und hält den Auftrag offen, bis er stoppt, ohne dass noch einer seiner eigenen Agenten läuft. Hat er zwei Minuten nach dem Ende des letzten von ihnen nicht weitergemacht, wird er nicht mehr als laufend gezählt, und der Status bleibt, wie er zuletzt gezeigt wurde. Die `<task-notification>` von Claude für einen Agenten sagt dasselbe in einem Hinweis; aipager liest ihn, wenn diese Meldung eine Aufgabe beginnt, und auch, wenn Claude sie mitten in einer laufenden Aufgabe abholt, solange die Arbeitskarte da ist. Eine Zeile wartet ein paar Sekunden, bevor sie fertig zeigt, damit dieser Hinweis sie vorher zurücknehmen kann. Der Status wird bei jedem Layout hinzugefügt, in der Sprache des Chats.

**Wie er aktuell gehalten wird.** aipager bearbeitet die Antwort still, nur wenn sich das geändert hat, was der Status zeigt, und höchstens einmal alle 30 Sekunden pro Antwort (die Antwort selbst zählt als die erste). Jede Antwort mit Status wird so aktuell gehalten (bis zu fünf offene pro Sitzung). Die Bearbeitungen haben niedrige Priorität: Eine, die das Sendebudget ablehnt oder die kommen würde, während der Chat stummgeschaltet ist, wird nicht gemacht, und der Status wartet auf seine nächste Änderung. Die letzte Bearbeitung, wenn alles geendet hat, wird nach dem Ende der Stummschaltung oder nach einer Ablehnung durch das Budget noch einmal versucht und dann aufgegeben. Keine Bearbeitung wird in einer gelöschten Antwort gemacht, für eine Sitzung, die beendet oder abgebrochen wurde, oder wenn sie die Antwort für Telegram zu lang machen würde (wird eine lange Antwort gekürzt, bleibt Platz für den Status).

Der Status bleibt, wie er zuletzt gezeigt wurde, und wird nie zu fertig, wenn aipager nicht wissen kann, dass die Arbeit fertig ist:

- ein Agent, von dem aipager 30 Minuten lang nichts hört (`AIPAGER_SUBAGENT_SILENCE`), wird nicht mehr als laufend gezählt, aber Stille ist kein Abschluss;
- ein Hintergrundbefehl, der länger als zwei Stunden läuft (`AIPAGER_BG_SHELL_MAX_TRACK`), ohne dass sein Ende gesehen wird, wird nicht mehr gezählt;
- ein Neustart von aipager vergisst, welche Antworten offen sind.

Ein Neustart von aipager behält die Agenten, die noch laufen, auch die, die auf ihre eigenen Agenten warten: Eine Antwort nach dem Neustart listet sie auf, eine Arbeitskarte, die der Neustart übernimmt, zeigt sie wieder, und ihr späteres Ende wird wie gewohnt zugeordnet. Agenten, die mehr als 30 Minuten vor dem Neustart gespeichert wurden, werden nicht zurückgeholt.

Eine Antwort, die eine Stummschaltung zurückgehalten hat oder die als einfacher Text zugestellt wurde, nachdem das formatierte Senden fehlschlug, geht ohne den Status raus, ebenso eine Aufgabe, die ohne Antworttext endet (nur eine Kopfzeile).

### <a id="end-confirmation"></a>Bestätigung zum Beenden

`/kill`, seine Auswahl und `⋮` → `⏹ Sitzung beenden` bestätigen immer:

```
⏹ jim beenden? Claude stoppt und die Sitzung endet.

  [⏹ Beenden]  [Abbrechen]
```

Nach `⏹ Beenden` sagt die Nachricht `⏹ jim beendet` über den restlichen Sitzungen dieses Chats. Eine Bestätigung, auf die getippt wird, nachdem die Sitzung eine neue Aufgabe begonnen hat, wird abgelehnt (`jim hat eine neue Aufgabe angefangen: …`), ebenso eine für eine Sitzung gleichen Namens, die seitdem gestartet wurde.

### <a id="voice-install-when-extra-isnt-installed"></a>Die Sprachfunktion installieren (wenn das Paket fehlt)

Wenn du eine Sprachnachricht schickst und `aipager[voice]` nicht installiert ist:

```
⚠️ Für Sprachnachrichten braucht es ein optionales Paket
   (etwa 200 MB zum Installieren und etwa 74 MB Modell bei der ersten Nutzung).

  [📦 Sprachfunktion installieren]  [Abbrechen]
```

Ein Tippen auf Installieren führt den passenden Installationsbefehl für deinen Installer aus (`uv tool install --reinstall aipager[voice]`, `pipx install --force`, oder als letzter Ausweg `pip install faster-whisper`), mit einer Bearbeitung der Nachricht alle 5 s, und bietet bei Erfolg einen Button `[🔄 aipager neu starten]` an.

### <a id="restart"></a>Neustart

`🔄 aipager neu starten`:

- Ein aipager, das als systemd-Benutzerdienst läuft, plant ein abgekoppeltes `systemctl --user restart aipager.service` 5 s später (mit einem Timer, der auf 1 s genau ist), in einem vorübergehenden Dienst außerhalb der Prozessgruppe von aipager selbst, damit es das Ende von aipager übersteht. Es verweigert, solange der Dienst deine Sitzungen beenden würde (`KillMode` anders als `process` oder `none`), und sagt dir, zuerst `aipager service install` auszuführen.
- macOS, wenn der launchd-Agent installiert ist (`~/Library/LaunchAgents/com.aipager.daemon.plist`): `launchctl kickstart -k gui/<uid>/com.aipager.daemon`, auch bei einem aipager, das du selbst gestartet hast.
- Sonst ein aipager, das du selbst gestartet hast (`aipager start`), auf Linux auch, wenn der Dienst zusätzlich installiert ist: Es startet einen abgekoppelten Nachfolger, der wartet, bis der Elternprozess endet, und dann denselben Befehl ausführt, mit dem aipager gestartet wurde (seine Ausgabe geht in das Log des Selbstneustarts). Das aktuelle aipager beendet sich sauber, sobald der Nachfolger lebt.

Kein SSH nötig. Um ein aipager-Update abzuschließen, startet **🔄 Jetzt neu starten** auf der Update-Karte aipager stattdessen an Ort und Stelle neu, im selben Terminal (siehe [Update](#update)).

## <a id="update"></a>Update

`/update` (nur für Admins; im persönlichen Betrieb nur für die Person, die aipager installiert hat) antwortet mit einem Button, **🔄 Nach Updates suchen**. Gesucht wird erst, wenn du darauf tippst. Das Tippen macht aus dieser Nachricht eine Zeile pro Produkt:

- `aipager 1.0.1 → 1.1.0`, wenn es eine neuere Version gibt (die neueste auf PyPI);
- `aipager 1.0.1 (aktuell)`, wenn nicht;
- `Claude Code 2.1.282 (konnte nicht geprüft werden)`, wenn die Suche fehlgeschlagen ist (Netzwerk weg, Zeitlimit von 5 s, oder `claude` nicht gefunden).

Claude Code wird mit der neuesten Version seines eigenen Update-Kanals verglichen (`autoUpdatesChannel`: latest, stable oder rc). Unter den Zeilen steht klein, wie aipager installiert wurde (zum Beispiel `pipx, from PyPI` oder `pipx, from local path …`; Gruppenchats zeigen nie Pfade).

Ist etwas neuer, gibt es EINEN Button, der nur die Produkte aktualisiert, die ein Update haben: **aipager aktualisieren**, **Claude Code aktualisieren** oder **Beide aktualisieren**, dazu **Abbrechen**. Wird aipager angeboten, sagt die Nachricht auch, wie der Neustart passiert: `Neustart: automatisch, sobald keine Aufgabe läuft.`, `Neustart: Tippe nach der Installation auf „Jetzt neu starten“.` (ein aipager ohne Dienst, siehe unten) oder `Neustart: manuell (Grund)`.

Ist ein neueres aipager schon installiert, das laufende aipager aber noch das alte (ein Update, das ohne Neustart fertig wurde, oder ein `pip install -U` von Hand), lautet die Zeile `aipager 1.1.0 ist installiert und wartet auf einen Neustart (läuft noch: 1.0.1).`, und die Nachricht bietet **🔄 Jetzt neu starten** statt eines Updates an: aipager aktualisieren wird nie für eine Version angeboten, die nur einen Neustart braucht. Die installierte Version wird geprüft, indem der Interpreter gestartet wird, in den aipager neu starten würde; eine Version, die installiert ist, sich aber nicht laden lässt, sagt das also (`aipager 1.1.0 ist installiert, lässt sich aber nicht laden. Installiere es neu mit …`) und bietet nichts an. Eine neuere Veröffentlichung als die installierte wird weiter als Update angeboten. Ein neueres aipager bei einer Installation, die sich von hier aus nicht aktualisieren lässt (editable, Nix, Snap, ein Paket des Systems, ein Container oder die Installation eines anderen Benutzers), wird mit dem Grund gezeigt und nicht angeboten. Dasselbe gilt für ein neueres Claude Code, das mit npm in einen Ordner installiert wurde, in den dein Benutzer nicht schreiben kann (zum Beispiel `sudo npm install -g` unter `/usr/lib/node_modules`), wo `claude update` nur scheitern könnte: Die Zeile sagt `von hier aus nicht aktualisierbar: installed with npm into a folder your user can't write to; run "claude install" once to switch to Claude Code's own installer, then restart aipager`. Lässt sich nichts aktualisieren, sagt die Nachricht „Alles ist aktuell.“, sagt, dass eine Prüfung fehlgeschlagen ist, oder, wenn es etwas Neueres gibt, das sich von hier aus nicht installieren lässt, „Es gibt ein Update, aber es lässt sich von hier aus nicht installieren.“, mit einem Button **Erneut prüfen** und ohne Button zum Aktualisieren.

Der Button zum Aktualisieren startet nur, was die Prüfung angeboten hat. Ist die Prüfung älter als 10 Minuten, lief seitdem ein anderes Update, oder passen die Versionen nicht mehr zum Button, bittet er dich stattdessen, noch einmal zu prüfen. Buttons aus einem älteren Menü von `/update` (die einzelnen **Claude Code aktualisieren** / **aipager aktualisieren** / **Beide**) antworten „Dieses Menü ist veraltet: Sende /update noch einmal“ und tun nichts. Jedes Tippen prüft die Admin-Regel erneut.

Claude Code zu aktualisieren führt `claude update` aus (mit absolutem Pfad, Zeitlimit 5 Min.) und meldet `Claude Code A → B`, „schon aktuell“ oder den Fehler mit dem Ende seiner Ausgabe. Laufende Sitzungen behalten die alte Version, bis du sie neu startest (`/restart`); die Antwort listet sie auf. Keine Sitzung wird für dich neu gestartet. Neue Sitzungen nutzen die neue Version.

aipager aktualisieren (**aipager aktualisieren**, oder die zweite Hälfte von **Beide aktualisieren**, das zuerst Claude Code aktualisiert):

1. Bei einer Installation von PyPI, die schon aktuell ist, sagt es das und hört auf.
2. Kann aipager sich selbst neu starten, **wartet es zuerst, bis nichts mehr läuft**: keine Sitzung, die eine Aufgabe ausführt, auf eine Frage wartet, einen Hintergrund-Agenten laufen lässt, eine Live-Arbeitskarte zeigt, ein Tool ausführt oder eine offene Berechtigungsanfrage hat, und keine zurückgehaltenen Antworten, die auf das Ende eines Ratenlimits warten. Die Nachricht listet auf, worauf es wartet, mit **Jetzt neu starten** (das Warten überspringen) und **Abbrechen**. Nach 10 Minuten fragt es noch einmal: **Noch 10 Min. warten**, **Jetzt neu starten**, **Abbrechen**. Eine Stunde ohne Antwort, und es bricht von selbst ab.
3. Es aktualisiert über den Installer, dem das laufende aipager gehört (`pipx upgrade aipager`, `uv tool upgrade aipager`, `brew upgrade aipager` oder `<venv>/bin/python -m pip install --upgrade aipager`), mit absolutem Pfad und einem Zeitlimit von 10 Min. Während der Installer läuft, gibt es kein Abbrechen.
4. Es prüft, ob die neue Version in einem frischen Interpreter lädt. Ein fehlgeschlagenes, zu langsames oder nicht ladbares Update startet nichts neu. Ein Update mit Zeitüberschreitung wurde mittendrin gestoppt, die Nachricht warnt also, dass die Installation unvollständig sein kann, und gibt den Befehl zur Neuinstallation an, der vor dem nächsten Neustart auszuführen ist. Die Ausgabe des Installers wird nur in einem privaten Chat gezeigt; eine Gruppe bekommt „(Ausgabe im Log von aipager)“.
5. Hat während des Updates eine Aufgabe begonnen, wartet es erneut.
6. Es plant ein abgekoppeltes `systemctl --user restart aipager.service` 5 s später (mit einem Timer, der auf 1 s genau ist), und die Statusnachricht sagt `⏳ aipager A → B installiert, startet neu…`. Während Schritt 5 wartet, sagt sie `⏳ aipager A → B installiert; Neustart, sobald die aktuelle Aufgabe fertig ist`. Nach dem Neustart macht das neue aipager aus genau dieser Nachricht `✅ aipager aktualisiert A → B, N Sitzungen wieder verbunden` (und `⚠️ Nicht zurückgekommen: …` für jede Sitzung, die nicht zurückkam). Wurde die Nachricht gelöscht oder lässt sie sich nicht mehr bearbeiten, schickt es das Ergebnis als neue Nachricht an den Chat, der gefragt hat.

In der Mini App zeigt **Einstellungen → Updates** „aipager wird neu gestartet…“ mit einer drehenden Laterne, bis das neue aipager antwortet, dann „Aktualisiert auf B, N Sitzungen wieder verbunden.“. Einen Countdown zeigt sie nie. Nach etwa 2 Minuten ohne Antwort (nie bevor das alte aipager einen Neustart hätte melden können, der nicht stattfand) sagt sie „Startet noch neu, öffne die App gleich noch einmal.“. Mit dem verwalteten Tunnel von aipager behält der Neustart eines Updates die Adresse der App (das neue aipager übernimmt den Tunnel), also kommt die offene Seite durch und zeigt „Aktualisiert auf B“. Ließ sich der Tunnel doch nicht behalten, sagt die Seite nach etwa 2 Minuten „Startet noch neu, öffne die App gleich noch einmal.“. Ein Update, das ein aipager ohne diese Funktion gestartet hat (oder mit `MINIAPP_KEEP_TUNNEL=0`), ändert die Adresse weiterhin: Sobald das alte aipager nicht mehr antwortet, sagt die offene Seite „aipager wurde neu gestartet. Öffne die App neu, sobald der Chat meldet, dass das Update fertig ist“; schließ sie und öffne sie wieder über den Menü-Button des Chats oder `/app`. Die Anmeldung der App gilt 5 Minuten, eine Seite, die länger offen ist, sagt also stattdessen „Öffne die App neu, um das Ergebnis des Updates zu sehen.“.

aipager startet sich nach dem Update nur selbst neu, wenn es als systemd-Benutzerdienst läuft **und** dieser Dienst `KillMode=process` (oder `none`) hat. Mit einem unsicheren `KillMode` wird aipager trotzdem aktualisiert, und die Nachricht sagt `Neustart nötig: …`: zuerst `aipager service install` ausführen (es listet die Sitzungen auf, die ein Neustart beenden würde).

**Jetzt neu starten, ohne Dienst.** Ein aipager, das kein Dienstmanager neu startet (`aipager start` in einem Terminal oder tmux, Docker oder der launchd-Agent auf macOS), installiert das Update und zeigt dann `Tippe auf „Jetzt neu starten“, um sie zu starten (aipager wartet, bis keine Aufgabe mehr läuft).` mit einem Button **🔄 Jetzt neu starten**. Antippen (nur für Admins, wie jeder Update-Button):

1. prüft, ob sich das installierte aipager laden lässt (`⏳ Installiertes aipager wird geprüft…`); eine kaputte Installation startet nichts neu, und dieselbe Version sagt, dass es nichts neu zu starten gibt;
2. wartet wie der systemd-Neustart, bis nichts mehr in Arbeit ist, mit **Jetzt neu starten** (Warten überspringen) und **Abbrechen**;
3. prüft die Installation noch einmal, sagt dann `⏳ aipager A → B installiert, startet neu…` und beendet aipager auf saubere Art (wie Ctrl+C);
4. startet das neue aipager an Ort und Stelle: derselbe Prozess, derselbe Befehl, dieselbe Umgebung und derselbe Ordner, im selben Terminal- oder tmux-Fenster (und in Docker derselbe Prozess des Containers). Sitzungen laufen weiter. Das neue aipager ändert die Nachricht in `✅ aipager aktualisiert A → B, N Sitzungen wieder verbunden`.

Ein Tippen während der paar Sekunden des Neustarts geht verloren, wie bei jedem Neustart. Ein Ctrl+C oder `docker stop` während des Neustarts gewinnt: aipager stoppt und startet nicht wieder. Lässt sich das neue aipager nicht an Ort und Stelle starten, startet eine abgekoppelte Kopie es, sobald das alte weg ist (ihre Ausgabe geht in das Log des Selbstneustarts, `aipager.log` im Ordner der Instanz oder `/tmp/aipager.log`, und das Log von aipager nennt den Befehl, den du ausführen musst, falls diese Kopie nicht zurückkommt); scheitert auch das, oder ist der Interpreter selbst weg, beendet sich aipager mit Code 1, und das Log von aipager sagt `restart.failed:` mit dem Befehl, den du ausführen musst.

Kann sich aipager überhaupt nicht selbst neu starten, nennt die Nachricht statt eines Buttons die genauen Schritte für dein Setup:

- ein Terminal oder tmux: `Drücke im Terminal- oder tmux-Fenster, in dem aipager läuft, Ctrl+C und führe dann aipager start aus.`
- macOS: `Führe auf dem Mac launchctl kickstart -k gui/<uid>/com.aipager.daemon aus.`
- Docker: `Führe auf dem Computer, auf dem Docker läuft, docker restart <container> aus.` (die kurze ID des Containers, wenn sein Hostname eine ist), mit dem Hinweis, dass ein neu erstellter Container aus seinem Image startet und damit die aipager-Version aus diesem Image zurückbringt.

**Jetzt neu starten** erscheint auch im Ergebnis von **Nach Updates suchen**, wenn ein neueres aipager installiert ist und wartet, und in einem einmaligen Hinweis: Laufen die Hooks von Claude Code (die bei jedem Ereignis frisch starten, aus dem installierten aipager) schon mit einem neueren aipager als das laufende aipager, bekommt der Besitzer pro neuerer Version und Laufzeit von aipager eine stille Nachricht, `⚠️ Das aipager-Update ist erst halb fertig: …`, mit dem Button. Sie wartet zwei Minuten ab dem Moment, in dem die Hooks die neuere Version zum ersten Mal ausgeführt haben (ein Neustart direkt nach einer Installation zeigt sie nie), dann, bis im privaten Chat des Besitzers keine Aufgabe mehr läuft. Nach dem Neustart in diese Version wird der Hinweis zu `✅ Das aipager-Update ist fertig: aipager läuft jetzt mit B.`, ohne den Button. In der Mini App zeigt **Einstellungen → Updates** denselben Stand als Zeile über der Karte, mit **Jetzt neu starten** (fragt vorher nach).

In der Mini App ist **Updates** die erste Karte im Tab Einstellungen: ihr Name, die laufende Version und ein kleines **Nach Updates suchen** am Ende der Zeile. Das Öffnen des Tabs Einstellungen prüft von selbst (höchstens alle zehn Minuten, nie während ein Update läuft oder aipager neu startet; eine Prüfung, die dort scheitert, sagt nichts), und ein Tippen prüft immer. Die Karte bleibt eine ruhige Zeile, bis es etwas zu tun gibt: Eine neuere Version bringt ihre Zeilen und den einen Update-Button, „Alles ist aktuell.“ ist eine einzelne Zeile ohne Button, und ein laufendes Update, ein Neustart oder **Jetzt neu starten** behalten ihre eigenen Buttons.

Es läuft immer nur ein Update gleichzeitig, über `/update`, die Karte **Einstellungen → Updates** der Mini App (dieselbe Prüfung, derselbe einzelne Button zum Aktualisieren, derselbe Auftrag) und `aipager update` auf der Befehlszeile hinweg. Das schließt die Sekunden zwischen „installiert, startet neu…“ und dem Neustart selbst ein: `/update` antwortet, dass aipager gleich neu startet, die Mini App bietet keine Buttons an, und **aipager neu starten** der Sprachfunktion verweigert, solange ein Update läuft oder auf den Neustart wartet. Lebt aipager zwei Minuten nach dem Planen seines Neustarts noch, stoppt es den geplanten Timer für den Neustart, gibt die Update-Sperre frei und sagt dir, es selbst neu zu starten.

Fährt aipager herunter, während ein Installer läuft, bekommt der Installer 3 s zum Fertigwerden und wird dann gestoppt (sonst würde er weiter an der Installation schreiben, während das nächste aipager startet). Die Statusnachricht und nach dem Neustart eine neue Nachricht sagen, dass die Installation unvollständig sein kann und wie man sie repariert.

Hat ein Herunterfahren begonnen, wird nichts neu gestartet und nichts Neues begonnen: Buttons von `/update` antworten, dass aipager herunterfährt, die Mini App antwortet mit 503, und kein Installer und keine Versionsprüfung wird gestartet. Ein Installer, der innerhalb seiner 3 s fertig wird, zählt trotzdem. Die Statusnachricht sagt, dass die neue Version installiert ist und nichts neu gestartet wurde, und der nächste Start meldet `aipager aktualisiert A → B`. Ein aipager, das du mit `aipager service stop` gestoppt hast, bleibt gestoppt. Der Anteil des Updates am Herunterfahren dauert insgesamt höchstens 8 s.

## <a id="free-messages"></a>Freie Nachrichten

### <a id="text"></a>Text

Wird als nächste Nachricht für die **aktive Sitzung** behandelt: das Ziel dieses Chats, die Sitzung, zu der du zuletzt gewechselt hast, der du eine Nachricht geschickt hast, die du gestartet hast oder die dir hier zuletzt geantwortet hat (siehe [die angeheftete Statusleiste](#the-pinned-status-bar)). In einer Gruppe hat jede Person ihr eigenes Ziel, das die Antwort einer Sitzung nicht verschiebt (siehe [Teambetrieb](groups.md#talking-to-a-session-in-a-group)). Nachrichten erreichen Claude **sofort**, auch während deine eigene Aufgabe läuft, genau wie das Tippen im Terminal (in einer Gruppe wartet die Nachricht einer anderen Person, bis diese Aufgabe endet; siehe unten). Schick mehrere, und sie kommen in die Warteschlange in Claude selbst, das jede an einer natürlichen Grenze abholt:

Die Reaktion an deiner Nachricht folgt ihr, so wie in Claude Code eine wartende Nachricht von grau zu weiß wird:

- 👀: an die Sitzung übergeben (oder zurückgehalten, siehe unten), aber Claude hat sie noch nicht abgeholt. Eine Nachricht, die während einer laufenden Aufgabe geschickt wird, bleibt 👀, solange sie in der Warteschlange von Claude wartet.
- 👍: Claude hat sie abgeholt: Es hat eine Aufgabe damit begonnen, sie in die laufende Aufgabe aufgenommen oder an einen laufenden Hintergrund-Agenten übergeben.
- 🤷: Sie wird nie abgeholt: eine zurückgehaltene Nachricht, die `/stop`, `/clearqueue` oder `/kill` verworfen haben oder die beim Freigeben nicht geschickt werden konnte; eine Nachricht oder ein Befehl, der noch wartete, als `/stop`, `/clearqueue`, `/kill` oder das Ende der Sitzung (nicht `/clear` oder `/resume`) ihn verworfen hat; oder eine Nachricht, die Claude Code abgelehnt hat (siehe unten). Nachrichten, die Claude schon eingereiht hatte, werden nur markiert, solange aipager diese Warteschlange sehen kann (das Live-Lesen der gespeicherten Unterhaltung läuft, und kein Hintergrundauftrag wartet); sonst behalten sie 👀. Escape im Terminal holt die Warteschlange von Claude zurück in sein Eingabefeld, von wo sie erneut geschickt werden kann, eine so verworfene Nachricht behält also ebenfalls 👀.
- 👌: ein Befehl von Claude Code, der gelaufen ist (siehe unten), oder aipager, das ein `/stop` bestätigt.

Eine Reaktion geht immer nur vorwärts, eine Nachricht bekommt also höchstens drei, jede einmal. Eine Reaktion, die in eine Sperre wegen eines Telegram-Ratenlimits fällt, wird übersprungen und nicht später nachgeholt, und ein Aufräumen markiert höchstens die zehn neuesten Nachrichten, die es verwirft.

Befehls-Buttons (`Komprimieren`, `/model …`): Bei bereiter Sitzung angetippt, bekommt der Befehl sofort 👌; Claude Code führt ihn mit Enter aus, und ein lokaler Befehl wie `/model` löst keinen Hook aus, der das später sagen könnte. Während einer laufenden Aufgabe angetippt (nicht `/model`: das wird bis zum Ende der Aufgabe abgelehnt, siehe [das Modell einer laufenden Sitzung wechseln](#switching-a-running-sessions-model)), bleibt er 👀, bis diese Aufgabe endet (normal, als Zwischenstopp eines Hintergrundauftrags oder bei einem API-Fehler), und dann 👌; vorher von `/stop`, `/clearqueue`, `/kill` oder dem Ende der Sitzung verworfen, ist er nie gelaufen: 🤷. Reiht Claude Code ihn stattdessen als Nachricht ein, folgt er dem Weg 👀 → 👍. Eine Sprachnachricht bekommt dieselben Reaktionen wie Text, sobald ihr Text geschickt wurde.

Die Arbeitskarte und die spätere Antwort folgen der Nachricht, die Claude für eine Aufgabe tatsächlich abgeholt hat (die, mit der es begonnen hat, wenn die Sitzung bereit war, oder die, die es in die laufende Aufgabe aufgenommen hat), nie einfach der letzten Nachricht, die du geschickt hast. Eine weitere Nachricht mitten in einer Aufgabe „zieht die Antwort“ nicht zu sich: Nimmt Claude sie in die Antwort auf, die gerade entsteht, springt die Karte in dem Moment zu ihr; macht Claude zuerst fertig und holt sie danach ab, bleibt die erste Antwort unter der ersten Nachricht, und die weitere bekommt eine eigene Aufgabe, mit eigener Karte und Antwort darunter.

Lehnt Claude Code eine Nachricht rundweg ab (ein unbekannter Slash-Befehl oder ein eingebauter, der im Terminal nur einen Dialog öffnet), wird kein Hook ausgelöst, nichts würde also die Aufgabe beenden, die aipager gerade angekündigt hat. Nach 8 s ohne jeden Hook (`PROMPT_HOOK_GRACE_SECONDS`) wird die Arbeitskarte zu `⚠️ name · Claude Code hat die Nachricht nicht angenommen` mit einer einzeiligen Erklärung, und die Sitzung ist wieder bereit; der Grund steht im Terminal, und die Nachricht bekommt 🤷 (ein Slash-Befehl bekommt stattdessen 👌: Ein eingebauter Befehl, der einen Dialog öffnet, löst auch keinen Hook aus). Nur eine Nachricht, die eine Aufgabe begonnen hat, wird so beurteilt; eine, die hinter einer laufenden Aufgabe wartet, behält ihr 👀, bis Claude sie abholt oder sie verworfen wird.

Drei Fälle werden zurückgehalten statt geschickt und automatisch zugestellt, wenn sie erledigt sind:

- Eine Berechtigungsanfrage oder eine Frage ist offen, und zwar so lange, wie sie offen bleibt (es gibt kein Zeitlimit): Dein Text würde sonst als Antwort auf diesen Dialog gelesen. Deine erste Nachricht bekommt dann eine Antwort unter der Anfrage, dass Claude noch auf deine Antwort wartet (spätere bekommen nur das 👀). Sobald du antwortest, in Telegram oder im Terminal, gehen die zurückgehaltenen Nachrichten in der Reihenfolge, in der du sie geschickt hast, an Claude, und diese Antwort wird gelöscht. Endet die Sitzung, während die Anfrage noch offen ist, werden sie mit einem 🤷 verworfen.
- (Teambetrieb) Die Nachricht einer anderen Person wartet noch auf das Abholen.
- (Teambetrieb) Die Aufgabe einer anderen Person läuft, auch ein Hintergrund-Agent, den sie gestartet hat. Eine im Terminal getippte Aufgabe zählt als die des Besitzers, es tritt ihr also nur die Nachricht eines Besitzers (oder, in einem privaten Chat, die seines eigenen Mitglieds) bei.

Die letzten beiden halten Nachrichten verschiedener Personen aus einer gemeinsamen Aufgabe heraus, damit jede mit den Rechten ihres eigenen Absenders läuft. Tritt doch eine bei (zum Beispiel von einem Absender, den aipager nicht kennt und der nie zurückgehalten wird), läuft die Aufgabe mit den strengsten Regeln aller Beteiligten (siehe [Teambetrieb](groups.md#who-a-message-runs-as)). Wird die Aufgabe, auf die eine Nachricht wartet, gestoppt oder von einem blockierten Tool angehalten, werden die zurückgehaltenen Nachrichten mit einem 🤷 verworfen.

Zurückgehaltene Nachrichten sind auf 50 pro Sitzung begrenzt und laufen nach 24 Std. ab; `/clearqueue` verwirft sie zusammen mit allem, was Claude hält.

#### <a id="send-a-queued-message-now"></a>Eine wartende Nachricht sofort senden

Eine Nachricht, die du schickst, während Claude an deiner eigenen Aufgabe arbeitet, geht sofort an Claude (aipager hält sie nicht zurück) und wartet in der Warteschlange von Claude, bis der aktuelle Schritt endet. Sobald die Warteschlange von Claude sie zeigt, antwortet aipager darunter:

```
⏳ In der Warteschlange: Claude liest sie nach dem aktuellen Schritt
[⚡ Jetzt senden]
```

Jede wartende Nachricht bekommt ihre eigene Zeile, und jede Zeile verschwindet mit ihrem 👍 in dem Moment, in dem Claude diese Nachricht abholt. Eine Nachricht, die Claude abholt, bevor ihr Eintrag in der Warteschlange gesehen wird, bekommt keine Zeile, nur 👀 und dann 👍.

Ein Tippen auf `⚡ Jetzt senden` (oder `/now` zu senden) drückt in dieser Sitzung die eigenen Sofort-senden-Tasten von Claude Code (Ctrl+X, dann Ctrl+S). Claude liest dann alles, was es in der Warteschlange hat, auf einmal, nicht nur die Nachricht, auf die du getippt hast. Was mit der laufenden Arbeit passiert, entscheidet Claude Code: Ein laufender Befehl oder Agent läuft im Hintergrund weiter; schreibt Claude gerade eine Antwort, wird sie abgebrochen und mit deiner Nachricht neu begonnen. Jede andere Art von Schritt (ein anderes Tool zum Beispiel) kann nach einer kurzen Frist ebenfalls unterbrochen werden. In manchen Fällen (zum Beispiel, wenn die Hintergrundaufgaben von Claude Code abgeschaltet sind) wird Sofort-senden zu einer einfachen Unterbrechung des laufenden Schritts. All das ist das eigene Verhalten von Sofort-senden in Claude Code, nichts, was aipager entscheidet.

Das Tippen antwortet `Sofort an Claude gesendet`, und alle Zeilen verschwinden auf einmal, weil Sofort-senden Claude alles übergibt, was es hält; `/now` entfernt sie genauso, und für die Nachrichten, die Jetzt senden geschickt hat, kommt keine Zeile (eine Nachricht, die du danach schickst, bekommt ihre eigene). Bricht Claude den laufenden Schritt ab, statt ihn in den Hintergrund zu schieben, holt es sofort nur die älteste wartende Nachricht ab und liest den Rest beim nächsten Schritt; die Antwort kommt unter die Nachricht, die es abgeholt hat, und diese Nachricht läuft nicht zweimal. Brauchen die Tasten länger als einen Moment bis zu Claude (ein ausgelasteter Computer), antwortet das Tippen stattdessen `Wird sofort an Claude gesendet`; die Zeile verschwindet dann, sobald sie angekommen sind, oder, wenn sie nicht zugestellt werden konnten, bleibt sie und sagt `⏳ In der Warteschlange: Claude war nicht erreichbar, tippe auf „Jetzt senden“, um es noch einmal zu versuchen`, mit ihrem Button, damit du noch einmal tippen kannst. Die Tasten werden nur gedrückt, wenn die gespeicherte Unterhaltung von Claude zeigt, dass es die Nachricht noch hält; sonst antwortet das Tippen `Claude hat sie schon abgeholt` und entfernt die Zeile. Abgelehnt wird es, ohne dass etwas gedrückt wird, solange eine Berechtigungsanfrage oder eine Frage offen ist (`Beantworte zuerst die offene Frage`), solange Claude vielleicht gerade eine geöffnet hat, die aipager noch nicht gezeigt hat (die Ereignisse einer neuen Aufgabe werden zurückgehalten, während die Antwort der vorigen Aufgabe noch zugestellt wird: `Beschäftigt, versuch es gleich noch einmal`), und solange aipager gerade eine andere Nachricht in die Sitzung tippt (`aipager tippt gerade eine Nachricht ein, versuch es gleich noch einmal`). Alle, die der Sitzung Nachrichten schicken dürfen, können den Button und `/now` nutzen; ein Mitglied mit `read_only` nicht.

Die Zeile verschwindet von selbst, wenn ihre Nachricht die Warteschlange von Claude verlässt: Claude holt sie ab (in die laufende Aufgabe aufgenommen, an einen Hintergrund-Agenten übergeben oder als nächste Aufgabe begonnen), Escape im Terminal holt die Warteschlange zurück ins Eingabefeld, oder sie wird durch `/stop`, `/clearqueue`, `/kill`, `/restart`, einen Sicherheitsstopp oder das Ende der Sitzung verworfen. Ein Neustart von aipager entfernt jede Zeile, die noch zu sehen ist.

Die Zeilen warten nie auf die Steuerung der Limits: Sie gehen raus und werden sofort entfernt, auch in einem ausgelasteten oder langsamen Chat und im Minimalmodus. Nur solange Telegram selbst den Chat stummgeschaltet hat, wird keine Zeile geschickt (`/now` funktioniert dann weiterhin), und hat Telegram aipager gerade gebeten, langsamer zu machen, wartet eine Zeile die paar Sekunden, um die es gebeten hat. Jede wartende Nachricht kostet für ihre Zeile einen Aufruf an den Chat; Zeilen, die zusammen gehen, werden mit einem Aufruf entfernt.

### <a id="files"></a>Dateien

Hochgeladene Dateien werden in einen privaten Ordner des Chats heruntergeladen, aus dem sie kamen, `/tmp/aipager-files/<chat>/` (mit dem Namen `<time>_<file name>`; `<chat>` ist ein Name, der aus der Chat-ID und dem Token des Bots gebildet wird und die Chat-ID nicht verrät). Die Ordner kann nur der Benutzer lesen, unter dem aipager läuft (`0700`, Dateien `0600`), und die Aufgabe eines Mitglieds in einem Chat kann die Uploads eines anderen Chats weder lesen noch durchsuchen noch beschreiben (siehe [Sicherheit](security.md#team-mode-enforcement)). Dateien von vor dieser Änderung bleiben, wo sie waren, direkt in `/tmp/aipager-files/`; nichts wird verschoben oder gelöscht, und aipager schränkt den Ordner beim Start auf `0700` ein, sodass nur dieser Benutzer sie erreichen kann. Ein neuer Token des Bots gibt jedem Chat einen neuen Ordner; Uploads von davor bleiben in den alten, die die Aufgabe eines Mitglieds nicht mehr lesen kann. Ein Download-Ordner, der ein symbolischer Link ist oder einem anderen Benutzer gehört, wird abgelehnt, und der Upload schlägt fehl. Der Pfad wird Claude angeboten: Mit einer Bildunterschrift ist die Nachricht die Bildunterschrift gefolgt vom Pfad (oder den Pfaden); ohne ist es nur `check this: <path>` (oder `check these: <paths>` bei einem Album), Claude wird also auf die Datei hingewiesen, ohne gesagt zu bekommen, was es damit tun soll. Eine Bildunterschrift, die mit `/<label>` beginnt, wählt die Sitzung, genau wie `/<label> <prompt>` bei Text, und der Name fällt aus der Nachricht weg: `/api` allein schickt `check this: <path>` an `api`, und `/api compare these` schickt dorthin `compare these <paths>`. Nur ein `/<label>` am Anfang wählt das Ziel; ein Schrägstrich weiter hinten in der Bildunterschrift ist nur Text. Ein Name, auf den keine Sitzung hört, wird mit `⚠️ Keine Sitzung mit diesem Namen` abgelehnt, und es wird nichts geschickt. Das Limit von 20 MB für Downloads von Bots in Telegram wird vorab angewendet; zu große Dateien bekommen eine klare Ablehnung, bevor ein Download versucht wird. Ein Download, der auf einen vorübergehenden Netzwerkfehler trifft, wird insgesamt bis zu dreimal versucht (mit 1 s, dann 2 s Wartezeit), bevor du einen Fehler siehst, und dieser Fehler nennt die Datei. Ein Album (mehrere Fotos oder Dokumente als eine Nachricht geschickt) wird Claude als eine einzige Nachricht übergeben (die Bildunterschrift, dann jeder Dateipfad der Reihe nach), sobald sein letztes Element angekommen ist; lässt sich ein Element nicht herunterladen, geht der Rest trotzdem raus, mit einem Hinweis, der das fehlende nennt.

### <a id="files-claude-sends"></a>Dateien, die Claude schickt

Claude kann dir Dateien aus seiner Sitzung schicken: Bitte es in deinen eigenen Worten darum („schick mir den Screenshot“, „schick mir das Bild unter ~/proj/chart.png“), oder es schickt eine, wenn es meint, dass du sie willst. Es nutzt dafür das Tool SendUserFile von Claude Code (Claude Code 2.1.301 oder neuer), das jede Sitzung bekommt, die aipager startet: aipager startet sie mit `CLAUDE_CODE_ATTACHMENTS_BY_PATH=1` und sagt Claude, dass es das Tool gibt. Eine Sitzung, die gestartet wurde, bevor dein aipager das konnte, bekommt das Tool erst nach einem Neustart: `/restart`, ein Moduswechsel, der sie neu startet, oder Fortsetzen.

- Die Dateien kommen nach der Antwort der Aufgabe, als Antwort darauf (auf deine Nachricht, wenn die Aufgabe keine Antwort hat), ohne Benachrichtigungston und nur im eigenen Chat der Sitzung. Solange die Aufgabe läuft, zeigt die Arbeitskarte `📎 2 Dateien werden gesendet`.
- Jede Datei geht nach ihrem Inhalt, nicht nach ihrem Namen: ein Bild als Foto, wenn es in Telegrams Grenzen für Fotos passt (höchstens 10 MB, Breite plus Höhe höchstens 10.000, die lange Seite höchstens 20-mal die kurze), sonst als Datei; ein GIF als Animation; ein MP4 als Video; ein MP3 oder M4A als Audio; alles andere als Datei. Bittet Claude um einen einfachen Anhang, geht alles als Datei.
- 2 bis 10 Fotos und Videos gehen als ein Album; Dateien gehen mit Dateien und Audio mit Audio; eine Animation geht allein. Mehr werden gleichmäßig aufgeteilt (11 gehen als 6 und 5), und kein Album ist größer als 50 MB.
- Die Bildunterschrift ist `💬 <session>`, mit Claudes eigener Bildunterschrift in der nächsten Zeile der ersten Nachricht, höchstens 1.024 Zeichen.
- Jede Datei wird zuerst geprüft, für alle, auch für den Owner ([Sicherheit](security.md#files-claude-sends)): Die Einstellungen von aipager, die Zugangsdaten von Claude, Schlüssel und Dateien, die wie Geheimnisse aussehen, werden nie geschickt; eine Datei muss eine normale Datei sein, nicht leer und höchstens 50 MB groß; ein Mitglied schickt nur Dateien im [Stammordner](groups.md#root-folders) des Chats. Wird eine Datei einer Anfrage abgelehnt, wird nichts von dieser Anfrage geschickt: Claude erfährt den Grund und sagt ihn dir, und die Aufgabe läuft weiter.
- aipager schickt eine private Kopie, die es gemacht hat, als Claude Code die Datei gelesen hat. Sie liegt in `~/.local/state/aipager/outbox/` und wird gelöscht, sobald sie geschickt ist, oder nach einem Tag.
- aipager setzt keine eigene Grenze für die Anzahl: Gesendet wird im Takt von Telegrams Limits, ein Album zählt eine pro Datei. Solange der Chat stummgeschaltet ist, warten die Dateien und kommen nach der Antwort.
- Eine Datei, die sich nicht schicken lässt, bekommt eine Zeile im Chat, zum Beispiel `⚠️ report.pdf konnte nicht gesendet werden: Telegram hat die Datei abgelehnt.` Eine Datei, deren Anfrage aipager nicht in Claudes eigenem Gesprächsverlauf findet, bekommt keine Zeile ([Sicherheit](security.md#files-claude-sends)). Eine Datei, deren Anfrage dort steht, die aipager aber nicht der laufenden Aufgabe zuordnen kann, bekommt `⚠️ report.pdf konnte nicht gesendet werden: aipager konnte nicht bestätigen, dass Claude die Datei in diesem Durchlauf gesendet hat.` Dateien kommen auch aus einer Nachricht, die du schickst, während Claude arbeitet, und die es als Nächstes übernimmt, und aus einer Aufgabe, die ein Stop-Hook weiterlaufen lässt (`/goal`). Jede geschickte, abgelehnte oder fehlgeschlagene Datei hat einen eigenen Eintrag im [Audit-Log](security.md#audit-log).

### <a id="voice"></a>Sprache

Sprachnachrichten laufen über `faster-whisper` (das Paket `aipager[voice]`). Das Audio wird auf diesem Computer in Text umgewandelt, als `🎙️ Gehört: …` zurückgegeben und eingetippt, als hättest du es geschrieben, auch als Name für `/new` oder für ein ausstehendes Umbenennen. Eine Sprachnachricht über dem Download-Limit von 20 MB für Bots in Telegram wird abgelehnt. Siehe [Hooks → UserPromptSubmit](hooks.md#userpromptsubmit) für das, was danach passiert.

---

## <a id="command-line-every-command"></a>Befehlszeile: alle Befehle

Führ `aipager <command> --help` aus, um dieselbe Liste auf deinem Computer zu sehen.

| Befehl | Was er tut |
|---|---|
| `aipager config` | Der interaktive Einrichtungsassistent: Token des Bots, Chats, Mitglieder und Rollen, die Hooks von Claude Code. Führ ihn noch einmal aus, um irgendetwas zu ändern (siehe [Teambetrieb](groups.md#setup)). |
| `aipager setup` | Dieselbe erste Einrichtung ohne Rückfragen, für Skripte und Coding-Agenten (siehe [unten](#command-line-aipager-setup-for-coding-agents)). |
| `aipager start` | Lässt aipager im Vordergrund laufen. Es verweigert den Start, solange ein anderes aipager läuft. |
| `aipager service install [--yes]` | Installiert aipager als Hintergrunddienst (systemd-Benutzerdienst auf Linux, launchd-Agent auf macOS) und startet ihn. `--yes` überspringt die Frage, wenn sich der installierte Dienst unterscheidet. |
| `aipager service start\|stop\|status\|logs\|uninstall` | Startet, stoppt oder zeigt den Dienst, folgt seinem Log oder entfernt ihn. |
| `aipager status [--json]` | aipager und jede Sitzung auf einen Blick: Zustand, Modell, Kontext, Kosten und der Stand der Telegram-Limits. |
| `aipager logs [-f] [-n LINES]` | Das Log von aipager (journald auf Linux, das launchd-Log auf macOS): die letzten 100 Zeilen, oder `-n` Zeilen; `-f` folgt ihm. |
| `aipager doctor [--fix] [--safety-check] [--json]` | Gesundheitsprüfungen (siehe [Fehlerbehebung](troubleshooting.md#aipager-doctor-check-list)). `--fix` bietet an, die Claude-Anmeldedaten und eine mehrdeutige Installation von `claude` zu reparieren, und fragt vorher; `--safety-check` gibt die geltenden Sicherheitsregeln aus; `--json` ist für Skripte (siehe [unten](#aipager-doctor---json)). |
| `aipager session <name> [claude args]` | Startet eine Claude-Code-Sitzung namens `<name>` unter dtach im aktuellen Ordner oder verbindet sich mit ihr, wenn sie läuft. Argumente nach dem Namen (oder nach `--`) gehen unverändert an `claude`, zum Beispiel `--resume`. Braucht ein laufendes aipager. |
| `aipager session ls [-a] [--json]` | Listet die Sitzungen auf; `-a` schließt beendete ein. Auch `aipager session list`. |
| `aipager session kill <name> [-y]` | Beendet eine Sitzung, nach einer Rückfrage, außer mit `-y`. |
| `aipager resume [name]` | Holt eine beendete Sitzung mit ihrer Unterhaltung zurück; ohne Namen eine Auswahl. |
| `aipager miniapp enable [--port PORT] [--url URL]` | Schaltet die Mini App an, auf `127.0.0.1:PORT` (Standard 8765), hinter dem verwalteten Tunnel oder deiner eigenen `https://`-URL. Wirkt beim nächsten Neustart von aipager. |
| `aipager miniapp disable\|status` | Schaltet sie ab (beim nächsten Neustart) oder zeigt ihre Einstellungen und Adresse. |
| `aipager policy validate` | Prüft `policy.yaml` (und `policy.d`); endet bei einem Problem mit einem Code ungleich null. |
| `aipager update` | Aktualisiert aipager über den Installer, dem es gehört (uv, pipx, Homebrew, pip). Es startet aipager nie neu; starte es selbst neu, oder nimm `/update` in Telegram, das das kann (siehe [Update](#update)). |
| `aipager report [--note TEXT]` | Zeigt einen Problembericht und schickt ihn, wenn du ja sagst (siehe [unten](#command-line-aipager-report)). |
| `aipager uninstall [-y]` | Stoppt und entfernt aipager und seine Konfiguration und behält seine Daten (siehe [unten](#command-line-aipager-uninstall)). |
| `aipager version`, `aipager --version` | Gibt die Version aus. |
| `aipager help [command]` | Hilfe zu aipager oder einem Befehl. |

## <a id="command-line-aipager-setup-for-coding-agents"></a>Befehlszeile: `aipager setup` (für Coding-Agenten)

Die Abschnitte oben beschreiben den Telegram-Bot. Dieser beschreibt einen Befehl, den du in einem Terminal ausführst oder den ein Skript oder dein Coding-Agent für dich ausführt: `aipager setup` richtet aipager ohne Rückfragen ein und ergibt dasselbe wie der erste Durchlauf von `aipager config`.

```sh
# 1. die Telegram-ID der Person herausfinden (sie öffnet den Bot und tippt auf Start)
aipager setup detect-chat --token-file ~/bot-token.txt --json
# 2. sobald sie bestätigt hat, dass sie es ist, aipager einrichten
aipager setup --token-file ~/bot-token.txt --chat-id 123456789 --service --json
# 3. die Installation prüfen
aipager doctor --json
```

Was er tut, in dieser Reihenfolge: Er liest den Token des Bots, prüft ihn bei Telegram (getMe), prüft alles auf diesem Computer, ohne zu schreiben (die bestehende Konfiguration, die Abhängigkeiten, `~/.claude/settings.json`, ob ein aipager läuft), schickt der Person eine Testnachricht ("aipager is set up for you. ...") und schreibt erst dann. Geschriebene Dateien, genau so, wie der Assistent sie schreibt:

- `~/.config/aipager/aipager.yaml` (Rechte 0600): `bot_token` und ein privater Chat, dessen einziges Mitglied die Person ist (`label: owner`, Rolle owner oder admin).
- `~/.claude/settings.json`: die Hooks von aipager und die statusLine (die bestehende Datei wird als `settings.json.bak.<time>` gesichert, wenn sie sich ändert).
- Das Audit-Log bekommt einen Eintrag `grant-owner`, wenn jemand Besitzer wird.
- Mit `--service` der Hintergrunddienst (dasselbe wie `aipager service install --yes`).
- Nur bei einer alten Installation mit `config.env` (noch kein `aipager.yaml`) die Übernahme, die der Assistent macht: eine Kopie `config.env.bak.<time>` (und `team.yaml.bak.<time>`, wenn es ein `team.yaml` gibt) und eine Vorlage `~/.config/aipager/policy.yaml`, wenn keine existiert. `config.env` selbst bleibt, wie es ist.

Schlägt eine Prüfung fehl, wird nichts geschickt und nichts geschrieben. Läuft schon ein aipager, wird eine Änderung von Chat oder Rolle im laufenden Betrieb neu geladen; ein neuer Token des Bots braucht einen Neustart (`aipager service stop`, dann `aipager service start`), den setup nie selbst macht, und `--service` wird übersprungen. Kann setup nicht feststellen, ob ein aipager läuft, tut es so, als liefe eins (Warnung `daemon_unknown`): Dann installiert es nie den Dienst, kann also nie ein laufendes aipager neu starten, schickt nach jeder Änderung kein Neuladen und meldet stattdessen `restart_needed: true`.

### <a id="aipager-setup-flags"></a>Optionen von `aipager setup`

| Option | Bedeutung |
|---|---|
| `--token-file PATH` | Liest den Token des Bots aus dieser Datei (höchstens 4096 Bytes; Text um einen Token herum ist in Ordnung). Eine Datei, die andere Benutzer lesen können, funktioniert, gibt aber die Warnung `token_file_shared`: Mach `chmod 600` darauf, oder lösch sie, sobald setup geklappt hat. |
| `--token-stdin` | Liest den Token des Bots von stdin (eine Pipe oder eine umgeleitete Datei, nie ein Terminal). Die Pipe muss sich schließen: setup wartet höchstens 30 Sekunden auf das Ende von stdin und endet dann mit 2 und `token_stdin_timeout`. `printf '%s\n' "$TOKEN" \| aipager setup --token-stdin ...` und `< token.txt` schließen sie beide. |
| `--chat-id N` | Pflicht. Die numerische Telegram-Benutzer-ID der Person, die auch die ID ihres privaten Chats ist. Gruppen werden mit `aipager config` hinzugefügt. |
| `--role owner\|admin` | Die Rolle der Person (Standard `owner`). |
| `--service` | Installiert und startet außerdem den Hintergrunddienst (übersprungen, solange ein aipager läuft). |
| `--force` | Erlaubt, den Token des Bots, den privaten Chat des Besitzers oder die Rolle einer bestehenden Installation zu ersetzen. Gruppen-Chats und andere private Chats bleiben erhalten. |
| `--dry-run` | Führt jede Prüfung aus, schickt nichts, schreibt nichts und meldet, was sich ändern würde. |
| `--json` | Gibt genau ein JSON-Objekt auf stdout aus; alles andere geht auf stderr. |

Gib genau eine der beiden Optionen `--token-file` und `--token-stdin` an. Der Token wird nie als Wert einer Option angenommen: `--token` und `--bot-token` werden abgelehnt (`token_on_command_line`), und der Wert wird nicht gezeigt. Abgekürzte Optionen (`--token-f`, `--dry`) werden nicht angenommen. setup stellt nie eine Frage, braucht nie ein Terminal und liest den Token nie aus einer Umgebungsvariablen.

Ein erneuter Aufruf mit demselben Token, Chat und derselben Rolle ändert nichts und sagt das (`unchanged`, keine Testnachricht). Ein anderer Token, Chat oder eine andere Rolle bei einer bestehenden Installation endet mit 6, bis du `--force` hinzufügst. Eine alte Installation mit `config.env` wird zuerst übernommen, wie es der Assistent tut (sie bekommt die Rolle `admin`, `--role owner` darauf braucht also `--force`).

### <a id="aipager-setup-detect-chat"></a>`aipager setup detect-chat`

```sh
aipager setup detect-chat (--token-file PATH | --token-stdin) [--timeout SECONDS] [--json]
```

Wartet auf die neueste private Nachricht an den Bot (ein Tippen auf „Starten“ schickt eine) und gibt ihren Absender aus: ID, Name und Benutzername. Er speichert nichts, zeig also zuerst die gefundene Person, und führ `aipager setup --chat-id` erst aus, wenn sie bestätigt ist. Es zählen nur Nachrichten, die höchstens 10 Minuten vor dem Start des Befehls geschickt wurden. `--timeout` ist 0 bis 3600 Sekunden (Standard 300; 0 heißt einmal nachsehen). Er liest Telegram, ohne eine Nachricht zu bestätigen, das `/start` bleibt also für den Assistenten und für aipager verfügbar.

Läuft ein aipager für denselben Bot, bekommt aipager die Nachrichten des Bots und notiert keine privaten Nachrichten von Personen, die noch nicht eingerichtet sind. detect-chat endet dann mit 9 (`source: "daemon"`): Stoppe aipager (`aipager service stop`) und führ den Befehl noch einmal aus, oder frag die Person nach ihrer numerischen ID. Bei einem aipager für einen anderen Bot liest er direkt Telegram (`source: "telegram"`).

Grenze: Weil detect-chat nie eine Nachricht bestätigt, zeigt Telegram ihm nur die ältesten 100 ungelesenen Updates der letzten 24 Stunden. Hat der Bot so viele wartend, ist ein neues `/start` nicht zu sehen: detect-chat läuft dann in eine Zeitüberschreitung (Exit 8) mit der Warnung `updates_backlog`, und noch einmal auf „Starten“ zu tippen hilft nicht. Frag die Person stattdessen nach ihrer numerischen Telegram-ID (oder starte aipager einmal, das diese Updates liest und leert).

### <a id="exit-codes"></a>Exit-Codes

| Code | `error` | Bedeutung |
|---|---|---|
| 0 | | Erfolg (`installed`, `updated`, `unchanged`, `dry_run` oder detect-chat `found`). |
| 1 | `config_malformed`, `ambiguous_install`, `settings_invalid`, `test_send_failed`, `write_failed`, `service_failed`, `internal_error` | Fehlschlag. `ambiguous_install`: Mehr als ein privater Chat könnte der des Besitzers sein; beheb das in `aipager config`. |
| 2 | `usage`, `missing_token_source`, `token_source_conflict`, `token_file_unreadable`, `token_stdin_is_tty`, `token_stdin_timeout`, `token_malformed`, `token_on_command_line`, `bad_chat_id`, `bad_role`, `bad_timeout` | Falsche oder fehlende Eingabe; `fix` nennt die Option. Eine unbekannte Option oder ein falsches Wort nach `setup` meldet der Argument-Parser (einfacher Text, kein JSON), ebenfalls mit 2. |
| 3 | `deps_missing` | dtach, claude, aipager-hook oder aipager-statusline fehlt; `fix` enthält die Befehle. Es wurde nichts geschrieben. |
| 4 | `token_rejected` | Telegram hat den Token abgelehnt. |
| 5 | `chat_not_started`, `bot_blocked` | Die Person hat im Bot nicht auf „Starten“ getippt ("Open t.me/&lt;bot&gt; and press Start, then run this again.") oder hat ihn blockiert. Es wurde nichts geschrieben. |
| 6 | `existing_install` | Ein anderer Token, Chat oder eine andere Rolle ist schon eingerichtet; füg `--force` hinzu. |
| 7 | `telegram_unreachable` | Netzwerkfehler, oder Telegram hat mit 429 oder 5xx geantwortet. |
| 8 | `detect_timeout` | detect-chat hat vor `--timeout` keine private Nachricht gesehen (mit der Warnung `updates_backlog`, wenn 100 alte ungelesene Updates neuere verdecken). |
| 9 | `daemon_running` | detect-chat: Ein laufendes aipager bekommt die Nachrichten des Bots. |
| 10 | `updates_conflict` | detect-chat: Ein anderes Programm liest die Nachrichten dieses Bots. |
| 130 | `interrupted` | Ctrl-C. |

Schlagen mehrere Prüfungen gleichzeitig fehl, folgt der Code dieser Reihenfolge: `config_malformed`, `ambiguous_install`, `existing_install`, `settings_invalid`, `deps_missing`. `--dry-run` endet mit dem Code, den der echte Lauf aufgrund dieser Prüfungen hätte.

### <a id="json-aipager-setup---json"></a>JSON: `aipager setup --json`

Jeder Schlüssel ist immer vorhanden; ein noch unbekannter Wert ist `null`.

```json
{
  "command": "setup",
  "status": "installed",
  "ok": true,
  "exit_code": 0,
  "error": null,
  "message": "aipager is set up for @example_bot (chat 123456789, role owner). ...",
  "fix": null,
  "dry_run": false,
  "bot_username": "example_bot",
  "chat_id": 123456789,
  "role": "owner",
  "changed": ["bot_token", "owner_dm", "settings_json"],
  "test_message": "sent",
  "deps": {"ok": true, "items": [{"name": "dtach", "found": true, "path": "/usr/bin/dtach", "fix": null}]},
  "settings_json": {"path": "/home/you/.claude/settings.json", "status": "created", "backup": null, "repointed": 0},
  "daemon": {"running": false, "reload": "not_needed", "restart_needed": false},
  "service": {"requested": false, "result": "not_requested"},
  "warnings": [],
  "next_step": "Start aipager with `aipager service install` ..."
}
```

- `status`: `installed`, `updated`, `unchanged`, `dry_run` oder `error`.
- `changed`: was sich geändert hat (oder bei einem Probelauf ändern würde), in dieser Reihenfolge: `migrated_v1`, `bot_token`, `owner_dm`, `role`, `settings_json`, `service`.
- `test_message`: `sent`, `not_needed`, `skipped_dry_run`, `failed` oder `not_attempted`.
- `settings_json.status`: `created`, `patched`, `unchanged`, `would_change` oder `not_checked`.
- `daemon.reload`: `reloaded`, `refused`, `not_reloaded` oder `not_needed`. `restart_needed` ist true nach einem neuen Token, während ein aipager läuft, und nach jeder Änderung, wenn aipager nicht neu geladen werden konnte oder setup nicht feststellen konnte, ob eins läuft.
- `service.result`: `not_requested`, `installed`, `already_installed`, `skipped_daemon_running`, `would_install`, `failed`, oder `null`, wenn setup vorher aufgehört hat.
- `warnings`: Objekte mit `code` (`token_file_shared`, `reload_refused`, `daemon_unknown`, `audit_write_failed`) und `message`. `audit_write_failed`: Die Rolle Besitzer ist geschrieben, aber ihr Eintrag `grant-owner` konnte nicht zum Audit-Log hinzugefügt werden; dieser Eintrag ist nur nach bestem Bemühen, der Exit-Code bleibt also 0.
- `next_step` nach einer Änderung, während ein aipager läuft, das nicht neu geladen werden konnte, sagt, es neu zu starten (`aipager service stop`, dann `aipager service start`); es nennt den Token des Bots nur, wenn sich der Token geändert hat.
- Mit der Warnung `daemon_unknown` sagt `next_step` nie, dass aipager läuft. Es sagt, dass setup das nicht feststellen konnte, verweist auf `aipager doctor --json` und sagt, aipager mit `aipager service install` oder `aipager start` zu starten, wenn es nicht läuft. Wäre ein Neustart nötig, bittet es nur dann darum, wenn ein aipager läuft, und sagt sonst, es genauso zu starten.
- Bei einem Fehler ist `status` gleich `error`, und `error`, `message` und `fix` sagen, was schiefging und was zu tun ist; `next_step` wiederholt `fix`. Kam der Fehler nach einem Schreiben, listet `changed` auf, was geschrieben wurde, und die normale Ausgabe fügt eine Zeile auf stderr hinzu: `Already written: aipager.yaml, ... (changed: ...)`.

### <a id="json-aipager-setup-detect-chat---json"></a>JSON: `aipager setup detect-chat --json`

```json
{
  "command": "detect-chat",
  "status": "found",
  "ok": true,
  "exit_code": 0,
  "error": null,
  "message": "Found Ada L (@ada), Telegram id 123456789.",
  "fix": null,
  "bot_username": "example_bot",
  "source": "telegram",
  "candidate": {"id": 123456789, "first_name": "Ada", "last_name": "L", "username": "ada", "date": 1700000000},
  "other_candidates": 0,
  "warnings": [],
  "next_step": "Show this person to the user; once they confirm, run: aipager setup --token-file <path> --chat-id 123456789"
}
```

`candidate` ist `null`, außer `status` ist `found`. `warnings` enthält Objekte mit `code` (`token_file_shared`, `updates_backlog`) und `message`. `other_candidates` zählt andere Personen, die dem Bot in denselben 10 Minuten geschrieben haben: Ist es nicht 0, stell sicher, dass du die richtige Person hast. `next_step` wiederholt die Token-Option, die du benutzt hast, nie den Token.

### <a id="aipager-doctor---json"></a>`aipager doctor --json`

Die Prüfungen von `aipager doctor` als ein JSON-Objekt auf stdout. Wie das einfache `aipager doctor` liest es nur und schickt keine Telegram-Nachricht. Exit 1, wenn eine Prüfung fehlschlägt, sonst 0. Es lässt sich nicht mit `--fix` oder `--safety-check` kombinieren (Exit 2 mit einem Fehlerobjekt).

```json
{
  "command": "doctor",
  "version": "1.0.1",
  "ok": true,
  "summary": {"ok": 14, "warn": 1, "fail": 0},
  "checks": [{"key": "token_valid", "status": "ok", "title": "Telegram bot token", "detail": ["@example_bot"], "fix": null}]
}
```

`checks` stehen in dieser Reihenfolge, mit diesen Schlüsseln: `config_parses`, `config`, `token_valid`, `chat_reachable`, `team`, `role_shell_access`, `peer_messages`, `claude`, `claude_auth`, `dtach`, `hook_scripts`, `settings_json`, `daemon`, `service_installed`, `service_unit_path`, `miniapp`. Eine Prüfung, die abstürzt, behält ihren Schlüssel mit dem Status `warn`. Die Texte sind einfach (ohne Auszeichnung), und alles, was wie ein Bot-Token aussieht, wird durch `<redacted>` ersetzt.

## <a id="command-line-aipager-uninstall"></a>Befehlszeile: `aipager uninstall`

`aipager uninstall` stoppt aipager und entfernt seinen Dienst, löscht den Konfigurationsordner (`~/.config/aipager`: der Token des Bots, die Chats und die Einstellungen) und die Sitzungsliste (`~/.claude/aipager-sessions.json`), räumt die Dateien von aipager in `/tmp` auf und deinstalliert aipager selbst. Es fragt vorher (`-y` überspringt die Frage).

Es behält die Daten von aipager, damit eine Neuinstallation dort weitermacht, wo sie aufgehört hat: `~/.local/share/aipager` (Sitzungsordner, heruntergeladene Hilfsprogramme, Sperr- und Markierungsdateien), `~/.local/state/aipager` (die Einträge der Problemberichte und der Stand der Sprachen), `~/.claude/aipager-audit.jsonl` (das Log der Antworten auf Berechtigungen), `~/.claude/aipager-flood-state.json` (der Verlauf der Telegram-Limits), `~/.claude/aipager-pending-users.json` (Personen, die den Bot ohne Zugang probiert haben) und `~/aipager-users` (die [Stammordner](groups.md#root-folders) der Mitglieder, mit ihrer Arbeit). Bevor es fragt, listet es auf, welche davon es auf deinem Computer gibt, und am Ende gibt es den Befehl `rm -rf` aus, der sie ebenfalls entfernt.

Es nimmt außerdem die eigenen Einträge von aipager aus der `~/.claude/settings.json` von Claude Code heraus: die Hooks, die `aipager-hook` ausführen, und die Statuszeile, die `aipager-statusline` ausführt (eine eigene Statuszeile von dir bleibt). Alles andere in dieser Datei bleibt, wie es war, und die Datei, wie sie war, wird daneben als `settings.json.bak.<time>` aufbewahrt. Ist die Datei ein Link auf eine andere Datei, lässt sie sich nicht lesen, ist sie kein gültiges JSON oder hat sie nicht die Form, die Claude Code liest, bleibt sie genau, wie sie ist, und uninstall sagt, welche Einträge von Hand zu entfernen sind. Ein eigener Hook, der noch `aipager-hook` oder `aipager-statusline` erwähnt (zum Beispiel ein Wrapper `aipager-hook-capped.sh`), bleibt ebenfalls, und uninstall nennt ihn. Schon offene Claude-Code-Sitzungen führen die Hooks, die sie geladen haben, weiter aus, bis du sie neu startest. `aipager config` fügt die Einträge nach einer Neuinstallation wieder hinzu. Dein Telegram-Bot und ältere Sicherungen `settings.json.bak.*` werden nicht angerührt, und auch nicht `skipDangerousModePermissionPrompt` (die Einstellung, die die Warnung von Claude Code überspringt, bevor es ohne Berechtigungsprüfungen läuft), die aipager setzt, die du aber vielleicht selbst gesetzt hast.

## <a id="command-line-aipager-report"></a>Befehlszeile: `aipager report`

`aipager report` erstellt einen Problembericht derselben Art wie der Button Problem melden des Bots, aus dem, was aipager auf diesem Computer aufgezeichnet hat (mit weniger Fakten über das laufende aipager: Modus, Chats, Funktionen, Laufzeit, Sitzungen und Details zu Claude Code bleiben unbekannt oder leer), gibt ihn vollständig aus und fragt `Send this report? [y/N]`. Nur `y` oder `yes` schickt ihn; alles andere, oder gar kein Terminal (ein Skript, eine Pipe, die Shell einer Claude-Code-Sitzung), schickt nichts. `--note "text"` fügt eine Notiz von höchstens 500 Zeichen hinzu, die im ausgegebenen Bericht steht. Es endet mit 0, wenn der Bericht gesendet wurde, auf deinen Wunsch nicht gesendet wurde oder nur ausgegeben wurde, und mit 1, wenn ein Senden, das du bestätigt hast, nicht rausging. Siehe [Problemberichte](problem-reports.md) für das, was ein Bericht enthält und wohin er geht.

## <a id="settings-in-the-environment"></a>Einstellungen aus der Umgebung

Ein paar Einstellungen werden aus der Umgebung von aipager gelesen statt aus `aipager.yaml`. Beim systemd-Dienst füg sie mit `systemctl --user edit aipager` unter `[Service]` hinzu (`Environment=STALE_BUSY_TIMEOUT=900`), dann `aipager service stop` und `aipager service start`; beim launchd-Dienst auf macOS füg sie zu `EnvironmentVariables` der plist hinzu (`~/Library/LaunchAgents/com.aipager.daemon.plist`; `aipager service install` schreibt diese Datei neu, füg sie danach also wieder hinzu); bei einem aipager, das du selbst startest, setz sie vor `aipager start`. Keine davon ist für den normalen Gebrauch nötig.

| Einstellung | Standard | Was sie ändert |
|---|---|---|
| `AIPAGER_WHISPER_MODEL` | `base` | Das Sprachmodell für Sprachnachrichten (`tiny`, `base`, `small`, `medium`, ...): größer ist genauer und langsamer. |
| `MINIAPP_PORT` | `8765` | Der Port der Mini App auf `127.0.0.1` (`aipager miniapp enable --port` speichert ihn stattdessen). |
| `MINIAPP_ENABLED` | aus `aipager.yaml` | `0` schaltet die Mini App ab und `1` an, egal was `aipager.yaml` sagt (`aipager miniapp enable\|disable` speichert die Wahl stattdessen). |
| `MINIAPP_PUBLIC_URL` | keine | Stellt die Mini App unter deiner eigenen `https://`-URL bereit statt über den verwalteten Tunnel ([Sicherheitsmodell](security.md#mini-app-tunnel)). |
| `KEEP_FINISHED_CARD` | `1` | `0` macht „Nur die Antwort (ohne Karte)“ zum Layout für Chats, die nie eines in `/settings` gewählt haben. |
| `STALE_BUSY_TIMEOUT` | `7200` (2 Stunden) | Sekunden, die eine arbeitende Sitzung ohne Neuigkeiten sein darf, bevor der Chat den Hinweis „arbeitet noch (120 Min. ohne Neuigkeiten)“ bekommt. |
| `PROMPT_HOOK_GRACE_SECONDS` | `8` | Sekunden, bevor eine Nachricht, die Claude Code nie angenommen hat, als „Claude Code hat die Nachricht nicht angenommen“ markiert wird ([freie Nachrichten](#text)). |
| `FINISH_CARD_GRACE_SECONDS` | `0.8` | Wie lange die fertige Karte beim Layout mit Karte vor der Antwort kommt; `0` schickt beides gleichzeitig. |
| `GONE_SESSION_MAX_AGE_DAYS` | `14` | Tage, die eine beendete Sitzung in `/resume` bleibt; `0` behält sie. |
| `STREAM_EDIT_INTERVAL`, `BUSY_EDIT_INTERVAL` | `1.2`, `3.0` | Die Aktualisierung der Arbeitskarte, während Text streamt und während sie ruhig ist, in Sekunden; das Limit von Telegram pro Chat gewinnt weiterhin ([Fehlerbehebung](troubleshooting.md#busy-cards-got-slower)). |
| `TYPING_INDICATOR_INTERVAL` | `4.5` | Sekunden zwischen den Blasen „schreibt…“; `0` schaltet sie ab. |
| `TELEGRAM_MAX_RETRY_AFTER` | `90` | Die Wartezeit von Telegram in Sekunden, ab der aipager ein Ratenlimit als Sperre behandelt (nicht senken; siehe [Fehlerbehebung](troubleshooting.md#the-bot-went-quiet-flood-control)). |
| `CLAUDE_RICH_SUMMARIES` | `1` | `0` schaltet die reichhaltige Formatierung von Antworten mit Codeblöcken ab (eine Ausweichlösung aus älteren Versionen). |
| `AIPAGER_INTERACTIVE_TIMEOUT` | `300` | Sekunden, die eine Berechtigungsanfrage oder Frage ohne Neuigkeiten von Claude Code warten darf, bevor aipager die gespeicherte Unterhaltung prüft; nur wenn es zeigt, dass die Anfrage vorbei ist, zählt die Sitzung wieder als arbeitend ([Fehlerbehebung](troubleshooting.md#a-permission-prompt-with-no-news-for-5-minutes)). |
| `AIPAGER_SUBAGENT_SILENCE` | `1800` | Sekunden Stille, nach denen ein Subagent ohne gesehenen Stopp entfernt wird ([Hooks](hooks.md#subagentstart--subagentstop)). |
| `AIPAGER_BG_SHELL_MAX_TRACK` | `7200` | Sekunden, nach denen ein Hintergrundbefehl ohne gesehenes Ende nicht mehr gezählt wird. |
| `AIPAGER_REPORT_PROMPTS` | an | `0` schaltet die automatischen Angebote für Problemberichte ab ([Problemberichte](problem-reports.md#when-aipager-offers-a-report)). |
| `AIPAGER_CLAUDE_BIN` | keine | Das `claude`, das laufen soll, wenn mehrere installiert sind (`claude_path` in `aipager.yaml` gewinnt). |
| `AIPAGER_SOCKET_PATH` | `$XDG_RUNTIME_DIR/aipager.sock` | Der Steuer-Socket von aipager. Sitzungen, die aipager startet, geben ihn an ihre Hooks weiter. |
| `AIPAGER_DEBUG` | aus | `1` in der Umgebung einer Sitzung lässt `aipager-hook` und `aipager-statusline` ihre Diagnosen auf stderr ausgeben. |
| `AIPAGER_INSTANCE_DIR`, `AIPAGER_TELEGRAM_API_BASE` | keine | Ein zweites, isoliertes aipager zum Testen ([Architektur](architecture.md#isolated-instance-for-testing)). |

## <a id="see-also"></a>Siehe auch

- [Architektur](architecture.md): wo der Bot hingehört.
- [Hook-Ereignisse](hooks.md): was aipager von claude zurückhört.
- [Fehlerbehebung](troubleshooting.md): wenn sich Befehle seltsam verhalten.
