# ktflash

**English** | [Bahasa Indonesia](README.id.md)

Cross-platform (Linux / macOS / Windows) command-line tool for flashing firmware onto **KTMicro KT02H20** USB-C DAC
dongles (JCALLY JM12 and similar rebrands), including the FiiO / JadeAudio **JA11** firmware. One Python file, no
vendor GUI, no Windows needed.

> **Read this before you flash anything**
>
> - **The original firmware cannot be backed up.** The KT02H20 boot ROM has no read command. Get a *stock* image for
>   your exact model first (for JCALLY, e.g. `jcally_jm12_v1.3.bin` from the seller) so you can revert.
> - "Same chip" does not mean "same board". Wiring, clocks and calibration can differ between brands. A mismatched image
>   can leave you with no sound or a dead microphone until you re-flash.
> - A failed write is recoverable: the dongle stays in bootloader mode (`8888:cdc0`, no audio) and the boot ROM is never
>   overwritten. Unplug, re-plug and flash a valid image.
> - Use at your own risk. Independent interoperability research, not affiliated with KTMicro, FiiO or JCALLY.

## Status

Verified on hardware (Linux, Fedora): a JCALLY KT02H20 dongle (`31b2:0111`) was flashed with FiiO JA11 V2.2 and
re-enumerated as `2972:0102 FiiO JadeAudio JA11`. The macOS and Windows code paths use `pyserial` / `hidapi` but are
**untested**. Only the standard flow is implemented (image written at base `0`); the alternate "BOOT flag" flow of the
vendor tool (base `0x8000`, `VER`/`KEY`/`INF`) is not.

## Requirements

- Python 3.8 or newer
- `pip install pyserial hidapi` (on Linux `hidapi` is optional — the tool talks to `/dev/hidraw*` directly)
- A KT02H20 dongle and a firmware `.bin` (not included — get it from the vendor)

## Step-by-step tutorial

### 1. Get the files

```bash
git clone https://github.com/txtsamu/kt02h20-flasher.git
cd kt02h20-flasher
pip install pyserial hidapi
```

Download the firmware you want from the vendor and unzip it. The FiiO JA11 firmware (*JadeAudio JA11 Firmware V2.2.zip*)
is published on the official FiiO forum:
**[FiiO forum — JadeAudio JA11 firmware](https://forum.fiio.com/note/showNoteContent.do?id=202406211150090736905)**.
Download the zip from that page; inside, the file you need is `Firmware/JadeAudio JA11_V2.2.bin` (the `Upgrade Tool`
folder is the Windows-only vendor GUI — you do **not** need it with `ktflash`).
**Also get a stock image for your own dongle** so you can go back (see the warning above).

### 2. Set up permissions

- **Linux:** install the udev rule so you don't need `sudo`, then replug the dongle:
  ```bash
  sudo cp packaging/99-ktflash.rules /etc/udev/rules.d/
  sudo udevadm control --reload && sudo udevadm trigger
  ```
  (Without it, run the commands below with `sudo`.)
- **macOS (untested):** nothing to install. If the HID step is blocked, you may need to allow your terminal under
  *System Settings → Privacy & Security → Input Monitoring*.
- **Windows:** nothing to install; the bootloader appears as a standard USB serial (COM) port. Run the terminal as a
  normal user.

### 3. Plug in the dongle and check it is detected

Use a direct USB port (avoid hubs), then:

```bash
python3 ktflash.py probe
```

You should see a HID device with VID `0x31b2` (or your rebrand's VID — pass `--vid 0x....` if different).
If you see `bootloader serial ports (8888:cdc0): [...]` the dongle is already in bootloader mode.

### 4. Check the firmware image

```bash
python3 ktflash.py image "JadeAudio JA11_V2.2.bin"
```

It must show `chip id : KT02H20B`, a matching size, and `66 data + 1 final = 67` packets for the JA11 V2.2 image.
Anything else (bad magic, wrong size) is rejected.

### 5. Dry run (sends nothing)

```bash
python3 ktflash.py flash "JadeAudio JA11_V2.2.bin"
```

This prints the packet plan only.

### 6. Flash

Make sure the dongle is in **normal mode** (it plays audio, `probe` shows `0x31b2`), nothing else is using it, and
**do not unplug it while writing** (it only takes a short while).

```bash
python3 ktflash.py flash "JadeAudio JA11_V2.2.bin" --execute --yes --no-backup-ok
```

What happens: the tool sends the HID unlock, waits for the bootloader, checks the chip ID (aborting **before erasing**
if it does not match the image), erases, writes 67 packets with a progress counter, and waits for the dongle to come
back. A successful run ends with something like `back as 2972:0102 FIIO JadeAudio JA11`.

### 7. Verify

- Linux: `lsusb | grep -i -E "2972|31b2"` and `cat /proc/asound/cards`
- macOS: *System Information → USB*
- Windows: *Device Manager → Sound, video and game controllers*

Play something at low volume first and check both channels.

### 8. Revert (if you have a stock image)

Unplug and re-plug the dongle, then flash the stock `.bin` the same way as step 6.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `no handshake ack (0x78)` | The bootloader accepts the handshake **once per boot**. Unplug, re-plug, run again. |
| Dongle shows as `8888:cdc0`, no audio | Bootloader mode. Re-plug to return to normal, or flash a valid image. |
| `could not open the dongle's HID interface` | Permissions: install the udev rule (Linux) or use `sudo`; close other programs using the dongle. |
| `bootloader (8888:cdc0) did not appear` | Re-plug, try another USB port, check `probe`. |
| `chip ... does not match image` | Wrong image for this chip. Nothing was erased. |
| `write failed at packet N` | Re-plug and flash again; the boot ROM is intact. |

## Commands

| Command | What it does |
|---|---|
| `probe` | List KT dongles (HID) and bootloader serial ports |
| `image FILE` | Validate an image (magic, chip ID, size), show CRC and packet plan |
| `snapshot [OUT.json]` | Save identity info (HID/USB descriptors). **Not** a firmware backup |
| `unlock` | Switch the dongle into bootloader mode (re-plug to leave it) |
| `flash FILE` | Dry run; with `--execute --yes --no-backup-ok` it writes the image |

Global option: `--vid 0x31b2` (app-mode USB vendor ID).

## How it works

1. HID output report `0x54` + `"12345678"` → the dongle re-enumerates as `8888:cdc0` (CDC-ACM serial bootloader).
2. `KTM` (`1e 4b 54 4d`, repeated until `78`) → `CHP` (`d2 43 48 50`, reply carries the chip ID `KT02H20B`) →
   erase/setup (`2d 29 00 10 0e 15 00 60 00 bc`) → `PWO` (`3c 50 57 4f`) → `KSTA` (`4b 53 54 41`) → data packets →
   final header packet → `STP` (`96 53 54 50`) → `ZRST` (`5a 52 53 54`). Command ack `0x78`, block ack `0xA5`.
3. Data packet: `69 | len lo | (len hi & 0x1f) | top3<<5 | addr (3 bytes LE) | payload ≤ 1 KiB | CRC32 (LE)`.
   CRC32 is the reflected `0xEDB88320` polynomial, init 0, **no** final xor, over header + payload. Block 0 skips the
   16-byte image header; the final packet writes it last, so an interrupted flash stays unbootable rather than
   half-valid.

## Tests

```bash
python3 -m unittest test_ktflash -v
KT_TEST_IMAGE="JadeAudio JA11_V2.2.bin" python3 -m unittest test_ktflash -v   # also test a real image
```

A mock bootloader reassembles the flash from the packets and compares it byte-for-byte with the image.

## Credits

The protocol was decoded from the vendor updater and cross-checked against public work:
[SMIPE444's ktflash toolkit](https://github.com/SMIPE444/fiio-ja11-jcally-jm12-moondropkt02-kt02h20-dac-amp-toolkit)
and [gxcreator/ktmicro-tools](https://github.com/gxcreator/ktmicro-tools).

## License

MIT — see [LICENSE](LICENSE).
