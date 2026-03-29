# ============================================================
# ETF 投資組合分析 — 常用指令
# 使用方式：make <target>
# ============================================================

PYTHON := python3

.PHONY: help run init update update-watch expense expense-update tracking-diff \
        pipeline pipeline-skip-fetch pipeline-skip-expense pipeline-force \
        report dca db-status db-prices

# ── 預設：顯示說明 ──────────────────────────────────────────
help:
	@echo ""
	@echo "ETF 投資組合分析 — 可用指令"
	@echo "============================================================"
	@echo ""
	@echo "【資料管理】"
	@echo "  make init                首次全量抓取所有 ETF 歷史資料"
	@echo "                           （含新增股票；耗時 30~60 分鐘）"
	@echo "  make update              增量更新至最新交易日"
	@echo "                           （config.yaml 中的新股票也會自動全量初始化）"
	@echo "  make update-watch        常駐模式，每 24 小時自動更新"
	@echo "  make expense             查詢台灣 ETF 最新費用率（不修改任何檔案）"
	@echo "  make expense-update      從 MoneyDJ 抓取費用率並更新 config.yaml"
	@echo "  make tracking-diff       抓取所有 ETF 季度追蹤差額（已有上季資料則跳過）"
	@echo ""
	@echo "【Pipeline 完整流程】"
	@echo "  make pipeline            完整流程：追蹤差額 → 資料更新 → 費用率 → Excel 報表"
	@echo "  make pipeline-skip-fetch 跳過資料抓取，只更新費用率 + 產報表"
	@echo "                           （DB 已是最新時使用）"
	@echo "  make pipeline-skip-expense 跳過費用率更新，只更新資料 + 產報表"
	@echo "                           （日常使用）"
	@echo "  make pipeline-force      強制重抓所有歷史資料（完整重建 DB）"
	@echo "  make report              只產出 Excel 報告，不更新任何資料"
	@echo "  make dca                 定期定額分析（互動輸入每月金額與投資日）→ Excel 報表"
	@echo ""
	@echo "【Web App】"
	@echo "  make run                 啟動 Flask 開發伺服器（http://localhost:5000）"
	@echo ""
	@echo "【資料庫查詢】"
	@echo "  make db-status           顯示各 ETF 的抓取狀態與最後更新日期"
	@echo "  make db-prices           顯示各 ETF 的資料筆數與最新日期"
	@echo ""

# ── Web App ─────────────────────────────────────────────────
run:
	$(PYTHON) run.py

# ── 資料管理 ─────────────────────────────────────────────────

# 首次全量抓取（或手動補某支新股票時，可改用 make update）
init:
	$(PYTHON) scripts/fetch_initial.py

# 增量更新（config.yaml 有新增股票時，會自動全量初始化該股票）
update:
	$(PYTHON) scripts/update_data.py --once

# 常駐模式，每 24 小時自動更新
update-watch:
	$(PYTHON) scripts/update_data.py --schedule

# 查詢最新台灣 ETF 費用率，只輸出不修改
expense:
	$(PYTHON) scripts/fetch_expense_ratios.py

# 從 MoneyDJ 抓取費用率並更新 config.yaml
expense-update:
	$(PYTHON) scripts/fetch_expense_ratios.py --update

# 從 MoneyDJ 抓取所有 ETF 季度追蹤差額（已有上季資料則跳過）
tracking-diff:
	$(PYTHON) scripts/fetch_tracking_diff.py

# ── Pipeline ─────────────────────────────────────────────────

# 完整流程：資料更新 → 費用率更新 → 產出 Excel 報表
pipeline:
	$(PYTHON) scripts/pipeline.py

# 跳過資料抓取：只更新費用率 + 產報表（DB 已是最新時使用）
pipeline-skip-fetch:
	$(PYTHON) scripts/pipeline.py --skip-fetch

# 跳過費用率更新：資料更新 + 產報表（日常使用；費用率非每日變動）
pipeline-skip-expense:
	$(PYTHON) scripts/pipeline.py --skip-expense

# 強制重抓所有 ETF 歷史資料，完整重建 DB
pipeline-force:
	$(PYTHON) scripts/pipeline.py --force-initial

# 只產出 Excel 報告（不抓資料、不更新費用率）
report:
	$(PYTHON) scripts/analyze_portfolios.py

# 定期定額（DCA）分析報告（互動式輸入每月金額與投資日）
dca:
	$(PYTHON) scripts/dca_analysis.py

# ── 資料庫查詢 ───────────────────────────────────────────────

# 顯示各 ETF 的抓取狀態與最後更新日期
db-status:
	$(PYTHON) -c "\
import duckdb; \
conn = duckdb.connect('data/etf.duckdb', read_only=True); \
rows = conn.execute('SELECT ticker, last_fetched_date, status, error_msg FROM fetch_status ORDER BY ticker').fetchall(); \
print(f'{'Ticker':<10} {'Last Fetched':<15} {'Status':<8} Error'); \
print('-' * 60); \
[print(f'{r[0]:<10} {str(r[1]):<15} {r[2]:<8} {r[3] or \"\"}') for r in rows]; \
conn.close()"

# 顯示各 ETF 的資料筆數與最新日期
db-prices:
	$(PYTHON) -c "\
import duckdb; \
conn = duckdb.connect('data/etf.duckdb', read_only=True); \
rows = conn.execute('SELECT ticker, COUNT(*) as cnt, MIN(date) as first, MAX(date) as last FROM etf_prices GROUP BY ticker ORDER BY ticker').fetchall(); \
print(f'{'Ticker':<10} {'Count':>7}  {'First Date':<12} {'Last Date':<12}'); \
print('-' * 50); \
[print(f'{r[0]:<10} {r[1]:>7}  {str(r[2]):<12} {str(r[3]):<12}') for r in rows]; \
conn.close()"
