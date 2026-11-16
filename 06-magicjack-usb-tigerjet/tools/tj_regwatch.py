#!/usr/bin/env python3
"""
tj_regwatch.py — find which control register bits track hook state / DTMF on a unit whose
register layout differs from the documented ARM one (e.g. bcdDevice 0.01, 32-byte reports).

Read-only. Polls reg 0x00..0x7c (step 4) and prints every change as old -> new with the
changed-bit mask. Optionally powers the line first (--on, same as `tj_linepower.py on`).

Usage (root):
  tj_regwatch.py [--on] [--secs N]
Then: lift the handset, hang up, press keys (note which digit each time). Paste the output.
"""
import sys, time, argparse
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from tj_linepower import TjLine

ap = argparse.ArgumentParser()
ap.add_argument('--on', action='store_true', help='line_on() first')
ap.add_argument('--secs', type=float, default=60)
a = ap.parse_args()

tj = TjLine()
regs = range(0x00, 0x80, 4)
def snap():
    out = {}
    for r in regs:
        try: out[r] = tj.rd(r)
        except OSError as e: out[r] = None
    return out
try:
    if a.on: tj.line_on()
    base = snap()
    print("baseline:", {f"0x{r:02x}": (None if v is None else f"0x{v:08x}") for r, v in base.items()})
    print("watching... lift/replace handset, press keys (Ctrl-C to stop)")
    t0 = time.time(); prev = base
    while time.time() - t0 < a.secs:
        cur = snap()
        for r in regs:
            if cur[r] != prev[r] and cur[r] is not None and prev[r] is not None:
                print(f"{time.time()-t0:7.2f}s reg0x{r:02x}: 0x{prev[r]:08x} -> 0x{cur[r]:08x}  xor=0x{prev[r]^cur[r]:08x}", flush=True)
        prev = cur
except KeyboardInterrupt:
    pass
finally:
    tj.close()
