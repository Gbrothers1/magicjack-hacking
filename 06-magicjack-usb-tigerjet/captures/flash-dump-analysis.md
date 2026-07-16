# magicJack USB (TigerJet 06e6:c200) — full SPI-flash dump & analysis

**Dumped 2026-07-16**, 8 MB, from Linux over USB HID in **normal softphone mode** (no updater mode, no
physical access), via the `0x80`/"prwC" flash **page-read** primitive (sub-reg `0x13` =
`_ReadOnePhyPageBulkMemARM`, 2048 B/page): `80 04 13 04 <page-ptr LE> prwC` → `80 14 ..prwC` →
`80 04 08 04 00.. prwC` → 32× GET → `80 14 ..prwC`. **Read-only** (ops 0x04/0x14/0x08; no write/erase).
Ran with `mj-fxs-bridge`/`mj-baresip` stopped (they poll the same HID channel). ~163 KB/s.

This resolves the long-standing open question: **the flash READ surface answers in normal mode** — the
earlier "flash is updater-mode-gated" belief was wrong for reads (geometry sub-reg 0x10 and page sub-reg
0x13 both answer). Writes/erase (0x02/0x12/0x1c/0x11) are still untested and may be gated — do NOT attempt.

Raw artifacts in this dir (gitignored — large): `magicjack-flash-8MB.bin` (+ `.gz`),
`armjack-firmware-region.bin` (the carved ARM firmware), `flash-config-block.txt` (page 0).

## Layout (8 MB, 38% erased 0xFF / 57% data)

| Offset | Pages | Content |
|---|---|---|
| `0x000000` | 0 | **Config block** (device parameters — below) |
| `0x044000`–`0x092000` | 136–293 | **ZeroCD driver image**: ISO9660 (`CD001` @0x4c001) + Windows PE + the SJphone/`talk4free` `Autorun2` (PDB path branch `iso_for_go_2_911`), magicJack LP code-signing, Apple plist |
| `0x095000`–`0x2e8000` | 298–1491 | Mixed: ARM code + an embedded PE (@0x97560) + assorted network/config strings (`2Wire 2700HG-B`, `8.8.8.8`, `AT&T`, DNS IPs — looks like diagnostic/example net data) |
| `0x41b000`–`0x672000` | 2102–3297 | **On-device ARM firmware** (~2.4 MB): starts with `ea00000e` reset branch + `14 f0 9f e5` LDR-pc vectors (matches `arm-mem-dump-0x0.bin`). **eCos-based "ArmJack" SDK** — full SIP stack (`/httpd/v3_0`, `/sipfrag`, `SIPUDPMax`), VoIP engine (`VoipEngineHandler`), A-law/DTMF/ring codecs, BOOTP. This is the telephony "brain" firmware. |
| `0x7a0000` | 3904 | **`MJSF` signed provisioning blob** (magic `4d 4a 53 46`, size `0x144a`, MD5 `b46db17e…`) — the MJSF container (MD5-only, no HMAC → forgeable, per the feature catalog) |

## Config block (page 0)

```
sn=<device-serial>            serial (matches USB iSerialNumber)
mf=TigerJet Network, Inc.
USB Internet Phone by TigerJet
vd=                          (empty)
ps=magicJackVideo            product/platform string
mac=l3   fs=1   slic=1   ar=0   pr=1   bd=1
hash=                        (empty)
```

**No E-number (<E-number>) anywhere in the flash** → the SIP identity is **network-provisioned**, not
stored on-chip. Confirms the long-held conclusion; on-chip identity read/spoof remains a dead end.

## What this unlocks

- **Offline RE of the ARM firmware** — `armjack-firmware-region.bin` (2.4 MB) can go into radare2/Ghidra
  and be cross-referenced with the macOS-binary protocol notes (`mac-binary-ARM-protocol.md`); the reset
  vectors already match the live boot dump.
- The ZeroCD ISO can be carved (ISO9660 @0x44000-ish) to recover the shipped Windows/Mac installer.
- The MJSF blob at 0x7a0000 can be unwrapped (`'MJSF'`+size+MD5+data) to inspect provisioning.
