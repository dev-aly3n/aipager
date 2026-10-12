# Hook olayları

<!-- i18n-source: docs/hooks.md @ 6fc084f -->

> Bu sayfa İngilizceden çevrildi; ikisi birbirini tutmazsa İngilizce sürüm geçerlidir. ([English](../../hooks.md))

Claude Code, bir görevin belirli anlarında hook komutları çalıştırır: bir mesaj gönderildi, bir araç çalışmak üzere, bir araç bitti, görev sona erdi ve benzerleri. aipager bu anların her birine bağlanır ve onları Telegram'da işlemlere çevirir: çalışma kartı, izin istekleri, yanıtlar, bildirimler ve denetim kaydı.

## <a id="how-it-is-wired"></a>Nasıl bağlanıyor

`aipager config` (ya da `aipager setup`) aipager'ın girdilerini `~/.claude/settings.json` dosyasına ekler; aipager da her başlangıçta onları kontrol eder:

- aşağıdaki 16 olayın her biri için bir `aipager-hook` komutu; araç olaylarında (`PreToolUse`, `PostToolUse`, `PermissionRequest`) `*` filtresiyle;
- `PreModelSwitch` için 5 saniyelik ve `aipager config` ile `aipager setup` tarafından `PermissionRequest` için 30 saniyelik zaman aşımı (hook'un kendisi en fazla 20 saniye bekler, aşağıya bak); diğer girdiler Claude Code'un varsayılanını kullanır;
- `statusLine` komutu, `aipager-statusline`.

`aipager uninstall` bu girdileri yeniden kaldırır (bkz. [komutlar](commands.md#command-line-aipager-uninstall)).

Claude Code her olay için yeni bir `aipager-hook` süreci (`aipager.dtach.notify_hook:main`) başlatır. Bu süreç olayın JSON'unu stdin'den okur, iki alan ekler (aşağıda) ve onu aipager'a bir Unix soketi üzerinden tek bir datagram olarak gönderir. Soket, sırasıyla: yalıtılmış bir test örneği için `$AIPAGER_INSTANCE_DIR/aipager.sock`, `$AIPAGER_SOCKET_PATH`, `$XDG_RUNTIME_DIR/aipager.sock` ya da `/tmp/aipager.sock`. Gönderim hiç beklemez: aipager dinlemiyorsa olay kaybolur ve oturum monitörü oturumun dosyalarından açığı kapatır.

Çoğu olay, süreç başlatma için Claude Code'a yaklaşık 20-25 ms'ye mal olur. Bazıları aipager'ın kural kodunu da yükler: bir araç çağrısından önce (güvenlik kontrolü, bkz. [PreToolUse](#pretooluse)) yaklaşık 70 ms, görevin sonunda (açık görev işaretini kaldırmak için) yaklaşık 55 ms ve `UserPromptSubmit` (görevin kurallarını yazar). `PermissionRequest` senin dokunmanı bilerek, 20 saniyeye kadar bekler.

Hook belleğini 1 GB ile sınırlar. Sınıra dayanan bir hook hatayla çıkar (`PreToolUse` için Claude Code'un ret olarak okuduğu bir çıkış koduyla) ve aipager'a bir `hook_memory_cap_hit` datagramı gönderir; bu da sohbette, kurulumun sahibi için “🐞 Bunu bildir” düğmesi olan bir bildirime dönüşür (bkz. [sorun raporları](problem-reports.md)). Hook'un ve durum satırının tanılarını stderr'e yazması için oturumun ortamında `AIPAGER_DEBUG=1` ayarla.

## <a id="what-the-daemon-receives"></a>aipager neyi alır

Datagram, Claude Code'un kendi hook JSON'udur, aipager'ın eklediği alanlarla (bazı olaylar daha fazlasını taşır; aşağıda her olayda belirtilmiştir):

| Alan | Anlamı |
|---|---|
| `hook_event_name` | Olay (Claude Code'un alanı; aipager işi buna göre dağıtır). |
| `session_id`, `transcript_path`, `cwd`, ... | Claude Code'un kendi alanları, geldiği gibi. `session_id`, Claude Code'daki konuşmanın kimliğidir. |
| `session` | aipager ekler: oturum için aipager'ın kullandığı ad, `claude-<name>`, aipager'ın oturumu başlatırken ayarladığı `CLAUDE_DTACH_SESSION` değişkeninden. aipager'ın başlatmadığı bir Claude Code oturumundan gelen olaylar adsız gelir ve aipager onları yok sayar. |
| `sl_tokens` | aipager ekler, `MessageDisplay` dışındaki her olayda, oturumun durum satırı dosyası varsa: `context_pct`, `total_output`, `total_input`, `current_output`, `lines_added` ve `lines_removed`. |

Araç olayları ayrıca `tool_name` ve `tool_input` taşır. Bir alt ajanın içindeki her olay `agent_id` taşır (alt ajan olayları ayrıca `agent_type` taşır); ana döngünün kendi olaylarında bunlar yoktur ve aipager, ajanın araç çağrılarını bu sayede o ajanın satırına koyar.

## <a id="event-reference"></a>Olay başvurusu

aipager'ın işleyicileri `aipager/dtach/hook_receiver.py` içinde; hook'un kendi işi `aipager/dtach/notify_hook.py` içinde.

### <a id="sessionstart"></a>`SessionStart`

Bir oturum başladı, devam ettirildi ya da konuşması sıkıştırıldı. aipager'ın henüz izlemediği bir oturum kaydedilir. Sıkıştırmadan sonra kart `📦 name · Konuşma sıkıştırıldı: 82% → 4%` gösterir (bkz. [PreCompact](#precompact--postcompact)). Yeni bir konuşma için hook ayrıca önceki görevin açık görev işaretini kaldırır.

### <a id="sessionend"></a>`SessionEnd`

Oturum sona erdi. GONE olarak işaretlenir: sabitlenmiş durum satırından kalkar ve sona eren oturumlara geçer; `/resume` onu oradan geri getirebilir. `aipager session <name>` ya da `/new <name>` yenisini başlatır.

### <a id="userpromptsubmit"></a>`UserPromptSubmit`

Claude, terminalde yazılan ya da Telegram'dan gönderilen bir mesajı aldı.

Hook önce mesajı oturumun izin notlarıyla (`/tmp/claude-notes-<session>/`, aipager'ın yazdığı her Telegram mesajı için bir tane) karşılaştırır ve görevin kurallarını, [PreToolUse](#pretooluse) kontrolünün okuduğu `/tmp/claude-policy-<session>.json` dosyasına yazar (bkz. [güvenlik → kurallar ekipte nasıl uygulanır](security.md#team-mode-enforcement)). Olayı iletmeden önce aipager'a Claude'un hangi mesajları aldığını söyler (`queue_pickup` datagramı). Sonra, varsa, Claude için ek bağlam yazdırır: `/settings` içinde seçilen yanıt tarzı ve daha eski bir mesaja yanıt verilirken o mesajda ne olduğu ve ne zaman. Bir `<task-notification>` mesajı (bir arka plan ajanı ya da komutu bitince Claude kendi kendini uyandırır) aynı işin devamıdır: hiçbir notla eşleşmez ve görevin kurallarını korur.

aipager oturumu çalışıyor olarak işaretler, yoksa bir çalışma kartı açar (Telegram'dan gelen bir mesajın zaten vardır) ve Claude'un aldığı mesajlardaki 👀 tepkisini 👍 yapar. Süren bir görev sırasında gönderilen bir mesaj, Claude onu o göreve kattığında ya da sonraki görevi onunla başlattığında 👍 alır.

### <a id="pretooluse"></a>`PreToolUse`

Bir araç çalışmak üzere. aipager'ın kendi güvenlik kontrolü burada, hook'un içinde, Claude Code kendi izin kurallarına bakmadan önce çalışır:

- Telegram'dan başlatılan bir görev, onu gönderenin rol kurallarına ve yalnızca `owner` rolünün aşabildiği güvenlik alt sınırına (korunan yollar, komut kalıpları) bağlı kalır; aipager'ın kimseye bağlayamadığı bir görev alt sınırı alır; senin terminalde yazdığın bir mesaj sınırsızdır. Kurallar [gruplar](groups.md#how-rules-work) ve [güvenlik](security.md#team-mode-enforcement) sayfalarında.
- Bir kural çağrıyı reddettiğinde hook bir `deny` kararı yazdırır, Claude Code aracı sormadan reddeder ve aipager bunu öğrenir (`safety_blocked`): görev `🛑 x1 · Bir güvenlik kuralı engelledi: ... (durduruldu)` ile durur ve o görevdeki sonraki her araç çağrısı da reddedilir.
- Kendisi bozulan bir kontrol reddeder; oturumun kuralları okunabilmiş ve sahip istisnası veriyorsa durum farklıdır.

aipager tarafında çağrı çalışma kartında bir satıra dönüşür (bir alt ajanın çağrısı o ajanın satırına gider); `/settings` → Değişiklik önizlemeleri açıkken (varsayılan kapalı) bir `Write` ya da `Edit` değişikliklerin önizlemesini gösterir. Bir `AskUserQuestion` çağrısı, her seçenek için bir düğmesi olan bir soruya dönüşür; hiçbir görev sürmezken Claude Code'un yardımcı bir ajanının sorduğu soru gösterilmez (seni soran Claude değildir). Claude'un kendi `Agent` çağrısı, başlattığı ajan için `subagent_type` ve `description` değerlerini saklar; bir alt ajanın çağrısı da o ajanın son işlemidir: ajan arka planda çalışırken ikisi de yanıtın altındaki durumda görünür.

### <a id="permissionrequest"></a>`PermissionRequest`

Claude Code izin istemek üzere. Çalışma kartı, Claude'un verdiği açıklamanın altında gerçek komutla ya da yolla bir isteğe dönüşür (bkz. [komutlar → izin istekleri](commands.md#permission-prompts)): İzin ver, Reddet, Durdur ve yalnızca Claude Code hatırlanabilecek gerçek bir kural önerdiğinde Hep izin ver.

Hook, yapabildiğinde isteği kendisi yanıtlar. Kısa ömürlü bir yanıt soketi açar, adresini olayla birlikte gönderir ve dokunmanı 20 saniyeye kadar bekler. Zamanında gelen bir dokunuş `PermissionRequest` kararı olarak döner (izin ver, kuralla izin ver ya da reddet), böylece Claude Code kendi iletişim kutusunu göstermez. Dokunuş zamanında gelmezse hook aipager'a beklemenin bittiğini söyler (`permission_reply_timeout`) ve hiçbir şey yazdırmaz; aipager'a ulaşılamadığında ya da bu yolda bir şey bozulduğunda da hiçbir şey yazdırmaz. O zaman Claude Code kendi iletişim kutusunu terminalde gösterir ve daha sonraki bir dokunuş ona tuşlarla yanıt verir. Denetim kaydı (`~/.claude/aipager-audit.jsonl`), her dokunuşun hangi yoldan yanıt verdiğini yazar: `"via": "hook_decision"` ya da `"via": "keystroke_fallback"`.

### <a id="posttooluse--posttoolusefailure"></a>`PostToolUse` / `PostToolUseFailure`

Bir araç bitti ya da başarısız oldu. Çalışma kartındaki satırı donar (hata satırda işaretlenir) ve açık bir isteğe verilen yanıt fark edilir: bundan sonra onun için eski bir İzin ver ya da Reddet hiçbir şey yazmaz. Oturum son 200 araç satırını tutar.

Claude Code'un arka plana taşıdığı bir `Bash` çağrısı bir `backgroundTaskId` bildirir. aipager onu, sonunu görene kadar bir arka plan komutu olarak izler (yalnızca ana döngünün komutları: `agent_id` taşıyan bir çağrı o alt ajana aittir): bir `<status>` taşıyan `<task-notification>` (Claude'u uyandıran mesajda ya da görevin ortasında gelirse konuşma geçmişinde), kimliğini taşıyan `TaskStop` ya da `KillShell`, oturumun sona ermesi ya da iki saat (`AIPAGER_BG_SHELL_MAX_TRACK`, saniye cinsinden, varsayılan 7200). Karttaki satırı için bkz. [komutlar](commands.md#idle-responses).

### <a id="notification"></a>`Notification`

aipager, Claude Code'un `notification_type` alanını okur:

- `permission_prompt`: izin isteğinin daha yavaş ve daha eski sinyali. İsteği yalnızca `PermissionRequest` henüz göstermediyse gösterir.
- Bir izin isteği ya da soru beklerken `idle_prompt`: oturum bekliyor olarak kalır ve sohbet, bekleme başına bir kez `⬆️ jim · hâlâ yukarıdaki yanıtını bekliyor` alır.
- Diğer durumlarda `idle_prompt`: `Stop` gibi işlenir (aşağıda). `Stop` olayı kaybolmuş bir görevi yakalar; zaten teslim edilmiş bir yanıt asla yeniden gönderilmez.

### <a id="stop"></a>`Stop`

Görev bitti. Oturum hazır olur, çalışma kartı seçilen görünümün söylediği gibi donar ve Claude'un yanıtı gönderilir (olaydaki `last_assistant_message` alanından ya da yoksa konuşma geçmişinden). Ardından görevin sonuna kadar bekletilen mesajlar gider. Hâlâ çalışan arka plan ajanları ve komutları işi açık tutar: kart bir iş durumu olarak kalır ve yanıt bu işin canlı durumuyla biter: her ajanın ne yaptığı ve işin kaç arka plan komutunun sürdüğü (bkz. [komutlar → hâlâ çalışan ajanlar](commands.md#agents-still-running-when-the-answer-goes-out)). Claude'un hemen sonraki görev olacak kuyrukta bir mesajı yoksa hook önce açık görev işaretini kaldırır.

### <a id="stopfailure"></a>`StopFailure`

Görev bir API hatasıyla bitti. Oturum hazır olur ve varsa yanıt `Stop` gibi teslim edilir; bilinen bir hata (aşırı yük, istek sınırı, ...) sade sözcüklerle gösterilir.

### <a id="subagentstart--subagentstop"></a>`SubagentStart` / `SubagentStop`

Bir alt ajan başladı ya da bitti. Her birinin çalışma kartında kendi satırı vardır, `🤖 <type> · <activity> · <elapsed>`; kendi araç çağrıları altında katlanmıştır ve durduğunda `✅ 🤖 <type> · N araç · <elapsed>` olarak donar. `SubagentStop` olayı hiç gelmeyen bir alt ajan, 30 dakika sessiz kaldığında (`AIPAGER_SUBAGENT_SILENCE`, saniye cinsinden; kimliğiyle hiçbir türden olay yok) kaldırılır. Çalışan bir ajan birkaç saniyede bir araç olayı gönderir, bu yüzden böyle yalnızca durması kaçırılmış bir ajan kaldırılır (yeniden başlatma, kaybolan bir datagram); o ajanın kimliğiyle tek bir olay gelmeden 30 dakika süren tek bir araç çağrısı da onu kaldırır. `AIPAGER_SUBAGENT_TTL` aynı ayarın eski adı olarak kabul edilir. Türü olmayan ve kimliği bilinmeyen `SubagentStop` olayları sık gelir ve yok sayılır.

### <a id="precompact--postcompact"></a>`PreCompact` / `PostCompact`

Claude bağlamını sıkıştırıyor (`trigger` değeri `auto` ya da `manual`). Çalışma kartı `🔄 name · Konuşma sıkıştırılıyor` gösterir (kart yoksa yeni bir mesaj), böylece duraklama bir çökme gibi görünmez. `PostCompact` bu durumu bitirir; `📦 name · Konuşma sıkıştırıldı: X% → Y%` satırı bir sonraki `SessionStart` olayından ya da o kaçırılırsa durum satırından gelir. Durum satırı ayrıca bağlam %80'e ulaştığında `⚠️ name · Bağlam 82% dolu - yakında otomatik sıkıştırılacak` uyarısını verir (Sıkıştır düğmesiyle), ve yeniden ancak bağlam %30'un altına indikten sonra.

### <a id="messagedisplay"></a>`MessageDisplay`

Claude'un kendi metni, ekranda göründüğü anda. Konuşma geçmişi yalnızca her araç çağrısı turundan sonra yazılır, bu yüzden Claude'un ne dediğinin tek güncel kaynağı budur ve onun yorumlarını çalışma kartına taşıyan da odur. Bir alt ajanın metni (kendi `agent_id` değerini taşıyan herhangi bir olay) asla Claude'un metni sanılmaz: çalışma kartının yorumlarına, Mini App'e ya da görev günlüğüne girmez ve yanıtın altındaki durumda o ajanın son işlemi olur. Hook bu olaya durum satırı verisi eklemez: Claude Code çizerken onu bekler ve mesaj başına birkaç kez gelir.

### <a id="premodelswitch"></a>`PreModelSwitch`

Claude Code 2.1.251 ve sonrası, model değiştirmeden önce bunu çağırır. İçerik `from_model`, `to_model`, `requested_model` ve `source` taşır (yazılan bir `/model` için `command`, ya da `picker` veya `sdk`). Değiştirmeden önce Claude Code genelde "Switch model?" diye sorar, çünkü sonraki yanıtın tüm konuşmayı yeniden okuması gerekir. `permissionDecision: "allow"` ile yanıt veren bir hook, değişikliği bu soru olmadan geçirir.

`aipager-hook` yalnızca aipager'ın kendisinin yazdığı bir değişikliğe, Mini App'teki model seçiciden ya da model klavyesinden gelen değişikliğe `allow` yanıtı verir. Diğer her değişiklik yanıt almaz, bu yüzden Claude Code her zamanki gibi sorar. Bu, operatörün terminalde yazdığı `/model` komutunu, listeden yapılan seçimi ve SDK ile yapılan değişikliği kapsar.

aipager kendi değişikliklerini bir işaret dosyasından tanır. aipager, `/model <name>` yazmadan hemen önce `aipager-modelswitch-<session>.json` dosyasını yazar:
- dosya, kontrol soketinin bulunduğu klasördedir (genelde `$XDG_RUNTIME_DIR`);
- atomik olarak, 0600 izinleriyle yazılır;
- modeli ve Claude oturum kimliğini içerir;
- 30 saniye sonra geçerliliğini yitirir.

Hook yalnızca şunların hepsi sağlanırsa `allow` yanıtı verir:
- işaret sana ait bir dosyadır ve sembolik bağlantı değildir;
- süresi dolmamıştır;
- aynı aipager oturumu ve aynı Claude oturum kimliği içindir;
- modeli, büyük-küçük harf ayrımı olmadan `requested_model` ya da `to_model` değerine eşittir;
- `source` değeri `command`'dır.

Sonra hook, yalnızca bir kez işe yarasın diye işareti atomik bir yeniden adlandırmayla alır. Kontrol tek bir yerel dosyayı okur, soket yok, bekleme yok. Olay aipager'a iletilmez.

Claude Code 2.1.101 ve sonrası tanımadığı hook olaylarını yok sayar, bu yüzden `PreModelSwitch` olmayan eski sürümler etkilenmez. Bir durumda soru yine de görünebilir: `opusplan` modeline ya da o modelden geçiş, Claude Code'un hook'a iki kez sormasına yol açabilir ve işareti yalnızca ilk soru bulur, böylece Claude Code terminalde "Switch model?" diye sorar.

## <a id="the-status-line"></a>Durum satırı

Bu bir hook olayı değildir: Claude Code, durum satırını her yeniden çizdiğinde `statusLine` komutunu, `aipager-statusline` (`aipager.dtach.statusline_notify:main`) programını çalıştırır. Komut:

- Claude Code'un durum JSON'unu `/tmp/claude-status-<session>.json` dosyasına yazar (`sl_tokens` için hook, `aipager status` ve aipager okur);
- aipager'a model, bağlam %'si, maliyet, token sayısı ve eklenen ile silinen satırlarla bir `statusline` datagramı gönderir; bunlar çalışma kartına, `/status` komutuna, Mini App'e, model değişikliğinin gerçekleştiğinin kontrolüne ve bağlam uyarısına gider;
- terminalin durum satırını yazdırır: `[name] Opus 5.5 | 42% ctx | $1.23`.

## <a id="aipagers-own-datagrams"></a>aipager'ın kendi datagramları

Hook olaylarının yanı sıra yardımcı programlar aynı soket üzerinden aipager'a birkaç kendi mesajını gönderir:

| Mesaj | Gönderen | Ne zaman |
|---|---|---|
| `queue_pickup` | hook | `UserPromptSubmit` sırasında: Claude'un hangi Telegram mesajlarını aldığı. |
| `safety_blocked` | hook | `PreToolUse` sırasında, güvenlik kontrolü bir çağrıyı reddettiğinde. |
| `permission_reply_timeout` | hook | `PermissionRequest` beklemesi dokunuş olmadan bitti. |
| `statusline` | durum satırı | Durum satırının her yeniden çiziminde. |
| `hook_memory_cap_hit` | hook, durum satırı | Bir süreç bellek sınırına dayandı. |
| `report_error` | hook, `aipager` komutları | Bir hata oldu: yalnızca hatanın türü ve aipager kodundaki yeri, olayın içeriği asla değil, [sorun raporları](problem-reports.md) için. |

## <a id="the-permission-flow"></a>Bir izin isteğinin yolu

```
claude              aipager-hook             aipager daemon          Telegram
  |                      |                         |                     |
  | PermissionRequest    |                         |                     |
  |--------------------->| opens a reply socket    |                     |
  |                      |------------------------>| busy card becomes   |
  |                      |                         | the prompt          |
  |                      |                         |-------------------->|
  |                      |                         |   you tap Allow     |
  |                      |                         |<--------------------|
  |                      |   allow (reply socket)  | audit log           |
  |                      |<------------------------|                     |
  |  decision: allow     |                         |                     |
  |<---------------------|                         |                     |
  | tool runs            |                         |                     |
```

20 saniye içinde dokunuş olmazsa hook hiçbir şey yazdırmaz ve Claude Code kendi iletişim kutusunu gösterir; daha sonraki bir dokunuş yanıtı oturumun terminali (`dtach -p`) üzerinden o iletişim kutusuna yazar. Her iki durumda da dokunuş denetim kaydına yazılır ve kartın altında yayınlanır (`✅ jim · İzin verildi · @alice · Bash: ls -la /tmp`).

## <a id="see-also"></a>Ayrıca bkz.

- [Mimari](architecture.md): hook alıcısının yeri.
- [Bot komutları → izin istekleri](commands.md#permission-prompts): senin gördüğün taraf.
- [Güvenlik modeli](security.md): güvenlik kontrolünün neyi kapsadığı ve neyi kapsamadığı.
