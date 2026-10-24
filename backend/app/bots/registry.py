"""智能体库、团队登记表与技能库（技术方案 4.3 / 10 / 23.2）。

- 智能体库（模板）：config/profiles/*.yaml 是岗位模板，config/bot-templates.yaml 补充展示信息；
- 团队（实例）：数据库 bots 表。从模板“招募”出实例，同一岗位可按区域招多个（如 market-intel--north）；
- 实例继承模板的工具、技能、例程；可覆盖显示名、区域作用域、状态（active / paused / retired）。
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import iso, load_yaml
from ..db import DB, dumps, loads

STATES = ("active", "paused", "retired")


@dataclass
class Bot:
    id: str
    display_name: str
    tools: list[str]
    scope: dict
    template: str = ""
    state: str = "active"
    triggers: list[dict] = field(default_factory=list)
    outputs: list[dict] = field(default_factory=list)
    default_skill: str | None = None
    limits: dict = field(default_factory=dict)
    memory: dict = field(default_factory=dict)
    kb_visibility: list[str] = field(default_factory=lambda: ["internal", "external"])
    status: str = "production"          # 岗位模板的上线阶段：production / staging
    owner: str | None = None
    business_owner: str | None = None
    service_account: str | None = None
    browser: bool = False
    created_by: str | None = None
    created_at: str | None = None

    @property
    def namespace(self) -> str:
        return f"bot/{self.id}"

    @property
    def active(self) -> bool:
        return self.state == "active"


class BotRegistry:
    def __init__(self, profiles_dir: Path, db: DB | None = None, meta_path: Path | None = None,
                 seed: list[str] | None = None, clock=None):
        self.templates: dict[str, dict] = {}
        for p in sorted(profiles_dir.glob("*.yaml")):
            d = load_yaml(p)
            self.templates[d["id"]] = d
        self.meta: dict[str, dict] = {}
        if meta_path and meta_path.exists():
            self.meta = {m["id"]: m for m in load_yaml(meta_path).get("templates", [])}
        self.db = db
        self.clock = clock
        self._mem: dict[str, dict] = {}  # 无数据库时（单元测试）的内存登记表
        for tid in (seed if seed is not None else list(self.templates)):
            if tid in self.templates and not self._row(tid):
                self._insert({"bot_id": tid, "template": tid, "display_name": None, "scope": None,
                              "state": "active", "created_by": "system"})

    # ---------- 存储 ----------
    def _now(self) -> str:
        return iso(self.clock.now()) if self.clock else ""

    def _row(self, bot_id: str) -> dict | None:
        if self.db is None:
            return self._mem.get(bot_id)
        return self.db.one("SELECT * FROM bots WHERE bot_id=?", [bot_id])

    def _rows(self) -> list[dict]:
        if self.db is None:
            return list(self._mem.values())
        return self.db.query("SELECT * FROM bots ORDER BY created_at, bot_id")

    def _insert(self, row: dict) -> None:
        row = {**row, "created_at": self._now()}
        if self.db is None:
            self._mem[row["bot_id"]] = row
            return
        self.db.execute("INSERT INTO bots(bot_id, template, display_name, scope, state, created_by, created_at)"
                        " VALUES (?,?,?,?,?,?,?)",
                        [row["bot_id"], row["template"], row["display_name"], row["scope"], row["state"],
                         row["created_by"], row["created_at"]])

    def _update(self, bot_id: str, **fields) -> None:
        if self.db is None:
            self._mem[bot_id].update(fields)
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE bots SET {sets} WHERE bot_id=?", [*fields.values(), bot_id])

    # ---------- 读取 ----------
    def _build(self, row: dict) -> Bot:
        t = copy.deepcopy(self.templates[row["template"]])
        known = Bot.__dataclass_fields__
        bot = Bot(**{k: v for k, v in t.items() if k in known and k != "id"}, id=row["bot_id"])
        bot.template = row["template"]
        bot.state = row["state"]
        bot.created_by, bot.created_at = row.get("created_by"), row.get("created_at")
        if row.get("display_name"):
            bot.display_name = row["display_name"]
        extra = loads(row.get("scope"), None)
        if extra:
            bot.scope = {**bot.scope, **extra}
        return bot

    def get(self, bot_id: str) -> Bot:
        row = self._row(bot_id)
        if not row or row["template"] not in self.templates:
            raise KeyError(f"未登记的 Bot：{bot_id}")
        return self._build(row)

    def list(self, include_retired: bool = False) -> list[Bot]:
        return [self._build(r) for r in self._rows()
                if r["template"] in self.templates and (include_retired or r["state"] != "retired")]

    @property
    def bots(self) -> dict[str, Bot]:
        return {b.id: b for b in self.list()}

    def template_of(self, bot_id: str) -> str:
        row = self._row(bot_id)
        return row["template"] if row else bot_id.split("--")[0]

    def resolve(self, target: str) -> str | None:
        """Bot 间消息的目标可以是实例 ID，也可以是岗位模板 ID（取第一个在岗实例）。"""
        row = self._row(target)
        if row and row["state"] == "active":
            return target
        for b in self.list():
            if b.template == target and b.active:
                return b.id
        return None

    def library(self) -> list[dict]:
        hired: dict[str, int] = {}
        for b in self.list():
            hired[b.template] = hired.get(b.template, 0) + 1
        out = []
        for tid, t in self.templates.items():
            m = self.meta.get(tid, {})
            out.append({
                "id": tid, "name": m.get("name") or t.get("display_name"), "title": m.get("title"),
                "summary": m.get("summary"), "capabilities": m.get("capabilities", []), "color": m.get("color"),
                "autonomy": m.get("autonomy"), "phase": m.get("phase"), "channel": m.get("channel", "内部"),
                "schedule": m.get("schedule"), "tools": t.get("tools", []), "hired": hired.get(tid, 0),
                "multi_instance": m.get("multi_instance", True),
            })
        return out

    # ---------- 招募与管理 ----------
    def hire(self, template: str, *, actor: str, display_name: str | None = None, region: str | None = None,
             country: str | None = None) -> Bot:
        from ..config import DEFAULT_MARKET, MARKETS
        if template not in self.templates:
            raise KeyError(f"智能体库中没有岗位 {template}")
        if country and country not in MARKETS:
            raise ValueError(f"未知市场 {country}")
        if country and region and region not in MARKETS[country]["regions"]:
            raise ValueError(f"{MARKETS[country]['name']}没有区域 {region}")
        m = self.meta.get(template, {})
        existing = [b for b in self.list(include_retired=True) if b.template == template]
        if existing and not m.get("multi_instance", True):
            raise ValueError(f"{m.get('name', template)} 只能有一个在岗实例")
        foreign = country and country != DEFAULT_MARKET
        bot_id = template
        if self._row(bot_id) or foreign:  # 其他市场的实例一律带后缀，ID 里看得出国家
            base = "-".join(x for x in [country.lower() if foreign else "", region or ""] if x)
            suffix = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-") or str(len(existing) + 1)
            bot_id = f"{template}--{suffix}"
            n = 2
            while self._row(bot_id):
                bot_id = f"{template}--{suffix}-{n}"
                n += 1
        place = region or (MARKETS[country]["name"] if foreign else None)
        name = display_name or (f"{m.get('name', template)} · {place}" if place else m.get("name"))
        scope = {**({"country": [country]} if country else {}), **({"region": [region]} if region else {})}
        self._insert({"bot_id": bot_id, "template": template, "display_name": name,
                      "scope": dumps(scope) if scope else None, "state": "active", "created_by": actor})
        return self.get(bot_id)

    def set_state(self, bot_id: str, state: str) -> Bot:
        if state not in STATES:
            raise ValueError(f"未知状态 {state}")
        self.get(bot_id)
        self._update(bot_id, state=state)
        return self.get(bot_id)

    def rename(self, bot_id: str, display_name: str) -> Bot:
        self.get(bot_id)
        self._update(bot_id, display_name=display_name.strip()[:60])
        return self.get(bot_id)


class SkillLibrary:
    def __init__(self, skills_dir: Path):
        self.dir = skills_dir

    def get(self, template: str, name: str) -> dict:
        name = name.split("/")[-1]
        for p in (self.dir / template / f"{name}.yaml", self.dir / "shared" / f"{name}.yaml"):
            if p.exists():
                skill = load_yaml(p)
                if template not in skill.get("bots", [template]):
                    raise KeyError(f"技能 {skill['name']} 未授权给 {template}")
                return skill
        raise KeyError(f"找不到技能 {template}/{name}")

    def for_template(self, template: str) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("*/*.yaml")):
            s = load_yaml(p)
            if template in s.get("bots", []):
                out.append({"name": s["name"], "version": s.get("version"), "description": s.get("description"),
                            "steps": [x.get("title", x["id"]) for x in s.get("steps", [])]})
        return out

    def list(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("*/*.yaml")):
            s = load_yaml(p)
            out.append({"name": s["name"], "version": s.get("version"), "bots": s.get("bots", []),
                        "description": s.get("description"), "steps": len(s.get("steps", [])),
                        "step_titles": [x.get("title", x.get("id")) for x in s.get("steps", [])]})
        return out
