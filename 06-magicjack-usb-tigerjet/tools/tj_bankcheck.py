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
def h(b): return ' '.join('%02x'%x for x in b[:16])
print("# Is GET stable WITHOUT any select? (3 back-to-back reads)")
for i in range(3): print("  ", h(getf())); time.sleep(0.05)
print("\n# bank-select determinism: 0x00, 0x40, 0x20, 0x00 (repeat 0x00 should match first 0x00)")
for bk in (0x00,0x40,0x20,0x00,0x40,0x00):
    setf([0x04,bk,bk,0x00]); time.sleep(0.01)
    print(f"  after select 0x{bk:02x}: {h(getf())}")
print("\n# does GET change on its own over 1s (device telemetry drift)?")
a=getf(); time.sleep(1.0); b=getf()
print("  t0:", h(a)); print("  t1:", h(b)); print("  drift:", "YES" if a!=b else "none")
os.close(fd)
