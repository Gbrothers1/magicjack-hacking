#!/usr/bin/env python3
"""
tj_callerid.py — inject Bellcore Type-1 (on-hook) Caller-ID to the handset attached to the
magicJack USB dongle (TigerJet 06e6:c200), from Linux. Make the phone's CID display show any
name + number you want.

HOW IT WORKS (recovered from mjupdate build_callid_wav @0x100021000 / SetRinging @0x10003b580;
see captures/mac-binary-feature-catalog.md — the "Caller-ID FSK" subsystem):
CID needs NO chip command. It is pure USB audio: a Bell-202 FSK-modulated MDMF message played
into the line through ALSA card 1, during the silent gap after the FIRST ring burst. The on-hook
handset's CID receiver demodulates it exactly like a real central-office CID.

  Bell-202 FSK : mark(1)=1200 Hz, space(0)=2200 Hz, 1200 baud, 8 kHz sample rate, amplitude +/-8192
  Framing      : channel-seizure (300 alternating bits) + mark preamble (180 mark bits) + message
  Byte framing : async 8N1 — 1 start bit (space) + 8 data bits LSB-first + 1 stop bit (mark)
  MDMF message : 0x80, <len>, params..., <checksum>   where each param = <type,len,ascii-data>
                   param 0x01 = date/time MMDDHHMM   0x02 = number   0x07 = name
                   0x04 = reason-for-absent-number   0x08 = reason-for-absent-name
                        (absence param data = 1 char: 'P'=private/blocked, 'O'=out-of-area)
                   checksum = two's-complement of the sum of all preceding message bytes (mod 256)
  SDMF message : 0x04, <len>, <date/time MMDDHHMM><number>, <checksum>  (flat, no TLV, no name;
                   absent number = the single char 'P' or 'O'; len = 8 + len(number))
  Delivery     : ring once -> ~0.5 s into the silent gap -> play the FSK -> continue ringing.

Usage:
  sudo python3 tj_callerid.py --name "ADA LOVELACE" --number 18005551212 [--cycles 4]
  sudo python3 tj_callerid.py --name "TEST" --number 5551234 --no-ring     # send FSK only (bench)
       python3 tj_callerid.py --name "TEST" --number 5551234 --wav /tmp/cid.wav   # build WAV, no HW
  sudo python3 tj_callerid.py --private                        # blocked call: no number, no name
  sudo python3 tj_callerid.py --number 5551234 --reason O      # number shown, name out-of-area
  sudo python3 tj_callerid.py --sdmf --number 5551234          # legacy Single Data Message Format
  sudo python3 tj_callerid.py --number 5551234 --cadence 2.0,4.0 --rings 6   # custom ring rhythm

Options:
  --name S       CID name  (<=15 chars typical; kept as-is). Omit -> absent-name reason param.
  --number S     CID number (digits). Omit -> absent-number reason param (see --private/--reason).
  --datetime MMDDHHMM   override the date/time stamp (default: now, local time)
  --private      blocked call: send absent-number/absent-name reason 'P' for any field not given
  --reason {P,O} absence reason char for a missing number/name (P=private, O=out-of-area; default O)
  --sdmf         emit Single Data Message Format (type 0x04) instead of MDMF; carries no name
  --cycles N     total ring bursts around the CID (default 4; CID goes after ring 1)
  --rings N      alias/override for --cycles (ring count)
  --cadence on,off      ring on/off seconds per burst (default 2.0,3.5)
  --no-ring      do not ring; just play the FSK (for a CID analyzer / bench capture)
  --wav PATH     write the modulated audio to a WAV file and exit (no device, no sudo needed)
  --level N      peak amplitude (default 8192, matching the vendor level)

SAFETY: rings the physical handset and plays audio into the line — coordinate with whoever is at
the phone. Ring uses the verified reg0 0x300 frame (tj_linepower.ring_on/off). No flash, no risk.
Validate the CID display + checksum against one real capture before trusting it (Bellcore checksum
polarity and the on-hook FSK-latch timing are the two things worth confirming on hardware).
"""
import os, sys, math, struct, wave, argparse, subprocess, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FS = 8000
F_MARK = 1200      # bit 1
F_SPACE = 2200     # bit 0
BAUD = 1200

def mdmf(number, name, mmddhhmm, reason='O'):
    """Build the MDMF Caller-ID message bytes (0x80 type + checksum). An absent (None) number
    emits 0x04 'reason for absence of number' and an absent name emits 0x08 'reason for absence
    of name', each carrying the single char reason ('P'=private / 'O'=out-of-area)."""
    params = []
    params += [0x01, len(mmddhhmm)] + list(mmddhhmm.encode('ascii'))
    if number is None:
        params += [0x04, 1, ord(reason)]                          # reason for absence of number
    else:
        params += [0x02, len(number)] + list(number.encode('ascii'))
    if name is None:
        params += [0x08, 1, ord(reason)]                          # reason for absence of name
    else:
        params += [0x07, len(name)] + list(name.encode('ascii'))
    body = [0x80, len(params)] + params
    checksum = (-sum(body)) & 0xff        # two's complement of the running sum
    return bytes(body + [checksum])

def sdmf(number, mmddhhmm):
    """Build the SDMF Caller-ID message bytes (0x04 type + checksum). Flat body = 8 date/time
    chars directly followed by the number chars (no TLV, no name); absent number = 'P'/'O'."""
    data = list(mmddhhmm.encode('ascii')) + list(number.encode('ascii'))
    body = [0x04, len(data)] + data       # len = 8 + len(number)
    checksum = (-sum(body)) & 0xff        # two's complement of the running sum
    return bytes(body + [checksum])

def _byte_bits(b):
    """8N1 async framing: start(0) + 8 data LSB-first + stop(1)."""
    bits = [0]                            # start bit = space
    for i in range(8):
        bits.append((b >> i) & 1)         # LSB first; 1 = mark
    bits.append(1)                        # stop bit = mark
    return bits

def bit_stream(msg):
    """channel seizure (300 alt bits) + mark preamble (180 mark bits) + framed message bytes."""
    bits = [i & 1 for i in range(300)]    # 010101... channel seizure
    bits += [1] * 180                     # mark signal
    for b in msg:
        bits += _byte_bits(b)
    return bits

def modulate(bits, level, lead_silence=0.2):
    """Phase-continuous Bell-202 FSK. Handles the fractional 6.667 samples/bit correctly by
    selecting each output sample's frequency from its bit index = floor(n*BAUD/FS)."""
    n_bits = len(bits)
    n_samples = int(math.ceil(n_bits * FS / BAUD))
    out = [0] * int(FS * lead_silence)    # leading silence
    phase = 0.0
    for n in range(n_samples):
        bit_index = int(n * BAUD // FS)
        if bit_index >= n_bits:
            break
        f = F_MARK if bits[bit_index] else F_SPACE
        phase += 2.0 * math.pi * f / FS
        out.append(int(level * math.sin(phase)))
    out += [0] * int(FS * 0.05)           # tiny trailing silence
    return out

def build_cid_pcm(msg, level):
    return modulate(bit_stream(msg), level)

def pcm_to_raw(samples):
    return struct.pack('<%dh' % len(samples), *[max(-32768, min(32767, s)) for s in samples])

def write_wav(path, samples):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(FS)
        w.writeframes(pcm_to_raw(samples))

def play(samples, device='plughw:1,0'):
    subprocess.run(['aplay', '-D', device, '-f', 'S16_LE', '-c', '1', '-r', str(FS), '-q', '-'],
                   input=pcm_to_raw(samples), check=True)

def main():
    ap = argparse.ArgumentParser(description="Inject Bellcore Type-1 Caller-ID to the attached handset")
    ap.add_argument('--name', default=None)
    ap.add_argument('--number', default=None)
    ap.add_argument('--datetime', default=None, help='MMDDHHMM (default: now)')
    ap.add_argument('--private', action='store_true', help="blocked call: reason 'P' for any absent field")
    ap.add_argument('--reason', choices=['P', 'O'], default=None, help='absence reason (P=private, O=out-of-area)')
    ap.add_argument('--sdmf', action='store_true', help='Single Data Message Format (no name)')
    ap.add_argument('--cycles', type=int, default=4)
    ap.add_argument('--rings', type=int, default=None, help='alias/override for --cycles')
    ap.add_argument('--cadence', default='2.0,3.5', help='ring on,off seconds (default 2.0,3.5)')
    ap.add_argument('--no-ring', action='store_true')
    ap.add_argument('--wav', default=None)
    ap.add_argument('--level', type=int, default=8192)
    a = ap.parse_args()

    mmddhhmm = a.datetime or time.strftime('%m%d%H%M')
    if len(mmddhhmm) != 8 or not mmddhhmm.isdigit():
        print("error: --datetime must be 8 digits MMDDHHMM"); return 2

    reason = 'P' if a.private else (a.reason or 'O')   # char used for any absent number/name field

    if a.sdmf:
        if a.name:
            print("warning: --name is ignored in SDMF (SDMF carries no name)")
        number = a.number if a.number else reason      # absent number -> single 'P'/'O' char
        msg, fmt = sdmf(number, mmddhhmm), 'SDMF'
    else:
        msg, fmt = mdmf(a.number, a.name, mmddhhmm, reason), 'MDMF'

    samples = build_cid_pcm(msg, a.level)
    dur = len(samples) / FS
    show_num = a.number if a.number else f'<absent:{reason}>'
    show_name = '<n/a>' if a.sdmf else (a.name if a.name else f'<absent:{reason}>')
    print(f"CID  name={show_name!r} number={show_num!r} datetime={mmddhhmm}  fmt={fmt}")
    print(f"{fmt} ({len(msg)} bytes): {msg.hex(' ')}")
    print(f"     checksum=0x{msg[-1]:02x}  (self-check sum(all)&0xff = 0x{sum(msg) & 0xff:02x}, must be 0x00)")
    print(f"FSK  {dur:.2f}s @ {FS} Hz, peak +/-{a.level}")

    if a.wav:
        write_wav(a.wav, samples); print(f"wrote {a.wav}"); return 0

    if a.no_ring:
        print("playing FSK only (no ring)..."); play(samples); return 0

    try:
        on, off = [float(x) for x in a.cadence.split(',')]
    except ValueError:
        print("error: --cadence must be 'on,off' seconds, e.g. 2.0,3.5"); return 2
    count = a.rings if a.rings is not None else a.cycles

    # on-hook Type-1 choreography: ring 1 -> silent gap -> CID -> remaining rings
    from tj_linepower import TjLine
    line = TjLine()
    try:
        print("ring 1..."); line.ring_on(); time.sleep(on); line.ring_off()
        time.sleep(0.5)                       # settle into the silent interval
        print("sending Caller-ID FSK..."); play(samples)
        for i in range(max(0, count - 1)):
            time.sleep(off); print(f"ring {i + 2}..."); line.ring_on(); time.sleep(on); line.ring_off()
    finally:
        line.ring_off(); line.close()
    print("done — check the handset's CID display.")
    return 0

if __name__ == '__main__':
    sys.exit(main())
