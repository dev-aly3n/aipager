# Instalar con un agente

<!-- i18n-source: docs/install-with-an-agent.md @ d63d62ef -->

> Esta página es una traducción de la página en inglés; si no coinciden, la versión en inglés es la correcta. ([English](../../install-with-an-agent.md))

Esta página es para un agente de programación con IA (Claude Code, o cualquier agente con shell) al que le piden instalar y configurar aipager para su persona. Síguela paso a paso. Todos los comandos de aquí se ejecutan sin preguntas y nunca necesitan una terminal interactiva.

aipager permite que esa persona maneje sesiones de Claude Code desde Telegram. Cuando termines, la persona tendrá un bot de Telegram que solo habla con ella, y aipager funcionando en segundo plano en esta computadora.

## <a id="1-what-only-the-person-can-do"></a>1. Lo que solo puede hacer la persona

Pídele a la persona que haga esto. Tú no puedes hacerlo por ella.

1. **Crear el bot.** En Telegram, que abra [@BotFather](https://t.me/BotFather), envíe `/newbot` y elija un nombre y un nombre de usuario. BotFather responde con el token del bot (parece `123456789:AA...`).
2. **Pulsar Start en el bot.** Que abra el bot nuevo (BotFather da el enlace `t.me/<bot username>`) y pulse Start. Un bot no puede escribirle a alguien que no hizo esto.
3. **Autenticarse en Claude Code en esta computadora.** Si ya usa Claude Code aquí, no hace falta nada. En un servidor sin pantalla, la persona ejecuta `claude` una vez en su propia terminal y se autentica (muestra un enlace para abrir en otro dispositivo). Si prefiere un token de larga duración, la persona ejecuta `claude setup-token`, que solo imprime el token: lo guarda ella misma para las sesiones de aipager, en su propia terminal (pega el token, pulsa Enter y luego Ctrl-D):

   ```sh
   mkdir -p ~/.config/aipager
   (umask 077 && { printf 'CLAUDE_CODE_OAUTH_TOKEN='; cat; } >> ~/.config/aipager/daemon.env)
   ```

   aipager le pasa ese token a cada sesión que arranca (mira [Modelo de seguridad](security.md#the-claude-credential-what-actually-protects-it)). Tú nunca lo lees.

## <a id="2-how-the-person-gives-you-the-token"></a>2. Cómo te da la persona el token

Quien tiene el token del bot controla el bot. Ofrécele a la persona estas dos opciones y deja que elija.

- **La más segura: un archivo que tú nunca lees.** En su propia terminal, la persona ejecuta:

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token)
  chmod 600 ~/.config/aipager/bot-token
  ```

  luego pega el token, pulsa Enter y pulsa Ctrl-D. Solo ella puede leer el archivo (permisos 600). Puede borrarlo cuando la configuración salga bien (aipager guarda su propia copia en su configuración). Te dice la ruta (`~/.config/aipager/bot-token`). El token nunca entra en tu chat, y tú pasas la ruta a `--token-file`.
- **La más rápida: pegarlo en el chat.** Funciona, pero di claramente lo que significa: el token queda en tu conversación y se envía a tu proveedor de modelos. Si alguna vez esa conversación se puede compartir, la persona debería luego enviar `/revoke` a @BotFather y poner el token nuevo con `aipager setup --force` (paso 3.2, con `--force`). Si elige esto, guarda el token en el mismo archivo sin imprimirlo: mejor con tu propia herramienta de escribir archivos y luego `chmod 600 ~/.config/aipager/bot-token`. Si solo tienes un shell, pásalo por stdin con un here-document, para que no sea un argumento del comando que lo escribe:

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token) <<'EOF'
  <the token>
  EOF
  chmod 600 ~/.config/aipager/bot-token
  ```

  Luego usa `--token-file` como abajo.

El ID de usuario de Telegram de la persona (el ID de su chat, un número como `123456789`) no es secreto. Si no lo sabe, tú puedes encontrarlo (paso 3.3).

## <a id="3-your-steps"></a>3. Tus pasos

### <a id="31-install-aipager"></a>3.1 Instalar aipager

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

Usa uv o pipx, el que haya (en macOS también Homebrew), e instala uv primero si no hay ninguno. También puedes ejecutar tú `uv tool install aipager` o `pipx install aipager`. Si después no se encuentra `aipager`, añade `~/.local/bin` al `PATH` de tu shell (`export PATH="$HOME/.local/bin:$PATH"`).

Si aipager ya estaba instalado, esto no lo actualiza. Comprueba que `aipager setup --help` funciona; si falla, el aipager instalado es demasiado viejo: ejecuta `aipager update` y vuelve a comprobarlo. Si `aipager update` dice que hay que reiniciar aipager, díselo a la persona; no lo reinicies tú (mira el punto 4).

No ejecutes `aipager config`: es el asistente interactivo para personas.

### <a id="32-set-aipager-up"></a>3.2 Configurar aipager

```sh
aipager setup --token-file ~/.config/aipager/bot-token --chat-id 123456789 --service --json
```

Usa el ID real de la persona y la ruta del archivo del token. En lugar de `--token-file`, puedes pasar el token por una tubería con `--token-stdin` (por ejemplo `--token-stdin < path`); la tubería tiene que cerrarse, y setup la espera como mucho 30 segundos.

setup comprueba el token con Telegram, comprueba esta computadora, envía a la persona un mensaje de prueba ("aipager is set up for you. ...") y solo entonces escribe su configuración, añade los hooks de Claude Code a `~/.claude/settings.json` (o `$CLAUDE_CONFIG_DIR/settings.json`) y, con `--service`, instala y arranca el servicio en segundo plano. Si alguna comprobación falla, no se escribe nada.

Añade `--dry-run` para ver primero qué cambiaría. `--role admin` hace que la persona sea admin en lugar de propietaria (el valor por defecto, propietaria, es el correcto para alguien que configura su propia computadora).

### <a id="33-if-the-person-does-not-know-their-telegram-id"></a>3.3 Si la persona no sabe su ID de Telegram

```sh
aipager setup detect-chat --token-file ~/.config/aipager/bot-token --json
```

Pídele a la persona que pulse Start en el bot (o que le envíe cualquier mensaje) mientras esto espera (hasta 5 minutos; `--timeout SECONDS` lo cambia). Imprime el remitente más reciente en `candidate` (`id`, `first_name`, `last_name`, `username`) y no guarda nada. Muéstrale el nombre y el nombre de usuario a la persona y pregúntale «¿Eres tú?». Solo cuando diga que sí, ejecuta el paso 3.2 con `--chat-id` igual a `candidate.id`. Si `other_candidates` no es 0, otras personas también le escribieron al bot: asegúrate bien.

### <a id="34-read-the-result"></a>3.4 Leer el resultado

Con `--json`, setup imprime exactamente un objeto JSON en stdout. Lee `exit_code` (el mismo que el código de salida del proceso), `status`, `error`, `message`, `fix` y `next_step`. Si stdout no es JSON (una opción desconocida, una palabra equivocada después de `setup`, o un aipager tan viejo que no tiene `setup`), el código de salida es 2 y el motivo está en texto plano en stderr: léelo, y para un aipager viejo mira el punto 3.1.

| Salida | `error` | Qué haces |
|---|---|---|
| 0 | | `installed`, `updated` o `unchanged`: listo, pasa al 3.5. `dry_run`: no se escribió nada; ejecuta el mismo comando sin `--dry-run`. detect-chat con `found`: confirma a la persona como en el 3.3 y luego ejecuta el 3.2. |
| 1 | `config_malformed`, `ambiguous_install`, `settings_invalid`, `test_send_failed`, `write_failed`, `service_failed`, `internal_error` | Muéstrale `message` y `fix` a la persona. `ambiguous_install`: la persona lo arregla en `aipager config`. No edites archivos a mano. Un `settings_invalid` que pide un `CLAUDE_CONFIG_DIR` absoluto: el `fix` nombra el archivo que hay que cambiar (`daemon.env` o un archivo de ajustes de Claude Code); pregúntale a la persona por la carpeta, cambia ese único valor solo si te lo dice y vuelve a ejecutar setup. |
| 2 | `usage`, `token_malformed`, `token_stdin_timeout`, `bad_chat_id`, ... | Tu entrada estaba mal; `fix` nombra la opción. `token_malformed`: el archivo no tiene un token de bot; pídele a la persona que lo revise. |
| 3 | `deps_missing` | Sigue `fix` (cada elemento que falta también tiene su propio `fix` en `deps.items`) y vuelve a ejecutar setup. |
| 4 | `token_rejected` | Telegram rechazó el token. Pídele el token otra vez a la persona (o uno nuevo de @BotFather). |
| 5 | `chat_not_started`, `bot_blocked` | Pídele a la persona que abra el bot y pulse Start (o lo desbloquee), y vuelve a ejecutar setup. |
| 6 | `existing_install` | aipager ya está configurado con otro token, chat o rol. Dile a la persona qué es distinto y pregúntale antes de añadir `--force`. |
| 7 | `telegram_unreachable` | Problema de red o Telegram está ocupado. Espera un minuto y vuelve a intentarlo. |
| 8 | `detect_timeout` | detect-chat no vio ningún mensaje. Pídele a la persona que vuelva a pulsar Start y ejecútalo de nuevo. Con la advertencia `updates_backlog`, pulsar Start no ayuda: pregúntale a la persona su ID numérico. |
| 9 | `daemon_running` | detect-chat: un aipager para este bot ya está en marcha y recibe sus mensajes. Pregúntale a la persona su ID numérico (o pregúntale antes de detenerlo con `aipager service stop`). |
| 10 | `updates_conflict` | detect-chat: otro programa lee los mensajes de este bot. Pregúntale a la persona su ID numérico. |
| 130 | `interrupted` | Se detuvo con Ctrl-C. |

Si `daemon.restart_needed` es true, aipager necesita reiniciarse para usar el cambio, o setup no pudo saber si está en marcha (la advertencia `daemon_unknown`). Sigue `next_step`, que dice cuál de las dos (si propone `aipager start`, que se queda en primer plano, prefiere `aipager service install`). Díselo a la persona y reinicia (`aipager service stop` y luego `aipager service start`) solo si ella lo dice, y nunca desde una sesión que la persona maneja con aipager (mira el punto 4). La lista completa de errores, advertencias y claves JSON está en [la referencia del comando setup](commands.md#command-line-aipager-setup-for-coding-agents).

### <a id="35-check-the-install"></a>3.5 Comprobar la instalación

```sh
aipager doctor --json
```

Solo lee y no envía nada. La salida 0 significa que ninguna comprobación falló. Para cada comprobación cuyo `status` sea `fail` (o `warn`), muéstrale a la persona su `title`, `detail` y `fix`.

### <a id="36-report-back-to-the-person"></a>3.6 Contarle el resultado a la persona

Díselo en pocas líneas:

- el bot (`@` + `bot_username`), y si se envió el mensaje de prueba (`test_message`);
- qué se configuró (`changed`, y `service.result`);
- cómo arrancar una sesión de Claude: enviar `/new` al bot en Telegram, o ejecutar `aipager session <name>` en una terminal;
- lo que haya señalado `doctor`, y `next_step` si pide algo.

## <a id="4-what-you-must-not-do"></a>4. Lo que no debes hacer

- No imprimas, no hagas echo ni `cat` del token, y nunca lo pongas en una línea de comandos (`--token` se rechaza de todas formas). Pásalo solo como archivo o por stdin.
- No edites `~/.config/aipager/` (aparte del archivo del token del paso 2) ni `~/.claude/settings.json` a mano. Los escriben `aipager setup` y `aipager config`. La única excepción es un grupo en un aipager más antiguo que este, donde `aipager setup group --help` falla: mira [5.5](#55-older-installs).
- No añadas `--force` sin preguntarle a la persona: reemplaza su token, chat o rol actuales. Con `setup group` cambia o quita roles, o da el rol `owner`.
- A través de aipager, solo las tareas del propietario pueden ejecutar `aipager setup` (en cualquier forma), `aipager config` o `aipager uninstall`: las reglas de seguridad de aipager los bloquean para cualquier otro rol. Si la persona habla contigo a través de aipager y no es el propietario, pídele que ejecute el comando ella misma en una terminal.
- No reinicies ni termines tu propia sesión de Claude Code. En una primera instalación, la sesión en la que estás no está bajo aipager, y no hace falta que lo esté: las sesiones nuevas salen de `/new` en Telegram o de `aipager session <name>`. Si aipager ya está configurado y la persona habla contigo a través de él (por ejemplo para ejecutar `setup --force` con un token nuevo), no detengas ni reinicies aipager tú: tu tarea y su respuesta quedarían a medias. Dile a la persona que lo reinicie cuando termines.

## <a id="5-groups"></a>5. Grupos

`aipager setup` configura el chat privado de la persona con el bot. `aipager setup group` añade un grupo de Telegram, sus miembros y sus roles a una instalación que ya está configurada, sin preguntas (`aipager config` hace lo mismo con preguntas). Lee el token del bot de la instalación, nunca reinicia aipager y no envía nada al grupo. Mira [Uso en equipo y grupos](groups.md) y [la referencia del comando](commands.md#aipager-setup-group).

### <a id="51-what-only-the-person-can-do"></a>5.1 Lo que solo la persona puede hacer

Estos pasos no los puedes hacer tú; pídele a la persona que los haga en Telegram:

1. Añadir el bot al grupo.
2. Hacer al bot administrador del grupo con permiso para fijar mensajes (aipager fija ahí una barra de estado; sin ese permiso el grupo funciona, pero sin barra de estado). Solo un administrador del grupo puede hacerlo.
3. Enviar `/start` en el grupo, para que puedas encontrar su ID (5.2).
4. Darte, de cada miembro, su ID numérico de usuario de Telegram, un nombre corto (así lo muestra aipager, como `@label`) y un rol: `owner`, `admin`, `user`, `read_only` o un rol propio de `policy.yaml`. El rol `owner` tiene el control total de esta computadora: es de la persona que la administra y de nadie más. Si no sabe algún ID, `aipager config` puede encontrarlo mientras esa persona escribe en el grupo.

### <a id="52-find-the-group"></a>5.2 Encontrar el grupo

```sh
aipager setup detect-group --json
```

Espera (hasta 300 segundos, `--timeout SECONDS`) un mensaje en un grupo que todavía no está configurado, enviado como mucho 10 minutos antes de que lo ejecutaras, e imprime el grupo (`candidate.id`, `candidate.title`). No guarda nada. Muéstrale el título a la persona y usa el ID solo cuando confirme que es el grupo correcto. Salida 8 quiere decir que no llegó nada: pídele que envíe `/start` en el grupo otra vez.

### <a id="53-set-it-up"></a>5.3 Configurarlo

Ejecútalo primero con `--dry-run` y después el mismo comando sin él:

```sh
aipager setup group --chat-id -1001234567890 \
  --member 111111111:alice:owner --member 222222222:bob:user \
  --label team --dry-run --json
```

- `--member ID:LABEL:ROLE` una vez por persona. Para añadir a alguien más tarde, ejecútalo otra vez solo con el `--member` nuevo: los demás se conservan.
- `--remove-member ID` quita a una persona.
- `--label NAME` le pone nombre al grupo, `--root PATH` fija la carpeta donde trabajan sus miembros con `user` y `read_only` (por defecto `~/aipager-users/group-<label>`), y `--block-edits` bloquea Write y Edit para sus miembros con `user`.
- Ejecutar el mismo comando otra vez no cambia nada (`unchanged`).
- La salida 6 (`needs_force`) quiere decir que el cambio necesita `--force`: `force_reasons` dice por qué (un cambio de rol, un propietario dado, degradado o quitado, una carpeta raíz arriesgada). Muéstraselo a la persona y añade `--force` solo si está de acuerdo.

### <a id="54-read-the-result"></a>5.4 Leer el resultado

| Salida | Qué hacer |
|---|---|
| 0 | Hecho (`created`, `updated`, `unchanged` o `dry_run`). Dile a la persona el `label` del grupo, sus `members` y lo que dice `daemon.outcome`. |
| 2 | Una opción está mal: `error`, `message` y `fix` la nombran. Corrígela y ejecútalo otra vez. |
| 6 | `needs_force`: pregúntale a la persona por cada entrada de `force_reasons`. |
| 11 | `not_set_up`: configura aipager primero ([3](#3-your-steps)). |
| 1 | Cualquier otra cosa (`config_malformed`, `policy_malformed`, `last_member`, `config_changed`...): muestra `message` y `fix`. Después de `config_changed`, ejecútalo otra vez. |

`daemon.outcome` dice qué hizo el aipager en marcha: `reloaded_live` (el grupo ya funciona), `no_daemon` (funcionará cuando aipager arranque), `restart_needed` o `not_applied` (muéstrale `next_step` a la persona; no reinicies aipager tú). Las advertencias nunca cambian el código de salida, pero muéstralas: `bot_not_in_group`, `bot_not_admin`, `bot_cannot_pin` y `group_not_found` quieren decir que a la persona le falta un paso de 5.1, y vale la pena mencionar `owner_granted` en voz alta.

### <a id="55-older-installs"></a>5.5 Instalaciones más antiguas

Si `aipager setup group --help` falla, este aipager es anterior al comando. Pídele a la persona que actualice aipager (`aipager update`) o, si quiere el grupo ya, añádelo a mano:

1. Añade un chat de grupo a `~/.config/aipager/aipager.yaml`, siguiendo [la estructura](groups.md#aipageryaml-schema-team-parts). El archivo contiene el token del bot: nunca lo imprimas ni le hagas `cat`, y mantén sus permisos en 0600 (`chmod 600 ~/.config/aipager/aipager.yaml`).
2. Compruébalo con `aipager policy validate` y luego que la línea `scope mode: ...` de `aipager doctor` cuente el grupo nuevo.
3. Mándale a aipager una recarga sin reiniciar, como se explica abajo.

En Linux, con el servicio en segundo plano:

```sh
pid=$(systemctl --user show -p MainPID --value aipager.service)
[ "${pid:-0}" -gt 0 ] && kill -USR1 "$pid"
```

En macOS:

```sh
pid=$(launchctl list | awk '$3 == "com.aipager.daemon" {print $1}')
[ "${pid:-0}" -gt 0 ] 2>/dev/null && kill -USR1 "$pid"
```

La comprobación `-gt 0` importa: sin aipager en marcha, `kill -USR1 0` mandaría la señal a los procesos de tu propia shell. Nunca reinicies aipager desde dentro de una sesión de aipager: tu tarea quedaría a medias.

## <a id="see-also"></a>Ver también

- [Comandos del bot y el comando setup](commands.md)
- [Solución de problemas](troubleshooting.md)
- [Modelo de seguridad](security.md)
