#!/usr/bin/env bash
# One host-side worker per USB disk; invoked by systemd, never directly by udev.
set -euo pipefail

readonly DEVICE="${1:-}"
readonly IMAGE="${USB_SENTINEL_IMAGE:-usb-sentinel:local}"
readonly RESULTS="${USB_SENTINEL_RESULTS:-/var/lib/usb-sentinel/results}"
readonly DB_DIR="${USB_SENTINEL_DB_DIR:-/var/lib/usb-sentinel/db}"
readonly CONFIG="${USB_SENTINEL_CONFIG:-/etc/usb-sentinel/config.toml}"
readonly DATABASE="${USB_SENTINEL_DATABASE:-/var/lib/clamav}"
# From docker.env. Anything other than "rw" (unset, empty, other values) means read-only.
readonly DEVICE_ACCESS="${USB_SENTINEL_DEVICE_ACCESS:-ro}"

# Decide the --device access mode (prints "r" or "rw").
# Writable access needs both opt-ins: USB_SENTINEL_DEVICE_ACCESS=rw in docker.env
# and an active (non-comment) "allow_destructive = true" line in config.toml.
# The capacity (f3) and speed (fio) steps erase the drive, so neither alone is enough.
device_access_mode() {
    local requested="${1:-ro}" config_file="${2:-}"
    local destructive=false
    if [[ -f "${config_file}" ]] &&
        grep -Eq '^[[:space:]]*allow_destructive[[:space:]]*=[[:space:]]*true' "${config_file}"; then
        destructive=true
    fi
    if [[ "${requested}" == rw && "${destructive}" == true ]]; then
        echo "notice: passing USB device read-write (docker.env rw + allow_destructive = true)" >&2
        echo rw
    elif [[ "${requested}" == rw ]]; then
        echo "notice: device stays read-only: allow_destructive = true missing in ${config_file:-<unset>}" >&2
        echo r
    elif [[ "${destructive}" == true ]]; then
        echo "notice: device stays read-only: USB_SENTINEL_DEVICE_ACCESS is not rw in docker.env" >&2
        echo r
    else
        echo r
    fi
}

if [[ $# -ne 1 || ! "${DEVICE}" =~ ^/dev/[a-zA-Z0-9_-]+$ || ! -b "${DEVICE}" ]]; then
    echo "usage: run-docker.sh /dev/<whole-usb-disk>" >&2
    exit 3
fi

# Wait for partition discovery before taking the --device snapshot.
udevadm settle --timeout=30
mapfile -t DEVICES < <(lsblk --noheadings --raw --paths --output PATH "${DEVICE}")
if [[ ${#DEVICES[@]} -eq 0 ]]; then
    echo "error: device disappeared" >&2
    exit 3
fi

# Containers do not see the host mount namespace. Check here before any mount.
# This also rejects the root disk and desktop automounts; never unmount them automatically.
if [[ "$(lsblk --noheadings --nodeps --output TYPE "${DEVICE}" | xargs)" != disk ]]; then
    echo "error: whole disk required" >&2
    exit 2
fi
host_mounts="$(lsblk --noheadings --output MOUNTPOINTS "${DEVICE}")"
if [[ -n "${host_mounts//[[:space:]]/}" ]]; then
    echo "error: host-mounted disk refused: ${DEVICE}" >&2
    exit 2
fi

# Kernel device access is read-only unless both opt-ins are set (see device_access_mode).
# The capacity (f3) and speed (fio) steps need write access and destroy the drive contents.
access_mode="$(device_access_mode "${DEVICE_ACCESS}" "${CONFIG}")"
device_args=()
for path in "${DEVICES[@]}"; do
    [[ -b "${path}" ]] || { echo "error: device disappeared: ${path}" >&2; exit 3; }
    device_args+=(--device "${path}:${path}:${access_mode}")
done

[[ -f "${CONFIG}" && -d "${DATABASE}" && -d "${RESULTS}" && -d "${DB_DIR}" ]] || {
    echo "error: config, ClamAV DB or results directory missing; run install-docker.sh" >&2
    exit 3
}
exec docker run --rm --pull=never --init \
    --name "usb-sentinel-${DEVICE##*/}" \
    --network none --read-only \
    --cap-drop ALL --cap-add SYS_ADMIN --cap-add DAC_OVERRIDE \
    --security-opt no-new-privileges --security-opt apparmor=unconfined \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m \
    --tmpfs /run:rw,nosuid,nodev,noexec,size=16m \
    "${device_args[@]}" \
    --mount type=bind,src=/sys,dst=/sys,readonly \
    --mount type=bind,src=/run/udev,dst=/run/udev,readonly \
    --mount "type=bind,src=${CONFIG},dst=/etc/usb-sentinel/config.toml,readonly" \
    --mount "type=bind,src=${DATABASE},dst=/var/lib/clamav,readonly" \
    --mount "type=bind,src=${RESULTS},dst=/var/lib/usb-sentinel/results" \
    --mount "type=bind,src=${DB_DIR},dst=/var/lib/usb-sentinel/db" \
    "${IMAGE}" inspect "${DEVICE}"
