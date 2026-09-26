"""bundle - navigate between a session bundle's catalog (SQLite) and its CLP archives.

A bundle is a directory:
  manifest.json    every source file: its kind, size, hash, and where it went (an archive and the
                   positions of its records there, or a path under files/)
  archives/        one clp-s archives dir, one archive per kind of log, nothing else in it
  files/           the files that are not JSON logs, at their paths relative to the session
  catalog.sqlite   the map: archives, sources, agents, graph nodes and edges, events (derived from
                   the three above, and rebuilt from them)

The catalog says what exists and how it connects; the archives hold the records. A node of
the catalog names its records by an ID they carry (agentId, runId), so no offsets are stored.
Stdlib only.
"""

import json
import os
import re
import sqlite3
import struct
import subprocess
from datetime import datetime

# The catalog's layout. A catalog of another layout is refused, and rebuilt from its bundle
# (`clp-bundle BUNDLE rebuild`) when the bundle's manifest layout is current.
LAYOUT = 3
# The layout of manifest.json, archives/ and files/. A bundle of another layout is made again (`build --force`).
MANIFEST_LAYOUT = 2

SCHEMA = """
CREATE TABLE bundle(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE archives(archive_id TEXT PRIMARY KEY, kind TEXT, records INTEGER);
-- archive_id and first_pos/records locate an archived file's records (positions first_pos to
-- first_pos + records - 1 in that archive); file is the path under files/ of one kept as a file.
-- nul_bytes and damaged_lines count the NUL bytes removed before compression (lost writes).
CREATE TABLE sources(path TEXT PRIMARY KEY, kind TEXT, bytes INTEGER, sha256 TEXT,
                     archive_id TEXT, file TEXT, agent_id TEXT, run_id TEXT,
                     first_pos INTEGER, records INTEGER, nul_bytes INTEGER, damaged_lines INTEGER);
CREATE TABLE agents(agent_id TEXT PRIMARY KEY, run_id TEXT, agent_type TEXT, description TEXT,
                    depth INTEGER, parent_agent_id TEXT, tool_use_id TEXT, is_fork INTEGER, model TEXT);
-- tokens_*: the usage the API reported for the node's model responses, each response counted once: the
-- main thread's own, an agent's or attempt's transcript, a workflow instance's or run's attempts summed.
-- Input is counted on every call. The workflow runtime's own figure, which is closer to the final context
-- size, is attrs.runtime_tokens on run nodes.
CREATE TABLE nodes(id TEXT PRIMARY KEY, kind TEXT, label TEXT, agent_id TEXT, run_id TEXT, task_id TEXT,
                   instance TEXT, phase TEXT, start TEXT, end TEXT, status TEXT, cause TEXT, attempts INTEGER,
                   tool_calls INTEGER, errors INTEGER, tokens_input INTEGER, tokens_output INTEGER,
                   tokens_cache_read INTEGER, tokens_cache_write INTEGER, attrs TEXT);
CREATE TABLE edges(src TEXT, dst TEXT, kind TEXT, at TEXT, via TEXT, inferred INTEGER, tool_use_id TEXT);
CREATE INDEX edges_src ON edges(src);
CREATE INDEX edges_dst ON edges(dst);
CREATE INDEX nodes_kind ON nodes(kind);
CREATE INDEX nodes_agent ON nodes(agent_id);
-- One row per conversation record (user or assistant, with a uuid) and per record of any other type that
-- carries a reference to an agent or task (the completion notifications): no text, only what a join
-- needs. The record itself is found by its uuid in the archive of its kind. Not events: records
-- without a uuid (the harness's bookkeeping rows such as mode, permission-mode, ai-title), records
-- with a uuid that carry nothing to join on (hook and reminder attachments, system rows), and journal
-- rows. bundle.events_skipped_<kind> and events_unlisted_<kind> count them.
-- pos is the record's position in the archive of its kind. A uuid can repeat: Claude Code rewrites a
-- session's first records, with the same uuid and changed fields, when the session is reopened.
-- message_id groups the records of one model response. tokens_* is that response's final usage, set on one
-- of its records only (the one that carries it), so summing a column counts each response once; input is
-- the context sent with that call, so it shows the context growing and a compaction resetting it.
CREATE TABLE events(id INTEGER PRIMARY KEY, uuid TEXT NOT NULL, kind TEXT, pos INTEGER, agent_id TEXT, ts TEXT,
                    type TEXT, turn INTEGER, human INTEGER, interrupt INTEGER, is_error INTEGER,
                    ref_agent_id TEXT, ref_task_id TEXT, message_id TEXT, tokens_input INTEGER,
                    tokens_output INTEGER, tokens_cache_read INTEGER, tokens_cache_write INTEGER);
-- The tool calls and results inside an event: a tool_use block (role 'use', with the tool's name) or a
-- tool_result block (role 'result', with its error flag). Join the two on tool_use_id to pair them.
CREATE TABLE event_tools(event INTEGER, tool_use_id TEXT, role TEXT, name TEXT, is_error INTEGER);
CREATE INDEX events_uuid ON events(uuid);
CREATE INDEX events_agent ON events(agent_id, ts);
CREATE INDEX events_turn ON events(turn) WHERE turn IS NOT NULL;
CREATE INDEX events_ref_agent ON events(ref_agent_id) WHERE ref_agent_id IS NOT NULL;
CREATE INDEX events_ref_task ON events(ref_task_id) WHERE ref_task_id IS NOT NULL;
CREATE INDEX events_calls ON events(agent_id, ts) WHERE tokens_input IS NOT NULL;
CREATE INDEX event_tools_event ON event_tools(event);
CREATE INDEX event_tools_use ON event_tools(tool_use_id);
CREATE INDEX event_tools_name ON event_tools(name) WHERE name IS NOT NULL;
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
    try:
        layout = db.execute("SELECT v FROM bundle WHERE k = 'layout'").fetchone()
    except sqlite3.DatabaseError:
        layout = None
    if layout is None or layout[0] != str(LAYOUT):
        db.close()
        found = f"layout {layout[0]}" if layout and str(layout[0]).isdigit() else "an older layout"
        raise BundleError(f"the catalog in {bundle_dir} has {found}, not layout {LAYOUT}; rebuild it with "
                          f"`clp-bundle {bundle_dir} rebuild` (or, for a bundle with no manifest.json, `build --force`)")
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


def resolve_events(db, ident):
    """The events a uuid names, or a unique prefix of it of eight or more characters. A uuid can
    name several events: Claude Code rewrites some records, with the same uuid, when a session is
    reopened."""
    rows = db.execute("SELECT * FROM events WHERE uuid = ? ORDER BY kind, pos", (ident,)).fetchall()
    if rows:
        return rows
    if len(ident) >= 8:
        uuids = [r[0] for r in db.execute("SELECT DISTINCT uuid FROM events WHERE uuid LIKE ? LIMIT 3", (ident + "%",))]
        if len(uuids) == 1:
            return db.execute("SELECT * FROM events WHERE uuid = ? ORDER BY kind, pos", (uuids[0],)).fetchall()
        if len(uuids) > 1:
            raise BundleError(f"{ident} is a prefix of several records")
    raise BundleError(f"no event with uuid: {ident} (records without a uuid are not events)")


def event_tools(db, event):
    return [dict(r) for r in db.execute(
        "SELECT tool_use_id, role, name, is_error FROM event_tools WHERE event = ? ORDER BY rowid", (event["id"],))]


def linked_nodes(db, event):
    """The catalog nodes an event points at, as (relation, node row): the agent whose transcript
    it is in, the agent or task it refers to, and the node a launch call in it created."""
    found, seen = [], set()

    def add(relation, node):
        if node and (relation, node["id"]) not in seen:
            seen.add((relation, node["id"]))
            found.append((relation, node))

    if event["agent_id"]:
        add("in", db.execute("SELECT * FROM nodes WHERE agent_id = ? AND kind IN ('attempt','agent')", (event["agent_id"],)).fetchone())
    if event["ref_agent_id"]:
        add("refers_to", db.execute("SELECT * FROM nodes WHERE agent_id = ? AND kind = 'agent'", (event["ref_agent_id"],)).fetchone())
    if event["ref_task_id"]:
        add("refers_to", db.execute("SELECT * FROM nodes WHERE task_id = ? OR (agent_id = ? AND kind = 'agent')",
                                    (event["ref_task_id"], event["ref_task_id"])).fetchone())
    for tool in event_tools(db, event):
        if tool["role"] == "use":
            for r in db.execute("SELECT n.* FROM edges e JOIN nodes n ON n.id = e.dst WHERE e.kind = 'launch' AND e.tool_use_id = ?",
                                (tool["tool_use_id"],)):
                add("launched", r)
    return found


def context(bundle_dir, db, event, before, after, clp_s=None):
    """The records around an event in its own source file, in their original order: (source path, first
    position, [(position, record)]). Positions come from the archive, so records without a uuid are
    included, and the window stops at the file's first and last record."""
    archive_id = db.execute("SELECT archive_id FROM archives WHERE kind = ?", (event["kind"],)).fetchone()
    source = db.execute("SELECT path, first_pos, records FROM sources WHERE archive_id = ? AND first_pos <= ? "
                        "AND ? < first_pos + records", (archive_id[0], event["pos"], event["pos"])).fetchone() if archive_id else None
    if source is None:
        raise BundleError(f"no source file holds position {event['pos']} of the {event['kind']} archive")
    expected = db.execute("SELECT records FROM archives WHERE archive_id = ?", (archive_id[0],)).fetchone()[0]
    start = max(source["first_pos"], event["pos"] - before)
    stop = min(source["first_pos"] + source["records"], event["pos"] + after + 1)
    archive = ArchiveRecords(resolve_clp_s(clp_s), os.path.join(bundle_dir, "archives", archive_id[0]), expected)
    try:
        rows = list(zip(range(start, stop), archive.records(start, stop - start)))
    finally:
        archive.close()
    return source["path"], source["first_pos"], rows


def fetch_event_records(bundle_dir, db, uuid, kind, wrapper):
    """Every record with this uuid in the archive of `kind`, in archive order."""
    ids = archive_ids(db, kind)
    if not ids:
        raise BundleError(f"the catalog lists no {kind} archive")
    records = []
    for archive_id in ids:
        proc = subprocess.run(search_command(bundle_dir, wrapper, archive_id, f'uuid:"{uuid}"'),
                              capture_output=True, text=True, encoding="utf-8")
        if proc.returncode != 0:
            raise BundleError(f"search failed ({proc.returncode}): {proc.stderr.strip()[-300:]}")
        records += [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]
    if not records:
        raise BundleError(f"the archive holds no record with uuid {uuid}; the catalog and archives disagree")
    return records


# ---- The engine seam: everything that runs clp-s goes through these functions (and search_command
# above), so a different engine changes this block and nothing else.

def resolve_clp_s(explicit=None):
    """The clp-s binary, in the order the shell wrappers use: an explicit path, CLP_S_BIN, the plugin's
    bin/clp-s, the plugin's .clp-core/bin/clp-s, then PATH. A path that was asked for and is not
    executable is an error, as in the wrappers, not a reason to fall through."""
    import shutil
    for label, value in (("--clp-s", explicit), ("CLP_S_BIN", os.environ.get("CLP_S_BIN"))):
        if value:
            if os.path.isfile(value) and os.access(value, os.X_OK):
                return value
            raise BundleError(f"{label} is set but is not an executable file: {value}")
    bin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for candidate in (os.path.join(bin_dir, "clp-s"), os.path.join(os.path.dirname(bin_dir), ".clp-core", "bin", "clp-s"),
                      shutil.which("clp-s")):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise BundleError("clp-s is not available. Run the plugin installer, set CLP_S_BIN or pass --clp-s.")


class Prepared:
    """A file as clp-s will read it: `path` is the file itself, or a copy with NUL bytes removed."""

    def __init__(self, source, path, records, nul_bytes, damaged_lines):
        self.source, self.path, self.records = source, path, records
        self.nul_bytes, self.damaged_lines = nul_bytes, damaged_lines


def prepare(source, workdir):
    """Count a JSONL file's records (non-blank lines, which is what clp-s counts) and, if it contains
    NUL bytes, write a copy without them into workdir. A raw NUL byte is never part of valid JSON
    (JSON writes it as \\u0000), so it is damage from a lost write and removing it drops no data. A
    line that is blank once they are gone is dropped too."""
    records = nul_bytes = damaged = number = last_number = 0
    last = b""
    with open(source, "rb") as fh:                      # one line at a time: files can be large
        for line in fh:
            number += 1
            nul = line.count(b"\x00")
            if nul:
                nul_bytes += nul
                damaged += 1
                line = line.replace(b"\x00", b"")
            if line.strip():
                records += 1
                last, last_number = line, number
    # clp-s drops a record cut off at the end of a file without a word, so check the last one here.
    if last:
        try:
            json.loads(last)
        except ValueError:
            if last.endswith(b"\n"):
                raise BundleError(f"{source}:{last_number}: not a JSON record") from None
            raise BundleError(f"{source}:{last_number}: the last record is cut off (the file ends in the middle of "
                              "it); if the session is still running, build it once it has stopped") from None
    if not nul_bytes:
        return Prepared(source, source, records, 0, 0)
    path = os.path.join(workdir, f"{len(os.listdir(workdir)):06d}-{os.path.basename(source)}")
    with open(source, "rb") as src, open(path, "wb") as dst:
        for line in src:
            line = line.replace(b"\x00", b"")
            if line.strip():
                dst.write(line if line.endswith(b"\n") else line + b"\n")
    return Prepared(source, path, records, nul_bytes, damaged)


def compress(clp_s, archives_dir, prepared, timestamp_key="timestamp"):
    """Compress the prepared files into archives_dir as one compress run, in the order given; returns
    the ID of the archive it made. Positions in the archive follow that order, so file k holds
    positions sum(records of files before k) onward."""
    import tempfile
    before = set(os.listdir(archives_dir)) if os.path.isdir(archives_dir) else set()
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as listing:
        listing.write("\n".join(p.path for p in prepared) + "\n")
    try:
        proc = subprocess.run([clp_s, "c", "--timestamp-key", timestamp_key, "--files-from", listing.name, archives_dir],
                              capture_output=True, text=True, encoding="utf-8")
    finally:
        os.remove(listing.name)
    if proc.returncode != 0:
        raise BundleError(_compress_error(proc.stderr, prepared))
    made = sorted(set(os.listdir(archives_dir)) - before)
    if len(made) != 1:
        raise BundleError(f"clp-s made {len(made)} archives from one compress run; the bundle expects one per kind")
    return made[0]


def _compress_error(stderr, prepared):
    """clp-s's parse error, as the source file and line it points at."""
    m = re.search(r"while trying to parse (.+?) after parsing (\d+) bytes", stderr)
    if m:
        for p in prepared:
            if p.path == m.group(1):
                # clp-s counts the bytes up to the end of the last record it could read, so the bad one
                # starts at the first byte after that which is not whitespace.
                with open(p.path, "rb") as fh:
                    data = fh.read()
                start = int(m.group(2))
                while start < len(data) and data[start:start + 1].isspace():
                    start += 1
                line = data[:start].count(b"\n") + 1
                where = f"{p.source}:{line}" if p.path == p.source else f"{p.source} (line {line} once NUL bytes are removed)"
                return f"{where}: not a JSON record"
    return f"clp-s could not compress {len(prepared)} files: {stderr.strip()[-400:]}"


class ArchiveRecords:
    """Every record of one archive, in its original order, without holding them in memory. A search writes
    each record with its position through clp-s's file output handler into a temporary file; only an index
    from position to byte range is kept, and each record is read from the file and parsed when asked for.
    `expected` is how many records the archive holds; positions must run 0 to expected - 1 with no gap.
    Close it to remove the temporary file."""

    def __init__(self, clp_s, archive_dir, expected):
        import array
        import tempfile
        fd, self._path = tempfile.mkstemp(suffix=".msgpack")
        os.close(fd)
        self._file = None
        try:
            proc = subprocess.run([clp_s, "s", archive_dir, "*", "file", "--path", self._path], capture_output=True,
                                  text=True, encoding="utf-8")
            if proc.returncode != 0:
                raise BundleError(f"clp-s could not read {archive_dir}: {proc.stderr.strip()[-300:]}")
            self._offset = array.array("Q", bytes(8 * expected))
            self._length = array.array("Q", bytes(8 * expected))
            seen = bytearray(expected)
            self._file = open(self._path, "rb")
            for position, start, size in _file_output_rows(self._file):
                if not 0 <= position < expected or seen[position]:
                    raise BundleError(f"{archive_dir}: position {position} is out of range or repeated")
                seen[position] = 1
                self._offset[position], self._length[position] = start, size
            if seen.count(1) != expected:
                raise BundleError(f"{archive_dir}: read {seen.count(1)} records, expected positions 0 to {expected - 1}")
        except BaseException:
            self.close()
            raise

    def records(self, first, count):
        """The records at positions first to first + count - 1, parsed one at a time."""
        fd = self._file.fileno()
        for position in range(first, first + count):
            yield json.loads(os.pread(fd, self._length[position], self._offset[position]))

    def close(self):
        if self._file is not None:
            self._file.close()
            self._file = None
        if os.path.exists(self._path):
            os.remove(self._path)


class SourceRecords:
    """One source file's records inside an ArchiveRecords: iterable any number of times, parsed each time."""

    def __init__(self, archive, first, count):
        self._archive, self.first, self._count = archive, first, count

    def __iter__(self):
        return self._archive.records(self.first, self._count)

    def __len__(self):
        return self._count


def _file_output_rows(fh, chunk=1 << 22):
    """(position, byte offset, byte length) of each record in clp-s's file output: a stream of msgpack arrays
    [timestamp, record, original path, archive id, position]. Decodes only the msgpack types that output
    uses. Reads the file in large chunks and skips over record text without copying it."""
    fixed = {0xcc: ">B", 0xcd: ">H", 0xce: ">I", 0xcf: ">Q", 0xd0: ">b", 0xd1: ">h", 0xd2: ">i", 0xd3: ">q"}
    fixed = {k: (struct.Struct(v).unpack_from, struct.calcsize(v)) for k, v in fixed.items()}
    sizes = {0xd9: fixed[0xcc], 0xda: fixed[0xcd], 0xdb: fixed[0xce]}
    buf, base, i = b"", 0, 0            # buf holds the file from byte `base`; i indexes into buf

    def need(n):
        """Make buf hold at least n bytes from i; False at a clean end of file."""
        nonlocal buf, base, i
        if len(buf) - i >= n:
            return True
        fh.seek(base + i)
        base, buf, i = base + i, fh.read(max(n, chunk)), 0
        if len(buf) < n:
            if not buf and n == 1:
                return False
            raise BundleError("clp-s output ends in the middle of a result")
        return True

    def value():
        """(integer, None) for an integer, (None, (offset, length)) for a string."""
        nonlocal i
        need(1)
        b = buf[i]
        i += 1
        if b <= 0x7f:
            return b, None
        if b >= 0xe0:
            return b - 0x100, None
        if b in fixed:
            unpack, size = fixed[b]
            need(size)
            v = unpack(buf, i)[0]
            i += size
            return v, None
        if 0xa0 <= b <= 0xbf:
            length = b & 0x1f
        elif b in sizes:
            unpack, size = sizes[b]
            need(size)
            length = unpack(buf, i)[0]
            i += size
        else:
            raise BundleError(f"unexpected msgpack type 0x{b:02x} in clp-s output at byte {base + i - 1}")
        start = base + i
        i += length                      # may pass the end of buf; need() seeks past the text
        return None, (start, length)

    while need(1):
        if buf[i] != 0x95:
            raise BundleError(f"clp-s output: expected a 5-element array at byte {base + i}")
        i += 1
        value()
        _, text = value()
        value()
        value()
        position, _ = value()
        if text is None or position is None:
            raise BundleError("clp-s output: a result is not [timestamp, record, path, archive, position]")
        yield position, text[0], text[1]


def archive_record_counts(clp_s, archives_dir):
    """{archive id: records} for every archive in archives_dir."""
    proc = subprocess.run([clp_s, "s", "--count", "--experimental", archives_dir, "*"],
                          capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise BundleError(f"clp-s could not count the archives: {proc.stderr.strip()[-300:]}")
    return {r["archive_id"]: r["count"] for r in (json.loads(l) for l in proc.stdout.splitlines() if l.startswith("{"))}
