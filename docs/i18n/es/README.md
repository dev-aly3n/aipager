# aipager

<!-- i18n-source: README.md @ a175bc5 -->

> Esta página es una traducción de la página en inglés; si no coinciden, la versión en inglés es la correcta. ([English](../../../README.md))

[![PyPI](https://img.shields.io/pypi/v/aipager?label=pypi&color=3775A9)](https://pypi.org/project/aipager/)
[![Python](https://img.shields.io/pypi/pyversions/aipager?color=3775A9)](https://pypi.org/project/aipager/)
[![License](https://img.shields.io/badge/license-MIT-black)](../../../LICENSE)

Control remoto de sesiones de [Claude Code](https://claude.com/claude-code) desde Telegram. Claude se ejecuta en tu computadora dentro de una terminal separada (`dtach`) y tú lo usas desde el teléfono: lees las respuestas de Claude, le envías mensajes, respondes a sus solicitudes de permiso y a sus preguntas, y cambias de una sesión a otra, sin tener una conexión SSH abierta.

[aipager.run](https://aipager.run) · [Documentación](#docs) · [Historial de cambios](../../../CHANGELOG.md) · [Issues en GitHub](https://github.com/dev-aly3n/aipager/issues)

**Leer en otro idioma:** [English](../../../README.md) · [Русский](../ru/README.md) · [Português (Brasil)](../pt-BR/README.md) · [简体中文](../zh-Hans/README.md) · [Français](../fr/README.md) · [Deutsch](../de/README.md) · [한국어](../ko/README.md) · [العربية](../ar/README.md) · [Bahasa Indonesia](../id/README.md) · [Українська](../uk/README.md) · [Italiano](../it/README.md) · [Türkçe](../tr/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

## <a id="install"></a>Instalación

¿Tienes un agente de programación (Claude Code, o cualquier agente con shell)? Crea un bot con [@BotFather](https://t.me/BotFather), pulsa Start en él y asegúrate de haber iniciado sesión en Claude Code en esta computadora. Guarda el token del bot en un archivo que tu agente nunca tenga que leer (pega el token, pulsa Enter y luego Ctrl-D):

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

Luego dale este mensaje a tu agente:

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

Si no sabes tu ID de usuario de Telegram, díselo al agente; él puede encontrarlo.

La guía que sigue el agente, incluida la forma segura de entregarle el token, es [Instalar con un agente](install-with-an-agent.md). Para instalarlo tú mismo, sigue leyendo.

Linux o macOS, cualquier arquitectura. `dtach` se instala solo a través de la dependencia [`dtach-bin`](https://pypi.org/project/dtach-bin/); no hace falta ningún paquete del sistema aparte.

### <a id="one-line-install-recommended"></a>Instalación en una línea (recomendada)

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

Usa `uv` si está, si no `pipx`, y si no (solo en macOS) Homebrew. Si no encuentra ninguno, instala primero `uv` (el gestor de herramientas de Python de Astral) y instala aipager con él.

### <a id="uv-recommended-on-macos"></a>uv (recomendado en macOS)

```sh
uv tool install aipager     # si uv ya está instalado
```

O, para instalar uv primero:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install aipager
```

uv trae su propio intérprete de Python, así que funciona en cualquier versión de macOS o Linux, esté como esté el Python del sistema; así evitas el choque entre el Python de Homebrew y Xcode que se describe en la sección de Homebrew más abajo.

### <a id="pipx"></a>pipx

```sh
pipx install aipager
```

### <a id="homebrew-tap-macos-linuxbrew"></a>Homebrew tap (macOS, Linuxbrew)

> **Nota:** en macOS la vía recomendada es `uv tool install aipager`. La fórmula de brew funciona cuando el paquete `python@3.12` de Homebrew y tus Xcode / Command Line Tools están sincronizados, pero de vez en cuando se desajustan; la última vez en **macOS Tahoe (26.x)**, donde la instalación falla con el error de símbolo `pyexpat _XML_SetAllocTrackerActivationThreshold` ([issue en upstream](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). Actualizar Xcode y Command Line Tools suele arreglarlo, pero es más fácil usar uv.

```sh
brew install dev-aly3n/tap/aipager
```

Toma `dtach` de la fórmula estándar de Homebrew e instala aipager en un venv de Python gestionado por Homebrew.

### <a id="docker"></a>Docker

Imagen completa con python, node, `claude` y `dtach` incluidos; buena para un VPS, un NAS o una Raspberry Pi donde no quieres Python ni Node en el sistema. Multiarquitectura (amd64, arm64).

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

Monta bajo `/workspace` las carpetas que quieres que Claude edite. El montaje de `~/.claude` lleva tus credenciales de Claude y tu historial de conversaciones; para iniciar sesión, ejecuta `claude` una vez en el sistema, o `docker exec -it aipager claude` para iniciar sesión dentro del contenedor.

Etiquetas: `latest`, `1.3`, `1.3.0` (una por versión exacta y una por versión menor).

### <a id="nix-flake"></a>Nix flake

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

Compila aipager desde el código fuente con las dependencias fijadas de nixpkgs. `dtach` lo pone Nix; `claude` **no**: instálalo aparte (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`, o según la documentación de Anthropic).

Para configuraciones declarativas de NixOS o Home Manager, añade aipager como entrada del flake y toma su paquete en `environment.systemPackages`:

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

Después, `aipager service install` crea un servicio de usuario de systemd.

### <a id="snap"></a>Snap

```sh
snap install aipager
```

Snap con confinamiento estricto que incluye python, node, `claude`, `dtach` y aipager. Por el aislamiento de snap, las carpetas de trabajo tienen que estar bajo `~/` (por ejemplo `~/projects/foo`). El manifiesto está en [`packaging/snap/`](../../../packaging/snap/).

## <a id="configure"></a>Configuración

```sh
aipager config
```

Un asistente interactivo: te pide el token del bot de Telegram (de [@BotFather](https://t.me/BotFather)) y el ID del chat, los comprueba y luego añade solo los hooks necesarios a `~/.claude/settings.json`. Nunca tienes que editar ningún archivo a mano.

¿Vas a configurar aipager para alguien desde un script o un agente de programación? `aipager setup --token-file FILE --chat-id ID` hace lo mismo sin preguntas (mira [el comando setup](commands.md#command-line-aipager-setup-for-coding-agents) y [la guía para agentes](install-with-an-agent.md)).

## <a id="run"></a>Ejecutar

```sh
aipager start
```

aipager se queda en primer plano. Abre una sesión de Claude en otra terminal:

```sh
aipager session dev
```

Esto crea una sesión de dtach llamada `claude-dev` que ejecuta Claude Code (o se vuelve a conectar a ella si ya existe). aipager la encuentra en unos segundos y Telegram empieza a mostrarla. También puedes crear una sesión desde Telegram: envía `/new dev` al bot. Vuelve a ejecutar el mismo comando para reconectarte más tarde; para salir sin detener a Claude, cierra la terminal o el panel de tmux (la sesión sigue en marcha).

Si la sesión de dtach se cerró (la computadora se reinició, por ejemplo) pero quieres retomar la conversación de Claude desde el disco, añade `--continue`:

```sh
aipager session dev --continue    # la última conversación de claude en esta carpeta
```

También puedes pasar `--resume <session-id>` (o cualquier otra opción de claude) al final:

```sh
aipager session dev -- --resume abc1234
```

### <a id="run-as-a-service-survives-logout"></a>Ejecutar como servicio (sigue después de cerrar sesión)

```sh
aipager service install
```

En Linux escribe un servicio de usuario de systemd en `~/.config/systemd/user/aipager.service` y lo arranca. En macOS escribe un plist de launchd en `~/Library/LaunchAgents/com.aipager.daemon.plist` y lo pone en marcha. Subcomandos: `start`, `stop`, `status`, `logs`, `uninstall`.

## <a id="team-mode-groups-and-other-people"></a>Uso en equipo (grupos y otras personas)

Por defecto aipager es un bot privado: tú y el bot en un chat privado. Para compartirlo, añade un grupo de Telegram, los chats privados de otras personas con el bot, o las dos cosas. Para un grupo: añade el bot al grupo (como admin, para que pueda fijar la barra de estado del grupo), vuelve a ejecutar `aipager config` y elige **Add a group scope**. El asistente te propone a ti (el propietario de tu propio chat privado) como propietario del grupo, añade a cada miembro con un rol (`owner`, `admin`, `user` o `read_only`), pregunta la carpeta raíz de los miembros y vuelve a cargar la configuración de aipager sin reiniciarlo. En el grupo, el bot solo atiende comandos, respuestas a sus mensajes y menciones (`@aipagerbot fix the tests`, `/jim run the tests`), y cada mensaje se ejecuta con el rol de quien lo envía. Un miembro `user` o `read_only` solo crea y mantiene sesiones dentro de la carpeta raíz de su chat (por defecto `~/aipager-users/<label>`). **Añadir a un miembro le da permiso para ejecutar código en tu computadora**: el modelo de confianza completo está en [Uso en equipo y grupos](groups.md).

## <a id="what-it-does"></a>Qué hace

- Muestra en Telegram lo que hace cada sesión de Claude Code: si está trabajando o lista, las herramientas que ejecuta, el % de contexto, el costo y las líneas cambiadas, en una tarjeta por tarea, y luego la respuesta de Claude
- Envía tus mensajes a Claude **al momento**, incluso mientras trabaja: envía varios y Claude los pone en cola él mismo, igual que si escribieras en la terminal. 👀 significa enviado, 👍 significa que Claude lo tomó
- Retiene un mensaje mientras hay una solicitud de permiso o una pregunta abierta, para que nunca se lea como la respuesta a ella, y lo envía cuando ya respondiste
- Muestra las solicitudes de permiso y las preguntas de Claude con botones (Permitir, Rechazar, Permitir siempre, uno por opción); un botón que quedó de una tarea ya terminada se niega en lugar de actuar sobre el trabajo en curso
- Tres modos de permisos por sesión, que se cambian con `/mode`: 💬 Modo Preguntar (Claude pregunta antes de cada acción), 🤖 Modo Automático, el de por defecto (la comprobación de seguridad del propio Claude Code ejecuta las acciones seguras y detiene las riesgosas para que tú decidas) y 🔓 Modo Acceso total (todo se ejecuta sin ninguna comprobación; para admins, y para ti en tu propio chat privado)
- Crea sesiones desde el chat: `/new x1` crea una al momento (`/new x1 fix the tests` también envía su primer mensaje), con el modo, el modelo y la carpeta que fijaste en `/settings`, y botones para cambiarlos
- Acepta fotos, archivos y audios (el audio se pasa a texto en tu computadora, con el complemento opcional de voz)
- Te avisa del contexto casi lleno, de la conversación compactada, de una sesión que terminó y de una que lleva rato sin dar noticias
- Ejecuta varias sesiones a la vez con un solo bot; uso en equipo opcional con roles y reglas por herramienta ([Uso en equipo y grupos](groups.md))
- Habla English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی y Tiếng Việt: solo los textos de aipager, nunca tus sesiones ni las respuestas de Claude ([Idiomas](commands.md#languages)). Este README y la documentación también están traducidos (mira **Leer en otro idioma** arriba)

### <a id="mini-app"></a>Mini App

`/app` abre un panel dentro de Telegram: tus sesiones con Detener, Terminar, Reiniciar, Cambiar nombre y el cambio de modo, un visor de diff para los cambios de `Write`/`Edit`, los ajustes y, para el propietario, Informar de un problema. Lo sirve el propio aipager y está **activado por defecto**; cada solicitud se comprueba con la firma `initData` de Telegram. Si estás en más de un chat (tu chat privado y un grupo), una fila con los nombres de los chats arriba te deja pasar de uno a otro. Gestiónalo con `aipager miniapp enable|disable|status`, o ponlo en tu propia dirección en lugar del túnel gestionado: mira [Modelo de seguridad](security.md#mini-app-tunnel).

### <a id="note-on-model-buttons-bedrock--vertex-users"></a>Nota sobre los botones de modelo (usuarios de Bedrock y Vertex)

El submenú **Modelo** del teclado fijo (y los selectores de modelo de la Mini App) ofrece los alias de familia de Claude Code, `sonnet`, `opus`, `haiku`, `fable` y `opusplan`, y modelos fijos como `claude-opus-5-5` y `claude-sonnet-5-5` (la lista completa está en [Teclado fijo](commands.md#persistent-keyboard)). En la API de Anthropic, un alias siempre apunta al modelo más nuevo de su familia. En **Bedrock** y **Vertex** esos mismos alias pueden apuntar a versiones más antiguas según lo que tenga tu proveedor. Si usas esos servicios y quieres un modelo concreto, envía `/model <full-id>` desde el chat.

## <a id="developing-locally"></a>Desarrollar en tu computadora

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach` viene con la dependencia `dtach-bin`, así que no hace falta nada más. Ejecuta los tests con un límite de memoria como arriba (en macOS, sin `systemd-run`): si no, un test descontrolado puede llenar toda la memoria de la computadora.

El proceso de publicación de versiones está en [CONTRIBUTING.md](../../../CONTRIBUTING.md).

## <a id="license"></a>Licencia

MIT; el texto completo está en [LICENSE](../../../LICENSE).

## <a id="docs"></a>Documentación

| Página | De qué trata |
|---|---|
| [Instalar con un agente](install-with-an-agent.md) | Los pasos que sigue un agente de programación para instalar y configurar aipager para su persona |
| [Arquitectura](architecture.md) | Cómo está hecho el programa, diagrama de piezas, dónde están los archivos y los sockets |
| [Eventos de hook](hooks.md) | Cada evento que aipager recibe de Claude Code, con sus campos |
| [Comandos del bot](commands.md) | Comandos, botones, la Mini App, audios y archivos, todos los comandos `aipager` y los ajustes que se leen del entorno |
| [Uso en equipo y grupos](groups.md) | Configuración para varias personas, roles, reglas, registro de quién hizo qué |
| [Solución de problemas](troubleshooting.md) | Fallos frecuentes y la tabla de soluciones de `aipager doctor` |
| [Informes de problemas](problem-reports.md) | Qué contiene un informe de problemas, adónde va y cuándo lo propone aipager |
| [Modelo de seguridad](security.md) | Límite de confianza, secretos, registro de auditoría, conexiones de red |
