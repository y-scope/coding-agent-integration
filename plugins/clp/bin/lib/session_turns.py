"""session_turns - the per-turn time breakdown of a Claude Code session.

A turn runs from one human prompt to the next. Its wall-clock time is split into
what the session was waiting on, with no second counted twice:

  human wait  a tool that blocks on the person (AskUserQuestion, ExitPlanMode)
  tool        any other tool call, from the record that emits it to the record
              that returns its result
  model       the model generating: from the latest input before a round's first
              output to the round's last output
  idle        a stretch with no event at all that lasts at least idle_seconds
  other       the rest: short gaps between events

Each turn also gets the tokens its model responses used, as the API reported them (input, output,
cache read, cache write). A response is written as several records (one per content block) that share
its message id and repeat its usage, and the first may carry a preliminary figure (output 0, no cache
fields), so a response is counted once, with its final usage: the one with the most output that carries
the cache fields. That choice needs no record order, so it holds for search results too.

Where two of these overlap (parallel tool calls, a tool running while the model
streams), the time goes to the first in the order above.

The record model follows TraceLab's Claude Code extractor
(https://github.com/uw-syfi/TraceLab, scripts/extract_claude_rounds.py, Apache-2.0):
the assistant records that share a message id form one round, and a tool call is
paired with its result by tool_use_id. What it adds is a filter for user records
that the harness injects (task notifications, command output, compaction summaries),
which are not human prompts, and the idle category.

Stdlib only, like the other plugin helpers.
"""

from datetime import datetime

HUMAN_WAIT_TOOLS = ("AskUserQuestion", "ExitPlanMode")

# User records that begin with one of these are written by the harness, not typed.
INJECTED_PREFIXES = (
    "<task-notification",
    "<local-command",
    "<command-",
    "Caveat:",
    "This session is being continued",
    "[Request interrupted",
    "Continue from where you left off",
)

IDLE_SECONDS = 600

# (name used here, field of message.usage)
TOKEN_FIELDS = (("input", "input_tokens"), ("output", "output_tokens"),
                ("cache_read", "cache_read_input_tokens"), ("cache_write", "cache_creation_input_tokens"))


def usage_of(message):
    """{name: tokens} from an assistant message's usage, or None when it has none. "final" ranks it
    among the records of one response (see final_usage)."""
    usage = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(usage, dict):
        return None
    out = {"final": (usage.get("output_tokens") or 0, usage.get("cache_read_input_tokens") is not None)}
    for name, field in TOKEN_FIELDS:
        value = usage.get(field)
        out[name] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
    return out


def final_usage(current, candidate):
    """The final one of two usages of the same response: the most output, then the one with cache fields."""
    if current is None or candidate["final"] > current["final"]:
        return candidate
    return current


def response_tokens(records):
    """Token totals over the model responses among `records` (in any order), counting each response once
    with its final usage. Synthetic records (the harness's own) are skipped."""
    last = {}
    for record in records:
        message = record.get("message") if isinstance(record, dict) else None
        if record.get("type") != "assistant" or not isinstance(message, dict):
            continue
        message_id = message.get("id")
        if not isinstance(message_id, str) or message_id == "<synthetic>" or message.get("model") == "<synthetic>":
            continue
        usage = usage_of(message)
        if usage is not None:
            last[message_id] = final_usage(last.get(message_id), usage)
    total = {name: 0 for name, _ in TOKEN_FIELDS}
    for usage in last.values():
        for name in total:
            total[name] += usage[name]
    return total


def parse_ts(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_ms(tool_use_result):
    """The tool's own duration, when Claude Code recorded one."""
    if not isinstance(tool_use_result, dict):
        return None
    for key, scale in (("durationMs", 1), ("duration_ms", 1), ("durationSeconds", 1000),
                       ("duration_seconds", 1000)):
        value = tool_use_result.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return round(float(value) * scale)
    return None


def _user_parts(record, message):
    """(text, tool_results) of a user record. text is None when the record carries only
    tool results; tool_results is a list of (tool_use_id, is_error)."""
    content = message.get("content")
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return None, []
    results, texts = [], []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_result":
            tool_id = block.get("tool_use_id")
            if isinstance(tool_id, str):
                results.append((tool_id, bool(block.get("is_error", False))))
        elif isinstance(block.get("text"), str):
            texts.append(block["text"])
    return ("\n".join(texts) if texts else None), results


def is_human_prompt(record, text):
    if not isinstance(text, str) or not text.strip():
        return False
    if record.get("isMeta") or record.get("isCompactSummary"):
        return False
    return not text.lstrip().startswith(INJECTED_PREFIXES)


def collect(records):
    """Reduce an iterable of session records to the events the breakdown needs:
    {"prompts": [(ts, text)], "gens": [(start, end)], "tools": [dict], "activity": [ts]}.
    Records may arrive in any order; they are sorted by timestamp first (an input
    before an assistant record at the same millisecond)."""
    items = []
    for record in records:
        if not isinstance(record, dict):
            continue
        ts = parse_ts(record.get("timestamp"))
        message = record.get("message")
        if ts is None or not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "assistant":
            message_id = message.get("id")
            if (not isinstance(message_id, str) or message_id == "<synthetic>"
                    or message.get("model") == "<synthetic>"):
                continue
            blocks = []
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") in ("thinking", "text", "tool_use"):
                    blocks.append((block["type"], block.get("name"), block.get("id")))
            items.append((ts, 1, "assistant", (message_id, blocks, usage_of(message))))
        elif role == "user":
            text, results = _user_parts(record, message)
            if text is None and not results:
                continue
            items.append((ts, 0, "user", (record, text, results)))
    items.sort(key=lambda item: (item[0], item[1]))

    prompts, activity, gens = [], [], []
    rounds, tools = {}, {}
    pending_inputs = []
    for ts, _, kind, data in items:
        if kind == "assistant":
            message_id, blocks, usage = data
            round_ = rounds.get(message_id)
            if round_ is None:
                round_ = {"inputs": pending_inputs, "outputs": [], "usage": None, "last": ts}
                pending_inputs = []
                rounds[message_id] = round_
            round_["last"] = ts
            if usage is not None:
                round_["usage"] = final_usage(round_["usage"], usage)
            for block_type, name, tool_id in blocks:
                round_["outputs"].append(ts)
                activity.append(ts)
                if block_type == "tool_use" and isinstance(tool_id, str) and tool_id not in tools:
                    tools[tool_id] = {"name": name, "id": tool_id, "emitted": ts, "returned": None,
                                      "is_error": None, "duration_ms": None}
            continue
        record, text, results = data
        for tool_id, is_error in results:
            pending_inputs.append(ts)
            activity.append(ts)
            tool = tools.get(tool_id)
            if tool is not None and tool["returned"] is None:
                tool["returned"] = ts
                tool["is_error"] = is_error
                tool["duration_ms"] = _duration_ms(record.get("toolUseResult"))
        if text is not None:
            pending_inputs.append(ts)
            activity.append(ts)
            if is_human_prompt(record, text):
                prompts.append((ts, text))

    responses = [(round_["last"], round_["usage"]) for round_ in rounds.values() if round_["usage"] is not None]
    for round_ in rounds.values():
        outputs, inputs = round_["outputs"], round_["inputs"]
        if not outputs or not inputs:
            continue
        first = min(outputs)
        before = [i for i in inputs if i <= first]
        if before and max(outputs) > max(before):
            gens.append((max(before), max(outputs)))
    prompts.sort(key=lambda p: p[0])
    activity.sort()
    return {"prompts": prompts, "gens": gens, "tools": list(tools.values()), "activity": activity,
            "responses": responses}


def _merge(intervals):
    merged = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _subtract(intervals, covered):
    """The parts of `intervals` (merged) that `covered` (merged) does not cover."""
    out = []
    for start, end in intervals:
        cursor = start
        for c_start, c_end in covered:
            if c_end <= cursor or c_start >= end:
                continue
            if c_start > cursor:
                out.append((cursor, c_start))
            cursor = max(cursor, c_end)
        if cursor < end:
            out.append((cursor, end))
    return out


def _seconds(intervals):
    return sum((end - start).total_seconds() for start, end in intervals)


def _clip(intervals, start, end):
    return [(max(a, start), min(b, end)) for a, b in intervals if b > start and a < end]


def breakdown(session, human_tools=HUMAN_WAIT_TOOLS, idle_seconds=IDLE_SECONDS):
    """One dict per turn: start, end, e2e_s, human_s, tool_s, model_s, idle_s, other_s,
    tool_calls, errors, longest_wait (tool name, seconds), prompt, and tokens ({input, output,
    cache_read, cache_write} of the responses that ended in the turn). The five time parts
    add up to e2e_s."""
    prompts, activity = session["prompts"], session["activity"]
    waits = []
    for tool in session["tools"]:
        if tool["returned"] is not None and tool["returned"] >= tool["emitted"]:
            waits.append((tool, (tool["returned"] - tool["emitted"]).total_seconds()))
    human_ivs = [(t["emitted"], t["returned"]) for t, _ in waits if t["name"] in human_tools]
    tool_ivs = [(t["emitted"], t["returned"]) for t, _ in waits if t["name"] not in human_tools]

    turns = []
    for index, (start, text) in enumerate(prompts):
        next_start = prompts[index + 1][0] if index + 1 < len(prompts) else None

        def in_window(ts, start=start, next_start=next_start):
            return ts >= start and (next_start is None or ts < next_start)

        in_turn = [a for a in activity if in_window(a)]
        if not in_turn:
            continue
        end = max(in_turn)
        parts, covered = {}, []
        for label, ivs in (("human_s", human_ivs), ("tool_s", tool_ivs), ("model_s", session["gens"])):
            fresh = _subtract(_merge(_clip(ivs, start, end)), covered)
            parts[label] = _seconds(fresh)
            covered = _merge(covered + fresh)
        gaps = _subtract([(start, end)], covered)
        parts["idle_s"] = _seconds([g for g in gaps if (g[1] - g[0]).total_seconds() >= idle_seconds])
        e2e = (end - start).total_seconds()
        other = e2e - parts["human_s"] - parts["tool_s"] - parts["model_s"] - parts["idle_s"]
        parts["other_s"] = 0.0 if abs(other) < 1e-6 else other
        calls = [t for t in session["tools"] if in_window(t["emitted"])]
        longest = max(((t, w) for t, w in waits if in_window(t["emitted"])),
                      key=lambda tw: tw[1], default=None)
        tokens = {name: 0 for name, _ in TOKEN_FIELDS}
        for ended, usage in session.get("responses", []):
            if in_window(ended):
                for name in tokens:
                    tokens[name] += usage[name]
        turns.append({
            "index": len(turns) + 1, "start": start, "end": end, "e2e_s": e2e, **parts, "tokens": tokens,
            "tool_calls": len(calls), "errors": sum(1 for t in calls if t["is_error"]),
            "longest_wait": (longest[0]["name"], longest[1]) if longest else None,
            "prompt": " ".join(text.split())[:80],
        })
    return turns


def longest_waits(session, count):
    """The `count` tool calls with the longest wall-clock time, longest first."""
    waits = [(t, (t["returned"] - t["emitted"]).total_seconds())
             for t in session["tools"] if t["returned"] is not None and t["returned"] >= t["emitted"]]
    waits.sort(key=lambda tw: -tw[1])
    return waits[:count]
