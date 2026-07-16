#!/usr/bin/env python3
"""
sjen_decrypt.py  --  Decryptor for SJ Labs SJphone "SJEN" profile blobs
                     (as used by the magicJack softphone / TigerJet device
                      to store Profiles.db and other CSJEncryptor payloads).

============================================================================
ALGORITHM (fully reverse-engineered from softphone/mj.so, x86_64 slice)
============================================================================

CSJEncryptor is a streaming COM-like object (RTTI "12CSJEncryptor",
type_info @0x7e3e0, vtable @0x7e3a0). Its main worker `process()` @0x3ba80
dispatches on an "operation" field (this+0x2c):

    op == 0  ->  RC4 stream cipher      (used by the SJEN file decoder)
    op == 1  ->  AES  (AES_encrypt / AES_set_encrypt_key, ECB blocks)
    op == 2  ->  signature verify       ("Sign is absent/invalid")

The high-level file decoder `SJDecode()` @0x11980:
    while IsSJEN(buf):                    # IsSJEN() @0xe190: first 4 bytes == "SJEN"
        buf = Decrypt(buf, op=0, key=K)   # op hard-coded 0  => RC4
        if IsCompressed(buf):             # IsCompressed() @0xc6c0: first 4 bytes == "SJCF"
            buf = Decompress(buf)         # CSJCompressor, custom (NON-zlib) LZ
    return buf

FILE / BLOB FORMAT
    bytes 0..3   : ASCII magic  "SJEN"          (plaintext, verified, then stripped)
    bytes 4..end : RC4 ciphertext               (the decrypt verifies ONLY the
                                                 4-byte magic, then RC4s the rest)
    => ciphertext starts at OFFSET 4.  The bytes that look like a header in a
       hexdump ("-zw\x14" for Profiles.db, or "\x22\x0c\x29..." for other blobs)
       are simply the first RC4-encrypted bytes, NOT a separate framing.

RC4 (op 0)  -- textbook, verified byte-for-byte in the binary:
    KSA  @0x42ba0 : S[i]=i for i in 0..255 (identity table copied from 0x73850,
                    stored as 256 x int32); then
                    j=(j+S[i]+key[i mod keylen]) & 0xff ; swap(S[i],S[j])
    PRGA @0x42430 : i=(i+1)&0xff ; j=(j+S[i])&0xff ; swap ;
                    out = in XOR S[(S[i]+S[j])&0xff]     (loop-unrolled x8)
    Key comes from CSJEncryptor.this+0x10 (a std::string), i.e. an EXTERNAL
    key supplied by the caller -- it is NOT stored in the blob.

INNER COMPRESSION (after RC4)
    "SJCF" + <4-byte BIG-ENDIAN uncompressed length> + <compressed data>
    The compressor is SJ Labs' own CSJCompressor (decompressor @0xc4a0); it is
    NOT zlib/gzip (mj.so imports no inflate). Only needed if the decrypted
    payload begins with "SJCF".

============================================================================
KEY
============================================================================
The RC4 key is an external std::string. It was NOT recoverable statically from
the supplied artifacts (mj.so, mj_dev, magicJack, magicjack-flash.bin).
The device firmware logs it at runtime ("DBKEY INI:\n%s\n\n" @flash 0x28d765),
confirming it is a computed/derived value rather than a hard-coded ASCII
constant.  Supply the key with --key / --key-hex / --key-file once known
(e.g. captured from that log, or dumped from the running softphone).

Usage:
    python3 sjen_decrypt.py <blob> [-o out] [--key STR | --key-hex HEX | --key-file F]
    python3 sjen_decrypt.py <blob> --try-keys words.txt      # dictionary attempt
"""
import sys, argparse


def rc4(key: bytes, data: bytes) -> bytes:
    if not key:
        raise ValueError("empty RC4 key")
    S = list(range(256))
    j = 0
    kl = len(key)
    for i in range(256):
        j = (j + S[i] + key[i % kl]) & 0xFF
        S[i], S[j] = S[j], S[i]
    i = j = 0
    out = bytearray(len(data))
    for n, b in enumerate(data):
        i = (i + 1) & 0xFF
        j = (j + S[i]) & 0xFF
        S[i], S[j] = S[j], S[i]
        out[n] = b ^ S[(S[i] + S[j]) & 0xFF]
    return bytes(out)


def parse_sjen(blob: bytes):
    """Return (magic_ok, ciphertext). Ciphertext starts at offset 4."""
    if blob[:4] != b"SJEN":
        return False, blob
    return True, blob[4:]


def looks_coherent(pt: bytes) -> bool:
    if pt[:4] in (b"SJCF", b"SQLi") or pt[:2] == b"<?" or pt[:1] == b"[":
        return True
    printable = sum(1 for x in pt if 32 <= x < 127 or x in (9, 10, 13))
    kws = (b"sip", b"URI", b"Proxy", b"Registrar", b"Password", b"Domain",
           b"talk4free", b"Codepage", b"Version", b"Transport", b"5070", b"Auth")
    hits = sum(pt.count(k) for k in kws)
    return printable > 0.75 * len(pt) or hits >= 4


def decrypt(blob: bytes, key: bytes):
    ok, ct = parse_sjen(blob)
    if not ok:
        raise ValueError("not an SJEN blob (missing 'SJEN' magic)")
    pt = rc4(key, ct)
    compressed = pt[:4] == b"SJCF"
    ulen = int.from_bytes(pt[4:8], "big") if compressed else None
    return pt, compressed, ulen


def main():
    ap = argparse.ArgumentParser(description="Decrypt SJ Labs SJEN blobs (RC4).")
    ap.add_argument("blob")
    ap.add_argument("-o", "--out")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--key", help="RC4 key as UTF-8 string")
    g.add_argument("--key-hex", help="RC4 key as hex")
    g.add_argument("--key-file", help="file whose raw bytes are the RC4 key")
    ap.add_argument("--try-keys", help="wordlist; try each line as the key")
    args = ap.parse_args()

    blob = open(args.blob, "rb").read()
    ok, ct = parse_sjen(blob)
    print(f"[i] blob {len(blob)} bytes; SJEN magic: {ok}; ciphertext {len(ct)} bytes (from offset 4)")

    if args.try_keys:
        for raw in open(args.try_keys, "rb"):
            k = raw.rstrip(b"\r\n")
            if not k:
                continue
            try:
                pt = rc4(k, ct)
            except ValueError:
                continue
            if looks_coherent(pt):
                print(f"[+] CANDIDATE key={k!r}")
                print("    ", pt[:80])
        return

    key = None
    if args.key:
        key = args.key.encode()
    elif args.key_hex:
        key = bytes.fromhex(args.key_hex)
    elif args.key_file:
        key = open(args.key_file, "rb").read()

    if key is None:
        sys.exit("[!] No key supplied. The RC4 key is external and not stored in "
                 "the blob; provide it with --key/--key-hex/--key-file (see the "
                 "device 'DBKEY INI:' log), or run --try-keys <wordlist>.")

    pt, compressed, ulen = decrypt(blob, key)
    out = args.out or (args.blob + ".decrypted")
    open(out, "wb").write(pt)
    print(f"[i] wrote {len(pt)} bytes -> {out}")
    if compressed:
        print(f"[i] decrypted payload is SJCF-compressed (uncompressed length {ulen}); "
              f"apply the CSJCompressor decompressor to finish.")
    if not looks_coherent(pt):
        print("[!] Result does not look coherent -- key is probably wrong.")


if __name__ == "__main__":
    main()
