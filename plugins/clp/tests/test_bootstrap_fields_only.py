"""clp bootstrap --fields-only: what the records are made of, and nothing from the dictionary or the cache.

A stub clp-s stands in for the binary and logs every query it is asked, so the test can check that the
dictionary is never dumped and the cache directory is never created.
"""

import os
import stat
import subprocess
import tempfile
import textwrap
import unittest

BOOTSTRAP = [os.path.join(os.path.dirname(__file__), "..", "bin", "clp"), "bootstrap"]

STUB = textwrap.dedent("""\\
    #!/usr/bin/env bash
    # Logs its arguments, answers the record search with two records and every stats query with nothing.
    printf '%s\\\\n' "$*" >> "$STUB_LOG"
    last="${@: -1}"
    if [[ "$last" == "*" ]]; then
      echo '{"type":"user","uuid":"u1"}'
      echo '{"type":"assistant","uuid":"u2"}'
    fi
""")


class FieldsOnly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stub = os.path.join(self.tmp.name, "clp-s")
        with open(self.stub, "w") as fh:
            fh.write(STUB)
        os.chmod(self.stub, os.stat(self.stub).st_mode | stat.S_IXUSR)
        self.archive = os.path.join(self.tmp.name, "archives", "a1")
        os.makedirs(self.archive)
        for f in ("header", "table_metadata"):
            open(os.path.join(self.archive, f), "w").close()
        self.log = os.path.join(self.tmp.name, "queries.log")
        self.cache = os.path.join(self.tmp.name, "cache")

    def run_bootstrap(self, *args):
        env = dict(os.environ, CLP_S_BIN=self.stub, STUB_LOG=self.log)
        return subprocess.run([*BOOTSTRAP, "--heartbeat", "0", "--cache-dir", self.cache,
                               "--out-dir", os.path.join(self.tmp.name, "out"), *args,
                               os.path.dirname(self.archive)],
                              capture_output=True, text=True, env=env)

    def test_it_reads_the_records_and_leaves_the_dictionary_and_the_cache_alone(self):
        p = self.run_bootstrap("--fields-only")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("FIELDS_ONLY=1", p.stdout)
        self.assertIn("[bootstrap] 1/1 ", p.stdout)
        for key in ("CACHE_MODE=", "LOG_SHAPE_COUNT=", "FREQS=", "TO_CLASSIFY="):
            self.assertNotIn(key, p.stdout)
        with open(self.log) as fh:
            queries = fh.read()
        self.assertIn("stats.schema_tree", queries)
        self.assertNotIn("stats.log_shapes", queries)
        self.assertFalse(os.path.exists(self.cache), "the classification cache was touched")

    def test_it_refuses_the_options_that_only_make_sense_with_a_dictionary(self):
        for extra in (["--dump"], ["--field-rules", os.path.join(self.tmp.name, "rules.json")]):
            p = self.run_bootstrap("--fields-only", *extra)
            self.assertEqual(p.returncode, 2, extra)
            self.assertIn("--fields-only", p.stderr)


if __name__ == "__main__":
    unittest.main()
