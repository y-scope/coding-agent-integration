# Logtype classification reference (logtype-insights step 6)

Read this when the bootstrap reported `CACHE_MODE=GROWTH` or `NEW` (or an
UPTODATE schema mismatch downgraded to NEW). It covers the cluster → classify →
expand → store pipeline and the full classification subagent prompt.

## Cache design in one paragraph

`app_key = sha256(sorted set of distinct logtype strings, each capped at a
character limit)` — a fingerprint of the application's *embedded* message
vocabulary. Use the bootstrap's `MAX_CHARS` (default 512); the same limit is
applied before embedding, so the key identifies exactly what was embedded. The
stored `templates[].logtype` are still the FULL, byte-exact strings. The entry
stores the discovered `schema`,
`taxonomy`, per-template `templates` classification, and `query_plan` (plus
`app_key`, `max_chars`, `classified_at`, `grown_from`). `logtype-cache diff` yields
**UPTODATE** (same fingerprint → reuse, no subagent; a full template that only
differs past the character limit is appended with the category of the truncated
form it shares, so the entry stays complete), **GROWTH** (a cached entry's
truncated set is a proper subset of the current one → classify only the new
templates, merge into the base via `put-merged`), or **NEW** (no compatible base
→ classify all, store fresh). Cache dir:
`~/.config/yscope-clp-plugin/logtype-cache/`
(override: `$CLP_LOGTYPE_CACHE_DIR` or `--cache-dir`).

GROWTH matching requires the stored `templates[].logtype` strings to be
**byte-exact** copies of the normalized NDJSON. The id-based pipeline below
guarantees this by construction: the LLM only ever returns cluster ids, and
`expand` re-attaches the member logtypes verbatim from the cluster file.

## Cluster contract (`logtype-cluster`)

```bash
# Group the to-classify templates (from the bootstrap) by semantic similarity.
# Each template is truncated to MAX_CHARS characters and de-duplicated first, so
# identical prefixes are embedded once; representatives and members stay FULL
# templates. Embeddings come from the semantic server — nothing is installed or
# started locally, and no model is downloaded:
"${CLAUDE_PLUGIN_ROOT}/bin/logtype-cluster" cluster \
  --max-chars "$MAX_CHARS" \
  --input /tmp/logtypes-to-classify.ndjson
```

`cluster` prints `CLUSTERS=`/`TEMPLATES=`/`EMBEDDED=`/`MAX_CHARS=` then one
`{"id","count","representative"}` line per cluster (ids `c1..cN`, largest
first; the representative is a real, full template closest to the cluster
centroid). `TEMPLATES` is the full count, `EMBEDDED` the distinct truncated
texts actually sent. Full memberships are written to
`/tmp/logtype-clusters.json`. Tunables:
`--threshold` / `$CLP_LOG_CLUSTER_THRESHOLD` (cosine, default 0.80 — raise to
0.85–0.90 if unrelated templates land in one cluster, lower to merge more),
`--max-chars` / `$CLP_LOGTYPE_MAX_CHARS` (character cap before embedding and
fingerprinting, default 512), `--semantic-endpoint` / `$CLP_SEMANTIC_ENDPOINT`
(the embedding server; falls back to the `semantic-endpoint` config file, then
the built-in remote endpoints), and `--batch-size` (texts per request, default
256; a separate byte ceiling bounds each request body).

**Raw-NDJSON last resort** (only if no embedding server is reachable): skip
clustering; paste `/tmp/logtypes-to-classify.ndjson` directly into the prompt,
replace the `assignments` output contract with
`"templates": [{"logtype":"<verbatim template>","category":"..."}]`, instruct
the subagent to copy each logtype **byte-exact** from the input, skip `expand`,
and piped straight into `put-merged --max-chars "$MAX_CHARS"`. Slower and fragile for
GROWTH — prefer fixing the endpoint.

## Classification subagent prompt template

Spawn ONE subagent (Agent tool), model **haiku**; if its output fails the
validation below, tell the user ("first classification attempt failed
validation — retrying with a stronger model") and retry once with `sonnet`.
Fill in `ARCHIVE`, the schema
fields, the severity/logger vocabularies (from the bootstrap DIST lines), and
paste the cluster lines from `logtype-cluster cluster`; for GROWTH also paste
the base taxonomy/plan labels:

```
You are classifying message-template clusters for a CLP archive, so a later
insight pass can run targeted queries. Do NOT write the final report — only the
classification JSON. Each cluster below groups semantically similar logtype
templates from the archive; its representative is a real template and count is
how many templates it covers. Assign a category to EVERY cluster id.

Archive: ARCHIVE
Discovered schema (field names in this archive):
  timestamp: <TS>
  severity:  <SEV>
  logger:    <LOGGER>
  message:   <MSG>          (the clp-string field the templates come from)
  payload:   <PAYLOAD>      (leaf paths if any, e.g. attr.durationMillis)
Severity values seen: <e.g. I,W,E,F or INFO,DEBUG,WARNING,ERROR>
Logger values seen:   <e.g. NETWORK,REPL,... or sflow.task.vllm_worker_3,...>

CLUSTERS TO CLASSIFY (one {"id","count","representative"} per line):
<PASTE the cluster lines printed by logtype-cluster cluster>

[Only for GROWTH] Existing categories from the previous classification — REUSE
these where a representative fits; add a new category only if none fits.
Existing query-plan labels (do not duplicate): <paste base taxonomy categories
and query_plan labels from /tmp/logtype-base-classification.json>

Method:
1. Assign EACH cluster id the best-fitting category, judged by its
   representative. Use this GENERIC default taxonomy, AND any APP-SPECIFIC
   categories already in use (GROWTH) or that the representatives suggest
   (NEW), e.g. Mongo: workload/operations, replication/election, sharding,
   indexing, storage; vLLM: worker-health, kv-cache, model-loading. Defaults:
     - errors / exceptions / failures
     - warnings
     - performance (latency / throughput / timing / "took <*> ms")
     - config / startup / initialization
     - network / connectivity / timeout
     - resource (memory / disk / file-descriptors / storage pressure)
     - lifecycle / state-transitions (start/stop/election/stepdown/restart)
     - security / auth / access
     - other (note but don't deep-search)
2. Build a QUERY PLAN: targeted queries derived from the representatives,
   expressed in the discovered field names. Per entry: label, the KQL filter
   (using the searchable scalar fields — severity/logger/payload leaves; NOT
   message:term, which is a clp-string and returns 0), the columns to
   --projection, and the method:
     - "count"        -> count matches via `... | grep -c '^{'`
     - "project+grep" -> project the message field, grep its static text
     - "project+jq"   -> project message/payload, jq-filter (e.g. a numeric
                         threshold on a payload leaf)
     - "semantic"     -> semantic("...") AND <scalar filter>, ONLY for an
                         ambiguous template or to group similar ones
   Example (Mongo): {"label":"Slow queries","kql":"attr.durationMillis:*",
     "project":"t.$date,attr.durationMillis,msg",
     "jq":"select((.attr.durationMillis//0)>100)","method":"project+jq"}
   Example (vLLM):  {"label":"Memory warnings","kql":"level:WARNING",
     "project":"timestamp,level,message","grep":"memory|OOM|KV",
     "method":"project+grep"}
   For GROWTH, add new plan entries only for genuinely new signals.
3. Remember: the message field is a clp-string. KQL `message:term` /
   `message:*term*` return 0. Only the scalar fields (severity, logger,
   payload leaves) are KQL-searchable; message content is retrieved by
   projecting the message field and grepping.

Write the result as valid JSON to /tmp/logtype-class.json with EXACTLY this
shape, then print "DONE" and nothing else:
  {
    "schema": {"timestamp":"<TS>","severity":"<SEV>","logger":"<LOGGER>","message":"<MSG>","payload":["<leaf>",...]},
    "taxonomy": [{"category":"<name>","description":"<one line>"}],
    "assignments": [{"id":"c1","category":"<name>"}],
    "query_plan": [{"label":"<...>","kql":"<...>","project":"<...>","grep":"<...>","jq":"<...>","method":"<count|project+grep|project+jq|semantic>"}]
  }
Rules:
- `assignments` must contain EVERY cluster id above exactly once, with ONLY
  ids — never logtype text; the member templates are re-attached mechanically.
- Omit "schema" for GROWTH (the base entry already has it); include it for NEW.
- Use only the keys each query_plan entry needs (omit null/empty keys).
- Use the discovered field names verbatim in `kql` and `project`.
```

## After the subagent returns: validate → expand → store

Tell the user the classifier returned and you are validating and storing the
plan; after `put-merged` succeeds, report the taxonomy and that the
classification is now cached.

```bash
# 1. Shape-validate — the fields must be ARRAYS (a bare `.assignments` test
#    passes for a scalar, which would poison the cache entry):
jq -e '(.taxonomy|type=="array") and (.assignments|type=="array") and (.query_plan|type=="array")' \
  /tmp/logtype-class.json >/dev/null || exit 1

# 2. Expand id-based assignments to every member template. Exits 2 and writes
#    NOTHING on missing/unknown/duplicate ids — in that case do NOT store;
#    announce the retry to the user, re-run the subagent (sonnet fallback),
#    and expand again:
"${CLAUDE_PLUGIN_ROOT}/bin/logtype-cluster" expand \
  --clusters /tmp/logtype-clusters.json \
  --classification /tmp/logtype-class.json \
  --output /tmp/logtype-expanded.json

# 3. Store. Use MODE/APP_KEY/BASE_KEY/MAX_CHARS from the bootstrap output
#    (re-declare — fresh shell). --max-chars must match the bootstrap's, or the
#    stored fingerprint won't match the next run. GROWTH merges into the base
#    entry (templates/taxonomy/query_plan unioned, grown_from recorded); NEW
#    stores fresh:
CACHE="${CLAUDE_PLUGIN_ROOT}/bin/logtype-cache"
if [[ "$MODE" == "GROWTH" ]]; then
  # Guard: an empty BASE_KEY would silently store ONLY the new templates.
  [[ -n "$BASE_KEY" ]] || { echo "error: GROWTH with empty BASE_KEY" >&2; exit 1; }
  "$CACHE" put-merged --max-chars "$MAX_CHARS" \
    --base-key "$BASE_KEY" --key "$APP_KEY" < /tmp/logtype-expanded.json
else
  "$CACHE" put-merged --max-chars "$MAX_CHARS" \
    --key "$APP_KEY" < /tmp/logtype-expanded.json
fi
"$CACHE" get "$APP_KEY" > /tmp/logtype-classification.json   # full plan for step 7
```

On the next run, an unchanged archive returns UPTODATE (no subagent); a grown
archive returns GROWTH and only the newly-added templates go through this file
again.

## Inspecting the cache

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/logtype-cache" list           # entries + grown_from lineage
"${CLAUDE_PLUGIN_ROOT}/bin/logtype-cache" show <APP_KEY>
```
