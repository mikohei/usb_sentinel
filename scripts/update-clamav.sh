#!/usr/bin/env bash
# Fetch or refresh the ClamAV signature database and verify that it is complete.
#
# Usage (needs the LAN; safe to re-run at any time):
#   sudo bash scripts/update-clamav.sh
#
# Runs freshclam inside the inspection image (up to 3 attempts), then checks that
# main, daily and bytecode (.cvd or .cld) exist and are non-empty in /var/lib/clamav.
# Exits 1 with the reason when the database is still incomplete.
set -euo pipefail

readonly REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly IMAGE="${USB_SENTINEL_IMAGE:-usb-sentinel:local}"
readonly CLAMAV_DB_DIR="${CLAMAV_DB_DIR:-/var/lib/clamav}"

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

source "${REPO_DIR}/scripts/clamav-db.sh"

[[ ${EUID} -eq 0 ]] || die "run with sudo"
command -v docker >/dev/null || die "docker is missing; run scripts/install-all.sh first"
docker image inspect "${IMAGE}" >/dev/null 2>&1 \
    || die "image ${IMAGE} is missing; run scripts/install-all.sh first"
install -d -m 0755 "${CLAMAV_DB_DIR}"
if ! clamav_fetch "${IMAGE}" "${CLAMAV_DB_DIR}"; then
    clamav_die_hint "${REPO_DIR}" "${CLAMAV_DB_DIR}" >&2
    exit 1
fi
echo "ClamAV signature database OK:"
ls -l "${CLAMAV_DB_DIR}"/main.c[vl]d "${CLAMAV_DB_DIR}"/daily.c[vl]d \
    "${CLAMAV_DB_DIR}"/bytecode.c[vl]d 2>/dev/null || true
