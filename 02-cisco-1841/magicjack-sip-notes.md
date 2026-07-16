# MagicJack ATA — SIP reverse-engineering notes

Captured 2026-07-10 on OpenWrt `br-lan` (post-NAT source `<router-wan-ip>`, the
1841's WAN IP). Device LAN IP `<ata-lan-ip>`. Goal: run our own SIP
registrar/proxy the ATA will talk to. Raw capture: `magicjack-sip.pcap`.

## Identity
- **AOR / SIP user**: `EXXXXXXXXXXXX` (the account/device id)
- **Domain**: `talk4free.com`
- **User-Agent**: `mJ/1.0.1043a.REDACTED-DEVICE-TOKEN`
- **SIP authentication: NONE.** REGISTER gets a `200 OK` directly — no
  `401/407` challenge, no `Authorization` header. Identity is just the username.

## Routes it looks for (DNS + destinations)
- **DNS on boot**: `prov1.talk4free.com` (A) — a **provisioning server**; this is
  where it pulls config (incl. which proxy to use). Also uses `8.8.8.8` (DHCP).
- **SIP registrar/proxy**: `216.234.65.40:5070/UDP` (`proxy01.dca1.talk4free.com`)
- **Media (RTP) servers**: `216.234.65.12`, `.13`, `.34`, `.35` (`vmsNN.dca1.talk4free.com`)
- **No `_sip._udp` SRV / no NAPTR** → hardcoded hostnames/IPs (provisioned), not
  DNS service discovery.
- **Signaling port is UDP 5070** (NOT the default 5060).

## Registration exchange
```
REGISTER sip:talk4free.com SIP/2.0
Via: SIP/2.0/UDP <ata-lan-ip>:38381;branch=z9hG4bK...;rport
From: "unknown" <sip:EXXXXXXXXXXXX@talk4free.com;reboot=4>;tag=...
To:   <sip:EXXXXXXXXXXXX@talk4free.com>
Contact: <sip:EXXXXXXXXXXXX@<ata-lan-ip>:38381>
CSeq: 1 REGISTER
Max-Forwards: 70
User-Agent: mJ/1.0.1043a...
Supported: path
Content-Length: 0

SIP/2.0 200 OK
Via: ...;received=<public-ip>;rport=8698     <- server learns public IP:port
Contact: <sip:EXXXXXXXXXXXX@<ata-lan-ip>:38381>;expires=1800
To: ...;tag=611b93d1-co1042-INS053
Date: Fri, 10 Jul 2026 22:10:15 GMT
```
- **Expires: 1800s** (re-registers every 30 min). Plus a **2-byte UDP keepalive
  every 5s** to the proxy for NAT hole-punching.
- Note the SIP payload carries the **private** IP `<ata-lan-ip>` (the 1841 has
  no SIP ALG). The server ignores it and replies to the `rport`/`received`
  public address — standard symmetric-NAT handling. **Our server must do the same.**

## Call exchange (outbound)
```
INVITE sip:<dialed-number>@talk4free.com SIP/2.0
From: <sip:EXXXXXXXXXXXX@talk4free.com>;tag=...
To:   <sip:<dialed-number>@talk4free.com>
Contact: <sip:EXXXXXXXXXXXX@<ata-lan-ip>:57483>
Content-Type: application/sdp
Supported: replaces,norefersub,timer

v=0
c=IN IP4 <ata-lan-ip>
m=audio 49152 RTP/AVP 0 8 101 13
a=rtpmap:0 PCMU/8000        (G.711 mu-law)
a=rtpmap:8 PCMA/8000        (G.711 A-law)
a=rtpmap:101 telephone-event/8000
a=fmtp:101 0-16             (DTMF, RFC 2833)
a=rtpmap:13 CN/8000         (comfort noise)
a=ptime:30
a=sendrecv

  <-- 100 Trying
  <-- 183 Session Progress   (early media / ringback)
  <-- 487 Request Terminated (no answer / hangup in this capture)
  --> ACK
```
- **Media**: RTP **G.711 mu-law (PCMU)**, ptime 30ms, device RTP port **49152**.
  Actual media flowed `<router-wan-ip>:49152 <-> 216.234.65.12:20346`.

## Building our own server (Asterisk / FreeSWITCH / Kamailio)
1. **Bind SIP to UDP 5070** (not 5060).
2. **Accept REGISTER for `EXXXXXXXXXXXX` with no auth** (static endpoint), realm
   `talk4free.com` — or make domain matching permissive.
3. **NAT**: reply via `rport`/`received` (comedia / `nat=force_rport,comedia` in
   Asterisk). Don't trust the private IP in Via/Contact/SDP.
4. **Codecs**: `ulaw, alaw, telephone-event, CN`; `ptime 30`.
5. To actually place PSTN calls, add an upstream trunk; SIP↔SIP works out of the box.

## Redirecting the ATA to our server (it uses hardcoded IPs)
**Option A — DNAT (no device change, recommended).** On OpenWrt:
```
# signaling
iptables -t nat -A PREROUTING -p udp -d 216.234.65.40 --dport 5070 \
  -j DNAT --to-destination <OUR_SIP_IP>:5070
# media servers
iptables -t nat -A PREROUTING -p udp -d 216.234.65.0/24 \
  -j DNAT --to-destination <OUR_SIP_IP>
```
(or `ip nat outside source static` on the 1841). Our server must accept the
`talk4free.com` domain in the Request-URI.

**Option B — provisioning spoof / MITM.** The ATA's **only** boot-time DNS lookup is
`prov1.talk4free.com`; it then fetches config over **plaintext HTTP :80**:
```
GET /softphone/provision/?dbkey=<290-byte encrypted blob>&osname=eCOS&rv=6.0&version=20191224049944
Host: prov1.talk4free.com    User-Agent: mJ
-> HTTP 200  text/html  1421 bytes  (encrypted config body)
```
`osname=eCOS` → the device runs the **eCos** RTOS. The 1421-byte response is
**encrypted** (high entropy from byte 0, not 16-byte aligned → stream/RC4-style or
custom XOR, not readable). The SIP proxy + media IPs live *inside* that blob — the
ATA never DNS-resolves them, so **DNS can only redirect provisioning, never the
SIP/media** (hence Option A/DNAT is required for the actual calls). The three tiers:
1. **Observe (now, zero-risk):** transparent logging MITM of `prov1` (we own
   DHCP→DNS) — device keeps working, we capture every request/response. Answers the
   key question: is `dbkey`/response constant per boot (→ replayable) or nonce'd?
2. **Redirect (in use):** DNAT `216.234.65.40:5070` → our Asterisk (crypto-independent).
3. **Forge (future/hard):** break the config cipher — firmware extraction of the eCos
   image, or known-plaintext/keystream-reuse against fields we already know
   (`216.234.65.40`, `EXXXXXXXXXXXX`, `talk4free.com`, `5070`) — to make the device
   natively provision to our proxy.

## Provisioning cipher — analysis & verdict (2026-07-13): UNCRACKABLE software-only

We attempted to break the encrypted provisioning profile. Captured two additional
provisioning responses via a power-cycle (`magicjack-provisioning-2.pcap`) to go with
the original (`magicjack-provisioning.pcap`), giving **3 ciphertexts** (response bodies
1421 / 1427 / 1428 bytes) each with its `dbkey` request blob (290 / 290 / 291 bytes).

**Single-ciphertext attacks — all failed:**
- Body length is **not a multiple of 16** → not AES-ECB/CBC (block cipher); it's a
  **stream cipher** (RC4 / CTR / ChaCha-class) or custom.
- **Index of coincidence ≈ 0.00387** (random-256 ≈ 0.0039) and **no repeated blocks**
  → rules out short repeating-key XOR (crib-dragging `talk4free.com`, `216.234.65.40`,
  `EXXXXXXXXXXXX`, `5070`, `ProxyPort`, etc. yields nothing structural).
- **RC4 dictionary** (serial, MAC in every format, `version`, `dbkey`, `talk4free.com`,
  `magicjack`, `ymax`, …) → no hit.
- **14,880 derived keys** — MD5/SHA1/SHA256 (raw + hex, truncated 5/8/16 B) of the
  serial / MAC / version / `dbkey`, both orders, mixed with candidate nonces
  (`resp[:4/8/12/16]`, `dbkey[:8/16]`) → no hit.
- **Short-key brute** (1–3 byte keys exhausted; 4-byte ≈ 10¹² ops, impractical) → best
  printable score 45/64 = noise (real config would score ~62–64/64).
- Not zlib / gzip / raw-deflate (± header skip).

**Multi-ciphertext (keystream-reuse / two-time-pad) attack — failed:**
- The **`dbkey` changes on every request** (carries a nonce/counter — it is NOT a
  static per-device token).
- `C_i XOR C_j` across all 3 responses is **random** (≤1.05% zero bytes, longest zero
  run = 2, no ±8 alignment shift improves it). If the keystream were reused, two
  near-identical profiles would XOR to long runs of zeros. They don't → **fresh
  keystream per request** (per-request nonce/key). Two-time-pad attack is impossible.

**Verdict:** the provisioning crypto is competently **per-request-keyed**, and the key
folds in a constant that exists only in the eCos firmware image. There is **no
software-only path** to decrypt or forge the profile. The only remaining route is
**firmware extraction** (hardware teardown — UART/flash dump), which is out of scope.
We don't need it: **SIP DNAT (`ip nat outside source static` on the 1841) already
re-points the device** — the cipher crack was only ever for *native* re-provisioning.

**Provisioning intel refresh (2026-07-13):** `prov1.talk4free.com` → CNAME
`waf-east-2.talk4free.com` → rotating AWS (`3.208.195.193`, `34.199.24.87`,
`3.220.208.119`, `3.224.2.204`); `HTTP/1.0` GET, response `text/html` ~1.4 KB
(encrypted). Device now resolves via **`1.1.1.1`** (was `8.8.8.8`). Firmware version
still `20191224049944` — **no OTA fired on reboot and no separate upgrade/firmware host
was contacted**, so an OTA image endpoint remains unobserved. The power-cycle did **not**
break the redirect: the ATA re-registered `Avail` on our Asterisk
(`EXXXXXXXXXXXX@<router-wan-ip>`).
