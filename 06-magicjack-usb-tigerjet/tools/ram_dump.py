#!/usr/bin/env python3
"""Fast dump of live device SDRAM to a file via the tj_armmem SoC-memory port (auto-increment GETs,
~190 KB/s). Read-only."""
import sys, time
sys.path.insert(0, '/home/h1ght0w3r/Development/Active/hardware/06-magicjack-usb-tigerjet/tools')
from tj_armmem import ArmMem, PRWC, usb_reset

OUT = '/tmp/claude-1000/-home-h1ght0w3r-Development-Active-hardware/76cf063d-7d0e-4fd4-a5db-0bb3dad0d0f5/scratchpad/re/ram-0-16M.bin'
END = 0x01000000
NBLK = 32

def set_addr(m, addr):
    a = [addr & 0xff, (addr>>8)&0xff, (addr>>16)&0xff, (addr>>24)&0xff]
    m._set([0x80, 0x04, 0x0e, 0x04] + a + PRWC); time.sleep(0.0015)

def main():
    m = ArmMem(); f = open(OUT, 'wb'); t0 = time.time(); addr = 0
    try:
        while addr < END:
            try:
                set_addr(m, addr)
                blk = b''.join(m._get()[:64] for _ in range(NBLK))
                m._finalize()
                f.write(blk); addr += 64 * NBLK
            except OSError as e:
                # pad the hole so file offset == address, then continue
                f.write(b'\x00' * (64 * NBLK)); addr += 64 * NBLK
                usb_reset(); m.__init__()
            if addr % 0x200000 == 0:
                el = time.time() - t0
                print(f"  0x{addr:x} ({addr//1024}KB, {addr/1024/max(el,0.1):.0f} KB/s)")
    finally:
        f.close(); m.close()
    print(f"wrote {addr} bytes to {OUT} in {time.time()-t0:.0f}s")

if __name__ == '__main__':
    main()
