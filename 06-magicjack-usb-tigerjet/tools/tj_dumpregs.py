#!/usr/bin/env python3
"""
tj_dumpregs.py — snapshot the full ARM control-register file of the magicJack USB dongle
(TigerJet 06e6:c200), the ground-truth diagnostic recovered from CTjIpDev::DumpRegistersARM()
(@0x100035be0 in the macOS mjupdate binary). Read-only and safe.

Reads the registers the firmware's own dump routine reads — 0x00..0x54 step 4, plus 0x58 and
0x5c — and the two extras the RE surfaced (0x40 digital-loopback, 0xf0 WiFi-mailbox status).
Use it to:
  * snapshot before/after any register-write experiment (restore = write the saved values back), and
  * diff across line states (on-hook / off-hook / ringing / audio) with --watch to reverse the
    still-unknown telemetry and GPIO registers directly on hardware.

Known meanings (from the RE — see captures/mac-binary-feature-catalog.md):
  0x00 reg0  : bit0 line-power, bits8-9 ring, bit16 tip/ring polarity
  0x14 reg14 : bit31 hook, bit9 dial-tone, bit7 activate-strobe, bit4 DTMF-mute
  0x38       : codec/master-clock (=3 when line up)      0x40 : digital-loopback (bit4)
  0x58/0x5c  : legacy AFE (Si321x gain/DTMF tuning, 6-bit fields)
  0x20 24 28 2c 30 3c 44 48 4c 50 54 : read-only telemetry (meaning TBD — diff to learn)

Usage:
  sudo python3 tj_dumpregs.py                 # one snapshot (human-readable)
  sudo python3 tj_dumpregs.py --json          # machine-readable snapshot
  sudo python3 tj_dumpregs.py --watch [secs]  # poll and print only registers that changed
"""
import os, sys, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tj_armreg import ArmReg

# the exact set DumpRegistersARM() reads (0x00..0x54 step 4, 0x58, 0x5c), plus RE-surfaced extras
REGS = sorted(set(list(range(0x00, 0x58, 4)) + [0x58, 0x5c, 0x40, 0xf0]))

LABEL = {
    0x00: "reg0  bit0=power bits8-9=ring bit16=polarity",
    0x14: "reg14 bit31=hook bit9=dialtone bit7=strobe bit4=dtmf-mute",
    0x38: "codec/master-clock (=3 up)",
    0x40: "digital-loopback (bit4)",
    0x58: "legacy AFE 0x32 (6-bit field)",
    0x5c: "legacy AFE 0x33 (6-bit field)",
    0xf0: "WiFi mailbox (absent on c200)",
}

def snapshot(a):
    # read_stable filters the device's GET-window glitch (a read occasionally returns reg0x14's
    # status window instead of the selected register under rapid polling)
    return {r: a.read_stable(r) for r in REGS}

def _decode(snap):
    p = snap[0x00]; h = snap[0x14]
    return (f"line={'ON' if p & 1 else 'off'}  ring={'ON' if p & 0x300 else 'off'}  "
            f"polarity={'reversed' if p & 0x10000 else 'normal'}  "
            f"hook={'OFF-HOOK' if h >> 31 else 'on-hook'}  "
            f"dialtone={'ON' if h & 0x200 else 'off'}  dtmf-mute={'ON' if h & 0x10 else 'off'}")

def main(argv):
    a = ArmReg()
    try:
        if '--watch' in argv:
            secs = 3600.0
            i = argv.index('--watch')
            if i + 1 < len(argv):
                try: secs = float(argv[i + 1])
                except ValueError: pass
            print(f"# watching {len(REGS)} ARM regs for {secs:g}s — printing only changes (Ctrl-C to stop)")
            prev = snapshot(a)
            for r in REGS:
                print(f"  0x{r:02x} = 0x{prev[r]:08x}   {LABEL.get(r, '')}")
            print(f"# {_decode(prev)}")
            t0 = time.time()
            while time.time() - t0 < secs:
                cur = snapshot(a)
                for r in REGS:
                    if cur[r] != prev[r]:
                        print(f"  {time.time() - t0:6.2f}s  0x{r:02x}: 0x{prev[r]:08x} -> 0x{cur[r]:08x}   {LABEL.get(r, '')}")
                prev = cur
                time.sleep(0.05)
            return 0

        snap = snapshot(a)
        if '--json' in argv:
            print(json.dumps({f"0x{r:02x}": f"0x{v:08x}" for r, v in snap.items()}, indent=2))
            return 0
        print(f"# magicJack ARM control-register snapshot ({len(REGS)} regs) — DumpRegistersARM replay")
        for r in REGS:
            print(f"  0x{r:02x} = 0x{snap[r]:08x}   {LABEL.get(r, '')}")
        print(f"# decoded: {_decode(snap)}")
    finally:
        a.close()
    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv))
