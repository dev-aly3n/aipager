# Bots observateurs

<!-- i18n-source: docs/observers.md @ 6fc084f -->

> Cette page est une traduction de la page anglaise ; si les deux ne disent pas la même chose, c'est la version anglaise qui fait foi. ([English](../../observers.md))

Des bots Telegram en lecture seule qui reçoivent une copie des notifications du bot principal. Ils reçoivent les résumés, les avertissements et les erreurs, mais ne peuvent pas contrôler les sessions.

## <a id="setup"></a>Configuration

1. Créez un bot avec [@BotFather](https://t.me/BotFather)
2. Ouvrez une discussion avec le bot et envoyez `/start`
3. Trouvez l'identifiant de votre discussion (envoyez un message, puis regardez `https://api.telegram.org/bot<TOKEN>/getUpdates`)
4. Définissez `OBSERVER_BOTS` dans l'environnement d'aipager. Pour un aipager au premier plan, exportez-la avant `aipager start` ; pour le service systemd :

```sh
systemctl --user edit aipager    # ajoutez sous [Service] :
# Environment=OBSERVER_BOTS=<bot_token>:<chat_id>
```

Plusieurs observateurs (séparés par des virgules) :

```
OBSERVER_BOTS=111:AAA_first:12345,222:BBB_second:67890
```

Le format est `token:chat_id` : la lecture utilise le **dernier** deux-points comme séparateur (les tokens de bot contiennent eux-mêmes un deux-points).

5. Redémarrez aipager

## <a id="what-observers-receive"></a>Ce que reçoivent les observateurs

Les observateurs reçoivent ces notifications depuis **toutes les discussions que sert le bot**, chaque discussion privée et chaque groupe, sans filtre : n'en configurez un que pour une discussion à qui vous confieriez tout cela. Chaque notification est dans la langue de la discussion d'où elle vient.

| Événement | Exemple |
|-------|---------|
| Tâche terminée | `💬 x1 · Terminé (2:05, +12 -3)` : seulement la ligne de résultat, pas la réponse ; plus le journal complet en fichier `.md` quand la réponse ou la carte a été coupée |
| Un travail en arrière-plan est terminé | `✅ x1 · Terminé (...)`, ou `⚠️ x1 · Terminé (...)` quand un agent a disparu ou que la fin d'une commande en arrière-plan n'a jamais été vue |
| Erreur de l'API | `⚠️ x1 · Les serveurs d'Anthropic sont surchargés. Réessayez dans un instant.` (sans bouton Réessayer) |
| Avertissement de contexte | `⚠️ x1 · Contexte à 82% - il sera bientôt compacté automatiquement` |
| Compactage en cours | `🔄 x1 · Compactage de la conversation` |
| Compactage terminé | `📦 x1 · Conversation compactée : 82% → 4%` |
| Une demande qui attend toujours | `⬆️ x1 · attend toujours votre réponse plus haut`, avec l'autorisation ou la question qu'elle attend |
| Une session au travail qui ne donne plus de nouvelles | `⏳ x1 · travaille encore (aucune nouvelle depuis 10 min)` |
| Un message que Claude Code n'a pas pris | `⚠️ x1 · Claude Code n'a pas reçu le message` |
| Session terminée | `🔴 x1 · Session terminée` |

## <a id="what-observers-dont-receive"></a>Ce que les observateurs NE reçoivent PAS

- Les animations d'activité
- Les mises à jour des appels d'outil
- Les demandes d'autorisation et leurs boutons (seul le rappel « attend toujours » ci-dessus dit ce que demande une demande)
- Les questions AskUserQuestion
- Aucun clavier ni bouton

Les observateurs n'ont aucun état : les envois partent sans attendre de retour. Un observateur en panne ne touche jamais le bot principal.
