<div align="center">

<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="96" height="96" alt="aipager"></a>

# aipager

<!-- i18n-source: README.md @ 6ececab -->

<h3>Claude es un chat más.</h3>

Tus sesiones de [Claude Code](https://claude.com/claude-code) en Telegram, junto a tu familia, tus amigos y tu equipo.<br>
Se ejecutan en tu propia computadora. Las sigues, les respondes y les das el siguiente trabajo desde donde estés.

<p>
<a href="https://pypi.org/project/aipager/"><img alt="PyPI" src="https://img.shields.io/pypi/v/aipager?style=flat-square&label=pypi&labelColor=181c20&color=2aabee"></a>
<a href="https://pypi.org/project/aipager/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/aipager?style=flat-square&labelColor=181c20&color=2aabee"></a>
<a href="https://github.com/dev-aly3n/aipager/actions/workflows/test.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/dev-aly3n/aipager/test.yml?branch=main&style=flat-square&label=tests&labelColor=181c20"></a>
<img alt="Linux and macOS" src="https://img.shields.io/badge/runs%20on-Linux%20%7C%20macOS-2aabee?style=flat-square&labelColor=181c20">
<a href="../../../LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-2aabee?style=flat-square&labelColor=181c20"></a>
</p>

**[Sitio web](https://aipager.run)** · **[Instalación](#install)** · **[Documentación](#documentation)** · **[Seguridad](security.md)** · **[Cambios](../../../CHANGELOG.md)**

**Leer en otro idioma:** [English](../../../README.md) · [Русский](../ru/README.md) · [Português (Brasil)](../pt-BR/README.md) · [简体中文](../zh-Hans/README.md) · [Français](../fr/README.md) · [Deutsch](../de/README.md) · [한국어](../ko/README.md) · [العربية](../ar/README.md) · [Bahasa Indonesia](../id/README.md) · [Українська](../uk/README.md) · [Italiano](../it/README.md) · [Türkçe](../tr/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

<sub>Esta página es una traducción de la página en inglés; si no coinciden, la versión en inglés es la correcta. ([English](../../../README.md))</sub>

<br>

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/hero.webp" width="100%" alt="Un mensaje escrito en Telegram llega a Claude Code en una terminal, y una tarjeta del chat se actualiza sola mientras Claude trabaja">

<sub>Escribes en Telegram. aipager lo teclea en Claude Code en tu computadora, y una tarjeta se actualiza sola mientras avanza la tarea.</sub>

</div>

<br>

## <a id="why-aipager"></a>Por qué aipager

<table>
<tr>
<td width="33%" valign="top">

**📟 Una tarjeta en vivo por tarea**<br>
Lo que Claude está haciendo ahora mismo: las herramientas que ejecuta, el contexto, el costo y las líneas cambiadas, y luego la respuesta. La tarjeta se actualiza sola, así el chat no se llena.

</td>
<td width="33%" valign="top">

**⚡ Háblale a mitad de tarea**<br>
Tu mensaje se teclea en la sesión al momento, incluso mientras Claude trabaja, igual que si escribieras en la terminal. 👀 significa enviado, 👍 significa que Claude lo tomó.

</td>
<td width="33%" valign="top">

**✅ Responde con un toque**<br>
Las solicitudes de permiso y las preguntas de Claude llegan con un botón para cada respuesta. Un botón que quedó de una tarea terminada se niega en lugar de actuar sobre la siguiente.

</td>
</tr>
<tr>
<td width="33%" valign="top">

**🛡️ Tres modos de permisos**<br>
💬 Modo Preguntar, 🤖 Modo Automático (el de por defecto para sesiones nuevas) y 🔓 Modo Acceso total, por sesión y cambiables en cualquier momento con `/mode`.

</td>
<td width="33%" valign="top">

**👥 Pensado para equipos**<br>
Añade el bot a un grupo. Cada mensaje se ejecuta con el rol de quien lo envía, puedes limitar a los miembros a su propia carpeta y cada toque queda en el registro de auditoría.

</td>
<td width="33%" valign="top">

**🔒 Nada en medio**<br>
Sin servidor de aipager, sin cuenta, sin puerto abierto. Tu token del bot, tu computadora, tus chats.

</td>
</tr>
</table>

## <a id="a-quick-tour"></a>Un recorrido rápido

### <a id="questions-come-to-you"></a>Las preguntas te llegan a ti

Cuando Claude te pregunta algo o necesita permiso, la pregunta llega a tu chat con un botón para cada respuesta, y la barra fijada dice qué te está esperando. Tu toque se teclea en la sesión como si estuvieras frente al teclado, y la terminal muestra la misma respuesta.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/question.webp" width="100%" alt="Claude pregunta si subir el tiempo de espera o esperar a la cookie; la respuesta tocada en Telegram se elige en la terminal">

### <a id="three-permission-modes"></a>Tres modos de permisos

Cada sesión está en uno de tres modos, que cambias en cualquier momento con `/mode`, incluso mientras Claude trabaja.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/modes.webp" width="100%" alt="La misma tarea en Modo Preguntar, Modo Automático y Modo Acceso total">

| Modo | Qué pasa |
|---|---|
| 💬 **Modo Preguntar**<br>`/mode ask` | Claude pregunta antes de cada acción. |
| 🤖 **Modo Automático**, el de por defecto<br>`/mode auto` | La comprobación de seguridad del propio Claude Code ejecuta las acciones seguras y detiene las riesgosas para que tú decidas. |
| 🔓 **Modo Acceso total**<br>`/mode full` | Todo se ejecuta sin ninguna comprobación. Para admins, y para ti en tu propio chat privado con el bot. |

Entre Modo Preguntar y Modo Automático se cambia en el momento, sin reiniciar. Entrar en Modo Acceso total o salir de él reinicia la sesión y conserva la conversación. Las preguntas de Claude te llegan en cualquier modo.

### <a id="your-team-in-one-group"></a>Tu equipo, en un grupo

Añade el bot a un grupo y da a cada miembro un rol: `owner`, `admin`, `user` o `read_only`. En el grupo, el bot solo atiende comandos, respuestas a sus mensajes y menciones (`@aipagerbot fix the tests`, `/web run the tests`), y cada mensaje se ejecuta con el rol de quien lo envía: una herramienta que el rol no puede usar se rechaza antes de ejecutarse. Un miembro con `user` o `read_only` solo trabaja dentro de su propia carpeta raíz (`~/aipager-users/<label>` por defecto). Otras personas también pueden tener su propio chat privado con el bot.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/team.webp" width="100%" alt="Un grupo de equipo donde un comando se rechaza por el rol de un miembro mientras las pruebas pasan">

> **⚠️ Importante:** Añadir a un miembro le da derecho a ejecutar código en tu computadora. Lee [el modelo de confianza](groups.md) antes de compartir el bot.

Para configurarlo, añade el bot al grupo como admin (para que pueda fijar la barra de estado del grupo), vuelve a ejecutar `aipager config` y elige **Add a group scope**. El asistente añade a cada miembro con un rol, pregunta la carpeta raíz de los miembros y vuelve a cargar la configuración del aipager que está en marcha.

### <a id="a-dashboard-inside-telegram"></a>Un panel dentro de Telegram

`/app` abre la Mini App: tus sesiones con Detener, Terminar, Reiniciar, Cambiar nombre y el cambio de modo, un visor de diff para cada `Write` y `Edit`, los ajustes y, para el propietario, Informar de un problema. La sirve el propio aipager, está activada por defecto y cada petición se comprueba con la firma de Telegram. Si estás en más de un chat, una fila con los nombres de los chats arriba te deja pasar de uno a otro. Apágala con `aipager miniapp disable` o ponla en tu propia dirección ([cómo](security.md#mini-app-tunnel)).

### <a id="fifteen-languages"></a>Quince idiomas

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/languages.webp" width="420" align="right" alt="Una solicitud de permiso en persa, de derecha a izquierda, con números persas">

- Habla English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی y Tiếng Việt.
- Elige uno con `/language`, o deja que un chat privado siga el idioma de tu app de Telegram.
- El persa y el árabe se leen de derecha a izquierda, con sus propios números.
- Solo cambian los textos de aipager. Tus sesiones no: Claude responde en el idioma en que le escribes.
- Este README y [la documentación](#documentation) también están traducidos.

<br clear="right">

### <a id="and-the-small-things"></a>Y los detalles

- **Muchas sesiones, un bot.** Un mensaje normal va a la sesión con la que hablaste por última vez; responde a un mensaje de una sesión para hablar con esa, o envía `/x1 <message>`.
- **Nada se mezcla.** Un mensaje enviado mientras hay una solicitud de permiso o una pregunta abierta espera a que respondas, así nunca se lee como la respuesta.
- **Fotos, archivos y audios.** Los audios se pasan a texto en tu computadora, con el complemento opcional `voice`.
- **Te avisa.** Del contexto casi lleno, de la conversación compactada, de una sesión que terminó y de una que lleva rato sin dar noticias.
- **Las sesiones sobreviven a todo.** Claude se ejecuta dentro de `dtach`, así que sigue trabajando aunque cierres la terminal, se corte el SSH o reinicies aipager.

## <a id="how-it-works"></a>Cómo funciona

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/how-it-works.webp" width="100%" alt="Claude Code, su hook, aipager, Telegram y tus chats, en fila">

Claude Code se ejecuta en una terminal de verdad, dentro de `dtach`, para que nadie tenga que dejar una ventana abierta. Cada paso dispara un [hook](hooks.md) que envía un datagrama a aipager por un socket local, y aipager edita un mensaje en tu chat. Tus respuestas vuelven por el mismo camino: una decisión al hook que espera la llamada, o pulsaciones de teclas en la sesión. La terminal sigue siendo la fuente de verdad. Más en [la arquitectura](architecture.md).

## <a id="install"></a>Instalación

Linux o macOS, cualquier arquitectura. Necesitas Claude Code, autenticado en esta computadora, y un bot de Telegram: crea uno con [@BotFather](https://t.me/BotFather) y pulsa Start en él.

```sh
curl -fsSL https://aipager.run/install | sh   # uv, pipx o Homebrew: el que tengas
aipager config                                # tu token del bot, tu chat, los hooks de Claude Code
aipager service install                       # mantiene aipager en marcha aunque te desconectes
aipager session api                           # Claude Code en esta carpeta, y en tu chat
```

El instalador usa `uv` si lo tienes, si no `pipx`, si no (en macOS) Homebrew, e instala `uv` cuando no hay ninguno. `dtach` viene con aipager (el paquete [`dtach-bin`](https://pypi.org/project/dtach-bin/)), así que no hay nada más que instalar. `aipager config` es un asistente: comprueba el token del bot y tu chat, y luego añade los hooks de aipager a `~/.claude/settings.json` por ti. Nunca editas un archivo a mano.

<details>
<summary><b>Deja que un agente de programación lo instale</b></summary>

<br>

¿Tienes Claude Code o cualquier agente con shell? Crea el bot, pulsa Start en él y guarda el token del bot en un archivo que tu agente nunca tenga que leer (pega el token, pulsa Enter y luego Ctrl-D):

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

Luego dale a tu agente este prompt:

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

Si no sabes tu ID de usuario de Telegram, dilo; el agente puede averiguarlo. La guía que sigue, incluido cómo entregar el token de forma segura, es [Instalar con un agente](install-with-an-agent.md). Para scripts, `aipager setup --token-file FILE --chat-id ID` configura aipager sin hacer ninguna pregunta ([el comando setup](commands.md#command-line-aipager-setup-for-coding-agents)).

</details>

### <a id="other-ways-to-install"></a>Otras formas de instalar

```sh
uv tool install aipager                        # uv: recomendado en macOS, trae su propio Python
pipx install aipager                           # pipx
brew install dev-aly3n/tap/aipager             # Homebrew, en macOS y Linuxbrew (mira la nota de abajo)
nix profile install github:dev-aly3n/aipager   # Nix: claude no viene incluido (mira abajo)
snap install aipager                           # Snap: confinamiento estricto, claude incluido, carpetas de trabajo dentro de ~/
```

O ejecútalo en [Docker](#docker), con Python, Node, `claude` y `dtach` dentro.

<details>
<summary><b>Homebrew en macOS 26 (Tahoe)</b></summary>

<br>

La fórmula funciona cuando la bottle `python@3.12` de Homebrew y tus Command Line Tools de Xcode están en sintonía, pero de vez en cuando se desajustan. En macOS 26 la instalación falla con un error de símbolo `pyexpat _XML_SetAllocTrackerActivationThreshold` ([issue de upstream](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). Actualizar Xcode y las Command Line Tools suele arreglarlo, pero con `uv tool install aipager` no pasa en absoluto. La fórmula toma `dtach` de Homebrew e instala aipager en un entorno de Python que gestiona Homebrew.

</details>

### <a id="docker"></a>Docker

Una imagen autocontenida con Python, Node, `claude` y `dtach`, buena para un VPS, un NAS o una Raspberry Pi donde no quieres Python ni Node en el host. Para varias arquitecturas (amd64, arm64).

```sh
# 1. Ejecuta el asistente de configuración una vez (interactivo)
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. Arranca aipager (en segundo plano, con reinicio automático)
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

Monta bajo `/workspace` las carpetas que quieres que Claude edite. El montaje de `~/.claude` lleva tus credenciales de Claude y el historial de conversaciones: ejecuta `claude` una vez en el host para autenticarte, o `docker exec -it aipager claude` para autenticarte dentro del contenedor.

Etiquetas: `latest`, `1.3`, `1.3.8` (una por versión exacta y una por versión menor).

### <a id="nix"></a>Nix

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

Compila aipager desde el código fuente con nixpkgs fijados. `dtach` lo da Nix; `claude` no, así que instálalo aparte (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`, o sigue la documentación de Anthropic).

<details>
<summary><b>NixOS y Home Manager</b></summary>

<br>

Añade aipager como input del flake y pon su paquete en `environment.systemPackages`:

```nix
{
  inputs.aipager.url = "github:dev-aly3n/aipager";

  outputs = { self, nixpkgs, aipager, ... }: {
    nixosConfigurations.myhost = nixpkgs.lib.nixosSystem {
      modules = [({ pkgs, ... }: {
        environment.systemPackages = [
          aipager.packages.${pkgs.system}.default
        ];
      })];
    };
  };
}
```

Después, `aipager service install` crea una unidad de usuario de systemd.

</details>

## <a id="everyday-use"></a>El día a día

### <a id="in-telegram"></a>En Telegram

| Envía | Para |
|---|---|
| `/status` | Ver las sesiones de este chat, cada una con su estado y sus botones. |
| `/new x1` | Crear una sesión llamada x1. `/new x1 fix the tests` también envía su primer mensaje. |
| `/x1` | Hacer de x1 la sesión a la que van tus mensajes. `/x1 <message>` le envía un mensaje directamente. |
| `/stop` | Interrumpir la tarea en curso y descartar lo que está en cola. |
| `/mode` | Cambiar entre Modo Preguntar, Modo Automático y Modo Acceso total. |
| `/app` | Abrir la Mini App. |
| `/language` | Elegir el idioma en que aipager habla en este chat. |
| `/update` | Actualizar aipager y Claude Code, y reiniciar en el sitio (admins). |
| `/help` | Una guía corta por tareas. |

Cada comando, botón y ajuste está en [Comandos del bot](commands.md).

### <a id="on-the-command-line"></a>En la línea de comandos

```sh
aipager start                             # aipager, en primer plano
aipager session dev                       # crea aquí una sesión llamada dev, o conéctate a ella
aipager session dev --continue            # retoma la última conversación de Claude en esta carpeta
aipager session dev -- --resume abc1234   # cualquier otra opción de claude pasa tal cual
aipager doctor                            # comprueba la instalación
```

Para salir de una sesión sin detener a Claude, cierra la terminal o el panel de tmux: la sesión sigue en marcha, y el mismo comando te vuelve a conectar.

`aipager service install` mantiene aipager en segundo plano: una unidad de usuario de systemd en Linux (`~/.config/systemd/user/aipager.service`) o un agente de launchd en macOS (`~/Library/LaunchAgents/com.aipager.daemon.plist`). En los dos casos aipager se ejecuta en tu carpeta personal, donde empieza una sesión nueva salvo que elijas otra carpeta. `aipager service start`, `stop`, `status`, `logs` y `uninstall` lo gestionan.

## <a id="security"></a>Seguridad

- **Sin servidor de aipager.** aipager habla con la Bot API de Telegram con tu propio token del bot. No hay cuenta de aipager ni servicio de aipager.
- **Sin puerto abierto.** aipager solo escucha en loopback. La Mini App sale por un túnel de Cloudflare donde cada petición debe llevar la firma de Telegram, y `aipager miniapp disable` lo apaga.
- **Cada respuesta queda registrada.** Cada Permitir, Rechazar y respuesta se añade a `~/.claude/aipager-audit.jsonl`, con quién lo tocó en un grupo.
- **Claude Code sigue decidiendo.** Tu configuración de Claude Code dice qué hay que preguntar. aipager solo transmite la pregunta y nunca eleva privilegios: nada de sudo, todo se ejecuta con tu usuario.

El panorama completo, incluido qué protege tu credencial de Claude, está en [el modelo de seguridad](security.md).

## <a id="faq"></a>Preguntas frecuentes

<details>
<summary><b>¿En qué se diferencia de las apps móviles de Claude?</b></summary>

<br>

Las apps de Claude hablan con el asistente que aloja Anthropic. aipager te conecta con una sesión de Claude Code que corre en tu propia computadora: tu repositorio, tus herramientas, tu entorno.

</details>

<details>
<summary><b>¿Mi código sale de mi computadora?</b></summary>

<br>

aipager envía a Telegram solo lo que ves en tus chats, con tu propio token del bot; no hay ningún servidor de aipager en medio. Claude Code, por su parte, habla con Anthropic como siempre.

</details>

<details>
<summary><b>¿Qué es dtach?</b></summary>

<br>

Un pequeño programa que desacopla una terminal. Mantiene vivo a Claude Code cuando se cierra la terminal o se corta el SSH, así que una respuesta que envías una hora después todavía llega a una sesión viva. Viene con aipager.

</details>

<details>
<summary><b>¿Y si Claude se desvía?</b></summary>

<br>

Las solicitudes esperan tu toque, `/stop` interrumpe la tarea y descarta lo que esté en cola, `/kill` termina la sesión, y los botones de una tarea terminada se niegan.

</details>

<details>
<summary><b>¿Cómo lo actualizo?</b></summary>

<br>

Envía `/update`: comprueba aipager y Claude Code e instala lo que haya de nuevo, y 🔄 Reiniciar ahora vuelve a levantar aipager, sea un servicio o no. Tus sesiones siguen en marcha mientras tanto. En la línea de comandos, `aipager update` actualiza con el instalador al que pertenece aipager.

</details>

<details>
<summary><b>Uso Bedrock o Vertex. ¿Funcionan los botones de modelo?</b></summary>

<br>

El menú **Modelo** del teclado y la Mini App ofrecen los alias de familia de Claude Code (`sonnet`, `opus`, `haiku`, `fable`, `opusplan`) y modelos fijos como `claude-opus-5-5` y `claude-sonnet-5-5`. En la API de Anthropic, un alias apunta al modelo más reciente de su familia. En Bedrock y Vertex el mismo alias puede apuntar a una versión más antigua, según lo que ofrezca tu proveedor; envía `/model <full-id>` para elegir un modelo exacto.

</details>

<details>
<summary><b>Uso una clave de API, un gateway u otro proveedor. ¿Funciona aipager?</b></summary>

<br>

Sí. aipager ejecuta Claude Code tal como lo configuraste y responde las preguntas que Claude Code hace al empezar (la carpeta, la clave de API, las pantallas del primer uso), para que una sesión iniciada desde Telegram no se quede parada en ellas. Pon los ajustes en el bloque `env` de `~/.claude/settings.json` o en `~/.config/aipager/daemon.env` (una línea `KEY=value` por ajuste, sin comillas). Los botones Opus, Sonnet y Haiku siguen `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL` y `ANTHROPIC_DEFAULT_HAIKU_MODEL`, y cualquier otro modelo se puede escribir por su nombre. Anthropic solo da soporte a Claude Code con modelos de Claude. Consulta [Solución de problemas](troubleshooting.md#using-an-api-key-a-gateway-or-another-provider).

</details>

<details>
<summary><b>¿Cuánto cuesta?</b></summary>

<br>

Nada. aipager tiene licencia MIT, el bot es tuyo y Claude Code sigue usando el plan o la clave que ya usa.

</details>

## <a id="documentation"></a>Documentación

| Página | De qué trata |
|---|---|
| [Comandos del bot](commands.md) | Cada comando, botón, ajuste y opción de la línea de comandos |
| [Uso en equipo y grupos](groups.md) | Roles, carpetas raíz, quién puede tocar qué, el modelo de confianza |
| [Modelo de seguridad](security.md) | Límite de confianza, secretos, registro de auditoría, red |
| [Eventos de hook](hooks.md) | Qué hace cada hook de Claude Code en aipager |
| [Arquitectura](architecture.md) | Las piezas de aipager, sus archivos y sockets |
| [Solución de problemas](troubleshooting.md) | `aipager doctor` y arreglos para problemas frecuentes |
| [Informes de problemas](problem-reports.md) | Qué contiene un informe y cuándo lo propone aipager |
| [Instalar con un agente](install-with-an-agent.md) | La guía que sigue un agente de programación |

## <a id="contributing"></a>Contribuir

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach` viene con la dependencia `dtach-bin`, así que no hace falta nada más. Ejecuta las pruebas con un límite de memoria como arriba (en macOS, sin `systemd-run`): si no, una prueba desbocada puede quedarse con toda la memoria de la computadora. El proceso de publicación está en [CONTRIBUTING.md](../../../CONTRIBUTING.md).

## <a id="license"></a>Licencia

MIT. Mira [LICENSE](../../../LICENSE).

<br>

<div align="center">
<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="32" height="32" alt=""></a>
<br>
<sub><a href="https://aipager.run">aipager.run</a></sub>
</div>
