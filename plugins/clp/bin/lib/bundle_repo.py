"""bundle_repo - match a session's commit and PR commands to what the repository actually holds.

The logs show that a command ran; whether it made a commit or a pull request, and which, is known for
sure only by the repository. This module reads the repository's own history at query time (nothing is
stored in the catalog, since the repository is the source of truth) and matches it to each command by
time: a commit or PR counts for a command when it was created between the command being issued (less 2 s,
since git keeps whole seconds) and its result coming back (plus 2 s).

Commits come from every commit object git still has (all refs and all reflogs), so commits that were
later rebased or amended away are found too. When several commits fall in one window (parallel agents,
a script committing several times), the subject line in the command's -m message picks one. How a
command matched is always reported:

  exact         the command's output printed this commit's sha
  time+subject  the only commit in the window whose subject is the command's message
  time          the only commit in the window (the message could not be compared, e.g. --amend --no-edit)
  ambiguous     several commits in the window and the message does not decide
  none          no commit in the window

PRs come from GitHub through the gh CLI; a single command can create many (a script looping over branches).
"""

import json
import re
import subprocess
from datetime import datetime, timedelta, timezone

import bundle as B

SLACK = timedelta(seconds=2)


def _utc(text):
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _run(args):
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise B.BundleError(f"{' '.join(args[:3])} failed: {proc.stderr.strip()[-300:]}")
    return proc.stdout


def find_repo(path):
    """The top of the git repository that holds `path`."""
    return _run(["git", "-C", path, "rev-parse", "--show-toplevel"]).strip()


def commits(repo, since, until):
    """{sha: (committer time, subject)} for every commit object git still has, created in [since, until]."""
    out = _run(["git", "-C", repo, "log", "--all", "--reflog", f"--since={since.isoformat()}",
                f"--until={until.isoformat()}", "--format=%H%x09%cI%x09%s"])
    found = {}
    for line in out.splitlines():
        sha, when, subject = (line.split("\t", 2) + [""])[:3]
        found[sha] = (_utc(when), subject)
    return found


def reflog_kinds(repo):
    """{sha: what created it}: the operation of its earliest reflog entry ("commit", "commit (amend)",
    "rebase (pick)", "cherry-pick", ...). Later entries (a push, a checkout) only moved a ref to it."""
    out = _run(["git", "-C", repo, "reflog", "--all", "--date=iso-strict", "--format=%H%x09%gd%x09%gs"])
    earliest = {}
    for line in out.splitlines():
        sha, selector, subject = (line.split("\t", 2) + ["", ""])[:3]
        m = re.search(r"@\{(.+)\}$", selector)
        if not m:
            continue
        when = _utc(m.group(1))
        if sha not in earliest or when < earliest[sha][0]:
            earliest[sha] = (when, subject.split(":")[0].strip() or "other")
    return {sha: kind for sha, (_, kind) in earliest.items()}


def pull_requests(repo):
    """[(number, url, created)] from GitHub, or raises when gh cannot list them."""
    out = _run(["gh", "pr", "list", "--repo", _github_name(repo), "--state", "all", "--limit", "1000",
                "--json", "number,url,createdAt"])
    return [(p["number"], p["url"], _utc(p["createdAt"])) for p in json.loads(out)]


def _github_name(repo):
    proc = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], cwd=repo,
                          capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise B.BundleError(f"gh cannot see this repository on GitHub: {proc.stderr.strip()[-200:]}")
    return proc.stdout.strip()


def commit_subjects(command):
    """The subject lines of the messages git commit commands give with -m (also -am, -qm, -mMSG, --message;
    quoted, or a $(cat <<EOF ... EOF) heredoc), or with -F - and a heredoc. Only flags that follow a
    `git commit` are read. Empty when there are none (--no-edit, -C, a message from a file)."""
    flag = re.compile(r"(?:(?<=\s)|^)(?:-[A-Za-z]*m\s*|--message[=\s]\s*)(\"(?:[^\"\\]|\\.)*\"|'[^']*')", re.S)
    heredoc = re.compile(r"<<-?\s*'?\"?(\w+)'?\"?\n(.*?)\n\s*\1\b", re.S)
    subjects = []
    for start in re.finditer(r"\bgit\s+commit\b", command):
        tail = command[start.end():]
        m = flag.search(tail)
        if m:
            text = m.group(1)[1:-1]
            inner = heredoc.search(text)
            text = (inner.group(2) if inner else text).strip()
        elif re.match(r"[^\n]*-F\s*-", tail) and heredoc.search(tail):
            text = heredoc.search(tail).group(2).strip()
        else:
            continue
        if text:
            subjects.append(text.splitlines()[0])
    return subjects


def commands_of(bundle_dir, db, uuids):
    """{event uuid: Bash command} for the given events, read from the archives by position."""
    wanted = {}
    for uuid in uuids:
        row = db.execute("SELECT kind, pos FROM events WHERE uuid = ? LIMIT 1", (uuid,)).fetchone()
        if row:
            wanted.setdefault(row[0], []).append((row[1], uuid))
    found = {}
    for kind, items in wanted.items():
        archive_id, expected = db.execute("SELECT archive_id, records FROM archives WHERE kind = ?", (kind,)).fetchone()
        archive = B.ArchiveRecords(B.resolve_clp_s(), f"{bundle_dir}/archives/{archive_id}", expected)
        try:
            for pos, uuid in items:
                record = next(archive.records(pos, 1))
                content = (record.get("message") or {}).get("content")
                for block in content if isinstance(content, list) else []:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash":
                        command = (block.get("input") or {}).get("command")
                        if isinstance(command, str) and re.search(r"\bgit commit\b|\bgh pr create\b", command):
                            found[uuid] = command
        finally:
            archive.close()
    return found


def match(bundle_dir, db, repo, github=True):
    """The commit and PR commands of a bundle matched to the repository's history (see the module doc)."""
    rows = db.execute("SELECT uuid, kind, agent_id, turn, ts, ended, action, sha FROM actions "
                      "WHERE action IN ('commit', 'pr') ORDER BY ts").fetchall()
    if not rows:
        return {"commits": [], "prs": [], "unattributed": {}, "commits_in_span": 0, "github": None}
    since = _utc(rows[0][4]) - timedelta(hours=1)
    until = _utc(max(r[5] or r[4] for r in rows)) + timedelta(hours=1)
    objects = commits(repo, since, until)
    commands = commands_of(bundle_dir, db, [r[0] for r in rows if r[6] == "commit"])
    out_commits, attributed = [], set()
    for uuid, kind, agent, turn, ts, ended, action, printed_sha in rows:
        if action != "commit":
            continue
        start, end = _utc(ts) - SLACK, _utc(ended or ts) + SLACK
        candidates = sorted((s for s, (t, _) in objects.items() if start <= t <= end), key=lambda s: objects[s][0])
        subjects = commit_subjects(commands.get(uuid, ""))
        by_subject = [s for s in candidates if any(objects[s][1] == x or objects[s][1][:60] == x[:60] for x in subjects)]
        if printed_sha and any(s.startswith(printed_sha) for s in candidates):
            how, chosen = "exact", [s for s in candidates if s.startswith(printed_sha)]
        elif len(by_subject) == 1:
            how, chosen = "time+subject", by_subject
        elif len(candidates) == 1:
            how, chosen = "time", candidates
        elif candidates:
            how, chosen = "ambiguous", candidates
        else:
            how, chosen = "none", []
        if how in ("exact", "time+subject", "time"):
            attributed.update(chosen)
        out_commits.append({"uuid": uuid, "kind": kind, "agent_id": agent, "turn": turn, "ts": ts, "match": how,
                            "commits": [{"sha": s, "subject": objects[s][1]} for s in chosen]})
    kinds = reflog_kinds(repo)
    unattributed = {}
    for sha in objects:
        if sha not in attributed:
            k = kinds.get(sha, "not in a reflog")
            unattributed[k] = unattributed.get(k, 0) + 1
    out_prs, gh_note = [], None
    pr_rows = [r for r in rows if r[6] == "pr"]
    if pr_rows and github:
        try:
            prs = pull_requests(repo)
            for uuid, kind, agent, turn, ts, ended, action, _ in pr_rows:
                start, end = _utc(ts) - SLACK, _utc(ended or ts) + SLACK
                made = sorted((n, url) for n, url, t in prs if start <= t <= end)
                out_prs.append({"uuid": uuid, "kind": kind, "agent_id": agent, "turn": turn, "ts": ts,
                                "prs": [url for _, url in made]})
            gh_note = f"{sum(len(p['prs']) for p in out_prs)} PRs created by {len(pr_rows)} commands"
        except B.BundleError as err:
            gh_note = f"GitHub not read: {err}"
    elif pr_rows:
        gh_note = "GitHub not read (--no-github)"
    return {"commits": out_commits, "prs": out_prs, "unattributed": unattributed, "commits_in_span": len(objects),
            "github": gh_note}
