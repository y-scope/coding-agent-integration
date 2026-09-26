"""The field rules in the classification cache key.

A ruled template takes its category from the rule and never reaches the
classifier, so the rules are part of the classification: these are the checks
that a changed rule set cannot reuse a classification built under the old one,
and that a rule set which only LOOKS different (re-ordered, re-indented, with or
without the annotations `--propose-rules` writes) still hits the cache.

Nothing here embeds anything: `log-shape-cluster cluster` needs no embedding
server when every template is ruled, which is what the digest-agreement test
uses.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

BIN = os.path.join(os.path.dirname(__file__), "..", "bin")
CACHE_BIN = os.path.join(BIN, "log-shape-cache")
CLUSTER_BIN = os.path.join(BIN, "log-shape-cluster.py")
sys.path.insert(0, os.path.join(BIN, "lib"))
from log_shapes import (  # noqa: E402
    NO_FIELD_RULES_DIGEST, app_key, field_rules_digest, normalize_field_rules, parse_field_rules)

# One rule as `log-shape-cluster fields --propose-rules` writes it: the field and
# the category, plus the ratio and counts it was derived from.
PROPOSED = [
    {"field": "message.content", "category": "free-text-message-content",
     "proposed_by": "free-text-ratio", "matched": "ratio", "ratio": 1.0,
     "templates": 3694, "values": 3694},
    {"field": "toolUseResult.lines", "category": "free-text-tool-use-result-lines",
     "proposed_by": "free-text-ratio", "matched": "templates", "ratio": 0.68,
     "templates": 5627, "values": 8242},
]
BARE = [{"field": r["field"], "category": r["category"]} for r in PROPOSED]

TEMPLATES = [f"worker <*> handled request {i}" for i in range(6)]


def taxonomy(*categories):
    return [{"category": c, "description": "d", "priority": "low", "why": "w"} for c in categories]


def classification(templates, rules):
    """A storable classification of `templates` under `rules`, all in one
    category, with the rules' categories ranked as `expand` requires."""
    import hashlib

    def sha(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    return {
        "schema": {"timestamp": "ts"},
        "taxonomy": taxonomy("ops", *(r["category"] for r in rules)),
        "templates": [{"hash": sha(t), "prefix_hash": sha(t[:500]), "category": "ops"}
                      for t in templates],
        "query_plan": [{"label": "Ops", "method": "count", "match": {"field": "ts", "exists": True},
                        "category": "ops", "priority": "high", "stage": "core"}],
        "max_chars": 500,
        "field_rules": rules,
    }


class Digest(unittest.TestCase):
    def test_order_and_annotations_do_not_change_it(self):
        self.assertEqual(field_rules_digest(PROPOSED), field_rules_digest(BARE))
        self.assertEqual(field_rules_digest(PROPOSED), field_rules_digest(list(reversed(BARE))))

    def test_whitespace_does_not_change_it(self):
        padded = [{"field": r["field"], "category": f"  {r['category']}\n"} for r in BARE]
        self.assertEqual(field_rules_digest(BARE), field_rules_digest(padded))

    def test_a_renamed_category_changes_it(self):
        renamed = [dict(r) for r in BARE]
        renamed[0]["category"] = "prose"
        self.assertNotEqual(field_rules_digest(BARE), field_rules_digest(renamed))

    def test_a_dropped_or_added_rule_changes_it(self):
        self.assertNotEqual(field_rules_digest(BARE), field_rules_digest(BARE[:1]))
        self.assertNotEqual(field_rules_digest(BARE),
                            field_rules_digest(BARE + [{"field": "x", "category": "y"}]))

    def test_no_rules_is_its_own_digest(self):
        # Stable, and the same however "none" is spelled -- but never a rule
        # set's digest, so dropping the rules re-keys the cache.
        self.assertEqual(NO_FIELD_RULES_DIGEST, field_rules_digest([]))
        self.assertEqual(NO_FIELD_RULES_DIGEST, field_rules_digest(None))
        self.assertNotEqual(NO_FIELD_RULES_DIGEST, field_rules_digest(BARE[:1]))
        self.assertNotEqual(NO_FIELD_RULES_DIGEST, field_rules_digest(BARE))

    def test_normalize_keeps_only_the_field_and_the_category(self):
        self.assertEqual(normalize_field_rules(PROPOSED),
                         [("message.content", "free-text-message-content"),
                          ("toolUseResult.lines", "free-text-tool-use-result-lines")])

    def test_parse_names_every_malformed_rule(self):
        rules, problems = parse_field_rules({"field_rules": [{"field": "a"}, {"category": "b"},
                                                             {"field": "a", "category": "c"}]})
        self.assertIsNone(rules)
        self.assertEqual(len(problems), 3)
        self.assertIsNone(parse_field_rules({})[0])


class KeyFoldsInTheRules(unittest.TestCase):
    def test_same_templates_different_rules_different_key(self):
        with_rules = app_key(TEMPLATES, 500, field_rules_digest(BARE))
        renamed = [dict(r) for r in BARE]
        renamed[0]["category"] = "prose"
        self.assertNotEqual(with_rules, app_key(TEMPLATES, 500, field_rules_digest(renamed)))
        self.assertNotEqual(with_rules, app_key(TEMPLATES, 500, NO_FIELD_RULES_DIGEST))
        self.assertEqual(with_rules, app_key(TEMPLATES, 500, field_rules_digest(PROPOSED)))

    def test_same_templates_same_rules_same_key(self):
        self.assertEqual(app_key(TEMPLATES, 500, field_rules_digest(BARE)),
                         app_key(reversed(TEMPLATES), 500, field_rules_digest(list(reversed(BARE)))))


class Probe(unittest.TestCase):
    """`log-shape-cache diff` against a stored entry, through the CLI."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = os.path.join(self.tmp.name, "cache")
        self.shapes = self.write_shapes("shapes.ndjson", TEMPLATES)
        self.rules = self.write_rules("rules.json", PROPOSED)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def write_shapes(self, name, templates):
        with open(self.path(name), "w", encoding="utf-8") as fh:
            for t in templates:
                fh.write(json.dumps({"log_shape": t}) + "\n")
        return self.path(name)

    def write_rules(self, name, rules):
        with open(self.path(name), "w", encoding="utf-8") as fh:
            json.dump({"field_rules": rules}, fh)
        return self.path(name)

    def cache_run(self, *args, stdin=None, check=True):
        p = subprocess.run([sys.executable, CACHE_BIN, *args], input=stdin, check=False,
                           capture_output=True, text=True)
        if check:
            self.assertEqual(p.returncode, 0, p.stderr)
        return p

    def key_for(self, shapes, rules=None):
        args = ["key", "--log-shapes-file", shapes]
        if rules:
            args += ["--field-rules", rules]
        return self.cache_run(*args).stdout.strip()

    def store(self, shapes, rules_file, rules, key=None):
        key = key or self.key_for(shapes, rules_file)
        with open(shapes, encoding="utf-8") as fh:
            templates = [json.loads(line)["log_shape"] for line in fh]
        self.cache_run("put", "--cache-dir", self.cache, "--key", key,
                       stdin=json.dumps(classification(templates, rules)))
        return key

    def probe(self, shapes, rules=None):
        """(mode, reason) of a diff header."""
        args = ["diff", "--cache-dir", self.cache, "--log-shapes-file", shapes]
        if rules:
            args += ["--field-rules", rules]
        header = self.cache_run(*args).stdout.splitlines()[0].split("\t")
        return header[0], header[-1]

    def test_unchanged_rules_are_up_to_date(self):
        self.store(self.shapes, self.rules, BARE)
        self.assertEqual(self.probe(self.shapes, self.rules), ("UPTODATE", "up-to-date"))

    def test_reordered_and_stripped_rules_are_still_up_to_date(self):
        self.store(self.shapes, self.rules, BARE)
        canonical = self.write_rules("canonical.json", list(reversed(BARE)))
        self.assertEqual(self.probe(self.shapes, canonical), ("UPTODATE", "up-to-date"))

    def test_a_renamed_category_is_new_and_says_the_rules_changed(self):
        self.store(self.shapes, self.rules, BARE)
        renamed = [dict(r) for r in BARE]
        renamed[0]["category"] = "prose"
        self.assertEqual(self.probe(self.shapes, self.write_rules("renamed.json", renamed)),
                         ("NEW", "rules-changed"))

    def test_dropping_the_rules_is_new_and_says_the_rules_changed(self):
        self.store(self.shapes, self.rules, BARE)
        self.assertEqual(self.probe(self.shapes), ("NEW", "rules-changed"))

    def test_an_unclassified_app_is_a_first_run(self):
        self.assertEqual(self.probe(self.shapes, self.rules), ("NEW", "first-run"))
        self.store(self.shapes, self.rules, BARE)
        other = self.write_shapes("other.ndjson", ["a wholly <*> different template"])
        self.assertEqual(self.probe(other, self.rules), ("NEW", "first-run"))

    def test_grown_templates_under_the_same_rules_are_growth(self):
        base = self.store(self.shapes, self.rules, BARE)
        grown = self.write_shapes("grown.ndjson", TEMPLATES + ["worker <*> retried request <*>"])
        args = ["diff", "--cache-dir", self.cache, "--log-shapes-file", grown,
                "--field-rules", self.rules]
        header = self.cache_run(*args).stdout.splitlines()[0].split("\t")
        self.assertEqual((header[0], header[-1]), ("GROWTH", "templates-grown"))
        self.assertEqual(header[2], base)

    def test_grown_templates_under_other_rules_are_not_growth(self):
        self.store(self.shapes, self.rules, BARE)
        grown = self.write_shapes("grown.ndjson", TEMPLATES + ["worker <*> retried request <*>"])
        self.assertEqual(self.probe(grown), ("NEW", "rules-changed"))

    def test_an_entry_stored_under_a_rule_blind_key_is_still_judged_by_its_rules(self):
        """The bootstrap probes before the rules are derived, so a classification
        can be stored under a key computed without them. It is still this run's
        classification when the rules match -- found through the prefix-set
        fallback -- and still not this run's when they do not: the digest is
        checked, not just folded into the key."""
        blind = self.key_for(self.shapes)
        self.store(self.shapes, None, BARE, key=blind)
        self.assertEqual(self.probe(self.shapes, self.rules), ("UPTODATE", "up-to-date"))
        self.assertEqual(self.probe(self.shapes), ("NEW", "rules-changed"))

    def test_the_entry_carries_the_rules_and_their_digest(self):
        key = self.store(self.shapes, self.rules, BARE)
        entry = json.loads(self.cache_run("get", "--cache-dir", self.cache, key).stdout)
        self.assertEqual(entry["field_rules"], BARE)
        self.assertEqual(entry["rules_digest"], field_rules_digest(BARE))

    def test_a_malformed_rules_file_is_refused(self):
        bad = self.write_rules("bad.json", [{"field": "a"}])
        p = self.cache_run("diff", "--cache-dir", self.cache, "--log-shapes-file", self.shapes,
                           "--field-rules", bad, check=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn('has no "category"', p.stderr)


class ClusterAgreesWithTheCache(unittest.TestCase):
    """The two tools must digest the same rules file identically, or a
    classification is stored under a key the next probe cannot find.

    `cluster` needs no embedding server here: every template is ruled, so
    nothing is left to embed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def digests_agree(self, rules):
        import hashlib

        rules_file = self.path("rules.json")
        with open(rules_file, "w", encoding="utf-8") as fh:
            json.dump({"field_rules": rules}, fh, indent=1)
        template = "worker <*> handled a request"
        with open(self.path("shapes.ndjson"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"log_shape": template}) + "\n")
        with open(self.path("fields.ndjson"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"hash": hashlib.sha256(template.encode()).hexdigest(),
                                 "fields": {rules[0]["field"]: 7}}) + "\n")
        cluster = subprocess.run(
            [sys.executable, CLUSTER_BIN, "cluster", "--input", self.path("shapes.ndjson"),
             "--template-fields", self.path("fields.ndjson"), "--field-rules", rules_file,
             "--output", self.path("clusters.json")],
            check=False, capture_output=True, text=True)
        self.assertEqual(cluster.returncode, 0, cluster.stderr)
        printed = dict(line.split("=", 1) for line in cluster.stdout.splitlines() if "=" in line)
        self.assertEqual(printed["FIELD_RULED"], "1", cluster.stdout)
        cache = subprocess.run([sys.executable, CACHE_BIN, "rules-digest", "--field-rules", rules_file],
                               check=False, capture_output=True, text=True)
        self.assertEqual(cache.returncode, 0, cache.stderr)
        self.assertEqual(printed["RULES_DIGEST"], cache.stdout.strip())
        with open(self.path("clusters.json"), encoding="utf-8") as fh:
            self.assertEqual(printed["RULES_DIGEST"], json.load(fh)["rules_digest"])
        return printed["RULES_DIGEST"]

    def test_the_same_rules_file_digests_the_same_in_both(self):
        self.assertEqual(self.digests_agree(PROPOSED), field_rules_digest(BARE))

    def test_no_rules_digests_the_same_in_both(self):
        p = subprocess.run([sys.executable, CACHE_BIN, "rules-digest"],
                           check=False, capture_output=True, text=True)
        self.assertEqual(p.stdout.strip(), NO_FIELD_RULES_DIGEST)


class OldFormatDiscard(unittest.TestCase):
    """A cache written before the rules were part of the key holds entries whose
    rules nothing records. They are discarded once, not migrated."""

    OLD_SCHEMA = """
    CREATE TABLE entries (
        app_key TEXT PRIMARY KEY, max_chars INTEGER NOT NULL, schema TEXT,
        taxonomy TEXT NOT NULL, query_plan TEXT NOT NULL, classified_at TEXT NOT NULL,
        plan_updated_at TEXT, grown_from TEXT);
    CREATE TABLE templates (
        app_key TEXT NOT NULL REFERENCES entries(app_key) ON DELETE CASCADE,
        hash TEXT NOT NULL, prefix_hash TEXT NOT NULL, category TEXT NOT NULL,
        PRIMARY KEY (app_key, hash)) WITHOUT ROWID;
    CREATE TABLE archives (
        archive_id TEXT PRIMARY KEY, max_chars INTEGER NOT NULL, has_counts INTEGER NOT NULL,
        ingested_at TEXT NOT NULL, used_at TEXT NOT NULL);
    CREATE TABLE archive_shapes (
        archive_id TEXT NOT NULL REFERENCES archives(archive_id) ON DELETE CASCADE,
        hash TEXT NOT NULL, prefix TEXT NOT NULL, length INTEGER NOT NULL, count INTEGER,
        PRIMARY KEY (archive_id, hash));
    """

    def setUp(self):
        import sqlite3

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = os.path.join(self.tmp.name, "cache")
        os.makedirs(self.cache)
        self.db_path = os.path.join(self.cache, "cache.sqlite")
        self.shapes = os.path.join(self.tmp.name, "shapes.ndjson")
        with open(self.shapes, "w", encoding="utf-8") as fh:
            for t in TEMPLATES:
                fh.write(json.dumps({"log_shape": t}) + "\n")

        old = classification(TEMPLATES, [])
        db = sqlite3.connect(self.db_path)
        db.executescript(self.OLD_SCHEMA)
        key = "a" * 64
        db.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?)",
                   (key, 500, json.dumps(old["schema"]), json.dumps(old["taxonomy"]),
                    json.dumps(old["query_plan"]), "2026-09-01T00:00:00Z", None, None))
        db.executemany("INSERT INTO templates VALUES (?,?,?,?)",
                       [(key, t["hash"], t["prefix_hash"], t["category"]) for t in old["templates"]])
        # A stored archive: it survives the discard, since an immutable archive's
        # templates are not affected by how classifications are keyed.
        db.execute("INSERT INTO archives VALUES ('arch1',500,1,'2026-09-01T00:00:00Z',"
                   "'2026-09-01T00:00:00Z')")
        db.commit()
        db.close()

    def diff(self):
        return subprocess.run([sys.executable, CACHE_BIN, "diff", "--cache-dir", self.cache,
                               "--log-shapes-file", self.shapes],
                              check=False, capture_output=True, text=True)

    def test_discarded_once_reported_and_the_database_stays_usable(self):
        import sqlite3

        first = self.diff()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("discarded 1 classification", first.stderr)
        self.assertIn(self.db_path, first.stderr)           # says which database
        self.assertIn("next run", first.stderr)
        snapshots = [n for n in os.listdir(self.cache) if ".format1." in n]
        self.assertEqual(len(snapshots), 1, os.listdir(self.cache))   # kept aside, not unlinked
        self.assertIn("discarded", first.stderr)
        self.assertEqual(first.stdout.splitlines()[0].split("\t")[0], "NEW")

        # Usable at once, with the stored archive intact and nothing to report twice.
        db = sqlite3.connect(self.db_path)
        self.addCleanup(db.close)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM archives").fetchone()[0], 1)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM entries").fetchone()[0], 0)

        second = self.diff()
        self.assertNotIn("discarded", second.stderr)
        mode, reason = (second.stdout.splitlines()[0].split("\t")[i] for i in (0, -1))
        self.assertEqual((mode, reason), ("NEW", "first-run"))

        # And a fresh classification is reused on the run after it.
        key = subprocess.run([sys.executable, CACHE_BIN, "key", "--log-shapes-file", self.shapes],
                             check=True, capture_output=True, text=True).stdout.strip()
        subprocess.run([sys.executable, CACHE_BIN, "put", "--cache-dir", self.cache, "--key", key],
                       input=json.dumps(classification(TEMPLATES, [])), check=True,
                       capture_output=True, text=True)
        third = self.diff()
        self.assertEqual(third.stdout.splitlines()[0].split("\t")[0], "UPTODATE")
        self.assertEqual(third.stdout.splitlines()[0].split("\t")[-1], "up-to-date")

    def test_a_snapshot_is_only_kept_when_something_was_dropped(self):
        import sqlite3

        db = sqlite3.connect(self.db_path)
        db.execute("DELETE FROM templates")
        db.execute("DELETE FROM entries")
        db.commit()
        db.close()
        p = self.diff()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("discarded", p.stderr)
        self.assertEqual([n for n in os.listdir(self.cache) if ".format1." in n], [])


if __name__ == "__main__":
    unittest.main()
