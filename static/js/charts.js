// ── 個股走勢圖 ──────────────────────────────────

async function loadPricesTab() {
  const ticker = document.getElementById("pricesTicker").value;
  if (!ticker) return;

  const url = `/api/prices/${ticker}?start=${state.startDate}&end=${state.endDate}`;
  try {
    const resp = await fetch(url);
    const data = await resp.json();
    renderCandlestickChart("pricesChart", ticker, data);
  } catch (e) {
    console.error("無法載入個股資料", e);
  }
}

function renderCandlestickChart(divId, ticker, data) {
  if (!data || data.length === 0) {
    Plotly.purge(divId);
    document.getElementById(divId).innerHTML = '<p style="padding:2rem;color:#999;">無資料</p>';
    return;
  }

  const dates = data.map((d) => d.date);
  const closes = data.map((d) => d.close);

  // 如果有完整 OHLC 資料，畫 candlestick；否則畫 line
  const hasOhlc = data[0].open != null && data[0].high != null && data[0].low != null;

  const traces = hasOhlc
    ? [
        {
          type: "candlestick",
          x: dates,
          open: data.map((d) => d.open),
          high: data.map((d) => d.high),
          low: data.map((d) => d.low),
          close: closes,
          name: ticker,
          increasing: { line: { color: "#e74c3c" } },
          decreasing: { line: { color: "#27ae60" } },
        },
      ]
    : [{ type: "scatter", mode: "lines", x: dates, y: closes, name: ticker }];

  // 成交量
  const volumeTrace = {
    type: "bar",
    x: dates,
    y: data.map((d) => d.volume),
    name: "成交量",
    yaxis: "y2",
    marker: { color: "rgba(100,150,200,0.4)" },
  };

  const layout = {
    title: ticker + " 歷史走勢",
    xaxis: { rangeslider: { visible: false } },
    yaxis: { title: "價格", domain: [0.25, 1] },
    yaxis2: { title: "成交量", domain: [0, 0.2] },
    margin: { t: 50, b: 40, l: 60, r: 20 },
    paper_bgcolor: "white",
    plot_bgcolor: "#fafafa",
  };

  Plotly.newPlot(divId, [...traces, volumeTrace], layout, { responsive: true });
}

// ── 正規化比較圖 ────────────────────────────────

async function loadNormalizedTab() {
  const checked = Array.from(
    document.querySelectorAll("#normalizedCheckboxes input:checked")
  ).map((cb) => cb.value);

  if (checked.length === 0) {
    Plotly.purge("normalizedChart");
    return;
  }

  const url = `/api/prices/normalized?tickers=${checked.join(",")}&start=${state.startDate}&end=${state.endDate}`;
  try {
    const resp = await fetch(url);
    const data = await resp.json();
    renderNormalizedChart("normalizedChart", data);
  } catch (e) {
    console.error("無法載入正規化資料", e);
  }
}

function renderNormalizedChart(divId, data) {
  const traces = Object.entries(data).map(([ticker, series]) => ({
    type: "scatter",
    mode: "lines",
    name: ticker,
    x: series.map((d) => d.date),
    y: series.map((d) => d.value),
  }));

  if (traces.length === 0) {
    document.getElementById(divId).innerHTML = '<p style="padding:2rem;color:#999;">無資料</p>';
    return;
  }

  const layout = {
    title: "ETF 正規化比較（起始值 = 100，已換算為 TWD）",
    xaxis: { title: "日期" },
    yaxis: { title: "指數（100 = 起始日）" },
    margin: { t: 50, b: 60, l: 70, r: 20 },
    hovermode: "x unified",
    paper_bgcolor: "white",
    plot_bgcolor: "#fafafa",
  };

  Plotly.newPlot(divId, traces, layout, { responsive: true });
}

// ── 回歸分析 ────────────────────────────────────

async function loadRegressionTab() {
  const url = `/api/prices/regression?start=${state.startDate}&end=${state.endDate}`;
  try {
    const resp = await fetch(url);
    const data = await resp.json();
    renderRegressionTable(data);
    renderRollingBetaChart("rollingBetaChart", data.rolling_beta || {});
  } catch (e) {
    console.error("無法載入回歸分析資料", e);
  }
}

function renderRegressionTable(data) {
  document.getElementById("benchmarkLabel").textContent = data.benchmark || "";
  const tbody = document.querySelector("#regressionTable tbody");
  tbody.innerHTML = "";
  const stats = data.stats || {};
  Object.entries(stats).forEach(([ticker, s]) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${ticker}</td>
      <td>${s.beta}</td>
      <td>${(s.alpha * 100).toFixed(3)}%</td>
      <td>${s.r_squared}</td>
      <td>${s.p_value}</td>
      <td>${s.n_obs}</td>
    `;
    tbody.appendChild(tr);
  });
  if (!Object.keys(stats).length) {
    tbody.innerHTML = '<tr><td colspan="6" style="color:#999;padding:1rem">無資料（請先執行 fetch_initial.py）</td></tr>';
  }
}

function renderRollingBetaChart(divId, rollingBeta) {
  const traces = Object.entries(rollingBeta).map(([ticker, series]) => ({
    type: "scatter",
    mode: "lines",
    name: ticker,
    x: series.map((d) => d.date),
    y: series.map((d) => d.beta),
  }));

  if (traces.length === 0) return;

  // Beta = 1 基準線
  const allDates = traces.flatMap((t) => t.x);
  const minDate = allDates.reduce((a, b) => (a < b ? a : b));
  const maxDate = allDates.reduce((a, b) => (a > b ? a : b));
  traces.push({
    type: "scatter",
    mode: "lines",
    name: "Beta = 1",
    x: [minDate, maxDate],
    y: [1, 1],
    line: { dash: "dot", color: "#999", width: 1 },
    showlegend: false,
  });

  const layout = {
    title: "滾動 Beta（60日，相對於 " + (document.getElementById("benchmarkLabel").textContent) + "）",
    xaxis: { title: "日期" },
    yaxis: { title: "Beta" },
    margin: { t: 50, b: 60, l: 60, r: 20 },
    hovermode: "x unified",
    paper_bgcolor: "white",
    plot_bgcolor: "#fafafa",
  };

  Plotly.newPlot(divId, traces, layout, { responsive: true });
}
