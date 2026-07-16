# magicJack — USB / TigerJet reverse engineering

Reverse-engineering the **USB personality** of the magicJack: the original goal was to drive the
device's analog phone port directly from Linux (as an FXS-over-USB adapter into Asterisk),
bypassing magicJack's software entirely. That goal is **done**. Since then the RE has gone much
deeper — a full feature catalog from the vendor binary, a complete 8 MB flash dump, the SIP config
crypto, a live firmware unpack, and a full map of the USB-vs-ATA "mode gate." This is the **USB
door** — distinct from, and complementary to, the SIP/Ethernet work in
[`../03-magicjack-sip/`](../03-magicjack-sip/).

> **★★ COMPLETE FXS-over-USB from Linux (2026-07-15) — every function hardware-verified:** line power
> on/off (dial tone + LED), hook detection (reg0x14 bit31), **two-way audio** (ALSA card 1 — captured the
> live line, and a host-sent 1 kHz tone heard clearly in the handset), **DTMF** (keypad "9 8 7 6 5 3 #"
> decoded exactly, `tools/tj_dtmf_decode.py`), and **ring** (reg0|=0x300 — the handset physically rings,
> `tj_linepower.py ring`). Wired into Asterisk as ext 200 (baresip + `mj-fxs-bridge.py`). See §7.
>
> **★★ DEEPER RE (2026-07-15 → 07-16), all summarized below and detailed in `captures/`:**
> a **~30-feature catalog** from the symbol-rich macOS binary with **four new hardware-verified
> primitives** — Caller-ID injection (`tools/tj_callerid.py`), DTMF remover, digital loopback, tip/ring
> polarity (§6); a **full 8 MB flash dump from Linux in normal mode** (§7); the **SIP config crypto**
> reverse-engineered — local `Profiles.db` decrypted, but the account stays per-session-keyed (§8); the
> **firmware unpacked via a live RAM dump** with its two RC4 keys recovered (§9); and a full map of the
> **USB-vs-ATA mode gate** and why the Ethernet/SIP brain is dormant over USB — next step is a physical
> cut-cable (§10).

---

## 1. The hardware

- **Device:** magicJack **HOME (Home Edition)** — user-verified 2026-07-15 (black box, blue accent
  stripe, RJ45 + RJ11 front jacks + USB). Earlier "Plus / model K1103 / FCC Y79K1103" was a **misID**;
  the HOME's firmware and ZeroCD still self-report as "magicJackPlus", same YMAX/TigerJet platform.
- **USB controller:** **TigerJet Network `06e6:c200`** — "USB Internet Phone by TigerJet",
  family **TJ780/TJ880** (firmware banner `Version 19.20 (magicJack 560/580/780/880/980/911)`,
  `TJ780 SPI flash`, board `IPC11`). USB 1.1 full-speed, bus-powered. **ARM-based SoC** (this is the key
  fact — the ARM firmware, not the host, owns the analog line; see §5).
- **Analog line chip (FXS):** **Silicon Labs Si321x ProSLIC** (string `Proslic` in firmware;
  ProSLIC indirect-register names `RING_OSC`, `RING_TRIP_*`, `CM_BIAS_RINGING`, `DTMF_*` in the
  driver). On ARM chips the SPI bridge to the ProSLIC is a host no-op — the firmware drives it.
- **USB hardware serial:** `<device-serial>` (this is *not* the SIP account id — see §4/§8).
- **Ethernet/ATA SoC (the other brain):** MAC = **Faraday FTMAC110 @`0x91000000`**, PHY reset on
  GPIO `0x98100318`, USB/PMU controller @`0x90600000` (the mode-gate block — §10).

### Two brains, one box
The device has a **networked ATA brain** (SoC + Ethernet, runs SIP to magicJack's cloud, owns the
phone number — only active on **wall power**) and the **TigerJet USB front-end** (this project —
active on a **PC USB port**; the Ethernet/ATA brain stays dormant). Which brain wakes is decided once
at boot by USB-host-enumeration detection — the "mode gate", now fully mapped (§10). See
[`fingerprint.md`](fingerprint.md).

---

## 2. USB interface map (what enumerates)

| Interface | Class | Linux node | Role |
|---|---|---|---|
| 0 | Mass Storage | `sr0` | ZeroCD autorun installer (`YMaxCorp magicJackPlus CD`) |
| 1 | Audio Control | card 1 mixer | mute/volume |
| 2 | Audio Streaming (capture) | ALSA **card 1** | line→host, **8 kHz mono S16LE** |
| 3 | Audio Streaming (playback) | ALSA **card 1** | host→line, 8/16 kHz mono S16LE |
| 4 | HID (Telephony) | **`hidraw1`** | hook/DTMF input + 64-byte vendor control report |

Full descriptors in [`captures/usb-descriptors.txt`](captures/usb-descriptors.txt);
decoded HID report descriptor in [`captures/hid-report-descriptor.hex`](captures/hid-report-descriptor.hex).

---

## 3. The Windows softphone (reverse-engineered)

The CD is a stub that downloads `http://upgrades.magicjack.com/upgrade/upgr811.exe` (a nested
NSIS installer). Unpacked, it is **SJ Labs "SJphone" SIP stack + TigerJet driver + Si321x ProSLIC**:

| Binary | Role |
|---|---|
| `magicJack.exe` (Qt4) | main app |
| `SJHandsetMagicJack.dll` | SJphone handset plugin (`Handset_Initialize/SetProp/Uninitialize`) |
| `TjIpSys.dll` | TigerJet chip driver (`TjIpSysCall`) — talks HID feature reports |
| `TjVista.dll` | TigerJet OS access layer |
| `magicJack.dll` | device discovery / RWD (account) read / CD eject |
| `AECSolicall.dll` | SoliCall echo cancellation |

The **macOS** build is more valuable: the firmware-updater's `mjupdate` Mach-O is **symbol-rich C++**
(full class tree `CTjIpDev`/`CTj780/880Phone_Hid`, named methods) — the Rosetta Stone for the ARM
protocol (§5) and the deep feature catalog (§6). The macOS softphone (`magicJack`/`mj_dev`) is stripped
but keeps `CSJHandsetMagicJackDriverBase::` log strings.

---

## 4. The SIP door (fully mapped — feeds `03-magicjack-sip`)

- Softphone = **SJphone** (brand "talk4free"). SIP identity = a **device serial from firmware ("RWD")**,
  used as `sip:<serial>@<UserDomain>`, **empty auth** unless challenged — matches the captured
  `<E-number>` / no-auth registration in `03-magicjack-sip`.
- ⚠️ The `E…`-number is **network-provisioned**, **not** the USB serial `<device-serial>`, and **not
  stored on-chip in the clear** — confirmed from the flash dump (§7) and crypto RE (§8).
- Provisioning = proprietary **MJSF** (signed) + the **SJEN/RC4/zlib** config cipher (§8) — no cleartext
  REST.
- **Self-host recipe:** the practical route is the **DNAT redirect** of `216.234.65.40:5070` → your own
  Asterisk (per `../03-magicjack-sip/`), because *forging* provisioning is hard (per-session RC4 keys +
  a per-device firmware master key — §8).

This is the **higher-value door** (keeps the magicJack number). See [`protocol.md`](protocol.md).

---

## 5. USB chip-control protocol (SOLVED)

All chip control is **HID feature reports** on `hidraw1` (`SET_REPORT`/`GET_REPORT`),
**report ID 0**, 64-byte data. The TJ780/880 is **ARM-based**, and its firmware space uses different
HID framing than plain TigerJet register writes (which is why blind SPORT fuzzing "stalled"):

- **Normal TigerJet register R/W** (**P1**, cmd `0x04`): READ = page-select `04 <bank> <bank> 00` + GET,
  value at offset `reg&0x1F`; WRITE = `04 <reg> <reg&0xE0> 01 <val>`.
- **ARM control-register R/W** (**P2**): WRITE `20 <reg> 00 01 <val32 LE>` • READ `00 00 <reg> 00` + GET
  (`resp[0:4]`). ARM ctrl regs **persist across USB reset** — only a physical power-cycle clears them.
- **ARM SoC memory R/W** (**P3**, cmd `0x80` + ASCII magic `"prwC"`): sub-reg `0x0e` = raw CPU-memory
  port (READ `80 04 0e 04 <addr32 LE> prwC` + GET; WRITE `80 02 0e 04 <addr LE> prwC` / `44 <val>` /
  `80 12 .. prwC`); sub-reg `0x13` = flash **page** read (§7). Hardware-confirmed: addr 0 = `0xea000012`
  (ARM reset vector); `0x00–0x01000000` = SDRAM/ROM; `0x98xxxxxx` = SoC peripherals. ⚠️ Reading an
  **unmapped** addr stalls+latches the endpoint → recover with `USBDEVFS_RESET` (the tools do it).

**The line-power sequence** (replay `CTj880::InitTjHardware` over P2): `reg0 |= 1` (bit0 = line enable),
`reg0x38 = 3`, `reg0x14 bit7` strobe (+10 ms). Ring = `reg0 |= 0x300`; hook = `reg0x14 bit31`;
DTMF = `reg0x14 byte0x16`=valid / `byte0x17`=nibble.

Full protocol + memory map + vtable trace:
[`captures/mac-binary-ARM-protocol.md`](captures/mac-binary-ARM-protocol.md).

---

## 6. Deep feature enumeration & new verified primitives (2026-07-15/16)

A multi-agent radare2 disassembly of the symbol-rich `mjupdate` (3426 syms, 8 subsystems) turned the
~5 primitives the FXS station uses into a **~30-feature frame-level catalog** (~18 Linux-reachable in
normal mode). Full write-up + machine-readable findings:
[`captures/mac-binary-feature-catalog.md`](captures/mac-binary-feature-catalog.md)
(+ `.findings.json`). Highlights:

**Hardware-VERIFIED new primitives:**
- **Caller-ID injection** — `tools/tj_callerid.py` makes the attached phone display any name+number.
  Pure USB-audio (NO HID opcode): builds a Bellcore Type-1 MDMF packet and Bell-202 FSK-modulates it
  (mark 1200 Hz / space 2200 Hz, 1200 baud, 8 kHz, ±8192), played to ALSA card 1 in the on-hook gap
  after a ring burst. **Decoded flawlessly on a modem** (`AT#CID=1`: DATE/TIME/NMBR/NAME all correct) —
  which proved the 2nd-wave RE spec correct end-to-end.
- **DTMF remover** — `reg0x14 bit4`. **Confirmed**: set = strips DTMF from the line→host audio path,
  clear (default) = passes it. Toggle via `tj_armreg.py rmw 0x14 0x10 0` / `rmw 0x14 0 0x10`.
- **Digital audio loopback** — new register `reg0x40 bit4`. **Confirmed**: host playback→codec→host
  capture internally (loopback off = card-1 capture silent; on = the 1 kHz tone returns at full level).
- **Tip/ring (battery) polarity reversal** — `reg0 bit16`. **Confirmed** reversible; and `reg0x28[31:16]`
  tracks it, confirming **reg0x28 is a genuine analog line/loop-current measurement** (not a live hook
  readout — a single-shot read over-implied that; the time-series latches around hook events).

**PARTIAL:** hardware **dial tone** `reg0x14 bit9` — the firmware presents a baseline off-hook dial tone
regardless; bit9 roughly **doubles** its amplitude (a level/boost modulator, not a clean on/off).

**Also mapped:** the **full ARM register file** per `DumpRegistersARM` (0x00–0x54 step 4 + 0x58/0x5c);
the P3 flash/bulk-memory read/write/erase protocol decoded byte-for-byte (read/geometry safe; write/erase
brick-capable and never attempted).

**Hard NEGATIVES (struck off — don't re-chase):** `SetTelephoneEnable` is **not** the line-feed switch
(its leaf only sets a software flag — line power is `InitTjHardware` reg0 bit0, already in
`tj_linepower.py`); all 6 EEPROM identity methods are **stubs** on the c200 (no serial-spoof frame — the
E-number is network-provisioned); gain/echo/record/buzzer/LCD/ring-detect are stubs too.

**New/updated tools this wave (hardware-validated):** `tools/tj_armreg.py` (generic P2 read/write/rmw
lib), `tools/tj_dumpregs.py` (register snapshot / `--watch` state-diff), `tools/tj_callerid.py`, and
`tools/tj_modem.py` — which drives the **Cisco-1841 aux-line modem** over reverse-telnet as a hands-free
test rig (real loop current for off-hook, `AT#CID=1` for CID) to verify hook/DTMF/CID/polarity/loopback
without a human at the handset. Baseline register snapshot:
`captures/arm-regs-baseline-lineon-onhook.txt`.

---

## 7. Full 8 MB flash dump from Linux, normal mode (2026-07-16)

The P3 flash **page-read** primitive (sub-reg `0x13` = `_ReadOnePhyPageBulkMemARM`, 2048 B/page)
**answers in normal softphone mode** — overturning the old "flash is updater-mode-gated" belief for
reads. Dumped all **8 MB @ ~163 KB/s, read-only** (writes/erase untested, may still be gated — never
attempted). Must stop `mj-fxs-bridge`/`mj-baresip` first (same HID channel). Layout:

| Offset | Content |
|---|---|
| `0x000000` | **Config block** — `sn=<device-serial> / mf=TigerJet / ps=magicJackVideo`, `hash=` empty |
| `0x044000`–`0x092000` | **ZeroCD driver image** — ISO9660 + Windows PE + SJphone/`talk4free` Autorun |
| `0x41b000`–`0x672000` (~2.4 MB) | **On-device ARM firmware** — an **eCos-based "ArmJack" SDK** build: full SIP stack, embedded httpd (`/httpd/v3_0`), VoIP engine, A-law/DTMF/ring codecs (reset vectors match the live boot dump) |
| `0x7a0000` | **`MJSF` signed provisioning blob** (MD5-only container) |

**NO E-number anywhere in flash → SIP identity is network-provisioned, CONFIRMED** — on-chip identity is
a dead end. Detail + artifacts list: [`captures/flash-dump-analysis.md`](captures/flash-dump-analysis.md).
(Raw `magicjack-flash-8MB.bin` / `armjack-firmware-region.bin` are gitignored — large.)

---

## 8. SIP config crypto — reverse-engineered (account stays per-session-keyed) (2026-07-16)

The SJphone config cipher (`CSJEncryptor`) is **fully RE'd**: `"SJEN"` + **RC4**(key) wrapping
`"SJCF"` + `uint32_BE(len)` + **zlib** → **UTF-16LE INI**. Three key tiers:

| Data | Key | Status |
|---|---|---|
| Local flash **`Profiles.db`** | fixed **app key** = `MD5(decoy_A)‖MD5(decoy_B)` = `1a909c8e…f340ed5dd` | ✅ **DECRYPTED** — but it is only a bare `SIPProxy` **shell** ("magicJack"), **NO account** |
| **PatchCache** account patch (`<E-number>`, proxy `216.234.65.40:5070`, talk4free.com, no auth) | per-device firmware master key + per-session RC4 | ❌ **not statically decryptable** |
| Provisioning HTTP responses | per-request RC4 session key (in `?dbkey=`) | ❌ per-session |

The real account's values are those already known from the captured pcap — the on-device copy is **not**
statically recoverable. **Do not claim the account was decrypted; it was not.** Decryptors:
`tools/sjen_decrypt.py`, `tools/prov_decrypt.py`, `tools/masterkey_decrypt.py`. Full synthesis:
[`captures/provisioning-crypto-RE.md`](captures/provisioning-crypto-RE.md).

---

## 9. Firmware unpacked via a live RAM dump (2026-07-16)

The flash firmware is **LZ-packed** (file ≠ RAM: RAM `0x0 = 12 00 00 ea` vs flash `0e 00 00 ea`), but it
runs **decompressed in SDRAM**. Key technique: the SoC-memory port (cmd `0x80`/`0x0e`) **auto-increments
across successive GETs** (32 blocks/setup) → **~190 KB/s** RAM read (vs the slow per-word path).
`tools/ram_dump.py` pulls live SDRAM `0x0–0x1000000` → the **unpacked, running eCos firmware**
(`captures/ram-0-16M.bin`, disassemblable ARM base 0) — what the packed flash never exposed. From it
(`tools/masterkey_decrypt.py`) the config cipher was confirmed in-firmware and the **two hardcoded
firmware RC4 keys recovered**: `magicJack.Patch` config key = 200 B `e0e7060e…` (@`0x36a103`) and
`dbkey`/provisioning key = 201 B `8a365901…` (@`0x36a589`) — base64 rodata; the firmware key is **not**
MD5-of-decoy (MD5 is only HTTP/SIP Digest here).

**Final wall:** the account + captured provisioning are **per-session keyed** (nonce/token/uuid are
runtime-only); in USB mode the SIP stack is dormant, so the decrypted account never appears in the 16 MB
RAM. Reusable win: the 190 KB/s RAM-read makes future live SDRAM RE fast. Detail:
[`captures/provisioning-crypto-RE.md`](captures/provisioning-crypto-RE.md) (Update section).

---

## 10. Why Ethernet/SIP is off over USB — the mode gate & the cut-cable path (2026-07-16)

The USB-vs-standalone personality is decided **ONCE AT BOOT** by **USB-host-enumeration detection** at
the UDC/PMU block **`0x90600000`** (`init_usbd`@`0x243c78` → detector `fcn.0x244278`):

- **USB host enumerates it → USB mode:** Ethernet clock-init is skipped, PHY/MAC never powered, DHCP/SIP/
  provisioning **dormant** (live globals confirm: DHCP thread handle `0`, dhcpstate `0xFF`). The device is
  a plain USB peripheral.
- **Wall power / USB-detect timeout → AC mode** (`*0xd31660=2`, "Enter AC mode"): Ethernet powers up, the
  device provisions and **REGISTERs**.

The gate is **reset-based** and **decided at boot** — **NO host HID command enables it** (confirmed; the
driver handlers have nothing network/mode). A **software "cut-cable"** via Linux USB (uhubctl per-port
power + port disable/deauthorize) was **tested and does NOT work** — the hub couples power and data, so
the bare bus reset still trips the gate. The viable path is a **physical cut-cable**: keep **VBUS
continuous** (harmless, since detection is reset-based, not power-based) but hold **D+/D− open through
boot** so no host enumerates → the device boots into AC mode and provisions while still USB-powered.

Alternative clean capture path: cold-boot on wall power (AC mode → account + session key live in RAM),
then attach the data-USB and, if it enumerates, dump RAM via `tj_armmem`; else fall back to a UART tap
(16550 @`0x98200000`). This is the only route to the live decrypted account (§8). HW refs: MAC = Faraday
FTMAC110 @`0x91000000` (MDIO @+0x90/+0x94), PHY reset GPIO `0x98100318`, MAC clock = SCU `0x90600144
bit5`. ⚠️ `0x90600000` also drives our USB link — MMIO-replay there can sever hidraw.

---

## 11. Tools (Linux, need `sudo` for `hidraw1`)

| Script | Purpose |
|---|---|
| **`tools/tj_linepower.py`** | **★ FXS control:** `on`/`off`/`status`/`hook`/`monitor`/`ring` — line power, hook, ring (hardware-verified) |
| `tools/tj_dtmf_decode.py` | Goertzel DTMF decoder for the card-1 capture |
| **`tools/tj_callerid.py`** | **★ Caller-ID injection** (Bellcore FSK over ALSA card 1) — `--name`/`--number`/`--no-ring`/`--wav` |
| `tools/tj_armreg.py` | generic P2 ARM control-register read/write/rmw lib |
| `tools/tj_dumpregs.py` | register snapshot / `--watch` state-diff |
| `tools/tj_armmem.py` | arbitrary ARM SoC memory + ARM control-register R/W over HID |
| `tools/tj_modem.py` | drive the Cisco-1841 aux modem (reverse-telnet) as a hands-free hook/CID test rig |
| `tools/ram_dump.py` | fast (~190 KB/s) live SDRAM dump → unpacked firmware image |
| `tools/ram_scan.py` | scan the live RAM image |
| `tools/sjen_decrypt.py` | generic SJEN/RC4/zlib config decryptor (`--key`/`--key-hex`/`--key-file`) |
| `tools/prov_decrypt.py` | decrypt the flash `Profiles.db` with the fixed app key; documents the key tiers |
| `tools/masterkey_decrypt.py` | recover the firmware RC4 keys from the unpacked RAM image |
| `tools/tj_arm.py` | ARM read-window sweep + read-only flash-open probe (exploration) |
| `tools/hid_getfeature.py`, `tj_read.py`, `tj_bankcheck.py`, `tj_diag.py`, `tj_hyp.py`, `tj_v2.py` | early fingerprint / read-model / fuzzing scripts |

---

## 12. Status

**FXS-over-USB: DONE and in production.** Line power, hook, two-way audio, DTMF, and ring are all driven
from Linux over the RE'd ARM protocol and hardware-verified, and wired into Asterisk as **ext 200**
(baresip on ALSA card 1 at native 8 kHz + the `mj-fxs-bridge.py` root daemon — full write-up
[`../03-magicjack-sip/README-fxs-usb.md`](../03-magicjack-sip/README-fxs-usb.md)).

**Deep RE: substantially advanced.** ~30-feature catalog with four new verified primitives (§6); full
8 MB flash dump (§7); config crypto RE'd, `Profiles.db` decrypted (§8); firmware unpacked with its RC4
keys (§9); mode gate fully mapped (§10).

**Open levers:** the physical **cut-cable** to force AC-mode provisioning over continuous USB power (§10);
capturing the live **decrypted account** (per-session-keyed — only obtainable during an AC-mode
provisioning boot, §8/§9); offline RE of `armjack-firmware-region.bin`. ⚠️ SoC/flash writes can
reboot/brick — keep any write minimal, reversible, and never blind.

**Reminder:** even fully working, this USB personality does **not** carry the magicJack phone *number* —
that lives behind the Ethernet/ATA brain (`<E-number>`), the SIP door in
[`../03-magicjack-sip/`](../03-magicjack-sip/).

## 13. Strategic note
The **USB door** yields a complete FXS-over-USB adapter but **not** the magicJack number. The **SIP door
(§4)** is the one that serves self-hosting the existing line — and the crypto/firmware/mode-gate RE above
now maps exactly how the number is provisioned and what it would take to carry it (a physical cut-cable +
a live provisioning capture, or the proven DNAT redirect). See [`../03-magicjack-sip/`](../03-magicjack-sip/).
