<div align="center">

<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="96" height="96" alt="aipager"></a>

# aipager

<!-- i18n-source: README.md @ e6f0c88 -->

<h3>O Claude vira mais um chat.</h3>

Suas sessões do [Claude Code](https://claude.com/claude-code) no Telegram, ao lado da sua família, dos seus amigos e da sua equipe.<br>
Elas rodam no seu próprio computador. Você acompanha, responde e passa o próximo trabalho de onde estiver.

<p>
<a href="https://pypi.org/project/aipager/"><img alt="PyPI" src="https://img.shields.io/pypi/v/aipager?style=flat-square&label=pypi&labelColor=181c20&color=2aabee"></a>
<a href="https://pypi.org/project/aipager/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/aipager?style=flat-square&labelColor=181c20&color=2aabee"></a>
<a href="https://github.com/dev-aly3n/aipager/actions/workflows/test.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/dev-aly3n/aipager/test.yml?branch=main&style=flat-square&label=tests&labelColor=181c20"></a>
<img alt="Linux and macOS" src="https://img.shields.io/badge/runs%20on-Linux%20%7C%20macOS-2aabee?style=flat-square&labelColor=181c20">
<a href="../../../LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-2aabee?style=flat-square&labelColor=181c20"></a>
</p>

**[Site](https://aipager.run)** · **[Instalação](#install)** · **[Documentação](#documentation)** · **[Segurança](security.md)** · **[Novidades](../../../CHANGELOG.md)**

**Leia em outro idioma:** [English](../../../README.md) · [Español](../es/README.md) · [Русский](../ru/README.md) · [简体中文](../zh-Hans/README.md) · [Français](../fr/README.md) · [Deutsch](../de/README.md) · [한국어](../ko/README.md) · [العربية](../ar/README.md) · [Bahasa Indonesia](../id/README.md) · [Українська](../uk/README.md) · [Italiano](../it/README.md) · [Türkçe](../tr/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

<sub>Esta página é uma tradução da página em inglês; se as duas não baterem, vale a versão em inglês. ([English](../../../README.md))</sub>

<br>

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/hero.webp" width="100%" alt="Uma mensagem digitada no Telegram chega ao Claude Code num terminal, e um cartão no chat se atualiza sozinho enquanto o Claude trabalha">

<sub>Você escreve no Telegram. O aipager digita isso no Claude Code no seu computador, e um cartão se atualiza sozinho enquanto a tarefa anda.</sub>

</div>

<br>

## <a id="why-aipager"></a>Por que o aipager

<table>
<tr>
<td width="33%" valign="top">

**📟 Um cartão ao vivo por tarefa**<br>
O que o Claude está fazendo agora: as ferramentas que roda, o contexto, o custo e as linhas alteradas, e depois a resposta. O cartão se atualiza sozinho, então o chat fica tranquilo.

</td>
<td width="33%" valign="top">

**⚡ Fale com ele no meio da tarefa**<br>
Sua mensagem é digitada na sessão na hora, mesmo enquanto o Claude trabalha, igualzinho a digitar no terminal. 👀 quer dizer enviada, 👍 quer dizer que o Claude pegou.

</td>
<td width="33%" valign="top">

**✅ Responda com um toque**<br>
Os pedidos de permissão e as perguntas do Claude chegam com um botão para cada resposta. Um botão que sobrou de uma tarefa terminada se recusa em vez de agir sobre a próxima.

</td>
</tr>
<tr>
<td width="33%" valign="top">

**🛡️ Três modos de permissão**<br>
💬 Modo Perguntar, 🤖 Modo Automático (o padrão das sessões novas) e 🔓 Modo Acesso total, definidos por sessão e trocados a qualquer hora com `/mode`.

</td>
<td width="33%" valign="top">

**👥 Feito para equipes**<br>
Coloque o bot num grupo. Cada mensagem roda com a função de quem a envia, os membros podem ficar presos à própria pasta, e cada toque fica no registro de auditoria.

</td>
<td width="33%" valign="top">

**🔒 Nada no meio**<br>
Sem servidor do aipager, sem conta, sem porta aberta. Seu token do bot, seu computador, seus chats.

</td>
</tr>
</table>

## <a id="a-quick-tour"></a>Um tour rápido

### <a id="questions-come-to-you"></a>As perguntas chegam até você

Quando o Claude pergunta algo ou precisa de permissão, a pergunta chega no seu chat com um botão para cada resposta, e a barra fixada diz o que está esperando por você. Seu toque é digitado na sessão como se você estivesse no teclado, e o terminal mostra a mesma resposta.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/question.webp" width="100%" alt="O Claude pergunta se aumenta o timeout ou espera o cookie; a resposta tocada no Telegram é escolhida no terminal">

### <a id="three-permission-modes"></a>Três modos de permissão

Cada sessão fica em um de três modos, trocado a qualquer hora com `/mode`, mesmo enquanto o Claude trabalha.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/modes.webp" width="100%" alt="A mesma tarefa no Modo Perguntar, no Modo Automático e no Modo Acesso total">

| Modo | O que acontece |
|---|---|
| 💬 **Modo Perguntar**<br>`/mode ask` | O Claude pergunta antes de cada ação. |
| 🤖 **Modo Automático**, o padrão<br>`/mode auto` | A verificação de segurança do próprio Claude Code roda as ações seguras e segura as arriscadas para você decidir. |
| 🔓 **Modo Acesso total**<br>`/mode full` | Tudo roda sem nenhuma verificação. Para admins, e para você no seu próprio chat privado com o bot. |

Entre o Modo Perguntar e o Modo Automático a troca é na hora, sem reiniciar. Entrar no Modo Acesso total ou sair dele reinicia a sessão e mantém a conversa. As perguntas do Claude chegam até você em qualquer modo.

### <a id="your-team-in-one-group"></a>Sua equipe, num grupo

Adicione o bot a um grupo e dê uma função a cada membro: `owner`, `admin`, `user` ou `read_only`. No grupo, o bot só responde a comandos, respostas às mensagens dele e menções (`@aipagerbot fix the tests`, `/web run the tests`), e cada mensagem roda com a função de quem a enviou: uma ferramenta que a função não pode usar é recusada antes de rodar. Um membro `user` ou `read_only` só trabalha dentro da própria pasta raiz (`~/aipager-users/<label>` por padrão). Outras pessoas também podem ter o próprio chat privado com o bot.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/team.webp" width="100%" alt="Um grupo de equipe em que um comando é recusado por causa da função de um membro enquanto os testes passam">

> **⚠️ Importante:** Adicionar um membro dá a ele o direito de rodar código no seu computador. Leia [o modelo de confiança](groups.md) antes de compartilhar o bot.

Para configurar, adicione o bot ao grupo como admin (para ele poder fixar a barra de status do grupo), rode `aipager config` de novo e escolha **Add a group scope**. O assistente adiciona cada membro com uma função, pergunta a pasta raiz dos membros e recarrega a configuração do aipager que está rodando.

### <a id="a-dashboard-inside-telegram"></a>Um painel dentro do Telegram

`/app` abre o Mini App: suas sessões com Parar, Encerrar, Reiniciar, Renomear e a troca de modo, um visualizador de diff para cada `Write` e `Edit`, as configurações e, para o dono, Relatar um problema. Quem serve é o próprio aipager, ele vem ligado por padrão e cada requisição é conferida com a assinatura do Telegram. Se você está em mais de um chat, uma fileira com os nomes dos chats no topo troca entre eles. Desligue com `aipager miniapp disable`, ou aponte para o seu próprio endereço ([como](security.md#mini-app-tunnel)).

### <a id="fifteen-languages"></a>Quinze idiomas

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/languages.webp" width="420" align="right" alt="Um pedido de permissão em persa, da direita para a esquerda, com algarismos persas">

- Fala English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی e Tiếng Việt.
- Escolha um com `/language`, ou deixe um chat privado seguir o idioma do seu app do Telegram.
- Persa e árabe são lidos da direita para a esquerda, com os próprios algarismos.
- Só os textos do aipager mudam. Suas sessões, não: o Claude responde no idioma em que você escreve para ele.
- Este README e [a documentação](#documentation) também estão traduzidos.

<br clear="right">

### <a id="and-the-small-things"></a>E os detalhes

- **Várias sessões, um bot.** Uma mensagem comum vai para a sessão com quem você falou por último; responda a uma mensagem de uma sessão para falar com ela, ou mande `/x1 <message>`.
- **Nada se mistura.** Uma mensagem enviada enquanto há um pedido de permissão ou uma pergunta aberta espera você responder, para nunca ser lida como a resposta.
- **Fotos, arquivos e áudios.** Os áudios viram texto no seu computador, com o extra opcional `voice`.
- **Ele avisa você.** Contexto quase cheio, conversa compactada, uma sessão que terminou e uma que está quieta há um tempo.
- **As sessões sobrevivem a tudo.** O Claude roda dentro do `dtach`, então continua trabalhando quando você fecha o terminal, perde o SSH ou reinicia o aipager.

## <a id="how-it-works"></a>Como funciona

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/how-it-works.webp" width="100%" alt="O Claude Code, o hook dele, o aipager, o Telegram e seus chats, em fila">

O Claude Code roda num terminal de verdade, dentro do `dtach`, para ninguém precisar deixar uma janela aberta. Cada passo dispara um [hook](hooks.md) que manda um datagrama para o aipager por um socket local, e o aipager edita uma mensagem no seu chat. Suas respostas voltam pelo mesmo caminho: uma decisão para o hook que segura a chamada, ou teclas digitadas na sessão. O terminal continua sendo a fonte da verdade. Mais na [arquitetura](architecture.md).

## <a id="install"></a>Instalação

Linux ou macOS, qualquer arquitetura. Você precisa do Claude Code com login feito neste computador e de um bot do Telegram: crie um com o [@BotFather](https://t.me/BotFather) e toque em Start nele.

```sh
curl -fsSL https://aipager.run/install | sh   # uv, pipx ou Homebrew: o que você tiver
aipager config                                # seu token do bot, seu chat, os hooks do Claude Code
aipager service install                       # mantém o aipager rodando depois que você sai
aipager session api                           # Claude Code nesta pasta, e no seu chat
```

O instalador usa o `uv` se você tiver, senão o `pipx`, senão (no macOS) o Homebrew, e instala o `uv` quando não há nenhum. O `dtach` vem com o aipager (o pacote [`dtach-bin`](https://pypi.org/project/dtach-bin/)), então não há mais nada para instalar. `aipager config` é um assistente: confere o token do bot e o seu chat, depois adiciona os hooks do aipager ao `~/.claude/settings.json` para você. Você nunca edita um arquivo à mão.

<details>
<summary><b>Deixe um agente de programação instalar</b></summary>

<br>

Tem o Claude Code ou qualquer agente com shell? Crie o bot, toque em Start nele e salve o token do bot num arquivo que o seu agente nunca precise ler (cole o token, aperte Enter e depois Ctrl-D):

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

Depois dê este prompt ao seu agente:

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

Se você não sabe o seu ID de usuário do Telegram, diga isso; o agente consegue descobrir. O guia que ele segue, inclusive como entregar o token com segurança, é [Instalar com um agente](install-with-an-agent.md). Para scripts, `aipager setup --token-file FILE --chat-id ID` configura o aipager sem nenhuma pergunta ([o comando setup](commands.md#command-line-aipager-setup-for-coding-agents)).

</details>

### <a id="other-ways-to-install"></a>Outras formas de instalar

```sh
uv tool install aipager                        # uv: recomendado no macOS, traz o próprio Python
pipx install aipager                           # pipx
brew install dev-aly3n/tap/aipager             # Homebrew, no macOS e no Linuxbrew (veja a nota abaixo)
nix profile install github:dev-aly3n/aipager   # Nix: o claude não vem junto (veja abaixo)
snap install aipager                           # Snap: confinamento estrito, claude incluso, pastas de trabalho dentro de ~/
```

Ou rode no [Docker](#docker), com Python, Node, `claude` e `dtach` dentro.

<details>
<summary><b>Homebrew no macOS 26 (Tahoe)</b></summary>

<br>

A fórmula funciona quando a bottle `python@3.12` do Homebrew e as suas Command Line Tools do Xcode estão em sintonia, mas de vez em quando elas se desencontram. No macOS 26 a instalação falha com um erro de símbolo `pyexpat _XML_SetAllocTrackerActivationThreshold` ([issue no upstream](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). Atualizar o Xcode e as Command Line Tools costuma resolver, mas com `uv tool install aipager` o problema nem aparece. A fórmula pega o `dtach` do Homebrew e instala o aipager num ambiente Python gerenciado pelo Homebrew.

</details>

### <a id="docker"></a>Docker

Uma imagem completa com Python, Node, `claude` e `dtach`, boa para um VPS, um NAS ou um Raspberry Pi onde você não quer Python nem Node no host. Multiarquitetura (amd64, arm64).

```sh
# 1. Rode o assistente de configuração uma vez (interativo)
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. Suba o aipager (em segundo plano, com reinício automático)
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

Monte em `/workspace` as pastas que você quer que o Claude edite. A montagem de `~/.claude` leva suas credenciais do Claude e o histórico de conversas: rode `claude` uma vez no host para fazer login, ou `docker exec -it aipager claude` para fazer login dentro do contêiner.

Tags: `latest`, `1.3`, `1.3.6` (uma por versão exata e uma por versão menor).

### <a id="nix"></a>Nix

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

Compila o aipager a partir do código-fonte com nixpkgs fixados. O `dtach` vem do Nix; o `claude` não, então instale à parte (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`, ou siga a documentação da Anthropic).

<details>
<summary><b>NixOS e Home Manager</b></summary>

<br>

Adicione o aipager como input do flake e coloque o pacote dele em `environment.systemPackages`:

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

Depois, `aipager service install` cria uma unidade de usuário do systemd.

</details>

## <a id="everyday-use"></a>No dia a dia

### <a id="in-telegram"></a>No Telegram

| Mande | Para |
|---|---|
| `/status` | Ver as sessões deste chat, cada uma com seu estado e seus botões. |
| `/new x1` | Criar uma sessão chamada x1. `/new x1 fix the tests` também manda a primeira mensagem. |
| `/x1` | Fazer de x1 a sessão para onde vão suas mensagens. `/x1 <message>` manda uma mensagem direto para ela. |
| `/stop` | Interromper a tarefa em andamento e descartar o que está na fila. |
| `/mode` | Trocar entre Modo Perguntar, Modo Automático e Modo Acesso total. |
| `/app` | Abrir o Mini App. |
| `/language` | Escolher o idioma em que o aipager fala neste chat. |
| `/update` | Atualizar o aipager e o Claude Code e reiniciar no lugar (admins). |
| `/help` | Um guia curto por tarefa. |

Todos os comandos, botões e configurações estão em [Comandos do bot](commands.md).

### <a id="on-the-command-line"></a>Na linha de comando

```sh
aipager start                             # o próprio aipager, em primeiro plano
aipager session dev                       # cria aqui uma sessão chamada dev, ou se conecta a ela
aipager session dev --continue            # retoma a última conversa do Claude nesta pasta
aipager session dev -- --resume abc1234   # qualquer outra opção do claude passa do jeito que está
aipager doctor                            # confere a instalação
```

Para sair de uma sessão sem parar o Claude, feche o terminal ou o painel do tmux: a sessão continua rodando, e o mesmo comando conecta você de novo.

`aipager service install` mantém o aipager rodando em segundo plano: uma unidade de usuário do systemd no Linux (`~/.config/systemd/user/aipager.service`) ou um agente do launchd no macOS (`~/Library/LaunchAgents/com.aipager.daemon.plist`). Nos dois casos o aipager roda na sua pasta pessoal, onde uma sessão nova começa se você não escolher outra pasta. `aipager service start`, `stop`, `status`, `logs` e `uninstall` cuidam dele.

## <a id="security"></a>Segurança

- **Sem servidor do aipager.** O aipager fala com a Bot API do Telegram usando o seu próprio token do bot. Não existe conta do aipager nem serviço do aipager.
- **Sem porta aberta.** O aipager só escuta no loopback. O Mini App sai por um túnel da Cloudflare em que cada requisição precisa levar a assinatura do Telegram, e `aipager miniapp disable` desliga tudo isso.
- **Cada resposta fica registrada.** Cada Permitir, Negar e resposta é acrescentado a `~/.claude/aipager-audit.jsonl`, com quem tocou num grupo.
- **Quem decide ainda é o Claude Code.** As suas configurações do Claude Code dizem o que precisa ser perguntado. O aipager só repassa a pergunta e nunca eleva privilégios: nada de sudo, tudo roda com o seu usuário.

O quadro completo, inclusive o que protege a sua credencial do Claude, está no [modelo de segurança](security.md).

## <a id="faq"></a>Perguntas frequentes

<details>
<summary><b>Qual é a diferença para os apps de celular do Claude?</b></summary>

<br>

Os apps do Claude falam com o assistente hospedado pela Anthropic. O aipager liga você a uma sessão do Claude Code rodando no seu próprio computador: seu repositório, suas ferramentas, seu ambiente.

</details>

<details>
<summary><b>Meu código sai do meu computador?</b></summary>

<br>

O aipager manda para o Telegram só o que você vê nos seus chats, com o seu próprio token do bot; não há nenhum servidor do aipager no meio. O próprio Claude Code fala com a Anthropic como sempre.

</details>

<details>
<summary><b>O que é o dtach?</b></summary>

<br>

Um programinha que desacopla um terminal. Ele mantém o Claude Code vivo quando o terminal fecha ou o SSH cai, então uma resposta que você manda uma hora depois ainda chega a uma sessão viva. Ele vem com o aipager.

</details>

<details>
<summary><b>E se o Claude sair dos trilhos?</b></summary>

<br>

Os pedidos esperam o seu toque, `/stop` interrompe a tarefa e descarta o que estiver na fila, `/kill` encerra a sessão, e os botões de uma tarefa terminada se recusam.

</details>

<details>
<summary><b>Como eu atualizo?</b></summary>

<br>

Mande `/update`: ele confere o aipager e o Claude Code e instala o que for novo, e 🔄 Reiniciar agora sobe o aipager de novo, seja um serviço ou não. Suas sessões continuam rodando enquanto isso. Na linha de comando, `aipager update` atualiza pelo instalador ao qual o aipager pertence.

</details>

<details>
<summary><b>Uso Bedrock ou Vertex. Os botões de modelo funcionam?</b></summary>

<br>

O menu **Modelo** do teclado e o Mini App oferecem os apelidos de família do Claude Code (`sonnet`, `opus`, `haiku`, `fable`, `opusplan`) e modelos fixos como `claude-opus-5-5` e `claude-sonnet-5-5`. Na API da Anthropic, um apelido aponta para o modelo mais novo da família. No Bedrock e no Vertex o mesmo apelido pode apontar para uma versão mais antiga, conforme o que o seu provedor oferece; mande `/model <full-id>` para escolher um modelo exato.

</details>

<details>
<summary><b>Quanto custa?</b></summary>

<br>

Nada. O aipager tem licença MIT, o bot é seu e o Claude Code continua usando o plano ou a chave que já usa.

</details>

## <a id="documentation"></a>Documentação

| Página | Do que trata |
|---|---|
| [Comandos do bot](commands.md) | Todos os comandos, botões, configurações e opções da linha de comando |
| [Uso em equipe e grupos](groups.md) | Funções, pastas raiz, quem pode tocar em quê, o modelo de confiança |
| [Modelo de segurança](security.md) | Limite de confiança, segredos, registro de auditoria, rede |
| [Eventos de hook](hooks.md) | O que cada hook do Claude Code faz no aipager |
| [Arquitetura](architecture.md) | As partes do aipager, os arquivos e os sockets |
| [Solução de problemas](troubleshooting.md) | `aipager doctor` e soluções para problemas comuns |
| [Relatórios de problemas](problem-reports.md) | O que um relatório contém e quando o aipager o sugere |
| [Instalar com um agente](install-with-an-agent.md) | O guia que um agente de programação segue |

## <a id="contributing"></a>Como contribuir

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

O `dtach` vem com a dependência `dtach-bin`, então não precisa de mais nada. Rode os testes com limite de memória como acima (no macOS, sem `systemd-run`): senão um teste descontrolado pode tomar toda a memória da máquina. O processo de lançamento está em [CONTRIBUTING.md](../../../CONTRIBUTING.md).

## <a id="license"></a>Licença

MIT. Veja [LICENSE](../../../LICENSE).

<br>

<div align="center">
<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="32" height="32" alt=""></a>
<br>
<sub><a href="https://aipager.run">aipager.run</a></sub>
</div>
