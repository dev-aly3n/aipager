# Comandi del bot e interfaccia

<!-- i18n-source: docs/commands.md @ 7cd1d1ad -->

> Questa pagina è tradotta dalla pagina in inglese; se le due versioni non coincidono, vale quella in inglese. ([English](../../commands.md))

Come guidi aipager da Telegram. Ci sono quattro modi di dargli input: comandi slash, pulsanti della tastiera, la dashboard della Mini App e messaggi liberi (testo, file, vocali).

Il bot accetta input solo dalle chat configurate; per il confine di fiducia vedi [Modello di sicurezza](security.md).

## <a id="slash-commands"></a>Comandi slash

Sono registrati in `aipager/bot/lifecycle.py`. Il menu dei comandi di Telegram li mostra, aggiornato all'avvio di aipager e ogni volta che cambiano le sessioni.

| Comando | Argomenti | Cosa fa |
|---|---|---|
| `/start` | - | La schermata principale: le sessioni di questa chat, dove va un messaggio normale (`✍️ I messaggi vanno a x1.`), e `🆕 Nuova sessione`, `⚙️ Impostazioni` e, quando questa chat ha una sessione terminata da riportare indietro, `↩️ Riprendi`. Mostra anche la tastiera fissa. |
| `/help` | - | Una breve guida organizzata per cosa vuoi fare (iniziare, parlare, controllare, gestire, impostazioni e, per un admin, `/update`), con il consiglio che toccare un comando lo invia subito: tienilo premuto (telefono) o premi Tab (computer) per aggiungere prima del testo. Per il proprietario dell'installazione (la persona nella chat privata dell'installazione stessa con il bot) porta anche un pulsante `🐞 Segnala un problema`: apre, in quella chat privata, un'anteprima esatta della segnalazione, con Invia, Aggiungi una nota e Annulla. Non parte niente finché non tocchi Invia ([cosa contiene una segnalazione](problem-reports.md)). |
| `/app` | - | Apre la dashboard della Mini App (sessioni, visualizzatore di diff, impostazioni). |
| `/status` | - | Le sessioni di questa chat come un elenco su cui puoi agire. Ognuna dice il suo stato a parole (`⚙️ al lavoro 3:12 · Bash: run tests`, `⏳ ha bisogno di te · Bash: make deploy`, `🔄 aspetta 2 agenti` quando l'attività di Claude è finita ma gli agenti o i comandi che ha avviato in background sono ancora in esecuzione, `💤 pronta`), con `✍️` su quella a cui va un messaggio normale, e una seconda riga con modello, contesto, costo, cosa c'è in coda (`coda 3 (1 in coda, 2 note)`) e gli agenti e i comandi in background in esecuzione (una sessione che aspetta i suoi agenti li nomina invece nel suo stato). Ogni sessione ha una riga di pulsanti: `✍️ x1` manda lì i tuoi prossimi messaggi, `⏹ Ferma` mentre lavora o aspetta i suoi agenti, `Rispondi` quando ha bisogno di te, e `⋮` per il resto. Sotto: `🆕 Nuova` e `⚫ Terminate (n)` per le sessioni terminate (riprendile, eliminale o toglile tutte dall'elenco; restano in `/resume`). |
| `/stop [label]` | facoltativo | Interrompe l'attività in corso di una sessione. Senza nome ferma l'unica sessione di questa chat al lavoro (anche se i tuoi messaggi vanno a un'altra; una sessione con un agente in background o un comando in background ancora in esecuzione conta come al lavoro), chiede quale con un selettore quando ce ne sono più di una e dice `Al momento non c'è nulla in esecuzione.` quando non ce n'è nessuna. `/stop x1` (o `/x1 stop`) ferma x1 (oppure risponde `x1 ora non sta facendo nulla.` quando non lavora). Scarta anche i messaggi in coda e dice quanti ne ha scartati. Su una sessione nel background di Claude Code che sta lavorando, ferma il lavoro del job in background e riporta qui la sessione. |
| `/now` | - | Invia subito i messaggi che Claude tiene nella coda della sessione attuale, invece che dopo il passaggio in corso (la scorciatoia «invia ora» di Claude Code, Ctrl+X Ctrl+S). Funziona sempre, anche quando gli invii della chat sono fermi o è in modalità minima; risponde `Non c'è niente in coda` quando Claude non tiene niente. Vedi [Inviare subito un messaggio in coda](#send-a-queued-message-now). |
| `/new [name] [first message]` | tutti e due facoltativi | Avvia una sessione. `/new x1` la avvia subito, e `/new x1 fix the tests` invia anche «fix the tests» come primo messaggio. Inviato senza niente (per esempio toccato dal menu), `/new` chiede un nome, e il prossimo messaggio che invii viene usato proprio come gli argomenti, quindi `/new` e poi `x1 fix the tests` fanno lo stesso. In un gruppo, rispondi alla scheda con il nome (o menziona il bot: `@aipagerbot x1`), perché lì un messaggio normale non arriva al bot. Vale solo il messaggio successivo: se prima fai altro (una foto, un altro comando, una risposta a un altro messaggio, un template della tastiera, un altro pulsante), viene gestito come sempre e la scheda si chiude; `/status`, `/settings` e i menu della tastiera la lasciano aperta. La scheda che chiede il nome mostra anche la modalità, il modello e la cartella che riceverà la sessione, con i pulsanti per cambiarli prima. In entrambi i casi si arriva alla stessa scheda «x1 è pronta»: cosa ha ricevuto la sessione, che il tuo prossimo messaggio va a lei e pulsanti con un tocco per passare a un'altra modalità o cambiare modello. Le nuove sessioni di tutti partono in modalità «Automatico»; cambia cosa usa /new in `/settings` → Nuove sessioni. Un `!` davanti al nome avvia la sessione in modalità «Completo», per un admin (i ruoli `owner` e `admin`) o per una persona nella propria chat privata con il bot il cui ruolo può inviare messaggi e rispondere a Claude (`user`, non `read_only`); per chiunque altro parte in modalità «Automatico» e la scheda dice perché. Un nome già in uso offre `↩️ Vai a questa` (quando quella sessione è in esecuzione) o `♻️ Riprendi` (quando è terminata), `🆕 Sostituisci con una nuova` e `↩️ Annulla`, solo a chi ha inviato `/new`. La scheda che chiede il nome scade dopo 10 minuti senza essere usata (`⏱ Questa scheda di nuova sessione è scaduta. Invia di nuovo /new.`). Un membro il cui ruolo non ha `can_manage` (`user`, `read_only`) avvia sessioni solo dentro la [cartella radice](groups.md#root-folders) della chat: la cartella sulla scheda è la cartella radice, la pagina Cartella elenca lei e le cartelle subito dentro (senza la riga «Cartella predefinita»), «Nuova cartella» crea la cartella dentro quella scelta, e «Vai a questa», «Riprendi» e «Sostituisci» su una sessione che lavora fuori dalla cartella radice vengono rifiutati («Questa sessione lavora fuori dalla tua cartella ~/..., quindi il tuo ruolo non può riprenderla, riavviarla o cambiarne la modalità. Chiedi a un admin.»). |
| `/resume [label]` | facoltativo | Riprende una sessione terminata per nome, o apre un selettore. Una sessione torna nella modalità che stava usando. Una che era in modalità «Completo» ci torna solo per un admin o per una persona nella propria chat privata; per chiunque altro torna in modalità «Automatico» e la risposta lo dice (`♻️ x1 è ripartita in modalità «Automatico»: la modalità «Completo» è solo per gli admin.`). Una sessione salvata da una versione più vecchia di aipager nella sua vecchia modalità Auto (che girava senza nessun controllo) torna in modalità «Completo», e una salvata in Ask torna in modalità «Chiedi». Una sessione terminata resta elencata per `GONE_SESSION_MAX_AGE_DAYS` giorni (predefinito 14), poi esce dall'elenco delle sessioni; la cronologia della conversazione di Claude non viene toccata. Un membro il cui ruolo non ha `can_manage` non può riprendere una sessione la cui cartella è fuori dalla [cartella radice](groups.md#root-folders) della chat (proprietario e admin sì); lo stesso vale per Riavvia e per ogni cambio di modalità, compreso il passaggio sul posto tra «Chiedi» e «Automatico». Riprendere segue una conversazione che in Claude Code è passata a un'altra (torna la più recente), e quando un job in background di Claude Code tiene ancora la conversazione, prima ferma quel job se non sta facendo nulla, oppure chiede (`Ferma e riporta qui` / `Lasciala andare`) mentre lavora; lo stesso vale per Riavvia. |
| `/kill [label]` | facoltativo | Termina una sessione, sempre dopo una conferma: `⏹ Terminare x1? Claude si ferma e la sessione si chiude.` con `[⏹ Termina] [Annulla]`. Senza nome va dritto alla conferma quando questa chat ha una sola sessione in esecuzione, altrimenti mostra un selettore delle sessioni in esecuzione di questa chat. Anche in `/status` → `⋮` → `⏹ Termina la sessione`. |
| `/restart [label]` | facoltativo | Riavvia una sessione dopo una conferma: Claude si ferma e riparte, con la conversazione conservata, nella modalità in cui aipager l'ha avviata o portata l'ultima volta. Una sessione avviata nel terminale con `aipager session` torna in modalità «Chiedi» a meno che da allora non le sia stata cambiata la modalità da Telegram (aipager non sa in che modalità l'ha avviata il terminale, e non indovina mai una modalità più ampia); lo stesso per riprenderla da Telegram. Una sessione la cui conversazione è passata nel background di Claude Code viene invece riportata qui (vedi [risoluzione dei problemi](troubleshooting.md#a-session-moved-to-claude-codes-background)). |
| `/rename [label] [new]` | facoltativo | Dà un nuovo nome a una sessione (anche a una terminata). `/rename x1 newname` la rinomina subito. Con meno parole chiede: il nuovo nome direttamente quando il comando può riguardare una sola sessione, altrimenti un selettore (una sola parola dopo `/rename` non sceglie la sessione, perché potrebbe essere il nome vecchio o quello nuovo). In un gruppo, rispondi alla domanda con il nome. |
| `/delete [label]` | facoltativo | Toglie una sessione terminata dall'elenco, dopo una conferma. |
| `/diff [label]` | facoltativo | Mostra il diff di git della cartella di lavoro di una sessione. Senza nome mostra quella a cui vanno i tuoi messaggi. |
| `/clearqueue` | - | Scarta tutti i messaggi non ancora presi della sessione attuale (sia quelli che aipager trattiene sia quelli in coda dentro Claude) senza interrompere l'attività in corso. Risponde con quanti ne ha tolti. |
| `/mode [label] [ask\|auto\|full]` | facoltativo | Mostra la modalità dei permessi di una sessione con un pulsante per passare a ciascuna delle altre: `✍️ x1 è in 🤖 Modalità «Automatico».` `[💬 Passa alla modalità «Chiedi»]` `[🔓 Passa alla modalità «Completo»]`. Ci sono tre modalità. **💬 Modalità «Chiedi»**: Claude chiede prima di ogni azione. **🤖 Modalità «Automatico»** (predefinita): il controllo di sicurezza di Claude Code esegue le azioni sicure e ferma quelle rischiose perché decida tu. **🔓 Modalità «Completo»**: tutto viene eseguito senza alcun controllo (`--dangerously-skip-permissions`); possono sceglierla solo un admin o una persona nella propria chat privata con il bot il cui ruolo può inviare messaggi e rispondere a Claude (`user`, non `read_only`). «Automatico» e «Completo» cambiano solo le richieste di permesso di Claude Code: l'hook di aipager applica comunque le regole del ruolo del membro. `/mode ask`, `/mode auto`, `/mode full` o `/mode x1 ask` cambiano subito; funziona anche la parola stessa della modalità nella lingua della chat, senza la parola «modalità» (`/mode x1 Chiedi` per 💬 Modalità «Chiedi», `/mode x1 Completo` per 🔓 Modalità «Completo»). Quando una sessione in esecuzione in questa chat ha esattamente quel nome (diciamo una sessione chiamata `demander`), la parola è il nome della sessione: `/mode demander` mostra la modalità di quella sessione, e `/mode x1 demander` cambia comunque x1. `ask`, `auto` e `full` sono sempre una modalità, in qualsiasi ordine (`/mode x1 ask` o `/mode ask x1`); solo quando entrambe le parole sono parole di modalità e una sola è il nome di una sessione in esecuzione, quella parola è il nome della sessione, quindi con una sessione chiamata `full` sia `/mode full ask` sia `/mode ask full` la passano alla modalità «Chiedi», e `/mode full` da solo è comunque una modalità. Passare tra «Chiedi» e «Automatico» avviene sul posto, senza riavvio e in qualsiasi momento (anche mentre Claude lavora): aipager preme Shift+Tab nella sessione come faresti tu, e la scheda dice `⚙️ x1 passa a 🤖 Modalità «Automatico» senza riavvio.` finché Claude Code non riferisce la nuova modalità al passaggio successivo, poi mostra il risultato. Se la modalità «Automatico» non è disponibile per quella sessione (per il suo modello o il suo piano), la scheda lo dice e la sessione resta in modalità «Chiedi». Non si può fare mentre la sessione mostra una domanda o una richiesta di permesso, nemmeno una che si apre proprio mentre i tasti stanno per essere premuti: prima rispondi (nessun tasto finisce nella richiesta). Passare alla modalità «Completo» richiede un admin (o una persona nella propria chat privata) e una conferma, e riavvia la sessione (la conversazione resta); anche uscire dalla modalità «Completo» la riavvia, subito. Una sessione avviata in modalità «Completo» si riavvia a ogni cambio, anche dopo essere stata passata a «Chiedi» o «Automatico» nel terminale, e così una che ha riferito «Completo» da quando è partita (una sessione del terminale avviata con `--dangerously-skip-permissions`): lì Shift+Tab potrebbe riportarla a «Completo». Con una sessione impegnata, il riavvio offre `🛑 Ferma e cambia` / `⏳ Non ora`. Un membro il cui ruolo non ha `can_manage` non può cambiare la modalità di una sessione la cui cartella è fuori dalla [cartella radice](groups.md#root-folders) della chat, né sul posto né con un riavvio. Toccare un pulsante di cambio modifica la scheda stessa invece di inviare un altro messaggio. `/perms` è il vecchio nome e funziona ancora allo stesso modo. |
| `/settings` | - | Come appaiono e come rispondono le sessioni di questa chat: la lingua usata da aipager, l'aspetto dei messaggi, le anteprime dei diff, gli aggiornamenti della scheda nelle attività lunghe, la formattazione, la lunghezza delle risposte, il livello di vocabolario; con cosa avvia le sessioni `/new`; le preferenze di ogni sessione; e, nella chat privata del proprietario, le proposte di inviare segnalazioni di problemi. Vedi [Impostazioni](#settings). In un gruppo chiunque può guardarle e cambiare un'impostazione richiede un admin. |
| `/language` | - | La lingua che aipager usa per i propri messaggi e pulsanti in questa chat (lo stesso elenco della prima riga di `/settings`). Ogni lingua è elencata con il suo nome: English, poi Español (spagnolo), Русский (russo), Português (Brasil) (portoghese del Brasile), 简体中文 (cinese semplificato), Français (francese), Deutsch (tedesco), 한국어 (coreano), العربية (arabo), Bahasa Indonesia (indonesiano), Українська (ucraino), Italiano, Türkçe (turco), فارسی (persiano) e Tiếng Việt (vietnamita). Una chat privata iniziata dopo questa versione segue la lingua della tua app di Telegram; una che esisteva già resta in inglese finché non ne scegli una (vedi [Lingue](#languages)). In spagnolo, portoghese, francese, italiano e indonesiano le parole della tastiera di risposta restano `status`, `stop`, `new` e `kill`, come i loro comandi. Tedesco e turco usano `Status`, `Stop`, `Neu…` e `Beenden…`, e `Özet`, `Stop`, `Oluştur` e `Sonlandır`; le parole inglesi funzionano ancora se le scrivi. In persiano e in arabo il testo di aipager si legge da destra a sinistra, con cifre persiane o arabe; nomi delle sessioni, comandi, nomi dei modelli e codice restano come sono. L'elenco dice cosa cambia: solo il testo di aipager, qui e nella Mini App; le tue sessioni di Claude non cambiano, e Claude risponde nella lingua in cui gli scrivi. In un gruppo chiunque può guardarla e cambiarla richiede un admin. `language` è un nome di sessione riservato. |
| `/whoami` | - | Dice chi sei qui: nell'uso in team, la tua etichetta, il tuo ruolo in questa chat e le regole che quel ruolo ti applica. |
| `/update` | - | Solo per gli admin (i ruoli `owner` e `admin`, o un ruolo con `can_manage`). Controlla se aipager e Claude Code hanno una versione più recente, poi aggiorna quello che ce l'ha con un solo pulsante. Vedi [Aggiornamenti](#update). |

Il menu `/` mostra prima le tue sessioni (`/x1 · Parla con x1`), poi i comandi di tutti i giorni: `new`, `status`, `stop`, `now`, `resume`, `mode`, `settings`, `language`, `help` (e `app` quando la Mini App è attiva, solo nelle chat private). `kill`, `restart`, `rename`, `delete`, `diff`, `clearqueue`, `perms`, `whoami` e `update` non sono nel menu ma funzionano se li scrivi; le azioni su una sessione sono anche in `/status` → `⋮`.

**I comandi che agiscono su una sessione** (`/stop`, `/mode`, `/kill`, `/restart`, `/rename`, `/delete`, `/diff`) seguono una regola:

- Con un nome (`/restart x1`) agiscono su quella sessione di questa chat (`/rename` per questo ha bisogno sia del nome vecchio sia di quello nuovo, vedi sopra).
- Senza nome agiscono sull'unica sessione a cui il comando può riferirsi (l'unica al lavoro per `/stop`, l'unica in esecuzione per `/kill` e `/restart`, l'unica terminata per `/delete`), passando dalla solita conferma se c'è.
- Altrimenti mostrano un selettore con le sole sessioni di questa chat adatte al comando: in cima quella a cui vanno i tuoi messaggi, segnata `✍️`, poi le altre per nome, e `✖️ Annulla`. Scegliere una sessione porta alla stessa scheda che scrivere il suo nome.
- In un gruppo, senza nome agiscono solo sulla tua sessione attuale, quando il comando le è adatto; altrimenti mostrano sempre il selettore, anche con una sola sessione, perché potrebbe essere di un altro membro (vedi [gruppi](groups.md#talking-to-a-session-in-a-group)).

`/mode` e `/diff` guardano per prima la sessione a cui vanno i tuoi messaggi.

### <a id="settings"></a>Impostazioni

`/settings` (e la scheda Impostazioni della Mini App, che mostra le stesse scelte) decide come appaiono e come rispondono le sessioni di questa chat. Ogni chat ha le sue; in un gruppo chiunque può guardarle e cambiare un'impostazione richiede un admin. Nella tua chat privata le cambi tu, qualunque sia il ruolo, in Telegram e nella Mini App. Una riga con ✅ è impostata diversamente dal predefinito («Come appare la risposta» mostra sempre la sua scelta).

| Impostazione | Scelte (prima la predefinita) | Cosa fa |
|---|---|---|
| 🌐 Lingua | Automatico (lingua del tuo Telegram), nelle chat private; English | La lingua dei messaggi e dei pulsanti di aipager in questa chat e nella Mini App, mostrata con il suo nome (lo stesso elenco di `/language`). Non cambia le tue sessioni di Claude: Claude risponde nella lingua in cui gli scrivi. Ci sono English, Español (spagnolo), Русский (russo), Português (Brasil) (portoghese del Brasile), 简体中文 (cinese semplificato), Français (francese), Deutsch (tedesco), 한국어 (coreano), العربية (arabo), Bahasa Indonesia (indonesiano), Українська (ucraino), Italiano, Türkçe (turco), فارسی (persiano) e Tiếng Việt (vietnamita); «Automatico» segue la lingua della tua app di Telegram (vedi [Lingue](#languages)). |
| 🖼 Come appare la risposta | Scheda + risposta a parte; Risposta dentro la scheda; Solo la risposta (senza scheda) | Come appare un'attività finita. «Scheda + risposta a parte» tiene la scheda sopra la risposta come traccia di come ci si è arrivati; «Risposta dentro la scheda» mette la risposta nella scheda; «Solo la risposta (senza scheda)» toglie la scheda e lascia la risposta da sola. Il predefinito è «Scheda + risposta a parte» (`KEEP_FINISHED_CARD=0` lo imposta su «Solo la risposta (senza scheda)» per le chat che non hanno mai scelto). Vedi [risposte a riposo](#idle-responses). |
| 📝 Anteprime dei diff | Disattivo; Attivo | Attivo pubblica ogni `Write`/`Edit` come un messaggio di diff a parte sotto la scheda di attività. Disattivo tiene una scheda e una risposta; la scheda elenca comunque ogni modifica, e la Mini App ha un visualizzatore di diff. |
| ⏱ Aggiorna scheda nelle attività lunghe | Attivo; Disattivo | Attivo aggiorna la scheda di attività ogni 10 secondi dopo 2 minuti di attività e ogni 30 secondi dopo 10 minuti (mai più lenta di così, per quanto duri l'attività), e allora il suo orologio avanza a passi di 30 secondi; Disattivo tiene il ritmo dei primi minuti per tutta l'attività (vedi [risoluzione dei problemi](troubleshooting.md#a-long-turns-card-refreshes-less-often)). |
| ✏️ Formattazione semplice | Disattivo; Attivo | Attivo chiede a Claude di scrivere solo testo semplice ed elenchi puntati: niente tabelle, blocchi di codice, titoli o grassetto. |
| 📏 Lunghezza delle risposte | Nessuna regola; Molto breve; Breve; Media; Lunga | Chiede a Claude di rispondere con quella lunghezza. |
| 🎓 Livello di vocabolario | Nessuna regola; Parole semplici; Normale; Avanzato | Chiede a Claude di usare quel livello di vocabolario. |
| 🎨 Aspetto | Pulsante Ferma, Pulsanti Consenti e Nega, Pulsante App, Menu impostazioni | Come appaiono i pulsanti di aipager in questa chat; tutto parte da Normale (l'aspetto di Telegram). Telegram permette a un bot di colorare un pulsante solo di blu, verde o rosso, quindi: il pulsante Ferma (quello sulla scheda di attività e quello sotto una domanda o una richiesta di permesso) Normale, Blu, Verde o Rosso; Consenti e Nega Normale o Colorati (Consenti e Consenti sempre verdi, Nega rosso); il pulsante App (sul messaggio fissato e sotto `/app`) Normale, Blu, Verde o Rosso (il pulsante del menu accanto al campo del messaggio non si può colorare); il menu impostazioni Normale (✅ segna la scelta attuale) oppure la scelta attuale anche in blu. Vale per tutta la chat, non per sessione; in un gruppo lo cambia solo un admin. Le app di Telegram che non supportano i colori mostrano tutti i pulsanti normali. La scheda Impostazioni della Mini App ha le stesse scelte in una sezione a parte, Aspetto, dopo Preferenze della chat, ognuna con un pallino colorato. |
| 🆕 Nuove sessioni | Permessi (modalità), Modello, Cartella | Con cosa avvia le sessioni `/new` quando non scegli tu: modalità «Chiedi», «Automatico» (predefinita) o «Completo», un modello dall'elenco dei modelli o il modello predefinito di Claude Code, e una cartella. Solo un admin, o una persona nella propria chat privata con il bot, può scegliere qui la modalità «Completo» (la schermata di una chat privata non la mostra a nessun altro), e non si applica mai a chi non può usarla: le sue sessioni partono in modalità «Automatico». |
| 👤 Preferenze di ogni sessione | una sessione, poi qualsiasi impostazione qui sopra tranne Nuove sessioni | Valori per una sola sessione (anche in `/status` → `⋮` → `👤 Preferenze`); ⭐ segna un'impostazione personalizzata e «Usa il predefinito della chat» toglie la personalizzazione. La pagina della sessione nella Mini App imposta gli stessi valori. |
| 🐞 Segnalazioni di problemi | Chiedimi; Disattivo | Solo nella chat privata del proprietario dell'installazione. «Chiedimi» lascia che aipager proponga ogni tanto di inviare una segnalazione su un errore interno che ha incontrato più di una volta (al massimo ogni 3 giorni); «Disattivo» non lo propone mai (il pulsante «Segnala un problema» in `/help` funziona comunque). Due proposte rifiutate di fila disattivano le proposte, e allora questa riga lo dice, con un pulsante per riattivarle. Vedi [segnalazioni di problemi](problem-reports.md#when-aipager-offers-a-report). |

Lunghezza delle risposte, livello di vocabolario e formattazione semplice arrivano a Claude come una breve istruzione aggiunta a ogni messaggio inviato da Telegram (l'aggiunge l'hook, vedi [hook](hooks.md#userpromptsubmit)), usando il valore personalizzato della sessione se c'è.

Qualunque sia l'aspetto scelto, ogni scheda di attività finisce con la riga di stato della sessione (`⏳`/`✅ nome · …`) e ogni risposta comincia con la sua riga di risultato (`💬 nome`, più `· Finito (…)` quando non resta una scheda finita a mostrare i numeri); «Risposta dentro la scheda» le mette una sopra l'altra, ognuna nella sua parte. Sopra la riga di stato della scheda di attività c'è una linea divisoria (in testo piccolo), e con «Risposta dentro la scheda» una seconda separa la scheda finita dalla risposta. Negli aspetti con la scheda la risposta arriva apposta un attimo dopo la scheda finita, così vedi la scheda dire Finito prima che sotto compaia la risposta; regola o togli questo ritardo con `FINISH_CARD_GRACE_SECONDS` (secondi, predefinito 0,8; 0 invia entrambe insieme). Nell'aspetto con la scheda, un'attività che non ha chiamato nessuno strumento non lascia la scheda: la scheda viene cancellata prima di dire Finito, e la prima riga della risposta porta i numeri, come in «Solo la risposta (senza scheda)».

### <a id="per-session-dynamic-commands"></a>Comandi di ogni sessione

Ogni sessione in esecuzione ha un comando, registrato con la sua etichetta:

| Forma | Cosa fa |
|---|---|
| `/<label>` | Fa di `<label>` la sessione a cui vanno i tuoi messaggi. La risposta lo dice, con lo stato e la modalità della sessione (`✍️ Ora parli con x1 · 💤 pronta · 🤖 Modalità «Automatico»`, oppure `· 🔄 aspetta 2 agenti ·` mentre i suoi agenti in background lavorano, poi «Invia un messaggio e arriverà a x1.»), offre **Rispondi** quando la sessione ha bisogno di te e **⋮ Altro** per il suo menu. I numeri completi sono in `/status`. In un gruppo dice invece «Rispondi a un messaggio di x1 (o menziona @aipagerbot) per parlarci.», perché lì un messaggio normale non arriva al bot (vedi [gruppi](groups.md#talking-to-a-session-in-a-group)). `/x1@aipagerbot`, che Telegram invia quando scegli `/x1` dal menu di un gruppo, funziona allo stesso modo. |
| `/<label> <prompt>` | Invia `<prompt>` direttamente a quella sessione. Quella sessione diventa anche quella a cui vanno i tuoi prossimi messaggi normali. |
| `/<label> stop` | Interrompe l'attività in corso di quella sessione. |

L'output di `/status` viene dagli stessi dati che `aipager status` mostra sulla riga di comando; l'elenco delle sessioni non ha bisogno di chiamate a Telegram.

Sul computer, `aipager status` e `aipager session ls` danno a ogni sessione uno stato: `IDLE`, `BUSY`, `WAITING` (una richiesta di permesso o una domanda ti aspetta, con il suo testo quando si conosce, per esempio `permission: Bash: make deploy`), `TASKS` (l'attività di Claude è finita, ma gli agenti o i comandi che ha avviato in background sono ancora in esecuzione, con quello che aspetta, per esempio `waiting for 2 agents and 1 shell`, o `finishing up` quando Claude sta per continuare con i loro risultati; conta come al lavoro) o `GONE`. Con `--json` ogni sessione ha anche `waiting_kind` (`permission` o `question`) e `waiting_summary`, entrambi `null` a meno che la sessione non stia aspettando, e `job_agents` e `job_shells`, quanti agenti e comandi in background aspetta una sessione `TASKS` (altrimenti `0`).

## <a id="languages"></a>Lingue

aipager parla English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی e Tiếng Việt, ognuna elencata con il suo nome.

- **Cambia solo il testo di aipager.** La lingua vale per i messaggi, i pulsanti, le schede e la Mini App di aipager. Le tue sessioni di Claude non cambiano, e Claude risponde nella lingua in cui gli scrivi. Nomi delle sessioni, comandi, nomi dei modelli e codice restano come sono.
- **`/language`** (o la prima riga di `/settings`) sceglie la lingua per la chat in cui sei. In una chat privata è anche la tua lingua personale: la usano gli avvisi che vedi nei gruppi e la tua Mini App.
- **Automatico** (solo nelle chat private) segue la lingua della tua app di Telegram, e la segue di nuovo se la cambi. Una lingua che aipager non ha si vede in inglese.
- **Chat nuove e vecchie.** Una chat privata iniziata dopo questa versione segue la lingua della tua app di Telegram finché non ne scegli una, e il suo primo messaggio in una lingua diversa dall'inglese porta una breve riga che lo dice e spiega come cambiarla. Una chat che esisteva già resta in inglese. Se la tua app di Telegram è in una lingua che aipager parla, ricevi una scheda che ti propone quella lingua, con un pulsante `Mantieni l'inglese`; arriva una volta sola, qualunque cosa rispondi.
- **I gruppi** hanno una lingua, scelta da un admin in `/language`; fino ad allora il gruppo è in inglese. L'avviso dopo un tocco si vede nella lingua di chi tocca: la sua scelta nella chat privata con il bot, altrimenti la lingua della sua app di Telegram per chi è entrato dopo questa versione, altrimenti la lingua del gruppo. Vedi [gruppi](groups.md).
- **Da destra a sinistra.** In persiano e in arabo il testo di aipager si legge da destra a sinistra, con cifre persiane o arabe. La scheda di attività segue la lingua della chat, in qualunque lingua scriva Claude: in una chat in inglese si legge da sinistra a destra, in una in persiano o in arabo da destra a sinistra, e una frase di Claude scritta nel verso opposto si legge comunque nel suo. La risposta segue il testo di Claude: quando Claude risponde in persiano o in arabo in una chat la cui lingua si scrive da sinistra a destra, tutta la risposta è un messaggio da destra a sinistra, e le righe di aipager al suo interno (gli agenti sotto la risposta e, con «Risposta dentro la scheda», la scheda finita sopra) stanno a destra come il resto e si leggono comunque da sinistra a destra.
- **Parole di modalità.** `/mode` accetta la parola della modalità nella tua lingua (`/mode Chiedi`), e `ask`, `auto` e `full` funzionano sempre. Se una sessione in esecuzione ha esattamente quel nome, la parola è il nome della sessione (vedi `/mode` sopra).
- **Descrizione del bot.** All'avvio aipager scrive la descrizione del bot (il testo sulla schermata di una chat vuota) e la descrizione breve in ognuna di queste lingue, per chi ha Telegram in quella lingua. Telegram mostra quel testo al posto di quello che hai scritto in BotFather, quindi aipager non imposta niente dove hai già un testo tuo (descrizione e descrizione breve sono controllate separatamente). Non sovrascrive mai il tuo testo e non cambia mai la descrizione inglese o quella predefinita. Quando il suo testo attuale è già impostato in ogni lingua, un avvio non chiede niente a Telegram. Altrimenti prima legge il tuo testo in BotFather (una lettura per la descrizione e una per la descrizione breve): se ce l'hai, si ferma lì, quindi con un testo tuo ogni avvio costa solo quelle letture, non di più. Se non ce l'hai, legge ogni lingua che non ha ancora il suo testo attuale, quindi una lingua in cui hai scritto un testo tuo viene riletta a ogni avvio. Se scrivi una descrizione in BotFather dopo che aipager ha impostato la sua, chi legge in queste lingue vede ancora quella di aipager (vedi [risoluzione dei problemi](troubleshooting.md#the-bot-description-is-not-the-one-i-wrote)).

## <a id="the-mini-app"></a>La Mini App

`/app` (e il pulsante del menu di Telegram) apre una dashboard fornita da aipager stesso. Di base è attiva; gestiscila con `aipager miniapp enable|disable|status`. Ogni richiesta viene controllata con la firma `initData` di Telegram (vedi [sicurezza → tunnel della Mini App](security.md#mini-app-tunnel)). La pagina non carica niente da nessun'altra parte: niente font, immagini o script da altri siti.

- **Chat.** L'app mostra una chat alla volta. Se appartieni a più di una chat (diciamo la tua chat privata e un gruppo), in alto una riga con i nomi delle chat ti fa passare dall'una all'altra; l'app si apre sulla tua chat privata e ricorda l'ultima scelta su quel telefono. Tutto quello che c'è sotto è della chat scelta, con il tuo ruolo lì. Con una sola chat la riga non c'è. Vedi [gruppi](groups.md).
- **Sessioni.** Una frase di riepilogo della chat, per esempio «1 ha bisogno di te, 2 al lavoro, 3 pronte», con la spesa sotto. Ogni sessione in esecuzione è un riquadro con il suo anello del contesto, la cartella, il modello e il costo; le sessioni al lavoro si illuminano con il colore di evidenza. Una sessione la cui attività è finita mentre gli agenti o i comandi che ha avviato in background sono ancora in esecuzione conta come al lavoro e si illumina anche lei, e il suo riquadro dice cosa aspetta («aspetta 2 agenti»). Le sessioni finite stanno in uno scaffale **Terminate** che si può chiudere. Il pulsante **+** avvia una nuova sessione.
- **Hanno bisogno di te.** Una sessione che aspetta una richiesta di permesso o una domanda sta in un vassoio color ambra in alto, che mostra cosa chiede. Ci sta anche una sessione nel background di Claude Code il cui job aspetta una richiesta di permesso, perché puoi rispondere dalla chat; la sua pagina offre anche «Riportala qui». **Rispondi nella chat** rimanda quella richiesta, con i suoi pulsanti, in fondo alla chat, come il pulsante **Rispondi** della barra fissata, e rispondi lì. Se era l'unica sessione in attesa l'app si chiude, così la richiesta è subito davanti a te; altrimenti resta aperta e dice «Inviata alla chat». Serve il permesso di inviare messaggi alla sessione, e una copia toccata dopo che la richiesta ha già avuto risposta o è stata sostituita dice «già risposto».
- **La pagina di una sessione.** In alto mostra l'anello del contesto, lo stato («Al lavoro (3:12)», «Aspetta 2 agenti», «Ha bisogno di te (permesso)», «Pronta, ultima attività 5 min fa», «Nel background di Claude Code», «Terminata») e un **Riportala qui** con un tocco (per una sessione nel background di Claude Code), un **Ferma** (mentre lavora, aspetta i suoi agenti o aspetta te) o un **Riprendi** (quando è terminata). Sotto: la richiesta in attesa con **Rispondi nella chat**, le ultime chiamate agli strumenti in una striscia (toccala per la cronologia completa), l'ultima risposta, i dati, il selettore del modello, le impostazioni della sessione, i file modificati e la cronologia. Il menu ⋮ ha tutte le azioni, con le stesse conferme di prima. Una sessione la cui conversazione è passata nel background di Claude Code (vedi [risoluzione dei problemi](troubleshooting.md#a-session-moved-to-claude-codes-background)) offre «Riportala qui», «Ferma», «Rinomina», «Termina» e «Riavvia»; quando il job in background sta ancora lavorando, «Riportala qui», «Riavvia» e «Riprendi» chiedono direttamente nella pagina, **Ferma e riporta qui** o **Lasciala andare**, e nella chat compare `↩️ x1 è tornata dal background di Claude Code.` quando è tornata.
- **Nuova sessione.** Una scheda guidata: nome, poi dove girare (con suggerimenti **Recenti** delle cartelle usate dalle tue sessioni, le più nuove prima), poi il modello (con suggerimenti **Suggeriti**), poi **Altre opzioni** per la modalità dei permessi e lo stile delle risposte. Il pulsante di Telegram in basso dice **Avvia sessione**. Per un membro il cui ruolo non ha `can_manage`, «dove girare» offre solo la [cartella radice](groups.md#root-folders) della chat e le cartelle subito dentro (la cartella radice è la predefinita, e «Nuova cartella» crea la cartella dentro quella scelta); una cartella fuori viene rifiutata con il motivo, e Riprendi, Riavvia e il cambio di modalità di una sessione che lavora fuori rispondono «Questa sessione lavora fuori dalla tua cartella ~/..., quindi il tuo ruolo non può riprenderla, riavviarla o cambiarne la modalità. Chiedi a un admin.»
- **Impostazioni.** **Lingua dell'app** è in cima: la tua lingua personale per il testo di aipager nell'app e nella tua chat privata (inglese, spagnolo, russo, portoghese (Brasile), cinese (semplificato), francese, tedesco, coreano, arabo, indonesiano, ucraino, italiano, turco, persiano o vietnamita; «Automatico» segue la lingua della tua app di Telegram, vedi [Lingue](#languages)). Chiunque può cambiare la propria, non viene mai pubblicata in chat e non cambia le tue sessioni di Claude. Poi le preferenze della chat (in un gruppo le cambiano solo gli admin; nella tua chat privata le cambi tu, qualunque sia il ruolo, come `/settings` lì) e, per un admin, **Aggiornamenti**, che rispecchia [`/update`](#update). Per il proprietario nella sua chat privata, **Segnala un problema** apre una pagina a parte: scrivi una nota, rivedi la segnalazione, inviala e vedi il suo riferimento senza lasciare l'app (vedi [segnalazioni di problemi](problem-reports.md#how-to-send-one)).

L'app segue il tema chiaro o scuro di Telegram quando lo cambi e ferma le piccole animazioni quando il telefono chiede meno movimento. Tutto quello che c'è nella Mini App si può fare anche dalla chat: il menu ⋮ sul pannello di una sessione ha le stesse azioni.

### <a id="switching-a-running-sessions-model"></a>Cambiare il modello di una sessione in esecuzione

La pagina di una sessione in esecuzione ha un selettore **Modello** che mostra il modello riferito dalla sessione (dalla riga di stato di Claude Code) e lo stesso elenco che offrono il selettore all'avvio e la tastiera dei modelli della chat. Scegliere un modello scrive esattamente `/model <name>` in quella sessione, lo stesso modo di scrivere che usa la tastiera dei modelli della chat, con le stesse regole su chi può farlo (chiunque possa inviare messaggi alla sessione). Il selettore dice **cambio in corso…** finché la riga di stato non riferisce un modello diverso, poi mostra quel modello; se non cambia niente in 15 secondi, dice **non confermato (controlla la sessione)**.

- **Solo quando la sessione è pronta, non mentre Claude lavora o c'è una richiesta aperta.** Claude Code esegue `/model` subito, anche a metà di un'attività, non dopo la sua fine, quindi il cambio viene rifiutato finché l'attività non finisce. La tastiera nella chat rifiuta negli stessi stati, con le stesse parole.
- **La domanda «Switch model?» di Claude Code.** Di solito Claude Code la fa prima di cambiare, perché la risposta successiva deve rileggere tutta la conversazione. Con Claude Code 2.1.251 e successivi, l'hook `PreModelSwitch` di aipager approva il cambio che aipager ha appena scritto, quindi la domanda non compare (vedi [hook](hooks.md#premodelswitch)). Ogni altro cambio chiede comunque. Con un Claude Code più vecchio, o passando a `opusplan` o da `opusplan`, la domanda può ancora comparire. Allora il cambio si vede come *non confermato* e la domanda aspetta nel terminale. Rispondi lì prima di inviare altro alla sessione, altrimenti il tuo prossimo messaggio verrà scritto dentro quella domanda. Per la stessa ragione aipager rifiuta un altro cambio per quella sessione per un minuto dopo un cambio non confermato, o finché la sessione non riferisce un nuovo modello.
- **Un messaggio inviato subito dopo un cambio aspetta.** Claude Code prima controlla il modello con una richiesta (fino a 15 secondi) e perde tutto ciò che viene digitato nel frattempo, quindi un messaggio che invii in quel momento viene trattenuto (👀) e arriva a Claude non appena la sessione segnala il nuovo modello, o dopo 17 secondi.
- **Il modello diventa anche il predefinito di Claude Code per le nuove sessioni.** Come con `/model` nel terminale, Claude Code salva il modello a cui passi nel suo `settings.json`, e ogni nuova sessione senza un modello suo parte con quello. La risposta in chat, la scheda della nuova sessione e la Mini App lo dicono quando succede. Una sessione avviata con un suo modello (nel passaggio del modello di `/new` o dai valori predefiniti di `/settings`) lo mantiene. In un gruppo, un membro il cui ruolo non può gestire la chat (`user`, `read_only`) cambia solo la propria sessione: aipager rimette il predefinito di Claude Code.

## <a id="the-pinned-status-bar"></a>La barra di stato fissata

Ogni chat con cui aipager parla (la tua chat privata e ogni gruppo) ha un messaggio fissato: la barra di stato. La fascia dei messaggi fissati di Telegram in cima alla chat mostra questo messaggio dalla **prima riga** in poi, con le righe unite, quindi quella riga dice la cosa che più ti serve:

| Prima riga | Quando |
|---|---|
| `⏳ jim ha bisogno di te - Bash: make deploy` | una sessione aspetta una richiesta di permesso, una domanda o un altro dialogo di Claude Code; `(+2 altre)` quando anche altre aspettano |
| `⚙️ jim (al lavoro)` / `💤 jim (pronta)` | la chat ha una sola sessione in esecuzione ed è al lavoro / pronta (`🔄 jim (in avvio)` mentre parte): questa riga è tutta la barra, salvo una riga sui limiti di frequenza |
| `🔄 jim (aspetta 2 agenti)` | l'attività dell'unica sessione in esecuzione è finita, ma gli agenti o i comandi che ha avviato in background sono ancora in esecuzione (`aspetta 1 comando in background`, `aspetta 2 agenti e 1 comando in background`, o `in chiusura` quando Claude sta per continuare con i loro risultati) |
| `⚙️ 2 al lavoro · 1 pronta` | più sessioni, nessuna in attesa: quante sessioni ci sono in ogni stato (`al lavoro`, `pronta`, `in avvio`; una sessione che aspetta i suoi agenti conta come al lavoro); `🔄` quando nessuna lavora e una sta partendo, `💤` quando sono tutte pronte |
| `💤 tutte pronte` | nessuna sessione in esecuzione |

Sotto, solo finché vale, c'è una riga sui limiti di frequenza (`🐢 invii più lenti dopo un avviso di Telegram` (sei ore dopo un 429) oppure `⏸ per ora le schede non si aggiornano (limite orario di Telegram)` / `(limite di Telegram)` (modalità minima, vedi [risoluzione dei problemi](troubleshooting.md#the-hourly-budget))), poi, quando la chat ha più di una sessione in esecuzione, `✍️ I messaggi vanno a jim` (la sessione a cui va un messaggio normale in questa chat, vedi sotto; non nei gruppi, dove ogni persona ha la sua destinazione) e una riga per sessione con il suo stato: `al lavoro`, `ha bisogno di te`, `aspetta 2 agenti`, `pronta` o `in avvio`. La barra nomina ogni sessione una volta sola: una sessione in attesa già nominata dalla prima riga non ha una riga sua, e la prima riga non elenca mai i nomi che le righe sotto ripetono. Una sessione la cui attività è finita mentre gli agenti o i comandi che ha avviato in background sono ancora in esecuzione dice cosa aspetta sulla sua riga (o sulla riga unica, quando c'è una sola sessione): `🔄 jim (aspetta 1 agente)`, `(aspetta 1 comando in background)`, oppure `(aspetta 2 agenti e 1 comando in background)` quando ci sono entrambi, gli stessi numeri che mostrano la sua scheda di attività, `/status` e la Mini App. Il lavoro in background che non appartiene più a nessuna attività (un server di sviluppo lasciato acceso da un'attività precedente, un agente che è andato avanti dopo `/stop`) viene contato invece dopo lo stato: `💤 jim (pronta, 1 comando in background in esecuzione)`, `(pronta, 1 agente al lavoro)` o `(al lavoro, 2 agenti e 1 comando in background in esecuzione)`. In entrambi i casi è un numero, mai i loro nomi, quindi la barra cambia solo quando cambia il numero. Tocca la barra per andare al messaggio.

**Dove va un messaggio normale.** Un messaggio che non è una risposta va alla sessione con cui questa chat ha parlato per ultima: quella che hai scelto per ultima con `/<label>`, a cui hai scritto, che hai avviato, o quella che ti ha appena risposto qui. Anche un `/mode`, `/clearqueue`, `/diff` o `/now` senza nome agisce su quella sessione (`/stop` ferma quella al lavoro). Ogni chat ha la sua destinazione: un messaggio in una chat non va mai a una sessione che appartiene a un'altra. In un gruppo contano solo una risposta al bot, un messaggio che menziona il bot, un comando o un tocco sulla tastiera del gruppo (vedi [gruppi](groups.md#what-the-bot-reads-in-a-group)), e ogni persona ha la sua destinazione, che la risposta di una sessione non cambia (vedi [gruppi](groups.md#talking-to-a-session-in-a-group)).

I pulsanti del messaggio fissato:

- **Rispondi a &lt;label&gt;** (uno per sessione in attesa, fino a tre) rimanda la richiesta di quella sessione, con i pulsanti per rispondere, in fondo alla chat, così rispondi senza scorrere. Se nel frattempo la richiesta ha già avuto risposta, ricevi un avviso «già risposto», e una copia (o la richiesta originale) toccata dopo che la richiesta ha avuto risposta altrove viene rifiutata allo stesso modo: non risponde mai a una richiesta arrivata dopo. Dopo un riavvio di aipager la richiesta in attesa si può ancora rispondere dalla scheda di attività o dal suo messaggio, ma aipager non sa più quale richiesta mostrava una copia più vecchia, quindi toccare una copia che la barra aveva rimandato prima del riavvio viene rifiutato con «questa richiesta non è più aperta». Chi può rispondere alla richiesta può usare il pulsante; gli altri no.
- **📱 App** apre la Mini App, nella tua chat privata, mentre la Mini App è in funzione.

La barra non ha orologio, costo, % di contesto o modello, quindi cambia solo quando cambia lo stato di una sessione. aipager la modifica (in silenzio, senza notifica) solo quando cambia quello che mostra, al massimo una volta ogni 30 secondi per chat (`PINNED_MIN_EDIT_GAP`); una modifica dentro quell'intervallo si vede alla sua fine, mai persa. Non viene mai modificata mentre la chat ha gli invii fermi e si aggiorna quando il blocco finisce.

In un gruppo il bot ha bisogno dei diritti di admin per fissare. Se non può fissare, aipager cancella il messaggio di stato che ha appena inviato (così non resta mai non fissato nella cronologia del gruppo) e non mostra la barra in quel gruppo fino a un riavvio di aipager; lo stesso per una chat che rifiuta del tutto il bot (bot espulso, bot bloccato). Se cancelli il messaggio fissato, aipager ne invia e ne fissa uno nuovo, al massimo una volta all'ora per chat.

## <a id="persistent-keyboard"></a>La tastiera fissa

Una tastiera fissa sta sotto il campo di scrittura della chat. Le righe, dall'alto:

| Riga | Pulsanti | Note |
|---|---|---|
| Sessioni | un pulsante per ogni etichetta di sessione in esecuzione | generati dall'elenco delle sessioni |
| Azioni | `status`, `stop`, `new` | scorciatoie di testo (`kill` funziona ancora se lo scrivi; terminare una sessione è in `/status` → `⋮`) |
| Navigazione | `Template ›`, `Comandi ›` | La Mini App si apre dal pulsante del menu accanto al campo di scrittura o con `/app`, non da questa tastiera |

`Modello ›` sta dentro il sottomenu Comandi. Toccare una voce di un sottomenu invia un messaggio già scritto o un comando slash:

- **Template**: messaggi che ti ritrovi a scrivere sempre uguali, per esempio «Scrivi i test», «Spiega il piano», «Aggiorna la memoria».
- **Comandi**: comandi slash che gestisce Claude Code stesso (`/compact`, `/clear`, ecc.), scritti subito. Un comando o un template nomina la sessione a cui è andato (`🧹 /clear inviato a x1`). Svuotare la conversazione viene rifiutato mentre la sessione lavora (`⚠️ Non si può cancellare la conversazione mentre la sessione lavora`).
- **Modello**: cambio rapido del modello per la sessione attuale. Ci sono gli alias di famiglia (`sonnet`, `opus`, `haiku`, `fable`, `opusplan`), che puntano sempre al modello più recente della famiglia, e modelli fissi (`claude-opus-5-5`, `claude-opus-5-5[1m]` con il contesto da 1M, `claude-sonnet-5-5`, `claude-fable-5-1`, `claude-haiku-5-5`). Il selettore all'avvio e la pagina della sessione nella Mini App usano lo stesso elenco. Un cambio viene rifiutato mentre la sessione lavora o ha una richiesta aperta. Poi la risposta `🔄` diventa il modello riferito dalla sessione, oppure dice che il cambio non è stato confermato (vedi [Cambiare il modello di una sessione in esecuzione](#switching-a-running-sessions-model)). Qualsiasi altro modello si scrive per nome nel passo del modello di `/new` o nella Mini App, come lo scrive Claude Code o il tuo provider: fino a 128 caratteri tra lettere, cifre e `. _ - : / @ ~ ,`, con `[1m]` ammesso alla fine ([provider](troubleshooting.md#using-an-api-key-a-gateway-or-another-provider)).

Per sostituire la disposizione predefinita, scrivi `~/.config/aipager/keyboard.json`:

```json
{
  "templates": [{"label": "Deploy",  "prompt": "Deploy to staging"}],
  "commands":  [{"label": "Compact", "send": "/compact"}],
  "models":    [{"label": "Sonnet",  "send": "/model sonnet"}]
}
```

Ogni sezione è indipendente: quelle che mancano usano il predefinito, quindi puoi sostituirne una senza scrivere le altre. Un JSON rovinato viene ignorato con un avviso nel log. Le modifiche richiedono un riavvio di aipager.

## <a id="per-message-inline-buttons"></a>Pulsanti sotto i messaggi

Quasi tutte le risposte del bot portano pulsanti adatti al momento:

### <a id="permission-prompts"></a>Richieste di permesso

Quando Claude vuole eseguire uno strumento che va approvato, la scheda di attività diventa una richiesta di permesso:

```
🔐 Bash: List the temp directory
ls -la /tmp

  [✅ Consenti]  [❌ Nega]
  [🟢 Consenti sempre]  [⏹ Ferma]
```

La scheda mostra il comando vero (o il percorso del file) che Claude chiede di eseguire, sotto la descrizione di Claude stesso: approva quello che riesci a leggere. Un carattere che può cambiare l'ordine in cui si disegna il testo (un carattere di controllo della direzione di Unicode come U+202E, che può far sembrare un comando un altro) viene mostrato come un segno tipo `⟨U+202E⟩`, e allora la scheda aggiunge la riga `⚠️ Questo testo contiene caratteri nascosti che cambiano la direzione di scrittura (mostrati come ⟨U+...⟩). Potrebbe eseguire qualcosa di diverso da quello che vedi.`

- **Consenti**: approva questa chiamata.
- **Nega**: la rifiuta; Claude non esegue lo strumento.
- **Consenti sempre**: approva e aggiunge la regola permanente proposta da Claude («don't ask again for …»). Il pulsante **compare solo quando Claude propone una regola così**; per un comando per cui non sa costruirne una (la maggior parte dei comandi composti) la scheda ha invece Consenti / Nega / Ferma, e così per un accesso in sola lettura a un file (`Read`, `Grep`, `Glob`) fuori dalla cartella di lavoro della sessione. Claude Code 2.1.259 e successivi mettono in quel posto una riga «switch to auto mode» nel loro dialogo di Bash, una riga «block reads outside the working directories from now on» nel dialogo delle letture esterne e, per un Write/Edit per cui non sanno costruire una regola per file, un cambio della modalità dei permessi come accettare in automatico tutte le modifiche ai file (`acceptEdits`); aipager non sceglie mai queste righe; cambia modalità di proposito con `/mode`.
- **Ferma**: interrompe l'attività invece di rispondere.

La risposta arriva a Claude Code tramite l'hook dei permessi di aipager, che la aspetta fino a 20 secondi. Una richiesta che arriva prima della scheda di attività (allora viene inviata come messaggio a parte, solo con Consenti e Nega) riceve risposta allo stesso modo. Quando quell'attesa è già passata, i pulsanti scrivono la risposta nel dialogo di Claude Code; allora Nega preme Esc («No, and tell Claude what to do differently»), che chiude l'attività di Claude come Ferma, e la scheda dice `🚫 jim · Negato`. Sulla domanda che Claude Code fa in modalità «Automatico» prima della prima lettura fuori dal progetto, un Consenti in ritardo non viene scritto: il suo Invio sceglierebbe «Yes, and keep allowing any reads outside the working directories», quindi il tocco ti dice di rispondere nel terminale (Nega funziona comunque).

Ogni tocco viene registrato in `~/.claude/aipager-audit.jsonl` e ripetuto come risposta di una riga sotto la scheda di attività: `✅ jim · Consentito · @alice · Bash: List the temp directory` (la descrizione della chiamata, o il suo comando quando non c'è; con una chat configurata con `aipager config`; nell'uso personale solo `Consentito`).

Mentre una richiesta (o un AskUserQuestion) ti aspetta, la sessione si vede in attesa, mai pronta. Se intanto Claude Code ricorda che l'input è fermo, la chat riceve `⬆️ jim · aspetta ancora la tua risposta più in alto` in risposta a quella richiesta, una volta per attesa.

I dialoghi `AskUserQuestion` si vedono allo stesso modo, con un pulsante per opzione (le prime quattro) e, per una domanda che accetta più risposte, caselle ⬜/☑ e un pulsante `✅ Invia`. La domanda e la descrizione di ogni opzione si vedono per intero, come nel terminale. Si accorcia solo un modulo troppo lungo per un messaggio di Telegram: la scheda di attività mostra prima meno righe di strumenti, poi i testi più lunghi tra la domanda e le descrizioni finiscono con `…` su una parola intera.

**Più di una richiesta.** Claude Code può aspettare due richieste nello stesso momento: un agente in background chiede un permesso mentre Claude ti fa una domanda, o il contrario. aipager le mostra tutte e due: la prima sulla scheda di attività (o come messaggio a parte), ognuna delle successive come messaggio a parte sotto la scheda, con i suoi pulsanti. La sessione risulta in attesa finché non hai risposto all'ultima, e un messaggio che mandi nel frattempo resta in attesa fino ad allora. Rispondi nell'ordine che vuoi. Consenti e Nega passano per l'hook dei permessi finché aspetta, e l'hook raggiunge proprio quella richiesta, ovunque sia. Un tocco che deve scrivere dei tasti (un'opzione di una domanda, oppure Consenti e Nega quando l'hook non aspetta più) li scrive solo nella richiesta che Claude Code mostra per prima: Claude Code mostra un dialogo alla volta, e i tasti arrivano a quello in cima. Altrimenti il tocco non scrive niente, tiene i suoi pulsanti e dice `Claude Code sta mostrando prima un'altra richiesta: rispondi a quella prima di questa.` oppure, quando aipager non conosce l'ordine (per esempio un agente di cui non ha visto l'avvio), `aipager non riesce a capire quale richiesta Claude Code mostra per prima: rispondi a questa nel terminale.` Se alla richiesta della scheda rispondi per prima, la scheda mostra che è in attesa, solo con Ferma, e le altre richieste tengono i loro pulsanti. Se Claude finisce la sua attività mentre la richiesta di un agente è ancora aperta, la risposta arriva come sempre e la sessione continua ad aspettare quella richiesta, inviata di nuovo come messaggio a parte. Un riavvio con due o più richieste aperte non ne riporta nessuna: i loro pulsanti rispondono `già risposto` e non scrivono niente, e alle richieste puoi ancora rispondere nel terminale. Una sola richiesta aperta torna come prima. In modalità «Completo» e «Automatico», Claude Code nega da solo la propria richiesta per un `rm` o `rmdir` rischioso quando nessuno risponde entro 2 minuti, quindi la sua scheda dice quando (`Se nessuno risponde, Claude Code negherà da solo questa richiesta verso le 14:05.`), e una volta che l'ha fatto, la scheda lo dice e i suoi pulsanti spariscono. Una richiesta così si apre sopra qualsiasi richiesta già aperta, quindi, mentre la richiesta Bash di Claude stesso in modalità «Completo» o «Automatico» è aperta accanto a un'altra, un tocco che deve scrivere dei tasti mostra il messaggio qui sopra in cui aipager dice di non conoscere l'ordine, e Consenti e Nega funzionano ancora finché l'hook aspetta.

All'80% di contesto la chat riceve `⚠️ name · Contesto usato: 82% - presto verrà compattato in automatico` con **📦 Compatta ora**, che invia `/compact` a quella sessione. L'avviso arriva una volta per conversazione: la compattazione non lo fa tornare, e una conversazione nuova (per esempio dopo `/clear`) lo riceve di nuovo.

### <a id="stale-buttons"></a>Pulsanti vecchi

I pulsanti che agiscono su un'attività in corso (Ferma, Termina, Riavvia, il pulsante di cambio di `/mode` e il suo «Ferma e cambia», «Sostituisci» di `/new`) sono legati all'attività per cui sono stati mostrati. Un pulsante rimasto da un'attività precedente non cambia niente invece di agire su quella in corso adesso, e così un pulsante mostrato prima che la sessione terminasse e venisse ripresa o riavviata. Termina, Riavvia, il pulsante di cambio di `/mode` e Ferma in `/status` o nel selettore di `/stop` rispondono `x1 ha iniziato un'altra attività: …` con cosa toccare o inviare di nuovo (oppure `Quella sessione è terminata.`); Ferma sulla scheda di attività risponde `Quell'attività è già finita: …`, e «Ferma e cambia» di `/mode` riporta la sua scheda alla scheda di `/mode` sotto la riga `⚠️ x1 ha iniziato un'altra attività, quindi non è cambiato nulla.` «Sostituisci» di `/new` dice `x1 ha iniziato a lavorare nel frattempo. Invia di nuovo /new per sostituirla.` quando la sessione ha cominciato un'attività dopo che la scheda è stata mostrata.

I pulsanti di una richiesta sono legati a quella richiesta. Quando ha avuto risposta, in Telegram o nel terminale, rispondono `già risposto` e non scrivono niente, anche mentre un'altra richiesta della sessione è ancora aperta.

Un pulsante `⚡ Invia ora` agisce solo sul messaggio sotto cui sta: se Claude ha già preso quel messaggio, il tocco risponde `Claude l'ha già preso`, non invia niente e toglie le righe della coda.

### <a id="idle-responses"></a>Risposte a riposo

Quando un'attività finisce, la risposta arriva secondo il tuo aspetto dei messaggi (vedi sotto e [Impostazioni](#settings)). Se la risposta di Claude è più lunga di quanto contenga un messaggio (circa 32 KB), il messaggio mostra quello che ci sta sotto la riga `📎 Risposta completa nel file qui sotto ↓`, e tutta l'attività (i suoi passaggi e la risposta completa) arriva dopo come `<name>_full_log.md`, che Telegram mostra senza un'altra app. Lo stesso file viene allegato quando la scheda di attività ha dovuto togliere delle righe.

Un'attività finita per un errore dell'API di Anthropic (crediti finiti, sovraccarico, limite di richieste, rete) mostra `⚠️ name · <what went wrong>` al posto della risposta, con **🔄 Riprova**, che reinvia l'ultimo messaggio alla stessa sessione. Mentre la chat [ha gli invii fermi](troubleshooting.md#the-bot-went-quiet-flood-control), toccarlo non fa niente (il messaggio non viene reinviato e il pulsante resta), quindi toccalo di nuovo quando il blocco è finito.

Nell'aspetto con la scheda («Scheda + risposta a parte»), la scheda finita sta sopra la risposta come traccia di come ci si è arrivati: le righe degli strumenti, le righe degli agenti e quello che Claude ha detto nel mezzo. La risposta arriva come messaggio a parte (con notifica). Un'attività che non ha chiamato nessuno strumento (nessuno strumento, agente, comando in background, domanda o richiesta di permesso) lascerebbe solo `✅ name · Finito · Ns`, quindi la sua scheda viene cancellata mentre dice ancora Al lavoro, senza mai diventare Finito, e la prima riga della risposta porta invece il tempo (`💬 name · Finito (…)`). Mandarti un file qui non conta come chiamare uno strumento: i file arrivano dopo la risposta. Un'attività senza una risposta da mostrare, o finita con un errore dell'API, tiene la sua scheda. Per un solo messaggio per attività, scegli «Risposta dentro la scheda» (la risposta va nella scheda) o «Solo la risposta (senza scheda)» (la scheda viene cancellata e la risposta resta da sola).

Ogni attività ha esattamente una scheda di attività. Un messaggio che invii mentre un'attività è in corso non aggiunge mai una seconda scheda per lei, e una scheda la cui attività è finita non tiene più un pulsante Ferma: se niente la chiude (un hook Stop perso, un riavvio), aipager la chiude dopo 30 secondi, secondo il tuo aspetto dei messaggi. Dopo un riavvio di aipager, una scheda la cui attività è ancora in corso viene tenuta e va avanti subito, con il tempo che continua dall'inizio dell'attività, e la sessione si vede al lavoro finché l'attività non finisce; tutte le altre schede rimaste da prima del riavvio vengono chiuse («aipager si è riavviato», pulsante Ferma tolto). Una sessione in attesa di una richiesta di permesso o di una singola domanda di Claude torna in attesa, non al lavoro: la sua scheda e la barra fissata dicono che ha bisogno di te e i pulsanti della richiesta rispondono ancora, a meno che la cronologia non mostri che nel frattempo è stata risposta nel terminale. Le domande con più parti o con scelta multipla, e le richieste fatte da un subagent, restano al terminale. Quando una richiesta è di sicuro chiusa (risposta con un tocco o nel terminale, la sua attività è finita, oppure non riportata dopo un riavvio perché nel frattempo ha avuto risposta o non si poteva salvare e rileggere), i suoi vecchi pulsanti di risposta rispondono «già risposto» e non scrivono niente, anche dopo un riavvio. Una richiesta a cui nessuno risponde resta aperta finché Claude la aspetta. aipager smette di aspettarla solo quando Claude non dà notizie per 5 minuti e la conversazione salvata mostra che la richiesta è superata (la notizia di una risposta è andata persa); una richiesta così non conta come chiusa, quindi i suoi pulsanti rispondono ancora al dialogo. Contano solo le notizie di Claude e quelle dell'agente che ha chiesto: un agente in background che lavora per conto suo non conta, e la conversazione salvata non dice niente della richiesta di un agente, che aspetta le notizie di quell'agente.

Un'attività che Claude avvia **da solo** (un agente in background che riporta il risultato con un `<task-notification>` quando nessun lavoro è aperto) riceve una scheda di attività solo quando fa davvero qualcosa: alla prima chiamata a uno strumento, o dopo 15 secondi, quello che arriva prima. La maggior parte di questi risvegli sono pochi secondi di «niente di nuovo»; ora mostrano solo la risposta. L'indicatore «sta scrivendo…» si vede comunque mentre gira, e la risposta stessa viene sempre inviata: niente viene filtrato come poco importante. Le attività che avvii tu, da Telegram o dal terminale, ricevono comunque una scheda subito.

Mentre una sessione è impegnata, ogni agente in background che Claude avvia (con lo strumento `Agent` di Claude Code, `Task` nelle versioni più vecchie) ha la sua riga sulla scheda di attività: `🤖 <type> · <activity> · <elapsed>`, che mostra il tipo di agente e cosa sta facendo, aggiornata quando arrivano le sue chiamate agli strumenti. Quando quell'agente ha chiamato strumenti tre o più volte, quelle chiamate finiscono in un pulsante `▸ N strumenti usati` a parte, subito sotto la sua riga; non compaiono mai nella cronologia dell'attività principale né nei suoi conteggi `Bash ×N`. Quando l'agente finisce, la sua riga si fissa in `✅ 🤖 <type> · N strumenti usati · <elapsed>` e tiene quel pulsante. Un agente avviato da un altro agente (un aiutante) nomina, prima del proprio tipo, l'agente in cima alla sua catena, `🤖 pipeline-runner › ship-tester`, quando aipager sa chi l'ha avviato nel momento in cui parte; le righe restano nell'ordine in cui gli agenti sono partiti. L'allegato `.md` completo qui sopra ha in più una sezione Agenti che elenca ogni agente che ha girato in quell'attività, quanto è durato, il numero di strumenti e quali strumenti ha chiamato.

Mentre gli agenti in background girano ancora dopo la fine della loro attività, la scheda resta come stato del lavoro. Quando Claude prende un nuovo messaggio in quel momento, la scheda attiva è quella sotto quel messaggio, che mostra ancora gli agenti in esecuzione. Un messaggio che invii a una sessione pronta avvia una nuova attività: la scheda dell'attività precedente si fissa al suo posto, con i passaggi conservati, e la nuova attività ha la sua scheda. Un messaggio messo in coda e poi preso continua l'attività del lavoro: la sua scheda viene rimandata sotto quel messaggio e la vecchia copia cancellata. Le chiamate agli strumenti dentro un agente non compaiono mai come righe dell'attività principale e non fanno mai credere ad aipager che sia iniziata una nuova attività.

Un comando in background di Claude (`run_in_background`, Ctrl+B, o un comando che Claude Code sposta in background perché dura troppo) ha la sua riga mentre gira: `⏳ comando in background: <description> (3:12)`, chiamato con la descrizione della chiamata, altrimenti con la prima riga del comando. Quando finisce, la riga si fissa in `✅ comando in background: <description> - finito (6:12)`, `❌ ... - finito con un errore (codice di uscita 1)` o `⏹ ... - fermato (6:12)` (Claude l'ha fermato con TaskStop, o Claude Code l'ha fermato per mancanza di memoria); le righe con errore e fermate tengono il loro segno sulla scheda finita. Un'attività che finisce mentre un comando così è ancora in esecuzione non è finita: la scheda aspetta come stato del lavoro (`🔄 name · aspetta 1 comando in background · 4:10`, oppure `aspetta 1 agente e 1 comando in background` quando ci sono anche agenti), e quando il comando finisce il seguito di Claude continua sulla stessa scheda. I comandi avviati dentro un agente sono di quell'agente e non vengono mostrati. Un comando di cui aipager non vede mai la fine (un riavvio, un evento perso) smette di essere contato dopo due ore (`AIPAGER_BG_SHELL_MAX_TRACK`, in secondi): la sua riga dice `⏹ ... - fine non vista`, e un lavoro che aspettava solo lui si chiude con `⚠️ name · Finito (2:00:04, nessuna notizia dal comando in background)`.

Quando la cronologia di un'attività si allunga, ogni gruppo di chiamate più vecchie (tre o più di fila, e non quello in corso) finisce in un pulsante `▸ N strumenti usati` a parte al suo posto, invece di accumularsi per intero o essere tagliato dal limite di lunghezza dei messaggi di Telegram; tocca un pulsante qualsiasi per leggerlo sul posto. Il commento non viene mai raccolto; l'attività più recente e la riga di stato si vedono sempre senza toccare niente. La riga di un agente in background ancora in esecuzione (o appena fissata) non finisce mai in un pulsante, solo le sue chiamate agli strumenti, e mai quando è l'unica cosa tra la cronologia e il limite. Solo quando la cronologia è così grande che non ci sta nemmeno con tutto raccolto, qualcosa esce davvero dalla scheda; allora l'allegato `.md` qui sopra contiene tutta la traccia.

#### <a id="agents-still-running-when-the-answer-goes-out"></a>Agenti ancora al lavoro quando parte la risposta

Claude Code può far girare agenti in background, e l'attività che li ha avviati può finire mentre loro lavorano ancora. Allora la risposta finisce con una linea divisoria e un breve stato di quel lavoro in background, nello stile del pannello degli agenti di Claude Code, aggiornato mentre gira. Tutto quello che sta sotto la linea è in testo piccolo (il piè di pagina di Telegram), così la risposta risalta:

```
⏳ 5 agenti al lavoro · 🖥 1 comando in background (shell) in esecuzione

○ 🤖 pipeline-runner · Writing final.md for the pipeline

- 🤖 ship-tester · Write and run criteria tests
- 🤖 ship-developer · Editing state.py
- 🤖 ship-developer › ship-reviewer · Collect line numbers

‖ 🤖 r2-runner · aspetta i suoi agenti

Quando gli agenti finiscono, il risultato arriva proprio qui.
```

- **La prima riga conta.** Gli agenti al lavoro (o finiti), e i comandi in background (shell) di questo lavoro in esecuzione, finiti o fermati. Un comando in background è sempre solo un numero, mai il suo comando.
- **Una riga per agente,** un 🤖 e il tipo in grassetto, poi cosa sta facendo: la sua ultima riga di testo (le parole di Claude, mostrate come sono scritte, mai tradotte), altrimenti il suo ultimo strumento come breve frase che dice su cosa lavora (`Reading state.py`, `Writing final.md`, `Searching for …`, o la descrizione propria di un comando), altrimenti il compito che Claude ha dato all'agente quando l'ha avviato, altrimenti solo il suo ultimo strumento (`Running a command`, `Using TodoWrite`), altrimenti solo il tipo. Così due agenti dello stesso tipo che eseguono comandi senza descrizione mostrano comunque ognuno il proprio compito. Un agente che si è fermato ad aspettare agenti avviati da lui stesso dice invece `‖ … aspetta i suoi agenti`.
- **Gli agenti ad albero.** Gli agenti avviati dalla sessione sono il primo livello. Ogni agente avviato da uno di loro, a qualsiasi profondità, è elencato subito sotto, ognuno subito dopo l'agente che l'ha avviato; uno avviato da un agente del secondo livello nomina prima quell'agente (`ship-developer › ship-reviewer`). La scheda di attività nomina invece lo stesso agente dopo l'agente in cima (`pipeline-runner › ship-reviewer`). Non ci sono mai più di due livelli. Un agente al lavoro sul secondo livello non ha un ○ suo: lì Telegram disegna un punto elenco. Un agente di cui aipager non sa ancora chi l'ha avviato si vede al primo livello, mai sotto un'ipotesi, e si sposta sotto chi l'ha avviato appena si sa.
- **Al massimo sei righe,** prima gli agenti ancora al lavoro, poi `+N altri agenti` per gli agenti senza riga. Un agente non compare mai senza gli agenti sopra di lui.
- **La promessa** «Quando gli agenti finiscono, il risultato arriva proprio qui.» resta solo finché qualche agente lavora ancora. Un comando in background non ha mai questa promessa: un server di sviluppo potrebbe non finire mai.

La riga di ogni agente si fissa da sola quando quell'agente si ferma, in `✓ 🤖 Explore · finito (0:42)` (quanto ha girato quell'agente), e la prima riga scende. Un agente che un agente dell'elenco avvia dopo l'invio della risposta entra nello stato sotto di lui al successivo aggiornamento; uno che la sessione stessa avvia più tardi compare invece sotto una risposta successiva. Quando tutti gli agenti hanno finito, le righe lasciano il posto a una sola riga, con il tempo dalla risposta alla fine dell'ultimo agente:

```
✅ 2 agenti hanno finito (6:12) · 🖥 1 comando in background (shell) in esecuzione
```

e quando quel comando in background finisce, `✅ 2 agenti hanno finito (6:12) · 1 comando in background (shell) finito (2:13:05)` (quanto ha girato il comando; `fermato` quando è stato terminato o fermato da Claude, `finito` quando è finito da solo, con o senza errore; la sua riga sulla scheda dice quale). Una risposta il cui lavoro ha solo un comando in background mostra `🖥 1 comando in background (shell) in esecuzione` e si fissa in `1 comando in background (shell) finito (…)` o `1 comando in background (shell) fermato (…)`.

Vengono contati solo i comandi in background del lavoro della risposta stessa: un server di sviluppo lasciato acceso da un'attività precedente non si ripete sotto ogni risposta (la barra fissata conta comunque ogni comando in background). `/stop` chiude il lavoro, ma Escape non chiude un comando in background: un comando ancora in esecuzione resta sulla barra fissata finché non finisce e non viene più contato sotto le risposte successive; il suo seguito, più avanti, avvia un'attività a parte.

Quella risposta viene inviata appena l'attività finisce, come messaggio normale (con notifica) in risposta al tuo messaggio: esattamente il testo che mostra il terminale. La scheda di attività resta sopra come stato dal vivo del lavoro, in ogni aspetto: `🔄 name · aspetta 1 agente · 1:18`, con il suo pulsante **Ferma**. Quando gli agenti riportano il risultato, la risposta di Claude per quel pezzo arriva come nuovo messaggio a parte; la risposta precedente non viene mai rimandata, nemmeno dopo un riavvio di aipager. Un lavoro i cui agenti riportano più volte riceve una risposta per ogni ritorno, ognuna inviata appena è scritta.

Un agente che si è fermato mentre il suo stesso lavoro in background è ancora in esecuzione conta come al lavoro, perché riprenderà. Un agente che ha avviato un agente suo e poi si è fermato per aspettarlo viene riconosciuto da quell'avvio (la sua stessa chiamata che ha avviato l'altro agente): resta nello stato come al lavoro, e tiene aperto il lavoro, finché non si ferma senza più nessuno dei suoi agenti in esecuzione. Se non ha ripreso due minuti dopo la fine dell'ultimo di essi, non viene più contato come in esecuzione e lo stato resta com'era mostrato l'ultima volta. Il `<task-notification>` di Claude per un agente dice lo stesso in una nota; aipager la legge quando quell'avviso avvia un'attività, e anche quando Claude lo prende a metà di un'attività in corso mentre la scheda di attività è visibile. Una riga aspetta qualche secondo prima di mostrarsi finita, così quell'avviso fa in tempo a riprenderla. Lo stato viene aggiunto in ogni aspetto, nella lingua della chat.

**Come si aggiorna.** aipager modifica la risposta in silenzio, solo quando cambia quello che mostra lo stato, e al massimo una volta ogni 30 secondi per risposta (la risposta stessa conta come prima volta). Ogni risposta con uno stato si aggiorna così (fino a cinque risposte in attesa per sessione). Le modifiche hanno priorità bassa: una modifica rifiutata dal budget di invio, o che capita mentre la chat ha gli invii fermi, non viene fatta, e lo stato aspetta il cambiamento successivo. L'ultima modifica, quando è tutto finito, viene ritentata una volta dopo la fine del blocco o quando il budget la rifiuta, poi si lascia perdere. Nessuna modifica va a una risposta cancellata, a una sessione terminata o chiusa, o quando renderebbe la risposta troppo lunga per Telegram (quando una risposta lunga viene tagliata, si lascia spazio per lo stato).

Lo stato resta com'era l'ultima volta, mai trasformato in finito, quando aipager non può sapere che il lavoro è finito:

- un agente di cui aipager non ha notizie da 30 minuti (`AIPAGER_SUBAGENT_SILENCE`) non viene più contato in esecuzione, ma il silenzio non vuol dire che abbia finito;
- un comando in background in esecuzione da più di due ore (`AIPAGER_BG_SHELL_MAX_TRACK`) senza una fine vista non viene più contato;
- un riavvio di aipager dimentica quali risposte erano in attesa.

Un riavvio di aipager conserva gli agenti ancora in esecuzione, anche quelli che aspettano i propri agenti: una risposta inviata dopo il riavvio li elenca, una scheda di attività che il riavvio riprende li mostra di nuovo, e la loro fine viene riconosciuta come sempre. Gli agenti salvati più di 30 minuti prima del riavvio non vengono recuperati.

Una risposta trattenuta per un blocco degli invii, o inviata come testo semplice dopo che l'invio formattato è fallito, viene inviata senza stato, e così un'attività finita senza testo di risposta (solo la prima riga).

### <a id="end-confirmation"></a>Conferma di chiusura

`/kill`, il suo selettore e `⋮` → `⏹ Termina la sessione` chiedono sempre conferma:

```
⏹ Terminare jim? Claude si ferma e la sessione si chiude.

  [⏹ Termina]  [Annulla]
```

Dopo `⏹ Termina` il messaggio dice `⏹ jim terminata` sopra le altre sessioni della chat. Una conferma toccata dopo che la sessione ha iniziato un'altra attività viene rifiutata (`jim ha iniziato un'altra attività: …`), e così la conferma per una sessione con lo stesso nome avviata dopo.

### <a id="voice-install-when-extra-isnt-installed"></a>Installare la funzione vocale (quando il pacchetto in più non c'è)

Quando invii un vocale e `aipager[voice]` non è installato:

```
⚠️ Per i vocali serve un pacchetto opzionale
   (circa 200 MB da installare e circa 74 MB di modello al primo uso).

  [📦 Installa la funzione vocale]  [Annulla]
```

Toccare Installa esegue il comando di installazione giusto per il tuo installatore (`uv tool install --reinstall aipager[voice]`, `pipx install --force`, oppure, come riserva, `pip install faster-whisper`), modifica il messaggio ogni 5 secondi per dire che è ancora in corso e, se riesce, offre un pulsante `[🔄 Riavvia aipager]`.

### <a id="restart"></a>Riavvio

`🔄 Riavvia aipager`:

- Un aipager che gira come servizio utente di systemd programma un `systemctl --user restart aipager.service` separato tra 5 secondi (un timer preciso al secondo), in un servizio temporaneo fuori dal gruppo di processi di aipager stesso, così sopravvive all'uscita di aipager. Si rifiuta quando il file di servizio terminerebbe le tue sessioni (`KillMode` diverso da `process` o `none`) e ti dice di eseguire prima `aipager service install`.
- Su macOS, quando l'agente launchd è installato (`~/Library/LaunchAgents/com.aipager.daemon.plist`): `launchctl kickstart -k gui/<uid>/com.aipager.daemon`, anche per un aipager avviato da te.
- Altrimenti, un aipager avviato da te (`aipager start`), su Linux anche quando c'è il servizio: crea un sostituto separato che aspetta la fine del padre e poi esegue lo stesso comando con cui era stato avviato aipager (il suo output va nel log del riavvio automatico). L'aipager attuale si ferma in modo pulito appena il sostituto è in vita.

Non serve SSH. Per completare un aggiornamento di aipager, il pulsante **🔄 Riavvia ora** della scheda dell'aggiornamento riavvia invece aipager sul posto, nello stesso terminale (vedi [Aggiornamento](#update)).

## <a id="update"></a>Aggiornamenti

`/update` (solo admin; nell'uso personale, solo il proprietario del bot) risponde con un solo pulsante, **🔄 Cerca aggiornamenti**. Niente viene cercato finché non lo tocchi. Il tocco cambia quel messaggio in una riga per prodotto:

- `aipager 1.0.1 → 1.1.0` quando c'è una versione più recente (l'ultima su PyPI);
- `aipager 1.0.1 (aggiornato)` quando non c'è;
- `Claude Code 2.1.282 (controllo non riuscito)` quando il controllo fallisce (rete giù, attesa di 5 secondi scaduta, o `claude` non trovato).

Claude Code viene confrontato con la versione più recente del suo canale di aggiornamento (`autoUpdatesChannel`: latest, stable o rc). Sotto quelle righe, in piccolo, c'è come è stato installato aipager (per esempio `pipx, from PyPI` o `pipx, from local path …`; una chat di gruppo non mostra mai il percorso).

Se c'è qualcosa di più recente, c'è UN pulsante che aggiorna solo i prodotti che hanno un aggiornamento: **Aggiorna aipager**, **Aggiorna Claude Code** o **Aggiorna entrambi**, più **Annulla**. Quando viene proposto un aggiornamento di aipager, il messaggio dice anche come andrà il riavvio: `Riavvio: automatico, quando nessuna attività è in corso.`, `Riavvio: tocca «Riavvia ora» quando è installato.` (un aipager senza servizio, vedi sotto) oppure `Riavvio: manuale (motivo)`.

Se un aipager più recente è già installato ma l'aipager in funzione è ancora quello vecchio (un aggiornamento finito senza riavvio, o un `pip install -U` a mano), la riga dice `aipager 1.1.0 è installato e aspetta un riavvio (in funzione: 1.0.1).` e il messaggio offre **🔄 Riavvia ora** invece di un aggiornamento: Aggiorna aipager non viene mai proposto per una versione che ha bisogno solo di un riavvio. La versione installata si controlla avviando l'interprete in cui aipager si riavvierebbe, quindi una versione installata che non si carica lo dice (`aipager 1.1.0 è installato ma non si carica. Reinstallalo con …`) e non offre niente. Una versione pubblicata più recente di quella installata viene comunque proposta come aggiornamento. Un aipager più recente su un'installazione che non si può aggiornare da qui (editable, Nix, Snap, pacchetto del sistema, container o l'installazione di un altro utente) viene mostrato con il motivo e non proposto. Così anche un Claude Code più recente installato con npm in una cartella in cui il tuo utente non può scrivere (per esempio `sudo npm install -g` in `/usr/lib/node_modules`), dove `claude update` può solo fallire: la sua riga dice `non si può aggiornare da qui: installed with npm into a folder your user can't write to; run "claude install" once to switch to Claude Code's own installer, then restart aipager`. Se non c'è niente da aggiornare, il messaggio dice «Tutto è aggiornato.», riferisce un controllo fallito oppure, quando c'è una versione più recente che non si può installare da qui, «C'è un aggiornamento disponibile, ma non si può installare da qui.», con un pulsante **Ricontrolla** e nessun pulsante di aggiornamento.

Il pulsante di aggiornamento esegue solo quello che il controllo ha proposto. Se il controllo ha più di 10 minuti, se nel frattempo è girato un altro aggiornamento o se le versioni non corrispondono più al pulsante, ti dice di ricontrollare. I pulsanti di un vecchio menu di `/update` (i pulsanti separati per prodotto **Aggiorna Claude Code** / **Aggiorna aipager** / **Entrambi**) rispondono «Questo menu è vecchio: invia di nuovo /update» e non fanno niente. Ogni tocco ricontrolla la regola «solo admin».

Aggiornare Claude Code esegue `claude update` (con il percorso assoluto, aspettando fino a 5 minuti) e riferisce `Claude Code A → B`, «è già aggiornato» o l'errore con la fine del suo output. Le sessioni in esecuzione tengono la versione vecchia finché non le riavvii (`/restart`); la risposta le elenca. Nessuna viene riavviata al posto tuo. Le nuove sessioni usano la nuova versione.

Aggiornare aipager (**Aggiorna aipager**, o la seconda metà di **Aggiorna entrambi**, dopo Claude Code):

1. Con un'installazione da PyPI già all'ultima versione, lo dice e si ferma.
2. Se aipager può riavviarsi da solo, prima **aspetta che non ci sia niente a metà**: nessuna sessione che esegue un'attività, aspetta una domanda, fa girare un agente in background, mostra una scheda di attività attiva, esegue uno strumento o ha una richiesta di permesso aperta, e nessuna risposta trattenuta in attesa che finisca un limite di frequenza. Il messaggio elenca cosa sta aspettando, con **Riavvia ora** (salta l'attesa) e **Annulla**. Dopo 10 minuti chiede di nuovo: **Aspetta altri 10 min**, **Riavvia ora**, **Annulla**. Se nessuno risponde per un'ora, si annulla da solo.
3. Aggiorna tramite l'installatore che ha installato l'aipager in esecuzione (`pipx upgrade aipager`, `uv tool upgrade aipager`, `brew upgrade aipager`, oppure `<venv>/bin/python -m pip install --upgrade aipager`), con il percorso assoluto, aspettando fino a 10 minuti. Mentre l'installatore gira non c'è il pulsante Annulla.
4. Controlla che la nuova versione si importi in un interprete nuovo. Un aggiornamento fallito, scaduto o che non si importa non riavvia niente. Un aggiornamento scaduto è stato fermato a metà, quindi il messaggio avvisa che l'installazione potrebbe essere incompleta e dà il comando di reinstallazione da eseguire prima del prossimo riavvio. L'output dell'installatore si vede solo in una chat privata; un gruppo riceve «(l'output è nel log di aipager)».
5. Se durante l'aggiornamento parte un'attività, aspetta di nuovo.
6. Programma un `systemctl --user restart aipager.service` separato tra 5 secondi (un timer preciso al secondo), e il messaggio di stato dice `⏳ aipager A → B installato, riavvio in corso…`. Mentre il passo 5 aspetta, dice `⏳ aipager A → B installato; si riavvia quando finisce l'attività in corso`. Dopo il riavvio il nuovo aipager modifica quello stesso messaggio in `✅ aipager aggiornato A → B; N sessioni sono state ricollegate` (e `⚠️ Non sono tornate: …` per quelle che non sono tornate). Se il messaggio è stato cancellato o non si può più modificare, pubblica il risultato come nuovo messaggio nella chat che l'ha chiesto.

Nella Mini App, **Impostazioni → Aggiornamenti** mostra «Riavvio di aipager…» con una lanterna che gira finché il nuovo aipager non risponde, poi «Aggiornato alla B; N sessioni sono state ricollegate.». Non mostra mai un conto alla rovescia. Dopo circa 2 minuti senza risposta (mai prima che il vecchio aipager possa riferire che un riavvio non è avvenuto) dice «Riavvio ancora in corso; riapri l'app tra un momento.». Con il tunnel gestito di aipager il riavvio di un aggiornamento tiene l'indirizzo dell'app (il nuovo aipager riprende il tunnel), quindi la pagina aperta resta su e mostra «Aggiornato alla B». Se alla fine il tunnel non si è potuto tenere, la pagina dice «Riavvio ancora in corso; riapri l'app tra un momento.» dopo circa 2 minuti. Un aggiornamento avviato da un aipager senza questa funzione (o con `MINIAPP_KEEP_TUNNEL=0`) cambia ancora l'indirizzo: quando il vecchio aipager smette di rispondere, la pagina aperta dice «aipager si è riavviato; riapri l'app quando la chat dice che è aggiornato»; chiudila e riaprila dal pulsante del menu della chat o da `/app`. L'accesso dell'app dura 5 minuti, quindi una pagina aperta più a lungo dice invece «Riapri l'app per vedere il risultato dell'aggiornamento.».

Dopo l'aggiornamento aipager si riavvia da solo solo quando gira come servizio utente di systemd **e** quel file di servizio ha `KillMode=process` (o `none`). Con un `KillMode` non sicuro aipager viene comunque aggiornato, e il messaggio dice `Serve un riavvio: …`: esegui prima `aipager service install` (elenca le sessioni che un riavvio terminerebbe).

**Riavvia ora, senza servizio.** Un aipager che nessun gestore di servizi riavvia (`aipager start` in un terminale o in tmux, Docker, o l'agente launchd su macOS) installa l'aggiornamento e poi mostra `Tocca «Riavvia ora» per avviarla (aipager aspetta che nessuna attività sia in corso).` con un pulsante **🔄 Riavvia ora**. Toccarlo (solo admin, come ogni pulsante dell'aggiornamento):

1. controlla che l'aipager installato si carichi (`⏳ Controllo dell'aipager installato…`); un'installazione rotta non riavvia niente, e con la stessa versione dice che non c'è niente da riavviare;
2. aspetta, come il riavvio di systemd, che non ci sia niente in corso, con **Riavvia ora** (salta l'attesa) e **Annulla**;
3. controlla di nuovo l'installazione, poi dice `⏳ aipager A → B installato, riavvio in corso…` e ferma aipager in modo pulito (come fa Ctrl+C);
4. avvia il nuovo aipager sul posto: lo stesso processo, lo stesso comando, lo stesso ambiente e la stessa cartella, nella stessa finestra del terminale o di tmux (e, in Docker, lo stesso processo del container). Le sessioni continuano a girare. Il nuovo aipager modifica il messaggio in `✅ aipager aggiornato A → B; N sessioni sono state ricollegate`.

I tocchi inviati nei pochi secondi del riavvio vanno persi, come in ogni riavvio. Un Ctrl+C o un `docker stop` durante il riavvio vince: aipager si ferma e non riparte. Se il nuovo aipager non si può avviare sul posto, una copia separata lo avvia quando il vecchio non c'è più (il suo output va nel log del riavvio automatico, `aipager.log` nella cartella dell'istanza o `/tmp/aipager.log`, e il log di aipager indica il comando da eseguire se quella copia non torna); se anche questo fallisce, o l'interprete stesso non c'è più, aipager esce con codice 1 e il log di aipager dice `restart.failed:` con il comando da eseguire.

Quando aipager non può riavviarsi da solo in nessun modo, il messaggio dà i passaggi esatti per il tuo ambiente al posto di un pulsante:

- un terminale o tmux: `Nella finestra del terminale o di tmux dove gira aipager, premi Ctrl+C, poi esegui aipager start.`
- macOS: `Sul Mac, esegui launchctl kickstart -k gui/<uid>/com.aipager.daemon.`
- Docker: `Sul computer dove gira Docker, esegui docker restart <container>.` (l'id breve del container quando il suo nome host è uno di questi), con un promemoria che ricreare il container dalla sua immagine riporta la versione di aipager di quell'immagine.

**Riavvia ora** compare anche nel risultato di **Cerca aggiornamenti** quando un aipager più recente è installato e aspetta, e in un avviso che arriva una volta sola: quando gli hook di Claude Code (che ripartono da zero a ogni evento, dall'aipager installato) eseguono già un aipager più recente di quello in funzione, il proprietario riceve un messaggio silenzioso per ogni versione più recente e ogni esecuzione di aipager, `⚠️ L'aggiornamento di aipager è fatto solo a metà: …`, con il pulsante. Aspetta due minuti da quando gli hook hanno eseguito la versione più recente per la prima volta (un riavvio subito dopo un'installazione non lo mostra mai), poi che nessuna attività sia in corso nella chat privata del proprietario. Dopo il riavvio in quella versione, l'avviso diventa `✅ L'aggiornamento di aipager è completato: ora aipager è alla B.`, senza il pulsante. Nella Mini App, **Impostazioni → Aggiornamenti** mostra lo stesso stato come una riga sopra la scheda, con **Riavvia ora** (chiede prima).

Nella Mini App, **Aggiornamenti** è la prima scheda della sezione Impostazioni: il suo nome, la versione in funzione e un piccolo **Cerca aggiornamenti** in fondo alla riga. Aprire la sezione Impostazioni controlla da solo (al massimo una volta ogni dieci minuti, mai mentre gira un aggiornamento o aipager si riavvia; un controllo che lì fallisce non dice niente), e un tocco controlla sempre. La scheda resta una riga tranquilla finché non c'è qualcosa da fare: una versione più recente porta le sue righe e l'unico pulsante di aggiornamento, «Tutto è aggiornato.» è una sola riga senza pulsante, e un aggiornamento in corso, un riavvio o **Riavvia ora** tengono i loro pulsanti.

Gira un solo aggiornamento alla volta, contando insieme `/update`, la scheda **Impostazioni → Aggiornamenti** della Mini App (stesso controllo, stesso unico pulsante di aggiornamento, stessa attività) e `aipager update` dalla riga di comando. Questo comprende i secondi tra «installato, riavvio in corso…» e il riavvio vero: `/update` risponde che aipager sta per riavviarsi, la Mini App non offre pulsanti, e il pulsante **Riavvia aipager** della funzione vocale si rifiuta mentre un aggiornamento è in corso o aspetta il riavvio. Se aipager è ancora vivo due minuti dopo aver programmato il riavvio, ferma il timer del riavvio in attesa, rilascia il lock dell'aggiornamento e ti dice di riavviarlo tu.

Se aipager si spegne mentre un installatore è in esecuzione, l'installatore ha 3 secondi per finire e poi viene fermato (altrimenti continuerebbe a scrivere nell'installazione mentre parte il prossimo aipager). Il messaggio di stato e, dopo il riavvio, un nuovo messaggio dicono che l'installazione potrebbe essere incompleta e come sistemarla.

Una volta iniziata la chiusura, niente si riavvia e niente di nuovo parte: i pulsanti di `/update` rispondono che aipager si sta spegnendo, la Mini App risponde 503, e non parte nessun installatore né controllo di versione. Un installatore che finisce entro i suoi 3 secondi conta comunque. Il messaggio di stato dice che la nuova versione è installata e che non si è riavviato niente, e l'avvio successivo annuncia `aipager aggiornato A → B`. Un aipager che hai fermato con `aipager service stop` resta fermo. La parte di aggiornamento della chiusura dura in tutto al massimo 8 secondi.

## <a id="free-messages"></a>Messaggi liberi

### <a id="text"></a>Testo

Viene trattato come il prossimo messaggio per la **sessione attuale**: la destinazione di questa chat, quella che hai scelto per ultima, a cui hai scritto, che hai avviato, o quella che ti ha appena risposto qui (vedi [barra di stato fissata](#the-pinned-status-bar)). In un gruppo ogni persona ha la sua destinazione, che la risposta di una sessione non cambia (vedi [gruppi](groups.md#talking-to-a-session-in-a-group)). Il messaggio arriva a Claude **subito**, anche mentre la tua attività è in corso, proprio come scrivere nel terminale (in un gruppo, il messaggio di un'altra persona aspetta che quell'attività finisca; vedi sotto). Se ne invii diversi, si mettono in coda dentro Claude stesso, e Claude li prende uno alla volta a una pausa naturale:

La reazione sul tuo messaggio lo segue, come un messaggio in coda in Claude Code passa dal grigio al bianco:

- 👀: consegnato alla sessione (o trattenuto, vedi sotto), ma Claude non l'ha ancora preso. Un messaggio inviato mentre un'attività è in corso resta 👀 mentre aspetta nella coda di Claude.
- 👍: Claude l'ha preso: ha avviato un'attività, l'ha unito a quella in corso o l'ha passato a un agente in background in esecuzione.
- 🤷: non verrà mai preso: un messaggio trattenuto scartato da `/stop`, `/clearqueue` o `/kill`, o il cui invio è fallito quando è stato rilasciato; un messaggio o un comando ancora in attesa scartato da `/stop`, `/clearqueue`, `/kill` o dalla fine della sessione (non da `/clear` o `/resume`); oppure un messaggio rifiutato da Claude Code (vedi sotto). I messaggi che Claude ha messo in coda vengono segnati solo quando aipager può ancora vedere quella coda (la lettura dal vivo della cronologia è attiva e nessun lavoro in background è in attesa); altrimenti restano 👀. Escape nel terminale riporta la coda di Claude nel campo di scrittura, da dove si può reinviare, quindi anche un messaggio scartato così resta 👀.
- 👌: è stato eseguito un comando di Claude Code (vedi sotto), oppure aipager ha confermato `/stop`.

Una reazione va solo avanti, quindi un messaggio riceve al massimo tre reazioni, una per tipo. Una reazione che capita mentre Telegram blocca per un limite di frequenza viene saltata e non recuperata dopo, e una pulizia segna al massimo i dieci messaggi più recenti che scarta.

Pulsanti dei comandi (`Compatta`, `/model …`): toccati con la sessione pronta, il comando riceve 👌 subito; Claude Code lo esegue a Invio, e un comando locale come `/model` non emette nessun hook che possa riferire più tardi. Toccati mentre un'attività è in corso (tranne `/model`: viene rifiutato finché l'attività non finisce, vedi [cambiare il modello di una sessione in esecuzione](#switching-a-running-sessions-model)) restano 👀 finché quell'attività non finisce (normalmente, come pausa di un lavoro in background o per un errore dell'API) e poi 👌; scartati prima da `/stop`, `/clearqueue`, `/kill` o dalla fine della sessione, non vengono mai eseguiti: 🤷. Quando Claude Code lo mette in coda come messaggio, segue la strada 👀 → 👍. Un vocale riceve le stesse reazioni del testo, una volta inviata la sua trascrizione.

La scheda di attività e la risposta finale seguono il messaggio che Claude ha davvero usato per un'attività (quello con cui ha avviato la sessione pronta, o quello che ha unito all'attività in corso), mai semplicemente l'ultimo che hai inviato. Mandare un altro messaggio a metà non fa «saltare» la risposta su di lui: se Claude lo unisce alla risposta che sta formando, la scheda si sposta sotto di lui in quel momento; se Claude prima finisce e poi lo prende, la prima risposta resta sotto il primo messaggio, e quello aggiunto ha la sua attività, con la sua scheda e la sua risposta sotto.

Se Claude Code rifiuta del tutto un messaggio (un comando slash sconosciuto, o uno integrato che apre solo un dialogo nel terminale), non parte nessun hook, quindi niente chiude l'attività che aipager ha appena annunciato. Dopo 8 secondi senza hook (`PROMPT_HOOK_GRACE_SECONDS`), la scheda di attività diventa `⚠️ name · Claude Code non ha ricevuto il messaggio` con una riga di spiegazione e la sessione torna pronta; il motivo è nel terminale, e il messaggio riceve 🤷 (un comando slash riceve 👌: anche un comando integrato che apre un dialogo non emette hook). Solo un messaggio che ha avviato una nuova attività viene giudicato così; un messaggio in coda dietro un'attività in corso resta 👀 finché Claude non lo prende o non viene scartato.

Tre casi vengono trattenuti invece di essere inviati, e partono da soli quando finiscono:

- C'è una richiesta di permesso o una domanda aperta, per tutto il tempo in cui resta aperta (non c'è un limite di tempo): altrimenti il tuo testo verrebbe letto come la risposta a quel dialogo. Il tuo primo messaggio riceve allora una risposta, sotto la richiesta, che dice che Claude aspetta ancora la tua risposta (i successivi ricevono solo il 👀). Appena rispondi, su Telegram o nel terminale, i messaggi trattenuti arrivano a Claude nell'ordine in cui li hai mandati e quella risposta viene cancellata. Se la sessione finisce con la richiesta ancora aperta, vengono scartati con un 🤷.
- (Uso in team) il messaggio di un'altra persona sta ancora aspettando di essere preso.
- (Uso in team) è in corso l'attività di un'altra persona, compreso un agente in background che quell'attività ha avviato. Un'attività scritta nel terminale conta come del proprietario, quindi vi si unisce solo il messaggio del proprietario (o, in una chat privata, quello del membro di quella chat).

Gli ultimi due tengono i messaggi di persone diverse fuori da una stessa attività, così ognuno gira con i permessi del proprio mittente. Se uno si unisce comunque (per esempio un mittente che aipager non conosce, che non viene mai trattenuto), l'attività gira con le regole più restrittive di tutti quelli che contiene (vedi [gruppi](groups.md#who-a-message-runs-as)). Se l'attività che un messaggio aspetta viene fermata, o bloccata per uno strumento vietato, i messaggi trattenuti vengono scartati con un 🤷.

Ogni sessione ne tiene al massimo 50 e scadono dopo 24 ore; `/clearqueue` li scarta insieme a tutto quello che Claude tiene.

#### <a id="send-a-queued-message-now"></a>Inviare subito un messaggio in coda

Un messaggio che invii mentre Claude lavora a una tua attività arriva a Claude subito (aipager non lo trattiene) e aspetta nella coda di Claude finché il passaggio in corso non finisce. Appena la coda di Claude lo mostra, aipager risponde sotto:

```
⏳ In coda: Claude lo leggerà dopo il passaggio in corso
[⚡ Invia ora]
```

Ogni messaggio in coda ha la sua riga, e ogni riga sparisce, insieme al suo 👍, appena Claude prende quel messaggio. Un messaggio che Claude prende prima che si veda la sua voce in coda non ha nessuna riga, solo 👀 e poi 👍.

Toccare `⚡ Invia ora` (o inviare `/now`) preme in quella sessione la scorciatoia «invia ora» di Claude Code (Ctrl+X, poi Ctrl+S). Allora Claude legge tutto quello che ha in coda insieme, non solo il messaggio che hai toccato. Cosa succede al lavoro in corso lo decide Claude Code: un comando o un agente in esecuzione continua in background; se Claude sta scrivendo una risposta, quella risposta si interrompe e riparte insieme al tuo messaggio. Anche qualsiasi altro tipo di passaggio (per esempio un altro strumento) può essere interrotto, dopo una breve attesa. In alcuni casi (per esempio con le attività in background di Claude Code disattivate) «invia ora» torna a essere una normale interruzione del passaggio in corso. È tutto il modo di inviare subito di Claude Code, non qualcosa che decide aipager.

Il tocco risponde `Inviato subito a Claude` e tutte le righe spariscono insieme, perché «invia ora» consegna a Claude tutto quello che teneva; `/now` le toglie allo stesso modo, e non c'è nessuna riga per i messaggi che «Invia ora» ha già consegnato (un messaggio che invii dopo ha la sua riga). Se Claude annulla il passaggio in corso invece di spostarlo in background, prende subito solo il messaggio in coda più vecchio e legge il resto al passaggio successivo; la risposta sta sotto il messaggio che ha preso, e quel messaggio non gira una seconda volta. Se i tasti ci mettono un po' di più ad arrivare a Claude (il computer è occupato), il tocco risponde invece `Invio subito a Claude`; allora la riga sparisce quando i tasti arrivano oppure, se non ci riescono, resta e dice `⏳ In coda: Claude non è raggiungibile, tocca «Invia ora» per riprovare`, con il suo pulsante, così puoi toccare di nuovo. I tasti vengono premuti solo quando la cronologia di Claude mostra che tiene ancora il messaggio; altrimenti il tocco risponde `Claude l'ha già preso` e toglie la riga. Viene rifiutato, senza premere niente, mentre c'è una richiesta di permesso o una domanda aperta (`Prima rispondi alla domanda aperta`), mentre Claude potrebbe aver appena aperto una richiesta che aipager non ha ancora mostrato (gli eventi di un'attività nuova vengono trattenuti mentre la risposta di quella precedente viene ancora inviata: `Occupato, riprova tra un momento`), e mentre aipager sta scrivendo un altro messaggio nella sessione (`aipager sta scrivendo un messaggio; riprova tra un momento`). Chiunque possa inviare messaggi alla sessione può usare il pulsante e `/now`; un membro `read_only` no.

La riga sparisce da sola quando il suo messaggio lascia la coda di Claude: Claude lo prende (unito all'attività in corso, passato a un agente in background, o avviato come attività successiva), Escape nel terminale riporta la coda nel campo di scrittura, oppure viene scartato da `/stop`, `/clearqueue`, `/kill`, `/restart`, uno stop di sicurezza o la fine della sessione. Un riavvio di aipager toglie tutte le righe ancora visibili.

Queste righe non aspettano mai il gestore dei limiti di frequenza: partono e si tolgono subito anche in una chat impegnata o rallentata, e in modalità minima. Solo quando è Telegram a fermare gli invii della chat non parte nessuna riga (`/now` funziona comunque), e se Telegram ha appena chiesto ad aipager di rallentare, una riga aspetta proprio i pochi secondi che Telegram ha chiesto. Ogni messaggio in attesa costa una chiamata alla chat per la sua riga; le righe che vanno insieme si tolgono con una sola chiamata.

### <a id="files"></a>File

I file caricati vengono scaricati in una cartella privata della chat da cui arrivano, `/tmp/aipager-files/<chat>/` (con il nome `<time>_<file name>`; `<chat>` è un nome ricavato dall'ID della chat e dal token del bot, che non rivela l'ID della chat). Le cartelle le può leggere solo l'utente con cui gira aipager (`0700`, i file `0600`), e l'attività di un membro in una chat non può leggere, cercare o scrivere i file caricati di un'altra chat (vedi [sicurezza](security.md#team-mode-enforcement)). I file di prima di questa modifica restano dov'erano, direttamente in `/tmp/aipager-files/`; non viene spostato né cancellato niente, e all'avvio aipager restringe la cartella a `0700`, così solo quell'utente ci arriva. Con un nuovo token del bot ogni chat riceve una nuova cartella; i file caricati prima restano in quelle vecchie, che l'attività di un membro non può più leggere. Una cartella di download che è un link simbolico, o che appartiene a un altro utente, viene rifiutata e il caricamento non riesce. Il percorso viene dato a claude: con una didascalia, il messaggio è la didascalia seguita dal percorso (o dai percorsi); senza didascalia, solo `check this: <path>` (o `check these: <paths>` per un album), così claude viene indirizzato al file senza dirgli cosa farne. Una didascalia che comincia con `/<label>` sceglie la sessione, proprio come `/<label> <prompt>` per il testo, e l'etichetta viene tolta dal messaggio: `/api` da solo invia `check this: <path>` ad `api`, e `/api compare these` invia lì `compare these <paths>`. Solo un `/<label>` iniziale sceglie la destinazione; una barra più avanti nella didascalia è solo testo. Un'etichetta che nessuna sessione ha viene rifiutata con `⚠️ Qui non c'è nessuna sessione chiamata …` e non viene inviato niente. Il limite di download di 20 MB dei bot di Telegram viene controllato prima: un file troppo grande riceve un rifiuto chiaro prima di qualsiasi download. Un download che incontra un errore di rete temporaneo viene tentato fino a tre volte in tutto (attesa di 1 s, poi 2 s) prima che tu veda l'errore, e l'errore nomina il file. Un album (più foto o documenti inviati come un solo messaggio) viene consegnato a claude come un solo messaggio (la didascalia, poi tutti i percorsi dei file in fila) quando arriva il suo ultimo elemento; se un elemento non si scarica, il resto viene inviato comunque, con una nota che nomina l'elemento mancante.

### <a id="files-claude-sends"></a>File che manda Claude

Claude può mandarti file dalla sua sessione: chiediglielo con parole tue («mandami lo screenshot», «mandami l'immagine in ~/proj/chart.png»), oppure ne manda uno quando pensa che ti serva. Usa lo strumento SendUserFile di Claude Code (Claude Code 2.1.301 o successivo), che riceve ogni sessione avviata da aipager: aipager la avvia con `CLAUDE_CODE_ATTACHMENTS_BY_PATH=1` e dice a Claude che lo strumento c'è. Una sessione avviata prima che il tuo aipager avesse questa funzione ha lo strumento solo dopo un riavvio: `/restart`, un cambio di modalità che la riavvia, o Riprendi.

- I file arrivano dopo la risposta dell'attività, come risposta a essa (al tuo messaggio quando l'attività non ha risposta), senza suono di notifica e solo nella chat della sessione. Mentre l'attività è in corso, la scheda di lavoro mostra `📎 Invio di 2 file`.
- Ogni file parte in base al contenuto, non al nome: un'immagine come foto quando rientra nei limiti di Telegram per le foto (al massimo 10 MB, larghezza più altezza al massimo 10.000, il lato lungo al massimo 20 volte quello corto), altrimenti come file; una GIF come animazione; un MP4 come video; un MP3 o M4A come audio; tutto il resto come file. Quando Claude chiede un semplice allegato, tutto parte come file.
- Da 2 a 10 foto e video partono in un solo album; i file vanno con i file e l'audio con l'audio; un'animazione parte da sola. Se sono di più si dividono in modo uniforme (11 partono come 6 e 5), e nessun album supera i 50 MB.
- La didascalia è `💬 <session>`, con la didascalia di Claude nella riga seguente del primo messaggio, al massimo 1.024 caratteri.
- Ogni file viene prima controllato, per tutti, owner compreso ([sicurezza](security.md#files-claude-sends)): le impostazioni di aipager, le credenziali di Claude, le chiavi e i file che sembrano segreti non vengono mai mandati; un file deve essere un file normale, non vuoto e di al massimo 50 MB; un membro manda solo file della [cartella radice](groups.md#root-folders) della chat. Se un file di una richiesta viene rifiutato, di quella richiesta non parte niente: Claude sa perché e te lo dice, e l'attività continua.
- aipager manda una copia privata fatta quando Claude Code ha letto il file, tenuta in `~/.local/state/aipager/outbox/` e cancellata appena mandata, o dopo un giorno.
- aipager non mette limiti di numero suoi: gli invii seguono il ritmo dei limiti di Telegram, e un album conta uno per file. Mentre la chat è silenziata, i file aspettano e arrivano dopo la risposta.
- Un file che non si può mandare riceve una riga nella chat, per esempio `⚠️ Non è stato possibile inviare report.pdf: Telegram l'ha rifiutato.` Un file la cui richiesta aipager non trova nella cronologia di Claude stesso non riceve nessuna riga ([sicurezza](security.md#files-claude-sends)). Un file la cui richiesta c'è ma che aipager non riesce a legare all'attività in corso riceve `⚠️ Non è stato possibile inviare report.pdf: aipager non è riuscito a confermare che Claude l'abbia inviato in questa attività.` Arrivano file anche da un messaggio che mandi mentre Claude lavora e che prende subito dopo, e da un'attività che un hook Stop fa proseguire (`/goal`). Ogni file mandato, rifiutato o non riuscito ha la sua voce nel [log di audit](security.md#audit-log).

### <a id="voice"></a>Vocali

I vocali passano da `faster-whisper` (il pacchetto in più `aipager[voice]`). L'audio viene trascritto in locale, ripetuto come `🎙️ Ho sentito: …` e scritto come se l'avessi scritto tu, anche come nome per `/new` o per una rinomina in attesa. Un vocale oltre il limite di download di 20 MB dei bot di Telegram viene rifiutato. Vedi [hook → UserPromptSubmit](hooks.md#userpromptsubmit) per cosa succede dopo.

---

## <a id="command-line-every-command"></a>Riga di comando: tutti i comandi

Esegui `aipager <command> --help` per lo stesso elenco sul tuo computer.

| Comando | Cosa fa |
|---|---|
| `aipager config` | Procedura guidata interattiva: token del bot, chat, membri e ruoli, hook di Claude Code. Rieseguila per cambiare qualsiasi cosa (vedi [gruppi](groups.md#setup)). |
| `aipager setup` | La stessa prima configurazione senza domande, per script e agenti di programmazione (vedi [sotto](#command-line-aipager-setup-for-coding-agents)). |
| `aipager start` | Fa girare aipager in primo piano. Si rifiuta di partire quando c'è già un altro aipager in esecuzione. |
| `aipager service install [--yes]` | Installa aipager come servizio in background (servizio utente di systemd su Linux, agente launchd su macOS) e lo avvia. `--yes` salta la domanda quando il file del servizio installato è diverso. |
| `aipager service start\|stop\|status\|logs\|uninstall` | Avvia, ferma o controlla il servizio, ne segue il log o lo toglie. |
| `aipager status [--json]` | aipager e tutte le sessioni a colpo d'occhio: stato, modello, contesto, costo e lo stato dei limiti di frequenza di Telegram. |
| `aipager logs [-f] [-n LINES]` | Il log di aipager (journald su Linux, log di launchd su macOS): le ultime 100 righe, o `-n` righe; `-f` lo segue. |
| `aipager doctor [--fix] [--safety-check] [--json]` | Controlli di salute (vedi [risoluzione dei problemi](troubleshooting.md#aipager-doctor-check-list)). `--fix` propone di sistemare la credenziale di Claude e un'installazione di `claude` ambigua, chiedendo prima; `--safety-check` stampa le regole di sicurezza in vigore; `--json` è per gli script (vedi [sotto](#aipager-doctor---json)). |
| `aipager session <name> [claude args]` | Avvia una sessione di Claude Code chiamata `<name>` sotto dtach nella cartella attuale, o si collega se è già in esecuzione. Gli argomenti dopo il nome (o dopo `--`) vanno così come sono a `claude`, per esempio `--resume`. Con `--resume <id>` (o `--resume=<id>`, `-r <id>`) riprende la conversazione a cui quell'ID è passato in Claude Code, e prima ferma un job in background di Claude Code che la tiene, se quel job non sta facendo nulla; uno che lavora non viene toccato: stampa `claude stop <job>` e `claude attach <job>` ed esce con il codice 1. `--continue` (`-c`) non indica nessuna conversazione, quindi non c'è nulla da seguire: va a `claude` così com'è. Serve un aipager in esecuzione. |
| `aipager session ls [-a] [--json]` | Elenca le sessioni; `-a` comprende quelle terminate. Anche `aipager session list`. |
| `aipager session kill <name> [-y]` | Termina una sessione, dopo una domanda a meno che non ci sia `-y`. |
| `aipager resume [name]` | Riporta una sessione terminata con la sua conversazione; senza nome mostra un selettore. Segue una conversazione passata a un'altra e gestisce un job in background che la tiene, come fa `aipager session --resume`. |
| `aipager miniapp enable [--port PORT] [--url URL]` | Attiva la Mini App, su `127.0.0.1:PORT` (predefinita 8765), dietro il tunnel gestito o un tuo URL `https://`. Vale dal prossimo riavvio di aipager. |
| `aipager miniapp disable\|status` | La disattiva (al prossimo riavvio), o ne mostra le impostazioni e l'indirizzo. |
| `aipager policy validate` | Controlla `policy.yaml` (e `policy.d`); esce con un codice diverso da 0 se ci sono problemi. |
| `aipager update` | Aggiorna aipager tramite l'installatore che lo possiede (uv, pipx, Homebrew, pip). Non riavvia mai aipager; riavvialo tu, oppure usa `/update` in Telegram, che lo fa (vedi [Aggiornamenti](#update)). |
| `aipager report [--note TEXT]` | Mostra una segnalazione di problemi e la invia se dici di sì (vedi [sotto](#command-line-aipager-report)). |
| `aipager uninstall [-y]` | Ferma e toglie aipager e la sua configurazione, tenendo i dati (vedi [sotto](#command-line-aipager-uninstall)). |
| `aipager version`, `aipager --version` | Stampa la versione. |
| `aipager help [command]` | Aiuto per aipager o per un comando. |

## <a id="command-line-aipager-setup-for-coding-agents"></a>Riga di comando: `aipager setup` (per agenti di programmazione)

Le sezioni qui sopra descrivono il bot di Telegram. Questa descrive un comando che esegui in un terminale, o che un tuo script o agente di programmazione esegue per te: `aipager setup` configura aipager senza domande e dà lo stesso risultato del primo avvio di `aipager config`.

```sh
# 1. trova l'ID di Telegram della persona (apre il bot e tocca Start)
aipager setup detect-chat --token-file ~/bot-token.txt --json
# 2. quando conferma che è lei, configura aipager
aipager setup --token-file ~/bot-token.txt --chat-id 123456789 --service --json
# 3. controlla l'installazione
aipager doctor --json
```

Cosa fa, in quest'ordine: legge il token del bot, lo controlla con Telegram (getMe), controlla tutto su questo computer senza scrivere niente (configurazione esistente, dipendenze, `~/.claude/settings.json` (o `$CLAUDE_CONFIG_DIR/settings.json`), se c'è un aipager in esecuzione), invia alla persona un messaggio di prova ("aipager is set up for you. ...") e solo dopo scrive. I file scritti, gli stessi della procedura guidata:

- `~/.config/aipager/aipager.yaml` (permessi 0600): `bot_token` e una chat privata configurata il cui unico membro è la persona (`label: owner`, ruolo owner o admin).
- `~/.claude/settings.json` (o `$CLAUDE_CONFIG_DIR/settings.json`, il file che leggono le sessioni del daemon: l'ambiente di questa shell più `daemon.env`): gli hook di aipager e la statusLine (il file esistente viene salvato come `settings.json.bak.<time>` quando cambia). `settings_json.path` nell'output JSON lo indica.
- Il log di audit riceve una voce `grant-owner` quando qualcuno diventa proprietario.
- Con `--service`, il servizio in background (come `aipager service install --yes`).
- Solo su una vecchia installazione con `config.env` (ancora senza `aipager.yaml`), la conversione che fa la procedura guidata: una copia `config.env.bak.<time>` (e `team.yaml.bak.<time>` quando c'è un `team.yaml`), e un `~/.config/aipager/policy.yaml` di esempio quando non ce n'è uno. `config.env` stesso resta com'è.

Se un controllo fallisce, non viene inviato niente e non viene scritto niente. Quando un aipager è già in esecuzione, un cambio di chat o di ruolo viene ricaricato senza riavvio; un nuovo token del bot richiede un riavvio (`aipager service stop`, poi `aipager service start`), che setup non fa mai da solo, e `--service` viene ignorato. Se setup non riesce a capire se c'è un aipager in esecuzione, agisce come se ci fosse (avviso `daemon_unknown`): allora non installa mai il servizio, quindi non può mai riavviare un aipager in esecuzione, e dopo qualsiasi modifica non invia l'ordine di ricarica ma riferisce `restart_needed: true`.

### <a id="aipager-setup-flags"></a>Opzioni di `aipager setup`

| Opzione | Significato |
|---|---|
| `--token-file PATH` | Legge il token del bot da questo file (al massimo 4096 byte; va bene anche altro testo intorno al token). Un file che altri utenti possono leggere funziona ma dà un avviso `token_file_shared`: fai `chmod 600`, o cancellalo una volta che setup è riuscito. |
| `--token-stdin` | Legge il token del bot da stdin (una pipe o un file rediretto, mai un terminale). La pipe deve chiudersi: setup aspetta fino a 30 secondi la fine di stdin, poi esce con codice 2 e `token_stdin_timeout`. `printf '%s\n' "$TOKEN" \| aipager setup --token-stdin ...` e `< token.txt` la chiudono entrambi. |
| `--chat-id N` | Obbligatorio. L'ID numerico dell'utente di Telegram della persona, che è anche l'ID della sua chat privata. I gruppi si aggiungono con `aipager config`. |
| `--role owner\|admin` | Il ruolo della persona (predefinito `owner`). |
| `--service` | Installa e avvia anche il servizio in background (ignorato quando un aipager è in esecuzione). |
| `--force` | Permette di sostituire il token del bot, la chat privata del proprietario o il ruolo di un'installazione esistente. I gruppi configurati e le altre chat private restano come sono. |
| `--dry-run` | Esegue tutti i controlli, non invia niente, non scrive niente e riferisce cosa cambierebbe. |
| `--json` | Stampa esattamente un oggetto JSON su stdout; tutto il resto va su stderr. |

Dai esattamente uno tra `--token-file` e `--token-stdin`. Il token non viene mai accettato come valore di un'opzione: `--token` e `--bot-token` vengono rifiutati (`token_on_command_line`) e il valore non viene mostrato. Le opzioni abbreviate (`--token-f`, `--dry`) non vengono accettate. setup non fa mai domande, non ha mai bisogno di un terminale e non legge mai il token da una variabile d'ambiente.

Rieseguirlo con lo stesso token, la stessa chat e lo stesso ruolo non cambia niente e lo dice (`unchanged`, nessun messaggio di prova). Un token, una chat o un ruolo diversi su un'installazione esistente escono con codice 6 finché non aggiungi `--force`. Una vecchia installazione con `config.env` viene prima convertita, come fa la procedura guidata (che le dà il ruolo `admin`, quindi `--role owner` su quell'installazione ha bisogno di `--force`).

### <a id="aipager-setup-detect-chat"></a>`aipager setup detect-chat`

```sh
aipager setup detect-chat (--token-file PATH | --token-stdin) [--timeout SECONDS] [--json]
```

Aspetta il messaggio privato più recente al bot (toccare «Avvia» ne invia uno) e stampa il suo mittente: ID, nome e username. Non salva niente, quindi mostra la persona a chi stai aiutando ed esegui `aipager setup --chat-id` solo quando conferma. Contano solo i messaggi inviati al massimo 10 minuti prima dell'avvio del comando. `--timeout` va da 0 a 3600 secondi (predefinito 300; 0 vuol dire guardare una volta sola). Legge Telegram senza confermare nessun messaggio, quindi `/start` resta disponibile per la procedura guidata e per aipager.

Mentre è in esecuzione un aipager per lo stesso bot, quell'aipager riceve i messaggi del bot e non registra i messaggi privati di persone non ancora configurate. Allora detect-chat esce con codice 9 (`source: "daemon"`): ferma aipager (`aipager service stop`) e rieseguilo, oppure chiedi alla persona il suo ID numerico. Con un aipager di un altro bot, legge Telegram direttamente (`source: "telegram"`).

Limite: siccome detect-chat non conferma mai i messaggi, Telegram gli mostra solo i 100 aggiornamenti non letti più vecchi delle ultime 24 ore. Quando il bot ne ha così tanti in attesa, un nuovo `/start` non si vede: allora detect-chat scade (codice 8) con l'avviso `updates_backlog`, e toccare di nuovo «Avvia» non serve. Chiedi invece alla persona il suo ID numerico di Telegram (oppure avvia aipager una volta, che legge e svuota l'arretrato).

### <a id="exit-codes"></a>Codici di uscita

| Codice | `error` | Significato |
|---|---|---|
| 0 | | Riuscito (`installed`, `updated`, `unchanged`, `dry_run`, o `found` di detect-chat). |
| 1 | `config_malformed`, `ambiguous_install`, `settings_invalid`, `test_send_failed`, `write_failed`, `service_failed`, `internal_error` | Fallito. `ambiguous_install`: più di una chat privata potrebbe essere del proprietario; sistemalo in `aipager config`. |
| 2 | `usage`, `missing_token_source`, `token_source_conflict`, `token_file_unreadable`, `token_stdin_is_tty`, `token_stdin_timeout`, `token_malformed`, `token_on_command_line`, `bad_chat_id`, `bad_role`, `bad_timeout` | Input sbagliato o mancante; `fix` nomina l'opzione. Un'opzione sconosciuta o una parola sbagliata dopo `setup` vengono riferite dall'analizzatore degli argomenti (testo semplice, niente JSON), sempre con codice 2. |
| 3 | `deps_missing` | Mancano dtach, claude, aipager-hook o aipager-statusline; `fix` contiene i comandi da eseguire. Non viene scritto niente. |
| 4 | `token_rejected` | Telegram ha rifiutato il token. |
| 5 | `chat_not_started`, `bot_blocked` | La persona non ha toccato «Avvia» nel bot ("Open t.me/&lt;bot&gt; and press Start, then run this again."), oppure l'ha bloccato. Non viene scritto niente. |
| 6 | `existing_install` | È già configurato un altro token, un'altra chat o un altro ruolo; aggiungi `--force`. |
| 7 | `telegram_unreachable` | Errore di rete, oppure Telegram ha risposto 429 o 5xx. |
| 8 | `detect_timeout` | detect-chat non ha visto nessun messaggio privato prima della fine di `--timeout` (con l'avviso `updates_backlog` quando 100 vecchi aggiornamenti non letti nascondono quelli più nuovi). |
| 9 | `daemon_running` | detect-chat: un aipager in esecuzione riceve i messaggi del bot. |
| 10 | `updates_conflict` | detect-chat: un altro programma sta leggendo i messaggi di questo bot. |
| 130 | `interrupted` | Ctrl-C. |

Quando più controlli falliscono insieme, il codice di uscita segue quest'ordine: `config_malformed`, `ambiguous_install`, `existing_install`, `settings_invalid`, `deps_missing`. `--dry-run` esce con il codice che darebbero questi controlli in un'esecuzione vera.

### <a id="json-aipager-setup---json"></a>JSON: `aipager setup --json`

Ogni chiave c'è sempre; un valore sconosciuto è `null`.

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

- `status`: `installed`, `updated`, `unchanged`, `dry_run` o `error`.
- `changed`: cosa è cambiato (o cambierebbe, in una prova), in quest'ordine: `migrated_v1`, `bot_token`, `owner_dm`, `role`, `settings_json`, `service`.
- `test_message`: `sent`, `not_needed`, `skipped_dry_run`, `failed` o `not_attempted`.
- `settings_json.status`: `created`, `patched`, `unchanged`, `would_change` o `not_checked`.
- `daemon.reload`: `reloaded`, `refused`, `not_reloaded` o `not_needed`. `restart_needed` è true dopo un nuovo token mentre un aipager è in esecuzione, e dopo qualsiasi modifica quando aipager non ha potuto ricaricare o setup non ha capito se c'è un aipager in esecuzione.
- `service.result`: `not_requested`, `installed`, `already_installed`, `skipped_daemon_running`, `would_install`, `failed`, oppure `null` quando setup si è fermato prima di quel passo.
- `warnings`: oggetti con `code` (`token_file_shared`, `reload_refused`, `daemon_unknown`, `audit_write_failed`) e `message`. `audit_write_failed`: il permesso di proprietario è stato scritto, ma la sua voce `grant-owner` non si è potuta aggiungere al log di audit; la voce è al meglio delle possibilità, quindi il codice di uscita resta 0.
- `next_step`, dopo una modifica mentre è in esecuzione un aipager che non ha potuto ricaricare, dice di riavviarlo (`aipager service stop`, poi `aipager service start`); nomina il token del bot solo quando il token è cambiato.
- Con l'avviso `daemon_unknown`, `next_step` non dice mai che aipager è in esecuzione. Dice che setup non è riuscito a capirlo, rimanda a `aipager doctor --json` e dice di avviare aipager con `aipager service install` o `aipager start` se non è in esecuzione. Quando serve un riavvio, chiede di riavviare solo se c'è un aipager in esecuzione, altrimenti di avviarlo allo stesso modo.
- In caso di errore, `status` è `error` ed `error`, `message` e `fix` dicono cosa è andato storto e cosa fare; `next_step` ripete `fix`. Quando l'errore arriva dopo una scrittura, `changed` elenca cosa è stato scritto, e l'output di testo aggiunge una riga su stderr `Already written: aipager.yaml, ... (changed: ...)`.

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

`candidate` è `null` a meno che `status` non sia `found`. `warnings` contiene oggetti con `code` (`token_file_shared`, `updates_backlog`) e `message`. `other_candidates` conta le altre persone che hanno scritto al bot negli stessi 10 minuti: quando non è 0, assicurati di aver scelto la persona giusta. `next_step` ripete l'opzione del token che hai usato, mai il token stesso.

### <a id="aipager-doctor---json"></a>`aipager doctor --json`

I controlli di `aipager doctor` come un oggetto JSON su stdout. Come `aipager doctor` normale, legge soltanto e non invia messaggi di Telegram. Esce con 1 quando un controllo fallisce, altrimenti con 0. Non si può usare insieme a `--fix` o `--safety-check` (esce con codice 2 e un oggetto di errore).

```json
{
  "command": "doctor",
  "version": "1.0.1",
  "ok": true,
  "summary": {"ok": 14, "warn": 1, "fail": 0},
  "checks": [{"key": "token_valid", "status": "ok", "title": "Telegram bot token", "detail": ["@example_bot"], "fix": null}]
}
```

`checks` è in quest'ordine, con queste chiavi: `config_parses`, `config`, `token_valid`, `chat_reachable`, `team`, `role_shell_access`, `peer_messages`, `claude`, `claude_auth`, `dtach`, `hook_scripts`, `settings_json`, `daemon`, `service_installed`, `service_unit_path`, `miniapp`. Un controllo andato in crash tiene comunque la sua chiave con lo stato `warn`. Il testo è semplice (senza formattazione), e tutto quello che sembra un token di bot viene sostituito con `<redacted>`.

## <a id="command-line-aipager-uninstall"></a>Riga di comando: `aipager uninstall`

`aipager uninstall` ferma aipager e toglie il suo servizio, cancella la cartella di configurazione (`~/.config/aipager`: token del bot, chat e impostazioni) e l'elenco delle sessioni (`~/.claude/aipager-sessions.json`), pulisce i file di aipager in `/tmp` e toglie aipager stesso. Prima chiede (`-y` salta la domanda).

Tiene i dati di aipager, così una reinstallazione riprende da dove eri: `~/.local/share/aipager` (cartelle delle sessioni, programmi di supporto scaricati, file di lock e di segno), `~/.local/state/aipager` (il registro delle segnalazioni di problemi e lo stato delle lingue), `~/.claude/aipager-audit.jsonl` (il log delle risposte alle richieste di permesso), `~/.claude/aipager-flood-state.json` (lo storico dei limiti di Telegram), `~/.claude/aipager-pending-users.json` (le persone che hanno provato il bot senza accesso) e `~/aipager-users` (le [cartelle radice](groups.md#root-folders) dei membri e il loro lavoro). Prima di chiedere elenca quali di questi ci sono sul tuo computer, e alla fine stampa il comando `rm -rf` per cancellare anche quelli.

Toglie anche le voci di aipager da `~/.claude/settings.json` (e da quello che leggono le sessioni del daemon, quando `CLAUDE_CONFIG_DIR` ne indica un altro) di Claude Code: gli hook che eseguono `aipager-hook` e la riga di stato che esegue `aipager-statusline` (una tua riga di stato resta). Tutto il resto di quel file resta com'è, e il file com'era prima viene tenuto accanto come `settings.json.bak.<time>`. Se il file è un link a un altro file, non si legge, non è un JSON valido o non ha la forma che legge Claude Code, viene lasciato com'è e uninstall dice quali voci togliere a mano. Un tuo hook che nomina `aipager-hook` o `aipager-statusline` (per esempio un involucro `aipager-hook-capped.sh`) resta anche lui, e uninstall lo nomina. Le sessioni di Claude Code aperte continuano a eseguire gli hook che hanno caricato finché non le riavvii. `aipager config` rimette le voci dopo una reinstallazione. Il tuo bot di Telegram e le copie di sicurezza `settings.json.bak.*` più vecchie non vengono toccati, e nemmeno `skipDangerousModePermissionPrompt` (l'impostazione che salta l'avviso di Claude Code prima di girare con i controlli dei permessi disattivati), perché aipager la imposta ma potresti averla impostata anche tu.

## <a id="command-line-aipager-report"></a>Riga di comando: `aipager report`

`aipager report` costruisce una segnalazione di problemi dello stesso tipo del pulsante «Segnala un problema» del bot, da quello che aipager ha registrato su questo computer (con meno informazioni sull'aipager in esecuzione: modalità, chat, funzioni, tempo di attività, sessioni e dettagli di Claude Code restano vuoti o sconosciuti), la stampa tutta e chiede `Send this report? [y/N]`. Solo `y` o `yes` la inviano; qualsiasi altra cosa, o nessun terminale (uno script, una pipe, la shell di una sessione di Claude Code), non invia niente. `--note "text"` aggiunge una nota di al massimo 500 caratteri, che compare nella segnalazione stampata. Esce con 0 quando la segnalazione è stata inviata, non inviata per tua scelta o solo stampata, e con 1 quando un invio che hai confermato non è partito. Vedi [Segnalazioni di problemi](problem-reports.md) per cosa contiene una segnalazione e dove va.

## <a id="settings-in-the-environment"></a>Impostazioni dall'ambiente

Alcune impostazioni si leggono dall'ambiente di aipager invece che da `aipager.yaml`. Per il servizio systemd, aggiungile con `systemctl --user edit aipager` sotto `[Service]` (`Environment=STALE_BUSY_TIMEOUT=900`), poi `aipager service stop` e `aipager service start`; per il servizio launchd su macOS, aggiungile alle `EnvironmentVariables` del file plist (`~/Library/LaunchAgents/com.aipager.daemon.plist`; `aipager service install` riscrive quel file, quindi aggiungile di nuovo dopo averlo eseguito); per un aipager avviato da te, impostale prima di `aipager start`. Per l'uso normale non ne serve nessuna.

| Impostazione | Predefinito | Cosa cambia |
|---|---|---|
| `AIPAGER_WHISPER_MODEL` | `base` | Il modello di riconoscimento vocale per i vocali (`tiny`, `base`, `small`, `medium`, ...): più grande è più preciso e più lento. |
| `MINIAPP_PORT` | `8765` | La porta della Mini App su `127.0.0.1` (`aipager miniapp enable --port` la salva). |
| `MINIAPP_ENABLED` | da `aipager.yaml` | `0` disattiva la Mini App e `1` la attiva, qualunque cosa dica `aipager.yaml` (`aipager miniapp enable\|disable` salva la scelta). |
| `MINIAPP_PUBLIC_URL` | nessuno | Serve la Mini App sul tuo URL `https://` invece che dal tunnel gestito ([sicurezza](security.md#mini-app-tunnel)). |
| `KEEP_FINISHED_CARD` | `1` | `0` imposta l'aspetto «Solo la risposta (senza scheda)» per le chat che non hanno mai scelto in `/settings`. |
| `STALE_BUSY_TIMEOUT` | `7200` (2 ore) | Quanti secondi una sessione al lavoro può restare in silenzio prima che la chat riceva una nota «è ancora al lavoro (nessuna novità da 120 min)». |
| `PROMPT_HOOK_GRACE_SECONDS` | `8` | Secondi dopo i quali un messaggio che Claude Code non ha mai preso viene segnato «Claude Code non ha ricevuto il messaggio» ([messaggi liberi](#text)). |
| `FINISH_CARD_GRACE_SECONDS` | `0.8` | Quanto prima della risposta arriva la scheda finita negli aspetti con la scheda; `0` invia entrambe insieme. |
| `GONE_SESSION_MAX_AGE_DAYS` | `14` | Per quanti giorni una sessione terminata resta in `/resume`; `0` le tiene per sempre. |
| `STREAM_EDIT_INTERVAL`, `BUSY_EDIT_INTERVAL` | `1.2`, `3.0` | Il ritmo di aggiornamento della scheda di attività mentre arriva testo e quando è tranquilla, in secondi; il limite per chat di Telegram vince comunque ([risoluzione dei problemi](troubleshooting.md#busy-cards-got-slower)). |
| `TYPING_INDICATOR_INTERVAL` | `4.5` | Secondi tra gli indicatori «sta scrivendo…»; `0` li disattiva. |
| `TELEGRAM_MAX_RETRY_AFTER` | `90` | L'attesa di Telegram, in secondi, oltre la quale aipager tratta un limite di frequenza come un blocco (non abbassarla; vedi [risoluzione dei problemi](troubleshooting.md#the-bot-went-quiet-flood-control)). |
| `CLAUDE_RICH_SUMMARIES` | `1` | `0` disattiva la formattazione ricca per le risposte con blocchi di codice (una riserva tenuta dalle vecchie versioni). |
| `AIPAGER_INTERACTIVE_TIMEOUT` | `300` | Secondi in cui una richiesta di permesso o una domanda può aspettare senza notizie da Claude Code prima che aipager controlli la conversazione salvata; solo se questa mostra che la richiesta è finita la sessione conta di nuovo come al lavoro ([risoluzione dei problemi](troubleshooting.md#a-permission-prompt-with-no-news-for-5-minutes)). |
| `AIPAGER_SUBAGENT_SILENCE` | `1800` | Secondi di silenzio dopo i quali un subagent di cui non si è vista la fine viene tolto ([hook](hooks.md#subagentstart--subagentstop)). |
| `AIPAGER_BG_SHELL_MAX_TRACK` | `7200` | Secondi dopo i quali un comando in background di cui non si è vista la fine smette di essere contato. |
| `AIPAGER_REPORT_PROMPTS` | attivo | `0` disattiva le proposte automatiche di segnalazioni di problemi ([segnalazioni di problemi](problem-reports.md#when-aipager-offers-a-report)). |
| `AIPAGER_CLAUDE_BIN` | nessuno | Il programma `claude` da eseguire, quando ne sono installati diversi (`claude_path` in `aipager.yaml` vince). |
| `AIPAGER_SOCKET_PATH` | `$XDG_RUNTIME_DIR/aipager.sock` | Il socket di controllo di aipager. Le sessioni avviate da aipager lo passano ai loro hook. |
| `AIPAGER_DEBUG` | disattivo | `1` nell'ambiente di una sessione fa stampare ad `aipager-hook` e `aipager-statusline` le informazioni di diagnostica su stderr. |
| `AIPAGER_INSTANCE_DIR`, `AIPAGER_TELEGRAM_API_BASE` | nessuno | Un secondo aipager, isolato, per i test ([architettura](architecture.md#isolated-instance-for-testing)). |

## <a id="see-also"></a>Vedi anche

- [Architettura](architecture.md): dove sta il bot.
- [Eventi hook](hooks.md): cosa sente aipager da claude.
- [Risoluzione dei problemi](troubleshooting.md): quando i comandi non fanno quello che ti aspetti.
