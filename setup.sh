#!/usr/bin/env bash
#
# setup.sh — get a hacked magicJack HOME from "plugged into USB with a phone in the
# RJ11 jack" to "registered as a SIP endpoint on YOUR PBX and working."
#
# What it wires up (exactly how the working system is built):
#   1. tj_linepower.py   — drives the phone LINE over /dev/hidraw as root
#                          (power/hook/ring/DTMF). Pure Python stdlib, no deps.
#   2. baresip           — a standard SIP softphone that carries the AUDIO via ALSA
#                          at the TigerJet's native 8 kHz mu-law and registers to YOUR
#                          PBX. Because it's a plain SIP client, ANY PBX works:
#                          Asterisk, FreePBX, FreeSWITCH, 3CX, ...
#   3. mj-fxs-bridge.py  — a root daemon that gives the phone its FXS behaviour:
#                          off-hook dial tone, register-based DTMF dialing, ring the
#                          physical bell on inbound, hangups both ways, reorder tone.
#                          It reads hook/DTMF over /dev/hidraw and drives baresip over
#                          its ctrl_tcp socket (127.0.0.1:4444).
#
# Target: Debian/Ubuntu (uses apt + systemd). On other distros it prints the manual
# steps and exits.
#
# This installs packages and creates systemd services — run it on the machine that
# will host the magicJack, NOT a production box you care about.
#
set -euo pipefail

# --------------------------------------------------------------------------- helpers
say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }
ask()  { # ask "prompt" "default" -> echoes the answer
  local prompt="$1" def="${2:-}" ans
  if [ -n "$def" ]; then read -r -p "$prompt [$def]: " ans || true; echo "${ans:-$def}"
  else read -r -p "$prompt: " ans || true; echo "$ans"; fi
}

REPO_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
TOOLS_DIR="$REPO_DIR/06-magicjack-usb-tigerjet/tools"
SIP_DIR="$REPO_DIR/03-magicjack-sip"
INSTALL_DIR="/opt/magicjack-fxs"
BARESIP_CFG_DIR="/etc/magicjack-fxs/baresip"
REORDER_WAV="/usr/local/share/mj-fxs-reorder.wav"

# --------------------------------------------------------------------------- 1. preflight
say "magicJack FXS-over-USB installer"

# must be root — re-exec with sudo if we can
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    say "re-running with sudo..."; exec sudo -E bash "$0" "$@"
  fi
  die "must run as root (try: sudo ./setup.sh)"
fi

# device present?
if command -v lsusb >/dev/null 2>&1; then
  lsusb | grep -qi '06e6:c200' || die "magicJack (TigerJet 06e6:c200) not found on USB.
       Plug the magicJack HOME into a USB port and re-run. (Check with: lsusb | grep 06e6)"
  ok "found magicJack on USB (TigerJet 06e6:c200)"
else
  warn "lsusb not available; skipping the USB presence check"
fi

# Debian/Ubuntu?
if ! command -v apt-get >/dev/null 2>&1; then
  warn "this installer targets Debian/Ubuntu (apt + systemd)."
  cat <<'EOF'

  Manual steps for other distros:
    1. Install:  baresip  alsa-utils  python3  curl
    2. Copy 06-magicjack-usb-tigerjet/tools/tj_linepower.py and
       03-magicjack-sip/mj-fxs-bridge.py into one directory (e.g. /opt/magicjack-fxs/).
    3. Find your TigerJet ALSA card number:  grep -i tigerjet /proc/asound/cards
    4. Write a baresip config (see this script's write_baresip_config for the exact
       contents) with audio bound to plughw:<N>,0 and ctrl_tcp on 127.0.0.1:4444.
    5. Run baresip as a user in the 'audio' group, and mj-fxs-bridge.py as root.
EOF
  exit 1
fi

# --------------------------------------------------------------------------- 2. packages
say "installing dependencies (baresip alsa-utils python3 curl)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y baresip alsa-utils python3 curl
ok "packages installed"

# --------------------------------------------------------------------------- 3. detect ALSA card
say "detecting the TigerJet ALSA sound card"
CARD_NUM=""
if [ -r /proc/asound/cards ]; then
  # index lines look like:  " 1 [TigerJet       ]: USB-Audio - ..."
  CARD_NUM="$(awk '/^[[:space:]]*[0-9]+[[:space:]]*\[/ && /TigerJet/ {print $1; exit}' /proc/asound/cards)"
fi
[ -n "$CARD_NUM" ] || die "could not find a TigerJet ALSA card in /proc/asound/cards.
       Is the magicJack enumerated as a USB sound card? (aplay -l should list 'TigerJet')"
ok "TigerJet is ALSA card $CARD_NUM  (using plughw:$CARD_NUM,0)"

# --------------------------------------------------------------------------- 4. PBX details
say "your SIP PBX — the magicJack will register to it as a normal SIP endpoint"
cat <<EOF

  This works with ANY SIP PBX (Asterisk / FreePBX / FreeSWITCH / 3CX / ...).
  You must ALSO create a matching extension/endpoint on that PBX: same username +
  password, codecs ulaw/alaw (pcmu/pcma). We'll print an example after install.

EOF
PBX_HOST="$(ask 'PBX host / IP')"
[ -n "$PBX_HOST" ] || die "PBX host is required"
PBX_PORT="$(ask 'PBX SIP port' '5060')"
SIP_USER="$(ask 'extension username (the SIP user this phone registers as)')"
[ -n "$SIP_USER" ] || die "SIP username is required"
SIP_PASS="$(ask 'extension password')"

# --------------------------------------------------------------------------- 5. service user
say "choosing the service user for baresip (needs the 'audio' group)"
SVC_USER="${SUDO_USER:-}"
SVC_USER="$(ask 'run baresip as which user' "${SVC_USER:-mjfxs}")"
if ! id "$SVC_USER" >/dev/null 2>&1; then
  say "creating system user '$SVC_USER'"
  useradd --system --create-home --shell /usr/sbin/nologin "$SVC_USER"
fi
usermod -aG audio "$SVC_USER"
ok "baresip will run as '$SVC_USER' (in group audio); the bridge daemon runs as root"

# --------------------------------------------------------------------------- 6. install files
say "installing to $INSTALL_DIR and $BARESIP_CFG_DIR"
install -d "$INSTALL_DIR" "$BARESIP_CFG_DIR"

# the two Python parts, side by side so the daemon imports tj_linepower from its own dir
install -m 0755 "$TOOLS_DIR/tj_linepower.py"    "$INSTALL_DIR/tj_linepower.py"
install -m 0755 "$SIP_DIR/mj-fxs-bridge.py"     "$INSTALL_DIR/mj-fxs-bridge.py"
# the reorder / fast-busy tone the daemon plays when the far end hangs up first
install -d "$(dirname "$REORDER_WAV")"
install -m 0644 "$SIP_DIR/mj-fxs-reorder.wav"   "$REORDER_WAV"
ok "installed tj_linepower.py, mj-fxs-bridge.py, reorder tone"

# baresip module path differs across distros — locate it
BARESIP_MODDIR="$(dirname "$(find /usr/lib /usr/local/lib -name 'g711.so' -path '*baresip*' 2>/dev/null | head -n1)")"
[ -n "$BARESIP_MODDIR" ] && [ -d "$BARESIP_MODDIR" ] || BARESIP_MODDIR="/usr/lib/baresip/modules"

say "writing baresip config ($BARESIP_CFG_DIR)"
cat > "$BARESIP_CFG_DIR/config" <<EOF
# magicJack FXS baresip config (generated by setup.sh)
sip_listen        127.0.0.1:5065
audio_player      alsa,plughw:$CARD_NUM,0
audio_source      alsa,plughw:$CARD_NUM,0
audio_alert       alsa,plughw:$CARD_NUM,0
ausrc_srate       8000
auplay_srate      8000
ctrl_tcp_listen   127.0.0.1:4444
module_path       $BARESIP_MODDIR
#module           stdio.so            ; disabled: needs a tty, breaks headless service
module            g711.so
module            alsa.so
module_app        ctrl_tcp.so         ; enabled: the FXS bridge drives baresip through this
EOF

# one account line: register SIP_USER@PBX_HOST, manual answer (daemon answers on off-hook)
cat > "$BARESIP_CFG_DIR/accounts" <<EOF
<sip:$SIP_USER@$PBX_HOST:$PBX_PORT;transport=udp>;auth_pass=$SIP_PASS;regint=60;answermode=manual;audio_codecs=pcmu,pcma;ptime=20;medianat=;mediaenc=
EOF
chmod 0640 "$BARESIP_CFG_DIR/accounts"
chown -R "$SVC_USER":"$(id -gn "$SVC_USER")" "$BARESIP_CFG_DIR"
ok "baresip config + account written"

# --------------------------------------------------------------------------- systemd units
say "writing systemd services"
cat > /etc/systemd/system/mj-baresip.service <<EOF
[Unit]
Description=magicJack USB FXS softphone (baresip, native 8kHz ulaw over the TigerJet card)
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=$SVC_USER
Group=audio
SupplementaryGroups=audio
ExecStart=/usr/bin/baresip -f $BARESIP_CFG_DIR
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/mj-fxs-bridge.service <<EOF
[Unit]
Description=magicJack USB FXS bridge (TigerJet hook/ring/DTMF <-> baresip)
After=mj-baresip.service
Wants=mj-baresip.service

[Service]
Type=simple
User=root
Environment=MJ_CARD=plughw:$CARD_NUM,0
Environment=MJ_REORDER_WAV=$REORDER_WAV
ExecStart=/usr/bin/python3 $INSTALL_DIR/mj-fxs-bridge.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now mj-baresip.service
systemctl enable --now mj-fxs-bridge.service
ok "services enabled and started"

# --------------------------------------------------------------------------- 7. verify + report
say "powering the phone line and checking registration"
python3 "$INSTALL_DIR/tj_linepower.py" on || warn "line power command failed (check dmesg / hidraw perms)"
sleep 3

REGISTERED="unknown"
# ask baresip over ctrl_tcp whether the account is registered (netstring: LEN:JSON,)
if command -v curl >/dev/null 2>&1; then :; fi
REQ='{"command":"reginfo","params":""}'
RESP="$(printf '%s:%s,' "${#REQ}" "$REQ" | timeout 3 bash -c 'cat >/dev/tcp/127.0.0.1/4444; cat <&0' 2>/dev/null || true)"
if printf '%s' "$RESP" | grep -qiE 'ok|registered|200'; then REGISTERED="yes"; fi

echo
if [ "$REGISTERED" = "yes" ]; then
  ok "baresip appears REGISTERED to $PBX_HOST:$PBX_PORT as '$SIP_USER'"
else
  warn "could not confirm registration automatically — check the logs (below)."
  warn "most common cause: the matching extension isn't created on your PBX yet."
fi

cat <<EOF

------------------------------------------------------------------------------
 magicJack FXS-over-USB is installed.

 NEXT: create a SIP extension on your PBX that matches what this phone registers as
   user:     $SIP_USER
   password: (the one you entered)
   codecs:   ulaw/alaw (pcmu, pcma)

   Asterisk (pjsip.conf) example:
     [$SIP_USER]
     type=endpoint
     context=from-internal
     disallow=all
     allow=ulaw,alaw
     auth=$SIP_USER-auth
     aors=$SIP_USER
     [$SIP_USER-auth]
     type=auth
     auth_type=userpass
     username=$SIP_USER
     password=<the password you entered>
     [$SIP_USER]
     type=aor
     max_contacts=1

   FreePBX / FreeSWITCH / 3CX / others: create a normal SIP extension with this
   username + password and ulaw enabled. Nothing magicJack-specific is required.

 TRY IT:
   * Lift the handset -> you should hear firmware dial tone.
   * Dial an extension on your PBX -> two-way audio.
   * Call '$SIP_USER' from your PBX -> the magicJack's bell rings; lift to answer.

 LOGS:
   sudo journalctl -u mj-baresip -f
   sudo journalctl -u mj-fxs-bridge -f

 UNINSTALL:
   sudo systemctl disable --now mj-fxs-bridge.service mj-baresip.service
   sudo rm /etc/systemd/system/mj-fxs-bridge.service /etc/systemd/system/mj-baresip.service
   sudo systemctl daemon-reload
   sudo rm -rf $INSTALL_DIR $BARESIP_CFG_DIR $REORDER_WAV
------------------------------------------------------------------------------
EOF
