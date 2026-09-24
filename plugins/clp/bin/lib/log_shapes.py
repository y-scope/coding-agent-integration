"""log_shapes - the character limit and hashing shared by the log-shape-* helpers.

A log shape (message template) can be hundreds of kilobytes long: CockroachDB logs
multi-line Pebble stats tables as single messages. Nothing downstream needs the
text itself, only a stable way to recognize the template again, so the cache
and the classification files identify each template by two hashes:

  hash         sha256 of the full template, which identifies it exactly
  prefix_hash  sha256 of its first max_chars characters, which is what
               log-shape-cluster embeds and what the cache fingerprint is built
               from; templates that differ only past the limit share it

The full text stays in the archive's own dictionary dump (the bootstrap's
log shapes and freqs files); whoever needs it joins on `hash`.

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


def app_key(log_shapes, max_chars):
    """The cache fingerprint: sha256 over the sorted, de-duplicated set of
    templates, each capped at max_chars."""
    h = hashlib.sha256()
    for text in sorted({truncate_chars(lt, max_chars) for lt in log_shapes}):
        h.update(text.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()
