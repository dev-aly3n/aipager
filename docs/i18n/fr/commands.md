# Commandes et interface du bot

<!-- i18n-source: docs/commands.md @ 6fc084f -->

> Cette page est une traduction de la page anglaise ; si les deux ne disent pas la même chose, c'est la version anglaise qui fait foi. ([English](../../commands.md))

Comment piloter aipager depuis Telegram. Quatre façons d'entrer : les commandes slash, les boutons du clavier, le tableau de bord de la Mini App et les messages libres (texte, fichiers, messages vocaux).

Le bot n'accepte d'entrées que depuis les discussions configurées ; voir [Modèle de sécurité](security.md) pour la limite de confiance.

## <a id="slash-commands"></a>Commandes slash

Elles sont enregistrées dans `aipager/bot/lifecycle.py`. Le menu des commandes de Telegram les affiche, et il est mis à jour au démarrage d'aipager et à chaque changement de sessions.

| Commande | Arguments | Ce qu'elle fait |
|---|---|---|
| `/start` | - | L'écran d'accueil : les sessions de cette discussion, où va un message ordinaire (`✍️ Les messages vont à x1.`), et `🆕 Nouvelle session`, `⚙️ Paramètres` et, quand cette discussion a une session terminée à ramener, `↩️ Reprendre`. Affiche aussi le clavier fixe. |
| `/help` | - | Un court guide par tâche (démarrer, parler, contrôler, gérer, paramètres et, pour les admins, `/update`), avec l'astuce que toucher une commande l'envoie aussitôt : appuyez longuement dessus (téléphone) ou appuyez sur Tab (ordinateur) pour ajouter du texte avant. Pour le propriétaire de l'installation (la personne de la propre discussion privée de l'installation avec le bot), il a aussi un bouton `🐞 Signaler un problème` : il ouvre dans cette discussion privée un aperçu du rapport de problème exact, avec Envoyer, Ajouter une note et Annuler. Rien n'est envoyé tant que vous n'avez pas touché Envoyer ([ce que contient un rapport](problem-reports.md)). |
| `/app` | - | Ouvre le tableau de bord de la Mini App (sessions, visionneuse de diff, paramètres). |
| `/status` | - | Les sessions de cette discussion sous forme de liste sur laquelle vous pouvez agir. Chacune dit son état en mots (`⚙️ au travail 3:12 · Bash: run tests`, `⏳ a besoin de vous · Bash: make deploy`, `💤 prête`), avec `✍️` sur celle qui reçoit les messages ordinaires, et une deuxième ligne avec le modèle, le contexte, le coût, ce qui est en file (`file d'attente 3 (1 en attente, 2 notes)`), et les agents et commandes en arrière-plan qui tournent. Chacune a une rangée de boutons : `✍️ x1` y envoie vos messages suivants, `⏹ Arrêter` pendant qu'elle travaille, `Répondre` pendant qu'elle vous attend, et `⋮` pour le reste. En dessous : `🆕 Nouvelle`, et `⚫ Terminées (n)` pour les sessions qui se sont terminées (reprendre, supprimer, ou toutes les retirer de la liste ; elles restent dans `/resume`). |
| `/stop [label]` | optionnel | Interrompt la tâche en cours d'une session. Sans nom, il arrête la seule session de cette discussion qui travaille (même si vos messages vont à une autre ; une session dont un agent ou une commande en arrière-plan tourne encore compte comme au travail), demande laquelle avec une liste quand il y en a plusieurs, et dit `Rien ne tourne en ce moment.` quand il n'y en a aucune. `/stop x1` (ou `/x1 stop`) arrête x1 (ou répond `x1 ne fait rien en ce moment.` quand elle ne travaille pas). Supprime aussi les messages en attente et dit combien il en a supprimé. |
| `/now` | - | Envoie tout de suite les messages que Claude garde dans la file d'attente de la session active, au lieu d'après l'étape en cours (les propres touches d'envoi immédiat de Claude Code, Ctrl+X Ctrl+S). Marche à tout moment, y compris quand la discussion est réduite au silence ou en mode minimal ; répond `Rien n'attend dans la file d'attente` quand Claude ne garde rien. Voir [Envoyer tout de suite un message en attente](#send-a-queued-message-now). |
| `/new [name] [first message]` | tout optionnel | Lance une session. `/new x1` la lance tout de suite, et `/new x1 fix the tests` envoie aussi « fix the tests » comme premier message. Envoyé seul (par exemple touché dans le menu), `/new` demande le nom, et le message suivant que vous envoyez est utilisé exactement comme l'argument, donc `/new` puis `x1 fix the tests` donne le même résultat. Dans un groupe, répondez à la carte avec le nom (ou mentionnez le bot : `@aipagerbot x1`), puisqu'un message ordinaire n'y arrive pas au bot. Seulement ce message suivant : si vous faites autre chose avant (une photo, une autre commande, une réponse à un autre message, un modèle du clavier, un autre bouton), cela est traité comme d'habitude et la carte se ferme ; `/status`, `/settings` et les menus du clavier la laissent ouverte. La carte qui demande montre aussi le mode, le modèle et le dossier qu'aura la session, avec des boutons pour les changer avant. Toutes les voies finissent sur la même carte « x1 est prête » : ce qu'a reçu la session, que votre prochain message va à elle, et des boutons en un appui pour passer à un autre mode ou changer de modèle. Les nouvelles sessions démarrent en Mode Automatique pour tout le monde ; changez ce qu'utilise /new dans `/settings` → Nouvelles sessions. Un `!` devant le nom lance la session en Mode Accès complet, pour un admin (les rôles `owner` et `admin`) ou pour une personne dans sa propre discussion privée avec le bot dont le rôle peut envoyer des messages et répondre à Claude (`user`, pas `read_only`) ; pour toute autre personne, elle démarre en Mode Automatique et la carte dit pourquoi. Un nom déjà utilisé propose `↩️ Y aller` (quand cette session tourne) ou `♻️ Reprendre` (quand elle est terminée), `🆕 Remplacer par une nouvelle` et `↩️ Annuler`, seulement pour la personne qui a envoyé `/new`. La carte qui demande un nom expire au bout de 10 minutes sans usage (`⏱ Cette carte de nouvelle session a expiré. Envoyez /new à nouveau.`). Un membre dont le rôle n'a pas `can_manage` (`user`, `read_only`) ne lance des sessions que dans le [dossier racine](groups.md#root-folders) de la discussion : le dossier de la carte est le dossier racine, la page Dossier liste celui-ci et les dossiers directement à l'intérieur (sans la ligne « Dossier par défaut »), Nouveau dossier crée dans celui qui est choisi, et Y aller, Reprendre et Remplacer sur une session qui travaille hors du dossier racine sont refusés (« Cette session travaille hors de votre dossier ~/..., donc votre rôle ne peut ni la reprendre, ni la redémarrer, ni changer son mode. Demandez à un admin. »). |
| `/resume [label]` | optionnel | Reprend par son nom une session déjà terminée, ou ouvre une liste. Une session revient dans le mode où elle était. Une session qui était en Mode Accès complet ne revient en Accès complet que pour un admin ou pour une personne dans sa propre discussion privée ; pour toute autre personne, elle revient en Mode Automatique et la réponse le dit (`♻️ x1 a repris en Mode Automatique : le Mode Accès complet est réservé aux admins.`). Une session qu'un ancien aipager a enregistrée dans son ancien Auto (qui tournait sans vérifications) revient en Mode Accès complet, et une session enregistrée en Ask revient en Mode Demander. Une session terminée reste dans la liste pendant `GONE_SESSION_MAX_AGE_DAYS` jours (par défaut 14), puis quitte la liste des sessions ; la conversation enregistrée de Claude n'est pas touchée. Un membre dont le rôle n'a pas `can_manage` ne peut pas reprendre une session dont le dossier est hors du [dossier racine](groups.md#root-folders) de la discussion (les propriétaires et les admins le peuvent) ; il en va de même pour Redémarrer et pour chaque changement de mode, y compris le changement sans redémarrage entre Demander et Automatique. |
| `/kill [label]` | optionnel | Termine une session, toujours après une confirmation : `⏹ Terminer x1 ? Claude s'arrête et la session prend fin.` avec `[⏹ Terminer] [Annuler]`. Sans nom, il va à la confirmation quand cette discussion a une seule session en cours, sinon à une liste des sessions en cours de cette discussion. Se trouve aussi dans `/status` → `⋮` → `⏹ Terminer la session`. |
| `/restart [label]` | optionnel | Redémarre une session après une confirmation : Claude s'arrête et redémarre, en gardant sa conversation, dans le mode dans lequel aipager l'a lancée la dernière fois ou vers lequel il l'a fait passer. Une session lancée dans le terminal avec `aipager session` revient en Mode Demander, sauf si son mode a été changé depuis Telegram depuis (aipager ne sait pas dans quel mode le terminal l'a lancée, et ne suppose jamais un mode plus ouvert) ; il en va de même pour la reprendre depuis Telegram. |
| `/rename [label] [new]` | optionnel | Donne un nouveau nom à une session (aussi aux sessions terminées). `/rename x1 newname` renomme tout de suite. Avec moins de mots, il demande : directement le nouveau nom quand la commande ne peut désigner qu'une session, sinon une liste (un seul mot après `/rename` ne choisit pas la session, puisqu'il pourrait être l'ancien nom ou le nouveau). Dans un groupe, répondez à la question avec le nom. |
| `/delete [label]` | optionnel | Retire une session terminée de la liste, après une confirmation. |
| `/diff [label]` | optionnel | Affiche le git diff du dossier de travail de la session. Sans nom, il montre la session à laquelle vont vos messages. |
| `/clearqueue` | - | Supprime tous les messages que la session active n'a pas encore pris (ceux que retient aipager et ceux déjà en file dans Claude), sans interrompre la tâche en cours. Répond avec le nombre de messages supprimés. |
| `/mode [label] [ask\|auto\|full]` | optionnel | Affiche le mode d'autorisation d'une session avec un bouton pour passer à chacun des autres : `✍️ x1 est en 🤖 Mode Automatique.` `[💬 Passer en Mode Demander]` `[🔓 Passer en Mode Accès complet]`. Il y a trois modes. **💬 Mode Demander** : Claude demande avant chaque action. **🤖 Mode Automatique** (le mode par défaut) : la vérification de sécurité de Claude Code lui-même laisse passer les actions sûres et arrête les risquées pour que vous décidiez. **🔓 Mode Accès complet** : tout s'exécute sans aucune vérification (`--dangerously-skip-permissions`) ; seul un admin, ou une personne dans sa propre discussion privée avec le bot dont le rôle peut envoyer des messages et répondre à Claude (`user`, pas `read_only`), peut le choisir. Automatique et Accès complet ne changent que les demandes d'autorisation de Claude Code lui-même : le hook d'aipager applique toujours les règles du rôle du membre. `/mode ask`, `/mode auto`, `/mode full` ou `/mode x1 ask` changent tout de suite ; le mot du mode dans la langue de la discussion marche aussi, sans le mot « mode » (`/mode x1 Automatique` pour 🤖 Mode Automatique, `/mode x1 Complet` pour 🔓 Mode Accès complet). Quand une session en cours dans cette discussion porte exactement ce nom (une session appelée `demander`, par exemple), le mot désigne la session : `/mode demander` affiche le mode de cette session, et `/mode x1 demander` change toujours x1. `ask`, `auto` et `full` désignent toujours le mode, dans n'importe quel ordre (`/mode x1 ask` ou `/mode ask x1`) ; seulement quand les deux mots sont des mots de mode et qu'un seul d'entre eux est le nom d'une session en cours, celui-là désigne la session, donc avec une session appelée `full`, `/mode full ask` et `/mode ask full` la passent toutes les deux en Mode Demander, tandis que `/mode full` seul reste le mode. Passer de Demander à Automatique ou l'inverse se fait sans redémarrage et à tout moment (même pendant que Claude travaille) : aipager appuie sur Shift+Tab dans la session comme vous le feriez, la carte dit `⚙️ x1 passe en 🤖 Mode Automatique sans redémarrage.` jusqu'à ce que Claude Code indique le nouveau mode à l'étape suivante, puis affiche le résultat. Si le Mode Automatique n'est pas disponible pour la session (à cause de son modèle ou de son abonnement), la carte le dit et la session reste en Mode Demander. Pas pendant que la session affiche une question ou une demande d'autorisation : répondez-y d'abord. Passer en Accès complet demande un admin (ou une personne dans sa propre discussion privée) et une confirmation, et redémarre la session (la conversation est gardée) ; quitter l'Accès complet la redémarre aussi, tout de suite. Une session lancée en Accès complet redémarre à chaque changement, même après qu'on l'a passée en Demander ou en Automatique dans le terminal, et il en va de même pour une session qui a signalé l'Accès complet depuis son lancement (une session de terminal lancée avec `--dangerously-skip-permissions`) : là, Shift+Tab pourrait la ramener, au fil du cycle, en Accès complet. Sur une session au travail, un redémarrage propose `Arrêter et changer` / `Pas maintenant`. Un membre dont le rôle n'a pas `can_manage` ne peut pas changer le mode d'une session dont le dossier est hors du [dossier racine](groups.md#root-folders) de la discussion, ni sans redémarrage ni avec. Toucher un changement modifie la carte elle-même au lieu d'envoyer un second message. `/perms` est l'ancien nom et marche toujours pareil. |
| `/settings` | - | Comment les sessions de cette discussion s'affichent et répondent : la langue d'aipager, l'affichage de la réponse, les aperçus des diffs, la mise à jour de la carte pendant les tâches longues, la mise en forme, la longueur des réponses, le niveau de vocabulaire ; avec quoi `/new` lance une session ; les paramètres de chaque session ; et, dans la discussion privée du propriétaire, les propositions de rapports de problèmes. Voir [Paramètres](#settings). Dans un groupe, tout le monde peut regarder, et changer un paramètre demande un admin. |
| `/language` | - | La langue qu'utilise aipager pour ses propres messages et boutons dans cette discussion (la même liste que la première ligne de `/settings`). Chaque langue apparaît sous son propre nom : English, puis Español, Русский (russe), Português (Brasil), 简体中文 (chinois simplifié), Français, Deutsch (allemand), 한국어 (coréen), العربية (arabe), Bahasa Indonesia, Українська (ukrainien), Italiano, Türkçe (turc), فارسی (persan) et Tiếng Việt (vietnamien). Une discussion privée qui commence après cette version suit la langue de votre app Telegram ; une discussion qui existait avant reste en anglais tant que vous ne choisissez pas (voir [Langues](#languages)). En espagnol, portugais, français, italien et indonésien, les mots du clavier du bas restent `status`, `stop`, `new` et `kill`, comme leurs commandes. L'allemand et le turc utilisent `Status`, `Stop`, `Neu…` et `Beenden…`, et `Özet`, `Stop`, `Oluştur` et `Sonlandır` ; les mots anglais marchent toujours quand on les tape. En persan et en arabe, les textes d'aipager se lisent de droite à gauche avec des chiffres persans ou arabes ; les noms des sessions, les commandes, les noms des modèles et le code restent tels quels. La liste dit ce qu'elle change : seulement les textes d'aipager, ici et dans la Mini App ; vos sessions Claude ne changent pas, et Claude répond dans la langue dans laquelle vous lui écrivez. Dans un groupe, tout le monde peut regarder, et la changer demande un admin. `language` est un nom de session réservé. |
| `/whoami` | - | Montre qui vous êtes ici : en usage en équipe, votre nom, votre rôle dans cette discussion et les règles qu'il vous donne. |
| `/update` | - | Réservé aux admins (les rôles `owner` et `admin`, ou un rôle avec `can_manage`). Cherche des versions plus récentes d'aipager et de Claude Code, puis met à jour, avec un seul bouton, ce qui en a une. Voir [Mise à jour](#update). |

Le menu `/` montre d'abord vos sessions (`/x1 · Parler à x1`), puis les commandes fréquentes : `new`, `status`, `stop`, `now`, `resume`, `mode`, `settings`, `language`, `help` (et `app` quand la Mini App est activée, dans une discussion privée seulement). `kill`, `restart`, `rename`, `delete`, `diff`, `clearqueue`, `perms`, `whoami` et `update` ne sont pas dans le menu mais marchent quand on les tape ; les actions sur les sessions sont aussi dans `/status` → `⋮`.

**Les commandes qui agissent sur une session** (`/stop`, `/mode`, `/kill`, `/restart`, `/rename`, `/delete`, `/diff`) suivent une règle :

- Avec un nom (`/restart x1`), elles agissent sur cette session de cette discussion (`/rename` a besoin pour cela de l'ancien et du nouveau nom, voir plus haut).
- Sans nom, elles agissent sur la seule session que la commande peut désigner (la seule au travail pour `/stop`, la seule en cours pour `/kill` et `/restart`, la seule terminée pour `/delete`), en passant par la confirmation habituelle quand il y en a une.
- Sinon, elles montrent une liste des seules sessions de cette discussion pour lesquelles la commande a du sens : d'abord celle à laquelle vont vos messages, marquée `✍️`, puis les autres par nom, et `✖️ Annuler`. En choisir une mène à la même carte que taper son nom.
- Dans un groupe, sans nom, elles n'agissent que sur votre propre session actuelle, quand la commande a du sens pour elle ; sinon elles montrent toujours la liste, même avec une seule session, puisqu'elle peut être celle d'un autre membre (voir [usage en équipe](groups.md#talking-to-a-session-in-a-group)).

`/mode` et `/diff` regardent d'abord la session à laquelle vont vos messages.

### <a id="settings"></a>Paramètres

`/settings` (et l'onglet Paramètres de la Mini App, qui montre les mêmes choix) règle comment les sessions de cette discussion s'affichent et répondent. Chaque discussion a les siens ; dans un groupe, tout le monde peut regarder, et changer un paramètre demande un admin. Dans votre propre discussion privée, vous les changez vous-même, quel que soit votre rôle, dans Telegram et dans la Mini App. Une ligne marquée ✅ a une valeur différente de sa valeur par défaut (Affichage de la réponse montre toujours son choix).

| Paramètre | Choix (le choix par défaut en premier) | Ce qu'il fait |
|---|---|---|
| 🌐 Langue | Automatique (langue de Telegram), dans une discussion privée ; English | La langue des messages et des boutons d'aipager lui-même dans cette discussion et dans la Mini App, affichée sous son propre nom (la même liste que `/language`). Elle ne change pas vos sessions Claude : Claude répond dans la langue dans laquelle vous lui écrivez. Sont proposés English, Español, Русский (russe), Português (Brasil), 简体中文 (chinois simplifié), Français, Deutsch (allemand), 한국어 (coréen), العربية (arabe), Bahasa Indonesia, Українська (ukrainien), Italiano, Türkçe (turc), فارسی (persan) et Tiếng Việt (vietnamien) ; Automatique suit la langue de votre app Telegram (voir [Langues](#languages)). |
| 🖼 Affichage de la réponse | Carte + réponse à part ; Réponse dans la carte ; Réponse seule (sans carte) | Comment s'affiche une tâche terminée. Carte + réponse à part garde la carte au-dessus de la réponse, comme trace de la façon dont on y est arrivé ; Réponse dans la carte met la réponse dans la carte ; Réponse seule retire la carte et laisse la réponse seule. Le choix par défaut est Carte + réponse à part (`KEEP_FINISHED_CARD=0` en fait Réponse seule pour les discussions qui n'ont jamais choisi). Voir [réponses](#idle-responses). |
| 📝 Aperçus des diffs | Désactivé ; Activé | Activé publie chaque `Write`/`Edit` comme son propre message de diff sous la carte d'activité. Désactivé garde une carte et une réponse ; la carte liste toujours chaque modification, et la Mini App a une visionneuse de diff. |
| ⏱ Mise à jour de carte (tâches longues) | Activé ; Désactivé | Activé, une carte d'activité est rafraîchie toutes les 10 s après 2 minutes de tâche et toutes les 30 s après 10 (jamais plus lentement, quelle que soit la durée de la tâche), et son horloge avance alors par pas de 30 s ; Désactivé garde le rythme des premières minutes pendant toute la tâche (voir [dépannage](troubleshooting.md#a-long-turns-card-refreshes-less-often)). |
| ✏️ Mise en forme simple | Désactivé ; Activé | Activé demande à Claude seulement du texte simple et des listes à tirets : pas de tableaux, de blocs de code, de titres ni de gras. |
| 📏 Longueur des réponses | Aucune règle ; Très courte ; Courte ; Moyenne ; Longue | Demande à Claude des réponses de cette longueur. |
| 🎓 Niveau de vocabulaire | Aucune règle ; Mots simples ; Normal ; Avancé | Demande à Claude ce vocabulaire. |
| 🆕 Nouvelles sessions | Autorisations (le mode), Modèle, Dossier | Avec quoi `/new` lance une session quand vous ne choisissez pas : Mode Demander, Automatique (le choix par défaut) ou Accès complet, un modèle de la liste des modèles ou le modèle par défaut de Claude Code, et un dossier. Seul un admin, ou une personne dans sa propre discussion privée avec le bot, peut choisir l'Accès complet ici (l'écran d'une discussion privée ne le montre à personne d'autre), et il ne s'applique jamais à quelqu'un qui n'a pas le droit de l'utiliser : ses sessions démarrent en Mode Automatique. |
| 👤 Paramètres de chaque session | une session, puis n'importe quel paramètre ci-dessus sauf Nouvelles sessions | Des valeurs propres à une session (aussi dans `/status` → `⋮` → `👤 Paramètres de la session`) ; ⭐ marque un paramètre qu'elle remplace, et « Valeur par défaut de la discussion » retire ce remplacement. La page de la session dans la Mini App règle les mêmes valeurs. |
| 🐞 Rapports de problèmes | Me demander ; Désactivé | Seulement dans la discussion privée du propriétaire de l'installation. Me demander laisse aipager proposer de temps en temps d'envoyer un rapport sur une erreur interne qu'il a rencontrée plus d'une fois (au plus une fois tous les 3 jours) ; Désactivé ne propose jamais (le bouton Signaler un problème de `/help` marche toujours). Deux propositions refusées d'affilée désactivent les propositions, et cette ligne le dit alors, avec un bouton pour les réactiver. Voir [rapports de problèmes](problem-reports.md#when-aipager-offers-a-report). |

La longueur des réponses, le niveau de vocabulaire et la mise en forme simple arrivent à Claude sous forme d'une courte instruction ajoutée à chaque message envoyé depuis Telegram (par le hook, voir [hooks](hooks.md#userpromptsubmit)), avec les valeurs propres de la session quand elle en a.

Quel que soit l'affichage, chaque carte d'activité se termine par la ligne d'état de sa session (`⏳`/`✅ nom · …`) et chaque réponse commence par sa ligne de résultat (`💬 nom`, plus `· Terminé (…)` quand il ne reste aucune carte terminée pour montrer les chiffres) ; l'affichage Réponse dans la carte empile les deux, chaque ligne dans sa propre section. Dans l'affichage avec carte, la réponse suit exprès la carte terminée d'un instant, pour qu'on voie la carte dire Terminé avant que la réponse arrive en dessous ; réglez ou supprimez cette avance avec `FINISH_CARD_GRACE_SECONDS` (en secondes, par défaut 0,8 ; 0 envoie les deux en même temps). L'affichage décide de la carte de chaque tâche, que des outils aient tourné ou non.

### <a id="per-session-dynamic-commands"></a>Commandes propres à chaque session

Une commande par session en cours, enregistrée à partir de son nom :

| Forme | Ce qu'elle fait |
|---|---|
| `/<label>` | Fait de `<label>` la session à laquelle vont vos messages. La réponse le dit avec son état et son mode (`✍️ Vous parlez maintenant à x1 · 💤 prête · 🤖 Mode Automatique`, puis « Envoyez un message et il arrivera à x1. »), propose **Répondre** quand elle vous attend, et **⋮ Plus** pour son menu. Les chiffres complets sont dans `/status`. Dans un groupe, elle dit à la place « Répondez à un message de x1 (ou mentionnez @aipagerbot) pour lui parler. », puisqu'un message ordinaire n'y arrive pas au bot (voir [usage en équipe](groups.md#talking-to-a-session-in-a-group)). `/x1@aipagerbot`, que Telegram envoie quand vous choisissez `/x1` dans le menu d'un groupe, marche pareil. |
| `/<label> <prompt>` | Envoie `<prompt>` directement à cette session. Elle devient aussi la session à laquelle vont vos messages ordinaires suivants. |
| `/<label> stop` | Interrompt la tâche en cours de cette session. |

Les résultats de `/status` viennent des mêmes données que celles qu'affiche `aipager status` dans le terminal ; la liste des sessions elle-même ne demande pas d'aller-retour avec Telegram.

Sur l'ordinateur, `aipager status` et `aipager session ls` donnent à chaque session un état : `IDLE`, `BUSY`, `WAITING` (une autorisation ou une question vous attend, avec ce que c'est quand on le sait, par exemple `permission: Bash: make deploy`) ou `GONE`. Avec `--json`, chaque session a aussi `waiting_kind` (`permission` ou `question`) et `waiting_summary`, tous les deux `null` sauf si elle attend.

## <a id="languages"></a>Langues

aipager parle English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی et Tiếng Việt, chacune sous son propre nom.

- **Seuls les textes d'aipager changent.** La langue vaut pour les messages, les boutons, les cartes et la Mini App d'aipager. Vos sessions Claude ne changent pas, et Claude répond dans la langue dans laquelle vous lui écrivez. Les noms des sessions, les commandes, les noms des modèles et le code restent tels quels.
- **`/language`** (ou la première ligne de `/settings`) choisit la langue de la discussion où vous êtes. Dans une discussion privée, c'est aussi votre propre langue : les petites notifications que vous voyez dans un groupe et votre Mini App l'utilisent.
- **Automatique** (dans les discussions privées seulement) suit la langue de votre app Telegram, et la suit de nouveau si vous la changez. Une langue qu'aipager n'a pas s'affiche en anglais.
- **Les nouvelles discussions et les anciennes.** Une discussion privée qui commence après cette version suit la langue de votre app Telegram tant que vous n'en choisissez pas une, et son premier message dans une autre langue que l'anglais est suivi d'une courte ligne qui le dit et explique comment changer. Une discussion qui existait avant reste en anglais. Si votre app Telegram est dans une langue que parle aipager, vous recevez une carte qui vous la propose, avec un bouton « Rester en anglais » ; elle vient une seule fois, quoi que vous répondiez.
- **Les groupes** ont une langue, choisie par un admin dans `/language` ; jusque-là ils sont en anglais. La petite notification après un appui est dans la propre langue de la personne qui a touché : son choix dans sa discussion privée avec le bot, sinon la langue de son app Telegram pour quelqu'un arrivé après cette version, sinon la langue du groupe. Voir [usage en équipe](groups.md).
- **De droite à gauche.** En persan et en arabe, les textes d'aipager se lisent de droite à gauche avec des chiffres persans ou arabes.
- **Les mots de mode.** `/mode` accepte le mot du mode dans votre langue (`/mode Demander`), et `ask`, `auto` et `full` marchent toujours. Si une session en cours porte exactement ce nom, le mot désigne la session à la place (voir `/mode` plus haut).
- **La description du bot.** Au démarrage, aipager écrit la description du bot (le texte de l'écran de la discussion vide) et sa description courte dans chacune de ces langues, pour les personnes dont Telegram est dans cette langue. Telegram montre ce texte à la place de celui que vous avez écrit dans BotFather, donc aipager n'en définit aucun tant que vous avez le vôtre (la description et la description courte sont vérifiées séparément). Il n'écrase jamais un texte à vous et ne change jamais le texte anglais ni celui par défaut. Quand son texte actuel est déjà défini dans toutes les langues, un démarrage ne demande rien à Telegram. Sinon, il lit d'abord votre texte de BotFather (une lecture pour la description et une pour la description courte) : si vous en avez un, il s'arrête là, donc avec votre propre texte chaque démarrage coûte ces lectures et rien de plus. Si vous n'en avez pas, il lit chaque langue qui n'a pas encore son texte actuel, donc une langue où vous avez écrit votre propre texte est relue à chaque démarrage. Si vous écrivez une description dans BotFather après qu'aipager a défini la sienne, les lecteurs de ces langues gardent celle d'aipager (voir [dépannage](troubleshooting.md#the-bot-description-is-not-the-one-i-wrote)).

## <a id="the-mini-app"></a>La Mini App

`/app` (et le bouton de menu de Telegram) ouvre un tableau de bord servi par aipager lui-même. Elle est activée par défaut ; gérez-la avec `aipager miniapp enable|disable|status`. Chaque demande est vérifiée avec la signature `initData` de Telegram (voir [sécurité → tunnel de la Mini App](security.md#mini-app-tunnel)). La page ne charge rien d'ailleurs : ni polices, ni images, ni scripts d'autres sites.

- **Les discussions.** L'app montre une discussion à la fois. Si vous faites partie de plus d'une discussion (votre discussion privée et un groupe, par exemple), une ligne de noms de discussions en haut permet de passer de l'une à l'autre ; l'app s'ouvre sur votre propre discussion privée et se souvient de votre dernier choix sur ce téléphone. Tout ce qui est en dessous concerne la discussion choisie, avec votre rôle dans celle-ci. Avec une seule discussion, la ligne n'est pas affichée. Voir [usage en équipe](groups.md).
- **Les sessions.** Une phrase résume la discussion, par exemple « 1 a besoin de vous, 2 au travail, 3 prêtes », avec ce qui a été dépensé en dessous. Chaque session en cours est une tuile avec un anneau de contexte, son dossier, son modèle et son coût ; celles qui travaillent brillent dans la couleur d'accent. Les sessions terminées reposent sur une étagère **Terminées** que l'on peut replier. Le bouton **+** lance une nouvelle session.
- **Ont besoin de vous.** Une session qui attend une demande d'autorisation ou une question se trouve dans un bandeau ambre en haut, qui montre ce qu'elle demande. **Répondre dans la discussion** renvoie cette demande, avec ses boutons, en bas de la discussion, comme le bouton **Répondre** de la barre épinglée, et vous y répondez là. Si c'était la seule session qui attendait, l'app se ferme pour que la demande soit juste devant vous ; sinon elle reste ouverte et dit « Envoyée dans la discussion ». Cela demande le droit d'envoyer des messages à la session, et une copie touchée après que la demande a reçu sa réponse ou a été remplacée dit « déjà répondu ».
- **La page d'une session.** L'en-tête montre l'anneau de contexte, l'état (« Au travail (3:12) », « A besoin de vous (autorisation) », « Prête, dernière activité il y a 5 min », « Terminée ») et un **Arrêter** en un appui (pendant qu'elle travaille ou attend) ou **Reprendre** (une fois terminée). En dessous : la demande qui attend, avec **Répondre dans la discussion**, les derniers appels d'outil sous forme de bande (touchez-la pour la chronologie complète), la dernière réponse, les informations, le contrôle du modèle, les paramètres propres à la session, et les fichiers modifiés et la chronologie. Le menu ⋮ contient toutes les actions, avec les mêmes confirmations qu'avant.
- **Nouvelle session.** Une carte guidée : le nom, puis où (avec des raccourcis **Récents** pour les dossiers qu'utilisent vos sessions, les plus récents d'abord), puis le modèle (avec des raccourcis **Suggérés**), puis **Plus d'options** pour le mode d'autorisation et le style des réponses. Le propre bouton de Telegram en bas dit **Lancer la session**. Pour un membre dont le rôle n'a pas `can_manage`, « où » ne propose que le [dossier racine](groups.md#root-folders) de la discussion et les dossiers directement à l'intérieur (le dossier racine est le choix par défaut, et Nouveau dossier crée dans celui qui est choisi) ; un dossier en dehors est refusé avec la raison, et Reprendre, Redémarrer et le changement de mode d'une session qui travaille en dehors répondent « Cette session travaille hors de votre dossier ~/..., donc votre rôle ne peut ni la reprendre, ni la redémarrer, ni changer son mode. Demandez à un admin. »
- **Paramètres.** D'abord la **Langue de l'app** : votre propre langue pour les textes d'aipager dans l'app et dans votre discussion privée (anglais, espagnol, russe, portugais (Brésil), chinois (simplifié), français, allemand, coréen, arabe, indonésien, ukrainien, italien, turc, persan ou vietnamien ; Automatique suit la langue de votre app Telegram, voir [Langues](#languages)). Chaque personne peut changer la sienne, elle n'est jamais publiée dans une discussion, et elle ne change pas vos sessions Claude. Ensuite les paramètres de la discussion (dans un groupe, seul un admin peut les changer ; dans votre propre discussion privée vous le pouvez, quel que soit votre rôle, comme avec `/settings` là-bas) et, pour un admin, **Mises à jour**, qui reprend [`/update`](#update). Pour le propriétaire dans sa discussion privée, **Signaler un problème** ouvre sa propre page : écrivez une note, vérifiez le rapport, envoyez-le et voyez sa référence sans quitter l'app (voir [rapports de problèmes](problem-reports.md#how-to-send-one)).

L'app suit le thème clair ou sombre de Telegram quand vous le changez, et arrête ses douces animations quand votre téléphone demande moins de mouvement. Tout ce qui est dans la Mini App est aussi accessible depuis la discussion : le menu ⋮ du tableau de bord d'une session contient les mêmes actions.

### <a id="switching-a-running-sessions-model"></a>Changer le modèle d'une session en cours

La page d'une session en cours a un contrôle **Modèle** qui montre le modèle que signale la session (d'après la statusline de Claude Code) et la même liste que proposent le sélecteur de lancement et le clavier des modèles de la discussion. En choisir un tape exactement `/model <name>` dans cette session, la même saisie qu'utilise le clavier des modèles de la discussion, avec la même règle sur qui peut le faire (quiconque peut envoyer des messages à la session). Le contrôle affiche **changement…** jusqu'à ce que la statusline signale un autre modèle, puis l'affiche ; si rien ne change en 15 secondes, il affiche **non confirmé (vérifiez la session)**.

- **Seulement quand la session est prête, pas pendant que Claude travaille ni quand une demande est ouverte.** Claude Code exécute `/model` tout de suite, même en pleine tâche, plutôt qu'après la tâche, donc le changement est refusé jusqu'à la fin de la tâche. Le clavier de la discussion refuse dans les mêmes états, avec les mêmes mots.
- **La question « Switch model? » de Claude Code.** Claude Code pose en général cette question avant un changement, parce que la réponse suivante doit relire toute la conversation. Avec Claude Code 2.1.251 ou plus récent, le hook `PreModelSwitch` d'aipager approuve le changement qu'aipager vient de taper, donc la question n'est pas posée (voir [hooks](hooks.md#premodelswitch)). Tout autre changement pose toujours la question. Avec un Claude Code plus ancien, ou pour un changement vers ou depuis `opusplan`, la question peut encore apparaître. Le changement s'affiche alors comme *non confirmé* et la question attend dans le terminal. Répondez-y là avant d'envoyer quoi que ce soit d'autre à la session, sinon votre prochain message y serait tapé. Pour la même raison, aipager refuse un autre changement de cette session pendant une minute après un changement non confirmé, ou jusqu'à ce que la session signale un nouveau modèle.

## <a id="the-pinned-status-bar"></a>La barre d'état épinglée

Chaque discussion où parle aipager (votre discussion privée, et chaque groupe) a un message épinglé : la barre d'état. La barre de Telegram en haut de la discussion montre le message à partir de sa **première ligne**, avec les lignes mises bout à bout, donc cette ligne dit ce qui a le plus besoin de vous :

| Première ligne | Quand |
|---|---|
| `⏳ jim a besoin de vous - Bash: make deploy` | une session attend une demande d'autorisation, une question ou une demande interactive ; `(+2 autres sessions)` quand d'autres attendent aussi |
| `⚙️ jim (au travail)` / `💤 jim (prête)` | la discussion a une seule session en cours et elle est au travail / prête (`🔄 jim (démarrage)` pendant qu'elle démarre) : cette ligne est toute la barre, à part une ligne de limites |
| `⚙️ 2 au travail · 1 prête` | plusieurs sessions, aucune en attente : combien il y en a dans chaque état (`au travail`, `prête`, `en démarrage`) ; `🔄` quand aucune ne travaille et qu'une démarre, `💤` quand toutes sont prêtes |
| `💤 toutes prêtes` | aucune session en cours |

En dessous, seulement tant qu'elle s'applique, une ligne de limites (`🐢 envoie plus lentement après un avertissement de Telegram` (les six heures après un 429) ou `⏸ les cartes ne sont pas mises à jour pour l'instant (limite horaire de Telegram)` / `(limite de Telegram)` (mode minimal, voir [dépannage](troubleshooting.md#the-hourly-budget))) puis, quand la discussion a plus d'une session en cours, `✍️ Les messages vont à jim` (la session à laquelle va un message ordinaire dans cette discussion, voir plus bas ; pas dans un groupe, où chaque personne a la sienne) et une ligne par session avec son état : `au travail`, `a besoin de vous`, `prête` ou `démarrage`. La barre nomme chaque session une fois : la session en attente que nomme la première ligne n'a pas de ligne à elle, et la première ligne ne liste jamais des noms que les lignes du dessous répètent. Une session dont des agents tournent encore en arrière-plan dit combien sur sa propre ligne (ou sur la ligne unique, avec une seule session) : `💤 jim (prête, 1 agent au travail)`. Les commandes que Claude exécute en arrière-plan sont comptées de la même façon : `(prête, 1 commande en arrière-plan en cours)`, ou `(prête, 2 agents et 1 commande en arrière-plan en cours)` avec les deux. C'est un nombre, jamais leurs noms, donc la barre ne bouge que quand le nombre change. Touchez la barre pour sauter au message.

**Où va un message ordinaire.** Un message qui n'est pas une réponse va à la session avec laquelle cette discussion a parlé en dernier : la dernière vers laquelle vous avez basculé avec `/<label>`, à laquelle vous avez envoyé un message, que vous avez lancée, ou qui vous a répondu ici en dernier. Un `/mode`, `/clearqueue`, `/diff` ou `/now` sans nom agit sur cette même session (`/stop` arrête celle qui travaille). Chaque discussion a la sienne : un message dans une discussion ne va jamais à une session qui appartient à une autre discussion. Dans un groupe, seuls comptent une réponse au bot, un message qui le mentionne, une commande ou un appui sur le clavier du groupe (voir [usage en équipe](groups.md#what-the-bot-reads-in-a-group)), et chaque personne a sa propre destination, que la réponse d'une session ne déplace pas (voir [usage en équipe](groups.md#talking-to-a-session-in-a-group)).

Les boutons du message épinglé :

- **Répondre à &lt;label&gt;** (un par session en attente, trois au plus) renvoie la demande de cette session, avec ses boutons de réponse, en bas de la discussion, pour que vous puissiez y répondre sans faire défiler. Si elle a reçu sa réponse entre-temps, vous recevez à la place une petite notification « déjà répondu », et une copie (ou la demande d'origine) touchée après que la demande a reçu sa réponse ailleurs est refusée de la même façon : elle ne répond jamais à une demande plus récente. Après un redémarrage d'aipager, la demande qui attendait marche toujours depuis la carte d'activité ou depuis son propre message, mais aipager ne sait plus quelle demande montrait une copie plus ancienne, donc un appui sur une copie que la barre a renvoyée avant le redémarrage est refusé avec « cette demande n'est plus ouverte ». Toute personne qui peut répondre à la demande peut l'utiliser ; personne d'autre.
- **📱 App** ouvre la Mini App, dans votre discussion privée, tant que la Mini App est activée.

La barre ne porte ni horloge, ni coût, ni % de contexte, ni modèle, donc elle ne change que quand l'état d'une session change. aipager la modifie (en silence, sans notification) seulement quand ce qu'elle montre a changé, au plus une fois toutes les 30 s par discussion (`PINNED_MIN_EDIT_GAP`) ; un changement pendant cet intervalle s'affiche à la fin de l'intervalle, il n'est jamais perdu. Elle n'est jamais modifiée pendant que la discussion est réduite au silence par un blocage, et se remet à jour quand le blocage se lève.

Dans un groupe, le bot a besoin des droits d'admin pour épingler. Si l'épinglage est refusé, aipager supprime le message d'état qu'il vient d'envoyer (pour qu'il ne reste jamais non épinglé dans l'historique du groupe) et n'affiche pas de barre dans ce groupe jusqu'au redémarrage d'aipager ; il en va de même pour une discussion qui refuse complètement le bot (il en a été expulsé, il est bloqué). Si vous supprimez le message épinglé, aipager en envoie et en épingle un nouveau, au plus une fois par heure et par discussion.

## <a id="persistent-keyboard"></a>Clavier fixe

Un clavier fixe se trouve sous le champ de saisie de la discussion. Les rangées, de haut en bas :

| Rangée | Boutons | Remarques |
|---|---|---|
| Sessions | un bouton par nom de session en cours | construite toute seule à partir de la liste des sessions |
| Actions | `status`, `stop`, `new` | des raccourcis texte (`kill` marche toujours quand on le tape ; terminer une session se trouve dans `/status` → `⋮`) |
| Navigation | `Modèles ›`, `Commandes ›`, `📱 App` | App apparaît dans les discussions privées tant que la Mini App est activée, et l'ouvre directement |

`Modèle IA ›` se trouve dans le sous-menu Commandes. Toucher une entrée d'un sous-menu envoie un message tout prêt ou une commande slash :

- **Modèles** : les messages que vous vous surprenez à taper encore et encore, par exemple « Écrivez des tests », « Expliquez le plan », « Mettez à jour la mémoire ».
- **Commandes** : les commandes slash que comprend Claude Code lui-même (`/compact`, `/clear`, etc.), tapées tout de suite. Une commande ou un modèle nomme la session à laquelle il est allé (`🧹 /clear envoyé à x1`). Effacer la conversation est refusé pendant que la session travaille (`⚠️ Impossible d'effacer la conversation pendant que la session travaille`).
- **Modèles IA** : des changements rapides de modèle pour la session active. Il y a les alias de famille (`sonnet`, `opus`, `haiku`, `fable`, `opusplan`), qui désignent toujours le modèle le plus récent de cette famille, et les modèles fixes (`claude-opus-5-5`, `claude-opus-5-5[1m]` avec la fenêtre de contexte de 1M, `claude-sonnet-5-5`, `claude-fable-5-1`, `claude-haiku-5-5`). Les sélecteurs de lancement et de session de la Mini App utilisent la même liste. Un changement est refusé pendant que la session travaille ou qu'une demande est ouverte. La réponse `🔄` change ensuite pour montrer le modèle que signale la session, ou dit que le changement n'a pas été confirmé (voir [Changer le modèle d'une session en cours](#switching-a-running-sessions-model)).

Pour changer la disposition par défaut, écrivez `~/.config/aipager/keyboard.json` :

```json
{
  "templates": [{"label": "Deploy",  "prompt": "Deploy to staging"}],
  "commands":  [{"label": "Compact", "send": "/compact"}],
  "models":    [{"label": "Sonnet",  "send": "/model sonnet"}]
}
```

Chaque section est indépendante : les sections absentes reprennent les valeurs intégrées, donc vous pouvez en changer une sans donner les autres. Un JSON mal formé est ignoré avec un avertissement dans le journal. Les changements demandent un redémarrage d'aipager.

## <a id="per-message-inline-buttons"></a>Les boutons sous chaque message

La plupart des réponses du bot portent des boutons propres à la situation :

### <a id="permission-prompts"></a>Demandes d'autorisation

Quand Claude veut exécuter un outil qui demande une approbation, la carte d'activité devient une demande d'autorisation :

```
🔐 Bash: List the temp directory
ls -la /tmp

  [✅ Autoriser]  [❌ Refuser]
  [🟢 Toujours autoriser]  [⏹ Arrêter]
```

La carte montre la vraie commande (ou le vrai chemin de fichier) que Claude veut exécuter, sous la description qu'en donne Claude lui-même : approuvez ce que vous pouvez lire. Un caractère qui changerait l'ordre dans lequel le texte est dessiné (un caractère de contrôle de direction d'Unicode comme U+202E, qui peut faire ressembler une commande à une autre) est affiché comme une marque du genre `⟨U+202E⟩`, et la carte ajoute alors `⚠️ Ce texte contient des caractères cachés qui changent le sens de l'écriture (affichés comme ⟨U+...⟩). Il peut exécuter autre chose que ce que vous voyez.`

- **Autoriser** : approuve ce seul appel.
- **Refuser** : le refuse ; Claude n'exécute pas l'outil.
- **Toujours autoriser** : approuve et ajoute la règle permanente que propose Claude (« ne plus demander pour … »). Le bouton n'apparaît **que quand Claude propose une telle règle** ; pour une commande dont il ne peut pas tirer de règle (la plupart des commandes composées), la carte porte à la place Autoriser / Refuser / Arrêter, comme pour une lecture de fichier qui ne fait que lire (`Read`, `Grep`, `Glob`) hors du dossier de travail de la session. Claude Code 2.1.259 et les versions suivantes mettent à cet endroit de leur propre boîte de dialogue Bash une ligne « passer en mode automatique », dans celle de lecture hors du dossier une ligne « bloquer désormais les lectures hors des dossiers de travail » et, pour un Write/Edit dont il ne peut pas tirer de règle par fichier, un changement de mode d'autorisation comme accepter automatiquement toutes les modifications de fichiers (`acceptEdits`) ; aipager ne choisit jamais aucune de ces lignes ; changez de mode exprès avec `/mode`.
- **Arrêter** : interrompt la tâche au lieu de répondre.

La réponse arrive à Claude Code par le hook d'autorisation d'aipager, qui l'attend jusqu'à 20 secondes. Une demande arrivée avant la carte d'activité (elle est alors envoyée comme son propre message, avec seulement Autoriser et Refuser) reçoit sa réponse de la même façon. Une fois cette attente passée, les boutons tapent la réponse dans la propre boîte de dialogue de Claude Code ; Refuser choisit alors la dernière ligne de la boîte de dialogue (« No, and tell Claude what to do differently »), qui termine la tâche de Claude comme Arrêter, et la carte dit `🚫 jim · Refusé`.

Chaque appui est noté dans `~/.claude/aipager-audit.jsonl` et repris dans une réponse d'une ligne sous la carte d'activité : `✅ jim · Autorisé · @alice · Bash: List the temp directory` (la description de l'appel, ou sa commande quand il n'en a pas ; avec une discussion configurée par `aipager config` ; `Autorisé` seul en usage personnel).

Pendant qu'une demande (ou un AskUserQuestion) vous attend, la session apparaît en attente, jamais prête. Si Claude Code signale pendant cette attente que la saisie est inactive, la discussion reçoit `⬆️ jim · attend toujours votre réponse plus haut` en réponse à la demande, une fois par attente.

Les boîtes de dialogue `AskUserQuestion` s'affichent de la même façon, avec un bouton par option (les quatre premières), et pour une question qui accepte plusieurs réponses, des cases ⬜/☑ et un bouton `✅ Envoyer`.

À 80 % de contexte, la discussion reçoit `⚠️ name · Contexte à 82% - il sera bientôt compacté automatiquement` avec **📦 Compacter maintenant**, qui envoie `/compact` à cette session.

### <a id="stale-buttons"></a>Boutons anciens

Les boutons qui agissent sur une tâche en cours (Arrêter, Terminer, Redémarrer, le changement de `/mode` et son Arrêter et changer, le Remplacer de `/new`) sont liés à la tâche pour laquelle ils ont été affichés. Un bouton resté d'une tâche précédente ne change rien, au lieu d'agir sur ce qui tourne maintenant, et il en va de même pour un bouton affiché avant que la session se termine et soit reprise ou redémarrée. Terminer, Redémarrer, le changement de `/mode`, et l'Arrêter de `/status` ou de la liste de `/stop` répondent `x1 a commencé une autre tâche : …` avec ce qu'il faut toucher ou envoyer de nouveau (ou `Cette session est terminée.`) ; l'Arrêter de la carte d'activité répond `Cette tâche est déjà terminée : …`, et l'Arrêter et changer de `/mode` redonne à sa carte la forme de la carte de `/mode`, sous `⚠️ x1 a commencé une autre tâche, rien n'a donc changé.` Le Remplacer de `/new` dit `x1 a commencé à travailler entre-temps. Renvoyez /new pour la remplacer.` quand la session a commencé une tâche après l'affichage de sa carte.

Un bouton `⚡ Envoyer maintenant` n'agit que sur le message sous lequel il se trouve : si Claude a déjà pris ce message, l'appui répond `Claude l'a déjà pris`, n'envoie rien et retire les lignes de la file.

### <a id="idle-responses"></a>Réponses

Une fois une tâche terminée, la réponse arrive comme le dit votre affichage (voir plus bas et [Paramètres](#settings)). Si la réponse de Claude est plus longue que ce que peut contenir un message (environ 32 Ko), le message montre ce qui tient sous une note `📎 Réponse complète dans le fichier ci-dessous ↓`, et toute la tâche (ses étapes et la réponse complète) suit sous forme de `<name>_full_log.md`, que Telegram affiche sans autre app. Le même fichier est joint quand la carte d'activité a dû laisser des lignes de côté.

Une tâche qui se termine sur une erreur de l'API d'Anthropic (crédit, serveurs surchargés, limite de débit, réseau) affiche `⚠️ name · <what went wrong>` au lieu d'une réponse, avec **🔄 Réessayer**, qui renvoie le dernier message à la même session. Pendant que la discussion est [réduite au silence par un blocage](troubleshooting.md#the-bot-went-quiet-flood-control), un appui ne fait rien du tout (le message n'est pas renvoyé et le bouton reste), alors touchez-le de nouveau une fois le blocage levé.

Dans l'affichage avec carte (« Carte + réponse à part »), la carte terminée reste toujours au-dessus de la réponse, pour chaque tâche, comme trace de la façon dont on y est arrivé : ses lignes d'outils, ses lignes d'agents et ce que Claude a dit entre les deux, ou seulement `✅ name · Terminé · Ns` pour une tâche qui n'a exécuté aucun outil. La réponse arrive dans son propre message (avec notification). Pour avoir un seul message par tâche, choisissez « Réponse dans la carte » (la réponse entre dans la carte) ou « Réponse seule (sans carte) » (la carte est retirée et la réponse reste seule).

Chaque tâche a exactement une carte d'activité. Un message que vous envoyez pendant qu'une tâche tourne n'ajoute jamais une seconde carte pour cette tâche, et une carte dont la tâche est terminée ne garde plus son bouton Arrêter : si rien ne l'a fermée (un Stop perdu, un redémarrage), aipager la ferme au bout de 30 secondes, comme le dit votre affichage. Après un redémarrage d'aipager, une carte dont la tâche tourne encore est gardée et reprend tout de suite sa progression, son temps compté depuis le début de la tâche, et la session apparaît au travail jusqu'à la fin de la tâche ; toute autre carte restée d'avant le redémarrage est fermée (« aipager a redémarré », sans Arrêter). Une session qui attendait une demande d'autorisation ou une seule question de Claude revient en attente, pas au travail : sa carte et la barre épinglée disent qu'elle a besoin de vous et les boutons de la demande y répondent toujours, sauf si l'historique de la conversation montre qu'elle a reçu sa réponse dans le terminal entre-temps. Les questions en plusieurs parties ou à plusieurs choix, et les demandes faites par un sous-agent, sont laissées au terminal. Une fois qu'on sait qu'une demande est finie (elle a reçu sa réponse par un appui ou dans le terminal, sa tâche s'est terminée, ou un redémarrage ne l'a pas ramenée parce qu'elle avait reçu sa réponse entre-temps ou n'avait pas pu être enregistrée puis relue), ses anciens boutons de réponse répondent « déjà répondu » et ne tapent rien, même après un redémarrage. Une demande à laquelle personne n'a répondu et qu'aipager a arrêté d'attendre (5 minutes sans nouvelles de Claude) n'est pas considérée comme finie, donc ses boutons répondent toujours à la boîte de dialogue.

Une tâche que Claude commence **de lui-même** (un agent en arrière-plan qui rend compte avec un `<task-notification>` quand aucun travail n'est ouvert) ne reçoit sa carte d'activité qu'une fois qu'elle fait quelque chose : à son premier appel d'outil, ou au bout de 15 s, selon ce qui arrive en premier. La plupart de ces réveils sont quelques secondes de « rien de nouveau » ; ceux-là ne montrent maintenant que la réponse. L'indicateur « en train d'écrire… » s'affiche toujours pendant qu'elle tourne, et la réponse elle-même est toujours livrée : rien n'est filtré comme sans importance. Les tâches que vous commencez, depuis Telegram ou le terminal, reçoivent toujours leur carte tout de suite.

Pendant qu'une session travaille, chaque agent en arrière-plan que lance Claude (avec l'outil `Agent` de Claude Code, `Task` dans les versions plus anciennes) a sa propre ligne sur la carte d'activité : `🤖 <type> · <activity> · <elapsed>`, qui montre le type de l'agent et ce qu'il fait en ce moment, mise à jour à mesure qu'arrivent ses propres appels d'outil. Une fois que cet agent a fait trois appels d'outil ou plus, ils se replient dans leur propre bouton `▸ N outils utilisés` juste sous sa ligne ; ils n'apparaissent jamais dans la chronologie de la tâche principale ni dans ses comptes `Bash ×N`. Quand l'agent se termine, sa ligne se fige en `✅ 🤖 <type> · N outils utilisés · <elapsed>` et garde le même bouton. Le fichier `.md` joint plus haut reçoit une section Agents qui liste chaque agent qui a tourné pendant la tâche, son temps, son nombre d'outils et les outils qu'il a utilisés.

Tant que des agents en arrière-plan tournent encore après la fin de leur tâche, la carte reste comme état du travail. Quand Claude prend un nouveau message entre-temps, la carte en direct est celle qui se trouve sous ce message, et elle montre toujours les agents qui tournent. Un message que vous envoyez à la session prête commence une nouvelle tâche : la carte de la tâche précédente se fige là où elle est, ses étapes gardées, et la nouvelle tâche a sa propre carte. Un message qui était en file et qui est pris continue la tâche du travail : sa carte est renvoyée sous ce message et l'ancienne copie est supprimée. Les appels d'outil faits dans un agent n'apparaissent jamais comme lignes propres de la tâche principale, et ne font jamais croire à aipager qu'une nouvelle tâche a commencé.

Une commande que Claude exécute en arrière-plan (`run_in_background`, Ctrl+B, ou une commande que Claude Code y envoie quand elle prend trop de temps) a sa propre ligne pendant qu'elle tourne : `⏳ commande en arrière-plan : <description> (3:12)`, nommée d'après la description de l'appel ou, sinon, la première ligne de la commande. Quand elle se termine, la ligne se fige en `✅ commande en arrière-plan : <description> - terminée (6:12)`, `❌ ... - terminée avec une erreur (code de sortie 1)`, ou `⏹ ... - arrêtée (6:12)` (Claude l'a arrêtée avec TaskStop, ou Claude Code l'a arrêtée par manque de mémoire) ; les lignes en erreur et arrêtées gardent cette marque sur la carte terminée. Une tâche qui se termine pendant qu'une telle commande tourne encore n'est pas finie : la carte attend comme état du travail (`🔄 name · 1 commande en arrière-plan : encore en cours · 4:10`, ou `1 agent (ship-reviewer) et 1 commande en arrière-plan : encore en cours` avec un agent aussi), et quand la commande se termine, la suite de Claude continue sur cette même carte. Les commandes lancées dans un agent appartiennent à cet agent et ne sont pas affichées. Une commande dont aipager ne voit jamais la fin (un redémarrage, un événement perdu) arrête d'être comptée au bout de deux heures (`AIPAGER_BG_SHELL_MAX_TRACK`, en secondes) : sa ligne dit `⏹ ... - fin non vue`, et un travail qui n'attendait qu'elle se ferme avec `⚠️ name · Terminé (2:00:04, pas de nouvelles de la commande en arrière-plan)`.

Quand la chronologie d'une tâche s'allonge, chaque série plus ancienne d'appels d'outil (trois ou plus à la suite, et pas la série en cours) se replie dans son propre bouton `▸ N outils utilisés` à l'endroit même où elle a eu lieu, au lieu de s'entasser en entier ou d'être coupée par la limite de longueur des messages de Telegram ; touchez n'importe lequel pour le lire sur place. Les commentaires ne se replient jamais ; l'activité la plus récente et la ligne d'état sont toujours visibles sans rien toucher. La propre ligne d'un agent en arrière-plan qui tourne encore (ou qui vient de se figer) n'est jamais repliée elle-même, seulement ses appels d'outil, et jamais tant qu'elle est la seule chose entre la chronologie et la limite. Ce n'est que si la chronologie est si grande que même tout replié ne tient pas que du contenu est vraiment retiré de la carte ; dans ce cas, le fichier `.md` joint plus haut contient la trace complète.

#### <a id="agents-still-running-when-the-answer-goes-out"></a>Des agents qui travaillent encore quand la réponse part

Claude Code peut faire tourner des agents en arrière-plan, et la tâche qui les a lancés peut se terminer pendant qu'ils travaillent. La réponse se termine alors par un court état de ce travail en arrière-plan, dans le style du propre panneau d'agents de Claude Code, tenu à jour tant qu'il tourne :

```
⏳ 2 agents au travail · 🖥 1 commande en arrière-plan (shell) en cours

○ pipeline-runner · Writing final.md for the pipeline

○ Explore · Reading state.py

Quand les agents auront fini, le résultat arrivera ici même.
```

- **La première ligne compte.** Les agents au travail (ou qui ont terminé), et les commandes en arrière-plan de ce travail en cours, terminées ou arrêtées. Une commande en arrière-plan n'est jamais qu'un nombre, jamais sa commande.
- **Une ligne par agent,** son type en gras, puis ce qu'il fait en ce moment : sa propre dernière ligne de texte (les mots de Claude, tels qu'ils sont écrits, jamais traduits), sinon son dernier outil sous forme d'une courte phrase, dans l'anglais de Claude Code lui-même (`Reading state.py`, `Writing final.md`, `Running a command`, `Searching for …`), sinon la description que Claude a donnée à l'agent en le lançant, sinon seulement le type. Quatre lignes au plus, puis `+N autres agents`.
- **La promesse** « Quand les agents auront fini, le résultat arrivera ici même. » ne reste que tant qu'un agent travaille encore. Les commandes en arrière-plan ne la portent jamais : un serveur de développement peut ne jamais se terminer.

La ligne de chaque agent se fige d'elle-même quand cet agent s'arrête, en `✓ Explore · terminé (0:42)` (combien de temps cet agent a tourné), et la première ligne décompte. Une fois que tous les agents ont terminé, les lignes laissent la place à une seule ligne, avec le temps entre la réponse et la fin du dernier agent :

```
✅ 2 agents ont terminé (6:12) · 🖥 1 commande en arrière-plan (shell) en cours
```

et quand cette commande se termine, `✅ 2 agents ont terminé (6:12) · 1 commande en arrière-plan (shell) terminée (2:13:05)` (combien de temps la commande a tourné ; `arrêtée` quand elle a été tuée ou arrêtée par Claude, `terminée` quand elle s'est finie d'elle-même, en erreur ou non ; sa ligne sur la carte dit comment). Une réponse avec seulement une commande en arrière-plan de son travail affiche `🖥 1 commande en arrière-plan (shell) en cours`, qui se fige en `1 commande en arrière-plan (shell) terminée (…)` ou `1 commande en arrière-plan (shell) arrêtée (…)`.

Seules sont comptées les commandes en arrière-plan du propre travail de la réponse : un serveur de développement qu'une tâche précédente a laissé tourner n'est pas répété sous chaque réponse (la barre épinglée compte toujours toutes les commandes). `/stop` termine le travail, mais Échap ne termine pas une commande en arrière-plan : une commande qui tourne encore reste dans la barre épinglée jusqu'à sa fin, et n'est plus comptée sous les réponses suivantes ; sa suite, plus tard, commence alors une tâche à elle.

Cette réponse part au moment où la tâche se termine, comme un message normal (avec notification) en réponse à votre message : le même texte que montre le terminal. La carte d'activité reste au-dessus comme état en direct du travail, dans tous les affichages : `🔄 name · 1 agent (general-purpose) : encore en cours · 1:18`, avec son bouton **Arrêter**. Quand les agents rendent compte, la réponse de Claude à ce compte rendu arrive dans un message à elle ; la réponse précédente n'est jamais renvoyée, même après un redémarrage d'aipager. Un travail dont les agents rendent compte plus d'une fois produit une réponse par compte rendu, chacune envoyée au fur et à mesure qu'elle est écrite.

Un agent qui s'est arrêté pendant qu'un travail à lui en arrière-plan tourne encore compte comme au travail, puisqu'il reprend plus tard ; aipager l'apprend par le `<task-notification>` de Claude pour lui, quand cette notification commence une tâche. Une ligne attend quelques secondes avant de s'afficher comme terminée, pour que cette note puisse revenir dessus avant. L'état est ajouté dans tous les affichages, dans la langue de la discussion.

**Comment il est tenu à jour.** aipager modifie la réponse en silence, seulement quand ce que montre l'état a changé, et au plus une fois toutes les 30 secondes par réponse (la réponse elle-même compte comme la première). Chaque réponse avec un état est tenue à jour ainsi (jusqu'à cinq en attente par session). Les modifications ont une faible priorité : une modification que le budget d'envoi refuse, ou qui tomberait pendant que la discussion est réduite au silence, n'est pas faite, et l'état attend son prochain changement. La modification finale, une fois que tout est terminé, est retentée une fois après la levée du silence ou le refus du budget, puis abandonnée. Aucune modification n'est faite dans une réponse supprimée, pour une session terminée ou tuée, ni quand elle rendrait la réponse trop longue pour Telegram (quand une longue réponse est coupée, de la place est gardée pour l'état).

L'état reste tel qu'il a été affiché en dernier, sans passer à terminé, quand aipager ne peut pas savoir que le travail est fini :

- un agent dont aipager n'a aucune nouvelle pendant 30 minutes (`AIPAGER_SUBAGENT_SILENCE`) n'est plus compté comme en cours, mais le silence n'est pas une fin ;
- une commande en arrière-plan qui tourne depuis plus de deux heures (`AIPAGER_BG_SHELL_MAX_TRACK`) sans qu'on ait vu sa fin n'est plus comptée ;
- un redémarrage d'aipager oublie quelles réponses sont en attente.

Une réponse retenue par un silence dû à un blocage, ou livrée en texte simple après l'échec de l'envoi mis en forme, part sans l'état, tout comme une tâche qui se termine sans texte de réponse (seulement un en-tête). Si Claude prend la notification d'un agent au milieu d'une tâche en cours, aipager ne la voit pas ; un agent qui s'est arrêté avec du travail encore en cours peut alors être marqué terminé trop tôt, et la barre épinglée le montre de nouveau en cours quand il reprend.

### <a id="end-confirmation"></a>Confirmation de fin

`/kill`, sa liste et `⋮` → `⏹ Terminer la session` demandent toujours confirmation :

```
⏹ Terminer jim ? Claude s'arrête et la session prend fin.

  [⏹ Terminer]  [Annuler]
```

Après `⏹ Terminer`, le message dit `⏹ jim terminée` au-dessus des sessions qui restent dans cette discussion. Une confirmation touchée après que la session a commencé une autre tâche est refusée (`jim a commencé une autre tâche : …`), tout comme une confirmation pour une session du même nom lancée depuis.

### <a id="voice-install-when-extra-isnt-installed"></a>Installer la fonction vocale (quand elle n'est pas installée)

Quand vous envoyez un message vocal et que `aipager[voice]` n'est pas installé :

```
⚠️ Les messages vocaux demandent un paquet optionnel
   (environ 200 Mo à installer et environ 74 Mo de modèle à la première utilisation).

  [📦 Installer la fonction vocale]  [Annuler]
```

Toucher Installer lance la bonne commande d'installation pour votre installateur (`uv tool install --reinstall aipager[voice]`, `pipx install --force`, ou en dernier recours `pip install faster-whisper`) avec une modification du message toutes les 5 s, puis propose un bouton `[🔄 Redémarrer aipager]` si tout s'est bien passé.

### <a id="restart"></a>Redémarrer

`🔄 Redémarrer aipager` :

- Un aipager qui tourne comme service utilisateur systemd programme un `systemctl --user restart aipager.service` détaché 5 s plus tard (avec un minuteur précis à 1 s), dans un service temporaire en dehors du groupe de processus d'aipager lui-même, pour qu'il survive à l'arrêt d'aipager. Il refuse tant que le service terminerait vos sessions (`KillMode` autre que `process` ou `none`), et vous dit de lancer d'abord `aipager service install`.
- macOS, quand l'agent launchd est installé (`~/Library/LaunchAgents/com.aipager.daemon.plist`) : `launchctl kickstart -k gui/<uid>/com.aipager.daemon`, même pour un aipager que vous avez lancé vous-même.
- Sinon, un aipager que vous avez lancé vous-même (`aipager start`), sur Linux même quand le service est aussi installé : il lance un remplaçant détaché qui attend la fin du processus parent, puis fait `exec aipager start`. L'aipager actuel s'envoie SIGTERM dès que le remplaçant est en vie.

Pas besoin de SSH.

## <a id="update"></a>Mise à jour

`/update` (réservé aux admins ; en usage personnel, seulement à la personne qui a installé aipager) répond avec un bouton, **🔄 Rechercher des mises à jour**. Rien n'est vérifié tant que vous ne l'avez pas touché. L'appui transforme ce message en une ligne par produit :

- `aipager 1.0.1 → 1.1.0` quand une version plus récente existe (la plus récente sur PyPI) ;
- `aipager 1.0.1 (à jour)` quand il n'y en a pas ;
- `Claude Code 2.1.282 (vérification impossible)` quand la recherche a échoué (réseau coupé, délai de 5 s dépassé, ou `claude` introuvable).

Claude Code est comparé à la plus récente version de son propre canal de mises à jour (`autoUpdatesChannel` : latest, stable ou rc). Sous les lignes, en petit, figure la façon dont aipager a été installé (par exemple `pipx, from PyPI` ou `pipx, from local path …` ; les discussions de groupe ne montrent jamais de chemins).

S'il y a quelque chose de plus récent, il y a UN bouton qui ne met à jour que les produits qui ont une mise à jour : **Mettre à jour aipager**, **Mettre à jour Claude Code** ou **Mettre à jour les deux**, plus **Annuler**. Quand aipager est proposé, le message dit aussi comment se fera le redémarrage : `Redémarrage : automatique, dès qu'aucune tâche n'est en cours.` ou `Redémarrage : manuel (raison)`. Un aipager plus récent sur une installation qui ne peut pas être mise à jour d'ici (editable, Nix, Snap, un paquet du système, un conteneur, ou l'installation d'un autre utilisateur) est affiché avec la raison et n'est pas proposé. Il en va de même pour un Claude Code plus récent installé avec npm dans un dossier où votre utilisateur ne peut pas écrire (par exemple `sudo npm install -g` sous `/usr/lib/node_modules`), où `claude update` ne pourrait qu'échouer : la ligne dit `mise à jour impossible d'ici : installed with npm into a folder your user can't write to; run "claude install" once to switch to Claude Code's own installer, then restart aipager`. Si rien ne peut être mis à jour, le message dit « Tout est à jour. », dit qu'une vérification a échoué, ou, quand quelque chose de plus récent existe mais ne peut pas être installé d'ici, « Une nouvelle mise à jour est sortie, mais elle ne peut pas être installée d'ici. », avec un bouton **Vérifier à nouveau** et aucun bouton de mise à jour.

Le bouton de mise à jour ne lance que ce que la vérification a proposé. Si la vérification a plus de 10 minutes, si une autre mise à jour a tourné depuis, ou si les versions ne correspondent plus au bouton, il vous demande plutôt de vérifier à nouveau. Les boutons d'un ancien menu `/update` (les **Mettre à jour Claude Code** / **Mettre à jour aipager** / **Les deux** par produit) répondent « Ce menu est ancien, renvoyez /update » et ne font rien. Chaque appui revérifie la règle des admins.

Mettre à jour Claude Code lance `claude update` (par son chemin absolu, avec un délai de 5 min) et indique `Claude Code A → B`, « déjà à jour », ou l'échec avec la fin de sa sortie. Les sessions en cours gardent l'ancienne version jusqu'à ce que vous les redémarriez (`/restart`) ; la réponse les liste. Aucune session n'est redémarrée à votre place. Les nouvelles sessions utilisent la nouvelle version.

Mettre à jour aipager (**Mettre à jour aipager**, ou la seconde moitié de **Mettre à jour les deux**, qui met d'abord à jour Claude Code) :

1. Sur une installation PyPI déjà à jour, il le dit et s'arrête.
2. Si aipager peut redémarrer de lui-même, il **attend d'abord que plus rien ne soit en cours** : aucune session qui fait tourner une tâche, attend une question, fait tourner un agent en arrière-plan, montre une carte d'activité en direct, exécute un outil ou a une demande d'autorisation ouverte, et aucune réponse retenue qui attend la levée d'une limite de débit. Le message liste ce qu'il attend, avec **Redémarrer maintenant** (sauter l'attente) et **Annuler**. Au bout de 10 minutes, il demande de nouveau : **Attendre 10 min de plus**, **Redémarrer maintenant**, **Annuler**. Sans réponse pendant une heure, il s'annule de lui-même.
3. Il fait la mise à jour par l'installateur qui possède l'aipager en cours (`pipx upgrade aipager`, `uv tool upgrade aipager`, `brew upgrade aipager`, ou `<venv>/bin/python -m pip install --upgrade aipager`), par son chemin absolu, avec un délai de 10 min. Il n'y a pas d'Annuler pendant que l'installateur tourne.
4. Il vérifie que la nouvelle version se charge dans un interpréteur neuf. Une mise à jour qui a échoué, dépassé le délai ou ne se charge pas ne redémarre rien. Une mise à jour qui a dépassé le délai a été arrêtée en cours de route, donc le message prévient que l'installation est peut-être incomplète et donne la commande de réinstallation à lancer avant le prochain redémarrage. La sortie de l'installateur n'est montrée que dans une discussion privée ; un groupe reçoit « (la sortie est dans le journal d'aipager) ».
5. Si une tâche a commencé pendant la mise à jour, il attend de nouveau.
6. Il programme un `systemctl --user restart aipager.service` détaché 5 s plus tard (avec un minuteur précis à 1 s), et le message d'état dit `⏳ aipager A → B installé, redémarrage…`. Pendant l'attente de l'étape 5, il dit `⏳ aipager A → B installé ; redémarrage quand la tâche en cours sera finie`. Après le redémarrage, le nouvel aipager transforme ce même message en `✅ aipager mis à jour A → B ; N sessions se sont reconnectées` (et `⚠️ Pas revenues : …` pour toute session qui n'est pas revenue). Si le message a été supprimé ou ne peut plus être modifié, il publie le résultat dans un nouveau message dans la discussion qui l'a demandé.

Dans la Mini App, **Paramètres → Mises à jour** affiche « Redémarrage d'aipager… » avec une lanterne qui tourne jusqu'à ce que le nouvel aipager réponde, puis « Mis à jour vers B ; N sessions se sont reconnectées. ». Elle n'affiche jamais de compte à rebours. Après environ 2 minutes sans réponse (jamais avant que l'ancien aipager ait pu signaler un redémarrage qui n'a pas eu lieu), elle dit « Redémarrage toujours en cours ; rouvrez l'app dans un instant. ». Avec le tunnel géré d'aipager, l'adresse de l'app change à chaque redémarrage, donc une fois que l'ancien aipager ne répond plus, la page ouverte dit « aipager a redémarré ; rouvrez l'app quand la discussion indique qu'il est à jour » ; fermez-la et rouvrez-la depuis le bouton de menu de la discussion ou `/app`. La connexion de l'app dure 5 minutes, donc une page ouverte depuis plus longtemps dit à la place « Rouvrez l'app pour voir le résultat de la mise à jour. ».

aipager ne redémarre de lui-même que quand il tourne comme service utilisateur systemd **et** que ce service a `KillMode=process` (ou `none`). Sinon, aipager est quand même mis à jour, et le message dit comment redémarrer : lancer d'abord `aipager service install` (il liste les sessions qu'un redémarrage terminerait), la commande `launchctl kickstart` sur macOS, ou `Il faut redémarrer : no service unit is running this daemon; restart it yourself` pour un aipager que vous avez lancé vous-même.

Une seule mise à jour tourne à la fois, entre `/update`, le bloc **Paramètres → Mises à jour** de la Mini App (le même bouton Rechercher des mises à jour, le même bouton unique de mise à jour, le même travail) et `aipager update` en ligne de commande. Cela comprend les secondes entre « installé, redémarrage… » et le redémarrage lui-même : `/update` répond qu'aipager est sur le point de redémarrer, la Mini App ne propose aucun bouton, et le **Redémarrer aipager** de la fonction vocale refuse tant qu'une mise à jour tourne ou attend de redémarrer. Si aipager est toujours en vie deux minutes après avoir programmé son redémarrage, il arrête le minuteur de redémarrage en attente, libère le verrou de mise à jour et vous dit de le redémarrer vous-même.

Si aipager s'arrête pendant qu'un installateur tourne, l'installateur a 3 s pour finir puis est arrêté (sinon il continuerait d'écrire l'installation pendant que le prochain aipager démarre). Le message d'état et, après le redémarrage, un nouveau message disent que l'installation est peut-être incomplète et comment la réparer.

Une fois qu'un arrêt a commencé, rien n'est redémarré et rien de nouveau ne commence : les boutons de `/update` répondent qu'aipager s'arrête, la Mini App répond 503, et aucun installateur ni aucune vérification de version n'est lancé. Un installateur qui finit dans ses 3 s compte quand même. Le message d'état dit que la nouvelle version est installée et que rien n'a été redémarré, et le démarrage suivant annonce `aipager mis à jour A → B`. Un aipager que vous avez arrêté avec `aipager service stop` reste arrêté. La part de la mise à jour dans l'arrêt prend au plus 8 s en tout.

## <a id="free-messages"></a>Messages libres

### <a id="text"></a>Texte

Il est traité comme le prochain message pour la **session active** : la destination de cette discussion, la dernière session vers laquelle vous avez basculé, à laquelle vous avez envoyé un message, que vous avez lancée, ou qui vous a répondu ici en dernier (voir [la barre d'état épinglée](#the-pinned-status-bar)). Dans un groupe, chaque personne a sa propre destination, que la réponse d'une session ne déplace pas (voir [usage en équipe](groups.md#talking-to-a-session-in-a-group)). Les messages arrivent à Claude **tout de suite**, même pendant que votre propre tâche tourne, exactement comme si vous tapiez dans le terminal (dans un groupe, le message de quelqu'un d'autre attend la fin de cette tâche ; voir plus bas). Envoyez-en plusieurs et ils se mettent en file dans Claude lui-même, qui prend chacun à un moment naturel :

La réaction sur votre message le suit, comme dans Claude Code un message en file passe du gris au blanc :

- 👀 : remis à la session (ou retenu, voir plus bas), mais Claude ne l'a pas encore pris. Un message envoyé pendant qu'une tâche tourne reste en 👀 tant qu'il attend dans la file de Claude.
- 👍 : Claude l'a pris : il a commencé une tâche avec, l'a intégré à la tâche en cours, ou l'a passé à un agent en arrière-plan qui tourne.
- 🤷 : il ne sera jamais pris : un message retenu que `/stop`, `/clearqueue` ou `/kill` ont supprimé, ou qui n'a pas pu être envoyé à sa libération ; un message ou une commande qui attendait encore quand `/stop`, `/clearqueue`, `/kill` ou la fin de la session (pas `/clear` ni `/resume`) l'ont supprimé ; ou un message que Claude Code a refusé (voir plus bas). Les messages que Claude avait déjà mis en file ne sont marqués que tant qu'aipager peut voir cette file (la lecture en direct de l'historique de la conversation tourne et aucun travail en arrière-plan n'attend) ; sinon ils gardent 👀. Échap dans le terminal ramène la file de Claude dans son champ de saisie, d'où elle peut être renvoyée, donc un message supprimé ainsi garde aussi 👀.
- 👌 : une commande de Claude Code qui a tourné (voir plus bas), ou aipager qui confirme un `/stop`.

Une réaction ne fait qu'avancer, donc un message en reçoit au plus trois, chacune une fois. Une réaction qui tombe pendant un blocage de Telegram pour limite de débit est sautée, pas rejouée plus tard, et un nettoyage marque au plus les dix messages les plus récents qu'il supprime.

Les boutons de commande (`Compacter`, `/model …`) : touchés quand la session est prête, la commande reçoit 👌 tout de suite ; Claude Code l'exécute sur Entrée, et une commande locale comme `/model` ne déclenche aucun hook qui pourrait le dire plus tard. Touchés pendant qu'une tâche tourne (pas `/model` : il est refusé jusqu'à la fin de la tâche, voir [changer le modèle d'une session en cours](#switching-a-running-sessions-model)), elle reste en 👀 jusqu'à la fin de cette tâche (normalement, comme pause d'un travail en arrière-plan, ou sur une erreur de l'API) puis 👌 ; supprimée avant par `/stop`, `/clearqueue`, `/kill` ou la fin de la session, elle n'a jamais tourné : 🤷. Quand Claude Code la met en file comme un message, elle suit le parcours 👀 → 👍. Un message vocal reçoit les mêmes réactions que le texte une fois sa transcription envoyée.

La carte d'activité et la réponse finale suivent le message que Claude a vraiment pris pour une tâche (celui avec lequel il a commencé si la session était prête, ou celui qu'il a intégré à la tâche en cours), jamais simplement le dernier message que vous avez envoyé. Envoyer un autre message en pleine tâche ne « tire » pas la réponse vers lui : si Claude l'intègre à la réponse qu'il est en train de former, la carte saute vers lui au moment où cela arrive ; si Claude finit d'abord puis le prend, la première réponse reste sous le premier message et le suivant a sa propre tâche, avec sa propre carte et sa réponse en dessous.

Si Claude Code refuse franchement un message (une commande slash inconnue, ou une commande intégrée qui ne fait qu'ouvrir une boîte de dialogue dans le terminal), aucun hook ne se déclenche, donc rien ne terminerait la tâche qu'aipager vient d'annoncer. Au bout de 8 s sans aucun hook (`PROMPT_HOOK_GRACE_SECONDS`), la carte d'activité devient `⚠️ name · Claude Code n'a pas reçu le message` avec une explication d'une ligne et la session redevient prête ; la raison est dans le terminal, et le message reçoit 🤷 (une commande slash reçoit 👌 à la place : une commande intégrée qui ouvre une boîte de dialogue ne déclenche pas de hook non plus). Seul un message qui a commencé une tâche est jugé ainsi ; un message en file derrière une tâche en cours garde son 👀 jusqu'à ce que Claude le prenne ou qu'il soit supprimé.

Trois cas sont retenus au lieu d'être envoyés, et livrés automatiquement une fois réglés :

- Une demande d'autorisation ou une question est ouverte : sinon votre texte serait lu comme une réponse à cette boîte de dialogue.
- (Usage en équipe) le message d'une autre personne attend encore d'être pris.
- (Usage en équipe) la tâche d'une autre personne tourne, y compris un agent en arrière-plan qu'elle a lancé. Une tâche tapée dans le terminal compte comme celle du propriétaire, donc seul le message d'un propriétaire (ou, dans une discussion privée, celui de son propre membre) la rejoint.

Les deux derniers gardent les messages de personnes différentes hors d'une même tâche, pour que chacun tourne avec les droits de son propre expéditeur. Si un message la rejoint quand même (par exemple celui d'un expéditeur qu'aipager ne connaît pas, qui n'est jamais retenu), la tâche tourne avec les règles les plus strictes de tous ceux qui en font partie (voir [usage en équipe](groups.md#who-a-message-runs-as)). Si la tâche qu'un message attend est arrêtée, ou s'arrête à cause d'un outil bloqué, les messages retenus sont supprimés avec un 🤷.

Les messages retenus sont limités à 50 par session et expirent au bout de 24 h ; `/clearqueue` les supprime avec tout ce que garde Claude.

#### <a id="send-a-queued-message-now"></a>Envoyer tout de suite un message en attente

Un message que vous envoyez pendant que Claude travaille sur votre propre tâche part tout de suite vers Claude (aipager ne le retient pas) et attend dans la file de Claude jusqu'à la fin de l'étape en cours. Dès que la file de Claude le montre, aipager répond en dessous :

```
⏳ En attente - Claude le lira après l'étape en cours
[⚡ Envoyer maintenant]
```

Chaque message en file a sa propre ligne, et chaque ligne disparaît, avec son 👍, au moment où Claude prend ce message. Un message que Claude prend avant que son entrée dans la file soit vue n'a pas de ligne, seulement 👀 puis 👍.

Toucher `⚡ Envoyer maintenant` (ou envoyer `/now`) appuie dans cette session sur les propres touches d'envoi immédiat de Claude Code (Ctrl+X, puis Ctrl+S). Claude lit alors d'un coup tout ce qu'il a en file, pas seulement le message que vous avez touché. Ce qui arrive au travail en cours, c'est Claude Code qui le décide : une commande ou un agent en cours continue en arrière-plan ; si Claude était en train d'écrire une réponse, cette réponse est coupée et recommencée avec votre message. Toute autre sorte d'étape (un autre outil, par exemple) peut aussi être interrompue, après un court délai de grâce. Dans certains cas (par exemple quand les tâches en arrière-plan de Claude Code sont désactivées), l'envoi immédiat se replie sur une simple interruption de l'étape en cours. Tout cela est le comportement d'envoi immédiat de Claude Code lui-même, pas quelque chose que décide aipager.

L'appui répond `Envoyé à Claude maintenant` et toutes les lignes disparaissent d'un coup, puisque l'envoi immédiat donne à Claude tout ce qu'il garde ; `/now` les retire de la même façon, et aucune ligne ne vient pour les messages qu'Envoyer maintenant a envoyés (un message que vous envoyez après a la sienne). Si Claude annule l'étape en cours au lieu de la passer en arrière-plan, il prend tout de suite seulement le plus ancien message en file et lit le reste à son étape suivante ; la réponse va sous le message qu'il a pris, et ce message n'est pas exécuté deux fois. Si les touches mettent plus d'un instant à arriver à Claude (un ordinateur occupé), l'appui répond à la place `Envoi à Claude maintenant` ; la ligne disparaît alors une fois qu'elles sont passées ou, si elles n'ont pas pu être remises, reste et dit `⏳ En attente : impossible de joindre Claude, touchez « Envoyer maintenant » pour réessayer`, avec son bouton, pour que vous puissiez toucher de nouveau. Les touches ne sont pressées que quand l'historique de la conversation de Claude montre qu'il garde encore le message ; sinon, l'appui répond `Claude l'a déjà pris` et retire la ligne. C'est refusé, sans rien presser, tant qu'une demande d'autorisation ou une question est ouverte (`Répondez d'abord à la question ouverte`), tant que Claude vient peut-être d'en ouvrir une qu'aipager n'a pas encore montrée (les événements d'une nouvelle tâche sont retenus pendant que la réponse de la tâche précédente est encore en cours de livraison : `Occupé, réessayez dans un instant`), et pendant qu'aipager est en train de taper un autre message dans la session (`aipager est en train de taper un message ; réessayez dans un instant`). Toute personne qui peut envoyer des messages à la session peut utiliser le bouton et `/now` ; un membre `read_only` ne le peut pas.

La ligne disparaît d'elle-même quand son message quitte la file de Claude : Claude le prend (intégré à la tâche en cours, passé à un agent en arrière-plan, ou commencé comme tâche suivante), Échap dans le terminal ramène la file dans le champ de saisie, ou il est supprimé par `/stop`, `/clearqueue`, `/kill`, `/restart`, un arrêt de sécurité ou la fin de la session. Un redémarrage d'aipager retire toute ligne encore affichée.

Les lignes n'attendent jamais le gestionnaire des limites : elles partent et sont retirées tout de suite, même dans une discussion chargée ou lente, et en mode minimal. Ce n'est que pendant que Telegram lui-même réduit la discussion au silence qu'aucune ligne n'est envoyée (`/now` marche toujours alors), et si Telegram vient de demander à aipager de ralentir, une ligne attend les quelques secondes demandées. Chaque message en attente coûte un appel à la discussion pour sa ligne ; les lignes qui partent ensemble sont retirées en un seul appel.

### <a id="files"></a>Fichiers

Les fichiers envoyés sont téléchargés dans `/tmp/aipager-files/` (sous le nom `<time>_<file name>`) et le chemin est proposé à Claude : avec une légende, le message est la légende suivie du chemin (ou des chemins) ; sans légende, c'est seulement `check this: <path>` (ou `check these: <paths>` pour un album), donc on montre le fichier à Claude sans lui dire quoi en faire. Une légende qui commence par `/<label>` choisit la session, exactement comme `/<label> <prompt>` pour le texte, et le nom est retiré du message : `/api` seul envoie `check this: <path>` à `api`, et `/api compare these` y envoie `compare these <paths>`. Seul un `/<label>` au début choisit la destination ; une barre oblique plus loin dans la légende n'est que du texte. Un nom auquel ne répond aucune session est refusé avec `⚠️ Aucune session ne porte ce nom` et rien n'est envoyé. La limite de 20 Mo de téléchargement des bots Telegram est appliquée d'avance ; les fichiers trop gros reçoivent un refus clair avant toute tentative de téléchargement. Un téléchargement qui tombe sur une erreur réseau passagère est essayé jusqu'à trois fois en tout (en attendant 1 s, puis 2 s) avant que vous voyiez une erreur, et cette erreur nomme le fichier. Un album (plusieurs photos ou documents envoyés comme un seul message) est passé à Claude comme un seul message (la légende, puis chaque chemin de fichier dans l'ordre) une fois que son dernier élément est arrivé ; si un élément ne peut pas être téléchargé, le reste part quand même, avec une note qui nomme celui qui manque.

### <a id="voice"></a>Voix

Les messages vocaux passent par `faster-whisper` (le paquet `aipager[voice]`). L'audio est transcrit sur cet ordinateur, répété sous la forme `🎙️ J'ai entendu : …`, et tapé comme si vous l'aviez écrit, y compris comme nom pour `/new` ou pour un renommage en attente. Un message vocal au-delà de la limite de 20 Mo de téléchargement des bots Telegram est refusé. Voir [hooks → UserPromptSubmit](hooks.md#userpromptsubmit) pour ce qui se passe ensuite.

---

## <a id="command-line-every-command"></a>Ligne de commande : toutes les commandes

Lancez `aipager <command> --help` pour voir la même liste sur votre ordinateur.

| Commande | Ce qu'elle fait |
|---|---|
| `aipager config` | L'assistant de configuration interactif : token du bot, discussions, membres et rôles, hooks de Claude Code. Relancez-le pour changer quoi que ce soit (voir [usage en équipe](groups.md#setup)). |
| `aipager setup` | La même première configuration sans questions, pour les scripts et les agents de programmation (voir [plus bas](#command-line-aipager-setup-for-coding-agents)). |
| `aipager start` | Fait tourner aipager au premier plan. Il refuse de démarrer pendant qu'un autre aipager tourne. |
| `aipager service install [--yes]` | Installe aipager comme service en arrière-plan (service utilisateur systemd sur Linux, agent launchd sur macOS) et le démarre. `--yes` saute la question quand le service installé est différent. |
| `aipager service start\|stop\|status\|logs\|uninstall` | Démarre, arrête ou affiche le service, suit son journal, ou le retire. |
| `aipager status [--json]` | aipager et toutes les sessions d'un coup d'œil : état, modèle, contexte, coût, et état des limites de débit de Telegram. |
| `aipager logs [-f] [-n LINES]` | Le journal d'aipager (journald sur Linux, le journal de launchd sur macOS) : les 100 dernières lignes, ou `-n` lignes ; `-f` le suit. |
| `aipager doctor [--fix] [--safety-check] [--json]` | Vérifications de santé (voir [dépannage](troubleshooting.md#aipager-doctor-check-list)). `--fix` propose de réparer l'identifiant Claude et une installation ambiguë de `claude`, en demandant d'abord ; `--safety-check` affiche les règles de sécurité en vigueur ; `--json` est pour les scripts (voir [plus bas](#aipager-doctor---json)). |
| `aipager session <name> [claude args]` | Lance une session Claude Code nommée `<name>` sous dtach dans le dossier actuel, ou s'y attache si elle tourne. Les arguments après le nom (ou après `--`) vont tels quels à `claude`, par exemple `--resume`. Demande un aipager en cours. |
| `aipager session ls [-a] [--json]` | Liste les sessions ; `-a` inclut les terminées. Aussi `aipager session list`. |
| `aipager session kill <name> [-y]` | Termine une session, après une question sauf avec `-y`. |
| `aipager resume [name]` | Ramène une session terminée avec sa conversation ; sans nom, une liste. |
| `aipager miniapp enable [--port PORT] [--url URL]` | Active la Mini App, sur `127.0.0.1:PORT` (par défaut 8765), derrière le tunnel géré ou votre propre URL `https://`. S'applique au prochain redémarrage d'aipager. |
| `aipager miniapp disable\|status` | La désactive (au prochain redémarrage), ou affiche ses paramètres et son adresse. |
| `aipager policy validate` | Vérifie `policy.yaml` (et `policy.d`) ; sort avec un code non nul en cas de problème. |
| `aipager update` | Met à jour aipager par l'installateur qui le possède (uv, pipx, Homebrew, pip). Il ne redémarre jamais aipager ; redémarrez-le vous-même, ou utilisez `/update` dans Telegram, qui le peut (voir [Mise à jour](#update)). |
| `aipager report [--note TEXT]` | Affiche un rapport de problème et l'envoie si vous dites oui (voir [plus bas](#command-line-aipager-report)). |
| `aipager uninstall [-y]` | Arrête et retire aipager et sa configuration, en gardant ses données (voir [plus bas](#command-line-aipager-uninstall)). |
| `aipager version`, `aipager --version` | Affiche la version. |
| `aipager help [command]` | L'aide d'aipager ou d'une commande. |

## <a id="command-line-aipager-setup-for-coding-agents"></a>Ligne de commande : `aipager setup` (pour les agents de programmation)

Les sections ci-dessus décrivent le bot Telegram. Celle-ci décrit une commande que vous lancez dans un terminal, ou qu'un script ou votre agent de programmation lance pour vous : `aipager setup` configure aipager sans poser de questions et donne le même résultat que le premier lancement d'`aipager config`.

```sh
# 1. trouver l'identifiant Telegram de la personne (elle ouvre le bot et appuie sur Start)
aipager setup detect-chat --token-file ~/bot-token.txt --json
# 2. une fois qu'elle a confirmé que c'est bien elle, configurer aipager
aipager setup --token-file ~/bot-token.txt --chat-id 123456789 --service --json
# 3. vérifier l'installation
aipager doctor --json
```

Ce qu'elle fait, dans cet ordre : elle lit le token du bot, le vérifie auprès de Telegram (getMe), vérifie tout sur cet ordinateur sans rien écrire (la configuration existante, les dépendances, `~/.claude/settings.json`, si un aipager tourne), envoie un message de test à la personne ("aipager is set up for you. ..."), et seulement ensuite écrit. Les fichiers écrits, de la même façon que les écrit l'assistant :

- `~/.config/aipager/aipager.yaml` (droits 0600) : `bot_token` et une discussion privée dont le seul membre est la personne (`label: owner`, rôle owner ou admin).
- `~/.claude/settings.json` : les hooks d'aipager et la statusLine (le fichier existant est sauvegardé dans `settings.json.bak.<time>` quand il change).
- Le journal d'audit reçoit une ligne `grant-owner` quand quelqu'un devient propriétaire.
- Avec `--service`, le service en arrière-plan (comme `aipager service install --yes`).
- Seulement sur une ancienne installation avec `config.env` (pas encore d'`aipager.yaml`), la migration que fait l'assistant : une copie `config.env.bak.<time>` (et `team.yaml.bak.<time>` quand il y a un `team.yaml`), et un `~/.config/aipager/policy.yaml` de départ quand il n'y en a pas. `config.env` lui-même est laissé tel quel.

Si une vérification échoue, rien n'est envoyé et rien n'est écrit. Quand un aipager tourne déjà, un changement de discussion ou de rôle est rechargé à chaud ; un nouveau token de bot demande un redémarrage (`aipager service stop`, puis `aipager service start`), que setup ne fait jamais lui-même, et `--service` est sauté. Si setup ne peut pas savoir si un aipager tourne, il fait comme s'il en tournait un (avertissement `daemon_unknown`) : il n'installe alors jamais le service, donc il ne peut jamais redémarrer un aipager en cours, et après tout changement il n'envoie aucun rechargement et indique `restart_needed: true` à la place.

### <a id="aipager-setup-flags"></a>Options d'`aipager setup`

| Option | Sens |
|---|---|
| `--token-file PATH` | Lit le token du bot dans ce fichier (4096 octets au plus ; du texte autour d'un token ne pose pas de problème). Un fichier que d'autres utilisateurs peuvent lire marche mais donne l'avertissement `token_file_shared` : faites `chmod 600` dessus, ou supprimez-le une fois setup réussi. |
| `--token-stdin` | Lit le token du bot sur stdin (un pipe ou un fichier redirigé, jamais un terminal). Le pipe doit se fermer : setup attend la fin de stdin au plus 30 secondes, puis sort avec 2 et `token_stdin_timeout`. `printf '%s\n' "$TOKEN" \| aipager setup --token-stdin ...` et `< token.txt` le ferment tous les deux. |
| `--chat-id N` | Obligatoire. L'identifiant numérique d'utilisateur Telegram de la personne, qui est aussi l'identifiant de sa discussion privée. Les groupes s'ajoutent avec `aipager config`. |
| `--role owner\|admin` | Le rôle de la personne (par défaut `owner`). |
| `--service` | Installe et démarre aussi le service en arrière-plan (sauté pendant qu'un aipager tourne). |
| `--force` | Permet de remplacer le token du bot, la discussion privée du propriétaire ou le rôle d'une installation existante. Les discussions de groupe et les autres discussions privées sont gardées. |
| `--dry-run` | Fait toutes les vérifications, n'envoie rien, n'écrit rien, et indique ce qui changerait. |
| `--json` | Affiche exactement un objet JSON sur stdout ; tout le reste va sur stderr. |

Donnez exactement une des deux options `--token-file` et `--token-stdin`. Le token n'est jamais accepté comme valeur d'une option : `--token` et `--bot-token` sont refusées (`token_on_command_line`) et la valeur n'est pas affichée. Les options abrégées (`--token-f`, `--dry`) ne sont pas acceptées. setup ne pose jamais de question, n'a jamais besoin d'un terminal et ne lit jamais le token dans une variable d'environnement.

Le relancer avec le même token, la même discussion et le même rôle ne change rien et le dit (`unchanged`, pas de message de test). Un token, une discussion ou un rôle différents sur une installation existante sortent avec 6 tant que vous n'ajoutez pas `--force`. Une ancienne installation avec `config.env` est d'abord migrée, comme le fait l'assistant (il donne le rôle `admin`, donc `--role owner` dessus demande `--force`).

### <a id="aipager-setup-detect-chat"></a>`aipager setup detect-chat`

```sh
aipager setup detect-chat (--token-file PATH | --token-stdin) [--timeout SECONDS] [--json]
```

Attend le message privé le plus récent envoyé au bot (appuyer sur Start en envoie un) et affiche son expéditeur : identifiant, nom et nom d'utilisateur. Il n'enregistre rien, donc montrez d'abord la personne trouvée, et ne lancez `aipager setup --chat-id` qu'une fois qu'elle a été confirmée. Seuls comptent les messages envoyés au plus 10 minutes avant le début de la commande. `--timeout` va de 0 à 3600 secondes (par défaut 300 ; 0 veut dire regarder une seule fois). Il lit Telegram sans confirmer aucun message, donc le `/start` reste disponible pour l'assistant et pour aipager.

Pendant qu'un aipager tourne pour le même bot, aipager reçoit les messages du bot, et il ne note pas les messages privés des personnes qui ne sont pas encore configurées. detect-chat sort alors avec 9 (`source: "daemon"`) : arrêtez aipager (`aipager service stop`) et relancez-le, ou demandez à la personne son identifiant numérique. Avec un aipager pour un autre bot, il lit directement Telegram (`source: "telegram"`).

Limite : comme detect-chat ne confirme jamais de message, Telegram ne lui montre que les 100 plus anciennes mises à jour non lues des dernières 24 heures. Quand le bot en a autant en attente, un nouveau `/start` ne peut pas être vu : detect-chat dépasse alors son délai (sortie 8) avec l'avertissement `updates_backlog`, et appuyer de nouveau sur Start n'aide pas. Demandez plutôt à la personne son identifiant Telegram numérique (ou démarrez aipager une fois, ce qui lit et vide ces mises à jour).

### <a id="exit-codes"></a>Codes de sortie

| Code | `error` | Sens |
|---|---|---|
| 0 | | Succès (`installed`, `updated`, `unchanged`, `dry_run`, ou detect-chat `found`). |
| 1 | `config_malformed`, `ambiguous_install`, `settings_invalid`, `test_send_failed`, `write_failed`, `service_failed`, `internal_error` | Échec. `ambiguous_install` : plus d'une discussion privée pourrait être celle du propriétaire ; corrigez-le dans `aipager config`. |
| 2 | `usage`, `missing_token_source`, `token_source_conflict`, `token_file_unreadable`, `token_stdin_is_tty`, `token_stdin_timeout`, `token_malformed`, `token_on_command_line`, `bad_chat_id`, `bad_role`, `bad_timeout` | Entrée mauvaise ou manquante ; `fix` nomme l'option. Une option inconnue ou un mauvais mot après `setup` est signalé par l'analyseur d'arguments (texte simple, sans JSON), aussi avec 2. |
| 3 | `deps_missing` | Il manque dtach, claude, aipager-hook ou aipager-statusline ; `fix` contient les commandes. Rien n'a été écrit. |
| 4 | `token_rejected` | Telegram a refusé le token. |
| 5 | `chat_not_started`, `bot_blocked` | La personne n'a pas appuyé sur Start dans le bot ("Open t.me/<bot> and press Start, then run this again."), ou l'a bloqué. Rien n'a été écrit. |
| 6 | `existing_install` | Un autre token, une autre discussion ou un autre rôle est déjà configuré ; ajoutez `--force`. |
| 7 | `telegram_unreachable` | Erreur réseau, ou Telegram a répondu 429 ou 5xx. |
| 8 | `detect_timeout` | detect-chat n'a vu aucun message privé avant `--timeout` (avec l'avertissement `updates_backlog` quand 100 anciennes mises à jour non lues cachent les nouvelles). |
| 9 | `daemon_running` | detect-chat : un aipager en cours reçoit les messages du bot. |
| 10 | `updates_conflict` | detect-chat : un autre programme lit les messages de ce bot. |
| 130 | `interrupted` | Ctrl-C. |

Quand plusieurs vérifications échouent à la fois, le code suit cet ordre : `config_malformed`, `ambiguous_install`, `existing_install`, `settings_invalid`, `deps_missing`. `--dry-run` sort avec le code qu'aurait la vraie exécution pour ces vérifications.

### <a id="json-aipager-setup---json"></a>JSON : `aipager setup --json`

Chaque clé est toujours présente ; une valeur pas encore connue vaut `null`.

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

- `status` : `installed`, `updated`, `unchanged`, `dry_run` ou `error`.
- `changed` : ce qui a changé (ou changerait, lors d'un essai à blanc), dans cet ordre : `migrated_v1`, `bot_token`, `owner_dm`, `role`, `settings_json`, `service`.
- `test_message` : `sent`, `not_needed`, `skipped_dry_run`, `failed` ou `not_attempted`.
- `settings_json.status` : `created`, `patched`, `unchanged`, `would_change` ou `not_checked`.
- `daemon.reload` : `reloaded`, `refused`, `not_reloaded` ou `not_needed`. `restart_needed` vaut true après un nouveau token pendant qu'un aipager tourne, et après tout changement quand aipager n'a pas pu être rechargé ou que setup n'a pas pu savoir s'il en tourne un.
- `service.result` : `not_requested`, `installed`, `already_installed`, `skipped_daemon_running`, `would_install`, `failed`, ou `null` quand setup s'est arrêté avant.
- `warnings` : des objets avec `code` (`token_file_shared`, `reload_refused`, `daemon_unknown`, `audit_write_failed`) et `message`. `audit_write_failed` : le rôle de propriétaire est écrit, mais sa ligne `grant-owner` n'a pas pu être ajoutée au journal d'audit ; cette ligne est faite au mieux, donc le code de sortie reste 0.
- `next_step` après un changement alors qu'un aipager tourne et n'a pas pu être rechargé dit de le redémarrer (`aipager service stop`, puis `aipager service start`) ; il ne nomme le token du bot que quand le token a changé.
- Avec l'avertissement `daemon_unknown`, `next_step` ne dit jamais qu'aipager tourne. Il dit que setup n'a pas pu le savoir, renvoie à `aipager doctor --json`, et dit de démarrer aipager avec `aipager service install` ou `aipager start` s'il ne tourne pas. Quand un redémarrage serait nécessaire, il n'en demande un que si un aipager tourne, et sinon dit de le démarrer de la même façon.
- En cas d'erreur, `status` vaut `error` et `error`, `message` et `fix` disent ce qui s'est mal passé et quoi faire ; `next_step` répète `fix`. Quand l'erreur est arrivée après une écriture, `changed` liste ce qui a été écrit, et la sortie normale ajoute une ligne sur stderr `Already written: aipager.yaml, ... (changed: ...)`.

### <a id="json-aipager-setup-detect-chat---json"></a>JSON : `aipager setup detect-chat --json`

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

`candidate` vaut `null` sauf si `status` vaut `found`. `warnings` contient des objets avec `code` (`token_file_shared`, `updates_backlog`) et `message`. `other_candidates` compte les autres personnes qui ont écrit au bot pendant les mêmes 10 minutes : quand ce n'est pas 0, assurez-vous d'avoir la bonne personne. `next_step` répète l'option de token que vous avez utilisée, jamais le token.

### <a id="aipager-doctor---json"></a>`aipager doctor --json`

Les vérifications d'`aipager doctor` sous forme d'un seul objet JSON sur stdout. Comme `aipager doctor` simple, il ne fait que lire et n'envoie aucun message Telegram. Sort avec 1 quand une vérification échoue, sinon 0. Il ne peut pas être combiné avec `--fix` ni `--safety-check` (sortie 2 avec un objet d'erreur).

```json
{
  "command": "doctor",
  "version": "1.0.1",
  "ok": true,
  "summary": {"ok": 14, "warn": 1, "fail": 0},
  "checks": [{"key": "token_valid", "status": "ok", "title": "Telegram bot token", "detail": ["@example_bot"], "fix": null}]
}
```

Les `checks` sont dans cet ordre, avec ces clés : `config_parses`, `config`, `token_valid`, `chat_reachable`, `team`, `role_shell_access`, `peer_messages`, `claude`, `claude_auth`, `dtach`, `hook_scripts`, `settings_json`, `daemon`, `service_installed`, `service_unit_path`, `miniapp`. Une vérification qui plante garde sa clé avec l'état `warn`. Les textes sont simples (sans balisage), et tout ce qui a la forme d'un token de bot est remplacé par `<redacted>`.

## <a id="command-line-aipager-uninstall"></a>Ligne de commande : `aipager uninstall`

`aipager uninstall` arrête aipager et retire son service, supprime le dossier de configuration (`~/.config/aipager` : le token du bot, les discussions et les paramètres) et la liste des sessions (`~/.claude/aipager-sessions.json`), nettoie les fichiers d'aipager dans `/tmp`, et désinstalle aipager lui-même. Il demande d'abord (`-y` saute la question).

Il garde les données d'aipager, pour qu'une réinstallation reprenne là où elle s'était arrêtée : `~/.local/share/aipager` (les dossiers des sessions, les programmes d'aide téléchargés, les fichiers de verrou et de marque), `~/.local/state/aipager` (les traces des rapports de problèmes et l'état des langues), `~/.claude/aipager-audit.jsonl` (le journal des réponses aux autorisations), `~/.claude/aipager-flood-state.json` (l'historique des limites de débit de Telegram), `~/.claude/aipager-pending-users.json` (les personnes qui ont essayé le bot sans accès) et `~/aipager-users` (les [dossiers racines](groups.md#root-folders) des membres, avec leur travail). Avant de demander, il liste ceux qui existent sur votre ordinateur, et à la fin il affiche la commande `rm -rf` qui les supprime aussi.

Il retire aussi les entrées d'aipager lui-même du `~/.claude/settings.json` de Claude Code : les hooks qui lancent `aipager-hook` et la ligne d'état qui lance `aipager-statusline` (une ligne d'état à vous reste). Tout le reste de ce fichier reste comme avant, et le fichier tel qu'il était est gardé à côté sous le nom `settings.json.bak.<time>`. Si le fichier est un lien vers un autre fichier, ne peut pas être lu, n'est pas du JSON valide, ou n'a pas la forme que lit Claude Code, il est laissé exactement tel quel et uninstall dit quelles entrées retirer à la main. Un hook à vous qui mentionne encore `aipager-hook` ou `aipager-statusline` (par exemple un script `aipager-hook-capped.sh` qui l'enveloppe) reste aussi, et uninstall le nomme. Les sessions Claude Code déjà ouvertes continuent de lancer les hooks qu'elles ont chargés jusqu'à ce que vous les redémarriez. `aipager config` remet les entrées après une réinstallation. Votre bot Telegram et les anciennes copies `settings.json.bak.*` ne sont pas touchés, pas plus que `skipDangerousModePermissionPrompt` (le réglage qui saute l'avertissement de Claude Code avant qu'il tourne sans vérifications d'autorisation), qu'aipager règle mais que vous avez peut-être réglé vous-même.

## <a id="command-line-aipager-report"></a>Ligne de commande : `aipager report`

`aipager report` construit un rapport de problème du même type que le bouton Signaler un problème du bot, à partir de ce qu'aipager a enregistré sur cet ordinateur (avec moins de faits sur l'aipager en cours : le mode, les discussions, les fonctions, le temps de fonctionnement, les sessions et les détails de Claude Code restent inconnus ou vides), l'affiche en entier, et demande `Send this report? [y/N]`. Seuls `y` ou `yes` l'envoient ; toute autre réponse, ou pas de terminal du tout (un script, un pipe, le shell d'une session Claude Code), n'envoie rien. `--note "text"` ajoute une note de 500 caractères au plus, affichée dans le rapport imprimé. Il sort avec 0 quand le rapport a été envoyé, n'a pas été envoyé parce que vous l'avez choisi, ou a seulement été affiché, et avec 1 quand un envoi que vous avez confirmé n'est pas parti. Voir [Rapports de problèmes](problem-reports.md) pour ce que contient un rapport et où il va.

## <a id="settings-in-the-environment"></a>Paramètres de l'environnement

Quelques paramètres sont lus dans l'environnement d'aipager plutôt que dans `aipager.yaml`. Pour le service systemd, ajoutez-les avec `systemctl --user edit aipager` sous `[Service]` (`Environment=STALE_BUSY_TIMEOUT=900`), puis `aipager service stop` et `aipager service start` ; pour le service launchd sur macOS, ajoutez-les aux `EnvironmentVariables` du plist (`~/Library/LaunchAgents/com.aipager.daemon.plist` ; `aipager service install` réécrit ce fichier, donc remettez-les après) ; pour un aipager que vous lancez vous-même, définissez-les avant `aipager start`. Aucun n'est nécessaire pour un usage normal.

| Paramètre | Par défaut | Ce qu'il change |
|---|---|---|
| `AIPAGER_WHISPER_MODEL` | `base` | Le modèle de reconnaissance vocale pour les messages vocaux (`tiny`, `base`, `small`, `medium`, ...) : plus il est gros, plus il est précis et lent. |
| `MINIAPP_PORT` | `8765` | Le port de la Mini App sur `127.0.0.1` (`aipager miniapp enable --port` l'enregistre à la place). |
| `MINIAPP_ENABLED` | selon `aipager.yaml` | `0` désactive la Mini App et `1` l'active, quoi que dise `aipager.yaml` (`aipager miniapp enable\|disable` enregistre le choix à la place). |
| `MINIAPP_PUBLIC_URL` | aucun | Sert la Mini App à votre propre URL `https://` au lieu du tunnel géré ([Modèle de sécurité](security.md#mini-app-tunnel)). |
| `OBSERVER_BOTS` | aucun | Bots observateurs en lecture seule ([bots observateurs](observers.md)). |
| `KEEP_FINISHED_CARD` | `1` | `0` fait de « Réponse seule (sans carte) » l'affichage des discussions qui n'en ont jamais choisi un dans `/settings`. |
| `STALE_BUSY_TIMEOUT` | `600` | Les secondes qu'une session au travail peut passer sans nouvelles avant que la discussion reçoive la note « travaille encore (aucune nouvelle depuis 10 min) ». |
| `PROMPT_HOOK_GRACE_SECONDS` | `8` | Les secondes avant qu'un message que Claude Code n'a jamais pris soit marqué « Claude Code n'a pas reçu le message » ([messages libres](#text)). |
| `FINISH_CARD_GRACE_SECONDS` | `0.8` | De combien la carte terminée précède la réponse dans l'affichage avec carte ; `0` envoie les deux en même temps. |
| `GONE_SESSION_MAX_AGE_DAYS` | `14` | Les jours pendant lesquels une session terminée reste dans `/resume` ; `0` les garde. |
| `STREAM_EDIT_INTERVAL`, `BUSY_EDIT_INTERVAL` | `1.2`, `3.0` | Le rafraîchissement de la carte d'activité pendant que du texte défile et quand elle est calme, en secondes ; la limite par discussion de Telegram l'emporte toujours ([dépannage](troubleshooting.md#busy-cards-got-slower)). |
| `TYPING_INDICATOR_INTERVAL` | `4.5` | Les secondes entre les bulles « en train d'écrire… » ; `0` les désactive. |
| `TELEGRAM_MAX_RETRY_AFTER` | `90` | L'attente demandée par Telegram, en secondes, au-delà de laquelle aipager traite une limite de débit comme un blocage (ne la baissez pas ; voir [dépannage](troubleshooting.md#the-bot-went-quiet-flood-control)). |
| `CLAUDE_RICH_SUMMARIES` | `1` | `0` désactive la mise en forme enrichie des réponses qui contiennent des blocs de code (une solution de repli gardée des anciennes versions). |
| `AIPAGER_INTERACTIVE_TIMEOUT` | `300` | Les secondes qu'une demande d'autorisation peut attendre sans nouvelles de Claude Code avant que la session compte de nouveau comme au travail ([dépannage](troubleshooting.md#a-permission-prompt-with-no-news-for-5-minutes)). |
| `AIPAGER_SUBAGENT_SILENCE` | `1800` | Les secondes de silence après lesquelles un sous-agent dont l'arrêt n'a pas été vu est retiré ([hooks](hooks.md#subagentstart--subagentstop)). |
| `AIPAGER_BG_SHELL_MAX_TRACK` | `7200` | Les secondes après lesquelles une commande en arrière-plan dont la fin n'a pas été vue n'est plus comptée. |
| `AIPAGER_REPORT_PROMPTS` | activé | `0` désactive les propositions automatiques de rapports de problèmes ([rapports de problèmes](problem-reports.md#when-aipager-offers-a-report)). |
| `AIPAGER_CLAUDE_BIN` | aucun | Le `claude` à lancer, quand plusieurs sont installés (`claude_path` dans `aipager.yaml` l'emporte). |
| `AIPAGER_SOCKET_PATH` | `$XDG_RUNTIME_DIR/aipager.sock` | Le socket de contrôle d'aipager. Les sessions lancées par aipager le transmettent à leurs hooks. |
| `AIPAGER_DEBUG` | désactivé | `1` dans l'environnement d'une session fait afficher à `aipager-hook` et `aipager-statusline` leurs diagnostics sur stderr. |
| `AIPAGER_INSTANCE_DIR`, `AIPAGER_TELEGRAM_API_BASE` | aucun | Un second aipager isolé pour les tests ([architecture](architecture.md#isolated-instance-for-testing)). |

## <a id="see-also"></a>Voir aussi

- [Architecture](architecture.md) : où se place le bot.
- [Événements de hook](hooks.md) : ce qu'aipager entend venir de claude.
- [Dépannage](troubleshooting.md) : quand les commandes se comportent mal.
