"""The plugin's own Markdown is not hard-wrapped.

writing-guide/rules.md, "Wrapping", forbids hand-wrapping a Markdown file, an
analysis report, and a prompt embedded in a Markdown file -- and says why that
last one drifts back: "A writer imitates the shape of its instructions." These
files ARE the instructions. A report writer reading a guide hand-wrapped at
eighty columns learns to hand-wrap, whatever the guide says in words, so the
rule needs a check and not only a sentence.

The test is mechanical: outside fenced code an unwrapped Markdown paragraph
occupies exactly one line, because paragraphs are separated by blank lines. Two
consecutive top-level prose lines therefore mean one paragraph broken by hand.
Everything that legitimately runs several lines together -- list items, table
rows, headings, block quotes, HTML, indented continuations, YAML frontmatter,
code -- is excluded rather than guessed at.
"""

import glob
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PLUGIN = os.path.join(ROOT, "clp")

# Vendored verbatim under MIT and refreshed from upstream with curl, so its
# shape is not ours to fix; the plugin's own preamble at the top of it is, and
# that is checked with the rest.
VENDORED = {"humanizer.md"}

# A line that starts a construct which legitimately spans several lines.
STRUCTURE = re.compile(r"^([-*+>|#]|\d+[.)]|<|\[|:|=)")


def prose_runs(path):
    """Runs of >=2 consecutive top-level prose lines, as (line_no, text)."""
    runs, run, fence, frontmatter = [], [], False, False
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    for number, line in enumerate(lines, 1):
        stripped = line.lstrip()
        # YAML frontmatter: --- on line 1 opens it, the next --- closes it.
        if number == 1 and line.rstrip() == "---":
            frontmatter = True
            continue
        if frontmatter:
            if line.rstrip() in ("---", "..."):
                frontmatter = False
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = not fence
            run = []
            continue
        if fence:
            continue
        plain = bool(line) and not line[0].isspace() and not STRUCTURE.match(line)
        if plain:
            run.append((number, line))
            continue
        if len(run) >= 2:
            runs.append(run)
        run = []
    if len(run) >= 2:
        runs.append(run)
    return runs


def markdown_files():
    patterns = (
        os.path.join(PLUGIN, "skills-claude", "**", "*.md"),
        os.path.join(PLUGIN, "writing-guide", "*.md"),
    )
    found = []
    for pattern in patterns:
        found.extend(glob.glob(pattern, recursive=True))
    return sorted(f for f in found if os.path.basename(f) not in VENDORED)


class MarkdownIsNotHardWrapped(unittest.TestCase):
    def test_there_are_files_to_check(self):
        """A glob that matches nothing would pass every assertion below."""
        self.assertGreater(len(markdown_files()), 10)

    def test_no_paragraph_is_broken_by_hand(self):
        offenders = []
        for path in markdown_files():
            for run in prose_runs(path):
                first, text = run[0]
                offenders.append(
                    f"{os.path.relpath(path, ROOT)}:{first}: paragraph spans"
                    f" {len(run)} lines -- {text[:70]}")
        self.assertEqual(offenders, [], "hard-wrapped paragraphs:\n" + "\n".join(offenders))


class DetectorItself(unittest.TestCase):
    """The check is worth nothing if it cannot see a wrapped paragraph."""

    def write(self, body):
        import tempfile
        handle = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                            encoding="utf-8")
        handle.write(body)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_catches_a_wrapped_paragraph(self):
        path = self.write("One sentence broken\nacross two source lines.\n")
        self.assertEqual(len(prose_runs(path)), 1)

    def test_passes_one_line_paragraphs(self):
        path = self.write("First paragraph on one line.\n\nSecond one too.\n")
        self.assertEqual(prose_runs(path), [])

    def test_ignores_fenced_code(self):
        path = self.write("Text.\n\n```\nfoo bar\nbaz qux\n```\n")
        self.assertEqual(prose_runs(path), [])

    def test_ignores_tables_lists_and_headings(self):
        path = self.write("| a | b |\n| - | - |\n| 1 | 2 |\n\n- one\n- two\n\n# H\n## H2\n")
        self.assertEqual(prose_runs(path), [])

    def test_ignores_yaml_frontmatter(self):
        path = self.write("---\nname: x\ndescription: y\n---\n\nBody on one line.\n")
        self.assertEqual(prose_runs(path), [])

    def test_ignores_indented_continuation(self):
        path = self.write("- item one\n  continued under the item\n  and again\n")
        self.assertEqual(prose_runs(path), [])


if __name__ == "__main__":
    unittest.main()
