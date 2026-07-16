#!/usr/bin/env python3
"""
tj_armreg.py — generic ARM control-register read / write / read-modify-write for the
magicJack USB dongle (TigerJet 06e6:c200, DevType 8 ARM) over USB HID feature reports.

This is the shared foundation library the newer register tools build on. It exposes the ARM
control-register "Port 2" recovered from the macOS mjupdate binary:
  READ  (cmd 0x00): SET [00 00 <reg> 00] then GET_FEATURE ; value = resp[0:4] little-endian
  WRITE (cmd 0x20): SET [20 <reg> 00 01 <val32 LE>]
The read register selector sits at DATA byte index 2 ([00 00 <reg> 00]); note that when the
report-id byte is counted (the 65-byte buffer) that is buffer index 3 — the asymmetry vs the
write frame, where the register is at data index 1. These primitives are byte-identical to the
hardware-verified rd/wr in tj_linepower.py, so any register they name reads/writes correctly.

rmw(reg, setbits, clearbits): new = (old & ~clearbits) | setbits  — mirrors the firmware's
WriteArmRegisterBits helper (@0x100003110 in mjupdate). ALWAYS RMW when touching a shared
register (reg0, reg0x14, ...) so you preserve the other live bits (line-power bit0, hook bit31).

Known reg0 (0x00) bits: bit0=line-power, bits8-9(0x300)=ring, bit16(0x10000)=tip/ring polarity.
Known reg0x14 bits: bit31=hook, bit9(0x200)=dial-tone, bit7(0x80)=activate-strobe, bit4(0x10)=DTMF-mute.
reg0x38=3 = codec/master-clock; reg0x40 bit4 = digital-loopback. See captures/mac-binary-feature-catalog.md.

CLI:
  sudo python3 tj_armreg.py read  <reg>
  sudo python3 tj_armreg.py write <reg> <val32>
  sudo python3 tj_armreg.py rmw   <reg> <setmask> [<clearmask>]
regs/vals accept 0x-hex or decimal.

SAFETY: reads are safe. Writes perturb live firmware state and PERSIST across USB reset (only a
physical unplug clears them). Do not write unknown registers blindly; snapshot with tj_dumpregs.py
first so you can restore. This port drives ONLY ARM control regs (0x00..~0x5c, 0xf0) — it is NOT the
SoC-memory port (that is tj_armmem.py, cmd 0x80/"prwC"), so it cannot latch on an unmapped address.
"""
import fcntl, os, glob, time, sys

def _ioc(d, t, nr, sz): return (d << 30) | (sz << 16) | (ord(t) << 8) | nr
HIDIOCSFEATURE = lambda l: _ioc(3, 'H', 0x06, l)
HIDIOCGFEATURE = lambda l: _ioc(3, 'H', 0x07, l)
RLEN = 65

def find_hidraw():
    for u in glob.glob('/sys/class/hidraw/hidraw*/device/uevent'):
        try:
            if '06E6:0000C200' in open(u).read():
                return '/dev/' + u.split('/')[4]
        except Exception:
            pass
    return '/dev/hidraw1'

class ArmReg:
    """ARM control-register port (Port 2). Verified-identical framing to tj_linepower.rd/wr."""
    def __init__(s, path=None):
        s.fd = os.open(path or find_hidraw(), os.O_RDWR)
    def close(s):
        os.close(s.fd)
    def __enter__(s):
        return s
    def __exit__(s, *a):
        s.close()
    def _set(s, data):
        b = bytearray(RLEN); b[1:1 + len(data)] = bytes(data)
        fcntl.ioctl(s.fd, HIDIOCSFEATURE(RLEN), b, True)
    def _get(s):
        b = bytearray(RLEN); fcntl.ioctl(s.fd, HIDIOCGFEATURE(RLEN), b, True); return bytes(b[1:])
    def read_u32(s, reg):
        s._set([0x00, 0x00, reg & 0xff, 0x00]); time.sleep(0.002)
        return int.from_bytes(s._get()[0:4], 'little')
    def write_u32(s, reg, val):
        v = [val & 0xff, (val >> 8) & 0xff, (val >> 16) & 0xff, (val >> 24) & 0xff]
        s._set([0x20, reg & 0xff, 0x00, 0x01] + v)
    def read_stable(s, reg, tries=8):
        """Glitch-resistant read. The device's GET window is stateful: a read of reg RR can
        occasionally (and stickily, esp. off-hook) return the default/status window = reg0x14's
        value instead of RR's. That glitch has an exact signature — the read equals reg0x14 — so
        for RR != 0x14 we sample reg0x14 once and reject reads equal to it, then require two
        consecutive agreeing reads. reg0x14 itself page-selects reliably. Use for snapshots/diffs;
        read_u32 is the raw single read."""
        from collections import Counter
        ref14 = s.read_u32(0x14) if (reg & 0xff) != 0x14 else None
        vals = []
        for _ in range(tries):
            v = s.read_u32(reg)
            if ref14 is not None and v == ref14:
                continue                     # GET-window glitch (returned reg0x14) — re-read
            vals.append(v)
            if len(vals) >= 2 and vals[-1] == vals[-2]:
                return vals[-1]
        return Counter(vals).most_common(1)[0][0] if vals else s.read_u32(reg)
    def rmw(s, reg, setbits=0, clearbits=0):
        old = s.read_stable(reg)
        new = (old & ~(clearbits & 0xffffffff)) | (setbits & 0xffffffff)
        s.write_u32(reg, new)
        return old, new

def _int(x): return int(x, 0)

def main(argv):
    if len(argv) < 3:
        print("usage: tj_armreg.py read <reg> | write <reg> <val> | rmw <reg> <setmask> [<clearmask>]")
        return 2
    op = argv[1]; a = ArmReg()
    try:
        if op == 'read':
            reg = _int(argv[2]); print(f"reg 0x{reg:02x} = 0x{a.read_u32(reg):08x}")
        elif op == 'write':
            reg = _int(argv[2]); val = _int(argv[3]); a.write_u32(reg, val)
            print(f"reg 0x{reg:02x} <- 0x{val:08x} ; readback 0x{a.read_u32(reg):08x}")
        elif op == 'rmw':
            reg = _int(argv[2]); sm = _int(argv[3]); cm = _int(argv[4]) if len(argv) > 4 else 0
            old, new = a.rmw(reg, sm, cm); print(f"reg 0x{reg:02x}: 0x{old:08x} -> 0x{new:08x}")
        else:
            print(f"unknown op: {op}"); return 2
    finally:
        a.close()
    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv))
