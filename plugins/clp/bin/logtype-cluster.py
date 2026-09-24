#!/usr/bin/env python3
"""logtype-cluster.py — merge semantically similar logtypes before classification.

Invoked via the `logtype-cluster` bash launcher, which resolves the embedding
server URL and exports it as CLP_SEMANTIC_ENDPOINT. Two subcommands:

  cluster  Truncate each logtype to a character limit (default 500, UTF-8
           characters), de-duplicate the results, embed the distinct texts
           via the semantic server's /v1/embeddings endpoint, and group them by
           greedy leader clustering at a cosine threshold. The LLM then
           classifies only the cluster representatives, by cluster id.
  expand   Propagate the LLM's per-cluster category assignments to every member
           logtype verbatim (stdlib-only; never re-generates logtype strings,
           so cache GROWTH matching stays byte-exact by construction).

Truncation and de-duplication concern only what is POSTed for embedding. `members`
and `representative` are always FULL logtype strings, so `expand` and the
classification cache keep full templates; the character limit is also the cache
fingerprint (see logtype-cache, which duplicates truncate_chars).

Embeddings come from an already-running server; this tool never starts one and
never downloads a model. Point it at a server with --semantic-endpoint,
CLP_SEMANTIC_ENDPOINT, or the semantic-endpoint config file. Clustering itself
is pure standard library — there is no third-party dependency (numpy is not
needed).

Exit codes: 0 ok, 1 input problem, 2 unreachable or rejected embedding server,
3 usage error.
"""

import argparse
import json
import math
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

# Templates are truncated to this many UTF-8 characters before embedding, then
# de-duplicated, so a long template cannot bloat a request and templates sharing
# a prefix are only ever embedded once. Configurable per session. The SAME limit
# is the classification-cache fingerprint (logtype-cache duplicates
# truncate_chars and reads the same env var), so changing it deliberately
# re-keys the cache. 500, not 512: the embedding model's context is 512 TOKENS
# including its special tokens, and punctuation-heavy templates (`,<*>,<*>,...`)
# tokenize to about one token per character, so a 512-character cap can overflow
# it and the server rejects the whole batch. 500 leaves headroom.
DEFAULT_MAX_CHARS = _positive_env_int("CLP_LOGTYPE_MAX_CHARS", 500)
# Hard ceiling on the encoded size of one /v1/embeddings request body, applied
# independently of the batch count. Decimal MB so it is under 100 MB however the
# limit is read.
DEFAULT_MAX_REQUEST_BYTES = _positive_env_int("CLP_LOGTYPE_MAX_REQUEST_BYTES", 100_000_000)


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


def truncate_chars(text, max_chars):
    """Return `text` capped at max_chars UTF-8 characters (code points).

    Python str slicing is by code point, so this cannot split a character and
    the result is always valid UTF-8. Mirrored in logtype-cache, which must agree
    exactly or the cache fingerprint diverges.
    """
    return text if len(text) <= max_chars else text[:max_chars]


def dedup_truncated(templates, max_chars):
    """Map full templates onto the distinct texts to embed.

    Returns (unique_texts, index_of): `unique_texts` is the sorted distinct
    truncate_chars(t, max_chars) values, each embedded exactly once, and
    `index_of[i]` is the position of templates[i]'s truncated form in it. That
    lets the caller realign the embeddings back onto the full-template list, so
    clustering membership, representatives and `expand` all stay full-template
    indexed. Templates sharing a prefix share one embedding, exactly.
    """
    truncated = [truncate_chars(t, max_chars) for t in templates]
    unique_texts = sorted(set(truncated))
    position = {text: i for i, text in enumerate(unique_texts)}
    return unique_texts, [position[text] for text in truncated]


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


def chunk_texts(texts, batch_size, max_request_bytes):
    """Yield (start_index, chunk) respecting both the count and byte caps.

    Body size matches what embed_batch builds: a u32 length prefix per text plus
    its UTF-8 payload. A text that cannot fit the cap on its own is a usage
    error — truncation normally makes that unreachable.
    """
    start = 0
    total = len(texts)
    while start < total:
        size = 0
        end = start
        while end < total and (end - start) < batch_size:
            text_size = 4 + len(texts[end].encode("utf-8"))
            if size + text_size > max_request_bytes:
                break
            size += text_size
            end += 1
        if end == start:
            single = 4 + len(texts[start].encode("utf-8"))
            fail(3, f"a single truncated text ({single} bytes) exceeds "
                    f"--max-request-bytes ({max_request_bytes})",
                 "lower --max-chars, or raise --max-request-bytes")
        yield start, texts[start:end]
        start = end


if hasattr(math, "sumprod"):
    # C-implemented dot product (Python 3.12+).
    def dot(a, b):
        return math.sumprod(a, b)
else:
    from operator import mul

    def dot(a, b):
        return sum(map(mul, a, b))


def l2_normalize(vector):
    """Scale `vector` to unit length; a zero vector is returned unchanged."""
    norm = math.sqrt(dot(vector, vector))
    return vector if norm == 0.0 else [v / norm for v in vector]


def embed_texts(url, texts, batch_size, max_request_bytes):
    """Embeds all texts in batches; returns L2-normalized float vectors.

    Pure stdlib: the clustering below only needs dot products and norms, so
    there is no numpy (or any other third-party) dependency.
    """
    vectors = []
    total = len(texts)
    for start, chunk in chunk_texts(texts, batch_size, max_request_bytes):
        vectors.extend(embed_batch(url, chunk))
        done = start + len(chunk)
        if done < total:
            print(f"embedded {done}/{total}", file=sys.stderr)
    return [l2_normalize([float(x) for x in vector]) for vector in vectors]


def cmd_cluster(args):
    try:
        threshold = float(args.threshold)
    except ValueError:
        fail(3, f"--threshold must be a number, got: {args.threshold}")
    if not 0.0 < threshold <= 1.0:
        fail(3, f"--threshold must be in (0, 1], got: {threshold}")
    if args.batch_size < 1:
        fail(3, f"--batch-size must be a positive integer, got: {args.batch_size}")
    if args.max_chars < 1:
        fail(3, f"--max-chars must be a positive integer, got: {args.max_chars}")
    if args.max_request_bytes < 1:
        fail(3, f"--max-request-bytes must be a positive integer, got: "
                f"{args.max_request_bytes}")

    templates = load_logtypes(args.input)

    url = embeddings_url(resolve_endpoint(args.semantic_endpoint))
    # Embed each distinct truncated text once, then realign onto the full
    # template list, so everything below stays full-template indexed.
    unique_texts, index_of = dedup_truncated(templates, args.max_chars)
    unique_embeddings = embed_texts(url, unique_texts, args.batch_size,
                                    args.max_request_bytes)
    embeddings = [unique_embeddings[k] for k in index_of]

    # Greedy leader clustering against running centroids. Vectors are already
    # unit-length, so a centroid's norm is all that is needed to turn a raw dot
    # product into a cosine. Input order is the sorted template list, so cluster
    # contents are deterministic. Strictly-greater comparison keeps the first
    # maximum, matching the previous argmax tie-break.
    sums, members = [], []
    for i, vec in enumerate(embeddings):
        best = -1
        best_sim = -1.0
        for c, total in enumerate(sums):
            norm = math.sqrt(dot(total, total))
            sim = dot(total, vec) / norm if norm else 0.0
            if sim > best_sim:
                best_sim, best = sim, c
        if best >= 0 and best_sim >= threshold:
            sums[best] = [a + b for a, b in zip(sums[best], vec)]
            members[best].append(i)
            continue
        sums.append(list(vec))
        members.append([i])

    clusters = []
    for total, idxs in zip(sums, members):
        norm = math.sqrt(dot(total, total))
        centroid = [v / norm for v in total] if norm else total
        rep = idxs[0]
        best_sim = -1.0
        for idx in idxs:
            sim = dot(embeddings[idx], centroid)
            if sim > best_sim:
                best_sim, rep = sim, idx
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
        "embedded_count": len(unique_texts),
        "max_chars": args.max_chars,
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
    print(f"EMBEDDED={len(unique_texts)}")
    print(f"MAX_CHARS={args.max_chars}")
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
    p_cluster.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS,
                           help=f"truncate each template to this many UTF-8 "
                                f"characters before embedding (default: "
                                f"{DEFAULT_MAX_CHARS}, or $CLP_LOGTYPE_MAX_CHARS)")
    p_cluster.add_argument("--max-request-bytes", type=int,
                           default=DEFAULT_MAX_REQUEST_BYTES,
                           help=f"ceiling on one embedding request body "
                                f"(default: {DEFAULT_MAX_REQUEST_BYTES}, or "
                                f"$CLP_LOGTYPE_MAX_REQUEST_BYTES)")
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
