import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("NGAP_SCHEDULER", "0")
os.environ.pop("QWEN_API_KEY", None)
os.environ.pop("DEEPSEEK_API_KEY", None)

from app.config import FixedClock  # noqa: E402
from app.platform import Platform  # noqa: E402

# 2026-11-03（周二）06:00 拉各斯时间
T0 = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)


@pytest.fixture
def clock():
    return FixedClock(T0)


@pytest.fixture
def p(clock):
    plat = Platform(clock=clock, db_path=":memory:")
    plat.runner.background = False
    return plat


@pytest.fixture
def users(p):
    return p.users
