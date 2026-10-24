"""例程调度：幂等键防重、补偿补跑、超时不补（技术方案 4.4 / 16.4）。"""
from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta

from ..audit import AuditLog
from ..bots.registry import BotRegistry
from ..config import LAGOS, Clock, iso
from ..db import DB

LATE_AFTER = timedelta(minutes=15)  # 计划时间后 15 分钟内开始算“按时”


def parse_cron(expr: str) -> tuple[int, int, set[int] | None]:
    """支持 'M H * * DOW'（DOW 为 * 或逗号分隔的 0–6，0=周日）。"""
    parts = expr.split()
    if len(parts) != 5 or parts[2] != "*" or parts[3] != "*":
        raise ValueError(f"不支持的 cron 表达式：{expr}")
    dow = None if parts[4] == "*" else {int(x) for x in parts[4].split(",")}
    return int(parts[0]), int(parts[1]), dow


def occurrences(expr: str, start: datetime, end: datetime, tz=LAGOS) -> list[datetime]:
    minute, hour, dow = parse_cron(expr)
    out = []
    day = start.astimezone(tz).date() - timedelta(days=1)
    last = end.astimezone(tz).date()
    while day <= last:
        cron_dow = (day.weekday() + 1) % 7  # Python 周一=0 → cron 周日=0
        if dow is None or cron_dow in dow:
            t = datetime.combine(day, time(hour, minute), tz)
            if start < t <= end:
                out.append(t)
        day += timedelta(days=1)
    return out


class Scheduler:
    def __init__(self, db: DB, audit: AuditLog, clock: Clock, bots: BotRegistry, tasks, runner,
                 lookback: timedelta = timedelta(hours=24)):
        self.db = db
        self.audit = audit
        self.clock = clock
        self.bots = bots
        self.tasks = tasks
        self.runner = runner
        self.lookback = lookback
        self.maintenance: list = []  # 每次 tick 顺带执行：审批升级 / 过期、卡住检测、记忆过期等

    def tick(self) -> dict:
        now = self.clock.now()
        started, skipped = [], []
        for bot in self.bots.list():
            if bot.status not in ("production", "staging"):
                continue
            for trig in bot.triggers:
                if trig.get("type") != "cron":
                    continue
                for t in occurrences(trig["expr"], now - self.lookback, now):
                    key = f"{bot.id}:{trig['routine']}:{iso(t)}"
                    if self.db.one("SELECT 1 FROM routine_runs WHERE idempotency_key=?", [key]):
                        continue
                    late = now - t > LATE_AFTER
                    cutoff = time.fromisoformat(trig.get("compensate_before", "23:59"))
                    run = {"run_id": "run_" + uuid.uuid4().hex[:10], "bot_id": bot.id, "routine": trig["routine"],
                           "scheduled_at": iso(t), "idempotency_key": key, "compensated": int(late)}
                    if late and (now.astimezone(LAGOS).time() > cutoff or now.astimezone(LAGOS).date() != t.date()):
                        run.update(state="skipped", note=f"错过补跑时限 {cutoff}")
                        self._insert(run)
                        self.audit.append("alert", bot_id=bot.id, level="P3", kind="routine.skipped",
                                          routine=trig["routine"], scheduled_at=iso(t))
                        skipped.append(key)
                        continue
                    task = self.tasks.create(bot_id=bot.id, goal=f"例程：{trig['routine']}", source="routine",
                                             skill=trig["routine"])
                    run.update(state="started", task_id=task["task_id"], started_at=iso(now), attempt=1)
                    self._insert(run)
                    self.audit.append("routine.start", bot_id=bot.id, trace_id=task["trace_id"],
                                      routine=trig["routine"], scheduled_at=iso(t), compensated=late)
                    self.runner.start(task["task_id"])
                    started.append(key)
        for fn in self.maintenance:
            fn()
        return {"started": started, "skipped": skipped}

    def _insert(self, run: dict) -> None:
        self.db.execute(f"INSERT INTO routine_runs({','.join(run)}) VALUES ({','.join('?' * len(run))})",
                        list(run.values()))

    def trigger(self, bot_id: str, routine: str) -> dict:
        """控制台手动触发（测试与补跑），同样留台账与审计。"""
        bot = self.bots.get(bot_id)
        if not any(t.get("routine") == routine for t in bot.triggers):
            raise KeyError(f"{bot_id} 没有例程 {routine}")
        now = self.clock.now()
        task = self.tasks.create(bot_id=bot_id, goal=f"例程：{routine}（手动触发）", source="routine", skill=routine)
        self._insert({"run_id": "run_" + uuid.uuid4().hex[:10], "bot_id": bot_id, "routine": routine,
                      "scheduled_at": iso(now), "idempotency_key": f"{bot_id}:{routine}:manual:{uuid.uuid4().hex[:6]}",
                      "state": "started", "task_id": task["task_id"], "started_at": iso(now), "attempt": 1,
                      "note": "手动触发"})
        self.runner.start(task["task_id"])
        return task

    def runs(self, limit: int = 100) -> list[dict]:
        rows = self.db.query("SELECT * FROM routine_runs ORDER BY scheduled_at DESC LIMIT ?", [limit])
        for r in rows:
            if r.get("task_id"):
                t = self.db.one("SELECT status FROM tasks WHERE task_id=?", [r["task_id"]])
                r["task_status"] = t["status"] if t else None
        return rows
