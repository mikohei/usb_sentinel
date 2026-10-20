#!/usr/bin/env bash
# GNOME keyboard/pointer lockdown for the kiosk display user.
#
# A USB keyboard plugged into the station can use GNOME shortcuts (overview, run dialog,
# terminal, dock favourites, workspace switch) to leave the kiosk window and act as the display
# user. This file clears every string-list shortcut key of the GNOME keybinding schemas, except
# the password-protected console switch (Ctrl+Alt+F1..F12), and fixes a few settings.
#
# Functions only: install-all.sh and backend/tests source this file. It needs the caller's
# `warn` function. Every function takes the gsettings command (a function name such as
# gsettings_user) as its first argument so tests can pass a fake. bash 3.2 only: no mapfile,
# no associative arrays, no `readonly -a`.
#
# Undo as the display user: `gsettings reset-recursively <schema>` for each schema in
# KIOSK_CLEAR_SCHEMAS and KIOSK_DOCK_SCHEMA, and `gsettings reset <schema> <key>` for each fixed
# setting in kiosk_lockdown.

# Keybinding schemas whose string-list keys are all cleared (except kiosk_keep_key).
KIOSK_CLEAR_SCHEMAS="org.gnome.desktop.wm.keybindings org.gnome.shell.keybindings
org.gnome.mutter.keybindings org.gnome.mutter.wayland.keybindings
org.gnome.settings-daemon.plugins.media-keys"
# Ubuntu Dock: only its hot-key lists are cleared; other list keys are appearance settings.
KIOSK_DOCK_SCHEMA=org.gnome.shell.extensions.dash-to-dock
KIOSK_DOCK_KEYS='^(app-(shift-|ctrl-)?hotkey-[0-9]+|shortcut)$'

# kiosk_keep_key <schema> <key>: 0 when the key keeps its default (console switch only).
kiosk_keep_key() {
    local schema="$1" key="$2"
    local keep_re='^switch-to-session-[0-9]+$'
    [[ "${schema}" == org.gnome.mutter.wayland.keybindings && "${key}" =~ ${keep_re} ]]
}

# kiosk_set <gs> <schema> <key> <value>: set one key; warn on failure, never fail.
kiosk_set() {
    local gs="$1" schema="$2" key="$3" value="$4"
    if ! "${gs}" set "${schema}" "${key}" "${value}" </dev/null 2>/dev/null; then
        warn "gsettings: could not set ${schema} ${key} (kept default)"
    fi
    return 0
}

# kiosk_clear_schema <gs> <schema> [<key-regex>]: set every non-empty string-list key of the
# schema to [] (only keys matching the regex when one is given; kept keys are skipped).
kiosk_clear_schema() {
    local gs="$1" schema="$2" key_re="${3:-}"
    local listing line s key value cleared=0 lists_seen=0
    if ! listing="$("${gs}" list-recursively "${schema}" </dev/null 2>/dev/null)"; then
        warn "gsettings: schema ${schema} not found (skipped)"
        return 0
    fi
    while IFS= read -r line; do
        [[ -n "${line}" ]] || continue
        read -r s key value <<< "${line}"
        [[ "${s}" == "${schema}" ]] || continue
        if [[ -n "${key_re}" ]] && ! [[ "${key}" =~ ${key_re} ]]; then
            continue
        fi
        if kiosk_keep_key "${schema}" "${key}"; then
            continue
        fi
        if [[ "${value}" == "@as []" || "${value}" == "[]" ]]; then
            lists_seen=1
            continue
        fi
        if [[ "${value}" == "['"* ]]; then
            lists_seen=1
            kiosk_set "${gs}" "${schema}" "${key}" "[]"
            cleared=$((cleared + 1))
        fi
    done <<< "${listing}"
    echo "GNOME: ${schema}: ${cleared} shortcuts cleared"
    if [[ -z "${key_re}" && "${lists_seen}" -eq 0 ]]; then
        warn "gsettings: ${schema} has no shortcut lists; check the GNOME version"
    fi
    return 0
}

# kiosk_lockdown <gs>: fixed settings, then clear the keybinding schemas and the dock hot keys.
kiosk_lockdown() {
    local gs="$1" schema
    # Alt+F2 run dialog, logout entries, user switching.
    kiosk_set "${gs}" org.gnome.desktop.lockdown disable-command-line true
    kiosk_set "${gs}" org.gnome.desktop.lockdown disable-log-out true
    kiosk_set "${gs}" org.gnome.desktop.lockdown disable-user-switching true
    # Bare Super key overview.
    kiosk_set "${gs}" org.gnome.mutter overlay-key "''"
    # No extra empty workspace where the dock shows.
    kiosk_set "${gs}" org.gnome.mutter dynamic-workspaces false
    kiosk_set "${gs}" org.gnome.desktop.wm.preferences num-workspaces 1
    # Pointer (BadUSB mouse) overview.
    kiosk_set "${gs}" org.gnome.desktop.interface enable-hot-corners false
    # Dock favourites (Files, Terminal) and the dock's own Super+N hot keys.
    kiosk_set "${gs}" org.gnome.shell favorite-apps "[]"
    kiosk_set "${gs}" "${KIOSK_DOCK_SCHEMA}" hot-keys false
    # Accessibility features toggled from the keyboard (Shift x5 and similar).
    kiosk_set "${gs}" org.gnome.desktop.a11y.keyboard enable false
    # Power key on a USB keyboard.
    kiosk_set "${gs}" org.gnome.settings-daemon.plugins.power power-button-action "'nothing'"
    for schema in ${KIOSK_CLEAR_SCHEMAS}; do
        kiosk_clear_schema "${gs}" "${schema}"
    done
    kiosk_clear_schema "${gs}" "${KIOSK_DOCK_SCHEMA}" "${KIOSK_DOCK_KEYS}"
    echo 'GNOME: kiosk lockdown done (see README "Hardening against BadUSB")'
}
