# ClamAV signature database helpers shared by install-all.sh and update-clamav.sh.
#
# Sourced, never executed: only functions are defined. Portable to macOS bash 3.2 so the
# tests can source it with a fake docker on PATH.
#
#   clamav_db_present DIR            0 when main, daily and bytecode each have a non-empty
#                                    DIR/<name>.cvd or DIR/<name>.cld
#   clamav_fetch IMAGE DIR [N] [S]   run freshclam inside IMAGE up to N times (default 3),
#                                    S seconds apart (default CLAMAV_RETRY_DELAY or 20),
#                                    then return clamav_db_present DIR
#   clamav_die_hint REPO_DIR DIR     the message shown when the database is still missing

clamav_db_present() {
    local dir="$1" name
    for name in main daily bytecode; do
        [[ -s "${dir}/${name}.cvd" || -s "${dir}/${name}.cld" ]] || return 1
    done
}

clamav_fetch() {
    local image="$1" dir="$2" attempts="${3:-3}" delay="${4:-${CLAMAV_RETRY_DELAY:-20}}"
    local attempt=1
    while :; do
        echo "freshclam: attempt ${attempt}/${attempts}"
        if docker run --rm --entrypoint freshclam \
                --mount "type=bind,src=${dir},dst=/var/lib/clamav" \
                "${image}" --user=root --stdout; then
            break
        fi
        if (( attempt >= attempts )); then
            printf 'warning: freshclam failed %s times\n' "${attempts}" >&2
            break
        fi
        attempt=$((attempt + 1))
        sleep "${delay}"
    done
    clamav_db_present "${dir}"
}

clamav_die_hint() {
    local repo_dir="$1" dir="$2"
    printf '%s\n' \
        "ClamAV signature database missing in ${dir} (need main, daily and bytecode .cvd/.cld)." \
        "The kiosk cannot detect malware without it (every inspection would be Inconclusive)." \
        "Connect the LAN and run:  sudo bash ${repo_dir}/scripts/update-clamav.sh"
}
