#!/usr/bin/env bash
# GNOME keyboard/pointer lockdown for the kiosk display user.
#
# A USB keyboard plugged into the station can use GNOME shortcuts (overview, run dialog,
# terminal, dock favourites, workspace switch) to leave the kiosk window and act as the display
# user. This file clears every string-list shortcut key of the GNOME keybinding schemas, except
# the password-protected console switch (Ctrl+Alt+F1..F12), and fixes a few settings.
#
# Functions only: install-all.sh, kiosk-enable.sh, kiosk-disable.sh and backend/tests source
# this file. It needs the caller's `warn` function. Every gsettings function takes the gsettings
# command (a function name such as gsettings_user) as its first argument so tests can pass a
# fake. bash 3.2 only: no mapfile, no associative arrays, no `readonly -a`.
#
# Undo with kiosk_unlock (scripts/kiosk-disable.sh): it resets exactly the keys kiosk_lockdown
# sets. The XDG autostart entry is toggled with kiosk_autostart_hide / kiosk_autostart_show
# (append or remove a `Hidden=true` line).

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

# kiosk_reset <gs> <schema> <key>: reset one key to its default; warn on failure, never fail.
kiosk_reset() {
    local gs="$1" schema="$2" key="$3"
    if ! "${gs}" reset "${schema}" "${key}" </dev/null 2>/dev/null; then
        warn "gsettings: could not reset ${schema} ${key}"
    fi
    return 0
}

# kiosk_reset_schema <gs> <schema>: reset every key of the schema to its default.
# On org.gnome.mutter.wayland.keybindings this also touches switch-to-session-N, which
# kiosk_lockdown left at default, so the reset is a no-op for them.
kiosk_reset_schema() {
    local gs="$1" schema="$2"
    if ! "${gs}" reset-recursively "${schema}" </dev/null 2>/dev/null; then
        warn "gsettings: schema ${schema} not found (skipped)"
        return 0
    fi
    echo "GNOME: ${schema}: reset to defaults"
    return 0
}

# kiosk_reset_dock_keys <gs>: reset only the dock keys matching KIOSK_DOCK_KEYS (the ones
# kiosk_clear_schema touched). No reset-recursively here: appearance keys must stay.
kiosk_reset_dock_keys() {
    local gs="$1" listing line s key value
    if ! listing="$("${gs}" list-recursively "${KIOSK_DOCK_SCHEMA}" </dev/null 2>/dev/null)"; then
        warn "gsettings: schema ${KIOSK_DOCK_SCHEMA} not found (skipped)"
        return 0
    fi
    while IFS= read -r line; do
        [[ -n "${line}" ]] || continue
        read -r s key value <<< "${line}"
        [[ "${s}" == "${KIOSK_DOCK_SCHEMA}" ]] || continue
        [[ "${key}" =~ ${KIOSK_DOCK_KEYS} ]] || continue
        kiosk_reset "${gs}" "${KIOSK_DOCK_SCHEMA}" "${key}"
    done <<< "${listing}"
    return 0
}

# kiosk_unlock <gs>: undo kiosk_lockdown. Same order: fixed settings, the keybinding schemas,
# then the dock hot keys. Keep this list in step with kiosk_lockdown (tests compare them).
kiosk_unlock() {
    local gs="$1" schema
    kiosk_reset "${gs}" org.gnome.desktop.lockdown disable-command-line
    kiosk_reset "${gs}" org.gnome.desktop.lockdown disable-log-out
    kiosk_reset "${gs}" org.gnome.desktop.lockdown disable-user-switching
    kiosk_reset "${gs}" org.gnome.mutter overlay-key
    kiosk_reset "${gs}" org.gnome.mutter dynamic-workspaces
    kiosk_reset "${gs}" org.gnome.desktop.wm.preferences num-workspaces
    kiosk_reset "${gs}" org.gnome.desktop.interface enable-hot-corners
    kiosk_reset "${gs}" org.gnome.shell favorite-apps
    kiosk_reset "${gs}" "${KIOSK_DOCK_SCHEMA}" hot-keys
    kiosk_reset "${gs}" org.gnome.desktop.a11y.keyboard enable
    kiosk_reset "${gs}" org.gnome.settings-daemon.plugins.power power-button-action
    for schema in ${KIOSK_CLEAR_SCHEMAS}; do
        kiosk_reset_schema "${gs}" "${schema}"
    done
    kiosk_reset_dock_keys "${gs}"
    echo 'GNOME: kiosk lockdown undone'
}

# kiosk_autostart_hide <file>: append Hidden=true to the autostart entry (idempotent).
kiosk_autostart_hide() {
    local file="$1"
    if [[ ! -f "${file}" ]]; then
        warn "autostart entry ${file} missing"
        return 0
    fi
    grep -q '^Hidden=true$' "${file}" || printf 'Hidden=true\n' >> "${file}"
    echo "autostart: disabled (Hidden=true) ${file}"
}

# kiosk_autostart_show <file>: remove every Hidden= line from the autostart entry.
# `sed -i.bak` works with both GNU and BSD sed.
kiosk_autostart_show() {
    local file="$1"
    if [[ ! -f "${file}" ]]; then
        warn "autostart entry ${file} missing"
        return 0
    fi
    sed -i.bak '/^Hidden=/d' "${file}" && rm -f "${file}.bak"
    echo "autostart: enabled ${file}"
}
