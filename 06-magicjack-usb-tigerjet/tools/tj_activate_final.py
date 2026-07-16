import fcntl,os,glob,time
def _ioc(d,t,nr,sz): return (d<<30)|(sz<<16)|(ord(t)<<8)|nr
def find():
    for u in glob.glob('/sys/class/hidraw/hidraw*/device/uevent'):
        try:
            if '06E6:0000C200' in open(u).read(): return '/dev/'+u.split('/')[4]
        except: pass
    return '/dev/hidraw1'
fd=os.open(find(),os.O_RDWR)
def setf(payload):
    b=bytearray(65); b[0]=0
    for i,v in enumerate(payload): b[1+i]=v
    try: fcntl.ioctl(fd,_ioc(3,'H',0x06,65),b,True); return True
    except OSError as e: return False
def getf():
    b=bytearray(65); b[0]=0
    try: n=fcntl.ioctl(fd,_ioc(3,'H',0x07,65),b,True); return bytes(b[1:n])
    except OSError: return None
def wtj(reg,val): return setf([0x04,reg,reg&0xE0,0x01,val])          # confirmed TigerJet write
def rtj(reg):
    setf([0x04,reg&0xE0,reg&0xE0,0x00]); time.sleep(0.003); w=getf(); return w[reg&0x1F] if w else None
# ProSLIC direct write/read via SPORT (header 0x40, bank 0x00, kick 0x63)
def pw(reg,val): return setf([0x40,0x26,0x00,0x04, reg&0x7f, val&0xff, 0x00, 0x63])
def pr(reg):
    setf([0x40,0x26,0x00,0x04, reg|0x80, 0x00, 0x00, 0x63]); time.sleep(0.003)
    return rtj(0x27)                                                  # SPI result lands in TJ 0x27

print("== reset-release ProSLIC (TigerJet EXTRST toggle) ==")
print("  reg0x00<-0xC0:",wtj(0x00,0xC0)); time.sleep(0.005)
print("  reg0x00<-0x40:",wtj(0x00,0x40)); time.sleep(0.010)
print("  device alive:", "yes" if rtj(0x55) is not None else "NO")

print("\n== SPI BRIDGE SELF-TEST: write ProSLIC 0x32=0xB7, read back ==")
pw(0x32,0xB7); time.sleep(0.004)
rb=pr(0x32)
print(f"  ProSLIC reg 0x32 readback = {('0x%02x'%rb) if rb is not None else 'FAIL'}  {'<== SPI BRIDGE LIVE!' if rb==0xB7 else '(readback may be fixed-struct; LED is the oracle)'}")
pw(0x32,0x00)

print("\n== read a few ProSLIC regs (after reset-release) ==")
for reg in (0,11,64,82,14):
    print(f"  ProSLIC reg {reg:3} = 0x{(pr(reg) or 0):02x}")

print("\n== DC-DC power-up + LINEFEED ==")
print("  reg92<-0xFF (PWM):",pw(0x5C,0xFF))
print("  reg14<-0x00 (DC-DC ON):",pw(0x0E,0x00)); print("  ...waiting 60ms for VBAT..."); time.sleep(0.06)
print("  reg82 VBAT sense:", "0x%02x"%(pr(82) or 0))
print("  reg93<-0x19 (DCTOF):",pw(0x5D,0x19)); time.sleep(0.02)
print("  reg64<-0x01 (LINEFEED FORWARD ACTIVE):",pw(0x40,0x01)); time.sleep(0.05)
print("  reg64 readback:", "0x%02x"%(pr(64) or 0), "(expect ~0x11: LF=001+LFS=001)")
print("  reg68 LOOP_STAT:", "0x%02x"%(pr(68) or 0))
print("\n>>> WATCH: phone-port LED + dial tone. Lift the handset to test off-hook. <<<")
os.close(fd)
