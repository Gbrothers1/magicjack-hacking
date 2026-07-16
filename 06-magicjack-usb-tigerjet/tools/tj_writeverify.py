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
    try: fcntl.ioctl(fd,_ioc(3,'H',0x06,65),b,True); return "OK"
    except OSError as e: return f"S{e.errno}"
def getf():
    b=bytearray(65); b[0]=0; n=fcntl.ioctl(fd,_ioc(3,'H',0x07,65),b,True); return bytes(b[1:n])
def wreg(reg,val): return setf([0x04,reg,reg&0xE0,0x01,val])          # count=1 write
def rreg(reg):
    setf([0x04,reg&0xE0,reg&0xE0,0x00]); time.sleep(0.004)            # bank-select
    return getf()[reg&0x1F]                                            # offset reg&0x1F
print("# CORRECT readback: value(reg)=GET[reg&0x1F] after bank-select(reg&0xE0)")
for reg in (0x30,0x22,0x24):
    cur=rreg(reg); print(f"\nreg 0x{reg:02x}: initial readback=0x{cur:02x}")
    for tv in (0x55,0xAA,cur):
        st=wreg(reg,tv); time.sleep(0.02); rb=rreg(reg)
        print(f"   wreg 0x{tv:02x} [{st}] -> readback 0x{rb:02x}  {'<== WRITE LANDS' if rb==tv else ''}")
os.close(fd)
