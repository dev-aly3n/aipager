# Sorun raporları: bir rapor neler içerir

<!-- i18n-source: docs/problem-reports.md @ 6fc084f -->

> Bu sayfa İngilizceden çevrildi; ikisi birbirini tutmazsa İngilizce sürüm geçerlidir. ([English](../../problem-reports.md))

aipager bir hatayla karşılaştığında, onu sürdüren kişiye tek dokunuşla bir sorun raporu gönderebilirsin. Bir şey gönderilmeden önce raporun tamamını görürsün, sen söylemeden bilgisayarından hiçbir şey çıkmaz ve raporda asla sohbetlerin, mesajların, adlar, yollar ya da token'lar olmaz. Rapor yalnızca sabit bir alan listesinden oluşur: sürümler, sayılar, tarihler, evet/hayır yanıtları, sabit listelerden seçimler, aipager'ın kendi kodundaki yerler ve onun `ap1-` ile başlayan hata adları, artı yalnızca sen yazarsan kısa bir not.

## <a id="how-to-send-one"></a>Nasıl gönderilir

Telegram'dan rapor gönderebilen tek kişi aipager kurulumunun sahibidir: kurulumun botla olan kendi özel sohbetindeki kişi (operatör; böyle birden çok sohbet varsa ve tek bir sahip yoksa ne “Sorun bildir” düğmesi ne de otomatik teklif olur, terminaldeki `aipager report` ise çalışmaya devam eder). Mini App'in kendi rapor sayfası vardır; Telegram'daki diğer tüm yollar, botla olan özel sohbetinde aynı önizleme kartını açar:

- **`/help`**: sahip gönderdiğinde yardım metni “🐞 Sorun bildir” düğmesiyle gelir. Grupta yalnızca sahibin dokunuşu işe yarar ve önizleme grupta değil, her zaman sahibin özel sohbetinde açılır.
- **Mini App**: Ayarlar sekmesi, “Sorun bildir” (uygulama özel sohbetinde açıkken) uygulamanın içinde bir sayfa açar. Notun en üstte (isteğe bağlı, sayaçla birlikte 500 karaktere kadar). Altında raporun kısa bir özeti: sürümler, kurulum, hatalar (koddaki yerlerini görmek için birine dokun) ve **Daha fazla ayrıntı**. **Raporun tam metnini göster**, gönderilecek metnin tamamını notunla birlikte gösterir ve sen yazdıkça değişir. **Raporu gönder** (alttaki Telegram düğmesi) tam olarak bunu bir kez gönderir, sonra sayfa **Kopyala** düğmesiyle birlikte bir referans gösterir. Geri hiçbir şey göndermez. aipager notunu temizlerse (kenarlardaki boşluklar, görünmez karakterler, 500 karakterden sonrası), sayfa temizlenmiş notu gösterir ve sen “Raporu gönder” düğmesine yeniden dokunana kadar hiçbir şey gönderilmez.
- **Bellek sınırı bildirimi**: bir aipager hook'u bellek sınırına dayandığında sohbetteki bildirimde “🐞 Bunu bildir” düğmesi olur.
- **Otomatik teklif**: aipager ara sıra fark ettiği bir hata için rapor göndermeyi önerir (bkz. [aipager ne zaman rapor önerir](#when-aipager-offers-a-report)).
- **Terminalde**: `aipager report` raporu yazdırır ve `Send this report? [y/N]` diye sorar. Aynı türden alanları içerir, ama çalışan aipager hakkında daha az bilgiyle (mod, sohbetler, özellikler, çalışma süresi, oturumlar ve Claude Code ayrıntıları bilinmiyor ya da boş kalır). `aipager report --note "what you were doing"` bir not ekler. Terminal yoksa (bir betik, bir pipe, bir Claude Code oturumunun shell'i) raporu yazdırır ve asla göndermez.

Önizleme kartı raporu tam gideceği gibi gösterir: açılabilen katlanmış bir blokta JSON ya da tek bir mesaj için çok uzunsa karta yanıt olarak bir `report.json` dosyası. Düğmeleri:

- **📤 Gönder**: bu raporu tam olarak, bir kez gönderir. Sonra kart `Gönderildi. Referans: ap1-...` yazar.
- **✏️ Not ekle**: bu sohbette 10 dakika içindeki bir sonraki mesajın raporun notu olur (500 karakterde kesilir) ve kart, sen göndermeden önce raporu notla birlikte yeniden gösterir. Bunun yerine başka bir şey yaparsan (bir komut, başka bir düğme, bir fotoğraf, Mini App'te bir işlem), not sorusu notsuz kapanır ve 10 dakika sonra sonraki mesaj her zamanki gibi Claude'a gider. Sorunun, notsuz önizlemeye dönen bir **‹ Geri** düğmesi ve bir İptal düğmesi vardır.
- **✖️ İptal**: hiçbir şey gönderilmez.

Önizleme 24 saat açık kalır, son 5 tanesi açık kalır (6. en eskisini kapatır). aipager yeniden başladıktan sonra eski bir kart gönderilemez: yenisini almak için “Sorun bildir”i yeniden aç. Mini App'te açılan bir rapor 24 saat içinde gönderilebilir (son 3 tanesi); yeniden başlatmadan sonra sayfa yenisini yükler.

## <a id="what-a-report-holds"></a>Bir rapor neler içerir

Her bölüm, sade sözcüklerle. Her değer, önizleme gösterilmeden önce ve gönderimden önce bir kez daha izin verilen değerler listesiyle karşılaştırılır; uymayan her şey `<invalid>` ile değiştirilir ya da çıkarılır, ama asla olduğu gibi gönderilmez.

**Rapor hakkında**
- `schema`: raporun biçimi (`aipager-report/1`).
- `trigger`: `manual` (rapor elle açıldı) ya da `auto` (otomatik teklif).
- `day`: UTC cinsinden tarih (saat olmadan).
- `note`: yalnızca sen eklersen: önizlemenin gösterdiği haliyle senin sözcüklerin (en fazla 500 karakter, görünmez karakterler olmadan).

**aipager ve bilgisayar**
- `aipager`: sürüm; nasıl kurulduğu (`pipx`, `uv`, `brew`, `pip`, `editable`, `nix`, `snap`, `docker`, `system`, `foreign` ya da `unknown`); nereden (paket dizini, yerel klasör, git kopyası); `aipager update` komutunun onu güncelleyip güncelleyemeyeceği.
- `python`: Python sürümü ve CPython mı PyPy mi olduğu.
- `os`: sistem (`linux`, `darwin`, `windows`, `freebsd`), işlemci türü, sabit bir listeden Linux dağıtımı ve sürüm numarası, çekirdek sürümü major.minor, varsa konteyner türü (`docker`, `lxc`, `wsl`, `podman`) ve aipager'ın bir systemd ya da launchd hizmeti olarak kurulup kurulmadığı.
- `deps`: aipager'ın bağlı olduğu kütüphanelerin sürümleri (python-telegram-bot, httpx, aiohttp, PyYAML, rich, questionary, dtach-bin).
- `claude_code`: Claude Code sürümü, kaç Claude Code kurulumu bulunduğu ve nasıl giriş yaptığı (`env`, `file`, `keychain` ya da neden belirlenemediği). Giriş bilgilerinin kendisi asla değil.

**Rakamlarla kurulumun**
- `config`: mod (`personal`, `team`, `scope`); kaç özel sohbet ve kaç grup yapılandırıldığı; kaç özel rol olduğu (adları dahil edilmez); hangi özelliklerin açık olduğu (Mini App, tünelinin türü, sesli mesajlar, değişiklik önizlemeleri, zengin özetler).
- `runtime`: aipager'ın ne zamandır çalıştığı, aralık olarak (`<10m`, `10m-1h`, `1-24h`, `1-7d`, `>7d`); kaç oturumun başlatılmış olduğu ve çalıştığı; aipager'ın son 7 günde kaç kez beklenmedik şekilde durduğu ve son seferde nasıl bittiği (`clean`, `crash`, `reboot`).
- `doctor`: kontroller yeniden çalıştırılmadan elde edilebildiğinde sabit bir sağlık kontrolü listesinin sonuçları (`ok`, `warn`, `fail`) (bottan ve terminalden gelen raporlar bu alanı boş bırakır).
- `flood`: tüm sohbetler genelinde özetlenmiş Telegram sınır durumu: son 7 gündeki engellemeler, şu an gönderimi kapalı, minimal modda ya da yavaşlatılmış bir sohbet olup olmadığı ve en yoğun sohbetin saatlik bütçe payı, aralık olarak. Sohbet kimliği yok.

**Ne ters gitti**
- `counters_24h`: sabit bir listedeki her olayın son 24 saatte kaç kez olduğu (örneğin çalışma kartı bekçisi yeniden başladı, belirli türde bir Telegram hatası, bir hook bellek sınırına dayandı, bir ağ ya da disk hatası). Yalnızca sayılar.
- `log_digest_24h`: son 24 saatte günlüğe en çok uyarı ya da hata yazan aipager kodundaki 15 yer için yer (`aipager/state.py:1234`), düzey ve kaç kez. Günlük metni asla değil.
- `errors`: aipager'ın kaydettiği en fazla 10 hata, her biri için:
  - `fingerprint`: `ap1-` ile başlayan hata adı (hata türünün ve aipager kodundaki en fazla 8 yerin adlarının hash'i, asla metin ya da veri değil);
  - nerede olduğu (aipager'ın kendisi, hook, durum satırı, bir komut, Mini App), nasıl yakalandığı ve bir hata mı yoksa yalnızca alışılmadık bir şey mi olduğu (`tier`);
  - aipager'ın günlüğe yazdığı bir hata için günlükçünün adı (`aipager` modülü, `asyncio` ya da `telegram.ext`'in bir parçası);
  - istisna türünün adı (örneğin `builtins.KeyError` ya da sabit paket listesi dışındaki bir istisna için `<other>`), en fazla üç nedenin türleri ve `ENOSPC` gibi bir hata numarası adı;
  - bir hook hatası için hook olayı ve araç (yerleşik bir araç adıyla, herhangi bir MCP aracı `mcp` olarak);
  - kaç kez, hangi günlerde ve hangi aipager sürümlerinde olduğu;
  - aipager'ın kendi kodunda içinden geçtiği en fazla 12 yer (dosya, satır, işlev adı) ve sabit bir listeden içinden geçtiği en fazla 8 dış paket adı.

**Asla dahil edilmeyenler**: sohbetlerin, mesajların, Claude'un yanıtları, oturum adları, klasör ya da dosya yolları, token'lar ya da anahtarlar, sohbet ya da kullanıcı kimlikleri, kullanıcı adları, bilgisayarın adı, herhangi bir hata mesajının metni ve günlük satırları. Yukarıdaki alanlarda bunlara yer yoktur ve rapor, gösterilmeden ve gönderilmeden önce bu listeyle karşılaştırılır.

## <a id="where-it-goes"></a>Nereye gider

Rapor adın olmadan, aipager'ı sürdüren kişinin [Sentry](https://sentry.io) üzerindeki özel hata kutusuna gider: hesap yok, kurulum kimliği yok. Her web isteği gibi bilgisayarının internet adresinden gelir; bu adresi GitHub ve Sentry görür; kutu onu saklamayacak şekilde ayarlanmıştır (Sentry'nin “Prevent Storing of IP Addresses” ayarı). aipager onu tek bir HTTPS isteğiyle kendisi gönderir (çok daha fazlasını toplayan Sentry'nin kendi kütüphanesini kullanmaz): önizlemedeki rapor bir `report.json` eki olarak gider, yanında gruplama için kısa bir özetle (sürüm, aipager'ın nasıl kurulduğu ve bir geliştirme kurulumu olup olmadığı, sistem, Python ve Claude Code sürümleri, tetikleyici ve parmak izi, ilk hatanın türü ve koddaki yerleri). İstek yalnızca `aipager/<version>` adını verir.

Gönderdikten sonra kart (ya da terminal) bir **referans** gösterir: ilk hatanın `ap1-` parmak izi ya da hatasız bir rapor için 32 karakterlik olay kimliği. Bir de [GitHub'da issue](https://github.com/dev-aly3n/aipager/issues) açarsan, aipager'ı sürdüren kişi raporunu bulabilsin diye referansı yaz. Issue'ya günlükleri ve `aipager doctor` çıktısını yapıştırma: bunlarda sohbet kimliğin, ev klasörünün yolları ve mesajlarının başı var.

Göndermeden hemen önce aipager, nereye göndereceğini öğrenmek için GitHub'daki aipager reposundan [report-endpoint.json](https://github.com/dev-aly3n/aipager/blob/main/report-endpoint.json) dosyasını okur (çalışan bir aipager iyi bir kopyayı bir gün tutar; başarısız bir okuma sonraki gönderimde yeniden denenir, her `aipager report` ise dosyayı baştan okur). Böylece aipager'ı sürdüren kişi, yeni bir sürüm yayımlamadan Sentry anahtarını değiştirebilir, raporları herkes için kapatabilir ya da belirli bir numaranın altındaki sürümlerin göndermesini engelleyebilir (kendi sürümünü bilmeyen bir derleme daha düşük sayılır). GitHub'a ulaşılamazsa ya da dosya aipager'ın anladığı türden değilse aipager bu sürüme gömülü anahtarı kullanır; okunabilen ve “kapalı” diyen bir dosyaya her zaman uyulur.

## <a id="limits"></a>Sınırlar

- Bir bilgisayardan günde (UTC) en fazla 5 gönderim denemesi, elle ve otomatik birlikte; başarısız bir deneme de sayılır. Sonraki şunu söyler: `Bir bilgisayardan günde en fazla 5 rapor gönderilebilir. Yarın tekrar dene.`
- Arka planda hiçbir şey yeniden denenmez: gönderim başarısız olursa kart `Şu an gönderilemedi. Hiçbir şey kaybolmadı; daha sonra tekrar dene.` yazar ve Gönder düğmesini bırakır.

## <a id="when-aipager-offers-a-report"></a>aipager ne zaman rapor önerir

aipager kendi hatalarını fark eder ve ara sıra özel sohbetinde onlar için rapor göndermeyi önerir: `aipager N kez iç hatayla karşılaştı (TypeError, yer: çalışma kartı). Bir rapor bunun daha çabuk düzeltilmesine yardımcı olur. ...` ile birlikte **Raporu önizle**, **Şimdi değil** ve **Bunu bir daha sorma**. “Raporu önizle”, yalnızca bu hataları içeren önizleme kartını açar; orada Gönder'e dokunana kadar hiçbir şey gönderilmez.

Teklif yalnızca şunların hepsi sağlanırsa gelir:
- Bu aipager'da gerçek bir hatadır: aipager'ın kendi kodunda, aralarında en az 10 dakika olacak şekilde en az iki kez olmuş bir hata ya da aipager bir hizmet olarak çalışırken çöktüyse hemen (ön planda çalışan ve örneğin terminali kapatılarak öldürülen bir `aipager start` yalnızca sayılır). Bir hook'un, durum satırı programının ya da bir komutun aipager'a bildirdiği hatalar (belleği yetmeyen bir hook dahil) ayrıca en az bir güne yayılmalıdır, bu yüzden böyle tek bir hata asla teklif doğurmaz; onlar için bildirimdeki “Bunu bildir” düğmesi var. aipager'ın suçu olmayan şeyler (Telegram sınırları, ağ, dolu disk) asla teklif doğurmaz; yalnızca elle göndereceğin bir rapor için sayılır.
- 3 günde en fazla bir teklif, birikmiş her şey bu tek teklifte.
- aipager'ın bu bilgisayardaki en ilk başlatılmasından sonraki ilk 48 saatte değil (güncellemeler bu süreyi sıfırlamaz) ve aipager'ın yeniden başlatılmasından sonraki ilk saatte değil.
- Yalnızca 2 dakikadır hiçbir şey çalışmıyorken ve gönderilmeyi bekleyen bir şey yokken, botla olan özel sohbetin de son 30 dakikada kullanılmışken. Asla bir grupta ve Telegram o sohbeti sınırlarken asla.
- Her hata, aipager sürümü başına bir kez önerilir. Herhangi bir yanıttan sonra o sürümde bir daha önerilmez; bir güncellemeden sonra ancak yeniden olursa geri gelebilir.
- **Şimdi değil** ve **Bunu bir daha sorma** ikisi de “hayır” sayılır; bir gün içinde yanıtlanmayan bir teklif de öyle. **Raporu önizle** sayacı sıfırlar, sonra iptal etsen bile. Arka arkaya iki “hayır” otomatik teklifleri kapatır: ikincisi bir dokunuşsa bunu bir satır söyler; her durumda `/settings` içinde görünür.
- Bir geliştirme kurulumu için asla (aipager yerel bir klasörden, bir git kopyasından ya da editable modda kurulmuşsa).

Otomatik teklifleri kapatmak için özel sohbetinde `/settings` aç: **Sorun raporları: Kapalı**. İki “hayır” onları kapattıysa aynı sayfada **Teklifleri yeniden aç** düğmesi olur. Var olan ama okunamayan bir `reports.json` da otomatik teklifleri kapatır (aipager artık önceki yanıtları bilemez); **Teklifleri yeniden aç** bunu da düzeltir. Sorulabilecek bir sohbeti olmayan bir bilgisayarda aipager'ın ortamında `AIPAGER_REPORT_PROMPTS=0` ayarla. “Sorun bildir” ve `aipager report` bu ayarlar ne derse desin her zaman çalışır.

## <a id="what-is-kept-on-your-machine"></a>Bilgisayarında ne saklanır

- `~/.local/state/aipager/reports.json`: aipager'ın kaydettiği hatalar (yukarıda gösterilen biçimde: tür adları, sürümler, koddaki yerler, en fazla 50), 8 günlük saatlik sayılar, 2 günlük günlük özeti, son durmaların nasıl bittiği ve tekliflerin durumu (son teklifin ne zaman olduğu, arka arkaya “hayır”lar, hangi hataların hangi sürümde önerildiği). Onu yalnızca sen okuyabilirsin.
- `~/.local/state/aipager/report-sends.json`: bugün kaç rapor gönderildiği ya da gönderilmeye çalışıldığı.
- `~/.local/share/aipager/install.json`: aipager'ın bu bilgisayarda ilk ne zaman başladığı (yukarıdaki 48 saat için).
- `~/.local/share/aipager/running.json`: aipager çalışırken yazılır (süreç numarası, başlangıç zamanı, hizmet olarak çalışıp çalışmadığı ve sistemin bu açılışının kimliği), çökmeyi normal durmadan ayırmak için.

Hiçbirinde sohbet, mesaj, ad, yol ya da token yoktur. `aipager uninstall` bunları aipager'ın diğer verileriyle birlikte saklar, böylece yeniden kurulum kaldığı yerden devam eder; neyi sakladığını listeler ve bunları silen komutu yazdırır (bkz. [`aipager uninstall`](commands.md#command-line-aipager-uninstall)). Yalnızca bunları silmek için bu dört dosyayı sil: `~/.local/state/aipager` klasörünün tamamını silmek ilk ikisini (ve onlarla birlikte dillerin durumunu, `languages.json`) kaldırır, ama `install.json` ile `running.json` adlarıyla silinmelidir (aipager'ın oturum klasörlerini de barındıran `~/.local/share/aipager` klasörünü silme).

## <a id="see-also"></a>Ayrıca bkz.

- [Sorun giderme](troubleshooting.md): sık karşılaşılan arızalar ve çözümleri.
- [Güvenlik modeli](security.md): aipager neye dokunur ve ağa ne gider.
- [Bot komutları](commands.md): `/help`, `/settings` ve komut satırı.
