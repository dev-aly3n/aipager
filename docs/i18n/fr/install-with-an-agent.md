# Installer avec un agent

<!-- i18n-source: docs/install-with-an-agent.md @ fc5d6810 -->

> Cette page est une traduction de la page anglaise ; si les deux ne disent pas la même chose, c'est la version anglaise qui fait foi. ([English](../../install-with-an-agent.md))

Cette page s'adresse à un agent de programmation IA (Claude Code, ou n'importe quel agent qui a un shell) à qui l'on demande d'installer et de configurer aipager pour la personne qu'il aide. Suivez-la étape par étape. Toutes les commandes d'ici s'exécutent sans poser de questions et n'ont jamais besoin d'un terminal interactif.

aipager permet à cette personne de piloter des sessions Claude Code depuis Telegram. Quand vous aurez fini, la personne aura un bot Telegram qui ne parle qu'à elle, et aipager tournera en arrière-plan sur cet ordinateur.

## <a id="1-what-only-the-person-can-do"></a>1. Ce que seule la personne peut faire

Demandez à la personne de faire ceci. Vous ne pouvez pas le faire à sa place.

1. **Créer le bot.** Dans Telegram, ouvrir [@BotFather](https://t.me/BotFather), envoyer `/newbot`, choisir un nom et un nom d'utilisateur. BotFather répond avec le token du bot (il ressemble à `123456789:AA...`).
2. **Appuyer sur Démarrer dans le bot.** Ouvrir le nouveau bot (BotFather donne le lien `t.me/<bot username>`) et appuyer sur Démarrer (« Start » si Telegram est en anglais). Un bot ne peut pas écrire à quelqu'un qui ne l'a pas fait.
3. **Se connecter à Claude Code sur cet ordinateur.** Si la personne utilise déjà Claude Code ici, il n'y a rien à faire. Sur un serveur sans écran, elle lance `claude` une fois dans son propre terminal et se connecte (il affiche un lien à ouvrir sur un autre appareil). Si elle préfère un token de longue durée, elle lance `claude setup-token`, qui ne fait qu'afficher le token : elle l'enregistre elle-même pour les sessions d'aipager, dans son propre terminal (coller le token, appuyer sur Entrée, puis sur Ctrl-D) :

   ```sh
   mkdir -p ~/.config/aipager
   (umask 077 && { printf 'CLAUDE_CODE_OAUTH_TOKEN='; cat; } >> ~/.config/aipager/daemon.env)
   ```

   aipager donne ce token à chaque session qu'il lance (voir [sécurité](security.md#the-claude-credential-what-actually-protects-it)). Vous ne le lisez jamais.

## <a id="2-how-the-person-gives-you-the-token"></a>2. Comment la personne vous donne le token

Quiconque a le token du bot contrôle le bot. Proposez ces deux façons à la personne et laissez-la choisir.

- **La plus sûre : un fichier que vous ne lisez jamais.** Dans son propre terminal, elle lance :

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token)
  chmod 600 ~/.config/aipager/bot-token
  ```

  puis colle le token, appuie sur Entrée, et appuie sur Ctrl-D. Elle seule peut lire le fichier (droits 600). Elle peut le supprimer une fois la configuration réussie (aipager garde sa propre copie dans sa configuration). Elle vous donne le chemin (`~/.config/aipager/bot-token`). Le token n'entre jamais dans votre conversation, et vous passez le chemin à `--token-file`.
- **La plus rapide : le coller dans la conversation.** Cela marche, mais dites clairement ce que cela implique : le token reste dans votre historique de conversation et est envoyé au fournisseur de votre modèle. Si cet historique peut un jour être partagé, la personne devra ensuite envoyer `/revoke` à @BotFather et enregistrer le nouveau token avec `aipager setup --force` (étape 3.2, avec `--force`). Si elle choisit cette voie, enregistrez le token dans le même fichier sans l'afficher : de préférence avec votre propre outil d'écriture de fichiers, puis `chmod 600 ~/.config/aipager/bot-token`. Si vous n'avez qu'un shell, passez-le sur stdin avec un here-document, pour qu'il ne soit pas un argument de la commande qui l'écrit :

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token) <<'EOF'
  <the token>
  EOF
  chmod 600 ~/.config/aipager/bot-token
  ```

  Puis utilisez `--token-file` comme ci-dessous.

L'identifiant d'utilisateur Telegram de la personne (l'identifiant de sa discussion, un nombre comme `123456789`) n'est pas secret. Si elle ne le connaît pas, vous pouvez le trouver (étape 3.3).

## <a id="3-your-steps"></a>3. Vos étapes

### <a id="31-install-aipager"></a>3.1 Installer aipager

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

Il utilise uv ou pipx, celui qui est là (Homebrew aussi, sur macOS), et installe d'abord uv s'il n'y en a aucun. Vous pouvez aussi lancer vous-même `uv tool install aipager` ou `pipx install aipager`. Si `aipager` est ensuite introuvable, ajoutez `~/.local/bin` au `PATH` de votre shell (`export PATH="$HOME/.local/bin:$PATH"`).

Si aipager était déjà installé, ces commandes ne le mettent pas à jour. Vérifiez que `aipager setup --help` marche ; s'il échoue, l'aipager installé est trop ancien : lancez `aipager update` et vérifiez à nouveau. Si `aipager update` dit de redémarrer aipager, dites-le à la personne ; ne le redémarrez pas vous-même (voir 4).

Ne lancez pas `aipager config` : c'est l'assistant interactif, fait pour les personnes.

### <a id="32-set-aipager-up"></a>3.2 Configurer aipager

```sh
aipager setup --token-file ~/.config/aipager/bot-token --chat-id 123456789 --service --json
```

Utilisez le vrai identifiant de la personne et le chemin du fichier du token. Au lieu de `--token-file`, vous pouvez envoyer le token par un pipe avec `--token-stdin` (par exemple `--token-stdin < path`) ; le pipe doit se fermer, et setup l'attend au plus 30 secondes.

setup vérifie le token auprès de Telegram, vérifie cet ordinateur, envoie un message de test à la personne ("aipager is set up for you. ..."), et seulement ensuite écrit sa configuration, ajoute les hooks de Claude Code dans `~/.claude/settings.json` (ou `$CLAUDE_CONFIG_DIR/settings.json`) et, avec `--service`, installe et démarre le service en arrière-plan. Si une vérification échoue, rien n'est écrit.

Ajoutez `--dry-run` pour voir d'abord ce qui changerait. `--role admin` donne à la personne le rôle admin au lieu du rôle propriétaire (le choix par défaut, propriétaire, est le bon pour une personne qui configure son propre ordinateur).

### <a id="33-if-the-person-does-not-know-their-telegram-id"></a>3.3 Si la personne ne connaît pas son identifiant Telegram

```sh
aipager setup detect-chat --token-file ~/.config/aipager/bot-token --json
```

Demandez à la personne d'appuyer sur Démarrer dans le bot (ou de lui envoyer n'importe quel message) pendant que la commande attend (jusqu'à 5 minutes ; `--timeout SECONDS` change ce délai). Elle affiche l'expéditeur le plus récent dans `candidate` (`id`, `first_name`, `last_name`, `username`) et n'enregistre rien. Montrez le nom et le nom d'utilisateur à la personne et demandez-lui « C'est bien vous ? ». Seulement si elle dit oui, lancez l'étape 3.2 avec `--chat-id` égal à `candidate.id`. Si `other_candidates` n'est pas 0, d'autres personnes ont aussi écrit au bot : vérifiez encore mieux.

### <a id="34-read-the-result"></a>3.4 Lire le résultat

Avec `--json`, setup affiche exactement un objet JSON sur stdout. Lisez `exit_code` (le même que le code de sortie du processus), `status`, `error`, `message`, `fix` et `next_step`. Si stdout n'est pas du JSON (une option inconnue, un mauvais mot après `setup`, ou un aipager trop ancien pour avoir `setup`), le code de sortie est 2 et la raison est en texte simple sur stderr : lisez-la, et pour un aipager ancien voyez 3.1.

| Sortie | `error` | Ce que vous faites |
|---|---|---|
| 0 | | `installed`, `updated` ou `unchanged` : c'est fait, passez à 3.5. `dry_run` : rien n'a été écrit ; relancez la même commande sans `--dry-run`. detect-chat `found` : faites confirmer la personne comme en 3.3, puis lancez 3.2. |
| 1 | `config_malformed`, `ambiguous_install`, `settings_invalid`, `test_send_failed`, `write_failed`, `service_failed`, `internal_error` | Montrez `message` et `fix` à la personne. `ambiguous_install` : la personne le corrige dans `aipager config`. Ne modifiez pas de fichiers à la main. Un `settings_invalid` qui demande un `CLAUDE_CONFIG_DIR` absolu : le `fix` nomme le fichier à changer (`daemon.env` ou un fichier de réglages de Claude Code) ; demandez le dossier à la personne, changez cette seule valeur uniquement si elle le dit, puis relancez setup. |
| 2 | `usage`, `token_malformed`, `token_stdin_timeout`, `bad_chat_id`, ... | Votre entrée était fausse ; `fix` nomme l'option. `token_malformed` : le fichier ne contient pas de token de bot, demandez à la personne de le vérifier. |
| 3 | `deps_missing` | Suivez `fix` (chaque élément manquant a aussi son propre `fix` dans `deps.items`), puis relancez setup. |
| 4 | `token_rejected` | Telegram a refusé le token. Redemandez le token à la personne (ou un nouveau à @BotFather). |
| 5 | `chat_not_started`, `bot_blocked` | Demandez à la personne d'ouvrir le bot, d'appuyer sur Démarrer (ou de le débloquer), puis relancez setup. |
| 6 | `existing_install` | aipager est déjà configuré avec un autre token, une autre discussion ou un autre rôle. Dites à la personne ce qui diffère et demandez-lui avant d'ajouter `--force`. |
| 7 | `telegram_unreachable` | Problème de réseau ou Telegram est surchargé. Attendez une minute et réessayez. |
| 8 | `detect_timeout` | detect-chat n'a vu aucun message. Demandez à la personne d'appuyer à nouveau sur Démarrer et relancez. Avec l'avertissement `updates_backlog`, appuyer sur Démarrer n'aide pas : demandez à la personne son identifiant numérique. |
| 9 | `daemon_running` | detect-chat : un aipager pour ce bot tourne déjà et reçoit ses messages. Demandez à la personne son identifiant numérique (ou demandez-lui avant de l'arrêter avec `aipager service stop`). |
| 10 | `updates_conflict` | detect-chat : un autre programme lit les messages de ce bot. Demandez à la personne son identifiant numérique. |
| 130 | `interrupted` | Arrêté avec Ctrl-C. |

Si `daemon.restart_needed` vaut true, aipager doit redémarrer pour appliquer le changement, ou setup n'a pas pu savoir s'il tourne (l'avertissement `daemon_unknown`). Suivez `next_step`, qui dit lequel des deux (s'il propose `aipager start`, qui reste au premier plan, préférez `aipager service install`). Dites-le à la personne, et ne redémarrez (`aipager service stop`, puis `aipager service start`) que si elle le demande, et jamais depuis une session que la personne pilote à travers aipager (voir 4). La liste complète des erreurs, des avertissements et des clés JSON est dans [la référence de la commande setup](commands.md#command-line-aipager-setup-for-coding-agents).

### <a id="35-check-the-install"></a>3.5 Vérifier l'installation

```sh
aipager doctor --json
```

La commande ne fait que lire et n'envoie rien. Une sortie 0 veut dire qu'aucune vérification n'a échoué. Pour chaque vérification dont le `status` est `fail` (ou `warn`), montrez son `title`, son `detail` et son `fix` à la personne.

### <a id="36-report-back-to-the-person"></a>3.6 Rendre compte à la personne

Dites-lui, en quelques lignes :

- le bot (`@` + `bot_username`), et si le message de test a été envoyé (`test_message`) ;
- ce qui a été configuré (`changed`, et `service.result`) ;
- comment lancer une session Claude : envoyer `/new` au bot dans Telegram, ou lancer `aipager session <name>` dans un terminal ;
- tout ce que `doctor` a signalé, et `next_step` s'il demande quelque chose.

## <a id="4-what-you-must-not-do"></a>4. Ce que vous ne devez pas faire

- N'affichez pas le token, ne l'envoyez pas à `echo` ni à `cat`, et ne le mettez jamais sur une ligne de commande (`--token` est de toute façon refusé). Passez-le seulement dans un fichier ou sur stdin.
- Ne modifiez pas à la main `~/.config/aipager/` (à part le fichier du token de l'étape 2) ni `~/.claude/settings.json`. `aipager setup` et `aipager config` les écrivent.
- N'ajoutez pas `--force` sans demander à la personne : cela remplace son token, sa discussion ou son rôle existants.
- Ne redémarrez pas et ne terminez pas votre propre session Claude Code. Lors d'une première installation, la session dans laquelle vous tournez n'est pas sous aipager, et n'a pas besoin de l'être : les nouvelles sessions viennent de `/new` dans Telegram ou d'`aipager session <name>`. Si aipager est déjà configuré et que la personne vous parle à travers lui (par exemple pour lancer `setup --force` avec un nouveau token), n'arrêtez pas et ne redémarrez pas aipager vous-même : votre tâche et sa réponse seraient coupées. Dites à la personne de le redémarrer quand vous aurez fini.

## <a id="5-groups"></a>5. Groupes

`aipager setup` configure la discussion privée de la personne avec le bot. Pour ajouter un groupe Telegram avec d'autres personnes et leurs rôles, la personne lance `aipager config` (la configuration des groupes ne peut pas encore se faire par script). Voir [Usage en équipe et groupes](groups.md).

## <a id="see-also"></a>Voir aussi

- [Commandes du bot et la commande setup](commands.md)
- [Dépannage](troubleshooting.md)
- [Modèle de sécurité](security.md)
