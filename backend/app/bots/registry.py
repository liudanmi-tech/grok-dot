"""岗位配置档与技能库（技术方案 4.3 / 10 / 23.2）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import load_yaml


@dataclass
class Bot:
    id: str
    display_name: str
    tools: list[str]
    scope: dict
    triggers: list[dict] = field(default_factory=list)
    outputs: list[dict] = field(default_factory=list)
    default_skill: str | None = None
    limits: dict = field(default_factory=dict)
    memory: dict = field(default_factory=dict)
    kb_visibility: list[str] = field(default_factory=lambda: ["internal", "external"])
    status: str = "production"
    owner: str | None = None
    business_owner: str | None = None
    service_account: str | None = None
    browser: bool = False

    @property
    def namespace(self) -> str:
        return self.memory.get("namespace", f"bot/{self.id}")


class BotRegistry:
    def __init__(self, profiles_dir: Path):
        self.bots: dict[str, Bot] = {}
        for p in sorted(profiles_dir.glob("*.yaml")):
            d = load_yaml(p)
            known = Bot.__dataclass_fields__
            self.bots[d["id"]] = Bot(**{k: v for k, v in d.items() if k in known})

    def get(self, bot_id: str) -> Bot:
        if bot_id not in self.bots:
            raise KeyError(f"未登记的 Bot：{bot_id}")
        return self.bots[bot_id]

    def list(self) -> list[Bot]:
        return list(self.bots.values())


class SkillLibrary:
    def __init__(self, skills_dir: Path):
        self.dir = skills_dir

    def get(self, bot_id: str, name: str) -> dict:
        name = name.split("/")[-1]
        for p in (self.dir / bot_id / f"{name}.yaml", self.dir / "shared" / f"{name}.yaml"):
            if p.exists():
                skill = load_yaml(p)
                if bot_id not in skill.get("bots", [bot_id]):
                    raise KeyError(f"技能 {skill['name']} 未授权给 {bot_id}")
                return skill
        raise KeyError(f"找不到技能 {bot_id}/{name}")

    def list(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("*/*.yaml")):
            s = load_yaml(p)
            out.append({"name": s["name"], "version": s.get("version"), "bots": s.get("bots", []),
                        "description": s.get("description"), "steps": len(s.get("steps", []))})
        return out
