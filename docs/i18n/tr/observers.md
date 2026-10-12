# Gözlemci botlar

<!-- i18n-source: docs/observers.md @ 6fc084f -->

> Bu sayfa İngilizceden çevrildi; ikisi birbirini tutmazsa İngilizce sürüm geçerlidir. ([English](../../observers.md))

Ana botun bildirimlerinin kopyalarını alan, yalnızca okuyan Telegram botları. Özetleri, uyarıları ve hataları alırlar ama oturumları yönetemezler.

## <a id="setup"></a>Kurulum

1. [@BotFather](https://t.me/BotFather) ile bir bot oluştur
2. Botla sohbeti aç ve `/start` gönder
3. Sohbet kimliğini öğren (bir mesaj gönder, sonra `https://api.telegram.org/bot<TOKEN>/getUpdates` adresine bak)
4. aipager'ın ortamında `OBSERVER_BOTS` değerini ayarla. Ön planda çalışan bir aipager için değişkeni `aipager start` öncesinde dışa aktar; systemd hizmeti için:

```sh
systemctl --user edit aipager    # [Service] bölümüne ekle:
# Environment=OBSERVER_BOTS=<bot_token>:<chat_id>
```

Birden çok gözlemci (virgülle ayrılmış):

```
OBSERVER_BOTS=111:AAA_first:12345,222:BBB_second:67890
```

Biçim `token:chat_id`: ayrıştırılırken ayırıcı olarak **son** iki nokta alınır (bot token'larında zaten bir iki nokta vardır).

5. aipager'ı yeniden başlat

## <a id="what-observers-receive"></a>Gözlemciler neler alır

Gözlemciler bu bildirimleri **botun hizmet verdiği her sohbetten**, her özel sohbetten ve her gruptan, filtresiz alır: gözlemciyi yalnızca bunların hepsiyle güvenilebilecek bir sohbet için kur. Her bildirim, geldiği sohbetin dilinde gelir.

| Olay | Örnek |
|-------|---------|
| Görev bitti | `💬 x1 · Bitti (2:05, +12 -3)`: yanıtın kendisi değil, yalnızca sonuç satırı; yanıt ya da kart kısaltılmak zorunda kaldıysa ek olarak tam günlük bir `.md` dosyası |
| Arka plan işi bitti | `✅ x1 · Bitti (...)` ya da bir ajan kaybolduysa veya bir arka plan komutunun bitişi hiç görülmediyse `⚠️ x1 · Bitti (...)` |
| API hatası | `⚠️ x1 · Anthropic sunucuları aşırı yüklü. Birazdan tekrar dene.` (Yeniden dene düğmesi olmadan) |
| Bağlam uyarısı | `⚠️ x1 · Bağlam 82% dolu - yakında otomatik sıkıştırılacak` |
| Sıkıştırma sürüyor | `🔄 x1 · Konuşma sıkıştırılıyor` |
| Sıkıştırma bitti | `📦 x1 · Konuşma sıkıştırıldı: 82% → 4%` |
| İstek hâlâ bekliyor | `⬆️ x1 · hâlâ yukarıdaki yanıtını bekliyor`, beklediği izin ya da soruyla birlikte |
| Çalışan bir oturum uzun süre sessiz | `⏳ x1 · hâlâ çalışıyor (10 dk'dır haber yok)` |
| Claude Code'un almadığı bir mesaj | `⚠️ x1 · Claude Code mesajı almadı` |
| Oturum sona erdi | `🔴 x1 · Oturum sona erdi` |

## <a id="what-observers-dont-receive"></a>Gözlemcilerin ALMADIĞI şeyler

- Çalışma kartı animasyonları
- Araç çağrısı güncellemeleri
- İzin istekleri ve düğmeleri (isteğin ne sorduğunu yalnızca yukarıdaki “bekliyor” hatırlatması söyler)
- AskUserQuestion iletişim kutuları
- Herhangi bir klavye ya da düğme

Gözlemcilerin hiçbir durumu yoktur: gönderim yanıt beklemeden yapılır. Arızalı bir gözlemci ana botu asla etkilemez.
