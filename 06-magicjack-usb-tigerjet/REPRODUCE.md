# Reproduce it yourself

A step-by-step guide to driving a magicJack HOME's phone port from your own Linux box. If you have the
same hardware, you can follow this end to end. If you have *different* hardware, the **method** — read
the protocol out of the vendor's driver, then find each function with the differential technique — is
the reusable part; see [`HOW-IT-WAS-HACKED.md`](HOW-IT-WAS-HACKED.md) for the reasoning behind each move.

> **Jargon, defined once.** **HID feature report:** a fixed-size control buffer you read/write over USB
> with `GET_REPORT`/`SET_REPORT` ioctls — here, the single 64-byte channel that carries all chip control.
> **Register:** a numbered slot in the chip you read/write to observe or change state. **FXS:** the jack
> a plain analog phone plugs into; it supplies dial tone, ring voltage, and talk battery. **DTMF:** the
> touch-tone digits a phone keypad sends. **Differential method:** change one real-world thing, diff the
> register window before/after — whatever bytes moved *are* that thing's register.

---

## 0. What you need

**Hardware**
- A **magicJack HOME** (Home Edition) that enumerates as USB **`06e6:c200`** (TigerJet, ARM family TJ780/880).
  Check with `lsusb | grep 06e6`. Other TigerJet families exist — the *transport* is the same but the
  register details below are for the ARM chip.
- A plain **analog phone** with an RJ11 plug (to verify dial tone, ring, hook, DTMF for real).
- A Linux host (this was done on Ubuntu; anything with `hidraw` and ALSA works).

**Software**
- Python 3 (the tools use only the stdlib + `numpy` for the DTMF decoder).
- `alsa-utils` (`arecord`/`aplay`), `usbutils` (`lsusb`).
- For the disassembly step: **[radare2](https://github.com/radareorg/radare2)** (`r2` / `rabin2`),
  plus `7z`/`unzip` and `strings`. [Ghidra](https://ghidra-sre.org/) works just as well.

**Access**
- `hidraw` and USB reset need **root** (`sudo`), or a udev rule granting your user access to the
  `06e6:c200` hidraw node.

Eject the ZeroCD mass-storage stub if it keeps re-mounting: `sudo eject /dev/sr0`.

---

## 1. Fingerprint the device

Know every interface before you poke anything. Plugged into USB, the magicJack HOME presents five
interfaces (full detail in [`fingerprint.md`](fingerprint.md)):

```bash
lsusb -v -d 06e6:c200        # descriptors: mass-storage + 3× audio + HID telephony
ls /proc/asound/             # a new card appears (usually "card1", "TigerJet")
ls /dev/hidraw*              # the HID telephony interface -> usually hidraw1
```

Confirm which `hidrawN` is the magicJack (don't assume `hidraw1`):
```bash
for u in /sys/class/hidraw/hidraw*/device/uevent; do grep -l 06E6:0000C200 "$u"; done
```
The included tools auto-detect this node, so you don't have to hard-code it.

**Takeaway:** audio is a standard USB sound card (ALSA card 1, 8 kHz). *All chip control* rides on
interface 4's **64-byte HID feature report**. That report is the whole game.

---

## 2. Get and disassemble the vendor driver (the pivot)

Blind register-poking hits a wall (see §7). The breakthrough is to stop guessing and **read the
protocol out of magicJack's own driver** — specifically the **macOS** build, which ships with symbols.
Everything here is downloaded for **local analysis only**; you never talk to magicJack's SIP/registrar
servers.

magicJack serves its macOS updaters publicly from `upgrades.magicjack.com` (the same host the official
installer fetches from). Two files matter:

- **`magicJackSetup.dmg`** (≈32 MB) — the client setup stub. Its `__DATA` segment has the **entire
  softphone app packed as a ZIP appended to the binary**. The central directory is mid-binary, so
  `unzip` won't open it — carve it by scanning for ZIP **local-file headers** (`PK\x03\x04`) and
  inflating each entry. Out comes `magicJack` (main app), **`mj_dev`** (the device driver), `mj.so`.
  These are *stripped* but keep their C++ **class log-strings** (`CSJHandsetMagicJackDriverBase::…`).
- **`mjisoupdate.dmg`** (≈34 MB) — the firmware updater. It contains **`mjupdate`**, a Mach-O that is
  **symbol-rich C++**: full class names, method names, even source filenames (`DeviceTigerJetMac.cpp`).
  **This is the Rosetta Stone** — the same protocol as the stripped Windows DLL, but readable.

Open `mjupdate` and look for the low-level HID primitives:
```bash
rabin2 -qs mjupdate | grep -iE 'HidWriteTjRegs|HidReadTjRegs|SetFeature|GetFeature|InitTjHardware'
r2 -A mjupdate                 # then: afl~Tj ; pdf @ sym._HidWriteTjRegs_ARM
strings -a mjupdate | grep -iE 'ARMBased|prwC|ProSlic|SetTelephoneEnable'
```

The names tell the story directly:
- `_HidWriteTjRegs` / `_HidReadTjRegs` — normal TigerJet register space.
- `_HidWriteTjRegs_ARM` / `_HidReadTjRegs_ARM` — **ARM-firmware space** (the door blind fuzzing missed).
- `_g_bIsARMBasedChip` — a global that routes writes to the ARM path when set. Our `06e6:c200` is
  ARM-based (`bcdDevice=0x08` → DevType 8), so this path is the live one.
- `CTj880Phone_Hid::InitTjHardware()` — the line bring-up sequence.
- `_ReadAddress` / `_Vitya_HAL_WRITE_UINT32` — arbitrary SoC memory access via the `"prwC"` magic.

Cross-reference the higher-level calls (e.g. `SetTelephoneEnable`) in the stripped `mj_dev` using the
shared class strings + RTTI. Full trace in [`captures/mac-binary-ARM-protocol.md`](captures/mac-binary-ARM-protocol.md).

---

## 3. The HID feature-report framings

All three transports are `HIDIOCSFEATURE`/`HIDIOCGFEATURE` ioctls, report ID 0, 64 data bytes. On Linux
`hidraw` the buffer is 65 bytes: `[00 report-num][ …64 data… ]`, so the "data[0]" below is `buf[1]`.

**Normal TigerJet register R/W** (matches the old non-ARM model):
```
data[0] = command (0x04)   data[1] = reg   data[2] = reg & 0xE0 (bank)   data[3] = count   data[4..] = bytes
READ:  SET [04 <bank> <bank> 00] ; GET ; value = window[reg & 0x1F]
WRITE: SET [04 <reg> <reg&0xE0> 01 <val>]
```

**ARM control-register R/W** ⭐ (32-bit words — note the different command byte and word count):
```
WRITE: SET [20 <reg> 00 01 <b0 b1 b2 b3>]      # cmd 0x20, count = # of 32-bit words, LE
READ:  SET [00 00 <reg> 00] ; GET ; value = resp[0:4] little-endian
```

**ARM SoC memory R/W** ⭐⭐ (arbitrary CPU address; `"prwC"` = `70 72 77 43`):
```
READ  addr:  SET [80 04 0e 04 <addr32 LE> 70 72 77 43] ; GET (val = resp[0:4]) ; SET [80 14 .. "prwC"]
WRITE addr:  SET [80 02 0e 04 <addr32 LE> 70 72 77 43] ; SET [44 <val32 LE>] ; SET [80 12 .. "prwC"]
```

Two footguns that cost real time:
1. The ARM write path counts **32-bit words**, not bytes. `count=1` means "expect 4 payload bytes."
   Sending a single byte (`B4`) instead of a word (`B4 00 00 00`) **stalls the endpoint** — this is
   the exact mistake that made the whole ARM avenue look dead.
2. Reg `0x55` is the read-only chip-revision register. Hammering it looks like "all writes fail" but is
   just writing to a read-only reg — not a transport problem.

---

## 4. Prove you have memory access (the "we're in" moment)

Before trusting anything, confirm the memory port is real by reading the CPU's own boot vectors:

```bash
sudo python3 tools/tj_armmem.py --read 0x00000000     # -> 0xea000012  (ARM "B" reset-vector branch)
sudo python3 tools/tj_armmem.py --read 0x00000004     # -> 0xe59ff014  (LDR pc,[pc,#0x14] exception vec)
sudo python3 tools/tj_armmem.py --dump 0x0 0x200 boot.bin
```

`0xea000012` is a genuine ARM branch instruction; the dump decodes as real reset code (enter SVC mode,
mask IRQ/FIQ, bring up SDRAM). A round-trip you can *verify* beats any plausible guess — this is how you
*know* you have arbitrary read, not a mirror. A clean 512-byte reference dump is in
[`captures/arm-mem-dump-0x0.bin`](captures/arm-mem-dump-0x0.bin).

**Readable memory map** (stay inside these):
- `0x00000000 – ~0x01000000` — SDRAM/ROM (16 MB): firmware + vectors.
- `0x98xxxxxx` — SoC peripherals (watchdog `0x98700xxx`, GPIO/misc `0x98100xxx`).
- ⚠️ **Any other high address stalls and *latches* the endpoint** (EPIPE cascade). `tj_armmem.py`
  recovers with `USBDEVFS_RESET`, but only read addresses you know are mapped.

---

## 5. Power the line, then find every other function

Powering the FXS line is a replay of `InitTjHardware()` over the ARM control-register port:

```bash
sudo python3 tools/tj_linepower.py on       # reg0|=1 ; reg0x38=3 ; reg0x14 bit7 strobe
```
Lift the attached handset — you should hear **dial tone**, and the port LED should light. `off` clears
`reg0` bit0 and the line drops; it's repeatable on→off→on.

### The differential register-discovery method (reusable)

Not everything is named in the symbols. For the rest, use this — it needs **no disassembly**:

> Snapshot the register window in state **A**, change one real-world thing, snapshot state **B**, and
> **diff**. Whatever bytes moved *are* the register for that thing.

Do it with the phone attached and a human toggling the real thing (don't run a blind timed window and
conclude while nobody's at the phone). This pinned down, on the ARM chip:

| Function | Found by | Result |
|---|---|---|
| **Hook state** | diff on-hook vs off-hook | exactly one bit moved: **`reg0x14` bit 31** (`0x0b000104` ↔ `0x8b000104`) |
| **DTMF** | hold keys '1', '5', '0' and diff | on-chip decoder writes **`reg0x14`**: byte 0x16 = key-valid, byte 0x17 low nibble = digit (1–9→1–9, 0→0xA, *→0xB, #→0xC) |
| **Ring** | trace `SetRinging` vtables + test | **`reg0 \|= 0x300`** (bits 8–9); firmware makes the ring voltage |

Verify each:
```bash
sudo python3 tools/tj_linepower.py monitor    # lift/replace handset -> hook flips live
sudo python3 tools/tj_linepower.py ring 3      # the bell physically rings, 3 cadence cycles
```

Because the chip decodes DTMF into a register, you can read dialed digits **without** touching audio,
and the firmware dial tone is never disturbed.

---

## 6. Two-way audio (no RE needed)

The audio side is standard USB Audio Class on ALSA **card 1**, native **8 kHz** mono. Record the line
and play a tone into the handset:

```bash
arecord -D plughw:1,0 -f S16_LE -c1 -r8000 -d 10 line.wav      # capture the line
aplay   -D plughw:1,0 tone8k.wav                                # host -> handset
```

DTMF also decodes cleanly from the capture stream if you'd rather not read the register:
```bash
arecord -D plughw:1,0 -f S16_LE -c1 -r8000 -d 15 dtmf.wav
python3 tools/tj_dtmf_decode.py dtmf.wav       # Goertzel dual-tone decode
```

> **Gotcha that matters for integration:** the device **captures at 8 kHz only**. Asterisk's console
> channel drivers (`chan_alsa`/`chan_console`) are hardwired to 16 kHz; forcing a resample gives XRUNs
> and choppy one-way audio. Match the native rate instead — see §8.

---

## 7. The dead ends (they're instructive)

Kept honestly, because they're the most useful part for someone reproducing this:

- **Blind register fuzzing over the HID report** mapped normal TigerJet registers fine but **stalled on
  every ProSLIC/line write**. Two full sweeps concluded the port was "firmware-locked" and impossible
  from Linux. **That conclusion was wrong.** Blind fuzzing shows you *where* the walls are, never *why*
  — and it's easy to over-generalize a stall. (Log: [`captures/hardware-fuzzing-log.md`](captures/hardware-fuzzing-log.md).)
- The stall had a mundane cause: on ARM chips the host-side ProSLIC/SPI-bridge path is a **no-op**
  (`_WriteProSlicDirectReg` returns without writing when `_g_bIsARMBasedChip` is set), and the one ARM
  probe that *was* tried used the wrong payload width. The firmware owns the ProSLIC; you reach it
  through the ARM register port, not the SPI bridge.
- The `0x80` **flash-page** path (sub-register `0x13`, not the `0x0e` memory port) only responds in the
  device's **updater/bootloader mode**; in normal softphone mode it stalls. You don't need it — the
  `0x0e` memory port and the `0x20` register port work in normal mode and do everything.

**Lesson:** when you're guessing at an undocumented protocol and hit a wall, stop guessing and go find a
description of the protocol. It's almost always in the driver — and vendors often strip only *one*
platform's build.

---

## 8. Wire it into Asterisk (optional)

To turn the working primitives into a real PBX extension:

- **Audio:** run a **baresip** softphone bound to ALSA `plughw:1,0` at native 8 kHz µ-law (no
  resampling) and register it to Asterisk as a normal SIP endpoint.
- **FXS behavior:** a small root daemon ([`../03-magicjack-sip/mj-fxs-bridge.py`](../03-magicjack-sip/mj-fxs-bridge.py))
  reads the hook/DTMF registers from §5 over `/dev/hidraw` and drives baresip via its `ctrl_tcp`
  control socket — off-hook → dial tone + keypad dialing, inbound → ring the bell, answer on off-hook,
  hangups both ways, reorder tone when the far end hangs up first.

Full integration write-up, config, and systemd units:
[`../03-magicjack-sip/README-fxs-usb.md`](../03-magicjack-sip/README-fxs-usb.md).

---

## Safety notes

- **Reads are safe; writes need care.** ARM control-register writes and SoC memory writes can perturb
  firmware state or reboot the device (there's a watchdog at `0x987000xx`). Read-modify-verify every
  write; never write blind; be ready to `USBDEVFS_RESET` (the tools do it).
- **Never issue the `0x80` erase/program sub-commands** (flash-page path) — those can brick the device.
  Everything in this guide is read-side or volatile control registers that a power-cycle clears.
- ARM control registers **persist across a USB reset** — only a physical unplug/replug fully clears
  them. After a run the device sits in its normal initialized state; this is harmless.
