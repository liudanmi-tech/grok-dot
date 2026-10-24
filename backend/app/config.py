"""配置加载与全局设置。

所有业务配置（策略、模型路由、任务参数、岗位配置档、技能）都放在 backend/config 下的 YAML 中，
变更走代码评审（技术方案 4.3 / 18.2）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

BACKEND_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("NGAP_CONFIG_DIR", BACKEND_DIR / "config"))
DATA_DIR = Path(os.environ.get("NGAP_DATA_DIR", BACKEND_DIR / "data"))
VAR_DIR = Path(os.environ.get("NGAP_VAR_DIR", BACKEND_DIR / "var"))

LAGOS = ZoneInfo("Africa/Lagos")

# 市场（国家）维度：数据、竞品、知识库、时区、币种、语言都按国家区分；数据权限按 scope.country 隔离
MARKETS = {
    "NG": {"code": "NG", "name": "尼日利亚", "en": "Nigeria", "city": "拉各斯", "tz": "Africa/Lagos", "currency": "NGN",
           "langs": "英语、皮钦语、豪萨语、约鲁巴语、伊博语", "langs_en": "English, Nigerian Pidgin and Hausa",
           "regions": ["Lagos", "North", "South-West", "South-East", "FCT"]},
    "ID": {"code": "ID", "name": "印尼", "en": "Indonesia", "city": "雅加达", "tz": "Asia/Jakarta", "currency": "IDR",
           "langs": "印尼语（Bahasa Indonesia）、爪哇语、巽他语", "langs_en": "Bahasa Indonesia, Javanese and Sundanese",
           "regions": ["Jakarta", "Jawa Barat", "Jawa Timur", "Sumatera Utara", "Sulawesi Selatan"]},
}
DEFAULT_MARKET = "NG"


def market(code: str | None) -> dict:
    return MARKETS.get(code or DEFAULT_MARKET, MARKETS[DEFAULT_MARKET])


def market_of(*scopes: dict | None) -> dict:
    """按作用域（下达人快照优先，其次 bot）判断市场：country 只有一个时取它，否则默认尼日利亚。"""
    for sc in scopes:
        c = (sc or {}).get("country")
        if c and len(c) == 1 and c[0] in MARKETS:
            return MARKETS[c[0]]
    return MARKETS[DEFAULT_MARKET]


def tz_of(m: dict) -> ZoneInfo:
    return ZoneInfo(m["tz"])


def _load_dotenv(path: Path) -> None:
    """读取 backend/.env（不覆盖已有环境变量）。密钥只放在这个文件里，不进 Git。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


if not os.environ.get("NGAP_SKIP_DOTENV"):
    _load_dotenv(BACKEND_DIR / ".env")


def load_yaml(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass
class Settings:
    db_path: Path = field(default_factory=lambda: Path(os.environ.get("NGAP_DB", VAR_DIR / "ngap.db")))
    # 服务器上用 PostgreSQL：postgresql://user:pass@host:5432/db
    database_url: str | None = field(default_factory=lambda: os.environ.get("NGAP_DATABASE_URL") or None)
    port: int = int(os.environ.get("NGAP_PORT", "8100"))
    scheduler_enabled: bool = os.environ.get("NGAP_SCHEDULER", "1") == "1"
    scheduler_interval_s: int = int(os.environ.get("NGAP_SCHEDULER_INTERVAL", "30"))


class Clock:
    """可替换的时钟，测试中用 FixedClock 控制时间。"""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class DemoClock(Clock):
    """演示时钟：真实时间 + 可调偏移（「快进一天」）。偏移由平台持久化。"""

    def __init__(self, offset_s: float = 0.0):
        self.offset_s = offset_s

    def now(self) -> datetime:
        from datetime import timedelta

        return datetime.now(timezone.utc) + timedelta(seconds=self.offset_s)


class FixedClock(Clock):
    def __init__(self, t: datetime):
        self.t = t

    def now(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        from datetime import timedelta

        self.t = self.t + timedelta(**kw)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)
