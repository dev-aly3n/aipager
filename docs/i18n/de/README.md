# aipager

<!-- i18n-source: README.md @ a175bc5 -->

> Diese Seite ist eine Übersetzung der englischen Seite; wenn die beiden voneinander abweichen, gilt die englische Fassung. ([English](../../../README.md))

[![PyPI](https://img.shields.io/pypi/v/aipager?label=pypi&color=3775A9)](https://pypi.org/project/aipager/)
[![Python](https://img.shields.io/pypi/pyversions/aipager?color=3775A9)](https://pypi.org/project/aipager/)
[![License](https://img.shields.io/badge/license-MIT-black)](../../../LICENSE)

Die Telegram-Fernbedienung für deine [Claude Code](https://claude.com/claude-code)-Sitzungen. Claude läuft auf deinem Computer in einem abgekoppelten Terminal (`dtach`), und du nutzt es vom Handy aus: Du liest Claudes Antworten, schickst ihm Nachrichten, beantwortest seine Berechtigungsanfragen und Fragen und wechselst zwischen Sitzungen, ohne dass eine SSH-Verbindung offen bleiben muss.

[aipager.run](https://aipager.run) · [Dokumentation](#docs) · [Änderungsprotokoll](../../../CHANGELOG.md) · [Issues auf GitHub](https://github.com/dev-aly3n/aipager/issues)

**In einer anderen Sprache lesen:** [English](../../../README.md) · [Español](../es/README.md) · [Русский](../ru/README.md) · [Português (Brasil)](../pt-BR/README.md) · [简体中文](../zh-Hans/README.md) · [Français](../fr/README.md) · [한국어](../ko/README.md) · [العربية](../ar/README.md) · [Bahasa Indonesia](../id/README.md) · [Українська](../uk/README.md) · [Italiano](../it/README.md) · [Türkçe](../tr/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

## <a id="install"></a>Installation

Du hast einen Coding-Agenten (Claude Code oder irgendeinen Agenten mit Shell)? Erstelle einen Bot mit [@BotFather](https://t.me/BotFather), tippe darin auf Start und prüfe, dass Claude Code auf diesem Computer bei deinem Konto angemeldet ist. Speichere den Token des Bots in einer Datei, die dein Agent nie lesen muss (Token einfügen, Enter drücken, dann Ctrl-D):

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

Dann gib deinem Agenten diese Nachricht:

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

Wenn du deine Telegram-ID nicht kennst, sag das dem Agenten; er kann sie herausfinden.

Die Anleitung, der der Agent folgt, einschließlich der sicheren Übergabe des Tokens, ist [Mit einem Agenten installieren](install-with-an-agent.md). Wenn du selbst installieren willst, lies weiter.

Linux oder macOS, jede Architektur. `dtach` wird über die Abhängigkeit [`dtach-bin`](https://pypi.org/project/dtach-bin/) automatisch installiert; ein eigenes Systempaket ist nicht nötig.

### <a id="one-line-install-recommended"></a>Installation in einer Zeile (empfohlen)

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

Sie nimmt `uv`, wenn es da ist, sonst `pipx`, sonst (nur auf macOS) Homebrew. Findet sie nichts davon, installiert sie zuerst `uv` (den Python-Tool-Manager von Astral) und installiert aipager damit.

### <a id="uv-recommended-on-macos"></a>uv (empfohlen auf macOS)

```sh
uv tool install aipager     # wenn uv schon installiert ist
```

Oder, um zuerst uv zu installieren:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install aipager
```

uv bringt seinen eigenen Python-Interpreter mit, deshalb funktioniert das auf jeder macOS- oder Linux-Version, egal in welchem Zustand das Python des Systems ist; so umgehst du den Konflikt zwischen dem Python von Homebrew und Xcode, der weiter unten im Abschnitt Homebrew beschrieben ist.

### <a id="pipx"></a>pipx

```sh
pipx install aipager
```

### <a id="homebrew-tap-macos-linuxbrew"></a>Homebrew tap (macOS, Linuxbrew)

> **Hinweis:** Auf macOS ist `uv tool install aipager` der empfohlene Weg. Die brew-Formel funktioniert, wenn das Paket `python@3.12` von Homebrew und deine Xcode / Command Line Tools zueinander passen, aber sie geraten immer wieder auseinander; zuletzt auf **macOS Tahoe (26.x)**, wo die Installation mit dem Symbolfehler `pyexpat _XML_SetAllocTrackerActivationThreshold` scheitert ([Issue im Upstream](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). Ein Update von Xcode und den Command Line Tools behebt das meistens, aber mit uv ist es einfacher.

```sh
brew install dev-aly3n/tap/aipager
```

Holt `dtach` aus der Standardformel von Homebrew und installiert aipager in ein Python-venv, das Homebrew verwaltet.

### <a id="docker"></a>Docker

Ein vollständiges Image, in dem python, node, `claude` und `dtach` schon drin sind; praktisch für einen VPS, ein NAS oder einen Raspberry Pi, auf dem du weder Python noch Node im System haben willst. Für mehrere Architekturen (amd64, arm64).

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

Binde unter `/workspace` die Ordner ein, die Claude bearbeiten soll. Die Einbindung von `~/.claude` bringt deine Claude-Anmeldedaten und den Verlauf deiner Unterhaltungen mit; zum Anmelden führ einmal `claude` auf dem System aus, oder `docker exec -it aipager claude`, um dich im Container anzumelden.

Tags: `latest`, `1.2`, `1.2.0` (eines pro genauer Version und eines pro Unterversion).

### <a id="nix-flake"></a>Nix flake

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

Baut aipager aus dem Quellcode mit festgelegten Abhängigkeiten aus nixpkgs. `dtach` kommt von Nix; `claude` **nicht**: Installiere es getrennt (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`, oder nach der Dokumentation von Anthropic).

Für eine deklarative Konfiguration mit NixOS oder Home Manager füg aipager als Eingabe des Flakes hinzu und nimm sein Paket in `environment.systemPackages` auf:

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

Danach richtet `aipager service install` einen systemd-Benutzerdienst ein.

### <a id="snap"></a>Snap

```sh
snap install aipager
```

Ein Snap mit strikter Isolierung, das python, node, `claude`, `dtach` und aipager enthält. Wegen der Sandbox von Snap müssen die Arbeitsordner unter `~/` liegen (zum Beispiel `~/projects/foo`). Das Manifest liegt in [`packaging/snap/`](../../../packaging/snap/).

## <a id="configure"></a>Einrichten

```sh
aipager config
```

Ein interaktiver Assistent: Er fragt nach dem Token deines Telegram-Bots (von [@BotFather](https://t.me/BotFather)) und der Chat-ID, prüft beide und trägt dann die nötigen Hooks selbst in `~/.claude/settings.json` ein. Du bearbeitest nie eine Datei von Hand.

Du richtest aipager für jemanden per Skript oder mit einem Coding-Agenten ein? `aipager setup --token-file FILE --chat-id ID` macht dasselbe ohne Rückfragen (siehe [den Befehl setup](commands.md#command-line-aipager-setup-for-coding-agents) und [die Anleitung für Agenten](install-with-an-agent.md)).

## <a id="run"></a>Starten

```sh
aipager start
```

aipager bleibt im Vordergrund. Starte in einem anderen Terminal eine Claude-Sitzung:

```sh
aipager session dev
```

Das erstellt eine dtach-Sitzung namens `claude-dev`, in der Claude Code läuft (oder verbindet sich wieder mit ihr, wenn es sie schon gibt). aipager findet sie innerhalb weniger Sekunden, und Telegram zeigt sie an. Du kannst eine Sitzung auch aus Telegram starten: Schick dem Bot `/new dev`. Führ denselben Befehl noch einmal aus, um dich später wieder zu verbinden; um zu gehen, ohne Claude zu stoppen, schließ das Terminal oder den tmux-Bereich (die Sitzung läuft weiter).

Wenn die dtach-Sitzung beendet wurde (zum Beispiel nach einem Neustart des Computers), du die Unterhaltung von Claude aber von der Festplatte fortsetzen willst, häng `--continue` an:

```sh
aipager session dev --continue    # die letzte claude-Unterhaltung in diesem Ordner
```

Du kannst auch `--resume <session-id>` (oder jede andere Option von claude) am Ende mitgeben:

```sh
aipager session dev -- --resume abc1234
```

### <a id="run-as-a-service-survives-logout"></a>Als Dienst laufen lassen (läuft nach dem Abmelden weiter)

```sh
aipager service install
```

Auf Linux schreibt das einen systemd-Benutzerdienst nach `~/.config/systemd/user/aipager.service` und startet ihn. Auf macOS schreibt es eine launchd-plist nach `~/Library/LaunchAgents/com.aipager.daemon.plist` und startet sie. Unterbefehle: `start`, `stop`, `status`, `logs`, `uninstall`.

## <a id="team-mode-groups-and-other-people"></a>Teambetrieb (Gruppen und andere Personen)

aipager ist standardmäßig ein privater Bot: du und der Bot in einem privaten Chat. Zum Teilen fügst du eine Telegram-Gruppe hinzu, die privaten Chats anderer Personen mit dem Bot oder beides. Für eine Gruppe: Füg den Bot zur Gruppe hinzu (als Admin, damit er die Statusleiste der Gruppe anheften kann), führ `aipager config` noch einmal aus und wähl **Add a group scope**. Der Assistent schlägt dich (den Besitzer deines eigenen privaten Chats) als Besitzer der Gruppe vor, fügt jedes Mitglied mit einer Rolle hinzu (`owner`, `admin`, `user` oder `read_only`), fragt nach dem Stammordner der Mitglieder und lädt die Konfiguration von aipager ohne Neustart neu. In der Gruppe reagiert der Bot nur auf Befehle, Antworten auf seine Nachrichten und Erwähnungen (`@aipagerbot fix the tests`, `/jim run the tests`), und jede Nachricht läuft mit der Rolle der Person, die sie geschickt hat. Ein Mitglied mit `user` oder `read_only` startet und behält Sitzungen nur im Stammordner seines Chats (standardmäßig `~/aipager-users/<label>`). **Ein Mitglied hinzuzufügen gibt ihm das Recht, Code auf deinem Computer auszuführen**: Das vollständige Vertrauensmodell steht in [Teambetrieb und Gruppen](groups.md).

## <a id="what-it-does"></a>Was es macht

- Zeigt in Telegram, was jede Claude-Code-Sitzung tut: ob sie arbeitet oder bereit ist, welche Tools sie nutzt, den Kontext in %, die Kosten und die geänderten Zeilen, auf einer Karte pro Aufgabe, danach die Antwort von Claude
- Schickt deine Nachrichten **sofort** an Claude, auch während es arbeitet: Schick mehrere, und Claude stellt sie selbst in die Warteschlange, genau wie beim Tippen im Terminal. 👀 heißt gesendet, 👍 heißt, Claude hat sie abgeholt
- Hält eine Nachricht zurück, solange eine Berechtigungsanfrage oder eine Frage offen ist, damit sie nie als Antwort darauf gelesen wird, und schickt sie, sobald du geantwortet hast
- Zeigt die Berechtigungsanfragen und Fragen von Claude mit Buttons (Erlauben, Ablehnen, Immer erlauben, einer pro Option); ein Button, der von einer schon fertigen Aufgabe übrig ist, verweigert, statt auf die laufende Arbeit zu wirken
- Drei Berechtigungsmodi pro Sitzung, gewechselt mit `/mode`: 💬 Fragen-Modus (Claude fragt vor jeder Aktion), 🤖 Auto-Modus, der Standard (die eigene Sicherheitsprüfung von Claude Code lässt sichere Aktionen durch und stoppt riskante, damit du entscheidest) und 🔓 Vollzugriff-Modus (alles läuft ohne jede Prüfung; für Admins und für dich in deinem eigenen privaten Chat)
- Startet Sitzungen aus dem Chat: `/new x1` startet sofort eine (`/new x1 fix the tests` schickt auch gleich die erste Nachricht), mit dem in `/settings` eingestellten Modus, Modell und Ordner, und mit Buttons, um sie zu ändern
- Nimmt Fotos, Dateien und Sprachnachrichten an (Sprachnachrichten werden auf deinem Computer in Text umgewandelt, mit dem optionalen Sprachpaket)
- Sagt dir Bescheid, wenn der Kontext fast voll ist, wenn die Unterhaltung komprimiert wurde, wenn eine Sitzung beendet ist und wenn eine sich länger nicht meldet
- Lässt mehrere Sitzungen gleichzeitig mit einem einzigen Bot laufen; optionaler Teambetrieb mit Rollen und Regeln pro Tool ([Teambetrieb und Gruppen](groups.md))
- Spricht English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی und Tiếng Việt: nur die Texte von aipager selbst, nie deine Sitzungen oder die Antworten von Claude ([Sprachen](commands.md#languages)). Dieses README und die Dokumentation sind auch übersetzt (siehe **In einer anderen Sprache lesen** oben)

### <a id="mini-app"></a>Mini App

`/app` öffnet ein Dashboard in Telegram: deine Sitzungen mit Stoppen, Beenden, Neu starten, Umbenennen und dem Moduswechsel, eine Diff-Ansicht für Änderungen durch `Write`/`Edit`, die Einstellungen und, für den Besitzer, Problem melden. Sie wird von aipager selbst bereitgestellt und ist **standardmäßig an**; jede Anfrage wird mit der `initData`-Signatur von Telegram geprüft. Wenn du in mehr als einem Chat bist (dein privater Chat und eine Gruppe), wechselt eine Zeile mit Chatnamen oben zwischen ihnen. Verwalte sie mit `aipager miniapp enable|disable|status`, oder lass sie statt auf den verwalteten Tunnel auf deine eigene Adresse zeigen: siehe [Sicherheitsmodell](security.md#mini-app-tunnel).

### <a id="note-on-model-buttons-bedrock--vertex-users"></a>Hinweis zu den Modell-Buttons (für Bedrock und Vertex)

Das Untermenü **Modell** der festen Tastatur (und die Modellauswahl der Mini App) bietet die Familien-Aliase von Claude Code an, `sonnet`, `opus`, `haiku`, `fable` und `opusplan`, und feste Modelle wie `claude-opus-5-5` und `claude-sonnet-5-5` (die vollständige Liste steht unter [Feste Tastatur](commands.md#persistent-keyboard)). Über die API von Anthropic zeigt ein Alias immer auf das neueste Modell seiner Familie. Auf **Bedrock** und **Vertex** können dieselben Aliase je nach den Versionen deines Anbieters auf ältere Stände zeigen. Wenn du diese Dienste nutzt und ein bestimmtes Modell willst, schick `/model <full-id>` aus dem Chat.

## <a id="developing-locally"></a>Auf deinem Computer entwickeln

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach` kommt mit der Abhängigkeit `dtach-bin`, mehr braucht es nicht. Führ die Tests wie oben mit einer Speichergrenze aus (auf macOS ohne `systemd-run`): Sonst kann ein außer Kontrolle geratener Test den ganzen Speicher des Computers belegen.

Wie Versionen veröffentlicht werden, steht in [CONTRIBUTING.md](../../../CONTRIBUTING.md).

## <a id="license"></a>Lizenz

MIT; den vollständigen Text findest du in [LICENSE](../../../LICENSE).

## <a id="docs"></a>Dokumentation

| Seite | Worum es geht |
|---|---|
| [Mit einem Agenten installieren](install-with-an-agent.md) | Die Schritte, denen ein Coding-Agent folgt, um aipager für seine Person zu installieren und einzurichten |
| [Architektur](architecture.md) | Wie das Programm funktioniert, Diagramm der Teile, wo die Dateien und Sockets liegen |
| [Hook-Ereignisse](hooks.md) | Jedes Ereignis, das aipager von Claude Code bekommt, mit seinen Feldern |
| [Befehle des Bots](commands.md) | Befehle, Buttons, die Mini App, Sprachnachrichten und Dateien, alle `aipager`-Befehle und die Einstellungen aus der Umgebung |
| [Teambetrieb und Gruppen](groups.md) | Einrichtung für mehrere Personen, Rollen, Regeln, Nachweis, wer was getan hat |
| [Fehlerbehebung](troubleshooting.md) | Häufige Fehler und die Lösungstabelle von `aipager doctor` |
| [Problemberichte](problem-reports.md) | Was ein Problembericht enthält, wohin er geht und wann aipager ihn anbietet |
| [Sicherheitsmodell](security.md) | Vertrauensgrenze, Geheimnisse, Audit-Log, Netzwerkverbindungen |
