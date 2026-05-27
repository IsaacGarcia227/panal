(() => {
  "use strict";

  const MESES = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"];

  function fmtTs(ts) {
    if (!ts) return "—";
    const norm = ts.replace("T", " ");
    const dd   = norm.slice(8, 10);
    const mon  = parseInt(norm.slice(5, 7), 10) - 1;
    const hhmm = norm.slice(11, 16);
    return `${dd} ${MESES[mon]} ${hhmm}`;
  }

  const LABEL_TO_STATE = {
    SANA: { cls: "ok", text: "Colmena sana" },
    ENJAMBRAZON: { cls: "alert", text: "¡Enjambrazón detectada!" },
    ESTRES_TERMICO: { cls: "warn", text: "Estrés térmico (Santa Ana)" },
    RESERVAS_BAJAS: { cls: "info", text: "Reservas bajas" },
    SIN_DATOS: { cls: "info", text: "Sin datos" },
    DESCONOCIDO: { cls: "info", text: "Desconocido" },
  };

  const els = {
    badge: document.getElementById("health-badge"),
    badgeLabel: document.querySelector("#health-badge .label"),
    weightValue: document.getElementById("weight-value"),
    weightFill: document.getElementById("weight-fill"),
    forecastDays: document.getElementById("forecast-days"),
    forecastDetail: document.getElementById("forecast-detail"),
    freshness: document.getElementById("freshness"),
    freshnessText: document.querySelector("#freshness .freshness-text"),
    timeline: document.getElementById("timeline"),
    timelineCount: document.getElementById("timeline-count"),
    timelineEmpty: document.getElementById("timeline-empty"),
    probGrid: document.getElementById("prob-grid"),
    lastUpdate: document.getElementById("last-update"),
    trendTitle: document.getElementById("trend-title"),
    trendRange: document.getElementById("trend-range"),
    chips: Array.from(document.querySelectorAll(".chip[data-preset]")),
  };

  // --- Trend chart --------------------------------------------------------
  // Chart with event markers and zoom support
const chart = new Chart(document.getElementById("trend-chart"), {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Peso (kg)",
          data: [],
          yAxisID: "y",
          borderColor: "#f6c026",
          backgroundColor: "rgba(246, 192, 38, 0.18)",
          tension: 0.35,
          fill: true,
          pointRadius: 0,
          borderWidth: 2,
        },
        {
          label: "Temperatura (°C)",
          data: [],
          yAxisID: "y1",
          borderColor: "#4aa8ff",
          backgroundColor: "rgba(74, 168, 255, 0.12)",
          tension: 0.35,
          fill: false,
          pointRadius: 0,
          borderWidth: 2,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      resizeDelay: 100,
      animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { color: "#e7edf3" } },
        tooltip: { backgroundColor: "#181d24", borderColor: "#2a323d", borderWidth: 1 },
      },
      scales: {
        x: {
          ticks: { color: "#8a96a6", maxRotation: 0, autoSkip: true, maxTicksLimit: 10 },
          grid: { color: "rgba(255,255,255,0.04)" },
        },
        y: {
          position: "left",
          ticks: { color: "#f6c026" },
          grid: { color: "rgba(255,255,255,0.04)" },
          title: { display: true, text: "kg", color: "#f6c026" },
        },
        y1: {
          position: "right",
          ticks: { color: "#4aa8ff" },
          grid: { drawOnChartArea: false },
          title: { display: true, text: "°C", color: "#4aa8ff" },
        },
      },
    },
  });

  // --- Filter state -------------------------------------------------------
  let currentPreset = "24h";
  let datasetMeta = null;

  // Biological thresholds — populated from /api/meta. Fallbacks aquí solo
  // cubren el caso de que el frontend cargue antes de tener metadata.
  let biology = {
    setpoint_c: 33,
    stress_temp_c: 30,
    harvest_goal_kg: 40,
    gauge_temp_min_c: 24,
    gauge_temp_max_c: 32,
  };

  function setActivePreset(name) {
    currentPreset = name;
    for (const chip of els.chips) {
      chip.classList.toggle("active", chip.dataset.preset === name);
    }
  }

  function currentFilterParams() {
    const params = new URLSearchParams();
    params.set("preset", currentPreset);
    return params;
  }

  // --- Helpers ------------------------------------------------------------
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const fmtSigned = (v, unit = "") =>
    `${v > 0 ? "+" : ""}${v.toFixed(2)}${unit ? " " + unit : ""}`;

  function paintBadge(label) {
    const state = LABEL_TO_STATE[label] || LABEL_TO_STATE.DESCONOCIDO;
    els.badge.classList.remove("ok", "warn", "alert", "info");
    els.badge.classList.add(state.cls);
    els.badgeLabel.textContent = state.text;
  }

  function paintGauges(latest) {
    const weight = latest.peso_kg;
    els.weightValue.textContent = weight.toFixed(2);
    const goal = biology.harvest_goal_kg;
    const wPct = clamp((weight / goal) * 100, 0, 100);
    els.weightFill.style.width = `${wPct}%`;
  }

  function paintForecast(forecast, health) {
    const slope = forecast.slope_kg_per_day;
    const hist = forecast.historical_slope_kg_per_day;
    const sourceTag = forecast.source === "historical" ? " (ritmo histórico)" : "";

    if (forecast.days_to_goal === null) {
      els.forecastDays.textContent = `Perdiendo ${Math.abs(slope).toFixed(2)} kg/día`;
      els.forecastDetail.textContent = forecast.message;
    } else if (forecast.days_to_goal === 0) {
      els.forecastDays.textContent = "Meta alcanzada";
      els.forecastDetail.textContent = forecast.message;
    } else {
      els.forecastDays.textContent = `${forecast.days_to_goal} días`;
      els.forecastDetail.textContent =
        `A ritmo actual${sourceTag} ${fmtSigned(slope, "kg/día")}` +
        (hist != null && Math.abs(hist - slope) > 0.01
          ? ` · histórico sano: ${fmtSigned(hist, "kg/día")}`
          : "");
    }

  }

  function paintFreshness(lastTs) {
    if (!lastTs) {
      els.freshnessText.textContent = "—";
      return;
    }
    els.freshnessText.textContent = `Actualizados hasta ${fmtTs(lastTs)}`;
  }

  function paintTimeline(events) {
    els.timeline.innerHTML = "";
    if (!events || events.length === 0) {
      els.timelineEmpty.hidden = false;
      els.timelineCount.textContent = "0 eventos";
      return;
    }
    els.timelineEmpty.hidden = true;
    els.timelineCount.textContent =
      events.length === 1 ? "1 evento" : `${events.length} eventos`;

    // Mostrar más reciente primero
    for (const ev of [...events].reverse()) {
      const li = document.createElement("li");
      li.className = `to-${ev.to}`;
      li.innerHTML = `
        <span class="timeline-time">${fmtTs(ev.timestamp)}</span>
        <span class="timeline-transition">
          <span class="timeline-from">${ev.from.replace("_", " ")}</span>
          <span class="timeline-arrow">→</span>
          <span class="timeline-to">${ev.to.replace("_", " ")}</span>
        </span>
      `;
      els.timeline.appendChild(li);
    }
  }

  function paintProbabilities(health) {
    const probs = health.probabilities || {};
    const entries = Object.entries(probs).sort((a, b) => b[1] - a[1]);
    els.probGrid.innerHTML = "";
    for (const [name, p] of entries) {
      // 1 decimal — así 0.995 + 0.005 = 99.5% + 0.5% = 100.0% (suma exacta)
      // en lugar de los 101% que generaba Math.round.
      const pct = (p * 100).toFixed(1);
      const cell = document.createElement("div");
      cell.className = "prob-cell" + (name === health.label ? " dominant" : "");
      cell.innerHTML = `
        <h3>${name.replace("_", " ")}</h3>
        <strong>${pct}%</strong>
        <div class="bar"><div style="width:${pct}%"></div></div>
      `;
      els.probGrid.appendChild(cell);
    }
  }

  function snappedRange(values, { padFrac = 0.2, minSpan = 1, step = 1 } = {}) {
    if (!values.length) return { min: undefined, max: undefined };
    const lo = Math.min(...values);
    const hi = Math.max(...values);
    const span = Math.max(hi - lo, minSpan);
    const pad = span * padFrac;
    return {
      min: Math.floor((lo - pad) / step) * step,
      max: Math.ceil((hi + pad) / step) * step,
    };
  }

  function tickLabel(tsStr, spanHours) {
    const norm = tsStr.replace("T", " ");
    const dd   = norm.slice(8, 10);
    const mon  = parseInt(norm.slice(5, 7), 10) - 1;
    const hhmm = norm.slice(11, 16);
    if (spanHours <= 24 * 14) return `${dd} ${MESES[mon]} ${hhmm}`;
    return `${dd} ${MESES[mon]}`;
  }

  function paintChart(history, win, events) {
    if (!history || !history.length) {
      chart.data.labels = [];
      chart.data.datasets.forEach((d) => (d.data = []));
      chart.update("none");
      return;
    }
    const spanHours =
      (new Date(win.end) - new Date(win.start)) / 1000 / 3600;

    const labels = history.map((r) => tickLabel(r.timestamp, spanHours));
    const weights = history.map((r) => r.peso_kg);
    const temps = history.map((r) => r.temperatura_c);

    chart.data.labels = labels;
    chart.data.datasets[0].data = weights;
    chart.data.datasets[1].data = temps;

    const w = snappedRange(weights, { padFrac: 0.2, minSpan: 1, step: 1 });
    const t = snappedRange(temps,   { padFrac: 0.2, minSpan: 1, step: 1 });
    chart.options.scales.y.min  = w.min;
    chart.options.scales.y.max  = w.max;
    chart.options.scales.y1.min = t.min;
    chart.options.scales.y1.max = t.max;

    chart.update("none");
  }

  function paintTrendHeader(win) {
    const fmt = (iso) => iso.replace("T", " ").slice(0, 16);
    let title = "Tendencia";
    if (win.label === "5min") title = "Tendencia últimos 5 minutos";
    else if (win.label === "24h") title = "Tendencia últimas 24 h";
    else if (win.label === "7d") title = "Tendencia últimos 7 días";
    else if (win.label === "30d") title = "Tendencia últimos 30 días";
    else if (win.label === "all") title = "Tendencia · dataset completo";
    else if (win.label === "custom") title = "Tendencia · rango personalizado";
    else title = `Tendencia · ${win.label}`;
    els.trendTitle.textContent = title;
    els.trendRange.textContent = `${fmt(win.start)} → ${fmt(win.end)}`;
  }

  // --- Network ------------------------------------------------------------
  async function safeJson(url) {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) {
      const detail = await r.json().catch(() => ({}));
      throw new Error(detail.detail || `${url} -> HTTP ${r.status}`);
    }
    return r.json();
  }

  async function loadMeta() {
    datasetMeta = await safeJson("/api/meta");
    if (datasetMeta.biology) biology = { ...biology, ...datasetMeta.biology };
  }

  async function applyFilters() {
    try {
      const params = currentFilterParams();
      const data = await safeJson(`/api/window?${params.toString()}`);
      paintTrendHeader(data.window);
      paintBadge(data.health.label);
      paintGauges(data.latest);
      paintForecast(data.forecast, data.health);
      paintFreshness(data.window.end);
      paintProbabilities(data.health);
      paintChart(data.history, data.window);
      paintTimeline(data.timeline);
      els.lastUpdate.textContent = new Date().toLocaleString("es-MX");
    } catch (err) {
      console.error(err);
      paintBadge("DESCONOCIDO");
      els.trendRange.textContent = "sin datos en el rango";
      chart.data.labels = [];
      chart.data.datasets.forEach((d) => (d.data = []));
      chart.update("none");
      els.lastUpdate.textContent = "error: " + err.message;
    }
  }

  // --- Wiring -------------------------------------------------------------
  for (const chip of els.chips) {
    chip.addEventListener("click", () => {
      setActivePreset(chip.dataset.preset);
      applyFilters();
    });
  }

  // --- Boot ---------------------------------------------------------------
  (async () => {
    try {
      await loadMeta();
    } catch (err) {
      console.error("meta load failed", err);
    }
    setActivePreset("24h");
    applyFilters();

    // Auto-refresh cada 5 s
    setInterval(() => applyFilters(), 5000);
  })();
})();
