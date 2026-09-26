"""clp compress status - report the state of a clp compress folder run.

Usage: clp compress status ARCHIVES_DIR

Reads ARCHIVES_DIR.compress-status.json, which the compress wrapper keeps up to
date. A run whose process is gone without a final state is reported as "died", so
a crashed compression is never mistaken for a running one — the check is a signal
0 to the recorded pid, not a scan of the process table that could match the
checking command itself.

Prints KEY=VALUE lines: STATE (running|done|failed|died), ELAPSED_S,
INPUT_BYTES, READ_BYTES, PROGRESS_PCT, ARCHIVE_BYTES, and EXIT_CODE when known.
Exit codes: 0 done, 3 running, 1 failed or died, 2 usage or no status found.
"""

import json
import os
import sys
import time

USAGE = """Usage: clp compress status ARCHIVES_DIR

Report the state of a `clp compress folder` run: STATE, ELAPSED_S, INPUT_BYTES,
READ_BYTES, PROGRESS_PCT, ARCHIVE_BYTES, and EXIT_CODE when known.
Exit codes: 0 done, 3 running, 1 failed or died, 2 usage or no status found."""


def alive(pid):
    """Whether a process with this pid exists. Signal 0 is a permission probe."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0].startswith("-"):
        print(USAGE, file=sys.stderr)
        return 2
    status_file = argv[0].rstrip("/") + ".compress-status.json"
    if not os.path.isfile(status_file):
        print(f"error: no status file at {status_file}", file=sys.stderr)
        return 2

    try:
        with open(status_file, encoding="utf-8") as handle:
            status = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read {status_file}: {exc}", file=sys.stderr)
        return 2

    state = status.get("state", "")
    pid = status.get("pid", 0)
    if state == "running" and not alive(pid):
        state = "died"

    started = status.get("startedAt", 0)
    updated = status.get("updatedAt", 0)
    elapsed = int(time.time()) - started if state == "running" else updated - started

    input_bytes = status.get("inputBytes", 0)
    read_bytes = status.get("readBytes", 0)
    # Floor, as the shell's arithmetic and the jq it replaced both did.
    percent = (read_bytes * 100 // input_bytes) if input_bytes > 0 else 0

    print(f"STATE={state}")
    print(f"ELAPSED_S={elapsed}")
    print(f"INPUT_BYTES={input_bytes}")
    print(f"READ_BYTES={read_bytes}")
    print(f"PROGRESS_PCT={percent}")
    print(f"ARCHIVE_BYTES={status.get('archiveBytes', 0)}")
    if status.get("exitCode") is not None:
        print(f"EXIT_CODE={status['exitCode']}")

    if state == "done":
        return 0
    if state == "running":
        return 3
    return 1


if __name__ == "__main__":
    sys.exit(main())
