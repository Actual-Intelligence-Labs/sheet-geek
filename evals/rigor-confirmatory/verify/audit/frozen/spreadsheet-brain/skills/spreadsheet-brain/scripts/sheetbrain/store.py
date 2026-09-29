"""The local index: machine only, never travels. Knows where every brain lives,
keeps private remarks, raw answers, backups, work state, and the cross-file
map. Location: ~/.spreadsheet-brain (override with SPREADSHEET_BRAIN_HOME).
Nothing here runs in the background; it is just files and one sqlite database.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import sqlite3

SYNC_MARKERS = ("Dropbox", "OneDrive", "iCloud", "CloudStorage", "Google Drive", "Box Sync")

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  brain_id TEXT PRIMARY KEY, path TEXT, name TEXT, kind TEXT, origin TEXT,
  project TEXT, tab_state TEXT, fingerprint TEXT, archetype TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS files_path ON files(path);
CREATE TABLE IF NOT EXISTS facts (
  brain_id TEXT, record_id TEXT, layer TEXT, record TEXT, travel TEXT, updated_at TEXT,
  PRIMARY KEY (brain_id, record_id, layer));
CREATE TABLE IF NOT EXISTS links (
  link_id TEXT PRIMARY KEY, from_brain TEXT, from_col TEXT, to_brain TEXT, to_col TEXT,
  to_name TEXT, rows_matched REAL, status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS answers (
  brain_id TEXT, qid TEXT, answer TEXT, at TEXT, PRIMARY KEY (brain_id, qid));
CREATE TABLE IF NOT EXISTS private (
  id TEXT PRIMARY KEY, brain_id TEXT, text TEXT, reason TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, brain_id TEXT, kind TEXT, detail TEXT, at TEXT);
"""


def now() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def today() -> str:
    return dt.date.today().isoformat()


def home() -> str:
    h = os.environ.get("SPREADSHEET_BRAIN_HOME") or os.path.join(os.path.expanduser("~"),
                                                                  ".spreadsheet-brain")
    os.makedirs(h, exist_ok=True)
    try:
        os.chmod(h, 0o700)
    except OSError:
        pass
    return h


def under_sync_root(path: str) -> bool:
    p = os.path.abspath(path)
    return any(m in p for m in SYNC_MARKERS)


class Store:
    def __init__(self, root: str | None = None):
        self.root = root or home()
        os.makedirs(self.root, exist_ok=True)
        self.db = sqlite3.connect(os.path.join(self.root, "index.sqlite"))
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self):
        self.db.close()

    # paths ---------------------------------------------------------------
    def work_dir(self, brain_id: str) -> str:
        d = os.path.join(self.root, "work", brain_id)
        os.makedirs(d, exist_ok=True)
        return d

    def backup_dir(self, brain_id: str) -> str:
        d = os.path.join(self.root, "backups", brain_id)
        os.makedirs(d, exist_ok=True)
        return d

    def cache_dir(self, brain_id: str) -> str:
        d = os.path.join(self.root, "cache", brain_id)
        os.makedirs(d, exist_ok=True)
        return d

    def project_dir(self, project: str) -> str:
        d = os.path.join(self.root, "projects", project)
        os.makedirs(d, exist_ok=True)
        return d

    @staticmethod
    def project_for(path: str) -> str:
        folder = os.path.dirname(os.path.abspath(path))
        slug = "".join(ch if ch.isalnum() else "-" for ch in os.path.basename(folder).lower())[:30]
        return f"{slug.strip('-') or 'root'}-{hashlib.sha256(folder.encode()).hexdigest()[:6]}"

    # files ---------------------------------------------------------------
    def brain_id_for(self, path: str, meta_id: str | None = None) -> tuple:
        """(brain_id, origin). meta_id is the id found inside the file, if any."""
        path = os.path.abspath(path)
        if meta_id:
            row = self.db.execute("SELECT origin, path FROM files WHERE brain_id=?", (meta_id,)).fetchone()
            if row:
                # a moved or renamed file takes its brain along; a saved copy next to the original does
                # not take the original's place, or the original would lose its answers
                if row[1] != path and not os.path.exists(row[1]):
                    self.db.execute("UPDATE files SET path=?, name=? WHERE brain_id=?",
                                    (path, os.path.basename(path), meta_id))
                    self.db.commit()
                return meta_id, row[0]
            return meta_id, "received"
        row = self.db.execute("SELECT brain_id, origin FROM files WHERE path=? "
                              "ORDER BY updated_at DESC LIMIT 1", (path,)).fetchone()
        if row:
            return row[0], row[1]
        new_id = secrets.token_hex(6)
        kind = "csv" if path.lower().endswith((".csv", ".tsv")) else "xlsx"
        self.upsert_file(new_id, path, kind, "own")      # remember it from first contact
        return new_id, "own"

    def upsert_file(self, brain_id: str, path: str, kind: str, origin: str, tab_state: str = "",
                    fingerprint: dict | None = None, archetype: str = ""):
        path = os.path.abspath(path)
        self.db.execute(
            "INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(brain_id) DO UPDATE SET "
            "path=excluded.path, name=excluded.name, kind=excluded.kind, origin=excluded.origin, "
            "project=excluded.project, tab_state=excluded.tab_state, "
            "fingerprint=excluded.fingerprint, archetype=excluded.archetype, "
            "updated_at=excluded.updated_at",
            (brain_id, path, os.path.basename(path), kind, origin, self.project_for(path), tab_state,
             json.dumps(fingerprint or {}), archetype, now()))
        self.db.commit()

    def file(self, brain_id: str) -> dict | None:
        row = self.db.execute("SELECT brain_id, path, name, kind, origin, project, tab_state, "
                              "fingerprint, archetype, updated_at FROM files WHERE brain_id=?",
                              (brain_id,)).fetchone()
        if not row:
            return None
        keys = ["brain_id", "path", "name", "kind", "origin", "project", "tab_state",
                "fingerprint", "archetype", "updated_at"]
        d = dict(zip(keys, row))
        d["fingerprint"] = json.loads(d["fingerprint"] or "{}")
        return d

    def files_in_project(self, project: str) -> list:
        rows = self.db.execute("SELECT brain_id FROM files WHERE project=? ORDER BY updated_at DESC",
                               (project,)).fetchall()
        return [self.file(r[0]) for r in rows]

    def files_under(self, folder: str) -> list:
        folder = os.path.abspath(folder)
        rows = self.db.execute("SELECT brain_id, path FROM files ORDER BY updated_at DESC").fetchall()
        return [self.file(b) for b, p in rows if p and os.path.abspath(p).startswith(folder + os.sep)]

    # facts ---------------------------------------------------------------
    def save_records(self, brain_id: str, records: list, layer: str = "own"):
        self.db.execute("DELETE FROM facts WHERE brain_id=? AND layer=?", (brain_id, layer))
        for r in records:
            travel = r.get("_travel", "file")
            rec = {k: v for k, v in r.items() if not k.startswith("_")}
            self.db.execute("INSERT OR REPLACE INTO facts VALUES (?,?,?,?,?,?)",
                            (brain_id, rec.get("id", ""), layer, json.dumps(rec), travel, now()))
        self.db.commit()

    def records(self, brain_id: str, layer: str = "own", travel: str | None = None) -> list:
        q = "SELECT record, travel FROM facts WHERE brain_id=? AND layer=?"
        args = [brain_id, layer]
        if travel:
            q += " AND travel=?"
            args.append(travel)
        return [dict(json.loads(r), _travel=t) for r, t in self.db.execute(q, args).fetchall()]

    # answers and private remarks ----------------------------------------
    def save_answer(self, brain_id: str, qid: str, answer: dict):
        self.db.execute("INSERT OR REPLACE INTO answers VALUES (?,?,?,?)",
                        (brain_id, qid, json.dumps(answer), now()))
        self.db.commit()

    def answers(self, brain_id: str) -> dict:
        return {q: json.loads(a) for q, a in self.db.execute(
            "SELECT qid, answer FROM answers WHERE brain_id=?", (brain_id,)).fetchall()}

    def add_private(self, brain_id: str, text: str, reason: str) -> str:
        pid = "p:" + hashlib.sha256((brain_id + text).encode()).hexdigest()[:10]
        self.db.execute("INSERT OR REPLACE INTO private VALUES (?,?,?,?,?)",
                        (pid, brain_id, text, reason, now()))
        self.db.commit()
        return pid

    def private(self, brain_id: str) -> list:
        return [{"id": i, "text": t, "reason": r, "at": a} for i, t, r, a in self.db.execute(
            "SELECT id, text, reason, at FROM private WHERE brain_id=?", (brain_id,)).fetchall()]

    def release_private(self, pid: str) -> dict | None:
        row = self.db.execute("SELECT brain_id, text, reason FROM private WHERE id=?", (pid,)).fetchone()
        if not row:
            return None
        self.db.execute("DELETE FROM private WHERE id=?", (pid,))
        self.db.commit()
        return {"brain_id": row[0], "text": row[1], "reason": row[2]}

    # links ---------------------------------------------------------------
    def save_link(self, from_brain: str, from_col: str, to_brain: str, to_col: str, to_name: str,
                  rows_matched: float, status: str):
        lid = "l:" + hashlib.sha256(f"{from_brain}|{from_col}|{to_brain}|{to_col}".encode()).hexdigest()[:10]
        self.db.execute("INSERT OR REPLACE INTO links VALUES (?,?,?,?,?,?,?,?,?)",
                        (lid, from_brain, from_col, to_brain, to_col, to_name, rows_matched, status,
                         now()))
        self.db.commit()
        return lid

    def links(self, brain_id: str) -> list:
        rows = self.db.execute("SELECT link_id, from_brain, from_col, to_brain, to_col, to_name, "
                               "rows_matched, status FROM links WHERE from_brain=? OR to_brain=?",
                               (brain_id, brain_id)).fetchall()
        keys = ["link_id", "from_brain", "from_col", "to_brain", "to_col", "to_name", "rows_matched",
                "status"]
        return [dict(zip(keys, r)) for r in rows]

    # events --------------------------------------------------------------
    def log(self, brain_id: str, kind: str, detail: str = ""):
        self.db.execute("INSERT INTO events (brain_id, kind, detail, at) VALUES (?,?,?,?)",
                        (brain_id, kind, detail[:2000], now()))
        self.db.commit()
        with open(os.path.join(self.root, "log.md"), "a", encoding="utf-8") as fh:
            fh.write(f"- {now()} {kind} {brain_id} {detail[:200]}\n")

    # work state ----------------------------------------------------------
    def state(self, brain_id: str) -> dict:
        p = os.path.join(self.work_dir(brain_id), "state.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        return {}

    def save_state(self, brain_id: str, state: dict):
        p = os.path.join(self.work_dir(brain_id), "state.json")
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1, default=str)
        os.replace(tmp, p)

    def write_json(self, brain_id: str, name: str, data) -> str:
        p = os.path.join(self.work_dir(brain_id), name)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, default=str)
        return p

    def prune_backups(self, brain_id: str, keep: int = 5):
        d = self.backup_dir(brain_id)
        files = sorted(os.listdir(d))
        for f in files[:-keep]:
            try:
                os.remove(os.path.join(d, f))
            except OSError:
                pass
