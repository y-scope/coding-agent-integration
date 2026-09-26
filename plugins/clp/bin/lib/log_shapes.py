"""log_shapes - the character limit, the field rules and the hashing shared by
the log-shape-* helpers.

A log shape (message template) can be hundreds of kilobytes long: CockroachDB logs
multi-line Pebble stats tables as single messages. Nothing downstream needs the
text itself, only a stable way to recognize the template again, so the cache
and the classification files identify each template by two hashes:

  hash         sha256 of the full template, which identifies it exactly
  prefix_hash  sha256 of its first max_chars characters, which is what
               log-shape-cluster embeds and what the cache fingerprint is built
               from; templates that differ only past the limit share it

The full text stays in the archive's own dictionary dump (the bootstrap's log
shapes file); whoever needs it joins on `hash`. The cache database stores, per
analyzed archive, each template's hash, count, length and first max_chars
characters (log-shape-cache ingest), which is all the fingerprint and a report
read.

The applied FIELD RULES are part of the classification too: a ruled template
takes its category from its field's rule and is never shown to the classifier,
so a changed rule set is a changed classification. The cache fingerprint
therefore folds in field_rules_digest() -- canonical over the rules, so only a
change that changes the outcome changes the key (see that function).

Stdlib only, like the other log-shape-* helpers.
"""

import hashlib
import os

# Templates are capped at this many characters before embedding and
# fingerprinting. 500 keeps embedded texts under the embedding model's 512-token
# context (see log-shape-cluster.py). Changing it deliberately re-keys the cache.
DEFAULT_MAX_CHARS = 500


def default_max_chars():
    try:
        value = int(os.environ.get("CLP_LOG_SHAPE_MAX_CHARS", DEFAULT_MAX_CHARS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_CHARS
    return value if value > 0 else DEFAULT_MAX_CHARS


def truncate_chars(text, max_chars):
    """Cap text at max_chars characters (code points), so the result is always
    valid UTF-8."""
    return text if len(text) <= max_chars else text[:max_chars]


def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def template_hash(text):
    return sha256_hex(text)


def prefix_hash(text, max_chars):
    return sha256_hex(truncate_chars(text, max_chars))


def app_key(log_shapes, max_chars, rules_digest):
    """The cache fingerprint: sha256 over the field rules digest and the sorted,
    de-duplicated set of templates, each capped at max_chars.

    rules_digest is field_rules_digest() of the rules that were (or will be)
    applied -- NO_FIELD_RULES_DIGEST when there are none. It is part of the key
    because it is part of the classification: with a rule, a field's templates
    take its category without ever reaching the classifier, so the same
    templates under a different rule set are a different classification.
    """
    h = hashlib.sha256()
    h.update(rules_digest.encode("utf-8"))
    h.update(b"\n")
    for text in sorted({truncate_chars(lt, max_chars) for lt in log_shapes}):
        h.update(text.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


# --- Field rules --------------------------------------------------------------
#
# A field rule is {"field": "<kql path>", "category": "<taxonomy category>"}.
# `log-shape-cluster fields --propose-rules` writes each one with the ratio and
# counts it was derived from ("proposed_by", "matched", "ratio", "templates",
# "values"); those annotations say WHY the rule exists and change nothing about
# what it does, so the digest below ignores them, and so does everything else
# that reads a rule.

_RULE_FIELDS = ("field", "category")

# Folded into every digest, so an empty rule set hashes to a digest of its own
# ("no rules" is a rule set, and a distinct one) instead of the digest of
# nothing, and so a later change of this scheme re-keys the cache on purpose.
_DIGEST_TAG = b"clp.field_rules.v1\n"


def normalize_field_rules(rules):
    """[(field, category)] sorted by field: all of a rule set that the
    classification depends on, in a canonical order. Whitespace around either
    value is stripped, so re-indenting or re-ordering the rules file is not a
    change."""
    pairs = set()
    for rule in rules or ():
        if not isinstance(rule, dict):
            continue
        field, category = (rule.get(k) for k in _RULE_FIELDS)
        pairs.add(((field or "").strip() if isinstance(field, str) else "",
                   (category or "").strip() if isinstance(category, str) else ""))
    return sorted(pairs)


def field_rules_digest(rules):
    """sha256 over a rule set, canonically: order-independent (sorted by field
    path), formatting-independent (only `field` and `category`, stripped), and
    stable for an empty or absent rule set, whose digest is distinct from every
    non-empty one."""
    h = hashlib.sha256()
    h.update(_DIGEST_TAG)
    for field, category in normalize_field_rules(rules):
        h.update(field.encode("utf-8"))
        h.update(b"\x00")
        h.update(category.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


NO_FIELD_RULES_DIGEST = field_rules_digest([])


def parse_field_rules(doc):
    """(rules, problems) from a parsed {"field_rules": [...]} document.

    `rules` is [{"field", "category"}] in the document's own order, with the
    category stripped and the annotations dropped; it is None when the document
    has no rule list at all. `problems` names every malformed rule, so the
    caller reports them all at once and refuses the file. log-shape-cluster and
    log-shape-cache both read rules through here, so neither can digest a rule
    set the other would read differently."""
    rules = doc.get("field_rules") if isinstance(doc, dict) else None
    if not isinstance(rules, list):
        return None, ['no "field_rules" list']
    problems = []
    seen = set()
    for n, rule in enumerate(rules, 1):
        field, category = (rule.get(k) if isinstance(rule, dict) else None for k in _RULE_FIELDS)
        if not isinstance(field, str) or not field:
            problems.append(f'field rule #{n} has no "field"')
        elif field in seen:
            problems.append(f"field {field!r} has more than one rule")
        elif not isinstance(category, str) or not category.strip():
            problems.append(f'field rule #{n} ({field}) has no "category"')
        if isinstance(field, str):
            seen.add(field)
    if problems:
        return None, problems
    return [{"field": r["field"], "category": r["category"].strip()} for r in rules], []
