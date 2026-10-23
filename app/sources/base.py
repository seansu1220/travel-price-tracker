"""來源配接器介面（SPEC 第三章）。

- A、B 級：繼承 SearchableAdapter，實作 search() 與 deeplink()
- C 級：繼承 DeeplinkOnlyAdapter，只實作 deeplink()，不得有 search()
"""

from abc import ABC, abstractmethod
from typing import ClassVar, Literal

from app.compliance.gate import ComplianceGate


class SourceAdapter(ABC):
    source_id: ClassVar[str]
    tier: ClassVar[Literal["A", "B", "C"]]
    kind: ClassVar[Literal["flight", "hotel"]]
    ttl_seconds: ClassVar[int]

    @abstractmethod
    def deeplink(self, query: dict) -> str:
        """產生帶日期、人數、聯盟追蹤碼的跳轉網址。所有等級都要實作。"""


class SearchableAdapter(SourceAdapter):
    """A、B 級：只能透過注入的 gate 發請求。"""

    def __init__(self, gate: ComplianceGate) -> None:
        if self.tier not in ("A", "B"):
            raise TypeError(f"{type(self).__name__} 為 {self.tier} 級，不可實作 search()")
        self.gate = gate

    @abstractmethod
    def search(self, query: dict) -> list:
        """查價，回傳 Offer 列表（Offer 型別於 P1 定義）。"""

    @abstractmethod
    def healthcheck(self) -> bool: ...


class DeeplinkOnlyAdapter(SourceAdapter):
    """C 級：不發任何請求，因此不接受 gate。"""

    tier: ClassVar[Literal["C"]] = "C"

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "search") or getattr(cls, "tier", "C") != "C":
            raise TypeError(f"C 級配接器 {cls.__name__} 不得實作 search()，等級必須為 C")
