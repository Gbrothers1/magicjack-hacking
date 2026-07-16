# TigerJet USB register-protocol — hardware fuzzing log

Empirical results from driving the live `06e6:c200` via `/dev/hidraw1` (HID feature reports,
`SET_REPORT`/`GET_REPORT`, report id 0, 64-byte data). Confirmed at the USB wire level with `usbmon`.

## Transport (confirmed via usbmon)
- Writes = `HidD_SetFeature` = control `SET_REPORT`, `bmRequestType 0x21`, `bRequest 0x09`,
  `wValue 0x0300` (report type Feature=3, id 0), `wLength 64`.
- Reads = `HidD_GetFeature` = `GET_REPORT`.
- No interrupt-OUT endpoint; the HID interface has only interrupt-IN `0x85` + the feature report.

## What WORKS
- **Page-select** `04 <x> <page> 00` — byte-3 = page. Deterministic; selects which 64-byte page
  `GET_FEATURE` returns. Verified: pages `0x00/0x20/0x40` give distinct, stable, repeatable windows;
  `04 40 20` ≡ `04 20 20` (byte-3 wins, byte-2 ignored for page). Wire status = 0 (completes).
- **`0x40`-header write** `40 <reg> <bank> 01 <val>` — **completes on the wire with URB status 0**
  (NOT a stall, NOT ignored-at-USB). Matches the Tiger560B datasheet where request-type `0x40`=write.
  This is the most likely real write command. BUT its effect is **not observable** in the readable
  mirror (see below), so individual writes can't be verified from Linux.

## What STALLS (URB status -32 / EPIPE, harmless, auto-recovers)
- Any `0x04`-header report with a nonzero byte-4 (a data/length byte): `04 55 40 01 B4`,
  `04 24 00 aa`, `04 55 40 02 B4`, `04 55 40 01 B4 00 63`. The `0x04` command only accepts the
  3-byte page-select form (byte-4 must be 0).
- The `0x20`-header 64-byte format `20 55 00 01 B4 …`.
- Most `0xC0`-read forms as feature reports (and they sometimes trigger a device **reset**).
- Raw EP0 vendor control transfers (`bmRequestType 0x40`/`0xC0`, reg in wIndex) — all stall.

## The core blocker: no write-VERIFICATION primitive
- `GET_FEATURE` returns a **fixed status/config struct**, not a live register file. The bytes that
  look like reg values (page-0 offset `0x05`=`0x32`=reg0x3C, `0x08–0x0B`=`80 3e 00 7d`=regs0x30-33)
  are **defaults that do not change** when those registers are written via any tried command.
- No register read-response arrives on the interrupt-IN channel either (drained after each command).
- Command-byte sweep (0x02–0xC4, 16 values) writing reg 0x24: none produced any observable change.

## Conclusion of blind fuzzing
The likely write command (`0x40`) is identified and completes on the wire, but **writes cannot be
verified from Linux** (status mirror is fixed; no readback). So the only way to know if an activation
sequence actually worked is the **port LED / dial tone** — which requires the *complete, correct*
ProSLIC bring-up (power-up + calibration + LINEFEED), not the partial codec init recovered so far.
Malformed probes occasionally trigger a harmless device re-enumeration (recovers fully).

**Definitive unblock = a real-Windows USBPcap capture** of `magicJack.exe` activating the device,
to get the exact accepted write field-layout + any open/enable precondition + the full ProSLIC init.

---

## UPDATE — register WRITES work; SPORT/SPI-bridge is firmware-blocked

### ✅ BREAKTHROUGH: register writes DO work from Linux
The entire "all writes stall" wall was a false conclusion caused by **fixating on reg 0x55, which is
the READ-ONLY chip-revision register** (it reads back 0x12/0x13). Writes to *writable* registers land
perfectly, **verified by round-trip readback**:
- Write format: `04 <reg> <reg&0xE0> 01 <val>` (count=1) — e.g. `04 30 20 01 55` sets reg 0x30=0x55.
- Read: bank-select `04 <bank> <bank> 00`, GET_FEATURE, value at offset `reg & 0x1F`.
- Confirmed: reg 0x30, 0x24 round-trip any value. count=4 block-write also lands (`04 30 20 04 A1 B2 C3 D4` → regs 0x30-0x33).
- Read-only/protected regs (0x22, 0x55) STALL on write — that's expected.

### ❌ The real wall: the SPI-bridge (SPORT) registers are firmware-blocked
To power the phone port you must reach the **Si321x ProSLIC**, which sits behind the TigerJet SPI
bridge at TJ regs **0x26/0x27/0x28/0x29**. The magicJack firmware **blocks host writes to 0x26/0x27/0x29**:
- `04 26 ...`, `04 27 ...`, `04 29 ...` (count=1) all **STALL**. (Only 0x28 accepts a write.)
- count=4 block-write to 0x26 (the ProSLIC-access transaction) **STALLS** under 0x04, and is
  silently ignored under 0x40 (TJ regs unchanged, ProSLIC reads all 0x00, no round-trip).
- Raw libusb vendor transfers (bRequest=4, the tjctl.c protocol) also STALL, even with the kernel
  driver detached — so the older 560-FXS EP0 vendor path is not exposed on c200.
- **Contrast:** count=4 to a *normal* reg (0x30) lands perfectly — so it's the SPORT registers
  specifically that the firmware protects, not the count mechanism.

### Interpretation
On c200 (USB softphone mode) the **on-chip firmware owns the Si321x SLIC** and denies the host
direct SPI-bridge access. `TjIpSys.dll` never writes LINEFEED; the ProSLIC power-up (DC-DC +
calibration + LINEFEED=001) lives in `SJHandsetMagicJack.dll`'s SPI layer, reached via
`SetTelephoneEnable` → but that path needs SPORT, which is blocked for us. This is why the port
stays dark. The Windows softphone reaches the SPORT via the signed TigerJet kernel driver using a
transport the firmware permits — the definitive way to learn it is a **real-Windows USBPcap capture**.

### What IS achievable from Linux now
Full read + write access to the TigerJet's *normal* register space, and the USB audio path (card 1).
The analog line power-up is the one firmware-gated piece.

---

## FINAL DETERMINATION — port activation is firmware-gated on c200

Two independent multi-agent workflows produced complete, byte-exact activation plans (reset-release →
SPI self-test → DC-DC power-up → calibration → LINEFEED=0x01), all expressed as SPORT writes
`40 26 00 04 <reg> <val> 00 63`. Executed on hardware in full, including:
- ProSLIC reset-release (TigerJet reg0x00 0xC0→0x40 EXTRST toggle) — accepted.
- SPI bridge self-test (write ProSLIC reg0x32=0xB7, read back) — **read back 0x00, never 0xB7**.
- Full sweep of kick bytes (0x63/0x67/0x61/0x43/0x27/0x47/0x23/0x62) × bank (0x00/0x20) ×
  header (0x40/0x04) × byte-order — **no variant passed the self-test**.
- DC-DC power-up + LINEFEED Forward Active — **LED stayed OFF, VBAT sense 0x00, LOOP_STAT 0x00**.

**Conclusion:** the magicJack c200 firmware blocks host access to the TigerJet↔ProSLIC SPI bridge.
Normal TigerJet registers read/write fine from Linux, but the SPORT window (regs 0x26/0x27/0x29) is
firmware-protected, so the Si321x ProSLIC cannot be driven from the host — the analog line power-up
(the LED / loop voltage / dial tone) is owned by the device's on-chip firmware and is not reachable
from Linux userspace via HID feature reports or libusb vendor transfers.

The Windows softphone activates the port via its signed TigerJet kernel driver using a transport the
firmware permits; replicating it would require capturing that driver's exact USB traffic on a real
Windows host (USBPcap) — and even then it may not be reproducible from Linux userspace.

### Net capability delivered from Linux (durable):
- Full TigerJet normal-register READ and WRITE (format: write `04 <reg> <reg&0xE0> 01 <val>`;
  read: bank-select `04 <bank> <bank> 00` + GET_FEATURE, value at offset `reg & 0x1F`).
- USB audio path present as ALSA card 1.
- The analog FXS port power-up is NOT host-accessible on this device.

---

## ADDENDUM — ruled out the two remaining USB surfaces (interfaces 0 and 1)

Checked whether the SPORT gate could be reached via a transport other than HID/EP0-vendor:

- **Interface 0 (Mass Storage / ZeroCD), `/dev/sg2`**: `sg_inq` → SCSI-2, `PDT=5` (cd/dvd), vendor
  `YMaxCorp` / product `magicJackPlus CD` / rev `3.00`. **No VPD pages** (`sg_inq -e` fails —
  "probably a STANDARD INQUIRY response"), **`sg_opcodes` (RSOC) → Illegal request/Invalid opcode**.
  This is a bare-bones generic USB-storage-to-CD bridge, not a register tunnel — no vendor CDB
  backdoor to chase here. Did not fuzz vendor opcode space (0xC0–0xFF) given no RSOC support and
  real risk of hanging the live SCSI/USB stack for low expected payoff.
- **Interface 1 (Audio Control)**: descriptor has only standard Input/Output Terminals (Handset,
  USB Streaming) and two Feature Units (5=mute/vol handset→host, 6=mute/vol host→handset) — **no
  Extension Unit**, so there's no vendor `SET_CUR`/`GET_CUR` control-selector path either.

**Conclusion:** all 5 USB interfaces are now accounted for. HID vendor feature report, raw EP0
vendor transfer, mass-storage vendor CDB, and audio class-specific requests are all either blocked
or absent. The SPORT/SPI-bridge gate is not reachable from **any** USB-visible surface on c200 —
this is a complete result, not just the HID path. Further access requires leaving USB entirely
(physical board access) or leaving this exact unit (a sibling device without the gate). See
`../README.md` §7 for the prioritized physical/lateral next steps.
