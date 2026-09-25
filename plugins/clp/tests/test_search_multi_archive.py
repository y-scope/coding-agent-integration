"""clp-s-search-kql over a directory that holds several archives.

A stub clp-s stands in for the binary: it answers each search with one JSON row per
matching record, so the tests exercise only what the wrapper does around it.
"""

import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest

WRAPPER = os.path.join(os.path.dirname(__file__), "..", "bin", "clp-s-search-kql")

STUB = textwrap.dedent("""\\
    #!/usr/bin/env bash
    # Args: s [options] DIR QUERY. Each archive dir holds a `rows` file with its record count.
    args=("$@"); limit=""; count=0; positional=()
    i=1
    while [[ $i -lt ${#args[@]} ]]; do
      case "${args[$i]}" in
        --limit) limit="${args[$((i+1))]}"; i=$((i+2));;
        --count) count=1; i=$((i+1));;
        --archive-id) echo "stub: --archive-id with an inner dir" >&2; exit 2;;
        --*) i=$((i+1));;
        *) positional+=("${args[$i]}"); i=$((i+1));;
      esac
    done
    dir="${positional[0]}"; rows="$(cat "$dir/rows")"; id="$(basename "$dir")"
    if [[ $count -eq 1 ]]; then
      echo "{\\"archive_id\\":\\"$id\\",\\"count\\":$rows}"; exit 0
    fi
    n=$rows; [[ -n "$limit" && $limit -lt $n ]] && n=$limit
    for ((k=0; k<n; k++)); do echo "{\\"archive\\":\\"$id\\",\\"n\\":$k}"; done
""")


class MultiArchiveSearch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stub = os.path.join(self.tmp.name, "clp-s")
        with open(self.stub, "w") as fh:
            fh.write(STUB)
        os.chmod(self.stub, os.stat(self.stub).st_mode | stat.S_IXUSR)
        self.root = os.path.join(self.tmp.name, "root")
        for name, rows in (("aaa", 3), ("bbb", 5)):
            path = os.path.join(self.root, name)
            os.makedirs(path)
            for f in ("header", "table_metadata"):
                open(os.path.join(path, f), "w").close()
            with open(os.path.join(path, "rows"), "w") as fh:
                fh.write(str(rows))
        # The sidecar the compress wrappers write; clp-s cannot open a root that holds it.
        with open(os.path.join(self.root, ".yscope-clp-archive.json"), "w") as fh:
            fh.write("{}")

    def run_wrapper(self, *args, target=None):
        env = dict(os.environ, CLP_S_BIN=self.stub)
        proc = subprocess.run([WRAPPER, *args, target or self.root, "*"],
                              capture_output=True, text=True, env=env)
        return proc.returncode, proc.stdout.splitlines(), proc.stderr

    def test_records_from_every_archive(self):
        code, out, _ = self.run_wrapper()
        self.assertEqual(code, 0)
        self.assertEqual(len(out), 8)

    def test_count_rows_carry_archive_id(self):
        code, out, _ = self.run_wrapper("--count")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(out), ['{"archive_id":"aaa","count":3}', '{"archive_id":"bbb","count":5}'])

    def test_limit_is_a_global_cap(self):
        for cap, expected in ((2, 2), (4, 4), (8, 8), (100, 8)):
            _, out, _ = self.run_wrapper("--limit", str(cap))
            self.assertEqual(len(out), expected, cap)

    def test_archive_id_picks_one(self):
        code, out, _ = self.run_wrapper("--count", "--archive-id", "bbb")
        self.assertEqual((code, out), (0, ['{"archive_id":"bbb","count":5}']))

    def test_unknown_archive_id_is_an_error(self):
        code, out, err = self.run_wrapper("--count", "--archive-id", "zzz")
        self.assertNotEqual(code, 0)
        self.assertIn("no archive with ID zzz", err)

    def test_with_archive_wraps_each_record_and_leaves_counts_alone(self):
        code, out, _ = self.run_wrapper("--with-archive", "--limit", "4")
        self.assertEqual(code, 0)
        self.assertEqual(len(out), 4)
        for line in out:
            row = json.loads(line)
            self.assertEqual(set(row), {"archive_id", "record"})
            self.assertEqual(row["record"]["archive"], row["archive_id"])
        code, out, _ = self.run_wrapper("--with-archive", "--count")
        self.assertEqual(sorted(out), ['{"archive_id":"aaa","count":3}', '{"archive_id":"bbb","count":5}'])

    def test_one_inner_archive_still_works(self):
        code, out, _ = self.run_wrapper("--count", target=os.path.join(self.root, "aaa"))
        self.assertEqual((code, out), (0, ['{"archive_id":"aaa","count":3}']))

    def test_no_archive_is_an_error(self):
        empty = os.path.join(self.tmp.name, "empty")
        os.makedirs(empty)
        code, _, err = self.run_wrapper(target=empty)
        self.assertNotEqual(code, 0)
        self.assertIn("could not find a clp-s archive directory", err)


if __name__ == "__main__":
    unittest.main()
