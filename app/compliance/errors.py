"""合規閘門的拒絕原因。每個例外都有固定的 code，會寫進稽核日誌。"""


class ComplianceError(Exception):
    code = "compliance_error"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code)


class GateMisconfigured(ComplianceError):
    code = "gate_misconfigured"


class SourceNotRegistered(ComplianceError):
    code = "source_not_registered"


class SourceTierC(ComplianceError):
    code = "source_tier_c"


class SourceReviewExpired(ComplianceError):
    code = "source_review_expired"


class SourceDisabled(ComplianceError):
    code = "source_disabled"


class SourcePaused(ComplianceError):
    code = "source_paused"


class UrlNotAllowed(ComplianceError):
    code = "url_not_allowed"


class RobotsDisallowed(ComplianceError):
    code = "robots_disallowed"

    def __init__(self, message: str = "", verdict: str = "disallowed") -> None:
        super().__init__(message)
        self.verdict = verdict  # disallowed 或 unavailable


class RateLimited(ComplianceError):
    code = "rate_limited"

    def __init__(self, message: str = "", retry_after_seconds: float = 0.0) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class BudgetExceeded(ComplianceError):
    code = "budget_exceeded"


class SourceBlocked(ComplianceError):
    """對方回 403/429/驗證碼頁：來源已被暫停，不可重試。"""

    code = "source_blocked"
