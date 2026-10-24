"""记忆服务（技术方案 9 / 23.9）。

- 三类岗位记忆：preference / method / summary；外加对象偏好 counterpart_preference
- 命名空间：bot/<id>、user/<员工>、dealer/<id>、promoter/<id>
- 写入拦截：业务事实（价格、库存、销量数字）、个人信息、疑似指令一律拒绝
- 可查、可改、可删，每次变更留历史
"""
from __future__ import annotations

import re
import uuid
from datetime import timedelta

from .audit import AuditLog
from .config import Clock, iso, parse_iso
from .db import DB
from .gateway.redact import redact

TYPES = {"preference", "method", "summary", "counterpart_preference"}

FACT_PATTERNS = [
    re.compile(r"(₦|NGN|naira|奈拉)\s*[\d,.]+", re.I),
    re.compile(r"[\d,.]+\s*(₦|NGN|naira|奈拉|元|美元|台|部|units?|pcs)", re.I),
    re.compile(r"(库存|销量|价格|售价|出货|stock|inventory|sold|price)[^。\n]{0,12}\d", re.I),
]
INSTRUCTION_PATTERNS = [
    re.compile(p, re.I) for p in (
        r"以后(都|一律|每次)", r"从现在(开始|起)", r"忽略(之前|以上|所有)", r"ignore (all|previous|the above)",
        r"always (give|offer|approve|send)", r"(给|打)\s*\d+\s*折", r"不用(审批|审核)",
    )
]


class MemoryRejected(Exception):
    pass


def _tokens(text: str) -> set[str]:
    words = set(re.findall(r"[A-Za-z0-9]{2,}", text.lower()))
    cjk = re.findall(r"[一-鿿]", text)
    words |= {a + b for a, b in zip(cjk, cjk[1:])}
    return words


class MemoryService:
    def __init__(self, db: DB, audit: AuditLog, clock: Clock, summary_ttl_days: int = 90, review_days: int = 90):
        self.db = db
        self.audit = audit
        self.clock = clock
        self.summary_ttl = timedelta(days=summary_ttl_days)
        self.review = timedelta(days=review_days)

    def check(self, content: str) -> None:
        for p in FACT_PATTERNS:
            if p.search(content):
                raise MemoryRejected("业务事实（价格、库存、销量等数字）不写入记忆，请实时查询业务系统")
        _, _, hits = redact(content)
        if hits:
            raise MemoryRejected(f"内容包含个人信息：{', '.join(hits)}")
        for p in INSTRUCTION_PATTERNS:
            if p.search(content):
                raise MemoryRejected("内容像是一条指令而不是偏好或方法，需人工确认后由运营录入")

    def propose(self, *, namespace: str, type: str, content: str, source: str | None = None,
                created_by: str = "bot", bot_id: str | None = None, level: str = "internal",
                trace_id: str | None = None) -> dict:
        if type not in TYPES:
            raise MemoryRejected(f"未知记忆类型 {type}")
        if len(content) > 1000:
            raise MemoryRejected("记忆正文不超过 1000 字")
        try:
            self.check(content)
        except MemoryRejected as e:
            self.audit.append("memory.blocked", bot_id=bot_id, trace_id=trace_id, namespace=namespace,
                              reason=str(e), preview=content[:80])
            raise
        now = self.clock.now()
        # 方法类须智能体运营确认后生效（9.1）；人工在控制台创建的直接生效
        status = "proposed" if (type == "method" and created_by == "bot") else "active"
        mem = {
            "memory_id": "mem_" + uuid.uuid4().hex[:12],
            "namespace": namespace, "type": type, "content": content, "source": source,
            "created_by": created_by, "status": status, "level": level,
            "expires_at": iso(now + self.summary_ttl) if type == "summary" else None,
            "review_due_at": iso(now + self.review) if type in ("preference", "method") else None,
            "created_at": iso(now), "updated_at": iso(now),
        }
        cols = ",".join(mem)
        self.db.execute(f"INSERT INTO memories({cols}) VALUES ({','.join('?' * len(mem))})", list(mem.values()))
        self._history(mem["memory_id"], 1, content, status, created_by, "created")
        self.audit.append("memory.write", bot_id=bot_id, trace_id=trace_id, memory_id=mem["memory_id"],
                          namespace=namespace, type=type, status=status)
        return self.get(mem["memory_id"])

    def get(self, memory_id: str) -> dict | None:
        return self.db.one("SELECT * FROM memories WHERE memory_id=?", [memory_id])

    def list(self, namespace: str | None = None, status: str | None = None, q: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM memories WHERE status != 'deleted'", []
        if namespace:
            sql += " AND namespace = ?"
            args.append(namespace)
        if status:
            sql += " AND status = ?"
            args.append(status)
        if q:
            sql += " AND content LIKE ?"
            args.append(f"%{q}%")
        return self.db.query(sql + " ORDER BY updated_at DESC", args)

    def recall(self, namespaces: list[str], query: str, k: int = 8, max_level: str | None = None) -> list[dict]:
        """只在允许的命名空间内召回（服务端过滤，不依赖模型自觉）。"""
        if not namespaces:
            return []
        now = iso(self.clock.now())
        rows = self.db.query(
            f"SELECT * FROM memories WHERE status='active' AND namespace IN ({','.join('?' * len(namespaces))})"
            " AND (expires_at IS NULL OR expires_at > ?)",
            [*namespaces, now],
        )
        if max_level == "external":
            rows = [r for r in rows if r.get("level") == "external"]
        qt = _tokens(query)
        scored = sorted(rows, key=lambda r: len(qt & _tokens(r["content"])), reverse=True)[:k]
        for r in scored:
            self.db.execute("UPDATE memories SET last_used_at=?, use_count=use_count+1 WHERE memory_id=?",
                            [now, r["memory_id"]])
        return scored

    def edit(self, memory_id: str, *, actor: str, reason: str, content: str | None = None,
             status: str | None = None, expires_at: str | None = None) -> dict:
        if not reason:
            raise MemoryRejected("修改记忆须填写原因")
        mem = self.get(memory_id)
        if not mem or mem["status"] == "deleted":
            raise KeyError(memory_id)
        if content is not None:
            self.check(content)
        new = {
            "content": content if content is not None else mem["content"],
            "status": status or mem["status"],
            "expires_at": expires_at if expires_at is not None else mem["expires_at"],
            "version": mem["version"] + 1,
            "updated_at": iso(self.clock.now()),
        }
        self.db.execute(
            "UPDATE memories SET content=?, status=?, expires_at=?, version=?, updated_at=? WHERE memory_id=?",
            [*new.values(), memory_id],
        )
        self._history(memory_id, new["version"], new["content"], new["status"], actor, reason)
        self.audit.append("memory.edit", memory_id=memory_id, actor=actor, reason=reason, status=new["status"])
        return self.get(memory_id)

    def approve(self, memory_id: str, actor: str) -> dict:
        return self.edit(memory_id, actor=actor, reason="运营确认方法类记忆", status="active")

    def delete(self, memory_id: str, *, actor: str, reason: str, hard: bool = False) -> None:
        mem = self.get(memory_id)
        if not mem:
            raise KeyError(memory_id)
        content = "[已删除]" if hard else mem["content"]
        self.db.execute("UPDATE memories SET status='deleted', content=?, updated_at=? WHERE memory_id=?",
                        [content, iso(self.clock.now()), memory_id])
        if hard:  # 个人数据删除请求：历史正文一并清除，只保留“已删除”记录
            self.db.execute("UPDATE memory_history SET content='[已删除]' WHERE memory_id=?", [memory_id])
        self._history(memory_id, mem["version"] + 1, content, "deleted", actor, reason)
        self.audit.append("memory.delete", memory_id=memory_id, actor=actor, reason=reason, hard=hard)

    def delete_namespace(self, namespace: str, *, actor: str, reason: str) -> int:
        rows = self.list(namespace=namespace)
        for r in rows:
            self.delete(r["memory_id"], actor=actor, reason=reason, hard=True)
        return len(rows)

    def history(self, memory_id: str) -> list[dict]:
        return self.db.query("SELECT * FROM memory_history WHERE memory_id=? ORDER BY id", [memory_id])

    def expire(self) -> int:
        now = iso(self.clock.now())
        rows = self.db.query("SELECT memory_id FROM memories WHERE status='active' AND expires_at IS NOT NULL"
                             " AND expires_at <= ?", [now])
        for r in rows:
            self.edit(r["memory_id"], actor="system", reason="摘要类记忆到期", status="expired")
        return len(rows)

    def _history(self, memory_id, version, content, status, actor, reason) -> None:
        self.db.execute(
            "INSERT INTO memory_history(memory_id, version, content, status, changed_by, reason, ts) VALUES (?,?,?,?,?,?,?)",
            [memory_id, version, content, status, actor, reason, iso(self.clock.now())],
        )

    @staticmethod
    def is_expired(mem: dict, now) -> bool:
        return bool(mem.get("expires_at")) and parse_iso(mem["expires_at"]) <= now
