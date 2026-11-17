# Mini App fonts

The fonts the Mini App uses for scripts the system font often renders
poorly, served by the daemon itself at `/fonts/<name>` (see
`aipager/miniapp/fonts.py`). The page never loads a font from another host.
Each `@font-face` carries a `unicode-range`, so a phone downloads a file
only when the page shows characters in that range.

| File | Font | Version | Used for |
|---|---|---|---|
| `vazirmatn-33.003-arabic.woff2` | Vazirmatn | 33.003 | Persian (`fa`) |
| `noto-sans-arabic-2.012-arabic.woff2` | Noto Sans Arabic | 2.012 | Arabic (`ar`) |
| `be-vietnam-pro-1.002-<weight>-<subset>.woff2` | Be Vietnam Pro | 1.002 | Vietnamese (`vi`), all text |

All three are under the SIL Open Font License 1.1, which sits beside the
files as `<font>-OFL.txt`. None declares a Reserved Font Name.

## Where they come from

Downloaded 2026-11-17 from Google Fonts (the families' official web
builds; the source repositories are named in each license), already split
into Google's `arabic`, `latin` and `vietnamese` subsets:

- Vazirmatn (https://github.com/rastikerdar/vazirmatn), arabic subset, variable `wght` 100-900:
  https://fonts.gstatic.com/s/vazirmatn/v16/Dxxo8j6PP2D_kU2muijlGMWWMmk.woff2
  sha256 `84a382e46c30fb4f73d0e3800c16d0af15888e2731e57fa5f93e2c29a2c6a957` (46,308 bytes)
- Noto Sans Arabic (https://github.com/notofonts/arabic), arabic subset, variable `wght` 100-900:
  https://fonts.gstatic.com/s/notosansarabic/v33/nwpCtLGrOAZMl5nJ_wfgRg3DrWFZWsnVBJ_sS6tlqHHFlj4wv4r4xA.woff2
  sha256 `ce85091f020920b65762b387b194ef59457ea5b25b760f2dcc35240a94bb8669` (165,960 bytes)
- Be Vietnam Pro (https://github.com/bettergui/BeVietnamPro), static weights, shipped unchanged:
  - 400 latin https://fonts.gstatic.com/s/bevietnampro/v12/QdVPSTAyLFyeg_IDWvOJmVES_Hw3BXo.woff2
  - 400 vietnamese https://fonts.gstatic.com/s/bevietnampro/v12/QdVPSTAyLFyeg_IDWvOJmVES_Hw4BXoKZA.woff2
  - 500 latin https://fonts.gstatic.com/s/bevietnampro/v12/QdVMSTAyLFyeg_IDWvOJmVES_HTEJm81Rb0.woff2
  - 500 vietnamese https://fonts.gstatic.com/s/bevietnampro/v12/QdVMSTAyLFyeg_IDWvOJmVES_HTEJm86Rb0bcw.woff2
  - 600 latin https://fonts.gstatic.com/s/bevietnampro/v12/QdVMSTAyLFyeg_IDWvOJmVES_HToIW81Rb0.woff2
  - 600 vietnamese https://fonts.gstatic.com/s/bevietnampro/v12/QdVMSTAyLFyeg_IDWvOJmVES_HToIW86Rb0bcw.woff2
  - 700 latin https://fonts.gstatic.com/s/bevietnampro/v12/QdVMSTAyLFyeg_IDWvOJmVES_HSMIG81Rb0.woff2
  - 700 vietnamese https://fonts.gstatic.com/s/bevietnampro/v12/QdVMSTAyLFyeg_IDWvOJmVES_HSMIG86Rb0bcw.woff2

The two Arabic-script fonts were narrowed to the weights the Mini App uses
(`wght` 400-700, still variable) with fontTools 4.68.0:
`python -m fontTools.varLib.instancer <file> wght=400:700`.

## The shipped files

| File | Bytes | sha256 |
|---|---:|---|
| `vazirmatn-33.003-arabic.woff2` | 33,960 | `b3e3adb66445142df64df411f63a16ac33ba74a05c72fbffe1f0cd4a5edf1f91` |
| `noto-sans-arabic-2.012-arabic.woff2` | 90,248 | `5d141980dfddd03e0597aa961d83b798a7980046f1f4ede160b506d233d6ef32` |
| `be-vietnam-pro-1.002-400-latin.woff2` | 21,168 | `03d1b589cff172e1a670b3573e731d3380bc326f80cf83b0d3504e3188e2e074` |
| `be-vietnam-pro-1.002-400-vietnamese.woff2` | 11,532 | `dc085e2fba3414e5c5bf1e6172f921a9f81c5859946a4ed3d63c1e470d96a9e2` |
| `be-vietnam-pro-1.002-500-latin.woff2` | 21,892 | `b621f77d35f777023aa11ca524462d511b4b28a813adbc0e9d15a10fc61dfe4e` |
| `be-vietnam-pro-1.002-500-vietnamese.woff2` | 12,172 | `86341610cbe907eecf461c9159c168d5efb52bb1a33963813a08f520555d8e66` |
| `be-vietnam-pro-1.002-600-latin.woff2` | 22,032 | `9503dec2a7c532c8331e9600bcafea287a4fd208573b8668d85ab8d8de1863c7` |
| `be-vietnam-pro-1.002-600-vietnamese.woff2` | 12,176 | `97658c6f9a384f29a3005c3d96e2a0d1c810192cf68979071c290f5a377a9f99` |
| `be-vietnam-pro-1.002-700-latin.woff2` | 22,152 | `a193dd87699bd2e18ddf72dc271493ea82a23dad9f5c334d9f2a257b1e05fc30` |
| `be-vietnam-pro-1.002-700-vietnamese.woff2` | 12,468 | `4f58af2d1c3e28a9ba14c51c82db2751d78344b75bdcb34de24a1031ebe59da6` |

## Changing a font

The route sends every file with a year-long `immutable` cache, so a
changed file must get a new name (the version is in the name for that
reason): add the new file, update `FONT_FILES` in `aipager/miniapp/fonts.py`
and the `@font-face` rules in `static/_styles.py`, update this table, and
delete the old file. `tests/test_miniapp_fonts.py` checks the table
against the files.
