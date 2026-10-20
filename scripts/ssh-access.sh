#!/usr/bin/env bash
# Opt-in SSH maintenance access for the kiosk (install-all.sh step 7, INSTALL_SSH=1).
#
# The 480x320 panel is unreadable and, once the kiosk is armed, the display app covers the
# screen. SSH over the wired LAN is the comfortable way to debug the station while it is still
# connected. The station later runs with the LAN unplugged, so sshd must be safe during the time
# it is reachable: key-only login for one admin user (the sudo caller, never the display user),
# no passwords, no root, no X11 forwarding.
#
# Functions only: install-all.sh and backend/tests source this file. It needs the caller's
# `warn` and `die` functions. Paths and the commands that need root are taken from variables so
# tests can point them at a temporary directory and fake binaries on PATH.
# bash 3.2 only: no mapfile, no associative arrays.

SSHD_DROPIN="${SSHD_DROPIN:-/etc/ssh/sshd_config.d/90-usb-sentinel.conf}"

# ssh_write_sshd_dropin <admin>: write the hardening drop-in and validate it with sshd -t.
ssh_write_sshd_dropin() {
    local admin="$1"
    install -d -m 0755 "$(dirname "${SSHD_DROPIN}")"
    cat > "${SSHD_DROPIN}" <<EOF
# Written by usb_sentinel scripts/install-all.sh (INSTALL_SSH=1). Key-only maintenance access.
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
AllowUsers ${admin}
X11Forwarding no
EOF
    chmod 0644 "${SSHD_DROPIN}"
    sshd -t || die "sshd rejected its configuration; see the message above (${SSHD_DROPIN})"
    echo "sshd: ${SSHD_DROPIN} written and validated (AllowUsers ${admin}, no passwords)"
}

# ssh_resolve_key: print the public key line given by SSH_AUTHORIZED_KEY or
# SSH_AUTHORIZED_KEY_FILE (first non-comment line). Prints nothing when neither is set.
ssh_resolve_key() {
    if [[ -n "${SSH_AUTHORIZED_KEY:-}" ]]; then
        printf '%s\n' "${SSH_AUTHORIZED_KEY}"
    elif [[ -n "${SSH_AUTHORIZED_KEY_FILE:-}" ]]; then
        [[ -r "${SSH_AUTHORIZED_KEY_FILE}" ]] \
            || die "SSH_AUTHORIZED_KEY_FILE ${SSH_AUTHORIZED_KEY_FILE} is not readable"
        grep -v -e '^[[:space:]]*#' -e '^[[:space:]]*$' "${SSH_AUTHORIZED_KEY_FILE}" | head -n 1
    fi
}

# ssh_install_key <admin> <home>: make sure ~admin/.ssh/authorized_keys holds at least one key.
# A key from the environment is appended once (idempotent). Without any key: die, never fall
# back to password login.
ssh_install_key() {
    local admin="$1" home="$2"
    local ssh_dir="${home}/.ssh" auth_keys="${home}/.ssh/authorized_keys" key group
    group="$(id -gn "${admin}")"
    key="$(ssh_resolve_key)"
    if [[ -n "${key}" ]]; then
        [[ "${key}" =~ ^(ssh-(ed25519|rsa)|ecdsa-sha2-nistp(256|384|521)|sk-)[^[:space:]]*[[:space:]]+[A-Za-z0-9+/=]+ ]] \
            || die "SSH_AUTHORIZED_KEY does not look like an OpenSSH public key line"
        install -d -o "${admin}" -g "${group}" -m 0700 "${ssh_dir}"
        if [[ -f "${auth_keys}" ]] && grep -qxF "${key}" "${auth_keys}"; then
            echo "authorized_keys: key already present (${auth_keys})"
        else
            printf '%s\n' "${key}" >> "${auth_keys}"
            echo "authorized_keys: key added (${auth_keys})"
        fi
        chown "${admin}:${group}" "${auth_keys}"
        chmod 0600 "${auth_keys}"
    fi
    if [[ ! -s "${auth_keys}" ]] \
            || ! grep -q -v -e '^[[:space:]]*#' -e '^[[:space:]]*$' "${auth_keys}"; then
        die "no SSH public key for ${admin} (${auth_keys} is empty or missing).
       Password login is never enabled. Supply a key with one of:
         sudo INSTALL_SSH=1 SSH_AUTHORIZED_KEY='ssh-ed25519 AAAA... you@laptop' bash scripts/install-all.sh
         sudo INSTALL_SSH=1 SSH_AUTHORIZED_KEY_FILE=/path/to/id_ed25519.pub bash scripts/install-all.sh
       (print yours on the laptop with: cat ~/.ssh/id_ed25519.pub)"
    fi
}

# ssh_open_firewall: allow 22/tcp only when ufw is installed and active. Never installs ufw.
ssh_open_firewall() {
    if ! command -v ufw >/dev/null 2>&1; then
        echo "firewall: ufw not installed (nothing to do)"
        return 0
    fi
    if ufw status 2>/dev/null | grep -q '^Status: active'; then
        ufw allow 22/tcp >/dev/null
        echo "firewall: ufw active, 22/tcp allowed"
    else
        echo "firewall: ufw inactive (nothing to do)"
    fi
}

# ssh_print_access <admin>: the LAN addresses and the exact ssh command.
ssh_print_access() {
    local admin="$1" ip ips
    ips="$(hostname -I 2>/dev/null || true)"
    echo "SSH maintenance access: enabled (key only, user ${admin})"
    if [[ -z "${ips// /}" ]]; then
        warn "no LAN address found (hostname -I); connect the wired LAN and run: hostname -I"
    fi
    for ip in ${ips}; do
        echo "  ssh ${admin}@${ip}"
    done
    cat <<EOF
  sshd stays enabled after the LAN is unplugged; that is harmless (nothing can reach it).
  To turn it off:   sudo systemctl disable --now ssh
EOF
}

# ssh_setup <admin> <home>: the whole step. apt, drop-in, key, service, firewall, summary.
ssh_setup() {
    local admin="$1" home="$2"
    [[ -n "${admin}" ]] || die "INSTALL_SSH=1 needs SSH_ADMIN_USER or sudo (SUDO_USER) to name the admin user"
    id "${admin}" >/dev/null 2>&1 || die "admin user ${admin} does not exist"
    [[ -d "${home}" ]] || die "home directory ${home} of ${admin} is missing"
    # The key is checked first so a missing key never leaves sshd installed and running.
    ssh_install_key "${admin}" "${home}"
    apt-get install -y --no-install-recommends openssh-server
    ssh_write_sshd_dropin "${admin}"
    systemctl enable --now ssh
    ssh_open_firewall
    ssh_print_access "${admin}"
}
