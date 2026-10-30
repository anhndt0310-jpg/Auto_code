"""
store.py — Persistent storage (SQLite, standard library only).

Keeps, per user:
  * runs          — full pipeline state (spec, files, stage, token stats...) = checkpoints
  * events        — log/error events of each run (so a reconnecting client can replay them)
  * conversations / messages — chat history (+ attached files)

Where the data lives:  $DATA_DIR/agent.db   (default: ./data/agent.db)
Put DATA_DIR in a synced folder (OneDrive / Dropbox / Google Drive) to carry history
between machines — but only run the server on ONE machine at a time.

The API key is never stored. Users are identified by a hash of the API key
(or of an optional "profile" sync code).
"""

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR = Path(os.environ.get("DATA_DIR") or (Path(__file__).parent / "data"))
DB_PATH = DATA_DIR / "agent.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id   TEXT PRIMARY KEY,
    user_id  TEXT NOT NULL,
    title    TEXT NOT NULL DEFAULT '',
    status   TEXT NOT NULL DEFAULT 'running',
    created  REAL NOT NULL,
    updated  REAL NOT NULL,
    state    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_user ON runs(user_id, updated DESC);

CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL,
    seq    INTEGER NOT NULL,
    ts     REAL NOT NULL,
    data   TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE IF NOT EXISTS conversations (
    conv_id  TEXT PRIMARY KEY,
    user_id  TEXT NOT NULL,
    title    TEXT NOT NULL DEFAULT '',
    files    TEXT NOT NULL DEFAULT '{}',
    created  REAL NOT NULL,
    updated  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, updated DESC);

CREATE TABLE IF NOT EXISTS messages (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id TEXT NOT NULL,
    role    TEXT NOT NULL,
    content TEXT NOT NULL,
    model   TEXT NOT NULL DEFAULT '',
    ts      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conv_id, id);
"""


def user_id_for(secret: str) -> str:
    """Stable, non-reversible user id derived from an API key or sync code."""
    return hashlib.sha256(("aca1:" + secret).encode("utf-8")).hexdigest()[:24]


class Store:
    """Thread-safe SQLite wrapper. Methods are blocking: call via asyncio.to_thread."""

    def __init__(self, path: Path = DB_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(
            str(path), check_same_thread=False, isolation_level=None, timeout=30
        )
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(SCHEMA)

    # ── helpers ─────────────────────────────────────────────────────────────
    def _all(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def _one(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, params).fetchone()

    def _run(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            return self._db.execute(sql, params).rowcount

    # ── runs ────────────────────────────────────────────────────────────────
    def create_run(self, run_id: str, user_id: str, title: str, state_json: str) -> None:
        now = time.time()
        self._run(
            "INSERT INTO runs(run_id,user_id,title,status,created,updated,state) "
            "VALUES(?,?,?,?,?,?,?)",
            (run_id, user_id, title[:200], "running", now, now, state_json),
        )

    def save_state(self, run_id: str, state_json: str, status: Optional[str] = None) -> None:
        now = time.time()
        if status:
            self._run("UPDATE runs SET state=?, status=?, updated=? WHERE run_id=?",
                      (state_json, status, now, run_id))
        else:
            self._run("UPDATE runs SET state=?, updated=? WHERE run_id=?",
                      (state_json, now, run_id))

    def set_status(self, run_id: str, status: str) -> None:
        self._run("UPDATE runs SET status=?, updated=? WHERE run_id=?",
                  (status, time.time(), run_id))

    def get_run(self, run_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        row = self._one("SELECT * FROM runs WHERE run_id=?", (run_id,))
        if not row or (user_id is not None and row["user_id"] != user_id):
            return None
        d = dict(row)
        d["state"] = json.loads(d["state"])
        return d

    def list_runs(self, user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self._all(
            "SELECT run_id,title,status,created,updated FROM runs "
            "WHERE user_id=? ORDER BY updated DESC LIMIT ?", (user_id, limit))
        return [dict(r) for r in rows]

    def delete_run(self, run_id: str, user_id: str) -> bool:
        n = self._run("DELETE FROM runs WHERE run_id=? AND user_id=?", (run_id, user_id))
        if n:
            self._run("DELETE FROM events WHERE run_id=?", (run_id,))
        return bool(n)

    def mark_interrupted(self) -> int:
        """Called at startup: anything still 'running' died with the previous process."""
        return self._run("UPDATE runs SET status='interrupted' WHERE status='running'")

    # ── events ──────────────────────────────────────────────────────────────
    def add_event(self, run_id: str, seq: int, event: dict) -> None:
        self._run("INSERT OR REPLACE INTO events(run_id,seq,ts,data) VALUES(?,?,?,?)",
                  (run_id, seq, time.time(), json.dumps(event, ensure_ascii=False)))

    def get_events(self, run_id: str, after_seq: int = 0, limit: int = 5000) -> List[dict]:
        rows = self._all(
            "SELECT data FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?",
            (run_id, after_seq, limit))
        return [json.loads(r["data"]) for r in rows]

    def max_seq(self, run_id: str) -> int:
        row = self._one("SELECT COALESCE(MAX(seq),0) AS m FROM events WHERE run_id=?", (run_id,))
        return int(row["m"]) if row else 0

    # ── conversations ───────────────────────────────────────────────────────
    def create_conversation(self, user_id: str, title: str, files: Optional[dict] = None) -> str:
        conv_id = uuid.uuid4().hex
        now = time.time()
        self._run(
            "INSERT INTO conversations(conv_id,user_id,title,files,created,updated) "
            "VALUES(?,?,?,?,?,?)",
            (conv_id, user_id, title[:100], json.dumps(files or {}, ensure_ascii=False), now, now))
        return conv_id

    def get_conversation(self, conv_id: str, user_id: str,
                         with_messages: bool = True) -> Optional[Dict[str, Any]]:
        row = self._one("SELECT * FROM conversations WHERE conv_id=? AND user_id=?",
                        (conv_id, user_id))
        if not row:
            return None
        d = dict(row)
        d["files"] = json.loads(d["files"] or "{}")
        if with_messages:
            msgs = self._all(
                "SELECT role,content,model,ts FROM messages WHERE conv_id=? ORDER BY id",
                (conv_id,))
            d["messages"] = [dict(m) for m in msgs]
        return d

    def set_conversation_files(self, conv_id: str, files: dict) -> None:
        self._run("UPDATE conversations SET files=?, updated=? WHERE conv_id=?",
                  (json.dumps(files, ensure_ascii=False), time.time(), conv_id))

    def add_message(self, conv_id: str, role: str, content: str, model: str = "") -> None:
        now = time.time()
        self._run("INSERT INTO messages(conv_id,role,content,model,ts) VALUES(?,?,?,?,?)",
                  (conv_id, role, content, model, now))
        self._run("UPDATE conversations SET updated=? WHERE conv_id=?", (now, conv_id))

    def recent_messages(self, conv_id: str, limit: int = 40) -> List[Dict[str, str]]:
        rows = self._all(
            "SELECT role,content FROM messages WHERE conv_id=? ORDER BY id DESC LIMIT ?",
            (conv_id, limit))
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def list_conversations(self, user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self._all(
            "SELECT conv_id,title,created,updated FROM conversations "
            "WHERE user_id=? ORDER BY updated DESC LIMIT ?", (user_id, limit))
        return [dict(r) for r in rows]

    def delete_conversation(self, conv_id: str, user_id: str) -> bool:
        n = self._run("DELETE FROM conversations WHERE conv_id=? AND user_id=?",
                      (conv_id, user_id))
        if n:
            self._run("DELETE FROM messages WHERE conv_id=?", (conv_id,))
        return bool(n)

    # ── export / import (manual backup, moving between machines) ────────────
    def export_user(self, user_id: str) -> Dict[str, Any]:
        runs = []
        for r in self._all("SELECT * FROM runs WHERE user_id=?", (user_id,)):
            d = dict(r)
            d["state"] = json.loads(d["state"])
            runs.append(d)
        convs = []
        for c in self._all("SELECT conv_id FROM conversations WHERE user_id=?", (user_id,)):
            conv = self.get_conversation(c["conv_id"], user_id)
            if conv:
                convs.append(conv)
        return {"version": 1, "exported": time.time(), "runs": runs, "conversations": convs}

    def import_user(self, user_id: str, data: Dict[str, Any]) -> Dict[str, int]:
        """Merge an export into this user's history. Existing ids are left untouched."""
        n_runs = n_convs = 0
        for r in data.get("runs", []):
            if not isinstance(r, dict) or not r.get("run_id") or "state" not in r:
                continue
            if self._one("SELECT 1 FROM runs WHERE run_id=?", (r["run_id"],)):
                continue
            status = r.get("status", "stopped")
            if status == "running":
                status = "interrupted"
            self._run(
                "INSERT INTO runs(run_id,user_id,title,status,created,updated,state) "
                "VALUES(?,?,?,?,?,?,?)",
                (r["run_id"], user_id, str(r.get("title", ""))[:200], status,
                 float(r.get("created", time.time())), float(r.get("updated", time.time())),
                 json.dumps(r["state"], ensure_ascii=False)))
            n_runs += 1
        for c in data.get("conversations", []):
            if not isinstance(c, dict) or not c.get("conv_id"):
                continue
            if self._one("SELECT 1 FROM conversations WHERE conv_id=?", (c["conv_id"],)):
                continue
            self._run(
                "INSERT INTO conversations(conv_id,user_id,title,files,created,updated) "
                "VALUES(?,?,?,?,?,?)",
                (c["conv_id"], user_id, str(c.get("title", ""))[:100],
                 json.dumps(c.get("files") or {}, ensure_ascii=False),
                 float(c.get("created", time.time())), float(c.get("updated", time.time()))))
            for m in c.get("messages", []):
                self._run(
                    "INSERT INTO messages(conv_id,role,content,model,ts) VALUES(?,?,?,?,?)",
                    (c["conv_id"], str(m.get("role", "user")), str(m.get("content", "")),
                     str(m.get("model", "")), float(m.get("ts", time.time()))))
            n_convs += 1
        return {"runs": n_runs, "conversations": n_convs}


_store: Optional[Store] = None


def get_store() -> Store:
    """Lazily-created process-wide store (so importing the module has no side effects)."""
    global _store
    if _store is None:
        _store = Store()
    return _store
