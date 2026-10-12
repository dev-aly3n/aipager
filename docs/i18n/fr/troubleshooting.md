# Dépannage

<!-- i18n-source: docs/troubleshooting.md @ 01d5a8b -->

> Cette page est une traduction de la page anglaise ; si les deux ne disent pas la même chose, c'est la version anglaise qui fait foi. ([English](../../troubleshooting.md))

Les pannes courantes et leur solution. En cas de doute, lancez :

```sh
aipager doctor
```

Il fait toutes les vérifications ci-dessous dans l'ordre et affiche la solution conseillée pour chaque échec. Source : `aipager/doctor.py`.

## <a id="another-aipager-daemon-already-owns-the-socket"></a>« Another aipager daemon already owns the socket »

Deux aipager ne peuvent pas partager un même socket de contrôle. Cette erreur veut dire qu'un aipager tourne encore et répond sur ce socket (le socket est `$XDG_RUNTIME_DIR/aipager.sock`, ou `/tmp/aipager.sock` quand `$XDG_RUNTIME_DIR` n'est pas défini). Un fichier de socket laissé par un aipager qui a planté n'est pas un problème : le démarrage suivant le supprime et en crée un nouveau. Arrêtez d'abord l'aipager qui tourne :

```sh
aipager service stop          # s'il tourne comme service
pkill -f 'aipager start'      # sinon
```

Si la vérification du socket ne le voit pas, `aipager start` refuse quand même avec `aipager is already running (pid=N)` et nomme son fichier de verrou (`~/.local/share/aipager/daemon.lock`) : arrêtez ce processus de la même façon.

## <a id="telegram-bot-doesnt-respond"></a>Le bot Telegram ne répond pas

1. `aipager status` : si la ligne d'aipager dit `not running`, démarrez-le.
2. `aipager doctor` : les vérifications `token_valid` et `chat_reachable` interrogent l'API de Telegram de bout en bout et affichent l'erreur exacte.
3. Mauvaise discussion : lancez `aipager config` et choisissez **Test bot reachability**. Pour corriger une mauvaise discussion privée, ajoutez la bonne avec **Add a DM scope** (voir [trouver les identifiants](groups.md#finding-ids-auto-detect)), puis retirez la mauvaise dans **Edit a scope**.
4. Le bot n'a jamais reçu `/start` : ouvrez le bot dans Telegram et touchez Start une fois.

## <a id="busy-cards-got-slower"></a>Les cartes d'activité sont devenues plus lentes

Telegram autorise environ un message par seconde dans une même discussion (et 20 par minute dans un groupe). Chaque message et chaque modification comptent. aipager donne à chaque discussion son propre budget d'environ 1 appel par seconde, avec une petite rafale possible, et les cartes d'activité de cette discussion le **partagent**. L'indicateur « en train d'écrire… » fait aussi partie de ce budget, comme la chose la **moins** prioritaire qu'envoie la discussion (voir « La bulle d'écriture » plus bas).

La part de la bulle est mise de côté d'abord (un appel toutes les 4,5 secondes tant que la tâche au travail la plus ancienne de la discussion a moins de dix minutes, moins souvent ensuite, voir « La bulle d'écriture »), et les cartes se partagent le reste. Ainsi, une seule carte dans une discussion privée est rafraîchie environ toutes les 4,8 secondes ; avec deux sessions au travail dans la même discussion, chaque carte environ toutes les 9,7 secondes ; avec trois, environ toutes les 14,5. **Une carte plus lente, c'est le budget qui fait son travail, pas un bug.** Les réponses, les messages en réponse et les réactions aux boutons ne sont jamais ralentis pour faire de la place à une carte : ils gardent un jeton réservé, et une modification de carte qui ne peut pas se payer un appel est simplement sautée et retentée au passage suivant, plutôt que mise en file devant votre réponse.

Dans un **groupe**, la limite est de 20 appels par minute, quel que soit le nombre de sessions, et la bulle seule en utiliserait les deux tiers. Les cartes du groupe gardent ensemble un rafraîchissement toutes les 10 secondes (11 s pour une seule carte), et la bulle prend le reste : dans un groupe, elle peut donc disparaître une seconde ou deux de temps en temps.

Pour accélérer les cartes : faites tourner moins de sessions en même temps par discussion, ou donnez aux plus actives une discussion à elles (`aipager config`). Vous pouvez aussi régler `STREAM_EDIT_INTERVAL` (par défaut `1.2`, le rythme quand la carte affiche du texte qui défile) et `BUSY_EDIT_INTERVAL` (par défaut `3.0`, quand elle est calme), comme variables d'environnement d'aipager (voir [paramètres de l'environnement](commands.md#settings-in-the-environment)). Mettre l'une ou l'autre *en dessous* du plancher par discussion ne fait aucun mal et ne change rien : le plancher l'emporte, et c'est ce qui garde la discussion sous la limite de Telegram quoi que vous régliez.

### <a id="a-long-turns-card-refreshes-less-often"></a>La carte d'une longue tâche est rafraîchie moins souvent

Le rythme ci-dessus vaut pour les deux premières minutes d'une tâche. Ensuite, la carte **ralentit à mesure que la tâche vieillit**, et à partir de 10 minutes son horloge avance par pas de 30 s, au même rythme que les rafraîchissements, pour qu'elle n'ait jamais l'air figée :

| âge de la tâche | la carte est rafraîchie au plus toutes les | l'horloge affiche |
|---|---|---|
| 0–2 min | comme ci-dessus (environ 4,8 s pour une carte dans une discussion privée) | `0:45` |
| 2–10 min | 10 s | `4:10` |
| 10–60 min | 30 s | `23:00`, `23:30` |
| plus de 60 min | 30 s | `1:23:00`, `1:23:30` |

L'horloge est arrondie vers le bas à son pas de 30 s, et les rafraîchissements sont espacés d'au moins 30 s, parfois un peu plus. La carte saute donc un pas de temps en temps : un rafraîchissement à 23 min 29 s affiche `23:00`, et si le suivant arrive à 24 min 1 s il affiche `24:00`, donc `23:30` est sauté.

Une nouvelle ligne d'outil ou une nouvelle phrase attend le rafraîchissement suivant ; un **changement d'état** (la session qui passe de au travail à l'attente d'un agent en arrière-plan, et inversement) s'affiche tout de suite, au plus une fois toutes les 10 secondes. Les lignes des agents en direct et la ligne d'attente suivent les mêmes pas. La carte terminée affiche la durée de la tâche en vraies secondes (`23:17`).

C'est ce qui empêche une tâche de quatre heures de modifier un même message des milliers de fois : le 2026-09-23, une carte a fait exactement cela, et la discussion a été bloquée pendant sept heures. Pour garder le rythme rapide pendant toute une tâche, désactivez **`/settings` → ⏱ Mise à jour de carte (tâches longues)** (pour la discussion, ou pour une seule session depuis la Mini App ou `👤 Paramètres de chaque session`).

### <a id="the-typing-bubble"></a>La bulle d'écriture

La bulle « en train d'écrire… » est envoyée par **une seule** boucle par discussion, quel que soit le nombre de sessions au travail dedans (la bulle est par discussion dans tous les clients Telegram), toutes les `TYPING_INDICATOR_INTERVAL` secondes (par défaut 4,5 : Telegram efface un état d'écriture au bout de 5 ; `0` désactive la bulle). Elle compte dans le budget de la discussion comme la chose la moins prioritaire qu'envoie la discussion :

- elle **ralentit à mesure que la tâche vieillit**, d'après l'âge de la tâche au travail la plus ancienne de la discussion : toutes les 4,5 s pendant les dix premières minutes (une bulle continue), toutes les 15 s ensuite ; Telegram l'affiche 5 s, donc la bulle d'une tâche plus ancienne clignote au lieu de rester allumée. À 4,5 s, elle ferait à elle seule 800 appels par heure et la limite horaire (plus bas) la couperait pendant de longues périodes ; désactiver **⏱ Mise à jour de carte (tâches longues)** la garde à 4,5 s ;
- elle **s'affiche dès le début de chaque tâche** ; une jeune carte est rafraîchie un peu plus lentement pour lui faire de la place : la part de la bulle est mise de côté avant que les cartes se partagent le reste (plus haut), donc les deux ensemble ne dépassent jamais les limites de la discussion ;
- elle ne prend jamais un jeton dont une modification de carte a besoin : une bulle prévue juste après une modification de carte attend une demi-seconde (`TYPING_RETRY_WAKE`) et réessaie, donc elle a au plus une seconde ou deux de retard, sans jamais manquer ;
- c'est la première chose abandonnée quand le volume horaire de la discussion devient élevé (plus bas) ;
- un 429 sur la bulle elle-même ne bloque que la bulle, aussi longtemps que Telegram l'a demandé (les cartes continuent), mais il déclenche bien le régime d'avertissement de six heures de la discussion (le rythme est plafonné à 0,5 appel/s, donc les cartes ralentissent aussi) ;
- un **blocage** sur la bulle (un `retry_after` au-delà du plafond plus bas) réduit la discussion au silence exactement comme un blocage sur n'importe quel autre appel : plus rien n'y entre, et la réponse de la tâche est retenue et livrée quand le blocage se lève.

Elle était gratuite autrefois (une boucle par session au travail, hors de tout budget), jusqu'à ce que, le 2026-09-23, deux sessions d'une même discussion privée l'aient envoyée environ 5 400 fois en trois heures et que Telegram limite la bulle elle-même.

## <a id="the-bot-slowed-down-a-rate-limit-429"></a>Le bot a ralenti : une limite de débit (429)

Si Telegram limite quand même une discussion, il répond à un appel avec `429` et un `retry_after` de quelques secondes. aipager arrête d'appeler cette discussion pendant exactement ce temps, refait ensuite l'envoi différé, et **double l'intervalle des cartes de cette discussion** (jusqu'à ×8). Chaque minute calme le divise de nouveau par deux jusqu'à la normale.

Ce que vous voyez :

- `aipager status` (et la ligne `aipager daemon` d'`aipager doctor`) affiche **`Telegram flood backoff ×4 (chat …), last 429 12 s ago`**.
- Exactement une ligne dans `aipager logs`, sans trace d'appels : `flood: chat … 429 retry_after=5s → cadence ×2`.
- Rien n'est réduit au silence, et **aucune réponse n'est perdue** : elle est différée, pas abandonnée.
- Le **débit gagné de la discussion est divisé par deux**, et la discussion entre dans un **régime d'avertissement de six heures** (`FLOOD_WARNING_HOURS`) : son débit ne peut pas monter au-delà de 0,5 appel/s (`FLOOD_WARNED_CEILING`), et il remonte lentement. `aipager status` l'affiche : `Telegram chat 123: rate 0.25/s (ceiling 0.50/s), 212/1200 calls in the last hour (as of 18s ago), 7/30 in the last minute - warning regime, 5h 52m left`.

Rien à faire. Cela se règle tout seul. La ligne de ralentissement disparaît une fois que la discussion est restée calme une minute ou deux ; le débit remonte de 0,1 appel/s par période calme (une minute normalement, 36 minutes pendant le régime d'avertissement) et le plafond complet revient quand le régime se termine. `aipager doctor` garde la ligne d'aipager au vert pendant tout ce temps : une discussion qui ralentit, c'est le fonctionnement normal. (Un 429 était autrefois oublié au bout d'environ cinq minutes. Le seul avertissement de Telegram avant le blocage du 2026-09-23 était arrivé 3 h 23 min plus tôt, et la discussion était revenue à pleine vitesse quelques minutes après.)

### <a id="the-hourly-budget"></a>Le budget horaire

Chaque discussion a aussi une **heure** glissante : au plus `FLOOD_HOURLY_MAX` (1200) appels sur n'importe quelles 60 minutes, bulles d'écriture, modifications de carte et réponses confondues ; seules les réactions en sont exemptées. Les 120 derniers (`FLOOD_HOURLY_ESSENTIAL_RESERVE`) sont gardés pour les réponses, les messages en réponse et les demandes :

- à 810 appels dans l'heure, la bulle d'écriture se met en pause (elle revient sous 648) ;
- à 1080, la discussion entre en **mode minimal** (plus bas) jusqu'à ce que l'heure redescende à 864 ;
- les réponses ne sont jamais refusées par l'heure ; si elles dépassent un jour 1200, le journal le dit une fois par heure.

`aipager status` affiche les appels de la discussion dans la dernière heure face à son budget. Le chiffre vient du fichier d'état, qu'aipager réécrit au plus une fois par minute tant que les appels continuent, il est donc accompagné de son âge. Deux sessions actives qui écrivent pendant des heures dans une même discussion privée restent bien en dessous du budget (l'incident du 2026-09-23, rejoué : environ 900 appels dans l'heure la plus chargée, contre environ 3 400 estimés pour le même travail sans ce budget).

### <a id="the-earned-rate-and-why-your-cards-may-be-slower-than-they-were"></a>Le débit gagné, et pourquoi vos cartes peuvent être plus lentes qu'avant

Depuis la 0.7.13, le débit d'envoi soutenu de chaque discussion est **appris et non supposé**. Telegram ne publie pas la limite qui s'applique vraiment à votre bot (elle dépend de l'historique récent de votre compte), alors aipager démarre chaque discussion à 0,5 appel/s, ajoute 0,1 par minute calme jusqu'à un plafond de 1/s, le divise par deux sur un 429 et le descend à 0,05 après un blocage. Un bot qui vient d'être bloqué avance donc beaucoup plus prudemment qu'un bot qui ne l'a pas été, et il regagne sa vitesse au fil des heures suivantes.

Si le débit d'une discussion tombe sous 0,2 appel/s, ou si la part de son budget horaire réservée aux cartes et aux bulles est épuisée, elle entre en **mode minimal** : les cartes d'activité arrêtent de s'animer et affichent une seule ligne fixe `⏳ x1 · au travail (la carte n'est pas mise à jour pour l'instant)`, la bulle d'écriture s'arrête, et la barre d'état épinglée affiche `⏸ les cartes ne sont pas mises à jour pour l'instant (limite horaire de Telegram)` (ou `(limite de Telegram)` quand c'est le débit qui y a mis la discussion) et sinon ne change plus jusqu'à ce que cela se lève. **Les réponses, les messages en réponse et les demandes d'autorisation continuent de passer** : c'est tout l'intérêt. La carte représente environ 95 % de ce qu'envoie ce bot et la réponse environ 5 %, donc sous la pression il sacrifie des pixels plutôt que du travail. `aipager doctor` signale le mode minimal comme un avertissement pour qu'une carte en pause ne soit jamais prise pour une carte cassée ; il se lève tout seul à mesure que le débit remonte.

## <a id="the-bot-went-quiet-flood-control"></a>Le bot est devenu muet : un blocage de Telegram (flood control)

Un `retry_after` au-delà de `TELEGRAM_MAX_RETRY_AFTER` (par défaut 90 s) n'est pas une limite de débit : c'est un **blocage**, et il peut durer des heures. Depuis la discussion, on dirait que le bot est mort : les messages arrivent toujours à Claude, les sessions continuent de travailler, mais aucune réponse ne revient.

Ce que vous voyez :

- `aipager status` (et la ligne `aipager daemon` d'`aipager doctor`) affiche **`Telegram flood-muted until HH:MM (chat …)`**.
- Une ligne dans `aipager logs` : `Telegram flood control — chat … muted for Ns (until HH:MM) …`, puis le silence pour cette discussion. Plus tard, une ligne `flood mute on chat … lifted`.
- `aipager status` montre aussi l'état complet de la discussion : `Telegram chat 123: rate 0.05/s (ceiling 0.50/s), 431/600 calls in the last hour (as of 12s ago) - MINIMAL MODE, card updates paused - flood-muted until 09:41 - warning regime, 5h 59m left (1 ban(s) in the last 7 days)`.

aipager réduit la discussion au silence pendant exactement le temps que Telegram a demandé et ne lui fait **aucun appel d'aucune sorte** : réponses, modifications de la carte d'activité, pièces jointes, réactions, indicateurs d'écriture, notifications après l'appui sur un bouton et les réponses qu'auraient produites vos commandes. Chacun serait une nouvelle infraction qui prolongerait le blocage ; c'est une requête envoyée pendant un blocage actif qui a transformé un blocage de 21 minutes en un blocage de 9 h 30 le 2026-09-15. Une commande tapée pendant un silence ne reçoit donc aucune réponse, et **l'appui sur un bouton n'est pas confirmé non plus** : jusqu'à la 0.7.12, la notification partait quand même, en pensant que Telegram la compte à part. C'est vrai pour le rythme ; ce n'est pas vrai pour les blocages. Les autres discussions ne sont pas touchées.

**Écrire au bot pendant un blocage est sans danger, et rien n'est perdu.** Rien de ce que vous faites dans la discussion ne pousse le bot à appeler Telegram tant que la discussion est réduite au silence, donc cela ne peut pas prolonger le blocage :

- Un message que vous envoyez (texte, message vocal, fichier, modèle rapide) arrive quand même à Claude et la session y travaille. Sa carte d'activité est retenue, pas abandonnée : si la tâche tourne encore quand le blocage se lève, la carte apparaît à ce moment-là. Un blocage laisse la discussion en mode minimal un moment après, donc la carte peut mettre quelques minutes de plus que le blocage lui-même. Si la tâche s'est terminée pendant le blocage, vous recevez sa réponse et pas de carte.
- **🔄 Réessayer** ne fait rien pendant un blocage. Le message n'est pas renvoyé et le bouton reste, pour que vous puissiez le toucher de nouveau une fois le blocage levé. Jusqu'à la 0.7.14 incluse, le message partait vers Claude alors que le message d'erreur et son bouton restaient, donc un second appui le renvoyait.
- Le clavier principal est retenu lui aussi, y compris celui qu'envoie un redémarrage (jusqu'à la 0.7.14, il était perdu jusqu'à ce que quelque chose d'autre le rafraîchisse). Il est envoyé une fois le blocage levé.

Une fois le blocage levé, les choses arrivent dans cet ordre : les réponses retenues, puis les cartes des tâches qui tournent encore, puis le clavier. Une carte qui doit attendre la fin du mode minimal arrive après le clavier.

**Les réponses ne sont pas perdues.** Une réponse produite pendant que la discussion est réduite au silence est retenue et livrée une fois le blocage levé, en quelques secondes, avec une première ligne qui dit `⏳ arrivé en retard : retenu 42 min par une limite de Telegram`. Vous n'avez pas besoin de redemander, et si vous avez redemandé, les deux réponses arrivent, la plus ancienne d'abord : un blocage de plusieurs heures couvre plusieurs tâches et la réponse de chacune vous revient. (Les réponses retenues vivent en mémoire : si vous redémarrez aipager alors que certaines attendent, elles sont perdues ; le journal d'arrêt dit combien. Une discussion en retient au plus 20, et rien de plus de 24 heures ; tout ce qui est abandonné pour l'une ou l'autre raison donne un avertissement dans le journal qui dit ce que c'était.)

**Un blocage rend la discussion plus lente ensuite, pas plus rapide.** Dès qu'un blocage est armé, le débit appris de la discussion tombe au plancher et le blocage est compté ; les heures de silence ne font rien regagner, la remontée recommence au moment où le blocage se lève, et **pendant sept jours** (`FLOOD_BAN_MEMORY_DAYS`) le plafond de débit de la discussion *et* son budget horaire sont divisés par un plus le nombre de blocages de la semaine : un blocage divise les deux par deux, deux les divisent par trois. `aipager status` affiche tout cela. (Cette mémoire durait autrefois 24 heures et ne divisait que le plafond ; une discussion bloquée le 2026-09-19 était revenue à pleine vitesse, et bloquée pendant sept heures, le 2026-09-23.) Cinq blocages ou plus dans une semaine gardent la discussion en mode minimal jusqu'à ce qu'ils vieillissent : les réponses passent toujours, les cartes restent en pause.

Ce qu'il ne faut PAS faire :

- Ne redémarrez pas aipager pour « réparer ». Le silence se lève tout seul à l'heure affichée. Depuis la 0.7.13, un redémarrage n'oublie plus le blocage (l'échéance, le débit gagné de la discussion et son historique de blocages sont enregistrés dans `~/.claude/aipager-flood-state.json` et restaurés au démarrage, tout comme la dernière heure d'appels de la discussion et son régime d'avertissement), donc redémarrer ne le prolonge plus non plus. Mais cela jettera toutes les réponses encore retenues, et cela ne répare rien.
- Ne descendez pas `TELEGRAM_MAX_RETRY_AFTER` sous les 90 s par défaut en espérant réessayer plus tôt : tout ce qui dépasse ce plafond est un blocage, pas une limite de débit.

Pour l'éviter : faites tourner moins de sessions en même temps par discussion, ou donnez aux plus actives une discussion à elles (`aipager config`).

## <a id="a-session-dropped-off-the-pinned-status-bar-it-is-gone"></a>Une session a disparu de la barre d'état épinglée (elle est GONE)

Le processus dtach de cette session s'est terminé (redémarrage de l'ordinateur, `pkill claude`, quelqu'un a tapé `exit` dans la session dtach attachée). Recréez-la : son dossier `~/.claude/projects/...` contient toujours la conversation.

Le plus simple est `/resume <label>` dans Telegram (ou `/resume` seul pour une liste) : cela relance la session et reprend la conversation. Depuis la ligne de commande :

```sh
aipager resume <label>             # la même chose depuis le terminal
aipager session <label>            # ou une nouvelle session, sans historique
aipager session <label> --continue # un nouveau dtach, la dernière conversation du dossier
```

## <a id="a-permission-prompt-with-no-news-for-5-minutes"></a>Une demande d'autorisation sans nouvelles depuis 5 minutes

Si une session attend une demande d'autorisation ou une question et que Claude Code ne donne aucune nouvelle pendant 5 minutes, aipager lit la conversation enregistrée de la session. Ce n'est que si elle montre que la demande est finie (répondue dans le terminal, et la nouvelle s'est perdue) qu'aipager arrête de l'attendre et compte de nouveau la session comme au travail. Sinon la demande reste ouverte aussi longtemps qu'il le faut : la carte et la barre épinglée disent toujours que la session a besoin de votre réponse, aucune note « toujours au travail » n'est envoyée, et vos messages sont retenus jusqu'à votre réponse (voir [texte](commands.md#text)). Un Claude Code qui se ferme avec la demande ouverte termine la session. Les boutons de la demande répondent toujours à la boîte de dialogue si elle est encore ouverte (voir [réponses](commands.md#idle-responses)).

Changer l'attente :

```sh
AIPAGER_INTERACTIVE_TIMEOUT=300 aipager start    # en secondes
```

## <a id="voice-extra-wont-install-via-telegram"></a>La fonction vocale ne s'installe pas depuis Telegram

Le bouton `[📦 Installer la fonction vocale]` lance l'installateur et modifie son message toutes les quelques secondes pendant qu'il travaille (`toujours en cours… (25 s)`). Si l'installation échoue, le message affiche le code de sortie et les 500 derniers caractères de la sortie de l'installateur. Causes courantes :

- Réseau : pip ne joint pas PyPI. Vérifiez la connexion depuis l'ordinateur où tourne aipager.
- Disque : `~/.cache/huggingface/` a besoin d'environ 74 Mo pour le modèle et le cache des wheels de pip d'environ 250 Mo de plus. Libérez de la place d'abord.
- Permission denied : venv en lecture seule. Passez à `uv tool install`, qui utilise toujours un venv qui appartient à votre utilisateur.

Si le bot a perdu la connexion pendant l'installation, l'installation elle-même s'est en général terminée sur l'ordinateur : redémarrez aipager et réessayez un message vocal.

## <a id="open-app-shows-open-this-page-from-the-telegram-app-to-sign-in"></a>Ouvrir l'app affiche « Ouvrez cette page depuis l'app Telegram pour vous connecter. »

La page s'est chargée, mais pas le script du SDK Mini App de Telegram (celui qui produit `initData`), donc la page n'a rien pour se connecter et ne fait aucun appel à l'API. Le message laisse croire que c'est votre faute ; la cause est réseau.

Les versions récentes d'aipager récupèrent elles-mêmes ce script et le servent depuis l'origine de la page (`/telegram-web-app.js`), donc le téléphone n'a besoin de joindre que l'hôte qui a livré la page, pas `telegram.org` en plus. Si vous voyez cela sur une version plus ancienne, faites la mise à jour.

Ensuite, dans l'ordre :

1. Regardez la source de la page. Si sa balise `<script>` pointe vers `https://telegram.org/js/telegram-web-app.js`, aipager n'a pas encore réussi à récupérer le script et la page se rabat sur un chargement depuis Telegram, ce qui est justement le cas qui échoue sur un téléphone qui ne joint pas `telegram.org`. aipager le récupère quand le serveur de la Mini App démarre et réessaie au plus une fois par jour, donc redémarrer aipager réessaie tout de suite ; `journalctl --user -u aipager | grep "webapp sdk"` dit ce qui n'a pas marché (en général, un `telegram.org` bloqué ou filtré).
2. Si la balise pointe vers `/telegram-web-app.js`, vérifiez qu'aipager le sert : `curl -sI http://127.0.0.1:8765/telegram-web-app.js` sur l'ordinateur d'aipager (ajustez le port si vous l'avez changé) doit répondre `200` avec `Content-Type: application/javascript`. La copie se trouve dans `~/.local/share/aipager/webapp-sdk/`.
3. Vous avez ouvert l'URL dans un navigateur ordinaire au lieu du bouton `/app` ou du bouton de menu du bot : c'est le message attendu. Le SDK ne produit `initData` que dans Telegram.
4. La page est restée ouverte plus de cinq minutes : `initData` expire et la page demande à être rouverte. Touchez de nouveau `/app`.
5. Vous avez touché `📱 App` sur un clavier d'une ancienne version d'aipager. Telegram ne donne pas d'`initData` à une Mini App ouverte depuis un bouton du clavier, c'est pourquoi le clavier d'aipager n'a plus ce bouton. Envoyez `/start` pour avoir le clavier actuel, puis ouvrez l'app avec le bouton de menu ou avec `/app`.

## <a id="pyexpat-_xml_setalloctrackeractivationthreshold-on-brew-install"></a>`pyexpat _XML_SetAllocTrackerActivationThreshold` à l'installation par brew

Le paquet `python@3.12` de Homebrew a été compilé avec une libexpat plus récente que celle de votre système, presque toujours parce que **Xcode et les Command Line Tools ne sont pas à jour** sur macOS Tahoe (26.x). La sortie de brew elle-même vous le dit en général. Deux solutions :

- **Utilisez plutôt `uv tool install aipager`**. uv apporte son propre python, ce qui évite complètement le problème. C'est la voie recommandée sur macOS ; voir le [README](README.md#install).
- **Mettez à jour Xcode et les Command Line Tools** :
  ```sh
  sudo rm -rf /Library/Developer/CommandLineTools
  sudo xcode-select --install
  ```
  Ou ouvrez Xcode dans l'App Store et installez la dernière version.

## <a id="modulenotfounderror-aipager"></a>« ModuleNotFoundError: aipager »

Votre exécutable d'aipager renvoie à un Python dans lequel aipager n'est plus installé (souvent après l'effacement d'un venv). Réinstallez :

```sh
uv tool install --reinstall aipager
# ou avec l'installateur que vous aviez utilisé : pipx, brew, pip
```

## <a id="the-session-list-was-lost-or-damaged"></a>La liste des sessions a été perdue ou abîmée

aipager garde ses sessions dans `~/.claude/aipager-sessions.json`, réécrit de façon atomique (un nouveau fichier, puis un renommage), donc une écriture interrompue laisse l'ancien fichier entier. Si le fichier est quand même absent, illisible ou n'est pas du JSON valide, aipager écrit dans le journal qu'il repart de zéro (un avertissement quand le fichier était là mais n'a pas pu être lu) et démarre avec une liste vide ; il ne s'arrête pas. Il n'y a pas de copie de sauvegarde de ce fichier. Rien d'important n'est perdu :

- les sessions en cours sont retrouvées au premier passage du moniteur, grâce à leurs sockets dtach (`/tmp/claude-dtach-*.sock`) ;
- chaque conversation est toujours dans le dossier `~/.claude/projects/...` de Claude Code lui-même, donc `aipager session <name> --continue` dans le dossier de cette session la reprend (`--resume` vous laisse en choisir une) ;
- ce qui est perdu, c'est la liste des sessions terminées (`/resume`), les cartes qui étaient ouvertes, et le reste de ce dont aipager se souvenait sur chaque session.

Si aipager continue d'échouer au démarrage, `aipager logs` (ou `journalctl --user -u aipager`) dit pourquoi ; mettez le fichier de côté (`mv ~/.claude/aipager-sessions.json ~/aipager-sessions.json.old`) pour écarter cette piste.

## <a id="a-session-launch-fails"></a>Le lancement d'une session échoue

Un lancement que dtach refuse affiche une raison d'une ligne dans la discussion (le socket existe déjà, le shell n'est pas exécutable, pas de pseudo-terminal, nom trop long, permission refusée, dossier manquant) ; le message de dtach lui-même, avec les chemins, est dans `aipager logs`.

## <a id="aipager-doctor-check-list"></a>Liste des vérifications d'`aipager doctor`

L'ordre compte : chaque vérification suppose que les précédentes ont réussi. Une vérification qui plante sur un environnement inattendu s'affiche comme une seule ligne ⚠ qui nomme la vérification et l'erreur ; les vérifications restantes tournent quand même.

| Vérification (clé `--json`) | Ce qu'elle vérifie | Solution conseillée |
|---|---|---|
| `config_parses` | `aipager.yaml` et `policy.yaml` peuvent être lus ; passe en premier, puisque chaque vérification suivante lit la configuration | l'erreur nomme le fichier et la ligne |
| `config` | `~/.config/aipager/aipager.yaml` existe et contient un token et une discussion | `aipager config` |
| `token_valid` | Le token marche avec `getMe` de Telegram | relancer `aipager config` |
| `chat_reachable` | Telegram connaît la discussion configurée (`getChat` ; rien n'est envoyé) | ouvrir le bot, toucher Start |
| `team` | Les discussions et les membres (ou un ancien `team.yaml`) : dit si c'est l'usage personnel ou l'usage en équipe, et signale un ancien `team.yaml` mal formé ou qui ne correspond pas | `aipager config` |
| `role_shell_access` | Avertit une fois pour chaque rôle détenu par un membre qui peut lancer Bash (ses règles de sécurité sont au mieux, pas une barrière ; voir [groupes](groups.md#roles)) | ne donner aucun Bash à ce rôle dans `policy.yaml`, ou l'accepter |
| `peer_messages` | Les messages entre sessions Claude : où sont gardés leurs reçus et si ce dossier est privé, si ce système peut vérifier l'heure de démarrage de l'expéditeur, et un avertissement pour chaque rôle qu'une règle autorise à utiliser SendMessage (voir [sécurité](security.md#messages-between-claude-sessions)) | supprimer un dossier de reçus partagé (aipager le recrée), ou ne donner aucun SendMessage à ce rôle |
| `claude` | Trouve l'exécutable `claude` de la même façon que chaque lancement (`claude_path` dans `aipager.yaml` ou `$AIPAGER_CLAUDE_BIN` l'emportent directement ; sinon le plus récent de `~/.local/bin`, du PATH et de Homebrew), et liste chaque AUTRE installation distincte trouvée | installer Claude Code, ou définir `claude_path` / `AIPAGER_CLAUDE_BIN` |
| `claude_auth` | Essaie `claude auth status` dans le même environnement que celui d'une vraie session. **N'échoue jamais** : « pas connecté » et « l'essai lui-même a échoué » sont signalés à part, et aucun des deux n'empêche une session de se lancer | `claude auth login`, ou définir une clé d'API / `CLAUDE_CODE_OAUTH_TOKEN` |
| `dtach` | L'exécutable `dtach` est trouvé (le `dtach-bin` d'aipager lui-même, ou un dans le PATH) | `uv tool install --reinstall aipager` |
| `hook_scripts` | `aipager-hook` et `aipager-statusline` sont dans le PATH | `uv tool install --reinstall aipager` |
| `settings_json` | `~/.claude/settings.json` a les hooks d'aipager branchés | `aipager config` |
| `daemon` | aipager tourne et son socket répond ; affiche aussi l'état des limites de Telegram (voir [plus haut](#the-bot-slowed-down-a-rate-limit-429)) | `aipager start` |
| `service_installed` | Optionnel : le fichier du service est présent | `aipager service install` |
| `service_unit_path` | Linux seulement : le `Environment=PATH=` du service *installé* contient bien le dossier de l'exécutable claude trouvé, lu comme du texte, puisque doctor ne peut pas voir le PATH de systemd depuis le shell interactif de l'opérateur | `aipager service install --yes` |
| `miniapp` | Si la Mini App peut démarrer quand elle est activée. **N'échoue jamais** : la Mini App est optionnelle | la solution qu'elle nomme |

`aipager doctor --json` affiche les mêmes vérifications, dans cet ordre, avec ces clés (voir [commandes](commands.md#aipager-doctor---json)).

Lancez `aipager doctor --fix` pour chercher et copier de façon interactive un identifiant Claude dans `daemon.env` (aussi quand le fichier existe mais n'en contient aucun), ou pour figer `claude_path` quand plus d'une installation est trouvée. Il n'agit jamais sans demander d'abord. Pour un service dont le PATH ne contient pas le dossier de claude, lancez `aipager service install --yes`.

## <a id="the-daemon-cant-find-claude-or-picks-the-wrong-install"></a>aipager ne trouve pas `claude`, ou choisit la mauvaise installation

Six endroits trouvaient autrefois `claude` chacun de leur côté et pouvaient ne pas être d'accord. Ils passent maintenant tous par une seule fonction de recherche (`aipager/claude_resolve.py`). `claude_path` dans `aipager.yaml`, puis `$AIPAGER_CLAUDE_BIN`, l'emportent directement quand ils marchent. Sinon, chaque `claude` trouvé dans `~/.local/bin`, dans le `$PATH` et dans les préfixes de Homebrew est vérifié, et la version la plus récente l'emporte (donc changer l'ordre du `$PATH` ne change pas le choix quand l'autre installation est plus récente). Lancez `aipager doctor` pour voir exactement laquelle il a choisie et ce qu'il a trouvé d'autre ; si la mauvaise l'emporte, figez la bonne :

```sh
aipager doctor --fix        # choix interactif parmi les installations trouvées
# ou à la main :
echo 'claude_path: /home/you/.local/bin/claude' >> ~/.config/aipager/aipager.yaml
```

## <a id="sessions-cant-authenticate-under-the-systemd-service-but-claude-works-fine-in-a-terminal"></a>Les sessions n'arrivent pas à se connecter sous le service systemd, mais `claude` marche bien dans un terminal

Les services `systemctl --user` ne lisent jamais `~/.bashrc` ni `~/.profile`, donc une ligne `export CLAUDE_CODE_OAUTH_TOKEN=…` dans ces fichiers n'arrive jamais à aipager. Le service utilise à la place le `LoadCredential=` de systemd, qui lit `~/.config/aipager/daemon.env` : un simple fichier `KEY=VALUE`, en 0600, créé automatiquement la première fois que vous lancez `aipager service install` (recopié d'un ancien `config.env` s'il contenait un token, sinon trouvé une fois dans votre shell de connexion, sinon laissé vide avec un avertissement). S'il est toujours vide, `aipager doctor --fix` cherche de nouveau aux mêmes endroits si vous dites oui et ajoute la ligne qu'il trouve, en gardant les autres lignes du fichier ; il n'affiche jamais le token. Ou ajoutez la ligne vous-même :

```sh
cat ~/.config/aipager/daemon.env          # voir ce qu'il contient (ou pas)
echo 'CLAUDE_CODE_OAUTH_TOKEN=sk-...' >> ~/.config/aipager/daemon.env
chmod 600 ~/.config/aipager/daemon.env
aipager service stop && aipager service start
```

Voir [Modèle de sécurité : l'identifiant Claude](security.md#the-claude-credential-what-actually-protects-it) pour ce contre quoi ce fichier protège ou ne protège pas.

## <a id="the-daemons-control-socket-moved"></a>Le socket de contrôle d'aipager a changé de place

`aipager doctor` / `aipager status` regardaient autrefois toujours `/tmp/aipager.sock`. Sous le service systemd, le socket de contrôle se trouve maintenant à `$XDG_RUNTIME_DIR/aipager.sock` (avec un repli sur `/tmp` seulement quand `$XDG_RUNTIME_DIR` n'est pas défini : conteneurs, WSL1, distributions minimales). Seul ce socket a bougé ; les sockets dtach de chaque session (`/tmp/claude-dtach-*.sock`) ne sont pas touchés. Remplacez-le avec `AIPAGER_SOCKET_PATH` si vous avez besoin d'un emplacement précis.

## <a id="update-says-the-restart-would-kill-sessions-killmode"></a>`/update` dit que le redémarrage terminerait des sessions (KillMode)

Les services écrits avant cette version n'ont pas de ligne `KillMode=`, donc systemd utilise `control-group` : arrêter ou redémarrer `aipager.service` termine toutes les sessions Claude qu'aipager a lancées. `/update` installe donc la nouvelle version mais ne redémarre pas aipager, et liste les sessions qu'un redémarrage ferait tomber. Corrigez-le une fois :

```
aipager service install
```

Cela réécrit le service avec `KillMode=process`, recharge systemd et redémarre aipager, et ce redémarrage tourne déjà avec le nouveau réglage. Vérifiez avec `systemctl --user show -p KillMode aipager.service` (`KillMode=process`). Avec ce réglage, systemd écrit à chaque redémarrage des lignes « left-over process » pour vos sessions ; c'est normal.

## <a id="aipager-update-cant-find-uv--pipx"></a>`aipager update` ne trouve pas uv ou pipx

`aipager update` trouve l'installateur par chemin absolu dans votre `PATH` plus `~/.local/bin`, `~/.cargo/bin`, les préfixes de Homebrew, `/usr/bin` et `/bin`. S'il dit encore qu'il n'a pas trouvé `pipx`, l'installateur qui a créé cet aipager n'est plus là ou se trouve ailleurs ; réinstallez avec l'outil que vous avez (`pipx install --force aipager`, `uv tool install --reinstall aipager`).

## <a id="update-refused-editable--not-your-install"></a>Mise à jour refusée : editable ou installation qui n'est pas la vôtre

`/update` et `aipager update` ne mettent à jour qu'une installation qui appartient à votre utilisateur et qu'il peut modifier, par l'installateur qui l'a créée. Ils refusent, en donnant la raison, pour :

- une copie de travail editable (de développement) : mettez-la à jour avec `git pull` ;
- une installation par Nix, Snap, Docker ou un paquet du système : utilisez la mise à jour de ce système ;
- une installation qui appartient à un autre utilisateur (par exemple un venv de root sous `/opt`) : mettez-la à jour en tant que cet utilisateur ;
- une installation faite depuis un dossier local (`pipx install /path/to/aipager`) : `mise à jour impossible d'ici : it was installed from a local folder; update it from that folder`. Récupérez la nouvelle version dans ce dossier et réinstallez depuis lui, ou passez à PyPI avec `pipx install --force aipager` ;
- une installation dont on ne sait pas quel installateur l'a faite (`could not tell which installer owns this aipager`) : réinstallez-la avec `uv tool install aipager` ou `pipx install aipager`.

## <a id="update-failed-unexpected-argument---refresh"></a>La mise à jour a échoué : unexpected argument '--refresh'

aipager 1.0.0 et les versions plus anciennes lançaient `uv tool upgrade aipager --refresh`, qu'aucune version de uv n'accepte, donc chaque mise à jour d'une installation uv s'arrêtait avec `error: unexpected argument '--refresh' found` et ne redémarrait rien. Les versions plus récentes lancent `uv tool upgrade aipager`. Pour passer ce cap une fois, faites la mise à jour depuis un terminal sur cet ordinateur (`aipager update` a le même problème sur ces versions) :

```
uv tool upgrade aipager
systemctl --user restart aipager.service
```

Sans le service (ou sur macOS), redémarrez aipager comme vous l'aviez lancé.

## <a id="claude-code-update-installed-with-npm-into-a-folder-you-cant-write-to"></a>Mise à jour de Claude Code : installé avec npm dans un dossier où vous ne pouvez pas écrire

Un Claude Code installé avec `sudo npm install -g` se trouve dans un dossier que seul root peut modifier (comme `/usr/lib/node_modules`), donc `claude update` lancé avec votre utilisateur échoue avec `global folder isn't writable`. `/update` et la Mini App affichent la version plus récente avec `mise à jour impossible d'ici : installed with npm into a folder your user can't write to` et sans bouton pour elle. Lancez `claude install` une fois avec l'utilisateur qui fait tourner aipager : cela passe à l'installateur de Claude Code lui-même dans votre dossier personnel, qui fait les mises à jour sans sudo. Puis redémarrez aipager (`systemctl --user restart aipager.service`, ou comme vous l'aviez lancé) : un aipager qui tourne garde le Claude Code qu'il a trouvé au démarrage, et après le redémarrage il prend le plus récent qu'il trouve. Si `claude_path` dans `aipager.yaml` (`aipager doctor --fix` peut le définir) ou `AIPAGER_CLAUDE_BIN` désigne celui de npm, faites-le pointer vers `~/.local/bin/claude` ou retirez-le d'abord, puisqu'ils l'emportent toujours.

## <a id="updated-but-the-version-didnt-change"></a>Mis à jour, mais la version n'a pas changé

Une installation pipx faite depuis une URL git se met à jour depuis cette source git, pas depuis PyPI. Quand la source n'a rien de plus récent, `/update` dit `✅ aipager est déjà en A : cette installation pipx se met à jour depuis son dépôt git, qui n'a rien de plus récent.`. Publiez ou choisissez la nouvelle version là-bas, ou passez à PyPI avec `pipx install --force aipager`.

## <a id="the-daemon-didnt-come-back-after-an-update"></a>aipager n'est pas revenu après une mise à jour

La mise à jour vérifie que la nouvelle version se charge avant tout redémarrage, mais si le nouvel aipager n'arrive toujours pas à démarrer, systemd continue de réessayer toutes les 5 s. Regardez pourquoi :

```
journalctl --user -u aipager.service -n 50
```

Réinstallez la version précédente avec le même installateur, puis redémarrez :

```
pipx install --force aipager==<previous>        # ou :
uv tool install --force aipager==<previous>     # ou :
<venv>/bin/python -m pip install aipager==<previous>
systemctl --user restart aipager.service
```

Une installation pipx depuis un chemin local : `pipx install --force /path/to/aipager` au commit précédent. Si une annonce périmée attend, supprimez-la avec `rm -f ~/.local/share/aipager/update-restart.json` (elle est de toute façon ignorée après 24 h) ; `~/.local/share/aipager/update.lock` n'est qu'un fichier de verrou et peut être supprimé sans risque quand aucune mise à jour ne tourne.

## <a id="an-update-was-interrupted-when-aipager-shut-down"></a>« Une mise à jour est restée à moitié à l'arrêt d'aipager »

aipager s'est arrêté (ou a été redémarré) pendant que `/update` installait, et l'installateur a été arrêté avec lui, donc l'installation est peut-être à moitié écrite. Si aipager ou Claude Code se comporte mal, réparez-le : pour aipager, lancez la commande de réinstallation du message (par exemple `pipx install --force aipager`) puis `systemctl --user restart aipager.service` ; pour Claude Code, relancez `claude update`.

## <a id="a-chat-is-in-the-wrong-language"></a>Une discussion est dans la mauvaise langue

La langue d'aipager vient de ce qui a été choisi pour la discussion, pas des messages qu'elle contient. Ouvrez `/language` dans cette discussion et choisissez la langue que vous voulez : ce choix l'emporte sur tout le reste. Dans une discussion privée, **Automatique** suit la langue de votre app Telegram (Paramètres → Langue dans Telegram) ; une langue qu'aipager n'a pas s'affiche en anglais. Une discussion privée qui existait avant l'arrivée des langues reste en anglais tant que vous n'en choisissez pas une. Un groupe est en anglais tant qu'un admin n'a pas choisi sa langue. La langue ne change que les textes d'aipager lui-même : Claude répond dans la langue dans laquelle vous lui écrivez, quoi qu'affiche aipager. Voir [Langues](commands.md#languages).

## <a id="the-bot-description-is-not-the-one-i-wrote"></a>La description du bot n'est pas celle que j'ai écrite

aipager définit la description et la description courte du bot dans chaque langue qu'il parle, pour les personnes dont l'app Telegram est dans cette langue, et Telegram leur montre ce texte au lieu de celui que vous avez écrit dans BotFather. aipager ne le fait que tant que vous n'avez pas de description à vous, donc une description que vous écrivez avant d'installer cette version arrive à tout le monde. Si vous l'avez écrite après qu'aipager a défini la sienne, les personnes dans ces langues voient toujours celle d'aipager. Pour leur montrer la vôtre, effacez le texte d'aipager pour chaque langue par la Bot API (`setMyDescription` et `setMyShortDescription` avec un texte vide et ce `language_code`) ; aipager ne le redéfinit jamais tant que la vôtre est là.

## <a id="aipager-says-languagesjson-could-not-be-used"></a>aipager dit que languages.json n'a pas pu être utilisé

aipager garde ce qu'il a appris sur les langues (qui était là avant l'arrivée des langues, la dernière langue Telegram qu'il a vue chez chaque personne, et si la proposition faite une seule fois et la ligne du premier contact sont parties) dans `~/.local/state/aipager/languages.json`. Si ce fichier ne peut pas être lu, aipager le met de côté sous le nom `languages.json.bad-<time>` dans le même dossier, écrit un avertissement dans le journal (de nouveau à chaque démarrage tant que vous n'avez pas décidé), et garde toutes les discussions en anglais sans proposition de langue tant que vous n'avez pas décidé : remettez l'ancien fichier si vous pouvez le réparer, ou supprimez `languages.json` et redémarrez, et aipager compte de nouveau chaque personne qu'il connaît comme déjà là (en anglais tant qu'elle n'a pas choisi). Une langue que quelqu'un a choisie n'est pas dans ce fichier et n'est jamais perdue.

## <a id="right-to-left-text-looks-wrong"></a>Le texte de droite à gauche s'affiche mal

En persan et en arabe, aipager met des marques de direction invisibles dans ses propres textes pour que chaque ligne se lise de droite à gauche, et garde de gauche à droite les noms des sessions, les commandes et le code. Si une ligne a encore l'air mélangée, c'est le plus souvent un client Telegram qui dessine autrement le texte à directions mêlées : vérifiez dans un autre client (téléphone contre Desktop ou Web) avant de le signaler. Les réponses de Claude lui-même s'affichent comme Claude les a écrites, sans marque ajoutée.

## <a id="a-translated-keyboard-word-does-nothing-in-another-chat"></a>Un mot traduit du clavier ne fait rien dans une autre discussion

Le clavier de chaque discussion est dans la langue de cette discussion. Les mots du clavier principal (status, stop, new, kill et les autres) sont compris dans toutes les discussions, dans toutes les langues, et les mots anglais marchent toujours. Le mot d'un bouton de modèle ou de commande est différent : tapé dans une discussion réglée sur une autre langue, c'est un message pour Claude, pas un bouton, donc un « Clear » russe ne lance jamais `/clear` dans une discussion persane ou anglaise. Utilisez le bouton lui-même, ou la commande anglaise (`/clear`). Si un clavier a l'air ancien après un changement de langue de la discussion, envoyez `/start` pour avoir le clavier actuel. Un mot qui est aussi le nom d'une de vos sessions parle à cette session. `/mode` lit toujours `ask`, `auto` et `full` comme le mode, et un mot de mode traduit comme une session seulement quand une session en cours a exactement ce nom.

## <a id="still-stuck"></a>Le problème persiste ?

Envoyez un rapport de problème : touchez **Signaler un problème** sous `/help` dans votre discussion privée avec le bot, ou lancez `aipager report` dans un terminal. Vous voyez tout le rapport avant le moindre envoi, et il ne contient ni discussions, ni messages, ni noms, ni chemins, ni tokens (voir [Rapports de problèmes](problem-reports.md)). Après l'envoi, vous recevez une référence comme `ap1-7c41d09e2b3a`.

Ensuite, si vous voulez en parler, ouvrez une issue sur [github.com/dev-aly3n/aipager/issues](https://github.com/dev-aly3n/aipager/issues) et indiquez cette référence. Merci de ne pas coller la sortie d'`aipager doctor` ni d'`aipager logs` dans une issue : elles contiennent l'identifiant de votre discussion, les chemins de votre dossier personnel, les noms de vos sessions et le début de vos messages. Si une ligne du journal est nécessaire, la personne qui maintient aipager demandera cette seule ligne, et vous pourrez d'abord en retirer tout ce qui est privé.

## <a id="see-also"></a>Voir aussi

- [Architecture](architecture.md) : ce que fait chaque partie.
- [Modèle de sécurité](security.md) : ce qu'aipager touche et ne touche pas.
