# Rapports de problèmes : ce que contient un rapport

<!-- i18n-source: docs/problem-reports.md @ 6fc084f -->

> Cette page est une traduction de la page anglaise ; si les deux ne disent pas la même chose, c'est la version anglaise qui fait foi. ([English](../../problem-reports.md))

Quand aipager rencontre un bug, vous pouvez envoyer un rapport de problème à la personne qui le maintient, en un seul appui. Vous voyez tout le rapport avant le moindre envoi, rien ne quitte votre ordinateur tant que vous ne l'avez pas dit, et un rapport ne contient jamais vos discussions, vos messages, vos noms, vos chemins ni vos tokens. Il est construit uniquement à partir d'une liste fixe de champs : des versions, des nombres, des dates, des réponses oui/non, des choix dans des listes fixes, des endroits du code d'aipager lui-même et les noms `ap1-` de ses bugs, plus une note courte, seulement si vous en écrivez une.

## <a id="how-to-send-one"></a>Comment en envoyer un

Seul le propriétaire de l'installation d'aipager peut envoyer un rapport depuis Telegram : la personne de la propre discussion privée de l'installation avec le bot (l'opérateur ; avec plusieurs discussions de ce genre et pas un seul propriétaire, il n'y a ni bouton Signaler un problème ni proposition automatique, et `aipager report` dans un terminal marche toujours). La Mini App a sa propre page de rapport ; toutes les autres façons de faire depuis Telegram ouvrent la même carte d'aperçu dans votre discussion privée avec le bot :

- **`/help`** : quand le propriétaire l'envoie, le texte d'aide est accompagné d'un bouton « 🐞 Signaler un problème ». Dans un groupe, seul l'appui du propriétaire fait quelque chose, et l'aperçu s'ouvre dans la discussion privée du propriétaire, jamais dans le groupe.
- **La Mini App** : l'onglet Paramètres, « Signaler un problème » (quand l'app est ouverte sur votre discussion privée), ouvre une page dans l'app. Votre note est en haut (optionnelle, jusqu'à 500 caractères, avec un compteur). En dessous se trouve un court résumé du rapport : les versions, la configuration, les erreurs (touchez-en une pour voir ses endroits dans le code) et **Plus de détails**. **Afficher le rapport exact** montre le texte exact qui sera envoyé, votre note comprise, et il suit ce que vous tapez. **Envoyer le rapport** (le bouton de Telegram en bas) envoie exactement cela, une seule fois, et la page montre ensuite la référence avec un bouton **Copier**. Retour n'envoie rien. Si aipager nettoie votre note (des espaces aux extrémités, des caractères invisibles, ce qui dépasse 500 caractères), la page montre la note nettoyée et rien n'est envoyé tant que vous n'avez pas de nouveau touché Envoyer le rapport.
- **La notification de limite de mémoire** : quand le hook d'aipager manque de mémoire, la notification dans la discussion a un bouton « 🐞 Signaler ceci ».
- **Une proposition automatique** : rarement, aipager propose d'envoyer un rapport sur un bug qu'il a remarqué (voir [Quand aipager propose un rapport](#when-aipager-offers-a-report)).
- **Dans un terminal** : `aipager report` affiche un rapport et demande `Send this report? [y/N]`. Il contient les mêmes sortes de champs, mais moins de faits sur l'aipager en cours d'exécution (le mode, les discussions, les fonctions, le temps de fonctionnement, les sessions et les détails de Claude Code restent inconnus ou vides). `aipager report --note "what you were doing"` ajoute une note. Sans terminal (un script, un pipe, le shell d'une session Claude Code), il affiche le rapport et ne l'envoie jamais.

La carte d'aperçu montre le rapport exactement tel qu'il sera envoyé : le JSON dans un bloc replié que vous pouvez déplier ou, quand il est trop long pour un seul message, sous forme d'un fichier `report.json` en réponse à la carte. Ses boutons :

- **📤 Envoyer** : envoie ce rapport exact, une seule fois. La carte dit ensuite `Envoyé. Référence : ap1-...`.
- **✏️ Ajouter une note** : votre prochain message dans cette discussion dans les 10 minutes devient la note du rapport (coupée à 500 caractères), et la carte remontre le rapport avec la note avant que vous l'envoyiez. Faire autre chose à la place (une commande, un autre bouton, une photo, une action dans la Mini App) ferme la question de la note sans ajouter de note, et après 10 minutes votre prochain message va à Claude comme d'habitude. La question a **‹ Retour**, qui revient à l'aperçu sans note, et Annuler.
- **✖️ Annuler** : rien n'est envoyé.

Un aperçu reste ouvert 24 heures, et les 5 plus récents restent ouverts (en ouvrir un 6e ferme le plus ancien). Après un redémarrage d'aipager, une ancienne carte ne peut plus être envoyée : rouvrez Signaler un problème pour en avoir une nouvelle. Un rapport ouvert dans la Mini App peut être envoyé pendant 24 heures (les 3 plus récents) ; après un redémarrage, la page en charge un nouveau.

## <a id="what-a-report-holds"></a>Ce que contient un rapport

Chaque partie, avec des mots simples. Chaque valeur est comparée à la liste des valeurs permises avant que l'aperçu soit affiché et de nouveau avant l'envoi ; tout ce qui ne convient pas est remplacé par `<invalid>` ou laissé de côté, jamais envoyé tel quel.

**À propos du rapport**
- `schema` : le format du rapport (`aipager-report/1`).
- `trigger` : `manual` (vous l'avez ouvert) ou `auto` (une proposition automatique).
- `day` : la date, en UTC (sans l'heure).
- `note` : seulement si vous en avez ajouté une : vos mots, exactement comme l'aperçu les montrait (au plus 500 caractères, sans caractères invisibles).

**aipager et l'ordinateur**
- `aipager` : la version ; comment il a été installé (`pipx`, `uv`, `brew`, `pip`, `editable`, `nix`, `snap`, `docker`, `system`, `foreign` ou `unknown`) ; d'où (le dépôt de paquets, un dossier local, une copie git) ; si `aipager update` peut le mettre à jour.
- `python` : la version de Python et s'il s'agit de CPython ou de PyPy.
- `os` : le système (`linux`, `darwin`, `windows`, `freebsd`), le type de processeur, la distribution Linux tirée d'une liste fixe et son numéro de version, la version majeure.mineure du noyau, le type de conteneur s'il y en a un (`docker`, `lxc`, `wsl`, `podman`), et si aipager est configuré comme service systemd ou launchd.
- `deps` : les versions des bibliothèques dont aipager dépend (python-telegram-bot, httpx, aiohttp, PyYAML, rich, questionary, dtach-bin).
- `claude_code` : la version de Claude Code, combien d'installations de Claude Code ont été trouvées, et comment il se connecte (`env`, `file`, `keychain`, ou pourquoi on n'a pas pu le savoir). Jamais l'identifiant de connexion lui-même.

**Votre configuration, en nombres**
- `config` : le mode (`personal`, `team`, `scope`) ; combien de discussions privées et combien de groupes sont configurés ; combien de rôles personnalisés existent (leurs noms ne sont pas inclus) ; quelles fonctions sont activées (Mini App, son type de tunnel, messages vocaux, aperçus des diffs, résumés enrichis).
- `runtime` : depuis combien de temps aipager tourne, sous forme de tranche (`<10m`, `10m-1h`, `1-24h`, `1-7d`, `>7d`) ; combien de sessions sont en cours et au travail ; combien de fois aipager s'est arrêté de façon inattendue ces 7 derniers jours et comment il s'est terminé la dernière fois (`clean`, `crash`, `reboot`).
- `doctor` : les résultats (`ok`, `warn`, `fail`) d'une liste fixe de vérifications de santé, quand ils sont disponibles sans relancer les vérifications (les rapports du bot et du terminal laissent ce champ vide).
- `flood` : l'état des limites de Telegram additionné sur toutes les discussions : les blocages des 7 derniers jours, si une discussion est en ce moment réduite au silence, en mode minimal ou ralentie, et la part du budget horaire de la discussion la plus active, sous forme de tranche. Aucun identifiant de discussion.

**Ce qui s'est mal passé**
- `counters_24h` : combien de fois chaque élément d'une liste fixe est arrivé ces dernières 24 heures (par exemple la surveillance de la carte d'activité qui redémarre, une sorte d'erreur de Telegram, le hook qui atteint sa limite de mémoire, une erreur de réseau ou de disque). Seulement les nombres.
- `log_digest_24h` : pour les 15 endroits du code d'aipager qui ont le plus écrit un avertissement ou une erreur dans le journal ces dernières 24 heures, l'endroit (`aipager/state.py:1234`), le niveau et le nombre de fois. Jamais le texte du journal.
- `errors` : jusqu'à 10 erreurs qu'aipager a enregistrées, chacune avec :
  - `fingerprint` : un nom `ap1-` pour le bug (une empreinte du type de l'erreur et des noms de jusqu'à 8 endroits du code d'aipager, jamais d'un texte ni d'une donnée) ;
  - où elle est arrivée (aipager lui-même, le hook, la ligne d'état, une commande, la Mini App), comment elle a été attrapée, et s'il s'agit d'un bug ou seulement de quelque chose d'inhabituel (`tier`) ;
  - pour une erreur écrite dans le journal par aipager, le nom du logger (un module d'`aipager`, `asyncio` ou une partie de `telegram.ext`) ;
  - le nom du type de l'exception (par exemple `builtins.KeyError`, ou `<other>` pour une exception venue d'en dehors d'une liste fixe de paquets), les types de jusqu'à trois causes, et un nom de numéro d'erreur comme `ENOSPC` ;
  - pour une erreur du hook, l'événement de hook et l'outil (un outil intégré par son nom, tout outil MCP sous le nom `mcp`) ;
  - combien de fois, quels jours et sur quelles versions d'aipager elle est arrivée ;
  - jusqu'à 12 endroits du code d'aipager lui-même par lesquels elle est passée (fichier, ligne, nom de fonction), et jusqu'à 8 noms de paquets extérieurs par lesquels elle est passée, tirés d'une liste fixe.

**Jamais inclus** : vos discussions, vos messages, les réponses de Claude, les noms des sessions, les chemins de dossiers ou de fichiers, les tokens ou les clés, les identifiants de discussion ou d'utilisateur, les noms d'utilisateur, le nom de l'ordinateur, le texte de tout message d'erreur, et les lignes du journal. Les champs ci-dessus n'ont pas de place pour eux, et un rapport est vérifié contre cette liste avant d'être affiché et avant d'être envoyé.

## <a id="where-it-goes"></a>Où il va

Un rapport va dans la boîte de réception d'erreurs privée de la personne qui maintient aipager, sur [Sentry](https://sentry.io), sans votre nom : pas de compte et pas d'identifiant d'installation. Comme toute requête web, il vient de l'adresse internet de votre ordinateur, que GitHub et Sentry peuvent voir ; la boîte de réception est réglée pour ne pas l'enregistrer (le réglage « Prevent Storing of IP Addresses » de Sentry). aipager l'envoie lui-même en une seule requête HTTPS (il n'utilise pas la bibliothèque de Sentry, qui collecte beaucoup plus de choses) : le rapport que vous avez vu part comme pièce jointe `report.json`, à côté d'un court résumé qui sert à regrouper les rapports (la version, comment aipager a été installé et s'il s'agit d'une installation de développement, le système, les versions de Python et de Claude Code, le déclencheur, et l'empreinte, le type et les endroits du code de la première erreur). La requête ne nomme que `aipager/<version>`.

Après l'envoi, la carte (ou le terminal) montre une **référence** : l'empreinte `ap1-` de la première erreur ou, pour un rapport sans erreur, son identifiant d'événement de 32 caractères. Si vous ouvrez aussi une [issue sur GitHub](https://github.com/dev-aly3n/aipager/issues), indiquez la référence pour que la personne qui maintient aipager trouve votre rapport. Ne collez pas de journaux ni de sortie d'`aipager doctor` dans une issue : ils contiennent l'identifiant de votre discussion, les chemins de votre dossier personnel et le début de vos messages.

Juste avant l'envoi, aipager lit [report-endpoint.json](https://github.com/dev-aly3n/aipager/blob/main/report-endpoint.json) dans le dépôt d'aipager sur GitHub pour savoir où envoyer (un aipager en cours d'exécution garde une bonne copie pendant un jour ; une lecture ratée est retentée au prochain envoi, et chaque `aipager report` le relit). Cela permet à la personne qui maintient aipager de changer la clé Sentry, de couper les rapports pour tout le monde, ou d'empêcher les versions en dessous d'un certain numéro d'envoyer (une version qui ne connaît pas son propre numéro compte comme en dessous), sans publier de nouvelle version. Si GitHub est injoignable, ou si le fichier n'est pas un fichier qu'aipager comprend, aipager utilise la clé intégrée à cette version ; un fichier lisible qui dit de couper est toujours respecté.

## <a id="limits"></a>Limites

- Au plus 5 tentatives d'envoi par jour (UTC) depuis un ordinateur, manuelles et automatiques ensemble, et une tentative qui a échoué compte aussi. La suivante dit `On peut envoyer au plus 5 rapports par jour depuis un ordinateur. Réessayez demain.`
- Rien n'est retenté en arrière-plan : si un envoi échoue, la carte dit `Impossible d'envoyer pour le moment. Rien n'est perdu ; réessayez plus tard.` et garde son bouton Envoyer.

## <a id="when-aipager-offers-a-report"></a>Quand aipager propose un rapport

aipager remarque ses propres bugs et, rarement, propose d'envoyer un rapport à leur sujet dans votre discussion privée : `aipager a rencontré une erreur interne N fois (TypeError dans la carte d'activité). Un rapport aide à corriger le problème plus vite. ...` avec **Voir le rapport**, **Pas maintenant** et **Ne plus me demander**. Voir le rapport ouvre la carte d'aperçu avec seulement ces erreurs ; rien n'est envoyé tant que vous n'y avez pas touché Envoyer.

Une proposition ne vient que si tout ceci est vrai :
- C'est un vrai bug d'aipager : une erreur dans le code d'aipager lui-même arrivée au moins deux fois, à au moins 10 minutes d'écart, ou tout de suite quand aipager a planté alors qu'il tournait comme service (un `aipager start` au premier plan qui est tué, par exemple en fermant son terminal, est seulement compté). Les erreurs que le hook, le programme de la ligne d'état ou une commande signalent à aipager (y compris le hook qui manque de mémoire) doivent en plus s'étaler sur au moins un jour, donc une seule ne déclenche jamais de proposition ; le bouton Signaler ceci de la notification est là pour elles. Ce qui n'est pas la faute d'aipager (les limites de Telegram, le réseau, un disque plein) ne déclenche jamais de proposition ; c'est seulement compté pour un rapport que vous envoyez vous-même.
- Au plus une proposition tous les 3 jours, avec tout ce qui est en attente dans cette seule proposition.
- Pas dans les 48 premières heures après le tout premier démarrage d'aipager sur cet ordinateur (les mises à jour ne remettent pas ce compteur à zéro), et pas dans la première heure après un redémarrage d'aipager.
- Seulement quand rien n'est en cours ni en attente d'envoi depuis 2 minutes, et que vous avez utilisé votre discussion privée avec le bot dans les 30 dernières minutes. Jamais dans un groupe, et jamais pendant que Telegram limite cette discussion.
- Chaque bug n'est proposé qu'une fois par version d'aipager. Après n'importe quelle réponse, il n'est plus proposé sur cette version ; il peut revenir après une mise à jour, seulement s'il se reproduit.
- **Pas maintenant** et **Ne plus me demander** comptent tous les deux comme un non, tout comme une proposition à laquelle vous ne répondez pas dans la journée. **Voir le rapport** remet le compte à zéro, même si vous annulez ensuite. Deux non d'affilée désactivent les propositions automatiques : quand le second est un appui, une ligne le dit ; dans tous les cas, `/settings` l'affiche.
- Jamais pour une installation de développement (aipager installé depuis un dossier local, une copie git, ou en mode editable).

Pour désactiver les propositions automatiques, utilisez `/settings` dans votre discussion privée : **Rapports de problèmes : Désactivé**. Quand deux non les ont désactivées, la même page a **Réactiver les propositions**. Un `reports.json` qui existe mais ne peut pas être lu désactive aussi les propositions automatiques (aipager ne peut plus savoir ce que vous avez répondu avant) ; **Réactiver les propositions** règle cela aussi. Sur un ordinateur sans discussion où demander, mettez `AIPAGER_REPORT_PROMPTS=0` dans l'environnement d'aipager. Signaler un problème et `aipager report` marchent toujours, quels que soient ces réglages.

## <a id="what-is-kept-on-your-machine"></a>Ce qui est gardé sur votre ordinateur

- `~/.local/state/aipager/reports.json` : les erreurs qu'aipager a enregistrées (sous la forme montrée plus haut : noms de types, versions, endroits du code, 50 au plus), les nombres par heure sur 8 jours, le résumé du journal sur 2 jours, comment se sont terminés les derniers arrêts, et l'état des propositions (quand il a proposé pour la dernière fois, les non d'affilée, quels bugs il a proposés sur quelle version). Personne d'autre que vous ne peut le lire.
- `~/.local/state/aipager/report-sends.json` : combien de rapports ont été envoyés ou tentés aujourd'hui.
- `~/.local/share/aipager/install.json` : quand aipager a démarré pour la première fois sur cet ordinateur (pour les 48 heures ci-dessus).
- `~/.local/share/aipager/running.json` : écrit pendant qu'aipager tourne (son numéro de processus, son heure de démarrage, s'il tourne comme service, et l'identifiant de ce démarrage de l'ordinateur), pour distinguer un plantage d'un arrêt propre.

Aucun d'eux ne contient de discussion, de message, de nom, de chemin ni de token. `aipager uninstall` les garde, avec le reste des données d'aipager, pour qu'une réinstallation reprenne là où elle s'était arrêtée ; il liste ce qu'il garde et affiche la commande qui le supprime (voir [`aipager uninstall`](commands.md#command-line-aipager-uninstall)). Pour ne supprimer que ceux-ci, effacez ces quatre fichiers : effacer tout le dossier `~/.local/state/aipager` supprime les deux premiers (et l'état des langues, `languages.json`, avec eux), mais `install.json` et `running.json` doivent être effacés par leur nom (n'effacez pas `~/.local/share/aipager`, qui contient aussi les dossiers des sessions d'aipager).

## <a id="see-also"></a>Voir aussi

- [Dépannage](troubleshooting.md) : pannes courantes et solutions.
- [Modèle de sécurité](security.md) : ce qu'aipager touche et ce qui passe par le réseau.
- [Commandes du bot](commands.md) : `/help`, `/settings` et la ligne de commande.
