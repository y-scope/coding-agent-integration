# Logtype insight reference (logtype-insights step 7)

Read this when a classification exists (`/tmp/logtype-classification.json`, either fresh from step 6 or fetched from the cache on UPTODATE). It covers building the insight subagent prompt and the report format.

## Build the prompt from the classification

Extract the pieces to paste (one call, fresh shell):

```bash
jq -c '.schema'  /tmp/logtype-classification.json
jq -r '.taxonomy[] | "- \(.category): \(.description)"' /tmp/logtype-classification.json
jq -r '.templates | group_by(.category)[] | "### \(.[0].category)\n" + (map("- " + .logtype) | join("\n"))' \
  /tmp/logtype-classification.json
jq -c '.query_plan[]' /tmp/logtype-classification.json
```

Spawn ONE insight subagent (Agent tool), model **haiku**; if the report comes back unusable, tell the user before re-spawning with `sonnet`. Replace `SEARCH_WRAPPER` with the **resolved absolute path** of `${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql` — the subagent does not inherit `${CLAUDE_PLUGIN_ROOT}`, so the literal variable will not work there.

## Insight subagent prompt template

Fill in `ARCHIVE`, `GOAL`, the schema fields, and the extracted taxonomy / templates-by-category / query_plan:

```
Analyze this CLP archive by executing the provided query plan: ARCHIVE
Search wrapper: SEARCH_WRAPPER
Goal: GOAL

SCHEMA (field names in this archive):
  timestamp: <TS>   severity: <SEV>   logger: <LOGGER>   message: <MSG>
  payload leaves: <...>     time-range flags work: <yes if epoch / no if string>

TAXONOMY (categories):
<PASTE taxonomy>

TEMPLATES BY CATEGORY (every distinct message template; <*> marks variables):
<PASTE templates grouped by category>

QUERY PLAN (each entry derived from a real template — execute each):
<PASTE query_plan>

Method (follow strictly):
1. Execute every query_plan entry. For "count": run the KQL and `grep -c
   '^{'`. For "project+grep": prefer folding the grep target into the KQL as
   `<message>:*text*` (wildcarded, works — see step 2) alongside the given
   `kql` filter, and only pipe to `grep -Ei '<grep>'` when the target needs a
   regex the wildcard syntax can't express. For "project+jq": run the KQL
   with --projection, then `grep '^{' | jq -r '<jq>'`. For "semantic": run
   `semantic("...") AND <kql>` with --projection.
2. `<message>:term` is an **exact** match against the whole field value, so
   it correctly returns 0 unless a message equals exactly `term` — the
   message field follows the same KQL rule as any other field. Exact match
   is faster, so use it directly wherever a field's full value is known
   (severity, an exact logger path); message content is free text, so it
   almost always needs a substring wildcard: `<message>:*term*`. Prefer that
   over project+grep for a template's distinctive STATIC text. Combine with
   a scalar field in one compound query when you can:
     <severity>:<value> AND <message>:*term*
     <logger>:*<substr>* AND <message>:*term*
   Fall back to project+grep only when the distinctive text needs a regex the
   wildcard syntax can't express.
3. Per-template FREQUENCIES for the whole archive in one pass (the count
   baseline) — project the message field, templatize, uniq -c:
     SEARCH_WRAPPER --projection <message> ARCHIVE '*' \
       | grep '^{' | jq -r '.<message>' \
       | sed -E 's/\{[^}]+\}/<*>/g; s/0x[0-9a-fA-F]+/<*>/g; s/\b[0-9]+\b/<*>/g' \
       | sort | uniq -c | sort -rn
   Report the dominant templates by count as "top repeated messages".
4. Total records: '*'. Severity breakdown: one count per severity value.
   Logger breakdown: project logger + uniq -c. Time span: project the
   timestamp field and use head/tail (records are chronological; do NOT sort),
   OR use --tge/--tle if the schema says time-range flags work.
5. MANDATORY semantic pass — in addition to any query_plan entries whose
   method is "semantic", always run at least one scoped semantic() query
   derived from the goal or the dominant templates, e.g.
   semantic("...") AND <severity>:<value> or
   semantic("...") AND <logger>:*<substr>*. Never run an unscoped
   semantic(). Discard any query that returns nothing or only
   generic/meaningless logtypes — do not include it in the report.

Efficiency rules:
- Compound KQL, not many separate queries.
- Project aggressively; omit --projection only when you need the full record.
- Do NOT use --tge/--tle unless the schema says the timestamp is epoch.
- `<message>:term` is an exact match, so it correctly returns 0 unless a
  message equals exactly `term`. Exact match is faster, so use it when you
  know a field's full value; message content is free text and almost always
  needs a substring wildcard — `<message>:*term*`. Fall back to projecting
  and grepping/jq-filtering only when the match needs a regex.
- Add --ignore-case when case is uncertain.

Return ONLY a Markdown Logtype Insights Report with these sections:
1. Summary — total records, severity counts, time span, top logger/component.
2. Logtype Baseline — total distinct templates; the top N templates by
   frequency (count + template); the discovered category breakdown
   (errors: K templates, performance: K, ...). This is the spine of the
   report.
3. Issues & Warnings — error/warning counts, top 3 warning TEMPLATES (not
   substrings), actionable problems; semantic-only findings if any.
4. Notable Categories — for each discovered category of interest, counts +
   representative templates and what they indicate.
5. Performance Signals — timing/throughput/slow-operation templates and counts
   (if the app produces any); semantic-only findings if any.
6. Configuration & Startup — config/init templates grounded in the baseline
   (if any).
7. Semantic Search Coverage — MANDATORY (the semantic pass always runs).
   Report ONLY meaningful findings: matches that template-classification
   missed or confirmed, with their queries. NEVER list empty/no-hit queries
   or meaningless matches — drop them. If nothing meaningful was found, the
   section is a single line saying semantic search surfaced nothing beyond
   the baseline.
8. Top 3 follow-up KQL queries (derived from templates, mix keyword+semantic).
```

## Report format (present in this order)

1. **Summary** — total records, severity counts, archive span, top logger/component.
2. **Logtype Baseline** — distinct template count, top templates by frequency with counts, the discovered category breakdown. The spine of the report.
3. **Issues & Warnings** — errors, warnings, top 3 warning *templates* (grounded, not guessed), actionable problems; semantic-only findings if any.
4. **Notable Categories** — per discovered category of interest, counts + representative templates and what they indicate.
5. **Performance Signals** — timing/throughput/slow-operation templates and counts (if the app produces any); semantic-only findings if any.
6. **Configuration & Startup** — config/init templates grounded in the baseline (if any).
7. **Semantic Search Coverage** — mandatory (the semantic pass always runs), but report only meaningful findings — matches that template-classification missed or confirmed, with their queries; drop empty/no-hit queries. If nothing meaningful surfaced, one line saying so.
8. **Follow-up queries** — 2–3 concrete queries derived from templates.
