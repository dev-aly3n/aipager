# Installare con un agente

<!-- i18n-source: docs/install-with-an-agent.md @ d63d62ef -->

> Questa pagina è tradotta dalla pagina in inglese; se le due versioni non coincidono, vale quella in inglese. ([English](../../install-with-an-agent.md))

Questa pagina è per un agente di programmazione AI (Claude Code, o un qualsiasi agente con una shell) a cui è stato chiesto di installare e configurare aipager per una persona. Seguila passo per passo. Nessun comando qui fa domande e nessuno ha bisogno di un terminale interattivo.

aipager permette alla persona di controllare le sessioni di Claude Code da Telegram. Alla fine la persona avrà un bot di Telegram che parla solo con lei, e aipager girerà in background su questo computer.

## <a id="1-what-only-the-person-can-do"></a>1. Cosa può fare solo la persona

Chiedi alla persona di fare queste cose. Tu non puoi farle al posto suo.

1. **Creare il bot**. In Telegram, aprire [@BotFather](https://t.me/BotFather), inviare `/newbot` e scegliere un nome e un username. BotFather risponde con il token del bot (simile a `123456789:AA...`).
2. **Toccare «Avvia» nel bot**. Aprire il nuovo bot (il link di BotFather ha la forma `t.me/<bot username>`) e toccare «Avvia» (Start). Un bot non può scrivere a chi non l'ha fatto.
3. **Fare l'accesso a Claude Code su questo computer**. Se la persona usa già Claude Code qui, non c'è niente da fare. Su un server senza schermo, la persona esegue `claude` una volta nel suo terminale e fa l'accesso (mostra un link da aprire su un altro dispositivo). Se preferisce un token di lunga durata, esegue `claude setup-token`, che si limita a stampare il token: è la persona stessa a salvarlo per le sessioni di aipager nel suo terminale (incolla il token, premi Invio e poi Ctrl-D):

   ```sh
   mkdir -p ~/.config/aipager
   (umask 077 && { printf 'CLAUDE_CODE_OAUTH_TOKEN='; cat; } >> ~/.config/aipager/daemon.env)
   ```

   aipager passa questo token a ogni sessione che avvia (vedi [Sicurezza](security.md#the-claude-credential-what-actually-protects-it)). Tu non lo leggi mai.

## <a id="2-how-the-person-gives-you-the-token"></a>2. Come la persona ti passa il token

Chi ha il token del bot controlla il bot. Spiega alla persona queste due strade e lascia scegliere a lei.

- **La più sicura: un file che tu non leggi mai**. La persona esegue nel suo terminale:

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token)
  chmod 600 ~/.config/aipager/bot-token
  ```

  Poi incolla il token, preme Invio e poi Ctrl-D. Solo la persona può leggere il file (permessi 600). Dopo che la configurazione è riuscita può cancellarlo (aipager ne tiene una copia nella sua configurazione). La persona ti dice il percorso (`~/.config/aipager/bot-token`). Il token non entra mai nella tua conversazione e tu passi il percorso a `--token-file`.
- **La più veloce: incollarlo nella chat**. Funziona, ma spiega cosa comporta: il token resta nella cronologia della tua conversazione e viene inviato al fornitore del tuo modello. Se quella cronologia potrebbe essere condivisa, la persona dovrebbe poi inviare `/revoke` a @BotFather e impostare il nuovo token con `aipager setup --force` (passo 3.2, con `--force`). Se la persona sceglie questa strada, salva il token nello stesso file senza stamparlo: meglio con il tuo strumento di scrittura dei file, seguito da `chmod 600 ~/.config/aipager/bot-token`. Se hai solo una shell, passalo su stdin con un here-document, così il token non diventa un argomento del comando che scrive il file:

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token) <<'EOF'
  <the token>
  EOF
  chmod 600 ~/.config/aipager/bot-token
  ```

  Poi usa `--token-file` come sotto.

L'ID utente di Telegram della persona (che è anche l'ID della chat, un numero come `123456789`) non è un segreto. Se la persona non lo conosce, puoi trovarlo tu (passo 3.3).

## <a id="3-your-steps"></a>3. I tuoi passi

### <a id="31-install-aipager"></a>3.1 Installare aipager

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

Usa quello che c'è già tra uv e pipx (e Homebrew su macOS) e, se non c'è nessuno dei due, installa prima uv. Puoi anche eseguire tu `uv tool install aipager` o `pipx install aipager`. Se poi `aipager` non si trova, aggiungi `~/.local/bin` al `PATH` della shell (`export PATH="$HOME/.local/bin:$PATH"`).

Se aipager era già installato, questi comandi non lo aggiornano. Controlla che `aipager setup --help` funzioni; se non funziona, l'aipager installato è troppo vecchio: esegui `aipager update` e controlla di nuovo. Se `aipager update` dice che aipager va riavviato, dillo alla persona; non riavviarlo tu (vedi la sezione 4).

Non eseguire `aipager config`: è la procedura guidata interattiva pensata per le persone.

### <a id="32-set-aipager-up"></a>3.2 Configurare aipager

```sh
aipager setup --token-file ~/.config/aipager/bot-token --chat-id 123456789 --service --json
```

Usa l'ID vero della persona e il percorso del file del token. Al posto di `--token-file` puoi passare il token in pipe con `--token-stdin` (per esempio `--token-stdin < path`); la pipe deve chiudersi, setup aspetta al massimo 30 secondi.

setup controlla il token con Telegram, controlla questo computer, invia alla persona un messaggio di prova ("aipager is set up for you. ...") e solo dopo scrive la configurazione, aggiunge gli hook di Claude Code a `~/.claude/settings.json` (o `$CLAUDE_CONFIG_DIR/settings.json`) e, con `--service`, installa e avvia il servizio in background. Se un controllo fallisce, non scrive niente.

Aggiungi `--dry-run` per vedere prima cosa cambierebbe. `--role admin` rende la persona admin invece di proprietaria (il proprietario, predefinito, è giusto per chi configura il proprio computer).

### <a id="33-if-the-person-does-not-know-their-telegram-id"></a>3.3 Se la persona non conosce il suo ID di Telegram

```sh
aipager setup detect-chat --token-file ~/.config/aipager/bot-token --json
```

Mentre il comando aspetta (fino a 5 minuti; cambialo con `--timeout SECONDS`), chiedi alla persona di toccare «Avvia» nel bot (o di mandargli un messaggio qualsiasi). Stampa l'ultimo mittente in `candidate` (`id`, `first_name`, `last_name`, `username`) e non salva niente. Mostra nome e username alla persona e chiedi: «Sei tu?». Solo quando la persona conferma, esegui il passo 3.2 con `candidate.id` come `--chat-id`. Se `other_candidates` non è 0, anche altre persone hanno scritto al bot: assicurati bene di chi è.

### <a id="34-read-the-result"></a>3.4 Leggere il risultato

Con `--json`, setup stampa su stdout uno e un solo oggetto JSON. Leggi `exit_code` (lo stesso codice di uscita del processo), `status`, `error`, `message`, `fix` e `next_step`. Se stdout non è JSON (opzione sconosciuta, parola sbagliata dopo `setup`, oppure un aipager così vecchio da non avere `setup`), il codice di uscita è 2 e il motivo è su stderr come testo semplice: leggilo e, se aipager è vecchio, vedi il passo 3.1.

| Uscita | `error` | Cosa fai |
|---|---|---|
| 0 | | `installed`, `updated` o `unchanged`: fatto, vai al passo 3.5. `dry_run`: non ha scritto niente; riesegui lo stesso comando senza `--dry-run`. detect-chat `found`: fai confermare la persona come al passo 3.3, poi esegui il passo 3.2. |
| 1 | `config_malformed`, `ambiguous_install`, `settings_invalid`, `test_send_failed`, `write_failed`, `service_failed`, `internal_error` | Mostra `message` e `fix` alla persona. `ambiguous_install`: lo sistema la persona in `aipager config`. Non modificare file a mano. Un `settings_invalid` che chiede un `CLAUDE_CONFIG_DIR` assoluto: il `fix` indica il file da cambiare (`daemon.env` o un file di impostazioni di Claude Code); chiedi la cartella alla persona, cambia quell'unico valore solo se te lo dice, poi esegui di nuovo setup. |
| 2 | `usage`, `token_malformed`, `token_stdin_timeout`, `bad_chat_id`, ... | Il tuo input è sbagliato; `fix` dice quale opzione. `token_malformed`: il file non contiene un token di bot, chiedi alla persona di controllarlo. |
| 3 | `deps_missing` | Segui `fix` (ogni elemento mancante ha anche il suo `fix` in `deps.items`), poi riesegui setup. |
| 4 | `token_rejected` | Telegram ha rifiutato il token. Chiedi di nuovo il token alla persona (o uno nuovo a @BotFather). |
| 5 | `chat_not_started`, `bot_blocked` | Chiedi alla persona di aprire il bot e toccare «Avvia» (o di sbloccarlo), poi riesegui setup. |
| 6 | `existing_install` | aipager è già configurato con un altro token, un'altra chat o un altro ruolo. Di' alla persona cosa cambia e chiedi il suo consenso prima di aggiungere `--force`. |
| 7 | `telegram_unreachable` | Problema di rete, o Telegram è occupato. Aspetta un minuto e riprova. |
| 8 | `detect_timeout` | detect-chat non ha visto nessun messaggio. Chiedi alla persona di toccare di nuovo «Avvia» e riesegui. Se c'è l'avviso `updates_backlog`, toccare di nuovo «Avvia» non serve: chiedi alla persona il suo ID numerico. |
| 9 | `daemon_running` | detect-chat: un aipager per questo bot è già in esecuzione e riceve lui i messaggi. Chiedi alla persona il suo ID numerico (oppure, dopo averglielo chiesto, fermalo con `aipager service stop`). |
| 10 | `updates_conflict` | detect-chat: un altro programma sta leggendo i messaggi di questo bot. Chiedi alla persona il suo ID numerico. |
| 130 | `interrupted` | Interrotto con Ctrl-C. |

Se `daemon.restart_needed` è true, aipager va riavviato per usare la modifica, oppure setup non è riuscito a capire se aipager è in esecuzione (avviso `daemon_unknown`). Segui `next_step`, che dice quale dei due casi è (se propone `aipager start`, che resta in primo piano, usa invece `aipager service install`). Dillo alla persona e riavvia solo se è d'accordo (`aipager service stop`, poi `aipager service start`), e mai dentro una sessione che la persona sta usando tramite aipager (vedi la sezione 4). L'elenco completo di errori, avvisi e chiavi JSON è nel [riferimento del comando setup](commands.md#command-line-aipager-setup-for-coding-agents).

### <a id="35-check-the-install"></a>3.5 Controllare l'installazione

```sh
aipager doctor --json
```

Legge soltanto e non invia niente. Il codice di uscita 0 vuol dire che nessun controllo è fallito. Per ogni controllo con `status` `fail` (o `warn`), mostra alla persona il suo `title`, `detail` e `fix`.

### <a id="36-report-back-to-the-person"></a>3.6 Riferire alla persona

Racconta alla persona, in poche righe:

- il bot (`@` + `bot_username`) e se il messaggio di prova è stato inviato (`test_message`);
- cosa è stato configurato (`changed`, e `service.result`);
- come avviare una sessione di Claude: inviare `/new` al bot in Telegram, oppure eseguire `aipager session <name>` in un terminale;
- qualsiasi problema segnalato da `doctor`, e `next_step` se chiede qualcosa.

## <a id="4-what-you-must-not-do"></a>4. Cosa non devi mai fare

- Non stampare il token, non mostrarlo con `echo` o `cat` e non metterlo mai sulla riga di comando (`--token` viene comunque rifiutato). Passalo solo con un file o con stdin.
- Non modificare a mano `~/.config/aipager/` (tranne il file del token del passo 2) o `~/.claude/settings.json`. Li scrivono `aipager setup` e `aipager config`. L'unica eccezione è un gruppo su un aipager più vecchio di questo, dove `aipager setup group --help` non funziona: vedi [5.5](#55-older-installs).
- Non aggiungere `--force` senza chiedere alla persona: sostituisce il token, la chat o il ruolo che ha già. Con `setup group` cambia o toglie ruoli, oppure dà il ruolo `owner`.
- Attraverso aipager, solo le attività del proprietario possono eseguire `aipager setup` (in qualsiasi forma), `aipager config` o `aipager uninstall`: le regole di sicurezza di aipager li bloccano per ogni altro ruolo. Se la persona ti parla attraverso aipager e non è il proprietario, chiedile di eseguire il comando lei stessa in un terminale.
- Non riavviare né terminare la sessione di Claude Code in cui ti trovi. Alla prima installazione la tua sessione non è sotto aipager e non serve che lo sia: le nuove sessioni nascono da `/new` in Telegram o da `aipager session <name>`. Se aipager è già configurato e la persona ti sta parlando attraverso di lui (per esempio per eseguire `setup --force` con un nuovo token), non fermare e non riavviare aipager tu: la tua attività e la sua risposta verrebbero tagliate. Quando hai finito, chiedi alla persona di riavviarlo lei.

## <a id="5-groups"></a>5. Gruppi

`aipager setup` configura la chat privata della persona con il bot. `aipager setup group` aggiunge un gruppo di Telegram, i suoi membri e i loro ruoli a un'installazione già configurata, senza domande (`aipager config` fa lo stesso con le domande). Legge il token del bot dall'installazione, non riavvia mai aipager e non invia niente al gruppo. Vedi [Uso in team e gruppi](groups.md) e [la guida del comando](commands.md#aipager-setup-group).

### <a id="51-what-only-the-person-can-do"></a>5.1 Quello che può fare solo la persona

Questi passi non puoi farli tu; chiedi alla persona di farli in Telegram:

1. Aggiungere il bot al gruppo.
2. Rendere il bot amministratore del gruppo con il permesso di fissare i messaggi (aipager ci fissa una barra di stato; senza quel permesso il gruppo funziona, ma senza barra di stato). Può farlo solo un amministratore del gruppo.
3. Inviare `/start` nel gruppo, così puoi trovarne l'ID (5.2).
4. Darti, per ogni membro, l'ID numerico dell'utente di Telegram, un nome breve (come aipager lo mostra, cioè `@label`) e un ruolo: `owner`, `admin`, `user`, `read_only` o un ruolo suo definito in `policy.yaml`. Il ruolo `owner` ha il pieno controllo di questo computer: spetta alla persona che lo gestisce e a nessun altro. Se non conosce un ID, `aipager config` può trovarlo mentre quella persona scrive nel gruppo.

### <a id="52-find-the-group"></a>5.2 Trovare il gruppo

```sh
aipager setup detect-group --json
```

Aspetta (fino a 300 secondi, `--timeout SECONDS`) un messaggio in un gruppo non ancora configurato, inviato al massimo 10 minuti prima che lo eseguissi, e stampa il gruppo (`candidate.id`, `candidate.title`). Non salva niente. Mostra il titolo alla persona e usa l'ID solo quando conferma che è il gruppo giusto. L'uscita 8 vuol dire che non è arrivato niente: chiedile di inviare di nuovo `/start` nel gruppo.

### <a id="53-set-it-up"></a>5.3 Configurarlo

Eseguilo prima con `--dry-run`, poi lo stesso comando senza:

```sh
aipager setup group --chat-id -1001234567890 \
  --member 111111111:alice:owner --member 222222222:bob:user \
  --label team --dry-run --json
```

- `--member ID:LABEL:ROLE` una volta per persona. Per aggiungere qualcuno più tardi, eseguilo di nuovo solo con il nuovo `--member`: gli altri restano.
- `--remove-member ID` toglie una persona.
- `--label NAME` dà un nome al gruppo, `--root PATH` sceglie la cartella in cui lavorano i suoi membri `user` e `read_only` (predefinita `~/aipager-users/group-<label>`), e `--block-edits` blocca Write ed Edit per i suoi membri `user`.
- Eseguire di nuovo lo stesso comando non cambia niente (`unchanged`).
- L'uscita 6 (`needs_force`) vuol dire che la modifica richiede `--force`: `force_reasons` dice perché (un cambio di ruolo, un proprietario aggiunto, declassato o tolto, una cartella radice rischiosa). Mostralo alla persona e aggiungi `--force` solo se è d'accordo.

### <a id="54-read-the-result"></a>5.4 Leggere il risultato

| Uscita | Cosa fare |
|---|---|
| 0 | Fatto (`created`, `updated`, `unchanged` o `dry_run`). Di' alla persona il `label` del gruppo, i suoi `members` e cosa dice `daemon.outcome`. |
| 2 | Un'opzione è sbagliata: `error`, `message` e `fix` la indicano. Correggila ed eseguilo di nuovo. |
| 6 | `needs_force`: chiedi alla persona di ogni voce di `force_reasons`. |
| 11 | `not_set_up`: prima configura aipager ([3](#3-your-steps)). |
| 1 | Tutto il resto (`config_malformed`, `policy_malformed`, `last_member`, `config_changed`...): mostra `message` e `fix`. Dopo `config_changed`, eseguilo di nuovo. |

`daemon.outcome` dice cosa ha fatto l'aipager in esecuzione: `reloaded_live` (il gruppo funziona già), `no_daemon` (funzionerà quando aipager parte), `restart_needed` o `not_applied` (mostra `next_step` alla persona; non riavviare aipager tu). Gli avvisi non cambiano mai il codice di uscita, ma mostrali: `bot_not_in_group`, `bot_not_admin`, `bot_cannot_pin` e `group_not_found` vogliono dire che alla persona manca un passo di 5.1, e vale la pena dire chiaramente `owner_granted`.

### <a id="55-older-installs"></a>5.5 Installazioni più vecchie

Se `aipager setup group --help` non funziona, questo aipager è più vecchio del comando. Chiedi alla persona di aggiornare aipager (`aipager update`) oppure, se vuole il gruppo subito, aggiungilo a mano:

1. Aggiungi un gruppo in `~/.config/aipager/aipager.yaml`, seguendo [la struttura](groups.md#aipageryaml-schema-team-parts). Il file contiene il token del bot: non stamparlo mai e non usare `cat`, e lascia i suoi permessi a 0600 (`chmod 600 ~/.config/aipager/aipager.yaml`).
2. Controllalo con `aipager policy validate`, poi controlla che la riga `scope mode: ...` di `aipager doctor` conti il nuovo gruppo.
3. Manda ad aipager un ricaricamento senza riavvio, come sotto.

Su Linux, con il servizio in background:

```sh
pid=$(systemctl --user show -p MainPID --value aipager.service)
[ "${pid:-0}" -gt 0 ] && kill -USR1 "$pid"
```

Su macOS:

```sh
pid=$(launchctl list | awk '$3 == "com.aipager.daemon" {print $1}')
[ "${pid:-0}" -gt 0 ] 2>/dev/null && kill -USR1 "$pid"
```

Il controllo `-gt 0` è importante: senza aipager in esecuzione, `kill -USR1 0` manderebbe il segnale ai processi della tua shell. Non riavviare mai aipager da una sessione di aipager: la tua attività verrebbe tagliata.

## <a id="see-also"></a>Vedi anche

- [Comandi del bot e il comando setup](commands.md)
- [Risoluzione dei problemi](troubleshooting.md)
- [Modello di sicurezza](security.md)
