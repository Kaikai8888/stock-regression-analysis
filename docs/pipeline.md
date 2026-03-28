# ETF 投資組合分析 Pipeline 說明

## 快速開始

```bash
# 完整流程（首次執行，含全部歷史資料抓取）
python scripts/pipeline.py

# 之後定期更新（只更新增量資料）
python scripts/pipeline.py --skip-expense   # 跳過費用率更新（非年底不需要）

# 只更新費用率 + 重新計算報告
python scripts/pipeline.py --skip-fetch

# 指定輸出路徑
python scripts/pipeline.py --output reports/Q1_2026.xlsx
```

報表輸出至 `reports/portfolio_analysis_YYYY-MM-DD.xlsx`。

---

## 流程說明

```
pipeline.py
  ├── Step 1  股價資料初始化 / 增量更新
  │           ├── 首次 / --force-initial → fetch_initial.py（全量抓取，耗時 30~60 分鐘）
  │           └── 已有資料 → update_data.py --once（增量更新至最新交易日）
  │
  ├── Step 2  台灣 ETF 費用率更新
  │           └── fetch_expense_ratios.py --update
  │               來源：MoneyDJ「總管理費用(%)」
  │               修改：config.yaml 的 expense_ratio 欄位
  │
  ├── Step 3  費用率同步至 DuckDB
  │           └── config.yaml → etf_info.expense_ratio
  │               確保分析腳本使用最新費用率
  │
  └── Step 4  投資組合報酬率分析
              └── analyze_portfolios.py --output <path>
                  輸出：reports/portfolio_analysis_YYYY-MM-DD.xlsx
```

---

## 各腳本說明

### `scripts/fetch_initial.py`
全量抓取所有 ETF 的歷史股價，寫入 DuckDB。

| 資料來源 | ETF 類型 | 說明 |
|---------|---------|------|
| yfinance | 美國 ETF（VT, IXN, IEF）| 抓取全部歷史（period="max"）|
| yfinance TWD=X | 匯率 USD→TWD | 抓取全部歷史 |
| twstock（主）/ TWSE API（備用） | 台灣 ETF | 從 2010 年起逐月爬取 |

> **注意**：TWSE API 限速 3 req/5sec，台灣 ETF 抓取需要 30~60 分鐘。

---

### `scripts/update_data.py`
增量更新，只抓取上次爬取日期之後的新資料。

```bash
python scripts/update_data.py --once        # 執行一次後結束
python scripts/update_data.py --schedule    # 常駐，每 24 小時執行
python scripts/update_data.py --hours 12    # 常駐，每 12 小時執行
```

---

### `scripts/fetch_expense_ratios.py`
從 [MoneyDJ](https://www.moneydj.com/ETF/X/Basic/Basic0004.xdjhtm) 抓取台灣 ETF 總費用率，更新 `config.yaml`。

- **資料欄位**：「總管理費用(%)」= 含經理費、保管費、手續費、交易稅及其他費用的年化比率
- **US ETF**（VT、IXN、IEF）不在此腳本範圍，費率以 config.yaml 中的人工確認值為準

```bash
python scripts/fetch_expense_ratios.py            # 只查詢，不修改
python scripts/fetch_expense_ratios.py --update   # 自動更新 config.yaml
```

> **建議頻率**：每年初執行一次（費用率通常按年度更新）。

---

### `scripts/analyze_portfolios.py`
計算各投資組合各時間區間的年化報酬率，輸出 xlsx。

```bash
python scripts/analyze_portfolios.py
python scripts/analyze_portfolios.py --output reports/my_report.xlsx
```

#### 計算方法

| 項目 | 方法 |
|------|------|
| 幣別換算 | USD ETF × yfinance TWD=X 歷史匯率 |
| 假日對齊 | 建立聯集日期索引，各 ticker forward-fill |
| 管理費扣除 | 每日扣 `expense_ratio / 252` |
| CAGR | `(end_value / start_value)^(365/days) - 1` |
| 年化波動度 | 日報酬標準差 × √252 |
| Sharpe Ratio | CAGR / 年化波動度（無風險利率=0） |
| 最大回撤 | min((累積值 - 歷史高點) / 歷史高點) |

#### 時間區間定義

| 區間 | 起始日期 |
|------|---------|
| 最長期間 | DB 中最早日期（受限於各 ETF 上市時間） |
| 10年 | 今天 - 10 年 |
| 5年 | 今天 - 5 年 |
| 3年 | 今天 - 3 年 |
| 1年 | 今天 - 1 年 |
| 6個月 | 今天 - 6 個月 |
| 3個月 | 今天 - 3 個月 |
| 1個月 | 今天 - 1 個月 |

> **注意**：若某區間的資料不足（ETF 尚未上市），該格顯示「資料不足」。

---

### `scripts/deprecated/`
已廢棄的舊腳本，保留供參考：

| 檔案 | 廢棄原因 |
|------|---------|
| `fetch_expense_ratios_playwright_screenshot.py` | 截圖解析度不足；官網只顯示費率結構；00735 被 CDN 封鎖 |
| `extract_expense_ratios_dom_text.py` | 官網無「總費用率」直接欄位，regex 無法解析 |

---

## 輸出報表格式（xlsx）

### Sheet 1：CAGR 年化報酬率
各投資組合 × 各時間區間的 CAGR，綠色=正報酬，紅色=負報酬。
最長期間格顯示備注（起始年份與年數）。

### Sheet 2：統計指標（最長期間）
最長可用期間的完整統計：CAGR、總報酬、年化波動度、Sharpe Ratio、最大回撤。

### Sheet 3：投資組合成分
各投資組合的成分 ETF，含代號、名稱、配置比例、費用率（年）、幣別。

---

## 設定檔 `config.yaml`

### ETF 清單
```yaml
etfs:
  - ticker: VT
    exchange: US
    name: "Vanguard Total World Stock ETF"
    expense_ratio: 0.0006   # ← 費率（小數），US ETF 請每年手動確認
    # 查詢：https://investor.vanguard.com/investment-products/etfs/profile/vt

  - ticker: "0050"
    exchange: TWSE
    name: "元大台灣50"
    expense_ratio: 0.0022   # ← 由 fetch_expense_ratios.py --update 自動更新
    # 查詢：https://www.moneydj.com/ETF/X/Basic/Basic0004.xdjhtm?etfid=0050.TW
```

### 投資組合
```yaml
portfolios:
  - name: "全球均衡"
    allocations:
      VT: 0.60    # 比例總和必須 = 1.0
      IEF: 0.20
      "0050": 0.20
```

---

## 資料庫結構（DuckDB `data/etf.duckdb`）

| 資料表 | 說明 |
|--------|------|
| `etf_prices` | 每日 OHLCV，含幣別（TWD/USD） |
| `etf_info` | ETF 基本資訊，含費用率（由 pipeline Step 3 同步） |
| `fetch_status` | 各 ETF 最後爬取日期與狀態 |
| `fx_rates` | USD→TWD 每日匯率 |

---

## 年度維護建議

| 時機 | 動作 |
|------|------|
| 每個交易日 | `python scripts/update_data.py --once` 或設 cron |
| 每年初（1~2月） | `python scripts/pipeline.py` 更新費用率並重新輸出報表 |
| 新增 ETF 時 | 編輯 `config.yaml` → 執行 `python scripts/fetch_initial.py` |
| US ETF 費率有異動 | 手動更新 `config.yaml` 的 `expense_ratio` 欄位 |

---

## 依賴套件

```bash
pip install -r requirements.txt
# 需要額外安裝 Playwright（僅廢棄腳本使用，主流程不需要）
# python -m playwright install chromium
```

主要套件版本要求見 `requirements.txt`。
