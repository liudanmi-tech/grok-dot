"""内部渠道出站（飞书 / 企业微信）。

POC：所有卡片写入 outbox 表，在控制台“消息”页查看；进度卡片按 card_id 原地更新。
如设置 FEISHU_WEBHOOK_URL，则同时以文本形式推送到该飞书群机器人（尽力而为，失败只记日志）。
"""
from __future__ import annotations

import logging
import os

import httpx

from ..config import Clock, iso
from ..db import DB, dumps, loads

log = logging.getLogger(__name__)


class Outbox:
    def __init__(self, db: DB, clock: Clock):
        self.db = db
        self.clock = clock
        self.webhook = os.environ.get("FEISHU_WEBHOOK_URL")

    def post(self, *, recipient: str, kind: str, card: dict, card_id: str | None = None,
             channel: str = "feishu") -> int:
        now = iso(self.clock.now())
        if card_id and self.db.one("SELECT id FROM outbox WHERE card_id=?", [card_id]):
            self.db.execute("UPDATE outbox SET card=?, updated_at=?, recipient=? WHERE card_id=?",
                            [dumps(card), now, recipient, card_id])
            row = self.db.one("SELECT id FROM outbox WHERE card_id=?", [card_id])
            self._push(recipient, card)
            return row["id"]
        new_id = self.db.insert(
            "INSERT INTO outbox(card_id, channel, recipient, kind, card, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            [card_id, channel, recipient, kind, dumps(card), now, now],
        )
        self._push(recipient, card)
        return new_id

    def list(self, recipient: str | None = None, limit: int = 100) -> list[dict]:
        sql, args = "SELECT * FROM outbox", []
        if recipient:
            sql += " WHERE recipient = ?"
            args.append(recipient)
        rows = self.db.query(sql + " ORDER BY updated_at DESC, id DESC LIMIT ?", [*args, limit])
        for r in rows:
            r["card"] = loads(r["card"], {})
        return rows

    def _push(self, recipient: str, card: dict) -> None:
        if not self.webhook:
            return
        text = f"[{recipient}] {card.get('title', '')}\n{card.get('body', '')}"[:3000]
        try:
            httpx.post(self.webhook, json={"msg_type": "text", "content": {"text": text}}, timeout=5)
        except httpx.HTTPError as e:  # 渠道不可用不影响主流程
            log.warning("feishu webhook failed: %s", e)
