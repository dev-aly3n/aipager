# Solución de problemas

<!-- i18n-source: docs/troubleshooting.md @ 33da7624 -->

> Esta página es una traducción de la página en inglés; si no coinciden, la versión en inglés es la correcta. ([English](../../troubleshooting.md))

Fallos frecuentes y cómo arreglarlos. Ante la duda, ejecuta:

```sh
aipager doctor
```

Ejecuta todas las comprobaciones de abajo en orden e imprime la solución recomendada para cada una que falle. Código: `aipager/doctor.py`.

## <a id="another-aipager-daemon-already-owns-the-socket"></a>«Another aipager daemon already owns the socket»

Dos aipager no pueden compartir un mismo socket de control. Este error significa que un aipager sigue en marcha y responde en él (el socket es `$XDG_RUNTIME_DIR/aipager.sock`, o `/tmp/aipager.sock` cuando `$XDG_RUNTIME_DIR` no está puesto). Un archivo de socket que dejó un aipager que se cayó no es un problema: el siguiente arranque lo borra y abre uno nuevo. Primero detén el aipager en marcha:

```sh
aipager service stop          # si funciona como servicio
pkill -f 'aipager start'      # si no
```

Si la comprobación del socket no lo ve, `aipager start` igualmente se niega con `aipager is already running (pid=N)` y nombra su archivo de bloqueo (`~/.local/share/aipager/daemon.lock`): detén ese proceso de la misma forma.

## <a id="telegram-bot-doesnt-respond"></a>El bot de Telegram no responde

1. `aipager status`: si la fila de aipager dice `not running`, arráncalo.
2. `aipager doctor`: las comprobaciones `token_valid` y `chat_reachable` hablan con la API de Telegram de punta a punta y muestran el error exacto.
3. Chat equivocado: ejecuta `aipager config` y elige **Test bot reachability**. Para arreglar un chat privado equivocado, añade el correcto con **Add a DM scope** (mira [encontrar IDs](groups.md#finding-ids-auto-detect)) y luego quita el equivocado en **Edit a scope**.
4. Nunca se pulsó `/start` en el bot: abre el bot en Telegram y pulsa Start una vez.

## <a id="busy-cards-got-slower"></a>Las tarjetas de actividad van más lentas

Telegram permite más o menos un mensaje por segundo en cada chat (y 20 por minuto en un grupo). Cuenta cada mensaje y cada edición. aipager le da a cada chat su propio presupuesto de alrededor de 1 llamada por segundo, con un pequeño margen para varias seguidas, y las tarjetas de actividad de ese chat lo **comparten**. El indicador «escribiendo…» también entra en ese presupuesto, como lo **menos importante** que envía el chat (mira «La burbuja de escribiendo» abajo).

Primero se reserva la parte de la burbuja (una llamada cada 4,5 segundos mientras la tarea más antigua en curso del chat tiene menos de diez minutos, menos a medida que se alarga; mira «La burbuja de escribiendo») y las tarjetas se reparten el resto. Así, una sola tarjeta en un chat privado se actualiza más o menos cada 4,8 segundos; con dos sesiones trabajando en el mismo chat, cada tarjeta más o menos cada 9,7 segundos; con tres, cada 14,5. **Una tarjeta más lenta es el presupuesto funcionando, no un error.** Las respuestas, las réplicas y las reacciones a los botones nunca se frenan para hacerle sitio a una tarjeta: tienen una parte reservada, y una edición de tarjeta que no tiene presupuesto simplemente se salta y se reintenta en el siguiente tic, en lugar de ponerse en cola delante de tu respuesta.

En un **grupo** el límite es de 20 llamadas por minuto, haya las sesiones que haya, y la burbuja sola usaría dos tercios. Las tarjetas del grupo juntas mantienen una actualización cada 10 segundos (11 s para una tarjeta), y la burbuja se queda con el resto, así que en un grupo puede cortarse un segundo o dos de vez en cuando.

Para que las tarjetas vayan más rápido: ejecuta menos sesiones a la vez por chat, o dales a las más activas un chat propio (`aipager config`). También puedes ajustar `STREAM_EDIT_INTERVAL` (por defecto `1.2`, el ritmo mientras la tarjeta muestra texto) y `BUSY_EDIT_INTERVAL` (por defecto `3.0`, cuando está tranquila), como variables de entorno de aipager (mira [ajustes del entorno](commands.md#settings-in-the-environment)). Ponerlas *por debajo* del mínimo de cada chat no hace daño y no cambia nada: el mínimo tiene prioridad, y es lo que mantiene el chat dentro del límite de Telegram pongas lo que pongas.

### <a id="a-long-turns-card-refreshes-less-often"></a>La tarjeta de una tarea larga se actualiza menos

El ritmo de arriba es para los dos primeros minutos de una tarea. Después, **la tarjeta va más despacio a medida que la tarea se alarga**, y desde los 10 minutos su reloj avanza de 30 en 30 segundos, al ritmo de las actualizaciones, para que nunca parezca colgada:

| tiempo de la tarea | la tarjeta se actualiza como mucho cada | el reloj marca |
|---|---|---|
| 0–2 min | como arriba (unos 4,8 s para una tarjeta en un chat privado) | `0:45` |
| 2–10 min | 10 s | `4:10` |
| 10–60 min | 30 s | `23:00`, `23:30` |
| más de 60 min | 30 s | `1:23:00`, `1:23:30` |

El reloj se redondea hacia abajo a su paso de 30 s, y las actualizaciones están al menos a 30 s unas de otras, a veces un poco más. Así que la tarjeta se salta un paso de vez en cuando: una actualización a los 23 min 29 s muestra `23:00`, y si la siguiente llega a los 24 min 1 s muestra `24:00`, así que `23:30` no se ve.

Una fila de herramienta o una frase nueva esperan a la siguiente actualización; un **cambio de estado** (la sesión pasa de trabajar a esperar a un agente en segundo plano, y al revés) se muestra enseguida, como mucho una vez cada 10 segundos. Las filas en vivo de los agentes y la línea de espera usan los mismos pasos. La tarjeta terminada muestra la duración de la tarea en segundos reales (`23:17`).

Esto es lo que evita que una tarea de cuatro horas edite un mensaje miles de veces: el 2026-09-23 una tarjeta hizo justo eso, y el chat quedó bloqueado siete horas. Para mantener el ritmo rápido durante toda la tarea, desactiva **`/settings` → ⏱ Actualizar tarjeta en tareas largas** (para el chat, o para una sesión desde la Mini App o `👤 Ajustes de cada sesión`).

### <a id="the-typing-bubble"></a>La burbuja de «escribiendo…»

La burbuja de «escribiendo…» la envía **un solo** bucle por chat, haya las sesiones que haya trabajando en él (la burbuja es de todo el chat en todas las apps de Telegram), cada `TYPING_INDICATOR_INTERVAL` segundos (por defecto 4,5; Telegram borra el estado de escribiendo después de 5; `0` apaga la burbuja). En el presupuesto del chat es lo menos importante que se envía:

- **va más despacio a medida que la tarea se alarga**, según el tiempo de la tarea más antigua en curso del chat: cada 4,5 s los primeros diez minutos (una burbuja fija), cada 15 s después; Telegram la muestra 5 s, así que la burbuja de una tarea más larga parpadea en lugar de quedarse encendida. A 4,5 s ella sola serían 800 llamadas por hora y el límite por hora (abajo) la apagaría durante largos ratos; desactivar **⏱ Actualizar tarjeta en tareas largas** la mantiene a 4,5 s;
- **se ve desde el principio de cada tarea**; una tarjeta recién creada se actualiza un poco más despacio para hacerle sitio: la parte de la burbuja se reserva antes de que las tarjetas se repartan el resto (arriba), así que las dos juntas nunca pasan los límites del chat;
- nunca le quita la parte a una edición de tarjeta: una burbuja que toca justo después de una edición espera medio segundo (`TYPING_RETRY_WAKE`) y lo vuelve a intentar, así que llega como mucho uno o dos segundos tarde, nunca falta;
- es lo primero que se deja de enviar cuando el volumen por hora del chat sube (abajo);
- un 429 en la propia burbuja bloquea solo la burbuja, el tiempo que pidió Telegram (las tarjetas siguen), pero sí empieza el periodo de aviso de seis horas del chat (el ritmo queda limitado a 0,5 llamadas/s, así que las tarjetas también van más lentas);
- un **bloqueo** en la burbuja (un `retry_after` por encima del límite de abajo) silencia el chat igual que un bloqueo en cualquier otra llamada: no se envía nada más, y la respuesta de la tarea se retiene y se entrega cuando el bloqueo termina.

Antes era gratis (un bucle por sesión trabajando, fuera de todo presupuesto), hasta que el 2026-09-23 dos sesiones en un chat privado la enviaron unas 5400 veces en tres horas y Telegram limitó la propia burbuja.

## <a id="the-bot-slowed-down-a-rate-limit-429"></a>El bot va más lento: un límite de velocidad (429)

Si Telegram limita la velocidad de un chat de todas formas, responde a una llamada con `429` y un `retry_after` de unos segundos. aipager deja de llamar a ese chat exactamente ese tiempo, vuelve a hacer después el único envío aplazado, y **duplica el intervalo de las tarjetas de ese chat** (hasta ×8). Cada minuto tranquilo lo reduce a la mitad hasta volver a lo normal.

Lo que ves:

- `aipager status` (y la fila `aipager daemon` de `aipager doctor`) muestra **`Telegram flood backoff ×4 (chat …), last 429 12 s ago`**.
- Exactamente una línea en `aipager logs`, sin traceback: `flood: chat … 429 retry_after=5s → cadence ×2`.
- No se silencia nada y **no se pierde ninguna respuesta**: se aplaza, no se descarta.
- **El ritmo que se había ganado el chat se reduce a la mitad**, y el chat entra en un **periodo de aviso de seis horas** (`FLOOD_WARNING_HOURS`): su ritmo no puede pasar de 0,5 llamadas/s (`FLOOD_WARNED_CEILING`), y sube despacio. `aipager status` lo muestra: `Telegram chat 123: rate 0.25/s (ceiling 0.50/s), 212/1200 calls in the last hour (as of 18s ago), 7/30 in the last minute - warning regime, 5h 52m left`.

No tienes que hacer nada. Se arregla solo. La línea del backoff desaparece cuando el chat lleva un minuto o dos tranquilo; el ritmo sube 0,1 llamadas/s por cada rato tranquilo (un minuto normalmente, 36 minutos durante el periodo de aviso) y el máximo completo vuelve cuando termina el periodo. `aipager doctor` mantiene en verde la fila de aipager todo el tiempo: un chat que frena es funcionamiento normal. (Antes, un 429 se perdonaba en unos cinco minutos. El único aviso de Telegram antes del bloqueo del 2026-09-23 llegó 3 h 23 min antes, y el chat volvió a toda velocidad a los pocos minutos.)

### <a id="the-hourly-budget"></a>El presupuesto por hora

Cada chat también tiene una **hora** móvil: como mucho `FLOOD_HOURLY_MAX` (1200) llamadas en cualquier ventana de 60 minutos; burbujas de escribiendo, ediciones de tarjetas y respuestas, todo junto; solo las reacciones no cuentan. Las últimas 120 (`FLOOD_HOURLY_ESSENTIAL_RESERVE`) se reservan para respuestas, réplicas y solicitudes:

- con 810 llamadas en la hora, la burbuja de escribiendo se pausa (vuelve por debajo de 648);
- con 1080, el chat entra en **modo mínimo** (abajo) hasta que la hora baja a 864;
- las respuestas nunca se rechazan por la hora; si alguna vez pasan de 1200, el log lo dice una vez por hora.

`aipager status` muestra las llamadas del chat en la última hora frente a su presupuesto. El dato sale del archivo de estado, que aipager reescribe como mucho una vez por minuto mientras hay llamadas, así que va con su antigüedad. Dos sesiones activas enviando a un chat privado durante horas quedan muy por debajo del presupuesto (el incidente del 2026-09-23, repetido: unas 900 llamadas en la hora más activa, frente a unas 3400 calculadas para el mismo trabajo sin él).

### <a id="the-earned-rate-and-why-your-cards-may-be-slower-than-they-were"></a>El ritmo ganado, y por qué tus tarjetas pueden ir más lentas que antes

Desde la 0.7.13, el ritmo sostenido de envío de cada chat **se aprende en lugar de suponerse**. Telegram no publica el límite que de verdad se aplica a tu bot (depende de la historia reciente de tu cuenta), así que aipager empieza cada chat a 0,5 llamadas/s, suma 0,1 por cada minuto tranquilo hasta un máximo de 1/s, lo divide entre dos con un 429 y lo baja a 0,05 después de un bloqueo. Un bot al que acaban de bloquear va así mucho más despacio que uno que no, y recupera su velocidad en las horas siguientes.

Si el ritmo de un chat baja de 0,2 llamadas/s (o se gasta la parte de su presupuesto por hora para tarjetas y burbujas), entra en **modo mínimo**: las tarjetas de actividad dejan de animarse y muestran una sola línea fija `⏳ x1 · trabajando (la tarjeta no se actualiza por ahora)`, la burbuja de escribiendo se detiene, y la barra de estado fijada muestra `⏸ las tarjetas no se actualizan por ahora (límite por hora de Telegram)` (o `⏸ las tarjetas no se actualizan por ahora (límite de Telegram)` cuando fue el ritmo lo que lo llevó ahí) y por lo demás no cambia hasta que se levanta. **Las respuestas, las réplicas y las solicitudes de permiso siguen llegando**: de eso se trata. La tarjeta es alrededor del 95 % de lo que envía este bot y la respuesta alrededor del 5 %, así que bajo presión sacrifica la apariencia y no el trabajo. `aipager doctor` informa del modo mínimo como advertencia para que una tarjeta en pausa nunca se confunda con una rota; se levanta solo cuando el ritmo se recupera.

## <a id="the-bot-went-quiet-flood-control"></a>El bot se quedó callado: un bloqueo de Telegram (flood control)

Un `retry_after` por encima de `TELEGRAM_MAX_RETRY_AFTER` (por defecto 90 s) no es un límite de velocidad: es un **bloqueo**, y puede durar horas. Desde el chat parece que el bot murió: los mensajes siguen llegando a Claude, las sesiones siguen trabajando, pero no vuelve ninguna respuesta.

Lo que ves:

- `aipager status` (y la fila `aipager daemon` de `aipager doctor`) muestra **`Telegram flood-muted until HH:MM (chat …)`**.
- Una línea en `aipager logs`: `Telegram flood control — chat … muted for Ns (until HH:MM) …`, y luego silencio para ese chat. Después, una línea `flood mute on chat … lifted`.
- `aipager status` también muestra el estado completo del chat: `Telegram chat 123: rate 0.05/s (ceiling 0.50/s), 431/600 calls in the last hour (as of 12s ago) - MINIMAL MODE, card updates paused - flood-muted until 09:41 - warning regime, 5h 59m left (1 ban(s) in the last 7 days)`.

aipager silencia el chat exactamente el tiempo que pidió Telegram y **no hace ninguna llamada de ningún tipo** a él: respuestas, ediciones de tarjetas, adjuntos, reacciones, indicadores de escribiendo, avisos cortos tras tocar un botón y las réplicas que habrían producido tus comandos. Cada una sería una infracción nueva que alarga el bloqueo; una petición en medio de un bloqueo activo fue lo que convirtió un bloqueo de 21 minutos en uno de 9,5 horas el 2026-09-15. Así que un comando escrito durante el silencio no responde nada, y **tocar un botón tampoco recibe confirmación**: hasta la 0.7.12 el aviso corto seguía saliendo, con la idea de que Telegram lo cuenta aparte. Lo cuenta aparte para el ritmo; para los bloqueos, no. Los demás chats no se ven afectados.

**Escribirle al bot durante un bloqueo es seguro, y no se pierde nada.** Nada de lo que hagas en el chat hace que el bot llame a Telegram mientras el chat está silenciado, así que no puede alargar el bloqueo:

- Un mensaje que envías (texto, audio, un archivo, una plantilla) igual llega a Claude y la sesión trabaja en él. Su tarjeta de actividad se retiene, no se descarta: si la tarea sigue en curso cuando el bloqueo termina, la tarjeta aparece entonces. Un bloqueo deja el chat en modo mínimo un rato después, así que la tarjeta puede tardar unos minutos más que el propio bloqueo. Si la tarea terminó durante el bloqueo, recibes su respuesta y ninguna tarjeta.
- **🔄 Reintentar** no hace nada durante un bloqueo. El mensaje no se reenvía y el botón se queda, para que lo vuelvas a tocar cuando el bloqueo termine. Hasta la 0.7.14 incluida, el mensaje llegaba a Claude mientras el mensaje de error y su botón se quedaban, así que un segundo toque lo volvía a enviar.
- El teclado principal también se retiene, incluido el que envía un reinicio (hasta la 0.7.14 incluida se perdía hasta que otra cosa lo actualizaba). Se envía cuando termina el bloqueo.

Cuando el bloqueo termina, las cosas llegan en este orden: las respuestas retenidas, luego las tarjetas de las tareas que siguen en curso, luego el teclado. Una tarjeta que tiene que esperar a que termine el modo mínimo llega después del teclado.

**Las respuestas no se pierden.** Una respuesta que se produce mientras el chat está silenciado se retiene y se entrega en un par de segundos cuando termina el bloqueo, con la primera línea `⏳ llegó tarde: retenido 42 min por un límite de Telegram`. No hace falta que vuelvas a preguntar, y si volviste a preguntar llegan las dos respuestas, la más antigua primero: un bloqueo de horas abarca varias tareas y cada respuesta es tuya. (Las respuestas retenidas viven en memoria: si reinicias aipager mientras hay alguna esperando, se pierden; el log de apagado dice cuántas. Un chat retiene como mucho 20, y nada de más de 24 horas; lo que se descarta por cualquiera de esas razones es una advertencia en el log que dice qué era.)

**Un bloqueo deja el chat más lento después, no más rápido.** En cuanto empieza un bloqueo, el ritmo aprendido del chat baja al mínimo y el bloqueo se cuenta; las horas en silencio no recuperan nada, la subida vuelve a empezar desde el momento en que termina el bloqueo, y **durante siete días** (`FLOOD_BAN_MEMORY_DAYS`) el máximo de ritmo del chat *y* su presupuesto por hora se dividen entre uno más el número de bloqueos de esa semana: un bloqueo los reduce a la mitad, dos a un tercio. `aipager status` lo muestra todo. (Antes esa memoria duraba 24 horas y solo reducía a la mitad el máximo; un chat bloqueado el 2026-09-19 volvió a toda velocidad, y quedó bloqueado siete horas, el 2026-09-23.) Cinco bloqueos o más en una semana dejan el chat en modo mínimo hasta que caducan: las respuestas siguen llegando, las tarjetas siguen en pausa.

Lo que NO hay que hacer:

- No reinicies aipager para «arreglarlo». El silencio se levanta solo a la hora que se muestra. Desde la 0.7.13 un reinicio ya no olvida el bloqueo (el plazo, el ritmo ganado del chat y su historial de bloqueos se guardan en `~/.claude/aipager-flood-state.json` y se recuperan al arrancar, igual que las llamadas de la última hora del chat y su periodo de aviso), así que reiniciar tampoco lo alarga. Pero sí descarta las respuestas que todavía estén retenidas, y no arregla nada.
- No bajes `TELEGRAM_MAX_RETRY_AFTER` por debajo de los 90 s por defecto con la esperanza de reintentar antes: todo lo que pasa de ese límite es un bloqueo, no un límite de velocidad.

Para evitarlo: ejecuta menos sesiones a la vez por chat, o dales a las más activas un chat propio (`aipager config`).

## <a id="a-session-dropped-off-the-pinned-status-bar-it-is-gone"></a>Una sesión desapareció de la barra de estado fijada (está GONE)

El proceso de dtach de esa sesión terminó (reinicio de la computadora, `pkill claude`, alguien escribió `exit` en la sesión conectada a dtach). Créala de nuevo; su carpeta `~/.claude/projects/...` todavía tiene la conversación:

La forma más fácil es `/resume <label>` en Telegram (o `/resume` solo, para elegir de una lista): vuelve a arrancar la sesión y retoma la conversación. Desde la línea de comandos:

```sh
aipager resume <label>             # lo mismo, desde la terminal
aipager session <label>            # o una sesión nueva, sin historial
aipager session <label> --continue # dtach nuevo, la última conversación de la carpeta
```

El aviso en el chat dice por qué terminó la sesión, a partir del código de salida que dejó `claude` (aipager lo guarda en `claude-exit-<session>.json` cuando `claude` termina):

- `🔴 x1 · La sesión falló o la terminaron`: no se guardó ningún código de salida (la computadora se reinició, o el proceso de la sesión terminó junto con él).
- `🔴 x1 · Sesión terminada`: `claude` terminó con normalidad (código de salida 0).
- `🔴 x1 · La sesión terminó con el código de error 2`: `claude` falló con ese código.
- `🔴 x1 · La sesión terminó por la señal SIGKILL`: algo terminó `claude` a la fuerza (los códigos de salida por encima de 128 indican la señal).
- `🔴 x1 · Claude Code se cerró justo después de iniciarse (código de salida 1)`: `claude` se rindió en los 10 segundos siguientes a arrancar, casi siempre por sus propios argumentos o su configuración. Conéctate con `aipager session x1` para ver su mensaje, o arráncalo a mano en una terminal.
- `🔴 x1 · Claude Code se cerró justo después de iniciarse porque su conversación se está ejecutando en el segundo plano de Claude Code`: Claude Code se niega a continuar una conversación que todavía tiene una tarea en segundo plano. Toca `↩️ Traerla de vuelta ahora`: aipager detiene esa tarea si no está haciendo nada (si está trabajando, pregunta antes) y continúa la conversación aquí. Mira la siguiente sección.

## <a id="a-session-moved-to-claude-codes-background"></a>Una sesión pasó al segundo plano de Claude Code

`🌙 x1 pasó al segundo plano de Claude Code, y tu próximo mensaje la trae de vuelta aquí.`

Pulsar ← en una entrada vacía de Claude Code pasa la conversación de la sesión a una tarea en segundo plano de Claude Code, y la terminal muestra la vista de agentes de Claude Code en lugar de la conversación. aipager lo nota en un par de segundos y deja de escribir en esa terminal: cualquier cosa que se escriba ahí ahora podría arrancar otra tarea en segundo plano. La tarea sigue trabajando, y su tarjeta, su respuesta y sus solicitudes de permiso siguen llegando al chat como las de la sesión. En la Mini App la sesión dice «en segundo plano», o «te necesita» mientras su tarea espera una solicitud de permiso que puedes responder desde el chat.

Para traerla de vuelta:

- Envíale un mensaje. El mensaje espera (👀), aipager detiene la tarea en segundo plano (`claude stop <job>`), reinicia la sesión con la conversación de la tarea y los ajustes propios de aipager, y luego envía el mensaje. Si la tarea todavía está trabajando, o si el trabajo que se movió con ← sigue en marcha, primero se te pregunta: `Detener y traer de vuelta` detiene lo que está haciendo, `Dejarla así` la deja terminar (tráela de vuelta después). En los primeros segundos después de ← una tarea en segundo plano nueva puede parecer quieta justo antes de seguir con ese trabajo, así que un mensaje enviado entonces espera un momento antes de que aipager decida. Alguien cuyo rol no puede reiniciar la sesión recibe `Tu mensaje para x1 espera hasta que alguien traiga la sesión de vuelta del segundo plano de Claude Code.`
- O toca `↩️ Traerla de vuelta ahora` bajo el aviso, `/restart x1`, o Traerla de vuelta en la Mini App. `/stop` mientras la tarea trabaja detiene ese trabajo y trae la sesión de vuelta. Un botón que quedó de un paso anterior al segundo plano responde `x1 no está en el segundo plano de Claude Code.` y no hace nada. Cuando un cambio de modo que reinicia la sesión (para pasar a Modo Acceso total o para salir de él) se encuentra con una tarea que está trabajando, `Detener y traer de vuelta` trae la sesión de vuelta en el modo que pediste.

Cuando una regla de seguridad detiene el trabajo de la sesión mientras está en segundo plano, aipager detiene la tarea en segundo plano. Si no puede, el aviso de la detención dice que el trabajo bloqueado puede seguir en marcha ahí, y su primera línea no termina en `(detenida)`; detén la tarea con `claude stop <job>` en una terminal.

Hasta entonces, los comandos que escribirían en la sesión (`/compact`, `/model`, un cambio a Modo Preguntar o Modo Automático, `/now`, Reintentar) responden `Primero trae x1 de vuelta del segundo plano de Claude Code.`, y la respuesta a una solicitud que aipager tendría que escribir recibe `Para responder, trae x1 de vuelta del segundo plano de Claude Code.` Las respuestas que pasan por el hook (la mayoría de las solicitudes de permiso) siguen funcionando.

`/resume`, Continuar en la Mini App y `aipager resume` siguen igual a una conversación que pasó a otra: se continúa la conversación más reciente, y una tarea en segundo plano que todavía la tenga se detiene antes si no está haciendo nada. `aipager session x1 --resume <id>` hace lo mismo en la terminal; cuando la tarea está trabajando, imprime `claude stop <job>` y `claude attach <job>` y sale con el código 1 sin arrancar nada.

## <a id="a-permission-prompt-with-no-news-for-5-minutes"></a>Una solicitud de permiso sin noticias durante 5 minutos

Si una sesión espera una solicitud de permiso o una pregunta y Claude Code no da noticias durante 5 minutos, aipager lee la conversación guardada de la sesión. Solo cuentan las noticias de Claude y las del agente que preguntó: un agente en segundo plano que sigue trabajando no impide esta comprobación. Solo si muestra que la solicitud terminó (se respondió en la terminal y se perdió el aviso), aipager deja de esperarla y vuelve a contar la sesión como trabajando. Si no, la solicitud sigue abierta lo que haga falta: la tarjeta y la barra fijada siguen diciendo que la sesión necesita tu respuesta, no llega ningún aviso de «sigue trabajando», y tus mensajes se retienen hasta que respondas (mira [texto](commands.md#text)). Un Claude Code que se cierra con la solicitud abierta termina la sesión. Si el diálogo sigue abierto, los botones de la solicitud todavía lo responden (mira [respuestas](commands.md#idle-responses)).

Cambiar la espera:

```sh
AIPAGER_INTERACTIVE_TIMEOUT=300 aipager start    # en segundos
```

## <a id="voice-extra-wont-install-via-telegram"></a>La función de voz no se instala desde Telegram

El botón `[📦 Instalar la función de voz]` ejecuta el instalador y edita su mensaje cada pocos segundos mientras trabaja (`📦 Instalando la función de voz - sigue en curso… (25 s)`). Si la instalación falla, el mensaje muestra el código de salida y los últimos 500 caracteres de la salida del instalador. Causas habituales:

- Red: pip no llega a PyPI. Comprueba la conexión desde la computadora donde funciona aipager.
- Disco: `~/.cache/huggingface/` necesita unos 74 MB para el modelo y la caché de wheels de pip otros 250 MB. Libera espacio antes.
- Permission denied: un venv de solo lectura. Pasa a `uv tool install`, que siempre usa un venv de tu usuario.

Si el bot perdió la conexión en mitad de la instalación, la instalación en sí suele haber terminado en la computadora: reinicia aipager y vuelve a probar con un audio.

## <a id="open-app-shows-open-this-page-from-the-telegram-app-to-sign-in"></a>Abrir la app muestra «Abre esta página desde la app de Telegram para entrar.»

La página cargó, pero el script de la Mini App de Telegram (el que produce `initData`) no, así que la página no tiene con qué entrar y no hace ninguna llamada a la API. El mensaje te echa la culpa a ti; la causa es de red.

Las versiones recientes de aipager descargan ellas mismas ese script y lo sirven desde la misma dirección de la página (`/telegram-web-app.js`), así que el teléfono solo tiene que llegar al sitio que entregó la página, y no también a `telegram.org`. Si ves esto en una versión anterior, actualiza.

Después, en orden:

1. Mira el código fuente de la página. Si su etiqueta `<script>` apunta a `https://telegram.org/js/telegram-web-app.js`, aipager todavía no consiguió descargar el script y la página lo está cargando desde Telegram, que es justo el caso que falla en un teléfono que no llega a `telegram.org`. aipager lo descarga cuando arranca el servidor de la Mini App y lo reintenta como mucho una vez al día, así que reiniciar aipager lo reintenta enseguida; `journalctl --user -u aipager | grep "webapp sdk"` dice qué falló (normalmente un `telegram.org` bloqueado o filtrado).
2. Si la etiqueta apunta a `/telegram-web-app.js`, comprueba que aipager lo sirve: `curl -sI http://127.0.0.1:8765/telegram-web-app.js` en la computadora de aipager debería responder `200` con `Content-Type: application/javascript` (ajusta el puerto si lo cambiaste). La copia está en `~/.local/share/aipager/webapp-sdk/`.
3. Abriste la dirección en un navegador normal en lugar de con el botón `/app` o el botón de menú del bot: entonces ese es el mensaje esperado. El SDK solo produce `initData` dentro de Telegram.
4. La página estuvo abierta más de cinco minutos: `initData` caduca y la página pide que la vuelvas a abrir. Toca `/app` otra vez.
5. Tocaste `📱 App` en un teclado de una versión anterior de aipager. Telegram no le da `initData` a una Mini App abierta desde un botón del teclado, así que el teclado de aipager ya no tiene ese botón. Envía `/start` para tener el teclado actual y abre la app con el botón de menú o con `/app`.

## <a id="pyexpat-_xml_setalloctrackeractivationthreshold-on-brew-install"></a>`pyexpat _XML_SetAllocTrackerActivationThreshold` al instalar con brew

El paquete `python@3.12` de Homebrew se compiló con una libexpat más nueva que la de tu sistema; casi siempre porque **Xcode y Command Line Tools están desactualizados** en macOS Tahoe (26.x). La propia salida de brew suele decirlo. Dos soluciones:

- **Usa `uv tool install aipager`** en su lugar. uv trae su propio python y esquiva el problema del todo. Es la vía recomendada en macOS; mira el [README](README.md#install).
- **Actualiza Xcode y Command Line Tools**:
  ```sh
  sudo rm -rf /Library/Developer/CommandLineTools
  sudo xcode-select --install
  ```
  O abre Xcode en la App Store y actualízalo a la última versión.

## <a id="modulenotfounderror-aipager"></a>«ModuleNotFoundError: aipager»

El ejecutable de aipager apunta a un Python que ya no tiene aipager instalado (a menudo después de borrar un venv). Reinstálalo:

```sh
uv tool install --reinstall aipager
# o con el instalador que usaste al principio: pipx, brew, pip
```

## <a id="the-session-list-was-lost-or-damaged"></a>La lista de sesiones se perdió o se dañó

aipager guarda sus sesiones en `~/.claude/aipager-sessions.json` y lo reescribe de forma atómica (un archivo nuevo y luego un cambio de nombre), así que una escritura interrumpida deja entero el archivo anterior. Si aun así el archivo no está, no se puede leer o no es JSON válido, aipager escribe en el log que empieza de cero (como advertencia cuando el archivo estaba pero no se pudo leer) y arranca con la lista vacía; no se detiene. No hay copias de seguridad de este archivo. No se pierde nada importante:

- las sesiones en marcha se vuelven a encontrar en el primer tic del vigilante, por sus sockets de dtach (`/tmp/claude-dtach-*.sock`);
- todas las conversaciones siguen en la carpeta `~/.claude/projects/...` del propio Claude Code, así que `aipager session <name> --continue` en la carpeta de esa sesión la retoma (`--resume` te deja elegir una);
- lo que se pierde es la lista de sesiones terminadas (`/resume`), las tarjetas que estaban abiertas, y el resto de lo que aipager recordaba de cada sesión.

Si aipager sigue fallando al arrancar, `aipager logs` (o `journalctl --user -u aipager`) muestra por qué; aparta el archivo (`mv ~/.claude/aipager-sessions.json ~/aipager-sessions.json.old`) para descartarlo.

## <a id="a-session-launch-fails"></a>Falla el arranque de una sesión

Un arranque que dtach rechaza muestra el motivo en una línea en el chat (el socket ya existe, el shell no se puede ejecutar, no hay pseudoterminal, el nombre es demasiado largo, permiso denegado, falta la carpeta); el mensaje propio de dtach, con las rutas, está en `aipager logs`.

## <a id="aipager-doctor-check-list"></a>Lista de comprobaciones de `aipager doctor`

El orden importa: cada comprobación posterior supone que las anteriores pasaron. Una comprobación que se cae en un entorno inesperado se muestra como una sola fila ⚠ con el nombre de la comprobación y el error; las demás comprobaciones se siguen ejecutando.

| Comprobación (clave de `--json`) | Qué comprueba | Solución |
|---|---|---|
| `config_parses` | `aipager.yaml` y `policy.yaml` se pueden leer; va primero, porque todas las demás leen la configuración | el error nombra el archivo y la línea |
| `config` | `~/.config/aipager/aipager.yaml` existe y tiene un token y un chat | `aipager config` |
| `token_valid` | El token funciona con `getMe` de Telegram | vuelve a ejecutar `aipager config` |
| `chat_reachable` | Telegram conoce el chat configurado (`getChat`; no se envía nada) | abre el bot y pulsa Start |
| `team` | Los chats y los miembros (o un `team.yaml` antiguo): dice si es uso personal o en equipo, y señala un `team.yaml` antiguo roto o que no encaja | `aipager config` |
| `role_shell_access` | Avisa una vez por cada rol que tiene un miembro y puede ejecutar Bash (sus reglas de seguridad son de mejor esfuerzo, no un límite; mira [uso en equipo](groups.md#roles)) | no le des Bash a ese rol en `policy.yaml`, o acéptalo |
| `peer_messages` | Mensajes entre sesiones de Claude: dónde se guardan sus recibos y si esa carpeta es privada, si este sistema puede comprobar la hora de inicio del remitente, y un aviso por cada rol al que una política le deja usar SendMessage (mira [Modelo de seguridad](security.md#messages-between-claude-sessions)) | borra una carpeta de recibos compartida (aipager la vuelve a crear), o no le des SendMessage a ese rol |
| `claude` | Encuentra el ejecutable `claude` igual que cada arranque (`claude_path` en `aipager.yaml` o `$AIPAGER_CLAUDE_BIN` ganan siempre; si no, la versión más nueva entre `~/.local/bin`, el PATH y Homebrew), y lista cualquier OTRA instalación distinta que encuentre | instala Claude Code, o pon `claude_path` / `AIPAGER_CLAUDE_BIN` |
| `claude_auth` | Ejecuta `claude auth status` en el mismo entorno que recibe una sesión real. **Nunca da FAIL**: «sin autenticar» y «la propia comprobación falló» se informan por separado, y ninguno impide arrancar una sesión. También avisa cuando a Claude Code se le dijo que no usara el `ANTHROPIC_API_KEY` de las sesiones y nada más las autentica. | `claude auth login`, o pon una clave de API / `CLAUDE_CODE_OAUTH_TOKEN`; con `ANTHROPIC_BASE_URL` definido, la clave de esa dirección ([proveedores](#using-an-api-key-a-gateway-or-another-provider)) |
| `dtach` | Se encontró el ejecutable `dtach` (el `dtach-bin` propio de aipager, o uno en el PATH) | `uv tool install --reinstall aipager` |
| `hook_scripts` | `aipager-hook` y `aipager-statusline` están en el PATH | `uv tool install --reinstall aipager` |
| `settings_json` | `~/.claude/settings.json` (o `$CLAUDE_CONFIG_DIR/settings.json`) tiene conectados los hooks de aipager; con `CLAUDE_CONFIG_DIR` definido, nombra el archivo y de dónde vino el valor | `aipager config` |
| `daemon` | aipager está en marcha y su socket responde; también muestra el estado de los límites de velocidad de Telegram (mira [arriba](#the-bot-slowed-down-a-rate-limit-429)) | `aipager start` |
| `service_installed` | Opcional: el archivo del servicio existe | `aipager service install` |
| `service_unit_path` | Solo Linux: el `Environment=PATH=` del servicio *instalado* contiene de verdad la carpeta del ejecutable de claude encontrado; se lee como texto, porque doctor no puede ver el PATH propio de systemd desde tu shell interactivo | `aipager service install --yes` |
| `miniapp` | Si la Mini App puede arrancar cuando está activada. **Nunca da FAIL**: la Mini App es opcional | la solución que indica |

`aipager doctor --json` imprime las mismas comprobaciones, en este orden, con estas claves (mira [comandos](commands.md#aipager-doctor---json)).

Ejecuta `aipager doctor --fix` para buscar de forma interactiva una credencial de Claude y copiarla en `daemon.env` (también cuando el archivo existe pero no tiene ninguna), o para fijar `claude_path` cuando hay más de una instalación. Siempre pregunta antes de hacer nada. Para un servicio cuyo PATH no tiene la carpeta de claude, ejecuta `aipager service install --yes`.

## <a id="the-daemon-cant-find-claude-or-picks-the-wrong-install"></a>aipager no encuentra `claude`, o elige la instalación equivocada

Antes había seis sitios que buscaban `claude` por su cuenta y podían no coincidir entre sí. Ahora todos pasan por un solo buscador (`aipager/claude_resolve.py`). `claude_path` en `aipager.yaml`, y luego `$AIPAGER_CLAUDE_BIN`, ganan siempre cuando funcionan. Si no, se comprueba cada `claude` que haya en `~/.local/bin`, en el `$PATH` y en las carpetas de Homebrew, y gana la versión más nueva (así que cambiar el orden del `$PATH` no cambia la elección cuando la otra instalación es más nueva). Ejecuta `aipager doctor` para ver exactamente cuál eligió y qué más encontró; si gana la equivocada, fija la correcta:

```sh
aipager doctor --fix        # elegir de forma interactiva entre las instalaciones encontradas
# o a mano:
echo 'claude_path: /home/you/.local/bin/claude' >> ~/.config/aipager/aipager.yaml
```

## <a id="sessions-cant-authenticate-under-the-systemd-service-but-claude-works-fine-in-a-terminal"></a>Las sesiones no pueden autenticarse bajo el servicio de systemd, pero `claude` funciona bien en una terminal

Los servicios `systemctl --user` nunca leen `~/.bashrc` ni `~/.profile`, así que una línea `export CLAUDE_CODE_OAUTH_TOKEN=…` ahí nunca llega a aipager. El servicio usa en su lugar el `LoadCredential=` de systemd y lee `~/.config/aipager/daemon.env`: un archivo sencillo `KEY=VALUE`, con permisos 0600, que se crea solo la primera vez que ejecutas `aipager service install` (se copia de un `config.env` antiguo si tenía un token, si no se busca una vez en tu shell de login, y si no se deja vacío con una advertencia). Si sigue vacío, `aipager doctor --fix` vuelve a buscar en los mismos sitios cuando le dices que sí y añade la línea que encuentra, conservando las demás líneas del archivo; nunca imprime el token. O añade la línea tú:

```sh
cat ~/.config/aipager/daemon.env          # mira qué hay (o qué no hay)
echo 'CLAUDE_CODE_OAUTH_TOKEN=sk-...' >> ~/.config/aipager/daemon.env
chmod 600 ~/.config/aipager/daemon.env
aipager service stop && aipager service start
```

Mira [Modelo de seguridad → la credencial de Claude](security.md#the-claude-credential-what-actually-protects-it) para saber de qué protege este archivo y de qué no.

## <a id="using-an-api-key-a-gateway-or-another-provider"></a>Con una clave de API, un gateway u otro proveedor

aipager ejecuta Claude Code tal como lo configuraste, así que una sesión iniciada desde Telegram se autentica igual que `claude` en tu terminal: con un inicio de sesión de claude.ai, una clave de API de Anthropic, un gateway (LiteLLM, Vercel, Cloudflare y similares) o la dirección con formato de Anthropic de otra empresa puesta en `ANTHROPIC_BASE_URL`. Anthropic solo da soporte a Claude Code con modelos de Claude; otros modelos pueden comportarse de otra forma.

- **Dónde van los ajustes.** En el bloque `env` de `~/.claude/settings.json` (o `$CLAUDE_CONFIG_DIR/settings.json`), que lee toda sesión de Claude Code, o en `~/.config/aipager/daemon.env`, que solo reciben las sesiones que inicia aipager: una línea `KEY=value` por ajuste, sin comillas (pasarían a formar parte del valor). Un servicio (`aipager service install`) nunca lee el perfil de tu shell, así que un `export` ahí no le llega. Reinicia aipager después de cambiar `daemon.env`. Los bloques `env` del `.claude/settings.json` y el `.claude/settings.local.json` de una carpeta de inicio de confianza y de los ajustes administrados (`managed-settings.json` y `managed-settings.d/` en `/etc/claude-code` en Linux, `/Library/Application Support/ClaudeCode` en macOS) también cuentan, como para Claude Code.
- **Qué ajuste de clave.** Para un gateway u otro proveedor, la documentación de Claude Code recomienda `ANTHROPIC_AUTH_TOKEN`, por el que Claude Code nunca pregunta. Con `ANTHROPIC_API_KEY` pregunta una vez «Do you want to use this API key?», y una sesión iniciada desde Telegram no puede responder, así que aipager responde en `~/.claude.json` (o en el archivo donde `CLAUDE_CONFIG_DIR` lo pone) justo antes de que empiece la sesión:
  - Sí, cuando la clave está en `settings.json` o `daemon.env`, o cuando nada más puede autenticar la sesión.
  - No, cuando la clave solo viene del entorno en el que se inició aipager y ya entras en Claude de otra forma: tus sesiones siguen con esa cuenta y su plan. Para usar la clave, actívala en una sesión de terminal que la tenga, con `/config` y «Use custom API key».
  - Una respuesta que diste tú se mantiene, salvo un No para una clave puesta en `settings.json` o `daemon.env` cuando nada más puede autenticar una sesión. `aipager doctor` avisa cuando un No deja las sesiones sin forma de entrar; vuelve a activar la clave en una terminal con `/config` y «Use custom API key».
- **Pantallas del primer uso.** Donde Claude Code nunca terminó su primer uso (un equipo o contenedor nuevo), aipager lo marca como hecho cuando la sesión tiene con qué autenticarse, para que no se quede en el selector de tema. Si no hay ninguna credencial, ejecuta `claude` una vez en una terminal e inicia sesión.
- **Modelos.** Los botones Opus, Sonnet y Haiku envían `opus`, `sonnet` y `haiku` de Claude Code, que los asigna a los modelos de tu proveedor cuando defines `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL` y `ANTHROPIC_DEFAULT_HAIKU_MODEL`. Los botones `claude-...` necesitan un proveedor que tenga esos modelos. Cualquier otro modelo se escribe en el paso de modelo de `/new` o en la Mini App, tal como lo escribe tu proveedor: hasta 128 caracteres de letras, dígitos y `. _ - : / @ ~ ,`, con la terminación `[1m]` de Claude Code si hace falta. `keyboard.json` cambia los botones (ver [Teclado fijo](commands.md#persistent-keyboard)).
- **`ANTHROPIC_BASE_URL` sin clave.** Entonces Claude Code envía tu inicio de sesión de claude.ai a esa dirección. Pon también la clave del proveedor, salvo que la dirección sea un gateway pensado para pasar tu inicio de sesión a Anthropic.
- **Sin soporte:** las herramientas que ejecutan Claude Code con la suscripción de chat de otra empresa (un plan de ChatGPT, por ejemplo) a través de un proxy local. Esa empresa no ha aprobado Claude Code para sus planes, y ese uso puede poner en riesgo esa cuenta.

**La configuración de Claude Code en otra carpeta (`CLAUDE_CONFIG_DIR`).** Claude Code guarda sus ajustes, el inicio de sesión, las transcripciones y la configuración global en `~/.claude` y `~/.claude.json`, o en la carpeta que nombra `CLAUDE_CONFIG_DIR`. aipager sigue la misma variable, así que sus hooks y su línea de estado van al `settings.json` que leen tus sesiones y sus respuestas al archivo que lee Claude Code (comprobado con Claude Code 2.1.303):

- **Defínela en `~/.config/aipager/daemon.env`** y reinicia aipager. Un servicio nunca lee el perfil de tu shell, así que un `export` solo llega al `aipager start` de esa shell. Al arrancar, el daemon pone sus hooks en `$CLAUDE_CONFIG_DIR/settings.json` y registra `sessions read Claude Code's config from ...`; si cambias el valor mientras corre, la siguiente sesión que inicie los recibe, igual que un archivo de ajustes que los haya perdido desde entonces. Cuando no se pueden poner los hooks, el registro dice `sessions will start without aipager's hooks` y `aipager doctor` avisa.
- **Usa una ruta completa.** Claude Code toma el valor tal cual: sin `~`, y un valor relativo (también uno vacío) significa una carpeta dentro de la carpeta propia de cada sesión. aipager no escribe nada ahí: el registro y `aipager doctor` avisan, `aipager setup` se detiene con la solución y `aipager config` se salta su paso de Claude Code con un aviso que muestra la solución y sigue. La solución nombra el archivo que tiene el valor: `daemon.env`, o el archivo de ajustes cuyo bloque `env` lo define.
- **Definida en los propios ajustes de Claude Code.** Un `CLAUDE_CONFIG_DIR` en el bloque `env` de `~/.claude/settings.json` (o de los ajustes administrados) mueve el archivo de ajustes, el inicio de sesión y las transcripciones, pero no `~/.claude.json`. aipager también sigue eso; `aipager doctor` dice dónde.
- **`aipager config` y `aipager setup`** modifican el archivo que leen las sesiones del daemon: el entorno de tu shell más `daemon.env`. Un `claude` que inicies en una shell con otro valor lee otro archivo y no tiene los hooks de aipager; `aipager doctor` lo nombra.
- **El antiguo `.config.json`.** Cuando `<config folder>/.config.json` está en uso (por ejemplo, existe y no está vacío), Claude Code lo lee en vez de `.claude.json`, y aipager también. Con un `CLAUDE_CONFIG_DIR` vacío ese archivo estaría en la carpeta propia de la sesión, y aipager no escribe nada ahí.
- **Lo que aipager no puede leer.** Los ajustes administrados que llegan por un perfil MDM (macOS), el registro de Windows o los servidores de Claude, y la opción `--settings`. Una clave o un interruptor de proveedor definido solo ahí no se ve cuando aipager responde las preguntas de inicio; entonces Claude Code pregunta, como haría sin aipager.
- **La lista de denegación.** Las reglas predeterminadas que mantienen a los roles restringidos y a los archivos enviados lejos de las credenciales de Claude nombran `~/.claude/**` y `~/.claude.json*`. Un `CLAUDE_CONFIG_DIR` en otro sitio no queda cubierto por ellas, así que déjalo dentro de `~/.claude` (el de la imagen de Docker lo está) cuando personas que no son administradoras usan tus sesiones ([seguridad](security.md#who-decides-whether-a-tool-call-runs)).

## <a id="the-daemons-control-socket-moved"></a>El socket de control de aipager cambió de sitio

`aipager doctor` y `aipager status` antes miraban siempre `/tmp/aipager.sock`. Bajo el servicio de systemd, el socket de control ahora está en `$XDG_RUNTIME_DIR/aipager.sock` (y solo vuelve a `/tmp` cuando `$XDG_RUNTIME_DIR` no está puesto: contenedores, WSL1, distribuciones mínimas). Solo se movió este socket; los sockets de dtach de cada sesión (`/tmp/claude-dtach-*.sock`) no cambian. Si necesitas un sitio concreto, cámbialo con `AIPAGER_SOCKET_PATH`.

## <a id="update-says-the-restart-would-kill-sessions-killmode"></a>`/update` dice que el reinicio terminaría las sesiones (KillMode)

Los servicios escritos antes de esta versión no tienen la línea `KillMode=`, así que systemd usa `control-group`: detener o reiniciar `aipager.service` termina todas las sesiones de Claude que arrancó aipager. Por eso `/update` instala la versión nueva pero no reinicia aipager, y lista las sesiones que terminaría un reinicio. Arréglalo una vez:

```
aipager service install
```

Reescribe el servicio con `KillMode=process`, recarga systemd y reinicia aipager, y ese reinicio ya va con el ajuste nuevo. Compruébalo con `systemctl --user show -p KillMode aipager.service` (`KillMode=process`). Con él, systemd escribe en el log líneas «left-over process» para tus sesiones en cada reinicio; es lo esperado.

## <a id="aipager-update-cant-find-uv--pipx"></a>`aipager update` no encuentra uv o pipx

`aipager update` busca el instalador por su ruta completa en tu `PATH` más `~/.local/bin`, `~/.cargo/bin`, las carpetas de Homebrew, `/usr/bin` y `/bin`. Si aun así dice que no encontró `pipx`, el instalador que creó este aipager ya no está o está en otro sitio; reinstálalo con la herramienta que tengas (`pipx install --force aipager`, `uv tool install --reinstall aipager`).

## <a id="update-refused-editable--not-your-install"></a>Actualización rechazada: editable o una instalación que no es tuya

`/update` y `aipager update` solo actualizan una instalación que es de tu usuario y en la que puede escribir, a través del instalador que la creó. Se niegan, y dicen el motivo, para:

- un checkout editable (de desarrollo): actualízalo con `git pull`;
- una instalación de Nix, Snap, Docker o un paquete del sistema operativo: usa la actualización de ese sistema;
- una instalación de otro usuario (por ejemplo un venv de root bajo `/opt`): actualízala con ese usuario;
- una instalación hecha desde una carpeta local (`pipx install /path/to/aipager`): `can't update from here: it was installed from a local folder; update it from that folder`. Trae la versión nueva a esa carpeta y reinstala desde ella, o pásate a PyPI con `pipx install --force aipager`;
- una instalación cuyo instalador no se puede saber (`could not tell which installer owns this aipager`): reinstálala con `uv tool install aipager` o `pipx install aipager`.

## <a id="update-failed-unexpected-argument---refresh"></a>Falló la actualización: unexpected argument '--refresh'

aipager 1.0.0 y anteriores ejecutaban `uv tool upgrade aipager --refresh`, que ninguna versión de uv acepta, así que cada actualización de una instalación con uv se paraba con `error: unexpected argument '--refresh' found` y no reiniciaba nada. Las versiones más nuevas ejecutan `uv tool upgrade aipager`. Para salir de eso una vez, actualiza desde una terminal en esa computadora (`aipager update` tiene el mismo problema en esas versiones):

```
uv tool upgrade aipager
systemctl --user restart aipager.service
```

Sin el servicio (o en macOS), reinicia aipager de la forma en que lo arrancaste.

## <a id="claude-code-update-installed-with-npm-into-a-folder-you-cant-write-to"></a>Actualización de Claude Code: instalado con npm en una carpeta donde no puedes escribir

Un Claude Code instalado con `sudo npm install -g` vive en una carpeta que solo root puede cambiar (como `/usr/lib/node_modules`), así que `claude update` ejecutado con tu usuario falla con `global folder isn't writable`. `/update` y la Mini App muestran la versión más nueva con `can't update from here: installed with npm into a folder your user can't write to` y sin botón para ella. Ejecuta `claude install` una vez con el usuario con el que funciona aipager: pasa al instalador propio de Claude Code bajo tu carpeta personal, que se actualiza sin sudo. Luego reinicia aipager (`systemctl --user restart aipager.service`, o de la forma en que lo arrancaste): un aipager en marcha se queda con el Claude Code que encontró al arrancar, y después del reinicio toma el más nuevo que encuentre. Si `claude_path` en `aipager.yaml` (`aipager doctor --fix` puede ponerlo) o `AIPAGER_CLAUDE_BIN` apunta al de npm, apúntalo primero a `~/.local/bin/claude` o quítalo, porque esos dos siempre ganan.

## <a id="updated-but-the-version-didnt-change"></a>Se actualizó, pero la versión no cambió

Una instalación de pipx hecha desde una URL de git se actualiza desde esa fuente de git, no desde PyPI. Cuando la fuente no tiene nada más nuevo, `/update` dice `already at A - this pipx install upgrades from its git source, which has nothing newer`. Sube o elige la versión nueva ahí, o pásate a PyPI con `pipx install --force aipager`.

**Sin servicio**, un aipager que ningún gestor de servicios reinicia (`aipager start` en una terminal o tmux, Docker, launchd) instala una actualización pero sigue con el código viejo hasta que se reinicia. `/update` dice entonces `aipager B está instalado y espera un reinicio (ahora funciona A).`, y los hooks de Claude Code, que arrancan de nuevo en cada evento, puede que ya usen B: el propietario recibe un aviso de que la actualización está a medias (dos minutos después de que los hooks usaran B por primera vez, así que un reinicio justo después de instalar nunca lo muestra, y cuando no haya ninguna tarea en curso en el chat privado del propietario). Cuando aipager ya usa B, ese aviso pasa a `✅ La actualización de aipager terminó: aipager ya está en B.`, sin su botón. Toca **🔄 Reiniciar ahora** (en el mensaje de la actualización, en el aviso o en **Ajustes → Actualizaciones** de la Mini App). aipager espera a que no haya ninguna tarea en curso, se detiene limpiamente y arranca la versión nueva en el sitio: el mismo proceso en la misma terminal, ventana de tmux o contenedor, con el mismo comando y el mismo entorno.

Si el arranque en el sitio falla, una copia separada arranca aipager cuando el proceso viejo ya no está. Su salida va entonces al log del reinicio que aipager hace por su cuenta (`aipager.log` en la carpeta de la instancia, o `/tmp/aipager.log`) en vez de a tu terminal, y el log de aipager indica el comando que hay que ejecutar si esa copia no vuelve. Si el propio intérprete ya no está (se borró su entorno), o no se pudo arrancar nada, aipager sale con el código 1, el log de aipager termina con `restart.failed:` y el comando que hay que ejecutar, y el siguiente arranque anuncia la actualización.

Cuando no hay botón **Reiniciar ahora**, el mensaje da los pasos para tu instalación: en una terminal o tmux, pulsa Ctrl+C y ejecuta `aipager start`; en macOS, `launchctl kickstart -k gui/<uid>/com.aipager.daemon`; en Docker, `docker restart <container>` en el host. Volver a crear un contenedor de Docker (`docker compose up` después de `down`, o `docker run` otra vez) lo arranca desde su imagen, y eso devuelve la versión de aipager de esa imagen: para quedarte con un aipager más nuevo, usa una imagen más nueva.

La imagen define `CLAUDE_CONFIG_DIR=/home/aipager/.claude`, así que la configuración global de Claude Code (`.claude.json`: la confianza en carpetas, su estado de primer uso, las respuestas sobre la clave de API, tu cuenta y los servidores MCP del usuario) se guarda en el volumen `.claude` con lo demás y sobrevive a que se recree el contenedor. Las imágenes anteriores la guardaban en `/home/aipager/.claude.json`, dentro del propio contenedor. En el primer arranque de la imagen nueva el archivo antiguo no se lee: tu inicio de sesión en el `.credentials.json` del volumen sigue funcionando, y aipager vuelve a responder las preguntas de confianza en la carpeta y de primer uso cuando empieza una sesión, pero los servidores MCP del usuario añadidos con `claude mcp add --scope user` hay que volver a añadirlos. Si montas el `~/.claude` de tu host (como hacen los comandos del README), el archivo aparece ahí como `~/.claude/.claude.json`; el Claude Code de tu host no lo lee (usa `~/.claude.json`). Un volumen con nombre mantiene los dos separados.

## <a id="the-daemon-didnt-come-back-after-an-update"></a>aipager no volvió después de una actualización

La actualización comprueba que la versión nueva se importa antes de reiniciar nada, pero si el aipager nuevo aun así no arranca, systemd lo reintenta cada 5 s. Mira por qué:

```
journalctl --user -u aipager.service -n 50
```

Reinstala la versión anterior con el mismo instalador y luego reinicia:

```
pipx install --force aipager==<previous>        # o:
uv tool install --force aipager==<previous>     # o:
<venv>/bin/python -m pip install aipager==<previous>
systemctl --user restart aipager.service
```

Una instalación de pipx desde una ruta local: `pipx install --force /path/to/aipager` en el commit anterior. Si queda un anuncio viejo pendiente, bórralo con `rm -f ~/.local/share/aipager/update-restart.json` (de todas formas se ignora pasadas 24 h); `~/.local/share/aipager/update.lock` es solo un archivo de bloqueo y se puede borrar sin problema cuando no hay ninguna actualización en curso.

## <a id="an-update-was-interrupted-when-aipager-shut-down"></a>«An update was interrupted when aipager shut down»

aipager se detuvo (o se reinició) mientras `/update` estaba instalando, y el instalador se detuvo con él, así que la instalación puede haber quedado a medias. Si aipager o Claude Code no funcionan bien, repáralo: para aipager, ejecuta el comando de reinstalación del mensaje (por ejemplo `pipx install --force aipager`) y luego `systemctl --user restart aipager.service`; para Claude Code, vuelve a ejecutar `claude update`.

Un Reiniciar ahora que no pudo volver a arrancar aipager (sin servicio) deja `restart.failed:` en el log de aipager con el comando que hay que ejecutar; ejecútalo, y el nuevo aipager anuncia la actualización.

## <a id="a-chat-is-in-the-wrong-language"></a>Un chat está en el idioma equivocado

El idioma de aipager sale de lo que se eligió para el chat, no de los mensajes que hay en él. Abre `/language` en ese chat y elige el idioma que quieres: esa elección tiene prioridad sobre todo lo demás. En un chat privado, **Automático (tu idioma de Telegram)** sigue el idioma de tu app de Telegram (Ajustes → Idioma en Telegram); un idioma que aipager no tiene se ve en inglés. Un chat privado que ya existía antes de que hubiera idiomas sigue en inglés hasta que elijas uno. Un grupo está en inglés hasta que un admin elige su idioma. El idioma solo cambia los textos de aipager: Claude responde en el idioma en que le escribes, muestre lo que muestre aipager. Mira [Idiomas](commands.md#languages).

## <a id="the-bot-description-is-not-the-one-i-wrote"></a>La descripción del bot no es la que escribí

aipager pone la descripción y la descripción corta del bot en cada idioma que habla, para las personas cuya app de Telegram está en ese idioma, y Telegram les muestra ese texto en lugar del que escribiste en BotFather. aipager solo lo hace mientras no tengas una descripción propia, así que una descripción que escribas antes de instalar esta versión le llega a todo el mundo. Si la escribiste después de que aipager pusiera la suya, las personas en esos idiomas siguen viendo la de aipager. Para que vean la tuya, borra el texto de aipager para cada idioma con la Bot API (`setMyDescription` y `setMyShortDescription` con un texto vacío y ese `language_code`); aipager no lo vuelve a poner mientras el tuyo esté ahí.

## <a id="aipager-says-languagesjson-could-not-be-used"></a>aipager dice que no pudo usar languages.json

aipager guarda lo que aprendió sobre los idiomas (quién estaba aquí antes de que hubiera idiomas, el último idioma de Telegram que vio de cada persona, y si ya se enviaron la propuesta única y la línea de primer contacto) en `~/.local/state/aipager/languages.json`. Si ese archivo no se puede leer, aipager lo aparta como `languages.json.bad-<time>` en la misma carpeta, escribe una advertencia en el log (otra vez en cada arranque hasta que decidas) y deja todos los chats en inglés y sin propuesta de idioma hasta que decidas: devuelve el archivo viejo a su sitio si puedes repararlo, o borra `languages.json` y reinicia, y aipager vuelve a contar a todas las personas que conoce como antiguas (en inglés hasta que elijan). Un idioma que alguien eligió no está en este archivo y nunca se pierde.

## <a id="right-to-left-text-looks-wrong"></a>El texto de derecha a izquierda se ve mal

En persa y árabe, aipager pone marcas invisibles de dirección en su propio texto para que cada línea se lea de derecha a izquierda, y mantiene los nombres de las sesiones, los comandos y el código de izquierda a derecha. Si una línea aun así se ve desordenada, casi siempre es una app de Telegram que dibuja de otra forma el texto con direcciones mezcladas: compruébalo en otra app (teléfono frente a Desktop o Web) antes de informar. Las respuestas del propio Claude se muestran tal como las escribió Claude, sin marcas añadidas.

## <a id="a-translated-keyboard-word-does-nothing-in-another-chat"></a>Una palabra traducida del teclado no hace nada en otro chat

El teclado de cada chat está en el idioma de ese chat. Las palabras del teclado principal (status, stop, new, kill y las demás) se entienden en todos los chats, en todos los idiomas, y las palabras en inglés siempre funcionan. La palabra de un botón de plantilla o de comando es distinta: escrita en un chat con otro idioma, es un mensaje para Claude, no un botón, así que un «Clear» en ruso nunca ejecuta `/clear` en un chat en persa o en inglés. Usa el propio botón, o el comando en inglés (`/clear`). Si un teclado parece viejo después de cambiar el idioma del chat, envía `/start` para tener el actual. Una palabra que además es el nombre de una de tus sesiones habla con esa sesión. `/mode` lee siempre `ask`, `auto` y `full` como el modo, y una palabra de modo traducida como una sesión solo cuando una sesión en marcha tiene exactamente ese nombre.

## <a id="still-stuck"></a>¿Sigues atascado?

Envía un informe de problemas: toca **Informar de un problema** en `/help` en tu chat privado con el bot, o ejecuta `aipager report` en una terminal. Ves el informe completo antes de que se envíe nada, y no contiene chats, mensajes, nombres, rutas ni tokens (mira [Informes de problemas](problem-reports.md)). Después de enviarlo, recibes una referencia como `ap1-7c41d09e2b3a`.

Luego, si quieres hablar del tema, abre un issue en [github.com/dev-aly3n/aipager/issues](https://github.com/dev-aly3n/aipager/issues) e incluye esa referencia. Por favor no pegues la salida de `aipager doctor` ni de `aipager logs` en un issue: contienen tu ID de chat, las rutas de tu carpeta personal, los nombres de tus sesiones y el principio de tus mensajes. Si hace falta una línea del log, quien mantiene aipager te pedirá esa línea, y puedes quitarle antes cualquier cosa privada.

## <a id="see-also"></a>Ver también

- [Arquitectura](architecture.md): qué hace cada pieza.
- [Modelo de seguridad](security.md): qué toca aipager y qué no.
