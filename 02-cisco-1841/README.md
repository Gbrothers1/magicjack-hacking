# 02 — Cisco 1841

Cisco 1841 Integrated Services Router. Console-connected to this machine via
the DB9 serial port.

## Access
- **Console**: `/dev/ttyS0` @ `9600 8N1`, no flow control
- Connect: `screen /dev/ttyS0 9600` (or `minicom`)
- Console reached privileged EXEC (`Router#`) directly — no console/enable
  password set.

## Hardware / IOS
- Model: Cisco 1841 (rev 7.0), board ID `FTX121120HJ`
- IOS: `12.3(11)YZ2` — `C1841-ADVIPSERVICESK9-M`
- Image: `flash:c1841-advipservicesk9-mz.123-11.YZ2.bin`
- RAM: 128 MB (117760K/13312K) · NVRAM: 191K · CompactFlash: 32 MB
- Interfaces: 2× FastEthernet, 1× Serial (WIC T1-DSU), VPN module

## Config-register note
`show version` reports config register **`0x2142`** (will be `0x2102` at next
reload). `0x2142` boots **ignoring the startup-config** (password-recovery /
bypass mode). In this case the running-config and startup-config are identical
(both 1305 bytes), so nothing was lost — but to make the box boot its saved
config normally, set it back:

```
conf t
 config-register 0x2102
end
write memory
```

## Current config summary
Simple NAT/PAT edge router:
- **FA0/0** — LAN, `<router-lan-ip>/24`, `ip nat inside`, DHCP server pool `LAN`
  (excludes .1–.10, hands out 8.8.8.8 / 1.1.1.1 for DNS)
- **FA0/1** — WAN uplink to OpenWrt, `ip address dhcp`, `ip nat outside`
- **NAT**: `ip nat inside source list 1 interface FastEthernet0/1 overload`
  (PAT), ACL 1 permits `<lan-subnet>/0.0.0.255`
- **Serial0/0/0** — shut down
- HTTP server disabled; VTY `login` (no password set → VTY login would fail,
  console open)

## Files
- `running-config.txt` — running-config (== startup-config)
- `show-version.txt` — `show version` output
