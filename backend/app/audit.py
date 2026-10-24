"""审计日志：追加写入 + 哈希链（技术方案 12.1 / 12.2）。

每条事件的 hash = SHA-256(prev_hash + 规范化 JSON)。任何一条被改动，verify() 都会在该处断链。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from .config import Clock, iso
from .db import DB, dumps, loads

GENESIS = "0" * 64


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


class AuditLog:
    def __init__(self, db: DB, clock: Clock, versions: dict | None = None):
        self.db = db
        self.clock = clock
        # 运行可复现（23.3）：每条事件附带当前生效的配置版本
        self.versions = versions or {}

    def append(self, event_type: str, *, bot_id: str | None = None, trace_id: str | None = None,
               versions: dict | None = None, **payload: Any) -> dict:
        body = {k: v for k, v in payload.items() if v is not None}
        if versions or self.versions:
            body["versions"] = {**self.versions, **(versions or {})}
        with self.db.transaction():
            last = self.db.one("SELECT hash FROM audit_events ORDER BY seq DESC LIMIT 1")
            prev = last["hash"] if last else GENESIS
            event = {
                "event_id": uuid.uuid4().hex,
                "ts": iso(self.clock.now()),
                "trace_id": trace_id,
                "bot_id": bot_id,
                "event_type": event_type,
                "payload": body,
            }
            h = hashlib.sha256((prev + canonical(event)).encode()).hexdigest()
            self.db.execute(
                "INSERT INTO audit_events(event_id, ts, trace_id, bot_id, event_type, payload, prev_hash, hash)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (event["event_id"], event["ts"], trace_id, bot_id, event_type, dumps(body), prev, h),
            )
        return {**event, "prev_hash": prev, "hash": h}

    def list(self, *, trace_id: str | None = None, bot_id: str | None = None, event_type: str | None = None,
             limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM audit_events WHERE 1=1"
        args: list[Any] = []
        for col, val in (("trace_id", trace_id), ("bot_id", bot_id), ("event_type", event_type)):
            if val:
                sql += f" AND {col} = ?"
                args.append(val)
        sql += " ORDER BY seq DESC LIMIT ?"
        args.append(limit)
        rows = self.db.query(sql, args)
        for r in rows:
            r["payload"] = loads(r["payload"], {})
        return rows

    def has_tool_call(self, trace_id: str, tool: str, since: str | None = None) -> bool:
        sql = ("SELECT 1 FROM audit_events WHERE trace_id=? AND event_type='tool.call' "
               "AND json_extract(payload,'$.tool')=?")
        args: list[Any] = [trace_id, tool]
        if since:
            sql += " AND ts >= ?"
            args.append(since)
        return self.db.one(sql + " LIMIT 1", args) is not None

    def verify(self) -> dict:
        """逐条重算哈希链，返回第一处断点。"""
        prev = GENESIS
        count = 0
        for r in self.db.query("SELECT * FROM audit_events ORDER BY seq ASC"):
            event = {
                "event_id": r["event_id"], "ts": r["ts"], "trace_id": r["trace_id"], "bot_id": r["bot_id"],
                "event_type": r["event_type"], "payload": loads(r["payload"], {}),
            }
            expect = hashlib.sha256((prev + canonical(event)).encode()).hexdigest()
            if r["prev_hash"] != prev or r["hash"] != expect:
                return {"ok": False, "checked": count, "broken_at_seq": r["seq"], "event_id": r["event_id"]}
            prev = r["hash"]
            count += 1
        return {"ok": True, "checked": count, "head": prev}
