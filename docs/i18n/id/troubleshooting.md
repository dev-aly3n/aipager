# Pemecahan masalah

<!-- i18n-source: docs/troubleshooting.md @ 76d125d -->

> Halaman ini diterjemahkan dari bahasa Inggris; kalau keduanya berbeda, versi bahasa Inggris yang berlaku. ([English](../../troubleshooting.md))

Kegagalan yang sering terjadi dan cara memperbaikinya. Kalau ragu, jalankan:

```sh
aipager doctor
```

Perintah ini menjalankan semua pemeriksaan di bawah secara berurutan dan mencetak solusi yang disarankan untuk setiap kegagalan. Sumbernya: `aipager/doctor.py`.

## <a id="another-aipager-daemon-already-owns-the-socket"></a>“Another aipager daemon already owns the socket”

Dua aipager tidak bisa berbagi satu soket kontrol. Error ini berarti ada aipager yang masih berjalan dan menjawab di soket itu (soketnya `$XDG_RUNTIME_DIR/aipager.sock`, atau `/tmp/aipager.sock` kalau `$XDG_RUNTIME_DIR` tidak diatur). File soket sisa aipager yang crash bukan masalah: start berikutnya menghapusnya dan membuat yang baru. Hentikan dulu aipager yang sedang berjalan:

```sh
aipager service stop          # kalau berjalan sebagai layanan
pkill -f 'aipager start'      # kalau tidak
```

Kalau pemeriksaan soket tidak menangkapnya, `aipager start` tetap menolak dengan `aipager is already running (pid=N)` dan menyebutkan file kuncinya (`~/.local/share/aipager/daemon.lock`): hentikan proses itu dengan cara yang sama.

## <a id="telegram-bot-doesnt-respond"></a>Bot Telegram tidak merespons

1. `aipager status`: kalau baris aipager bertuliskan `not running`, jalankan aipager.
2. `aipager doctor`: pemeriksaan `token_valid` dan `chat_reachable` menghubungi API Telegram dari awal sampai akhir dan menunjukkan error yang persis.
3. Chat yang salah: jalankan `aipager config` dan pilih **Test bot reachability**. Untuk memperbaiki chat pribadi yang salah, tambahkan yang benar dengan **Add a DM scope** (lihat [cara mencari ID](groups.md#finding-ids-auto-detect)), lalu hapus yang salah di **Edit a scope**.
4. Bot belum pernah menerima `/start`: buka bot di Telegram dan ketuk Start sekali.

## <a id="busy-cards-got-slower"></a>Kartu kerja jadi lebih lambat

Telegram mengizinkan kira-kira satu pesan per detik ke satu chat (dan 20 per menit ke grup). Setiap pesan dan setiap edit dihitung. aipager memberi setiap chat anggarannya sendiri, kira-kira 1 panggilan per detik dengan sedikit ruang untuk lonjakan, dan kartu kerja di chat itu **berbagi** anggaran itu. Indikator “sedang mengetik…” juga termasuk dalam anggaran ini, sebagai **hal paling tidak penting** yang dikirim chat itu (lihat “Gelembung mengetik” di bawah).

Bagian untuk gelembung disisihkan lebih dulu (satu panggilan setiap 4,5 detik selama tugas tertua yang sedang berjalan di chat itu berumur kurang dari sepuluh menit, lebih jarang saat tugas itu menua; lihat “Gelembung mengetik”), dan kartu berbagi sisanya. Jadi satu kartu di chat pribadi diperbarui kira-kira setiap 4,8 detik; dengan dua sesi yang bekerja di satu chat, setiap kartu kira-kira setiap 9,7 detik; dengan tiga, kira-kira setiap 14,5. **Kartu yang lambat adalah anggaran yang sedang bekerja, bukan bug.** Jawaban, pesan balasan, dan respons tombol tidak pernah diperlambat demi kartu: mereka punya token cadangan, sedangkan edit kartu yang tidak kebagian panggilan dilewati dan dicoba lagi di putaran berikutnya, bukan mengantre di depan jawabanmu.

Di **grup**, batasnya 20 panggilan per menit, berapa pun sesi di dalamnya, dan satu gelembung saja akan menghabiskan dua pertiganya. Kartu-kartu grup bersama-sama tetap mendapat satu pembaruan setiap 10 detik (11 dtk untuk satu kartu) dan gelembung mendapat sisanya: karena itu di grup gelembung bisa sesekali hilang satu-dua detik.

Untuk mempercepat kartu: kurangi sesi yang berjalan bersamaan di satu chat, atau beri sesi yang paling sibuk chat-nya sendiri (`aipager config`). Kamu juga bisa mengatur `STREAM_EDIT_INTERVAL` (bawaannya `1.2`, laju saat kartu sedang mengalirkan teks) dan `BUSY_EDIT_INTERVAL` (bawaannya `3.0`, saat sedang tenang) sebagai variabel environment aipager (lihat [pengaturan dari environment](commands.md#settings-in-the-environment)). Mengatur salah satunya *di bawah* batas bawah chat aman dan tidak mengubah apa pun: batas bawah yang menang, dan justru itulah yang menjaga chat di bawah batas Telegram, apa pun nilai pengaturannya.

### <a id="a-long-turns-card-refreshes-less-often"></a>Kartu tugas yang panjang diperbarui lebih jarang

Laju di atas berlaku selama dua menit pertama tugas. Setelah itu kartu **melambat seiring tugas menua**, dan mulai menit ke-10 jamnya bergerak dalam langkah 30 detik, seirama dengan pembaruannya, supaya tidak pernah terlihat membeku:

| umur tugas | kartu diperbarui paling sering setiap | jam menunjukkan |
|---|---|---|
| 0–2 mnt | seperti di atas (sekitar 4,8 dtk untuk satu kartu di chat pribadi) | `0:45` |
| 2–10 mnt | 10 dtk | `4:10` |
| 10–60 mnt | 30 dtk | `23:00`, `23:30` |
| lebih dari 60 mnt | 30 dtk | `1:23:00`, `1:23:30` |

Jam dibulatkan ke bawah ke langkah 30 detiknya, dan pembaruan datang paling sering setiap 30 detik, kadang sedikit lebih jarang. Jadi kartu sesekali melewatkan satu langkah: pembaruan di 23 mnt 29 dtk menunjukkan `23:00`, dan kalau yang berikutnya datang di 24 mnt 1 dtk, ia menunjukkan `24:00` dan `23:30` terlewat.

Baris alat atau kalimat baru menunggu pembaruan berikutnya; **perubahan keadaan** (sesi berpindah dari bekerja ke menunggu agen latar belakang dan sebaliknya) langsung ditampilkan, paling sering sekali setiap 10 detik. Baris agen yang hidup dan baris penantian bergerak dengan langkah yang sama. Kartu yang sudah selesai menunjukkan durasi tugas dalam detik sebenarnya (`23:17`).

Inilah yang mencegah tugas empat jam mengedit satu pesan ribuan kali: pada 2026-09-23 satu kartu melakukan persis itu, dan chat-nya diblokir selama tujuh jam. Untuk mempertahankan laju cepat sepanjang tugas, matikan **`/settings` → ⏱ Pembaruan kartu tugas panjang** (untuk chat, atau untuk satu sesi dari Mini App atau `👤 Preferensi per sesi`).

### <a id="the-typing-bubble"></a>Gelembung mengetik

Gelembung “sedang mengetik…” mengirim **satu** putaran per chat, berapa pun sesi yang bekerja di sana (di semua klien Telegram gelembungnya satu per chat), setiap `TYPING_INDICATOR_INTERVAL` detik (bawaannya 4,5: Telegram menghapus status mengetik setelah 5; `0` mematikan gelembung). Dalam anggaran chat, gelembung ini dihitung sebagai hal paling tidak penting yang dikirim chat itu:

- ia **melambat seiring tugas menua**, mengikuti umur tugas tertua yang sedang berjalan di chat itu: setiap 4,5 dtk selama sepuluh menit pertama (gelembung menyala terus), lalu setiap 15 dtk; Telegram menampilkannya selama 5 dtk, jadi gelembung untuk tugas yang sudah lama berkedip, tidak menyala terus. Pada 4,5 dtk gelembung itu sendiri akan menjadi 800 panggilan per jam, dan batas per jam (di bawah) akan mematikannya lama sekali; mematikan **⏱ Pembaruan kartu tugas panjang** menahannya di 4,5 dtk;
- ia **muncul sejak awal setiap tugas**; kartu yang masih muda diperbarui sedikit lebih lambat untuk memberinya ruang: bagian gelembung disisihkan sebelum kartu berbagi sisanya (di atas), jadi bersama-sama keduanya tidak pernah melewati batas chat;
- ia tidak pernah mengambil token yang dibutuhkan edit kartu: gelembung yang seharusnya dikirim tepat setelah edit kartu menunggu setengah detik (`TYPING_RETRY_WAKE`) lalu mencoba lagi, jadi paling banyak terlambat satu-dua detik, tapi tidak pernah hilang;
- ia yang pertama dilepas saat volume per jam chat itu membesar (di bawah);
- 429 pada gelembung itu sendiri hanya memblokir gelembung, selama yang diminta Telegram (kartu tetap berjalan), tapi memulai rezim peringatan enam jam untuk chat itu (laju dibatasi 0,5 panggilan/dtk, jadi kartu juga melambat);
- **pemblokiran** pada gelembung (`retry_after` di atas batas di bawah) mematikan pengiriman ke chat itu persis seperti pemblokiran pada panggilan lain: tidak ada lagi yang dikirim ke sana, dan jawaban tugas ditahan lalu dikirim setelah blokir dicabut.

Dulu gelembung ini gratis (satu putaran per sesi yang bekerja, di luar anggaran mana pun), sampai pada 2026-09-23 dua sesi di satu chat pribadi mengirimnya sekitar 5.400 kali dalam tiga jam dan Telegram membatasi gelembung itu sendiri.

## <a id="the-bot-slowed-down-a-rate-limit-429"></a>Bot melambat: batas permintaan (429)

Kalau Telegram tetap membatasi sebuah chat, ia menjawab satu panggilan dengan `429` dan `retry_after` beberapa detik. aipager berhenti menghubungi chat itu persis selama itu, lalu mencoba lagi satu pengiriman yang tertunda dan **menggandakan interval kartu chat itu** (sampai ×8). Setiap menit yang tenang membaginya dua kembali ke normal.

Yang kamu lihat:

- `aipager status` (dan baris `aipager daemon` di `aipager doctor`) menampilkan **`Telegram flood backoff ×4 (chat …), last 429 12 s ago`**.
- Tepat satu baris di `aipager logs`, tanpa traceback: `flood: chat … 429 retry_after=5s → cadence ×2`.
- Tidak ada yang dimatikan dan **tidak ada jawaban yang hilang**: jawaban itu ditunda, tidak dibuang.
- **Laju kirim yang “diperoleh” chat itu turun separuh**, dan chat masuk ke **rezim peringatan enam jam** (`FLOOD_WARNING_HOURS`): lajunya tidak bisa naik di atas 0,5 panggilan/dtk (`FLOOD_WARNED_CEILING`) dan naik kembali dengan lambat. `aipager status` menunjukkannya: `Telegram chat 123: rate 0.25/s (ceiling 0.50/s), 212/1200 calls in the last hour (as of 18s ago), 7/30 in the last minute - warning regime, 5h 52m left`.

Kamu tidak perlu melakukan apa pun. Ini hilang dengan sendirinya. Baris perlambatan hilang setelah chat tenang selama satu-dua menit; laju naik 0,1 panggilan/dtk untuk setiap jendela yang tenang (biasanya semenit, dalam rezim peringatan 36 menit), dan batas atas penuh kembali saat rezim itu berakhir. Selama itu `aipager doctor` tetap menjaga baris aipager hijau: chat yang sedang diperlambat adalah operasi normal. (Dulu 429 dilupakan setelah sekitar lima menit. Satu-satunya peringatan Telegram sebelum pemblokiran 2026-09-23 datang 3 jam 23 menit sebelumnya, dan chat itu kembali ke kecepatan penuh beberapa menit setelah peringatan itu.)

### <a id="the-hourly-budget"></a>Anggaran per jam

Setiap chat juga punya **jam** bergeser: paling banyak `FLOOD_HOURLY_MAX` (1200) panggilan dalam 60 menit mana pun, gelembung mengetik, edit kartu, dan jawaban digabung; hanya reaksi yang dikecualikan. 120 terakhir (`FLOOD_HOURLY_ESSENTIAL_RESERVE`) disisihkan untuk jawaban, pesan balasan, dan permintaan:

- pada 810 panggilan per jam, gelembung mengetik dijeda (kembali di bawah 648);
- pada 1080, chat masuk ke **mode minimal** (di bawah) sampai jam itu turun ke 864;
- jawaban tidak pernah ditolak oleh anggaran per jam; kalau suatu saat melewati 1200, log mengatakannya sekali per jam.

`aipager status` menampilkan panggilan chat dalam satu jam terakhir dibandingkan anggarannya. Angkanya berasal dari file keadaan yang ditulis ulang aipager paling sering sekali per menit selama ada panggilan, jadi umurnya ditampilkan di sampingnya. Dua sesi aktif yang mengirim ke satu chat pribadi selama berjam-jam tetap jauh di bawah anggaran (insiden 2026-09-23, dimainkan ulang: sekitar 900 panggilan di jam tersibuk, dibandingkan sekitar 3.400 menurut model untuk pekerjaan yang sama tanpa anggaran).

### <a id="the-earned-rate-and-why-your-cards-may-be-slower-than-they-were"></a>Laju yang diperoleh, dan kenapa kartumu bisa lebih lambat dari sebelumnya

Sejak 0.7.13, laju kirim tetap setiap chat **dipelajari, bukan diasumsikan**. Telegram tidak menerbitkan batas yang benar-benar berlaku untuk botmu (tergantung riwayat akunmu belakangan ini), jadi aipager memulai setiap chat pada 0,5 panggilan/dtk, menambah 0,1 untuk setiap menit yang tenang sampai batas atas 1/dtk, membaginya dua saat 429, dan menurunkannya ke 0,05 setelah pemblokiran. Bot yang baru saja diblokir berjalan jauh lebih hati-hati daripada yang belum, dan memperoleh kembali lajunya dalam beberapa jam berikutnya.

Kalau laju sebuah chat turun di bawah 0,2 panggilan/dtk, atau bagian anggaran per jamnya untuk kartu dan gelembung habis, chat itu masuk ke **mode minimal**: kartu kerja berhenti beranimasi dan menampilkan satu baris diam `⏳ x1 · sedang bekerja (kartu sementara tidak diperbarui)`, gelembung mengetik berhenti, dan bilah status yang disematkan menampilkan `⏸ kartu sementara tidak diperbarui (batas per jam Telegram)` (atau `(batas Telegram)` kalau lajunya yang membawa chat ke sana) dan selain itu tidak berubah sampai keadaan ini lewat. **Jawaban, pesan balasan, dan permintaan izin tetap dikirim**: itulah intinya. Kartu adalah sekitar 95% dari yang dikirim bot ini dan jawaban sekitar 5%, jadi di bawah tekanan bot mengorbankan piksel, bukan pekerjaan. `aipager doctor` menampilkan mode minimal sebagai peringatan, supaya kartu yang dijeda tidak pernah dikira rusak; mode ini hilang sendiri saat lajunya pulih.

## <a id="the-bot-went-quiet-flood-control"></a>Bot jadi diam: pemblokiran Telegram (flood control)

`retry_after` yang lebih besar dari `TELEGRAM_MAX_RETRY_AFTER` (bawaannya 90 dtk) bukan batas permintaan, melainkan **pemblokiran**, dan bisa berlangsung berjam-jam. Dari chat, kelihatannya bot sudah mati: pesan tetap sampai ke Claude, sesi tetap bekerja, tapi tidak ada jawaban yang datang.

Yang kamu lihat:

- `aipager status` (dan baris `aipager daemon` di `aipager doctor`) menampilkan **`Telegram flood-muted until HH:MM (chat …)`**.
- Satu baris di `aipager logs`: `Telegram flood control — chat … muted for Ns (until HH:MM) …`, lalu sunyi untuk chat itu. Kemudian satu baris `flood mute on chat … lifted`.
- `aipager status` juga menampilkan keadaan lengkap chat itu: `Telegram chat 123: rate 0.05/s (ceiling 0.50/s), 431/600 calls in the last hour (as of 12s ago) - MINIMAL MODE, card updates paused - flood-muted until 09:41 - warning regime, 5h 59m left (1 ban(s) in the last 7 days)`.

aipager mematikan pengiriman ke chat itu persis selama yang diminta Telegram dan tidak membuat **satu panggilan pun dalam bentuk apa pun** ke sana: tidak ada jawaban, edit kartu kerja, lampiran, reaksi, indikator mengetik, notifikasi singkat setelah ketukan tombol, atau balasan atas perintahmu. Masing-masing akan menjadi pelanggaran baru yang memperpanjang pemblokiran; satu permintaan di tengah pemblokiran yang masih aktif pada 2026-09-15 mengubah pemblokiran 21 menit menjadi 9,5 jam. Jadi perintah yang diketik selama pengiriman dimatikan tidak mendapat jawaban sama sekali, dan **ketukan tombol juga tidak dikonfirmasi**: sampai 0.7.12 notifikasi singkat itu tetap dikirim, dengan anggapan Telegram menghitungnya terpisah. Untuk laju, itu benar; untuk pemblokiran, tidak. Chat lain tidak terpengaruh.

**Mengirim pesan ke bot selama pemblokiran aman, dan tidak ada yang hilang.** Tidak ada yang kamu lakukan di chat yang membuat bot menghubungi Telegram selama pengiriman ke chat itu dimatikan, jadi itu tidak bisa memperpanjang pemblokiran:

- Pesan yang kamu kirim (teks, pesan suara, file, templat cepat) tetap sampai ke Claude, dan sesi tetap mengerjakannya. Kartu kerjanya ditahan, bukan dibuang: kalau tugasnya masih berjalan saat blokir dicabut, kartu itu muncul saat itu. Setelah pemblokiran, chat tetap dalam mode minimal untuk beberapa waktu, jadi kartu bisa muncul beberapa menit setelah pemblokiran berakhir. Kalau tugasnya selesai selama pemblokiran, kamu mendapat jawabannya dan tidak ada kartu.
- **🔄 Coba lagi** selama pemblokiran tidak melakukan apa pun. Pesannya tidak dikirim lagi, dan tombolnya tetap ada, jadi kamu bisa mengetuknya lagi setelah pemblokiran. Sampai 0.7.14, pesannya tetap dikirim ke Claude sementara pesan error dengan tombolnya tetap ada, jadi ketukan kedua mengirimnya sekali lagi.
- Keyboard utama juga ditahan, termasuk yang dikirim mulai ulang (sampai 0.7.14 keyboard itu hilang sampai ada hal lain yang menyegarkannya). Keyboard itu dikirim setelah pemblokiran.

Setelah blokir dicabut, semuanya datang dengan urutan ini: jawaban yang ditahan, lalu kartu dari tugas yang masih berjalan, lalu keyboard. Kartu yang harus menunggu mode minimal selesai datang setelah keyboard.

**Jawaban tidak hilang.** Jawaban yang selesai saat pengiriman ke chat dimatikan ditahan dan dikirim beberapa detik setelah blokir dicabut, dengan baris pertama `⏳ terlambat sampai: tertahan 42 menit karena batas Telegram`. Kamu tidak perlu bertanya lagi, dan kalau bertanya, kedua jawaban akan datang, yang lama dulu: pemblokiran beberapa jam mencakup beberapa tugas, dan kamu butuh jawaban masing-masing. (Jawaban yang ditahan tersimpan di memori: kalau kamu memulai ulang aipager saat ada yang menunggu, jawaban itu hilang; log saat mati mengatakan berapa. Satu chat menahan paling banyak 20 dan tidak ada yang lebih tua dari 24 jam; apa pun yang dibuang karena salah satu alasan itu dicatat sebagai peringatan di log, dengan keterangan apa isinya.)

**Pemblokiran membuat chat lebih lambat sesudahnya, bukan lebih cepat.** Begitu pemblokiran dipasang, laju yang dipelajari chat itu jatuh ke dasar dan pemblokiran itu dihitung; jam-jam saat pengiriman dimatikan tidak mengembalikan apa pun, pertumbuhan dimulai lagi dari saat blokir dicabut, dan selama **tujuh hari** (`FLOOD_BAN_MEMORY_DAYS`) batas atas laju chat *dan* anggaran per jamnya dibagi satu ditambah jumlah pemblokiran minggu itu: satu pemblokiran membagi keduanya dua, dua pemblokiran membaginya tiga. `aipager status` menampilkan semua ini. (Dulu ingatan ini hanya 24 jam dan hanya membagi batas atas; sebuah chat yang diblokir pada 2026-09-19 kembali ke kecepatan penuh dan diblokir selama tujuh jam pada 2026-09-23.) Lima pemblokiran atau lebih dalam seminggu menahan chat di mode minimal sampai pemblokiran-pemblokiran itu kedaluwarsa: jawaban tetap dikirim, kartu tetap dijeda.

Yang JANGAN dilakukan:

- Jangan memulai ulang aipager untuk “memperbaiki”-nya. Pengiriman yang dimatikan akan pulih sendiri pada waktu yang ditampilkan. Sejak 0.7.13, mulai ulang tidak lagi melupakan pemblokiran (batas waktunya, laju yang diperoleh chat, dan riwayat pemblokirannya disimpan di `~/.claude/aipager-flood-state.json` dan dipulihkan saat mulai, begitu juga panggilan chat dalam satu jam terakhir dan rezim peringatannya), jadi mulai ulang juga tidak lagi memperpanjangnya. Tapi mulai ulang membuang semua jawaban yang masih ditahan dan tidak memperbaiki apa pun.
- Jangan menurunkan `TELEGRAM_MAX_RETRY_AFTER` di bawah bawaannya, 90 dtk, dengan harapan bisa mencoba lebih cepat: apa pun di atas ambang itu adalah pemblokiran, bukan batas permintaan.

Cara menghindarinya: kurangi sesi yang berjalan bersamaan di satu chat, atau beri sesi yang paling sibuk chat-nya sendiri (`aipager config`).

## <a id="a-session-dropped-off-the-pinned-status-bar-it-is-gone"></a>Sebuah sesi hilang dari bilah status yang disematkan (statusnya GONE)

Proses dtach sesi itu berakhir (komputer dinyalakan ulang, `pkill claude`, seseorang mengetik `exit` di sesi dtach yang tersambung). Buat ulang sesinya: percakapannya masih ada di foldernya di `~/.claude/projects/...`.

Paling mudah `/resume <label>` di Telegram (atau `/resume` saja untuk daftar): ini memulai ulang sesi dan melanjutkan percakapannya. Dari baris perintah:

```sh
aipager resume <label>             # sama, dari terminal
aipager session <label>            # atau sesi baru, tanpa riwayat
aipager session <label> --continue # dtach baru, percakapan terakhir di folder ini
```

## <a id="a-permission-prompt-with-no-news-for-5-minutes"></a>Permintaan izin tanpa kabar selama 5 menit

Kalau sebuah sesi sedang menunggu permintaan izin dan Claude Code tidak melaporkan apa pun selama 5 menit, aipager berhenti menunggu permintaan itu dan menganggap sesi itu sedang bekerja lagi. Ini menangkap Claude Code yang crash di tengah permintaan yang tidak bisa dijawab siapa pun lagi. Tombol permintaan itu tetap menjawab di dialognya kalau dialog itu masih terbuka (lihat [jawaban](commands.md#idle-responses)).

Untuk mengubah lamanya menunggu:

```sh
AIPAGER_INTERACTIVE_TIMEOUT=300 aipager start    # dalam detik
```

## <a id="voice-extra-wont-install-via-telegram"></a>Fitur pesan suara tidak bisa dipasang lewat Telegram

Tombol `[📦 Pasang fitur pesan suara]` menjalankan installer dan mengedit pesannya setiap beberapa detik selama installer berjalan (`masih berjalan… (25 dtk)`). Kalau pemasangan gagal, pesan itu menampilkan kode keluar dan 500 karakter terakhir dari keluaran installer. Penyebab yang sering:

- Jaringan: pip tidak bisa menjangkau PyPI. Periksa koneksi dari komputer tempat aipager berjalan.
- Disk: `~/.cache/huggingface/` butuh sekitar 74 MB untuk model, dan cache wheel pip butuh sekitar 250 MB lagi. Kosongkan ruang dulu.
- Permission denied: venv-nya hanya-baca. Beralih ke `uv tool install`, yang selalu memakai venv milik penggunamu.

Kalau bot kehilangan koneksi di tengah pemasangan, pemasangan di komputernya sendiri biasanya sudah selesai: mulai ulang aipager dan coba lagi pesan suara.

## <a id="open-app-shows-open-this-page-from-the-telegram-app-to-sign-in"></a>Membuka aplikasi menampilkan “Buka halaman ini dari aplikasi Telegram untuk masuk.”

Halamannya termuat, tapi skrip Mini App SDK dari Telegram (yang memberi `initData`) tidak, jadi halaman tidak punya apa pun untuk masuk dan tidak membuat satu pun panggilan API. Pesan itu terdengar seperti salahmu; sebenarnya penyebabnya jaringan.

Versi aipager yang baru mengunduh skrip ini sendiri dan menyajikannya dari asal halaman itu (`/telegram-web-app.js`), jadi ponsel hanya perlu menjangkau host yang menyajikan halaman, bukan juga `telegram.org`. Kalau kamu melihat ini di versi lama, perbarui.

Lalu, berurutan:

1. Lihat sumber halamannya. Kalau tag `<script>`-nya menunjuk ke `https://telegram.org/js/telegram-web-app.js`, aipager belum bisa mengunduh skripnya dan halaman memuatnya dari Telegram, dan justru itu yang gagal di ponsel yang tidak bisa menjangkau `telegram.org`. aipager mengunduhnya saat server Mini App mulai dan mencoba lagi paling sering sekali sehari, jadi memulai ulang aipager langsung mencoba lagi; `journalctl --user -u aipager | grep "webapp sdk"` mengatakan apa yang salah (biasanya `telegram.org` yang diblokir atau difilter).
2. Kalau tag-nya menunjuk ke `/telegram-web-app.js`, periksa bahwa aipager menyajikannya: `curl -sI http://127.0.0.1:8765/telegram-web-app.js` di komputer aipager (sesuaikan port kalau sudah kamu ubah) harus menjawab `200` dengan `Content-Type: application/javascript`. Salinannya ada di `~/.local/share/aipager/webapp-sdk/`.
3. URL-nya dibuka di browser biasa, bukan lewat tombol `/app` atau tombol menu bot: maka pesan ini memang yang diharapkan. SDK memberi `initData` hanya di dalam Telegram.
4. Halaman itu sudah terbuka lebih dari lima menit: `initData` kedaluwarsa dan halaman meminta dibuka lagi. Ketuk `/app` lagi.
5. Kamu mengetuk `📱 App` di keyboard dari versi aipager yang lebih lama. Telegram tidak memberi `initData` ke Mini App yang dibuka dari tombol keyboard, jadi keyboard aipager sekarang tidak punya tombol itu lagi. Kirim `/start` untuk mendapatkan keyboard yang sekarang, lalu buka app-nya dari tombol menu atau dengan `/app`.

## <a id="pyexpat-_xml_setalloctrackeractivationthreshold-on-brew-install"></a>`pyexpat _XML_SetAllocTrackerActivationThreshold` saat memasang dengan brew

Paket `python@3.12` dari Homebrew dibangun dengan libexpat yang lebih baru daripada yang ada di sistemmu, hampir selalu karena **Xcode dan Command Line Tools sudah usang** di macOS Tahoe (26.x). Keluaran brew biasanya sudah memberi tahu. Dua solusi:

- **Pakai `uv tool install aipager` saja**. uv membawa python-nya sendiri dan melewati masalah ini sepenuhnya. Ini jalur yang disarankan di macOS; lihat [README](README.md#install).
- **Perbarui Xcode dan Command Line Tools**:
  ```sh
  sudo rm -rf /Library/Developer/CommandLineTools
  sudo xcode-select --install
  ```
  Atau buka Xcode di App Store dan perbarui ke versi terbaru.

## <a id="modulenotfounderror-aipager"></a>“ModuleNotFoundError: aipager”

Program aipager menunjuk ke Python yang tidak lagi berisi aipager (sering setelah venv dihapus). Pasang ulang:

```sh
uv tool install --reinstall aipager
# atau dengan installer yang dipakai sejak awal: pipx, brew, pip
```

## <a id="the-session-list-was-lost-or-damaged"></a>Daftar sesi hilang atau rusak

aipager menyimpan sesinya di `~/.claude/aipager-sessions.json` dan menulis ulang file itu secara atomik (file baru, lalu diganti nama), jadi penulisan yang terputus meninggalkan file lama tetap utuh. Kalau file itu tetap tidak ada, tidak bisa dibaca, atau bukan JSON yang valid, aipager mencatat di log bahwa ia mulai dari awal (peringatan, kalau file itu ada tapi tidak bisa dibaca) dan mulai dengan daftar kosong; ia tidak berhenti. File ini tidak punya cadangan. Tidak ada yang penting yang hilang:

- sesi yang sedang berjalan ditemukan lagi pada putaran monitor pertama, dari soket dtach-nya (`/tmp/claude-dtach-*.sock`);
- setiap percakapan masih ada di folder Claude Code sendiri `~/.claude/projects/...`, jadi `aipager session <name> --continue` di folder sesi itu melanjutkannya (`--resume` membiarkanmu memilih);
- yang hilang adalah daftar sesi yang sudah berakhir (`/resume`), kartu yang sedang terbuka, dan hal-hal lain yang diingat aipager tentang setiap sesi.

Kalau aipager terus crash saat mulai, `aipager logs` (atau `journalctl --user -u aipager`) mengatakan sebabnya; sisihkan file itu (`mv ~/.claude/aipager-sessions.json ~/aipager-sessions.json.old`) untuk menyingkirkannya dari dugaan.

## <a id="a-session-launch-fails"></a>Sebuah sesi gagal dimulai

Peluncuran yang ditolak dtach menampilkan alasannya dalam satu baris di chat (soketnya sudah ada, shell tidak bisa dieksekusi, tidak ada pseudo-terminal, nama terlalu panjang, akses ditolak, folder tidak ada); pesan dtach sendiri, dengan path-nya, ada di `aipager logs`.

## <a id="aipager-doctor-check-list"></a>Daftar pemeriksaan `aipager doctor`

Urutannya penting: setiap pemeriksaan menganggap pemeriksaan sebelumnya sudah lolos. Pemeriksaan yang crash di lingkungan yang tak terduga ditampilkan sebagai satu baris ⚠ dengan nama pemeriksaan dan error-nya; pemeriksaan lain tetap berjalan.

| Pemeriksaan (kunci `--json`) | Yang diperiksa | Cara memperbaiki |
|---|---|---|
| `config_parses` | `aipager.yaml` dan `policy.yaml` bisa dibaca; berjalan paling awal karena semua pemeriksaan berikutnya membaca konfigurasi | error-nya menyebutkan file dan barisnya |
| `config` | `~/.config/aipager/aipager.yaml` ada dan berisi token dan chat | `aipager config` |
| `token_valid` | Token bekerja dengan `getMe` di Telegram | jalankan lagi `aipager config` |
| `chat_reachable` | Telegram mengenal chat yang diatur (`getChat`; tidak ada yang dikirim) | buka bot, ketuk Start |
| `team` | Chat dan anggota (atau `team.yaml` lama): menyebutkan apakah ini mode pribadi atau mode tim, dan menandai `team.yaml` lama yang rusak atau tidak cocok | `aipager config` |
| `role_shell_access` | Memperingatkan sekali untuk setiap peran anggota yang bisa menjalankan Bash (aturan keamanannya bekerja sebisanya, bukan batas; lihat [grup](groups.md#roles)) | jangan beri peran itu Bash di `policy.yaml`, atau terima saja |
| `peer_messages` | Pesan antarsesi Claude: di mana tanda terimanya disimpan dan apakah folder itu tertutup, apakah sistem ini bisa memeriksa waktu mulai pengirim, dan peringatan untuk setiap peran yang diberi SendMessage oleh sebuah aturan (lihat [keamanan](security.md#messages-between-claude-sessions)) | hapus folder tanda terima bersama (aipager membuatnya lagi), atau jangan beri peran itu SendMessage |
| `claude` | Menemukan program `claude` dengan cara yang sama seperti setiap peluncuran (`claude_path` di `aipager.yaml` atau `$AIPAGER_CLAUDE_BIN` langsung menang; kalau tidak, yang terbaru dari `~/.local/bin`, PATH, dan Homebrew), dan mencantumkan setiap instalasi LAIN yang ditemukan | pasang Claude Code atau atur `claude_path` / `AIPAGER_CLAUDE_BIN` |
| `claude_auth` | Mencoba `claude auth status` di environment yang sama dengan yang didapat sesi sungguhan. **Tidak pernah gagal**: “belum login” dan “percobaannya sendiri gagal” ditampilkan berbeda, dan keduanya tidak menghalangi peluncuran sesi | `claude auth login`, atau atur kunci API / `CLAUDE_CODE_OAUTH_TOKEN` |
| `dtach` | Program `dtach` ditemukan (`dtach-bin` milik aipager sendiri atau yang ada di PATH) | `uv tool install --reinstall aipager` |
| `hook_scripts` | `aipager-hook` dan `aipager-statusline` ada di PATH | `uv tool install --reinstall aipager` |
| `settings_json` | Hook aipager terpasang di `~/.claude/settings.json` | `aipager config` |
| `daemon` | aipager berjalan dan soketnya menjawab; juga menampilkan keadaan batas Telegram (lihat [di atas](#the-bot-slowed-down-a-rate-limit-429)) | `aipager start` |
| `service_installed` | Opsional: file layanan ada | `aipager service install` |
| `service_unit_path` | Hanya Linux: `Environment=PATH=` milik layanan yang *terpasang* benar-benar memuat folder program claude yang ditemukan, dibaca sebagai teks karena doctor tidak bisa melihat PATH systemd dari shell interaktif operator | `aipager service install --yes` |
| `miniapp` | Apakah Mini App bisa mulai saat dinyalakan. **Tidak pernah gagal**: Mini App opsional | perbaikan yang disebutkannya |

`aipager doctor --json` mencetak pemeriksaan yang sama dengan urutan dan kunci ini (lihat [perintah](commands.md#aipager-doctor---json)).

Jalankan `aipager doctor --fix` untuk mencari kredensial Claude secara interaktif dan menyalinnya ke `daemon.env` (juga saat file itu ada tapi tidak berisi kredensial), atau untuk mengunci `claude_path` saat ditemukan lebih dari satu instalasi. Perintah ini hanya bertindak setelah bertanya. Untuk layanan yang PATH-nya tidak memuat folder claude, jalankan `aipager service install --yes`.

## <a id="the-daemon-cant-find-claude-or-picks-the-wrong-install"></a>aipager tidak menemukan `claude` atau memilih instalasi yang salah

Dulu enam tempat mencari `claude` dengan caranya sendiri-sendiri dan bisa berbeda hasilnya. Sekarang semuanya lewat satu fungsi pencarian (`aipager/claude_resolve.py`). `claude_path` di `aipager.yaml`, lalu `$AIPAGER_CLAUDE_BIN`, langsung menang kalau berfungsi. Kalau tidak, setiap `claude` yang ditemukan di `~/.local/bin`, di `$PATH`, dan di prefix Homebrew diperiksa, dan versi terbaru yang menang (jadi mengubah urutan `$PATH` tidak mengubah pilihan kalau instalasi lain lebih baru). Jalankan `aipager doctor` untuk melihat persis mana yang dipilihnya dan apa lagi yang ditemukannya; kalau yang salah menang, kunci yang benar:

```sh
aipager doctor --fix        # pilih secara interaktif di antara instalasi yang ditemukan
# atau dengan tangan:
echo 'claude_path: /home/you/.local/bin/claude' >> ~/.config/aipager/aipager.yaml
```

## <a id="sessions-cant-authenticate-under-the-systemd-service-but-claude-works-fine-in-a-terminal"></a>Sesi tidak bisa login di bawah layanan systemd, tapi `claude` lancar di terminal

Layanan `systemctl --user` tidak pernah membaca `~/.bashrc` atau `~/.profile`, jadi baris `export CLAUDE_CODE_OAUTH_TOKEN=…` di sana tidak pernah sampai ke aipager. Sebagai gantinya, layanan memakai `LoadCredential=` dari systemd dan membaca `~/.config/aipager/daemon.env`: file `KEY=VALUE` biasa dengan izin 0600 yang dibuat otomatis saat `aipager service install` pertama kali dijalankan (dipindahkan dari `config.env` lama kalau di sana ada token, kalau tidak dicari sekali di shell login-mu, kalau tidak dibiarkan kosong dengan peringatan). Kalau masih kosong, `aipager doctor --fix` mencari lagi di tempat-tempat yang sama kalau kamu menjawab ya, dan menambahkan baris yang ditemukannya sambil mempertahankan baris lain di file itu; ia tidak pernah mencetak token-nya. Atau tambahkan barisnya dengan tangan:

```sh
cat ~/.config/aipager/daemon.env          # lihat apa yang ada (atau tidak ada)
echo 'CLAUDE_CODE_OAUTH_TOKEN=sk-...' >> ~/.config/aipager/daemon.env
chmod 600 ~/.config/aipager/daemon.env
aipager service stop && aipager service start
```

Lihat [Model keamanan: kredensial Claude](security.md#the-claude-credential-what-actually-protects-it) tentang apa yang dilindungi file ini dan apa yang tidak.

## <a id="the-daemons-control-socket-moved"></a>Soket kontrol aipager pindah

`aipager doctor` / `aipager status` dulu selalu melihat ke `/tmp/aipager.sock`. Di bawah layanan systemd, soket kontrol sekarang ada di `$XDG_RUNTIME_DIR/aipager.sock` (dengan cadangan `/tmp` hanya saat `$XDG_RUNTIME_DIR` tidak diatur: kontainer, WSL1, distro minimal). Hanya satu soket ini yang pindah; soket dtach setiap sesi (`/tmp/claude-dtach-*.sock`) tidak terpengaruh. Atur `AIPAGER_SOCKET_PATH` kalau kamu butuh tempat tertentu.

## <a id="update-says-the-restart-would-kill-sessions-killmode"></a>`/update` bilang mulai ulang akan mengakhiri sesi (KillMode)

File layanan yang ditulis sebelum versi ini tidak punya baris `KillMode=`, jadi systemd memakai `control-group`: menghentikan atau memulai ulang `aipager.service` mengakhiri setiap sesi Claude yang dimulai aipager. Karena itu `/update` memasang versi baru tapi tidak memulai ulang aipager, dan mencantumkan sesi yang akan dihentikan oleh mulai ulang. Perbaiki sekali:

```
aipager service install
```

Ini menulis ulang layanan dengan `KillMode=process`, memuat ulang systemd, dan memulai ulang aipager, dan mulai ulang itu sudah memakai pengaturan baru. Periksa dengan `systemctl --user show -p KillMode aipager.service` (`KillMode=process`). Dengan pengaturan ini, systemd menulis baris “left-over process” untuk sesi-sesimu di setiap mulai ulang; itu normal.

## <a id="aipager-update-cant-find-uv--pipx"></a>`aipager update` tidak menemukan uv atau pipx

`aipager update` mencari installer dengan path absolut di `PATH`-mu, dan juga di `~/.local/bin`, `~/.cargo/bin`, prefix Homebrew, `/usr/bin`, dan `/bin`. Kalau tetap bilang tidak menemukan `pipx`, installer yang membuat aipager ini sudah tidak ada atau berada di tempat lain; pasang ulang dengan alat yang ada (`pipx install --force aipager`, `uv tool install --reinstall aipager`).

## <a id="update-refused-editable--not-your-install"></a>Pembaruan ditolak: editable atau bukan instalasimu

`/update` dan `aipager update` hanya memperbarui instalasi milik penggunamu dan bisa ditulisnya, lewat installer yang membuatnya. Keduanya menolak, dan menyebut alasannya, untuk:

- salinan kerja dalam mode editable (untuk pengembangan): perbarui dengan `git pull`;
- instalasi lewat Nix, Snap, Docker, atau paket sistem: pakai pembaruan milik sistem itu;
- instalasi milik pengguna lain (misalnya venv root di `/opt`): perbarui sebagai pengguna itu;
- instalasi dari folder lokal (`pipx install /path/to/aipager`): `tidak bisa diperbarui dari sini: it was installed from a local folder; update it from that folder`. Ambil versi baru ke folder itu lalu pasang ulang dari sana, atau pindah ke PyPI dengan `pipx install --force aipager`;
- instalasi yang installer-nya tidak bisa diketahui (`could not tell which installer owns this aipager`): pasang ulang dengan `uv tool install aipager` atau `pipx install aipager`.

## <a id="update-failed-unexpected-argument---refresh"></a>Pembaruan gagal: unexpected argument '--refresh'

aipager 1.0.0 dan sebelumnya menjalankan `uv tool upgrade aipager --refresh`, padahal tidak ada versi uv yang menerima flag itu, jadi setiap pembaruan instalasi uv berhenti dengan `error: unexpected argument '--refresh' found` dan tidak memulai ulang apa pun. Versi yang lebih baru menjalankan `uv tool upgrade aipager`. Untuk melewati titik ini sekali, perbarui dari terminal di komputer ini (`aipager update` di versi itu punya masalah yang sama):

```
uv tool upgrade aipager
systemctl --user restart aipager.service
```

Tanpa layanan (atau di macOS), mulai ulang aipager dengan cara yang sama seperti ia dimulai.

## <a id="claude-code-update-installed-with-npm-into-a-folder-you-cant-write-to"></a>Pembaruan Claude Code: dipasang dengan npm ke folder yang tidak bisa kamu tulisi

Claude Code yang dipasang dengan `sudo npm install -g` ada di folder yang hanya bisa diubah root (seperti `/usr/lib/node_modules`), jadi `claude update` sebagai penggunamu gagal dengan `global folder isn't writable`. `/update` dan Mini App menampilkan versi yang lebih baru dengan `tidak bisa diperbarui dari sini: installed with npm into a folder your user can't write to` dan tanpa tombol untuknya. Jalankan `claude install` sekali sebagai pengguna yang menjalankan aipager: ini beralih ke installer Claude Code sendiri di folder home-mu, yang bisa memperbarui tanpa sudo. Lalu mulai ulang aipager (`systemctl --user restart aipager.service`, atau dengan cara ia dimulai): aipager yang sedang berjalan memegang Claude Code yang ditemukannya saat mulai, dan setelah mulai ulang ia mengambil yang terbaru dari yang ditemukannya. Kalau `claude_path` di `aipager.yaml` (`aipager doctor --fix` bisa mengaturnya) atau `AIPAGER_CLAUDE_BIN` menunjuk ke salinan npm, arahkan ke `~/.local/bin/claude` atau hapus dulu, karena keduanya selalu menang.

## <a id="updated-but-the-version-didnt-change"></a>Sudah diperbarui, tapi versinya tidak berubah

Instalasi pipx dari URL git diperbarui dari sumber git itu, bukan dari PyPI. Saat di sumbernya tidak ada yang lebih baru, `/update` menampilkan `✅ aipager sudah di A - instalasi pipx ini diperbarui dari sumber git-nya, dan di sana tidak ada versi yang lebih baru.`. Push atau pilih versi baru di sana, atau pindah ke PyPI dengan `pipx install --force aipager`.

## <a id="the-daemon-didnt-come-back-after-an-update"></a>aipager tidak kembali setelah pembaruan

Pembaruan memeriksa bahwa versi baru bisa dimuat sebelum memulai ulang apa pun, tapi kalau aipager yang baru tetap gagal mulai, systemd mencoba lagi setiap 5 dtk. Lihat penyebabnya:

```
journalctl --user -u aipager.service -n 50
```

Pasang ulang versi sebelumnya dengan installer yang sama, lalu mulai ulang:

```
pipx install --force aipager==<previous>        # atau:
uv tool install --force aipager==<previous>     # atau:
<venv>/bin/python -m pip install aipager==<previous>
systemctl --user restart aipager.service
```

Instalasi pipx dari path lokal: `pipx install --force /path/to/aipager` pada commit yang lama. Kalau ada pengumuman usang yang menunggu, hapus dengan `rm -f ~/.local/share/aipager/update-restart.json` (toh diabaikan setelah 24 jam); `~/.local/share/aipager/update.lock` hanyalah file kunci dan aman dihapus saat tidak ada pembaruan yang berjalan.

## <a id="an-update-was-interrupted-when-aipager-shut-down"></a>“Sebuah pembaruan terputus saat aipager dimatikan”

aipager berhenti (atau dimulai ulang) saat `/update` sedang memasang, dan installer ikut berhenti bersamanya, jadi pemasangannya mungkin baru tertulis setengah. Kalau aipager atau Claude Code berperilaku aneh, perbaiki: untuk aipager, jalankan perintah pasang ulang dari pesannya (misalnya `pipx install --force aipager`) lalu `systemctl --user restart aipager.service`; untuk Claude Code, jalankan lagi `claude update`.

## <a id="a-chat-is-in-the-wrong-language"></a>Sebuah chat memakai bahasa yang salah

Bahasa aipager berasal dari yang dipilih untuk chat itu, bukan dari pesan-pesan di dalamnya. Buka `/language` di chat itu dan pilih bahasa yang kamu mau: pilihan ini mengalahkan semuanya. Di chat pribadi, **Otomatis** mengikuti bahasa aplikasi Telegram-mu (Pengaturan → Bahasa di Telegram); bahasa yang tidak dimiliki aipager ditampilkan dalam bahasa Inggris. Chat pribadi yang sudah ada sebelum ada bahasa tetap berbahasa Inggris sampai kamu memilih bahasa. Grup tetap berbahasa Inggris sampai seorang admin memilihkan bahasa untuknya. Bahasa hanya mengubah teks aipager sendiri: Claude menjawab dalam bahasa yang kamu pakai untuk menulis kepadanya, apa pun yang ditampilkan aipager. Lihat [Bahasa](commands.md#languages).

## <a id="the-bot-description-is-not-the-one-i-wrote"></a>Deskripsi bot bukan yang ditulis di BotFather

aipager memasang deskripsi dan deskripsi singkat bot dalam setiap bahasa yang dikuasainya, untuk orang yang aplikasi Telegram-nya memakai bahasa itu, dan Telegram menampilkan teks itu kepada mereka sebagai ganti yang ditulis di BotFather. aipager hanya melakukannya selama kamu tidak punya deskripsi sendiri, jadi deskripsi yang ditulis sebelum versi ini dipasang terlihat oleh semua orang. Kalau deskripsi itu ditulis setelah aipager memasang miliknya, orang dalam bahasa-bahasa itu tetap melihat deskripsi aipager. Untuk menampilkan milikmu kepada mereka, hapus teks aipager untuk setiap bahasa lewat Bot API (`setMyDescription` dan `setMyShortDescription` dengan teks kosong dan `language_code` itu); aipager tidak akan memasangnya lagi selama milikmu ada.

## <a id="aipager-says-languagesjson-could-not-be-used"></a>aipager bilang languages.json tidak bisa dipakai

aipager menyimpan apa yang dipelajarinya tentang bahasa (siapa yang sudah ada di sini sebelum ada bahasa, bahasa Telegram terakhir yang dilihatnya pada setiap orang, dan apakah tawaran sekali saja dan baris kontak pertama sudah terkirim) di `~/.local/state/aipager/languages.json`. Kalau file ini tidak bisa dibaca, aipager menyisihkannya sebagai `languages.json.bad-<time>` di folder yang sama, mencatat peringatan di log (lagi di setiap mulai, sampai ada keputusan), dan menahan semua chat dalam bahasa Inggris tanpa tawaran bahasa sampai ada keputusan: kembalikan file lama kalau kamu bisa memperbaikinya, atau hapus `languages.json` lalu mulai ulang, dan aipager kembali menganggap setiap orang yang dikenalnya sudah ada di sini sebelumnya (bahasa Inggris, sampai orang itu memilih). Bahasa yang dipilih seseorang tidak disimpan di file ini dan tidak pernah hilang.

## <a id="right-to-left-text-looks-wrong"></a>Teks kanan-ke-kiri terlihat salah

Dalam bahasa Persia dan Arab, aipager memasukkan tanda arah yang tak terlihat ke teksnya, supaya setiap baris terbaca dari kanan ke kiri sementara nama sesi, perintah, dan kode tetap dari kiri ke kanan. Kalau sebuah baris tetap terlihat berantakan, penyebabnya paling sering klien Telegram yang menggambar teks berarah campuran dengan caranya sendiri: coba di klien lain (ponsel dibandingkan Desktop atau Web) sebelum melaporkannya. Jawaban Claude sendiri ditampilkan seperti yang ditulis Claude, tanpa tanda tambahan.

## <a id="a-translated-keyboard-word-does-nothing-in-another-chat"></a>Kata keyboard yang diterjemahkan tidak berfungsi di chat lain

Keyboard setiap chat memakai bahasa chat itu. Kata-kata keyboard utama (status, stop, new, kill, dan lainnya) dipahami di setiap chat dalam bahasa apa pun, dan kata bahasa Inggris selalu berfungsi. Kata dari tombol templat atau perintah lain ceritanya: kalau diketik di chat dengan bahasa lain, itu pesan untuk Claude, bukan tombol, jadi “Hapus percakapan” dalam bahasa Indonesia tidak akan pernah menjalankan `/clear` di chat berbahasa Persia atau Inggris. Pakai tombolnya sendiri atau perintah bahasa Inggrisnya (`/clear`). Kalau keyboard terlihat usang setelah bahasa chat diubah, kirim `/start` untuk mendapatkan yang terbaru. Kata yang juga merupakan nama salah satu sesimu akan menyapa sesi itu. `/mode` selalu membaca `ask`, `auto`, dan `full` sebagai mode, dan kata mode yang diterjemahkan sebagai sesi hanya kalau ada sesi berjalan yang namanya persis kata itu.

## <a id="still-stuck"></a>Masih buntu?

Kirim laporan masalah: ketuk **Laporkan masalah** di bawah `/help` di chat pribadimu dengan bot, atau jalankan `aipager report` di terminal. Kamu melihat seluruh laporan sebelum apa pun dikirim, dan isinya tidak memuat chat, pesan, nama, path, atau token (lihat [Laporan masalah](problem-reports.md)). Setelah terkirim, kamu mendapat kode referensi seperti `ap1-7c41d09e2b3a`.

Lalu, kalau kamu ingin membahasnya, buka issue di [github.com/dev-aly3n/aipager/issues](https://github.com/dev-aly3n/aipager/issues) dan sebutkan kode referensi itu. Tolong jangan tempel keluaran `aipager doctor` atau `aipager logs` ke issue: isinya ada ID chat-mu, path folder home-mu, nama sesi, dan awal pesan-pesanmu. Kalau dibutuhkan satu baris log, orang yang merawat aipager akan meminta baris itu saja, dan kamu bisa menghapus hal-hal pribadi darinya dulu.

## <a id="see-also"></a>Lihat juga

- [Arsitektur](architecture.md): apa yang dikerjakan setiap bagian.
- [Model keamanan](security.md): apa yang disentuh aipager dan apa yang tidak.
