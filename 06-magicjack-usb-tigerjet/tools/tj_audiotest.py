#!/usr/bin/env python3
"""
tj_audiotest.py — figure out why the TigerJet USB-audio capture is all zeros.

Run as root, *with PulseAudio released* so ALSA can open the card:
    pasuspender -- sudo python3 tj_audiotest.py [--card 2] [--activate]
Pick up the handset, and during each 4 s recording talk / whistle into the mouthpiece.

Steps (each prints peak/RMS of what was recorded and the USB stream state):
  A  line_on(), capture only
  B  line_on(), capture + 1 kHz tone playing to the earpiece (some USB phones only clock the
     capture path while playback is running)
  C  (only with --activate) replay the Windows driver's activation writes
     (TjIpSys, see protocol.md), then capture + playback again.  Writes volatile TJ regs
     0x00,0x02,0x30-0x33,0x3c,0x55,0x57; unplug/replug resets them.
A fully-zero file = no data from the codec. Small non-zero noise = ADC alive but no signal.
"""
import sys, os, time, wave, math, array, argparse, subprocess, struct, glob, re

sys.path.insert(0, __file__.rsplit('/', 1)[0])
from tj_slic import Slic

ap = argparse.ArgumentParser()
ap.add_argument('--card', type=int)
ap.add_argument('--activate', action='store_true')
ap.add_argument('--secs', type=int, default=4)
a = ap.parse_args()


def find_card():
    for line in open('/proc/asound/cards'):
        m = re.match(r'\s*(\d+)\s*\[TigerJet', line)
        if m: return int(m.group(1))
    sys.exit("TigerJet card not found")


card = a.card if a.card is not None else find_card()
dev = f"plughw:{card},0"

TONE = "/tmp/tj_tone.wav"
with wave.open(TONE, 'wb') as w:
    w.setnchannels(1);
    w.setsampwidth(2);
    w.setframerate(8000)
    w.writeframes(array.array('h', [int(12000 * math.sin(2 * math.pi * 1000 * i / 8000))
                                    for i in range(8000 * (a.secs + 2))]).tobytes())


def stats(path):
    with wave.open(path) as w:
        d = array.array('h');
        d.frombytes(w.readframes(w.getnframes()))
    if not d: return "EMPTY file"
    peak = max(abs(x) for x in d);
    rms = math.sqrt(sum(x * x for x in d) / len(d))
    return f"{len(d)} samples, peak={peak}, rms={rms:.1f}, nonzero={sum(1 for x in d if x)}"


def stream_state():
    out = []
    for f in glob.glob(f"/proc/asound/card{card}/stream*") + glob.glob(f"/proc/asound/card{card}/pcm0c/sub0/hw_params"):
        try:
            out.append(open(f).read().strip().replace('\n', ' | ')[:300])
        except OSError:
            pass
    return out


def record(label, play):
    path = f"/tmp/tj_{label}.wav"
    print(f"\n=== {label}: talk/whistle now ({a.secs}s){' [tone playing]' if play else ''} ===", flush=True)
    rec = subprocess.Popen(['arecord', '-q', '-D', dev, '-f', 'S16_LE', '-r', '8000', '-c', '1',
                            '-d', str(a.secs), path], stderr=subprocess.PIPE)
    pl = subprocess.Popen(['aplay', '-q', '-D', dev, TONE], stderr=subprocess.PIPE) if play else None
    time.sleep(a.secs / 2)
    for s in stream_state(): print("  stream:", s)
    rec.wait()
    if pl:
        pl.terminate();
        pl.wait()
        err = pl.stderr.read().decode().strip()
        if err: print("  aplay:", err)
    err = rec.stderr.read().decode().strip()
    if err: print("  arecord:", err)
    print("  ->", stats(path) if os.path.exists(path) else "no file")


t = Slic()
try:
    t.line_on()
    print(f"card={card} line_on done; regs: reg0=0x{t.rd(0):08x} reg0x38=0x{t.rd(0x38):08x} reg0x14=0x{t.rd(0x14):08x}")
    input("Lift the handset (off-hook), then press Enter ...")
    record("A_capture_only", False)
    record("B_capture_plus_playback", True)
    if a.activate:
        print("\nReplaying Windows activation sequence ...")
        for reg, val in [(0x00, 0xC0), (0x02, 0x20)]:
            t.tj_wr(reg, [val]);
            time.sleep(0.01)
        for off in range(0, 128, 16):
            t.tj_wr(0x57 + off, [0] * 16)
        for reg, val in [(0x00, 0x40), (0x02, 0x00), (0x3C, 0x32), (0x30, 0x80), (0x31, 0x3E),
                         (0x32, 0x00), (0x33, 0x7D), (0x55, 0xB4)]:
            try:
                t.tj_wr(reg, [val])
            except OSError as e:
                print(f"  write 0x{reg:02x}=0x{val:02x}: {e}")
            time.sleep(0.01)
        record("C_after_activation", True)
finally:
    t.close()
