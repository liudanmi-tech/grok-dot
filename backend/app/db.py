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
CREATE TABLE IF NOT EXISTS bots (
  bot_id TEXT PRIMARY KEY, template TEXT NOT NULL, display_name TEXT, scope TEXT,
  state TEXT NOT NULL, created_by TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, bot_id TEXT, model TEXT, trace_id TEXT,
  input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL
);

-- 目标与节奏：KPI 树、周分解、晨会/晚会、团队群
CREATE TABLE IF NOT EXISTS kpis (
  kpi_id TEXT PRIMARY KEY, parent_id TEXT, title TEXT NOT NULL, metric TEXT NOT NULL, filter TEXT,
  baseline_daily REAL, uplift_pct REAL, start_date TEXT, end_date TEXT, owner TEXT, squad TEXT,
  status TEXT NOT NULL, created_by TEXT, created_at TEXT, claimed_at TEXT
);
CREATE TABLE IF NOT EXISTS kpi_weeks (
  kpi_id TEXT NOT NULL, week_no INTEGER NOT NULL, start_date TEXT, end_date TEXT, target_units REAL,
  items TEXT, brief TEXT, status TEXT NOT NULL, created_at TEXT, confirmed_by TEXT,
  PRIMARY KEY (kpi_id, week_no)
);
CREATE TABLE IF NOT EXISTS meetings (
  meeting_id TEXT PRIMARY KEY, kpi_id TEXT NOT NULL, kind TEXT NOT NULL, day TEXT NOT NULL, status TEXT NOT NULL,
  data TEXT, mission_task_id TEXT, host TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS skill_installs (
  template TEXT NOT NULL, pack_id TEXT NOT NULL, installed_by TEXT, installed_at TEXT, PRIMARY KEY (template, pack_id)
);
CREATE TABLE IF NOT EXISTS kb_docs (
  doc_id TEXT PRIMARY KEY, title TEXT NOT NULL, doc_type TEXT, visibility TEXT, country TEXT, lang TEXT, version INTEGER,
  approved INTEGER, text TEXT, created_by TEXT, created_at TEXT, updated_by TEXT, updated_at TEXT, approved_by TEXT,
  deleted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS field_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL, ts TEXT NOT NULL, promoter_id TEXT NOT NULL, promoter_name TEXT, store TEXT, region TEXT, country TEXT,
  sku TEXT, model TEXT, brand TEXT, units INTEGER, raw TEXT, conversation_id TEXT, source TEXT
);
CREATE TABLE IF NOT EXISTS channel_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kpi_id TEXT NOT NULL, ts TEXT NOT NULL, author TEXT, author_kind TEXT, kind TEXT, text TEXT, ref TEXT
);
"""


class DB:
    """同一套 SQL 跑在 SQLite（本地开发、测试）和 PostgreSQL（服务器）上。

    url 为 postgresql://... 时使用 PostgreSQL，否则视为 SQLite 文件路径（或 :memory:）。
    POC 为单进程：一个连接 + 进程内锁；审计追加在 PostgreSQL 上另加事务级咨询锁，保证哈希链串行。
    """

    def __init__(self, url: Path | str):
        self.url = str(url)
        self.is_pg = self.url.startswith(("postgresql://", "postgres://"))
        self._lock = threading.RLock()
        self._connect()
        with self._lock:
            if self.is_pg:
                self._conn.execute(PG_SCHEMA)
            else:
                if self.url != ":memory:":
                    self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.executescript(SCHEMA)

    def _connect(self) -> None:
        if self.is_pg:
            import psycopg
            from psycopg.rows import dict_row

            self._conn = psycopg.connect(self.url, autocommit=True, row_factory=dict_row)
        else:
            if self.url != ":memory:":
                Path(self.url).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.url, check_same_thread=False, isolation_level=None)
            self._conn.row_factory = sqlite3.Row

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def _sql(self, sql: str) -> str:
        return sql.replace("%", "%%").replace("?", "%s") if self.is_pg else sql

    def _run(self, sql: str, params: Iterable[Any]):
        try:
            return self._conn.execute(self._sql(sql), tuple(params))
        except Exception as e:  # PostgreSQL 连接断开时重连一次（事务中不重试）
            if self.is_pg and self._conn.closed and not getattr(self, "_in_tx", False):
                self._connect()
                return self._conn.execute(self._sql(sql), tuple(params))
            raise e

    def execute(self, sql: str, params: Iterable[Any] = ()):
        with self._lock:
            return self._run(sql, params)

    def insert(self, sql: str, params: Iterable[Any] = ()) -> int:
        """插入并返回自增主键 id。"""
        with self._lock:
            if self.is_pg:
                return self._run(sql + " RETURNING id", params).fetchone()["id"]
            return self._run(sql, params).lastrowid

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._run(sql, params).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def json_text(self, column: str, key: str) -> str:
        """取 JSON 文本列中的某个字段（两种数据库写法不同）。"""
        return f"({column}::jsonb->>'{key}')" if self.is_pg else f"json_extract({column},'$.{key}')"

    def serialize(self, key: int) -> None:
        """在当前事务内串行化（PostgreSQL 咨询锁，多进程 / 多副本也安全）。"""
        if self.is_pg:
            self._run("SELECT pg_advisory_xact_lock(?)", [key])

    def transaction(self):
        return _Tx(self)


class _Tx:
    def __init__(self, db: DB):
        self.db = db

    def __enter__(self):
        self.db.lock.acquire()
        self.db._run("BEGIN" if self.db.is_pg else "BEGIN IMMEDIATE", ())
        self.db._in_tx = True
        return self.db

    def __exit__(self, exc_type, *_):
        try:
            self.db._run("ROLLBACK" if exc_type else "COMMIT", ())
        finally:
            self.db._in_tx = False
            self.db.lock.release()
        return False


PG_SCHEMA = (SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
             .replace(" REAL", " DOUBLE PRECISION"))


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def loads(s: str | None, default: Any = None) -> Any:
    if s is None or s == "":
        return default
    return json.loads(s)
