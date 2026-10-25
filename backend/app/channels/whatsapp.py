"""WhatsApp 网关（技术方案 13）：独占 WhatsApp 凭证与公网出口，Bot 只能经两个内部动作收发。

入站：签名校验 → 去重 → 身份绑定 → 同意 / 关键词（退订、转人工、切换语言）→ 清洗 → 语言识别 → 建任务交给经销商助理
出站：仅由已批准的审批单触发 → 窗口校验 → 退订校验 → 熔断 → 限流 → 发送 → 留痕

未配置 WA_ACCESS_TOKEN / WA_PHONE_NUMBER_ID 时为“模拟模式”：出站消息只写入记录，在控制台查看。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import uuid
from datetime import timedelta
from typing import Any, Callable

import httpx

from ..audit import AuditLog
from ..config import Clock, iso, parse_iso
from ..db import DB, dumps, loads

WINDOW = timedelta(hours=24)
LANG_NAMES = {"en": "English", "pcm": "Nigerian Pidgin", "ha": "Hausa", "yo": "Yoruba", "ig": "Igbo"}

# 网关直接发出的固定话术（已审定文本，相当于长期规则；不含任何业务内容）
FIXED = {
    "unknown": {
        "en": "Hello! This is the TECNO / Infinix / itel dealer service line. Your number is not registered as a dealer yet. "
              "Please contact your channel manager to get registered.",
    },
    "handoff": {
        "en": "Thank you. Your channel manager will reply to you shortly.",
        "pcm": "Thank you. Your channel manager go reply you soon.",
        "ha": "Na gode. Manajan tashar ku zai amsa muku nan ba da jimawa ba.",
        "yo": "E se. Oluṣakoso ikanni yin yoo dahun laipẹ.",
        "ig": "Daalụ. Onye njikwa ọwa gị ga-aza gị n'oge na-adịghị anya.",
    },
    "stop": {"en": "You have been unsubscribed. Reply START to subscribe again."},
    "start": {"en": "You are subscribed again. How can we help?"},
    "lang": {
        "en": "Language set to English.", "pcm": "Language don change to Pidgin.", "ha": "An canza harshe zuwa Hausa.",
        "yo": "A ti yi ede pada si Yoruba.", "ig": "Agbanweela asụsụ gaa Igbo.",
    },
    "text_only": {"en": "Sorry, I can only read text messages for now. Please type your question."},
}

PIDGIN = re.compile(r"\b(abeg|wetin|dey|una|wahala|sabi|oga|comot|abi|oya|howfar|how far|wey|na im|e don|make i)\b", re.I)
HAUSA = re.compile(r"\b(sannu|nawa|yaya|kudi|wayar|waya|don allah|nagode|na gode|ina son|akwai|zan|kuna|yanzu)\b", re.I)
YORUBA = re.compile(r"(ẹ|ọ|ṣ)|\b(bawo|elo ni|owo|e kaaro|ese|mo fe|se|ni bayi|e jowo)\b", re.I)
IGBO = re.compile(r"(ị|ụ|ṅ)|\b(kedu|ego ole|biko|daalu|achọrọ|ugbu a|nnoo)\b", re.I)
INJECTION = re.compile(r"(ignore (all|previous|the above)|system prompt|you are now|disregard|忽略(之前|以上))", re.I)


def detect_language(text: str) -> tuple[str, float]:
    scores = {"pcm": len(PIDGIN.findall(text)), "ha": len(HAUSA.findall(text)),
              "yo": len(YORUBA.findall(text)), "ig": len(IGBO.findall(text))}
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "en", 0.6
    return best, min(0.95, 0.6 + 0.15 * scores[best])


def normalize_phone(p: str) -> str:
    return re.sub(r"\D", "", p or "")


class WhatsAppGateway:
    def __init__(self, db: DB, audit: AuditLog, clock: Clock, dealers: list[dict], promoters: list[dict] | None = None):
        self.db = db
        self.audit = audit
        self.clock = clock
        self.dealers = dealers
        self.phone_to_dealer = {normalize_phone(ph): d for d in dealers for ph in d.get("phones", [])}
        self.promoters = promoters or []
        self.phone_to_promoter = {normalize_phone(ph): pr for pr in self.promoters for ph in pr.get("phones", [])}
        self.on_field_report: Callable[[dict, dict, str], dict] | None = None  # 促销员报数 → 一线日报员
        self.on_dealer_message: Callable[[dict, dict, str], None] | None = None  # 建任务交给经销商助理
        self.on_handoff: Callable[[dict, str], None] | None = None

    # ---------- 配置 ----------
    @property
    def live(self) -> bool:
        return bool(os.environ.get("WA_ACCESS_TOKEN") and os.environ.get("WA_PHONE_NUMBER_ID"))

    def verify_signature(self, body: bytes, header: str | None) -> bool:
        secret = os.environ.get("WA_APP_SECRET")
        if not secret:
            return not self.live  # 真实模式必须配置 App Secret
        if not header or not header.startswith("sha256="):
            return False
        expect = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expect, header[7:])

    def killswitch(self) -> bool:
        r = self.db.one("SELECT v FROM kv WHERE k='wa.killswitch'")
        return bool(r and r["v"] == "1")

    def set_killswitch(self, on: bool, actor: str) -> None:
        self.db.execute("INSERT INTO kv(k, v) VALUES ('wa.killswitch', ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                        ["1" if on else "0"])
        self.audit.append("killswitch.toggle", actor=actor, on=on, channel="whatsapp")

    # ---------- 会话 ----------
    def conversation(self, phone: str) -> dict:
        phone = normalize_phone(phone)
        row = self.db.one("SELECT * FROM wa_conversations WHERE phone=?", [phone])
        if row:
            return row
        dealer = self.phone_to_dealer.get(phone)
        now = iso(self.clock.now())
        cid = "cv_" + uuid.uuid4().hex[:10]
        self.db.execute(
            "INSERT INTO wa_conversations(conversation_id, phone, dealer_id, lang, lang_source, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            [cid, phone, dealer["dealer_id"] if dealer else None, dealer.get("lang") if dealer else None,
             "profile" if dealer else None, now, now])
        return self.db.one("SELECT * FROM wa_conversations WHERE conversation_id=?", [cid])

    def get_conversation(self, conversation_id: str) -> dict:
        c = self.db.one("SELECT * FROM wa_conversations WHERE conversation_id=?", [conversation_id])
        if not c:
            raise KeyError(conversation_id)
        return c

    def dealer_of(self, conv: dict) -> dict | None:
        return next((d for d in self.dealers if d["dealer_id"] == conv.get("dealer_id")), None)

    def messages(self, conversation_id: str, limit: int = 50) -> list[dict]:
        rows = self.db.query("SELECT * FROM wa_messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
                             [conversation_id, limit])
        for r in rows:
            r["flags"] = loads(r["flags"], {})
        return list(reversed(rows))

    def list_conversations(self) -> list[dict]:
        out = []
        for c in self.db.query("SELECT * FROM wa_conversations ORDER BY updated_at DESC"):
            d = self.dealer_of(c)
            last = self.db.one("SELECT text, direction, created_at, status FROM wa_messages WHERE conversation_id=?"
                               " ORDER BY id DESC LIMIT 1", [c["conversation_id"]])
            out.append({**c, "dealer": d, "last": last, "window_open": self.window_open(c)})
        return out

    def window_open(self, conv: dict) -> bool:
        return bool(conv.get("last_inbound_at")) and self.clock.now() - parse_iso(conv["last_inbound_at"]) < WINDOW

    def _log(self, conv: dict, direction: str, text: str, status: str, **extra: Any) -> int:
        new_id = self.db.insert(
            "INSERT INTO wa_messages(conversation_id, direction, wamid, text, status, kind, approval_id, task_id, flags,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [conv["conversation_id"], direction, extra.get("wamid"), text, status, extra.get("kind", "text"),
             extra.get("approval_id"), extra.get("task_id"), dumps(extra.get("flags") or {}), iso(self.clock.now())])
        self.db.execute("UPDATE wa_conversations SET updated_at=? WHERE conversation_id=?",
                        [iso(self.clock.now()), conv["conversation_id"]])
        return new_id

    # ---------- 入站 ----------
    def handle_webhook(self, payload: dict) -> int:
        n = 0
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for st in value.get("statuses", []):  # 送达 / 已读回执
                    self.db.execute("UPDATE wa_messages SET status=? WHERE wamid=?", [st.get("status"), st.get("id")])
                for m in value.get("messages", []):
                    text = (m.get("text") or {}).get("body") if m.get("type") == "text" else None
                    self.receive(m.get("from", ""), text, wamid=m.get("id"), msg_type=m.get("type", "text"))
                    n += 1
        return n

    def receive(self, phone: str, text: str | None, *, wamid: str | None = None, msg_type: str = "text",
                simulated: bool = False) -> dict:
        if wamid and self.db.one("SELECT 1 FROM wa_messages WHERE wamid=?", [wamid]):
            return {"status": "duplicate"}
        conv = self.conversation(phone)
        now = iso(self.clock.now())
        self.db.execute("UPDATE wa_conversations SET last_inbound_at=? WHERE conversation_id=?",
                        [now, conv["conversation_id"]])
        conv = self.get_conversation(conv["conversation_id"])
        flags = {"simulated": simulated}
        body = (text or "").strip()[:2000]
        if body and INJECTION.search(body):
            flags["possible_injection"] = True
        self._log(conv, "in", body or f"[{msg_type}]", "received", wamid=wamid, flags=flags)
        self.audit.append("message.inbound", channel="whatsapp", conversation_id=conv["conversation_id"],
                          dealer_id=conv["dealer_id"], simulated=simulated, flags=flags)

        promoter = self.phone_to_promoter.get(conv["phone"])
        if promoter and self.on_field_report:  # 驻店促销员：报数走一线日报员，不进经销商助理
            if not body:
                self.send_fixed(conv, "text_only")
                return {"status": "non_text"}
            return {"status": "field_report", "conversation_id": conv["conversation_id"],
                    **self.on_field_report(conv, promoter, body)}

        dealer = self.dealer_of(conv)
        if not dealer:
            self.send_fixed(conv, "unknown")
            return {"status": "unknown_number", "conversation_id": conv["conversation_id"]}
        if not body:
            self.send_fixed(conv, "text_only")
            return {"status": "non_text"}

        kw = body.lower().strip()
        if kw in ("stop", "unsubscribe", "退订"):
            self.db.execute("UPDATE wa_conversations SET opted_out=1 WHERE conversation_id=?", [conv["conversation_id"]])
            self.send_fixed(conv, "stop", force=True)
            return {"status": "opted_out"}
        if kw in ("start", "subscribe"):
            self.db.execute("UPDATE wa_conversations SET opted_out=0 WHERE conversation_id=?", [conv["conversation_id"]])
            self.send_fixed(conv, "start")
            return {"status": "opted_in"}
        m = re.fullmatch(r"lang\s+(en|pcm|ha|yo|ig)", kw)
        if m:
            self.db.execute("UPDATE wa_conversations SET lang=?, lang_source='user' WHERE conversation_id=?",
                            [m.group(1), conv["conversation_id"]])
            conv = self.get_conversation(conv["conversation_id"])
            self.send_fixed(conv, "lang")
            return {"status": "lang_set", "lang": m.group(1)}
        if kw in ("agent", "human", "转人工", "manager") or re.search(r"\b(talk to|speak to) (a )?(human|person)\b", kw):
            self.handoff(conv, "经销商要求转人工")
            return {"status": "handoff"}
        if conv["human_mode"]:
            return {"status": "human_mode"}  # 人工处理期间 Bot 不生成回复

        # 语言：用户选择 > 本条识别 > 档案默认 > 英语（14.2）
        lang = conv["lang"] if conv["lang_source"] == "user" else None
        if not lang:
            detected, conf = detect_language(body)
            lang = detected if conf >= 0.75 else (dealer.get("lang") or "en")
        if self.on_dealer_message:
            self.on_dealer_message(conv, dealer, body if not flags.get("possible_injection") else body, lang)
        return {"status": "queued", "conversation_id": conv["conversation_id"], "lang": lang}

    def handoff(self, conv: dict, reason: str) -> None:
        self.db.execute("UPDATE wa_conversations SET human_mode=1 WHERE conversation_id=?", [conv["conversation_id"]])
        self.send_fixed(conv, "handoff")
        self.audit.append("tool.handoff", channel="whatsapp", conversation_id=conv["conversation_id"], reason=reason)
        if self.on_handoff:
            self.on_handoff(conv, reason)

    def release(self, conversation_id: str, actor: str) -> None:
        self.db.execute("UPDATE wa_conversations SET human_mode=0 WHERE conversation_id=?", [conversation_id])
        self.audit.append("whatsapp.release", conversation_id=conversation_id, actor=actor)

    # ---------- 出站 ----------
    def send_fixed(self, conv: dict, key: str, force: bool = False) -> dict:
        texts = FIXED[key]
        text = texts.get(conv.get("lang") or "en") or texts["en"]
        return self.send(conv["conversation_id"], text, kind="fixed", force=force)

    def _rate_ok(self, conversation_id: str) -> bool:
        now = self.clock.now()
        per_min = self.db.one("SELECT COUNT(*) n FROM wa_messages WHERE conversation_id=? AND direction='out' "
                              "AND created_at >= ?", [conversation_id, iso(now - timedelta(minutes=1))])["n"]
        per_hour = self.db.one("SELECT COUNT(*) n FROM wa_messages WHERE conversation_id=? AND direction='out' "
                               "AND created_at >= ?", [conversation_id, iso(now - timedelta(hours=1))])["n"]
        total = self.db.one("SELECT COUNT(*) n FROM wa_messages WHERE direction='out' AND created_at >= ?",
                            [iso(now - timedelta(hours=1))])["n"]
        return per_min < 3 and per_hour < 20 and total < 1000

    def send(self, conversation_id: str, text: str, *, kind: str = "reply", approval_id: str | None = None,
             task_id: str | None = None, force: bool = False) -> dict:
        conv = self.get_conversation(conversation_id)
        if self.killswitch():
            raise RuntimeError("WhatsApp 对外发送已熔断")
        if not self.window_open(conv):
            raise RuntimeError("24 小时客服窗口已关闭，只能发送已审核模板")
        if conv["opted_out"] and not force:
            raise RuntimeError("对方已退订")
        if not self._rate_ok(conversation_id):
            self.audit.append("alert", level="P2", kind="whatsapp.rate_limited", conversation_id=conversation_id)
            raise RuntimeError("触发发送限流")
        wamid, status = None, "simulated"
        if self.live:
            wamid = self._post(conv["phone"], text)
            status = "sent"
        mid = self._log(conv, "out", text, status, wamid=wamid, kind=kind, approval_id=approval_id, task_id=task_id)
        self.audit.append("message.outbound", channel="whatsapp", conversation_id=conversation_id, kind=kind,
                          approval_id=approval_id, live=self.live)
        return {"message_id": mid, "wamid": wamid, "status": status}

    def _post(self, phone: str, text: str) -> str:
        ver = os.environ.get("WA_GRAPH_VERSION", "v21.0")
        url = f"https://graph.facebook.com/{ver}/{os.environ['WA_PHONE_NUMBER_ID']}/messages"
        r = httpx.post(url, headers={"Authorization": f"Bearer {os.environ['WA_ACCESS_TOKEN']}"}, timeout=15,
                       json={"messaging_product": "whatsapp", "to": phone, "type": "text", "text": {"body": text}})
        if r.status_code >= 400:
            err = (r.json().get("error") or {}).get("message", r.text) if r.headers.get("content-type", "").startswith(
                "application/json") else r.text
            raise RuntimeError(f"WhatsApp 接口返回 {r.status_code}：{err[:200]}")
        return r.json()["messages"][0]["id"]
