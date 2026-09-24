"""
classification - validate the ranking a log-insights classification
carries on top of its query filters.

The classifier ranks what it found, so the insight pass can run the most
useful queries first and offer the user a focus:

  taxonomy[]    {"category", "description", "priority", "why"}
  query_plan[]  {"label", "match", "method", ..., "category", "priority", "stage"}

`priority` is one of PRIORITIES. `why` is one line on why the category matters
for this application. `stage` is "core" (runs on every analysis) or "drill"
(runs only when the user focuses on the entry's category). Each plan entry's
`category` names a taxonomy category.

A cached classification without this ranking is not reused: the application is
classified again and the entry replaced.
"""

from kql_build import FilterError, check_entry

PRIORITIES = ("high", "medium", "low")
STAGES = ("core", "drill")


def taxonomy_errors(taxonomy):
    """Error lines for the taxonomy entries that lack a valid ranking."""
    if not isinstance(taxonomy, list):
        return ["taxonomy must be a JSON array"]
    errors = []
    for i, c in enumerate(taxonomy, 1):
        if not isinstance(c, dict) or not isinstance(c.get("category"), str) or not c["category"]:
            errors.append(f"taxonomy[{i}]: needs a 'category' string")
            continue
        name = c["category"]
        if c.get("priority") not in PRIORITIES:
            errors.append(f"taxonomy[{i}] {name}: 'priority' must be one of {', '.join(PRIORITIES)}")
        for key in ("description", "why"):
            if not isinstance(c.get(key), str) or not c[key].strip():
                errors.append(f"taxonomy[{i}] {name}: needs a non-empty '{key}' string")
    return errors


def check_ranked_entry(entry, categories=None):
    """Validate one classified query_plan entry: its filter (check_entry) and
    its ranking. `categories`, when given, is the set the entry's category must
    belong to. Returns the entry's KQL or raises FilterError."""
    kql = check_entry(entry)
    category = entry.get("category")
    if not isinstance(category, str) or not category:
        raise FilterError("plan entry needs a 'category' naming a taxonomy category")
    if categories is not None and category not in categories:
        raise FilterError(f"category {category!r} is not in the taxonomy")
    if entry.get("priority") not in PRIORITIES:
        raise FilterError(f"'priority' must be one of {', '.join(PRIORITIES)}")
    if entry.get("stage") not in STAGES:
        raise FilterError(f"'stage' must be one of {', '.join(STAGES)}")
    return kql


def plan_errors(plan, categories=None):
    """Error lines for the query_plan entries that cannot be stored or run."""
    if not isinstance(plan, list):
        return ["query_plan must be a JSON array"]
    errors = []
    for i, entry in enumerate(plan, 1):
        try:
            check_ranked_entry(entry, categories)
        except FilterError as exc:
            label = entry.get("label", "") if isinstance(entry, dict) else ""
            errors.append(f"query_plan[{i}] {label}: {exc}")
    return errors


def category_names(taxonomy):
    return {c["category"] for c in taxonomy if isinstance(c, dict) and isinstance(c.get("category"), str)}


def classification_errors(obj):
    """Every ranking and filter error in a complete classification: its
    taxonomy, and its plan checked against that taxonomy."""
    taxonomy = obj.get("taxonomy", [])
    errors = taxonomy_errors(taxonomy)
    if not errors and not taxonomy:
        errors.append("taxonomy is empty")
    names = category_names(taxonomy) if isinstance(taxonomy, list) else set()
    return errors + plan_errors(obj.get("query_plan", []), names)


def priority_rank(entry):
    """Sort key: high before medium before low."""
    p = entry.get("priority") if isinstance(entry, dict) else None
    return PRIORITIES.index(p) if p in PRIORITIES else len(PRIORITIES)
