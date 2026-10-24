"""审批服务：哈希绑定、状态机、升级与过期、自动预审、批量批准（技术方案 8.3–8.7）。"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import timedelta
from typing import Any, Callable

from .audit import AuditLog, canonical
from .channels.outbox import Outbox
from .config import Clock, iso, parse_iso
from .db import DB, dumps, loads
from .policy import PolicyEngine

OPEN_STATES = ("pending", "escalated")


class ApprovalError(Exception):
    pass


def payload_hash(action_class: str, tool: str, target: Any, payload: dict) -> str:
    return hashlib.sha256(canonical({"a": action_class, "t": tool, "g": target, "p": payload}).encode()).hexdigest()


# ---------- 自动预审（8.7）：POC 为规则版，接口预留给独立审查模型 ----------
NUM = re.compile(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?%?")
COMMIT_WORDS = re.compile(r"(保证|承诺|一定会|guarantee|promise|will deliver by|assured)", re.I)
DEALER_ID = re.compile(r"\bD\d{4}\b")


class RuleReviewer:
    version = "rule-reviewer@1"

    def review(self, action_class: str, target: Any, payload: dict, evidence_texts: list[str]) -> dict:
        issues: list[dict] = []
        text = " ".join(str(v) for k, v in payload.items() if isinstance(v, (str, int, float)) and k != "draft_id")
        preview = payload.get("_preview") or ""
        text = f"{text} {preview}"
        evidence = " ".join(evidence_texts)
        outward = action_class.startswith("send:")
        nums = {n.rstrip("%").replace(",", "") for n in NUM.findall(text) if len(n.replace(",", "")) >= 3 or n.endswith("%")}
        ev_nums = {n.rstrip("%").replace(",", "") for n in NUM.findall(evidence)}
        missing = sorted(nums - ev_nums)
        if missing:
            issues.append({"severity": "reject" if outward else "attention",
                           "msg": f"以下数字在引用依据中找不到出处：{', '.join(missing[:5])}"})
        if outward and COMMIT_WORDS.search(text):
            issues.append({"severity": "reject", "msg": "出现承诺性措辞"})
        if outward:
            others = {d for d in DEALER_ID.findall(text) if str(target or "").find(d) < 0}
            if others:
                issues.append({"severity": "reject", "msg": f"回复中出现其他经销商标识：{', '.join(sorted(others))}"})
        if not payload.get("evidence_refs") and not evidence_texts:
            issues.append({"severity": "attention", "msg": "未附依据"})
        pct = payload.get("change_pct")
        if isinstance(pct, (int, float)) and abs(pct) > 15:
            issues.append({"severity": "attention", "msg": f"调整幅度 {pct}% 超过 15%（high_impact）"})
        level = "reject" if any(i["severity"] == "reject" for i in issues) else ("attention" if issues else "low")
        return {"level": level, "issues": issues, "reviewer": self.version}


class ApprovalService:
    def __init__(self, db: DB, audit: AuditLog, clock: Clock, policy: PolicyEngine, outbox: Outbox,
                 reviewer: RuleReviewer | None = None):
        self.db = db
        self.audit = audit
        self.clock = clock
        self.policy = policy
        self.outbox = outbox
        self.reviewer = reviewer or RuleReviewer()
        self.executor: Callable[[dict], dict] | None = None  # 由工具代理注入：执行已批准动作
        self.listeners: list[Callable[[dict], None]] = []   # 审批结束回调（任务恢复、记忆候选等）

    # ---------- 创建 ----------
    def create(self, *, bot_id: str, action_class: str, tool: str, target: Any, payload: dict,
               evidence: list[dict] | None = None, trace_id: str | None = None, task_id: str | None = None,
               scope: dict | None = None) -> dict:
        evidence = evidence or []
        review = self.reviewer.review(action_class, target, payload, [e.get("text", "") for e in evidence])
        approver = self.policy.approver_for(action_class)
        ttl = self.policy.ttl_for(action_class)
        now = self.clock.now()
        rec = {
            "approval_id": "apr_" + uuid.uuid4().hex[:12],
            "bot_id": bot_id, "trace_id": trace_id, "task_id": task_id,
            "action_class": action_class, "tool": tool, "target": dumps(target),
            "payload": dumps({"args": payload, "scope": scope or {}}),
            "payload_hash": payload_hash(action_class, tool, target, payload),
            "evidence": dumps(evidence), "review": dumps(review),
            "approver_role": approver.get("role"), "backup_role": approver.get("backup"),
            "state": "returned" if review["level"] == "reject" else "pending",
            "created_at": iso(now),
            "escalate_at": iso(now + timedelta(minutes=ttl.get("escalate_minutes", ttl["minutes"]))),
            "expires_at": iso(now + timedelta(minutes=ttl["minutes"])),
        }
        self.db.execute(f"INSERT INTO approvals({','.join(rec)}) VALUES ({','.join('?' * len(rec))})", list(rec.values()))
        self.audit.append("approval.create", bot_id=bot_id, trace_id=trace_id, approval_id=rec["approval_id"],
                          action_class=action_class, tool=tool, review_level=review["level"], state=rec["state"])
        out = self.get(rec["approval_id"])
        if rec["state"] == "pending":
            self._card(out, recipient=f"role:{rec['approver_role']}")
        self._notify(out)
        return out

    # ---------- 读取 ----------
    def get(self, approval_id: str) -> dict | None:
        r = self.db.one("SELECT * FROM approvals WHERE approval_id=?", [approval_id])
        return self._decode(r) if r else None

    def list(self, state: str | None = None, role: str | None = None, limit: int = 200) -> list[dict]:
        sql, args = "SELECT * FROM approvals WHERE 1=1", []
        if state:
            states = state.split(",")
            sql += f" AND state IN ({','.join('?' * len(states))})"
            args += states
        if role:
            sql += " AND (approver_role=? OR (backup_role=? AND state='escalated'))"
            args += [role, role]
        return [self._decode(r) for r in self.db.query(sql + " ORDER BY created_at DESC LIMIT ?", [*args, limit])]

    @staticmethod
    def _decode(r: dict) -> dict:
        r = dict(r)
        for k in ("target", "payload", "evidence", "review", "result"):
            r[k] = loads(r.get(k), None)
        return r

    # ---------- 决定 ----------
    def _authorize(self, rec: dict, actor: dict) -> None:
        roles = set(actor.get("roles", []))
        ok = rec["approver_role"] in roles or (rec["state"] == "escalated" and rec["backup_role"] in roles)
        if not ok:
            raise ApprovalError(f"{actor.get('id')} 不具备审批角色 {rec['approver_role']}")

    def decide(self, approval_id: str, *, actor: dict, decision: str, edited_args: dict | None = None,
               note: str | None = None) -> dict:
        rec = self.get(approval_id)
        if not rec:
            raise ApprovalError("审批单不存在")
        if rec["state"] not in OPEN_STATES:
            raise ApprovalError(f"审批单状态为 {rec['state']}，不能再处理")
        if parse_iso(rec["expires_at"]) <= self.clock.now():
            self._set(approval_id, "expired")
            raise ApprovalError("审批单已过期")
        self._authorize(rec, actor)
        now = iso(self.clock.now())
        args = rec["payload"]["args"]

        if decision == "reject":
            if not note:
                raise ApprovalError("拒绝须填写原因")
            self.db.execute("UPDATE approvals SET state='rejected', approver_id=?, decided_at=?, decision_note=? "
                            "WHERE approval_id=?", [actor["id"], now, note, approval_id])
            self.audit.append("approval.decide", bot_id=rec["bot_id"], trace_id=rec["trace_id"],
                              approval_id=approval_id, decision="reject", actor=actor["id"], note=note)
            return self._finish(approval_id)

        if decision == "edit":
            if not edited_args:
                raise ApprovalError("修改后批准须提供修改后的内容")
            new_hash = payload_hash(rec["action_class"], rec["tool"], rec["target"], edited_args)
            self.db.execute("UPDATE approvals SET payload=?, payload_hash=? WHERE approval_id=?",
                            [dumps({"args": edited_args, "scope": rec["payload"]["scope"]}), new_hash, approval_id])
            self.audit.append("approval.decide", bot_id=rec["bot_id"], trace_id=rec["trace_id"],
                              approval_id=approval_id, decision="edit", actor=actor["id"],
                              diff={"before": args, "after": edited_args})
            rec = self.get(approval_id)
            args = edited_args
        elif decision == "approve":
            # 哈希绑定：批准的是“这条内容”，存储内容被改动则失效
            if payload_hash(rec["action_class"], rec["tool"], rec["target"], args) != rec["payload_hash"]:
                self.db.execute("UPDATE approvals SET state='failed', decided_at=?, decision_note=? WHERE approval_id=?",
                                [now, "内容哈希不一致，审批失效", approval_id])
                self.audit.append("alert", level="P1", kind="approval.hash_mismatch", approval_id=approval_id)
                self._finish(approval_id)
                raise ApprovalError("内容哈希不一致，审批失效")
            self.audit.append("approval.decide", bot_id=rec["bot_id"], trace_id=rec["trace_id"],
                              approval_id=approval_id, decision="approve", actor=actor["id"])
        else:
            raise ApprovalError(f"未知决定 {decision}")

        self.db.execute("UPDATE approvals SET approver_id=?, decided_at=?, decision_note=? WHERE approval_id=?",
                        [actor["id"], now, note or decision, approval_id])
        return self._execute(approval_id, args, rec)

    def batch_approve(self, ids: list[str], *, actor: dict) -> list[dict]:
        out = []
        for aid in ids:
            rec = self.get(aid)
            if not rec or (rec.get("review") or {}).get("level") != "low":
                raise ApprovalError(f"{aid} 不是低风险，不能批量批准")
        for aid in ids:
            out.append(self.decide(aid, actor=actor, decision="approve", note="批量批准（低风险）"))
        return out

    def _execute(self, approval_id: str, args: dict, rec: dict) -> dict:
        if not self.executor:
            raise ApprovalError("未配置执行器")
        try:
            result = self.executor({**rec, "payload": {"args": args, "scope": rec["payload"]["scope"]}})
            state = "executed"
        except Exception as e:  # 对外发送类动作不自动重试
            result, state = {"error": str(e)}, "failed"
            self.audit.append("alert", bot_id=rec["bot_id"], trace_id=rec["trace_id"], level="P2",
                              kind="approval.execute_failed", approval_id=approval_id, error=str(e))
        self.db.execute("UPDATE approvals SET state=?, result=? WHERE approval_id=?", [state, dumps(result), approval_id])
        self.audit.append("approval.executed" if state == "executed" else "approval.failed", bot_id=rec["bot_id"],
                          trace_id=rec["trace_id"], approval_id=approval_id)
        return self._finish(approval_id)

    def _finish(self, approval_id: str) -> dict:
        rec = self.get(approval_id)
        self._card(rec, recipient=f"role:{rec['approver_role']}")
        self._notify(rec)
        return rec

    def _notify(self, rec: dict) -> None:
        for fn in self.listeners:
            fn(rec)

    def _set(self, approval_id: str, state: str) -> None:
        self.db.execute("UPDATE approvals SET state=? WHERE approval_id=?", [state, approval_id])

    # ---------- 定时：升级与过期 ----------
    def tick(self) -> dict:
        now = self.clock.now()
        escalated = expired = 0
        for rec in self.list(state="pending,escalated", limit=10000):
            if parse_iso(rec["expires_at"]) <= now:
                self._set(rec["approval_id"], "expired")
                self.audit.append("approval.expire", bot_id=rec["bot_id"], trace_id=rec["trace_id"],
                                  approval_id=rec["approval_id"])
                self._finish(rec["approval_id"])
                expired += 1
            elif rec["state"] == "pending" and parse_iso(rec["escalate_at"]) <= now:
                self._set(rec["approval_id"], "escalated")
                self.audit.append("approval.escalate", bot_id=rec["bot_id"], trace_id=rec["trace_id"],
                                  approval_id=rec["approval_id"], to_role=rec["backup_role"])
                self._card(self.get(rec["approval_id"]), recipient=f"role:{rec['backup_role']}")
                escalated += 1
        return {"escalated": escalated, "expired": expired}

    def _card(self, rec: dict, recipient: str) -> None:
        review = rec.get("review") or {}
        self.outbox.post(
            recipient=recipient, kind="approval", card_id=f"approval:{rec['approval_id']}",
            card={
                "title": f"待审批：{rec['action_class']} · {rec['target']}",
                "state": rec["state"],
                "approval_id": rec["approval_id"],
                "review_level": review.get("level"),
                "issues": review.get("issues", []),
                "preview": rec["payload"]["args"],
                "evidence": rec.get("evidence") or [],
                "expires_at": rec["expires_at"],
                "buttons": ["approve", "edit", "reject"] if rec["state"] in OPEN_STATES else [],
            },
        )
