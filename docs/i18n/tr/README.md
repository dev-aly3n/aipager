# aipager

<!-- i18n-source: README.md @ a175bc5 -->

> Bu sayfa İngilizceden çevrildi; ikisi birbirini tutmazsa İngilizce sürüm geçerlidir. ([English](../../../README.md))

[![PyPI](https://img.shields.io/pypi/v/aipager?label=pypi&color=3775A9)](https://pypi.org/project/aipager/)
[![Python](https://img.shields.io/pypi/pyversions/aipager?color=3775A9)](https://pypi.org/project/aipager/)
[![License](https://img.shields.io/badge/license-MIT-black)](../../../LICENSE)

[Claude Code](https://claude.com/claude-code) oturumları için Telegram üzerinden bir uzaktan kumanda. Claude senin bilgisayarında, ayrılmış bir terminalde (`dtach`) çalışır; sen de onu telefonundan kullanırsın: Claude'un yanıtlarını okur, ona mesaj gönderir, izin isteklerini ve sorularını yanıtlar, oturumlar arasında geçiş yaparsın; bunun için açık bir SSH bağlantısı tutman gerekmez.

[aipager.run](https://aipager.run) · [Belgeler](#docs) · [Değişiklik günlüğü](../../../CHANGELOG.md) · [GitHub'daki issue'lar](https://github.com/dev-aly3n/aipager/issues)

**Başka bir dilde oku:** [English](../../../README.md) · [Español](../es/README.md) · [Русский](../ru/README.md) · [Português (Brasil)](../pt-BR/README.md) · [简体中文](../zh-Hans/README.md) · [Français](../fr/README.md) · [Deutsch](../de/README.md) · [한국어](../ko/README.md) · [العربية](../ar/README.md) · [Bahasa Indonesia](../id/README.md) · [Українська](../uk/README.md) · [Italiano](../it/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

## <a id="install"></a>Kurulum

Bir kodlama ajanın mı var (Claude Code ya da shell kullanabilen herhangi bir ajan)? [@BotFather](https://t.me/BotFather) ile bir bot oluştur, botta Start'a dokun ve bu bilgisayardaki Claude Code'un hesabına giriş yaptığından emin ol. Bot token'ını ajanın hiç okumak zorunda kalmayacağı bir dosyaya kaydet (token'ı yapıştır, Enter'a, sonra Ctrl-D'ye bas):

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

Telegram kimliğini bilmiyorsan ajana bunu söyle; onu bulabilir.

Ajanın izlediği talimatlar, token'ı ona güvenle nasıl vereceğin de dahil, [Ajanla kurulum](install-with-an-agent.md) sayfasındadır. Kendin kurmak için okumaya devam et.

Linux ya da macOS, herhangi bir mimari. `dtach`, [`dtach-bin`](https://pypi.org/project/dtach-bin/) bağımlılığı sayesinde kendiliğinden kurulur: ayrı bir sistem paketi gerekmez.

### <a id="one-line-install-recommended"></a>Tek satırla kurulum (önerilen)

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

Varsa `uv`, yoksa `pipx`, o da yoksa (yalnızca macOS'ta) Homebrew kullanır. Hiçbiri yoksa önce `uv` aracını (Astral'ın Python araç yöneticisi) kurar ve aipager'ı onunla kurar.

### <a id="uv-recommended-on-macos"></a>uv (macOS'ta önerilen)

```sh
uv tool install aipager     # uv zaten kuruluysa
```

Ya da önce uv aracını kurmak için:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install aipager
```

uv kendi Python yorumlayıcısını getirir, bu yüzden sistem Python'u ne durumda olursa olsun her macOS ya da Linux sürümünde çalışır; böylece aşağıdaki Homebrew bölümünde anlatılan Homebrew Python'u ile Xcode arasındaki çakışmadan kaçınmış olursun.

### <a id="pipx"></a>pipx

```sh
pipx install aipager
```

### <a id="homebrew-tap-macos-linuxbrew"></a>Homebrew tap (macOS, Linuxbrew)

> **Not:** macOS'ta önerilen yol `uv tool install aipager`. brew formülü, Homebrew'un `python@3.12` paketi ile Xcode / Command Line Tools uyumlu olduğunda çalışır, ama zaman zaman aralarında uyumsuzluk çıkar; en son **macOS Tahoe (26.x)** üzerinde, kurulum `pyexpat _XML_SetAllocTrackerActivationThreshold` sembol hatasıyla duruyordu ([upstream issue](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). Xcode'u ve Command Line Tools'u güncellemek genelde işe yarar, ama uv kullanmak daha kolay.

```sh
brew install dev-aly3n/tap/aipager
```

`dtach` paketini Homebrew'un standart formülünden alır ve aipager'ı Homebrew'un yönettiği bir Python venv'ine kurar.

### <a id="docker"></a>Docker

İçinde python, node, `claude` ve `dtach` hazır gelen bir imaj; sisteme Python ya da Node kurmak istemediğin bir VPS, NAS ya da Raspberry Pi için kullanışlı. Birden çok mimari (amd64, arm64).

```sh
# 1. Kurulum sihirbazını bir kez çalıştır (etkileşimli)
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. aipager'ı çalıştır (arka planda, otomatik yeniden başlatmayla)
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

Claude'un düzenlemesini istediğin klasörleri `/workspace` altına bağla. `~/.claude` klasörünü bağlamak Claude kimlik bilgilerini ve konuşma geçmişini taşır; giriş yapmak için sistemde bir kez `claude` çalıştır ya da konteynerin içinde giriş yapmak için `docker exec -it aipager claude` kullan.

Etiketler: `latest`, `1.3`, `1.3.4` (tam sürüm ve küçük sürüm başına birer tane).

### <a id="nix-flake"></a>Nix flake

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

aipager'ı nixpkgs'ten sabitlenmiş bağımlılıklarla kaynaktan derler. `dtach` paketini Nix sağlar; `claude` ise **sağlanmaz**: onu ayrıca kur (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code` ya da Anthropic'in belgelerine göre).

Bildirimsel bir NixOS ya da Home Manager yapılandırması için aipager'ı bir flake girdisi olarak ekle ve paketini `environment.systemPackages` içine al:

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

Ardından `aipager service install` bir systemd kullanıcı hizmeti kurar.

### <a id="snap"></a>Snap

```sh
snap install aipager
```

İçinde python, node, `claude`, `dtach` ve aipager bulunan, sıkı yalıtımlı bir snap. Snap'in korumalı alanı yüzünden çalışma klasörleri `~/` altında olmalı (örneğin `~/projects/foo`). Bildirim dosyası [`packaging/snap/`](../../../packaging/snap/) içinde.

## <a id="configure"></a>Yapılandırma

```sh
aipager config
```

Etkileşimli bir sihirbaz: Telegram bot token'ını ([@BotFather](https://t.me/BotFather)'dan) ve sohbet kimliğini sorar, ikisini doğrular ve gereken hook'ları `~/.claude/settings.json` dosyasına kendisi ekler. Hiçbir dosyayı elle düzenlemen gerekmez.

aipager'ı bir betikten ya da bir kodlama ajanıyla başkası için mi kuruyorsun? `aipager setup --token-file FILE --chat-id ID` aynı şeyi soru sormadan yapar (bkz. [setup komutu](commands.md#command-line-aipager-setup-for-coding-agents) ve [ajanlar için talimatlar](install-with-an-agent.md)).

## <a id="run"></a>Çalıştırma

```sh
aipager start
```

aipager ön planda kalır. Başka bir terminalde bir Claude oturumu başlat:

```sh
aipager session dev
```

Bu, içinde Claude Code çalışan `claude-dev` adlı bir dtach oturumu oluşturur (zaten varsa ona bağlanır). aipager onu birkaç saniye içinde bulur ve Telegram göstermeye başlar. Oturumu Telegram'dan da başlatabilirsin: bota `/new dev` gönder. Sonra yeniden bağlanmak için aynı komutu çalıştır; Claude'u durdurmadan çıkmak için terminali ya da tmux bölmesini kapat (oturum çalışmaya devam eder).

dtach oturumu kapandıysa (örneğin bilgisayar yeniden başladıysa) ve Claude'un konuşmasına diskten devam etmek istiyorsan `--continue` ekle:

```sh
aipager session dev --continue    # bu klasördeki son claude konuşması
```

Sona `--resume <session-id>` (ya da başka herhangi bir claude bayrağı) da ekleyebilirsin:

```sh
aipager session dev -- --resume abc1234
```

### <a id="run-as-a-service-survives-logout"></a>Hizmet olarak çalıştırma (oturumu kapatınca da çalışır)

```sh
aipager service install
```

Linux'ta bu, `~/.config/systemd/user/aipager.service` içine bir systemd kullanıcı hizmeti yazar ve başlatır. macOS'ta `~/Library/LaunchAgents/com.aipager.daemon.plist` içine bir launchd plist'i yazar ve başlatır. Alt komutlar: `start`, `stop`, `status`, `logs`, `uninstall`.

## <a id="team-mode-groups-and-other-people"></a>Ekip modu (gruplar ve diğer kişiler)

Varsayılan olarak aipager kişisel bir bottur: sen ve bot, tek bir özel sohbette. Paylaşmak için bir Telegram grubu, başka kişilerin botla olan özel sohbetlerini ya da ikisini birden ekle. Grup için: botu gruba ekle (grubun durum satırını sabitleyebilmesi için yönetici olarak), `aipager config` komutunu yeniden çalıştır ve **Add a group scope** seçeneğini seç. Sihirbaz seni (kendi özel sohbetinin sahibi olarak) grubun sahibi olarak önerir, her üyeyi bir rolle (`owner`, `admin`, `user` ya da `read_only`) ekler, üyelerin kök klasörünü sorar ve aipager'ın ayarlarını yeniden başlatmadan yeniden yükler. Grupta bot yalnızca komutlara, kendi mesajlarına verilen yanıtlara ve kendisinin anıldığı mesajlara tepki verir (`@aipagerbot fix the tests`, `/jim run the tests`) ve her mesaj, gönderen kişinin rolüyle çalışır. `user` ya da `read_only` rolündeki bir üye oturumları yalnızca kendi sohbetinin kök klasöründe başlatır ve orada tutar (varsayılan `~/aipager-users/<label>`). **Bir üye eklemek, ona senin bilgisayarında kod çalıştırma hakkı vermektir**: güven modelinin tamamı [Ekip modu ve gruplar](groups.md) sayfasında anlatılıyor.

## <a id="what-it-does"></a>Neler yapar

- Her Claude Code oturumunun ne yaptığını Telegram'da gösterir: çalışıyor mu hazır mı, hangi araçları kullanıyor, bağlam %'si, maliyet ve değişen satırlar, görev başına tek bir kartta; ardından Claude'un yanıtı
- Mesajlarını Claude'a **hemen** gönderir, o çalışırken bile: birkaç tane gönder, Claude onları terminalde yazıyormuşsun gibi kendisi sıraya koyar. 👀 gönderildi demek, 👍 Claude onu aldı demek
- Açık bir izin isteği ya da soru varken mesajı bekletir, böylece mesaj asla ona verilmiş bir yanıt gibi okunmaz; yanıt verdiğinde de gönderir
- Claude'un izin isteklerini ve sorularını düğmelerle gösterir (İzin ver, Reddet, Hep izin ver, her seçenek için bir düğme); zaten bitmiş bir görevden kalan bir düğme, o anki işi etkilemek yerine reddeder
- Oturum başına üç izin modu, `/mode` ile değiştirilir: 💬 Sor modu (Claude her işlemden önce sorar), varsayılan olan 🤖 Otomatik mod (Claude Code'un kendi güvenlik denetimi güvenli işlemleri geçirir, riskli olanları karar senin olsun diye durdurur) ve 🔓 Tam erişim modu (her şey hiçbir denetim olmadan çalışır; yöneticiler için ve kendi özel sohbetinde senin için)
- Oturumları sohbetten başlatır: `/new x1` hemen bir tane başlatır (`/new x1 fix the tests` ilk mesajı da gönderir), `/settings` içindeki mod, model ve klasörle ve bunları değiştirmek için düğmelerle
- Fotoğraf, dosya ve sesli mesaj kabul eder (sesli mesajlar, sesli mesaj paketiyle, senin bilgisayarında metne çevrilir)
- Bağlam neredeyse dolduğunda, konuşma sıkıştırıldığında, bir oturum sona erdiğinde ve biri uzun süre sessiz kaldığında haber verir
- Tek bir botla aynı anda birden çok oturumu yönetir; istersen her araç için roller ve kurallarla ekip modu ([Ekip modu ve gruplar](groups.md))
- English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی ve Tiếng Việt konuşur: yalnızca aipager'ın kendi metinleri, asla oturumların ya da Claude'un yanıtları değil ([Diller](commands.md#languages)). Bu README ve belgeler de çevrildi (yukarıdaki **Başka bir dilde oku** satırına bak)

### <a id="mini-app"></a>Mini App

`/app`, Telegram'ın içinde bir panel açar: Durdur, Sonlandır, Yeniden başlat, Yeniden adlandır ve mod değiştirme ile oturumların, `Write`/`Edit` değişiklikleri için diff görünümü, ayarlar ve sahip için “Sorun bildir”. Onu aipager'ın kendisi sunar ve **varsayılan olarak açıktır**; her istek, Telegram'ın `initData` imzasıyla doğrulanır. Birden fazla sohbette bulunuyorsan (kendi özel sohbetin ve bir grup), üstteki sohbet adları satırı aralarında geçiş yapar. `aipager miniapp enable|disable|status` ile yönet ya da yönetilen tünel yerine kendi adresine yönlendir: bkz. [Güvenlik modeli](security.md#mini-app-tunnel).

### <a id="note-on-model-buttons-bedrock--vertex-users"></a>Model düğmeleri hakkında (Bedrock ve Vertex kullanıcıları)

Kalıcı klavyedeki **Model** alt menüsü (ve Mini App'teki model seçici), Claude Code'un aile takma adlarını (`sonnet`, `opus`, `haiku`, `fable` ve `opusplan`) ve `claude-opus-5-5` ile `claude-sonnet-5-5` gibi sabitlenmiş modelleri sunar (tam liste [Kalıcı klavye](commands.md#persistent-keyboard) bölümünde). Anthropic API'sinde bir takma ad her zaman kendi ailesinin en yeni modelini gösterir. **Bedrock** ve **Vertex** üzerinde aynı takma adlar, sağlayıcında ne olduğuna bağlı olarak daha eski sürümleri gösterebilir. Bu hizmetler üzerinden çalışıyorsan ve belirli bir model gerekiyorsa sohbetten `/model <full-id>` gönder.

## <a id="developing-locally"></a>Kendi bilgisayarında geliştirme

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach`, `dtach-bin` bağımlılığıyla gelir, başka bir şey gerekmez. Testleri yukarıdaki gibi bellek sınırıyla çalıştır (macOS'ta `systemd-run` olmadan): yoksa kontrolden çıkan bir test bilgisayarın tüm belleğini tüketebilir.

Sürüm yayınlama süreci [CONTRIBUTING.md](../../../CONTRIBUTING.md) içinde.

## <a id="license"></a>Lisans

MIT; tam metin [LICENSE](../../../LICENSE) içinde.

## <a id="docs"></a>Belgeler

| Sayfa | Konusu |
|---|---|
| [Ajanla kurulum](install-with-an-agent.md) | Bir kodlama ajanının aipager'ı kendi kişisi için kurup yapılandırdığı adımlar |
| [Mimari](architecture.md) | Programın nasıl kurulduğu, parçaların şeması, dosyaların ve soketlerin yerleri |
| [Hook olayları](hooks.md) | aipager'ın Claude Code'dan aldığı her olay, alanlarıyla |
| [Bot komutları](commands.md) | Komutlar, düğmeler, Mini App, sesli mesajlar ve dosyalar, tüm `aipager` komutları ve ortam ayarları |
| [Ekip modu ve gruplar](groups.md) | Birden çok kişi için kurulum, roller, kurallar, kimin ne yaptığı |
| [Sorun giderme](troubleshooting.md) | Sık karşılaşılan arızalar ve `aipager doctor` çözüm tablosu |
| [Sorun raporları](problem-reports.md) | Bir sorun raporunun neler içerdiği, nereye gittiği ve aipager'ın onu ne zaman önerdiği |
| [Güvenlik modeli](security.md) | Güven sınırı, gizli bilgiler, denetim kaydı, ağ bağlantıları |
