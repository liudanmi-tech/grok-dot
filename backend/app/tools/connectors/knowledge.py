"""knowledge 连接器：产品、价格政策、话术知识库（只读）。文档带可见范围（internal / external）与审定标记。"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

import yaml

from ...policy import allows
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
            self.docs.append({**meta, "text": body.strip(), "source": "builtin"})
        self.file_docs = list(self.docs)
        self.db = None

    # ---------- 上传文档（知识库页面）：存数据库，审定后才对数字员工可见 ----------
    DOC_TYPES = {"product", "price_band", "price_policy", "policy", "channel", "aftersales", "faq", "other"}

    def attach(self, db, clock, audit) -> None:
        self.db, self.clock, self.audit = db, clock, audit
        self._reload()

    def _reload(self) -> None:
        rows = self.db.query("SELECT * FROM kb_docs WHERE deleted=0 ORDER BY updated_at DESC") if self.db else []
        self.docs = self.file_docs + [{**r, "approved": bool(r["approved"]), "source": "upload"} for r in rows]

    def _check(self, f: dict) -> None:
        if not (f.get("title") or "").strip() or not (f.get("text") or "").strip():
            raise ToolError("INVALID_ARGS", "标题和正文都不能为空")
        if f.get("doc_type") and f["doc_type"] not in self.DOC_TYPES:
            raise ToolError("INVALID_ARGS", f"未知文档类型 {f['doc_type']}")
        if f.get("visibility") and f["visibility"] not in ("internal", "external"):
            raise ToolError("INVALID_ARGS", "可见范围只能是 internal / external")
        if len(f.get("text") or "") > 60000:
            raise ToolError("INVALID_ARGS", "正文过长（上限 6 万字）")

    def create(self, *, actor: str, title: str, text: str, doc_type: str = "other", visibility: str = "internal",
               country: str = "NG", lang: str = "zh") -> dict:
        from ...config import iso
        self._check({"title": title, "text": text, "doc_type": doc_type, "visibility": visibility})
        doc_id = "kb:up-" + uuid.uuid4().hex[:8]
        now = iso(self.clock.now())
        self.db.execute("INSERT INTO kb_docs(doc_id, title, doc_type, visibility, country, lang, version, approved, text,"
                        " created_by, created_at, updated_by, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [doc_id, title.strip()[:120], doc_type, visibility, country, lang, 1, 0, text, actor, now, actor, now])
        self.audit.append("kb.create", actor=actor, doc_id=doc_id, title=title[:80], country=country)
        self._reload()
        return self.doc(doc_id)

    def update(self, doc_id: str, *, actor: str, **fields) -> dict:
        from ...config import iso
        d = self._uploaded(doc_id)
        merged = {**d, **{k: v for k, v in fields.items() if v is not None}}
        self._check(merged)
        self.db.execute("UPDATE kb_docs SET title=?, doc_type=?, visibility=?, country=?, lang=?, text=?, version=?, approved=0,"
                        " approved_by=NULL, updated_by=?, updated_at=? WHERE doc_id=?",
                        [merged["title"].strip()[:120], merged["doc_type"], merged["visibility"], merged["country"], merged["lang"],
                         merged["text"], d["version"] + 1, actor, iso(self.clock.now()), doc_id])
        self.audit.append("kb.update", actor=actor, doc_id=doc_id, version=d["version"] + 1)
        self._reload()
        return self.doc(doc_id)

    def approve(self, doc_id: str, *, actor: str) -> dict:
        d = self._uploaded(doc_id)
        self.db.execute("UPDATE kb_docs SET approved=1, approved_by=? WHERE doc_id=?", [actor, doc_id])
        self.audit.append("kb.approve", actor=actor, doc_id=doc_id, version=d["version"])
        self._reload()
        return self.doc(doc_id)

    def delete(self, doc_id: str, *, actor: str) -> None:
        self._uploaded(doc_id)
        self.db.execute("UPDATE kb_docs SET deleted=1 WHERE doc_id=?", [doc_id])
        self.audit.append("kb.delete", actor=actor, doc_id=doc_id)
        self._reload()

    def _uploaded(self, doc_id: str) -> dict:
        r = self.db.one("SELECT * FROM kb_docs WHERE doc_id=? AND deleted=0", [doc_id]) if self.db else None
        if not r:
            raise ToolError("NOT_FOUND", "只有上传的文档可以修改（内置文档随版本发布）")
        return r

    def doc(self, doc_id: str) -> dict:
        for d in self.docs:
            if d["doc_id"] == doc_id:
                return {**d, "country": d.get("country", "NG")}
        raise ToolError("NOT_FOUND", f"文档不存在：{doc_id}")

    def catalog(self) -> list[dict]:
        return [{k: v for k, v in {**d, "country": d.get("country", "NG"), "size": len(d["text"]),
                                    "snippet": d["text"][:160]}.items() if k != "text"} for d in self.docs]

    def search(self, args: dict, ctx: ToolContext) -> dict:
        q = _tokens(args["query"])
        types = set(args.get("doc_type") or [])
        out = []
        for d in self.docs:
            if d.get("visibility") not in ctx.kb_visibility:
                continue
            if d.get("source") == "upload" and not d.get("approved"):  # 上传文档未审定：数字员工看不到
                continue
            if not allows(ctx.scope, "country", d.get("country", "NG")):  # 知识按国家隔离（未标注的属尼日利亚）
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
        if not out and not types:
            # 本地语言提问时关键词可能对不上：退回到可见范围内全部已审定文档，由模型判断相关性
            out = [{"doc_id": d["doc_id"], "title": d["title"], "version": d.get("version"), "approved": True,
                    "visibility": d.get("visibility"), "doc_type": d.get("doc_type"), "text": d["text"][:800],
                    "score": 0} for d in self.docs
                   if d.get("approved") and d.get("visibility") in ctx.kb_visibility
                   and allows(ctx.scope, "country", d.get("country", "NG"))]
        out.sort(key=lambda r: r["score"], reverse=True)
        out = out[: args.get("limit", 5)]
        refs = [f"{r['doc_id']}#v{r['version']}" for r in out]
        return {"results": out, "count": len(out), "top_ref": refs[0] if refs else None, "refs": refs,
                "source": "knowledge"}

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
