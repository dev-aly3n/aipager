# Hook-Ereignisse

<!-- i18n-source: docs/hooks.md @ 932dbe80 -->

> Diese Seite ist eine Übersetzung der englischen Seite; wenn die beiden voneinander abweichen, gilt die englische Fassung. ([English](../../hooks.md))

Claude Code führt Hook-Befehle zu festen Zeitpunkten einer Aufgabe aus: Eine Nachricht wird abgeschickt, ein Tool wird gleich ausgeführt, ein Tool ist fertig, die Aufgabe ist zu Ende und so weiter. aipager hängt sich an jeden dieser Zeitpunkte und macht daraus Aktivität in Telegram: die Arbeitskarte, Berechtigungsanfragen, Antworten, Hinweise und das Audit-Log.

## <a id="how-it-is-wired"></a>Wie es verbunden ist

`aipager config` (oder `aipager setup`) trägt die Einträge von aipager in `~/.claude/settings.json` (oder `$CLAUDE_CONFIG_DIR/settings.json`, die Datei, die die Sitzungen des Daemons lesen) ein, und aipager prüft sie bei jedem Start:

- ein Befehl `aipager-hook` für jedes der 16 Ereignisse unten, mit dem Matcher `*` bei den Tool-Ereignissen (`PreToolUse`, `PostToolUse`, `PermissionRequest`);
- ein Timeout von 5 Sekunden bei `PreModelSwitch` und, aus `aipager config` und `aipager setup`, eines von 30 Sekunden bei `PermissionRequest` (der Hook selbst wartet höchstens 20 Sekunden, siehe unten); die anderen Einträge nehmen den Standard von Claude Code;
- der Befehl `statusLine`, `aipager-statusline`.

`aipager uninstall` nimmt diese Einträge wieder heraus (siehe [Befehle](commands.md#command-line-aipager-uninstall)).

Claude Code startet für jedes Ereignis einen neuen Prozess `aipager-hook` (`aipager.dtach.notify_hook:main`). Er liest das JSON des Ereignisses von stdin, fügt zwei Felder hinzu (unten) und schickt es als ein Datagramm über einen Unix-Socket an aipager. Der Socket ist, in dieser Reihenfolge: `$AIPAGER_INSTANCE_DIR/aipager.sock` für eine isolierte Testinstanz, `$AIPAGER_SOCKET_PATH`, `$XDG_RUNTIME_DIR/aipager.sock` oder `/tmp/aipager.sock`. Das Senden wartet nie: Wenn kein aipager zuhört, geht das Ereignis verloren, und der Sitzungsmonitor holt den Stand aus den Dateien der Sitzung nach.

Die meisten Ereignisse kosten Claude Code etwa 20 bis 25 ms Prozessstart. Einige laden zusätzlich den Regelcode von aipager: vor einem Tool-Aufruf (die Sicherheitsprüfung, siehe [PreToolUse](#pretooluse)) etwa 70 ms, am Ende einer Aufgabe (um die Markierung der offenen Aufgabe zu löschen) etwa 55 ms, und `UserPromptSubmit` (das die Regeln der Aufgabe schreibt). Ein `PermissionRequest` wartet absichtlich auf dein Tippen, bis zu 20 Sekunden.

Der Hook begrenzt seinen eigenen Speicher auf 1 GB. Ein Hook, der die Grenze erreicht, beendet sich mit einem Fehler (bei `PreToolUse` mit dem Exit-Code, den Claude Code als Ablehnung liest) und schickt aipager ein Datagramm `hook_memory_cap_hit`, aus dem ein Hinweis im Chat wird, mit einem Button „🐞 Das melden“ für den Besitzer der Installation (siehe [Problemberichte](problem-reports.md)). Setz `AIPAGER_DEBUG=1` in der Umgebung der Sitzung, damit der Hook und die Statuszeile ihre Diagnosen auf stderr ausgeben.

## <a id="what-the-daemon-receives"></a>Was aipager bekommt

Das Datagramm ist das Hook-JSON von Claude Code selbst, ergänzt um die Felder von aipager (einige Ereignisse bringen mehr mit, das jeweils unten beim Ereignis steht):

| Feld | Bedeutung |
|---|---|
| `hook_event_name` | Das Ereignis (Feld von Claude Code; aipager verteilt die Arbeit danach). |
| `session_id`, `transcript_path`, `cwd`, ... | Die eigenen Felder von Claude Code, unverändert. `session_id` ist die Gesprächs-ID von Claude Code. |
| `session` | Von aipager hinzugefügt: sein Name für die Sitzung, `claude-<name>`, aus der Variablen `CLAUDE_DTACH_SESSION`, die aipager setzt, wenn es eine Sitzung startet. Ereignisse aus einer Claude-Code-Sitzung, die aipager nicht gestartet hat, haben keinen Namen und werden ignoriert. |
| `sl_tokens` | Von aipager hinzugefügt, bei jedem Ereignis außer `MessageDisplay`, wenn die Statuszeilen-Datei der Sitzung existiert: `context_pct`, `total_output`, `total_input`, `current_output`, `lines_added` und `lines_removed`. |
| `claude_pid` | Von aipager hinzugefügt, bei den eigenen Ereignissen der Sitzung: die PID ihres `claude` (`CLAUDE_PID` von Claude Code), mit der aipager dieses claude in der Sitzungsliste von Claude Code findet. |
| `bg_job` | Von aipager hinzugefügt, bei jedem Ereignis eines Hintergrund-Jobs von Claude Code, der für die Sitzung zählt (siehe unten): die ID des Jobs. |

Tool-Ereignisse bringen außerdem `tool_name` und `tool_input` mit. Jedes Ereignis aus einem Subagenten trägt `agent_id` (und die Subagenten-Ereignisse `agent_type`); die eigenen Ereignisse der Hauptschleife tragen das nicht, und aipager nutzt das, um die Tool-Aufrufe eines Agenten auf dessen eigene Zeile zu setzen.

Auch die Hintergrund-Jobs von Claude Code führen ihre Hooks aus. ← in einer leeren Eingabezeile verschiebt die Unterhaltung einer Sitzung in einen Hintergrund-Job (die Agent-Ansicht von Claude Code), und Claude Code hält außerdem freie Hintergrundprozesse für den nächsten Job bereit. Ihre Hooks laufen mit gesetztem `CLAUDE_JOB_DIR` und erben den Sitzungsnamen des claude, das den Hintergrunddienst von Claude Code gestartet hat; dieser Name sagt also nichts über sie. Der Hook zählt einen Hintergrund-Job deshalb nur für die Sitzung, deren eigenes claude ihre Unterhaltung in diesen Job verschoben hat: Er findet diese Sitzung in der Verknüpfungsdatei des Jobs (`claude-bgjob-<job>.json`, die aipager schreibt, wenn es den Wechsel sieht, siehe [Architektur](architecture.md#file-and-socket-layout)) oder sonst in der Sitzungsliste von Claude Code zusammen mit der PID-Liste von aipager. Die Ereignisse eines solchen Jobs gehen als Ereignisse dieser Sitzung an aipager, mit gesetztem `bg_job`, sodass seine Antwort, seine Karte und seine Anfragen den Chat der Sitzung erreichen. Jeder andere Hintergrundprozess ist fremd: Sein Hook schreibt nichts, schickt nichts und gibt nichts aus, nur prüft `PreToolUse` seine Tool-Aufrufe weiterhin gegen die Regeln der Sitzung, die er geerbt hat, und gibt eine Ablehnung aus (er erlaubt nie mehr, als diese Sitzung erlauben würde). aipager verwirft ein `bg_job`-Ereignis, das es keiner Sitzung im Hintergrund zuordnen kann, und legt dafür nichts an.

## <a id="event-reference"></a>Referenz der Ereignisse

Die Handler von aipager stehen in `aipager/dtach/hook_receiver.py`; die eigene Arbeit des Hooks steht in `aipager/dtach/notify_hook.py`.

### <a id="sessionstart"></a>`SessionStart`

Eine Sitzung ist gestartet, wurde fortgesetzt oder komprimiert. Eine Sitzung, die aipager noch nicht verfolgt, wird registriert. Nach einer Komprimierung zeigt die Karte `📦 name · Unterhaltung bei 82% komprimiert` (siehe [PreCompact](#precompact--postcompact)). Bei einer neuen Unterhaltung löscht der Hook außerdem die Markierung der offenen Aufgabe der vorigen Aufgabe. Das erste `SessionStart` eines Hintergrund-Jobs (Quelle `fork`) teilt aipager nur die Gesprächs-ID des Jobs mit; die Aufgabe, die mit ihm gewechselt ist, beendet es nicht. Das erste `SessionStart` einer Sitzung, die aus dem Hintergrund von Claude Code zurückgeholt wurde, schickt die Nachrichten, die bis dahin zurückgehalten wurden.

### <a id="sessionend"></a>`SessionEnd`

Die Sitzung ist beendet. Sie wird als GONE markiert: Sie verlässt die angeheftete Statusleiste und wandert zu den beendeten Sitzungen, aus denen `/resume` sie zurückholen kann. `aipager session <name>` oder `/new <name>` startet eine neue. Das `SessionEnd` eines Hintergrund-Jobs (der Job wurde gestoppt) beendet nie die Sitzung: Es beendet eine Aufgabe, die der Job offen gelassen hat, so wie `StopFailure`.

### <a id="userpromptsubmit"></a>`UserPromptSubmit`

Claude hat eine Nachricht abgeholt, im Terminal getippt oder aus Telegram geschickt.

Der Hook gleicht die Nachricht zuerst mit den Berechtigungsnotizen der Sitzung ab (`/tmp/claude-notes-<session>/`, eine pro Telegram-Nachricht, die aipager eingetippt hat) und schreibt die Regeln der Aufgabe nach `/tmp/claude-policy-<session>.json`, die die Prüfung bei [PreToolUse](#pretooluse) liest (siehe [Sicherheit → wie die Regeln im Teambetrieb gelten](security.md#team-mode-enforcement)). Er sagt aipager, welche Nachrichten Claude abgeholt hat (ein Datagramm `queue_pickup`), bevor er das Ereignis weitergibt. Danach gibt er zusätzlichen Kontext für Claude aus, wenn es welchen gibt: den in `/settings` gewählten Antwortstil und, bei einer Antwort auf eine ältere Nachricht, was diese Nachricht gesagt hat und wann. Eine Nachricht `<task-notification>` (Claude weckt sich selbst, wenn ein Hintergrund-Agent oder ein Befehl fertig ist) ist die Fortsetzung derselben Arbeit: Sie passt zu keiner Notiz und behält die Regeln der Aufgabe.

aipager markiert die Sitzung als arbeitend, öffnet die Arbeitskarte, wenn keine da ist (eine aus Telegram geschickte Nachricht hat schon eine), und macht aus dem 👀 der Nachrichten, die Claude abgeholt hat, ein 👍. Eine Nachricht, die während einer laufenden Aufgabe geschickt wird, bekommt ihr 👍, wenn Claude sie in diese Aufgabe aufnimmt oder die nächste Aufgabe damit beginnt.

### <a id="pretooluse"></a>`PreToolUse`

Ein Tool wird gleich ausgeführt. Hier läuft die eigene Sicherheitsprüfung von aipager, im Hook, bevor Claude Code seine Berechtigungsregeln ansieht:

- Eine aus Telegram gestartete Aufgabe wird an die Regeln der Rolle der Person gebunden, die sie geschickt hat, und an die Sicherheitsuntergrenze (geschützte Pfade, Befehlsmuster), die nur die Rolle `owner` umgeht; eine Aufgabe, die aipager niemandem zuordnen kann, bekommt die Untergrenze; eine Nachricht, die du im Terminal getippt hast, ist nicht eingeschränkt. Die Regeln stehen unter [Gruppen](groups.md#how-rules-work) und [Sicherheit](security.md#team-mode-enforcement).
- Wenn eine Regel den Aufruf ablehnt, gibt der Hook eine Entscheidung `deny` aus, Claude Code lehnt das Tool ohne Rückfrage ab, und aipager erfährt es (`safety_blocked`): Die Aufgabe stoppt mit `🛑 x1 · Von einer Sicherheitsregel blockiert: ... (gestoppt)`, und jeder weitere Tool-Aufruf in dieser Aufgabe wird ebenfalls abgelehnt.
- Ein Aufruf von `SendUserFile` (Claude schickt Dateien in den Chat) wird für jede Aufgabe geprüft, auch die des Owners und eine im Terminal getippte: die Grenze für das Senden, die Dateiprüfungen und die eigenen Regeln der Aufgabe ([Sicherheit](security.md#files-claude-sends)). Ein abgelehnter Aufruf bekommt ein `deny` ohne die Markierung „aipager safety policy“: Claude liest den Grund und sagt ihn dir, die Aufgabe läuft weiter, und der Hook schickt aipager `send_files_refused` mit jeder Datei, für das Audit-Log.
- Eine Prüfung, die fehlschlägt, lehnt ab, außer die Regeln der Sitzung konnten gelesen werden und gewähren die Ausnahme des Besitzers.

Auf der Seite von aipager wird der Aufruf zu einer Zeile auf der Arbeitskarte (der Aufruf eines Subagenten kommt auf die Zeile dieses Agenten), und ein `Write` oder `Edit` zeigt eine Diff-Vorschau, wenn `/settings` → Diff-Vorschauen an ist (standardmäßig aus). Ein Aufruf `AskUserQuestion` wird zur Frage mit einem Button pro Option; eine Frage, die ein Hilfsagent von Claude Code stellt, während keine Aufgabe läuft, wird nicht gezeigt (das ist nicht Claude, das dich etwas fragt). Der eigene Aufruf `Agent` von Claude hinterlässt seinen `subagent_type` und seine `description` für den Agenten, den er startet, und der Aufruf eines Subagenten ist die neueste Aktivität dieses Agenten: Beides erscheint im Status unter einer Antwort, solange der Agent im Hintergrund läuft.

### <a id="permissionrequest"></a>`PermissionRequest`

Claude Code will gleich um Erlaubnis fragen. Die Arbeitskarte wird zur Anfrage, mit dem echten Befehl oder Pfad unter der Beschreibung, die Claude davon gibt (siehe [Befehle → Berechtigungsanfragen](commands.md#permission-prompts)): Erlauben, Ablehnen, Stoppen, und Immer erlauben nur dann, wenn Claude Code eine echte Regel zum Merken anbietet.

Der Hook beantwortet die Anfrage selbst, wenn er kann. Er öffnet einen kurzlebigen Antwort-Socket, schickt dessen Adresse mit dem Ereignis und wartet bis zu 20 Sekunden auf dein Tippen. Ein rechtzeitiges Tippen kommt als Entscheidung `PermissionRequest` zurück (erlauben, mit der Regel erlauben oder ablehnen), Claude Code zeigt seinen eigenen Dialog dann nie. Kommt kein Tippen rechtzeitig, sagt der Hook aipager, dass das Warten vorbei ist (`permission_reply_timeout`), und gibt nichts aus; er gibt auch nichts aus, wenn aipager nicht erreichbar ist oder auf diesem Weg etwas schiefgeht. Claude Code zeigt dann seinen eigenen Dialog im Terminal, und ein späteres Tippen beantwortet ihn, indem es die Tasten eintippt. Das Audit-Log (`~/.claude/aipager-audit.jsonl`) hält fest, auf welchem Weg jedes Tippen geantwortet hat, `"via": "hook_decision"` oder `"via": "keystroke_fallback"`.

Ein `PermissionRequest`, der kommt, während eine andere Anfrage offen ist, wird ebenfalls gezeigt, als eigene Nachricht unter der Arbeitskarte. Derselbe Dialog, der noch einmal kommt (derselbe Fragende, dasselbe Tool und derselbe Aufruf, oder dieselbe Eingabe), wird nur einmal gezeigt. Claude Code führt diesen Hook auch für ein `AskUserQuestion` aus; diese Frage ist schon über ihr `PreToolUse` zu sehen und wird deshalb nie ein zweites Mal gezeigt. Der Dialog eines Hintergrund-Agenten erscheint im Terminal erst, wenn sein Hook nicht mehr wartet, unter allen Dialogen, die dort schon offen sind. Deshalb gibt ein Tippen Tasten nur in die Anfrage ein, die Claude Code zuerst zeigt (siehe [Befehle → Berechtigungsanfragen](commands.md#permission-prompts)).

### <a id="posttooluse--posttoolusefailure"></a>`PostToolUse` / `PostToolUseFailure`

Ein Tool ist fertig oder fehlgeschlagen. Seine Zeile auf der Arbeitskarte kommt zur Ruhe (ein Fehler wird auf der Zeile markiert), und eine Antwort auf eine offene Anfrage wird bemerkt: Ein altes Erlauben oder Ablehnen dafür tippt danach nichts mehr. Die Sitzung behält die neuesten 200 Tool-Zeilen.

Ein `Bash`-Aufruf, den Claude Code in den Hintergrund verschoben hat, meldet eine `backgroundTaskId`. aipager verfolgt ihn dann als Hintergrundbefehl (nur die der Hauptschleife: Ein Aufruf mit `agent_id` gehört zu diesem Subagenten), bis sein Ende gesehen wird: eine `<task-notification>` mit einem `<status>` (in der Nachricht, die Claude weckt, oder in der gespeicherten Unterhaltung, wenn sie mitten in einer Aufgabe kommt), ein `TaskStop` oder `KillShell` mit seiner ID, das Ende der Sitzung oder zwei Stunden (`AIPAGER_BG_SHELL_MAX_TRACK`, in Sekunden, Standard 7200). Seine Zeile auf der Karte steht unter [Befehle](commands.md#idle-responses).

Ein `Agent`-Aufruf (oder `Task`-Aufruf) innerhalb eines Subagenten (sein Ereignis trägt `agent_id`) meldet die ID des Agenten, den er gestartet hat. aipager merkt sich, welcher Agent welchen gestartet hat, und kann so einen Agenten, der gestoppt hat, um auf seinen eigenen Agenten zu warten, von einem unterscheiden, der fertig ist (siehe `SubagentStart` / `SubagentStop` unten).

Bei einem `SendUserFile`, das gelaufen ist, kopiert aipager die Dateien sofort, einmal je Aufruf und nur während einer Aufgabe, die mit einem Prompt begann, aus der Liste, die sein `PreToolUse` gezeigt hat (nie aus dem Ergebnis des Tools), und prüft jede noch einmal; die Kopien werden nach der Antwort der Aufgabe geschickt, sobald der Aufruf in dem Gesprächsverlauf gefunden ist, den aipager sich für die Aufgabe gemerkt hat, und während dieser Aufgabe geschrieben wurde ([Sicherheit](security.md#files-claude-sends), [Befehle](commands.md#files-claude-sends)). Eine Aufgabe, die Claude Code ohne Hook beginnt (eine Nachricht, die es als Nächstes übernimmt, die Rückmeldung eines Stop-Hooks), zählt, sobald der Gesprächsverlauf ihren Beginn zeigt.

### <a id="notification"></a>`Notification`

aipager liest den `notification_type` von Claude Code:

- `permission_prompt`: ein langsameres, älteres Signal für eine Berechtigungsanfrage. Es zeigt die Anfrage nur, wenn keine Anfrage offen ist: Ist eine offen, ist es der Dialog, den `PermissionRequest` schon gezeigt hat.
- `idle_prompt`, während eine Berechtigungsanfrage oder eine Frage wartet: Die Sitzung bleibt wartend, und der Chat bekommt `⬆️ jim · wartet noch auf deine Antwort oben`, einmal pro Wartezeit.
- `idle_prompt` sonst: wird wie `Stop` (unten) behandelt. Es fängt eine Aufgabe auf, deren `Stop` verloren ging; eine schon zugestellte Antwort wird nie noch einmal geschickt.

### <a id="stop"></a>`Stop`

Die Aufgabe ist zu Ende. Die Sitzung wird bereit, die Arbeitskarte kommt so zur Ruhe, wie dein Layout es sagt, und die Antwort von Claude wird geschickt (aus `last_assistant_message` des Ereignisses, sonst aus der gespeicherten Unterhaltung). Nachrichten, die bis zum Ende der Aufgabe zurückgehalten wurden, gehen danach raus. Hintergrund-Agenten und -befehle, die noch laufen, halten den Auftrag offen: Die Karte bleibt als Status des Auftrags, und die Antwort endet mit einem Live-Status dieser Arbeit: was jeder Agent gerade tut und wie viele Hintergrundbefehle des Auftrags laufen (siehe [Befehle → Agenten, die noch arbeiten](commands.md#agents-still-running-when-the-answer-goes-out)). Der Hook löscht zuerst die Markierung der offenen Aufgabe, außer Claude hält noch eine wartende Nachricht, die sofort zur nächsten Aufgabe wird.

### <a id="stopfailure"></a>`StopFailure`

Die Aufgabe endete mit einem API-Fehler. Die Sitzung wird bereit, und die Antwort, falls es eine gibt, wird wie bei `Stop` zugestellt; ein bekannter Fehler (überlastet, Ratenlimit, ...) wird in einfachen Worten angezeigt.

### <a id="subagentstart--subagentstop"></a>`SubagentStart` / `SubagentStop`

Ein Subagent ist gestartet oder beendet. Jeder bekommt seine eigene Zeile auf der Arbeitskarte, `🤖 <type> · <activity> · <elapsed>`, mit seinen eigenen Tool-Aufrufen eingeklappt darunter (der neueste immer sichtbar unter der eingeklappten Zeile), und kommt zu `✅ 🤖 <type> · N Tools genutzt · <elapsed>` zur Ruhe, wenn er stoppt. `SubagentStart` sagt nicht, welcher Agent ihn gestartet hat: aipager ordnet ihn dem wartenden `Agent`-Aufruf (`PreToolUse`) seines Typs zu. Kamen alle solchen Aufrufe von einem Agenten, wird der neue Agent sofort mit diesem Agenten verknüpft (seine Zeile auf der Karte lautet `🤖 pipeline-runner › ship-tester`, und im Status unter einer Antwort steht er unter diesem Agenten); kamen sie von verschiedenen Agenten, wird nichts verknüpft, bis das eigene `PostToolUse` des startenden Agenten ihn nennt. Die Beschreibung eines Aufrufs wird nur genutzt, wenn genau ein Aufruf dieses Typs wartet. Ein Subagent, dessen `SubagentStop` nie ankommt, wird entfernt, wenn er 30 Minuten lang still war (`AIPAGER_SUBAGENT_SILENCE`, in Sekunden; kein Ereignis irgendeiner Art mit seiner ID). Ein arbeitender Agent schickt alle paar Sekunden Tool-Ereignisse, das räumt also nur einen auf, dessen Stopp verpasst wurde (ein Neustart, ein verlorenes Datagramm); ein einzelner Tool-Aufruf, der 30 Minuten ohne jedes Ereignis unter der ID dieses Agenten läuft, räumt ihn ebenfalls auf. `AIPAGER_SUBAGENT_TTL` wird als alter Name derselben Einstellung angenommen. Ereignisse `SubagentStop` ohne Typ und mit unbekannter ID kommen oft und werden ignoriert.

Ein Hintergrundagent, der stoppt, während ein von ihm gestarteter Agent noch läuft, bekommt ebenfalls ein `SubagentStop`. aipager nimmt das als Pause, nicht als Ende: Der Agent bleibt laufend (seine Zeile, der Auftrag, der Status unter Antworten, die angeheftete Leiste). Sein nächstes `SubagentStart` mit derselben ID ist sein Weitermachen und fügt keine zweite Zeile hinzu; jeder eigene Tool-Aufruf zählt ebenfalls als Weitermachen. Er endet, wenn er stoppt, ohne dass noch einer seiner eigenen Agenten läuft. Hat er zwei Minuten nach dem Ende des letzten von ihnen nicht weitergemacht, wird er nicht mehr als laufend gezählt, aber nie als fertig gezeigt. Die laufenden Agenten, auch die wartenden, bleiben über einen Neustart von aipager erhalten.

Agenten, die mit den Tasten von Claude Code zum Stoppen der Hintergrund-Agenten (ctrl+x ctrl+k, zweimal gedrückt) gestoppt wurden, lösen kein `SubagentStop` aus. aipager beendet sie selbst: sofort, wenn sein eigenes Stoppen diese Tasten gedrückt hat, wenn das Transkript von Claude Code seine Zeile `agents_killed` bekommt (gelesen beim `UserPromptSubmit` der Aufgabe, die Claude Code dann von selbst startet und deren Eingabe ein schlichter Satz ohne Hülle ist), oder wenn `background_tasks` eines `Stop`-Ereignisses Agenten nicht mehr aufführt, die ein früheres `Stop` aufgeführt hat. Ihre offenen Anfragen schließen sich (kein Hook sagt das), und ein `SubagentStop`, das für einen von ihnen später doch noch kommt, wird ignoriert. Diese Aufgabe wird nie für eine im Terminal getippte Eingabe gehalten.

### <a id="precompact--postcompact"></a>`PreCompact` / `PostCompact`

Claude komprimiert seinen Kontext (`trigger` ist `auto` oder `manual`). Die Arbeitskarte zeigt `🔄 name · Komprimiert die Unterhaltung` (eine neue Nachricht, wenn keine Karte da ist), damit die Pause nicht wie ein Absturz aussieht. `PostCompact` beendet diesen Zustand; die Zeile `📦 name · Unterhaltung bei X% komprimiert` kommt aus dem folgenden `SessionStart` oder, wenn das verpasst wird, aus der Statuszeile. Die Statuszeile von Claude Code zeigt bis zur nächsten Nachricht 0 %, die neue Größe ist also noch nicht bekannt: Nach einer Komprimierung zwischen zwei Aufgaben wird die Zeile zu `📦 name · Unterhaltung komprimiert: X% → Y%`, sobald die Statuszeile sie meldet. Die Statuszeile gibt außerdem die Warnung `⚠️ name · Kontext bei 82% - wird bald automatisch komprimiert` (mit einem Button Komprimieren), sobald der Kontext 80 % erreicht, einmal pro Unterhaltung: Komprimieren wiederholt sie nicht, und eine neue Unterhaltung (zum Beispiel nach `/clear`) bekommt sie wieder.

### <a id="messagedisplay"></a>`MessageDisplay`

Der eigene Text von Claude, während er auf dem Bildschirm erscheint. Die gespeicherte Unterhaltung wird erst nach jeder Runde von Tool-Aufrufen geschrieben, das ist also die einzige aktuelle Quelle dafür, was Claude gerade sagt, und das, was seine Kommentare in die Arbeitskarte fließen lässt. Der Text eines Subagenten (jedes Ereignis mit seiner `agent_id`) wird nie für den von Claude gehalten: Er erreicht nie die Kommentare der Arbeitskarte, die Mini App oder das Log der Aufgabe und wird zur neuesten Aktivität dieses Agenten im Status unter einer Antwort. Der Hook fügt diesem Ereignis keine Daten der Statuszeile hinzu: Claude Code wartet darauf, während es zeichnet, und es kommt mehrmals pro Nachricht.

### <a id="premodelswitch"></a>`PreModelSwitch`

Claude Code 2.1.251 und neuer löst das aus, bevor es das Modell wechselt. Der Inhalt bringt `from_model`, `to_model`, `requested_model` und `source` mit (`command` für ein getipptes `/model`, oder `picker` oder `sdk`). Claude Code fragt vor einem Wechsel meistens "Switch model?", weil die nächste Antwort die ganze Unterhaltung neu lesen muss. Ein Hook, der mit `permissionDecision: "allow"` antwortet, lässt den Wechsel ohne diese Frage durch.

`aipager-hook` antwortet nur bei einem Wechsel mit `allow`, den aipager selbst getippt hat, aus der Modellsteuerung der Mini App oder der Modell-Tastatur. Jeder andere Wechsel bekommt keine Antwort, Claude Code fragt also wie immer. Das betrifft das eigene `/model` des Betreibers im Terminal, die Auswahl und einen Wechsel über das SDK.

aipager erkennt seine eigenen Wechsel an einer Markierungsdatei. Kurz bevor es `/model <name>` tippt, schreibt aipager `aipager-modelswitch-<session>.json`:
- sie liegt in dem Ordner, der den Steuer-Socket enthält (normalerweise `$XDG_RUNTIME_DIR`);
- sie wird atomar geschrieben, mit Rechten 0600;
- sie nennt das Modell und die ID der Claude-Sitzung;
- sie läuft nach 30 Sekunden ab.

Der Hook antwortet nur mit `allow`, wenn all das zutrifft:
- die Markierung ist eine Datei, die dir gehört, und kein symbolischer Link;
- sie ist nicht abgelaufen;
- sie gilt für dieselbe aipager-Sitzung und dieselbe ID der Claude-Sitzung;
- ihr Modell ist gleich `requested_model` oder `to_model`, ohne Rücksicht auf Groß- und Kleinschreibung;
- `source` ist `command`.

Der Hook übernimmt die Markierung dann mit einem atomaren Umbenennen, damit sie genau einmal benutzt wird. Die Prüfung liest eine lokale Datei, ohne Socket und ohne Warten. Das Ereignis wird nicht an aipager weitergegeben.

Claude Code 2.1.101 und neuer ignorieren Hook-Ereignisse, die sie nicht kennen, ältere Versionen ohne `PreModelSwitch` sind also nicht betroffen. Ein Fall kann die Frage noch zeigen: Ein Wechsel zu oder von `opusplan` kann Claude Code den Hook zweimal fragen lassen, und nur die erste Frage findet die Markierung, Claude Code fragt dann also "Switch model?" im Terminal.

## <a id="the-status-line"></a>Die Statuszeile

Kein Hook-Ereignis: Claude Code führt den Befehl `statusLine`, `aipager-statusline` (`aipager.dtach.statusline_notify:main`), jedes Mal aus, wenn es seine Statuszeile neu zeichnet. Der Befehl:

- schreibt das Status-JSON von Claude Code nach `/tmp/claude-status-<session>.json` (gelesen vom Hook für `sl_tokens`, von `aipager status` und von aipager);
- schickt aipager ein Datagramm `statusline` mit dem Modell, dem Kontext in %, den Kosten, den Token-Zahlen und den hinzugefügten und entfernten Zeilen, die in die Arbeitskarte, `/status`, die Mini App, die Prüfung eines Modellwechsels und die Kontextwarnung einfließen;
- gibt die Statuszeile des Terminals aus: `[name] Opus 5.5 | 42% ctx | $1.23`.

## <a id="aipagers-own-datagrams"></a>Die eigenen Datagramme von aipager

Neben den Hook-Ereignissen schicken die Hilfsprogramme aipager ein paar eigene Nachrichten über denselben Socket:

| Nachricht | Geschickt von | Wann |
|---|---|---|
| `queue_pickup` | Hook | Bei `UserPromptSubmit`: welche Telegram-Nachrichten Claude abgeholt hat. |
| `safety_blocked` | Hook | Bei `PreToolUse`, wenn die Sicherheitsprüfung den Aufruf abgelehnt hat. |
| `send_files_refused` | Hook | Bei `PreToolUse`, wenn Dateien abgelehnt wurden, die Claude schicken wollte: jede Datei und der Grund, für das Audit-Log. |
| `permission_reply_timeout` | Hook | Das Warten bei einem `PermissionRequest` endete ohne Tippen. |
| `statusline` | Statuszeile | Bei jedem Neuzeichnen der Statuszeile. |
| `hook_memory_cap_hit` | Hook, Statuszeile | Der Prozess hat seine Speichergrenze erreicht. |
| `report_error` | Hook, Befehle von `aipager` | Er hatte einen Fehler: nur der Typ des Fehlers und seine Stelle im Code von aipager, nie der Inhalt des Ereignisses, für [Problemberichte](problem-reports.md). |

## <a id="the-permission-flow"></a>Der Weg einer Berechtigungsanfrage

```
claude              aipager-hook             aipager daemon          Telegram
  |                      |                         |                     |
  | PermissionRequest    |                         |                     |
  |--------------------->| opens a reply socket    |                     |
  |                      |------------------------>| busy card becomes   |
  |                      |                         | the prompt          |
  |                      |                         |-------------------->|
  |                      |                         |   you tap Allow     |
  |                      |                         |<--------------------|
  |                      |   allow (reply socket)  | audit log           |
  |                      |<------------------------|                     |
  |  decision: allow     |                         |                     |
  |<---------------------|                         |                     |
  | tool runs            |                         |                     |
```

Kommt innerhalb von 20 Sekunden kein Tippen, gibt der Hook nichts aus und Claude Code zeigt seinen eigenen Dialog; ein späteres Tippen tippt die Antwort über das Terminal der Sitzung in diesen Dialog (`dtach -p`). In beiden Fällen wird das Tippen im Audit-Log festgehalten und unter der Karte veröffentlicht (`✅ jim · Erlaubt · @alice · Bash: ls -la /tmp`).

## <a id="see-also"></a>Siehe auch

- [Architektur](architecture.md): wo der Hook-Empfänger hingehört.
- [Befehle des Bots → Berechtigungsanfragen](commands.md#permission-prompts): die Seite, die du siehst.
- [Sicherheitsmodell](security.md): was die Sicherheitsprüfung abdeckt und was nicht.
