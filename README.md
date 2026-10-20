# ktflash

Cross-platform (Linux / macOS / Windows) CLI for flashing firmware onto KTMicro **KT02H20** USB-C DAC dongles
(Jcally JM12 and similar), including FiiO/JadeAudio JA11 images. Single file, Python 3.8+.

```bash
pip install pyserial hidapi          # hidapi is optional on Linux (falls back to /dev/hidraw)
python3 ktflash.py probe                         # find dongle / bootloader port
python3 ktflash.py image  fw.bin                 # validate image, show packet plan
python3 ktflash.py snapshot                      # save identity info (NOT a firmware backup)
python3 ktflash.py flash  fw.bin                 # DRY RUN
python3 ktflash.py flash  fw.bin --execute --yes --no-backup-ok
```

## Read this first

- **The original firmware cannot be backed up.** The KT02H20 boot ROM has no read command. Get a stock image for your
  exact model *before* flashing (e.g. JCALLY's `jcally_jm12_v1.3.bin`) so you can revert.
- Same chip does not mean same board: wiring, clocks and calibration can differ between brands.
- A failed write leaves the dongle in bootloader mode (`8888:cdc0`, no audio). It is not bricked: unplug, re-plug, and
  flash a valid image. The bootloader accepts the handshake only once per boot, so always re-plug before a retry.
- Writes are refused unless `--execute --yes --no-backup-ok` are all given, and abort **before erasing** if the chip
  ID reported by the bootloader differs from the image header.

## Protocol (summary)

1. HID output report `0x54` + `"12345678"` -> bootloader `8888:cdc0` (CDC-ACM serial).
2. `KTM` (`1e 4b 54 4d`, repeated until `78`) -> `CHP` (`d2 43 48 50`, reply carries `KT02H20B`) -> erase/setup
   (`2d 29 00 10 0e 15 00 60 00 bc`) -> `PWO` (`3c 50 57 4f`) -> `KSTA` (`4b 53 54 41`) -> data packets -> final
   header packet -> `STP` (`96 53 54 50`) -> `ZRST` (`5a 52 53 54`). Command ack `0x78`, block ack `0xA5`.
3. Data packet: `69 | len lo | (len hi & 0x1f) | top3<<5 | addr(3, LE) | payload <= 1 KiB | CRC32(LE)`;
   CRC32 = reflected 0xEDB88320, init 0, no final xor, over header+payload. Block 0 skips the 16-byte image header,
   which the final packet writes last.

## Status

Verified on hardware (Linux, Fedora): a Jcally KT02H20 dongle (`31b2:0111`) was flashed with the FiiO JA11 V2.2 image
and re-enumerated as `2972:0102 FiiO JadeAudio JA11`. macOS and Windows paths use `pyserial`/`hidapi` but are untested.
Firmware images are not included — get them from the vendor. Use at your own risk; this can render a dongle unusable
until it is re-flashed.

## Credits and disclaimer

Independent interoperability research, not affiliated with KTMicro, FiiO or JCALLY. The protocol was decoded from the
vendor updater and cross-checked against public work:
[SMIPE444's ktflash toolkit](https://github.com/SMIPE444/fiio-ja11-jcally-jm12-moondropkt02-kt02h20-dac-amp-toolkit)
and [gxcreator/ktmicro-tools](https://github.com/gxcreator/ktmicro-tools).

Tests: `python3 -m unittest test_ktflash -v` (a mock bootloader reassembles the flash and compares it to the image;
set `KT_TEST_IMAGE` to also test against a real image).

Linux permissions: `sudo cp packaging/99-ktflash.rules /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger`.
