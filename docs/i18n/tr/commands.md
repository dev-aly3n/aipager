# Komutlar ve bot arayüzü

<!-- i18n-source: docs/commands.md @ d99edfa -->

> Bu sayfa İngilizceden çevrildi; ikisi birbirini tutmazsa İngilizce sürüm geçerlidir. ([English](../../commands.md))

aipager'ı Telegram'dan nasıl yönetirsin. Dört girdi yolu: slash komutları, klavye düğmeleri, Mini App paneli ve serbest mesajlar (metin, dosyalar, sesli mesajlar).

Bot yalnızca yapılandırılmış sohbetlerden girdi kabul eder; güven sınırı için bkz. [Güvenlik modeli](security.md).

## <a id="slash-commands"></a>Slash komutları

`aipager/bot/lifecycle.py` içinde kaydedilirler. Telegram'ın komut menüsü onları gösterir ve aipager başladığında ve oturumlar her değiştiğinde güncellenir.

| Komut | Argümanlar | Ne yapar |
|---|---|---|
| `/start` | - | Ana ekran: bu sohbetin oturumları, sıradan bir mesajın nereye gittiği (`✍️ Mesajlar x1 oturumuna gider.`) ve `🆕 Yeni oturum`, `⚙️ Ayarlar` ve bu sohbette geri getirilebilecek sona ermiş bir oturum varsa `↩️ Devam ettir`. Kalıcı klavyeyi de gösterir. |
| `/help` | - | Görevlere göre kısa bir kılavuz (başla, konuş, yönet, oturumlar, ayarlar ve yöneticiler için `/update`), bir komuta dokunmanın onu hemen gönderdiği ipucuyla: önce metin eklemek için üzerine uzun bas (telefon) ya da Tab'a bas (bilgisayar). Kurulumun sahibi (kurulumun botla olan kendi özel sohbetindeki kişi) için ayrıca bir `🐞 Sorun bildir` düğmesi vardır: o özel sohbette, Gönder, Not ekle ve İptal düğmeleriyle tam bir sorun raporunun önizlemesini açar. Gönder'e dokunana kadar hiçbir şey gönderilmez ([bir rapor neler içerir](problem-reports.md)). |
| `/app` | - | Mini App panelini açar (oturumlar, diff görünümü, ayarlar). |
| `/status` | - | Bu sohbetin oturumları, üzerinde işlem yapılabilen bir liste olarak. Her biri durumunu sözcüklerle söyler (`⚙️ çalışıyor 3:12 · Bash: run tests`, `⏳ seni bekliyor · Bash: make deploy`, `💤 hazır`); sıradan mesajların gittiği oturumda `✍️` vardır ve ikinci satırda model, bağlam, maliyet, kuyrukta bekleyenler (`kuyruk 3 (1 kuyrukta, 2 not)`) ile çalışan ajanlar ve arka plan komutları yer alır. Her birinin bir düğme sırası vardır: `✍️ x1` sonraki mesajlarını oraya yönlendirir, çalışırken `⏹ Durdur`, seni beklerken `Yanıtla` ve geri kalanı için `⋮`. Altında: sona eren oturumlar için `🆕 Yeni` ve `⚫ Bitenler (n)` (devam ettir, sil ya da hepsini listeden kaldır; `/resume` içinde kalırlar). |
| `/stop [label]` | isteğe bağlı | Oturumun mevcut görevini yarıda keser. Ad olmadan bu sohbetin çalışan tek oturumunu durdurur (mesajların başka bir oturuma gitse bile; arka plan ajanı ya da arka plan komutu hâlâ çalışan bir oturum çalışıyor sayılır), birden fazlaysa hangisi olduğunu bir listeyle sorar ve hiçbiri yoksa `Şu an çalışan bir şey yok.` yazar. `/stop x1` (ya da `/x1 stop`) x1'i durdurur (ya da çalışmıyorken `x1 şu an bir şey yapmıyor.` yanıtını verir). Ayrıca kuyruktaki mesajları atar ve kaç tanesinin atıldığını söyler. |
| `/now` | - | Claude'un etkin oturumun kuyruğunda tuttuğu mesajları mevcut adımdan sonra değil, hemen gönderir (Claude Code'un kendi “şimdi gönder” tuşları, Ctrl+X Ctrl+S). Sohbete gönderim kapalıyken ya da sohbet minimal moddayken de dahil her zaman çalışır; Claude hiçbir şey tutmuyorsa `Kuyrukta bekleyen bir şey yok` yanıtını verir. Bkz. [Kuyruktaki bir mesajı şimdi göndermek](#send-a-queued-message-now). |
| `/new [name] [first message]` | hepsi isteğe bağlı | Bir oturum başlatır. `/new x1` onu hemen başlatır, `/new x1 fix the tests` ise ayrıca ilk mesaj olarak “fix the tests” gönderir. Tek başına gönderildiğinde (örneğin menüden dokunulduğunda) `/new` bir ad sorar ve gönderilen bir sonraki mesaj tam olarak argüman gibi kullanılır, bu yüzden `/new` ve ardından `x1 fix the tests` aynı sonucu verir. Grupta kartı bir adla yanıtla (ya da botu an: `@aipagerbot x1`), çünkü orada sıradan bir mesaj bota ulaşmaz. Yalnızca o bir sonraki mesaj: önce başka bir şey yaparsan (bir fotoğraf, başka bir komut, başka bir mesaja yanıt, bir klavye şablonu, başka bir düğme) o her zamanki gibi işlenir ve kart kapanır; `/status`, `/settings` ve klavye menüleri onu açık bırakır. Soran kart ayrıca oturumun alacağı modu, modeli ve klasörü, önce onları değiştirmek için düğmelerle gösterir. Tüm yollar aynı “x1 hazır” kartıyla biter: oturumun ne aldığı, bir sonraki mesajının ona gideceği ve başka bir moda geçmek ya da modeli değiştirmek için tek dokunuşluk düğmeler. Yeni oturumlar herkes için Otomatik modda başlar; /new komutunun ne kullandığı `/settings` → Yeni oturumlar içinden değiştirilir. Addan önce `!`, oturumu Tam erişim modunda başlatır; bir yönetici (`owner` ve `admin` rolleri) için ya da botla olan kendi özel sohbetinde, rolü mesaj gönderip Claude'u yanıtlayabilen (`user`, `read_only` değil) bir kişi için; diğer herkes için Otomatik modda başlar ve kart nedenini söyler. Kullanılmakta olan bir ad, yalnızca `/new` gönderen kişiye `↩️ Ona geç` (o oturum çalışıyorsa) ya da `♻️ Devam ettir` (sona erdiyse), `🆕 Yenisiyle değiştir` ve `↩️ İptal` sunar. Ad isteyen kart 10 dakika kullanılmazsa süresi dolar (`⏱ Bu yeni oturum kartının süresi doldu. /new komutunu tekrar gönder.`). Rolünde `can_manage` olmayan bir üye (`user`, `read_only`) oturumları yalnızca sohbetin [kök klasörünün](groups.md#root-folders) içinde başlatır: kartın klasörü kök klasördür, “Klasör” sayfası onu ve hemen içindeki klasörleri gösterir (“Varsayılan klasör” satırı olmadan), “Yeni klasör” seçilenin içinde bir klasör oluşturur ve kök klasörün dışında çalışan bir oturum için “Ona geç”, “Devam ettir” ve “Yenisiyle değiştir” reddedilir (“Bu oturum klasörünün (~/...) dışında çalışıyor, bu yüzden rolün ona devam edemez, onu yeniden başlatamaz ya da modunu değiştiremez. Bir yöneticiye sor.”). |
| `/resume [label]` | isteğe bağlı | Zaten sona ermiş bir oturuma adıyla devam ettirir ya da bir liste açar. Oturum bulunduğu modda geri gelir. Tam erişim modundaki bir oturum yalnızca bir yönetici için ya da kendi özel sohbetindeki kişi için Tam erişimde geri gelir; diğer herkes için Otomatik modda geri gelir ve yanıt bunu söyler (`♻️ x1 yeniden çalışıyor, Otomatik modda: Tam erişim modu yalnızca yöneticiler için.`). Daha eski bir aipager'ın kendi eski Auto modunda (denetimsiz çalışan) kaydettiği bir oturum Tam erişim modunda, Ask modunda kaydedilmiş biri ise Sor modunda geri gelir. Sona eren bir oturum listede `GONE_SESSION_MAX_AGE_DAYS` gün (varsayılan 14) kalır, sonra oturum listesinden çıkar; Claude'un kaydedilmiş konuşmasına dokunulmaz. Rolünde `can_manage` olmayan bir üye, klasörü sohbetin [kök klasörünün](groups.md#root-folders) dışında olan bir oturuma devam ettiremez (sahipler ve yöneticiler ettirebilir); Yeniden başlat ve Sor ile Otomatik arasındaki yeniden başlatmasız geçiş dahil her mod değişikliği için de aynısı geçerlidir. |
| `/kill [label]` | isteğe bağlı | Bir oturumu, her zaman onaydan sonra sonlandırır: `[⏹ Sonlandır] [İptal]` ile `⏹ x1 sonlandırılsın mı? Claude durur ve oturum sona erer.` Ad olmadan, bu sohbette çalışan tek bir oturum varsa onaya, yoksa bu sohbetin çalışan oturumlarının listesine gider. `/status` → `⋮` → `⏹ Oturumu sonlandır` içinde de vardır. |
| `/restart [label]` | isteğe bağlı | Bir oturumu onaydan sonra yeniden başlatır: Claude durur ve konuşmayı koruyarak, aipager'ın onu en son başlattığı ya da geçirdiği modda yeniden başlar. Terminalde `aipager session` ile başlatılan bir oturum, modu o zamandan beri Telegram'dan değiştirilmediyse Sor modunda geri gelir (aipager terminalin onu hangi modda başlattığını bilmez ve daha serbest olanı asla tahmin etmez); Telegram'dan devam ettirildiğinde de aynısı geçerlidir. |
| `/rename [label] [new]` | isteğe bağlı | Bir oturuma yeni bir ad verir (sona ermiş birine de). `/rename x1 newname` hemen yeniden adlandırır. Daha az sözcükle sorar: komut yalnızca tek bir oturumu kastedebiliyorsa doğrudan yeni adı, aksi halde bir liste (`/rename` sonrasındaki tek bir sözcük bir oturum seçmez, çünkü hem eski ad hem yeni ad olabilir). Grupta soruyu adla yanıtla. |
| `/delete [label]` | isteğe bağlı | Sona ermiş bir oturumu onaydan sonra listeden kaldırır. |
| `/diff [label]` | isteğe bağlı | Oturumun çalışma klasörünün git diff'ini gösterir. Ad olmadan mesajlarının gittiği oturumu gösterir. |
| `/clearqueue` | - | Etkin oturumun henüz almadığı tüm mesajları (hem aipager'ın beklettikleri hem de Claude'un içindeki kuyrukta zaten olanlar), süren görevi yarıda kesmeden atar. Kaç tanesinin atıldığını yanıtlar. |
| `/mode [label] [ask\|auto\|full]` | isteğe bağlı | Oturumun izin modunu, diğer her moda geçmek için bir düğmeyle gösterir: `✍️ x1: 🤖 Otomatik mod.` `[💬 Sor moduna geç]` `[🔓 Tam erişim moduna geç]`. Üç mod vardır. **💬 Sor modu**: Claude her işlemden önce sorar. **🤖 Otomatik mod** (varsayılan): Claude Code'un kendi güvenlik denetimi güvenli işlemleri çalıştırır, riskli olanları karar senin olsun diye durdurur. **🔓 Tam erişim modu**: her şey hiçbir denetim olmadan çalışır (`--dangerously-skip-permissions`); onu yalnızca bir yönetici ya da botla olan kendi özel sohbetinde, rolü mesaj gönderip Claude'u yanıtlayabilen (`user`, `read_only` değil) bir kişi seçebilir. Otomatik ve Tam erişim yalnızca Claude Code'un kendi izin isteklerini değiştirir: aipager'ın hook'u üyenin rol kurallarını uygulamaya devam eder. `/mode ask`, `/mode auto`, `/mode full` ya da `/mode x1 ask` hemen geçirir; sohbetin dilindeki mod sözcüğü de “mod” sözcüğü olmadan çalışır (💬 Sor modu için `/mode x1 Sor`, 🔓 Tam erişim modu için `/mode x1 Tam`). Bu sohbette çalışan bir oturumun adı tam olarak bu olduğunda (örneğin `demander` adlı bir oturum), sözcük oturumu adlandırır: `/mode demander` o oturumun modunu gösterir, `/mode x1 demander` ise x1'i geçirmeye devam eder. `ask`, `auto` ve `full` her sırada (`/mode x1 ask` ya da `/mode ask x1`) her zaman modu ifade eder; yalnızca iki sözcük de mod sözcüğüyse ve yalnızca biri çalışan bir oturumun adıysa o oturumu adlandırır, bu yüzden `full` adlı bir oturumla hem `/mode full ask` hem `/mode ask full` onu Sor moduna geçirir, tek başına `/mode full` ise mod olarak kalır. Sor ile Otomatik arasında geçiş yeniden başlatmadan ve her an (Claude çalışırken bile) yapılır: aipager oturumda senin yapacağın gibi Shift+Tab'a basar ve kart, Claude Code bir sonraki adımda yeni modu bildirene kadar `⚙️ x1 yeniden başlatılmadan geçiyor: 🤖 Otomatik mod.` yazar, sonra sonucu gösterir. Otomatik mod bir oturum için kullanılamıyorsa (modeli ya da planı yüzünden) kart bunu söyler ve oturum Sor modunda kalır. Oturum bir soru ya da izin isteği gösterirken değil: önce onu yanıtla. Tam erişime geçmek bir yönetici (ya da kendi özel sohbetindeki kişi) ve onay gerektirir ve oturumu yeniden başlatır (konuşma korunur); Tam erişimden çıkmak da onu hemen yeniden başlatır. Tam erişimde başlatılmış bir oturum, terminalde Sor ya da Otomatik moda geçirildikten sonra bile her değişiklikte yeniden başlar; başladığından beri Tam erişim bildiren bir oturum da (`--dangerously-skip-permissions` ile başlatılmış bir terminal oturumu) öyle: Shift+Tab orada onu Tam erişime geri döndürebilirdi. Çalışan bir oturum için yeniden başlatma `Durdur ve geç` / `Şimdi değil` sunar. Rolünde `can_manage` olmayan bir üye, klasörü sohbetin [kök klasörünün](groups.md#root-folders) dışında olan bir oturumun modunu ne yeniden başlatmadan ne de yeniden başlatarak değiştirebilir. Bir değişikliğe dokunmak ikinci bir mesaj göndermez, kartın kendisini değiştirir. `/perms` eski addır ve aynı şekilde çalışır. |
| `/settings` | - | Bu sohbetin oturumlarının nasıl göründüğü ve yanıt verdiği: aipager dili, yanıt görünümü, değişiklik önizlemeleri, uzun görevlerde kart güncellemeleri, biçimlendirme, yanıt uzunluğu, kelime düzeyi; `/new` komutunun bir oturumu neyle başlattığı; her oturumun kendi ayarları; ve sahibin özel sohbetinde sorun raporu teklifleri. Bkz. [Ayarlar](#settings). Grupta herkes bakabilir, ama bir ayarı yalnızca bir yönetici değiştirebilir. |
| `/language` | - | aipager'ın bu sohbette kendi mesajları ve düğmeleri için kullandığı dil (`/settings` içindeki ilk satırla aynı liste). Her dil kendi adıyla gösterilir: English, sonra Español, Русский (Rusça), Português (Brasil), 简体中文 (Basitleştirilmiş Çince), Français, Deutsch, 한국어 (Korece), العربية (Arapça), Bahasa Indonesia, Українська (Ukraynaca), Italiano, Türkçe, فارسی (Farsça) ve Tiếng Việt (Vietnamca). Bu sürümden sonra başlayan bir özel sohbet Telegram uygulamanın dilini izler; daha önce var olan bir sohbet, sen seçene kadar İngilizce kalır (bkz. [Diller](#languages)). İspanyolca, Portekizce, Fransızca, İtalyanca ve Endonezce'de alt klavyenin sözcükleri, komutları gibi `status`, `stop`, `new` ve `kill` olarak kalır. Almanca ve Türkçe `Status`, `Stop`, `Neu…` ve `Beenden…` ile `Özet`, `Stop`, `Oluştur` ve `Sonlandır` kullanır; İngilizce sözcükler yazıldığında çalışmaya devam eder. Farsça ve Arapçada aipager'ın metinleri sağdan sola, Farsça ya da Arapça rakamlarla okunur; oturum adları, komutlar, model adları ve kod olduğu gibi kalır. Liste neyi değiştirdiğini söyler: yalnızca aipager'ın metinlerini, burada ve Mini App'te; Claude oturumların değişmez ve Claude ona hangi dilde yazarsan o dilde yanıt verir. Grupta herkes bakabilir, ama yalnızca bir yönetici değiştirebilir. `language` ayrılmış bir oturum adıdır. |
| `/whoami` | - | Burada kim olduğunu gösterir: ekip modunda adını, bu sohbetteki rolünü ve onun sana verdiği kuralları. |
| `/update` | - | Yalnızca yöneticiler için (`owner` ve `admin` rolleri ya da `can_manage` olan bir rol). aipager'ın ve Claude Code'un yeni sürümleri olup olmadığını kontrol eder, sonra tek bir düğmeyle yeni sürümü olanları günceller. Bkz. [Güncelleme](#update). |

`/` menüsü önce oturumlarını (`/x1 · x1 ile konuş`), sonra sık kullanılan komutları gösterir: `new`, `status`, `stop`, `now`, `resume`, `mode`, `settings`, `language`, `help` (ve Mini App açıksa, yalnızca özel sohbette `app`). `kill`, `restart`, `rename`, `delete`, `diff`, `clearqueue`, `perms`, `whoami` ve `update` menüde yoktur ama yazılınca çalışır; oturum işlemleri `/status` → `⋮` içinde de vardır.

**Bir oturuma etki eden komutlar** (`/stop`, `/mode`, `/kill`, `/restart`, `/rename`, `/delete`, `/diff`) tek bir kurala uyar:

- Adla (`/restart x1`) bu sohbetin o oturumuna etki ederler (`/rename` bunun için eski ve yeni adı ister, yukarıya bak).
- Ad olmadan, komutun kastedebileceği tek oturuma (`/stop` için çalışan tek oturum, `/kill` ve `/restart` için başlatılmış tek oturum, `/delete` için sona ermiş tek oturum), varsa her zamanki onayla etki ederler.
- Aksi halde bu sohbetin yalnızca komutun anlamlı olduğu oturumlarının bir listesini gösterirler: önce mesajlarının gittiği oturum `✍️` işaretiyle, sonra diğerleri adlarına göre ve `✖️ İptal`. Birini seçmek, adını yazmakla aynı karta götürür.
- Grupta ad olmadan yalnızca kendi güncel oturumuna, komut onun için anlamlıysa etki ederler; aksi halde tek bir oturumla bile her zaman bir liste gösterirler, çünkü o oturum başka bir üyeye ait olabilir (bkz. [ekip modu](groups.md#talking-to-a-session-in-a-group)).

`/mode` ve `/diff` önce mesajlarının gittiği oturuma bakar.

### <a id="settings"></a>Ayarlar

`/settings` (ve aynı seçeneklerin bulunduğu Mini App'teki “Ayarlar” sekmesi) bu sohbetin oturumlarının nasıl göründüğünü ve yanıt verdiğini belirler. Her sohbetin kendi ayarları vardır; grupta herkes bakabilir, ama bir ayarı yalnızca bir yönetici değiştirebilir. Kendi özel sohbetinde, rolün ne olursa olsun, onları Telegram'da ve Mini App'te sen değiştirirsin. ✅ işaretli satır varsayılanda olmayan satırdır (“Yanıt nasıl görünsün” her zaman kendi seçimini gösterir).

| Ayar | Seçenekler (varsayılan ilki) | Ne yapar |
|---|---|---|
| 🌐 Dil | Otomatik (Telegram'daki dilin), özel sohbette; English | aipager'ın kendi mesajlarının ve düğmelerinin bu sohbetteki ve Mini App'teki dili, kendi adıyla gösterilir (`/language` ile aynı liste). Claude oturumlarını değiştirmez: Claude ona hangi dilde yazarsan o dilde yanıt verir. English, Español, Русский (Rusça), Português (Brasil), 简体中文 (Basitleştirilmiş Çince), Français, Deutsch, 한국어 (Korece), العربية (Arapça), Bahasa Indonesia, Українська (Ukraynaca), Italiano, Türkçe, فارسی (Farsça) ve Tiếng Việt (Vietnamca) sunulur; “Otomatik”, Telegram uygulamanın dilini izler (bkz. [Diller](#languages)). |
| 🖼 Yanıt nasıl görünsün | Kart + ayrı yanıt; Yanıt kartın içinde; Yalnızca yanıt (kartsız) | Biten bir görevin nasıl göründüğü. “Kart + ayrı yanıt”, kartı yanıtın üstünde, ona nasıl varıldığının kaydı olarak tutar; “Yanıt kartın içinde” yanıtı kartın içine koyar; “Yalnızca yanıt” kartı kaldırır ve yanıtı tek başına bırakır. Varsayılan “Kart + ayrı yanıt” (`KEEP_FINISHED_CARD=0`, hiç seçim yapmamış sohbetler için onu “Yalnızca yanıt” yapar). Bkz. [yanıtlar](#idle-responses). |
| 📝 Değişiklik önizlemeleri | Kapalı; Açık | “Açık”, her `Write`/`Edit` işlemini çalışma kartının altında diff içeren kendi mesajıyla yayımlar. “Kapalı” tek bir kart ve tek bir yanıt bırakır; kart yine her düzenlemeyi listeler ve Mini App'te bir diff görünümü vardır. |
| ⏱ Uzun görevlerde kart güncellemeleri | Açık; Kapalı | “Açık”: çalışma kartı görevin 2. dakikasından sonra her 10 sn'de, 10. dakikasından sonra her 30 sn'de güncellenir (görev ne kadar sürerse sürsün asla daha yavaş değil) ve saati o zaman 30 sn'lik adımlarla ilerler; “Kapalı”, ilk dakikaların temposunu görevin tamamında tutar (bkz. [sorun giderme](troubleshooting.md#a-long-turns-card-refreshes-less-often)). |
| ✏️ Sade biçimlendirme | Kapalı; Açık | “Açık”, Claude'dan yalnızca düz metin ve tireli listeler kullanmasını ister: tablo, kod bloğu, başlık ve kalın yazı yok. |
| 📏 Yanıt uzunluğu | Kural yok; Çok kısa; Kısa; Orta; Uzun | Claude'dan bu uzunlukta yanıt vermesini ister. |
| 🎓 Kelime düzeyi | Kural yok; Basit kelimeler; Normal; İleri | Claude'dan bu kelime düzeyini ister. |
| 🎨 Görünüm | Durdur düğmesi, İzin ver ve Reddet düğmeleri, App düğmesi, Ayarlar menüsü | aipager düğmelerinin bu sohbette nasıl göründüğü; her şey Normal ile başlar (Telegram'ın kendi görünümü). Telegram bir botun düğmeyi yalnızca maviye, yeşile ya da kırmızıya boyamasına izin verir, bu yüzden: Durdur düğmesi (çalışma kartındaki ve bir sorunun ya da izin isteğinin altındaki) Normal, Mavi, Yeşil ya da Kırmızı; İzin ver ve Reddet Normal ya da Renkli (İzin ver ve Her zaman izin ver yeşil, Reddet kırmızı); App düğmesi (sabitlenmiş mesajda ve `/app` altında) Normal, Mavi, Yeşil ya da Kırmızı (mesaj kutusunun yanındaki menü düğmesi boyanamaz); Ayarlar menüsü Normal (✅ geçerli seçimi işaretler) ya da geçerli seçim ayrıca mavi. Oturum başına değil, bütün sohbet için geçerlidir; bir grupta bunu yalnızca bir yönetici değiştirir. Renkleri desteklemeyen Telegram uygulamaları tüm düğmeleri normal gösterir. Mini App'in Ayarlar sekmesinde aynı seçenekler, Sohbet tercihleri'nden sonra kendi bölümünde, Görünüm'de, her biri renkli bir noktayla bulunur. |
| 🆕 Yeni oturumlar | İzinler (mod), Model, Klasör | Sen seçmediğinde `/new` komutunun bir oturumu neyle başlattığı: Sor, Otomatik (varsayılan) ya da Tam erişim modu, model listesinden bir model ya da Claude Code'un varsayılan modeli ve klasör. Burada Tam erişimi yalnızca bir yönetici ya da botla olan kendi özel sohbetindeki kişi seçebilir (özel sohbetin ekranı onu başka kimseye göstermez) ve onu kullanamayan birine asla uygulanmaz: böyle oturumlar Otomatik modda başlar. |
| 👤 Her oturumun kendi ayarları | oturum, sonra “Yeni oturumlar” dışındaki yukarıdaki herhangi bir ayar | Tek bir oturum için değerler (`/status` → `⋮` → `👤 Ayarlar` içinde de var); ⭐ oturumun geçersiz kıldığı ayarı işaretler, “Sohbetteki gibi” geçersiz kılmayı kaldırır. Mini App'teki oturum sayfası aynı değerleri ayarlar. |
| 🐞 Sorun raporları | Bana sor; Kapalı | Yalnızca kurulumun sahibinin özel sohbetinde. “Bana sor”, aipager'ın birden fazla kez olmuş bir iç hata için ara sıra (3 günde birden sık değil) rapor göndermeyi önermesine izin verir; “Kapalı” asla önermez (`/help` içindeki “Sorun bildir” düğmesi çalışmaya devam eder). Arka arkaya iki ret teklifleri kapatır ve bu satır o zaman bunu, onları yeniden açmak için bir düğmeyle söyler. Bkz. [sorun raporları](problem-reports.md#when-aipager-offers-a-report). |

Yanıt uzunluğu, kelime düzeyi ve sade biçimlendirme, Telegram'dan gelen her mesaja eklenen kısa bir talimatla (hook üzerinden, bkz. [hooks](hooks.md#userpromptsubmit)) Claude'a ulaşır; varsa oturumun kendi değerleriyle.

Görünüm ne olursa olsun her çalışma kartı kendi oturumunun durum satırıyla biter (`⏳`/`✅ ad · …`) ve her yanıt kendi sonuç satırıyla başlar (`💬 ad`, artı rakamları gösterecek biten bir kart kalmadıysa `· Bitti (…)`); “Yanıt kartın içinde” görünümü onları üst üste, her satırı kendi bölümünde koyar. Çalışma kartının durum satırının üstünde bir ayırıcı çizgi durur (küçük yazıyla), ve “Yanıt kartın içinde” görünümünde ikinci bir çizgi biten kartı yanıttan ayırır. Kartlı görünümde yanıt, kartın “Bitti” dediğini, yanıt altında belirmeden önce görebilesin diye biten kartın arkasından bilerek küçük bir gecikmeyle gelir; bu farkı `FINISH_CARD_GRACE_SECONDS` ile ayarla ya da kapat (saniye, varsayılan 0,8; 0 ikisini aynı anda gönderir). Görünüm, içinde araç çalışmış olsun ya da olmasın her görevin kartını belirler.

### <a id="per-session-dynamic-commands"></a>Her oturumun kendi komutları

Çalışan her oturum için, adıyla kaydedilen bir komut:

| Biçim | Ne yapar |
|---|---|
| `/<label>` | `<label>` oturumunu mesajlarının gittiği oturum yapar. Yanıt bunu, durumu ve moduyla söyler (`✍️ Artık x1 ile konuşuyorsun · 💤 hazır · 🤖 Otomatik mod`, ardından “Bir mesaj gönder, x1 oturumuna gider.”), seni beklerken **Yanıtla** ve menüsü için **⋮ Daha fazla** sunar. Tam rakamlar `/status` içinde. Grupta bunun yerine “Onunla konuşmak için x1 oturumundan gelen bir mesajı yanıtla (ya da @aipagerbot adını an).” der, çünkü orada sıradan bir mesaj bota ulaşmaz (bkz. [ekip modu](groups.md#talking-to-a-session-in-a-group)). Grubun menüsünde `/x1` seçtiğinde Telegram'ın gönderdiği `/x1@aipagerbot` de aynı şekilde çalışır. |
| `/<label> <prompt>` | `<prompt>` metnini doğrudan o oturuma gönderir. Oturum ayrıca sonraki sıradan mesajlarının gittiği oturum olur. |
| `/<label> stop` | O oturumun mevcut görevini yarıda keser. |

`/status` sonuçları, komut satırındaki `aipager status` komutunun gösterdiği verilerden gelir; oturum listesinin kendisi için Telegram'a çağrı gerekmez.

Bilgisayarda `aipager status` ve `aipager session ls` her oturuma tek bir durum verir: `IDLE`, `BUSY`, `WAITING` (seni bir izin ya da soru bekliyor, biliniyorsa ne olduğuyla, örneğin `permission: Bash: make deploy`) ya da `GONE`. `--json` ile her oturumun ayrıca `waiting_kind` (`permission` ya da `question`) ve `waiting_summary` alanları vardır; beklemiyorsa ikisi de `null`.

## <a id="languages"></a>Diller

aipager English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی ve Tiếng Việt konuşur; her biri kendi adıyla gösterilir.

- **Yalnızca aipager'ın metinleri değişir.** Dil, aipager'daki mesajlara, düğmelere, kartlara ve Mini App'e uygulanır. Claude oturumların değişmez ve Claude ona hangi dilde yazarsan o dilde yanıt verir. Oturum adları, komutlar, model adları ve kod olduğu gibi kalır.
- **`/language`** (ya da `/settings` içindeki ilk satır) bulunduğun sohbetin dilini seçer. Özel sohbette bu aynı zamanda senin kendi dilindir: bir grupta gördüğün kısa bildirimler ve Mini App'in onu kullanır.
- **Otomatik** (yalnızca özel sohbetlerde) Telegram uygulamanın dilini izler ve onu değiştirirsen yeniden izler. aipager'da olmayan bir dil İngilizce gösterilir.
- **Yeni ve eski sohbetler.** Bu sürümden sonra başlayan bir özel sohbet, sen bir dil seçene kadar Telegram uygulamanın dilini izler ve İngilizce olmayan ilk mesajının ardından, bunu ve nasıl değiştirileceğini söyleyen kısa bir satır gelir. Daha önce var olan bir sohbet İngilizce kalır. Telegram uygulaman aipager'ın konuştuğu bir dildeyse o dili öneren ve “İngilizce kalsın” düğmesi olan tek bir kart alırsın; yanıt ne olursa olsun bir kez gelir.
- **Grupların** tek bir dili vardır ve onu bir yönetici `/language` ile ayarlar; o zamana kadar İngilizcedir. Bir dokunuştan sonraki kısa bildirim, dokunan kişinin kendi dilinde gelir: botla olan özel sohbetindeki seçimi, yoksa kişi bu sürümden sonra geldiyse Telegram uygulamasının dili, o da yoksa grubun dili. Bkz. [ekip modu](groups.md).
- **Sağdan sola.** Farsça ve Arapçada aipager'ın metinleri sağdan sola, Farsça ya da Arapça rakamlarla okunur. Claude, dili soldan sağa yazılan bir sohbette Farsça ya da Arapça yanıt verdiğinde yanıtın tamamı sağdan sola bir mesaj olur: içindeki aipager'ın kendi satırları (yanıtın altındaki ajanlar, çalışma kartının durum satırı) geri kalanı gibi sağda durur ve yine de soldan sağa okunur.
- **Mod sözcükleri.** `/mode`, mod sözcüğünü senin dilinde kabul eder (`/mode Sor`), `ask`, `auto` ve `full` ise her zaman çalışır. Çalışan bir oturumun adı tam olarak buysa sözcük bunun yerine oturumu adlandırır (yukarıdaki `/mode` satırına bak).
- **Botun açıklaması.** Başlangıçta aipager, botun açıklamasını (boş bir sohbetin ekranındaki metin) ve kısa açıklamasını bu dillerin her birinde, Telegram'ı o dilde olan kişiler için yazar. Telegram böyle bir metni BotFather'da yazılanın yerine gösterir, bu yüzden aipager, seninki varken hiçbir şey koymaz (açıklama ve kısa açıklama ayrı ayrı kontrol edilir). Senin metninin üzerine asla yazmaz ve İngilizce ya da varsayılan metni asla değiştirmez. Mevcut metni zaten tüm dillerde duruyorsa başlangıç Telegram'a hiçbir şey sormaz. Aksi halde önce BotFather'daki metnini okur (açıklama için bir okuma, kısa açıklama için bir okuma): varsa aipager orada durur, bu yüzden kendi metninle her başlangıç bu okumalara mal olur, daha fazlasına değil. Senin metnin yoksa, henüz kendi güncel metni olmayan her dili okur, bu yüzden BotFather'da senin kendi metnin olan bir dil her başlangıçta yeniden okunur. aipager kendisininkini koyduktan sonra BotFather'da bir açıklama yazarsan bu dillerdeki okuyucular hâlâ aipager'ın açıklamasını görür (bkz. [sorun giderme](troubleshooting.md#the-bot-description-is-not-the-one-i-wrote)).

## <a id="the-mini-app"></a>Mini App

`/app` (ve Telegram'ın menü düğmesi), aipager'ın kendisinin sunduğu bir panel açar. Varsayılan olarak açıktır; onu `aipager miniapp enable|disable|status` ile yönet. Her istek Telegram'ın `initData` imzasıyla doğrulanır (bkz. [güvenlik → Mini App tüneli](security.md#mini-app-tunnel)). Sayfa dışarıdan hiçbir şey yüklemez: başka sitelerden yazı tipi, resim ya da betik yok.

- **Sohbetler.** Uygulama bir seferde tek bir sohbet gösterir. Birden fazla sohbette bulunuyorsan (örneğin kendi özel sohbetin ve bir grup), üstteki sohbet adları satırı aralarında geçiş yapar; uygulama kendi özel sohbetinde açılır ve bu telefondaki son seçimini hatırlar. Aşağıdaki her şey seçili sohbete, oradaki rolünle aittir. Tek bir sohbetle satır gösterilmez. Bkz. [ekip modu](groups.md).
- **Oturumlar.** Tek bir cümle sohbeti özetler, örneğin “1 seni bekliyor, 2 çalışıyor, 3 hazır”, altında da harcanan tutar. Çalışan her oturum, bağlam halkası, klasörü, modeli ve maliyetiyle bir karodur; çalışanlar vurgu rengiyle parlar. Biten oturumlar katlanabilir **Bitenler** rafında durur. **+** düğmesi yeni bir oturum başlatır.
- **Seni bekleyenler.** Bir izin isteği ya da soru bekleyen bir oturum en üstteki kehribar renkli şeritte durur ve ne sorduğunu gösterir. **Sohbette yanıtla**, o isteği düğmeleriyle birlikte sohbetin sonuna yeniden gönderir, tıpkı sabitlenmiş satırdaki **Yanıtla** düğmesi gibi, ve sen orada yanıtlarsın. Bekleyen tek oturum buysa uygulama, istek tam önünde olsun diye kapanır; aksi halde açık kalır ve “Sohbete gönderildi” yazar. Bunun için oturuma mesaj gönderme hakkı gerekir; istek yanıtlandıktan ya da değiştirildikten sonra dokunulan bir kopya ise “zaten yanıtlandı” yazar.
- **Oturum sayfası.** Başlık bağlam halkasını, durumu (“Çalışıyor (3:12)”, “Seni bekliyor (izin)”, “Hazır, son etkinlik 5 dk önce”, “Bitti”) ve tek dokunuşluk **Durdur** (çalışırken ya da beklerken) veya **Devam ettir** (bittiğinde) düğmesini gösterir. Altında: **Sohbette yanıtla** ile bekleyen istek, şerit halinde son araç çağrıları (tam zaman çizelgesini görmek için dokun), son yanıt, ayrıntılar, model seçici, oturumun kendi ayarları, değişen dosyalar ve zaman çizelgesi. ⋮ menüsü, eskisiyle aynı onaylarla tüm işlemleri içerir.
- **Yeni oturum.** Adım adım bir kart: ad, sonra nerede (oturumlarının kullandığı klasörler için **Son kullanılanlar** önerileri, en yeniler önce), sonra model (**Önerilen** önerileriyle), sonra izin modu ve yanıt tarzı için **Daha fazla seçenek**. Telegram'ın alttaki kendi düğmesinin adı **Oturumu başlat**. Rolünde `can_manage` olmayan bir üye için “nerede” yalnızca sohbetin [kök klasörünü](groups.md#root-folders) ve hemen içindeki klasörleri sunar (varsayılan kök klasördür ve “Yeni klasör” seçilenin içinde oluşturur); onun dışındaki bir klasör nedeniyle reddedilir, onun dışında çalışan bir oturum için “Devam ettir”, “Yeniden başlat” ve mod değişikliği ise “Bu oturum klasörünün (~/...) dışında çalışıyor, bu yüzden rolün ona devam edemez, onu yeniden başlatamaz ya da modunu değiştiremez. Bir yöneticiye sor.” yanıtını verir.
- **Ayarlar.** Önce **Uygulama dili**: aipager'ın uygulamadaki ve özel sohbetindeki metinleri için kendi dilin (İngilizce, İspanyolca, Rusça, Portekizce (Brezilya), Çince (Basitleştirilmiş), Fransızca, Almanca, Korece, Arapça, Endonezce, Ukraynaca, İtalyanca, Türkçe, Farsça ya da Vietnamca; “Otomatik” Telegram uygulamanın dilini izler, bkz. [Diller](#languages)). Herkes kendisininkini değiştirebilir; sohbette asla yayımlanmaz ve Claude oturumlarını değiştirmez. Sonra sohbetin ayarları (grupta onları yalnızca bir yönetici değiştirebilir; kendi özel sohbetinde, rolün ne olursa olsun, oradaki `/settings` gibi sen değiştirebilirsin) ve bir yönetici için [`/update`](#update) komutunu yineleyen **Güncellemeler**. Sahip için kendi özel sohbetinde **Sorun bildir** kendi sayfasını açar: bir not yaz, raporu kontrol et, gönder ve referansını uygulamadan çıkmadan gör (bkz. [sorun raporları](problem-reports.md#how-to-send-one)).

Uygulama, değiştirdiğinde Telegram'ın açık ya da koyu temasını izler ve telefon daha az hareket istediğinde yumuşak animasyonlarını durdurur. Mini App'teki her şeye sohbetten de ulaşılabilir: oturum panelindeki ⋮ menüsü aynı işlemleri içerir.

### <a id="switching-a-running-sessions-model"></a>Çalışan bir oturumun modelini değiştirmek

Çalışan bir oturumun sayfasında, oturumun bildirdiği modeli (Claude Code'un durum satırından) ve başlatma seçicisiyle sohbetteki model klavyesinin sunduğu listenin aynısını gösteren bir **Model** seçicisi vardır. Birini seçmek o oturuma tam olarak `/model <name>` yazar; sohbetteki model klavyesinin kullandığı girdinin aynısı ve buna kimin izinli olduğuna dair aynı kuralla (oturuma mesaj gönderebilen herkes). Seçici, durum satırı başka bir model bildirene kadar **değiştiriliyor…** gösterir, sonra onu gösterir; 15 saniye içinde hiçbir şey değişmezse **onaylanmadı (oturumu kontrol et)** gösterir.

- **Yalnızca oturum hazırken, Claude çalışırken ya da bir istek açıkken değil.** Claude Code `/model` komutunu bir görevin ortasında bile sonrasında değil hemen çalıştırır, bu yüzden değişiklik görev bitene kadar reddedilir. Sohbetteki klavye de aynı durumlarda aynı sözcüklerle reddeder.
- **Claude Code'un “Switch model?” sorusu.** Değiştirmeden önce Claude Code genelde bu soruyu sorar, çünkü sonraki yanıtın tüm konuşmayı yeniden okuması gerekir. Claude Code 2.1.251 ya da sonrasıyla aipager'ın `PreModelSwitch` hook'u, aipager'ın az önce yazdığı değişikliği onaylar, bu yüzden soru sorulmaz (bkz. [hooks](hooks.md#premodelswitch)). Diğer her değişiklik yine sorar. Eski bir Claude Code ile ya da `opusplan` modeline veya o modelden geçerken soru yine de görünebilir. O zaman değişiklik *onaylanmadı* olarak gösterilir ve soru terminalde bekler. Oturuma başka bir şey göndermeden önce orada yanıtla, yoksa bir sonraki mesajın o soruya yazılırdı. Aynı nedenle aipager, onaylanmamış bir değişiklikten sonraki bir dakika içinde ya da oturum yeni bir model bildirene kadar bu oturum için bir değişikliği daha reddeder.

## <a id="the-pinned-status-bar"></a>Sabitlenmiş durum satırı

aipager'ın konuştuğu her sohbette (özel sohbetin ve her grup) tek bir sabitlenmiş mesaj vardır: durum satırı. Telegram'ın sohbetin üstündeki şeridi mesajı satırları birleştirerek **ilk satırından** gösterir, bu yüzden bu satır seni en çok neyin beklediğini söyler:

| İlk satır | Ne zaman |
|---|---|
| `⏳ jim seni bekliyor - Bash: make deploy` | bir oturum bir izin isteği, bir soru ya da etkileşimli bir istek bekliyor; başkaları da bekliyorsa `(+2 oturum daha)` |
| `⚙️ jim (çalışıyor)` / `💤 jim (hazır)` | sohbette tek bir çalışan oturum var ve çalışıyor / hazır (başlarken `🔄 jim (başlıyor)`): bu satır, sınırlarla ilgili satır dışında, durum satırının tamamıdır |
| `⚙️ 2 çalışıyor · 1 hazır` | birkaç oturum, hiçbiri beklemiyor: her durumda kaç tane olduğu (`çalışıyor`, `hazır`, `başlıyor`); hiçbiri çalışmıyor ama biri başlıyorsa `🔄`, hepsi hazırsa `💤` |
| `💤 hepsi hazır` | çalışan oturum yok |

Altında, yalnızca geçerli olduğu sürece, sınırlarla ilgili bir satır (`🐢 Telegram uyarısından sonra daha yavaş gönderiyor` (bir 429'dan sonra altı saat) ya da `⏸ kartlar şimdilik güncellenmiyor (Telegram'ın saatlik sınırı)` / `(Telegram sınırı)` (minimal mod, bkz. [sorun giderme](troubleshooting.md#the-hourly-budget))), sonra sohbette birden fazla çalışan oturum varsa `✍️ Mesajlar jim oturumuna gider` (bu sohbette sıradan bir mesajın gittiği oturum, aşağıya bak; herkesin kendininkine sahip olduğu bir grupta değil) ve her oturum için durumuyla bir satır: `çalışıyor`, `seni bekliyor`, `hazır` ya da `başlıyor`. Durum satırı her oturumu bir kez adlandırır: ilk satırın adlandırdığı bekleyen oturum kendi satırını almaz ve ilk satır, aşağıdaki satırların tekrarladığı adları asla listelemez. Arka planda hâlâ çalışan ajanları olan bir oturum, kendi satırında (ya da tek oturumsa tek satırda) kaç tane olduğunu söyler: `💤 jim (hazır, 1 ajan çalışıyor)`. Claude'un arka planda çalıştırdığı komutlar da aynı şekilde sayılır: `(hazır, 1 arka plan komutu çalışıyor)` ya da ikisi birden varsa `(hazır, 2 ajan ve 1 arka plan komutu çalışıyor)`. Bu her zaman adları değil bir sayıdır, bu yüzden satır yalnızca sayı değiştiğinde değişir. Mesaja gitmek için satıra dokun.

**Sıradan bir mesaj nereye gider.** Yanıt olmayan bir mesaj, bu sohbetin en son konuştuğu oturuma gider: sohbetin en son `/<label>` ile geçtiği, buradan en son mesaj gönderilen, burada başlatılan ya da burada sana en son yanıt veren oturum. Çıplak `/mode`, `/clearqueue`, `/diff` ya da `/now` aynı oturuma etki eder (`/stop` çalışanı durdurur). Her sohbetin kendisininki vardır: bir sohbetteki mesaj, başka bir sohbete ait bir oturuma asla gitmez. Grupta yalnızca bota bir yanıt, onu anan bir mesaj, bir komut ya da grubun klavyesine bir dokunuş sayılır (bkz. [ekip modu](groups.md#what-the-bot-reads-in-a-group)) ve her kişinin, bir oturumun yanıtının kaydırmadığı kendi hedefi vardır (bkz. [ekip modu](groups.md#talking-to-a-session-in-a-group)).

Sabitlenmiş mesajın düğmeleri:

- **Yanıtla: &lt;label&gt;** (bekleyen her oturum için bir tane, en fazla üç) o oturumun isteğini yanıt düğmeleriyle birlikte sohbetin sonuna yeniden gönderir, böylece kaydırmadan yanıt verebilirsin. Bu arada yanıtlandıysa bunun yerine kısa bir “zaten yanıtlandı” bildirimi gelir; istek başka bir yerde yanıtlandıktan sonra dokunulan bir kopya (ya da asıl istek) de aynı şekilde reddedilir: daha sonraki bir isteği asla yanıtlamaz. aipager yeniden başladıktan sonra bekleyen bir istek çalışma kartından ya da kendi mesajından çalışmaya devam eder, ama aipager daha eski bir kopyanın hangi isteği gösterdiğini artık bilmez, bu yüzden satırın yeniden başlatmadan önce gönderdiği bir kopyaya dokunmak “bu istek artık açık değil” ile reddedilir. İsteği yanıtlayabilen herkes onu kullanabilir; başka kimse kullanamaz.
- **📱 App**, Mini App açık olduğu sürece özel sohbetinde Mini App'i açar.

Satırda saat, maliyet, bağlam %'si ve model yoktur, bu yüzden yalnızca bir oturumun durumu değiştiğinde değişir. aipager onu (sessizce, bildirim olmadan) yalnızca gösterdiği şey değiştiğinde ve sohbet başına en sık 30 sn'de bir (`PINNED_MIN_EDIT_GAP`) düzenler; bu aralıktaki bir değişiklik aralık bitince gösterilir ve asla kaybolmaz. Sohbete gönderim bir engelleme yüzünden kapalıyken asla düzenlenmez ve engelleme kalkınca yetişir.

Grupta botun sabitlemek için yönetici haklarına ihtiyacı vardır. Sabitlemesine izin verilmezse aipager az önce gönderdiği durum mesajını siler (grubun geçmişinde asla sabitlenmemiş halde asılı kalmasın diye) ve aipager yeniden başlayana kadar bu grupta satırı göstermez; botu tamamen reddeden (atan, engelleyen) bir sohbet için de aynısı geçerlidir. Sabitlenmiş mesajı silersen aipager, sohbet başına en sık saatte bir, yenisini gönderip sabitler.

## <a id="persistent-keyboard"></a>Kalıcı klavye

Sohbetin giriş alanının altında kalıcı bir klavye vardır. Sıralar yukarıdan aşağıya:

| Sıra | Düğmeler | Notlar |
|---|---|---|
| Oturumlar | çalışan her oturumun adı için bir düğme | oturum listesinden kendiliğinden oluşur |
| İşlemler | `Özet`, `Stop`, `Oluştur` | hızlı metin düğmeleri (`Sonlandır` yazılınca çalışmaya devam eder; bir oturumu sonlandırmak `/status` → `⋮` içinde) |
| Gezinme | `Şablonlar ›`, `Komutlar ›` | Mini App, mesaj kutusunun yanındaki menü düğmesinden ya da `/app` ile açılır, bu klavyeden açılmaz |

`Model ›`, “Komutlar” alt menüsünün içindedir. Bir alt menü öğesine dokunmak hazır bir mesaj ya da bir slash komutu gönderir:

- **Şablonlar**: kendini tekrar tekrar yazarken bulduğun mesajlar, örneğin “Test yaz”, “Planı açıkla”, “Belleği güncelle”.
- **Komutlar**: Claude Code'un kendisinin anladığı slash komutları (`/compact`, `/clear` ve diğerleri) hemen yazılır. Bir komut ya da şablon nereye gittiğini söyler (`🧹 /clear gönderildi: x1`). Oturum çalışırken konuşmayı temizlemek reddedilir (`⚠️ Oturum çalışırken konuşma temizlenemez`).
- **Modeller**: etkin oturum için hızlı model değişikliği. Her zaman ailelerinin en yeni modelini ifade eden aile takma adları (`sonnet`, `opus`, `haiku`, `fable`, `opusplan`) ve sabitlenmiş modeller (`claude-opus-5-5`, 1M bağlam pencereli `claude-opus-5-5[1m]`, `claude-sonnet-5-5`, `claude-fable-5-1`, `claude-haiku-5-5`) vardır. Başlatma seçicisi ve Mini App'teki oturum sayfası aynı listeyi kullanır. Oturum çalışırken ya da bir istek açıkken değişiklik reddedilir. `🔄` yanıtı sonra değişir ve oturumun bildirdiği modeli gösterir ya da değişikliğin onaylanmadığını söyler (bkz. [Çalışan bir oturumun modelini değiştirmek](#switching-a-running-sessions-model)).

Varsayılan düzeni değiştirmek için `~/.config/aipager/keyboard.json` dosyasını yaz:

```json
{
  "templates": [{"label": "Deploy",  "prompt": "Deploy to staging"}],
  "commands":  [{"label": "Compact", "send": "/compact"}],
  "models":    [{"label": "Sonnet",  "send": "/model sonnet"}]
}
```

Her bölüm bağımsızdır: eksik bölümler yerleşik değerlerden alınır, bu yüzden diğerlerini belirtmeden birini değiştirebilirsin. Bozuk JSON, günlükte bir uyarıyla yok sayılır. Değişiklikler aipager'ın yeniden başlatılmasını gerektirir.

## <a id="per-message-inline-buttons"></a>Her mesajın altındaki düğmeler

Botun yanıtlarının çoğunda duruma göre düğmeler vardır:

### <a id="permission-prompts"></a>İzin istekleri

Claude onay gerektiren bir araç çalıştırmak istediğinde çalışma kartı bir izin isteğine dönüşür:

```
🔐 Bash: List the temp directory
ls -la /tmp

  [✅ İzin ver]  [❌ Reddet]
  [🟢 Hep izin ver]  [⏹ Durdur]
```

Kart, Claude'un çalıştırmak istediği gerçek komutu (ya da dosya yolunu) onun kendi açıklamasının altında gösterir: okuyabildiğini onayla. Metnin çizildiği sırayı değiştirecek bir karakter (bir komutu başka bir komut gibi gösterebilen U+202E gibi bir Unicode yön denetim karakteri) `⟨U+202E⟩` gibi bir etiketle gösterilir ve kart o zaman şunu ekler: `⚠️ Burada yazı yönünü değiştiren gizli karakterler var (⟨U+...⟩ olarak gösteriliyor). Gördüğünden farklı bir şey çalışabilir.`

- **İzin ver**: bu tek çağrıyı onaylar.
- **Reddet**: onu reddeder; Claude aracı çalıştırmaz.
- **Hep izin ver**: onaylar ve Claude'un önerdiği kalıcı bir kuralı ekler (“… için bir daha sorma”). Düğme **yalnızca Claude böyle bir kural önerdiğinde** görünür; kural çıkaramadığı bir komut için (çoğu bileşik komut) kartta bunun yerine İzin ver / Reddet / Durdur bulunur, oturumun çalışma klasörünün dışında yalnızca okuma amaçlı bir dosya erişimi (`Read`, `Grep`, `Glob`) için de. Claude Code 2.1.259 ve sonrası, bu yere kendi Bash iletişim kutusunda “otomatik moda geç” satırını, klasör dışı okuma iletişim kutusunda “bundan sonra çalışma klasörleri dışındaki okumaları engelle” satırını ve dosya için kural çıkaramadığı bir Write/Edit için, tüm dosya düzenlemelerini otomatik kabul etme (`acceptEdits`) gibi bir izin modu değişikliğini koyar; aipager bunların hiçbirini asla seçmez; modları bilinçli olarak `/mode` ile değiştir.
- **Durdur**: yanıt vermek yerine görevi yarıda keser.

Yanıt, Claude Code'a aipager'ın izin hook'u üzerinden gider; hook onu 20 saniyeye kadar bekler. Çalışma kartından önce gelen bir istek (o zaman yalnızca İzin ver ve Reddet ile kendi mesajı olarak gönderilir) de aynı şekilde yanıtlanır. Bu bekleme bittiğinde düğmeler yanıtı bunun yerine Claude Code'un kendi iletişim kutusuna yazar; Reddet o zaman iletişim kutusunun son satırını (“No, and tell Claude what to do differently”) seçer; bu, Durdur gibi Claude'un görevini bitirir ve kart `🚫 jim · Reddedildi` yazar.

Her dokunuş `~/.claude/aipager-audit.jsonl` dosyasına yazılır ve çalışma kartının altında tek satırlık bir yanıtla tekrarlanır: `✅ jim · İzin verildi · @alice · Bash: List the temp directory` (çağrının açıklaması ya da açıklama yoksa komutu; `aipager config` ile yapılandırılmış bir sohbetle; kişisel modda yalnızca `İzin verildi`).

Bir istek (ya da AskUserQuestion) seni beklerken oturum hazır değil, bekliyor olarak gösterilir. Bu bekleme sırasında Claude Code girdinin boşta durduğunu hatırlatırsa sohbet, bekleme başına bir kez, isteğe yanıt olarak `⬆️ jim · hâlâ yukarıdaki yanıtını bekliyor` alır.

`AskUserQuestion` iletişim kutuları da aynı şekilde, her seçenek için bir düğmeyle (ilk dördü), birden fazla yanıtın seçilebildiği bir soru için ise ⬜/☑ onay kutuları ve bir `✅ Gönder` düğmesiyle gösterilir. Soru ve her seçeneğin açıklaması terminaldeki gibi tam gösterilir. Yalnızca tek bir Telegram mesajına sığmayacak kadar uzun bir form kısaltılır: çalışma kartı önce daha az araç satırı gösterir, sonra soru ve açıklamalar arasındaki en uzun metinler bir kelimenin sonunda `…` ile biter.

**Birden fazla istek.** Claude Code aynı anda iki isteği bekleyebilir: bir arka plan ajanı izin isterken Claude sana bir soru sorar, ya da tersi. aipager ikisini de gösterir: ilkini çalışma kartında (ya da kendi mesajı olarak), sonra gelen her birini kartın altında kendi mesajı olarak, kendi düğmeleriyle. Sonuncusu yanıtlanana kadar oturum bekliyor görünür ve bu arada gönderdiğin bir mesaj o zamana kadar bekletilir. İstediğin sırayla yanıtla. İzin ver ve Reddet, izin hook'u beklediği sürece onun üzerinden gider ve hook, nerede olursa olsun tam o isteğe ulaşır. Tuş yazması gereken bir dokunuş (bir sorunun seçeneği ya da hook beklemeyi bıraktıktan sonra İzin ver ve Reddet) tuşları yalnızca Claude Code'un önce gösterdiği isteğe yazar: Claude Code bir seferde tek bir iletişim kutusu gösterir ve tuşlar en üsttekine gider. Aksi hâlde dokunuş hiçbir şey yazmaz, düğmelerini korur ve `Claude Code önce başka bir isteği gösteriyor: önce onu, sonra bunu yanıtla.` der ya da aipager sırayı bilmediğinde (örneğin başladığını görmediği bir ajan için) `aipager, Claude Code'un hangi isteği önce gösterdiğini bilemiyor: bunu terminalde yanıtla.` der. Kartın isteği önce yanıtlanırsa kart, yalnızca Durdur ile beklediğini gösterir ve diğer istekler düğmelerini korur. Bir ajanın isteği hâlâ açıkken Claude görevini bitirirse yanıt her zamanki gibi gelir ve oturum o isteği beklemeye devam eder; istek yeniden kendi mesajı olarak gönderilir. İki ya da daha fazla istek açıkken yapılan bir yeniden başlatma hiçbirini geri getirmez: düğmeleri `zaten yanıtlandı` yanıtını verir ve hiçbir şey yazmaz, isteklere yine de terminalde yanıt verilebilir. Açık tek bir istek eskisi gibi geri gelir.

Bağlam %80'e ulaştığında sohbet, o oturuma `/compact` gönderen **📦 Şimdi sıkıştır** ile `⚠️ name · Bağlam 82% dolu - yakında otomatik sıkıştırılacak` alır. Bu uyarı her konuşmada bir kez gelir: sıkıştırma onu geri getirmez, yeni bir konuşma (örneğin `/clear` sonrası) onu yeniden alır.

### <a id="stale-buttons"></a>Eski düğmeler

Süren bir göreve etki eden düğmeler (Durdur, Sonlandır, Yeniden başlat, bir `/mode` değişikliği ve onun “Durdur ve geç” düğmesi, `/new` içindeki “Yenisiyle değiştir”) gösterildikleri göreve bağlıdır. Önceki bir görevden kalan bir düğme, şu an sürene etki etmek yerine hiçbir şeyi değiştirmez; oturum sona erip devam ettirilmeden ya da yeniden başlatılmadan önce gösterilen bir düğme de öyle. `/status` içindeki ya da `/stop` listesindeki Sonlandır, Yeniden başlat, bir `/mode` değişikliği ve Durdur, neye yeniden dokunulacağını ya da ne gönderileceğini belirterek `x1 yeni bir göreve başladı: …` (ya da `Bu oturum sona erdi.`) yanıtını verir; çalışma kartındaki Durdur `Bu görev zaten bitti: …` yanıtını verir, `/mode` içindeki “Durdur ve geç” ise kartını `⚠️ x1 yeni bir göreve başladı, bu yüzden hiçbir şey değişmedi.` altında yeniden bir `/mode` kartına çevirir. `/new` içindeki “Yenisiyle değiştir”, oturum kartı gösterildikten sonra bir göreve başladıysa `x1 bu arada çalışmaya başladı. Yerine yenisini koymak için /new komutunu tekrar gönder.` yazar.

Bir isteğin düğmeleri o isteğe bağlıdır. İstek yanıtlandıktan sonra, Telegram'da ya da terminalde, oturumun başka bir isteği hâlâ açık olsa da `zaten yanıtlandı` yanıtını verir ve hiçbir şey yazmazlar.

`⚡ Şimdi gönder` düğmesi yalnızca altında durduğu mesaja etki eder: Claude o mesajı zaten aldıysa dokunmak `Claude zaten aldı` yanıtını verir, hiçbir şey göndermez ve kuyruk satırlarını kaldırır.

### <a id="idle-responses"></a>Yanıtlar

Bir görev bittiğinde yanıt, yanıt görünümünün söylediği şekilde gelir (aşağıya ve [Ayarlar](#settings) bölümüne bak). Claude'un yanıtı tek bir mesaja sığmayacak kadar uzunsa (yaklaşık 32 KB), mesaj sığabildiği kadarını `📎 Yanıtın tamamı aşağıdaki dosyada ↓` notunun altında gösterir ve görevin tamamı (adımları ve tam yanıt) Telegram'ın başka bir uygulama olmadan gösterdiği bir `<name>_full_log.md` dosyası olarak gelir. Çalışma kartı satır atmak zorunda kaldığında da aynı dosya eklenir.

Bir Anthropic API hatasıyla (krediler, aşırı yük, istek sınırı, ağ) biten bir görev, yanıt yerine `⚠️ name · <what went wrong>` gösterir; son mesajı aynı oturuma yeniden gönderen bir **🔄 Yeniden dene** düğmesiyle. Sohbete gönderim [bir engelleme yüzünden kapalıyken](troubleshooting.md#the-bot-went-quiet-flood-control) dokunmak hiçbir şey yapmaz (mesaj yeniden gönderilmez, düğme kalır), bu yüzden engelleme kalkınca ona yeniden dokun.

Kartlı görünümde (“Kart + ayrı yanıt”) biten kart her görev için, yanıta nasıl varıldığının kaydı olarak her zaman yanıtın üstünde kalır: araç satırları, ajan satırları ve Claude'un aralarında söyledikleri ya da araçsız bir görev için yalnızca `✅ name · Bitti · Ns`. Yanıt ayrı bir mesaj olarak (bildirimle) gelir. Görev başına tek bir mesaj için “Yanıt kartın içinde” (yanıt kartın içine girer) ya da “Yalnızca yanıt (kartsız)” (kart kaldırılır ve yanıt tek başına durur) seç.

Her görevin tam olarak bir çalışma kartı vardır. Bir görev sürerken gönderdiğin bir mesaj o göreve asla ikinci bir kart eklemez ve görevi biten bir kart artık Durdur düğmesini tutmaz: onu hiçbir şey kapatmadıysa (kaybolan bir Stop hook'u, bir yeniden başlatma) aipager onu 30 saniye sonra görünümünün söylediği şekilde kapatır. aipager yeniden başladıktan sonra görevi hâlâ süren bir kart korunur ve görevin başından itibaren sayarak hemen işlemeye devam eder ve oturum görevin sonuna kadar çalışıyor olarak gösterilir; yeniden başlatmadan önceden kalan diğer her kart kapanır (“aipager yeniden başlatıldı”, Durdur olmadan). Bir izin isteğini ya da Claude'dan tek bir soruyu bekleyen bir oturum, çalışıyor değil, bekliyor olarak geri gelir: kartı ve sabitlenmiş satır onun seni beklediğini söyler ve isteğin düğmeleri, konuşma geçmişi o arada terminalde yanıtlandığını göstermedikçe onu yanıtlamaya devam eder. Çok parçalı ya da çok seçenekli sorular ve bir alt ajanın istekleri terminale kalır. Bir isteğin bittiği bilindiğinde (dokunuşla ya da terminalde yanıtlandı, görevi bitti ya da yeniden başlatma onu geri getirmedi, çünkü bu arada yanıtlanmıştı ya da kaydedilip geri okunamadı), eski yanıt düğmeleri yeniden başlatmadan sonra bile “zaten yanıtlandı” yanıtını verir ve hiçbir şey yazmaz. Kimsenin yanıtlamadığı bir istek, Claude onu beklediği sürece açık kalır. aipager onu beklemeyi yalnızca Claude'dan 5 dakika haber gelmediğinde ve kayıtlı sohbet isteğin geride kaldığını gösterdiğinde bırakır (bir yanıtın haberi kaybolmuştur); böyle bir istek bitmiş sayılmaz, bu yüzden düğmeleri iletişim kutusunda yanıt vermeye devam eder. Burada yalnızca Claude'un kendisinden ve soran ajandan gelen haberler sayılır: kendi başına çalışan bir arka plan ajanı sayılmaz ve kayıtlı sohbet bir ajanın isteği hakkında hiçbir şey söylemez; o istek o ajanın kendi haberini bekler.

Claude'un **kendi kendine** başlattığı bir görev (hiçbir iş açık değilken bir arka plan ajanının `<task-notification>` ile rapor vermesi), yalnızca bir şey yaptığında çalışma kartı alır: ilk araç çağrısında ya da hangisi önce olursa 15 sn sonra. Bu uyanmaların çoğu birkaç saniyelik “yeni bir şey yok”tur; artık yalnızca yanıtı gösterirler. “yazıyor…” göstergesi görev sürerken görünmeye devam eder ve yanıtın kendisi her zaman teslim edilir: hiçbir şey önemsiz diye elenmez. Senin başlattığın görevler, Telegram'dan ya da terminalden, hemen kart almaya devam eder.

Oturum çalışırken Claude'un başlattığı her arka plan ajanı (Claude Code'daki `Agent` aracıyla, eski sürümlerde `Task`), çalışma kartında kendi satırını alır: ajanın türü ve şu an ne yaptığıyla `🤖 <type> · <activity> · <elapsed>`; satır, ajanın kendi araç çağrılarıyla güncellenir. Bu ajan üç ya da daha fazla araç çağrısı yaptığında bunlar, hemen satırının altında kendi `▸ N araç kullanıldı` düğmesine katlanır; ana görevin zaman çizelgesinde ve `Bash ×N` sayımlarında asla görünmezler. Ajan bittiğinde satırı `✅ 🤖 <type> · N araç · <elapsed>` olarak donar ve aynı düğmeyi korur. Başka bir ajanın başlattığı bir ajan (bir yardımcı), aipager onu kimin başlattığını başladığı anda biliyorsa, kendi türünden önce zincirinin en üstündeki ajanın adını verir: `🤖 pipeline-runner › ship-tester`; satırlar ajanların başladığı sırada kalır. Yukarıdaki `.md` dosyası, görevde çalışan her ajanı, süresini, araç sayısını ve kullandığı araçları içeren bir “Ajanlar” bölümü alır.

Arka plan ajanları görevleri bittikten sonra hâlâ çalışırken kart bir iş durumu olarak kalır. Claude bu arada yeni bir mesaj alırsa canlı kart o mesajın altındakidir ve çalışan ajanları göstermeye devam eder. Hazır bir oturuma gönderdiğin bir mesaj yeni bir görev başlatır: önceki görevin kartı adımları korunmuş halde olduğu yerde donar ve yeni görev kendi kartını alır. Kuyrukta duran ve alınan bir mesaj, işin görevini sürdürür: kartı o mesajın altına yeniden gönderilir ve eski kopya silinir. Bir ajanın içindeki araç çağrıları asla ana görevin kendi satırları olarak gösterilmez ve aipager'ın yeni bir görevin başladığını sanmasına asla yol açmaz.

Claude'un arka planda çalıştırdığı bir komut (`run_in_background`, Ctrl+B ya da çok uzun sürdüğünde Claude Code'un oraya taşıdığı bir komut) çalışırken kendi satırını alır: çağrının açıklamasıyla ya da yoksa komutun ilk satırıyla adlandırılmış `⏳ arka plan komutu: <description> (3:12)`. Bittiğinde satır `✅ arka plan komutu: <description> - bitti (6:12)`, `❌ ... - hatayla bitti (çıkış kodu 1)` ya da `⏹ ... - durduruldu (6:12)` olarak donar (Claude onu TaskStop ile durdurdu ya da Claude Code bellek yetmediği için durdurdu); hatalı ve durdurulmuş satırlar bu işareti biten kartta korur. Böyle bir komut hâlâ çalışırken biten bir görev bitmiş sayılmaz: kart bir iş durumu olarak bekler (`🔄 name · 1 arka plan komutu hâlâ çalışıyor · 4:10` ya da bir ajan da varsa `1 ajan (ship-reviewer) ve 1 arka plan komutu hâlâ çalışıyor`) ve komut bitince Claude'un devamı aynı kartta gelir. Bir ajanın içinde başlatılan komutlar o ajana aittir ve gösterilmez. aipager'ın sonunu hiç görmediği bir komut (bir yeniden başlatma, kaybolan bir olay) iki saat sonra sayılmayı bırakır (`AIPAGER_BG_SHELL_MAX_TRACK`, saniye cinsinden): satırı `⏹ ... - bitişi görülmedi` yazar ve yalnızca onu bekleyen iş `⚠️ name · Bitti (2:00:04, arka plan komutundan haber yok)` ile kapanır.

Bir görevin zaman çizelgesi uzadığında, daha önceki her araç çağrısı dizisi (arka arkaya üç ya da daha fazla ve şu an süren değil), olduğu gibi birikmek ya da Telegram'ın kendi mesaj uzunluğu sınırıyla kesilmek yerine, olduğu yerde kendi `▸ N araç kullanıldı` düğmesine katlanır; yerinde okumak için herhangi birine dokun. Yorumlar asla katlanmaz; en güncel etkinlik ve durum satırı dokunmadan her zaman görünür. Hâlâ çalışan (ya da az önce donan) bir arka plan ajanının kendi satırı asla kendi kendine katlanmaz, yalnızca araç çağrıları katlanır ve zaman çizelgesi ile sınır arasında duran tek şey o olduğu sürece asla katlanmaz. Yalnızca zaman çizelgesi her şey katlanmışken bile sığmayacak kadar büyükse içerik gerçekten karttan çıkarılır; o zaman tam kayıt yukarıdaki `.md` dosyasındadır.

#### <a id="agents-still-running-when-the-answer-goes-out"></a>Yanıt giderken hâlâ çalışan ajanlar

Claude Code ajanları arka planda başlatabilir ve onları başlatan görev, onlar çalışırken bitebilir. O zaman yanıt, bir ayırıcı çizgiyle ve Claude Code'un kendi ajan paneli tarzında, bu arka plan işinin kısa bir durumuyla biter ve iş sürdükçe güncellenir. Çizginin altındaki her şey küçük yazıdır (Telegram'ın alt bilgisi), böylece yanıtın kendisi öne çıkar:

```
⏳ 5 ajan çalışıyor · 🖥 1 arka plan komutu (shell) çalışıyor

○ 🤖 pipeline-runner · Writing final.md for the pipeline

- 🤖 ship-tester · Write and run criteria tests
- 🤖 ship-developer · Editing state.py
- 🤖 ship-developer › ship-reviewer · Collect line numbers

‖ 🤖 r2-runner · kendi ajanlarını bekliyor

Ajanlar bitirince sonuç tam buraya gelecek.
```

- **İlk satır sayar.** Çalışan (ya da biten) ajanları ve bu işin süren, biten ya da durdurulan arka plan komutlarını. Bir arka plan komutu her zaman yalnızca bir sayıdır, asla komutu değil.
- **Ajan başına bir satır,** bir 🤖 ve türü kalın, ardından şu an ne yaptığı: kendi son metin satırı (Claude'un sözcükleri, yazıldığı gibi, asla çevrilmeden), yoksa üzerinde çalıştığı şeyi söyleyen kısa bir ifade olarak son aracı (`Reading state.py`, `Writing final.md`, `Searching for …` ya da bir komutun kendi açıklaması), yoksa Claude'un ajanı başlatırken ona verdiği görev, yoksa yalnızca son aracı (`Running a command`, `Using TodoWrite`), yoksa yalnızca türü. Böylece aynı türden, açıklaması olmayan komutlar çalıştıran iki ajan yine her biri kendi görevini gösterir. Kendi başlattığı ajanları beklemek için duran bir ajan bunun yerine `‖ … kendi ajanlarını bekliyor` der.
- **Ağaç olarak ajanlar.** Oturumun başlattığı ajanlar birinci düzeydir. Bunlardan birinin başlattığı her ajan, ne kadar derinde olursa olsun, hemen onun altında, her biri kendisini başlatan ajanın hemen arkasında listelenir; ikinci düzeydeki bir ajanın başlattığı ajan önce o ajanın adını verir (`ship-developer › ship-reviewer`). Çalışma kartı ise aynı ajanı en üstteki ajandan sonra adlandırır (`pipeline-runner › ship-reviewer`). Asla ikiden fazla düzey olmaz. İkinci düzeyde çalışan bir ajanın kendi ○ işareti yoktur: orada Telegram bir madde işareti çizer. aipager'ın kimin başlattığını henüz bilmediği bir ajan, asla bir tahminin altında değil, birinci düzeyde gösterilir ve başlatanı belli olunca onun altına geçer.
- **En fazla altı satır,** önce hâlâ çalışan ajanlar, sonra satırı olmayan ajanlar için `+N ajan daha`. Bir ajan asla üstündeki ajanlar olmadan görünmez.
- **Söz**, “Ajanlar bitirince sonuç tam buraya gelecek.”, yalnızca bir ajan hâlâ çalışırken kalır. Arka plan komutlarında asla yoktur: bir geliştirme sunucusu hiç bitmeyebilir.

Her ajanın satırı, o ajan durduğunda kendiliğinden `✓ 🤖 Explore · tamamlandı (0:42)` olarak donar (o ajanın ne kadar çalıştığı) ve ilk satır sayıyı azaltır. Listedeki bir ajanın, yanıt gittikten sonra başlattığı bir ajan, bir sonraki güncellemede durumda onun altına katılır; oturumun kendisinin daha sonra başlattığı bir ajan ise sonraki bir yanıtın altında listelenir. Bütün ajanlar bittiğinde satırlar, yanıttan son ajanın bitişine kadar geçen süreyi gösteren tek bir satıra yerini bırakır:

```
✅ 2 ajan tamamlandı (6:12) · 🖥 1 arka plan komutu (shell) çalışıyor
```

ve o komut bitince `✅ 2 ajan tamamlandı (6:12) · 1 arka plan komutu (shell) bitti (2:13:05)` (komutun ne kadar sürdüğü; öldürüldüyse ya da Claude durdurduysa `durduruldu`, hatayla ya da hatasız kendi kendine bittiyse `bitti`; karttaki satırı nasıl olduğunu söyler). İşinde yalnızca bir arka plan komutu olan bir yanıt, `1 arka plan komutu (shell) bitti (…)` ya da `1 arka plan komutu (shell) durduruldu (…)` olarak donan `🖥 1 arka plan komutu (shell) çalışıyor` gösterir.

Yalnızca yanıtın kendi işinin arka plan komutları sayılır: önceki bir görevin çalışır bıraktığı bir geliştirme sunucusu her yanıtın altında tekrarlanmaz (sabitlenmiş satır yine tüm komutları sayar). `/stop` işi bitirir, ama Escape bir arka plan komutunu bitirmez: hâlâ çalışan bir komut bitene kadar sabitlenmiş satırda kalır ve sonraki yanıtların altında artık sayılmaz; daha sonraki devamı o zaman kendi görevini başlatır.

Bu yanıt, görev bittiği anda, mesajına yanıt olarak sıradan bir mesajla (bildirimle) gider: terminalin gösterdiği metnin aynısı. Çalışma kartı, görünüm ne olursa olsun, kendi **Durdur** düğmesiyle işin canlı durumu olarak onun üstünde kalır: `🔄 name · 1 ajan (general-purpose) hâlâ çalışıyor · 1:18`. Ajanlar rapor verdiğinde Claude'un buna verdiği yanıt ayrı bir mesaj olarak gelir; önceki yanıt, aipager yeniden başladıktan sonra bile asla yeniden gönderilmez. Ajanları birden fazla kez rapor veren bir iş, her rapor için bir yanıt verir ve her biri yazıldıkça gönderilir.

Kendi arka plan işi hâlâ sürerken duran bir ajan, daha sonra devam edeceği için çalışıyor sayılır. Kendi ajanını başlatıp onu beklemek için duran bir ajan bu başlatmadan tanınır (diğer ajanı başlatan kendi çağrısından): kendi ajanlarından hiçbiri artık çalışmıyorken durana kadar durumda çalışıyor olarak kalır ve işi açık tutar. Bunların sonuncusu bittikten iki dakika sonra hâlâ devam etmediyse artık çalışıyor sayılmaz ve durum en son gösterildiği gibi kalır. Claude'un bir ajan için gönderdiği `<task-notification>` aynı şeyi bir notla söyler; aipager bu notu, o bildirim bir görev başlattığında ve ayrıca çalışma kartı ekrandayken Claude bildirimi süren bir görevin ortasında aldığında okur. Satır, o not onu geri getirebilsin diye bitmiş görünmeden önce birkaç saniye bekler. Durum, görünüm ne olursa olsun, sohbetin dilinde eklenir.

**Nasıl güncellenir.** aipager yanıtı yalnızca durumun gösterdiği şey değiştiğinde ve yanıt başına en sık 30 saniyede bir sessizce düzenler (yanıtın kendisi ilki sayılır). Durumu olan her yanıt böyle güncellenir (oturum başına beklemedeki beşe kadar). Düzenlemelerin önceliği düşüktür: gönderim bütçesinin reddettiği ya da sohbete gönderimin kapalı olduğu bir zamana denk gelecek bir düzenleme yapılmaz ve durum bir sonraki değişikliği bekler. Her şey bittiğindeki son düzenleme, kapalılık kalktıktan ya da bütçe reddettikten sonra bir kez daha denenir, sonra bırakılır. Silinmiş bir yanıtta, sona ermiş ya da öldürülmüş bir oturum için ya da düzenleme yanıtı Telegram için çok uzun yapacaksa düzenleme yok (uzun bir yanıt kesildiğinde durum için yer bırakılır).

Durum en son gösterildiği gibi kalır ve aipager işin bittiğini bilemediğinde asla “tamamlandı”ya dönmez:

- aipager'ın 30 dakikadır hiçbir şey duymadığı bir ajan (`AIPAGER_SUBAGENT_SILENCE`) artık çalışıyor sayılmaz, ama sessizlik bitiş değildir;
- iki saatten uzun süren (`AIPAGER_BG_SHELL_MAX_TRACK`) ve sonu görülmeyen bir arka plan komutu artık sayılmaz;
- aipager'ın yeniden başlatılması hangi yanıtların beklediğini unutur.

aipager'ın yeniden başlatılması hâlâ çalışan ajanları, kendi ajanlarını bekleyenler dahil, korur: yeniden başlatmadan sonra gönderilen bir yanıt onları listeler, yeniden başlatmanın devraldığı bir çalışma kartı onları yeniden gösterir ve sonraki bitişleri her zamanki gibi tanınır. Yeniden başlatmadan 30 dakikadan daha önce kaydedilmiş ajanlar geri getirilmez.

Gönderimin kapalı olması yüzünden bekletilen ya da biçimlendirmeli gönderim başarısız olduktan sonra düz metin olarak teslim edilen bir yanıt durumsuz gider; yanıt metni olmadan biten bir görev (yalnızca başlık) de öyle.

### <a id="end-confirmation"></a>Sonlandırma onayı

`/kill`, listesi ve `⋮` → `⏹ Oturumu sonlandır` her zaman onay ister:

```
⏹ jim sonlandırılsın mı? Claude durur ve oturum sona erer.

  [⏹ Sonlandır]  [İptal]
```

`⏹ Sonlandır` sonrasında mesaj, bu sohbetin kalan oturumlarının üstünde `⏹ jim sonlandırıldı` yazar. Oturum yeni bir göreve başladıktan sonra dokunulan bir onay reddedilir (`jim yeni bir göreve başladı: …`), o zamandan beri başlatılmış aynı adlı bir oturum için verilen onay da öyle.

### <a id="voice-install-when-extra-isnt-installed"></a>Sesli mesajların kurulumu (paket yokken)

Bir sesli mesaj gönderdiğinde ve `aipager[voice]` kurulu değilse:

```
⚠️ Sesli mesajlar için isteğe bağlı bir paket gerekiyor
   (kurulum için yaklaşık 200 MB, ilk kullanımda model için yaklaşık 74 MB).

  [📦 Sesli mesajları kur]  [İptal]
```

Kur'a dokunmak, kurucun için doğru kurulum komutunu çalıştırır (`uv tool install --reinstall aipager[voice]`, `pipx install --force` ya da son çare olarak `pip install faster-whisper`), mesajı her 5 sn'de düzenler ve başarılı olursa bir `[🔄 aipager'ı yeniden başlat]` düğmesi sunar.

### <a id="restart"></a>Yeniden başlatma

`🔄 aipager'ı yeniden başlat`:

- systemd kullanıcı hizmeti olarak çalışan bir aipager, ayrılmış bir `systemctl --user restart aipager.service` komutunu 5 sn sonrasına (1 sn hassasiyetli bir zamanlayıcıyla), aipager çıktığında yaşamaya devam etsin diye aipager'ın kendi süreç grubunun dışındaki geçici bir hizmette zamanlar. Hizmet oturumlarını sonlandıracaksa (`KillMode` değeri `process` ya da `none` değilse) reddeder ve önce `aipager service install` çalıştırmanı söyler.
- macOS, launchd ajanı kuruluyken (`~/Library/LaunchAgents/com.aipager.daemon.plist`): elle başlattığın bir aipager için bile `launchctl kickstart -k gui/<uid>/com.aipager.daemon`.
- Aksi halde elle başlattığın bir aipager (`aipager start`), Linux'ta hizmet de kurulu olsa bile: üst süreç ölene kadar bekleyen, sonra aipager'ın başlatıldığı komutun aynısını çalıştıran ayrılmış bir yedek başlatır (çıktısı kendi kendini yeniden başlatma günlüğüne gider). Mevcut aipager, yedek ayağa kalkar kalkmaz temiz bir şekilde durur.

SSH gerekmez. Bir aipager güncellemesini bitirmek için güncelleme kartındaki **🔄 Şimdi yeniden başlat** ise aipager'ı aynı terminalde, yerinde yeniden başlatır (bkz. [Güncelleme](#update)).

## <a id="update"></a>Güncelleme

`/update` (yalnızca yöneticiler için; kişisel modda yalnızca aipager'ı kuran kişi için) tek bir düğmeyle yanıt verir: **🔄 Güncellemeleri kontrol et**. Kontrol yalnızca ona dokunulduğunda yapılır. Dokunmak bu mesajı ürün başına bir satıra çevirir:

- daha yeni bir sürüm varsa (PyPI'daki en yenisi) `aipager 1.0.1 → 1.1.0`;
- yoksa `aipager 1.0.1 (güncel)`;
- kontrol başarısız olduysa (ağ yok, 5 sn'lik sınır ya da `claude` bulunamadı) `Claude Code 2.1.282 (kontrol edilemedi)`.

Claude Code, kendi güncelleme kanalının en yeni sürümüyle karşılaştırılır (`autoUpdatesChannel`: latest, stable ya da rc). Satırların altında aipager'ın nasıl kurulduğu küçük harflerle yazar (örneğin `pipx, from PyPI` ya da `pipx, from local path …`; grup sohbetleri asla yol göstermez).

Daha yeni bir şey varsa yalnızca güncellemesi olan ürünleri güncelleyen TEK bir düğme vardır: **aipager'ı güncelle**, **Claude Code'u güncelle** ya da **İkisini de güncelle**, artı **İptal**. aipager önerildiğinde mesaj yeniden başlatmanın nasıl olacağını da söyler: `Yeniden başlatma: otomatik, çalışan görev kalmayınca.`, `Yeniden başlatma: kurulunca “Şimdi yeniden başlat” düğmesine dokun.` (hizmeti olmayan bir aipager, aşağıya bak) ya da `Yeniden başlatma: elle (neden)`.

Daha yeni bir aipager zaten kuruluysa ama çalışan aipager hâlâ eskisiyse (yeniden başlatma olmadan biten bir güncelleme ya da elle yapılan bir `pip install -U`), satır `aipager 1.1.0 kurulu ve yeniden başlatılmayı bekliyor (çalışan: 1.0.1).` yazar ve mesaj bir güncelleme yerine **🔄 Şimdi yeniden başlat** önerir: yalnızca yeniden başlatma gereken bir sürüm için aipager'ı güncelle asla önerilmez. Kurulu sürüm, aipager'ın yeniden başlayacağı yorumlayıcı çalıştırılarak kontrol edilir, bu yüzden kurulu ama yüklenmeyen bir sürüm bunu söyler (`aipager 1.1.0 kurulu ama yüklenmiyor. … ile yeniden kur.`) ve hiçbir şey önermez. Kurulu olandan daha yeni bir sürüm yine güncelleme olarak önerilir. Buradan güncellenemeyen bir kurulumdaki (editable, Nix, Snap, bir sistem paketi, bir konteyner ya da başka bir kullanıcının kurulumu) daha yeni bir aipager nedeniyle gösterilir ve önerilmez. Kullanıcının yazamadığı bir klasöre npm ile kurulmuş (örneğin `/usr/lib/node_modules` altında `sudo npm install -g`) ve `claude update` komutunun yalnızca başarısız olabileceği daha yeni bir Claude Code da öyle: satır `buradan güncellenemez: installed with npm into a folder your user can't write to; run "claude install" once to switch to Claude Code's own installer, then restart aipager` yazar. Güncellenecek bir şey yoksa mesaj “Her şey güncel.” der, bir kontrolün başarısız olduğunu söyler ya da buradan kurulamayan daha yeni bir şey varsa “Bir güncelleme var ama buradan kurulamıyor.” der; bir **Tekrar kontrol et** düğmesiyle ve güncelleme düğmesi olmadan.

Güncelleme düğmesi yalnızca kontrolün önerdiğini başlatır. Kontrol 10 dakikadan eskiyse, o zamandan beri başka bir güncelleme yapıldıysa ya da sürümler artık düğmeyle eşleşmiyorsa bunun yerine yeniden kontrol etmeni ister. Daha eski bir `/update` menüsünden kalan düğmeler (ayrı **Claude Code'u güncelle** / **aipager'ı güncelle** / **İkisi**) “Bu menü eskidi: /update komutunu tekrar gönder” yanıtını verir ve hiçbir şey yapmaz. Her dokunuş yönetici kuralını yeniden kontrol eder.

Claude Code'u güncellemek `claude update` komutunu çalıştırır (mutlak yolla, 5 dk sınır) ve `Claude Code A → B`, “zaten güncel” ya da çıktısının sonuyla birlikte hatayı bildirir. Çalışan oturumlar sen onları yeniden başlatana kadar (`/restart`) eski sürümde kalır; yanıt onları listeler. Hiçbir oturum senin yerine yeniden başlatılmaz. Yeni oturumlar yeni sürümü kullanır.

aipager'ı güncellemek (**aipager'ı güncelle** ya da önce Claude Code'u güncelleyen **İkisini de güncelle** düğmesinin ikinci yarısı):

1. Zaten güncel olan bir PyPI kurulumunda bunu söyler ve durur.
2. aipager kendini yeniden başlatabiliyorsa **önce hiçbir şeyin sürmemesini bekler**: bir görev yürüten, bir soruyu bekleyen, çalışan bir arka plan ajanı tutan, canlı bir çalışma kartı gösteren, bir araç çalıştıran ya da açık bir izin isteği tutan hiçbir oturum ve bir sınırın bitmesini bekleyen hiçbir bekletilmiş yanıt yok. Mesaj neyi beklediğini **Şimdi yeniden başlat** (beklemeyi atla) ve **İptal** ile listeler. 10 dakika sonra yeniden sorar: **10 dk daha bekle**, **Şimdi yeniden başlat**, **İptal**. Bir saat yanıt gelmezse kendiliğinden iptal olur.
3. Çalışan aipager'ın sahibi olan kurucuyla günceller (`pipx upgrade aipager`, `uv tool upgrade aipager`, `brew upgrade aipager` ya da `<venv>/bin/python -m pip install --upgrade aipager`), mutlak yolla ve 10 dk sınırla. Kurucu çalışırken iptal edilemez.
4. Yeni sürümün temiz bir yorumlayıcıda yüklendiğini kontrol eder. Başarısız olan, çok uzun süren ya da yüklenmeyen bir güncelleme hiçbir şeyi yeniden başlatmaz. Sınırı aşan bir güncelleme ortasında durdurulmuştur, bu yüzden mesaj kurulumun eksik olabileceği konusunda uyarır ve bir sonraki yeniden başlatmadan önce çalıştırılacak yeniden kurulum komutunu verir. Kurucunun çıktısı yalnızca bir özel sohbette gösterilir; bir grup “(çıktı aipager günlüğünde)” alır.
5. Güncelleme sırasında bir görev başladıysa yeniden bekler.
6. Ayrılmış bir `systemctl --user restart aipager.service` komutunu 5 sn sonrasına (1 sn hassasiyetli bir zamanlayıcıyla) zamanlar ve durum mesajı `⏳ aipager A → B kuruldu, yeniden başlatılıyor…` yazar. 5. adım beklerken `⏳ aipager A → B kuruldu; mevcut görev bitince yeniden başlayacak` yazar. Yeniden başladıktan sonra yeni aipager aynı mesajı `✅ aipager güncellendi A → B; N oturum yeniden bağlandı` olarak değiştirir (geri gelmeyen her oturum için `⚠️ Geri gelmeyenler: …` ile). Mesaj silindiyse ya da artık düzenlenemiyorsa sonucu, soran sohbete yeni bir mesaj olarak gönderir.

Mini App'te **Ayarlar → Güncellemeler**, yeni aipager yanıt verene kadar dönen bir fenerle “aipager yeniden başlatılıyor…”, sonra “B sürümüne güncellendi; N oturum yeniden bağlandı.” gösterir. Asla geri sayım göstermez. Yaklaşık 2 dakika yanıt gelmezse (eski aipager'ın, olmayan bir yeniden başlatmayı bildirebileceği süreden asla önce değil) “Hâlâ yeniden başlıyor, birazdan uygulamayı yeniden aç.” yazar. aipager'ın yönetilen tüneliyle bir güncellemenin yeniden başlatması uygulamanın adresini korur (yeni aipager tüneli devralır), bu yüzden açık sayfa bunu atlatır ve “B sürümüne güncellendi” gösterir. Tünel sonunda korunamadıysa sayfa yaklaşık 2 dakika sonra “Hâlâ yeniden başlıyor, birazdan uygulamayı yeniden aç.” yazar. Bu özelliği olmayan bir aipager'dan (ya da `MINIAPP_KEEP_TUNNEL=0` ile) başlatılan bir güncelleme adresi yine değiştirir: eski aipager yanıt vermeyi bırakır bırakmaz açık sayfa “aipager yeniden başladı. Sohbet güncellendiğini söyleyince uygulamayı yeniden aç” yazar; onu kapat ve sohbetin menü düğmesinden ya da `/app` ile yeniden aç. Uygulamaya giriş 5 dakika geçerlidir, bu yüzden daha uzun süre açık kalan bir sayfa bunun yerine “Güncellemenin sonucunu görmek için uygulamayı yeniden aç.” yazar.

aipager güncellemeden sonra kendini yalnızca bir systemd kullanıcı hizmeti olarak çalışıyorsa **ve** bu hizmetin `KillMode=process` (ya da `none`) değeri varsa yeniden başlatır. Güvensiz bir `KillMode` ile aipager yine güncellenir ve mesaj `Yeniden başlatma gerekli: …` der: önce `aipager service install` çalıştır (yeniden başlatmanın sonlandıracağı oturumları listeler).

**Hizmet olmadan Şimdi yeniden başlat.** Hiçbir hizmet yöneticisinin yeniden başlatmadığı bir aipager (bir terminalde ya da tmux'ta `aipager start`, Docker ya da macOS'taki launchd ajanı) güncellemeyi kurar ve sonra bir **🔄 Şimdi yeniden başlat** düğmesiyle `Çalıştırmak için “Şimdi yeniden başlat” düğmesine dokun (aipager çalışan görev kalmayana kadar bekler).` gösterir. Ona dokunmak (her güncelleme düğmesi gibi yalnızca yöneticiler için):

1. kurulu aipager'ın yüklendiğini kontrol eder (`⏳ Kurulu aipager kontrol ediliyor…`); bozuk bir kurulum hiçbir şeyi yeniden başlatmaz, aynı sürüm ise yeniden başlatılacak bir şey olmadığını söyler;
2. systemd yeniden başlatması gibi, devam eden hiçbir şey kalmayana kadar **Şimdi yeniden başlat** (beklemeyi atla) ve **İptal** ile bekler;
3. kurulumu yeniden kontrol eder, sonra `⏳ aipager A → B kuruldu, yeniden başlatılıyor…` der ve aipager'ı temiz bir şekilde durdurur (Ctrl+C gibi);
4. yeni aipager'ı yerinde başlatır: aynı süreç, aynı komut, aynı ortam ve klasör, aynı terminal ya da tmux penceresinde (ve Docker'da aynı konteyner süreci). Oturumlar çalışmaya devam eder. Yeni aipager mesajı `✅ aipager güncellendi A → B; N oturum yeniden bağlandı` olarak düzenler.

Yeniden başlatmanın birkaç saniyesinde gönderilen dokunuşlar, her yeniden başlatmada olduğu gibi kaybolur. Yeniden başlatma sırasında bir Ctrl+C ya da `docker stop` kazanır: aipager durur ve yeniden başlamaz. Yeni aipager yerinde başlatılamazsa, eskisi gittikten sonra ayrı bir kopya onu başlatır (çıktısı kendi kendini yeniden başlatma günlüğüne, örnek klasöründeki `aipager.log` ya da `/tmp/aipager.log` dosyasına gider ve o kopya geri gelmezse aipager günlüğü çalıştırılacak komutu söyler); bu da başarısız olursa ya da yorumlayıcının kendisi yoksa aipager 1 koduyla çıkar ve aipager günlüğü çalıştırılacak komutla birlikte `restart.failed:` der.

aipager kendini hiç yeniden başlatamadığında mesaj, bir düğme yerine kurulumuna uygun adımları tam olarak verir:

- bir terminal ya da tmux: `aipager'ın çalıştığı terminal ya da tmux penceresinde Ctrl+C tuşlarına bas, sonra aipager start komutunu çalıştır.`
- macOS: `Mac'te launchctl kickstart -k gui/<uid>/com.aipager.daemon komutunu çalıştır.`
- Docker: `Docker'ın çalıştığı bilgisayarda docker restart <container> komutunu çalıştır.` (sunucu adı bir kimlikse konteynerin kısa kimliği), konteyneri imajından yeniden oluşturmanın o imajdaki aipager sürümünü geri getirdiğine dair bir hatırlatmayla.

**Şimdi yeniden başlat**, daha yeni bir aipager kurulu ve bekliyorsa **Güncellemeleri kontrol et** sonucunda da görünür, bir de tek seferlik bir bildirimde: Claude Code'un hook'ları (her olayda kurulu aipager'dan yeniden başlayan) çalışan aipager'dan daha yeni bir aipager'ı zaten çalıştırıyorsa, sahip her yeni sürüm ve her aipager çalışması için düğmeyle birlikte tek bir sessiz mesaj alır: `⚠️ aipager güncellemesi yarım kaldı: …`. Bu mesaj, hook'ların daha yeni sürümü ilk çalıştırdığı andan itibaren iki dakika bekler (bir kurulumun hemen ardından gelen bir yeniden başlatma onu hiç göstermez), sonra sahibin özel sohbetinde çalışan görev kalmayana kadar bekler. O sürüme yeniden başlattıktan sonra bildirim, düğmesi olmadan `✅ aipager güncellemesi tamamlandı: aipager artık B sürümünde çalışıyor.` olarak düzenlenir. Mini App'te **Ayarlar → Güncellemeler** aynı durumu kartın üstünde bir satır olarak, **Şimdi yeniden başlat** ile gösterir (önce sorar).

Mini App'te **Güncellemeler**, Ayarlar sekmesindeki ilk karttır: adı, çalışan sürüm ve satırın sonunda küçük bir **Güncellemeleri kontrol et**. Ayarlar sekmesini açmak kendiliğinden kontrol eder (en fazla on dakikada bir, bir güncelleme sürerken ya da aipager yeniden başlarken asla; orada başarısız olan bir kontrol hiçbir şey söylemez), bir dokunuş ise her zaman kontrol eder. Kart, yapılacak bir şey olana kadar tek bir sakin satır olarak kalır: daha yeni bir sürüm kendi satırlarını ve tek güncelleme düğmesini getirir, “Her şey güncel.” düğmesiz tek bir satırdır, süren bir güncelleme, bir yeniden başlatma ya da **Şimdi yeniden başlat** ise kendi düğmelerini korur.

`/update`, Mini App'in **Ayarlar → Güncellemeler** kartı (aynı kontrol, aynı tek güncelleme düğmesi, aynı iş) ve komut satırındaki `aipager update` genelinde aynı anda yalnızca bir güncelleme çalışır. Bu, “kuruldu, yeniden başlatılıyor…” ile yeniden başlatmanın kendisi arasındaki saniyeleri de kapsar: `/update` aipager'ın birazdan yeniden başlayacağını söyler, Mini App düğme sunmaz ve sesli mesajların **aipager'ı yeniden başlat** düğmesi bir güncelleme sürerken ya da yeniden başlatmayı beklerken reddeder. aipager yeniden başlatmasını zamanladıktan iki dakika sonra hâlâ hayattaysa zamanlanmış yeniden başlatma zamanlayıcısını durdurur, güncelleme kilidini bırakır ve onu kendin yeniden başlatmanı söyler.

Bir kurucu çalışırken aipager kapanırsa kurucuya bitirmesi için 3 sn verilir, sonra durdurulur (aksi halde bir sonraki aipager başlarken kuruluma yazmaya devam ederdi). Durum mesajı ve yeniden başlatmadan sonra yeni bir mesaj, kurulumun eksik olabileceğini ve nasıl onarılacağını söyler.

Bir kapanış başladığında hiçbir şey yeniden başlatılmaz ve yeni hiçbir şey başlamaz: `/update` düğmeleri aipager'ın kapandığını söyler, Mini App 503 ile yanıt verir ve ne kurucu ne de sürüm kontrolü başlatılır. 3 saniyesi içinde biten bir kurucu yine sayılır. Durum mesajı yeni sürümün kurulduğunu ve hiçbir şeyin yeniden başlatılmadığını söyler, bir sonraki başlangıç da `aipager güncellendi A → B` bildirir. `aipager service stop` ile durdurduğun bir aipager durmuş olarak kalır. Güncellemenin kapanıştaki payı toplamda en fazla 8 sn sürer.

## <a id="free-messages"></a>Sıradan mesajlar

### <a id="text"></a>Metin

**Etkin oturum** için bir sonraki mesaj olarak ele alınır: bu sohbetin hedefi, yani sohbetin en son geçtiği, buradan en son mesaj gönderilen, burada başlatılan ya da burada sana en son yanıt veren oturum (bkz. [sabitlenmiş durum satırı](#the-pinned-status-bar)). Grupta her kişinin, bir oturumun yanıtının kaydırmadığı kendi hedefi vardır (bkz. [ekip modu](groups.md#talking-to-a-session-in-a-group)). Mesajlar, kendi görevin sürerken bile, tıpkı terminalde yazmak gibi **hemen** Claude'a ulaşır (grupta başka birinin mesajı o görev bitene kadar bekler; aşağıya bak). Birkaç tane gönder, her birini doğal bir sınırda alan Claude'un kendi kuyruğuna girerler:

Mesajındaki tepki onu izler, tıpkı Claude Code'da bekleyen bir mesajın griden beyaza dönmesi gibi:

- 👀: oturuma verildi (ya da bekletildi, aşağıya bak), ama Claude henüz almadı. Süren bir görev sırasında gönderilen bir mesaj, Claude'un kuyruğunda beklediği sürece 👀 kalır.
- 👍: Claude onu aldı: onunla bir görev başlattı, süren göreve kattı ya da çalışan bir arka plan ajanına verdi.
- 🤷: asla alınmayacak: `/stop`, `/clearqueue` ya da `/kill` ile atılan ya da serbest bırakıldığında gönderilemeyen bekletilmiş bir mesaj; `/stop`, `/clearqueue`, `/kill` ya da oturumun sona ermesi (`/clear` ya da `/resume` değil) onu attığında hâlâ bekleyen bir mesaj ya da komut; ya da Claude Code'un reddettiği bir mesaj (aşağıya bak). Claude'un zaten kuyruğa aldığı mesajlar yalnızca aipager bu kuyruğu görebildiği sürece işaretlenir (konuşma geçmişinin canlı okunması çalışıyor ve hiçbir arka plan işi beklemiyor); aksi halde 👀 olarak kalırlar. Terminalde Escape, Claude'un kuyruğunu yeniden gönderilebileceği giriş alanına geri getirir, bu yüzden böyle atılan bir mesaj da 👀 olarak kalır.
- 👌: çalışmış bir Claude Code komutu (aşağıya bak) ya da bir `/stop` komutunu onaylayan aipager.

Bir tepki her zaman yalnızca ileri gider, bu yüzden bir mesaj her biri bir kez olmak üzere en fazla üç tepki alır. Bir Telegram sınırı engellemesine denk gelen bir tepki atlanır, sonra eklenmez ve bir temizleme, attığı en yeni on mesajdan fazlasını işaretlemez.

Komut düğmeleri (`Sıkıştır`, `/model …`): oturum hazırken dokunulursa komut hemen 👌 alır; Claude Code onu Enter ile çalıştırır ve `/model` gibi yerel bir komut, bunu sonradan söyleyebilecek bir hook tetiklemez. Süren bir görev sırasında dokunulursa (`/model` değil: o, görev bitene kadar reddedilir, bkz. [Çalışan bir oturumun modelini değiştirmek](#switching-a-running-sessions-model)), o görev bitene kadar (normal şekilde, bir arka plan işinin ara durağı olarak ya da bir API hatasıyla) 👀 kalır, sonra 👌; ondan önce `/stop`, `/clearqueue`, `/kill` ya da oturumun sona ermesiyle atılırsa hiç çalışmamıştır: 🤷. Claude Code onu bunun yerine bir mesaj olarak kuyruğa alırsa 👀 → 👍 yolunu izler. Bir sesli mesaj, metni gönderildiği anda metinle aynı tepkileri alır.

Çalışma kartı ve sonra yanıt, yalnızca gönderdiğin son mesajı değil, Claude'un bir görev için gerçekten aldığı mesajı izler (oturum hazırsa onunla başladığı mesaj ya da süren göreve kattığı mesaj). Bir görevin ortasındaki başka bir mesaj “yanıtı kendine çekmez”: Claude onu şu an yazdığı yanıta katarsa kart o anda ona geçer; Claude önce bitirip sonra onu alırsa ilk yanıt ilk mesajın altında kalır ve sonraki kendi görevini, altında kendi kartı ve yanıtıyla alır.

Claude Code bir mesajı doğrudan reddederse (bilinmeyen bir slash komutu ya da terminalde yalnızca bir iletişim kutusu açan yerleşik bir komut), hiçbir hook tetiklenmez, bu yüzden aipager'ın az önce duyurduğu görevi hiçbir şey bitirmezdi. Hiçbir hook gelmeden 8 sn geçince (`PROMPT_HOOK_GRACE_SECONDS`) çalışma kartı tek satırlık bir açıklamayla `⚠️ name · Claude Code mesajı almadı` olur ve oturum yeniden hazırdır; nedeni terminalde görünür ve mesaj 🤷 alır (bunun yerine bir slash komutu 👌 alır: iletişim kutusu açan yerleşik bir komut da hook tetiklemez). Yalnızca bir görev başlatan mesaj bu şekilde değerlendirilir; süren bir görevin arkasında bekleyen bir mesaj, Claude onu alana ya da atılana kadar 👀 olarak kalır.

Üç durum gönderilmek yerine bekletilir ve çözüldüklerinde kendiliğinden teslim edilir:

- Bir izin isteği ya da soru açık, hem de açık kaldığı sürece (süre sınırı yok): aksi halde metnin o iletişim kutusuna bir yanıt olarak okunurdu. O sırada gönderdiğin ilk mesaja isteğin altında, Claude'un hâlâ yanıtını beklediğini söyleyen tek bir yanıt gelir (sonrakilere yalnızca 👀 gelir). Telegram'da ya da terminalde yanıt verdiğin anda bekletilen mesajlar gönderdiğin sırayla Claude'a gider ve o yanıt silinir. İstek hâlâ açıkken oturum biterse mesajlar bir 🤷 ile bırakılır.
- (Ekip modu) Başka bir kişinin mesajı hâlâ alınmayı bekliyor.
- (Ekip modu) Başka bir kişinin görevi sürüyor, başlattığı bir arka plan ajanı dahil. Terminalde yazılmış bir görev sahibin görevi sayılır, bu yüzden ona yalnızca sahibin mesajı (ya da bir özel sohbette, kendi üyesinin mesajı) katılır.

Son ikisi, her biri kendi göndereninin yetkileriyle çalışsın diye farklı kişilerin mesajlarını ortak bir görevin dışında tutar. Biri yine de katılırsa (örneğin aipager'ın tanımadığı ve hiç bekletilmeyen bir gönderenden), görev ona katılan herkesin en sıkı kurallarıyla çalışır (bkz. [ekip modu](groups.md#who-a-message-runs-as)). Bir mesajın beklediği görev durdurulursa ya da engellenen bir araç onu yarıda keserse bekletilen mesajlar bir 🤷 ile atılır.

Bekletilen mesajlar oturum başına 50 ile sınırlıdır ve 24 sa sonra süreleri dolar; `/clearqueue` onları Claude'un tuttuğu her şeyle birlikte atar.

#### <a id="send-a-queued-message-now"></a>Kuyruktaki bir mesajı şimdi göndermek

Claude kendi görevin üzerinde çalışırken gönderdiğin bir mesaj hemen Claude'a gider (aipager onu bekletmez) ve mevcut adım bitene kadar Claude'un kuyruğunda bekler. Claude'un kuyruğu onu gösterir göstermez aipager altına yanıt verir:

```
⏳ Kuyrukta: Claude bunu mevcut adımdan sonra okuyacak
[⚡ Şimdi gönder]
```

Bekleyen her mesaj kendi satırını alır ve her satır, Claude o mesajı aldığı anda kendi 👍 tepkisiyle birlikte kaybolur. Claude'un kuyruk kaydı görülmeden önce aldığı bir mesaj satır almaz, yalnızca 👀 ve sonra 👍 alır.

`⚡ Şimdi gönder` düğmesine dokunmak (ya da `/now` göndermek) bu oturumda Claude Code'un kendi “şimdi gönder” tuşlarına basar (Ctrl+X, sonra Ctrl+S). Claude o zaman yalnızca dokunduğun mesajı değil, kuyruğunda olan her şeyi birden okur. Süren işe ne olacağına Claude Code karar verir: çalışan bir komut ya da ajan arka planda çalışmaya devam eder; Claude bir yanıt yazıyorsa yanıt yarıda kesilir ve mesajınla yeniden başlar. Başka türden bir adım (örneğin başka bir araç) da kısa bir süreden sonra yarıda kesilebilir. Bazı durumlarda (örneğin Claude Code'un arka plan görevleri kapalıyken) şimdi gönder, mevcut adımın basit bir kesintisine dönüşür. Bunların hepsi aipager'ın kararı değil, Claude Code'daki şimdi gönder özelliğinin kendi davranışıdır.

Dokunmak `Hemen Claude'a gönderildi` yanıtını verir ve şimdi gönder Claude'a tuttuğu her şeyi verdiği için tüm satırlar birden kaybolur; `/now` da onları aynı şekilde kaldırır ve Şimdi gönder'in gönderdiği mesajlar için satır gelmez (ondan sonra gönderdiğin bir mesaj kendi satırını alır). Claude mevcut adımı arka plana almak yerine yarıda keserse hemen yalnızca en eski bekleyen mesajı alır ve geri kalanını bir sonraki adımda okur; yanıt, aldığı mesajın altına gelir ve o mesaj iki kez çalışmaz. Tuşların Claude'a ulaşması bir andan uzun sürerse (yoğun bir bilgisayar), dokunmak bunun yerine `Hemen Claude'a gönderiliyor` yanıtını verir; satır o zaman tuşlar ulaşınca kaybolur ya da teslim edilemedilerse yeniden dokunabilesin diye düğmesiyle birlikte kalır ve `⏳ Kuyrukta: Claude'a ulaşılamadı, tekrar denemek için “Şimdi gönder” düğmesine dokun` yazar. Tuşlara yalnızca Claude'un konuşma geçmişi onun mesajı hâlâ tuttuğunu gösterdiğinde basılır; aksi halde dokunmak `Claude zaten aldı` yanıtını verir ve satırı kaldırır. Bir izin isteği ya da soru açıkken (`Önce açık soruyu yanıtla`), Claude henüz aipager'ın göstermediği bir tanesini az önce açmış olabilirken (önceki görevin yanıtı teslim edilirken yeni bir görevin olayları bekletilir: `Meşgul, birazdan tekrar dene`) ve aipager oturuma başka bir mesaj yazarken (`aipager şu an bir mesaj yazıyor, birazdan tekrar dene`) hiçbir tuşa basılmadan reddedilir. Oturuma mesaj gönderebilen herkes düğmeyi ve `/now` komutunu kullanabilir; `read_only` rolündeki bir üye kullanamaz.

Satır, mesajı Claude'un kuyruğundan çıktığında kendiliğinden kaybolur: Claude onu alır (süren göreve katar, bir arka plan ajanına verir ya da onunla bir sonraki görevi başlatır), terminalde Escape kuyruğu giriş alanına geri getirir ya da `/stop`, `/clearqueue`, `/kill`, `/restart`, bir güvenlik durdurması veya oturumun sona ermesi onu atar. aipager'ın yeniden başlatılması hâlâ görünen her satırı kaldırır.

Satırlar asla sınır yönetimini beklemez: yoğun ya da yavaş bir sohbette ve minimal modda bile hemen gider ve kaldırılır. Yalnızca Telegram'ın kendisi sohbeti susturmuşken satır gönderilmez (`/now` o zaman da çalışır) ve Telegram aipager'dan az önce yavaşlamasını istediyse bir satır istediği birkaç saniyeyi bekler. Bekleyen her mesaj, satırı için sohbete bir çağrıya mal olur; birlikte giden satırlar tek bir çağrıyla kaldırılır.

### <a id="files"></a>Dosyalar

Yüklenen dosyalar `/tmp/aipager-files/` klasörüne (`<time>_<file name>` adıyla) indirilir ve yol Claude'a sunulur: bir açıklamayla mesaj, açıklama ve ardından yol (ya da yollar); açıklama olmadan yalnızca `check this: <path>` (ya da bir albüm için `check these: <paths>`), böylece Claude'a onunla ne yapacağı söylenmeden dosyaya işaret edilir. `/<label>` ile başlayan bir açıklama, metindeki `/<label> <prompt>` gibi oturumu seçer ve ad mesajdan çıkarılır: tek başına `/api`, `api` oturumuna `check this: <path>` gönderir, `/api compare these` ise oraya `compare these <paths>` gönderir. Hedefi yalnızca en baştaki bir `/<label>` seçer; açıklamanın daha ilerisindeki bir eğik çizgi yalnızca metindir. Hiçbir oturumun yanıt vermediği bir ad `⚠️ Bu adda bir oturum yok` ile reddedilir ve hiçbir şey gönderilmez. Telegram'ın botlar için 20 MB indirme sınırı önceden uygulanır; çok büyük dosyalar indirme denenmeden önce açık bir ret alır. Geçici bir ağ hatasına takılan bir indirme, sen bir hata görmeden önce toplam üç kereye kadar denenir (1 sn, sonra 2 sn bekleyerek) ve bu hata dosyanın adını verir. Bir albüm (tek mesaj olarak gönderilen birkaç fotoğraf ya da belge), son öğesi geldiği anda Claude'a tek bir mesaj olarak (açıklama, sonra sırayla her dosyanın yolu) verilir; bir öğe indirilemezse geri kalanı, eksik olanın adını veren bir notla yine gider.

### <a id="files-claude-sends"></a>Claude'un gönderdiği dosyalar

Claude oturumundan sana dosya gönderebilir: kendi sözlerinle iste ("ekran görüntüsünü gönder", "~/proj/chart.png'deki resmi gönder"), ya da istediğini düşündüğünde kendisi gönderir. Bunun için Claude Code'un SendUserFile aracını kullanır (Claude Code 2.1.301 veya daha yenisi); aipager'ın başlattığı her oturum bu aracı alır: aipager oturumu `CLAUDE_CODE_ATTACHMENTS_BY_PATH=1` ile başlatır ve Claude'a aracın orada olduğunu söyler. aipager'ın bunu yapabildiği sürümden önce başlatılmış bir oturum, aracı ancak yeniden başlatıldıktan sonra alır: `/restart`, oturumu yeniden başlatan bir mod değişikliği ya da Devam ettir.

- Dosyalar görevin cevabından sonra, ona yanıt olarak gelir (görevin cevabı yoksa senin mesajına), bildirim sesi olmadan ve yalnızca oturumun kendi sohbetinde. Görev sürerken çalışma kartı `📎 2 dosya gönderiliyor` gösterir.
- Her dosya adına göre değil içeriğine göre gider: bir resim, Telegram'ın fotoğraf sınırlarına uyuyorsa fotoğraf olarak (en çok 10 MB, genişlik artı yükseklik en çok 10.000, uzun kenar kısa kenarın en çok 20 katı), uymuyorsa dosya olarak; GIF animasyon olarak; MP4 video olarak; MP3 ya da M4A ses olarak; geri kalan her şey dosya olarak. Claude düz bir ek isterse her şey dosya olarak gider.
- 2 ile 10 arası fotoğraf ve video tek albüm olarak gider; dosyalar dosyalarla, sesler seslerle gider; animasyon tek başına gider. Daha fazlası eşit bölünür (11 tanesi 6 ve 5 olarak gider) ve hiçbir albüm 50 MB'ı geçmez.
- Açıklama `💬 <session>` olur; Claude'un kendi açıklaması ilk mesajın bir sonraki satırındadır, en çok 1.024 karakter.
- Her dosya önce denetlenir, owner dahil herkes için ([güvenlik](security.md#files-claude-sends)): aipager'ın ayarları, Claude'un kimlik bilgileri, anahtarlar ve gizli bilgiye benzeyen dosyalar asla gönderilmez; dosya normal bir dosya olmalı, boş olmamalı ve en çok 50 MB olmalı; bir üye yalnızca sohbetin [kök klasörü](groups.md#root-folders) içindeki dosyaları gönderir. Bir isteğin bir dosyası reddedilirse o istekten hiçbir şey gönderilmez: Claude nedenini öğrenir ve sana söyler, görev de devam eder.
- aipager, Claude Code dosyayı okuduğu anda aldığı özel bir kopyayı gönderir; kopya `~/.local/state/aipager/outbox/` içinde tutulur ve gönderilince ya da bir gün sonra silinir.
- aipager'ın kendine ait bir sayı sınırı yoktur: gönderimler Telegram'ın hız sınırlarına göre ayarlanır, bir albüm her dosya için bir sayılır. Sohbet susturulmuşken dosyalar bekler ve cevabın ardından gelir.
- Gönderilemeyen bir dosya sohbette bir satır alır, örneğin `⚠️ report.pdf gönderilemedi: Telegram dosyayı kabul etmedi.` Gönderilen, reddedilen ya da başarısız olan her dosyanın [denetim kaydında](security.md#audit-log) kendi kaydı vardır.

### <a id="voice"></a>Sesli mesajlar

Sesli mesajlar `faster-whisper` (`aipager[voice]` paketi) üzerinden geçer. Ses bu bilgisayarda metne çevrilir, `🎙️ Duyulan: …` olarak geri döner ve sen yazmışsın gibi, `/new` için ya da bekleyen bir yeniden adlandırma için bir ad olarak da, yazılır. Telegram'ın botlar için 20 MB indirme sınırını aşan bir sesli mesaj reddedilir. Sonra ne olduğu için bkz. [Hooks → UserPromptSubmit](hooks.md#userpromptsubmit).

---

## <a id="command-line-every-command"></a>Komut satırı: tüm komutlar

Aynı listeyi kendi bilgisayarında görmek için `aipager <command> --help` çalıştır.

| Komut | Ne yapar |
|---|---|
| `aipager config` | Etkileşimli kurulum sihirbazı: bot token'ı, sohbetler, üyeler ve roller, Claude Code hook'ları. Herhangi bir şeyi değiştirmek için yeniden çalıştır (bkz. [ekip modu](groups.md#setup)). |
| `aipager setup` | Aynı ilk kurulum, soru sormadan, betikler ve kodlama ajanları için (bkz. [aşağısı](#command-line-aipager-setup-for-coding-agents)). |
| `aipager start` | aipager'ı ön planda çalıştırır. Başka bir aipager çalışırken başlamayı reddeder. |
| `aipager service install [--yes]` | aipager'ı bir arka plan hizmeti olarak kurar (Linux'ta systemd kullanıcı hizmeti, macOS'ta launchd ajanı) ve başlatır. `--yes`, kurulu hizmet farklıyken soruyu atlar. |
| `aipager service start\|stop\|status\|logs\|uninstall` | Hizmeti başlatır, durdurur ya da gösterir, günlüğünü izler ya da kaldırır. |
| `aipager status [--json]` | aipager ve her oturum tek bakışta: durum, model, bağlam, maliyet ve Telegram sınırlarının durumu. |
| `aipager logs [-f] [-n LINES]` | aipager'ın günlüğü (Linux'ta journald, macOS'ta launchd günlüğü): son 100 satır ya da `-n` satır; `-f` onu izler. |
| `aipager doctor [--fix] [--safety-check] [--json]` | Sağlık kontrolleri (bkz. [sorun giderme](troubleshooting.md#aipager-doctor-check-list)). `--fix`, Claude girişini ve belirsiz bir `claude` kurulumunu, önce sorarak onarmayı önerir; `--safety-check` geçerli güvenlik kurallarını yazdırır; `--json` betikler içindir (bkz. [aşağısı](#aipager-doctor---json)). |
| `aipager session <name> [claude args]` | Geçerli klasörde dtach altında `<name>` adlı bir Claude Code oturumu başlatır ya da çalışıyorsa ona bağlanır. Addan (ya da `--` işaretinden) sonraki argümanlar `claude` programına olduğu gibi gider, örneğin `--resume`. Çalışan bir aipager gerektirir. |
| `aipager session ls [-a] [--json]` | Oturumları listeler; `-a` sona erenleri de katar. `aipager session list` de olur. |
| `aipager session kill <name> [-y]` | `-y` verilmediyse sorduktan sonra bir oturumu sonlandırır. |
| `aipager resume [name]` | Sona ermiş bir oturumu konuşmasıyla geri getirir; ad olmadan seçtirir. |
| `aipager miniapp enable [--port PORT] [--url URL]` | Mini App'i `127.0.0.1:PORT` üzerinde (varsayılan 8765), yönetilen tünelin arkasında ya da kendi `https://` adresinde açar. aipager'ın bir sonraki yeniden başlatılmasından itibaren geçerlidir. |
| `aipager miniapp disable\|status` | Onu kapatır (bir sonraki yeniden başlatmadan itibaren) ya da ayarlarını ve adresini gösterir. |
| `aipager policy validate` | `policy.yaml` (ve `policy.d`) dosyasını kontrol eder; bir sorunda sıfır olmayan bir kodla çıkar. |
| `aipager update` | aipager'ı sahibi olan kurucuyla günceller (uv, pipx, Homebrew, pip). aipager'ı asla yeniden başlatmaz; onu kendin yeniden başlat ya da bunu yapabilen Telegram'daki `/update` komutunu kullan (bkz. [Güncelleme](#update)). |
| `aipager report [--note TEXT]` | Bir sorun raporu gösterir ve evet dersen gönderir (bkz. [aşağısı](#command-line-aipager-report)). |
| `aipager uninstall [-y]` | aipager'ı ve yapılandırmasını durdurup kaldırır, verilerini korur (bkz. [aşağısı](#command-line-aipager-uninstall)). |
| `aipager version`, `aipager --version` | Sürümü yazdırır. |
| `aipager help [command]` | aipager ya da bir komut için yardım. |

## <a id="command-line-aipager-setup-for-coding-agents"></a>Komut satırı: `aipager setup` (kodlama ajanları için)

Yukarıdaki bölümler Telegram botunu anlatır. Bu bölüm bir terminalde çalıştırdığın ya da bir betiğin veya kodlama ajanının senin için çalıştırdığı bir komutu anlatır: `aipager setup`, aipager'ı soru sormadan kurar ve `aipager config` komutunun ilk çalıştırmasıyla aynı sonucu verir.

```sh
# 1. kişinin Telegram kimliğini öğren (botu açıp Start'a dokunur)
aipager setup detect-chat --token-file ~/bot-token.txt --json
# 2. kişi kendisi olduğunu onayladıktan sonra aipager'ı kur
aipager setup --token-file ~/bot-token.txt --chat-id 123456789 --service --json
# 3. kurulumu kontrol et
aipager doctor --json
```

Ne yaptığı, sırasıyla: bot token'ını okur, onu Telegram'da doğrular (getMe), hiçbir şey yazmadan bu bilgisayardaki her şeyi kontrol eder (mevcut yapılandırma, bağımlılıklar, `~/.claude/settings.json`, bir aipager'ın çalışıp çalışmadığı), kişiye bir test mesajı gönderir ("aipager is set up for you. ...") ve ancak ondan sonra yazar. Yazılan dosyalar, tam olarak sihirbazın yazdığı gibi:

- `~/.config/aipager/aipager.yaml` (izinler 0600): `bot_token` ve tek üyesi bu kişi olan bir özel sohbet (`label: owner`, owner ya da admin rolü).
- `~/.claude/settings.json`: aipager'ın hook'ları ve statusLine (mevcut dosya değiştiğinde `settings.json.bak.<time>` olarak saklanır).
- Biri sahip olduğunda denetim kaydı bir `grant-owner` girdisi alır.
- `--service` ile arka plan hizmeti (`aipager service install --yes` ile aynı).
- Yalnızca `config.env` olan eski bir kurulumda (henüz `aipager.yaml` yok), sihirbazın yaptığı taşıma: bir `config.env.bak.<time>` kopyası (ve bir `team.yaml` varsa `team.yaml.bak.<time>`) ve yoksa bir `~/.config/aipager/policy.yaml` şablonu. `config.env` dosyasının kendisi olduğu gibi kalır.

Bir kontrol başarısız olursa hiçbir şey gönderilmez ve hiçbir şey yazılmaz. Bir aipager zaten çalışıyorsa bir sohbet ya da rol değişikliği anında yeniden yüklenir; yeni bir bot token'ı, setup'ın asla kendisinin yapmadığı bir yeniden başlatma (`aipager service stop`, sonra `aipager service start`) gerektirir ve `--service` atlanır. setup bir aipager'ın çalışıp çalışmadığını belirleyemezse çalışıyormuş gibi davranır (`daemon_unknown` uyarısı): o zaman hizmeti asla kurmaz, bu yüzden çalışan bir aipager'ı asla yeniden başlatamaz, bir değişiklikten sonra yeniden yükleme göndermez ve bunun yerine `restart_needed: true` bildirir.

### <a id="aipager-setup-flags"></a>`aipager setup` seçenekleri

| Seçenek | Anlamı |
|---|---|
| `--token-file PATH` | Bot token'ını bu dosyadan okur (en fazla 4096 bayt; token'ın etrafındaki metin sorun değil). Diğer kullanıcıların okuyabildiği bir dosya çalışır ama `token_file_shared` uyarısı verir: ona `chmod 600` uygula ya da setup başarılı olunca sil. |
| `--token-stdin` | Bot token'ını stdin'den okur (bir pipe ya da yönlendirilmiş bir dosya, asla bir terminal). Pipe kapanmalıdır: setup stdin'in sonunu en fazla 30 saniye bekler, sonra 2 ve `token_stdin_timeout` ile çıkar. `printf '%s\n' "$TOKEN" \| aipager setup --token-stdin ...` ve `< token.txt` ikisi de onu kapatır. |
| `--chat-id N` | Zorunlu. Kişinin sayısal Telegram kullanıcı kimliği; bu aynı zamanda özel sohbetinin kimliğidir. Gruplar `aipager config` ile eklenir. |
| `--role owner\|admin` | Kişinin rolü (varsayılan `owner`). |
| `--service` | Arka plan hizmetini de kurar ve başlatır (bir aipager çalışırken atlanır). |
| `--force` | Mevcut bir kurulumun bot token'ının, sahibin özel sohbetinin ya da rolünün değiştirilmesine izin verir. Grup sohbetleri ve diğer özel sohbetler korunur. |
| `--dry-run` | Her kontrolü çalıştırır, hiçbir şey göndermez, hiçbir şey yazmaz ve neyin değişeceğini bildirir. |
| `--json` | stdout'a tam olarak bir JSON nesnesi yazdırır; geri kalan her şey stderr'e gider. |

`--token-file` ve `--token-stdin` seçeneklerinden tam olarak birini ver. Token asla bir seçeneğin değeri olarak kabul edilmez: `--token` ve `--bot-token` reddedilir (`token_on_command_line`) ve değer gösterilmez. Kısaltılmış seçenekler (`--token-f`, `--dry`) kabul edilmez. setup asla soru sormaz, asla terminal gerektirmez ve token'ı asla bir ortam değişkeninden okumaz.

Aynı token, sohbet ve rolle yeniden çalıştırmak hiçbir şeyi değiştirmez ve bunu söyler (`unchanged`, test mesajı yok). Mevcut bir kurulumda farklı bir token, sohbet ya da rol, sen `--force` ekleyene kadar 6 ile çıkar. `config.env` olan eski bir kurulum önce sihirbazın yaptığı gibi taşınır (`admin` rolünü alır, bu yüzden üzerine `--role owner` için `--force` gerekir).

### <a id="aipager-setup-detect-chat"></a>`aipager setup detect-chat`

```sh
aipager setup detect-chat (--token-file PATH | --token-stdin) [--timeout SECONDS] [--json]
```

Bota gelen en yeni özel mesajı bekler (Start'a dokunmak bir tane gönderir) ve göndereni yazdırır: kimlik, ad ve kullanıcı adı. Hiçbir şey kaydetmez, bu yüzden önce bulunan kişiyi göster ve `aipager setup --chat-id` komutunu ancak kişi onaylandıktan sonra çalıştır. Yalnızca komut başlamadan en fazla 10 dakika önce gönderilmiş mesajlar sayılır. `--timeout` 0 ile 3600 saniye arasıdır (varsayılan 300; 0 bir kez bakmak demektir). Telegram'ı hiçbir mesajı onaylamadan okur, bu yüzden `/start` sihirbaz ve aipager için kullanılabilir kalır.

Aynı bot için bir aipager çalışıyorsa botun mesajlarını aipager alır ve henüz yapılandırılmamış kişilerden gelen özel mesajları kaydetmez. detect-chat o zaman 9 ile çıkar (`source: "daemon"`): aipager'ı durdur (`aipager service stop`) ve komutu yeniden çalıştır ya da kişiye sayısal kimliğini sor. Başka bir bot için çalışan bir aipager varken Telegram'ı doğrudan okur (`source: "telegram"`).

Sınır: detect-chat hiçbir mesajı onaylamadığı için Telegram ona yalnızca son 24 saatin en eski 100 okunmamış güncellemesini gösterir. Botun bu kadar bekleyeni varsa yeni bir `/start` görünmez: detect-chat o zaman zaman aşımıyla (çıkış 8) `updates_backlog` uyarısıyla biter ve Start'a yeniden dokunmak işe yaramaz. Bunun yerine kişiye sayısal Telegram kimliğini sor (ya da bu güncellemeleri okuyup temizleyen aipager'ı bir kez başlat).

### <a id="exit-codes"></a>Çıkış kodları

| Kod | `error` | Anlamı |
|---|---|---|
| 0 | | Başarı (`installed`, `updated`, `unchanged`, `dry_run` ya da detect-chat `found`). |
| 1 | `config_malformed`, `ambiguous_install`, `settings_invalid`, `test_send_failed`, `write_failed`, `service_failed`, `internal_error` | Başarısızlık. `ambiguous_install`: birden fazla özel sohbet sahibin sohbeti olabilir; bunu `aipager config` ile düzelt. |
| 2 | `usage`, `missing_token_source`, `token_source_conflict`, `token_file_unreadable`, `token_stdin_is_tty`, `token_stdin_timeout`, `token_malformed`, `token_on_command_line`, `bad_chat_id`, `bad_role`, `bad_timeout` | Hatalı ya da eksik girdi; `fix` seçeneği söyler. Bilinmeyen bir seçeneği ya da `setup` sonrasındaki yanlış bir sözcüğü argüman ayrıştırıcısı bildirir (düz metin, JSON değil), yine 2 ile. |
| 3 | `deps_missing` | dtach, claude, aipager-hook ya da aipager-statusline eksik; `fix` komutları içerir. Hiçbir şey yazılmadı. |
| 4 | `token_rejected` | Telegram token'ı reddetti. |
| 5 | `chat_not_started`, `bot_blocked` | Kişi botta Start'a dokunmadı ("Open t.me/<bot> and press Start, then run this again.") ya da onu engelledi. Hiçbir şey yazılmadı. |
| 6 | `existing_install` | Farklı bir token, sohbet ya da rol zaten yapılandırılmış; `--force` ekle. |
| 7 | `telegram_unreachable` | Ağ hatası ya da Telegram 429 veya 5xx ile yanıt verdi. |
| 8 | `detect_timeout` | detect-chat `--timeout` dolmadan özel bir mesaj görmedi (100 eski okunmamış güncelleme daha yenilerini gizlediğinde `updates_backlog` uyarısıyla). |
| 9 | `daemon_running` | detect-chat: botun mesajlarını çalışan bir aipager alıyor. |
| 10 | `updates_conflict` | detect-chat: bu botun mesajlarını başka bir program okuyor. |
| 130 | `interrupted` | Ctrl-C. |

Birkaç kontrol aynı anda başarısız olursa kod şu sırayı izler: `config_malformed`, `ambiguous_install`, `existing_install`, `settings_invalid`, `deps_missing`. `--dry-run`, gerçek çalıştırmanın bu kontrollere göre alacağı kodla çıkar.

### <a id="json-aipager-setup---json"></a>JSON: `aipager setup --json`

Her anahtar her zaman vardır; henüz bilinmeyen bir değer `null` olur.

```json
{
  "command": "setup",
  "status": "installed",
  "ok": true,
  "exit_code": 0,
  "error": null,
  "message": "aipager is set up for @example_bot (chat 123456789, role owner). ...",
  "fix": null,
  "dry_run": false,
  "bot_username": "example_bot",
  "chat_id": 123456789,
  "role": "owner",
  "changed": ["bot_token", "owner_dm", "settings_json"],
  "test_message": "sent",
  "deps": {"ok": true, "items": [{"name": "dtach", "found": true, "path": "/usr/bin/dtach", "fix": null}]},
  "settings_json": {"path": "/home/you/.claude/settings.json", "status": "created", "backup": null, "repointed": 0},
  "daemon": {"running": false, "reload": "not_needed", "restart_needed": false},
  "service": {"requested": false, "result": "not_requested"},
  "warnings": [],
  "next_step": "Start aipager with `aipager service install` ..."
}
```

- `status`: `installed`, `updated`, `unchanged`, `dry_run` ya da `error`.
- `changed`: neyin değiştiği (ya da bir deneme çalıştırmasında neyin değişeceği), şu sırayla: `migrated_v1`, `bot_token`, `owner_dm`, `role`, `settings_json`, `service`.
- `test_message`: `sent`, `not_needed`, `skipped_dry_run`, `failed` ya da `not_attempted`.
- `settings_json.status`: `created`, `patched`, `unchanged`, `would_change` ya da `not_checked`.
- `daemon.reload`: `reloaded`, `refused`, `not_reloaded` ya da `not_needed`. `restart_needed`, bir aipager çalışırken yeni bir token'dan sonra ve aipager yeniden yüklenemediğinde ya da setup onun çalışıp çalışmadığını belirleyemediğinde her değişiklikten sonra true olur.
- `service.result`: `not_requested`, `installed`, `already_installed`, `skipped_daemon_running`, `would_install`, `failed` ya da setup daha önce durduysa `null`.
- `warnings`: `code` (`token_file_shared`, `reload_refused`, `daemon_unknown`, `audit_write_failed`) ve `message` içeren nesneler. `audit_write_failed`: sahip rolü yazıldı ama `grant-owner` girdisi denetim kaydına eklenemedi; bu girdi elden geldiğince yapılır, bu yüzden çıkış kodu 0 kalır.
- Yeniden yüklenemeyen bir aipager çalışırken yapılan bir değişiklikten sonra `next_step` onu yeniden başlatmanı söyler (`aipager service stop`, sonra `aipager service start`); bot token'ından yalnızca token değiştiyse söz eder.
- `daemon_unknown` uyarısıyla `next_step` asla aipager'ın çalıştığını söylemez. setup'ın bunu belirleyemediğini söyler, `aipager doctor --json` komutuna yönlendirir ve çalışmıyorsa aipager'ı `aipager service install` ya da `aipager start` ile başlatmanı söyler. Bir yeniden başlatma gerekecek olsaydı onu yalnızca bir aipager çalışıyorsa ister, aksi halde onu aynı şekilde başlatmanı söyler.
- Bir hatada `status` değeri `error` olur ve `error`, `message` ile `fix` neyin ters gittiğini ve ne yapılacağını söyler; `next_step` `fix` değerini tekrarlar. Hata bir yazmadan sonra olduysa `changed` neyin yazıldığını listeler ve normal çıktı stderr'e bir satır ekler: `Already written: aipager.yaml, ... (changed: ...)`.

### <a id="json-aipager-setup-detect-chat---json"></a>JSON: `aipager setup detect-chat --json`

```json
{
  "command": "detect-chat",
  "status": "found",
  "ok": true,
  "exit_code": 0,
  "error": null,
  "message": "Found Ada L (@ada), Telegram id 123456789.",
  "fix": null,
  "bot_username": "example_bot",
  "source": "telegram",
  "candidate": {"id": 123456789, "first_name": "Ada", "last_name": "L", "username": "ada", "date": 1700000000},
  "other_candidates": 0,
  "warnings": [],
  "next_step": "Show this person to the user; once they confirm, run: aipager setup --token-file <path> --chat-id 123456789"
}
```

`status` değeri `found` değilse `candidate` değeri `null` olur. `warnings`, `code` (`token_file_shared`, `updates_backlog`) ve `message` içeren nesneler barındırır. `other_candidates`, aynı 10 dakika içinde bota yazan diğer kişileri sayar: 0 değilse doğru kişiyi seçtiğinden emin ol. `next_step` kullandığın token seçeneğini tekrarlar, token'ın kendisini asla.

### <a id="aipager-doctor---json"></a>`aipager doctor --json`

`aipager doctor` kontrolleri, stdout'ta tek bir JSON nesnesi olarak. Sade `aipager doctor` gibi yalnızca okur ve Telegram mesajı göndermez. Bir kontrol başarısız olursa çıkış 1, aksi halde 0. `--fix` ya da `--safety-check` ile birleştirilemez (bir hata nesnesiyle çıkış 2).

```json
{
  "command": "doctor",
  "version": "1.0.1",
  "ok": true,
  "summary": {"ok": 14, "warn": 1, "fail": 0},
  "checks": [{"key": "token_valid", "status": "ok", "title": "Telegram bot token", "detail": ["@example_bot"], "fix": null}]
}
```

`checks` şu sırayla, şu anahtarlarla gelir: `config_parses`, `config`, `token_valid`, `chat_reachable`, `team`, `role_shell_access`, `peer_messages`, `claude`, `claude_auth`, `dtach`, `hook_scripts`, `settings_json`, `daemon`, `service_installed`, `service_unit_path`, `miniapp`. Çöken bir kontrol anahtarını `warn` durumuyla korur. Metinler sadedir (biçimlendirme yok) ve bot token'ına benzeyen her şey `<redacted>` ile değiştirilir.

## <a id="command-line-aipager-uninstall"></a>Komut satırı: `aipager uninstall`

`aipager uninstall`, aipager'ı durdurur ve hizmetini kaldırır, yapılandırma klasörünü (`~/.config/aipager`: bot token'ı, sohbetler ve ayarlar) ve oturum listesini (`~/.claude/aipager-sessions.json`) siler, aipager'ın `/tmp` içindeki dosyalarını temizler ve aipager'ın kendisini kaldırır. Önce sorar (`-y` soruyu atlar).

Yeniden kurulum kaldığı yerden devam etsin diye aipager'ın verilerini korur: `~/.local/share/aipager` (oturum klasörleri, indirilen yardımcı programlar, kilit ve işaret dosyaları), `~/.local/state/aipager` (sorun raporlarının kayıtları ve dillerin durumu), `~/.claude/aipager-audit.jsonl` (izin yanıtlarının kaydı), `~/.claude/aipager-flood-state.json` (Telegram sınırlarının geçmişi), `~/.claude/aipager-pending-users.json` (botu erişimi olmadan deneyen kişiler) ve `~/aipager-users` (üyelerin [kök klasörleri](groups.md#root-folders), çalışmalarıyla birlikte). Sormadan önce bunlardan hangilerinin bilgisayarında olduğunu listeler ve sonunda bunları da kaldıran `rm -rf` komutunu yazdırır.

Ayrıca aipager'ın kendi girdilerini Claude Code'un `~/.claude/settings.json` dosyasından çıkarır: `aipager-hook` çalıştıran hook'lar ve `aipager-statusline` çalıştıran durum satırı (kendi durum satırın kalır). Bu dosyadaki diğer her şey olduğu gibi kalır ve dosyanın önceki hali yanında `settings.json.bak.<time>` olarak saklanır. Dosya başka bir dosyaya bağlantıysa, okunamıyorsa, geçerli JSON değilse ya da Claude Code'un okuduğu biçimde değilse tam olarak olduğu gibi kalır ve uninstall hangi girdilerin elle kaldırılacağını söyler. Hâlâ `aipager-hook` ya da `aipager-statusline` adını geçiren kendi hook'un da (örneğin bir `aipager-hook-capped.sh` sarmalayıcısı) kalır ve uninstall onun adını verir. Zaten açık olan Claude Code oturumları, sen onları yeniden başlatana kadar yükledikleri hook'ları çalıştırmaya devam eder. `aipager config`, yeniden kurulumdan sonra girdileri yeniden ekler. Telegram botuna ve daha eski `settings.json.bak.*` yedeklerine dokunulmaz; `skipDangerousModePermissionPrompt` ayarına da (Claude Code'un izin kontrolleri olmadan çalışmadan önceki uyarısını atlayan ayar): onu aipager ayarlar, ama sen de ayarlamış olabilirsin.

## <a id="command-line-aipager-report"></a>Komut satırı: `aipager report`

`aipager report`, botun Sorun bildir düğmesiyle aynı türden bir sorun raporunu aipager'ın bu bilgisayarda kaydettiklerinden derler (çalışan aipager hakkında daha az bilgiyle: mod, sohbetler, özellikler, çalışma süresi, oturumlar ve Claude Code ayrıntıları bilinmiyor ya da boş kalır), onu bütünüyle yazdırır ve `Send this report? [y/N]` diye sorar. Yalnızca `y` ya da `yes` onu gönderir; başka her şey ya da hiç terminal olmaması (bir betik, bir pipe, bir Claude Code oturumunun shell'i) hiçbir şey göndermez. `--note "text"`, yazdırılan raporda görünen en fazla 500 karakterlik bir not ekler. Rapor gönderildiğinde, senin seçiminle gönderilmediğinde ya da yalnızca yazdırıldığında 0 ile, onayladığın bir gönderim gitmediğinde 1 ile çıkar. Bir raporun neler içerdiği ve nereye gittiği için bkz. [Sorun raporları](problem-reports.md).

## <a id="settings-in-the-environment"></a>Ortamdan gelen ayarlar

Birkaç ayar `aipager.yaml` yerine aipager'ın ortamından okunur. systemd hizmeti için onları `systemctl --user edit aipager` ile `[Service]` altına ekle (`Environment=STALE_BUSY_TIMEOUT=900`), sonra `aipager service stop` ve `aipager service start`; macOS'taki launchd hizmeti için plist'in `EnvironmentVariables` bölümüne ekle (`~/Library/LaunchAgents/com.aipager.daemon.plist`; `aipager service install` bu dosyayı yeniden yazar, bu yüzden sonra onları yeniden ekle); elle başlattığın bir aipager için onları `aipager start` öncesinde ayarla. Normal kullanım için hiçbiri gerekli değildir.

| Ayar | Varsayılan | Neyi değiştirir |
|---|---|---|
| `AIPAGER_WHISPER_MODEL` | `base` | Sesli mesajlar için konuşma tanıma modeli (`tiny`, `base`, `small`, `medium`, ...): büyüğü daha doğru ve daha yavaştır. |
| `MINIAPP_PORT` | `8765` | Mini App'in `127.0.0.1` üzerindeki portu (`aipager miniapp enable --port` bunun yerine onu kaydeder). |
| `MINIAPP_ENABLED` | `aipager.yaml` dosyasından | `0` Mini App'i kapatır, `1` açar; `aipager.yaml` ne derse desin (`aipager miniapp enable\|disable` bunun yerine seçimi kaydeder). |
| `MINIAPP_PUBLIC_URL` | yok | Mini App'i yönetilen tünel yerine kendi `https://` adresinde sunar ([güvenlik modeli](security.md#mini-app-tunnel)). |
| `KEEP_FINISHED_CARD` | `1` | `0`, `/settings` içinde hiç görünüm seçmemiş sohbetler için görünümü “Yalnızca yanıt (kartsız)” yapar. |
| `STALE_BUSY_TIMEOUT` | `7200` (2 saat) | Çalışan bir oturumun, sohbet “hâlâ çalışıyor (120 dk'dır haber yok)” notunu almadan önce haber vermeden kalabileceği saniye. |
| `PROMPT_HOOK_GRACE_SECONDS` | `8` | Claude Code'un hiç almadığı bir mesajın “Claude Code mesajı almadı” olarak işaretlenmesinden önceki saniye ([sıradan mesajlar](#text)). |
| `FINISH_CARD_GRACE_SECONDS` | `0.8` | Kartlı görünümde biten kartın yanıtın ne kadar önünde geldiği; `0` ikisini aynı anda gönderir. |
| `GONE_SESSION_MAX_AGE_DAYS` | `14` | Sona ermiş bir oturumun `/resume` içinde kaldığı gün; `0` onu tutar. |
| `STREAM_EDIT_INTERVAL`, `BUSY_EDIT_INTERVAL` | `1.2`, `3.0` | Metin akarken ve sakinken çalışma kartının güncellenmesi, saniye olarak; Telegram'ın sohbet başına sınırı yine önceliklidir ([sorun giderme](troubleshooting.md#busy-cards-got-slower)). |
| `TYPING_INDICATOR_INTERVAL` | `4.5` | “yazıyor…” balonları arasındaki saniye; `0` onları kapatır. |
| `TELEGRAM_MAX_RETRY_AFTER` | `90` | aipager'ın bir sınırı engelleme saymaya başladığı, saniye cinsinden Telegram beklemesi (düşürme; bkz. [sorun giderme](troubleshooting.md#the-bot-went-quiet-flood-control)). |
| `CLAUDE_RICH_SUMMARIES` | `1` | `0`, kod bloklu yanıtların zengin biçimlendirmesini kapatır (eski sürümlerden kalma bir geçici çözüm). |
| `AIPAGER_INTERACTIVE_TIMEOUT` | `300` | Bir izin isteğinin ya da sorunun, aipager kayıtlı sohbete bakmadan önce Claude Code'dan haber almadan bekleyebileceği saniye; oturum yalnızca sohbet isteğin bittiğini gösterirse yeniden çalışıyor sayılır ([sorun giderme](troubleshooting.md#a-permission-prompt-with-no-news-for-5-minutes)). |
| `AIPAGER_SUBAGENT_SILENCE` | `1800` | Durması görülmemiş bir alt ajanın kaldırılmasından önceki sessizlik saniyesi ([hooks](hooks.md#subagentstart--subagentstop)). |
| `AIPAGER_BG_SHELL_MAX_TRACK` | `7200` | Sonu görülmemiş bir arka plan komutunun artık sayılmamasından önceki saniye. |
| `AIPAGER_REPORT_PROMPTS` | açık | `0`, otomatik sorun raporu tekliflerini kapatır ([sorun raporları](problem-reports.md#when-aipager-offers-a-report)). |
| `AIPAGER_CLAUDE_BIN` | yok | Birkaç tane kuruluyken çalıştırılacak `claude` (`aipager.yaml` içindeki `claude_path` önceliklidir). |
| `AIPAGER_SOCKET_PATH` | `$XDG_RUNTIME_DIR/aipager.sock` | aipager'ın kontrol soketi. aipager'ın başlattığı oturumlar onu hook'larına geçirir. |
| `AIPAGER_DEBUG` | kapalı | Bir oturumun ortamında `1`, `aipager-hook` ve `aipager-statusline` programlarının tanılarını stderr'e yazdırmasını sağlar. |
| `AIPAGER_INSTANCE_DIR`, `AIPAGER_TELEGRAM_API_BASE` | yok | Testler için ikinci, yalıtılmış bir aipager ([mimari](architecture.md#isolated-instance-for-testing)). |

## <a id="see-also"></a>Ayrıca bkz.

- [Mimari](architecture.md): botun yeri.
- [Hook olayları](hooks.md): aipager'ın claude'dan geri ne duyduğu.
- [Sorun giderme](troubleshooting.md): komutlar tuhaf davrandığında.
