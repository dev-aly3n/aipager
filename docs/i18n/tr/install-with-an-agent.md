# Ajanla kurulum

<!-- i18n-source: docs/install-with-an-agent.md @ 6fc084f -->

> Bu sayfa İngilizceden çevrildi; ikisi birbirini tutmazsa İngilizce sürüm geçerlidir. ([English](../../install-with-an-agent.md))

Bu sayfa, kendi kişisi için aipager'ı kurup yapılandırması istenen bir yapay zekâ kodlama ajanı (Claude Code ya da shell kullanabilen herhangi bir ajan) içindir. Adım adım izle. Buradaki her komut soru sormadan çalışır ve asla etkileşimli bir terminal gerektirmez.

aipager, bu kişinin Claude Code oturumlarını Telegram'dan yönetmesini sağlar. İşin bittiğinde kişinin yalnızca kendisiyle konuşan bir Telegram botu olur ve aipager bu bilgisayarda arka planda çalışır.

## <a id="1-what-only-the-person-can-do"></a>1. Yalnızca kişinin kendisinin yapabileceği şeyler

Bunları kişiden yapmasını iste. Onun yerine sen yapamazsın.

1. **Bir bot oluşturmak.** Telegram'da [@BotFather](https://t.me/BotFather) sohbetini açmak, `/newbot` göndermek, bir ad ve kullanıcı adı seçmek. BotFather bir bot token'ıyla yanıt verir (`123456789:AA...` gibi görünür).
2. **Botta Start'a dokunmak.** Yeni botu açmak (BotFather bir `t.me/<bot username>` bağlantısı verir) ve Start'a dokunmak. Bot, bunu yapmamış birine mesaj yazamaz.
3. **Bu bilgisayarda Claude Code'a giriş yapmak.** Kişi burada zaten Claude Code kullanıyorsa bir şey yapmaya gerek yok. Ekranı olmayan bir sunucuda kendi terminalinde bir kez `claude` çalıştırır ve giriş yapar (orada başka bir cihazda açılacak bir bağlantı olur). Uzun ömürlü bir token tercih ederse `claude setup-token` çalıştırır; bu komut yalnızca token'ı yazdırır: kişi onu aipager oturumları için kendi terminalinde kendisi kaydeder (token'ı yapıştırmak, Enter'a, sonra Ctrl-D'ye basmak):

   ```sh
   mkdir -p ~/.config/aipager
   (umask 077 && { printf 'CLAUDE_CODE_OAUTH_TOKEN='; cat; } >> ~/.config/aipager/daemon.env)
   ```

   aipager bu token'ı başlattığı her oturuma verir (bkz. [güvenlik](security.md#the-claude-credential-what-actually-protects-it)). Sen onu hiçbir zaman okumazsın.

## <a id="2-how-the-person-gives-you-the-token"></a>2. Kişi token'ı sana nasıl verir

Bot token'ı kimdeyse bot onun kontrolündedir. Kişiye bu iki yolu sun ve seçmesine izin ver.

- **En güvenlisi: senin hiç okumadığın bir dosya.** Kişi kendi terminalinde şunu çalıştırır:

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token)
  chmod 600 ~/.config/aipager/bot-token
  ```

  ardından token'ı yapıştırır, Enter'a ve Ctrl-D'ye basar. Dosyayı yalnızca bu kişi okuyabilir (izinler 600). Kurulum başarılı olunca dosya silinebilir (aipager kendi yapılandırmasında bir kopyasını tutar). Kişi sana yolu söyler (`~/.config/aipager/bot-token`). Token hiçbir zaman senin konuşmana girmez ve sen yolu `--token-file` ile verirsin.
- **En hızlısı: konuşmaya yapıştırmak.** Bu da olur, ama ne anlama geldiğini açıkça söyle: token konuşma geçmişinde kalır ve modelinin sağlayıcısına gider. Bu geçmişi bir gün başkaları görebilecekse kişinin daha sonra @BotFather'a `/revoke` göndermesi ve `aipager setup --force` ile yeni bir token koyması iyi olur (adım 3.2, `--force` ile). Kişi bu yolu seçerse token'ı ekrana yazdırmadan aynı dosyaya kaydet: en iyisi kendi dosya yazma aracınla, ardından `chmod 600 ~/.config/aipager/bot-token`. Yalnızca shell'in varsa onu yazan komutun argümanı olmasın diye bir here-document ile stdin'den ver:

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token) <<'EOF'
  <the token>
  EOF
  chmod 600 ~/.config/aipager/bot-token
  ```

  Sonra aşağıdaki gibi `--token-file` kullan.

Kişinin Telegram kimliği (sohbetinin kimliği, `123456789` gibi bir sayı) gizli değildir. Kişi bilmiyorsa sen bulabilirsin (adım 3.3).

## <a id="3-your-steps"></a>3. Senin adımların

### <a id="31-install-aipager"></a>3.1 aipager'ı kurmak

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

Hangisini bulursa uv ya da pipx kullanır (macOS'ta Homebrew da) ve hiçbiri yoksa önce uv'yi kurar. `uv tool install aipager` ya da `pipx install aipager` komutunu kendin de çalıştırabilirsin. Bundan sonra `aipager` bulunamıyorsa shell'inin `PATH` değişkenine `~/.local/bin` ekle (`export PATH="$HOME/.local/bin:$PATH"`).

aipager zaten kuruluysa bu komutlar onu güncellemez. `aipager setup --help` komutunun çalıştığını kontrol et; çalışmıyorsa kurulu aipager çok eskidir: `aipager update` çalıştır ve yeniden kontrol et. `aipager update` aipager'ı yeniden başlatmanı söylerse bunu kişiye ilet ve kendi tarafından yeniden başlatma (bkz. 4).

`aipager config` çalıştırma: o, insanlar için etkileşimli bir sihirbaz.

### <a id="32-set-aipager-up"></a>3.2 aipager'ı yapılandırmak

```sh
aipager setup --token-file ~/.config/aipager/bot-token --chat-id 123456789 --service --json
```

Kişinin gerçek kimliğini ve token dosyasının yolunu kullan. `--token-file` yerine token'ı `--token-stdin` ile bir pipe üzerinden verebilirsin (örneğin `--token-stdin < path`); pipe kapanmalı, setup bunu en fazla 30 saniye bekler.

setup token'ı Telegram'da doğrular, bu bilgisayarı kontrol eder, kişiye bir test mesajı gönderir ("aipager is set up for you. ...") ve ancak bundan sonra kendi yapılandırmasını yazar, Claude Code hook'larını `~/.claude/settings.json` dosyasına ekler ve `--service` ile arka plan hizmetini kurup başlatır. Bir kontrol başarısız olursa hiçbir şey yazılmaz.

Önce neyin değişeceğini görmek için `--dry-run` ekle. `--role admin` kişiye owner rolü yerine admin rolü verir (varsayılan olan owner, kendi bilgisayarını kuran biri için doğru olanıdır).

### <a id="33-if-the-person-does-not-know-their-telegram-id"></a>3.3 Kişi Telegram kimliğini bilmiyorsa

```sh
aipager setup detect-chat --token-file ~/.config/aipager/bot-token --json
```

Komut beklerken (5 dakikaya kadar; `--timeout SECONDS` bu süreyi değiştirir) kişiden botta Start'a dokunmasını (ya da bota herhangi bir mesaj göndermesini) iste. Komut en son göndereni `candidate` içinde yazdırır (`id`, `first_name`, `last_name`, `username`) ve hiçbir şey kaydetmez. Kişiye adı ve kullanıcı adını göster ve “Bu sen misin?” diye sor. Yalnızca kişi evet derse adım 3.2'yi `candidate.id` değerine eşit `--chat-id` ile çalıştır. `other_candidates` 0 değilse bota başka kişiler de yazmış demektir: özellikle dikkatli kontrol et.

### <a id="34-read-the-result"></a>3.4 Sonucu okumak

`--json` ile setup, stdout'a tam olarak bir JSON nesnesi yazdırır. `exit_code` (sürecin çıkış koduyla aynı), `status`, `error`, `message`, `fix` ve `next_step` alanlarını oku. stdout JSON değilse (bilinmeyen bir bayrak, `setup` sonrasında yanlış bir kelime ya da `setup` komutu olmayacak kadar eski bir aipager), çıkış kodu 2'dir ve neden stderr'de düz metin olarak yazılıdır: onu oku; eski bir aipager için bkz. 3.1.

| Çıkış | `error` | Ne yaparsın |
|---|---|---|
| 0 | | `installed`, `updated` ya da `unchanged`: tamam, 3.5'e geç. `dry_run`: hiçbir şey yazılmadı; aynı komutu `--dry-run` olmadan çalıştır. detect-chat `found`: kişiden 3.3'teki gibi onaylamasını iste, sonra 3.2'yi çalıştır. |
| 1 | `config_malformed`, `ambiguous_install`, `settings_invalid`, `test_send_failed`, `write_failed`, `service_failed`, `internal_error` | Kişiye `message` ve `fix` değerlerini göster. `ambiguous_install`: kişi bunu `aipager config` ile düzeltir. Dosyaları elle düzenleme. |
| 2 | `usage`, `token_malformed`, `token_stdin_timeout`, `bad_chat_id`, ... | Girdin hatalıydı; `fix` bayrağı söyler. `token_malformed`: dosyada bot token'ı yok, kişiden kontrol etmesini iste. |
| 3 | `deps_missing` | `fix` adımlarını izle (eksik her öğenin `deps.items` içinde kendi `fix` değeri var), sonra setup'ı yeniden çalıştır. |
| 4 | `token_rejected` | Telegram token'ı reddetti. Kişiden token'ı yeniden (ya da @BotFather'dan yenisini) iste. |
| 5 | `chat_not_started`, `bot_blocked` | Kişiden botu açmasını, Start'a dokunmasını (ya da engeli kaldırmasını) iste, sonra setup'ı yeniden çalıştır. |
| 6 | `existing_install` | aipager zaten başka bir token, sohbet ya da rolle yapılandırılmış. Kişiye neyin farklı olduğunu söyle ve `--force` eklemeden önce sor. |
| 7 | `telegram_unreachable` | Ağ sorunu ya da Telegram aşırı yüklü. Bir dakika bekleyip yeniden dene. |
| 8 | `detect_timeout` | detect-chat hiç mesaj görmedi. Kişiden Start'a yeniden dokunmasını iste ve tekrar çalıştır. `updates_backlog` uyarısıyla Start'a dokunmak işe yaramaz: kişiye sayısal kimliğini sor. |
| 9 | `daemon_running` | detect-chat: bu bot için zaten çalışan bir aipager var ve mesajlarını o alıyor. Kişiye sayısal kimliğini sor (ya da `aipager service stop` ile durdurmadan önce izin iste). |
| 10 | `updates_conflict` | detect-chat: bu botun mesajlarını başka bir program okuyor. Kişiye sayısal kimliğini sor. |
| 130 | `interrupted` | Ctrl-C ile durduruldu. |

`daemon.restart_needed` true ise değişikliğin uygulanması için aipager'ın yeniden başlatılması gerekir ya da setup onun çalışıp çalışmadığını anlayamadı (`daemon_unknown` uyarısı). `next_step` değerini izle, hangisi olduğunu o söyler (ön planda kalan `aipager start` komutunu öneriyorsa onun yerine `aipager service install` seç). Kişiye söyle ve yalnızca o isterse yeniden başlat (`aipager service stop`, sonra `aipager service start`), ve asla kişinin aipager üzerinden yönettiği bir oturumun içinden değil (bkz. 4). Hataların, uyarıların ve JSON anahtarlarının tam listesi [setup komutu başvurusunda](commands.md#command-line-aipager-setup-for-coding-agents).

### <a id="35-check-the-install"></a>3.5 Kurulumu kontrol etmek

```sh
aipager doctor --json
```

Bu komut yalnızca okur ve hiçbir şey göndermez. 0 çıkışı hiçbir kontrolün başarısız olmadığı anlamına gelir. `status` değeri `fail` (ya da `warn`) olan her kontrol için kişiye `title`, `detail` ve `fix` değerlerini göster.

### <a id="36-report-back-to-the-person"></a>3.6 Kişiye sonucu anlatmak

Birkaç satırla şunları söyle:

- botu (`@` + `bot_username`) ve test mesajının gönderilip gönderilmediğini (`test_message`);
- neyin yapılandırıldığını (`changed` ve `service.result`);
- bir Claude oturumunun nasıl başlatılacağını: Telegram'da bota `/new` göndermek ya da terminalde `aipager session <name>` çalıştırmak;
- `doctor` komutunun işaret ettiği her şeyi ve bir şey istiyorsa `next_step` değerini.

## <a id="4-what-you-must-not-do"></a>4. Yapmaman gerekenler

- Token'ı yazdırma, `echo` ya da `cat` ile geçirme ve asla komut satırına koyma (`--token` zaten reddedilir). Onu yalnızca bir dosyayla ya da stdin üzerinden ver.
- `~/.config/aipager/` (2. adımdaki token dosyası dışında) ve `~/.claude/settings.json` dosyalarını elle düzenleme. Onları `aipager setup` ve `aipager config` yazar.
- Kişiye sormadan `--force` ekleme: mevcut token'ı, sohbeti ya da rolü değiştirir.
- Kendi Claude Code oturumunu yeniden başlatma ya da sonlandırma. İlk kurulumda içinde çalıştığın oturum aipager altında değildir ve olması da gerekmez: yeni oturumlar Telegram'da `/new` ile ya da `aipager session <name>` ile gelir. aipager zaten yapılandırılmışsa ve kişi seninle onun üzerinden konuşuyorsa (örneğin yeni bir token'la `setup --force` çalıştırmak için), aipager'ı kendi tarafından durdurma ya da yeniden başlatma: görevin ve yanıtı yarıda kesilir. İşin bitince kişiye aipager'ı yeniden başlatmasını söyle.

## <a id="5-groups"></a>5. Gruplar

`aipager setup`, kişinin botla olan özel sohbetini yapılandırır. Başka kişilerle ve onların rolleriyle bir Telegram grubu eklemek için kişi `aipager config` çalıştırır (grup kurulumu henüz betikle yapılamıyor). Bkz. [Ekip modu ve gruplar](groups.md).

## <a id="see-also"></a>Ayrıca bkz.

- [Bot komutları ve setup komutu](commands.md)
- [Sorun giderme](troubleshooting.md)
- [Güvenlik modeli](security.md)
