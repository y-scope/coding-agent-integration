"""
kql_build - render structured filters into clp-s KQL.

Query plans used to carry hand-written KQL strings, and a model writing KQL
by hand keeps making the same two mistakes: an unquoted wildcard value with a
space (`message:*SQL txn*`, which clp-s reads as natural language and turns
into a semantic query) and a mixed AND/OR without parentheses (clp-s reads
`a OR b AND c` as `(a OR b) AND c`). A filter written as JSON and rendered
here cannot make either: every string value is quoted and escaped, and every
nested group is parenthesized.

Filter grammar (one JSON object per node):

  {"all": [F, ...]}                    every child matches      a AND b
  {"any": [F, ...]}                    at least one matches     a OR b
  {"not": F}                           F does not match         NOT a
  {"field": "f", "eq": V}              exact value              f:"V"  (numbers
                                                                and booleans
                                                                unquoted)
  {"field": "f", "contains": "s"}      substring                f:"*s*"
  {"field": "f", "contains": ["a","b"]}
                                       substrings, in order     f:"*a*b*"
  {"field": "f", "prefix": "s"}        starts with              f:"s*"
  {"field": "f", "exists": true}       has any value            f:*
  {"field": "f", "gt": N}              numeric comparison       f > N
                                       (also "gte", "lt", "lte")
  {"semantic": "text"}                 semantic search          semantic("text")

A semantic node must be scoped: some enclosing "all" must also hold a child
without any semantic node, so the semantic match is always ANDed with a
concrete filter. It may not appear under "not".

Stdlib only, like the other logtype-* helpers.
"""

import re

LEAF_OPS = ("eq", "contains", "prefix", "exists", "gt", "gte", "lt", "lte")
COMPARISONS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
METHODS = ("count", "project+grep", "project+jq", "semantic")

# Characters that end or restructure an unquoted key. Keys come from the
# discovered schema (dotted paths such as attr.durationMillis), so reject
# these rather than guess at an escaping the key never needed.
_BAD_FIELD_CHARS = set(' \t\n\r"\\*:()<>{}')

# Escapes inside a quoted value: `\`, `"`, and the wildcards `*`/`?` must be
# escaped to match literally; control characters use their escape sequences.
_VALUE_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "*": "\\*",
    "?": "\\?",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}

# field:*value* with an unquoted value. It runs on the query with its quoted
# strings masked (see _mask_quoted), so a `:*` inside a quoted value -- as in
# message:"*Error:*" -- is never taken for one. The value stops at a quote,
# colon or parenthesis, so `a:* AND b:*` (two exists filters) is not read as
# one value spanning the AND. Shared with kql-validate-wildcards.
_UNQUOTED_WILDCARD_RE = re.compile(r'(?<!["\']):\*([^*\n":()]*)\*')


class FilterError(ValueError):
    """A filter or plan entry that cannot be rendered."""


def _mask_quoted(kql):
    """kql with the contents of every double-quoted string (escapes included)
    replaced by "_", so positions are unchanged but nothing inside a quoted
    value can match. An unterminated quote masks to the end."""
    out, quoted, escaped = [], False, False
    for ch in kql:
        if quoted:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = False
                out.append(ch)
                continue
            out.append("_")
        else:
            quoted = ch == '"'
            out.append(ch)
    return "".join(out)


def unquoted_wildcard_terms(kql):
    """Return the unquoted wildcard values in kql that contain whitespace."""
    return [
        kql[m.start():m.end()]
        for m in _UNQUOTED_WILDCARD_RE.finditer(_mask_quoted(kql))
        if " " in m.group(1) or "\t" in m.group(1)
    ]


def escape_value(text):
    out = []
    for ch in text:
        if ch in _VALUE_ESCAPES:
            out.append(_VALUE_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def _escape_semantic(text):
    # Natural-language text, not a pattern: `*` and `?` stay literal.
    out = []
    for ch in text:
        if ch in _VALUE_ESCAPES and ch not in "*?":
            out.append(_VALUE_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def _field(node, path):
    field = node.get("field")
    if not isinstance(field, str) or not field:
        raise FilterError(f"{path}: 'field' must be a non-empty string")
    bad = sorted(set(field) & _BAD_FIELD_CHARS)
    if bad:
        raise FilterError(f"{path}: field {field!r} contains {''.join(bad)!r}")
    if field.startswith(".") or field.endswith(".") or ".." in field:
        raise FilterError(f"{path}: field {field!r} is not a dotted path")
    return field


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _text(value, path, what):
    if not isinstance(value, str) or not value:
        raise FilterError(f"{path}: {what} must be a non-empty string")
    return value


def _kind(node, path):
    if not isinstance(node, dict):
        raise FilterError(f"{path}: expected a filter object, got {type(node).__name__}")
    kinds = [k for k in ("all", "any", "not", "semantic") if k in node]
    if "field" in node:
        kinds.append("leaf")
    if len(kinds) != 1:
        raise FilterError(
            f"{path}: a filter node needs exactly one of all, any, not, semantic, "
            f"or field (got {', '.join(sorted(node)) or 'nothing'})"
        )
    kind = kinds[0]
    allowed = {"leaf": {"field", *LEAF_OPS}}.get(kind, {kind})
    extra = sorted(set(node) - allowed)
    if extra:
        raise FilterError(f"{path}: unexpected key(s) {', '.join(extra)} in a {kind} node")
    return kind


def _has_semantic(node):
    if not isinstance(node, dict):
        return False
    if "semantic" in node:
        return True
    if "not" in node:
        return _has_semantic(node["not"])
    for key in ("all", "any"):
        if isinstance(node.get(key), list):
            return any(_has_semantic(child) for child in node[key])
    return False


def _render_leaf(node, path):
    field = _field(node, path)
    ops = [op for op in LEAF_OPS if op in node]
    if len(ops) != 1:
        raise FilterError(f"{path}: a field node needs exactly one of {', '.join(LEAF_OPS)}")
    op = ops[0]
    value = node[op]
    if op == "eq":
        if isinstance(value, bool):
            return f"{field}:{'true' if value else 'false'}"
        if _is_number(value):
            return f"{field}:{value}"
        return f'{field}:"{escape_value(_text(value, path, "eq"))}"'
    if op == "contains":
        parts = [value] if isinstance(value, str) else value
        if not isinstance(parts, list) or not parts:
            raise FilterError(f"{path}: contains must be a string or a non-empty list of strings")
        for i, part in enumerate(parts):
            _text(part, f"{path}.contains[{i}]", "each contains fragment")
        return f'{field}:"*{"*".join(escape_value(p) for p in parts)}*"'
    if op == "prefix":
        return f'{field}:"{escape_value(_text(value, path, "prefix"))}*"'
    if op == "exists":
        if value is not True:
            raise FilterError(f"{path}: exists must be true (use not for absence)")
        return f"{field}:*"
    if not _is_number(value):
        raise FilterError(f"{path}: {op} needs a number")
    return f"{field} {COMPARISONS[op]} {value}"


def _render(node, path, top, scoped):
    kind = _kind(node, path)
    if kind == "leaf":
        return _render_leaf(node, path)
    if kind == "semantic":
        if not scoped:
            raise FilterError(
                f"{path}: semantic must be ANDed with a non-semantic filter "
                "(put it in an \"all\" beside one); unscoped semantic search is not allowed"
            )
        return f'semantic("{_escape_semantic(_text(node["semantic"], path, "semantic"))}")'
    if kind == "not":
        child = node["not"]
        if _has_semantic(child):
            raise FilterError(f"{path}: semantic is not allowed under not")
        inner = _render(child, f"{path}.not", False, scoped)
        if isinstance(child, dict) and "not" in child:
            inner = f"({inner})"
        return f"NOT {inner}"

    children = node[kind]
    if not isinstance(children, list) or not children:
        raise FilterError(f"{path}: {kind} must be a non-empty list of filters")
    plain = [not _has_semantic(child) for child in children]
    parts = []
    for i, child in enumerate(children):
        child_scoped = scoped
        if kind == "all":
            # Scoped when any sibling is a semantic-free filter.
            child_scoped = scoped or any(p for j, p in enumerate(plain) if j != i)
        parts.append(_render(child, f"{path}.{kind}[{i}]", False, child_scoped))
    if len(parts) == 1:
        return parts[0]
    joined = f" {'AND' if kind == 'all' else 'OR'} ".join(parts)
    return joined if top else f"({joined})"


def render(node):
    """Render a filter to KQL; raise FilterError when it is malformed."""
    return _render(node, "match", True, False)


def entry_kql(entry):
    """Render a query_plan entry's `match` filter; raise FilterError when the
    entry has no valid `match` (a hand-written `kql` string is not accepted)."""
    if not isinstance(entry, dict):
        raise FilterError(f"plan entry must be an object, got {type(entry).__name__}")
    if "kql" in entry:
        raise FilterError(
            "plan entry has a hand-written 'kql' string; write the filter as 'match' instead"
        )
    if "match" not in entry:
        raise FilterError("plan entry needs a 'match' filter")
    return render(entry["match"])


def check_entry(entry):
    """Validate one query_plan entry; return its KQL or raise FilterError."""
    kql = entry_kql(entry)
    method = entry.get("method")
    if method not in METHODS:
        raise FilterError(f"unknown method {method!r} (expected one of {', '.join(METHODS)})")
    if method == "semantic" and not _has_semantic(entry["match"]):
        raise FilterError("method is semantic but match has no semantic node")
    for key in ("label", "project", "grep", "jq"):
        if key in entry and not isinstance(entry[key], str):
            raise FilterError(f"{key!r} must be a string")
    return kql
