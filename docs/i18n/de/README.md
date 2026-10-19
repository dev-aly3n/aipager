<div align="center">

<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="96" height="96" alt="aipager"></a>

# aipager

<!-- i18n-source: README.md @ 2d10247 -->

<h3>Claude ist einfach ein Chat mehr.</h3>

Deine [Claude Code](https://claude.com/claude-code)-Sitzungen in Telegram, gleich neben deiner Familie, deinen Freunden und deinem Team.<br>
Sie laufen auf deinem eigenen Computer. Du verfolgst sie, beantwortest ihre Fragen und gibst ihnen die nächste Aufgabe, egal wo du gerade bist.

<p>
<a href="https://pypi.org/project/aipager/"><img alt="PyPI" src="https://img.shields.io/pypi/v/aipager?style=flat-square&label=pypi&labelColor=181c20&color=2aabee"></a>
<a href="https://pypi.org/project/aipager/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/aipager?style=flat-square&labelColor=181c20&color=2aabee"></a>
<a href="https://github.com/dev-aly3n/aipager/actions/workflows/test.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/dev-aly3n/aipager/test.yml?branch=main&style=flat-square&label=tests&labelColor=181c20"></a>
<img alt="Linux and macOS" src="https://img.shields.io/badge/runs%20on-Linux%20%7C%20macOS-2aabee?style=flat-square&labelColor=181c20">
<a href="../../../LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-2aabee?style=flat-square&labelColor=181c20"></a>
</p>

**[Website](https://aipager.run)** · **[Installation](#install)** · **[Dokumentation](#documentation)** · **[Sicherheit](security.md)** · **[Änderungsprotokoll](../../../CHANGELOG.md)**

**In einer anderen Sprache lesen:** [English](../../../README.md) · [Español](../es/README.md) · [Русский](../ru/README.md) · [Português (Brasil)](../pt-BR/README.md) · [简体中文](../zh-Hans/README.md) · [Français](../fr/README.md) · [한국어](../ko/README.md) · [العربية](../ar/README.md) · [Bahasa Indonesia](../id/README.md) · [Українська](../uk/README.md) · [Italiano](../it/README.md) · [Türkçe](../tr/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

<sub>Diese Seite ist eine Übersetzung der englischen Seite; wenn die beiden voneinander abweichen, gilt die englische Fassung. ([English](../../../README.md))</sub>

<br>

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/hero.webp" width="100%" alt="Eine in Telegram getippte Nachricht erreicht Claude Code in einem Terminal, und eine Karte im Chat aktualisiert sich, während Claude arbeitet">

<sub>Du schreibst in Telegram. aipager tippt es in Claude Code auf deinem Computer, und eine Karte aktualisiert sich, während die Aufgabe läuft.</sub>

</div>

<br>

## <a id="why-aipager"></a>Warum aipager

<table>
<tr>
<td width="33%" valign="top">

**📟 Eine Live-Karte pro Aufgabe**<br>
Was Claude gerade tut: die Tools, die es aufruft, Kontext, Kosten und geänderte Zeilen, danach die Antwort. Die Karte aktualisiert sich selbst, so bleibt der Chat ruhig.

</td>
<td width="33%" valign="top">

**⚡ Auch mitten in der Arbeit**<br>
Deine Nachricht wird sofort in die Sitzung getippt, auch während Claude arbeitet, genau wie beim Tippen im Terminal. 👀 heißt gesendet, 👍 heißt, Claude hat sie abgeholt.

</td>
<td width="33%" valign="top">

**✅ Antworten mit einem Tippen**<br>
Berechtigungsanfragen und Fragen von Claude kommen mit einem Button pro Antwort. Ein Button, der von einer fertigen Aufgabe übrig ist, ist veraltet und wirkt nicht auf die nächste.

</td>
</tr>
<tr>
<td width="33%" valign="top">

**🛡️ Drei Berechtigungsmodi**<br>
💬 Fragen-Modus, 🤖 Auto-Modus (der Standard für neue Sitzungen) und 🔓 Vollzugriff-Modus, pro Sitzung eingestellt und jederzeit mit `/mode` gewechselt.

</td>
<td width="33%" valign="top">

**👥 Gemacht für Teams**<br>
Füg den Bot zu einer Gruppe hinzu. Jede Nachricht läuft mit der Rolle der Person, die sie schickt, Mitglieder lassen sich auf ihren eigenen Ordner beschränken, und jedes Tippen steht im Audit-Log.

</td>
<td width="33%" valign="top">

**🔒 Nichts dazwischen**<br>
Kein aipager-Server, kein Konto, kein offener Port. Dein Bot-Token, dein Computer, deine Chats.

</td>
</tr>
</table>

## <a id="a-quick-tour"></a>Ein kurzer Rundgang

### <a id="questions-come-to-you"></a>Fragen kommen zu dir

Wenn Claude dich etwas fragt oder eine Berechtigung braucht, landet die Frage mit einem Button pro Antwort in deinem Chat, und die angeheftete Leiste sagt, was auf dich wartet. Deine Antwort wird in die Sitzung getippt, als säßest du an der Tastatur, und das Terminal zeigt dieselbe Antwort.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/question.webp" width="100%" alt="Claude fragt, ob es das Timeout erhöhen oder auf das Cookie warten soll; die in Telegram getippte Antwort wird im Terminal gewählt">

### <a id="three-permission-modes"></a>Drei Berechtigungsmodi

Jede Sitzung ist in einem von drei Modi, jederzeit mit `/mode` gewechselt, auch während Claude arbeitet.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/modes.webp" width="100%" alt="Dieselbe Aufgabe im Fragen-Modus, im Auto-Modus und im Vollzugriff-Modus">

| Modus | Was passiert |
|---|---|
| 💬 **Fragen-Modus**<br>`/mode ask` | Claude fragt vor jeder Aktion. |
| 🤖 **Auto-Modus**, der Standard<br>`/mode auto` | Die eigene Sicherheitsprüfung von Claude Code lässt sichere Aktionen durch und stoppt riskante, damit du entscheidest. |
| 🔓 **Vollzugriff-Modus**<br>`/mode full` | Alles läuft ohne jede Prüfung. Für Admins und für dich in deinem eigenen privaten Chat mit dem Bot. |

Zwischen Fragen-Modus und Auto-Modus wird direkt gewechselt, ohne Neustart. Der Wechsel in den Vollzugriff-Modus oder aus ihm heraus startet die Sitzung neu; die Unterhaltung bleibt erhalten. Fragen von Claude kommen in jedem Modus zu dir.

### <a id="your-team-in-one-group"></a>Dein Team in einer Gruppe

Füg den Bot zu einer Gruppe hinzu und gib jedem Mitglied eine Rolle: `owner`, `admin`, `user` oder `read_only`. In der Gruppe reagiert der Bot nur auf Befehle, Antworten auf seine Nachrichten und Erwähnungen (`@aipagerbot fix the tests`, `/web run the tests`), und jede Nachricht läuft mit der Rolle der Person, die sie schickt: Ein Tool, das die Rolle nicht nutzen darf, wird abgelehnt, bevor es läuft. Ein Mitglied mit `user` oder `read_only` arbeitet nur in seinem eigenen Stammordner (standardmäßig `~/aipager-users/<label>`). Andere Personen können auch ihren eigenen privaten Chat mit dem Bot haben.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/team.webp" width="100%" alt="Eine Teamgruppe, in der ein Befehl wegen der Rolle eines Mitglieds abgelehnt wird, während die Tests durchlaufen">

> **⚠️ Wichtig:** Wer Mitglied wird, darf Code auf deinem Computer ausführen. Lies [das Vertrauensmodell](groups.md), bevor du den Bot teilst.

Zum Einrichten fügst du den Bot als Admin zur Gruppe hinzu (damit er die Statusleiste der Gruppe anheften kann), führst `aipager config` noch einmal aus und wählst **Add a group scope**. Der Assistent fügt jedes Mitglied mit einer Rolle hinzu, fragt nach dem Stammordner der Mitglieder und lädt die Konfiguration des laufenden aipager neu.

### <a id="a-dashboard-inside-telegram"></a>Ein Dashboard in Telegram

`/app` öffnet die Mini App: deine Sitzungen mit Stoppen, Beenden, Neu starten, Umbenennen und dem Moduswechsel, eine Diff-Ansicht für jedes `Write` und `Edit`, die Einstellungen und, für den Besitzer, Problem melden. aipager stellt sie selbst bereit, sie ist standardmäßig an, und jede Anfrage wird mit der Signatur von Telegram geprüft. Wenn du in mehr als einem Chat bist, wechselt eine Zeile mit Chatnamen oben zwischen ihnen. Schalte sie mit `aipager miniapp disable` aus oder lass sie auf deine eigene Adresse zeigen ([so geht's](security.md#mini-app-tunnel)).

### <a id="fifteen-languages"></a>Fünfzehn Sprachen

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/languages.webp" width="420" align="right" alt="Eine Berechtigungsanfrage auf Persisch, von rechts nach links, mit persischen Ziffern">

- Spricht English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی und Tiếng Việt.
- Wähl eine mit `/language` oder lass einen privaten Chat der Sprache deiner Telegram-App folgen.
- Persisch und Arabisch laufen von rechts nach links, mit ihren eigenen Ziffern.
- Nur die Texte von aipager selbst ändern sich. Deine Sitzungen nicht: Claude antwortet in der Sprache, in der du ihm schreibst.
- Dieses README und [die Dokumentation](#documentation) sind auch übersetzt.

<br clear="right">

### <a id="and-the-small-things"></a>Und die kleinen Dinge

- **Viele Sitzungen, ein Bot.** Eine normale Nachricht geht an die Sitzung, mit der du zuletzt geredet hast; antworte auf eine Nachricht einer Sitzung, um mit genau dieser zu reden, oder schick `/x1 <message>`.
- **Nichts kommt durcheinander.** Eine Nachricht, die du schickst, während eine Berechtigungsanfrage oder eine Frage offen ist, wartet, bis du geantwortet hast, damit sie nie als Antwort darauf gelesen wird.
- **Fotos, Dateien und Sprachnachrichten.** Sprachnachrichten werden auf deinem Computer in Text umgewandelt, mit dem optionalen Extra `voice`.
- **Es sagt dir Bescheid.** Wenn der Kontext fast voll ist, die Unterhaltung komprimiert wird, eine Sitzung beendet ist oder eine sich länger nicht meldet.
- **Sitzungen überleben alles.** Claude läuft in `dtach` und arbeitet weiter, wenn du das Terminal schließt, SSH abbricht oder aipager neu startet.

## <a id="how-it-works"></a>So funktioniert es

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/how-it-works.webp" width="100%" alt="Claude Code, sein Hook, aipager, Telegram und deine Chats, in einer Reihe">

Claude Code läuft in einem echten Terminal, in `dtach`, damit niemand ein Fenster offen halten muss. Jeder Schritt löst einen [Hook](hooks.md) aus, der ein Datagramm über einen lokalen Socket an aipager schickt, und aipager bearbeitet eine Nachricht in deinem Chat. Deine Antworten nehmen denselben Weg zurück: eine Entscheidung an den Hook, der auf den Aufruf wartet, oder Tastendrücke in die Sitzung. Das Terminal bleibt die Quelle der Wahrheit. Mehr dazu in der [Architektur](architecture.md).

## <a id="install"></a>Installation

Linux oder macOS, jede Architektur. Du brauchst Claude Code, auf diesem Computer angemeldet, und einen Telegram-Bot: Erstell einen mit [@BotFather](https://t.me/BotFather) und tippe darin auf „Starten“.

```sh
curl -fsSL https://aipager.run/install | sh   # uv, pipx oder Homebrew: was du eben hast
aipager config                                # dein Bot-Token, dein Chat, die Hooks von Claude Code
aipager service install                       # hält aipager am Laufen, auch nach dem Abmelden
aipager session api                           # Claude Code in diesem Ordner und in deinem Chat
```

Das Installationsskript nimmt `uv`, wenn du es hast, sonst `pipx`, sonst (auf macOS) Homebrew, und installiert `uv`, wenn nichts davon da ist. `dtach` kommt mit aipager (das Paket [`dtach-bin`](https://pypi.org/project/dtach-bin/)), du musst also sonst nichts installieren. `aipager config` ist ein Assistent: Er prüft den Bot-Token und deinen Chat und trägt dann die Hooks von aipager für dich in `~/.claude/settings.json` ein. Du bearbeitest nie eine Datei von Hand.

<details>
<summary><b>Lass einen Coding-Agenten installieren</b></summary>

<br>

Du hast Claude Code oder einen anderen Agenten mit Shell? Erstell den Bot, tippe darin auf „Starten“ und speichere den Token des Bots in einer Datei, die dein Agent nie lesen muss (Token einfügen, Enter drücken, dann Ctrl-D):

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

Dann gib deinem Agenten diesen Prompt:

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

Wenn du deine Telegram-ID nicht kennst, sag das stattdessen; der Agent kann sie herausfinden. Die Anleitung, der er folgt, auch dazu, wie der Token sicher übergeben wird, ist [Mit einem Agenten installieren](install-with-an-agent.md). Für Skripte richtet `aipager setup --token-file FILE --chat-id ID` aipager ohne jede Frage ein ([der Befehl setup](commands.md#command-line-aipager-setup-for-coding-agents)).

</details>

### <a id="other-ways-to-install"></a>Weitere Wege zur Installation

```sh
uv tool install aipager                        # uv: auf macOS empfohlen, bringt sein eigenes Python mit
pipx install aipager                           # pipx
brew install dev-aly3n/tap/aipager             # Homebrew, auf macOS und Linuxbrew (siehe Hinweis unten)
nix profile install github:dev-aly3n/aipager   # Nix: claude ist nicht dabei (siehe unten)
snap install aipager                           # Snap: strikt abgeschottet, claude dabei, Arbeitsordner unter ~/
```

Oder lass es in [Docker](#docker) laufen, mit Python, Node, `claude` und `dtach` an Bord.

<details>
<summary><b>Homebrew auf macOS 26 (Tahoe)</b></summary>

<br>

Die Formel funktioniert, wenn die `python@3.12`-Bottle von Homebrew und deine Xcode Command Line Tools zusammenpassen, aber die beiden laufen ab und zu auseinander. Auf macOS 26 bricht die Installation mit einem Symbolfehler `pyexpat _XML_SetAllocTrackerActivationThreshold` ab ([Upstream-Issue](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). Xcode und die Command Line Tools zu aktualisieren hilft meistens, aber mit `uv tool install aipager` passiert das gar nicht erst. Die Formel nimmt `dtach` von Homebrew und installiert aipager in eine Python-Umgebung, die Homebrew verwaltet.

</details>

### <a id="docker"></a>Docker

Ein in sich geschlossenes Image mit Python, Node, `claude` und `dtach`, gut für einen VPS, ein NAS oder einen Raspberry Pi, wenn du auf dem Host kein Python oder Node haben willst. Für mehrere Architekturen (amd64, arm64).

```sh
# 1. Den Einrichtungsassistenten einmal ausführen (interaktiv)
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. aipager starten (im Hintergrund, mit automatischem Neustart)
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

Häng die Ordner, die Claude bearbeiten soll, unter `/workspace` ein. Der Mount von `~/.claude` bringt deine Anmeldedaten für Claude und den Verlauf deiner Unterhaltungen mit: Führ `claude` einmal auf dem Host aus, um dich anzumelden, oder melde dich mit `docker exec -it aipager claude` im Container an.

Tags: `latest`, `1.3`, `1.3.7` (eines pro genauer Version und eines pro Unterversion).

### <a id="nix"></a>Nix

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

Baut aipager aus dem Quellcode gegen fest gepinnte nixpkgs. `dtach` kommt von Nix, `claude` nicht, installier es also getrennt (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`, oder folge der Dokumentation von Anthropic).

<details>
<summary><b>NixOS und Home Manager</b></summary>

<br>

Füg aipager als Flake-Input hinzu und nimm sein Paket in `environment.systemPackages` auf:

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

Danach richtet `aipager service install` eine systemd-Benutzereinheit ein.

</details>

## <a id="everyday-use"></a>Im Alltag

### <a id="in-telegram"></a>In Telegram

| Schick | Um |
|---|---|
| `/status` | die Sitzungen dieses Chats zu sehen, jede mit ihrem Zustand und ihren Buttons. |
| `/new x1` | eine Sitzung namens x1 zu starten. `/new x1 fix the tests` schickt auch gleich die erste Nachricht. |
| `/x1` | x1 zur Sitzung zu machen, an die deine Nachrichten gehen. `/x1 <message>` schickt ihr direkt eine Nachricht. |
| `/stop` | die laufende Aufgabe zu unterbrechen und alles Wartende zu verwerfen. |
| `/mode` | zwischen Fragen-Modus, Auto-Modus und Vollzugriff-Modus zu wechseln. |
| `/app` | die Mini App zu öffnen. |
| `/language` | die Sprache zu wählen, die aipager in diesem Chat spricht. |
| `/update` | aipager und Claude Code zu aktualisieren und an Ort und Stelle neu zu starten (für Admins). |
| `/help` | eine kurze Anleitung nach Aufgaben zu bekommen. |

Jeder Befehl, jeder Button und jede Einstellung steht in [Befehle des Bots](commands.md).

### <a id="on-the-command-line"></a>Auf der Befehlszeile

```sh
aipager start                             # aipager selbst, im Vordergrund
aipager session dev                       # hier eine Sitzung namens dev starten oder sich mit ihr verbinden
aipager session dev --continue            # die letzte Claude-Unterhaltung in diesem Ordner fortsetzen
aipager session dev -- --resume abc1234   # jedes andere claude-Flag geht unverändert durch
aipager doctor                            # die Installation prüfen
```

Um eine Sitzung zu verlassen, ohne Claude zu stoppen, schließ das Terminal oder den tmux-Bereich: Die Sitzung läuft weiter, und derselbe Befehl verbindet dich wieder.

`aipager service install` hält aipager im Hintergrund am Laufen: als systemd-Benutzereinheit auf Linux (`~/.config/systemd/user/aipager.service`) oder als launchd-Agent auf macOS (`~/Library/LaunchAgents/com.aipager.daemon.plist`). In beiden Fällen läuft aipager in deinem Home-Ordner, wo eine neue Sitzung startet, wenn du keinen anderen Ordner wählst. Mit `aipager service start`, `stop`, `status`, `logs` und `uninstall` verwaltest du den Dienst.

## <a id="security"></a>Sicherheit

- **Kein aipager-Server.** aipager spricht mit deinem eigenen Bot-Token direkt mit der Bot API von Telegram. Es gibt kein aipager-Konto und keinen aipager-Dienst.
- **Kein offener Port.** aipager lauscht nur auf Loopback. Die Mini App geht über einen Cloudflare-Tunnel nach draußen, in dem jede Anfrage die Signatur von Telegram tragen muss, und `aipager miniapp disable` schaltet ihn ab.
- **Jede Antwort wird festgehalten.** Jedes Erlauben, Ablehnen und jede Antwort wird an `~/.claude/aipager-audit.jsonl` angehängt, in einer Gruppe mit dem Namen der Person, die getippt hat.
- **Claude Code entscheidet weiterhin.** Deine Einstellungen von Claude Code legen fest, was gefragt wird. aipager leitet die Frage nur weiter und holt sich nie mehr Rechte: kein sudo, alles läuft als dein Benutzer.

Das ganze Bild, auch was deine Anmeldedaten für Claude schützt, steht im [Sicherheitsmodell](security.md).

## <a id="faq"></a>Häufige Fragen

<details>
<summary><b>Was ist der Unterschied zu den Mobil-Apps von Claude?</b></summary>

<br>

Die Apps von Claude sprechen mit dem Assistenten, den Anthropic betreibt. aipager verbindet dich mit einer Claude-Code-Sitzung auf deinem eigenen Computer: dein Repository, deine Tools, deine Umgebung.

</details>

<details>
<summary><b>Verlässt mein Code meinen Computer?</b></summary>

<br>

aipager schickt an Telegram nur, was du in deinen Chats siehst, mit deinem eigenen Bot-Token; dazwischen gibt es keinen aipager-Server. Claude Code selbst spricht wie immer mit Anthropic.

</details>

<details>
<summary><b>Was ist dtach?</b></summary>

<br>

Ein winziges Programm, das ein Terminal abkoppelt. Es hält Claude Code am Laufen, wenn das Terminal geschlossen wird oder SSH abbricht, sodass eine Antwort, die du eine Stunde später schickst, noch eine laufende Sitzung erreicht. Es kommt mit aipager.

</details>

<details>
<summary><b>Und wenn Claude aus dem Ruder läuft?</b></summary>

<br>

Anfragen warten auf dein Tippen, `/stop` unterbricht die Aufgabe und verwirft alles Wartende, `/kill` beendet die Sitzung, und Buttons einer fertigen Aufgabe sind veraltet und tun nichts mehr.

</details>

<details>
<summary><b>Wie aktualisiere ich es?</b></summary>

<br>

Schick `/update`: Es prüft aipager und Claude Code und installiert, was neu ist, und „🔄 Jetzt neu starten“ startet aipager neu, ob als Dienst oder nicht. Deine Sitzungen laufen dabei weiter. Auf der Befehlszeile aktualisiert `aipager update` über den Installer, dem aipager gehört.

</details>

<details>
<summary><b>Ich nutze Bedrock oder Vertex. Funktionieren die Modell-Buttons?</b></summary>

<br>

Das Menü **Modell** der Tastatur und die Mini App bieten die Familien-Aliase von Claude Code (`sonnet`, `opus`, `haiku`, `fable`, `opusplan`) und feste Modelle wie `claude-opus-5-5` und `claude-sonnet-5-5` an. Über die API von Anthropic zeigt ein Alias auf das neueste Modell seiner Familie. Auf Bedrock und Vertex kann derselbe Alias je nach Angebot deines Anbieters auf einen älteren Stand zeigen; schick `/model <full-id>`, um ein genaues Modell zu wählen.

</details>

<details>
<summary><b>Ich nutze einen API-Schlüssel, ein Gateway oder einen anderen Anbieter. Funktioniert aipager?</b></summary>

<br>

Ja. aipager startet Claude Code so, wie du es eingerichtet hast, und beantwortet die Fragen, die Claude Code beim Start stellt (der Ordner, der API-Schlüssel, die Bildschirme beim ersten Start), damit eine aus Telegram gestartete Session nicht daran hängen bleibt. Trag die Einstellungen in den `env`-Block von `~/.claude/settings.json` ein oder in `~/.config/aipager/daemon.env` (eine Zeile `KEY=value` pro Einstellung, ohne Anführungszeichen). Die Buttons Opus, Sonnet und Haiku folgen `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL` und `ANTHROPIC_DEFAULT_HAIKU_MODEL`, und jedes andere Modell kannst du mit seinem Namen eintippen. Anthropic unterstützt Claude Code nur mit Claude-Modellen. Mehr dazu unter [Fehlerbehebung](troubleshooting.md#using-an-api-key-a-gateway-or-another-provider).

</details>

<details>
<summary><b>Was kostet es?</b></summary>

<br>

Nichts. aipager steht unter der MIT-Lizenz, der Bot gehört dir, und Claude Code nutzt weiter den Plan oder Schlüssel, den es schon nutzt.

</details>

## <a id="documentation"></a>Dokumentation

| Seite | Worum es geht |
|---|---|
| [Befehle des Bots](commands.md) | Jeder Befehl, jeder Button, jede Einstellung und jede Option der Befehlszeile |
| [Teambetrieb und Gruppen](groups.md) | Rollen, Stammordner, wer was antippen darf, das Vertrauensmodell |
| [Sicherheitsmodell](security.md) | Vertrauensgrenze, Geheimnisse, Audit-Log, Netzwerk |
| [Hook-Ereignisse](hooks.md) | Was jeder Hook von Claude Code in aipager tut |
| [Architektur](architecture.md) | Die Teile von aipager, seine Dateien und Sockets |
| [Fehlerbehebung](troubleshooting.md) | `aipager doctor` und Lösungen für häufige Probleme |
| [Problemberichte](problem-reports.md) | Was ein Bericht enthält und wann aipager ihn anbietet |
| [Mit einem Agenten installieren](install-with-an-agent.md) | Die Anleitung, der ein Coding-Agent folgt |

## <a id="contributing"></a>Mitmachen

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach` kommt mit der Abhängigkeit `dtach-bin`, mehr braucht es nicht. Führ die Tests wie oben mit einer Speichergrenze aus (auf macOS ohne `systemd-run`): Sonst kann ein außer Kontrolle geratener Test den ganzen Speicher des Computers belegen. Wie Versionen veröffentlicht werden, steht in [CONTRIBUTING.md](../../../CONTRIBUTING.md).

## <a id="license"></a>Lizenz

MIT; den vollständigen Text findest du in [LICENSE](../../../LICENSE).

<br>

<div align="center">
<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="32" height="32" alt=""></a>
<br>
<sub><a href="https://aipager.run">aipager.run</a></sub>
</div>
