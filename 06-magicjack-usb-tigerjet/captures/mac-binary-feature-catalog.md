# magicJack USB — Untapped Feature Catalog & Build Roadmap

<!-- Provenance: derived 2026-07-15 from static reverse-engineering of the macOS mjupdate
     (symbol-rich C++ Mach-O) and mj_dev binaries, via radare2 across an 8-subsystem
     multi-agent disassembly with adversarial frame-verification. Companion to
     captures/mac-binary-ARM-protocol.md. Machine-readable per-feature findings:
     captures/mac-binary-feature-catalog.findings.json
     Unit: magicJack Home Edition, TigerJet 06e6:c200, DevType 8 (ARM). HID frames below are
     DATA-level feature-report bytes (buf[1..]); reuse the verified rd/wr primitives in
     tools/tj_linepower.py / tools/tj_armreg.py rather than hand-rolling the buffer. -->


## 1. Executive summary

We can go **much** deeper. The current Linux tooling (`tj_linepower.py`, `tj_dtmf_decode.py`, `tj_armmem.py`) exercises roughly **5** primitives — line power, ring, hook-read, DTMF-decode, and raw SoC-mem poke. Static RE of the macOS `mjupdate`/`mj_dev` binaries across 8 subsystems newly enumerates **~30 concrete, frame-level features**, of which about **18 are Linux-reachable in normal softphone mode** and roughly **10 are genuinely new, safe, build-ready primitives** (tip/ring polarity reversal, digital audio loopback, DTMF-mute, hardware dial-tone, a full 24-register ARM dump, generic RMW helper, watchdog reboot, flash geometry read, full flash-page dump, and a complete Bellcore Caller-ID FSK generator). The headline: **the ARM register map is now fully transcribed (regs 0x00–0x5c + 0xf0), the flash/bulk-memory port-3 read/write/erase protocol is decoded byte-for-byte, and Caller-ID injection is fully specified as pure USB-audio** — none of which the current tools touch. A large block of named methods (all 6 EEPROM identity calls, gain/echo/record, LCD, buzzer, switch, ring-detect) are **confirmed dead stubs** on the ARM c200 and can be permanently struck off the hunt.

## 2. Feature catalog table

Legend — HID ports: **P1** = normal TJ reg (cmd 0x04), **P2** = ARM control reg (cmd 0x20 write / 0x00 read), **P3** = SoC-mem/flash (cmd 0x80). Frames shown as data bytes buf[1..]; prepend `00` report-id for the 65-byte buffer. "cur" = value from a prior read (always read-modify-write).

### Subsystem: line / analog / line-feed (ARM reg0, reg0x14)

| Feature | Mechanism | HID port + frame | Linux-reachable? | Risk | Have it? | Value |
|---|---|---|---|---|---|---|
| Line power up/down (`InitTjHardware` @0x10002bbf0) | reg0 bit0; reg0x38=3; reg0x14 bit7 strobe | P2: `20 00 00 01 <cur\|1>`; `20 38 00 01 03 00 00 00`; strobe `20 14 00 01 <r14\|0x80>` wait 10ms `20 14 00 01 <r14&~0x80>` | yes-normal | reversible | ✅ tj_linepower | high (source of truth) |
| **Tip/Ring polarity reversal** (`SetTipRingPolarity` @0x10002bfa0) | reg0 **bit16** (0x10000) | P2 RMW: on `20 00 00 01 <cur\|0x10000>`; off `20 00 00 01 <cur&0xFFFEFFFF>` | yes-normal | reversible | ✅ **VERIFIED 7-15** | **high** — battery reversal; **confirmed** reversible + reg0x28 line-sense tracks it |
| Ring on/off (`Ring` @0x10002bf10) | reg0 bits8-9 (0x300) | P2: on `20 00 00 01 <(cur&0xFFFFFCFF)\|0x300>`; off `20 00 00 01 <cur&0xFFFFFCFF>` | yes-normal | reversible | ✅ tj_linepower | medium |
| Live hook / loop-closure sense (`GetHookState` @0x10002c0b0; cache written by InitTjHardware @0x10002bc61) | reg0x14 **bit31** (read) | P2 read: `00 00 14 00` → GET; hook=(resp[0:4]LE>>31)&1 | yes-normal | safe-read | ✅ tj_linepower | medium (note: `this+0x58` cache is stale; re-read live) |

### Subsystem: tone / ring / DTMF-mute (ARM reg0x14)

| Feature | Mechanism | HID port + frame | Linux-reachable? | Risk | Have it? | Value |
|---|---|---|---|---|---|---|
| **Hardware dial tone** (`PlayTone(0,on)` @0x10002be90) | reg0x14 **bit9** (0x200); off clears 0x700 | P2 RMW: on `20 14 00 01 <cur\|0x200>`; off `20 14 00 01 <cur&0xFFFFF8FF>` | yes-normal | reversible | ◑ **PARTIAL 7-16** | med — bit9 **boosts** the 350+440Hz dial tone ~2x, but the firmware presents a baseline off-hook dial tone regardless, so bit9 is a level/enable modulator, not a clean on/off |
| **DTMF-mute / DTMF-remover** (`EnableDTMFMute` @0x10002bff0; custom `SetDTMFRemoverEnable`) | reg0x14 **bit4** (0x10) | P2 RMW: on `20 14 00 01 <cur\|0x10>`; off `20 14 00 01 <cur&0xFFFFFFEF>` | yes-normal | reversible | ✅ **VERIFIED 7-15** | high — **confirmed**: set strips DTMF from card1 audio, clear passes it (modem-driven test) |
| Stutter dial tone (`SetToneType` case 3 @0x100034c41) | reg0x14 bit9 pulsed ~150ms by host | P2: same as dial-tone frame, host-toggled | yes-normal | reversible | ❌ | medium (cosmetic, software cadence) |
| Ring cadence / on-ring CID trigger | reg0 0x300 host-timed; no HW cadence reg | P2: loop ring on/off on a schedule | yes-normal | reversible | ❌ | high (custom ring cadence) |

### Subsystem: audio-dsp (loopback, DTMF-mute, gain/echo/record)

| Feature | Mechanism | HID port + frame | Linux-reachable? | Risk | Have it? | Value |
|---|---|---|---|---|---|---|
| **Digital audio loopback** (`SetupDigitalLoopback` @0x100002130 → legacy reg0x4e→ARM reg0x40) | reg0x40 **bit4** (0x10) | P2 RMW: on `20 40 00 01 <cur\|0x10>`; off `20 40 00 01 <cur&0xFFFFFFEF>` | yes-normal | reversible | ✅ **VERIFIED 7-15** | **high** — **confirmed**: on=1kHz tone loops playback→capture, off=silent; new register |
| SetGainControl / EchoCanceller / StartRecord / EnableAudioPath | all base-class **stubs** (xor eax,eax;ret @0x10002c800/7e0/7f0/940/8e0/8f0) | none | no | — | (host audio) | low — do in host mixer / Asterisk / arecord |

### Subsystem: ARM register map (transcribed) — all P2

| Feature | Mechanism | HID port + frame | Linux-reachable? | Risk | Have it? | Value |
|---|---|---|---|---|---|---|
| **PORT-2 encoder ABI** (`_HidWriteTjRegs_ARM` @0x100026460 / `_HidReadTjRegs_ARM` @0x100026590) | write reg@buf[2], read reg@**buf[3]** | WRITE `20 RR 00 01 <v LE>`; READ `00 00 RR 00` (RR at index 3!) | yes-normal | safe | ✅ (partial) | high — **critical asymmetry**: read reg byte is at offset 3, not 2 |
| reg0x38 codec/master-clock (=3) | whole-reg write | P2: `20 38 00 01 03 00 00 00`; read `00 00 38 00` | yes-normal | reversible | ❌ | medium (USB-audio prereq; firmware sets it) |
| **regs 0x58 / 0x5c** — legacy 0x32/0x33 AFE (DevType 7/8) | 6-bit fields [5:0] | P2 read `00 00 58 00` / `00 00 5c 00`; write RMW mask 0x3f | yes-normal | reversible | ❌ | medium (SLIC gain/DTMF tuning, our chip's home) |
| composite regs 0x04/08/0c/10/18/1c — legacy 8-bit backing (22-row table @0x100064950) | packed bitfields; 0x5a bit0x20 invert quirk | P2 read `00 00 RR 00`; field RMW | yes-normal | unknown (recoverable by re-init) | ❌ | low-med (codec coeffs; need Si321x datasheet before writing) |
| reg0x34 GPIO/status bits0,1 | via legacy 0x3d path | P2 read `00 00 34 00` | yes-normal | unknown | ❌ | low-med (read-only; meaning undecided) |
| read-only telemetry regs 0x20/24/28/2c/30/3c/44/48/4c/50/54 | pure reads | P2 read `00 00 RR 00` | yes-normal | safe-read | ❌ | medium (diff across hook/ring/audio states) |
| **Full register dump** (`DumpRegistersARM` @0x100035be0) | 24 reads: 0x00–0x54 + 0x58/0x5c | P2: read each `00 00 RR 00` | yes-normal | safe-read | ❌ **NEW** | **high** — single most useful diagnostic; snapshot/restore ground truth |
| **Generic RMW helper** (`WriteArmRegisterBits` @0x100003110) | v=(old&keepmask)\|(newbits&~keepmask) | P2 read+write template | yes-normal | reversible | ✅ pattern | high — one helper covers polarity/tone/mute/loopback |
| reg0xf0 WiFi mailbox (`IsWiFiModulePresent` @0x100036e14) | cmd low byte, bit8=done, result>>16 | P2: `20 f0 00 01 07 00 00 00` then poll `00 00 f0 00` | needs-hw-test | reversible | ❌ | low (no WiFi on c200; cmd7 confirms absent) |

### Subsystem: Caller-ID FSK (pure USB-audio, ALSA card 1 — no HID)

| Feature | Mechanism | HID port + frame | Linux-reachable? | Risk | Have it? | Value |
|---|---|---|---|---|---|---|
| **Bellcore on-hook Type-1 CID modulator** (`build_callid_wav`/genmsg @0x100021000, orchestrator @0x100025150) | Bell-202 FSK: mark 1200 Hz / space 2200 Hz, 1200 baud, 8 kHz, ±8192; 0.5s silence + seizure(0x55) + mark + MDMF | none — S16_LE/8kHz/mono PCM to **hw:1** | yes-normal (host DSP) | safe | ❌ **NEW** | **high** — the whole CID-spoof capability, fully specified |
| MDMF message builder (@0x1000220d0) | `01 08 MMDDHHMM` \| `02 <n> num` \| `07 <m> name`; 0x80 type + checksum added by modulator (@0x10002160e) | none | yes-normal | safe | ❌ | high (exact on-wire layout; verify checksum polarity vs real capture) |
| CID delivery = audio playback (`PlayCallerIDData` Mac override @0x10003bb00 → `PlayMemoryBuffer`) | ALSA card1 write during on-hook gap; ProSLIC in OHT | none / ALSA | needs-hw-test (latch depends on ProSLIC state) | reversible (audio) | reuse card1 | high — no CID opcode needed |
| Ring+CID choreography (`SetRinging` @0x10003b580) | ring→~2s (SetTimer 0x7d0)→FSK | P2 ring frames + ALSA | yes-normal | reversible | ✅ ring | high (exact timing recipe) |
| "Private Call" / privacy caps (`GetNameAndNumberForCallerID` @0x100037290) | flag [obj+0x118]; MDMF reason 'P'/'O'; 16-char field caps | none (host state) | yes-normal | safe | ❌ | medium (--private flag) |
| Off-hook/CAS+SAS CID (type=1, CAS 2130+2750 Hz, SAS 440 Hz) | same PlayMemoryBuffer | none / ALSA | yes (audio) | safe | ❌ | low (niche call-waiting) |
| ETSI/V.23 CID (space 2100 / mark 1300 Hz, type 2/3) | swap tone pair | none / ALSA | yes (audio) | safe | ❌ | low (non-US handsets) |

### Subsystem: flash / bulk-memory / updater (P3, cmd 0x80, magic `prwC`=70 72 77 43)

| Feature | Mechanism | HID port + frame | Linux-reachable? | Risk | Have it? | Value |
|---|---|---|---|---|---|---|
| **Read flash geometry / "scsi info"** (`_UpdateScsiInfoARM_HID` @0x100026ac0) | subreg 0x10 op 0x04/0x14 | P3: `80 04 10 04 00 00 00 00 prwC` → GET → finalize `80 14 00*6 prwC` | ✅ **yes-normal-mode** | **safe-read** | ✅ **VERIFIED 7-16** | **high** — **ANSWERS in normal mode** (descriptor `01 00 20 17 88 …`, real data, no stall) → flash READ surface reachable WITHOUT updater mode; full-dump path likely feasible |
| **Read one physical flash page (2048 B)** (`_ReadOnePhyPageBulkMemARM` @0x100027b70) | subreg 0x13 page-ptr + 0x08 xdata, op 0x04/0x14; 32× GET | P3: `80 04 13 04 PP PP PP PP prwC` / `80 14 00*6 prwC` / `80 04 08 04 00 00 00 00 prwC` / 32× GET / `80 14 00*6 prwC` | ✅ **yes-normal-mode** | safe-read | ✅ **VERIFIED 7-16** | **high** — **full 8MB flash DUMPED in normal mode** @~163KB/s (config + ZeroCD ISO + eCos "ArmJack" ARM firmware + MJSF blob); see flash-dump-analysis.md |
| Read hash/serial/flash-id (`_GetHashDataStringFromFlash` @0x100028240) | TJ regs 0x61/0x63 (P1 via ARM read), flash_id 0xbf41 SST, then page read | P1 read (exact ARM sub-frame **not** byte-verified) + P3 page read | needs-hw-test | safe-read | ❌ | medium (identity/health probe) |
| **Reboot ARM SoC / watchdog** (`_RebootTj880` @0x100028410 via `_Vitya_HAL_WRITE_UINT32`) | 5× SoC writes: (0x9870000c,0)(0x98700004,0x7a120)(0x98700008,0x5ab9)(0x9870000c,0x1b)(0x98100020,0x400) | P3 subreg 0x0e, per write: `80 02 0e 04 AA AA AA AA prwC` / `44 VV VV VV VV` / `80 12 00*6 prwC` | **yes-normal** (same port tj_armmem uses) | reversible (returns to normal fw) | ❌ **NEW** | medium — clean forced re-enumeration/recovery |
| Write one flash page (`_WriteOnePhyPageBulkMemARM` @0x100027e50) | subreg 0x08 xdata + 0x12 page-program; data-push `0x40\|len` | P3 multi-stage (see finding) | needs-hw-test | **brick-risk** | ❌ | high value / high danger — refuse page 0, read-back verify |
| Erase SPI sector 4 KB (`_EraseSpiSector_HID` @0x1000277b0) | subreg 0x1c op 0x02 + suffix `78 62 01`; **no host poll on ARM** | P3: `80 02 1c 04 SS SS 00 00 prwC 78 62 01` / `80 12 00*6 prwC` | needs-hw-test | brick-risk | ❌ | medium (safest erase granularity) |
| Erase 64 KB block (`_EraseBlock_HID` @0x100027130) | subreg 0x11 + `78 62 01` | P3: `80 02 11 04 BB BB 00 00 prwC 78 62 01` / finalize | needs-hw-test | brick-risk | ❌ | medium (prefer sector erase) |
| GetDeviceMacAddress / Get/SetPrivateData / Get/SetCDData / MJSF transport | CPrivateArea page R/W over P3 0x13 | P3 flash multi-frame | mode-gated-updater | safe-read (get) / **brick-risk** (set) | ❌ | medium (provisioning store; read-only first) |
| **MJSF container codec** (`MJSFHelpers::Write` @0x100012920) | `'MJSF'`+size+**MD5(data)**+MD5(header); MD5-only, **no HMAC** — forgeable | none (host math) | yes-normal (codec) | safe (codec) | ❌ | high (intel) — provisioning is unauthenticated |

### Subsystem: custom-command dispatch (host-side, not a HID frame)

| Feature | Mechanism | HID port + frame | Linux-reachable? | Risk | Have it? | Value |
|---|---|---|---|---|---|---|
| `-cc` dispatch / `ExecuteCustomCommand` (@0x100032940 / @0x100035ff0), 22 handlers | pure host C++ strcmp table @0x100074dd0 | **none** — no "send-a-command" frame; port handlers individually | no (as a frame) | safe | ❌ | high (the 22 handlers ARE the map; not a device channel) |
| SetRegisters / GetRegisters (curated) | legacy-index → ARM reg (table dyld-bound, garbage statically) | P2 raw reg R/W (skip legacy abstraction) | yes-normal | reversible / safe | ❌ | medium (raw ARM regs already cover it) |
| SetTelephoneEnable custom | enable = line-power path; **"0" disable is sw-only** ([r12+0x1c9]=0) | P2 (via line power) | yes-normal | reversible | ✅ tj_linepower | medium (see §4) |

**Footnote — confirmed dead stubs (do not build):** All 6 EEPROM identity methods (`ReadEEProm`/`WriteEEProm`/`EnableEEPromWrite`/`GenEEPromCk`/`Read`/`WriteEEPROMSerialNumber`, contiguous stub block 0x10002c990–0x10002c9e0) — **no EEPROM channel exists on the c200; no serial-spoof frame is derivable.** Also stubs: `SetGainControl`, `SetEchoCanceller`/`ResetEchoCanceller`, `StartRecord`/`StopRecord`, `EnableAudioPath`, `PlayBusyToneSample` (@0x10003b570), `DetectRing`/`IsLineRinging`/`IsConnectedToLine`/`IsPhonelineConnected`, `InitBuzzer`/`TurnBuzzerOn`/`SetBuzzerFreq`, `KeyScan`/`HasKeypadScanning` (no VIRTUAL_KEY string in any binary), all LCD methods (`InitLCD`/`WriteLCDCommand`/`CloseLCD`/`DisplayLCDBuzzer`), config-switch methods (`TurnSwitchOnOff`/`SwitchSetting`/`ReadSwitchSetting`), `HasVBAT`/`ReadVBat` (real Tj880 overrides hard-returning 0), `Combo_PPG_Session`. The `reg0x57` tone-RAM and `reg0x34/0x3d` (Tj560B/Tj780) tone paths are **wrong-chip** — inert on the c200 (ProSLIC direct writes are no-ops on ARM). Device serial is read via **USB iSerialNumber** (`/sys/bus/usb/devices/1-1.3/serial`), not HID — and it is *not* the E-number SIP identity, which is network-provisioned.

## 3. Prioritized build roadmap

Ranked by value × feasibility / risk.

### SAFE — read-only or reversible, do now

**1. `tj_dumpregs.py` — full ARM register snapshot (BUILD FIRST).**
- What: read all 24 ARM regs (0x00–0x54 step 4, plus 0x58/0x5c) as the ground-truth diagnostic and snapshot/restore basis for every write experiment.
- Steps: for `RR` in {0x00,0x04,…,0x54,0x58,0x5c}: write `00 00 00 RR 00` (P2 read, reg at **index 3**), HIDIOCGFEATURE, value=resp[1..4] LE. Diff across on-hook / off-hook / ring / audio states to reverse the unknown telemetry and reg0x34 GPIO bits.
- Risk: none. Evidence: `DumpRegistersARM` @0x100035be0.

**2. `tj_flashinfo.py` — flash geometry probe (safest flash-port test).**
- What: the lowest-risk way to learn whether the P3 `0x80` flash port answers in normal mode, and it returns the geometry every flash tool needs (flash_id, CD image location, partition layout).
- Frames (P3): `80 04 10 04 00 00 00 00 70 72 77 43` → GET (descriptor) → finalize `80 14 00 00 00 00 00 00 00 70 72 77 43`.
- Risk: safe-read. A valid `flash_id` (expect SST 0xbf41) in normal mode strongly implies the whole flash port is reachable without a mode switch. Evidence: `_UpdateScsiInfoARM_HID` @0x100026ac0.

**3. `tj_armreg.py` — generic P2 read/write/RMW helper (foundation lib).**
- What: `read_u32(reg)` / `write_u32(reg,val)` / `rmw(reg,newbits,keepmask)` implementing `v=(old&keepmask)|(newbits&~keepmask)`. One helper backs polarity, tone, DTMF-mute, loopback, and all field pokes.
- Frames: WRITE `20 RR 00 01 <v LE>` (reg at index **2**); READ `00 00 RR 00` (reg at index **3** — the load-bearing asymmetry). Evidence: `_HidWriteTjRegs_ARM` @0x100026460, `_HidReadTjRegs_ARM` @0x100026590, `WriteArmRegisterBits` @0x100003110.

**4. `tj_polarity.py` — tip/ring (battery) reversal.**
- What: genuinely new, safe, reversible line-control primitive; enables polarity-reversal supervision experiments no current tool exposes.
- Frames (P2 RMW on reg0): read `00 00 00 00`; on `20 00 00 01 <cur|0x00010000>`; off `20 00 00 01 <cur&0xFFFEFFFF>`. Must RMW to preserve line-power bit0. Evidence: `SetTipRingPolarity` @0x10002bfa0.

**5. `tj_callerid.py` — Bellcore Type-1 Caller-ID injection (highest new user-facing value). ✅ BUILT + HARDWARE-VERIFIED 2026-07-15** — decoded flawlessly on a modem (`AT#CID=1`): DATE/TIME/NMBR/NAME all correct. The RE spec (checksum polarity + on-hook latch timing) was right end-to-end.**
- What: generate MDMF (`01 08 MMDDHHMM` | `02 <n> number` | `07 <m> name`), Bell-202 modulate (mark 1200 Hz / space 2200 Hz, 1200 baud, 8 kHz, ±8192), prepend 0.5s silence + 0x55 seizure + mark preamble, play S16_LE/8kHz/mono to ALSA hw:1. Fold in the existing ring: ring burst → ~2s → FSK → resume cadence. Add `--private` (MDMF 'P'/'O' reason code). No HID opcode needed.
- Steps/risk: pure DSP + reuse of proven card-1 playback; only device action is the already-safe ring frame. Bench-verify the on-hook phone latches FSK in the OHT gap, and validate checksum polarity (Bellcore two's-complement) against one real hardware CID capture. Evidence: `build_callid_wav` @0x100021000/0x100025150, `SetRinging` @0x10003b580 (SetTimer 0x7d0), `PlayCallerIDData` @0x10003bb00.

**6. `tj_dtmfmute.py` — DTMF-remover toggle (high experimental value).**
- What: reg0x14 bit4 RMW. If the ARM firmware's DTMF remover swallows in-band digits, clearing this bit may make DTMF pass through the ATA audio path — directly relevant to the softmodem/BBS and DTMF-signaling work.
- Frames (P2 RMW reg0x14): read `00 00 14 00`; on `20 14 00 01 <cur|0x10>`; off `20 14 00 01 <cur&0xFFFFFFEF>`. Evidence: `EnableDTMFMute` @0x10002bff0.

**7. `tj_loopback.py` — digital audio self-test.**
- What: reg0x40 bit4 loopback (codec TX→RX); play a tone to card1 playback, confirm it returns on card1 capture — proves the full USB-audio path with no phone or call. Adds a **new register (0x40)** to the master map.
- Frames (P2 RMW reg0x40): read `00 00 40 00`; on `20 40 00 01 <cur|0x10>`; off `20 40 00 01 <cur&0xFFFFFFEF>`. (The updater's extra P1 ProSLIC reg0x4e=0x80 write is a no-op on ARM; ignore it.) Evidence: `SetupDigitalLoopback` @0x100002130, table row @0x100064980.

**8. `tj_reboot.py` — SoC watchdog reset.**
- What: clean forced re-enumeration to recover a wedged device without unplugging; returns to normal firmware (not a bootloader).
- Frames (P3 subreg 0x0e, 5 writes): for each (addr,val) in {(0x9870000c,0),(0x98700004,0x7a120),(0x98700008,0x5ab9),(0x9870000c,0x1b),(0x98100020,0x400)}: `80 02 0e 04 <addr LE> 70 72 77 43` / `44 <val LE>` / `80 12 00*6 70 72 77 43`. Runs on the exact port `tj_armmem.py` already drives — trivial. Evidence: `_RebootTj880` @0x100028410, `_Vitya_HAL_WRITE_UINT32` @0x100026000.

Also cheap and safe when convenient: `tj_dialtone.py` (reg0x14 bit9, bench-verify audibility) and a pure-Python `mjsf.py` pack/unpack (`'MJSF'`+size+md5(data)+md5(header)+data) for offline provisioning-blob inspection.

### RISKY — brick-capable, gate behind recovery tooling

- **`tj_flashdump.py`** (full firmware/flash dump via `_ReadOnePhyPageBulkMemARM` @0x100027b70). *Read-only* and high-value, but validate on a **single** page and diff two reads for stability first; it depends on the flash port answering in normal mode (test with #2 above). This is the backup you take before ANY write.
- **Flash writer / erase** (`_WriteOnePhyPageBulkMemARM` @0x100027e50, `_EraseSpiSector_HID` @0x1000277b0, `_EraseBlock_HID` @0x100027130). Do **not** build a bare writer. Require: a verified known-good full image, refuse page 0 (mirror the "Zerro page is write-protected" guard @0x10004d960 + sector-alignment guard @0x10004d9b7), erase-sector-then-program, read-back-compare every page, ARM firmware self-waits (no host poll on our chip), and an external SPI recovery plan. Brick risk is real.
- **Private-area / CD / MAC / provisioning writes** (`Custom_SetPrivateData` @0x100031490, `WriteCD` @0x100039a20). Read-only exploration first (dump offline, unwrap MJSF). Writes are flash + mode-gated + brick-risk — never against the live c200 without recovery.

## 4. The line-feed holy grail

**Resolved — with a polarity-inverted correction to the original finding, and a decisive negative on `SetTelephoneEnable`.**

`SetTelephoneEnable` (`Custom_SetTelephoneEnable` @0x100032230) is **NOT** the line-feed power switch. Disassembly shows its enable **leaf is a stub**: the "1" path (je 0x1000322a9) only sets the software flag `byte[r12+0x1c9]=0` and returns with **no** vtable/hardware calls; it is the **"0" path** (je 0x1000322b6) that runs the vtbl sequence (driver slots [0xb8],[0x98],[0xe0], then inner-dev [r12+0xf0] vtbl[0x90](1)) and sets `[r12+0x1c9]=1`. Crucially, vtable slot +0x90 resolves to `SetEchoCanceller` @0x10002c7e0 = a confirmed **stub**; the only real hardware side-effect anywhere in the method is a nested stop-ring (reg0 bits8-9 cleared). So "disable" is a pure software no-op and "enable" does no line-power write. (The original finding had the branch polarity inverted and the flag at +0x1b1 instead of +0x1c9 — corrected here.)

**The real line-feed power register was pinned down elsewhere:** `CTj880Phone_Hid::InitTjHardware` @0x10002bbf0 is the single source of truth. Line-feed power = **ARM reg0 bit0** (`or esi,1` @0x10002bc24 → `tjoutp_ARM(reg0)`), gated by codec/clock **reg0x38=3** and an **activate strobe on reg0x14 bit7** (set @0x10002bc68, `MySleep(10ms)` @0x10002bc81, cleared @0x10002bc90). This exactly matches what `tj_linepower.py` already does — the vendor path is confirmed identical.

**Exact next experiment (the one open lever):** the from-cold init has a **chip-specific reg0x14 low-nibble** the project previously lacked. For our DevType-8 (980-class) chip the final strobe-clear write is `(reg0x14 & 0xFFFFFF70) | 4` (@0x10002c22b-33), **not** the 880's `& 0xFFFFFF7F`. Build `tj_init.py` replaying the 5 steps with the DevType-8 mask: (1) `20 00 00 01 <reg0|1>`; (2) `20 38 00 01 03 00 00 00`; (3) read `00 00 14 00`, save hook=bit31; (4) `20 14 00 01 <reg14|0x80>`, wait 10ms; (5) `20 14 00 01 <(reg14&0xFFFFFF70)|4>`. Then read reg0x14 back after the on-board firmware's own init and confirm your mirrored value matches — that verifies whether the reg0x14 bit2-set / bits4-6-kept low nibble is the missing piece that makes the FXS line fully "light" and carry audio identically to the vendor bring-up.

## 5. Open questions / dead-ends

- **Updater-mode entry is still unmapped.** Every flash write/erase/private-data path is labeled "mode-gated-updater," but the *entry* transition (how the device leaves softphone mode into an idle/updater state where subreg 0x13/0x11/0x12/0x1c stop stalling) was never traced. The encouraging lead: the read primitives (`_ReadOnePhyPageBulkMemARM`, geometry read) also exist in `mj_dev`, the **normal-mode** softphone driver — so the flash port *may* answer read frames in normal mode with exclusive hidraw ownership. **UPDATE 2026-07-16 — the geometry read (subreg 0x10) DOES answer in normal softphone mode** (descriptor `01 00 20 17 88 00 00 00 00 04 00 00 …`, no stall). So at least the flash *read* command surface is reachable without a mode switch. Next: try the actual page read (subreg 0x13, `_ReadOnePhyPageBulkMemARM`) on a single page and diff two reads for stability — if it answers too, a full firmware dump is doable from normal mode. WRITE/erase (0x12/0x1c/0x11) may still be updater-gated; do not attempt without recovery tooling.
- **Whether the on-chip audible tones actually sound.** The dial-tone (reg0x14 bit9) and DTMF-mute (bit4) *register writes* are frame-confirmed, but the earpiece/handset audible effect is bench-unverified — may require codec/clock reg0x38=3 routing to be active. Needs an off-hook handset on the bench.
- **reg0x34 GPIO bits 0/1 semantics** and the packed legacy coeff regs (0x04/08/0c/10/18/1c, regs 0x58/0x5c AFE) are transcribed structurally but their *physical meaning* is not decidable from the binary — requires diffing reads across line states (do via `tj_dumpregs.py`) and, for writes, the Tiger560/Si321x datasheet.
- **CID checksum polarity** (Bellcore two's-complement) and the on-hook FSK-latch timing window must be validated against one real hardware capture before shipping `tj_callerid.py`.
- **Confirmed dead, stop chasing:** on-chip EEPROM identity read/write (all 6 methods stubbed — no serial-spoof frame exists); VBAT/line-voltage sense (Tj880 overrides hard-return 0; a ProSLIC-reg-0x52 SoC mirror is speculative PORT-3 research only); ring *detection* (FXS generates ring, doesn't detect it); buzzer/LCD/config-switch (no such hardware). The E-number SIP identity is **network-provisioned**, not on-chip — keep identity work in the self-hosted registrar (03-magicjack-sip), not in a chip read/spoof.