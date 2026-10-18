# Instalar con un agente

<!-- i18n-source: docs/install-with-an-agent.md @ 6fc084f -->

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

setup comprueba el token con Telegram, comprueba esta computadora, envía a la persona un mensaje de prueba ("aipager is set up for you. ...") y solo entonces escribe su configuración, añade los hooks de Claude Code a `~/.claude/settings.json` y, con `--service`, instala y arranca el servicio en segundo plano. Si alguna comprobación falla, no se escribe nada.

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
| 1 | `config_malformed`, `ambiguous_install`, `settings_invalid`, `test_send_failed`, `write_failed`, `service_failed`, `internal_error` | Muéstrale `message` y `fix` a la persona. `ambiguous_install`: la persona lo arregla en `aipager config`. No edites archivos a mano. |
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
- No edites `~/.config/aipager/` (aparte del archivo del token del paso 2) ni `~/.claude/settings.json` a mano. Los escriben `aipager setup` y `aipager config`.
- No añadas `--force` sin preguntarle a la persona: reemplaza su token, chat o rol actuales.
- No reinicies ni termines tu propia sesión de Claude Code. En una primera instalación, la sesión en la que estás no está bajo aipager, y no hace falta que lo esté: las sesiones nuevas salen de `/new` en Telegram o de `aipager session <name>`. Si aipager ya está configurado y la persona habla contigo a través de él (por ejemplo para ejecutar `setup --force` con un token nuevo), no detengas ni reinicies aipager tú: tu tarea y su respuesta quedarían a medias. Dile a la persona que lo reinicie cuando termines.

## <a id="5-groups"></a>5. Grupos

`aipager setup` configura el chat privado de la persona con el bot. Para añadir un grupo de Telegram con más personas y sus roles, la persona ejecuta `aipager config` (configurar grupos todavía no se puede hacer con un script). Mira [Uso en equipo y grupos](groups.md).

## <a id="see-also"></a>Ver también

- [Comandos del bot y el comando setup](commands.md)
- [Solución de problemas](troubleshooting.md)
- [Modelo de seguridad](security.md)
