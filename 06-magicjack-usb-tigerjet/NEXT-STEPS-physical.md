# New access vectors — beyond USB

> **⚠️ SUPERSEDED PREMISE (2026-07-15/16).** The heading below ("c200 is a dead end over USB") is
> **wrong** — it reflected the early blind-fuzzing wall. The vendor macOS binary later gave us ARM
> control-register + SoC-memory + flash-page HID ports (`README.md` §5–§9): the **FXS port is driven
> from Linux** (line power/hook/audio/DTMF/ring), the **full 8 MB flash was read from the host in normal
> mode** (item 5 below is done — no clip needed), and the **firmware was unpacked from live RAM**. What
> is still genuinely physical-only: forcing **AC-mode provisioning** to capture the live decrypted
> account — the current best idea is a **physical cut-cable** (continuous VBUS, D+/D− open through boot;
> the software uhubctl version was tested and fails), and/or a **UART tap** (16550 @`0x98200000`) during
> an AC-mode boot (README §10). Items 1–4 below remain valid *alternatives* but are no longer required.

`captures/hardware-fuzzing-log.md` now has a complete negative result: HID vendor feature reports,
raw EP0 vendor transfers, the mass-storage vendor CDB path, and the audio class-specific control
path have **all** been tried against the live `06e6:c200` and all either stall or are firmware-gated
before the SPI bridge. That's every USB-visible surface the device exposes. Staying on USB and
fuzzing harder is not going to move this forward — the five ideas below leave USB (or leave this
exact unit) instead. Ranked by effort/payoff, not by order of execution — pick based on what's on
hand.

## 1. Buy the un-gated sibling chip: TigerJet 560 FXS (USB `06e6:831c`)
`reference-drivers/tjctl.c` in this repo is a **complete, working, 20-year-old open-source Linux
driver** for that exact device — same TigerJet-ASIC + Si3210-ProSLIC family, but it reaches the
SPI bridge over raw EP0 vendor transfers (`InitProSlic()`, `calibrateAndActivateProSlic()`,
`readProSlicDirectReg()`/`writeProSlicDirectReg()`) with **no firmware gate**. That's *why* the
"raw libusb vendor transfer, tjctl.c protocol" line in the fuzzing log stalls on c200 but worked
on 831c back in 2004. A used/NOS TigerJet-badged (non-magicJack) USB phone adapter on eBay is
cheap and sidesteps the entire c200 puzzle: plug in, run `tjctl`, get FXS-over-USB into Asterisk
directly. Doesn't unlock *this* unit, but delivers the capability the USB door was chasing
(FXS-over-USB) with near-zero effort and zero risk to the magicJack unit. **Best effort/payoff
ratio of the five.**

## 2. UART recon on the ATA mainboard (the Ethernet/SIP brain, not the TigerJet dongle)
Everything so far has targeted the USB front-end. The **other board** in the Plus — the
Ethernet-connected SoC that owns the phone number and runs SIP to magicJack's cloud (see
`README.md` §"Two brains, one box") — hasn't been physically examined at all. Embedded VoIP ATAs
almost always carry a 3.3V TTL UART console (u-boot + embedded Linux/RTOS) on unpopulated header
pads near the main SoC. Passive recon: open the case, identify the SoC, probe candidate
TX/RX/GND pads with a multimeter (3.3V idle-high = likely TX), clip a USB-UART adapter, power on
over wall power, and just listen — most bootloaders dump a banner and a boot log unprompted.
Interrupting autoboot (a keypress within the u-boot countdown) commonly drops to a bootloader
shell with filesystem/memory access. This is **purely passive/listen-only and zero-risk to the
device or to magicJack's servers** — it's the highest-payoff avenue (root on the brain that
actually owns the number) and the only one not yet attempted in any form.

## 3. Passive SPI bus tap between the TigerJet ASIC and the Si3210 ProSLIC
Identify the 4-wire SPI bus (CS/CLK/MOSI/MISO) between the two chips on the c200 board (datasheet
pinout + continuity trace). Clip a cheap logic analyzer (any FX2-based "24MHz 8-channel" clone +
PulseView, ~$10) across those four lines, then power the port using the **real Windows softphone**
(a Windows VM with USB passthrough — QEMU/VirtualBox both support this — is enough; doesn't need
bare metal). This answers the open question definitively at the hardware level: what the on-chip
firmware actually shifts out to the ProSLIC to unlock LINEFEED, independent of which USB transport
carries the *trigger* for it. Purely passive (no signal injection), so no risk of bricking anything.

## 4. Direct ProSLIC hijack (become the SPI master yourself)
The natural follow-on to #3: once the SPI pins are located, lift/cut them from the TigerJet ASIC's
side of the bus and wire them to a Raspberry Pi (`spidev`) or an FT232H/Bus Pirate. Drive the Si3210
directly using either the public datasheet register sequence (`datasheets/Si3210-ProSLIC.pdf`,
already in this repo) or — better — literally reuse `tjctl.c`'s already-correct
`InitProSlic()`/`calibrateAndActivateProSlic()` sequence, which is known-good on this chip family.
The c200 firmware's gate only governs its *own* SPI master logic; once an external master owns the
bus, the gate is architecturally irrelevant. This is the most direct "finish the job" option but
requires fine-pitch soldering/rework — do #3 first to confirm pin identification before cutting
anything.

## 5. Read-only SPI-flash dump of the TJ780 firmware — ✅ DONE from the host (no clip needed)
**Resolved 2026-07-16:** the flash **read** surface answers over USB HID in normal mode (P3 page-read,
sub-reg `0x13`). The full **8 MB was dumped from Linux** (`captures/flash-dump-analysis.md`) and the
*unpacked* firmware pulled from live RAM (`README.md` §7/§9) — so the in-circuit clip below is no longer
needed for a firmware dump. (Erase/write remain untested and possibly gated — never attempted.) Original
note preserved for reference:

The `61-65` opcode range (`protocol.md` §"Opcode map") is flash erase/write/read; erase/write are
guarded by magic `0x07773456` but **read was never confirmed to require it**. Either try the flash
**read** opcode from the host, or — safer and independent of host gating — clip a SOIC/TSSOP test
clip on the SPI flash chip in-circuit with a CH341A or a Pi's `spidev` while the board is otherwise
untouched, and dump it read-only. Disassembling the firmware would show exactly what condition
gates SPORT register access (test/manufacturing backdoor opcode, a signed-command check, or simply
hardwired off in USB-softphone mode) — informative even if none of #1–4 pan out, and pure-read is
zero-risk to the flash contents.

---
**If picking one to start today with no new hardware purchase:** #2 (UART recon) — it's listen-only,
uses tools already implied by this household's Cisco-console workflow, and targets the board that
actually matters (the number-owning brain), not the lower-value USB door.
**If picking one to start today with a $10 purchase:** #1 (sibling 831c unit) — closes the loop on
"FXS-over-USB into Asterisk" almost immediately using a driver already sitting in this repo.
