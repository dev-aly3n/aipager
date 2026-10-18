# Eventos de hook

<!-- i18n-source: docs/hooks.md @ 054b893 -->

> Esta página es una traducción de la página en inglés; si no coinciden, la versión en inglés es la correcta. ([English](../../hooks.md))

Claude Code ejecuta comandos de hook en momentos fijos de una tarea: se envía un mensaje, una herramienta está a punto de ejecutarse, una herramienta terminó, la tarea terminó, y así. aipager se engancha a todos ellos y los convierte en actividad en Telegram: la tarjeta de actividad, las solicitudes de permiso, las respuestas, los avisos y el registro de auditoría.

## <a id="how-it-is-wired"></a>Cómo se conecta

`aipager config` (o `aipager setup`) añade las entradas de aipager a `~/.claude/settings.json`, y aipager las comprueba en cada arranque:

- un comando `aipager-hook` para cada uno de los 16 eventos de abajo, con el matcher `*` en los eventos de herramienta (`PreToolUse`, `PostToolUse`, `PermissionRequest`);
- un tiempo límite de 5 segundos en `PreModelSwitch` y, desde `aipager config` y `aipager setup`, uno de 30 segundos en `PermissionRequest` (el propio hook espera como mucho 20 segundos, mira abajo); las demás entradas usan el valor por defecto de Claude Code;
- el comando `statusLine`, `aipager-statusline`.

`aipager uninstall` vuelve a quitar estas entradas (mira [comandos](commands.md#command-line-aipager-uninstall)).

Claude Code arranca un proceso nuevo de `aipager-hook` (`aipager.dtach.notify_hook:main`) para cada evento. Lee el JSON del evento por stdin, le añade dos campos (abajo) y se lo envía a aipager como un datagrama por un socket de Unix. El socket es, en este orden: `$AIPAGER_INSTANCE_DIR/aipager.sock` para una instancia de prueba aislada, `$AIPAGER_SOCKET_PATH`, `$XDG_RUNTIME_DIR/aipager.sock`, o `/tmp/aipager.sock`. El envío nunca espera: si aipager no está escuchando, el evento se pierde y el vigilante de sesiones se pone al día con los archivos de la sesión.

La mayoría de los eventos le cuestan a Claude Code unos 20 a 25 ms de arranque del proceso. Unos pocos también cargan el código de reglas de aipager: antes de ejecutar una herramienta (la comprobación de seguridad, mira [PreToolUse](#pretooluse)) unos 70 ms, al final de una tarea (para borrar la marca de tarea abierta) unos 55 ms, y `UserPromptSubmit` (que escribe las reglas de la tarea). Un `PermissionRequest` espera a propósito tu toque, hasta 20 segundos.

El hook limita su propia memoria a 1 GB. Un hook que llega al límite sale con un error (para `PreToolUse`, con el código de salida que Claude Code lee como rechazo) y le envía a aipager un datagrama `hook_memory_cap_hit`, que se convierte en un aviso en el chat, con un botón «🐞 Informar de esto» para el propietario de la instalación (mira [informes de problemas](problem-reports.md)). Pon `AIPAGER_DEBUG=1` en el entorno de la sesión para que el hook y la línea de estado impriman sus diagnósticos en stderr.

## <a id="what-the-daemon-receives"></a>Qué recibe aipager

El datagrama es el JSON de hook del propio Claude Code con los campos que añade aipager (algunos eventos llevan más, indicados en cada evento abajo):

| Campo | Significado |
|---|---|
| `hook_event_name` | El evento (campo de Claude Code; aipager reparte el trabajo según él). |
| `session_id`, `transcript_path`, `cwd`, ... | Los campos propios de Claude Code, tal como llegan. `session_id` es el ID de la conversación en Claude Code. |
| `session` | Lo añade aipager: su nombre para la sesión, `claude-<name>`, de la variable `CLAUDE_DTACH_SESSION` que aipager pone al arrancar una sesión. Los eventos de una sesión de Claude Code que aipager no arrancó no llevan nombre y aipager los ignora. |
| `sl_tokens` | Lo añade aipager, en todos los eventos menos `MessageDisplay`, cuando existe el archivo de la línea de estado de la sesión: `context_pct`, `total_output`, `total_input`, `current_output`, `lines_added` y `lines_removed`. |

Los eventos de herramienta también llevan `tool_name` y `tool_input`. Cada evento que ocurre dentro de un subagente lleva `agent_id` (y los eventos de subagente, `agent_type`); los del bucle principal no, y aipager usa eso para poner las herramientas de un agente en la fila de ese agente.

## <a id="event-reference"></a>Referencia de eventos

Los manejadores de aipager están en `aipager/dtach/hook_receiver.py`; el trabajo del propio hook está en `aipager/dtach/notify_hook.py`.

### <a id="sessionstart"></a>`SessionStart`

Una sesión arrancó, se reanudó o se compactó su conversación. Una sesión que aipager aún no sigue se registra. Después de compactar, la tarjeta dice `📦 name · Conversación compactada: 82% → 4%` (mira [PreCompact](#precompact--postcompact)). Para una conversación nueva, el hook también borra la marca de tarea abierta de la tarea anterior.

### <a id="sessionend"></a>`SessionEnd`

La sesión terminó. Se marca como GONE: sale de la barra de estado fijada y pasa a las sesiones terminadas, donde `/resume` puede recuperarla. `aipager session <name>` o `/new <name>` crea una nueva.

### <a id="userpromptsubmit"></a>`UserPromptSubmit`

Claude tomó un mensaje, escrito en la terminal o enviado desde Telegram.

El hook primero empareja el mensaje con las notas de permisos de la sesión (`/tmp/claude-notes-<session>/`, una por cada mensaje de Telegram que escribió aipager) y escribe las reglas de la tarea en `/tmp/claude-policy-<session>.json`, que lee la comprobación de [PreToolUse](#pretooluse) (mira [Modelo de seguridad → cómo se aplican las reglas en equipo](security.md#team-mode-enforcement)). Le dice a aipager qué mensajes tomó Claude (un datagrama `queue_pickup`) antes de reenviar el evento. Luego imprime contexto extra para Claude cuando lo hay: el estilo de respuesta elegido en `/settings` y, para una respuesta a un mensaje anterior, qué decía ese mensaje y cuándo. Un mensaje `<task-notification>` (Claude despertándose solo cuando termina un agente o un comando en segundo plano) es el mismo trabajo que continúa: no coincide con ninguna nota y conserva las reglas de la tarea.

aipager marca la sesión como trabajando, abre la tarjeta de actividad si no hay ninguna (un mensaje enviado desde Telegram ya tiene una) y convierte el 👀 de los mensajes que tomó Claude en 👍. Un mensaje enviado mientras hay una tarea en curso recibe su 👍 cuando Claude lo suma a esa tarea o empieza la siguiente con él.

### <a id="pretooluse"></a>`PreToolUse`

Una herramienta está a punto de ejecutarse. Aquí es donde se ejecuta la comprobación de seguridad de aipager, dentro del hook, antes de que Claude Code mire sus reglas de permisos:

- Una tarea que empezó en Telegram queda sujeta a las reglas del rol de quien la envió y al piso de seguridad (rutas protegidas, patrones de comandos), del que solo está exento el rol `owner`; una tarea que aipager no puede atribuir a nadie recibe el piso; un mensaje que escribiste tú en la terminal no tiene restricciones. Las reglas están en [uso en equipo](groups.md#how-rules-work) y en [Modelo de seguridad](security.md#team-mode-enforcement).
- Cuando una regla rechaza la ejecución, el hook imprime una decisión `deny`, Claude Code rechaza la herramienta sin preguntar, y se avisa a aipager (`safety_blocked`): la tarea se detiene con `🛑 x1 · Bloqueado por una regla de seguridad: ... (detenida)`, y todas las herramientas siguientes de esa tarea también se rechazan.
- Una llamada a `SendUserFile` (Claude envía archivos al chat) se comprueba para cada tarea, la del owner y un mensaje escrito en el terminal incluidos: el mínimo de envío, las comprobaciones de archivo y las reglas de la tarea ([seguridad](security.md#files-claude-sends)). Una llamada rechazada recibe un `deny` sin la marca «aipager safety policy»: Claude lee el motivo y te lo dice, la tarea sigue, y el hook le envía a aipager `send_files_refused` con cada archivo, para el registro de auditoría.
- Una comprobación que falla rechaza, salvo que las reglas de la sesión se puedan leer y den la exención del propietario.

Del lado de aipager, la ejecución se convierte en una fila de la tarjeta de actividad (la de un subagente va en la fila de ese agente), y un `Write` o `Edit` muestra una vista previa del diff cuando `/settings` → Vistas previas de diff está activado (por defecto está desactivado). Una llamada a `AskUserQuestion` se convierte en la pregunta con un botón por opción; una que hace un agente auxiliar de Claude Code mientras no hay ninguna tarea en curso no se muestra (no es Claude preguntándote a ti). La llamada `Agent` del propio Claude deja `subagent_type` y `description` para el agente que arranca, y una ejecución de un subagente es la última actividad de ese agente: las dos se ven en el estado debajo de una respuesta mientras el agente trabaja en segundo plano.

### <a id="permissionrequest"></a>`PermissionRequest`

Claude Code va a pedir permiso. La tarjeta de actividad se convierte en la solicitud, con el comando o la ruta reales debajo de la descripción del propio Claude (mira [comandos → solicitudes de permiso](commands.md#permission-prompts)): Permitir, Rechazar, Detener, y Permitir siempre solo cuando Claude Code ofrece una regla de verdad para recordar.

El hook responde la solicitud él mismo cuando puede. Abre un socket de respuesta de vida corta, envía su dirección con el evento y espera tu toque hasta 20 segundos. Un toque a tiempo vuelve como una decisión de `PermissionRequest` (permitir, permitir con la regla, o rechazar), así que Claude Code nunca muestra su propio diálogo. Si no llega ningún toque a tiempo, el hook le dice a aipager que la espera terminó (`permission_reply_timeout`) y no imprime nada; tampoco imprime nada cuando no puede llegar a aipager o algo falla en ese camino. Entonces Claude Code muestra su propio diálogo en la terminal, y un toque posterior lo responde escribiendo las teclas. El registro de auditoría (`~/.claude/aipager-audit.jsonl`) guarda por qué vía respondió cada toque: `"via": "hook_decision"` o `"via": "keystroke_fallback"`.

Un `PermissionRequest` que llega mientras otra solicitud está abierta también se muestra, como su propio mensaje bajo la tarjeta de actividad. El mismo diálogo visto de nuevo (el mismo solicitante, herramienta y llamada, o la misma entrada) se muestra una sola vez. Claude Code también ejecuta este hook para un `AskUserQuestion`; esa pregunta ya se muestra desde su `PreToolUse`, así que nunca se muestra una segunda vez. El diálogo de un agente en segundo plano aparece en la terminal solo después de que su hook dejó de esperar, debajo de cualquier diálogo ya abierto allí; por eso un toque escribe teclas solo en la solicitud que Claude Code muestra primero (ver [comandos → solicitudes de permiso](commands.md#permission-prompts)).

### <a id="posttooluse--posttoolusefailure"></a>`PostToolUse` / `PostToolUseFailure`

Una herramienta terminó, o falló. Su fila de la tarjeta de actividad queda fija (un fallo se marca en la fila), y se nota la respuesta a una solicitud abierta: un Permitir o Rechazar viejo para ella ya no escribe nada después. La sesión guarda las 200 filas de herramientas más recientes.

Un `Bash` que Claude Code mandó a segundo plano informa un `backgroundTaskId`. aipager lo sigue entonces como un comando en segundo plano (solo los del bucle principal: una llamada con `agent_id` es de ese subagente) hasta ver su final: un `<task-notification>` con un `<status>` (en el mensaje que despierta a Claude, o en la conversación guardada cuando llega a mitad de una tarea), un `TaskStop` o `KillShell` con su ID, el final de la sesión, o dos horas (`AIPAGER_BG_SHELL_MAX_TRACK`, en segundos, por defecto 7200). Mira [comandos](commands.md#idle-responses) para su fila en la tarjeta.

Una llamada `Agent` (o `Task`) hecha dentro de un subagente (su evento lleva `agent_id`) informa del ID del agente que arrancó. aipager recuerda qué agente arrancó a cuál, así puede distinguir un agente que paró para esperar a su propio agente de uno que terminó (ver `SubagentStart` / `SubagentStop` más abajo).

Tras un `SendUserFile` que se ejecutó, aipager copia enseguida sus archivos, una vez por llamada y solo durante una tarea que empezó con un prompt, a partir de la lista que mostró su `PreToolUse` (nunca del resultado de la herramienta), comprobando cada uno otra vez; las copias se envían después de la respuesta de la tarea, una vez encontrada la llamada en la conversación guardada que aipager anotó para la tarea, escrita durante esa tarea ([seguridad](security.md#files-claude-sends), [comandos](commands.md#files-claude-sends)). Una tarea que Claude Code empieza sin hook (un mensaje que toma a continuación, la respuesta de un hook de Stop) cuenta en cuanto la conversación guardada muestra que empezó.

### <a id="notification"></a>`Notification`

aipager lee el `notification_type` de Claude Code:

- `permission_prompt`: una señal más lenta y antigua de una solicitud de permiso. La muestra solo cuando no hay ninguna solicitud abierta: con una abierta, es el diálogo que `PermissionRequest` ya mostró.
- `idle_prompt` mientras espera una solicitud de permiso o una pregunta: la sesión sigue esperando, y el chat recibe `⬆️ jim · sigue esperando tu respuesta arriba`, una vez por espera.
- `idle_prompt` en otro caso: se trata como `Stop` (abajo). Recoge una tarea cuyo `Stop` se perdió; una respuesta ya entregada nunca se vuelve a enviar.

### <a id="stop"></a>`Stop`

La tarea terminó. La sesión queda lista, la tarjeta de actividad se fija como dice tu formato, y se envía la respuesta de Claude (desde el `last_assistant_message` del evento, o si no desde la conversación guardada). Después salen los mensajes que esperaban a que terminara la tarea. Los agentes y comandos en segundo plano que siguen en marcha mantienen el trabajo abierto: la tarjeta se queda como estado de ese trabajo y la respuesta termina con un estado en vivo de ese trabajo: qué hace cada agente y cuántos comandos en segundo plano de este trabajo siguen en marcha (mira [comandos → agentes que siguen trabajando](commands.md#agents-still-running-when-the-answer-goes-out)). El hook borra primero la marca de tarea abierta, salvo que Claude todavía tenga en cola un mensaje que se convierte de inmediato en la siguiente tarea.

### <a id="stopfailure"></a>`StopFailure`

La tarea terminó con un error de la API. La sesión queda lista y la respuesta, si la hay, se entrega como en `Stop`; un error conocido (servidores saturados, límite de velocidad, ...) se muestra en palabras sencillas.

### <a id="subagentstart--subagentstop"></a>`SubagentStart` / `SubagentStop`

Un subagente arrancó o terminó. Cada uno tiene su propia fila en la tarjeta de actividad, `🤖 <type> · <activity> · <elapsed>`, con sus propias herramientas plegadas debajo, y al terminar se fija como `✅ 🤖 <type> · N herramientas usadas · <elapsed>`. `SubagentStart` no dice qué agente lo lanzó: aipager lo empareja con la llamada a `Agent` (`PreToolUse`) de su tipo que está esperando. Si todas esas llamadas vienen de un mismo agente, el nuevo agente queda unido a ese agente enseguida (su fila en la tarjeta dice `🤖 pipeline-runner › ship-tester`, y en el estado bajo una respuesta aparece debajo de él); si vienen de agentes distintos, no se une nada hasta que el propio `PostToolUse` del agente que lo lanzó lo nombra. La descripción de una llamada solo se usa cuando hay exactamente una llamada de ese tipo esperando. Un subagente cuyo `SubagentStop` nunca llega se descarta cuando lleva 30 minutos sin dar noticias (`AIPAGER_SUBAGENT_SILENCE`, en segundos; ningún evento de ningún tipo con su ID). Un agente que trabaja envía eventos de herramientas cada pocos segundos, así que esto solo recoge uno cuyo final se perdió (un reinicio, un datagrama perdido); una sola herramienta que dure 30 minutos sin ningún evento con el ID de ese agente también lo recoge. `AIPAGER_SUBAGENT_TTL` se acepta como nombre antiguo del mismo ajuste. Llegan a menudo eventos `SubagentStop` sin tipo y con un ID desconocido, y se ignoran.

Un agente en segundo plano que para mientras sigue en marcha un agente que él arrancó también recibe un `SubagentStop`. aipager lo toma como una pausa, no como un final: el agente sigue en marcha (su fila, el trabajo, el estado bajo las respuestas, la barra fijada). Su siguiente `SubagentStart`, con el mismo ID, es su continuación y no añade una segunda fila; cualquier llamada a una herramienta hecha por él mismo también cuenta como continuación. Termina cuando para sin ningún agente propio en marcha. Si no ha continuado dos minutos después de que terminara el último de ellos, deja de contarse como en marcha, pero nunca se muestra terminado. Los agentes en marcha, también los que esperan, se conservan tras un reinicio de aipager.

### <a id="precompact--postcompact"></a>`PreCompact` / `PostCompact`

Claude está compactando su contexto (`trigger` es `auto` o `manual`). La tarjeta de actividad dice `🔄 name · Compactando la conversación` (un mensaje nuevo si no hay tarjeta abierta), para que la pausa no parezca una caída. `PostCompact` termina ese estado; la línea `📦 name · Conversación compactada: X% → Y%` viene del `SessionStart` que sigue, o de la línea de estado si ese se pierde. La línea de estado también da el aviso `⚠️ name · Contexto al 82% - pronto se compactará solo` (con un botón para compactar) cuando el contexto llega al 80%, una vez por conversación: compactar no lo repite, y una conversación nueva (tras `/clear`, por ejemplo) lo vuelve a recibir.

### <a id="messagedisplay"></a>`MessageDisplay`

El texto propio de Claude en el momento en que llega a la pantalla. La conversación guardada solo se escribe después de cada ronda de herramientas, así que esta es la única fuente al día de lo que está diciendo Claude, y lo que lleva sus comentarios a la tarjeta de actividad. El texto de un subagente (cualquier evento con su `agent_id`) nunca se toma por el de Claude: nunca llega a los comentarios de la tarjeta, a la Mini App ni al registro de la tarea, y pasa a ser la última actividad de ese agente en el estado debajo de una respuesta. El hook no añade datos de la línea de estado a este evento: Claude Code lo espera mientras dibuja, y ocurre varias veces por mensaje.

### <a id="premodelswitch"></a>`PreModelSwitch`

Claude Code 2.1.251 y posteriores lo envían antes de cambiar de modelo. Lleva `from_model`, `to_model`, `requested_model` y `source` (`command` para un `/model` escrito, o `picker` o `sdk`). Claude Code suele preguntar «Switch model?» antes de un cambio, porque la siguiente respuesta tiene que volver a leer toda la conversación. Un hook que responde `permissionDecision: "allow"` deja que el cambio siga sin esa pregunta.

`aipager-hook` responde `allow` solo para un cambio que escribió el propio aipager, desde el control de modelo de la Mini App o el teclado de modelos. Cualquier otro cambio no recibe respuesta, así que Claude Code pregunta como siempre. Eso incluye tu propio `/model` en la terminal, el selector y un cambio desde el SDK.

aipager reconoce sus propios cambios con un archivo de marca. Justo antes de escribir `/model <name>`, aipager escribe `aipager-modelswitch-<session>.json`:
- va en la carpeta del socket de control (normalmente `$XDG_RUNTIME_DIR`);
- se escribe de forma atómica, con permisos 0600;
- lleva el nombre del modelo y el ID de la sesión de Claude;
- caduca a los 30 segundos.

El hook responde `allow` solo cuando se cumple todo esto:
- la marca es un archivo tuyo, y no un enlace simbólico;
- no ha caducado;
- es para la misma sesión de aipager y el mismo ID de sesión de Claude;
- su modelo es igual a `requested_model` o `to_model`, sin distinguir mayúsculas;
- `source` es `command`.

Después el hook toma la marca con un cambio de nombre atómico, para que se use exactamente una vez. La comprobación lee un solo archivo local, sin socket y sin esperas. El evento no se reenvía a aipager.

Claude Code 2.1.101 y posteriores ignoran los eventos de hook que no conocen, así que las versiones más antiguas sin `PreModelSwitch` no se ven afectadas. Un caso todavía puede mostrar la pregunta: un cambio hacia o desde `opusplan` puede hacer que Claude Code le pregunte dos veces al hook, y solo la primera encuentra la marca, así que Claude Code pregunta «Switch model?» en la terminal.

## <a id="the-status-line"></a>La línea de estado

No es un evento de hook: Claude Code ejecuta el comando `statusLine`, `aipager-statusline` (`aipager.dtach.statusline_notify:main`), cada vez que vuelve a dibujar su línea de estado. El comando:

- escribe el JSON de estado de Claude Code en `/tmp/claude-status-<session>.json` (lo leen el hook para `sl_tokens`, `aipager status` y aipager);
- le envía a aipager un datagrama `statusline` con el modelo, el % de contexto, el costo, las cantidades de tokens y las líneas añadidas y quitadas, que alimentan la tarjeta de actividad, `/status`, la Mini App, la comprobación de que un cambio de modelo se hizo, y el aviso de contexto;
- imprime la línea de estado de la terminal: `[name] Opus 5.5 | 42% ctx | $1.23`.

## <a id="aipagers-own-datagrams"></a>Los datagramas propios de aipager

Además de los eventos de hook, los ayudantes le envían a aipager algunos mensajes propios por el mismo socket:

| Mensaje | Lo envía | Cuándo |
|---|---|---|
| `queue_pickup` | hook | En `UserPromptSubmit`: qué mensajes de Telegram tomó Claude. |
| `safety_blocked` | hook | En `PreToolUse`, cuando la comprobación de seguridad rechazó la ejecución. |
| `send_files_refused` | hook | En `PreToolUse`, cuando se rechazaron archivos que Claude quería enviar: cada archivo y el motivo, para el registro de auditoría. |
| `permission_reply_timeout` | hook | La espera de un `PermissionRequest` terminó sin ningún toque. |
| `statusline` | línea de estado | En cada redibujo de la línea de estado. |
| `hook_memory_cap_hit` | hook, línea de estado | El proceso llegó a su límite de memoria. |
| `report_error` | hook, comandos `aipager` | Tuvo un error: solo el tipo de error y su lugar en el código de aipager, nunca el contenido del evento, para los [informes de problemas](problem-reports.md). |

## <a id="the-permission-flow"></a>El camino de una solicitud de permiso

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

Si no llega ningún toque en 20 segundos, el hook no imprime nada y Claude Code muestra su propio diálogo; un toque posterior escribe la respuesta en ese diálogo a través de la terminal de la sesión (`dtach -p`). En los dos casos el toque queda en el registro de auditoría y se publica debajo de la tarjeta (`✅ jim · Permitido · @alice · Bash: ls -la /tmp`).

## <a id="see-also"></a>Ver también

- [Arquitectura](architecture.md): dónde encaja el receptor de hooks.
- [Comandos del bot → solicitudes de permiso](commands.md#permission-prompts): el lado que ves tú.
- [Modelo de seguridad](security.md): qué cubre y qué no cubre la comprobación de seguridad.
