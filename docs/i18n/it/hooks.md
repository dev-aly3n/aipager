# Eventi hook

<!-- i18n-source: docs/hooks.md @ 932dbe80 -->

> Questa pagina è tradotta dalla pagina in inglese; se le due versioni non coincidono, vale quella in inglese. ([English](../../hooks.md))

Claude Code esegue comandi hook in momenti precisi di un'attività: quando viene inviato un messaggio, quando uno strumento sta per partire, quando ha finito, quando l'attività termina, e così via. aipager si aggancia a ognuno di questi momenti e li trasforma nell'attività che vedi in Telegram: la scheda di attività, le richieste di permesso, le risposte, gli avvisi e il log di audit.

## <a id="how-it-is-wired"></a>Come è collegato

`aipager config` (o `aipager setup`) aggiunge le voci di aipager a `~/.claude/settings.json` (o `$CLAUDE_CONFIG_DIR/settings.json`, il file che leggono le sessioni del daemon), e aipager le controlla a ogni avvio:

- un comando `aipager-hook` per ognuno dei 16 eventi qui sotto, con matcher `*` sugli eventi degli strumenti (`PreToolUse`, `PostToolUse`, `PermissionRequest`);
- un timeout di 5 secondi su `PreModelSwitch` e, se configurato con `aipager config` e `aipager setup`, uno di 30 secondi su `PermissionRequest` (l'hook stesso aspetta al massimo 20 secondi, vedi sotto); le altre voci usano i valori predefiniti di Claude Code;
- il comando `statusLine`, `aipager-statusline`.

`aipager uninstall` toglie di nuovo queste voci (vedi [comandi](commands.md#command-line-aipager-uninstall)).

Claude Code avvia un nuovo processo `aipager-hook` (`aipager.dtach.notify_hook:main`) per ogni evento. Questo legge il JSON dell'evento da stdin, aggiunge due campi (vedi sotto) e lo invia ad aipager come un singolo datagram su un socket Unix. Il socket si sceglie in quest'ordine: `$AIPAGER_INSTANCE_DIR/aipager.sock` per un'istanza di test isolata, `$AIPAGER_SOCKET_PATH`, `$XDG_RUNTIME_DIR/aipager.sock` oppure `/tmp/aipager.sock`. L'invio non aspetta mai: se nessun aipager è in ascolto, l'evento va perso e il controllo periodico delle sessioni di aipager recupera dai file della sessione.

Per la maggior parte degli eventi, avviare il processo costa a Claude Code circa 20-25 millisecondi. Alcuni caricano anche il codice delle regole di aipager: circa 70 millisecondi prima di una chiamata a uno strumento (il controllo di sicurezza, vedi [PreToolUse](#pretooluse)), circa 55 quando un'attività finisce (si cancella il segno di attività aperta) e `UserPromptSubmit` (scrive le regole dell'attività). `PermissionRequest` aspetta apposta il tuo tocco, fino a 20 secondi.

L'hook limita la propria memoria a 1 GB. Un hook che arriva a quel limite esce con un errore (per `PreToolUse`, un codice di uscita che Claude Code tratta come un rifiuto) e invia ad aipager un datagram `hook_memory_cap_hit`, che diventa un avviso in chat con un pulsante «🐞 Segnala questo» per il proprietario dell'installazione (vedi [Segnalazioni di problemi](problem-reports.md)). Imposta `AIPAGER_DEBUG=1` nell'ambiente della sessione per far stampare all'hook e alla riga di stato le informazioni di diagnostica su stderr.

## <a id="what-the-daemon-receives"></a>Cosa riceve aipager

Il datagram è il JSON dell'hook di Claude Code più i campi di aipager (alcuni eventi ne portano altri, descritti sotto ogni evento):

| Campo | Significato |
|---|---|
| `hook_event_name` | L'evento (campo di Claude Code; aipager smista in base a questo). |
| `session_id`, `transcript_path`, `cwd`, ... | Campi propri di Claude Code, lasciati come sono. `session_id` è l'ID della conversazione di Claude Code. |
| `session` | Aggiunto da aipager: il suo nome per la sessione, `claude-<name>`, dalla variabile `CLAUDE_DTACH_SESSION` che aipager imposta quando avvia una sessione. Gli eventi delle sessioni di Claude Code che aipager non ha avviato non hanno nome e aipager li ignora. |
| `sl_tokens` | Aggiunto da aipager a ogni evento tranne `MessageDisplay`, quando esiste il file della riga di stato della sessione: `context_pct`, `total_output`, `total_input`, `current_output`, `lines_added` e `lines_removed`. |
| `claude_pid` | Aggiunto da aipager agli eventi propri della sessione: il PID del suo `claude` (il `CLAUDE_PID` di Claude Code), con cui aipager trova quel claude nell'elenco delle sessioni di Claude Code. |
| `bg_job` | Aggiunto da aipager a ogni evento di un job in background di Claude Code che conta per la sessione (vedi sotto): l'ID del job. |

Gli eventi degli strumenti portano anche `tool_name` e `tool_input`. Ogni evento nato dentro un subagent porta `agent_id` (gli eventi dei subagent anche `agent_type`); quelli del ciclo principale no, ed è così che aipager mette le chiamate agli strumenti di un agente sulla riga di quell'agente.

Anche i job in background di Claude Code eseguono i loro hook. ← su un campo di input vuoto sposta la conversazione di una sessione in un job in background (la vista degli agenti di Claude Code), e Claude Code tiene anche pronti dei processi in background di riserva per il job successivo. I loro hook girano con `CLAUDE_JOB_DIR` impostato ed ereditano il nome di sessione del claude, qualunque sia, che ha avviato il servizio in background di Claude Code, quindi quel nome non dice nulla su di loro. Per questo l'hook conta un job in background solo per la sessione il cui claude ha spostato la propria conversazione in quel job: trova quella sessione nel file di collegamento del job (`claude-bgjob-<job>.json`, che aipager scrive quando vede lo spostamento, vedi [architettura](architecture.md#file-and-socket-layout)), oppure nell'elenco delle sessioni di Claude Code insieme all'elenco dei PID di aipager. Gli eventi di un job così arrivano ad aipager come quelli di quella sessione, con `bg_job` impostato, così la sua risposta, la sua scheda e le sue richieste arrivano alla chat della sessione. Qualsiasi altro processo in background è estraneo: il suo hook non scrive nulla, non invia nulla e non stampa nulla, tranne che `PreToolUse` controlla comunque le sue chiamate agli strumenti con le regole della sessione che ha ereditato e stampa un rifiuto (non consente mai più di quanto consentirebbe quella sessione). aipager scarta un evento `bg_job` che non riesce ad abbinare a una sessione nel background, e non crea nulla per lui.

## <a id="event-reference"></a>Riferimento degli eventi

I gestori di aipager sono in `aipager/dtach/hook_receiver.py`; il lavoro dell'hook stesso è in `aipager/dtach/notify_hook.py`.

### <a id="sessionstart"></a>`SessionStart`

Una sessione è partita, è stata ripresa o è stata compattata. Una sessione che aipager non seguiva ancora viene registrata. Dopo una compattazione la scheda mostra `📦 name · Conversazione compattata (prima: 82%)` (vedi [PreCompact](#precompact--postcompact)). Per una conversazione nuova, l'hook cancella anche il segno di attività aperta di un'attività precedente. Il primo `SessionStart` di un job in background (origine `fork`) dice ad aipager solo l'ID della conversazione del job; non chiude l'attività aperta che si è spostata con esso. Il primo `SessionStart` di una sessione riportata qui dal background di Claude Code invia i messaggi trattenuti nel frattempo.

### <a id="sessionend"></a>`SessionEnd`

Una sessione è terminata. Viene segnata GONE: esce dalla barra di stato fissata, passa tra le sessioni terminate e `/resume` può riportarla indietro. `aipager session <name>` o `/new <name>` ne avviano una nuova. Il `SessionEnd` di un job in background (il job è stato fermato) non termina mai la sessione: chiude un'attività che il job ha lasciato aperta, come fa `StopFailure`.

### <a id="userpromptsubmit"></a>`UserPromptSubmit`

Claude ha preso un messaggio, scritto nel terminale o inviato da Telegram.

Per prima cosa l'hook confronta il messaggio con le note di permessi della sessione (`/tmp/claude-notes-<session>/`, una per ogni messaggio di Telegram che aipager ha scritto) e scrive le regole dell'attività in `/tmp/claude-policy-<session>.json` per il controllo [PreToolUse](#pretooluse) (vedi [sicurezza → applicazione delle regole nell'uso in team](security.md#team-mode-enforcement)). Prima di inoltrare l'evento, dice ad aipager quali messaggi ha preso Claude (un datagram `queue_pickup`). Poi stampa contesto in più per Claude quando c'è: lo stile di risposta scelto in `/settings` e, quando rispondi a un messaggio precedente, cosa diceva quel messaggio e quando è stato inviato. Un messaggio `<task-notification>` (Claude che si sveglia da solo quando un agente in background o un comando in background finisce) è il seguito dello stesso lavoro: non corrisponde a nessuna nota e mantiene le regole dell'attività.

aipager segna la sessione come al lavoro, apre una scheda di attività se non ce n'è già una (un messaggio arrivato da Telegram ce l'ha già) e trasforma 👀 in 👍 sui messaggi che Claude ha preso. Un messaggio inviato mentre un'attività è in corso riceve 👍 quando Claude lo unisce a quell'attività o con esso ne avvia una nuova.

### <a id="pretooluse"></a>`PreToolUse`

Uno strumento sta per partire. Il controllo di sicurezza di aipager gira proprio qui, nell'hook, prima che Claude Code guardi le sue regole sui permessi:

- Un'attività partita da Telegram rispetta le regole del ruolo di chi l'ha inviata e la base di sicurezza (percorsi protetti, schemi di comandi), che solo il ruolo `owner` può saltare; un'attività che aipager non sa attribuire riceve la base di sicurezza; un messaggio scritto da te nel terminale non ha limiti. Le regole sono in [gruppi](groups.md#how-rules-work) e in [sicurezza](security.md#team-mode-enforcement).
- Quando una regola nega la chiamata, l'hook stampa una decisione `deny`, Claude Code nega lo strumento senza chiedere e avvisa aipager (`safety_blocked`): l'attività si ferma con `🛑 x1 · Bloccato da una regola di sicurezza: ... (fermata)` e anche ogni chiamata successiva dell'attività viene negata.
- Una chiamata a `SendUserFile` (Claude manda file nella chat) viene controllata per ogni attività, compresa quella dell'owner e un messaggio scritto nel terminale: la soglia di invio, i controlli sul file e le regole dell'attività ([sicurezza](security.md#files-claude-sends)). Una chiamata rifiutata riceve un `deny` senza il segno «aipager safety policy»: Claude legge il motivo e te lo dice, l'attività continua, e l'hook manda ad aipager `send_files_refused` con ogni file, per il log di audit.
- Un errore nel controllo stesso nega, a meno che le regole della sessione si possano leggere e concedano al proprietario di saltarle.

Dalla parte di aipager, la chiamata diventa una riga della scheda di attività (quella di un subagent finisce sulla riga del suo agente) e, quando `/settings` → Anteprime dei diff è attivo (di base è disattivo), un `Write` o un `Edit` mostra l'anteprima della modifica. Una chiamata `AskUserQuestion` diventa una domanda con un pulsante per opzione; una domanda posta da un agente di supporto di Claude Code quando nessuna attività è in corso non viene mostrata (non è Claude che ti chiede qualcosa). Le chiamate `Agent` di Claude lasciano `subagent_type` e `description` per l'agente che avviano, e la chiamata di un subagent è l'attività più recente di quell'agente: entrambe compaiono nello stato sotto una risposta mentre l'agente gira in background.

### <a id="permissionrequest"></a>`PermissionRequest`

Claude Code sta per chiedere un permesso. La scheda di attività diventa la richiesta, con il comando o il percorso vero sotto la descrizione di Claude (vedi [comandi → richieste di permesso](commands.md#permission-prompts)): Consenti, Nega, Ferma, e Consenti sempre solo quando Claude Code offre una regola che si possa davvero ricordare.

L'hook risponde da solo alla richiesta quando può. Apre un socket di risposta di breve durata, invia il suo indirizzo con l'evento e aspetta il tuo tocco fino a 20 secondi. Un tocco in tempo torna come decisione di `PermissionRequest` (consenti, consenti con regola o nega), quindi Claude Code non mostra il suo dialogo. Senza un tocco in tempo, l'hook dice ad aipager che l'attesa è finita (`permission_reply_timeout`) e non stampa niente; non stampa niente nemmeno quando aipager non è raggiungibile o qualcosa va storto lungo questa strada. Allora Claude Code mostra il suo dialogo nel terminale e un tocco successivo risponde scrivendo i tasti. Il log di audit (`~/.claude/aipager-audit.jsonl`) registra come ha risposto ogni tocco: `"via": "hook_decision"` oppure `"via": "keystroke_fallback"`.

Anche un `PermissionRequest` che arriva mentre un'altra richiesta è aperta viene mostrato, come messaggio a parte sotto la scheda di attività. Lo stesso dialogo che arriva di nuovo (stesso richiedente, stesso strumento e stessa chiamata, o stesso input) viene mostrato una volta sola. Claude Code esegue questo hook anche per un `AskUserQuestion`; quella domanda è già mostrata dal suo `PreToolUse`, quindi non viene mai mostrata una seconda volta. Il dialogo di un agente in background compare nel terminale solo dopo che il suo hook ha smesso di aspettare, sotto ogni dialogo già aperto lì; per questo un tocco scrive tasti solo nella richiesta che Claude Code mostra per prima (vedi [comandi → richieste di permesso](commands.md#permission-prompts)).

### <a id="posttooluse--posttoolusefailure"></a>`PostToolUse` / `PostToolUseFailure`

Uno strumento ha finito, o è fallito. La sua riga nella scheda di attività si fissa (un errore viene segnato sulla riga) e la risposta a una richiesta aperta viene notata: un vecchio pulsante Consenti o Nega per quella richiesta poi non scrive più niente. La sessione tiene le 200 righe di strumenti più recenti.

Una chiamata `Bash` che Claude Code ha spostato in background riporta un `backgroundTaskId`. aipager la segue allora come comando in background (solo quelli del ciclo principale: una chiamata con `agent_id` appartiene a quel subagent) finché non ne vede la fine: un `<task-notification>` con `<status>` (nel messaggio che sveglia Claude, oppure nella cronologia se arriva a metà di un'attività), un `TaskStop` o `KillShell` per il suo ID, la fine della sessione, oppure due ore (`AIPAGER_BG_SHELL_MAX_TRACK`, in secondi, predefinito 7200). La sua riga sulla scheda è in [comandi](commands.md#idle-responses).

Una chiamata `Agent` (o `Task`) fatta dentro un subagent (il suo evento porta `agent_id`) riporta l'ID dell'agente che ha avviato. aipager ricorda quale agente ha avviato quale, così distingue un agente che si è fermato per aspettare un suo agente da uno che ha finito (vedi `SubagentStart` / `SubagentStop` più sotto).

Dopo un `SendUserFile` che è stato eseguito, aipager copia subito i suoi file, una volta per chiamata e solo durante un'attività iniziata con un prompt, dalla lista che mostrava il suo `PreToolUse` (mai dal risultato dello strumento), controllando di nuovo ognuno; le copie partono dopo la risposta dell'attività, una volta trovata la chiamata nella cronologia che aipager si è annotato per l'attività, scritta durante quell'attività ([sicurezza](security.md#files-claude-sends), [comandi](commands.md#files-claude-sends)). Un'attività che Claude Code inizia senza hook (un messaggio che prende subito dopo, la risposta di un hook Stop) conta appena la cronologia mostra il suo inizio.

### <a id="notification"></a>`Notification`

aipager legge il `notification_type` di Claude Code:

- `permission_prompt`: un segnale di richiesta di permesso più lento e più vecchio. Mostra la richiesta solo quando non c'è nessuna richiesta aperta: se ce n'è una, è il dialogo che `PermissionRequest` ha già mostrato.
- `idle_prompt` mentre una richiesta di permesso o una domanda è in attesa: la sessione resta in attesa e la chat riceve `⬆️ jim · aspetta ancora la tua risposta più in alto`, una volta per attesa.
- `idle_prompt` negli altri casi: viene trattato come `Stop` (vedi sotto). Recupera un'attività il cui `Stop` è andato perso; una risposta già consegnata non viene mai rimandata.

### <a id="stop"></a>`Stop`

L'attività è finita. La sessione diventa pronta, la scheda di attività si fissa secondo il tuo aspetto dei messaggi e la risposta di Claude (da `last_assistant_message` dell'evento, altrimenti dalla cronologia) viene inviata. Poi partono i messaggi che erano trattenuti in attesa della fine dell'attività. Gli agenti in background e i comandi in background ancora in esecuzione tengono aperto il lavoro: la scheda resta come stato del lavoro e la risposta si chiude con lo stato dal vivo di quel lavoro: cosa sta facendo ogni agente e quanti comandi in background del lavoro sono in esecuzione (vedi [comandi → agenti ancora al lavoro quando parte la risposta](commands.md#agents-still-running-when-the-answer-goes-out)). L'hook prima cancella il segno di attività aperta, a meno che Claude non tenga un messaggio in coda che sta per diventare l'attività successiva.

### <a id="stopfailure"></a>`StopFailure`

L'attività è finita per un errore dell'API. La sessione diventa pronta e la risposta, se c'è, viene consegnata come con `Stop`; gli errori noti (server sovraccarichi, limite di richieste, …) sono spiegati in parole semplici.

### <a id="subagentstart--subagentstop"></a>`SubagentStart` / `SubagentStop`

Un subagent è partito o ha finito. Ogni subagent ha la sua riga nella scheda di attività, `🤖 <type> · <activity> · <elapsed>`, con le sue chiamate agli strumenti raccolte sotto (la più recente sempre visibile dopo la parte raccolta), e quando si ferma si fissa in `✅ 🤖 <type> · N strumenti usati · <elapsed>`. `SubagentStart` non dice quale agente l'ha avviato: aipager lo abbina alla chiamata `Agent` (`PreToolUse`) del suo tipo in attesa. Se tutte quelle chiamate vengono da un solo agente, il nuovo agente viene legato subito a quell'agente (la sua riga nella scheda diventa `🤖 pipeline-runner › ship-tester`, e nello stato sotto una risposta compare sotto quell'agente); se vengono da agenti diversi, non viene legato nulla finché il `PostToolUse` dell'agente che l'ha avviato non lo nomina. La descrizione di una chiamata si usa solo quando c'è esattamente una chiamata di quel tipo in attesa. Un subagent il cui `SubagentStop` non arriva mai viene tolto dopo 30 minuti di silenzio (`AIPAGER_SUBAGENT_SILENCE`, in secondi; nessun evento con il suo ID nel frattempo). Un agente al lavoro invia eventi degli strumenti ogni pochi secondi, quindi questo raccoglie solo gli agenti di cui si è perso il segnale di fine (un riavvio, un datagram perso); lo fa anche una singola chiamata a uno strumento che dura 30 minuti senza nessun evento con l'ID di quell'agente. `AIPAGER_SUBAGENT_TTL` è ancora accettato come vecchio nome della stessa impostazione. Gli eventi `SubagentStop` senza tipo e con un ID sconosciuto arrivano spesso e vengono ignorati.

Un agente in background che si ferma mentre un agente avviato da lui è ancora in esecuzione riceve anche lui un `SubagentStop`. aipager lo considera una pausa, non una fine: l'agente resta in esecuzione (la sua riga, il lavoro, lo stato sotto le risposte, la barra fissata). Il suo `SubagentStart` successivo, con lo stesso ID, è la sua ripresa e non aggiunge una seconda riga; anche qualsiasi chiamata a uno strumento fatta da lui conta come ripresa. Finisce quando si ferma senza più nessuno dei suoi agenti in esecuzione. Se non ha ripreso due minuti dopo la fine dell'ultimo di essi, non viene più contato come in esecuzione, ma non viene mai mostrato come finito. Gli agenti in esecuzione, anche quelli in attesa, restano dopo un riavvio di aipager.

Gli agenti fermati con i tasti di Claude Code che fermano gli agenti in background (ctrl+x ctrl+k, premuti due volte) non fanno scattare nessun `SubagentStop`. aipager li chiude da solo: subito quando il suo Ferma ha premuto quei tasti, quando la trascrizione di Claude Code riceve la sua riga `agents_killed` (letta al `UserPromptSubmit` dell'attività che Claude Code avvia poi da sé, il cui testo è una frase semplice senza involucro), oppure quando `background_tasks` di un evento `Stop` non elenca più agenti che un `Stop` precedente elencava. Le loro richieste aperte si chiudono (nessun hook lo dice), e un `SubagentStop` che arrivi più tardi per uno di loro viene ignorato. Quell'attività non viene presa per un messaggio digitato nel terminale quando è stato il Ferma di aipager a premere quei tasti, o non appena la trascrizione di Claude Code mostra che gli agenti sono stati fermati.

### <a id="precompact--postcompact"></a>`PreCompact` / `PostCompact`

Claude sta compattando il suo contesto (`trigger` `auto` o `manual`). La scheda di attività mostra `🔄 name · Compattazione della conversazione` (o, senza scheda, viene inviato un messaggio nuovo), così la pausa non sembra un crash. `PostCompact` chiude questo stato; la riga `📦 name · Conversazione compattata (prima: X%)` arriva dal `SessionStart` successivo, oppure dalla riga di stato se quello sfugge. La riga di stato di Claude Code segna 0% fino al messaggio successivo, quindi la nuova dimensione non si conosce ancora: dopo una compattazione tra un'attività e l'altra, la riga diventa `📦 name · Conversazione compattata: X% → Y%` appena la riga di stato la riporta. La riga di stato dà anche l'avviso `⚠️ name · Contesto usato: 82% - presto verrà compattato in automatico` all'80% di contesto (con un pulsante «Compatta ora»), una sola volta per conversazione: la compattazione non lo ripete, e una conversazione nuova (per esempio dopo `/clear`) lo riceve di nuovo.

### <a id="messagedisplay"></a>`MessageDisplay`

Il momento in cui il testo di Claude arriva sullo schermo. La cronologia si scrive solo dopo ogni giro di chiamate agli strumenti, quindi questa è l'unica fonte immediata di cosa sta dicendo Claude adesso ed è ciò che porta il suo racconto nella scheda di attività. Il testo di un subagent (qualsiasi evento con il suo `agent_id`) non viene mai preso come testo di Claude: non va nel racconto della scheda di attività, nella Mini App o nel log dell'attività, ma diventa l'attività più recente di quell'agente nello stato sotto una risposta. L'hook non aggiunge i dati della riga di stato a questo evento: Claude Code lo aspetta mentre disegna e scatta più volte per ogni messaggio.

### <a id="premodelswitch"></a>`PreModelSwitch`

Claude Code 2.1.251 e successivi lo emettono prima di cambiare modello. Il payload contiene `from_model`, `to_model`, `requested_model` e `source` (`command` quando è stato scritto `/model`, oppure `picker`, `sdk`). Di solito Claude Code chiede "Switch model?" prima di cambiare, perché la risposta successiva deve rileggere tutta la conversazione. Un hook che risponde `permissionDecision: "allow"` fa avvenire il cambio senza quella domanda.

`aipager-hook` risponde `allow` solo ai cambi che aipager stesso ha scritto, dal selettore di modello della Mini App o dalla tastiera dei modelli. Ogni altro cambio non riceve risposta e Claude Code chiede come al solito. Questo comprende un `/model` scritto dal proprietario del bot nel terminale, il selettore di modello e i cambi fatti tramite l'SDK.

aipager riconosce i propri cambi grazie a un file segno. Subito prima di scrivere `/model <name>`, aipager scrive `aipager-modelswitch-<session>.json`:
- sta nella stessa cartella del socket di controllo (di solito `$XDG_RUNTIME_DIR`);
- è scritto in modo atomico con permessi 0600;
- contiene il modello e l'ID della sessione di Claude;
- scade dopo 30 secondi.

L'hook risponde `allow` solo quando valgono tutte queste condizioni:
- il segno è un file tuo e non un link simbolico;
- non è ancora scaduto;
- è per la stessa sessione di aipager e lo stesso ID di sessione di Claude;
- il modello che registra è uguale a `requested_model` o a `to_model`, senza distinguere maiuscole e minuscole;
- `source` è `command`.

Poi l'hook prende il segno con una rinomina atomica, così funziona una volta sola. Il controllo legge solo un file locale, senza socket e senza attese. Questo evento non viene inoltrato ad aipager.

Claude Code 2.1.101 e successivi ignorano gli eventi hook che non conoscono, quindi le versioni più vecchie senza `PreModelSwitch` non ne risentono. Un caso può ancora mostrare la domanda: cambiare verso `opusplan` o da `opusplan` può far chiedere a Claude Code due volte all'hook, e solo la prima richiesta trova il segno, quindi Claude Code chiede "Switch model?" nel terminale.

## <a id="the-status-line"></a>La riga di stato

Non è un evento hook: Claude Code esegue il comando `statusLine` `aipager-statusline` (`aipager.dtach.statusline_notify:main`) ogni volta che ridisegna la riga di stato. Il comando:

- scrive il JSON di stato di Claude Code in `/tmp/claude-status-<session>.json` (l'hook lo legge per `sl_tokens`, e lo leggono anche `aipager status` e aipager);
- invia ad aipager un datagram `statusline` con modello, percentuale di contesto, costo, numero di token e righe aggiunte e tolte, che servono per la scheda di attività, `/status`, la Mini App, il controllo che un cambio di modello sia andato a buon fine e l'avviso sul contesto;
- stampa la riga di stato del terminale: `[name] Opus 5.5 | 42% ctx | $1.23`.

## <a id="aipagers-own-datagrams"></a>I datagram di aipager

Oltre agli eventi hook, questi programmi di supporto mandano ad aipager alcuni messaggi propri sullo stesso socket:

| Messaggio | Chi lo invia | Quando |
|---|---|---|
| `queue_pickup` | l'hook | A `UserPromptSubmit`: quali messaggi di Telegram ha preso Claude. |
| `safety_blocked` | l'hook | A `PreToolUse`, quando il controllo di sicurezza ha negato la chiamata. |
| `send_files_refused` | hook | A `PreToolUse`, quando file che Claude voleva mandare sono stati rifiutati: ogni file e il motivo, per il log di audit. |
| `permission_reply_timeout` | l'hook | Un'attesa di `PermissionRequest` è finita senza tocchi. |
| `statusline` | la riga di stato | A ogni ridisegno della riga di stato. |
| `hook_memory_cap_hit` | l'hook, la riga di stato | Il processo ha raggiunto il limite di memoria. |
| `report_error` | l'hook, i comandi `aipager` | C'è stato un errore: solo il tipo di errore e il punto del codice di aipager, mai il contenuto dell'evento, per le [segnalazioni di problemi](problem-reports.md). |

## <a id="the-permission-flow"></a>Il percorso di una richiesta di permesso

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

Senza un tocco entro 20 secondi, l'hook non stampa niente e Claude Code mostra il suo dialogo; un tocco successivo scrive la risposta in quel dialogo tramite il terminale della sessione (`dtach -p`). In entrambi i casi il tocco viene registrato nel log di audit e pubblicato sotto la scheda (`✅ jim · Consentito · @alice · Bash: ls -la /tmp`).

## <a id="see-also"></a>Vedi anche

- [Architettura](architecture.md): dove sta il ricevitore degli hook.
- [Comandi del bot → richieste di permesso](commands.md#permission-prompts): il lato che vedi tu.
- [Modello di sicurezza](security.md): cosa copre il controllo di sicurezza e cosa no.
