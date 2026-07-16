# magicJack USB handset as Asterisk extension 200 (FXS-over-USB)

The reverse-engineered magicJack **USB personality** (TigerJet `06e6:c200`, see
[`../06-magicjack-usb-tigerjet/`](../06-magicjack-usb-tigerjet/)) is wired into this Asterisk PBX as a
real FXS station on **extension 200** — dial tone, keypad dialing, inbound ring, two-way voice, and
hangups both ways — driven entirely from Linux over USB. No Windows/Mac app, no wall power, no mods.

## Architecture

```
  analog phone ──RJ11──> magicJack USB (TigerJet + Si321x ProSLIC)
                              │  USB
        ┌─────────────────────┴───────────────────────┐
        │ audio (ALSA card 1, 8kHz)        control (hidraw feature reports) │
        ▼                                                   ▼
   baresip softphone  ──SIP/RTP──>  Asterisk        mj-fxs-bridge.py (root daemon)
   (native 8k ulaw)    register as   (ext 200)       line power / hook / ring / DTMF
   ctrl_tcp :4444  <───── commands ────────────────  + drives baresip via ctrl_tcp
```

- **Audio** goes through **baresip** (a softphone) using ALSA **card 1** at the device's native
  **8 kHz µ-law** — zero resampling, clean. baresip SIP-registers to Asterisk as endpoint `mjusb`.
  (An earlier `chan_console` attempt is kept in `console.conf` but is **unused** — chan_console is
  hardwired to 16 kHz and the TigerJet captures at 8 kHz only, so it needed lossy resampling.)
- **Line power / hook / ring / DTMF** are pure register I/O over `hidraw` (the RE'd protocol),
  handled by **`mj-fxs-bridge.py`** (runs as root). baresip only does audio; the daemon gives it
  the "FXS" behaviour and controls it (dial/accept/hangup + `listcalls` polling) via ctrl_tcp.

## The TigerJet register map used (all via `tools/tj_linepower.py` in the sibling repo)
| Function | Register | Notes |
|---|---|---|
| Line power on/off | reg0 bit0 | InitTjHardware: reg0\|=1, reg0x38=3, reg0x14 bit7 strobe |
| Ring | reg0 bits 8-9 (`0x300`) | firmware makes the ring voltage; toggle 2s/4s cadence |
| Hook state | reg0x14 **bit31** | on-hook `0x…0114` / off-hook `0x8…0114` |
| DTMF (on-chip decoder) | reg0x14 **byte0x16**=valid, **byte0x17** low nibble=digit | 1-9→1-9, 0→0xA, *→0xB, #→0xC — no audio capture needed |

## Files
| File | Where | Purpose |
|---|---|---|
| `pjsip.conf` `[mjusb]` | `/etc/asterisk/pjsip.conf` | SIP endpoint baresip registers as; ext 200 dials `PJSIP/mjusb` |
| `extensions.conf` ext 200 | `/etc/asterisk/extensions.conf` | `Dial(PJSIP/mjusb,25)` then fall back to the network ATA + voicemail |
| `asound.conf` | `/etc/asound.conf` | `tjfxs` plug PCM (only needed by the unused chan_console path) |
| `console.conf` | `/etc/asterisk/console.conf` | chan_console config — **unused**, kept for reference |
| `mj-fxs-bridge.py` | `/usr/local/bin/` | the FXS bridge daemon (root) |
| `mj-fxs-reorder.wav` | `/usr/local/share/mj-fxs-reorder.wav` | fast-busy tone played when the far end hangs up first |
| `mj-baresip.service` | `/etc/systemd/system/` | runs baresip as a service user in the `audio` group |
| `mj-fxs-bridge.service` | `/etc/systemd/system/` | runs the bridge as root, after baresip |
| `~/.baresip/config`, `~/.baresip/accounts` | the service user’s home | baresip: alsa `plughw:1,0`, 8kHz, g711, ctrl_tcp, `mjusb` account, `answermode=manual` |

## Operate
```bash
sudo systemctl status mj-baresip mj-fxs-bridge      # both enabled on boot
sudo journalctl -u mj-fxs-bridge -f                 # watch hook/DTMF/ring/call events
sudo asterisk -rx "pjsip show contacts" | grep mjusb  # baresip registered = Avail
```
Test: lift handset → firmware dial tone → dial `100` (echo) or `201`/`203` (other phones). Call `200`
from any other extension → the handset rings → pick up. Far-end hangup while you hold the line →
reorder/fast-busy tone until you hang up (real-PSTN behaviour); re-lift for a fresh dial tone.

## Notes / limits
- baresip account password (`CHANGEME_MJUSB_PASSWORD`) lives in `~/.baresip/accounts` and `pjsip.conf` `[mjusb-auth]`.
- This USB personality does **NOT** carry the magicJack phone *number* (that's the separate
  Ethernet/ATA brain, endpoint `EXXXXXXXXXXXX`). ext 200 still falls back to that ATA + voicemail if
  the USB handset doesn't answer.
- `/etc/asterisk` was backed up to `backups/etc-asterisk-<timestamp>/` before changes (git-ignored).
