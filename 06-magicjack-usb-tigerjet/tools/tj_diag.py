import fcntl,os,glob,time
def _ioc(d,t,nr,sz): return (d<<30)|(sz<<16)|(ord(t)<<8)|nr
SF=lambda l:_ioc(3,'H',0x06,l); GF=lambda l:_ioc(3,'H',0x07,l); RLEN=65
def find():
    for u in glob.glob('/sys/class/hidraw/hidraw*/device/uevent'):
        try:
            if '06E6:0000C200' in open(u).read(): return '/dev/'+u.split('/')[4]
        except: pass
    return '/dev/hidraw1'
PATH=find()
class D:
    def __init__(s): s.fd=os.open(PATH,os.O_RDWR)
    def ro(s):
        try: os.close(s.fd)
        except: pass
        s.fd=os.open(PATH,os.O_RDWR)
    def setf(s,p):
        b=bytearray(RLEN); b[0]=0
        for i,v in enumerate(p): b[1+i]=v
        try: fcntl.ioctl(s.fd,SF(RLEN),b,True); return "OK"
        except OSError as e: s.ro(); return f"STALL{e.errno}"
    def outp(s,p):   # OUTPUT report via write() syscall (control SET_REPORT Output)
        b=bytearray([0])+bytearray(p)
        try: n=os.write(s.fd,bytes(b)); return f"OK({n})"
        except OSError as e: s.ro(); return f"ERR{e.errno}"
    def getf(s):
        b=bytearray(RLEN); b[0]=0
        try: n=fcntl.ioctl(s.fd,GF(RLEN),b,True); return bytes(b[1:n])
        except OSError: s.ro(); return None
d=D()
def bank(bk): d.setf([0x04,bk,bk,0x00]); time.sleep(0.004)
def win(bk):
    bank(bk); return d.getf()
def fulldiff(a,b):
    if not a or not b: return "GET-FAIL"
    ds=[(i,a[i],b[i]) for i in range(min(len(a),len(b))) if a[i]!=b[i]]
    return (", ".join(f'[{i:02x}]{o:02x}->{n:02x}' for i,o,n in ds)) if ds else "IDENTICAL"

print("=== TEST 1: does ANY 0x40-header write change ANY byte of its bank window? ===")
for reg,val in ((0x22,0x2A),(0x22,0x14),(0x0A,0xFF),(0x1F,0x55)):
    bk=reg&0xE0
    before=win(bk)
    st=d.setf([0x40, reg, bk, 0x01, val]); time.sleep(0.02)
    after=win(bk)
    print(f"  0x40 write reg0x{reg:02x}<-0x{val:02x} [{st}]  bank0x{bk:02x} fulldiff: {fulldiff(before,after)}")

print("\n=== TEST 2: OUTPUT report (write syscall) as the write transport ===")
for hdr in (0x04,0x40):
    bk=0x20
    before=win(bk)
    r=d.outp([hdr,0x22,bk,0x01,0x2A]); time.sleep(0.02)
    after=win(bk)
    print(f"  OUTPUT hdr0x{hdr:02x} reg0x22<-0x2A -> write()={r}  fulldiff: {fulldiff(before,after)}")

print("\n=== TEST 3: OUTPUT report bank-select + does a plain 0x04 write work as OUTPUT? ===")
before=win(0x40)
r1=d.outp([0x04,0x40,0x40,0x00]); r2=d.outp([0x04,0x55,0x40,0x01,0xB4]); time.sleep(0.02)
after=win(0x40)
print(f"  OUTPUT bankselect={r1} write0x55={r2}  fulldiff(bank0x40): {fulldiff(before,after)}")
os.close(d.fd)
