#!/usr/bin/env python3
"""
mj-fxs-bridge.py — FXS behaviour glue between the reverse-engineered magicJack USB
handset (TigerJet, hidraw line/hook/ring/DTMF) and a baresip softphone (ctrl_tcp).

Runs as ROOT (hidraw needs it). baresip carries the audio (native 8kHz ulaw, over
the auto-detected TigerJet ALSA card) and SIP-registers to whatever PBX you point it
at (Asterisk, FreePBX, FreeSWITCH, 3CX, ...) as a standard SIP endpoint.

Everything the handset needs is read/written over hidraw feature reports:
  * hook  = reg0x14 bit31           (off-hook)
  * DTMF  = reg0x14: byte0x16 valid flag, byte0x17 low nibble = digit
            (1-9->1-9, 0->0xA, *->0xB, #->0xC)  -- on-chip decoder, no audio needed
  * line  = reg0 bit0 (power),  ring = reg0 bits8-9
baresip call state is POLLED via ctrl_tcp 'listcalls' (its event push is unreliable).

Behaviour (a real FXS station):
  off-hook idle              -> firmware dial tone; read keypad digits; dial via baresip
  on-hook while in a call    -> hang up baresip
  inbound (listcalls INCOMING) -> ring the handset; off-hook -> answer
  far end hangs up (no calls) -> stop ringing / return to idle
"""
import socket, json, time, threading, sys, subprocess, os, re

# --- locate tj_linepower.TjLine (portable) ---------------------------------
# Installed layout (setup.sh): tj_linepower.py sits in THIS file's directory
# (both are copied into /opt/magicjack-fxs/). In-repo layout: tj_linepower.py
# lives in ../06-magicjack-usb-tigerjet/tools/. Try the sibling dir first, then
# fall back to the repo path, so the daemon runs unchanged in either place.
_HERE=os.path.dirname(os.path.realpath(__file__))
sys.path.insert(1, os.path.join(_HERE, '..', '06-magicjack-usb-tigerjet', 'tools'))  # repo fallback
sys.path.insert(0, _HERE)                                                            # installed sibling (wins)
from tj_linepower import TjLine

def detect_card():
    """Return 'plughw:N,0' for the TigerJet ALSA card. Env MJ_CARD overrides;
    otherwise parse /proc/asound/cards for the card whose name contains 'TigerJet'."""
    env=os.environ.get('MJ_CARD')
    if env: return env
    try:
        for line in open('/proc/asound/cards'):
            # index lines look like:  " 1 [TigerJet       ]: USB-Audio - ..."
            m=re.match(r'\s*(\d+)\s*\[([^\]]*)\]', line)
            if m and 'TigerJet' in m.group(2):
                return f'plughw:{m.group(1)},0'
    except OSError:
        pass
    return 'plughw:1,0'   # last-resort default

BARESIP=('127.0.0.1',4444)
CARD=detect_card()
REORDER_WAV=os.environ.get('MJ_REORDER_WAV', '/usr/local/share/mj-fxs-reorder.wav')
DTMF_MAP={1:'1',2:'2',3:'3',4:'4',5:'5',6:'6',7:'7',8:'8',9:'9',0xA:'0',0xB:'*',0xC:'#'}

def log(*a): print("[mj-fxs]", *a, flush=True)

# ---------------- baresip ctrl_tcp (short-lived request/response) ----------------
class Baresip:
    def rpc(s, command, params=""):
        p=json.dumps({"command":command,"params":params}).encode()
        try:
            c=socket.create_connection(BARESIP, timeout=2); c.settimeout(1.0)
            c.sendall(f"{len(p)}:".encode()+p+b",")
            out=b""
            try:
                while True:
                    d=c.recv(4096)
                    if not d: break
                    out+=d
            except socket.timeout: pass
            c.close(); return out.decode(errors='replace')
        except OSError as e:
            log("baresip rpc failed:", e); return ""
    def cmd(s, command, params=""): s.rpc(command, params)
    def call_state(s):
        r=s.rpc('listcalls')
        if 'INCOMING' in r: return 'INCOMING'
        if 'ESTABLISHED' in r: return 'ESTABLISHED'
        if 'OUTGOING' in r or 'RINGING' in r or 'EARLY' in r: return 'OUTGOING'
        if 'Active calls (0)' in r: return 'IDLE'
        return None   # unknown (rpc failed) -> treat as "no change"

class Bridge:
    def __init__(s):
        s.tj=TjLine(); s.tjlock=threading.Lock()
        s.bs=Baresip()
        s.state='IDLE'
        s.ring_stop=threading.Event()
        s.reorder=None
        with s.tjlock: s.tj.line_on()
        log("line powered on")
    # ---- reorder / fast-busy tone (far end hung up while you're still off-hook) ----
    def reorder_start(s):
        if s.reorder is None or s.reorder.poll() is not None:
            s.reorder=subprocess.Popen(['aplay','-q','-D',CARD,REORDER_WAV],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def reorder_stop(s):
        if s.reorder and s.reorder.poll() is None:
            s.reorder.terminate()
            try: s.reorder.wait(timeout=1)
            except subprocess.TimeoutExpired: s.reorder.kill()
        s.reorder=None
    # ---- TigerJet (locked) ----
    def read_hook_dtmf(s):
        with s.tjlock: v=s.tj.rd(0x14)
        return ((v>>31)&1, (v>>16)&1, (v>>24)&0xF)   # (offhook, dtmf_valid, nibble)
    def off_hook(s):
        oh,_,_=s.read_hook_dtmf(); return bool(oh)
    def ring_start(s):
        s.ring_stop.clear()
        def loop():
            while not s.ring_stop.is_set():
                with s.tjlock: s.tj.ring_on()
                if s.ring_stop.wait(2.0): break
                with s.tjlock: s.tj.ring_off()
                if s.ring_stop.wait(4.0): break
            with s.tjlock: s.tj.ring_off()
        threading.Thread(target=loop, daemon=True).start()
    def ring_end(s): s.ring_stop.set()
    # ---- outbound: read keypad digits from the DTMF register, then dial ----
    def collect_and_dial(s):
        log("off-hook idle -> dial tone; reading keypad")
        digits=""; last=time.time(); armed=True
        while s.off_hook():
            oh,valid,nib=s.read_hook_dtmf()
            if not oh: break
            if valid:
                if armed:
                    armed=False; d=DTMF_MAP.get(nib)
                    if d=='#': break
                    if d and d!='*':
                        digits+=d; last=time.time(); log("digit:", d, "->", digits)
            else:
                armed=True
            if digits and time.time()-last>4.0: break
            time.sleep(0.03)
        if not s.off_hook():
            log("hung up while dialing"); return
        if digits:
            log("dialing", digits); s.state='INCALL'; s.bs.cmd('dial', digits)
        # if no digits and still off-hook, just fall through (stay off-hook, idle)
    # ---- main poll loop: hook + baresip call state ----
    def run(s):
        prev_oh=s.off_hook(); last_cs=0.0; cs='IDLE'
        while True:
            time.sleep(0.05)
            oh=s.off_hook()
            now=time.time()
            if now-last_cs>0.3:
                last_cs=now; cs=s.bs.call_state()
            # ---- state machine ----
            if s.state=='IDLE':
                if cs=='INCOMING':
                    s.state='RINGING'; log("INBOUND -> ringing handset"); s.ring_start()
                elif oh and not prev_oh:
                    s.state='DIALING'; s.collect_and_dial()
                    if s.state=='DIALING': s.state='IDLE'
            elif s.state=='RINGING':
                if oh:
                    s.ring_end(); log("off-hook -> answer"); s.bs.cmd('accept'); s.state='INCALL'
                elif cs=='IDLE':
                    s.ring_end(); log("caller gave up -> idle"); s.state='IDLE'
            elif s.state=='INCALL':
                if not oh:
                    log("on-hook -> hangup"); s.bs.cmd('hangup'); s.state='IDLE'
                elif cs=='IDLE':
                    # far end hung up but you're still holding the handset -> reorder tone
                    log("far end hung up -> reorder"); s.reorder_start(); s.state='REORDER'
            elif s.state=='REORDER':
                if not oh:
                    s.reorder_stop(); s.state='IDLE'   # you hung up -> reset
            prev_oh=oh

if __name__=='__main__':
    Bridge().run()
