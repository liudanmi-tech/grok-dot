"""SQLite 存储（POC）。生产环境替换为 PostgreSQL（技术方案 3.1），表结构保持一致。"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT UNIQUE NOT NULL,
  ts TEXT NOT NULL,
  trace_id TEXT,
  bot_id TEXT,
  event_type TEXT NOT NULL,
  payload TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_trace ON audit_events(trace_id);

CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT PRIMARY KEY,
  bot_id TEXT, trace_id TEXT, task_id TEXT,
  action_class TEXT NOT NULL, tool TEXT NOT NULL,
  target TEXT, payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
  evidence TEXT, review TEXT,
  approver_role TEXT, backup_role TEXT, approver_id TEXT,
  state TEXT NOT NULL,
  created_at TEXT NOT NULL, escalate_at TEXT, expires_at TEXT,
  decided_at TEXT, decision_note TEXT, result TEXT
);

CREATE TABLE IF NOT EXISTS memories (
  memory_id TEXT PRIMARY KEY,
  namespace TEXT NOT NULL, type TEXT NOT NULL, content TEXT NOT NULL,
  source TEXT, created_by TEXT, status TEXT NOT NULL, level TEXT,
  expires_at TEXT, review_due_at TEXT, last_used_at TEXT,
  use_count INTEGER DEFAULT 0, version INTEGER DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id TEXT NOT NULL, version INTEGER NOT NULL, content TEXT,
  status TEXT, changed_by TEXT, reason TEXT, ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY,
  parent_task_id TEXT, source TEXT NOT NULL,
  requester TEXT, requester_scope TEXT,
  bot_id TEXT NOT NULL, goal TEXT NOT NULL, deadline TEXT,
  skill TEXT, plan_version INTEGER DEFAULT 0, steps TEXT DEFAULT '[]',
  status TEXT NOT NULL, progress_done INTEGER DEFAULT 0, progress_total INTEGER DEFAULT 0,
  cost_usd REAL DEFAULT 0, budget_usd REAL DEFAULT 0,
  replan_count INTEGER DEFAULT 0, trace_id TEXT NOT NULL,
  outputs TEXT DEFAULT '[]', approvals TEXT DEFAULT '[]', notes TEXT DEFAULT '[]', context TEXT DEFAULT '{}',
  feedback TEXT, stall_hinted INTEGER DEFAULT 0, retries INTEGER DEFAULT 0,
  card_id TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_activity_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS routine_runs (
  run_id TEXT PRIMARY KEY,
  bot_id TEXT NOT NULL, routine TEXT NOT NULL, scheduled_at TEXT NOT NULL,
  idempotency_key TEXT UNIQUE NOT NULL,
  state TEXT NOT NULL, attempt INTEGER DEFAULT 0,
  compensated INTEGER DEFAULT 0, task_id TEXT,
  started_at TEXT, finished_at TEXT, note TEXT
);

CREATE TABLE IF NOT EXISTS bus_messages (
  msg_id TEXT PRIMARY KEY,
  from_bot TEXT NOT NULL, to_bot TEXT NOT NULL, intent TEXT NOT NULL,
  payload_ref TEXT NOT NULL, scope TEXT, trace_id TEXT,
  created_at TEXT NOT NULL, read_at TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
  ref TEXT PRIMARY KEY, bot_id TEXT, kind TEXT, content TEXT NOT NULL, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drafts (
  draft_id TEXT PRIMARY KEY, kind TEXT NOT NULL, bot_id TEXT,
  payload TEXT NOT NULL, state TEXT NOT NULL, approval_id TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  card_id TEXT, channel TEXT NOT NULL, recipient TEXT NOT NULL,
  kind TEXT NOT NULL, card TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_outbox_card ON outbox(card_id) WHERE card_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS wa_conversations (
  conversation_id TEXT PRIMARY KEY,
  phone TEXT UNIQUE NOT NULL, dealer_id TEXT,
  lang TEXT, lang_source TEXT,
  last_inbound_at TEXT, opted_out INTEGER DEFAULT 0, human_mode INTEGER DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wa_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id TEXT NOT NULL, direction TEXT NOT NULL,
  wamid TEXT, text TEXT, status TEXT NOT NULL, kind TEXT,
  approval_id TEXT, task_id TEXT, flags TEXT,
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_wa_wamid ON wa_messages(wamid) WHERE wamid IS NOT NULL;
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);

CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, bot_id TEXT, model TEXT, trace_id TEXT,
  input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL
);
"""


class DB:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def transaction(self):
        return _Tx(self)


class _Tx:
    def __init__(self, db: DB):
        self.db = db

    def __enter__(self):
        self.db.lock.acquire()
        self.db._conn.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, exc_type, *_):
        try:
            self.db._conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.db.lock.release()
        return False


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def loads(s: str | None, default: Any = None) -> Any:
    if s is None or s == "":
        return default
    return json.loads(s)
