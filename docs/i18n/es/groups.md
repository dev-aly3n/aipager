# Uso en equipo y grupos

<!-- i18n-source: docs/groups.md @ daaa753 -->

> Esta página es una traducción de la página en inglés; si no coinciden, la versión en inglés es la correcta. ([English](../../groups.md))

Por defecto aipager es un bot privado: tú y el bot en un chat privado, nadie más. El **uso en equipo** lo abre a más personas: un grupo de Telegram donde varias personas que programan manejan sesiones, los chats privados de otras personas con el bot, o las dos cosas. Cada chat tiene sus propios miembros, y cada miembro tiene un rol que decide qué puede hacer ahí: enviar mensajes, responder a las preguntas y a las solicitudes de permiso de Claude, gestionar sesiones, o solo mirar.

El uso en equipo es opcional. Una instalación con solo tu chat privado funciona como antes.

## <a id="decide-carefully"></a>Decide con cuidado

Añadir a un usuario de Telegram a un chat le da **permiso para ejecutar código en la computadora donde funciona aipager**. Según su rol, puede:

- enviar mensajes que Claude convierte en comandos de shell, ediciones de archivos y peticiones de red;
- responder a las solicitudes de permiso (Permitir / Rechazar). Tu `~/.claude/settings.json` sigue decidiendo sobre qué herramientas pregunta Claude, pero cualquiera cuyo rol pueda aprobar puede tocar Permitir;
- crear, detener, terminar y cambiar sesiones.

Trata la lista de miembros como tratas el acceso SSH a la computadora. El registro de auditoría ([`~/.claude/aipager-audit.jsonl`](security.md#audit-log)) guarda quién hizo qué, pero solo después de que pasó.

## <a id="setup"></a>Configuración

Todo se configura con `aipager config`. Nunca tienes que editar un archivo a mano.

**Primera vez** (todavía no hay configuración). El asistente pregunta, en cuatro pasos:

1. el token del bot;
2. tu propio chat privado con el bot (se detecta solo, o lo pegas);
3. si tu cuenta es la `owner` (por defecto; si dices que no, serás `admin`, lo que mantiene activado para ti el piso de seguridad);
4. comprueba las dependencias e instala los hooks de Claude Code.

Escribe tu chat privado como primer chat. No pregunta un modo por defecto: su resumen dice «New sessions start in Auto mode; owners and admins can choose Full access in Telegram under /settings → New sessions.» (las sesiones nuevas arrancan en Modo Automático; los propietarios y los admins pueden elegir Acceso total en Telegram en /settings → Sesiones nuevas). Luego pregunta «Connected. Add a team group or other people now?» con Add a group, Add a person (DM) e I'm done.

**Las veces siguientes** abren el menú de edición. Muestra los chats que tienes y ofrece:

- **Add a group scope**: un grupo nuevo, con sus miembros (abajo).
- **Add a DM scope**: el chat privado de otra persona con el bot. Un chat privado tiene un miembro; eliges su rol (por defecto `user`; elige `owner` solo para una instalación de una sola persona, como el contenedor propio de un amigo). Para un rol sin `can_manage` (como `user`) luego pregunta su [carpeta raíz](#root-folders), con el valor por defecto ya puesto (`~/aipager-users/<label>`). Para una persona que ya tiene un chat privado configurado, el asistente lo dice y no cambia nada: usa Edit a member para cambiar su rol.
- **Edit a scope**: para un grupo, **Add a member**; para cualquier chat, Rename, Edit scope deny_tools, **Set the members' root folder** (mira [Carpetas raíz](#root-folders)) y Remove this scope (el último chat no se puede quitar). Termina las sesiones de un chat antes de quitarlo: sus sesiones en marcha siguen publicando ahí, y ningún chat puede llegar a ellas desde Telegram (desde la computadora, `aipager session ls` y `aipager session kill <name>` todavía pueden).
- **Edit a member**: Set role, Edit member deny_tools, Remove member (no en un chat privado, ni el último miembro de un grupo). Para un propietario o un admin (o un rol tuyo con `bypass_role_denies`), Edit member deny_tools solo dice que las herramientas bloqueadas no se aplican a ese rol.
- Test bot reachability, View policy, Re-install Claude Code hooks y Refresh bot token.

### <a id="adding-a-group"></a>Añadir un grupo

1. Añade el bot al grupo y elige Add a group scope (o Add a group la primera vez).
2. **El ID del grupo.** Elige la detección automática y envía `/start` en el grupo, o pega el ID (un número negativo). El asistente envía un mensaje de prueba y pregunta si llegó. Un grupo que ya está configurado se para aquí con «This group is already set up. Use Edit a scope → Add a member.» y no cambia nada.
3. **Una etiqueta** para el grupo (se muestra en el asistente y en la Mini App).
4. **Tú primero.** Cuando eres el propietario de tu propio chat privado, el asistente pregunta si añadirte como primer miembro del grupo con el rol `owner` (por defecto sí; puedes saltarlo). Sin un propietario o un admin en el grupo, nadie ahí podría usar el Acceso total, `/settings` ni `/update`.
5. **Los demás miembros**, uno por uno: cómo encontrarlos (detección automática, o pegar un ID o un `@handle`), una etiqueta (se muestra como `@label` en el chat) y un rol (por defecto `user`). La lista de roles incluye `owner`, con un aviso: un propietario tiene control total de esta computadora, incluidos la configuración de aipager, el token del bot y las credenciales de Claude. Dáselo solo a ti. Da `admin` a las personas que deban usar el Acceso total, `/settings` y `/update` en el grupo. (En su propio chat privado con el bot, cualquiera que pueda enviar mensajes y responder a Claude puede usar el Acceso total para sus propias sesiones, tenga el rol que tenga.)
6. **Una regla opcional:** «Block file edits (Write, Edit) for `user` members in this group? Admins and the owner are not affected.» (¿Bloquear la edición de archivos para los miembros `user` de este grupo? Los admins y el propietario no se ven afectados.) Por defecto es no: un `user` ya solo escribe dentro de la carpeta de proyecto de la sesión. Con sí, cada Write y Edit en una tarea de un miembro cuyo rol no está exento de las reglas de rol (`user`, `read_only`, y los roles que defines sin `bypass_role_denies`) se rechaza sin preguntar y la tarea se detiene. Entonces un admin o el propietario tiene que enviar ese trabajo.
7. **La carpeta raíz de los miembros**, con `~/aipager-users/group-<label>` ya puesto: donde los miembros `user` y `read_only` del grupo crean sus sesiones y trabajan (mira [Carpetas raíz](#root-folders)).

Para añadir a alguien a un grupo que ya está configurado, usa Edit a scope → el grupo → Add a member. Todos los que ya están en el grupo conservan su sitio y su rol.

### <a id="finding-ids-auto-detect"></a>Encontrar IDs (detección automática)

La detección automática de un miembro toma el mensaje más reciente de alguien que todavía no se añadió y pregunta «Is this the person to add?» (¿Es esta la persona que hay que añadir?) antes de usarlo (responde que no y busca más atrás). Propone su nombre de usuario de Telegram como etiqueta. En un grupo, la persona tiene que dirigirse al bot: mencionarlo o enviar `/start` (mira [qué lee el bot](#what-the-bot-reads-in-a-group)). El asistente dice dónde está mirando:

- **«Watching through the running daemon»** (a través del aipager en marcha): mientras `aipager start` está en marcha, aipager recibe todos los mensajes del bot, así que el asistente lee lo que aipager anotó en `~/.claude/aipager-pending-users.json`. aipager anota quién se dirige al bot en un grupo donde todavía no trabaja (el ID, el título y el tipo del grupo, y el ID, el nombre de usuario y el nombre de la persona), y a cualquiera que no sea miembro de un grupo donde sí trabaja. Nunca responde en un grupo donde no trabaja. Un mensaje privado de alguien que no está configurado recibe respuesta y no se anota, así que para añadir el chat privado de una persona mientras aipager está en marcha, pega su ID (o detén aipager primero).
- **«Watching Telegram directly»** (directamente en Telegram): cuando aipager está detenido, el asistente le pide él mismo a Telegram los mensajes recientes del bot.

Tu propio chat privado, la primera vez, siempre se lee de Telegram. Un mensaje de un admin anónimo del grupo, o uno publicado como el grupo o como un canal, no cuenta: su ID de remitente (1087968824, 136817688 o 777000) lo comparten todos los que publican así, así que el asistente lo rechaza, escrito o detectado. Pídele a la persona que publique con su propia cuenta.

### <a id="where-the-config-lives"></a>Dónde está la configuración

El asistente escribe `~/.config/aipager/aipager.yaml` (permisos 0600): el token del bot, los chats donde trabaja el bot, y los miembros de cada chat con sus roles. Lo que puede hacer cada rol está en `~/.config/aipager/policy.yaml`, que es tuyo: el asistente nunca lo escribe (mira [Cómo funcionan las reglas](#how-rules-work)). Las instalaciones configuradas con el formato antiguo `team.yaml` se migran solas, por aipager al arrancar o por el asistente la siguiente vez que se ejecute.

### <a id="live-reload"></a>Recargar la configuración sin reiniciar

Después de cada cambio de chats o de miembros hecho desde el menú de edición (añadir o quitar un grupo o un chat privado, añadir o quitar un miembro, poner un rol, editar `deny_tools`, cambiar el nombre de un chat), y después de un grupo o una persona añadidos al final de la primera vez o de un grupo a medias que retomas (una sola recarga para todo lo que añadiste ahí), `aipager config` le envía al aipager en marcha una recarga en vivo (**SIGUSR1**) y dice «Scopes reloaded live (no daemon restart needed)». La recarga vuelve a leer `aipager.yaml` y `policy.yaml`. Si la configuración del disco se rechazaría, el asistente dice «Not applied» con el motivo, y aipager se queda con su configuración anterior hasta que lo arregles. Si el asistente no puede saber con seguridad qué proceso es aipager, te dice que lo reinicies.

Lo que una recarga aplica al momento:

- quién puede usar el bot en cada chat, con qué rol y qué reglas;
- un grupo o chat privado nuevo empieza a recibir mensajes y recibe su menú `/`; uno quitado deja de hacerlo (sus sesiones siguen en marcha hasta que las termines);
- un mensaje retenido (uno que espera a que se responda una solicitud o a que termine la tarea de otra persona) de alguien que ya no puede enviar en ese chat se descarta: recibe un 🤷 y el bot responde «No se envió a Claude: @bob ya no puede enviar mensajes aquí.»;
- una tarea en curso de una persona a la que quitaron o le quitaron permisos se queda, el resto de esa tarea, solo con lo más estricto entre sus reglas viejas y las nuevas (el piso restringido para una persona quitada). Una tarea escrita en la terminal no cambia, salvo que se le haya sumado un mensaje de Telegram: aipager no registra de quién, así que una tarea así se restringe cuando cualquier propietario de ese chat (o, en un chat privado, su miembro) pierde permisos. Una tarea que ya estaba en curso cuando aipager se reinició (todavía trabajando, o esperando una pregunta o un permiso) se restringe por cada miembro de su chat que perdió permisos. Un ascenso nunca amplía una tarea en curso: los permisos nuevos se aplican desde la siguiente tarea de esa persona.

Solo un **token de bot** nuevo necesita reiniciar (`aipager config` lo dice después de Refresh bot token). Cuando no hay ningún aipager en marcha, no se envía ninguna recarga: el siguiente `aipager start` lee la configuración nueva.

Para recargar a mano (por ejemplo después de editar `policy.yaml`), envía la señal solo al proceso de aipager. Bajo el servicio en segundo plano:

```sh
pid=$(systemctl --user show -p MainPID --value aipager.service)
[ "${pid:-0}" -gt 0 ] && kill -USR1 "$pid"
```

(La comprobación importa: cuando el servicio no está en marcha, `MainPID` es `0`, y `kill -USR1 0` le enviaría la señal a los procesos de tu propio shell.)

Para un aipager que arrancaste tú con `aipager start`, envía `kill -USR1 <pid>` a ese proceso. No le envíes la señal a todo lo que coincida con `pgrep -f 'aipager start'` (un shell o un contenedor que ejecuta aipager también coincide, y SIGUSR1 detiene un proceso que no la maneja), y no uses `systemctl --user kill` sin `--kill-whom=main` (las sesiones del servicio comparten su grupo y se detendrían).

Si `aipager.yaml` o `policy.yaml` están rotos al recargar, o falta `aipager.yaml`, aipager escribe una advertencia en el log y se queda con la configuración anterior en memoria. Un error de escritura o un archivo apartado no pueden dejarte fuera ni dejar entrar a nadie: una recarga nunca devuelve al uso personal a un aipager que tiene chats configurados (solo un reinicio lo hace).

### <a id="bot-admin-rights-and-privacy-mode"></a>Permisos de admin del bot y modo privacidad

**Haz al bot admin del grupo, con permiso para fijar mensajes.** Lo necesita para fijar la barra de estado del grupo (mira [Lo que ve todo el mundo](#what-everyone-sees)). Sin eso, el grupo funciona pero no tiene barra de estado; si le das el permiso más tarde, reinicia aipager para que aparezca la barra.

Un bot que es admin del grupo recibe todos los mensajes del grupo, tenga el modo de privacidad que tenga en BotFather. Eso es seguro: aipager solo atiende los mensajes de [qué lee el bot en un grupo](#what-the-bot-reads-in-a-group) e ignora el resto, así que la charla del grupo nunca llega a una sesión. Un bot que no es admin está en modo privacidad por defecto (déjalo activado), y entonces Telegram solo le pasa algunos mensajes del grupo; si no llega uno tuyo, responde a uno de los mensajes del bot o usa un comando. El ajuste de privacidad no cambia nada de lo que atiende aipager.

Ser admin de Telegram del grupo no le da nada a nadie en aipager. Lo que cada persona puede hacer sale solo de su rol en `aipager.yaml`.

### <a id="when-telegram-upgrades-the-group"></a>Cuando Telegram mejora el grupo

Telegram convierte un grupo básico en supergrupo cuando lo haces público, activas los temas, pasa de 200 miembros o cambias algunos permisos de admin, y esa mejora le da al grupo un ID de chat nuevo. aipager lo sigue solo: cuando ve la mejora (el aviso de Telegram en el grupo, o el primer envío al ID viejo), mueve la entrada del grupo en `aipager.yaml` al ID nuevo, mueve con ella las sesiones, los destinos de los mensajes y los `/settings` del grupo, y publica una línea en el grupo: «Telegram actualizó este grupo. aipager se movió con él; no tienes que hacer nada.» La barra de estado fijada vuelve a empezar en el grupo mejorado. El log de aipager tiene una advertencia con los dos IDs.

Los nombres de las sesiones dentro de aipager conservan el ID viejo (nunca los ves), y `aipager.yaml` guarda un registro corto `chat_migrations` del cambio, para que una sesión creada antes de la mejora siga siendo del grupo mejorado, incluso después de un reinicio. El nombre interno de una sesión con el ID viejo (`/name__g<old id>`) funciona en el grupo mejorado y en ningún otro sitio. Si aipager estaba detenido durante la mejora, lo nota en el siguiente arranque. Una tarea que está en curso en el momento de la mejora termina y su respuesta llega al grupo mejorado, pero su tarjeta de progreso no se vuelve a mostrar durante el resto de esa tarea. Si el ID nuevo ya es un chat aparte en la configuración, no se mueve nada y el log lo dice; quita uno de los dos con `aipager config`. «Test bot reachability» en `aipager config` muestra el ID nuevo cuando se encuentra con un grupo mejorado.

En cada arranque, aipager consulta cada grupo una vez. Si Telegram responde que el grupo no existe o que el bot no puede usarlo (el grupo se borró, se quitó el bot, o el grupo se mejoró mientras aipager estaba detenido y Telegram no dijo a qué ID), el log de aipager tiene una advertencia con el nombre del chat: comprueba que el bot sigue en el grupo y, si se mejoró, vuelve a añadir el grupo en `aipager config` con Add a group scope (recibe el ID nuevo; vuelve a añadir a sus miembros) y luego quita el viejo con Edit a scope → Remove this scope (el ID viejo ya no funciona). aipager no publica nada y no lo vuelve a intentar.

### <a id="forum-topics-are-not-supported"></a>Los temas de foro no están soportados

Usa un grupo normal (sin temas) por equipo. En un grupo con los temas activados, aipager sigue funcionando, pero no se queda en un tema:

- la respuesta a un comando o un mensaje (por ejemplo `/status`, o la respuesta a «¿Qué sesión?») se queda en el tema donde se envió;
- los avisos, las tarjetas de actividad, las respuestas, las solicitudes de permiso y la barra de estado fijada se publican en el grupo sin tema, así que pueden caer en General;
- cada persona tiene una sesión actual para todo el grupo, no una por tema.

Activar los temas también mejora un grupo básico a supergrupo, que aipager sigue (arriba).

## <a id="roles"></a>Roles

Cuatro roles propios (mira `aipager/safety.py`). El rol que cuenta es el de la persona **en el chat en el que actúa**. Nadie puede cambiar miembros ni roles desde Telegram ni desde la Mini App: solo `aipager config` en la computadora lo hace, y eso lo deja en manos del propietario.

| | `owner` | `admin` | `user` | `read_only` |
|---|---|---|---|---|
| Enviar mensajes (`can_prompt`) | ✅ | ✅ | ✅ | ❌ |
| Responder a Claude: Permitir, Rechazar, preguntas (`can_approve`) | ✅ | ✅ | ✅ | ❌ |
| Gestionar: Acceso total, `/settings` de un grupo, `/update`, instalar la voz (`can_manage`) | ✅ | ✅ | ❌ (Acceso total: ✅ en su propio chat privado) | ❌ |
| Cambiar miembros y roles desde Telegram o la Mini App | ❌ | ❌ | ❌ | ❌ |
| Exento de las reglas de rechazo de rol (`bypass_role_denies`) | ✅ | ✅ | ❌ | ❌ |
| Exento del piso de seguridad (`bypass_safety`) | ✅ | ❌ | ❌ | ❌ |
| Bash y otras herramientas que ejecutan código | ✅ | ✅ (reglas de mejor esfuerzo) | ❌ | ❌ |
| Cualquier comando de barra | ✅ | ✅ | solo los del teclado de Comandos, `/compact` y los cambios de modelo | igual que `user` |
| Dónde se crean las sesiones (`/new`, Continuar, Reiniciar, el cambio de modo) | en cualquier sitio | en cualquier sitio | solo dentro de la [carpeta raíz](#root-folders) del chat | igual que `user` |
| Dónde se escriben los archivos | en cualquier sitio | en cualquier sitio que permita el piso de seguridad | la carpeta de proyecto de la sesión (cuando está dentro de la carpeta raíz) y su scratchpad, nunca la carpeta personal | igual que `user` |
| Lecturas en tu carpeta personal | en cualquier sitio que permita el piso de seguridad | en cualquier sitio que permita el piso de seguridad | solo dentro de la carpeta raíz (otras carpetas de la computadora como antes, las carpetas raíz de otros miembros nunca) | igual que `user` |
| Archivos de credenciales (`~/.ssh`, `~/.git-credentials`, ...) | legibles | legibles | nunca (por defecto) | nunca (por defecto) |
| `Grep`/`Glob` | en cualquier sitio | en cualquier sitio menos las carpetas protegidas de aipager (ni una carpeta que lo sea, esté dentro de una o contenga una) | la carpeta de proyecto de la sesión y su scratchpad | igual que `user` |

- **owner**: control total, incluido el piso de seguridad propio. Debería haber exactamente uno: la persona que administra la computadora.
- **admin**: todo lo que un propietario puede hacer en Telegram y en la Mini App: pasar sesiones al Modo Acceso total (y arrancarlas en él), reanudar una sesión en Acceso total, cambiar los `/settings` de un grupo, ejecutar `/update`, instalar la función de voz, enviar cualquier comando de barra. Un admin también está exento de las reglas de rechazo de rol (los `deny_tools` del chat y del miembro no se le aplican). Dos cosas quedan para el propietario:
  - cambiar miembros y roles: solo `aipager config` en la computadora lo hace, y nada en Telegram ni en la Mini App puede;
  - estar exento del piso de seguridad (la configuración y el token del bot de aipager, las credenciales de Claude Code, los archivos de control de aipager). El `Grep` y el `Glob` de un admin pueden buscar en cualquier otro sitio, pero no en una carpeta que sea una de esas, esté dentro de una o contenga una: `Grep` en `~`, `/` o `/tmp` (o sin carpeta desde una sesión en la carpeta personal) se rechaza, y también un `Glob` cuyo patrón empiece en una (como `/home/you/.config/aipager/*`). Busca dentro de una carpeta de proyecto. En versiones de Claude Code sin herramienta `Grep`, Claude busca por Bash; ahí también se rechaza una búsqueda o un listado recursivo de toda la carpeta personal o de `/` (`grep -rn x ~`, `rg x $HOME`, `find / -name x`), con mejor esfuerzo (una búsqueda sin carpeta, o con `..`, ejecutada desde la carpeta personal o por debajo no se ve), y las búsquedas en proyectos no, incluida una búsqueda DE una ruta personal (`grep -rn "$HOME" src`) ([Modelo de seguridad](security.md#team-mode-enforcement)).

  Un admin conserva Bash, y con un shell los patrones de comandos del piso son de mejor esfuerzo, no un límite: haz admin a alguien solo si le darías un shell en la computadora.
- **user**: se aplican las reglas de rechazo. Sin Bash, sin ninguna otra herramienta que ejecute código (PowerShell, Monitor, mensajes programados, workflows; la lista es `safety.CODE_EXECUTION_TOOLS`), sin `SendMessage` a otras sesiones, sin cambiar de worktree. Los archivos solo se escriben dentro de la carpeta de la sesión y de su scratchpad de Claude Code, y nunca en `.claude/`, `.git/` ni `.mcp.json` de esa carpeta (cada uno ejecuta comandos). `Grep` y `Glob` solo buscan dentro de esas carpetas, con globs relativos sencillos. Un `user` crea, reanuda, reinicia y cambia sesiones solo dentro de la [carpeta raíz](#root-folders) del chat, y no lee nada de tu carpeta personal fuera de ella. Una carpeta de sesión que es tu carpeta personal, `/` o una carpeta por encima, o que está fuera de la carpeta raíz (una sesión creada antes de las carpetas raíz, o por otra persona), no cuenta: ahí un `user` solo puede escribir y buscar en el scratchpad. Las lecturas siguen las reglas de rutas protegidas, y tus archivos de credenciales nunca se pueden leer: `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config/gh`, `~/.git-credentials`, `~/.netrc`, `~/.docker/config.json`, `~/.kube`, `~/.config/gcloud`, `~/.azure`, `~/.password-store`, `~/.pgpass`, `~/.npmrc` y `~/.pypirc`. Comandos de barra: solo los del teclado de Comandos, `/compact` y los cambios de modelo (mira [Con qué permisos se ejecuta un mensaje](#who-a-message-runs-as)).
- **read_only**: observadores. Ven todo en el grupo, y pueden usar `/status`, `/diff`, `/whoami`, `/help`, `/start` y `/app`, mirar `/settings` y pasar las páginas de una lista de `/resume` que abrió otra persona. Sus propios mensajes de texto, audio y archivos no se envían a ningún sitio. El primero que envían en un chat recibe una respuesta que dice que su rol es `read_only`; después, el bot no les dice nada en ese chat (ni respuesta, ni reacción) hasta que aipager se reinicia. Tienen los mismos límites de herramientas y escritura que `user`, por si alguna vez uno de sus mensajes llega a una sesión.

### <a id="custom-roles"></a>Roles personalizados

`policy.yaml` puede cambiar cualquier campo de un rol propio, o definir roles nuevos. Un campo que pones reemplaza el valor propio. Los campos:

- `can_prompt`, `can_approve`: enviar mensajes; responder a Claude.
- `can_manage`: usar el Acceso total, cambiar los `/settings` de un grupo, ejecutar `/update` e instalar la función de voz (propios: `owner` y `admin` true, `user` y `read_only` false). Nunca deja a nadie cambiar miembros ni roles, y nunca exime del piso de seguridad.
- `bypass_role_denies`, `bypass_safety`: mira la tabla.
- `deny_tools`, `allow_tools`, `deny_bash_patterns`, `deny_paths_no_access`, `deny_paths_no_write`: mira [Cómo funcionan las reglas](#how-rules-work).

Un rol que defines desde cero empieza con `can_prompt` y `can_approve` activados, `can_manage` y las dos exenciones desactivados, los archivos de credenciales en su `deny_paths_no_access`, y **ninguna herramienta rechazada, así que tiene Bash** hasta que pongas `deny_tools` (`aipager doctor` avisa una vez cuando un miembro lo tiene). El límite de escritura y búsqueda se aplica a cada rol sin `bypass_safety` ni `bypass_role_denies`. La [carpeta raíz](#root-folders) se aplica a cada rol sin `can_manage`, tenga las exenciones que tenga: un rol que defines con `bypass_role_denies` o `bypass_safety` pero sin `can_manage` escribe y busca en cualquier sitio dentro de su carpeta raíz (y de su scratchpad), no lee nada de tu carpeta personal fuera de ella, y en ningún otro sitio (con `bypass_safety`, en una sesión que funciona fuera de su carpeta raíz, solo escribe en su scratchpad). Solo los propietarios y los admins no tienen restricciones.

### <a id="root-folders"></a>Carpetas raíz

Cada chat tiene una carpeta raíz para los miembros: la carpeta donde los miembros cuyo rol ahí no tiene `can_manage` (los `user` y `read_only` propios, y los roles que defines sin él) crean y mantienen sus sesiones. Los propietarios y los admins no están limitados a ella. La carpeta raíz de un chat privado es de esa persona; la de un grupo la comparten los miembros restringidos del grupo. Dos chats nunca comparten una salvo que les des la misma carpeta a propósito (el asistente te pide que lo confirmes).

- **Ponerla.** `aipager config` la pregunta cuando añades un grupo, o un chat privado para un rol sin `can_manage`, con el valor por defecto ya puesto, y Edit a scope → **Set the members' root folder** la cambia (se aplica al momento, con una recarga en vivo). La configuración actual muestra la carpeta raíz de cada chat, con `(default)` cuando no hay ninguna puesta.
- **El valor por defecto** es `~/aipager-users/<label>` para un chat privado (la etiqueta de la persona) y `~/aipager-users/group-<label>` para un grupo. La etiqueta se pasa a minúsculas y todo lo que no sea una letra, un número, `.`, `_` o `-` se convierte en `-`; cuando dos chats acabarían en la misma carpeta, a cada uno se le añade el ID de su chat (`sara-123456`). La carpeta (y `~/aipager-users`, cuando es nueva) se crea, con permisos 0700 para que solo tú puedas abrirla, la primera vez que un miembro abre `/new` o el formulario de Nueva sesión de la Mini App. Antes de que empiece una sesión en ella, aipager la marca como de confianza para Claude Code, así la pregunta «Do you trust this folder?» de Claude Code no termina la primera sesión del miembro. Un valor por defecto nunca se mueve: cuando un cambio en `aipager config` (la carpeta raíz de otro chat, un chat quitado, una etiqueta nueva) cambiaría la carpeta por defecto de un chat, el asistente escribe la carpeta que tenía como carpeta raíz de ese chat.
- **Lo que rechaza el asistente**: una ruta relativa, caracteres comodín, `/`, tu carpeta personal o una carpeta por encima de ella, y una carpeta que sea, esté dentro de o contenga una de las carpetas protegidas de aipager o de los archivos de credenciales (`~/.claude`, `~/.config/aipager`, `~/.config`, `~/.ssh`, ...), y la carpeta temporal de Claude Code (`/tmp/claude-*`, donde vive el scratchpad de cada sesión). Una carpeta que todavía no existe está bien. Te pide confirmación para una carpeta desde la que tu sistema ejecuta programas (`~/bin`, `~/.local/bin`, `~/.config/systemd`, `~/.config/autostart`, o una dentro de ellas).
- **Crear sesiones.** La página de Carpeta de `/new` y la Mini App muestran la carpeta raíz y las carpetas que están directamente dentro de ella (no las ocultas, ni un enlace que lleve fuera de ella), sin la fila «Carpeta por defecto»; una sesión nueva arranca en la carpeta raíz salvo que elijas una de esas. Nueva carpeta crea la carpeta dentro de la que elegiste. Cualquier otra cosa recibe «Tu rol solo puede crear sesiones en tu carpeta ~/... o en una carpeta dentro de ella.»
- **Sesiones fuera de ella.** Continuar, reiniciar, Reemplazar y cambiar el modo de una sesión cuya carpeta está fuera de la carpeta raíz reciben «Esta sesión trabaja fuera de tu carpeta ~/..., así que tu rol no puede continuarla, reiniciarla ni cambiarle el modo. Pídeselo a un admin.» Los mensajes a una sesión así que esté en marcha se siguen enviando, y el hook de seguridad limita la tarea: ahí solo escribe en el scratchpad.
- **A qué limita el hook una tarea.** La tarea de un miembro restringido solo escribe en la carpeta de la sesión (cuando está dentro de la carpeta raíz) y en su scratchpad, no lee, ni hace `Grep` ni `Glob` de nada de tu carpeta personal fuera de la carpeta raíz (salvo su scratchpad y su propio archivo de respuesta), y nunca toca la carpeta raíz de otro chat, esté donde esté. Las carpetas fuera de tu carpeta personal (`/usr`, `/etc`, ...) siguen siendo legibles, las carpetas protegidas siguen rechazadas, y `Bash` sigue rechazado para `user`. Cuando una tarea mezcla a dos miembros con carpetas raíz distintas, gana la más estrecha; dos que no están una dentro de otra dejan solo la carpeta de la propia sesión.

Para devolverle Bash a `user` (solo para personas a las que darías un shell), nómbralo en `allow_tools` (que entonces pasa a ser también la lista de permitidos de ese rol) o reemplaza su `deny_tools`:

```yaml
roles:
  user:
    # la lista propia, menos Bash
    deny_tools: [PowerShell, Monitor, REPL, Workflow, CronCreate,
                 RemoteTrigger, AppifactRepl, self_hosted_runner_spawn_local,
                 self_hosted_runner_requeue_session, SendMessage,
                 EnterWorktree, ExitWorktree]
```

Entonces `aipager doctor` avisa: `role user has Bash: its safety rules are best-effort, not a boundary`. Si tu `policy.yaml` ya ponía `deny_tools` para `user` antes de que existiera este valor por defecto, tu lista sigue reemplazándolo, así que ese rol tiene Bash hasta que lo vuelvas a añadir; el aviso de doctor te lo dice. Mira [Modelo de seguridad → cómo se aplican las reglas en equipo](security.md#team-mode-enforcement) para lo que estos límites no cubren.

## <a id="talking-to-the-bot-in-a-group"></a>Hablar con el bot en un grupo

### <a id="what-the-bot-reads-in-a-group"></a>Qué lee el bot en un grupo

En un grupo, aipager solo atiende un mensaje que sea:

- **un comando**: `/status`, `/x1 fix the tests`, o lo mismo con `@aipagerbot` después del comando (`/x1@aipagerbot`), que Telegram añade cuando lo eliges del menú. Un comando para otro bot (`/x1@otherbot`) se ignora;
- **una respuesta a uno de los mensajes del bot**;
- **un mensaje que menciona al bot**: `@aipagerbot fix the tests`;
- **un toque en el teclado del bot** en el grupo. En un grupo, cada botón del teclado empieza con un cuadradito (`▫️ stop`, `▫️ Borrar conversación`), y solo ese texto marcado cuenta como toque. Escribir la palabra sola es charla normal: un miembro que escribe `stop`, `Borrar conversación`, `Opus` o el nombre de una sesión no cambia nada. Para usar a propósito una palabra del teclado sin el teclado, dirígete al bot: `@aipagerbot stop`, o `stop` como respuesta a uno de sus mensajes.

Todo lo demás (los miembros hablando entre ellos, una foto o un audio que no es respuesta al bot) no recibe ni reacción ni respuesta. Esto se cumple sea o no el bot admin del grupo. Los mensajes editados se ignoran en todas partes: edita un mensaje y no pasa nada; vuelve a enviarlo.

Una foto, un archivo o un audio funcionan igual: envíalo como respuesta al bot, o menciona al bot en el pie. En un álbum, pon el pie en su primer elemento: se toman los elementos del álbum que llegan después del que tiene el pie, la mención o la respuesta, y los anteriores no.

**La mención se quita.** `@aipagerbot fix the tests` llega a Claude como `fix the tests` (Claude Code leería `@word` como un archivo). `@aipagerbot status` y `@aipagerbot x2` funcionan como escribir `status` o `x2` en un chat privado, y `@aipagerbot` solo muestra adónde van tus mensajes. Un comando de sesión después de la mención funciona (`@aipagerbot /x1 fix it`), pero uno de los comandos propios de aipager no: `@aipagerbot /stop` recibe «Envía /stop solo, sin mencionar al bot.»

**Solo miembros.** Una persona que no es miembro del grupo recibe una respuesta («🚫 No estás entre las personas permitidas en este chat.») con su ID de Telegram para que te lo dé, y se anota para `aipager config`; después, el bot la ignora hasta que aipager se reinicia. A un miembro `read_only` se le dice su rol una vez (mira [Roles](#roles)).

**Los admins anónimos tienen que publicar con su cuenta.** Un admin del grupo con «Remain anonymous» activado, o un mensaje publicado como el grupo o como un canal, no dice quién lo escribió: Telegram muestra el mismo remitente para todos los admins anónimos. aipager no puede darle a un mensaje así el rol de nadie, así que responde «Escribe con tu propia cuenta para usar el bot.» (una vez por chat hasta que aipager se reinicia) y no hace nada más. Nunca se anota como usuario pendiente, y el asistente rechaza ese ID compartido. Desactiva «Remain anonymous» para ti en los ajustes de admin del grupo, o publica desde tu propia cuenta.

### <a id="talking-to-a-session-in-a-group"></a>Hablar con una sesión en un grupo

Responde a uno de los mensajes de una sesión (su respuesta, su tarjeta) y tu mensaje va a esa sesión. O envía `/x1 tu mensaje`, o un pie que empiece con `/x1`.

**Cada persona tiene su propia sesión actual**: la última a la que pasó (`/x1`), a la que le envió un mensaje, a la que respondió o que creó. Que responda la sesión de otro miembro no la cambia, y tampoco los mensajes de otras personas. Una mención, un audio, una plantilla o un comando del teclado van a tu propia sesión actual, y un comando del teclado o una plantilla la nombra en su respuesta («🧹 /clear enviado a x1»). Un `/stop`, `/mode`, `/kill`, `/restart`, `/rename`, `/delete` o `/diff` solo actúa sobre tu propia sesión actual (con su confirmación de siempre), y solo cuando el comando tiene sentido para ella (trabajando para `/stop`, terminada para `/delete`); si no, siempre muestra la lista para elegir, aunque quede una sola sesión, porque esa puede ser de otra persona. `/clearqueue` y `/now` actúan sobre tu sesión actual. `/kill x1` y los demás comandos con nombre actúan sobre esa sesión, como antes.

- Sin una sesión actual tuya y con más de una sesión en marcha, el bot pregunta «¿Qué sesión? Responde a uno de sus mensajes o escribe /x1 seguido de tu mensaje.» con un botón por sesión, y no envía nada. Con una sola sesión en marcha, va ahí.
- Si tu sesión actual terminó, el mensaje no se envía a la única sesión que sigue en marcha (puede ser de otra persona): el bot responde «x1 terminó. ¿Qué sesión?» con un botón por cada sesión en marcha y, cuando x1 se puede continuar, «▶️ Continuar x1», y no envía nada.
- Tocar el botón de una sesión en cualquiera de las dos preguntas también envía a esa sesión el mensaje por el que se preguntaba (un texto, el texto de un audio o un archivo), como si lo hubieras enviado ahí, y la pregunta cambia a «Enviado a x2.». Solo quien envió el mensaje puede usar los botones, y solo durante diez minutos y hasta que aipager se reinicia: después el botón solo hace que esa sea tu sesión actual («Ese mensaje ya es antiguo y no se envió; vuelve a enviarlo.»). ▶️ Continuar recupera x1 pero no envía nada: vuelve a enviar tu mensaje.
- La barra fijada no tiene la línea «Los mensajes van a» en un grupo, porque es distinta para cada persona.

**Las tarjetas que esperan una respuesta son de una persona.** La tarjeta de sesión nueva dice «Responde a este mensaje con un nombre y, si quieres, lo primero que hay que hacer:», y la pregunta de cambio de nombre «... Responde a este mensaje con el nuevo nombre.». En un grupo cada una muestra de quién es («🆕 Nueva sesión (@alice)»), solo esa persona puede responderla, y dos personas pueden crear sesiones o cambiar nombres a la vez. La respuesta de otro miembro a la tarjeta de sesión nueva de alguien recibe «Esta tarjeta es de @alice. Envía /new para abrir la tuya.» y no va a ningún sitio. Una pregunta de cambio de nombre deja de esperar a los 10 minutos, o en cuanto su persona hace otra cosa. La tarjeta «x1 está lista», la respuesta a `/x1`, `/start` y `/help` dicen que respondas al mensaje de una sesión o menciones al bot («Responde a un mensaje de x1 (o menciona a @aipagerbot) para hablar con ella.»), porque un mensaje normal no le llega al bot.

**El teclado es de cada persona.** Plantillas, Comandos, Modelo › y « Atrás cambian solo el teclado de quien los toca: el bot responde como respuesta al toque de esa persona, y el teclado de nadie más se mueve. « Atrás sube un nivel desde donde estás, miren lo que miren los demás miembros. El teclado principal que envía `/start` es de todo el grupo, y devuelve a todo el mundo al teclado principal. Cuando una sesión del grupo arranca o termina, aipager le envía al grupo ese teclado principal otra vez, una vez, para que sus botones de sesión estén al día. Después de actualizar desde una versión sin los botones marcados, puede que se ignore un toque en un botón del teclado viejo: envía `/start` una vez para darle al grupo el teclado nuevo. El teclado de un chat privado no tiene marca y funciona como antes.

### <a id="the-mini-app"></a>La Mini App

`/app` solo abre la Mini App en un chat privado. En un grupo no está en el menú `/`; si se escribe ahí, a un miembro cuyo chat propio con el bot está configurado le dice «📱 La Mini App solo funciona en un chat privado: escribe al bot por privado y envía /app ahí.», y a cualquier otra persona «La Mini App se abre desde tu chat privado con el bot. Pide al propietario del bot que te añada.»

La Mini App muestra un chat a la vez. Quien está en más de un chat (por ejemplo su propio chat privado y un grupo) tiene una fila con los nombres de los chats arriba para pasar de uno a otro; se abre en su propio chat privado, si no en el primer chat que lo incluye, y recuerda la última elección en ese teléfono. Todo lo que hay dentro (las sesiones, sus botones, los ajustes y los avisos que publica) es del chat elegido, con el rol que la persona tiene en ese chat. Con un solo chat no se muestra nada de más.

## <a id="who-a-message-runs-as"></a>Con qué permisos se ejecuta un mensaje

Cada mensaje se ejecuta con **los permisos de quien lo envió**, en el chat desde el que lo envió. Alguien que es `user` en el grupo y `owner` de su propio chat privado envía, toca y ejecuta comandos en el grupo como `user`, sea cual sea el orden de los chats en `aipager.yaml`.

- Cada mensaje de Telegram lleva a la sesión la identidad y las reglas de quien lo envió (mira [Modelo de seguridad → cómo se aplican las reglas en equipo](security.md#team-mode-enforcement)). Claude ve quién envió cada mensaje (`[via Telegram · @bob · role:user]` en un grupo), nunca a quien manejaba la sesión antes. Quien envía un mensaje pasa a manejar la sesión, lo haya escrito, haya usado `/x1`, una plantilla o un comando del teclado.
- **Los mensajes retenidos conservan a su remitente.** Mientras está en curso la tarea de otra persona (incluido un agente en segundo plano que arrancó), o mientras el mensaje de otra persona todavía espera a que lo tomen, o mientras hay una solicitud de permiso o una pregunta abierta, un mensaje nuevo se retiene (👀) y se envía cuando eso termina, como su propia tarea, **con los permisos de su propio remitente**. Una tarea escrita en la terminal cuenta como del propietario: solo el mensaje de un propietario (o, en un chat privado, el de su propio miembro) se suma a ella. Tus propios mensajes nunca esperan a tu propia tarea. Si la tarea que esperan se detiene, o se para porque se bloqueó una herramienta, los mensajes retenidos se descartan (🤷): vuelve a enviarlos.
- **Gana la regla más estricta.** Si los mensajes de personas distintas acaban de todas formas en una sola tarea, la tarea se ejecuta con la combinación más restrictiva de todos los que están en ella. Un mensaje que se suma a una tarea en curso nunca la amplía: el resto de la tarea conserva las reglas más estrictas de todos los que se sumaron.
- **Comandos de barra de los roles restringidos.** Un rol que no está exento de las reglas de rol (`user`, `read_only`, los roles que defines sin `bypass_role_denies`, y un remitente que aipager no conoce) solo puede enviar los comandos de barra del teclado de Comandos (el tuyo, si `keyboard.json` lo cambia), `/compact` y los cambios de modelo (`/model <name>`). Cualquier otro, escrito, enviado con `/x1 /deliver ...`, como pie de un archivo, como primer mensaje de `/new x1 /review ...` o como plantilla, recibe «Ese comando es solo para admins.» y un 🤷, y no llega nada a Claude. Los propietarios y los admins envían cualquiera.
- **Los roles restringidos se quedan fuera de la carpeta personal y de los archivos de credenciales.** Una tarea de `user` o `read_only` nunca escribe en una carpeta de sesión que sea tu carpeta personal, `/` o una superior (solo en su scratchpad), y nunca lee los archivos de credenciales que aparecen en [Roles](#roles).
- **Los roles restringidos se quedan en su carpeta raíz.** La tarea de un rol sin `can_manage` no lee nada de tu carpeta personal fuera de la [carpeta raíz](#root-folders) del chat, y no escribe nada en una carpeta de sesión fuera de ella. La carpeta raíz es la del chat del que vino el mensaje del miembro, vaya a la sesión que vaya.
- **Un miembro quitado pierde sus permisos al momento, no su tarea en curso.** Un remitente que aipager ya no conoce (quitado mientras su mensaje esperaba) se ejecuta con el piso restringido. Después de una recarga en vivo se descartan sus mensajes retenidos, y una tarea suya que sigue en curso sigue funcionando, sujeta al piso restringido hasta que termine; envía `/stop` para terminarla ya (mira [Recargar la configuración sin reiniciar](#live-reload)).
- **Un mensaje de otra sesión de Claude lleva los permisos de quien lo envía.** Un mensaje que una sesión le envía a otra (el `SendMessage` de Claude Code) se ejecuta con los permisos de la tarea que lo envió, tal como los verificó aipager, nunca con los anteriores de la sesión que lo recibe. Un mensaje que aipager no puede verificar se ejecuta con las reglas más estrictas y sin cambios de archivos ni acceso a la web (mira [Modelo de seguridad → mensajes entre sesiones de Claude](security.md#messages-between-claude-sessions)).
- **Una herramienta bloqueada detiene la tarea.** Cuando una tarea intenta una herramienta que sus reglas rechazan, la tarea se detiene al momento con «🛑 x1 · Bloqueado por una regla de seguridad: ... (detenida)», y cada herramienta posterior de esa tarea también se rechaza.

## <a id="who-can-tap-what"></a>Quién puede tocar qué

Cada botón comprueba el rol de quien lo toca, en el chat donde está el botón, en el momento en que lo toca. El rol de quien envió el comando que creó el botón no cuenta. (Algunas tarjetas también comprueban quién las pidió: las tarjetas de confirmación y las tarjetas de `/new`, abajo.)

- **Mirar está abierto a todos los miembros** (VIEW). Los miembros `read_only` pueden pasar las páginas de `/resume`, abrir la vista Terminadas de `/status`, el menú ⋮ de una sesión, su modo o su diff, recorrer `/settings` y cerrar un menú. No pueden actuar: cualquier otro botón responde «Tu rol en este chat no permite hacer eso.»
- **Responder a Claude necesita `can_approve`** (APPROVE): Permitir, Permitir siempre, Rechazar, las opciones de una pregunta, Enviar, Continuar y el Responder de la barra fijada. `read_only` lo tiene desactivado.
- **Actuar sobre una sesión necesita `can_prompt`** (PROMPT): Detener, Reintentar, Compactar, Enviar ahora, Terminar, Reiniciar, Cambiar nombre, Eliminar, Continuar, el cambio de modo, ✍️ hablar, Borrar todas, los ajustes propios de una sesión y las tarjetas de `/new`. Un botón que aipager no conoce también lo necesita.
- **Pasar a Acceso total necesita un admin** (MANAGE: `owner`, `admin`, o un rol con `can_manage`, la misma regla que `/mode full`): Sí, cambiar y Detener y cambiar en una tarjeta que pasa a Acceso total, y Continuar en Acceso total. Pasar entre Preguntar y Automático es de cualquiera que pueda actuar sobre la sesión. Continuar una sesión que estaba en Acceso total (escribiendo `/resume x1`, con la lista, con Continuar en la tarjeta de nombre de `/new`, o desde la Mini App) la devuelve en Modo Automático para cualquier otra persona, y la respuesta lo dice: «♻️ x1 vuelve a estar en marcha en Modo Automático: el Modo Acceso total es solo para admins.»
- **En su propio chat privado, una persona puede usar el Acceso total tenga el rol que tenga** (un `user` incluido; no `read_only`, que no envía mensajes; un rol personalizado necesita `can_prompt` y `can_approve`, porque el Acceso total responde a todas las solicitudes de permiso de Claude Code). Todas las vías hacia el Acceso total de arriba le funcionan ahí: `/mode full`, la tarjeta de modo, `/new !x1`, las tarjetas de `/new`, `/resume` y la Mini App. Su propio chat es el único donde se aplica esto: en un grupo, y en el chat privado de cualquier otra persona, el Acceso total sigue necesitando un admin. Sus sesiones nuevas arrancan en Modo Automático, como las de todos, hasta que elija Acceso total en `/settings` → Sesiones nuevas. Nada más de lo que hace un admin se le abre (`/update`, el botón de instalar la voz y los `/settings` de un grupo siguen siendo solo para admins). Repartir el Acceso total así es seguro: arranca Claude Code con `--dangerously-skip-permissions`, que solo se salta las propias solicitudes de permiso de Claude Code. El hook PreToolUse de aipager sigue ejecutándose en cada herramienta y aplica las reglas del rol del miembro (el piso de seguridad, el límite de carpetas, las reglas de rechazo, los archivos de credenciales) exactamente igual que en Preguntar y Automático.
- **Instalar necesita un admin** (UPDATE): los botones de instalar y reiniciar de la función de voz, igual que los de `/update`.
- **Cambiar los `/settings` de un grupo necesita un admin.** Mirarlos puede cualquiera.
- **Una tarjeta de confirmación es de quien la pidió.** En un grupo, las tarjetas de confirmación de Terminar (`/kill`, ⋮ Terminar sesión), Reiniciar, Eliminar y el cambio de modo, las listas de sesiones que muestran `/kill`, `/restart`, `/delete`, `/mode`, `/stop`, `/rename` y `/diff`, y la tarjeta «Continuar x1 en:» que abre un toque en Continuar (sus botones de modo) solo responden a quien envió el comando o tocó el botón que las abrió. El toque de cualquier otra persona, Cancelar incluido, recibe «Esta tarjeta es de @alice. Envía /kill x2 para tener la tuya.» (con el comando de esa tarjeta) y no cambia nada. La propia tarjeta de `/mode` (en qué modo está una sesión) es de todos: un cambio tocado en ella abre una confirmación que es de quien tocó. Las tarjetas de `/new` solo responden a quien envió `/new`. El dueño de una tarjeta se recuerda hasta que se usa o se cancela, también después de un reinicio de aipager (las 4096 tarjetas más recientes). La tarjeta de nombre de `/new` y la pregunta de nombre de `/rename` son la excepción: caducan a los 10 minutos de todas formas, así que después de un reinicio vuelve a enviar el comando.
- **Un botón solo funciona en el chat de su propia sesión.** Uno tocado en cualquier otro sitio responde «Este botón pertenece a otro chat.» y no hace nada, y Continuar siempre continúa la sesión que nombra el botón, nunca una sesión de otro chat con el mismo nombre.
- **Los botones viejos no cambian nada.** Un botón que quedó de una tarea anterior, o de antes de que la sesión terminara y volviera, no actúa sobre lo que está en curso ahora; lo dice (mira [comandos → botones viejos](commands.md#stale-buttons)).

## <a id="what-everyone-sees"></a>Lo que ve todo el mundo

**Respuestas a Claude.** Cada Permitir, Rechazar y respuesta se publica en el chat, debajo de la tarjeta de la sesión, con quién lo hizo:

```
✅ x1 · Permitido · @alice · Bash: ls -la /tmp
🚫 x1 · Rechazado · @bob · WebFetch: https://example.com
✓ x1 · Respondido · @carol · Which database? → Postgres
```

**Quién cambió una sesión.** Un toque en un botón solo lo ve quien toca, así que en un grupo cada resultado que cambia una sesión dice quién lo hizo:

```
⚠️ x1 · @bob la detuvo
⏹ @bob terminó x2
🔄 @alice reinició [x1].
🗑️ @carol eliminó [x3].
⚙️ @alice cambió la sesión a 🤖 Modo Automático.
Lo cambió @alice.          (debajo de /settings)
```

La tarjeta «x1 está lista» dice quién cambió su modo o su modelo («@bob cambió el modelo a Opus.»). En un chat privado estas líneas de resultado no nombran a nadie. Un cambio hecho en la Mini App se publica en el chat como «... desde la Mini App» (una detención desde ahí también nombra a quién en la tarjeta de la sesión).

**Una herramienta bloqueada por una regla.** Una tarea se detiene con «🛑 x1 · Bloqueado por una regla de seguridad: Write is in this role's deny_tools (detenida)». Si Claude Code pide permiso para una herramienta que no puede usar la persona cuyo mensaje empezó la tarea en curso, el bot responde Rechazar él solo, no muestra ninguna solicitud, y publica `⛔ x1 · Edit bloqueado para @bob (rol user)` con el resumen de la llamada debajo, nombrando a esa persona. Una tarea que empezaron los mensajes de varias personas se bloquea cuando cualquiera de ellas no puede usar la herramienta, y nombra a esa. Una tarea escrita en la terminal es tuya y nunca la bloquean las reglas de un miembro, salvo que se le haya sumado un mensaje de Telegram: entonces se comprueba con las reglas de quien envió el último mensaje de la sesión (la lista propia del grupo cuando eso no se sabe). Cuando aipager no sabe de quién es la tarea (ya estaba en curso cuando aipager se reinició), usa las reglas de quien envió el último mensaje de la sesión, y la lista propia del grupo cuando eso tampoco se sabe.

**Estado de los mensajes.** 👀 en un mensaje significa que se envió a la sesión (o que está retenido hasta que se pueda); 👍 significa que Claude lo tomó; 🤷 significa que se descartó antes de que Claude lo tomara. `/whoami` muestra tu etiqueta, tu rol y tus reglas en este chat.

**La barra de estado fijada.** El grupo tiene su propia barra de estado fijada (mira [comandos → la barra de estado fijada](commands.md#the-pinned-status-bar)) que solo muestra las sesiones de este grupo, si el bot es un admin con permiso para fijar. Sin ese permiso, la barra se omite para el grupo: el mensaje se borra en lugar de quedarse sin fijar en el historial.

**Auditoría en disco.** `~/.claude/aipager-audit.jsonl` registra cada decisión con `user_id`, `username` y el chat, para que más tarde puedas reconstruir qué hizo cada persona. Siempre se escribe en inglés, esté el chat en el idioma que esté.

**Idioma.** Un grupo tiene un idioma para los mensajes, las tarjetas y los teclados propios de aipager, que pone un admin con `/language`; hasta que un admin elige uno, está en inglés. Nunca sigue la app de Telegram de nadie. Lo que solo ve una persona, el aviso corto después de un toque, está en el idioma de esa persona: lo que eligió en su chat privado con el bot, si no el idioma de su app de Telegram para alguien que llegó después de esta versión, y si no el idioma del grupo. Solo cambian los textos de aipager; Claude responde en el idioma en que le escribe cada persona. Mira [Idiomas](commands.md#languages).

## <a id="where-messages-go"></a>Adónde van los mensajes

- **Cada sesión es de un chat, y todo lo suyo va ahí**: sus tarjetas, respuestas, solicitudes de permiso, archivos y avisos. Un mensaje en un chat nunca va a una sesión de otro chat, y el nombre de una sesión solo funciona en su propio chat: escribir el nombre de una sesión de otro chat responde «No hay ninguna sesión con ese nombre». Una sesión creada mientras aipager funcionaba sin chats configurados recibe su chat en cuanto `aipager config` los añade; hasta que una sesión tiene un chat, ningún chat la muestra ni puede actuar sobre ella.
- **Las sesiones creadas en la terminal** (`aipager session <name>`) son del chat privado del propietario: el chat privado cuyo miembro tiene el rol `owner` (un rol con `bypass_safety`). Con varios chats privados y ninguno del propietario, es el primer chat privado de la lista; sin ningún chat privado, el primer chat. Nunca van a un grupo en una instalación que tiene un chat privado.
- **La mejora a supergrupo se sigue sola** (mira [Cuando Telegram mejora el grupo](#when-telegram-upgrades-the-group)).

## <a id="aipageryaml-schema-team-parts"></a>Estructura de `aipager.yaml` (partes del equipo)

```yaml
schema_version: 3
bot_token: "…"

scopes:
  - kind: dm
    chat_id: 12345       # tu propio chat privado: su ID es tu ID de usuario
    label: owner DM
    members:
      - id: 12345
        label: alice
        role: owner
  - kind: group
    chat_id: -100123456789
    label: team
    deny_tools: [Write, Edit]   # opcional: la respuesta a «Block file edits»
    root: /home/alice/aipager-users/group-team   # la carpeta raíz de los miembros
    members:
      - id: 12345        # ID de usuario de Telegram (NO una etiqueta, NO un ID de chat)
        label: alice     # cómo se muestra a la persona en el chat (@alice)
        role: owner
      - id: 67890
        label: bob
        role: user
        deny_tools: [WebFetch]  # opcional, por miembro
      - id: 11111
        label: charlie
        role: read_only
```

Un chat privado y los chats de grupo conviven: el bot trabaja en cada chat de la lista, con las sesiones de cada chat separadas. `aipager config` escribe este archivo; nunca hace falta que lo edites.

## <a id="how-rules-work"></a>Cómo funcionan las reglas

`~/.config/aipager/policy.yaml` define las reglas de cada rol. Compruébalo con `aipager policy validate`. Campos que admite cada rol (además de los de [Roles personalizados](#custom-roles)):

- `deny_tools`: nombres de herramientas que se rechazan sin preguntar (por ejemplo `Write`, `Edit`, `Bash`, `WebFetch`).
- `allow_tools`: si no está vacío, una lista de permitidos: todo lo demás se rechaza para ese rol.
- `deny_bash_patterns`: patrones que se comparan con las entradas de `Bash`.
- `deny_paths_no_access` / `deny_paths_no_write`: reglas de rutas. Para `user`, `read_only` y cada rol que defines sin `bypass_role_denies`, `deny_paths_no_access` empieza con los archivos de credenciales que aparecen en **user** arriba; ponerlo reemplaza esa lista, así que copia en la tuya los que quieras conservar (una tarea en curso a la que se suma otro mensaje vuelve a quedar sujeta a la lista hasta que termina). Una regla sin `/` ni `~` al principio (`**/.env`) coincide en cualquier sitio, así que mientras haya una, cada `Grep`/`Glob` de un rol restringido se rechaza (cualquier búsqueda podría leer un archivo así), lo que detiene esa tarea.

Los `deny_tools` de un chat y de un miembro en `aipager.yaml` se suman a los del rol, y los `allow_tools` de un miembro se suman a la lista de permitidos del rol. Todo esto solo se aplica a los roles sin `bypass_role_denies`: no afecta a un propietario ni a un admin.

Los archivos de `~/.config/aipager/policy.d/*.yaml` se leen después de `policy.yaml`, por orden de nombre, con el mismo formato: un campo de rol puesto en un archivo posterior reemplaza el valor anterior, y una lista `safety:` solo suma. `aipager policy validate` los comprueba todos.

Debajo de todos los roles hay un **piso de seguridad** (rutas protegidas y patrones de comandos) del que solo está exento `owner`: los admins también están sujetos a él. Es la lista propia de aipager más la sección `safety:` de `policy.yaml`, que solo puede sumar:

```yaml
safety:
  deny_paths_no_access: [/srv/secrets/**]   # ni leer, ni escribir, ni buscar
  deny_paths_no_write: [~/notes/**]         # leer sí, escribir no
  deny_bash_patterns: ['\bcurl\b']          # comandos de Bash que se bloquean
```

Cada tarea de cada rol excepto `owner` lo recibe entero, y también las tareas que aipager no puede atribuir a nadie; `aipager doctor --safety-check` muestra lo que está en vigor. Después de una recarga en vivo, un cambio en él se aplica a los mensajes enviados después de la recarga; una tarea que ya está en curso, o un mensaje que ya espera en la cola, conserva la lista vieja (detén esa tarea para aplicar el cambio al momento). Una ruta en `deny_paths_no_access` ahí sin `/` ni `~` al principio coincide en cualquier sitio, así que mientras haya una, cada `Grep`/`Glob` de cada rol excepto `owner` se rechaza (`aipager doctor` avisa de ello). Las rutas protegidas valen para las herramientas de archivos de Claude Code (`Read`, `Write`, `Edit`, `MultiEdit`, `NotebookEdit`, `LSP`, `Grep`, `Glob`); el `Grep`/`Glob` de un rol restringido también tiene que quedarse dentro de la carpeta de la sesión (una búsqueda de `aipager/**` en `~/.config` se rechaza). Herramientas como `WebFetch` o `SendFile` no se comprueban por ruta. Una tarea de Telegram que aipager no puede atribuir a un remitente recibe el piso, que es al menos tan estricto como `user`. Reintentar se ejecuta como quien lo toca si esa persona envió el mensaje que se reintenta, y con el piso si no. Los patrones de comandos solo importan para un rol que tiene Bash, y ahí son un filtro, no un muro; mira [Modelo de seguridad → cómo se aplican las reglas en equipo](security.md#team-mode-enforcement).

## <a id="privacy-considerations"></a>Cuestiones de privacidad

- El bot solo escucha **los chats configurados**. Añadir el bot a otro grupo no lo activa ahí: nunca responde ahí. Sí anota quién se dirige a él ahí (su ID, su nombre de usuario y su nombre, y el ID y el título del grupo) en `~/.claude/aipager-pending-users.json`, para que `aipager config` pueda añadir ese grupo. Solo tú puedes leer ese archivo, y guarda las 200 personas más recientes vistas en los últimos 30 días.
- Los miembros `read_only` **pueden leer** los mensajes, las respuestas y las entradas de las herramientas. No pueden actuar, pero ven todo en el grupo. Si algún trabajo tiene que quedar oculto para alguien, esa persona no debería estar en el grupo.
- En una instalación con chat privado, tus sesiones de terminal y las de tu chat privado nunca aparecen en un grupo (mira [Adónde van los mensajes](#where-messages-go)). Una sesión de terminal que un aipager anterior ya movió al grupo sigue siendo del grupo: termínala, elimínala de las sesiones terminadas del grupo y vuelve a crearla.
- `aipager.yaml` tiene permisos 0600 (solo su dueño). aipager no pone los permisos de `policy.yaml`: mantenlo tú solo para su dueño (`chmod 600 ~/.config/aipager/policy.yaml`).
- El registro de auditoría (`~/.claude/aipager-audit.jsonl`) no tiene permisos propios: es tan privado como tu carpeta `~/.claude`.

## <a id="revoking-a-user"></a>Quitarle el acceso a un usuario

1. Ejecuta `aipager config` → Edit a member → el chat → esa persona → Remove member. Para quitarle el permiso de enviar mensajes pero dejar que siga leyendo, usa en su lugar Set role y elige `read_only`. (Para volver a añadirla más tarde: Edit a scope → el grupo → Add a member.) Para el chat privado de una persona, termina sus sesiones y luego Edit a scope → su chat privado → Remove this scope.
2. El asistente recarga aipager en vivo; dice «Scopes reloaded live». Al momento se descartan sus mensajes retenidos (🤷 y una respuesta «No se envió a Claude») y una tarea suya que sigue en curso queda sujeta al piso restringido hasta que termine. Envía `/stop` para terminar ya esa tarea.
3. Sácala también del grupo de Telegram. Mientras siga ahí, puede seguir leyendo todo lo que se publica: tarjetas, respuestas, entradas de las herramientas y solicitudes de permiso.

Revisa lo que dice el asistente después del paso 1. Si dice que reinicies aipager, no pudo llegar a él: reinícialo (`aipager service stop` y luego `aipager service start`), porque hasta entonces la lista anterior de miembros sigue en memoria. Si dice «Not applied», arregla el problema que nombra: aipager conserva la configuración anterior hasta entonces. Después de editar `aipager.yaml` a mano, envía tú la recarga (mira [Recargar la configuración sin reiniciar](#live-reload)).

## <a id="related-docs"></a>Páginas relacionadas

- [Arquitectura](architecture.md): cómo funciona el proceso.
- [Comandos del bot](commands.md): referencia de la interfaz.
- [Modelo de seguridad](security.md): límite de confianza, lista de amenazas.
- [Solución de problemas](troubleshooting.md): referencia de `aipager doctor`.
