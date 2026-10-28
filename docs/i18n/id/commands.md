# Perintah dan antarmuka bot

<!-- i18n-source: docs/commands.md @ e7eb4004 -->

> Halaman ini diterjemahkan dari bahasa Inggris; kalau keduanya berbeda, versi bahasa Inggris yang berlaku. ([English](../../commands.md))

Cara mengendalikan aipager dari Telegram. Ada empat cara memberi input: perintah slash, tombol keyboard, dasbor Mini App, dan pesan bebas (teks, file, pesan suara).

Bot hanya menerima input dari chat yang diatur; tentang batas kepercayaannya, lihat [Model keamanan](security.md).

## <a id="slash-commands"></a>Perintah slash

Perintah-perintah ini didaftarkan di `aipager/bot/lifecycle.py`. Menu perintah Telegram menampilkannya dan diperbarui saat aipager mulai dan setiap kali sesi berubah.

| Perintah | Argumen | Fungsinya |
|---|---|---|
| `/start` | - | Layar utama: sesi-sesi chat ini, ke mana pesan biasa pergi (`✍️ Pesan masuk ke x1.`), dan `🆕 Sesi baru`, `⚙️ Pengaturan`, serta, kalau chat ini punya sesi yang sudah berakhir dan bisa dikembalikan, `↩️ Lanjutkan`. Juga menampilkan keyboard tetap. |
| `/help` | - | Panduan singkat per tugas (mulai, bicara, kelola, sesi, pengaturan, dan, untuk admin, `/update`) dengan tips bahwa mengetuk perintah langsung mengirimnya: tekan lama (ponsel) atau tekan Tab (desktop) untuk menambah teks dulu. Untuk pemilik instalasi (orang di chat pribadi instalasi itu sendiri dengan bot) ada juga tombol `🐞 Laporkan masalah`: tombol itu membuka, di chat pribadi itu, pratinjau laporan masalah yang persis dengan tombol Kirim, Tambah catatan, dan Batal. Tidak ada yang dikirim sampai kamu mengetuk Kirim ([isi laporan](problem-reports.md)). |
| `/app` | - | Membuka dasbor Mini App (sesi, tampilan diff, pengaturan). |
| `/status` | - | Sesi-sesi chat ini sebagai daftar yang bisa kamu tindaki. Setiap sesi menyebut keadaannya dengan kata-kata (`⚙️ sedang bekerja 3:12 · Bash: run tests`, `⏳ menunggu jawabanmu · Bash: make deploy`, `💤 siap`), dengan `✍️` pada sesi tempat pesan biasa masuk, dan baris kedua berisi model, konteks, biaya, apa yang mengantre (`antrean 3 (1 di antrean, 2 catatan)`), serta agen dan perintah latar belakang yang berjalan. Setiap sesi punya satu baris tombol: `✍️ x1` mengarahkan pesan berikutnya ke sana, `⏹ Hentikan` selama sesi itu bekerja, `Jawab` selama sesi itu menunggu jawabanmu, dan `⋮` untuk sisanya. Di bawahnya: `🆕 Baru` dan `⚫ Sudah berakhir (n)` untuk sesi yang sudah berakhir (lanjutkan, hapus, atau keluarkan semuanya dari daftar; sesi itu tetap ada di `/resume`). |
| `/stop [label]` | opsional | Memutus tugas sesi saat ini. Tanpa nama, perintah ini menghentikan satu-satunya sesi chat ini yang sedang bekerja (bahkan kalau pesanmu masuk ke sesi lain; sesi yang masih punya agen atau perintah latar belakang yang berjalan dihitung sedang bekerja), menanyakan yang mana lewat daftar kalau ada beberapa, dan menulis `Sedang tidak ada yang dikerjakan.` kalau tidak ada. `/stop x1` (atau `/x1 stop`) menghentikan x1 (atau menjawab `x1 sedang tidak mengerjakan apa pun.` kalau sesi itu tidak bekerja). Juga membuang pesan di antrean dan menyebut berapa yang dibuang. Pada sesi di latar belakang Claude Code yang sedang bekerja, perintah ini menghentikan pekerjaan tugas latar belakang itu dan mengembalikan sesinya. |
| `/now` | - | Mengirim pesan yang ditahan Claude di antrean sesi aktif sekarang juga, bukan setelah langkah saat ini (tombol “kirim sekarang” milik Claude Code sendiri, Ctrl+X Ctrl+S). Berfungsi kapan saja, termasuk saat pengiriman ke chat dimatikan atau chat dalam mode minimal; menjawab `Tidak ada pesan yang menunggu di antrean` saat Claude tidak menahan apa pun. Lihat [Mengirim pesan antrean sekarang](#send-a-queued-message-now). |
| `/new [name] [first message]` | semua opsional | Memulai sesi. `/new x1` langsung memulainya, dan `/new x1 fix the tests` juga mengirim “fix the tests” sebagai pesan pertama. Kalau dikirim sendiri (misalnya diketuk dari menu), `/new` menanyakan nama, dan pesan berikutnya yang kamu kirim dipakai persis sebagai argumennya, jadi `/new` lalu `x1 fix the tests` hasilnya sama. Di grup, balas kartunya dengan nama (atau sebut bot: `@aipagerbot x1`), karena pesan biasa tidak sampai ke bot di sana. Hanya pesan berikutnya itu: kalau kamu melakukan hal lain dulu (foto, perintah lain, balasan ke pesan lain, templat keyboard, tombol lain), hal itu diproses seperti biasa dan kartunya ditutup; `/status`, `/settings`, dan menu keyboard membiarkannya tetap terbuka. Kartu yang bertanya juga menampilkan mode, model, dan folder yang akan didapat sesi itu, dengan tombol untuk menggantinya dulu. Semua jalur berakhir di kartu “x1 siap” yang sama: apa yang didapat sesi itu, bahwa pesan berikutnya masuk ke sana, dan tombol sekali ketuk untuk pindah mode atau mengganti model. Sesi baru semua orang dimulai dalam Mode Auto; apa yang dipakai /new diubah di `/settings` → Sesi baru. `!` di depan nama memulai sesi dalam Mode Akses penuh, untuk admin (peran `owner` dan `admin`) atau orang di chat pribadinya sendiri dengan bot yang perannya bisa mengirim pesan dan menjawab Claude (`user`, bukan `read_only`); untuk semua orang lain sesi itu dimulai dalam Mode Auto, dan kartunya menyebut alasannya. Nama yang sedang dipakai menawarkan `↩️ Pindah ke sesi itu` (saat sesi itu berjalan) atau `♻️ Lanjutkan` (saat sudah berakhir), `🆕 Ganti dengan yang baru`, dan `↩️ Batal`, hanya kepada orang yang mengirim `/new`. Kartu yang meminta nama kedaluwarsa setelah 10 menit tidak dipakai (`⏱ Kartu sesi baru ini sudah kedaluwarsa. Kirim /new lagi.`). Anggota yang perannya tanpa `can_manage` (`user`, `read_only`) memulai sesi hanya di dalam [folder utama](groups.md#root-folders) chat itu: folder kartunya adalah folder utama, halaman “Folder” menampilkan folder itu dan folder tepat di dalamnya (tanpa baris “Folder bawaan”), “Folder baru” membuat folder di dalam yang dipilih, dan “Pindah ke sesi itu”, “Lanjutkan”, serta “Ganti” untuk sesi yang berjalan di luar folder utama ditolak (“Sesi ini bekerja di luar foldermu ~/..., jadi peranmu tidak boleh melanjutkan, memulai ulang, atau mengganti mode-nya. Minta bantuan admin.”). |
| `/resume [label]` | opsional | Melanjutkan sesi yang sudah berakhir berdasarkan nama, atau membuka daftar. Sesi kembali dalam mode yang dipakainya. Sesi yang dulu dalam Mode Akses penuh kembali dalam Akses penuh hanya untuk admin atau orang di chat pribadinya sendiri; untuk semua orang lain sesi itu kembali dalam Mode Auto, dan balasannya mengatakan itu (`♻️ x1 dilanjutkan dalam Mode Auto: Mode Akses penuh perlu admin.`). Sesi yang disimpan aipager versi lama dalam Auto lamanya (yang berjalan tanpa pemeriksaan) kembali dalam Mode Akses penuh, dan yang disimpan dalam Ask kembali dalam Mode Tanya. Sesi yang sudah berakhir tetap ada di daftar selama `GONE_SESSION_MAX_AGE_DAYS` hari (bawaannya 14), lalu keluar dari daftar sesi; percakapan Claude yang tersimpan tidak disentuh. Anggota yang perannya tanpa `can_manage` tidak bisa melanjutkan sesi yang foldernya di luar [folder utama](groups.md#root-folders) chat itu (pemilik dan admin bisa); sama halnya dengan Mulai ulang dan pergantian mode apa pun, termasuk pergantian tanpa mulai ulang antara Tanya dan Auto. Melanjutkan mengikuti percakapan yang sudah pindah di Claude Code (yang terbaru yang kembali), dan kalau tugas latar belakang Claude Code masih memegang percakapan itu, tugas itu dihentikan dulu kalau sedang tidak mengerjakan apa pun, atau kamu ditanya dulu (`Hentikan dan kembalikan` / `Biarkan saja`) selama tugas itu masih bekerja; sama halnya dengan Mulai ulang. |
| `/kill [label]` | opsional | Mengakhiri sesi, selalu setelah konfirmasi: `⏹ Akhiri x1? Claude berhenti bekerja dan sesinya ditutup.` dengan `[⏹ Akhiri] [Batal]`. Tanpa nama, langsung ke konfirmasi kalau chat ini punya satu sesi yang berjalan, kalau tidak ke daftar sesi chat ini yang berjalan. Juga ada di `/status` → `⋮` → `⏹ Akhiri sesi`. |
| `/restart [label]` | opsional | Memulai ulang sesi setelah konfirmasi: Claude berhenti lalu mulai lagi dengan percakapannya tetap utuh, dalam mode yang terakhir dipakai aipager untuk meluncurkannya atau mengalihkannya. Sesi yang dimulai di terminal dengan `aipager session` kembali dalam Mode Tanya kecuali mode-nya sudah diganti dari Telegram sejak itu (aipager tidak tahu terminal meluncurkannya dalam mode apa dan tidak pernah menebak yang lebih longgar); sama saja saat melanjutkannya dari Telegram. Sesi yang percakapannya pindah ke latar belakang Claude Code justru dikembalikan (lihat [pemecahan masalah](troubleshooting.md#a-session-moved-to-claude-codes-background)). |
| `/rename [label] [new]` | opsional | Memberi sesi nama baru (juga sesi yang sudah berakhir). `/rename x1 newname` langsung mengganti namanya. Dengan kata yang lebih sedikit, perintah ini bertanya: langsung nama baru kalau perintah itu hanya bisa berarti satu sesi, kalau tidak, daftar pilihan (satu kata setelah `/rename` tidak memilih sesi, karena bisa saja nama lama atau nama baru). Di grup, balas pertanyaannya dengan nama. |
| `/delete [label]` | opsional | Menghapus sesi yang sudah berakhir dari daftar, setelah konfirmasi. |
| `/diff [label]` | opsional | Menampilkan git diff dari folder kerja sesi. Tanpa nama, menampilkan sesi tempat pesanmu masuk. |
| `/clearqueue` | - | Membuang semua pesan yang belum diambil sesi aktif (baik yang ditahan aipager maupun yang sudah ada di antrean di dalam Claude) tanpa memutus tugas yang sedang berjalan. Menjawab berapa yang dibuang. |
| `/mode [label] [ask\|auto\|full]` | opsional | Menampilkan mode izin sesi, dengan satu tombol untuk pindah ke setiap mode lain: `✍️ x1 dalam 🤖 Mode Auto.` `[💬 Beralih ke Mode Tanya]` `[🔓 Beralih ke Mode Akses penuh]`. Ada tiga mode. **💬 Mode Tanya**: Claude bertanya sebelum setiap tindakan. **🤖 Mode Auto** (bawaan): pemeriksaan keamanan Claude Code sendiri menjalankan tindakan aman dan menghentikan yang berisiko supaya kamu yang memutuskan. **🔓 Mode Akses penuh**: semuanya berjalan tanpa pemeriksaan apa pun (`--dangerously-skip-permissions`); hanya admin, atau orang di chat pribadinya sendiri dengan bot yang perannya bisa mengirim pesan dan menjawab Claude (`user`, bukan `read_only`), yang bisa memilihnya. Auto dan Akses penuh hanya mengubah permintaan izin milik Claude Code sendiri: hook aipager tetap menerapkan aturan peran anggota. `/mode ask`, `/mode auto`, `/mode full`, atau `/mode x1 ask` langsung mengalihkan; kata mode itu sendiri dalam bahasa chat juga berfungsi, tanpa kata “mode” (`/mode x1 Tanya` untuk 💬 Mode Tanya, `/mode x1 Penuh` untuk 🔓 Mode Akses penuh). Saat ada sesi berjalan di chat ini yang namanya persis kata itu (misalnya sesi bernama `demander`), kata itu menyebut sesinya: `/mode demander` menampilkan mode sesi itu, dan `/mode x1 demander` tetap mengalihkan x1. `ask`, `auto`, dan `full` selalu berarti mode, dalam urutan apa pun (`/mode x1 ask` atau `/mode ask x1`); hanya saat kedua kata adalah kata mode dan hanya satu yang merupakan nama sesi yang berjalan, kata itu menyebut sesinya, jadi dengan sesi bernama `full`, `/mode full ask` maupun `/mode ask full` mengalihkannya ke Mode Tanya, sedangkan `/mode full` saja tetap berarti mode. Peralihan antara Tanya dan Auto terjadi tanpa mulai ulang dan kapan saja (bahkan saat Claude bekerja): aipager menekan Shift+Tab di sesi itu, seperti yang akan kamu lakukan, dan kartunya menulis `⚙️ x1 beralih ke 🤖 Mode Auto tanpa dimulai ulang.` sampai Claude Code melaporkan mode baru di langkah berikutnya, lalu menampilkan hasilnya. Kalau Mode Auto tidak tersedia untuk sesi itu (karena model atau paketnya), kartunya mengatakan itu dan sesi tetap dalam Mode Tanya. Tidak saat sesi sedang menampilkan pertanyaan atau permintaan izin, termasuk yang muncul tepat saat tombolnya akan ditekan: jawab itu dulu (tidak ada tombol yang masuk ke permintaan itu). Pindah ke Akses penuh butuh admin (atau orang di chat pribadinya sendiri) dan konfirmasi, dan memulai ulang sesi (percakapannya tetap utuh); keluar dari Akses penuh juga memulai ulangnya, langsung. Sesi yang diluncurkan dalam Akses penuh dimulai ulang di setiap pergantian, bahkan setelah dialihkan ke Tanya atau Auto di terminal, begitu juga sesi yang melaporkan Akses penuh sejak diluncurkan (sesi terminal yang dimulai dengan `--dangerously-skip-permissions`): di sana Shift+Tab bisa memutarnya kembali ke Akses penuh. Untuk sesi yang sedang bekerja, mulai ulang menawarkan `🛑 Hentikan & alihkan` / `⏳ Nanti saja`. Anggota yang perannya tanpa `can_manage` tidak bisa mengganti mode sesi yang foldernya di luar [folder utama](groups.md#root-folders) chat itu, baik tanpa mulai ulang maupun dengan mulai ulang. Mengetuk pergantian mengubah kartunya sendiri, bukan mengirim pesan kedua. `/perms` adalah nama lamanya dan berfungsi sama. |
| `/settings` | - | Cara sesi chat ini tampil dan menjawab: bahasa aipager, tata letak pesan, pratinjau perubahan, pembaruan kartu tugas panjang, format, panjang jawaban, tingkat kosakata; dengan apa `/new` memulai sesi; preferensi per sesi; dan, di chat pribadi pemilik, tawaran laporan masalah. Lihat [Pengaturan](#settings). Di grup, siapa pun bisa melihat, tapi hanya admin yang bisa mengubah pengaturan. |
| `/language` | - | Bahasa yang dipakai aipager untuk pesan dan tombolnya sendiri di chat ini (daftar yang sama dengan baris pertama `/settings`). Setiap bahasa ditampilkan dengan namanya sendiri: English, lalu Español, Русский (Rusia), Português (Brasil), 简体中文 (Tionghoa Sederhana), Français, Deutsch (Jerman), 한국어 (Korea), العربية (Arab), Bahasa Indonesia, Українська (Ukraina), Italiano, Türkçe (Turki), فارسی (Persia), dan Tiếng Việt (Vietnam). Chat pribadi yang dimulai setelah versi ini mengikuti bahasa aplikasi Telegram-mu; chat yang sudah ada sebelumnya tetap berbahasa Inggris sampai kamu memilih (lihat [Bahasa](#languages)). Dalam bahasa Spanyol, Portugis, Prancis, Italia, dan Indonesia, kata-kata keyboard bawah tetap `status`, `stop`, `new`, dan `kill`, sama seperti perintahnya. Bahasa Jerman dan Turki memakai `Status`, `Stop`, `Neu…`, dan `Beenden…`, serta `Özet`, `Stop`, `Oluştur`, dan `Sonlandır`; kata bahasa Inggris tetap berfungsi kalau diketik. Dalam bahasa Persia dan Arab, teks aipager dibaca dari kanan ke kiri, dengan angka Persia atau Arab; nama sesi, perintah, nama model, dan kode tetap apa adanya. Daftar itu menyebutkan apa yang diubahnya: hanya teks aipager, di sini dan di Mini App; sesi Claude-mu tidak berubah, dan Claude menjawab dalam bahasa yang kamu pakai untuk menulis kepadanya. Di grup, siapa pun bisa melihat, tapi hanya admin yang bisa mengubahnya. `language` adalah nama sesi yang dicadangkan. |
| `/whoami` | - | Menunjukkan siapa kamu di sini: dalam mode tim, namamu, peranmu di chat ini, dan aturan yang diberikan peran itu kepadamu. |
| `/update` | - | Khusus admin (peran `owner` dan `admin`, atau peran dengan `can_manage`). Mengecek apakah ada versi baru aipager dan Claude Code, lalu dengan satu tombol memperbarui yang punya versi baru. Lihat [Pembaruan](#update). |

Menu `/` menampilkan sesimu lebih dulu (`/x1 · Bicara dengan x1`), lalu perintah yang sering dipakai: `new`, `status`, `stop`, `now`, `resume`, `mode`, `settings`, `language`, `help` (dan `app` saat Mini App menyala, hanya di chat pribadi). `kill`, `restart`, `rename`, `delete`, `diff`, `clearqueue`, `perms`, `whoami`, dan `update` tidak ada di menu tapi berfungsi kalau diketik; tindakan sesi juga ada di `/status` → `⋮`.

**Perintah yang bekerja pada satu sesi** (`/stop`, `/mode`, `/kill`, `/restart`, `/rename`, `/delete`, `/diff`) mengikuti satu aturan:

- Dengan nama (`/restart x1`), perintah bekerja pada sesi itu di chat ini (`/rename` butuh nama lama dan nama baru untuk ini, lihat di atas).
- Tanpa nama, perintah bekerja pada satu-satunya sesi yang bisa dimaksud perintah itu (satu-satunya yang sedang bekerja untuk `/stop`, satu-satunya yang diluncurkan untuk `/kill` dan `/restart`, satu-satunya yang sudah berakhir untuk `/delete`), lewat konfirmasi biasa kalau ada.
- Kalau tidak, perintah menampilkan daftar yang hanya berisi sesi chat ini yang cocok untuk perintah itu: lebih dulu sesi tempat pesanmu masuk, ditandai `✍️`, lalu sisanya menurut nama, dan `✖️ Batal`. Memilih salah satu membawa ke kartu yang sama seperti mengetik namanya.
- Di grup, tanpa nama, perintah hanya bekerja pada sesimu sendiri saat ini, kalau perintah itu cocok untuknya; kalau tidak, selalu menampilkan daftar, bahkan dengan satu sesi, karena sesi itu bisa milik anggota lain (lihat [mode tim](groups.md#talking-to-a-session-in-a-group)).

`/mode` dan `/diff` lebih dulu melihat sesi tempat pesanmu masuk.

### <a id="settings"></a>Pengaturan

`/settings` (dan tab “Pengaturan” di Mini App, dengan pilihan yang sama) mengatur cara sesi chat ini tampil dan menjawab. Setiap chat punya pengaturannya sendiri; di grup, siapa pun bisa melihat, tapi hanya admin yang bisa mengubah pengaturan. Di chat pribadimu sendiri, kamu yang mengubahnya, apa pun peranmu, di Telegram dan di Mini App. Baris dengan ✅ adalah yang tidak memakai nilai bawaan (“Tata letak pesan” selalu menampilkan pilihannya).

| Pengaturan | Pilihan (bawaannya yang pertama) | Fungsinya |
|---|---|---|
| 🌐 Bahasa | Otomatis (bahasa Telegram-mu), di chat pribadi; English | Bahasa pesan dan tombol milik aipager sendiri di chat ini dan di Mini App, ditampilkan dengan namanya sendiri (daftar yang sama dengan `/language`). Tidak mengubah sesi Claude-mu: Claude menjawab dalam bahasa yang kamu pakai untuk menulis kepadanya. Yang ditawarkan: English, Español, Русский (Rusia), Português (Brasil), 简体中文 (Tionghoa Sederhana), Français, Deutsch (Jerman), 한국어 (Korea), العربية (Arab), Bahasa Indonesia, Українська (Ukraina), Italiano, Türkçe (Turki), فارسی (Persia), dan Tiếng Việt (Vietnam); “Otomatis” mengikuti bahasa aplikasi Telegram-mu (lihat [Bahasa](#languages)). |
| 🖼 Tata letak pesan | Kartu kerja + jawaban terpisah; Jawaban di dalam kartu kerja; Hanya jawaban, kartu dihapus | Bagaimana tugas yang selesai terlihat. “Kartu kerja + jawaban terpisah” mempertahankan kartu di atas jawaban sebagai catatan bagaimana jawaban itu didapat; “Jawaban di dalam kartu kerja” menaruh jawaban di dalam kartu; “Hanya jawaban” menghapus kartu dan membiarkan jawaban sendirian. Bawaannya “Kartu kerja + jawaban terpisah” (`KEEP_FINISHED_CARD=0` menjadikan “Hanya jawaban” untuk chat yang belum pernah memilih). Lihat [jawaban](#idle-responses). |
| 📝 Pratinjau perubahan (diff) | Mati; Nyala | “Nyala” menerbitkan setiap `Write`/`Edit` sebagai pesan diff tersendiri di bawah kartu kerja. “Mati” mempertahankan satu kartu dan satu jawaban; kartunya tetap mencantumkan setiap edit, dan Mini App punya tampilan diff. |
| ⏱ Pembaruan kartu tugas panjang | Nyala; Mati | “Nyala”: kartu kerja diperbarui setiap 10 dtk setelah 2 menit tugas dan setiap 30 dtk setelah 10 menit (tidak pernah lebih lambat, berapa lama pun tugas berjalan), dan jamnya lalu bergerak dalam langkah 30 dtk; “Mati” mempertahankan laju menit-menit pertama sepanjang tugas (lihat [pemecahan masalah](troubleshooting.md#a-long-turns-card-refreshes-less-often)). |
| ✏️ Format sederhana | Mati; Nyala | “Nyala” meminta Claude menulis hanya teks biasa dan daftar dengan tanda hubung: tanpa tabel, blok kode, judul, atau huruf tebal. |
| 📏 Panjang jawaban | Tanpa aturan; Sangat pendek; Pendek; Sedang; Panjang | Meminta Claude menjawab sepanjang ini. |
| 🎓 Tingkat kosakata | Tanpa aturan; Kata sederhana; Normal; Mahir | Meminta Claude memakai kosakata ini. |
| 🎨 Tampilan | Tombol Hentikan, Tombol Izinkan dan Tolak, Tombol App, Menu pengaturan | Cara tombol aipager terlihat di chat ini; semuanya dimulai dari Biasa (tampilan bawaan Telegram). Telegram hanya membolehkan bot mewarnai tombol biru, hijau, atau merah, jadi: tombol Hentikan (yang di kartu kerja dan yang di bawah pertanyaan atau permintaan izin) Biasa, Biru, Hijau, atau Merah; Izinkan dan Tolak Biasa atau Berwarna (Izinkan dan Selalu izinkan hijau, Tolak merah); tombol App (di pesan yang disematkan dan di bawah `/app`) Biasa, Biru, Hijau, atau Merah (tombol menu di samping kotak pesan tidak bisa diwarnai); menu pengaturan Biasa (✅ menandai pilihan saat ini) atau pilihan saat ini juga biru. Berlaku untuk seluruh chat, bukan per sesi; di grup hanya admin yang bisa mengubahnya. Aplikasi Telegram yang tidak mendukung warna menampilkan semua tombol biasa. Tab Pengaturan di Mini App punya pilihan yang sama di bagiannya sendiri, Tampilan, setelah Pengaturan chat, masing-masing dengan titik berwarna. |
| 🆕 Sesi baru | Izin (mode), Model, Folder | Dengan apa `/new` memulai sesi saat kamu tidak memilih: Mode Tanya, Auto (bawaan), atau Akses penuh, model dari daftar model atau model bawaan Claude Code, dan foldernya. Hanya admin, atau orang di chat pribadinya sendiri dengan bot, yang bisa memilih Akses penuh di sini (layar chat pribadi tidak menampilkannya ke orang lain), dan pilihan itu tidak pernah berlaku untuk orang yang tidak boleh memakainya: sesi seperti itu dimulai dalam Mode Auto. |
| 👤 Preferensi per sesi | sesi, lalu pengaturan mana pun di atas kecuali “Sesi baru” | Nilai untuk satu sesi (juga ada di `/status` → `⋮` → `👤 Preferensi`); ⭐ menandai pengaturan yang ditimpanya, dan “Pakai bawaan chat” menghapus timpaan itu. Halaman sesi di Mini App mengatur nilai yang sama. |
| 🐞 Laporan masalah | Tanya aku; Mati | Hanya di chat pribadi pemilik instalasi. “Tanya aku” membiarkan aipager sesekali menawarkan untuk mengirim laporan tentang error internal yang terjadi lebih dari sekali (paling sering sekali setiap 3 hari); “Mati” tidak pernah menawarkan (tombol “Laporkan masalah” di `/help` tetap berfungsi). Dua penolakan berturut-turut mematikan tawaran itu, dan baris ini lalu mengatakannya, dengan tombol untuk menyalakannya lagi. Lihat [laporan masalah](problem-reports.md#when-aipager-offers-a-report). |

Panjang jawaban, tingkat kosakata, dan format sederhana sampai ke Claude sebagai instruksi singkat yang ditambahkan ke setiap pesan dari Telegram (lewat hook, lihat [hooks](hooks.md#userpromptsubmit)), dengan nilai milik sesi itu sendiri kalau ada.

Dengan tata letak apa pun, setiap kartu kerja diakhiri baris status sesinya (`⏳`/`✅ nama · …`), dan setiap jawaban diawali baris hasilnya (`💬 nama`, ditambah `· Selesai (…)` kalau tidak ada kartu selesai yang tersisa untuk menampilkan angkanya); tata letak “Jawaban di dalam kartu kerja” menumpuk keduanya, masing-masing di bagiannya sendiri. Di atas baris status kartu kerja ada garis pemisah (dengan teks kecil), dan di tata letak “Jawaban di dalam kartu kerja” garis kedua memisahkan kartu yang selesai dari jawabannya. Dalam tata letak kartu, jawaban sengaja datang sedikit di belakang kartu yang selesai, supaya kamu melihat kartu itu bertuliskan “Selesai” sebelum jawabannya muncul di bawahnya; atur atau matikan jeda ini dengan `FINISH_CARD_GRACE_SECONDS` (detik, bawaannya 0,8; 0 mengirim keduanya bersamaan). Dalam tata letak kartu, tugas yang tidak memanggil alat apa pun tidak meninggalkan kartu: kartunya dihapus sebelum bertuliskan “Selesai”, dan baris pertama jawabannya menampilkan angkanya, seperti di tata letak “Hanya jawaban, kartu dihapus”.

### <a id="per-session-dynamic-commands"></a>Perintah milik setiap sesi

Satu perintah untuk setiap sesi yang berjalan, didaftarkan dengan namanya:

| Bentuk | Fungsinya |
|---|---|
| `/<label>` | Menjadikan `<label>` sesi tempat pesanmu masuk. Balasannya mengatakan itu beserta keadaan dan mode-nya (`✍️ Sekarang bicara dengan x1 · 💤 siap · 🤖 Mode Auto`, lalu “Kirim pesan, nanti masuk ke x1.”), menawarkan **Jawab** saat sesi itu menunggumu, dan **⋮ Lainnya** untuk menunya. Angka lengkapnya ada di `/status`. Di grup, balasannya justru “Balas pesan dari x1 (atau sebut @aipagerbot) untuk bicara dengan sesi itu.”, karena pesan biasa tidak sampai ke bot di sana (lihat [mode tim](groups.md#talking-to-a-session-in-a-group)). `/x1@aipagerbot`, yang dikirim Telegram saat kamu memilih `/x1` dari menu grup, berfungsi sama. |
| `/<label> <prompt>` | Mengirim `<prompt>` langsung ke sesi itu. Sesi itu juga menjadi tempat pesan biasa berikutnya masuk. |
| `/<label> stop` | Memutus tugas sesi itu saat ini. |

Hasil `/status` berasal dari data yang sama dengan yang ditampilkan `aipager status` di baris perintah; daftar sesinya sendiri tidak butuh panggilan ke Telegram.

Di komputer, `aipager status` dan `aipager session ls` memberi setiap sesi satu keadaan: `IDLE`, `BUSY`, `WAITING` (izin atau pertanyaan sedang menunggumu, dengan keterangan apa kalau diketahui, misalnya `permission: Bash: make deploy`), atau `GONE`. Dengan `--json`, setiap sesi juga punya `waiting_kind` (`permission` atau `question`) dan `waiting_summary`, keduanya `null` kalau sesi itu tidak menunggu.

## <a id="languages"></a>Bahasa

aipager berbicara English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی, dan Tiếng Việt, masing-masing ditampilkan dengan namanya sendiri.

- **Hanya teks aipager yang berubah.** Bahasa berlaku untuk pesan, tombol, kartu, dan Mini App milik aipager. Sesi Claude-mu tidak berubah, dan Claude menjawab dalam bahasa yang kamu pakai untuk menulis kepadanya. Nama sesi, perintah, nama model, dan kode tetap apa adanya.
- **`/language`** (atau baris pertama `/settings`) memilih bahasa chat tempatmu berada. Di chat pribadi, itu juga bahasamu sendiri: notifikasi singkat yang kamu lihat di grup dan Mini App-mu memakainya.
- **Otomatis** (hanya di chat pribadi) mengikuti bahasa aplikasi Telegram-mu, dan mengikutinya lagi kalau kamu menggantinya. Bahasa yang tidak dimiliki aipager ditampilkan dalam bahasa Inggris.
- **Chat baru dan lama.** Chat pribadi yang dimulai setelah versi ini mengikuti bahasa aplikasi Telegram-mu sampai kamu memilih bahasa, dan pesan pertamanya yang bukan bahasa Inggris diikuti satu baris singkat yang menyebutkan hal itu dan cara menggantinya. Chat yang sudah ada sebelumnya tetap berbahasa Inggris. Kalau aplikasi Telegram-mu memakai bahasa yang dikuasai aipager, kamu mendapat satu kartu yang menawarkan bahasa itu dengan tombol “Tetap bahasa Inggris”; kartu itu datang sekali, apa pun jawabanmu.
- **Grup** punya satu bahasa, diatur admin di `/language`; sampai saat itu grup berbahasa Inggris. Notifikasi singkat setelah ketukan datang dalam bahasa orang yang mengetuk: pilihannya di chat pribadi dengan bot, kalau tidak, bahasa aplikasi Telegram-nya kalau orang itu datang setelah versi ini, kalau tidak juga, bahasa grup. Lihat [mode tim](groups.md).
- **Kanan ke kiri.** Dalam bahasa Persia dan Arab, teks aipager dibaca dari kanan ke kiri, dengan angka Persia atau Arab. Kartu kerja mengikuti bahasa chat, apa pun bahasa yang Claude pakai: di chat berbahasa Inggris kartu dibaca dari kiri ke kanan, di chat berbahasa Persia atau Arab dari kanan ke kiri, dan kalimat Claude di kartu yang ditulis ke arah sebaliknya tetap dibaca ke arahnya sendiri. Jawaban mengikuti teks Claude: saat Claude menjawab dalam bahasa Persia atau Arab di chat yang bahasanya ditulis dari kiri ke kanan, seluruh jawaban menjadi pesan dari kanan ke kiri, dan baris milik aipager sendiri di dalamnya (agen di bawah jawaban, dan pada “Jawaban di dalam kartu kerja” kartu yang sudah selesai di atasnya) ada di kanan seperti bagian lainnya dan tetap dibaca dari kiri ke kanan.
- **Kata mode.** `/mode` menerima kata mode dalam bahasamu (`/mode Tanya`), dan `ask`, `auto`, serta `full` selalu berfungsi. Kalau ada sesi berjalan yang namanya persis kata itu, kata itu justru menyebut sesinya (lihat `/mode` di atas).
- **Deskripsi bot.** Saat mulai, aipager menulis deskripsi bot (teks di layar chat kosong) dan deskripsi singkatnya dalam setiap bahasa ini, untuk orang yang Telegram-nya memakai bahasa itu. Telegram menampilkan teks seperti itu sebagai ganti yang ditulis di BotFather, jadi aipager tidak memasang apa pun selama kamu punya teks sendiri (deskripsi dan deskripsi singkat diperiksa terpisah). aipager tidak pernah menimpa teksmu dan tidak pernah mengubah teks bahasa Inggris atau teks bawaan. Saat teks terbarunya sudah terpasang di semua bahasa, start tidak menanyakan apa pun ke Telegram. Kalau tidak, aipager lebih dulu membaca teksmu di BotFather (satu kali baca untuk deskripsi dan satu kali untuk deskripsi singkat): kalau ada, aipager berhenti di situ, jadi dengan teksmu sendiri setiap start memakan pembacaan itu dan tidak lebih. Kalau teksmu tidak ada, aipager membaca setiap bahasa yang belum berisi teks terbarunya, jadi bahasa yang di BotFather berisi teksmu sendiri dibaca lagi di setiap start. Kalau kamu menulis deskripsi di BotFather setelah aipager memasang miliknya, pembaca dalam bahasa-bahasa itu tetap melihat deskripsi aipager (lihat [pemecahan masalah](troubleshooting.md#the-bot-description-is-not-the-one-i-wrote)).

## <a id="the-mini-app"></a>Mini App

`/app` (dan tombol menu Telegram) membuka dasbor yang disajikan aipager sendiri. Dasbor ini menyala secara bawaan; kelola dengan `aipager miniapp enable|disable|status`. Setiap permintaan diperiksa dengan tanda tangan `initData` dari Telegram (lihat [keamanan → tunnel Mini App](security.md#mini-app-tunnel)). Halamannya tidak memuat apa pun dari luar: tidak ada font, gambar, atau skrip dari situs lain.

- **Chat.** Aplikasi menampilkan satu chat sekaligus. Kalau kamu ada di lebih dari satu chat (misalnya chat pribadimu dan satu grup), baris nama chat di bagian atas berpindah di antaranya; aplikasi terbuka di chat pribadimu sendiri dan mengingat pilihan terakhirmu di ponsel ini. Semua yang di bawah berlaku untuk chat yang dipilih, dengan peranmu di sana. Dengan satu chat, baris itu tidak ditampilkan. Lihat [mode tim](groups.md).
- **Sesi.** Satu kalimat merangkum chat, misalnya “1 menunggu jawabanmu, 2 sedang bekerja, 3 siap”, dan di bawahnya jumlah yang sudah terpakai. Setiap sesi yang berjalan tampil sebagai kotak dengan cincin konteks, foldernya, model, dan biaya; yang sedang bekerja menyala dengan warna aksen. Sesi yang sudah selesai ada di rak **Sudah berakhir** yang bisa dilipat. Tombol **+** memulai sesi baru.
- **Menunggumu.** Sesi yang menunggu permintaan izin atau pertanyaan ada di pita kuning di bagian atas dan menunjukkan apa yang ditanyakannya. Sesi di latar belakang Claude Code yang tugasnya menunggu permintaan izin juga ada di sana, karena kamu bisa menjawabnya dari chat; halamannya juga menawarkan Kembalikan. **Jawab di chat** mengirim ulang permintaan itu dengan tombolnya ke bagian bawah chat, sama seperti tombol **Jawab** di bilah yang disematkan, dan kamu menjawab di sana. Kalau itu satu-satunya sesi yang menunggu, aplikasi menutup supaya permintaannya tepat di depanmu; kalau tidak, aplikasi tetap terbuka dan menulis “Dikirim ke chat”. Ini butuh hak mengirim pesan ke sesi itu, dan salinan yang diketuk setelah permintaannya dijawab atau diganti menulis “sudah dijawab”.
- **Halaman sesi.** Kepalanya menampilkan cincin konteks, keadaan (“Bekerja (3:12)”, “Menunggu jawabanmu (izin)”, “Siap, terakhir aktif 5 menit lalu”, “Di latar belakang Claude Code”, “Sudah berakhir”), dan **Kembalikan** sekali ketuk (untuk sesi yang ada di latar belakang Claude Code), **Hentikan** (selama bekerja atau menunggu) atau **Lanjutkan** (saat sudah selesai). Di bawahnya: permintaan yang menunggu dengan **Jawab di chat**, pemanggilan alat terakhir sebagai strip (ketuk untuk melihat linimasa lengkapnya), jawaban terakhir, detail, pemilih model, preferensi milik sesi itu, file yang berubah, dan linimasa. Menu ⋮ berisi semua tindakan, dengan konfirmasi yang sama seperti sebelumnya. Sesi yang percakapannya pindah ke latar belakang Claude Code (lihat [pemecahan masalah](troubleshooting.md#a-session-moved-to-claude-codes-background)) menawarkan Kembalikan, Hentikan, Ganti nama, Akhiri, dan Mulai ulang; kalau tugas latar belakangnya masih bekerja, Kembalikan, Mulai ulang, dan Lanjutkan bertanya langsung di halaman itu, **Hentikan dan kembalikan** atau **Biarkan saja**, dan chat menampilkan `↩️ x1 sudah kembali dari latar belakang Claude Code.` setelah sesinya kembali.
- **Sesi baru.** Kartu langkah demi langkah: nama, lalu lokasi (dengan saran **Baru-baru ini** untuk folder yang dipakai sesi-sesimu, yang terbaru dulu), lalu model (dengan saran **Saran**), lalu **Opsi lainnya** untuk mode izin dan gaya jawaban. Tombol bawah milik Telegram sendiri bernama **Mulai sesi**. Untuk anggota yang perannya tanpa `can_manage`, “lokasi” hanya menawarkan [folder utama](groups.md#root-folders) chat itu dan folder tepat di dalamnya (folder utama sebagai bawaan, dan “Folder baru” membuat di dalam yang dipilih); folder di luarnya ditolak dengan alasannya, dan “Lanjutkan”, “Mulai ulang”, serta pergantian mode untuk sesi yang berjalan di luarnya menjawab “Sesi ini bekerja di luar foldermu ~/..., jadi peranmu tidak boleh melanjutkan, memulai ulang, atau mengganti mode-nya. Minta bantuan admin.”
- **Pengaturan.** Pertama **Bahasa aplikasi**: bahasamu sendiri untuk teks aipager di aplikasi dan di chat pribadimu (Inggris, Spanyol, Rusia, Portugis (Brasil), Tionghoa (Sederhana), Prancis, Jerman, Korea, Arab, Indonesia, Ukraina, Italia, Turki, Persia, atau Vietnam; “Otomatis” mengikuti bahasa aplikasi Telegram-mu, lihat [Bahasa](#languages)). Setiap orang bisa mengubah miliknya sendiri; pilihan itu tidak pernah diterbitkan di chat dan tidak mengubah sesi Claude-mu. Lalu pengaturan chat (di grup hanya admin yang bisa mengubahnya; di chat pribadimu sendiri kamu bisa, apa pun peranmu, sama seperti `/settings` di sana) dan, untuk admin, **Pembaruan**, yang mencerminkan [`/update`](#update). Untuk pemilik di chat pribadinya, **Laporkan masalah** membuka halamannya sendiri: tulis catatan, periksa laporannya, kirim, dan lihat kode referensinya tanpa keluar dari aplikasi (lihat [laporan masalah](problem-reports.md#how-to-send-one)).

Aplikasi mengikuti tema terang atau gelap Telegram saat kamu menggantinya, dan menghentikan animasi halusnya saat ponsel meminta gerakan yang lebih sedikit. Semua yang ada di Mini App juga bisa dijangkau dari chat: menu ⋮ di panel sesi berisi tindakan yang sama.

### <a id="switching-a-running-sessions-model"></a>Mengganti model sesi yang sedang berjalan

Halaman sesi yang sedang berjalan punya pemilih **Model**, yang menampilkan model yang dilaporkan sesi itu (dari baris status Claude Code) dan daftar yang sama dengan yang ditawarkan pemilih saat peluncuran dan keyboard model di chat. Memilih salah satunya mengetik persis `/model <name>` ke sesi itu, input yang sama dengan yang dipakai keyboard model di chat, dengan aturan yang sama tentang siapa yang boleh (siapa pun yang bisa mengirim pesan ke sesi itu). Pemilih menampilkan **mengganti…** sampai baris status melaporkan model lain, lalu menampilkannya; kalau tidak ada yang berubah dalam 15 detik, pemilih menampilkan **belum dikonfirmasi (periksa sesinya)**.

- **Hanya saat sesi siap, bukan saat Claude bekerja atau ada permintaan terbuka.** Claude Code menjalankan `/model` langsung, bahkan di tengah tugas, bukan setelahnya, jadi pergantian ditolak sampai tugasnya selesai. Keyboard di chat menolak dalam keadaan yang sama dengan kata-kata yang sama.
- **Pertanyaan “Switch model?” dari Claude Code.** Sebelum mengganti, Claude Code biasanya mengajukan pertanyaan ini, karena jawaban berikutnya harus membaca ulang seluruh percakapan. Dengan Claude Code 2.1.251 atau yang lebih baru, hook `PreModelSwitch` milik aipager menyetujui pergantian yang baru saja diketik aipager, jadi pertanyaannya tidak muncul (lihat [hooks](hooks.md#premodelswitch)). Pergantian lain tetap bertanya. Dengan Claude Code yang lebih lama, atau saat pindah ke atau dari `opusplan`, pertanyaan itu tetap bisa muncul. Pergantian lalu ditampilkan sebagai *belum dikonfirmasi*, dan pertanyaannya menunggu di terminal. Jawab di sana sebelum mengirim apa pun lagi ke sesi itu, kalau tidak pesan berikutnya akan diketik ke pertanyaan itu. Dengan alasan yang sama, aipager menolak satu pergantian lagi untuk sesi itu dalam satu menit setelah pergantian yang belum dikonfirmasi, atau sampai sesi melaporkan model baru.
- **Pesan yang dikirim tepat setelah ganti model akan menunggu.** Claude Code lebih dulu mengecek model dengan satu permintaan (sampai 15 detik) dan kehilangan apa pun yang diketik selama itu, jadi pesan yang kamu kirim saat itu ditahan (👀) dan dikirim ke Claude begitu sesi melaporkan model baru, atau setelah 17 detik.
- **Model itu juga jadi bawaan Claude Code untuk sesi baru.** Sama seperti `/model` di terminal, Claude Code menyimpan model yang kamu pilih di `settings.json`-nya, dan setiap sesi baru tanpa model sendiri dimulai dengan model itu. Balasan di chat, kartu sesi baru, dan Mini App memberi tahu kalau ini terjadi. Sesi yang dimulai dengan model sendiri (di langkah model `/new` atau dari bawaan `/settings`) tetap memakainya. Di grup, anggota yang perannya tidak bisa mengelola chat (`user`, `read_only`) hanya mengganti model sesinya sendiri: aipager mengembalikan model bawaan Claude Code seperti semula.

## <a id="the-pinned-status-bar"></a>Bilah status yang disematkan

Setiap chat tempat aipager berbicara (chat pribadimu dan setiap grup) punya satu pesan yang disematkan: bilah status. Strip Telegram di atas chat menampilkan pesan itu dari **baris pertamanya**, dengan baris-baris disambung, jadi baris itu menyebut hal yang paling membutuhkanmu:

| Baris pertama | Kapan |
|---|---|
| `⏳ jim menunggu jawabanmu - Bash: make deploy` | ada sesi yang menunggu permintaan izin, pertanyaan, atau permintaan interaktif; `(+2 lainnya)` saat yang lain juga menunggu |
| `⚙️ jim (sedang bekerja)` / `💤 jim (siap)` | chat ini punya satu sesi yang berjalan, dan sesi itu sedang bekerja / siap (`🔄 jim (sedang dimulai)` saat dimulai): baris itulah seluruh bilah status, selain baris tentang batas |
| `⚙️ 2 sedang bekerja · 1 siap` | beberapa sesi, tidak ada yang menunggu: berapa di setiap keadaan (`sedang bekerja`, `siap`, `sedang dimulai`); `🔄` saat tidak ada yang bekerja tapi satu sedang dimulai, `💤` saat semuanya siap |
| `💤 semua siap` | tidak ada sesi yang berjalan |

Di bawahnya, hanya selama berlaku, baris tentang batas (`🐢 mengirim lebih pelan setelah peringatan Telegram` (enam jam setelah 429) atau `⏸ kartu sementara tidak diperbarui (batas per jam Telegram)` / `(batas Telegram)` (mode minimal, lihat [pemecahan masalah](troubleshooting.md#the-hourly-budget))), lalu, saat chat ini punya lebih dari satu sesi yang berjalan, `✍️ Pesan masuk ke jim` (sesi tempat pesan biasa di chat ini masuk, lihat di bawah; tidak di grup, tempat setiap orang punya miliknya sendiri) dan satu baris per sesi dengan keadaannya: `sedang bekerja`, `menunggu jawabanmu`, `siap`, atau `sedang dimulai`. Bilah status menyebut setiap sesi sekali: sesi yang menunggu dan disebut baris pertama tidak mendapat barisnya sendiri, dan baris pertama tidak pernah mencantumkan nama yang diulang baris-baris di bawahnya. Sesi yang masih punya agen yang berjalan di latar belakang menyebut berapa jumlahnya di barisnya (atau di satu-satunya baris kalau hanya ada satu sesi): `💤 jim (siap, 1 agen sedang bekerja)`. Perintah yang dijalankan Claude di latar belakang dihitung dengan cara yang sama: `(siap, 1 perintah latar belakang berjalan)`, atau `(siap, 2 agen dan 1 perintah latar belakang berjalan)` saat keduanya ada. Ini selalu berupa jumlah, bukan nama-namanya, jadi baris itu hanya berubah saat jumlahnya berubah. Ketuk bilah itu untuk melompat ke pesannya.

**Ke mana pesan biasa pergi.** Pesan yang bukan balasan masuk ke sesi yang terakhir diajak bicara chat ini: sesi yang terakhir dipilih chat ini dengan `/<label>`, yang terakhir dikirimi pesan dari sini, yang dimulai di sini, atau yang terakhir menjawabmu di sini. `/mode`, `/clearqueue`, `/diff`, atau `/now` tanpa argumen bekerja pada sesi yang sama itu (`/stop` menghentikan yang sedang bekerja). Setiap chat punya miliknya sendiri: pesan di satu chat tidak pernah masuk ke sesi milik chat lain. Di grup, yang dihitung hanya balasan ke bot, pesan yang menyebutnya, perintah, atau ketukan pada keyboard grup (lihat [mode tim](groups.md#what-the-bot-reads-in-a-group)), dan setiap orang punya tujuannya sendiri yang tidak bergeser oleh jawaban sesi mana pun (lihat [mode tim](groups.md#talking-to-a-session-in-a-group)).

Tombol pesan yang disematkan:

- **Jawab &lt;label&gt;** (satu per sesi yang menunggu, paling banyak tiga) mengirim ulang permintaan sesi itu dengan tombol jawabannya ke bagian bawah chat, supaya bisa dijawab tanpa menggulir. Kalau sudah dijawab sementara itu, yang datang justru notifikasi singkat “sudah dijawab”, dan salinan (atau permintaan aslinya) yang diketuk setelah permintaan itu dijawab di tempat lain ditolak dengan cara yang sama: salinan itu tidak pernah menjawab permintaan yang lebih belakangan. Setelah aipager dimulai ulang, permintaan yang sedang menunggu tetap berfungsi dari kartu kerjanya atau pesannya sendiri, tapi aipager tidak lagi tahu permintaan mana yang ditampilkan salinan yang lebih lama, jadi mengetuk salinan yang dikirim bilah ini sebelum mulai ulang ditolak dengan “permintaan ini sudah tidak terbuka”. Siapa pun yang bisa menjawab permintaan itu boleh memakainya; yang lain tidak.
- **📱 App** membuka Mini App, di chat pribadimu, selama Mini App menyala.

Bilah ini tidak punya jam, biaya, % konteks, atau model, jadi bilah itu hanya berubah saat keadaan sesi berubah. aipager mengeditnya (diam-diam, tanpa notifikasi) hanya saat yang ditampilkannya berubah, paling sering sekali setiap 30 dtk per chat (`PINNED_MIN_EDIT_GAP`); perubahan di dalam jeda itu ditampilkan saat jedanya berakhir dan tidak pernah hilang. Bilah ini tidak pernah diedit selama pengiriman ke chat itu dimatikan karena pemblokiran, dan menyusul setelah blokir dicabut.

Di grup, bot butuh hak admin untuk menyematkan. Kalau tidak diizinkan menyematkan, aipager menghapus pesan status yang baru saja dikirimnya (supaya tidak pernah tertinggal tidak tersemat di riwayat grup) dan tidak menampilkan bilah di grup itu sampai aipager dimulai ulang; sama halnya untuk chat yang sama sekali menolak bot (mengeluarkannya, memblokirnya). Kalau kamu menghapus pesan yang disematkan, aipager mengirim dan menyematkan yang baru, paling sering sekali sejam per chat.

## <a id="persistent-keyboard"></a>Keyboard tetap

Ada keyboard tetap di bawah kolom input chat. Barisnya dari atas ke bawah:

| Baris | Tombol | Catatan |
|---|---|---|
| Sesi | satu tombol per nama sesi yang berjalan | disusun otomatis dari daftar sesi |
| Tindakan | `status`, `stop`, `new` | tombol teks cepat (`kill` tetap berfungsi kalau diketik; mengakhiri sesi ada di `/status` → `⋮`) |
| Navigasi | `Templat ›`, `Perintah ›` | Mini App dibuka dari tombol menu di sebelah kolom ketik atau dengan `/app`, bukan dari keyboard ini |

`Model ›` ada di dalam submenu “Perintah”. Mengetuk item submenu mengirim pesan siap pakai atau perintah slash:

- **Templat**: pesan yang sering kamu ketik berulang kali, misalnya “Tulis tes”, “Jelaskan rencana”, “Perbarui memori”.
- **Perintah**: perintah slash yang dipahami Claude Code sendiri (`/compact`, `/clear`, dan seterusnya) langsung diketik. Perintah atau templat menyebut sesi tujuannya (`🧹 /clear dikirim ke x1`). Menghapus percakapan ditolak selama sesinya bekerja (`⚠️ Percakapan tidak bisa dihapus saat sesi sedang bekerja`).
- **Model**: penggantian model cepat untuk sesi aktif. Ada alias keluarga (`sonnet`, `opus`, `haiku`, `fable`, `opusplan`), yang selalu berarti model terbaru di keluarganya, dan model yang dikunci (`claude-opus-5-5`, `claude-opus-5-5[1m]` dengan jendela konteks 1M, `claude-sonnet-5-5`, `claude-fable-5-1`, `claude-haiku-5-5`). Pemilih saat peluncuran dan halaman sesi di Mini App memakai daftar yang sama. Pergantian ditolak selama sesinya bekerja atau ada permintaan terbuka. Balasan `🔄` kemudian berubah untuk menampilkan model yang dilaporkan sesi itu, atau menyebut bahwa pergantiannya belum dikonfirmasi (lihat [Mengganti model sesi yang sedang berjalan](#switching-a-running-sessions-model)). Model lain bisa diketik namanya di langkah model `/new` atau di Mini App, persis seperti yang ditulis Claude Code atau penyediamu: sampai 128 karakter berupa huruf, angka, dan `. _ - : / @ ~ ,`, dengan `[1m]` boleh di akhir ([penyedia](troubleshooting.md#using-an-api-key-a-gateway-or-another-provider)).

Untuk mengubah tata letak bawaan, tulis `~/.config/aipager/keyboard.json`:

```json
{
  "templates": [{"label": "Deploy",  "prompt": "Deploy to staging"}],
  "commands":  [{"label": "Compact", "send": "/compact"}],
  "models":    [{"label": "Sonnet",  "send": "/model sonnet"}]
}
```

Setiap bagian berdiri sendiri: bagian yang tidak ada diambil dari nilai bawaan, jadi kamu bisa mengubah satu tanpa menyebut yang lain. JSON yang rusak diabaikan dengan peringatan di log. Perubahan butuh mulai ulang aipager.

## <a id="per-message-inline-buttons"></a>Tombol di bawah setiap pesan

Sebagian besar balasan bot punya tombol sesuai situasinya:

### <a id="permission-prompts"></a>Permintaan izin

Saat Claude ingin menjalankan alat yang butuh persetujuan, kartu kerja berubah menjadi permintaan izin:

```
🔐 Bash: List the temp directory
ls -la /tmp

  [✅ Izinkan]  [❌ Tolak]
  [🟢 Selalu izinkan]  [⏹ Hentikan]
```

Kartu itu menampilkan perintah (atau path file) yang sebenarnya ingin dijalankan Claude, di bawah deskripsinya sendiri: setujui yang bisa kamu baca. Karakter yang akan mengubah urutan teks digambar (karakter kontrol arah Unicode seperti U+202E, yang bisa membuat satu perintah terlihat seperti perintah lain) ditampilkan sebagai label seperti `⟨U+202E⟩`, dan kartu lalu menambahkan `⚠️ Ini berisi karakter tersembunyi yang mengubah arah teks (ditampilkan sebagai ⟨U+...⟩). Yang dijalankan mungkin tidak sama dengan yang kamu lihat.`

- **Izinkan**: menyetujui satu pemanggilan ini.
- **Tolak**: menolaknya; Claude tidak menjalankan alat itu.
- **Selalu izinkan**: menyetujui dan menambahkan aturan permanen yang disarankan Claude (“don't ask again for …”). Tombol ini muncul **hanya saat Claude menawarkan aturan seperti itu**; untuk perintah yang tidak bisa dibuatkan aturan (kebanyakan perintah gabungan), kartu justru menampilkan Izinkan / Tolak / Hentikan, begitu juga untuk akses file yang hanya membaca (`Read`, `Grep`, `Glob`) di luar folder kerja sesi. Claude Code 2.1.259 dan yang lebih baru menaruh, di tempat itu, baris “switch to auto mode” di dialog Bash-nya sendiri, baris “block reads outside the working directories from now on” di dialog baca-di-luar-folder, dan, untuk Write/Edit yang tidak bisa dibuatkan aturan file, pergantian mode izin seperti menerima semua edit file otomatis (`acceptEdits`); aipager tidak pernah memilih satu pun; ganti mode dengan sadar lewat `/mode`.
- **Hentikan**: memutus tugas, bukan menjawab.

Jawabannya sampai ke Claude Code lewat hook izin milik aipager, yang menunggunya sampai 20 detik. Permintaan yang datang sebelum kartu kerja (lalu dikirim sebagai pesannya sendiri, hanya dengan Izinkan dan Tolak) dijawab dengan cara yang sama. Saat penantian itu berakhir, tombol-tombolnya justru mengetikkan jawaban ke dialog Claude Code sendiri; Tolak lalu menekan Escape (“No, and tell Claude what to do differently”), yang mengakhiri tugas Claude seperti Hentikan, dan kartunya menulis `🚫 jim · Izin ditolak`. Pada pertanyaan yang diajukan Claude Code dalam Mode Auto sebelum pembacaan pertama di luar proyek, Izinkan yang terlambat tidak diketikkan: Enter-nya akan memilih “Yes, and keep allowing any reads outside the working directories”, jadi ketukannya memintamu menjawab di terminal (Tolak tetap berfungsi).

Setiap ketukan dicatat ke `~/.claude/aipager-audit.jsonl` dan diulang sebagai balasan satu baris di bawah kartu kerja: `✅ jim · Diizinkan · @alice · Bash: List the temp directory` (deskripsi pemanggilan, atau perintahnya kalau tidak ada deskripsi; dengan chat yang diatur lewat `aipager config`; dalam mode pribadi cukup `Diizinkan`).

Selama permintaan (atau AskUserQuestion) menunggu jawabanmu, sesi ditampilkan sebagai menunggu, bukan siap. Kalau selama penantian itu Claude Code mengingatkan bahwa inputnya diam, chat mendapat `⬆️ jim · masih menunggu jawabanmu di atas` sebagai balasan ke permintaan itu, sekali per penantian.

Dialog `AskUserQuestion` ditampilkan dengan cara yang sama, dengan satu tombol per opsi (empat pertama), dan, untuk pertanyaan yang bisa dijawab lebih dari satu, kotak centang ⬜/☑ dan tombol `✅ Kirim`. Pertanyaan dan deskripsi setiap opsi ditampilkan utuh, seperti di terminal. Hanya formulir yang terlalu panjang untuk satu pesan Telegram yang dipendekkan: kartu kerja lebih dulu menampilkan lebih sedikit baris alat, lalu teks terpanjang di antara pertanyaan dan deskripsi diakhiri `…` di batas kata.

**Lebih dari satu permintaan.** Claude Code bisa menunggu dua permintaan sekaligus: agen latar belakang meminta izin sementara Claude mengajukan pertanyaan kepadamu, atau sebaliknya. aipager menampilkan keduanya: yang pertama di kartu kerja (atau sebagai pesannya sendiri), dan setiap yang datang sesudahnya sebagai pesannya sendiri di bawah kartu, dengan tombolnya sendiri. Sesi tampil menunggu sampai permintaan terakhir dijawab, dan pesan yang kamu kirim selama itu ditahan sampai saat itu. Jawab dengan urutan mana pun. Izinkan dan Tolak lewat hook izin selama hook itu masih menunggu, dan hook itu sampai ke permintaan itu sendiri di mana pun letaknya. Ketukan yang harus mengetik tombol keyboard (opsi pertanyaan, atau Izinkan dan Tolak setelah hook berhenti menunggu) hanya mengetiknya ke permintaan yang ditampilkan Claude Code lebih dulu: Claude Code menampilkan satu dialog setiap kali, dan ketikan sampai ke dialog yang paling atas. Kalau tidak, ketukan itu tidak mengetik apa pun, tombolnya tetap ada, dan muncul `Claude Code sedang menampilkan permintaan lain lebih dulu, jadi jawab yang itu dulu sebelum yang ini.` atau, kalau aipager tidak tahu urutannya (misalnya agen yang mulainya tidak terlihat olehnya), `aipager tidak bisa tahu permintaan mana yang ditampilkan Claude Code lebih dulu, jadi jawab yang ini di terminal.` Kalau permintaan di kartu dijawab lebih dulu, kartu menampilkan “Menunggu jawabanmu” hanya dengan Hentikan, dan permintaan lain tetap punya tombolnya. Kalau Claude menyelesaikan tugasnya sementara permintaan dari agen masih terbuka, jawabannya datang seperti biasa dan sesi tetap menunggu permintaan itu, yang dikirim ulang sebagai pesannya sendiri. Mulai ulang saat dua permintaan atau lebih terbuka tidak mengembalikan satu pun: tombolnya menjawab `sudah dijawab` dan tidak mengetik apa pun, dan permintaannya tetap bisa dijawab di terminal. Satu permintaan yang terbuka kembali seperti sebelumnya. Dalam Akses penuh dan Mode Auto, Claude Code menolak sendiri permintaannya untuk `rm` atau `rmdir` yang berisiko kalau tidak ada yang menjawab dalam 2 menit, jadi kartunya menunjukkan kapan (`Kalau tidak ada yang menjawab, Claude Code akan menolak permintaan ini sendiri sekitar pukul 14:05.`), dan setelah itu terjadi, kartunya mengatakannya dan tombolnya hilang. Permintaan seperti itu muncul di atas permintaan apa pun yang sudah terbuka, jadi selama permintaan Bash milik Claude sendiri dalam Akses penuh atau Mode Auto terbuka bersama permintaan lain, ketukan yang harus mengetik tombol keyboard menampilkan pesan di atas bahwa aipager tidak tahu urutannya, dan Izinkan dan Tolak tetap berfungsi selama hook masih menunggu.

Saat konteks mencapai 80%, chat mendapat `⚠️ name · Konteks terisi 82% - sebentar lagi diringkas otomatis` dengan **📦 Ringkas sekarang**, yang mengirim `/compact` ke sesi itu. Peringatan ini datang sekali per percakapan: meringkas tidak memunculkannya lagi, dan percakapan baru (misalnya setelah `/clear`) mendapatkannya lagi.

### <a id="stale-buttons"></a>Tombol lama

Tombol yang bekerja pada tugas yang sedang berjalan (Hentikan, Akhiri, Mulai ulang, pergantian `/mode` dan “Hentikan & alihkan”-nya, “Ganti” di `/new`) terikat pada tugas tempat tombol itu ditampilkan. Tombol yang tersisa dari tugas sebelumnya tidak mengubah apa pun, alih-alih bekerja pada yang sedang berjalan sekarang, begitu juga tombol yang ditampilkan sebelum sesi berakhir lalu dilanjutkan atau dimulai ulang. Akhiri, Mulai ulang, pergantian `/mode`, dan Hentikan di `/status` atau di daftar `/stop` menjawab `x1 sudah mengerjakan hal baru - …` dengan apa yang perlu diketuk atau dikirim lagi (atau `Sesi itu sudah berakhir.`); Hentikan di kartu kerja menjawab `Tugas itu sudah selesai - …`, dan “Hentikan & alihkan” di `/mode` mengubah kartunya kembali menjadi kartu `/mode` di bawah `⚠️ x1 sudah mulai tugas baru, jadi tidak ada yang berubah.` “Ganti” di `/new` menulis `Sementara itu x1 sudah mulai bekerja. Kirim /new lagi untuk menggantinya.` saat sesi itu sudah mulai tugas setelah kartunya ditampilkan.

Tombol setiap permintaan terikat pada permintaan itu. Setelah dijawab, di Telegram atau di terminal, tombolnya menjawab `sudah dijawab` dan tidak mengetik apa pun, juga selama permintaan lain dari sesi itu masih terbuka.

Tombol `⚡ Kirim sekarang` hanya bekerja pada pesan tempat tombol itu berada: kalau Claude sudah mengambil pesan itu, ketukan menjawab `Claude sudah mengambilnya`, tidak mengirim apa pun, dan menghapus baris antreannya.

### <a id="idle-responses"></a>Jawaban

Saat tugas selesai, jawabannya datang sesuai tata letak pesanmu (lihat di bawah dan [Pengaturan](#settings)). Kalau jawaban Claude lebih panjang dari yang muat di satu pesan (sekitar 32 KB), pesan itu menampilkan sebanyak yang muat di bawah catatan `📎 Jawaban lengkap ada di file di bawah ↓`, dan seluruh tugas (langkah-langkahnya dan jawaban lengkap) datang sebagai file `<name>_full_log.md`, yang ditampilkan Telegram tanpa aplikasi lain. File yang sama dilampirkan saat kartu kerja harus membuang baris.

Tugas yang berakhir dengan error API Anthropic (kredit, kelebihan beban, batas permintaan, jaringan) menampilkan `⚠️ name · <what went wrong>` sebagai ganti jawaban, dengan **🔄 Coba lagi**, yang mengirim lagi pesan terakhir ke sesi yang sama. Selama pengiriman ke chat [dimatikan karena pemblokiran](troubleshooting.md#the-bot-went-quiet-flood-control), mengetuknya sama sekali tidak melakukan apa pun (pesannya tidak dikirim lagi, tombolnya tetap ada), jadi ketuk lagi setelah blokir dicabut.

Dalam tata letak kartu (“Kartu kerja + jawaban terpisah”), kartu yang selesai tetap di atas jawaban sebagai catatan bagaimana jawaban itu didapat: baris alatnya, baris agennya, dan apa yang dikatakan Claude di antaranya. Jawabannya datang sebagai pesan terpisah (dengan notifikasi). Tugas yang tidak memanggil alat apa pun (tanpa alat, agen, perintah latar belakang, ataupun pertanyaan atau permintaan izin) hanya akan menyisakan `✅ name · Selesai · Ns`, jadi kartunya dihapus selagi masih bertuliskan “Bekerja”, tidak pernah diubah menjadi “Selesai”, dan baris pertama jawabannya menampilkan waktunya (`💬 name · Selesai (…)`). Mengirimimu file tidak dihitung sebagai memanggil alat di sini: file-filenya menyusul setelah jawaban. Tugas tanpa jawaban untuk ditampilkan, atau yang berakhir dengan error API, tetap menyimpan kartunya. Untuk satu pesan per tugas, pilih “Jawaban di dalam kartu kerja” (jawaban masuk ke kartu) atau “Hanya jawaban, kartu dihapus” (kartu dihapus dan jawaban berdiri sendiri).

Setiap tugas punya tepat satu kartu kerja. Pesan yang kamu kirim selama tugas berjalan tidak pernah menambahkan kartu kedua ke tugas itu, dan kartu yang tugasnya sudah selesai tidak lagi memegang tombol Hentikan: kalau tidak ada yang menutupnya (hook Stop yang hilang, mulai ulang), aipager menutupnya setelah 30 detik sesuai tata letakmu. Setelah aipager dimulai ulang, kartu yang tugasnya masih berjalan dipertahankan dan langsung kembali berdetak, menghitung dari awal tugas, dan sesinya ditampilkan sedang bekerja sampai tugas selesai; kartu lain yang tersisa dari sebelum mulai ulang ditutup (“aipager dimulai ulang”, tanpa Hentikan). Sesi yang tadinya menunggu permintaan izin atau satu pertanyaan dari Claude kembali sebagai menunggu, bukan bekerja: kartunya dan bilah yang disematkan mengatakan sesi itu menunggu jawabanmu, dan tombol permintaan itu tetap menjawabnya, kecuali riwayat percakapan menunjukkan permintaan itu sudah dijawab di terminal sementara itu. Pertanyaan bertahap atau multi-pilihan dan permintaan dari subagen tetap diserahkan ke terminal. Begitu permintaan diketahui selesai (dijawab lewat ketukan atau di terminal, tugasnya berakhir, atau mulai ulang tidak membawanya kembali karena sudah dijawab sementara itu atau tidak bisa disimpan dan dibaca lagi), tombol jawabannya yang lama menjawab “sudah dijawab” dan tidak mengetik apa pun, bahkan setelah mulai ulang. Permintaan yang tidak dijawab siapa pun tetap terbuka selama Claude menunggunya. aipager berhenti menunggunya hanya kalau Claude tidak memberi kabar selama 5 menit dan percakapan yang tersimpan menunjukkan permintaan itu sudah lewat (kabar tentang jawabannya hilang); permintaan seperti itu tidak dianggap selesai, jadi tombolnya tetap menjawab di dialog. Kabar yang dihitung hanya kabar dari Claude sendiri dan dari agen yang bertanya: agen latar belakang yang bekerja sendiri tidak dihitung, dan percakapan yang tersimpan tidak mengatakan apa pun tentang permintaan dari agen, yang menunggu kabar dari agen itu sendiri.

Tugas yang dimulai Claude **sendiri** (agen latar belakang melapor lewat `<task-notification>` saat tidak ada pekerjaan yang terbuka) baru mendapat kartu kerja saat melakukan sesuatu: pada pemanggilan alat pertamanya, atau setelah 15 dtk, mana yang lebih dulu. Sebagian besar tugas seperti itu hanya berisi beberapa detik “tidak ada yang baru”; sekarang tugas seperti itu hanya menampilkan jawabannya. Indikator “sedang mengetik…” tetap terlihat selama tugas itu berjalan, dan jawabannya sendiri selalu dikirim: tidak ada yang disaring karena dianggap tidak penting. Tugas yang kamu mulai, dari Telegram atau dari terminal, tetap langsung mendapat kartu.

Selama sesi bekerja, setiap agen latar belakang yang dimulai Claude (lewat alat `Agent` di Claude Code, `Task` di versi lama) mendapat barisnya sendiri di kartu kerja: `🤖 <type> · <activity> · <elapsed>`, dengan jenis agen dan apa yang sedang dikerjakannya, dan baris itu diperbarui seiring pemanggilan alatnya sendiri. Saat agen itu sudah melakukan tiga pemanggilan alat atau lebih, pemanggilan-pemanggilan itu dilipat ke tombol `▸ N alat dipakai` miliknya sendiri, tepat di bawah barisnya; pemanggilan itu tidak pernah muncul di linimasa tugas utama atau di hitungan `Bash ×N`-nya. Saat agen itu selesai, barisnya membeku menjadi `✅ 🤖 <type> · N alat · <elapsed>` dan mempertahankan tombol yang sama. Agen yang dimulai oleh agen lain (pembantu) menyebut agen di puncak rantainya sebelum jenisnya sendiri, `🤖 pipeline-runner › ship-tester`, kalau aipager tahu siapa yang memulainya pada saat agen itu mulai; baris-barisnya tetap dalam urutan agen-agen itu dimulai. File `.md` di atas mendapat bagian “Agen” berisi setiap agen yang bekerja di tugas itu, waktunya, jumlah alatnya, dan alat yang dipakainya.

Selama agen latar belakang masih berjalan setelah tugasnya selesai, kartu tetap menjadi status pekerjaan. Saat Claude mengambil pesan baru sementara itu, kartu yang hidup adalah kartu di bawah pesan itu, dan kartu itu tetap menampilkan agen yang sedang berjalan. Pesan yang kamu kirim ke sesi yang siap memulai tugas baru: kartu tugas sebelumnya membeku di tempatnya dengan langkah-langkahnya tersimpan, dan tugas baru mendapat kartunya sendiri. Pesan yang tadinya mengantre lalu diambil melanjutkan tugas pekerjaan itu: kartunya dikirim ulang di bawah pesan itu dan salinan lamanya dihapus. Pemanggilan alat di dalam agen tidak pernah ditampilkan sebagai baris tugas utama sendiri, dan tidak pernah membuat aipager mengira tugas baru sudah dimulai.

Perintah yang dijalankan Claude di latar belakang (`run_in_background`, Ctrl+B, atau yang dipindahkan Claude Code ke sana saat berjalan terlalu lama) mendapat barisnya sendiri selama berjalan: `⏳ perintah latar belakang: <description> (3:12)`, dinamai dari deskripsi pemanggilan, atau kalau tidak ada, dari baris pertama perintahnya. Saat selesai, baris itu membeku menjadi `✅ perintah latar belakang: <description> - selesai (6:12)`, `❌ ... - selesai dengan error (kode keluar 1)`, atau `⏹ ... - dihentikan (6:12)` (Claude menghentikannya dengan TaskStop, atau Claude Code menghentikannya karena kekurangan memori); baris yang gagal dan dihentikan mempertahankan tanda itu di kartu yang selesai. Tugas yang berakhir selagi perintah seperti itu masih berjalan belum selesai: kartunya menunggu sebagai status pekerjaan (`🔄 name · 1 perintah latar belakang masih bekerja · 4:10`, atau `1 agen (ship-reviewer) dan 1 perintah latar belakang masih bekerja` kalau ada agen juga), dan saat perintahnya selesai, kelanjutan dari Claude datang di kartu yang sama. Perintah yang dijalankan di dalam agen milik agen itu dan tidak ditampilkan. Perintah yang akhirnya tidak pernah terlihat aipager (mulai ulang, event yang hilang) berhenti dihitung setelah dua jam (`AIPAGER_BG_SHELL_MAX_TRACK`, dalam detik): barisnya menulis `⏹ ... - akhirnya tidak terlihat`, dan pekerjaan yang hanya menunggu perintah itu ditutup dengan `⚠️ name · Selesai (2:00:04, tidak ada kabar dari perintah latar belakang)`.

Saat linimasa tugas memanjang, setiap rangkaian pemanggilan alat yang lebih awal (tiga atau lebih berturut-turut, dan bukan yang sedang berjalan) dilipat ke tombol `▸ N alat dipakai` miliknya sendiri, tepat di tempatnya, alih-alih menumpuk utuh atau dipotong batas panjang pesan Telegram sendiri; ketuk salah satunya untuk membacanya di tempat. Komentar tidak pernah dilipat; aktivitas terbaru dan baris status selalu terlihat tanpa mengetuk. Baris milik agen latar belakang yang masih berjalan (atau baru saja membeku) tidak pernah dilipat sendiri, hanya pemanggilan alatnya, dan tidak pernah selama baris itu satu-satunya yang berdiri antara linimasa dan batasnya. Hanya kalau linimasanya begitu besar sehingga tidak muat bahkan dengan semuanya terlipat, isinya benar-benar dikeluarkan dari kartu; catatan lengkapnya lalu ada di file `.md` di atas.

#### <a id="agents-still-running-when-the-answer-goes-out"></a>Agen yang masih berjalan saat jawaban dikirim

Claude Code bisa menjalankan agen di latar belakang, dan tugas yang memulainya bisa selesai selagi agen itu masih bekerja. Jawabannya lalu diakhiri garis pemisah dan status singkat dari pekerjaan latar belakang itu, dengan gaya panel agen milik Claude Code sendiri, dan status itu diperbarui selama pekerjaannya berjalan. Semua yang ada di bawah garis itu berupa teks kecil (footer Telegram), supaya jawabannya sendiri menonjol:

```
⏳ 5 agen bekerja · 🖥 1 perintah latar belakang (shell) berjalan

○ 🤖 pipeline-runner · Writing final.md for the pipeline

- 🤖 ship-tester · Write and run criteria tests
- 🤖 ship-developer · Editing state.py
- 🤖 ship-developer › ship-reviewer · Collect line numbers

‖ 🤖 r2-runner · menunggu agennya sendiri

Saat agen selesai, hasilnya dikirim ke sini.
```

- **Baris pertama menghitung.** Agen yang bekerja (atau sudah selesai), dan perintah latar belakang pekerjaan itu yang berjalan, selesai, atau dihentikan. Perintah latar belakang selalu hanya berupa jumlah, tidak pernah perintahnya.
- **Satu baris per agen,** berisi 🤖 dan jenisnya dalam huruf tebal, lalu apa yang sedang dikerjakannya: baris teks terakhirnya sendiri (kata-kata Claude, sebagaimana ditulis, tidak pernah diterjemahkan), kalau tidak ada, alat terakhirnya sebagai frasa pendek yang menyebut apa yang dikerjakannya (`Reading state.py`, `Writing final.md`, `Searching for …`, atau deskripsi perintah itu sendiri), kalau tidak ada juga, tugas yang diberikan Claude ke agen itu saat memulainya, kalau tidak ada juga, alat terakhirnya saja (`Running a command`, `Using TodoWrite`), kalau tidak ada juga, sekadar jenisnya. Jadi dua agen berjenis sama yang menjalankan perintah tanpa deskripsi tetap menampilkan tugasnya masing-masing. Agen yang berhenti untuk menunggu agen yang dimulainya sendiri justru menulis `‖ … menunggu agennya sendiri`.
- **Agen sebagai pohon.** Agen yang dimulai sesi adalah tingkat pertama. Setiap agen yang dimulai salah satu dari mereka, sedalam apa pun, dicantumkan tepat di bawahnya, masing-masing tepat setelah agen yang memulainya; agen yang dimulai oleh agen tingkat kedua menyebut agen itu lebih dulu (`ship-developer › ship-reviewer`). Kartu kerja malah menyebut agen yang sama setelah agen di puncak (`pipeline-runner › ship-reviewer`). Tidak pernah ada lebih dari dua tingkat. Agen yang sedang bekerja di tingkat kedua tidak punya ○ sendiri: Telegram menggambar butir di sana. Agen yang pemulainya belum diketahui aipager ditampilkan di tingkat pertama, tidak pernah di bawah tebakan, dan pindah ke bawah pemulainya begitu itu diketahui.
- **Paling banyak enam baris,** agen yang masih bekerja lebih dulu, lalu `+N agen lainnya` untuk agen tanpa baris. Agen tidak pernah tampil tanpa agen di atasnya.
- **Janji** “Saat agen selesai, hasilnya dikirim ke sini.” tetap ada hanya selama masih ada agen yang bekerja. Perintah latar belakang tidak pernah mendapatnya: server pengembangan bisa saja tidak pernah selesai.

Baris setiap agen membeku sendiri saat agen itu berhenti, menjadi `✓ 🤖 Explore · selesai (0:42)` (berapa lama agen itu bekerja), dan baris pertama mengurangi hitungannya. Agen yang dimulai oleh agen di daftar setelah jawaban terkirim ikut masuk status di bawahnya pada pembaruan berikutnya; agen yang dimulai sesi itu sendiri belakangan dicantumkan di bawah jawaban berikutnya. Saat semua agen sudah selesai, baris-baris itu diganti satu baris berisi waktu dari jawaban sampai agen terakhir selesai:

```
✅ 2 agen selesai (6:12) · 🖥 1 perintah latar belakang (shell) berjalan
```

dan saat perintah itu selesai, `✅ 2 agen selesai (6:12) · 1 perintah latar belakang (shell) selesai (2:13:05)` (berapa lama perintah itu berjalan; `dihentikan` kalau dimatikan atau Claude menghentikannya, `selesai` kalau berakhir sendiri, dengan error atau tidak; barisnya di kartu menyebut bagaimana). Jawaban yang pekerjaannya hanya punya perintah latar belakang menampilkan `🖥 1 perintah latar belakang (shell) berjalan`, yang membeku menjadi `1 perintah latar belakang (shell) selesai (…)` atau `1 perintah latar belakang (shell) dihentikan (…)`.

Hanya perintah latar belakang dari pekerjaan jawaban itu sendiri yang dihitung: server pengembangan yang dibiarkan berjalan oleh tugas sebelumnya tidak diulang di bawah setiap jawaban (bilah yang disematkan tetap menghitung semua perintah). `/stop` mengakhiri pekerjaan itu, tapi Escape tidak mengakhiri perintah latar belakang: perintah yang masih berjalan tetap ada di bilah yang disematkan sampai selesai dan tidak lagi dihitung di bawah jawaban-jawaban berikutnya; kelanjutannya yang belakangan lalu memulai tugasnya sendiri.

Jawaban ini dikirim begitu tugas selesai, sebagai pesan biasa (dengan notifikasi) yang membalas pesanmu: teks yang sama dengan yang ditampilkan terminal. Kartu kerja tetap di atasnya sebagai status pekerjaan yang hidup, dengan tata letak apa pun: `🔄 name · 1 agen (general-purpose) masih bekerja · 1:18`, dengan tombol **Hentikan**-nya sendiri. Saat para agen melapor, jawaban Claude atas laporan itu datang sebagai pesan terpisah; jawaban sebelumnya tidak pernah dikirim lagi, bahkan setelah aipager dimulai ulang. Pekerjaan yang agennya melapor lebih dari sekali memberi satu jawaban per laporan, masing-masing dikirim saat ditulis.

Agen yang berhenti selagi pekerjaan latar belakangnya sendiri masih berjalan dihitung sedang bekerja, karena agen itu akan melanjutkan nanti. Agen yang memulai agennya sendiri lalu berhenti untuk menunggunya dikenali dari mulainya itu (pemanggilannya sendiri yang memulai agen lain itu): agen itu tetap ada di status sebagai sedang bekerja, dan menjaga pekerjaan tetap terbuka, sampai berhenti saat tidak ada lagi agennya sendiri yang berjalan. Kalau agen itu belum melanjutkan dua menit setelah yang terakhir dari mereka selesai, agen itu tidak lagi dihitung berjalan dan status dibiarkan seperti yang terakhir ditampilkan. `<task-notification>` milik Claude untuk agen mengatakan hal yang sama dalam catatan; aipager membacanya saat notifikasi itu memulai tugas, dan juga saat Claude mengambilnya di tengah tugas yang sedang berjalan selagi kartu kerja masih ada. Barisnya menunggu beberapa detik sebelum ditampilkan selesai, supaya catatan itu sempat mengembalikannya. Status ditambahkan dengan tata letak apa pun, dalam bahasa chat.

**Cara memperbaruinya.** aipager mengedit jawaban itu diam-diam, hanya saat yang ditampilkan status berubah, dan paling sering sekali setiap 30 detik per jawaban (jawabannya sendiri dihitung sebagai yang pertama). Setiap jawaban yang punya status diperbarui dengan cara ini (sampai lima yang menunggu per sesi). Edit ini berprioritas rendah: edit yang ditolak anggaran pengiriman, atau yang akan jatuh di saat pengiriman ke chat dimatikan, tidak dilakukan, dan status menunggu perubahan berikutnya. Edit terakhir, saat semuanya selesai, dicoba lagi setelah pengiriman yang dimatikan dicabut atau anggaran menolak, lalu ditinggalkan. Tidak ada edit pada jawaban yang dihapus, untuk sesi yang sudah berakhir atau dimatikan, atau saat edit itu akan membuat jawaban terlalu panjang untuk Telegram (saat jawaban panjang dipotong, ruang untuk status disisakan).

Status tetap seperti yang terakhir ditampilkan dan tidak pernah berubah menjadi “selesai” saat aipager tidak bisa tahu pekerjaannya sudah berakhir:

- agen yang tidak terdengar kabarnya oleh aipager selama 30 menit (`AIPAGER_SUBAGENT_SILENCE`) tidak lagi dihitung bekerja, tapi diam bukan berarti selesai;
- perintah latar belakang yang berjalan lebih dari dua jam (`AIPAGER_BG_SHELL_MAX_TRACK`) dan akhirnya tidak terlihat tidak lagi dihitung;
- mulai ulang aipager melupakan jawaban mana yang sedang menunggu.

Mulai ulang aipager menyimpan agen yang masih berjalan, termasuk yang menunggu agennya sendiri: jawaban yang dikirim setelah mulai ulang mencantumkannya, kartu kerja yang diambil alih oleh mulai ulang menampilkannya lagi, dan akhirnya nanti dikenali seperti biasa. Agen yang disimpan lebih dari 30 menit sebelum mulai ulang tidak dikembalikan.

Jawaban yang ditahan karena pengiriman dimatikan, atau dikirim sebagai teks biasa setelah pengiriman berformat gagal, dikirim tanpa status, begitu juga tugas yang berakhir tanpa teks jawaban (hanya judul).

### <a id="end-confirmation"></a>Konfirmasi mengakhiri

`/kill`, daftarnya, dan `⋮` → `⏹ Akhiri sesi` selalu meminta konfirmasi:

```
⏹ Akhiri jim? Claude berhenti bekerja dan sesinya ditutup.

  [⏹ Akhiri]  [Batal]
```

Setelah `⏹ Akhiri`, pesannya menulis `⏹ jim diakhiri` di atas sesi-sesi chat itu yang tersisa. Konfirmasi yang diketuk setelah sesinya mulai tugas baru ditolak (`jim sudah mengerjakan hal baru - …`), begitu juga konfirmasi untuk sesi dengan nama yang sama yang dimulai sejak itu.

### <a id="voice-install-when-extra-isnt-installed"></a>Pemasangan pesan suara (saat paketnya belum ada)

Saat kamu mengirim pesan suara dan `aipager[voice]` belum terpasang:

```
⚠️ Pesan suara butuh paket tambahan opsional
   (sekitar 200 MB untuk dipasang · sekitar 74 MB model saat pertama dipakai).

  [📦 Pasang fitur pesan suara]  [Batal]
```

Mengetuk Pasang menjalankan perintah pemasangan yang tepat untuk installer-mu (`uv tool install --reinstall aipager[voice]`, `pipx install --force`, atau, sebagai cadangan, `pip install faster-whisper`) dengan pesan yang diedit setiap 5 dtk, dan saat berhasil menawarkan tombol `[🔄 Mulai ulang aipager]`.

### <a id="restart"></a>Mulai ulang

`🔄 Mulai ulang aipager`:

- aipager yang berjalan sebagai layanan pengguna systemd menjadwalkan `systemctl --user restart aipager.service` yang terlepas 5 dtk kemudian (dengan timer yang akurat sampai 1 dtk), di layanan sementara di luar grup proses aipager sendiri, supaya tetap hidup saat aipager keluar. aipager menolak selama layanan itu akan mengakhiri sesi-sesimu (`KillMode` bukan `process` atau `none`), dan memintamu menjalankan `aipager service install` dulu.
- macOS, saat agen launchd sudah terpasang (`~/Library/LaunchAgents/com.aipager.daemon.plist`): `launchctl kickstart -k gui/<uid>/com.aipager.daemon`, bahkan untuk aipager yang kamu jalankan sendiri.
- Kalau tidak, aipager yang kamu jalankan sendiri (`aipager start`), di Linux bahkan saat layanannya juga terpasang: aipager memulai pengganti yang terlepas, yang menunggu proses induknya mati lalu menjalankan perintah yang sama dengan yang dipakai untuk menjalankan aipager (keluarannya masuk ke log mulai ulang sendiri). aipager saat ini berhenti dengan rapi begitu pengganti itu hidup.

Tidak perlu SSH. Untuk menyelesaikan pembaruan aipager, **🔄 Mulai ulang sekarang** di kartu pembaruan justru memulai ulang aipager di tempat, di terminal yang sama (lihat [Pembaruan](#update)).

## <a id="update"></a>Pembaruan

`/update` (khusus admin; dalam mode pribadi hanya orang yang memasang aipager) menjawab dengan satu tombol, **🔄 Cek pembaruan**. Pengecekan baru dijalankan saat tombol itu diketuk. Mengetuknya mengubah pesan itu menjadi satu baris per produk:

- `aipager 1.0.1 → 1.1.0` saat ada versi yang lebih baru (yang terbaru di PyPI);
- `aipager 1.0.1 (terbaru)` saat tidak ada;
- `Claude Code 2.1.282 (tidak bisa dicek)` saat pengecekan gagal (jaringan mati, batas 5 dtk, atau `claude` tidak ditemukan).

Claude Code dibandingkan dengan versi terbaru di kanal pembaruannya sendiri (`autoUpdatesChannel`: latest, stable, atau rc). Di bawah baris-baris itu tertulis kecil cara aipager dipasang (misalnya `pipx, from PyPI` atau `pipx, from local path …`; chat grup tidak pernah menampilkan path).

Kalau ada yang lebih baru, ada SATU tombol yang hanya memperbarui produk yang punya pembaruan: **Perbarui aipager**, **Perbarui Claude Code**, atau **Perbarui keduanya**, ditambah **Batal**. Saat aipager ditawarkan, pesannya juga menyebut bagaimana mulai ulangnya akan terjadi: `Mulai ulang: otomatis, saat tidak ada tugas yang berjalan.`, `Mulai ulang: ketuk “Mulai ulang sekarang” setelah terpasang.` (aipager tanpa layanan, lihat di bawah), atau `Mulai ulang: manual (alasan)`.

Kalau aipager yang lebih baru sudah terpasang tetapi aipager yang berjalan masih yang lama (pembaruan yang selesai tanpa mulai ulang, atau `pip install -U` yang dijalankan manual), barisnya menulis `aipager 1.1.0 sudah terpasang dan menunggu dimulai ulang (yang berjalan: 1.0.1).` dan pesannya menawarkan **🔄 Mulai ulang sekarang** alih-alih pembaruan: Perbarui aipager tidak pernah ditawarkan untuk versi yang hanya perlu dimulai ulang. Versi yang terpasang diperiksa dengan menjalankan interpreter yang akan dipakai aipager saat dimulai ulang, jadi versi yang terpasang tetapi tidak bisa dimuat mengatakannya (`aipager 1.1.0 sudah terpasang tetapi tidak bisa dimuat. Pasang ulang dengan …`) dan tidak menawarkan apa-apa. Rilis yang lebih baru dari yang terpasang tetap ditawarkan sebagai pembaruan. aipager yang lebih baru di pemasangan yang tidak bisa diperbarui dari sini (editable, Nix, Snap, paket sistem, kontainer, atau pemasangan milik pengguna lain) ditampilkan dengan alasannya dan tidak ditawarkan. Begitu juga Claude Code yang lebih baru yang dipasang dengan npm ke folder yang tidak bisa ditulisi penggunamu (misalnya `sudo npm install -g` di bawah `/usr/lib/node_modules`), tempat `claude update` hanya bisa gagal: barisnya menulis `tidak bisa diperbarui dari sini: installed with npm into a folder your user can't write to; run "claude install" once to switch to Claude Code's own installer, then restart aipager`. Saat tidak ada yang bisa diperbarui, pesannya bilang “Semuanya sudah terbaru.”, bilang ada pengecekan yang gagal, atau, saat ada yang lebih baru yang tidak bisa dipasang dari sini, “Ada pembaruan, tapi tidak bisa dipasang dari sini.”, dengan tombol **Cek lagi** dan tanpa tombol pembaruan.

Tombol pembaruan hanya menjalankan yang ditawarkan pengecekan. Kalau pengecekan itu sudah lebih dari 10 menit, ada pembaruan lain yang berjalan sejak itu, atau versinya tidak lagi cocok dengan tombol, tombol itu justru memintamu mengecek lagi. Tombol dari menu `/update` yang lebih lama (**Perbarui Claude Code** / **Perbarui aipager** / **Keduanya** yang terpisah) menjawab “Menu ini sudah usang, kirim /update lagi” dan tidak melakukan apa pun. Setiap ketukan memeriksa lagi aturan khusus admin.

Memperbarui Claude Code menjalankan `claude update` (dengan path absolut, batas 5 mnt) dan melaporkan `Claude Code A → B`, “sudah terbaru”, atau error-nya beserta ujung keluarannya. Sesi yang sedang berjalan tetap memakai versi lama sampai kamu memulai ulangnya (`/restart`); balasannya mencantumkan sesi-sesi itu. Tidak ada sesi yang dimulai ulang untukmu. Sesi baru memakai versi baru.

Memperbarui aipager (**Perbarui aipager**, atau paruh kedua **Perbarui keduanya**, yang memperbarui Claude Code lebih dulu):

1. Pada pemasangan PyPI yang sudah terbaru, aipager mengatakannya lalu berhenti.
2. Kalau aipager bisa memulai ulang dirinya sendiri, aipager **lebih dulu menunggu sampai tidak ada yang berjalan**: tidak ada sesi yang menjalankan tugas, menunggu jawaban pertanyaan, menahan agen latar belakang yang berjalan, menampilkan kartu kerja yang hidup, menjalankan alat, atau memegang permintaan izin yang terbuka, dan tidak ada jawaban tertahan yang menunggu batas berakhir. Pesannya mencantumkan apa yang ditunggunya, dengan **Mulai ulang sekarang** (lewati penantian) dan **Batal**. Setelah 10 menit aipager bertanya lagi: **Tunggu 10 menit lagi**, **Mulai ulang sekarang**, **Batal**. Kalau satu jam tidak dijawab, aipager membatalkannya sendiri.
3. aipager memperbarui lewat installer yang memiliki aipager yang sedang berjalan (`pipx upgrade aipager`, `uv tool upgrade aipager`, `brew upgrade aipager`, atau `<venv>/bin/python -m pip install --upgrade aipager`), dengan path absolut dan batas 10 mnt. Selama installer berjalan, tidak bisa dibatalkan.
4. aipager memeriksa bahwa versi baru bisa dimuat di interpreter yang baru. Pembaruan yang gagal, terlalu lama, atau tidak bisa dimuat tidak memulai ulang apa pun. Pembaruan yang melewati batas waktu dihentikan di tengah jalan, jadi pesannya memperingatkan bahwa pemasangannya mungkin tidak lengkap dan memberi perintah pasang ulang yang perlu dijalankan sebelum mulai ulang berikutnya. Keluaran installer hanya ditampilkan di chat pribadi; grup mendapat “(keluarannya ada di log aipager)”.
5. Kalau ada tugas yang dimulai selama pembaruan, aipager menunggu lagi.
6. aipager menjadwalkan `systemctl --user restart aipager.service` yang terlepas 5 dtk kemudian (dengan timer yang akurat sampai 1 dtk), dan pesan statusnya menulis `⏳ aipager A → B terpasang, sedang dimulai ulang…`. Selama langkah 5 menunggu, pesannya menulis `⏳ aipager A → B terpasang; dimulai ulang setelah tugas saat ini selesai`. Setelah mulai ulang, aipager yang baru mengubah pesan yang sama itu menjadi `✅ aipager diperbarui A → B, N sesi tersambung lagi` (dan `⚠️ Belum kembali: …` untuk setiap sesi yang tidak kembali). Kalau pesan itu sudah dihapus atau tidak bisa diedit lagi, aipager mengirim hasilnya sebagai pesan baru ke chat yang bertanya.

Di Mini App, **Pengaturan → Pembaruan** menampilkan “Memulai ulang aipager…” dengan lentera berputar sampai aipager yang baru menjawab, lalu “Diperbarui ke B, N sesi tersambung lagi.”. Halaman itu tidak pernah menampilkan hitung mundur. Setelah sekitar 2 menit tanpa jawaban (tidak pernah sebelum aipager lama sempat melaporkan mulai ulang yang tidak terjadi), halaman menulis “Masih dimulai ulang, buka lagi aplikasinya sebentar lagi.”. Dengan tunnel milik aipager yang dikelola, mulai ulang setelah pembaruan mempertahankan alamat aplikasi (aipager yang baru mengambil alih tunnel itu), jadi halaman yang terbuka tetap bertahan dan menampilkan “Diperbarui ke B”. Kalau tunnel itu ternyata tidak bisa dipertahankan, halaman menulis “Masih dimulai ulang, buka lagi aplikasinya sebentar lagi.” setelah sekitar 2 menit. Pembaruan yang dimulai dari aipager yang belum punya fitur ini (atau dengan `MINIAPP_KEEP_TUNNEL=0`) tetap mengubah alamatnya: begitu aipager lama berhenti menjawab, halaman yang terbuka menulis “aipager sudah dimulai ulang. Buka lagi aplikasinya setelah chat bilang sudah diperbarui”; tutup lalu buka lagi dari tombol menu chat atau `/app`. Login aplikasi berlaku 5 menit, jadi halaman yang terbuka lebih lama dari itu justru menulis “Buka lagi aplikasinya untuk melihat hasil pembaruan.”.

Setelah pembaruan, aipager hanya memulai ulang dirinya sendiri saat berjalan sebagai layanan pengguna systemd **dan** layanan itu punya `KillMode=process` (atau `none`). Dengan `KillMode` yang tidak aman, aipager tetap diperbarui, dan pesannya menulis `Perlu mulai ulang: …`: jalankan `aipager service install` dulu (perintah itu mencantumkan sesi yang akan diakhiri oleh mulai ulang).

**Mulai ulang sekarang, tanpa layanan.** aipager yang tidak dimulai ulang oleh pengelola layanan mana pun (`aipager start` di terminal atau tmux, Docker, atau agen launchd di macOS) memasang pembaruan lalu menampilkan `Ketuk “Mulai ulang sekarang” untuk menjalankannya (aipager menunggu sampai tidak ada tugas yang berjalan).` dengan tombol **🔄 Mulai ulang sekarang**. Mengetuknya (hanya admin, seperti setiap tombol pembaruan):

1. memeriksa bahwa aipager yang terpasang bisa dimuat (`⏳ Memeriksa aipager yang terpasang…`); pemasangan yang rusak tidak memulai ulang apa pun, dan versi yang sama menyatakan tidak ada yang perlu dimulai ulang;
2. menunggu, seperti mulai ulang systemd, sampai tidak ada yang sedang berjalan, dengan **Mulai ulang sekarang** (lewati penantian) dan **Batal**;
3. memeriksa pemasangannya sekali lagi, lalu menulis `⏳ aipager A → B terpasang, sedang dimulai ulang…` dan menghentikan aipager dengan rapi (seperti Ctrl+C);
4. menjalankan aipager yang baru di tempat: proses yang sama, perintah yang sama, lingkungan dan folder yang sama, di jendela terminal atau tmux yang sama (dan, di Docker, proses kontainer yang sama). Sesi tetap berjalan. aipager yang baru mengedit pesannya menjadi `✅ aipager diperbarui A → B, N sesi tersambung lagi`.

Ketukan yang dikirim selama beberapa detik mulai ulang itu hilang, seperti pada setiap mulai ulang. Ctrl+C atau `docker stop` selama mulai ulang yang menang: aipager berhenti dan tidak mulai lagi. Kalau aipager yang baru tidak bisa dijalankan di tempat, salinan terpisah menjalankannya setelah yang lama hilang (keluarannya masuk ke log mulai ulang sendiri, `aipager.log` di folder instans atau `/tmp/aipager.log`, dan log aipager menyebut perintah yang harus dijalankan kalau salinan itu tidak kembali); kalau itu pun gagal, atau interpreternya sendiri hilang, aipager keluar dengan kode 1 dan log aipager menulis `restart.failed:` beserta perintah yang harus dijalankan.

Saat aipager sama sekali tidak bisa memulai ulang dirinya sendiri, pesannya memberi langkah persis untuk penyiapanmu, bukan tombol:

- terminal atau tmux: `Di jendela terminal atau tmux tempat aipager berjalan, tekan Ctrl+C, lalu jalankan aipager start.`
- macOS: `Di Mac, jalankan launchctl kickstart -k gui/<uid>/com.aipager.daemon.`
- Docker: `Di komputer tempat Docker berjalan, jalankan docker restart <container>.` (ID pendek kontainer kalau nama host-nya berupa ID), dengan pengingat bahwa membuat ulang kontainer dari image-nya mengembalikan versi aipager di image itu.

**Mulai ulang sekarang** juga muncul di hasil **Cek pembaruan** saat aipager yang lebih baru sudah terpasang dan menunggu, dan di pemberitahuan sekali saja: saat hook Claude Code (yang mulai baru di setiap event, dari aipager yang terpasang) sudah menjalankan aipager yang lebih baru daripada yang sedang berjalan, pemilik mendapat satu pesan senyap per versi yang lebih baru dan per jalannya aipager, `⚠️ Pembaruan aipager baru setengah jalan: …`, dengan tombol itu. Pesan itu menunggu dua menit sejak hook pertama kali menjalankan versi yang lebih baru (mulai ulang yang langsung menyusul pemasangan tidak pernah memunculkannya), lalu sampai tidak ada tugas yang berjalan di chat pribadi pemilik. Setelah mulai ulang ke versi itu, pemberitahuan itu diedit menjadi `✅ Pembaruan aipager sudah selesai: aipager sekarang sudah menjalankan B.`, tanpa tombol. Di Mini App, **Pengaturan → Pembaruan** menampilkan keadaan yang sama sebagai baris di atas kartu, dengan **Mulai ulang sekarang** (dengan konfirmasi dulu).

Di Mini App, **Pembaruan** adalah kartu pertama di tab Pengaturan: namanya, versi yang berjalan, dan tombol kecil **Cek pembaruan** di ujung barisnya. Membuka tab Pengaturan langsung mengecek (paling sering sekali tiap sepuluh menit, tidak pernah saat pembaruan berjalan atau aipager dimulai ulang; pengecekan yang gagal di sana tidak bilang apa-apa), dan mengetuknya selalu mengecek. Kartu itu tetap satu baris yang tenang sampai ada yang perlu dilakukan: versi yang lebih baru membawa baris-barisnya dan satu tombol pembaruan, “Semuanya sudah terbaru.” cukup satu baris tanpa tombol, dan pembaruan yang sedang berjalan, mulai ulang, atau **Mulai ulang sekarang** tetap punya tombolnya sendiri.

Hanya satu pembaruan yang berjalan pada satu waktu, di seluruh `/update`, kartu **Pengaturan → Pembaruan** di Mini App (pengecekan yang sama, satu tombol pembaruan yang sama, pekerjaan yang sama), dan `aipager update` di baris perintah. Ini termasuk detik-detik antara “terpasang, sedang dimulai ulang…” dan mulai ulangnya sendiri: `/update` menjawab bahwa aipager akan segera dimulai ulang, Mini App tidak menawarkan tombol, dan **Mulai ulang aipager** milik fitur pesan suara menolak selama pembaruan berjalan atau menunggu mulai ulang. Kalau aipager masih hidup dua menit setelah menjadwalkan mulai ulangnya, aipager menghentikan timer mulai ulang yang dijadwalkan, melepas kunci pembaruan, dan memintamu memulai ulangnya sendiri.

Kalau aipager dimatikan selagi installer berjalan, installer itu diberi 3 dtk untuk selesai lalu dihentikan (kalau tidak, installer itu akan terus menulis ke pemasangan selagi aipager berikutnya mulai). Pesan status, dan setelah mulai ulang juga pesan baru, mengatakan bahwa pemasangannya mungkin tidak lengkap dan cara memperbaikinya.

Begitu pematian dimulai, tidak ada yang dimulai ulang dan tidak ada yang baru yang dimulai: tombol `/update` menjawab bahwa aipager sedang dimatikan, Mini App menjawab 503, dan tidak ada installer atau pengecekan versi yang dimulai. Installer yang selesai dalam 3 dtk-nya tetap dihitung. Pesan status mengatakan versi baru sudah terpasang dan tidak ada yang dimulai ulang, dan start berikutnya melaporkan `aipager diperbarui A → B`. aipager yang kamu hentikan dengan `aipager service stop` tetap berhenti. Bagian pembaruan dari proses pematian paling lama 8 dtk secara total.

## <a id="free-messages"></a>Pesan biasa

### <a id="text"></a>Teks

Diperlakukan sebagai pesan berikutnya untuk **sesi aktif**: tujuan chat ini, sesi yang terakhir dipilih chat ini, yang terakhir dikirimi pesan dari sini, yang dimulai di sini, atau yang terakhir menjawabmu di sini (lihat [bilah status yang disematkan](#the-pinned-status-bar)). Di grup, setiap orang punya tujuannya sendiri yang tidak bergeser oleh jawaban sesi mana pun (lihat [mode tim](groups.md#talking-to-a-session-in-a-group)). Pesan sampai ke Claude **langsung**, bahkan selagi tugasmu sendiri berjalan, persis seperti mengetik di terminal (di grup, pesan orang lain menunggu sampai tugas itu selesai; lihat di bawah). Kirim beberapa, dan pesan-pesan itu masuk ke antrean di dalam Claude sendiri, yang mengambil masing-masing di batas yang wajar:

Reaksi pada pesanmu mengikutinya, seperti pesan yang menunggu di Claude Code berubah dari abu-abu menjadi putih:

- 👀: diserahkan ke sesi (atau ditahan, lihat di bawah), tapi Claude belum mengambilnya. Pesan yang dikirim selama tugas berjalan tetap 👀 selama menunggu di antrean Claude.
- 👍: Claude sudah mengambilnya: memulai tugas dengannya, memasukkannya ke tugas yang sedang berjalan, atau menyerahkannya ke agen latar belakang yang sedang berjalan.
- 🤷: tidak akan pernah diambil: pesan tertahan yang dibuang `/stop`, `/clearqueue`, atau `/kill`, atau yang tidak bisa dikirim saat dilepas; pesan atau perintah yang masih menunggu saat `/stop`, `/clearqueue`, `/kill`, atau berakhirnya sesi (bukan `/clear` atau `/resume`) membuangnya; atau pesan yang ditolak Claude Code (lihat di bawah). Pesan yang sudah diantrekan Claude hanya ditandai selama aipager bisa melihat antrean itu (pembacaan langsung riwayat percakapan berjalan, dan tidak ada pekerjaan latar belakang yang menunggu); kalau tidak, pesan itu tetap 👀. Escape di terminal mengembalikan antrean Claude ke kolom input-nya, tempat antrean itu bisa dikirim lagi, jadi pesan yang dibuang dengan cara itu juga tetap 👀.
- 👌: perintah Claude Code yang sudah dijalankan (lihat di bawah), atau aipager yang mengonfirmasi `/stop`.

Reaksi selalu hanya bergerak maju, jadi satu pesan mendapat paling banyak tiga, masing-masing sekali. Reaksi yang jatuh saat pemblokiran karena batas Telegram dilewati, bukan ditambahkan belakangan, dan satu pembersihan menandai paling banyak sepuluh pesan terbaru yang dibuangnya.

Tombol perintah (`Ringkas percakapan`, `/model …`): kalau diketuk saat sesinya siap, perintah itu langsung mendapat 👌; Claude Code menjalankannya dengan Enter, dan perintah lokal seperti `/model` tidak memicu hook yang bisa mengatakannya belakangan. Kalau diketuk selama tugas berjalan (bukan `/model`: perintah itu ditolak sampai tugas selesai, lihat [Mengganti model sesi yang sedang berjalan](#switching-a-running-sessions-model)), perintah itu tetap 👀 sampai tugas itu selesai (normal, sebagai perhentian sementara dari pekerjaan latar belakang, atau karena error API), lalu 👌; kalau dibuang sebelum itu oleh `/stop`, `/clearqueue`, `/kill`, atau berakhirnya sesi, perintah itu tidak pernah dijalankan: 🤷. Kalau Claude Code justru mengantrekannya sebagai pesan, perintah itu mengikuti jalur 👀 → 👍. Pesan suara mendapat reaksi yang sama dengan teks, begitu teksnya dikirim.

Kartu kerja, lalu jawabannya, mengikuti pesan yang benar-benar diambil Claude untuk satu tugas (yang dipakainya untuk memulai saat sesinya siap, atau yang dimasukkannya ke tugas yang sedang berjalan), bukan sekadar pesan terakhir yang kamu kirim. Pesan lain di tengah tugas tidak “menarik jawaban” ke dirinya: kalau Claude memasukkannya ke jawaban yang sedang ditulisnya, kartu itu pindah ke pesan itu saat itu juga; kalau Claude menyelesaikan dulu lalu mengambilnya, jawaban pertama tetap di bawah pesan pertama, dan pesan berikutnya mendapat tugasnya sendiri, dengan kartu dan jawabannya sendiri di bawahnya.

Kalau Claude Code langsung menolak pesan (perintah slash yang tidak dikenal, atau perintah bawaan yang di terminal hanya membuka dialog), tidak ada hook yang terpicu, jadi tidak ada yang akan mengakhiri tugas yang baru saja diumumkan aipager. Setelah 8 dtk tanpa hook apa pun (`PROMPT_HOOK_GRACE_SECONDS`), kartu kerja berubah menjadi `⚠️ name · Claude Code tidak menerima pesan` dengan penjelasan satu baris, dan sesinya siap lagi; alasannya terlihat di terminal, dan pesannya mendapat 🤷 (perintah slash justru mendapat 👌: perintah bawaan yang membuka dialog juga tidak memicu hook). Hanya pesan yang memulai tugas yang dinilai dengan cara ini; pesan yang menunggu di belakang tugas yang sedang berjalan tetap 👀 sampai Claude mengambilnya atau pesan itu dibuang.

Tiga kasus ditahan alih-alih dikirim, dan dikirim otomatis setelah selesai:

- Permintaan izin atau pertanyaan sedang terbuka, selama masih terbuka (tidak ada batas waktu): kalau tidak, teksmu akan dibaca sebagai jawaban dialog itu. Pesan pertamamu lalu mendapat satu balasan, di bawah permintaan itu, yang bilang Claude masih menunggu jawabanmu (pesan berikutnya hanya mendapat 👀). Begitu kamu menjawab, di Telegram atau di terminal, pesan yang ditahan dikirim ke Claude sesuai urutan kamu mengirimnya dan balasan itu dihapus. Kalau sesi berakhir saat permintaan masih terbuka, pesan itu dibuang dengan 🤷.
- (Mode tim) Pesan orang lain masih menunggu diambil.
- (Mode tim) Tugas orang lain sedang berjalan, termasuk agen latar belakang yang dimulainya. Tugas yang diketik di terminal dihitung sebagai milik pemilik, jadi hanya pesan pemilik (atau, di chat pribadi, anggotanya sendiri) yang bergabung ke dalamnya.

Dua yang terakhir menjaga pesan dari orang-orang berbeda tetap di luar satu tugas bersama, supaya masing-masing berjalan dengan hak pengirimnya sendiri. Kalau ada yang tetap bergabung (misalnya dari pengirim yang tidak dikenal aipager, yang tidak pernah ditahan), tugas itu berjalan dengan aturan paling ketat dari semua yang ikut (lihat [mode tim](groups.md#who-a-message-runs-as)). Kalau tugas yang ditunggu pesan-pesan itu dihentikan, atau diputus oleh alat yang diblokir, pesan-pesan yang ditahan dibuang dengan 🤷.

Pesan yang ditahan dibatasi 50 per sesi dan kedaluwarsa setelah 24 jam; `/clearqueue` membuangnya bersama semua yang ditahan Claude.

#### <a id="send-a-queued-message-now"></a>Mengirim pesan antrean sekarang

Pesan yang kamu kirim selagi Claude mengerjakan tugasmu sendiri langsung dikirim ke Claude (aipager tidak menahannya) dan menunggu di antrean Claude sampai langkah saat ini selesai. Begitu antrean Claude menampilkannya, aipager membalas di bawahnya:

```
⏳ Dalam antrean - Claude akan membacanya setelah langkah ini
[⚡ Kirim sekarang]
```

Setiap pesan yang menunggu mendapat barisnya sendiri, dan setiap baris hilang bersama 👍-nya saat Claude mengambil pesan itu. Pesan yang diambil Claude sebelum entri antreannya terlihat tidak mendapat baris, hanya 👀 lalu 👍.

Mengetuk `⚡ Kirim sekarang` (atau mengirim `/now`) menekan tombol “kirim sekarang” milik Claude Code sendiri di sesi itu (Ctrl+X, lalu Ctrl+S). Claude lalu membaca semua yang ada di antreannya sekaligus, bukan hanya pesan yang kamu ketuk. Apa yang terjadi pada pekerjaan yang sedang berjalan diputuskan Claude Code: perintah atau agen yang sedang berjalan tetap berjalan di latar belakang; kalau Claude sedang menulis jawaban, jawaban itu diputus dan dimulai lagi dengan pesanmu. Jenis langkah lain (misalnya alat lain) juga bisa diputus setelah jeda singkat. Dalam beberapa kasus (misalnya saat tugas latar belakang Claude Code dimatikan), kirim sekarang menjadi sekadar memutus langkah saat ini. Semua ini perilaku kirim sekarang milik Claude Code sendiri, bukan keputusan aipager.

Ketukan itu menjawab `Langsung dikirim ke Claude`, dan semua baris hilang sekaligus, karena kirim sekarang menyerahkan ke Claude semua yang ditahannya; `/now` menghapusnya dengan cara yang sama, dan tidak ada baris yang datang untuk pesan yang dikirim Kirim sekarang (pesan yang kamu kirim setelahnya mendapat barisnya sendiri). Kalau Claude memutus langkah saat ini alih-alih memindahkannya ke latar belakang, Claude langsung mengambil hanya pesan menunggu yang paling lama dan membaca sisanya di langkah berikutnya; jawabannya datang di bawah pesan yang diambilnya, dan pesan itu tidak berjalan dua kali. Kalau tombol-tombolnya butuh lebih dari sesaat untuk sampai ke Claude (komputer yang sibuk), ketukan itu justru menjawab `Sedang dikirim ke Claude`; barisnya lalu hilang saat tombol-tombol itu sampai, atau, kalau tidak bisa dikirim, tetap ada dan menulis `⏳ Dalam antrean - belum sampai ke Claude; ketuk “Kirim sekarang” lagi`, dengan tombolnya, supaya kamu bisa mengetuk lagi. Tombolnya hanya ditekan saat riwayat percakapan Claude menunjukkan Claude masih menahan pesan itu; kalau tidak, ketukan menjawab `Claude sudah mengambilnya` dan menghapus barisnya. Ketukan ditolak tanpa menekan apa pun selama permintaan izin atau pertanyaan terbuka (`Jawab dulu pertanyaan yang terbuka`), selama Claude mungkin baru saja membuka satu yang belum ditampilkan aipager (event tugas baru ditahan selagi jawaban tugas sebelumnya masih dikirim: `Belum bisa sekarang, coba lagi sebentar lagi`), dan selama aipager sedang mengetik pesan lain ke sesi itu (`aipager sedang mengetik pesan, coba lagi sebentar lagi`). Siapa pun yang boleh mengirim pesan ke sesi itu bisa memakai tombol dan `/now`; anggota `read_only` tidak bisa.

Barisnya hilang sendiri saat pesannya keluar dari antrean Claude: Claude mengambilnya (memasukkannya ke tugas yang sedang berjalan, menyerahkannya ke agen latar belakang, atau memulai tugas berikutnya dengannya), Escape di terminal mengembalikan antrean ke kolom input, atau pesan itu dibuang oleh `/stop`, `/clearqueue`, `/kill`, `/restart`, penghentian karena keamanan, atau berakhirnya sesi. Mulai ulang aipager menghapus setiap baris yang masih terlihat.

Baris-baris ini tidak pernah menunggu pengatur batas: baris itu dikirim dan dihapus seketika, bahkan di chat yang sibuk atau lambat dan dalam mode minimal. Hanya selama Telegram sendiri membungkam chat itu tidak ada baris yang dikirim (`/now` saat itu tetap berfungsi), dan kalau Telegram baru saja meminta aipager melambat, baris itu menunggu beberapa detik yang diminta Telegram. Setiap pesan yang menunggu memakan satu panggilan ke chat untuk barisnya; baris-baris yang pergi bersamaan dihapus dengan satu panggilan.

### <a id="files"></a>File

File yang diunggah diunduh ke folder pribadi milik chat asalnya, `/tmp/aipager-files/<chat>/` (dengan nama `<time>_<file name>`; `<chat>` adalah nama yang dibuat dari ID chat dan token bot, yang tidak membocorkan ID chat itu). Folder-folder itu hanya bisa dibaca oleh akun pengguna yang menjalankan aipager (`0700`, file `0600`), dan tugas anggota di satu chat tidak bisa membaca, mencari, atau menulis unggahan chat lain (lihat [keamanan](security.md#team-mode-enforcement)). File dari sebelum perubahan ini tetap di tempatnya, langsung di `/tmp/aipager-files/`; tidak ada yang dipindahkan atau dihapus, dan saat mulai aipager mempersempit izin folder itu menjadi `0700`, jadi hanya pengguna itu yang bisa menjangkaunya. Token bot baru memberi setiap chat folder baru; unggahan dari sebelumnya tetap di folder lama, yang tidak bisa lagi dibaca tugas anggota. Folder unduhan yang berupa symlink, atau milik pengguna lain, ditolak dan unggahannya gagal. Path-nya ditawarkan ke Claude: dengan keterangan, pesannya adalah keterangan itu diikuti path-nya (atau path-path-nya); tanpa keterangan, cukup `check this: <path>` (atau `check these: <paths>` untuk album), jadi Claude diarahkan ke file itu tanpa diberi tahu apa yang harus dilakukan dengannya. Keterangan yang diawali `/<label>` memilih sesinya, sama seperti `/<label> <prompt>` pada teks, dan nama itu dihapus dari pesan: `/api` saja mengirim `check this: <path>` ke `api`, dan `/api compare these` mengirim `compare these <paths>` ke sana. Hanya `/<label>` di paling awal yang memilih tujuan; garis miring lebih jauh di keterangan hanyalah teks. Nama yang tidak dijawab sesi mana pun ditolak dengan `⚠️ Tidak ada sesi bernama`, dan tidak ada yang dikirim. Batas unduhan bot Telegram sebesar 20 MB diterapkan di awal; file yang terlalu besar mendapat penolakan yang jelas sebelum unduhan dicoba. Unduhan yang terkena error jaringan sementara dicoba sampai tiga kali secara total (menunggu 1 dtk, lalu 2 dtk) sebelum kamu melihat error, dan error itu menyebut nama file-nya. Album (beberapa foto atau dokumen yang dikirim sebagai satu pesan) diserahkan ke Claude sebagai satu pesan (keterangan, lalu path setiap file berurutan) begitu item terakhirnya sampai; kalau satu item gagal diunduh, sisanya tetap dikirim dengan catatan yang menyebut item yang hilang.

### <a id="files-claude-sends"></a>File yang dikirim Claude

Claude bisa mengirimimu file dari sesinya: minta dengan kata-katamu sendiri ("kirim screenshot-nya", "kirim gambar di ~/proj/chart.png"), atau ia mengirim satu saat menurutnya kamu memerlukannya. Ia memakai alat SendUserFile milik Claude Code (Claude Code 2.1.301 atau lebih baru), yang didapat setiap sesi yang dimulai aipager: aipager memulainya dengan `CLAUDE_CODE_ATTACHMENTS_BY_PATH=1` dan memberi tahu Claude bahwa alat itu ada. Sesi yang dimulai sebelum aipager-mu punya fitur ini baru mendapat alat itu setelah dimulai ulang: `/restart`, ganti mode yang memulainya ulang, atau Lanjutkan.

- File datang setelah jawaban tugas, sebagai balasan ke jawaban itu (ke pesanmu kalau tugasnya tidak punya jawaban), tanpa bunyi notifikasi, dan hanya di chat sesi itu sendiri. Selama tugas berjalan, kartu kerja menampilkan `📎 Mengirim 2 file`.
- Setiap file dikirim menurut isinya, bukan namanya: gambar sebagai foto kalau muat dalam batas foto Telegram (paling besar 10 MB, lebar ditambah tinggi paling banyak 10.000, sisi panjang paling banyak 20 kali sisi pendek), kalau tidak sebagai file; GIF sebagai animasi; MP4 sebagai video; MP3 atau M4A sebagai audio; selain itu sebagai file. Kalau Claude meminta lampiran biasa, semuanya dikirim sebagai file.
- 2 sampai 10 foto dan video dikirim sebagai satu album; file bersama file dan audio bersama audio; animasi dikirim sendiri. Kalau lebih banyak, dibagi rata (11 dikirim sebagai 6 dan 5), dan tidak ada album yang lebih dari 50 MB.
- Keterangannya `💬 <session>`, dengan keterangan dari Claude sendiri di baris berikutnya pada pesan pertama, paling banyak 1.024 karakter.
- Setiap file diperiksa dulu, untuk semua orang, owner juga ([keamanan](security.md#files-claude-sends)): pengaturan aipager, kredensial Claude, kunci, dan file yang tampak seperti rahasia tidak pernah dikirim; file harus file biasa, tidak kosong, dan paling besar 50 MB; anggota hanya mengirim file di dalam [folder utama](groups.md#root-folders) chat itu. Kalau satu file dalam sebuah permintaan ditolak, tidak ada yang dikirim dari permintaan itu: Claude diberi tahu alasannya dan memberitahumu, dan tugas berjalan terus.
- aipager mengirim salinan pribadi yang dibuatnya saat Claude Code membaca file itu, disimpan di `~/.local/state/aipager/outbox/` dan dihapus begitu terkirim, atau setelah sehari.
- aipager tidak memasang batas jumlah sendiri: pengiriman mengikuti batas laju Telegram, dan satu album dihitung satu per file. Selama chat dibisukan, file menunggu lalu menyusul jawabannya.
- File yang tidak bisa dikirim mendapat satu baris di chat, misalnya `⚠️ report.pdf tidak terkirim: Telegram menolaknya.` File yang permintaannya tidak ditemukan aipager di riwayat percakapan Claude sendiri tidak mendapat baris apa pun ([keamanan](security.md#files-claude-sends)). File yang permintaannya ada di sana tetapi tidak bisa dikaitkan aipager dengan tugas yang sedang berjalan mendapat `⚠️ report.pdf tidak terkirim: aipager tidak bisa memastikan Claude mengirimnya di tugas ini.` File juga bisa datang dari pesan yang Anda kirim saat Claude bekerja dan diambilnya berikutnya, dan dari tugas yang dibuat terus berjalan oleh hook Stop (`/goal`). Setiap file yang terkirim, ditolak, atau gagal punya catatannya sendiri di [log audit](security.md#audit-log).

### <a id="voice"></a>Pesan suara

Pesan suara melewati `faster-whisper` (paket `aipager[voice]`). Audionya diubah menjadi teks di komputer ini, dikembalikan sebagai `🎙️ Yang kudengar: …`, dan diketik seolah kamu yang menulisnya, termasuk sebagai nama untuk `/new` atau untuk penggantian nama yang sedang menunggu. Pesan suara di atas batas unduhan bot Telegram sebesar 20 MB ditolak. Untuk apa yang terjadi selanjutnya, lihat [Hooks → UserPromptSubmit](hooks.md#userpromptsubmit).

---

## <a id="command-line-every-command"></a>Baris perintah: semua perintah

Jalankan `aipager <command> --help` untuk melihat daftar yang sama di komputermu.

| Perintah | Fungsinya |
|---|---|
| `aipager config` | Wizard pengaturan interaktif: token bot, chat, anggota dan peran, hook Claude Code. Jalankan lagi untuk mengubah apa pun (lihat [mode tim](groups.md#setup)). |
| `aipager setup` | Pengaturan pertama yang sama tanpa bertanya, untuk skrip dan agen coding (lihat [di bawah](#command-line-aipager-setup-for-coding-agents)). |
| `aipager start` | Menjalankan aipager di latar depan. Menolak mulai selama ada aipager lain yang berjalan. |
| `aipager service install [--yes]` | Memasang aipager sebagai layanan latar belakang (layanan pengguna systemd di Linux, agen launchd di macOS) lalu menjalankannya. `--yes` melewati pertanyaan saat layanan yang terpasang berbeda. |
| `aipager service start\|stop\|status\|logs\|uninstall` | Menjalankan, menghentikan, atau menampilkan layanan, mengikuti log-nya, atau menghapusnya. |
| `aipager status [--json]` | aipager dan setiap sesi dalam sekali lihat: keadaan, model, konteks, biaya, dan keadaan batas Telegram. |
| `aipager logs [-f] [-n LINES]` | Log aipager (journald di Linux, log launchd di macOS): 100 baris terakhir, atau `-n` baris; `-f` mengikutinya. |
| `aipager doctor [--fix] [--safety-check] [--json]` | Pemeriksaan kesehatan (lihat [pemecahan masalah](troubleshooting.md#aipager-doctor-check-list)). `--fix` menawarkan untuk memperbaiki login Claude dan instalasi `claude` yang ambigu, dengan bertanya dulu; `--safety-check` mencetak aturan keamanan yang berlaku; `--json` untuk skrip (lihat [di bawah](#aipager-doctor---json)). |
| `aipager session <name> [claude args]` | Memulai sesi Claude Code bernama `<name>` di bawah dtach di folder saat ini, atau menyambung ke sesi itu kalau sedang berjalan. Argumen setelah nama (atau setelah `--`) diteruskan ke `claude` apa adanya, misalnya `--resume`. Dengan `--resume <id>` (atau `--resume=<id>`, `-r <id>`), perintah ini melanjutkan percakapan tempat ID itu pindah di Claude Code, dan lebih dulu menghentikan tugas latar belakang Claude Code yang memegangnya kalau tugas itu sedang tidak mengerjakan apa pun; tugas yang sedang bekerja tidak disentuh: perintah ini mencetak `claude stop <job>` dan `claude attach <job>` lalu keluar dengan kode 1. `--continue` (`-c`) tidak menyebut percakapan apa pun, jadi tidak ada yang diikuti: argumen itu diteruskan ke `claude` apa adanya. Butuh aipager yang sedang berjalan. |
| `aipager session ls [-a] [--json]` | Mencantumkan sesi; `-a` menyertakan yang sudah berakhir. Juga `aipager session list`. |
| `aipager session kill <name> [-y]` | Mengakhiri sesi, setelah bertanya kecuali dengan `-y`. |
| `aipager resume [name]` | Mengembalikan sesi yang sudah berakhir dengan percakapannya; tanpa nama, daftar pilihan. Perintah ini mengikuti percakapan yang sudah pindah dan menangani tugas latar belakang yang memegangnya, sama seperti `aipager session --resume`. |
| `aipager miniapp enable [--port PORT] [--url URL]` | Menyalakan Mini App, di `127.0.0.1:PORT` (bawaannya 8765), di belakang tunnel yang dikelola atau URL `https://` milikmu sendiri. Berlaku pada mulai ulang aipager berikutnya. |
| `aipager miniapp disable\|status` | Mematikannya (pada mulai ulang berikutnya), atau menampilkan pengaturan dan alamatnya. |
| `aipager policy validate` | Memeriksa `policy.yaml` (dan `policy.d`); keluar dengan kode bukan nol kalau ada masalah. |
| `aipager update` | Memperbarui aipager lewat installer yang memilikinya (uv, pipx, Homebrew, pip). Tidak pernah memulai ulang aipager; mulai ulang sendiri, atau pakai `/update` di Telegram, yang bisa melakukannya (lihat [Pembaruan](#update)). |
| `aipager report [--note TEXT]` | Menampilkan laporan masalah dan mengirimnya kalau kamu bilang ya (lihat [di bawah](#command-line-aipager-report)). |
| `aipager uninstall [-y]` | Menghentikan dan menghapus aipager serta konfigurasinya, dengan tetap menyimpan datanya (lihat [di bawah](#command-line-aipager-uninstall)). |
| `aipager version`, `aipager --version` | Mencetak versinya. |
| `aipager help [command]` | Bantuan untuk aipager atau satu perintah. |

## <a id="command-line-aipager-setup-for-coding-agents"></a>Baris perintah: `aipager setup` (untuk agen coding)

Bagian-bagian di atas menjelaskan bot Telegram. Bagian ini menjelaskan perintah yang kamu jalankan di terminal, atau yang dijalankan skrip atau agen coding-mu untukmu: `aipager setup` mengatur aipager tanpa bertanya, dan hasilnya sama dengan menjalankan `aipager config` pertama kali.

```sh
# 1. cari tahu ID Telegram orangnya (ia membuka bot dan mengetuk Start)
aipager setup detect-chat --token-file ~/bot-token.txt --json
# 2. setelah orangnya mengonfirmasi bahwa itu dia, atur aipager
aipager setup --token-file ~/bot-token.txt --chat-id 123456789 --service --json
# 3. periksa pemasangannya
aipager doctor --json
```

Yang dilakukannya, berurutan: membaca token bot, memeriksanya di Telegram (getMe), memeriksa semua yang ada di komputer ini tanpa menulis apa pun (konfigurasi yang ada, dependensi, `~/.claude/settings.json` (atau `$CLAUDE_CONFIG_DIR/settings.json`), apakah aipager sedang berjalan), mengirim pesan tes ke orangnya (“aipager is set up for you. ...”), lalu baru menulis. File yang ditulis, persis seperti yang ditulis wizard:

- `~/.config/aipager/aipager.yaml` (izin 0600): `bot_token` dan chat pribadi dengan orang itu sebagai satu-satunya anggota (`label: owner`, peran owner atau admin).
- `~/.claude/settings.json` (atau `$CLAUDE_CONFIG_DIR/settings.json`, file yang dibaca sesi-sesi daemon: lingkungan shell ini ditambah `daemon.env`): hook aipager dan statusLine (file yang ada disimpan sebagai `settings.json.bak.<time>` kalau berubah). `settings_json.path` di keluaran JSON menyebutkannya.
- Log audit mendapat entri `grant-owner` saat seseorang menjadi pemilik.
- Dengan `--service`, layanan latar belakang (sama dengan `aipager service install --yes`).
- Hanya pada pemasangan lama dengan `config.env` (belum ada `aipager.yaml`), pemindahan yang dilakukan wizard: salinan `config.env.bak.<time>` (dan `team.yaml.bak.<time>` kalau ada `team.yaml`), serta templat `~/.config/aipager/policy.yaml` kalau belum ada. `config.env` sendiri dibiarkan apa adanya.

Kalau ada pemeriksaan yang gagal, tidak ada yang dikirim dan tidak ada yang ditulis. Kalau aipager sudah berjalan, perubahan chat atau peran dimuat ulang langsung; token bot baru butuh mulai ulang (`aipager service stop`, lalu `aipager service start`), yang tidak pernah dilakukan setup sendiri, dan `--service` dilewati. Kalau setup tidak bisa mengetahui apakah aipager sedang berjalan, setup bertindak seolah aipager berjalan (peringatan `daemon_unknown`): setup lalu tidak pernah memasang layanan, jadi tidak pernah bisa memulai ulang aipager yang berjalan, tidak mengirim muat ulang setelah perubahan, dan justru melaporkan `restart_needed: true`.

### <a id="aipager-setup-flags"></a>Opsi `aipager setup`

| Opsi | Arti |
|---|---|
| `--token-file PATH` | Membaca token bot dari file ini (paling banyak 4096 byte; teks di sekitar token tidak apa-apa). File yang bisa dibaca pengguna lain tetap berfungsi tapi memberi peringatan `token_file_shared`: lakukan `chmod 600`, atau hapus setelah setup berhasil. |
| `--token-stdin` | Membaca token bot dari stdin (pipe atau file yang dialihkan, tidak pernah terminal). Pipe-nya harus tertutup: setup menunggu akhir stdin paling lama 30 detik, lalu keluar dengan 2 dan `token_stdin_timeout`. `printf '%s\n' "$TOKEN" \| aipager setup --token-stdin ...` dan `< token.txt` sama-sama menutupnya. |
| `--chat-id N` | Wajib. ID pengguna Telegram numerik orang itu, yang juga ID chat pribadinya. Grup ditambahkan dengan `aipager config`. |
| `--role owner\|admin` | Peran orang itu (bawaannya `owner`). |
| `--service` | Juga memasang dan menjalankan layanan latar belakang (dilewati selama aipager berjalan). |
| `--force` | Membolehkan mengganti token bot, chat pribadi pemilik, atau peran pada pemasangan yang ada. Chat grup dan chat pribadi lain tetap disimpan. |
| `--dry-run` | Menjalankan setiap pemeriksaan, tidak mengirim apa pun, tidak menulis apa pun, dan melaporkan apa yang akan berubah. |
| `--json` | Mencetak tepat satu objek JSON ke stdout; semua yang lain ke stderr. |

Berikan tepat satu dari `--token-file` dan `--token-stdin`. Token tidak pernah diterima sebagai nilai opsi: `--token` dan `--bot-token` ditolak (`token_on_command_line`), dan nilainya tidak ditampilkan. Opsi yang disingkat (`--token-f`, `--dry`) tidak diterima. setup tidak pernah mengajukan pertanyaan, tidak pernah butuh terminal, dan tidak pernah membaca token dari variabel environment.

Menjalankan lagi dengan token, chat, dan peran yang sama tidak mengubah apa pun dan mengatakannya (`unchanged`, tanpa pesan tes). Token, chat, atau peran yang berbeda pada pemasangan yang ada keluar dengan 6 sampai kamu menambahkan `--force`. Pemasangan lama dengan `config.env` dipindahkan dulu, seperti yang dilakukan wizard (pemasangan itu mendapat peran `admin`, jadi `--role owner` di atasnya butuh `--force`).

### <a id="aipager-setup-detect-chat"></a>`aipager setup detect-chat`

```sh
aipager setup detect-chat (--token-file PATH | --token-stdin) [--timeout SECONDS] [--json]
```

Menunggu pesan pribadi terbaru ke bot (mengetuk Start mengirim satu) dan mencetak pengirimnya: ID, nama, dan username. Perintah ini tidak menyimpan apa pun, jadi tunjukkan dulu orang yang ditemukan, dan jalankan `aipager setup --chat-id` hanya setelah orang itu dikonfirmasi. Yang dihitung hanya pesan yang dikirim tidak lebih dari 10 menit sebelum perintah ini dimulai. `--timeout` bernilai 0 sampai 3600 detik (bawaannya 300; 0 berarti melihat sekali saja). Perintah ini membaca Telegram tanpa mengonfirmasi pesan apa pun, jadi `/start` tetap tersedia untuk wizard dan untuk aipager.

Kalau ada aipager yang berjalan untuk bot yang sama, aipager itulah yang menerima pesan-pesan bot, dan aipager itu tidak mencatat pesan pribadi dari orang yang belum diatur. detect-chat lalu keluar dengan 9 (`source: "daemon"`): hentikan aipager (`aipager service stop`) lalu jalankan lagi, atau tanyakan ID numerik orang itu. Dengan aipager yang berjalan untuk bot lain, perintah ini membaca Telegram langsung (`source: "telegram"`).

Batasan: karena detect-chat tidak pernah mengonfirmasi pesan, Telegram hanya menunjukkan kepadanya 100 pembaruan belum dibaca yang paling lama dari 24 jam terakhir. Kalau bot punya sebanyak itu yang menunggu, `/start` yang baru tidak terlihat: detect-chat lalu berakhir karena batas waktu (keluar 8) dengan peringatan `updates_backlog`, dan mengetuk Start lagi tidak akan membantu. Tanyakan saja ID Telegram numerik orang itu (atau jalankan aipager sekali, yang membaca dan menghabiskan pembaruan-pembaruan itu).

### <a id="exit-codes"></a>Kode keluar

| Kode | `error` | Arti |
|---|---|---|
| 0 | | Berhasil (`installed`, `updated`, `unchanged`, `dry_run`, atau detect-chat `found`). |
| 1 | `config_malformed`, `ambiguous_install`, `settings_invalid`, `test_send_failed`, `write_failed`, `service_failed`, `internal_error` | Gagal. `ambiguous_install`: lebih dari satu chat pribadi bisa jadi milik pemilik; perbaiki di `aipager config`. |
| 2 | `usage`, `missing_token_source`, `token_source_conflict`, `token_file_unreadable`, `token_stdin_is_tty`, `token_stdin_timeout`, `token_malformed`, `token_on_command_line`, `bad_chat_id`, `bad_role`, `bad_timeout` | Input salah atau tidak ada; `fix` menyebutkan opsinya. Opsi yang tidak dikenal atau kata yang salah setelah `setup` dilaporkan oleh pengurai argumen (teks biasa, bukan JSON), juga dengan 2. |
| 3 | `deps_missing` | dtach, claude, aipager-hook, atau aipager-statusline tidak ada; `fix` berisi perintahnya. Tidak ada yang ditulis. |
| 4 | `token_rejected` | Telegram menolak token itu. |
| 5 | `chat_not_started`, `bot_blocked` | Orang itu belum mengetuk Start di bot (“Open t.me/&lt;bot&gt; and press Start, then run this again.”) atau memblokirnya. Tidak ada yang ditulis. |
| 6 | `existing_install` | Token, chat, atau peran lain sudah diatur; tambahkan `--force`. |
| 7 | `telegram_unreachable` | Error jaringan, atau Telegram menjawab 429 atau 5xx. |
| 8 | `detect_timeout` | detect-chat tidak melihat pesan pribadi sebelum `--timeout` (dengan peringatan `updates_backlog` saat 100 pembaruan lama yang belum dibaca menutupi yang lebih baru). |
| 9 | `daemon_running` | detect-chat: aipager yang sedang berjalan menerima pesan-pesan bot. |
| 10 | `updates_conflict` | detect-chat: program lain sedang membaca pesan bot ini. |
| 130 | `interrupted` | Ctrl-C. |

Kalau beberapa pemeriksaan gagal sekaligus, kodenya mengikuti urutan ini: `config_malformed`, `ambiguous_install`, `existing_install`, `settings_invalid`, `deps_missing`. `--dry-run` keluar dengan kode yang akan didapat jalankan sungguhan berdasarkan pemeriksaan-pemeriksaan itu.

### <a id="json-aipager-setup---json"></a>JSON: `aipager setup --json`

Setiap kunci selalu ada; nilai yang belum diketahui adalah `null`.

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

- `status`: `installed`, `updated`, `unchanged`, `dry_run`, atau `error`.
- `changed`: apa yang berubah (atau akan berubah, pada jalankan uji), dengan urutan ini: `migrated_v1`, `bot_token`, `owner_dm`, `role`, `settings_json`, `service`.
- `test_message`: `sent`, `not_needed`, `skipped_dry_run`, `failed`, atau `not_attempted`.
- `settings_json.status`: `created`, `patched`, `unchanged`, `would_change`, atau `not_checked`.
- `daemon.reload`: `reloaded`, `refused`, `not_reloaded`, atau `not_needed`. `restart_needed` bernilai true setelah token baru selagi aipager berjalan, dan setelah perubahan apa pun saat aipager tidak bisa dimuat ulang atau setup tidak bisa mengetahui apakah aipager berjalan.
- `service.result`: `not_requested`, `installed`, `already_installed`, `skipped_daemon_running`, `would_install`, `failed`, atau `null` saat setup berhenti lebih dulu.
- `warnings`: objek dengan `code` (`token_file_shared`, `reload_refused`, `daemon_unknown`, `audit_write_failed`) dan `message`. `audit_write_failed`: peran pemilik sudah ditulis, tapi entri `grant-owner`-nya tidak bisa ditambahkan ke log audit; entri itu dibuat sebisanya, jadi kode keluarnya tetap 0.
- `next_step` setelah perubahan selagi aipager berjalan yang tidak bisa dimuat ulang memintamu memulai ulangnya (`aipager service stop`, lalu `aipager service start`); token bot hanya disebut kalau token-nya berubah.
- Dengan peringatan `daemon_unknown`, `next_step` tidak pernah mengatakan aipager sedang berjalan. `next_step` mengatakan setup tidak bisa mengetahuinya, mengarahkan ke `aipager doctor --json`, dan memintamu menjalankan aipager dengan `aipager service install` atau `aipager start` kalau tidak berjalan. Kalau mulai ulang dibutuhkan, `next_step` memintanya hanya kalau aipager berjalan, dan kalau tidak, memintamu menjalankannya dengan cara yang sama.
- Saat error, `status` bernilai `error`, dan `error`, `message`, serta `fix` mengatakan apa yang salah dan apa yang harus dilakukan; `next_step` mengulang `fix`. Kalau error terjadi setelah ada yang ditulis, `changed` mencantumkan apa yang sudah ditulis, dan keluaran biasa menambahkan satu baris di stderr: `Already written: aipager.yaml, ... (changed: ...)`.

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

`candidate` bernilai `null` kecuali `status` bernilai `found`. `warnings` berisi objek dengan `code` (`token_file_shared`, `updates_backlog`) dan `message`. `other_candidates` menghitung orang lain yang mengirim pesan ke bot dalam 10 menit yang sama: kalau bukan 0, pastikan kamu memilih orang yang tepat. `next_step` mengulang opsi token yang kamu pakai, tidak pernah token-nya.

### <a id="aipager-doctor---json"></a>`aipager doctor --json`

Pemeriksaan `aipager doctor` sebagai satu objek JSON di stdout. Seperti `aipager doctor` biasa, perintah ini hanya membaca dan tidak mengirim pesan Telegram. Keluar 1 kalau ada pemeriksaan yang gagal, kalau tidak 0. Tidak bisa digabung dengan `--fix` atau `--safety-check` (keluar 2 dengan objek error).

```json
{
  "command": "doctor",
  "version": "1.0.1",
  "ok": true,
  "summary": {"ok": 14, "warn": 1, "fail": 0},
  "checks": [{"key": "token_valid", "status": "ok", "title": "Telegram bot token", "detail": ["@example_bot"], "fix": null}]
}
```

`checks` muncul dengan urutan ini, dengan kunci-kunci ini: `config_parses`, `config`, `token_valid`, `chat_reachable`, `team`, `role_shell_access`, `peer_messages`, `claude`, `claude_auth`, `dtach`, `hook_scripts`, `settings_json`, `daemon`, `service_installed`, `service_unit_path`, `miniapp`. Pemeriksaan yang crash mempertahankan kuncinya dengan status `warn`. Teksnya biasa (tanpa markup), dan apa pun yang mirip token bot diganti dengan `<redacted>`.

## <a id="command-line-aipager-uninstall"></a>Baris perintah: `aipager uninstall`

`aipager uninstall` menghentikan aipager dan menghapus layanannya, menghapus folder konfigurasinya (`~/.config/aipager`: token bot, chat, dan pengaturan) dan daftar sesinya (`~/.claude/aipager-sessions.json`), membersihkan file aipager di `/tmp`, lalu mencopot aipager sendiri. Perintah ini bertanya dulu (`-y` melewati pertanyaannya).

Perintah ini menyimpan data aipager supaya pemasangan ulang bisa melanjutkan dari tempat terakhir: `~/.local/share/aipager` (folder sesi, program pembantu yang diunduh, file kunci dan penanda), `~/.local/state/aipager` (catatan laporan masalah dan keadaan bahasa), `~/.claude/aipager-audit.jsonl` (log jawaban izin), `~/.claude/aipager-flood-state.json` (riwayat batas Telegram), `~/.claude/aipager-pending-users.json` (orang yang mencoba bot tanpa akses), dan `~/aipager-users` ([folder utama](groups.md#root-folders) para anggota, dengan pekerjaan mereka). Sebelum bertanya, perintah ini mencantumkan mana yang ada di komputermu, dan di akhir mencetak perintah `rm -rf` yang juga menghapusnya.

Perintah ini juga mengeluarkan entri milik aipager sendiri dari `~/.claude/settings.json` (dan dari file yang dibaca sesi-sesi daemon, kalau `CLAUDE_CONFIG_DIR` menunjuk file lain) milik Claude Code: hook yang menjalankan `aipager-hook` dan baris status yang menjalankan `aipager-statusline` (baris status milikmu sendiri tetap ada). Semua yang lain di file itu tetap seperti semula, dan file dalam bentuk sebelumnya disimpan di sebelahnya sebagai `settings.json.bak.<time>`. Kalau file itu berupa tautan ke file lain, tidak bisa dibaca, bukan JSON yang valid, atau bentuknya tidak seperti yang dibaca Claude Code, file itu dibiarkan persis apa adanya dan uninstall memberi tahu entri mana yang perlu dihapus dengan tangan. Hook milikmu sendiri yang masih menyebut `aipager-hook` atau `aipager-statusline` (misalnya pembungkus `aipager-hook-capped.sh`) juga dibiarkan, dan uninstall menyebutnya. Sesi Claude Code yang sudah terbuka tetap menjalankan hook yang sudah dimuatnya sampai kamu memulai ulangnya. `aipager config` menambahkan entri-entri itu lagi setelah pemasangan ulang. Bot Telegram-mu dan cadangan `settings.json.bak.*` yang lebih lama tidak disentuh, begitu juga `skipDangerousModePermissionPrompt` (pengaturan yang melewati peringatan Claude Code sebelum berjalan tanpa pemeriksaan izin), yang diatur aipager tapi mungkin juga kamu atur sendiri.

## <a id="command-line-aipager-report"></a>Baris perintah: `aipager report`

`aipager report` menyusun laporan masalah yang sama jenisnya dengan tombol Laporkan masalah di bot, dari apa yang dicatat aipager di komputer ini (dengan lebih sedikit fakta tentang aipager yang sedang berjalan: mode, chat, fitur, lama berjalan, sesi, dan detail Claude Code tetap tidak diketahui atau kosong), mencetaknya utuh, dan bertanya `Send this report? [y/N]`. Hanya `y` atau `yes` yang mengirimnya; apa pun yang lain, atau tanpa terminal sama sekali (skrip, pipe, shell sesi Claude Code), tidak mengirim apa pun. `--note "text"` menambahkan catatan paling banyak 500 karakter, yang terlihat di laporan yang dicetak. Keluar 0 saat laporan terkirim, tidak dikirim atas pilihanmu, atau hanya dicetak, dan 1 saat pengiriman yang kamu konfirmasi tidak terkirim. Untuk isi laporan dan ke mana laporan itu dikirim, lihat [Laporan masalah](problem-reports.md).

## <a id="settings-in-the-environment"></a>Pengaturan dari environment

Beberapa pengaturan dibaca dari environment aipager, bukan dari `aipager.yaml`. Untuk layanan systemd, tambahkan dengan `systemctl --user edit aipager` di bawah `[Service]` (`Environment=STALE_BUSY_TIMEOUT=900`), lalu `aipager service stop` dan `aipager service start`; untuk layanan launchd di macOS, tambahkan ke `EnvironmentVariables` di plist-nya (`~/Library/LaunchAgents/com.aipager.daemon.plist`; `aipager service install` menulis ulang file itu, jadi tambahkan lagi setelahnya); untuk aipager yang kamu jalankan sendiri, atur sebelum `aipager start`. Tidak satu pun dibutuhkan untuk pemakaian normal.

| Pengaturan | Bawaan | Yang diubahnya |
|---|---|---|
| `AIPAGER_WHISPER_MODEL` | `base` | Model pengenalan suara untuk pesan suara (`tiny`, `base`, `small`, `medium`, ...): yang lebih besar lebih akurat dan lebih lambat. |
| `MINIAPP_PORT` | `8765` | Port Mini App di `127.0.0.1` (`aipager miniapp enable --port` justru menyimpannya). |
| `MINIAPP_ENABLED` | dari `aipager.yaml` | `0` mematikan Mini App dan `1` menyalakannya, apa pun kata `aipager.yaml` (`aipager miniapp enable\|disable` justru menyimpan pilihannya). |
| `MINIAPP_PUBLIC_URL` | tidak ada | Menyajikan Mini App di URL `https://` milikmu sendiri, bukan lewat tunnel yang dikelola ([model keamanan](security.md#mini-app-tunnel)). |
| `KEEP_FINISHED_CARD` | `1` | `0` menjadikan “Hanya jawaban, kartu dihapus” sebagai tata letak untuk chat yang belum pernah memilih tata letak di `/settings`. |
| `STALE_BUSY_TIMEOUT` | `7200` (2 jam) | Berapa detik sesi yang bekerja boleh tanpa kabar sebelum chat mendapat catatan “masih bekerja (120 menit tanpa kabar)”. |
| `PROMPT_HOOK_GRACE_SECONDS` | `8` | Detik sebelum pesan yang tidak pernah diterima Claude Code ditandai sebagai “Claude Code tidak menerima pesan” ([pesan biasa](#text)). |
| `FINISH_CARD_GRACE_SECONDS` | `0.8` | Seberapa jauh kartu yang selesai mendahului jawaban dalam tata letak kartu; `0` mengirim keduanya bersamaan. |
| `GONE_SESSION_MAX_AGE_DAYS` | `14` | Berapa hari sesi yang sudah berakhir tetap ada di `/resume`; `0` menyimpannya selamanya. |
| `STREAM_EDIT_INTERVAL`, `BUSY_EDIT_INTERVAL` | `1.2`, `3.0` | Pembaruan kartu kerja selagi teks mengalir dan selagi tenang, dalam detik; batas Telegram per chat tetap menang ([pemecahan masalah](troubleshooting.md#busy-cards-got-slower)). |
| `TYPING_INDICATOR_INTERVAL` | `4.5` | Detik di antara gelembung “sedang mengetik…”; `0` mematikannya. |
| `TELEGRAM_MAX_RETRY_AFTER` | `90` | Penantian Telegram, dalam detik, yang mulai dianggap aipager sebagai pemblokiran (jangan diturunkan; lihat [pemecahan masalah](troubleshooting.md#the-bot-went-quiet-flood-control)). |
| `CLAUDE_RICH_SUMMARIES` | `1` | `0` mematikan format kaya untuk jawaban yang berisi blok kode (jalan pintas dari versi lama). |
| `AIPAGER_INTERACTIVE_TIMEOUT` | `300` | Berapa detik permintaan izin atau pertanyaan boleh menunggu tanpa kabar dari Claude Code sebelum aipager memeriksa percakapan yang tersimpan; hanya kalau percakapan itu menunjukkan permintaannya sudah selesai, sesinya kembali dihitung bekerja ([pemecahan masalah](troubleshooting.md#a-permission-prompt-with-no-news-for-5-minutes)). |
| `AIPAGER_SUBAGENT_SILENCE` | `1800` | Detik keheningan setelah subagen yang berhentinya tidak terlihat dihapus ([hooks](hooks.md#subagentstart--subagentstop)). |
| `AIPAGER_BG_SHELL_MAX_TRACK` | `7200` | Detik setelah perintah latar belakang yang akhirnya tidak terlihat berhenti dihitung. |
| `AIPAGER_REPORT_PROMPTS` | nyala | `0` mematikan tawaran laporan masalah otomatis ([laporan masalah](problem-reports.md#when-aipager-offers-a-report)). |
| `AIPAGER_CLAUDE_BIN` | tidak ada | `claude` yang dijalankan saat ada beberapa yang terpasang (`claude_path` di `aipager.yaml` menang). |
| `AIPAGER_SOCKET_PATH` | `$XDG_RUNTIME_DIR/aipager.sock` | Soket kontrol aipager. Sesi yang dimulai aipager meneruskannya ke hook-nya. |
| `AIPAGER_DEBUG` | mati | `1` di environment sesi membuat `aipager-hook` dan `aipager-statusline` mencetak diagnosisnya ke stderr. |
| `AIPAGER_INSTANCE_DIR`, `AIPAGER_TELEGRAM_API_BASE` | tidak ada | aipager kedua yang terisolasi untuk pengujian ([arsitektur](architecture.md#isolated-instance-for-testing)). |

## <a id="see-also"></a>Lihat juga

- [Arsitektur](architecture.md): di mana bot berada.
- [Event hook](hooks.md): apa yang didengar aipager kembali dari claude.
- [Pemecahan masalah](troubleshooting.md): saat perintah berperilaku aneh.
