"""knowledge 连接器：产品、价格政策、话术知识库（只读）。文档带可见范围（internal / external）与审定标记。"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from ..registry import Tool, ToolContext, ToolError, ToolRegistry


def _tokens(text: str) -> set[str]:
    words = set(re.findall(r"[A-Za-z0-9]{2,}", text.lower()))
    cjk = re.findall(r"[一-鿿]", text)
    return words | {a + b for a, b in zip(cjk, cjk[1:])}


class KnowledgeBase:
    def __init__(self, directory: Path):
        self.docs: list[dict] = []
        for p in sorted(directory.glob("*.md")):
            raw = p.read_text(encoding="utf-8")
            meta, body = {}, raw
            if raw.startswith("---"):
                _, fm, body = raw.split("---", 2)
                meta = yaml.safe_load(fm) or {}
            self.docs.append({**meta, "text": body.strip()})

    def search(self, args: dict, ctx: ToolContext) -> dict:
        q = _tokens(args["query"])
        types = set(args.get("doc_type") or [])
        out = []
        for d in self.docs:
            if d.get("visibility") not in ctx.kb_visibility:
                continue
            if types and d.get("doc_type") not in types:
                continue
            score = len(q & _tokens(d["title"] + " " + d["text"])) + (3 if types else 0)
            if score <= 0:
                continue
            out.append({
                "doc_id": d["doc_id"], "title": d["title"], "version": d.get("version"),
                "approved": bool(d.get("approved")), "visibility": d.get("visibility"),
                "doc_type": d.get("doc_type"), "text": d["text"][:800], "score": score,
            })
        out.sort(key=lambda r: r["score"], reverse=True)
        out = out[: args.get("limit", 5)]
        top = f"{out[0]['doc_id']}#v{out[0]['version']}" if out else None
        return {"results": out, "count": len(out), "top_ref": top, "source": "knowledge"}

    def get_approved_text(self, args: dict, ctx: ToolContext) -> dict:
        for d in self.docs:
            if d["doc_id"] == args["doc_id"] and d.get("visibility") in ctx.kb_visibility:
                if not d.get("approved"):
                    raise ToolError("NOT_FOUND", "该文档未审定，不能用于引用")
                if args.get("lang") and d.get("lang") != args["lang"]:
                    raise ToolError("NOT_FOUND", f"没有 {args['lang']} 语言的审定版本")
                return {"doc_id": d["doc_id"], "version": d.get("version"), "lang": d.get("lang"), "text": d["text"],
                        "source": "knowledge"}
        raise ToolError("NOT_FOUND", f"文档不存在：{args['doc_id']}")

    def text_for_ref(self, ref: str) -> str | None:
        doc_id = ref.split("#")[0]
        for d in self.docs:
            if d["doc_id"] == doc_id:
                return d["text"]
        return None


def register(reg: ToolRegistry, kb: KnowledgeBase) -> None:
    reg.register(Tool(
        name="knowledge.search", action_class="read:kb", handler=kb.search,
        description="检索产品、价格政策、话术知识库",
        schema={"type": "object", "required": ["query"], "additionalProperties": False, "properties": {
            "query": {"type": "string", "maxLength": 500},
            "doc_type": {"type": "array"}, "lang": {"type": "string"}, "limit": {"type": "integer", "minimum": 1},
        }},
    ))
    reg.register(Tool(
        name="knowledge.get_approved_text", action_class="read:kb", handler=kb.get_approved_text,
        description="读取人工审定的某语言版本原文（政策类只能引用，不能自行翻译）",
        schema={"type": "object", "required": ["doc_id"], "additionalProperties": False,
                "properties": {"doc_id": {"type": "string"}, "lang": {"type": "string"}}},
    ))
