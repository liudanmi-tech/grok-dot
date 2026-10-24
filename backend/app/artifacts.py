"""产出存储：Bot 间只传引用（payload_ref），接收方凭引用读取（技术方案 3.4 / 4.5）。"""
from __future__ import annotations

import uuid
from typing import Any

from .config import Clock, iso
from .db import DB, dumps, loads


class ArtifactStore:
    def __init__(self, db: DB, clock: Clock):
        self.db = db
        self.clock = clock

    def put(self, bot_id: str, kind: str, content: Any) -> str:
        ref = f"art:{kind}:{uuid.uuid4().hex[:10]}"
        self.db.execute("INSERT INTO artifacts(ref, bot_id, kind, content, created_at) VALUES (?,?,?,?,?)",
                        [ref, bot_id, kind, dumps(content), iso(self.clock.now())])
        return ref

    def get(self, ref: str) -> dict | None:
        r = self.db.one("SELECT * FROM artifacts WHERE ref=?", [ref])
        if r:
            r["content"] = loads(r["content"])
        return r
