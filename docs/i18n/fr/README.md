<div align="center">

<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="96" height="96" alt="aipager"></a>

# aipager

<!-- i18n-source: README.md @ bd8a636 -->

<h3>Claude, c'est une discussion de plus.</h3>

Vos sessions [Claude Code](https://claude.com/claude-code) dans Telegram, à côté de votre famille, de vos amis et de votre équipe.<br>
Elles tournent sur votre propre ordinateur. Vous les suivez, leur répondez et leur confiez la suite, où que vous soyez.

<p>
<a href="https://pypi.org/project/aipager/"><img alt="PyPI" src="https://img.shields.io/pypi/v/aipager?style=flat-square&label=pypi&labelColor=181c20&color=2aabee"></a>
<a href="https://pypi.org/project/aipager/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/aipager?style=flat-square&labelColor=181c20&color=2aabee"></a>
<a href="https://github.com/dev-aly3n/aipager/actions/workflows/test.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/dev-aly3n/aipager/test.yml?branch=main&style=flat-square&label=tests&labelColor=181c20"></a>
<img alt="Linux and macOS" src="https://img.shields.io/badge/runs%20on-Linux%20%7C%20macOS-2aabee?style=flat-square&labelColor=181c20">
<a href="../../../LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-2aabee?style=flat-square&labelColor=181c20"></a>
</p>

**[Site web](https://aipager.run)** · **[Installation](#install)** · **[Documentation](#documentation)** · **[Sécurité](security.md)** · **[Journal des modifications](../../../CHANGELOG.md)**

**Lire dans une autre langue :** [English](../../../README.md) · [Español](../es/README.md) · [Русский](../ru/README.md) · [Português (Brasil)](../pt-BR/README.md) · [简体中文](../zh-Hans/README.md) · [Deutsch](../de/README.md) · [한국어](../ko/README.md) · [العربية](../ar/README.md) · [Bahasa Indonesia](../id/README.md) · [Українська](../uk/README.md) · [Italiano](../it/README.md) · [Türkçe](../tr/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

<sub>Cette page est une traduction de la page anglaise ; si les deux ne disent pas la même chose, c'est la version anglaise qui fait foi. ([English](../../../README.md))</sub>

<br>

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/hero.webp" width="100%" alt="Un message tapé dans Telegram arrive à Claude Code dans un terminal, et une carte de la discussion se met à jour pendant que Claude travaille">

<sub>Vous écrivez dans Telegram. aipager le tape dans Claude Code sur votre ordinateur, et une carte se met à jour au fil de la tâche.</sub>

</div>

<br>

## <a id="why-aipager"></a>Pourquoi aipager

<table>
<tr>
<td width="33%" valign="top">

**📟 Une carte en direct par tâche**<br>
Ce que Claude fait en ce moment : les outils qu'il lance, le contexte, le coût et les lignes modifiées, puis la réponse. La carte se met à jour toute seule, la discussion reste calme.

</td>
<td width="33%" valign="top">

**⚡ Parlez-lui en pleine tâche**<br>
Votre message est tapé dans la session tout de suite, même pendant que Claude travaille, exactement comme dans le terminal. 👀 veut dire envoyé, 👍 veut dire que Claude l'a pris.

</td>
<td width="33%" valign="top">

**✅ Répondez d'un appui**<br>
Les demandes d'autorisation et les questions de Claude arrivent avec un bouton par réponse. Un bouton resté d'une tâche finie refuse au lieu d'agir sur la suivante.

</td>
</tr>
<tr>
<td width="33%" valign="top">

**🛡️ Trois modes d'autorisation**<br>
💬 Mode Demander, 🤖 Mode Automatique (le mode par défaut des nouvelles sessions) et 🔓 Mode Accès complet, réglés par session et changés à tout moment avec `/mode`.

</td>
<td width="33%" valign="top">

**👥 Pensé pour les équipes**<br>
Mettez le bot dans un groupe. Chaque message est traité avec le rôle de la personne qui l'envoie, les membres peuvent être limités à leur propre dossier, et chaque appui est inscrit au journal d'audit.

</td>
<td width="33%" valign="top">

**🔒 Rien au milieu**<br>
Pas de serveur aipager, pas de compte, pas de port ouvert. Votre token de bot, votre ordinateur, vos discussions.

</td>
</tr>
</table>

## <a id="a-quick-tour"></a>Petite visite

### <a id="questions-come-to-you"></a>Les questions viennent à vous

Quand Claude vous demande quelque chose ou a besoin d'une autorisation, la question arrive dans votre discussion avec un bouton par réponse, et la barre épinglée dit ce qui vous attend. Votre appui est tapé dans la session comme si vous étiez au clavier, et le terminal affiche la même réponse.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/question.webp" width="100%" alt="Claude demande s'il faut allonger le délai ou attendre le cookie ; la réponse choisie dans Telegram est sélectionnée dans le terminal">

### <a id="three-permission-modes"></a>Trois modes d'autorisation

Chaque session est dans l'un des trois modes, changé à tout moment avec `/mode`, même pendant que Claude travaille.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/modes.webp" width="100%" alt="La même tâche en Mode Demander, Mode Automatique et Mode Accès complet">

| Mode | Ce qui se passe |
|---|---|
| 💬 **Mode Demander**<br>`/mode ask` | Claude demande avant chaque action. |
| 🤖 **Mode Automatique**, le mode par défaut<br>`/mode auto` | La vérification de sécurité de Claude Code lui-même laisse passer les actions sûres et arrête les risquées pour que vous décidiez. |
| 🔓 **Mode Accès complet**<br>`/mode full` | Tout s'exécute sans aucune vérification. Pour les admins, et pour vous dans votre propre discussion privée avec le bot. |

Entre le Mode Demander et le Mode Automatique, le changement se fait sur place, sans redémarrage. Passer en Mode Accès complet ou en sortir redémarre la session et garde la conversation. Les questions de Claude vous arrivent dans tous les modes.

### <a id="your-team-in-one-group"></a>Votre équipe, dans un groupe

Ajoutez le bot à un groupe et donnez un rôle à chaque membre : `owner`, `admin`, `user` ou `read_only`. Dans le groupe, le bot ne réagit qu'aux commandes, aux réponses à ses messages et aux mentions (`@aipagerbot fix the tests`, `/web run the tests`), et chaque message est traité avec le rôle de la personne qui l'envoie : un outil que le rôle n'a pas le droit d'utiliser est refusé avant de s'exécuter. Un membre `user` ou `read_only` ne travaille que dans son propre dossier racine (`~/aipager-users/<label>` par défaut). D'autres personnes peuvent aussi avoir leur propre discussion privée avec le bot.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/team.webp" width="100%" alt="Un groupe d'équipe où une commande est refusée à cause du rôle d'un membre pendant que les tests passent">

> **⚠️ Important :** Ajouter un membre lui donne le droit d'exécuter du code sur votre ordinateur. Lisez [le modèle de confiance](groups.md) avant de partager le bot.

Pour le mettre en place, ajoutez le bot au groupe comme admin (pour qu'il puisse épingler la barre d'état du groupe), relancez `aipager config` et choisissez **Add a group scope**. L'assistant ajoute chaque membre avec un rôle, demande le dossier racine des membres et recharge la configuration de l'aipager en cours d'exécution.

### <a id="a-dashboard-inside-telegram"></a>Un tableau de bord dans Telegram

`/app` ouvre la Mini App : vos sessions avec Arrêter, Terminer, Redémarrer, Renommer et le changement de mode, une vue diff pour chaque `Write` et `Edit`, les réglages et, pour le propriétaire, « Signaler un problème ». aipager la sert lui-même, elle est activée par défaut, et chaque requête est vérifiée avec la signature de Telegram. Si vous êtes dans plus d'une discussion, une rangée de noms en haut permet de passer de l'une à l'autre. Coupez-la avec `aipager miniapp disable`, ou faites-la pointer vers votre propre adresse ([comment](security.md#mini-app-tunnel)).

### <a id="fifteen-languages"></a>Quinze langues

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/languages.webp" width="420" align="right" alt="Une demande d'autorisation en persan, de droite à gauche, avec des chiffres persans">

- Parle English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی et Tiếng Việt.
- Choisissez-en une avec `/language`, ou laissez une discussion privée suivre la langue de votre app Telegram.
- Le persan et l'arabe se lisent de droite à gauche, avec leurs propres chiffres.
- Seuls les textes d'aipager changent. Vos sessions, non : Claude répond dans la langue où vous lui écrivez.
- Ce README et [la documentation](#documentation) sont traduits aussi.

<br clear="right">

### <a id="and-the-small-things"></a>Et les petites choses

- **Plusieurs sessions, un seul bot.** Un message ordinaire va à la session avec laquelle vous avez parlé en dernier ; répondez à un message d'une session pour parler à celle-là, ou envoyez `/x1 <message>`.
- **Rien ne se mélange.** Un message envoyé pendant qu'une demande d'autorisation ou une question est ouverte attend votre réponse, pour ne jamais être lu comme la réponse.
- **Photos, fichiers et messages vocaux.** Les messages vocaux sont transformés en texte sur votre ordinateur, avec l'extra optionnel `voice`.
- **Il vous prévient.** Contexte presque plein, conversation compactée, session terminée et session qui ne donne plus de nouvelles.
- **Les sessions survivent à tout.** Claude tourne dans `dtach` : il continue à travailler quand vous fermez le terminal, perdez la connexion SSH ou redémarrez aipager.

## <a id="how-it-works"></a>Comment ça marche

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/how-it-works.webp" width="100%" alt="Claude Code, son hook, aipager, Telegram et vos discussions, à la suite">

Claude Code tourne dans un vrai terminal, dans `dtach`, pour que personne n'ait à garder une fenêtre ouverte. Chaque étape déclenche un [hook](hooks.md) qui envoie un datagramme à aipager par un socket local, et aipager modifie un message dans votre discussion. Vos réponses reviennent par le même chemin : une décision au hook qui attend l'appel, ou des frappes de touches dans la session. Le terminal reste la source de vérité. Plus de détails dans [l'architecture](architecture.md).

## <a id="install"></a>Installation

Linux ou macOS, toute architecture. Il vous faut Claude Code, connecté sur cet ordinateur, et un bot Telegram : créez-en un avec [@BotFather](https://t.me/BotFather) et appuyez sur Démarrer dans sa discussion.

```sh
curl -fsSL https://aipager.run/install | sh   # uv, pipx ou Homebrew : celui que vous avez
aipager config                                # votre token de bot, votre discussion, les hooks de Claude Code
aipager service install                       # garde aipager en marche après la déconnexion
aipager session api                           # Claude Code dans ce dossier, et dans votre discussion
```

L'installateur prend `uv` si vous l'avez, sinon `pipx`, sinon (sur macOS) Homebrew, et installe `uv` quand il n'y a rien. `dtach` est fourni avec aipager (le paquet [`dtach-bin`](https://pypi.org/project/dtach-bin/)), il n'y a donc rien d'autre à installer. `aipager config` est un assistant : il vérifie le token du bot et votre discussion, puis ajoute pour vous les hooks d'aipager à `~/.claude/settings.json`. Vous ne modifiez jamais un fichier à la main.

<details>
<summary><b>Laissez un agent de programmation l'installer</b></summary>

<br>

Vous avez Claude Code ou un autre agent avec un shell ? Créez le bot, appuyez sur Démarrer dans sa discussion, et enregistrez le token du bot dans un fichier que votre agent n'aura jamais à lire (collez le token, appuyez sur Entrée, puis Ctrl-D) :

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

Puis donnez ce prompt à votre agent :

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

Si vous ne connaissez pas votre identifiant Telegram, dites-le plutôt ; l'agent peut le trouver. Le guide qu'il suit, y compris la façon de remettre le token en sécurité, est [Installer avec un agent](install-with-an-agent.md). Pour les scripts, `aipager setup --token-file FILE --chat-id ID` configure aipager sans poser de question ([la commande setup](commands.md#command-line-aipager-setup-for-coding-agents)).

</details>

### <a id="other-ways-to-install"></a>Autres façons d'installer

```sh
uv tool install aipager                        # uv : conseillé sur macOS, il apporte son propre Python
pipx install aipager                           # pipx
brew install dev-aly3n/tap/aipager             # Homebrew, sur macOS et Linuxbrew (voir la note ci-dessous)
nix profile install github:dev-aly3n/aipager   # Nix : claude n'est pas inclus (voir plus bas)
snap install aipager                           # Snap : confinement strict, claude inclus, dossiers de travail sous ~/
```

Ou faites-le tourner dans [Docker](#docker), avec Python, Node, `claude` et `dtach` à l'intérieur.

<details>
<summary><b>Homebrew sur macOS 26 (Tahoe)</b></summary>

<br>

La formule fonctionne quand la bottle `python@3.12` de Homebrew et vos Command Line Tools de Xcode sont en phase, mais elles se décalent de temps en temps. Sur macOS 26, l'installation échoue avec une erreur de symbole `pyexpat _XML_SetAllocTrackerActivationThreshold` ([issue en amont](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). Mettre à jour Xcode et les Command Line Tools règle en général le problème, mais avec `uv tool install aipager` il ne se pose pas du tout. La formule prend `dtach` dans Homebrew et installe aipager dans un environnement Python géré par Homebrew.

</details>

### <a id="docker"></a>Docker

Une image autonome avec Python, Node, `claude` et `dtach`, idéale pour un VPS, un NAS ou un Raspberry Pi où vous ne voulez ni Python ni Node sur l'hôte. Multi-architecture (amd64, arm64).

```sh
# 1. Lancer l'assistant de configuration une fois (interactif)
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. Démarrer aipager (en arrière-plan, redémarrage automatique)
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

Montez sous `/workspace` les dossiers que Claude doit modifier. Le montage de `~/.claude` apporte vos identifiants Claude et l'historique de vos conversations : lancez `claude` une fois sur l'hôte pour vous connecter, ou `docker exec -it aipager claude` pour vous connecter dans le conteneur.

Tags : `latest`, `1.4`, `1.4.0` (un par version exacte et un par version mineure).

### <a id="nix"></a>Nix

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

Compile aipager depuis les sources avec des nixpkgs épinglés. `dtach` vient de Nix ; `claude` non, installez-le à part (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`, ou suivez la documentation d'Anthropic).

<details>
<summary><b>NixOS et Home Manager</b></summary>

<br>

Ajoutez aipager comme entrée du flake et mettez son paquet dans `environment.systemPackages` :

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

`aipager service install` crée ensuite une unité utilisateur systemd.

</details>

## <a id="everyday-use"></a>Au quotidien

### <a id="in-telegram"></a>Dans Telegram

| Envoyez | Pour |
|---|---|
| `/status` | Voir les sessions de cette discussion, chacune avec son état et ses boutons. |
| `/new x1` | Lancer une session nommée x1. `/new x1 fix the tests` envoie aussi son premier message. |
| `/x1` | Faire de x1 la session où vont vos messages. `/x1 <message>` lui envoie un message directement. |
| `/stop` | Interrompre la tâche en cours et jeter ce qui est en file d'attente. |
| `/mode` | Passer d'un mode à l'autre : Mode Demander, Mode Automatique, Mode Accès complet. |
| `/app` | Ouvrir la Mini App. |
| `/language` | Choisir la langue qu'aipager parle dans cette discussion. |
| `/update` | Mettre à jour aipager et Claude Code, puis redémarrer sur place (admins). |
| `/help` | Un petit guide, organisé par besoin. |

Chaque commande, bouton et réglage est dans [Commandes du bot](commands.md).

### <a id="on-the-command-line"></a>En ligne de commande

```sh
aipager start                             # aipager lui-même, au premier plan
aipager session dev                       # lancer ici une session nommée dev, ou s'y rattacher
aipager session dev --continue            # reprendre la dernière conversation Claude de ce dossier
aipager session dev -- --resume abc1234   # toute autre option de claude passe telle quelle
aipager doctor                            # vérifier l'installation
```

Pour quitter une session sans arrêter Claude, fermez le terminal ou le panneau tmux : la session continue, et la même commande vous y rattache.

`aipager service install` garde aipager en marche en arrière-plan : une unité utilisateur systemd sur Linux (`~/.config/systemd/user/aipager.service`) ou un agent launchd sur macOS (`~/Library/LaunchAgents/com.aipager.daemon.plist`). Dans les deux cas, aipager tourne dans votre dossier personnel, où démarre une nouvelle session sauf si vous choisissez un autre dossier. `aipager service start`, `stop`, `status`, `logs` et `uninstall` le gèrent.

## <a id="security"></a>Sécurité

- **Pas de serveur aipager.** aipager parle à la Bot API de Telegram avec votre propre token de bot. Il n'y a ni compte aipager ni service aipager.
- **Pas de port ouvert.** aipager n'écoute que sur la boucle locale. La Mini App sort par un tunnel Cloudflare où chaque requête doit porter la signature de Telegram, et `aipager miniapp disable` la coupe.
- **Chaque réponse est consignée.** Chaque Autoriser, Refuser et réponse est ajouté à `~/.claude/aipager-audit.jsonl`, avec qui a appuyé dans un groupe.
- **Claude Code décide toujours.** Vos réglages de Claude Code disent ce qui doit être demandé. aipager ne fait que transmettre la question et ne demande jamais plus de droits : pas de sudo, tout tourne sous votre utilisateur.

Le tableau complet, y compris ce qui protège votre identifiant Claude, est dans [le modèle de sécurité](security.md).

## <a id="faq"></a>Questions fréquentes

<details>
<summary><b>Quelle différence avec les apps mobiles de Claude ?</b></summary>

<br>

Les apps de Claude parlent à l'assistant hébergé par Anthropic. aipager vous relie à une session Claude Code qui tourne sur votre propre ordinateur : votre dépôt, vos outils, votre environnement.

</details>

<details>
<summary><b>Mon code quitte-t-il mon ordinateur ?</b></summary>

<br>

aipager n'envoie à Telegram que ce que vous voyez dans vos discussions, avec votre propre token de bot ; aucun serveur aipager entre les deux. Claude Code, lui, parle à Anthropic comme d'habitude.

</details>

<details>
<summary><b>Qu'est-ce que dtach ?</b></summary>

<br>

Un petit outil qui détache un terminal. Il garde Claude Code en vie quand le terminal se ferme ou que SSH coupe, si bien qu'une réponse envoyée une heure plus tard atteint encore une session vivante. Il est fourni avec aipager.

</details>

<details>
<summary><b>Et si Claude part dans le décor ?</b></summary>

<br>

Les demandes d'autorisation attendent votre appui, `/stop` interrompt la tâche et jette tout ce qui attend, `/kill` termine la session, et les boutons d'une tâche finie refusent.

</details>

<details>
<summary><b>Comment le mettre à jour ?</b></summary>

<br>

Envoyez `/update` : il vérifie aipager et Claude Code et installe ce qui est nouveau, puis 🔄 Redémarrer maintenant relance aipager, en service ou non. Vos sessions continuent pendant ce temps. En ligne de commande, `aipager update` met à jour avec l'installateur auquel appartient aipager.

</details>

<details>
<summary><b>J'utilise Bedrock ou Vertex. Les boutons de modèle marchent-ils ?</b></summary>

<br>

Le menu **Modèle IA ›** du clavier et la Mini App proposent les alias de famille de Claude Code (`sonnet`, `opus`, `haiku`, `fable`, `opusplan`) et des modèles fixes comme `claude-opus-5-5` et `claude-sonnet-5-5`. Sur l'API d'Anthropic, un alias pointe vers le dernier modèle de sa famille. Sur Bedrock et Vertex, le même alias peut pointer vers une version plus ancienne, selon ce que propose votre fournisseur ; envoyez `/model <full-id>` pour choisir un modèle précis.

</details>

<details>
<summary><b>J'utilise une clé d'API, une passerelle ou un autre fournisseur. aipager fonctionne-t-il ?</b></summary>

<br>

Oui. aipager lance Claude Code tel que vous l'avez configuré, et répond aux questions que Claude Code pose au démarrage (le dossier, la clé d'API, les écrans du premier lancement), pour qu'une session démarrée depuis Telegram ne s'y arrête pas. Mettez les réglages dans le bloc `env` de `~/.claude/settings.json`, ou dans `~/.config/aipager/daemon.env` (une ligne `KEY=value` par réglage, sans guillemets). Les boutons Opus, Sonnet et Haiku suivent `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL` et `ANTHROPIC_DEFAULT_HAIKU_MODEL`, et tout autre modèle peut être saisi par son nom. Anthropic ne prend en charge Claude Code qu'avec les modèles Claude. Voir [Dépannage](troubleshooting.md#using-an-api-key-a-gateway-or-another-provider).

</details>

<details>
<summary><b>Combien ça coûte ?</b></summary>

<br>

Rien. aipager est sous licence MIT, le bot est à vous, et Claude Code continue d'utiliser l'offre ou la clé qu'il utilise déjà.

</details>

## <a id="documentation"></a>Documentation

| Page | Sujet |
|---|---|
| [Commandes du bot](commands.md) | Chaque commande, bouton, réglage et option de la ligne de commande |
| [Usage en équipe et groupes](groups.md) | Rôles, dossiers racines, qui peut appuyer sur quoi, le modèle de confiance |
| [Modèle de sécurité](security.md) | Limite de confiance, secrets, journal d'audit, réseau |
| [Événements de hook](hooks.md) | Ce que fait chaque hook de Claude Code dans aipager |
| [Architecture](architecture.md) | Les parties d'aipager, ses fichiers et ses sockets |
| [Dépannage](troubleshooting.md) | `aipager doctor` et solutions aux problèmes courants |
| [Rapports de problèmes](problem-reports.md) | Ce que contient un rapport, et quand aipager le propose |
| [Installer avec un agent](install-with-an-agent.md) | Le guide que suit un agent de programmation |

## <a id="contributing"></a>Contribuer

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach` est fourni par la dépendance `dtach-bin`, il ne faut rien d'autre. Lancez les tests avec une limite de mémoire comme ci-dessus (sur macOS, sans `systemd-run`) : sinon un test qui s'emballe peut prendre toute la mémoire de l'ordinateur. Le processus de publication est décrit dans [CONTRIBUTING.md](../../../CONTRIBUTING.md).

## <a id="license"></a>Licence

MIT. Voir [LICENSE](../../../LICENSE).

<br>

<div align="center">
<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="32" height="32" alt=""></a>
<br>
<sub><a href="https://aipager.run">aipager.run</a></sub>
</div>
