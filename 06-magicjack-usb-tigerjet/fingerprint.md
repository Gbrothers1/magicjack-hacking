# magicJack — USB fingerprint

> Unit is a magicJack **HOME (Home Edition)** — user-verified; the firmware/ZeroCD self-report as
> "magicJackPlus" (same YMAX/TigerJet platform). The **64-byte vendor FEATURE report** flagged below as
> "the RE target for full control" is now **fully reverse-engineered** — see `README.md` §5 and
> `captures/mac-binary-ARM-protocol.md`.

**Device:** `06e6:c200` — Tiger Jet Network, Inc. "USB Internet Phone by TigerJet"
**Serial:** <device-serial> · **bcdDevice** 0.08 · USB 1.1 full-speed · bus-powered 500mA
**CD label:** `YMaxCorp magicJackPlus CD 3.00`

The magicJack Plus is a **TigerJet USB telephony controller**. Over USB it presents as a
generic USB-audio + HID "handset" bridge — NOT an AT modem, and NOT its SIP stack.
The RJ11 analog phone plugged into it is the "Handset" terminal.

## 5 interfaces
| # | Class | Role | Node |
|---|-------|------|------|
| 0 | Mass Storage (SCSI BBB) | ZeroCD autorun installer | `sr0` (eject it) |
| 1 | Audio Control | mixer / terminal topology | card1 controls |
| 2 | Audio Streaming (capture) | **line → host** | card1 capture |
| 3 | Audio Streaming (playback) | **host → line** | card1 playback |
| 4 | HID (Telephony) | hook / keypad / vendor ctrl | `hidraw1` EP 0x85 IN |

## Audio (ALSA card 1 [TigerJet], device 0, S16_LE mono)
- **Capture** (far-end/line audio in): 8000 Hz only — iso EP 0x83 IN
- **Playback** (your audio out to line): 16000 Hz (alt1) or 8000 Hz (alt2) — iso EP 0x04 OUT
- Mixer scontrols: `PCM`, `Headset`
- AudioControl topology: T1 Handset-IN, T2 USBStream-IN, T3 Handset-OUT, T4 USBStream-OUT,
  FeatureUnit5 (mute+vol) on handset→host, FeatureUnit6 (mute+vol) on host→handset.

## HID report descriptor (decoded, 0x6f bytes)
Usage Page = **Telephony (0x0B)**, Application = Phone.
INPUT report (device→host, 2 bytes) packs:
- 4 bits: **keypad code** 1..12 = telephony keys 0xB0..0xBB (digits 0-9, *, #) — DTMF from attached phone
- 6 bits: telephony function array (hook-flash / redial / etc.: usages 0x21,0x23,0x24,0x25,0x26,0x2F,0x31)
- 2 bits: relative volume (Consumer 0xE0)
- 3 bits: buttons 1..4 (Button page)
- 1 bit: **Hook Switch** (Telephony 0x20) — on/off hook of attached phone
FEATURE report: **64-byte VENDOR report (usage page 0xFFFF)** — the chip's register/control
backdoor (DTMF gen to line, ring, codec/hybrid config). This is the RE target for full control.

## What "dial from USB" actually was
The Windows app ran SIP itself over the network, used the **audio streams** for voice,
read the attached phone's **hook/keypad via HID**, and drove the chip via the **64-byte vendor
feature report** (DTMF out, ring, hybrid/echo). magicJack servers were never required by the USB path.

## Implication for self-hosting
This makes the dongle a **USB audio + HID endpoint we can wire straight into Asterisk**
(chan_alsa or an ALSA bridge), with the analog phone as the station — bypassing magicJack
entirely. Hook/DTMF come from hidraw1; two-way voice from card 1.
