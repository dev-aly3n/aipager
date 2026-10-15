# aipager

<!-- i18n-source: README.md @ a175bc5 -->

> Cette page est une traduction de la page anglaise ; si les deux ne disent pas la même chose, c'est la version anglaise qui fait foi. ([English](../../../README.md))

[![PyPI](https://img.shields.io/pypi/v/aipager?label=pypi&color=3775A9)](https://pypi.org/project/aipager/)
[![Python](https://img.shields.io/pypi/pyversions/aipager?color=3775A9)](https://pypi.org/project/aipager/)
[![License](https://img.shields.io/badge/license-MIT-black)](../../../LICENSE)

La télécommande Telegram de vos sessions [Claude Code](https://claude.com/claude-code). Claude tourne sur votre ordinateur dans un terminal détaché (`dtach`), et vous l'utilisez depuis votre téléphone : vous lisez les réponses de Claude, vous lui envoyez des messages, vous répondez à ses demandes d'autorisation et à ses questions, et vous passez d'une session à l'autre, sans garder de connexion SSH ouverte.

[aipager.run](https://aipager.run) · [Documentation](#docs) · [Historique des versions](../../../CHANGELOG.md) · [Issues sur GitHub](https://github.com/dev-aly3n/aipager/issues)

**Lire dans une autre langue :** [English](../../../README.md) · [Español](../es/README.md) · [Русский](../ru/README.md) · [Português (Brasil)](../pt-BR/README.md) · [简体中文](../zh-Hans/README.md) · [Deutsch](../de/README.md) · [한국어](../ko/README.md) · [العربية](../ar/README.md) · [Bahasa Indonesia](../id/README.md) · [Українська](../uk/README.md) · [Italiano](../it/README.md) · [Türkçe](../tr/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

## <a id="install"></a>Installation

Vous avez un agent de programmation (Claude Code, ou n'importe quel agent qui a un shell) ? Créez un bot avec [@BotFather](https://t.me/BotFather), appuyez sur Start dans ce bot, et vérifiez que Claude Code est connecté à votre compte sur cet ordinateur. Enregistrez le token du bot dans un fichier que votre agent n'aura jamais besoin de lire (collez le token, appuyez sur Entrée, puis sur Ctrl-D) :

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

Puis donnez ce message à votre agent :

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

Si vous ne connaissez pas votre identifiant d'utilisateur Telegram, dites-le à l'agent ; il sait le trouver.

Le guide que suit l'agent, y compris la façon de lui confier le token en toute sécurité, est [Installer avec un agent](install-with-an-agent.md). Pour installer vous-même, lisez la suite.

Linux ou macOS, toutes architectures. `dtach` s'installe tout seul grâce à la dépendance [`dtach-bin`](https://pypi.org/project/dtach-bin/) : aucun paquet système à part n'est nécessaire.

### <a id="one-line-install-recommended"></a>Installation en une ligne (recommandée)

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

Elle utilise `uv` s'il est là, sinon `pipx`, sinon (sur macOS seulement) Homebrew. Si elle n'en trouve aucun, elle installe d'abord `uv` (le gestionnaire d'outils Python d'Astral) et installe aipager avec.

### <a id="uv-recommended-on-macos"></a>uv (recommandé sur macOS)

```sh
uv tool install aipager     # si uv est déjà installé
```

Ou, pour installer d'abord uv :

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install aipager
```

uv apporte son propre interpréteur Python, donc cela marche sur n'importe quelle version de macOS ou de Linux, quel que soit l'état du Python du système ; vous évitez ainsi le conflit entre le Python de Homebrew et Xcode décrit dans la section Homebrew plus bas.

### <a id="pipx"></a>pipx

```sh
pipx install aipager
```

### <a id="homebrew-tap-macos-linuxbrew"></a>Homebrew tap (macOS, Linuxbrew)

> **Remarque :** sur macOS, la voie recommandée est `uv tool install aipager`. La formule brew marche quand le paquet `python@3.12` de Homebrew et vos Xcode / Command Line Tools sont accordés, mais ils se désaccordent de temps en temps ; la dernière fois, c'était sur **macOS Tahoe (26.x)**, où l'installation échoue avec l'erreur de symbole `pyexpat _XML_SetAllocTrackerActivationThreshold` ([issue en amont](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). Mettre à jour Xcode et les Command Line Tools règle en général le problème, mais il est plus simple d'utiliser uv.

```sh
brew install dev-aly3n/tap/aipager
```

Prend `dtach` dans la formule standard de Homebrew et installe aipager dans un venv Python géré par Homebrew.

### <a id="docker"></a>Docker

Une image complète avec python, node, `claude` et `dtach` déjà dedans ; pratique pour un VPS, un NAS ou un Raspberry Pi où vous ne voulez ni Python ni Node sur le système. Multi-architecture (amd64, arm64).

```sh
# 1. Lancez une fois l'assistant de configuration (interactif)
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. Démarrez aipager (en arrière-plan, redémarrage automatique)
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

Montez sous `/workspace` les dossiers que Claude doit pouvoir modifier. Le montage de `~/.claude` apporte vos identifiants Claude et l'historique de vos conversations ; pour vous connecter, lancez `claude` une fois sur le système, ou `docker exec -it aipager claude` pour vous connecter dans le conteneur.

Tags : `latest`, `1.3`, `1.3.0` (une par version exacte et une par version mineure).

### <a id="nix-flake"></a>Nix flake

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

Compile aipager depuis les sources avec les dépendances figées de nixpkgs. `dtach` est fourni par Nix ; `claude` **ne l'est pas** : installez-le à part (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`, ou en suivant la documentation d'Anthropic).

Pour une configuration déclarative NixOS ou Home Manager, ajoutez aipager comme entrée du flake et prenez son paquet dans `environment.systemPackages` :

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

Ensuite, `aipager service install` crée un service utilisateur systemd.

### <a id="snap"></a>Snap

```sh
snap install aipager
```

Un snap en confinement strict qui contient python, node, `claude`, `dtach` et aipager. À cause du bac à sable des snaps, les dossiers de travail doivent se trouver sous `~/` (par exemple `~/projects/foo`). Le manifeste est dans [`packaging/snap/`](../../../packaging/snap/).

## <a id="configure"></a>Configuration

```sh
aipager config
```

Un assistant interactif : il demande le token de votre bot Telegram (donné par [@BotFather](https://t.me/BotFather)) et l'identifiant de la discussion, vérifie les deux, puis ajoute tout seul les hooks nécessaires dans `~/.claude/settings.json`. Vous ne modifiez jamais aucun fichier à la main.

Vous configurez aipager pour quelqu'un depuis un script ou un agent de programmation ? `aipager setup --token-file FILE --chat-id ID` fait la même chose sans poser de questions (voir [la commande setup](commands.md#command-line-aipager-setup-for-coding-agents) et [le guide pour les agents](install-with-an-agent.md)).

## <a id="run"></a>Lancer

```sh
aipager start
```

aipager reste au premier plan. Lancez une session Claude dans un autre terminal :

```sh
aipager session dev
```

Cela crée une session dtach nommée `claude-dev` qui fait tourner Claude Code (ou s'y reconnecte si elle existe déjà). aipager la trouve en quelques secondes et Telegram commence à l'afficher. Vous pouvez aussi lancer une session depuis Telegram : envoyez `/new dev` au bot. Relancez la même commande pour vous reconnecter plus tard ; pour partir sans arrêter Claude, fermez le terminal ou le panneau tmux (la session continue de tourner).

Si la session dtach a été fermée (l'ordinateur a redémarré, par exemple) mais que vous voulez reprendre la conversation de Claude depuis le disque, ajoutez `--continue` :

```sh
aipager session dev --continue    # la dernière conversation claude dans ce dossier
```

Vous pouvez aussi passer `--resume <session-id>` (ou n'importe quelle autre option de claude) à la fin :

```sh
aipager session dev -- --resume abc1234
```

### <a id="run-as-a-service-survives-logout"></a>Lancer comme service (continue après la déconnexion)

```sh
aipager service install
```

Sur Linux, cela écrit un service utilisateur systemd dans `~/.config/systemd/user/aipager.service` et le démarre. Sur macOS, cela écrit un plist launchd dans `~/Library/LaunchAgents/com.aipager.daemon.plist` et le met en route. Sous-commandes : `start`, `stop`, `status`, `logs`, `uninstall`.

## <a id="team-mode-groups-and-other-people"></a>Usage en équipe (groupes et autres personnes)

Par défaut, aipager est un bot privé : vous et le bot dans une discussion privée. Pour le partager, ajoutez un groupe Telegram, les discussions privées d'autres personnes avec le bot, ou les deux. Pour un groupe : ajoutez le bot au groupe (comme admin, pour qu'il puisse épingler la barre d'état du groupe), relancez `aipager config` et choisissez **Add a group scope**. L'assistant vous propose (vous, le propriétaire de votre propre discussion privée) comme propriétaire du groupe, ajoute chaque membre avec un rôle (`owner`, `admin`, `user` ou `read_only`), demande le dossier racine des membres et recharge la configuration d'aipager sans redémarrage. Dans le groupe, le bot ne réagit qu'aux commandes, aux réponses à ses messages et aux mentions (`@aipagerbot fix the tests`, `/jim run the tests`), et chaque message s'exécute avec le rôle de la personne qui l'a envoyé. Un membre `user` ou `read_only` ne crée et ne garde des sessions que dans le dossier racine de sa discussion (par défaut `~/aipager-users/<label>`). **Ajouter un membre lui donne le droit d'exécuter du code sur votre ordinateur** : le modèle de confiance complet est dans [Usage en équipe et groupes](groups.md).

## <a id="what-it-does"></a>Ce qu'il fait

- Montre dans Telegram ce que fait chaque session Claude Code : au travail ou prête, les outils qu'elle utilise, le % de contexte, le coût et les lignes modifiées, sur une carte par tâche, puis la réponse de Claude
- Envoie vos messages à Claude **tout de suite**, même pendant qu'il travaille : envoyez-en plusieurs et Claude les met lui-même en file d'attente, exactement comme si vous tapiez dans le terminal. 👀 veut dire envoyé, 👍 veut dire que Claude l'a pris
- Retient un message tant qu'une demande d'autorisation ou une question est ouverte, pour qu'il ne soit jamais lu comme la réponse, et l'envoie une fois que vous avez répondu
- Affiche les demandes d'autorisation et les questions de Claude avec des boutons (Autoriser, Refuser, Toujours autoriser, un par option) ; un bouton resté d'une tâche déjà finie refuse au lieu d'agir sur le travail en cours
- Trois modes d'autorisation par session, changés avec `/mode` : 💬 Mode Demander (Claude demande avant chaque action), 🤖 Mode Automatique, le mode par défaut (la vérification de sécurité de Claude Code lui-même laisse passer les actions sûres et arrête les risquées pour que vous décidiez) et 🔓 Mode Accès complet (tout s'exécute sans aucune vérification ; pour les admins, et pour vous dans votre propre discussion privée)
- Lance des sessions depuis la discussion : `/new x1` en lance une tout de suite (`/new x1 fix the tests` envoie aussi son premier message), avec le mode, le modèle et le dossier réglés dans `/settings`, et des boutons pour les changer
- Accepte les photos, les fichiers et les messages vocaux (un message vocal est transformé en texte sur votre ordinateur, avec le paquet vocal optionnel)
- Vous prévient quand le contexte est presque plein, quand la conversation a été compactée, quand une session s'est terminée et quand une autre ne donne plus de nouvelles
- Fait tourner plusieurs sessions à la fois avec un seul bot ; usage en équipe optionnel, avec des rôles et des règles par outil ([Usage en équipe et groupes](groups.md))
- Parle English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی et Tiếng Việt : seulement les textes d'aipager, jamais vos sessions ni les réponses de Claude ([Langues](commands.md#languages)). Ce README et la documentation sont aussi traduits (voir **Lire dans une autre langue** plus haut)

### <a id="mini-app"></a>Mini App

`/app` ouvre un tableau de bord dans Telegram : vos sessions avec Arrêter, Terminer, Redémarrer, Renommer et le changement de mode, une visionneuse de diff pour les modifications de `Write`/`Edit`, les paramètres et, pour le propriétaire, Signaler un problème. Elle est servie par aipager lui-même et elle est **activée par défaut** ; chaque demande est vérifiée avec la signature `initData` de Telegram. Si vous êtes dans plus d'une discussion (votre discussion privée et un groupe), une ligne de noms de discussions en haut permet de passer de l'une à l'autre. Gérez-la avec `aipager miniapp enable|disable|status`, ou faites-la pointer vers votre propre adresse au lieu du tunnel géré : voir [Modèle de sécurité](security.md#mini-app-tunnel).

### <a id="note-on-model-buttons-bedrock--vertex-users"></a>À propos des boutons de modèle (utilisateurs de Bedrock et Vertex)

Le sous-menu **Modèle IA** du clavier fixe (et les sélecteurs de modèle de la Mini App) propose les alias de famille de Claude Code, `sonnet`, `opus`, `haiku`, `fable` et `opusplan`, et des modèles fixes comme `claude-opus-5-5` et `claude-sonnet-5-5` (la liste complète est dans [Clavier fixe](commands.md#persistent-keyboard)). Sur l'API d'Anthropic, un alias désigne toujours le modèle le plus récent de sa famille. Sur **Bedrock** et **Vertex**, ces mêmes alias peuvent désigner des versions plus anciennes, selon ce que propose votre fournisseur. Si vous utilisez ces services et voulez un modèle précis, envoyez `/model <full-id>` depuis la discussion.

## <a id="developing-locally"></a>Développer sur votre ordinateur

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach` vient avec la dépendance `dtach-bin`, il n'y a donc rien d'autre à installer. Lancez les tests avec une limite de mémoire comme ci-dessus (sur macOS, sans `systemd-run`) : sinon, un test qui s'emballe peut prendre toute la mémoire de l'ordinateur.

La procédure de publication des versions est dans [CONTRIBUTING.md](../../../CONTRIBUTING.md).

## <a id="license"></a>Licence

MIT ; le texte complet est dans [LICENSE](../../../LICENSE).

## <a id="docs"></a>Documentation

| Page | Sujet |
|---|---|
| [Installer avec un agent](install-with-an-agent.md) | Les étapes qu'un agent de programmation suit pour installer et configurer aipager pour la personne qu'il aide |
| [Architecture](architecture.md) | Comment le programme fonctionne, schéma des parties, où se trouvent les fichiers et les sockets |
| [Événements de hook](hooks.md) | Chaque événement qu'aipager reçoit de Claude Code, avec ses champs |
| [Commandes du bot](commands.md) | Commandes, boutons, la Mini App, messages vocaux et fichiers, toutes les commandes `aipager` et les paramètres lus dans l'environnement |
| [Usage en équipe et groupes](groups.md) | Configuration pour plusieurs personnes, rôles, règles, trace de qui a fait quoi |
| [Dépannage](troubleshooting.md) | Pannes courantes et la table des solutions d'`aipager doctor` |
| [Rapports de problèmes](problem-reports.md) | Ce que contient un rapport de problème, où il va et quand aipager le propose |
| [Modèle de sécurité](security.md) | Limite de confiance, secrets, journal d'audit, connexions réseau |
