# Mode tim dan grup

<!-- i18n-source: docs/groups.md @ 6fc084f -->

> Halaman ini diterjemahkan dari bahasa Inggris; kalau keduanya berbeda, versi bahasa Inggris yang berlaku. ([English](../../groups.md))

Secara bawaan aipager adalah bot pribadi: kamu dan bot dalam satu chat pribadi, tidak ada orang lain. **Mode tim** membukanya untuk orang lain: grup Telegram tempat beberapa developer mengendalikan sesi, chat pribadi orang lain dengan bot, atau keduanya. Setiap chat punya anggotanya sendiri, dan setiap anggota punya peran yang menentukan apa yang boleh dilakukannya di sana: mengirim pesan, menjawab pertanyaan dan permintaan izin Claude, mengelola sesi, atau hanya melihat.

Mode tim bersifat opsional. Pemasangan yang hanya berisi chat pribadimu berjalan seperti biasa.

## <a id="decide-carefully"></a>Putuskan dengan hati-hati

Menambahkan seseorang dari Telegram ke sebuah chat berarti memberinya **hak menjalankan kode di komputer tempat aipager berjalan**. Tergantung perannya, orang itu bisa:

- mengirim pesan yang diubah Claude menjadi perintah shell, edit file, dan permintaan jaringan;
- menjawab permintaan izin (Izinkan / Tolak). `~/.claude/settings.json` milikmu tetap menentukan alat mana yang ditanyakan Claude, tapi siapa pun yang perannya boleh menyetujui bisa mengetuk Izinkan;
- memulai, menghentikan, mengakhiri, dan berpindah antarsesi.

Perlakukan daftar anggota seperti akses SSH ke komputer itu. Log audit ([`~/.claude/aipager-audit.jsonl`](security.md#audit-log)) mencatat siapa melakukan apa, tapi hanya setelah kejadian.

## <a id="setup"></a>Pengaturan

Semuanya diatur lewat `aipager config`. Kamu tidak pernah perlu mengedit file dengan tangan.

**Saat pertama dijalankan** (belum ada konfigurasi). Wizard bertanya dalam empat langkah:

1. token bot;
2. chat pribadimu sendiri dengan bot (dideteksi otomatis atau ditempel);
3. apakah akunmu `owner` (bawaannya ya; kalau kamu jawab tidak, kamu menjadi `admin` dan batas bawah keamanan tetap aktif untukmu);
4. ia memeriksa dependensi dan memasang hook Claude Code.

Ia mencatat chat pribadimu sebagai chat pertama. Ia tidak menanyakan mode bawaan: ringkasannya bertuliskan "New sessions start in Auto mode; owners and admins can choose Full access in Telegram under /settings → New sessions." (sesi baru dimulai dalam Mode Auto; pemilik dan admin bisa memilih Akses penuh di Telegram di /settings → Sesi baru). Lalu ia bertanya "Connected. Add a team group or other people now?" dengan Add a group, Add a person (DM), dan I'm done.

**Saat dijalankan berikutnya** membuka menu perubahan. Menu itu menampilkan chat-chatmu dan menawarkan:

- **Add a group scope**: grup baru beserta anggotanya (di bawah).
- **Add a DM scope**: chat pribadi orang lain dengan bot. Chat pribadi punya satu anggota; kamu memilih perannya (bawaannya `user`; pilih `owner` hanya untuk pemasangan satu orang, misalnya kontainer milik seorang teman). Untuk peran tanpa `can_manage` (seperti `user`), wizard lalu menanyakan [folder utama](#root-folders)-nya, dengan nilai bawaan sudah terisi (`~/aipager-users/<label>`). Untuk orang yang chat pribadinya sudah diatur, wizard mengatakannya dan tidak mengubah apa pun: untuk mengganti peran, pakai Edit a member.
- **Edit a scope**: untuk grup, **Add a member**; untuk chat mana pun, Rename, Edit scope deny_tools, **Set the members' root folder** (lihat [Folder utama](#root-folders)), dan Remove this scope (chat terakhir tidak bisa dihapus). Akhiri sesi-sesi sebuah chat sebelum menghapusnya: sesinya yang masih berjalan tetap menulis ke sana, dan tidak ada lagi chat yang bisa menjangkaunya dari Telegram (dari komputer, `aipager session ls` dan `aipager session kill <name>` tetap bisa).
- **Edit a member**: Set role, Edit member deny_tools, Remove member (bukan di chat pribadi, dan bukan anggota terakhir sebuah grup). Untuk pemilik atau admin (atau peran buatanmu dengan `bypass_role_denies`), Edit member deny_tools hanya memberi tahu bahwa alat yang diblokir tidak berlaku untuk peran itu.
- Test bot reachability, View policy, Re-install Claude Code hooks, dan Refresh bot token.

### <a id="adding-a-group"></a>Menambahkan grup

1. Tambahkan bot ke grup, lalu pilih Add a group scope (atau Add a group saat pertama kali dijalankan).
2. **ID grup.** Pilih deteksi otomatis dan kirim `/start` di grup, atau tempel ID-nya (angka negatif). Wizard mengirim pesan tes dan menanyakan apakah pesan itu sampai. Grup yang sudah diatur berhenti di sini dengan "This group is already set up. Use Edit a scope → Add a member.", dan tidak ada yang berubah.
3. **Nama** grup (ditampilkan di wizard dan di Mini App).
4. **Kamu dulu.** Kalau kamu pemilik chat pribadimu, wizard menawarkan untuk menambahkanmu sebagai anggota pertama grup dengan peran `owner` (bawaannya ya; bisa dilewati). Tanpa pemilik atau admin di grup, tidak ada yang bisa memakai Akses penuh, `/settings`, atau `/update` di sana.
5. **Anggota lain**, satu per satu: cara menemukan mereka (deteksi otomatis, atau tempel ID atau `@handle`), nama (ditampilkan di chat sebagai `@label`), dan peran (bawaannya `user`). Daftar peran mencantumkan `owner` dengan peringatan: pemilik punya kendali penuh atas komputer ini, termasuk konfigurasi aipager, token bot, dan kredensial Claude. Berikan hanya untuk dirimu sendiri. Berikan `admin` kepada mereka yang perlu memakai Akses penuh, `/settings`, dan `/update` di grup. (Di chat pribadinya sendiri dengan bot, siapa pun yang bisa mengirim pesan dan menjawab Claude bisa memakai Akses penuh untuk sesinya sendiri, apa pun perannya.)
6. **Satu aturan opsional:** "Block file edits (Write, Edit) for `user` members in this group? Admins and the owner are not affected." (Blokir edit file (Write, Edit) untuk anggota `user` di grup ini? Admin dan pemilik tidak terpengaruh.) Bawaannya tidak: `user` memang hanya menulis di folder proyek sesi. Dengan ya, setiap Write dan Edit dalam tugas seorang anggota yang perannya tidak melewati aturan peran (`user`, `read_only`, dan peran yang kamu definisikan tanpa `bypass_role_denies`) ditolak tanpa bertanya, dan tugasnya berhenti. Pekerjaan itu lalu harus dikirim oleh admin atau pemilik.
7. **Folder utama para anggota**, dengan `~/aipager-users/group-<label>` sudah terisi: tempat anggota `user` dan `read_only` grup itu memulai sesi dan bekerja (lihat [Folder utama](#root-folders)).

Untuk menambahkan seseorang ke grup yang sudah diatur, pakai Edit a scope → grup → Add a member. Semua orang yang sudah ada di grup mempertahankan tempat dan perannya.

### <a id="finding-ids-auto-detect"></a>Mencari ID (deteksi otomatis)

Deteksi otomatis anggota mengambil pesan terbaru dari seseorang yang belum ditambahkan dan bertanya "Is this the person to add?" (Ini orang yang mau ditambahkan?) sebelum memakainya (jawab tidak dan ia terus mencari). Sebagai nama, ia menyarankan username Telegram orang itu. Di grup, orang itu harus menyapa bot: menyebutnya, atau mengirim `/start` (lihat [apa yang dibaca bot](#what-the-bot-reads-in-a-group)). Wizard mengatakan di mana ia mencari:

- **"Watching through the running daemon"** (lewat aipager yang sedang berjalan): selama `aipager start` berjalan, aipager menerima setiap pesan untuk bot, jadi wizard membaca apa yang dicatat aipager di `~/.claude/aipager-pending-users.json`. aipager mencatat siapa yang menyapa bot di grup yang belum dilayaninya (ID, judul, dan jenis grup, serta ID, username, dan nama orang itu), dan setiap orang yang bukan anggota grup yang dilayaninya. Di grup yang tidak dilayaninya, ia tidak pernah membalas. Pesan pribadi dari seseorang yang belum diatur mendapat balasan dan tidak dicatat, jadi untuk menambahkan chat pribadi seseorang selama aipager berjalan, tempel ID-nya (atau hentikan aipager dulu).
- **"Watching Telegram directly"** (langsung di Telegram): saat aipager dihentikan, wizard sendiri yang menanyakan pesan terbaru bot ke Telegram.

Chat pribadimu sendiri saat pertama dijalankan selalu dibaca dari Telegram. Pesan dari admin grup anonim, atau pesan yang diterbitkan atas nama grup atau kanal, tidak dihitung: ID pengirimnya (1087968824, 136817688, atau 777000) sama untuk semua yang menerbitkan dengan cara itu, jadi wizard menolaknya, baik diketik maupun ditemukan. Minta orang itu menulis dari akunnya sendiri.

### <a id="where-the-config-lives"></a>Di mana konfigurasinya

Wizard menulis `~/.config/aipager/aipager.yaml` (izin 0600): token bot, chat yang dilayani bot, dan anggota setiap chat dengan perannya. Apa yang boleh dilakukan setiap peran ada di `~/.config/aipager/policy.yaml`, dan itu file milikmu: wizard tidak pernah menulisnya (lihat [Cara kerja aturan](#how-rules-work)). Pemasangan yang diatur dengan format `team.yaml` lama dipindahkan otomatis: oleh aipager saat mulai, atau oleh wizard saat dijalankan berikutnya.

### <a id="live-reload"></a>Muat ulang tanpa mulai ulang

Setelah setiap perubahan chat atau anggota dari menu perubahan (menambah atau menghapus grup atau chat pribadi, menambah atau menghapus anggota, mengatur peran, mengubah `deny_tools`, mengganti nama chat), dan juga setelah grup atau orang yang ditambahkan di akhir saat pertama dijalankan, atau grup setengah jadi yang kamu lanjutkan (satu muat ulang untuk semua yang ditambahkan di sana), `aipager config` mengirim muat ulang langsung (**SIGUSR1**) ke aipager yang sedang berjalan dan menulis "Scopes reloaded live (no daemon restart needed)". Muat ulang membaca lagi `aipager.yaml` dan `policy.yaml`. Kalau konfigurasi di disk akan ditolak, wizard menulis "Not applied" dengan alasannya, dan aipager tetap memakai konfigurasi sebelumnya sampai kamu memperbaikinya. Kalau wizard tidak bisa memastikan proses mana yang merupakan aipager, ia memintamu memulai ulangnya.

Yang langsung diterapkan muat ulang:

- siapa yang bisa memakai bot di setiap chat, dengan peran dan aturan apa;
- grup atau chat pribadi baru mulai menerima pesan dan mendapat menu `/`-nya; yang dihapus berhenti (sesinya tetap berjalan sampai kamu mengakhirinya);
- pesan yang ditahan (yang menunggu jawaban sebuah permintaan atau selesainya tugas orang lain) dari seseorang yang tidak lagi boleh mengirim pesan di chat itu dibuang: pesan itu mendapat 🤷 dan bot membalas “Tidak dikirim ke Claude: @bob sudah tidak boleh mengirim pesan di sini.”;
- tugas yang sedang berjalan milik orang yang dihapus atau kehilangan hak, sampai tugas itu selesai, hanya mempertahankan yang paling ketat dari aturan lama dan aturan barunya (untuk orang yang dihapus, batas bawah yang dibatasi). Tugas yang diketik di terminal tidak berubah, kecuali ada pesan Telegram yang bergabung ke dalamnya: aipager tidak mencatat dari siapa, jadi tugas seperti itu menyempit saat pemilik mana pun di chat itu (atau, di chat pribadi, anggotanya) kehilangan hak. Tugas yang sudah berjalan saat aipager dimulai ulang (masih bekerja, atau menunggu pertanyaan atau izin) menyempit untuk setiap anggota chat-nya yang kehilangan hak. Kenaikan hak tidak pernah melebarkan tugas yang sedang berjalan: hak baru berlaku dari tugas berikutnya orang itu.

Hanya **token bot** baru yang butuh mulai ulang (`aipager config` mengatakannya setelah Refresh bot token). Saat aipager tidak berjalan, tidak ada muat ulang yang dikirim: `aipager start` berikutnya membaca konfigurasi baru.

Untuk memuat ulang dengan tangan (misalnya setelah mengedit `policy.yaml`), kirim sinyal hanya ke proses aipager. Di bawah layanan latar belakang:

```sh
pid=$(systemctl --user show -p MainPID --value aipager.service)
[ "${pid:-0}" -gt 0 ] && kill -USR1 "$pid"
```

(Pemeriksaan ini penting: saat layanan tidak berjalan, `MainPID` bernilai `0`, dan `kill -USR1 0` akan mengirim sinyal ke proses shell-mu sendiri.)

Untuk aipager yang kamu jalankan sendiri dengan `aipager start`, kirim `kill -USR1 <pid>` ke proses itu. Jangan kirim sinyal ke semua yang ditemukan `pgrep -f 'aipager start'` (shell atau kontainer tempat aipager berjalan juga cocok, dan SIGUSR1 menghentikan proses yang tidak menanganinya), dan jangan pakai `systemctl --user kill` tanpa `--kill-whom=main` (sesi-sesi layanan di grupnya juga akan dihentikan).

Kalau saat muat ulang `aipager.yaml` atau `policy.yaml` rusak, atau `aipager.yaml` tidak ada, aipager mencatat peringatan di log dan tetap memakai konfigurasi sebelumnya di memori. Salah ketik atau file yang disisihkan tidak bisa mengunci kamu di luar maupun membiarkan orang masuk: muat ulang tidak pernah mengembalikan aipager yang chat-nya sudah diatur ke mode pribadi (hanya mulai ulang yang melakukannya).

### <a id="bot-admin-rights-and-privacy-mode"></a>Hak admin bot dan mode privasi

**Jadikan bot admin grup dengan hak menyematkan pesan.** Bot butuh ini untuk menyematkan bilah status grup (lihat [Yang dilihat semua orang](#what-everyone-sees)). Tanpanya grup tetap berjalan, tapi tidak punya bilah status; kalau hak itu kamu berikan nanti, mulai ulang aipager supaya bilahnya muncul.

Bot yang menjadi admin grup menerima setiap pesan di grup, apa pun mode privasinya di BotFather. Ini aman: aipager hanya memproses pesan yang tercantum di [apa yang dibaca bot](#what-the-bot-reads-in-a-group) dan mengabaikan sisanya, jadi obrolan di grup tidak pernah masuk ke sesi. Bot yang bukan admin secara bawaan ada dalam mode privasi (biarkan menyala), dan Telegram lalu hanya meneruskan sebagian pesan grup kepadanya; kalau salah satu pesanmu tidak sampai, balas salah satu pesan bot atau pakai sebuah perintah. Pengaturan privasi tidak mengubah apa pun pada yang diproses aipager.

Menjadi admin grup di Telegram tidak memberi seseorang apa pun di aipager. Siapa boleh melakukan apa hanya ditentukan oleh peran di `aipager.yaml`.

### <a id="when-telegram-upgrades-the-group"></a>Saat Telegram meng-upgrade grup

Telegram mengubah grup biasa menjadi supergroup saat grup itu dibuat publik, topik dinyalakan, anggotanya melewati 200, atau beberapa hak admin berubah, dan upgrade itu memberi grup ID chat baru. aipager mengikutinya sendiri: saat melihat upgrade itu (notifikasi Telegram di grup, atau pengiriman pertama ke ID lama), ia memindahkan entri grup di `aipager.yaml` ke ID baru, memindahkan sesi, alamat pesan, dan `/settings` grup bersamanya, lalu menulis satu baris ke grup: “Telegram meng-upgrade grup ini. aipager ikut pindah; kamu tidak perlu melakukan apa pun.” Bilah status yang disematkan mulai dari awal lagi di grup yang sudah di-upgrade. Log aipager punya peringatan berisi kedua ID itu.

Nama sesi di dalam aipager tetap memakai ID lama (kamu tidak pernah melihatnya), dan `aipager.yaml` menyimpan catatan singkat `chat_migrations` tentang perpindahan itu, supaya sesi yang dimulai sebelum upgrade tetap menjadi milik grup yang sudah di-upgrade, bahkan setelah mulai ulang. Nama internal sesi dengan ID lama (`/name__g<old id>`) berfungsi di grup yang sudah di-upgrade dan tidak di tempat lain. Kalau aipager sedang berhenti saat upgrade, ia mengetahuinya pada start berikutnya. Tugas yang sedang berjalan saat upgrade tetap selesai dan jawabannya sampai ke grup yang sudah di-upgrade, tapi kartu progresnya tidak ditampilkan lagi sampai akhir tugas itu. Kalau ID baru sudah menjadi chat terpisah di konfigurasi, tidak ada yang dipindahkan dan log-nya mengatakan itu; hapus salah satu dari keduanya lewat `aipager config`. “Test bot reachability” di `aipager config` menampilkan ID baru saat menemukan grup yang sudah di-upgrade.

Setiap kali mulai, aipager memeriksa setiap grup sekali. Kalau Telegram menjawab bahwa grup tidak ditemukan atau bot tidak bisa memakainya (grupnya dihapus, botnya dikeluarkan, atau grupnya di-upgrade saat aipager berhenti tanpa Telegram memberi tahu ke ID mana), log aipager punya satu peringatan berisi nama chat-nya: periksa bahwa bot masih ada di grup itu, dan kalau grupnya di-upgrade, tambahkan lagi grupnya di `aipager config` dengan Add a group scope (grup itu mendapat ID barunya; tambahkan lagi anggotanya), lalu hapus yang lama dengan Edit a scope → Remove this scope (ID lama tidak berfungsi lagi). aipager tidak menerbitkan apa pun dan tidak mencoba lagi.

### <a id="forum-topics-are-not-supported"></a>Topik forum tidak didukung

Pakai satu grup biasa (tanpa topik) per tim. Di grup dengan topik yang menyala, aipager tetap berjalan, tapi tidak tetap di dalam topik:

- balasan ke perintah atau pesan (misalnya `/status` atau jawaban ke “Sesi yang mana?”) tetap di topik tempat pesan itu dikirim;
- notifikasi, kartu kerja, jawaban, permintaan izin, dan bilah status yang disematkan diterbitkan ke grup tanpa topik, jadi bisa masuk ke General;
- setiap orang punya satu sesi saat ini untuk seluruh grup, bukan satu per topik.

Menyalakan topik juga mengubah grup biasa menjadi supergroup, yang diikuti aipager (di atas).

## <a id="roles"></a>Peran

Empat peran bawaan (lihat `aipager/safety.py`). Yang dihitung adalah peran seseorang **di chat tempat ia bertindak**. Tidak ada yang bisa mengubah anggota atau peran dari Telegram atau Mini App: hanya `aipager config` di komputer yang melakukannya, dan dengan begitu hal itu tetap di tangan pemilik.

| | `owner` | `admin` | `user` | `read_only` |
|---|---|---|---|---|
| Mengirim pesan (`can_prompt`) | ✅ | ✅ | ✅ | ❌ |
| Menjawab Claude: Izinkan, Tolak, pertanyaan (`can_approve`) | ✅ | ✅ | ✅ | ❌ |
| Mengelola: Akses penuh, `/settings` grup, `/update`, pemasangan pesan suara (`can_manage`) | ✅ | ✅ | ❌ (Akses penuh: ✅ di chat pribadinya sendiri) | ❌ |
| Mengubah anggota dan peran dari Telegram atau Mini App | ❌ | ❌ | ❌ | ❌ |
| Melewati aturan larangan peran (`bypass_role_denies`) | ✅ | ✅ | ❌ | ❌ |
| Melewati batas bawah keamanan (`bypass_safety`) | ✅ | ❌ | ❌ | ❌ |
| Bash dan alat lain yang menjalankan kode | ✅ | ✅ (aturan sebisanya) | ❌ | ❌ |
| Perintah slash apa pun | ✅ | ✅ | hanya perintah keyboard “Perintah”, `/compact`, dan penggantian model | seperti `user` |
| Tempat sesi dimulai (`/new`, Lanjutkan, Mulai ulang, pergantian mode) | di mana saja | di mana saja | hanya di dalam [folder utama](#root-folders) chat itu | seperti `user` |
| Tempat penulisan file berakhir | di mana saja | di mana pun yang diizinkan batas bawah keamanan | folder proyek sesi (saat ada di dalam folder utama) dan scratchpad-nya, tidak pernah folder home | seperti `user` |
| Membaca di folder home-mu | di mana pun yang diizinkan batas bawah keamanan | di mana pun yang diizinkan batas bawah keamanan | hanya di dalam folder utama (folder lain di komputer seperti biasa, folder utama anggota lain tidak pernah) | seperti `user` |
| File kredensial (`~/.ssh`, `~/.git-credentials`, ...) | dibaca | dibaca | tidak pernah (bawaan) | tidak pernah (bawaan) |
| `Grep`/`Glob` | di mana saja | di mana saja kecuali folder aipager yang dilindungi (bukan di folder yang merupakan, ada di dalam, atau memuat folder itu) | folder proyek sesi dan scratchpad-nya | seperti `user` |

- **owner**: kendali penuh, termasuk atas batas bawah keamanan bawaan. Pemiliknya harus tepat satu: orang yang mengelola komputer itu.
- **admin**: semua yang bisa dilakukan pemilik di Telegram dan Mini App: mengalihkan sesi ke Mode Akses penuh (dan memulai di sana), melanjutkan sesi dalam Akses penuh, mengubah `/settings` grup, menjalankan `/update`, memasang fitur pesan suara, mengirim perintah slash apa pun. Admin juga melewati aturan larangan peran (`deny_tools` chat dan anggota tidak berlaku untuk admin). Dua hal tetap milik pemilik:
  - mengubah anggota dan peran: hanya `aipager config` di komputer yang melakukannya, dan tidak ada apa pun di Telegram atau Mini App yang bisa;
  - melewati batas bawah keamanan (konfigurasi dan token bot aipager, kredensial Claude Code, file kontrol aipager). `Grep` dan `Glob` admin bisa mencari di mana saja selain itu, tapi tidak di folder yang merupakan salah satunya, ada di dalam salah satunya, atau memuat salah satunya: `Grep` atas `~`, `/`, atau `/tmp` (atau tanpa folder dari sesi di folder home) ditolak, begitu juga `Glob` yang polanya mulai di salah satunya (seperti `/home/you/.config/aipager/*`). Cari di dalam folder proyek saja. Di versi Claude Code tanpa alat `Grep`, Claude mencari lewat Bash; di sana pencarian atau pendaftaran rekursif atas seluruh folder home atau `/` (`grep -rn x ~`, `rg x $HOME`, `find / -name x`) juga ditolak, sebisanya (pencarian tanpa folder, atau dengan `..`, yang dijalankan dari folder home atau di bawahnya tidak terlihat), sedangkan mencari di proyek tidak, termasuk mencari path home (`grep -rn "$HOME" src`) ([keamanan](security.md#team-mode-enforcement)).

  Admin tetap punya Bash, dan dengan shell, pola perintah batas bawah bekerja sebisanya, bukan batas: jadikan seseorang admin hanya kalau kamu memercayainya dengan shell di komputer ini.
- **user**: aturan larangan berlaku. Tanpa Bash dan tanpa alat lain yang menjalankan kode (PowerShell, Monitor, pesan terjadwal, workflow; daftarnya `safety.CODE_EXECUTION_TOOLS`), tanpa `SendMessage` ke sesi lain, tanpa pergantian worktree. File hanya ditulis ke folder sesi dan scratchpad Claude Code-nya, dan tidak pernah ke `.claude/`, `.git/`, atau `.mcp.json` di folder itu (masing-masing menjalankan perintah). `Grep` dan `Glob` hanya mencari di folder-folder itu, dengan glob relatif sederhana. `user` memulai, melanjutkan, memulai ulang, dan mengalihkan sesi hanya di dalam [folder utama](#root-folders) chat itu, dan tidak membaca apa pun di folder home-mu di luarnya. Folder sesi yang merupakan folder home-mu, `/`, atau folder di atasnya, atau yang ada di luar folder utama (sesi yang dimulai sebelum folder utama ada, atau oleh orang lain), tidak dihitung: di sana `user` hanya bisa menulis dan mencari di scratchpad. Membaca mengikuti aturan path yang dilindungi, dan file kredensialmu tidak pernah dibaca: `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config/gh`, `~/.git-credentials`, `~/.netrc`, `~/.docker/config.json`, `~/.kube`, `~/.config/gcloud`, `~/.azure`, `~/.password-store`, `~/.pgpass`, `~/.npmrc`, dan `~/.pypirc`. Perintah slash: hanya perintah keyboard “Perintah”, `/compact`, dan penggantian model (lihat [Dengan hak siapa sebuah pesan berjalan](#who-a-message-runs-as)).
- **read_only**: pengamat. Mereka melihat semua yang ada di grup dan bisa memakai `/status`, `/diff`, `/whoami`, `/help`, `/start`, dan `/app`, melihat `/settings`, dan menelusuri daftar `/resume` yang dibuka seseorang. Pesan teks, pesan suara, dan file milik mereka sendiri tidak dikirim ke mana pun. Hal pertama yang mereka kirim di sebuah chat mendapat balasan bahwa peran mereka `read_only`; setelah itu bot diam bagi mereka di chat itu (tanpa balasan, tanpa reaksi) sampai aipager dimulai ulang. Mereka punya batasan alat dan penulisan yang sama dengan `user`, kalau-kalau salah satu pesan mereka tetap sampai ke sebuah sesi.

### <a id="custom-roles"></a>Peran buatan sendiri

`policy.yaml` bisa mengubah field apa pun dari peran bawaan, atau mendefinisikan peran baru. Field yang kamu atur menggantikan nilai bawaannya. Field-nya:

- `can_prompt`, `can_approve`: mengirim pesan; menjawab Claude.
- `can_manage`: memakai Akses penuh, mengubah `/settings` grup, menjalankan `/update`, dan memasang fitur pesan suara (bawaan: `owner` dan `admin` true, `user` dan `read_only` false). Field ini tidak pernah membolehkan mengubah anggota atau peran, dan tidak pernah melewati batas bawah keamanan.
- `bypass_role_denies`, `bypass_safety`: lihat tabel.
- `deny_tools`, `allow_tools`, `deny_bash_patterns`, `deny_paths_no_access`, `deny_paths_no_write`: lihat [Cara kerja aturan](#how-rules-work).

Peran yang didefinisikan dari nol dimulai dengan `can_prompt` dan `can_approve` menyala, `can_manage` dan kedua pengecualian mati, file kredensial di `deny_paths_no_access`-nya, dan **tanpa alat yang dilarang, artinya dengan Bash**, sampai kamu mengatur `deny_tools` (`aipager doctor` memperingatkan sekali saat seorang anggota memakai peran itu). Batas penulisan dan pencarian berlaku untuk setiap peran tanpa `bypass_safety` atau `bypass_role_denies`. [Folder utama](#root-folders) berlaku untuk setiap peran tanpa `can_manage`, apa pun pengecualiannya: peran yang kamu definisikan dengan `bypass_role_denies` atau `bypass_safety` tapi tanpa `can_manage` menulis dan mencari di mana saja di dalam folder utamanya (dan scratchpad-nya), tidak membaca apa pun di folder home-mu di luarnya, dan tidak di tempat lain (dengan `bypass_safety`, pada sesi yang berjalan di luar folder utamanya, peran itu hanya menulis ke scratchpad-nya). Yang tanpa batasan hanya pemilik dan admin.

### <a id="root-folders"></a>Folder utama

Setiap chat punya folder utama untuk anggota: folder tempat anggota yang perannya di sana tidak punya `can_manage` (`user` dan `read_only` bawaan, dan peran yang kamu definisikan tanpanya) memulai dan menyimpan sesi mereka. Folder ini tidak menahan pemilik dan admin. Folder utama chat pribadi milik orang itu; folder utama grup dipakai bersama oleh anggota terbatas grup itu. Dua chat tidak pernah berbagi satu folder, kecuali kamu sengaja memberi keduanya folder yang sama (wizard memintamu mengonfirmasinya).

- **Cara mengaturnya.** `aipager config` menanyakannya saat kamu menambahkan grup atau chat pribadi untuk peran tanpa `can_manage`, dengan nilai bawaan sudah terisi, dan Edit a scope → **Set the members' root folder** mengubahnya (langsung berlaku, lewat muat ulang langsung). Konfigurasi saat ini menampilkan folder utama setiap chat, dengan `(default)` kalau belum diatur.
- **Bawaannya** `~/aipager-users/<label>` untuk chat pribadi (nama orangnya) dan `~/aipager-users/group-<label>` untuk grup. Namanya dibuat huruf kecil, dan apa pun selain huruf, angka, `.`, `_`, dan `-` menjadi `-`; kalau dua chat akan mendapat folder yang sama, ID chat masing-masing ditambahkan (`sara-123456`). Folder itu (dan `~/aipager-users` kalau baru) dibuat dengan izin 0700, supaya hanya kamu yang bisa membukanya, saat seorang anggota pertama kali membuka `/new` atau formulir “Sesi baru” di Mini App. Nilai bawaan tidak pernah berpindah: saat sebuah perubahan di `aipager config` (folder utama chat lain, chat yang dihapus, nama baru) akan mengubah folder bawaan sebuah chat, wizard mencatat folder yang dimilikinya sebagai folder utama chat itu.
- **Yang ditolak wizard**: path relatif, wildcard, `/`, folder home-mu atau folder di atasnya, folder yang merupakan, ada di dalam, atau memuat salah satu folder aipager yang dilindungi atau file kredensial (`~/.claude`, `~/.config/aipager`, `~/.config`, `~/.ssh`, ...), dan folder sementara Claude Code (`/tmp/claude-*`, tempat scratchpad setiap sesi berada). Folder yang belum ada tidak apa-apa. Wizard meminta konfirmasi untuk folder tempat sistemmu menjalankan program (`~/bin`, `~/.local/bin`, `~/.config/systemd`, `~/.config/autostart`, atau folder di dalamnya).
- **Memulai sesi.** Halaman “Folder” di `/new` dan di Mini App menampilkan folder utama dan folder tepat di dalamnya (bukan yang tersembunyi, dan bukan tautan yang mengarah keluar), tanpa baris “Folder bawaan”; sesi baru dimulai di folder utama kecuali kamu memilih salah satunya. “Folder baru” membuat folder di dalam yang dipilih. Semua yang lain mendapat “Peranmu hanya bisa memulai sesi di foldermu ~/... atau folder di dalamnya.”
- **Sesi di luarnya.** Melanjutkan, memulai ulang, Ganti, dan mengganti mode sesi yang foldernya di luar folder utama mendapat “Sesi ini bekerja di luar foldermu ~/..., jadi peranmu tidak boleh melanjutkan, memulai ulang, atau mengganti mode-nya. Minta bantuan admin.” Pesan ke sesi seperti itu yang sedang berjalan tetap terkirim, dan hook keamanan menahan tugasnya: di sana tugas itu hanya menulis ke scratchpad.
- **Apa yang ditahan hook pada tugas.** Tugas anggota yang dibatasi hanya menulis ke folder sesi (saat ada di dalam folder utama) dan scratchpad-nya, tidak membaca dan tidak menjalankan `Grep` atau `Glob` atas apa pun di folder home-mu di luar folder utama (kecuali scratchpad dan file balasannya sendiri), dan tidak pernah menyentuh folder utama chat lain, di mana pun letaknya. Folder di luar folder home-mu (`/usr`, `/etc`, ...) tetap bisa dibaca, folder yang dilindungi tetap terlarang, dan `Bash` untuk `user` tetap dilarang. Saat dua anggota dengan folder utama berbeda bercampur dalam satu tugas, yang lebih sempit menang; dua folder yang tidak saling bersarang hanya menyisakan folder sesi itu sendiri.

Untuk mengembalikan Bash ke `user` (hanya untuk orang yang kamu percayai dengan shell), sebutkan di `allow_tools` (yang lalu juga menjadi daftar yang diizinkan untuk peran itu) atau ganti `deny_tools`-nya:

```yaml
roles:
  user:
    # daftar bawaan tanpa Bash
    deny_tools: [PowerShell, Monitor, REPL, Workflow, CronCreate,
                 RemoteTrigger, AppifactRepl, self_hosted_runner_spawn_local,
                 self_hosted_runner_requeue_session, SendMessage,
                 EnterWorktree, ExitWorktree]
```

Setelah itu `aipager doctor` memperingatkan: `role user has Bash: its safety rules are best-effort, not a boundary`. Kalau `policy.yaml`-mu sudah mengatur `deny_tools` untuk `user` sebelum nilai bawaan ini ada, daftarmu tetap menggantikannya, jadi peran itu punya Bash sampai kamu menambahkannya lagi; peringatan doctor akan mengatakan itu. Tentang apa yang tidak dicakup batasan-batasan ini, lihat [keamanan → cara aturan diterapkan di tim](security.md#team-mode-enforcement).

## <a id="talking-to-the-bot-in-a-group"></a>Bicara dengan bot di grup

### <a id="what-the-bot-reads-in-a-group"></a>Apa yang dibaca bot di grup

Di grup, aipager hanya memproses pesan yang merupakan:

- **sebuah perintah**: `/status`, `/x1 fix the tests`, atau yang sama dengan `@aipagerbot` setelah perintahnya (`/x1@aipagerbot`), yang ditambahkan Telegram saat kamu memilihnya dari menu. Perintah untuk bot lain (`/x1@otherbot`) diabaikan;
- **balasan ke salah satu pesan bot**;
- **pesan yang menyebut bot**: `@aipagerbot fix the tests`;
- **ketukan pada keyboard bot** di grup. Di grup, setiap tombol keyboard diawali kotak kecil (`▫️ stop`, `▫️ Hapus percakapan`), dan hanya teks bertanda itu yang dihitung sebagai ketukan. Mengetik kata itu begitu saja adalah obrolan biasa: anggota yang mengetik `stop`, `Hapus percakapan`, `Opus`, atau nama sesi tidak mengubah apa pun. Untuk sengaja memakai kata keyboard tanpa keyboard, sapa bot: `@aipagerbot stop`, atau `stop` sebagai balasan ke salah satu pesannya.

Semua yang lain (anggota mengobrol satu sama lain, foto atau pesan suara yang bukan balasan ke bot) tidak mendapat reaksi maupun balasan. Sama saja, apakah bot itu admin grup atau bukan. Pesan yang diedit diabaikan di mana pun: edit sebuah pesan dan tidak terjadi apa-apa; kirim lagi.

Foto, file, atau pesan suara bekerja dengan cara yang sama: kirim sebagai balasan ke bot, atau sebut bot di keterangannya. Untuk album, taruh keterangannya di item pertama: item album yang datang setelah item yang membawa keterangan, sebutan, atau balasan diambil, dan yang sebelumnya tidak.

**Sebutan dihapus.** `@aipagerbot fix the tests` sampai ke Claude sebagai `fix the tests` (Claude Code akan membaca `@word` sebagai file). `@aipagerbot status` dan `@aipagerbot x2` bekerja seperti mengetik `status` atau `x2` di chat pribadi, dan `@aipagerbot` saja menunjukkan ke mana pesanmu pergi. Perintah sesi setelah sebutan bekerja (`@aipagerbot /x1 fix it`), tapi perintah milik aipager sendiri tidak: `@aipagerbot /stop` mendapat “Kirim /stop sendirian, tanpa menyebut bot.”

**Hanya anggota.** Orang yang bukan anggota grup mendapat satu balasan (“🚫 Kamu tidak diizinkan memakai bot di chat ini.”) beserta ID Telegram-nya untuk diteruskan kepadamu, dan dicatat untuk `aipager config`; setelah itu bot mengabaikannya sampai aipager dimulai ulang. Anggota `read_only` diberi tahu perannya sekali (lihat [Peran](#roles)).

**Admin anonim harus menulis dari akunnya sendiri.** Admin grup yang menyalakan “Remain anonymous”, atau pesan yang diterbitkan atas nama grup atau kanal, tidak memberi tahu siapa penulisnya: Telegram menampilkan pengirim yang sama untuk semua admin anonim. aipager tidak bisa memberi pesan seperti itu peran siapa pun, jadi ia membalas “Kirim pesan atas namamu sendiri untuk memakai bot.” (sekali per chat, sampai aipager dimulai ulang) dan tidak melakukan hal lain. Pesan itu tidak pernah dicatat sebagai pengguna yang menunggu, dan wizard menolak ID bersama itu. Matikan “Remain anonymous” untuk dirimu di pengaturan admin grup, atau tulis dari akunmu sendiri.

### <a id="talking-to-a-session-in-a-group"></a>Bicara dengan sesi di grup

Balas salah satu pesan sebuah sesi (jawabannya, kartunya) dan pesanmu masuk ke sesi itu. Atau kirim `/x1 pesanmu`, atau keterangan yang diawali `/x1`.

**Setiap orang punya sesinya sendiri saat ini**: sesi yang terakhir ia pilih (`/x1`), kirimi pesan, balas, atau mulai. Jawaban dari sesi anggota lain tidak mengubahnya, begitu juga pesan orang lain. Sebutan, pesan suara, templat, atau perintah keyboard masuk ke sesimu sendiri saat ini, dan perintah keyboard atau templat menyebut sesi itu di balasannya (“🧹 /clear dikirim ke x1”). `/stop`, `/mode`, `/kill`, `/restart`, `/rename`, `/delete`, atau `/diff` tanpa argumen hanya bekerja pada sesimu sendiri saat ini (dengan konfirmasi biasa) dan hanya kalau perintah itu masuk akal untuknya (sedang bekerja untuk `/stop`, sudah berakhir untuk `/delete`); kalau tidak, selalu menampilkan daftar, bahkan saat tinggal satu sesi, karena sesi itu bisa milik orang lain. `/clearqueue` dan `/now` bekerja pada sesimu saat ini. `/kill x1` dan perintah lain dengan nama bekerja pada sesi itu, seperti biasa.

- Kalau kamu belum punya sesi saat ini dan lebih dari satu sesi sedang berjalan, bot bertanya “Sesi yang mana? Balas salah satu pesannya, atau kirim /x1 beserta pesanmu.” dengan satu tombol per sesi, dan tidak mengirim apa pun. Kalau hanya satu yang berjalan, pesannya masuk ke sana.
- Kalau sesimu sendiri saat ini sudah berakhir, pesannya tidak masuk ke satu-satunya sesi yang masih berjalan (sesi itu bisa milik orang lain): bot membalas “x1 sudah berakhir. Sesi yang mana?” dengan satu tombol per sesi yang berjalan, dan, kalau x1 bisa dilanjutkan, “▶️ Lanjutkan x1”, dan tidak mengirim apa pun.
- Mengetuk tombol sesi di salah satu pertanyaan ini juga mengirim pesan yang dimaksud (teks, transkrip pesan suara, atau file) ke sesi itu, seolah-olah dikirim ke sana, dan pertanyaannya berubah menjadi “Dikirim ke x2.”. Hanya orang yang mengirim pesan itu yang bisa memakai tombolnya, dan hanya selama sepuluh menit dan sebelum aipager dimulai ulang: setelah itu tombolnya hanya menjadikan sesi itu sesimu saat ini (“Pesan itu sudah terlalu lama dan tidak dikirim; kirim lagi.”). ▶️ Lanjutkan mengembalikan x1 tapi tidak mengirim apa pun: kirim lagi pesanmu.
- Di grup, bilah yang disematkan tidak punya baris “Pesan masuk ke”, karena setiap orang punya miliknya sendiri.

**Kartu yang menunggu balasan milik satu orang.** Kartu sesi baru bilang “Balas pesan ini dengan sebuah nama, dan kalau mau, apa yang dikerjakan pertama:”, pertanyaan ganti nama “… Balas pesan ini dengan nama baru.”. Di grup, setiap kartu menunjukkan milik siapa (“🆕 Sesi baru (@alice)”), hanya orang itu yang bisa membalasnya, dan dua orang bisa memulai atau mengganti nama sesi pada waktu yang sama. Balasan anggota lain ke kartu sesi baru milik orang lain mendapat “Kartu ini milik @alice. Kirim /new untuk kartumu sendiri.” dan tidak dikirim ke mana pun. Pertanyaan ganti nama berhenti menunggu setelah 10 menit, atau begitu pemiliknya melakukan hal lain. Kartu “x1 siap”, balasan ke `/x1`, `/start`, dan `/help` memintamu membalas pesan sesi atau menyebut bot (“Balas pesan dari x1 (atau sebut @aipagerbot) untuk bicara dengan sesi itu.”), karena pesan biasa tidak sampai ke bot.

**Setiap orang punya keyboard sendiri.** Templat, Perintah, Model ›, dan « Kembali hanya mengubah keyboard orang yang mengetuknya: bot membalas sebagai balasan ke ketukan orang itu, dan keyboard orang lain tidak bergerak. « Kembali naik satu tingkat dari posisimu, apa pun yang sedang dilihat anggota lain. Keyboard utama yang dikirim `/start` dipakai bersama seluruh grup dan membawa semua orang kembali ke keyboard utama. Saat sebuah sesi grup dimulai atau berakhir, aipager mengirim ulang keyboard utama itu ke grup sekali, supaya tombol sesinya tetap terbaru. Setelah memperbarui dari versi tanpa tombol bertanda, mengetuk tombol keyboard lama mungkin diabaikan: kirim `/start` sekali untuk memberi grup keyboard baru. Keyboard chat pribadi tidak punya tanda dan bekerja seperti biasa.

### <a id="the-mini-app"></a>Mini App

`/app` membuka Mini App hanya di chat pribadi. Di grup, perintah itu tidak ada di menu `/`; kalau diketik di sana, perintah itu memberi tahu anggota yang punya chat sendiri dengan bot “📱 Mini App hanya bisa dipakai di chat pribadi - kirim pesan pribadi ke bot, lalu kirim /app di sana.”, dan semua orang lain “Mini App dibuka dari chat pribadimu dengan bot. Minta pemilik bot untuk menambahkanmu.”

Mini App menampilkan satu chat sekaligus. Seseorang yang ada di lebih dari satu chat (misalnya chat pribadinya dan sebuah grup) mendapat baris nama chat di bagian atas untuk berpindah di antaranya; baris itu terbuka di chat pribadi orang itu sendiri, kalau tidak ada di chat pertama tempat ia berada, dan mengingat pilihan terakhir di ponsel itu. Semua yang ada di dalamnya (sesi, tombolnya, pengaturan, dan notifikasi yang diterbitkannya) berlaku untuk chat yang dipilih, dengan peran orang itu di chat tersebut. Dengan satu chat, tidak ada yang tambahan ditampilkan.

## <a id="who-a-message-runs-as"></a>Dengan hak siapa sebuah pesan berjalan

Setiap pesan berjalan dengan **hak orang yang mengirimnya**, di chat tempat pesan itu dikirim. Seseorang yang menjadi `user` di grup dan `owner` di chat pribadinya sendiri mengirim, mengetuk, dan menjalankan perintah di grup sebagai `user`, apa pun urutan chat itu di `aipager.yaml`.

- Setiap pesan dari Telegram membawa identitas dan aturan pengirimnya ke sesi (lihat [keamanan → cara aturan diterapkan di tim](security.md#team-mode-enforcement)). Claude melihat siapa yang mengirim setiap pesan (`[via Telegram · @bob · role:user]` di grup), dan tidak pernah siapa yang mengendalikan sesi sebelumnya. Orang yang mengirim pesan menjadi orang yang mengendalikan sesi, entah pesan itu diketik, dikirim lewat `/x1`, templat, atau perintah keyboard.
- **Pesan yang ditahan mempertahankan pengirimnya.** Selama tugas orang lain berjalan (termasuk agen latar belakang yang dimulainya), atau selama pesan orang lain masih menunggu diambil, atau selama permintaan izin atau pertanyaan terbuka, pesan baru ditahan (👀) dan dikirim setelah itu selesai, sebagai tugasnya sendiri, **dengan hak pengirimnya sendiri**. Tugas yang diketik di terminal dihitung sebagai milik pemilik: hanya pesan pemilik (atau, di chat pribadi, anggotanya sendiri) yang bergabung ke dalamnya. Pesanmu sendiri tidak pernah menunggu tugasmu sendiri. Kalau tugas yang ditunggu dihentikan, atau berhenti karena alat yang diblokir, pesan yang ditahan dibuang (🤷): kirim lagi.
- **Yang paling ketat menang.** Kalau pesan dari orang-orang berbeda tetap berakhir di satu tugas, tugas itu berjalan dengan gabungan paling ketat dari semua yang ikut. Pesan yang bergabung ke tugas yang sedang berjalan tidak pernah melebarkannya: sisa tugas mempertahankan aturan paling ketat dari semua yang bergabung.
- **Perintah slash peran terbatas.** Peran yang tidak melewati aturan peran (`user`, `read_only`, peran yang kamu definisikan tanpa `bypass_role_denies`, dan pengirim yang tidak dikenal aipager) hanya bisa mengirim perintah slash dari keyboard “Perintah” (keyboard milikmu, kalau `keyboard.json` mengubahnya), `/compact`, dan penggantian model (`/model <name>`). Semua yang lain, diketik, dikirim lewat `/x1 /deliver ...`, sebagai keterangan file, sebagai pesan pertama `/new x1 /review ...`, atau sebagai templat, mendapat “Perintah itu khusus admin.” dan 🤷, dan tidak ada yang sampai ke Claude. Pemilik dan admin bisa mengirim apa saja.
- **Peran terbatas menjauh dari folder home dan file kredensial.** Tugas `user` atau `read_only` tidak pernah menulis ke folder sesi yang merupakan folder home-mu, `/`, atau di atasnya (hanya ke scratchpad-nya), dan tidak pernah membaca file kredensial yang tercantum di [Peran](#roles).
- **Peran terbatas tetap di folder utamanya.** Tugas peran tanpa `can_manage` tidak membaca apa pun di folder home-mu di luar [folder utama](#root-folders) chat itu dan tidak menulis apa pun ke folder sesi di luarnya. Folder utama itu adalah milik chat tempat pesan anggota itu datang, ke sesi mana pun pesan itu pergi.
- **Anggota yang dihapus langsung kehilangan haknya, tapi tidak tugasnya yang sedang berjalan.** Pengirim yang tidak lagi dikenal aipager (dihapus saat pesannya menunggu) berjalan dengan batas bawah yang dibatasi. Setelah muat ulang langsung, pesannya yang ditahan dibuang dan tugasnya yang masih berjalan tetap berjalan, dibatasi batas bawah sampai selesai; kirim `/stop` untuk mengakhirinya sekarang (lihat [Muat ulang tanpa mulai ulang](#live-reload)).
- **Pesan dari sesi Claude lain membawa hak pengirimnya.** Pesan yang dikirim satu sesi ke sesi lain (`SendMessage` milik Claude Code) berjalan dengan hak tugas yang mengirimnya, sebagaimana diperiksa aipager, dan tidak pernah dengan hak sebelumnya milik sesi penerima. Pesan yang tidak bisa diperiksa aipager berjalan dengan aturan paling ketat dan tanpa perubahan file atau akses internet (lihat [keamanan → pesan antarsesi Claude](security.md#messages-between-claude-sessions)).
- **Alat yang diblokir menghentikan tugas.** Saat sebuah tugas mencoba alat yang dilarang aturannya, tugas itu langsung berhenti dengan “🛑 x1 · Diblokir aturan keamanan: ... (dihentikan)”, dan setiap pemanggilan alat berikutnya dalam tugas itu juga ditolak.

## <a id="who-can-tap-what"></a>Siapa bisa mengetuk apa

Setiap tombol memeriksa peran orang yang mengetuknya, di chat tempat tombol itu berada, pada saat diketuk. Peran orang yang mengirim perintah yang membuat tombol itu tidak dihitung. (Beberapa kartu juga memeriksa siapa yang memintanya: kartu konfirmasi dan kartu `/new`, di bawah.)

- **Melihat terbuka untuk setiap anggota** (VIEW). Anggota `read_only` bisa menelusuri `/resume`, membuka tampilan “Sudah berakhir” di `/status`, menu ⋮ sebuah sesi, mode-nya, atau diff-nya, melihat `/settings`, dan menutup menu. Mereka tidak bisa bertindak: tombol lain mana pun menjawab “Peranmu tidak boleh melakukan itu di chat ini.”
- **Menjawab Claude butuh `can_approve`** (APPROVE): Izinkan, Selalu izinkan, Tolak, opsi pertanyaan, Kirim, Lanjutkan, dan Jawab di bilah yang disematkan. `read_only` tidak punya ini.
- **Bertindak pada sesi butuh `can_prompt`** (PROMPT): Hentikan, Coba lagi, Ringkas, Kirim sekarang, Akhiri, Mulai ulang, Ganti nama, Hapus, Lanjutkan, pergantian mode, ✍️ bicara, Hapus semua, preferensi milik sesi itu, dan kartu `/new`. Tombol yang tidak dikenal aipager juga butuh ini.
- **Pindah ke Akses penuh butuh admin** (MANAGE: `owner`, `admin`, atau peran dengan `can_manage`, aturan yang sama dengan `/mode full`): “Ya, alihkan” dan “Hentikan & alihkan” di kartu yang mengalihkan ke Akses penuh, dan melanjutkan dalam Akses penuh. Mengalihkan antara Tanya dan Auto terbuka untuk siapa pun yang bisa bertindak pada sesi itu. Melanjutkan sesi yang dulu dalam Akses penuh (`/resume x1` yang diketik, daftarnya, Lanjutkan di kartu nama `/new`, atau Mini App) mengembalikannya dalam Mode Auto untuk semua orang lain, dan balasannya mengatakan itu: “♻️ x1 dilanjutkan dalam Mode Auto: Mode Akses penuh perlu admin.”
- **Di chat pribadinya sendiri, seseorang bisa memakai Akses penuh, apa pun perannya** (termasuk `user`; bukan `read_only`, yang tidak mengirim pesan; peran buatan butuh `can_prompt` dan `can_approve`, karena Akses penuh menjawab semua permintaan izin Claude Code). Semua jalur ke Akses penuh di atas berfungsi untuknya di sana: `/mode full`, kartu mode, `/new !x1`, kartu `/new`, `/resume`, dan Mini App. Chat-nya sendiri adalah satu-satunya tempat ini berlaku: di grup dan di chat pribadi orang lain mana pun, Akses penuh tetap butuh admin. Sesi barunya dimulai dalam Mode Auto seperti milik semua orang sampai ia memilih Akses penuh di `/settings` → Sesi baru. Tidak ada hal lain yang dilakukan admin yang terbuka untuknya (`/update`, tombol pemasangan pesan suara, dan `/settings` grup tetap khusus admin). Membagikan Akses penuh dengan cara ini aman: Akses penuh menjalankan Claude Code dengan `--dangerously-skip-permissions`, yang hanya melewati permintaan izin milik Claude Code sendiri. Hook PreToolUse milik aipager tetap berjalan di setiap pemanggilan alat dan menerapkan aturan peran anggota itu (batas bawah keamanan, batas folder, aturan larangan, file kredensial) persis seperti di Mode Tanya dan Mode Auto.
- **Pemasangan butuh admin** (UPDATE): tombol pemasangan dan mulai ulang untuk pesan suara, seperti `/update`.
- **Hanya admin yang bisa mengubah `/settings` grup.** Siapa pun boleh melihat.
- **Kartu konfirmasi milik orang yang memintanya.** Di grup, kartu konfirmasi untuk Akhiri (`/kill`, ⋮ Akhiri sesi), Mulai ulang, Hapus, dan pergantian mode, daftar sesi yang ditampilkan `/kill`, `/restart`, `/delete`, `/mode`, `/stop`, `/rename`, dan `/diff`, dan kartu “Lanjutkan x1 sebagai:” yang dibuka dengan mengetuk Lanjutkan (tombol mode-nya) hanya menjawab orang yang mengirim perintah atau mengetuk tombol yang membukanya. Ketukan orang lain, termasuk Batal, mendapat “Ini kartu milik @alice. Kirim /kill x2 untuk kartumu sendiri.” (dengan perintah kartu itu) dan tidak mengubah apa pun. Kartu `/mode` sendiri (sesi itu dalam mode apa) dipakai bersama: pergantian yang diketuk di sana membuka konfirmasi yang menjadi milik orang yang mengetuk. Kartu `/new` hanya menjawab orang yang mengirim `/new`. Pemilik sebuah kartu diingat sampai kartu itu dipakai atau dibatalkan, bahkan melewati mulai ulang aipager (4.096 kartu terakhir). Pengecualiannya adalah kartu nama `/new` dan pertanyaan nama `/rename`: keduanya toh kedaluwarsa setelah 10 menit, jadi setelah mulai ulang kirim lagi perintahnya.
- **Tombol hanya berfungsi di chat sesinya.** Kalau diketuk di tempat lain, tombol menjawab “Tombol ini milik chat lain.” dan tidak melakukan apa pun, dan Lanjutkan selalu melanjutkan sesi yang disebut tombolnya, tidak pernah sesi chat lain dengan nama yang sama.
- **Tombol lama tidak mengubah apa pun.** Tombol yang tersisa dari tugas sebelumnya, atau dari sebelum sesi itu berakhir dan kembali, tidak bekerja pada yang sedang berjalan sekarang; tombol itu mengatakannya (lihat [perintah → tombol lama](commands.md#stale-buttons)).

## <a id="what-everyone-sees"></a>Yang dilihat semua orang

**Jawaban ke Claude.** Setiap Izinkan, Tolak, dan jawaban diterbitkan di chat di bawah kartu sesi, dengan siapa yang melakukannya:

```
✅ x1 · Diizinkan · @alice · Bash: ls -la /tmp
🚫 x1 · Ditolak · @bob · WebFetch: https://example.com
✓ x1 · Dijawab · @carol · Which database? → Postgres
```

**Siapa yang mengubah sesi.** Ketukan tombol hanya terlihat oleh orang yang mengetuk, jadi di grup setiap hasil yang mengubah sebuah sesi menyebut siapa yang melakukannya:

```
⚠️ x1 · @bob menghentikannya
⏹ @bob mengakhiri x2
🔄 @alice memulai ulang [x1].
🗑️ @carol menghapus [x3].
⚙️ @alice mengalihkan sesi ke 🤖 Mode Auto.
@alice mengubahnya.          (di bawah /settings)
```

Kartu “x1 siap” menyebut siapa yang mengganti mode atau modelnya (“@bob mengganti model ke Opus.”). Di chat pribadi, baris-baris hasil ini tidak menyebut siapa pun. Perubahan yang dilakukan di Mini App diterbitkan di chat sebagai “... dari Mini App” (penghentian dari sana juga menyebut di kartu sesi siapa yang melakukannya).

**Alat yang diblokir aturan.** Tugasnya berhenti dengan “🛑 x1 · Diblokir aturan keamanan: Write is in this role's deny_tools (dihentikan)”. Kalau Claude Code meminta izin untuk alat yang tidak boleh dipakai orang yang pesannya memulai tugas yang sedang berjalan, bot sendiri menjawab Tolak, tidak menampilkan permintaan apa pun, dan menerbitkan `⛔ x1 · Edit diblokir untuk @bob (peran user)` dengan ringkasan pemanggilannya di bawah, menyebut orang itu. Tugas yang dimulai oleh pesan dari beberapa orang diblokir saat salah satu dari mereka tidak boleh memakai alat itu, dan menyebut orang itu. Tugas yang diketik di terminal adalah milikmu dan tidak pernah diblokir oleh aturan anggota, kecuali ada pesan Telegram yang bergabung ke dalamnya: lalu tugas itu diperiksa dengan aturan orang yang mengirim pesan terakhir sesi itu (daftar milik grup sendiri, kalau itu tidak diketahui). Saat aipager tidak tahu tugas itu milik siapa (sudah berjalan saat aipager dimulai ulang), ia memakai aturan orang yang mengirim pesan terakhir sesi itu, atau, kalau itu juga tidak diketahui, daftar milik grup sendiri.

**Keadaan pesan.** 👀 pada sebuah pesan berarti pesan itu dikirim ke sesi (atau ditahan sampai bisa); 👍 berarti Claude sudah mengambilnya; 🤷 berarti pesan itu dibuang sebelum Claude mengambilnya. `/whoami` menampilkan namamu, peranmu, dan aturanmu di chat ini.

**Bilah status yang disematkan.** Grup punya bilah status yang disematkan sendiri (lihat [perintah → bilah status yang disematkan](commands.md#the-pinned-status-bar)) yang hanya mencantumkan sesi grup itu, kalau bot adalah admin dengan hak menyematkan. Tanpa hak itu, bilah untuk grup dilewati: pesannya dihapus, bukan dibiarkan tidak tersemat di riwayat.

**Audit di disk.** `~/.claude/aipager-audit.jsonl` mencatat setiap keputusan dengan `user_id`, `username`, dan chat-nya, supaya nanti bisa disusun ulang apa yang dilakukan setiap orang. Log ini selalu ditulis dalam bahasa Inggris, apa pun bahasa chat-nya.

**Bahasa.** Sebuah grup punya satu bahasa untuk pesan, kartu, dan keyboard milik aipager sendiri, diatur oleh admin lewat `/language`; sampai admin memilihnya, bahasanya Inggris. Bahasa grup tidak pernah mengikuti aplikasi Telegram siapa pun. Yang hanya dilihat satu orang, notifikasi singkat setelah sebuah ketukan, datang dalam bahasa orang itu sendiri: pilihannya di chat pribadi dengan bot, kalau tidak, bahasa aplikasi Telegram-nya kalau orang itu datang setelah versi ini, kalau tidak juga, bahasa grup. Hanya teks aipager yang berubah; Claude menjawab setiap orang dalam bahasa yang dipakai orang itu untuk menulis kepadanya. Lihat [Bahasa](commands.md#languages).

## <a id="where-messages-go"></a>Ke mana pesan pergi

- **Setiap sesi milik satu chat, dan semua tentang sesi itu dikirim ke sana**: kartunya, jawabannya, permintaan izinnya, file, dan notifikasinya. Pesan di satu chat tidak pernah masuk ke sesi chat lain, dan nama sesi hanya berfungsi di chat-nya sendiri: mengetik nama sesi chat lain mendapat balasan “Tidak ada sesi bernama”. Sesi yang dimulai saat aipager berjalan tanpa chat yang diatur mendapat chat-nya begitu `aipager config` menambahkannya; selama sebuah sesi tidak punya chat, tidak ada chat yang menampilkannya atau bisa bertindak padanya.
- **Sesi yang dimulai di terminal** (`aipager session <name>`) milik chat pribadi pemilik: chat pribadi yang anggotanya berperan `owner` (peran dengan `bypass_safety`). Kalau ada beberapa chat pribadi dan tidak satu pun milik pemilik, itu chat pribadi pertama di daftar; kalau sama sekali tidak ada chat pribadi, chat pertama. Pada pemasangan dengan chat pribadi, sesi itu tidak pernah masuk ke grup.
- **Perpindahan ke supergroup diikuti otomatis** (lihat [Saat Telegram meng-upgrade grup](#when-telegram-upgrades-the-group)).

## <a id="aipageryaml-schema-team-parts"></a>Struktur `aipager.yaml` (bagian untuk tim)

```yaml
schema_version: 3
bot_token: "…"

scopes:
  - kind: dm
    chat_id: 12345       # chat pribadimu sendiri: ID-nya sama dengan ID penggunamu
    label: owner DM
    members:
      - id: 12345
        label: alice
        role: owner
  - kind: group
    chat_id: -100123456789
    label: team
    deny_tools: [Write, Edit]   # opsional: jawaban untuk “Block file edits”
    root: /home/alice/aipager-users/group-team   # folder utama para anggota
    members:
      - id: 12345        # ID pengguna Telegram (BUKAN username, BUKAN ID chat)
        label: alice     # cara orang itu ditampilkan di chat (@alice)
        role: owner
      - id: 67890
        label: bob
        role: user
        deny_tools: [WebFetch]  # opsional, per anggota
      - id: 11111
        label: charlie
        role: read_only
```

Chat pribadi dan chat grup hidup berdampingan: bot melayani setiap chat dalam daftar, dan sesi setiap chat dijaga terpisah. File ini ditulis oleh `aipager config`; kamu tidak pernah perlu mengeditnya.

## <a id="how-rules-work"></a>Cara kerja aturan

`~/.config/aipager/policy.yaml` mengatur aturan per peran. Periksa dengan `aipager policy validate`. Field yang diterima setiap peran (selain yang tercantum di [Peran buatan sendiri](#custom-roles)):

- `deny_tools`: nama alat yang dilarang tanpa bertanya (misalnya `Write`, `Edit`, `Bash`, `WebFetch`).
- `allow_tools`: kalau tidak kosong, daftar yang diizinkan: semua yang lain dilarang untuk peran ini.
- `deny_bash_patterns`: pola yang dicocokkan dengan input `Bash`.
- `deny_paths_no_access` / `deny_paths_no_write`: aturan path. Untuk `user`, `read_only`, dan setiap peran yang kamu definisikan tanpa `bypass_role_denies`, `deny_paths_no_access` dimulai dengan file kredensial yang tercantum di atas pada **user**; mengaturnya menggantikan daftar itu, jadi salin yang ingin kamu pertahankan ke daftarmu (tugas yang sedang berjalan yang digabungi pesan lain kembali menaati daftar itu sampai selesai). Aturan tanpa `/` atau `~` di awal (`**/.env`) cocok di mana saja, jadi selama aturan itu ada, setiap `Grep`/`Glob` dari peran terbatas ditolak (pencarian apa pun bisa membaca file seperti itu), dan itu menghentikan tugasnya.

`deny_tools` chat dan `deny_tools` anggota di `aipager.yaml` ditambahkan ke peran, dan `allow_tools` anggota ditambahkan ke daftar izin peran. Semua ini hanya berlaku untuk peran tanpa `bypass_role_denies`: tidak memengaruhi pemilik atau admin.

File `~/.config/aipager/policy.d/*.yaml` dibaca setelah `policy.yaml`, menurut urutan nama, dengan format yang sama: field peran yang diatur di file belakangan menggantikan nilai sebelumnya, dan daftar `safety:` hanya ditambahi. `aipager policy validate` memeriksa semuanya.

Di bawah semua peran ada **batas bawah keamanan** (path yang dilindungi dan pola perintah) yang hanya dilewati `owner`: admin juga menaatinya. Isinya daftar bawaan aipager ditambah bagian `safety:` di `policy.yaml`, yang hanya bisa menambah:

```yaml
safety:
  deny_paths_no_access: [/srv/secrets/**]   # tidak boleh dibaca, ditulis, atau dicari
  deny_paths_no_write: [~/notes/**]         # boleh dibaca, tidak boleh ditulis
  deny_bash_patterns: ['\bcurl\b']          # perintah Bash yang diblokir
```

Setiap tugas dari setiap peran kecuali `owner` mendapatkannya utuh, begitu juga tugas yang tidak bisa dikaitkan aipager ke siapa pun; `aipager doctor --safety-check` menampilkan apa yang berlaku. Setelah muat ulang langsung, perubahan di sana berlaku untuk pesan yang dikirim setelah muat ulang; tugas yang sudah berjalan atau pesan yang sudah menunggu di antrean mempertahankan daftar lama (hentikan tugas itu untuk menerapkan perubahannya sekarang). Path `deny_paths_no_access` di sana tanpa `/` atau `~` di awal cocok di mana saja, jadi selama path itu ada, setiap `Grep`/`Glob` dari setiap peran kecuali `owner` ditolak (`aipager doctor` memperingatkannya). Path yang dilindungi berlaku untuk alat file Claude Code (`Read`, `Write`, `Edit`, `MultiEdit`, `NotebookEdit`, `LSP`, `Grep`, `Glob`); `Grep`/`Glob` peran terbatas juga harus tetap di dalam folder sesi (pencarian `aipager/**` di `~/.config` ditolak). Alat seperti `WebFetch` atau `SendFile` tidak diperiksa berdasarkan path. Tugas Telegram yang tidak bisa dikaitkan aipager ke pengirimnya mendapat batas bawah yang tidak lebih longgar dari `user`. Coba lagi berjalan dengan peran orang yang mengetuk kalau orang itu yang mengirim pesan yang diulang, dan dengan batas bawah kalau tidak. Pola perintah hanya penting untuk peran yang punya Bash, dan di sana fungsinya filter, bukan tembok; lihat [Model keamanan → cara aturan diterapkan di tim](security.md#team-mode-enforcement).

## <a id="privacy-considerations"></a>Pertimbangan privasi

- Bot hanya mendengarkan **chat yang diatur**. Kalau kamu menambahkan bot ke grup lain, bot tidak aktif di sana: tidak pernah membalas di sana. Bot mencatat siapa yang menyapanya di sana (ID, username, dan nama orang itu, ID dan judul grup) ke `~/.claude/aipager-pending-users.json`, supaya `aipager config` bisa menambahkan grup itu. Hanya kamu yang bisa membaca file itu, dan file itu menyimpan 200 orang terakhir yang terlihat dalam 30 hari.
- Anggota `read_only` **bisa membaca** pesan, jawaban, dan input alat. Mereka tidak bisa bertindak, tapi melihat semua yang ada di grup. Kalau suatu pekerjaan harus dirahasiakan dari seseorang, orang itu tidak semestinya ada di grup.
- Pada pemasangan dengan chat pribadi, sesi terminalmu dan sesi chat pribadimu tidak pernah muncul di grup (lihat [Ke mana pesan pergi](#where-messages-go)). Sesi terminal yang sudah dipindahkan aipager versi lama ke grup tetap di grup: akhiri, hapus dari sesi grup yang sudah berakhir, lalu mulai lagi.
- `aipager.yaml` berizin 0600 (hanya pemilik). aipager tidak mengatur izin `policy.yaml`: jaga supaya hanya pemilik yang bisa mengaksesnya (`chmod 600 ~/.config/aipager/policy.yaml`).
- Log audit (`~/.claude/aipager-audit.jsonl`) tidak punya izin sendiri: sepribadi folder `~/.claude`-mu.
- [Bot pengamat](observers.md) menerima notifikasi dari setiap chat yang dilayani bot, termasuk grup: pasang pengamat hanya untuk chat yang bisa dipercaya dengan semua itu.

## <a id="revoking-a-user"></a>Mencabut akses seseorang

1. Jalankan `aipager config` → Edit a member → chat → orangnya → Remove member. Untuk mencabut hak mengirim pesan tapi tetap membiarkannya membaca, pakai Set role dan pilih `read_only`. (Untuk menambahkannya lagi nanti: Edit a scope → grup → Add a member.) Untuk chat pribadi seseorang, akhiri sesinya, lalu Edit a scope → chat pribadinya → Remove this scope.
2. Wizard memuat ulang aipager secara langsung; ia menulis "Scopes reloaded live". Seketika itu pesannya yang ditahan dibuang (🤷 dan balasan “Tidak dikirim ke Claude”), dan tugasnya yang masih berjalan dibatasi pada batas bawah sampai selesai. Kirim `/stop` untuk mengakhiri tugas itu sekarang.
3. Keluarkan juga orang itu dari grup Telegram. Selama ia di sana, ia masih bisa membaca semua yang diterbitkan di sana: kartu, jawaban, input alat, dan permintaan izin.

Periksa apa yang dikatakan wizard setelah langkah 1. Kalau ia memintamu memulai ulang aipager, ia tidak bisa menjangkaunya: mulai ulang (`aipager service stop`, lalu `aipager service start`), karena sampai saat itu daftar anggota sebelumnya tetap di memori. Kalau ia menulis "Not applied", perbaiki masalah yang disebutkannya: sampai saat itu aipager tetap memakai konfigurasi sebelumnya. Setelah mengedit `aipager.yaml` dengan tangan, kirim muat ulangnya dengan tangan (lihat [Muat ulang tanpa mulai ulang](#live-reload)).

## <a id="related-docs"></a>Halaman terkait

- [Arsitektur](architecture.md): cara prosesnya disusun.
- [Perintah bot](commands.md): referensi antarmuka.
- [Model keamanan](security.md): batas kepercayaan, daftar ancaman.
- [Pemecahan masalah](troubleshooting.md): referensi `aipager doctor`.
