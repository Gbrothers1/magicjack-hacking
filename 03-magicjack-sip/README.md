# 03 — MagicJack self-hosted SIP server

MagicJack's cloud has been **replaced with our own Asterisk PBX** that the ATA
registers to. The device is redirected at the network layer (no firmware change),
and the PBX now runs a full auto-attendant, internal extensions, radio-scanner
and SDR feeds, a dial-up BBS, voicemail, and outbound PSTN via a Steam Deck
cellular trunk. Reverse-engineering details behind the redirect:
`../02-cisco-1841/magicjack-sip-notes.md` (+ pcaps).

```
MagicJack ATA ──SIP :5070, no-auth, G.711──► Asterisk (pbx-host, <pbx-ip>)
                                                 │
      softphones (6001, steamdeck, thomas) ──────┤
      USB-FXS handset (mjusb / baresip) ─ ext 200 ┤
                                                 ├──► internal extensions / IVR
                                                 ├──► scanners + local SDR (Icecast)
                                                 ├──► dial-up BBS (app_softmodem)
                                                 └──► PSTN out ─► Steam Deck cellular (oFono HFP)
```

## Status — LIVE

- **Asterisk 20** on this host (`pbx-host`, `<pbx-ip>`), running as a service.
- The MagicJack ATA is **redirected to us and registered** (`EXXXXXXXXXXXX`,
  `Avail`). The redirect is a **permanent NAT line on the Cisco 1841** (see
  "Redirect" below) — durable across reboots, no OpenWrt dependency.
- **Outbound PSTN works today** via a **cellular trunk** (a phone's cellular voice
  bridged over Bluetooth HFP into Asterisk). A traditional SIP DID trunk (VoIP.ms) is
  **blocked** — see "PSTN" below.
- Config lives here and is installed to `/etc/asterisk` by `deploy.sh`.

## SIP endpoints (`pjsip.conf`)

UDP transport on **5070**. Seven endpoints:

| Endpoint | What it is | Notes |
|---|---|---|
| `EXXXXXXXXXXXX` | the MagicJack ATA | **no auth**, matched by source IP, G.711, comedia NAT |
| `mjusb` | USB-FXS handset via baresip | the reverse-engineered TigerJet handset on **ext 200** — see `README-fxs-usb.md` |
| `6001` | softphone (Tailscale) | |
| `steamdeck` | Steam Deck cellular trunk | outbound PSTN path (oFono HFP → cellular) |
| `thomas` | Thomas's softphone (Tailscale) | ext 203, mailbox `thomas` |
| `twilio` | Twilio Elastic SIP trunk | |
| `voipms` | VoIP.ms trunk stub | **not registered** — DID blocked (see PSTN) |

> ℹ️ In this **public mirror** every credential in `pjsip.conf` (voipms / twilio / steamdeck / mjusb /
> thomas) is a placeholder — `CHANGEME_*`, `YOUR_*`. Fill in your own trunk/endpoint credentials to
> use it. (In the private working repo these are the real values, kept on a private Gitea remote.)

## Dialplan (`extensions.conf`) — rebuilt as an IVR auto-attendant

Rebuilt 2026-07-14 around an **IVR** plus reusable GoSub subroutines
(`sub-stream`, `sub-dialvm`, `sub-bbs`). All legacy numbers still dial directly;
**dial `0`** for the voice menu.

- **`0`** → main voice menu (`[ivr-main]`): 1=scanners, 2=people, 3=BBS, 4=NOAA WX,
  9=echo test, 0=operator (rings the ATA).
  > `#` is included in the dialplan but **softphones can't send it** (it's their
  > dial/send key), so the menu entry point is `0`, not `#`.
- **People / voicemail:** `200` = the magicJack line (now the **USB-FXS handset**
  `mjusb`, falls back to the network ATA + voicemail), `201`/`6001` = softphone,
  `203` = Thomas, `2000` = old ATA-only behaviour (kept for rollback). `*97` =
  retrieve voicemail.
- **Scanners (Elko County):** `89801` = Broadcastify feed 8543; `89802`–`89804` =
  **local SDR** on this host (NESDR/RTL2838 → `rtl_airband` → Icecast :8000 mounts
  `elko-county` / `elko-so-tac` / `elko-so-car`). `89805` = NOAA WX WXL28 162.550 —
  **needs a 2nd RTL-SDR** (out of the single dongle's 154.4 MHz passband); staged in
  `rtl_airband-noaa-2nd-dongle.snippet.conf`, plays an "unavailable" notice until
  the mount exists. Needs `mpg123` (app_mp3 backend).
- **Dial-up BBS (`app_softmodem`):** `500` V22bis, `501` V22, `502` V23, `503`
  Bell103 (tuned for the low-output USB SLIC), `504` V21, `507` Bell202, plus tuned
  experiments `505`/`506`/`509`. Backend BBS on `127.0.0.1:2323`.
- **Test:** `100` echo, `101` milliwatt, `102` congrats. **`508`** = ChanSpy on the
  MagicJack line (listen-in).
- **Outbound PSTN:** `_NXXNXXXXXX` / `_1NXXNXXXXXX` → the **Steam Deck cellular**
  trunk (`cell-dial.sh` originates the cellular call, then `Dial(PJSIP/steamdeck)`).
- **Inbound PSTN** (`[from-pstn]`) → the IVR.

**IVR prompts are flite TTS WAVs** that must be installed into the *resolved* `en`
voice-pack dir (`"$(readlink -f /usr/share/asterisk/sounds/en)/custom/"`), **not**
`/var/lib/asterisk/sounds`, or the menu is silent. Regenerate/install with
`make-ivr-prompts.sh` (needs `flite`+`ffmpeg`); WAVs archived in `ivr-prompts/`.
`deploy.sh` does **not** handle prompts — that's a separate step.

## Deploy

1. `./deploy.sh` — installs `pjsip.conf` / `extensions.conf` / `rtp.conf` /
   `voicemail.conf` to `/etc/asterisk` and reloads.
   > `deploy.sh` **full-overwrites** those four files. When you've only touched one
   > endpoint/extension live, `sudo install` just that file and reload — don't run
   > the full deploy unless you mean to replace everything. `/etc/asterisk` is backed
   > up to `backups/` (git-ignored) before changes.
2. Install IVR prompts once: `./make-ivr-prompts.sh`.
3. Verify: `sudo asterisk -rx 'pjsip show contacts'` → `EXXXXXXXXXXXX` (and `mjusb`)
   should show `Avail`.

## How the redirect works (full data path)

> **Just want to set it up on your own router?** → **[`REDIRECT.md`](REDIRECT.md)** is a
> router-agnostic, copy-paste guide (iptables, nftables, OpenWrt, Cisco IOS,
> pfSense/OPNsense, or any router — it comes down to two rules). The sections below are
> the background for *why* it works.

The MagicJack ATA is an ordinary SIP device that just happens to ship pointed at
MagicJack's cloud. We move it onto our own PBX with a pure **network redirect** —
the device firmware is never touched, reflashed, or modified.

1. **Normally:** the ATA registers to MagicJack's SIP proxy
   `216.234.65.40:5070` (`proxy01.dca1.talk4free.com`) and its calls/media go to
   MagicJack's media servers. It learned that proxy IP from provisioning, so it's
   effectively hardcoded (DNS spoofing won't catch it).
2. **The intercept:** the **Cisco 1841** rewrites the destination of the ATA's SIP
   packets from `216.234.65.40:5070` to our Asterisk (`ip nat outside source
   static` — see "Redirect" below). The 1841 already PATs the ATA's source to its
   WAN `<router-wan-ip>`, so the return path is symmetric for free.
3. **Registration:** the ATA, none the wiser, sends its `REGISTER` to our
   Asterisk. MagicJack's own proxy never required authentication, so ours doesn't
   either — Asterisk identifies the device by source IP and accepts it.
4. **Calls:** the ATA's `INVITE` lands on Asterisk, which is a **B2BUA** — it
   terminates the call and routes it per our dialplan. **Media (RTP) flows ATA ↔
   Asterisk directly** (Asterisk advertises its own address); MagicJack's media
   servers are never involved.
5. **Reversible:** removing the NAT line puts the ATA straight back on MagicJack's
   cloud. Nothing on the device changed.

## Redirect — permanent line on the 1841

The redirect lives on the **1841 itself** as a single permanent NAT line, saved to
NVRAM (`write memory`). This replaced an earlier, fragile OpenWrt conntrack
"zombie" (a translation kept alive only by the ATA's keepalives, with no rule
behind it — it died on any conntrack flush / ATA-off-3-min and the ATA silently
reverted to MagicJack's cloud).

```
ip nat outside source static <pbx-ip> 216.234.65.40
```

Cisco reads this as outside-global `<pbx-ip>` ↔ outside-local `216.234.65.40`:
the ATA still believes it's talking to MagicJack's proxy, and the router swaps the
destination to our Asterisk on every packet (return traffic swapped back). One line
redirects **additional MagicJacks** too (all hit `216.234.65.40`; Asterisk tells
them apart by AOR — each needs its own endpoint).

The commands (run via the console, `/dev/ttyS0`):
```
conf t
 ip nat outside source static <pbx-ip> 216.234.65.40
end
clear ip nat translation *          ! force a clean re-register
show ip nat translations            ! verify the redirect
write memory                        ! persist across reboots
```
Revert instantly: `no ip nat outside source static <pbx-ip> 216.234.65.40`.

> **Diagnose "redirect down" on the 1841**, not OpenWrt: `show ip nat translations`.
> If the ATA drops off, power-cycle it (forces a fresh REGISTER; comes up `Avail`
> in ~20–25s from `<router-wan-ip>`).

## Provisioning & discovery chain (reverse-engineered)

Capturing the ATA's boot pinned down **exactly one name it looks up** and how it
learns everything else — which is what makes the redirect design above correct:

- **Only DNS query on boot:** `prov1.talk4free.com` (A). Everything else is handed
  down by provisioning, not resolved via DNS.
- **Provisioning is plaintext HTTP on port 80** (no TLS):
  `GET /softphone/provision/?dbkey=<blob>&osname=eCOS&rv=6.0&version=20191224049944`
  (`Host: prov1.talk4free.com`, `User-Agent: mJ`). `osname=eCOS` → the ATA runs the
  **eCos** RTOS. `dbkey` = a **~290-byte encrypted** identity token; the response is
  **~1.4 KB, encrypted** (stream/RC4-style, not readable config).
- **The SIP proxy `216.234.65.40:5070` and RTP media IPs are literal values carried
  inside that encrypted response** — the ATA never DNS-resolves them. So **DNS
  spoofing cannot redirect the calls** (only *provisioning* is DNS-reachable); the
  working redirect must be the IP-layer NAT above.

**Provisioning cipher — UNCRACKABLE software-only (verdict 2026-07-13).** Three
captured ciphertexts (`magicjack-provisioning*.pcap`) settled it: not block-aligned
(stream cipher); IoC ≈ random with no repeated blocks (no repeating-XOR); RC4
dictionary + ~15k hash/KDF-derived keys + short-key brute all miss; and the `dbkey`
**changes every request** (per-request nonce) so `C_i XOR C_j` is random →
**two-time-pad impossible**. The key folds a constant that only exists in the eCos
firmware image; the only remaining path is firmware extraction (hardware teardown,
out of scope). We don't need it — the NAT redirect already re-points the device; the
cipher crack was only ever for *native* re-provisioning. Full analysis in
`../02-cisco-1841/magicjack-sip-notes.md`.

## PSTN — what works and what's blocked

- ✅ **Outbound PSTN works via a cellular trunk** (a real phone's cellular voice
  bridged over Bluetooth HFP into Asterisk). The dialplan's `_NXXNXXXXXX` patterns
  route out through it. Inbound cellular also rings the group (`6001` + ATA + `thomas`).
- ❌ **A traditional SIP DID (VoIP.ms) is blocked:** VoIP.ms — like most ITSPs —
  requires a **government ID** for DID/KYC, which we don't have. The `voipms` trunk
  stub is in `pjsip.conf` with CHANGEME placeholders but is **not registered**.
  No-gov-ID candidates to revisit: JMP.chat/Cheogram, Callcentric free DID.
- **911:** not provisioned. Do **not** rely on this line for emergencies.
- **Why the USR Courier got `NO CARRIER` dialing out via the MagicJack (historical):**
  the MagicJack **subscription had expired** — the `183` early media was MagicJack's
  "account expired" announcement and the `487` was their switch refusing the call.
  A signaling-level refusal, not a media problem — and a core reason for self-hosting.
- **Modem/fax over VoIP** is a separate general limit: G.711 carries voice, but
  modem carrier is jitter/loss-sensitive. What reliably works is **modem-to-modem
  entirely inside this PBX on the local LAN** (the BBS extensions above, ≈V.22/1200
  baud). Also note: dialing an external dial-up number *over the cellular trunk*
  gives `NO CARRIER` — cellular voice + SCO codecs destroy modem carrier; voice only.

## Voicemail (`voicemail.conf`)

- Mailboxes: `200` = Shop Phone (the MagicJack), `6001` = softphone, `thomas` =
  Thomas. Default PIN `1234` — change via `*97`.
- Unanswered calls to a phone extension (20–25s) drop to voicemail.
- Dial **`*97`** from any registered phone to retrieve (enter mailbox + PIN).
- **Voicemail-to-email** is pre-wired (`you@example.com`,
  `serveremail=pbx@pbx-host`) but needs a working mail path on this host — install
  an MTA/SMTP relay (e.g. `msmtp`/`postfix`) so `/usr/sbin/sendmail` can deliver.

## Does this interfere with MagicJack's servers? — No.

This is a device owner redirecting **their own hardware's traffic on their own LAN**.
The redirect happens entirely on your router, on your device's packets. We do **not**
attack, probe, overload, spoof, or send forged traffic to MagicJack's
infrastructure, and nothing impersonates MagicJack *to* MagicJack. **Boot-time
provisioning is deliberately left alone** — on power-up the ATA still fetches its
(encrypted) profile from `prov1.talk4free.com` exactly as designed. The only thing
MagicJack's side observes is your device **stopping its registrations** —
indistinguishable from unplugging the ATA.

Trade-off (not interference): while the redirect is active the ATA is registered to
*you*, not MagicJack — so it won't ring/place calls via MagicJack's network during
that time. The two are mutually exclusive by design; removing the NAT line hands the
device back.

## Related

- `REDIRECT.md` — **router-agnostic setup guide** for the SIP redirect (iptables /
  nftables / OpenWrt / Cisco / pfSense / any router). Start here to do it yourself.
- `README-fxs-usb.md` — the USB-FXS handset (ext 200) wired into this PBX.
- `../06-magicjack-usb-tigerjet/` — the TigerJet USB reverse-engineering behind it.
- `../02-cisco-1841/` — the 1841 router that hosts the redirect.
