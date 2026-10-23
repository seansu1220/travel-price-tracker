# 變更紀錄

每次修改程式都記在這裡：問題描述、根本原因、修改的檔案與內容。階段進度見 `PROGRESS.md`。

---

## 2026-10-23　P0 依通用規則調整、待確認事項定案

**問題描述**

1. `CLAUDE.md` 新增「通用規則」，P0 既有程式有幾處不符：業務參數寫死在程式中、少數變數命名不清（`s`、`data`）、Redis 或資料庫故障時錯誤訊息看不出發生在哪一步、`.gitignore` 未排除 `*.pkl`、尚無本變更紀錄檔。
2. 擁有者對待確認前三點做出決定：A 級不檢查 robots.txt（同意）、robots.txt 請求不計入限速與預算但要寫稽核日誌（同意）、預算 100% 拒絕（同意，並要求 80% 起排程器頻率減半）。

**根本原因**

- P0 撰寫時尚未有通用規則；部分參數（robots 失敗重試間隔、告警去重秒數、新來源限速預設值）當成程式常數。
- 閘門把 Redis、資料庫例外直接往外丟，呼叫端只看到底層錯誤。
- 連線失敗時直接丟出 httpx 例外，沒有說明是哪個來源、哪個網址。

**修改的檔案與內容**

| 檔案 | 內容 |
| --- | --- |
| `app/config.py`、`.env.example` | 新增 `SOURCE_DEFAULT_MIN_INTERVAL_SECONDS`、`SOURCE_DEFAULT_DAILY_CALL_LIMIT`、`ROBOTS_UNAVAILABLE_RETRY_SECONDS`、`ALERT_DEDUPE_SECONDS` |
| `app/compliance/registry.py` | 新來源的限速預設值改從設定讀取 |
| `app/compliance/robots.py` | 無法確認時的重試間隔改由設定傳入 |
| `app/compliance/budget.py` | Redis 計數器保存期限改為具名常數；註解更新為「80% 由排程器降頻、100% 拒絕」 |
| `app/compliance/errors.py` | 新增 `GateUnavailable`、`AuditWriteError`、`FetchFailed` |
| `app/compliance/gate.py` | Redis／資料庫故障時一律拒絕放行，錯誤訊息標明步驟（載入來源登錄表、檢查 robots.txt、限速器、預算熔斷）；連線失敗改丟 `FetchFailed`（含來源與網址，仍不重試）；封鎖時改為「先暫停 → 告警 → 寫日誌」，暫停寫入失敗也會發「暫停失敗、請立即人工停用」的告警；變數命名改善 |
| `app/compliance/audit.py` | 寫入失敗時丟出 `AuditWriteError`，說明資料表、來源、判定與網址 |
| `.gitignore` | 新增 `*.pkl` |
| `SPEC.md` 第六章 | 預算熔斷改為：80% 告警並由排程器把頻率減半（P1 實作）；100% 閘門拒絕 |
| `tests/test_gate.py` | 新增 robots.txt 請求的稽核紀錄測試（含 404／403／500 與快取命中不重複記錄），並驗證不計入限速與預算；新增 Redis 斷線、暫停寫入失敗、稽核寫入失敗測試 |
| `tests/test_registry.py` | 新增限速預設值由設定讀取的測試 |
| `tests/test_static_rules.py` | 新增 `.gitignore` 必要項目檢查 |

robots.txt 請求寫入稽核日誌在 P0 原本就已實作（`decision = robots_fetch`），本次補上更完整的測試。

---

## 2026-10-23　P0 合規基礎

**問題描述**：系統需要在任何對外請求之前，強制執行 SPEC 第二章的合規閘門。

**根本原因**：新專案，尚無程式。

**修改的檔案與內容**：新增來源登錄表與稽核日誌資料模型（`app/models/`、`alembic/`）、合規閘門與 robots 檢查、限速器、預算熔斷、封鎖偵測（`app/compliance/`）、配接器介面（`app/sources/base.py`）與全部測試（`tests/`）。細節見 `PROGRESS.md`。
