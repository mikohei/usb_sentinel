# Host erase setting shared by install-docker.sh (sourced; defines functions only).
#
#   docker_env_write FILE IMAGE ACCESS   write a fresh docker.env (ACCESS = ro or rw)
#   apply_erase_setting ETC_DIR ALLOW    ALLOW=1: allow_destructive = true and
#                                        USB_SENTINEL_DEVICE_ACCESS=rw; ALLOW=0: false and ro
#
# apply_erase_setting edits only those two key lines (keyfile.sh: replace or insert,
# FILE.bak once, no write when unchanged), so re-installs keep every other key.
# Erasing still needs the operator to switch the display to "Scan + erase" (M key or the
# mode button, code shown on screen); that gate is not configured here.

source "$(dirname "${BASH_SOURCE[0]}")/keyfile.sh"

docker_env_write() {
    local file="$1" image="$2" access="$3"
    cat > "${file}" <<EOF
USB_SENTINEL_IMAGE=${image}
# Host ceiling for USB device access passed to docker --device: ro or rw.
# rw lets the capacity (f3) and speed (fio) checks ERASE the drive contents.
# install-docker.sh sets rw by default (INSTALL_ALLOW_ERASE=0 keeps ro) together with
# config.toml allow_destructive = true. Erasing still requires the operator to switch
# the display to "Scan + erase" (M key or the mode button, code shown on screen) before
# the drive is inserted; otherwise the device stays read-only.
USB_SENTINEL_DEVICE_ACCESS=${access}
EOF
    chmod 0644 "${file}"
}

apply_erase_setting() {
    local etc_dir="$1" allow="$2" destructive access
    case "${allow}" in
        1) destructive=true; access=rw ;;
        0) destructive=false; access=ro ;;
        *) printf 'error: INSTALL_ALLOW_ERASE must be 0 or 1 (got "%s")\n' "${allow}" >&2
           return 1 ;;
    esac
    set_key_line "${etc_dir}/config.toml" allow_destructive \
        "allow_destructive = ${destructive}" before-table || return 1
    set_key_line "${etc_dir}/docker.env" USB_SENTINEL_DEVICE_ACCESS \
        "USB_SENTINEL_DEVICE_ACCESS=${access}" || return 1
    if [[ "${allow}" == 1 ]]; then
        echo "erase on host: enabled (allow_destructive = true, USB_SENTINEL_DEVICE_ACCESS=rw)"
    else
        echo "erase on host: disabled (allow_destructive = false, USB_SENTINEL_DEVICE_ACCESS=ro)"
    fi
}
