#!/usr/bin/env python3
# tj_dtmf_decode.py - decode DTMF keypad digits from a WAV captured off the magicJack line
# (ALSA card 1, plughw:1,0, S16_LE 8kHz mono). Goertzel dual-tone detector.
# VERIFIED 2026-07-15: exact match on live keypad presses "9 8 7 6 5 3 #" via the TigerJet capture path.
# Usage: arecord -D plughw:1,0 -f S16_LE -c1 -r8000 -d 15 out.wav ; python3 tj_dtmf_decode.py out.wav
import wave, sys, numpy as np
LOW=[697,770,852,941]; HIGH=[1209,1336,1477,1633]
KEYS={(697,1209):'1',(697,1336):'2',(697,1477):'3',(697,1633):'A',
      (770,1209):'4',(770,1336):'5',(770,1477):'6',(770,1633):'B',
      (852,1209):'7',(852,1336):'8',(852,1477):'9',(852,1633):'C',
      (941,1209):'*',(941,1336):'0',(941,1477):'#',(941,1633):'D'}
def goertzel(x, sr, f):
    N=len(x); k=int(0.5+N*f/sr); w=2*np.pi*k/N; coeff=2*np.cos(w)
    s1=s2=0.0
    for v in x: s0=v+coeff*s1-s2; s2=s1; s1=s0
    return s1*s1+s2*s2-coeff*s1*s2
def main(path):
    w=wave.open(path); sr=w.getframerate(); n=w.getnframes()
    x=np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float64)
    win=int(sr*0.045); hop=int(sr*0.02)
    seq=[]; last=None; run=0
    for i in range(0,len(x)-win,hop):
        seg=x[i:i+win]
        energy=np.sqrt(np.mean(seg**2))
        if energy<80: last=None; run=0; continue
        lo=[(f,goertzel(seg,sr,f)) for f in LOW]
        hi=[(f,goertzel(seg,sr,f)) for f in HIGH]
        lf,lm=max(lo,key=lambda t:t[1]); hf,hm=max(hi,key=lambda t:t[1])
        lo2=sorted(v for _,v in lo)[-2]; hi2=sorted(v for _,v in hi)[-2]
        # dominant tone in each group must beat 2nd-best by 4x (clean dual-tone)
        if lm>4*lo2 and hm>4*hi2:
            key=KEYS.get((lf,hf))
            if key==last: run+=1
            else: run=1; last=key
            if key and run==2 and (not seq or seq[-1]!=('_'+key)):
                seq.append(key); 
        else:
            last=None; run=0
    # collapse consecutive dups from long presses
    out=[]
    for k in seq:
        if not out or out[-1]!=k: out.append(k)
    print("decoded DTMF:", ' '.join(out) if out else "(none detected)")
if __name__=='__main__': main(sys.argv[1])
