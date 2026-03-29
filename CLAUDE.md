# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Taiwan ETF Portfolio Analysis System. Fetches historical price data from yfinance (US ETFs) and TWSE API (Taiwan ETFs), stores it in DuckDB, and provides regression analysis and portfolio comparison via a Flask web dashboard and Excel reports.

## Common Commands

```bash
# Start Flask dev server
make run                          # http://localhost:5000

# Data management
make init                         # Full historical fetch (first time, 30-60 min)
make update                       # Incremental daily update (one-time)
make update-watch                 # Scheduled update every 24h

# Full pipeline (fetch → dividends → expense ratios → DB → Excel report)
make pipeline                     # Complete pipeline (6 steps)
make pipeline-skip-fetch          # Skip data fetch (use existing DB data)
make pipeline-skip-expense        # Skip expense ratio fetch
make pipeline-skip-dividends      # Skip dividend records fetch
make pipeline-force               # Force full re-fetch from scratch

# DCA analysis report
make dca                          # Interactive: input monthly amount + nth trading day → Excel

# Database inspection
make db-status                    # Fetch status and last update dates per ticker
make db-prices                    # Record counts and date ranges per ticker

# Expense ratios
make expense                      # View latest Taiwan ETF expense ratios (read-only)
make expense-update               # Update config.yaml with latest ratios

# Standalone data fetches
make tracking-diff                # Fetch quarterly tracking diffs from MoneyDJ
make dividends                    # Fetch dividend records from MoneyDJ
```

## Architecture

### Data Flow
```
yfinance / TWSE API / MoneyDJ
    → scripts/fetch_initial.py (full) or scripts/update_data.py (incremental)
    → scripts/fetch_tracking_diff.py  → etf_tracking_diff
    → scripts/fetch_dividends.py      → etf_dividends
    → data/etf.duckdb (6 tables)
    → Flask API (app/routes/) or Excel report (scripts/analyze_portfolios.py)
    → Web dashboard (index.html + Plotly) or reports/*.xlsx
```

### Key Layers

**`config.yaml`** — Single source of truth for ETF universe (11 ETFs: 8 Taiwan + 3 US), 5 portfolios (weighted allocations), base currency (TWD), benchmark (VT), and DCA fee settings (`dca.fees`):
- `tw.fee_twd` / `tw.min_investment_twd` — flat fee and minimum per purchase for Taiwan ETFs (TWD)
- `us.fee_usd` / `us.min_investment_usd` — flat fee and minimum per purchase for US ETFs (USD, converted to TWD via FX rate)

**`app/db.py`** — DuckDB schema and connection management. The 6 tables:
- `etf_prices`: ticker, date, OHLCV, currency
- `etf_info`: ticker, name, exchange, expense_ratio, inception_date
- `fetch_status`: per-ticker fetch state and error tracking
- `fx_rates`: USD→TWD daily historical rates
- `etf_tracking_diff`: ticker, quarter (e.g. `2025Q4`), market_return, benchmark_return, diff (market − benchmark, %)
- `etf_dividends`: ticker, ex_date, amount_per_unit (native currency), currency — populated by `fetch_dividends.py` from MoneyDJ Basic0005 (last 2 years only)

**`app/services/regression.py`** — Beta, alpha (annualized), R², P-value vs benchmark; 60-day rolling beta.

**`app/services/portfolio.py`** — Portfolio cumulative returns (starting value=100), USD→TWD FX conversion, per-day expense deduction (`expense_ratio/252`), and stats (CAGR, volatility, Sharpe, max drawdown).

**`app/routes/`** — Three Flask blueprints:
- `charts.py` (`/api/prices/*`): raw prices, normalized (base=100), regression stats
- `portfolio.py` (`/api/portfolio/*`): cumulative returns and summary stats for all portfolios
- `info.py` (`/api/info/*`): ETF metadata and fetch status

**`scripts/dca_analysis.py`** — Dollar-cost averaging (DCA) simulation. Prompts for monthly amount and N-th trading day, splits by portfolio weights, buys at actual TWD prices, computes IRR (Newton-Raphson XIRR) and simple CAGR per period. Also reports quarterly tracking-error impact and dividend-reinvested returns. Outputs `reports/dca_analysis_YYYY-MM-DD.xlsx` with 5 sheets:
1. **DCA CAGR 各期間** — IRR & simple CAGR without dividends
2. **各期間詳細統計** — Full stats without dividends, including total fees paid
3. **追蹤誤差分析** — Tracking error per ETF and portfolio
4. **含股息再投資 CAGR** — IRR & simple CAGR with dividend reinvestment; portfolios using estimated dividends marked with `*`
5. **含股息再投資詳細** — Full stats with dividends + total fees paid + per-ETF dividend estimation notes

Fee logic (`_applyFee`): reads `config.yaml dca.fees`; applied to every purchase (regular DCA and dividend reinvestment alike). Purchases below the minimum investment threshold are skipped entirely. Fee amounts for US ETFs are converted from USD to TWD using the FX rate at the trade date. Total fees are tracked in `summary["total_fees"]` and shown in the Excel detail sheets.

Dividend reinvestment logic (`buildDividendCalendars`): MoneyDJ only provides the last 2 years of data. For years without actual records, the average annual per-unit dividend across all years with data is used as an estimate, distributed at the typical payment months (e.g. January & July for 0050). Estimated events are flagged `is_estimated=True`; the Excel sheets annotate this clearly.

**`scripts/fetch_dividends.py`** — Fetches ETF dividend records from MoneyDJ Basic0005 (last 2 years). Taiwan ETFs use `etfid=TICKER.TW`; US ETFs use `etfid=TICKER`. Skips if DB already has data within the last 45 days.

**`scripts/pipeline.py`** — Orchestrates the full 6-step pipeline in one command:
1. Fetch tracking diffs (`--skip-tracking-diff` to skip)
2. Fetch dividend records (`--skip-dividends` to skip)
3. Price data init / incremental update (`--skip-fetch` to skip)
4. Expense ratio update (`--skip-expense` to skip)
5. Sync expense ratios to DB
6. Portfolio analysis → Excel report

---

# 專案規範

## 網址安全檢查規則

在使用任何工具（WebFetch、瀏覽器、curl 等）存取外部網址之前，必須先執行以下流程：

1. **先至 [Norton Safe Web](https://safeweb.norton.com/) 查詢該網址的安全性**
2. 若 Norton Safe Web 顯示該網址有問題（警告、惡意、詐騙等），**直接阻擋，不得繼續存取**
3. 若 Norton Safe Web 顯示安全，再向使用者請求工具使用權限
4. **請求權限時，必須在訊息中說明「已透過 Norton Safe Web 確認該網址安全」**，並附上被查詢的網址

### 範例說明訊息格式

> 已透過 Norton Safe Web (https://safeweb.norton.com/) 確認以下網址安全，請求存取權限：
> `https://www.example.com/page`
