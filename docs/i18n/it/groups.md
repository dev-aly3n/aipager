# Uso in team e gruppi

<!-- i18n-source: docs/groups.md @ daaa753 -->

> Questa pagina è tradotta dalla pagina in inglese; se le due versioni non coincidono, vale quella in inglese. ([English](../../groups.md))

Di base aipager è un bot personale: tu e il bot in una chat privata, nessun altro. L'**uso in team** lo apre a più persone: un gruppo di Telegram in cui più sviluppatori guidano le sessioni, le chat private di altre persone con il bot, o entrambe le cose. Ogni chat ha i suoi membri e ogni membro ha un ruolo che decide cosa può fare lì: inviare messaggi, rispondere alle domande e alle richieste di permesso di Claude, gestire le sessioni o solo guardare.

L'uso in team è facoltativo. Un'installazione con solo la tua chat privata funziona esattamente come prima.

## <a id="decide-carefully"></a>Decidi con attenzione

Aggiungere un utente di Telegram a una chat gli dà **la possibilità di eseguire codice sul computer su cui gira aipager**. A seconda del ruolo, può:

- inviare messaggi che Claude trasforma in comandi shell, modifiche ai file e connessioni di rete;
- rispondere alle richieste di permesso (Consenti / Nega). Il tuo `~/.claude/settings.json` decide ancora per quali strumenti Claude chiede, ma chiunque abbia un ruolo che può approvare può toccare Consenti;
- avviare, fermare e terminare sessioni, e passare dall'una all'altra.

Tratta l'elenco dei membri come l'accesso SSH al computer. Il log di audit ([`~/.claude/aipager-audit.jsonl`](security.md#audit-log)) registra chi ha fatto cosa, ma solo dopo che è successo.

## <a id="setup"></a>Configurazione

Tutto si configura con `aipager config`. Non devi mai modificare un file a mano.

**Primo avvio** (ancora nessuna configurazione). La procedura guidata chiede quattro passaggi:

1. il token del bot;
2. la tua chat privata con il bot (rilevata in automatico, o incollata);
3. se il tuo account è `owner` (predefinito sì; rispondendo no diventi `admin`, un ruolo che per te mantiene la base di sicurezza);
4. controlla le dipendenze e installa gli hook di Claude Code.

Scrive la tua chat privata come prima chat. Non chiede una modalità predefinita: il suo riepilogo dice "New sessions start in Auto mode; owners and admins can choose Full access in Telegram under /settings → New sessions." (le nuove sessioni partono in modalità «Automatico»; proprietari e admin possono scegliere la modalità «Completo» in Telegram, in /settings → Nuove sessioni). Poi chiede "Connected. Add a team group or other people now?" con Add a group, Add a person (DM) e I'm done.

**Gli avvii successivi** aprono il menu di modifica. Mostra le chat che hai e offre:

- **Add a group scope**: un nuovo gruppo, con i suoi membri (vedi sotto).
- **Add a DM scope**: la chat privata di un'altra persona con il bot. Una chat privata ha un solo membro; scegli il suo ruolo (predefinito `user`; scegli `owner` solo per un'installazione usata da una persona sola, come il container di un amico). Per un ruolo senza `can_manage` (come `user`), poi chiede la sua [cartella radice](#root-folders), già compilata con il valore predefinito (`~/aipager-users/<label>`). Per una persona che ha già una chat privata configurata, la procedura guidata lo dice e non cambia niente: usa Edit a member per cambiarne il ruolo.
- **Edit a scope**: per un gruppo, **Add a member**; per ogni chat, Rename, Edit scope deny_tools, **Set the members' root folder** (vedi [Cartelle radice](#root-folders)) e Remove this scope (l'ultima chat non si può togliere). Termina le sessioni di una chat prima di toglierla: quelle in esecuzione continuano a pubblicarci, e nessuna chat le raggiunge più da Telegram (dal computer, `aipager session ls` e `aipager session kill <name>` funzionano ancora).
- **Edit a member**: Set role, Edit member deny_tools, Remove member (non in una chat privata, e non per l'ultimo membro di un gruppo). Per un proprietario o un admin (o un tuo ruolo con `bypass_role_denies`), Edit member deny_tools dice solo che gli strumenti bloccati non valgono per quel ruolo.
- Test bot reachability, View policy, Re-install Claude Code hooks e Refresh bot token.

### <a id="adding-a-group"></a>Aggiungere un gruppo

1. Aggiungi il bot al gruppo, poi scegli Add a group scope (o Add a group al primo avvio).
2. **L'ID del gruppo.** Scegli il rilevamento automatico e poi invia `/start` nel gruppo, oppure incolla l'ID (un numero negativo). La procedura guidata invia un messaggio di prova e chiede se è arrivato. Un gruppo già configurato si ferma qui con "This group is already set up. Use Edit a scope → Add a member." e non cambia niente.
3. **Un'etichetta** per il gruppo (si vede nella procedura guidata e nella Mini App).
4. **Prima tu.** Quando nella tua chat privata hai il ruolo di proprietario, la procedura guidata propone di aggiungerti come primo membro del gruppo con il ruolo `owner` (predefinito sì; puoi saltarlo). Se il gruppo non ha né proprietario né admin, lì nessuno può usare la modalità «Completo», `/settings` o `/update`.
5. **Gli altri membri**, uno alla volta: come trovarli (rilevamento automatico, oppure incolla un ID o un `@handle`), un'etichetta (si vede come `@label` in chat) e un ruolo (predefinito `user`). L'elenco dei ruoli comprende `owner`, con un avviso: un proprietario controlla completamente questo computer, compresa la configurazione di aipager, il token del bot e la credenziale di Claude. Quel ruolo tienilo solo per te. Dai `admin` a chi deve poter usare la modalità «Completo», `/settings` e `/update` nel gruppo. (Nella propria chat privata con il bot, chiunque possa inviare messaggi e rispondere a Claude può usare la modalità «Completo» per le proprie sessioni, qualunque sia il ruolo.)
6. **Una regola facoltativa:** "Block file edits (Write, Edit) for `user` members in this group? Admins and the owner are not affected." (Bloccare le modifiche ai file (Write, Edit) per i membri `user` di questo gruppo? Admin e proprietario non sono toccati.) Il predefinito è no: un `user` scrive già solo dentro la cartella di progetto della sessione. Con sì, ogni Write ed Edit nell'attività di un membro il cui ruolo non può saltare le regole del ruolo (`user`, `read_only` e i ruoli che definisci tu senza `bypass_role_denies`) viene negato senza chiedere, e l'attività si ferma. Quella parte del lavoro deve inviarla un admin o il proprietario.
7. **La cartella radice dei membri**, già compilata con `~/aipager-users/group-<label>`: dove i membri `user` e `read_only` del gruppo avviano le sessioni e lavorano (vedi [Cartelle radice](#root-folders)).

Per aggiungere qualcuno a un gruppo già configurato, usa Edit a scope → quel gruppo → Add a member. Chi è già nel gruppo mantiene posto e ruolo.

### <a id="finding-ids-auto-detect"></a>Trovare gli ID (rilevamento automatico)

Rilevare un membro in automatico prende il messaggio più recente di qualcuno non ancora aggiunto e chiede "Is this the person to add?" (È questa la persona da aggiungere?) prima di usarlo (un no fa cercare oltre quella persona). Propone lo username di Telegram della persona come etichetta. In un gruppo la persona deve chiamare il bot: menzionarlo o inviare `/start` (vedi [cosa legge il bot](#what-the-bot-reads-in-a-group)). La procedura guidata dice dove guarda:

- **"Watching through the running daemon"** (tramite l'aipager in esecuzione): mentre `aipager start` è in esecuzione, aipager riceve tutti i messaggi inviati al bot, quindi la procedura guidata legge quello che aipager ha registrato in `~/.claude/aipager-pending-users.json`. aipager registra chi chiama il bot in un gruppo che non serve ancora (l'ID, il titolo e il tipo del gruppo, più l'ID, lo username e il nome della persona) e chiunque non sia membro di un gruppo che serve. Non risponde mai in un gruppo che non serve. Un messaggio privato di qualcuno non ancora configurato riceve una risposta e non viene registrato, quindi per aggiungere la chat privata di una persona mentre aipager è in esecuzione incolla il suo ID (o prima ferma aipager).
- **"Watching Telegram directly"** (direttamente da Telegram): quando aipager è fermo, la procedura guidata chiede lei stessa a Telegram i messaggi recenti del bot.

La tua chat privata al primo avvio si legge sempre da Telegram. Un messaggio di un admin anonimo del gruppo, o uno pubblicato a nome del gruppo o di un canale, non conta: il suo ID mittente (1087968824, 136817688 o 777000) è condiviso da tutti quelli che pubblicano così, quindi la procedura guidata lo rifiuta, scritto o rilevato che sia. Chiedi alla persona di scrivere con il proprio nome.

### <a id="where-the-config-lives"></a>Dove sta la configurazione

La procedura guidata scrive `~/.config/aipager/aipager.yaml` (permessi 0600): il token del bot, le chat che il bot serve e i membri di ogni chat con i loro ruoli. Cosa può fare ogni ruolo sta in `~/.config/aipager/policy.yaml`, che è tuo: la procedura guidata non lo scrive mai (vedi [Come funzionano le regole](#how-rules-work)). Le installazioni configurate con il vecchio formato `team.yaml` vengono convertite da sole, da aipager all'avvio o dalla procedura guidata all'avvio successivo.

### <a id="live-reload"></a>Ricaricare senza riavviare

Dopo ogni modifica a chat o membri fatta dal menu di modifica (aggiungere o togliere un gruppo o una chat privata, aggiungere o togliere un membro, impostare un ruolo, modificare `deny_tools`, rinominare una chat), e dopo un gruppo o una persona aggiunti alla fine del primo avvio o un gruppo a metà che riprendi (una ricarica per tutto quello che hai aggiunto lì), `aipager config` manda all'aipager in esecuzione un ordine di ricarica (**SIGUSR1**) e scrive "Scopes reloaded live (no daemon restart needed)". La ricarica rilegge `aipager.yaml` e `policy.yaml`. Se la configurazione sul disco verrebbe rifiutata, la procedura guidata scrive "Not applied" con il motivo e aipager tiene la configurazione vecchia finché non la correggi. Se la procedura guidata non è sicura di quale processo sia aipager, ti dice di riavviarlo.

Cosa applica subito una ricarica:

- chi può usare il bot in ogni chat, con quale ruolo e quali regole;
- un nuovo gruppo o una nuova chat privata comincia a ricevere messaggi e riceve il suo menu `/`; una chat tolta si ferma (le sue sessioni continuano a girare finché non le termini);
- un messaggio trattenuto (in attesa che una richiesta abbia risposta o che finisca l'attività di un'altra persona) di qualcuno che in quella chat non può più inviare viene scartato: riceve un 🤷 e il bot risponde «Non inviato a Claude: @bob non può più inviare messaggi qui.»;
- un'attività in corso di qualcuno tolto o che ha perso permessi mantiene solo il più restrittivo tra le regole vecchie e quelle nuove (per una persona tolta, la base limitata) per il resto di quell'attività. Un'attività scritta nel terminale non cambia, a meno che non vi si sia unito un messaggio di Telegram: aipager non registra di chi fosse, quindi un'attività così si restringe quando perde permessi un qualsiasi proprietario di quella chat (o, in una chat privata, il suo membro). Un'attività già in corso quando aipager si è riavviato (ancora al lavoro, o in attesa di una domanda o di una richiesta di permesso) si restringe in base a ogni membro di quella chat che ha perso permessi. Un aumento di permessi non allarga mai un'attività in corso: i nuovi permessi valgono dalla prossima attività della persona.

Solo il **token del bot** richiede un riavvio (`aipager config` lo dice dopo Refresh bot token). Quando nessun aipager è in esecuzione, non parte nessun ordine di ricarica: il prossimo `aipager start` legge la nuova configurazione.

Per ricaricare a mano (per esempio dopo aver modificato `policy.yaml`), manda il segnale solo al processo di aipager. Con il servizio in background:

```sh
pid=$(systemctl --user show -p MainPID --value aipager.service)
[ "${pid:-0}" -gt 0 ] && kill -USR1 "$pid"
```

(Il controllo conta: quando il servizio non è in esecuzione `MainPID` è `0`, e `kill -USR1 0` manderebbe il segnale ai processi della tua stessa shell.)

Per un aipager che hai avviato tu con `aipager start`, manda `kill -USR1 <pid>` a quel processo. Non mandare il segnale a ogni risultato di `pgrep -f 'aipager start'` (ci rientrano anche una shell o un container che fanno girare aipager, e SIGUSR1 ferma un processo che non lo gestisce), e non usare `systemctl --user kill` senza `--kill-whom=main` (le sessioni del servizio condividono il suo gruppo di processi e verrebbero fermate).

Se durante una ricarica `aipager.yaml` o `policy.yaml` è rovinato, o `aipager.yaml` manca, aipager scrive un avviso nel log e tiene in memoria la configurazione precedente. Un errore di battitura o un file spostato non possono chiuderti fuori né far entrare qualcuno: una ricarica non riporta mai all'uso personale un aipager che ha chat configurate (lo fa solo un riavvio).

### <a id="bot-admin-rights-and-privacy-mode"></a>Diritti di admin del bot e modalità privacy

**Rendi il bot admin del gruppo, con il permesso di fissare i messaggi.** Il bot ne ha bisogno per fissare la barra di stato del gruppo (vedi [Cosa vedono tutti](#what-everyone-sees)). Senza quel permesso il gruppo funziona ma non ha la barra di stato; se il permesso lo dai dopo, riavvia aipager perché compaia.

Un bot admin del gruppo riceve tutti i messaggi del gruppo, qualunque sia la sua modalità privacy in BotFather. Va bene così: aipager elabora solo i messaggi elencati in [cosa legge il bot](#what-the-bot-reads-in-a-group) e ignora il resto, quindi le chiacchiere del gruppo non arrivano mai a una sessione. Un bot che non è admin è in modalità privacy di predefinito (lasciala attiva), e allora Telegram gli passa solo alcuni messaggi del gruppo; se un tuo messaggio non arriva, rispondi a un messaggio del bot o usa un comando. L'impostazione della privacy non cambia niente di quello che aipager elabora.

Essere admin del gruppo in Telegram non dà a una persona nessun permesso in più in aipager. Chi può fare cosa dipende solo dal suo ruolo in `aipager.yaml`.

### <a id="when-telegram-upgrades-the-group"></a>Quando Telegram aggiorna il gruppo

Telegram trasforma un gruppo normale in supergroup quando rendi pubblico il gruppo, attivi gli argomenti, superi i 200 membri o cambi alcuni permessi di admin, e l'aggiornamento dà al gruppo un nuovo ID di chat. aipager lo segue da solo: quando vede l'aggiornamento (l'avviso di Telegram nel gruppo, o il primo invio al vecchio ID), sposta lo scope del gruppo in `aipager.yaml` sul nuovo ID, porta con sé le sessioni del gruppo, la destinazione dei messaggi e `/settings`, e pubblica una riga nel gruppo: «Telegram ha aggiornato questo gruppo. aipager si è spostato con lui: non devi fare nulla.» La barra di stato fissata riparte nel gruppo aggiornato. Il log di aipager ha un avviso con entrambi gli ID.

I nomi delle sessioni dentro aipager tengono il vecchio ID (non li vedi mai), e `aipager.yaml` tiene una breve nota `chat_migrations` dello spostamento, così una sessione avviata prima dell'aggiornamento appartiene ancora al gruppo aggiornato, anche dopo un riavvio. Il nome interno di una sessione con il vecchio ID (`/name__g<old id>`) funziona nel gruppo aggiornato e da nessun'altra parte. Se aipager era fermo durante l'aggiornamento, se ne accorge all'avvio successivo. Un'attività in corso al momento dell'aggiornamento arriva in fondo e la sua risposta raggiunge il gruppo aggiornato, ma la sua scheda di avanzamento non ricompare per il resto di quell'attività. Se il nuovo ID è già uno scope a sé, non si sposta niente e il log lo dice; togli uno dei due con `aipager config`. «Test bot reachability» in `aipager config` mostra il nuovo ID quando incontra un gruppo aggiornato.

A ogni avvio aipager controlla ogni gruppo una volta. Se Telegram risponde che il gruppo non si trova o che il bot non può usarlo (il gruppo è stato cancellato, il bot è stato tolto, oppure il gruppo è stato aggiornato mentre aipager era fermo e Telegram non ha detto verso quale ID), il log di aipager ha un avviso con il nome dello scope: controlla che il bot sia ancora nel gruppo e, se il gruppo è stato aggiornato, aggiungilo di nuovo in `aipager config` con Add a group scope (il gruppo prende il nuovo ID; aggiungi di nuovo i suoi membri), poi togli il vecchio scope con Edit a scope → Remove this scope (il vecchio ID non funziona più). aipager non pubblica niente e non continua a riprovare.

### <a id="forum-topics-are-not-supported"></a>Gli argomenti del forum non sono supportati

Usa un gruppo normale (senza argomenti) per ogni team. In un gruppo con gli argomenti attivi aipager funziona, ma non resta dentro un argomento:

- la risposta a un comando o a un messaggio (per esempio `/status`, o la risposta a «Quale sessione?») resta nell'argomento in cui è stato inviato;
- avvisi, schede di attività, risposte, richieste di permesso e la barra di stato fissata vengono pubblicati nel gruppo senza argomento, quindi possono finire in General;
- ogni persona ha una sessione attuale per tutto il gruppo, non una per argomento.

Attivare gli argomenti trasforma anche un gruppo normale in supergroup, e aipager lo segue (sopra).

## <a id="roles"></a>Ruoli

Ci sono quattro ruoli predefiniti (vedi `aipager/safety.py`). Il ruolo di una persona è quello **nella chat in cui sta agendo**. Nessuno può cambiare membri o ruoli da Telegram o dalla Mini App: lo fa solo `aipager config` sul computer, e questo lo lascia nelle mani del proprietario.

| | `owner` | `admin` | `user` | `read_only` |
|---|---|---|---|---|
| Inviare messaggi (`can_prompt`) | ✅ | ✅ | ✅ | ❌ |
| Rispondere a Claude: Consenti, Nega, domande (`can_approve`) | ✅ | ✅ | ✅ | ❌ |
| Gestire: modalità «Completo», `/settings` del gruppo, `/update`, installare la funzione vocale (`can_manage`) | ✅ | ✅ | ❌ (modalità «Completo»: ✅ nella propria chat privata) | ❌ |
| Cambiare membri e ruoli da Telegram o dalla Mini App | ❌ | ❌ | ❌ | ❌ |
| Saltare le regole di divieto del ruolo (`bypass_role_denies`) | ✅ | ✅ | ❌ | ❌ |
| Saltare la base di sicurezza (`bypass_safety`) | ✅ | ❌ | ❌ | ❌ |
| Bash e gli altri strumenti che eseguono codice | ✅ | ✅ (regole solo al meglio delle possibilità) | ❌ | ❌ |
| Tutti i comandi slash | ✅ | ✅ | solo quelli della tastiera «Comandi», `/compact` e i cambi di modello | come `user` |
| Dove avvia le sessioni (`/new`, Riprendi, Riavvia, cambio di modalità) | ovunque | ovunque | solo dentro la [cartella radice](#root-folders) della chat | come `user` |
| Dove scrive i file | ovunque | ovunque la base di sicurezza lo consenta | la cartella di progetto della sessione (quando è dentro la cartella radice) e lo scratchpad della sessione, mai la cartella home | come `user` |
| Letture nella tua cartella home | ovunque la base di sicurezza lo consenta | ovunque la base di sicurezza lo consenta | solo dentro la cartella radice (le altre cartelle del computer come prima, mai la cartella radice di un altro membro) | come `user` |
| File con le credenziali (`~/.ssh`, `~/.git-credentials`, ...) | leggibili | leggibili | mai (predefinito) | mai (predefinito) |
| `Grep`/`Glob` | ovunque | ovunque tranne le cartelle protette di aipager (nessuna ricerca in una cartella che lo è, che sta dentro una di esse o che ne contiene una) | la cartella di progetto della sessione e il suo scratchpad | come `user` |

- **owner**: controllo completo, base di sicurezza predefinita compresa. Dovrebbe essere esattamente una persona: chi gestisce il computer.
- **admin**: tutto quello che un proprietario può fare in Telegram e nella Mini App: passare le sessioni alla modalità «Completo» (e avviarle così), riprendere una sessione in modalità «Completo», cambiare `/settings` di un gruppo, eseguire `/update`, installare la funzione vocale, inviare qualsiasi comando slash. Gli admin saltano anche le regole di divieto del ruolo (i `deny_tools` dello scope e del membro non valgono per loro). Due cose restano del proprietario:
  - cambiare membri e ruoli: lo fa solo `aipager config` sul computer, e niente in Telegram o nella Mini App;
  - saltare la base di sicurezza (la configurazione di aipager e il token del bot, le credenziali di Claude Code, i file di controllo di aipager). `Grep` e `Glob` di un admin cercano ovunque altro, ma non in una cartella che sia una di queste, che stia dentro una di queste o che ne contenga una: `Grep` su `~`, `/` o `/tmp` (o senza cartella, da una sessione nella home) viene negato, e così un `Glob` il cui schema parte da una cartella così (come `/home/you/.config/aipager/*`). Cerca dentro una cartella di progetto. Sulle versioni di Claude Code senza lo strumento `Grep`, Claude cerca tramite Bash; lì viene negata anche una ricerca o un elenco ricorsivo di tutta la home o di `/` (`grep -rn x ~`, `rg x $HOME`, `find / -name x`), al meglio delle possibilità (non vede una ricerca senza cartella, o con `..`, eseguita dalla home o da sotto), mentre le ricerche nel progetto non vengono toccate, compresa una ricerca DI un percorso della home (`grep -rn "$HOME" src`) ([sicurezza](security.md#team-mode-enforcement)).

  Un admin ha comunque Bash, e con una shell gli schemi di comandi della base sono al meglio delle possibilità, non un confine: rendi admin qualcuno solo se gli daresti una shell sul computer.
- **user**: le regole di divieto valgono. Niente Bash, nessun altro strumento che esegue codice (PowerShell, Monitor, messaggi programmati, workflow; l'elenco è `safety.CODE_EXECUTION_TOOLS`), niente `SendMessage` verso altre sessioni, niente cambi di worktree. Scrive file solo dentro la cartella della sessione e lo scratchpad di Claude Code della sessione, e mai in `.claude/`, `.git/` o `.mcp.json` di quella cartella (ognuno esegue comandi). `Grep` e `Glob` cercano solo dentro quelle cartelle, con glob relativi semplici. Un `user` avvia, riprende, riavvia e cambia modalità alle sessioni solo dentro la [cartella radice](#root-folders) della chat, e non legge niente della tua cartella home fuori da lì. Una cartella di sessione che è la tua home, `/` o una cartella sopra, oppure che sta fuori dalla cartella radice (una sessione avviata prima delle cartelle radice, o da qualcun altro), non conta: lì un `user` scrive e cerca solo nello scratchpad. Le letture seguono le regole dei percorsi protetti, e i file con le tue credenziali non sono mai leggibili: `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config/gh`, `~/.git-credentials`, `~/.netrc`, `~/.docker/config.json`, `~/.kube`, `~/.config/gcloud`, `~/.azure`, `~/.password-store`, `~/.pgpass`, `~/.npmrc` e `~/.pypirc`. Comandi slash: solo quelli della tastiera «Comandi», `/compact` e i cambi di modello (vedi [Con quali permessi gira un messaggio](#who-a-message-runs-as)).
- **read_only**: osservatore. Vede tutto nel gruppo e può usare `/status`, `/diff`, `/whoami`, `/help`, `/start` e `/app`, guardare `/settings` e sfogliare un elenco di `/resume` aperto da qualcun altro. I suoi messaggi di testo, vocali e file non vengono inviati da nessuna parte. Il primo messaggio che invia in una chat riceve una risposta che dice che il suo ruolo è `read_only`; dopo, in quella chat il bot resta in silenzio con questa persona (niente risposte, niente reazioni) fino a un riavvio di aipager. Ha gli stessi limiti di strumenti e scritture di `user`, nel caso un suo messaggio arrivi a una sessione.

### <a id="custom-roles"></a>Ruoli personalizzati

`policy.yaml` può cambiare qualsiasi campo di un ruolo predefinito, o definire ruoli nuovi. Un campo che imposti sostituisce il valore predefinito. I campi:

- `can_prompt`, `can_approve`: inviare messaggi; rispondere a Claude.
- `can_manage`: usare la modalità «Completo», cambiare `/settings` di un gruppo, eseguire `/update` e installare la funzione vocale (predefiniti: true per `owner` e `admin`, false per `user` e `read_only`). Non permette mai a nessuno di cambiare membri o ruoli, e non salta mai la base di sicurezza.
- `bypass_role_denies`, `bypass_safety`: vedi la tabella.
- `deny_tools`, `allow_tools`, `deny_bash_patterns`, `deny_paths_no_access`, `deny_paths_no_write`: vedi [Come funzionano le regole](#how-rules-work).

Un ruolo che definisci da zero parte con `can_prompt` e `can_approve` attivi, `can_manage` ed entrambi i permessi per saltare le regole disattivi, i file con le credenziali nel suo `deny_paths_no_access`, e **nessuno strumento vietato, quindi ha Bash** finché non imposti `deny_tools` (`aipager doctor` avvisa una volta quando un membro ha quel ruolo). I limiti di scrittura e ricerca valgono per ogni ruolo senza `bypass_safety` o `bypass_role_denies`. La [cartella radice](#root-folders) vale per ogni ruolo senza `can_manage`, qualunque siano i suoi permessi per saltare le regole: un ruolo che definisci con `bypass_role_denies` o `bypass_safety` ma senza `can_manage` scrive e cerca ovunque dentro la sua cartella radice (e il suo scratchpad), non legge niente della tua cartella home fuori da lì, e da nessun'altra parte (con `bypass_safety`, in una sessione che gira fuori dalla sua cartella radice, scrive solo nello scratchpad). Solo proprietario e admin non hanno limiti.

### <a id="root-folders"></a>Cartelle radice

Ogni chat ha una cartella radice per i membri: la cartella in cui i membri il cui ruolo lì non ha `can_manage` (i predefiniti `user` e `read_only`, e i ruoli che definisci senza) avviano e tengono le loro sessioni. Proprietario e admin non sono legati a lei. La cartella radice di una chat privata è di quella persona; quella di un gruppo è condivisa dai membri limitati del gruppo. Due chat non condividono mai una cartella, a meno che tu non dia a entrambe la stessa di proposito (la procedura guidata ti chiede di confermarlo).

- **Impostarla.** `aipager config` la chiede quando aggiungi un gruppo, o una chat privata per un ruolo senza `can_manage`, con il valore predefinito già compilato, e Edit a scope → **Set the members' root folder** la cambia (vale subito, con una ricarica senza riavvio). La configurazione attuale mostra la cartella radice di ogni chat, con `(default)` quando non è impostata.
- **Il valore predefinito** è `~/aipager-users/<label>` per una chat privata (l'etichetta della persona) e `~/aipager-users/group-<label>` per un gruppo. L'etichetta diventa minuscola, e ogni carattere che non sia una lettera, una cifra, `.`, `_` o `-` diventa `-`; quando due chat finirebbero sulla stessa cartella, ognuna riceve in più il proprio ID di chat (`sara-123456`). La cartella (e `~/aipager-users`, quando è nuova) viene creata con permessi 0700, così può aprirla solo tu, la prima volta che un membro apre `/new` o il modulo «Avvia sessione» della Mini App. Prima che una sessione parta lì, aipager la segna come fidata per Claude Code, così la domanda «Do you trust this folder?» di Claude Code non chiude la prima sessione del membro. Un valore predefinito non si sposta mai: quando una modifica in `aipager config` (la cartella radice di un'altra chat, una chat tolta, una nuova etichetta) cambierebbe la cartella predefinita di una chat, la procedura guidata scrive la cartella che quella chat ha già come sua cartella radice.
- **Cosa rifiuta la procedura guidata**: un percorso relativo, i caratteri jolly, `/`, la tua cartella home o una cartella sopra di lei, una cartella che è, sta dentro o contiene una delle cartelle protette di aipager o dei file con le credenziali (`~/.claude`, `~/.config/aipager`, `~/.config`, `~/.ssh`, ...) e la cartella temporanea di Claude Code (`/tmp/claude-*`, dove stanno gli scratchpad di tutte le sessioni). Una cartella che non esiste ancora va bene. Chiede conferma per una cartella da cui il sistema esegue programmi (`~/bin`, `~/.local/bin`, `~/.config/systemd`, `~/.config/autostart`, o una cartella dentro di loro).
- **Avviare le sessioni.** La pagina Cartella di `/new` e della Mini App elenca la cartella radice e le cartelle subito dentro (niente cartelle nascoste, e niente link che portano fuori), senza la riga «Cartella predefinita»; una nuova sessione parte nella cartella radice, a meno che tu non scelga una di quelle cartelle. «Nuova cartella» crea la cartella dentro quella scelta. Tutto il resto riceve «Il tuo ruolo può avviare sessioni solo nella tua cartella ~/... o in una cartella al suo interno.»
- **Le sessioni fuori.** Riprendi, Riavvia, Sostituisci e il cambio di modalità su una sessione la cui cartella è fuori dalla cartella radice ricevono «Questa sessione lavora fuori dalla tua cartella ~/..., quindi il tuo ruolo non può riprenderla, riavviarla o cambiarne la modalità. Chiedi a un admin.» I messaggi per una sessione così in esecuzione vengono comunque consegnati, e l'hook di sicurezza tiene a freno l'attività: lì scrive solo nello scratchpad.
- **Dove l'hook tiene un'attività.** L'attività di un membro limitato scrive solo nella cartella della sessione (quando è dentro la cartella radice) e nel suo scratchpad, non legge e non fa `Grep` o `Glob` su niente della tua cartella home fuori dalla cartella radice (tranne il suo scratchpad e il suo file di risposta), e non tocca mai la cartella radice di un'altra chat, ovunque si trovi. Le cartelle fuori dalla tua home (`/usr`, `/etc`, ...) restano leggibili, le cartelle protette restano negate e `Bash` resta vietato a `user`. Quando un'attività mescola due membri con cartelle radice diverse, vince la più stretta; due cartelle che non si contengono lasciano solo la cartella della sessione.

Per restituire Bash a `user` (solo per persone a cui daresti una shell), nominalo in `allow_tools` (che diventa anche l'elenco di permessi di quel ruolo) oppure sostituisci il suo `deny_tools`:

```yaml
roles:
  user:
    # l'elenco predefinito senza Bash
    deny_tools: [PowerShell, Monitor, REPL, Workflow, CronCreate,
                 RemoteTrigger, AppifactRepl, self_hosted_runner_spawn_local,
                 self_hosted_runner_requeue_session, SendMessage,
                 EnterWorktree, ExitWorktree]
```

Poi `aipager doctor` avvisa: `role user has Bash: its safety rules are best-effort, not a boundary`. Se il tuo `policy.yaml` impostava già `deny_tools` per `user` prima che esistesse questo valore predefinito, il tuo elenco lo sostituisce ancora, quindi quel ruolo ha Bash finché non lo rimetti; l'avviso di doctor te lo dice. Vedi [sicurezza → applicazione delle regole nell'uso in team](security.md#team-mode-enforcement) per cosa non coprono questi limiti.

## <a id="talking-to-the-bot-in-a-group"></a>Parlare con il bot in un gruppo

### <a id="what-the-bot-reads-in-a-group"></a>Cosa legge il bot in un gruppo

In un gruppo aipager elabora un messaggio solo se è:

- **un comando**: `/status`, `/x1 fix the tests`, o lo stesso con `@aipagerbot` dopo il comando (`/x1@aipagerbot`), che Telegram aggiunge quando scegli il comando dal menu. Un comando per un altro bot (`/x1@otherbot`) viene ignorato;
- **una risposta a un messaggio del bot**;
- **un messaggio che menziona il bot**: `@aipagerbot fix the tests`;
- **un tocco sulla tastiera del bot** nel gruppo. In un gruppo ogni pulsante della tastiera comincia con un quadratino (`▫️ stop`, `▫️ Svuota conversazione`), e conta come tocco solo il testo con quel segno. Scriverlo a mano è una normale chiacchierata: un membro che scrive `stop`, `Svuota conversazione`, `Opus` o il nome di una sessione non cambia niente. Per usare apposta una parola della tastiera senza la tastiera, chiama il bot: `@aipagerbot stop`, oppure `stop` come risposta a un messaggio del bot.

Tutto il resto (i membri che parlano tra loro, una foto o un vocale che non risponde al bot) non riceve né reazione né risposta. Vale che il bot sia admin del gruppo oppure no. I messaggi modificati vengono ignorati ovunque: modificare un messaggio non fa niente, invialo di nuovo.

Lo stesso per una foto, un file o un vocale: invialo come risposta al bot, oppure menziona il bot nella didascalia. Per un album, metti la didascalia sul primo elemento: gli elementi dell'album che arrivano dopo quello con la didascalia, la menzione o la risposta vengono presi, quelli prima no.

**La menzione viene tolta.** `@aipagerbot fix the tests` arriva a Claude come `fix the tests` (Claude Code leggerebbe `@word` come un file). `@aipagerbot status` e `@aipagerbot x2` funzionano come scrivere `status` o `x2` in una chat privata, e `@aipagerbot` da solo dice dove sta andando il tuo messaggio. Un comando di sessione dopo la menzione funziona ancora (`@aipagerbot /x1 fix it`), ma un comando di aipager no: `@aipagerbot /stop` riceve «Invia /stop da solo, senza menzionare il bot.»

**Solo i membri.** Una persona che non è membro del gruppo riceve una risposta («🚫 Non sei tra le persone consentite in questa chat.») con il suo ID di Telegram da passarti, e viene registrata per `aipager config`; poi il bot la ignora fino a un riavvio di aipager. A un membro `read_only` il suo ruolo viene detto una volta (vedi [Ruoli](#roles)).

**Gli admin anonimi devono scrivere con il proprio nome.** Un admin del gruppo con «Remain anonymous» attivo, o un messaggio pubblicato a nome del gruppo o di un canale, non dice chi l'ha scritto: Telegram mostra lo stesso mittente per ogni admin anonimo. aipager non può attribuire a un messaggio così il ruolo di nessuno, quindi risponde «Scrivi con il tuo account per usare il bot.» (una volta per chat fino a un riavvio di aipager) e non fa nient'altro. Non viene mai registrato come persona in attesa, e la procedura guidata rifiuta quell'ID condiviso. Disattiva «Remain anonymous» per te nelle impostazioni di admin del gruppo, oppure scrivi dal tuo account.

### <a id="talking-to-a-session-in-a-group"></a>Parlare con una sessione in un gruppo

Rispondi a un messaggio di una sessione (la sua risposta, la sua scheda) e il tuo messaggio va a quella sessione. Oppure invia `/x1 il tuo messaggio`, o una didascalia che comincia con `/x1`.

**Ogni persona ha la sua sessione attuale**: quella a cui è passata per ultima (`/x1`), a cui ha scritto, a cui ha risposto o che ha avviato. La sessione di un altro membro che risponde non la cambia, e nemmeno il messaggio di qualcun altro. Una menzione, un vocale, un template o un comando della tastiera vanno alla tua sessione attuale, e un comando della tastiera o un template nominano quella sessione nella risposta («🧹 /clear inviato a x1»). Un `/stop`, `/mode`, `/kill`, `/restart`, `/rename`, `/delete` o `/diff` senza nome agisce solo sulla tua sessione attuale (con la sua solita conferma), e solo quando il comando ha senso per lei (al lavoro per `/stop`, terminata per `/delete`); altrimenti mostra sempre il selettore, anche quando resta una sola sessione, perché potrebbe essere di qualcun altro. `/clearqueue` e `/now` agiscono sulla tua sessione attuale. `/kill x1` e gli altri comandi con il nome di una sessione agiscono su quella sessione, come prima.

- Quando non hai ancora una tua sessione attuale e c'è più di una sessione in esecuzione, il bot chiede «Quale sessione? Rispondi a uno dei suoi messaggi o scrivi /x1 seguito dal tuo messaggio.» con un pulsante per sessione, e non invia niente. Quando c'è una sola sessione in esecuzione, il messaggio va lì.
- Se la tua sessione attuale è terminata, il messaggio non va all'unica sessione ancora in esecuzione (potrebbe essere di qualcun altro): il bot risponde «x1 è terminata. Quale sessione?» con un pulsante per ogni sessione in esecuzione e, quando x1 si può riprendere, «▶️ Riprendi x1», e non invia niente.
- Toccare il pulsante di una sessione in una di queste due domande invia anche a quella sessione il messaggio di cui parlava la domanda (un messaggio di testo, la trascrizione di un vocale o un file), come se l'avessi inviato lì, e la domanda diventa «Inviato a x2.». Solo chi ha inviato il messaggio può usare i pulsanti, e solo per dieci minuti e fino a un riavvio di aipager: dopo, il pulsante rende solo quella sessione la tua sessione attuale («Quel messaggio è troppo vecchio e non è stato inviato; invialo di nuovo.»). ▶️ Riprendi riporta indietro x1 ma non invia niente: invia di nuovo il tuo messaggio.
- Nella barra fissata di un gruppo non c'è la riga «I messaggi vanno a», perché è diversa per ogni persona.

**Le schede che aspettano una risposta sono di ognuno.** La scheda di nuova sessione dice «Rispondi a questo messaggio con un nome e, se vuoi, cosa fare per prima cosa:», la domanda per rinominare dice «Rispondi a questo messaggio con il nuovo nome.». In un gruppo ogni scheda mostra di chi è («🆕 Nuova sessione (@alice)»), può rispondere solo quella persona, e due persone possono avviare o rinominare sessioni nello stesso momento. La risposta di un altro membro alla scheda di nuova sessione di qualcuno riceve «Questa scheda è di @alice. Invia /new per averne una tua.» e non va da nessuna parte. Una domanda per rinominare smette di aspettare dopo 10 minuti, o appena la sua persona fa altro. La scheda «x1 è pronta», la risposta a `/x1`, `/start` e `/help` dicono di rispondere a un messaggio della sessione o di menzionare il bot («Rispondi a un messaggio di x1 (o menziona @aipagerbot) per parlarci.»), perché un messaggio normale non arriva al bot.

**Le tastiere sono di ognuno.** Template ›, Comandi ›, Modello › e « Indietro cambiano solo la tastiera di chi li tocca: il bot risponde come risposta al suo tocco, e la tastiera di nessun altro cambia. « Indietro sale da dove sei tu, qualunque cosa stiano guardando gli altri membri. La tastiera principale inviata da `/start` è di tutto il gruppo e riporta tutti alla tastiera principale. Quando una sessione del gruppo parte o termina, aipager rimanda quella tastiera principale al gruppo, una volta, così i pulsanti delle sessioni sono sempre giusti. Dopo l'aggiornamento da una versione senza i pulsanti segnati, il tocco di un pulsante della vecchia tastiera può essere ignorato: invia `/start` una volta perché il gruppo abbia la nuova tastiera. La tastiera di una chat privata non ha segni e funziona come prima.

### <a id="the-mini-app"></a>La Mini App

`/app` apre la Mini App solo in una chat privata. In un gruppo non è nel menu `/`; scritto lì, dice a un membro che ha già una chat privata con il bot «📱 La Mini App funziona solo in una chat privata: scrivi al bot in privato e invia /app lì.», e agli altri «La Mini App si apre dalla tua chat privata con il bot. Chiedi al proprietario del bot di aggiungerti.»

La Mini App mostra una chat alla volta. Una persona che appartiene a più chat (per esempio la sua chat privata e un gruppo) ha in alto una riga con i nomi delle chat per passare dall'una all'altra; si apre sulla sua chat privata, altrimenti sulla prima chat che la nomina, e ricorda l'ultima scelta su quel telefono. Tutto quello che c'è dentro (le sessioni, i loro pulsanti, le impostazioni e gli avvisi che pubblica) è della chat scelta, con il ruolo che la persona ha lì. Con una sola chat non si aggiunge niente.

## <a id="who-a-message-runs-as"></a>Con quali permessi gira un messaggio

Ogni messaggio gira con **i permessi della persona che l'ha inviato**, nella chat in cui l'ha inviato. Una persona che è `user` in un gruppo e `owner` della propria chat privata invia, tocca ed esegue comandi nel gruppo come `user`, qualunque sia l'ordine delle chat in `aipager.yaml`.

- Ogni messaggio di Telegram porta nella sessione l'identità e le regole di chi l'ha inviato (vedi [sicurezza → applicazione delle regole nell'uso in team](security.md#team-mode-enforcement)). Claude vede chi ha inviato ogni messaggio (`[via Telegram · @bob · role:user]` in un gruppo), mai chi ha guidato la sessione prima. Chi invia un messaggio diventa chi guida la sessione, che l'abbia scritto, usato `/x1`, un template o un comando della tastiera.
- **Un messaggio trattenuto mantiene il suo mittente.** Mentre è in corso l'attività di un'altra persona (compreso un agente in background che quell'attività ha avviato), o il messaggio di qualcun altro aspetta ancora di essere preso, o c'è una richiesta di permesso o una domanda aperta, un nuovo messaggio viene trattenuto (👀) e inviato quando finiscono, come attività a sé, **con i permessi del proprio mittente**. Un'attività scritta nel terminale conta come del proprietario: vi si unisce solo il messaggio del proprietario (o, in una chat privata, quello del membro di quella chat). I tuoi messaggi non aspettano mai la tua attività. Se l'attività che aspettano viene fermata, o bloccata da uno strumento vietato, i messaggi trattenuti vengono scartati (🤷): inviali di nuovo.
- **Vince la più restrittiva.** Se messaggi di persone diverse finiscono comunque nella stessa attività, quell'attività gira con la combinazione più restrittiva di tutte le persone che contiene. Un messaggio che si unisce a un'attività in corso non la allarga mai: il resto dell'attività tiene le regole più restrittive di tutti quelli che si sono uniti.
- **Comandi slash dai ruoli limitati.** Un ruolo che non può saltare le regole del ruolo (`user`, `read_only`, i ruoli che definisci senza `bypass_role_denies`, e un mittente che aipager non conosce) può inviare solo i comandi slash della tastiera «Comandi» (la tua, se `keyboard.json` la cambia), `/compact` e i cambi di modello (`/model <name>`). Tutto il resto, scritto, inviato con `/x1 /deliver ...`, come didascalia di un file, come primo messaggio di `/new x1 /review ...` o come template, riceve «Quel comando è solo per gli admin.» e un 🤷, e niente arriva a Claude. Proprietario e admin possono inviare qualsiasi comando.
- **I ruoli limitati stanno lontani dalla home e dai file con le credenziali.** L'attività di un `user` o di un `read_only` non scrive mai in una cartella di sessione che sia la tua home, `/` o una cartella sopra (solo nello scratchpad) e non legge mai i file con le credenziali elencati in [Ruoli](#roles).
- **I ruoli limitati restano nella loro cartella radice.** L'attività di un ruolo senza `can_manage` non legge niente della tua cartella home fuori dalla [cartella radice](#root-folders) della chat e non scrive niente in una cartella di sessione fuori da lì. La cartella radice è quella della chat da cui è arrivato il messaggio del membro, qualunque sia la sessione a cui va.
- **Un membro tolto perde l'accesso subito, ma non la sua attività in corso.** Un mittente che aipager non conosce più (tolto mentre il suo messaggio aspettava) gira con la base limitata. Dopo una ricarica senza riavvio i suoi messaggi trattenuti vengono scartati, e una sua attività ancora in corso continua a girare, legata alla base limitata fino alla fine; invia `/stop` per terminarla subito (vedi [Ricaricare senza riavviare](#live-reload)).
- **Un messaggio da un'altra sessione di Claude porta i permessi di chi lo manda.** Un messaggio che una sessione manda a un'altra (`SendMessage` di Claude Code) gira con i permessi dell'attività che l'ha mandato, come li ha verificati aipager, mai con i permessi precedenti della sessione che lo riceve. Un messaggio che aipager non riesce a verificare gira con le regole più restrittive, senza modifiche ai file e senza accesso al web (vedi [sicurezza → messaggi tra sessioni di Claude](security.md#messages-between-claude-sessions)).
- **Uno strumento bloccato ferma l'attività.** Quando un'attività prova uno strumento che le sue regole vietano, si ferma subito con «🛑 x1 · Bloccato da una regola di sicurezza: ... (fermata)», e viene negata anche ogni chiamata successiva a uno strumento in quell'attività.

## <a id="who-can-tap-what"></a>Chi può toccare cosa

Ogni pulsante controlla il ruolo di chi lo tocca, nella chat del pulsante, nel momento del tocco. Il ruolo di chi ha inviato il comando che ha prodotto il pulsante non conta. (Alcune schede controllano anche chi le ha chieste: le schede di conferma e quelle di `/new`, sotto.)

- **Ogni membro può guardare** (VIEW). I membri `read_only` possono sfogliare `/resume`, aprire la parte Terminate di `/status`, il menu ⋮ di una sessione, la sua modalità o il suo diff, guardare `/settings` e chiudere un menu. Non possono agire: ogni altro pulsante risponde «Il tuo ruolo in questa chat non lo consente.»
- **Rispondere a Claude richiede `can_approve`** (APPROVE): Consenti, Consenti sempre, Nega, le opzioni di una domanda, Invia, Continua e il pulsante Rispondi della barra fissata. `read_only` non ce l'ha.
- **Agire su una sessione richiede `can_prompt`** (PROMPT): Ferma, Riprova, Compatta, Invia ora, Termina, Riavvia, Rinomina, Elimina, Riprendi, il cambio di modalità, ✍️ parla, Cancella tutte, le preferenze di una sessione e le schede di `/new`. Lo richiede anche un pulsante che aipager non conosce.
- **Passare alla modalità «Completo» richiede un admin** (MANAGE: `owner`, `admin`, o un ruolo con `can_manage`, la stessa regola di `/mode full`): «Sì, cambia» e «Ferma e cambia» su una scheda che passa a «Completo», e riprendere in modalità «Completo». Passare tra «Chiedi» e «Automatico» lo può fare chiunque possa agire sulla sessione. Riprendere una sessione che era in modalità «Completo» (scrivendo `/resume x1`, dal selettore, con Riprendi sulla scheda del nome di `/new` o dalla Mini App) la riporta in modalità «Automatico» per chiunque altro, e la risposta lo dice: «♻️ x1 è ripartita in modalità «Automatico»: la modalità «Completo» è solo per gli admin.»
- **Nella propria chat privata, una persona può usare la modalità «Completo» qualunque sia il ruolo** (anche `user`; non `read_only`, un ruolo che non invia messaggi; un ruolo personalizzato ha bisogno di `can_prompt` e `can_approve`, perché la modalità «Completo» risponde a ogni richiesta di permesso di Claude Code). Lì può usare tutte le strade verso la modalità «Completo» viste sopra: `/mode full`, la scheda della modalità, `/new !x1`, le schede di `/new`, `/resume` e la Mini App. Vale solo nella propria chat privata: in un gruppo, e nella chat privata di qualcun altro, la modalità «Completo» richiede ancora un admin. Le sue nuove sessioni partono in modalità «Automatico», come quelle di tutti, finché non sceglie la modalità «Completo» in `/settings` → Nuove sessioni. Nient'altro di quello che può fare un admin si apre per lei (`/update`, il pulsante «Installa la funzione vocale» e `/settings` di un gruppo restano solo per gli admin). Concedere la modalità «Completo» così è sicuro: avvia Claude Code con `--dangerously-skip-permissions`, che salta solo le richieste di permesso di Claude Code. L'hook PreToolUse di aipager gira comunque a ogni chiamata a uno strumento e applica le regole del ruolo del membro (base di sicurezza, limiti delle cartelle, regole di divieto, file con le credenziali), esattamente come nelle modalità «Chiedi» e «Automatico».
- **Installare software richiede un admin** (UPDATE): i pulsanti Installa e Riavvia della funzione vocale, come quelli di `/update`.
- **Cambiare `/settings` di un gruppo richiede un admin.** Guardare lo può fare chiunque.
- **Una scheda di conferma è di chi l'ha chiesta.** In un gruppo, le schede di conferma di Termina (`/kill`, ⋮ Termina la sessione), Riavvia, Elimina e del cambio di modalità, i selettori di sessione mostrati da `/kill`, `/restart`, `/delete`, `/mode`, `/stop`, `/rename` e `/diff`, e la scheda «Riprendi x1 in:» che un tocco su Riprendi produce (i suoi pulsanti della modalità) rispondono solo alla persona che ha inviato il comando o toccato il pulsante che le ha prodotte. Il tocco di chiunque altro, Annulla compreso, riceve «Questa scheda è di @alice. Invia /kill x2 per averne una tua.» (con il comando di quella scheda) e non cambia niente. La scheda di `/mode` in sé (che mostra in che modalità è la sessione) è di tutti: un cambio toccato lì produce una scheda di conferma di chi ha toccato. Le schede di `/new` rispondono solo a chi ha inviato `/new`. Il proprietario di una scheda viene ricordato finché la scheda non viene usata o annullata, anche dopo un riavvio di aipager (le 4096 schede più recenti). La scheda del nome di `/new` e la domanda del nome di `/rename` sono l'eccezione: scadono comunque dopo 10 minuti, quindi dopo un riavvio invia di nuovo il comando.
- **Un pulsante funziona solo nella chat della sua sessione.** Toccato da qualsiasi altra parte risponde «Questo pulsante appartiene a un'altra chat.» e non fa niente, e Riprendi riprende sempre la sessione che il pulsante nomina, mai una sessione con lo stesso nome in un'altra chat.
- **Un pulsante vecchio non cambia niente.** Un pulsante rimasto da un'attività precedente, o da prima che la sessione terminasse e tornasse, non agisce su quello che è in corso ora; lo dice (vedi [comandi → pulsanti vecchi](commands.md#stale-buttons)).

## <a id="what-everyone-sees"></a>Cosa vedono tutti

**Risposte a Claude.** Ogni Consenti, Nega e risposta viene pubblicato nella chat, sotto la scheda della sessione, con chi l'ha fatto:

```
✅ x1 · Consentito · @alice · Bash: ls -la /tmp
🚫 x1 · Negato · @bob · WebFetch: https://example.com
✓ x1 · Risposto · @carol · Which database? → Postgres
```

**Chi ha cambiato una sessione.** Il risultato del tocco di un pulsante lo vede solo chi tocca, quindi in un gruppo ogni risultato che cambia una sessione nomina chi l'ha fatto:

```
⚠️ x1 · @bob l'ha fermata
⏹ @bob ha terminato x2
🔄 @alice ha riavviato [x1].
🗑️ @carol ha eliminato [x3].
⚙️ @alice ha passato la sessione a 🤖 Modalità «Automatico».
@alice l'ha cambiato.          (sotto /settings)
```

La scheda «x1 è pronta» nomina chi ha cambiato la sua modalità o il suo modello («@bob ha cambiato il modello in Opus.»). In una chat privata queste righe di risultato non nominano nessuno. Una modifica fatta nella Mini App viene pubblicata in chat come «... dalla Mini App» (anche uno Ferma da lì nomina chi l'ha fatto sulla scheda della sessione).

**Uno strumento bloccato da una regola.** Un'attività si ferma con «🛑 x1 · Bloccato da una regola di sicurezza: Write è in deny_tools di questo ruolo (fermata)». Se Claude Code chiede un permesso per uno strumento che chi ha mandato il messaggio dell'attività in corso non può usare, il bot risponde Nega da solo, senza mostrare la richiesta, e pubblica `⛔ x1 · Edit bloccato per @bob (ruolo user)` con il riepilogo della chiamata sotto, nominando quella persona. Un'attività avviata dai messaggi di più persone viene bloccata quando una qualsiasi di loro non può usare lo strumento, e la nomina. Un'attività scritta nel terminale è tua e non viene mai bloccata dalle regole dei membri, a meno che non vi si sia unito un messaggio di Telegram: allora viene controllata con le regole di chi ha mandato il messaggio più recente della sessione (l'elenco del gruppo stesso quando non si sa chi è). Quando aipager non sa di chi sia l'attività (era già in corso quando aipager si è riavviato), usa le regole di chi ha mandato il messaggio più recente della sessione, e l'elenco del gruppo quando non si sa nemmeno questo.

**Stato dei messaggi.** 👀 su un messaggio vuol dire che è stato consegnato alla sessione (o trattenuto finché si può consegnare); 👍 vuol dire che Claude l'ha preso; 🤷 vuol dire che è stato scartato prima che Claude lo prendesse. `/whoami` mostra la tua etichetta, il tuo ruolo e le tue regole in questa chat.

**Barra di stato fissata.** Il gruppo ha la sua barra di stato fissata (vedi [comandi → barra di stato fissata](commands.md#the-pinned-status-bar)), che elenca solo le sessioni di questo gruppo, se il bot è un admin con il permesso di fissare. Senza quel permesso il gruppo non ha la barra: il messaggio viene cancellato, non lasciato senza fissare nella cronologia.

**Registro di audit sul disco.** `~/.claude/aipager-audit.jsonl` registra ogni decisione con `user_id`, `username` e la chat, così più tardi puoi ricostruire chi ha fatto cosa. È sempre scritto in inglese, qualunque lingua usi la chat.

**Lingua.** Un gruppo ha una lingua per i messaggi, le schede e le tastiere di aipager, scelta da un admin con `/language`; finché un admin non sceglie, è l'inglese. Non segue mai l'app di Telegram di nessuno. Quello che vede una sola persona, l'avviso dopo un tocco, viene mostrato nella sua lingua: la sua scelta nella chat privata con il bot, altrimenti la lingua della sua app di Telegram per chi è entrato dopo questa versione, altrimenti la lingua del gruppo. Cambiano solo i testi di aipager; Claude risponde nella lingua in cui ognuno gli scrive. Vedi [Lingue](commands.md#languages).

## <a id="where-messages-go"></a>Dove vanno i messaggi

- **Ogni sessione appartiene a una chat, e tutto quello che la riguarda va in quella chat**: la sua scheda, le sue risposte, le richieste di permesso, i file e gli avvisi. Un messaggio in una chat non va mai a una sessione di un'altra chat, e il nome di una sessione funziona solo nella sua chat: scrivere il nome di una sessione di un'altra chat riceve la risposta «Qui non c'è nessuna sessione chiamata …». Una sessione avviata mentre aipager gira senza nessuna chat configurata riceve la sua chat appena `aipager config` ne aggiunge una; finché una sessione non ha una chat, nessuna chat la elenca o agisce su di lei.
- **Le sessioni avviate nel terminale** (`aipager session <name>`) appartengono alla chat privata del proprietario: la chat privata il cui membro ha il ruolo `owner` (un ruolo con `bypass_safety`). Con più chat private e nessuna del proprietario, è la prima chat privata dell'elenco; senza nessuna chat privata, la prima chat. In un'installazione con una chat privata non vanno mai in un gruppo.
- **Il passaggio a supergroup viene seguito da solo** (vedi [Quando Telegram aggiorna il gruppo](#when-telegram-upgrades-the-group)).

## <a id="aipageryaml-schema-team-parts"></a>Struttura di `aipager.yaml` (le parti per l'uso in team)

```yaml
schema_version: 3
bot_token: "…"

scopes:
  - kind: dm
    chat_id: 12345       # la tua chat privata: il suo ID è il tuo ID utente
    label: owner DM
    members:
      - id: 12345
        label: alice
        role: owner
  - kind: group
    chat_id: -100123456789
    label: team
    deny_tools: [Write, Edit]   # facoltativo: la risposta a «Block file edits»
    root: /home/alice/aipager-users/group-team   # la cartella radice dei membri
    members:
      - id: 12345        # ID utente di Telegram (NON l'etichetta, NON l'ID della chat)
        label: alice     # come compare la persona in chat (@alice)
        role: owner
      - id: 67890
        label: bob
        role: user
        deny_tools: [WebFetch]  # facoltativo, per membro
      - id: 11111
        label: charlie
        role: read_only
```

Uno scope di chat privata e gli scope di gruppo convivono: il bot serve ogni chat dell'elenco e le sessioni di ogni chat restano separate. `aipager config` scrive questo file; non devi mai modificarlo.

## <a id="how-rules-work"></a>Come funzionano le regole

`~/.config/aipager/policy.yaml` definisce le regole di ogni ruolo. Controllalo con `aipager policy validate`. I campi supportati per ruolo (oltre a quelli di [Ruoli personalizzati](#custom-roles)):

- `deny_tools`: nomi di strumenti vietati senza chiedere (per esempio `Write`, `Edit`, `Bash`, `WebFetch`).
- `allow_tools`: se non è vuoto, è un elenco di permessi: tutto il resto è vietato a quel ruolo.
- `deny_bash_patterns`: schemi confrontati con l'input di `Bash`.
- `deny_paths_no_access` / `deny_paths_no_write`: regole sui percorsi. Per `user`, `read_only` e ogni ruolo che definisci senza `bypass_role_denies`, `deny_paths_no_access` parte con i file delle credenziali elencati sopra in **user**; impostarlo sostituisce quell'elenco, quindi copia nel tuo elenco quelli che vuoi tenere (un'attività in corso a cui si unisce un altro messaggio torna a essere legata a quell'elenco fino alla fine). Una regola che non comincia con `/` o `~` (`**/.env`) corrisponde ovunque, quindi con una regola così ogni `Grep`/`Glob` di un ruolo limitato viene negato (qualsiasi ricerca potrebbe leggere un file del genere), e questo ferma l'attività.

Il `deny_tools` di una chat e quello di un membro in `aipager.yaml` si aggiungono a quello del ruolo, e l'`allow_tools` di un membro si aggiunge all'elenco di permessi del ruolo. Tutto questo vale solo per i ruoli senza `bypass_role_denies`: un proprietario o un admin non ne risente.

I file in `~/.config/aipager/policy.d/*.yaml` vengono letti dopo `policy.yaml`, in ordine di nome, con lo stesso formato: un campo di ruolo impostato in un file successivo sostituisce il valore precedente, mentre un elenco `safety:` può solo aggiungere. `aipager policy validate` li controlla tutti.

Sotto ogni ruolo c'è una **base di sicurezza** (percorsi protetti e schemi di comandi) che solo `owner` può saltare: gli admin ci sono legati anche loro. È l'elenco predefinito di aipager più la sezione `safety:` di `policy.yaml`, che può solo aggiungere:

```yaml
safety:
  deny_paths_no_access: [/srv/secrets/**]   # niente lettura, scrittura o ricerca
  deny_paths_no_write: [~/notes/**]         # leggibile, ma non scrivibile
  deny_bash_patterns: ['\bcurl\b']          # comandi Bash da bloccare
```

Tutte le attività di tutti i ruoli tranne `owner` ricevono questa base per intero, e così le attività che aipager non sa di chi siano; `aipager doctor --safety-check` elenca cosa è in vigore. Dopo una ricarica senza riavvio, una modifica vale per i messaggi inviati dopo la ricarica; un'attività in corso, o un messaggio che aspetta in coda, tiene l'elenco vecchio (ferma l'attività per applicare subito la modifica). Un percorso `deny_paths_no_access` lì dentro che non comincia con `/` o `~` corrisponde ovunque, quindi con uno così ogni `Grep`/`Glob` di ogni ruolo tranne `owner` viene negato (`aipager doctor` lo segnala). I percorsi protetti valgono per gli strumenti sui file di Claude Code (`Read`, `Write`, `Edit`, `MultiEdit`, `NotebookEdit`, `LSP`, `Grep`, `Glob`); il `Grep`/`Glob` di un ruolo limitato deve anche restare dentro la cartella della sessione (una ricerca di `aipager/**` in `~/.config` viene negata). `WebFetch` non viene controllato per percorso; `SendUserFile` (i file che Claude manda nella chat) sì, come un Read, e un membro manda solo dalla cartella radice della chat ([sicurezza](security.md#files-claude-sends)). Un'attività da Telegram che aipager non riesce ad attribuire a nessun mittente riceve la base, restrittiva almeno quanto `user`. Riprova gira con i permessi di chi tocca se è stata quella persona a inviare il messaggio da riprovare, e con la base altrimenti. Gli schemi di comandi contano solo per un ruolo con Bash, e lì sono un filtro, non un muro; vedi [sicurezza → applicazione delle regole nell'uso in team](security.md#team-mode-enforcement).

## <a id="privacy-considerations"></a>Cose da sapere sulla privacy

- Il bot ascolta solo le **chat configurate**. Aggiungerlo a un altro gruppo non lo attiva lì: lì non risponde mai. Registra chi lo chiama lì (l'ID, lo username e il nome della persona, più l'ID e il titolo del gruppo) in `~/.claude/aipager-pending-users.json`, così `aipager config` può aggiungere quel gruppo. Quel file lo puoi leggere solo tu e tiene le 200 persone più recenti viste negli ultimi 30 giorni.
- I membri `read_only` **possono leggere** messaggi, risposte e input degli strumenti. Non possono agire, ma vedono tutto nel gruppo. Se un lavoro va tenuto nascosto a qualcuno, quella persona non dovrebbe essere nel gruppo.
- In un'installazione con una chat privata, le sessioni del terminale e quelle della tua chat privata non compaiono mai in un gruppo (vedi [Dove vanno i messaggi](#where-messages-go)). Una sessione del terminale che una versione più vecchia di aipager ha spostato in un gruppo resta al gruppo: terminala, eliminala dalle sessioni terminate del gruppo e ricominciala.
- `aipager.yaml` ha permessi 0600 (solo il proprietario). aipager non imposta i permessi di `policy.yaml`: tienilo tu solo per il proprietario (`chmod 600 ~/.config/aipager/policy.yaml`).
- Il log di audit (`~/.claude/aipager-audit.jsonl`) non ha permessi suoi: è privato quanto la tua cartella `~/.claude`.

## <a id="revoking-a-user"></a>Togliere l'accesso a qualcuno

1. Esegui `aipager config` → Edit a member → la chat → la persona → Remove member. Per togliere solo la possibilità di inviare messaggi lasciando quella di leggere, usa Set role e scegli `read_only`. (Per aggiungere di nuovo qualcuno più tardi: Edit a scope → il gruppo → Add a member.) Per la chat privata di una persona, termina le sue sessioni, poi Edit a scope → la sua chat privata → Remove this scope.
2. La procedura guidata ricarica aipager senza riavvio; scrive "Scopes reloaded live". Subito i suoi messaggi trattenuti vengono scartati (🤷 e una risposta «Non inviato a Claude») e una sua attività ancora in corso resta legata alla base limitata fino alla fine. Invia `/stop` per terminare subito quell'attività.
3. Togli la persona anche dal gruppo di Telegram. Finché è nel gruppo legge ancora tutto quello che vi viene pubblicato: schede, risposte, input degli strumenti e richieste di permesso.

Guarda cosa dice la procedura guidata dopo il passo 1. Se ti dice di riavviare aipager, non è riuscita a raggiungerlo: riavvialo (`aipager service stop`, poi `aipager service start`), perché fino ad allora il vecchio elenco dei membri resta in memoria. Se scrive "Not applied", correggi il problema che indica: fino ad allora aipager tiene la configurazione vecchia. Dopo aver modificato `aipager.yaml` a mano, manda tu l'ordine di ricarica (vedi [Ricaricare senza riavviare](#live-reload)).

## <a id="related-docs"></a>Documenti correlati

- [Architettura](architecture.md): come è organizzato il processo.
- [Comandi del bot](commands.md): riferimento dell'interfaccia.
- [Modello di sicurezza](security.md): confine di fiducia, elenco delle minacce.
- [Risoluzione dei problemi](troubleshooting.md): riferimento di `aipager doctor`.
