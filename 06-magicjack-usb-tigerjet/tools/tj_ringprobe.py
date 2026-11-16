#!/usr/bin/env python3
"""
tj_ringprobe.py — find which reg0 bits make the phone ring on units whose layout differs
from the documented ARM one (e.g. bcdDevice 0.01).

The documented ARM ring request is reg0 |= 0x300. This tries that first, then (with --sweep)
each single bit in reg0 bits 8..15, one at a time, for a few seconds each, always restoring
the original reg0 afterwards. You answer y/n/q after each step.

Keep the handset ON-HOOK (hung up) — the phone only rings when on-hook. Run as root:
  tj_ringprobe.py [--sweep] [--secs 3]
"""
import sys, time, argparse

sys.path.insert(0, __file__.rsplit('/', 1)[0])
from tj_linepower import TjLine

ap = argparse.ArgumentParser()
ap.add_argument('--sweep', action='store_true', help='also try single bits 8..15')
ap.add_argument('--secs', type=float, default=3.0)
a = ap.parse_args()

tj = TjLine()
orig = tj.rd(0x00)
print(f"reg0 original = 0x{orig:08x}")
cands = [("ARM ring request: bits 8+9 (0x300)", 0x300)]
if a.sweep:
    cands += [(f"bit {b} (0x{1 << b:x})", 1 << b) for b in range(8, 16) if (1 << b) != 0x300]
found = []
try:
    tj.line_on()
    orig = tj.rd(0x00)  # after line_on
    for name, mask in cands:
        input(f"\nHandset ON-HOOK? Press Enter to try {name} for {a.secs:.0f}s ...")
        tj.wr(0x00, orig | mask)
        print(f"  reg0 -> 0x{tj.rd(0x00):08x}")
        time.sleep(a.secs)
        tj.wr(0x00, orig)
        ans = input("  Did the phone ring / click / buzz? [y/N/q] ").strip().lower()
        if ans == 'q': break
        if ans == 'y': found.append((name, mask))
finally:
    try:
        tj.wr(0x00, orig)
    except Exception:
        pass
    tj.close()
print("\nResult:", found or "nothing rang")
for n, m in found: print(f"  RING MASK 0x{m:x}  ({n})")
