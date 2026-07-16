#!/usr/bin/env python3
# ARM-register-window tester for TigerJet magicJack (06e6:c200, TJ780/880 = ARM-based chip).
# Framing recovered from the macOS `mjupdate` binary (symbols intact):
#   _HidReadTjRegs_ARM : SET_FEATURE [00][ (req&1) ][addr][00][00...]  then GET_FEATURE;
#                        result = count 32-bit LE words copied from returned data[0..].
#   _HidWriteTjRegs_ARM: SET_FEATURE [00][ 0x20|(req&1) ][addr][00][count_words][ words x4B ]
# Report id = 0x00 (buf[0]); 64 data bytes follow (ARM chips use a 64-byte feature report).
# This tool is READ-ONLY by default (no writes) — a safe probe of whether the ARM window responds.
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

class TjArm:
    def __init__(s,path): s.fd=os.open(path,os.O_RDWR)
    def close(s): os.close(s.fd)
    def _set(s,data):
        b=bytearray(RLEN); b[0]=0
        b[1:1+len(data)]=bytes(data)
        return fcntl.ioctl(s.fd,HIDIOCSFEATURE(RLEN),b,True)
    def _get(s):
        b=bytearray(RLEN); b[0]=0
        n=fcntl.ioctl(s.fd,HIDIOCGFEATURE(RLEN),b,True)
        return bytes(b[1:n]) if n>1 else b''
    def read_arm(s,addr,count=1,req=0):
        # SET_FEATURE request frame, then GET_FEATURE readback
        s._set([ (req&1), 0x00, addr&0xFF, 0x00 ]); time.sleep(0.003)
        data=s._get()
        words=[]
        for i in range(count):
            w=data[i*4:i*4+4]
            words.append(int.from_bytes(w,'little') if len(w)==4 else None)
        return words, data
    def write_arm(s,addr,word,req=0):
        payload=[ 0x20|(req&1), addr&0xFF, 0x00, 0x01,
                  word&0xFF,(word>>8)&0xFF,(word>>16)&0xFF,(word>>24)&0xFF ]
        return s._set(payload)
    def open_flash_read(s):
        # EXACT read-only open sequence from macOS _OpenTjDevice (ReadOnePageBulkMem, flash READ).
        # Three 0x80/"prwC" setup reports, then stream 32x GET_FEATURE (a 2KB page), then finalize.
        # Byte layout per report (buf[1..]): 80 <b2> <b3> <b4> <dword=0> .. 'p' 'r' 'w' 'C' at buf[9..12]
        PRWC=[0x70,0x72,0x77,0x43]
        def rep(b2,b3,b4):
            d=[0x80,b2,b3,b4, 0,0,0,0]+PRWC
            return s._set(d)
        page=bytearray()
        rep(0x04,0x13,0x04)         # set read flash data pointer
        rep(0x14,0x00,0x00)
        rep(0x04,0x08,0x04)
        for _ in range(32):
            page += s._get()[:0x40]
        rep(0x14,0x00,0x00)         # finalize
        return bytes(page)

def sweep(tj,label):
    print(f"\n# ARM read sweep addr 0x00..0x3F  [{label}]:")
    hits=[]; stalls=0
    for addr in range(0x00,0x40):
        try:
            words,data=tj.read_arm(addr,1); w=words[0]
            if w not in (0x00000000,0xFFFFFFFF,None):
                print(f"  arm[0x{addr:02x}] = 0x{w:08x} <-- nonzero"); hits.append((addr,w))
        except OSError:
            stalls+=1
    print(f"  -> {stalls}/64 stalled, {len(hits)} nonzero: {[(hex(a),hex(w)) for a,w in hits]}")
    return stalls,hits

def main():
    p=find_hidraw(); print(f"# hidraw: {p}   (ARM framing from macOS mjupdate)")
    tj=TjArm(p)
    try:
        print("\n# baseline GET_FEATURE (no request):"); dump(tj._get())
        # BEFORE open:
        sweep(tj,"before open")
        # Replay the read-only flash-open handshake, then sweep again:
        print("\n# replaying read-only 0x80/'prwC' flash-open handshake (ReadOnePageBulkMem)...")
        try:
            page=tj.open_flash_read()
            print(f"  got {len(page)} bytes; first 32:")
            dump(page[:32])
            sig=int.from_bytes(page[0:4],'little') if len(page)>=4 else 0
            print(f"  partition-info dword0 = 0x{sig:08x}  (open expects (x&0xff000000)==0x31000000, low16==0x6462 'bd', byte2==0x3d)")
        except OSError as e:
            print(f"  open handshake stalled: errno {e.errno}")
        sweep(tj,"after open")
        return
        hits=[]
        for addr in range(0x00,0x40):
            try:
                words,data=tj.read_arm(addr,1)
                w=words[0]
                tag=""
                if w not in (0x00000000,0xFFFFFFFF,None): tag=" <-- nonzero"
                if w is not None:
                    print(f"  arm[0x{addr:02x}] = 0x{w:08x}{tag}")
                    if tag: hits.append((addr,w))
            except OSError as e:
                print(f"  arm[0x{addr:02x}] = STALL/err ({e.errno})")
        print(f"\n# nonzero/interesting ARM words: {[(hex(a),hex(w)) for a,w in hits]}")
        # Compare: does the ARM request frame actually change GET_FEATURE vs a plain read?
        print("\n# control: ARM read addr 0x00 full 64-byte GET window:")
        _,data=tj.read_arm(0x00,16); dump(data)
    finally:
        tj.close()

if __name__=='__main__': main()
