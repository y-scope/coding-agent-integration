#!/usr/bin/env python3
"""Plot number of log records vs number of distinct logtypes as a folder of
JSON log files is ingested in filename (time) order.

The curve shows the logtype vocabulary saturating (sublinear) while the record
count grows linearly -- the premise of the logtype-baseline method: a few
hundred distinct message templates, no matter how many millions of records.

Usage:
  plot_logs_vs_logtypes.py --folder DIR [--msg-key KEY] [--stride N]
      [--max-files N] [--every-files K] [--out PNG] [--title TITLE]

  --folder DIR       Folder of JSON-per-line log files (e.g. mongod logs).
  --msg-key KEY       JSON field holding the message text (default: msg).
  --stride N         Parse every Nth record when counting logtypes (default 1 =
                     all). Speeds up large folders at the cost of possibly
                     missing rare logtypes; the record COUNT on the x-axis is
                     always the true per-file count (unsampled).
  --max-files N       Stop after N files (0 = all).
  --every-files K     Record a data point every K files (default 1). Set 0 to
                     sample only by --every-records.
  --every-records R   Also record a data point every R cumulative records (default
                     0 = off). Use this to capture the intra-file rise as the
                     startup templates accumulate (the per-file points alone are
                     often flat from the first file).
  --out PNG           Output PNG path (default: logs_vs_logtypes.png).
  --title TITLE       Plot title.

The script is self-contained (stdlib + matplotlib). Records one data point per
file (or every K files) plus the origin.
"""
import argparse
import json
import os
import re
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Templatize variable runs into <*> so distinct messages collapse to templates.
# Order matters: {var} placeholders and hex first, then bare numbers.
_TEMPLATIZE = [
    (re.compile(r"\{[^}]+\}"), "<*>"),      # Mongo {var} placeholders
    (re.compile(r"0x[0-9a-fA-F]+"), "<*>"),  # hex
    (re.compile(r"\b\d+\b"), "<*>"),          # bare numbers
]


def templatize(s):
    for pat, rep in _TEMPLATIZE:
        s = pat.sub(rep, s)
    return s


def iter_files(folder):
    out = []
    for root, _, names in os.walk(folder):
        for n in names:
            out.append(os.path.join(root, n))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folder", required=True, help="Folder of JSON-per-line log files.")
    ap.add_argument("--msg-key", default="msg", help="JSON field holding the message text (default: msg).")
    ap.add_argument("--stride", type=int, default=1, help="Parse every Nth record for logtypes (default 1 = all).")
    ap.add_argument("--max-files", type=int, default=0, help="Stop after N files (0 = all).")
    ap.add_argument("--every-files", type=int, default=1, help="Record a data point every K files (default 1; 0 = off).")
    ap.add_argument("--every-records", type=int, default=0, help="Also record a data point every R cumulative records (0 = off).")
    ap.add_argument("--out", default="logs_vs_logtypes.png", help="Output PNG path.")
    ap.add_argument("--title", default=None, help="Plot title.")
    args = ap.parse_args()

    if args.stride < 1:
        sys.exit("error: --stride must be >= 1")
    if not args.every_files and not args.every_records:
        sys.exit("error: set at least one of --every-files or --every-records")

    files = iter_files(args.folder)
    if not files:
        sys.exit(f"error: no files under {args.folder}")
    if args.max_files > 0:
        files = files[: args.max_files]

    distinct = set()
    xs = [0]
    ys = [0]
    total = 0
    next_record_mark = args.every_records  # 0 if --every-records off
    for i, path in enumerate(files, 1):
        file_records = 0
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                file_records += 1
                total += 1
                if args.stride > 1 and (file_records % args.stride) != 1:
                    # Still record record-based points at the true count even on
                    # skipped lines, so the x-axis reflects real record growth.
                    pass
                else:
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        obj = None
                    if obj is not None:
                        msg = obj.get(args.msg_key)
                        if isinstance(msg, str):
                            distinct.add(templatize(msg))
                # Intra-file points (capture the startup rise).
                if next_record_mark and total >= next_record_mark:
                    xs.append(total)
                    ys.append(len(distinct))
                    next_record_mark += args.every_records
        # Per-file point.
        if args.every_files and (i % args.every_files == 0):
            xs.append(total)
            ys.append(len(distinct))
            print(f"  file {i:>3}/{len(files)}  {os.path.basename(path)}  records={total:>12,}  logtypes={len(distinct)}", file=sys.stderr)

    title = args.title or f"Log records vs distinct logtypes  ({len(distinct)} logtypes over {total:,} records; {len(files)} files)"

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(xs, ys, "o-", color="#1f77b4", linewidth=1.5, markersize=4)
    ax.set_xlabel("Number of log records ingested")
    ax.set_ylabel("Distinct message templates (templatized)")
    ax.set_xscale("log")
    ax.set_title(title)
    # Subtitle noting the templatized count vs CLP's finer stats.logtypes count.
    ax.text(
        0.02, 0.98,
        f"y = sed-templatized msg templates (collapse {{var}}/hex/\\d -> <*>)\n"
        f"CLP stats.logtypes reports a finer count for the same archive\n"
        f"(e.g. 199 here) -- both saturate while records grow linearly.",
        transform=ax.transAxes, fontsize=7, color="#555555", va="top",
    )
    ax.grid(True, which="both", alpha=0.3)
    ax.set_ylim(bottom=0)
    fig.tight_layout()
    fig.savefig(args.out, dpi=120)
    print(f"wrote {args.out}: {len(xs)} points, {len(distinct)} distinct (templatized) logtypes, {total:,} records across {len(files)} files")


if __name__ == "__main__":
    main()