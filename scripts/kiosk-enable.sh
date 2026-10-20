#!/usr/bin/env bash
# Arm the kiosk: final step after scripts/install-all.sh and the manual tuning.
#
# Enables the XDG autostart entry (removes Hidden=true) and applies the GNOME lockdown of
# scripts/kiosk-lockdown.sh to the display user. Run this only once the display resolution is
# 480x320 and the ports are calibrated: afterwards the app covers the whole screen, hides the
# cursor and takes focus back from every other window, and GNOME has no shortcuts left except
# Ctrl+Alt+F1..F12. The reversal is scripts/kiosk-disable.sh (works from a text console).
#
# Usage:
#   sudo bash scripts/kiosk-enable.sh                  # display user = the sudo caller
#   sudo DISPLAY_USER=kiosk bash scripts/kiosk-enable.sh
#   sudo INSTALL_YES=1 bash scripts/kiosk-enable.sh    # skip the confirmation
#   sudo KIOSK_FORCE=1 bash scripts/kiosk-enable.sh    # arm even if 480x320 is not confirmed
#
# The resolution check reads the display user's ~/.config/monitors.xml (written by
# Settings -> Displays, readable from a TTY and under Wayland), then xrandr when the caller has
# an X11 session. Otherwise the script refuses unless KIOSK_FORCE=1.
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
readonly USER_UID="$(id -u "${DISPLAY_USER}")"
readonly DESKTOP_FILE="${USER_HOME}/.config/autostart/usb-sentinel-display.desktop"

# Use the running session bus when the user is logged in, otherwise a throwaway dbus session.
gsettings_user() {
    local uid="${USER_UID}"
    if [[ -S "/run/user/${uid}/bus" ]]; then
        sudo -u "${DISPLAY_USER}" env DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${uid}/bus" gsettings "$@"
    else
        sudo -u "${DISPLAY_USER}" dbus-run-session -- gsettings "$@"
    fi
}
# kiosk_lockdown and kiosk_autostart_show; they use warn.
source "${REPO_DIR}/scripts/kiosk-lockdown.sh"

# check_resolution: 0 when a 480x320 mode is confirmed for the display user.
check_resolution() {
    local monitors="${USER_HOME}/.config/monitors.xml"
    if [[ -f "${monitors}" ]] \
        && tr -d ' \n' < "${monitors}" | grep -q '<width>480</width><height>320</height>'; then
        echo "resolution: 480x320 found in ${monitors}"
        return 0
    fi
    if [[ "${XDG_SESSION_TYPE:-}" == x11 && -n "${DISPLAY:-}" ]] && command -v xrandr >/dev/null; then
        if sudo -u "${DISPLAY_USER}" env DISPLAY="${DISPLAY}" xrandr --current 2>/dev/null \
            | grep -q '^\S\+ connected.* 480x320'; then
            echo "resolution: 480x320 reported by xrandr"
            return 0
        fi
    fi
    return 1
}

log "Arming the kiosk for ${DISPLAY_USER}"
[[ -f "${DESKTOP_FILE}" ]] || die "autostart entry missing; run scripts/install-all.sh first"

if ! check_resolution; then
    warn "could not confirm a 480x320 display (no match in ${USER_HOME}/.config/monitors.xml; Wayland does not expose xrandr)"
    warn "on the 3.5-inch panel a larger mode makes GNOME unreadable and the kiosk unrecoverable from the GUI"
    [[ "${KIOSK_FORCE:-0}" == 1 ]] \
        || die "fix the resolution first (frontend/README.md 'Display setup' step 1) or re-run with KIOSK_FORCE=1"
    warn "KIOSK_FORCE=1: continuing without a confirmed resolution"
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

command -v gsettings >/dev/null || die "gsettings not found"
# Without a session bus this starts one dbus session per call (about a minute in total).
kiosk_lockdown gsettings_user

kiosk_autostart_show "${DESKTOP_FILE}"
# sed -i may recreate the file as root.
chown "${DISPLAY_USER}:${DISPLAY_USER}" "${DESKTOP_FILE}"

echo "kiosk armed; reboot (sudo reboot) to start the display. Undo: sudo bash ${REPO_DIR}/scripts/kiosk-disable.sh"
