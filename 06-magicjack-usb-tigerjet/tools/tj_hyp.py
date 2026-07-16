import fcntl,os,glob,time
def _ioc(d,t,nr,sz): return (d<<30)|(sz<<16)|(ord(t)<<8)|nr
SF=lambda l:_ioc(3,'H',0x06,l); GF=lambda l:_ioc(3,'H',0x07,l); RLEN=65
def find():
    for u in glob.glob('/sys/class/hidraw/hidraw*/device/uevent'):
        try:
            if '06E6:0000C200' in open(u).read(): return '/dev/'+u.split('/')[4]
        except: pass
    return '/dev/hidraw1'
fd=os.open(find(),os.O_RDWR)
def setf(p):
    b=bytearray(RLEN); b[0]=0
    for i,v in enumerate(p): b[1+i]=v
    try: fcntl.ioctl(fd,SF(RLEN),b,True); return "OK"
    except OSError as e: return f"STALL{e.errno}"
def getf():
    b=bytearray(RLEN); b[0]=0; n=fcntl.ioctl(fd,GF(RLEN),b,True); return bytes(b[1:n])
def h(b,n=32): return ' '.join('%02x'%x for x in b[:n])
def readpage(bk): setf([0x04,bk,bk]); time.sleep(0.005); return getf()
def diff(a,b):
    ds=[(i,a[i],b[i]) for i in range(min(len(a),len(b))) if a[i]!=b[i]]
    return (", ".join(f'[{i:02x}]{o:02x}->{n:02x}' for i,o,n in ds)) if ds else "IDENTICAL"

print("=== HYP: '04 <reg> <val>' (no length byte) writes val to reg ===")
for reg,val in ((0x24,0xAA),(0x24,0x11),(0x22,0x77),(0x1F,0x08)):
    bk=reg&0xE0
    before=readpage(bk)
    st=setf([0x04,reg,val]); time.sleep(0.02)   # <-- hypothesis: write reg=val, NO len byte
    after=readpage(bk)
    print(f"  write '04 {reg:02x} {val:02x}' [{st}]  page0x{bk:02x} diff: {diff(before,after)}")

print("\n=== page-register test: is '04 40 <v>' writing v to reg 0x40 (page ptr)? ===")
w40=readpage(0x40)   # 04 40 40
setf([0x04,0x40,0x20]); time.sleep(0.01); w40_20=getf()   # 04 40 20 : write reg0x40=0x20?
w20=readpage(0x20)   # 04 20 20
print(f"  '04 40 40' window: {h(w40,16)}")
print(f"  '04 40 20' window: {h(w40_20,16)}")
print(f"  '04 20 20' window: {h(w20,16)}")
print(f"  '04 40 20' == '04 20 20'? {'YES (reg0x40 is a page ptr)' if w40_20==w20 else 'no'}")

print("\n=== 2-byte form '04 <reg> <val>' vs 3-arg: also try len at different spot ===")
bk=0x20; before=readpage(bk)
for p in ([0x04,0x24,0xAA,0x00,0x00],[0x04,0x24,0x00,0xAA],[0x05,0x24,0xAA],[0x84,0x24,0xAA]):
    st=setf(p); time.sleep(0.015); after=readpage(bk)
    print(f"  {' '.join('%02x'%x for x in p):<18} [{st}] diff: {diff(before,after)}")
    before=after
os.close(fd)
