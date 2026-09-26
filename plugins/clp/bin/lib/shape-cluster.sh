#!/usr/bin/env bash
set -euo pipefail

# clp shape-cluster — launcher for the log-insights pre-clustering tool.
#
# Resolves the semantic (embedding) server URL with the same precedence, scheme
# check, and health check as the search wrapper — inline flag,
# CLP_SEMANTIC_ENDPOINT, the semantic-endpoint config file, then the built-in
# remote endpoints — and exports it for shape_cluster.py, which embeds the
# log shapes via the server's /v1/embeddings endpoint.
#
# No venv, no model download, no server of our own, and no third-party
# dependency: embeddings always come from an already-running server, and the
# clustering is pure Python standard library (numpy is not required).

CLP_PLUGIN_LIB_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# The engine shim and the sibling helpers are resolved from bin/, one level up.
# Read by clp-common.sh's clp-s resolution rather than by this script itself.
# shellcheck disable=SC2034
CLP_PLUGIN_BIN_DIR="$(cd -- "${CLP_PLUGIN_LIB_DIR}/.." && pwd -P)"
# shellcheck disable=SC1091
source "${CLP_PLUGIN_LIB_DIR}/clp-common.sh"

if [[ "${1:-}" == "setup" ]]; then
  cat >&2 <<EOF
error: 'clp shape-cluster setup' has been removed.

Clustering no longer installs a venv or downloads an embedding model — it
embeds templates through a semantic server that is already running. Point it
at one (highest precedence first):
  clp shape-cluster cluster --semantic-endpoint URL ...
  CLP_SEMANTIC_ENDPOINT=URL
  echo URL > $(semantic_endpoint_config_file)
Otherwise the built-in remote endpoints are used automatically.

There is no local dependency: clustering is pure Python standard library.
EOF
  exit 2
fi

# Decide whether this invocation needs an embedding server at all, and pick up
# an inline --semantic-endpoint if one was given. Scanning argv positionally
# (rather than pattern-matching "$*") keeps an argument VALUE that happens to
# contain the flag text from being mistaken for the flag itself.
needs_endpoint=1
inline_endpoint=""
expect_endpoint_value=0
end_of_flags=0

case "${1:-}" in
  # `expand` only propagates categories to cluster members and `fields` only
  # summarizes files — both stdlib-only and fully offline. Help and the bare
  # usage path must stay offline and instant, so never health-check the network
  # just to print text.
  expand|fields|-h|--help|"") needs_endpoint=0 ;;
esac

for arg in "$@"; do
  if [[ "$expect_endpoint_value" -eq 1 ]]; then
    inline_endpoint="$arg"
    expect_endpoint_value=0
    continue
  fi
  if [[ "$end_of_flags" -eq 1 ]]; then
    continue
  fi
  case "$arg" in
    --) end_of_flags=1 ;;
    --semantic-endpoint) expect_endpoint_value=1 ;;
    --semantic-endpoint=*) inline_endpoint="${arg#*=}" ;;
    -h|--help) needs_endpoint=0 ;;
  esac
done

if [[ "$needs_endpoint" -eq 1 ]]; then
  # Always route through resolve_semantic_endpoint — including the inline
  # value — so the HTTPS/loopback scheme check and the health check apply
  # uniformly to every source. Map any resolution failure to exit 2, the
  # clusterer's documented "cannot embed" code (see shape_cluster.py).
  CLP_SEMANTIC_ENDPOINT="$(resolve_semantic_endpoint "$inline_endpoint")" || exit 2
  export CLP_SEMANTIC_ENDPOINT
fi

exec python3 "${CLP_PLUGIN_LIB_DIR}/shape_cluster.py" "$@"
