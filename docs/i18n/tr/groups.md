# Ekip modu ve gruplar

<!-- i18n-source: docs/groups.md @ 6fc084f -->

> Bu sayfa İngilizceden çevrildi; ikisi birbirini tutmazsa İngilizce sürüm geçerlidir. ([English](../../groups.md))

Varsayılan olarak aipager kişisel bir bottur: sen ve bot, tek bir özel sohbette, başka kimse yok. **Ekip modu** onu başka kişilere açar: birkaç geliştiricinin oturumları yönettiği bir Telegram grubu, başka kişilerin botla olan özel sohbetleri ya da ikisi birden. Her sohbetin kendi üyeleri vardır ve her üyenin, orada neye izinli olduğuna karar veren bir rolü vardır: mesaj göndermek, Claude'un sorularını ve izin isteklerini yanıtlamak, oturumları yönetmek ya da yalnızca izlemek.

Ekip modu isteğe bağlıdır. Yalnızca senin özel sohbetinle yapılmış bir kurulum eskisi gibi çalışır.

## <a id="decide-carefully"></a>Dikkatle karar ver

Bir Telegram kişisini bir sohbete eklemek, ona **aipager'ın çalıştığı bilgisayarda kod çalıştırma hakkı** vermektir. Rolüne göre bu kişi şunları yapabilir:

- Claude'un shell komutlarına, dosya düzenlemelerine ve ağ isteklerine dönüştürdüğü mesajlar göndermek;
- izin isteklerini yanıtlamak (İzin ver / Reddet). Claude'un hangi araçlar için soracağına hâlâ senin `~/.claude/settings.json` dosyan karar verir, ama İzin ver'e rolü onaylamaya izin veren herkes dokunabilir;
- oturumları başlatmak, durdurmak, sonlandırmak ve aralarında geçiş yapmak.

Üye listesine, bilgisayara SSH erişimine davrandığın gibi davran. Denetim kaydı ([`~/.claude/aipager-audit.jsonl`](security.md#audit-log)) kimin ne yaptığını yazar, ama ancak olduktan sonra.

## <a id="setup"></a>Kurulum

Her şey `aipager config` ile kurulur. Dosyaları elle düzenlemen hiç gerekmez.

**İlk çalıştırma** (henüz yapılandırma yok). Sihirbaz dört adımda sorar:

1. bot token'ı;
2. botla olan kendi özel sohbetin (kendiliğinden algılanır ya da yapıştırılır);
3. hesabının `owner` olup olmadığı (varsayılan evet; hayır dersen `admin` olur ve senin için güvenlik alt sınırı açık kalır);
4. bağımlılıkları kontrol eder ve Claude Code hook'larını kurar.

Özel sohbetini ilk sohbet olarak yazar. Varsayılan modu sormaz: özetinde "New sessions start in Auto mode; owners and admins can choose Full access in Telegram under /settings → New sessions." yazar (yeni oturumlar Otomatik modda başlar; sahipler ve yöneticiler Telegram'da /settings → Yeni oturumlar içinde Tam erişimi seçebilir). Sonra Add a group, Add a person (DM) ve I'm done seçenekleriyle "Connected. Add a team group or other people now?" diye sorar.

**Sonraki çalıştırmalar** değişiklik menüsünü açar. Menü sohbetlerini gösterir ve şunları sunar:

- **Add a group scope**: üyeleriyle birlikte yeni bir grup (aşağıda).
- **Add a DM scope**: başka bir kişinin botla olan kendi özel sohbeti. Bir özel sohbette tek üye vardır; rolünü sen seçersin (varsayılan `user`; `owner` rolünü yalnızca tek kişilik bir kurulum için seç, örneğin bir arkadaşın kendi konteyneri). `can_manage` olmayan bir rol için (`user` gibi) sihirbaz ardından varsayılan değer hazır doldurulmuş olarak [kök klasörü](#root-folders) sorar (`~/aipager-users/<label>`). Özel sohbeti zaten yapılandırılmış bir kişi için sihirbaz bunu söyler ve hiçbir şeyi değiştirmez: rolü değiştirmek için Edit a member kullan.
- **Edit a scope**: bir grup için **Add a member**; her sohbet için Rename, Edit scope deny_tools, **Set the members' root folder** (bkz. [Kök klasörler](#root-folders)) ve Remove this scope (son sohbet kaldırılamaz). Bir sohbeti kaldırmadan önce oturumlarını sonlandır: çalışan oturumları oraya yazmaya devam eder ve artık hiçbir sohbet onlara Telegram'dan ulaşamaz (bilgisayardan `aipager session ls` ve `aipager session kill <name>` hâlâ ulaşabilir).
- **Edit a member**: Set role, Edit member deny_tools, Remove member (bir özel sohbette ve bir grubun son üyesi için değil). Bir sahip ya da yönetici (ya da `bypass_role_denies` olan bir rolün) için Edit member deny_tools yalnızca engellenen araçların bu role uygulanmadığını bildirir.
- Test bot reachability, View policy, Re-install Claude Code hooks ve Refresh bot token.

### <a id="adding-a-group"></a>Grup ekleme

1. Botu gruba ekle, sonra Add a group scope seç (ya da ilk çalıştırmada Add a group).
2. **Grup kimliği.** Otomatik algılamayı seç ve grupta `/start` gönder ya da kimliği (negatif bir sayı) yapıştır. Sihirbaz bir test mesajı gönderir ve gelip gelmediğini sorar. Zaten yapılandırılmış bir grup burada "This group is already set up. Use Edit a scope → Add a member." ile durur ve hiçbir şey değişmez.
3. Grubun **adı** (sihirbazda ve Mini App'te gösterilir).
4. **Önce sen.** Kendi özel sohbetinin sahibiysen sihirbaz seni grubun ilk üyesi olarak `owner` rolüyle eklemeyi önerir (varsayılan evet; atlanabilir). Grupta bir sahip ya da yönetici olmadan orada kimse Tam erişimi, `/settings` ya da `/update` kullanamazdı.
5. **Diğer üyeler**, tek tek: onları nasıl bulacağın (otomatik algılama ya da bir kimlik veya `@handle` yapıştırmak), adı (sohbette `@label` olarak gösterilir) ve rolü (varsayılan `user`). Rol listesinde bir uyarıyla `owner` vardır: sahip bu bilgisayar üzerinde tam denetime sahiptir; aipager'ın yapılandırması, bot token'ı ve Claude kimlik bilgileri dahil. Onu yalnızca kendine ver. Grupta Tam erişimi, `/settings` ve `/update` kullanması gerekenlere `admin` ver. (Botla olan kendi özel sohbetinde, mesaj gönderip Claude'u yanıtlayabilen herkes, rolü ne olursa olsun kendi oturumları için Tam erişimi kullanabilir.)
6. **İsteğe bağlı tek bir kural:** "Block file edits (Write, Edit) for `user` members in this group? Admins and the owner are not affected." (Bu grupta `user` üyelerin dosya düzenlemesi engellensin mi? Yöneticiler ve sahip etkilenmez.) Varsayılan hayır: `user` zaten yalnızca oturumun proje klasörüne yazar. Evet ile, rolü rol kurallarını aşmayan bir üyenin (`user`, `read_only` ve `bypass_role_denies` olmadan tanımladığın roller) görevindeki her Write ve Edit sormadan reddedilir ve görev durur. O zaman bu işi bir yönetici ya da sahip göndermelidir.
7. **Üyelerin kök klasörü**, `~/aipager-users/group-<label>` hazır doldurulmuş olarak: grubun `user` ve `read_only` üyelerinin oturum başlattığı ve çalıştığı yer (bkz. [Kök klasörler](#root-folders)).

Zaten yapılandırılmış bir gruba birini eklemek için Edit a scope → grup → Add a member kullan. Grupta zaten olan herkes yerini ve rolünü korur.

### <a id="finding-ids-auto-detect"></a>Kimlikleri bulmak (otomatik algılama)

Bir üyeyi otomatik algılama, henüz eklenmemiş birinden gelen en yeni mesajı alır ve onu kullanmadan önce "Is this the person to add?" (Eklenecek kişi bu mu?) diye sorar (hayır dersen aramaya devam eder). Ad olarak o kişinin Telegram kullanıcı adını önerir. Grupta kişinin bota seslenmesi gerekir: onu anmak ya da `/start` göndermek (bkz. [bot neyi okur](#what-the-bot-reads-in-a-group)). Sihirbaz nereye baktığını söyler:

- **"Watching through the running daemon"** (çalışan aipager üzerinden): `aipager start` çalışırken bot için her mesajı aipager alır, bu yüzden sihirbaz aipager'ın `~/.claude/aipager-pending-users.json` dosyasına yazdığını okur. aipager, henüz hizmet vermediği bir grupta bota seslenen kişiyi (grubun kimliği, adı ve türü, ayrıca kişinin kimliği, kullanıcı adı ve adı) ve hizmet verdiği bir grubun üyesi olmayan herkesi kaydeder. Hizmet vermediği bir grupta asla yanıt vermez. Yapılandırılmamış birinden gelen özel bir mesaj bir yanıt alır ve kaydedilmez, bu yüzden aipager çalışırken bir kişinin özel sohbetini eklemek için kimliğini yapıştır (ya da önce aipager'ı durdur).
- **"Watching Telegram directly"** (doğrudan Telegram'da): aipager durduğunda sihirbaz botun son mesajlarını Telegram'a kendisi sorar.

İlk çalıştırmada kendi özel sohbetin her zaman Telegram'dan okunur. Anonim bir grup yöneticisinin mesajı ya da bir grup veya kanal adına yayımlanan bir mesaj sayılmaz: gönderen kimliği (1087968824, 136817688 ya da 777000) bu şekilde yayımlayan herkes için aynıdır, bu yüzden sihirbaz onu, yazılmış ya da bulunmuş olsun, reddeder. Kişiden kendi hesabıyla yazmasını iste.

### <a id="where-the-config-lives"></a>Yapılandırma nerede durur

Sihirbaz `~/.config/aipager/aipager.yaml` dosyasını yazar (izinler 0600): bot token'ı, botun hizmet verdiği sohbetler ve her sohbetin rolleriyle birlikte üyeleri. Her rolün neye izinli olduğu `~/.config/aipager/policy.yaml` dosyasında durur ve bu senin dosyandır: sihirbaz onu asla yazmaz (bkz. [Kurallar nasıl çalışır](#how-rules-work)). Eski `team.yaml` biçiminde yapılandırılmış kurulumlar kendiliğinden taşınır: başlarken aipager ya da bir sonraki çalıştırmada sihirbaz.

### <a id="live-reload"></a>Yeniden başlatmadan yeniden yükleme

Değişiklik menüsünden sohbetlerde ya da üyelerde yapılan her değişiklikten sonra (bir grup ya da özel sohbet eklemek veya kaldırmak, bir üye eklemek veya kaldırmak, bir rol ayarlamak, `deny_tools` değiştirmek, bir sohbeti yeniden adlandırmak), ayrıca ilk çalıştırmanın sonunda eklenen bir grup ya da kişiden veya devam ettirdiğin yarım kalmış bir gruptan sonra (orada eklenen her şey için tek bir yeniden yükleme), `aipager config` çalışan aipager'a anında bir yeniden yükleme (**SIGUSR1**) gönderir ve "Scopes reloaded live (no daemon restart needed)" yazar. Yeniden yükleme `aipager.yaml` ve `policy.yaml` dosyalarını baştan okur. Diskteki yapılandırma reddedilecek olursa sihirbaz nedeniyle birlikte "Not applied" yazar ve sen düzeltene kadar aipager önceki yapılandırmayı tutar. Sihirbaz hangi sürecin aipager olduğunu kesin olarak söyleyemezse onu yeniden başlatmanı söyler.

Yeniden yüklemenin hemen uyguladıkları:

- her sohbette botu kimin, hangi rolle ve hangi kurallarla kullanabileceği;
- yeni bir grup ya da yeni bir özel sohbet mesaj almaya başlar ve kendi `/` menüsünü alır; kaldırılan biri durur (oturumları sen sonlandırana kadar çalışmaya devam eder);
- artık bu sohbette yazmasına izin verilmeyen birinden gelen bekletilmiş bir mesaj (bir isteğin yanıtını ya da başkasının görevinin bitmesini bekleyen) atılır: 🤷 alır ve bot “Claude'a gönderilmedi: @bob artık burada yazamaz.” diye yanıt verir;
- kaldırılan ya da yetkilerini kaybeden bir kişinin süren görevi, görevin sonuna kadar yalnızca eski ve yeni kurallarından en sıkı olanını korur (kaldırılan bir kişi için kısıtlı alt sınır). Terminalde yazılmış bir görev, ona bir Telegram mesajı katılmadıkça değişmez: aipager kimden olduğunu kaydetmez, bu yüzden böyle bir görev, bu sohbetin herhangi bir sahibi (ya da bir özel sohbette, üyesi) yetkilerini kaybettiğinde daralır. aipager yeniden başladığında zaten süren bir görev (hâlâ çalışıyor ya da bir soru veya izin bekliyor), sohbetinin yetkilerini kaybeden her üyesi için daralır. Bir yükseltme süren bir görevi asla genişletmez: yeni yetkiler o kişinin bir sonraki görevinden itibaren geçerlidir.

Yalnızca yeni bir **bot token'ı** yeniden başlatma gerektirir (`aipager config` bunu Refresh bot token sonrasında söyler). aipager çalışmıyorken yeniden yükleme gönderilmez: bir sonraki `aipager start` yeni yapılandırmayı okur.

Elle yeniden yüklemek için (örneğin `policy.yaml` dosyasını düzenledikten sonra) sinyali yalnızca aipager sürecine gönder. Arka plan hizmeti altında:

```sh
pid=$(systemctl --user show -p MainPID --value aipager.service)
[ "${pid:-0}" -gt 0 ] && kill -USR1 "$pid"
```

(Kontrol önemlidir: hizmet çalışmıyorken `MainPID` değeri `0`'dır ve `kill -USR1 0` kendi shell'inin süreçlerine sinyal gönderirdi.)

`aipager start` ile kendin başlattığın bir aipager için o sürece `kill -USR1 <pid>` gönder. `pgrep -f 'aipager start'` komutunun bulduğu her şeye sinyal gönderme (aipager'ın çalıştığı shell ya da konteyner de eşleşir ve SIGUSR1 onu işlemeyen bir süreci durdurur) ve `systemctl --user kill` komutunu `--kill-whom=main` olmadan kullanma (hizmetin grubundaki oturumları da durdururdu).

Yeniden yükleme anında `aipager.yaml` ya da `policy.yaml` bozuksa veya `aipager.yaml` yoksa aipager günlüğe bir uyarı yazar ve önceki yapılandırmayı bellekte tutar. Bir yazım hatası ya da kenara alınmış bir dosya ne seni dışarıda bırakabilir ne de birini içeri alabilir: yeniden yükleme, sohbetleri yapılandırılmış bir aipager'ı asla kişisel moda döndürmez (bunu yalnızca yeniden başlatma yapar).

### <a id="bot-admin-rights-and-privacy-mode"></a>Botun yönetici hakları ve gizlilik modu

**Botu, mesaj sabitleme hakkıyla grubun yöneticisi yap.** Grubun durum satırını sabitlemek için buna ihtiyacı vardır (bkz. [Herkesin gördükleri](#what-everyone-sees)). Bu olmadan grup çalışır ama durum satırı olmaz; bu hakkı bota sonradan verirsen satırın görünmesi için aipager'ı yeniden başlat.

Grubun yöneticisi olan bir bot, BotFather'daki gizlilik modu ne olursa olsun gruptaki her mesajı alır. Bu güvenlidir: aipager yalnızca [bot neyi okur](#what-the-bot-reads-in-a-group) bölümünde listelenen mesajları işler ve geri kalanını yok sayar, bu yüzden gruptaki sohbetler asla bir oturuma girmez. Yönetici olmayan bir bot varsayılan olarak gizlilik modundadır (açık bırak) ve o zaman Telegram ona grubun yalnızca bazı mesajlarını iletir; bir mesajın ulaşmadıysa botun mesajlarından birini yanıtla ya da bir komut kullan. Gizlilik ayarı aipager'ın işlediği şeylerde hiçbir şeyi değiştirmez.

Telegram'da grup yöneticisi olmak bir kişiye aipager'da hiçbir şey kazandırmaz. Kimin neye izinli olduğunu yalnızca `aipager.yaml` içindeki rol belirler.

### <a id="when-telegram-upgrades-the-group"></a>Telegram grubu yükselttiğinde

Telegram, bir grup herkese açık yapıldığında, konular açıldığında, 200 üyeyi geçtiğinde ya da bazı yönetici hakları değiştiğinde sıradan bir grubu süper gruba çevirir ve bu yükseltme gruba yeni bir sohbet kimliği verir. aipager onu kendiliğinden izler: yükseltmeyi gördüğünde (gruptaki Telegram bildirimi ya da eski kimliğe ilk gönderim) grubun `aipager.yaml` içindeki kaydını yeni kimliğe taşır, onunla birlikte oturumları, mesaj adreslerini ve grubun `/settings` ayarlarını taşır ve gruba tek bir satır yazar: “Telegram bu grubu yükseltti. aipager da onunla birlikte taşındı, yapman gereken bir şey yok.” Sabitlenmiş durum satırı yükseltilmiş grupta baştan başlar. aipager'ın günlüğünde iki kimlikle bir uyarı olur.

aipager'ın içindeki oturum adları eski kimliği korur (onları hiç görmezsin) ve `aipager.yaml` taşınma hakkında kısa bir `chat_migrations` kaydı tutar, böylece yükseltmeden önce başlatılan bir oturum, yeniden başlatmadan sonra bile yükseltilmiş gruba ait olmaya devam eder. Eski kimliği taşıyan iç oturum adı (`/name__g<old id>`) yükseltilmiş grupta çalışır, başka hiçbir yerde çalışmaz. aipager yükseltme sırasında durmuşsa bir sonraki başlangıçta fark eder. Yükseltme anında süren bir görev biter ve yanıtı yükseltilmiş gruba ulaşır, ama ilerleme kartı o görevin sonuna kadar artık gösterilmez. Yeni kimlik yapılandırmada zaten ayrı bir sohbetse hiçbir şey taşınmaz ve günlük bunu söyler; ikisinden birini `aipager config` ile kaldır. `aipager config` içindeki “Test bot reachability”, yükseltilmiş bir grupla karşılaştığında yeni kimliği gösterir.

Her başlangıçta aipager her grubu bir kez kontrol eder. Telegram grubun bulunamadığını ya da botun onu kullanamadığını söylerse (grup silindi, bot çıkarıldı ya da aipager dururken grup yükseltildi ve Telegram hangi kimliğe olduğunu söylemedi), aipager'ın günlüğünde sohbetin adıyla tek bir uyarı olur: botun hâlâ grupta olduğunu kontrol et ve yükseltildiyse grubu `aipager config` içinde Add a group scope ile yeniden ekle (yeni kimliğini alır; üyelerini yeniden ekle), sonra eskisini Edit a scope → Remove this scope ile kaldır (eski kimlik artık çalışmaz). aipager hiçbir şey yayımlamaz ve yeniden denemez.

### <a id="forum-topics-are-not-supported"></a>Forum konuları desteklenmiyor

Ekip başına tek bir sıradan grup (konusuz) kullan. Konuları açık bir grupta aipager yine çalışır, ama konuya bağlı kalmaz:

- bir komuta ya da mesaja verilen yanıt (örneğin `/status` ya da “Hangi oturum?” sorusuna verilen yanıt) gönderildiği konuda kalır;
- bildirimler, çalışma kartları, yanıtlar, izin istekleri ve sabitlenmiş durum satırı gruba konusuz yayımlanır, bu yüzden General içinde kalabilir;
- her kişinin konu başına değil, bütün grup için tek bir güncel oturumu vardır.

Konuları açmak ayrıca sıradan bir grubu süper gruba çevirir; aipager bunu izler (yukarıda).

## <a id="roles"></a>Roller

Dört yerleşik rol (bkz. `aipager/safety.py`). Sayılan, bir kişinin **işlem yaptığı sohbetteki** rolüdür. Kimse üyeleri ya da rolleri Telegram'dan veya Mini App'ten değiştiremez: bunu yalnızca bilgisayardaki `aipager config` yapar ve böylece bu, sahibe kalır.

| | `owner` | `admin` | `user` | `read_only` |
|---|---|---|---|---|
| Mesaj göndermek (`can_prompt`) | ✅ | ✅ | ✅ | ❌ |
| Claude'u yanıtlamak: İzin ver, Reddet, sorular (`can_approve`) | ✅ | ✅ | ✅ | ❌ |
| Yönetmek: Tam erişim, grubun `/settings` ayarları, `/update`, sesli mesaj kurulumu (`can_manage`) | ✅ | ✅ | ❌ (Tam erişim: kendi özel sohbetinde ✅) | ❌ |
| Üyeleri ve rolleri Telegram'dan ya da Mini App'ten değiştirmek | ❌ | ❌ | ❌ | ❌ |
| Rolün yasak kurallarını aşar (`bypass_role_denies`) | ✅ | ✅ | ❌ | ❌ |
| Güvenlik alt sınırını aşar (`bypass_safety`) | ✅ | ❌ | ❌ | ❌ |
| Bash ve kod çalıştıran diğer araçlar | ✅ | ✅ (kurallar elden geldiğince) | ❌ | ❌ |
| Herhangi bir slash komutu | ✅ | ✅ | yalnızca “Komutlar” klavyesinin komutları, `/compact` ve model değişikliği | `user` gibi |
| Oturumların başladığı yer (`/new`, Devam ettir, Yeniden başlat, mod değişikliği) | her yer | her yer | yalnızca sohbetin [kök klasörünün](#root-folders) içinde | `user` gibi |
| Dosya yazmanın gittiği yer | her yer | güvenlik alt sınırının izin verdiği yer | oturumun proje klasörü (kök klasörün içindeyse) ve scratchpad'i, asla ev klasörü | `user` gibi |
| Ev klasöründe okuma | güvenlik alt sınırının izin verdiği yer | güvenlik alt sınırının izin verdiği yer | yalnızca kök klasörün içinde (bilgisayarın diğer klasörleri eskisi gibi, diğer üyelerin kök klasörleri asla) | `user` gibi |
| Kimlik bilgisi dosyaları (`~/.ssh`, `~/.git-credentials`, ...) | okunur | okunur | asla (varsayılan) | asla (varsayılan) |
| `Grep`/`Glob` | her yer | aipager'ın korunan klasörleri dışında her yer (onlardan biri olan, içinde duran ya da onları içeren bir klasörde değil) | oturumun proje klasörü ve scratchpad'i | `user` gibi |

- **owner**: güvenliğin yerleşik alt sınırı dahil tam denetim. Tam olarak bir sahip olmalı: bilgisayarı yöneten kişi.
- **admin**: sahibin Telegram'da ve Mini App'te yapabildiği her şey: oturumları Tam erişim moduna almak (ve onda başlatmak), bir oturuma Tam erişimde devam ettirmek, grubun `/settings` ayarlarını değiştirmek, `/update` çalıştırmak, sesli mesaj desteğini kurmak, herhangi bir slash komutu göndermek. Yönetici ayrıca rolün yasak kurallarını aşar (sohbetin ve üyenin `deny_tools` değerleri yöneticilere uygulanmaz). İki şey sahibe kalır:
  - üyeleri ve rolleri değiştirmek: bunu yalnızca bilgisayardaki `aipager config` yapar ve Telegram'daki ya da Mini App'teki hiçbir şey bunu yapamaz;
  - güvenlik alt sınırını aşmak (aipager'ın yapılandırması ve bot token'ı, Claude Code kimlik bilgileri, aipager'ın kontrol dosyaları). Bir yöneticinin `Grep` ve `Glob` araçları başka her yerde arama yapabilir, ama bunlardan biri olan, bunlardan birinin içinde duran ya da bunlardan birini içeren bir klasörde değil: `~`, `/` ya da `/tmp` üzerinde (ya da ev klasöründeki bir oturumdan klasörsüz) `Grep` reddedilir; kalıbı bunlardan birinde başlayan bir `Glob` da öyle (`/home/you/.config/aipager/*` gibi). Bunun yerine proje klasörünün içinde ara. `Grep` aracı olmayan Claude Code sürümlerinde Claude Bash ile arar; orada da ev klasörünün tamamında ya da `/` üzerinde özyinelemeli arama veya listeleme (`grep -rn x ~`, `rg x $HOME`, `find / -name x`) elden geldiğince reddedilir (ev klasöründen ya da altından çalıştırılan klasörsüz ya da `..` ile yapılan bir arama görünmez), projelerde arama ise reddedilmez, ev klasörü yolunu ARAMA dahil (`grep -rn "$HOME" src`) ([güvenlik](security.md#team-mode-enforcement)).

  Yöneticinin Bash'i kalır ve bir shell ile alt sınırın komut kalıpları elden geldiğince çalışır, bir sınır değildir: bir kişiyi ancak bu bilgisayarda bir shell ile güvenilebilirse yönetici yap.
- **user**: yasak kuralları geçerlidir. Bash yok ve kod çalıştıran başka hiçbir araç yok (PowerShell, Monitor, zamanlanmış mesajlar, workflow'lar; liste `safety.CODE_EXECUTION_TOOLS`), diğer oturumlara `SendMessage` yok, worktree değişikliği yok. Dosyalar yalnızca oturum klasörüne ve onun Claude Code scratchpad'ine yazılır, bu klasörün `.claude/`, `.git/` ya da `.mcp.json` öğelerine asla (her biri komut çalıştırır). `Grep` ve `Glob` yalnızca bu klasörlerde, basit göreli glob'larla arar. `user` oturumları yalnızca sohbetin [kök klasörünün](#root-folders) içinde başlatır, devam ettirir, yeniden başlatır ve geçirir, ev klasöründe onun dışındaki hiçbir şeyi okumaz. Ev klasörün, `/` ya da üstündeki bir klasör olan veya kök klasörün dışında duran bir oturum klasörü (kök klasörlerden önce ya da başka biri tarafından başlatılmış bir oturum) sayılmaz: orada `user` yalnızca scratchpad'e yazabilir ve orada arayabilir. Okuma korunan yol kurallarına tabidir ve kimlik bilgisi dosyaların asla okunmaz: `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config/gh`, `~/.git-credentials`, `~/.netrc`, `~/.docker/config.json`, `~/.kube`, `~/.config/gcloud`, `~/.azure`, `~/.password-store`, `~/.pgpass`, `~/.npmrc` ve `~/.pypirc`. Slash komutları: yalnızca “Komutlar” klavyesinin komutları, `/compact` ve model değişikliği (bkz. [Bir mesaj kimin yetkileriyle çalışır](#who-a-message-runs-as)).
- **read_only**: izleyiciler. Gruptaki her şeyi görürler ve `/status`, `/diff`, `/whoami`, `/help`, `/start` ve `/app` kullanabilir, `/settings` ayarlarına bakabilir ve birinin açtığı `/resume` listesinde gezinebilirler. Kendi metin mesajları, sesli mesajları ve dosyaları hiçbir yere gönderilmez. Sohbette gönderdikleri ilk şey, rollerinin `read_only` olduğunu söyleyen bir yanıt alır; bundan sonra bot o sohbette onlar için, aipager yeniden başlayana kadar sessiz kalır (yanıt yok, tepki yok). Mesajlarından biri yine de bir oturuma ulaşırsa diye `user` ile aynı araç ve yazma sınırlarına sahiptirler.

### <a id="custom-roles"></a>Özel roller

`policy.yaml` yerleşik bir rolün herhangi bir alanını değiştirebilir ya da yeni roller tanımlayabilir. Ayarladığın bir alan yerleşik değerin yerine geçer. Alanlar:

- `can_prompt`, `can_approve`: mesaj göndermek; Claude'u yanıtlamak.
- `can_manage`: Tam erişimi kullanmak, grubun `/settings` ayarlarını değiştirmek, `/update` çalıştırmak ve sesli mesaj desteğini kurmak (yerleşik: `owner` ve `admin` true, `user` ve `read_only` false). Üyeleri ya da rolleri değiştirmeye asla izin vermez ve güvenlik alt sınırını asla aşmaz.
- `bypass_role_denies`, `bypass_safety`: tabloya bak.
- `deny_tools`, `allow_tools`, `deny_bash_patterns`, `deny_paths_no_access`, `deny_paths_no_write`: bkz. [Kurallar nasıl çalışır](#how-rules-work).

Sıfırdan tanımlanan bir rol `can_prompt` ve `can_approve` açık, `can_manage` ve iki istisna kapalı, kimlik bilgisi dosyaları `deny_paths_no_access` listesinde ve sen `deny_tools` ayarlayana kadar **yasaklanmış araç olmadan, yani Bash ile** başlar (`aipager doctor` bir üyede olduğunda bir kez uyarır). Yazma ve arama sınırı, `bypass_safety` ya da `bypass_role_denies` olmayan her rol için geçerlidir. [Kök klasör](#root-folders), istisnaları ne olursa olsun `can_manage` olmayan her rol için geçerlidir: `bypass_role_denies` ya da `bypass_safety` ile ama `can_manage` olmadan tanımladığın bir rol, kök klasörünün (ve scratchpad'inin) içinde her yere yazar ve arar, ev klasöründe onun dışındaki hiçbir şeyi okumaz ve başka hiçbir yerde de (kök klasörünün dışında çalışan bir oturumda `bypass_safety` ile yalnızca kendi scratchpad'ine yazar). Sınırsız olanlar yalnızca sahipler ve yöneticilerdir.

### <a id="root-folders"></a>Kök klasörler

Her sohbetin bir üye kök klasörü vardır: o sohbette rolü `can_manage` olmayan üyelerin (yerleşik `user` ve `read_only` ve onsuz tanımladığın roller) oturumlarını başlattığı ve tuttuğu klasör. Sahipleri ve yöneticileri tutmaz. Bir özel sohbetin kök klasörü o kişiye aittir; bir grubun kök klasörü grubun kısıtlı üyeleri arasında paylaşılır. İki sohbet, sen bilerek onlara aynı klasörü vermedikçe asla birini paylaşmaz (sihirbaz bunu onaylamanı ister).

- **Nasıl ayarlanır.** `aipager config`, `can_manage` olmayan bir rol için bir grup ya da özel sohbet eklediğinde varsayılanı hazır doldurulmuş olarak onu sorar, Edit a scope → **Set the members' root folder** ise onu değiştirir (anında yeniden yüklemeyle hemen uygulanır). Mevcut yapılandırma her sohbetin kök klasörünü, ayarlanmamışsa `(default)` ile gösterir.
- **Varsayılan**, bir özel sohbet için `~/aipager-users/<label>` (kişinin adı) ve bir grup için `~/aipager-users/group-<label>`. Ad küçük harfe çevrilir ve harf, rakam, `.`, `_` ve `-` dışındaki her şey `-` olur; iki sohbet aynı klasörü alacak olursa her birine kendi sohbet kimliği eklenir (`sara-123456`). Klasör (ve yeniyse `~/aipager-users`), bir üye ilk kez `/new` ya da Mini App'teki “Yeni oturum” formunu açtığında, yalnızca senin açabilmen için 0700 izinleriyle oluşturulur. Varsayılan asla taşınmaz: `aipager config` içindeki bir değişiklik (başka bir sohbetin kök klasörü, kaldırılan bir sohbet, yeni bir ad) bir sohbetin varsayılan klasörünü değiştirecek olursa sihirbaz, sahip olduğu klasörü o sohbetin kök klasörü olarak yazar.
- **Sihirbazın reddettikleri**: göreli bir yol, joker karakterler, `/`, ev klasörün ya da üstündeki bir klasör, aipager'ın korunan klasörlerinden ya da kimlik bilgisi dosyalarından biri olan, onlardan birinin içinde duran ya da onlardan birini içeren bir klasör (`~/.claude`, `~/.config/aipager`, `~/.config`, `~/.ssh`, ...) ve Claude Code'un geçici klasörü (`/tmp/claude-*`, her oturumun scratchpad'inin durduğu yer). Henüz var olmayan bir klasör uygundur. Sisteminin program çalıştırdığı bir klasör için (`~/bin`, `~/.local/bin`, `~/.config/systemd`, `~/.config/autostart` ya da bunların içindeki bir klasör) onay ister.
- **Oturum başlatma.** `/new` içindeki ve Mini App'teki “Klasör” sayfası, “Varsayılan klasör” satırı olmadan, kök klasörü ve hemen içindeki klasörleri (gizli olanlar ve dışarı çıkan bir bağlantı hariç) gösterir; birini seçmezsen yeni oturum kök klasörde başlar. “Yeni klasör”, seçilenin içinde bir klasör oluşturur. Diğer her şey “Rolün yalnızca kendi klasöründe (~/...) ya da onun içindeki bir klasörde oturum başlatabilir.” alır.
- **Onun dışındaki oturumlar.** Klasörü kök klasörün dışında olan bir oturuma devam ettirmek, onu yeniden başlatmak, Değiştir ya da modunu değiştirmek “Bu oturum klasörünün (~/...) dışında çalışıyor, bu yüzden rolün ona devam edemez, onu yeniden başlatamaz ya da modunu değiştiremez. Bir yöneticiye sor.” alır. Böyle çalışan bir oturuma mesajlar gönderilmeye devam eder ve güvenlik hook'u görevi tutar: orada yalnızca scratchpad'e yazar.
- **Hook görevi neyle sınırlar.** Kısıtlı bir üyenin görevi yalnızca oturum klasörüne (kök klasörün içindeyse) ve onun scratchpad'ine yazar, ev klasöründe kök klasörün dışındaki hiçbir şeyi (kendi scratchpad'i ve yanıt dosyası dışında) okumaz, üzerinde `Grep` ve `Glob` yapmaz ve nerede olursa olsun başka bir sohbetin kök klasörüne asla dokunmaz. Ev klasörünün dışındaki klasörler (`/usr`, `/etc`, ...) okunabilir kalır, korunan klasörler yasak kalır ve `user` için `Bash` yasak kalır. Bir görevde farklı kök klasörleri olan iki üye karıştığında daha dar olan kazanır; iç içe olmayan iki tanesi yalnızca oturumun kendi klasörünü bırakır.

`user` rolüne Bash'i geri vermek için (yalnızca bir shell ile güvenilebilecek kişiler için), onu `allow_tools` içinde adlandır (bu durumda o rol için ayrıca bir izin listesi olur) ya da `deny_tools` değerini değiştir:

```yaml
roles:
  user:
    # Bash olmadan yerleşik liste
    deny_tools: [PowerShell, Monitor, REPL, Workflow, CronCreate,
                 RemoteTrigger, AppifactRepl, self_hosted_runner_spawn_local,
                 self_hosted_runner_requeue_session, SendMessage,
                 EnterWorktree, ExitWorktree]
```

O zaman `aipager doctor` uyarır: `role user has Bash: its safety rules are best-effort, not a boundary`. `policy.yaml` dosyanda `user` için `deny_tools` bu varsayılan gelmeden önce ayarlandıysa listen onun yerine geçmeye devam eder, bu yüzden sen geri ekleyene kadar bu rolün Bash'i vardır; doctor uyarısı bunu söyler. Bu sınırların neyi kapsamadığı için bkz. [güvenlik → kurallar ekipte nasıl uygulanır](security.md#team-mode-enforcement).

## <a id="talking-to-the-bot-in-a-group"></a>Grupta botla konuşmak

### <a id="what-the-bot-reads-in-a-group"></a>Bot grupta neyi okur

Grupta aipager yalnızca şunlardan biri olan bir mesajı işler:

- bir **komut**: `/status`, `/x1 fix the tests` ya da komuttan sonra `@aipagerbot` ile aynısı (`/x1@aipagerbot`); Telegram bunu, komutu menüden seçtiğinde ekler. Başka bir bot için bir komut (`/x1@otherbot`) yok sayılır;
- **botun mesajlarından birine bir yanıt**;
- **botu anan bir mesaj**: `@aipagerbot fix the tests`;
- gruptaki **botun klavyesine bir dokunuş**. Grupta her klavye düğmesi küçük bir kareyle başlar (`▫️ Stop`, `▫️ Konuşmayı temizle`) ve yalnızca bu işaretli metin dokunuş sayılır. Bir sözcüğü öylece yazmak sıradan bir konuşmadır: `Stop`, `Konuşmayı temizle`, `Opus` ya da bir oturum adı yazan bir üye hiçbir şeyi değiştirmez. Bir klavye sözcüğünü klavye olmadan bilerek kullanmak için bota seslen: `@aipagerbot Stop` ya da onun mesajlarından birine yanıt olarak `Stop`.

Geri kalan her şey (üyelerin kendi aralarında konuşması, bota yanıt olmayan bir fotoğraf ya da sesli mesaj) ne tepki ne yanıt alır. Bot grupta yönetici olsun ya da olmasın aynıdır. Düzenlenen mesajlar her yerde yok sayılır: bir mesajı düzenlersen hiçbir şey olmaz; onu yeniden gönder.

Bir fotoğraf, dosya ya da sesli mesaj da aynı şekilde çalışır: bota yanıt olarak gönder ya da açıklamada botu an. Bir albüm için açıklamayı ilk öğeye koy: albümün açıklama, anma ya da yanıt taşıyan öğeden sonra gelen öğeleri alınır, ondan öncekiler alınmaz.

**Anma kaldırılır.** `@aipagerbot fix the tests`, Claude'a `fix the tests` olarak ulaşır (Claude Code `@word` ifadesini bir dosya olarak okurdu). `@aipagerbot status` ve `@aipagerbot x2`, özel sohbette `status` ya da `x2` yazmak gibi çalışır, tek başına `@aipagerbot` ise mesajlarının nereye gittiğini gösterir. Anmadan sonra bir oturum komutu çalışır (`@aipagerbot /x1 fix it`), aipager'ın kendi komutlarından biri ise çalışmaz: `@aipagerbot /stop` şunu alır: “/stop komutunu botu anmadan, tek başına gönder.”

**Yalnızca üyeler.** Grubun üyesi olmayan bir kişi, sana iletebilmesi için Telegram kimliğiyle birlikte tek bir yanıt alır (“🚫 Bu sohbette izin verilenler listesinde değilsin.”) ve `aipager config` için kaydedilir; bundan sonra bot, aipager yeniden başlayana kadar onu yok sayar. `read_only` rolündeki bir üye rolünü bir kez öğrenir (bkz. [Roller](#roles)).

**Anonim yöneticiler kendi hesaplarıyla yazmalı.** “Remain anonymous” açık bir grup yöneticisi ya da bir grup veya kanal adına yayımlanan bir mesaj kimin yazdığını söylemez: Telegram tüm anonim yöneticiler için aynı göndereni gösterir. aipager böyle bir mesaja kimsenin rolünü veremez, bu yüzden “Botu kullanmak için kendi hesabınla yaz.” diye yanıt verir (aipager yeniden başlayana kadar sohbet başına bir kez) ve başka hiçbir şey yapmaz. Asla bekleyen bir kullanıcı olarak kaydedilmez ve sihirbaz bu ortak kimliği reddeder. Grubun yönetici ayarlarında kendin için “Remain anonymous” seçeneğini kapat ya da kendi hesabınla yaz.

### <a id="talking-to-a-session-in-a-group"></a>Grupta bir oturumla konuşmak

Bir oturumun mesajlarından birini (yanıtını, kartını) yanıtla, mesajın o oturuma gider. Ya da `/x1 mesajın` ya da `/x1` ile başlayan bir açıklama gönder.

**Her kişinin kendi güncel oturumu vardır**: en son geçtiği (`/x1`), mesaj gönderdiği, yanıtladığı ya da başlattığı oturum. Başka bir üyenin oturumunun yanıtı onu değiştirmez, başkalarının mesajları da değiştirmez. Bir anma, bir sesli mesaj, bir şablon ya da bir klavye komutu senin kendi güncel oturumuna gider ve bir klavye komutu ya da şablon yanıtta onun adını verir (“🧹 /clear gönderildi: x1”). Çıplak `/stop`, `/mode`, `/kill`, `/restart`, `/rename`, `/delete` ya da `/diff` yalnızca senin kendi güncel oturumuna (her zamanki onayla) ve yalnızca komut onun için anlamlıysa (`/stop` için çalışıyor, `/delete` için sona ermiş) etki eder; aksi halde tek bir oturum kalmış olsa bile her zaman bir liste gösterir, çünkü o oturum başkasına ait olabilir. `/clearqueue` ve `/now` senin güncel oturumuna etki eder. `/kill x1` ve adı olan diğer komutlar eskisi gibi o oturuma etki eder.

- Güncel oturumun yoksa ve birden fazla oturum çalışıyorsa bot “Hangi oturum? Mesajlarından birini yanıtla ya da /x1 yazıp ardından mesajını ekle.” diye her oturum için bir düğmeyle sorar ve hiçbir şey göndermez. Yalnızca biri çalışıyorsa mesaj oraya gider.
- Kendi güncel oturumun sona erdiyse mesaj hâlâ çalışan tek oturuma gitmez (başkasına ait olabilir): bot, çalışan her oturum için bir düğmeyle ve x1'e devam edilebiliyorsa “▶️ Devam ettir: x1” ile “x1 sona erdi. Hangi oturum?” diye yanıt verir ve hiçbir şey göndermez.
- Bu sorulardan herhangi birinde bir oturum düğmesine dokunmak, söz konusu mesajı da (metin, sesli mesajın yazıya dökümü ya da dosya) oraya gönderilmiş gibi o oturuma gönderir ve soru “x2 oturumuna gönderildi.” olarak değişir. Düğmeleri yalnızca mesajı gönderen kişi ve yalnızca on dakika boyunca ve aipager yeniden başlayana kadar kullanabilir: bundan sonra düğme yalnızca oturumu senin güncel oturumun yapar (“Bu mesaj çok eski olduğu için gönderilmedi; tekrar gönder.”). ▶️ Devam ettir x1'i geri getirir ama hiçbir şey göndermez: mesajını yeniden gönder.
- Grupta sabitlenmiş satırda “Mesajlar … oturumuna gider” satırı yoktur, çünkü her kişinin kendisininki vardır.

**Yanıt bekleyen kartlar tek bir kişiye aittir.** Yeni oturum kartı “Bu mesajı bir adla yanıtla; istersen ilk ne yapılacağını da yaz:” der, yeniden adlandırma sorusu “… Bu mesajı yeni adla yanıtla.”. Grupta her biri kime ait olduğunu gösterir (“🆕 Yeni oturum (@alice)”), onu yalnızca o kişi yanıtlayabilir ve iki kişi aynı anda oturum başlatabilir ya da yeniden adlandırabilir. Başka bir üyenin, başkasına ait bir yeni oturum kartına verdiği yanıt “Bu kart @alice adlı kişiye ait. Kendi kartın için /new gönder.” alır ve hiçbir yere gitmez. Yeniden adlandırma sorusu 10 dakika sonra ya da sahibi başka bir şey yaptığı anda beklemeyi bırakır. “x1 hazır” kartı, `/x1`, `/start` ve `/help` yanıtı, oturumun bir mesajını yanıtlamayı ya da botu anmayı söyler (“Onunla konuşmak için x1 oturumundan gelen bir mesajı yanıtla (ya da @aipagerbot adını an).”), çünkü sıradan bir mesaj bota ulaşmaz.

**Herkesin klavyesi kendisine aittir.** Şablonlar, Komutlar, Model › ve « Geri yalnızca onlara dokunan kişinin klavyesini değiştirir: bot o kişinin dokunuşuna yanıt olarak yanıt verir ve başka hiç kimsenin klavyesi kıpırdamaz. « Geri, diğer üyeler ne görüntülüyor olursa olsun, bulunduğun yerden bir düzey yukarı çıkar. `/start` komutunun gönderdiği ana klavye bütün grup için ortaktır ve herkesi ana klavyeye döndürür. Grubun bir oturumu başladığında ya da sona erdiğinde aipager, oturum düğmeleri güncel kalsın diye bu ana klavyeyi gruba bir kez daha gönderir. İşaretli düğmeleri olmayan bir sürümden güncellemeden sonra eski klavyenin bir düğmesine dokunmak yok sayılabilir: gruba yeni klavyeyi vermek için bir kez `/start` gönder. Özel sohbetin klavyesinde işaret yoktur ve eskisi gibi çalışır.

### <a id="the-mini-app"></a>Mini App

`/app`, Mini App'i yalnızca bir özel sohbette açar. Grupta `/` menüsünde yoktur; orada yazılırsa, botla kendi sohbeti yapılandırılmış bir üyeye “📱 Mini App yalnızca özel sohbette çalışır: bota özelden yaz ve orada /app gönder.”, diğer herkese ise “Mini App, botla olan özel sohbetinden açılır. Seni eklemesi için botun sahibine sor.” der.

Mini App bir seferde tek bir sohbet gösterir. Birden fazla sohbette bulunan biri (örneğin kendi özel sohbetinde ve bir grupta) aralarında geçiş yapmak için üstte bir sohbet adları satırı alır; satır kişinin kendi özel sohbetinde, yoksa bulunduğu ilk sohbette açılır ve o telefondaki son seçimi hatırlar. İçindeki her şey (oturumlar, düğmeleri, yayımladığı ayarlar ve bildirimler) seçilen sohbete, kişinin o sohbetteki rolüyle aittir. Tek bir sohbetle fazladan hiçbir şey gösterilmez.

## <a id="who-a-message-runs-as"></a>Bir mesaj kimin yetkileriyle çalışır

Her mesaj, gönderildiği sohbette, **onu gönderenin yetkileriyle** çalışır. Grupta `user` ve kendi özel sohbetinde `owner` olan biri, sohbetler `aipager.yaml` içinde hangi sırada olursa olsun, grupta `user` olarak gönderir, dokunur ve komut çalıştırır.

- Telegram'dan gelen her mesaj, göndereninin kimliğini ve kurallarını oturuma getirir (bkz. [güvenlik → kurallar ekipte nasıl uygulanır](security.md#team-mode-enforcement)). Claude her mesajı kimin gönderdiğini görür (grupta `[via Telegram · @bob · role:user]`), asla ondan önce oturumu kimin yönettiğini değil. Mesajı gönderen kişi, yazılmış, `/x1` ile, bir şablonla ya da bir klavye komutuyla gönderilmiş olsun, oturumu yöneten kişi olur.
- **Bekletilen mesajlar göndereninin kimliğini korur.** Başka birinin görevi (başlattığı bir arka plan ajanı dahil) sürerken ya da başkasının mesajı hâlâ alınmayı beklerken ya da bir izin isteği veya soru açıkken yeni bir mesaj bekletilir (👀) ve bu geçince **kendi göndereninin yetkileriyle** kendi görevi olarak gönderilir. Terminalde yazılmış bir görev sahibin görevi sayılır: ona yalnızca sahibin mesajı (ya da bir özel sohbette, kendi üyesinin mesajı) katılır. Kendi mesajların asla kendi görevini beklemez. Bekledikleri görev durdurulursa ya da engellenen bir araç yüzünden durursa bekletilen mesajlar atılır (🤷): onları yeniden gönder.
- **En sıkı olan kazanır.** Farklı kişilerin mesajları yine de tek bir görevde buluşursa görev, ona katılan herkesin en sıkı birleşimiyle çalışır. Süren bir göreve katılan bir mesaj onu asla genişletmez: görevin geri kalanı, katılan herkesin en sıkı kurallarını korur.
- **Kısıtlı rollerin slash komutları.** Rol kurallarını aşmayan bir rol (`user`, `read_only`, `bypass_role_denies` olmadan tanımladığın roller ve aipager'ın tanımadığı bir gönderen), yalnızca “Komutlar” klavyesinin (`keyboard.json` onu değiştiriyorsa seninkinin) slash komutlarını, `/compact` ve model değişikliğini (`/model <name>`) gönderebilir. Yazılan, `/x1 /deliver ...` ile, bir dosya açıklaması olarak, ilk `/new x1 /review ...` mesajı ya da bir şablon olarak gönderilen diğer her şey “Bu komut yalnızca yöneticiler için.” ve 🤷 alır ve hiçbir şey Claude'a ulaşmaz. Sahipler ve yöneticiler hepsini gönderebilir.
- **Kısıtlı roller ev klasörünün dışında ve kimlik bilgisi dosyalarından uzak kalır.** Bir `user` ya da `read_only` görevi, ev klasörün, `/` ya da üstü olan bir oturum klasörüne asla yazmaz (yalnızca kendi scratchpad'ine) ve [Roller](#roles) bölümünde listelenen kimlik bilgisi dosyalarını asla okumaz.
- **Kısıtlı roller kendi kök klasörlerinde kalır.** `can_manage` olmayan bir rolün görevi, ev klasöründe sohbetin [kök klasörünün](#root-folders) dışındaki hiçbir şeyi okumaz ve onun dışındaki bir oturum klasörüne hiçbir şey yazmaz. Kök klasör, hangi oturuma giderse gitsin, üyenin mesajının geldiği sohbetin klasörüdür.
- **Çıkarılan bir üye yetkilerini hemen kaybeder, ama süren görevini değil.** aipager'ın artık tanımadığı bir gönderen (mesajı beklerken çıkarılmış) kısıtlı alt sınırla gider. Anında yeniden yüklemeden sonra bekletilen mesajları atılır ve hâlâ süren görevi sonuna kadar alt sınırla kısıtlanmış olarak çalışmaya devam eder; onu şimdi bitirmek için `/stop` gönder (bkz. [Yeniden başlatmadan yeniden yükleme](#live-reload)).
- **Başka bir Claude oturumundan gelen bir mesaj, göndereninin yetkilerini taşır.** Bir oturumun diğerine gönderdiği bir mesaj (Claude Code'daki `SendMessage`), onu gönderen görevin yetkileriyle, aipager'ın doğruladığı haliyle gider, asla alan oturumun önceki yetkileriyle değil. aipager'ın doğrulayamadığı bir mesaj en sıkı kurallarla, dosya değişikliği ve internet erişimi olmadan gider (bkz. [güvenlik → Claude oturumları arasındaki mesajlar](security.md#messages-between-claude-sessions)).
- **Engellenen bir araç görevi durdurur.** Bir görev kurallarının yasakladığı bir aracı denediğinde görev hemen “🛑 x1 · Bir güvenlik kuralı engelledi: ... (durduruldu)” ile durur ve o görevdeki sonraki her araç çağrısı da reddedilir.

## <a id="who-can-tap-what"></a>Kim neye dokunabilir

Her düğme, dokunma anında, düğmenin bulunduğu sohbette ona dokunan kişinin rolünü kontrol eder. Düğmeyi oluşturan komutu gönderen kişinin rolü sayılmaz. (Bazı kartlar ayrıca onları kimin istediğini kontrol eder: onay kartları ve `/new` kartları, aşağıda.)

- **Bakmak her üyeye açıktır** (VIEW). `read_only` üyeler `/resume` listesinde gezinebilir, `/status` içindeki “Bitenler” görünümünü, bir oturumun ⋮ menüsünü, modunu ya da diff'ini açabilir, `/settings` ayarlarına bakabilir ve menüleri kapatabilir. İşlem yapamazlar: diğer her düğme “Bu sohbetteki rolün buna izin vermiyor.” der.
- **Claude'u yanıtlamak `can_approve` ister** (APPROVE): İzin ver, Hep izin ver, Reddet, sorunun seçenekleri, Gönder, Devam et ve sabitlenmiş satırdaki Yanıtla. `read_only` rolünde yoktur.
- **Bir oturumla işlem yapmak `can_prompt` ister** (PROMPT): Durdur, Yeniden dene, Sıkıştır, Şimdi gönder, Sonlandır, Yeniden başlat, Yeniden adlandır, Sil, Devam ettir, mod değişikliği, ✍️ konuş, Hepsini kaldır, oturumun kendi ayarları ve `/new` kartları. aipager'ın tanımadığı bir düğme de buna ihtiyaç duyar.
- **Tam erişime geçmek yönetici ister** (MANAGE: `owner`, `admin` ya da `can_manage` olan bir rol, `/mode full` ile aynı kural): Tam erişime geçiren karttaki “Evet, geç” ve “Durdur ve geç” ile Tam erişimde devam ettirme. Sor ile Otomatik arasında geçiş yapmak, oturumla işlem yapabilen herkese açıktır. Tam erişimde olan bir oturuma devam ettirmek (yazılan `/resume x1`, liste, bir `/new` ad kartındaki Devam ettir ya da Mini App) diğer herkes için onu Otomatik modda geri getirir ve yanıt bunu söyler: “♻️ x1 yeniden çalışıyor, Otomatik modda: Tam erişim modu yalnızca yöneticiler için.”
- **Kişi kendi özel sohbetinde, rolü ne olursa olsun Tam erişimi kullanabilir** (`user` dahil; mesaj göndermeyen `read_only` hariç; özel bir rolün `can_prompt` ve `can_approve` gerekir, çünkü Tam erişim Claude Code'un tüm izin isteklerini yanıtlar). Yukarıdaki Tam erişime giden bütün yollar onun için orada çalışır: `/mode full`, mod kartı, `/new !x1`, `/new` kartları, `/resume` ve Mini App. Bunun geçerli olduğu tek yer kendi sohbetidir: bir grupta ve başka herhangi birinin özel sohbetinde Tam erişim yine yönetici ister. Yeni oturumları, o `/settings` → Yeni oturumlar içinde Tam erişimi seçene kadar herkesinki gibi Otomatik modda başlar. Yöneticinin yaptıklarından başka hiçbir şey ona açılmaz (`/update`, sesli mesaj kurulum düğmesi ve grubun `/settings` ayarları yalnızca yöneticiler için kalır). Tam erişimi böyle dağıtmak güvenlidir: Claude Code'u yalnızca Claude Code'un kendi izin isteklerini atlayan `--dangerously-skip-permissions` ile başlatır. aipager'ın PreToolUse hook'u her araç çağrısında çalışmaya devam eder ve üyenin rol kurallarını (güvenlik alt sınırı, klasör sınırı, yasak kuralları, kimlik bilgisi dosyaları) tam olarak Sor ve Otomatik modlarda olduğu gibi uygular.
- **Kurulum yönetici ister** (UPDATE): sesli mesaj kurulum ve yeniden başlatma düğmeleri, `/update` gibi.
- **Grubun `/settings` ayarlarını yalnızca bir yönetici değiştirebilir.** Bakmak herkese açık.
- **Bir onay kartı onu isteyen kişiye aittir.** Grupta, Sonlandır (`/kill`, ⋮ Oturumu sonlandır), Yeniden başlat, Sil ve mod değişikliği için onay kartları, `/kill`, `/restart`, `/delete`, `/mode`, `/stop`, `/rename` ve `/diff` komutlarının gösterdiği oturum listeleri ve Devam ettir'e dokununca açılan “x1 nasıl devam etsin?” kartı (mod düğmeleri) yalnızca komutu gönderen ya da onları açan düğmeye dokunan kişiye yanıt verir. Başka herhangi birinin dokunuşu, İptal dahil, “Bu kart @alice adlı kişiye ait. Kendi kartın için /kill x2 gönder.” (o kartın komutuyla) alır ve hiçbir şeyi değiştirmez. `/mode` kartının kendisi (oturum hangi modda) ortaktır: onun üzerinde dokunulan bir değişiklik, dokunan kişiye ait bir onay açar. `/new` kartları yalnızca `/new` gönderen kişiye yanıt verir. Bir kartın sahibi, kart kullanılana ya da iptal edilene kadar, aipager yeniden başlasa bile hatırlanır (son 4.096 kart). İstisnalar `/new` ad kartı ve `/rename` ad sorusudur: ikisi de her durumda 10 dakika sonra geçersiz olur, bu yüzden yeniden başlatmadan sonra komutu yeniden gönder.
- **Bir düğme yalnızca kendi oturumunun sohbetinde çalışır.** Başka bir yerde dokunulursa “Bu düğme başka bir sohbete ait.” der ve hiçbir şey yapmaz; Devam ettir de her zaman düğmenin adını verdiği oturuma devam ettirir, asla aynı adlı başka bir sohbetin oturumuna değil.
- **Eski düğmeler hiçbir şeyi değiştirmez.** Önceki bir görevden ya da oturum henüz sona erip geri gelmeden önceki bir zamandan kalan bir düğme, şu an sürene etki etmez; bunu söyler (bkz. [komutlar → eski düğmeler](commands.md#stale-buttons)).

## <a id="what-everyone-sees"></a>Herkesin gördükleri

**Claude'a verilen yanıtlar.** Her İzin ver, Reddet ve yanıt, bunu kimin yaptığıyla birlikte sohbette oturumun kartının altında yayımlanır:

```
✅ x1 · İzin verildi · @alice · Bash: ls -la /tmp
🚫 x1 · Reddedildi · @bob · WebFetch: https://example.com
✓ x1 · Yanıtlandı · @carol · Which database? → Postgres
```

**Oturumu kim değiştirdi.** Bir düğmeye dokunmayı yalnızca dokunan kişi görür, bu yüzden grupta oturumu değiştiren her sonuç bunu kimin yaptığını söyler:

```
⚠️ x1 · @bob durdurdu
⏹ @bob, x2 oturumunu sonlandırdı
🔄 @alice, [x1] oturumunu yeniden başlattı.
🗑️ @carol, [x3] oturumunu sildi.
⚙️ @alice oturumu değiştirdi: 🤖 Otomatik mod.
@alice bunu değiştirdi.          (/settings altında)
```

“x1 hazır” kartı, modunu ya da modelini kimin değiştirdiğini söyler (“@bob modeli değiştirdi: Opus.”). Özel sohbette bu sonuç satırları kimsenin adını vermez. Mini App'te yapılan bir değişiklik sohbette “... (Mini App'ten)” olarak yayımlanır (oradan yapılan bir durdurma ayrıca oturumun kartında bunu kimin yaptığını söyler).

**Bir kuralın engellediği araç.** Görev “🛑 x1 · Bir güvenlik kuralı engelledi: Write is in this role's deny_tools (durduruldu)” ile durur. Claude Code, mesajı süren görevi başlatan kişinin kullanamadığı bir araç için izin isterse bot hiçbir istek göstermeden kendisi Reddet yanıtını verir ve o kişinin adını vererek, altında çağrının kısa bir açıklamasıyla `⛔ x1 · Edit engellendi (kişi: @bob, rol: user)` yayımlar. Birkaç kişinin mesajlarıyla başlayan bir görev, içlerinden biri aracı kullanamadığında engellenir ve onun adını verir. Terminalde yazılmış bir görev senindir ve ona bir Telegram mesajı katılmadıkça asla bir üyenin kurallarıyla engellenmez: katılırsa oturumun son mesajını gönderenin kurallarıyla (bu bilinmiyorsa grubun kendi listesiyle) kontrol edilir. aipager görevin kime ait olduğunu bilmediğinde (aipager yeniden başladığında görev zaten sürüyordu), oturumun son mesajını gönderenin kurallarını, o da bilinmiyorsa grubun kendi listesini kullanır.

**Mesajların durumu.** Bir mesajdaki 👀, oturuma gönderildiği (ya da mümkün olana kadar bekletildiği) anlamına gelir; 👍, Claude'un onu aldığı; 🤷 ise Claude almadan önce atıldığı anlamına gelir. `/whoami` bu sohbetteki adını, rolünü ve kurallarını gösterir.

**Sabitlenmiş durum satırı.** Bot sabitleme hakkı olan bir yöneticiyse grubun, yalnızca o grubun oturumlarını listeleyen kendi sabitlenmiş durum satırı vardır (bkz. [komutlar → sabitlenmiş durum satırı](commands.md#the-pinned-status-bar)). Bu hak yoksa satır grup için atlanır: mesaj geçmişte sabitlenmemiş kalmak yerine silinir.

**Diskteki denetim.** `~/.claude/aipager-audit.jsonl`, sonradan her kişinin ne yaptığı yeniden kurulabilsin diye her kararı `user_id`, `username` ve sohbetle birlikte yazar. Sohbetin dili ne olursa olsun her zaman İngilizce yazılır.

**Dil.** Bir grubun, aipager'ın kendi mesajları, kartları ve klavyeleri için tek bir dili vardır ve onu bir yönetici `/language` ile ayarlar; bir yönetici seçene kadar İngilizcedir. Asla kimsenin Telegram uygulamasını izlemez. Yalnızca tek bir kişinin gördüğü şey, bir dokunuştan sonraki kısa bildirim, o kişinin kendi dilinde gelir: botla olan özel sohbetindeki seçimi, yoksa kişi bu sürümden sonra geldiyse Telegram uygulamasının dili, o da yoksa grubun dili. Yalnızca aipager'ın metinleri değişir; Claude her kişiye, ona hangi dilde yazıyorsa o dilde yanıt verir. Bkz. [Diller](commands.md#languages).

## <a id="where-messages-go"></a>Mesajlar nereye gider

- **Her oturum tek bir sohbete aittir ve onunla ilgili her şey oraya gider**: kartları, yanıtları, izin istekleri, dosyaları ve bildirimleri. Bir sohbetteki mesaj asla başka bir sohbetin oturumuna gitmez ve bir oturumun adı yalnızca kendi sohbetinde çalışır: başka bir sohbetin oturumunun adını yazarsan yanıt “Bu adda bir oturum yok” olur. aipager sohbetleri yapılandırılmamışken çalışırken başlatılan bir oturum, `aipager config` onları ekler eklemez kendi sohbetini alır; bir oturumun sohbeti olmadığı sürece hiçbir sohbet onu göstermez ve onunla işlem yapamaz.
- **Terminalde başlatılan oturumlar** (`aipager session <name>`) sahibin özel sohbetine aittir: üyesinin rolü `owner` (`bypass_safety` olan rol) olan özel sohbet. Birkaç özel sohbet varsa ve hiçbiri sahibin değilse listedeki ilk özel sohbettir; hiç özel sohbet yoksa ilk sohbettir. Özel sohbeti olan bir kurulumda asla bir gruba gitmezler.
- **Süper gruba geçiş kendiliğinden izlenir** (bkz. [Telegram grubu yükselttiğinde](#when-telegram-upgrades-the-group)).

## <a id="aipageryaml-schema-team-parts"></a>`aipager.yaml` yapısı (ekip için bölümler)

```yaml
schema_version: 3
bot_token: "…"

scopes:
  - kind: dm
    chat_id: 12345       # kendi özel sohbetin: kimliği kullanıcı kimliğinle aynı
    label: owner DM
    members:
      - id: 12345
        label: alice
        role: owner
  - kind: group
    chat_id: -100123456789
    label: team
    deny_tools: [Write, Edit]   # isteğe bağlı: “Block file edits” yanıtı
    root: /home/alice/aipager-users/group-team   # üyelerin kök klasörü
    members:
      - id: 12345        # Telegram kullanıcı kimliği (ad DEĞİL, sohbet kimliği DEĞİL)
        label: alice     # kişinin sohbette nasıl gösterildiği (@alice)
        role: owner
      - id: 67890
        label: bob
        role: user
        deny_tools: [WebFetch]  # isteğe bağlı, üye için
      - id: 11111
        label: charlie
        role: read_only
```

Özel sohbet ve grup sohbetleri birlikte yaşar: bot listedeki her sohbete hizmet verir ve her sohbetin oturumları ayrı tutulur. Bu dosyayı `aipager config` yazar; onu düzenlemen hiç gerekmez.

## <a id="how-rules-work"></a>Kurallar nasıl çalışır

`~/.config/aipager/policy.yaml` her rol için kuralları belirler. Onu `aipager policy validate` ile kontrol et. Her rolün kabul ettiği alanlar ([Özel roller](#custom-roles) bölümünde listelenenlerin dışında):

- `deny_tools`: sormadan yasaklanan araç adları (örneğin `Write`, `Edit`, `Bash`, `WebFetch`).
- `allow_tools`: boş değilse bir izin listesi: bu rol için geri kalan her şey yasaktır.
- `deny_bash_patterns`: `Bash` girdisiyle karşılaştırılan kalıplar.
- `deny_paths_no_access` / `deny_paths_no_write`: yol kuralları. `user`, `read_only` ve `bypass_role_denies` olmadan tanımladığın her rol için `deny_paths_no_access`, yukarıda **user** altında listelenen kimlik bilgisi dosyalarıyla başlar; onu ayarlarsan bu liste değiştirilir, bu yüzden korumak istediklerini kendi listene kopyala (başka bir mesajın katıldığı süren bir görev, sonuna kadar yine bu listeye uyar). Başında `/` ya da `~` olmayan bir kural (`**/.env`) her yerde eşleşir, bu yüzden o var olduğu sürece kısıtlı bir rolün her `Grep`/`Glob` araması reddedilir (her arama böyle bir dosyayı okuyabilirdi) ve bu görevi durdurur.

`aipager.yaml` içindeki sohbetin `deny_tools` değeri ve üyenin `deny_tools` değeri role eklenir, üyenin `allow_tools` değeri ise rolün izin listesine eklenir. Bunların hepsi yalnızca `bypass_role_denies` olmayan roller için geçerlidir: sahibi ya da yöneticiyi etkilemez.

`~/.config/aipager/policy.d/*.yaml` dosyaları `policy.yaml` dosyasından sonra, ad sırasıyla, aynı biçimde okunur: daha sonraki bir dosyada ayarlanan bir rol alanı önceki değerin yerine geçer, `safety:` listesine ise yalnızca ekleme yapılır. `aipager policy validate` hepsini kontrol eder.

Bütün rollerin altında yalnızca `owner` rolünün aştığı bir **güvenlik alt sınırı** (korunan yollar ve komut kalıpları) yatar: yöneticiler de ona uyar. Bu, aipager'ın yerleşik listesi artı `policy.yaml` içindeki yalnızca ekleme yapabilen `safety:` bölümüdür:

```yaml
safety:
  deny_paths_no_access: [/srv/secrets/**]   # okumak, yazmak, aramak yok
  deny_paths_no_write: [~/notes/**]         # okunabilir, yazılamaz
  deny_bash_patterns: ['\bcurl\b']          # engellenen Bash komutları
```

`owner` dışındaki her rolün her görevi onu bütünüyle alır, aipager'ın kimseye bağlayamadığı görevler de; `aipager doctor --safety-check` neyin geçerli olduğunu gösterir. Anında yeniden yüklemeden sonra onda yapılan bir değişiklik, yeniden yüklemeden sonra gönderilen mesajlara uygulanır; zaten süren bir görev ya da kuyrukta zaten bekleyen bir mesaj eski listeyi korur (değişikliği hemen uygulamak için o görevi durdur). Oradaki `deny_paths_no_access` içinde başında `/` ya da `~` olmayan bir yol her yerde eşleşir, bu yüzden o var olduğu sürece `owner` dışındaki her rolün her `Grep`/`Glob` araması reddedilir (`aipager doctor` bunun için uyarır). Korunan yollar Claude Code'un dosya araçlarına uygulanır (`Read`, `Write`, `Edit`, `MultiEdit`, `NotebookEdit`, `LSP`, `Grep`, `Glob`); kısıtlı bir rolün `Grep`/`Glob` araması ayrıca oturum klasörünün içinde kalmalıdır (`~/.config` içinde `aipager/**` araması reddedilir). `WebFetch` ya da `SendFile` gibi araçlar yola göre kontrol edilmez. aipager'ın bir gönderene bağlayamadığı bir Telegram görevi, `user` rolünden daha gevşek olmayan bir alt sınır alır. Yeniden dene, dokunan kişi tekrarlanan mesajı kendisi gönderdiyse onun rolüyle, aksi halde alt sınırla gider. Komut kalıpları yalnızca Bash'i olan bir rol için önemlidir ve orada bir duvar değil, bir filtredir; bkz. [Güvenlik modeli → kurallar ekipte nasıl uygulanır](security.md#team-mode-enforcement).

## <a id="privacy-considerations"></a>Gizlilikle ilgili konular

- Bot yalnızca **yapılandırılmış sohbetleri** dinler. Botu başka bir gruba eklersen orada etkinleşmez: orada asla yanıt vermez. Orada ona kimin seslendiğini (kişinin kimliği, kullanıcı adı ve adı, grubun kimliği ve adı) `~/.claude/aipager-pending-users.json` dosyasına kaydeder, böylece `aipager config` o grubu ekleyebilir. Bu dosyayı yalnızca sen okuyabilirsin ve 30 gün içinde görülen son 200 kişiyi tutar.
- `read_only` üyeler mesajları, yanıtları ve araç girdilerini **okuyabilir**. İşlem yapamazlar ama gruptaki her şeyi görürler. Bir işin birinden gizli kalması gerekiyorsa o kişinin grupta yeri yoktur.
- Özel sohbeti olan bir kurulumda terminal oturumların ve özel sohbetinin oturumları asla grupta görünmez (bkz. [Mesajlar nereye gider](#where-messages-go)). Daha eski bir aipager'ın zaten gruba taşıdığı bir terminal oturumu grupta kalır: onu sonlandır, grubun sona eren oturumlarından sil ve yeniden başlat.
- `aipager.yaml` dosyasının izinleri 0600'dür (yalnızca sahibi). aipager `policy.yaml` dosyasının izinlerini ayarlamaz: onu yalnızca sahibin erişebileceği şekilde tut (`chmod 600 ~/.config/aipager/policy.yaml`).
- Denetim kaydının (`~/.claude/aipager-audit.jsonl`) kendi izinleri yoktur: `~/.claude` klasörün ne kadar kişiselse o kadar kişiseldir.

## <a id="revoking-a-user"></a>Bir kişinin erişimini kaldırmak

1. `aipager config` → Edit a member → sohbet → kişi → Remove member çalıştır. Mesaj gönderme hakkını alıp okuma hakkını bırakmak için Set role kullan ve `read_only` seç. (Sonradan yeniden eklemek için: Edit a scope → grup → Add a member.) Bir kişinin özel sohbeti için oturumlarını sonlandır, sonra Edit a scope → onun özel sohbeti → Remove this scope.
2. Sihirbaz aipager'ı anında yeniden yükler; "Scopes reloaded live" yazar. Hemen bekletilen mesajları atılır (🤷 ve “Claude'a gönderilmedi” yanıtı) ve hâlâ süren görevi sonuna kadar alt sınırla kısıtlanır. Bu görevi şimdi bitirmek için `/stop` gönder.
3. Onu Telegram grubundan da çıkar. Orada olduğu sürece oraya yayımlanan her şeyi okumaya devam edebilir: kartlar, yanıtlar, araç girdileri ve izin istekleri.

1. adımdan sonra sihirbazın ne söylediğini kontrol et. aipager'ı yeniden başlatmanı söylerse ona ulaşamamış demektir: onu yeniden başlat (`aipager service stop`, sonra `aipager service start`), çünkü o zamana kadar bellekte önceki üye listesi kalır. "Not applied" yazarsa söylediği sorunu düzelt: o zamana kadar aipager önceki yapılandırmayı tutar. `aipager.yaml` dosyasını elle düzenledikten sonra yeniden yüklemeyi elle gönder (bkz. [Yeniden başlatmadan yeniden yükleme](#live-reload)).

## <a id="related-docs"></a>İlgili sayfalar

- [Mimari](architecture.md): süreç nasıl kurulu.
- [Bot komutları](commands.md): arayüz başvurusu.
- [Güvenlik modeli](security.md): güven sınırı, tehdit listesi.
- [Sorun giderme](troubleshooting.md): `aipager doctor` başvurusu.
