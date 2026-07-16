#!/usr/bin/env python3
"""
tj_modem.py — drive the analog modem attached to the magicJack FXS line, over the network.

The modem (a USR Courier on the Cisco-1841 `line aux 0`, `modem InOut`, 115200, hw flow) is reached
by reverse-telnet at <router-wan-ip>:2001. This gives a hands-free stand-in for a human at the handset:
take the line off-hook / on-hook, dial DTMF, detect dial tone, decode Caller-ID — all scriptable, so
register/audio experiments on the FXS port can be automated instead of coordinated with a person.

It pairs with the register tools: drive the modem here while reading the chip with tj_dumpregs.py /
tj_armreg.py (different devices — modem over TCP, magicJack over hidraw — so no contention).

Verified 2026-07-15/16: ATH1=off-hook (chip sees reg0x14 bit31 set, a Courier draws real loop current),
ATH0=on-hook, ATDT<digits>;=send DTMF, ATX4+ATDT detects dial tone (NO DIALTONE if absent).

Usage:
  python3 tj_modem.py at "ATI"                 # send an AT command, print the reply
  python3 tj_modem.py offhook                  # ATH1  (close the loop)
  python3 tj_modem.py onhook                   # ATH0
  python3 tj_modem.py dtmf 12345               # ATDT<digits>; then hang up (send DTMF onto the line)
  python3 tj_modem.py raw "ATX3" "ATDT5551212;" "ATH0"   # send several commands in order
  [--host H] [--port P]  (defaults <router-wan-ip> 2001)

No root needed (pure TCP). Leaves the modem on-hook.
"""
import socket, sys, time, argparse

DEF_HOST, DEF_PORT = '<router-wan-ip>', 2001

def _strip_iac(sock, data):
    """Minimal telnet IAC handling: refuse all options (DO->WONT, WILL->DONT), drop the triplets."""
    out = bytearray(); i = 0
    while i < len(data):
        if data[i] == 0xff and i + 2 < len(data):
            cmd, opt = data[i+1], data[i+2]
            if cmd == 0xfd:   sock.sendall(bytes([0xff, 0xfc, opt]))   # DO   -> WONT
            elif cmd == 0xfb: sock.sendall(bytes([0xff, 0xfe, opt]))   # WILL -> DONT
            i += 3
        elif data[i] == 0xff and i + 1 < len(data):
            i += 2
        else:
            out.append(data[i]); i += 1
    return bytes(out)

class Modem:
    def __init__(s, host=DEF_HOST, port=DEF_PORT, timeout=5):
        s.sock = socket.create_connection((host, port), timeout=timeout)
        s.sock.settimeout(0.6)
    def drain(s):
        buf = b''
        try:
            while True:
                d = s.sock.recv(4096)
                if not d: break
                buf += d
        except socket.timeout:
            pass
        return _strip_iac(s.sock, buf).decode('ascii', 'replace')
    def cmd(s, c, wait=1.0):
        s.drain()
        s.sock.sendall((c + '\r').encode())
        time.sleep(wait)
        return s.drain().strip()
    def offhook(s): return s.cmd('ATH1', 0.5)
    def onhook(s):  return s.cmd('ATH0', 0.5)
    def dtmf(s, digits):
        s.cmd('ATX3', 0.4)                       # blind dial (ignore dial-tone detect)
        r = s.cmd(f'ATDT{digits};', 2.5)         # send digits as DTMF, stay off-hook
        s.cmd('ATH0', 0.4)
        return r
    def close(s):
        try: s.sock.close()
        except Exception: pass

def main():
    ap = argparse.ArgumentParser(description="Drive the magicJack-line modem over reverse-telnet")
    ap.add_argument('action', choices=['at', 'offhook', 'onhook', 'dtmf', 'raw'])
    ap.add_argument('args', nargs='*')
    ap.add_argument('--host', default=DEF_HOST)
    ap.add_argument('--port', type=int, default=DEF_PORT)
    a = ap.parse_args()
    m = Modem(a.host, a.port)
    try:
        m.drain()
        if a.action == 'at':
            print(m.cmd(a.args[0] if a.args else 'AT', 1.0))
        elif a.action == 'offhook':
            print(m.offhook())
        elif a.action == 'onhook':
            print(m.onhook())
        elif a.action == 'dtmf':
            print(m.dtmf(a.args[0] if a.args else '1234567890'))
        elif a.action == 'raw':
            for c in a.args:
                print(f"> {c}\n{m.cmd(c, 1.0)}")
    finally:
        m.close()

if __name__ == '__main__':
    main()
