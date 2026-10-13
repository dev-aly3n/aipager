# aipager

<!-- i18n-source: README.md @ a175bc5 -->

> Halaman ini diterjemahkan dari bahasa Inggris; kalau keduanya berbeda, versi bahasa Inggris yang berlaku. ([English](../../../README.md))

[![PyPI](https://img.shields.io/pypi/v/aipager?label=pypi&color=3775A9)](https://pypi.org/project/aipager/)
[![Python](https://img.shields.io/pypi/pyversions/aipager?color=3775A9)](https://pypi.org/project/aipager/)
[![License](https://img.shields.io/badge/license-MIT-black)](../../../LICENSE)

Remote control lewat Telegram untuk sesi [Claude Code](https://claude.com/claude-code). Claude berjalan di komputermu dalam terminal yang terlepas (`dtach`), dan kamu memakainya dari ponsel: membaca jawaban Claude, mengirim pesan, menjawab permintaan izin dan pertanyaannya, serta berpindah antarsesi, tanpa perlu menjaga koneksi SSH tetap terbuka.

[aipager.run](https://aipager.run) · [Dokumentasi](#docs) · [Catatan perubahan](../../../CHANGELOG.md) · [Issue di GitHub](https://github.com/dev-aly3n/aipager/issues)

**Baca dalam bahasa lain:** [English](../../../README.md) · [Español](../es/README.md) · [Русский](../ru/README.md) · [Português (Brasil)](../pt-BR/README.md) · [简体中文](../zh-Hans/README.md) · [Français](../fr/README.md) · [Deutsch](../de/README.md) · [한국어](../ko/README.md) · [العربية](../ar/README.md) · [Українська](../uk/README.md) · [Italiano](../it/README.md) · [Türkçe](../tr/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

## <a id="install"></a>Pemasangan

Punya agen coding (Claude Code atau agen apa pun yang bisa memakai shell)? Buat bot lewat [@BotFather](https://t.me/BotFather), ketuk Start di bot itu, dan pastikan Claude Code di komputer ini sudah masuk ke akunmu. Simpan token bot di file yang tidak perlu dibaca oleh agen (tempel token, tekan Enter, lalu Ctrl-D):

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

Lalu beri agenmu pesan ini:

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

Kalau kamu tidak tahu ID Telegram-mu, bilang saja ke agen; ia bisa menemukannya.

Petunjuk yang diikuti agen, termasuk cara aman memberinya token, ada di [Pemasangan dengan agen](install-with-an-agent.md). Untuk memasang sendiri, lanjutkan membaca.

Linux atau macOS, arsitektur apa pun. `dtach` terpasang otomatis lewat dependensi [`dtach-bin`](https://pypi.org/project/dtach-bin/): tidak perlu paket sistem terpisah.

### <a id="one-line-install-recommended"></a>Pemasangan satu baris (disarankan)

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

Skrip ini memakai `uv` kalau ada, kalau tidak `pipx`, kalau tidak juga (hanya di macOS) Homebrew. Kalau semuanya tidak ada, skrip ini memasang `uv` dulu (pengelola alat Python dari Astral) lalu memasang aipager dengannya.

### <a id="uv-recommended-on-macos"></a>uv (disarankan di macOS)

```sh
uv tool install aipager     # kalau uv sudah terpasang
```

Atau, untuk memasang uv dulu:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install aipager
```

uv membawa interpreter Python-nya sendiri, jadi ini berjalan di versi macOS atau Linux mana pun, apa pun keadaan Python sistem; dengan begitu kamu menghindari bentrok antara Python dari Homebrew dan Xcode yang dijelaskan di bagian Homebrew di bawah.

### <a id="pipx"></a>pipx

```sh
pipx install aipager
```

### <a id="homebrew-tap-macos-linuxbrew"></a>Homebrew tap (macOS, Linuxbrew)

> **Catatan:** di macOS jalur yang disarankan adalah `uv tool install aipager`. Formula brew berjalan kalau paket `python@3.12` dari Homebrew dan Xcode / Command Line Tools milikmu cocok, tapi sesekali keduanya tidak cocok; terakhir kali di **macOS Tahoe (26.x)**, pemasangan gagal dengan error simbol `pyexpat _XML_SetAllocTrackerActivationThreshold` ([issue upstream](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). Memperbarui Xcode dan Command Line Tools biasanya menyelesaikannya, tapi memakai uv lebih mudah.

```sh
brew install dev-aly3n/tap/aipager
```

Mengambil `dtach` dari formula standar Homebrew dan memasang aipager di venv Python yang dikelola Homebrew.

### <a id="docker"></a>Docker

Image siap pakai yang sudah berisi python, node, `claude`, dan `dtach`; praktis untuk VPS, NAS, atau Raspberry Pi tempat kamu tidak mau memasang Python atau Node di sistem. Multi-arsitektur (amd64, arm64).

```sh
# 1. Jalankan wizard pengaturan sekali (interaktif)
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. Jalankan aipager (di latar belakang, dengan mulai ulang otomatis)
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

Pasang (mount) folder yang ingin Claude ubah di `/workspace`. Memasang `~/.claude` membawa kredensial Claude dan riwayat percakapanmu; untuk masuk, jalankan `claude` sekali di sistem, atau `docker exec -it aipager claude` untuk masuk dari dalam kontainer.

Tag: `latest`, `1.2`, `1.2.0` (satu untuk versi persis dan satu untuk versi minor).

### <a id="nix-flake"></a>Nix flake

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

Membangun aipager dari sumber dengan dependensi dari nixpkgs yang dikunci versinya. `dtach` disediakan oleh Nix; `claude` **tidak**: pasang terpisah (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`, atau ikuti dokumentasi Anthropic).

Untuk konfigurasi deklaratif NixOS atau Home Manager, tambahkan aipager sebagai input flake dan ambil paketnya di `environment.systemPackages`:

```nix
{
  inputs.aipager.url = "github:dev-aly3n/aipager";

  outputs = { self, nixpkgs, aipager, ... }: {
    nixosConfigurations.myhost = nixpkgs.lib.nixosSystem {
      modules = [({ pkgs, ... }: {
        environment.systemPackages = [
          aipager.packages.${pkgs.system}.default
        ];
      })];
    };
  };
}
```

Setelah itu `aipager service install` memasang layanan pengguna systemd.

### <a id="snap"></a>Snap

```sh
snap install aipager
```

Snap dengan isolasi ketat yang berisi python, node, `claude`, `dtach`, dan aipager. Karena sandbox snap, folder kerja harus berada di dalam `~/` (misalnya `~/projects/foo`). Manifest-nya ada di [`packaging/snap/`](../../../packaging/snap/).

## <a id="configure"></a>Pengaturan

```sh
aipager config
```

Wizard interaktif: menanyakan token bot Telegram-mu (dari [@BotFather](https://t.me/BotFather)) dan ID chat, memeriksa keduanya, lalu menambahkan sendiri hook yang diperlukan ke `~/.claude/settings.json`. Kamu tidak perlu mengedit file apa pun dengan tangan.

Memasang aipager untuk orang lain dari skrip atau lewat agen coding? `aipager setup --token-file FILE --chat-id ID` melakukan hal yang sama tanpa bertanya (lihat [perintah setup](commands.md#command-line-aipager-setup-for-coding-agents) dan [petunjuk untuk agen](install-with-an-agent.md)).

## <a id="run"></a>Menjalankan

```sh
aipager start
```

aipager tetap berjalan di latar depan. Mulai sesi Claude di terminal lain:

```sh
aipager session dev
```

Ini membuat sesi dtach bernama `claude-dev` yang menjalankan Claude Code (atau menyambung ke sesi itu kalau sudah ada). aipager menemukannya dalam beberapa detik dan Telegram mulai menampilkannya. Sesi juga bisa dimulai dari Telegram: kirim `/new dev` ke bot. Untuk menyambung lagi nanti, jalankan perintah yang sama; untuk keluar tanpa menghentikan Claude, tutup terminal atau panel tmux (sesinya tetap berjalan).

Kalau sesi dtach sudah tertutup (misalnya komputer dinyalakan ulang) dan kamu ingin melanjutkan percakapan Claude dari disk, tambahkan `--continue`:

```sh
aipager session dev --continue    # percakapan claude terakhir di folder ini
```

Kamu juga bisa menambahkan `--resume <session-id>` (atau flag claude lain) di akhir:

```sh
aipager session dev -- --resume abc1234
```

### <a id="run-as-a-service-survives-logout"></a>Menjalankan sebagai layanan (tetap berjalan setelah logout)

```sh
aipager service install
```

Di Linux, ini menulis layanan pengguna systemd ke `~/.config/systemd/user/aipager.service` lalu menjalankannya. Di macOS, ini menulis plist launchd ke `~/Library/LaunchAgents/com.aipager.daemon.plist` lalu menjalankannya. Subperintah: `start`, `stop`, `status`, `logs`, `uninstall`.

## <a id="team-mode-groups-and-other-people"></a>Mode tim (grup dan orang lain)

Secara bawaan aipager adalah bot pribadi: kamu dan bot dalam satu chat pribadi. Untuk berbagi, tambahkan grup Telegram, chat pribadi orang lain dengan bot, atau keduanya. Untuk grup: tambahkan bot ke grup (sebagai admin, supaya bot bisa menyematkan bilah status grup), jalankan lagi `aipager config`, lalu pilih **Add a group scope**. Wizard menyarankan kamu (pemilik chat pribadimu sendiri) sebagai pemilik grup, menambahkan setiap anggota dengan sebuah peran (`owner`, `admin`, `user`, atau `read_only`), menanyakan folder utama para anggota, lalu memuat ulang pengaturan aipager tanpa memulai ulang. Di grup, bot hanya merespons perintah, balasan ke pesannya, dan sebutan (`@aipagerbot fix the tests`, `/jim run the tests`), dan setiap pesan berjalan dengan peran pengirimnya. Anggota dengan `user` atau `read_only` memulai dan menyimpan sesi hanya di folder utama chat-nya (bawaannya `~/aipager-users/<label>`). **Menambahkan anggota berarti memberinya hak menjalankan kode di komputermu**: model kepercayaan lengkapnya ada di [Mode tim dan grup](groups.md).

## <a id="what-it-does"></a>Apa yang dilakukannya

- Menampilkan di Telegram apa yang sedang dikerjakan setiap sesi Claude Code: sedang bekerja atau siap, alat apa yang dipakai, % konteks, biaya, dan baris yang berubah, dalam satu kartu per tugas, lalu jawaban Claude
- Mengirim pesanmu ke Claude **langsung**, bahkan saat ia sedang bekerja: kirim beberapa, dan Claude sendiri yang mengantrekannya, seperti saat mengetik di terminal. 👀 berarti terkirim, 👍 berarti Claude sudah mengambilnya
- Menahan pesan selama ada permintaan izin atau pertanyaan yang terbuka, supaya pesan itu tidak pernah dibaca sebagai jawabannya, lalu mengirimnya setelah kamu menjawab
- Menampilkan permintaan izin dan pertanyaan Claude dengan tombol (Izinkan, Tolak, Selalu izinkan, satu per opsi); tombol yang tersisa dari tugas yang sudah selesai akan menolak, bukan memengaruhi pekerjaan yang sedang berjalan
- Tiga mode izin per sesi, diganti dengan `/mode`: 💬 Mode Tanya (Claude bertanya sebelum setiap tindakan), 🤖 Mode Auto, bawaan (pemeriksaan keamanan Claude Code sendiri menjalankan tindakan aman dan menghentikan yang berisiko supaya kamu yang memutuskan), dan 🔓 Mode Akses penuh (semuanya berjalan tanpa pemeriksaan apa pun; untuk admin dan untukmu di chat pribadimu sendiri)
- Memulai sesi dari chat: `/new x1` langsung memulai satu (`/new x1 fix the tests` sekaligus mengirim pesan pertama), dengan mode, model, dan folder dari `/settings` dan tombol untuk menggantinya
- Menerima foto, file, dan pesan suara (pesan suara diubah menjadi teks di komputermu, dengan paket tambahan pesan suara)
- Memberi tahu saat konteks hampir penuh, saat percakapan diringkas, saat sebuah sesi berakhir, dan saat ada sesi yang lama tidak memberi kabar
- Mengelola beberapa sesi sekaligus lewat satu bot; opsional, mode tim dengan peran dan aturan per alat ([Mode tim dan grup](groups.md))
- Berbicara dalam English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی, dan Tiếng Việt: hanya teks aipager sendiri, tidak pernah sesimu atau jawaban Claude ([Bahasa](commands.md#languages)). README ini dan dokumentasinya juga sudah diterjemahkan (lihat **Baca dalam bahasa lain** di atas)

### <a id="mini-app"></a>Mini App

`/app` membuka dasbor di dalam Telegram: sesi-sesimu dengan Hentikan, Akhiri, Mulai ulang, Ganti nama, dan pergantian mode, tampilan diff untuk perubahan `Write`/`Edit`, pengaturan, dan, untuk pemilik, “Laporkan masalah”. Dasbor ini disajikan oleh aipager sendiri dan **menyala secara bawaan**; setiap permintaan diperiksa dengan tanda tangan `initData` dari Telegram. Kalau kamu ada di lebih dari satu chat (chat pribadimu dan sebuah grup), baris nama chat di bagian atas berpindah di antara keduanya. Kelola lewat `aipager miniapp enable|disable|status`, atau arahkan ke alamatmu sendiri sebagai ganti tunnel yang dikelola: lihat [Model keamanan](security.md#mini-app-tunnel).

### <a id="note-on-model-buttons-bedrock--vertex-users"></a>Tentang tombol model (pengguna Bedrock dan Vertex)

Submenu **Model** di keyboard tetap (dan pemilih model di Mini App) menawarkan alias keluarga Claude Code, `sonnet`, `opus`, `haiku`, `fable`, dan `opusplan`, serta model yang dikunci seperti `claude-opus-5-5` dan `claude-sonnet-5-5` (daftar lengkap di [Keyboard tetap](commands.md#persistent-keyboard)). Di API Anthropic, sebuah alias selalu menunjuk ke model terbaru di keluarganya. Di **Bedrock** dan **Vertex**, alias yang sama bisa menunjuk ke versi lebih lama, tergantung apa yang tersedia di penyediamu. Kalau kamu memakai layanan itu dan butuh model tertentu, kirim `/model <full-id>` dari chat.

## <a id="developing-locally"></a>Mengembangkan di komputermu

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach` ikut datang bersama dependensi `dtach-bin`, tidak perlu yang lain. Jalankan tes dengan batas memori seperti di atas (di macOS tanpa `systemd-run`): kalau tidak, satu tes yang lepas kendali bisa menghabiskan seluruh memori komputer.

Proses rilis dijelaskan di [CONTRIBUTING.md](../../../CONTRIBUTING.md).

## <a id="license"></a>Lisensi

MIT; teks lengkapnya di [LICENSE](../../../LICENSE).

## <a id="docs"></a>Dokumentasi

| Halaman | Isi |
|---|---|
| [Pemasangan dengan agen](install-with-an-agent.md) | Langkah yang diikuti agen coding untuk memasang dan mengatur aipager bagi orangnya |
| [Arsitektur](architecture.md) | Cara program disusun, diagram bagian-bagiannya, letak file dan soket |
| [Event hook](hooks.md) | Setiap event yang diterima aipager dari Claude Code, beserta field-nya |
| [Perintah bot](commands.md) | Perintah, tombol, Mini App, pesan suara dan file, semua perintah `aipager`, dan pengaturan dari environment |
| [Mode tim dan grup](groups.md) | Pengaturan untuk beberapa orang, peran, aturan, siapa melakukan apa |
| [Pemecahan masalah](troubleshooting.md) | Kegagalan yang sering terjadi dan tabel solusi `aipager doctor` |
| [Laporan masalah](problem-reports.md) | Isi laporan masalah, ke mana laporan dikirim, dan kapan aipager menawarkannya |
| [Model keamanan](security.md) | Batas kepercayaan, rahasia, log audit, koneksi jaringan |
