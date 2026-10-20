# Idempotent "KEY = value" line setter shared by the installer scripts.
#
# Sourced, never executed: defining the function has no side effects. Portable to macOS
# bash 3.2 and BSD tools (no sed -i), so the tests run on a developer Mac.
#
#   set_key_line FILE KEY NEW_LINE [append|before-table]
#
# Every line matching ^[[:space:]]*KEY[[:space:]]*= is replaced by NEW_LINE. When KEY is
# absent, NEW_LINE is appended, or with before-table inserted just before the first
# "[table]" header (top-level TOML keys must precede tables); with before-table, lines
# inside tables are never touched. All other lines stay byte-identical. When the result
# equals the current file nothing is written (no .bak, mtime unchanged). Otherwise FILE is
# copied to FILE.bak once per shell (later edits in the same run keep the first backup)
# and rewritten in place, keeping its owner and mode.

_KEYFILE_BACKED_UP="|"

set_key_line() {
    local file="$1" key="$2" line="$3" where="${4:-append}"
    local tmp
    [[ -f "${file}" ]] || { printf 'error: %s is missing\n' "${file}" >&2; return 1; }
    tmp="$(mktemp)"
    awk -v key="${key}" -v line="${line}" -v where="${where}" '
        BEGIN { re = "^[[:space:]]*" key "[[:space:]]*="; done = 0; in_table = 0 }
        /^[[:space:]]*\[/ {
            if (where == "before-table" && !in_table && !done) { print line; done = 1 }
            in_table = 1
        }
        $0 ~ re && !(where == "before-table" && in_table) { print line; done = 1; next }
        { print }
        END { if (!done) print line }
    ' "${file}" > "${tmp}"
    if cmp -s "${tmp}" "${file}"; then
        rm -f "${tmp}"
        return 0
    fi
    case "${_KEYFILE_BACKED_UP}" in
        *"|${file}|"*) ;;
        *) cp -p "${file}" "${file}.bak"; _KEYFILE_BACKED_UP="${_KEYFILE_BACKED_UP}${file}|" ;;
    esac
    cat "${tmp}" > "${file}"
    rm -f "${tmp}"
}
