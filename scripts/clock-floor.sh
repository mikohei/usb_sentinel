#!/usr/bin/env bash
# Keep the wall clock from going back across reboots on a station without an RTC or network.
#
# The Raspberry Pi 4 has no RTC and the station runs offline, so every boot starts at the same
# old time. Results of earlier sessions then look newer than a drive inserted now, and the
# display can bind one of them to that drive. "restore" (at boot, before Docker) moves the clock
# to one second after the newest saved state: the stamp written by "save" at shutdown, the
# results and the SQLite index. It never moves the clock back. The clock still lags real time
# by as long as the station was off.
#
# Functions are sourced by backend/tests with the system bash (3.2) and BSD ls/date;
# main is not.
set -euo pipefail

readonly STATE_DIR="${USB_SENTINEL_STATE_DIR:-/var/lib/usb-sentinel}"
readonly STAMP="${STATE_DIR}/clock"

# later <a> <b>: print the larger epoch value; an empty or non-numeric value is ignored.
later() {
    local a="$1" b="$2"
    [[ "${a}" =~ ^[0-9]+$ ]] || a=""
    [[ "${b}" =~ ^[0-9]+$ ]] || b=""
    if [[ -z "${a}" ]]; then
        echo "${b}"
    elif [[ -z "${b}" ]] || (( a >= b )); then
        echo "${a}"
    else
        echo "${b}"
    fi
}

# newest_mtime <path>...: print the newest mtime (epoch seconds) of the existing paths and, for
# a directory, of its newest direct child. Prints nothing when no path exists.
newest_mtime() {
    local newest="" path child
    for path in "$@"; do
        [[ -e "${path}" ]] || continue
        newest="$(later "${newest}" "$(date -r "${path}" +%s 2>/dev/null || true)")"
        [[ -d "${path}" ]] || continue
        # || true: head may close the pipe early on a large directory (pipefail).
        child="$(ls -At -- "${path}" 2>/dev/null | head -n 1 || true)"
        [[ -n "${child}" ]] || continue
        newest="$(later "${newest}" "$(date -r "${path}/${child}" +%s 2>/dev/null || true)")"
    done
    [[ -z "${newest}" ]] || echo "${newest}"
}

# clock_target <now> <floor>: print floor + 1 unless the clock is already past the floor.
clock_target() {
    local now="$1" floor="$2"
    [[ "${now}" =~ ^[0-9]+$ && "${floor}" =~ ^[0-9]+$ ]] || return 0
    (( now > floor )) || echo $(( floor + 1 ))
}

main() {
    local now floor target
    case "${1:-}" in
        restore)
            now="$(date +%s)"
            floor="$(newest_mtime "${STAMP}" "${STATE_DIR}/results" "${STATE_DIR}/db")"
            target="$(clock_target "${now}" "${floor}")"
            if [[ -n "${target}" ]]; then
                date -s "@${target}" >/dev/null
                echo "notice: clock moved forward by $(( target - now )) s to the last saved state"
            fi
            ;;
        save)
            touch -- "${STAMP}"
            ;;
        *)
            echo "usage: clock-floor.sh restore|save" >&2
            exit 2
            ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
