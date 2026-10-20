#!/usr/bin/env bash
# Boot video hint for the 3.5-inch 480x320 panel: appends `video=HDMI-A-1:480x320@60` to
# /boot/firmware/cmdline.txt so the text console and the GDM greeter use that mode too.
#
# Opt-in: install-all.sh runs video_hint_append only with INSTALL_VIDEO_HINT=1. The display app
# scales its 480x320 design to whatever the screen is, so the hint is not needed for the kiosk;
# a mode the panel rejects can blank the console, which is the recovery path. The first run
# keeps a copy as cmdline.txt.bak (never overwritten). Undo:
#   sudo cp /boot/firmware/cmdline.txt.bak /boot/firmware/cmdline.txt
#
# Functions only: install-all.sh and backend/tests source this file. It needs the caller's
# `warn` function. CMDLINE_FILE and VIDEO_HINT can be overridden from the environment so tests
# point them at a temporary file. bash 3.2 only: no mapfile, no associative arrays.

CMDLINE_FILE="${CMDLINE_FILE:-/boot/firmware/cmdline.txt}"
VIDEO_HINT="${VIDEO_HINT:-video=HDMI-A-1:480x320@60}"
VIDEO_HINT_CONNECTOR="${VIDEO_HINT%%:*}:"            # "video=HDMI-A-1:"

# video_hint_append: append VIDEO_HINT once to the single-line cmdline.txt, after a backup.
# Prints one status line on stdout; warnings go to stderr. Returns 0 in every handled case
# (the installer must not fail on it).
video_hint_append() {
    if [[ ! -f "${CMDLINE_FILE}" ]]; then
        warn "${CMDLINE_FILE} not found; boot video hint skipped"
        return 0
    fi
    if [[ "$(wc -l < "${CMDLINE_FILE}")" -gt 1 ]]; then
        warn "${CMDLINE_FILE} has more than one line; not touched"
        return 0
    fi
    if grep -q -- "${VIDEO_HINT_CONNECTOR}" "${CMDLINE_FILE}"; then
        if grep -qw -- "${VIDEO_HINT}" "${CMDLINE_FILE}"; then
            echo "cmdline.txt: ${VIDEO_HINT} already present"
        else
            warn "${CMDLINE_FILE} already has a ${VIDEO_HINT_CONNECTOR} entry; not touched"
        fi
        return 0
    fi
    # Plain cp: the boot partition is vfat, which keeps no ownership or mode bits.
    if [[ ! -f "${CMDLINE_FILE}.bak" ]]; then
        cp "${CMDLINE_FILE}" "${CMDLINE_FILE}.bak"
    fi
    local line
    line="$(tr -d '\n' < "${CMDLINE_FILE}")"
    printf '%s %s\n' "${line}" "${VIDEO_HINT}" > "${CMDLINE_FILE}"
    echo "cmdline.txt: appended ${VIDEO_HINT} (backup ${CMDLINE_FILE}.bak; effective after reboot)"
    return 0
}
