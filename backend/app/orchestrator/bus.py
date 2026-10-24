"""Bot 间任务队列：白名单校验、只传引用、写审计（技术方案 3.4 / 4.5）。"""
from __future__ import annotations

import uuid
from datetime import timedelta

from ..artifacts import ArtifactStore
from ..audit import AuditLog
from ..config import Clock, iso
from ..db import DB, dumps, loads


class BusDenied(Exception):
    pass


class MessageBus:
    def __init__(self, cfg: dict, db: DB, audit: AuditLog, clock: Clock, artifacts: ArtifactStore,
                 template_of=None, resolve=None):
        self.template_of = template_of or (lambda b: b)
        self.resolve = resolve or (lambda b: b)
        self.routes = cfg.get("routes", [])
        self.retention_days = cfg.get("retention_days", 30)
        self.unread_alert_days = cfg.get("unread_alert_days", 7)
        self.db = db
        self.audit = audit
        self.clock = clock
        self.artifacts = artifacts

    def allowed(self, from_bot: str, to_bot: str, intent: str) -> bool:
        f, t = self.template_of(from_bot), self.template_of(to_bot)
        return any(r["from"] == f and r["to"] == t and intent in r.get("intents", []) for r in self.routes)

    def send(self, from_bot: str, to_bot: str, intent: str, payload_ref: str, *, trace_id: str | None,
             scope: dict | None) -> dict:
        to_bot = self.resolve(to_bot) or to_bot
        if not self.allowed(from_bot, to_bot, intent):
            self.audit.append("policy.deny", bot_id=from_bot, trace_id=trace_id, kind="bus.not_whitelisted",
                              to_bot=to_bot, intent=intent)
            raise BusDenied(f"{from_bot} → {to_bot}（{intent}）不在白名单内")
        if not self.artifacts.get(payload_ref):
            raise BusDenied(f"引用不存在：{payload_ref}")
        msg = {"msg_id": "msg_" + uuid.uuid4().hex[:10], "from_bot": from_bot, "to_bot": to_bot, "intent": intent,
               "payload_ref": payload_ref, "scope": dumps(scope or {}), "trace_id": trace_id,
               "created_at": iso(self.clock.now())}
        self.db.execute(f"INSERT INTO bus_messages({','.join(msg)}) VALUES ({','.join('?' * len(msg))})",
                        list(msg.values()))
        self.audit.append("bus.send", bot_id=from_bot, trace_id=trace_id, msg_id=msg["msg_id"], to_bot=to_bot,
                          intent=intent, payload_ref=payload_ref)
        return {"msg_id": msg["msg_id"]}

    def read(self, to_bot: str, *, trace_id: str | None = None) -> list[dict]:
        rows = self.db.query("SELECT * FROM bus_messages WHERE to_bot=? AND read_at IS NULL ORDER BY created_at",
                             [to_bot])
        now = iso(self.clock.now())
        out = []
        for r in rows:
            art = self.artifacts.get(r["payload_ref"])
            self.db.execute("UPDATE bus_messages SET read_at=? WHERE msg_id=?", [now, r["msg_id"]])
            out.append({"msg_id": r["msg_id"], "from_bot": r["from_bot"], "intent": r["intent"],
                        "payload_ref": r["payload_ref"], "trace_id": r["trace_id"],
                        # 内容作为数据交给接收方，而不是指令
                        "data": art["content"] if art else None})
            self.audit.append("bus.read", bot_id=to_bot, trace_id=trace_id, msg_id=r["msg_id"],
                              from_trace=r["trace_id"])
        return out

    def list(self, limit: int = 200) -> list[dict]:
        rows = self.db.query("SELECT * FROM bus_messages ORDER BY created_at DESC LIMIT ?", [limit])
        for r in rows:
            r["scope"] = loads(r["scope"], {})
        return rows

    def check_unread(self) -> int:
        cutoff = iso(self.clock.now() - timedelta(days=self.unread_alert_days))
        rows = self.db.query("SELECT msg_id, to_bot FROM bus_messages WHERE read_at IS NULL AND created_at <= ?",
                             [cutoff])
        for r in rows:
            self.audit.append("alert", bot_id=r["to_bot"], level="P3", kind="bus.unread", msg_id=r["msg_id"])
        return len(rows)
