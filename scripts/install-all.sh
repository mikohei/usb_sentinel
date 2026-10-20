#!/usr/bin/env bash
# Install USB Sentinel end to end on one Raspberry Pi 4 running Ubuntu Desktop.
#
# Run once while the Pi is connected to the network. Afterwards unplug the wired
# LAN and use the Pi as an isolated inspection station. Re-running is safe: the
# existing display.toml is never overwritten, and config.toml/docker.env keep every key
# except the two erase keys (allow_destructive, USB_SENTINEL_DEVICE_ACCESS).
#
# Usage:
#   sudo bash scripts/install-all.sh                 # display user = the sudo caller
#   sudo DISPLAY_USER=kiosk bash scripts/install-all.sh
#   sudo INSTALL_YES=1 bash scripts/install-all.sh   # answer every question with yes (no prompts at all)
#   sudo INSTALL_SSH=1 SSH_AUTHORIZED_KEY="ssh-ed25519 AAAA... you@laptop" bash scripts/install-all.sh
#                                                    # opt-in key-only SSH for the sudo caller
#   sudo INSTALL_KIOSK=0 bash scripts/install-all.sh # install but do NOT arm the kiosk at the end
#   sudo INSTALL_ALLOW_ERASE=0 bash scripts/install-all.sh
#                                                    # scan-only station: the host never erases
#   sudo INSTALL_VIDEO_HINT=1 bash scripts/install-all.sh
#                                                    # also add video=HDMI-A-1:480x320@60 to cmdline.txt
#   sudo bash scripts/kiosk-enable.sh                # arm later, when INSTALL_KIOSK=0 was used
#   sudo bash scripts/kiosk-disable.sh               # undo from a text console
#
# What it does:
#   1. Preflight checks (Ubuntu, arm64, sudo, internet), then EVERY question the
#      script has is asked here, before anything is changed. Three questions: the
#      uv installer (downloaded, SHA-256 shown; skipped when uv is already
#      installed), whether to run freshclam in step 8, and whether to arm the
#      kiosk at the end (default yes). After the preflight the run is unattended.
#      INSTALL_YES=1 answers yes to everything; INSTALL_KIOSK=0/1 overrides the
#      kiosk question.
#   2. apt: Docker Engine, Noto CJK fonts, python3-tk, curl
#   3. uv (official installer downloaded and confirmed in step 1)
#   4. backend: scripts/install-docker.sh (image build, udev/systemd, result dirs,
#      erase enabled on the host unless INSTALL_ALLOW_ERASE=0)
#   5. frontend: uv sync --frozen and a tkinter import check
#   6. Display user: usb-sentinel group, GDM autologin, GNOME automount off,
#      sleep/lock off, display.toml, XDG autostart entry written DISABLED
#      (Hidden=true) so an aborted run never leaves autostart armed; boot video
#      hint (cmdline.txt) only with INSTALL_VIDEO_HINT=1
#   7. SSH maintenance access, only with INSTALL_SSH=1 (default: nothing is
#      installed): openssh-server, key-only login for the sudo caller
#      (SSH_AUTHORIZED_KEY or SSH_AUTHORIZED_KEY_FILE required; no password
#      fallback), drop-in /etc/ssh/sshd_config.d/90-usb-sentinel.conf, ufw rule
#      only when ufw is already active. See scripts/ssh-access.sh
#   8. ClamAV signature database (freshclam inside the container)
#   9. Offline self-check (selftest with --network none, uv run --offline)
#  10. Arms the kiosk (GNOME lockdown + autostart enabled) unless the kiosk
#      question was answered no or INSTALL_KIOSK=0, then prints the escape
#      routes, the optional steps and the "before unplugging the LAN" checklist.
#      After a reboot the display app starts full screen on power-on.
#
# What it leaves to the operator (printed at the end):
#   - Touch panel driver (vendor scripts are never executed by this project)
#   - Port calibration (optional: uncalibrated ports show P? and keep working)
#   - Nominal capacity/speed in config.toml
#   - Switching the display to "Scan + erase" (M key or the mode button, code shown on
#     screen). The installer enables erasing on the host (allow_destructive = true,
#     USB_SENTINEL_DEVICE_ACCESS=rw; INSTALL_ALLOW_ERASE=0 for scan-only stations).
#   The app scales its 480x320 design to the actual screen; no resolution setting
#   is needed.
set -euo pipefail

readonly REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly DISPLAY_USER="${DISPLAY_USER:-${SUDO_USER:-}}"
readonly INSTALL_YES="${INSTALL_YES:-0}"
readonly INSTALL_SSH="${INSTALL_SSH:-0}"
# Kiosk arming at the end: 0 = never, 1 = always, empty = ask (INSTALL_YES=1 answers yes).
readonly INSTALL_KIOSK="${INSTALL_KIOSK:-}"
# Boot video hint (video=HDMI-A-1:480x320@60 in cmdline.txt): opt-in, the app scales anyway.
readonly INSTALL_VIDEO_HINT="${INSTALL_VIDEO_HINT:-0}"
# Host erase gates (config.toml allow_destructive, docker.env USB_SENTINEL_DEVICE_ACCESS):
# 1 (default) enables them, 0 makes a scan-only station. Read by install-docker.sh.
readonly INSTALL_ALLOW_ERASE="${INSTALL_ALLOW_ERASE:-1}"
# Admin user allowed to log in over SSH: the sudo caller, never the display (kiosk) user.
readonly SSH_ADMIN_USER="${SSH_ADMIN_USER:-${SUDO_USER:-}}"
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

confirm_default_yes() {
    # Like confirm, but an empty answer means yes; n, N, no and NO decline. INSTALL_YES=1 answers yes.
    if [[ "${INSTALL_YES}" == 1 ]]; then
        return 0
    fi
    local answer
    read -r -p "$1 [Y/n] " answer
    [[ ! "${answer}" =~ ^[Nn]([Oo])?$ ]]
}

run_as_user() {
    # Run a command as the display user with that user's HOME and ~/.local/bin on PATH.
    sudo -u "${DISPLAY_USER}" -H env "PATH=/home/${DISPLAY_USER}/.local/bin:${PATH}" "$@"
}

# Shared helpers, sourced before step 1 so steps 6 and 10 can use them:
#   kiosk-lockdown.sh: kiosk_gsettings_user, kiosk_autostart_hide, kiosk_resolution_hint, kiosk_arm
#   boot-video-hint.sh: video_hint_append (step 6, only with INSTALL_VIDEO_HINT=1)
source "${REPO_DIR}/scripts/kiosk-lockdown.sh"
source "${REPO_DIR}/scripts/boot-video-hint.sh"

# ---------------------------------------------------------------- 1. preflight
log "1/10 Preflight checks"
[[ ${EUID} -eq 0 ]] || die "run with sudo"
[[ "$(uname -s)" == Linux ]] || die "run on Linux (Ubuntu)"
[[ "${INSTALL_ALLOW_ERASE}" == 0 || "${INSTALL_ALLOW_ERASE}" == 1 ]] \
    || die "INSTALL_ALLOW_ERASE must be 0 or 1"
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

# All questions are asked here so that the rest of the run needs no operator.
# Steps 3 and 8 only read RUN_UV_INSTALLER and RUN_FRESHCLAM.
readonly UV_BIN="${USER_HOME}/.local/bin/uv"
RUN_UV_INSTALLER=0
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
    confirm "Run this installer as ${DISPLAY_USER} (in step 3/10)?" \
        || die "uv installation cancelled; install uv manually and re-run"
    RUN_UV_INSTALLER=1
fi
readonly RUN_UV_INSTALLER
if confirm "Run freshclam in step 8/10 to update ${CLAMAV_DB_DIR}? (takes a few minutes)"; then
    readonly RUN_FRESHCLAM=1
else
    readonly RUN_FRESHCLAM=0
fi
# Precedence: INSTALL_KIOSK (0 or 1) > INSTALL_YES=1 (yes) > prompt (default yes).
if [[ "${INSTALL_KIOSK}" == 0 ]]; then
    readonly ARM_KIOSK=0; echo "kiosk: not armed at the end (INSTALL_KIOSK=0)"
elif [[ "${INSTALL_KIOSK}" == 1 ]]; then
    readonly ARM_KIOSK=1; echo "kiosk: armed at the end (INSTALL_KIOSK=1)"
elif confirm_default_yes "Arm the kiosk (autostart + lockdown) at the end?"; then
    readonly ARM_KIOSK=1
else
    readonly ARM_KIOSK=0
fi
echo "No more questions; the rest runs unattended (about 15 minutes). INSTALL_YES=1 skips the questions entirely."

# ---------------------------------------------------------------- 2. apt
log "2/10 Installing apt packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
# docker.io: inspection container. fonts-noto-cjk: Japanese UI text.
# python3-tk: fallback when uv's Python lacks tkinter. curl/ca-certificates: uv download.
apt-get install -y --no-install-recommends \
    docker.io curl ca-certificates fonts-noto-cjk python3 python3-tk
systemctl enable --now docker.service

# ---------------------------------------------------------------- 3. uv
log "3/10 Installing uv into the display user's ~/.local/bin"
if [[ "${RUN_UV_INSTALLER}" == 0 ]]; then
    echo "uv already present: $("${UV_BIN}" --version)"
else
    # Installer downloaded, shown and confirmed in step 1.
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
log "4/10 backend (Docker image, udev, systemd, result directories)"
# INSTALL_ALLOW_ERASE is readonly here, so it is exported rather than set per command.
export INSTALL_ALLOW_ERASE
USB_SENTINEL_IMAGE="${IMAGE}" bash "${REPO_DIR}/scripts/install-docker.sh"

# ---------------------------------------------------------------- 5. frontend
log "5/10 Syncing frontend dependencies (the app starts with --offline from now on)"
run_as_user "${UV_BIN}" sync --frozen --project "${REPO_DIR}/frontend"
if run_as_user "${UV_BIN}" run --offline --frozen --no-sync --project "${REPO_DIR}/frontend" \
        python -c "import tkinter" 2>/dev/null; then
    echo "tkinter: available in uv's Python"
else
    warn "tkinter cannot be imported from uv's Python; switch to apt's python3-tk"
    warn "see frontend/README.md, 'Display setup' step 8, for the alternative .desktop Exec line"
fi

# ---------------------------------------------------------------- 6. display user
log "6/10 Configuring the display user ${DISPLAY_USER}"

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

# GNOME automount and sleep settings live in the user's dconf. kiosk_gsettings_user
# (scripts/kiosk-lockdown.sh) uses the running session bus when the user is logged in,
# otherwise a throwaway dbus session.
if command -v gsettings >/dev/null; then
    # With automount on, run-docker.sh refuses the disk ("host-mounted disk refused").
    kiosk_gsettings_user "${DISPLAY_USER}" set org.gnome.desktop.media-handling automount false
    kiosk_gsettings_user "${DISPLAY_USER}" set org.gnome.desktop.media-handling automount-open false
    kiosk_gsettings_user "${DISPLAY_USER}" set org.gnome.desktop.session idle-delay 0
    kiosk_gsettings_user "${DISPLAY_USER}" set org.gnome.desktop.screensaver lock-enabled false
    kiosk_gsettings_user "${DISPLAY_USER}" set org.gnome.desktop.screensaver idle-activation-enabled false
    kiosk_gsettings_user "${DISPLAY_USER}" set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
    echo "GNOME: automount off, sleep and lock off"
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
# Hidden=true so an aborted run never leaves autostart armed; step 10 (kiosk_arm) or
# scripts/kiosk-enable.sh removes that line.
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
echo "autostart: ${DESKTOP_FILE} (disabled until step 10/10)"
grep '^Exec=' "${DESKTOP_FILE}"

# Boot video hint (scripts/boot-video-hint.sh), opt-in. The status line is kept for step 10;
# video_hint_append writes its warnings to stderr, so they are not captured.
if [[ "${INSTALL_VIDEO_HINT}" == 1 ]]; then
    VIDEO_HINT_STATE="$(video_hint_append)"
    if [[ -z "${VIDEO_HINT_STATE}" ]]; then
        VIDEO_HINT_STATE="boot video hint: not applied (see the warning in step 6/10)"
    fi
    echo "${VIDEO_HINT_STATE}"
else
    VIDEO_HINT_STATE="boot video hint: skipped (opt in with INSTALL_VIDEO_HINT=1; the app scales, so it is not required)"
    echo "${VIDEO_HINT_STATE}"
fi

# ---------------------------------------------------------------- 7. SSH (opt-in)
log "7/10 SSH maintenance access (INSTALL_SSH=${INSTALL_SSH})"
source "${REPO_DIR}/scripts/ssh-access.sh"
if [[ "${INSTALL_SSH}" == 1 ]]; then
    [[ "${SSH_ADMIN_USER}" != "${DISPLAY_USER}" ]] \
        || warn "SSH admin user equals the display user ${DISPLAY_USER}; prefer a separate admin account"
    ssh_setup "${SSH_ADMIN_USER}" "$(getent passwd "${SSH_ADMIN_USER}" | cut -d: -f6)"
else
    echo "skipped (set INSTALL_SSH=1 with SSH_AUTHORIZED_KEY=... to enable key-only SSH)"
fi

# ---------------------------------------------------------------- 8. ClamAV DB
log "8/10 Fetching the ClamAV signature database (cannot be updated once isolated)"
if [[ "${RUN_FRESHCLAM}" == 1 ]]; then   # answered in step 1
    docker run --rm --entrypoint freshclam \
        --mount "type=bind,src=${CLAMAV_DB_DIR},dst=/var/lib/clamav" \
        "${IMAGE}" --user=root --stdout || warn "freshclam failed; update later using the README steps"
else
    warn "signature database not fetched; run README section '2. ClamAV definition DB' before isolating"
fi

# ---------------------------------------------------------------- 9. self-check
log "9/10 Verifying that everything works offline"
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

# --------------------------------------------------------------- 10. arm the kiosk
if [[ "${ARM_KIOSK}" == 1 ]]; then
    log "10/10 Arming the kiosk (autostart + GNOME lockdown)"
    kiosk_resolution_hint "${USER_HOME}"
    # Mirrors step 6: a missing gsettings only warns here; kiosk_arm dies for kiosk-enable.sh.
    if command -v gsettings >/dev/null; then
        kiosk_arm "${DISPLAY_USER}" "${DESKTOP_FILE}"
        KIOSK_STATE="armed: reboot (sudo reboot) and the display starts full screen on power-on"
    else
        warn "gsettings not found; kiosk not armed. Install GNOME, then run scripts/kiosk-enable.sh"
        KIOSK_STATE="not armed: gsettings missing; run  sudo bash ${REPO_DIR}/scripts/kiosk-enable.sh"
    fi
else
    log "10/10 Installation complete (kiosk NOT armed)"
    KIOSK_STATE="not armed: run  sudo bash ${REPO_DIR}/scripts/kiosk-enable.sh  when ready"
fi
if [[ "${INSTALL_SSH}" == 1 ]]; then
    SSH_RECOVERY_HINT="SSH as ${SSH_ADMIN_USER} (enabled in step 7/10; works while the LAN is plugged in)"
else
    SSH_RECOVERY_HINT="SSH (not installed; re-run with INSTALL_SSH=1 SSH_AUTHORIZED_KEY=... to add it)"
fi
cat <<EOF

Kiosk: ${KIOSK_STATE}

[Escape routes]
  Once armed, the app covers the whole screen, hides the cursor and takes focus back
  from every other window; GNOME keeps no shortcut except Ctrl+Alt+F1..F12
  (scripts/kiosk-lockdown.sh). To get out:
    - Ctrl+Alt+F3 (text console, password) or ${SSH_RECOVERY_HINT}, then
        pkill -f usb-sentinel-display                      stop the app until the next login
        sudo bash ${REPO_DIR}/scripts/kiosk-disable.sh     undo autostart and lockdown
    - Ctrl+Alt+F1 returns to the graphical session.

[Port calibration (optional, later)]
  Uncalibrated ports show P? and the screen keeps working. Over SSH or from the
  text console run
      ${UV_BIN} run --offline --frozen --no-sync \\
          --project ${REPO_DIR}/frontend usb-sentinel-display calibrate
  and write the [[ports]] table into ${DISPLAY_CONF}; the app reads it at the next start.

[Boot video hint]
  ${VIDEO_HINT_STATE}
  undo: cp /boot/firmware/cmdline.txt.bak /boot/firmware/cmdline.txt

[Optional]
  - Touch panel driver (the keyboard covers every action): https://osoyoo.com/ja/?p=56145
  - /etc/usb-sentinel/config.toml: [capacity] nominal_bytes, [speed] nominal_*_mb_s.
    Capacity and speed checks ERASE the sample. The installer enabled erasing on the
      host (allow_destructive = true, USB_SENTINEL_DEVICE_ACCESS=rw; re-run with
      INSTALL_ALLOW_ERASE=0 for a scan-only station). The operator switches the display
      to "Scan + erase" with M or the mode button (code shown on screen).
  - The app scales to any screen; no resolution setting is needed.
  - GDM autologin stays on in both kiosk states (${GDM_CONF}; remove the two
    AutomaticLogin lines to turn it off).

[Checklist before unplugging the LAN]
 [ ] backend selftest exited 0 (see step 9/10 above)
 [ ] After a reboot the full-screen display shows four tiles
 [ ] A disposable USB stick shows "Scanning" and then a verdict
 [ ] result.json appears under /var/lib/usb-sentinel/results/
 [ ] The on-screen QR codes scan with a phone (summary QR and the 3 detail pages)
 [ ] The ClamAV database date is recent (result.json: steps.malware.details.database)

Once all items pass, unplug the wired LAN. No network is needed at boot
(autostart runs with --offline, inspections run with --network none).
EOF
