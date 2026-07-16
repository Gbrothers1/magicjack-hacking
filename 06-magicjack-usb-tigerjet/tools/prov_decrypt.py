#!/usr/bin/env python3
"""
magicJack / talk4free "SJphone" provisioning-config crypto — recovered.
Interop RE of the user's own device. No hardware touched, no server contacted.

CIPHER (CONFIRMED, from macOS softphone `magicJack`, class CSJEncryptor, and
demonstrated end-to-end on the device's own flash config store):

  outer:  b"SJEN" + RC4(key, inner)                      # encryption layer
  inner:  b"SJCF" + uint32_BE(uncompressed_len) + zlib   # compression layer
  -> zlib.decompress -> UTF-16LE (BOM ff fe) INI text

  - RC4: plain ARCFOUR, key = raw bytes, NO IV / NO salt.
  - Nesting: strip/RC4 while the data starts with b"SJEN"; then if it starts
    with b"SJCF", inflate. (Coder mode field [obj+0x2c]==0 => RC4; ==1/2 => AES
    ECB/CBC, unused by the provisioning path.)

KEYS:
  * APP / local-config-store key (CONFIRMED, concrete):
        MD5(concat(decoy_string_set_A)) || MD5(concat(decoy_string_set_B))
      = 1a909c8e737d3977614c278067f4aa32d60ae46bf862661aa41dbe4f340ed5dd
    This decrypts the device's own MJSF "Profiles.db" config store (see demo).
  * Profile-patch / HTTP-response key (CONFIRMED mechanism):
      RC4 key = hexdecode(config["EncryptKey"]), a per-device value; the live
      provisioning responses are keyed per-request (a session RC4 key carried
      inside the `dbkey`, itself encrypted under an eCOS-firmware MASTER key).
      That master key lives in the LZ-packed eCOS firmware and is not
      statically extractable, so the captured HTTP responses are NOT
      decryptable here (documented in the report).
"""
import os, struct, zlib
HERE = os.path.dirname(os.path.abspath(__file__))
APP_KEY = bytes.fromhex('1a909c8e737d3977614c278067f4aa32d60ae46bf862661aa41dbe4f340ed5dd')

def rc4(key, data):
    if isinstance(key, str): key = key.encode()
    S = list(range(256)); j = 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) & 255; S[i], S[j] = S[j], S[i]
    i = j = 0; out = bytearray()
    for b in data:
        i = (i + 1) & 255; j = (j + S[i]) & 255; S[i], S[j] = S[j], S[i]
        out.append(b ^ S[(S[i] + S[j]) & 255])
    return bytes(out)

def sj_decrypt(blob, key):
    """Return decrypted+decompressed plaintext, or None if it doesn't fit the format."""
    b = blob
    for _ in range(4):
        if b[:4] == b'SJEN':
            b = rc4(key, b[4:])
        elif b[:4] == b'SJCF':
            ulen = struct.unpack('>I', b[4:8])[0]
            d = zlib.decompressobj()
            pt = d.decompress(b[8:]) + d.flush()
            if len(pt) != ulen:  # sanity
                pass
            return pt
        else:
            return None
    return None

def as_text(pt):
    if pt[:2] == b'\xff\xfe': return pt.decode('utf-16-le', 'replace')
    return pt.decode('latin1', 'replace')

if __name__ == '__main__':
    # DEMO 1 (WORKS): decrypt the device's own flash config store with the APP key.
    flash = os.path.join(HERE, 'magicjack-flash.bin')
    if os.path.exists(flash):
        d = open(flash, 'rb').read()
        off = d.find(b'SJEN', 0x7a0000)        # device MJSF "Profiles.db" SJEN blob
        pt = sj_decrypt(d[off:off+0x2000], APP_KEY)
        print(f"[OK] flash Profiles.db @0x{off:x} decrypted with APP key ({len(pt)} bytes):")
        print(as_text(pt))
    # DEMO 2 (BLOCKED): the captured HTTP provisioning responses.
    for f in ('prov1_resp0.bin', 'prov2_resp0.bin', 'prov2_resp1.bin'):
        p = os.path.join(HERE, f)
        if not os.path.exists(p): continue
        body = open(p, 'rb').read()
        r = sj_decrypt(body, APP_KEY)
        print(f"[BLOCKED] {f}: not 'SJEN'-framed under APP key "
              f"(first4={body[:4].hex()}); needs the per-request session key "
              f"(firmware master key). => {r if r else 'undecryptable here'}")
