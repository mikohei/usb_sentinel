#!/usr/bin/env bash
# One host-side worker per USB disk; invoked by systemd, never directly by udev.
#
# The script is split into functions so that backend/tests can source it and call them with
# the system bash. Functions called by tests use only bash 3.2 and BSD find/head/tail/wc
# features; `mapfile` stays inside main.
set -euo pipefail

readonly DEVICE="${1:-}"
readonly IMAGE="${USB_SENTINEL_IMAGE:-usb-sentinel:local}"
readonly RESULTS="${USB_SENTINEL_RESULTS:-/var/lib/usb-sentinel/results}"
readonly DB_DIR="${USB_SENTINEL_DB_DIR:-/var/lib/usb-sentinel/db}"
readonly CONFIG="${USB_SENTINEL_CONFIG:-/etc/usb-sentinel/config.toml}"
readonly DATABASE="${USB_SENTINEL_DATABASE:-/var/lib/clamav}"
# From docker.env. Anything other than "rw" (unset, empty, other values) means read-only.
readonly DEVICE_ACCESS="${USB_SENTINEL_DEVICE_ACCESS:-ro}"
# Mode file written by the display (tmpfs directory created by systemd-tmpfiles at boot).
# No environment override: tests pass these values as arguments.
readonly CONTROL_FILE=/run/usb-sentinel-control/mode
readonly CONTROL_OWNER=root
readonly CONTROL_GROUP=usb-sentinel
# `full` is honoured only while 0 <= now - stamp <= MODE_TTL_S (frontend mirrors this value).
readonly MODE_TTL_S=20

# Decide the host ceiling for device access (prints "r" or "rw").
# Writable access needs both opt-ins: USB_SENTINEL_DEVICE_ACCESS=rw in docker.env
# and an active (non-comment) "allow_destructive = true" line in config.toml.
# Even then the device is passed rw only when the display requested a full inspection
# (see inspection_access and plan_docker_argv).
device_access_mode() {
    local requested="${1:-ro}" config_file="${2:-}"
    local destructive=false
    if [[ -f "${config_file}" ]] &&
        grep -Eq '^[[:space:]]*allow_destructive[[:space:]]*=[[:space:]]*true' "${config_file}"; then
        destructive=true
    fi
    if [[ "${requested}" == rw && "${destructive}" == true ]]; then
        echo "notice: host allows read-write (docker.env rw + allow_destructive = true)" >&2
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

# Print the scan_only fallback with one notice line on stderr.
inspection_mode_rejected() {
    echo "notice: inspection mode scan_only: $1" >&2
    echo scan_only
}

# Read the display's mode file once: inspection_mode <file> <now_epoch> <dir_owner> <group> <ttl_s>
# Prints exactly "full" or "scan_only" and always returns 0. The file must be exactly
# "<scan_only|full> <1-12 digits>\n" (at most 64 bytes) in a directory owned by
# <dir_owner>:<group> that is not world-writable; "full" must also be fresh.
inspection_mode() {
    local f="$1" now="$2" owner="$3" group="$4" ttl="$5"
    local LC_ALL=C
    local dir="${f%/*}" content size last mode stamp age
    local line_re='^(scan_only|full) ([0-9]{1,12})$'
    # 1. Directory present and not a symlink.
    if ! [[ -d "${dir}" && ! -L "${dir}" ]]; then
        inspection_mode_rejected "control directory missing or a symlink"; return 0
    fi
    # 2. Directory owner, group and no world write.
    if [[ -z "$(find "${dir}" -prune -user "${owner}" -group "${group}" ! -perm -0002 2>/dev/null || true)" ]]; then
        inspection_mode_rejected "control directory has wrong owner, group or permissions"; return 0
    fi
    # 3. No file at all: the display has not written anything since boot.
    if [[ ! -e "${f}" && ! -L "${f}" ]]; then
        inspection_mode_rejected "no mode file"; return 0
    fi
    # 4. Regular file, not a symlink.
    if ! [[ -f "${f}" && ! -L "${f}" ]]; then
        inspection_mode_rejected "mode file is not a regular file"; return 0
    fi
    # 5. File group, no world write, at most 64 bytes.
    if [[ -z "$(find "${f}" -prune -type f -group "${group}" ! -perm -0002 -size -65c 2>/dev/null || true)" ]]; then
        inspection_mode_rejected "mode file has wrong group, permissions or size"; return 0
    fi
    # 6. Read it (the braces also silence bash's NUL-byte warning).
    if ! { content="$(head -c 64 -- "${f}")"; } 2>/dev/null; then
        inspection_mode_rejected "mode file unreadable"; return 0
    fi
    # 7. Exactly one valid line; save the captures before any other =~.
    if ! [[ "${content}" =~ ${line_re} ]]; then
        inspection_mode_rejected "mode file content invalid"; return 0
    fi
    mode="${BASH_REMATCH[1]}"
    stamp="${BASH_REMATCH[2]}"
    # 8. The file holds exactly one byte more than the matched text (no NUL, one newline).
    size="$(wc -c < "${f}" 2>/dev/null || true)"
    size="${size//[[:space:]]/}"
    if ! [[ "${size}" =~ ^[0-9]+$ ]] || (( 10#${size} != ${#content} + 1 )); then
        inspection_mode_rejected "mode file content invalid"; return 0
    fi
    # 9. That extra byte is the final newline.
    if ! { last="$(tail -c 1 -- "${f}"; printf x)"; } 2>/dev/null || [[ "${last}" != $'\nx' ]]; then
        inspection_mode_rejected "mode file content invalid"; return 0
    fi
    # 10. "full" must be fresh (10# reads a stamp with leading zeros as decimal).
    if [[ "${mode}" == full ]]; then
        age=$(( now - 10#${stamp} ))
        if (( age < 0 || age > ttl )); then
            inspection_mode_rejected "mode file stale (age ${age}s)"; return 0
        fi
        echo "notice: display requested full inspection (age ${age}s)" >&2
        echo full
        return 0
    fi
    echo "notice: inspection mode scan_only (display)" >&2
    echo scan_only
}

# inspection_access <ceiling> <requested_mode>: prints "rw full" or "r scan_only".
inspection_access() {
    local ceiling="$1" requested="$2"
    if [[ "${ceiling}" == rw && "${requested}" == full ]]; then
        echo "rw full"
        return 0
    fi
    if [[ "${ceiling}" == rw ]]; then
        echo "notice: device stays read-only: inspection mode is scan_only" >&2
    fi
    echo "r scan_only"
}

# docker_argv <access> <mode> <device> <image> <config> <database> <results> <db_dir> <path>...
# Pure: prints the arguments after "docker", one per line. Returns 1 with an error line for
# an inconsistent (access, mode) pair, a bad device path, no path, or an empty/multi-line value.
docker_argv() {
    local access="$1" mode="$2" device="$3" image="$4" config="$5" database="$6" results="$7" db_dir="$8"
    shift 8
    local path_re='^/dev/[a-zA-Z0-9_/-]+$' path value
    if ! [[ ( "${access}" == r && "${mode}" == scan_only ) || ( "${access}" == rw && "${mode}" == full ) ]]; then
        echo "error: refusing device access '${access}' with inspection mode '${mode}'" >&2
        return 1
    fi
    if [[ $# -eq 0 ]]; then
        echo "error: no device paths" >&2
        return 1
    fi
    for path in "${device}" "$@"; do
        if ! [[ "${path}" =~ ${path_re} ]]; then
            echo "error: invalid device path: ${path}" >&2
            return 1
        fi
    done
    for value in "${image}" "${config}" "${database}" "${results}" "${db_dir}"; do
        if [[ -z "${value}" || "${value}" == *$'\n'* ]]; then
            echo "error: empty or multi-line docker argument" >&2
            return 1
        fi
    done
    printf '%s\n' run --rm --pull=never --init \
        --name "usb-sentinel-${device##*/}" \
        --network none --read-only \
        --cap-drop ALL --cap-add SYS_ADMIN --cap-add DAC_OVERRIDE \
        --security-opt no-new-privileges --security-opt apparmor=unconfined \
        --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m \
        --tmpfs /run:rw,nosuid,nodev,noexec,size=16m
    for path in "$@"; do
        printf '%s\n' --device "${path}:${path}:${access}"
    done
    printf '%s\n' \
        --mount type=bind,src=/sys,dst=/sys,readonly \
        --mount type=bind,src=/run/udev,dst=/run/udev,readonly \
        --mount "type=bind,src=${config},dst=/etc/usb-sentinel/config.toml,readonly" \
        --mount "type=bind,src=${database},dst=/var/lib/clamav,readonly" \
        --mount "type=bind,src=${results},dst=/var/lib/usb-sentinel/results" \
        --mount "type=bind,src=${db_dir},dst=/var/lib/usb-sentinel/db" \
        "${image}" inspect --mode "${mode}" "${device}"
}

# plan_docker_argv <device_access> <config> <requested_mode> <device> <image> <database>
#                  <results> <db_dir> <path>...
# The only place that combines the host ceiling with the display's requested mode.
plan_docker_argv() {
    local device_access="$1" config="$2" requested_mode="$3" device="$4" image="$5"
    local database="$6" results="$7" db_dir="$8"
    shift 8
    local ceiling access mode
    ceiling="$(device_access_mode "${device_access}" "${config}")"
    read -r access mode <<< "$(inspection_access "${ceiling}" "${requested_mode}")"
    docker_argv "${access}" "${mode}" "${device}" "${image}" "${config}" "${database}" \
        "${results}" "${db_dir}" "$@"
}

main() {
    if [[ $# -ne 1 || ! "${DEVICE}" =~ ^/dev/[a-zA-Z0-9_-]+$ || ! -b "${DEVICE}" ]]; then
        echo "usage: run-docker.sh /dev/<whole-usb-disk>" >&2
        exit 3
    fi

    # Snapshot the display's mode at insertion, before the (up to 30 s) settle wait.
    local requested_mode
    requested_mode="$(inspection_mode "${CONTROL_FILE}" "$(date +%s)" \
        "${CONTROL_OWNER}" "${CONTROL_GROUP}" "${MODE_TTL_S}")"

    # Wait for partition discovery before taking the --device snapshot.
    udevadm settle --timeout=30
    local -a DEVICES
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
    local host_mounts path
    host_mounts="$(lsblk --noheadings --output MOUNTPOINTS "${DEVICE}")"
    if [[ -n "${host_mounts//[[:space:]]/}" ]]; then
        echo "error: host-mounted disk refused: ${DEVICE}" >&2
        exit 2
    fi
    for path in "${DEVICES[@]}"; do
        [[ -b "${path}" ]] || { echo "error: device disappeared: ${path}" >&2; exit 3; }
    done

    [[ -f "${CONFIG}" && -d "${DATABASE}" && -d "${RESULTS}" && -d "${DB_DIR}" ]] || {
        echo "error: config, ClamAV DB or results directory missing; run install-docker.sh" >&2
        exit 3
    }

    # Kernel device access is read-only unless both host opt-ins are set AND the display
    # requested a full inspection; the container gets the same mode as --mode.
    local argv_text
    argv_text="$(plan_docker_argv "${DEVICE_ACCESS}" "${CONFIG}" "${requested_mode}" "${DEVICE}" \
        "${IMAGE}" "${DATABASE}" "${RESULTS}" "${DB_DIR}" "${DEVICES[@]}")" || exit 3
    local -a argv
    mapfile -t argv <<< "${argv_text}"
    exec docker "${argv[@]}"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
