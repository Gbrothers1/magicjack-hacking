#!/usr/bin/env python3
"""Scan the live device's SDRAM (via the tj_armmem SoC-memory port) for the decrypted SIP account /
DB key / diag buffer. Read-only. First tests whether successive GETs auto-increment (fast path like
the flash page-read) vs one-block-per-SET (slow), then dumps+greps a bounded low-RAM region."""
import sys, time
sys.path.insert(0, '/home/h1ght0w3r/Development/Active/hardware/06-magicjack-usb-tigerjet/tools')
from tj_armmem import ArmMem, PRWC, usb_reset

TARGETS = [b'<E-number>', b'216.234.65', b'talk4free', b'SJEN', b'SJCF', b'sip:',
           b'PROFILES/MAGICJACK', b'DBKEY', b'EncryptKey', b'@talk4free', b'proxy']

def set_addr(m, addr):
    a = [addr & 0xff, (addr>>8)&0xff, (addr>>16)&0xff, (addr>>24)&0xff]
    m._set([0x80, 0x04, 0x0e, 0x04] + a + PRWC); time.sleep(0.0015)

def block(m):        # one 64-byte GET
    return m._get()[:64]

def main():
    m = ArmMem()
    try:
        # --- verify + test auto-increment ---
        set_addr(m, 0x0); b0 = block(m); b1 = block(m); b2 = block(m); m._finalize()
        print("block0 @0x00:", b0[:16].hex())
        print("block1 (next GET):", b1[:16].hex())
        # reference: independent single reads
        set_addr(m, 0x40); r40 = block(m); m._finalize()
        auto = (b1 == r40)
        print(f"reference @0x40: {r40[:16].hex()}")
        print(f"=> auto-increment across GETs: {auto}\n")

        # --- choose method + scan ---
        END = 0x00800000      # scan low 8 MB of SDRAM
        NBLK = 32 if auto else 1
        step = 64 * NBLK
        print(f"scanning 0x0..0x{END:x} in {step}-byte reads ({'fast auto-incr' if auto else 'slow per-block'})")
        buf = bytearray(); base = 0; found = False
        t0 = time.time(); addr = 0
        while addr < END:
            try:
                set_addr(m, addr)
                for _ in range(NBLK):
                    buf += block(m)
                m._finalize()
            except OSError as e:
                print(f"  stall @0x{addr:x} errno {e.errno}; usb reset"); usb_reset(); m.__init__()
                addr += step; continue
            addr += step
            # grep the accumulated buffer periodically (keep a tail overlap for split matches)
            if len(buf) >= 0x20000 or addr >= END:
                for t in TARGETS:
                    i = buf.find(t)
                    while i >= 0:
                        hit = base + i
                        ctx = bytes(buf[max(0,i-8):i+56])
                        printable = ''.join(chr(x) if 32<=x<127 else '.' for x in ctx)
                        print(f"  ** HIT {t!r} @0x{hit:06x}: {printable}")
                        found = True
                        i = buf.find(t, i+1)
                # keep last 64 bytes for overlap, advance base
                keep = 64
                base += len(buf) - keep
                buf = buf[-keep:]
            if addr % 0x100000 == 0:
                el = time.time()-t0
                print(f"  ..0x{addr:x} ({addr//1024}KB, {addr/1024/max(el,0.1):.0f} KB/s)")
                if found and addr >= 0x200000:   # early-ish stop once we have hits + covered a range
                    pass
        print(f"\nscan done ({(time.time()-t0):.0f}s). hits found: {found}")
    finally:
        m.close()

if __name__ == '__main__':
    main()
