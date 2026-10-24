"""模型设置：OpenRouter Key 与三档模型，在控制台配置，存数据库（只写不读）。

- Key 保存后不再完整返回给前端，只给出前缀和后 4 位用于辨认；
- 每次修改写审计（不含 Key 本身）；
- 环境变量 OPENROUTER_API_KEY 可作为兜底（数据库未配置时使用）。

注意：POC 中 Key 以明文存放在数据库 kv 表，能访问数据库的人就能看到 Key；生产环境改为凭证库。
"""
from __future__ import annotations

import os

from ..audit import AuditLog
from ..db import DB, dumps, loads

KV_KEY = "model.openrouter"
SLOTS = ("standard", "economy", "premium")
DEFAULT_MODELS = {"standard": "qwen/qwen3.8-flash", "economy": "qwen/qwen3.7-flash", "premium": "qwen/qwen3.8-max-0902"}


def hint(key: str | None) -> str | None:
    if not key:
        return None
    return f"{key[:6]}…{key[-4:]}" if len(key) > 12 else "已设置"


class ModelSettings:
    def __init__(self, db: DB, audit: AuditLog):
        self.db = db
        self.audit = audit

    def _raw(self) -> dict:
        row = self.db.one("SELECT v FROM kv WHERE k=?", [KV_KEY])
        return loads(row["v"], {}) if row else {}

    def get(self) -> dict:
        """供模型网关使用（含完整 Key）。"""
        raw = self._raw()
        key = raw.get("api_key") or os.environ.get("OPENROUTER_API_KEY") or ""
        models = {**DEFAULT_MODELS, **(raw.get("models") or {})}
        return {"api_key": key, "models": models}

    def public(self) -> dict:
        """给控制台看的版本（Key 打码）。"""
        raw = self._raw()
        db_key = raw.get("api_key")
        env_key = os.environ.get("OPENROUTER_API_KEY")
        return {
            "configured": bool(db_key or env_key),
            "source": "console" if db_key else ("env" if env_key else None),
            "key_hint": hint(db_key or env_key),
            "models": {**DEFAULT_MODELS, **(raw.get("models") or {})},
            "updated_by": raw.get("updated_by"),
            "updated_at": raw.get("updated_at"),
        }

    def update(self, *, actor: str, at: str, api_key: str | None = None, clear_key: bool = False,
               models: dict | None = None) -> dict:
        raw = self._raw()
        changed = []
        if clear_key:
            raw.pop("api_key", None)
            changed.append("api_key(cleared)")
        elif api_key:
            api_key = api_key.strip()
            if not api_key.startswith("sk-") or len(api_key) < 20:
                raise ValueError("看起来不是有效的 OpenRouter Key（应以 sk- 开头）")
            raw["api_key"] = api_key
            changed.append("api_key")
        if models:
            cur = {**DEFAULT_MODELS, **(raw.get("models") or {})}
            for slot, mid in models.items():
                if slot not in SLOTS:
                    raise ValueError(f"未知档位 {slot}")
                mid = (mid or "").strip()
                if not mid or "/" not in mid:
                    raise ValueError(f"{slot} 档的模型 ID 应为 厂商/模型 格式，如 qwen/qwen-plus")
                if cur.get(slot) != mid:
                    changed.append(f"{slot}={mid}")
                cur[slot] = mid
            raw["models"] = cur
        raw["updated_by"], raw["updated_at"] = actor, at
        self.db.execute("INSERT INTO kv(k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                        [KV_KEY, dumps(raw)])
        self.audit.append("settings.change", actor=actor, area="model.openrouter", changed=changed,
                          key_hint=hint(raw.get("api_key")))
        return self.public()
