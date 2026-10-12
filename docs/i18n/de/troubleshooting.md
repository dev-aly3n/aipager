# Fehlerbehebung

<!-- i18n-source: docs/troubleshooting.md @ 7e36944 -->

> Diese Seite ist eine Übersetzung der englischen Seite; wenn die beiden voneinander abweichen, gilt die englische Fassung. ([English](../../troubleshooting.md))

Häufige Fehler und ihre Lösung. Im Zweifel führ aus:

```sh
aipager doctor
```

Es führt alle Prüfungen unten der Reihe nach aus und gibt für jeden Fehlschlag den empfohlenen Lösungshinweis aus. Quelle: `aipager/doctor.py`.

## <a id="another-aipager-daemon-already-owns-the-socket"></a>„Another aipager daemon already owns the socket“

Zwei aipager können sich keinen Steuer-Socket teilen. Dieser Fehler heißt, dass ein aipager noch läuft und darauf antwortet (der Socket ist `$XDG_RUNTIME_DIR/aipager.sock`, oder `/tmp/aipager.sock`, wenn `$XDG_RUNTIME_DIR` nicht gesetzt ist). Eine Socket-Datei, die ein abgestürztes aipager zurückgelassen hat, ist kein Problem: Der nächste Start entfernt sie und bindet einen neuen. Stoppe zuerst das laufende aipager:

```sh
aipager service stop          # wenn es als Dienst läuft
pkill -f 'aipager start'      # sonst
```

Wenn die Socket-Prüfung es übersieht, verweigert `aipager start` trotzdem mit `aipager is already running (pid=N)` und nennt seine Sperrdatei (`~/.local/share/aipager/daemon.lock`): Stoppe diesen Prozess auf dieselbe Weise.

## <a id="telegram-bot-doesnt-respond"></a>Der Telegram-Bot antwortet nicht

1. `aipager status`: Wenn die Zeile von aipager `not running` sagt, starte es.
2. `aipager doctor`: Die Prüfungen `token_valid` und `chat_reachable` fragen die Telegram-API von Anfang bis Ende ab und zeigen den genauen Fehler.
3. Falscher Chat: Führ `aipager config` aus und wähl **Test bot reachability**. Um einen falschen privaten Chat zu korrigieren, füg den richtigen mit **Add a DM scope** hinzu (siehe [IDs herausfinden](groups.md#finding-ids-auto-detect)) und entfern dann den falschen unter **Edit a scope**.
4. Der Bot hat nie `/start` bekommen: Öffne den Bot in Telegram und tippe einmal auf Start.

## <a id="busy-cards-got-slower"></a>Die Arbeitskarten sind langsamer geworden

Telegram erlaubt ungefähr eine Nachricht pro Sekunde in einen Chat (und 20 pro Minute in eine Gruppe). Jede Nachricht und jede Bearbeitung zählt. aipager gibt jedem Chat sein eigenes Budget von etwa 1 Aufruf pro Sekunde, mit einem kleinen Puffer für Spitzen, und die Arbeitskarten dieses Chats **teilen** es sich. Die Anzeige „schreibt…“ gehört auch zu diesem Budget, als das **unwichtigste**, was der Chat sendet (siehe „Die Schreibblase“ unten).

Der Anteil der Blase wird zuerst beiseitegelegt (ein Aufruf alle 4,5 Sekunden, solange die älteste arbeitende Aufgabe des Chats jünger als zehn Minuten ist, seltener, wenn sie älter wird, siehe „Die Schreibblase“), und die Karten teilen sich den Rest. Eine einzelne Karte in einem privaten Chat wird also etwa alle 4,8 Sekunden aktualisiert; bei zwei arbeitenden Sitzungen im selben Chat jede Karte etwa alle 9,7 Sekunden; bei dreien etwa alle 14,5. **Eine langsamere Karte ist das Budget, das seine Arbeit macht, kein Bug.** Antworten, Erwiderungen und Rückmeldungen auf Buttons werden nie gebremst, um einer Karte Platz zu machen: Sie behalten ein reserviertes Token, und eine Kartenbearbeitung, die sich keinen Aufruf leisten kann, wird einfach übersprungen und beim nächsten Durchlauf erneut versucht, statt sich vor deine Antwort zu stellen.

In einer **Gruppe** ist das Limit 20 Aufrufe pro Minute, egal wie viele Sitzungen darin sind, und die Blase allein würde zwei Drittel davon verbrauchen. Die Karten der Gruppe behalten zusammen eine Aktualisierung alle 10 Sekunden (11 s für eine einzelne Karte), und die Blase nimmt den Rest: In einer Gruppe kann sie also ab und zu für ein, zwei Sekunden aussetzen.

So werden die Karten schneller: Lass weniger Sitzungen gleichzeitig pro Chat laufen, oder gib den aktivsten einen eigenen Chat (`aipager config`). Du kannst auch `STREAM_EDIT_INTERVAL` (Standard `1.2`, das Tempo, während die Karte Text streamt) und `BUSY_EDIT_INTERVAL` (Standard `3.0`, wenn sie ruhig ist) als Umgebungsvariablen von aipager einstellen (siehe [Einstellungen aus der Umgebung](commands.md#settings-in-the-environment)). Einen der beiden *unter* die Untergrenze pro Chat zu setzen schadet nicht und ändert nichts: Die Untergrenze gewinnt, und genau das hält den Chat unter dem Limit von Telegram, egal was du einstellst.

### <a id="a-long-turns-card-refreshes-less-often"></a>Die Karte einer langen Aufgabe wird seltener aktualisiert

Das Tempo oben gilt für die ersten zwei Minuten einer Aufgabe. Danach wird die Karte **langsamer, je älter die Aufgabe wird**, und ab 10 Minuten springt ihre Uhr in 30-Sekunden-Schritten, passend zur Aktualisierung, damit sie nie eingefroren aussieht:

| Alter der Aufgabe | Karte höchstens alle | Uhr zeigt |
|---|---|---|
| 0–2 Min. | wie oben (etwa 4,8 s für eine Karte in einem privaten Chat) | `0:45` |
| 2–10 Min. | 10 s | `4:10` |
| 10–60 Min. | 30 s | `23:00`, `23:30` |
| über 60 Min. | 30 s | `1:23:00`, `1:23:30` |

Die Uhr wird auf ihren 30-Sekunden-Schritt abgerundet, und Aktualisierungen liegen mindestens 30 s auseinander, manchmal etwas mehr. Die Karte überspringt also ab und zu einen Schritt: Eine Aktualisierung bei 23 Min. 29 s zeigt `23:00`, und kommt die nächste bei 24 Min. 1 s, zeigt sie `24:00`, `23:30` wird also übersprungen.

Eine neue Tool-Zeile oder ein neuer Satz wartet auf die nächste Aktualisierung; ein **Zustandswechsel** (die Sitzung geht von arbeitend zu wartend auf einen Hintergrund-Agenten und zurück) wird sofort gezeigt, höchstens einmal alle 10 Sekunden. Die Live-Zeilen der Agenten und die Wartezeile nutzen dieselben Schritte. Die fertige Karte zeigt die Dauer der Aufgabe in echten Sekunden (`23:17`).

Das verhindert, dass eine vierstündige Aufgabe eine Nachricht tausende Male bearbeitet: Am 2026-09-23 hat eine Karte genau das getan, und der Chat wurde für sieben Stunden gesperrt. Um das schnelle Tempo für eine ganze Aufgabe zu behalten, schalte **`/settings` → ⏱ Karten-Updates bei langen Aufgaben** aus (für den Chat oder für eine einzelne Sitzung in der Mini App oder unter `👤 Einstellungen pro Sitzung`).

### <a id="the-typing-bubble"></a>Die Schreibblase

Die Blase „schreibt…“ wird von **einer** Schleife pro Chat geschickt, egal wie viele Sitzungen darin arbeiten (in jedem Telegram-Client gibt es die Blase pro Chat), alle `TYPING_INDICATOR_INTERVAL` Sekunden (Standard 4,5: Telegram löscht einen Schreibstatus nach 5; `0` schaltet die Blase ab). Sie zählt im Budget des Chats als das unwichtigste, was der Chat sendet:

- sie **wird langsamer, je älter die Aufgabe wird**, gemessen an der ältesten arbeitenden Aufgabe des Chats: alle 4,5 s in den ersten zehn Minuten (eine durchgehende Blase), danach alle 15 s; Telegram zeigt sie 5 s lang, die Blase einer älteren Aufgabe flackert also, statt zu leuchten. Bei 4,5 s wären es allein 800 Aufrufe pro Stunde, und das Stundenlimit (unten) würde sie lange abschalten; **⏱ Karten-Updates bei langen Aufgaben** auszuschalten hält sie bei 4,5 s;
- sie **erscheint vom Beginn jeder Aufgabe an**; eine junge Karte wird etwas langsamer aktualisiert, um ihr Platz zu machen: Der Anteil der Blase wird beiseitegelegt, bevor die Karten den Rest teilen (oben), die beiden zusammen überschreiten also nie die Limits des Chats;
- sie nimmt nie ein Token, das eine Kartenbearbeitung braucht: Eine Blase, die kurz nach einer Kartenbearbeitung fällig ist, wartet eine halbe Sekunde (`TYPING_RETRY_WAKE`) und versucht es noch einmal, sie kommt also höchstens ein, zwei Sekunden zu spät, fehlt aber nie;
- sie ist das Erste, was wegfällt, wenn das Stundenvolumen des Chats hoch wird (unten);
- ein 429 auf die Blase selbst blockiert nur die Blase, so lange, wie Telegram es verlangt hat (die Karten laufen weiter), startet aber die sechsstündige Warnphase des Chats (das Tempo ist auf 0,5 Aufrufe/s begrenzt, die Karten werden also auch langsamer);
- eine **Sperre** auf die Blase (ein `retry_after` über der Grenze unten) schaltet den Chat genauso stumm wie eine Sperre auf jeden anderen Aufruf: Es geht nichts mehr hinein, und die Antwort der Aufgabe wird zurückgehalten und zugestellt, wenn die Sperre aufgehoben ist.

Früher war sie kostenlos (eine Schleife pro arbeitender Sitzung, außerhalb jedes Budgets), bis am 2026-09-23 zwei Sitzungen in einem privaten Chat sie in drei Stunden etwa 5.400-mal geschickt hatten und Telegram die Blase selbst begrenzt hat.

## <a id="the-bot-slowed-down-a-rate-limit-429"></a>Der Bot ist langsamer geworden: ein Ratenlimit (429)

Wenn Telegram einen Chat trotzdem begrenzt, beantwortet es einen Aufruf mit `429` und einem `retry_after` von ein paar Sekunden. aipager ruft diesen Chat dann genau so lange nicht auf, holt den einen aufgeschobenen Versand danach nach und **verdoppelt das Kartenintervall dieses Chats** (bis zu ×8). Jede ruhige Minute halbiert es wieder bis zum Normalwert.

Was du siehst:

- `aipager status` (und die Zeile `aipager daemon` von `aipager doctor`) zeigt **`Telegram flood backoff ×4 (chat …), last 429 12 s ago`**.
- Genau eine Zeile in `aipager logs`, ohne Traceback: `flood: chat … 429 retry_after=5s → cadence ×2`.
- Nichts ist stummgeschaltet, und **keine Antwort geht verloren**: Sie wird aufgeschoben, nicht verworfen.
- Die **erarbeitete Senderate des Chats halbiert sich**, und der Chat kommt in eine **sechsstündige Warnphase** (`FLOOD_WARNING_HOURS`): Seine Rate darf nicht über 0,5 Aufrufe/s steigen (`FLOOD_WARNED_CEILING`), und sie steigt nur langsam wieder. `aipager status` zeigt das: `Telegram chat 123: rate 0.25/s (ceiling 0.50/s), 212/1200 calls in the last hour (as of 18s ago), 7/30 in the last minute - warning regime, 5h 52m left`.

Nichts zu tun. Das erledigt sich von selbst. Die Zeile zum Bremsen verschwindet, sobald der Chat eine oder zwei Minuten ruhig war; die Rate steigt pro ruhigem Zeitfenster um 0,1 Aufrufe/s (normalerweise eine Minute, in der Warnphase 36 Minuten), und die volle Obergrenze kommt zurück, wenn die Warnphase endet. `aipager doctor` lässt die Zeile von aipager die ganze Zeit grün: Ein Chat, der bremst, ist normaler Betrieb. (Ein 429 wurde früher nach etwa fünf Minuten vergessen. Die eine Warnung von Telegram vor der Sperre vom 2026-09-23 kam 3 Std. 23 Min. vorher, und der Chat war Minuten danach wieder auf voller Geschwindigkeit.)

### <a id="the-hourly-budget"></a>Das Stundenbudget

Jeder Chat hat außerdem eine gleitende **Stunde**: höchstens `FLOOD_HOURLY_MAX` (1200) Aufrufe in beliebigen 60 Minuten, Schreibblasen, Kartenbearbeitungen und Antworten zusammen; nur Reaktionen sind ausgenommen. Die letzten 120 (`FLOOD_HOURLY_ESSENTIAL_RESERVE`) sind für Antworten, Erwiderungen und Anfragen reserviert:

- bei 810 Aufrufen in der Stunde pausiert die Schreibblase (sie kommt unter 648 zurück);
- bei 1080 geht der Chat in den **Minimalmodus** (unten), bis die Stunde wieder auf 864 fällt;
- Antworten werden von der Stunde nie abgelehnt; wenn sie jemals über 1200 gehen, sagt das Log das einmal pro Stunde.

`aipager status` zeigt die Aufrufe des Chats in der letzten Stunde im Verhältnis zu seinem Budget. Die Zahl kommt aus der Zustandsdatei, die aipager höchstens einmal pro Minute neu schreibt, solange Aufrufe laufen, sie ist also mit ihrem Alter beschriftet. Zwei aktive Sitzungen, die stundenlang in einen privaten Chat streamen, bleiben deutlich unter dem Budget (der Vorfall vom 2026-09-23, nachgespielt: etwa 900 Aufrufe in der vollsten Stunde, gegenüber geschätzten etwa 3.400 für dieselbe Arbeit ohne Budget).

### <a id="the-earned-rate-and-why-your-cards-may-be-slower-than-they-were"></a>Die erarbeitete Rate, und warum deine Karten langsamer sein können als früher

Seit 0.7.13 wird die dauerhafte Senderate jedes Chats **gelernt statt angenommen**. Telegram veröffentlicht das Limit nicht, das für deinen Bot tatsächlich gilt (es hängt vom jüngsten Verlauf deines Kontos ab), daher startet aipager jeden Chat bei 0,5 Aufrufen/s, legt pro ruhiger Minute 0,1 drauf bis zu einer Obergrenze von 1/s, halbiert sie bei einem 429 und senkt sie nach einer Sperre auf 0,05. Ein Bot, der gerade gesperrt war, wird also viel vorsichtiger getaktet als einer, der es nicht war, und er erarbeitet sich seine Geschwindigkeit in den folgenden Stunden zurück.

Fällt die Rate eines Chats unter 0,2 Aufrufe/s, oder ist der Anteil seines Stundenbudgets für Karten und Blasen aufgebraucht, geht er in den **Minimalmodus**: Die Arbeitskarten werden nicht mehr animiert und zeigen eine einzige feste Zeile `⏳ x1 · arbeitet (Karte wird gerade nicht aktualisiert)`, die Schreibblase hört auf, und die angeheftete Statusleiste zeigt `⏸ Karten werden gerade nicht aktualisiert (Stundenlimit von Telegram)` (oder `(Limit von Telegram)`, wenn die Rate den Chat dorthin gebracht hat) und ändert sich sonst nicht mehr, bis es vorbei ist. **Antworten, Erwiderungen und Berechtigungsanfragen laufen weiter**: Genau darum geht es. Die Karte macht etwa 95 % von dem aus, was dieser Bot sendet, und die Antwort etwa 5 %; unter Druck spart er also Pixel und keine Arbeit. `aipager doctor` meldet den Minimalmodus als Warnung, damit eine pausierte Karte nie für eine kaputte gehalten wird; er endet von selbst, wenn sich die Rate erholt.

## <a id="the-bot-went-quiet-flood-control"></a>Der Bot ist verstummt: eine Sperre von Telegram (flood control)

Ein `retry_after` über `TELEGRAM_MAX_RETRY_AFTER` (Standard 90 s) ist kein Ratenlimit: Es ist eine **Sperre**, und sie kann Stunden dauern. Im Chat sieht es aus, als wäre der Bot tot: Nachrichten erreichen Claude weiter, Sitzungen arbeiten weiter, aber keine Antwort kommt zurück.

Was du siehst:

- `aipager status` (und die Zeile `aipager daemon` von `aipager doctor`) zeigt **`Telegram flood-muted until HH:MM (chat …)`**.
- Eine Zeile in `aipager logs`: `Telegram flood control — chat … muted for Ns (until HH:MM) …`, danach Stille für diesen Chat. Später eine Zeile `flood mute on chat … lifted`.
- `aipager status` zeigt außerdem den vollständigen Stand des Chats: `Telegram chat 123: rate 0.05/s (ceiling 0.50/s), 431/600 calls in the last hour (as of 12s ago) - MINIMAL MODE, card updates paused - flood-muted until 09:41 - warning regime, 5h 59m left (1 ban(s) in the last 7 days)`.

aipager schaltet den Chat genau so lange stumm, wie Telegram verlangt hat, und macht **keinen Aufruf irgendeiner Art** an ihn: Antworten, Bearbeitungen der Arbeitskarte, Anhänge, Reaktionen, Schreibanzeigen, kurze Hinweise nach dem Tippen auf einen Button und die Antworten, die deine Befehle erzeugt hätten. Jeder davon wäre ein neuer Verstoß, der die Sperre verlängert; eine Anfrage mitten in eine aktive Sperre hat am 2026-09-15 aus einer 21-minütigen Sperre eine von 9,5 Stunden gemacht. Ein Befehl, der während einer Stummschaltung getippt wird, bekommt daher gar keine Antwort, und **auch ein Tippen auf einen Button wird nicht bestätigt**: Bis 0.7.12 ging der kurze Hinweis trotzdem raus, in der Annahme, dass Telegram ihn getrennt zählt. Für das Tempo stimmt das; für Sperren nicht. Andere Chats sind nicht betroffen.

**Dem Bot während einer Sperre zu schreiben ist sicher, und nichts geht verloren.** Nichts, was du im Chat tust, bringt den Bot dazu, Telegram aufzurufen, solange der Chat stummgeschaltet ist; es kann die Sperre also nicht verlängern:

- Eine Nachricht, die du schickst (Text, Sprachnachricht, eine Datei, eine schnelle Vorlage), erreicht Claude trotzdem, und die Sitzung arbeitet daran. Ihre Arbeitskarte wird zurückgehalten, nicht verworfen: Läuft die Aufgabe noch, wenn die Sperre aufgehoben wird, erscheint die Karte dann. Eine Sperre lässt den Chat danach noch eine Weile im Minimalmodus, die Karte kann also ein paar Minuten länger brauchen als die Sperre selbst. Ist die Aufgabe während der Sperre fertig geworden, bekommst du ihre Antwort und keine Karte.
- **🔄 Erneut versuchen** tut während einer Sperre nichts. Die Nachricht wird nicht noch einmal geschickt und der Button bleibt, du kannst also nach der Sperre noch einmal darauf tippen. Bis einschließlich 0.7.14 ging die Nachricht an Claude, während die Fehlermeldung und ihr Button blieben, ein zweites Tippen schickte sie also noch einmal.
- Die Haupttastatur wird ebenfalls zurückgehalten, auch die, die ein Neustart schickt (bis 0.7.14 ging sie verloren, bis etwas anderes sie aktualisiert hat). Sie wird nach der Sperre geschickt.

Nach der Sperre kommt alles in dieser Reihenfolge: zurückgehaltene Antworten, dann Karten für Aufgaben, die noch laufen, dann die Tastatur. Eine Karte, die auf das Ende des Minimalmodus warten muss, kommt nach der Tastatur.

**Antworten gehen nicht verloren.** Eine Antwort, die entsteht, während der Chat stummgeschaltet ist, wird zurückgehalten und nach der Sperre innerhalb weniger Sekunden zugestellt, mit der ersten Zeile `⏳ verspätet angekommen: 42 Min. wegen eines Telegram-Limits zurückgehalten`. Du musst nicht noch einmal fragen, und wenn du noch einmal gefragt hast, kommen beide Antworten, die älteste zuerst: Eine stundenlange Sperre umfasst mehrere Aufgaben, und die Antwort jeder einzelnen gehört dir. (Zurückgehaltene Antworten liegen im Speicher: Startest du aipager neu, während welche warten, gehen sie verloren; das Log beim Herunterfahren sagt, wie viele. Ein Chat hält höchstens 20 zurück und nichts, was älter als 24 Stunden ist; alles, was aus einem dieser Gründe wegfällt, steht als Warnung im Log, mit dem, was es war.)

**Eine Sperre macht den Chat danach langsamer, nicht schneller.** Sobald eine Sperre greift, fällt die gelernte Rate des Chats auf die Untergrenze, und die Sperre wird gezählt; die stummen Stunden bringen nichts zurück, der Anstieg beginnt neu ab dem Moment, in dem die Sperre endet, und **sieben Tage lang** (`FLOOD_BAN_MEMORY_DAYS`) werden die Obergrenze der Rate des Chats *und* sein Stundenbudget durch eins plus die Zahl der Sperren dieser Woche geteilt: Eine Sperre halbiert beides, zwei dritteln es. `aipager status` zeigt das alles. (Dieses Gedächtnis dauerte früher 24 Stunden und halbierte nur die Obergrenze; ein am 2026-09-19 gesperrter Chat war am 2026-09-23 wieder auf voller Geschwindigkeit und wurde für sieben Stunden gesperrt.) Fünf oder mehr Sperren in einer Woche halten den Chat im Minimalmodus, bis sie verfallen: Antworten laufen weiter, die Karten bleiben pausiert.

Was du NICHT tun solltest:

- Starte aipager nicht neu, um es zu „reparieren“. Die Stummschaltung endet von selbst zur angezeigten Zeit. Seit 0.7.13 vergisst ein Neustart die Sperre nicht mehr (die Frist, die erarbeitete Rate des Chats und sein Verlauf an Sperren werden in `~/.claude/aipager-flood-state.json` gespeichert und beim Start wiederhergestellt, ebenso die letzte Stunde an Aufrufen des Chats und seine Warnphase), ein Neustart verlängert sie also auch nicht mehr. Er wirft aber alle noch zurückgehaltenen Antworten weg und repariert nichts.
- Senk `TELEGRAM_MAX_RETRY_AFTER` nicht unter die standardmäßigen 90 s, in der Hoffnung, früher erneut zu versuchen: Alles über dieser Grenze ist eine Sperre, kein Ratenlimit.

So vermeidest du es: Lass weniger Sitzungen gleichzeitig pro Chat laufen, oder gib den aktivsten einen eigenen Chat (`aipager config`).

## <a id="a-session-dropped-off-the-pinned-status-bar-it-is-gone"></a>Eine Sitzung ist aus der angehefteten Statusleiste verschwunden (sie ist GONE)

Der dtach-Prozess dieser Sitzung hat sich beendet (Neustart des Computers, `pkill claude`, jemand hat in der verbundenen dtach-Sitzung `exit` getippt). Erstell sie neu: Ihr Ordner `~/.claude/projects/...` enthält die Unterhaltung noch.

Am einfachsten ist `/resume <label>` in Telegram (oder `/resume` allein für eine Auswahl): Das startet die Sitzung neu und setzt die Unterhaltung fort. Aus dem Terminal:

```sh
aipager resume <label>             # dasselbe aus dem Terminal
aipager session <label>            # oder eine neue Sitzung, ohne Verlauf
aipager session <label> --continue # neues dtach, die letzte Unterhaltung des Ordners
```

## <a id="a-permission-prompt-with-no-news-for-5-minutes"></a>Eine Berechtigungsanfrage seit 5 Minuten ohne Neuigkeiten

Wenn eine Sitzung auf eine Berechtigungsanfrage oder eine Frage wartet und Claude Code 5 Minuten lang nichts meldet, liest aipager das gespeicherte Gespräch der Sitzung. Nur wenn es zeigt, dass die Anfrage vorbei ist (im Terminal beantwortet, und die Nachricht davon ging verloren), hört aipager auf, auf sie zu warten, und zählt die Sitzung wieder als arbeitend. Sonst bleibt die Anfrage offen, egal wie lange es dauert: Die Karte und die angeheftete Leiste sagen weiter, dass die Sitzung deine Antwort braucht, es kommt kein Hinweis „arbeitet noch“, und deine Nachrichten werden zurückgehalten, bis du antwortest (siehe [Text](commands.md#text)). Ein Claude Code, das sich mit offener Anfrage beendet, beendet die Sitzung. Die Buttons der Anfrage beantworten den Dialog weiterhin, wenn er noch offen ist (siehe [Antworten](commands.md#idle-responses)).

Die Wartezeit ändern:

```sh
AIPAGER_INTERACTIVE_TIMEOUT=300 aipager start    # in Sekunden
```

## <a id="voice-extra-wont-install-via-telegram"></a>Die Sprachfunktion lässt sich nicht über Telegram installieren

Der Button `[📦 Sprachfunktion installieren]` startet den Installer und bearbeitet seine Nachricht alle paar Sekunden, während er arbeitet (`läuft noch… (25 s)`). Schlägt die Installation fehl, zeigt die Nachricht den Exit-Code und die letzten 500 Zeichen der Ausgabe des Installers. Häufige Ursachen:

- Netzwerk: pip erreicht PyPI nicht. Prüf die Verbindung von dem Computer aus, auf dem aipager läuft.
- Festplatte: `~/.cache/huggingface/` braucht etwa 74 MB für das Modell und der Wheel-Cache von pip noch etwa 250 MB. Mach zuerst Platz frei.
- Permission denied: venv ohne Schreibrechte. Wechsel zu `uv tool install`, das immer ein venv nutzt, das deinem Benutzer gehört.

Hat der Bot mitten in der Installation die Verbindung verloren, ist die Installation selbst auf dem Computer meist fertig geworden: Starte aipager neu und versuch noch einmal eine Sprachnachricht.

## <a id="open-app-shows-open-this-page-from-the-telegram-app-to-sign-in"></a>Beim Öffnen der App steht „Öffne diese Seite aus der Telegram-App, um dich anzumelden.“

Die Seite wurde geladen, das Skript des Mini-App-SDK von Telegram (das, das `initData` erzeugt) aber nicht, die Seite hat also nichts zum Anmelden und macht überhaupt keinen API-Aufruf. Die Meldung klingt, als wäre es deine Schuld; die Ursache liegt im Netzwerk.

Neuere Versionen von aipager holen dieses Skript selbst und liefern es vom eigenen Ursprung der Seite aus (`/telegram-web-app.js`), das Handy muss also nur den Host erreichen, der die Seite geliefert hat, nicht zusätzlich `telegram.org`. Siehst du das auf einer älteren Version, aktualisiere.

Dann der Reihe nach:

1. Sieh dir den Quelltext der Seite an. Zeigt ihr `<script>`-Tag auf `https://telegram.org/js/telegram-web-app.js`, hat aipager das Skript noch nicht holen können, und die Seite lädt es ersatzweise von Telegram, und genau das scheitert auf einem Handy, das `telegram.org` nicht erreicht. aipager holt es, wenn der Server der Mini App startet, und versucht es höchstens einmal am Tag erneut, ein Neustart von aipager versucht es also sofort; `journalctl --user -u aipager | grep "webapp sdk"` sagt, was schiefging (meist ein blockiertes oder gefiltertes `telegram.org`).
2. Zeigt das Tag auf `/telegram-web-app.js`, prüfe, ob aipager es ausliefert: `curl -sI http://127.0.0.1:8765/telegram-web-app.js` auf dem Computer von aipager (pass den Port an, wenn du ihn geändert hast) sollte mit `200` und `Content-Type: application/javascript` antworten. Die Kopie liegt unter `~/.local/share/aipager/webapp-sdk/`.
3. Du hast die URL in einem normalen Browser geöffnet statt über den Button `/app` oder den Menü-Button des Bots: Dann ist das die erwartete Meldung. Das SDK erzeugt `initData` nur in Telegram.
4. Die Seite war länger als fünf Minuten offen: `initData` läuft ab, und die Seite bittet darum, neu geöffnet zu werden. Tippe noch einmal auf `/app`.
5. Du hast auf `📱 App` in einer Tastatur einer älteren aipager-Version getippt. Telegram gibt einer Mini App, die über einen Tastatur-Button geöffnet wird, kein `initData`, deshalb hat die Tastatur von aipager diesen Button nicht mehr. Schick `/start`, um die aktuelle Tastatur zu bekommen, und öffne die App über den Menü-Button oder mit `/app`.

## <a id="pyexpat-_xml_setalloctrackeractivationthreshold-on-brew-install"></a>`pyexpat _XML_SetAllocTrackerActivationThreshold` bei der Installation mit brew

Das Paket `python@3.12` von Homebrew wurde gegen eine neuere libexpat gebaut, als dein System hat, fast immer, weil **Xcode und die Command Line Tools** auf macOS Tahoe (26.x) **veraltet** sind. Die Ausgabe von brew sagt dir das meistens selbst. Zwei Lösungen:

- **Nimm stattdessen `uv tool install aipager`**. uv bringt sein eigenes python mit und umgeht das Problem ganz. Das ist der empfohlene Weg auf macOS; siehe das [README](README.md#install).
- **Aktualisiere Xcode und die Command Line Tools**:
  ```sh
  sudo rm -rf /Library/Developer/CommandLineTools
  sudo xcode-select --install
  ```
  Oder öffne Xcode im App Store und aktualisiere auf die neueste Version.

## <a id="modulenotfounderror-aipager"></a>„ModuleNotFoundError: aipager“

Dein aipager-Programm verweist auf ein Python, in dem aipager nicht mehr installiert ist (oft, nachdem ein venv gelöscht wurde). Installier neu:

```sh
uv tool install --reinstall aipager
# oder mit dem Installer, mit dem du angefangen hast: pipx, brew, pip
```

## <a id="the-session-list-was-lost-or-damaged"></a>Die Sitzungsliste ist verloren oder beschädigt

aipager speichert seine Sitzungen in `~/.claude/aipager-sessions.json` und schreibt sie atomar neu (eine neue Datei, dann ein Umbenennen), ein unterbrochenes Schreiben lässt die alte Datei also ganz. Fehlt die Datei trotzdem, ist sie unlesbar oder kein gültiges JSON, loggt aipager, dass es neu anfängt (eine Warnung, wenn die Datei da war, aber nicht gelesen werden konnte), und startet mit einer leeren Liste; es stoppt nicht. Von dieser Datei gibt es keine Sicherungen. Nichts Wichtiges geht verloren:

- laufende Sitzungen werden beim ersten Durchlauf des Monitors über ihre dtach-Sockets wiedergefunden (`/tmp/claude-dtach-*.sock`);
- jede Unterhaltung liegt noch im eigenen Ordner `~/.claude/projects/...` von Claude Code, `aipager session <name> --continue` im Ordner dieser Sitzung setzt sie also fort (`--resume` lässt dich eine auswählen);
- verloren sind die Liste der beendeten Sitzungen (`/resume`), die Karten, die offen waren, und der Rest dessen, was aipager sich zu jeder Sitzung gemerkt hatte.

Scheitert aipager weiterhin beim Start, sagt `aipager logs` (oder `journalctl --user -u aipager`), warum; leg die Datei beiseite (`mv ~/.claude/aipager-sessions.json ~/aipager-sessions.json.old`), um sie als Ursache auszuschließen.

## <a id="a-session-launch-fails"></a>Der Start einer Sitzung schlägt fehl

Einen Start, den dtach verweigert, zeigt der Chat mit einem einzeiligen Grund (Socket existiert schon, Shell nicht ausführbar, kein Pseudo-Terminal, Name zu lang, Zugriff verweigert, Ordner fehlt); die Meldung von dtach selbst, mit Pfaden, steht in `aipager logs`.

## <a id="aipager-doctor-check-list"></a>Die Prüfliste von `aipager doctor`

Die Reihenfolge zählt: Jede spätere Prüfung setzt voraus, dass die früheren bestanden sind. Eine Prüfung, die in einer unerwarteten Umgebung abstürzt, erscheint als eine einzige ⚠-Zeile mit dem Namen der Prüfung und dem Fehler; die übrigen Prüfungen laufen trotzdem.

| Prüfung (Schlüssel bei `--json`) | Was sie prüft | Lösungshinweis |
|---|---|---|
| `config_parses` | `aipager.yaml` und `policy.yaml` lassen sich lesen; läuft als Erste, weil jede spätere Prüfung die Konfiguration liest | der Fehler nennt Datei und Zeile |
| `config` | `~/.config/aipager/aipager.yaml` existiert und hat einen Token und einen Chat | `aipager config` |
| `token_valid` | Der Token funktioniert mit `getMe` von Telegram | `aipager config` noch einmal ausführen |
| `chat_reachable` | Telegram kennt den eingerichteten Chat (`getChat`; es wird nichts gesendet) | den Bot öffnen, auf Start tippen |
| `team` | Die Chats und Mitglieder (oder ein altes `team.yaml`): sagt, ob persönlicher Betrieb oder Teambetrieb, und meldet ein fehlerhaftes oder nicht passendes altes `team.yaml` | `aipager config` |
| `role_shell_access` | Warnt einmal für jede Rolle, die ein Mitglied hat und mit der Bash laufen kann (ihre Sicherheitsregeln sind nur nach bestem Bemühen, keine Grenze; siehe [Gruppen](groups.md#roles)) | dieser Rolle in `policy.yaml` kein Bash geben, oder es hinnehmen |
| `peer_messages` | Nachrichten zwischen Claude-Sitzungen: wo ihre Belege liegen und ob dieser Ordner privat ist, ob dieses System die Startzeit des Absenders prüfen kann, und eine Warnung für jede Rolle, der eine Regel SendMessage erlaubt (siehe [Sicherheit](security.md#messages-between-claude-sessions)) | einen gemeinsam genutzten Ordner für Belege entfernen (aipager legt ihn neu an), oder dieser Rolle kein SendMessage geben |
| `claude` | Findet das Programm `claude` genau so wie jeder Start (`claude_path` in `aipager.yaml` oder `$AIPAGER_CLAUDE_BIN` gewinnen direkt; sonst das neueste aus `~/.local/bin`, PATH und Homebrew) und listet jede ANDERE gefundene Installation auf | Claude Code installieren, oder `claude_path` / `AIPAGER_CLAUDE_BIN` setzen |
| `claude_auth` | Probiert `claude auth status` in derselben Umgebung, die eine echte Sitzung bekommt. **Schlägt nie fehl**: „nicht angemeldet“ und „der Versuch selbst ist fehlgeschlagen“ werden getrennt gemeldet, und keins von beiden hindert eine Sitzung am Start | `claude auth login`, oder einen API-Schlüssel / `CLAUDE_CODE_OAUTH_TOKEN` setzen |
| `dtach` | Das Programm `dtach` wurde gefunden (das eigene `dtach-bin` von aipager oder eins im PATH) | `uv tool install --reinstall aipager` |
| `hook_scripts` | `aipager-hook` und `aipager-statusline` sind im PATH | `uv tool install --reinstall aipager` |
| `settings_json` | In `~/.claude/settings.json` sind die Hooks von aipager eingetragen | `aipager config` |
| `daemon` | aipager läuft und sein Socket antwortet; zeigt auch den Stand der Telegram-Limits (siehe [oben](#the-bot-slowed-down-a-rate-limit-429)) | `aipager start` |
| `service_installed` | Optional: die Dienstdatei ist vorhanden | `aipager service install` |
| `service_unit_path` | Nur Linux: Das `Environment=PATH=` des *installierten* Dienstes enthält wirklich den Ordner des gefundenen claude-Programms, als Text gelesen, weil doctor den PATH von systemd aus der interaktiven Shell des Betreibers nicht sehen kann | `aipager service install --yes` |
| `miniapp` | Ob die Mini App starten kann, wenn sie an ist. **Schlägt nie fehl**: Die Mini App ist optional | die Lösung, die sie nennt |

`aipager doctor --json` gibt dieselben Prüfungen in dieser Reihenfolge mit diesen Schlüsseln aus (siehe [Befehle](commands.md#aipager-doctor---json)).

Führ `aipager doctor --fix` aus, um interaktiv Claude-Anmeldedaten zu finden und in `daemon.env` zu kopieren (auch wenn die Datei da ist, aber keine enthält), oder um `claude_path` festzulegen, wenn mehr als eine Installation gefunden wird. Es handelt nur, nachdem es gefragt hat. Für einen Dienst, in dessen PATH der Ordner von claude fehlt, führ `aipager service install --yes` aus.

## <a id="the-daemon-cant-find-claude-or-picks-the-wrong-install"></a>aipager findet `claude` nicht oder nimmt die falsche Installation

Früher haben sechs Stellen `claude` jeweils für sich gesucht und konnten sich widersprechen. Jetzt gehen alle über eine einzige Suchfunktion (`aipager/claude_resolve.py`). `claude_path` in `aipager.yaml`, dann `$AIPAGER_CLAUDE_BIN`, gewinnen direkt, wenn sie funktionieren. Sonst wird jedes `claude` geprüft, das in `~/.local/bin`, im `$PATH` und in den Präfixen von Homebrew liegt, und die neueste Version gewinnt (eine andere Reihenfolge im `$PATH` ändert die Wahl also nicht, wenn die andere Installation neuer ist). Führ `aipager doctor` aus, um genau zu sehen, welche es gewählt und was es sonst gefunden hat; gewinnt die falsche, leg die richtige fest:

```sh
aipager doctor --fix        # interaktive Auswahl unter den gefundenen Installationen
# oder von Hand:
echo 'claude_path: /home/you/.local/bin/claude' >> ~/.config/aipager/aipager.yaml
```

## <a id="sessions-cant-authenticate-under-the-systemd-service-but-claude-works-fine-in-a-terminal"></a>Sitzungen können sich unter dem systemd-Dienst nicht anmelden, aber `claude` funktioniert im Terminal

Dienste von `systemctl --user` lesen nie `~/.bashrc` oder `~/.profile`, eine Zeile `export CLAUDE_CODE_OAUTH_TOKEN=…` darin erreicht aipager also nie. Der Dienst nutzt stattdessen `LoadCredential=` von systemd und liest `~/.config/aipager/daemon.env`: eine einfache Datei mit `KEY=VALUE`, 0600, die beim ersten Ausführen von `aipager service install` automatisch angelegt wird (aus einem alten `config.env` übernommen, wenn es einen Token enthielt, sonst einmal aus deiner Login-Shell ermittelt, sonst mit einer Warnung leer gelassen). Ist sie immer noch leer, sucht `aipager doctor --fix` an denselben Stellen noch einmal, wenn du ja sagst, und fügt die gefundene Zeile hinzu, ohne die anderen Zeilen der Datei anzutasten; den Token gibt es nie aus. Oder füg die Zeile selbst hinzu:

```sh
cat ~/.config/aipager/daemon.env          # sehen, was drin ist (oder nicht)
echo 'CLAUDE_CODE_OAUTH_TOKEN=sk-...' >> ~/.config/aipager/daemon.env
chmod 600 ~/.config/aipager/daemon.env
aipager service stop && aipager service start
```

Siehe [Sicherheitsmodell: die Claude-Anmeldedaten](security.md#the-claude-credential-what-actually-protects-it) dafür, wovor diese Datei schützt und wovor nicht.

## <a id="the-daemons-control-socket-moved"></a>Der Steuer-Socket von aipager ist umgezogen

`aipager doctor` / `aipager status` haben früher immer `/tmp/aipager.sock` angesehen. Unter dem systemd-Dienst liegt der Steuer-Socket jetzt unter `$XDG_RUNTIME_DIR/aipager.sock` (mit `/tmp` als Ausweichstelle nur, wenn `$XDG_RUNTIME_DIR` nicht gesetzt ist: Container, WSL1, minimale Distributionen). Nur dieser eine Socket ist umgezogen; die dtach-Sockets der einzelnen Sitzungen (`/tmp/claude-dtach-*.sock`) sind nicht betroffen. Überschreib ihn mit `AIPAGER_SOCKET_PATH`, wenn du einen bestimmten Ort brauchst.

## <a id="update-says-the-restart-would-kill-sessions-killmode"></a>`/update` sagt, dass der Neustart Sitzungen beenden würde (KillMode)

Dienstdateien, die vor dieser Version geschrieben wurden, haben keine Zeile `KillMode=`, systemd nutzt also `control-group`: `aipager.service` zu stoppen oder neu zu starten beendet jede Claude-Sitzung, die aipager gestartet hat. `/update` installiert daher die neue Version, startet aipager aber nicht neu, und listet die Sitzungen auf, die ein Neustart mitreißen würde. Beheb das einmal:

```
aipager service install
```

Das schreibt den Dienst mit `KillMode=process` neu, lädt systemd neu und startet aipager neu, und dieser Neustart läuft schon mit der neuen Einstellung. Prüf es mit `systemctl --user show -p KillMode aipager.service` (`KillMode=process`). Damit loggt systemd bei jedem Neustart „left-over process“-Zeilen für deine Sitzungen; das ist so gewollt.

## <a id="aipager-update-cant-find-uv--pipx"></a>`aipager update` findet uv oder pipx nicht

`aipager update` sucht den Installer mit absolutem Pfad in deinem `PATH` sowie in `~/.local/bin`, `~/.cargo/bin`, den Präfixen von Homebrew, `/usr/bin` und `/bin`. Sagt es trotzdem, dass es `pipx` nicht finden konnte, ist der Installer, der dieses aipager angelegt hat, weg oder liegt woanders; installier neu mit dem Tool, das du hast (`pipx install --force aipager`, `uv tool install --reinstall aipager`).

## <a id="update-refused-editable--not-your-install"></a>Update verweigert: editable oder nicht deine Installation

`/update` und `aipager update` aktualisieren nur eine Installation, die deinem eigenen Benutzer gehört und von ihm beschreibbar ist, über den Installer, der sie angelegt hat. Sie verweigern es, mit Grund, bei:

- einer editierbaren Arbeitskopie (Entwicklung): aktualisiere sie mit `git pull`;
- einer Installation über Nix, Snap, Docker oder ein Paket des Systems: nimm das Update dieses Systems;
- einer Installation, die einem anderen Benutzer gehört (zum Beispiel ein venv von root unter `/opt`): aktualisiere sie als dieser Benutzer;
- einer Installation aus einem lokalen Ordner (`pipx install /path/to/aipager`): `von hier aus nicht aktualisierbar: it was installed from a local folder; update it from that folder`. Hol die neue Version in diesen Ordner und installier daraus neu, oder wechsel mit `pipx install --force aipager` zu PyPI;
- einer Installation, bei der sich der Installer nicht feststellen lässt (`could not tell which installer owns this aipager`): installier sie mit `uv tool install aipager` oder `pipx install aipager` neu.

## <a id="update-failed-unexpected-argument---refresh"></a>Update fehlgeschlagen: unexpected argument '--refresh'

aipager 1.0.0 und älter führten `uv tool upgrade aipager --refresh` aus, was keine Version von uv annimmt, jedes Update einer uv-Installation stoppte also mit `error: unexpected argument '--refresh' found` und startete nichts neu. Neuere Versionen führen `uv tool upgrade aipager` aus. Um einmal darüber hinwegzukommen, aktualisiere aus einem Terminal auf diesem Computer (`aipager update` hat auf diesen Versionen dasselbe Problem):

```
uv tool upgrade aipager
systemctl --user restart aipager.service
```

Ohne den Dienst (oder auf macOS) starte aipager so neu, wie du es gestartet hast.

## <a id="claude-code-update-installed-with-npm-into-a-folder-you-cant-write-to"></a>Update von Claude Code: mit npm in einen Ordner installiert, in den du nicht schreiben kannst

Ein mit `sudo npm install -g` installiertes Claude Code liegt in einem Ordner, den nur root ändern kann (wie `/usr/lib/node_modules`), `claude update` als dein Benutzer scheitert also mit `global folder isn't writable`. `/update` und die Mini App zeigen die neuere Version mit `von hier aus nicht aktualisierbar: installed with npm into a folder your user can't write to` und ohne Button dafür. Führ `claude install` einmal als der Benutzer aus, unter dem aipager läuft: Das wechselt zum eigenen Installer von Claude Code in deinem Home-Ordner, der ohne sudo aktualisiert. Dann starte aipager neu (`systemctl --user restart aipager.service`, oder so, wie du es gestartet hast): Ein laufendes aipager behält das Claude Code, das es beim Start gefunden hat, und nach dem Neustart nimmt es das neueste, das es findet. Wenn `claude_path` in `aipager.yaml` (`aipager doctor --fix` kann es setzen) oder `AIPAGER_CLAUDE_BIN` auf das von npm zeigt, lass es auf `~/.local/bin/claude` zeigen oder entfern es vorher, denn diese gewinnen immer.

## <a id="updated-but-the-version-didnt-change"></a>Aktualisiert, aber die Version hat sich nicht geändert

Eine pipx-Installation aus einer git-URL aktualisiert aus dieser git-Quelle, nicht aus PyPI. Hat die Quelle nichts Neueres, sagt `/update` `✅ aipager ist schon auf A: Diese pipx-Installation aktualisiert aus ihrem git-Repo, und dort gibt es nichts Neueres.`. Push die neue Version dorthin oder wähl sie dort aus, oder wechsel mit `pipx install --force aipager` zu PyPI.

## <a id="the-daemon-didnt-come-back-after-an-update"></a>aipager ist nach einem Update nicht zurückgekommen

Das Update prüft vor jedem Neustart, dass die neue Version lädt, aber wenn das neue aipager trotzdem nicht startet, versucht systemd es alle 5 s erneut. Sieh nach, warum:

```
journalctl --user -u aipager.service -n 50
```

Installier die vorige Version mit demselben Installer neu und starte dann neu:

```
pipx install --force aipager==<previous>        # oder:
uv tool install --force aipager==<previous>     # oder:
<venv>/bin/python -m pip install aipager==<previous>
systemctl --user restart aipager.service
```

Eine pipx-Installation aus einem lokalen Pfad: `pipx install --force /path/to/aipager` auf dem alten Commit. Wenn eine veraltete Meldung aussteht, entfern sie mit `rm -f ~/.local/share/aipager/update-restart.json` (sie wird nach 24 Std. ohnehin ignoriert); `~/.local/share/aipager/update.lock` ist nur eine Sperrdatei und kann gefahrlos gelöscht werden, wenn kein Update läuft.

## <a id="an-update-was-interrupted-when-aipager-shut-down"></a>„Ein Update wurde unterbrochen, als aipager herunterfuhr“

aipager wurde gestoppt (oder neu gestartet), während `/update` installierte, und der Installer wurde mitgestoppt, die Installation ist also vielleicht halb geschrieben. Wenn aipager oder Claude Code sich seltsam verhalten, repariere es: Für aipager führ den Befehl zur Neuinstallation aus der Nachricht aus (zum Beispiel `pipx install --force aipager`) und danach `systemctl --user restart aipager.service`; für Claude Code führ `claude update` noch einmal aus.

## <a id="a-chat-is-in-the-wrong-language"></a>Ein Chat ist in der falschen Sprache

Die Sprache von aipager kommt aus dem, was für den Chat gewählt wurde, nicht aus den Nachrichten darin. Öffne `/language` in diesem Chat und wähl die Sprache, die du willst: Diese Wahl gewinnt gegen alles andere. In einem privaten Chat folgt **Automatisch** der Sprache deiner Telegram-App (Einstellungen → Sprache in Telegram); eine Sprache, die aipager nicht hat, erscheint auf Englisch. Ein privater Chat, den es schon vor den Sprachen gab, bleibt Englisch, bis du eine wählst. Eine Gruppe ist Englisch, bis ein Admin ihre Sprache wählt. Die Sprache ändert nur die Texte von aipager selbst: Claude antwortet in der Sprache, in der du ihm schreibst, egal was aipager zeigt. Siehe [Sprachen](commands.md#languages).

## <a id="the-bot-description-is-not-the-one-i-wrote"></a>Die Beschreibung des Bots ist nicht die, die ich geschrieben habe

aipager setzt die Beschreibung und die Kurzbeschreibung des Bots in jeder Sprache, die es spricht, für Personen, deren Telegram-App in dieser Sprache ist, und Telegram zeigt ihnen diesen Text statt dessen, den du in BotFather geschrieben hast. aipager tut das nur, solange du keine eigene Beschreibung hast; eine Beschreibung, die du vor der Installation dieser Version schreibst, erreicht also alle. Hast du sie geschrieben, nachdem aipager seine gesetzt hatte, sehen Personen in diesen Sprachen weiter die von aipager. Um ihnen deine zu zeigen, lösch den Text von aipager für jede Sprache über die Bot-API (`setMyDescription` und `setMyShortDescription` mit einem leeren Text und diesem `language_code`); aipager setzt ihn nie wieder, solange deiner da ist.

## <a id="aipager-says-languagesjson-could-not-be-used"></a>aipager sagt, languages.json konnte nicht verwendet werden

aipager speichert, was es über Sprachen gelernt hat (wer schon vor den Sprachen da war, die letzte Telegram-Sprache, die es bei jeder Person gesehen hat, und ob das einmalige Angebot und die Zeile beim ersten Kontakt verschickt wurden), in `~/.local/state/aipager/languages.json`. Lässt sich diese Datei nicht lesen, legt aipager sie als `languages.json.bad-<time>` im selben Ordner beiseite, loggt eine Warnung (bei jedem Start erneut, bis du entscheidest) und lässt jeden Chat ohne Sprachangebot auf Englisch, bis du entscheidest: Leg die alte Datei zurück, wenn du sie reparieren kannst, oder lösch `languages.json` und starte neu, dann zählt aipager jede Person, die es kennt, wieder als schon vorhanden (Englisch, bis sie wählt). Eine Sprache, die jemand gewählt hat, steht nicht in dieser Datei und geht nie verloren.

## <a id="right-to-left-text-looks-wrong"></a>Text von rechts nach links sieht falsch aus

Auf Persisch und Arabisch setzt aipager unsichtbare Richtungszeichen in seine eigenen Texte, damit jede Zeile von rechts nach links läuft, und lässt Sitzungsnamen, Befehle und Code von links nach rechts. Sieht eine Zeile trotzdem durcheinander aus, ist es meist ein Telegram-Client, der gemischte Schreibrichtungen anders zeichnet: Prüf es in einem anderen Client (Handy gegen Desktop oder Web), bevor du es meldest. Die eigenen Antworten von Claude werden so gezeigt, wie Claude sie geschrieben hat, ohne zusätzliche Zeichen.

## <a id="a-translated-keyboard-word-does-nothing-in-another-chat"></a>Ein übersetztes Tastaturwort tut in einem anderen Chat nichts

Die Tastatur jedes Chats ist in der Sprache dieses Chats. Die Wörter der Haupttastatur (status, stop, new, kill und die übrigen) werden in jedem Chat in jeder Sprache verstanden, und die englischen Wörter funktionieren immer. Das Wort eines Vorlagen- oder Befehls-Buttons ist anders: In einem Chat mit einer anderen Sprache getippt, ist es eine Nachricht für Claude und kein Button, ein russisches „Clear“ führt in einem persischen oder englischen Chat also nie `/clear` aus. Nimm den Button selbst oder den englischen Befehl (`/clear`). Sieht eine Tastatur nach einem Sprachwechsel des Chats veraltet aus, sende `/start`, um die aktuelle zu bekommen. Ein Wort, das auch der Name einer deiner Sitzungen ist, spricht mit dieser Sitzung. `/mode` liest `ask`, `auto` und `full` immer als den Modus, und ein übersetztes Moduswort nur dann als Sitzung, wenn eine laufende Sitzung genau diesen Namen hat.

## <a id="still-stuck"></a>Kommst du nicht weiter?

Schick einen Problembericht: Tippe in deinem privaten Chat mit dem Bot unter `/help` auf **Problem melden**, oder führ `aipager report` in einem Terminal aus. Du siehst den ganzen Bericht, bevor irgendetwas gesendet wird, und er enthält keine Chats, Nachrichten, Namen, Pfade oder Tokens (siehe [Problemberichte](problem-reports.md)). Nach dem Senden bekommst du eine Referenz wie `ap1-7c41d09e2b3a`.

Wenn du darüber sprechen willst, öffne danach ein Issue auf [github.com/dev-aly3n/aipager/issues](https://github.com/dev-aly3n/aipager/issues) und gib diese Referenz an. Bitte füg keine Ausgabe von `aipager doctor` oder `aipager logs` in ein Issue ein: Sie enthalten deine Chat-ID, die Pfade deines Home-Ordners, Sitzungsnamen und den Anfang deiner Nachrichten. Wird eine Log-Zeile gebraucht, fragt die Person, die aipager pflegt, nach genau dieser einen Zeile, und du kannst vorher alles Private daraus entfernen.

## <a id="see-also"></a>Siehe auch

- [Architektur](architecture.md): was jeder Teil tut.
- [Sicherheitsmodell](security.md): was aipager anfasst und was nicht.
