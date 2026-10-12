# aipager

<!-- i18n-source: README.md @ 6fc084f -->

> Esta página é uma tradução da página em inglês; se as duas não baterem, vale a versão em inglês. ([English](../../../README.md))

[![PyPI](https://img.shields.io/pypi/v/aipager?label=pypi&color=3775A9)](https://pypi.org/project/aipager/)
[![Python](https://img.shields.io/pypi/pyversions/aipager?color=3775A9)](https://pypi.org/project/aipager/)
[![License](https://img.shields.io/badge/license-MIT-black)](../../../LICENSE)

Controle remoto de sessões do [Claude Code](https://claude.com/claude-code) pelo Telegram. O Claude roda no seu computador dentro de um terminal separado (`dtach`), e você usa pelo celular: lê as respostas do Claude, manda mensagens, responde aos pedidos de permissão e às perguntas dele, e troca de uma sessão para outra, sem nenhuma conexão SSH aberta.

[aipager.run](https://aipager.run) · [Documentação](#docs) · [Histórico de mudanças](../../../CHANGELOG.md) · [Issues no GitHub](https://github.com/dev-aly3n/aipager/issues)

**Leia em outro idioma:** [English](../../../README.md) · [Español](../es/README.md) · [Русский](../ru/README.md) · [简体中文](../zh-Hans/README.md) · [Français](../fr/README.md) · [Deutsch](../de/README.md) · [한국어](../ko/README.md) · [العربية](../ar/README.md) · [Bahasa Indonesia](../id/README.md) · [Українська](../uk/README.md) · [Italiano](../it/README.md) · [Türkçe](../tr/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

## <a id="install"></a>Instalação

Você tem um agente de programação (o Claude Code, ou qualquer agente com shell)? Crie um bot com o [@BotFather](https://t.me/BotFather), toque em Start nele e confirme que você já fez login no Claude Code neste computador. Salve o token do bot num arquivo que o seu agente nunca precise ler (cole o token, aperte Enter e depois Ctrl-D):

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

Depois dê esta mensagem ao seu agente:

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

Se você não sabe o seu ID de usuário do Telegram, diga isso ao agente; ele consegue descobrir.

O guia que o agente segue, incluindo como receber o token com segurança, é [Instalar com um agente](install-with-an-agent.md). Para instalar você mesmo, continue lendo.

Linux ou macOS, qualquer arquitetura. O `dtach` é instalado sozinho pela dependência [`dtach-bin`](https://pypi.org/project/dtach-bin/); não precisa de nenhum pacote do sistema à parte.

### <a id="one-line-install-recommended"></a>Instalação em uma linha (recomendada)

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

Usa o `uv` se ele existir, senão o `pipx`, senão (só no macOS) o Homebrew. Se não encontrar nenhum, instala primeiro o `uv` (o gerenciador de ferramentas Python da Astral) e instala o aipager com ele.

### <a id="uv-recommended-on-macos"></a>uv (recomendado no macOS)

```sh
uv tool install aipager     # se o uv já estiver instalado
```

Ou, para instalar o uv primeiro:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install aipager
```

O uv traz o próprio interpretador Python, então funciona em qualquer versão do macOS ou do Linux, não importa o estado do Python do sistema; assim você foge do conflito entre o Python do Homebrew e o Xcode descrito na seção do Homebrew mais abaixo.

### <a id="pipx"></a>pipx

```sh
pipx install aipager
```

### <a id="homebrew-tap-macos-linuxbrew"></a>Homebrew tap (macOS, Linuxbrew)

> **Observação:** no macOS o caminho recomendado é `uv tool install aipager`. A fórmula do brew funciona quando o pacote `python@3.12` do Homebrew e o seu Xcode / Command Line Tools estão sincronizados, mas de tempos em tempos eles se desencontram; a última vez foi no **macOS Tahoe (26.x)**, onde a instalação falha com o erro de símbolo `pyexpat _XML_SetAllocTrackerActivationThreshold` ([issue no upstream](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). Atualizar o Xcode e o Command Line Tools costuma resolver, mas é mais fácil usar o uv.

```sh
brew install dev-aly3n/tap/aipager
```

Pega o `dtach` da fórmula padrão do Homebrew e instala o aipager num venv Python gerenciado pelo Homebrew.

### <a id="docker"></a>Docker

Imagem completa com python, node, `claude` e `dtach` dentro; boa para uma VPS, um NAS ou um Raspberry Pi onde você não quer Python nem Node no sistema. Multiarquitetura (amd64, arm64).

```sh
# 1. Rode o assistente de configuração uma vez (interativo)
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. Inicie o aipager (em segundo plano, com reinício automático)
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

Monte em `/workspace` as pastas que você quer que o Claude edite. A montagem de `~/.claude` leva suas credenciais do Claude e o histórico das conversas; para fazer login, rode `claude` uma vez no sistema, ou `docker exec -it aipager claude` para fazer login dentro do contêiner.

Tags: `latest`, `1.0`, `1.0.1` (uma para cada versão exata e uma para cada versão menor).

### <a id="nix-flake"></a>Nix flake

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

Compila o aipager do código-fonte com as dependências fixadas do nixpkgs. O `dtach` vem do Nix; o `claude` **não**: instale-o à parte (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`, ou pela documentação da Anthropic).

Para configurações declarativas do NixOS ou do Home Manager, adicione o aipager como entrada do flake e pegue o pacote dele em `environment.systemPackages`:

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

Depois, `aipager service install` cria um serviço de usuário do systemd.

### <a id="snap"></a>Snap

```sh
snap install aipager
```

Snap com confinamento estrito que traz python, node, `claude`, `dtach` e o aipager. Por causa do isolamento do snap, as pastas de trabalho precisam ficar dentro de `~/` (por exemplo `~/projects/foo`). O manifesto está em [`packaging/snap/`](../../../packaging/snap/).

## <a id="configure"></a>Configuração

```sh
aipager config
```

Um assistente interativo: pede o token do bot do Telegram (do [@BotFather](https://t.me/BotFather)) e o ID do chat, confere os dois e depois adiciona sozinho os hooks necessários em `~/.claude/settings.json`. Você nunca precisa editar nenhum arquivo à mão.

Vai configurar o aipager para alguém por um script ou um agente de programação? `aipager setup --token-file FILE --chat-id ID` faz o mesmo sem perguntas (veja [o comando setup](commands.md#command-line-aipager-setup-for-coding-agents) e [o guia para agentes](install-with-an-agent.md)).

## <a id="run"></a>Executar

```sh
aipager start
```

O aipager fica em primeiro plano. Abra uma sessão do Claude em outro terminal:

```sh
aipager session dev
```

Isso cria uma sessão do dtach chamada `claude-dev` rodando o Claude Code (ou se reconecta a ela, se já existir). O aipager a encontra em poucos segundos e o Telegram começa a mostrá-la. Você também pode criar uma sessão pelo Telegram: mande `/new dev` para o bot. Rode o mesmo comando de novo para se reconectar depois; para sair sem parar o Claude, feche o terminal ou o painel do tmux (a sessão continua rodando).

Se a sessão do dtach foi fechada (o computador reiniciou, por exemplo) mas você quer retomar a conversa do Claude a partir do disco, adicione `--continue`:

```sh
aipager session dev --continue    # a última conversa do claude nesta pasta
```

Você também pode passar `--resume <session-id>` (ou qualquer outra opção do claude) no final:

```sh
aipager session dev -- --resume abc1234
```

### <a id="run-as-a-service-survives-logout"></a>Rodar como serviço (continua depois de sair da sessão)

```sh
aipager service install
```

No Linux, escreve um serviço de usuário do systemd em `~/.config/systemd/user/aipager.service` e o inicia. No macOS, escreve um plist do launchd em `~/Library/LaunchAgents/com.aipager.daemon.plist` e o coloca para rodar. Subcomandos: `start`, `stop`, `status`, `logs`, `uninstall`.

## <a id="team-mode-groups-and-other-people"></a>Uso em equipe (grupos e outras pessoas)

Por padrão o aipager é um bot privado: você e o bot num chat privado. Para compartilhar, adicione um grupo do Telegram, os chats privados de outras pessoas com o bot, ou as duas coisas. Para um grupo: adicione o bot ao grupo (como admin, para ele poder fixar a barra de status do grupo), rode `aipager config` de novo e escolha **Add a group scope**. O assistente sugere você (o dono do seu próprio chat privado) como dono do grupo, adiciona cada membro com uma função (`owner`, `admin`, `user` ou `read_only`), pergunta a pasta raiz dos membros e recarrega a configuração do aipager sem reiniciar. No grupo, o bot só atende comandos, respostas às mensagens dele e menções (`@aipagerbot fix the tests`, `/jim run the tests`), e cada mensagem roda com a função de quem a mandou. Um membro `user` ou `read_only` só cria e mantém sessões dentro da pasta raiz do chat dele (por padrão `~/aipager-users/<label>`). **Adicionar um membro dá a ele permissão para rodar código no seu computador**: o modelo de confiança completo está em [Uso em equipe e grupos](groups.md).

## <a id="what-it-does"></a>O que ele faz

- Mostra no Telegram o que cada sessão do Claude Code está fazendo: se está trabalhando ou pronta, as ferramentas que roda, o % de contexto, o custo e as linhas alteradas, num cartão por tarefa, e depois a resposta do Claude
- Manda suas mensagens ao Claude **na hora**, mesmo enquanto ele trabalha: mande várias e o próprio Claude coloca na fila, igualzinho a digitar no terminal. 👀 quer dizer enviada, 👍 quer dizer que o Claude pegou
- Segura uma mensagem enquanto há um pedido de permissão ou uma pergunta aberta, para ela nunca ser lida como resposta, e a envia depois que você responde
- Mostra os pedidos de permissão e as perguntas do Claude com botões (Permitir, Negar, Sempre permitir, um por opção); um botão que sobrou de uma tarefa já terminada se recusa em vez de agir sobre o trabalho em andamento
- Três modos de permissão por sessão, trocados com `/mode`: 💬 Modo Perguntar (o Claude pergunta antes de cada ação), 🤖 Modo Automático, o padrão (a verificação de segurança do próprio Claude Code roda as ações seguras e segura as arriscadas para você decidir) e 🔓 Modo Acesso total (tudo roda sem nenhuma verificação; para admins, e para você no seu próprio chat privado)
- Cria sessões pelo chat: `/new x1` cria uma na hora (`/new x1 fix the tests` também manda a primeira mensagem), com o modo, o modelo e a pasta definidos em `/settings`, e botões para trocá-los
- Recebe fotos, arquivos e áudios (o áudio vira texto no seu computador, com o recurso opcional de voz)
- Avisa sobre o contexto quase cheio, a conversa compactada, uma sessão que foi encerrada e uma que está há um tempo sem dar notícias
- Roda várias sessões ao mesmo tempo com um único bot; uso em equipe opcional, com funções e regras por ferramenta ([Uso em equipe e grupos](groups.md))
- Bots observadores somente leitura, opcionais
- Fala English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی e Tiếng Việt: só os textos do aipager, nunca suas sessões nem as respostas do Claude ([Idiomas](commands.md#languages)). Este README e a documentação também estão traduzidos (veja **Leia em outro idioma** acima)

### <a id="mini-app"></a>Mini App

`/app` abre um painel dentro do Telegram: suas sessões com Parar, Encerrar, Reiniciar, Renomear e a troca de modo, um visualizador de diff para as mudanças de `Write`/`Edit`, as configurações e, para o dono, Relatar um problema. Quem serve é o próprio aipager, e ele vem **ligado por padrão**; cada pedido é conferido com a assinatura `initData` do Telegram. Se você está em mais de um chat (seu chat privado e um grupo), uma linha com os nomes dos chats no topo deixa você trocar entre eles. Gerencie com `aipager miniapp enable|disable|status`, ou aponte para o seu próprio endereço em vez do túnel gerenciado: veja [Modelo de segurança](security.md#mini-app-tunnel).

### <a id="note-on-model-buttons-bedrock--vertex-users"></a>Observação sobre os botões de modelo (usuários de Bedrock e Vertex)

O submenu **Modelo** do teclado fixo (e os seletores de modelo do Mini App) oferece os apelidos de família do Claude Code, `sonnet`, `opus`, `haiku`, `fable` e `opusplan`, e modelos fixos como `claude-opus-5-5` e `claude-sonnet-5-5` (a lista completa está em [Teclado fixo](commands.md#persistent-keyboard)). Na API da Anthropic, um apelido sempre aponta para o modelo mais novo da família. No **Bedrock** e no **Vertex**, esses mesmos apelidos podem apontar para versões mais antigas, dependendo do que o seu provedor tem. Se você usa esses serviços e quer um modelo específico, mande `/model <full-id>` pelo chat.

## <a id="developing-locally"></a>Desenvolver no seu computador

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

O `dtach` vem com a dependência `dtach-bin`, então não precisa de mais nada. Rode os testes com um limite de memória como acima (no macOS, sem `systemd-run`): senão, um teste descontrolado pode ocupar toda a memória do computador.

O processo de lançamento de versões está em [CONTRIBUTING.md](../../../CONTRIBUTING.md).

## <a id="license"></a>Licença

MIT; o texto completo está em [LICENSE](../../../LICENSE).

## <a id="docs"></a>Documentação

| Página | Do que trata |
|---|---|
| [Instalar com um agente](install-with-an-agent.md) | Os passos que um agente de programação segue para instalar e configurar o aipager para a pessoa dele |
| [Arquitetura](architecture.md) | Como o programa funciona, diagrama das partes, onde ficam os arquivos e os sockets |
| [Eventos de hook](hooks.md) | Cada evento que o aipager recebe do Claude Code, com os campos |
| [Comandos do bot](commands.md) | Comandos, botões, o Mini App, áudios e arquivos, todos os comandos `aipager` e as configurações lidas do ambiente |
| [Uso em equipe e grupos](groups.md) | Configuração para várias pessoas, funções, regras, registro de quem fez o quê |
| [Bots observadores](observers.md) | Bots somente leitura que recebem cópias dos avisos |
| [Solução de problemas](troubleshooting.md) | Falhas comuns e a tabela de soluções do `aipager doctor` |
| [Relatórios de problemas](problem-reports.md) | O que um relatório de problemas contém, para onde vai e quando o aipager o sugere |
| [Modelo de segurança](security.md) | Limite de confiança, segredos, registro de auditoria, conexões de rede |
