#!/usr/bin/env bash
# GO LIVE — redirect the MagicJack ATA's SIP to our Asterisk.
#
# This is the OpenWrt-specific recipe. For the router-agnostic explanation
# (iptables / nftables / Cisco / pfSense / any router) see REDIRECT.md.
#
# The ATA sends SIP to MagicJack's proxy 216.234.65.40:5070. This adds two
# nftables rules on the OpenWrt gateway (fw4):
#   * DNAT  : rewrite that destination to our Asterisk <pbx-ip>:5070
#   * MASQUERADE: rewrite the source so replies return via OpenWrt. The ATA and
#     the server share subnet <server-subnet>/24, so without this the reply path is
#     asymmetric (goes direct, bypassing OpenWrt) and the ATA rejects it.
# Rules are TRANSIENT (not saved to uci) — rollback.sh clears them, and they also
# vanish on a firewall reload / reboot. Media (RTP) needs no redirect: Asterisk is
# the media endpoint (direct_media=no), so it rewrites the media address itself.
set -euo pipefail
GW=${GW:-<gw-ip>}
SERVER=${SERVER:-<pbx-ip>}
PROXY=${PROXY:-216.234.65.40}
ssh root@"$GW" "
nft add rule inet fw4 dstnat ip daddr $PROXY udp dport 5070 dnat ip to $SERVER:5070 comment \\\"mjsip\\\"
nft add rule inet fw4 srcnat ip daddr $SERVER udp dport 5070 masquerade comment \\\"mjsip\\\"
"
echo "REDIRECT LIVE. The ATA re-registers to $SERVER within ~30s (or power-cycle the ATA to force it)."
echo "Verify:  sudo asterisk -rx 'pjsip show contacts'    # expect EXXXXXXXXXXXX with a contact"
echo "Revert:  ./rollback.sh"
