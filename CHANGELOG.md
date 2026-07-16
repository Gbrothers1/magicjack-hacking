# Changelog

The dated timeline of the lab. Newest first. Deep technical detail lives in each project's own docs;
this is the "what happened when" overview. Dates are the working dates for each milestone.

---

## 2026-07-16 — deep RE: new primitives, full flash dump, config crypto, firmware unpack, mode gate

A second wave of reverse-engineering went well past the working FXS station. Full write-ups in
[`06-magicjack-usb-tigerjet/`](06-magicjack-usb-tigerjet/) (`README.md` §6–§10) and `captures/`.

- **~30-feature catalog** from the symbol-rich macOS `mjupdate` binary
  ([`captures/mac-binary-feature-catalog.md`](06-magicjack-usb-tigerjet/captures/mac-binary-feature-catalog.md)),
  with **four new hardware-verified primitives**:
  - **Caller-ID injection** (`tools/tj_callerid.py`) — a Bellcore Type-1 MDMF packet Bell-202 FSK-modulated
    over USB audio; **decoded flawlessly on a real modem** (`AT#CID=1`: date/time/number/name all correct).
  - **DTMF remover** (`reg0x14 bit4`) — strips DTMF from the line→host audio path.
  - **Digital audio loopback** (`reg0x40 bit4`) — host playback→codec→host capture internally.
  - **Tip/ring (battery) polarity reversal** (`reg0 bit16`) — confirmed reversible; also proved
    `reg0x28[31:16]` is a genuine loop-current measurement.
  - Plus **partial** hardware dial-tone amplitude control (`reg0x14 bit9`).
- **Full 8 MB SPI-flash dump from Linux in normal mode** via the `0x80`/"prwC" page-read primitive —
  overturning the old "flash is updater-mode-gated" belief for reads. Layout mapped (config block, ZeroCD
  image, ~2.4 MB eCos "ArmJack" ARM firmware, MJSF provisioning blob).
  [`captures/flash-dump-analysis.md`](06-magicjack-usb-tigerjet/captures/flash-dump-analysis.md).
- **SIP config crypto reverse-engineered** (`CSJEncryptor`): `"SJEN"`+RC4 wrapping `"SJCF"`+zlib →
  UTF-16LE INI. The local flash `Profiles.db` decrypts with a fixed app key — but it is only a bare
  `SIPProxy` template with **no account**; the real account is per-session-keyed and was **not** decrypted.
  Decryptors: `tools/{sjen_decrypt,prov_decrypt,masterkey_decrypt}.py`.
  [`captures/provisioning-crypto-RE.md`](06-magicjack-usb-tigerjet/captures/provisioning-crypto-RE.md).
- **Firmware unpacked via a live RAM dump.** The flash image is LZ-packed but runs decompressed in SDRAM;
  the SoC-memory port auto-increments across GETs (~190 KB/s), so `tools/ram_dump.py` pulls the unpacked,
  running eCos firmware — and its two hardcoded RC4 keys were recovered from it.
- **USB-vs-ATA "mode gate" fully mapped.** The personality is decided once at boot by USB-host-enumeration
  detection at the UDC/PMU block; **no host command flips it**, and a software "cut-cable" over USB does
  not work (the hub couples power and data). The viable route is a **physical cut-cable** (VBUS continuous,
  D+/D− held open through boot) so the device boots into AC mode and provisions while still USB-powered.

## 2026-07-15 — magicJack USB personality reverse-engineered + wired into Asterisk

The flagship result. Full write-up: [`06-magicjack-usb-tigerjet/HOW-IT-WAS-HACKED.md`](06-magicjack-usb-tigerjet/HOW-IT-WAS-HACKED.md).

- **Overturned the "USB port can't be driven from Linux" verdict.** Pulled magicJack's **macOS**
  driver (symbol-rich, unlike the stripped Windows DLL), disassembled it, and recovered the real
  ARM control protocol: arbitrary **SoC-memory** and **control-register** access over HID feature
  reports. The earlier "firmware-locked SPI bridge" was a red herring (wrong access path).
- **Proved arbitrary memory access on hardware** by reading the chip's own ARM reset vectors / boot
  code out of its RAM. Tool: `06-.../tools/tj_armmem.py`.
- **Found and hardware-verified every telephone function as a register:** line power (reg0 bit0 +
  InitTjHardware sequence → dial tone + LED), hook (reg0x14 bit31), on-chip **DTMF** decoder
  (reg0x14 byte0x16/byte0x17), **ring** (reg0 bits 8-9 → the handset physically rang). Tool:
  `06-.../tools/tj_linepower.py` (`on`/`off`/`hook`/`ring`).
- **Two-way audio** confirmed over ALSA card 1 (native 8 kHz).
- **Integrated as Asterisk extension 200** (`03-magicjack-sip/`): a `baresip` softphone carries the
  audio at native 8 kHz µ-law; a root daemon (`mj-fxs-bridge.py`) provides FXS behavior — dial tone +
  register-DTMF dialing, inbound ring + answer-on-offhook, hangups both ways, reorder tone on far-end
  hangup — driving baresip over `ctrl_tcp`. Both run as systemd services. (An earlier `chan_console`
  approach failed on a 16 kHz-vs-8 kHz resample mismatch and was dropped.)

## 2026-07-11 — magicJack provisioning RE + durable registration

- Documented the magicJack provisioning handshake; replaced a fragile conntrack-zombie NAT redirect
  with a durable one on the Cisco 1841 so the ATA reliably registers to the self-hosted Asterisk.

## 2026-07-10 — self-hosted magicJack SIP, Cisco 1841, voicemail

- **Reverse-engineered the magicJack ATA's SIP and self-hosted it in Asterisk**
  (`03-magicjack-sip/`): it registers with `user@domain`, **no auth**, G.711 — so a matching Asterisk
  endpoint captures it. The networked magicJack now lives on my PBX, not magicJack's cloud.
- Added the **Cisco 1841** edge router (`02-cisco-1841/`) — console-pulled config; it's also the
  packet-capture vantage point that revealed the SIP.
- Fixed voicemail (forced file-based `app_voicemail` over the IMAP variant).
