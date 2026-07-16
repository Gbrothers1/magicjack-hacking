import fcntl,os,glob,time
def _ioc(d,t,nr,sz): return (d<<30)|(sz<<16)|(ord(t)<<8)|nr
def find():
    for u in glob.glob('/sys/class/hidraw/hidraw*/device/uevent'):
        try:
            if '06E6:0000C200' in open(u).read(): return '/dev/'+u.split('/')[4]
        except: pass
    return '/dev/hidraw1'
fd=os.open(find(),os.O_RDWR)
def setf(p):
    b=bytearray(65); b[0]=0
    for i,v in enumerate(p): b[1+i]=v
    try: fcntl.ioctl(fd,_ioc(3,'H',0x06,65),b,True); return True
    except OSError: return False
def getf():
    b=bytearray(65); b[0]=0
    try: n=fcntl.ioctl(fd,_ioc(3,'H',0x07,65),b,True); return bytes(b[1:n])
    except OSError: return None
def rtj(reg):
    setf([0x04,reg&0xE0,reg&0xE0,0x00]); time.sleep(0.002); w=getf(); return w[reg&0x1F] if w else None
def wtj(reg,val): return setf([0x04,reg,reg&0xE0,0x01,val])
# reset-release once
wtj(0x00,0xC0); time.sleep(0.005); wtj(0x00,0x40); time.sleep(0.01)
def selftest(hdr,bank,kick,order):
    if order=="rvk0":  wp=[hdr,0x26,bank,0x04, 0x32, 0xB7, 0x00, kick]        # [reg,val,00,kick]
    elif order=="rv0k":wp=[hdr,0x26,bank,0x04, 0x32, 0xB7, kick, 0x00]
    elif order=="krv0":wp=[hdr,0x26,bank,0x04, kick, 0x32, 0xB7, 0x00]
    setf(wp); time.sleep(0.003)
    # SPORT read reg 0x32: write [reg|0x80,0,0,kick], then read TJ 0x27
    if order=="rvk0":  rp=[hdr,0x26,bank,0x04, 0x32|0x80, 0x00, 0x00, kick]
    elif order=="rv0k":rp=[hdr,0x26,bank,0x04, 0x32|0x80, 0x00, kick, 0x00]
    else:              rp=[hdr,0x26,bank,0x04, kick, 0x32|0x80, 0x00, 0x00]
    setf(rp); time.sleep(0.003)
    return rtj(0x27)
hits=0
for hdr in (0x40,0x04):
  for bank in (0x00,0x20):
    for kick in (0x63,0x67,0x61,0x43,0x27,0x47,0x23,0x62):
      for order in ("rvk0","rv0k"):
        v=selftest(hdr,bank,kick,order)
        if v==0xB7:
            print(f"  *** HIT: hdr0x{hdr:02x} bank0x{bank:02x} kick0x{kick:02x} order{order} -> 0xB7 SPI LIVE ***"); hits+=1
        elif v not in (0x00,None,0xff,0x0f):
            print(f"  note: hdr0x{hdr:02x} bank0x{bank:02x} kick0x{kick:02x} {order} -> 0x{v:02x}")
print(f"\n{'CRACKED' if hits else 'no variant produced the 0xB7 self-test readback'}")
os.close(fd)
