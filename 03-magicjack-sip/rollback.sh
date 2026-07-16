#!/usr/bin/env bash
# Revert the redirect — restore the ATA's normal path to MagicJack.
# Deletes the transient "mjsip" nft rules from the fw4 dstnat/srcnat chains.
set -euo pipefail
GW=${GW:-<gw-ip>}
ssh root@"$GW" 'sh -s' <<'EOF'
for chain in dstnat srcnat; do
  for h in $(nft -a list chain inet fw4 $chain 2>/dev/null | grep 'comment "mjsip"' | grep -oE 'handle [0-9]+' | sed 's/handle //'); do
    nft delete rule inet fw4 $chain handle $h
  done
done
EOF
echo "Redirect removed; ATA restored to MagicJack. (A firewall reload/reboot also clears it.)"
