# Risoluzione dei problemi

<!-- i18n-source: docs/troubleshooting.md @ bb690d5 -->

> Questa pagina è tradotta dalla pagina in inglese; se le due versioni non coincidono, vale quella in inglese. ([English](../../troubleshooting.md))

I guasti più comuni e come si risolvono. Nel dubbio, esegui:

```sh
aipager doctor
```

Esegue in ordine tutti i controlli qui sotto e per ogni errore stampa il suggerimento standard per risolverlo. Codice: `aipager/doctor.py`.

## <a id="another-aipager-daemon-already-owns-the-socket"></a>«Another aipager daemon already owns the socket»

Due aipager non possono condividere un socket di controllo. Questo errore vuol dire che un aipager è ancora in esecuzione e risponde su quel socket (il socket è `$XDG_RUNTIME_DIR/aipager.sock`, oppure `/tmp/aipager.sock` quando `$XDG_RUNTIME_DIR` non è impostato). Un file di socket lasciato da un aipager andato in crash non è un problema: all'avvio successivo viene cancellato e ricreato. Prima ferma l'aipager in esecuzione:

```sh
aipager service stop          # se gira come servizio
pkill -f 'aipager start'      # altrimenti
```

Se il controllo del socket non lo trova, `aipager start` si rifiuta comunque con `aipager is already running (pid=N)` e indica il suo file di lock (`~/.local/share/aipager/daemon.lock`): ferma quel processo allo stesso modo.

## <a id="telegram-bot-doesnt-respond"></a>Il bot di Telegram non risponde

1. `aipager status`: se la riga di aipager dice `not running`, avvialo.
2. `aipager doctor`: i controlli `token_valid` e `chat_reachable` interrogano l'API di Telegram da cima a fondo e mostrano l'errore esatto.
3. Chat sbagliata: esegui `aipager config` e scegli **Test bot reachability**. Per correggere una chat privata sbagliata, aggiungi quella giusta con **Add a DM scope** (vedi [come trovare gli ID](groups.md#finding-ids-auto-detect)), poi togli quella sbagliata in **Edit a scope**.
4. Il bot non ha mai ricevuto `/start`: apri il bot in Telegram e tocca Start una volta.

## <a id="busy-cards-got-slower"></a>Le schede di attività sono diventate più lente

Telegram permette circa un messaggio al secondo in una chat (e 20 al minuto in un gruppo). Conta ogni messaggio e ogni modifica. aipager dà a ogni chat un suo budget di circa 1 chiamata al secondo, con un piccolo margine per i picchi, e le schede di attività di quella chat lo **condividono**. Anche l'indicatore «sta scrivendo…» rientra in questo budget, come la cosa **meno importante** che la chat invia (vedi «L'indicatore di scrittura» più sotto).

La parte dell'indicatore viene messa da parte per prima (una chiamata ogni 4,5 secondi finché l'attività in corso più vecchia della chat ha meno di dieci minuti, meno spesso man mano che invecchia; vedi «L'indicatore di scrittura») e le schede si dividono il resto. Quindi una sola scheda in una chat privata si aggiorna circa ogni 4,8 secondi; con due sessioni al lavoro nella stessa chat, ogni scheda circa ogni 9,7 secondi; con tre, circa ogni 14,5. **Una scheda più lenta è il budget che fa il suo lavoro, non un bug.** Le risposte, le repliche e i riscontri dei pulsanti non vengono mai rallentati per fare spazio a una scheda: hanno sempre una quota riservata, e una modifica della scheda che non trova posto viene semplicemente saltata e ritentata al giro dopo, invece di mettersi in coda davanti alla tua risposta.

In un **gruppo** il limite è di 20 chiamate al minuto, qualunque sia il numero di sessioni, e l'indicatore da solo ne userebbe due terzi. Le schede del gruppo insieme mantengono un aggiornamento ogni 10 secondi (11 s con una sola scheda) e l'indicatore prende il resto: per questo in un gruppo può sparire per un secondo o due ogni tanto.

Per rendere le schede più veloci: esegui meno sessioni insieme in ogni chat, oppure dai alle più impegnate una chat tutta loro (`aipager config`). Puoi anche regolare `STREAM_EDIT_INTERVAL` (predefinito `1.2`, il ritmo mentre la scheda riceve testo) e `BUSY_EDIT_INTERVAL` (predefinito `3.0`, quando è tranquilla), come variabili d'ambiente di aipager (vedi [impostazioni dall'ambiente](commands.md#settings-in-the-environment)). Impostarle *sotto* il minimo della chat non fa danni e non cambia niente: vince il minimo, ed è proprio questo che tiene la chat sotto il limite di Telegram qualunque valore imposti.

### <a id="a-long-turns-card-refreshes-less-often"></a>La scheda di un'attività lunga si aggiorna meno spesso

Il ritmo qui sopra vale per i primi due minuti di un'attività. Dopo, la scheda **rallenta man mano che l'attività invecchia**, e dal decimo minuto il suo orologio avanza a passi di 30 s, in sincronia con l'aggiornamento, così non sembra mai bloccata:

| durata dell'attività | la scheda si aggiorna al massimo ogni | l'orologio mostra |
|---|---|---|
| 0–2 min | come sopra (circa 4,8 s per una scheda in una chat privata) | `0:45` |
| 2–10 min | 10 s | `4:10` |
| 10–60 min | 30 s | `23:00`, `23:30` |
| oltre 60 min | 30 s | `1:23:00`, `1:23:30` |

L'orologio è arrotondato per difetto al passo di 30 s e gli aggiornamenti sono distanti almeno 30 s, a volte un po' di più. Così ogni tanto la scheda salta un passo: un aggiornamento a 23 min 29 s mostra `23:00` e, se il successivo arriva a 24 min 1 s, mostra `24:00`, quindi `23:30` viene saltato.

Una nuova riga di strumento o una nuova frase aspetta l'aggiornamento successivo; un **cambio di stato** (la sessione che passa da al lavoro ad aspettare un agente in background, e viceversa) si vede subito, al massimo una volta ogni 10 secondi. Le righe degli agenti al lavoro e la riga di attesa usano gli stessi passi. La scheda finita mostra la durata dell'attività in secondi esatti (`23:17`).

È questo che impedisce a un'attività di quattro ore di modificare un messaggio migliaia di volte: il 2026-09-23 una scheda ha fatto proprio questo e la chat è stata bloccata per sette ore. Per tenere il ritmo veloce per tutta l'attività, disattiva **`/settings` → ⏱ Aggiorna scheda nelle attività lunghe** (per la chat, o per singola sessione dalla Mini App o da `👤 Preferenze di ogni sessione`).

### <a id="the-typing-bubble"></a>L'indicatore di scrittura

L'indicatore «sta scrivendo…» viene inviato da **un solo** ciclo per chat, qualunque sia il numero di sessioni al lavoro (in ogni app di Telegram l'indicatore è della chat intera), ogni `TYPING_INDICATOR_INTERVAL` secondi (predefinito 4,5: Telegram toglie lo stato di scrittura dopo 5; `0` lo disattiva). Nel budget della chat conta come la cosa meno importante che la chat invia:

- **rallenta man mano che l'attività invecchia**, in base all'età dell'attività in corso più vecchia della chat: ogni 4,5 s per i primi dieci minuti (un indicatore sempre acceso), poi ogni 15 s; Telegram lo mostra per 5 s, quindi l'indicatore di un'attività più vecchia lampeggia invece di restare acceso. A 4,5 s da solo farebbe 800 chiamate all'ora e il limite orario (più sotto) lo spegnerebbe per lunghi tratti; disattivare **⏱ Aggiorna scheda nelle attività lunghe** lo tiene a 4,5 s;
- **si vede dall'inizio di ogni attività**; una scheda appena nata si aggiorna un po' più piano per fargli spazio: la parte dell'indicatore viene messa da parte prima che le schede si dividano il resto (sopra), quindi insieme non superano mai i limiti della chat;
- non prende mai una quota che serve a una modifica della scheda: un indicatore che tocca subito dopo una modifica della scheda aspetta mezzo secondo (`TYPING_RETRY_WAKE`) e riprova, quindi arriva al massimo un secondo o due in ritardo, mai saltato;
- è la prima cosa che si toglie quando il volume orario della chat sale (più sotto);
- un 429 sull'indicatore stesso blocca solo l'indicatore, per il tempo chiesto da Telegram (le schede vanno avanti), ma fa partire il periodo di avviso di sei ore della chat (il ritmo è limitato a 0,5 chiamate/s, quindi rallentano anche le schede);
- un **blocco** sull'indicatore (un `retry_after` oltre il limite qui sotto) ferma gli invii alla chat proprio come un blocco su qualsiasi altra chiamata: non ci entra più niente, e la risposta dell'attività viene trattenuta e consegnata quando il blocco finisce.

Prima era gratis (un ciclo per ogni sessione al lavoro, fuori da ogni budget), finché il 2026-09-23 due sessioni in una chat privata l'hanno inviato circa 5.400 volte in tre ore e Telegram ha limitato l'indicatore stesso.

## <a id="the-bot-slowed-down-a-rate-limit-429"></a>Il bot ha rallentato: un limite di frequenza (429)

Se Telegram limita comunque una chat, risponde a una chiamata con `429` e un `retry_after` di qualche secondo. aipager smette di chiamare quella chat esattamente per quel tempo, poi rifà una volta l'invio rimandato e **raddoppia l'intervallo delle schede di quella chat** (fino a ×8). Ogni minuto tranquillo lo dimezza, fino a tornare normale.

Cosa vedi:

- `aipager status` (e la riga `aipager daemon` di `aipager doctor`) mostra **`Telegram flood backoff ×4 (chat …), last 429 12 s ago`**.
- Esattamente una riga in `aipager logs`, senza traceback: `flood: chat … 429 retry_after=5s → cadence ×2`.
- Niente viene fermato e **nessuna risposta va persa**: viene rimandata, non scartata.
- Il **ritmo di invio guadagnato** dalla chat si dimezza e la chat entra in un **periodo di avviso di sei ore** (`FLOOD_WARNING_HOURS`): il suo ritmo non può salire oltre 0,5 chiamate/s (`FLOOD_WARNED_CEILING`) e risale piano. `aipager status` lo mostra: `Telegram chat 123: rate 0.25/s (ceiling 0.50/s), 212/1200 calls in the last hour (as of 18s ago), 7/30 in the last minute - warning regime, 5h 52m left`.

Non devi fare niente. Si sistema da solo. La riga del rallentamento sparisce dopo che la chat è rimasta tranquilla un minuto o due; il ritmo risale di 0,1 chiamate/s per ogni intervallo tranquillo (un minuto di solito, 36 minuti durante il periodo di avviso) e il tetto pieno torna quando il periodo finisce. Per tutto il tempo `aipager doctor` tiene verde la riga di aipager: una chat che rallenta è il funzionamento normale. (Prima un 429 veniva dimenticato in circa cinque minuti. L'unico avviso di Telegram prima del blocco del 2026-09-23 era arrivato 3 h 23 min prima, e la chat era tornata alla velocità piena pochi minuti dopo.)

### <a id="the-hourly-budget"></a>Il budget orario

Ogni chat ha anche un'**ora** mobile: al massimo `FLOOD_HOURLY_MAX` (1200) chiamate in qualsiasi intervallo di 60 minuti, che siano indicatori di scrittura, modifiche delle schede o risposte; solo le reazioni sono escluse. Le ultime 120 (`FLOOD_HOURLY_ESSENTIAL_RESERVE`) sono tenute per risposte, repliche e richieste:

- a 810 chiamate nell'ora l'indicatore di scrittura si ferma (torna sotto 648);
- a 1080 la chat entra in **modalità minima** (più sotto) finché l'ora non scende a 864;
- le risposte non vengono mai rifiutate per il limite orario; se ogni tanto superano 1200, il log lo dice una volta all'ora.

`aipager status` mostra le chiamate della chat nell'ultima ora rispetto al suo budget. Il numero viene dal file di stato, che aipager riscrive al massimo una volta al minuto mentre ci sono chiamate, quindi è indicato con la sua età. Due sessioni impegnate che scrivono in una chat privata per ore restano ben sotto il budget (l'incidente del 2026-09-23 rigiocato: circa 900 chiamate nell'ora più intensa, contro circa 3.400 stimate per lo stesso lavoro senza budget).

### <a id="the-earned-rate-and-why-your-cards-may-be-slower-than-they-were"></a>Il ritmo guadagnato, e perché le schede possono essere più lente di prima

Dalla 0.7.13 il ritmo di invio costante di ogni chat è **imparato, non presunto**. Telegram non pubblica il limite che vale davvero per il tuo bot (dipende dalla storia recente del tuo account), quindi aipager fa partire ogni chat da 0,5 chiamate/s, aggiunge 0,1 per ogni minuto tranquillo fino a un tetto di 1/s, lo dimezza a ogni 429 e lo porta a 0,05 dopo un blocco. Così un bot appena bloccato invia con molta più prudenza di uno che non lo è mai stato, e riguadagna velocità nelle ore successive.

Se il ritmo di una chat scende sotto 0,2 chiamate/s, oppure la parte del budget orario per schede e indicatori è finita, la chat entra in **modalità minima**: le schede di attività smettono di muoversi e mostrano una sola riga fissa `⏳ x1 · al lavoro (per ora la scheda non si aggiorna)`, l'indicatore di scrittura si ferma e la barra di stato fissata mostra `⏸ per ora le schede non si aggiornano (limite orario di Telegram)` (oppure `(limite di Telegram)` quando a portarla lì è stato il ritmo) e per il resto non cambia finché la modalità non finisce. **Risposte, repliche e richieste di permesso continuano ad arrivare**: è proprio questo lo scopo. La scheda è circa il 95% di quello che invia questo bot e la risposta circa il 5%, quindi sotto pressione rinuncia ai pixel, non al lavoro. `aipager doctor` segnala la modalità minima come avviso, così una scheda in pausa non viene mai scambiata per una rotta; finisce da sola quando il ritmo si riprende.

## <a id="the-bot-went-quiet-flood-control"></a>Il bot è diventato muto: il blocco di Telegram (flood control)

Un `retry_after` oltre `TELEGRAM_MAX_RETRY_AFTER` (predefinito 90 s) non è un limite di frequenza: è un **blocco**, e può durare ore. Dalla chat sembra che il bot sia morto: i messaggi arrivano ancora a Claude e le sessioni continuano a lavorare, ma non torna nessuna risposta.

Cosa vedi:

- `aipager status` (e la riga `aipager daemon` di `aipager doctor`) mostra **`Telegram flood-muted until HH:MM (chat …)`**.
- Una riga in `aipager logs`: `Telegram flood control — chat … muted for Ns (until HH:MM) …`, poi silenzio per quella chat. Più tardi, una riga `flood mute on chat … lifted`.
- `aipager status` mostra anche lo stato completo della chat: `Telegram chat 123: rate 0.05/s (ceiling 0.50/s), 431/600 calls in the last hour (as of 12s ago) - MINIMAL MODE, card updates paused - flood-muted until 09:41 - warning regime, 5h 59m left (1 ban(s) in the last 7 days)`.

aipager ferma gli invii alla chat esattamente per il tempo chiesto da Telegram e **non fa nessuna chiamata, di nessun tipo**: niente risposte, modifiche delle schede di attività, allegati, reazioni, indicatori di scrittura, avvisi dopo il tocco di un pulsante, e nemmeno le risposte che i tuoi comandi avrebbero prodotto. Ognuna di queste sarebbe una nuova violazione che allunga il blocco; il 2026-09-15 una richiesta inviata durante un blocco attivo ha trasformato un blocco di 21 minuti in uno di 9,5 ore. Per questo un comando scritto durante il blocco non riceve nessuna risposta, e **nemmeno il tocco di un pulsante viene confermato**: fino alla 0.7.12 l'avviso partiva lo stesso, con l'idea che Telegram lo contasse a parte. Per il ritmo è così; per i blocchi no. Le altre chat non ne risentono.

**Scrivere al bot durante un blocco è sicuro, e non si perde niente.** Niente di quello che fai nella chat fa chiamare Telegram al bot mentre gli invii sono fermi, quindi non puoi allungare il blocco:

- Un messaggio che invii (testo, vocale, un file, un template rapido) arriva comunque a Claude e la sessione ci lavora. La sua scheda di attività viene trattenuta, non scartata: se l'attività è ancora in corso quando il blocco finisce, la scheda compare allora. Dopo un blocco la chat resta per un po' in modalità minima, quindi la scheda può arrivare qualche minuto dopo la fine del blocco. Se l'attività è finita durante il blocco, ricevi la risposta senza scheda.
- **🔄 Riprova** durante un blocco non fa niente. Il messaggio non viene reinviato e il pulsante resta, così puoi toccarlo di nuovo dopo il blocco. Fino alla 0.7.14 compresa il messaggio andava a Claude mentre il messaggio di errore e il suo pulsante restavano, quindi un secondo tocco lo inviava di nuovo.
- Anche la tastiera principale viene trattenuta, compresa quella che invia un riavvio (fino alla 0.7.14 andava persa finché qualcos'altro non la aggiornava). Viene inviata dopo la fine del blocco.

Dopo la fine del blocco le cose arrivano in quest'ordine: le risposte trattenute, poi le schede delle attività ancora in corso, poi la tastiera. Una scheda che deve aspettare la fine della modalità minima arriva dopo la tastiera.

**Le risposte non vanno perse.** Una risposta prodotta mentre la chat ha gli invii fermi viene trattenuta e consegnata entro un paio di secondi dalla fine del blocco, con la prima riga `⏳ arrivato in ritardo: trattenuto 42 min per un limite di Telegram`. Non serve chiedere di nuovo; e se hai chiesto di nuovo, arrivano tutte e due le risposte, la più vecchia per prima: un blocco che dura ore copre più attività e la risposta di ognuna è tua. (Le risposte trattenute stanno in memoria: se riavvii aipager mentre ce ne sono in attesa, vanno perse; il log di chiusura dice quante. Una chat ne tiene al massimo 20 e niente più vecchio di 24 ore; tutto quello che viene scartato per uno dei due motivi lascia nel log un avviso che dice cos'era.)

**Un blocco rende la chat più lenta dopo, non più veloce.** Appena il blocco viene registrato, il ritmo imparato dalla chat scende al minimo e il blocco viene contato; le ore di invii fermi non fanno guadagnare niente, la risalita riparte dal momento in cui il blocco finisce, e per **sette giorni** (`FLOOD_BAN_MEMORY_DAYS`) il tetto del ritmo *e* il budget orario della chat vengono divisi per uno più il numero di blocchi di quella settimana: un blocco li dimezza entrambi, due li riducono a un terzo. `aipager status` mostra tutto questo. (Prima questa memoria durava 24 ore e dimezzava solo il tetto; una chat bloccata il 2026-09-19 era tornata alla velocità piena, e bloccata per sette ore, il 2026-09-23.) Cinque o più blocchi in una settimana tengono la chat in modalità minima finché non scadono: le risposte continuano ad arrivare, le schede restano in pausa.

Cosa NON fare:

- Non riavviare aipager per «aggiustarlo». Lo stop degli invii finisce da solo all'ora indicata. Dalla 0.7.13 un riavvio non dimentica più il blocco (la scadenza, il ritmo guadagnato dalla chat e la sua storia di blocchi sono salvati in `~/.claude/aipager-flood-state.json` e ripristinati all'avvio, così come le chiamate dell'ultima ora e il periodo di avviso della chat), quindi riavviare non lo allunga più. Ma butta via le risposte ancora trattenute e non aggiusta niente.
- Non abbassare `TELEGRAM_MAX_RETRY_AFTER` sotto i 90 s predefiniti sperando di riprovare prima: tutto quello che supera quel limite è un blocco, non un limite di frequenza.

Per evitarlo: esegui meno sessioni insieme in ogni chat, oppure dai alle più impegnate una chat tutta loro (`aipager config`).

## <a id="a-session-dropped-off-the-pinned-status-bar-it-is-gone"></a>Una sessione è sparita dalla barra di stato fissata (è GONE)

Il processo dtach di quella sessione è terminato (riavvio del computer, `pkill claude`, qualcuno ha scritto `exit` nella sessione dtach collegata). Ricrea la sessione: la conversazione è ancora nella sua cartella `~/.claude/projects/...`.

La strada più semplice è `/resume <label>` in Telegram (o `/resume` da solo per scegliere da un elenco): riavvia la sessione e riprende la conversazione. Dalla riga di comando:

```sh
aipager resume <label>             # la stessa cosa, dal terminale
aipager session <label>            # oppure una sessione nuova, senza cronologia
aipager session <label> --continue # dtach nuovo, l'ultima conversazione della cartella
```

## <a id="a-permission-prompt-with-no-news-for-5-minutes"></a>Una richiesta di permesso senza notizie da 5 minuti

Se una sessione aspetta una richiesta di permesso o una domanda e Claude Code non dà notizie per 5 minuti, aipager legge la conversazione salvata della sessione. Solo se questa mostra che la richiesta è finita (risposta nel terminale, e la notizia è andata persa) aipager smette di aspettarla e conta di nuovo la sessione come al lavoro. Altrimenti la richiesta resta aperta per tutto il tempo che serve: la scheda e la barra fissata continuano a dire che la sessione ha bisogno della tua risposta, non arriva nessuna nota "ancora al lavoro", e i tuoi messaggi restano trattenuti finché non rispondi (vedi [testo](commands.md#text)). Un Claude Code che si chiude con la richiesta aperta termina la sessione. I pulsanti della richiesta rispondono ancora al dialogo, se è ancora aperto (vedi [risposte a riposo](commands.md#idle-responses)).

Per cambiare l'attesa:

```sh
AIPAGER_INTERACTIVE_TIMEOUT=300 aipager start    # in secondi
```

## <a id="voice-extra-wont-install-via-telegram"></a>La funzione vocale non si installa da Telegram

Il pulsante `[📦 Installa la funzione vocale]` esegue l'installatore e modifica il suo messaggio ogni pochi secondi mentre lavora (`ancora in corso… (25 s)`). Se l'installazione fallisce, il messaggio mostra il codice di uscita e gli ultimi 500 caratteri dell'output dell'installatore. Cause comuni:

- Rete: pip non riesce a raggiungere PyPI. Controlla la connessione dal computer su cui gira aipager.
- Disco: `~/.cache/huggingface/` ha bisogno di circa 74 MB per il modello e la cache dei wheel di pip di altri 250 MB circa. Prima libera spazio.
- Permission denied: venv in sola lettura. Passa a `uv tool install`, che usa sempre un venv del tuo utente.

Se il bot ha perso la connessione a metà installazione, l'installazione sul computer di solito è arrivata in fondo: riavvia aipager e riprova con un vocale.

## <a id="open-app-shows-open-this-page-from-the-telegram-app-to-sign-in"></a>Aprendo l'app compare «Apri questa pagina dall'app di Telegram per accedere.»

La pagina si è caricata, ma lo script Mini App SDK di Telegram (quello che produce `initData`) no, quindi la pagina non ha niente con cui accedere e non fa nessuna chiamata all'API. Il messaggio sembra dare la colpa a te; la causa è la rete.

Le versioni recenti di aipager scaricano quello script da sole e lo servono dall'origine della pagina stessa (`/telegram-web-app.js`), così al telefono basta raggiungere il server che ha consegnato la pagina, non anche `telegram.org`. Se lo vedi su una versione vecchia, aggiorna.

Poi, in ordine:

1. Guarda il codice della pagina. Se il suo tag `<script>` punta a `https://telegram.org/js/telegram-web-app.js`, aipager non è ancora riuscito a scaricare lo script e la pagina ripiega caricandolo da Telegram, che è proprio il caso che non funziona su un telefono che non raggiunge `telegram.org`. aipager scarica lo script quando parte il server della Mini App e riprova al massimo una volta al giorno, quindi riavviare aipager riprova subito; `journalctl --user -u aipager | grep "webapp sdk"` dice cosa è andato storto (di solito un `telegram.org` bloccato o filtrato).
2. Se il tag punta a `/telegram-web-app.js`, controlla che aipager lo serva: `curl -sI http://127.0.0.1:8765/telegram-web-app.js` sul computer di aipager (cambia la porta se l'hai cambiata) deve rispondere `200` con `Content-Type: application/javascript`. La copia sta sotto `~/.local/share/aipager/webapp-sdk/`.
3. Hai aperto l'URL in un browser normale invece che dal pulsante `/app` o dal pulsante del menu del bot: allora il messaggio è quello previsto. L'SDK produce `initData` solo dentro Telegram.
4. La pagina è rimasta aperta più di cinque minuti: `initData` scade e la pagina chiede di essere riaperta. Tocca di nuovo `/app`.
5. Hai toccato `📱 App` su una tastiera di una versione precedente di aipager. Telegram non dà `initData` a una Mini App aperta da un pulsante della tastiera, perciò la tastiera di aipager non ha più quel pulsante. Manda `/start` per avere la tastiera attuale e apri l'app dal pulsante del menu o con `/app`.

## <a id="pyexpat-_xml_setalloctrackeractivationthreshold-on-brew-install"></a>`pyexpat _XML_SetAllocTrackerActivationThreshold` nell'installazione con brew

Il pacchetto `python@3.12` di Homebrew è stato compilato con una libexpat più nuova di quella del tuo sistema, quasi sempre perché **Xcode e Command Line Tools non sono aggiornati** su macOS Tahoe (26.x). Di solito lo dice anche l'output di brew. Due soluzioni:

- **Usa invece `uv tool install aipager`**. uv porta con sé il proprio python ed evita del tutto il problema. È la strada consigliata su macOS; vedi il [README](README.md#install).
- **Aggiorna Xcode e Command Line Tools**:
  ```sh
  sudo rm -rf /Library/Developer/CommandLineTools
  sudo xcode-select --install
  ```
  Oppure apri Xcode nell'App Store e aggiornalo all'ultima versione.

## <a id="modulenotfounderror-aipager"></a>«ModuleNotFoundError: aipager»

Il tuo programma aipager punta a un Python in cui aipager non è più installato (spesso dopo aver cancellato un venv). Reinstallalo:

```sh
uv tool install --reinstall aipager
# oppure l'installatore che hai usato all'inizio: pipx, brew, pip
```

## <a id="the-session-list-was-lost-or-damaged"></a>L'elenco delle sessioni è andato perso o è rovinato

aipager tiene le sue sessioni in `~/.claude/aipager-sessions.json`, riscritto in modo atomico (un file nuovo, poi una rinomina), così una scrittura interrotta lascia intero il file vecchio. Se il file manca comunque, non si legge o non è un JSON valido, aipager scrive nel log che riparte da zero (un avviso quando il file c'era ma non si poteva leggere) e parte con un elenco vuoto; non si ferma. Di questo file non ci sono copie di sicurezza. Non si perde niente di importante:

- le sessioni in esecuzione vengono ritrovate al primo controllo, dai loro socket dtach (`/tmp/claude-dtach-*.sock`);
- ogni conversazione è ancora nella cartella `~/.claude/projects/...` di Claude Code, quindi `aipager session <name> --continue` nella cartella di quella sessione la riprende (`--resume` ti fa scegliere);
- quello che si perde è l'elenco delle sessioni terminate (`/resume`), le schede che erano aperte e il resto di quello che aipager ricordava di ogni sessione.

Se aipager continua a fallire all'avvio, `aipager logs` (o `journalctl --user -u aipager`) dice perché; sposta il file da un'altra parte (`mv ~/.claude/aipager-sessions.json ~/aipager-sessions.json.old`) per escluderlo.

## <a id="a-session-launch-fails"></a>L'avvio di una sessione fallisce

Un avvio rifiutato da dtach mostra in chat il motivo in una riga (il socket esiste già, la shell non è eseguibile, nessun pseudo-terminale, nome troppo lungo, permesso negato, cartella mancante); il messaggio di dtach, percorsi compresi, è in `aipager logs`.

## <a id="aipager-doctor-check-list"></a>Elenco dei controlli di `aipager doctor`

L'ordine conta: ogni controllo dà per scontato che i precedenti siano passati. Un controllo che va in crash per un ambiente inatteso compare come una sola riga ⚠ con il nome del controllo e l'errore; gli altri controlli girano comunque.

| Controllo (chiave `--json`) | Cosa verifica | Suggerimento |
|---|---|---|
| `config_parses` | `aipager.yaml` e `policy.yaml` si leggono; gira per primo, perché ogni controllo successivo legge la configurazione | l'errore indica il file e la riga |
| `config` | `~/.config/aipager/aipager.yaml` esiste e contiene un token e una chat | `aipager config` |
| `token_valid` | Il token funziona con `getMe` di Telegram | riesegui `aipager config` |
| `chat_reachable` | Telegram conosce la chat configurata (`getChat`; non invia niente) | apri il bot, tocca Start |
| `team` | Le chat e i membri (o un vecchio `team.yaml`): dice se è uso personale o in team e segnala un vecchio `team.yaml` rovinato o non coerente | `aipager config` |
| `role_shell_access` | Avvisa una volta per ogni ruolo di un membro che può eseguire Bash (le sue regole di sicurezza sono al meglio delle possibilità, non un confine; vedi [gruppi](groups.md#roles)) | non dare Bash a quel ruolo in `policy.yaml`, oppure accettalo |
| `peer_messages` | Messaggi tra sessioni di Claude: dove si tengono le loro ricevute e se quella cartella è privata, se questo sistema può controllare l'ora di avvio di chi invia, e un avviso per ogni ruolo a cui una regola permette SendMessage (vedi [sicurezza](security.md#messages-between-claude-sessions)) | togli una cartella delle ricevute condivisa (aipager la ricrea), oppure non dare SendMessage a quel ruolo |
| `claude` | Trova il programma `claude` come fa ogni avvio (`claude_path` in `aipager.yaml` o `$AIPAGER_CLAUDE_BIN` vincono subito; altrimenti il più recente tra `~/.local/bin`, PATH e Homebrew) ed elenca ogni ALTRA installazione trovata | installa Claude Code, oppure imposta `claude_path` / `AIPAGER_CLAUDE_BIN` |
| `claude_auth` | Prova `claude auth status` nello stesso ambiente che riceve una sessione vera. **Non dà mai FAIL**: «non hai fatto l'accesso» e «la prova stessa non è riuscita» vengono segnalati in modo distinto, e nessuno dei due impedisce l'avvio di una sessione | `claude auth login`, oppure imposta una chiave API / `CLAUDE_CODE_OAUTH_TOKEN` |
| `dtach` | Il programma `dtach` è stato trovato (il `dtach-bin` di aipager, o uno nel PATH) | `uv tool install --reinstall aipager` |
| `hook_scripts` | `aipager-hook` e `aipager-statusline` sono nel PATH | `uv tool install --reinstall aipager` |
| `settings_json` | `~/.claude/settings.json` ha collegati gli hook di aipager | `aipager config` |
| `daemon` | aipager è in esecuzione e il suo socket risponde; mostra anche lo stato dei limiti di Telegram (vedi [sopra](#the-bot-slowed-down-a-rate-limit-429)) | `aipager start` |
| `service_installed` | Facoltativo: il file del servizio esiste | `aipager service install` |
| `service_unit_path` | Solo Linux: l'`Environment=PATH=` del servizio *installato* contiene davvero la cartella del programma claude trovato, letto come testo, perché doctor non vede il PATH di systemd dalla shell interattiva del proprietario del bot | `aipager service install --yes` |
| `miniapp` | Se la Mini App può partire quando è attiva. **Non dà mai FAIL**: la Mini App è facoltativa | la soluzione che indica |

`aipager doctor --json` stampa gli stessi controlli, in quest'ordine, con queste chiavi (vedi [comandi](commands.md#aipager-doctor---json)).

Esegui `aipager doctor --fix` per trovare e copiare in modo interattivo una credenziale di Claude in `daemon.env` (anche quando il file c'è ma non ne contiene nessuna), oppure per fissare `claude_path` quando si trova più di un'installazione. Agisce sempre solo dopo aver chiesto. Per un servizio il cui PATH non contiene la cartella di claude, esegui `aipager service install --yes`.

## <a id="the-daemon-cant-find-claude-or-picks-the-wrong-install"></a>aipager non trova `claude`, o sceglie l'installazione sbagliata

Prima sei punti diversi cercavano `claude` ognuno per conto suo e potevano non essere d'accordo. Ora passano tutti da un solo cercatore (`aipager/claude_resolve.py`). `claude_path` in `aipager.yaml`, poi `$AIPAGER_CLAUDE_BIN`, vincono subito se funzionano. Altrimenti viene controllato ogni `claude` trovato in `~/.local/bin`, nel `$PATH` e nelle cartelle di Homebrew, e vince la versione più recente (quindi riordinare il `$PATH` non cambia la scelta quando l'altra installazione è più recente). Esegui `aipager doctor` per vedere esattamente quale ha scelto e cos'altro ha trovato; se vince quella sbagliata, fissa quella giusta:

```sh
aipager doctor --fix        # scelta interattiva tra le installazioni trovate
# oppure a mano:
echo 'claude_path: /home/you/.local/bin/claude' >> ~/.config/aipager/aipager.yaml
```

## <a id="sessions-cant-authenticate-under-the-systemd-service-but-claude-works-fine-in-a-terminal"></a>Le sessioni non riescono ad accedere con il servizio systemd, ma `claude` funziona bene nel terminale

I servizi `systemctl --user` non leggono mai `~/.bashrc` / `~/.profile`, quindi una riga `export CLAUDE_CODE_OAUTH_TOKEN=…` lì non arriva mai ad aipager. Il servizio usa invece `LoadCredential=` di systemd e legge `~/.config/aipager/daemon.env`: un semplice file `KEY=VALUE`, permessi 0600, creato in automatico la prima volta che esegui `aipager service install` (copiato da un vecchio `config.env` se conteneva un token, altrimenti cercato una volta nella tua shell di login, altrimenti lasciato vuoto con un avviso). Se è ancora vuoto, `aipager doctor --fix` cerca di nuovo negli stessi posti quando dici di sì e aggiunge la riga che trova, lasciando le altre righe del file; non stampa mai il token. Oppure aggiungi tu la riga:

```sh
cat ~/.config/aipager/daemon.env          # guarda cosa c'è (o cosa manca)
echo 'CLAUDE_CODE_OAUTH_TOKEN=sk-...' >> ~/.config/aipager/daemon.env
chmod 600 ~/.config/aipager/daemon.env
aipager service stop && aipager service start
```

Vedi [Modello di sicurezza: la credenziale di Claude](security.md#the-claude-credential-what-actually-protects-it) per cosa protegge questo file e cosa no.

## <a id="the-daemons-control-socket-moved"></a>Il socket di controllo di aipager si è spostato

`aipager doctor` / `aipager status` prima guardavano sempre `/tmp/aipager.sock`. Con il servizio systemd il socket di controllo ora sta in `$XDG_RUNTIME_DIR/aipager.sock` (e torna a `/tmp` solo quando `$XDG_RUNTIME_DIR` non è impostato: container, WSL1, distribuzioni minime). Si è spostato solo questo socket; i socket dtach di ogni sessione (`/tmp/claude-dtach-*.sock`) non cambiano. Imposta `AIPAGER_SOCKET_PATH` se ti serve una posizione precisa.

## <a id="update-says-the-restart-would-kill-sessions-killmode"></a>`/update` dice che il riavvio terminerebbe le sessioni (KillMode)

I file di servizio scritti prima di questa versione non hanno la riga `KillMode=`, quindi systemd usa `control-group`: fermare o riavviare `aipager.service` termina ogni sessione di Claude avviata da aipager. Per questo `/update` installa la nuova versione ma non riavvia aipager, ed elenca le sessioni che un riavvio farebbe cadere. Si sistema una volta:

```
aipager service install
```

Riscrive il servizio con `KillMode=process`, ricarica systemd e riavvia aipager, e quel riavvio gira già con la nuova impostazione. Controlla con `systemctl --user show -p KillMode aipager.service` (`KillMode=process`). Con questa impostazione systemd scrive righe «left-over process» per le tue sessioni a ogni riavvio; è normale.

## <a id="aipager-update-cant-find-uv--pipx"></a>`aipager update` non trova uv / pipx

`aipager update` cerca l'installatore con un percorso assoluto nel tuo `PATH`, più `~/.local/bin`, `~/.cargo/bin`, le cartelle di Homebrew, `/usr/bin` e `/bin`. Se dice ancora di non trovare `pipx`, l'installatore che ha creato questo aipager non c'è più o sta altrove; reinstalla con lo strumento che hai (`pipx install --force aipager`, `uv tool install --reinstall aipager`).

## <a id="update-refused-editable--not-your-install"></a>Aggiornamento rifiutato: installazione editable / non tua

`/update` e `aipager update` aggiornano solo un'installazione del tuo utente e scrivibile da lui, tramite l'installatore che l'ha creata. Si rifiutano, spiegando il motivo, per:

- una copia del codice in modalità editable (per lo sviluppo): aggiornala con `git pull`;
- un'installazione con Nix, Snap, Docker o un pacchetto del sistema operativo: usa l'aggiornamento di quel sistema;
- un'installazione di un altro utente (per esempio un venv di root sotto `/opt`): aggiornala come quell'utente;
- un'installazione da una cartella locale (`pipx install /path/to/aipager`): `non si può aggiornare da qui: it was installed from a local folder; update it from that folder`. Porta la nuova versione in quella cartella e reinstalla da lì, oppure passa a PyPI con `pipx install --force aipager`;
- un'installazione di cui non si sa l'installatore (`could not tell which installer owns this aipager`): reinstallala con `uv tool install aipager` o `pipx install aipager`.

## <a id="update-failed-unexpected-argument---refresh"></a>Aggiornamento fallito: unexpected argument '--refresh'

aipager 1.0.0 e precedenti eseguivano `uv tool upgrade aipager --refresh`, che nessuna versione di uv accetta, quindi ogni aggiornamento di un'installazione con uv si fermava con `error: unexpected argument '--refresh' found` e non riavviava niente. Le versioni più recenti eseguono `uv tool upgrade aipager`. Per superarlo una volta, aggiorna da un terminale su quel computer (`aipager update` ha lo stesso problema in quelle versioni):

```
uv tool upgrade aipager
systemctl --user restart aipager.service
```

Senza il servizio (o su macOS), riavvia aipager nel modo in cui l'hai avviato.

## <a id="claude-code-update-installed-with-npm-into-a-folder-you-cant-write-to"></a>Aggiornamento di Claude Code: installato con npm in una cartella in cui non puoi scrivere

Un Claude Code installato con `sudo npm install -g` sta in una cartella che solo root può modificare (come `/usr/lib/node_modules`), quindi `claude update` eseguito come tuo utente fallisce con `global folder isn't writable`. `/update` e la Mini App mostrano la versione più recente con `non si può aggiornare da qui: installed with npm into a folder your user can't write to` e nessun pulsante per lei. Esegui `claude install` una volta come l'utente con cui gira aipager: passa all'installatore di Claude Code nella tua cartella home, che si aggiorna senza sudo. Poi riavvia aipager (`systemctl --user restart aipager.service`, o nel modo in cui l'hai avviato): un aipager in esecuzione tiene il Claude Code trovato all'avvio, e dopo il riavvio prende il più recente che trova. Se `claude_path` in `aipager.yaml` (`aipager doctor --fix` può impostarlo) o `AIPAGER_CLAUDE_BIN` indicano quello di npm, prima puntali a `~/.local/bin/claude` o toglili, perché vincono sempre.

## <a id="updated-but-the-version-didnt-change"></a>Aggiornato, ma la versione non è cambiata

Un'installazione con pipx fatta da un URL git si aggiorna da quella sorgente git, non da PyPI. Quando la sorgente non ha niente di più recente, `/update` dice `✅ aipager è già alla A: questa installazione con pipx si aggiorna dal suo repo git, che non ha niente di più recente.`. Pubblica o scegli lì la nuova versione, oppure passa a PyPI con `pipx install --force aipager`.

**Senza servizio**, un aipager che nessun gestore di servizi riavvia (`aipager start` in un terminale o in tmux, Docker, launchd) installa un aggiornamento ma continua a eseguire il vecchio codice finché non si riavvia. `/update` dice allora `aipager B è installato e aspetta un riavvio (in funzione: A).`, e gli hook di Claude Code, che ripartono da zero a ogni evento, potrebbero già usare B: il proprietario riceve un avviso che l'aggiornamento è fatto solo a metà (due minuti dopo che gli hook hanno usato B per la prima volta, così un riavvio subito dopo l'installazione non lo mostra mai, e quando nessuna attività è in corso nella chat privata del proprietario). Quando aipager usa B, quell'avviso diventa `✅ L'aggiornamento di aipager è completato: ora aipager è alla B.`, senza il suo pulsante. Tocca **🔄 Riavvia ora** (nel messaggio dell'aggiornamento, nell'avviso o in **Impostazioni → Aggiornamenti** della Mini App). aipager aspetta che nessuna attività sia in corso, si ferma in modo pulito e avvia la nuova versione sul posto: lo stesso processo nello stesso terminale, nella stessa finestra di tmux o nello stesso container, con lo stesso comando e lo stesso ambiente.

Se l'avvio sul posto non riesce, una copia separata avvia aipager quando il vecchio processo non c'è più. Il suo output va allora nel log del riavvio automatico (`aipager.log` nella cartella dell'istanza, o `/tmp/aipager.log`) invece che nel tuo terminale, e il log di aipager indica il comando da eseguire se quella copia non torna. Se l'interprete stesso non c'è più (il suo ambiente è stato rimosso), o non si è potuto avviare niente, aipager esce con codice 1, il log di aipager finisce con `restart.failed:` e il comando da eseguire, e l'avvio successivo annuncia l'aggiornamento.

Quando non c'è il pulsante **Riavvia ora**, il messaggio dà i passaggi per il tuo ambiente: in un terminale o in tmux, premi Ctrl+C ed esegui `aipager start`; su macOS, `launchctl kickstart -k gui/<uid>/com.aipager.daemon`; in Docker, `docker restart <container>` sull'host. Ricreare un container Docker (`docker compose up` dopo `down`, o di nuovo `docker run`) lo fa partire dalla sua immagine, e questo riporta la versione di aipager di quell'immagine: per tenere un aipager più nuovo, usa un'immagine più nuova.

## <a id="the-daemon-didnt-come-back-after-an-update"></a>aipager non è ripartito dopo un aggiornamento

L'aggiornamento controlla che la nuova versione si importi prima di qualsiasi riavvio, ma se il nuovo aipager non riesce comunque a partire, systemd riprova ogni 5 s. Guarda perché:

```
journalctl --user -u aipager.service -n 50
```

Reinstalla la versione precedente con lo stesso installatore, poi riavvia:

```
pipx install --force aipager==<previous>        # oppure:
uv tool install --force aipager==<previous>     # oppure:
<venv>/bin/python -m pip install aipager==<previous>
systemctl --user restart aipager.service
```

Un'installazione con pipx da un percorso locale: `pipx install --force /path/to/aipager` sul commit vecchio. Se c'è un vecchio annuncio in sospeso, toglilo con `rm -f ~/.local/share/aipager/update-restart.json` (dopo 24 h viene comunque ignorato); `~/.local/share/aipager/update.lock` è solo un file di lock e si può cancellare senza problemi quando nessun aggiornamento è in corso.

## <a id="an-update-was-interrupted-when-aipager-shut-down"></a>«Un aggiornamento è rimasto a metà quando aipager si è spento»

aipager si è fermato (o è stato riavviato) mentre `/update` installava, e l'installatore si è fermato con lui, quindi l'installazione potrebbe essere scritta a metà. Se aipager o Claude Code non funzionano bene, sistemali: per aipager, esegui il comando di reinstallazione del messaggio (per esempio `pipx install --force aipager`) e poi `systemctl --user restart aipager.service`; per Claude Code, esegui di nuovo `claude update`.

Un Riavvia ora che non è riuscito a far ripartire aipager (senza servizio) lascia `restart.failed:` nel log di aipager con il comando da eseguire; eseguilo, e il nuovo aipager annuncia l'aggiornamento.

## <a id="a-chat-is-in-the-wrong-language"></a>Una chat è nella lingua sbagliata

La lingua di aipager viene da quella scelta per la chat, non dai messaggi che contiene. Apri `/language` in quella chat e scegli la lingua che vuoi: la scelta vince su tutto il resto. In una chat privata, **Automatico** segue la lingua della tua app di Telegram (Impostazioni → Lingua in Telegram); una lingua che aipager non ha si vede in inglese. Una chat privata che esisteva prima delle lingue resta in inglese finché non ne scegli una. Un gruppo è in inglese finché un admin non sceglie la sua lingua. La lingua cambia solo i testi di aipager: Claude risponde nella lingua in cui gli scrivi, qualunque cosa mostri aipager. Vedi [Lingue](commands.md#languages).

## <a id="the-bot-description-is-not-the-one-i-wrote"></a>La descrizione del bot non è quella che ho scritto

aipager imposta la descrizione e la descrizione breve del bot in ogni lingua che parla, per chi ha l'app di Telegram in quella lingua, e Telegram mostra a queste persone quel testo invece di quello che hai scritto in BotFather. aipager lo fa solo finché non hai una descrizione tua, quindi una descrizione scritta prima di installare questa versione arriva a tutti. Se l'hai scritta dopo che aipager aveva impostato la sua, chi usa quelle lingue vede ancora quella di aipager. Per mostrare la tua, cancella il testo di aipager per ogni lingua tramite la Bot API (`setMyDescription` e `setMyShortDescription` con un testo vuoto e quel `language_code`); aipager non lo reimposta mai finché c'è il tuo.

## <a id="aipager-says-languagesjson-could-not-be-used"></a>aipager dice che languages.json non si può usare

aipager tiene quello che ha imparato sulle lingue (chi c'era già prima delle lingue, l'ultima lingua di Telegram vista per ogni persona, e se la proposta unica e la riga del primo contatto sono già partite) in `~/.local/state/aipager/languages.json`. Se quel file non si legge, aipager lo sposta in `languages.json.bad-<time>` nella stessa cartella, scrive un avviso nel log (di nuovo a ogni avvio finché non decidi) e tiene ogni chat in inglese, senza proposte di lingua, finché non decidi: rimetti al suo posto il file vecchio se riesci a ripararlo, oppure cancella `languages.json` e riavvia, e aipager conta di nuovo ogni persona che conosce come già presente (inglese finché non sceglie). Una lingua scelta da qualcuno non sta in questo file e non va mai persa.

## <a id="right-to-left-text-looks-wrong"></a>Il testo da destra a sinistra si vede male

In persiano e in arabo aipager mette nel proprio testo dei segni di direzione invisibili perché ogni riga si legga da destra a sinistra, e tiene da sinistra a destra nomi di sessione, comandi e codice. Se una riga sembra ancora confusa, il più delle volte è un'app di Telegram che disegna diversamente il testo con direzioni miste: controllala in un'altra app (telefono contro Desktop o Web) prima di segnalarlo. Le risposte di Claude si vedono come le ha scritte Claude, senza segni aggiunti.

## <a id="a-translated-keyboard-word-does-nothing-in-another-chat"></a>Una parola della tastiera tradotta non fa niente in un'altra chat

La tastiera di risposta di ogni chat è nella lingua di quella chat. Le parole della tastiera principale (status, stop, new, kill e le altre) vengono capite in ogni chat, in ogni lingua, e le parole inglesi funzionano sempre. La parola di un pulsante di template o di comando è diversa: scritta in una chat impostata su un'altra lingua è un messaggio per Claude, non un pulsante, quindi «Svuota conversazione» in italiano non esegue mai `/clear` in una chat in persiano o in inglese. Usa il pulsante stesso, oppure il comando inglese (`/clear`). Se una tastiera sembra vecchia dopo che la lingua della chat è cambiata, invia `/start` per avere quella attuale. Una parola che è anche il nome di una tua sessione parla con quella sessione. `/mode` legge sempre `ask`, `auto` e `full` come modalità, e una parola di modalità tradotta come sessione solo quando una sessione in esecuzione ha esattamente quel nome.

## <a id="still-stuck"></a>Non hai ancora risolto?

Invia una segnalazione di problemi: tocca **Segnala un problema** sotto `/help` nella tua chat privata con il bot, oppure esegui `aipager report` in un terminale. Vedi tutta la segnalazione prima che parta qualsiasi cosa, e non contiene chat, messaggi, nomi, percorsi o token (vedi [Segnalazioni di problemi](problem-reports.md)). Dopo l'invio ricevi un riferimento come `ap1-7c41d09e2b3a`.

Poi, se vuoi parlarne, apri una issue su [github.com/dev-aly3n/aipager/issues](https://github.com/dev-aly3n/aipager/issues) e cita quel riferimento. Per favore non incollare nella issue l'output di `aipager doctor` o di `aipager logs`: contengono l'ID della tua chat, i percorsi della tua cartella home, i nomi delle sessioni e l'inizio dei tuoi messaggi. Se serve una riga del log, lo sviluppatore chiederà proprio quella riga, e potrai prima togliere tutto quello che è privato.

## <a id="see-also"></a>Vedi anche

- [Architettura](architecture.md): cosa fa ogni componente.
- [Modello di sicurezza](security.md): cosa tocca aipager e cosa no.
