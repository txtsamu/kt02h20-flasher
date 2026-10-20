# ktflash

[English](README.md) | **Bahasa Indonesia**

Tool command-line lintas platform (Linux / macOS / Windows) untuk mem-flash firmware ke dongle DAC USB-C
**KTMicro KT02H20** (JCALLY JM12 dan rebrand lainnya), termasuk firmware **JA11** dari FiiO / JadeAudio. Satu file
Python, tanpa GUI vendor, tidak perlu Windows.

> **Baca ini sebelum flash**
>
> - **Firmware asli TIDAK bisa di-backup.** Boot ROM KT02H20 tidak punya perintah baca (read). Siapkan image *stock*
>   untuk model dongle kamu terlebih dulu (untuk JCALLY, misalnya `jcally_jm12_v1.3.bin` dari penjual) supaya bisa
>   dikembalikan.
> - "Chip sama" bukan berarti "board sama". Wiring, clock, dan kalibrasi bisa berbeda antar merek. Image yang tidak
>   cocok bisa membuat suara hilang atau mikrofon mati sampai kamu flash ulang.
> - Kalau penulisan gagal, masih bisa dipulihkan: dongle tetap di mode bootloader (`8888:cdc0`, tanpa suara) dan boot
>   ROM tidak pernah tertimpa. Cabut, colok lagi, lalu flash image yang valid.
> - Risiko ditanggung sendiri. Ini riset interoperabilitas independen, tidak berafiliasi dengan KTMicro, FiiO, atau
>   JCALLY.

## Status

Sudah diverifikasi di hardware (Linux, Fedora): dongle JCALLY KT02H20 (`31b2:0111`) berhasil di-flash dengan FiiO JA11
V2.2 dan muncul kembali sebagai `2972:0102 FiiO JadeAudio JA11`. Jalur macOS dan Windows memakai `pyserial` / `hidapi`
tetapi **belum diuji**. Hanya alur standar yang diimplementasikan (image ditulis di base `0`); alur alternatif "BOOT
flag" milik tool vendor (base `0x8000`, `VER`/`KEY`/`INF`) belum ada.

## Kebutuhan

- Python 3.8 atau lebih baru
- `pip install pyserial hidapi` (di Linux `hidapi` opsional — tool langsung memakai `/dev/hidraw*`)
- Dongle KT02H20 dan file firmware `.bin` (tidak disertakan — ambil dari vendor)

## Tutorial langkah demi langkah

### 1. Ambil file-nya

```bash
git clone https://github.com/txtsamu/kt02h20-flasher.git
cd kt02h20-flasher
pip install pyserial hidapi
```

Unduh firmware yang diinginkan dari vendor lalu ekstrak. Firmware FiiO JA11 (*JadeAudio JA11 Firmware V2.2.zip*)
dipublikasikan di forum resmi FiiO:
**[Forum FiiO — firmware JadeAudio JA11](https://forum.fiio.com/note/showNoteContent.do?id=202406211150090736905)**.
Unduh file zip dari halaman itu; di dalamnya file yang kamu butuhkan adalah `Firmware/JadeAudio JA11_V2.2.bin` (folder
`Upgrade Tool` adalah GUI vendor khusus Windows — **tidak perlu** dipakai bersama `ktflash`).
**Ambil juga image stock untuk dongle kamu sendiri** supaya bisa kembali (lihat peringatan di atas).

### 2. Atur izin akses

- **Linux:** pasang aturan udev supaya tidak perlu `sudo`, lalu colok ulang dongle:
  ```bash
  sudo cp packaging/99-ktflash.rules /etc/udev/rules.d/
  sudo udevadm control --reload && sudo udevadm trigger
  ```
  (Tanpa aturan ini, jalankan perintah di bawah dengan `sudo`.)
- **macOS (belum diuji):** tidak ada yang perlu dipasang. Jika langkah HID diblokir, mungkin kamu perlu mengizinkan
  terminal di *System Settings → Privacy & Security → Input Monitoring*.
- **Windows:** tidak ada yang perlu dipasang; bootloader muncul sebagai port serial USB (COM) standar. Jalankan
  terminal sebagai user biasa.

### 3. Colok dongle dan cek apakah terdeteksi

Pakai port USB langsung (hindari hub), lalu:

```bash
python3 ktflash.py probe
```

Kamu harus melihat perangkat HID dengan VID `0x31b2` (atau VID rebrand kamu — pakai `--vid 0x....` jika berbeda).
Jika muncul `bootloader serial ports (8888:cdc0): [...]`, berarti dongle sudah berada di mode bootloader.

### 4. Cek image firmware

```bash
python3 ktflash.py image "JadeAudio JA11_V2.2.bin"
```

Harus tampil `chip id : KT02H20B`, ukuran yang cocok, dan `66 data + 1 final = 67` paket untuk image JA11 V2.2.
Selain itu (magic salah, ukuran salah) akan ditolak.

### 5. Dry run (tidak mengirim apa pun)

```bash
python3 ktflash.py flash "JadeAudio JA11_V2.2.bin"
```

Ini hanya menampilkan rencana paket.

### 6. Flash

Pastikan dongle dalam **mode normal** (bisa memutar audio, `probe` menunjukkan `0x31b2`), tidak dipakai program lain,
dan **jangan dicabut saat penulisan** (hanya butuh waktu singkat).

```bash
python3 ktflash.py flash "JadeAudio JA11_V2.2.bin" --execute --yes --no-backup-ok
```

Yang terjadi: tool mengirim HID unlock, menunggu bootloader, memeriksa chip ID (batal **sebelum menghapus** jika tidak
cocok dengan image), menghapus, menulis 67 paket dengan penghitung progres, lalu menunggu dongle muncul kembali.
Hasil yang berhasil berakhir dengan teks seperti `back as 2972:0102 FIIO JadeAudio JA11`.

### 7. Verifikasi

- Linux: `lsusb | grep -i -E "2972|31b2"` dan `cat /proc/asound/cards`
- macOS: *System Information → USB*
- Windows: *Device Manager → Sound, video and game controllers*

Putar sesuatu dengan volume kecil dulu dan cek kedua channel (kiri dan kanan).

### 8. Kembalikan ke firmware lama (jika punya image stock)

Cabut dan colok ulang dongle, lalu flash file `.bin` stock dengan cara yang sama seperti langkah 6.

## Pemecahan masalah

| Gejala | Solusi |
|---|---|
| `no handshake ack (0x78)` | Bootloader hanya menerima handshake **sekali per boot**. Cabut, colok lagi, jalankan ulang. |
| Dongle muncul sebagai `8888:cdc0`, tanpa audio | Mode bootloader. Colok ulang untuk kembali normal, atau flash image yang valid. |
| `could not open the dongle's HID interface` | Izin akses: pasang aturan udev (Linux) atau pakai `sudo`; tutup program lain yang memakai dongle. |
| `bootloader (8888:cdc0) did not appear` | Colok ulang, coba port USB lain, cek `probe`. |
| `chip ... does not match image` | Image salah untuk chip ini. Tidak ada yang terhapus. |
| `write failed at packet N` | Colok ulang dan flash lagi; boot ROM tetap utuh. |

## Perintah

| Perintah | Fungsi |
|---|---|
| `probe` | Daftar dongle KT (HID) dan port serial bootloader |
| `image FILE` | Validasi image (magic, chip ID, ukuran), tampilkan CRC dan rencana paket |
| `snapshot [OUT.json]` | Simpan info identitas (descriptor HID/USB). **Bukan** backup firmware |
| `unlock` | Pindahkan dongle ke mode bootloader (colok ulang untuk keluar) |
| `flash FILE` | Dry run; dengan `--execute --yes --no-backup-ok` akan menulis image |

Opsi global: `--vid 0x31b2` (USB vendor ID saat mode normal).

## Cara kerja

1. HID output report `0x54` + `"12345678"` → dongle re-enumerate sebagai `8888:cdc0` (bootloader serial CDC-ACM).
2. `KTM` (`1e 4b 54 4d`, diulang sampai `78`) → `CHP` (`d2 43 48 50`, balasan berisi chip ID `KT02H20B`) →
   erase/setup (`2d 29 00 10 0e 15 00 60 00 bc`) → `PWO` (`3c 50 57 4f`) → `KSTA` (`4b 53 54 41`) → paket data →
   paket header final → `STP` (`96 53 54 50`) → `ZRST` (`5a 52 53 54`). Ack perintah `0x78`, ack blok `0xA5`.
3. Paket data: `69 | len lo | (len hi & 0x1f) | top3<<5 | addr (3 byte LE) | payload ≤ 1 KiB | CRC32 (LE)`.
   CRC32 memakai polinomial reflected `0xEDB88320`, init 0, **tanpa** xor akhir, dihitung atas header + payload. Blok 0
   melewati 16 byte header image; paket final menulisnya paling akhir, sehingga flash yang terputus tetap tidak bisa
   boot (bukan setengah valid).

## Tes

```bash
python3 -m unittest test_ktflash -v
KT_TEST_IMAGE="JadeAudio JA11_V2.2.bin" python3 -m unittest test_ktflash -v   # tes juga dengan image asli
```

Bootloader tiruan (mock) menyusun ulang flash dari paket-paket dan membandingkannya byte demi byte dengan image.

## Kredit

Protokol didekode dari updater vendor dan dicocokkan dengan karya publik:
[ktflash toolkit milik SMIPE444](https://github.com/SMIPE444/fiio-ja11-jcally-jm12-moondropkt02-kt02h20-dac-amp-toolkit)
dan [gxcreator/ktmicro-tools](https://github.com/gxcreator/ktmicro-tools).

## Lisensi

MIT — lihat [LICENSE](LICENSE).
