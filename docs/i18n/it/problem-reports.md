# Segnalazioni di problemi: cosa contiene una segnalazione

<!-- i18n-source: docs/problem-reports.md @ 6fc084f -->

> Questa pagina è tradotta dalla pagina in inglese; se le due versioni non coincidono, vale quella in inglese. ([English](../../problem-reports.md))

Quando aipager incontra un bug, puoi inviare una segnalazione allo sviluppatore di aipager con un tocco. Vedi tutta la segnalazione prima che parta qualsiasi cosa, niente lascia il tuo computer finché non dici di sì, e una segnalazione non contiene mai le tue chat, i messaggi, i nomi, i percorsi o i token. È fatta solo da un elenco fisso di campi: versioni, conteggi, date, risposte sì/no, scelte da elenchi fissi, punti del codice di aipager e il nome `ap1-` del bug, più una breve nota (solo se la scrivi tu).

## <a id="how-to-send-one"></a>Come inviarne una

Da Telegram solo il proprietario dell'installazione di aipager può inviare una segnalazione: la persona che parla con il bot nella chat privata dell'installazione stessa (il proprietario del bot; con più chat private così e nessun proprietario unico, non c'è il pulsante «Segnala un problema» né la proposta automatica, ma `aipager report` nel terminale funziona lo stesso). La Mini App ha la sua pagina di segnalazione; ogni altra strada da Telegram apre la stessa scheda di anteprima nella tua chat privata con il bot:

- **`/help`**: quando lo invia il proprietario, l'aiuto porta un pulsante «🐞 Segnala un problema». In un gruppo funziona solo il tocco del proprietario e l'anteprima si apre nella chat privata del proprietario, mai nel gruppo.
- **Mini App**: nella scheda Impostazioni, «Segnala un problema» (quando l'app è aperta sulla tua chat privata) apre una pagina dentro l'app. In alto c'è la tua nota (facoltativa, fino a 500 caratteri, con un contatore). Sotto c'è un breve riepilogo della segnalazione: versioni, impostazioni, errori (tocca un errore per vedere i punti del codice) e **Altri dettagli**. **Mostra la segnalazione esatta** mostra il testo preciso che verrà inviato, nota compresa, e cambia mentre scrivi. **Invia segnalazione** (il pulsante di Telegram in basso) invia una sola volta proprio quel testo, poi la pagina mostra il riferimento con un pulsante **Copia**. Tornare indietro non invia niente. Se aipager ha sistemato la tua nota (spazi all'inizio o alla fine, caratteri invisibili, la parte oltre i 500 caratteri), la pagina mostra la nota sistemata e non invia niente finché non tocchi di nuovo «Invia segnalazione».
- **Avviso sul limite di memoria**: quando un hook di aipager finisce la memoria, l'avviso in chat porta un pulsante «🐞 Segnala questo».
- **Proposta automatica**: ogni tanto aipager propone di inviare una segnalazione su un bug che ha notato (vedi [Quando aipager propone una segnalazione](#when-aipager-offers-a-report)).
- **Nel terminale**: `aipager report` stampa una segnalazione e chiede `Send this report? [y/N]`. Ha gli stessi tipi di campi ma meno informazioni sull'aipager in esecuzione (modalità, chat, funzioni, tempo di attività, sessioni e dettagli di Claude Code restano vuoti o sconosciuti). `aipager report --note "what you were doing"` aggiunge una nota. Senza un terminale (uno script, una pipe, la shell di una sessione di Claude Code) stampa soltanto la segnalazione e non la invia mai.

La scheda di anteprima mostra la segnalazione esattamente come verrà inviata: JSON dentro un blocco ripiegato che puoi aprire, oppure, quando non entra in un messaggio, un file `report.json` in risposta alla scheda. I suoi pulsanti:

- **📤 Invia**: invia proprio questa segnalazione, una sola volta. Poi la scheda dice `Inviata. Riferimento: ap1-...`.
- **✏️ Aggiungi una nota**: il prossimo messaggio che invii in questa chat entro 10 minuti diventa la nota della segnalazione (tagliata a 500 caratteri), e la scheda mostra di nuovo la segnalazione con la nota prima che tu la invii. Se prima fai altro (un comando, un altro pulsante, una foto, un'azione nella Mini App), la domanda sulla nota si chiude senza aggiungere nulla; dopo 10 minuti il tuo prossimo messaggio va a Claude come sempre. La domanda ha **‹ Indietro** (torna all'anteprima senza nota) e Annulla.
- **✖️ Annulla**: non invia niente.

Le anteprime restano aperte 24 ore, e restano aperte le 5 più recenti (aprire la sesta chiude la più vecchia). Dopo un riavvio di aipager una vecchia scheda non può più inviare: apri di nuovo «Segnala un problema» per averne una nuova. Una segnalazione aperta nella Mini App si può inviare per 24 ore (le 3 più recenti); dopo un riavvio la pagina ne carica una nuova.

## <a id="what-a-report-holds"></a>Cosa contiene una segnalazione

Campo per campo, in parole semplici. Ogni valore viene confrontato con un elenco di valori ammessi una volta prima di mostrare l'anteprima e una volta prima dell'invio; un valore che non va bene viene sostituito da `<invalid>` o tolto, mai inviato così com'è.

**Sulla segnalazione**
- `schema`: il formato della segnalazione (`aipager-report/1`).
- `trigger`: `manual` (l'hai aperta tu) o `auto` (la proposta automatica).
- `day`: la data, in UTC (senza ora).
- `note`: solo se hai aggiunto una nota: le tue parole esatte come le mostrava l'anteprima (al massimo 500 caratteri, senza caratteri invisibili).

**aipager e il computer**
- `aipager`: la versione; come è stato installato (`pipx`, `uv`, `brew`, `pip`, `editable`, `nix`, `snap`, `docker`, `system`, `foreign` o `unknown`); da dove viene (un indice di pacchetti, una cartella locale, una copia git); se `aipager update` può aggiornarlo.
- `python`: la versione di Python e se è CPython o PyPy.
- `os`: il sistema (`linux`, `darwin`, `windows`, `freebsd`), il tipo di processore, la distribuzione Linux da un elenco fisso e il suo numero di versione, la versione major.minor del kernel, il tipo di container se c'è (`docker`, `lxc`, `wsl`, `podman`) e se aipager è configurato come servizio systemd o launchd.
- `deps`: le versioni delle librerie su cui si basa aipager (python-telegram-bot, httpx, aiohttp, PyYAML, rich, questionary, dtach-bin).
- `claude_code`: la versione di Claude Code, quante installazioni di Claude Code sono state trovate e come accede (`env`, `file`, `keychain`, o il motivo per cui non si sa). Mai le credenziali stesse.

**Le tue impostazioni, come numeri**
- `config`: la modalità (`personal`, `team`, `scope`); quante chat private e quanti gruppi sono configurati; quanti ruoli personalizzati esistono (senza i nomi); quali funzioni sono attive (la Mini App e il tipo del suo tunnel, i vocali, le anteprime dei diff, i riepiloghi ricchi).
- `runtime`: da quanto tempo gira aipager, come fascia (`<10m`, `10m-1h`, `1-24h`, `1-7d`, `>7d`); quante sessioni sono in esecuzione e quante al lavoro; quante volte aipager si è fermato in modo imprevisto negli ultimi 7 giorni e come è finita l'ultima volta (`clean`, `crash`, `reboot`).
- `doctor`: il risultato (`ok`, `warn`, `fail`) di un elenco fisso di controlli di salute, quando è disponibile senza rifare i controlli (le segnalazioni dal bot e dal terminale lo lasciano vuoto).
- `flood`: lo stato dei limiti di Telegram, sommato su tutte le chat: blocchi negli ultimi 7 giorni, se in questo momento c'è una chat con gli invii fermi, in modalità minima o rallentata, e quanta parte del budget orario ha usato la chat più impegnata, come fascia. Nessun ID di chat.

**Cosa è andato storto**
- `counters_24h`: quante volte è successa ogni cosa di un elenco fisso nelle ultime 24 ore (per esempio un riavvio del controllo della scheda di attività, un certo tipo di errore di Telegram, un hook che arriva al limite di memoria, un errore di rete o di disco). Solo conteggi.
- `log_digest_24h`: i 15 punti del codice di aipager che hanno scritto più avvisi o errori nel log nelle ultime 24 ore, come punto (`aipager/state.py:1234`), livello e conteggio. Mai il testo del log.
- `errors`: fino a 10 errori che aipager ha registrato, ognuno con:
  - `fingerprint`: il nome `ap1-` del bug (un hash del tipo di errore e dei nomi di fino a 8 punti del codice di aipager, mai di testo o di dati);
  - dove è successo (aipager, l'hook, la riga di stato, un comando, la Mini App), come è stato intercettato e se è un bug o solo una situazione insolita (`tier`);
  - per un errore che aipager ha scritto nel log, il nome del logger (un modulo di `aipager`, `asyncio` o una parte di `telegram.ext`);
  - il nome del tipo di eccezione (per esempio `builtins.KeyError`, oppure `<other>` per un'eccezione che viene da fuori di un elenco fisso di pacchetti), i tipi di fino a tre cause e il nome di un codice di errore come `ENOSPC`;
  - per un errore dell'hook, l'evento hook e lo strumento (uno strumento integrato con il suo nome, qualsiasi strumento MCP come `mcp`);
  - quante volte, in quali giorni e su quali versioni di aipager è successo;
  - fino a 12 punti del codice di aipager stesso da cui è passato (file, riga, nome della funzione) e i nomi di fino a 8 pacchetti esterni, da un elenco fisso, da cui è passato.

**Mai dentro**: le tue chat, i messaggi, le risposte di Claude, i nomi delle sessioni, i percorsi di cartelle o file, token o chiavi, ID di chat o di utenti, username, il nome del computer, il testo di qualsiasi messaggio di errore e le righe del log. I campi qui sopra non hanno posto per queste cose e la segnalazione viene confrontata con l'elenco prima di essere mostrata e prima di essere inviata.

## <a id="where-it-goes"></a>Dove va

Una segnalazione va, senza il tuo nome, nella casella privata degli errori dello sviluppatore di aipager su [Sentry](https://sentry.io): nessun account e nessun ID di installazione. Come qualsiasi richiesta web parte dall'indirizzo internet del tuo computer, che GitHub e Sentry vedono; la casella è impostata per non salvarlo (l'opzione «Prevent Storing of IP Addresses» di Sentry). aipager invia la segnalazione da solo con una richiesta HTTPS (non usa la libreria di Sentry, che raccoglie molto di più): la segnalazione che hai visto viene inviata come allegato `report.json`, con un breve riepilogo per ordinarla (versione, tipo di installazione e se è un'installazione di sviluppo, sistema, versioni di Python e di Claude Code, trigger, e l'impronta, il tipo e il punto del codice del primo errore). La richiesta dice solo `aipager/<version>`.

Dopo l'invio la scheda (o il terminale) mostra un **riferimento**: l'impronta `ap1-` del primo errore oppure, per una segnalazione senza errori, il suo ID evento di 32 caratteri. Se apri anche una [issue su GitHub](https://github.com/dev-aly3n/aipager/issues), cita il riferimento così lo sviluppatore trova la tua segnalazione. Non incollare log o l'output di `aipager doctor` nella issue: contengono l'ID della tua chat, i percorsi della tua cartella home e l'inizio dei tuoi messaggi.

Subito prima di inviare, aipager legge [report-endpoint.json](https://github.com/dev-aly3n/aipager/blob/main/report-endpoint.json) dal repo di aipager su GitHub per sapere dove inviare (un aipager in esecuzione tiene per un giorno una copia valida; una lettura fallita riprova all'invio successivo, e ogni `aipager report` lo rilegge). Questo permette allo sviluppatore di cambiare la chiave di Sentry, disattivare le segnalazioni per tutti o impedire l'invio alle versioni sotto un certo numero, senza pubblicare una nuova versione (una build che non conosce la propria versione conta come più bassa). Se GitHub non si raggiunge o il file non è in un formato che aipager capisce, aipager usa la chiave inclusa in questa versione; un file leggibile che dice «disattivato» viene sempre rispettato.

## <a id="limits"></a>Limiti

- Al massimo 5 tentativi di invio al giorno (UTC) da un computer, manuali e automatici insieme, compresi quelli falliti. Quello dopo dice `Da un computer si possono inviare al massimo 5 segnalazioni al giorno. Riprova domani.`
- Nessun nuovo tentativo in background: se un invio fallisce, la scheda dice `Al momento non è stato possibile inviarla. Non si è perso nulla; riprova più tardi.` e tiene il pulsante di invio.

## <a id="when-aipager-offers-a-report"></a>Quando aipager propone una segnalazione

aipager nota i propri bug e ogni tanto propone, nella tua chat privata, di inviare una segnalazione: `aipager ha avuto un errore interno N volte (TypeError nella scheda di attività). Una segnalazione aiuta a risolverlo prima. ...`, con **Vedi la segnalazione**, **Non ora** e **Non chiedere più**. «Vedi la segnalazione» apre la scheda di anteprima solo con quegli errori; non parte niente finché non tocchi «Invia» lì.

Una proposta arriva solo quando vale tutto questo:
- È un vero bug di aipager: un errore nel codice di aipager stesso, almeno due volte e ad almeno 10 minuti di distanza, oppure subito per un crash mentre aipager gira come servizio (un `aipager start` in primo piano che viene chiuso, per esempio chiudendo il suo terminale, viene solo contato). Un errore che l'hook, il programma della riga di stato o un comando segnalano ad aipager (compreso un hook che finisce la memoria) deve anche distribuirsi su almeno un giorno, quindi una volta sola non porta mai a una proposta; a quelli pensa il pulsante «Segnala questo» dell'avviso. Le cose che non sono colpa di aipager (i limiti di Telegram, la rete, un disco pieno) non portano mai a una proposta; vengono solo contate per una segnalazione che invii tu.
- Al massimo una proposta ogni 3 giorni, con tutto quello che è in sospeso raccolto in quella sola proposta.
- Nessuna proposta nelle prime 48 ore dopo il primo avvio di aipager su questo computer (un aggiornamento non fa ripartire questo conteggio), né nella prima ora dopo un riavvio di aipager.
- Solo quando niente è in esecuzione o in attesa di invio da 2 minuti e hai usato la chat privata con il bot negli ultimi 30 minuti. Mai in un gruppo, mai mentre Telegram limita quella chat.
- Ogni bug viene proposto una volta per versione di aipager. Qualunque cosa rispondi, su quella versione non viene più proposto; dopo un aggiornamento può tornare solo se succede di nuovo.
- **Non ora** e **Non chiedere più** contano entrambi come «no», e così una proposta lasciata senza risposta per un giorno. **Vedi la segnalazione** azzera il conteggio, anche se poi annulli. Due «no» di fila disattivano le proposte automatiche: se il secondo era un tocco, una riga te lo dice; in ogni caso si vede in `/settings`.
- Mai per un'installazione di sviluppo (aipager installato da una cartella locale, da una copia git, o in modalità editable).

Per disattivare le proposte automatiche, usa `/settings` nella tua chat privata: **Segnalazioni di problemi: Disattivo**. Quando due «no» di fila le hanno disattivate, nella stessa pagina c'è **Riattiva le proposte**. Anche un `reports.json` presente ma illeggibile disattiva le proposte automatiche (aipager non sa più le tue risposte precedenti); **Riattiva le proposte** cancella anche questo stato. Su un computer senza una chat a cui chiedere, imposta `AIPAGER_REPORT_PROMPTS=0` nell'ambiente di aipager. «Segnala un problema» e `aipager report` funzionano sempre, qualunque siano queste impostazioni.

## <a id="what-is-kept-on-your-machine"></a>Cosa resta sul tuo computer

- `~/.local/state/aipager/reports.json`: gli errori registrati da aipager (nella forma descritta sopra: nomi dei tipi, versioni, punti del codice, al massimo 50), conteggi orari per 8 giorni, riassunti del log per 2 giorni, come sono finite le ultime chiusure, e lo stato delle proposte (quando è arrivata l'ultima, quanti «no» di fila, quali bug sono stati proposti su quale versione). Lo puoi leggere solo tu.
- `~/.local/state/aipager/report-sends.json`: quante segnalazioni sono state inviate o tentate oggi.
- `~/.local/share/aipager/install.json`: quando aipager è partito per la prima volta su questo computer (per le 48 ore qui sopra).
- `~/.local/share/aipager/running.json`: scritto mentre aipager è in esecuzione (il numero del suo processo, quando è partito, se gira come servizio e l'ID di questo avvio del computer), per distinguere un crash da una chiusura normale.

Nessuno contiene chat, messaggi, nomi, percorsi o token. `aipager uninstall` li lascia, insieme al resto dei dati di aipager, così una reinstallazione riprende da dove eri; elenca cosa ha lasciato e stampa il comando per cancellarlo (vedi [`aipager uninstall`](commands.md#command-line-aipager-uninstall)). Per cancellare solo questi, cancella i quattro file: cancellare tutta la cartella `~/.local/state/aipager` toglie i primi due (insieme allo stato delle lingue, `languages.json`), ma `install.json` e `running.json` vanno cancellati per nome (non cancellare `~/.local/share/aipager`, che contiene anche le cartelle delle sessioni di aipager).

## <a id="see-also"></a>Vedi anche

- [Risoluzione dei problemi](troubleshooting.md): guasti frequenti e soluzioni.
- [Modello di sicurezza](security.md): cosa tocca aipager e cosa passa sulla rete.
- [Comandi del bot](commands.md): `/help`, `/settings` e la riga di comando.
