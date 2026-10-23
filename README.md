# travel-price-tracker

機票飯店比價監控系統：免費比價搜尋，付費 24 小時價格監控與降價通知。

- 產品規格：[`SPEC.md`](SPEC.md)
- 開發規則（給 Claude Code）：[`CLAUDE.md`](CLAUDE.md)
- 開發進度：[`PROGRESS.md`](PROGRESS.md)

## 開發方式

本專案由 Claude Code 依 `SPEC.md` 第十一章分階段實作，一次一個階段：

| 階段 | 內容 |
| --- | --- |
| P0 | 合規基礎：來源登錄表、合規閘門、robots 檢查、限速器、稽核日誌 |
| P1 | 自用 MVP：Duffel 查價、監控任務、Telegram 通知 |
| P2 | 免費比價網頁 |
| P3 | 來源擴充與管理後台 |
| P4 | 會員與付費 |

部署方式完成 P1 後見 `docs/DEPLOY.md`。
