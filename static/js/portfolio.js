// ── 投資組合比較 ────────────────────────────────

async function loadPortfolioTab() {
  const compareUrl = `/api/portfolio/compare?start=${state.startDate}&end=${state.endDate}`;
  const summaryUrl = `/api/portfolio/summary?start=${state.startDate}&end=${state.endDate}`;

  try {
    const [compareResp, summaryResp] = await Promise.all([
      fetch(compareUrl),
      fetch(summaryUrl),
    ]);
    const compareData = await compareResp.json();
    const summaryData = await summaryResp.json();

    renderPortfolioChart("portfolioChart", compareData);
    renderPortfolioSummaryTable(summaryData);
  } catch (e) {
    console.error("無法載入投資組合資料", e);
  }
}

function renderPortfolioChart(divId, data) {
  const traces = Object.entries(data).map(([name, series]) => ({
    type: "scatter",
    mode: "lines",
    name,
    x: series.map((d) => d.date),
    y: series.map((d) => d.value),
  }));

  if (traces.length === 0) {
    document.getElementById(divId).innerHTML =
      '<p style="padding:2rem;color:#999;">無資料（請先執行 fetch_initial.py）</p>';
    return;
  }

  const layout = {
    title: "投資組合累積報酬比較（起始值 = 100，已扣管理費，幣別換算為 TWD）",
    xaxis: { title: "日期" },
    yaxis: { title: "累積指數（100 = 起始日）" },
    margin: { t: 55, b: 60, l: 70, r: 20 },
    hovermode: "x unified",
    paper_bgcolor: "white",
    plot_bgcolor: "#fafafa",
  };

  Plotly.newPlot(divId, traces, layout, { responsive: true });
}

function renderPortfolioSummaryTable(data) {
  const tbody = document.querySelector("#portfolioSummary tbody");
  tbody.innerHTML = "";

  Object.entries(data).forEach(([name, stats]) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${name}</td>
      <td>${stats.total_return != null ? stats.total_return + "%" : "-"}</td>
      <td>${stats.cagr != null ? stats.cagr + "%" : "-"}</td>
      <td>${stats.volatility != null ? stats.volatility + "%" : "-"}</td>
      <td>${stats.sharpe != null ? stats.sharpe : "-"}</td>
      <td>${stats.max_drawdown != null ? stats.max_drawdown + "%" : "-"}</td>
    `;
    tbody.appendChild(tr);
  });

  if (!Object.keys(data).length) {
    tbody.innerHTML =
      '<tr><td colspan="6" style="color:#999;padding:1rem">無資料（請先執行 fetch_initial.py）</td></tr>';
  }
}
