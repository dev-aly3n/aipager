# Bots observadores

<!-- i18n-source: docs/observers.md @ b12e978 -->

> Esta página es una traducción de la página en inglés; si no coinciden, la versión en inglés es la correcta. ([English](../../observers.md))

Bots de Telegram de solo lectura que repiten los avisos del bot principal. Reciben resúmenes, advertencias y errores, pero no pueden controlar las sesiones.

## <a id="setup"></a>Configuración

1. Crea un bot con [@BotFather](https://t.me/BotFather).
2. Abre un chat con el bot y envía `/start`.
3. Consigue el ID del chat (envía un mensaje y luego abre `https://api.telegram.org/bot<TOKEN>/getUpdates`).
4. Pon `OBSERVER_BOTS` en el entorno de aipager. Si ejecutas aipager tú mismo en una terminal, expórtalo antes de `aipager start`; para el servicio de systemd:

```sh
systemctl --user edit aipager    # añade debajo de [Service]:
# Environment=OBSERVER_BOTS=<bot_token>:<chat_id>
```

Varios observadores (separados por comas):

```
OBSERVER_BOTS=111:AAA_first:12345,222:BBB_second:67890
```

El formato es `token:chat_id`; al leerlo, el separador es el **último** dos puntos (el token del bot ya lleva dos puntos dentro).

5. Reinicia aipager.

## <a id="what-observers-receive"></a>Qué reciben los observadores

Los observadores reciben estos avisos de **todos los chats donde está el bot**, de cada chat privado y cada grupo, sin ningún filtro: configura uno solo para un chat al que le confiarías todo eso. Cada aviso llega en el idioma del chat del que viene.

| Evento | Ejemplo |
|-------|---------|
| Tarea terminada | `💬 x1 · Terminado (2:05, +12 -3)`: solo la línea del resultado, no la respuesta; y además el registro completo como archivo `.md` cuando la respuesta o la tarjeta se recortaron |
| Terminó un trabajo en segundo plano | `✅ x1 · Terminado (...)`, o `⚠️ x1 · Terminado (...)` cuando un agente desapareció o no se vio el final de un comando en segundo plano |
| Error de la API | `⚠️ x1 · Los servidores de Anthropic están saturados. Vuelve a intentarlo en un momento.` (sin botón de reintentar) |
| Aviso de contexto | `⚠️ x1 · Contexto al 82% - pronto se compactará solo` |
| Compactando | `🔄 x1 · Compactando la conversación` |
| Compactación terminada | `📦 x1 · Conversación compactada: 82% → 4%` |
| Una solicitud que sigue esperando | `⬆️ x1 · sigue esperando tu respuesta arriba`, con el permiso o la pregunta que espera |
| Una sesión que trabaja sin dar noticias | `⏳ x1 · sigue trabajando (120 min sin novedades)` |
| Un mensaje que Claude Code no recibió | `⚠️ x1 · Claude Code no recibió el mensaje` |
| La sesión terminó | `🔴 x1 · Sesión terminada` |

## <a id="what-observers-dont-receive"></a>Qué no reciben los observadores

- La animación de la tarjeta de actividad
- Las actualizaciones de las herramientas que se ejecutan
- Las solicitudes de permiso y sus botones (solo el recordatorio de «sigue esperando» de arriba dice qué pide una solicitud)
- Las preguntas de AskUserQuestion
- Ningún teclado ni botón

Los observadores no guardan ningún estado: se envía el mensaje y listo. Si un observador falla, no afecta en nada al bot principal.
