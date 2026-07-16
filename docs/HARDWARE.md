# Hardware reference — magicJack HOME (Home Edition)

Everything known about the specific unit this project reverse-engineers, grouped and tagged by
confidence:

- **[verified]** — observed directly on the device (via `lsusb`, `/proc/asound`, firmware memory, or
  physical behavior with a real phone attached).
- **[reported]** — the device's own firmware / ZeroCD self-report (may not match the physical model).
- **[unverified]** — could not be confirmed; read it off your own unit to be sure.

> **The name discrepancy is itself a finding.** The physical unit is a **magicJack HOME**, but its
> firmware banner and ZeroCD volume still self-identify as **`magicJackPlus`** — because the HOME is
> built on the **same YMAX / TigerJet platform** as the magicJack Plus. Treat the `Plus` strings below
> as *observed platform strings*, not the product name.

For a real photo of the device, see magicJack's official product page:
<https://www.magicjack.com/> (link only — no vendor image is redistributed in this repo). The
illustration in [`magicjack.svg`](magicjack.svg) is an original drawing.

---

## Physical  **[verified from photos + behavior]**

- **magicJack HOME** — a small **black enclosure with a blue accent stripe** around it.
- **Front ports:** an **RJ45 Ethernet** jack and an **RJ11 telephone** jack (phone icon), side by side.
- **Status LEDs:** two small indicators (green / amber) on the front.
- **USB:** a **USB-A** connector for plugging into a PC.
- **Power:** DC power for standalone use.
- **Two ways to run it:**
  1. **Standalone** — Ethernet + power; the on-board networked brain runs SIP to magicJack's cloud.
  2. **Over USB** — plugged into a PC; magicJack's app (or, here, our Linux tooling) drives the
     attached phone through the USB front-end. **This project uses the USB path.**

---

## USB identity  **[verified — `lsusb` / kernel enumeration]**

| Field | Value |
|---|---|
| VID:PID | `06e6:c200` — Tiger Jet Network, Inc., "USB Internet Phone by TigerJet" |
| iSerial | `<device-serial>` (USB hardware serial — **not** the SIP account id) |
| bcdDevice | `0.08` |
| USB | 1.1 full-speed, bus-powered (~500 mA) |

**Five interfaces:**

| # | Class | Linux node | Role |
|---|---|---|---|
| 0 | Mass Storage | `sr0` | ZeroCD autorun installer stub |
| 1 | Audio Control | card mixer | mute/volume |
| 2 | Audio Streaming (capture) | ALSA card | line → host, 8 kHz mono |
| 3 | Audio Streaming (playback) | ALSA card | host → line, 8/16 kHz mono |
| 4 | HID (Telephony) | `hidraw` | hook/DTMF input + 64-byte vendor control report (all chip control) |

Full descriptors: [`../06-magicjack-usb-tigerjet/captures/usb-descriptors.txt`](../06-magicjack-usb-tigerjet/captures/usb-descriptors.txt).

---

## Chips  **[verified — firmware strings + behavior]**

- **TigerJet USB controller**, family **TJ780 / TJ880**, **ARM-based** SoC — carries the ARM firmware
  that owns the line. (DevType 8 = the ARM/flash-capable variant; see the ARM protocol notes.)
- **Silicon Labs Si321x ProSLIC** — the analog line / **FXS** chip: powers the loop, generates the
  ring voltage, and detects hook + DTMF on-chip. On this ARM platform it is driven by the TigerJet's
  **on-chip firmware**, not directly by the host.
- **On-board SPI flash** — holds the TigerJet firmware.

Datasheets (background reference, fair use): [`../06-magicjack-usb-tigerjet/datasheets/`](../06-magicjack-usb-tigerjet/datasheets/)
(`Si3210-ProSLIC.pdf`, `Tiger560B.pdf`).

---

## Firmware  **[reported — device self-report]**

- **Firmware banner:** `Version 19.20 (magicJack 560/580/780/880/980/911)`; board `IPC11`.
- **ZeroCD volume label:** `YMaxCorp magicJackPlus CD 3.00`.
- The platform **self-identifies as `magicJackPlus`** even though the physical unit is a HOME (same
  YMAX/TigerJet platform — see the note at the top).

---

## SIP identity  **[verified in packet capture]**

The **networked brain** (Ethernet, the ATA/SoC — a separate computer from the USB front-end) registers
to magicJack's cloud as:

- `sip:EXXXXXXXXXXXX@talk4free.com` — **placeholder**; the real serial-like username is redacted in
  this public repo.
- **No authentication** (empty password unless challenged), **G.711** audio.

This is what makes the self-hosting hack possible — a matching Asterisk endpoint captures the
registration. Details: [`../02-cisco-1841/magicjack-sip-notes.md`](../02-cisco-1841/magicjack-sip-notes.md)
and [`../03-magicjack-sip/`](../03-magicjack-sip/). Note the SIP username lives in the ATA brain's DRAM
and is **separate** from the USB hardware serial `<device-serial>`.

---

## Model number / FCC ID  **[unverified]**

Not confirmed for this HOME unit. Earlier notes assumed a Plus (`K1103` / `Y79K1103`), which is **not
verified** here. **Read the model number and FCC ID off the label on your own device** to identify it
precisely — then look the FCC ID up at <https://fcc.report/> or the FCC OET database.
