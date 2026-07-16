#!/usr/bin/env python3
"""
masterkey_decrypt.py -- Recover the magicJack "ArmJack" eCOS firmware RC4 keys
from the UNPACKED, running SDRAM image (ram-0-16M.bin, base 0, file off == vaddr)
and decrypt what those keys can decrypt.  Legitimate interop RE of the user's own
device.  Static analysis only.

============================================================================
WHAT WAS RECOVERED FROM THE RAM IMAGE (all vaddrs are file offsets, base 0)
============================================================================

RC4 primitive (CONFIRMED, textbook):
  * KSA  @ vaddr 0x0e4ef4 : S[i]=i (256), then unrolled
                            j=(j+S[i]+key[k % keylen])&0xff; swap(S[i],S[j]).
                            Called as KSA(state, keylen=[enc+0xc], key=[enc+8]).
  * The SJEN codec allocates a 0x40c-byte (=1036=256*4+state) RC4 S-box object,
    vtable @ 0x33ca30, in the encryptor transform @ 0x1843c0 when the mode byte
    [enc+0x16] == 0 (==1 selects AES).

SJEN codec call convention (CONFIRMED by register trace):
    decode/encode(ARG1=input, ARG2=output, ARG3=KEY, ARG4=mode)
  wrappers 0x1808b0 / 0x180880 -> 0x18072c -> CSJEncryptor ctor 0x184348, which
  copies ARG3 (a std::string) via copy-ctor 0x1c699c into encryptor+8, i.e. the
  RC4 key IS the 3rd argument.  Nesting is the same as the macOS app:
      outer  "SJEN" + RC4(key, inner)
      inner  "SJCF" + uint32_BE(uncompressed_len) + zlib  -> UTF-16LE INI

TWO HARDCODED RC4 KEYS (device-independent, obfuscated as base64 rodata):
  * magicJack.Patch / config-patch key  (CONFIRMED as the key argument):
      loaded @ vaddr 0x36a103 ("4OcGDkx1...", 268 b64 chars) -> base64decode
      -> 200 bytes = e0e7060e4c754dac...c0838b
      Used at the "magicJack.Patch" decrypt path:
        0x1a8314 loads "magicJack.Patch" -> 0x1a7f7c -> 0x1a6de0, where
        var_30h = base64decode(@0x36a103) is passed as ARG3 (the RC4 key) to the
        codec 0x1808b0.  (The adjacent "SJEN\0" @0x36a0fe is a SEPARATE string
        used only as the framing magic that gets prepended to the data.)
  * dbkey / provisioning key (CONFIRMED as the key argument):
      loaded @ vaddr 0x36a589 ("ijZZAbDx...", 268 b64 chars) -> base64decode
      -> 201 bytes = 8a365901b0f16f7b...
      Used in the CProvisioning dbkey path at 0x1a9430 (key=base64decode(ijZZ)).

NOT the derivation shape of the macOS app: MD5 exists (MD5_Init @0x1c5aa0,
transform ending @0x1c5958, one-shot @0x1c5c38 with ZERO callers) but is used
ONLY for HTTP/SIP Digest auth (MD5(a:b:c) @0xe4704 / 0xe4874).  So the firmware
master key is NOT MD5(concat(strings)); it is a hardcoded high-entropy blob.

The two colon-number tables (@0x369f64: 130 ints; @0x36a696: 136 ints) drive a
block-descrambler (0x1b5eb8) used for an integrity/"chkval" compare on runtime
data -- they are NOT key material (they don't map to the blob/key lengths).
============================================================================
"""
import os, base64, struct, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
RAM  = os.path.join(HERE, 'ram-0-16M.bin')

APP_KEY = bytes.fromhex('1a909c8e737d3977614c278067f4aa32'
                        'd60ae46bf862661aa41dbe4f340ed5dd')   # macOS/local Profiles.db key

def rc4(key, data):
    if isinstance(key, str): key = key.encode()
    if not key: raise ValueError('empty key')
    S = list(range(256)); j = 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) & 255; S[i], S[j] = S[j], S[i]
    i = j = 0; out = bytearray()
    for b in data:
        i = (i + 1) & 255; j = (j + S[i]) & 255; S[i], S[j] = S[j], S[i]
        out.append(b ^ S[(S[i] + S[j]) & 255])
    return bytes(out)

def sj_decrypt(blob, key):
    """SJEN/SJCF unwrap -> plaintext (or None)."""
    b = blob
    for _ in range(4):
        if b[:4] == b'SJEN':
            b = rc4(key, b[4:])
        elif b[:4] == b'SJCF':
            d = zlib.decompressobj(); return d.decompress(b[8:]) + d.flush()
        else:
            return None
    return None

def as_text(pt):
    return pt.decode('utf-16-le', 'replace') if pt[:2] == b'\xff\xfe' else pt.decode('latin1', 'replace')

def cstr(data, a):
    e = data.find(b'\x00', a); return data[a:e]

def extract_keys():
    data = open(RAM, 'rb').read()
    patch_key = base64.b64decode(cstr(data, 0x36a103))               # 200B  e0e7060e...
    dbkey_key = base64.b64decode(cstr(data, data.find(b'ijZZAbDx'))) # 201B  8a365901...
    return patch_key, dbkey_key

if __name__ == '__main__':
    patch_key, dbkey_key = extract_keys()
    print('[KEY] magicJack.Patch RC4 key  (@0x36a103, %d bytes):\n      %s' %
          (len(patch_key), patch_key.hex()))
    print('[KEY] dbkey/provisioning key   (@0x36a589, %d bytes):\n      %s\n' %
          (len(dbkey_key), dbkey_key.hex()))

    # DEMO (WORKS): local Profiles.db shell decrypts with the APP key -> SJCF -> INI.
    pdb = os.path.join(HERE, 'mjsf-profiles-db.bin')
    if os.path.exists(pdb):
        pt = sj_decrypt(open(pdb, 'rb').read(), APP_KEY)
        if pt:
            open(os.path.join(HERE, 'profiles_db_decrypted.ini'), 'w').write(as_text(pt))
            print('[OK ] Profiles.db decrypted with APP key -> profiles_db_decrypted.ini '
                  '(a SIPProxy *shell*, no account)')

    # Attempt the two firmware keys against every captured ciphertext (honest negatives).
    caps = ['dbkey0.bin', 'dbkey1.bin', 'dbkey2.bin',
            'prov1_resp0.bin', 'prov2_resp0.bin', 'prov2_resp1.bin',
            'patchcache-inner.bin']
    print('\n[TRY] firmware keys vs captured provisioning ciphertext:')
    for f in caps:
        p = os.path.join(HERE, f)
        if not os.path.exists(p): continue
        b = open(p, 'rb').read()
        line = '  %-20s' % f
        for name, k in (('patch', patch_key), ('dbkey', dbkey_key)):
            for tag, d in (('raw', b), ('SJEN+', b'SJEN' + b)):
                pt = rc4(k, d if tag == 'raw' else d[4:])
                if pt[:4] in (b'SJCF', b'SJEN'):
                    line += '  %s/%s=MAGIC' % (name, tag)
        print(line + '  (no SJCF/SJEN -> session-keyed, not statically decryptable)')
