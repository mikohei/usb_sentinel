#!/usr/bin/env bash
# Disarm the kiosk: the recovery path for scripts/kiosk-enable.sh.
#
# Appends Hidden=true to the XDG autostart entry, stops the running display app and resets
# every gsettings key that scripts/kiosk-lockdown.sh set (kiosk_unlock). Meant to be run blind
# from a text console (Ctrl+Alt+F3) or SSH on a 480x320 panel, so it never prompts and prints
# what it does. GDM autologin is left on.
#
# Usage:
#   sudo bash scripts/kiosk-disable.sh                 # display user = the sudo caller
#   sudo DISPLAY_USER=kiosk bash scripts/kiosk-disable.sh
set -euo pipefail

readonly REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly DISPLAY_USER="${DISPLAY_USER:-${SUDO_USER:-}}"

log()  { printf '\n==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

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
# kiosk_unlock and kiosk_autostart_hide; they use warn.
source "${REPO_DIR}/scripts/kiosk-lockdown.sh"

log "Disarming the kiosk for ${DISPLAY_USER}"

# 1. Autostart off (warns and continues when the entry is absent).
kiosk_autostart_hide "${DESKTOP_FILE}"
if [[ -f "${DESKTOP_FILE}" ]]; then
    chown "${DISPLAY_USER}:${DISPLAY_USER}" "${DESKTOP_FILE}"
fi

# 2. Stop the app. Scoped to the display user so an admin's own `uv run ... --help` survives;
#    pkill returns 1 when nothing matched.
if pkill -u "${DISPLAY_USER}" -f usb-sentinel-display; then
    echo "display app stopped"
else
    echo "display app was not running"
fi

# 3. Reset the GNOME lockdown.
if command -v gsettings >/dev/null; then
    kiosk_unlock gsettings_user
else
    warn "gsettings not found; GNOME lockdown left in place"
fi

echo "kiosk disarmed: autostart Hidden=true, lockdown reset. Autologin is still on (/etc/gdm3/custom.conf). Press Ctrl+Alt+F1 (or F2) to return to the desktop."
