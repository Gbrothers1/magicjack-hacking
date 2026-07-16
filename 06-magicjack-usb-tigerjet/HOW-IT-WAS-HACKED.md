# How the magicJack USB personality was reverse-engineered

The technical process — method, dead ends, and the pivot that cracked it. For the plain-language
version see [`../docs/the-magicjack-hack.md`](../docs/the-magicjack-hack.md); for the final protocol
reference see [`captures/mac-binary-ARM-protocol.md`](captures/mac-binary-ARM-protocol.md).

The goal throughout: **drive the magicJack's analog phone port from Linux — power, ring, hook, DTMF,
audio — with none of magicJack's software running.** No interaction with magicJack's servers; pure
interoperability RE against a device I own and a driver binary that's publicly downloadable.

---

## 1. Fingerprint the device (what does it even look like to the OS?)

Plugged into USB, the magicJack Plus enumerates as **`06e6:c200`** — a *TigerJet* USB telephony
controller — and presents five interfaces: a ZeroCD mass-storage stub, three USB-audio interfaces
(so it's a sound card), and a **HID "telephony" interface** exposing a 64-byte vendor feature report.
The analog line itself is a **Silicon Labs Si321x ProSLIC** chip, driven by the TigerJet.

Method: `lsusb -v`, the HID report descriptor, `/proc/asound`, and watching which kernel drivers bind.
Result recorded in [`fingerprint.md`](fingerprint.md). Takeaway: **all chip control happens through
that 64-byte HID feature report** — that's the one channel to understand.

## 2. The dead end: blind register fuzzing

The obvious approach is to poke the HID feature report and watch the chip. This *partly* worked —
normal TigerJet registers read and wrote fine — but every attempt to reach the **ProSLIC** (the
chip that actually powers the phone line) either stalled (USB EPIPE) or was silently ignored.

Two full sweeps concluded the ProSLIC's SPI bridge was "firmware-locked" and the port could not be
powered from Linux. **This conclusion was wrong**, but it's instructive: blind fuzzing gives you a
map of what *doesn't* work without telling you *why*, and it's easy to over-generalize a wall.
(The blow-by-blow is in [`captures/hardware-fuzzing-log.md`](captures/hardware-fuzzing-log.md).)

**Lesson:** when you're guessing at an undocumented protocol and hitting a wall, stop guessing and go
find a description of the protocol. There almost always is one — in the driver.

## 3. The pivot: get the vendor's driver and disassemble it

magicJack ships Windows and macOS drivers. The Windows one (`TjIpSys.dll`) is **stripped** — no
symbols — and was only partially useful. The **macOS** build was the key move.

- The macOS "installer" (`magicJackSetup.dmg`, publicly served from magicJack's upgrade host) is a
  small stub. But its `__DATA` segment contains a **ZIP of the entire softphone app** appended to the
  binary — carve it out (parse the ZIP local-file headers + inflate) and you get `magicJack`,
  `mj_dev` (the device driver), `mj.so`, etc.
- The macOS **firmware-updater** (`mjisoupdate.dmg`) contains **`mjupdate`** — a Mach-O that is
  **symbol-rich C++**: full class names (`CTjIpDev`, `CTj880Phone_Hid`, `CSJHandsetMagicJackDriverBase`),
  method names, even source filenames. This is the Rosetta Stone.

Tools: `7z`/manual ZIP carving, `rabin2`/`radare2` for the Mach-O, `strings`. Everything downloaded
for local analysis only — the same file the official installer fetches.

## 4. Read the protocol out of the symbols

With named functions, the transport fell out quickly:

- The chip is **ARM-based** (`_g_bIsARMBasedChip`), and its firmware space uses a **different HID
  feature-report framing** than the normal register writes — which is *exactly why* the blind SPORT
  writes stalled: they used the wrong framing.
- `_HidWriteTjRegs_ARM` / `_HidReadTjRegs_ARM` / `_SetFeature` spelled out the byte layout. Two access
  ports emerged:
  - an **ARM control-register port** (command byte `0x20`), and
  - an **arbitrary SoC-memory port** (command `0x80` + an ASCII magic `"prwC"`, sub-register `0x0e`).

Then I **proved it on hardware**: reading SoC address `0x00000000` returned the **ARM CPU's reset
vector table** (`0xEA000012` = a branch instruction, then `LDR pc,[pc,#…]` exception vectors), and a
dump of the first bytes decoded as genuine boot code (set CPU to supervisor mode, mask interrupts,
bring up SDRAM). That's the unambiguous "we have arbitrary memory access" moment — you're reading the
chip's own firmware out of its RAM. (Tool: [`tools/tj_armmem.py`](tools/tj_armmem.py).)

**Lesson:** a symbol-rich binary for the *same protocol* on a *different OS* is worth more than any
amount of black-box probing. Vendors strip one platform and forget another.

## 5. Find each telephone function — the differential-register method

Not everything was named (the shipping `mj_dev` softphone is stripped; only the `mjupdate` flasher had
symbols). For the rest I used a simple, powerful **differential** technique that needs no disassembly:

> Snapshot the chip's register window in state A, change one real-world thing, snapshot state B, and
> diff. Whatever bytes moved *are* the register for that thing.

Done with a physical phone attached and a human toggling it (coordination matters — don't run a timed
window and conclude while nobody's at the phone). This pinned down:

- **Line power** — replaying `CTj880Phone_Hid::InitTjHardware()` (from the symbols) powers the line:
  set reg0 bit0, reg0x38, strobe reg0x14 bit7. Verified by **dial tone + the port LED**, and proven
  controllable by toggling it off/on.
- **Hook state** — off-hook vs on-hook diff moved exactly one bit: **reg0x14 bit 31**.
- **DTMF** — holding keys '1', '5', '0' showed the chip's **on-chip decoder** writes the digit into
  **reg0x14** (byte0x16 = key-valid flag, byte0x17 low nibble = digit; 1-9→1-9, 0→0xA, *→0xB, #→0xC).
  This means you read dialed digits from a register — no audio DSP needed, and the firmware dial tone
  is never disturbed.
- **Ring** — traced through the softphone's C++ vtables (reconstructed via RTTI in the stripped
  `mj_dev`): `SetTelephoneEnable`/`SetRinging` bottom out at a device method that sets **reg0 bits 8-9
  (`0x300`)**; the ARM firmware then generates the ring voltage. Verified: **the handset physically
  rang.**

Every capability is captured in [`captures/mac-binary-ARM-protocol.md`](captures/mac-binary-ARM-protocol.md)
and wrapped in [`tools/tj_linepower.py`](tools/tj_linepower.py) (`on`/`off`/`hook`/`ring`).

## 6. Two-way audio

The USB-audio side needed no reverse-engineering — it's standard USB Audio Class, ALSA **card 1**,
8 kHz mono. Verified by recording the live line (capture) and playing a tone the user heard in the
handset (playback). The only gotcha showed up during integration (§7): the device captures at 8 kHz
*only*.

## 7. Integrate into Asterisk (making it a real extension)

Turning working primitives into a usable phone took a couple of design iterations — themselves worth
recording:

- **`chan_alsa` / `chan_console` dead end.** Asterisk's console channel drivers are hardwired to
  16 kHz, but the TigerJet captures at 8 kHz only. Forcing a resample produced XRUNs and choppy,
  one-way audio. Not worth fighting.
- **The clean path: a softphone at native 8 kHz.** [baresip](https://github.com/baresip/baresip) uses
  the ALSA card directly at the device's native 8 kHz µ-law (zero resampling) and registers to
  Asterisk as a normal SIP endpoint. Clean audio immediately.
- **The FXS behavior** — dial tone, keypad dialing, ring, hook, hangup — is a small root daemon
  ([`../03-magicjack-sip/mj-fxs-bridge.py`](../03-magicjack-sip/mj-fxs-bridge.py)) that reads the
  registers from §5 over `/dev/hidraw` and drives baresip over its `ctrl_tcp` control socket. Dialed
  digits come from the DTMF register (so the firmware dial tone stays); inbound calls are detected by
  polling baresip's call list; the physical bell is rung via the ring register.

Full integration write-up: [`../03-magicjack-sip/README-fxs-usb.md`](../03-magicjack-sip/README-fxs-usb.md).

---

# Part II — going deeper (2026-07-15 → 07-16)

With the FXS station done, the same symbol-rich binary and the same three HID ports were pushed much
further: a full feature catalog, a flash dump, the config crypto, a live firmware unpack, and the map of
*why* the number-owning brain sleeps over USB.

## 8. Mine the whole binary — the feature catalog

The `mjupdate` Mach-O has ~3400 symbols, so instead of chasing one function I disassembled it across
**8 subsystems** (a multi-agent radare2 pass with adversarial frame-verification) and transcribed every
telephone primitive into a **~30-feature, frame-level catalog** — the exact HID bytes for each, whether
it's Linux-reachable, and its risk. This also **struck off dead ends** authoritatively: `SetTelephoneEnable`
is a software-flag stub (line power really is `InitTjHardware` reg0 bit0), and all six EEPROM identity
calls are stubs — so the E-number is network-provisioned, not an on-chip read. Result:
[`captures/mac-binary-feature-catalog.md`](captures/mac-binary-feature-catalog.md).

**Lesson:** once you've *proven* the transport (§4), a symbol-rich binary is a parts catalog — read it
exhaustively rather than one function at a time; half the value is the confirmed *negatives*.

## 9. Hardware-verify the new primitives — with a robot at the phone

The catalog predicted several new registers. Rather than a human toggling the handset (§5), I built a
**hands-free rig**: `tools/tj_modem.py` drives the Cisco-1841 aux-line modem over reverse-telnet — a real
Courier draws loop current (a genuine off-hook), dials DTMF, and displays CID (`AT#CID=1`). Driving it
while diffing the register file confirmed, on hardware:

- **Caller-ID injection** (`tools/tj_callerid.py`) — Bellcore Type-1 MDMF, Bell-202 FSK, played as USB
  audio in the on-hook gap after a ring. The modem decoded DATE/TIME/NMBR/NAME perfectly — end-to-end
  proof the catalog's spec was right. **No HID opcode at all — pure audio.**
- **DTMF remover** (`reg0x14 bit4`) — dial DTMF on the modem, decode card-1 audio: bit4 clear passes it,
  bit4 set strips it. (Gotcha: +15 dB card-1 capture *clips* DTMF and defeats the decoder — drop the gain.)
- **Digital loopback** (`reg0x40 bit4`) and **tip/ring polarity** (`reg0 bit16`) — both clean and
  reversible; polarity reversal moves `reg0x28[31:16]`, which pinned that field as a real analog
  line/loop measurement (a single-shot read had over-implied it was a live hook readout — the time-series
  corrected that).

**Lesson:** a machine you can script at the far end of the line makes differential RE repeatable — no
"was the human at the phone?" ambiguity, and you can run tight matched A/B captures.

## 10. Dump the flash from Linux

The catalog decoded the P3 (`0x80`/"prwC") flash port byte-for-byte. The old belief was "flash is
updater-mode-gated" — but the **read** primitives (geometry sub-reg `0x10`, page sub-reg `0x13`) turned
out to answer in **normal softphone mode**. So the whole **8 MB flashed out over USB HID** at ~163 KB/s,
read-only — config block, the ZeroCD installer ISO, and a ~2.4 MB **eCos "ArmJack" firmware** with a full
SIP stack. Crucially: **no E-number anywhere in flash**, confirming the identity is network-provisioned.
Write-up: [`captures/flash-dump-analysis.md`](captures/flash-dump-analysis.md).

**Lesson:** a "gated" surface is often only gated for the *dangerous* verbs. Test read separately from
write — reads are usually ungated and get you the firmware.

## 11. Reverse the config crypto (and hit the per-session wall honestly)

With the flash + the macOS `CSJEncryptor`, the SJphone config cipher fell out: `"SJEN"` + **RC4** wrapping
`"SJCF"` + zlib → **UTF-16 INI**. A **fixed app key** (two MD5s over decoy strings, same on every device)
decrypts the local `Profiles.db` — but that's only a bare `SIPProxy` *shell*, **no account**. The real
account lives in the `PatchCache` and the provisioning responses under a **per-device master key** and
**per-request session keys**. Those are runtime-only, so the account is **not statically decryptable** —
its values remain the ones from the pcap. Write-up:
[`captures/provisioning-crypto-RE.md`](captures/provisioning-crypto-RE.md). Decryptors: `sjen_decrypt.py`,
`prov_decrypt.py`, `masterkey_decrypt.py`.

**Lesson:** RE the scheme fully *and* state the wall precisely. "Cipher understood, static keys recovered,
account still per-session-keyed" is a complete result — resist rounding it up to "cracked".

## 12. Unpack the firmware from live RAM

The flash firmware is LZ-packed (file bytes ≠ RAM bytes at 0x0), and no decompressor sits in the readable
boot region — so the packed image can't be disassembled directly. But it runs **decompressed in SDRAM**.
The trick that made this cheap: the SoC-memory port **auto-increments across successive GETs**, so reads
run at ~190 KB/s instead of per-word. `tools/ram_dump.py` pulled the low 16 MB of RAM — the **unpacked,
running eCos image** — and from it `tools/masterkey_decrypt.py` recovered the firmware's two hardcoded RC4
keys. (The account still didn't appear: in USB mode the SIP stack is dormant, so there's nothing to
decrypt live.)

**Lesson:** when flash is packed, dump the *running* image out of RAM. And profile your read primitive —
a hidden auto-increment turned a "hours per MB" scan into a practical full dump.

## 13. Map the mode gate (why the number-owning brain sleeps over USB)

Finally, *why* is the Ethernet/SIP brain dormant on USB? Reading `init_usbd` in the unpacked firmware: at
boot the SoC's UDC/PMU block (`0x90600000`) checks whether a USB host **enumerates** it. If yes → USB mode,
and the Ethernet clock-init is skipped → PHY/MAC never power → DHCP/SIP/provisioning never start. If it
sees wall power / no host → **AC mode**, and it provisions and registers. The decision is **made once at
boot and is reset-based** — no HID command flips it. A **software cut-cable** (uhubctl port power + disable)
was tested and *fails*, because the hub couples power and data and the bare bus reset still trips the gate.
The clean lever is a **physical cut-cable**: continuous VBUS (harmless — detection is reset-based) with
D+/D− held open through boot, so the device boots into AC mode while still USB-powered. See README §10.

**Lesson:** a "mode" you can't reach from software is usually a *boot-time* decision — find where it's
sampled (here, USB enumeration at a specific MMIO block) and you learn exactly which physical signal to
withhold, rather than fuzzing for a command that doesn't exist.

---

## What generalizes

1. **Fingerprint before you poke** — know every interface the device exposes.
2. **Blind fuzzing maps walls, not reasons** — don't over-conclude from a stall.
3. **The driver is the spec** — and vendors often strip only *one* platform's build. Chase the
   readable one.
4. **A round-trip you can verify beats a plausible guess** — reading the CPU's own boot vectors is how
   you *know* you have memory access.
5. **Differential register discovery** — change one real thing, diff the register window — finds
   hardware state with zero disassembly, as long as a human can toggle the thing.
6. **Match the device's native format** — half the integration pain was fighting a 16 kHz vs 8 kHz
   mismatch; going native made it trivial.
7. **Read the whole parts catalog** — once the transport is proven, mine every symbol; the confirmed
   *negatives* (dead stubs) are as valuable as the features.
8. **A scriptable machine at the far end** beats a human toggling the phone — repeatable, matched A/B
   differential RE.
9. **Gates usually guard the dangerous verbs only** — test read separately from write; reads got us the
   whole flash and then the firmware out of RAM.
10. **State the wall precisely** — "cipher fully understood, static keys recovered, account still
    per-session-keyed" is a complete result; don't round it up to "cracked".
11. **When flash is packed, dump the running image from RAM** — and profile the read primitive; a hidden
    GET auto-increment turned an infeasible scan into a full dump.
12. **A mode you can't reach from software is a boot-time decision** — find where it's sampled and you
    learn which physical signal to withhold, instead of fuzzing for a nonexistent command.
