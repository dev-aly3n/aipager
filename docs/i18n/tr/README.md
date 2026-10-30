<div align="center">

<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="96" height="96" alt="aipager"></a>

# aipager

<!-- i18n-source: README.md @ bd8a636 -->

<h3>Claude artık sohbetlerinden biri.</h3>

[Claude Code](https://claude.com/claude-code) oturumların Telegram'da; ailenin, arkadaşlarının ve ekibinin hemen yanında.<br>
Kendi bilgisayarında çalışırlar. Nerede olursan ol onları takip eder, sorularını yanıtlar ve sıradaki işi verirsin.

<p>
<a href="https://pypi.org/project/aipager/"><img alt="PyPI" src="https://img.shields.io/pypi/v/aipager?style=flat-square&label=pypi&labelColor=181c20&color=2aabee"></a>
<a href="https://pypi.org/project/aipager/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/aipager?style=flat-square&labelColor=181c20&color=2aabee"></a>
<a href="https://github.com/dev-aly3n/aipager/actions/workflows/test.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/dev-aly3n/aipager/test.yml?branch=main&style=flat-square&label=tests&labelColor=181c20"></a>
<img alt="Linux and macOS" src="https://img.shields.io/badge/runs%20on-Linux%20%7C%20macOS-2aabee?style=flat-square&labelColor=181c20">
<a href="../../../LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-2aabee?style=flat-square&labelColor=181c20"></a>
</p>

**[Web sitesi](https://aipager.run)** · **[Kurulum](#install)** · **[Belgeler](#documentation)** · **[Güvenlik](security.md)** · **[Değişiklik günlüğü](../../../CHANGELOG.md)**

**Başka bir dilde oku:** [English](../../../README.md) · [Español](../es/README.md) · [Русский](../ru/README.md) · [Português (Brasil)](../pt-BR/README.md) · [简体中文](../zh-Hans/README.md) · [Français](../fr/README.md) · [Deutsch](../de/README.md) · [한국어](../ko/README.md) · [العربية](../ar/README.md) · [Bahasa Indonesia](../id/README.md) · [Українська](../uk/README.md) · [Italiano](../it/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

<sub>Bu sayfa İngilizceden çevrildi; ikisi birbirini tutmazsa İngilizce sürüm geçerlidir. ([English](../../../README.md))</sub>

<br>

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/hero.webp" width="100%" alt="Telegram'da yazılan bir mesaj terminaldeki Claude Code'a ulaşır ve sohbetteki tek bir kart, Claude çalışırken kendini günceller">

<sub>Sen Telegram'a yazarsın. aipager bunu bilgisayarındaki Claude Code'a yazar ve tek bir kart, görev ilerledikçe kendini günceller.</sub>

</div>

<br>

## <a id="why-aipager"></a>Neden aipager

<table>
<tr>
<td width="33%" valign="top">

**📟 Görev başına tek canlı kart**<br>
Claude'un şu an ne yaptığı: çalıştırdığı araçlar, bağlam, maliyet ve değişen satırlar, ardından yanıt. Kart kendini günceller, böylece sohbet sakin kalır.

</td>
<td width="33%" valign="top">

**⚡ İş sürerken de yaz**<br>
Mesajın, Claude çalışırken bile, hemen oturuma yazılır; tıpkı terminalde yazmak gibi. 👀 gönderildi demek, 👍 Claude onu aldı demek.

</td>
<td width="33%" valign="top">

**✅ Tek dokunuşla yanıtla**<br>
İzin istekleri ve Claude'un soruları her yanıt için bir düğmeyle gelir. Bitmiş bir görevden kalan düğme, sıradaki görevi etkilemek yerine çalışmayı reddeder.

</td>
</tr>
<tr>
<td width="33%" valign="top">

**🛡️ Üç izin modu**<br>
💬 Sor modu, 🤖 Otomatik mod (yeni oturumların varsayılanı) ve 🔓 Tam erişim modu; oturum başına ayarlanır ve `/mode` ile istediğin an değiştirilir.

</td>
<td width="33%" valign="top">

**👥 Ekipler için**<br>
Botu bir gruba ekle. Her mesaj gönderenin rolüyle çalışır, üyeler kendi klasörleriyle sınırlanabilir ve her dokunuş denetim kaydına yazılır.

</td>
<td width="33%" valign="top">

**🔒 Arada kimse yok**<br>
aipager sunucusu yok, hesap yok, açık port yok. Senin bot token'ın, senin bilgisayarın, senin sohbetlerin.

</td>
</tr>
</table>

## <a id="a-quick-tour"></a>Kısa bir tur

### <a id="questions-come-to-you"></a>Sorular sana gelir

Claude sana bir şey sorduğunda ya da izin istediğinde, soru her yanıt için bir düğmeyle sohbetine düşer ve sabitlenmiş çubuk seni neyin beklediğini söyler. Dokunuşun, klavyenin başındaymışsın gibi oturuma yazılır ve terminal de aynı yanıtı gösterir.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/question.webp" width="100%" alt="Claude zaman aşımını artırmayı mı yoksa çerezi beklemeyi mi soruyor; Telegram'da dokunulan yanıt terminalde seçiliyor">

### <a id="three-permission-modes"></a>Üç izin modu

Her oturum üç moddan birindedir ve `/mode` ile istediğin an, Claude çalışırken bile değiştirilir.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/modes.webp" width="100%" alt="Aynı görev Sor modunda, Otomatik modda ve Tam erişim modunda">

| Mod | Ne olur |
|---|---|
| 💬 **Sor modu**<br>`/mode ask` | Claude her işlemden önce sorar. |
| 🤖 **Otomatik mod**, varsayılan<br>`/mode auto` | Claude Code'un kendi güvenlik denetimi güvenli işlemleri çalıştırır, riskli olanları karar senin olsun diye durdurur. |
| 🔓 **Tam erişim modu**<br>`/mode full` | Her şey hiçbir denetim olmadan çalışır. Yöneticiler için ve botla olan kendi özel sohbetinde senin için. |

Sor modu ile Otomatik mod arasında geçiş yerinde olur, yeniden başlatma olmadan. Tam erişim moduna geçmek ya da ondan çıkmak oturumu yeniden başlatır ve konuşmayı korur. Claude'un soruları her modda sana gelir.

### <a id="your-team-in-one-group"></a>Ekibin, tek bir grupta

Botu bir gruba ekle ve her üyeye bir rol ver: `owner`, `admin`, `user` ya da `read_only`. Grupta bot yalnızca komutlara, kendi mesajlarına verilen yanıtlara ve onu anan mesajlara tepki verir (`@aipagerbot fix the tests`, `/web run the tests`) ve her mesaj gönderenin rolüyle çalışır: rolün kullanamadığı bir araç, çalışmadan önce reddedilir. `user` ya da `read_only` rolündeki bir üye yalnızca kendi kök klasöründe çalışır (varsayılan olarak `~/aipager-users/<label>`). Başka kişiler de botla kendi özel sohbetlerine sahip olabilir.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/team.webp" width="100%" alt="Testler geçerken bir üyenin rolü yüzünden bir komutun reddedildiği bir ekip grubu">

> **⚠️ Önemli:** Bir üye eklemek, ona bilgisayarında kod çalıştırma hakkı verir. Botu paylaşmadan önce [güven modelini](groups.md) oku.

Kurmak için botu gruba yönetici olarak ekle (grubun durum çubuğunu sabitleyebilsin diye), `aipager config` komutunu yeniden çalıştır ve **Add a group scope** seçeneğini seç. Sihirbaz her üyeyi bir rolle ekler, üyelerin kök klasörünü sorar ve çalışan aipager'ın ayarlarını yeniden yükler.

### <a id="a-dashboard-inside-telegram"></a>Telegram'ın içinde bir panel

`/app`, Mini App'i açar: Durdur, Sonlandır, Yeniden başlat, Yeniden adlandır ve mod değiştirme düğmeleriyle oturumların, her `Write` ve `Edit` için bir diff görüntüleyici, ayarlar ve sahibi için Sorun bildir. Onu aipager'ın kendisi sunar, varsayılan olarak açıktır ve her istek Telegram'ın imzasıyla denetlenir. Birden fazla sohbetteysen, üstteki sohbet adları satırı aralarında geçiş yapar. `aipager miniapp disable` ile kapat ya da kendi adresine yönlendir ([nasıl](security.md#mini-app-tunnel)).

### <a id="fifteen-languages"></a>On beş dil

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/languages.webp" width="420" align="right" alt="Farsça, sağdan sola ve Farsça rakamlarla bir izin isteği">

- English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی ve Tiếng Việt konuşur.
- `/language` ile birini seç ya da özel sohbetin Telegram uygulamanın dilini izlemesine izin ver.
- Farsça ve Arapça sağdan sola okunur, kendi rakamlarıyla.
- Yalnızca aipager'ın kendi metinleri değişir. Oturumların değişmez: Claude ona hangi dilde yazarsan o dilde yanıt verir.
- Bu README ve [belgeler](#documentation) de çevrildi.

<br clear="right">

### <a id="and-the-small-things"></a>Ve küçük şeyler

- **Çok oturum, tek bot.** Normal bir mesaj en son konuştuğun oturuma gider; o oturumla konuşmak için mesajlarından birine yanıt ver ya da `/x1 <message>` gönder.
- **Hiçbir şey karışmaz.** Açık bir izin isteği ya da soru varken gönderilen mesaj sen yanıtlayana kadar bekler, böylece asla yanıt gibi okunmaz.
- **Fotoğraf, dosya ve sesli mesaj.** Sesli mesajlar, isteğe bağlı `voice` ekiyle senin bilgisayarında metne çevrilir.
- **Sana haber verir.** Neredeyse dolan bağlam, sıkıştırılan konuşma, biten bir oturum ve uzun süredir ses vermeyen bir oturum.
- **Oturumlar her şeye dayanır.** Claude `dtach` içinde çalışır; terminali kapatsan, SSH kopsa ya da aipager'ı yeniden başlatsan da çalışmaya devam eder.

## <a id="how-it-works"></a>Nasıl çalışır

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/how-it-works.webp" width="100%" alt="Claude Code, hook'u, aipager, Telegram ve sohbetlerin, yan yana">

Claude Code gerçek bir terminalde, `dtach` içinde çalışır, böylece kimsenin bir pencereyi açık tutması gerekmez. Her adım bir [hook](hooks.md) tetikler; hook yerel bir soket üzerinden aipager'a tek bir datagram gönderir ve aipager sohbetindeki bir mesajı düzenler. Yanıtların da aynı yoldan geri döner: çağrıyı bekleten hook'a bir karar ya da oturuma tuş vuruşları. Terminal her zaman asıl kaynak olarak kalır. Ayrıntılar [mimaride](architecture.md).

## <a id="install"></a>Kurulum

Linux ya da macOS, her mimari. Bu bilgisayarda giriş yapılmış Claude Code'a ve bir Telegram botuna ihtiyacın var: [@BotFather](https://t.me/BotFather) ile bir tane oluştur ve içinde Başlat'a bas.

```sh
curl -fsSL https://aipager.run/install | sh   # uv, pipx ya da Homebrew: hangisi varsa
aipager config                                # bot token'ın, sohbetin, Claude Code'un hook'ları
aipager service install                       # bilgisayardan çıkış yapsan da aipager'ı çalışır tutar
aipager session api                           # bu klasörde ve sohbetinde Claude Code
```

Kurulum betiği varsa `uv` kullanır, yoksa `pipx`, o da yoksa (macOS'te) Homebrew; hiçbiri yoksa `uv` kurar. `dtach` aipager ile birlikte gelir ([`dtach-bin`](https://pypi.org/project/dtach-bin/) paketi), yani kurulacak başka bir şey yok. `aipager config` bir sihirbazdır: bot token'ını ve sohbetini denetler, sonra aipager'ın hook'larını senin için `~/.claude/settings.json` dosyasına ekler. Hiçbir dosyayı elle düzenlemezsin.

<details>
<summary><b>Bir kodlama ajanı kursun</b></summary>

<br>

Claude Code ya da shell'i olan başka bir ajanın mı var? Botu oluştur, içinde Başlat'a bas ve bot token'ını ajanının hiç okumak zorunda kalmayacağı bir dosyaya kaydet (token'ı yapıştır, Enter'a bas, sonra Ctrl-D):

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

Sonra ajanına şu mesajı ver:

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

Telegram kullanıcı kimliğini bilmiyorsan bunu söyle; ajan onu bulabilir. İzlediği rehber, token'ın güvenle nasıl teslim edileceği de dahil, [Ajanla kurulum](install-with-an-agent.md). Betikler için `aipager setup --token-file FILE --chat-id ID` aipager'ı hiç soru sormadan kurar ([setup komutu](commands.md#command-line-aipager-setup-for-coding-agents)).

</details>

### <a id="other-ways-to-install"></a>Diğer kurulum yolları

```sh
uv tool install aipager                        # uv: macOS'te önerilir, kendi Python'unu getirir
pipx install aipager                           # pipx
brew install dev-aly3n/tap/aipager             # Homebrew, macOS ve Linuxbrew'da (aşağıdaki nota bak)
nix profile install github:dev-aly3n/aipager   # Nix: claude dahil değil (aşağıya bak)
snap install aipager                           # Snap: sıkı yalıtım, claude içinde, çalışma klasörleri ~/ altında
```

Ya da içinde Python, Node, `claude` ve `dtach` olan [Docker](#docker) ile çalıştır.

<details>
<summary><b>macOS 26'da (Tahoe) Homebrew</b></summary>

<br>

Formül, Homebrew'un `python@3.12` bottle'ı ile Xcode Command Line Tools'un uyumlu olduğu sürece çalışır, ama ikisi zaman zaman birbirinden kopar. macOS 26'da kurulum `pyexpat _XML_SetAllocTrackerActivationThreshold` sembol hatasıyla başarısız olur ([upstream issue](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). Xcode'u ve Command Line Tools'u güncellemek genelde düzeltir, ama `uv tool install aipager` ile bu sorun hiç çıkmaz. Formül `dtach`'ı Homebrew'dan alır ve aipager'ı Homebrew'un yönettiği bir Python ortamına kurar.

</details>

### <a id="docker"></a>Docker

Python, Node, `claude` ve `dtach` içeren bağımsız bir imaj; bilgisayarın kendisine Python ya da Node kurmak istemediğin bir VPS, NAS ya da Raspberry Pi için ideal. Çoklu mimari (amd64, arm64).

```sh
# 1. Kurulum sihirbazını bir kez çalıştır (etkileşimli)
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. aipager'ı başlat (arka planda, otomatik yeniden başlatmayla)
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

Claude'un düzenlemesini istediğin klasörleri `/workspace` altına bağla. `~/.claude` bağlaması Claude kimlik bilgilerini ve konuşma geçmişini taşır: giriş yapmak için konteynerin dışında, bilgisayarın kendisinde bir kez `claude` çalıştır ya da konteynerin içinde giriş için `docker exec -it aipager claude` kullan.

Etiketler: `latest`, `1.4`, `1.4.0` (biri tam sürüm, biri küçük sürüm için).

### <a id="nix"></a>Nix

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

aipager'ı sabitlenmiş nixpkgs ile kaynaktan derler. `dtach` Nix'ten gelir; `claude` gelmez, onu ayrıca kur (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code` ya da Anthropic'in belgelerini izle).

<details>
<summary><b>NixOS ve Home Manager</b></summary>

<br>

aipager'ı bir flake girdisi olarak ekle ve paketini `environment.systemPackages` içine koy:

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

Ardından `aipager service install` bir systemd kullanıcı birimi kurar.

</details>

## <a id="everyday-use"></a>Günlük kullanım

### <a id="in-telegram"></a>Telegram'da

| Gönder | Amaç |
|---|---|
| `/status` | Bu sohbetin oturumlarını, her biri durumu ve düğmeleriyle görmek. |
| `/new x1` | x1 adında bir oturum başlatmak. `/new x1 fix the tests` ilk mesajı da gönderir. |
| `/x1` | x1'i mesajlarının gideceği oturum yapmak. `/x1 <message>` ona doğrudan bir mesaj gönderir. |
| `/stop` | Süren görevi kesmek ve sırada bekleyenleri atmak. |
| `/mode` | Sor modu, Otomatik mod ve Tam erişim modu arasında geçmek. |
| `/app` | Mini App'i açmak. |
| `/language` | aipager'ın bu sohbette konuşacağı dili seçmek. |
| `/update` | aipager'ı ve Claude Code'u güncelleyip yerinde yeniden başlatmak (yöneticiler). |
| `/help` | Göreve göre kısa bir rehber. |

Her komut, düğme ve ayar [Bot komutları](commands.md) sayfasında.

### <a id="on-the-command-line"></a>Komut satırında

```sh
aipager start                             # aipager'ın kendisi, ön planda
aipager session dev                       # burada dev adında bir oturum başlat ya da ona bağlan
aipager session dev --continue            # bu klasördeki son Claude konuşmasını sürdür
aipager session dev -- --resume abc1234   # claude'un diğer seçenekleri olduğu gibi geçer
aipager doctor                            # kurulumu denetle
```

Claude'u durdurmadan bir oturumdan çıkmak için terminali ya da tmux bölmesini kapat: oturum çalışmaya devam eder ve aynı komut seni yeniden bağlar.

`aipager service install`, aipager'ı arka planda çalışır tutar: Linux'ta bir systemd kullanıcı birimi (`~/.config/systemd/user/aipager.service`), macOS'te bir launchd ajanı (`~/Library/LaunchAgents/com.aipager.daemon.plist`). İki durumda da aipager ev klasöründe çalışır; başka bir klasör seçmezsen yeni oturum da orada başlar. `aipager service start`, `stop`, `status`, `logs` ve `uninstall` onu yönetir.

## <a id="security"></a>Güvenlik

- **aipager sunucusu yok.** aipager, Telegram Bot API ile senin bot token'ınla konuşur. aipager hesabı da aipager hizmeti de yoktur.
- **Açık port yok.** aipager yalnızca loopback'i dinler. Mini App, her isteğin Telegram'ın imzasını taşıması gereken bir Cloudflare tüneliyle dışarı çıkar ve `aipager miniapp disable` onu kapatır.
- **Her yanıt kayıtta.** Her İzin ver, Reddet ve yanıt `~/.claude/aipager-audit.jsonl` dosyasına eklenir; grupta kimin dokunduğuyla birlikte.
- **Kararı yine Claude Code verir.** Neyin sorulacağını Claude Code ayarların belirler. aipager yalnızca soruyu iletir ve asla yetki yükseltmez: sudo yok, her şey senin kullanıcınla çalışır.

Claude kimlik bilgini neyin koruduğu da dahil tüm tablo [güvenlik modelinde](security.md).

## <a id="faq"></a>Sık sorulanlar

<details>
<summary><b>Claude'un mobil uygulamalarından farkı ne?</b></summary>

<br>

Claude'un uygulamaları Anthropic'in barındırdığı asistanla konuşur. aipager seni kendi bilgisayarında çalışan bir Claude Code oturumuna bağlar: senin repon, senin araçların, senin ortamın.

</details>

<details>
<summary><b>Kodum bilgisayarımdan çıkıyor mu?</b></summary>

<br>

aipager, Telegram'a yalnızca sohbetlerinde gördüklerini kendi bot token'ınla gönderir; arada hiçbir aipager sunucusu yok. Claude Code'un kendisi ise her zamanki gibi Anthropic ile konuşur.

</details>

<details>
<summary><b>dtach nedir?</b></summary>

<br>

Bir terminali ayıran minik bir program. Terminal kapandığında ya da SSH koptuğunda Claude Code'u canlı tutar, böylece bir saat sonra gönderdiğin yanıt bile canlı bir oturuma ulaşır. aipager ile birlikte gelir.

</details>

<details>
<summary><b>Ya Claude yoldan çıkarsa?</b></summary>

<br>

İstekler senin dokunuşunu bekler, `/stop` görevi keser ve sırada ne varsa atar, `/kill` oturumu sonlandırır ve bitmiş bir görevin düğmeleri çalışmayı reddeder.

</details>

<details>
<summary><b>Nasıl güncellerim?</b></summary>

<br>

`/update` gönder: aipager'ı ve Claude Code'u denetler ve yeni olanı kurar; “🔄 Şimdi yeniden başlat” da aipager'ı, hizmet olarak çalışsın ya da çalışmasın, yeniden ayağa kaldırır. Oturumların bu sırada çalışmaya devam eder. Komut satırında `aipager update`, aipager'ı kurulduğu araçla günceller.

</details>

<details>
<summary><b>Bedrock ya da Vertex kullanıyorum. Model düğmeleri çalışır mı?</b></summary>

<br>

Klavyedeki **Model** menüsü ve Mini App, Claude Code'un aile takma adlarını (`sonnet`, `opus`, `haiku`, `fable`, `opusplan`) ve `claude-opus-5-5`, `claude-sonnet-5-5` gibi sabit modelleri sunar. Anthropic API'sinde bir takma ad, ailesinin en yeni modelini gösterir. Bedrock ve Vertex'te aynı takma ad, sağlayıcının sunduğuna göre daha eski bir sürümü gösterebilir; belirli bir modeli seçmek için `/model <full-id>` gönder.

</details>

<details>
<summary><b>API anahtarı, bir gateway ya da başka bir sağlayıcı kullanıyorum. aipager çalışır mı?</b></summary>

<br>

Evet. aipager, Claude Code'u senin kurduğun gibi çalıştırır ve Claude Code'un başlarken sorduğu soruları (klasör, API anahtarı, ilk açılış ekranları) yanıtlar; böylece Telegram'dan başlatılan bir oturum bunlarda takılmaz. Ayarları `~/.claude/settings.json` içindeki `env` bloğuna ya da `~/.config/aipager/daemon.env` dosyasına koy (her ayar için bir `KEY=value` satırı, tırnaksız). Opus, Sonnet ve Haiku düğmeleri `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL` ve `ANTHROPIC_DEFAULT_HAIKU_MODEL` değerlerini izler, başka herhangi bir model de adıyla yazılabilir. Anthropic, Claude Code'u yalnızca Claude modelleriyle destekler. [Sorun giderme](troubleshooting.md#using-an-api-key-a-gateway-or-another-provider) sayfasına bak.

</details>

<details>
<summary><b>Ücreti ne kadar?</b></summary>

<br>

Hiçbir şey. aipager MIT lisanslıdır, bot senindir ve Claude Code zaten kullandığı planı ya da anahtarı kullanmaya devam eder.

</details>

## <a id="documentation"></a>Belgeler

| Sayfa | İçinde ne var |
|---|---|
| [Bot komutları](commands.md) | Her komut, düğme, ayar ve komut satırı seçeneği |
| [Ekip modu ve gruplar](groups.md) | Roller, kök klasörler, kimin neye dokunabileceği, güven modeli |
| [Güvenlik modeli](security.md) | Güven sınırı, gizli bilgiler, denetim kaydı, ağ |
| [Hook olayları](hooks.md) | Claude Code'un her hook'unun aipager'da ne yaptığı |
| [Mimari](architecture.md) | aipager'ın parçaları, dosyaları ve soketleri |
| [Sorun giderme](troubleshooting.md) | `aipager doctor` ve sık sorunların çözümleri |
| [Sorun raporları](problem-reports.md) | Bir raporda ne olduğu ve aipager'ın onu ne zaman önerdiği |
| [Ajanla kurulum](install-with-an-agent.md) | Bir kodlama ajanının izlediği rehber |

## <a id="contributing"></a>Katkıda bulunmak

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach`, `dtach-bin` bağımlılığıyla gelir, başka bir şey gerekmez. Testleri yukarıdaki gibi bellek sınırıyla çalıştır (macOS'te `systemd-run` olmadan): yoksa kontrolden çıkan bir test bilgisayarın tüm belleğini alabilir. Sürüm yayınlama süreci [CONTRIBUTING.md](../../../CONTRIBUTING.md) dosyasında.

## <a id="license"></a>Lisans

MIT. Bkz. [LICENSE](../../../LICENSE).

<br>

<div align="center">
<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="32" height="32" alt=""></a>
<br>
<sub><a href="https://aipager.run">aipager.run</a></sub>
</div>
