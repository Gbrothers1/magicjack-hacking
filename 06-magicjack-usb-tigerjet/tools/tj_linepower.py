#!/usr/bin/env python3
"""
tj_linepower.py — power the magicJack Plus (TigerJet 06e6:c200) analog FXS phone port ON/OFF
from Linux, over USB HID. No Windows, no Mac app, no wall power, no hardware mods.

★ VERIFIED ON HARDWARE (2026-07-15): `on` produces dial tone + lit port LED on the attached RJ11
phone; `off` drops it; repeatable on->off->on. This is a replay of CTj880Phone_Hid::InitTjHardware()
(recovered from the macOS mjupdate binary) driven through the TigerJet ARM control-register port.

Mechanism (ARM control-register port, HID feature reports, report id 0):
  register READ  : SET [00 00 <reg> 00] then GET_FEATURE ; value = resp[0:4] little-endian
  register WRITE : SET [20 <reg> 00 01 <val32 LE>]                       (cmd 0x20 = ARM reg write)
Enable sequence (CTj880 InitTjHardware): reg0 |= 1 ; reg0x38 = 3 ; reg0x14 |= 0x80, 10ms, clear bit7.
Disable: clear reg0 bit 0. (reg0 bit0 gates line power; verified by dial-tone on/off.)
RING: reg0 |= 0x300 (bits 8-9) requests ring; the ARM firmware generates the ring voltage/waveform.
  Toggle on ~2s / off ~4s for US cadence. VERIFIED: the handset physically rings. Clear 0x300 to stop.
Hook state: reg0x14 bit 31 (on-hook 0x0b000104 / off-hook 0x8b000104), verified.
NOTE: these ARM control regs persist across USB reset — only a physical unplug clears them.
"""
import fcntl, os, glob, time, sys

def _ioc(d,t,nr,sz): return (d<<30)|(sz<<16)|(ord(t)<<8)|nr
HIDIOCSFEATURE=lambda l:_ioc(3,'H',0x06,l); HIDIOCGFEATURE=lambda l:_ioc(3,'H',0x07,l)
RLEN=65

def find_hidraw():
    for u in glob.glob('/sys/class/hidraw/hidraw*/device/uevent'):
        try:
            if '06E6:0000C200' in open(u).read(): return '/dev/'+u.split('/')[4]
        except Exception: pass
    return '/dev/hidraw1'

class TjLine:
    def __init__(s): s.fd=os.open(find_hidraw(), os.O_RDWR)
    def close(s): os.close(s.fd)
    def _set(s,data):
        b=bytearray(RLEN); b[1:1+len(data)]=bytes(data); fcntl.ioctl(s.fd,HIDIOCSFEATURE(RLEN),b,True)
    def _get(s):
        b=bytearray(RLEN); fcntl.ioctl(s.fd,HIDIOCGFEATURE(RLEN),b,True); return bytes(b[1:])
    def rd(s,reg):
        s._set([0x00,0x00,reg&0xff,0x00]); time.sleep(0.002); return int.from_bytes(s._get()[0:4],'little')
    def wr(s,reg,val):
        v=[val&0xff,(val>>8)&0xff,(val>>16)&0xff,(val>>24)&0xff]; s._set([0x20,reg&0xff,0x00,0x01]+v)
    def line_on(s):
        s.wr(0x00, s.rd(0x00)|1)          # enable bit
        s.wr(0x38, 3)                      # codec/clock
        v=s.rd(0x14); s.wr(0x14, v|0x80); time.sleep(0.01); s.wr(0x14, v & 0xffffff7f)  # strobe
    def line_off(s):
        s.wr(0x00, s.rd(0x00) & ~1)       # clear enable bit -> line drops
    def ring_on(s):
        s.wr(0x00, (s.rd(0x00) & ~0x300) | 0x300)   # reg0 bits8-9 = ring request (VERIFIED: handset rings)
    def ring_off(s):
        s.wr(0x00, s.rd(0x00) & ~0x300)
    def ring(s, cycles=4, on=2.0, off=4.0):
        import time as _t
        try:
            for _ in range(cycles):
                s.ring_on();  _t.sleep(on)
                s.ring_off(); _t.sleep(off)
        finally:
            s.ring_off()
    def status(s):
        r14=s.rd(0x14)
        return dict(reg0=s.rd(0x00), reg0x14=r14, reg0x38=s.rd(0x38),
                    powered=bool(s.rd(0x00)&1), off_hook=bool(r14>>31))
    def off_hook(s):
        # VERIFIED: reg0x14 bit31 = live hook state (on-hook 0x0b000104, off-hook 0x8b000104)
        return bool(s.rd(0x14) >> 31)
    def monitor_hook(s, seconds=30):
        last=None; import time as _t; t0=_t.time()
        while _t.time()-t0 < seconds:
            oh=s.off_hook()
            if oh!=last:
                print(f"  {_t.time()-t0:5.1f}s  {'OFF-HOOK (handset lifted)' if oh else 'on-hook (handset down)'}")
                last=oh
            _t.sleep(0.05)

def main():
    cmd = sys.argv[1] if len(sys.argv)>1 else 'status'
    t=TjLine()
    try:
        if cmd=='on':   t.line_on();  print("line ON  ->", t.status())
        elif cmd=='off':t.line_off(); print("line OFF ->", t.status())
        elif cmd=='hook': print("OFF-HOOK" if t.off_hook() else "on-hook")
        elif cmd=='ring':
            n=int(sys.argv[2]) if len(sys.argv)>2 else 4
            print(f"ringing {n} cycles (2s on / 4s off)..."); t.ring(n)
        elif cmd=='ring-on':  t.ring_on();  print("ring ON  ->", t.status())
        elif cmd=='ring-off': t.ring_off(); print("ring OFF ->", t.status())
        elif cmd=='monitor':
            print("monitoring hook state (Ctrl-C to stop) — lift/replace the handset:")
            t.monitor_hook(int(sys.argv[2]) if len(sys.argv)>2 else 30)
        else:           print("status   ->", t.status())
    finally:
        t.close()

if __name__=='__main__':
    if len(sys.argv)<2: print("usage: tj_linepower.py [on|off|status|hook|monitor [secs]|ring [cycles]|ring-on|ring-off]")
    main()
