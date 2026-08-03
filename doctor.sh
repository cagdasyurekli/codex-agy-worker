#!/usr/bin/env bash
# Compatibility entry point; the distributable skill owns the canonical doctor.
set -uo pipefail

doctor_launcher_fail() {
    echo "doctor: launcher location is unavailable" >&2
    exit 3
}

doctor_resolve_source() {
    local source_path="$1" source_dir link_target link_count=0

    case "$source_path" in
        */*) ;;
        *) source_path="$(command -v -- "$source_path" 2>/dev/null)" || return 1 ;;
    esac
    case "$source_path" in
        *$'\n'*|*$'\r'*|'') return 1 ;;
    esac

    while [[ -L "$source_path" ]]; do
        (( link_count < 40 )) || return 1
        source_dir="$(CDPATH= cd -- "${source_path%/*}" 2>/dev/null && pwd -P)" \
            || return 1
        link_target="$(/usr/bin/readlink "$source_path" 2>/dev/null)" || return 1
        case "$link_target" in
            *$'\n'*|*$'\r'*|'') return 1 ;;
            /*) source_path="$link_target" ;;
            *) source_path="$source_dir/$link_target" ;;
        esac
        link_count=$((link_count + 1))
    done

    [[ -f "$source_path" && "${source_path##*/}" == 'doctor.sh' ]] || return 1
    printf '%s\n' "$source_path"
}

doctor_launcher_owned_directory() {
    local parent="$1" name="$2" candidate canonical
    candidate="$parent/$name"
    [[ -d "$candidate" && ! -L "$candidate" ]] || return 1
    canonical="$(CDPATH= cd -- "$candidate" 2>/dev/null && pwd -P)" || return 1
    [[ "$canonical" == "$candidate" ]] || return 1
    case "$canonical" in
        "$parent"/*) printf '%s\n' "$canonical" ;;
        *) return 1 ;;
    esac
}

doctor_launcher_main() {
    local source_path script_dir component_root runtime_doctor

    source_path="$(doctor_resolve_source "${BASH_SOURCE[0]}")" \
        || doctor_launcher_fail
    script_dir="$(CDPATH= cd -- "${source_path%/*}" 2>/dev/null && pwd -P)" \
        || doctor_launcher_fail
    component_root="$(doctor_launcher_owned_directory "$script_dir" skills)" \
        || doctor_launcher_fail
    component_root="$(doctor_launcher_owned_directory "$component_root" agy-worker)" \
        || doctor_launcher_fail
    component_root="$(doctor_launcher_owned_directory "$component_root" runtime)" \
        || doctor_launcher_fail
    runtime_doctor="$component_root/doctor.sh"
    [[ -f "$runtime_doctor" && -x "$runtime_doctor" && ! -L "$runtime_doctor" ]] \
        || doctor_launcher_fail
    exec "$runtime_doctor" "$@"
    doctor_launcher_fail
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    doctor_launcher_main "$@"
fi
