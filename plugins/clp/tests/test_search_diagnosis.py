"""What clp-s-search-kql says when a query fails, matches nothing, or hits a bad binary.

Three defects are covered, each of which used to leave the reader with no signal:

  P1  an inadequate clp-s produced its own confusing error ("Aggregations are only
      supported with the reducer output handler") and nothing said the binary was
      the cause. A stub stands in for a real old build, so the test needs no
      particular clp-s on the machine.
  P2  a filter written at an Object path can never match, so `message:*` returned
      zero on an archive where every record has a `message`. The explanation is a
      pure function of the archive's schema tree, so it is tested directly.
  P4  `--count` printed nothing at all when nothing matched, leaving a caller to
      read silence as a number.
"""

import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

BIN = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "bin")
WRAPPER = os.path.join(BIN, "clp-s-search-kql")
sys.path.insert(0, os.path.join(BIN, "lib"))

from explain_zero import explain, query_fields  # noqa: E402

# Accepts --count (as the real old builds do, which is why probing --help cannot
# tell) and then refuses it, exactly like a clp-s that predates standalone counts.
NO_COUNT_STUB = textwrap.dedent("""\
    #!/usr/bin/env bash
    if [[ " $* " == *" --help "* ]]; then echo "  --count   Count matching records"; exit 0; fi
    echo "[error] Aggregations are only supported with the reducer output handler." >&2
    exit 1
""")

# Counts fine, so the wrapper must never blame it.
WORKING_STUB = textwrap.dedent("""\
    #!/usr/bin/env bash
    args=("$@"); count=0; positional=()
    i=1
    while [[ $i -lt ${#args[@]} ]]; do
      case "${args[$i]}" in
        --count) count=1; i=$((i+1));;
        --*) i=$((i+1));;
        *) positional+=("${args[$i]}"); i=$((i+1));;
      esac
    done
    dir="${positional[0]}"; query="${positional[1]:-}"
    if [[ "$query" == stats.schema_tree ]]; then exit 0; fi
    if [[ $count -eq 1 ]]; then
      rows="$(cat "$dir/rows" 2>/dev/null || echo 0)"
      # Like clp-s: no row at all for an archive nothing matched.
      [[ "$rows" == 0 ]] || echo "{\\"archive_id\\":\\"$(basename "$dir")\\",\\"count\\":$rows}"
      exit 0
    fi
    exit 0
""")


def executable(path, body):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class ArchiveCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.archive = os.path.join(self.tmp.name, "aaa")
        os.makedirs(self.archive)
        for name in ("header", "table_metadata"):   # what the wrapper checks for
            open(os.path.join(self.archive, name), "w").close()

    def rows(self, n):
        with open(os.path.join(self.archive, "rows"), "w", encoding="utf-8") as fh:
            fh.write(str(n))

    def run_wrapper(self, clp_s, *args):
        env = dict(os.environ, CLP_S_BIN=clp_s)
        return subprocess.run([WRAPPER, *args], capture_output=True, text=True, env=env)


class InadequateBinaryIsNamed(ArchiveCase):
    """P1: the failure must say which binary ran and how to change it."""

    def test_a_binary_that_cannot_count_is_identified_as_the_cause(self):
        stub = executable(os.path.join(self.tmp.name, "clp-s-old"), NO_COUNT_STUB)
        p = self.run_wrapper(stub, "--count", self.archive, "a:*")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("the binary is the problem, not the query", p.stderr)
        self.assertIn(stub, p.stderr)                      # which binary ran
        self.assertIn("CLP_S_BIN", p.stderr)               # how to point elsewhere
        self.assertIn("reducer output handler", p.stderr)  # what it is missing

    def test_a_working_binary_is_never_blamed(self):
        """A zero, or any ordinary result, must not produce a binary diagnosis."""
        stub = executable(os.path.join(self.tmp.name, "clp-s"), WORKING_STUB)
        self.rows(0)
        p = self.run_wrapper(stub, "--count", self.archive, "a:*")
        self.assertEqual(p.returncode, 0)
        self.assertNotIn("the binary is the problem", p.stderr)


class ZeroIsExplicit(ArchiveCase):
    """P4: a zero is a row, in the same shape as any other count."""

    def test_no_matches_prints_an_explicit_zero(self):
        stub = executable(os.path.join(self.tmp.name, "clp-s"), WORKING_STUB)
        self.rows(0)
        p = self.run_wrapper(stub, "--count", self.archive, "a:*")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), '{"archive_id":"aaa","count":0}')

    def test_a_real_count_is_untouched(self):
        stub = executable(os.path.join(self.tmp.name, "clp-s"), WORKING_STUB)
        self.rows(7)
        p = self.run_wrapper(stub, "--count", self.archive, "a:*")
        self.assertEqual(p.stdout.strip(), '{"archive_id":"aaa","count":7}')


# One archive, in the shape stats.schema_tree emits: an unkeyed record root whose
# parent_id is -1, `message` an Object with queryable leaves, `id` a scalar.
TREE = [
    '{"archive_id":"aaa","nodes":['
    '{"id":0,"parent_id":-1,"key":"","type":5,"count":10,"children":[1,2]},'
    '{"id":1,"parent_id":0,"key":"id","type":3,"count":10,"children":[]},'
    '{"id":2,"parent_id":0,"key":"message","type":5,"count":10,"children":[3,4]},'
    '{"id":3,"parent_id":2,"key":"role","type":3,"count":10,"children":[]},'
    '{"id":4,"parent_id":2,"key":"model","type":3,"count":10,"children":[]}'
    ']}'
]


class StructureExplainsTheZero(unittest.TestCase):
    """P2: the three answers, all derived from the archive rather than a field list."""

    def paths(self):
        from explain_zero import archive_paths
        return archive_paths(TREE)

    def test_a_structural_path_names_a_leaf_to_filter_instead(self):
        lines = "\n".join(explain("message:*", self.paths()))
        self.assertIn("holds it as Object", lines)
        self.assertIn("`message.role`", lines)

    def test_an_absent_path_is_called_a_typo_and_gets_a_suggestion(self):
        lines = "\n".join(explain("mesage.role:*", self.paths()))
        self.assertIn("is not a path in this archive", lines)
        self.assertIn("`message.role`", lines)

    def test_a_scalar_path_that_simply_matched_nothing_says_nothing(self):
        """The zero is then a fact about the data, and inventing a cause would be
        the bug this whole change is about."""
        self.assertEqual(explain('message.role:"nobody"', self.paths()), [])
        self.assertEqual(explain("id:*", self.paths()), [])

    def test_operators_and_functions_are_not_read_as_fields(self):
        self.assertEqual(query_fields('id:"a" AND NOT message.role:"b"'), ["id", "message.role"])

    def test_no_tree_means_no_claim(self):
        """With no schema tree (an old binary, or a dump that failed) nothing is
        asserted, rather than every path being called a typo."""
        self.assertEqual(explain("message:*", {}), [])


if __name__ == "__main__":
    unittest.main()
