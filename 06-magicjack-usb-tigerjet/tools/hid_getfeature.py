#!/usr/bin/env python3
# Read the TigerJet 64-byte vendor HID FEATURE report from the magicJack (ground truth).
# Report descriptor declared: UsagePage 0xFFFF, Usage 0x00, ReportSize 8, ReportCount 64, Feature. No Report ID -> id 0.
import fcntl, sys, os

HIDIOCGFEATURE = lambda length: (3 << 30) | (ord('H') << 8) | 0x07 | (length << 16)  # _IOC(READ|WRITE,'H',7,len)

path = sys.argv[1] if len(sys.argv) > 1 else "/dev/hidraw1"
length = 65  # report id byte + 64 data
fd = os.open(path, os.O_RDWR)
try:
    for rid in (0, 1):
        buf = bytearray(length)
        buf[0] = rid
        try:
            n = fcntl.ioctl(fd, HIDIOCGFEATURE(length), buf, True)
            print(f"[report id {rid}] GET_FEATURE returned {n} bytes:")
            data = bytes(buf[:max(n,0)])
            for off in range(0, len(data), 16):
                chunk = data[off:off+16]
                hexs = ' '.join(f'{b:02x}' for b in chunk)
                asci = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
                print(f"  {off:02x}: {hexs:<47} {asci}")
        except OSError as e:
            print(f"[report id {rid}] GET_FEATURE failed: {e}")
finally:
    os.close(fd)
