#!/usr/bin/env python3
"""
mj-audio.py — use the magicJack USB (TigerJet) + analog phone as a plain USB audio
interface: phone mic -> capture, playback -> phone earpiece.

The audio side needs no driver: the device enumerates as a standard USB-Audio ALSA
card (8 kHz, mono, capture+playback). The only thing Linux can't do by itself is the
FXS side (battery/line power, hook switch, ringing, DTMF), which is done over hidraw
with the RE'd TjLine class. This script does that and exposes events to your app.

Run as root (hidraw). Examples:
  mj-audio.py status
  mj-audio.py monitor [--dialtone file|hw|off]  # print hook / DTMF events; dial tone at off-hook
  mj-audio.py scope [secs]                  # raw fast log of hook/digit/strobe bytes (debug DTMF timing)
  mj-audio.py ring [seconds]                # ring the phone
  mj-audio.py session -- CMD [ARGS...]      # power line; while OFF-HOOK run CMD, kill on hang-up
  mj-audio.py session --record in.wav --play out.wav
Env in CMD: MJ_CARD (e.g. plughw:1,0). Override card with MJ_CARD or --card.

Notes: the firmware may play its own dial tone to the earpiece at off-hook; if it
bleeds into your audio, the tone ends/mutes only per the device firmware (untested here).
Capture/playback are 8 kHz S16_LE mono; use plughw: for other formats/rates.
"""
import sys, os, re, time, argparse, subprocess, signal

_HERE = os.path.dirname(os.path.realpath(__file__))
TOOLS = os.path.join(_HERE, '..', '06-magicjack-usb-tigerjet', 'tools')
sys.path.insert(1, TOOLS)
sys.path.insert(0, _HERE)

from tj_linepower import TjLine

DTMF = {1: '1', 2: '2', 3: '3', 4: '4', 5: '5', 6: '6', 7: '7', 8: '8',
        9: '9', 0xA: '0', 0xB: '*', 0xC: '#'}


def detect_card():
    if os.environ.get('MJ_CARD'):
        return os.environ['MJ_CARD']
    try:
        for line in open('/proc/asound/cards'):
            m = re.match(r'\s*(\d+)\s*\[([^\]]*)\]', line)
            if m and 'TigerJet' in m.group(2):
                return f'plughw:{m.group(1)},0'
    except OSError:
        pass
    return None


# set by chk_chip(): True on non-ARM units (e.g. bcdDevice 0.01)
LEGACY = False


def chk_chip():
    """
    ARM layout (reg0x14 bit31 hook) only applies to
    bcdDevice low nibble 5/7/8 (DevType table [1,2,0,4,5,0,7,8]).

    Other units use the LEGACY layout (see hook_dtmf).
    """
    global LEGACY
    try:
        for d in os.listdir('/sys/bus/usb/devices'):
            p = f'/sys/bus/usb/devices/{d}/'
            try:
                if (open(p + 'idVendor').read().strip() != '06e6'
                        or open(p + 'idProduct').read().strip() != 'c200'):
                    continue
                bcd = open(p + 'bcdDevice').read().strip()
            except OSError:
                continue
            n = int(bcd, 16) & 0xF
            dev = [1, 2, 0, 4, 5, 0, 7, 8][n - 1] if 1 <= n <= 8 else 0
            LEGACY = dev not in (5, 7, 8)
            if LEGACY:
                print(f"note: bcdDevice={bcd} -> DevType {dev} "
                      "(non-ARM): using legacy hook/DTMF layout",
                      file=sys.stderr)
            return
    except OSError:
        pass


def hook_dtmf(tj):
    """Returns (off_hook, dtmf_valid, nibble).
    ARM:    reg0x14 bit31 = hook, bit16 = valid, bits24-27 = digit.
    LEGACY (bcdDevice 0.01; empirically derived from tj_regwatch.py, one session):
            reg0x10 bit21 = off-hook, reg0x14 bit14 = digit strobe (~90 ms), bits8-11 = digit."""
    if LEGACY:
        w = win(tj)  # one register-window read: byte 0x12 bit5 = hook, byte 0x15 = digit/strobe
        return (w[0x12] >> 5) & 1, (w[0x15] >> 6) & 1, w[0x15] & 0xF
    v = tj.rd(0x14)
    return (v >> 31) & 1, (v >> 16) & 1, (v >> 24) & 0xF


def win(tj):
    """Whole TigerJet register bank 0x00-0x1f in
    one HID transaction (normal 0x04 framing)."""
    tj._set([0x04, 0x00, 0x00, 0x00])
    return tj._get()


class Tone:
    """Dial tone for the earpiece. mode 'file':
    loop-play a wav with aplay until stop().
    mode 'hw': just hold a capture stream open
    (the chip appears to play its own dial tone
    while the audio path is active — unverified)."""

    def __init__(s, card, mode, wav):
        s.card, s.mode, s.wav, s.p = card, mode, wav, None

    def start(s):
        if s.p and s.p.poll() is None:
            return
        if s.mode == 'file':
            cmd = ['aplay', '-q', '-D', s.card, s.wav]
        else:
            cmd = ['arecord',
                   '-q',
                   '-D', s.card,
                   '-f', 'S16_LE',
                   '-r', '8000',
                   '-c', '1',
                   '-t', 'raw',
                   '/dev/null']
        s.p = subprocess.Popen(cmd, stderr=subprocess.PIPE)
        time.sleep(0.3)
        if s.p.poll() is not None:
            err = s.p.stderr.read().decode().strip()
            print(f"dial tone: {cmd[0]} failed: {err}\n  "
                  "(if 'busy': PulseAudio holds the card — "
                  "run as `pasuspender -- sudo ./mj-audio.py ...`)",
                  file=sys.stderr)

    def stop(s):
        if s.p and s.p.poll() is None:
            s.p.terminate()
            try:
                s.p.wait(timeout=1)
            except subprocess.TimeoutExpired:
                s.p.kill()
        s.p = None


def scope(tj, secs):
    """
    Print every change of register bytes 0x10..0x17 with ms timestamps
    (fast poll). Use it to see how long hook/digit/strobe really last
    for brief key taps.
    """
    prev = None
    t0 = time.time()
    while secs <= 0 or time.time() - t0 < secs:
        w = win(tj)
        cur = bytes(w[0x10:0x18])
        if cur != prev:
            prev = cur
            print(f"{(time.time() - t0) * 1000:8.0f} ms  [0x10..0x17]="
                  f"{cur.hex(' ')}  "
                  f"hook={(cur[2] >> 5) & 1} strobe={(cur[5] >> 6) & 1} "
                  f"digit=0x{cur[5] & 0xF:x}", flush=True)


def scope_wide(tj, secs):
    """
    Hunt for a per-keypress signal: learn which register bytes are noisy
    while idle (1.5 s), then log every change of the quiet bytes across
    banks 0x00/0x20/0x40/0x60.
    """
    banks = (0x00, 0x20, 0x40, 0x60)

    def snap():
        out = {}
        for b in banks:
            tj._set([0x04, b, b, 0x00])
            out[b] = tj._get()
        return out

    base = snap()
    noisy = set()
    t0 = time.time()
    print("learning noise for 1.5 s - don't touch the phone ...",
          flush=True)
    while time.time() - t0 < 1.5:
        cur = snap()
        for b in banks:
            for i in range(32):
                if cur[b][i] != base[b][i]:
                    noisy.add((b, i))
        base = cur
    print("noisy bytes ignored:",
          sorted(f"{b + i:02x}" for b, i in noisy), flush=True)
    print("now press keys (short taps, repeats of the same digit, "
          "long holds) ...", flush=True)
    prev = snap()
    t0 = time.time()
    while secs <= 0 or time.time() - t0 < secs:
        cur = snap()
        for b in banks:
            for i in range(32):
                if (b, i) not in noisy and cur[b][i] != prev[b][i]:
                    print(f"{(time.time() - t0) * 1000:8.0f} ms  "
                          f"reg 0x{b + i:02x}: {prev[b][i]:02x} -> "
                          f"{cur[b][i]:02x}", flush=True)
        prev = cur


class AudioDTMF:
    """In-band DTMF detector on the capture stream (Goertzel, 205-sample
    blocks = 25.6 ms at 8 kHz).

    Unlike the chip's own detector it fires for taps of ~50 ms and for
    repeated digits.

    NOTE it holds the capture device open while off-hook;
    use --audio-cmd to read from something shareable
    (e.g. a PulseAudio `parec` command) if another app needs the mic."""
    LOW = (697, 770, 852, 941)
    HIGH = (1209, 1336, 1477, 1633)
    KEYS = ['123A', '456B', '789C', '*0#D']
    N = 205

    def __init__(s, card, on_digit, cmd=None):
        s.on_digit, s.p, s.th = on_digit, None, None
        s.cmd = cmd or ['arecord',
                        '-q',
                        '-D', card,
                        '-f', 'S16_LE',
                        '-r', '8000',
                        '-c', '1',
                        '-t', 'raw',
                        '--period-time=20000',
                        # small buffers = low detection latency
                        '--buffer-time=80000']

    def _goertzel(s, x, f):
        import math
        k = 2 * math.cos(2 * math.pi * f / 8000)
        s1 = s2 = 0.0
        for v in x:
            s0 = v + k * s1 - s2
            s2 = s1
            s1 = s0
        return s1 * s1 + s2 * s2 - k * s1 * s2

    def classify(s, x):
        e = sum(v * v for v in x)
        if e / len(x) < 40 * 40:
            return None  # too quiet (rms < 40)
        norm = (len(x) / 2) * e
        lo = [s._goertzel(x, f) / norm for f in s.LOW]
        hi = [s._goertzel(x, f) / norm for f in s.HIGH]
        il = max(range(4), key=lambda i: lo[i])
        ih = max(range(4), key=lambda i: hi[i])
        if lo[il] < 0.1 or hi[ih] < 0.1 or lo[il] + hi[ih] < 0.55:
            return None
        if sorted(lo)[-2] > 0.25 * lo[il] or sorted(hi)[-2] > 0.25 * hi[ih]:
            return None
        return s.KEYS[il][ih]

    def _run(s):
        import struct
        nbytes = s.N * 2
        cur = None
        run = 0
        sent = False
        while s.p and s.p.poll() is None:
            buf = s.p.stdout.read(nbytes)
            if not buf or len(buf) < nbytes:
                break
            d = s.classify(struct.unpack('<%dh' % s.N, buf))
            if d is not None and d == cur:
                run += 1
            else:
                cur, run, sent = d, (1 if d else 0), False
            if cur and run >= 2 and not sent:
                # 2 blocks (~51 ms) of the same pair
                sent = True
                s.on_digit(cur)

    def start(s):
        import threading
        if s.p and s.p.poll() is None:
            return
        s.p = subprocess.Popen(s.cmd,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
        s.th = threading.Thread(target=s._run, daemon=True)
        s.th.start()

    def stop(s):
        if s.p and s.p.poll() is None:
            s.p.terminate()
            try:
                s.p.wait(timeout=1)
            except subprocess.TimeoutExpired:
                s.p.kill()
        s.p = None


def monitor_legacy(tj, on_hook, on_unhook, on_digit, poll, reg_digits=True):
    """
    bcdDevice 0.01 units. From the scope trace: the digit nibble
    (byte 0x15 low bits) updates immediately on a key press even for
    short taps; the strobe (bit 6) only appears ~100 ms later
    and only if the key is still held. So: a nibble CHANGE = new digit.

    A strobe edge that is not the follow-up of a change
    (>0.4 s after it) = the same digit pressed again (held long enough).

    Limitation: a quick tap of the SAME digit as the previous one produces
    no signal at all.
    """
    prev_oh = None
    last_nib = None
    t_change = -9.0
    prev_strobe = 0

    def emit(n):
        print("DTMF", DTMF[n], flush=True)
        on_digit and on_digit(DTMF[n])

    while True:
        w = win(tj)
        oh = (w[0x12] >> 5) & 1
        strobe = (w[0x15] >> 6) & 1
        nib = w[0x15] & 0xF
        now = time.time()
        if oh != prev_oh:
            prev_oh = oh
            last_nib = nib  # ignore the stale latched digit
            cb = on_unhook if oh else on_hook
            if cb:
                cb()
            print("OFF-HOOK" if oh else "ON-HOOK", flush=True)
        elif oh and reg_digits:
            if nib != last_nib:
                last_nib = nib
                t_change = now
                if nib in DTMF:
                    emit(nib)
            if (strobe and not prev_strobe and now - t_change > 0.4
                    and nib in DTMF):
                emit(nib)
        prev_strobe = strobe
        time.sleep(poll)


def monitor(tj, on_hook=None, on_unhook=None, on_digit=None, poll=0.004,
            reg_digits=True):
    if LEGACY:
        return monitor_legacy(tj, on_hook, on_unhook, on_digit, poll,
                              reg_digits)
    prev = None
    armed = True
    while True:
        oh, valid, nib = hook_dtmf(tj)
        if oh != prev:
            prev = oh
            cb = on_unhook if oh else on_hook
            if cb:
                cb()
            print("OFF-HOOK" if oh else "ON-HOOK", flush=True)
        if valid:
            if armed and nib in DTMF:
                armed = False
                print("DTMF", DTMF[nib], flush=True)
                on_digit and on_digit(DTMF[nib])
        else:
            armed = True
        time.sleep(poll)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument('cmd', choices=['status', 'monitor', 'ring', 'session',
                                    'scope'])
    ap.add_argument('arg', nargs='*')
    ap.add_argument('--card')
    ap.add_argument('--record', help='wav file: phone -> file')
    ap.add_argument('--play', help='wav file: file -> phone')
    ap.add_argument('--dialtone', choices=['off', 'file', 'hw'],
                    default='file',
                    help="monitor only: dial tone at off-hook until first "
                         "digit / on-hook (file = loop mj-fxs-dialtone.wav"
                         " with aplay; hw = hold a capture stream open)")
    ap.add_argument('--dialtone-wav',
                    default=os.environ.get(
                        'MJ_DIALTONE_WAV', os.path.join(
                            _HERE, 'mj-fxs-dialtone.wav'))
                    )
    ap.add_argument('--audio-dtmf', action='store_true',
                    help="monitor: detect DTMF from the captured audio "
                         "(catches short taps/repeats); holds the "
                         "capture device open while off-hook")
    ap.add_argument('--audio-cmd',
                    help="monitor --audio-dtmf: command that writes raw "
                         "S16_LE 8 kHz mono to stdout (default: arecord "
                         "on the card), e.g. a PulseAudio parec command")
    a = ap.parse_args()
    card = a.card or detect_card()
    chk_chip()
    tj = TjLine()
    try:
        if a.cmd == 'status':
            print("card:", card)
            print(tj.status())
            return
        tj.line_on()
        if a.cmd == 'ring':
            n = float(a.arg[0]) if a.arg else 2.0
            if LEGACY:
                # ring via ProSLIC LINEFEED over the SPI bridge
                # see tj_slic.py
                from tj_slic import Slic
                s = Slic()
                try:
                    s.ring_slic(n)
                except RuntimeError as e:
                    sys.exit(
                        f"ring not supported on this unit yet: {e}\n"
                        "(bcdDevice 0.01: ProSLIC not reachable over "
                        "the SPI bridge; hook/DTMF/audio still work)")
                finally:
                    s.close()
                return
            tj.ring_on()
            time.sleep(n)
            tj.ring_off()
            return
        if a.cmd == 'scope':
            secs = float(a.arg[0]) if a.arg else 30.0
            (scope_wide if 'wide' in a.arg[1:] else scope)(tj, secs)
            return
        if a.cmd == 'monitor':
            tone = Tone(card, a.dialtone, a.dialtone_wav) \
                if a.dialtone != 'off' and card else None

            def digit(d):
                if tone:
                    tone.stop()  # dial tone ends at the first digit

            ad = None
            if a.audio_dtmf:
                if not card:
                    sys.exit("TigerJet ALSA card not found "
                             "(set MJ_CARD / --card)")
                import shlex
                def audio_digit(d):
                    print("DTMF", d, flush=True)
                    digit(d)

                ad = AudioDTMF(card,
                               audio_digit,
                               shlex.split(a.audio_cmd)
                               if a.audio_cmd else None)

            def unhook():
                if tone:
                    tone.start()
                if ad:
                    ad.start()

            def hook():
                if tone:
                    tone.stop()
                if ad:
                    ad.stop()

            try:
                monitor(tj,
                        on_hook=hook,
                        on_unhook=unhook,
                        on_digit=digit,
                        reg_digits=not ad)
            finally:
                hook()
            return
        # session
        if not card:
            sys.exit("TigerJet ALSA card not found (set MJ_CARD / --card)")
        env = dict(os.environ, MJ_CARD=card)
        cmd = a.arg[1:] if a.arg and a.arg[0] == '--' else a.arg
        procs = []

        def start():
            if cmd:
                procs.append(subprocess.Popen(cmd, env=env))
            if a.record:
                procs.append(subprocess.Popen(
                    ['arecord', '-q',
                     '-D', card,
                     '-f', 'S16_LE',
                     '-r', '8000',
                     '-c', '1',
                     a.record]
                ))
            if a.play:
                procs.append(subprocess.Popen(
                    ['aplay',
                     '-q', '-D', card, a.play]
                ))

        def stop():
            for p in procs:
                if p.poll() is None:
                    p.send_signal(signal.SIGINT
                                  if a.record else signal.SIGTERM)
            for p in procs:
                try:
                    p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    p.kill()
            procs.clear()

        try:
            monitor(tj, on_hook=stop, on_unhook=start)
        finally:
            stop()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            tj.ring_off()
        except Exception:
            pass


if __name__ == '__main__':
    main()
