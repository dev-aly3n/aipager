# Pemasangan dengan agen

<!-- i18n-source: docs/install-with-an-agent.md @ fc5d6810 -->

> Halaman ini diterjemahkan dari bahasa Inggris; kalau keduanya berbeda, versi bahasa Inggris yang berlaku. ([English](../../install-with-an-agent.md))

Halaman ini untuk agen coding AI (Claude Code atau agen apa pun yang bisa memakai shell) yang diminta memasang dan mengatur aipager untuk orangnya. Ikuti langkah demi langkah. Setiap perintah di sini berjalan tanpa bertanya dan tidak pernah butuh terminal interaktif.

Dengan aipager, orang itu bisa mengendalikan sesi Claude Code dari Telegram. Setelah selesai, orang itu punya bot Telegram yang hanya bicara dengannya, dan aipager berjalan di latar belakang di komputer ini.

## <a id="1-what-only-the-person-can-do"></a>1. Yang hanya bisa dilakukan oleh orangnya sendiri

Minta orangnya melakukan ini. Kamu tidak bisa melakukannya untuk mereka.

1. **Membuat bot.** Di Telegram, buka [@BotFather](https://t.me/BotFather), kirim `/newbot`, pilih nama dan username. BotFather membalas dengan token bot (bentuknya seperti `123456789:AA...`).
2. **Mengetuk Start di bot.** Buka bot baru itu (BotFather memberi tautan `t.me/<bot username>`) dan ketuk Start. Bot tidak bisa mengirim pesan ke orang yang belum melakukannya.
3. **Masuk ke Claude Code di komputer ini.** Kalau orang itu sudah memakai Claude Code di sini, tidak perlu melakukan apa pun. Di server tanpa layar, mereka menjalankan `claude` sekali di terminal mereka dan masuk (akan ada tautan yang perlu dibuka di perangkat lain). Kalau lebih suka token berumur panjang, mereka menjalankan `claude setup-token`, yang hanya mencetak token: orang itu sendiri yang menyimpannya untuk sesi aipager, di terminal mereka (tempel token, tekan Enter, lalu Ctrl-D):

   ```sh
   mkdir -p ~/.config/aipager
   (umask 077 && { printf 'CLAUDE_CODE_OAUTH_TOKEN='; cat; } >> ~/.config/aipager/daemon.env)
   ```

   aipager meneruskan token ini ke setiap sesi yang dimulainya (lihat [keamanan](security.md#the-claude-credential-what-actually-protects-it)). Kamu tidak pernah membacanya.

## <a id="2-how-the-person-gives-you-the-token"></a>2. Cara orangnya memberimu token

Siapa pun yang memegang token bot memegang kendali bot itu. Tawarkan dua cara ini ke orangnya dan biarkan mereka memilih.

- **Paling aman: file yang tidak pernah kamu baca.** Di terminal mereka sendiri, orang itu menjalankan:

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token)
  chmod 600 ~/.config/aipager/bot-token
  ```

  lalu menempelkan token, menekan Enter, dan menekan Ctrl-D. Hanya orang itu yang bisa membaca file tersebut (izin 600). Setelah pengaturan berhasil, file itu boleh dihapus (aipager menyimpan salinannya sendiri di konfigurasinya). Orang itu memberitahumu path-nya (`~/.config/aipager/bot-token`). Token tidak pernah masuk ke percakapanmu, dan kamu memberikan path itu ke `--token-file`.
- **Paling cepat: menempelkannya di percakapan.** Ini juga boleh, tapi jelaskan artinya: token akan tersimpan di riwayat percakapanmu dan terkirim ke penyedia modelmu. Kalau riwayat itu suatu saat bisa dilihat orang lain, sebaiknya orang itu nanti mengirim `/revoke` ke @BotFather dan memasang token baru dengan `aipager setup --force` (langkah 3.2, dengan `--force`). Kalau orang itu memilih cara ini, simpan token ke file yang sama tanpa mencetaknya: paling baik dengan alat penulis file milikmu, lalu `chmod 600 ~/.config/aipager/bot-token`. Kalau kamu hanya punya shell, berikan lewat stdin memakai here-document, supaya token tidak menjadi argumen perintah yang menulisnya:

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token) <<'EOF'
  <the token>
  EOF
  chmod 600 ~/.config/aipager/bot-token
  ```

  Lalu pakai `--token-file` seperti di bawah.

ID Telegram orang itu (ID chat-nya, angka seperti `123456789`) bukan rahasia. Kalau mereka tidak tahu, kamu bisa mencarinya (langkah 3.3).

## <a id="3-your-steps"></a>3. Langkahmu

### <a id="31-install-aipager"></a>3.1 Memasang aipager

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

Skrip ini memakai uv atau pipx, mana pun yang ditemukannya (di macOS juga Homebrew), dan memasang uv dulu kalau tidak ada satu pun. Kamu juga bisa menjalankan `uv tool install aipager` atau `pipx install aipager` sendiri. Kalau setelah itu `aipager` tidak ditemukan, tambahkan `~/.local/bin` ke `PATH` shell-mu (`export PATH="$HOME/.local/bin:$PATH"`).

Kalau aipager sudah terpasang sebelumnya, perintah ini tidak memperbaruinya. Periksa bahwa `aipager setup --help` berjalan; kalau tidak, aipager yang terpasang terlalu lama: jalankan `aipager update` lalu periksa lagi. Kalau `aipager update` bilang aipager perlu dimulai ulang, sampaikan ke orangnya dan jangan memulai ulangnya sendiri (lihat 4).

Jangan jalankan `aipager config`: itu wizard interaktif untuk manusia.

### <a id="32-set-aipager-up"></a>3.2 Mengatur aipager

```sh
aipager setup --token-file ~/.config/aipager/bot-token --chat-id 123456789 --service --json
```

Pakai ID asli orang itu dan path file token-nya. Sebagai ganti `--token-file`, kamu bisa mengalirkan token lewat pipe dengan `--token-stdin` (misalnya `--token-stdin < path`); pipe-nya harus tertutup, setup menunggunya paling lama 30 detik.

setup memeriksa token di Telegram, memeriksa komputer ini, mengirim satu pesan tes ke orang itu (“aipager is set up for you. ...”), dan baru setelah itu menulis konfigurasinya, menambahkan hook Claude Code ke `~/.claude/settings.json` (atau `$CLAUDE_CONFIG_DIR/settings.json`), dan, dengan `--service`, memasang serta menjalankan layanan latar belakang. Kalau ada pemeriksaan yang gagal, tidak ada yang ditulis.

Tambahkan `--dry-run` untuk melihat dulu apa yang akan berubah. `--role admin` memberi orang itu peran admin sebagai ganti peran owner (bawaannya, owner, tepat untuk orang yang mengatur komputernya sendiri).

### <a id="33-if-the-person-does-not-know-their-telegram-id"></a>3.3 Kalau orangnya tidak tahu ID Telegram-nya

```sh
aipager setup detect-chat --token-file ~/.config/aipager/bot-token --json
```

Minta orang itu mengetuk Start di bot (atau mengirim pesan apa pun ke bot) selama perintah ini menunggu (sampai 5 menit; `--timeout SECONDS` mengubahnya). Perintah ini mencetak pengirim terbaru di `candidate` (`id`, `first_name`, `last_name`, `username`) dan tidak menyimpan apa pun. Tunjukkan nama dan username itu ke orangnya dan tanyakan “Ini kamu?”. Hanya kalau mereka menjawab ya, jalankan langkah 3.2 dengan `--chat-id` yang sama dengan `candidate.id`. Kalau `other_candidates` bukan 0, ada orang lain juga yang mengirim pesan ke bot: periksa dengan sangat hati-hati.

### <a id="34-read-the-result"></a>3.4 Membaca hasilnya

Dengan `--json`, setup mencetak tepat satu objek JSON ke stdout. Baca `exit_code` (sama dengan kode keluar proses), `status`, `error`, `message`, `fix`, dan `next_step`. Kalau stdout bukan JSON (flag yang tidak dikenal, kata yang salah setelah `setup`, atau aipager yang terlalu lama sehingga tidak punya `setup`), kode keluarnya 2 dan alasannya tertulis sebagai teks biasa di stderr: baca itu, dan untuk aipager yang lama lihat 3.1.

| Keluar | `error` | Yang kamu lakukan |
|---|---|---|
| 0 | | `installed`, `updated`, atau `unchanged`: selesai, lanjut ke 3.5. `dry_run`: tidak ada yang ditulis; jalankan perintah yang sama tanpa `--dry-run`. detect-chat `found`: minta orangnya mengonfirmasi seperti di 3.3, lalu jalankan 3.2. |
| 1 | `config_malformed`, `ambiguous_install`, `settings_invalid`, `test_send_failed`, `write_failed`, `service_failed`, `internal_error` | Tunjukkan `message` dan `fix` ke orangnya. `ambiguous_install`: orangnya memperbaikinya di `aipager config`. Jangan mengedit file dengan tangan. `settings_invalid` yang meminta `CLAUDE_CONFIG_DIR` absolut: `fix` menyebut file yang harus diubah (`daemon.env` atau file pengaturan Claude Code); tanyakan foldernya ke orang itu, ubah satu nilai itu saja dan hanya kalau dia bilang, lalu jalankan setup lagi. |
| 2 | `usage`, `token_malformed`, `token_stdin_timeout`, `bad_chat_id`, ... | Input-mu salah; `fix` menyebutkan flag-nya. `token_malformed`: file itu tidak berisi token bot, minta orangnya memeriksanya. |
| 3 | `deps_missing` | Ikuti `fix` (setiap item yang hilang punya `fix` sendiri di `deps.items`), lalu jalankan setup lagi. |
| 4 | `token_rejected` | Telegram menolak token itu. Minta token lagi ke orangnya (atau yang baru dari @BotFather). |
| 5 | `chat_not_started`, `bot_blocked` | Minta orangnya membuka bot dan mengetuk Start (atau membuka blokirnya), lalu jalankan setup lagi. |
| 6 | `existing_install` | aipager sudah diatur dengan token, chat, atau peran lain. Beri tahu orangnya apa yang berbeda dan tanyakan dulu sebelum menambahkan `--force`. |
| 7 | `telegram_unreachable` | Masalah jaringan, atau Telegram sedang sibuk. Tunggu semenit lalu coba lagi. |
| 8 | `detect_timeout` | detect-chat tidak melihat pesan apa pun. Minta orangnya mengetuk Start lagi lalu jalankan ulang. Dengan peringatan `updates_backlog`, mengetuk Start tidak akan membantu: tanyakan ID numerik mereka. |
| 9 | `daemon_running` | detect-chat: aipager untuk bot ini sudah berjalan dan menerima pesannya. Tanyakan ID numerik orangnya (atau minta izin dulu sebelum menghentikannya dengan `aipager service stop`). |
| 10 | `updates_conflict` | detect-chat: ada program lain yang membaca pesan bot ini. Tanyakan ID numerik orangnya. |
| 130 | `interrupted` | Dihentikan dengan Ctrl-C. |

Kalau `daemon.restart_needed` bernilai true, aipager perlu dimulai ulang supaya perubahannya berlaku, atau setup tidak bisa mengetahui apakah aipager sedang berjalan (peringatan `daemon_unknown`). Ikuti `next_step`, yang menyebutkan yang mana (kalau `next_step` menyarankan `aipager start`, yang tetap berjalan di latar depan, pilih `aipager service install` saja). Beri tahu orangnya dan mulai ulang (`aipager service stop`, lalu `aipager service start`) hanya kalau mereka meminta, dan jangan pernah dari dalam sesi yang dikendalikan orang itu lewat aipager (lihat 4). Daftar lengkap error, peringatan, dan kunci JSON ada di [referensi perintah setup](commands.md#command-line-aipager-setup-for-coding-agents).

### <a id="35-check-the-install"></a>3.5 Memeriksa pemasangan

```sh
aipager doctor --json
```

Perintah ini hanya membaca dan tidak mengirim apa pun. Keluar 0 berarti tidak ada pemeriksaan yang gagal. Untuk setiap pemeriksaan dengan `status` bernilai `fail` (atau `warn`), tunjukkan `title`, `detail`, dan `fix`-nya ke orangnya.

### <a id="36-report-back-to-the-person"></a>3.6 Melapor ke orangnya

Sampaikan dalam beberapa baris:

- botnya (`@` + `bot_username`) dan apakah pesan tes terkirim (`test_message`);
- apa saja yang diatur (`changed` dan `service.result`);
- cara memulai sesi Claude: kirim `/new` ke bot di Telegram, atau jalankan `aipager session <name>` di terminal;
- apa pun yang ditandai `doctor`, dan `next_step` kalau isinya meminta sesuatu.

## <a id="4-what-you-must-not-do"></a>4. Yang tidak boleh kamu lakukan

- Jangan mencetak token, mengalirkannya lewat `echo` atau `cat`, atau menaruhnya di baris perintah (`--token` toh akan ditolak). Berikan hanya lewat file atau stdin.
- Jangan mengedit `~/.config/aipager/` (selain file token dari langkah 2) atau `~/.claude/settings.json` dengan tangan. Yang menulisnya adalah `aipager setup` dan `aipager config`.
- Jangan menambahkan `--force` tanpa bertanya ke orangnya: flag ini mengganti token, chat, atau peran yang sudah ada.
- Jangan memulai ulang atau mengakhiri sesi Claude Code-mu sendiri. Pada pemasangan pertama, sesi tempatmu bekerja tidak berada di bawah aipager, dan memang tidak perlu: sesi baru datang lewat `/new` di Telegram atau `aipager session <name>`. Kalau aipager sudah diatur dan orang itu bicara denganmu lewat aipager (misalnya untuk menjalankan `setup --force` dengan token baru), jangan menghentikan atau memulai ulang aipager sendiri: tugasmu dan jawabannya akan terputus. Minta orang itu memulai ulang aipager setelah kamu selesai.

## <a id="5-groups"></a>5. Grup

`aipager setup` mengatur chat pribadi orang itu dengan bot. Untuk menambahkan grup Telegram dengan orang lain dan peran mereka, orang itu menjalankan `aipager config` (pengaturan grup belum bisa diskripkan). Lihat [Mode tim dan grup](groups.md).

## <a id="see-also"></a>Lihat juga

- [Perintah bot dan perintah setup](commands.md)
- [Pemecahan masalah](troubleshooting.md)
- [Model keamanan](security.md)
