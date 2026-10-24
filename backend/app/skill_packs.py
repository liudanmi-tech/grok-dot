"""技能广场：开源「方法技能」（Agent Skills 规范的 SKILL.md）+ 平台内置「流程技能」（YAML 步骤）。

方法技能不执行代码，只作为方法论：安装到岗位后，成员执行 LLM 步骤时按任务目标挑选最相关的 1–2 个，
把要点注入系统提示（与给定数据、政策冲突时以数据和政策为准）。来源与许可证随目录保留。
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from .config import iso

MAX_INJECT_CHARS = 3000


def _front(text: str) -> tuple[dict, str]:
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)
        try:
            return yaml.safe_load(fm) or {}, body.strip()
        except yaml.YAMLError:
            return {}, body.strip()
    return {}, text


def _tokens(text: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9]{3,}", text.lower()))
    cjk = re.findall(r"[一-鿿]", text)
    return words | {a + b for a, b in zip(cjk, cjk[1:])}


class SkillPacks:
    def __init__(self, directory: Path, db, clock, audit):
        self.db, self.clock, self.audit = db, clock, audit
        cat = yaml.safe_load((directory / "catalog.yaml").read_text(encoding="utf-8")) if (directory / "catalog.yaml").exists() else {}
        self.sources = cat.get("sources", {})
        meta = cat.get("skills", {})
        self.packs: dict[str, dict] = {}
        for src in sorted(p for p in directory.iterdir() if p.is_dir()):
            for d in sorted(p for p in src.iterdir() if p.is_dir() and (p / "SKILL.md").exists()):
                fm, body = _front((d / "SKILL.md").read_text(encoding="utf-8"))
                pid = f"{src.name}/{d.name}"
                m = meta.get(pid, {})
                desc = str(fm.get("description", "")).strip()
                self.packs[pid] = {
                    "id": pid, "name": fm.get("name", d.name), "zh": m.get("zh"), "zh_desc": m.get("desc"),
                    "category": m.get("cat", "其他（偏线上 / SaaS）"),
                    "description": desc, "version": (fm.get("metadata") or {}).get("version") or fm.get("version"),
                    "source": self.sources.get(src.name, {"name": src.name}), "recommended": m.get("roles", []),
                    "references": sorted(r.name for r in (d / "references").glob("*.md")) if (d / "references").exists() else [],
                    "body": body, "dir": d,
                }
                self.packs[pid]["_tokens"] = _tokens(f"{pid} {m.get('zh', '')} {m.get('cat', '')} {desc[:600]}")
        self._seed_defaults()

    # ---------- 安装 ----------
    def _seed_defaults(self) -> None:
        """首次启动：按目录里的推荐，把方法技能装到对应岗位。之后以人工调整为准。"""
        if self.db.one("SELECT 1 FROM kv WHERE k='skill_packs.seeded'"):
            return
        now = iso(self.clock.now())
        for pid, p in self.packs.items():
            for tpl in p["recommended"]:
                self.db.execute("INSERT INTO skill_installs(template, pack_id, installed_by, installed_at) VALUES (?,?,?,?)",
                                [tpl, pid, "system", now])
        self.db.execute("INSERT INTO kv(k, v) VALUES ('skill_packs.seeded', '1')")

    def installs(self, template: str | None = None) -> list[dict]:
        if template:
            return self.db.query("SELECT * FROM skill_installs WHERE template=? ORDER BY pack_id", [template])
        return self.db.query("SELECT * FROM skill_installs ORDER BY template, pack_id")

    def set_templates(self, pack_id: str, templates: list[str], *, actor: str) -> list[str]:
        if pack_id not in self.packs:
            raise KeyError("没有这个技能")
        with self.db.lock:
            self.db.execute("DELETE FROM skill_installs WHERE pack_id=?", [pack_id])
            now = iso(self.clock.now())
            for t in dict.fromkeys(templates):
                self.db.execute("INSERT INTO skill_installs(template, pack_id, installed_by, installed_at) VALUES (?,?,?,?)",
                                [t, pack_id, actor, now])
        self.audit.append("skill_pack.install", actor=actor, pack_id=pack_id, templates=templates)
        return templates

    # ---------- 浏览 ----------
    def public(self, p: dict, *, full: bool = False) -> dict:
        out = {k: v for k, v in p.items() if not k.startswith("_") and k not in ("dir", "body")}
        out["installed"] = [r["template"] for r in self.db.query("SELECT template FROM skill_installs WHERE pack_id=?", [p["id"]])]
        if full:
            out["body"] = p["body"]
        return out

    def listing(self) -> list[dict]:
        return [self.public(p) for p in self.packs.values()]

    def detail(self, pack_id: str) -> dict:
        if pack_id not in self.packs:
            raise KeyError("没有这个技能")
        return self.public(self.packs[pack_id], full=True)

    def reference(self, pack_id: str, name: str) -> str:
        p = self.packs.get(pack_id)
        if not p or name not in p["references"]:
            raise KeyError("没有这份参考资料")
        return (p["dir"] / "references" / name).read_text(encoding="utf-8")

    # ---------- 执行时选用 ----------
    def pick(self, template: str, goal: str, k: int = 2) -> list[dict]:
        """为本次任务挑选已安装、且与目标最相关的方法技能。"""
        ids = [r["pack_id"] for r in self.installs(template) if r["pack_id"] in self.packs]
        if not ids:
            return []
        q = _tokens(goal)
        scored = sorted(((len(q & self.packs[i]["_tokens"]), i) for i in ids), reverse=True)
        chosen = [i for s, i in scored if s > 0][:k] or [scored[0][1]]
        return [self.packs[i] for i in chosen]

    def inject(self, packs: list[dict]) -> str:
        if not packs:
            return ""
        parts = [f"### {p['zh'] or p['name']}（{p['id']}）\n{p['body'][:MAX_INJECT_CHARS]}" for p in packs]
        return ("\n\n【方法技能 · 来自技能广场】以下是可借鉴的方法论（原文多为英文、面向线上 SaaS 场景）。只取适用于本任务的部分；"
                "与给定数据、政策和交付格式冲突时，以数据、政策和交付格式为准：\n" + "\n\n".join(parts))
