"""pricing-draft 连接器：调价 / 促销建议写入草稿区；提交审批后由公司现有价格流程拉取（技术方案 7.4）。"""
from __future__ import annotations

import uuid

from ...artifacts import ArtifactStore
from ...config import Clock, iso
from ...db import DB, dumps, loads
from ..registry import Tool, ToolContext, ToolError, ToolRegistry
from .knowledge import KnowledgeBase


class PricingDrafts:
    def __init__(self, db: DB, clock: Clock, artifacts: ArtifactStore, kb: KnowledgeBase):
        self.db = db
        self.clock = clock
        self.artifacts = artifacts
        self.kb = kb

    def create_suggestion(self, args: dict, ctx: ToolContext) -> dict:
        draft_id = "drf_" + uuid.uuid4().hex[:10]
        now = iso(self.clock.now())
        self.db.execute("INSERT INTO drafts(draft_id, kind, bot_id, payload, state, created_at, updated_at)"
                        " VALUES (?,?,?,?,?,?,?)", [draft_id, "pricing", ctx.bot_id, dumps(args), "draft", now, now])
        return {"draft_id": draft_id, "state": "draft"}

    def _draft(self, draft_id: str) -> dict:
        d = self.db.one("SELECT * FROM drafts WHERE draft_id=?", [draft_id])
        if not d:
            raise ToolError("NOT_FOUND", f"草稿不存在：{draft_id}")
        d["payload"] = loads(d["payload"])
        return d

    def describe_submit(self, args: dict, ctx: ToolContext) -> dict:
        d = self._draft(args["draft_id"])
        p = d["payload"]
        evidence = []
        for ref in p.get("evidence_refs", []):
            if ref.startswith("art:"):
                art = self.artifacts.get(ref)
                if art:
                    evidence.append({"ref": ref, "text": dumps(art["content"])[:2000]})
            elif ref.startswith("kb:"):
                evidence.append({"ref": ref, "text": self.kb.text_for_ref(ref) or ""})
        preview = p.get("rationale", "")
        return {"target": f"{p.get('sku')}@{p.get('region')}", "preview": preview, "evidence": evidence}

    def submit_suggestion(self, args: dict, ctx: ToolContext) -> dict:
        """仅在审批通过后执行：草稿转为 approved，等待公司现有价格审批流程拉取。"""
        d = self._draft(args["draft_id"])
        self.db.execute("UPDATE drafts SET state='approved', updated_at=? WHERE draft_id=?",
                        [iso(self.clock.now()), d["draft_id"]])
        return {"draft_id": d["draft_id"], "state": "approved", "next": "已转入现有价格审批流程"}

    def mark(self, draft_id: str, state: str, approval_id: str | None = None) -> None:
        self.db.execute("UPDATE drafts SET state=?, approval_id=COALESCE(?, approval_id), updated_at=? WHERE draft_id=?",
                        [state, approval_id, iso(self.clock.now()), draft_id])

    def list(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM drafts ORDER BY created_at DESC LIMIT 200")
        for r in rows:
            r["payload"] = loads(r["payload"])
        return rows


def register(reg: ToolRegistry, pd: PricingDrafts) -> None:
    reg.register(Tool(
        name="pricing-draft.create_suggestion", action_class="draft:pricing", handler=pd.create_suggestion,
        description="写入调价 / 促销建议草稿", scope_keys=("region",),
        schema={"type": "object", "required": ["sku", "region", "proposal_type", "rationale", "evidence_refs"],
                "additionalProperties": False, "properties": {
                    "sku": {"type": "string"}, "region": {"type": "string"},
                    "proposal_type": {"type": "string", "enum": ["price_change", "promotion"]},
                    "proposed_price_ngn": {"type": "number", "minimum": 0},
                    "change_pct": {"type": "number"},
                    "promo": {"type": "object"},
                    "rationale": {"type": "string", "maxLength": 2000},
                    "evidence_refs": {"type": "array", "minItems": 1},
                }},
    ))
    reg.register(Tool(
        name="pricing-draft.submit_suggestion", action_class="submit:pricing_change", handler=pd.submit_suggestion,
        describe=pd.describe_submit, description="提交调价建议（需国家经理审批）",
        schema={"type": "object", "required": ["draft_id"], "additionalProperties": False,
                "properties": {"draft_id": {"type": "string"}}},
    ))
