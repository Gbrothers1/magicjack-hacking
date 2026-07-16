#!/usr/bin/env python3
# SAFE read-only chip ID for TigerJet magicJack (06e6:c200), using the CORRECT read model
# recovered from TjIpSys.dll: GET_FEATURE-only, with an optional zero-length bank-select SET.
# Wire (HIGH confidence): bank-select = 00 04 <bank> <bank> 00 ; bank = reg&0xE0 ;
#   value of reg = GET_FEATURE data[ reg & 0x1F ].  Report ID = 0x00. No dir/0x55 bytes.
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

def dump(b,p='  '):
    for o in range(0,len(b),16):
        c=b[o:o+16]
        print(f"{p}{o:02x}: {' '.join(f'{x:02x}' for x in c):<47} {''.join(chr(x) if 32<=x<127 else '.' for x in c)}")

class Tj:
    def __init__(s,path): s.fd=os.open(path,os.O_RDWR); s.bank=None
    def close(s): os.close(s.fd)
    def get(s):
        b=bytearray(RLEN); b[0]=0
        n=fcntl.ioctl(s.fd,HIDIOCGFEATURE(RLEN),b,True)
        return bytes(b[1:n]) if n>1 else b''      # strip report-id byte -> 64 data bytes
    def bank_select(s,bank):
        b=bytearray(RLEN); b[1]=0x04; b[2]=bank; b[3]=bank; b[4]=0x00   # 00 04 BB BB 00
        fcntl.ioctl(s.fd,HIDIOCSFEATURE(RLEN),b,True); s.bank=bank; time.sleep(0.004)
    def read_reg(s,reg):
        bank=reg&0xE0
        if s.bank!=bank: s.bank_select(bank)
        data=s.get()
        idx=reg&0x1F
        return data[idx] if idx<len(data) else None, data

def main():
    p=find_hidraw(); print(f"# hidraw: {p}")
    tj=Tj(p)
    try:
        print("# bank 0 window (default GET_FEATURE):"); dump(tj.get())
        # sanity reg 0x01 (probe expects &0x6C==0x6C), then chip-id 0x55 (0x13->type2,0x12->type3), rev 0x56
        for reg in (0x01,0x55,0x56):
            v,win=tj.read_reg(reg)
            note=""
            if reg==0x01: note=f"(&0x6C=0x{v&0x6C:02x}; probe wants 0x6C)" if v is not None else ""
            if reg==0x55: note={0x13:"=> chip TYPE 2",0x12:"=> chip TYPE 3"}.get(v,"(unmapped)")
            if reg==0x56: note="chip revision"
            print(f"\n# reg 0x{reg:02x} (bank 0x{reg&0xE0:02x}, offset 0x{reg&0x1F:02x}) = "
                  f"{'0x%02x'%v if v is not None else '??'}  {note}")
    finally:
        tj.close()

if __name__=='__main__': main()
