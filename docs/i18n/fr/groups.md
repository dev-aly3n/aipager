# Usage en équipe et groupes

<!-- i18n-source: docs/groups.md @ 6fc084f -->

> Cette page est une traduction de la page anglaise ; si les deux ne disent pas la même chose, c'est la version anglaise qui fait foi. ([English](../../groups.md))

Par défaut, aipager est un bot privé : vous et le bot dans une discussion privée, personne d'autre. L'**usage en équipe** l'ouvre à plus de monde : un groupe Telegram où plusieurs personnes qui programment pilotent des sessions, les discussions privées d'autres personnes avec le bot, ou les deux. Chaque discussion a ses propres membres, et chaque membre a un rôle qui décide de ce qu'il peut y faire : envoyer des messages, répondre aux questions et aux demandes d'autorisation de Claude, gérer les sessions, ou seulement regarder.

L'usage en équipe est optionnel. Une installation avec seulement votre propre discussion privée marche comme avant.

## <a id="decide-carefully"></a>Décidez avec soin

Ajouter une personne de Telegram à une discussion lui donne **le droit d'exécuter du code sur l'ordinateur où tourne aipager**. Selon son rôle, elle peut :

- envoyer des messages que Claude transforme en commandes shell, en modifications de fichiers et en requêtes réseau ;
- répondre aux demandes d'autorisation (Autoriser / Refuser). Votre `~/.claude/settings.json` décide toujours des outils pour lesquels Claude demande, mais quiconque a un rôle qui peut approuver peut toucher Autoriser ;
- lancer, arrêter, terminer et changer de session.

Traitez la liste des membres comme vous traitez l'accès SSH à l'ordinateur. Le journal d'audit ([`~/.claude/aipager-audit.jsonl`](security.md#audit-log)) garde la trace de qui a fait quoi, mais seulement après coup.

## <a id="setup"></a>Configuration

Tout se configure avec `aipager config`. Vous ne modifiez jamais un fichier à la main.

**Premier lancement** (pas encore de configuration). L'assistant demande, en quatre étapes :

1. le token du bot ;
2. votre propre discussion privée avec le bot (détectée automatiquement, ou collée) ;
3. si votre compte est le `owner` (le choix par défaut ; répondez non et vous êtes `admin` à la place, ce qui garde le socle de sécurité actif pour vous) ;
4. il vérifie les dépendances et installe les hooks de Claude Code.

Il écrit votre discussion privée comme première discussion. Il ne demande pas de mode par défaut : son résumé dit "New sessions start in Auto mode; owners and admins can choose Full access in Telegram under /settings → New sessions." (les nouvelles sessions démarrent en Mode Automatique ; les propriétaires et les admins peuvent choisir l'Accès complet dans Telegram, dans /settings → Nouvelles sessions). Puis il demande "Connected. Add a team group or other people now?" avec Add a group, Add a person (DM) et I'm done.

**Les lancements suivants** ouvrent le menu de modification. Il montre vos discussions et propose :

- **Add a group scope** : un nouveau groupe, avec ses membres (plus bas).
- **Add a DM scope** : la propre discussion privée d'une autre personne avec le bot. Une discussion privée a un seul membre ; vous choisissez son rôle (par défaut `user` ; choisissez `owner` seulement pour une installation d'une seule personne, comme le conteneur personnel d'un ami). Pour un rôle sans `can_manage` (comme `user`), il demande ensuite son [dossier racine](#root-folders), prérempli avec la valeur par défaut (`~/aipager-users/<label>`). Pour une personne qui a déjà une discussion privée configurée, l'assistant le dit et ne change rien : utilisez Edit a member pour changer son rôle.
- **Edit a scope** : pour un groupe, **Add a member** ; pour n'importe quelle discussion, Rename, Edit scope deny_tools, **Set the members' root folder** (voir [Dossiers racines](#root-folders)) et Remove this scope (la dernière discussion ne peut pas être retirée). Terminez les sessions d'une discussion avant de la retirer : ses sessions en cours continuent d'y publier, et plus aucune discussion ne peut les atteindre depuis Telegram (depuis l'ordinateur, `aipager session ls` et `aipager session kill <name>` le peuvent toujours).
- **Edit a member** : Set role, Edit member deny_tools, Remove member (pas dans une discussion privée, ni pour le dernier membre d'un groupe). Pour un propriétaire ou un admin (ou un de vos rôles avec `bypass_role_denies`), Edit member deny_tools dit seulement que les outils bloqués ne s'appliquent pas à ce rôle.
- Test bot reachability, View policy, Re-install Claude Code hooks et Refresh bot token.

### <a id="adding-a-group"></a>Ajouter un groupe

1. Ajoutez le bot au groupe, puis choisissez Add a group scope (ou Add a group au premier lancement).
2. **L'identifiant du groupe.** Choisissez la détection automatique et envoyez `/start` dans le groupe, ou collez l'identifiant (un nombre négatif). L'assistant envoie un message de test et demande s'il est arrivé. Un groupe déjà configuré s'arrête ici avec "This group is already set up. Use Edit a scope → Add a member." et rien ne change.
3. **Un nom** pour le groupe (affiché dans l'assistant et dans la Mini App).
4. **Vous d'abord.** Quand vous êtes le propriétaire de votre propre discussion privée, l'assistant propose de vous ajouter comme premier membre du groupe avec le rôle `owner` (oui par défaut ; vous pouvez passer). Sans propriétaire ni admin dans le groupe, personne n'y pourrait utiliser l'Accès complet, `/settings` ou `/update`.
5. **Les autres membres**, un par un : comment les trouver (détection automatique, ou coller un identifiant ou un `@handle`), un nom (affiché comme `@label` dans la discussion), et un rôle (par défaut `user`). La liste des rôles contient `owner`, avec un avertissement : un propriétaire a le contrôle complet de cet ordinateur, y compris la configuration d'aipager, le token du bot et les identifiants de Claude. Ne le donnez qu'à vous-même. Donnez `admin` aux personnes qui doivent utiliser l'Accès complet, `/settings` et `/update` dans le groupe. (Dans sa propre discussion privée avec le bot, toute personne qui peut envoyer des messages et répondre à Claude peut utiliser l'Accès complet pour ses propres sessions, quel que soit son rôle.)
6. **Une règle optionnelle :** "Block file edits (Write, Edit) for `user` members in this group? Admins and the owner are not affected." (Bloquer les modifications de fichiers pour les membres `user` de ce groupe ? Les admins et le propriétaire ne sont pas concernés.) Par défaut c'est non : un `user` n'écrit déjà que dans le dossier de projet de la session. Avec oui, chaque Write et chaque Edit dans une tâche d'un membre dont le rôle n'est pas exempté des règles de rôle (`user`, `read_only`, et les rôles que vous définissez sans `bypass_role_denies`) est refusé sans demander, et la tâche s'arrête. Un admin ou le propriétaire doit alors envoyer ce travail.
7. **Le dossier racine des membres**, prérempli avec `~/aipager-users/group-<label>` : là où les membres `user` et `read_only` du groupe lancent leurs sessions et travaillent (voir [Dossiers racines](#root-folders)).

Pour ajouter quelqu'un à un groupe déjà configuré, utilisez Edit a scope → le groupe → Add a member. Toutes les personnes déjà dans le groupe gardent leur place et leur rôle.

### <a id="finding-ids-auto-detect"></a>Trouver les identifiants (détection automatique)

La détection automatique d'un membre prend le message le plus récent de quelqu'un qui n'a pas encore été ajouté et demande "Is this the person to add?" (Est-ce la personne à ajouter ?) avant de l'utiliser (répondez non et elle cherche plus loin). Elle propose le nom d'utilisateur Telegram de la personne comme nom. Dans un groupe, la personne doit s'adresser au bot : le mentionner ou envoyer `/start` (voir [ce que lit le bot](#what-the-bot-reads-in-a-group)). L'assistant dit où il regarde :

- **"Watching through the running daemon"** (à travers l'aipager en cours) : pendant qu'`aipager start` tourne, aipager reçoit tous les messages du bot, donc l'assistant lit ce qu'aipager a noté dans `~/.claude/aipager-pending-users.json`. aipager note qui s'adresse au bot dans un groupe qu'il ne sert pas encore (l'identifiant, le titre et le type du groupe, et l'identifiant, le nom d'utilisateur et le nom de la personne), et quiconque n'est pas membre d'un groupe qu'il sert. Il ne répond jamais dans un groupe qu'il ne sert pas. Un message privé de quelqu'un qui n'est pas configuré reçoit une réponse et n'est pas noté, donc pour ajouter la discussion privée d'une personne pendant qu'aipager tourne, collez son identifiant (ou arrêtez d'abord aipager).
- **"Watching Telegram directly"** (directement dans Telegram) : quand aipager est arrêté, l'assistant demande lui-même à Telegram les messages récents du bot.

Votre propre discussion privée, au premier lancement, est toujours lue dans Telegram. Un message d'un admin anonyme du groupe, ou un message publié au nom du groupe ou d'un canal, ne compte pas : son identifiant d'expéditeur (1087968824, 136817688 ou 777000) est le même pour tous ceux qui publient ainsi, donc l'assistant le refuse, tapé ou détecté. Demandez à la personne de publier avec son propre compte.

### <a id="where-the-config-lives"></a>Où se trouve la configuration

L'assistant écrit `~/.config/aipager/aipager.yaml` (droits 0600) : le token du bot, les discussions que sert le bot, et les membres de chaque discussion avec leurs rôles. Ce que chaque rôle peut faire se trouve dans `~/.config/aipager/policy.yaml`, qui est à vous : l'assistant ne l'écrit jamais (voir [Comment les règles fonctionnent](#how-rules-work)). Les installations configurées avec l'ancien format `team.yaml` sont migrées automatiquement, par aipager au démarrage ou par l'assistant à son prochain lancement.

### <a id="live-reload"></a>Recharger sans redémarrer

Après chaque changement de discussion ou de membre fait depuis le menu de modification (ajouter ou retirer un groupe ou une discussion privée, ajouter ou retirer un membre, définir un rôle, modifier `deny_tools`, renommer une discussion), et après un groupe ou une personne ajoutés à la fin du premier lancement ou un groupe inachevé que vous reprenez (un seul rechargement pour tout ce que vous y avez ajouté), `aipager config` envoie à l'aipager en cours un rechargement à chaud (**SIGUSR1**) et dit "Scopes reloaded live (no daemon restart needed)". Le rechargement relit `aipager.yaml` et `policy.yaml`. Si la configuration sur le disque devait être refusée, l'assistant dit "Not applied" avec la raison, et aipager garde sa configuration précédente jusqu'à ce que vous corrigiez. Si l'assistant ne peut pas savoir avec certitude quel processus est aipager, il vous dit de le redémarrer à la place.

Ce qu'un rechargement applique tout de suite :

- qui peut utiliser le bot dans chaque discussion, avec quel rôle et quelles règles ;
- un nouveau groupe ou une nouvelle discussion privée commence à recevoir des messages et reçoit son menu `/` ; une discussion retirée s'arrête (ses sessions continuent de tourner jusqu'à ce que vous les terminiez) ;
- un message retenu (qui attend qu'une demande reçoive sa réponse ou que la tâche de quelqu'un d'autre se termine) d'une personne qui n'a plus le droit d'écrire dans cette discussion est abandonné : il reçoit un 🤷 et le bot répond « Pas envoyé à Claude : @bob n'a plus le droit d'écrire ici. » ;
- une tâche en cours d'une personne retirée ou qui a perdu des droits ne garde, pour le reste de la tâche, que le plus strict de ses anciennes règles et des nouvelles (le socle restreint pour une personne retirée). Une tâche tapée dans le terminal n'est pas changée, sauf si un message Telegram l'a rejointe : aipager ne note pas de qui, donc une telle tâche est restreinte quand n'importe quel propriétaire de cette discussion (ou, dans une discussion privée, son membre) perd des droits. Une tâche qui tournait déjà quand aipager a redémarré (encore au travail, ou en attente d'une question ou d'une autorisation) est restreinte pour chaque membre de sa discussion qui a perdu des droits. Une promotion n'élargit jamais une tâche en cours : les nouveaux droits s'appliquent à partir de la prochaine tâche de cette personne.

Seul un nouveau **token de bot** demande un redémarrage (`aipager config` le dit après Refresh bot token). Quand aucun aipager ne tourne, aucun rechargement n'est envoyé : le prochain `aipager start` lit la nouvelle configuration.

Pour recharger à la main (par exemple après avoir modifié `policy.yaml`), envoyez le signal au seul processus d'aipager. Sous le service en arrière-plan :

```sh
pid=$(systemctl --user show -p MainPID --value aipager.service)
[ "${pid:-0}" -gt 0 ] && kill -USR1 "$pid"
```

(La vérification compte : quand le service ne tourne pas, `MainPID` vaut `0`, et `kill -USR1 0` enverrait le signal aux processus de votre propre shell.)

Pour un aipager que vous avez lancé vous-même avec `aipager start`, envoyez `kill -USR1 <pid>` à ce processus. N'envoyez pas le signal à tout ce que trouve `pgrep -f 'aipager start'` (un shell ou un conteneur qui fait tourner aipager correspond aussi, et SIGUSR1 arrête un processus qui ne le gère pas), et n'utilisez pas `systemctl --user kill` sans `--kill-whom=main` (les sessions du service sont dans son groupe et seraient arrêtées).

Si `aipager.yaml` ou `policy.yaml` est mal formé au moment du rechargement, ou si `aipager.yaml` est absent, aipager écrit un avertissement dans son journal et garde la configuration précédente en mémoire. Une faute de frappe ou un fichier mis de côté ne peut ni vous bloquer dehors ni laisser entrer qui que ce soit : un rechargement ne fait jamais repasser en usage personnel un aipager qui a des discussions configurées (seul un redémarrage le fait).

### <a id="bot-admin-rights-and-privacy-mode"></a>Droits d'admin du bot et mode confidentialité

**Faites du bot un admin du groupe, avec le droit d'épingler des messages.** Il en a besoin pour épingler la barre d'état du groupe (voir [Ce que tout le monde voit](#what-everyone-sees)). Sans cela, le groupe marche mais n'a pas de barre d'état ; si vous donnez le droit au bot plus tard, redémarrez aipager pour que la barre apparaisse.

Un bot qui est admin du groupe reçoit tous les messages du groupe, quel que soit son mode de confidentialité dans BotFather. C'est sans danger : aipager ne traite que les messages listés dans [ce que lit le bot](#what-the-bot-reads-in-a-group) et ignore le reste, donc la conversation du groupe n'arrive jamais à une session. Un bot qui n'est pas admin est en mode confidentialité par défaut (laissez-le activé), et Telegram ne lui passe alors que certains messages du groupe ; si l'un des vôtres n'arrive pas, répondez à l'un des messages du bot ou utilisez une commande. Le réglage de confidentialité ne change rien à ce que traite aipager.

Être admin Telegram du groupe ne donne rien à personne dans aipager. Ce que quelqu'un peut faire vient seulement de son rôle dans `aipager.yaml`.

### <a id="when-telegram-upgrades-the-group"></a>Quand Telegram met le groupe à niveau

Telegram transforme un groupe simple en supergroupe quand vous le rendez public, activez les sujets, dépassez 200 membres ou changez certains droits d'admin, et cette mise à niveau donne au groupe un nouvel identifiant de discussion. aipager suit tout seul : quand il voit la mise à niveau (l'avis de Telegram dans le groupe, ou le premier envoi vers l'ancien identifiant), il déplace l'entrée du groupe dans `aipager.yaml` vers le nouvel identifiant, emmène avec elle les sessions, les destinations des messages et les `/settings` du groupe, et publie une ligne dans le groupe : « Telegram a mis à niveau ce groupe. aipager a suivi ; vous n'avez rien à faire. » La barre d'état épinglée repart dans le groupe mis à niveau. Le journal d'aipager contient un avertissement avec les deux identifiants.

Les noms des sessions dans aipager gardent l'ancien identifiant (vous ne les voyez jamais), et `aipager.yaml` garde une courte trace `chat_migrations` du déplacement, pour qu'une session lancée avant la mise à niveau appartienne toujours au groupe mis à niveau, même après un redémarrage. Le nom interne d'une session avec l'ancien identifiant (`/name__g<old id>`) marche dans le groupe mis à niveau et nulle part ailleurs. Si aipager était arrêté pendant la mise à niveau, il s'en aperçoit à son prochain démarrage. Une tâche qui tourne au moment de la mise à niveau se termine et sa réponse arrive dans le groupe mis à niveau, mais sa carte de progression n'est plus affichée pour le reste de cette tâche. Si le nouvel identifiant est déjà une discussion à part dans la configuration, rien ne bouge et le journal le dit ; retirez l'une des deux avec `aipager config`. « Test bot reachability » dans `aipager config` montre le nouvel identifiant quand il rencontre un groupe mis à niveau.

À chaque démarrage, aipager consulte une fois chaque groupe. Si Telegram répond que le groupe est introuvable ou que le bot ne peut pas l'utiliser (le groupe a été supprimé, le bot en a été retiré, ou le groupe a été mis à niveau pendant qu'aipager était arrêté et Telegram n'a pas dit vers quel identifiant), le journal d'aipager contient un avertissement qui nomme la discussion : vérifiez que le bot est toujours dans le groupe et, s'il a été mis à niveau, ajoutez de nouveau le groupe dans `aipager config` avec Add a group scope (il reçoit le nouvel identifiant ; ajoutez de nouveau ses membres), puis retirez l'ancien avec Edit a scope → Remove this scope (l'ancien identifiant ne marche plus). aipager ne publie rien et n'insiste pas.

### <a id="forum-topics-are-not-supported"></a>Les sujets de forum ne sont pas pris en charge

Utilisez un groupe ordinaire (sans sujets) par équipe. Dans un groupe où les sujets sont activés, aipager marche quand même, mais il ne reste pas dans un sujet :

- la réponse à une commande ou à un message (par exemple `/status`, ou la réponse à « Quelle session ? ») reste dans le sujet où elle a été envoyée ;
- les notifications, les cartes d'activité, les réponses, les demandes d'autorisation et la barre d'état épinglée sont publiées dans le groupe sans sujet, donc elles peuvent atterrir dans General ;
- chaque personne a une session actuelle pour tout le groupe, pas une par sujet.

Activer les sujets transforme aussi un groupe simple en supergroupe, ce qu'aipager suit (plus haut).

## <a id="roles"></a>Rôles

Quatre rôles intégrés (voir `aipager/safety.py`). Le rôle qui compte est celui de la personne **dans la discussion où elle agit**. Personne ne peut changer les membres ni les rôles depuis Telegram ou la Mini App : seul `aipager config` sur l'ordinateur le fait, ce qui laisse cela entre les mains du propriétaire.

| | `owner` | `admin` | `user` | `read_only` |
|---|---|---|---|---|
| Envoyer des messages (`can_prompt`) | ✅ | ✅ | ✅ | ❌ |
| Répondre à Claude : Autoriser, Refuser, questions (`can_approve`) | ✅ | ✅ | ✅ | ❌ |
| Gérer : Accès complet, `/settings` d'un groupe, `/update`, installation de la fonction vocale (`can_manage`) | ✅ | ✅ | ❌ (Accès complet : ✅ dans sa propre discussion privée) | ❌ |
| Changer les membres et les rôles depuis Telegram ou la Mini App | ❌ | ❌ | ❌ | ❌ |
| Exempté des règles de refus de rôle (`bypass_role_denies`) | ✅ | ✅ | ❌ | ❌ |
| Exempté du socle de sécurité (`bypass_safety`) | ✅ | ❌ | ❌ | ❌ |
| Bash et les autres outils qui exécutent du code | ✅ | ✅ (règles au mieux) | ❌ | ❌ |
| N'importe quelle commande slash | ✅ | ✅ | seulement celles du clavier Commandes, `/compact` et les changements de modèle | comme `user` |
| Où démarrent les sessions (`/new`, Reprendre, Redémarrer, le changement de mode) | partout | partout | seulement dans le [dossier racine](#root-folders) de la discussion | comme `user` |
| Où vont les écritures de fichiers | partout | partout où le socle de sécurité le permet | le dossier de projet de la session (quand il est dans le dossier racine) et son scratchpad, jamais le dossier personnel | comme `user` |
| Lectures dans votre dossier personnel | partout où le socle de sécurité le permet | partout où le socle de sécurité le permet | seulement dans le dossier racine (les autres dossiers de l'ordinateur comme avant, jamais les dossiers racines des autres membres) | comme `user` |
| Fichiers d'identifiants (`~/.ssh`, `~/.git-credentials`, ...) | lisibles | lisibles | jamais (par défaut) | jamais (par défaut) |
| `Grep`/`Glob` | partout | partout sauf dans les dossiers protégés d'aipager (ni un dossier qui en est un, ni un dossier qui se trouve dans l'un d'eux ou en contient un) | le dossier de projet de la session et son scratchpad | comme `user` |

- **owner** : contrôle complet, y compris sur le socle de sécurité intégré. Il ne devrait y en avoir qu'un : la personne qui administre l'ordinateur.
- **admin** : tout ce qu'un propriétaire peut faire dans Telegram et la Mini App : passer des sessions en Mode Accès complet (et les lancer dans ce mode), reprendre une session en Accès complet, changer les `/settings` d'un groupe, lancer `/update`, installer la fonction vocale, envoyer n'importe quelle commande slash. Un admin est aussi exempté des règles de refus de rôle (les `deny_tools` de la discussion et du membre ne s'appliquent pas à lui). Deux choses restent au propriétaire :
  - changer les membres et les rôles : seul `aipager config` sur l'ordinateur le fait, et rien dans Telegram ni dans la Mini App ne le peut ;
  - l'exemption du socle de sécurité (la configuration et le token du bot d'aipager, les identifiants de Claude Code, les fichiers de contrôle d'aipager). Le `Grep` et le `Glob` d'un admin peuvent chercher partout ailleurs, mais pas dans un dossier qui est l'un de ceux-là, se trouve dans l'un d'eux ou en contient un : un `Grep` de `~`, de `/` ou de `/tmp` (ou sans dossier, depuis une session dans le dossier personnel) est refusé, tout comme un `Glob` dont le motif commence dans l'un d'eux (comme `/home/you/.config/aipager/*`). Cherchez plutôt dans un dossier de projet. Sur les versions de Claude Code sans outil `Grep`, Claude cherche par Bash ; là aussi, une recherche ou un listage récursif de tout le dossier personnel ou de `/` (`grep -rn x ~`, `rg x $HOME`, `find / -name x`) est refusé, au mieux (une recherche sans dossier, ou avec `..`, lancée depuis le dossier personnel ou en dessous n'est pas vue), et les recherches dans un projet ne le sont pas, y compris une recherche D'UN chemin personnel (`grep -rn "$HOME" src`) ([sécurité](security.md#team-mode-enforcement)).

  Un admin garde Bash, et avec un shell les motifs de commandes du socle sont au mieux, pas une barrière : ne faites de quelqu'un un admin que si vous lui donneriez un shell sur l'ordinateur.
- **user** : les règles de refus s'appliquent. Pas de Bash, ni aucun autre outil qui exécute du code (PowerShell, Monitor, messages programmés, workflows ; la liste est `safety.CODE_EXECUTION_TOOLS`), pas de `SendMessage` vers d'autres sessions, pas de changement de worktree. Les écritures de fichiers ne vont que dans le dossier de la session et dans son scratchpad Claude Code, et jamais dans le `.claude/`, le `.git/` ou le `.mcp.json` de ce dossier (chacun d'eux exécute des commandes). `Grep` et `Glob` ne cherchent que dans ces dossiers, avec de simples globs relatifs. Un `user` ne lance, reprend, redémarre et change de mode des sessions que dans le [dossier racine](#root-folders) de la discussion, et ne lit rien de votre dossier personnel en dehors. Un dossier de session qui est votre dossier personnel, `/` ou un dossier au-dessus, ou qui se trouve hors du dossier racine (une session lancée avant les dossiers racines, ou par quelqu'un d'autre), ne compte pas : là, un `user` ne peut écrire et chercher que dans le scratchpad. Les lectures suivent les règles des chemins protégés, et vos fichiers d'identifiants ne sont jamais lisibles : `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config/gh`, `~/.git-credentials`, `~/.netrc`, `~/.docker/config.json`, `~/.kube`, `~/.config/gcloud`, `~/.azure`, `~/.password-store`, `~/.pgpass`, `~/.npmrc` et `~/.pypirc`. Commandes slash : seulement celles du clavier Commandes, `/compact` et les changements de modèle (voir [Avec les droits de qui un message s'exécute](#who-a-message-runs-as)).
- **read_only** : des observateurs. Ils voient tout dans le groupe, et peuvent utiliser `/status`, `/diff`, `/whoami`, `/help`, `/start` et `/app`, regarder `/settings`, et feuilleter une liste `/resume` que quelqu'un d'autre a ouverte. Leurs propres messages texte, vocaux et fichiers ne sont envoyés nulle part. Le premier qu'ils envoient dans une discussion reçoit une réponse disant que leur rôle est `read_only` ; ensuite le bot reste muet pour eux dans cette discussion (ni réponse, ni réaction) jusqu'au redémarrage d'aipager. Ils ont les mêmes limites d'outils et d'écriture que `user`, au cas où l'un de leurs messages arriverait un jour à une session.

### <a id="custom-roles"></a>Rôles personnalisés

`policy.yaml` peut changer n'importe quel champ d'un rôle intégré, ou définir de nouveaux rôles. Un champ que vous définissez remplace la valeur intégrée. Les champs :

- `can_prompt`, `can_approve` : envoyer des messages ; répondre à Claude.
- `can_manage` : utiliser l'Accès complet, changer les `/settings` d'un groupe, lancer `/update` et installer la fonction vocale (intégré : `owner` et `admin` à true, `user` et `read_only` à false). Il ne permet jamais à personne de changer les membres ou les rôles, et n'exempte jamais du socle de sécurité.
- `bypass_role_denies`, `bypass_safety` : voir le tableau.
- `deny_tools`, `allow_tools`, `deny_bash_patterns`, `deny_paths_no_access`, `deny_paths_no_write` : voir [Comment les règles fonctionnent](#how-rules-work).

Un rôle que vous définissez à partir de rien commence avec `can_prompt` et `can_approve` activés, `can_manage` et les deux exemptions désactivés, les fichiers d'identifiants dans son `deny_paths_no_access`, et **aucun outil refusé, donc il a Bash** tant que vous ne définissez pas `deny_tools` (`aipager doctor` avertit une fois qu'un membre l'a). L'enfermement des écritures et des recherches s'applique à chaque rôle sans `bypass_safety` ni `bypass_role_denies`. Le [dossier racine](#root-folders) s'applique à chaque rôle sans `can_manage`, quelles que soient ses exemptions : un rôle que vous définissez avec `bypass_role_denies` ou `bypass_safety` mais sans `can_manage` écrit et cherche partout dans son dossier racine (et son scratchpad), ne lit rien de votre dossier personnel en dehors, et nulle part ailleurs (avec `bypass_safety`, dans une session qui tourne hors de son dossier racine, il n'écrit que dans son scratchpad). Seuls les propriétaires et les admins n'ont pas de restriction.

### <a id="root-folders"></a>Dossiers racines

Chaque discussion a un dossier racine pour ses membres : le dossier où les membres dont le rôle n'y a pas `can_manage` (les rôles intégrés `user` et `read_only`, et les rôles que vous définissez sans lui) lancent et gardent leurs sessions. Les propriétaires et les admins n'y sont pas tenus. Le dossier racine d'une discussion privée appartient à cette personne ; celui d'un groupe est partagé par les membres restreints du groupe. Deux discussions ne partagent jamais le même, sauf si vous leur donnez exprès le même dossier (l'assistant vous demande de le confirmer).

- **Le définir.** `aipager config` le demande quand vous ajoutez un groupe, ou une discussion privée pour un rôle sans `can_manage`, prérempli avec la valeur par défaut, et Edit a scope → **Set the members' root folder** le change (appliqué tout de suite, par un rechargement à chaud). La configuration actuelle montre le dossier racine de chaque discussion, avec `(default)` quand aucun n'est défini.
- **La valeur par défaut** est `~/aipager-users/<label>` pour une discussion privée (le nom de la personne) et `~/aipager-users/group-<label>` pour un groupe. Le nom est mis en minuscules et tout ce qui n'est pas une lettre, un chiffre, `.`, `_` ou `-` devient `-` ; quand deux discussions auraient le même dossier, chacune reçoit en plus l'identifiant de sa discussion (`sara-123456`). Le dossier (et `~/aipager-users`, s'il est nouveau) est créé, avec les droits 0700 pour que personne d'autre que vous ne puisse l'ouvrir, la première fois qu'un membre ouvre `/new` ou le formulaire Nouvelle session de la Mini App. Une valeur par défaut ne bouge jamais : quand une modification dans `aipager config` (le dossier racine d'une autre discussion, une discussion retirée, un nouveau nom) changerait le dossier par défaut d'une discussion, l'assistant écrit le dossier qu'elle avait comme dossier racine de cette discussion.
- **Ce que l'assistant refuse** : un chemin relatif, des caractères jokers, `/`, votre dossier personnel ou un dossier au-dessus, un dossier qui est l'un des dossiers protégés d'aipager ou des fichiers d'identifiants, se trouve dans l'un d'eux ou en contient un (`~/.claude`, `~/.config/aipager`, `~/.config`, `~/.ssh`, ...), et le dossier temporaire de Claude Code (`/tmp/claude-*`, où se trouve le scratchpad de chaque session). Un dossier qui n'existe pas encore convient. Il demande de confirmer un dossier depuis lequel votre système lance des programmes (`~/bin`, `~/.local/bin`, `~/.config/systemd`, `~/.config/autostart`, ou un dossier à l'intérieur).
- **Lancer des sessions.** La page Dossier de `/new` et la Mini App listent le dossier racine et les dossiers directement à l'intérieur (pas les cachés, ni un lien qui en sort), sans la ligne « Dossier par défaut » ; une nouvelle session démarre dans le dossier racine sauf si vous choisissez l'un de ceux-là. Nouveau dossier crée le dossier dans celui que vous avez choisi. Tout le reste reçoit « Votre rôle ne peut lancer des sessions que dans votre dossier ~/... ou dans un dossier à l'intérieur. »
- **Les sessions en dehors.** Reprendre, redémarrer, Remplacer et changer le mode d'une session dont le dossier est hors du dossier racine reçoivent « Cette session travaille hors de votre dossier ~/..., donc votre rôle ne peut ni la reprendre, ni la redémarrer, ni changer son mode. Demandez à un admin. » Les messages vers une telle session en cours sont toujours envoyés, et le hook de sécurité tient la tâche : là, elle n'écrit que dans le scratchpad.
- **Ce à quoi le hook tient une tâche.** La tâche d'un membre restreint n'écrit que dans le dossier de la session (quand il est dans le dossier racine) et dans son scratchpad, ne lit, ne fait de `Grep` ni de `Glob` sur rien de votre dossier personnel en dehors du dossier racine (à part son scratchpad et son propre fichier de réponse), et ne touche jamais le dossier racine d'une autre discussion, où qu'il soit. Les dossiers en dehors de votre dossier personnel (`/usr`, `/etc`, ...) restent lisibles, les dossiers protégés restent refusés, et `Bash` reste refusé à `user`. Quand une tâche mélange deux membres avec des dossiers racines différents, le plus étroit l'emporte ; deux qui ne sont pas l'un dans l'autre ne laissent que le dossier de la session elle-même.

Pour redonner Bash à `user` (seulement pour des personnes à qui vous donneriez un shell), nommez-le dans `allow_tools` (qui devient alors aussi la liste d'autorisation de ce rôle) ou remplacez son `deny_tools` :

```yaml
roles:
  user:
    # la liste intégrée sans Bash
    deny_tools: [PowerShell, Monitor, REPL, Workflow, CronCreate,
                 RemoteTrigger, AppifactRepl, self_hosted_runner_spawn_local,
                 self_hosted_runner_requeue_session, SendMessage,
                 EnterWorktree, ExitWorktree]
```

`aipager doctor` avertit alors : `role user has Bash: its safety rules are best-effort, not a boundary`. Si votre `policy.yaml` définissait déjà `deny_tools` pour `user` avant l'arrivée de cette valeur par défaut, votre liste la remplace toujours, donc ce rôle a Bash tant que vous ne le rajoutez pas ; l'avertissement du doctor vous le dit. Voir [sécurité → comment les règles s'appliquent en équipe](security.md#team-mode-enforcement) pour ce que ces limites ne couvrent pas.

## <a id="talking-to-the-bot-in-a-group"></a>Parler au bot dans un groupe

### <a id="what-the-bot-reads-in-a-group"></a>Ce que lit le bot dans un groupe

Dans un groupe, aipager ne traite qu'un message qui est :

- **une commande** : `/status`, `/x1 fix the tests`, ou la même chose avec `@aipagerbot` après la commande (`/x1@aipagerbot`), que Telegram ajoute quand vous la choisissez dans le menu. Une commande pour un autre bot (`/x1@otherbot`) est ignorée ;
- **une réponse à l'un des messages du bot** ;
- **un message qui mentionne le bot** : `@aipagerbot fix the tests` ;
- **un appui sur le clavier du bot** dans le groupe. Dans un groupe, chaque bouton du clavier commence par un petit carré (`▫️ stop`, `▫️ Effacer la conversation`), et seul ce texte marqué compte comme un appui. Taper le mot seul, c'est de la conversation ordinaire : un membre qui tape `stop`, `Effacer la conversation`, `Opus` ou le nom d'une session ne change rien. Pour utiliser exprès un mot du clavier sans le clavier, adressez-vous au bot : `@aipagerbot stop`, ou `stop` en réponse à l'un de ses messages.

Tout le reste (les membres qui parlent entre eux, une photo ou un message vocal qui n'est pas une réponse au bot) ne reçoit ni réaction ni réponse. C'est vrai que le bot soit admin du groupe ou non. Les messages modifiés sont ignorés partout : modifiez un message et rien ne se passe ; renvoyez-le plutôt.

Une photo, un fichier ou un message vocal marchent de la même façon : envoyez-les en réponse au bot, ou mentionnez le bot dans la légende. Pour un album, mettez la légende sur son premier élément : les éléments de l'album qui arrivent après celui qui porte la légende, la mention ou la réponse sont pris, ceux d'avant ne le sont pas.

**La mention est retirée.** `@aipagerbot fix the tests` arrive à Claude sous la forme `fix the tests` (Claude Code lirait `@word` comme un fichier). `@aipagerbot status` et `@aipagerbot x2` marchent comme taper `status` ou `x2` dans une discussion privée, et `@aipagerbot` seul montre où vont vos messages. Une commande de session après la mention marche (`@aipagerbot /x1 fix it`), mais une des commandes d'aipager lui-même non : `@aipagerbot /stop` reçoit « Envoyez /stop seul, sans mentionner le bot. »

**Les membres seulement.** Une personne qui n'est pas membre du groupe reçoit une réponse (« 🚫 Vous n'êtes pas parmi les personnes autorisées dans cette discussion. ») avec son identifiant Telegram à vous transmettre, et est notée pour `aipager config` ; ensuite, le bot l'ignore jusqu'au redémarrage d'aipager. Un membre `read_only` apprend son rôle une fois (voir [Rôles](#roles)).

**Les admins anonymes doivent publier avec leur propre compte.** Un admin du groupe qui a activé « Remain anonymous », ou un message publié au nom du groupe ou d'un canal, ne dit pas qui l'a écrit : Telegram montre le même expéditeur pour tous les admins anonymes. aipager ne peut donner à un tel message le rôle de personne, donc il répond « Écrivez avec votre propre compte pour utiliser le bot. » (une fois par discussion jusqu'au redémarrage d'aipager) et ne fait rien d'autre. Il n'est jamais noté comme utilisateur en attente, et l'assistant refuse cet identifiant partagé. Désactivez « Remain anonymous » pour vous dans les paramètres d'admin du groupe, ou publiez depuis votre propre compte.

### <a id="talking-to-a-session-in-a-group"></a>Parler à une session dans un groupe

Répondez à l'un des messages d'une session (sa réponse, sa carte) et votre message va à cette session. Ou envoyez `/x1 votre message`, ou une légende qui commence par `/x1`.

**Chaque personne a sa propre session actuelle** : la dernière vers laquelle elle est passée (`/x1`), à laquelle elle a envoyé un message, répondu ou qu'elle a lancée. La réponse de la session d'un autre membre ne la change pas, pas plus que les messages des autres. Une mention, un message vocal, un modèle ou une commande du clavier va à votre propre session actuelle, et une commande du clavier ou un modèle la nomme dans sa réponse (« 🧹 /clear envoyé à x1 »). Un `/stop`, `/mode`, `/kill`, `/restart`, `/rename`, `/delete` ou `/diff` sans nom n'agit que sur votre propre session actuelle (avec sa confirmation habituelle), et seulement quand la commande a du sens pour elle (au travail pour `/stop`, terminée pour `/delete`) ; sinon il montre toujours la liste, même quand il ne reste qu'une session, puisqu'elle peut être celle de quelqu'un d'autre. `/clearqueue` et `/now` agissent sur votre session actuelle. `/kill x1` et les autres commandes avec un nom agissent sur cette session, comme avant.

- Sans session actuelle à vous et avec plus d'une session en cours, le bot demande « Quelle session ? Répondez à l'un de ses messages, ou écrivez /x1 suivi de votre message. » avec un bouton par session, et n'envoie rien. Avec une seule session en cours, le message va à celle-là.
- Si votre propre session actuelle est terminée, un message n'est pas envoyé à la seule session encore en cours (elle peut être celle de quelqu'un d'autre) : le bot répond « x1 est terminée. Quelle session ? » avec un bouton par session en cours et, quand x1 peut être reprise, « ▶️ Reprendre x1 », et n'envoie rien.
- Toucher le bouton d'une session sur l'une ou l'autre question envoie aussi à cette session le message dont il était question (un texte, la transcription d'un message vocal ou un fichier), comme si vous l'aviez envoyé là, et la question devient « Envoyé à x2. ». Seule la personne qui a envoyé le message peut utiliser les boutons, et seulement pendant dix minutes et jusqu'au redémarrage d'aipager : ensuite, le bouton fait seulement de cette session votre session actuelle (« Ce message est trop ancien et n'a pas été envoyé ; renvoyez-le. »). ▶️ Reprendre ramène x1 mais n'envoie rien : renvoyez votre message.
- La barre épinglée n'a pas de ligne « Les messages vont à » dans un groupe, puisque cela change d'une personne à l'autre.

**Les cartes qui attendent une réponse appartiennent à une personne.** La carte de nouvelle session dit « Répondez à ce message avec un nom et, si vous voulez, ce qu'il faut faire en premier : », la question de renommage « … Répondez à ce message avec le nouveau nom. ». Dans un groupe, chacune montre à qui elle appartient (« 🆕 Nouvelle session (@alice) »), seule cette personne peut y répondre, et deux personnes peuvent lancer ou renommer des sessions en même temps. La réponse d'un autre membre à la carte de nouvelle session de quelqu'un reçoit « Cette carte appartient à @alice. Envoyez /new pour avoir la vôtre. » et ne va nulle part. Une question de renommage arrête d'attendre au bout de 10 minutes, ou dès que sa personne fait autre chose. La carte « x1 est prête », la réponse à `/x1`, `/start` et `/help` disent de répondre au message d'une session ou de mentionner le bot (« Répondez à un message de x1 (ou mentionnez @aipagerbot) pour lui parler. »), puisqu'un message ordinaire n'arrive pas au bot.

**Le clavier est propre à chaque personne.** Modèles, Commandes, Modèle IA › et « Retour ne changent que le clavier de la personne qui les touche : le bot répond en réponse à l'appui de cette personne, et le clavier de personne d'autre ne bouge. « Retour remonte d'un niveau depuis là où vous êtes, quoi que regardent les autres membres. Le clavier principal qu'envoie `/start` est celui de tout le groupe, et remet tout le monde sur le clavier principal. Quand une session du groupe démarre ou se termine, aipager renvoie ce clavier principal au groupe, une fois, pour que ses boutons de session restent à jour. Après une mise à jour depuis une version sans les boutons marqués, un appui sur un bouton de l'ancien clavier peut être ignoré : envoyez `/start` une fois pour donner au groupe le nouveau clavier. Le clavier d'une discussion privée n'a pas de marque et marche comme avant.

### <a id="the-mini-app"></a>La Mini App

`/app` n'ouvre la Mini App que dans une discussion privée. Dans un groupe, elle n'est pas dans le menu `/` ; tapée là, elle dit à un membre dont la propre discussion avec le bot est configurée « 📱 La Mini App ne marche que dans une discussion privée : écrivez au bot en privé et envoyez /app là-bas. », et à toute autre personne « La Mini App s'ouvre depuis votre discussion privée avec le bot. Demandez au propriétaire du bot de vous ajouter. »

La Mini App montre une discussion à la fois. Quelqu'un qui fait partie de plus d'une discussion (par exemple sa propre discussion privée et un groupe) reçoit en haut une ligne de noms de discussions pour passer de l'une à l'autre ; elle s'ouvre sur sa propre discussion privée, sinon sur la première discussion qui la contient, et se souvient du dernier choix sur ce téléphone. Tout ce qu'elle contient (les sessions, leurs boutons, les paramètres et les notifications qu'elle publie) est pour la discussion choisie, avec le rôle que la personne a dans cette discussion. Avec une seule discussion, rien de plus n'est affiché.

## <a id="who-a-message-runs-as"></a>Avec les droits de qui un message s'exécute

Chaque message s'exécute avec **les droits de la personne qui l'a envoyé**, dans la discussion d'où elle l'a envoyé. Quelqu'un qui est `user` dans le groupe et `owner` de sa propre discussion privée envoie, touche et lance des commandes dans le groupe en tant que `user`, quel que soit l'ordre des discussions dans `aipager.yaml`.

- Chaque message Telegram apporte à la session l'identité et les règles de son expéditeur (voir [sécurité → comment les règles s'appliquent en équipe](security.md#team-mode-enforcement)). Claude voit qui a envoyé chaque message (`[via Telegram · @bob · role:user]` dans un groupe), jamais la personne qui pilotait la session avant. La personne qui envoie un message prend la main sur la session, qu'elle l'ait tapé ou qu'elle ait utilisé `/x1`, un modèle ou une commande du clavier.
- **Les messages retenus gardent leur expéditeur.** Pendant que la tâche d'une autre personne tourne (y compris un agent en arrière-plan qu'elle a lancé), ou pendant que le message de quelqu'un d'autre attend encore d'être pris, ou pendant qu'une demande d'autorisation ou une question est ouverte, un nouveau message est retenu (👀) et envoyé quand cela se libère, comme sa propre tâche, **avec les droits de son propre expéditeur**. Une tâche tapée dans le terminal compte comme celle du propriétaire : seul le message d'un propriétaire (ou, dans une discussion privée, celui de son propre membre) la rejoint. Vos propres messages n'attendent jamais votre propre tâche. Si la tâche qu'ils attendent est arrêtée, ou s'arrête parce qu'un outil a été bloqué, les messages retenus sont abandonnés (🤷) : renvoyez-les.
- **Le plus strict l'emporte.** Si des messages de personnes différentes finissent quand même dans une même tâche, la tâche tourne avec la combinaison la plus restrictive de tous ceux qui en font partie. Un message qui rejoint une tâche en cours ne l'élargit jamais : le reste de la tâche garde les règles les plus strictes de tous ceux qui l'ont rejointe.
- **Les commandes slash des rôles restreints.** Un rôle qui n'est pas exempté des règles de rôle (`user`, `read_only`, les rôles que vous définissez sans `bypass_role_denies`, et un expéditeur qu'aipager ne connaît pas) ne peut envoyer que les commandes slash du clavier Commandes (le vôtre, si `keyboard.json` le change), `/compact` et les changements de modèle (`/model <name>`). Toute autre, tapée, envoyée avec `/x1 /deliver ...`, en légende d'un fichier, comme premier message de `/new x1 /review ...` ou comme modèle, reçoit « Cette commande est réservée aux admins. » et un 🤷, et rien n'arrive à Claude. Les propriétaires et les admins envoient n'importe laquelle.
- **Les rôles restreints restent hors du dossier personnel et loin des fichiers d'identifiants.** Une tâche `user` ou `read_only` n'écrit jamais dans un dossier de session qui est votre dossier personnel, `/` ou au-dessus (seulement dans son scratchpad), et ne lit jamais les fichiers d'identifiants listés dans [Rôles](#roles).
- **Les rôles restreints restent dans leur dossier racine.** Une tâche d'un rôle sans `can_manage` ne lit rien de votre dossier personnel hors du [dossier racine](#root-folders) de la discussion, et n'écrit rien dans un dossier de session hors de lui. Le dossier racine est celui de la discussion d'où vient le message du membre, quelle que soit la session à laquelle il va.
- **Un membre retiré perd ses droits tout de suite, pas sa tâche en cours.** Un expéditeur qu'aipager ne connaît plus (retiré pendant que son message attendait) tourne avec le socle restreint. Après un rechargement à chaud, ses messages retenus sont abandonnés, et une tâche à lui qui tourne encore continue de tourner, tenue au socle restreint jusqu'à sa fin ; envoyez `/stop` pour l'arrêter tout de suite (voir [Recharger sans redémarrer](#live-reload)).
- **Un message d'une autre session Claude porte les droits de son expéditeur.** Un message qu'une session envoie à une autre (le `SendMessage` de Claude Code) tourne avec les droits de la tâche qui l'a envoyé, tels qu'aipager les a vérifiés, jamais avec les droits antérieurs de la session qui le reçoit. Un message qu'aipager ne peut pas vérifier tourne avec les règles les plus strictes et sans modification de fichiers ni accès au web (voir [sécurité → messages entre sessions Claude](security.md#messages-between-claude-sessions)).
- **Un outil bloqué arrête la tâche.** Quand une tâche essaie un outil que ses règles refusent, la tâche s'arrête tout de suite avec « 🛑 x1 · Bloqué par une règle de sécurité : ... (arrêtée) », et chaque appel d'outil suivant de cette tâche est refusé aussi.

## <a id="who-can-tap-what"></a>Qui peut toucher quoi

Chaque bouton vérifie le rôle de la personne qui le touche, dans la discussion où se trouve le bouton, au moment où elle le touche. Le rôle de la personne qui a envoyé la commande qui a créé le bouton ne compte pas. (Certaines cartes vérifient aussi qui les a demandées : les cartes de confirmation et les cartes de `/new`, plus bas.)

- **Regarder est ouvert à tous les membres** (VIEW). Les membres `read_only` peuvent feuilleter `/resume`, ouvrir la vue Terminées de `/status`, le menu ⋮ d'une session, son mode ou son diff, parcourir `/settings` et fermer un menu. Ils ne peuvent pas agir : tout autre bouton répond « Votre rôle dans cette discussion ne le permet pas. »
- **Répondre à Claude demande `can_approve`** (APPROVE) : Autoriser, Toujours autoriser, Refuser, les options d'une question, Envoyer, Continuer et le Répondre de la barre épinglée. `read_only` ne l'a pas.
- **Agir sur une session demande `can_prompt`** (PROMPT) : Arrêter, Réessayer, Compacter, Envoyer maintenant, Terminer, Redémarrer, Renommer, Supprimer, Reprendre, le changement de mode, ✍️ parler, Tout retirer, les paramètres propres à une session et les cartes de `/new`. Un bouton qu'aipager ne connaît pas le demande aussi.
- **Passer en Accès complet demande un admin** (MANAGE : `owner`, `admin`, ou un rôle avec `can_manage`, la même règle que pour `/mode full`) : Oui, changer et Arrêter et changer sur une carte qui passe en Accès complet, et Reprendre en Accès complet. Passer entre Demander et Automatique est permis à quiconque peut agir sur la session. Reprendre une session qui était en Accès complet (`/resume x1` tapé, la liste, Reprendre sur la carte de nom de `/new`, ou la Mini App) la ramène en Mode Automatique pour toute autre personne, et la réponse le dit : « ♻️ x1 a repris en Mode Automatique : le Mode Accès complet est réservé aux admins. »
- **Dans sa propre discussion privée, une personne peut utiliser l'Accès complet quel que soit son rôle** (un `user` compris ; pas `read_only`, qui n'envoie pas de messages ; un rôle personnalisé a besoin de `can_prompt` et de `can_approve`, puisque l'Accès complet répond à toutes les demandes d'autorisation de Claude Code). Toutes les voies vers l'Accès complet ci-dessus y marchent pour elle : `/mode full`, la carte de mode, `/new !x1`, les cartes de `/new`, `/resume` et la Mini App. Sa propre discussion est le seul endroit où cela s'applique : dans un groupe, et dans la discussion privée de n'importe qui d'autre, l'Accès complet demande toujours un admin. Ses nouvelles sessions démarrent en Mode Automatique, comme celles de tout le monde, jusqu'à ce qu'elle choisisse l'Accès complet dans `/settings` → Nouvelles sessions. Rien d'autre de ce que fait un admin ne s'ouvre à elle (`/update`, le bouton Installer de la fonction vocale et les `/settings` d'un groupe restent réservés aux admins). Distribuer l'Accès complet ainsi est sans danger : il lance Claude Code avec `--dangerously-skip-permissions`, qui ne saute que les demandes d'autorisation de Claude Code lui-même. Le hook PreToolUse d'aipager tourne toujours sur chaque appel d'outil et applique les règles du rôle du membre (le socle de sécurité, l'enfermement dans son dossier, les règles de refus, les fichiers d'identifiants) exactement comme en Demander et en Automatique.
- **Installer demande un admin** (UPDATE) : les boutons Installer et Redémarrer de la fonction vocale, comme ceux de `/update`.
- **Changer les `/settings` d'un groupe demande un admin.** Tout le monde peut regarder.
- **Une carte de confirmation appartient à la personne qui l'a demandée.** Dans un groupe, les cartes de confirmation de Terminer (`/kill`, ⋮ Terminer la session), Redémarrer, Supprimer et du changement de mode, les listes de sessions que montrent `/kill`, `/restart`, `/delete`, `/mode`, `/stop`, `/rename` et `/diff`, et la carte « Reprendre x1 en : » qu'ouvre un appui sur Reprendre (ses boutons de mode) ne répondent qu'à la personne qui a envoyé la commande ou touché le bouton qui les a ouvertes. L'appui de n'importe qui d'autre, Annuler compris, reçoit « Cette carte appartient à @alice. Envoyez /kill x2 pour avoir la vôtre. » (avec la commande de cette carte) et ne change rien. La carte de `/mode` elle-même (dans quel mode est une session) est à tout le monde : un changement touché dessus ouvre une confirmation qui appartient à la personne qui l'a touché. Les cartes de `/new` ne répondent qu'à la personne qui a envoyé `/new`. La personne à qui appartient une carte est retenue jusqu'à ce que la carte soit utilisée ou annulée, même après un redémarrage d'aipager (les 4 096 cartes les plus récentes). La carte de nom de `/new` et la question de nom de `/rename` font exception : elles expirent de toute façon au bout de 10 minutes, donc après un redémarrage, renvoyez la commande.
- **Un bouton ne marche que dans la discussion de sa propre session.** Touché n'importe où ailleurs, il répond « Ce bouton appartient à une autre discussion. » et ne fait rien, et Reprendre reprend toujours la session que nomme le bouton, jamais une session d'une autre discussion qui porte le même nom.
- **Les anciens boutons ne changent rien.** Un bouton resté d'une tâche précédente, ou d'avant que la session se termine et revienne, n'agit pas sur ce qui tourne maintenant ; il le dit (voir [commandes → boutons anciens](commands.md#stale-buttons)).

## <a id="what-everyone-sees"></a>Ce que tout le monde voit

**Les réponses à Claude.** Chaque Autoriser, Refuser et réponse est publié dans la discussion, sous la carte de la session, avec la personne qui l'a fait :

```
✅ x1 · Autorisé · @alice · Bash: ls -la /tmp
🚫 x1 · Refusé · @bob · WebFetch: https://example.com
✓ x1 · Répondu · @carol · Which database? → Postgres
```

**Qui a changé une session.** Un appui sur un bouton n'est vu que par la personne qui touche, donc dans un groupe chaque résultat qui change une session nomme la personne qui l'a fait :

```
⚠️ x1 · @bob l'a arrêtée
⏹ @bob a terminé x2
🔄 @alice a redémarré [x1].
🗑️ @carol a supprimé [x3].
⚙️ @alice a passé la session en 🤖 Mode Automatique.
@alice a modifié ceci.          (sous /settings)
```

La carte « x1 est prête » nomme qui a changé son mode ou son modèle (« @bob a changé le modèle pour Opus. »). Dans une discussion privée, ces lignes de résultat ne nomment personne. Un changement fait dans la Mini App est publié dans la discussion avec « ... depuis la Mini App » (un Arrêter fait depuis elle nomme aussi qui, sur la carte de la session).

**Un outil bloqué par une règle.** Une tâche s'arrête avec « 🛑 x1 · Bloqué par une règle de sécurité : Write is in this role's deny_tools (arrêtée) ». Si Claude Code demande une autorisation pour un outil que la personne dont le message a lancé la tâche en cours n'a pas le droit d'utiliser, le bot répond Refuser lui-même, n'affiche aucune demande, et publie `⛔ x1 · Edit bloqué pour @bob (rôle user)` avec le résumé de l'appel en dessous, en nommant cette personne. Une tâche lancée par les messages de plusieurs personnes est bloquée quand l'une d'elles n'a pas le droit d'utiliser l'outil, et nomme celle-là. Une tâche tapée dans le terminal est la vôtre et n'est jamais bloquée par les règles d'un membre, sauf si un message Telegram l'a rejointe : elle est alors vérifiée avec les règles de la personne qui a envoyé le dernier message de la session (la propre liste du groupe quand on ne le sait pas). Quand aipager ne sait pas à qui est la tâche (elle tournait déjà quand aipager a redémarré), il utilise les règles de la personne qui a envoyé le dernier message de la session, et la propre liste du groupe quand on ne le sait pas non plus.

**L'état des messages.** 👀 sur un message veut dire qu'il a été envoyé à la session (ou qu'il est retenu jusqu'à ce qu'il puisse l'être) ; 👍 veut dire que Claude l'a pris ; 🤷 veut dire qu'il a été abandonné avant que Claude le prenne. `/whoami` montre votre nom, votre rôle et vos règles dans cette discussion.

**La barre d'état épinglée.** Le groupe a sa propre barre d'état épinglée (voir [commandes → la barre d'état épinglée](commands.md#the-pinned-status-bar)), qui ne liste que les sessions de ce groupe, si le bot est un admin qui a le droit d'épingler. Sans ce droit, la barre est sautée pour le groupe : le message est supprimé plutôt que laissé non épinglé dans l'historique.

**L'audit sur le disque.** `~/.claude/aipager-audit.jsonl` enregistre chaque décision avec `user_id`, `username` et la discussion, pour que vous puissiez reconstituer plus tard ce que chaque personne a fait. Il est toujours écrit en anglais, quelle que soit la langue de la discussion.

**La langue.** Un groupe a une langue pour les messages, les cartes et les claviers d'aipager lui-même, choisie par un admin avec `/language` ; tant qu'un admin n'en a pas choisi, il est en anglais. Elle ne suit jamais l'app Telegram de qui que ce soit. Ce qu'une seule personne voit, la petite notification après un appui, est dans la propre langue de cette personne : son choix dans sa discussion privée avec le bot, sinon la langue de son app Telegram pour quelqu'un arrivé après cette version, sinon la langue du groupe. Seuls les textes d'aipager changent ; Claude répond dans la langue dans laquelle chaque personne lui écrit. Voir [Langues](commands.md#languages).

## <a id="where-messages-go"></a>Où vont les messages

- **Chaque session appartient à une discussion, et tout ce qui la concerne y va** : ses cartes, ses réponses, ses demandes d'autorisation, ses fichiers et ses notifications. Un message dans une discussion ne va jamais à une session d'une autre discussion, et le nom d'une session ne marche que dans sa propre discussion : taper le nom d'une session d'une autre discussion répond « Aucune session ne porte ce nom ». Une session lancée pendant qu'aipager tournait sans discussions configurées reçoit sa discussion dès qu'`aipager config` les ajoute ; tant qu'une session n'a pas de discussion, aucune discussion ne la liste ni ne peut agir sur elle.
- **Les sessions lancées dans le terminal** (`aipager session <name>`) appartiennent à la discussion privée du propriétaire : la discussion privée dont le membre a le rôle `owner` (un rôle avec `bypass_safety`). Avec plusieurs discussions privées et aucune du propriétaire, c'est la première discussion privée de la liste ; sans aucune discussion privée, la première discussion. Elles ne vont jamais dans un groupe sur une installation qui a une discussion privée.
- **Une mise à niveau en supergroupe est suivie automatiquement** (voir [Quand Telegram met le groupe à niveau](#when-telegram-upgrades-the-group)).

## <a id="aipageryaml-schema-team-parts"></a>Structure d'`aipager.yaml` (parties pour l'équipe)

```yaml
schema_version: 3
bot_token: "…"

scopes:
  - kind: dm
    chat_id: 12345       # votre propre discussion privée : son identifiant est votre identifiant d'utilisateur
    label: owner DM
    members:
      - id: 12345
        label: alice
        role: owner
  - kind: group
    chat_id: -100123456789
    label: team
    deny_tools: [Write, Edit]   # optionnel : la réponse à « Block file edits »
    root: /home/alice/aipager-users/group-team   # le dossier racine des membres
    members:
      - id: 12345        # identifiant d'utilisateur Telegram (PAS un nom, PAS un identifiant de discussion)
        label: alice     # comment la personne apparaît dans la discussion (@alice)
        role: owner
      - id: 67890
        label: bob
        role: user
        deny_tools: [WebFetch]  # optionnel, par membre
      - id: 11111
        label: charlie
        role: read_only
```

Une discussion privée et des discussions de groupe coexistent : le bot sert chaque discussion de la liste, avec les sessions de chaque discussion séparées. `aipager config` écrit ce fichier ; vous n'avez jamais besoin de le modifier.

## <a id="how-rules-work"></a>Comment les règles fonctionnent

`~/.config/aipager/policy.yaml` définit les règles de chaque rôle. Vérifiez-le avec `aipager policy validate`. Les champs acceptés pour chaque rôle (en plus de ceux de [Rôles personnalisés](#custom-roles)) :

- `deny_tools` : les noms d'outils refusés sans demander (par exemple `Write`, `Edit`, `Bash`, `WebFetch`).
- `allow_tools` : s'il n'est pas vide, une liste d'autorisation : tout le reste est refusé pour ce rôle.
- `deny_bash_patterns` : des motifs comparés aux entrées de `Bash`.
- `deny_paths_no_access` / `deny_paths_no_write` : des règles de chemins. Pour `user`, `read_only` et chaque rôle que vous définissez sans `bypass_role_denies`, `deny_paths_no_access` commence avec les fichiers d'identifiants listés pour **user** plus haut ; le définir remplace cette liste, donc recopiez dans la vôtre ceux que vous voulez garder (une tâche en cours qu'un autre message rejoint y est de nouveau tenue jusqu'à sa fin). Une règle sans `/` ni `~` au début (`**/.env`) correspond partout, donc tant qu'il y en a une, chaque `Grep`/`Glob` d'un rôle restreint est refusé (n'importe quelle recherche pourrait lire un tel fichier), ce qui arrête cette tâche.

Les `deny_tools` d'une discussion et ceux d'un membre dans `aipager.yaml` s'ajoutent à ceux du rôle, et les `allow_tools` d'un membre s'ajoutent à la liste d'autorisation du rôle. Tout cela ne s'applique qu'aux rôles sans `bypass_role_denies` : cela ne touche ni un propriétaire ni un admin.

Les fichiers de `~/.config/aipager/policy.d/*.yaml` sont lus après `policy.yaml`, dans l'ordre de leurs noms, au même format : un champ de rôle défini dans un fichier plus tardif remplace la valeur précédente, et une liste `safety:` ne fait qu'ajouter. `aipager policy validate` les vérifie tous.

Sous tous les rôles se trouve un **socle de sécurité** (chemins protégés et motifs de commandes) dont seul `owner` est exempté : les admins y sont tenus aussi. C'est la liste intégrée d'aipager plus la section `safety:` de `policy.yaml`, qui ne peut qu'ajouter :

```yaml
safety:
  deny_paths_no_access: [/srv/secrets/**]   # ni lire, ni écrire, ni chercher
  deny_paths_no_write: [~/notes/**]         # lire oui, écrire non
  deny_bash_patterns: ['\bcurl\b']          # les commandes Bash bloquées
```

Chaque tâche de chaque rôle sauf `owner` le reçoit en entier, tout comme les tâches qu'aipager ne peut attribuer à personne ; `aipager doctor --safety-check` liste ce qui est en vigueur. Après un rechargement à chaud, un changement s'applique aux messages envoyés après le rechargement ; une tâche déjà en cours, ou un message qui attend déjà dans la file, garde l'ancienne liste (arrêtez cette tâche pour appliquer le changement tout de suite). Un chemin de `deny_paths_no_access` de cette section sans `/` ni `~` au début correspond partout, donc tant qu'il y en a un, chaque `Grep`/`Glob` de chaque rôle sauf `owner` est refusé (`aipager doctor` prévient à ce sujet). Les chemins protégés s'appliquent aux outils de fichiers de Claude Code (`Read`, `Write`, `Edit`, `MultiEdit`, `NotebookEdit`, `LSP`, `Grep`, `Glob`) ; le `Grep`/`Glob` d'un rôle restreint doit aussi rester dans le dossier de la session (une recherche de `aipager/**` dans `~/.config` est refusée). Les outils comme `WebFetch` ou `SendFile` ne sont pas vérifiés par chemin. Une tâche Telegram qu'aipager ne peut attribuer à aucun expéditeur reçoit le socle, qui est au moins aussi strict que `user`. Réessayer tourne avec le rôle de la personne qui le touche si c'est elle qui a envoyé le message réessayé, et avec le socle sinon. Les motifs de commandes ne comptent que pour un rôle qui a Bash, et là ils sont un filtre, pas un mur ; voir [Modèle de sécurité → comment les règles s'appliquent en équipe](security.md#team-mode-enforcement).

## <a id="privacy-considerations"></a>Questions de confidentialité

- Le bot n'écoute que **les discussions configurées**. Ajouter le bot à un autre groupe ne l'y active pas : il n'y répond jamais. Il note qui s'adresse à lui là-bas (l'identifiant, le nom d'utilisateur et le nom de la personne, et l'identifiant et le titre du groupe) dans `~/.claude/aipager-pending-users.json`, pour qu'`aipager config` puisse ajouter ce groupe. Personne d'autre que vous ne peut lire ce fichier, et il garde les 200 personnes les plus récentes vues dans les 30 derniers jours.
- Les membres `read_only` **peuvent lire** les messages, les réponses et les entrées des outils. Ils ne peuvent pas agir, mais ils voient tout dans le groupe. Si un travail doit rester caché à quelqu'un, cette personne ne devrait pas être dans le groupe.
- Sur une installation avec une discussion privée, vos sessions de terminal et celles de votre discussion privée n'apparaissent jamais dans un groupe (voir [Où vont les messages](#where-messages-go)). Une session de terminal qu'un aipager plus ancien a déjà mise dans le groupe reste au groupe : terminez-la, supprimez-la des sessions terminées du groupe et relancez-la.
- `aipager.yaml` a les droits 0600 (le propriétaire seulement). aipager ne règle pas les droits de `policy.yaml` : gardez-le vous-même pour le propriétaire seulement (`chmod 600 ~/.config/aipager/policy.yaml`).
- Le journal d'audit (`~/.claude/aipager-audit.jsonl`) n'a pas de droits à lui : il est aussi privé que votre dossier `~/.claude`.
- Les [bots observateurs](observers.md) reçoivent les notifications de toutes les discussions que sert le bot, groupes compris : n'en configurez un que pour une discussion à qui vous confieriez tout cela.

## <a id="revoking-a-user"></a>Retirer l'accès d'un utilisateur

1. Lancez `aipager config` → Edit a member → la discussion → la personne → Remove member. Pour lui retirer le droit d'envoyer des messages tout en la laissant lire, utilisez Set role et choisissez `read_only`. (Pour la rajouter plus tard : Edit a scope → le groupe → Add a member.) Pour la discussion privée d'une personne, terminez ses sessions, puis Edit a scope → sa discussion privée → Remove this scope.
2. L'assistant recharge aipager à chaud ; il dit "Scopes reloaded live". Tout de suite, ses messages retenus sont abandonnés (🤷 et une réponse « Pas envoyé à Claude ») et une tâche à elle qui tourne encore est tenue au socle restreint jusqu'à sa fin. Envoyez `/stop` pour arrêter cette tâche tout de suite.
3. Retirez-la aussi du groupe Telegram. Tant qu'elle y reste, elle peut encore lire tout ce qui y est publié : cartes, réponses, entrées des outils et demandes d'autorisation.

Vérifiez ce que dit l'assistant après l'étape 1. S'il dit de redémarrer aipager, il n'a pas pu le joindre : redémarrez-le (`aipager service stop` puis `aipager service start`), parce que jusque-là l'ancienne liste des membres reste en mémoire. S'il dit "Not applied", corrigez le problème qu'il nomme : aipager garde la configuration précédente jusque-là. Après avoir modifié `aipager.yaml` à la main, envoyez vous-même le rechargement (voir [Recharger sans redémarrer](#live-reload)).

## <a id="related-docs"></a>Pages liées

- [Architecture](architecture.md) : comment le processus fonctionne.
- [Commandes du bot](commands.md) : référence de l'interface.
- [Modèle de sécurité](security.md) : limite de confiance, liste des menaces.
- [Dépannage](troubleshooting.md) : référence d'`aipager doctor`.
