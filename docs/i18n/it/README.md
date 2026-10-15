# aipager

<!-- i18n-source: README.md @ a175bc5 -->

> Questa pagina è tradotta dalla pagina in inglese; se le due versioni non coincidono, vale quella in inglese. ([English](../../../README.md))

[![PyPI](https://img.shields.io/pypi/v/aipager?label=pypi&color=3775A9)](https://pypi.org/project/aipager/)
[![Python](https://img.shields.io/pypi/pyversions/aipager?color=3775A9)](https://pypi.org/project/aipager/)
[![License](https://img.shields.io/badge/license-MIT-black)](../../../LICENSE)

Controllo a distanza delle sessioni di [Claude Code](https://claude.com/claude-code) da Telegram. Claude gira sul tuo computer dentro un terminale separato (`dtach`) e tu lo usi dal telefono: leggi le risposte di Claude, gli invii messaggi, rispondi alle sue richieste di permesso e alle sue domande e passi da una sessione all'altra, senza tenere aperta una connessione SSH.

[aipager.run](https://aipager.run) · [Documentazione](#docs) · [Registro delle modifiche](../../../CHANGELOG.md) · [Issue su GitHub](https://github.com/dev-aly3n/aipager/issues)

**Leggi in un'altra lingua:** [English](../../../README.md) · [Español](../es/README.md) · [Русский](../ru/README.md) · [Português (Brasil)](../pt-BR/README.md) · [简体中文](../zh-Hans/README.md) · [Français](../fr/README.md) · [Deutsch](../de/README.md) · [한국어](../ko/README.md) · [العربية](../ar/README.md) · [Bahasa Indonesia](../id/README.md) · [Українська](../uk/README.md) · [Türkçe](../tr/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

## <a id="install"></a>Installazione

Hai un agente di programmazione (Claude Code, o un qualsiasi agente con una shell)? Crea un bot con [@BotFather](https://t.me/BotFather), tocca Start nel bot e controlla di aver fatto l'accesso a Claude Code su questo computer. Salva il token del bot in un file che il tuo agente non dovrà mai leggere (incolla il token, premi Invio e poi Ctrl-D):

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

Se non conosci il tuo ID utente di Telegram, dillo all'agente: può trovarlo lui.

La guida che segue l'agente, compreso il modo sicuro per passargli il token, è [Installare con un agente](install-with-an-agent.md). Per installarlo da te, continua a leggere.

Linux o macOS, qualsiasi architettura. `dtach` si installa da solo tramite la dipendenza [`dtach-bin`](https://pypi.org/project/dtach-bin/): non serve nessun pacchetto di sistema a parte.

### <a id="one-line-install-recommended"></a>Installazione in una riga (consigliata)

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

Usa `uv` se c'è, altrimenti `pipx`, altrimenti (solo su macOS) Homebrew. Se non trova nessuno dei tre, prima installa `uv` (il gestore di strumenti Python di Astral) e poi installa aipager con quello.

### <a id="uv-recommended-on-macos"></a>uv (consigliato su macOS)

```sh
uv tool install aipager     # se uv è già installato
```

Oppure, per installare prima uv:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install aipager
```

uv porta con sé il proprio interprete Python, quindi funziona su qualsiasi versione di macOS o Linux, in qualunque stato sia il Python di sistema; così eviti il conflitto tra il Python di Homebrew e Xcode descritto nella sezione Homebrew più sotto.

### <a id="pipx"></a>pipx

```sh
pipx install aipager
```

### <a id="homebrew-tap-macos-linuxbrew"></a>Homebrew tap (macOS, Linuxbrew)

> **Nota:** su macOS la strada consigliata è `uv tool install aipager`. La formula di brew funziona quando il pacchetto `python@3.12` di Homebrew e i tuoi Xcode / Command Line Tools sono allineati, ma ogni tanto si disallineano; l'ultima volta su **macOS Tahoe (26.x)**, dove l'installazione si ferma con l'errore di simbolo `pyexpat _XML_SetAllocTrackerActivationThreshold` ([issue a monte](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). Aggiornare Xcode e Command Line Tools di solito risolve, ma usare uv è più semplice.

```sh
brew install dev-aly3n/tap/aipager
```

Prende `dtach` dalla formula standard di Homebrew e installa aipager in un venv Python gestito da Homebrew.

### <a id="docker"></a>Docker

Un'immagine pronta con python, node, `claude` e `dtach` già dentro; adatta a un VPS, a un NAS o a un Raspberry Pi dove non vuoi Python o Node nel sistema. Multiarchitettura (amd64, arm64).

```sh
# 1. Esegui una volta la procedura guidata di configurazione (interattiva)
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

Monta sotto `/workspace` le cartelle che vuoi far modificare a Claude. Il montaggio di `~/.claude` porta con sé le tue credenziali di Claude e la cronologia delle conversazioni; per fare l'accesso, esegui `claude` una volta sul computer, oppure `docker exec -it aipager claude` per accedere dentro il container.

Tag: `latest`, `1.3`, `1.3.3` (uno per la versione esatta e uno per la versione minore).

### <a id="nix-flake"></a>Nix flake

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

Compila aipager dal codice sorgente con le dipendenze fissate da nixpkgs. `dtach` lo fornisce Nix; `claude` **no**: installalo a parte (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`, oppure seguendo la documentazione di Anthropic).

Per configurazioni dichiarative di NixOS o Home Manager, aggiungi aipager come input del flake e prendi il suo pacchetto in `environment.systemPackages`:

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

Poi `aipager service install` crea un servizio utente di systemd.

### <a id="snap"></a>Snap

```sh
snap install aipager
```

Uno snap con confinamento stretto che contiene python, node, `claude`, `dtach` e aipager. Per via dell'isolamento di snap, le cartelle di lavoro devono stare sotto `~/` (per esempio `~/projects/foo`). Il manifesto è in [`packaging/snap/`](../../../packaging/snap/).

## <a id="configure"></a>Configurazione

```sh
aipager config
```

Una procedura guidata interattiva: ti chiede il token del bot di Telegram (da [@BotFather](https://t.me/BotFather)) e l'ID della chat, li controlla e poi aggiunge da sola gli hook necessari a `~/.claude/settings.json`. Non devi mai modificare un file a mano.

Devi configurare aipager per qualcuno da uno script o da un agente di programmazione? `aipager setup --token-file FILE --chat-id ID` fa la stessa cosa senza domande (vedi [il comando setup](commands.md#command-line-aipager-setup-for-coding-agents) e [la guida per gli agenti](install-with-an-agent.md)).

## <a id="run"></a>Avvio

```sh
aipager start
```

aipager resta in primo piano. Avvia una sessione di Claude in un altro terminale:

```sh
aipager session dev
```

Questo crea una sessione di dtach chiamata `claude-dev` che esegue Claude Code (o si ricollega a quella esistente). aipager la trova in pochi secondi e Telegram comincia a mostrarla. Puoi anche avviare una sessione da Telegram: invia `/new dev` al bot. Riesegui lo stesso comando per ricollegarti più tardi; per uscire senza fermare Claude, chiudi il terminale o il pannello di tmux (la sessione continua a girare).

Se la sessione di dtach si è chiusa (per esempio il computer si è riavviato) ma vuoi riprendere la conversazione di Claude dal disco, aggiungi `--continue`:

```sh
aipager session dev --continue    # l'ultima conversazione di claude in questa cartella
```

Puoi anche passare `--resume <session-id>` (o qualsiasi altra opzione di claude) alla fine:

```sh
aipager session dev -- --resume abc1234
```

### <a id="run-as-a-service-survives-logout"></a>Avvio come servizio (resta attivo dopo il logout)

```sh
aipager service install
```

Su Linux scrive un servizio utente di systemd in `~/.config/systemd/user/aipager.service` e lo avvia. Su macOS scrive un plist di launchd in `~/Library/LaunchAgents/com.aipager.daemon.plist` e lo mette in funzione. Sottocomandi: `start`, `stop`, `status`, `logs`, `uninstall`.

## <a id="team-mode-groups-and-other-people"></a>Uso in team (gruppi e altre persone)

Di base aipager è un bot personale: tu e il bot in una chat privata. Per condividerlo, aggiungi un gruppo di Telegram, le chat private di altre persone con il bot, o entrambe le cose. Per un gruppo: aggiungi il bot al gruppo (come admin, così può fissare la barra di stato del gruppo), esegui di nuovo `aipager config` e scegli **Add a group scope**. La procedura guidata propone te (il proprietario della tua chat privata) come proprietario del gruppo, aggiunge ogni membro con un ruolo (`owner`, `admin`, `user` o `read_only`), chiede la cartella radice dei membri e ricarica le impostazioni di aipager senza riavviarlo. Nel gruppo il bot risponde solo a comandi, risposte ai suoi messaggi e menzioni (`@aipagerbot fix the tests`, `/jim run the tests`), e ogni messaggio viene eseguito con il ruolo di chi lo invia. Un membro `user` o `read_only` avvia e tiene le sessioni solo dentro la cartella radice della sua chat (predefinita `~/aipager-users/<label>`). **Aggiungere un membro gli permette di eseguire codice sul tuo computer**: il modello di fiducia completo è in [Uso in team e gruppi](groups.md).

## <a id="what-it-does"></a>Cosa fa

- Mostra in Telegram cosa sta facendo ogni sessione di Claude Code: se è al lavoro o pronta, gli strumenti che usa, la % di contesto, il costo e le righe cambiate, in una scheda per attività, e poi la risposta di Claude
- Invia i tuoi messaggi a Claude **subito**, anche mentre lavora: mandane quanti vuoi e Claude li mette in coda da solo, come se scrivessi nel terminale. 👀 vuol dire inviato, 👍 vuol dire che Claude l'ha preso
- Trattiene un messaggio finché c'è una richiesta di permesso o una domanda aperta, così non viene mai letto come la risposta a quella, e lo invia quando hai risposto
- Mostra le richieste di permesso e le domande di Claude con dei pulsanti (Consenti, Nega, Consenti sempre, uno per ogni opzione); un pulsante rimasto da un'attività già finita si rifiuta invece di agire sul lavoro in corso
- Tre modalità di permessi per sessione, da cambiare con `/mode`: 💬 Modalità «Chiedi» (Claude chiede prima di ogni azione), 🤖 Modalità «Automatico», quella predefinita (il controllo di sicurezza di Claude Code fa partire le azioni sicure e ferma quelle rischiose perché decida tu) e 🔓 Modalità «Completo» (tutto viene eseguito senza alcun controllo; per gli admin, e per te nella tua chat privata)
- Avvia sessioni dalla chat: `/new x1` ne avvia una subito (`/new x1 fix the tests` invia anche il primo messaggio), con la modalità, il modello e la cartella impostati in `/settings`, e i pulsanti per cambiarli
- Accetta foto, file e vocali (i vocali diventano testo sul tuo computer, con il pacchetto opzionale per la voce)
- Ti avvisa quando il contesto è quasi pieno, quando la conversazione viene compattata, quando una sessione termina e quando una sessione resta troppo a lungo in silenzio
- Esegue più sessioni insieme con un solo bot; uso in team opzionale con ruoli e regole per strumento ([Uso in team e gruppi](groups.md))
- Parla English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی e Tiếng Việt: solo i testi di aipager, mai le tue sessioni né le risposte di Claude ([Lingue](commands.md#languages)). Anche questo README e la documentazione sono tradotti (vedi **Leggi in un'altra lingua** qui sopra)

### <a id="mini-app"></a>Mini App

`/app` apre un pannello dentro Telegram: le tue sessioni con Ferma, Termina, Riavvia, Rinomina e il cambio di modalità, un visualizzatore di diff per le modifiche di `Write`/`Edit`, le impostazioni e, per il proprietario, «Segnala un problema». La fornisce aipager stesso ed è **attiva di predefinito**; ogni richiesta viene controllata con la firma `initData` di Telegram. Se sei in più di una chat (la tua chat privata e un gruppo), in alto una riga con i nomi delle chat ti fa passare dall'una all'altra. Gestiscila con `aipager miniapp enable|disable|status`, oppure puntala al tuo indirizzo al posto del tunnel gestito: vedi [Modello di sicurezza](security.md#mini-app-tunnel).

### <a id="note-on-model-buttons-bedrock--vertex-users"></a>Nota sui pulsanti dei modelli (per chi usa Bedrock e Vertex)

Il sottomenu **Modello** della tastiera fissa (e i selettori di modello della Mini App) offre gli alias di famiglia di Claude Code, `sonnet`, `opus`, `haiku`, `fable` e `opusplan`, e modelli fissi come `claude-opus-5-5` e `claude-sonnet-5-5` (l'elenco completo è in [Tastiera fissa](commands.md#persistent-keyboard)). Sull'API di Anthropic un alias punta sempre al modello più recente della sua famiglia. Su **Bedrock** e **Vertex** gli stessi alias possono puntare a versioni più vecchie, a seconda di cosa offre il tuo fornitore. Se usi questi servizi e ti serve un modello preciso, invia `/model <full-id>` dalla chat.

## <a id="developing-locally"></a>Sviluppare sul tuo computer

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach` arriva con la dipendenza `dtach-bin`, quindi non serve altro. Esegui i test con un limite di memoria come sopra (su macOS, senza `systemd-run`): altrimenti un test fuori controllo può riempire tutta la memoria del computer.

Il processo di pubblicazione delle versioni è in [CONTRIBUTING.md](../../../CONTRIBUTING.md).

## <a id="license"></a>Licenza

MIT; il testo completo è in [LICENSE](../../../LICENSE).

## <a id="docs"></a>Documentazione

| Pagina | Di cosa parla |
|---|---|
| [Installare con un agente](install-with-an-agent.md) | I passaggi che segue un agente di programmazione per installare e configurare aipager per la sua persona |
| [Architettura](architecture.md) | Come è fatto il programma, schema dei componenti, dove stanno i file e i socket |
| [Eventi hook](hooks.md) | Ogni evento che aipager riceve da Claude Code, con i suoi campi |
| [Comandi del bot](commands.md) | Comandi, pulsanti, la Mini App, vocali e file, tutti i comandi `aipager` e le impostazioni lette dall'ambiente |
| [Uso in team e gruppi](groups.md) | Configurazione per più persone, ruoli, regole, chi ha fatto cosa |
| [Risoluzione dei problemi](troubleshooting.md) | Guasti frequenti e la tabella delle soluzioni di `aipager doctor` |
| [Segnalazioni di problemi](problem-reports.md) | Cosa contiene una segnalazione, dove va e quando aipager propone di inviarla |
| [Modello di sicurezza](security.md) | Confine di fiducia, segreti, log di audit, connessioni di rete |
