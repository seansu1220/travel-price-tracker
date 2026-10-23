# 開發進度

## 目前階段

**P0 合規基礎：已完成，驗收關卡達成。** 下一步：P1 自用 MVP（尚未開始，等擁有者確認後再做）。

分支：`claude/p0-compliance-foundation`

## 已完成項目

### P0 合規基礎（2026-10-23）

| 項目 | 檔案 | 說明 |
| --- | --- | --- |
| 來源登錄表 | `app/models/source.py`、`app/compliance/registry.py` | 必填：等級、條款網址、審閱日期、審閱人、允許主機與路徑、限速；欄位不齊或格式錯誤無法登錄；C 級不可設定主機 |
| 合規閘門 | `app/compliance/gate.py` | 系統唯一能對外發請求的地方；檢查順序：登錄 → 等級 → 審閱 180 天 → 啟用／暫停 → 網址範圍 → robots（B 級）→ 限速 → 預算 |
| robots.txt 檢查 | `app/compliance/robots.py` | 每次請求前檢查，Redis 快取 24 小時；抓不到時保守拒絕 |
| 限速器 | `app/compliance/limiter.py` | 每網域最少間隔（B 級預設 10 秒），Redis 原子操作，多工作者共用 |
| 預算熔斷 | `app/compliance/budget.py` | 每來源每日次數上限（預設 500）、每月金額上限；80% 告警、100% 拒絕並告警 |
| 封鎖處理 | `app/compliance/detection.py` | 403、429、驗證碼頁、封鎖頁 → 立即暫停來源、告警、不重試；需人工 `resume_source()` 恢復 |
| 稽核日誌 | `app/models/fetch_log.py`、`app/compliance/audit.py` | 每筆請求（含被拒、robots 抓取、網路錯誤）都記錄；`purge_expired_logs()` 清除 1 年前紀錄 |
| 誠實 User-Agent | `gate.py` | `TravelPriceTrackerBot/0.1.0 (+mailto:聯絡信箱)`；呼叫端無法覆蓋；未設定聯絡信箱時閘門拒絕啟動 |
| 配接器介面 | `app/sources/base.py` | A/B 級 `SearchableAdapter`（必須注入閘門）；C 級 `DeeplinkOnlyAdapter`（定義 `search()` 會直接報錯） |
| 資料庫遷移 | `alembic/versions/0001_p0_compliance.py` | 建立 `sources`、`fetch_logs` |
| 靜態規則測試 | `tests/test_static_rules.py` | `app/compliance/` 以外不得引入 httpx、requests、playwright 等連網模組；不得引入 stealth、驗證碼破解、代理池類套件 |

## 驗收關卡狀態

| 階段 | 驗收關卡 | 狀態 | 證據 |
| --- | --- | --- | --- |
| P0 | 閘門測試全部通過：未登錄或禁止的來源一律被拒 | **達成** | `tests/test_gate.py`（37 項）全部通過，含：未登錄 `test_unregistered_source_is_denied_without_any_request`、C 級 `test_tier_c_source_is_always_denied`、審閱過期 `test_review_older_than_180_days_is_denied`、停用／暫停、網址越界 7 種、robots 禁止、封鎖暫停不重試、限速、預算。全套 74 項測試在 SQLite+fakeredis 與 PostgreSQL+Redis 兩種環境都通過；另做過突變檢查：拿掉 C 級拒絕後測試確實會失敗 |
| P1 | 自用 30 天無漏報；測出單條監控的月成本 | 未開始 | — |
| P2 | 快取結果 3 秒內出現；首屏 2 秒內載入 | 未開始 | — |
| P3 | 各來源成功率 95% 以上；封鎖事件為 0 | 未開始 | — |
| P4 | 營運主體與律師複核完成 | 未開始 | — |

### 如何執行測試

```bash
uv venv -p python3.12 .venv && uv pip install -p .venv -e ".[dev]"
.venv/bin/python -m pytest
# 用真的 PostgreSQL 與 Redis 跑（選用）：
TEST_DATABASE_URL=postgresql+psycopg://... TEST_REDIS_URL=redis://localhost:6379/15 .venv/bin/python -m pytest
```

## 待確認

規格沒寫清楚、P0 先採保守做法的地方，請擁有者確認：

1. **A 級來源不檢查 robots.txt**：A 級是官方 API／合約授權，robots.txt 不適用；B 級每次都檢查。
2. **robots.txt 本身的請求不計入限速與預算**：每網域約每天一次，仍寫入稽核日誌。若要嚴格算進「每 10 秒 1 次」，第一次請求會被延後。
3. **robots.txt 取得失敗的判定**：404 等 4xx → 視為沒有限制（業界慣例）；401／403 → 全部禁止；3xx 轉址、5xx、連線失敗 → 無法確認，一律不放行，1 小時後再試。
4. **預算達 100% 時閘門直接拒絕**；規格寫的「自動降頻」由 P1 排程器負責。
5. **每日次數上限以「來源」計，最小間隔以「網域」計**（規格寫「每網域每日 500 次」；目前一個來源通常對應一個網域，差異不大）。
6. **金額以最小貨幣單位（如美分）存整數**：若某 API 每次呼叫成本不到 1 分，需改用更小單位，屆時再調整。
7. **封鎖頁關鍵字偵測寧可誤判**：HTML 中出現 captcha、access denied 等字樣即暫停來源；JSON 回應只看 403／429。誤停時需人工恢復。
8. **閘門不自動跟隨轉址**：轉址目標要重新經過閘門檢查，由配接器決定是否再請求。
9. **告警目前只寫入日誌**，P1 接上 Telegram 管理者告警。
10. **稽核日誌月分區未做**：目前用索引加每日清理函式；資料量大時（P3）再改分區表。清理排程在 P1 用 Celery Beat 執行。

合規規則違反請求紀錄：本階段無。

## 下一步建議

1. 擁有者審閱本分支並確認上方「待確認」各點（不同意的直接在此註記）。
2. 合併後開始 **P1 自用 MVP**：Duffel 機票查價配接器（經閘門）、監控任務、查詢合併排程、Telegram 通知與管理者告警、docker-compose 與部署腳本。
3. P1 開始前擁有者需準備：Duffel 帳號與測試金鑰（並確認第十二章的合約問題）、Telegram Bot（BotFather）、管理者 chat_id、要寫進 User-Agent 的聯絡信箱。
