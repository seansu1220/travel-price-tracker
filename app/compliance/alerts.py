"""告警介面。P0 先寫入日誌；P1 接上 Telegram 管理者告警。"""

import logging
from typing import Protocol

logger = logging.getLogger("compliance.alerts")


class Alerter(Protocol):
    def send(self, level: str, title: str, detail: str) -> None: ...


class LoggingAlerter:
    def send(self, level: str, title: str, detail: str) -> None:
        log = logger.error if level == "critical" else logger.warning
        log("[%s] %s：%s", level, title, detail)
