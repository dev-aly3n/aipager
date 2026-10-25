<div align="center">

<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="96" height="96" alt="aipager"></a>

# aipager

<!-- i18n-source: README.md @ 6ececab -->

<h3>Claude è una chat in più.</h3>

Le tue sessioni di [Claude Code](https://claude.com/claude-code) su Telegram, accanto alla tua famiglia, ai tuoi amici e al tuo team.<br>
Girano sul tuo computer. Le segui, rispondi alle loro domande e dai loro il lavoro successivo, ovunque tu sia.

<p>
<a href="https://pypi.org/project/aipager/"><img alt="PyPI" src="https://img.shields.io/pypi/v/aipager?style=flat-square&label=pypi&labelColor=181c20&color=2aabee"></a>
<a href="https://pypi.org/project/aipager/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/aipager?style=flat-square&labelColor=181c20&color=2aabee"></a>
<a href="https://github.com/dev-aly3n/aipager/actions/workflows/test.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/dev-aly3n/aipager/test.yml?branch=main&style=flat-square&label=tests&labelColor=181c20"></a>
<img alt="Linux and macOS" src="https://img.shields.io/badge/runs%20on-Linux%20%7C%20macOS-2aabee?style=flat-square&labelColor=181c20">
<a href="../../../LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-2aabee?style=flat-square&labelColor=181c20"></a>
</p>

**[Sito web](https://aipager.run)** · **[Installazione](#install)** · **[Documentazione](#documentation)** · **[Sicurezza](security.md)** · **[Novità](../../../CHANGELOG.md)**

**Leggi in un'altra lingua:** [English](../../../README.md) · [Español](../es/README.md) · [Русский](../ru/README.md) · [Português (Brasil)](../pt-BR/README.md) · [简体中文](../zh-Hans/README.md) · [Français](../fr/README.md) · [Deutsch](../de/README.md) · [한국어](../ko/README.md) · [العربية](../ar/README.md) · [Bahasa Indonesia](../id/README.md) · [Українська](../uk/README.md) · [Türkçe](../tr/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

<sub>Questa pagina è tradotta dalla pagina in inglese; se le due versioni non coincidono, vale quella in inglese. ([English](../../../README.md))</sub>

<br>

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/hero.webp" width="100%" alt="Un messaggio scritto su Telegram arriva a Claude Code in un terminale, e una scheda nella chat si aggiorna da sola mentre Claude lavora">

<sub>Scrivi su Telegram. aipager lo digita in Claude Code sul tuo computer, e una scheda si aggiorna da sola mentre l'attività va avanti.</sub>

</div>

<br>

## <a id="why-aipager"></a>Perché aipager

<table>
<tr>
<td width="33%" valign="top">

**📟 Una scheda dal vivo per attività**<br>
Cosa sta facendo Claude in questo momento: gli strumenti che usa, il contesto, il costo e le righe cambiate, e poi la risposta. La scheda si aggiorna da sola, così la chat resta tranquilla.

</td>
<td width="33%" valign="top">

**⚡ Scrivigli anche mentre lavora**<br>
Il tuo messaggio viene digitato subito nella sessione, anche mentre Claude lavora, proprio come se scrivessi nel terminale. 👀 vuol dire inviato, 👍 vuol dire che Claude l'ha preso.

</td>
<td width="33%" valign="top">

**✅ Rispondi con un tocco**<br>
Le richieste di permesso e le domande di Claude arrivano con un pulsante per ogni risposta. Un pulsante rimasto da un'attività finita si rifiuta invece di agire su quella successiva.

</td>
</tr>
<tr>
<td width="33%" valign="top">

**🛡️ Tre modalità di permessi**<br>
💬 Modalità «Chiedi», 🤖 Modalità «Automatico» (quella predefinita per le nuove sessioni) e 🔓 Modalità «Completo», impostate per sessione e cambiate quando vuoi con `/mode`.

</td>
<td width="33%" valign="top">

**👥 Fatto per i team**<br>
Aggiungi il bot a un gruppo. Ogni messaggio gira con il ruolo di chi lo invia, i membri possono essere limitati alla propria cartella e ogni tocco finisce nel log di audit.

</td>
<td width="33%" valign="top">

**🔒 Niente in mezzo**<br>
Nessun server di aipager, nessun account, nessuna porta aperta. Il tuo token del bot, il tuo computer, le tue chat.

</td>
</tr>
</table>

## <a id="a-quick-tour"></a>Un giro veloce

### <a id="questions-come-to-you"></a>Le domande arrivano a te

Quando Claude ti chiede qualcosa o ha bisogno di un permesso, la domanda arriva nella tua chat con un pulsante per ogni risposta, e la barra fissata dice cosa ti sta aspettando. Il tuo tocco viene digitato nella sessione come se fossi alla tastiera, e il terminale mostra la stessa risposta.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/question.webp" width="100%" alt="Claude chiede se alzare il timeout o aspettare il cookie; la risposta toccata su Telegram viene scelta nel terminale">

### <a id="three-permission-modes"></a>Tre modalità di permessi

Ogni sessione è in una di tre modalità, da cambiare quando vuoi con `/mode`, anche mentre Claude lavora.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/modes.webp" width="100%" alt="La stessa attività in modalità «Chiedi», «Automatico» e «Completo»">

| Modalità | Cosa succede |
|---|---|
| 💬 **Modalità «Chiedi»**<br>`/mode ask` | Claude chiede prima di ogni azione. |
| 🤖 **Modalità «Automatico»**, quella predefinita<br>`/mode auto` | Il controllo di sicurezza di Claude Code fa partire le azioni sicure e ferma quelle rischiose perché decida tu. |
| 🔓 **Modalità «Completo»**<br>`/mode full` | Tutto viene eseguito senza alcun controllo. Per gli admin, e per te nella tua chat privata con il bot. |

Tra la modalità «Chiedi» e la modalità «Automatico» si passa sul posto, senza riavvio. Passare alla modalità «Completo», o uscirne, riavvia la sessione e la conversazione resta. Le domande di Claude ti arrivano in ogni modalità.

### <a id="your-team-in-one-group"></a>Il tuo team, in un gruppo

Aggiungi il bot a un gruppo e dai un ruolo a ogni membro: `owner`, `admin`, `user` o `read_only`. Nel gruppo il bot risponde solo a comandi, risposte ai suoi messaggi e menzioni (`@aipagerbot fix the tests`, `/web run the tests`), e ogni messaggio gira con il ruolo di chi lo invia: uno strumento che il ruolo non può usare viene rifiutato prima di partire. Un membro `user` o `read_only` lavora solo nella propria cartella radice (`~/aipager-users/<label>` di base). Anche altre persone possono avere la loro chat privata con il bot.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/team.webp" width="100%" alt="Un gruppo di team dove un comando viene rifiutato per il ruolo di un membro mentre i test passano">

> **⚠️ Importante:** Aggiungere un membro gli dà il diritto di eseguire codice sul tuo computer. Leggi [il modello di fiducia](groups.md) prima di condividere il bot.

Per configurarlo, aggiungi il bot al gruppo come admin (così può fissare la barra di stato del gruppo), esegui di nuovo `aipager config` e scegli **Add a group scope**. La procedura guidata aggiunge ogni membro con un ruolo, chiede la cartella radice dei membri e ricarica la configurazione dell'aipager in esecuzione.

### <a id="a-dashboard-inside-telegram"></a>Una dashboard dentro Telegram

`/app` apre la Mini App: le tue sessioni con Ferma, Termina, Riavvia, Rinomina e il cambio di modalità, un visualizzatore di diff per ogni `Write` e `Edit`, le impostazioni e, per il proprietario, Segnala un problema. La serve aipager stesso, è attiva di base e ogni richiesta viene controllata con la firma di Telegram. Se sei in più di una chat, una riga con i nomi delle chat in alto ti fa passare dall'una all'altra. Spegnila con `aipager miniapp disable`, o falla puntare al tuo indirizzo ([come](security.md#mini-app-tunnel)).

### <a id="fifteen-languages"></a>Quindici lingue

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/languages.webp" width="420" align="right" alt="Una richiesta di permesso in persiano, da destra a sinistra, con le cifre persiane">

- Parla English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی e Tiếng Việt.
- Scegline una con `/language`, o lascia che una chat privata segua la lingua della tua app di Telegram.
- Il persiano e l'arabo si leggono da destra a sinistra, con le loro cifre.
- Cambiano solo i testi di aipager. Le tue sessioni no: Claude risponde nella lingua in cui gli scrivi.
- Questo README e [la documentazione](#documentation) sono tradotti anche loro.

<br clear="right">

### <a id="and-the-small-things"></a>E le piccole cose

- **Tante sessioni, un solo bot.** Un messaggio normale va alla sessione con cui hai parlato per ultima; rispondi a un messaggio di una sessione per parlare con quella, o invia `/x1 <message>`.
- **Niente si mescola.** Un messaggio inviato mentre c'è una richiesta di permesso o una domanda aperta aspetta la tua risposta, così non viene mai letto come la risposta.
- **Foto, file e vocali.** I vocali diventano testo sul tuo computer, con l'extra opzionale `voice`.
- **Ti avvisa.** Contesto quasi pieno, conversazione compattata, una sessione che è terminata e una che da un po' non dà notizie.
- **Le sessioni sopravvivono a tutto.** Claude gira dentro `dtach`, quindi continua a lavorare anche se chiudi il terminale, perdi la connessione SSH o riavvii aipager.

## <a id="how-it-works"></a>Come funziona

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/how-it-works.webp" width="100%" alt="Claude Code, il suo hook, aipager, Telegram e le tue chat, in fila">

Claude Code gira in un vero terminale, dentro `dtach`, così nessuno deve tenere aperta una finestra. Ogni passo fa partire un [hook](hooks.md) che invia un datagramma ad aipager su un socket locale, e aipager modifica un messaggio nella tua chat. Le tue risposte tornano indietro allo stesso modo: una decisione all'hook che tiene in sospeso la chiamata, o tasti premuti nella sessione. Il terminale resta la fonte di verità. Di più nell'[architettura](architecture.md).

## <a id="install"></a>Installazione

Linux o macOS, qualsiasi architettura. Ti servono Claude Code, con l'accesso fatto su questo computer, e un bot di Telegram: creane uno con [@BotFather](https://t.me/BotFather) e premi «Avvia» nella sua chat.

```sh
curl -fsSL https://aipager.run/install | sh   # uv, pipx o Homebrew: quello che hai
aipager config                                # il token del bot, la tua chat, gli hook di Claude Code
aipager service install                       # tiene aipager acceso anche dopo il logout
aipager session api                           # Claude Code in questa cartella, e nella tua chat
```

L'installatore usa `uv` se ce l'hai, altrimenti `pipx`, altrimenti (su macOS) Homebrew, e installa `uv` quando non c'è nessuno dei tre. `dtach` arriva con aipager (il pacchetto [`dtach-bin`](https://pypi.org/project/dtach-bin/)), quindi non c'è altro da installare. `aipager config` è una procedura guidata: controlla il token del bot e la tua chat, poi aggiunge per te gli hook di aipager a `~/.claude/settings.json`. Non modifichi mai un file a mano.

<details>
<summary><b>Fallo installare a un agente di programmazione</b></summary>

<br>

Hai Claude Code, o un qualsiasi agente con una shell? Crea il bot, premi «Avvia» nella sua chat e salva il token del bot in un file che il tuo agente non debba mai leggere (incolla il token, premi Invio, poi Ctrl-D):

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

Poi dai al tuo agente questo messaggio:

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

Se non conosci il tuo ID utente di Telegram, dillo; l'agente può trovarlo. La guida che segue, compreso come consegnare il token in sicurezza, è [Installare con un agente](install-with-an-agent.md). Per gli script, `aipager setup --token-file FILE --chat-id ID` configura aipager senza fare domande ([il comando setup](commands.md#command-line-aipager-setup-for-coding-agents)).

</details>

### <a id="other-ways-to-install"></a>Altri modi per installare

```sh
uv tool install aipager                        # uv: consigliato su macOS, porta il suo Python
pipx install aipager                           # pipx
brew install dev-aly3n/tap/aipager             # Homebrew, su macOS e Linuxbrew (vedi la nota qui sotto)
nix profile install github:dev-aly3n/aipager   # Nix: claude non è incluso (vedi più sotto)
snap install aipager                           # Snap: isolamento stretto, claude incluso, cartelle di lavoro sotto ~/
```

Oppure fallo girare in [Docker](#docker), con Python, Node, `claude` e `dtach` dentro.

<details>
<summary><b>Homebrew su macOS 26 (Tahoe)</b></summary>

<br>

La formula funziona quando la bottle `python@3.12` di Homebrew e i tuoi Command Line Tools di Xcode vanno d'accordo, ma ogni tanto si disallineano. Su macOS 26 l'installazione fallisce con un errore di simbolo `pyexpat _XML_SetAllocTrackerActivationThreshold` ([issue upstream](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). Aggiornare Xcode e i Command Line Tools di solito lo risolve, ma con `uv tool install aipager` il problema non si presenta proprio. La formula prende `dtach` da Homebrew e installa aipager in un ambiente Python gestito da Homebrew.

</details>

### <a id="docker"></a>Docker

Un'immagine completa con Python, Node, `claude` e `dtach`, adatta a un VPS, un NAS o un Raspberry Pi dove non vuoi Python o Node sull'host. Multi-architettura (amd64, arm64).

```sh
# 1. Esegui la procedura guidata una volta (interattiva)
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. Avvia aipager (in background, con riavvio automatico)
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

Monta sotto `/workspace` le cartelle che vuoi far modificare a Claude. Il mount di `~/.claude` porta con sé le tue credenziali di Claude e la cronologia delle conversazioni: esegui `claude` una volta sull'host per fare l'accesso, o `docker exec -it aipager claude` per farlo dentro il container.

Tag: `latest`, `1.3`, `1.3.9` (uno per la versione esatta e uno per la versione minore).

### <a id="nix"></a>Nix

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

Compila aipager dal sorgente con nixpkgs fissati. `dtach` arriva da Nix; `claude` no, quindi installalo a parte (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`, o segui la documentazione di Anthropic).

<details>
<summary><b>NixOS e Home Manager</b></summary>

<br>

Aggiungi aipager come input del flake e metti il suo pacchetto in `environment.systemPackages`:

```nix
{
  inputs.aipager.url = "github:dev-aly3n/aipager";

  outputs = { self, nixpkgs, aipager, ... }: {
    nixosConfigurations.myhost = nixpkgs.lib.nixosSystem {
      modules = [({ pkgs, ... }: {
        environment.systemPackages = [
          aipager.packages.${pkgs.system}.default
        ];
      })];
    };
  };
}
```

Poi `aipager service install` crea un'unità utente di systemd.

</details>

## <a id="everyday-use"></a>Tutti i giorni

### <a id="in-telegram"></a>Su Telegram

| Invia | Per |
|---|---|
| `/status` | Vedere le sessioni di questa chat, ognuna con il suo stato e i suoi pulsanti. |
| `/new x1` | Avviare una sessione chiamata x1. `/new x1 fix the tests` invia anche il primo messaggio. |
| `/x1` | Fare di x1 la sessione a cui vanno i tuoi messaggi. `/x1 <message>` le invia un messaggio diretto. |
| `/stop` | Interrompere l'attività in corso e scartare quello che è in coda. |
| `/mode` | Passare tra la modalità «Chiedi», «Automatico» e «Completo». |
| `/app` | Aprire la Mini App. |
| `/language` | Scegliere la lingua in cui aipager parla in questa chat. |
| `/update` | Aggiornare aipager e Claude Code e poi riavviare sul posto (admin). |
| `/help` | Una breve guida per attività. |

Ogni comando, pulsante e impostazione è in [Comandi del bot](commands.md).

### <a id="on-the-command-line"></a>Da riga di comando

```sh
aipager start                             # aipager stesso, in primo piano
aipager session dev                       # avvia qui una sessione chiamata dev, o collegati a essa
aipager session dev --continue            # riprendi l'ultima conversazione di Claude in questa cartella
aipager session dev -- --resume abc1234   # ogni altra opzione di claude passa così com'è
aipager doctor                            # controlla l'installazione
```

Per uscire da una sessione senza fermare Claude, chiudi il terminale o il pannello di tmux: la sessione continua, e lo stesso comando ti ricollega.

`aipager service install` tiene aipager acceso in background: un'unità utente di systemd su Linux (`~/.config/systemd/user/aipager.service`) o un agente launchd su macOS (`~/Library/LaunchAgents/com.aipager.daemon.plist`). In entrambi i casi aipager gira nella tua cartella home, dove parte una nuova sessione se non scegli un'altra cartella. `aipager service start`, `stop`, `status`, `logs` e `uninstall` lo gestiscono.

## <a id="security"></a>Sicurezza

- **Nessun server di aipager.** aipager parla con la Bot API di Telegram usando il tuo token del bot. Non esiste un account aipager né un servizio aipager.
- **Nessuna porta aperta.** aipager ascolta solo su loopback. La Mini App esce da un tunnel Cloudflare dove ogni richiesta deve portare la firma di Telegram, e `aipager miniapp disable` lo spegne.
- **Ogni risposta resta agli atti.** Ogni Consenti, Nega e risposta viene aggiunto a `~/.claude/aipager-audit.jsonl`, con chi l'ha toccato in un gruppo.
- **Decide sempre Claude Code.** Le tue impostazioni di Claude Code dicono cosa va chiesto. aipager si limita a girare la domanda e non alza mai i propri permessi: niente sudo, tutto gira con il tuo utente.

Il quadro completo, compreso cosa protegge la tua credenziale di Claude, è nel [modello di sicurezza](security.md).

## <a id="faq"></a>Domande frequenti

<details>
<summary><b>In cosa è diverso dalle app mobili di Claude?</b></summary>

<br>

Le app di Claude parlano con l'assistente ospitato da Anthropic. aipager ti collega a una sessione di Claude Code che gira sul tuo computer: il tuo repository, i tuoi strumenti, il tuo ambiente.

</details>

<details>
<summary><b>Il mio codice esce dal mio computer?</b></summary>

<br>

aipager invia a Telegram solo quello che vedi nelle tue chat, con il tuo token del bot; in mezzo non c'è nessun server di aipager. Claude Code, invece, parla con Anthropic come sempre.

</details>

<details>
<summary><b>Cos'è dtach?</b></summary>

<br>

Un piccolo programma che stacca un terminale. Tiene in vita Claude Code quando il terminale si chiude o la connessione SSH cade, così una risposta che invii un'ora dopo arriva ancora a una sessione viva. Arriva con aipager.

</details>

<details>
<summary><b>E se Claude va fuori strada?</b></summary>

<br>

Le richieste aspettano il tuo tocco, `/stop` interrompe l'attività e scarta tutto ciò che è in coda, `/kill` termina la sessione, e i pulsanti di un'attività finita si rifiutano.

</details>

<details>
<summary><b>Come lo aggiorno?</b></summary>

<br>

Invia `/update`: controlla aipager e Claude Code e installa quello che c'è di nuovo, poi «🔄 Riavvia ora» rimette in piedi aipager, che sia un servizio o no. Le tue sessioni intanto continuano. Da riga di comando, `aipager update` aggiorna tramite l'installatore a cui appartiene aipager.

</details>

<details>
<summary><b>Uso Bedrock o Vertex. I pulsanti del modello funzionano?</b></summary>

<br>

Il menu **Modello** della tastiera e la Mini App offrono gli alias di famiglia di Claude Code (`sonnet`, `opus`, `haiku`, `fable`, `opusplan`) e modelli fissi come `claude-opus-5-5` e `claude-sonnet-5-5`. Sull'API di Anthropic un alias punta al modello più recente della sua famiglia. Su Bedrock e Vertex lo stesso alias può puntare a una versione più vecchia, a seconda di cosa offre il tuo provider; invia `/model <full-id>` per scegliere un modello preciso.

</details>

<details>
<summary><b>Uso una chiave API, un gateway o un altro provider. aipager funziona?</b></summary>

<br>

Sì. aipager avvia Claude Code come l'hai configurato e risponde alle domande che Claude Code fa all'avvio (la cartella, la chiave API, le schermate del primo avvio), così una sessione avviata da Telegram non si ferma lì. Metti le impostazioni nel blocco `env` di `~/.claude/settings.json` o in `~/.config/aipager/daemon.env` (una riga `KEY=value` per impostazione, senza virgolette). I pulsanti Opus, Sonnet e Haiku seguono `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL` e `ANTHROPIC_DEFAULT_HAIKU_MODEL`, e qualsiasi altro modello si può scrivere per nome. Anthropic supporta Claude Code solo con i modelli Claude. Vedi [Risoluzione dei problemi](troubleshooting.md#using-an-api-key-a-gateway-or-another-provider).

</details>

<details>
<summary><b>Quanto costa?</b></summary>

<br>

Niente. aipager ha licenza MIT, il bot è tuo e Claude Code continua a usare il piano o la chiave che usa già.

</details>

## <a id="documentation"></a>Documentazione

| Pagina | Di cosa parla |
|---|---|
| [Comandi del bot](commands.md) | Ogni comando, pulsante, impostazione e opzione della riga di comando |
| [Uso in team e gruppi](groups.md) | Ruoli, cartelle radice, chi può toccare cosa, il modello di fiducia |
| [Modello di sicurezza](security.md) | Confine di fiducia, segreti, log di audit, rete |
| [Eventi hook](hooks.md) | Cosa fa ogni hook di Claude Code in aipager |
| [Architettura](architecture.md) | Le parti di aipager, i suoi file e i suoi socket |
| [Risoluzione dei problemi](troubleshooting.md) | `aipager doctor` e soluzioni ai problemi comuni |
| [Segnalazioni di problemi](problem-reports.md) | Cosa contiene una segnalazione e quando aipager propone di inviarla |
| [Installare con un agente](install-with-an-agent.md) | La guida che segue un agente di programmazione |

## <a id="contributing"></a>Contribuire

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach` arriva con la dipendenza `dtach-bin`, quindi non serve altro. Esegui i test con un limite di memoria come sopra (su macOS, senza `systemd-run`): altrimenti un test fuori controllo può prendersi tutta la memoria del computer. Il processo di rilascio è in [CONTRIBUTING.md](../../../CONTRIBUTING.md).

## <a id="license"></a>Licenza

MIT. Vedi [LICENSE](../../../LICENSE).

<br>

<div align="center">
<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="32" height="32" alt=""></a>
<br>
<sub><a href="https://aipager.run">aipager.run</a></sub>
</div>
