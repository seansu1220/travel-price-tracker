# 開發進度

## 目前階段

**P0 合規基礎：已完成並結案（2026-10-23），驗收關卡達成，待確認事項全部定案。** 下一步：P1 自用 MVP（尚未開始）。

分支：`claude/p0-compliance-foundation`

## 已完成項目

### P0 合規基礎（2026-10-23）

| 項目 | 檔案 | 說明 |
| --- | --- | --- |
| 來源登錄表 | `app/models/source.py`、`app/compliance/registry.py` | 必填：等級、條款網址、審閱日期、審閱人、允許主機與路徑、限速；欄位不齊或格式錯誤無法登錄；C 級不可設定主機 |
| 合規閘門 | `app/compliance/gate.py` | 系統唯一能對外發請求的地方；檢查順序：登錄 → 等級 → 審閱 180 天 → 啟用／暫停 → 網址範圍 → robots（B 級）→ 限速 → 預算 |
| robots.txt 檢查 | `app/compliance/robots.py` | 每次請求前檢查，Redis 快取 24 小時；轉址最多跟 5 次且只限同網域或其子網域；抓不到時保守拒絕 |
| 限速器 | `app/compliance/limiter.py` | 每網域最少間隔（B 級預設 10 秒），以網域全域計算，多個來源共用網域時取較嚴格的間隔；Redis 原子操作，多工作者共用 |
| 預算熔斷 | `app/compliance/budget.py` | 每來源每日次數上限（預設 500）、每月金額上限；80% 告警、100% 拒絕並告警 |
| 封鎖處理 | `app/compliance/detection.py` | 403、429、驗證碼頁、封鎖頁 → 立即暫停來源、告警、不重試；需人工 `resume_source()` 恢復 |
| 稽核日誌 | `app/models/fetch_log.py`、`app/compliance/audit.py` | 每筆請求（含被拒、robots 抓取、網路錯誤）都記錄；`purge_expired_logs()` 清除 1 年前紀錄 |
| 誠實 User-Agent | `gate.py` | `TravelPriceTrackerBot/0.1.0 (+mailto:聯絡信箱)`；呼叫端無法覆蓋；未設定聯絡信箱時閘門拒絕啟動 |
| 配接器介面 | `app/sources/base.py` | A/B 級 `SearchableAdapter`（必須注入閘門）；C 級 `DeeplinkOnlyAdapter`（定義 `search()` 會直接報錯） |
| 資料庫遷移 | `alembic/versions/0001_p0_compliance.py` | 建立 `sources`、`fetch_logs` |
| 依通用規則調整 | 見 `docs/CHANGELOG.md` | 業務參數移入設定、Redis／資料庫故障時拒絕放行並標明步驟、連線失敗與稽核寫入失敗的錯誤說明 |
| 靜態規則測試 | `tests/test_static_rules.py` | `app/compliance/` 以外不得引入 httpx、requests、playwright 等連網模組；不得引入 stealth、驗證碼破解、代理池類套件 |

## 驗收關卡狀態

| 階段 | 驗收關卡 | 狀態 | 證據 |
| --- | --- | --- | --- |
| P0 | 閘門測試全部通過：未登錄或禁止的來源一律被拒 | **達成** | `tests/test_gate.py`（57 項）全部通過，含：未登錄 `test_unregistered_source_is_denied_without_any_request`、C 級 `test_tier_c_source_is_always_denied`、審閱過期 `test_review_older_than_180_days_is_denied`、停用／暫停、網址越界 7 種、robots 禁止、封鎖暫停不重試、robots 轉址、限速（含網域共用）、預算。全套 98 項測試在 SQLite+fakeredis 與 PostgreSQL+Redis 兩種環境都通過；另做過突變檢查：拿掉 C 級拒絕後測試確實會失敗 |
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

### 已定案（擁有者 2026-10-23 決定）

| 項目 | 決定 | 落實位置 |
| --- | --- | --- |
| A 級來源不檢查 robots.txt | 同意 | `gate.py`，測試 `test_tier_a_does_not_fetch_robots` |
| robots.txt 請求不計入限速與預算 | 同意，但必須寫入稽核日誌 | 已寫入（`decision = robots_fetch`），測試 `test_robots_fetch_is_audited_but_not_counted`、`test_robots_fetch_is_audited_for_every_status` |
| 預算達 100% 閘門直接拒絕 | 同意；另外 80% 起排程器把頻率減半 | 100% 拒絕已實作（`budget.py`）；80% 降頻已寫入 `SPEC.md` 第六章，P1 實作 |

| robots.txt 取得失敗 | 404 等 4xx 無限制、401／403 全禁、5xx 或連不上不放行並 1 小時後重試：同意。**轉址改為**依 RFC 9309 跟隨，最多 5 次，只限同網域或其子網域 | `robots.py` 的 `robots_redirect_allowed()`、`gate.py` 的 `_fetch_robots()`；測試 `test_robots_redirect_*`、`test_robots_follows_up_to_five_redirects`、`test_robots_more_than_five_redirects_is_denied` |
| 每日次數以來源計、最小間隔以網域計 | 同意；間隔必須以網域全域計算 | `limiter.py` 取相鄰兩次請求中較嚴格的間隔；測試 `test_sources_sharing_a_domain_obey_the_stricter_interval`、`test_interval_is_the_stricter_of_consecutive_callers` |
| 金額以最小貨幣單位存整數 | 同意 | 維持現狀 |
| 封鎖頁關鍵字寧可誤判 | 同意；**來源被暫停時一定要告警，P1 接上 Telegram 後要收得到** | 暫停時一律發 critical 告警（含暫停寫入失敗的情況）；P1 待辦已列入 |
| 閘門不自動跟隨一般請求的轉址 | 同意 | 維持現狀 |
| 告警目前只寫入日誌 | 同意，P1 接 Telegram | P1 待辦 |
| 稽核日誌暫不做月分區 | 同意 | P3 再評估 |

### 新的待確認

1. **robots.txt 轉址到「上層網域」不跟隨**：依您「同一個網域或其子網域」的說法，`www.example.com` → `example.com` 屬於上層網域，目前**不跟隨、不放行**。若要允許 www 與主網域互轉，請告訴我。
2. **網域以完整主機名稱計算限速**：`example.com` 與 `www.example.com` 目前視為兩個網域、各自計算間隔。若要合併計算（以主網域為單位），請告訴我。

### CLAUDE.md 通用規則與專案規則的對照

合併 main 後逐條比對，**沒有直接衝突**；以下是需要解讀的地方，皆以本專案規則為準：

- 「修改程式後要 commit 並推上 GitHub」與「工作方式」的小步提交、推送分支一致。
- 「模型分工」：本次修改集中在合規閘門核心 `app/compliance/`，屬規則中的例外，由主模型直接修改。
- 「配置驅動」與 SPEC 第六章「預設參數（可在後台調整）」：P0 先用環境變數，管理後台（P3）再改成可存在資料表調整。
- 「異常處理：捕捉錯誤不代表可以重試」與合規規則一致：閘門任何錯誤都只拒絕、不重試。
- 「依賴管理」提到的 Docker 映像與 `scripts/deploy.sh` 屬 P1，目前尚未建立。

合規規則違反請求紀錄：本階段無。

### 合併規則（擁有者 2026-10-23 決定）

- P0 最後一次合併：本機全部測試通過即合併（尚無 CI）。
- **從 P1 起，CI 通過才合併**；已寫入 `CLAUDE.md`「工作方式」。

## 下一步建議

1. 擁有者回覆上方「新的待確認」2 點。
2. 開始 **P1 自用 MVP**，順序：
   1. **第一件事：GitHub Actions 自動測試**（之後 CI 通過才合併）
   2. Duffel 機票查價配接器（經閘門）、監控任務、查詢合併排程（含預算 80% 頻率減半）
   3. Telegram 通知與管理者告警：**來源被暫停、預算告警、robots 不允許都必須送到 Telegram**，並加測試
   4. docker-compose 與部署腳本
3. P1 開始前擁有者需準備：Duffel 帳號與測試金鑰（並確認第十二章的合約問題）、Telegram Bot（BotFather）、管理者 chat_id、要寫進 User-Agent 的聯絡信箱。
