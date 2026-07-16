# magicJack provisioning & config crypto — reverse-engineering synthesis

**Goal:** decrypt the device's stored SIP config from the flash dump and compare it to the provisioning
we captured on the wire (the "hijack" pcaps in `../../02-cisco-1841/`), to fully understand how the
magicJack is provisioned. Work done 2026-07-16 from the 8 MB flash dump + the macOS softphone binaries.
Legitimate interop RE of the user's own device.

## TL;DR

- The SJphone config crypto is **fully reverse-engineered**: `"SJEN"` + RC4(key) wrapping `"SJCF"` +
  `uint32_BE(len)` + **zlib** → UTF-16LE INI. (Confirmed in `mj.so`/`magicJack` `CSJEncryptor`, and
  demonstrated on real device data.)
- The device's local **`Profiles.db` is DECRYPTED** with a **fixed "app key"** — but it is only a
  **profile shell** (`Type=SIPProxy`, `Name=magicJack`), with **no account credentials**.
- The **provisioned account** (`sip:<E-number>@talk4free.com`, proxy `216.234.65.40:5070`, no auth)
  is **not stored in plaintext or under the app key**. It lives in the `PatchCache` and in the
  provisioning HTTP responses, both encrypted under a **per-device / firmware master key** that is
  **locked behind the firmware's opaque LZ flash→RAM compression** and was not recoverable statically or
  from live RAM. So the account values we have are exactly the ones observed in the pcap.

## The cipher (CONFIRMED)

`CSJEncryptor` (RTTI `12CSJEncryptor`, macOS vtable `0x100a92560`) dispatches on a mode field: **0 = RC4**
(the config path), 1/2 = AES (unused for config). Framing:

```
outer:  "SJEN" ‖ RC4(key, inner)                       # raw-key RC4, no IV/salt; ciphertext at offset 4
inner:  "SJCF" ‖ uint32_BE(uncompressed_len) ‖ zlib     # zlib 1.2.11 (78 9c), NOT a custom codec
        → inflate → UTF-16LE (BOM ff fe) INI text
```

Decryptors: **`tools/sjen_decrypt.py`** (generic, takes `--key`/`--key-hex`/`--key-file`) and
**`tools/prov_decrypt.py`** (bakes in the app key, decrypts the flash `Profiles.db`, documents the tiers).

## The three crypto tiers (this is the key insight)

| Data | Where | Key | Status |
|---|---|---|---|
| **`Profiles.db`** (profile shell) | flash MJSF `0x7a0045` | **App key** (fixed, all devices): `MD5(decoy_set_A) ‖ MD5(decoy_set_B)` = `1a909c8e737d3977614c278067f4aa32d60ae46bf862661aa41dbe4f340ed5dd` | ✅ **DECRYPTED** |
| **`PatchCache`** (the account patch) | nested MJSF `0x7a0136` (`patchcache-inner.bin`) | **eCOS firmware master key** (per-device) | ❌ blocked |
| **Provisioning HTTP responses** | wire (`provisioning/prov*_resp*.bin`) | **per-request RC4 session key**, itself carried in `?dbkey=` encrypted under the firmware master key | ❌ blocked |

The "app key" is a static obfuscation key (two MD5s over hardcoded decoy/log strings in the binary) — the
same for every device, which is why it opens the generic profile shell. The **master key** is the
per-device secret; it protects everything account-specific.

## Decrypted `Profiles.db` (the app-key layer)

`captures/mjsf-profiles-db.bin` (227 B, encrypted) → `captures/mjsf-profiles-db.decrypted`:

```ini
[225633EC-0F49-4E5A-B279-8CD8C6772A4A]
SavePersonalData = 1
Type = SIPProxy
FileName = resid:PROFILES/MAGICJACK
Name = magicJack
CustomField0/2/8/12/19 = "" / 1
```

A bare `SIPProxy` profile named "magicJack" pointing at an internal resource `resid:PROFILES/MAGICJACK`.
**No proxy URI, AOR, username, password, realm, or E-number.** The GUID `225633EC-…` is the product/
registration profile id (also in the firmware SIP-config template).

## The provisioning flow (from the firmware RE + the pcaps)

1. Boot → DHCP/DNS; the device's **only** boot-time lookup is `prov1.talk4free.com` (also `srv1.mj.gy`
   over HTTPS with a full CA bundle in-firmware).
2. **HTTP GET** `prov1.talk4free.com/softphone/provision/?dbkey=<base64(RC4-encrypted, ~155 B)>&osname=eCOS&rv=6.0&version=20191224049944`.
   The `dbkey` carries a **per-request RC4 session key**, encrypted under the firmware master key.
   (`provisioning/dbkey0..2.bin` — three captures share no common prefix → per-request.)
3. Server → **~1421 B encrypted body** = `RC4(session_key, "SJCF"+zlib(config INI))`
   (`provisioning/prov1_resp0.bin` etc.; non-16-aligned lengths rule out AES).
4. The decrypted config carries the account + the proxy/media IPs; the device merges it (as a `PatchCache`
   patch over the `Profiles.db` shell) and **REGISTERs** to the proxy.

Observed on the wire (`02-cisco-1841/magicjack-sip-notes.md`):
`REGISTER sip:talk4free.com` · From/To `sip:<E-number>@talk4free.com` · proxy **`216.234.65.40:5070/UDP`** ·
**no auth** (direct `200 OK`) · media `216.234.65.12/13/34/35` · Expires 1800 + 2-byte/5 s keepalive.

## Comparison / conclusion

The decrypted local config **matches and explains** the observed provisioning:
- Local `Profiles.db` = the SJphone **`SIPProxy` shell** ("magicJack", `resid:PROFILES/MAGICJACK`) — the
  frame the account is merged into. It intentionally holds **no credentials**.
- The **account** (`<E-number>`, `216.234.65.40:5070`, `talk4free.com`, no auth) is **network-provisioned**
  into the `PatchCache`/via the server response — confirming, from the authoritative on-device source, the
  long-standing conclusion that the E-number/identity is not on-chip in the clear.
- So "how it's provisioned": a fixed profile shell (app-key) + a per-device account patch fetched from
  `prov1` and stored in `PatchCache`, all under the SJEN/RC4/zlib scheme, with the account patch protected
  by the per-device firmware master key.

## What's blocked, and the only remaining paths to the master key

The firmware master key (→ `PatchCache` + the provisioning session key) is **not recoverable with the
current artifacts**: the eCOS app is **LZ-packed in flash and decompressed to SDRAM at boot** (live reads
prove file ≠ RAM: RAM `0x0 = 12 00 00 ea` vs flash `0e 00 00 ea`), no decompressor is present in the
readable boot region, and there is no uncompressed app copy anywhere in the 8 MB flash. A blind live-RAM
scan is infeasible (~2 ms/word HID reads → hours for MBs) and in USB-peripheral mode the SIP/provisioning
stack is likely dormant. Remaining options, all harder / out of scope for static work:
- **Capture the `DBKEY INI:` log** from the running device (firmware console / the embedded httpd) — one
  line yields the master key, then `sjen_decrypt.py` finishes it.
- **Reverse the flash→RAM LZ decompressor** (likely a first-stage/ROM loader outside the two carved banks),
  then unpack offline and recover the `MD5-of-decoy-strings` master-key derivation statically.
- Note: even with the master key, *forging* provisioning to a self-hosted proxy is hard — the transport
  also involves EC/ECDH + per-request session keys (server-side). The proven redirect path (DNAT
  `216.234.65.40:5070` → our Asterisk, per `magicjack-sip-notes.md` Option A) remains the practical route.

## Update (2026-07-16) — firmware keys recovered from a live RAM dump of the *unpacked* firmware

The flash firmware is LZ-packed, but it runs **decompressed in SDRAM**. Using the SoC-memory port
(`tj_armmem` cmd 0x80/0x0e, which **auto-increments across successive GETs** → ~190 KB/s), we dumped the
live low 16 MB of RAM (`captures/ram-0-16M.bin`) — i.e. the **unpacked, running eCOS firmware**, which the
static flash image never exposed. RE of that image (`tools/masterkey_decrypt.py`) yielded:

- **The config cipher & RC4 primitive are confirmed in the firmware** (RC4 KSA @`0xe4ef4`; SJEN codec key =
  3rd argument; `"SJEN"`+RC4 → `"SJCF"`+u32_BE(len)+zlib → UTF-16 INI) — identical to the macOS app.
- **Two hardcoded firmware RC4 keys recovered** (device-independent, stored as base64 rodata, extracted by
  vaddr from the RAM image):
  - `magicJack.Patch` config-patch key = 200 B `e0e7060e4c754dac…` (@`0x36a103`; passed as the RC4 key on
    the `magicJack.Patch` channel — evidence `0x1a8314`→`0x1a6de0`).
  - `dbkey`/provisioning key = 201 B `8a365901b0f16f7b…` (@`0x36a589`; used by CProvisioning `0x1a9430`).
- **The firmware key is NOT `MD5(concat(decoy))`** (that was the macOS app's obfuscation) — MD5 in the
  firmware is only HTTP/SIP **Digest auth**. The keys are just hardcoded blobs.

### Why the account still can't be decrypted (the real, final wall)
- The **captured provisioning** (`dbkey*.bin` / `prov*_resp*.bin`) is **per-session keyed**: the client
  builds `dbkey = RC4(dbkey_key, UTF16(INI + ";%d%d%d%d%d" 5-digit random nonce))` (RNG `0x1a61a8`), and the
  server reply is encrypted under the resulting **session** key. The nonce + runtime session fields
  (`token`/`uuid`/`space`) are ephemeral and are **not** in any static snapshot.
- The on-device **`PatchCache`** account patch (`patchcache-inner.bin`, value head `381498e6…`) does **not**
  decrypt with any recovered key (app / `magicJack.Patch` / `dbkey`) → it too is under a per-session key from
  when it was provisioned.
- In USB-peripheral mode the SIP/provisioning stack is **dormant**: the 16 MB RAM has **no** decrypted
  account (`<E-number>`/`216.234.65.40` absent) and the `DBKEY INI:` log never fires (only the format
  string is resident). So neither the session key nor the plaintext account is available statically.

**Net:** the crypto is fully understood and every *static* key is recovered; the account is protected by a
**runtime per-session key** and is only obtainable by capturing it live *during a provisioning boot*
(device in Ethernet/ATA mode) — e.g. the `DBKEY INI:` UART log, or a RAM dump taken while it's registered.
The account values themselves are already known from the pcap. Artifacts: `captures/ram-0-16M.bin` (unpacked
firmware image), `tools/masterkey_decrypt.py`, `tools/ram_scan.py`/`ram_dump.py`.

## Artifacts
- `tools/sjen_decrypt.py`, `tools/prov_decrypt.py` — the decryptors.
- `captures/mjsf-payload.bin` (MJSF store), `mjsf-profiles-db.bin` (enc) / `.decrypted`, `patchcache-inner.bin`.
- `captures/provisioning/` — `dbkey{0,1,2}.bin`, `prov{1,2}_resp*.bin` (extracted from the 02-cisco-1841 pcaps).
