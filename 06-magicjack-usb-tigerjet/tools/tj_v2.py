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
def wreg(reg,val): return setf([0x04,reg,val])              # <-- CORRECT FORMAT
def page(bk): setf([0x04,bk,bk]); time.sleep(0.004); return getf()

print("=== VERIFY a write lands: reg 0x30 mirrors in page-0 at offset 0x08 ===")
p0=page(0x00); print(f"  page0 offset0x08 before: 0x{p0[0x08]:02x}")
for tv in (0x55,0xAA,0x80):
    st=wreg(0x30,tv); time.sleep(0.01); p0=page(0x00)
    ok = "<== WRITE CONFIRMED" if p0[0x08]==tv else ""
    print(f"  wreg(0x30,0x{tv:02x}) [{st}] -> page0[0x08]=0x{p0[0x08]:02x} {ok}")

print("\n=== CORRECTED ACTIVATION (04 <reg> <val> format) ===")
SEQ=[(0x00,0xC0),(0x02,0x20),(0x00,0x40),(0x02,0x00),(0x3C,0x32),
     (0x30,0x80),(0x31,0x3E),(0x32,0x00),(0x33,0x7D),(0x55,0xB4)]
for reg,val in SEQ:
    st=wreg(reg,val); time.sleep(0.02)
    print(f"  wreg(0x{reg:02x}, 0x{val:02x}) [{st}]")
print("\n# also try ProSLIC linefeed via SPI bridge: reg0x29 enable pins, then linefeed")
# datasheet: reg 0x29 bit5 = enable serial uP pins; write ProSLIC reg 64 (LINEFEED) = 0x01 (fwd active)
print("  enable SPI pins reg0x29<-0x20:", wreg(0x29,0x20))
print("  SPI: reg0x26<-0x40 (ProSLIC LINEFEED addr|wr):", wreg(0x26,0x40))
print("  SPI: reg0x27<-0x01 (fwd active):", wreg(0x27,0x01))
print("  SPI: reg0x29<-0x21 (start xfer):", wreg(0x29,0x21))
print("\n# >>> WATCH port LED / dial tone <<<")
os.close(fd)
