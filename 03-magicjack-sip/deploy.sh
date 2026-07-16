#!/usr/bin/env bash
# Install these configs into /etc/asterisk and reload Asterisk.
# Backs up the original /etc/asterisk files once, into /etc/asterisk/orig-backup.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DST=/etc/asterisk
BK="$DST/orig-backup"
sudo mkdir -p "$BK"
for f in pjsip.conf extensions.conf rtp.conf voicemail.conf; do
  if [ -f "$DST/$f" ] && [ ! -f "$BK/$f" ]; then sudo cp "$DST/$f" "$BK/$f"; fi
  sudo install -o asterisk -g asterisk -m 640 "$SRC/$f" "$DST/$f"
done
# Debian autoloads app_voicemail_imap.so, which breaks file-based voicemail.
# Force the standard module so voicemail.conf mailboxes actually work.
if ! sudo grep -q 'noload => app_voicemail_imap.so' /etc/asterisk/modules.conf; then
  sudo sed -i '/^autoload=yes/a noload => app_voicemail_imap.so\nnoload => app_voicemail_odbc.so' /etc/asterisk/modules.conf
  sudo systemctl restart asterisk
else
  sudo asterisk -rx "core reload" >/dev/null 2>&1 || sudo systemctl restart asterisk
fi
echo "Deployed + reloaded. Verify with:"
echo "  sudo asterisk -rx 'pjsip show endpoints'"
echo "  sudo asterisk -rx 'pjsip show registrations'   # voipms should be Registered once creds are set"
