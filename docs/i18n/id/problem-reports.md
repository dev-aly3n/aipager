# Laporan masalah: isi sebuah laporan

<!-- i18n-source: docs/problem-reports.md @ 6fc084f -->

> Halaman ini diterjemahkan dari bahasa Inggris; kalau keduanya berbeda, versi bahasa Inggris yang berlaku. ([English](../../problem-reports.md))

Saat aipager menemui bug, kamu bisa mengirim laporan masalah ke orang yang merawatnya dengan satu ketukan. Kamu melihat seluruh laporan sebelum apa pun dikirim, tidak ada yang keluar dari komputermu sampai kamu bilang, dan laporan itu tidak pernah berisi chat, pesan, nama, path, atau token milikmu. Laporan disusun hanya dari daftar field yang tetap: versi, jumlah, tanggal, jawaban ya/tidak, pilihan dari daftar tetap, lokasi di kode aipager sendiri, dan nama bug aipager yang berawalan `ap1-`, ditambah catatan singkat hanya kalau kamu menulisnya.

## <a id="how-to-send-one"></a>Cara mengirimnya

Yang bisa mengirim laporan dari Telegram hanya pemilik instalasi aipager: orang di chat pribadi instalasi itu sendiri dengan bot (operatornya; kalau ada beberapa chat seperti itu dan tidak ada satu pemilik, tidak ada tombol “Laporkan masalah” dan tidak ada tawaran otomatis, sedangkan `aipager report` di terminal tetap berjalan). Mini App punya halaman laporan sendiri; semua jalur lain dari Telegram membuka kartu pratinjau yang sama di chat pribadimu dengan bot:

- **`/help`**: saat pemilik mengirimnya, teks bantuannya datang dengan tombol “🐞 Laporkan masalah”. Di grup, hanya ketukan pemilik yang berlaku, dan pratinjaunya dibuka di chat pribadi pemilik, tidak pernah di grup.
- **Mini App**: tab Pengaturan, “Laporkan masalah” (saat aplikasi dibuka di chat pribadimu) membuka halaman di dalam aplikasi. Catatanmu ada di paling atas (opsional, sampai 500 karakter, dengan penghitung). Di bawahnya ringkasan singkat laporan: versi, pengaturan, error (ketuk satu untuk melihat lokasinya di kode), dan **Detail lainnya**. **Tampilkan isi persis laporan** menampilkan teks persis yang akan dikirim, termasuk catatanmu, dan teks itu berubah sambil kamu mengetik. **Kirim laporan** (tombol Telegram di bawah) mengirim persis itu, sekali, lalu halaman menampilkan kode referensi dengan tombol **Salin**. Kembali tidak mengirim apa pun. Kalau aipager merapikan catatanmu (spasi di tepi, karakter tak terlihat, apa pun setelah 500 karakter), halaman menampilkan catatan yang sudah dirapikan, dan tidak ada yang dikirim sampai kamu mengetuk “Kirim laporan” lagi.
- **Notifikasi batas memori**: saat hook aipager mentok di batas memorinya, notifikasi di chat punya tombol “🐞 Laporkan ini”.
- **Tawaran otomatis**: sesekali aipager menawarkan untuk mengirim laporan tentang bug yang dilihatnya (lihat [Kapan aipager menawarkan laporan](#when-aipager-offers-a-report)).
- **Di terminal**: `aipager report` mencetak laporan dan bertanya `Send this report? [y/N]`. Isinya jenis field yang sama, tapi dengan lebih sedikit informasi tentang aipager yang sedang berjalan (mode, chat, fitur, lama berjalan, sesi, dan detail Claude Code tetap tidak diketahui atau kosong). `aipager report --note "what you were doing"` menambahkan catatan. Tanpa terminal (skrip, pipe, shell sesi Claude Code), perintah ini mencetak laporan dan tidak pernah mengirimnya.

Kartu pratinjau menampilkan laporan persis seperti yang akan dikirim: JSON dalam blok terlipat yang bisa kamu buka, atau, kalau terlalu panjang untuk satu pesan, file `report.json` sebagai balasan ke kartu. Tombolnya:

- **📤 Kirim**: mengirim laporan persis ini, sekali. Kartu lalu menampilkan `Terkirim. Kode referensi: ap1-...`.
- **✏️ Tambah catatan**: pesanmu berikutnya di chat ini dalam 10 menit menjadi catatan laporan (dipotong di 500 karakter), dan kartu menampilkan laporan lagi dengan catatan itu sebelum kamu mengirimnya. Kalau kamu melakukan hal lain (sebuah perintah, tombol lain, foto, tindakan di Mini App), pertanyaan catatan ditutup tanpa catatan, dan setelah 10 menit pesan berikutnya dikirim ke Claude seperti biasa. Pertanyaan itu punya **‹ Kembali**, yang kembali ke pratinjau tanpa catatan, dan Batal.
- **✖️ Batal**: tidak ada yang dikirim.

Pratinjau tetap terbuka 24 jam, dan 5 yang terbaru tetap terbuka (yang ke-6 menutup yang paling lama). Setelah aipager dimulai ulang, kartu lama tidak bisa dikirim lagi: buka “Laporkan masalah” lagi untuk mendapat yang baru. Laporan yang dibuka di Mini App bisa dikirim dalam 24 jam (3 yang terbaru); setelah mulai ulang, halaman itu memuat laporan baru.

## <a id="what-a-report-holds"></a>Isi sebuah laporan

Setiap bagian, dengan kata-kata sederhana. Setiap nilai dicocokkan dengan daftar nilai yang diizinkan sebelum pratinjau ditampilkan dan sekali lagi sebelum dikirim; apa pun yang tidak cocok diganti dengan `<invalid>` atau dibuang, tapi tidak pernah dikirim apa adanya.

**Tentang laporan**
- `schema`: format laporan (`aipager-report/1`).
- `trigger`: `manual` (laporan dibuka sendiri) atau `auto` (tawaran otomatis).
- `day`: tanggal, dalam UTC (tanpa jam).
- `note`: hanya kalau kamu menambahkannya: kata-katamu, persis seperti yang ditampilkan pratinjau (paling banyak 500 karakter, tanpa karakter tak terlihat).

**aipager dan komputernya**
- `aipager`: versi; cara memasangnya (`pipx`, `uv`, `brew`, `pip`, `editable`, `nix`, `snap`, `docker`, `system`, `foreign`, atau `unknown`); dari mana (indeks paket, folder lokal, salinan git); apakah `aipager update` bisa memperbaruinya.
- `python`: versi Python dan apakah itu CPython atau PyPy.
- `os`: sistem (`linux`, `darwin`, `windows`, `freebsd`), jenis prosesor, distribusi Linux dari daftar tetap beserta nomor versinya, versi kernel major.minor, jenis kontainer kalau ada (`docker`, `lxc`, `wsl`, `podman`), dan apakah aipager diatur sebagai layanan systemd atau launchd.
- `deps`: versi pustaka yang dipakai aipager (python-telegram-bot, httpx, aiohttp, PyYAML, rich, questionary, dtach-bin).
- `claude_code`: versi Claude Code, berapa instalasi Claude Code yang ditemukan, dan cara ia login (`env`, `file`, `keychain`, atau kenapa hal itu tidak bisa diketahui). Tidak pernah data login-nya sendiri.

**Pengaturanmu dalam angka**
- `config`: mode (`personal`, `team`, `scope`); berapa chat pribadi dan berapa grup yang diatur; berapa peran buatan sendiri yang ada (namanya tidak disertakan); fitur mana yang menyala (Mini App, jenis tunnel-nya, pesan suara, pratinjau perubahan, ringkasan kaya).
- `runtime`: sudah berapa lama aipager berjalan, sebagai rentang (`<10m`, `10m-1h`, `1-24h`, `1-7d`, `>7d`); berapa sesi yang dimulai dan sedang berjalan; berapa kali aipager berhenti tanpa diduga dalam 7 hari terakhir dan bagaimana ia berakhir terakhir kali (`clean`, `crash`, `reboot`).
- `doctor`: hasil (`ok`, `warn`, `fail`) dari daftar pemeriksaan kesehatan yang tetap, kalau tersedia tanpa menjalankan ulang pemeriksaan (laporan dari bot dan dari terminal membiarkan field ini kosong).
- `flood`: keadaan batas Telegram, dirangkum dari semua chat: pemblokiran dalam 7 hari terakhir, apakah sekarang ada chat yang pengirimannya dimatikan, dalam mode minimal, atau sedang diperlambat, dan bagian anggaran per jam dari chat tersibuk sebagai rentang. Tanpa ID chat.

**Apa yang salah**
- `counters_24h`: berapa kali setiap kejadian dari daftar tetap terjadi dalam 24 jam terakhir (misalnya penjaga kartu kerja dimulai ulang, error Telegram jenis tertentu, hook mentok di batas memorinya, error jaringan atau disk). Hanya jumlah.
- `log_digest_24h`: untuk 15 lokasi di kode aipager yang paling sering menulis peringatan atau error ke log dalam 24 jam terakhir, lokasinya (`aipager/state.py:1234`), tingkatnya, dan berapa kali. Tidak pernah teks log-nya.
- `errors`: sampai 10 error yang dicatat aipager, masing-masing dengan:
  - `fingerprint`: nama bug berawalan `ap1-` (hash dari jenis error dan nama sampai 8 lokasi di kode aipager, tidak pernah teks atau data);
  - di mana terjadinya (aipager sendiri, hook, baris status, sebuah perintah, Mini App), bagaimana ia tertangkap, dan apakah itu bug atau hanya sesuatu yang tidak biasa (`tier`);
  - untuk error yang ditulis aipager ke log, nama logger-nya (modul `aipager`, `asyncio`, atau bagian dari `telegram.ext`);
  - nama jenis exception (misalnya `builtins.KeyError`, atau `<other>` untuk exception dari luar daftar paket tetap), jenis sampai tiga penyebabnya, dan nama nomor error seperti `ENOSPC`;
  - untuk error hook, event hook dan alatnya (alat bawaan dengan namanya, alat MCP apa pun sebagai `mcp`);
  - berapa kali, pada hari apa, dan di versi aipager mana error itu terjadi;
  - sampai 12 lokasi di kode aipager sendiri yang dilewatinya (file, baris, nama fungsi) dan sampai 8 nama paket luar dari daftar tetap yang dilewatinya.

**Tidak pernah disertakan**: chat, pesan, jawaban Claude, nama sesi, path folder atau file, token atau kunci, ID chat atau pengguna, username, nama komputer, teks pesan error apa pun, dan baris log. Field di atas tidak punya tempat untuk itu semua, dan laporan dicocokkan dengan daftar ini sebelum ditampilkan dan sebelum dikirim.

## <a id="where-it-goes"></a>Ke mana laporan dikirim

Laporan dikirim tanpa namamu ke kotak error pribadi milik orang yang merawat aipager di [Sentry](https://sentry.io): tanpa akun, tanpa ID instalasi. Seperti permintaan web lainnya, laporan datang dari alamat internet komputermu, yang terlihat oleh GitHub dan Sentry; kotak itu diatur untuk tidak menyimpannya (pengaturan Sentry “Prevent Storing of IP Addresses”). aipager mengirimnya sendiri dengan satu permintaan HTTPS (tidak memakai pustaka Sentry sendiri, yang mengumpulkan jauh lebih banyak): laporan yang tadi dipratinjau dikirim sebagai lampiran `report.json`, bersama ringkasan singkat untuk pengelompokan (versi, cara aipager dipasang dan apakah ini instalasi pengembangan, sistem, versi Python dan Claude Code, pemicu dan sidik jari, jenis dan lokasi di kode dari error pertama). Permintaan itu hanya menyebut `aipager/<version>`.

Setelah terkirim, kartu (atau terminal) menampilkan **kode referensi**: sidik jari `ap1-` dari error pertama, atau, untuk laporan tanpa error, ID event-nya yang 32 karakter. Kalau kamu juga membuka [issue di GitHub](https://github.com/dev-aly3n/aipager/issues), sebutkan kode referensi itu supaya orang yang merawat aipager bisa menemukan laporanmu. Jangan tempel log atau keluaran `aipager doctor` ke issue: isinya ada ID chat-mu, path folder home-mu, dan awal pesan-pesanmu.

Tepat sebelum mengirim, aipager membaca [report-endpoint.json](https://github.com/dev-aly3n/aipager/blob/main/report-endpoint.json) dari repo aipager di GitHub untuk tahu ke mana harus mengirim (aipager yang sedang berjalan menyimpan salinan yang baik selama sehari; pembacaan yang gagal dicoba lagi pada pengiriman berikutnya, dan setiap `aipager report` membacanya baru). Dengan begitu, orang yang merawat aipager bisa mengganti kunci Sentry, mematikan laporan untuk semua orang, atau menolak pengiriman dari versi di bawah nomor tertentu (build yang tidak tahu versinya sendiri dianggap lebih rendah) tanpa merilis versi baru. Kalau GitHub tidak bisa dijangkau atau file itu bukan jenis yang dipahami aipager, aipager memakai kunci yang tertanam di versi ini; file yang bisa dibaca dan berisi “mati” selalu dipatuhi.

## <a id="limits"></a>Batas

- Paling banyak 5 percobaan pengiriman per hari (UTC) dari satu komputer, manual dan otomatis digabung, dan percobaan yang gagal juga dihitung. Percobaan berikutnya akan bilang `Dari satu komputer paling banyak 5 laporan sehari. Coba lagi besok.`
- Tidak ada yang dicoba ulang di latar belakang: kalau pengiriman gagal, kartu menampilkan `Belum bisa mengirim sekarang. Tidak ada yang hilang; coba lagi nanti.` dan tetap menyisakan tombol Kirim.

## <a id="when-aipager-offers-a-report"></a>Kapan aipager menawarkan laporan

aipager memperhatikan bug-nya sendiri dan sesekali menawarkan untuk mengirim laporan tentangnya di chat pribadimu: `aipager mengalami error internal N kali (TypeError di kartu kerja). Dengan laporan, masalah ini lebih cepat diperbaiki. ...` dengan **Lihat laporan**, **Nanti saja**, dan **Jangan tanya lagi**. “Lihat laporan” membuka kartu pratinjau yang hanya berisi error-error ini; tidak ada yang dikirim sampai kamu mengetuk Kirim di sana.

Tawaran hanya datang kalau semua ini terpenuhi:
- Ini memang bug di aipager: error di kode aipager sendiri yang terjadi setidaknya dua kali dengan jarak setidaknya 10 menit, atau langsung kalau aipager crash saat berjalan sebagai layanan (`aipager start` di latar depan yang dimatikan, misalnya dengan menutup terminalnya, hanya dihitung). Error yang dilaporkan ke aipager oleh hook, program baris status, atau sebuah perintah (termasuk hook yang kehabisan memori) juga harus tersebar setidaknya selama sehari, jadi satu error seperti itu tidak pernah memicu tawaran; untuk itu ada tombol “Laporkan ini” di notifikasinya. Hal yang bukan kesalahan aipager (batas Telegram, jaringan, disk penuh) tidak pernah memicu tawaran; hal itu hanya dihitung untuk laporan yang kamu kirim sendiri.
- Paling banyak satu tawaran setiap 3 hari, dengan semua yang terkumpul di satu tawaran itu.
- Tidak dalam 48 jam pertama setelah aipager pertama kali dijalankan di komputer ini (pembaruan tidak mengulang hitungannya), dan tidak dalam satu jam pertama setelah aipager dimulai ulang.
- Hanya saat tidak ada yang berjalan dan tidak ada yang menunggu dikirim selama 2 menit, dan chat pribadimu dengan bot dipakai dalam 30 menit terakhir. Tidak pernah di grup, dan tidak pernah selama Telegram membatasi chat itu.
- Setiap bug ditawarkan sekali per versi aipager. Setelah jawaban apa pun, bug itu tidak ditawarkan lagi di versi itu; setelah pembaruan, bug itu bisa muncul lagi hanya kalau terjadi lagi.
- **Nanti saja** dan **Jangan tanya lagi** sama-sama dihitung sebagai “tidak”, begitu juga tawaran yang tidak dijawab dalam sehari. **Lihat laporan** mengulang hitungannya, meskipun kamu kemudian membatalkan. Dua “tidak” berturut-turut mematikan tawaran otomatis: kalau yang kedua berupa ketukan, sebuah baris akan memberitahumu; bagaimanapun juga, hal itu terlihat di `/settings`.
- Tidak pernah untuk instalasi pengembangan (aipager dipasang dari folder lokal, salinan git, atau dalam mode editable).

Untuk mematikan tawaran otomatis, buka `/settings` di chat pribadimu: **Laporan masalah: Mati**. Saat dua “tidak” mematikannya, halaman yang sama punya **Tawarkan lagi**. `reports.json` yang ada tapi tidak bisa dibaca juga mematikan tawaran otomatis (aipager tidak bisa lagi tahu jawaban sebelumnya); **Tawarkan lagi** juga memperbaiki ini. Di komputer tanpa chat untuk bertanya, atur `AIPAGER_REPORT_PROMPTS=0` di environment aipager. “Laporkan masalah” dan `aipager report` selalu berjalan, apa pun isi pengaturan ini.

## <a id="what-is-kept-on-your-machine"></a>Apa yang disimpan di komputermu

- `~/.local/state/aipager/reports.json`: error yang dicatat aipager (dalam bentuk seperti di atas: nama jenis, versi, lokasi di kode, paling banyak 50), jumlah per jam selama 8 hari, ringkasan log selama 2 hari, bagaimana berhenti-berhenti terakhir berakhir, dan keadaan tawaran (kapan tawaran terakhir, “tidak” berturut-turut, bug mana yang ditawarkan di versi mana). Hanya kamu yang bisa membacanya.
- `~/.local/state/aipager/report-sends.json`: berapa laporan yang dikirim atau dicoba hari ini.
- `~/.local/share/aipager/install.json`: kapan aipager pertama kali dijalankan di komputer ini (untuk 48 jam di atas).
- `~/.local/share/aipager/running.json`: ditulis selama aipager berjalan (nomor prosesnya, waktu mulainya, apakah berjalan sebagai layanan, dan ID boot sistem ini), untuk membedakan crash dari berhenti normal.

Tidak satu pun berisi chat, pesan, nama, path, atau token. `aipager uninstall` menyimpannya bersama data aipager lainnya supaya pemasangan ulang bisa melanjutkan dari tempat terakhir; perintah itu mencantumkan apa yang disimpannya dan mencetak perintah untuk menghapusnya (lihat [`aipager uninstall`](commands.md#command-line-aipager-uninstall)). Untuk menghapus hanya file-file ini, hapus keempat file itu: menghapus seluruh folder `~/.local/state/aipager` menghapus dua yang pertama (dan bersamanya keadaan bahasa, `languages.json`), tapi `install.json` dan `running.json` harus dihapus dengan namanya (jangan hapus `~/.local/share/aipager`, yang juga berisi folder sesi aipager).

## <a id="see-also"></a>Lihat juga

- [Pemecahan masalah](troubleshooting.md): kegagalan yang sering terjadi dan solusinya.
- [Model keamanan](security.md): apa yang disentuh aipager dan apa yang keluar ke jaringan.
- [Perintah bot](commands.md): `/help`, `/settings`, dan baris perintah.
