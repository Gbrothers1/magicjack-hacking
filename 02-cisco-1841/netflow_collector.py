#!/usr/bin/env python3
"""Minimal NetFlow v5 collector — no external deps.

Listens for NetFlow v5 exports (default UDP/2055) and prints each decoded flow.
Flows whose source OR destination matches --target are highlighted and
aggregated, so you can see exactly what one host (e.g. the MagicJack ATA) is
talking to.

Usage:
    python3 netflow_collector.py [--port 2055] [--target <ata-lan-ip>]

Ctrl-C prints a per-destination summary for the target host.
"""
import argparse
import signal
import socket
import struct
import sys
from collections import defaultdict

PROTO = {1: "ICMP", 2: "IGMP", 6: "TCP", 17: "UDP", 47: "GRE", 50: "ESP", 51: "AH"}

HDR = struct.Struct("!HHIIIIBBH")      # 24-byte v5 header
REC = struct.Struct("!IIIHHIIIIHHBBBBHHBBH")  # 48-byte v5 record


def ip(n):
    return socket.inet_ntoa(struct.pack("!I", n))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=2055)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--target", default=None, help="highlight/aggregate this IP")
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.bind, args.port))

    # aggregate: (proto, dst, dstport) -> [pkts, bytes] for flows FROM target
    agg = defaultdict(lambda: [0, 0])
    seen_pkts = 0

    def dump_summary(*_):
        if args.target:
            print(f"\n=== Summary: traffic SENT BY {args.target} "
                  f"({seen_pkts} export packets seen) ===")
            rows = sorted(agg.items(), key=lambda kv: kv[1][1], reverse=True)
            if not rows:
                print("  (no flows sourced from target yet)")
            for (proto, dst, dport), (pk, by) in rows:
                print(f"  {proto:4} -> {dst}:{dport:<5}  {pk:>7} pkts  {by:>10} bytes")
        sys.exit(0)

    signal.signal(signal.SIGINT, dump_summary)
    signal.signal(signal.SIGTERM, dump_summary)

    tgt = "ALL" if not args.target else args.target
    print(f"NetFlow v5 collector on {args.bind}:{args.port}  (target={tgt})")
    print("time-src ->            dst                proto  sport dport   pkts    bytes")

    while True:
        data, addr = sock.recvfrom(8192)
        if len(data) < HDR.size:
            continue
        ver, count = struct.unpack("!HH", data[:4])
        if ver != 5:
            print(f"[!] non-v5 packet (version={ver}) from {addr[0]} — "
                  f"router must export 'version 5'")
            continue
        seen_pkts += 1
        off = HDR.size
        for _ in range(count):
            if off + REC.size > len(data):
                break
            r = REC.unpack(data[off:off + REC.size])
            off += REC.size
            src, dst, _nh, _in, _out, pk, by, _f, _l, sp, dp, _p1, tf, proto = r[:14]
            s, d = ip(src), ip(dst)
            pname = PROTO.get(proto, str(proto))
            hot = args.target and (s == args.target or d == args.target)
            mark = ">>" if hot else "  "
            print(f"{mark} {s:<15} -> {d:<15} {pname:5} {sp:>5} {dp:>5} "
                  f"{pk:>6} {by:>8}")
            if args.target and s == args.target:
                agg[(pname, d, dp)][0] += pk
                agg[(pname, d, dp)][1] += by


if __name__ == "__main__":
    main()
