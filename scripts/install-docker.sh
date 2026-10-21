#!/usr/bin/env bash
# Run on Ubuntu 26 / Raspberry Pi 4 after Docker Engine has been installed.
set -euo pipefail
readonly REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly APP_DIR="/opt/usb-sentinel"
readonly IMAGE="${USB_SENTINEL_IMAGE:-usb-sentinel:local}"
# 1 (default): this host may erase (allow_destructive = true, USB_SENTINEL_DEVICE_ACCESS=rw);
# the operator still has to switch the display to "Scan + erase". 0: scan-only station.
readonly INSTALL_ALLOW_ERASE="${INSTALL_ALLOW_ERASE:-1}"

if [[ ${EUID} -ne 0 || "$(uname -s)" != Linux ]]; then
    echo "error: run with sudo on the Ubuntu host" >&2
    exit 1
fi
case "${INSTALL_ALLOW_ERASE}" in
    0|1) ;;
    *) echo "error: INSTALL_ALLOW_ERASE must be 0 or 1" >&2; exit 1 ;;
esac
source "${REPO_DIR}/scripts/erase-setting.sh"
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
install -m 0755 "${REPO_DIR}/scripts/clock-floor.sh" "${APP_DIR}/scripts/clock-floor.sh"
if [[ ! -f /etc/usb-sentinel/config.toml ]]; then
    install -m 0644 "${REPO_DIR}/deploy/config.example.toml" /etc/usb-sentinel/config.toml
fi
# Existing docker.env and config.toml keep every key except the two erase keys, which
# apply_erase_setting (scripts/erase-setting.sh) sets from INSTALL_ALLOW_ERASE.
if [[ ! -f /etc/usb-sentinel/docker.env ]]; then
    fresh_access=ro
    [[ "${INSTALL_ALLOW_ERASE}" == 1 ]] && fresh_access=rw
    docker_env_write /etc/usb-sentinel/docker.env "${IMAGE}" "${fresh_access}"
fi
apply_erase_setting /etc/usb-sentinel "${INSTALL_ALLOW_ERASE}"
install -m 0644 "${REPO_DIR}/deploy/usb-sentinel-docker@.service" \
    /etc/systemd/system/usb-sentinel-docker@.service
# Remove only this project's legacy rule to prevent two inspections of the same disk.
rm -f /etc/udev/rules.d/99-usb-sentinel.rules
install -m 0644 "${REPO_DIR}/deploy/99-usb-sentinel-docker.rules" \
    /etc/udev/rules.d/99-usb-sentinel-docker.rules
# No RTC and no network: keep the clock from going back at each boot (scripts/clock-floor.sh).
install -m 0644 "${REPO_DIR}/deploy/usb-sentinel-clock.service" /etc/systemd/system/usb-sentinel-clock.service
systemctl daemon-reload
systemctl enable --now usb-sentinel-clock.service
udevadm control --reload
echo "Display users need: sudo usermod -aG usb-sentinel <user> (then log in again)."
echo "Installed. Fetch the ClamAV DB (sudo bash scripts/update-clamav.sh), then insert a USB disk."
echo "Erasing checks run only when the display is switched to \"Scan + erase\" (M key or the mode button)."
