"""Tests for lib/session_turns.py. Run: python3 -m unittest discover -s plugins/clp/tests"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "bin", "lib"))

from session_turns import breakdown, collect, longest_waits, response_tokens  # noqa: E402

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def at(seconds):
    return (BASE + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def prompt(t, text, **extra):
    return {"type": "user", "timestamp": at(t), "message": {"role": "user", "content": text}, **extra}


def assistant(t, message_id, *blocks):
    return {"type": "assistant", "timestamp": at(t),
            "message": {"role": "assistant", "id": message_id, "model": "m", "content": list(blocks)}}


def thinking():
    return {"type": "thinking", "thinking": "..."}


def text():
    return {"type": "text", "text": "..."}


def tool_use(tool_id, name):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": {}}


def result(t, tool_id, is_error=False, duration_ms=None):
    record = {"type": "user", "timestamp": at(t), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": "ok", "is_error": is_error}]}}
    if duration_ms is not None:
        record["toolUseResult"] = {"durationMs": duration_ms}
    return record


class PromptsTest(unittest.TestCase):
    def test_injected_and_meta_records_are_not_prompts(self):
        records = [
            prompt(0, "hello"),
            prompt(1, "<task-notification> <task-id>x</task-id>"),
            prompt(2, "This session is being continued from a previous conversation"),
            prompt(3, "<local-command-stdout>x</local-command-stdout>"),
            prompt(4, "meta text", isMeta=True),
            prompt(5, "a summary", isCompactSummary=True),
            {"type": "user", "timestamp": at(6), "message": {"role": "user", "content": [
                {"type": "text", "text": "typed next to an image"}]}},
            {"type": "user", "timestamp": at(7), "message": {"role": "user", "content": [
                {"type": "text", "text": "Continue from where you left off."}]}},
        ]
        got = [text for _, text in collect(records)["prompts"]]
        self.assertEqual(got, ["hello", "typed next to an image"])

    def test_synthetic_assistant_messages_are_ignored(self):
        records = [prompt(0, "go"), assistant(5, "<synthetic>", text())]
        self.assertEqual(collect(records)["gens"], [])
        self.assertEqual(collect(records)["activity"], [datetime.fromisoformat(at(0).replace("Z", "+00:00"))])


class ToolPairingTest(unittest.TestCase):
    def test_result_is_paired_by_id_whatever_the_order(self):
        records = [result(40, "t1", is_error=True, duration_ms=25000),
                   assistant(10, "m1", tool_use("t1", "Bash")), prompt(0, "go")]
        tools = collect(records)["tools"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "Bash")
        self.assertTrue(tools[0]["is_error"])
        self.assertEqual(tools[0]["duration_ms"], 25000)
        [(tool, wall)] = longest_waits(collect(records), 1)
        self.assertEqual(wall, 30.0)

    def test_a_call_with_no_result_has_no_wait(self):
        session = collect([prompt(0, "go"), assistant(10, "m1", tool_use("t1", "Bash"))])
        self.assertEqual(longest_waits(session, 5), [])


class BreakdownTest(unittest.TestCase):
    def test_blocks_of_one_message_form_one_round(self):
        session = collect([prompt(10, "go"), assistant(11, "m1", thinking()), assistant(14, "m1", text())])
        [turn] = breakdown(session)
        self.assertEqual((turn["model_s"], turn["e2e_s"]), (4.0, 4.0))

    def test_human_wait_wins_over_an_overlapping_tool_and_idle_is_split_out(self):
        records = [
            prompt(0, "go"),
            assistant(2, "m1", text(), tool_use("ask", "AskUserQuestion")),
            assistant(50, "m2", tool_use("sh", "Bash")),
            result(80, "sh"),
            result(100, "ask"),
            prompt(700, "<task-notification>done</task-notification>"),
        ]
        [turn] = breakdown(collect(records), idle_seconds=600)
        self.assertEqual(turn["e2e_s"], 700.0)
        self.assertEqual(turn["model_s"], 2.0)
        self.assertEqual(turn["human_s"], 98.0)
        self.assertEqual(turn["tool_s"], 0.0)
        self.assertEqual(turn["idle_s"], 600.0)
        self.assertEqual(turn["other_s"], 0.0)
        self.assertEqual(turn["longest_wait"], ("AskUserQuestion", 98.0))
        [shorter_threshold] = breakdown(collect(records), idle_seconds=601)
        self.assertEqual((shorter_threshold["idle_s"], shorter_threshold["other_s"]), (0.0, 600.0))

    def test_the_parts_always_add_up_to_the_turn(self):
        records = [
            prompt(0, "go"), assistant(3, "m1", tool_use("a", "Bash"), tool_use("b", "Read")),
            result(9, "b"), result(20, "a"), assistant(25, "m2", text()),
            prompt(400, "next"), assistant(410, "m3", thinking()),
        ]
        for turn in breakdown(collect(records)):
            parts = turn["human_s"] + turn["tool_s"] + turn["model_s"] + turn["idle_s"] + turn["other_s"]
            self.assertAlmostEqual(parts, turn["e2e_s"])

    def test_a_turn_ends_at_its_last_event_not_at_the_next_prompt(self):
        records = [prompt(0, "one"), assistant(5, "m1", text()), prompt(100, "two"), assistant(110, "m2", text())]
        turns = breakdown(collect(records))
        self.assertEqual([t["e2e_s"] for t in turns], [5.0, 10.0])
        self.assertEqual([t["prompt"] for t in turns], ["one", "two"])

    def test_no_prompts_gives_no_turns(self):
        self.assertEqual(breakdown(collect([assistant(1, "m1", text())])), [])


if __name__ == "__main__":
    unittest.main()


class RefusesSeveralArchives(unittest.TestCase):
    """A bundle's archives/ holds the main log and the agent transcripts; reading it all as one session
    would count every agent's prompt as a human prompt."""

    def test_a_directory_of_several_archives_is_refused_with_the_way_out(self):
        import subprocess, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            archives = os.path.join(tmp, "bundle", "archives")
            for name in ("a1", "a2"):
                os.makedirs(os.path.join(archives, name))
                for f in ("header", "table_metadata"):
                    open(os.path.join(archives, name, f), "w").close()
            open(os.path.join(tmp, "bundle", "catalog.sqlite"), "w").close()
            script = os.path.join(os.path.dirname(__file__), "..", "bin", "clp-s-session-turns")
            p = subprocess.run([script, archives, "--search-wrapper", "/nonexistent"], capture_output=True, text=True)
            self.assertEqual(p.returncode, 2)
            self.assertIn("holds 2 archives", p.stderr)
            self.assertIn("select archive_id from archives where kind='main'", p.stderr)


def usage(record, inp, out, cache=True):
    record["message"]["usage"] = {"input_tokens": inp, "output_tokens": out}
    if cache:
        record["message"]["usage"].update(cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return record


class TokensTest(unittest.TestCase):
    """A response is written as several records sharing its message id: the first may carry a preliminary
    usage (output 0, no cache fields), the rest repeat the final one. It must count once, with the final."""

    def records(self):
        return [
            prompt(0, "go"),
            usage(assistant(1, "m1", thinking()), 900, 0, cache=False),       # preliminary
            usage(assistant(2, "m1", text()), 800, 40),
            usage(assistant(2, "m1", tool_use("t1", "Bash")), 800, 40),
            result(3, "t1"),
            usage(assistant(4, "m2", text()), 1000, 7),
            prompt(10, "next"),
            usage(assistant(11, "m3", text()), 50, 5),
        ]

    def test_each_response_counts_once_with_its_final_usage(self):
        self.assertEqual(response_tokens(self.records()), {"input": 1850, "output": 52, "cache_read": 0, "cache_write": 0})

    def test_order_does_not_matter(self):
        self.assertEqual(response_tokens(reversed(self.records())), response_tokens(self.records()))

    def test_tokens_per_turn(self):
        turns = breakdown(collect(reversed(self.records())))
        self.assertEqual([t["tokens"]["input"] for t in turns], [1800, 50])
        self.assertEqual([t["tokens"]["output"] for t in turns], [47, 5])
