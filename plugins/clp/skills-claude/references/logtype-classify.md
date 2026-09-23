# Logtype classification reference (logtype-insights step 6)

Read this when the bootstrap reported `CACHE_MODE=GROWTH` or `NEW` (or an UPTODATE schema mismatch downgraded to NEW). It covers the cluster → classify → expand → store pipeline and the full classification subagent prompt.

## Cache design in one paragraph

`app_key = sha256(sorted set of distinct logtype strings, each capped at a character limit)` — a fingerprint of the application's *embedded* message vocabulary. Use the bootstrap's `MAX_CHARS` (default 512); the same limit is applied before embedding, so the key identifies exactly what was embedded. The stored `templates[].logtype` are still the FULL, byte-exact strings. The entry stores the discovered `schema`, `taxonomy`, per-template `templates` classification, and `query_plan` (plus `app_key`, `max_chars`, `classified_at`, `grown_from`). `logtype-cache diff` yields **UPTODATE** (same fingerprint → reuse, no subagent; a full template that only differs past the character limit is appended with the category of the truncated form it shares, so the entry stays complete), **GROWTH** (a cached entry's truncated set is a proper subset of the current one → classify only the new templates, merge into the base via `put-merged`), or **NEW** (no compatible base → classify all, store fresh). Cache dir: `~/.config/yscope-clp-plugin/logtype-cache/` (override: `$CLP_LOGTYPE_CACHE_DIR` or `--cache-dir`).

GROWTH matching requires the stored `templates[].logtype` strings to be **byte-exact** copies of the normalized NDJSON. The id-based pipeline below guarantees this by construction: the LLM only ever returns cluster ids, and `expand` re-attaches the member logtypes verbatim from the cluster file.

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

`cluster` prints `CLUSTERS=`/`TEMPLATES=`/`EMBEDDED=`/`MAX_CHARS=` then one `{"id","count","representative"}` line per cluster (ids `c1..cN`, largest first; the representative is a real, full template closest to the cluster centroid). `TEMPLATES` is the full count, `EMBEDDED` the distinct truncated texts actually sent. Full memberships are written to `/tmp/logtype-clusters.json`. Tunables: `--threshold` / `$CLP_LOG_CLUSTER_THRESHOLD` (cosine, default 0.80 — raise to 0.85–0.90 if unrelated templates land in one cluster, lower to merge more), `--max-chars` / `$CLP_LOGTYPE_MAX_CHARS` (character cap before embedding and fingerprinting, default 512), `--semantic-endpoint` / `$CLP_SEMANTIC_ENDPOINT` (the embedding server; falls back to the `semantic-endpoint` config file, then the built-in remote endpoints), and `--batch-size` (texts per request, default 256; a separate byte ceiling bounds each request body).

**Raw-NDJSON last resort** (only if no embedding server is reachable): skip clustering; paste `/tmp/logtypes-to-classify.ndjson` directly into the prompt, replace the `assignments` output contract with `"templates": [{"logtype":"<verbatim template>","category":"..."}]`, instruct the subagent to copy each logtype **byte-exact** from the input, skip `expand`, and piped straight into `put-merged --max-chars "$MAX_CHARS"`. Slower and fragile for GROWTH — prefer fixing the endpoint.

## Classification subagent prompt template

Spawn ONE subagent (Agent tool), model **haiku**; if its output fails the validation below, tell the user ("first classification attempt failed validation — retrying with a stronger model") and retry once with `sonnet`. Fill in `ARCHIVE`, the schema fields, the severity/logger vocabularies (from the bootstrap DIST lines), and paste the cluster lines from `logtype-cluster cluster`; for GROWTH also paste the base taxonomy/plan labels:

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
   expressed in the discovered field names. Per entry: label, the filter as
   a structured `match` object, the columns to --projection, and the method.
   Never write a KQL string: the plan runner renders `match` to KQL itself,
   quoting and escaping every value and parenthesizing every group, and an
   entry carrying a "kql" key is rejected. `match` grammar (nest freely):
     {"all": [F, ...]}                  every child matches (AND)
     {"any": [F, ...]}                  at least one child matches (OR)
     {"not": F}                         F does not match
     {"field": "<f>", "eq": V}          exact value; fastest, so use it for
                                        a scalar field whose full value is
                                        known (severity, logger, a payload
                                        leaf)
     {"field": "<f>", "contains": "text"}
                                        substring -- what message content
                                        almost always needs. The text is
                                        literal: spaces, quotes, `*` and `?`
                                        need no escaping
     {"field": "<f>", "contains": ["a", "b"]}
                                        substrings in this order, e.g. a
                                        template's static fragments around
                                        its <*> variables
     {"field": "<f>", "prefix": "text"}  starts with
     {"field": "<f>", "exists": true}   the field is present
     {"field": "<f>", "gt": N}          numeric comparison (also gte, lt, lte)
     {"semantic": "text"}               semantic search; only inside an
                                        "all" beside a concrete filter, never
                                        alone and never under "not"
   Methods:
     - "count"        -> count matches via native `--count` (in-engine
                         aggregation, mutually exclusive with --projection;
                         runs in ~constant time regardless of match volume --
                         use it even for a filter matching most of the
                         archive). Do NOT use `--projection ... | grep -c
                         '^{'` for a plain count -- that's much slower once
                         the match set is large. When the filter is
                         expressible purely as message text, summing
                         `count` over the matching templates in the
                         bootstrap's /tmp/logtype-freqs.ndjson is an
                         alternative that's O(distinct templates) instead
                         of O(records).
     - "project+grep" -> fold the static text into `match` as an "any" of
                         "contains" nodes, even for a keyword alternation --
                         that still runs inside the search engine, not as a
                         post-filter. Only add a real `grep` pipe stage when
                         the text needs a genuine regex feature (anchors,
                         character classes, backreferences) that "contains"
                         can't express; never use grep merely to implement
                         `a|b|c` matching.
     - "project+jq"   -> project message/payload, jq-filter (e.g. a numeric
                         threshold on a payload leaf)
     - "semantic"     -> a "semantic" node inside an "all" beside a scalar
                         filter, ONLY for an ambiguous template or to group
                         similar ones
   Example (Mongo): {"label":"Slow queries",
     "match":{"field":"attr.durationMillis","exists":true},
     "project":"t.$date,attr.durationMillis,msg",
     "jq":"select((.attr.durationMillis//0)>100)","method":"project+jq"}
   Example (vLLM):  {"label":"Memory or OOM warnings",
     "match":{"all":[{"field":"level","eq":"WARNING"},
                     {"any":[{"field":"message","contains":"memory"},
                             {"field":"message","contains":"OOM"},
                             {"field":"message","contains":"oom-killer"}]}]},
     "project":"timestamp,level,message",
     "method":"project+grep"}
   For GROWTH, add new plan entries only for genuinely new signals.
3. Remember: {"field":"<message>","eq":"term"} is an exact match against the
   whole message, so it correctly returns 0 unless a message equals exactly
   `term` -- it is not a sign that message content is unsearchable. Use "eq"
   when a field's full value is known (it's faster); "contains" is for
   substring matches, which message content almost always needs. Combine a
   scalar filter (severity, logger, payload leaves) with the message
   "contains" in one "all" when both apply.

Write the result as valid JSON to /tmp/logtype-class.json with EXACTLY this
shape, then print "DONE" and nothing else:
  {
    "schema": {"timestamp":"<TS>","severity":"<SEV>","logger":"<LOGGER>","message":"<MSG>","payload":["<leaf>",...]},
    "taxonomy": [{"category":"<name>","description":"<one line>"}],
    "assignments": [{"id":"c1","category":"<name>"}],
    "query_plan": [{"label":"<...>","match":{<filter>},"project":"<...>","grep":"<...>","jq":"<...>","method":"<count|project+grep|project+jq|semantic>"}]
  }
Rules:
- `assignments` must contain EVERY cluster id above exactly once, with ONLY
  ids — never logtype text; the member templates are re-attached mechanically.
- Omit "schema" for GROWTH (the base entry already has it); include it for NEW.
- Use only the keys each query_plan entry needs (omit null/empty keys).
- Write every filter as `match`; never add a "kql" key.
- Use the discovered field names verbatim in `match` and `project`.
```

## After the subagent returns: validate → expand → store

Tell the user the classifier returned and you are validating and storing the plan; after `put-merged` succeeds, report the taxonomy and that the classification is now cached.

The subagent never writes KQL: each query_plan entry carries a `match` filter that `kql-build` renders, so an unquoted wildcard or an ungrouped AND/OR cannot reach the cache. `put-merged` refuses to store an entry without a valid `match` too, as a backstop.

```bash
# 1. Shape-validate — the fields must be ARRAYS (a bare `.assignments` test
#    passes for a scalar, which would poison the cache entry):
jq -e '(.taxonomy|type=="array") and (.assignments|type=="array") and (.query_plan|type=="array")' \
  /tmp/logtype-class.json >/dev/null || exit 1

# 2. Plan-validate -- every query_plan entry needs a valid `match` filter and
#    method. check-plan prints one "[i] OK <kql>" or "[i] ERROR <label>: <why>"
#    line per entry and exits 1 on any ERROR -- in that case do NOT store;
#    announce the retry to the user and re-run the subagent (sonnet fallback)
#    with the ERROR lines appended to its prompt:
"${CLAUDE_PLUGIN_ROOT}/bin/kql-build" check-plan /tmp/logtype-class.json || exit 1

# 3. Expand id-based assignments to every member template. Exits 2 and writes
#    NOTHING on missing/unknown/duplicate ids — in that case do NOT store;
#    announce the retry to the user, re-run the subagent (sonnet fallback),
#    and expand again:
"${CLAUDE_PLUGIN_ROOT}/bin/logtype-cluster" expand \
  --clusters /tmp/logtype-clusters.json \
  --classification /tmp/logtype-class.json \
  --output /tmp/logtype-expanded.json

# 4. Store. Use MODE/APP_KEY/BASE_KEY/MAX_CHARS from the bootstrap output
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

On the next run, an unchanged archive returns UPTODATE (no subagent); a grown archive returns GROWTH and only the newly-added templates go through this file again.

## Inspecting the cache

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/logtype-cache" list           # entries + grown_from lineage
"${CLAUDE_PLUGIN_ROOT}/bin/logtype-cache" show <APP_KEY>
```
