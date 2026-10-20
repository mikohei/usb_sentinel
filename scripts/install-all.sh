#!/usr/bin/env bash
# Install USB Sentinel end to end on one Raspberry Pi 4 running Ubuntu Desktop.
#
# Run once while the Pi is connected to the network. Afterwards unplug the wired
# LAN and use the Pi as an isolated inspection station. Re-running is safe: the
# existing config.toml, docker.env and display.toml are never overwritten.
#
# Usage:
#   sudo bash scripts/install-all.sh                 # display user = the sudo caller
#   sudo DISPLAY_USER=kiosk bash scripts/install-all.sh
#   sudo INSTALL_YES=1 bash scripts/install-all.sh   # answer every prompt with yes
#   sudo bash scripts/kiosk-enable.sh                # final step, after the manual steps
#   sudo bash scripts/kiosk-disable.sh               # undo from a text console
#
# What it does:
#   1. Preflight checks (Ubuntu, arm64, sudo, internet)
#   2. apt: Docker Engine, Noto CJK fonts, python3-tk, curl
#   3. uv (official installer; the script is downloaded, its SHA-256 shown,
#      and confirmation asked before running it)
#   4. backend: scripts/install-docker.sh (image build, udev/systemd, result dirs)
#   5. frontend: uv sync --frozen and a tkinter import check
#   6. Display user: usb-sentinel group, GDM autologin (kept on purpose: it lands
#      the user on the normal desktop for the manual steps; the app does NOT
#      start until scripts/kiosk-enable.sh is run), GNOME automount off,
#      sleep/lock off, display.toml, XDG autostart entry written DISABLED
#      (Hidden=true)
#      Kiosk lockdown and autostart are armed later by scripts/kiosk-enable.sh
#   7. ClamAV signature database (freshclam inside the container)
#   8. Offline self-check (selftest with --network none, uv run --offline)
#   9. Remaining manual steps and the "before unplugging the LAN" checklist
#
# What it leaves to the operator (printed at the end):
#   - Fixing the display resolution to 480x320 (GNOME monitors.xml)
#   - Touch panel driver (vendor scripts are never executed by this project)
#   - Port calibration (insert a USB stick into each port and record the path)
#   - Nominal capacity/speed in config.toml, and enabling destructive checks
#     (allow_destructive = true plus USB_SENTINEL_DEVICE_ACCESS=rw; erasing then
#     still needs the display's mode button switched to "Scan + erase")
set -euo pipefail

readonly REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly DISPLAY_USER="${DISPLAY_USER:-${SUDO_USER:-}}"
readonly INSTALL_YES="${INSTALL_YES:-0}"
readonly UV_VERSION_PIN="${UV_VERSION_PIN:-}"   # e.g. 0.11.7; empty = latest
readonly IMAGE="${USB_SENTINEL_IMAGE:-usb-sentinel:local}"
readonly CLAMAV_DB_DIR="/var/lib/clamav"
readonly UV_INSTALLER_URL="https://astral.sh/uv/install.sh"

log()  { printf '\n==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

confirm() {
    # Ask a y/N question. INSTALL_YES=1 answers yes without prompting.
    if [[ "${INSTALL_YES}" == 1 ]]; then
        return 0
    fi
    local answer
    read -r -p "$1 [y/N] " answer
    [[ "${answer}" =~ ^[Yy]$ ]]
}

run_as_user() {
    # Run a command as the display user with that user's HOME and ~/.local/bin on PATH.
    sudo -u "${DISPLAY_USER}" -H env "PATH=/home/${DISPLAY_USER}/.local/bin:${PATH}" "$@"
}

# ---------------------------------------------------------------- 1. preflight
log "1/9 Preflight checks"
[[ ${EUID} -eq 0 ]] || die "run with sudo"
[[ "$(uname -s)" == Linux ]] || die "run on Linux (Ubuntu)"
[[ -n "${DISPLAY_USER}" ]] || die "cannot determine the display user; set DISPLAY_USER=<user>"
id "${DISPLAY_USER}" >/dev/null 2>&1 || die "user ${DISPLAY_USER} does not exist"
readonly USER_HOME="$(getent passwd "${DISPLAY_USER}" | cut -d: -f6)"
[[ -d "${USER_HOME}" ]] || die "home directory ${USER_HOME} is missing"
if ! grep -qi ubuntu /etc/os-release; then
    warn "not Ubuntu; apt package names may differ"
fi
case "$(uname -m)" in
    aarch64) ;;
    *) warn "not arm64 ($(uname -m)); these steps target Raspberry Pi 4 but continuing" ;;
esac
if ! getent hosts pypi.org >/dev/null 2>&1; then
    die "DNS resolution failed; connect to the network first (unplug it after installing)"
fi
echo "display user: ${DISPLAY_USER} (${USER_HOME})"
echo "repository:   ${REPO_DIR}"

# ---------------------------------------------------------------- 2. apt
log "2/9 Installing apt packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
# docker.io: inspection container. fonts-noto-cjk: Japanese UI text.
# python3-tk: fallback when uv's Python lacks tkinter. curl/ca-certificates: uv download.
apt-get install -y --no-install-recommends \
    docker.io curl ca-certificates fonts-noto-cjk python3 python3-tk
systemctl enable --now docker.service

# ---------------------------------------------------------------- 3. uv
log "3/9 Installing uv into the display user's ~/.local/bin"
readonly UV_BIN="${USER_HOME}/.local/bin/uv"
if [[ -x "${UV_BIN}" ]]; then
    echo "uv already present: $("${UV_BIN}" --version)"
else
    readonly UV_TMP="$(mktemp -d)"
    trap 'rm -rf "${UV_TMP}"' EXIT
    # mktemp -d creates a root-owned 0700 directory; the display user must be
    # able to traverse it to read install.sh.
    chmod 0755 "${UV_TMP}"
    curl -LsSf "${UV_INSTALLER_URL}" -o "${UV_TMP}/install.sh"
    echo "downloaded uv installer: ${UV_INSTALLER_URL}"
    echo "SHA-256: $(sha256sum "${UV_TMP}/install.sh" | cut -d' ' -f1)"
    echo "to inspect it first, run in another terminal: less ${UV_TMP}/install.sh"
    confirm "Run this installer as ${DISPLAY_USER}?" \
        || die "uv installation cancelled; install uv manually and re-run"
    chown "${DISPLAY_USER}" "${UV_TMP}/install.sh"
    if [[ -n "${UV_VERSION_PIN}" ]]; then
        run_as_user env UV_INSTALL_DIR="${USER_HOME}/.local/bin" UV_NO_MODIFY_PATH=1 \
            sh "${UV_TMP}/install.sh" --version "${UV_VERSION_PIN}"
    else
        run_as_user env UV_INSTALL_DIR="${USER_HOME}/.local/bin" UV_NO_MODIFY_PATH=1 \
            sh "${UV_TMP}/install.sh"
    fi
    [[ -x "${UV_BIN}" ]] || die "uv was not installed at ${UV_BIN}"
    echo "uv: $("${UV_BIN}" --version)"
fi

# ---------------------------------------------------------------- 4. backend
log "4/9 backend (Docker image, udev, systemd, result directories)"
USB_SENTINEL_IMAGE="${IMAGE}" bash "${REPO_DIR}/scripts/install-docker.sh"

# ---------------------------------------------------------------- 5. frontend
log "5/9 Syncing frontend dependencies (the app starts with --offline from now on)"
run_as_user "${UV_BIN}" sync --frozen --project "${REPO_DIR}/frontend"
if run_as_user "${UV_BIN}" run --offline --frozen --no-sync --project "${REPO_DIR}/frontend" \
        python -c "import tkinter" 2>/dev/null; then
    echo "tkinter: available in uv's Python"
else
    warn "tkinter cannot be imported from uv's Python; switch to apt's python3-tk"
    warn "see frontend/README.md, 'Display setup' step 8, for the alternative .desktop Exec line"
fi

# ---------------------------------------------------------------- 6. display user
log "6/9 Configuring the display user ${DISPLAY_USER}"

# Group membership for reading the result directory and the SQLite index.
usermod -aG usb-sentinel "${DISPLAY_USER}"
usermod -aG docker "${DISPLAY_USER}" 2>/dev/null || true
echo "groups: usb-sentinel (effective after the next login)"

# GDM autologin. Existing settings are left untouched.
readonly GDM_CONF="/etc/gdm3/custom.conf"
if [[ -f "${GDM_CONF}" ]]; then
    if grep -q '^AutomaticLoginEnable' "${GDM_CONF}"; then
        echo "autologin: existing setting kept"
    else
        cp "${GDM_CONF}" "${GDM_CONF}.bak.$(date +%Y%m%d%H%M%S)"
        if grep -q '^\[daemon\]' "${GDM_CONF}"; then
            sed -i "/^\[daemon\]/a AutomaticLoginEnable=true\nAutomaticLogin=${DISPLAY_USER}" "${GDM_CONF}"
        else
            printf '\n[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=%s\n' "${DISPLAY_USER}" >> "${GDM_CONF}"
        fi
        echo "autologin: enabled for ${DISPLAY_USER} (${GDM_CONF})"
    fi
else
    warn "GDM config ${GDM_CONF} not found; enable autologin manually"
fi

# GNOME automount and sleep settings live in the user's dconf. Use the running
# session bus when the user is logged in, otherwise a throwaway dbus session.
gsettings_user() {
    local uid
    uid="$(id -u "${DISPLAY_USER}")"
    if [[ -S "/run/user/${uid}/bus" ]]; then
        sudo -u "${DISPLAY_USER}" env DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${uid}/bus" gsettings "$@"
    else
        sudo -u "${DISPLAY_USER}" dbus-run-session -- gsettings "$@"
    fi
}
# Kiosk helpers (kiosk_autostart_hide here; kiosk_lockdown is used by kiosk-enable.sh).
source "${REPO_DIR}/scripts/kiosk-lockdown.sh"
if command -v gsettings >/dev/null; then
    # With automount on, run-docker.sh refuses the disk ("host-mounted disk refused").
    gsettings_user set org.gnome.desktop.media-handling automount false
    gsettings_user set org.gnome.desktop.media-handling automount-open false
    gsettings_user set org.gnome.desktop.session idle-delay 0
    gsettings_user set org.gnome.desktop.screensaver lock-enabled false
    gsettings_user set org.gnome.desktop.screensaver idle-activation-enabled false
    gsettings_user set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
    echo "GNOME: automount off, sleep and lock off"
    # Kiosk lockdown is deliberately NOT applied here; scripts/kiosk-enable.sh does it
    # once the display is tuned.
else
    warn "gsettings not found; disable automount and sleep manually"
fi

# display.toml: created from the example when missing. Port table is filled by calibrate later.
readonly DISPLAY_CONF_DIR="${USER_HOME}/.config/usb-sentinel"
readonly DISPLAY_CONF="${DISPLAY_CONF_DIR}/display.toml"
install -d -o "${DISPLAY_USER}" -g "${DISPLAY_USER}" -m 0755 "${DISPLAY_CONF_DIR}"
if [[ -f "${DISPLAY_CONF}" ]]; then
    echo "display.toml: existing file kept (${DISPLAY_CONF})"
else
    install -o "${DISPLAY_USER}" -g "${DISPLAY_USER}" -m 0644 \
        "${REPO_DIR}/frontend/deploy/display.example.toml" "${DISPLAY_CONF}"
    echo "display.toml: created from the example (${DISPLAY_CONF})"
fi

# XDG autostart entry with <user> and the project path filled in. It is written with
# Hidden=true so nothing starts at login until scripts/kiosk-enable.sh removes that line.
readonly AUTOSTART_DIR="${USER_HOME}/.config/autostart"
readonly DESKTOP_FILE="${AUTOSTART_DIR}/usb-sentinel-display.desktop"
install -d -o "${DISPLAY_USER}" -g "${DISPLAY_USER}" -m 0755 "${AUTOSTART_DIR}"
sed -e "s|/home/<user>/.local/bin/uv|${UV_BIN}|g" \
    -e "s|/home/<user>/usb_sentinel/frontend|${REPO_DIR}/frontend|g" \
    -e "s|<user>|${DISPLAY_USER}|g" \
    "${REPO_DIR}/frontend/deploy/usb-sentinel-display.desktop" > "${DESKTOP_FILE}"
chown "${DISPLAY_USER}:${DISPLAY_USER}" "${DESKTOP_FILE}"
chmod 0644 "${DESKTOP_FILE}"
kiosk_autostart_hide "${DESKTOP_FILE}"
echo "autostart: ${DESKTOP_FILE} (disabled until kiosk-enable.sh)"
grep '^Exec=' "${DESKTOP_FILE}"

# ---------------------------------------------------------------- 7. ClamAV DB
log "7/9 Fetching the ClamAV signature database (cannot be updated once isolated)"
if confirm "Run freshclam now to update ${CLAMAV_DB_DIR}? (takes a few minutes)"; then
    docker run --rm --entrypoint freshclam \
        --mount "type=bind,src=${CLAMAV_DB_DIR},dst=/var/lib/clamav" \
        "${IMAGE}" --user=root --stdout || warn "freshclam failed; update later using the README steps"
else
    warn "signature database not fetched; run README section '2. ClamAV definition DB' before isolating"
fi

# ---------------------------------------------------------------- 8. self-check
log "8/9 Verifying that everything works offline"
set +e
docker run --rm --network none \
    --mount "type=bind,src=${CLAMAV_DB_DIR},dst=/var/lib/clamav,readonly" \
    "${IMAGE}" selftest
selftest_rc=$?
set -e
case "${selftest_rc}" in
    0) echo "backend selftest: OK (tools and signature database present)" ;;
    3) warn "backend selftest exited 3; the signature database is missing or stale. Fix before isolating" ;;
    *) warn "backend selftest exited ${selftest_rc}; check the output above" ;;
esac
if run_as_user "${UV_BIN}" run --offline --frozen --no-sync --project "${REPO_DIR}/frontend" \
        usb-sentinel-display --help >/dev/null 2>&1; then
    echo "frontend: starts with uv run --offline (autostart will work without network)"
else
    die "frontend does not start with --offline; check that uv sync --frozen completed"
fi

# ---------------------------------------------------------------- 9. next steps
log "9/9 Installation complete. Remaining manual steps and pre-isolation checklist"
cat <<EOF

[Manual steps]
 1. Fix the display resolution to 480x320
      frontend/README.md, "Display setup" step 1 (GNOME monitors.xml)
 2. Touch panel driver (optional; the keyboard covers every action)
      vendor instructions: https://osoyoo.com/ja/?p=56145
 3. Calibrate the ports (insert one USB stick per port and record its path)
      after re-login:  ${UV_BIN} run --offline --frozen --no-sync \\
                         --project ${REPO_DIR}/frontend usb-sentinel-display calibrate
      write the result into the [[ports]] table of ${DISPLAY_CONF}
 4. Adjust /etc/usb-sentinel/config.toml for your operation
      - [capacity] nominal_bytes, [speed] nominal_*_mb_s / device_class (when known)
      - To enable the capacity and speed checks (they ERASE the sample):
          config.toml:  allow_destructive = true
          docker.env:   USB_SENTINEL_DEVICE_ACCESS=rw
        then switch the display's mode button to "Scan + erase" and enter the
        code shown (resets to scan only at every start). All three are required;
        without any one of them the device stays read-only.
 5. Log out once and let autologin bring you back (activates the usb-sentinel group).
      The display app does NOT start yet: its autostart entry is written with Hidden=true.
 6. Arm the kiosk (only after steps 1 and 3 are done):
      sudo bash ${REPO_DIR}/scripts/kiosk-enable.sh
    This enables autostart and applies the GNOME lockdown (scripts/kiosk-lockdown.sh:
    every shortcut except Ctrl+Alt+F1..F12 is cleared, the dock is emptied).
    Once armed, the app covers the whole screen, hides the cursor and takes focus back
    from every other window. The only ways out are:
      - Ctrl+Alt+F3 (text console, password) or SSH, then
          pkill -f usb-sentinel-display                      stop the app until the next login
          sudo bash ${REPO_DIR}/scripts/kiosk-disable.sh     undo autostart and lockdown
    GDM autologin stays on in both states (${GDM_CONF}; remove the two AutomaticLogin
    lines to turn it off).

[Checklist before unplugging the LAN]
 [ ] backend selftest exited 0 (see step 8/9 above)
 [ ] After kiosk-enable.sh and a reboot the full-screen display shows four tiles
 [ ] A disposable USB stick shows "Scanning" and then a verdict
 [ ] result.json appears under /var/lib/usb-sentinel/results/
 [ ] The on-screen QR codes scan with a phone (summary QR and the 3 detail pages)
 [ ] The ClamAV database date is recent (result.json: steps.malware.details.database)

Once all items pass, unplug the wired LAN. No network is needed at boot
(autostart runs with --offline, inspections run with --network none).
EOF
