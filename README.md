# ETF Portfolio Backtester — 驗證 AI 產出的練習

> 一個用 AI coding agent 開發的台美 ETF 投資組合回測工具。
> 這個專案的重點不在功能，而在練習**如何驗證 AI 寫出來的東西是對的**。

## 為什麼做這個專案

我平常的工作語言不是 Python。這個專案刻意用不熟悉的語言，幾乎全程交給 AI coding agent（Claude Code）實作。

用不熟的語言，就沒辦法靠讀 code 的直覺抓錯，只能回到最根本的問題：**怎麼確認結果是對的？**

金融回測很適合拿來練習，因為它的 bug 通常不會 crash，數字看起來也合理，但結果是錯的。

👉 **[驗證案例紀錄](docs/case-studies.md)**：開發過程中抓到的 5 類 bug、發現方式，以及我整理出的驗證方法。

### 幾個例子

| AI 產出的問題 | 影響 |
|--------------|------|
| ETF 上市前的缺漏資料被當成「報酬 0%」 | 投組最長期間 CAGR 從 14.46% 被算成 9.55% |
| 含息價格又加一次股息再投資 | 股息被重複計算 |
| 用升息期間的配息回推零利率年代 | SHY 2021 年配息被高估 14 倍 |
| 台幣 ETF 直接對美元 benchmark 做回歸 | beta / alpha 被匯率污染 |

## 功能

- **資料抓取**：yfinance（美股 ETF、配息歷史）、TWSE API（台股 ETF）、MoneyDJ（費用率、追蹤差異、VT 國家配置）
- **投資組合回測**：含息總報酬、USD→TWD 匯率換算，計算 CAGR、波動度、Sharpe、最大回撤
- **回歸分析**：相對 VT 的 beta、alpha、R²，以及 60 日滾動 beta
- **定期定額（DCA）模擬**：考慮手續費與最低申購額，XIRR 報酬率，配息再投資（含缺漏年份估算）
- **輸出**：Flask + Plotly 網頁儀表板、Excel 報表

## 技術

Python · pandas · DuckDB · Flask · Plotly · openpyxl

```
yfinance / TWSE / MoneyDJ
   → scripts/（抓取、更新）
   → DuckDB
   → app/services/（回測、回歸）
   → Flask 儀表板 / Excel 報表
```

## 執行方式

```bash
pip install -r requirements.txt
make init        # 第一次執行：抓取完整歷史資料（約 30–60 分鐘）
make run         # 啟動儀表板 http://localhost:5000
make pipeline    # 完整流程：抓資料 → 配息 → 費用率 → Excel 報表
make dca         # 定期定額模擬
```

ETF 清單與投資組合設定在 `config.yaml`。完整流程說明見 [docs/pipeline.md](docs/pipeline.md)。

## 開發方式

- **AI 工具**：Claude Code（commit 中的 `Co-Authored-By` 可以看出各階段使用的模型）
- **約束 AI 的方式**：[`CLAUDE.md`](CLAUDE.md) 記錄架構與**資料欄位語意**（例如 `close` 和 `adj_close` 各自的用途），避免 AI 在後續開發中重蹈覆轍
- **驗證方式**：與外部數據對照、邊界情境檢查、修一個 bug 就找同類 bug，詳見[案例紀錄](docs/case-studies.md)

## 已知限制與下一步

- 目前**沒有自動化測試**，驗證仍以人工對照為主。下一步是用 golden data 替 XIRR、DCA 和配息估算補上測試
- 部分回測數字仍在和外部資料比對中
- 資料來自免費公開來源，可能有缺漏或延遲

## 免責聲明

本專案僅供個人學習與技術練習，**不構成任何投資建議**。回測結果不代表未來績效。
