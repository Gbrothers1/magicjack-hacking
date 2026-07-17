# The redirect (the "hijack"): pointing the magicJack ATA at your own PBX

**Goal:** make the networked magicJack register to *your* SIP server (e.g. Asterisk)
instead of magicJack's cloud — **without touching the device**. It's a pure network
change, done on whatever router the magicJack sits behind, and it's instantly
reversible.

This guide is **router-agnostic**. The idea is the same on every router; only the exact
command differs. Read [The idea](#the-idea-one-picture) and [What you must
do](#what-you-must-do-two-rules-on-any-router) first — those are universal — then copy
the one recipe that matches the router you actually have (or translate the two rules into
its config yourself).

---

## The idea (one picture)

The magicJack ATA always sends its SIP to **one hardcoded address:
`216.234.65.40:5070/UDP`** (magicJack's proxy). It never looks that address up in DNS —
the IP is baked into its encrypted provisioning profile — so you **cannot** redirect it
with DNS or `/etc/hosts` tricks. You redirect it by making the router **rewrite the
destination** of those packets to your PBX.

```
BEFORE:  [magicJack ATA] ──SIP to 216.234.65.40:5070──►[ router ]──► magicJack cloud

AFTER:   [magicJack ATA] ──SIP to 216.234.65.40:5070──►[ router ]
                                                          │ rewrites destination
                                                          ▼
                                                    [ your PBX ]  ◄── registers here now
```

The ATA is none the wiser: it still *thinks* it's talking to `216.234.65.40`. The router
quietly swaps the destination on every packet (and swaps it back on the replies).

---

## Why this is all it takes (three facts)

These come from packet-capturing a real ATA (full notes:
[`../02-cisco-1841/magicjack-sip-notes.md`](../02-cisco-1841/magicjack-sip-notes.md)).
They're what make a plain destination-rewrite sufficient:

1. **Hardcoded proxy, no DNS.** The SIP proxy IP lives inside the ATA's encrypted
   provisioning blob; the ATA never DNS-resolves it. → You must rewrite at the **IP
   layer** (destination NAT). DNS spoofing can't catch the calls.
2. **No authentication.** magicJack's proxy accepts `REGISTER` with just a username and
   **no password**. → Your PBX needs no shared secret — it just has to accept that
   username and recognize the device (by source IP). See
   [Point your PBX at the device](#3-point-your-pbx-at-the-device).
3. **Signaling is UDP 5070, media is plain G.711.** SIP is on **5070**, not the usual
   5060. **Media (RTP) needs no redirect at all** as long as your PBX is the media
   endpoint (`direct_media=no` in Asterisk) — the PBX hands the ATA its own address for
   audio, so the RTP just follows the SIP. You only ever redirect port **5070**.

---

## What you must do (two rules, on any router)

Every recipe below is just these two rules expressed in one router's syntax. If your
router isn't listed, translate *these* — they're the whole job.

### Rule 1 — Destination rewrite (always required)

> For UDP packets headed to `216.234.65.40:5070`, change the destination to
> `YOUR_PBX_IP:5070`.

This is **destination NAT** (a.k.a. DNAT / "port forward" / `ip nat outside source`).
Every router that can NAT can do this.

### Rule 2 — Keep the reply path symmetric (required *only sometimes*)

The ATA accepts a reply **only if it appears to come back from `216.234.65.40`** — the
address it sent to. Whether you need to do anything about this depends on **where your PBX
sits relative to the ATA**, not on your router brand:

| Your topology | Return path | Rule 2? |
|---|---|---|
| **PBX is across the router** (different subnet, or reached via the router's WAN/uplink) | Replies naturally route back *through* the router, which un-rewrites them. Symmetric for free. | **Not needed** — Rule 1 alone works. |
| **PBX is on the same LAN/subnet as the ATA** | After Rule 1 the PBX would answer the ATA *directly* — source `PBX_IP`, not `216.234.65.40` — and the ATA drops it. | **Required.** Also rewrite the **source** of the redirected packets (masquerade / SNAT) so replies detour back through the router. This is the classic **NAT-hairpin** problem. |

**Rule of thumb:** *ATA and PBX on the same LAN → add source NAT. PBX on the far side of
the router → destination rewrite alone is enough.*

That's the entire theory. The rest is copy-paste.

---

## Recipes

Set these first (used by every recipe):

| Placeholder | Meaning | Example |
|---|---|---|
| `PBX_IP` | your SIP server (Asterisk) | `10.0.0.5` |
| `MJ_PROXY` | magicJack's proxy — **do not change** | `216.234.65.40` |
| `ROUTER_LAN_IP` | the router's own LAN address (only for the same-LAN / masquerade case) | `10.0.0.1` |

> In each recipe, the **first** rule is Rule 1 (always). The **second** rule is Rule 2 —
> include it **only if** your PBX shares a LAN with the ATA (see the table above);
> omit it if the PBX is across the router.

### A. Linux gateway — `iptables`

```bash
# Rule 1: redirect the ATA's SIP to your PBX
iptables -t nat -A PREROUTING -p udp -d 216.234.65.40 --dport 5070 \
  -j DNAT --to-destination PBX_IP:5070

# Rule 2 (same-LAN only): make the reply return via this router
iptables -t nat -A POSTROUTING -p udp -d PBX_IP --dport 5070 \
  -j MASQUERADE
```

Make sure forwarding is on: `sysctl -w net.ipv4.ip_forward=1`.
Persist with `iptables-save` / your distro's mechanism (these rules are otherwise lost on
reboot).

### B. Linux gateway — `nftables`

```bash
nft add table ip mjnat
nft 'add chain ip mjnat prerouting  { type nat hook prerouting  priority dstnat; }'
nft 'add chain ip mjnat postrouting { type nat hook postrouting priority srcnat; }'

# Rule 1
nft add rule ip mjnat prerouting  udp dport 5070 ip daddr 216.234.65.40 dnat to PBX_IP:5070

# Rule 2 (same-LAN only)
nft add rule ip mjnat postrouting udp dport 5070 ip daddr PBX_IP masquerade
```

### C. OpenWrt (fw4 / nftables) — scripts included

OpenWrt's firewall *is* nftables, in the `inet fw4` table. This repo ships ready-to-run
scripts for exactly this — they SSH to the gateway and add both rules (the lab's PBX
shares a subnet with the ATA, so Rule 2 is included):

- **Go live:** [`cutover.sh`](cutover.sh) — adds the `dstnat` (Rule 1) and `srcnat`
  masquerade (Rule 2) rules.
- **Revert:** [`rollback.sh`](rollback.sh) — deletes them.

```bash
# edit GW / SERVER at the top (or pass as env vars), then:
GW=192.168.1.1 SERVER=PBX_IP ./cutover.sh
```

These rules are **transient** — a firewall reload or reboot clears them. To make it
permanent, add the equivalent under `/etc/config/firewall` (a `redirect` section for Rule
1) or a startup script.

### D. Cisco IOS (this lab's edge router, a 1841)

On Cisco, a single **`ip nat outside source static`** line does Rule 1, and because the
router already PATs the ATA's traffic outbound, the return path is symmetric — **no
separate Rule 2 needed**. It saves to NVRAM, so it survives reboots.

```
conf t
 ip nat outside source static PBX_IP 216.234.65.40
end
clear ip nat translation *          ! force a clean re-register
show ip nat translations            ! verify
write memory                        ! persist across reboots
```

Cisco reads this as *outside-global `PBX_IP` ↔ outside-local `216.234.65.40`*: the ATA
still believes it's talking to magicJack, and the router swaps the destination on every
packet. Revert: `no ip nat outside source static PBX_IP 216.234.65.40`.

> This is the redirect the live lab runs on — durable, one line, no host dependency. Full
> write-up in [the README](README.md#redirect--permanent-line-on-the-1841).

### E. pfSense / OPNsense (GUI)

- **Rule 1** — *Firewall → NAT → Port Forward*, on the interface facing the ATA:
  - Protocol **UDP**, Destination **`216.234.65.40`**, Destination port **`5070`**
  - Redirect target IP **`PBX_IP`**, target port **`5070`**
- **Rule 2 (same-LAN only)** — enable **NAT reflection** on that port-forward
  (*Advanced → NAT Reflection: Enable (NAT + Proxy)*), or add a matching **Outbound NAT**
  rule so the redirected traffic is source-NATed to the router. If your PBX is on a
  different interface/subnet, skip this.

### Any other router

Express the two rules in its config: a **UDP port-forward / DNAT** sending
`216.234.65.40:5070` → `PBX_IP:5070` (Rule 1), plus — **only if** the PBX shares the ATA's
LAN — **source NAT / NAT-reflection / hairpin NAT** on that flow (Rule 2). If your router
can't DNAT by *destination IP* (some consumer firmware only forwards inbound ports), put a
small Linux box (recipe A/B) inline as the ATA's gateway and do it there.

---

## 3. Point your PBX at the device

Because the ATA sends no password (fact #2), your PBX identifies it **by source IP**. In
Asterisk `pjsip.conf`, the `type=identify` `match=` must be the IP the ATA's SIP *appears
to come from* after the redirect — and that depends on whether you used Rule 2:

- **You added Rule 2 (masquerade / same-LAN):** the SIP arrives **from the router's LAN
  IP** → `match=ROUTER_LAN_IP`.
- **Rule 1 only (PBX across the router):** it arrives from the **ATA's own post-NAT
  address** → `match=` that IP.

The shipped [`pjsip.conf`](pjsip.conf) has both lines with one commented — pick the one
matching your setup. Everything else in that endpoint (no auth, `ulaw,alaw`,
`rtp_symmetric=yes`, `force_rport=yes`, `direct_media=no`) is already correct for the ATA.

---

## Verify

```bash
sudo asterisk -rx 'pjsip show contacts'     # expect the ATA's AOR (EXXXXXXXXXXXX) with a contact
sudo asterisk -rx 'pjsip show endpoints'    # its endpoint should be "Avail"
```

The ATA re-registers on its own every ~30 min (and sends a keepalive every 5 s). To not
wait: **power-cycle the ATA** — it re-registers within ~20–30 s. Then dial its extension
(200 in this lab's dialplan) and the attached phone should ring.

## Roll back

Undo whichever rule(s) you added — the ATA goes straight back to magicJack's cloud on its
next register. Nothing on the device ever changed.

- iptables: `iptables -t nat -D …` the two rules (or flush the chain).
- nftables: `nft delete table ip mjnat`.
- OpenWrt: [`rollback.sh`](rollback.sh) (or reload the firewall).
- Cisco: `no ip nat outside source static PBX_IP 216.234.65.40` + `clear ip nat translation *`.
- pfSense/OPNsense: disable the port-forward.

## Troubleshoot

| Symptom | Likely cause | Fix |
|---|---|---|
| ATA never appears on the PBX | Rule 1 not matching | Confirm you're rewriting **UDP dest `216.234.65.40` port `5070`** (not 5060), on the interface the ATA's traffic actually traverses. `tcpdump -ni <if> udp port 5070`. |
| PBX sees the `REGISTER` but the ATA keeps retrying / shows unregistered | Missing Rule 2 (asymmetric reply) | PBX and ATA share a LAN → add the source-NAT/masquerade rule so replies return via the router. |
| Registered, but `pjsip show endpoints` says the ATA is unknown/rejected | `match=` IP wrong | Set `match=` to the source IP the SIP actually arrives from (router LAN IP *with* Rule 2; ATA's post-NAT IP *without*). Check with `pjsip set logger on`. |
| One-way or no audio on calls | RTP/NAT, not the redirect | Ensure `direct_media=no` and `rtp_symmetric=yes`; for calls leaving your LAN set `external_media_address` in `pjsip.conf`. The redirect itself never carries media. |
| Worked, then silently reverted after a while | Transient rules cleared (conntrack flush / firewall reload / reboot) | Persist the rules (NVRAM on Cisco, `iptables-save`/uci/config on Linux/OpenWrt). Power-cycle the ATA to force a fresh register. |

---

## Is this attacking magicJack? — No.

This rewrites **your own device's packets on your own router**. Nothing is sent to,
forged toward, or probed against magicJack's servers; boot-time provisioning is left
untouched. All magicJack's side ever sees is your ATA **stop registering** —
indistinguishable from unplugging it. See
[README → "Does this interfere with MagicJack's servers?"](README.md#does-this-interfere-with-magicjacks-servers--no).
