# Log shape classification reference (log-insights step 6)

Read this when the bootstrap reported `CACHE_MODE=GROWTH` or `NEW` (or an UPTODATE schema mismatch downgraded to NEW). It covers the cluster → classify → expand → merge → store pipeline and the full classification subagent prompt.

## Cache design in one paragraph

`app_key = sha256(sorted set of distinct log shape strings, each capped at a character limit)` — a fingerprint of the application's *embedded* message vocabulary. Use the bootstrap's `MAX_CHARS` (default 500); the same limit is applied before embedding, so the key identifies exactly what was embedded. The cache is one SQLite database, `<cache-dir>/cache.sqlite`, holding per entry the discovered `schema`, `taxonomy` and `query_plan` (plus `max_chars`, `classified_at`, `grown_from`) and one row per template: its `hash` (sha256 of the full template), its `prefix_hash` (sha256 of the first `MAX_CHARS` characters) and its `category`. Templates are stored by hash, never by text — CockroachDB templates average ~184 KB, and a text-holding entry grew past 2 GB — and the text stays in the archive's own dictionary dump, which `log-shape-insight-extract` joins on the hash. `log-shape-cache diff` yields **UPTODATE** (same fingerprint → reuse, no subagent; a template that differs from a cached one only past the character limit takes its category through the shared prefix hash), **GROWTH** (a cached entry's prefix-hash set is a proper subset of the current one → classify only the new templates, merge them into the base), or **NEW** (no compatible base → classify all). Cache dir: `~/.config/yscope-clp-plugin/log-shape-cache/` (override: `$CLP_LOG_SHAPE_CACHE_DIR` or `--cache-dir`). An entry stored before classifications were ranked (no `priority` on its taxonomy and plan) is never reused: `diff` warns and reports NEW or GROWTH as if it were absent, the app is classified again, and `put` replaces the entry. If `diff` warns that entries are in the old JSON format, tell the user they are ignored and can be deleted.

GROWTH matching needs each stored hash to come from the exact template text in the normalized NDJSON. The id-based pipeline below guarantees this by construction: the LLM only ever returns cluster ids, and `expand` hashes the member templates straight from the cluster file.

## Cluster contract (`log-shape-cluster`)

```bash
# Group the to-classify templates (from the bootstrap) by semantic similarity.
# Each template is truncated to MAX_CHARS characters and de-duplicated first, so
# identical prefixes are embedded once; representatives and members stay FULL
# templates. Embeddings come from the semantic server — nothing is installed or
# started locally, and no model is downloaded:
"${CLAUDE_PLUGIN_ROOT}/bin/log-shape-cluster" cluster \
  --max-chars "$MAX_CHARS" \
  --input /tmp/log-shapes-to-classify.ndjson
```

`cluster` prints `CLUSTERS=`/`TEMPLATES=`/`EMBEDDED=`/`MAX_CHARS=` then one `{"id","count","representative"}` line per cluster (ids `c1..cN`, largest first; the representative is a real, full template closest to the cluster centroid). `TEMPLATES` is the full count, `EMBEDDED` the distinct truncated texts actually sent. Full memberships are written to `/tmp/log-shape-clusters.json`. Tunables: `--threshold` / `$CLP_LOG_CLUSTER_THRESHOLD` (cosine, default 0.80 — raise to 0.85–0.90 if unrelated templates land in one cluster, lower to merge more), `--max-chars` / `$CLP_LOG_SHAPE_MAX_CHARS` (character cap before embedding and fingerprinting, default 500), `--semantic-endpoint` / `$CLP_SEMANTIC_ENDPOINT` (the embedding server; falls back to the `semantic-endpoint` config file, then the built-in remote endpoints), and `--batch-size` (texts per request, default 256; a separate byte ceiling bounds each request body).

There is no classification without clustering: if the embedding server is unreachable (`cluster` exits 2), report the error to the user verbatim and stop.

## Classification subagent prompt template

Spawn ONE subagent (Agent tool), model **opus**; if the Agent tool rejects `opus` as unavailable, use `sonnet`, and tell the user which model is classifying. The taxonomy is reused on every later run of the app, so it is worth the strongest model: a fast model lumped 102 of 146 vLLM clusters, per-request lines included, into config/startup. If its output fails the validation below, tell the user ("the classification failed validation — retrying once with the errors") and re-run the same model once with the error lines appended. Fill in `ARCHIVE`, the schema fields, the severity/logger vocabularies (from the bootstrap DIST lines), and paste the cluster lines from `log-shape-cluster cluster`; for GROWTH also paste the base taxonomy/plan labels:

```
You are classifying message-template clusters for a CLP archive, so a later
insight pass can run targeted queries. Do NOT write the final report — only the
classification JSON. Each cluster below groups semantically similar log shape
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
<PASTE the cluster lines printed by log-shape-cluster cluster>

[Only with field rules] FIELD RULES (already decided): every template whose
values sit in these fields already has the rule's category and is not among the
clusters above. Your taxonomy MUST include each of these categories, with a priority and a
why; you may also assign clusters to them:
<PASTE one line per category: "<category>: <field>, <field>, ...">

[Only for GROWTH] Existing categories from the previous classification — REUSE
these where a representative fits; add a new category only if none fits.
Existing query-plan labels (do not duplicate): <paste base taxonomy categories
and query_plan labels from /tmp/log-shape-base-classification.json>

This classification is cached and reused for every later capture of this
application, so rank by what matters for the application, not for one
incident: nothing about this particular run belongs in it.

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
   clp-s projects leaf columns only, and an array is a leaf returned whole:
   a column inside an array, or an object, projects nothing. The search
   wrapper rewrites such a column to the array that holds it (or the leaf
   columns under the object) and notes it in the result's
   projection_notes; a "jq" program sees the rewritten shape, so index into
   an array as `.message.content[]?.text`, not `.message.content.text`.
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
                         bootstrap's /tmp/log-shape-freqs.ndjson is an
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
4. RANK what you found, so the insight pass runs the most useful queries
   first and can offer the user a focus:
   - Give every taxonomy category a "priority" -- "high" (problems, or what
     tells whether the application is healthy and doing its job: errors,
     failures, request outcomes, latency), "medium" (useful context: resource
     use, configuration that shapes behaviour), or "low" (routine or
     uninformative) -- and a one-line "why": what a reader learns from it,
     in terms of this application. Mark at most a handful high.
   - Give every query_plan entry its "category" (a taxonomy category), a
     "priority" (same scale), and a "stage":
       "core"  -- runs on every analysis. Keep these to the queries that
                  give the overview: counts and the key probes.
       "drill" -- runs only when the user focuses on the entry's category.
                  Write 1-3 per high or medium category: the next question
                  a reader would ask once that category matters (the
                  records behind a count, a narrower failure signal, the
                  slow or failed subset), derived from the category's
                  templates like any other entry.
3. Remember: {"field":"<message>","eq":"term"} is an exact match against the
   whole message, so it correctly returns 0 unless a message equals exactly
   `term` -- it is not a sign that message content is unsearchable. Use "eq"
   when a field's full value is known (it's faster); "contains" is for
   substring matches, which message content almost always needs. Combine a
   scalar filter (severity, logger, payload leaves) with the message
   "contains" in one "all" when both apply.

Write the result as valid JSON to /tmp/log-shape-class.json with EXACTLY this
shape, then print "DONE" and nothing else:
  {
    "schema": {"timestamp":"<TS>","severity":"<SEV>","logger":"<LOGGER>","message":"<MSG>","payload":["<leaf>",...]},
    "taxonomy": [{"category":"<name>","description":"<one line>","priority":"<high|medium|low>","why":"<one line>"}],
    "assignments": [{"id":"c1","category":"<name>"}],
    "query_plan": [{"label":"<...>","match":{<filter>},"project":"<...>","grep":"<...>","jq":"<...>","method":"<count|project+grep|project+jq|semantic>","category":"<name>","priority":"<high|medium|low>","stage":"<core|drill>"}]
  }
Rules:
- `assignments` must contain EVERY cluster id above exactly once, with ONLY
  ids — never log shape text; the member templates are re-attached mechanically.
- Omit "schema" for GROWTH (the base entry already has it); include it for NEW.
- Use only the keys each query_plan entry needs (omit null/empty keys).
- Write every filter as `match`; never add a "kql" key.
- Use the discovered field names verbatim in `match` and `project`.
- Every taxonomy entry has "priority" and "why"; every query_plan entry has
  "category" (a taxonomy category), "priority", and "stage".
- [Only with field rules] The taxonomy includes every field-rule category.
```

## After the subagent returns: validate → expand → merge → store

Validation needs no message to the user unless it fails. The insight pass needs the merged classification now; the cache only needs it by the next run, so storing it runs in the background.

The subagent never writes KQL: each query_plan entry carries a `match` filter that `kql-build` renders, so an unquoted wildcard or an ungrouped AND/OR cannot reach the cache. `log-shape-cache merge` and `put` refuse an entry without a valid `match` or ranking too, as a backstop; `put` also checks every entry's category against the merged taxonomy.

```bash
# 1. Shape-validate — the fields must be ARRAYS (a bare `.assignments` test
#    passes for a scalar, which would poison the cache entry):
jq -e '(.taxonomy|type=="array") and (.assignments|type=="array") and (.query_plan|type=="array")' \
  /tmp/log-shape-class.json >/dev/null || exit 1

# 2. Plan-validate -- every query_plan entry needs a valid `match` filter,
#    method, and ranking (category, priority, stage), and every taxonomy
#    entry its priority and why. check-plan prints one "[i] OK <kql>" or
#    "[i] ERROR <label>: <why>" line per entry, and a "TAXONOMY ERROR" line per
#    unranked category, and exits 1 on any error -- in that case do NOT go on;
#    announce the retry to the user and re-run the subagent once with the
#    error lines appended to its prompt. On GROWTH the new entries may use
#    the base's categories, so pass the base classification too:
#    Always pass --drift-file: it catches a filter that will silently answer
#    about only part of its field, because clp-s stores a node per type and a
#    scalar filter reaches a path's scalar types but never its Object or
#    StructuredArray nodes. On one archive `toolUseResult:*` matches 116
#    records, not the 4,641 that carry the path. An entry fails when the share
#    it can reach falls below 0.95.
"${CLAUDE_PLUGIN_ROOT}/bin/kql-build" check-plan /tmp/log-shape-class.json \
  --drift-file "$TYPE_DRIFT_FILE" || exit 1                                              # NEW
"${CLAUDE_PLUGIN_ROOT}/bin/kql-build" check-plan /tmp/log-shape-class.json \
  --drift-file "$TYPE_DRIFT_FILE" \
  --categories-from /tmp/log-shape-base-classification.json || exit 1                    # GROWTH

# 3. Expand id-based assignments to every member template, by hash. Exits 2
#    and writes NOTHING on missing/unknown/duplicate ids — in that case do NOT
#    go on; announce the retry to the user, re-run the subagent once, and
#    expand again. On GROWTH add
#    --categories-from /tmp/log-shape-base-classification.json: the classifier
#    lists only the categories it adds, so a field rule's category that the base
#    already ranks would otherwise be refused:
"${CLAUDE_PLUGIN_ROOT}/bin/log-shape-cluster" expand \
  --clusters /tmp/log-shape-clusters.json \
  --classification /tmp/log-shape-class.json \
  --output /tmp/log-shape-expanded.json                                       # NEW
"${CLAUDE_PLUGIN_ROOT}/bin/log-shape-cluster" expand \
  --clusters /tmp/log-shape-clusters.json \
  --classification /tmp/log-shape-class.json \
  --categories-from /tmp/log-shape-base-classification.json \
  --output /tmp/log-shape-expanded.json                                       # GROWTH

# 4. Merge (milliseconds). Use MODE/BASE_KEY from the bootstrap output
#    (re-declare — fresh shell). GROWTH merges the new templates into the base
#    entry (templates by hash, taxonomy and query_plan unioned, grown_from
#    recorded); NEW passes the expanded classification through. The result is
#    what step 7 reads:
CACHE="${CLAUDE_PLUGIN_ROOT}/bin/log-shape-cache"
if [[ "$MODE" == "GROWTH" ]]; then
  # Guard: an empty BASE_KEY would silently keep ONLY the new templates.
  [[ -n "$BASE_KEY" ]] || { echo "error: GROWTH with empty BASE_KEY" >&2; exit 1; }
  "$CACHE" merge --base-key "$BASE_KEY" < /tmp/log-shape-expanded.json > /tmp/log-shape-classification.json
else
  "$CACHE" merge < /tmp/log-shape-expanded.json > /tmp/log-shape-classification.json
fi
```

Then store it for the next run, as a separate background Bash call (`run_in_background`, no trailing `&`), and go straight on to step 7 without waiting.

**Key it with the field rules, not with the bootstrap's `APP_KEY`.** A ruled template takes its category from its rule, so the rules are part of the classification and belong in the key. The bootstrap computed `APP_KEY` before the rules existed — they come from `log-shape-cluster fields`, which runs later — so storing under it would file this classification as though no rules had applied. Re-key first, passing the same rules file you gave `cluster`, and use the same `--max-chars` as the bootstrap or the fingerprint will not match next run:

```bash
KEY=$("${CLAUDE_PLUGIN_ROOT}/bin/log-shape-cache" key \
  --log-shapes-file "$LOG_SHAPES_FILE" --max-chars "$MAX_CHARS" \
  --field-rules /tmp/log-shape-field-rules.json)
"${CLAUDE_PLUGIN_ROOT}/bin/log-shape-cache" put --key "$KEY" --max-chars "$MAX_CHARS" \
  --field-rules /tmp/log-shape-field-rules.json \
  < /tmp/log-shape-classification.json
```

Omit both `--field-rules` when this run applied no rules; "no rules" is its own key and must not be conflated with a rule set. Sanity-check that the key you store under matches what the clusterer used: `expand` prints `RULES_DIGEST=`, and it must equal the digest in the key you just computed.

When it finishes, mention in your next message that the classification is cached for later runs; it needs no message of its own. If it fails, report the error; this run's report does not depend on it.

On the next run, an unchanged archive returns UPTODATE (no subagent); a grown archive returns GROWTH and only the newly-added templates go through this file again.

## Inspecting the cache

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/log-shape-cache" list           # entries + grown_from lineage
"${CLAUDE_PLUGIN_ROOT}/bin/log-shape-cache" show <APP_KEY>
```
