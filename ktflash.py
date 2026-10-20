#!/usr/bin/env python3
"""ktflash - cross-platform firmware flasher for KTMicro KT02H20 USB DAC dongles.

Works on Linux, macOS and Windows (Python 3.8+).  Needs `pyserial`; `hidapi`
is used for the HID "enter bootloader" step (Linux falls back to /dev/hidraw).

Protocol (reverse engineered from the vendor KT_USB_BOOT updater, cross-checked
against public research - see README.md):

  1. HID output report 0x54 with payload "12345678" -> device re-enumerates as
     a CDC-ACM serial bootloader, USB 8888:CDC0.
  2. Serial: KTM handshake -> CHP chip-id check -> erase/setup -> PWO -> KSTA ->
     1 KiB CRC-checked data packets -> final (header) packet -> STP -> RESET.

The bootloader has NO read command: the original firmware CANNOT be backed up
in software.  A failed write leaves the dongle in bootloader mode (not bricked);
re-run with a valid image.

Default behaviour is read-only / dry-run.  Writing needs --execute --yes
--no-backup-ok.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import struct
import sys
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

VID_KT = 0x31B2            # KTMicro app-mode VID (many rebrands keep it)
VID_BOOT, PID_BOOT = 0x8888, 0xCDC0
VID_JA11, PID_JA11 = 0x2972, 0x0102

IMAGE_MAGIC = b"KT_Helios_v1b___"
BLOCK = 0x400
REGION_BLOCKS = 0x20       # 32 blocks per bank; bank id sent on region starts
HEADER_LEN = 0x10

# Command tokens (opcode byte + 3 ASCII chars) as sent by the vendor tool.
CMD_KTM = bytes.fromhex("1e4b544d")
CMD_VER = bytes.fromhex("f0564552")      # not used in the flag=0 flow
CMD_CHP = bytes.fromhex("d2434850")
CMD_KEY = bytes.fromhex("f04b4559")      # not used in the flag=0 flow
CMD_ERASE = bytes.fromhex("2d2900100e15006000bc")   # erase/setup block (10 bytes)
CMD_PWO = bytes.fromhex("3c50574f")
CMD_KSTA = bytes.fromhex("4b535441")
CMD_STP = bytes.fromhex("96535450")
CMD_RESET = bytes.fromhex("5a525354")    # "ZRST"
UNLOCK_REPORT = bytes([0x54]) + b"12345678" + b"\x00\x00"   # 11 bytes

ACK_CMD = 0x78
ACK_BLOCK = 0xA5


class KtError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# CRC / image / packet planner (pure functions, unit-tested)
# --------------------------------------------------------------------------
def _make_table() -> List[int]:
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
        table.append(c)
    return table


_TABLE = _make_table()


def crc32_kt(data: bytes) -> int:
    """Reflected CRC-32 (poly 0xEDB88320) with init 0 and NO final xor."""
    crc = 0
    for b in data:
        crc = (crc >> 8) ^ _TABLE[(crc ^ b) & 0xFF]
    return crc


@dataclass
class Image:
    data: bytes
    chip_id: bytes
    declared_size: int

    @property
    def size(self) -> int:
        return len(self.data)

    @classmethod
    def load(cls, path: str) -> "Image":
        with open(path, "rb") as f:
            return cls.parse(f.read())

    @classmethod
    def parse(cls, data: bytes) -> "Image":
        if len(data) <= BLOCK:
            raise KtError("image too small (%d bytes)" % len(data))
        if not data.startswith(IMAGE_MAGIC):
            raise KtError("bad image magic %r (expected %r)" % (data[:16], IMAGE_MAGIC))
        chip = data[0x10:0x18]
        declared = struct.unpack_from("<I", data, 0x1C)[0]
        if declared != len(data):
            raise KtError("header size field %d != file size %d (corrupt image?)"
                          % (declared, len(data)))
        return cls(data, chip, declared)

    def strings(self, limit: int = 0x1200) -> List[str]:
        return [m.group().decode() for m in re.finditer(rb"[\x20-\x7e]{6,}", self.data[:limit])]


@dataclass
class Packet:
    data: bytes
    addr: int
    payload_len: int
    final: bool = False


def build_packet(payload: bytes, addr: int, top3: int) -> bytes:
    n = len(payload)
    if n > BLOCK:
        raise ValueError("payload > 0x400")
    hdr = bytes([0x69, n & 0xFF, ((n >> 8) & 0x1F) | (top3 << 5),
                 addr & 0xFF, (addr >> 8) & 0xFF, (addr >> 16) & 0xFF])
    body = hdr + payload
    return body + struct.pack("<I", crc32_kt(body))


def plan_packets(img: Image, base: int = 0) -> List[Packet]:
    """Data packets for blocks 0..N-1, then the final header packet.

    Block 0 skips the 16-byte image header (it is written LAST by the final
    packet so an interrupted flash stays unbootable instead of half-valid).
    """
    data = img.data
    out: List[Packet] = []
    k = 0
    while k * BLOCK < len(data):
        if k == 0:
            payload, addr = data[HEADER_LEN:BLOCK], base + HEADER_LEN
        else:
            payload, addr = data[k * BLOCK:(k + 1) * BLOCK], base + k * BLOCK
        top3 = 1 if k % REGION_BLOCKS == 0 else 0b111
        out.append(Packet(build_packet(payload, addr, top3), addr, len(payload)))
        k += 1
    out.append(Packet(build_packet(data[:HEADER_LEN], base, 0b111), base, HEADER_LEN, True))
    return out


# --------------------------------------------------------------------------
# Transports
# --------------------------------------------------------------------------
class SerialLink:
    """pyserial wrapper with the 'drain, send, wait-for-byte' pattern the
    vendor tool uses (it clears its RX buffer after every matched ack)."""

    def __init__(self, port: str):
        import serial  # pyserial
        self.ser = serial.Serial(port, 115200, timeout=0, write_timeout=5)
        self.ser.dtr = True
        self.ser.rts = True

    def close(self):
        self.ser.close()

    def drain(self):
        self.ser.reset_input_buffer()

    def send(self, data: bytes):
        self.ser.write(data)
        self.ser.flush()

    def read_some(self) -> bytes:
        n = self.ser.in_waiting
        return self.ser.read(n) if n else b""


def wait_for(link, wanted: int, timeout: float) -> bytes:
    """Accumulate RX until byte `wanted` appears; returns everything received."""
    end = time.time() + timeout
    buf = b""
    while time.time() < end:
        buf += link.read_some()
        if wanted in buf:
            return buf
        time.sleep(0.002)
    raise TimeoutError("no 0x%02x ack within %.1fs (got %s)" % (wanted, timeout, buf.hex(" ") or "nothing"))


def wait_len(link, n: int, timeout: float) -> bytes:
    end = time.time() + timeout
    buf = b""
    while time.time() < end:
        buf += link.read_some()
        if len(buf) >= n:
            return buf
        time.sleep(0.002)
    raise TimeoutError("expected >=%d bytes within %.1fs (got %s)" % (n, timeout, buf.hex(" ") or "nothing"))


# --------------------------------------------------------------------------
# Device discovery / HID unlock
# --------------------------------------------------------------------------
def find_boot_ports() -> List[str]:
    from serial.tools import list_ports
    return [p.device for p in list_ports.comports() if p.vid == VID_BOOT and p.pid == PID_BOOT]


def hid_devices(vid: Optional[int] = None) -> List[dict]:
    if sys.platform.startswith("linux"):
        raw = _hidraw_devices(vid)       # kernel hidraw: no libusb / extra permissions needed
        if raw:
            return raw
    try:
        import hid  # package "hidapi"
    except ImportError:
        return _hidraw_devices(vid)
    return [d for d in hid.enumerate(vid or 0, 0) if vid is None or d["vendor_id"] == vid]


def _hidraw_devices(vid: Optional[int]) -> List[dict]:
    out = []
    for uev in glob.glob("/sys/class/hidraw/hidraw*/device/uevent"):
        txt = open(uev).read()
        m = re.search(r"HID_ID=0003:([0-9A-Fa-f]{8}):([0-9A-Fa-f]{8})", txt)
        if not m:
            continue
        v, p = int(m.group(1), 16), int(m.group(2), 16)
        if vid is None or v == vid:
            name = re.search(r"HID_NAME=(.*)", txt)
            out.append({"vendor_id": v, "product_id": p,
                        "path": ("/dev/" + uev.split("/")[4]).encode(),
                        "product_string": name.group(1) if name else ""})
    return out


def send_unlock(vid: int = VID_KT) -> str:
    """Send the 'enter bootloader' HID report. Returns a description of the device used."""
    cands = hid_devices(vid)
    if not cands:
        raise KtError("no HID device with VID 0x%04x found (is the dongle plugged in, and in normal mode?)" % vid)
    last = None
    for d in cands:
        try:
            if d["path"].startswith(b"/dev/hidraw"):
                fd = os.open(d["path"], os.O_WRONLY)
                try:
                    os.write(fd, UNLOCK_REPORT)
                finally:
                    os.close(fd)
            else:
                import hid
                dev = hid.device()
                dev.open_path(d["path"])
                try:
                    n = dev.write(list(UNLOCK_REPORT))
                finally:
                    dev.close()
                if n < 0:
                    raise OSError("hid write failed")
            return "%04x:%04x %s" % (d["vendor_id"], d["product_id"], d.get("product_string", ""))
        except OSError as e:
            last = e
    hint = ""
    if sys.platform.startswith("linux"):
        hint = " (permissions? install packaging/99-ktflash.rules or run with sudo)"
    raise KtError("could not open the dongle's HID interface: %s%s" % (last, hint))


def wait_for_boot_port(timeout: float = 15.0) -> str:
    end = time.time() + timeout
    while time.time() < end:
        ports = find_boot_ports()
        if ports:
            time.sleep(0.2)  # let the OS finish creating the node
            return ports[0]
        time.sleep(0.1)
    raise KtError("bootloader (8888:cdc0) did not appear within %.0fs" % timeout)


# --------------------------------------------------------------------------
# Bootloader session
# --------------------------------------------------------------------------
class Bootloader:
    def __init__(self, link, log: Callable[[str], None] = print):
        self.link = link
        self.log = log

    def _cmd(self, data: bytes, ack: int, timeout: float) -> bytes:
        self.link.drain()
        self.link.send(data)
        return wait_for(self.link, ack, timeout)

    def handshake(self, timeout: float = 10.0):
        """The vendor tool re-sends KTM every ~10 ms until the first 0x78."""
        end = time.time() + timeout
        while time.time() < end:
            self.link.send(CMD_KTM)
            t = time.time() + 0.01
            buf = b""
            while time.time() < t:
                buf += self.link.read_some()
                time.sleep(0.001)
            if ACK_CMD in buf:
                return
        raise KtError("no handshake ack (0x78). The bootloader accepts KTM only once per "
                      "boot: unplug and re-plug the dongle, then retry.")

    def chip_id(self) -> bytes:
        self.link.drain()
        self.link.send(CMD_CHP)
        reply = wait_len(self.link, 13, 3.0)
        return reply[4:12]

    def flash(self, plan: List[Packet], progress: Optional[Callable[[int, int], None]] = None):
        self._cmd(CMD_ERASE, ACK_CMD, 20)      # <- point of no return
        self._cmd(CMD_PWO, ACK_CMD, 5)
        self._cmd(CMD_KSTA, ACK_CMD, 20)       # vendor allows 6 s here
        for i, pkt in enumerate(plan):
            try:
                self._cmd(pkt.data, ACK_BLOCK, 5)
            except TimeoutError as e:
                raise KtError("write failed at packet %d/%d (addr 0x%06x): %s" % (i + 1, len(plan), pkt.addr, e))
            if progress:
                progress(i + 1, len(plan))
        self._cmd(CMD_STP, ACK_CMD, 5)
        try:
            self._cmd(CMD_RESET, ACK_CMD, 3)
        except TimeoutError:
            self.log("note: no ack to RESET (device may have rebooted already)")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def cmd_probe(args):
    print("HID devices (VID 0x%04x):" % args.vid)
    for d in hid_devices(args.vid):
        print("  %04x:%04x  %s" % (d["vendor_id"], d["product_id"], d.get("product_string", "")))
    try:
        ports = find_boot_ports()
    except ImportError:
        ports = []
        print("pyserial not installed (pip install pyserial)")
    print("bootloader serial ports (8888:cdc0): %s" % (ports or "none"))
    return 0


def cmd_image(args):
    img = Image.load(args.image)
    plan = plan_packets(img, args.base)
    print("file        : %s" % args.image)
    print("size        : %d bytes (header declares %d)" % (img.size, img.declared_size))
    print("chip id     : %s" % img.chip_id.decode("ascii", "replace"))
    print("packets     : %d data + 1 final = %d" % (len(plan) - 1, len(plan)))
    print("image crc   : 0x%08x (KT crc32)" % crc32_kt(img.data))
    for s in img.strings():
        print("  string    : %s" % s)
    return 0


def cmd_unlock(args):
    print("sent bootloader unlock to %s" % send_unlock(args.vid))
    print("bootloader port: %s" % wait_for_boot_port())
    print("NOTE: unplug/re-plug the dongle to return to normal mode if you do not flash.")
    return 0


def cmd_snapshot(args):
    snap = {"taken": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "note": "Identity snapshot only. This is NOT a firmware backup: the KT02H20 "
                    "bootloader has no read command.",
            "hid": [{k: (v.decode() if isinstance(v, bytes) else v) for k, v in d.items()}
                    for d in hid_devices(args.vid)]}
    if sys.platform.startswith("linux"):
        for dev in glob.glob("/sys/bus/usb/devices/*"):
            try:
                if int(open(dev + "/idVendor").read(), 16) == args.vid:
                    snap.setdefault("usb_descriptors_hex", {})[os.path.basename(dev)] = \
                        open(dev + "/descriptors", "rb").read().hex()
            except (OSError, ValueError):
                pass
    with open(args.out, "w") as f:
        json.dump(snap, f, indent=2)
    print("wrote %s" % args.out)
    return 0


def cmd_flash(args):
    img = Image.load(args.image)
    plan = plan_packets(img, args.base)
    print("image: %s  chip=%s  %d bytes  %d packets" %
          (os.path.basename(args.image), img.chip_id.decode("ascii", "replace"), img.size, len(plan)))
    if not args.execute:
        print("\nDRY RUN - nothing was sent. First packets:")
        for p in plan[:3] + plan[-1:]:
            print("  addr 0x%06x len %4d hdr %s%s" % (p.addr, p.payload_len, p.data[:6].hex(" "),
                                                      "  (final)" if p.final else ""))
        print("\nTo write: add --execute --yes --no-backup-ok")
        return 0
    if not (args.yes and args.no_backup_ok):
        raise KtError("refusing to write: pass --yes AND --no-backup-ok (the original firmware "
                      "cannot be backed up in software; you need a stock image to revert)")

    port = args.port
    if not port:
        ports = find_boot_ports()
        if not ports:
            print("unlocking: %s" % send_unlock(args.vid))
            ports = [wait_for_boot_port()]
        port = ports[0]
    print("bootloader port: %s" % port)
    link = SerialLink(port)
    try:
        bl = Bootloader(link)
        bl.handshake()
        chip = bl.chip_id()
        print("chip id: %s" % chip.decode("ascii", "replace"))
        if chip != img.chip_id:
            raise KtError("chip %r does not match image %r - aborting BEFORE erase" % (chip, img.chip_id))

        def progress(i, n):
            sys.stderr.write("\rwriting %3d%%  (%d/%d)" % (100 * i // n, i, n))
            sys.stderr.flush()
        bl.flash(plan, progress)
        sys.stderr.write("\n")
    except (KtError, TimeoutError) as e:
        sys.stderr.write("\n")
        raise KtError("%s\nThe dongle is NOT bricked: the boot ROM survives. Unplug/re-plug and "
                      "re-run with a valid image." % e)
    finally:
        link.close()
    print("write complete; waiting for the dongle to re-enumerate ...")
    end = time.time() + 15
    while time.time() < end:
        devs = [d for d in hid_devices(None) if d["vendor_id"] in (VID_KT, VID_JA11)]
        if devs:
            d = devs[0]
            print("back as %04x:%04x %s" % (d["vendor_id"], d["product_id"], d.get("product_string", "")))
            return 0
        time.sleep(0.3)
    print("did not re-enumerate in 15s - unplug/re-plug it.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ktflash", description=__doc__.split("\n\n")[0])
    ap.add_argument("--vid", type=lambda s: int(s, 0), default=VID_KT, help="app-mode USB VID (default 0x31b2)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe", help="list KT dongles / bootloader ports").set_defaults(fn=cmd_probe)
    p = sub.add_parser("image", help="validate a firmware image and show its packet plan")
    p.add_argument("image"); p.add_argument("--base", type=lambda s: int(s, 0), default=0)
    p.set_defaults(fn=cmd_image)
    sub.add_parser("unlock", help="switch the dongle into bootloader mode").set_defaults(fn=cmd_unlock)
    p = sub.add_parser("snapshot", help="save a device identity snapshot (NOT a firmware backup)")
    p.add_argument("out", nargs="?", default="kt-snapshot.json"); p.set_defaults(fn=cmd_snapshot)
    p = sub.add_parser("flash", help="flash an image (dry run unless --execute)")
    p.add_argument("image"); p.add_argument("--port"); p.add_argument("--base", type=lambda s: int(s, 0), default=0)
    p.add_argument("--execute", action="store_true"); p.add_argument("--yes", action="store_true")
    p.add_argument("--no-backup-ok", action="store_true",
                   help="acknowledge the original firmware cannot be backed up")
    p.set_defaults(fn=cmd_flash)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except KtError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
