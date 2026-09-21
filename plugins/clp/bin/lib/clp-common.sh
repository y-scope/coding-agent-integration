#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC2034  # consumed by sourced wrappers
# Remote semantic-cache endpoint used as the last resort when the endpoint was
# not supplied inline, in the environment, or in the config file. This is the
# default deployment clp-s itself documents; it fronts the regional backends,
# so the list holds one entry rather than per-region hostnames. Still an array:
# the resolver walks it in order and uses the first that passes /health.
#
# The plugin never starts an embedding server of its own (no Docker, no local
# model download) — it only ever talks to an already-running server. To use a
# server you host yourself (including one on localhost), configure it via
# --semantic-endpoint, CLP_SEMANTIC_ENDPOINT, or the semantic-endpoint config
# file; it is not auto-detected.
DEFAULT_SEMANTIC_ENDPOINTS=(
  "https://ca-central-semantic-cache.yscope.ai"
)
# Default local embedded-semantic-cache directory (auto-enabled by the search
# wrapper under the plugin config dir). Override per-invocation with
# --semantic-cache-dir or the CLP_SEMANTIC_CACHE_DIR environment variable; set
# either to "none" to disable the local cache and use remote-only /v1/similarity.
DEFAULT_SEMANTIC_CACHE_SUBDIR="semantic-cache"
# Cold-tier entry capacity for the auto-enabled local cache. Matches clp-s's
# own default (10 M entries, ~4 GB cold file on disk). Override with
# --semantic-cache-cold-capacity or CLP_SEMANTIC_CACHE_COLD_CAPACITY.
DEFAULT_SEMANTIC_CACHE_COLD_CAPACITY="10000000"

default_semantic_cache_dir() {
  printf '%s/%s\n' "$(clp_config_dir)" "$DEFAULT_SEMANTIC_CACHE_SUBDIR"
}

# Config file holding the semantic (embedding) server URL, one line. Sits
# between the environment and the built-in remote defaults in the precedence
# chain, so a user can pin an endpoint once instead of exporting it per shell.
semantic_endpoint_config_file() {
  if [[ -n "${CLP_SEMANTIC_ENDPOINT_FILE:-}" ]]; then
    printf '%s\n' "$CLP_SEMANTIC_ENDPOINT_FILE"
  else
    printf '%s/semantic-endpoint\n' "$(clp_config_dir)"
  fi
}

# Reads the endpoint from the config file, ignoring blank lines and #comments.
# Return: 0 endpoint printed, 1 no config file (fall through to the defaults),
# 2 the file exists but is unusable (unreadable, or no endpoint in it) — the
# caller must treat that as a hard error rather than silently using a default.
read_configured_semantic_endpoint() {
  local config_file line
  config_file="$(semantic_endpoint_config_file)"
  [[ -e "$config_file" ]] || return 1
  if [[ ! -r "$config_file" ]]; then
    echo "error: semantic endpoint config file is not readable: $config_file" >&2
    return 2
  fi

  while IFS= read -r line || [[ -n "$line" ]]; do
    # Strip comments, a trailing CR (CRLF files), and surrounding whitespace.
    line="${line%%#*}"
    line="${line%$'\r'}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -n "$line" ]] || continue
    printf '%s\n' "$line"
    return 0
  done < "$config_file"

  echo "error: semantic endpoint config file contains no endpoint: $config_file" >&2
  return 2
}

# Resolves the semantic endpoint by precedence:
#   1. $1              inline (--semantic-endpoint)
#   2. CLP_SEMANTIC_ENDPOINT / CLAUDE_PLUGIN_OPTION_SEMANTIC_ENDPOINT
#   3. config file     (~/.config/yscope-clp-plugin/semantic-endpoint)
#   4. DEFAULT_SEMANTIC_ENDPOINTS, first one that passes /health
#
# Sources 1-3 name one specific server, so a failure there is a hard error — an
# unhealthy endpoint, an unusable config file, or a non-HTTPS URL all stop the
# run. Silently falling back to a yscope-hosted endpoint would ship log text
# somewhere the user did not choose. Only the built-in defaults in 4 are probed
# and skipped on failure.
#
# Prints the endpoint on stdout; diagnostics go to stderr.
# Return: 0 ok, 1 nothing configured/reachable, 2 rejected URL or bad config.
resolve_semantic_endpoint() {
  local inline="${1:-}"
  local endpoint source candidate rc

  if [[ -n "$inline" ]]; then
    endpoint="$inline"
    source="--semantic-endpoint"
  elif [[ -n "${CLP_SEMANTIC_ENDPOINT:-}" ]]; then
    endpoint="$CLP_SEMANTIC_ENDPOINT"
    source="CLP_SEMANTIC_ENDPOINT"
  elif [[ -n "${CLAUDE_PLUGIN_OPTION_SEMANTIC_ENDPOINT:-}" ]]; then
    endpoint="$CLAUDE_PLUGIN_OPTION_SEMANTIC_ENDPOINT"
    source="CLAUDE_PLUGIN_OPTION_SEMANTIC_ENDPOINT"
  else
    # Assign separately from `local` so the function's return code survives.
    endpoint="$(read_configured_semantic_endpoint)"
    rc=$?
    if [[ "$rc" -eq 0 ]]; then
      source="$(semantic_endpoint_config_file)"
    elif [[ "$rc" -eq 2 ]]; then
      # The user pinned a config file but it is unusable — fail rather than
      # quietly sending their logs to a built-in default.
      return 2
    else
      for candidate in "${DEFAULT_SEMANTIC_ENDPOINTS[@]}"; do
        if check_semantic_endpoint "$candidate" >/dev/null 2>&1; then
          printf '%s\n' "$candidate"
          echo "Semantic endpoint: $candidate (built-in default)" >&2
          return 0
        fi
      done
      echo "error: no semantic endpoint is configured or reachable." >&2
      echo "  This plugin never starts an embedding server; it uses one you point it at." >&2
      echo "  Set one of (highest precedence first):" >&2
      echo "    --semantic-endpoint URL" >&2
      echo "    CLP_SEMANTIC_ENDPOINT=URL" >&2
      echo "    echo URL > $(semantic_endpoint_config_file)" >&2
      echo "  Built-in defaults tried (all unreachable):" >&2
      printf '    - %s\n' "${DEFAULT_SEMANTIC_ENDPOINTS[@]}" >&2
      return 1
    fi
  fi

  require_secure_url "$endpoint" || return 2
  if ! check_semantic_endpoint "$endpoint"; then
    echo "error: semantic endpoint from $source is unavailable: $endpoint" >&2
    echo "  Check that the embedding server is running and reachable." >&2
    return 1
  fi
  printf '%s\n' "$endpoint"
  echo "Semantic endpoint: $endpoint (from $source)" >&2
}

resolve_clp_s() {
  local script_dir plugin_root candidate

  if [[ -n "${CLP_S_BIN:-}" ]]; then
    if [[ -x "$CLP_S_BIN" ]]; then
      printf '%s\n' "$CLP_S_BIN"
      return 0
    fi
    echo "error: CLP_S_BIN is set but is not executable: $CLP_S_BIN" >&2
    return 1
  fi

  script_dir="${CLP_PLUGIN_BIN_DIR:-}"
  if [[ -z "$script_dir" ]]; then
    script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[1]}")" && pwd -P)"
  fi
  plugin_root="$(cd -- "${script_dir}/.." && pwd -P)"

  candidate="${script_dir}/clp-s"
  if [[ -x "$candidate" ]]; then
    printf '%s\n' "$candidate"
    return 0
  fi

  candidate="${plugin_root}/.clp-core/bin/clp-s"
  if [[ -x "$candidate" ]]; then
    printf '%s\n' "$candidate"
    return 0
  fi

  if command -v clp-s >/dev/null 2>&1; then
    command -v clp-s
    return 0
  fi

  echo "error: clp-s is not available. Run the plugin installer or set CLP_S_BIN." >&2
  return 127
}

canonicalize_output_path() {
  local path="$1"
  local parent base parent_real

  if realpath -m "$path" >/dev/null 2>&1; then
    realpath -m "$path"
    return 0
  fi

  parent="$(dirname -- "$path")"
  base="$(basename -- "$path")"
  parent_real="$(cd -- "$parent" && pwd -P)" || return 1
  if [[ "$parent_real" == "/" ]]; then
    printf '/%s\n' "$base"
  else
    printf '%s/%s\n' "$parent_real" "$base"
  fi
}

clp_config_dir() {
  if [[ -n "${CLP_S_CONFIG_DIR:-}" ]]; then
    printf '%s\n' "$CLP_S_CONFIG_DIR"
  elif [[ -n "${XDG_CONFIG_HOME:-}" ]]; then
    printf '%s\n' "${XDG_CONFIG_HOME}/yscope-clp-plugin"
  else
    printf '%s\n' "${HOME:-.}/.config/yscope-clp-plugin"
  fi
}

clp_archives_root_config_file() {
  if [[ -n "${CLP_S_ARCHIVES_ROOT_FILE:-}" ]]; then
    printf '%s\n' "$CLP_S_ARCHIVES_ROOT_FILE"
  else
    printf '%s/archives-root\n' "$(clp_config_dir)"
  fi
}

read_configured_archives_root() {
  local config_file line

  config_file="$(clp_archives_root_config_file)"
  [[ -f "$config_file" ]] || return 1

  IFS= read -r line < "$config_file" || return 1
  [[ -n "$line" ]] || return 1
  printf '%s\n' "$line"
}

write_configured_archives_root() {
  local archives_root="$1"
  local config_file

  config_file="$(clp_archives_root_config_file)"
  mkdir -p "$(dirname -- "$config_file")"
  printf '%s\n' "$archives_root" > "$config_file"
}

directory_file_bytes() {
  local path="$1"
  local total=0
  local size file

  while IFS= read -r -d '' file; do
    size="$(wc -c < "$file" | tr -d '[:space:]')"
    total=$((total + size))
  done < <(find "$path" -type f -print0)

  printf '%s\n' "$total"
}

file_size_bytes() {
  local path="$1"
  if stat -c %s "$path" >/dev/null 2>&1; then
    stat -c %s "$path"
    return 0
  fi
  stat -f %z "$path"
}

file_mtime_epoch() {
  local path="$1"
  if stat -c %Y "$path" >/dev/null 2>&1; then
    stat -c %Y "$path"
    return 0
  fi
  stat -f %m "$path"
}

file_type_letter() {
  local path="$1"
  if [[ -L "$path" ]]; then
    printf 'l\n'
  elif [[ -d "$path" ]]; then
    printf 'd\n'
  elif [[ -f "$path" ]]; then
    printf 'f\n'
  else
    printf '?\n'
  fi
}

sha256_file() {
  local path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$path" | awk '{print $1}'
    return 0
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$path" | awk '{print $1}'
    return 0
  fi
  echo "error: sha256sum or shasum is required" >&2
  return 127
}

find_immediate_children() {
  local dir="$1"
  find "$dir"/* "$dir"/.[!.]* "$dir"/..?* -prune -print0 2>/dev/null
}

clp_archive_metadata_file() {
  local archives_dir="$1"
  printf '%s/.yscope-clp-archive.json\n' "$archives_dir"
}

looks_like_clp_s_archive_dir() {
  local path="$1"

  [[ -d "$path" ]] || return 1
  [[ -f "$path/header" && -f "$path/table_metadata" ]]
}

find_archive_metadata_file() {
  local archives_dir="$1"
  local metadata_file

  metadata_file="$(clp_archive_metadata_file "$archives_dir")"
  if [[ -f "$metadata_file" ]]; then
    printf '%s\n' "$metadata_file"
    return 0
  fi

  metadata_file="$(clp_archive_metadata_file "$(dirname -- "$archives_dir")")"
  if [[ -f "$metadata_file" ]]; then
    printf '%s\n' "$metadata_file"
    return 0
  fi

  return 1
}

resolve_clp_s_archive_dir() {
  local archives_dir="$1"
  local child resolved=""
  local count=0

  if looks_like_clp_s_archive_dir "$archives_dir"; then
    printf '%s\n' "$archives_dir"
    return 0
  fi

  [[ -d "$archives_dir" ]] || return 1

  while IFS= read -r -d '' child; do
    if looks_like_clp_s_archive_dir "$child"; then
      resolved="$child"
      count=$((count + 1))
    fi
  done < <(find_immediate_children "$archives_dir")

  if [[ "$count" -eq 1 ]]; then
    printf '%s\n' "$resolved"
    return 0
  fi

  return 1
}

print_archive_metadata_summary() {
  local archives_dir="$1"
  local metadata_file

  metadata_file="$(find_archive_metadata_file "$archives_dir")" || return 0
  [[ -f "$metadata_file" ]] || return 0

  echo "Archive metadata: $metadata_file"
  if command -v jq >/dev/null 2>&1; then
    jq -r '
      "Archive source agent: " + (.agent // "unknown"),
      "Archive source session: " + (.session.file // "unknown"),
      "Archive source root: " + (.sourceRoot // "unknown")
    ' "$metadata_file" 2>/dev/null || true
  fi
}

is_broad_output_dir() {
  local output_dir="$1"
  local output_real home_real codex_home_real codex_sessions_root_real claude_projects_root_real

  output_real="$(canonicalize_output_path "$output_dir")" || return 1
  case "$output_real" in
    /|/home|/Users|/home/*/.claude|/home/*/.claude/projects|/home/*/.codex|/home/*/.codex/sessions|/Users/*/.claude|/Users/*/.claude/projects|/Users/*/.codex|/Users/*/.codex/sessions)
      return 0
      ;;
  esac

  if [[ -n "${HOME:-}" ]]; then
    home_real="$(canonicalize_output_path "$HOME")" || home_real=""
    case "$output_real" in
      "$home_real"|"$home_real/.claude"|"$home_real/.claude/projects"|"$home_real/.codex"|"$home_real/.codex/sessions")
        return 0
        ;;
    esac
  fi

  if [[ -n "${CODEX_HOME:-}" ]]; then
    codex_home_real="$(canonicalize_output_path "$CODEX_HOME")" || codex_home_real=""
    case "$output_real" in
      "$codex_home_real"|"$codex_home_real/sessions")
        return 0
        ;;
    esac
  fi

  if [[ -n "${CLP_S_CODEX_SESSIONS_ROOT:-}" ]]; then
    codex_sessions_root_real="$(canonicalize_output_path "$CLP_S_CODEX_SESSIONS_ROOT")" || codex_sessions_root_real=""
    if [[ "$output_real" == "$codex_sessions_root_real" ]]; then
      return 0
    fi
  fi

  if [[ -n "${CLP_S_CLAUDE_PROJECTS_ROOT:-}" ]]; then
    claude_projects_root_real="$(canonicalize_output_path "$CLP_S_CLAUDE_PROJECTS_ROOT")" || claude_projects_root_real=""
    if [[ "$output_real" == "$claude_projects_root_real" ]]; then
      return 0
    fi
  fi

  return 1
}

require_secure_url() {
  local url="$1"
  case "$url" in
    https://*) return 0 ;;
    http://localhost|http://localhost/*|http://localhost:*|http://localhost:*/*) return 0 ;;
    http://127.0.0.1|http://127.0.0.1/*|http://127.0.0.1:*|http://127.0.0.1:*/*) return 0 ;;
    http://\[::1\]|http://\[::1\]/*|http://\[::1\]:*|http://\[::1\]:*/*) return 0 ;;
    http://*.yscope.ai|http://*.yscope.ai/*) return 0 ;;
  esac
  echo "error: refusing non-HTTPS semantic endpoint URL: $url" >&2
  echo "  Use HTTPS, a localhost endpoint, or a *.yscope.ai endpoint." >&2
  return 1
}

semantic_health_url() {
  local endpoint="$1"
  local proto host
  proto="${endpoint%%://*}"
  host="${endpoint#*://}"
  host="${host%%/*}"
  host="${host%%\?*}"
  printf '%s://%s/health\n' "$proto" "$host"
}

check_semantic_endpoint() {
  local endpoint="$1"
  local health_url http_code
  health_url="$(semantic_health_url "$endpoint")" || return 1

  if ! command -v curl >/dev/null 2>&1; then
    echo "error: curl is required for semantic search health checks" >&2
    return 1
  fi

  http_code="$(curl -fsSL \
    --connect-timeout 5 \
    --max-time 10 \
    -o /dev/null \
    -w '%{http_code}' \
    "$health_url" 2>/dev/null)" || {
    echo "error: semantic endpoint health check failed: $health_url" >&2
    echo "  The embedding service may be down or unreachable." >&2
    return 1
  }

  if [[ "$http_code" != "200" ]]; then
    echo "error: semantic endpoint health check returned HTTP $http_code: $health_url" >&2
    return 1
  fi
}
