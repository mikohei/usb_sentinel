#!/usr/bin/env bash
# Arm the kiosk by hand. scripts/install-all.sh does this itself at the end (step 10/10);
# this script is for installs run with INSTALL_KIOSK=0 or answered "no", and for re-arming
# after scripts/kiosk-disable.sh.
#
# Enables the XDG autostart entry (removes Hidden=true) and applies the GNOME lockdown of
# scripts/kiosk-lockdown.sh to the display user (kiosk_arm). Afterwards the app covers the
# whole screen, hides the cursor and takes focus back from every other window, and GNOME has no
# shortcuts left except Ctrl+Alt+F1..F12. The reversal is scripts/kiosk-disable.sh (works from
# a text console).
#
# The app scales its 480x320 design to the actual screen, so the resolution check is
# informational only: it reads the display user's ~/.config/monitors.xml and warns when no
# 480x320 mode is found, but never refuses. KIOSK_FORCE is accepted and ignored (kept so old
# runbooks work).
#
# Usage:
#   sudo bash scripts/kiosk-enable.sh                  # display user = the sudo caller
#   sudo DISPLAY_USER=kiosk bash scripts/kiosk-enable.sh
#   sudo INSTALL_YES=1 bash scripts/kiosk-enable.sh    # skip the confirmation
set -euo pipefail

readonly REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly DISPLAY_USER="${DISPLAY_USER:-${SUDO_USER:-}}"
readonly INSTALL_YES="${INSTALL_YES:-0}"

log()  { printf '\n==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

confirm() {
    # Ask a y/N question. INSTALL_YES=1 answers yes without prompting.
    if [[ "${INSTALL_YES}" == 1 ]]; then
        return 0
    fi
    local answer
    read -r -p "$1 [y/N] " answer
    [[ "${answer}" =~ ^[Yy]$ ]]
}

[[ ${EUID} -eq 0 ]] || die "run with sudo"
[[ -n "${DISPLAY_USER}" ]] || die "cannot determine the display user; set DISPLAY_USER=<user>"
id "${DISPLAY_USER}" >/dev/null 2>&1 || die "user ${DISPLAY_USER} does not exist"
readonly USER_HOME="$(getent passwd "${DISPLAY_USER}" | cut -d: -f6)"
readonly DESKTOP_FILE="${USER_HOME}/.config/autostart/usb-sentinel-display.desktop"

# kiosk_resolution_hint and kiosk_arm (which uses kiosk_gsettings_user); they use warn and die.
source "${REPO_DIR}/scripts/kiosk-lockdown.sh"

log "Arming the kiosk for ${DISPLAY_USER}"
[[ -f "${DESKTOP_FILE}" ]] || die "autostart entry missing; run scripts/install-all.sh first"

kiosk_resolution_hint "${USER_HOME}"
if [[ "${KIOSK_FORCE:-0}" == 1 ]]; then
    warn "KIOSK_FORCE is no longer needed; the app scales to the screen"
fi

cat <<EOF

This enables autostart and applies the GNOME lockdown (scripts/kiosk-lockdown.sh:
every shortcut except Ctrl+Alt+F1..F12 is cleared, the dock is emptied).
Once armed, the app covers the whole screen, hides the cursor and takes focus back
from every other window. The only ways out are:
  - Ctrl+Alt+F3 (text console, password) or SSH, then
      pkill -f usb-sentinel-display                      stop the app until the next login
      sudo bash ${REPO_DIR}/scripts/kiosk-disable.sh     undo autostart and lockdown
EOF
confirm "Arm the kiosk for ${DISPLAY_USER} now?" || die "cancelled; nothing changed"

# Without a session bus this starts one dbus session per call (about a minute in total).
kiosk_arm "${DISPLAY_USER}" "${DESKTOP_FILE}"

echo "kiosk armed; reboot (sudo reboot) to start the display. Undo: sudo bash ${REPO_DIR}/scripts/kiosk-disable.sh"
