#!/usr/bin/env python3
"""
tj_armmem.py — arbitrary ARM SoC memory access on the magicJack Plus (TigerJet 06e6:c200)
over USB HID feature reports, from Linux. NO Windows, NO updater mode, NO physical access.

Protocol recovered from the macOS `mjupdate` binary (symbols intact): _ReadAddress /
_Vitya_HAL_WRITE_UINT32. All commands are HID SET/GET_FEATURE (report id 0, 64-byte data),
command byte 0x80 with the ASCII magic "prwC" (0x70 0x72 0x77 0x43), sub-register 0x0e = the
raw-memory address port (distinct from 0x13 = flash-page, which IS mode-gated).

READ  addr:  SET 80 04 0e 04 <addr32 LE> "prwC" ; GET -> value at resp[0:4] ; SET 80 14 .. "prwC"
WRITE addr:  SET 80 02 0e 04 <addr32 LE> "prwC" ; SET 44 <val32 LE> ; SET 80 12 .. "prwC"

Confirmed on hardware (read): addr 0 returns the ARM reset-vector table; 0x00000000-0x01000000 is
RAM/ROM; 0x98xxxxxx are SoC peripherals. Reading an UNMAPPED address (e.g. 0x40000000) STALLS and
latches the endpoint — this tool recovers via USBDEVFS_RESET. Stay within known-mapped ranges.

WRITE is GATED behind --allow-write and is NOT exercised by default. Writing SoC peripheral or
watchdog registers can reboot or destabilise the device; never write blind. Read-only by default.
"""
import fcntl, os, glob, time, sys, argparse

def _ioc(d,t,nr,sz): return (d<<30)|(sz<<16)|(ord(t)<<8)|nr
HIDIOCSFEATURE=lambda l:_ioc(3,'H',0x06,l); HIDIOCGFEATURE=lambda l:_ioc(3,'H',0x07,l)
RLEN=65
PRWC=[0x70,0x72,0x77,0x43]
USBDEVFS_RESET=ord('U')<<8|20

def find_hidraw():
    for u in glob.glob('/sys/class/hidraw/hidraw*/device/uevent'):
        try:
            if '06E6:0000C200' in open(u).read(): return '/dev/'+u.split('/')[4]
        except Exception: pass
    return '/dev/hidraw1'

def usb_reset():
    import subprocess
    out=subprocess.check_output(['lsusb']).decode()
    for line in out.splitlines():
        if '06e6:c200' in line:
            b=line.split()[1]; d=line.split()[3].rstrip(':')
            fd=os.open(f'/dev/bus/usb/{b}/{d}', os.O_WRONLY)
            try: fcntl.ioctl(fd, USBDEVFS_RESET, 0)
            finally: os.close(fd)
            time.sleep(3); return

class ArmMem:
    def __init__(s): s.fd=os.open(find_hidraw(), os.O_RDWR)
    def close(s):
        try: os.close(s.fd)
        except Exception: pass
    def _set(s,data):
        b=bytearray(RLEN); b[0]=0; b[1:1+len(data)]=bytes(data)
        fcntl.ioctl(s.fd, HIDIOCSFEATURE(RLEN), b, True)
    def _get(s):
        b=bytearray(RLEN); b[0]=0; fcntl.ioctl(s.fd, HIDIOCGFEATURE(RLEN), b, True); return bytes(b[1:])
    def _finalize(s): s._set([0x80,0x14]+[0]*6+PRWC)
    def read_u32(s,addr):
        a=[addr&0xff,(addr>>8)&0xff,(addr>>16)&0xff,(addr>>24)&0xff]
        s._set([0x80,0x04,0x0e,0x04]+a+PRWC); time.sleep(0.0015)
        r=s._get(); s._finalize()
        return int.from_bytes(r[0:4],'little')
    def write_u32(s,addr,val):
        a=[addr&0xff,(addr>>8)&0xff,(addr>>16)&0xff,(addr>>24)&0xff]
        v=[val&0xff,(val>>8)&0xff,(val>>16)&0xff,(val>>24)&0xff]
        s._set([0x80,0x02,0x0e,0x04]+a+PRWC)          # 1) load address port
        s._set([0x44]+v)                               # 2) load data
        s._set([0x80,0x12]+[0]*6+PRWC)                 # 3) commit/execute
    def dump(s,addr,length):
        out=bytearray()
        for off in range(0,length,4):
            out+=s.read_u32(addr+off).to_bytes(4,'little')
        return bytes(out)
    # --- ARM control-register port (cmd 0x20 / read window), distinct from SoC memory port ---
    # These are the TigerJet ARM firmware control registers (byte-addressed 32-bit window) that
    # CTj880Phone_Hid::InitTjHardware() drives (_tjinp_ARM / _tjoutp_ARM). Confirmed R/W + round-trip.
    # NOTE: these persist across USB reset (only a physical power-cycle clears them).
    def areg_read(s,reg):
        s._set([0x00,0x00,reg&0xff,0x00]); time.sleep(0.002)
        return int.from_bytes(s._get()[0:4],'little')
    def areg_write(s,reg,val):
        v=[val&0xff,(val>>8)&0xff,(val>>16)&0xff,(val>>24)&0xff]
        s._set([0x20,reg&0xff,0x00,0x01]+v)

def main():
    ap=argparse.ArgumentParser(description="ARM SoC memory access over TigerJet HID")
    ap.add_argument('--read',metavar='ADDR',help='read one u32 (hex)')
    ap.add_argument('--dump',nargs=2,metavar=('ADDR','LEN'),help='dump LEN bytes from ADDR (hex), to stdout hex + optional --out')
    ap.add_argument('--out',help='write --dump bytes to this file')
    ap.add_argument('--write',nargs=2,metavar=('ADDR','VAL'),help='write u32 VAL to ADDR (hex); requires --allow-write')
    ap.add_argument('--allow-write',action='store_true',help='safety gate for --write')
    a=ap.parse_args()
    m=ArmMem()
    try:
        if a.read is not None:
            addr=int(a.read,16); print(f"[0x{addr:08x}] = 0x{m.read_u32(addr):08x}")
        if a.dump:
            addr=int(a.dump[0],16); length=int(a.dump[1],16); data=m.dump(addr,length)
            if a.out: open(a.out,'wb').write(data); print(f"# {len(data)} bytes -> {a.out}")
            for o in range(0,len(data),16):
                c=data[o:o+16]; print(f"  {addr+o:08x}: "+' '.join('%02x'%x for x in c))
        if a.write:
            if not a.allow_write:
                print("refusing --write without --allow-write (SoC/watchdog writes can reboot the device)"); sys.exit(2)
            addr=int(a.write[0],16); val=int(a.write[1],16)
            before=m.read_u32(addr); m.write_u32(addr,val); after=m.read_u32(addr)
            print(f"[0x{addr:08x}] {before:#010x} -> wrote {val:#010x} -> {after:#010x}")
    except OSError as e:
        print(f"OSError errno {e.errno} (unmapped addr latches the endpoint; run with a USB reset)");
    finally:
        m.close()

if __name__=='__main__': main()
