# Bot pengamat

<!-- i18n-source: docs/observers.md @ 6fc084f -->

> Halaman ini diterjemahkan dari bahasa Inggris; kalau keduanya berbeda, versi bahasa Inggris yang berlaku. ([English](../../observers.md))

Bot Telegram yang hanya membaca dan menerima salinan notifikasi bot utama. Bot ini menerima ringkasan, peringatan, dan error, tapi tidak bisa mengendalikan sesi.

## <a id="setup"></a>Pengaturan

1. Buat bot lewat [@BotFather](https://t.me/BotFather)
2. Buka chat dengan bot itu dan kirim `/start`
3. Cari ID chat-mu (kirim sebuah pesan, lalu buka `https://api.telegram.org/bot<TOKEN>/getUpdates`)
4. Atur `OBSERVER_BOTS` di environment aipager. Untuk aipager di latar depan, export variabelnya sebelum `aipager start`; untuk layanan systemd:

```sh
systemctl --user edit aipager    # tambahkan di bagian [Service]:
# Environment=OBSERVER_BOTS=<bot_token>:<chat_id>
```

Beberapa pengamat (dipisahkan koma):

```
OBSERVER_BOTS=111:AAA_first:12345,222:BBB_second:67890
```

Formatnya `token:chat_id`: saat dibaca, titik dua **terakhir** yang dipakai sebagai pemisah (token bot sudah berisi titik dua sendiri).

5. Mulai ulang aipager

## <a id="what-observers-receive"></a>Apa yang diterima pengamat

Pengamat menerima notifikasi ini dari **setiap chat yang dilayani bot**, setiap chat pribadi dan setiap grup, tanpa filter: pasang pengamat hanya untuk chat yang bisa dipercaya dengan semua itu. Setiap notifikasi datang dalam bahasa chat asalnya.

| Event | Contoh |
|-------|---------|
| Tugas selesai | `💬 x1 · Selesai (2:05, +12 -3)`: hanya baris hasilnya, bukan jawabannya; ditambah log lengkap sebagai file `.md` kalau jawaban atau kartunya harus dipotong |
| Pekerjaan latar belakang selesai | `✅ x1 · Selesai (...)`, atau `⚠️ x1 · Selesai (...)` kalau ada agen yang hilang atau akhir sebuah perintah latar belakang tidak pernah terlihat |
| Error API | `⚠️ x1 · Server Anthropic sedang sangat sibuk. Coba lagi sebentar lagi.` (tanpa tombol Coba lagi) |
| Peringatan konteks | `⚠️ x1 · Konteks terisi 82% - sebentar lagi diringkas otomatis` |
| Peringkasan berjalan | `🔄 x1 · Meringkas percakapan` |
| Peringkasan selesai | `📦 x1 · Percakapan diringkas: 82% → 4%` |
| Permintaan masih menunggu | `⬆️ x1 · masih menunggu jawabanmu di atas`, dengan izin atau pertanyaan yang ditunggunya |
| Sesi yang bekerja lama tidak memberi kabar | `⏳ x1 · masih bekerja (10 menit tanpa kabar)` |
| Pesan yang tidak diterima Claude Code | `⚠️ x1 · Claude Code tidak menerima pesan` |
| Sesi berakhir | `🔴 x1 · Sesi berakhir` |

## <a id="what-observers-dont-receive"></a>Yang TIDAK diterima pengamat

- Animasi kartu kerja
- Pembaruan pemanggilan alat
- Permintaan izin dan tombolnya (hanya pengingat “menunggu” di atas yang menyebutkan apa yang ditanyakan permintaan itu)
- Dialog AskUserQuestion
- Keyboard atau tombol apa pun

Pengamat tidak menyimpan keadaan apa pun: pengiriman dilakukan tanpa menunggu balasan. Pengamat yang rusak tidak pernah memengaruhi bot utama.
