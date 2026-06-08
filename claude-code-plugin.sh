#!/usr/bin/env bash
set -euo pipefail

# Fill this in for the hosted one-liner build, or pass --installer-manifest-url
# while testing. This manifest points to compiled installer binaries, not the
# CLP marketplace/artifact manifest consumed by the installer binary.
DEFAULT_INSTALLER_MANIFEST_URL=""
DEFAULT_CLP_INSTALL_MANIFEST_URL=""

INSTALLER_MANIFEST_URL="${YSCOPE_CLP_INSTALLER_MANIFEST_URL:-${YSCOPE_CLP_BOOTSTRAP_MANIFEST_URL:-$DEFAULT_INSTALLER_MANIFEST_URL}}"
CLP_INSTALL_MANIFEST_URL="${YSCOPE_CLP_INSTALL_MANIFEST_URL:-$DEFAULT_CLP_INSTALL_MANIFEST_URL}"
CLP_INSTALL_MANIFEST_EXPLICIT=0
if [[ -n "${YSCOPE_CLP_INSTALL_MANIFEST_URL:-}" ]]; then
  CLP_INSTALL_MANIFEST_EXPLICIT=1
fi
INSTALLER_URL="${YSCOPE_CLP_INSTALLER_URL:-}"
INSTALLER_SHA256="${YSCOPE_CLP_INSTALLER_SHA256:-}"
INSTALLER_PATH="${YSCOPE_CLP_INSTALLER_PATH:-}"
ALLOW_INSECURE_HTTP="${YSCOPE_CLP_ALLOW_INSECURE_HTTP:-0}"
BOOTSTRAP_VERBOSE="${YSCOPE_CLP_BOOTSTRAP_VERBOSE:-0}"
BOOTSTRAP_PROGRESS="${YSCOPE_CLP_BOOTSTRAP_PROGRESS:-auto}"
LOCAL_CHECKOUT_MODE="${YSCOPE_CLP_LOCAL_CHECKOUT_MODE:-auto}"

TMP_DIR=""
FORWARD_ARGS=()

usage() {
  cat <<'EOF'
Usage:
  claude-code-plugin.sh [bootstrap options] [-- installer options]

Bootstrap options:
  --installer-manifest-url URL  Manifest with platform-specific installer binaries.
  --installer-url URL           Direct installer binary URL, useful for signed URLs.
  --installer-sha256 SHA256     Expected SHA-256 for --installer-url.
  --installer-path PATH         Local installer binary path, useful for development.
  -h, --help                    Show this help.

Installer options forwarded as environment:
  --manifest-url URL            CLP marketplace/artifact manifest URL.
  --install-root PATH           Marketplace install root.
  --local-marketplace PATH      Install from a local yscope marketplace checkout.
  --accept-terms                Accept pre-release terms non-interactively.

Environment:
  YSCOPE_CLP_INSTALLER_MANIFEST_URL
  YSCOPE_CLP_INSTALLER_URL
  YSCOPE_CLP_INSTALLER_SHA256
  YSCOPE_CLP_INSTALLER_PATH
  YSCOPE_CLP_BOOTSTRAP_VERBOSE=1
  YSCOPE_CLP_BOOTSTRAP_PROGRESS=0
  YSCOPE_CLP_LOCAL_CHECKOUT_MODE=0
  YSCOPE_CLP_INSTALL_MANIFEST_URL
  YSCOPE_CLP_ACCESS_KEY
  YSCOPE_CLP_INSTALL_ROOT
  YSCOPE_CLP_LOCAL_MARKETPLACE_ROOT
  YSCOPE_CLP_ACCEPT_TERMS=1

For curl-pipe usage while the installer manifest URL is not embedded:
  curl -fsSL https://example.yscope.io/clp/claude-code-plugin.sh \
    | bash -s -- --installer-manifest-url https://example.yscope.io/clp/installer-manifest.json \
      --manifest-url https://example.yscope.io/clp/manifest.json
EOF
}

log() {
  case "$BOOTSTRAP_VERBOSE" in
    1|true|TRUE|yes|YES)
      printf '[yscope-clp-bootstrap] %s\n' "$*" >&2
      ;;
  esac
}

die() {
  printf '[yscope-clp-bootstrap] error: %s\n' "$*" >&2
  exit 1
}

cleanup() {
  if [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]]; then
    rm -rf "$TMP_DIR"
  fi
}
trap cleanup EXIT

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "$1 is required"
}

need_value() {
  local option="$1"
  local value="${2:-}"
  [[ -n "$value" ]] || die "$option requires a value"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --installer-manifest-url|--bootstrap-manifest-url)
      need_value "$1" "${2:-}"
      INSTALLER_MANIFEST_URL="$2"
      shift 2
      ;;
    --installer-url)
      need_value "$1" "${2:-}"
      INSTALLER_URL="$2"
      shift 2
      ;;
    --installer-sha256)
      need_value "$1" "${2:-}"
      INSTALLER_SHA256="$2"
      shift 2
      ;;
    --installer-path)
      need_value "$1" "${2:-}"
      INSTALLER_PATH="$2"
      shift 2
      ;;
    --manifest-url)
      need_value "$1" "${2:-}"
      CLP_INSTALL_MANIFEST_URL="$2"
      CLP_INSTALL_MANIFEST_EXPLICIT=1
      FORWARD_ARGS+=("$1" "$2")
      shift 2
      ;;
    --install-root)
      need_value "$1" "${2:-}"
      export YSCOPE_CLP_INSTALL_ROOT="$2"
      FORWARD_ARGS+=("$1" "$2")
      shift 2
      ;;
    --local-marketplace)
      need_value "$1" "${2:-}"
      export YSCOPE_CLP_LOCAL_MARKETPLACE_ROOT="$2"
      FORWARD_ARGS+=("$1" "$2")
      shift 2
      ;;
    --accept-terms)
      export YSCOPE_CLP_ACCEPT_TERMS=1
      FORWARD_ARGS+=("$1")
      shift
      ;;
    --no-link-user-bin)
      # Deprecated compatibility flag. clp-s is always installed plugin-local.
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      FORWARD_ARGS+=("$@")
      break
      ;;
    *)
      FORWARD_ARGS+=("$1")
      shift
      ;;
  esac
done

init_tmp() {
  TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/yscope-clp-bootstrap.XXXXXX")"
  chmod 700 "$TMP_DIR"
}

detect_platform() {
  local os arch libc
  case "$(uname -s)" in
    Linux) os="linux" ;;
    Darwin) os="darwin" ;;
    MINGW*|MSYS*|CYGWIN*) os="win32" ;;
    *) os="$(uname -s | tr '[:upper:]' '[:lower:]')" ;;
  esac

  case "$(uname -m)" in
    x86_64|amd64) arch="x64" ;;
    aarch64|arm64) arch="arm64" ;;
    *) arch="$(uname -m)" ;;
  esac

  libc=""
  if [[ "$os" == "linux" ]]; then
    if ldd --version 2>&1 | grep -qi musl; then
      libc="musl"
    elif ldd --version >/dev/null 2>&1; then
      libc="glibc"
    else
      libc="unknown"
    fi
  fi

  PLATFORM_OS="$os"
  PLATFORM_ARCH="$arch"
  PLATFORM_LIBC="$libc"
}

require_secure_url() {
  local url="$1"
  case "$url" in
    https://*) return ;;
    http://localhost/*|http://localhost:*/*) return ;;
    http://127.0.0.1/*|http://127.0.0.1:*/*) return ;;
    http://\[::1\]/*|http://\[::1\]:*/*) return ;;
  esac
  [[ "$ALLOW_INSECURE_HTTP" == "1" ]] || die "refusing non-HTTPS URL: $url"
}

download_url() {
  local url="$1"
  local output="$2"
  local show_progress="${3:-0}"
  require_secure_url "$url"
  if [[ "$show_progress" == "1" ]]; then
    download_url_with_progress "$url" "$output"
  else
    curl -fsSL --retry 3 --connect-timeout 20 -o "$output" "$url"
  fi
}

content_length() {
  local url="$1"
  curl -fsSIL --retry 3 --connect-timeout 20 "$url" \
    | awk '
      BEGIN { IGNORECASE = 1 }
      /^content-length:/ {
        gsub(/\r/, "", $2)
        contentLength = $2
      }
      END {
        if (contentLength ~ /^[0-9]+$/) {
          print contentLength
        } else {
          exit 1
        }
      }
    '
}

file_size() {
  local file="$1"
  if [[ -f "$file" ]]; then
    wc -c <"$file" | tr -d '[:space:]'
  else
    printf '0\n'
  fi
}

render_download_progress() {
  local output="$1"
  local total="$2"
  local width=28
  local current percent filled empty bar rest

  current="$(file_size "$output")"
  if (( current > total )); then
    current="$total"
  fi

  percent=$(( current * 100 / total ))
  filled=$(( percent * width / 100 ))
  empty=$(( width - filled ))
  printf -v bar '%*s' "$filled" ''
  printf -v rest '%*s' "$empty" ''
  bar="${bar// /=}"
  rest="${rest// /-}"

  printf '\rDownloading installer [%s%s] %3d%%' "$bar" "$rest" "$percent" >&2
}

download_url_with_spinner() {
  local url="$1"
  local output="$2"
  local curl_pid status frame_index frame
  local frames='-\|/'

  curl -fsSL --retry 3 --connect-timeout 20 -o "$output" "$url" &
  curl_pid="$!"

  frame_index=0
  while kill -0 "$curl_pid" 2>/dev/null; do
    frame="${frames:frame_index:1}"
    printf '\rDownloading installer %s' "$frame" >&2
    frame_index=$(( (frame_index + 1) % 4 ))
    sleep 0.5
  done

  status=0
  wait "$curl_pid" || status="$?"
  if [[ "$status" == "0" ]]; then
    printf '\rDownloading installer complete\n' >&2
  else
    printf '\rDownloading installer failed\n' >&2
  fi
  return "$status"
}

download_url_with_progress() {
  local url="$1"
  local output="$2"
  local total curl_pid status

  total="$(content_length "$url" || true)"
  if [[ ! "$total" =~ ^[0-9]+$ || "$total" -le 0 ]]; then
    download_url_with_spinner "$url" "$output"
    return
  fi

  curl -fsSL --retry 3 --connect-timeout 20 -o "$output" "$url" &
  curl_pid="$!"

  while kill -0 "$curl_pid" 2>/dev/null; do
    render_download_progress "$output" "$total"
    sleep 0.5
  done

  status=0
  wait "$curl_pid" || status="$?"
  if [[ "$status" == "0" ]]; then
    render_download_progress "$output" "$total"
    printf '\n' >&2
  else
    printf '\rDownloading installer failed\n' >&2
  fi
  return "$status"
}

should_show_progress() {
  case "$BOOTSTRAP_PROGRESS" in
    0|false|FALSE|no|NO) return 1 ;;
    1|true|TRUE|yes|YES) [[ -t 2 ]] ;;
    auto|AUTO|"") [[ -t 2 ]] ;;
    *) [[ -t 2 ]] ;;
  esac
}

sha256_file() {
  local file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print tolower($1)}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file" | awk '{print tolower($1)}'
  else
    die "sha256sum or shasum is required for checksum verification"
  fi
}

verify_sha256() {
  local file="$1"
  local expected="$2"
  [[ -n "$expected" ]] || return
  local actual
  actual="$(sha256_file "$file")"
  expected="$(printf '%s' "$expected" | tr '[:upper:]' '[:lower:]')"
  [[ "$actual" == "$expected" ]] || die "SHA-256 mismatch for $(basename "$file")"
}

prepare_installer_binary() {
  local downloaded="$1"
  case "$downloaded" in
    *.gz)
      require_command gzip
      local installer="${downloaded%.gz}"
      gzip -dc "$downloaded" > "$installer"
      chmod 700 "$installer"
      printf '%s\n' "$installer"
      ;;
    *)
      chmod 700 "$downloaded"
      printf '%s\n' "$downloaded"
      ;;
  esac
}

is_false() {
  case "$1" in
    0|false|FALSE|no|NO) return 0 ;;
    *) return 1 ;;
  esac
}

find_local_project_root() {
  local candidates=()
  candidates+=("$PWD")

  if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
    local script_dir
    script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
    candidates+=("$script_dir")
  fi

  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -f "$candidate/yscope/.claude-plugin/marketplace.json" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

configure_local_checkout_if_present() {
  if is_false "$LOCAL_CHECKOUT_MODE"; then
    return 0
  fi
  if [[ "$CLP_INSTALL_MANIFEST_EXPLICIT" == "1" ]]; then
    return 0
  fi

  local project_root
  project_root="$(find_local_project_root || true)"
  [[ -n "$project_root" ]] || return 0

  CLP_INSTALL_MANIFEST_URL=""
  unset YSCOPE_CLP_INSTALL_MANIFEST_URL
  export YSCOPE_CLP_LOCAL_MARKETPLACE_ROOT="$project_root/yscope"
  log "Using local checkout marketplace: $YSCOPE_CLP_LOCAL_MARKETPLACE_ROOT"

  if [[ -z "$INSTALLER_PATH" && -x "$project_root/installer/dist/yscope-clp-installer" ]]; then
    INSTALLER_PATH="$project_root/installer/dist/yscope-clp-installer"
    log "Using local installer binary: $INSTALLER_PATH"
  fi
}

url_basename() {
  local url_path name fallback
  url_path="${1%%\?*}"
  fallback="$2"
  name="$(basename "$url_path")"
  if [[ -z "$name" || "$name" == "." || "$name" == "/" ]]; then
    name="$fallback"
  fi
  printf '%s\n' "$name"
}

parse_installer_manifest() {
  local manifest_file="$1"
  mapfile -t INSTALLER_VALUES < <(
    python3 - "$manifest_file" "$PLATFORM_OS" "$PLATFORM_ARCH" "$PLATFORM_LIBC" <<'PY'
import json
import sys

path, os_name, arch, libc = sys.argv[1:5]
with open(path, "r", encoding="utf-8") as f:
    manifest = json.load(f)

installers = manifest.get("installers")
if not isinstance(installers, list) or not installers:
    raise SystemExit("installer manifest is missing installers")

match = None
for installer in installers:
    if installer.get("os") != os_name:
        continue
    if installer.get("arch") != arch:
        continue
    installer_libc = installer.get("libc")
    if os_name == "linux" and installer_libc and installer_libc != libc:
        continue
    match = installer
    break

if match is None:
    platform = f"{os_name}/{arch}" + (f"/{libc}" if libc else "")
    raise SystemExit(f"no installer binary in manifest for {platform}")

for value in [match.get("url", ""), match.get("sha256", "")]:
    print(value)
PY
  )

  [[ ${#INSTALLER_VALUES[@]} -eq 2 ]] || die "failed to parse installer manifest"
  INSTALLER_URL="${INSTALLER_VALUES[0]}"
  INSTALLER_SHA256="${INSTALLER_VALUES[1]}"
  [[ -n "$INSTALLER_URL" ]] || die "matched installer manifest entry is missing url"
}

resolve_installer() {
  if [[ -n "$INSTALLER_PATH" ]]; then
    [[ -x "$INSTALLER_PATH" ]] || die "installer path is not executable: $INSTALLER_PATH"
    printf '%s\n' "$INSTALLER_PATH"
    return
  fi

  require_command curl
  init_tmp

  if [[ -z "$INSTALLER_URL" ]]; then
    require_command python3
    [[ -n "$INSTALLER_MANIFEST_URL" ]] \
      || die "installer manifest URL is required; use --installer-manifest-url or set YSCOPE_CLP_INSTALLER_MANIFEST_URL"
    local manifest_file
    manifest_file="$TMP_DIR/installer-manifest.json"
    log "Downloading installer manifest"
    download_url "$INSTALLER_MANIFEST_URL" "$manifest_file"
    parse_installer_manifest "$manifest_file"
  fi

  local output
  output="$TMP_DIR/$(url_basename "$INSTALLER_URL" "yscope-clp-installer")"
  log "Downloading installer binary"
  if should_show_progress; then
    download_url "$INSTALLER_URL" "$output" 1
  else
    download_url "$INSTALLER_URL" "$output"
  fi
  verify_sha256 "$output" "$INSTALLER_SHA256"
  prepare_installer_binary "$output"
}

main() {
  configure_local_checkout_if_present

  if [[ -n "$CLP_INSTALL_MANIFEST_URL" ]]; then
    export YSCOPE_CLP_INSTALL_MANIFEST_URL="$CLP_INSTALL_MANIFEST_URL"
  fi

  require_command uname
  require_command mktemp
  require_command chmod
  require_command rm
  detect_platform
  log "Detected platform: ${PLATFORM_OS}/${PLATFORM_ARCH}${PLATFORM_LIBC:+/$PLATFORM_LIBC}"

  local installer
  installer="$(resolve_installer)"
  log "Starting installer"

  if { exec 3</dev/tty; } 2>/dev/null; then
    exec "$installer" "${FORWARD_ARGS[@]}" <&3
  fi
  exec "$installer" "${FORWARD_ARGS[@]}"
}

main "$@"
