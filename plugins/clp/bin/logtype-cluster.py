#!/usr/bin/env python3
"""logtype-cluster.py — merge semantically similar logtypes before classification.

Invoked via the `logtype-cluster` bash launcher, which resolves the embedding
server URL and exports it as CLP_SEMANTIC_ENDPOINT. Two subcommands:

  cluster  Embed the input logtypes via the semantic server's /v1/embeddings
           endpoint and group them by greedy leader clustering at a cosine
           threshold. The LLM then classifies only the cluster
           representatives, by cluster id.
  expand   Propagate the LLM's per-cluster category assignments to every member
           logtype verbatim (stdlib-only; never re-generates logtype strings,
           so cache GROWTH matching stays byte-exact by construction).

Embeddings come from an already-running server; this tool never starts one and
never downloads a model. Point it at a server with --semantic-endpoint,
CLP_SEMANTIC_ENDPOINT, or the semantic-endpoint config file.

Exit codes: 0 ok, 1 input problem, 2 missing dependency / unreachable or
rejected embedding server, 3 usage error.
"""

import argparse
import json
import os
import signal
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_THRESHOLD = os.environ.get("CLP_LOG_CLUSTER_THRESHOLD", "0.80")

# Model contract advertised to the semantic server. It exact-matches these
# against its configured profile, so they must stay byte-identical to what
# clp-s sends (see SemanticSearchClient.cpp): BGE-base int8[768].
MODEL_NAME = os.environ.get("CLP_SEMANTIC_MODEL", "BAAI/bge-base-en-v1.5")
EMBEDDING_DIM = int(os.environ.get("CLP_SEMANTIC_EMBEDDING_DIM", "768"))
EMBEDDING_DTYPE = "int8"
EMBEDDINGS_PATH = "/v1/embeddings"
SIMILARITY_PATH = "/v1/similarity"
CONTENT_TYPE = "application/vnd.clp.embeddings.text.utf8.length-table.v1"
# Cloudflare fronts the hosted endpoints and rejects urllib's default
# User-Agent with HTTP 403 (error 1010), so send an explicit one.
USER_AGENT = "clp-logtype-cluster/1"
# Texts per request. 400 templates embed in ~4 s; keeps bodies well under any
# proxy limit while holding the round-trip count low. A malformed or
# non-positive override falls back to the default rather than crashing later.
def _positive_env_int(name, default):
    try:
        value = int(os.environ[name])
    except (KeyError, ValueError):
        return default
    return value if value > 0 else default


DEFAULT_BATCH = _positive_env_int("CLP_SEMANTIC_EMBED_BATCH", 256)
REQUEST_TIMEOUT_S = _positive_env_int("CLP_SEMANTIC_TIMEOUT_S", 120)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        sys.exit(3)


def fail(code, *lines):
    for line in lines:
        print(f"error: {line}", file=sys.stderr)
    sys.exit(code)


def load_logtypes(path):
    """Read {"logtype": ...} NDJSON; return sorted unique logtype strings."""
    logtypes = set()
    skipped = 0
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1
                    continue
                value = obj.get("logtype") if isinstance(obj, dict) else None
                if isinstance(value, str) and value:
                    logtypes.add(value)
                else:
                    skipped += 1
    except OSError as exc:
        fail(1, f"cannot read --input file: {exc}")
    if skipped:
        print(f"warning: skipped {skipped} line(s) without a usable \"logtype\" field",
              file=sys.stderr)
    if not logtypes:
        fail(1, f"no logtypes found in {path}")
    return sorted(logtypes)


def require_secure_url(url):
    """Mirrors require_secure_url in lib/clp-common.sh.

    The launcher normally validates before exec'ing us, but this module is also
    runnable directly, so re-check rather than trusting the caller: templates
    must not be POSTed in the clear to an arbitrary host.
    """
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme == "https":
        return
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "http" and (host in ("localhost", "127.0.0.1", "::1")
                                    or host.endswith(".yscope.ai")):
        return
    fail(2, f"refusing non-HTTPS semantic endpoint URL: {url}",
         "use HTTPS, a localhost endpoint, or a *.yscope.ai endpoint")


def resolve_endpoint(inline):
    """Endpoint precedence: inline flag, env, then the config file.

    The bash launcher normally resolves this (sharing the health check and the
    built-in remote defaults with the search wrapper) and passes it down via
    CLP_SEMANTIC_ENDPOINT; this is the fallback for direct python invocation.
    """
    for value in (inline, os.environ.get("CLP_SEMANTIC_ENDPOINT"),
                  os.environ.get("CLAUDE_PLUGIN_OPTION_SEMANTIC_ENDPOINT")):
        if value:
            require_secure_url(value)
            return value.rstrip("/")

    config_file = os.environ.get("CLP_SEMANTIC_ENDPOINT_FILE")
    if not config_file:
        config_dir = (os.environ.get("CLP_S_CONFIG_DIR")
                      or os.path.join(os.environ.get("XDG_CONFIG_HOME")
                                      or os.path.expanduser("~/.config"),
                                      "yscope-clp-plugin"))
        config_file = os.path.join(config_dir, "semantic-endpoint")
    try:
        with open(config_file, encoding="utf-8") as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                if line:
                    require_secure_url(line)
                    return line.rstrip("/")
    except OSError:
        pass

    fail(2, "no embedding server configured",
         "this tool never starts one — point it at a running server with",
         "  --semantic-endpoint URL, CLP_SEMANTIC_ENDPOINT=URL, or",
         f"  echo URL > {config_file}")


def embeddings_url(endpoint):
    """Normalizes a base / similarity / embeddings URL to /v1/embeddings."""
    endpoint = endpoint.rstrip("/")
    for path in (EMBEDDINGS_PATH, SIMILARITY_PATH):
        if endpoint.endswith(path):
            return endpoint[: -len(path)] + EMBEDDINGS_PATH
    return endpoint + EMBEDDINGS_PATH


def embed_batch(url, texts):
    """POSTs one `text.utf8.length-table.v1` batch; returns a list of vectors.

    Request body: row_lens:u32[N] (little-endian UTF-8 byte lengths) followed
    by the N payloads. Response is `embeddings.indexed.v1` — N records of
    (index:u32le, values:int8[dim]) followed by a msgpack stats trailer, which
    is ignored. Records carry their own index, so they are placed by index
    rather than by arrival order.
    """
    payloads = [t.encode("utf-8") for t in texts]
    body = b"".join(struct.pack("<I", len(p)) for p in payloads) + b"".join(payloads)
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": CONTENT_TYPE,
            "Content-Encoding": "identity",
            "Accept-Encoding": "identity",
            "User-Agent": USER_AGENT,
            "X-CLP-Model": MODEL_NAME,
            "X-CLP-Embedding-Dtype": EMBEDDING_DTYPE,
            "X-CLP-Embedding-Dim": str(EMBEDDING_DIM),
            "X-CLP-Item-Count": str(len(texts)),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:400].decode("utf-8", "replace")
        fail(2, f"embedding request failed: HTTP {exc.code} {detail}",
             f"endpoint: {url}")
    except (urllib.error.URLError, OSError) as exc:
        fail(2, f"cannot reach the embedding server: {exc}", f"endpoint: {url}")

    record_size = 4 + EMBEDDING_DIM
    if len(data) < record_size * len(texts):
        fail(2, f"embedding response too short: got {len(data)} bytes, "
                f"expected at least {record_size * len(texts)} "
                f"({len(texts)} x {record_size})")

    vectors = [None] * len(texts)
    for n in range(len(texts)):
        offset = n * record_size
        index = struct.unpack_from("<I", data, offset)[0]
        if index >= len(texts):
            fail(2, f"embedding response index {index} out of range "
                    f"for {len(texts)} inputs")
        vectors[index] = struct.unpack_from(
            f"<{EMBEDDING_DIM}b", data, offset + 4)
    if any(v is None for v in vectors):
        fail(2, "embedding response did not cover every input index")
    return vectors


def embed_texts(np, url, texts, batch_size):
    """Embeds all texts in batches; returns an L2-normalized float64 matrix."""
    vectors = []
    for start in range(0, len(texts), batch_size):
        chunk = texts[start:start + batch_size]
        vectors.extend(embed_batch(url, chunk))
        if len(texts) > batch_size:
            print(f"embedded {min(start + batch_size, len(texts))}/{len(texts)}",
                  file=sys.stderr)
    embeddings = np.asarray(vectors, dtype=np.float64)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return embeddings / norms


def cmd_cluster(args):
    try:
        threshold = float(args.threshold)
    except ValueError:
        fail(3, f"--threshold must be a number, got: {args.threshold}")
    if not 0.0 < threshold <= 1.0:
        fail(3, f"--threshold must be in (0, 1], got: {threshold}")
    if args.batch_size < 1:
        fail(3, f"--batch-size must be a positive integer, got: {args.batch_size}")

    templates = load_logtypes(args.input)

    try:
        import numpy as np
    except ImportError:
        fail(2, "missing dependency (numpy)",
             "install it with:  python3 -m pip install numpy")

    url = embeddings_url(resolve_endpoint(args.semantic_endpoint))
    embeddings = embed_texts(np, url, templates, args.batch_size)

    # Greedy leader clustering against running centroids. Input order is the
    # sorted template list, so cluster contents are deterministic.
    sums, counts, members = [], [], []
    for i, vec in enumerate(embeddings):
        if sums:
            centroids = np.stack(sums)
            centroid_norms = np.linalg.norm(centroids, axis=1)
            centroid_norms[centroid_norms == 0] = 1.0
            sims = (centroids @ vec) / centroid_norms
            best = int(np.argmax(sims))
            if sims[best] >= threshold:
                sums[best] = sums[best] + vec
                counts[best] += 1
                members[best].append(i)
                continue
        sums.append(vec.copy())
        counts.append(1)
        members.append([i])

    clusters = []
    for total, idxs in zip(sums, members):
        centroid = total / np.linalg.norm(total) if np.linalg.norm(total) else total
        sims = embeddings[idxs] @ centroid
        rep = idxs[int(np.argmax(sims))]
        clusters.append({
            "representative": templates[rep],
            "members": [templates[i] for i in idxs],
            "count": len(idxs),
        })
    clusters.sort(key=lambda c: (-c["count"], c["representative"]))
    for n, cluster in enumerate(clusters, 1):
        cluster["id"] = f"c{n}"

    result = {
        "model": MODEL_NAME,
        "endpoint": url,
        "threshold": threshold,
        "template_count": len(templates),
        "clusters": [
            {"id": c["id"], "representative": c["representative"],
             "members": c["members"], "count": c["count"]}
            for c in clusters
        ],
    }
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
        fh.write("\n")

    print(f"CLUSTERS={len(clusters)}")
    print(f"TEMPLATES={len(templates)}")
    print(f"MODEL={MODEL_NAME}")
    print(f"ENDPOINT={url}")
    print(f"THRESHOLD={threshold}")
    print(f"OUTPUT={args.output}")
    # Paste-ready NDJSON for the classification prompt: id + member count +
    # representative only — the LLM never sees (or echoes) member logtypes.
    for c in clusters:
        print(json.dumps({"id": c["id"], "count": c["count"],
                          "representative": c["representative"]},
                         ensure_ascii=False))


def load_json_file(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except OSError as exc:
        fail(1, f"cannot read {what} file: {exc}")
    except json.JSONDecodeError as exc:
        fail(2, f"{what} file is not valid JSON: {exc}")


def cmd_expand(args):
    clusters_doc = load_json_file(args.clusters, "--clusters")
    classification = load_json_file(args.classification, "--classification")

    clusters = clusters_doc.get("clusters") if isinstance(clusters_doc, dict) else None
    if not isinstance(clusters, list) or not clusters:
        fail(2, f"--clusters file has no \"clusters\" list: {args.clusters}")
    if not isinstance(classification, dict):
        fail(2, "--classification file must be a JSON object")

    assignments = classification.get("assignments")
    if not isinstance(assignments, list):
        fail(2, "classification is missing the \"assignments\" list "
                "(expected [{\"id\": \"c1\", \"category\": \"...\"}, ...])")

    cluster_ids = [c.get("id") for c in clusters]
    known = set(cluster_ids)
    categories = {}
    problems = []
    for n, entry in enumerate(assignments, 1):
        if not isinstance(entry, dict):
            problems.append(f"assignment #{n} is not an object")
            continue
        cid, category = entry.get("id"), entry.get("category")
        if not isinstance(cid, str) or not cid:
            problems.append(f"assignment #{n} has no \"id\"")
        elif cid not in known:
            problems.append(f"assignment #{n}: unknown cluster id {cid!r}")
        elif cid in categories:
            problems.append(f"cluster id {cid!r} assigned more than once")
        elif not isinstance(category, str) or not category.strip():
            problems.append(f"assignment #{n} ({cid}) has an empty \"category\"")
        else:
            categories[cid] = category.strip()
    missing = [cid for cid in cluster_ids if cid not in categories]
    if missing:
        problems.append(f"{len(missing)} cluster id(s) not assigned: "
                        + ", ".join(missing))
    if problems:
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        print("error: fix the classification JSON and re-run expand — nothing "
              "was written, do NOT store this in the cache", file=sys.stderr)
        sys.exit(2)

    for key, kind in (("taxonomy", list), ("query_plan", list), ("schema", dict)):
        if key in classification and not isinstance(classification[key], kind):
            fail(2, f"classification \"{key}\" must be a {kind.__name__}")
    if "templates" in classification:
        print("warning: classification contains \"templates\"; ignoring it — "
              "templates are rebuilt from the cluster members", file=sys.stderr)

    templates = []
    for cluster in clusters:
        cat = categories[cluster["id"]]
        for member in cluster.get("members", []):
            templates.append({"logtype": member, "category": cat})

    expanded = {}
    if "schema" in classification:
        expanded["schema"] = classification["schema"]
    expanded["taxonomy"] = classification.get("taxonomy", [])
    expanded["templates"] = templates
    expanded["query_plan"] = classification.get("query_plan", [])

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(expanded, fh, ensure_ascii=False, indent=1)
        fh.write("\n")

    print(f"TEMPLATES={len(templates)}")
    print(f"CATEGORIES={len(set(categories.values()))}")
    print(f"OUTPUT={args.output}")


def main():
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    parser = Parser(prog="logtype-cluster", description=__doc__,
                    formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_cluster = sub.add_parser("cluster",
                               help="group similar logtypes; print representatives")
    p_cluster.add_argument("--input", required=True,
                           help='{"logtype": ...} NDJSON (e.g. /tmp/logtypes-to-classify.ndjson)')
    p_cluster.add_argument("--semantic-endpoint", default=None,
                           help="embedding server URL (default: $CLP_SEMANTIC_ENDPOINT, "
                                "then the semantic-endpoint config file)")
    p_cluster.add_argument("--batch-size", type=int, default=DEFAULT_BATCH,
                           help=f"texts per embedding request (default: {DEFAULT_BATCH})")
    p_cluster.add_argument("--threshold", default=DEFAULT_THRESHOLD,
                           help=f"cosine similarity threshold in (0,1] (default: {DEFAULT_THRESHOLD})")
    p_cluster.add_argument("--output", default="/tmp/logtype-clusters.json",
                           help="clusters JSON path (default: /tmp/logtype-clusters.json)")
    p_cluster.set_defaults(func=cmd_cluster)

    p_expand = sub.add_parser("expand",
                              help="propagate per-cluster categories to all members")
    p_expand.add_argument("--clusters", required=True,
                          help="clusters JSON produced by `cluster`")
    p_expand.add_argument("--classification", required=True,
                          help='LLM output JSON with "assignments" (per cluster id)')
    p_expand.add_argument("--output", default="/tmp/logtype-expanded.json",
                          help="expanded classification path (default: /tmp/logtype-expanded.json)")
    p_expand.set_defaults(func=cmd_expand)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
