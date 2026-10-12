# Événements de hook

<!-- i18n-source: docs/hooks.md @ 7228205 -->

> Cette page est une traduction de la page anglaise ; si les deux ne disent pas la même chose, c'est la version anglaise qui fait foi. ([English](../../hooks.md))

Claude Code lance des commandes de hook à des moments précis d'une tâche : un message est envoyé, un outil va s'exécuter, un outil a fini, la tâche est terminée, et ainsi de suite. aipager s'accroche à chacun de ces moments et en fait de l'activité dans Telegram : la carte d'activité, les demandes d'autorisation, les réponses, les notifications et le journal d'audit.

## <a id="how-it-is-wired"></a>Comment c'est branché

`aipager config` (ou `aipager setup`) ajoute les entrées d'aipager dans `~/.claude/settings.json`, et aipager les vérifie à chaque démarrage :

- une commande `aipager-hook` pour chacun des 16 événements ci-dessous, avec le filtre `*` sur les événements d'outil (`PreToolUse`, `PostToolUse`, `PermissionRequest`) ;
- un délai de 5 secondes sur `PreModelSwitch` et, depuis `aipager config` et `aipager setup`, un délai de 30 secondes sur `PermissionRequest` (le hook lui-même attend au plus 20 secondes, voir plus bas) ; les autres entrées utilisent la valeur par défaut de Claude Code ;
- la commande `statusLine`, `aipager-statusline`.

`aipager uninstall` retire de nouveau ces entrées (voir [commandes](commands.md#command-line-aipager-uninstall)).

Claude Code lance un nouveau processus `aipager-hook` (`aipager.dtach.notify_hook:main`) pour chaque événement. Il lit le JSON de l'événement sur stdin, ajoute deux champs (ci-dessous) et l'envoie à aipager en un seul datagramme sur un socket Unix. Le socket est, dans cet ordre : `$AIPAGER_INSTANCE_DIR/aipager.sock` pour une instance de test isolée, `$AIPAGER_SOCKET_PATH`, `$XDG_RUNTIME_DIR/aipager.sock`, ou `/tmp/aipager.sock`. L'envoi n'attend jamais : si aucun aipager n'écoute, l'événement est perdu, et le moniteur des sessions rattrape le retard grâce aux fichiers de la session.

La plupart des événements coûtent à Claude Code environ 20 à 25 ms de démarrage de processus. Quelques-uns chargent aussi le code des règles d'aipager : avant un appel d'outil (la vérification de sécurité, voir [PreToolUse](#pretooluse)) environ 70 ms, à la fin d'une tâche (pour effacer la marque de tâche ouverte) environ 55 ms, et `UserPromptSubmit` (qui écrit les règles de la tâche). Un `PermissionRequest` attend exprès votre appui, jusqu'à 20 secondes.

Le hook limite sa propre mémoire à 1 Go. Un hook qui atteint la limite se termine avec une erreur (pour `PreToolUse`, le code de sortie que Claude Code lit comme un refus) et envoie à aipager un datagramme `hook_memory_cap_hit`, qui devient une notification dans la discussion, avec un bouton « 🐞 Signaler ceci » pour le propriétaire de l'installation (voir [rapports de problèmes](problem-reports.md)). Mettez `AIPAGER_DEBUG=1` dans l'environnement de la session pour que le hook et la ligne d'état affichent leurs diagnostics sur stderr.

## <a id="what-the-daemon-receives"></a>Ce qu'aipager reçoit

Le datagramme est le JSON de hook de Claude Code lui-même, avec les champs d'aipager en plus (quelques événements en portent d'autres, nommés sous chaque événement plus bas) :

| Champ | Sens |
|---|---|
| `hook_event_name` | L'événement (champ de Claude Code ; aipager répartit le travail selon lui). |
| `session_id`, `transcript_path`, `cwd`, ... | Les champs de Claude Code lui-même, tels quels. `session_id` est l'identifiant de conversation de Claude Code. |
| `session` | Ajouté par aipager : son nom pour la session, `claude-<name>`, tiré de la variable `CLAUDE_DTACH_SESSION` qu'aipager définit quand il lance une session. Les événements d'une session Claude Code qu'aipager n'a pas lancée n'ont pas de nom et aipager les ignore. |
| `sl_tokens` | Ajouté par aipager, sur chaque événement sauf `MessageDisplay`, quand le fichier de ligne d'état de la session existe : `context_pct`, `total_output`, `total_input`, `current_output`, `lines_added` et `lines_removed`. |

Les événements d'outil portent aussi `tool_name` et `tool_input`. Chaque événement déclenché à l'intérieur d'un sous-agent porte `agent_id` (et les événements de sous-agent, `agent_type`) ; ceux de la boucle principale n'en ont pas, et aipager s'en sert pour mettre les appels d'outil d'un agent sur la ligne de cet agent.

## <a id="event-reference"></a>Référence des événements

Les gestionnaires d'aipager sont dans `aipager/dtach/hook_receiver.py` ; le travail propre du hook est dans `aipager/dtach/notify_hook.py`.

### <a id="sessionstart"></a>`SessionStart`

Une session a démarré, a repris ou a été compactée. Une session qu'aipager ne suit pas encore est enregistrée. Après un compactage, la carte affiche `📦 name · Conversation compactée : 82% → 4%` (voir [PreCompact](#precompact--postcompact)). Pour une nouvelle conversation, le hook efface aussi la marque de tâche ouverte de la tâche précédente.

### <a id="sessionend"></a>`SessionEnd`

La session est terminée. Elle est marquée GONE : elle quitte la barre d'état épinglée et passe dans les sessions terminées, d'où `/resume` peut la ramener. `aipager session <name>` ou `/new <name>` en lance une nouvelle.

### <a id="userpromptsubmit"></a>`UserPromptSubmit`

Claude a pris un message, tapé dans le terminal ou envoyé depuis Telegram.

Le hook rapproche d'abord le message des notes d'autorisation de la session (`/tmp/claude-notes-<session>/`, une par message Telegram qu'aipager a tapé) et écrit les règles de la tâche dans `/tmp/claude-policy-<session>.json`, que lit la vérification [PreToolUse](#pretooluse) (voir [sécurité → comment les règles s'appliquent en équipe](security.md#team-mode-enforcement)). Il dit à aipager quels messages Claude a pris (un datagramme `queue_pickup`) avant de transmettre l'événement. Il affiche ensuite du contexte en plus pour Claude quand il y en a : le style de réponse choisi dans `/settings` et, pour une réponse à un message plus ancien, ce que disait ce message et quand. Un message `<task-notification>` (Claude qui se réveille tout seul quand un agent ou une commande en arrière-plan finit) est la suite du même travail : il ne correspond à aucune note et garde les règles de la tâche.

aipager marque la session au travail, ouvre la carte d'activité s'il n'y en a pas (un message envoyé depuis Telegram en a déjà une), et change le 👀 des messages que Claude a pris en 👍. Un message envoyé pendant qu'une tâche tourne reçoit son 👍 quand Claude l'intègre à cette tâche ou commence la tâche suivante avec lui.

### <a id="pretooluse"></a>`PreToolUse`

Un outil va s'exécuter. C'est là que tourne la vérification de sécurité d'aipager, dans le hook, avant que Claude Code regarde ses propres règles d'autorisation :

- Une tâche lancée depuis Telegram est tenue par les règles du rôle de la personne qui l'a envoyée et par le socle de sécurité (chemins protégés, motifs de commandes), dont seul le rôle `owner` est exempté ; une tâche qu'aipager ne peut attribuer à personne reçoit le socle ; un message que vous avez tapé dans le terminal n'est pas limité. Les règles sont dans [groupes](groups.md#how-rules-work) et [sécurité](security.md#team-mode-enforcement).
- Quand une règle refuse l'appel, le hook affiche une décision `deny`, Claude Code refuse l'outil sans demander, et aipager est prévenu (`safety_blocked`) : la tâche s'arrête avec `🛑 x1 · Bloqué par une règle de sécurité : ... (arrêtée)`, et chaque appel d'outil suivant de cette tâche est refusé aussi.
- Une vérification qui échoue refuse, sauf si les règles de la session ont pu être lues et donnent l'exemption du propriétaire.

Côté aipager, l'appel devient une ligne sur la carte d'activité (l'appel d'un sous-agent va sur la ligne de cet agent), et un `Write` ou un `Edit` affiche un aperçu du diff quand `/settings` → Aperçus des diffs est activé (désactivé par défaut). Un appel `AskUserQuestion` devient la question avec un bouton par option ; une question posée par un agent d'aide de Claude Code alors qu'aucune tâche ne tourne n'est pas affichée (ce n'est pas Claude qui vous demande quelque chose). L'appel `Agent` de Claude lui-même laisse son `subagent_type` et sa `description` pour l'agent qu'il lance, et l'appel d'un sous-agent est la dernière activité de cet agent : les deux s'affichent dans l'état sous une réponse pendant que l'agent tourne en arrière-plan.

### <a id="permissionrequest"></a>`PermissionRequest`

Claude Code va demander une autorisation. La carte d'activité devient la demande, avec la vraie commande ou le vrai chemin sous la description qu'en donne Claude (voir [commandes → demandes d'autorisation](commands.md#permission-prompts)) : Autoriser, Refuser, Arrêter, et Toujours autoriser seulement quand Claude Code propose une vraie règle à retenir.

Le hook répond lui-même à la demande quand il le peut. Il ouvre un socket de réponse de courte durée, envoie son adresse avec l'événement, et attend votre appui jusqu'à 20 secondes. Un appui à temps revient comme une décision `PermissionRequest` (autoriser, autoriser avec la règle, ou refuser), donc Claude Code n'affiche jamais sa propre boîte de dialogue. Si aucun appui n'arrive à temps, le hook dit à aipager que l'attente est finie (`permission_reply_timeout`) et n'affiche rien ; il n'affiche rien non plus quand aipager est injoignable ou que quelque chose échoue sur ce chemin. Claude Code affiche alors sa propre boîte de dialogue dans le terminal, et un appui plus tard y répond en tapant les touches. Le journal d'audit (`~/.claude/aipager-audit.jsonl`) note par quelle voie chaque appui a répondu, `"via": "hook_decision"` ou `"via": "keystroke_fallback"`.

### <a id="posttooluse--posttoolusefailure"></a>`PostToolUse` / `PostToolUseFailure`

Un outil a fini, ou a échoué. Sa ligne sur la carte d'activité se fige (un échec est marqué sur la ligne), et une réponse à une demande ouverte est remarquée : un vieux Autoriser ou Refuser pour elle ne tape plus rien ensuite. La session garde les 200 lignes d'outil les plus récentes.

Un appel `Bash` que Claude Code a mis en arrière-plan indique un `backgroundTaskId`. aipager le suit alors comme une commande en arrière-plan (seulement celles de la boucle principale : un appel qui porte `agent_id` appartient à ce sous-agent) jusqu'à ce que sa fin soit vue : un `<task-notification>` avec un `<status>` (dans le message qui réveille Claude, ou dans l'historique de la conversation quand il arrive en pleine tâche), un `TaskStop` ou un `KillShell` de son identifiant, la fin de la session, ou deux heures (`AIPAGER_BG_SHELL_MAX_TRACK`, en secondes, par défaut 7200). Voir [commandes](commands.md#idle-responses) pour sa ligne sur la carte.

### <a id="notification"></a>`Notification`

aipager lit le `notification_type` de Claude Code :

- `permission_prompt` : un signal plus lent et plus ancien d'une demande d'autorisation. Il n'affiche la demande que si `PermissionRequest` ne l'a pas déjà fait.
- `idle_prompt` pendant qu'une demande d'autorisation ou une question attend : la session reste en attente, et la discussion reçoit `⬆️ jim · attend toujours votre réponse plus haut`, une fois par attente.
- `idle_prompt` sinon : traité comme `Stop` (ci-dessous). Il rattrape une tâche dont le `Stop` s'est perdu ; une réponse déjà livrée n'est jamais renvoyée.

### <a id="stop"></a>`Stop`

La tâche est terminée. La session devient prête, la carte d'activité se fige comme le dit votre affichage, et la réponse de Claude est envoyée (prise dans le `last_assistant_message` de l'événement, sinon dans l'historique de la conversation). Les messages retenus en attendant la fin de la tâche partent ensuite. Les agents et les commandes en arrière-plan qui tournent encore gardent le travail ouvert : la carte reste comme état du travail et la réponse se termine par un état en direct de ce travail : ce que fait chaque agent et combien de commandes en arrière-plan du travail tournent (voir [commandes → agents qui travaillent encore](commands.md#agents-still-running-when-the-answer-goes-out)). Le hook efface d'abord la marque de tâche ouverte, sauf si Claude garde encore un message en attente qui devient tout de suite la tâche suivante.

### <a id="stopfailure"></a>`StopFailure`

La tâche s'est terminée sur une erreur de l'API. La session devient prête et la réponse, s'il y en a une, est livrée comme pour `Stop` ; une erreur connue (serveurs surchargés, limite de débit, ...) est affichée avec des mots simples.

### <a id="subagentstart--subagentstop"></a>`SubagentStart` / `SubagentStop`

Un sous-agent a démarré ou s'est terminé. Chacun a sa propre ligne sur la carte d'activité, `🤖 <type> · <activity> · <elapsed>`, avec ses propres appels d'outil repliés en dessous, et se fige en `✅ 🤖 <type> · N outils utilisés · <elapsed>` quand il s'arrête. Un sous-agent dont le `SubagentStop` n'arrive jamais est retiré une fois qu'il est resté silencieux 30 minutes (`AIPAGER_SUBAGENT_SILENCE`, en secondes ; aucun événement d'aucune sorte qui porte son identifiant). Un agent au travail envoie des événements d'outil toutes les quelques secondes, donc cela ne ramasse qu'un agent dont l'arrêt a été manqué (un redémarrage, un datagramme perdu) ; un seul appel d'outil qui dure 30 minutes sans aucun événement sous l'identifiant de cet agent le ramasse aussi. `AIPAGER_SUBAGENT_TTL` est accepté comme ancien nom du même réglage. Des événements `SubagentStop` sans type et avec un identifiant inconnu arrivent souvent et sont ignorés.

### <a id="precompact--postcompact"></a>`PreCompact` / `PostCompact`

Claude compacte son contexte (`trigger` vaut `auto` ou `manual`). La carte d'activité affiche `🔄 name · Compactage de la conversation` (un nouveau message quand il n'y a pas de carte), pour que la pause ne ressemble pas à un plantage. `PostCompact` met fin à cet état ; la ligne `📦 name · Conversation compactée : X% → Y%` vient du `SessionStart` qui suit, ou de la ligne d'état quand celui-ci est manqué. La ligne d'état donne aussi l'avertissement `⚠️ name · Contexte à 82% - il sera bientôt compacté automatiquement` (avec un bouton Compacter) une fois que le contexte atteint 80 %, une seule fois par conversation : le compactage ne le répète pas, et une nouvelle conversation (après `/clear`, par exemple) le reçoit à nouveau.

### <a id="messagedisplay"></a>`MessageDisplay`

Le texte de Claude lui-même au moment où il arrive à l'écran. L'historique de la conversation n'est écrit qu'après chaque série d'appels d'outil, donc c'est la seule source à jour de ce que dit Claude, et c'est ce qui fait défiler ses commentaires dans la carte d'activité. Le texte d'un sous-agent (tout événement qui porte son `agent_id`) n'est jamais pris pour celui de Claude : il n'arrive jamais dans les commentaires de la carte d'activité, ni dans la Mini App, ni dans le journal de la tâche, et devient la dernière activité de cet agent dans l'état sous une réponse. Le hook n'ajoute pas de données de ligne d'état à cet événement : Claude Code l'attend pendant qu'il dessine, et il se déclenche plusieurs fois par message.

### <a id="premodelswitch"></a>`PreModelSwitch`

Claude Code 2.1.251 et les versions suivantes le déclenchent avant de changer de modèle. Le contenu porte `from_model`, `to_model`, `requested_model` et `source` (`command` pour un `/model` tapé, ou `picker` ou `sdk`). Claude Code demande en général "Switch model?" avant un changement, parce que la réponse suivante doit relire toute la conversation. Un hook qui répond `permissionDecision: "allow"` laisse le changement se faire sans cette question.

`aipager-hook` ne répond `allow` que pour un changement qu'aipager a tapé lui-même, depuis le contrôle Modèle de la Mini App ou le clavier des modèles. Tout autre changement ne reçoit pas de réponse, donc Claude Code demande comme toujours. Cela couvre le `/model` que tape l'opérateur dans le terminal, le sélecteur, et un changement par le SDK.

aipager reconnaît ses propres changements grâce à un fichier marqueur. Juste avant de taper `/model <name>`, aipager écrit `aipager-modelswitch-<session>.json` :
- il va dans le dossier qui contient le socket de contrôle (normalement `$XDG_RUNTIME_DIR`) ;
- il est écrit de façon atomique, avec les droits 0600 ;
- il nomme le modèle et l'identifiant de la session Claude ;
- il expire au bout de 30 secondes.

Le hook ne répond `allow` que si tout ceci est vrai :
- le marqueur est un fichier qui vous appartient, et pas un lien symbolique ;
- il n'a pas expiré ;
- il est pour la même session aipager et le même identifiant de session Claude ;
- son modèle est égal à `requested_model` ou à `to_model`, sans tenir compte des majuscules ;
- `source` vaut `command`.

Le hook prend alors le marqueur avec un renommage atomique, pour qu'il ne serve qu'une seule fois. La vérification lit un fichier local, sans socket et sans attente. L'événement n'est pas transmis à aipager.

Claude Code 2.1.101 et les versions suivantes ignorent les événements de hook qu'ils ne connaissent pas, donc les versions plus anciennes sans `PreModelSwitch` ne sont pas touchées. Un cas peut encore afficher la question : un changement vers ou depuis `opusplan` peut faire demander deux fois au hook par Claude Code, et seule la première demande trouve le marqueur, donc Claude Code demande "Switch model?" dans le terminal.

## <a id="the-status-line"></a>La ligne d'état

Ce n'est pas un événement de hook : Claude Code lance la commande `statusLine`, `aipager-statusline` (`aipager.dtach.statusline_notify:main`), chaque fois qu'il redessine sa ligne d'état. La commande :

- écrit le JSON d'état de Claude Code dans `/tmp/claude-status-<session>.json` (lu par le hook pour `sl_tokens`, par `aipager status` et par aipager) ;
- envoie à aipager un datagramme `statusline` avec le modèle, le % de contexte, le coût, les nombres de tokens et les lignes ajoutées et retirées, qui alimentent la carte d'activité, `/status`, la Mini App, la vérification qu'un changement de modèle a bien eu lieu, et l'avertissement de contexte ;
- affiche la ligne d'état du terminal : `[name] Opus 5.5 | 42% ctx | $1.23`.

## <a id="aipagers-own-datagrams"></a>Les datagrammes d'aipager lui-même

En plus des événements de hook, les programmes d'aide envoient à aipager quelques messages à eux sur le même socket :

| Message | Envoyé par | Quand |
|---|---|---|
| `queue_pickup` | hook | À `UserPromptSubmit` : quels messages Telegram Claude a pris. |
| `safety_blocked` | hook | À `PreToolUse`, quand la vérification de sécurité a refusé l'appel. |
| `permission_reply_timeout` | hook | Une attente de `PermissionRequest` s'est terminée sans appui. |
| `statusline` | ligne d'état | À chaque redessin de la ligne d'état. |
| `hook_memory_cap_hit` | hook, ligne d'état | Le processus a atteint sa limite de mémoire. |
| `report_error` | hook, commandes `aipager` | Il a rencontré une erreur : seulement le type de l'erreur et son endroit dans le code d'aipager, jamais le contenu de l'événement, pour les [rapports de problèmes](problem-reports.md). |

## <a id="the-permission-flow"></a>Le parcours d'une demande d'autorisation

```
claude              aipager-hook             aipager daemon          Telegram
  |                      |                         |                     |
  | PermissionRequest    |                         |                     |
  |--------------------->| opens a reply socket    |                     |
  |                      |------------------------>| busy card becomes   |
  |                      |                         | the prompt          |
  |                      |                         |-------------------->|
  |                      |                         |   you tap Allow     |
  |                      |                         |<--------------------|
  |                      |   allow (reply socket)  | audit log           |
  |                      |<------------------------|                     |
  |  decision: allow     |                         |                     |
  |<---------------------|                         |                     |
  | tool runs            |                         |                     |
```

Sans appui dans les 20 secondes, le hook n'affiche rien et Claude Code affiche sa propre boîte de dialogue ; un appui plus tard tape la réponse dans cette boîte de dialogue par le terminal de la session (`dtach -p`). Dans les deux cas, l'appui est noté dans le journal d'audit et publié sous la carte (`✅ jim · Autorisé · @alice · Bash: ls -la /tmp`).

## <a id="see-also"></a>Voir aussi

- [Architecture](architecture.md) : où se place le récepteur des hooks.
- [Commandes du bot → demandes d'autorisation](commands.md#permission-prompts) : le côté que vous voyez.
- [Modèle de sécurité](security.md) : ce que la vérification de sécurité couvre et ne couvre pas.
