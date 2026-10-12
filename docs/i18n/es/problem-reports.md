# Informes de problemas: qué contienen

<!-- i18n-source: docs/problem-reports.md @ 6fc084f -->

> Esta página es una traducción de la página en inglés; si no coinciden, la versión en inglés es la correcta. ([English](../../problem-reports.md))

Cuando aipager se topa con un error, puedes enviar un informe de problemas a quien lo mantiene con un solo toque. Ves el informe completo antes de que se envíe nada, nada sale de tu computadora hasta que tú lo dices, y un informe nunca contiene tus chats, mensajes, nombres, rutas ni tokens. Se construye solo a partir de una lista fija de campos: versiones, cantidades, fechas, respuestas de sí o no, opciones de listas fijas, lugares del propio código de aipager y los nombres `ap1-` de sus errores, más una nota corta solo si tú la escribes.

## <a id="how-to-send-one"></a>Cómo enviar uno

Desde Telegram, solo el propietario de esta instalación de aipager puede enviar un informe: la persona del propio chat privado de la instalación con el bot (quien instaló aipager; si hay varios chats así y no un único propietario, no hay botón de Informar de un problema ni propuesta automática, y `aipager report` en una terminal sigue funcionando). La Mini App tiene su propia página de informe; todas las demás vías de Telegram abren la misma tarjeta de vista previa en tu chat privado con el bot:

- **`/help`**: cuando lo envía el propietario, el texto de ayuda trae un botón «🐞 Informar de un problema». En un grupo solo cuenta el toque del propietario, y la vista previa se abre en el chat privado del propietario, nunca en el grupo.
- **La Mini App**: en la pestaña de Ajustes, «Informar de un problema» (cuando la app está abierta en tu chat privado) abre una página dentro de la app. Tu nota va arriba (opcional, hasta 500 caracteres, con un contador). Debajo hay un resumen corto del informe: versiones, configuración, los errores (toca uno para ver sus lugares en el código) y **Más detalles**. **Mostrar el informe exacto** enseña el texto exacto que se va a enviar, con tu nota incluida, y se actualiza mientras escribes. **Enviar informe** (el botón de Telegram de abajo) envía exactamente eso, una sola vez, y luego la página muestra la referencia con un botón **Copiar**. Volver atrás no envía nada. Si aipager limpia tu nota (espacios al principio o al final, caracteres invisibles, lo que pase de 500 caracteres), la página muestra la nota limpia y no se envía nada hasta que vuelvas a tocar Enviar informe.
- **El aviso de límite de memoria**: cuando al hook de aipager se le acaba la memoria, el aviso del chat trae un botón «🐞 Informar de esto».
- **Una propuesta automática**: de vez en cuando, aipager propone enviar un informe de un error que notó (mira [Cuándo propone aipager un informe](#when-aipager-offers-a-report)).
- **En una terminal**: `aipager report` imprime un informe y pregunta `Send this report? [y/N]`. Tiene el mismo tipo de campos, pero menos datos del aipager en marcha (el modo, los chats, las funciones, el tiempo encendido, las sesiones y los detalles de Claude Code quedan desconocidos o vacíos). `aipager report --note "what you were doing"` añade una nota. Sin terminal (un script, una tubería, el shell de una sesión de Claude Code) imprime el informe y nunca lo envía.

La tarjeta de vista previa muestra el informe exactamente como se enviará: el JSON en un bloque plegado que puedes abrir o, cuando es demasiado largo para un mensaje, como un archivo `report.json` que responde a la tarjeta. Sus botones:

- **📤 Enviar**: envía ese informe exacto, una sola vez. Luego la tarjeta dice `Enviado. Referencia: ap1-...`.
- **✏️ Añadir una nota**: tu siguiente mensaje en ese chat, dentro de 10 minutos, se convierte en la nota del informe (cortada a 500 caracteres), y la tarjeta vuelve a mostrar el informe con la nota antes de enviarlo. Si haces otra cosa (un comando, otro botón, una foto, algo en la Mini App), la pregunta de la nota se cierra sin añadir nota, y pasados 10 minutos tu siguiente mensaje va a Claude como siempre. La pregunta tiene **‹ Atrás**, que vuelve a la vista previa sin nota, y Cancelar.
- **✖️ Cancelar**: no se envía nada.

Una vista previa queda abierta 24 horas, y quedan abiertas las 5 más recientes (abrir una sexta cierra la más antigua). Después de reiniciar aipager, una tarjeta vieja ya no se puede enviar: abre Informar de un problema otra vez para tener una nueva. Un informe abierto en la Mini App se puede enviar durante 24 horas (los 3 más recientes); después de un reinicio, la página carga uno nuevo.

## <a id="what-a-report-holds"></a>Qué contiene un informe

Cada parte, en palabras sencillas. Cada valor se comprueba contra la lista de valores permitidos antes de mostrar la vista previa y otra vez antes de enviar; lo que no encaja se cambia por `<invalid>` o se quita, nunca se envía tal cual.

**Sobre el informe**
- `schema`: el formato del informe (`aipager-report/1`).
- `trigger`: `manual` (lo abriste tú) o `auto` (una propuesta automática).
- `day`: la fecha, en UTC (sin la hora).
- `note`: solo si añadiste una: tus palabras, exactamente como las mostró la vista previa (como mucho 500 caracteres, sin caracteres invisibles).

**aipager y la computadora**
- `aipager`: la versión; cómo se instaló (`pipx`, `uv`, `brew`, `pip`, `editable`, `nix`, `snap`, `docker`, `system`, `foreign` o `unknown`); de dónde (el índice de paquetes, una carpeta local, un checkout de git); y si `aipager update` puede actualizarlo.
- `python`: la versión de Python y si es CPython o PyPy.
- `os`: el sistema (`linux`, `darwin`, `windows`, `freebsd`), el tipo de procesador, la distribución de Linux de una lista fija y su número de versión, la versión major.minor del kernel, el tipo de contenedor si lo hay (`docker`, `lxc`, `wsl`, `podman`), y si aipager está configurado como servicio de systemd o launchd.
- `deps`: las versiones de las bibliotecas de las que depende aipager (python-telegram-bot, httpx, aiohttp, PyYAML, rich, questionary, dtach-bin).
- `claude_code`: la versión de Claude Code, cuántas instalaciones de Claude Code se encontraron, y cómo inicia sesión (`env`, `file`, `keychain`, o por qué no se pudo saber). Nunca el inicio de sesión en sí.

**Tu configuración, solo como cantidades**
- `config`: el tipo de uso (`personal`, `team`, `scope`); cuántos chats privados y cuántos grupos hay configurados; cuántos roles personalizados existen (sin sus nombres); qué funciones están activadas (la Mini App, su tipo de túnel, la voz, las vistas previas de diff, los resúmenes enriquecidos).
- `runtime`: cuánto tiempo lleva aipager encendido, como un rango (`<10m`, `10m-1h`, `1-24h`, `1-7d`, `>7d`); cuántas sesiones hay en marcha y cuántas trabajando; cuántas veces se detuvo aipager de forma inesperada en los últimos 7 días y cómo terminó la última vez (`clean`, `crash`, `reboot`).
- `doctor`: los resultados (`ok`, `warn`, `fail`) de una lista fija de comprobaciones de salud, cuando están disponibles sin volver a ejecutarlas (los informes del bot y de la terminal dejan esto vacío).
- `flood`: el estado de los límites de velocidad de Telegram, sumado en todos los chats: bloqueos en los últimos 7 días, si ahora mismo algún chat está silenciado, en modo mínimo o frenando, y la parte del presupuesto por hora que usa el chat más activo, como un rango. Sin IDs de chat.

**Qué falló**
- `counters_24h`: cuántas veces pasó cada cosa de una lista fija en las últimas 24 horas (por ejemplo el vigilante de la tarjeta de actividad reiniciándose, una clase de error de Telegram, el hook llegando a su límite de memoria, un error de red o de disco). Solo las cantidades.
- `log_digest_24h`: para los 15 lugares más activos del código de aipager que registraron una advertencia o un error en las últimas 24 horas, el lugar (`aipager/state.py:1234`), el nivel y cuántas veces. Nunca el texto del log.
- `errors`: hasta 10 errores que aipager registró, cada uno con:
  - `fingerprint`: un nombre `ap1-` para el error (un hash del tipo de error y de los nombres de hasta 8 lugares del código de aipager, nunca de ningún texto ni dato);
  - dónde pasó (aipager, el hook, la línea de estado, un comando, la Mini App), cómo se capturó, y si es un error de verdad o solo algo inusual (`tier`);
  - para un error que registró aipager, el nombre del logger (un módulo de `aipager`, `asyncio` o una parte de `telegram.ext`);
  - el nombre del tipo de excepción (por ejemplo `builtins.KeyError`, o `<other>` para uno que no venga de una lista fija de paquetes), los tipos de hasta tres causas, y el nombre de un número de error como `ENOSPC`;
  - para un error del hook, el evento del hook y la herramienta (una herramienta propia por su nombre, cualquier herramienta MCP como `mcp`);
  - cuántas veces, en qué días y en qué versiones de aipager pasó;
  - hasta 12 lugares del propio código de aipager por los que pasó (archivo, línea, nombre de la función), y hasta 8 nombres de paquetes externos por los que pasó, de una lista fija.

**Nunca se incluye**: tus chats, mensajes, las respuestas de Claude, los nombres de las sesiones, rutas de carpetas o archivos, tokens o claves, IDs de chat o de usuario, nombres de usuario, el nombre de la computadora, el texto de ningún mensaje de error, ni líneas del log. Los campos de arriba no tienen sitio para eso, y el informe se comprueba contra esa lista antes de mostrarse y antes de enviarse.

## <a id="where-it-goes"></a>Adónde va

Un informe va, sin tu nombre, a la bandeja privada de errores de quien mantiene aipager en [Sentry](https://sentry.io): sin cuenta y sin ID de instalación. Como cualquier petición web, sale desde la dirección de internet de tu computadora, que GitHub y Sentry pueden ver; la bandeja está configurada para no guardarla (la opción «Prevent Storing of IP Addresses» de Sentry). aipager lo envía él mismo en una sola petición HTTPS (no usa la biblioteca de Sentry, que recoge mucho más): el informe que viste va como adjunto `report.json`, junto con un resumen corto para agrupar (la versión, cómo se instaló y si es una instalación de desarrollo, el sistema, las versiones de Python y de Claude Code, cómo se abrió el informe, y el fingerprint, el tipo y los lugares del código del primer error). La petición solo se identifica como `aipager/<version>`.

Después de enviarlo, la tarjeta (o la terminal) muestra una **referencia**: el fingerprint `ap1-` del primer error o, para un informe sin errores, su ID de evento de 32 caracteres. Si además abres un [issue en GitHub](https://github.com/dev-aly3n/aipager/issues), incluye la referencia para que quien mantiene aipager encuentre tu informe. No pegues logs ni la salida de `aipager doctor` en un issue: contienen tu ID de chat, las rutas de tu carpeta personal y el principio de tus mensajes.

Justo antes de enviar, aipager lee [report-endpoint.json](https://github.com/dev-aly3n/aipager/blob/main/report-endpoint.json) del repo de aipager en GitHub para saber adónde enviarlo (un aipager en marcha guarda una copia buena durante un día; si la lectura falla, se reintenta en el siguiente envío, y cada `aipager report` lo lee de nuevo). Así quien mantiene aipager puede cambiar la clave de Sentry, apagar los informes para todos o impedir que envíen las versiones por debajo de un número (una versión que no sabe su propio número cuenta como inferior), sin publicar una versión nueva. Si GitHub no responde o el archivo no es algo que aipager entienda, aipager usa la clave incluida en esta versión; un archivo legible que diga apagado siempre se respeta.

## <a id="limits"></a>Límites

- Como mucho 5 intentos de envío al día (en UTC) desde una computadora, manuales y automáticos juntos, y un intento fallido también cuenta. El siguiente dice `Se pueden enviar como mucho 5 informes al día desde una computadora. Vuelve a intentarlo mañana.`
- Nada se reintenta en segundo plano: si un envío falla, la tarjeta dice `No se pudo enviar ahora. No se perdió nada; vuelve a intentarlo más tarde.` y conserva su botón de Enviar.

## <a id="when-aipager-offers-a-report"></a>Cuándo propone aipager un informe

aipager nota sus propios errores y, de vez en cuando, propone en tu chat privado enviar un informe sobre ellos: `aipager tuvo un error interno N veces (TypeError en la tarjeta de actividad). Un informe ayuda a que se arregle antes. ...` con **Ver el informe**, **Ahora no** y **No volver a preguntar**. Ver el informe abre la tarjeta de vista previa solo con esos errores; no se envía nada hasta que toques Enviar ahí.

Una propuesta solo llega cuando se cumple todo esto:
- Es un error real de aipager: un error en el propio código de aipager que pasó al menos dos veces, con al menos 10 minutos de diferencia, o enseguida cuando aipager se cayó funcionando como servicio (un `aipager start` en primer plano que se cierra, por ejemplo al cerrar su terminal, solo se cuenta). Los errores que avisan el hook, la línea de estado o un comando (incluido que al hook se le acabe la memoria) también tienen que haber pasado a lo largo de al menos un día, así que uno solo nunca provoca una propuesta; para eso está el botón Informar de esto del aviso. Las cosas que no son culpa de aipager (los límites de Telegram, la red, un disco lleno) nunca provocan una propuesta; solo se cuentan para un informe que envíes tú.
- Como mucho una propuesta cada 3 días, con todo lo pendiente en esa misma propuesta.
- No en las primeras 48 horas desde que aipager arrancó por primera vez en esta computadora (las actualizaciones no reinician esa cuenta), ni en la primera hora después de reiniciar aipager.
- Solo cuando no hay nada en marcha ni esperando a enviarse durante 2 minutos, y usaste tu chat privado con el bot en los últimos 30 minutos. Nunca en un grupo, y nunca mientras Telegram esté limitando ese chat.
- Cada error se propone una vez por versión de aipager. Después de cualquier respuesta, no se vuelve a proponer en esa versión; puede volver después de actualizar, solo si pasa otra vez.
- **Ahora no** y **No volver a preguntar** cuentan los dos como un no, y también una propuesta que no respondas en un día. **Ver el informe** pone la cuenta a cero, aunque luego canceles. Dos noes seguidos apagan las propuestas automáticas: cuando el segundo es un toque, una línea lo dice; en cualquier caso `/settings` lo muestra.
- Nunca para una instalación de desarrollo (aipager instalado desde una carpeta local, un checkout de git o en modo editable).

Para apagar las propuestas automáticas, usa `/settings` en tu chat privado: **🐞 Informes de problemas: Desactivado**. Cuando dos noes las apagaron, la misma página tiene **Volver a activar las propuestas**. Un `reports.json` que existe pero no se puede leer también apaga las propuestas automáticas (aipager ya no puede saber qué respondiste antes); **Volver a activar las propuestas** también arregla eso. En una computadora sin chat donde preguntar, pon `AIPAGER_REPORT_PROMPTS=0` en el entorno de aipager. Informar de un problema y `aipager report` siempre funcionan, digan lo que digan estos ajustes.

## <a id="what-is-kept-on-your-machine"></a>Qué se guarda en tu computadora

- `~/.local/state/aipager/reports.json`: los errores que registró aipager (con la forma de arriba: nombres de tipos, versiones, lugares del código, como mucho 50), cantidades por hora durante 8 días, el resumen del log durante 2 días, cómo terminaron las paradas recientes, y el estado de las propuestas (cuándo propuso por última vez, los noes seguidos, qué errores propuso en qué versión). Solo tú puedes leerlo.
- `~/.local/state/aipager/report-sends.json`: cuántos informes se enviaron o intentaron hoy.
- `~/.local/share/aipager/install.json`: cuándo arrancó aipager por primera vez en esta computadora (para las 48 horas de arriba).
- `~/.local/share/aipager/running.json`: se escribe mientras aipager está en marcha (su número de proceso, la hora de inicio, si funciona como servicio y el ID de este arranque del sistema), para distinguir una caída de una parada normal.

Ninguno contiene un chat, un mensaje, un nombre, una ruta ni un token. `aipager uninstall` los conserva, junto con el resto de los datos de aipager, para que una reinstalación siga desde donde estaba; muestra lo que conserva e imprime el comando que lo borra (mira [`aipager uninstall`](commands.md#command-line-aipager-uninstall)). Para borrar solo estos, elimina esos cuatro archivos: borrar toda la carpeta `~/.local/state/aipager` elimina los dos primeros (y con ellos el estado de idioma, `languages.json`), pero `install.json` y `running.json` hay que borrarlos por su nombre (no borres `~/.local/share/aipager`, que también tiene las carpetas de las sesiones de aipager).

## <a id="see-also"></a>Ver también

- [Solución de problemas](troubleshooting.md): fallos frecuentes y cómo arreglarlos.
- [Modelo de seguridad](security.md): qué toca aipager y qué sale por la red.
- [Comandos del bot](commands.md): `/help`, `/settings` y la línea de comandos.
