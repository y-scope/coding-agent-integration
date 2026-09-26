"""archive_json - the JSON documents and reductions a compression run writes.

This module owns every archive-side JSON document: the `.yscope-clp-archive.json`
metadata for a folder archive and for a session archive, the
`.compress-status.json` progress file, and the two readings of clp-s's
`--print-archive-stats` output that end up in the metadata (the time range) or on
the terminal (the "Time range:" line) plus the "Archive source ..." summary.

It exists because these documents used to be assembled by `jq` in the shell
wrappers, and jq is not present on a base install. Python is already required for
the rest of the plugin.

Subcommands:
  folder-metadata --out F ...    write a folder archive's .yscope-clp-archive.json
  session-metadata --out F ...   write a session archive's .yscope-clp-archive.json
  status-write --out F ...       write .compress-status.json atomically
  time-range --stats F           earliest/latest timestamp as JSON, or null
  time-range-text --key K --range J
                                 the "Time range:" line for a time-range result
  metadata-summary --file F      the "Archive source ..." lines for an archive

Conventions: stdout carries data only, diagnostics go to stderr as `error: ...`,
exit 2 for a usage error and 1 when an input cannot be read. Stdlib only.
"""

import argparse
import datetime
import json
import os
import sys


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        sys.exit(2)


def fail(message):
    print(f"error: {message}", file=sys.stderr)
    return 1


def load_json(text, what):
    """Parse a JSON literal passed on the command line; None means the literal `null`."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{what} is not JSON: {exc}") from exc


def optional(text, convert=str):
    """The shell's `if $x == "" then null else $x end` idiom, with a type."""
    if text is None or text == "":
        return None
    return convert(text)


def write_document(path, document):
    """Write atomically: a reader never sees a half-written document."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def source_extensions(args):
    """The filter a folder archive was compressed with: null, "*", or the list.

    `null` is a single-file or path-list input, which had no extension filter;
    `"*"` records `--extensions '*'` as the string it was typed as.
    """
    if args.source_extensions_all:
        return "*"
    return args.source_extensions or None


def cmd_folder_metadata(args):
    try:
        document = {
            "schemaVersion": int(args.schema_version),
            "createdAt": args.created_at,
            "plugin": args.plugin,
            "source": {
                "type": args.source_type,
                "path": args.source_path,
                "name": args.source_name,
                "recursive": optional(args.source_recursive, int)
                if args.source_recursive != "null" else None,
                "extensions": source_extensions(args),
                "paths": args.paths or [],
                "fileCount": int(args.source_file_count),
            },
            "archiveRoot": optional(args.archive_root),
            "archiveRootSource": args.archive_root_source,
            "archiveDir": args.archive_dir,
            "clpArchiveDir": optional(args.clp_archive_dir),
            "timestampKey": optional(args.timestamp_key),
            "timeRange": load_json(args.time_range, "--time-range"),
            "structurize": args.structurize == 1,
            "parser": optional(args.parser),
            "compression": {
                "rawBytes": int(args.input_bytes),
                "archiveBytes": int(args.archive_bytes),
                "ratio": args.compression_ratio,
                "reductionBytes": int(args.reduction_bytes),
                "reductionPercent": args.reduction_percent,
            },
            "command": args.command or [],
        }
    except ValueError as exc:
        return fail(str(exc))
    write_document(args.out, document)
    return 0


def cmd_session_metadata(args):
    try:
        raw_bytes = int(args.input_bytes)
        document = {
            "schemaVersion": int(args.schema_version),
            "createdAt": args.created_at,
            "plugin": args.plugin,
            "agent": args.agent,
            "sourceRoot": args.source_root,
            "archiveRoot": optional(args.archive_root),
            "archiveRootSource": args.archive_root_source,
            "archiveDir": args.archive_dir,
            "clpArchiveDir": optional(args.clp_archive_dir),
            "timestampKey": optional(args.timestamp_key),
            "timeRange": load_json(args.time_range, "--time-range"),
            "selection": {
                "file": optional(args.selection_file),
                "index": optional(args.session_index, int),
            },
            "session": {
                "file": args.session_file,
                "id": args.session_id,
                "sha256": args.session_sha256,
                "bytes": raw_bytes,
            },
            "compression": {
                "rawBytes": raw_bytes,
                "archiveBytes": int(args.archive_bytes),
                "ratio": args.compression_ratio,
                "reductionBytes": int(args.reduction_bytes),
                "reductionPercent": args.reduction_percent,
            },
            "command": args.command or [],
        }
    except ValueError as exc:
        return fail(str(exc))
    write_document(args.out, document)
    return 0


def cmd_status_write(args):
    document = {
        "state": args.state,
        "pid": int(args.pid),
        "startedAt": int(args.started_at),
        "updatedAt": int(args.updated_at),
        "inputBytes": int(args.input_bytes),
        "readBytes": int(args.read_bytes),
        "archiveBytes": int(args.archive_bytes),
        "exitCode": None if args.exit_code is None else int(args.exit_code),
    }
    write_document(args.out, document)
    return 0


def read_stats_lines(path):
    """The JSON objects clp-s printed into a --print-archive-stats file."""
    records = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number} is not JSON: {exc}") from exc
    return records


def iso_utc(epoch_ms):
    seconds = epoch_ms // 1000
    stamp = datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_time_range(args):
    try:
        records = read_stats_lines(args.stats)
    except (OSError, ValueError) as exc:
        return fail(f"cannot read the clp-s archive stats: {exc}")

    # An archive where no record carries the timestamp key reports 0 for both
    # ends, so it says nothing about the range and is left out.
    ends = [(r.get("begin_timestamp", 0), r.get("end_timestamp", 0)) for r in records]
    ends = [(begin, end) for begin, end in ends if begin != 0 or end != 0]
    if not ends:
        print("null")
        return 0

    begin_ms = min(begin for begin, _ in ends)
    end_ms = max(end for _, end in ends)
    print(json.dumps({
        "beginMs": begin_ms,
        "endMs": end_ms,
        "begin": iso_utc(begin_ms),
        "end": iso_utc(end_ms),
    }))
    return 0


def cmd_time_range_text(args):
    try:
        time_range = load_json(args.range, "--range")
    except ValueError as exc:
        return fail(str(exc))
    if time_range is None:
        print(f"Time range: none (no record has the timestamp key {args.key})")
    else:
        print(f"Time range: {time_range['begin']} to {time_range['end']}")
    return 0


def cmd_metadata_summary(args):
    try:
        with open(args.file, encoding="utf-8") as handle:
            metadata = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return fail(f"cannot read {args.file}: {exc}")

    session = metadata.get("session") or {}
    print("Archive source agent: " + str(metadata.get("agent") or "unknown"))
    print("Archive source session: " + str(session.get("file") or "unknown"))
    print("Archive source root: " + str(metadata.get("sourceRoot") or "unknown"))
    return 0


def build_parser():
    parser = Parser(prog="archive_json.py", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add(sub, *names, **kwargs):
        sub.add_argument(*names, **kwargs)

    folder = subparsers.add_parser("folder-metadata")
    add(folder, "--out", required=True)
    add(folder, "--schema-version", default="1")
    add(folder, "--created-at", required=True)
    add(folder, "--plugin", default="yscope-clp")
    add(folder, "--source-type", required=True)
    add(folder, "--source-path", required=True)
    add(folder, "--source-name", required=True)
    add(folder, "--source-recursive", default="null")
    add(folder, "--source-extension", dest="source_extensions", action="append", default=[])
    add(folder, "--source-extensions-all", action="store_true")
    add(folder, "--source-file-count", required=True)
    add(folder, "--path", dest="paths", action="append", default=[])
    add(folder, "--archive-root", default="")
    add(folder, "--archive-root-source", default="output-dir")
    add(folder, "--archive-dir", required=True)
    add(folder, "--clp-archive-dir", default="")
    add(folder, "--timestamp-key", default="")
    add(folder, "--time-range", default="null")
    add(folder, "--input-bytes", required=True)
    add(folder, "--archive-bytes", required=True)
    add(folder, "--reduction-bytes", required=True)
    add(folder, "--compression-ratio", required=True)
    add(folder, "--reduction-percent", required=True)
    add(folder, "--command", action="append", default=[])
    add(folder, "--structurize", type=int, default=0)
    add(folder, "--parser", default="")
    folder.set_defaults(func=cmd_folder_metadata)

    session = subparsers.add_parser("session-metadata")
    add(session, "--out", required=True)
    add(session, "--schema-version", default="1")
    add(session, "--created-at", required=True)
    add(session, "--plugin", default="yscope-clp")
    add(session, "--agent", required=True)
    add(session, "--source-root", required=True)
    add(session, "--archive-root", default="")
    add(session, "--archive-root-source", default="output-dir")
    add(session, "--archive-dir", required=True)
    add(session, "--clp-archive-dir", default="")
    add(session, "--timestamp-key", default="")
    add(session, "--time-range", default="null")
    add(session, "--selection-file", default="")
    add(session, "--session-index", default="")
    add(session, "--session-file", required=True)
    add(session, "--session-id", required=True)
    add(session, "--session-sha256", required=True)
    add(session, "--input-bytes", required=True)
    add(session, "--archive-bytes", required=True)
    add(session, "--reduction-bytes", required=True)
    add(session, "--compression-ratio", required=True)
    add(session, "--reduction-percent", required=True)
    add(session, "--command", action="append", default=[])
    session.set_defaults(func=cmd_session_metadata)

    status = subparsers.add_parser("status-write")
    add(status, "--out", required=True)
    add(status, "--state", required=True)
    add(status, "--pid", required=True)
    add(status, "--started-at", required=True)
    add(status, "--updated-at", required=True)
    add(status, "--input-bytes", required=True)
    add(status, "--read-bytes", required=True)
    add(status, "--archive-bytes", required=True)
    add(status, "--exit-code", default=None)
    status.set_defaults(func=cmd_status_write)

    time_range = subparsers.add_parser("time-range")
    add(time_range, "--stats", required=True)
    time_range.set_defaults(func=cmd_time_range)

    range_text = subparsers.add_parser("time-range-text")
    add(range_text, "--key", required=True)
    add(range_text, "--range", required=True)
    range_text.set_defaults(func=cmd_time_range_text)

    summary = subparsers.add_parser("metadata-summary")
    add(summary, "--file", required=True)
    summary.set_defaults(func=cmd_metadata_summary)

    return parser


def main(argv=None):
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
