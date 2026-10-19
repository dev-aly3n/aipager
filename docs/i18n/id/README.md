<div align="center">

<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="96" height="96" alt="aipager"></a>

# aipager

<!-- i18n-source: README.md @ 2d10247 -->

<h3>Claude jadi satu chat lagi.</h3>

Sesi [Claude Code](https://claude.com/claude-code) kamu di Telegram, di samping keluarga, teman, dan timmu.<br>
Semuanya berjalan di komputermu sendiri. Kamu bisa memantau, menjawab, dan memberi tugas berikutnya dari mana saja.

<p>
<a href="https://pypi.org/project/aipager/"><img alt="PyPI" src="https://img.shields.io/pypi/v/aipager?style=flat-square&label=pypi&labelColor=181c20&color=2aabee"></a>
<a href="https://pypi.org/project/aipager/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/aipager?style=flat-square&labelColor=181c20&color=2aabee"></a>
<a href="https://github.com/dev-aly3n/aipager/actions/workflows/test.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/dev-aly3n/aipager/test.yml?branch=main&style=flat-square&label=tests&labelColor=181c20"></a>
<img alt="Linux and macOS" src="https://img.shields.io/badge/runs%20on-Linux%20%7C%20macOS-2aabee?style=flat-square&labelColor=181c20">
<a href="../../../LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-2aabee?style=flat-square&labelColor=181c20"></a>
</p>

**[Situs](https://aipager.run)** · **[Pemasangan](#install)** · **[Dokumentasi](#documentation)** · **[Keamanan](security.md)** · **[Catatan perubahan](../../../CHANGELOG.md)**

**Baca dalam bahasa lain:** [English](../../../README.md) · [Español](../es/README.md) · [Русский](../ru/README.md) · [Português (Brasil)](../pt-BR/README.md) · [简体中文](../zh-Hans/README.md) · [Français](../fr/README.md) · [Deutsch](../de/README.md) · [한국어](../ko/README.md) · [العربية](../ar/README.md) · [Українська](../uk/README.md) · [Italiano](../it/README.md) · [Türkçe](../tr/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

<sub>Halaman ini diterjemahkan dari bahasa Inggris; kalau keduanya berbeda, versi bahasa Inggris yang berlaku. ([English](../../../README.md))</sub>

<br>

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/hero.webp" width="100%" alt="Pesan yang diketik di Telegram sampai ke Claude Code di terminal, dan satu kartu di chat memperbarui dirinya sendiri selama Claude bekerja">

<sub>Kamu menulis di Telegram. aipager mengetiknya ke Claude Code di komputermu, dan satu kartu memperbarui dirinya sendiri selama tugas berjalan.</sub>

</div>

<br>

## <a id="why-aipager"></a>Kenapa aipager

<table>
<tr>
<td width="33%" valign="top">

**📟 Satu kartu langsung per tugas**<br>
Apa yang sedang dikerjakan Claude saat ini: alat yang dijalankannya, konteks, biaya, dan baris yang berubah, lalu jawabannya. Kartunya memperbarui diri sendiri, jadi chat tetap tenang.

</td>
<td width="33%" valign="top">

**⚡ Ajak bicara di tengah tugas**<br>
Pesanmu langsung diketik ke sesi, bahkan saat Claude sedang bekerja, persis seperti mengetik di terminal. 👀 berarti terkirim, 👍 berarti Claude sudah mengambilnya.

</td>
<td width="33%" valign="top">

**✅ Jawab dengan sekali ketuk**<br>
Permintaan izin dan pertanyaan Claude datang dengan satu tombol untuk tiap jawaban. Tombol yang tersisa dari tugas yang sudah selesai akan menolak, bukan memengaruhi tugas berikutnya.

</td>
</tr>
<tr>
<td width="33%" valign="top">

**🛡️ Tiga mode izin**<br>
💬 Mode Tanya, 🤖 Mode Auto (bawaan untuk sesi baru), dan 🔓 Mode Akses penuh, diatur per sesi dan bisa diganti kapan saja dengan `/mode`.

</td>
<td width="33%" valign="top">

**👥 Dibuat untuk tim**<br>
Masukkan bot ke grup. Setiap pesan berjalan dengan peran pengirimnya, anggota bisa dibatasi ke folder mereka sendiri, dan setiap ketukan tercatat di log audit.

</td>
<td width="33%" valign="top">

**🔒 Tidak ada perantara**<br>
Tanpa server aipager, tanpa akun, tanpa port terbuka. Token bot milikmu, komputermu, chat-mu.

</td>
</tr>
</table>

## <a id="a-quick-tour"></a>Tur singkat

### <a id="questions-come-to-you"></a>Pertanyaan datang kepadamu

Saat Claude menanyakan sesuatu atau butuh izin, pertanyaannya masuk ke chat-mu dengan satu tombol untuk tiap jawaban, dan bilah yang disematkan memberi tahu apa yang menunggumu. Ketukanmu diketik ke sesi seolah kamu duduk di depan keyboard, dan terminal menampilkan jawaban yang sama.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/question.webp" width="100%" alt="Claude bertanya apakah perlu menaikkan timeout atau menunggu cookie; jawaban yang diketuk di Telegram dipilih di terminal">

### <a id="three-permission-modes"></a>Tiga mode izin

Setiap sesi berada di salah satu dari tiga mode, yang bisa diganti kapan saja dengan `/mode`, bahkan saat Claude sedang bekerja.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/modes.webp" width="100%" alt="Tugas yang sama di Mode Tanya, Mode Auto, dan Mode Akses penuh">

| Mode | Yang terjadi |
|---|---|
| 💬 **Mode Tanya**<br>`/mode ask` | Claude bertanya sebelum setiap tindakan. |
| 🤖 **Mode Auto**, bawaan<br>`/mode auto` | Pemeriksaan keamanan Claude Code sendiri menjalankan tindakan yang aman dan menghentikan yang berisiko supaya kamu yang memutuskan. |
| 🔓 **Mode Akses penuh**<br>`/mode full` | Semuanya berjalan tanpa pemeriksaan apa pun. Untuk admin, dan untukmu di chat pribadimu sendiri dengan bot. |

Pindah antara Mode Tanya dan Mode Auto terjadi di tempat, tanpa mulai ulang. Masuk ke Mode Akses penuh atau keluar darinya memulai ulang sesi dan tetap menyimpan percakapan. Pertanyaan dari Claude tetap datang kepadamu di mode apa pun.

### <a id="your-team-in-one-group"></a>Timmu, dalam satu grup

Tambahkan bot ke grup dan beri setiap anggota satu peran: `owner`, `admin`, `user`, atau `read_only`. Di grup, bot hanya menanggapi perintah, balasan ke pesannya, dan sebutan (`@aipagerbot fix the tests`, `/web run the tests`), dan setiap pesan berjalan dengan peran pengirimnya: alat yang tidak boleh dipakai peran itu ditolak sebelum dijalankan. Anggota `user` atau `read_only` hanya bekerja di dalam folder utamanya sendiri (bawaannya `~/aipager-users/<label>`). Orang lain juga bisa punya chat pribadi sendiri dengan bot.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/team.webp" width="100%" alt="Grup tim tempat perintah ditolak karena peran anggota itu, sementara tesnya lolos">

> **⚠️ Penting:** Menambahkan anggota berarti memberinya hak menjalankan kode di komputermu. Baca [model kepercayaannya](groups.md) sebelum membagikan bot.

Untuk menyiapkannya, tambahkan bot ke grup sebagai admin (supaya bisa menyematkan bilah status grup), jalankan `aipager config` lagi, lalu pilih **Add a group scope**. Wizard akan menambahkan setiap anggota dengan perannya, menanyakan folder utama para anggota, dan memuat ulang konfigurasi aipager yang sedang berjalan.

### <a id="a-dashboard-inside-telegram"></a>Dasbor di dalam Telegram

`/app` membuka Mini App: sesi-sesimu dengan Hentikan, Akhiri, Mulai ulang, Ganti nama, dan pengalih mode, tampilan diff untuk setiap `Write` dan `Edit`, pengaturan, dan, untuk pemilik, Laporkan masalah. Mini App disajikan oleh aipager sendiri, aktif secara bawaan, dan setiap permintaan diperiksa dengan tanda tangan Telegram. Kalau kamu ada di lebih dari satu chat, sebaris nama chat di atas bisa memindahkanmu di antaranya. Matikan dengan `aipager miniapp disable`, atau arahkan ke alamatmu sendiri ([caranya](security.md#mini-app-tunnel)).

### <a id="fifteen-languages"></a>Lima belas bahasa

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/languages.webp" width="420" align="right" alt="Permintaan izin dalam bahasa Persia, dari kanan ke kiri, dengan angka Persia">

- Berbicara dalam English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی, dan Tiếng Việt.
- Pilih satu dengan `/language`, atau biarkan chat pribadi mengikuti bahasa aplikasi Telegram-mu.
- Bahasa Persia dan Arab dibaca dari kanan ke kiri, dengan angkanya sendiri.
- Hanya teks aipager sendiri yang berubah. Sesimu tidak: Claude menjawab dalam bahasa yang kamu pakai untuk menulis kepadanya.
- README ini dan [dokumentasinya](#documentation) juga sudah diterjemahkan.

<br clear="right">

### <a id="and-the-small-things"></a>Dan hal-hal kecil

- **Banyak sesi, satu bot.** Pesan biasa masuk ke sesi yang terakhir kamu ajak bicara; balas salah satu pesan dari sesi untuk bicara dengan sesi itu, atau kirim `/x1 <message>`.
- **Tidak ada yang tertukar.** Pesan yang dikirim saat ada permintaan izin atau pertanyaan yang terbuka akan menunggu sampai kamu menjawab, jadi tidak pernah dibaca sebagai jawabannya.
- **Foto, file, dan pesan suara.** Pesan suara diubah menjadi teks di komputermu, dengan tambahan opsional `voice`.
- **aipager memberi tahu.** Konteks yang hampir penuh, percakapan yang diringkas, sesi yang berakhir, dan sesi yang lama tak ada kabar.
- **Sesi bertahan dari apa saja.** Claude berjalan di dalam `dtach`, jadi tetap bekerja saat kamu menutup terminal, kehilangan SSH, atau memulai ulang aipager.

## <a id="how-it-works"></a>Cara kerjanya

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/how-it-works.webp" width="100%" alt="Claude Code, hook-nya, aipager, Telegram, dan chat-mu, berjajar">

Claude Code berjalan di terminal sungguhan, di dalam `dtach`, jadi tidak ada yang perlu membiarkan jendela terbuka. Setiap langkah memicu [hook](hooks.md) yang mengirim satu datagram ke aipager lewat soket lokal, dan aipager mengedit satu pesan di chat-mu. Jawabanmu kembali lewat jalan yang sama: keputusan untuk hook yang sedang menahan panggilan, atau ketukan tombol ke dalam sesi. Terminal tetap menjadi sumber kebenaran. Selengkapnya di [arsitektur](architecture.md).

## <a id="install"></a>Pemasangan

Linux atau macOS, arsitektur apa pun. Kamu butuh Claude Code yang sudah login di komputer ini, dan bot Telegram: buat satu dengan [@BotFather](https://t.me/BotFather) lalu tekan Start di dalamnya.

```sh
curl -fsSL https://aipager.run/install | sh   # uv, pipx, atau Homebrew: yang mana pun yang kamu punya
aipager config                                # token bot, chat-mu, hook Claude Code
aipager service install                       # menjaga aipager tetap jalan setelah kamu logout
aipager session api                           # Claude Code di folder ini, dan di chat-mu
```

Installer memakai `uv` kalau kamu punya, kalau tidak `pipx`, kalau tidak (di macOS) Homebrew, dan memasang `uv` kalau tidak ada satu pun. `dtach` ikut bersama aipager (paket [`dtach-bin`](https://pypi.org/project/dtach-bin/)), jadi tidak ada lagi yang perlu dipasang. `aipager config` adalah wizard yang memeriksa token bot dan chat-mu, lalu menambahkan hook aipager ke `~/.claude/settings.json` untukmu. Kamu tidak pernah perlu mengedit file dengan tangan.

<details>
<summary><b>Biarkan agen coding yang memasangnya</b></summary>

<br>

Punya Claude Code, atau agen apa pun yang punya shell? Buat bot, tekan Start di dalamnya, lalu simpan token bot ke file yang tidak pernah perlu dibaca agenmu (tempel tokennya, tekan Enter, lalu Ctrl-D):

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

Lalu berikan prompt ini ke agenmu:

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

Kalau kamu tidak tahu ID pengguna Telegram-mu, katakan saja; agen bisa menemukannya. Panduan yang diikutinya, termasuk cara menyerahkan token dengan aman, ada di [Pemasangan dengan agen](install-with-an-agent.md). Untuk skrip, `aipager setup --token-file FILE --chat-id ID` menyiapkan aipager tanpa pertanyaan apa pun ([perintah setup](commands.md#command-line-aipager-setup-for-coding-agents)).

</details>

### <a id="other-ways-to-install"></a>Cara lain untuk memasang

```sh
uv tool install aipager                        # uv: disarankan di macOS, membawa Python-nya sendiri
pipx install aipager                           # pipx
brew install dev-aly3n/tap/aipager             # Homebrew, di macOS dan Linuxbrew (lihat catatan di bawah)
nix profile install github:dev-aly3n/aipager   # Nix: claude tidak termasuk (lihat di bawah)
snap install aipager                           # Snap: kurungan ketat, claude sudah ada, folder kerja di dalam ~/
```

Atau jalankan di [Docker](#docker), dengan Python, Node, `claude`, dan `dtach` di dalamnya.

<details>
<summary><b>Homebrew di macOS 26 (Tahoe)</b></summary>

<br>

Formula ini berjalan kalau bottle `python@3.12` dari Homebrew dan Command Line Tools Xcode-mu selaras, tetapi sesekali keduanya tidak cocok. Di macOS 26 pemasangan gagal dengan error simbol `pyexpat _XML_SetAllocTrackerActivationThreshold` ([issue di upstream](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). Memperbarui Xcode dan Command Line Tools biasanya membereskannya, tetapi dengan `uv tool install aipager` masalah ini tidak muncul sama sekali. Formula mengambil `dtach` dari Homebrew dan memasang aipager di lingkungan Python yang dikelola Homebrew.

</details>

### <a id="docker"></a>Docker

Image mandiri berisi Python, Node, `claude`, dan `dtach`, cocok untuk VPS, NAS, atau Raspberry Pi tempat kamu tidak ingin ada Python atau Node di host. Mendukung banyak arsitektur (amd64, arm64).

```sh
# 1. Jalankan wizard penyiapan sekali (interaktif)
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. Jalankan aipager (di latar belakang, mulai ulang otomatis)
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

Mount folder yang ingin diedit Claude di bawah `/workspace`. Mount `~/.claude` membawa kredensial Claude dan riwayat percakapanmu: jalankan `claude` sekali di host untuk login, atau `docker exec -it aipager claude` untuk login di dalam kontainer.

Tag: `latest`, `1.3`, `1.3.8` (satu per versi persis dan satu per versi minor).

### <a id="nix"></a>Nix

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

Membangun aipager dari kode sumber dengan nixpkgs yang dipatok. `dtach` datang dari Nix; `claude` tidak, jadi pasang terpisah (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`, atau ikuti dokumentasi Anthropic).

<details>
<summary><b>NixOS dan Home Manager</b></summary>

<br>

Tambahkan aipager sebagai input flake dan masukkan paketnya ke `environment.systemPackages`:

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

Setelah itu `aipager service install` memasang unit pengguna systemd.

</details>

## <a id="everyday-use"></a>Sehari-hari

### <a id="in-telegram"></a>Di Telegram

| Kirim | Untuk |
|---|---|
| `/status` | Melihat sesi-sesi chat ini, masing-masing dengan status dan tombolnya. |
| `/new x1` | Memulai sesi bernama x1. `/new x1 fix the tests` sekaligus mengirim pesan pertamanya. |
| `/x1` | Menjadikan x1 sesi tujuan pesan-pesanmu. `/x1 <message>` mengirim pesan langsung ke sesi itu. |
| `/stop` | Menghentikan tugas yang sedang berjalan dan membuang yang masih mengantre. |
| `/mode` | Berpindah antara Mode Tanya, Mode Auto, dan Mode Akses penuh. |
| `/app` | Membuka Mini App. |
| `/language` | Memilih bahasa yang dipakai aipager di chat ini. |
| `/update` | Memperbarui aipager dan Claude Code, lalu memulai ulang di tempat (admin). |
| `/help` | Panduan singkat per tugas. |

Semua perintah, tombol, dan pengaturan ada di [Perintah bot](commands.md).

### <a id="on-the-command-line"></a>Di baris perintah

```sh
aipager start                             # aipager sendiri, di latar depan
aipager session dev                       # memulai sesi bernama dev di sini, atau menyambung ke sana
aipager session dev --continue            # melanjutkan percakapan Claude terakhir di folder ini
aipager session dev -- --resume abc1234   # opsi claude lainnya diteruskan apa adanya
aipager doctor                            # memeriksa pemasangan
```

Untuk keluar dari sesi tanpa menghentikan Claude, tutup terminal atau panel tmux: sesi tetap berjalan, dan perintah yang sama menyambungkanmu lagi.

`aipager service install` menjaga aipager tetap berjalan di latar belakang: unit pengguna systemd di Linux (`~/.config/systemd/user/aipager.service`) atau agen launchd di macOS (`~/Library/LaunchAgents/com.aipager.daemon.plist`). Dalam kedua cara, aipager berjalan di folder home-mu, tempat sesi baru dimulai kecuali kamu memilih folder lain. `aipager service start`, `stop`, `status`, `logs`, dan `uninstall` mengelolanya.

## <a id="security"></a>Keamanan

- **Tanpa server aipager.** aipager berbicara dengan Bot API Telegram memakai token bot milikmu sendiri. Tidak ada akun aipager dan tidak ada layanan aipager.
- **Tanpa port terbuka.** aipager hanya mendengarkan di loopback. Mini App keluar lewat tunnel Cloudflare yang setiap permintaannya harus membawa tanda tangan Telegram, dan `aipager miniapp disable` mematikannya.
- **Setiap jawaban tercatat.** Setiap Izinkan, Tolak, dan jawaban ditambahkan ke `~/.claude/aipager-audit.jsonl`, beserta siapa yang mengetuknya di grup.
- **Claude Code tetap yang memutuskan.** Pengaturan Claude Code-mu menentukan apa yang perlu ditanyakan. aipager hanya meneruskan pertanyaannya dan tidak pernah menaikkan hak akses: tanpa sudo, semuanya berjalan sebagai penggunamu.

Gambaran lengkapnya, termasuk apa yang melindungi kredensial Claude-mu, ada di [model keamanan](security.md).

## <a id="faq"></a>Pertanyaan umum

<details>
<summary><b>Apa bedanya dengan aplikasi Claude di ponsel?</b></summary>

<br>

Aplikasi Claude berbicara dengan asisten yang dihosting Anthropic. aipager menghubungkanmu dengan sesi Claude Code yang berjalan di komputermu sendiri: repositorimu, alat-alatmu, lingkunganmu.

</details>

<details>
<summary><b>Apakah kodeku keluar dari komputerku?</b></summary>

<br>

aipager hanya mengirim ke Telegram apa yang kamu lihat di chat-mu, dengan token bot milikmu sendiri; tidak ada server aipager di tengahnya. Claude Code sendiri tetap berbicara dengan Anthropic seperti biasa.

</details>

<details>
<summary><b>Apa itu dtach?</b></summary>

<br>

Program kecil yang melepaskan terminal. dtach menjaga Claude Code tetap hidup saat terminal ditutup atau SSH terputus, jadi jawaban yang kamu kirim sejam kemudian tetap sampai ke sesi yang masih hidup. dtach ikut terpasang bersama aipager.

</details>

<details>
<summary><b>Bagaimana kalau Claude melenceng?</b></summary>

<br>

Permintaan menunggu ketukanmu, `/stop` menghentikan tugas dan membuang semua yang mengantre, `/kill` mengakhiri sesi, dan tombol dari tugas yang sudah selesai akan menolak.

</details>

<details>
<summary><b>Bagaimana cara memperbaruinya?</b></summary>

<br>

Kirim `/update`: perintah ini memeriksa aipager dan Claude Code dan memasang yang baru, lalu 🔄 Mulai ulang sekarang menghidupkan aipager lagi, entah sebagai layanan atau tidak. Sesimu tetap berjalan selama itu. Di baris perintah, `aipager update` memperbarui lewat installer yang memasang aipager.

</details>

<details>
<summary><b>Aku memakai Bedrock atau Vertex. Apakah tombol model berfungsi?</b></summary>

<br>

Menu **Model** di keyboard dan Mini App menawarkan alias keluarga Claude Code (`sonnet`, `opus`, `haiku`, `fable`, `opusplan`) dan model tetap seperti `claude-opus-5-5` dan `claude-sonnet-5-5`. Di API Anthropic, setiap alias mengarah ke model terbaru dalam keluarganya. Di Bedrock dan Vertex alias yang sama bisa mengarah ke versi yang lebih lama, tergantung apa yang ditawarkan penyedia layananmu; kirim `/model <full-id>` untuk memilih model yang persis.

</details>

<details>
<summary><b>Saya memakai kunci API, gateway, atau penyedia lain. Apakah aipager bisa dipakai?</b></summary>

<br>

Bisa. aipager menjalankan Claude Code sesuai pengaturanmu, dan menjawab pertanyaan yang diajukan Claude Code saat mulai (folder, kunci API, layar saat pertama kali dijalankan), supaya sesi yang dimulai dari Telegram tidak berhenti di situ. Taruh pengaturannya di blok `env` pada `~/.claude/settings.json`, atau di `~/.config/aipager/daemon.env` (satu baris `KEY=value` per pengaturan, tanpa tanda kutip). Tombol Opus, Sonnet, dan Haiku mengikuti `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, dan `ANTHROPIC_DEFAULT_HAIKU_MODEL`, dan model lain bisa diketik namanya. Anthropic hanya mendukung Claude Code dengan model Claude. Lihat [Pemecahan masalah](troubleshooting.md#using-an-api-key-a-gateway-or-another-provider).

</details>

<details>
<summary><b>Berapa biayanya?</b></summary>

<br>

Gratis. aipager berlisensi MIT, botnya milikmu, dan Claude Code tetap memakai paket atau kunci yang sudah dipakainya.

</details>

## <a id="documentation"></a>Dokumentasi

| Halaman | Isinya |
|---|---|
| [Perintah bot](commands.md) | Semua perintah, tombol, pengaturan, dan opsi baris perintah |
| [Mode tim dan grup](groups.md) | Peran, folder utama, siapa boleh mengetuk apa, model kepercayaan |
| [Model keamanan](security.md) | Batas kepercayaan, rahasia, log audit, jaringan |
| [Event hook](hooks.md) | Apa yang dilakukan setiap hook Claude Code di aipager |
| [Arsitektur](architecture.md) | Bagian-bagian aipager, file, dan soketnya |
| [Pemecahan masalah](troubleshooting.md) | `aipager doctor` dan solusi masalah umum |
| [Laporan masalah](problem-reports.md) | Isi laporan, dan kapan aipager menawarkannya |
| [Pemasangan dengan agen](install-with-an-agent.md) | Panduan yang diikuti agen coding |

## <a id="contributing"></a>Berkontribusi

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach` ikut lewat dependensi `dtach-bin`, jadi tidak perlu apa-apa lagi. Jalankan tes dengan batas memori seperti di atas (di macOS, tanpa `systemd-run`): kalau tidak, tes yang lepas kendali bisa memakan seluruh memori komputer. Proses rilis ada di [CONTRIBUTING.md](../../../CONTRIBUTING.md).

## <a id="license"></a>Lisensi

MIT. Lihat [LICENSE](../../../LICENSE).

<br>

<div align="center">
<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="32" height="32" alt=""></a>
<br>
<sub><a href="https://aipager.run">aipager.run</a></sub>
</div>
