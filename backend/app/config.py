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
    port: int = int(os.environ.get("NGAP_PORT", "8100"))
    scheduler_enabled: bool = os.environ.get("NGAP_SCHEDULER", "1") == "1"
    scheduler_interval_s: int = int(os.environ.get("NGAP_SCHEDULER_INTERVAL", "30"))


class Clock:
    """可替换的时钟，测试中用 FixedClock 控制时间。"""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


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
