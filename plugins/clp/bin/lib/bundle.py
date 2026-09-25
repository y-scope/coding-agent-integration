"""bundle - navigate between a session bundle's catalog (SQLite) and its CLP archives.

A bundle is a directory:
  catalog.sqlite   the map: archives, sources, agents, graph nodes and edges (derived)
  archives/        one clp-s archives dir, one archive per kind of log
  files/           the files that are not JSON logs

The catalog says what exists and how it connects; the archives hold the records. A node of
the catalog names its records by an ID they carry (agentId, runId), so no offsets are stored.
Stdlib only.
"""

import json
import os
import sqlite3
import subprocess
from datetime import datetime

SCHEMA = """
CREATE TABLE bundle(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE archives(archive_id TEXT PRIMARY KEY, kind TEXT, records INTEGER);
CREATE TABLE sources(path TEXT PRIMARY KEY, kind TEXT, bytes INTEGER, sha256 TEXT,
                     archive_id TEXT, file TEXT, agent_id TEXT, run_id TEXT);
CREATE TABLE agents(agent_id TEXT PRIMARY KEY, run_id TEXT, agent_type TEXT, description TEXT,
                    depth INTEGER, parent_agent_id TEXT, tool_use_id TEXT, is_fork INTEGER, model TEXT);
CREATE TABLE nodes(id TEXT PRIMARY KEY, kind TEXT, label TEXT, agent_id TEXT, run_id TEXT, task_id TEXT,
                   instance TEXT, phase TEXT, start TEXT, end TEXT, status TEXT, cause TEXT, attempts INTEGER,
                   tool_calls INTEGER, errors INTEGER, tokens INTEGER, attrs TEXT);
CREATE TABLE edges(src TEXT, dst TEXT, kind TEXT, at TEXT, via TEXT, inferred INTEGER, tool_use_id TEXT);
CREATE INDEX edges_src ON edges(src);
CREATE INDEX edges_dst ON edges(dst);
CREATE INDEX nodes_kind ON nodes(kind);
CREATE INDEX nodes_agent ON nodes(agent_id);
"""

# The kind of archive that holds a node's records, and the field that names them.
EVIDENCE = {
    "agent": ("agent", "agentId"),
    "attempt": ("workflow-agent", "agentId"),
    "workflow": ("workflow-run", "runId"),
    "run": ("workflow-run", "runId"),
}


class BundleError(Exception):
    pass


def open_catalog(bundle_dir):
    path = os.path.join(bundle_dir, "catalog.sqlite")
    if not os.path.isfile(path):
        raise BundleError(f"not a bundle (no catalog.sqlite): {bundle_dir}")
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only = ON")
    return db


def resolve(db, ident):
    """The one node an identifier names: a node id, an agent id (or a unique prefix of one of
    six or more characters), a run id or a task id."""
    row = db.execute("SELECT * FROM nodes WHERE id = ?", (ident,)).fetchone()
    if row:
        return row
    for column, kinds in (("agent_id", ("attempt", "agent")), ("run_id", ("run",)), ("task_id", ("workflow",))):
        marks = ",".join("?" * len(kinds))
        rows = db.execute(f"SELECT * FROM nodes WHERE {column} = ? AND kind IN ({marks})", (ident, *kinds)).fetchall()
        if len(rows) == 1:
            return rows[0]
        if len(rows) > 1:
            raise BundleError(f"{ident} names several nodes: " + ", ".join(r["id"] for r in rows))
    if len(ident) >= 6:
        rows = db.execute("SELECT * FROM nodes WHERE agent_id LIKE ? AND kind IN ('attempt','agent')",
                          (ident + "%",)).fetchall()
        if len(rows) == 1:
            return rows[0]
        if len(rows) > 1:
            raise BundleError(f"{ident} is a prefix of several agents: " + ", ".join(r["agent_id"] for r in rows[:5]))
    raise BundleError(f"no node matches: {ident}")


def archive_ids(db, kind):
    return [r["archive_id"] for r in db.execute("SELECT archive_id FROM archives WHERE kind = ? ORDER BY archive_id", (kind,))]


def search_command(bundle_dir, wrapper, archive_id, kql):
    return [wrapper, "--archive-id", archive_id, os.path.join(bundle_dir, "archives"), kql]


def evidence_query(node):
    """(archive kind, KQL) for a node's own records, or None when it has none."""
    target = EVIDENCE.get(node["kind"])
    if not target:
        return None
    kind, field = target
    value = node["agent_id"] if field == "agentId" else node["run_id"]
    return kind, f'{field}:"{value}"'


def describe(db, node):
    """Everything the catalog knows about a node, as a dict."""
    out = {k: node[k] for k in node.keys() if k != "attrs" and node[k] is not None}
    out["attrs"] = json.loads(node["attrs"] or "{}")
    out["parents"] = [dict(r) for r in db.execute(
        "SELECT e.kind AS edge, e.via, n.id, n.kind, n.label FROM edges e JOIN nodes n ON n.id = e.src "
        "WHERE e.dst = ? AND e.kind != 'result' ORDER BY n.start LIMIT 8", (node["id"],))]
    out["children"] = [dict(r) for r in db.execute(
        "SELECT e.kind AS edge, n.kind, COUNT(*) AS n FROM edges e JOIN nodes n ON n.id = e.dst "
        "WHERE e.src = ? AND e.kind != 'result' GROUP BY e.kind, n.kind", (node["id"],))]
    out["results"] = [dict(r) for r in db.execute(
        "SELECT e.at, e.dst, e.kind FROM edges e WHERE e.src = ? AND e.kind = 'result'", (node["id"],))]
    if node["kind"] == "attempt":
        unit = db.execute("SELECT n.id, n.label, n.phase FROM edges e JOIN nodes n ON n.id = e.src "
                          "WHERE e.dst = ? AND n.kind = 'unit'", (node["id"],)).fetchone()
        out["unit"] = dict(unit) if unit else None
        if unit and not out.get("label"):
            out["label"] = unit["label"]  # an attempt has no name of its own; its logical agent does
    target = evidence_query(node)
    if target:
        out["archives"] = archive_ids(db, target[0])
        out["query"] = target[1]
    return out


def find_at(db, when, kinds, limit):
    """Nodes running at `when` (UTC, ISO, any precision from minutes), with a count per kind."""
    stamp = normalize_time(when)
    marks = ",".join("?" * len(kinds))
    counts = {r["kind"]: r["n"] for r in db.execute(
        "SELECT kind, COUNT(*) AS n FROM nodes WHERE start <= ? AND end >= ? AND kind IN "
        "('agent','workflow','attempt') GROUP BY kind", (stamp, stamp))}
    rows = [dict(r) for r in db.execute(
        f"SELECT id, kind, label, status, start, end FROM nodes WHERE start <= ? AND end >= ? AND kind IN ({marks}) "
        "ORDER BY start LIMIT ?", (stamp, stamp, *kinds, limit))]
    return counts, rows


def normalize_time(value):
    text = value.strip().rstrip("Z").replace(" ", "T")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text[:19], fmt).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    raise BundleError(f"cannot read a time from: {value} (use YYYY-MM-DDTHH:MM[:SS], UTC)")


def fetch_records(bundle_dir, db, node, wrapper):
    """The node's own records from its archive, sorted by time. Returns (records, command)."""
    target = evidence_query(node)
    if not target:
        raise BundleError(f"{node['id']} ({node['kind']}) has no records of its own")
    kind, kql = target
    ids = archive_ids(db, kind)
    if not ids:
        raise BundleError(f"the catalog lists no {kind} archive")
    records, command = [], None
    for archive_id in ids:
        command = search_command(bundle_dir, wrapper, archive_id, kql)
        proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        if proc.returncode != 0:
            raise BundleError(f"search failed ({proc.returncode}): {proc.stderr.strip()[-300:]}")
        for line in proc.stdout.splitlines():
            if line.startswith("{"):
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    records.sort(key=lambda r: r.get("timestamp") or "")
    return records, command


def brief(record, width=90):
    """One line for a transcript record: time, type and what it did."""
    stamp = (record.get("timestamp") or "")[11:19] or "        "
    message = record.get("message")
    detail = ""
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        detail = content
    elif isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "tool_use":
                parts.append(f"tool_use {block.get('name')}")
            elif kind == "tool_result":
                inner = block.get("content")
                if isinstance(inner, list):
                    inner = " ".join(str(b.get("text", "")) for b in inner if isinstance(b, dict))
                parts.append("tool_result " + str(inner))
            elif kind == "text":
                parts.append(str(block.get("text", "")))
            elif kind == "thinking":
                parts.append("thinking")
        detail = " | ".join(parts)
    elif "type" in record and "key" in record:  # a workflow journal row
        detail = f"{record.get('type')} {record.get('key', '')[:14]}"
    detail = " ".join(detail.split())
    if len(detail) > width:
        detail = detail[:width - 1] + "…"
    return f"{stamp} {record.get('type', '?'):<10} {detail}"
