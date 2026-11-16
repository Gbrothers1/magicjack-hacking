#!/usr/bin/env python3
"""
tj_slic.py — talk to the Si321x ProSLIC through the TigerJet SPI bridge (TJ regs 0x26..0x29), for NON-ARM units (bcdDevice 0.01, 32-byte HID reports) where reg0 ring bits do nothing.

Framing (same as reference-drivers/tjctl.c, carried over HID "normal register" reports):
  TJ reg write : SET [04 <reg> <reg&0xE0> <count> <bytes...>]
  TJ reg read  : SET [04 <bank> <bank> 00] ; GET ; value = window[reg & 0x1F]
  ProSLIC direct write : TJ reg 0x26 <- [addr&0x7f, val, 0, 0x27]
  ProSLIC direct read  : TJ reg 0x26 <- [addr|0x80, 0, 0, 0x67] ; then read TJ reg 0x27
  ProSLIC reg 64 (LINEFEED) low 3 bits: 0 open, 1 fwd active, 2 fwd on-hook xmit, 3 tip open, 4 RINGING, 5 reverse active, 6 reverse on-hook xmit, 7 ring open.

On the repo's ARM unit the firmware blocks these registers (they STALL). On other chips they may work — this tool just tries and reports. Read-only except `write` and `ring`. Run as root.

  tj_slic.py dump              # ProSLIC direct regs 0..0x6c (id, status, linefeed, ...)
  tj_slic.py read  REG
  tj_slic.py write REG VAL     # raw ProSLIC direct write (you are responsible)
  tj_slic.py ring [secs]       # LINEFEED -> 0x44 (ringing) for secs, then restore. Keep handset on-hook.
  tj_slic.py tjdump            # read-only: TigerJet normal regs (banks 0x00/0x20/0x40/0x60)
  tj_slic.py init --yes        # tjctl.c init_hardware(): reset pulse + enable SPI pins, then re-probe SLIC
"""
import sys, time

sys.path.insert(0, __file__.rsplit('/', 1)[0])
from tj_linepower import TjLine

TJ_SPORT0, TJ_SPORT1 = 0x26, 0x27
LINEFEED = 64


class Slic(TjLine):
    def tj_wr(s, reg, data):
        s._set([0x04, reg & 0xff, reg & 0xE0, len(data)] + list(data))

    def tj_rd(s, reg):
        bank = reg & 0xE0
        s._set([0x04, bank, bank, 0x00])
        time.sleep(0.002)
        return s._get()[reg & 0x1F]

    def slic_rd(s, addr):
        s.tj_wr(TJ_SPORT0, [addr | 0x80, 0, 0, 0x67])
        time.sleep(0.003)
        return s.tj_rd(TJ_SPORT1)

    def slic_wr(s, addr, val):
        s.tj_wr(TJ_SPORT0, [addr & 0x7f, val & 0xff, 0, 0x27])
        time.sleep(0.003)

    def ring_slic(s, secs=2.0):
        """tjctl.c: ring = LINEFEED 0x44, normal active = 0x11 (high nibble = shadow of low)."""
        orig = s.slic_rd(LINEFEED)
        if orig == 0xff:
            raise RuntimeError("ProSLIC not responding (reads 0xff) — run `tj_slic.py init --yes` / `tjdump`")
        try:
            s.slic_wr(LINEFEED, 0x44)
            time.sleep(secs)
        finally:
            s.slic_wr(LINEFEED, orig if orig != 0x44 else 0x11)
        return orig

    def init_spi(s):
        """First half of tjctl.c init_hardware(): AUX6 (SLIC reset line) low->output, ext reset
        pulse, enable the SPI pin definition (TJ reg 0x29 bit5), release reset, zero gains."""
        for reg, val in [(0x12, 0x00), (0x13, 0x40), (0x00, 0x50), (0x29, 0x20), (0x00, 0x51),
                         (0x22, 0x00), (0x23, 0xf2), (0x24, 0x00), (0x25, 0xc9)]:
            s.tj_wr(reg, [val])
            time.sleep(0.01)

    def tj_dump(s):
        for bank in (0x00, 0x20, 0x40, 0x60):
            s._set([0x04, bank, bank, 0x00])
            time.sleep(0.002)
            w = s._get()[:32]
            print(f"TJ 0x{bank:02x}: " + ' '.join(f"{b:02x}" for b in w))

    def spi_xfer(s, b0, ctrl):
        """One raw SPI transaction via TJ regs 0x26..0x29; returns (b26,b27,b28,reg29,reg12)."""
        s.tj_wr(TJ_SPORT0, [b0, 0, 0, ctrl])
        for _ in range(20):
            time.sleep(0.002)
            s._set([0x04, 0x20, 0x20, 0x00])
            w = s._get()
            if not (w[9] & 1):
                break
        s._set([0x04, 0x00, 0x00, 0x00])
        w0 = s._get()
        return w[6], w[7], w[8], w[9], w0[0x12]

    def spi_sweep(s):
        """Try every combination of: driving one free AUX pin low/high (maybe the SLIC reset
        line) x several SPI control words, reading ProSLIC reg 0/1. Prints anything that is not
        the idle 0xff pattern. Skips AUX0/1 (SPI), AUX2 (suspend), AUX5 (hook sense)."""
        s.init_spi()
        hits = 0
        n = 0
        ctrls = [0x67, 0x7f, 0x27, 0x3f, 0x61, 0xe7, 0xa7]
        try:
            for pin in (None, 3, 4, 6, 7):
                for lvl in ((0,) if pin is None else (0, 1)):
                    if pin is not None:
                        s.tj_wr(0x13, [1 << pin])
                        s.tj_wr(0x12, [lvl << pin])
                        time.sleep(0.05)
                    for ctrl in ctrls:
                        for reg in (0, 1):
                            r = s.spi_xfer(reg | 0x80, ctrl)
                            n += 1
                            if r[:3] != (0xff, 0xff, 0xff):
                                hits += 1
                                print(f"HIT pin={pin} lvl={lvl} ctrl=0x{ctrl:02x} reg={reg}: "
                                      f"26/27/28={r[0]:02x} {r[1]:02x} {r[2]:02x} 0x29={r[3]:02x} 0x12={r[4]:02x}")
                    print(f"pin={pin} lvl={lvl}: done (last 0x29={r[3]:02x} 0x12={r[4]:02x})")
        finally:
            s.tj_wr(0x13, [0x00])
            s.tj_wr(0x12, [0x00])
        print(f"{n} transactions, {hits} non-0xff replies")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'dump'
    t = Slic()
    try:
        if cmd == 'dump':
            for a in range(0, 0x6d):
                try:
                    v = t.slic_rd(a)
                except OSError as e:
                    print(f"reg {a:3d} (0x{a:02x}): ERR {e}"); break
                print(f"reg {a:3d} (0x{a:02x}): 0x{v:02x}")
        elif cmd == 'read':
            a = int(sys.argv[2], 0)
            print(f"reg {a}: 0x{t.slic_rd(a):02x}")
        elif cmd == 'write':
            a, v = int(sys.argv[2], 0), int(sys.argv[3], 0)
            t.slic_wr(a, v)
            print(f"wrote reg {a} <- 0x{v:02x}; readback 0x{t.slic_rd(a):02x}")
        elif cmd == 'tjdump':
            t.tj_dump()
        elif cmd == 'spisweep':
            t.spi_sweep()
        elif cmd == 'init':
            if '--yes' not in sys.argv:
                print("init WRITES TJ regs 0x00,0x12,0x13,0x22-0x25,0x29 (tjctl.c init_hardware, volatile).\n"
                      "Run `tjdump` first and save its output; re-run with --yes to proceed.")
                return
            print("--- before init ---")
            t.tj_dump()
            t.init_spi()
            print("--- after init ---")
            t.tj_dump()
            print("ProSLIC regs 0,1,64,68:", [f"0x{t.slic_rd(a):02x}" for a in (0, 1, 64, 68)])
        elif cmd == 'ring':
            n = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
            print(f"LINEFEED before: 0x{t.slic_rd(LINEFEED):02x}")
            orig = t.ring_slic(n)
            print(f"restored LINEFEED=0x{orig:02x}; now 0x{t.slic_rd(LINEFEED):02x}")
        else:
            print(__doc__)
    finally:
        t.close()


if __name__ == '__main__':
    main()
