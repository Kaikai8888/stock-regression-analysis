// 全域狀態
const state = {
  startDate: "2020-01-01",
  endDate: new Date().toISOString().slice(0, 10),
  etfList: [],
};

// ── 初始化 ─────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("endDate").value = state.endDate;
  document.getElementById("startDate").value = state.startDate;

  setupTabs();
  setupDateRange();

  loadEtfInfo().then(() => {
    populateTickerSelect();
    populateNormalizedCheckboxes();
    loadActiveTab();
  });
});

function setupTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach((s) => s.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
      loadActiveTab();
    });
  });
}

function setupDateRange() {
  document.getElementById("applyDate").addEventListener("click", () => {
    state.startDate = document.getElementById("startDate").value;
    state.endDate = document.getElementById("endDate").value;
    loadActiveTab();
  });
}

function activeTab() {
  const btn = document.querySelector(".tab-btn.active");
  return btn ? btn.dataset.tab : "prices";
}

function loadActiveTab() {
  const tab = activeTab();
  if (tab === "prices") loadPricesTab();
  else if (tab === "normalized") loadNormalizedTab();
  else if (tab === "regression") loadRegressionTab();
  else if (tab === "portfolio") loadPortfolioTab();
  else if (tab === "etfinfo") renderEtfInfoTable(state.etfList);
}

// ── ETF 資訊載入 ────────────────────────────────

async function loadEtfInfo() {
  try {
    const resp = await fetch("/api/info/etfs");
    state.etfList = await resp.json();
  } catch (e) {
    console.error("無法載入 ETF 資訊", e);
    state.etfList = [];
  }
}

function populateTickerSelect() {
  const sel = document.getElementById("pricesTicker");
  sel.innerHTML = "";
  state.etfList.forEach((etf) => {
    const opt = document.createElement("option");
    opt.value = etf.ticker;
    opt.textContent = etf.ticker + (etf.name ? ` - ${etf.name}` : "");
    sel.appendChild(opt);
  });
  sel.addEventListener("change", loadPricesTab);
}

function populateNormalizedCheckboxes() {
  const container = document.getElementById("normalizedCheckboxes");
  container.innerHTML = "";
  state.etfList.forEach((etf, i) => {
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = etf.ticker;
    checkbox.checked = i < 4; // 預設選前 4 支
    checkbox.addEventListener("change", loadNormalizedTab);
    const span = document.createElement("span");
    span.textContent = etf.ticker;
    label.appendChild(checkbox);
    label.appendChild(span);
    container.appendChild(label);
  });
}

// ── ETF 資訊表格 ────────────────────────────────

function renderEtfInfoTable(etfList) {
  const tbody = document.querySelector("#etfInfoTable tbody");
  tbody.innerHTML = "";
  etfList.forEach((etf) => {
    const tr = document.createElement("tr");
    const expenseStr = etf.expense_ratio != null
      ? (etf.expense_ratio * 100).toFixed(2) + "%"
      : "N/A";
    const statusClass = "status-" + (etf.status || "not_fetched");
    const statusLabel = { ok: "正常", error: "錯誤", not_fetched: "未抓取", partial: "部分" }[etf.status] || etf.status;
    tr.innerHTML = `
      <td>${etf.ticker}</td>
      <td>${etf.name || "-"}</td>
      <td>${etf.exchange || "-"}</td>
      <td>${etf.currency || "-"}</td>
      <td>${expenseStr}</td>
      <td>${etf.last_fetched_date || "-"}</td>
      <td class="${statusClass}">${statusLabel}</td>
    `;
    tbody.appendChild(tr);
  });
}
