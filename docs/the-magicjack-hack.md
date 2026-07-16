# The magicJack hack, in plain language

*An on-ramp for anyone — you don't need to know telephony or reverse-engineering. It builds up
the ideas as it goes. If you just want the deep technical process, jump to
[`../06-magicjack-usb-tigerjet/HOW-IT-WAS-HACKED.md`](../06-magicjack-usb-tigerjet/HOW-IT-WAS-HACKED.md).*

## The gadget

A **magicJack** is a little box that turns your internet connection into a home phone line. Plug an
old-school telephone into it, plug the box into power/Ethernet (or into a computer's USB port), pay
the yearly fee, and you can make and take calls. Millions were sold in the 2000s–2010s as a cheap way
to ditch the phone company.

The catch: it *only* works with magicJack's own servers and software. Your phone, your line — but
the vendor holds all the keys. If they change something, raise the price, or shut down, your hardware
is a paperweight.

I wanted to own it. Fully. Make the phone port ring when *I* say, route calls to *my* systems, and
keep working with zero dependence on magicJack's cloud. This is the story of getting there.

## Two brains in one box

Taking the magicJack HOME apart (electronically) revealed it's actually **two separate computers**:

- **The "network brain":** a chip with an Ethernet jack that speaks **SIP** — the standard language
  VoIP phones use — to magicJack's servers. This is the part that owns your phone *number*.
- **The "USB brain":** a *completely different* chip (made by a company called TigerJet) plus a
  dedicated telephone-line chip (from Silicon Labs). When you plug the magicJack into a PC's USB port
  instead of the wall, magicJack's desktop app uses *this* to drive your phone.

They're independent. And both are locked to magicJack's software. So there were really two hacks to do.

## Hack #1 — free the network brain (the easy-ish one)

SIP is a standard, and standards can be observed. I put the magicJack on my lab network behind a
router I control, and **watched the conversation** it had with magicJack's servers using a packet
sniffer (like reading postcards as they go by).

What I learned was almost funny: the magicJack logs in with just a username (a long serial-like
string) and **no password at all**, using ordinary G.711 audio. There was nothing secret to break.

So I stood up my own **[Asterisk](https://www.asterisk.org/)** server (free, open-source phone-system
software), told it to accept that exact login, and redirected the magicJack's traffic to my server
instead of magicJack's. The magicJack didn't know the difference — it happily registered to *my* PBX.
Now the networked magicJack is mine: it rings my extensions, hits my voicemail, my auto-attendant.
(That's project **[`03-magicjack-sip/`](../03-magicjack-sip/)**, with the capture notes in
**[`02-cisco-1841/`](../02-cisco-1841/)**.)

## Hack #2 — free the USB brain (the hard one)

This is the real prize. When you plug the magicJack into USB, it becomes a little sound card + a
"telephony" gadget, and magicJack's app drives the attached phone through it. But **no one documents
how.** There's no public spec for how to make the phone line power up, ring, detect the handset, or
pass audio. That's all buried inside magicJack's driver.

The naïve approach — poke the chip's registers and see what happens — went nowhere for a long time.
Every attempt to power the phone line just... stalled. Earlier notes even concluded it was
"firmware-locked" and impossible from Linux.

**The breakthrough was a change of source material.** Instead of guessing at the hardware, I went and
got magicJack's own **macOS driver** (the same file their installer downloads — publicly available)
and took it apart with a disassembler. The Mac version turned out to be a goldmine: unlike the
stripped-down Windows driver, it still had all its **function names and structure intact**. It was
like finding the answer key.

Reading that driver revealed the real story:

- The chip is **ARM-based**, and its control registers are reached over USB with a specific little
  message format the driver spelled out exactly. The "firmware lock" everyone hit was a red herring —
  it applied to the *wrong* access path.
- With the right format, I could **read and write the chip's memory and registers arbitrarily** from
  plain Linux. I proved it by reading the chip's own boot code out of its memory.
- From there, every telephone function was just a matter of finding the right register:
  - **Powering the line** (so there's dial tone and the port LED lights) — one control register.
  - **Ringing the phone** — set two bits and the firmware generates the ~90-volt ring signal.
  - **Hook detection** (is the handset up or down?) — one bit that flips.
  - **The dialed digits** — the chip *decodes the keypad tones for you* and puts the number in a
    register, so I don't even have to listen to the audio.
  - **Two-way voice** — that's just the USB sound card, at the phone-standard 8 kHz.

Every one of these was **verified on the real hardware, with a physical phone plugged in** — dial
tone in the earpiece, the bell actually ringing, digits decoding, voice going both ways. All from a
Python script talking to `/dev/hidraw`, with magicJack's software never running. (That's project
**[`06-magicjack-usb-tigerjet/`](../06-magicjack-usb-tigerjet/)**.)

## Putting it together — a real extension

The last step was making it *useful*, not just a pile of working tricks. I wired the USB phone port
into the same Asterisk PBX as a normal extension (**200**):

- A small **softphone** ([baresip](https://github.com/baresip/baresip)) handles the voice, using the
  magicJack's USB sound card at native quality, and registers to Asterisk like any SIP phone.
- A little **bridge daemon** ties the physical phone behavior to it: lift the handset → dial tone and
  the keypad works; dial a number → it connects; someone calls → the bell rings and you pick up;
  hang up → the call ends. When the far end hangs up first, you even get the classic "please hang up"
  fast-busy tone.

The result: a plain analog telephone, plugged into a hacked magicJack, plugged into a Linux PC, that
behaves exactly like a phone on any office PBX — dialing extensions, ringing on incoming calls,
carrying real conversations — with **none** of magicJack's software or servers involved.

## Why this matters (beyond the fun)

The magicJack is a stand-in for a whole category of hardware: cheap, capable devices that are
artificially chained to a vendor's cloud. The chips inside can do far more than the vendor lets them;
"the account/subscription" is often the only thing standing between you and full control of a device
you already own.

This project is one worked example of taking that control back — legally, on your own gear, by
understanding how the thing actually works. The techniques (packet capture, binary reverse-engineering,
register-level hardware control, and gluing it into open-source software) generalize to a lot more
than telephones.

*See the [`CHANGELOG.md`](../CHANGELOG.md) for the actual day-by-day path, including all the dead ends.*
