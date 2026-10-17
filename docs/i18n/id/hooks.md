# Event hook

<!-- i18n-source: docs/hooks.md @ 054b893 -->

> Halaman ini diterjemahkan dari bahasa Inggris; kalau keduanya berbeda, versi bahasa Inggris yang berlaku. ([English](../../hooks.md))

Claude Code menjalankan perintah hook pada momen tertentu dalam sebuah tugas: pesan dikirim, alat akan dijalankan, alat selesai, tugas berakhir, dan seterusnya. aipager mengait ke setiap momen itu dan mengubahnya menjadi tindakan di Telegram: kartu kerja, permintaan izin, jawaban, notifikasi, dan log audit.

## <a id="how-it-is-wired"></a>Cara pemasangannya

`aipager config` (atau `aipager setup`) menambahkan entri aipager ke `~/.claude/settings.json`, dan aipager memeriksanya setiap kali mulai:

- satu perintah `aipager-hook` untuk setiap 16 event di bawah, dengan filter `*` pada event alat (`PreToolUse`, `PostToolUse`, `PermissionRequest`);
- batas waktu 5 detik untuk `PreModelSwitch` dan, dari `aipager config` dan `aipager setup`, 30 detik untuk `PermissionRequest` (hook-nya sendiri menunggu paling lama 20 detik, lihat di bawah); entri lain memakai nilai bawaan Claude Code;
- perintah `statusLine`, `aipager-statusline`.

`aipager uninstall` menghapus lagi entri-entri ini (lihat [perintah](commands.md#command-line-aipager-uninstall)).

Claude Code menjalankan proses `aipager-hook` (`aipager.dtach.notify_hook:main`) baru untuk setiap event. Proses itu membaca JSON event dari stdin, menambahkan dua field (di bawah), lalu mengirimnya ke aipager sebagai satu datagram lewat soket Unix. Soketnya, berurutan: `$AIPAGER_INSTANCE_DIR/aipager.sock` untuk instance pengujian terisolasi, `$AIPAGER_SOCKET_PATH`, `$XDG_RUNTIME_DIR/aipager.sock`, atau `/tmp/aipager.sock`. Pengiriman tidak pernah menunggu: kalau aipager tidak sedang mendengarkan, event itu hilang dan monitor sesi menyusul dari file-file sesi.

Sebagian besar event memakan waktu Claude Code sekitar 20-25 ms untuk memulai proses. Beberapa juga memuat kode aturan aipager: sebelum pemanggilan alat (pemeriksaan keamanan, lihat [PreToolUse](#pretooluse)) sekitar 70 ms, di akhir tugas (untuk melepas penanda tugas terbuka) sekitar 55 ms, dan `UserPromptSubmit` (yang menulis aturan tugas). `PermissionRequest` sengaja menunggu ketukanmu, sampai 20 detik.

Hook membatasi memorinya sampai 1 GB. Hook yang mentok di batas itu keluar dengan error (untuk `PreToolUse` dengan kode keluar yang dibaca Claude Code sebagai penolakan) dan mengirim datagram `hook_memory_cap_hit` ke aipager, yang menjadi notifikasi di chat dengan tombol “🐞 Laporkan ini” untuk pemilik instalasi (lihat [laporan masalah](problem-reports.md)). Atur `AIPAGER_DEBUG=1` di environment sesi supaya hook dan baris status mencetak diagnosisnya ke stderr.

## <a id="what-the-daemon-receives"></a>Apa yang diterima aipager

Datagram itu adalah JSON hook milik Claude Code sendiri dengan field tambahan dari aipager (beberapa event membawa lebih banyak, disebutkan di masing-masing event di bawah):

| Field | Arti |
|---|---|
| `hook_event_name` | Event-nya (field Claude Code; aipager membagi pekerjaan berdasarkan ini). |
| `session_id`, `transcript_path`, `cwd`, ... | Field milik Claude Code sendiri, apa adanya. `session_id` adalah ID percakapan di Claude Code. |
| `session` | Ditambahkan aipager: nama sesi menurut aipager, `claude-<name>`, dari variabel `CLAUDE_DTACH_SESSION` yang diatur aipager saat memulai sesi. Event dari sesi Claude Code yang tidak dimulai aipager datang tanpa nama, dan aipager mengabaikannya. |
| `sl_tokens` | Ditambahkan aipager, di setiap event kecuali `MessageDisplay`, kalau file baris status sesi itu ada: `context_pct`, `total_output`, `total_input`, `current_output`, `lines_added`, dan `lines_removed`. |

Event alat juga membawa `tool_name` dan `tool_input`. Setiap event di dalam subagen membawa `agent_id` (dan event subagen juga membawa `agent_type`); event milik loop utama tidak membawanya, dan dari situ aipager menaruh pemanggilan alat sebuah agen di baris agen itu.

## <a id="event-reference"></a>Referensi event

Penangan aipager ada di `aipager/dtach/hook_receiver.py`; pekerjaan hook sendiri ada di `aipager/dtach/notify_hook.py`.

### <a id="sessionstart"></a>`SessionStart`

Sebuah sesi mulai, dilanjutkan, atau percakapannya diringkas. Sesi yang belum dilacak aipager akan didaftarkan. Setelah peringkasan, kartu menampilkan `📦 name · Percakapan diringkas: 82% → 4%` (lihat [PreCompact](#precompact--postcompact)). Untuk percakapan baru, hook juga melepas penanda tugas terbuka dari tugas sebelumnya.

### <a id="sessionend"></a>`SessionEnd`

Sesi berakhir. Sesi itu ditandai GONE: hilang dari bilah status yang disematkan dan pindah ke sesi yang sudah berakhir, tempat `/resume` bisa mengembalikannya. `aipager session <name>` atau `/new <name>` memulai yang baru.

### <a id="userpromptsubmit"></a>`UserPromptSubmit`

Claude mengambil sebuah pesan, yang diketik di terminal atau dikirim dari Telegram.

Hook pertama-tama mencocokkan pesan itu dengan catatan izin sesi (`/tmp/claude-notes-<session>/`, satu untuk setiap pesan Telegram yang diketik aipager) dan menulis aturan tugas ke `/tmp/claude-policy-<session>.json`, yang dibaca pemeriksaan [PreToolUse](#pretooluse) (lihat [keamanan → cara aturan diterapkan di tim](security.md#team-mode-enforcement)). Hook memberi tahu aipager pesan mana yang diambil Claude (datagram `queue_pickup`) sebelum meneruskan event. Lalu, kalau ada, ia mencetak konteks tambahan untuk Claude: gaya jawaban yang dipilih di `/settings` dan, saat membalas pesan yang lebih lama, isi pesan itu dan kapan dikirim. Pesan `<task-notification>` (Claude membangunkan dirinya sendiri saat agen atau perintah latar belakang selesai) adalah lanjutan dari pekerjaan yang sama: pesan ini tidak cocok dengan catatan mana pun dan mempertahankan aturan tugas.

aipager menandai sesi sedang bekerja, membuka kartu kerja kalau belum ada (pesan dari Telegram sudah punya), dan mengubah 👀 pada pesan yang diambil Claude menjadi 👍. Pesan yang dikirim selama tugas berjalan mendapat 👍 saat Claude memasukkannya ke tugas itu atau memulai tugas berikutnya dengannya.

### <a id="pretooluse"></a>`PreToolUse`

Sebuah alat akan dijalankan. Pemeriksaan keamanan milik aipager sendiri berjalan di sini, di dalam hook, sebelum Claude Code melihat aturan izinnya sendiri:

- Tugas yang dimulai dari Telegram terikat pada aturan peran pengirimnya dan pada batas bawah keamanan (path yang dilindungi, pola perintah), yang hanya bisa dilewati peran `owner`; tugas yang tidak bisa dikaitkan aipager ke siapa pun mendapat batas bawah itu; pesan yang kamu ketik di terminal tidak dibatasi. Aturannya dijelaskan di [grup](groups.md#how-rules-work) dan [keamanan](security.md#team-mode-enforcement).
- Saat sebuah aturan menolak pemanggilan, hook mencetak keputusan `deny`, Claude Code menolak alat itu tanpa bertanya, dan aipager mengetahuinya (`safety_blocked`): tugas berhenti dengan `🛑 x1 · Diblokir aturan keamanan: ... (dihentikan)`, dan setiap pemanggilan alat berikutnya dalam tugas itu juga ditolak.
- Pemeriksaan yang rusak sendiri akan menolak, kecuali aturan sesi berhasil dibaca dan memberi pengecualian pemilik.

Di sisi aipager, pemanggilan itu menjadi satu baris di kartu kerja (pemanggilan subagen masuk ke baris agen itu), dan `Write` atau `Edit` menampilkan pratinjau perubahan saat `/settings` → Pratinjau perubahan (diff) menyala (bawaannya mati). Pemanggilan `AskUserQuestion` menjadi pertanyaan dengan satu tombol per opsi; pertanyaan yang diajukan agen pembantu Claude Code saat tidak ada tugas yang berjalan tidak ditampilkan (bukan Claude yang bertanya kepadamu). Pemanggilan `Agent` milik Claude sendiri menyimpan `subagent_type` dan `description` untuk agen yang dimulainya, dan pemanggilan subagen adalah tindakan terakhir agen itu: keduanya terlihat di status di bawah jawaban selama agen bekerja di latar belakang.

### <a id="permissionrequest"></a>`PermissionRequest`

Claude Code akan meminta izin. Kartu kerja berubah menjadi permintaan, dengan perintah atau path yang sebenarnya di bawah deskripsi yang diberikan Claude (lihat [perintah → permintaan izin](commands.md#permission-prompts)): Izinkan, Tolak, Hentikan, dan Selalu izinkan hanya saat Claude Code menawarkan aturan sungguhan untuk diingat.

Hook menjawab permintaan itu sendiri kalau bisa. Ia membuka soket balasan berumur pendek, mengirim alamatnya bersama event, dan menunggu ketukanmu sampai 20 detik. Ketukan yang tepat waktu kembali sebagai keputusan `PermissionRequest` (izinkan, izinkan dengan aturan, atau tolak), jadi Claude Code tidak menampilkan dialognya sendiri. Kalau ketukan tidak datang tepat waktu, hook memberi tahu aipager bahwa penantiannya berakhir (`permission_reply_timeout`) dan tidak mencetak apa pun; ia juga tidak mencetak apa pun kalau aipager tidak bisa dihubungi atau ada yang rusak di jalur ini. Lalu Claude Code menampilkan dialognya di terminal, dan ketukan yang lebih belakangan menjawabnya dengan mengetikkan tombol. Log audit (`~/.claude/aipager-audit.jsonl`) mencatat jalur mana yang dipakai setiap ketukan: `"via": "hook_decision"` atau `"via": "keystroke_fallback"`.

`PermissionRequest` yang datang saat permintaan lain sedang terbuka juga ditampilkan, sebagai pesannya sendiri di bawah kartu kerja. Dialog yang sama yang datang lagi (peminta, alat dan pemanggilan yang sama, atau input yang sama) hanya ditampilkan sekali. Claude Code juga menjalankan hook ini untuk `AskUserQuestion`; pertanyaan itu sudah tampil dari `PreToolUse`-nya, jadi tidak pernah ditampilkan dua kali. Dialog agen latar belakang baru muncul di terminal setelah hook-nya berhenti menunggu, di bawah dialog apa pun yang sudah terbuka di sana; karena itu ketukan hanya mengetik ke permintaan yang ditampilkan Claude Code lebih dulu (lihat [perintah → permintaan izin](commands.md#permission-prompts)).

### <a id="posttooluse--posttoolusefailure"></a>`PostToolUse` / `PostToolUseFailure`

Sebuah alat selesai atau gagal. Barisnya di kartu kerja membeku (error ditandai di baris itu), dan jawaban atas permintaan yang terbuka dicatat: Izinkan atau Tolak yang lama untuk permintaan itu tidak mengetik apa pun lagi. Sesi menyimpan 200 baris alat terakhir.

Pemanggilan `Bash` yang dipindahkan Claude Code ke latar belakang melaporkan `backgroundTaskId`. aipager lalu melacaknya sebagai perintah latar belakang (hanya perintah loop utama: pemanggilan dengan `agent_id` milik subagen itu) sampai ia melihat akhirnya: `<task-notification>` dengan `<status>` (di pesan yang membangunkan Claude, atau di riwayat percakapan kalau datang di tengah tugas), `TaskStop` atau `KillShell` dengan ID-nya, sesi berakhir, atau dua jam (`AIPAGER_BG_SHELL_MAX_TRACK`, dalam detik, bawaannya 7200). Tentang barisnya di kartu, lihat [perintah](commands.md#idle-responses).

Pemanggilan `Agent` (atau `Task`) di dalam sebuah subagen (event-nya membawa `agent_id`) melaporkan ID agen yang dimulainya. aipager mengingat agen mana yang memulai agen mana, sehingga bisa membedakan agen yang berhenti untuk menunggu agennya sendiri dari agen yang sudah selesai (lihat `SubagentStart` / `SubagentStop` di bawah).

### <a id="notification"></a>`Notification`

aipager membaca `notification_type` dari Claude Code:

- `permission_prompt`: sinyal permintaan izin yang lebih lambat dan lebih lama. Ia menampilkan permintaan hanya kalau tidak ada permintaan yang terbuka: kalau ada yang terbuka, itu dialog yang sudah ditampilkan `PermissionRequest`.
- `idle_prompt` selama permintaan izin atau pertanyaan menunggu: sesi tetap dalam keadaan menunggu dan chat mendapat `⬆️ jim · masih menunggu jawabanmu di atas`, sekali per penantian.
- `idle_prompt` dalam keadaan lain: diperlakukan seperti `Stop` (di bawah). Ini menangkap tugas yang `Stop`-nya hilang; jawaban yang sudah terkirim tidak pernah dikirim lagi.

### <a id="stop"></a>`Stop`

Tugas selesai. Sesi menjadi siap, kartu kerja membeku sesuai tata letak yang dipilih, dan jawaban Claude dikirim (dari `last_assistant_message` milik event, atau kalau tidak ada dari riwayat percakapan). Lalu pesan yang ditahan sampai tugas selesai dikirim. Agen dan perintah latar belakang yang masih berjalan membuat pekerjaan tetap terbuka: kartu tetap menjadi status pekerjaan, dan jawabannya diakhiri status langsung dari pekerjaan itu: apa yang dikerjakan setiap agen dan berapa perintah latar belakang pekerjaan itu yang masih berjalan (lihat [perintah → agen yang masih berjalan](commands.md#agents-still-running-when-the-answer-goes-out)). Hook lebih dulu melepas penanda tugas terbuka, kecuali Claude punya pesan di antrean yang langsung menjadi tugas berikutnya.

### <a id="stopfailure"></a>`StopFailure`

Tugas berakhir dengan error API. Sesi menjadi siap dan jawaban, kalau ada, dikirim seperti pada `Stop`; error yang dikenal (kelebihan beban, batas permintaan, ...) ditampilkan dengan kata-kata sederhana.

### <a id="subagentstart--subagentstop"></a>`SubagentStart` / `SubagentStop`

Sebuah subagen mulai atau selesai. Masing-masing punya baris sendiri di kartu kerja, `🤖 <type> · <activity> · <elapsed>`, dengan pemanggilan alatnya sendiri dilipat di bawahnya, dan saat berhenti membeku menjadi `✅ 🤖 <type> · N alat · <elapsed>`. `SubagentStart` tidak menyebut agen mana yang memulainya: aipager mencocokkannya dengan pemanggilan `Agent` (`PreToolUse`) bertipe sama yang sedang menunggu. Kalau semua pemanggilan itu datang dari satu agen, agen baru langsung dikaitkan dengan agen itu (barisnya di kartu menjadi `🤖 pipeline-runner › ship-tester`, dan di status di bawah jawaban ia muncul di bawah agen itu); kalau datang dari agen yang berbeda, tidak ada yang dikaitkan sampai `PostToolUse` milik agen yang memulainya menyebutnya. Deskripsi sebuah pemanggilan hanya dipakai kalau tepat satu pemanggilan bertipe itu sedang menunggu. Subagen yang `SubagentStop`-nya tidak pernah datang dihapus setelah diam selama 30 menit (`AIPAGER_SUBAGENT_SILENCE`, dalam detik; tidak ada event jenis apa pun dengan ID-nya). Agen yang sedang bekerja mengirim event alat setiap beberapa detik, jadi yang dihapus dengan cara ini hanya agen yang berhentinya terlewat (mulai ulang, datagram yang hilang); satu pemanggilan alat yang berjalan 30 menit tanpa satu pun event di bawah ID agen itu juga menghapusnya. `AIPAGER_SUBAGENT_TTL` diterima sebagai nama lama untuk pengaturan yang sama. Event `SubagentStop` tanpa tipe dan dengan ID tak dikenal sering datang dan diabaikan.

Agen latar belakang yang berhenti selagi agen yang dimulainya masih berjalan juga mendapat `SubagentStop`. aipager menganggapnya jeda, bukan akhir: agen itu tetap berjalan (barisnya, pekerjaan, status di bawah jawaban, bilah yang disematkan). `SubagentStart` berikutnya dengan ID yang sama adalah lanjutannya dan tidak menambah baris kedua; panggilan alat apa pun oleh agen itu sendiri juga dihitung sebagai lanjutan. Ia selesai saat berhenti tanpa ada lagi agennya sendiri yang berjalan. Kalau ia belum melanjutkan dua menit setelah yang terakhir dari mereka selesai, ia tidak lagi dihitung berjalan, tetapi tidak pernah ditampilkan selesai. Agen yang berjalan, termasuk yang menunggu, tetap disimpan saat aipager dimulai ulang.

### <a id="precompact--postcompact"></a>`PreCompact` / `PostCompact`

Claude sedang meringkas konteksnya (`trigger` bernilai `auto` atau `manual`). Kartu kerja menampilkan `🔄 name · Meringkas percakapan` (pesan baru kalau tidak ada kartu), supaya jeda itu tidak terlihat seperti crash. `PostCompact` mengakhiri keadaan itu; baris `📦 name · Percakapan diringkas: X% → Y%` datang dari `SessionStart` berikutnya, atau kalau itu terlewat, dari baris status. Baris status juga memberi peringatan `⚠️ name · Konteks terisi 82% - sebentar lagi diringkas otomatis` (dengan tombol Ringkas sekarang) saat konteks mencapai 80%, sekali per percakapan: meringkas tidak memunculkannya lagi, dan percakapan baru (misalnya setelah `/clear`) mendapatkannya lagi.

### <a id="messagedisplay"></a>`MessageDisplay`

Teks milik Claude sendiri pada saat muncul di layar. Riwayat percakapan baru ditulis setelah setiap putaran pemanggilan alat, jadi ini satu-satunya sumber terkini tentang apa yang dikatakan Claude, dan inilah yang membawa komentarnya ke kartu kerja. Teks subagen (event apa pun dengan `agent_id`-nya) tidak pernah dianggap teks Claude: tidak masuk ke komentar kartu kerja, Mini App, atau log tugas, dan menjadi tindakan terakhir agen itu di status di bawah jawaban. Hook tidak menambahkan data baris status ke event ini: Claude Code menunggunya saat menggambar, dan event ini datang beberapa kali per pesan.

### <a id="premodelswitch"></a>`PreModelSwitch`

Claude Code 2.1.251 dan yang lebih baru memanggilnya sebelum mengganti model. Isinya membawa `from_model`, `to_model`, `requested_model`, dan `source` (`command` untuk `/model` yang diketik, atau `picker` atau `sdk`). Sebelum mengganti, Claude Code biasanya bertanya "Switch model?", karena jawaban berikutnya harus membaca ulang seluruh percakapan. Hook yang menjawab `permissionDecision: "allow"` meloloskan penggantian tanpa pertanyaan itu.

`aipager-hook` menjawab `allow` hanya untuk penggantian yang diketik aipager sendiri, dari pemilih model di Mini App atau dari keyboard model. Penggantian lain tidak mendapat jawaban, jadi Claude Code bertanya seperti biasa. Ini berlaku untuk `/model` yang diketik operator di terminal, pilihan dari daftar, dan penggantian lewat SDK.

aipager mengenali penggantiannya sendiri dari sebuah file penanda. Tepat sebelum mengetik `/model <name>`, aipager menulis `aipager-modelswitch-<session>.json`:
- file itu ada di folder tempat soket kontrol berada (biasanya `$XDG_RUNTIME_DIR`);
- ditulis secara atomik, dengan izin 0600;
- berisi model dan ID sesi Claude;
- kedaluwarsa setelah 30 detik.

Hook menjawab `allow` hanya kalau semua ini terpenuhi:
- penanda itu file milikmu dan bukan symlink;
- belum kedaluwarsa;
- untuk sesi aipager yang sama dan ID sesi Claude yang sama;
- modelnya sama dengan `requested_model` atau `to_model`, tanpa membedakan huruf besar-kecil;
- `source` bernilai `command`.

Lalu hook mengambil penanda itu dengan penggantian nama atomik, supaya hanya berlaku sekali. Pemeriksaan ini membaca satu file lokal, tanpa soket, tanpa menunggu. Event ini tidak diteruskan ke aipager.

Claude Code 2.1.101 dan yang lebih baru mengabaikan event hook yang tidak dikenalnya, jadi versi lama tanpa `PreModelSwitch` tidak terpengaruh. Satu kasus masih bisa memunculkan pertanyaan itu: mengganti ke atau dari `opusplan` bisa membuat Claude Code bertanya ke hook dua kali, dan hanya pertanyaan pertama yang menemukan penandanya, jadi Claude Code bertanya "Switch model?" di terminal.

## <a id="the-status-line"></a>Baris status

Ini bukan event hook: Claude Code menjalankan perintah `statusLine`, `aipager-statusline` (`aipager.dtach.statusline_notify:main`), setiap kali menggambar ulang baris statusnya. Perintah ini:

- menulis JSON status Claude Code ke `/tmp/claude-status-<session>.json` (dibaca oleh hook untuk `sl_tokens`, oleh `aipager status`, dan oleh aipager);
- mengirim datagram `statusline` ke aipager dengan model, % konteks, biaya, jumlah token, serta baris yang ditambah dan dihapus, yang dipakai untuk kartu kerja, `/status`, Mini App, pemeriksaan bahwa penggantian model berhasil, dan peringatan konteks;
- mencetak baris status terminal: `[name] Opus 5.5 | 42% ctx | $1.23`.

## <a id="aipagers-own-datagrams"></a>Datagram milik aipager sendiri

Selain event hook, program pembantu mengirim beberapa pesan milik mereka sendiri ke aipager lewat soket yang sama:

| Pesan | Pengirim | Kapan |
|---|---|---|
| `queue_pickup` | hook | Saat `UserPromptSubmit`: pesan Telegram mana yang diambil Claude. |
| `safety_blocked` | hook | Saat `PreToolUse`, ketika pemeriksaan keamanan menolak pemanggilan. |
| `permission_reply_timeout` | hook | Penantian `PermissionRequest` berakhir tanpa ketukan. |
| `statusline` | baris status | Setiap kali baris status digambar ulang. |
| `hook_memory_cap_hit` | hook, baris status | Sebuah proses mentok di batas memorinya. |
| `report_error` | hook, perintah `aipager` | Terjadi error: hanya jenis error dan lokasinya di kode aipager, tidak pernah isi event-nya, untuk [laporan masalah](problem-reports.md). |

## <a id="the-permission-flow"></a>Alur permintaan izin

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

Kalau tidak ada ketukan dalam 20 detik, hook tidak mencetak apa pun dan Claude Code menampilkan dialognya sendiri; ketukan yang lebih belakangan mengetik jawabannya ke dialog itu lewat terminal sesi (`dtach -p`). Dalam kedua kasus, ketukan itu dicatat di log audit dan diterbitkan di bawah kartu (`✅ jim · Diizinkan · @alice · Bash: ls -la /tmp`).

## <a id="see-also"></a>Lihat juga

- [Arsitektur](architecture.md): letak penerima hook.
- [Perintah bot → permintaan izin](commands.md#permission-prompts): sisi yang kamu lihat.
- [Model keamanan](security.md): apa yang dicakup pemeriksaan keamanan dan apa yang tidak.
