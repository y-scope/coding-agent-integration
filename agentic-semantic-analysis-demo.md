# Agentic Semantic Analysis — Demo Walkthrough

**Define the question and filters, then test semantic search and discovery on that data**

Read the [capabilities overview](agentic-semantic-analysis-capabilities.md) first for the proposed observability pilot. Use the [tooling companion](agentic-semantic-analysis-tooling.md) for tool behavior and composition.

**Status: preparation guide, with no measured results yet.** The earlier demo's data was on the Uber laptop and was not backed up here. This repository does not currently bundle a replacement dataset, pinned demo environment, or saved run outputs. The procedure below is based on the repository's wrapper interfaces; it has not been executed against a replacement dataset for this document.

## 1. Choose the question and data

Start with one service, an operational symptom, and a known incident with a comparable reference window. An illustrative question is “What explains the increase in request latency?” Replace it with the actual symptom before running the demo. Keep the known incident explanation separate until evaluating the result.

Prepare a capture covering both windows and relevant hosts. Declare the incident and reference selections separately, including every service, time, host, or request predicate. Structure discovery, template inventory, semantic search, and analysis must describe those selections. For the comparison with initial semantic retrieval, retain the service/time selection as the reference scope for coverage. For a shareable demo, select data that can be distributed with the walkthrough.

The input may also be results retrieved from another search system. Export those records in a supported format and retain the source query, retrieval time, and any result limits. The local parsing and compression steps below turn that selection into a context cache for repeated analysis and quick lookup. This walkthrough exercises the local path; distributed worker execution and merging require a separate deployment-level demonstration.

Record the following before the run:

| Item | Required for the demo package |
|---|---|
| Data | Location, source, checksum, service, capture period, and record count; source query, retrieval time, and truncation limits for externally retrieved results. |
| Scope | Exact incident and reference predicates, hosts/components included, exclusions, and subsequent filter changes. |
| Question | The exact symptom prompt and the keyword baseline chosen before inspecting semantic results. |
| Expected evidence | Known incident findings and a reviewed set of relevant records/templates for evaluation. |
| Environment | Repository commit, exact `clp-s` build, OS, embedding endpoint/model, agent model, and relevant settings. |
| Outputs | Compression summary, raw dictionary, normalized templates, queries, results, timings, and final report. |

## 2. Prepare the tools

Run commands from the repository root in Bash. The [plugin README](plugins/clp/README.md) describes installation and binary resolution; [local testing](LOCAL_TESTING.md) covers smoke checks. The walkthrough requires Bash, Python 3, `jq`, `rg` (ripgrep), and a `clp-s` build supporting semantic search and the shapes API (`clp-core` 0.13+ for the dictionary path). Record the exact build used rather than relying only on that minimum version.

Configure a running embedding endpoint explicitly. The example below uses localhost and assumes a compatible service is already running there; these commands do not start it. Without an explicit endpoint, the wrapper can try remote services. Account separately for content sent to the agent model during classification and investigation.

```bash
DEMO_BIN="$PWD/plugins/clp/bin"
DEMO_RUN="$(mktemp -d /tmp/clp-semantic-demo.XXXXXX)"
DEMO_LOGS=/absolute/path/to/prepared-jsonl-logs
DEMO_ARCHIVE="$DEMO_RUN/archive"
export CLP_SEMANTIC_ENDPOINT=http://localhost:8080
export CLP_LOGTYPE_CACHE_DIR="$DEMO_RUN/classification-cache"
git rev-parse HEAD
```

Replace `DEMO_LOGS` with the prepared dataset directory. If needed, set `CLP_S_BIN` to the exact binary. Keep this shell open for the subsequent commands. The run directory is temporary; copy the finished evidence package to a durable location before sharing it.

For optional clustering, no model install is needed: `logtype-cluster` embeds templates through the same already-running embedding server that powers semantic search (`setup` has been removed). Configure the endpoint once — `CLP_SEMANTIC_ENDPOINT`, `--semantic-endpoint`, or the `semantic-endpoint` config file — or rely on the built-in default. There is no local dependency (clustering is pure Python standard library). Templates are capped at `--max-chars` (default 500 characters) and de-duplicated before embedding; record the endpoint and the character limit for reproduction.

## 3. Ingest and inspect the capture

For prepared JSONL whose timestamp field is named `timestamp`:

```bash
"$DEMO_BIN/clp-s-compress-folder" \
  --folder "$DEMO_LOGS" \
  --extensions jsonl,ndjson \
  --timestamp-key timestamp \
  --output-dir "$DEMO_ARCHIVE"

"$DEMO_BIN/clp-s-search-kql" "$DEMO_ARCHIVE" 'stats.schema_tree'

"$DEMO_BIN/logtype-insights-bootstrap" \
  --out-dir "$DEMO_RUN/bootstrap" \
  --cache-dir "$CLP_LOGTYPE_CACHE_DIR" \
  "$DEMO_ARCHIVE"
```

Use the actual timestamp field for your data. Supported plain-text logs can instead be ingested with `--structurize`; inspect parsing warnings and account for skipped files. Do not apply that preprocessing to already structured JSONL.

Save the compression summary and bootstrap output. Confirm that records were ingested, inspect the schema, and require `FREQS=OK` for the dictionary-based demo. If it reports `FREQS=UNAVAILABLE`, recompress the logs with the current binary. Bootstrap field distributions are sampled and must not be reported as complete frequencies.

Preserve the dictionary before normalization as well:

```bash
"$DEMO_BIN/clp-s-search-kql" "$DEMO_ARCHIVE" 'stats.log_shapes' \
  > "$DEMO_RUN/shapes.stdout"
```

The wrapper writes metadata headers alongside JSON lines. Extract JSON lines before processing results as NDJSON. Keep raw shape identifiers for traceability; normalized templates render variable types as `<*>` and may merge display-equivalent shapes.

These schema and dictionary outputs cover the capture archive, which in this example includes both windows. They are candidate metadata for the incident selection. The current stats commands and bootstrap do not take an arbitrary record filter. Establish field and template occurrence with the scoped queries before describing the incident data's structure or inventory. Alternatively, prepare separate archives containing exactly each declared selection and run discovery on each. Record which approach was used.

## 4. Reproduce semantic search

Choose the actual incident window and replace the field names below with those discovered in the capture. This example assumes `timestamp`, `level`, `logger`, and `message` exist and that the archive is already restricted to the chosen service.

```bash
DEMO_TGE=REPLACE_WITH_INCIDENT_START_EPOCH_MS
DEMO_TLE=REPLACE_WITH_INCIDENT_END_EPOCH_MS

time "$DEMO_BIN/clp-s-search-kql" \
  --tge "$DEMO_TGE" --tle "$DEMO_TLE" \
  --projection timestamp,level,logger,message \
  "$DEMO_ARCHIVE" '*' > "$DEMO_RUN/scoped-records.stdout"

time "$DEMO_BIN/clp-s-search-kql" \
  --tge "$DEMO_TGE" --tle "$DEMO_TLE" \
  --semantic-endpoint "$CLP_SEMANTIC_ENDPOINT" \
  --semantic-top-k 5 --semantic-threshold 0.3 \
  --projection timestamp,level,logger,message \
  "$DEMO_ARCHIVE" 'semantic("requests waiting for available capacity")' \
  > "$DEMO_RUN/semantic-results.stdout"
```

The question and retrieval settings are starting examples, not validated choices. Record changes to them and show both relevant and irrelevant matches. Top-k limits candidate templates; a short result set does not establish exhaustive recall. If the archive includes multiple services, add the same service/host constraints to both queries.

For a transparent keyword baseline over exactly the retrieved scope, this example tests `timeout`, `slow`, and `latency` in the message field:

```bash
rg '^\{' "$DEMO_RUN/scoped-records.stdout" \
  | jq -c 'select((.message // "") | test("timeout|slow|latency"; "i"))' \
  > "$DEMO_RUN/keyword-results.ndjson"

rg '^\{' "$DEMO_RUN/semantic-results.stdout" \
  > "$DEMO_RUN/semantic-results.ndjson"
```

Choose baseline terms appropriate to the real question. Check each search's exit status and stderr before interpreting an empty result; `rg` also returns a nonzero status when it finds no matches. Keep bulk files outside the model context and inspect only selected evidence.

This local keyword scan is useful for comparing retrieval coverage. It is not a performance benchmark against Uber's current search system. For that comparison, run the existing system over the same dataset and measure its actual investigation workflow. Do not use an unsupported message-field query returning zero as evidence that semantic search improves recall.

## 5. Build context for the filtered data and question

Optional clustering of the capture inventory makes representatives available for reusable classification. Templates are capped at `--max-chars` characters and de-duplicated before embedding, but representatives and members are the full templates, and the result still covers the capture; select and validate its applicable members for the investigation scope:

```bash
"$DEMO_BIN/logtype-cluster" cluster \
  --max-chars 500 \
  --input "$DEMO_RUN/bootstrap/logtypes.ndjson" \
  --output "$DEMO_RUN/clusters.json"
```

Use the installed `logtype-insights` skill in an agent session with the actual archive path, run directory, endpoint, and windows substituted into this prompt:

> Analyze ARCHIVE_PATH for QUESTION under INCIDENT_FILTERS, compared with REFERENCE_FILTERS. Use the existing bootstrap artifacts in RUN_DIRECTORY and the classification cache in RUN_DIRECTORY/classification-cache. Use EMBEDDING_ENDPOINT for semantic queries. Treat capture-wide metadata as candidates: establish the fields and patterns present under each selection's filters. Reuse applicable template labels and adapt the categories and query plan to the selected data and question. Cross-check semantic results against the scoped inventory. Execute queries for counts and localization. Keep bulk results in local files. Return the filters, exact queries, selected records supporting each finding, uncategorized templates, and uncertainty. Record any changes of scope. Distinguish observed changes from possible causes. Do not treat sampled field distributions or null dictionary counts as measured frequencies, and label any sampled discovery as incomplete.

The agent performs classification and query planning; clustering alone does not populate the classification cache. Preserve the resulting classification, scope-specific working context, and executed queries with the report. Check whether grouped members justify their representative's label. A reusable label does not establish that a template occurs within the current filters or answers the current question.

For each proposed finding, ask whether the inventory added relevant evidence beyond the initial semantic search. For a missing event, name the reference expectation and verify its occurrence in both windows. Zero matches are an observation to explain, not proof of a logging failure.

## 6. Repeat and measure

After the agent stores a classification, rerun bootstrap using the same archive and classification cache:

```bash
time "$DEMO_BIN/logtype-insights-bootstrap" \
  --out-dir "$DEMO_RUN/bootstrap-repeat" \
  --cache-dir "$CLP_LOGTYPE_CACHE_DIR" \
  "$DEMO_ARCHIVE"
```

For a successfully stored matching template set, expect `CACHE_MODE=UPTODATE` and `TO_CLASSIFY=0`. Verify those outputs. Then rerun the investigation's queries to measure the remaining work. Report classification reuse and semantic-cache reuse separately.

On a second capture of the service, inspect the template-set difference. A strict superset can exercise `GROWTH`; a changed capture can also omit previously seen patterns. Review the merged classification and schema-dependent query plan before attributing a speedup to correct reuse.

### Evaluate semantic-search scaling

Use increasing data volumes in two series: captures dominated by repeated occurrences of known templates, and captures that introduce additional template diversity. Record the actual template counts in both. Keep the question, filter selectivity, retrieval settings, hardware, embedding model, and output policy comparable, and record any differences. Controlled repetition can illustrate the mechanism but must be labeled synthetic; representative service data is needed to judge the intended workload.

For each capture, measure a first run and a repeat run. Record any endpoint-side cache state; a first run alone does not prove all embedding work was cold. Capture embedding requests/items, candidate-template counts, cache size, resource use, and semantic-stage versus record-retrieval time where instrumentation permits. If only end-to-end timing is available, report that explicitly rather than estimating internal timings. Classification-cache reuse is a separate measurement.

The hypothesis is that repeated events increase record volume much faster than semantic representation size, while new templates add embedding and matching work. Validate that relationship alongside retrieval quality. A small demo can establish this mechanism; a claim about petabyte-scale throughput, latency, or cost needs a representative large-scale run with stated concurrency and infrastructure.

## 7. Results to attach

The table is intentionally unfilled: no replacement demo run has been recorded for this package.

| Measurement | Result to attach |
|---|---|
| Input and reduction | Source bytes, ingested and selected record counts, skipped input, discovered fields, raw shape entries, normalized templates, clusters; distinguish capture-wide totals from scoped results. |
| Retrieval | Reviewed relevant/irrelevant matches for each approach, including examples unique to either approach. |
| Added investigation value | Discovered fields that enable useful filtering or correlation; a supported event type or missing expected event the initial search overlooked, or an explicit result that the inventory added none. |
| Timing and resources | Setup, ingestion, discovery, key-value, wildcard, and semantic search queries, classification, follow-up queries, and time to first supported answer; compute, memory, and archive I/O where observable. |
| Semantic-search scale | Record-to-template ratio at each volume, structural and template diversity, embedding items and cost, semantic-cache state and size, and cold/warm latency; stage timings, hardware, and concurrency. |
| Model context | Actual model input size, calls, and selected records exposed to the model. |
| Reuse | First-run versus repeat-run timings and cache states; new-template behavior if tested. |
| Local context cache | Source retrieval and local parsing/compression time, archive size, and repeated lookup latency; state whether each question was answerable from cached data or required a fresh source query. |
| Distributed context assembly, if tested separately | Worker processing and merge time, partial-result size, context delivered to the agent, and handling of overlapping results. Mark untested when using only the local walkthrough. |
| Evidence | Exact queries, full saved result files, representative records, known incident comparison, and unresolved questions. |

Before sending this as a reproducible demo, attach the dataset or its accessible location, pin the environment, replace the example question and scope with actual values, and save one successful run. Include failures and irrelevant results so Yun can judge what needs further prototyping.

## 8. Adapt it to an Uber scenario

Replace the demo capture with one service's incident and reference data, configure the approved embedding and agent endpoints, and update the field mappings. Agree on the operational question and evaluation criteria before inspecting results. Run the team's existing investigation method and the CLP workflow against the same scope, using the evidence checklist above to compare retrieval quality, time to a supported answer, cost, and reuse.

If the results warrant a VP proposal, summarize the operational problem, one measured improvement, the supporting evidence, integration requirements, and the scope of the next experiment. The [capabilities overview](agentic-semantic-analysis-capabilities.md) provides the entry point for that discussion.
