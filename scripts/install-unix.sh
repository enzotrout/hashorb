#!/usr/bin/env bash
set -euo pipefail

usage() {
    printf '%s\n' "Usage: scripts/install-unix.sh [install|upgrade|uninstall] [--dry-run]"
}

action="install"
dry_run=0
for argument in "$@"; do
    case "$argument" in
        install|upgrade|uninstall)
            action="$argument"
            ;;
        --dry-run)
            dry_run=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'Unsupported argument: %s\n' "$argument" >&2
            usage >&2
            exit 2
            ;;
    esac
done

case "$(uname -s)" in
    Linux|Darwin) ;;
    *)
        printf '%s\n' "This installer supports Linux and macOS only." >&2
        exit 1
        ;;
esac

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "$script_directory/.." && pwd)"

run_visible() {
    printf '+'
    printf ' %q' "$@"
    printf '\n'
    if ((dry_run == 0)); then
        "$@"
    fi
}

if ((dry_run == 0)) && ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' "uv is required and must already be on PATH." >&2
    exit 1
fi

if [[ "$action" == "uninstall" ]]; then
    run_visible uv tool uninstall hashorb
    exit 0
fi

run_visible uv python find --no-python-downloads 3.13
install_arguments=(
    uv tool install
    --no-python-downloads
    --python 3.13
    --force
)
if [[ "$action" == "upgrade" ]]; then
    install_arguments+=(--upgrade)
fi
install_arguments+=("$project_root")
run_visible "${install_arguments[@]}"

printf '%s\n' "Installation complete. Run 'hashorb doctor' from your configuration directory."
