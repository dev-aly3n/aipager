# Comandos e interfaz del bot

<!-- i18n-source: docs/commands.md @ 76d125d -->

> Esta página es una traducción de la página en inglés; si no coinciden, la versión en inglés es la correcta. ([English](../../commands.md))

Cómo manejas aipager desde Telegram. Cuatro vías de entrada: los comandos de barra, los botones del teclado, el panel de la Mini App y los mensajes libres (texto, archivos, audio).

El bot solo acepta entradas de los chats configurados; mira [Modelo de seguridad](security.md) para el límite de confianza.

## <a id="slash-commands"></a>Comandos de barra

Se registran en `aipager/bot/lifecycle.py`. El menú de comandos de Telegram los muestra, y se actualiza al arrancar aipager y con cada cambio de sesiones.

| Comando | Argumentos | Qué hace |
|---|---|---|
| `/start` | - | La pantalla de inicio: las sesiones de este chat, adónde va un mensaje normal (`✍️ Los mensajes van a x1.`), y `🆕 Nueva sesión`, `⚙️ Ajustes` y, cuando este chat tiene una sesión terminada para recuperar, `↩️ Continuar`. También muestra el teclado fijo. |
| `/help` | - | Una guía corta por tareas (empezar, hablar, controlar, gestionar, ajustes, y para admins `/update`), con el consejo de que tocar un comando lo envía al momento: mantenlo pulsado (móvil) o pulsa Tab (escritorio) para añadir texto antes. Para el propietario de la instalación (la persona del propio chat privado de la instalación con el bot) también tiene un botón `🐞 Informar de un problema`: abre en ese chat privado una vista previa del informe exacto, con Enviar, Añadir una nota y Cancelar. No se envía nada hasta que tocas Enviar ([qué contiene un informe](problem-reports.md)). |
| `/app` | - | Abre el panel de la Mini App (sesiones, visor de diff, ajustes). |
| `/status` | - | Las sesiones de este chat como una lista con la que puedes actuar. Cada una dice su estado con palabras (`⚙️ trabajando 3:12 · Bash: run tests`, `⏳ te necesita · Bash: make deploy`, `💤 lista`), con `✍️` en la que recibe los mensajes normales, y una segunda línea con el modelo, el contexto, el costo, lo que hay en cola (`cola 3 (1 en cola, 2 notas)`), y los agentes y comandos en segundo plano en marcha. Cada una tiene una fila de botones: `✍️ x1` envía ahí tus siguientes mensajes, `⏹ Detener` mientras trabaja, `Responder` mientras te espera, y `⋮` para lo demás. Abajo: `🆕 Nueva`, y `⚫ Terminadas (n)` para las sesiones que terminaron (continuar, eliminar, o borrarlas todas de la lista; siguen en `/resume`). |
| `/stop [label]` | opcional | Interrumpe la tarea actual de una sesión. Sin nombre, detiene la única sesión de este chat que está trabajando (aunque tus mensajes vayan a otra; una sesión cuyo agente o comando en segundo plano sigue en marcha cuenta como trabajando), pregunta cuál con una lista cuando hay varias, y dice `Ahora no hay nada en marcha.` cuando no hay ninguna. `/stop x1` (o `/x1 stop`) detiene x1 (o responde `x1 no está haciendo nada ahora.` cuando no está trabajando). También descarta los mensajes en cola y dice cuántos descartó. |
| `/now` | - | Envía ahora mismo los mensajes que Claude tiene en la cola de la sesión activa, en lugar de después del paso actual (las propias teclas de enviar ya de Claude Code, Ctrl+X Ctrl+S). Funciona en cualquier momento, también mientras el chat está silenciado o en modo mínimo; responde `No hay nada esperando en la cola` cuando Claude no tiene nada. Mira [Enviar ya un mensaje en cola](#send-a-queued-message-now). |
| `/new [name] [first message]` | todo opcional | Crea una sesión. `/new x1` la crea al momento, y `/new x1 fix the tests` también envía «fix the tests» como su primer mensaje. Enviado solo (por ejemplo tocado en el menú), `/new` pregunta el nombre, y el siguiente mensaje que envíes se usa exactamente como el argumento, así que `/new` y luego `x1 fix the tests` da el mismo resultado. En un grupo, responde a la tarjeta con el nombre (o menciona al bot: `@aipagerbot x1`), porque un mensaje normal ahí no le llega al bot. Solo ese siguiente mensaje: si antes haces otra cosa (una foto, otro comando, una respuesta a otro mensaje, una plantilla del teclado, otro botón), se trata como siempre y la tarjeta se cierra; `/status`, `/settings` y los menús del teclado la dejan abierta. La tarjeta que pregunta también muestra el modo, el modelo y la carpeta que tendrá la sesión, con botones para cambiarlos antes. Todas las vías terminan en la misma tarjeta «x1 está lista»: lo que recibió la sesión, que tu siguiente mensaje va a ella, y botones de un toque para pasar a otro modo o cambiar el modelo. Las sesiones nuevas arrancan en Modo Automático para todos; cambia lo que usa /new en `/settings` → Sesiones nuevas. Un `!` antes del nombre arranca la sesión en Modo Acceso total, para un admin (los roles `owner` y `admin`) o una persona en su propio chat privado con el bot cuyo rol le deja enviar mensajes y responder a Claude (`user`, no `read_only`); para cualquier otra persona arranca en Modo Automático y la tarjeta dice por qué. Un nombre que ya está en uso ofrece `↩️ Ir a ella` (cuando esa sesión está en marcha) o `♻️ Continuar` (cuando terminó), `🆕 Reemplazar por una nueva` y `↩️ Cancelar`, solo para quien envió `/new`. La tarjeta que pide un nombre caduca a los 10 minutos sin usarse (`⏱ Esta tarjeta de nueva sesión caducó. Envía /new otra vez.`). Un miembro cuyo rol no tiene `can_manage` (`user`, `read_only`) solo crea sesiones dentro de la [carpeta raíz](groups.md#root-folders) del chat: la carpeta de la tarjeta es la carpeta raíz, la página de Carpeta muestra esa y las carpetas que están directamente dentro (sin la fila «Carpeta por defecto»), Nueva carpeta crea dentro de la elegida, e Ir a ella, Continuar y Reemplazar sobre una sesión que trabaja fuera de la carpeta raíz se rechazan («Esta sesión trabaja fuera de tu carpeta ~/..., así que tu rol no puede continuarla, reiniciarla ni cambiarle el modo. Pídeselo a un admin.»). |
| `/resume [label]` | opcional | Continúa por su nombre una sesión que ya terminó, o abre una lista para elegir. Una sesión vuelve en el modo en que estaba. Una sesión que estaba en Modo Acceso total solo vuelve en Acceso total para un admin o para una persona en su propio chat privado; para cualquier otra persona vuelve en Modo Automático y la respuesta lo dice (`♻️ x1 vuelve a estar en marcha en Modo Automático: el Modo Acceso total es solo para admins.`). Una sesión que un aipager anterior guardó en su antiguo Auto (que se ejecutaba sin comprobaciones) vuelve en Modo Acceso total, y una guardada en Ask vuelve en Modo Preguntar. Una sesión terminada sigue en la lista `GONE_SESSION_MAX_AGE_DAYS` días (por defecto 14) y luego sale de la lista de sesiones; la conversación guardada de Claude no se toca. Un miembro cuyo rol no tiene `can_manage` no puede continuar una sesión cuya carpeta está fuera de la [carpeta raíz](groups.md#root-folders) del chat (los propietarios y los admins sí); lo mismo vale para Reiniciar y cualquier cambio de modo, incluido el cambio sin reinicio entre Preguntar y Automático. |
| `/kill [label]` | opcional | Termina una sesión, siempre tras una confirmación: `⏹ ¿Terminar x1? Claude se detiene y la sesión termina.` con `[⏹ Terminar] [Cancelar]`. Sin nombre, va a la confirmación cuando este chat tiene una sola sesión en marcha, y si no, a una lista de las sesiones en marcha de este chat. También está en `/status` → `⋮` → `⏹ Terminar sesión`. |
| `/restart [label]` | opcional | Reinicia una sesión tras una confirmación: Claude se detiene y vuelve a arrancar, conservando su conversación, en el modo con el que aipager la arrancó por última vez o al que la cambió. Una sesión arrancada en la terminal con `aipager session` vuelve en Modo Preguntar salvo que se cambiara desde Telegram desde entonces (aipager no sabe con qué modo la arrancó la terminal, y nunca supone uno más abierto); lo mismo vale para continuarla desde Telegram. |
| `/rename [label] [new]` | opcional | Le da un nombre nuevo a una sesión (también a las terminadas). `/rename x1 newname` cambia el nombre al momento. Con menos palabras, pregunta: directamente el nombre nuevo cuando el comando solo puede referirse a una sesión, y si no, una lista (una sola palabra después de `/rename` no elige la sesión, porque podría ser el nombre viejo o el nuevo). En un grupo, responde a la pregunta con el nombre. |
| `/delete [label]` | opcional | Quita de la lista una sesión terminada, tras una confirmación. |
| `/diff [label]` | opcional | Muestra el git diff de la carpeta de trabajo de la sesión. Sin nombre, muestra la sesión a la que van tus mensajes. |
| `/clearqueue` | - | Descarta todos los mensajes que todavía no tomó la sesión activa (los que retiene aipager y los que ya están en la cola dentro de Claude), sin interrumpir la tarea en curso. Responde con cuántos borró. |
| `/mode [label] [ask\|auto\|full]` | opcional | Muestra el modo de permisos de una sesión con un botón para pasar a cada uno de los otros: `✍️ x1 está en 🤖 Modo Automático.` `[💬 Cambiar a Modo Preguntar]` `[🔓 Cambiar a Modo Acceso total]`. Hay tres modos. **💬 Modo Preguntar**: Claude pregunta antes de cada acción. **🤖 Modo Automático** (el de por defecto): la comprobación de seguridad propia de Claude Code ejecuta las acciones seguras y detiene las riesgosas para que decidas tú. **🔓 Modo Acceso total**: todo se ejecuta sin ninguna comprobación (`--dangerously-skip-permissions`); solo un admin, o una persona en su propio chat privado con el bot cuyo rol le deja enviar mensajes y responder a Claude (`user`, no `read_only`), puede elegirlo. Automático y Acceso total solo cambian las propias solicitudes de permiso de Claude Code: el hook de aipager sigue aplicando las reglas del rol del miembro. `/mode ask`, `/mode auto`, `/mode full` o `/mode x1 ask` cambian al momento; la palabra del propio modo en el idioma del chat también funciona, sin la palabra «modo» (`/mode x1 Automático` para 🤖 Modo Automático, `/mode x1 Total` para 🔓 Modo Acceso total). Cuando una sesión en marcha de este chat tiene exactamente ese nombre (una sesión llamada `demander`, por ejemplo), la palabra nombra la sesión: `/mode demander` muestra el modo de esa sesión, y `/mode x1 demander` sigue cambiando x1. `ask`, `auto` y `full` siempre significan el modo, en cualquier orden (`/mode x1 ask` o `/mode ask x1`); solo cuando las dos palabras son palabras de modo y una sola de ellas es el nombre de una sesión en marcha, esa nombra la sesión, así que con una sesión llamada `full`, `/mode full ask` y `/mode ask full` la pasan las dos a Modo Preguntar, mientras que `/mode full` solo sigue siendo el modo. Pasar entre Preguntar y Automático pasa sin reiniciar y en cualquier momento (también mientras Claude trabaja): aipager pulsa Shift+Tab en la sesión como lo harías tú, la tarjeta dice `⚙️ Cambiando x1 a 🤖 Modo Automático sin reiniciar.` hasta que Claude Code informa el modo nuevo en su siguiente paso, y luego muestra el resultado. Si el Modo Automático no está disponible para la sesión (por su modelo o su plan), la tarjeta lo dice y la sesión se queda en Modo Preguntar. No mientras la sesión muestra una pregunta o una solicitud de permiso: respóndela primero. Pasar a Acceso total necesita un admin (o una persona en su propio chat privado) y una confirmación, y reinicia la sesión (la conversación se conserva); salir del Acceso total también la reinicia, al momento. Una sesión arrancada en Acceso total se reinicia con cada cambio, también después de que la pasaran a Preguntar o Automático en la terminal, y lo mismo una que informó Acceso total desde que arrancó (una sesión de terminal arrancada con `--dangerously-skip-permissions`): Shift+Tab ahí podría devolverla por el ciclo al Acceso total. En una sesión que está trabajando, un reinicio ofrece `Detener la tarea y cambiar` / `Ahora no`. Un miembro cuyo rol no tiene `can_manage` no puede cambiar una sesión cuya carpeta está fuera de la [carpeta raíz](groups.md#root-folders) del chat, ni sin reiniciar ni reiniciando. Tocar un cambio cambia la propia tarjeta en lugar de enviar un segundo mensaje. `/perms` es el nombre antiguo y sigue funcionando igual. |
| `/settings` | - | Cómo se ven y responden las sesiones de este chat: el idioma que usa aipager, cómo se ve la respuesta, las vistas previas de diff, la actualización de la tarjeta en tareas largas, el formato, la longitud de las respuestas, el nivel de vocabulario; con qué arranca una sesión `/new`; los ajustes de cada sesión; y, en el chat privado del propietario, las propuestas de informes de problemas. Mira [Ajustes](#settings). En un grupo cualquiera puede mirar, y cambiar un ajuste necesita un admin. |
| `/language` | - | El idioma que usa aipager para sus propios mensajes y botones en este chat (la misma lista que la primera fila de `/settings`). Cada idioma aparece con su propio nombre: English, luego Español, Русский (ruso), Português (Brasil), 简体中文 (chino simplificado), Français, Deutsch (alemán), 한국어 (coreano), العربية (árabe), Bahasa Indonesia, Українська (ucraniano), Italiano, Türkçe (turco), فارسی (persa) y Tiếng Việt (vietnamita). Un chat privado que empieza después de esta versión sigue el idioma de tu app de Telegram; uno que ya existía sigue en inglés hasta que elijas (mira [Idiomas](#languages)). En español, portugués, francés, italiano e indonesio, las palabras del teclado de abajo siguen siendo `status`, `stop`, `new` y `kill`, igual que sus comandos. El alemán y el turco usan `Status`, `Stop`, `Neu…` y `Beenden…`, y `Özet`, `Stop`, `Oluştur` y `Sonlandır`; las palabras en inglés siguen funcionando si se escriben. En persa y árabe, el texto de aipager se lee de derecha a izquierda con números persas o árabes; los nombres de las sesiones, los comandos, los nombres de los modelos y el código se quedan como están. La lista dice qué cambia: solo los textos de aipager, aquí y en la Mini App; tus sesiones de Claude no cambian, y Claude responde en el idioma en que le escribes. En un grupo cualquiera puede mirar, y cambiarlo necesita un admin. `language` es un nombre de sesión reservado. |
| `/whoami` | - | Muestra quién eres aquí: en el uso en equipo tu etiqueta, tu rol en este chat y las reglas que te da. |
| `/update` | - | Solo admins (los roles `owner` y `admin`, o un rol con `can_manage`). Busca versiones nuevas de aipager y de Claude Code, y luego actualiza con un solo botón lo que tenga una. Mira [Actualizar](#update). |

El menú `/` muestra primero tus sesiones (`/x1 · Hablar con x1`) y luego los comandos frecuentes: `new`, `status`, `stop`, `now`, `resume`, `mode`, `settings`, `language`, `help` (y `app` con la Mini App activada, solo en un chat privado). `kill`, `restart`, `rename`, `delete`, `diff`, `clearqueue`, `perms`, `whoami` y `update` no están en el menú pero funcionan si se escriben; las acciones sobre sesiones también están en `/status` → `⋮`.

**Los comandos que actúan sobre una sesión** (`/stop`, `/mode`, `/kill`, `/restart`, `/rename`, `/delete`, `/diff`) siguen una regla:

- Con un nombre (`/restart x1`) actúan sobre esa sesión de este chat (`/rename` necesita para eso el nombre viejo y el nuevo, mira arriba).
- Sin nombre, actúan sobre la única sesión a la que puede referirse el comando (la única trabajando para `/stop`, la única en marcha para `/kill` y `/restart`, la única terminada para `/delete`), pasando por la confirmación de siempre donde la hay.
- Si no, muestran una lista solo con las sesiones de este chat para las que el comando tiene sentido: primero la que recibe tus mensajes, marcada con `✍️`, luego el resto por nombre, y `✖️ Cancelar`. Elegir una lleva a la misma tarjeta que escribir su nombre.
- En un grupo, sin nombre, solo actúan sobre tu propia sesión actual, cuando el comando tiene sentido para ella; si no, siempre muestran la lista, aunque haya una sola sesión, porque puede ser de otro miembro (mira [uso en equipo](groups.md#talking-to-a-session-in-a-group)).

`/mode` y `/diff` miran primero la sesión a la que van tus mensajes.

### <a id="settings"></a>Ajustes

`/settings` (y la pestaña de Ajustes de la Mini App, que muestra las mismas opciones) decide cómo se ven y responden las sesiones de este chat. Cada chat tiene los suyos; en un grupo cualquiera puede mirar, y cambiar un ajuste necesita un admin. En tu propio chat privado los cambias tú, tengas el rol que tengas, en Telegram y en la Mini App. Una fila marcada con ✅ tiene un valor distinto del de por defecto (Cómo se ve la respuesta siempre muestra su opción).

| Ajuste | Opciones (la de por defecto primero) | Qué hace |
|---|---|---|
| 🌐 Idioma | Automático (tu idioma de Telegram), en un chat privado; English | El idioma de los mensajes y botones propios de aipager en este chat y en la Mini App, con su propio nombre (la misma lista que `/language`). No cambia tus sesiones de Claude: Claude responde en el idioma en que le escribes. Se ofrecen English, Español, Русский (ruso), Português (Brasil), 简体中文 (chino simplificado), Français, Deutsch (alemán), 한국어 (coreano), العربية (árabe), Bahasa Indonesia, Українська (ucraniano), Italiano, Türkçe (turco), فارسی (persa) y Tiếng Việt (vietnamita); Automático sigue el idioma de tu app de Telegram (mira [Idiomas](#languages)). |
| 🖼 Cómo se ve la respuesta | Tarjeta + respuesta aparte; Respuesta dentro de la tarjeta; Solo la respuesta (sin tarjeta) | Cómo aparece una tarea terminada. Tarjeta + respuesta aparte deja la tarjeta encima de la respuesta como registro de cómo se llegó a ella; Respuesta dentro de la tarjeta pone la respuesta en la tarjeta; Solo la respuesta quita la tarjeta y deja la respuesta sola. La de por defecto es Tarjeta + respuesta aparte (`KEEP_FINISHED_CARD=0` la cambia por Solo la respuesta en los chats que nunca eligieron). Mira [respuestas](#idle-responses). |
| 📝 Vistas previas de diff | Desactivado; Activado | Activado publica cada `Write`/`Edit` como su propio mensaje de diff debajo de la tarjeta de actividad. Desactivado deja una tarjeta y una respuesta; la tarjeta sigue mostrando cada edición, y la Mini App tiene un visor de diff. |
| ⏱ Actualizar tarjeta en tareas largas | Activado; Desactivado | Activado, la tarjeta de actividad se actualiza cada 10 s pasados 2 minutos de una tarea y cada 30 s pasados 10 (nunca más despacio, por larga que sea la tarea), y su reloj avanza entonces de 30 en 30 s; Desactivado mantiene el ritmo de los primeros minutos durante toda la tarea (mira [solución de problemas](troubleshooting.md#a-long-turns-card-refreshes-less-often)). |
| ✏️ Formato simple | Desactivado; Activado | Activado le pide a Claude solo texto llano y listas con guiones: sin tablas, bloques de código, títulos ni negritas. |
| 📏 Longitud de respuesta | Sin ninguna regla; Extra corta; Corta; Media; Larga | Le pide a Claude respuestas de esa longitud. |
| 🎓 Nivel de vocabulario | Sin ninguna regla; Palabras sencillas; Normal; Avanzado | Le pide a Claude ese vocabulario. |
| 🆕 Sesiones nuevas | Permisos (el modo), Modelo, Carpeta | Con qué arranca `/new` una sesión cuando no eliges: Modo Preguntar, Automático (el de por defecto) o Acceso total, un modelo de la lista de Modelos o el de por defecto de Claude Code, y una carpeta. Solo un admin, o una persona en su propio chat privado con el bot, puede elegir aquí Acceso total (la pantalla de un chat privado no se lo muestra a nadie más), y nunca se le aplica a alguien que no puede usarlo: sus sesiones arrancan en Modo Automático. |
| 👤 Ajustes de cada sesión | una sesión, y luego cualquier ajuste de arriba menos Sesiones nuevas | Ajustes propios de una sesión (también en `/status` → `⋮` → `👤 Preferencias`); ⭐ marca un ajuste que esa sesión cambia, y «↩️ Usar el valor por defecto del chat» quita el cambio. La página de la sesión en la Mini App pone los mismos ajustes. |
| 🐞 Informes de problemas | Preguntarme; Desactivado | Solo en el chat privado del propietario de la instalación. Preguntarme deja que aipager proponga de vez en cuando enviar un informe de un error interno que tuvo más de una vez (como mucho una vez cada 3 días); Desactivado nunca lo propone (el botón Informar de un problema de `/help` sigue funcionando). Dos propuestas rechazadas seguidas apagan las propuestas, y entonces esta fila lo dice, con un botón para volver a activarlas. Mira [informes de problemas](problem-reports.md#when-aipager-offers-a-report). |

La longitud de las respuestas, el nivel de vocabulario y el formato simple le llegan a Claude como una instrucción corta que se añade a cada mensaje enviado desde Telegram (lo hace el hook; mira [hooks](hooks.md#userpromptsubmit)), usando los ajustes propios de la sesión cuando los tiene.

Sea cual sea la forma, cada tarjeta de actividad termina con la línea de estado de su sesión (`⏳`/`✅ name · …`) y cada respuesta empieza con su línea de resultado (`💬 name`, más `· Terminado (…)` cuando no queda ninguna tarjeta terminada para mostrar las estadísticas); la forma Respuesta dentro de la tarjeta pone las dos una encima de otra, cada línea en su propia sección. En la forma de tarjeta, la respuesta llega a propósito un momento después de la tarjeta terminada, para que se vea a la tarjeta decir Terminado antes de que la respuesta llegue debajo; ajusta o apaga ese margen con `FINISH_CARD_GRACE_SECONDS` (en segundos, por defecto 0,8; 0 envía las dos a la vez). La forma decide la tarjeta de cada tarea, se hayan ejecutado herramientas o no.

### <a id="per-session-dynamic-commands"></a>Comandos propios de cada sesión

Un comando por cada sesión en marcha, que se registra a partir de su etiqueta:

| Forma | Qué hace |
|---|---|
| `/<label>` | Hace que `<label>` sea la sesión a la que van tus mensajes. La respuesta lo dice con su estado y su modo (`✍️ Ahora hablas con x1 · 💤 lista · 🤖 Modo Automático`, y luego «Envía un mensaje y llegará a x1.»), ofrece **Responder** cuando te está esperando, y **⋮ Más** para su menú. Las estadísticas completas están en `/status`. En un grupo dice en su lugar «Responde a un mensaje de x1 (o menciona a @aipagerbot) para hablar con ella.», porque un mensaje normal ahí no le llega al bot (mira [uso en equipo](groups.md#talking-to-a-session-in-a-group)). `/x1@aipagerbot`, que Telegram envía cuando eliges `/x1` en el menú de un grupo, funciona igual. |
| `/<label> <prompt>` | Envía `<prompt>` directamente a esa sesión. Esa sesión pasa también a ser la que recibe tus siguientes mensajes normales. |
| `/<label> stop` | Interrumpe la tarea actual de esa sesión. |

Los resultados de `/status` salen de los mismos datos que muestra `aipager status` en la línea de comandos; para la lista de sesiones en sí no hace falta ir y volver a Telegram.

En la computadora, `aipager status` y `aipager session ls` le dan a cada sesión un estado: `IDLE`, `BUSY`, `WAITING` (te espera un permiso o una pregunta, con qué es cuando se sabe, por ejemplo `permission: Bash: make deploy`) o `GONE`. Con `--json`, cada sesión también tiene `waiting_kind` (`permission` o `question`) y `waiting_summary`, los dos `null` salvo que esté esperando.

## <a id="languages"></a>Idiomas

aipager habla English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی y Tiếng Việt, cada uno en la lista con su propio nombre.

- **Solo cambian los textos de aipager.** El idioma es para los mensajes, los botones, las tarjetas y la Mini App de aipager. Tus sesiones de Claude no cambian, y Claude responde en el idioma en que le escribes. Los nombres de las sesiones, los comandos, los nombres de los modelos y el código se quedan como están.
- **`/language`** (o la primera fila de `/settings`) elige el idioma del chat en el que estás. En un chat privado también es tu propio idioma: lo usan los avisos cortos que ves en un grupo y tu Mini App.
- **Automático** (solo en chats privados) sigue el idioma de tu app de Telegram, y lo vuelve a seguir si lo cambias. Un idioma que aipager no tiene se ve en inglés.
- **Chats nuevos y existentes.** Un chat privado que empieza después de esta versión sigue el idioma de tu app de Telegram hasta que elijas uno, y su primer mensaje en un idioma que no sea el inglés va seguido de una línea corta que lo dice y explica cómo cambiarlo. Un chat que ya existía sigue en inglés. Si tu app de Telegram está en un idioma que habla aipager, recibes una tarjeta que lo ofrece, con un botón `Seguir en inglés`; llega una sola vez, respondas lo que respondas.
- **Los grupos** tienen un idioma, que pone un admin en `/language`; hasta entonces están en inglés. El aviso corto después de un toque está en el idioma de quien toca: lo que eligió en su chat privado con el bot, si no el idioma de su app de Telegram para alguien que llegó después de esta versión, y si no el idioma del grupo. Mira [uso en equipo](groups.md).
- **De derecha a izquierda.** En persa y árabe, el texto de aipager se lee de derecha a izquierda con números persas o árabes.
- **Palabras de modo.** `/mode` acepta la palabra del modo en tu idioma (`/mode demander`), y `ask`, `auto` y `full` siempre funcionan. Si una sesión en marcha tiene exactamente ese nombre, la palabra nombra la sesión en su lugar (mira `/mode` arriba).
- **Descripción del bot.** Al arrancar, aipager escribe la descripción del bot (el texto de su pantalla de chat vacía) y la descripción corta en cada uno de estos idiomas, para las personas cuyo Telegram está en ese idioma. Telegram muestra ese texto en lugar del que escribiste en BotFather, así que aipager no pone ninguno mientras tengas el tuyo (la descripción y la descripción corta se comprueban por separado). Nunca sobrescribe un texto tuyo y nunca cambia el texto en inglés o el de por defecto. Cuando su texto actual ya está puesto en todos los idiomas, un arranque no le pregunta nada a Telegram. Si no, primero lee tu texto de BotFather (una lectura para la descripción y otra para la descripción corta): si tienes uno, se para ahí, así que con tu propio texto cada arranque cuesta esas lecturas y nada más. Si no tienes ninguno, lee cada idioma que todavía no tiene su texto actual, así que un idioma en el que escribiste tu propio texto se vuelve a leer en cada arranque. Si escribes una descripción en BotFather después de que aipager pusiera la suya, los lectores de estos idiomas siguen viendo la de aipager (mira [solución de problemas](troubleshooting.md#the-bot-description-is-not-the-one-i-wrote)).

## <a id="the-mini-app"></a>La Mini App

`/app` (y el botón de menú de Telegram) abre un panel que sirve el propio aipager. Está activada por defecto; gestiónala con `aipager miniapp enable|disable|status`. Cada petición se comprueba con la firma `initData` de Telegram (mira [Modelo de seguridad → el túnel de la Mini App](security.md#mini-app-tunnel)). La página no carga nada de ningún otro sitio: ni fuentes, ni imágenes, ni scripts de otras webs.

- **Chats.** La app muestra un chat a la vez. Si estás en más de un chat (tu chat privado y un grupo, por ejemplo), una fila con los nombres de los chats arriba te deja pasar de uno a otro; la app se abre en tu propio chat privado y recuerda tu última elección en ese teléfono. Todo lo de abajo es del chat elegido, con tu rol en él. Con un solo chat la fila no se muestra. Mira [uso en equipo](groups.md).
- **Sesiones.** Una frase resume el chat, por ejemplo «1 te necesita, 2 trabajando, 3 listas», con lo gastado debajo. Cada sesión en marcha es un recuadro con un anillo de contexto, su carpeta, su modelo y su costo; las que trabajan brillan con el color principal. Las sesiones terminadas descansan en un estante plegable de **Terminadas**. El botón **+** crea una sesión nueva.
- **Te necesita.** Una sesión que espera una solicitud de permiso o una pregunta se coloca en una bandeja ámbar arriba, mostrando qué pide. **Responder en el chat** vuelve a enviar esa solicitud, con sus botones, al final del chat, igual que el botón **Responder** de la barra fijada, y la respondes ahí. Si era la única sesión esperando, la app se cierra para que tengas la solicitud delante; si no, se queda abierta y dice «Enviada al chat. Respóndela allí.». Necesita el permiso de enviar mensajes a la sesión, y una copia tocada después de que la solicitud se respondiera o se reemplazara dice «ya respondida».
- **La página de una sesión.** La cabecera muestra el anillo de contexto, el estado («Trabajando (3:12)», «Te necesita (permiso)», «Lista, última actividad hace 5 min», «Terminada») y un **Detener** de un toque (mientras trabaja o espera) o **Continuar** (cuando terminó). Debajo: la solicitud que espera con **Responder en el chat**, las últimas herramientas como una tira (tócala para ver la línea de tiempo completa), la última respuesta, los datos, el control de modelo, los ajustes propios de la sesión, y los archivos cambiados y la línea de tiempo. El menú ⋮ tiene todas las acciones, con las mismas confirmaciones de antes.
- **Nueva sesión.** Una tarjeta guiada: nombre, luego dónde (con chips de **Recientes** para las carpetas que usan tus sesiones, las más nuevas primero), luego el modelo (con chips de **Sugeridos**), luego **Más opciones** para el modo de permisos y el estilo de respuesta. El propio botón de Telegram abajo dice **Crear sesión**. Para un miembro cuyo rol no tiene `can_manage`, «dónde» solo ofrece la [carpeta raíz](groups.md#root-folders) del chat y las carpetas que están directamente dentro (la carpeta raíz es la de por defecto, y Nueva carpeta crea dentro de la elegida); una carpeta fuera de ella se rechaza con el motivo, y Continuar, Reiniciar y el cambio de modo de una sesión que trabaja fuera de ella responden «Esta sesión trabaja fuera de tu carpeta ~/..., así que tu rol no puede continuarla, reiniciarla ni cambiarle el modo. Pídeselo a un admin.»
- **Ajustes.** Primero **Idioma de la app**: tu propio idioma para los textos de aipager en la app y en tu chat privado (inglés, español, ruso, portugués (Brasil), chino (simplificado), francés, alemán, coreano, árabe, indonesio, ucraniano, italiano, turco, persa o vietnamita; Automático sigue el idioma de tu app de Telegram, mira [Idiomas](#languages)). Cualquiera puede cambiar el suyo, nunca se publica en un chat, y no cambia tus sesiones de Claude. Luego los ajustes del chat (en un grupo solo un admin puede cambiarlos; en tu propio chat privado puedes tú, tengas el rol que tengas, igual que `/settings` ahí) y, para un admin, **Actualizaciones**, que es lo mismo que [`/update`](#update). Para el propietario en su chat privado, **Informar de un problema** abre su propia página: escribe una nota, revisa el informe, envíalo y ve su referencia sin salir de la app (mira [informes de problemas](problem-reports.md#how-to-send-one)).

La app sigue el tema claro u oscuro de Telegram cuando lo cambias, y detiene sus animaciones suaves cuando tu teléfono pide menos movimiento. Todo lo de la Mini App también se puede hacer desde el chat: el menú ⋮ del panel de una sesión tiene las mismas acciones.

### <a id="switching-a-running-sessions-model"></a>Cambiar el modelo de una sesión en marcha

La página de una sesión en marcha tiene un control de **Modelo** que muestra el modelo que informa la sesión (de la línea de estado de Claude Code) y la misma lista que ofrecen el selector de creación y el teclado de Modelos del chat. Elegir uno escribe exactamente `/model <name>` en esa sesión, lo mismo que hace el teclado de Modelos del chat, con la misma regla sobre quién puede hacerlo (cualquiera que pueda enviarle mensajes a la sesión). El control dice **cambiando…** hasta que la línea de estado informa otro modelo, y luego lo muestra; si no cambia nada en 15 segundos, dice **no confirmado (revisa la sesión)**.

- **Solo con la sesión lista, no mientras Claude trabaja ni con una solicitud abierta.** Claude Code ejecuta `/model` al momento, incluso a mitad de una tarea, y no después, así que el cambio se rechaza hasta que termina la tarea. El teclado del chat también lo rechaza en esos casos, con las mismas palabras.
- **La pregunta «Switch model?» de Claude Code.** Claude Code suele preguntar esto antes de un cambio, porque la siguiente respuesta tiene que volver a leer toda la conversación. Con Claude Code 2.1.251 o posterior, el hook `PreModelSwitch` de aipager aprueba el cambio que acaba de escribir aipager, así que la pregunta no se hace (mira [hooks](hooks.md#premodelswitch)). Cualquier otro cambio sigue preguntando. Con un Claude Code anterior, o en un cambio hacia o desde `opusplan`, la pregunta todavía puede aparecer. Entonces el cambio se muestra como *no confirmado* y la pregunta espera en la terminal. Respóndela ahí antes de enviarle nada más a la sesión, o tu siguiente mensaje se escribiría en ella. Por la misma razón, aipager rechaza otro cambio de esa sesión durante un minuto después de uno sin confirmar, o hasta que la sesión informa un modelo nuevo.

## <a id="the-pinned-status-bar"></a>La barra de estado fijada

Cada chat donde habla aipager (tu chat privado, y cada grupo) tiene un mensaje fijado: la barra de estado. La barra de arriba del chat en Telegram muestra el mensaje desde su **primera línea**, con las líneas seguidas, así que esa línea dice lo que más te necesita:

| Primera línea | Cuándo |
|---|---|
| `⏳ jim te necesita - Bash: make deploy` | una sesión espera una solicitud de permiso, una pregunta o una solicitud interactiva; `(+2 sesiones más)` cuando hay otras esperando también |
| `⚙️ jim (trabajando)` / `💤 jim (lista)` | el chat tiene una sola sesión en marcha y está trabajando / lista (`🔄 jim (iniciándose)` mientras arranca): esta línea es toda la barra, aparte de una línea de límites |
| `⚙️ 2 trabajando · 1 lista` | varias sesiones, ninguna esperando: cuántas hay en cada estado (`trabajando`, `lista`, `iniciándose`); `🔄` cuando ninguna trabaja y una arranca, `💤` cuando todas están listas |
| `💤 todas listas` | ninguna sesión en marcha |

Debajo, solo mientras aplica, una línea de límites (`🐢 envía más despacio tras un aviso de Telegram` (las seis horas después de un 429) o `⏸ las tarjetas no se actualizan por ahora (límite por hora de Telegram)` / `(límite de Telegram)` (modo mínimo, mira [solución de problemas](troubleshooting.md#the-hourly-budget))) y luego, cuando el chat tiene más de una sesión en marcha, `✍️ Los mensajes van a jim` (la sesión a la que va un mensaje normal en este chat, mira abajo; no en un grupo, donde cada persona tiene la suya) y una línea por sesión con su estado: `trabajando`, `te necesita`, `lista` o `iniciándose`. La barra nombra cada sesión una vez: la sesión que espera y que nombra la primera línea no tiene línea propia, y la primera línea nunca enumera nombres que repitan las líneas de abajo. Una sesión que todavía tiene agentes trabajando en segundo plano dice cuántos en su propia línea (o en la única línea, con una sola sesión): `💤 jim (lista, 1 agente trabajando)`. Los comandos que Claude ejecuta en segundo plano se cuentan igual: `(lista, 1 comando en segundo plano en marcha)`, o `(lista, 2 agentes y 1 comando en segundo plano en marcha)` con los dos. Es una cuenta, nunca sus nombres, así que la barra solo se mueve cuando cambia la cuenta. Toca la barra para saltar al mensaje.

**Adónde va un mensaje normal.** Un mensaje que no es una respuesta va a la sesión con la que este chat habló la última vez: la última a la que pasaste con `/<label>`, a la que le enviaste un mensaje, que creaste, o que te respondió aquí la última vez. Un `/mode`, `/clearqueue`, `/diff` o `/now` sin nombre actúa sobre esa misma sesión (`/stop` detiene la que esté trabajando). Cada chat tiene la suya: un mensaje en un chat nunca va a una sesión que es de otro chat. En un grupo solo cuentan una respuesta al bot, un mensaje que lo menciona, un comando o un toque en el teclado del grupo (mira [uso en equipo](groups.md#what-the-bot-reads-in-a-group)), y cada persona tiene su propio destino, que la respuesta de una sesión no mueve (mira [uso en equipo](groups.md#talking-to-a-session-in-a-group)).

Botones del mensaje fijado:

- **Responder a &lt;label&gt;** (uno por cada sesión que espera, como mucho tres) vuelve a enviar la solicitud de esa sesión, con sus botones de respuesta, al final del chat, para que la respondas sin subir. Si mientras tanto se respondió, recibes un aviso corto de «ya respondida» en su lugar, y una copia (o la solicitud original) tocada después de que se respondiera en otro sitio se rechaza igual; nunca responde a una solicitud posterior. Después de un reinicio de aipager, la solicitud que esperaba sigue funcionando desde la tarjeta de actividad o desde su propio mensaje, pero aipager ya no sabe qué solicitud mostraba una copia anterior, así que un toque en una copia que la barra volvió a enviar antes del reinicio se rechaza con «esta solicitud ya no está abierta». Cualquiera que pueda responder la solicitud puede usarlo; nadie más.
- **📱 App** abre la Mini App, en tu chat privado, mientras la Mini App está activada.

La barra no lleva reloj, costo, % de contexto ni modelo, así que solo cambia cuando cambia el estado de una sesión. aipager la edita (en silencio, sin notificación) solo cuando cambió lo que muestra, como mucho una vez cada 30 s por chat (`PINNED_MIN_EDIT_GAP`); un cambio dentro de ese margen se muestra cuando el margen termina, nunca se pierde. Nunca se edita mientras el chat está silenciado por un bloqueo, y se pone al día cuando el silencio termina.

En un grupo el bot necesita permisos de admin para fijar. Si se rechaza el fijado, aipager borra el mensaje de estado que acaba de enviar (para que nunca se quede sin fijar en el historial del grupo) y no muestra barra en ese grupo hasta que aipager se reinicia; lo mismo para un chat que rechaza el bot del todo (lo echó, lo bloqueó). Si borras el mensaje fijado, aipager envía y fija uno nuevo, como mucho una vez por hora por chat.

## <a id="persistent-keyboard"></a>Teclado fijo

Debajo del campo de escritura del chat hay un teclado fijo. Filas, de arriba abajo:

| Fila | Botones | Notas |
|---|---|---|
| Sesiones | un botón por cada etiqueta de sesión en marcha | se crea solo a partir de la lista de sesiones |
| Acciones | `status`, `stop`, `new` | atajos de texto (`kill` sigue funcionando si se escribe; terminar una sesión está en `/status` → `⋮`) |
| Navegación | `Plantillas ›`, `Comandos ›` | La Mini App se abre desde el botón de menú junto al campo de escritura o con `/app`, no desde este teclado |

`Modelo ›` está dentro del submenú de Comandos. Tocar una entrada de un submenú envía un mensaje preparado o un comando de barra:

- **Plantillas**: mensajes que te encuentras escribiendo una y otra vez, por ejemplo «Escribe tests», «Explica el plan», «Actualiza la memoria».
- **Comandos**: comandos de barra que entiende el propio Claude Code (`/compact`, `/clear`, etc.), que se escriben al momento. Un comando o una plantilla nombra la sesión a la que fue (`🧹 /clear enviado a x1`). Borrar la conversación se rechaza mientras la sesión trabaja (`⚠️ No se puede borrar la conversación mientras la sesión trabaja`).
- **Modelos**: cambios rápidos de modelo para la sesión activa. Están los alias de familia (`sonnet`, `opus`, `haiku`, `fable`, `opusplan`), que siempre significan el modelo más nuevo de esa familia, y los modelos fijos (`claude-opus-5-5`, `claude-opus-5-5[1m]` con la ventana de contexto de 1M, `claude-sonnet-5-5`, `claude-fable-5-1`, `claude-haiku-5-5`). Los selectores de creación y de sesión de la Mini App usan la misma lista. Un cambio se rechaza mientras la sesión trabaja o hay una solicitud abierta. La respuesta `🔄` cambia luego para mostrar el modelo que informa la sesión, o dice que el cambio no se confirmó (mira [Cambiar el modelo de una sesión en marcha](#switching-a-running-sessions-model)).

Para cambiar la disposición por defecto, escribe `~/.config/aipager/keyboard.json`:

```json
{
  "templates": [{"label": "Deploy",  "prompt": "Deploy to staging"}],
  "commands":  [{"label": "Compact", "send": "/compact"}],
  "models":    [{"label": "Sonnet",  "send": "/model sonnet"}]
}
```

Cada sección es independiente: las secciones que faltan usan los valores propios, así que puedes cambiar una sin dar las demás. Un JSON roto se ignora con una advertencia en el log. Los cambios necesitan reiniciar aipager.

## <a id="per-message-inline-buttons"></a>Botones debajo de cada mensaje

La mayoría de las respuestas del bot llevan botones propios de la situación:

### <a id="permission-prompts"></a>Solicitudes de permiso

Cuando Claude quiere ejecutar una herramienta que necesita aprobación, la tarjeta de actividad se convierte en una solicitud de permiso:

```
🔐 Bash: List the temp directory
ls -la /tmp

  [✅ Permitir]  [❌ Rechazar]
  [🟢 Permitir siempre]  [⏹ Detener]
```

La tarjeta muestra el comando (o la ruta del archivo) real que Claude quiere ejecutar, debajo de su propia descripción: aprueba lo que puedas leer. Un carácter que cambiaría el orden en que se dibuja el texto (un control de dirección de Unicode como U+202E, que puede hacer que un comando parezca otro) se muestra como una marca del tipo `⟨U+202E⟩`, y entonces la tarjeta añade `⚠️ Este texto tiene caracteres ocultos que cambian la dirección de escritura (se muestran como ⟨U+...⟩). Puede ejecutar algo distinto de lo que ves.`

- **Permitir**: aprueba esta única ejecución.
- **Rechazar**: la rechaza; Claude no ejecuta la herramienta.
- **Permitir siempre**: aprueba y añade la regla permanente que ofrece Claude («no volver a preguntar por …»). El botón aparece **solo cuando Claude ofrece una regla así**; para un comando para el que no puede sacar una (la mayoría de los comandos compuestos) la tarjeta lleva en su lugar Permitir / Rechazar / Detener, igual que para una lectura de solo lectura de un archivo (`Read`, `Grep`, `Glob`) fuera de la carpeta de trabajo de la sesión. Claude Code 2.1.259 y posteriores ponen en ese lugar de su propio diálogo de Bash una fila «pasar al modo automático», en el de lectura fuera de la carpeta una fila «bloquear desde ahora las lecturas fuera de las carpetas de trabajo», y, para un Write/Edit del que no puede sacar una regla por archivo, un cambio de modo de permisos como aceptar solas todas las ediciones de archivos (`acceptEdits`); aipager nunca elige ninguna de estas; cambia de modo a propósito con `/mode`.
- **Detener**: interrumpe la tarea en lugar de responder.

La respuesta le llega a Claude Code por el hook de permisos de aipager, que la espera hasta 20 segundos. Una solicitud que llegó antes de la tarjeta de actividad (entonces se envía como su propio mensaje, solo con Permitir y Rechazar) se responde igual. Cuando esa espera termina, los botones escriben la respuesta en el propio diálogo de Claude Code; Rechazar elige entonces la última fila del diálogo («No, and tell Claude what to do differently»), que termina la tarea de Claude igual que Detener, y la tarjeta dice `🚫 jim · Rechazado`.

Cada toque se guarda en `~/.claude/aipager-audit.jsonl` y se repite como una respuesta de una línea debajo de la tarjeta de actividad: `✅ jim · Permitido · @alice · Bash: List the temp directory` (la descripción de la llamada, o su comando cuando no la tiene; con un chat configurado por `aipager config`; en uso personal solo `Permitido`).

Mientras te espera una solicitud (o un AskUserQuestion), la sesión se muestra esperando, nunca lista. Si Claude Code avisa durante esa espera de que la entrada lleva un rato inactiva, el chat recibe `⬆️ jim · sigue esperando tu respuesta arriba` como respuesta a la solicitud, una vez por espera.

Los diálogos de `AskUserQuestion` se muestran igual, con un botón por opción (las cuatro primeras), y para una pregunta que admite varias respuestas, casillas ⬜/☑ y un botón `✅ Enviar`.

Al 80% del contexto, el chat recibe `⚠️ name · Contexto al 82% - pronto se compactará solo` con **📦 Compactar ahora**, que envía `/compact` a esa sesión. El aviso llega una vez por conversación: compactar no lo trae de vuelta, y una conversación nueva (tras `/clear`, por ejemplo) lo recibe otra vez.

### <a id="stale-buttons"></a>Botones viejos

Los botones que actúan sobre una tarea en curso (Detener, Terminar, Reiniciar, el cambio de `/mode` y su Detener y cambiar, el Reemplazar de `/new`) están atados a la tarea para la que se mostraron. Uno que quedó de una tarea anterior no cambia nada, en lugar de actuar sobre lo que esté en curso ahora, y lo mismo uno que se mostró antes de que la sesión terminara y se continuara o reiniciara. Terminar, Reiniciar, el cambio de `/mode`, y el Detener de `/status` o de la lista de `/stop` responden `x1 empezó otra tarea: …` con lo que tienes que volver a tocar o enviar (o `Esa sesión terminó.`); el Detener de la tarjeta de actividad responde `Esa tarea ya terminó: …`, y el Detener y cambiar de `/mode` devuelve su tarjeta a la tarjeta de `/mode` debajo de `⚠️ x1 empezó otra tarea, así que no cambió nada.` El Reemplazar de `/new` dice `x1 empezó a trabajar mientras tanto. Envía /new otra vez para reemplazarla.` cuando la sesión empezó una tarea después de que se mostrara su tarjeta.

Un botón `⚡ Enviar ahora` solo actúa sobre el mensaje que tiene encima: si Claude ya tomó ese mensaje, el toque responde `Claude ya lo tomó`, no envía nada y quita las líneas de la cola.

### <a id="idle-responses"></a>Respuestas

Cuando termina una tarea, la respuesta llega como dice tu forma de ver la respuesta (mira abajo y [Ajustes](#settings)). Si la respuesta de Claude es más larga de lo que cabe en un mensaje (unos 32 KB), el mensaje muestra lo que quepa bajo una nota `📎 Respuesta completa en el archivo de abajo ↓`, y la tarea entera (sus pasos y la respuesta completa) llega como `<name>_full_log.md`, que Telegram muestra sin otra app. El mismo archivo se adjunta cuando la tarjeta de actividad tuvo que dejar filas fuera.

Una tarea que termina con un error de la API de Anthropic (crédito, servidores saturados, límite de velocidad, red) muestra `⚠️ name · <what went wrong>` en lugar de una respuesta, con **🔄 Reintentar**, que vuelve a enviar el último mensaje a la misma sesión. Mientras el chat está [silenciado por un bloqueo](troubleshooting.md#the-bot-went-quiet-flood-control) un toque no hace nada en absoluto (el mensaje no se reenvía y el botón se queda), así que vuelve a tocarlo cuando el bloqueo termine.

En la forma de tarjeta («Tarjeta + respuesta aparte») la tarjeta terminada siempre se queda encima de la respuesta, en cada tarea, como registro de cómo se llegó a ella: sus filas de herramientas, sus filas de agentes y lo que Claude dijo entre medias, o solo `✅ name · Terminado · Ns` en una tarea que no ejecutó herramientas. La respuesta llega como su propio mensaje (con notificación). Para tener un solo mensaje por tarea, elige «Respuesta dentro de la tarjeta» (la respuesta entra en la tarjeta) o «Solo la respuesta (sin tarjeta)» (la tarjeta se quita y la respuesta queda sola).

Cada tarea tiene exactamente una tarjeta de actividad. Un mensaje que envías mientras una tarea está en curso nunca añade una segunda tarjeta para esa tarea, y una tarjeta cuya tarea terminó ya no conserva su botón Detener: si nada la cerró (un Stop perdido, un reinicio), aipager la cierra a los 30 segundos, como diga tu forma. Después de un reinicio de aipager, una tarjeta cuya tarea sigue en curso se conserva y sigue avanzando enseguida, con su tiempo contado desde que empezó la tarea, y la sesión se muestra trabajando hasta que termina la tarea; cualquier otra tarjeta que quedó de antes del reinicio se cierra («aipager se reinició», sin Detener). Una sesión que esperaba una solicitud de permiso o una sola pregunta de Claude vuelve esperando, no trabajando: su tarjeta y la barra fijada dicen que te necesita y los botones de la solicitud la siguen respondiendo, salvo que la conversación guardada muestre que se respondió en la terminal mientras tanto. Las preguntas con varias partes o varias opciones, y las solicitudes que hace un subagente, se dejan a la terminal. Cuando se sabe que una solicitud terminó (respondida con un toque o en la terminal, terminó su tarea, o un reinicio no la recuperó porque se respondió mientras tanto o no se pudo guardar y volver a leer), sus botones de respuesta viejos responden «ya respondida» y no escriben nada, también después de un reinicio. Una solicitud que nadie respondió y que aipager dejó de esperar (5 minutos sin noticias de Claude) no se sabe que haya terminado, así que sus botones siguen respondiendo el diálogo.

Una tarea que Claude empieza **por su cuenta** (un agente en segundo plano que informa con un `<task-notification>` cuando no hay ningún trabajo abierto) solo recibe su tarjeta de actividad cuando hace algo: en su primera herramienta, o a los 15 s, lo que llegue antes. La mayoría de esos despertares son unos segundos de «nada nuevo»; esos ahora solo muestran la respuesta. El indicador de «escribiendo…» sigue apareciendo mientras se ejecuta, y la respuesta en sí siempre se entrega: nada se filtra por poco importante. Las tareas que empiezas tú, desde Telegram o la terminal, siguen recibiendo su tarjeta al momento.

Mientras una sesión trabaja, cada agente en segundo plano que lanza Claude (con la herramienta `Agent` de Claude Code, `Task` en versiones anteriores) tiene su propia fila en la tarjeta de actividad: `🤖 <type> · <activity> · <elapsed>`, que muestra el tipo del agente y lo que está haciendo, y se actualiza al llegar sus propias herramientas. Cuando ese agente lleva tres herramientas o más, se pliegan en su propio botón `▸ N herramientas usadas` justo debajo de su fila; nunca aparecen en la línea de tiempo de la tarea principal ni en sus cuentas `Bash ×N`. Cuando el agente termina, su fila se fija como `✅ 🤖 <type> · N herramientas usadas · <elapsed>` y conserva el mismo botón. El adjunto `.md` de arriba suma una sección de Agentes con cada agente que se ejecutó en la tarea, su tiempo, su cantidad de herramientas y las herramientas que usó.

Mientras los agentes en segundo plano siguen en marcha después de que terminó su tarea, la tarjeta se queda como estado del trabajo. Cuando Claude toma un mensaje nuevo mientras tanto, la tarjeta en vivo es la que está debajo de ese mensaje, y sigue mostrando los agentes en marcha. Un mensaje que envías a la sesión lista empieza una tarea nueva: la tarjeta de la tarea anterior se fija donde está, conservando sus pasos, y la tarea nueva tiene su propia tarjeta. Un mensaje que estaba en cola y se toma continúa la tarea del trabajo: su tarjeta se vuelve a enviar debajo de ese mensaje y la copia vieja se borra. Las herramientas que se usan dentro de un agente nunca aparecen como filas propias de la tarea principal, y nunca le hacen creer a aipager que empezó una tarea nueva.

Un comando que Claude ejecuta en segundo plano (`run_in_background`, Ctrl+B, o uno que Claude Code manda ahí cuando tarda demasiado) tiene su propia fila mientras se ejecuta: `⏳ comando en segundo plano: <description> (3:12)`, con el nombre de la descripción de la llamada o, si no, de la primera línea del comando. Cuando termina, la fila se fija como `✅ comando en segundo plano: <description> - terminado (6:12)`, `❌ ... - terminó con error (código de salida 1)`, o `⏹ ... - detenido (6:12)` (Claude lo detuvo con TaskStop, o Claude Code lo detuvo por falta de memoria); las filas con error y detenidas conservan esa marca en la tarjeta terminada. Una tarea que termina mientras un comando así sigue en marcha no está terminada: la tarjeta espera como estado del trabajo (`🔄 name · 1 comando en segundo plano aún en marcha · 4:10`, o `1 agente (ship-reviewer) y 1 comando en segundo plano aún en marcha` con un agente también), y cuando el comando termina, la continuación de Claude sigue en esa misma tarjeta. Los comandos que arrancó un agente son de ese agente y no se muestran. Un comando cuyo final aipager nunca ve (un reinicio, un evento perdido) deja de contar pasadas dos horas (`AIPAGER_BG_SHELL_MAX_TRACK`, en segundos): su fila dice `⏹ ... - no se vio el final`, y un trabajo que solo lo esperaba a él se cierra con `⚠️ name · Terminado (2:00:04, sin noticias del comando en segundo plano)`.

Cuando la línea de tiempo de una tarea se alarga, cada tramo anterior de herramientas (tres o más seguidas, y no el tramo que está en curso) se pliega en su propio botón `▸ N herramientas usadas` justo donde pasó, en lugar de amontonarse entero o quedar cortado por el límite de longitud de mensaje de Telegram; toca cualquiera para leerlo ahí mismo. Los comentarios nunca se pliegan; la actividad más reciente y la línea de estado siempre se ven sin tocar nada. La propia fila de un agente en segundo plano que sigue en marcha (o que acaba de fijarse) nunca se pliega ella misma, solo sus herramientas, y nunca mientras sea lo único que se interpone entre la línea de tiempo y el límite. Solo si la línea de tiempo es tan grande que ni con todo plegado cabe, se quita de verdad contenido de la tarjeta; en ese caso el adjunto `.md` de arriba tiene el registro completo.

#### <a id="agents-still-running-when-the-answer-goes-out"></a>Agentes que siguen trabajando cuando sale la respuesta

Claude Code puede ejecutar agentes en segundo plano, y la tarea que los lanzó puede terminar mientras ellos trabajan. Entonces la respuesta termina con un estado corto de ese trabajo en segundo plano, al estilo del propio panel de agentes de Claude Code, que se mantiene al día mientras sigue:

```
⏳ 2 agentes trabajando · 🖥 1 comando en segundo plano (shell) en marcha

○ pipeline-runner · Writing final.md for the pipeline

○ Explore · Reading state.py

Cuando los agentes terminen, el resultado llegará aquí mismo.
```

- **La primera línea cuenta.** Los agentes trabajando (o terminados), y los comandos en segundo plano de este trabajo en marcha, terminados o detenidos. Un comando en segundo plano siempre es solo una cuenta, nunca su comando.
- **Una fila por agente,** su tipo en negrita, y luego lo que está haciendo: su propia última línea de texto (las palabras de Claude, tal como están escritas, nunca traducidas), si no su última herramienta como una frase corta, en el inglés del propio Claude Code (`Reading state.py`, `Writing final.md`, `Running a command`, `Searching for …`), si no la descripción que Claude le dio al agente al lanzarlo, y si no solo el tipo. Como mucho cuatro filas, y luego `+N agentes más`.
- **La promesa** «Cuando los agentes terminen, el resultado llegará aquí mismo.» solo se queda mientras un agente sigue trabajando. Los comandos en segundo plano nunca la llevan: un servidor de desarrollo puede no terminar nunca.

La fila de cada agente se fija por su cuenta cuando ese agente para, como `✓ Explore · terminado (0:42)` (cuánto trabajó ese agente), y la primera línea descuenta. Cuando todos los agentes terminan, las filas dejan paso a una sola línea, con el tiempo desde la respuesta hasta el final del último agente:

```
✅ 2 agentes terminaron (6:12) · 🖥 1 comando en segundo plano (shell) en marcha
```

y cuando ese comando termina, `✅ 2 agentes terminaron (6:12) · 1 comando en segundo plano (shell) terminó (2:13:05)` (cuánto se ejecutó el comando; `detenido` cuando lo cerraron o lo detuvo Claude, `terminó` cuando acabó solo, con error o sin él; su fila de la tarjeta dice cómo). Una respuesta con solo un comando en segundo plano de su trabajo muestra `🖥 1 comando en segundo plano (shell) en marcha`, que se fija como `1 comando en segundo plano (shell) terminó (…)` o `1 comando en segundo plano (shell) detenido (…)`.

Solo se cuentan los comandos en segundo plano del propio trabajo de la respuesta: un servidor de desarrollo que dejó en marcha una tarea anterior no se repite debajo de cada respuesta (la barra fijada sigue contando todos). `/stop` termina el trabajo, pero Escape no termina un comando en segundo plano: uno que sigue en marcha se queda en la barra fijada hasta que termina, y deja de contarse debajo de las respuestas siguientes; su continuación posterior empieza entonces una tarea propia.

Esa respuesta sale en cuanto termina la tarea, como un mensaje normal (con notificación) en respuesta a tu mensaje: el mismo texto que muestra la terminal. La tarjeta de actividad se queda encima como el estado en vivo del trabajo, en todas las formas: `🔄 name · 1 agente (general-purpose) aún en marcha · 1:18`, con su botón **Detener**. Cuando los agentes informan, la respuesta de Claude a eso llega como un mensaje propio; la respuesta anterior nunca se vuelve a enviar, tampoco con un reinicio de aipager. Un trabajo cuyos agentes informan más de una vez produce una respuesta por informe, cada una enviada a medida que se escribe.

Un agente que paró mientras sigue en marcha un trabajo suyo en segundo plano cuenta como trabajando, porque continúa más tarde; aipager lo sabe por el `<task-notification>` de Claude para él, cuando esa notificación empieza una tarea. Una fila espera unos segundos antes de mostrarse terminada, para que esa nota pueda deshacerlo antes. El estado se añade en todas las formas, en el idioma del chat.

**Cómo se mantiene al día.** aipager edita la respuesta en silencio, solo cuando cambió lo que muestra el estado, y como mucho una vez cada 30 segundos por respuesta (la propia respuesta cuenta como la primera). Cada respuesta con estado se mantiene al día así (hasta cinco pendientes por sesión). Las ediciones son de baja prioridad: una que rechaza el presupuesto de envío, o que caería mientras el chat está silenciado, no se hace, y el estado espera a su siguiente cambio. La edición final, cuando todo terminó, se intenta una vez más cuando el silencio termina o si el presupuesto la rechaza, y luego se abandona. No se edita una respuesta borrada, ni para una sesión que terminó o se cerró, ni cuando haría la respuesta demasiado larga para Telegram (cuando se recorta una respuesta larga, se deja sitio para el estado).

El estado se queda como se mostró por última vez, sin pasar a terminado, cuando aipager no puede saber si el trabajo terminó:

- un agente del que aipager no sabe nada durante 30 minutos (`AIPAGER_SUBAGENT_SILENCE`) deja de contarse como en marcha, pero no tener noticias no es haber terminado;
- un comando en segundo plano que se ejecuta más de dos horas (`AIPAGER_BG_SHELL_MAX_TRACK`) sin que se vea su final deja de contarse;
- un reinicio de aipager olvida qué respuestas están pendientes.

Una respuesta retenida por un silencio de bloqueo, o entregada como texto llano después de que fallara el envío con formato, sale sin el estado, y también una tarea que termina sin texto de respuesta (solo una cabecera). Si Claude toma la notificación de un agente en mitad de una tarea en curso, aipager no la ve; un agente que paró con trabajo todavía en marcha puede entonces marcarse como terminado demasiado pronto, y la barra fijada lo vuelve a mostrar en marcha cuando continúa.

### <a id="end-confirmation"></a>Confirmación de terminar

`/kill`, su lista y `⋮` → `⏹ Terminar sesión` siempre piden confirmación:

```
⏹ ¿Terminar jim? Claude se detiene y la sesión termina.

  [⏹ Terminar]  [Cancelar]
```

Después de `⏹ Terminar`, el mensaje dice `⏹ jim terminada` encima de las sesiones que quedan en este chat. Una confirmación tocada después de que la sesión empezara otra tarea se rechaza (`jim empezó otra tarea: …`), y también una para una sesión del mismo nombre creada después.

### <a id="voice-install-when-extra-isnt-installed"></a>Instalar la voz (cuando no está el complemento)

Cuando envías un audio y `aipager[voice]` no está instalado:

```
⚠️ Para los audios hace falta un paquete opcional
   (unos 200 MB de instalación y unos 74 MB de modelo la primera vez que se use).

  [📦 Instalar la función de voz]  [Cancelar]
```

Tocar Instalar ejecuta el comando de instalación correcto para tu instalador (`uv tool install --reinstall aipager[voice]`, `pipx install --force`, o como último recurso `pip install faster-whisper`) con una edición del mensaje cada 5 s, y si sale bien ofrece un botón `[🔄 Reiniciar aipager]`.

### <a id="restart"></a>Reiniciar

`🔄 Reiniciar aipager`:

- Un aipager que funciona como servicio de usuario de systemd programa un `systemctl --user restart aipager.service` separado para 5 s después (con un temporizador de 1 s de precisión), en un servicio temporal fuera del grupo de procesos del propio aipager, para que sobreviva al cierre de aipager. Se niega mientras el servicio cerraría tus sesiones (`KillMode` distinto de `process` o `none`), y te dice que ejecutes primero `aipager service install`.
- macOS, cuando el agente de launchd está instalado (`~/Library/LaunchAgents/com.aipager.daemon.plist`): `launchctl kickstart -k gui/<uid>/com.aipager.daemon`, incluso para un aipager que arrancaste tú.
- Si no, un aipager que arrancaste tú (`aipager start`), en Linux incluso cuando el servicio también está instalado: lanza un sustituto separado que espera a que muera el proceso padre y luego hace `exec aipager start`. El aipager actual se envía SIGTERM a sí mismo en cuanto el sustituto está vivo.

No hace falta SSH.

## <a id="update"></a>Actualizar

`/update` (solo admins; en uso personal, solo quien instaló aipager) responde con un botón, **🔄 Buscar actualizaciones**. No se busca nada hasta que lo tocas. El toque convierte ese mensaje en una línea por producto:

- `aipager 1.0.1 → 1.1.0` cuando hay una versión más nueva (la última de PyPI);
- `aipager 1.0.1 (actualizado)` cuando no la hay;
- `Claude Code 2.1.282 (no se pudo comprobar)` cuando falló la búsqueda (red caída, tiempo límite de 5 s, o no se encontró `claude`).

Claude Code se compara con la última versión de su propio canal de actualizaciones (`autoUpdatesChannel`: latest, stable o rc). Debajo de las líneas, en letra pequeña, va cómo se instaló aipager (por ejemplo `pipx, from PyPI` o `pipx, from local path …`; los chats de grupo nunca muestran rutas).

Si hay algo más nuevo, hay UN botón que actualiza solo los productos que tienen actualización: **Actualizar aipager**, **Actualizar Claude Code** o **Actualizar ambos**, más **Cancelar**. Cuando se ofrece aipager, el mensaje también dice cómo será el reinicio: `Reinicio: automático, cuando no haya ninguna tarea en curso.` o `Reinicio: manual (motivo)`. Un aipager más nuevo en una instalación que no se puede actualizar desde aquí (editable, Nix, Snap, un paquete del sistema, un contenedor, o la instalación de otro usuario) se muestra con el motivo y no se ofrece. Lo mismo para un Claude Code más nuevo instalado con npm en una carpeta donde tu usuario no puede escribir (por ejemplo `sudo npm install -g` bajo `/usr/lib/node_modules`), donde `claude update` solo podría fallar: la línea dice `no se puede actualizar desde aquí: installed with npm into a folder your user can't write to; run "claude install" once to switch to Claude Code's own installer, then restart aipager`. Si no se puede actualizar nada, el mensaje dice «Todo está actualizado.», dice que una comprobación falló, o, cuando hay algo más nuevo pero no se puede instalar desde aquí, «Hay una actualización nueva, pero no se puede instalar desde aquí.», con un botón **Volver a buscar** y sin botón de actualizar.

El botón Actualizar solo arranca lo que ofreció la búsqueda. Si la búsqueda tiene más de 10 minutos, se ejecutó otra actualización desde entonces, o las versiones ya no coinciden con el botón, en su lugar te pide que vuelvas a buscar. Los botones de un menú anterior de `/update` (los **Actualizar Claude Code** / **Actualizar aipager** / **Ambos** por producto) responden «Este menú es antiguo: envía /update otra vez» y no hacen nada. Cada toque vuelve a comprobar la regla de admin.

Actualizar Claude Code ejecuta `claude update` (con su ruta completa, 5 min de tiempo límite) e informa `Claude Code A → B`, «actualizado», o el fallo con el final de su salida. Las sesiones en marcha se quedan con la versión vieja hasta que las reinicies (`/restart`); la respuesta las enumera. No se reinicia ninguna sesión por ti. Las sesiones nuevas usan la versión nueva.

Actualizar aipager (**Actualizar aipager**, o la segunda mitad de **Actualizar ambos**, que actualiza primero Claude Code):

1. En una instalación de PyPI que ya está al día, lo dice y se para.
2. Si aipager puede reiniciarse solo, primero **espera hasta que no haya nada en curso**: ninguna sesión ejecutando una tarea, esperando una pregunta, ejecutando un agente en segundo plano, mostrando una tarjeta de actividad en vivo, ejecutando una herramienta o con una solicitud de permiso abierta, y ninguna respuesta retenida esperando a que se levante un límite de velocidad. El mensaje dice qué está esperando, con **Reiniciar ahora** (saltarse la espera) y **Cancelar**. A los 10 minutos vuelve a preguntar: **Esperar 10 min más**, **Reiniciar ahora**, **Cancelar**. Si nadie responde en una hora, se cancela solo.
3. Actualiza a través del instalador que es dueño del aipager en marcha (`pipx upgrade aipager`, `uv tool upgrade aipager`, `brew upgrade aipager`, o `<venv>/bin/python -m pip install --upgrade aipager`), con su ruta completa y 10 minutos de tiempo límite. No hay Cancelar mientras se ejecuta el instalador.
4. Comprueba que la versión nueva se importa en un intérprete nuevo. Una actualización que falla, se pasa de tiempo o no se puede importar no reinicia nada. Una actualización que se pasó de tiempo se detuvo a medias, así que el mensaje avisa de que la instalación puede estar incompleta y da el comando de reinstalación para ejecutar antes del siguiente reinicio. La salida del instalador solo se muestra en un chat privado; un grupo recibe «(la salida está en el log de aipager)».
5. Si empezó una tarea durante la actualización, vuelve a esperar.
6. Programa un `systemctl --user restart aipager.service` separado para 5 s después (con un temporizador de 1 s de precisión), y el mensaje de estado dice `⏳ aipager A → B instalado, reiniciando…`. Mientras espera el paso 5, dice `⏳ aipager A → B instalado; se reiniciará cuando termine la tarea actual`. Después del reinicio, el aipager nuevo convierte ese mismo mensaje en `✅ aipager actualizado A → B; N sesiones se volvieron a conectar` (y `⚠️ No volvieron: …` para cada sesión que no volvió). Si el mensaje se borró o ya no se puede editar, publica el resultado como un mensaje nuevo en el chat que lo pidió.

En la Mini App, **Ajustes → Actualizaciones** muestra «Reiniciando aipager…» con un farol que gira hasta que el aipager nuevo responde, y luego «Actualizado a B.» con la cantidad de sesiones que se volvieron a conectar. Nunca muestra una cuenta atrás. Pasados unos 2 minutos sin respuesta (nunca antes de que el aipager viejo hubiera podido informar de un reinicio que no pasó) dice «Todavía se está reiniciando; vuelve a abrir la app en un momento.». Con el túnel gestionado de aipager, la dirección de la app cambia en cada reinicio, así que cuando el aipager viejo deja de responder la página abierta dice «aipager se reinició; vuelve a abrir la app cuando el chat diga que se actualizó»; ciérrala y vuelve a abrirla desde el botón de menú del chat o `/app`. El inicio de sesión de la app dura 5 minutos, así que una página abierta hace más tiempo dice en su lugar «Vuelve a abrir la app para ver el resultado de la actualización.».

aipager solo se reinicia solo cuando funciona como servicio de usuario de systemd **y** ese servicio tiene `KillMode=process` (o `none`). Si no, aipager igualmente se actualiza, y el mensaje te dice cómo reiniciarlo: ejecuta primero `aipager service install` (enumera las sesiones que cerraría un reinicio), el comando `launchctl kickstart` en macOS, o `Hace falta reiniciar: no service unit is running this daemon; restart it yourself` para un aipager que arrancaste tú.

Solo se ejecuta una actualización a la vez, entre `/update`, el bloque **Ajustes → Actualizaciones** de la Mini App (el mismo botón Buscar actualizaciones, el mismo botón único de Actualizar, el mismo trabajo) y `aipager update` en la línea de comandos. Eso incluye los segundos entre «instalado, reiniciando…» y el propio reinicio: `/update` responde que aipager está a punto de reiniciarse, la Mini App no ofrece botones, y el **Reiniciar aipager** de la función de voz se niega mientras una actualización se ejecuta o espera para reiniciar. Si aipager sigue vivo dos minutos después de programar su reinicio, detiene el temporizador de reinicio pendiente, libera el bloqueo de actualización y te dice que lo reinicies tú.

Si aipager se apaga mientras un instalador se está ejecutando, el instalador tiene 3 s para terminar y luego se detiene (si no, seguiría escribiendo la instalación mientras arranca el siguiente aipager). El mensaje de estado y, después del reinicio, un mensaje nuevo dicen que la instalación puede estar incompleta y cómo repararla.

Una vez que empezó un apagado, no se reinicia nada y no empieza nada nuevo: los botones de `/update` responden que aipager se está apagando, la Mini App responde 503, y no se lanza ningún instalador ni búsqueda de versiones. Un instalador que termina dentro de sus 3 s sigue contando. El mensaje de estado dice que la versión nueva está instalada y que no se reinició nada, y el siguiente arranque anuncia `aipager updated A → B`. Un aipager que detuviste con `aipager service stop` se queda detenido. La parte de la actualización en el apagado tarda como mucho 8 s en total.

## <a id="free-messages"></a>Mensajes libres

### <a id="text"></a>Texto

Se toma como el siguiente mensaje para la **sesión activa**: el destino de este chat, la sesión a la que pasaste por última vez, a la que le enviaste un mensaje, que creaste, o que te respondió aquí la última vez (mira [la barra de estado fijada](#the-pinned-status-bar)). En un grupo cada persona tiene su propio destino, que la respuesta de una sesión no mueve (mira [uso en equipo](groups.md#talking-to-a-session-in-a-group)). Los mensajes llegan a Claude **al momento**, incluso mientras tu propia tarea está en curso, exactamente como escribir en la terminal (en un grupo, el mensaje de otra persona espera a que termine esa tarea; mira abajo). Envía varios y se ponen en cola dentro del propio Claude, que toma cada uno en un momento natural:

La reacción a tu mensaje lo acompaña, igual que en Claude Code un mensaje en cola pasa de gris a blanco:

- 👀: se entregó a la sesión (o se retuvo, mira abajo), pero Claude todavía no lo tomó. Un mensaje enviado mientras hay una tarea en curso se queda en 👀 mientras espera en la cola de Claude.
- 👍: Claude lo tomó: empezó una tarea con él, lo sumó a la tarea en curso, o se lo pasó a un agente en segundo plano que está trabajando.
- 🤷: nunca se tomará: un mensaje retenido que descartaron `/stop`, `/clearqueue` o `/kill`, o que no se pudo enviar al liberarse; un mensaje o comando que todavía esperaba cuando `/stop`, `/clearqueue`, `/kill` o el final de la sesión (no `/clear` ni `/resume`) lo descartaron; o un mensaje que Claude Code rechazó (mira abajo). Los mensajes que Claude ya tenía en cola solo se marcan mientras aipager puede ver esa cola (la lectura en vivo de la conversación guardada está en marcha y no espera ningún trabajo en segundo plano); si no, se quedan en 👀. Escape en la terminal devuelve la cola de Claude a su campo de escritura, desde donde puede volver a enviarse, así que un mensaje descartado así también se queda en 👀.
- 👌: un comando de Claude Code que se ejecutó (mira abajo), o aipager confirmando un `/stop`.

Una reacción solo avanza, así que un mensaje recibe como mucho tres, cada una una vez. Una reacción que cae dentro de un bloqueo por límite de velocidad de Telegram se salta, no se repite más tarde, y un cierre marca como mucho los diez mensajes más recientes que descarta.

Botones de comando (`Compactar`, `/model …`): tocados con la sesión lista, el comando recibe 👌 al momento; Claude Code lo ejecuta con Enter, y un comando local como `/model` no lanza ningún hook que pudiera decirlo más tarde. Tocados mientras hay una tarea en curso (no `/model`: se rechaza hasta que termina la tarea, mira [cambiar el modelo de una sesión en marcha](#switching-a-running-sessions-model)), se queda en 👀 hasta que termina esa tarea (de forma normal, como pausa de un trabajo en segundo plano, o con un error de la API) y luego 👌; si antes lo descartan `/stop`, `/clearqueue`, `/kill` o el final de la sesión, nunca se ejecutó: 🤷. Cuando Claude Code lo pone en cola como un mensaje, sigue el camino 👀 → 👍. Un audio recibe las mismas reacciones que el texto una vez que se envía su texto.

La tarjeta de actividad y la respuesta final siguen al mensaje que Claude tomó de verdad para una tarea (con el que empezó si la sesión estaba lista, o el que sumó a la tarea en curso), nunca simplemente al último mensaje que enviaste. Enviar otro mensaje a mitad de una tarea no «se lleva la respuesta»: si Claude lo suma a la respuesta que está formando, la tarjeta salta a él en ese momento; si Claude termina primero y luego lo toma, la primera respuesta se queda debajo del primer mensaje y el siguiente tiene su propia tarea, con su propia tarjeta y su respuesta debajo.

Si Claude Code rechaza un mensaje del todo (un comando de barra desconocido, o uno propio que solo abre un diálogo en la terminal), no llega ningún hook, así que nada terminaría la tarea que aipager acaba de anunciar. Pasados 8 s sin ningún hook (`PROMPT_HOOK_GRACE_SECONDS`), la tarjeta de actividad pasa a `⚠️ name · Claude Code no recibió el mensaje` con una explicación de una línea y la sesión vuelve a estar lista; el motivo está en la terminal, y el mensaje recibe 🤷 (un comando de barra recibe 👌 en su lugar: un comando propio que abre un diálogo tampoco lanza ningún hook). Solo se juzga así un mensaje que empezó una tarea; uno en cola detrás de una tarea en curso conserva su 👀 hasta que Claude lo toma o se descarta.

Tres casos se retienen en lugar de enviarse, y se entregan solos cuando se resuelven:

- Hay una solicitud de permiso o una pregunta abierta: si no, tu texto se leería como la respuesta a ese diálogo.
- (Uso en equipo) el mensaje de otra persona todavía espera a que lo tomen.
- (Uso en equipo) la tarea de otra persona está en curso, incluido un agente en segundo plano que arrancó. Una tarea escrita en la terminal cuenta como del propietario, así que solo el mensaje de un propietario (o, en un chat privado, el de su propio miembro) se suma a ella.

Los dos últimos mantienen separados en tareas distintas los mensajes de personas distintas, para que cada uno se ejecute con los permisos de quien lo envió. Si aun así uno se suma (por ejemplo de un remitente que aipager no conoce, que nunca se retiene), la tarea se ejecuta con las reglas más estrictas de todos los que están en ella (mira [uso en equipo](groups.md#who-a-message-runs-as)). Si se detiene la tarea que espera un mensaje, o la para una herramienta bloqueada, los mensajes retenidos se descartan con un 🤷.

Los mensajes retenidos tienen un límite de 50 por sesión y caducan a las 24 h; `/clearqueue` los descarta junto con lo que tenga Claude.

#### <a id="send-a-queued-message-now"></a>Enviar ya un mensaje en cola

Un mensaje que envías mientras Claude trabaja en tu propia tarea le llega a Claude al momento (aipager no lo retiene) y espera en la cola de Claude hasta que termina el paso actual. En cuanto la cola de Claude lo muestra, aipager responde debajo de él:

```
⏳ En cola - Claude lo leerá después del paso actual
[⚡ Enviar ahora]
```

Cada mensaje en cola tiene su propia línea, y cada línea desaparece, con su 👍, en el momento en que Claude toma ese mensaje. Un mensaje que Claude toma antes de que se vea su registro en la cola no recibe línea, solo 👀 y luego 👍.

Tocar `⚡ Enviar ahora` (o enviar `/now`) pulsa en esa sesión las propias teclas de enviar ya de Claude Code (Ctrl+X y luego Ctrl+S). Claude entonces lee de una vez todo lo que tiene en cola, no solo el mensaje que tocaste. Qué pasa con el trabajo en curso lo decide Claude Code: un comando o un agente en marcha sigue en segundo plano; si Claude estaba escribiendo una respuesta, esa respuesta se corta y vuelve a empezar con tu mensaje. Cualquier otro tipo de paso (otra herramienta, por ejemplo) también puede interrumpirse, tras un breve margen. En algunos casos (por ejemplo con las tareas en segundo plano de Claude Code desactivadas) enviar ya vuelve a ser una simple interrupción del paso en curso. Todo esto es el comportamiento propio de enviar ya de Claude Code, no algo que decida aipager.

El toque responde `Enviado a Claude ahora` y todas las líneas desaparecen a la vez, porque enviar ya le da a Claude todo lo que tiene; `/now` las quita igual, y no llega ninguna línea para los mensajes que envió Enviar ahora (un mensaje que envíes después tiene la suya). Si Claude cancela el paso en curso en lugar de mandarlo a segundo plano, toma al momento solo el mensaje en cola más antiguo y lee el resto en su siguiente paso; la respuesta va debajo del mensaje que tomó, y ese mensaje no se ejecuta dos veces. Si las teclas tardan más de un momento en llegar a Claude (una computadora ocupada), el toque responde en su lugar `Enviando a Claude ahora`; la línea desaparece cuando llegan o, si no se pudieron entregar, se queda y dice `⏳ En cola: no se pudo llegar a Claude, toca «Enviar ahora» para reintentar`, con su botón, para que vuelvas a tocar. Las teclas solo se pulsan cuando la conversación guardada de Claude muestra que todavía tiene el mensaje; si no, el toque responde `Claude ya lo tomó` y quita la línea. Se rechaza, sin pulsar nada, mientras hay una solicitud de permiso o una pregunta abierta (`Primero responde a la pregunta abierta`), mientras Claude puede acabar de abrir una que aipager todavía no mostró (los eventos de una tarea nueva se retienen mientras todavía se está entregando la respuesta de la tarea anterior: `Ocupado, vuelve a intentarlo en un momento`), y mientras aipager está escribiendo otro mensaje en la sesión (`aipager está escribiendo un mensaje; vuelve a intentarlo en un momento`). Cualquiera que pueda enviar mensajes a la sesión puede usar el botón y `/now`; un miembro `read_only` no.

La línea desaparece sola cuando su mensaje sale de la cola de Claude: Claude lo toma (lo suma a la tarea en curso, se lo pasa a un agente en segundo plano, o lo empieza como la siguiente tarea), Escape en la terminal devuelve la cola al campo de escritura, o lo descartan `/stop`, `/clearqueue`, `/kill`, `/restart`, una parada de seguridad o el final de la sesión. Un reinicio de aipager quita cualquier línea que quede a la vista.

Las líneas nunca esperan al gestor de límites: salen y se quitan al momento incluso en un chat ocupado o lento, y en modo mínimo. Solo mientras el propio Telegram tiene el chat silenciado no se envía ninguna línea (`/now` sigue funcionando entonces), y si Telegram acaba de pedirle a aipager que vaya más despacio, una línea espera los segundos que pidió. Cada mensaje que espera cuesta una llamada al chat para su línea; las líneas que se van juntas se quitan con una sola llamada.

### <a id="files"></a>Archivos

Los archivos subidos se descargan en `/tmp/aipager-files/` (con el nombre `<time>_<file name>`) y la ruta se le ofrece a Claude: con un pie, el mensaje es el pie seguido de la ruta (o las rutas); sin pie, es solo `check this: <path>` (o `check these: <paths>` para un álbum), así que a Claude se le señala el archivo sin decirle qué hacer con él. Un pie que empieza con `/<label>` elige la sesión, exactamente como `/<label> <prompt>` para el texto, y la etiqueta se quita del mensaje: `/api` solo envía `check this: <path>` a `api`, y `/api compare these` envía ahí `compare these <paths>`. Solo un `/<label>` al principio elige el destino; una barra más adelante en el pie es solo texto. Una etiqueta a la que no responde ninguna sesión se rechaza con `⚠️ No hay ninguna sesión con ese nombre` y no se envía nada. El límite de 20 MB de descarga de archivos de los bots de Telegram se aplica de antemano; los archivos más grandes reciben un rechazo claro antes de intentar ninguna descarga. Una descarga que se topa con un error de red pasajero se intenta hasta tres veces en total (esperando 1 s y luego 2 s) antes de que veas un error, y ese error nombra el archivo. Un álbum (varias fotos o documentos enviados como un solo mensaje) se le pasa a Claude como un solo mensaje (el pie, y luego cada ruta de archivo en orden) cuando ya llegó su último elemento; si un elemento no se puede descargar, los demás salen igual, con una nota que nombra el que falta.

### <a id="voice"></a>Voz

Los audios pasan por `faster-whisper` (el complemento `aipager[voice]`). El audio se pasa a texto en esta computadora, se repite como `🎙️ Escuché: …`, y se escribe como si lo hubieras tecleado tú, también como el nombre para `/new` o para un cambio de nombre pendiente. Un audio que pasa del límite de 20 MB de descarga de los bots de Telegram se rechaza. Mira [hooks → UserPromptSubmit](hooks.md#userpromptsubmit) para lo que pasa después.

---

## <a id="command-line-every-command"></a>Línea de comandos: todos los comandos

Ejecuta `aipager <command> --help` para ver la misma lista en tu computadora.

| Comando | Qué hace |
|---|---|
| `aipager config` | El asistente de configuración interactivo: token del bot, chats, miembros y roles, los hooks de Claude Code. Vuelve a ejecutarlo para cambiar cualquier cosa (mira [uso en equipo](groups.md#setup)). |
| `aipager setup` | La misma primera configuración sin preguntas, para scripts y agentes de programación (mira [abajo](#command-line-aipager-setup-for-coding-agents)). |
| `aipager start` | Ejecuta aipager en primer plano. Se niega a arrancar mientras otro aipager está en marcha. |
| `aipager service install [--yes]` | Instala aipager como servicio en segundo plano (servicio de usuario de systemd en Linux, agente de launchd en macOS) y lo arranca. `--yes` se salta la pregunta cuando el servicio instalado es distinto. |
| `aipager service start\|stop\|status\|logs\|uninstall` | Arranca, detiene o muestra el servicio, sigue su log, o lo quita. |
| `aipager status [--json]` | aipager y todas las sesiones de un vistazo: estado, modelo, contexto, costo, y el estado de los límites de velocidad de Telegram. |
| `aipager logs [-f] [-n LINES]` | El log de aipager (journald en Linux, el log de launchd en macOS): las últimas 100 líneas, o `-n` líneas; `-f` lo sigue. |
| `aipager doctor [--fix] [--safety-check] [--json]` | Comprobaciones de salud (mira [solución de problemas](troubleshooting.md#aipager-doctor-check-list)). `--fix` se ofrece a arreglar la credencial de Claude y una instalación ambigua de `claude`, preguntando antes; `--safety-check` imprime las reglas de seguridad en vigor; `--json` es para scripts (mira [abajo](#aipager-doctor---json)). |
| `aipager session <name> [claude args]` | Arranca una sesión de Claude Code llamada `<name>` bajo dtach en la carpeta actual, o se conecta a ella si está en marcha. Los argumentos después del nombre (o después de `--`) van tal cual a `claude`, por ejemplo `--resume`. Necesita un aipager en marcha. |
| `aipager session ls [-a] [--json]` | Lista las sesiones; `-a` incluye las terminadas. También `aipager session list`. |
| `aipager session kill <name> [-y]` | Termina una sesión, tras una pregunta salvo con `-y`. |
| `aipager resume [name]` | Recupera una sesión terminada con su conversación; sin nombre, una lista para elegir. |
| `aipager miniapp enable [--port PORT] [--url URL]` | Activa la Mini App, en `127.0.0.1:PORT` (por defecto 8765), detrás del túnel gestionado o de tu propia URL `https://`. Se aplica en el siguiente reinicio de aipager. |
| `aipager miniapp disable\|status` | La desactiva (en el siguiente reinicio), o muestra sus ajustes y su dirección. |
| `aipager policy validate` | Comprueba `policy.yaml` (y `policy.d`); sale con un código distinto de cero si hay un problema. |
| `aipager update` | Actualiza aipager con el instalador que es su dueño (uv, pipx, Homebrew, pip). Nunca reinicia aipager; reinícialo tú, o usa `/update` en Telegram, que sí puede (mira [Actualizar](#update)). |
| `aipager report [--note TEXT]` | Muestra un informe de problemas y lo envía si dices que sí (mira [abajo](#command-line-aipager-report)). |
| `aipager uninstall [-y]` | Detiene y quita aipager y su configuración, conservando sus datos (mira [abajo](#command-line-aipager-uninstall)). |
| `aipager version`, `aipager --version` | Imprime la versión. |
| `aipager help [command]` | Ayuda de aipager o de un comando. |

## <a id="command-line-aipager-setup-for-coding-agents"></a>Línea de comandos: `aipager setup` (para agentes de programación)

Las secciones de arriba describen el bot de Telegram. Esta describe un comando que ejecutas en una terminal, o que ejecuta por ti un script o tu agente de programación: `aipager setup` configura aipager sin preguntas y da el mismo resultado que la primera ejecución de `aipager config`.

```sh
# 1. encontrar el ID de Telegram de la persona (abre el bot y pulsa Start)
aipager setup detect-chat --token-file ~/bot-token.txt --json
# 2. cuando confirme que es ella, configurar aipager
aipager setup --token-file ~/bot-token.txt --chat-id 123456789 --service --json
# 3. comprobar la instalación
aipager doctor --json
```

Qué hace, en este orden: lee el token del bot, lo comprueba con Telegram (getMe), comprueba todo en esta computadora sin escribir nada (la configuración existente, las dependencias, `~/.claude/settings.json`, si hay un aipager en marcha), envía un mensaje de prueba a la persona ("aipager is set up for you. ..."), y solo entonces escribe. Los archivos que escribe, igual que el asistente:

- `~/.config/aipager/aipager.yaml` (permisos 0600): `bot_token` y un chat privado cuyo único miembro es la persona (`label: owner`, rol owner o admin).
- `~/.claude/settings.json`: los hooks de aipager y la statusLine (cuando el archivo existente cambia, se guarda una copia `settings.json.bak.<time>`).
- El registro de auditoría recibe un registro `grant-owner` cuando alguien pasa a ser propietario.
- Con `--service`, el servicio en segundo plano (lo mismo que `aipager service install --yes`).
- Solo en una instalación antigua con `config.env` (todavía sin `aipager.yaml`), la migración que hace el asistente: una copia `config.env.bak.<time>` (y `team.yaml.bak.<time>` cuando hay un `team.yaml`), y un `~/.config/aipager/policy.yaml` de ejemplo cuando no hay ninguno. `config.env` en sí se deja como está.

Si alguna comprobación falla, no se envía ni se escribe nada. Cuando ya hay un aipager en marcha, un cambio de chat o de rol se recarga en vivo; un token de bot nuevo necesita reiniciar (`aipager service stop` y luego `aipager service start`), cosa que setup nunca hace por su cuenta, y `--service` se salta. Si setup no puede saber si hay un aipager en marcha, actúa como si lo hubiera (advertencia `daemon_unknown`): entonces nunca instala el servicio, así que nunca puede reiniciar un aipager vivo, y después de cualquier cambio no envía ninguna recarga y en su lugar informa `restart_needed: true`.

### <a id="aipager-setup-flags"></a>Opciones de `aipager setup`

| Opción | Significado |
|---|---|
| `--token-file PATH` | Lee el token del bot de este archivo (como mucho 4096 bytes; vale que haya texto alrededor de un token). Un archivo que otros usuarios pueden leer funciona pero da la advertencia `token_file_shared`: hazle `chmod 600`, o bórralo cuando setup salga bien. |
| `--token-stdin` | Lee el token del bot de stdin (una tubería o un archivo redirigido, nunca una terminal). La tubería tiene que cerrarse: setup espera como mucho 30 segundos el final de stdin y luego sale con 2 y `token_stdin_timeout`. `printf '%s\n' "$TOKEN" \| aipager setup --token-stdin ...` y `< token.txt` la cierran los dos. |
| `--chat-id N` | Obligatoria. El ID numérico de usuario de Telegram de la persona, que también es el ID de su chat privado. Los grupos se añaden con `aipager config`. |
| `--role owner\|admin` | El rol de la persona (por defecto `owner`). |
| `--service` | También instala y arranca el servicio en segundo plano (se salta mientras hay un aipager en marcha). |
| `--force` | Permite reemplazar el token del bot, el chat privado del propietario o el rol de una instalación existente. Los chats de grupo y los demás chats privados se conservan. |
| `--dry-run` | Ejecuta todas las comprobaciones, no envía nada, no escribe nada, e informa de lo que cambiaría. |
| `--json` | Imprime exactamente un objeto JSON en stdout; todo lo demás va a stderr. |

Da exactamente una de `--token-file` y `--token-stdin`. El token nunca se acepta como valor de una opción: `--token` y `--bot-token` se rechazan (`token_on_command_line`) y el valor no se muestra. Las opciones abreviadas (`--token-f`, `--dry`) no se aceptan. setup nunca hace preguntas, nunca necesita una terminal y nunca lee el token de una variable de entorno.

Volver a ejecutarlo con el mismo token, chat y rol no cambia nada y lo dice (`unchanged`, sin mensaje de prueba). Un token, un chat o un rol distintos en una instalación existente salen con 6 hasta que añadas `--force`. Una instalación antigua con `config.env` se migra primero, como hace el asistente (le da el rol `admin`, así que `--role owner` en ella necesita `--force`).

### <a id="aipager-setup-detect-chat"></a>`aipager setup detect-chat`

```sh
aipager setup detect-chat (--token-file PATH | --token-stdin) [--timeout SECONDS] [--json]
```

Espera el mensaje privado más reciente al bot (pulsar Start envía uno) e imprime su remitente: ID, nombre y nombre de usuario. No guarda nada, así que muéstrale la persona al usuario y ejecuta `aipager setup --chat-id` solo cuando la confirme. Solo cuentan los mensajes enviados como mucho 10 minutos antes de que empezara el comando. `--timeout` va de 0 a 3600 segundos (por defecto 300; 0 significa mirar una sola vez). Lee Telegram sin confirmar ningún mensaje, así que el `/start` sigue disponible para el asistente y para aipager.

Mientras hay en marcha un aipager para el mismo bot, aipager recibe los mensajes del bot, y no anota los mensajes privados de personas que todavía no están configuradas. Entonces detect-chat sale con 9 (`source: "daemon"`): detén aipager (`aipager service stop`) y vuelve a ejecutarlo, o pregúntale a la persona su ID numérico. Con un aipager para otro bot, lee Telegram directamente (`source: "telegram"`).

Límite: como detect-chat nunca confirma un mensaje, Telegram solo le muestra las 100 actualizaciones sin leer más antiguas de las últimas 24 horas. Cuando el bot tiene tantas esperando, un `/start` nuevo no se ve: detect-chat entonces se pasa de tiempo (salida 8) con la advertencia `updates_backlog`, y volver a pulsar Start no ayuda. Pregúntale en su lugar a la persona su ID numérico de Telegram (o arranca aipager una vez, que lee y vacía esas actualizaciones).

### <a id="exit-codes"></a>Códigos de salida

| Código | `error` | Significado |
|---|---|---|
| 0 | | Bien (`installed`, `updated`, `unchanged`, `dry_run`, o detect-chat con `found`). |
| 1 | `config_malformed`, `ambiguous_install`, `settings_invalid`, `test_send_failed`, `write_failed`, `service_failed`, `internal_error` | Fallo. `ambiguous_install`: más de un chat privado podría ser el del propietario; arréglalo en `aipager config`. |
| 2 | `usage`, `missing_token_source`, `token_source_conflict`, `token_file_unreadable`, `token_stdin_is_tty`, `token_stdin_timeout`, `token_malformed`, `token_on_command_line`, `bad_chat_id`, `bad_role`, `bad_timeout` | Entrada mala o que falta; `fix` nombra la opción. Una opción desconocida o una palabra equivocada después de `setup` la informa el analizador de argumentos (texto llano, sin JSON), también con 2. |
| 3 | `deps_missing` | Falta dtach, claude, aipager-hook o aipager-statusline; `fix` tiene los comandos. No se escribió nada. |
| 4 | `token_rejected` | Telegram rechazó el token. |
| 5 | `chat_not_started`, `bot_blocked` | La persona no pulsó Start en el bot ("Open t.me/<bot> and press Start, then run this again."), o lo bloqueó. No se escribió nada. |
| 6 | `existing_install` | Ya hay configurado otro token, chat o rol; añade `--force`. |
| 7 | `telegram_unreachable` | Error de red, o Telegram respondió 429 o 5xx. |
| 8 | `detect_timeout` | detect-chat no vio ningún mensaje privado antes de `--timeout` (con la advertencia `updates_backlog` cuando 100 actualizaciones viejas sin leer tapan las nuevas). |
| 9 | `daemon_running` | detect-chat: un aipager en marcha recibe los mensajes del bot. |
| 10 | `updates_conflict` | detect-chat: otro programa lee los mensajes de este bot. |
| 130 | `interrupted` | Ctrl-C. |

Cuando fallan varias comprobaciones a la vez, el código sigue este orden: `config_malformed`, `ambiguous_install`, `existing_install`, `settings_invalid`, `deps_missing`. `--dry-run` sale con el código que tendría la ejecución real por estas comprobaciones.

### <a id="json-aipager-setup---json"></a>JSON: `aipager setup --json`

Todas las claves están siempre; un valor que todavía no se sabe es `null`.

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

- `status`: `installed`, `updated`, `unchanged`, `dry_run` o `error`.
- `changed`: qué cambió (o cambiaría, en una ejecución de prueba), en este orden: `migrated_v1`, `bot_token`, `owner_dm`, `role`, `settings_json`, `service`.
- `test_message`: `sent`, `not_needed`, `skipped_dry_run`, `failed` o `not_attempted`.
- `settings_json.status`: `created`, `patched`, `unchanged`, `would_change` o `not_checked`.
- `daemon.reload`: `reloaded`, `refused`, `not_reloaded` o `not_needed`. `restart_needed` es true después de un token nuevo mientras hay un aipager en marcha, y después de cualquier cambio cuando aipager no se pudo recargar o setup no pudo saber si había uno en marcha.
- `service.result`: `not_requested`, `installed`, `already_installed`, `skipped_daemon_running`, `would_install`, `failed`, o `null` cuando setup se paró antes.
- `warnings`: objetos con `code` (`token_file_shared`, `reload_refused`, `daemon_unknown`, `audit_write_failed`) y `message`. `audit_write_failed`: el permiso de propietario está escrito, pero su registro `grant-owner` no se pudo añadir al registro de auditoría; ese registro es de mejor esfuerzo, así que el código de salida se queda en 0.
- `next_step` después de un cambio con un aipager en marcha que no se pudo recargar dice que lo reinicies (`aipager service stop` y luego `aipager service start`); nombra el token del bot solo cuando el token cambió.
- Con la advertencia `daemon_unknown`, `next_step` nunca dice que aipager está en marcha. Dice que setup no pudo saberlo, señala `aipager doctor --json`, y dice que arranques aipager con `aipager service install` o `aipager start` si no está en marcha. Cuando haría falta reiniciar, solo lo pide si hay un aipager en marcha, y si no dice que lo arranques igual.
- En caso de error, `status` es `error` y `error`, `message` y `fix` dicen qué salió mal y qué hacer; `next_step` repite `fix`. Cuando el error llegó después de una escritura, `changed` enumera lo que se escribió, y la salida normal añade una línea en stderr `Already written: aipager.yaml, ... (changed: ...)`.

### <a id="json-aipager-setup-detect-chat---json"></a>JSON: `aipager setup detect-chat --json`

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

`candidate` es `null` salvo que `status` sea `found`. `warnings` contiene objetos con `code` (`token_file_shared`, `updates_backlog`) y `message`. `other_candidates` cuenta a otras personas que le escribieron al bot en los mismos 10 minutos: cuando no es 0, asegúrate de que tienes a la persona correcta. `next_step` repite la opción de token que usaste, nunca el token.

### <a id="aipager-doctor---json"></a>`aipager doctor --json`

Las comprobaciones de `aipager doctor` como un objeto JSON en stdout. Igual que `aipager doctor` normal, solo lee y no envía ningún mensaje de Telegram. Sale con 1 cuando falla alguna comprobación, y si no con 0. No se puede combinar con `--fix` ni `--safety-check` (sale con 2 y un objeto de error).

```json
{
  "command": "doctor",
  "version": "1.0.1",
  "ok": true,
  "summary": {"ok": 14, "warn": 1, "fail": 0},
  "checks": [{"key": "token_valid", "status": "ok", "title": "Telegram bot token", "detail": ["@example_bot"], "fix": null}]
}
```

`checks` van en este orden, con estas claves: `config_parses`, `config`, `token_valid`, `chat_reachable`, `team`, `role_shell_access`, `peer_messages`, `claude`, `claude_auth`, `dtach`, `hook_scripts`, `settings_json`, `daemon`, `service_installed`, `service_unit_path`, `miniapp`. Una comprobación que se cae conserva su clave con estado `warn`. Los textos son planos (sin marcado), y todo lo que tenga forma de token de bot se cambia por `<redacted>`.

## <a id="command-line-aipager-uninstall"></a>Línea de comandos: `aipager uninstall`

`aipager uninstall` detiene aipager y quita su servicio, borra la carpeta de configuración (`~/.config/aipager`: el token del bot, los chats y los ajustes) y la lista de sesiones (`~/.claude/aipager-sessions.json`), limpia los archivos de aipager en `/tmp`, y desinstala el propio aipager. Pregunta antes (`-y` se salta la pregunta).

Conserva los datos de aipager, para que una reinstalación siga desde donde estaba: `~/.local/share/aipager` (las carpetas de las sesiones, las herramientas descargadas, los archivos de bloqueo y de marca), `~/.local/state/aipager` (los registros de los informes de problemas y el estado de idioma), `~/.claude/aipager-audit.jsonl` (el registro de las respuestas a permisos), `~/.claude/aipager-flood-state.json` (el historial de límites de velocidad de Telegram), `~/.claude/aipager-pending-users.json` (personas que probaron el bot sin acceso) y `~/aipager-users` (las [carpetas raíz](groups.md#root-folders) de los miembros, con su trabajo). Antes de preguntar, enumera los que existen en tu computadora, y al final imprime el comando `rm -rf` que también los borra.

También quita las entradas propias de aipager del `~/.claude/settings.json` de Claude Code: los hooks que ejecutan `aipager-hook` y la línea de estado que ejecuta `aipager-statusline` (una línea de estado tuya se queda). Todo lo demás de ese archivo se queda como estaba, y el archivo tal como estaba se guarda a su lado como `settings.json.bak.<time>`. Si el archivo es un enlace a otro archivo, no se puede leer, no es JSON válido, o no tiene la forma que lee Claude Code, se deja exactamente como está y uninstall dice qué entradas quitar a mano. Un hook tuyo que todavía menciona `aipager-hook` o `aipager-statusline` (por ejemplo un envoltorio `aipager-hook-capped.sh`) también se queda, y uninstall lo nombra. Las sesiones de Claude Code que ya están abiertas siguen ejecutando los hooks que cargaron hasta que las reinicies. `aipager config` vuelve a añadir las entradas después de reinstalar. Tu bot de Telegram y las copias `settings.json.bak.*` más antiguas no se tocan, ni tampoco `skipDangerousModePermissionPrompt` (el ajuste que se salta el aviso de Claude Code antes de ejecutarse con las comprobaciones de permisos apagadas), que pone aipager pero que puede que hayas puesto tú.

## <a id="command-line-aipager-report"></a>Línea de comandos: `aipager report`

`aipager report` crea un informe de problemas del mismo tipo que el botón Informar de un problema del bot, a partir de lo que aipager registró en esta computadora (con menos datos del aipager en marcha: el modo, los chats, las funciones, el tiempo encendido, las sesiones y los detalles de Claude Code quedan desconocidos o vacíos), lo imprime entero, y pregunta `Send this report? [y/N]`. Solo `y` o `yes` lo envían; cualquier otra cosa, o no tener terminal (un script, una tubería, el shell de una sesión de Claude Code), no envía nada. `--note "text"` añade una nota de como mucho 500 caracteres, que se ve en el informe impreso. Sale con 0 cuando el informe se envió, no se envió porque así lo elegiste, o solo se imprimió, y con 1 cuando un envío que confirmaste no salió. Mira [Informes de problemas](problem-reports.md) para qué contiene un informe y adónde va.

## <a id="settings-in-the-environment"></a>Ajustes del entorno

Unos pocos ajustes se leen del entorno de aipager en lugar de `aipager.yaml`. Para el servicio de systemd, añádelos con `systemctl --user edit aipager` debajo de `[Service]` (`Environment=STALE_BUSY_TIMEOUT=900`), y luego `aipager service stop` y `aipager service start`; para el servicio de launchd en macOS, añádelos al `EnvironmentVariables` del plist (`~/Library/LaunchAgents/com.aipager.daemon.plist`; `aipager service install` vuelve a escribir ese archivo, así que vuelve a añadirlos después); para un aipager que arrancas tú, ponlos antes de `aipager start`. Ninguno hace falta para el uso normal.

| Ajuste | Por defecto | Qué cambia |
|---|---|---|
| `AIPAGER_WHISPER_MODEL` | `base` | El modelo de voz para los audios (`tiny`, `base`, `small`, `medium`, ...): más grande es más preciso y más lento. |
| `MINIAPP_PORT` | `8765` | El puerto de la Mini App en `127.0.0.1` (`aipager miniapp enable --port` lo guarda en su lugar). |
| `MINIAPP_ENABLED` | según `aipager.yaml` | `0` desactiva la Mini App y `1` la activa, diga lo que diga `aipager.yaml` (`aipager miniapp enable\|disable` guarda la elección en su lugar). |
| `MINIAPP_PUBLIC_URL` | ninguno | Sirve la Mini App en tu propia URL `https://` en lugar del túnel gestionado ([Modelo de seguridad](security.md#mini-app-tunnel)). |
| `OBSERVER_BOTS` | ninguno | Bots observadores de solo lectura ([bots observadores](observers.md)). |
| `KEEP_FINISHED_CARD` | `1` | `0` hace que «Solo la respuesta (sin tarjeta)» sea la forma de los chats que nunca eligieron una en `/settings`. |
| `STALE_BUSY_TIMEOUT` | `600` | Segundos que una sesión trabajando puede pasar sin noticias antes de que el chat reciba la nota «sigue trabajando (10 min sin novedades)». |
| `PROMPT_HOOK_GRACE_SECONDS` | `8` | Segundos antes de que un mensaje que Claude Code nunca recibió se marque como «Claude Code no recibió el mensaje» ([mensajes libres](#text)). |
| `FINISH_CARD_GRACE_SECONDS` | `0.8` | Cuánto se adelanta la tarjeta terminada a la respuesta en la forma de tarjeta; `0` envía las dos a la vez. |
| `GONE_SESSION_MAX_AGE_DAYS` | `14` | Días que una sesión terminada sigue en `/resume`; `0` las conserva. |
| `STREAM_EDIT_INTERVAL`, `BUSY_EDIT_INTERVAL` | `1.2`, `3.0` | El ritmo de actualización de la tarjeta de actividad mientras muestra texto y mientras está tranquila, en segundos; el límite de Telegram por chat sigue mandando ([solución de problemas](troubleshooting.md#busy-cards-got-slower)). |
| `TYPING_INDICATOR_INTERVAL` | `4.5` | Segundos entre burbujas de «escribiendo…»; `0` las apaga. |
| `TELEGRAM_MAX_RETRY_AFTER` | `90` | La espera de Telegram, en segundos, a partir de la cual aipager trata un límite de velocidad como un bloqueo (no lo bajes; mira [solución de problemas](troubleshooting.md#the-bot-went-quiet-flood-control)). |
| `CLAUDE_RICH_SUMMARIES` | `1` | `0` apaga el formato enriquecido de las respuestas que contienen bloques de código (una opción de reserva que queda de versiones anteriores). |
| `AIPAGER_INTERACTIVE_TIMEOUT` | `300` | Segundos que una solicitud de permiso puede esperar sin noticias de Claude Code antes de que la sesión vuelva a contar como trabajando ([solución de problemas](troubleshooting.md#a-permission-prompt-with-no-news-for-5-minutes)). |
| `AIPAGER_SUBAGENT_SILENCE` | `1800` | Segundos sin noticias tras los cuales se descarta un subagente cuyo final no se vio ([hooks](hooks.md#subagentstart--subagentstop)). |
| `AIPAGER_BG_SHELL_MAX_TRACK` | `7200` | Segundos tras los cuales un comando en segundo plano cuyo final no se vio deja de contarse. |
| `AIPAGER_REPORT_PROMPTS` | activado | `0` apaga las propuestas automáticas de informes de problemas ([informes de problemas](problem-reports.md#when-aipager-offers-a-report)). |
| `AIPAGER_CLAUDE_BIN` | ninguno | El `claude` que hay que ejecutar, cuando hay varios instalados (`claude_path` en `aipager.yaml` manda). |
| `AIPAGER_SOCKET_PATH` | `$XDG_RUNTIME_DIR/aipager.sock` | El socket de control de aipager. Las sesiones que arranca aipager se lo pasan a sus hooks. |
| `AIPAGER_DEBUG` | desactivado | `1` en el entorno de una sesión hace que `aipager-hook` y `aipager-statusline` impriman sus diagnósticos en stderr. |
| `AIPAGER_INSTANCE_DIR`, `AIPAGER_TELEGRAM_API_BASE` | ninguno | Un segundo aipager aislado para pruebas ([arquitectura](architecture.md#isolated-instance-for-testing)). |

## <a id="see-also"></a>Ver también

- [Arquitectura](architecture.md): dónde encaja el bot.
- [Eventos de hook](hooks.md): lo que aipager oye de claude.
- [Solución de problemas](troubleshooting.md): cuando los comandos no funcionan bien.
