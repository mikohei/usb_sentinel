#!/usr/bin/env bash
# Run on Ubuntu 26 / Raspberry Pi 4 after Docker Engine has been installed.
set -euo pipefail
readonly REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly APP_DIR="/opt/usb-sentinel"
readonly IMAGE="${USB_SENTINEL_IMAGE:-usb-sentinel:local}"

if [[ ${EUID} -ne 0 || "$(uname -s)" != Linux ]]; then
    echo "error: run with sudo on the Ubuntu host" >&2
    exit 1
fi
for command in docker udevadm systemctl lsblk systemd-tmpfiles; do
    command -v "${command}" >/dev/null || { echo "error: missing ${command}" >&2; exit 1; }
done
systemctl enable --now docker.service
# Build first so a failed build cannot activate a broken USB handler.
docker build --tag "${IMAGE}" "${REPO_DIR}/backend"
install -d -m 0755 "${APP_DIR}/scripts" /etc/usb-sentinel /var/lib/clamav
# The display user reads results via the usb-sentinel group.
# install -d also re-applies the mode and group on re-run.
getent group usb-sentinel >/dev/null || groupadd --system usb-sentinel
# Control directory for the display's inspection-mode file: tmpfs, root:usb-sentinel 2770,
# recreated empty at every boot (a missing file means scan only).
install -m 0644 "${REPO_DIR}/deploy/usb-sentinel.tmpfiles.conf" /etc/tmpfiles.d/usb-sentinel.conf
systemd-tmpfiles --create /etc/tmpfiles.d/usb-sentinel.conf
install -d -m 0750 -g usb-sentinel /var/lib/usb-sentinel/results
install -d -m 2770 -g usb-sentinel /var/lib/usb-sentinel/db
install -m 0755 "${REPO_DIR}/scripts/run-docker.sh" "${APP_DIR}/scripts/run-docker.sh"
if [[ ! -f /etc/usb-sentinel/config.toml ]]; then
    install -m 0644 "${REPO_DIR}/deploy/config.example.toml" /etc/usb-sentinel/config.toml
fi
# Existing docker.env is kept as-is. Reinstalls therefore never add or change its keys;
# run-docker.sh treats a missing USB_SENTINEL_DEVICE_ACCESS as "ro".
if [[ ! -f /etc/usb-sentinel/docker.env ]]; then
    cat > /etc/usb-sentinel/docker.env <<EOF
USB_SENTINEL_IMAGE=${IMAGE}
# Host ceiling for USB device access passed to docker --device: ro (default) or rw.
# rw lets the capacity (f3) and speed (fio) checks ERASE the drive contents.
# The device becomes writable only when this is rw AND config.toml has
# allow_destructive = true AND the display is switched to "Scan + erase" when the
# drive is inserted. Any one of the three missing keeps the device read-only.
USB_SENTINEL_DEVICE_ACCESS=ro
EOF
    chmod 0644 /etc/usb-sentinel/docker.env
fi
install -m 0644 "${REPO_DIR}/deploy/usb-sentinel-docker@.service" \
    /etc/systemd/system/usb-sentinel-docker@.service
# Remove only this project's legacy rule to prevent two inspections of the same disk.
rm -f /etc/udev/rules.d/99-usb-sentinel.rules
install -m 0644 "${REPO_DIR}/deploy/99-usb-sentinel-docker.rules" \
    /etc/udev/rules.d/99-usb-sentinel-docker.rules
systemctl daemon-reload
udevadm control --reload
echo "Display users need: sudo usermod -aG usb-sentinel <user> (then log in again)."
echo "Installed. Update ClamAV DB using the README steps, then insert a USB disk."
echo "Erasing checks run only when the display's mode button is set to \"Scan + erase\"."
