# ★★★ SOLVED — FXS line powered from Linux (dial tone + LED, verified on/off)

**2026-07-15, hardware-verified with the RJ11 handset attached and a human watching the port:**
Replaying `CTj880Phone_Hid::InitTjHardware()` via the TigerJet **ARM control-register port** produces
**dial tone + a lit port LED**; clearing the enable bit drops it; repeatable on→off→on.

Enable = the InitTjHardware sequence (ARM reg writes, cmd 0x20 `20 <reg> 00 01 <val32 LE>`):
```
reg0  |= 1            # line-enable bit  (clearing reg0 bit0 = line OFF, verified)
reg0x38 = 3           # codec/clock (reads back 0x07)
reg0x14 |= 0x80 ; sleep 10ms ; reg0x14 &= ~0x80    # activate strobe
```
Register read (cmd 0 window): `SET [00 00 <reg> 00]` then GET, value = resp[0:4] LE.
One-liner: `sudo python3 tools/tj_linepower.py on` (also `off` / `status`).
The SPI-bridge/ProSLIC "gate" was a red herring: on ARM chips the host doesn't touch the ProSLIC
directly (that path is a no-op) — the ARM firmware powers the line when the host sets these control
registers. NOTE: ARM control regs persist across USB reset (only a physical unplug resets them).

**Hook detection ALSO solved (verified with the handset, user-coordinated differential):**
`GetHookState` on the 880 returns cached `[this+0x58]`, which InitTjHardware seeds from **reg0x14 bit 31**.
Confirmed live on hardware by an on-hook vs off-hook register diff: exactly one byte moved —
reg0x14 `0x0b000104` (on-hook) ↔ `0x8b000104` (off-hook), i.e. **bit 31 = handset state**. Poll it:
`tools/tj_linepower.py hook` / `monitor`. No interrupt stream needed (the 880's KeyScan is a no-op; the
HID interrupt-IN reports don't flow to hidraw without more setup, but polling reg0x14 bit31 works cleanly).

**★ COMPLETE FXS-over-USB from Linux — all four capabilities hardware-verified (2026-07-15):**
| Capability | Mechanism | Verified |
|---|---|---|
| Line power on/off | ARM reg writes (InitTjHardware replay), `tj_linepower.py on/off` | dial tone + port LED, on→off→on |
| Hook detection | poll **reg0x14 bit 31** (`tj_linepower.py hook/monitor`) | on-hook 0x0b000104 / off-hook 0x8b000104 |
| Audio capture (line→host) | ALSA **card 1** `plughw:1,0`, S16_LE 8kHz mono | recorded live line; on-hook rms~292 vs off-hook rms~513 |
| Audio playback (host→line) | ALSA **card 1** playback | 1 kHz tone sent, **heard clearly in the handset** |
| DTMF (keypad → host) | decode card-1 capture (Goertzel, `tools/tj_dtmf_decode.py`) | **exact match on live "9 8 7 6 5 3 #"** |
| **Ring the handset** | **reg0 \|= 0x300** (bits 8-9 = ring request; firmware makes the voltage), toggle 2s/4s | **the handset physically rang** |

**Ring mechanism (from `mj_dev`, CTj880 vtbl[0x150] = the device ring method):** read ARM reg0,
clear bits 8-9, and if enabling `|= 0x300`, write reg0 back via the ARM reg-write port (cmd 0x20). The
`SetRinging`→`EnableRinger` path also builds a Bellcore caller-ID WAV and runs a 2000 ms cadence timer,
but the raw hardware ring is just **reg0 bits 8-9**; the ARM firmware generates the actual ring voltage.
`tools/tj_linepower.py ring [cycles]` (also `ring-on`/`ring-off`). Verified: the physical handset rings.

Two-way audio works with **no extra routing setup** — the InitTjHardware line-power state is sufficient to
carry audio both directions over card 1, and keypad **DTMF decodes cleanly from the capture stream**
(verified against a known live sequence; ~0.5–2% clipping at default gain, harmless). This is the full
USB-door goal: the dongle is a **complete Linux-driven FXS station** — line power, hook, two-way audio, and
DTMF, all hardware-verified. Remaining is pure integration (bridge card 1 + `tj_linepower.py` hook poll +
line power into Asterisk via chan_alsa; Asterisk's own DTMF detection handles digits from the card-1 audio).
(Still does NOT carry the magicJack phone number — that's the Ethernet/ATA brain; see ../03-magicjack-sip.)

---

# BREAKTHROUGH — the ARM register transport (from the macOS binary, WITH SYMBOLS)

Source: `mjupdate` (the macOS firmware-updater), pulled from
`https://upgrades.magicjack.com/upgrade/mjisoupdate.dmg` (34 MB, the "El Capitan
compatibility" updater). Unlike the stripped Windows `TjIpSys.dll`, this Mach-O is a
**symbol-rich C++ binary** — source filenames (`DeviceTigerJetMac.cpp`), full class
hierarchy, and named low-level primitives all preserved. This is the definitive source
for the USB/HID chip-control protocol.

## Why this matters
Prior Linux fuzzing concluded the SPORT/SPI-bridge (ProSLIC) was "firmware-gated" because
every attempt to write the ProSLIC space **stalled**. The Mac binary shows the real reason:
**our TJ780/880 is an ARM-based chip, and ARM-space register access uses a DIFFERENT HID
feature-report framing** than the normal TigerJet register writes Linux had working. Blind
fuzzing tried the ARM command byte (`0x20`) once, but with the wrong payload width and target,
so it stalled — and the whole avenue was written off. It should not have been.

## The class hierarchy (all symbolized)
- `CTjIpDev` — base device (register access, hook/DTMF, EEPROM, ring, echo-canceller).
- `CTj560BPhone_Hid` / `CTj560CPhone_Hid` — **older** chips: carry the full host-side ProSLIC
  bring-up (`InitProSlic`, `calibrateAndActivateProSlic`, `initializeIndirectRegisters`,
  `powerUp`, `ReadVBat`, `powerLeakTest`). These are the `tjctl.c`-style parts.
- `CTj780Phone_Hid` / `CTj880Phone_Hid` / `CTj980Phone_Hid` / `CTj911Phone_Hid` — **our family**.
  They override only `InitTjHardware()`, `KeyScan()`, `Reboot()`, `UpdateDialtone()`, etc.
  They do **NOT** carry their own ProSlic methods → on these chips the ProSLIC/line bring-up is
  owned by the **on-chip ARM firmware**, reached via the ARM register window (below), not by
  host-side SPI-bridge writes. This finally *explains* the prior wall instead of just hitting it.

## Low-level primitives (all named in the binary)
`_HidWriteTjRegs` / `_HidReadTjRegs` — normal TJ register space.
`_HidWriteTjRegs_ARM` / `_HidReadTjRegs_ARM` — **ARM-firmware space** (the new door).
`_SetFeature` / `_GetFeature` → `IOHIDDeviceSetReport` / `IOHIDDeviceGetReport` (feature reports).
Routing (`_HidWriteTjRegs`): if device open AND `_g_bIsARMBasedChip` set AND the request byte has
its high bit set → dispatches to `_HidWriteTjRegs_ARM`. Global `_g_bIsARMBasedChip` selects family.

## Shared HID feature buffer `_m_Buffer` (@ 0x1000750f0), field layout
`_SetFeature` writes the HID report-id into `_m_Buffer[0]`, then submits **`_m_Buffer[1..]`** as the
report via `IOHIDDeviceSetReport(dev, type=2 Feature, reportID=0, buf=_m_Buffer+1, len)`.
Report length `len` = **0x40 (64) for ARM chips**, 0x20 (32) for non-ARM. (Our device = 64, as used.)
On Linux `hidraw` the 65-byte buffer is `[00 report-num][ ...64 data... ]`, so the 64 data bytes below
map directly to `HIDIOCSFEATURE` buffer[1..64].

### Normal write `_HidWriteTjRegs` (64-byte data) — MATCHES what Linux already had
```
data[0] = request/command   (e.g. 0x04)
data[1] = reg index
data[2] = reg & 0xE0        (bank)   [or full reg if a flag set]
data[3] = count             (# of BYTES, clamped ≤ 0x1C = 28)
data[4..] = count data bytes
```
→ single-reg form on the wire: `04 <reg> <reg&0xE0> 01 <val>`  ✅ (confirmed earlier from Linux).
Paginates: reg += 0x1C, len -= 0x1C per 28-byte block.

### ⭐ ARM write `_HidWriteTjRegs_ARM` (64-byte data) — THE NEW FRAMING
```
data[0] = 0x20 | (request & 1)   → 0x20 or 0x21   (NOT 0x04!)
data[1] = ARM reg address
data[2] = 0x00                   (hardcoded — NOT reg&0xE0)
data[3] = count of 32-bit WORDS  (clamped ≤ 0x0F = 15)
data[4..] = count × 4-byte LITTLE-ENDIAN words   (payload is 32-bit words, not bytes)
```
Paginates: reg += 0x3C (60 = 15 words × 4B), len -= 15 per block (only when flag bit 0 == 0).
→ single-word write on the wire: **`20 <armreg> 00 01 <b0> <b1> <b2> <b3>`**

### ARM read `_HidReadTjRegs_ARM`
```
SET_FEATURE request: data[0] = (request & 1)   (0x00/0x01, NOT 0x20)
                     data[1] = 0x00
                     data[2] = ARM reg address
                     data[3] = 0x00
then GET_FEATURE; result = count × 4-byte words copied from returned data[0..].
```

## Why the old fuzz stalled (now explained)
Prior log: "`20 55 00 01 B4 …` STALLS." Three faults vs the real ARM frame:
1. payload was a **single byte** `B4`; the ARM path requires a **4-byte word** (`B4 00 00 00`),
   and `data[3]=count` counts **words**, so `count=1` means "expect 4 payload bytes".
2. `reg 0x55` is the read-only revision reg — not a valid ARM target.
3. `data[2]` must be `0x00` (it was, by luck) — good, but items 1–2 alone stall it.

## NEXT — actionable, testable on live hardware (not a guess)
1. **ARM read sweep** (safe, read-only): for a range of ARM addresses, send
   `SET_FEATURE [00][00][addr][00][00…]` then `GET_FEATURE`, and log the 4-byte words. This
   confirms the ARM window responds at all (the missing "read-verification primitive" from before).
2. **ARM write test**: `SET_FEATURE [00][20][addr][00][01][b0 b1 b2 b3]`, then ARM-read back to
   verify. If round-trip works, the ARM door is open from Linux.
3. Trace `CTj780Phone_Hid::InitTjHardware()` and how line power is triggered on ARM chips
   (likely a specific ARM-register/word sequence, or a higher-level firmware command), then
   replay it. `DumpRegistersARM()` / `DumpRegisterARM(int)` are ready-made readback helpers to mirror.

## ⭐⭐ THE PRECONDITION — the ARM "open" handshake (from `_OpenTjDevice`, 1424 bytes)
Hardware test (`tools/tj_arm.py`, read-only) with the exact ARM read frame:
`SET_FEATURE [00][00][00][addr][00…]` → `GET_FEATURE`. Result on the live c200:
- **addr 0x00 → returns `0x00000040`, NO stall** (the framing is structurally accepted!).
- **addr 0x01–0x3F → all STALL (EPIPE/errno 32).**
That pattern = the ARM window needs an **open/enable handshake first**. `_OpenTjDevice` provides it:

1. **DevType / ARM detection:** `_IOHIDDevice_GetVersionNumber() & 0xF`, `-1`, indexed into table
   `0x10004e2a0` → `_g_DevType`. `_g_bIsARMBasedChip = (DevType ∈ {5,7}) || (DevType == 8)`.
   `_g_bIsSpiFlash` set from the SCSI/HID id word at `0x100075142` (`&0xFC==0x14` or `==0xBF41`).
2. **First ARM read:** `_HidReadTjRegs_ARM(0x14, buf, count=1, flag=4)` — reads ARM reg 0x14.
3. **Then a sequence of raw command-`0x80` feature reports** (built inline, sent via `_SetFeature`).
   Report data bytes (buf[1..], report-id in buf[0]):
   ```
   80 04 13 04  00 00 00 00  70 72 77 43      ("prwC")
   80 14 00 00  00 00 00 00  70 72 77 43
   80 04 08 04  00 00 00 00  70 72 77 43
   ```
   i.e. **command 0x80, then {subcmd, reg, len}, a 32-bit field, and the ASCII magic
   `70 72 77 43` = "prwC" (LE 0x43777270)**. This magic-guarded 0x80 command is the access
   enabler — the analogue of the Windows flash-guard magic `0x07773456` in `protocol.md`.
4. Then a **loop of `GetFeature`** calls collecting a large (0x680-byte) block into a buffer —
   the readback channel for whatever the 0x80 command addressed.

### ⚠️ SAFETY — this is the SPI-FLASH access path
The 0x80/"prwC" command lives among `bm_Erase_One_Spi_Sector` / `bm_Write_One_Page` / `EraseSpiSector_HID`
(`_g_bIsSpiFlash`). Replaying the **read** side is safe; issuing any **erase/write** sub-command
risks corrupting device firmware (brick). Rule for the next iteration: **replay ONLY read/status
sub-commands, never erase/program**, and diff-verify against a known-good dump before any write.

### Next iteration (concrete)
1. Fully decode `_OpenTjDevice`'s 0x80/"prwC" sequence + the GetFeature readback loop and the exit
   test (what makes it declare success). Identify which sub-commands are read-only vs destructive.
2. Implement the open handshake in `tools/tj_arm.py` (read-only sub-commands only), then re-run the
   ARM read sweep — expect addr≥1 to stop stalling once the window is enabled.
3. If the ARM window opens: read the ARM register map, locate the line/ProSLIC-control words, and
   only then consider a minimal, reversible write to power the FXS port.

## HARDWARE RESULT (2026-07-15) — the ARM/flash surface is DEVICE-MODE-gated, not framing-gated
Ran the recovered framing + open handshake against the live c200 (`tools/tj_arm.py`, read-only),
including a clean **USB reset** between attempts to rule out latched error state:

- **Clean state, single gentle probe:** ARM reads do **not** stall (earlier all-stall was the device
  latching after being hammered with 63 rejected reports — state drifts, confirmed by the GET window
  changing between runs). BUT the reads return the **ordinary page-select mirror, not a distinct ARM
  register space**: `SET [00 00 14 00]` then GET returned exactly the bytes at **offset 0x14 of the
  normal GET window** (`04 01 00 00 99 01 40 01 …`); `SET [00 00 00 00]` returned the default window
  unchanged. So in this mode **command byte 0x00 = page-select** (byte3 = page), exactly like the
  normal read path — it is *not* reaching an ARM register file.
- **`0x80`/"prwC" flash-open:** **STALLS** (EPIPE) in normal softphone mode.

**Interpretation (revised, precise):** the c200's normal enumeration (Mass-Storage + USB-Audio + HID,
what we have) does **not** expose the ARM/flash HID command surface. The Mac `mjupdate` ARM/flash path
(command 0x20 ARM-reg, command 0x80 "prwC" flash) is only live when the device is in its
**updater/bootloader mode** — and `mjupdate`'s own Read Me *requires* that mode ("plug in, wait for the
magicJack **drive window** to appear," "Detected a magicJack device. Opening…"). We are in softphone
mode, not updater mode. This finally distinguishes the two possibilities the old fuzzing log couldn't:
it is **MODE-gated, not transport-gated** — and we now hold the exact protocol for when the device IS
in updater mode.

### ✅ Confirmed: OUR unit IS ARM-based (so this avenue is real, not a red herring)
DevType table @ `0x10004e2a0` = `[1, 2, 0, 4, 5, 0, 7, 8]`. Detection: `DevType = table[(bcdDevice & 0xF) - 1]`.
Our c200 `bcdDevice = 0x08` → index `(8&0xF)-1 = 7` → `table[7] = 8`. ARM iff DevType ∈ {5,7,8} →
**DevType 8 = ARM-based**, the flash/GSM-capable variant (it also takes the special end-of-open step
`_ReadAddress(0x98100060)` → `_Vitya_HAL_WRITE_UINT32(0x98100060, x|0x40000)`). So the ARM/flash
protocol applies to *this exact device* — the only thing standing between us and it is **updater mode**.

### ⭐ The real lever now: how does the device ENTER updater mode?
`mjupdate` calls `_UpdateScsiInfo_HID` and reads the SCSI id word — the **Mass-Storage interface**
(the ZeroCD LUN `/dev/sg2`, earlier dismissed) is implicated in the mode handshake. Candidates for
the next iteration:
1. **Reverse how `mjupdate`/`upgrade` triggers updater mode** — look for a SCSI vendor CDB, a
   `START STOP UNIT`/eject, or an HID command that flips the firmware from softphone → updater
   enumeration. `_UpdateScsiInfo_HID`, `_HIDBuildDeviceList`, `_FindTjFeatureElements`, and the
   mass-storage strings in `mjupdate` are the trace targets.
2. Check whether updater mode is a **distinct USB PID** (e.g. the device re-enumerates as a different
   VID:PID or adds an interface) — run `mjupdate` under a Windows/mac VM with USB passthrough, or just
   watch `dmesg`/`lsusb` while triggering the suspected mode-switch, and diff the descriptors.
3. Confirm our unit's `_g_DevType` (from the HID version number, `GetVersionNumber & 0xF` → table
   `0x10004e2a0`, ARM iff ∈{5,7,8}) — tells us definitively whether this unit even uses the ARM path.

## ⭐⭐⭐ SOLVED (2026-07-15) — ARBITRARY ARM SoC MEMORY ACCESS WORKS IN NORMAL MODE
The updater-mode question turned out to be moot for *memory* access. The macOS binary has two
higher-level primitives built on the 0x80/"prwC" command that use **sub-register 0x0e = a raw
CPU-memory address port** (NOT 0x13 = flash-page, which is the mode-gated one that stalled):

- **`_ReadAddress(addr)`** — arbitrary 32-bit read:
  `SET 80 04 0e 04 <addr32 LE> "prwC"` → `GET_FEATURE` (value = resp[0:4]) → `SET 80 14 … "prwC"`.
- **`_Vitya_HAL_WRITE_UINT32(addr,val)`** — arbitrary 32-bit write (3 reports):
  `SET 80 02 0e 04 <addr32 LE> "prwC"` → `SET 44 <val32 LE>` → `SET 80 12 … "prwC"`.
  (`_RebootTj880` is just this write hitting the watchdog regs 0x98700004/8/c — no HID reg needed.)

**Hardware-CONFIRMED (read) on the live c200, normal softphone enumeration, no updater mode:**
```
0x00000000 = 0xea000012   <- ARM reset-vector "B" instruction
0x00000004 = 0xe59ff014   <- LDR pc,[pc,#0x14]  (exception vectors)  ... vectors x7
0x00100000 = 0xe1a05001   (mov r5,r1 — ARM code at 1MB)
0x00200000 = 0xebffe766   (bl … — ARM code at 2MB)
0x98700004 = 0x005b8d80   (watchdog counter)   0x98100204 = 0x140a0101 (GPIO/config)
```
A clean 512-byte dump from addr 0 (`captures/arm-mem-dump-0x0.bin`) decodes as the genuine CPU boot
code: vector table → vector targets → reset init `bic r0,#0x1f / orr r0,#0xd3 / msr CPSR,r0` (enter
SVC, mask IRQ/FIQ) → register clears → SDRAM/clock bring-up. **This is real, sustained ARM SoC memory
read from Linux userspace over HID — the master access primitive.** Tool: **`tools/tj_armmem.py`**
(`--read`, `--dump`, and a `--write`/`--allow-write`-gated write).

### Readable map (so far) & the one gotcha
- **0x00000000 – ~0x01000000**: SDRAM/ROM (16 MB) — firmware & vectors live here.
- **0x98xxxxxx**: SoC peripherals (watchdog 0x98700xxx, GPIO/misc 0x98100xxx, …).
- **Unmapped addresses (≥0x10000000 except 0x98…) STALL and *latch* the endpoint** (EPIPE, then a
  cascade; 0x40000000 gave ESHUTDOWN). Recover with `USBDEVFS_RESET` (tj_armmem does this). So: only
  read known-mapped ranges; a bad address wedges the session until reset.

### What this unlocks (next, in priority order)
1. **Dump the full firmware** from RAM/ROM (0x0–0x1000000, avoiding holes) for offline RE — locates
   the ProSLIC/SLIC driver and the line-power code paths directly in the running firmware.
2. **Map the SoC peripheral registers** that gate the FXS line: the ProSLIC is driven by the ARM
   firmware writing SoC/SPI registers; with arbitrary read we can watch them change when the line
   state changes, then (carefully, reversibly) **write** the line-power register to light the port —
   the original goal, now reachable without touching the SPI-bridge HID gate at all.
3. **Reboot control** via the watchdog write (`_RebootTj880` sequence) if a re-init is ever needed.
⚠️ Writes to SoC/peripheral space can reboot/hang the device — keep writes minimal, reversible, and
never blind; read-modify-verify each one.

## Line-power hunt — progress (2026-07-15)
Using the working ARM memory port (`tools/tj_armmem.py`):
- **Firmware dump works** at ~30 KB/s via 64-byte block reads (one SET-addr → one 64-byte GET returns
  16 consecutive words; verified against per-word reads). Dumped `0x0–0x40000` (`/tmp`, code only —
  **no ASCII strings** in that region; it's pure ARM code + vector tables). Full 16 MB is impractical
  (~9 min/MB); dump targeted regions the vectors point at instead.
- **SoC peripheral baseline captured** (port unpowered): `captures/soc-peripheral-baseline-unpowered.txt`
  — 0x98100000 (GPIO/pin-mux) and 0x98700000 (timer/WDT) blocks. This is the reference for a
  powered-vs-unpowered diff to locate the FXS line-power register.

### Key structural finding: on ARM chips the host does NOT drive the ProSLIC directly
- `_WriteProSlicDirectReg(reg,val)` builds `[reg&0x7f][val][00][0x63]` and calls
  `_HidWriteTjRegs(0x26,…,request=4)` — but that routing **returns a no-op on ARM chips**
  (request≥0 + `_g_bIsARMBasedChip` → the function returns without writing). So the old SPORT/ProSLIC
  HID writes were *architecturally* dead on our device, not just mis-framed. The ProSLIC is owned by
  the on-chip ARM firmware.
- Our chip's `CTj880Phone_Hid::InitTjHardware()` drives **ARM firmware registers** via
  `_tjinp_ARM`/`_tjoutp_ARM` (the command-0x20 register port): read reg0 |=1 → write reg0; write
  reg 0x38 = 3; read reg 0x14, save bit31, set bit7, **10 ms sleep**, clear bit7. That bit-7 strobe on
  reg 0x14 is a reset/enable pulse. These ARM "registers" are the firmware control surface.

### Two concrete ways forward to actually power the port
1. **Get the real softphone client binary.** The DMG only ships the *setup stub* (`upgrade`) +
   *flasher* (`mjupdate`). The stub downloads `magicJack.app/Contents/MacOS/magicJack` at runtime —
   THAT binary has `SetTelephoneEnable`/the full line-power sequence for our chip. Recover its download
   URL from the stub (`DownloadUpgrade`/`ExecuteUpgrade`, base `upgrades.magicjack.com`) and RE it the
   same way. **Highest-value, lowest-risk next step.**
2. **Replay `CTj880Phone_Hid::InitTjHardware()`'s ARM-reg writes** (reg0|=1, reg0x38=3, reg0x14 bit7
   strobe) via the command-0x20 register port, with the **handset attached**, and watch the peripheral
   snapshot + port LED for change. Careful/reversible — ARM-reg writes can perturb firmware state; do
   read-modify-verify and be ready to USB-reset. Pair with a powered-vs-unpowered peripheral diff.

## ⭐ Got the real softphone driver (`mj_dev`) — SetTelephoneEnable traced to the device vtable
The 32 MB `magicJackSetup.dmg` setup stub (`upgrade`) has the **entire softphone packed as a ZIP inside
its `__DATA.__data`** (central dir mid-binary, so `unzip` won't open it — carve via local headers +
inflate; script pattern saved). Extracted to `scratchpad/mac/softphone/`:
`magicJack` (22.8 MB main app), **`mj_dev` (6.2 MB device driver)**, `mj.so`, `config`, `checksums`.
`mj_dev`/`magicJack` are **stripped** (only 2 nominal symbols) BUT retain the class log-strings
(`CSJHandsetMagicJackDriverBase::…`) — same hierarchy as the symbol-rich `mjupdate`, so cross-reference
works.

**SetTelephoneEnable line-power path (from `mj_dev`, x86_64 slice):**
- A command table registers `"SetTelephoneEnable"` → handler `func.10000b640`.
- Handler for arg **"1" (enable)** calls, on the device object (`r12`):
  `vtbl[0xb8](0)` → `vtbl[0x98](0)` → `bool = vtbl[0xe0]()` ; if `!bool`: `subobj=[r12+0xe0]; subobj->vtbl[0x90](1)` ;
  then sets `[r12+0x1b1] = 1`. Arg "0" just clears `[r12+0x1b1]=0`.
- So powering the line = three vtable calls (0xb8, 0x98, 0xe0) + a conditional `[sub+0x90](1)`. These
  slots correspond to `CTjIpDev`/`CTj880Phone_Hid` methods whose **bodies are readable in `mjupdate`**
  (e.g. `InitTjHardware`, hook/line state, ProSLIC/enable). Next: reconstruct the device vtable layout,
  map 0xb8/0x98/0xe0/0x90 to named `mjupdate` methods, and express them as the concrete ARM
  register/memory writes to replay via `tools/tj_armmem.py` (register port 0x20 for the `_tjoutp_ARM`
  ops; memory port 0x0e for any SoC-register writes). Then test on hardware (handset attached, watch LED
  + a powered-vs-unpowered peripheral diff against `soc-peripheral-baseline-unpowered.txt`).

## vtable mapped + WRITE port confirmed + InitTjHardware replayed (2026-07-15)
**vtable slot mapping (mj_dev, via RTTI):** `r12` in the SetTelephoneEnable handler = a
`CSJHandsetMagicJackDriverMac` (primary vptr `0x10028d388`; a base class is embedded at obj+0xe0 with
secondary vtable `0x10028d5e8`, offset-to-top −0xe0). Enable("1") calls, in order:
`vtbl[0xb8]=func.10000fc70(0)`, `vtbl[0x98]=func.1000148a0(0)`, `vtbl[0xe0]=func.10000f700()` (bool
"is-enabled": reads device obj at **`[handset+0x148]`** and returns `dev->vtbl[0x40]()==0`), then if
not-enabled `base.vtbl[0x90]=func.100187190(1)` → thunks `base.vtbl[0x70]`→`base.vtbl[0x68]` down into
the SJphone handset framework toward the device object. The device object (CTj880Phone_Hid) lives at
`[handset+0x148]`; its methods are the leaf register writers. (Full leaf trace of the SetTelephoneEnable
path is deep through the SJphone abstraction; the concrete device bring-up is captured below instead.)

**★ Device control fully working on hardware (all four ports confirmed):**
| Port | Framing | Status |
|---|---|---|
| SoC memory READ | `80 04 0e 04 <addr32> "prwC"` + GET | ✅ verified (reads ARM boot code) |
| SoC memory WRITE | `80 02 0e 04 <addr32> "prwC"` / `44 <val32>` / `80 12 "prwC"` | recovered (not yet exercised) |
| ARM reg READ | `00 00 <reg> 00` + GET (val=resp[0:4]) | ✅ verified |
| ARM reg WRITE | `20 <reg> 00 01 <val32>` | ✅ **verified by round-trip** (reg0 0→1→0) |
Tool: `tools/tj_armmem.py` (`areg_read`/`areg_write` added).

**Replayed `CTj880Phone_Hid::InitTjHardware()`** on hardware (reg0|=1; reg0x38=3; reg0x14 bit7 strobe
+10 ms): writes LAND and change live state (reg0 0→1, reg0x38 0x83→0x07), device stays healthy. The
sampled line peripherals (0x98100000 GPIO block) did **not** change → InitTjHardware is open-time
codec/clock init, not the line-feed power itself (as expected; `SetTelephoneEnable` does more).
NOTE: these ARM control regs **persist across USB reset** (only a physical unplug/replug clears them);
after the replay the device sits in its normal *initialized* state — harmless.

**Remaining to light the port:** the actual line-feed is a device-object (CTj880) method reached at the
end of the SetTelephoneEnable chain. Two ways to finish: (a) finish tracing the SJphone thunk chain to
the leaf CTj880 method + its exact ARM reg/mem writes; (b) drive the device object's line/codec methods
directly (cross-ref CTjIpDev methods: `EnableAudioPath(bool)`, `ToggleLine`, etc. in symbol-rich
`mjupdate`). Either way the FINAL confirmation is the **physical port LED / dial tone** — a signal only a
human at the device can see, so the powering attempt should be run with the RJ11 handset attached and a
person watching.

## Provenance / handling
DMGs are magicJack's own publicly-served macOS updaters (client `magicJackSetup.dmg` = `upgr980.dmg`
= `upgrade.dmg`, all one 32 MB file; updater `mjisoupdate.dmg`). Downloaded for local RE only, same
as the Windows `upgr811.exe` path — no interaction with magicJack's SIP/registrar servers. Binaries
live in the session scratchpad (`scratchpad/mac/`), not committed.
