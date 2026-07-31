#!/usr/bin/env bash
set -euo pipefail

usage() {
    printf '%s\n' "Usage: scripts/run-security-audit.sh [source|artifacts|image IMAGE]"
}

if (($# < 1)); then
    usage >&2
    exit 2
fi

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "$script_directory/.." && pwd)"
temporary_directory="$(mktemp -d)"
trap 'rm -rf -- "$temporary_directory"' EXIT

download_verified() {
    local url="$1"
    local expected_sha256="$2"
    local destination="$3"
    curl --fail --silent --show-error --location "$url" --output "$destination"
    printf '%s  %s\n' "$expected_sha256" "$destination" | sha256sum --check --status
}

install_native_scanners() {
    local architecture
    local actionlint_sha256
    local actionlint_architecture
    local gitleaks_architecture
    local gitleaks_sha256
    local trivy_architecture
    local trivy_sha256
    architecture="$(uname -m)"
    case "$architecture" in
        x86_64|amd64)
            actionlint_architecture="amd64"
            actionlint_sha256="8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"
            gitleaks_architecture="x64"
            gitleaks_sha256="551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
            trivy_architecture="64bit"
            trivy_sha256="1816b632dfe529869c740c0913e36bd1629cb7688bd5634f4a858c1d57c88b75"
            ;;
        aarch64|arm64)
            actionlint_architecture="arm64"
            actionlint_sha256="325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6"
            gitleaks_architecture="arm64"
            gitleaks_sha256="e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080"
            trivy_architecture="ARM64"
            trivy_sha256="7e3924a974e912e57b4a99f65ece7931f8079584dae12eb7845024f97087bdfd"
            ;;
        *)
            printf '%s\n' "Security scanners do not support this architecture." >&2
            exit 1
            ;;
    esac

    download_verified \
        "https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_${actionlint_architecture}.tar.gz" \
        "$actionlint_sha256" \
        "$temporary_directory/actionlint.tar.gz"
    tar -xzf "$temporary_directory/actionlint.tar.gz" -C "$temporary_directory" actionlint

    download_verified \
        "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_${gitleaks_architecture}.tar.gz" \
        "$gitleaks_sha256" \
        "$temporary_directory/gitleaks.tar.gz"
    tar -xzf "$temporary_directory/gitleaks.tar.gz" -C "$temporary_directory" gitleaks

    download_verified \
        "https://github.com/aquasecurity/trivy/releases/download/v0.69.3/trivy_0.69.3_Linux-${trivy_architecture}.tar.gz" \
        "$trivy_sha256" \
        "$temporary_directory/trivy.tar.gz"
    tar -xzf "$temporary_directory/trivy.tar.gz" -C "$temporary_directory" trivy
}

require_empty_json_array() {
    local report="$1"
    local category="$2"
    if ! python3 - "$report" <<'PY'
import json
import sys
from pathlib import Path

value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
raise SystemExit(0 if isinstance(value, list) and not value else 1)
PY
    then
        printf '%s\n' "$category findings detected; report contents suppressed." >&2
        exit 1
    fi
}

require_clean_trivy_report() {
    local report="$1"
    if ! python3 - "$report" <<'PY'
import json
import sys
from pathlib import Path

document = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
findings = 0
for result in document.get("Results") or ():
    findings += len(result.get("Vulnerabilities") or ())
    findings += len(result.get("Misconfigurations") or ())
    findings += len(result.get("Secrets") or ())
raise SystemExit(0 if findings == 0 else 1)
PY
    then
        printf '%s\n' "Trivy High/Critical findings detected; report contents suppressed." >&2
        exit 1
    fi
}

run_source_audit() {
    install_native_scanners
    cd -- "$project_root"
    uvx --from 'bandit==1.9.4' bandit -q -r src

    uv export --locked --all-groups --no-emit-project --no-hashes \
        --format requirements-txt --output-file "$temporary_directory/requirements.txt" \
        >/dev/null
    uvx --from 'pip-audit==2.10.1' pip-audit \
        --requirement "$temporary_directory/requirements.txt" --no-deps --disable-pip

    "$temporary_directory/actionlint" -color
    uvx --from 'zizmor==1.28.0' zizmor --pedantic .github/workflows

    "$temporary_directory/gitleaks" git --redact --no-banner --exit-code 0 \
        --report-format json --report-path "$temporary_directory/gitleaks.json" . \
        >"$temporary_directory/gitleaks.stdout" 2>"$temporary_directory/gitleaks.stderr"
    require_empty_json_array "$temporary_directory/gitleaks.json" "Gitleaks"
    "$temporary_directory/gitleaks" dir --redact --no-banner --exit-code 0 \
        --report-format json --report-path "$temporary_directory/gitleaks-tree.json" . \
        >"$temporary_directory/gitleaks-tree.stdout" \
        2>"$temporary_directory/gitleaks-tree.stderr"
    require_empty_json_array "$temporary_directory/gitleaks-tree.json" "Gitleaks tree"

    "$temporary_directory/trivy" filesystem --quiet --scanners vuln,misconfig,secret \
        --severity HIGH,CRITICAL --exit-code 0 --format json \
        --skip-dirs .git --skip-dirs .venv --output "$temporary_directory/trivy-fs.json" .
    require_clean_trivy_report "$temporary_directory/trivy-fs.json"
}

run_artifact_audit() {
    cd -- "$project_root"
    if ! uv build --out-dir "$temporary_directory/dist" \
        >"$temporary_directory/build.stdout" 2>"$temporary_directory/build.stderr"; then
        printf '%s\n' "Distribution build failed; detailed output suppressed." >&2
        exit 1
    fi
    uv run python scripts/verify-distributions.py "$temporary_directory/dist"
    uv run python scripts/smoke-installed-distribution.py "$temporary_directory/dist"

    uv export --locked --no-dev --no-emit-project --no-hashes \
        --format requirements-txt \
        --output-file "$temporary_directory/runtime-requirements.txt" >/dev/null
    uvx --from 'pip-audit==2.10.1' pip-audit \
        --requirement "$temporary_directory/runtime-requirements.txt" \
        --no-deps --disable-pip \
        --format cyclonedx-json --output "$temporary_directory/hashorb.cdx.json"
    if ! python3 - "$temporary_directory/hashorb.cdx.json" <<'PY'
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
forbidden = (
    "/" + "home" + "/",
    "/" + "Users" + "/",
    ":\\" + "Users" + "\\",
    "rpc_" + "password",
    "cookie_" + "contents",
)
raise SystemExit(0 if not any(marker in text for marker in forbidden) else 1)
PY
    then
        printf '%s\n' "Generated SBOM contains a forbidden private field." >&2
        exit 1
    fi
}

run_image_audit() {
    if (($# != 1)); then
        usage >&2
        exit 2
    fi
    install_native_scanners
    local image="$1"
    "$temporary_directory/trivy" image --quiet --scanners vuln,misconfig,secret \
        --severity HIGH,CRITICAL --exit-code 0 --format json \
        --ignorefile "$project_root/security/trivy-image-ignore.yaml" \
        --output "$temporary_directory/trivy-image.json" "$image"
    require_clean_trivy_report "$temporary_directory/trivy-image.json"
}

case "$1" in
    source)
        (($# == 1)) || { usage >&2; exit 2; }
        run_source_audit
        ;;
    artifacts)
        (($# == 1)) || { usage >&2; exit 2; }
        run_artifact_audit
        ;;
    image)
        shift
        run_image_audit "$@"
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac
