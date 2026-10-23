"""封鎖偵測：403、429、驗證碼頁或封鎖頁都視為「對方不歡迎」，閘門會暫停該來源。"""

_BLOCK_STATUS = {403: "http_403", 429: "http_429"}

# 只檢查 HTML 回應；寧可誤判暫停，也不要在對方封鎖時繼續請求
_BLOCK_MARKERS = (
    "captcha",
    "cf-chl",
    "challenge-platform",
    "verify you are human",
    "are you a robot",
    "unusual traffic",
    "access denied",
    "request blocked",
    "請完成驗證",
    "您的請求已被封鎖",
)

MAX_SCAN_CHARS = 200_000


def detect_block(status_code: int, content_type: str, body_text: str) -> str | None:
    """回傳封鎖原因代碼；不是封鎖則回傳 None。"""
    if status_code in _BLOCK_STATUS:
        return _BLOCK_STATUS[status_code]
    if "html" not in content_type.lower():
        return None
    lowered = body_text[:MAX_SCAN_CHARS].lower()
    for marker in _BLOCK_MARKERS:
        if marker in lowered:
            return f"block_page:{marker}"
    return None
