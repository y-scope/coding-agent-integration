#!/usr/bin/env python3
"""logtype-cluster.py — merge semantically similar logtypes before classification.

Invoked via the `logtype-cluster` bash launcher, which prefers the plugin venv
(created by `logtype-cluster setup`) and pins HF_HOME. Two subcommands:

  cluster  Embed the input logtypes with a model2vec static model and group
           them by greedy leader clustering at a cosine threshold. The LLM then
           classifies only the cluster representatives, by cluster id.
  expand   Propagate the LLM's per-cluster category assignments to every member
           logtype verbatim (stdlib-only; never re-generates logtype strings,
           so cache GROWTH matching stays byte-exact by construction).

Exit codes: 0 ok, 1 input problem, 2 missing dependency or validation failure,
3 usage error.
"""

import argparse
import json
import os
import signal
import sys

DEFAULT_MODEL = os.environ.get("CLP_LOG_CLUSTER_MODEL", "minishlab/potion-base-8M")
DEFAULT_THRESHOLD = os.environ.get("CLP_LOG_CLUSTER_THRESHOLD", "0.80")
SETUP_CMD = os.environ.get("LOGTYPE_CLUSTER_SETUP_CMD", "logtype-cluster setup")


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


def cmd_cluster(args):
    try:
        threshold = float(args.threshold)
    except ValueError:
        fail(3, f"--threshold must be a number, got: {args.threshold}")
    if not 0.0 < threshold <= 1.0:
        fail(3, f"--threshold must be in (0, 1], got: {threshold}")

    templates = load_logtypes(args.input)

    try:
        import numpy as np
        from model2vec import StaticModel
    except ImportError as exc:
        fail(2, f"missing dependency ({exc.name or exc})",
             f"run once to install it:  {SETUP_CMD}")

    try:
        model = StaticModel.from_pretrained(args.model)
    except Exception as exc:  # network/cache errors surface as various types
        fail(2, f"could not load embedding model {args.model!r}: {exc}",
             f"first use needs network access; run:  {SETUP_CMD}")

    embeddings = np.asarray(model.encode(templates), dtype=np.float64)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embeddings = embeddings / norms

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
        "model": args.model,
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
    print(f"MODEL={args.model}")
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
    p_cluster.add_argument("--model", default=DEFAULT_MODEL,
                           help=f"model2vec model (default: {DEFAULT_MODEL})")
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
