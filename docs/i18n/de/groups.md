# Teambetrieb und Gruppen

<!-- i18n-source: docs/groups.md @ a236da1 -->

> Diese Seite ist eine Übersetzung der englischen Seite; wenn die beiden voneinander abweichen, gilt die englische Fassung. ([English](../../groups.md))

Standardmäßig ist aipager ein privater Bot: du und der Bot in einem privaten Chat, sonst niemand. Der **Teambetrieb** öffnet ihn für mehr Personen: eine Telegram-Gruppe, in der mehrere Leute, die programmieren, Sitzungen steuern, die eigenen privaten Chats anderer Personen mit dem Bot, oder beides. Jeder Chat hat seine eigenen Mitglieder, und jedes Mitglied hat eine Rolle, die entscheidet, was es dort tun darf: Nachrichten schicken, die Fragen und Berechtigungsanfragen von Claude beantworten, Sitzungen verwalten oder nur zusehen.

Der Teambetrieb ist optional. Eine Installation nur mit deinem eigenen privaten Chat funktioniert wie bisher.

## <a id="decide-carefully"></a>Entscheide mit Bedacht

Eine Person aus Telegram zu einem Chat hinzuzufügen gibt ihr **das Recht, auf dem Computer, auf dem aipager läuft, Code auszuführen**. Je nach Rolle kann sie:

- Nachrichten schicken, die Claude in Shell-Befehle, Dateiänderungen und Netzwerkaufrufe umsetzt;
- Berechtigungsanfragen beantworten (Erlauben / Ablehnen). Deine `~/.claude/settings.json` entscheidet weiterhin, nach welchen Tools Claude fragt, aber alle, deren Rolle zustimmen darf, können auf Erlauben tippen;
- Sitzungen starten, stoppen, beenden und wechseln.

Behandle die Liste der Mitglieder so, wie du den SSH-Zugang zum Computer behandelst. Das Audit-Log ([`~/.claude/aipager-audit.jsonl`](security.md#audit-log)) hält fest, wer was getan hat, aber erst hinterher.

## <a id="setup"></a>Einrichtung

Alles wird mit `aipager config` eingerichtet. Du bearbeitest nie eine Datei von Hand.

**Beim ersten Mal** (noch keine Konfiguration). Der Assistent fragt in vier Schritten:

1. den Token des Bots;
2. deinen eigenen privaten Chat mit dem Bot (automatisch erkannt oder eingefügt);
3. ob dein Konto der `owner` ist (der Standard; sagst du nein, bist du stattdessen `admin`, wodurch die Sicherheitsuntergrenze für dich aktiv bleibt);
4. er prüft die Abhängigkeiten und installiert die Hooks von Claude Code.

Er schreibt deinen privaten Chat als ersten Chat. Nach einem Standardmodus fragt er nicht: Seine Zusammenfassung sagt "New sessions start in Auto mode; owners and admins can choose Full access in Telegram under /settings → New sessions." (neue Sitzungen starten im Auto-Modus; Besitzer und Admins können in Telegram unter /settings → Neue Sitzungen den Vollzugriff-Modus wählen). Dann fragt er "Connected. Add a team group or other people now?" mit Add a group, Add a person (DM) und I'm done.

**Bei späteren Aufrufen** öffnet sich das Bearbeitungsmenü. Es zeigt deine Chats und bietet an:

- **Add a group scope**: eine neue Gruppe mit ihren Mitgliedern (unten).
- **Add a DM scope**: den eigenen privaten Chat einer anderen Person mit dem Bot. Ein privater Chat hat ein Mitglied; du wählst dessen Rolle (Standard `user`; wähl `owner` nur bei einer Installation für eine einzige Person, etwa dem eigenen Container eines Freundes). Bei einer Rolle ohne `can_manage` (wie `user`) fragt er danach nach dem [Stammordner](#root-folders), mit dem Standard schon eingetragen (`~/aipager-users/<label>`). Bei einer Person, die schon einen privaten Chat eingerichtet hat, sagt der Assistent das und ändert nichts: Nimm Edit a member, um ihre Rolle zu ändern.
- **Edit a scope**: bei einer Gruppe **Add a member**; bei jedem Chat Rename, Edit scope deny_tools, **Set the members' root folder** (siehe [Stammordner](#root-folders)) und Remove this scope (der letzte Chat kann nicht entfernt werden). Beende die Sitzungen eines Chats, bevor du ihn entfernst: Seine laufenden Sitzungen schreiben weiter dorthin, und kein Chat kann sie aus Telegram mehr erreichen (vom Computer aus können `aipager session ls` und `aipager session kill <name>` das weiterhin).
- **Edit a member**: Set role, Edit member deny_tools, Remove member (nicht in einem privaten Chat und nicht beim letzten Mitglied einer Gruppe). Bei einem Besitzer oder Admin (oder einer deiner Rollen mit `bypass_role_denies`) sagt Edit member deny_tools nur, dass blockierte Tools für diese Rolle nicht gelten.
- Test bot reachability, View policy, Re-install Claude Code hooks und Refresh bot token.

### <a id="adding-a-group"></a>Eine Gruppe hinzufügen

1. Füg den Bot zur Gruppe hinzu, dann wähl Add a group scope (oder beim ersten Mal Add a group).
2. **Die ID der Gruppe.** Wähl die automatische Erkennung und sende `/start` in der Gruppe, oder füg die ID ein (eine negative Zahl). Der Assistent schickt eine Testnachricht und fragt, ob sie angekommen ist. Eine Gruppe, die schon eingerichtet ist, endet hier mit "This group is already set up. Use Edit a scope → Add a member.", und nichts ändert sich.
3. **Einen Namen** für die Gruppe (im Assistenten und in der Mini App angezeigt).
4. **Zuerst du.** Wenn du Besitzer deines eigenen privaten Chats bist, bietet der Assistent an, dich als erstes Mitglied der Gruppe mit der Rolle `owner` hinzuzufügen (Standard ja; du kannst es überspringen). Ohne Besitzer oder Admin in der Gruppe könnte dort niemand den Vollzugriff-Modus, `/settings` oder `/update` nutzen.
5. **Die anderen Mitglieder**, eins nach dem anderen: wie man sie findet (automatische Erkennung, oder eine ID oder ein `@handle` einfügen), einen Namen (im Chat als `@label` gezeigt) und eine Rolle (Standard `user`). Die Liste der Rollen enthält `owner`, mit einer Warnung: Ein Besitzer hat die volle Kontrolle über diesen Computer, einschließlich der Konfiguration von aipager, des Tokens des Bots und der Anmeldedaten von Claude. Gib das nur dir selbst. Gib `admin` den Personen, die in der Gruppe den Vollzugriff-Modus, `/settings` und `/update` nutzen sollen. (In ihrem eigenen privaten Chat mit dem Bot dürfen alle, die Nachrichten schicken und Claude antworten können, den Vollzugriff-Modus für ihre eigenen Sitzungen nutzen, egal welche Rolle sie haben.)
6. **Eine optionale Regel:** "Block file edits (Write, Edit) for `user` members in this group? Admins and the owner are not affected." (Dateiänderungen für die Mitglieder mit `user` in dieser Gruppe blockieren? Admins und der Besitzer sind nicht betroffen.) Standard ist nein: Ein `user` schreibt ohnehin nur im Projektordner der Sitzung. Mit ja wird jedes Write und Edit in einer Aufgabe eines Mitglieds, dessen Rolle die Rollenregeln nicht umgeht (`user`, `read_only` und Rollen, die du ohne `bypass_role_denies` definierst), ohne Rückfrage abgelehnt, und die Aufgabe stoppt. Ein Admin oder der Besitzer muss diese Arbeit dann stattdessen schicken.
7. **Der Stammordner der Mitglieder**, mit `~/aipager-users/group-<label>` schon eingetragen: wo die Mitglieder der Gruppe mit `user` und `read_only` ihre Sitzungen starten und arbeiten (siehe [Stammordner](#root-folders)).

Um jemanden zu einer Gruppe hinzuzufügen, die schon eingerichtet ist, nimm Edit a scope → die Gruppe → Add a member. Alle, die schon in der Gruppe sind, behalten ihren Platz und ihre Rolle.

### <a id="finding-ids-auto-detect"></a>IDs herausfinden (automatische Erkennung)

Die automatische Erkennung eines Mitglieds nimmt die neueste Nachricht einer Person, die noch nicht hinzugefügt ist, und fragt "Is this the person to add?" (Ist das die Person, die hinzugefügt werden soll?), bevor sie sie nimmt (sag nein, und sie sucht weiter zurück). Sie schlägt den Telegram-Benutzernamen der Person als Namen vor. In einer Gruppe muss die Person den Bot ansprechen: ihn erwähnen oder `/start` senden (siehe [was der Bot liest](#what-the-bot-reads-in-a-group)). Der Assistent sagt, wo er hinsieht:

- **"Watching through the running daemon"** (über das laufende aipager): Solange `aipager start` läuft, bekommt aipager jede Nachricht an den Bot, der Assistent liest also, was aipager in `~/.claude/aipager-pending-users.json` notiert hat. aipager notiert, wer den Bot in einer Gruppe anspricht, für die es noch nicht zuständig ist (die ID, den Titel und den Typ der Gruppe sowie die ID, den Benutzernamen und den Namen der Person), und alle, die nicht Mitglied einer Gruppe sind, für die es zuständig ist. In einer Gruppe, für die es nicht zuständig ist, antwortet es nie. Eine private Nachricht einer Person, die nicht eingerichtet ist, bekommt eine Antwort und wird nicht notiert; um den privaten Chat einer Person hinzuzufügen, während aipager läuft, füg also ihre ID ein (oder stoppe aipager vorher).
- **"Watching Telegram directly"** (direkt in Telegram): Wenn aipager gestoppt ist, fragt der Assistent Telegram selbst nach den neuesten Nachrichten des Bots.

Dein eigener privater Chat wird beim ersten Mal immer aus Telegram gelesen. Eine Nachricht eines anonymen Admins der Gruppe, oder eine, die als die Gruppe oder als ein Kanal gepostet wurde, zählt nicht: Ihre Absender-ID (1087968824, 136817688 oder 777000) teilen alle, die so posten, der Assistent lehnt sie also ab, getippt oder erkannt. Bitte die Person, mit ihrem eigenen Konto zu posten.

### <a id="where-the-config-lives"></a>Wo die Konfiguration liegt

Der Assistent schreibt `~/.config/aipager/aipager.yaml` (Rechte 0600): den Token des Bots, die Chats, für die der Bot zuständig ist, und die Mitglieder jedes Chats mit ihren Rollen. Was jede Rolle tun darf, steht in `~/.config/aipager/policy.yaml`, das dir gehört: Der Assistent schreibt es nie (siehe [Wie die Regeln funktionieren](#how-rules-work)). Installationen, die mit dem älteren Format `team.yaml` eingerichtet wurden, werden automatisch übernommen, von aipager beim Start oder vom Assistenten beim nächsten Aufruf.

### <a id="live-reload"></a>Ohne Neustart neu laden

Nach jeder Änderung an Chats oder Mitgliedern im Bearbeitungsmenü (eine Gruppe oder einen privaten Chat hinzufügen oder entfernen, ein Mitglied hinzufügen oder entfernen, eine Rolle setzen, `deny_tools` bearbeiten, einen Chat umbenennen), und nach einer Gruppe oder Person, die am Ende des ersten Durchlaufs hinzugefügt wurde, oder einer unfertigen Gruppe, die du fortsetzt (ein einziges Neuladen für alles, was du dort hinzugefügt hast), schickt `aipager config` dem laufenden aipager ein Neuladen im laufenden Betrieb (**SIGUSR1**) und sagt "Scopes reloaded live (no daemon restart needed)". Das Neuladen liest `aipager.yaml` und `policy.yaml` erneut. Würde die Konfiguration auf der Festplatte abgelehnt, sagt der Assistent "Not applied" mit dem Grund, und aipager behält seine vorige Konfiguration, bis du es behebst. Kann der Assistent nicht sicher sagen, welcher Prozess aipager ist, sagt er dir stattdessen, es neu zu starten.

Was ein Neuladen sofort übernimmt:

- wer den Bot in jedem Chat nutzen darf, mit welcher Rolle und welchen Regeln;
- eine neue Gruppe oder ein neuer privater Chat bekommt ab jetzt Nachrichten und sein Menü `/`; ein entfernter hört auf (seine Sitzungen laufen weiter, bis du sie beendest);
- eine zurückgehaltene Nachricht (eine, die darauf wartet, dass eine Anfrage beantwortet wird oder die Aufgabe einer anderen Person endet) von einer Person, die in diesem Chat nicht mehr schreiben darf, wird verworfen: Sie bekommt ein 🤷, und der Bot antwortet „Nicht an Claude gesendet: @bob darf hier nicht mehr schreiben.“;
- eine laufende Aufgabe einer Person, die entfernt wurde oder Rechte verloren hat, behält für den Rest der Aufgabe nur das Strengste aus ihren alten und den neuen Regeln (bei einer entfernten Person die eingeschränkte Untergrenze). Eine im Terminal getippte Aufgabe wird nicht verändert, außer eine Telegram-Nachricht ist ihr beigetreten: aipager hält nicht fest, von wem, eine solche Aufgabe wird also eingeengt, wenn irgendein Besitzer dieses Chats (oder, in einem privaten Chat, dessen Mitglied) Rechte verliert. Eine Aufgabe, die schon lief, als aipager neu gestartet wurde (noch arbeitend oder wartend auf eine Frage oder Berechtigung), wird für jedes Mitglied ihres Chats eingeengt, das Rechte verloren hat. Eine Beförderung macht eine laufende Aufgabe nie weiter: Die neuen Rechte gelten ab der nächsten Aufgabe dieser Person.

Nur ein neuer **Token des Bots** braucht einen Neustart (`aipager config` sagt das nach Refresh bot token). Läuft kein aipager, wird kein Neuladen geschickt: Das nächste `aipager start` liest die neue Konfiguration.

Um von Hand neu zu laden (zum Beispiel nach einer Änderung an `policy.yaml`), schick das Signal nur an den Prozess von aipager. Unter dem Hintergrunddienst:

```sh
pid=$(systemctl --user show -p MainPID --value aipager.service)
[ "${pid:-0}" -gt 0 ] && kill -USR1 "$pid"
```

(Die Prüfung ist wichtig: Wenn der Dienst nicht läuft, ist `MainPID` gleich `0`, und `kill -USR1 0` würde das Signal an die Prozesse deiner eigenen Shell schicken.)

Bei einem aipager, das du selbst mit `aipager start` gestartet hast, schick `kill -USR1 <pid>` an diesen Prozess. Schick das Signal nicht an alles, was `pgrep -f 'aipager start'` findet (eine Shell oder ein Container, in dem aipager läuft, passt auch, und SIGUSR1 stoppt einen Prozess, der es nicht behandelt), und nimm `systemctl --user kill` nicht ohne `--kill-whom=main` (die Sitzungen des Dienstes sind in seiner Gruppe und würden gestoppt).

Ist `aipager.yaml` oder `policy.yaml` beim Neuladen fehlerhaft, oder fehlt `aipager.yaml`, loggt aipager eine Warnung und behält die vorige Konfiguration im Speicher. Ein Tippfehler oder eine beiseitegelegte Datei kann dich weder aussperren noch jemanden hereinlassen: Ein Neuladen schaltet ein aipager mit eingerichteten Chats nie zurück in den persönlichen Betrieb (das tut nur ein Neustart).

### <a id="bot-admin-rights-and-privacy-mode"></a>Admin-Rechte des Bots und Privatsphäre-Modus

**Mach den Bot zum Admin der Gruppe, mit dem Recht, Nachrichten anzuheften.** Er braucht das, um die Statusleiste der Gruppe anzuheften (siehe [Was alle sehen](#what-everyone-sees)). Ohne das funktioniert die Gruppe, hat aber keine Statusleiste; gibst du dem Bot das Recht später, starte aipager neu, damit die Leiste erscheint.

Ein Bot, der Admin der Gruppe ist, bekommt jede Nachricht der Gruppe, egal wie sein Privatsphäre-Modus bei BotFather eingestellt ist. Das ist sicher: aipager bearbeitet nur die Nachrichten, die unter [was der Bot liest](#what-the-bot-reads-in-a-group) stehen, und ignoriert den Rest, Unterhaltungen in der Gruppe erreichen also nie eine Sitzung. Ein Bot, der kein Admin ist, ist standardmäßig im Privatsphäre-Modus (lass ihn an), und Telegram gibt ihm dann nur manche Nachrichten der Gruppe weiter; kommt eine deiner Nachrichten nicht an, antworte auf eine Nachricht des Bots oder nimm einen Befehl. Die Privatsphäre-Einstellung ändert nichts daran, was aipager bearbeitet.

Admin der Gruppe in Telegram zu sein gibt einer Person in aipager nichts. Was jemand tun darf, kommt nur aus der Rolle in `aipager.yaml`.

### <a id="when-telegram-upgrades-the-group"></a>Wenn Telegram die Gruppe umstellt

Telegram macht aus einer einfachen Gruppe eine Supergruppe, wenn du sie öffentlich machst, Themen einschaltest, über 200 Mitglieder kommst oder manche Admin-Rechte änderst, und diese Umstellung gibt der Gruppe eine neue Chat-ID. aipager zieht von selbst mit: Wenn es die Umstellung sieht (den Hinweis von Telegram in der Gruppe oder den ersten Versand an die alte ID), verschiebt es den Eintrag der Gruppe in `aipager.yaml` auf die neue ID, nimmt die Sitzungen, die Ziele der Nachrichten und die `/settings` der Gruppe mit und schreibt eine Zeile in die Gruppe: „Telegram hat diese Gruppe umgestellt. aipager ist mit umgezogen, du musst nichts tun.“ Die angeheftete Statusleiste beginnt in der umgestellten Gruppe neu. Das Log von aipager hat eine Warnung mit beiden IDs.

Die Namen der Sitzungen in aipager behalten die alte ID (du siehst sie nie), und `aipager.yaml` hält einen kurzen Eintrag `chat_migrations` des Umzugs fest, damit eine Sitzung, die vor der Umstellung gestartet wurde, weiter zur umgestellten Gruppe gehört, auch nach einem Neustart. Der interne Name einer Sitzung mit der alten ID (`/name__g<old id>`) funktioniert in der umgestellten Gruppe und nirgends sonst. War aipager während der Umstellung gestoppt, bemerkt es sie beim nächsten Start. Eine Aufgabe, die im Moment der Umstellung läuft, wird fertig, und ihre Antwort erreicht die umgestellte Gruppe, aber ihre Arbeitskarte wird für den Rest dieser Aufgabe nicht mehr gezeigt. Ist die neue ID schon ein eigener Chat in der Konfiguration, wird nichts verschoben, und das Log sagt das; entfern einen der beiden mit `aipager config`. „Test bot reachability“ in `aipager config` zeigt die neue ID, wenn es auf eine umgestellte Gruppe trifft.

Bei jedem Start fragt aipager jede Gruppe einmal ab. Antwortet Telegram, dass die Gruppe nicht gefunden wird oder der Bot sie nicht nutzen darf (die Gruppe wurde gelöscht, der Bot wurde entfernt, oder die Gruppe wurde umgestellt, während aipager gestoppt war, und Telegram hat nicht gesagt, auf welche ID), hat das Log von aipager eine Warnung, die den Chat nennt: Prüf, ob der Bot noch in der Gruppe ist, und wenn sie umgestellt wurde, füg die Gruppe in `aipager config` mit Add a group scope noch einmal hinzu (sie bekommt die neue ID; füg ihre Mitglieder noch einmal hinzu) und entfern dann die alte mit Edit a scope → Remove this scope (die alte ID funktioniert nicht mehr). aipager schreibt nichts und versucht es nicht weiter.

### <a id="forum-topics-are-not-supported"></a>Forum-Themen werden nicht unterstützt

Nimm eine normale Gruppe (ohne Themen) pro Team. In einer Gruppe mit eingeschalteten Themen funktioniert aipager weiterhin, bleibt aber nicht in einem Thema:

- die Antwort auf einen Befehl oder eine Nachricht (zum Beispiel `/status` oder die Antwort auf „Welche Sitzung?“) bleibt in dem Thema, in dem sie geschickt wurde;
- Hinweise, Arbeitskarten, Antworten, Berechtigungsanfragen und die angeheftete Statusleiste werden ohne Thema in die Gruppe geschrieben, sie können also in General landen;
- jede Person hat eine aktuelle Sitzung für die ganze Gruppe, nicht eine pro Thema.

Themen einzuschalten stellt eine einfache Gruppe außerdem auf eine Supergruppe um, und aipager zieht mit (oben).

## <a id="roles"></a>Rollen

Vier eingebaute Rollen (siehe `aipager/safety.py`). Es zählt die Rolle der Person **in dem Chat, in dem sie handelt**. Niemand kann Mitglieder oder Rollen aus Telegram oder der Mini App ändern: Das tut nur `aipager config` auf dem Computer, und so bleibt das beim Besitzer.

| | `owner` | `admin` | `user` | `read_only` |
|---|---|---|---|---|
| Nachrichten schicken (`can_prompt`) | ✅ | ✅ | ✅ | ❌ |
| Claude antworten: Erlauben, Ablehnen, Fragen (`can_approve`) | ✅ | ✅ | ✅ | ❌ |
| Verwalten: Vollzugriff-Modus, `/settings` einer Gruppe, `/update`, Installation der Sprachfunktion (`can_manage`) | ✅ | ✅ | ❌ (Vollzugriff-Modus: ✅ im eigenen privaten Chat) | ❌ |
| Mitglieder und Rollen aus Telegram oder der Mini App ändern | ❌ | ❌ | ❌ | ❌ |
| Umgeht die Ablehnungsregeln der Rolle (`bypass_role_denies`) | ✅ | ✅ | ❌ | ❌ |
| Umgeht die Sicherheitsuntergrenze (`bypass_safety`) | ✅ | ❌ | ❌ | ❌ |
| Bash und andere Tools, die Code ausführen | ✅ | ✅ (Regeln nach bestem Bemühen) | ❌ | ❌ |
| Jeder Slash-Befehl | ✅ | ✅ | nur die der Befehle-Tastatur, `/compact` und Modellwechsel | wie `user` |
| Wo Sitzungen starten (`/new`, Fortsetzen, Neu starten, der Moduswechsel) | überall | überall | nur im [Stammordner](#root-folders) des Chats | wie `user` |
| Wohin Dateien geschrieben werden | überall | überall, wo die Sicherheitsuntergrenze es erlaubt | der Projektordner der Sitzung (wenn er im Stammordner liegt) und ihr Scratchpad, nie der Home-Ordner | wie `user` |
| Lesen in deinem Home-Ordner | überall, wo die Sicherheitsuntergrenze es erlaubt | überall, wo die Sicherheitsuntergrenze es erlaubt | nur im Stammordner (andere Ordner auf dem Computer wie bisher, die Stammordner anderer Mitglieder nie) | wie `user` |
| Dateien mit Anmeldedaten (`~/.ssh`, `~/.git-credentials`, ...) | lesbar | lesbar | nie (standardmäßig) | nie (standardmäßig) |
| `Grep`/`Glob` | überall | überall außer in den geschützten Ordnern von aipager (kein Ordner, der einer ist, in einem liegt oder einen enthält) | der Projektordner der Sitzung und ihr Scratchpad | wie `user` |

- **owner**: volle Kontrolle, einschließlich der eingebauten Sicherheitsuntergrenze. Es sollte genau einen geben: die Person, die den Computer betreut.
- **admin**: alles, was ein Besitzer in Telegram und in der Mini App tun kann: Sitzungen in den Vollzugriff-Modus wechseln (und darin starten), eine Sitzung im Vollzugriff-Modus fortsetzen, die `/settings` einer Gruppe ändern, `/update` ausführen, die Sprachfunktion installieren, jeden Slash-Befehl senden. Ein Admin umgeht außerdem die Ablehnungsregeln der Rolle (die `deny_tools` des Chats und des Mitglieds gelten für Admins nicht). Zwei Dinge bleiben beim Besitzer:
  - Mitglieder und Rollen ändern: Das tut nur `aipager config` auf dem Computer, und nichts in Telegram oder der Mini App kann es;
  - die Sicherheitsuntergrenze umgehen (die Konfiguration und der Token des Bots von aipager, die Anmeldedaten von Claude Code, die Steuerdateien von aipager). `Grep` und `Glob` eines Admins dürfen überall sonst suchen, aber nicht in einem Ordner, der einer davon ist, in einem liegt oder einen enthält: Ein `Grep` von `~`, `/` oder `/tmp` (oder ohne Ordner aus einer Sitzung im Home-Ordner) wird abgelehnt, ebenso ein `Glob`, dessen Muster in einem davon beginnt (wie `/home/you/.config/aipager/*`). Such stattdessen in einem Projektordner. Bei Versionen von Claude Code ohne das Tool `Grep` sucht Claude über Bash; auch dort wird eine rekursive Suche oder Auflistung des ganzen Home-Ordners oder von `/` (`grep -rn x ~`, `rg x $HOME`, `find / -name x`) abgelehnt, nach bestem Bemühen (eine Suche ohne Ordner, oder mit `..`, die aus dem Home-Ordner oder darunter läuft, wird nicht gesehen), Suchen in Projekten dagegen nicht, auch keine Suche NACH einem Pfad im Home-Ordner (`grep -rn "$HOME" src`) ([Sicherheit](security.md#team-mode-enforcement)).

  Ein Admin behält Bash, und mit einer Shell sind die Befehlsmuster der Untergrenze nur nach bestem Bemühen, keine Grenze: Mach jemanden nur zum Admin, wenn du dieser Person eine Shell auf dem Computer geben würdest.
- **user**: Die Ablehnungsregeln gelten. Kein Bash und kein anderes Tool, das Code ausführt (PowerShell, Monitor, geplante Nachrichten, Workflows; die Liste ist `safety.CODE_EXECUTION_TOOLS`), kein `SendMessage` an andere Sitzungen, kein Wechsel der Worktrees. Dateien werden nur in den Ordner der Sitzung und ihr Claude-Code-Scratchpad geschrieben und nie in `.claude/`, `.git/` oder `.mcp.json` dieses Ordners (jedes davon führt Befehle aus). `Grep` und `Glob` suchen nur in diesen Ordnern, mit einfachen relativen Globs. Ein `user` startet, setzt fort, startet neu und wechselt Sitzungen nur im [Stammordner](#root-folders) des Chats und liest nichts in deinem Home-Ordner außerhalb davon. Ein Sitzungsordner, der dein Home-Ordner, `/` oder ein Ordner darüber ist, oder der außerhalb des Stammordners liegt (eine Sitzung, die vor den Stammordnern oder von jemand anderem gestartet wurde), zählt nicht: Dort kann ein `user` nur im Scratchpad schreiben und suchen. Lesen folgt den Regeln für geschützte Pfade, und deine Dateien mit Anmeldedaten sind nie lesbar: `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config/gh`, `~/.git-credentials`, `~/.netrc`, `~/.docker/config.json`, `~/.kube`, `~/.config/gcloud`, `~/.azure`, `~/.password-store`, `~/.pgpass`, `~/.npmrc` und `~/.pypirc`. Slash-Befehle: nur die der Befehle-Tastatur, `/compact` und Modellwechsel (siehe [Mit wessen Rechten eine Nachricht läuft](#who-a-message-runs-as)).
- **read_only**: Zuschauer. Sie sehen alles in der Gruppe und können `/status`, `/diff`, `/whoami`, `/help`, `/start` und `/app` nutzen, `/settings` ansehen und in einer Liste von `/resume` blättern, die jemand anderes geöffnet hat. Ihre eigenen Text-, Sprach- und Dateinachrichten werden nirgendwohin geschickt. Die erste, die sie in einem Chat schicken, bekommt eine Antwort, dass ihre Rolle `read_only` ist; danach bleibt der Bot für sie in diesem Chat still (keine Antwort, keine Reaktion), bis aipager neu startet. Sie haben dieselben Grenzen für Tools und Schreiben wie `user`, falls eine ihrer Nachrichten doch einmal eine Sitzung erreicht.

### <a id="custom-roles"></a>Eigene Rollen

`policy.yaml` kann jedes Feld einer eingebauten Rolle ändern oder neue Rollen definieren. Ein Feld, das du setzt, ersetzt den eingebauten Wert. Die Felder:

- `can_prompt`, `can_approve`: Nachrichten schicken; Claude antworten.
- `can_manage`: den Vollzugriff-Modus nutzen, die `/settings` einer Gruppe ändern, `/update` ausführen und die Sprachfunktion installieren (eingebaut: `owner` und `admin` true, `user` und `read_only` false). Es lässt nie jemanden Mitglieder oder Rollen ändern und umgeht nie die Sicherheitsuntergrenze.
- `bypass_role_denies`, `bypass_safety`: siehe die Tabelle.
- `deny_tools`, `allow_tools`, `deny_bash_patterns`, `deny_paths_no_access`, `deny_paths_no_write`: siehe [Wie die Regeln funktionieren](#how-rules-work).

Eine Rolle, die du von Grund auf definierst, beginnt mit `can_prompt` und `can_approve` an, `can_manage` und beiden Ausnahmen aus, den Dateien mit Anmeldedaten in ihrem `deny_paths_no_access` und **ohne abgelehnte Tools, sie hat also Bash**, bis du `deny_tools` setzt (`aipager doctor` warnt einmal, sobald ein Mitglied sie hat). Die Beschränkung von Schreiben und Suchen gilt für jede Rolle ohne `bypass_safety` oder `bypass_role_denies`. Der [Stammordner](#root-folders) gilt für jede Rolle ohne `can_manage`, egal welche Ausnahmen sie hat: Eine Rolle, die du mit `bypass_role_denies` oder `bypass_safety`, aber ohne `can_manage` definierst, schreibt und sucht überall in ihrem Stammordner (und ihrem Scratchpad) und sonst nirgends, und sie liest in deinem Home-Ordner nichts außerhalb ihres Stammordners (mit `bypass_safety` schreibt sie in einer Sitzung, die außerhalb ihres Stammordners läuft, nur in ihr Scratchpad). Nur Besitzer und Admins sind uneingeschränkt.

### <a id="root-folders"></a>Stammordner

Jeder Chat hat einen Stammordner für seine Mitglieder: den Ordner, in dem die Mitglieder, deren Rolle dort kein `can_manage` hat (die eingebauten `user` und `read_only` und Rollen, die du ohne es definierst), ihre Sitzungen starten und behalten. Besitzer und Admins sind nicht daran gebunden. Der Stammordner eines privaten Chats gehört dieser Person; der einer Gruppe wird von den eingeschränkten Mitgliedern der Gruppe geteilt. Zwei Chats teilen sich nie einen, außer du gibst ihnen absichtlich denselben Ordner (der Assistent bittet dich, das zu bestätigen).

- **Festlegen.** `aipager config` fragt danach, wenn du eine Gruppe hinzufügst oder einen privaten Chat für eine Rolle ohne `can_manage`, mit dem Standard schon eingetragen, und Edit a scope → **Set the members' root folder** ändert ihn (sofort übernommen, durch ein Neuladen im laufenden Betrieb). Die aktuelle Konfiguration zeigt den Stammordner jedes Chats, mit `(default)`, wenn keiner gesetzt ist.
- **Der Standard** ist `~/aipager-users/<label>` für einen privaten Chat (der Name der Person) und `~/aipager-users/group-<label>` für eine Gruppe. Der Name wird kleingeschrieben, und alles außer Buchstaben, Ziffern, `.`, `_` und `-` wird zu `-`; würden zwei Chats denselben Ordner bekommen, bekommt jeder die ID seines Chats angehängt (`sara-123456`). Der Ordner (und `~/aipager-users`, wenn er neu ist) wird mit Rechten 0700 angelegt, damit nur du ihn öffnen kannst, wenn ein Mitglied zum ersten Mal `/new` oder das Formular Neue Sitzung der Mini App öffnet. Bevor eine Sitzung darin startet, markiert aipager ihn für Claude Code als vertrauenswürdig, damit die Frage „Do you trust this folder?“ von Claude Code nicht die erste Sitzung des Mitglieds beendet. Ein Standard zieht nie um: Würde eine Änderung in `aipager config` (der Stammordner eines anderen Chats, ein entfernter Chat, ein neuer Name) den Standardordner eines Chats ändern, schreibt der Assistent den Ordner, den er hatte, als Stammordner dieses Chats.
- **Was der Assistent ablehnt**: einen relativen Pfad, Platzhalterzeichen, `/`, deinen Home-Ordner oder einen Ordner darüber, einen Ordner, der einer der geschützten Ordner von aipager oder der Dateien mit Anmeldedaten ist, in einem davon liegt oder einen enthält (`~/.claude`, `~/.config/aipager`, `~/.config`, `~/.ssh`, ...), und den temporären Ordner von Claude Code (`/tmp/claude-*`, wo das Scratchpad jeder Sitzung liegt). Ein Ordner, den es noch nicht gibt, ist in Ordnung. Er bittet um Bestätigung bei einem Ordner, aus dem dein System Programme startet (`~/bin`, `~/.local/bin`, `~/.config/systemd`, `~/.config/autostart` oder einer darin).
- **Sitzungen starten.** Die Seite Ordner von `/new` und die Mini App listen den Stammordner und die Ordner direkt darin auf (keine versteckten und keinen Link, der hinausführt), ohne die Zeile „Standardordner“; eine neue Sitzung startet im Stammordner, außer du wählst einen davon. Neuer Ordner legt den Ordner in dem an, den du gewählt hast. Alles andere bekommt „Deine Rolle kann Sitzungen nur in deinem Ordner ~/... oder einem Ordner darin starten.“
- **Sitzungen außerhalb.** Fortsetzen, Neu starten, Ersetzen und der Moduswechsel einer Sitzung, deren Ordner außerhalb des Stammordners liegt, bekommen „Diese Sitzung arbeitet außerhalb deines Ordners ~/..., daher kann deine Rolle sie nicht fortsetzen, neu starten oder ihren Modus wechseln. Frag einen Admin.“ Nachrichten an eine solche laufende Sitzung werden weiter geschickt, und der Sicherheits-Hook hält die Aufgabe fest: Dort schreibt sie nur ins Scratchpad.
- **Woran der Hook eine Aufgabe bindet.** Die Aufgabe eines eingeschränkten Mitglieds schreibt nur in den Ordner der Sitzung (wenn er im Stammordner liegt) und ihr Scratchpad, liest, durchsucht mit `Grep` und `Glob` nichts in deinem Home-Ordner außerhalb des Stammordners (außer ihrem Scratchpad und ihrer eigenen Antwortdatei) und berührt nie den Stammordner eines anderen Chats, wo auch immer er liegt. Ordner außerhalb deines Home-Ordners (`/usr`, `/etc`, ...) bleiben lesbar, die geschützten Ordner bleiben abgelehnt, und `Bash` bleibt für `user` abgelehnt. Mischt eine Aufgabe zwei Mitglieder mit verschiedenen Stammordnern, gewinnt der engere; zwei, die nicht ineinander liegen, lassen nur den eigenen Ordner der Sitzung übrig.

Um `user` Bash zurückzugeben (nur für Personen, denen du eine Shell geben würdest), nenn es in `allow_tools` (das dann auch die Liste des Erlaubten dieser Rolle wird) oder ersetz ihr `deny_tools`:

```yaml
roles:
  user:
    # die eingebaute Liste ohne Bash
    deny_tools: [PowerShell, Monitor, REPL, Workflow, CronCreate,
                 RemoteTrigger, AppifactRepl, self_hosted_runner_spawn_local,
                 self_hosted_runner_requeue_session, SendMessage,
                 EnterWorktree, ExitWorktree]
```

`aipager doctor` warnt dann: `role user has Bash: its safety rules are best-effort, not a boundary`. Hat deine `policy.yaml` schon `deny_tools` für `user` gesetzt, bevor es diesen Standard gab, ersetzt deine Liste ihn weiterhin, diese Rolle hat also Bash, bis du es wieder hinzufügst; die Warnung des doctor sagt dir das. Siehe [Sicherheit → wie die Regeln im Teambetrieb gelten](security.md#team-mode-enforcement) für das, was diese Grenzen nicht abdecken.

## <a id="talking-to-the-bot-in-a-group"></a>Mit dem Bot in einer Gruppe sprechen

### <a id="what-the-bot-reads-in-a-group"></a>Was der Bot in einer Gruppe liest

In einer Gruppe bearbeitet aipager nur eine Nachricht, die Folgendes ist:

- **ein Befehl**: `/status`, `/x1 fix the tests`, oder dasselbe mit `@aipagerbot` nach dem Befehl (`/x1@aipagerbot`), was Telegram anhängt, wenn du ihn aus dem Menü wählst. Ein Befehl für einen anderen Bot (`/x1@otherbot`) wird ignoriert;
- **eine Antwort auf eine der Nachrichten des Bots**;
- **eine Nachricht, die den Bot erwähnt**: `@aipagerbot fix the tests`;
- **ein Tippen auf die Tastatur des Bots** in der Gruppe. In einer Gruppe beginnt jeder Button der Tastatur mit einem kleinen Quadrat (`▫️ Stop`, `▫️ Unterhaltung leeren`), und nur dieser markierte Text zählt als Tippen. Das bloße Wort zu tippen ist normale Unterhaltung: Ein Mitglied, das `Stop`, `Unterhaltung leeren`, `Opus` oder den Namen einer Sitzung tippt, ändert nichts. Um ein Wort der Tastatur absichtlich ohne die Tastatur zu nutzen, sprich den Bot an: `@aipagerbot Stop`, oder `Stop` als Antwort auf eine seiner Nachrichten.

Alles andere (Mitglieder, die miteinander reden, ein Foto oder eine Sprachnachricht, die keine Antwort an den Bot ist) bekommt weder eine Reaktion noch eine Antwort. Das gilt, ob der Bot Admin der Gruppe ist oder nicht. Bearbeitete Nachrichten werden überall ignoriert: Bearbeite eine Nachricht, und nichts passiert; schick sie stattdessen noch einmal.

Ein Foto, eine Datei oder eine Sprachnachricht funktionieren genauso: Schick sie als Antwort an den Bot, oder erwähn den Bot in der Bildunterschrift. Bei einem Album setz die Bildunterschrift auf das erste Element: Die Elemente des Albums, die nach dem mit der Bildunterschrift, der Erwähnung oder der Antwort ankommen, werden genommen, die davor nicht.

**Die Erwähnung wird entfernt.** `@aipagerbot fix the tests` kommt bei Claude als `fix the tests` an (Claude Code würde `@word` als Datei lesen). `@aipagerbot status` und `@aipagerbot x2` funktionieren wie das Tippen von `status` oder `x2` in einem privaten Chat, und `@aipagerbot` allein zeigt, wohin deine Nachrichten gehen. Ein Sitzungsbefehl nach der Erwähnung funktioniert (`@aipagerbot /x1 fix it`), einer der eigenen Befehle von aipager aber nicht: `@aipagerbot /stop` bekommt „Sende /stop allein, ohne den Bot zu erwähnen.“

**Nur Mitglieder.** Eine Person, die nicht Mitglied der Gruppe ist, bekommt eine Antwort („🚫 Du stehst in diesem Chat nicht auf der Liste der erlaubten Personen.“) mit ihrer Telegram-ID, die sie dir geben kann, und wird für `aipager config` notiert; danach ignoriert der Bot sie, bis aipager neu startet. Ein Mitglied mit `read_only` erfährt seine Rolle einmal (siehe [Rollen](#roles)).

**Anonyme Admins müssen mit ihrem eigenen Konto schreiben.** Ein Admin der Gruppe, der „Remain anonymous“ eingeschaltet hat, oder eine Nachricht, die als die Gruppe oder als ein Kanal gepostet wurde, sagt nicht, wer sie geschrieben hat: Telegram zeigt für alle anonymen Admins denselben Absender. aipager kann einer solchen Nachricht keine Rolle von irgendwem geben, es antwortet also „Schreib mit deinem eigenen Konto, um den Bot zu nutzen.“ (einmal pro Chat, bis aipager neu startet) und tut sonst nichts. Sie wird nie als wartende Person notiert, und der Assistent lehnt diese geteilte ID ab. Schalte „Remain anonymous“ für dich in den Admin-Einstellungen der Gruppe ab, oder schreib mit deinem eigenen Konto.

### <a id="talking-to-a-session-in-a-group"></a>Mit einer Sitzung in einer Gruppe sprechen

Antworte auf eine der Nachrichten einer Sitzung (ihre Antwort, ihre Karte), und deine Nachricht geht an diese Sitzung. Oder sende `/x1 deine Nachricht`, oder eine Bildunterschrift, die mit `/x1` beginnt.

**Jede Person hat ihre eigene aktuelle Sitzung**: die, zu der sie zuletzt gewechselt ist (`/x1`), der sie eine Nachricht geschickt hat, auf die sie geantwortet oder die sie gestartet hat. Die Antwort der Sitzung eines anderen Mitglieds ändert sie nicht, die Nachrichten anderer auch nicht. Eine Erwähnung, eine Sprachnachricht, eine Vorlage oder ein Befehl der Tastatur geht an deine eigene aktuelle Sitzung, und ein Befehl der Tastatur oder eine Vorlage nennt sie in der Antwort („🧹 /clear an x1 gesendet“). Ein bloßes `/stop`, `/mode`, `/kill`, `/restart`, `/rename`, `/delete` oder `/diff` wirkt nur auf deine eigene aktuelle Sitzung (mit der üblichen Bestätigung) und nur, wenn der Befehl für sie Sinn ergibt (arbeitend bei `/stop`, beendet bei `/delete`); sonst zeigt es immer die Auswahl, auch wenn nur eine Sitzung übrig ist, weil sie jemand anderem gehören kann. `/clearqueue` und `/now` wirken auf deine aktuelle Sitzung. `/kill x1` und die anderen Befehle mit Namen wirken wie bisher auf diese Sitzung.

- Ohne eigene aktuelle Sitzung und mit mehr als einer laufenden Sitzung fragt der Bot „Welche Sitzung? Antworte auf eine ihrer Nachrichten oder schreib /x1 und dahinter deine Nachricht.“ mit einem Button pro Sitzung und schickt nichts. Läuft nur eine Sitzung, geht die Nachricht dorthin.
- Ist deine eigene aktuelle Sitzung beendet, wird eine Nachricht nicht an die einzige noch laufende Sitzung geschickt (sie kann jemand anderem gehören): Der Bot antwortet „x1 ist beendet. Welche Sitzung?“ mit einem Button pro laufender Sitzung und, wenn x1 fortgesetzt werden kann, „▶️ x1 fortsetzen“, und schickt nichts.
- Ein Tippen auf den Button einer Sitzung bei einer der beiden Fragen schickt außerdem die Nachricht, um die es ging (einen Text, den Text einer Sprachnachricht oder eine Datei), an diese Sitzung, als hättest du sie dorthin geschickt, und die Frage wird zu „An x2 gesendet.“. Nur die Person, die die Nachricht geschickt hat, kann die Buttons nutzen, und nur zehn Minuten lang und bis aipager neu startet: Danach macht der Button die Sitzung nur zu deiner aktuellen („Diese Nachricht ist zu alt und wurde nicht gesendet; sende sie noch einmal.“). ▶️ fortsetzen holt x1 zurück, schickt aber nichts: Schick deine Nachricht noch einmal.
- Die angeheftete Leiste hat in einer Gruppe keine Zeile „Nachrichten gehen an“, weil das für jede Person anders ist.

**Karten, die auf eine Antwort warten, gehören einer Person.** Die Karte für eine neue Sitzung sagt „Antworte auf diese Nachricht mit einem Namen und, wenn du willst, womit es losgehen soll:“, die Frage zum Umbenennen „… Antworte auf diese Nachricht mit dem neuen Namen.“. In einer Gruppe zeigt jede, wem sie gehört („🆕 Neue Sitzung (@alice)“), nur diese Person kann sie beantworten, und zwei Personen können gleichzeitig Sitzungen starten oder umbenennen. Die Antwort eines anderen Mitglieds auf die Karte für eine neue Sitzung von jemandem bekommt „Diese Karte gehört @alice. Sende /new für deine eigene.“ und geht nirgendwohin. Eine Frage zum Umbenennen wartet nach 10 Minuten nicht mehr, oder sobald ihre Person etwas anderes tut. Die Karte „x1 ist bereit“, die Antwort auf `/x1`, `/start` und `/help` sagen, dass man auf die Nachricht einer Sitzung antworten oder den Bot erwähnen soll („Antworte auf eine Nachricht von x1 (oder erwähne @aipagerbot), um mit ihr zu sprechen.“), weil eine normale Nachricht den Bot nicht erreicht.

**Die Tastatur gehört jeder Person selbst.** Vorlagen, Befehle, Modell › und « Zurück ändern nur die Tastatur der Person, die darauf tippt: Der Bot antwortet als Antwort auf das Tippen dieser Person, und die Tastatur von niemand anderem bewegt sich. « Zurück geht von da aus eine Ebene hoch, wo du bist, egal was andere Mitglieder gerade ansehen. Die Haupttastatur, die `/start` schickt, gehört der ganzen Gruppe und bringt alle zurück zur Haupttastatur. Wenn eine Sitzung der Gruppe startet oder endet, schickt aipager der Gruppe diese Haupttastatur noch einmal, einmal, damit ihre Sitzungsbuttons aktuell bleiben. Nach einem Update von einer Version ohne die markierten Buttons kann ein Tippen auf einen Button der alten Tastatur ignoriert werden: Sende einmal `/start`, um der Gruppe die neue Tastatur zu geben. Die Tastatur eines privaten Chats hat keine Markierung und funktioniert wie bisher.

### <a id="the-mini-app"></a>Die Mini App

`/app` öffnet die Mini App nur in einem privaten Chat. In einer Gruppe steht es nicht im Menü `/`; dort getippt, sagt es einem Mitglied, dessen eigener Chat mit dem Bot eingerichtet ist, „📱 Die Mini App funktioniert nur im privaten Chat: Schreib dem Bot privat und sende dort /app.“, und allen anderen „Die Mini App öffnet sich aus deinem privaten Chat mit dem Bot. Bitte den Besitzer des Bots, dich hinzuzufügen.“

Die Mini App zeigt einen Chat auf einmal. Wer in mehr als einem Chat ist (zum Beispiel im eigenen privaten Chat und einer Gruppe), bekommt oben eine Zeile mit Chatnamen, um zwischen ihnen zu wechseln; sie öffnet sich im eigenen privaten Chat der Person, sonst im ersten Chat, in dem sie steht, und merkt sich die letzte Wahl auf diesem Handy. Alles darin (die Sitzungen, ihre Buttons, die Einstellungen und die Hinweise, die sie schreibt) gilt für den gewählten Chat, mit der Rolle, die die Person in diesem Chat hat. Mit nur einem Chat wird nichts Zusätzliches gezeigt.

## <a id="who-a-message-runs-as"></a>Mit wessen Rechten eine Nachricht läuft

Jede Nachricht läuft mit **den Rechten der Person, die sie geschickt hat**, in dem Chat, aus dem sie geschickt wurde. Wer in der Gruppe `user` und im eigenen privaten Chat `owner` ist, schickt, tippt und führt Befehle in der Gruppe als `user` aus, egal in welcher Reihenfolge die Chats in `aipager.yaml` stehen.

- Jede Telegram-Nachricht bringt die Identität und die Regeln ihres Absenders in die Sitzung (siehe [Sicherheit → wie die Regeln im Teambetrieb gelten](security.md#team-mode-enforcement)). Claude sieht, wer jede Nachricht geschickt hat (`[via Telegram · @bob · role:user]` in einer Gruppe), nie die Person, die die Sitzung vorher gesteuert hat. Wer eine Nachricht schickt, steuert danach die Sitzung, ob getippt, mit `/x1`, einer Vorlage oder einem Befehl der Tastatur.
- **Zurückgehaltene Nachrichten behalten ihren Absender.** Solange die Aufgabe einer anderen Person läuft (auch ein Hintergrund-Agent, den sie gestartet hat), solange die Nachricht von jemand anderem noch auf das Abholen wartet oder solange eine Berechtigungsanfrage oder Frage offen ist, wird eine neue Nachricht zurückgehalten (👀) und geschickt, wenn das vorbei ist, als eigene Aufgabe, **mit den Rechten ihres eigenen Absenders**. Eine im Terminal getippte Aufgabe zählt als die des Besitzers: Nur die Nachricht eines Besitzers (oder, in einem privaten Chat, die seines eigenen Mitglieds) tritt ihr bei. Deine eigenen Nachrichten warten nie auf deine eigene Aufgabe. Wird die Aufgabe, auf die sie warten, gestoppt oder angehalten, weil ein Tool blockiert wurde, werden die zurückgehaltenen Nachrichten verworfen (🤷): Schick sie noch einmal.
- **Das Strengste gewinnt.** Landen Nachrichten verschiedener Personen trotzdem in einer Aufgabe, läuft die Aufgabe mit der strengsten Kombination aller Beteiligten. Eine Nachricht, die einer laufenden Aufgabe beitritt, macht sie nie weiter: Der Rest der Aufgabe behält die strengsten Regeln aller, die beigetreten sind.
- **Slash-Befehle eingeschränkter Rollen.** Eine Rolle, die die Rollenregeln nicht umgeht (`user`, `read_only`, Rollen, die du ohne `bypass_role_denies` definierst, und ein Absender, den aipager nicht kennt), darf nur die Slash-Befehle der Befehle-Tastatur (deiner, wenn `keyboard.json` sie ändert), `/compact` und Modellwechsel (`/model <name>`) senden. Alles andere, getippt, mit `/x1 /deliver ...` geschickt, als Bildunterschrift einer Datei, als erste Nachricht von `/new x1 /review ...` oder als Vorlage, bekommt „Dieser Befehl ist nur für Admins.“ und ein 🤷, und nichts erreicht Claude. Besitzer und Admins senden jeden.
- **Eingeschränkte Rollen bleiben aus dem Home-Ordner und von den Dateien mit Anmeldedaten fern.** Eine Aufgabe von `user` oder `read_only` schreibt nie in einen Sitzungsordner, der dein Home-Ordner, `/` oder darüber ist (nur in ihr Scratchpad), und liest nie die Dateien mit Anmeldedaten, die unter [Rollen](#roles) stehen.
- **Eingeschränkte Rollen bleiben in ihrem Stammordner.** Eine Aufgabe einer Rolle ohne `can_manage` liest nichts in deinem Home-Ordner außerhalb des [Stammordners](#root-folders) des Chats und schreibt nichts in einen Sitzungsordner außerhalb davon. Der Stammordner ist der des Chats, aus dem die Nachricht des Mitglieds kam, egal an welche Sitzung sie geht.
- **Ein entferntes Mitglied verliert seine Rechte sofort, nicht seine laufende Aufgabe.** Ein Absender, den aipager nicht mehr kennt (entfernt, während seine Nachricht wartete), läuft mit der eingeschränkten Untergrenze. Nach einem Neuladen im laufenden Betrieb werden seine zurückgehaltenen Nachrichten verworfen, und eine noch laufende Aufgabe von ihm läuft weiter, an die eingeschränkte Untergrenze gebunden, bis sie endet; sende `/stop`, um sie sofort zu beenden (siehe [Ohne Neustart neu laden](#live-reload)).
- **Eine Nachricht aus einer anderen Claude-Sitzung trägt die Rechte ihres Absenders.** Eine Nachricht, die eine Sitzung einer anderen schickt (`SendMessage` von Claude Code), läuft mit den Rechten der Aufgabe, die sie geschickt hat, so wie aipager sie geprüft hat, nie mit den früheren Rechten der empfangenden Sitzung. Eine Nachricht, die aipager nicht prüfen kann, läuft mit den strengsten Regeln und ohne Dateiänderungen oder Webzugriff (siehe [Sicherheit → Nachrichten zwischen Claude-Sitzungen](security.md#messages-between-claude-sessions)).
- **Ein blockiertes Tool stoppt die Aufgabe.** Versucht eine Aufgabe ein Tool, das ihre Regeln ablehnen, stoppt die Aufgabe sofort mit „🛑 x1 · Von einer Sicherheitsregel blockiert: ... (gestoppt)“, und jeder weitere Tool-Aufruf in dieser Aufgabe wird ebenfalls abgelehnt.

## <a id="who-can-tap-what"></a>Wer worauf tippen darf

Jeder Button prüft die Rolle der Person, die darauf tippt, in dem Chat, in dem der Button ist, im Moment des Tippens. Die Rolle der Person, die den Befehl geschickt hat, der den Button erzeugt hat, zählt nicht. (Manche Karten prüfen außerdem, wer sie angefordert hat: Bestätigungskarten und die Karten von `/new`, unten.)

- **Ansehen dürfen alle Mitglieder** (VIEW). Mitglieder mit `read_only` können in `/resume` blättern, die Ansicht Beendet von `/status` öffnen, das Menü ⋮ einer Sitzung, ihren Modus oder ihren Diff, `/settings` durchsehen und ein Menü schließen. Handeln können sie nicht: Jeder andere Button antwortet „Deine Rolle in diesem Chat erlaubt das nicht.“
- **Claude antworten braucht `can_approve`** (APPROVE): Erlauben, Immer erlauben, Ablehnen, die Optionen einer Frage, Senden, Weiter und das Antworten der angehefteten Leiste. Bei `read_only` ist es aus.
- **Auf eine Sitzung einwirken braucht `can_prompt`** (PROMPT): Stoppen, Erneut versuchen, Komprimieren, Jetzt senden, Beenden, Neu starten, Umbenennen, Löschen, Fortsetzen, der Moduswechsel, ✍️ sprechen, Alle entfernen, die eigenen Einstellungen einer Sitzung und die Karten von `/new`. Ein Button, den aipager nicht kennt, braucht das ebenfalls.
- **In den Vollzugriff-Modus wechseln braucht einen Admin** (MANAGE: `owner`, `admin` oder eine Rolle mit `can_manage`, dieselbe Regel wie bei `/mode full`): Ja, wechseln und Stoppen & wechseln auf einer Karte, die in den Vollzugriff-Modus wechselt, und Fortsetzen im Vollzugriff-Modus. Zwischen Fragen- und Auto-Modus wechseln dürfen alle, die auf die Sitzung einwirken dürfen. Eine Sitzung fortzusetzen, die im Vollzugriff-Modus war (getipptes `/resume x1`, die Auswahl, Fortsetzen auf der Namenskarte von `/new` oder die Mini App), bringt sie für alle anderen im Auto-Modus zurück, und die Antwort sagt das: „♻️ x1 läuft wieder, im Auto-Modus: Der Vollzugriff-Modus ist nur für Admins.“
- **In ihrem eigenen privaten Chat darf eine Person den Vollzugriff-Modus nutzen, egal welche Rolle sie hat** (auch ein `user`; nicht `read_only`, das keine Nachrichten schickt; eine eigene Rolle braucht `can_prompt` und `can_approve`, weil der Vollzugriff-Modus jede Berechtigungsanfrage von Claude Code beantwortet). Jeder Weg in den Vollzugriff-Modus oben funktioniert dort für sie: `/mode full`, die Moduskarte, `/new !x1`, die Karten von `/new`, `/resume` und die Mini App. Ihr eigener Chat ist der einzige, in dem das gilt: In einer Gruppe und im privaten Chat von jemand anderem braucht der Vollzugriff-Modus weiterhin einen Admin. Ihre neuen Sitzungen starten im Auto-Modus wie die aller anderen, bis sie unter `/settings` → Neue Sitzungen den Vollzugriff-Modus wählt. Sonst öffnet sich nichts von dem, was ein Admin tut (`/update`, der Button Installieren der Sprachfunktion und die `/settings` einer Gruppe bleiben nur für Admins). Den Vollzugriff-Modus so freizugeben ist sicher: Er startet Claude Code mit `--dangerously-skip-permissions`, was nur die eigenen Berechtigungsanfragen von Claude Code überspringt. Der Hook PreToolUse von aipager läuft weiterhin bei jedem Tool-Aufruf und setzt die Regeln der Rolle des Mitglieds durch (die Sicherheitsuntergrenze, die Beschränkung auf den Ordner, die Ablehnungsregeln, die Dateien mit Anmeldedaten), genau wie im Fragen- und im Auto-Modus.
- **Installieren braucht einen Admin** (UPDATE): die Buttons Installieren und Neu starten der Sprachfunktion, wie die von `/update`.
- **Die `/settings` einer Gruppe zu ändern braucht einen Admin.** Ansehen dürfen alle.
- **Eine Bestätigungskarte gehört der Person, die sie angefordert hat.** In einer Gruppe antworten die Bestätigungskarten für Beenden (`/kill`, ⋮ Sitzung beenden), Neu starten, Löschen und den Moduswechsel, die Sitzungsauswahlen, die `/kill`, `/restart`, `/delete`, `/mode`, `/stop`, `/rename` und `/diff` zeigen, und die Karte „x1 fortsetzen im:“, die ein Tippen auf Fortsetzen öffnet (ihre Modus-Buttons), nur der Person, die den Befehl geschickt oder auf den Button getippt hat, der sie geöffnet hat. Das Tippen jeder anderen Person, Abbrechen eingeschlossen, bekommt „Das ist die Karte von @alice. Sende /kill x2 für deine eigene.“ (mit dem Befehl dieser Karte) und ändert nichts. Die Karte von `/mode` selbst (in welchem Modus eine Sitzung ist) gehört allen: Ein Wechsel, der darauf getippt wird, öffnet eine Bestätigung, die der Person gehört, die getippt hat. Die Karten von `/new` antworten nur der Person, die `/new` geschickt hat. Wem eine Karte gehört, wird gespeichert, bis sie benutzt oder abgebrochen wird, auch über einen Neustart von aipager hinweg (die neuesten 4.096 Karten). Die Namenskarte von `/new` und die Namensfrage von `/rename` sind die Ausnahme: Sie laufen ohnehin nach 10 Minuten ab, nach einem Neustart sende also den Befehl noch einmal.
- **Ein Button funktioniert nur im Chat seiner eigenen Sitzung.** Woanders angetippt, antwortet er „Dieser Button gehört zu einem anderen Chat.“ und tut nichts, und Fortsetzen setzt immer die Sitzung fort, die der Button nennt, nie eine gleichnamige Sitzung eines anderen Chats.
- **Alte Buttons ändern nichts.** Ein Button, der von einer früheren Aufgabe übrig ist, oder aus der Zeit, bevor die Sitzung endete und zurückkam, wirkt nicht auf das, was jetzt läuft; er sagt das (siehe [Befehle → alte Buttons](commands.md#stale-buttons)).

## <a id="what-everyone-sees"></a>Was alle sehen

**Antworten an Claude.** Jedes Erlauben, Ablehnen und jede Antwort wird im Chat unter der Karte der Sitzung veröffentlicht, mit der Person, die es getan hat:

```
✅ x1 · Erlaubt · @alice · Bash: ls -la /tmp
🚫 x1 · Abgelehnt · @bob · WebFetch: https://example.com
✓ x1 · Beantwortet · @carol · Which database? → Postgres
```

**Wer eine Sitzung geändert hat.** Ein Tippen auf einen Button sieht nur die Person, die tippt, in einer Gruppe nennt deshalb jedes Ergebnis, das eine Sitzung ändert, die Person, die es getan hat:

```
⚠️ x1 · @bob hat sie gestoppt
⏹ @bob hat x2 beendet
🔄 @alice hat [x1] neu gestartet.
🗑️ @carol hat [x3] gelöscht.
⚙️ @alice hat die Sitzung in den 🤖 Auto-Modus gewechselt.
@alice hat das geändert.          (unter /settings)
```

Die Karte „x1 ist bereit“ nennt, wer ihren Modus oder ihr Modell gewechselt hat („@bob hat das Modell auf Opus gewechselt.“). In einem privaten Chat nennen diese Ergebniszeilen niemanden. Eine Änderung in der Mini App wird im Chat als „... über die Mini App“ veröffentlicht (ein Stoppen dort nennt außerdem auf der Karte der Sitzung, wer).

**Ein Tool, das eine Regel blockiert.** Eine Aufgabe stoppt mit „🛑 x1 · Von einer Sicherheitsregel blockiert: Write is in this role's deny_tools (gestoppt)“. Bittet Claude Code um Erlaubnis für ein Tool, das die Person, deren Nachricht die laufende Aufgabe gestartet hat, nicht nutzen darf, antwortet der Bot selbst mit Ablehnen, zeigt keine Anfrage und veröffentlicht `⛔ x1 · Edit blockiert für @bob (Rolle user)` mit der Zusammenfassung des Aufrufs darunter, wobei diese Person genannt wird. Eine Aufgabe, die die Nachrichten mehrerer Personen gestartet haben, wird blockiert, wenn eine von ihnen das Tool nicht nutzen darf, und nennt diese. Eine im Terminal getippte Aufgabe gehört dir und wird nie durch die Regeln eines Mitglieds blockiert, außer eine Telegram-Nachricht ist ihr beigetreten: Dann wird sie gegen die Regeln der Person geprüft, die die letzte Nachricht der Sitzung geschickt hat (die eigene Liste der Gruppe, wenn das nicht bekannt ist). Weiß aipager nicht, wessen Aufgabe es ist (sie lief schon, als aipager neu gestartet wurde), nimmt es die Regeln der Person, die die letzte Nachricht der Sitzung geschickt hat, und die eigene Liste der Gruppe, wenn auch das nicht bekannt ist.

**Zustand der Nachrichten.** 👀 an einer Nachricht heißt, sie wurde an die Sitzung geschickt (oder wird zurückgehalten, bis das geht); 👍 heißt, Claude hat sie abgeholt; 🤷 heißt, sie wurde verworfen, bevor Claude sie abgeholt hat. `/whoami` zeigt deinen Namen, deine Rolle und deine Regeln in diesem Chat.

**Die angeheftete Statusleiste.** Die Gruppe bekommt ihre eigene angeheftete Statusleiste (siehe [Befehle → die angeheftete Statusleiste](commands.md#the-pinned-status-bar)), die nur die Sitzungen dieser Gruppe auflistet, wenn der Bot ein Admin mit dem Recht zum Anheften ist. Ohne dieses Recht wird die Leiste für die Gruppe übersprungen: Die Nachricht wird gelöscht, statt unangeheftet im Verlauf zu bleiben.

**Audit auf der Festplatte.** `~/.claude/aipager-audit.jsonl` hält jede Entscheidung mit `user_id`, `username` und dem Chat fest, damit du später nachvollziehen kannst, was jede Person getan hat. Es wird immer auf Englisch geschrieben, egal welche Sprache der Chat nutzt.

**Sprache.** Eine Gruppe hat eine Sprache für die eigenen Nachrichten, Karten und Tastaturen von aipager, die ein Admin mit `/language` festlegt; bis ein Admin eine wählt, ist sie Englisch. Sie folgt nie der Telegram-App von irgendwem. Was nur eine Person sieht, der kurze Hinweis nach einem Tippen, ist in der eigenen Sprache dieser Person: ihre Wahl aus ihrem privaten Chat mit dem Bot, sonst die Sprache ihrer Telegram-App, wenn die Person nach dieser Version dazukam, sonst die Sprache der Gruppe. Nur die Texte von aipager ändern sich; Claude antwortet in der Sprache, in der jede Person ihm schreibt. Siehe [Sprachen](commands.md#languages).

## <a id="where-messages-go"></a>Wohin Nachrichten gehen

- **Jede Sitzung gehört zu einem Chat, und alles von ihr geht dorthin**: ihre Karten, Antworten, Berechtigungsanfragen, Dateien und Hinweise. Eine Nachricht in einem Chat geht nie an eine Sitzung eines anderen Chats, und der Name einer Sitzung funktioniert nur in ihrem eigenen Chat: Den Sitzungsnamen eines anderen Chats zu tippen antwortet „Keine Sitzung mit diesem Namen“. Eine Sitzung, die gestartet wurde, als aipager ohne eingerichtete Chats lief, bekommt ihren Chat, sobald `aipager config` sie hinzufügt; solange eine Sitzung keinen Chat hat, listet kein Chat sie auf, und keiner kann auf sie einwirken.
- **Im Terminal gestartete Sitzungen** (`aipager session <name>`) gehören zum privaten Chat des Besitzers: dem privaten Chat, dessen Mitglied die Rolle `owner` hat (eine Rolle mit `bypass_safety`). Gibt es mehrere private Chats und keiner gehört dem Besitzer, ist es der erste private Chat der Liste; gibt es gar keinen privaten Chat, der erste Chat. Bei einer Installation mit privatem Chat gehen sie nie in eine Gruppe.
- **Eine Umstellung auf eine Supergruppe wird automatisch nachvollzogen** (siehe [Wenn Telegram die Gruppe umstellt](#when-telegram-upgrades-the-group)).

## <a id="aipageryaml-schema-team-parts"></a>Aufbau von `aipager.yaml` (Teile für das Team)

```yaml
schema_version: 3
bot_token: "…"

scopes:
  - kind: dm
    chat_id: 12345       # dein eigener privater Chat: seine ID ist deine Benutzer-ID
    label: owner DM
    members:
      - id: 12345
        label: alice
        role: owner
  - kind: group
    chat_id: -100123456789
    label: team
    deny_tools: [Write, Edit]   # optional: die Antwort auf „Block file edits“
    root: /home/alice/aipager-users/group-team   # der Stammordner der Mitglieder
    members:
      - id: 12345        # Telegram-Benutzer-ID (KEIN Name, KEINE Chat-ID)
        label: alice     # wie die Person im Chat erscheint (@alice)
        role: owner
      - id: 67890
        label: bob
        role: user
        deny_tools: [WebFetch]  # optional, pro Mitglied
      - id: 11111
        label: charlie
        role: read_only
```

Ein privater Chat und Gruppen-Chats bestehen nebeneinander: Der Bot ist für jeden aufgelisteten Chat zuständig, und die Sitzungen jedes Chats bleiben getrennt. `aipager config` schreibt diese Datei; du musst sie nie bearbeiten.

## <a id="how-rules-work"></a>Wie die Regeln funktionieren

`~/.config/aipager/policy.yaml` legt die Regeln jeder Rolle fest. Prüf sie mit `aipager policy validate`. Die Felder, die jede Rolle annimmt (zusätzlich zu denen unter [Eigene Rollen](#custom-roles)):

- `deny_tools`: Namen von Tools, die ohne Rückfrage abgelehnt werden (zum Beispiel `Write`, `Edit`, `Bash`, `WebFetch`).
- `allow_tools`: wenn nicht leer, eine Liste des Erlaubten: Alles andere wird für diese Rolle abgelehnt.
- `deny_bash_patterns`: Muster, die mit den Eingaben von `Bash` verglichen werden.
- `deny_paths_no_access` / `deny_paths_no_write`: Regeln für Pfade. Bei `user`, `read_only` und jeder Rolle, die du ohne `bypass_role_denies` definierst, beginnt `deny_paths_no_access` mit den Dateien mit Anmeldedaten, die oben bei **user** stehen; es zu setzen ersetzt diese Liste, kopier also die, die du behalten willst, in deine (eine laufende Aufgabe, der eine andere Nachricht beitritt, ist bis zu ihrem Ende wieder an die Liste gebunden). Eine Regel ohne `/` oder `~` am Anfang (`**/.env`) passt überall; solange eine da ist, wird also jedes `Grep`/`Glob` einer eingeschränkten Rolle abgelehnt (jede Suche könnte eine solche Datei lesen), was diese Aufgabe anhält.

Die `deny_tools` eines Chats und die eines Mitglieds in `aipager.yaml` kommen zu denen der Rolle dazu, und die `allow_tools` eines Mitglieds kommen zur Liste des Erlaubten der Rolle dazu. All das gilt nur für Rollen ohne `bypass_role_denies`: Einen Besitzer oder Admin betrifft es nicht.

Dateien in `~/.config/aipager/policy.d/*.yaml` werden nach `policy.yaml` gelesen, in der Reihenfolge ihrer Namen, im selben Format: Ein Feld einer Rolle in einer späteren Datei ersetzt den früheren Wert, und eine Liste `safety:` ergänzt nur. `aipager policy validate` prüft sie alle.

Unter allen Rollen liegt eine **Sicherheitsuntergrenze** (geschützte Pfade und Befehlsmuster), die nur `owner` umgeht: Auch Admins sind daran gebunden. Sie ist die eingebaute Liste von aipager plus der Abschnitt `safety:` von `policy.yaml`, der nur ergänzen kann:

```yaml
safety:
  deny_paths_no_access: [/srv/secrets/**]   # weder lesen noch schreiben noch suchen
  deny_paths_no_write: [~/notes/**]         # lesen ja, schreiben nein
  deny_bash_patterns: ['\bcurl\b']          # Bash-Befehle, die blockiert werden
```

Jede Aufgabe jeder Rolle außer `owner` bekommt sie ganz, ebenso die Aufgaben, die aipager niemandem zuordnen kann; `aipager doctor --safety-check` listet auf, was gerade gilt. Nach einem Neuladen im laufenden Betrieb gilt eine Änderung daran für Nachrichten, die nach dem Neuladen geschickt werden; eine schon laufende Aufgabe oder eine Nachricht, die schon in der Warteschlange wartet, behält die alte Liste (stoppe diese Aufgabe, um die Änderung sofort anzuwenden). Ein Pfad in `deny_paths_no_access` dort ohne `/` oder `~` am Anfang passt überall; solange einer da ist, wird also jedes `Grep`/`Glob` jeder Rolle außer `owner` abgelehnt (`aipager doctor` warnt davor). Die geschützten Pfade gelten für die Datei-Tools von Claude Code (`Read`, `Write`, `Edit`, `MultiEdit`, `NotebookEdit`, `LSP`, `Grep`, `Glob`); das `Grep`/`Glob` einer eingeschränkten Rolle muss außerdem im Ordner der Sitzung bleiben (eine Suche nach `aipager/**` in `~/.config` wird abgelehnt). `WebFetch` wird nicht nach Pfad geprüft; `SendUserFile` (Dateien, die Claude in den Chat schickt) wird geprüft wie ein Read, und ein Mitglied schickt nur aus dem Stammordner des Chats ([Sicherheit](security.md#files-claude-sends)). Eine Telegram-Aufgabe, die aipager keinem Absender zuordnen kann, bekommt die Untergrenze, die mindestens so streng ist wie `user`. Erneut versuchen läuft mit der Rolle der Person, die darauf tippt, wenn sie die wiederholte Nachricht geschickt hat, sonst mit der Untergrenze. Die Befehlsmuster zählen nur für eine Rolle mit Bash, und dort sind sie ein Filter, keine Mauer; siehe [Sicherheitsmodell → wie die Regeln im Teambetrieb gelten](security.md#team-mode-enforcement).

## <a id="privacy-considerations"></a>Fragen zur Privatsphäre

- Der Bot hört nur auf **die eingerichteten Chats**. Den Bot zu einer anderen Gruppe hinzuzufügen, macht ihn dort nicht aktiv: Er antwortet dort nie. Er notiert, wer ihn dort anspricht (die ID, den Benutzernamen und den Namen der Person sowie die ID und den Titel der Gruppe), in `~/.claude/aipager-pending-users.json`, damit `aipager config` diese Gruppe hinzufügen kann. Nur du kannst diese Datei lesen, und sie behält die neuesten 200 Personen der letzten 30 Tage.
- Mitglieder mit `read_only` **können** die Nachrichten, die Antworten und die Eingaben der Tools **lesen**. Sie können nicht handeln, sehen aber alles in der Gruppe. Muss eine Arbeit vor jemandem verborgen bleiben, sollte diese Person nicht in der Gruppe sein.
- Bei einer Installation mit privatem Chat tauchen deine Terminal-Sitzungen und die deines privaten Chats nie in einer Gruppe auf (siehe [Wohin Nachrichten gehen](#where-messages-go)). Eine Terminal-Sitzung, die ein älteres aipager schon in die Gruppe gebracht hat, bleibt bei der Gruppe: Beende sie, lösch sie aus den beendeten Sitzungen der Gruppe und starte sie neu.
- `aipager.yaml` hat die Rechte 0600 (nur der Eigentümer). aipager setzt die Rechte von `policy.yaml` nicht: Halte sie selbst nur für den Eigentümer lesbar (`chmod 600 ~/.config/aipager/policy.yaml`).
- Das Audit-Log (`~/.claude/aipager-audit.jsonl`) hat keine eigenen Rechte: Es ist so privat wie dein Ordner `~/.claude`.

## <a id="revoking-a-user"></a>Einer Person den Zugang entziehen

1. Führ `aipager config` → Edit a member → den Chat → die Person → Remove member aus. Um ihr das Recht zum Schicken von Nachrichten zu nehmen, sie aber weiter lesen zu lassen, nimm Set role und wähl `read_only`. (Um sie später wieder hinzuzufügen: Edit a scope → die Gruppe → Add a member.) Für den privaten Chat einer Person beende zuerst die Sitzungen dieses Chats, dann Edit a scope → ihr privater Chat → Remove this scope.
2. Der Assistent lädt aipager im laufenden Betrieb neu; er sagt "Scopes reloaded live". Sofort werden ihre zurückgehaltenen Nachrichten verworfen (🤷 und eine Antwort „Nicht an Claude gesendet“), und eine noch laufende Aufgabe von ihr wird bis zu ihrem Ende an die eingeschränkte Untergrenze gebunden. Sende `/stop`, um diese Aufgabe sofort zu beenden.
3. Entfern sie auch aus der Telegram-Gruppe. Solange sie dort bleibt, kann sie weiter alles lesen, was gepostet wird: Karten, Antworten, Eingaben der Tools und Berechtigungsanfragen.

Prüf, was der Assistent nach Schritt 1 sagt. Sagt er, du sollst aipager neu starten, hat er es nicht erreicht: Starte es neu (`aipager service stop`, dann `aipager service start`), denn bis dahin bleibt die vorige Mitgliederliste im Speicher. Sagt er "Not applied", beheb das Problem, das er nennt: aipager behält bis dahin die vorige Konfiguration. Nach einer Änderung an `aipager.yaml` von Hand schick das Neuladen selbst (siehe [Ohne Neustart neu laden](#live-reload)).

## <a id="related-docs"></a>Verwandte Seiten

- [Architektur](architecture.md): wie der Prozess aufgebaut ist.
- [Befehle des Bots](commands.md): Referenz der Oberfläche.
- [Sicherheitsmodell](security.md): Vertrauensgrenze, Liste der Bedrohungen.
- [Fehlerbehebung](troubleshooting.md): Referenz zu `aipager doctor`.
