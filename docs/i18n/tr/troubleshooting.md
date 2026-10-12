# Sorun giderme

<!-- i18n-source: docs/troubleshooting.md @ 7e36944 -->

> Bu sayfa İngilizceden çevrildi; ikisi birbirini tutmazsa İngilizce sürüm geçerlidir. ([English](../../troubleshooting.md))

Sık karşılaşılan arızalar ve nasıl düzeltileceği. Emin değilsen şunu çalıştır:

```sh
aipager doctor
```

Aşağıdaki kontrollerin hepsini sırayla çalıştırır ve her arıza için önerilen çözümü yazdırır. Kaynak: `aipager/doctor.py`.

## <a id="another-aipager-daemon-already-owns-the-socket"></a>“Another aipager daemon already owns the socket”

İki aipager aynı kontrol soketini paylaşamaz. Bu hata, bir aipager'ın hâlâ çalıştığı ve o sokette yanıt verdiği anlamına gelir (soket `$XDG_RUNTIME_DIR/aipager.sock` ya da `$XDG_RUNTIME_DIR` ayarlı değilse `/tmp/aipager.sock`). Çöken bir aipager'dan kalan bir soket dosyası sorun değildir: sonraki başlangıç onu siler ve yenisini oluşturur. Önce çalışan aipager'ı durdur:

```sh
aipager service stop          # hizmet olarak çalışıyorsa
pkill -f 'aipager start'      # aksi halde
```

Soket kontrolü onu fark etmezse `aipager start` yine de `aipager is already running (pid=N)` ile reddeder ve kilit dosyasının adını verir (`~/.local/share/aipager/daemon.lock`): o süreci de aynı şekilde durdur.

## <a id="telegram-bot-doesnt-respond"></a>Telegram botu yanıt vermiyor

1. `aipager status`: aipager satırında `not running` yazıyorsa onu başlat.
2. `aipager doctor`: `token_valid` ve `chat_reachable` kontrolleri Telegram API'sine baştan sona gider ve tam hatayı gösterir.
3. Yanlış sohbet: `aipager config` çalıştır ve **Test bot reachability** seç. Yanlış bir özel sohbeti düzeltmek için doğrusunu **Add a DM scope** ile ekle (bkz. [kimlikleri bulmak](groups.md#finding-ids-auto-detect)), sonra yanlış olanı **Edit a scope** içinden kaldır.
4. Bota hiç `/start` gönderilmemiş: Telegram'da botu aç ve bir kez Start'a dokun.

## <a id="busy-cards-got-slower"></a>Çalışma kartları yavaşladı

Telegram bir sohbete saniyede yaklaşık bir mesaja (ve bir gruba dakikada 20 mesaja) izin verir. Her mesaj ve her düzenleme sayılır. aipager her sohbete, ani artışlar için küçük bir payla saniyede yaklaşık 1 çağrılık kendi bütçesini verir ve o sohbetin çalışma kartları bu bütçeyi **paylaşır**. “yazıyor…” göstergesi de bu bütçeye, sohbetin gönderdiği **en önemsiz şey** olarak dahildir (aşağıda “Yazıyor balonu” bölümüne bak).

Önce balonun payı ayrılır (sohbetin süren en eski görevi on dakikadan gençken her 4,5 saniyede bir çağrı, görev yaşlandıkça daha seyrek; bkz. “Yazıyor balonu”) ve kartlar kalanı paylaşır. Bu yüzden özel sohbetteki tek bir kart yaklaşık her 4,8 saniyede güncellenir; aynı sohbette iki oturum çalışırken her kart yaklaşık her 9,7 saniyede; üç oturumla yaklaşık her 14,5 saniyede. **Yavaş bir kart, işini yapan bütçedir, bir hata değil.** Yanıtlar, yanıt mesajları ve düğme tepkileri bir kart uğruna asla yavaşlamaz: ayrılmış bir jetonları vardır; çağrısı yetmeyen bir kart düzenlemesi ise senin yanıtının önünde sıraya girmek yerine atlanır ve bir sonraki taramada yeniden denenir.

Bir **grupta** sınır, kaç oturum olursa olsun dakikada 20 çağrıdır ve tek bir balon bunun üçte ikisini yerdi. Grubun kartları birlikte her 10 saniyede bir güncelleme hakkını korur (tek bir kart için 11 sn) ve balon kalanı alır: bu yüzden grupta zaman zaman bir iki saniyeliğine kaybolabilir.

Kartları hızlandırmak için: aynı sohbette daha az eşzamanlı oturum tut ya da en hareketli olanlara kendi sohbetlerini ver (`aipager config`). `STREAM_EDIT_INTERVAL` (varsayılan `1.2`, kart metin akıtırken hız) ve `BUSY_EDIT_INTERVAL` (varsayılan `3.0`, sakinken) değerlerini aipager'ın ortam değişkenleri olarak da ayarlayabilirsin (bkz. [ortam ayarları](commands.md#settings-in-the-environment)). Bunlardan birini sohbetin alt sınırının *altına* ayarlamak güvenlidir ve hiçbir şeyi değiştirmez: alt sınır kazanır ve ayarlarda hangi değer olursa olsun sohbeti Telegram sınırının altında tutan tam olarak budur.

### <a id="a-long-turns-card-refreshes-less-often"></a>Uzun bir görevin kartı daha seyrek güncellenir

Yukarıdaki hız görevin ilk iki dakikası için geçerlidir. Sonra kart **görev yaşlandıkça yavaşlar** ve 10 dakikadan itibaren saati, hiç donmuş görünmesin diye güncellemelerle aynı ritimde 30 saniyelik adımlarla ilerler:

| görevin yaşı | kart en sık şu aralıkla güncellenir | saat şunu gösterir |
|---|---|---|
| 0–2 dk | yukarıdaki gibi (özel sohbetteki tek bir kart için yaklaşık 4,8 sn) | `0:45` |
| 2–10 dk | 10 sn | `4:10` |
| 10–60 dk | 30 sn | `23:00`, `23:30` |
| 60 dk'dan fazla | 30 sn | `1:23:00`, `1:23:30` |

Saat 30 saniyelik adımına aşağı yuvarlanır ve güncellemeler en sık 30 saniyede bir, bazen biraz daha seyrek gelir. Bu yüzden kart arada bir adım atlar: 23 dk 29 sn'deki bir güncelleme `23:00` gösterir, sonraki 24 dk 1 sn'de gelirse `24:00` gösterir ve `23:30` atlanır.

Yeni bir araç satırı ya da yeni bir cümle bir sonraki güncellemeyi bekler; **durum değişikliği** (oturum çalışmaktan bir arka plan ajanını beklemeye geçer ve geri döner) 10 saniyede birden sık olmamak üzere hemen gösterilir. Canlı ajan satırları ve bekleme satırı aynı adımlarla ilerler. Biten bir kart görevin süresini gerçek saniyelerle gösterir (`23:17`).

Dört saatlik bir görevin tek bir mesajı binlerce kez düzenlemesini engelleyen budur: 2026-09-23'te bir kart tam olarak bunu yaptı ve sohbet yedi saat engellendi. Hızlı tempoyu tüm görev boyunca korumak için **`/settings` → ⏱ Uzun görevlerde kart güncellemeleri** ayarını kapat (sohbet için ya da Mini App'ten veya `👤 Her oturumun kendi ayarları` içinden tek bir oturum için).

### <a id="the-typing-bubble"></a>Yazıyor balonu

“yazıyor…” balonu, orada kaç oturum çalışırsa çalışsın sohbet başına **tek** bir döngü gönderir (tüm Telegram istemcilerinde balon sohbet başına tektir), her `TYPING_INDICATOR_INTERVAL` saniyede bir (varsayılan 4,5: Telegram yazıyor durumunu 5 saniyede kaldırır; `0` balonu kapatır). Sohbetin bütçesinde, sohbetin gönderdiği en önemsiz şey sayılır:

- sohbetin süren en eski görevinin yaşına göre **görev yaşlandıkça yavaşlar**: ilk on dakikada her 4,5 sn'de (balon sürekli yanar), sonra her 15 sn'de; Telegram onu 5 sn gösterir, bu yüzden eski bir görevin balonu yanık durmak yerine yanıp söner. 4,5 sn'de tek başına saatte 800 çağrı yapardı ve saatlik sınır (aşağıda) onu uzun süre kapatırdı; **⏱ Uzun görevlerde kart güncellemeleri** ayarını kapatmak onu 4,5 sn'de tutar;
- **her görevin en başından görünür**; genç bir kart ona yer açmak için biraz daha yavaş güncellenir: kartlar kalanı paylaşmadan önce balonun payı ayrılır (yukarıda), bu yüzden ikisi birlikte sohbetin sınırlarını asla aşmaz;
- bir kart düzenlemesinin ihtiyaç duyduğu jetonu asla almaz: bir kart düzenlemesinin hemen ardından gitmesi gereken balon yarım saniye bekler (`TYPING_RETRY_WAKE`) ve yeniden dener, böylece en fazla bir iki saniye gecikir ama asla kaybolmaz;
- sohbetin saatlik hacmi büyüdüğünde ilk o bırakılır (aşağıda);
- balonun kendisine gelen bir 429 yalnızca balonu, Telegram'ın istediği süre kadar engeller (kartlar çalışmaya devam eder), ama sohbetin altı saatlik uyarı rejimini başlatır (hız 0,5 çağrı/sn ile sınırlanır, bu yüzden kartlar da yavaşlar);
- balona gelen bir **engelleme** (aşağıdaki sınırın üstünde bir `retry_after`), sohbete gönderimi tam olarak başka herhangi bir çağrıdaki engelleme gibi kapatır: sohbete artık hiçbir şey gitmez ve görevin yanıtı bekletilir, engelleme kalkınca teslim edilir.

Eskiden bedavaydı (çalışan her oturum için bir döngü, hiçbir bütçenin dışında), ta ki 2026-09-23'te aynı özel sohbetteki iki oturum onu üç saatte yaklaşık 5.400 kez gönderip Telegram balonun kendisini sınırlayana kadar.

## <a id="the-bot-slowed-down-a-rate-limit-429"></a>Bot yavaşladı: istek sınırı (429)

Telegram yine de bir sohbeti sınırlarsa bir çağrıya `429` ve birkaç saniyelik bir `retry_after` ile yanıt verir. aipager tam bu süre boyunca o sohbete çağrı yapmayı bırakır, sonra ertelenen tek gönderimi yeniden dener ve **o sohbetin kart aralığını ikiye katlar** (×8'e kadar). Her sakin dakika onu yarıya indirerek normale döndürür.

Ne görürsün:

- `aipager status` (ve `aipager doctor` içindeki `aipager daemon` satırı) **`Telegram flood backoff ×4 (chat …), last 429 12 s ago`** gösterir.
- `aipager logs` içinde tam olarak bir satır, yığın izi olmadan: `flood: chat … 429 retry_after=5s → cadence ×2`.
- Hiçbir şey kapatılmaz ve **hiçbir yanıt kaybolmaz**: ertelenir, atılmaz.
- **Sohbetin kazanılmış gönderim hızı yarıya düşer** ve sohbet **altı saatlik bir uyarı rejimine** girer (`FLOOD_WARNING_HOURS`): hızı 0,5 çağrı/sn'nin (`FLOOD_WARNED_CEILING`) üstüne çıkamaz ve yavaşça geri yükselir. `aipager status` bunu gösterir: `Telegram chat 123: rate 0.25/s (ceiling 0.50/s), 212/1200 calls in the last hour (as of 18s ago), 7/30 in the last minute - warning regime, 5h 52m left`.

Bir şey yapmana gerek yok. Kendiliğinden geçer. Sohbet bir iki dakika sakin kalınca yavaşlatma satırı kaybolur; hız her sakin pencerede 0,1 çağrı/sn artar (genelde bir dakika, uyarı rejiminde 36 dakika) ve rejim bitince tam tavan geri gelir. Bu süre boyunca `aipager doctor` aipager satırını yeşil tutar: yavaşlayan bir sohbet normal çalışmadır. (Eskiden bir 429 yaklaşık beş dakika sonra unutuluyordu. 2026-09-23'teki engellemeden önceki tek Telegram uyarısı ondan 3 sa 23 dk önce gelmişti ve sohbet uyarıdan birkaç dakika sonra tam hıza dönmüştü.)

### <a id="the-hourly-budget"></a>Saatlik bütçe

Her sohbetin bir de kayan bir **saati** vardır: herhangi bir 60 dakikada en fazla `FLOOD_HOURLY_MAX` (1200) çağrı; yazıyor balonları, kart düzenlemeleri ve yanıtlar birlikte; yalnızca tepkiler hariç. Son 120 çağrı (`FLOOD_HOURLY_ESSENTIAL_RESERVE`) yanıtlar, yanıt mesajları ve istekler için ayrılmıştır:

- saatte 810 çağrıda yazıyor balonu duraklatılır (648'in altında geri gelir);
- 1080'de sohbet, saat 864'e inene kadar **minimal moda** girer (aşağıda);
- saatlik bütçe yanıtları asla reddetmez; bir gün 1200'ü aşarlarsa günlük bunu saatte bir kez söyler.

`aipager status`, sohbetin son saatteki çağrılarını bütçesine göre gösterir. Sayı, çağrılar sürerken aipager'ın en sık dakikada bir yeniden yazdığı bir durum dosyasından gelir, bu yüzden yanında yaşı belirtilir. Aynı özel sohbete saatlerce yazan iki etkin oturum bütçenin belirgin şekilde altında kalır (2026-09-23 olayı yeniden üretildiğinde: en yoğun saatte yaklaşık 900 çağrı, aynı iş için bütçesiz modelde yaklaşık 3.400'e karşı).

### <a id="the-earned-rate-and-why-your-cards-may-be-slower-than-they-were"></a>Kazanılmış hız ve kartlarının neden eskisinden yavaş olabileceği

0.7.13 sürümünden beri her sohbetin sürekli gönderim hızı **varsayılmaz, öğrenilir**. Telegram, botun için gerçekte geçerli olan sınırı yayımlamaz (hesabının yakın geçmişine bağlıdır), bu yüzden aipager her sohbete 0,5 çağrı/sn ile başlar, her sakin dakika için 0,1 ekler (1/sn tavanına kadar), bir 429'da yarıya indirir ve bir engellemeden sonra 0,05'e düşürür. Az önce engellenmiş bir bot, engellenmemiş olandan çok daha temkinli ilerler ve hızını sonraki saatlerde geri kazanır.

Bir sohbetin hızı 0,2 çağrı/sn'nin altına düşerse ya da saatlik bütçesinin kartlara ve balonlara ayrılan payı tükenirse **minimal moda** girer: çalışma kartları animasyonu bırakır ve hareketsiz tek bir satır gösterir, `⏳ x1 · çalışıyor (kart şimdilik güncellenmiyor)`; yazıyor balonu durur; sabitlenmiş durum satırı `⏸ kartlar şimdilik güncellenmiyor (Telegram'ın saatlik sınırı)` (ya da sohbeti oraya hız getirdiyse `(Telegram sınırı)`) gösterir ve bu geçene kadar başka değişmez. **Yanıtlar, yanıt mesajları ve izin istekleri gitmeye devam eder**: bütün mesele bu. Bu botun gönderdiklerinin yaklaşık %95'i kart, yaklaşık %5'i yanıttır, bu yüzden baskı altında işten değil piksellerden fedakârlık eder. `aipager doctor` minimal modu bir uyarı olarak gösterir, böylece duraklatılmış bir kart asla bozuk sanılmaz; hız düzelince kendiliğinden kalkar.

## <a id="the-bot-went-quiet-flood-control"></a>Bot sustu: Telegram engellemesi (flood control)

`TELEGRAM_MAX_RETRY_AFTER` değerinden (varsayılan 90 sn) büyük bir `retry_after` bir istek sınırı değil, bir **engellemedir** ve saatlerce sürebilir. Sohbetten bakınca bot ölmüş gibi görünür: mesajlar hâlâ Claude'a ulaşır, oturumlar çalışmaya devam eder, ama yanıt gelmez.

Ne görürsün:

- `aipager status` (ve `aipager doctor` içindeki `aipager daemon` satırı) **`Telegram flood-muted until HH:MM (chat …)`** gösterir.
- `aipager logs` içinde bir satır: `Telegram flood control — chat … muted for Ns (until HH:MM) …`, sonra o sohbet için sessizlik. Daha sonra bir satır `flood mute on chat … lifted`.
- `aipager status` ayrıca sohbetin tam durumunu gösterir: `Telegram chat 123: rate 0.05/s (ceiling 0.50/s), 431/600 calls in the last hour (as of 12s ago) - MINIMAL MODE, card updates paused - flood-muted until 09:41 - warning regime, 5h 59m left (1 ban(s) in the last 7 days)`.

aipager sohbete gönderimi tam olarak Telegram'ın istediği süre boyunca kapatır ve ona **hiçbir türden çağrı yapmaz**: yanıt yok, çalışma kartı düzenlemesi yok, ek yok, tepki yok, yazıyor göstergesi yok, düğmelere dokunduktan sonraki kısa bildirimler yok, komutlarının vereceği yanıtlar yok. Bunların her biri engellemeyi uzatan yeni bir ihlal olurdu; 2026-09-15'te süren bir engellemenin ortasındaki tek bir istek, 21 dakikalık bir engellemeyi 9,5 saatlik bir engellemeye çevirdi. Bu yüzden kapalıyken yazılan bir komut hiç yanıt almaz ve **bir düğmeye dokunmak da onaylanmaz**: 0.7.12'ye kadar Telegram'ın onu ayrı saydığı varsayımıyla kısa bildirim yine de gidiyordu. Hız için bu doğru; engellemeler için değil. Diğer sohbetler etkilenmez.

**Engelleme sırasında bota yazmak güvenlidir ve hiçbir şey kaybolmaz.** Sohbete gönderim kapalıyken sohbette yaptığın hiçbir şey botun Telegram'a çağrı yapmasına yol açmaz, bu yüzden engellemeyi uzatamaz:

- Gönderdiğin bir mesaj (metin, sesli mesaj, dosya, hızlı şablon) yine Claude'a ulaşır ve oturum onun üzerinde çalışır. Çalışma kartı atılmaz, bekletilir: engelleme kalktığında görev hâlâ sürüyorsa kart o zaman görünür. Engellemeden sonra sohbet bir süre daha minimal modda kalır, bu yüzden kart engellemenin bitişinden birkaç dakika sonra görünebilir. Görev engelleme sırasında bittiyse yanıtını alırsın, kart almazsın.
- Engelleme sırasında **🔄 Yeniden dene** hiçbir şey yapmaz. Mesaj yeniden gönderilmez ve düğme kalır, böylece engellemeden sonra yeniden dokunabilirsin. 0.7.14 dahil o zamana kadar mesaj Claude'a gidiyor, düğmeli hata mesajı ise kalıyordu, böylece ikinci dokunuş onu bir kez daha gönderiyordu.
- Ana klavye de bekletilir, yeniden başlatmanın gönderdiği dahil (0.7.14'e kadar başka bir şey onu yenileyene kadar kayboluyordu). Engellemeden sonra gönderilir.

Engelleme kalktıktan sonra her şey şu sırayla gelir: bekletilen yanıtlar, sonra hâlâ süren görevlerin kartları, sonra klavye. Minimal modun bitmesini beklemek zorunda kalan bir kart klavyeden sonra gelir.

**Yanıtlar kaybolmaz.** Sohbete gönderim kapalıyken çıkan bir yanıt bekletilir ve engelleme kalktıktan sonra birkaç saniye içinde, ilk satırında `⏳ geç geldi: Telegram sınırı yüzünden 42 dk bekletildi` ile teslim edilir. Yeniden sormana gerek yok; sorarsan iki yanıt da gelir, önce eskisi: birkaç saatlik bir engelleme birden çok görevi kapsar ve her birinin yanıtına ihtiyacın var. (Bekletilen yanıtlar bellekte yaşar: bazıları beklerken aipager'ı yeniden başlatırsan kaybolurlar; kapanış günlüğü kaç tane olduğunu söyler. Bir sohbet en fazla 20 tanesini ve 24 saatten eski olmayanları bekletir; bu nedenlerden biriyle atılan her şey, ne olduğu belirtilerek günlüğe bir uyarıyla yazılır.)

**Bir engelleme sohbeti sonradan daha hızlı değil, daha yavaş yapar.** Engelleme kurulur kurulmaz sohbetin öğrenilmiş hızı en düşüğe iner ve engelleme sayılır; kapalı geçen saatler hiçbir şeyi geri getirmez, büyüme engellemenin kalktığı andan yeniden başlar ve **yedi gün** boyunca (`FLOOD_BAN_MEMORY_DAYS`) sohbetin hız tavanı *ve* saatlik bütçesi, bir artı o haftaki engelleme sayısına bölünür: bir engelleme ikisini de yarıya, iki engelleme üçe böler. `aipager status` bunların hepsini gösterir. (Eskiden bu bellek 24 saat sürüyor ve yalnızca tavanı bölüyordu; 2026-09-19'da engellenen bir sohbet tam hıza dönmüş ve 2026-09-23'te yedi saat engellenmişti.) Bir haftada beş ya da daha fazla engelleme, eskiyene kadar sohbeti minimal modda tutar: yanıtlar gider, kartlar duraklatılmış kalır.

YAPMAMAN gerekenler:

- “Düzeltmek” için aipager'ı yeniden başlatma. Kapalılık gösterilen saatte kendiliğinden kalkar. 0.7.13'ten beri yeniden başlatma engellemeyi artık unutmaz (bitiş zamanı, sohbetin kazanılmış hızı ve engelleme geçmişi `~/.claude/aipager-flood-state.json` dosyasında saklanır ve başlangıçta geri yüklenir; sohbetin son saatteki çağrıları ve uyarı rejimi de öyle), bu yüzden yeniden başlatma onu artık uzatmaz da. Ama hâlâ bekletilen bütün yanıtları atar ve hiçbir şeyi düzeltmez.
- Daha erken yeniden denemek umuduyla `TELEGRAM_MAX_RETRY_AFTER` değerini varsayılan 90 sn'nin altına indirme: bu eşiğin üstündeki her şey bir istek sınırı değil, bir engellemedir.

Bundan nasıl kaçınılır: aynı sohbette daha az eşzamanlı oturum tut ya da en hareketli olanlara kendi sohbetlerini ver (`aipager config`).

## <a id="a-session-dropped-off-the-pinned-status-bar-it-is-gone"></a>Bir oturum sabitlenmiş durum satırından düştü (GONE oldu)

O oturumun dtach süreci sona erdi (bilgisayar yeniden başladı, `pkill claude`, biri bağlı bir dtach oturumunda `exit` yazdı). Onu yeniden oluştur: konuşma, `~/.claude/projects/...` altındaki klasöründe duruyor.

En kolayı Telegram'da `/resume <label>` (ya da liste için yalnızca `/resume`): oturumu yeniden başlatır ve konuşmaya devam eder. Komut satırından:

```sh
aipager resume <label>             # aynısı, terminalden
aipager session <label>            # ya da geçmişi olmayan yeni bir oturum
aipager session <label> --continue # yeni bir dtach, bu klasörün son konuşması
```

## <a id="a-permission-prompt-with-no-news-for-5-minutes"></a>5 dakikadır haber gelmeyen bir izin isteği

Bir oturum bir izin isteğini ya da soruyu bekliyorsa ve Claude Code 5 dakika boyunca hiçbir şey bildirmezse aipager oturumun kayıtlı sohbetini okur. aipager isteği beklemeyi ancak sohbet isteğin bittiğini gösterirse (terminalde yanıtlanmış ve haberi kaybolmuşsa) bırakır ve oturumu yeniden çalışıyor sayar. Aksi halde istek ne kadar sürerse sürsün açık kalır: kart ve sabitlenmiş çubuk oturumun yanıtını beklediğini söylemeye devam eder, "hâlâ çalışıyor" notu gelmez ve mesajların sen yanıt verene kadar bekletilir (bkz. [metin](commands.md#text)). İstek açıkken kapanan bir Claude Code oturumu sona erdirir. İsteğin düğmeleri, iletişim kutusu hâlâ açıksa onda yanıt vermeye devam eder (bkz. [yanıtlar](commands.md#idle-responses)).

Beklemeyi değiştirmek için:

```sh
AIPAGER_INTERACTIVE_TIMEOUT=300 aipager start    # saniye cinsinden
```

## <a id="voice-extra-wont-install-via-telegram"></a>Sesli mesaj desteği Telegram üzerinden kurulmuyor

`[📦 Sesli mesajları kur]` düğmesi kurucuyu çalıştırır ve o çalışırken birkaç saniyede bir kendi mesajını düzenler (`hâlâ sürüyor… (25 sn)`). Kurulum başarısız olursa mesaj çıkış kodunu ve kurucunun çıktısının son 500 karakterini gösterir. Sık nedenler:

- Ağ: pip PyPI'a ulaşamıyor. Bağlantıyı aipager'ın çalıştığı bilgisayardan kontrol et.
- Disk: `~/.cache/huggingface/` model için yaklaşık 74 MB ve pip'in wheel önbelleği için yaklaşık 250 MB daha ister. Önce yer aç.
- Permission denied: venv salt okunur. Her zaman kullanıcına ait bir venv kullanan `uv tool install` yöntemine geç.

Bot kurulumun ortasında bağlantıyı kaybettiyse bilgisayardaki kurulumun kendisi genelde tamamlanmıştır: aipager'ı yeniden başlat ve sesli mesajı yeniden dene.

## <a id="open-app-shows-open-this-page-from-the-telegram-app-to-sign-in"></a>Uygulamayı açınca “Giriş yapmak için bu sayfayı Telegram uygulamasından aç.” görünüyor

Sayfa yüklendi ama Telegram'ın Mini App SDK betiği (`initData` veren betik) yüklenmedi, bu yüzden sayfanın giriş yapacak bir şeyi yok ve hiçbir API çağrısı yapmıyor. Mesaj suç sendeymiş gibi duyuluyor; aslında neden ağdır.

Yeni aipager sürümleri bu betiği kendileri indirir ve sayfanın kaynağından sunar (`/telegram-web-app.js`), böylece telefonun yalnızca sayfayı sunan sunucuya ulaşması gerekir, ayrıca `telegram.org` adresine değil. Bunu eski bir sürümde görüyorsan güncelle.

Sonra sırayla:

1. Sayfanın kaynağına bak. `<script>` etiketi `https://telegram.org/js/telegram-web-app.js` adresini gösteriyorsa aipager betiği henüz indirememiş ve sayfa onu Telegram'dan yüklüyor; `telegram.org` adresine ulaşamayan bir telefonda çalışmayan da tam olarak bu. aipager onu Mini App sunucusu başlarken indirir ve günde en fazla bir kez yeniden dener, bu yüzden aipager'ı yeniden başlatmak hemen yeniden dener; `journalctl --user -u aipager | grep "webapp sdk"` neyin ters gittiğini söyler (genelde engellenmiş ya da filtrelenmiş bir `telegram.org`).
2. Etiket `/telegram-web-app.js` adresini gösteriyorsa aipager'ın onu sunduğunu kontrol et: aipager bilgisayarında `curl -sI http://127.0.0.1:8765/telegram-web-app.js` (değiştirdiysen portu düzelt) `Content-Type: application/javascript` ile `200` döndürmeli. Kopya `~/.local/share/aipager/webapp-sdk/` içinde durur.
3. URL, `/app` düğmesiyle ya da botun menü düğmesiyle değil, sıradan bir tarayıcıda açıldı: o zaman bu beklenen mesajdır. SDK, `initData` değerini yalnızca Telegram'ın içinde verir.
4. Sayfa beş dakikadan uzun süre açık kaldı: `initData` süresi dolar ve sayfa yeniden açılmasını ister. `/app` düğmesine yeniden dokun.

## <a id="pyexpat-_xml_setalloctrackeractivationthreshold-on-brew-install"></a>brew ile kurulumda `pyexpat _XML_SetAllocTrackerActivationThreshold`

Homebrew'un `python@3.12` paketi, sistemindekinden daha yeni bir libexpat ile derlenmiş; neredeyse her zaman macOS Tahoe (26.x) üzerinde **Xcode ve Command Line Tools eski** olduğu için. brew çıktısı genelde bunu kendisi söyler. İki çözüm:

- **Bunun yerine `uv tool install aipager` kullan**. uv kendi python'unu getirir ve sorunu tamamen atlar. macOS'ta önerilen yol budur; bkz. [README](README.md#install).
- **Xcode'u ve Command Line Tools'u güncelle**:
  ```sh
  sudo rm -rf /Library/Developer/CommandLineTools
  sudo xcode-select --install
  ```
  Ya da App Store'da Xcode'u aç ve son sürüme güncelle.

## <a id="modulenotfounderror-aipager"></a>“ModuleNotFoundError: aipager”

aipager programı, artık içinde aipager kurulu olmayan bir Python'u gösteriyor (genelde bir venv silindikten sonra). Yeniden kur:

```sh
uv tool install --reinstall aipager
# ya da başta kullandığın kurucuyla: pipx, brew, pip
```

## <a id="the-session-list-was-lost-or-damaged"></a>Oturum listesi kayboldu ya da bozuldu

aipager oturumlarını `~/.claude/aipager-sessions.json` dosyasında tutar ve onu atomik olarak yeniden yazar (yeni dosya, sonra yeniden adlandırma), bu yüzden yarıda kalan bir yazım eski dosyayı sağlam bırakır. Dosya yine de yoksa, okunamıyorsa ya da geçerli JSON değilse aipager günlüğe baştan başladığını yazar (dosya vardı ama okunamadıysa bir uyarı) ve boş bir listeyle başlar; durmaz. Bu dosyanın yedeği yoktur. Önemli hiçbir şey kaybolmaz:

- çalışan oturumlar, monitörün ilk taramasında dtach soketlerinden (`/tmp/claude-dtach-*.sock`) yeniden bulunur;
- her konuşma hâlâ Claude Code'un kendi `~/.claude/projects/...` klasöründe durur, bu yüzden o oturumun klasöründe `aipager session <name> --continue` ona devam eder (`--resume` seçmeni sağlar);
- kaybolanlar sona eren oturumların listesi (`/resume`), açık olan kartlar ve aipager'ın her oturum hakkında hatırladığı geri kalan şeylerdir.

aipager başlangıçta çökmeye devam ederse `aipager logs` (ya da `journalctl --user -u aipager`) nedenini söyler; onu devre dışı bırakmak için dosyayı kenara al (`mv ~/.claude/aipager-sessions.json ~/aipager-sessions.json.old`).

## <a id="a-session-launch-fails"></a>Bir oturum başlamıyor

dtach'ın reddettiği bir başlatma, nedenini sohbette tek satırla gösterir (soket zaten var, shell çalıştırılabilir değil, sözde terminal yok, ad çok uzun, erişim reddedildi, klasör yok); dtach'ın yollarla birlikte kendi mesajı `aipager logs` içindedir.

## <a id="aipager-doctor-check-list"></a>`aipager doctor` kontrol listesi

Sıra önemlidir: her kontrol öncekilerin geçtiğini varsayar. Beklenmedik bir ortamda çöken bir kontrol, kontrolün adı ve hatayla tek bir ⚠ satırı olarak gösterilir; diğer kontroller yine de çalışır.

| Kontrol (`--json` anahtarı) | Neyi kontrol eder | Nasıl düzeltilir |
|---|---|---|
| `config_parses` | `aipager.yaml` ve `policy.yaml` okunabiliyor; ilk o gelir, çünkü sonraki tüm kontroller yapılandırmayı okur | hata dosyayı ve satırı söyler |
| `config` | `~/.config/aipager/aipager.yaml` var ve içinde bir token ve bir sohbet var | `aipager config` |
| `token_valid` | Token Telegram'da `getMe` ile çalışıyor | `aipager config` komutunu yeniden çalıştır |
| `chat_reachable` | Telegram yapılandırılmış sohbeti tanıyor (`getChat`; hiçbir şey gönderilmez) | botu aç, Start'a dokun |
| `team` | Sohbetler ve üyeler (ya da eski `team.yaml`): kişisel mi ekip modu mu olduğunu söyler ve bozuk ya da uyuşmayan eski bir `team.yaml` dosyasını işaretler | `aipager config` |
| `role_shell_access` | Bash çalıştırabilen her üye rolü için bir kez uyarır (o rolün güvenlik kuralları elden geldiğince çalışır, bir sınır değildir; bkz. [gruplar](groups.md#roles)) | bu role `policy.yaml` içinde Bash verme ya da kabullen |
| `peer_messages` | Claude oturumları arasındaki mesajlar: makbuzlarının nerede tutulduğu ve bu klasörün kapalı olup olmadığı, bu sistemin gönderenin başlangıç zamanını doğrulayıp doğrulayamayacağı ve bir kuralın SendMessage izni verdiği her rol için bir uyarı (bkz. [güvenlik](security.md#messages-between-claude-sessions)) | paylaşılan makbuz klasörünü kaldır (aipager yeniden oluşturur) ya da bu role SendMessage verme |
| `claude` | `claude` programını her başlatmanın bulduğu gibi bulur (`aipager.yaml` içindeki `claude_path` ya da `$AIPAGER_CLAUDE_BIN` hemen kazanır; yoksa `~/.local/bin`, PATH ve Homebrew içindeki en yenisi) ve bulunan DİĞER her kurulumu listeler | Claude Code'u kur ya da `claude_path` / `AIPAGER_CLAUDE_BIN` ayarla |
| `claude_auth` | Gerçek bir oturumun aldığı ortamda `claude auth status` dener. **Asla başarısız olmaz**: “giriş yapılmamış” ve “denemenin kendisi başarısız oldu” farklı gösterilir ve ikisi de oturum başlatmayı engellemez | `claude auth login` ya da bir API anahtarı / `CLAUDE_CODE_OAUTH_TOKEN` ayarla |
| `dtach` | `dtach` programı bulundu (aipager'ın kendi `dtach-bin` paketi ya da PATH'teki) | `uv tool install --reinstall aipager` |
| `hook_scripts` | `aipager-hook` ve `aipager-statusline` PATH'te | `uv tool install --reinstall aipager` |
| `settings_json` | `~/.claude/settings.json` içinde aipager hook'ları bağlı | `aipager config` |
| `daemon` | aipager çalışıyor ve soketi yanıt veriyor; Telegram sınırlarının durumunu da gösterir (bkz. [yukarısı](#the-bot-slowed-down-a-rate-limit-429)) | `aipager start` |
| `service_installed` | İsteğe bağlı: hizmet dosyası yerinde | `aipager service install` |
| `service_unit_path` | Yalnızca Linux: *kurulu* hizmetin `Environment=PATH=` değeri gerçekten bulunan claude programının klasörünü içeriyor; metin olarak okunur, çünkü doctor systemd'nin PATH'ini operatörün etkileşimli shell'inden göremez | `aipager service install --yes` |
| `miniapp` | Açıksa Mini App başlayabiliyor mu. **Asla başarısız olmaz**: Mini App isteğe bağlıdır | gösterdiği düzeltme |

`aipager doctor --json` aynı kontrolleri bu sırayla, bu anahtarlarla yazdırır (bkz. [komutlar](commands.md#aipager-doctor---json)).

Claude kimlik bilgilerini etkileşimli olarak bulup `daemon.env` dosyasına kopyalamak (dosya var ama içinde yoksa da) ya da birden fazla kurulum bulunduğunda `claude_path` değerini sabitlemek için `aipager doctor --fix` çalıştır. Yalnızca sorduktan sonra işlem yapar. PATH'inde claude klasörü olmayan bir hizmet için `aipager service install --yes` çalıştır.

## <a id="the-daemon-cant-find-claude-or-picks-the-wrong-install"></a>aipager `claude` programını bulamıyor ya da yanlış kurulumu seçiyor

Eskiden altı ayrı yer `claude` programını her biri kendi yoluyla arıyordu ve birbirini tutmayabiliyordu. Artık hepsi tek bir arama işlevinden geçer (`aipager/claude_resolve.py`). `aipager.yaml` içindeki `claude_path`, sonra `$AIPAGER_CLAUDE_BIN`, çalışıyorlarsa hemen kazanır. Aksi halde `~/.local/bin`, `$PATH` ve Homebrew öneklerinde bulunan her `claude` denenir ve en yeni sürüm kazanır (bu yüzden başka bir kurulum daha yeniyse `$PATH` sırasını değiştirmek seçimi değiştirmez). Tam olarak hangisini seçtiğini ve başka neler bulduğunu görmek için `aipager doctor` çalıştır; yanlış olan kazanıyorsa doğrusunu sabitle:

```sh
aipager doctor --fix        # bulunan kurulumlar arasından etkileşimli seçim
# ya da elle:
echo 'claude_path: /home/you/.local/bin/claude' >> ~/.config/aipager/aipager.yaml
```

## <a id="sessions-cant-authenticate-under-the-systemd-service-but-claude-works-fine-in-a-terminal"></a>Oturumlar systemd hizmeti altında giriş yapamıyor, ama `claude` terminalde sorunsuz çalışıyor

`systemctl --user` hizmetleri `~/.bashrc` ya da `~/.profile` dosyasını asla okumaz, bu yüzden oradaki bir `export CLAUDE_CODE_OAUTH_TOKEN=…` satırı aipager'a asla ulaşmaz. Hizmet bunun yerine systemd'nin `LoadCredential=` özelliğini kullanır ve `~/.config/aipager/daemon.env` dosyasını okur: `aipager service install` ilk çalıştığında otomatik oluşturulan, 0600 izinli düz bir `KEY=VALUE` dosyası (eski `config.env` dosyasında token varsa oradan taşınır, yoksa bir kez giriş shell'inde bulunur, o da yoksa bir uyarıyla boş kalır). Hâlâ boşsa `aipager doctor --fix` evet dersen aynı yerlerde yeniden arar ve bulduğu satırı, dosyanın diğer satırlarını koruyarak ekler; token'ı asla yazdırmaz. Ya da satırı elle ekle:

```sh
cat ~/.config/aipager/daemon.env          # ne olduğuna (ya da olmadığına) bak
echo 'CLAUDE_CODE_OAUTH_TOKEN=sk-...' >> ~/.config/aipager/daemon.env
chmod 600 ~/.config/aipager/daemon.env
aipager service stop && aipager service start
```

Bu dosyanın neye karşı koruduğu ve neye karşı korumadığı için bkz. [Güvenlik modeli: Claude kimlik bilgileri](security.md#the-claude-credential-what-actually-protects-it).

## <a id="the-daemons-control-socket-moved"></a>aipager'ın kontrol soketi taşındı

`aipager doctor` / `aipager status` eskiden hep `/tmp/aipager.sock` dosyasına bakardı. systemd hizmeti altında kontrol soketi artık `$XDG_RUNTIME_DIR/aipager.sock` içinde durur (`/tmp` yedeği yalnızca `$XDG_RUNTIME_DIR` ayarlı değilken: konteynerler, WSL1, minimal dağıtımlar). Yalnızca bu tek soket taşındı; her oturumun dtach soketleri (`/tmp/claude-dtach-*.sock`) etkilenmedi. Belirli bir yer gerekiyorsa `AIPAGER_SOCKET_PATH` ayarla.

## <a id="update-says-the-restart-would-kill-sessions-killmode"></a>`/update`, yeniden başlatmanın oturumları sonlandıracağını söylüyor (KillMode)

Bu sürümden önce yazılmış hizmet dosyalarında `KillMode=` satırı yoktur, bu yüzden systemd `control-group` kullanır: `aipager.service` hizmetini durdurmak ya da yeniden başlatmak, aipager'ın başlattığı her Claude oturumunu sonlandırır. Bu yüzden `/update` yeni sürümü kurar ama aipager'ı yeniden başlatmaz ve yeniden başlatmanın düşüreceği oturumları listeler. Bunu bir kez düzelt:

```
aipager service install
```

Bu, hizmeti `KillMode=process` ile yeniden yazar, systemd'yi yeniden yükler ve aipager'ı yeniden başlatır; bu yeniden başlatma da yeni ayarla yapılır. `systemctl --user show -p KillMode aipager.service` ile kontrol et (`KillMode=process`). Bu ayarla systemd her yeniden başlatmada oturumların için “left-over process” satırları yazar; bu normaldir.

## <a id="aipager-update-cant-find-uv--pipx"></a>`aipager update` uv ya da pipx bulamıyor

`aipager update` kurucuyu `PATH` içinde ve ayrıca `~/.local/bin`, `~/.cargo/bin`, Homebrew önekleri, `/usr/bin` ve `/bin` içinde mutlak yolla arar. Yine de `pipx` bulamadığını söylüyorsa bu aipager'ı oluşturan kurucu artık yoktur ya da başka bir yerdedir; elindeki araçla yeniden kur (`pipx install --force aipager`, `uv tool install --reinstall aipager`).

## <a id="update-refused-editable--not-your-install"></a>Güncelleme reddedildi: editable ya da sana ait olmayan kurulum

`/update` ve `aipager update` yalnızca kullanıcına ait ve onun yazabildiği bir kurulumu, onu oluşturan kurucuyla günceller. Şunlar için reddeder ve nedenini söyler:

- editable modda bir çalışma kopyası (geliştirme için): onu `git pull` ile güncelle;
- Nix, Snap, Docker ya da bir sistem paketiyle yapılmış bir kurulum: o sistemin güncellemesini kullan;
- başka bir kullanıcıya ait bir kurulum (örneğin `/opt` içindeki bir root venv'i): onu o kullanıcı olarak güncelle;
- yerel bir klasörden yapılmış bir kurulum (`pipx install /path/to/aipager`): `buradan güncellenemez: it was installed from a local folder; update it from that folder`. Yeni sürümü o klasöre al ve oradan yeniden kur ya da `pipx install --force aipager` ile PyPI'a geç;
- hangi kurucunun yaptığı anlaşılmayan bir kurulum (`could not tell which installer owns this aipager`): onu `uv tool install aipager` ya da `pipx install aipager` ile yeniden kur.

## <a id="update-failed-unexpected-argument---refresh"></a>Güncelleme başarısız: unexpected argument '--refresh'

aipager 1.0.0 ve öncesi `uv tool upgrade aipager --refresh` çalıştırıyordu, ama hiçbir uv sürümü bu bayrağı kabul etmiyor, bu yüzden uv ile yapılmış bir kurulumun her güncellemesi `error: unexpected argument '--refresh' found` ile duruyor ve hiçbir şeyi yeniden başlatmıyordu. Yeni sürümler `uv tool upgrade aipager` çalıştırır. Bu noktayı bir kez geçmek için bu bilgisayardaki bir terminalden güncelle (bu sürümlerde `aipager update` da aynı sorunu yaşar):

```
uv tool upgrade aipager
systemctl --user restart aipager.service
```

Hizmet yoksa (ya da macOS'ta) aipager'ı nasıl başlatıldıysa öyle yeniden başlat.

## <a id="claude-code-update-installed-with-npm-into-a-folder-you-cant-write-to"></a>Claude Code güncellemesi: yazamadığın bir klasöre npm ile kurulmuş

`sudo npm install -g` ile kurulan Claude Code, yalnızca root'un değiştirebildiği bir klasörde durur (`/usr/lib/node_modules` gibi), bu yüzden kullanıcının olarak `claude update` çalıştırmak `global folder isn't writable` ile başarısız olur. `/update` ve Mini App daha yeni sürümü `buradan güncellenemez: installed with npm into a folder your user can't write to` ile ve onun için bir düğme olmadan gösterir. aipager'ın çalıştığı kullanıcı olarak bir kez `claude install` çalıştır: bu, ev klasöründe Claude Code'un kendi kurucusuna geçer ve o da sudo olmadan güncellenir. Sonra aipager'ı yeniden başlat (`systemctl --user restart aipager.service` ya da nasıl başlatıldıysa öyle): çalışan aipager başlangıçta bulduğu Claude Code'u tutar, yeniden başladıktan sonra bulduklarının en yenisini alır. `aipager.yaml` içindeki `claude_path` (`aipager doctor --fix` onu ayarlayabilir) ya da `AIPAGER_CLAUDE_BIN` npm sürümünü gösteriyorsa onu `~/.local/bin/claude` adresine yönlendir ya da önce kaldır, çünkü bunlar her zaman kazanır.

## <a id="updated-but-the-version-didnt-change"></a>Güncellendi ama sürüm değişmedi

Bir git URL'inden yapılmış bir pipx kurulumu PyPI'dan değil, o git kaynağından güncellenir. Kaynakta daha yeni bir şey olmadığında `/update` şunu yazar: `✅ aipager zaten A sürümünde: bu pipx kurulumu kendi git reposundan güncellenir ve orada daha yenisi yok.`. Yeni bir sürümü oraya gönder ya da seç veya `pipx install --force aipager` ile PyPI'a geç.

## <a id="the-daemon-didnt-come-back-after-an-update"></a>aipager bir güncellemeden sonra geri gelmedi

Güncelleme, bir şeyi yeniden başlatmadan önce yeni sürümün yüklendiğini kontrol eder, ama yeni aipager yine de başlamazsa systemd her 5 sn'de bir yeniden dener. Nedenine bak:

```
journalctl --user -u aipager.service -n 50
```

Önceki sürümü aynı kurucuyla yeniden kur, sonra yeniden başlat:

```
pipx install --force aipager==<previous>        # ya da:
uv tool install --force aipager==<previous>     # ya da:
<venv>/bin/python -m pip install aipager==<previous>
systemctl --user restart aipager.service
```

Yerel bir yoldan yapılmış pipx kurulumu: eski bir commit üzerinde `pipx install --force /path/to/aipager`. Bekleyen eskimiş bir duyuru varsa `rm -f ~/.local/share/aipager/update-restart.json` ile kaldır (24 sa sonra zaten yok sayılır); `~/.local/share/aipager/update.lock` yalnızca bir kilit dosyasıdır ve hiçbir güncelleme sürmüyorken silmek güvenlidir.

## <a id="an-update-was-interrupted-when-aipager-shut-down"></a>“aipager kapanırken bir güncelleme yarıda kaldı”

`/update` kurulum yaparken aipager durdu (ya da yeniden başlatıldı) ve kurucu onunla birlikte durdu, bu yüzden kurulum yarım yazılmış olabilir. aipager ya da Claude Code tuhaf davranıyorsa onar: aipager için mesajdaki yeniden kurulum komutunu çalıştır (örneğin `pipx install --force aipager`), sonra `systemctl --user restart aipager.service`; Claude Code için `claude update` komutunu yeniden çalıştır.

## <a id="a-chat-is-in-the-wrong-language"></a>Bir sohbet yanlış dilde

aipager'ın dili, sohbetteki mesajlardan değil, o sohbet için seçilenden gelir. O sohbette `/language` aç ve istediğin dili seç: bu seçim her şeyden önce gelir. Özel sohbette **Otomatik**, Telegram uygulamanın dilini izler (Telegram'da Ayarlar → Dil); aipager'da olmayan bir dil İngilizce gösterilir. Diller gelmeden önce var olan bir özel sohbet, sen bir dil seçene kadar İngilizce kalır. Bir grup, bir yönetici ona bir dil seçene kadar İngilizce kalır. Dil yalnızca aipager'ın kendi metinlerini değiştirir: Claude, aipager ne gösterirse göstersin, ona hangi dilde yazarsan o dilde yanıt verir. Bkz. [Diller](commands.md#languages).

## <a id="the-bot-description-is-not-the-one-i-wrote"></a>Botun açıklaması BotFather'da yazılan değil

aipager, botun açıklamasını ve kısa açıklamasını, konuştuğu her dilde, Telegram uygulaması o dilde olan kişiler için ayarlar ve Telegram onlara BotFather'da yazılanın yerine bu metni gösterir. aipager bunu yalnızca senin kendi açıklaman yokken yapar, bu yüzden bu sürüm kurulmadan önce yazılmış bir açıklamayı herkes görür. aipager kendisininkini koyduktan sonra yazıldıysa bu dillerdeki kişiler hâlâ aipager'ın açıklamasını görür. Onlara seninkini göstermek için aipager'ın metnini her dil için Bot API ile sil (boş metin ve o `language_code` ile `setMyDescription` ve `setMyShortDescription`); seninki varken aipager onu bir daha asla koymaz.

## <a id="aipager-says-languagesjson-could-not-be-used"></a>aipager, languages.json dosyasının kullanılamadığını söylüyor

aipager diller hakkında öğrendiklerini (diller gelmeden önce burada kimin olduğu, her kişide gördüğü son Telegram dili ve tek seferlik öneri ile ilk temas satırının gidip gitmediği) `~/.local/state/aipager/languages.json` dosyasında tutar. Bu dosya okunamazsa aipager onu aynı klasörde `languages.json.bad-<time>` olarak kenara alır, günlüğe bir uyarı yazar (bir karar verilene kadar her başlangıçta yeniden) ve bir karar verilene kadar tüm sohbetleri dil önerisi olmadan İngilizce tutar: onarabiliyorsan eski dosyayı geri koy ya da `languages.json` dosyasını silip yeniden başlat; aipager o zaman tanıdığı herkesi yine burada önceden bulunmuş sayar (kişi seçene kadar İngilizce). Birinin seçtiği dil bu dosyada tutulmaz ve asla kaybolmaz.

## <a id="right-to-left-text-looks-wrong"></a>Sağdan sola metin yanlış görünüyor

Farsça ve Arapçada aipager, her satır sağdan sola okunsun, oturum adları, komutlar ve kod ise soldan sağa kalsın diye kendi metinlerine görünmez yön işaretleri koyar. Bir satır yine de karışık görünüyorsa sebep çoğu zaman karışık yönlü metni kendine göre çizen Telegram istemcisidir: bildirmeden önce başka bir istemcide dene (telefona karşı Desktop ya da Web). Claude'un kendi yanıtları, eklenmiş işaretler olmadan Claude'un yazdığı gibi gösterilir.

## <a id="a-translated-keyboard-word-does-nothing-in-another-chat"></a>Çevrilmiş bir klavye sözcüğü başka bir sohbette işe yaramıyor

Her sohbetin klavyesi o sohbetin dilindedir. Ana klavyenin sözcükleri (status, stop, new, kill ve diğerleri) her sohbette her dilde anlaşılır ve İngilizce sözcükler her zaman çalışır. Bir şablon ya da komut düğmesinin sözcüğü farklıdır: başka dildeki bir sohbette yazılınca bir düğme değil, Claude'a bir mesajdır, bu yüzden Türkçe “Konuşmayı temizle” Farsça ya da İngilizce bir sohbette asla `/clear` çalıştırmaz. Düğmenin kendisini ya da İngilizce komutu (`/clear`) kullan. Sohbetin dili değiştikten sonra klavye eski görünüyorsa güncelini almak için `/start` gönder. Aynı zamanda oturumlarından birinin adı olan bir sözcük o oturuma seslenir. `/mode`, `ask`, `auto` ve `full` sözcüklerini her zaman mod olarak okur; çevrilmiş bir mod sözcüğünü ise yalnızca çalışan bir oturumun adı tam olarak buysa oturum olarak okur.

## <a id="still-stuck"></a>Hâlâ çözemedin mi?

Bir sorun raporu gönder: botla olan özel sohbetinde `/help` altındaki **Sorun bildir** düğmesine dokun ya da terminalde `aipager report` çalıştır. Bir şey gönderilmeden önce raporun tamamını görürsün ve içinde sohbet, mesaj, ad, yol ya da token yoktur (bkz. [Sorun raporları](problem-reports.md)). Gönderdikten sonra `ap1-7c41d09e2b3a` gibi bir referans alırsın.

Sonra, konuşmak istersen [github.com/dev-aly3n/aipager/issues](https://github.com/dev-aly3n/aipager/issues) adresinde bir issue aç ve bu referansı yaz. Lütfen issue'ya `aipager doctor` ya da `aipager logs` çıktısını yapıştırma: bunlarda sohbet kimliğin, ev klasörünün yolları, oturum adların ve mesajlarının başı var. Bir günlük satırı gerekirse aipager'ı sürdüren kişi tam o tek satırı ister ve sen de önce içindeki kişisel her şeyi çıkarabilirsin.

## <a id="see-also"></a>Ayrıca bkz.

- [Mimari](architecture.md): her parçanın ne yaptığı.
- [Güvenlik modeli](security.md): aipager neye dokunur, neye dokunmaz.
