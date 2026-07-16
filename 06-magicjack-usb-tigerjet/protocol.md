# magicJack (TigerJet 06e6:c200) — full RE synthesis (Windows-softphone view)

> **⚠️ SUPERSEDED for chip control — read `captures/mac-binary-ARM-protocol.md` + `README.md` §5–§6
> first.** This page is the *early* synthesis from the stripped Windows `TjIpSys.dll`, and models chip
> access as SPORT/SPI register writes (e.g. the "activation sequence" via reg 0x55=0xB4). The device is
> **ARM-based**: real control is over the **ARM control-register (cmd 0x20)** and **SoC-memory/flash
> (cmd 0x80 "prwC")** HID ports, not the SPI bridge (a host no-op on ARM). Line power = `InitTjHardware`
> reg0 bit0; **ring = reg0 bit8-9** (the "needs a USBPcap diff" note below is solved); flash **reads**
> answer from the host in normal mode (README §7). Treat the tables below as historical context. The
> SIP/provisioning section is refined in `captures/provisioning-crypto-RE.md`. (Unit is a magicJack
> **HOME**; firmware self-reports as "magicJackPlus".)

Three doors, all mapped from the official softphone (`upgr811.exe` → SJphone + TigerJet driver).

## Stack
SJphone SIP engine (SJ Labs, brand "talk4free") → `SJHandsetMagicJack.dll` (handset plugin)
→ dynamically loads `TjIpSys.dll` (`TjIpSysCall`) → HID feature reports to the chip.
Audio = USB Audio Class (ALSA card 1). Control = 64-byte vendor HID feature report on hidraw.

## Transport (TjIpSys, HID mode — the Linux-relevant path)
- All chip access = `HidD_SetFeature`/`HidD_GetFeature`, 65-byte buffer (`buf[0]=0x00` report id + 64 data).
  → Linux: `HIDIOCSFEATURE(65)` / `HIDIOCGFEATURE(65)` on the 06e6:c200 MI_01 hidraw node.
- Register primitives: WriteReg(reg,val) `fcn.1000c611`, ReadReg(reg) `fcn.1000c625`.
  Low-level descriptor: {len=4, 0, reg(word), val(word), dir(0=wr/1=rd), 0x55} → transaction core fcn.1000b015.
- SET_FEATURE payload (medium confidence framing): byte1=flags(0x04=write), byte2=reg, byte4=len(0x01), byte5=data.

## ★ Activation sequence (TJIP_SYS_OPEN opcode 0 → fcn.1000b375) — powers SLIC/line-feed/LED
Chip probe first (fcn.1000b2eb): ReadReg(0x01)&0x6C==0x6C; ReadReg(0x55) 0x13→type2 / 0x12→type3.
Then codec/SLIC init writes IN ORDER:
| reg | val | note |
|-----|-----|------|
| 0x00| 0xC0| control: reset/enable phase |
| 0x02| 0x20| |
| 0x57| 128×0x00 (block) | coefficient/tone RAM clear |
| 0x00| 0x40| control: run phase |
| 0x02| 0x00| |
| 0x3C| 0x32| |
| 0x30| 0x80| codec block |
| 0x31| 0x3E| |
| 0x32| 0x00| |
| 0x33| 0x7D| |
| 0x55| 0xB4| **SLIC mode / line-feed enable** ← most likely LED/port power |
Line-feed/LED enable strongly tied to reg 0x00 (C0→40) and reg 0x55=0xB4.
Reversible: these are volatile codec regs; unplug/replug resets. (Flash writes are a SEPARATE
opcode path guarded by magic 0x07773456 — NOT touched here, so no brick risk.)

## Opcode map (TjIpSysCall, export 0x1000c85c, 76-way jump table)
0=SYS_OPEN(+activation) 1=SYS_CLOSE 11=SEND_VIRTUAL_KEY 16/17=start/stop detect
61-65=flash erase/write/read 69-72=serial/patch-version reads 300=raw addr read/write.
Named (inside handlers): BUILD/PLAY_CALLERID, PLAY_AUDIO_DATA, START_AUDIO_CAPTURE, OPEN_MIXER, ECC_INIT.

## Input report (interrupt IN, fcn.10006dfa) — events FROM attached phone
0x00=On-Hook, 0x20=Off-Hook; 0xB0-0xB9=digits 0-9; 0xBA=* ; 0xBB=#.
DTMF/hook are detected on-chip and reported UP (they are NOT host commands).

## Handset plugin (SJHandsetMagicJack.dll) — SJphone→device control
Exports Handset_Initialize / Handset_SetProp / Handset_Uninitialize.
SetProp IDs: 1=Active 2=Ringing 3=Ring 5=Beep 7=Silent 8=CallerID(ptr) 9=Sessions(→call-progress
tone Silent/Dial/Busy/Stutter) 11=ToneType 12=Custom(Registers/TechnicalDump) 14=AllowDeviceOpening.
Init chain: OpenTjIpDevice → TjIpSysCall(0x49,0,{2}) then InitTjHardware → SetTelephoneEnable(1)
= the FXS line-feed power. Event enum up to app: Connected/OnHook/KeyPressed/*/#/Flash/Redial/...

## Outbound DTMF / dial audio
NOT HID — generated as PCM by the softphone through the USB audio OUT endpoint (ALSA card1 playback).
Ringing the attached phone IS a SLIC function (reg 0x00/0x02/0x55 group + cadence); no dedicated
"ring" debug string isolated — needs a Windows USBPcap diff to pin the exact ring bit.

## SIP / provisioning door (magicJack.dll + .exe)
- SIP identity = device serial from TigerJet DRAM ("RWD"): sip:<serial>@<UserDomain>, empty auth
  unless challenged (matches captured <E-number> / pjsip no-auth).
  NOTE: USB iSerial = <device-serial> (hardware); the E-number is a SEPARATE DRAM/RWD value.
- Provisioning: proprietary MJSF (signed) + NVPS2 over UDP "traversal" (no cleartext REST). Bypassable.
- Config: reg HKLM\Software\talk4free\USB Softphone\Install ; SJphone profiles %LOCALAPPDATA%\SJphone\
  (Profiles.db + *.ini): SIPProxyURI/RegistrarProxyURI/RegisterOnProxy/AuthPassword(empty).
- Self-host: pjsip endpoint user=<serial>, empty auth, G.711 → point device/UA at your Asterisk;
  block ForceProvisioning/UsbProvisioning/AutoProvisioning so it can't overwrite the profile.

## Key addresses (TjIpSys.dll, base 0x10000000)
discovery 10005f84 · SetFeature 10007fd4 · GetFeature 10008076 · reportlen 10007fc3
· dispatcher 1000c85c (table 1000dc44) · input decode 10006dfa · ACTIVATION 1000b375 + probe 1000b2eb
(from SYS_OPEN 0x1000c982) · WriteReg 1000c611 · ReadReg 1000c625 · txn core 1000b015
· builders 10008233/1000845b.
