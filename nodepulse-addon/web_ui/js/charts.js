/**
 * NodePulse Web UI — Chart Manager
 *
 * Manages the five signal history charts on the Dashboard:
 *   1. SNR history (dB) for the selected/most-recently-heard node.
 *   2. RSSI history (dBm) for the same node.
 *   3. Node count over time.
 *   4. Channel utilization (%) — longer window for trends.
 *   5. Airtime utilization (%) — longer window for trends.
 *
 * Charts 1-3 use a short rolling window; charts 4-5 use a longer window (120
 * points ≈ 30 min at 15 s poll interval) so the user can see medium-term
 * airtime/channel congestion trends that are not visible on the short-term
 * charts.
 *
 * Chart.js is loaded from CDN in index.html — we access it via the global `Chart`.
 */

const SHORT_WINDOW = 30;    // rolling window for SNR/RSSI/count (~7.5 min)
const LONG_WINDOW  = 120;   // rolling window for utilization (~30 min)

const CHART_COLOR_TEAL   = '#00d4aa';
const CHART_COLOR_BLUE   = '#4fc3f7';
const CHART_COLOR_PURPLE = '#a78bfa';
const CHART_COLOR_ORANGE = '#ff7043';
const CHART_COLOR_PINK   = '#f06292';

/** Shared Chart.js defaults applied to every chart to match the dark theme. */
const SHARED_DEFAULTS = {
  responsive: true,
  maintainAspectRatio: false,
  animation: { duration: 300 },
  plugins: {
    legend: { display: false },
    tooltip: {
      backgroundColor: '#141c2e',
      borderColor: 'rgba(0,212,170,0.25)',
      borderWidth: 1,
      titleColor: '#8892a4',
      bodyColor: '#e8eaf0',
    },
  },
  scales: {
    x: {
      display: false,
    },
    y: {
      grid:   { color: 'rgba(255,255,255,0.04)', drawBorder: false },
      ticks:  { color: '#4a5568', font: { size: 10, family: "'JetBrains Mono'" } },
      border: { display: false },
    },
  },
};

function _makeDataset(label, color, data) {
  return {
    label,
    data,
    borderColor: color,
    backgroundColor: color + '18',
    borderWidth: 2,
    pointRadius: 0,
    pointHoverRadius: 4,
    tension: 0.4,
    fill: true,
    spanGaps: true,
  };
}

function _rollingPush(arr, value, max) {
  arr.push(value);
  if (arr.length > max) arr.shift();
}

export class ChartManager {
  constructor() {
    this._charts = {};
  }

  init() {
    this._charts.snr = new Chart(
      document.getElementById('chart-snr'),
      {
        type: 'line',
        data: { labels: [], datasets: [_makeDataset('SNR (dB)', CHART_COLOR_TEAL, [])] },
        options: {
          ...SHARED_DEFAULTS,
          scales: { ...SHARED_DEFAULTS.scales, y: { ...SHARED_DEFAULTS.scales.y, title: { display: true, text: 'SNR (dB)', color: '#4a5568', font: { size: 10 } } } },
        },
      }
    );

    this._charts.rssi = new Chart(
      document.getElementById('chart-rssi'),
      {
        type: 'line',
        data: { labels: [], datasets: [_makeDataset('RSSI (dBm)', CHART_COLOR_BLUE, [])] },
        options: {
          ...SHARED_DEFAULTS,
          scales: { ...SHARED_DEFAULTS.scales, y: { ...SHARED_DEFAULTS.scales.y, title: { display: true, text: 'RSSI (dBm)', color: '#4a5568', font: { size: 10 } } } },
        },
      }
    );

    this._charts.count = new Chart(
      document.getElementById('chart-count'),
      {
        type: 'line',
        data: { labels: [], datasets: [_makeDataset('Node count', CHART_COLOR_PURPLE, [])] },
        options: {
          ...SHARED_DEFAULTS,
          scales: { ...SHARED_DEFAULTS.scales, y: { ...SHARED_DEFAULTS.scales.y, min: 0, ticks: { ...SHARED_DEFAULTS.scales.y.ticks, stepSize: 1 }, title: { display: true, text: 'Nodes', color: '#4a5568', font: { size: 10 } } } },
        },
      }
    );

    this._charts.chanUtil = new Chart(
      document.getElementById('chart-chan-util'),
      {
        type: 'line',
        data: { labels: [], datasets: [_makeDataset('Chan Util %', CHART_COLOR_ORANGE, [])] },
        options: {
          ...SHARED_DEFAULTS,
          scales: { ...SHARED_DEFAULTS.scales, y: { ...SHARED_DEFAULTS.scales.y, min: 0, max: 100, title: { display: true, text: '%', color: '#4a5568', font: { size: 10 } } } },
        },
      }
    );

    this._charts.airUtil = new Chart(
      document.getElementById('chart-air-util'),
      {
        type: 'line',
        data: { labels: [], datasets: [_makeDataset('Air Util %', CHART_COLOR_PINK, [])] },
        options: {
          ...SHARED_DEFAULTS,
          scales: { ...SHARED_DEFAULTS.scales, y: { ...SHARED_DEFAULTS.scales.y, min: 0, max: 100, title: { display: true, text: '%', color: '#4a5568', font: { size: 10 } } } },
        },
      }
    );
  }

  /**
   * Ingest a new data point. Called by the main poll loop.
   *
   * @param {number|null} snr      - SNR in dB for the currently selected node.
   * @param {number|null} rssi     - RSSI in dBm for the same node.
   * @param {number}      count    - Total node count.
   * @param {number|null} chanUtil - Channel utilization % (from self node).
   * @param {number|null} airUtil  - Airtime utilization % (from self node).
   */
  addPoint(snr, rssi, count, chanUtil, airUtil) {
    if (!this._charts.snr) return;
    const label = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    const updateChart = (chart, val, windowSize) => {
      if (!chart) return;
      _rollingPush(chart.data.labels, label, windowSize);
      _rollingPush(chart.data.datasets[0].data, val ?? null, windowSize);
      chart.update('none');
    };

    updateChart(this._charts.snr, snr, SHORT_WINDOW);
    updateChart(this._charts.rssi, rssi, SHORT_WINDOW);
    updateChart(this._charts.count, count, SHORT_WINDOW);
    updateChart(this._charts.chanUtil, chanUtil, LONG_WINDOW);
    updateChart(this._charts.airUtil, airUtil, LONG_WINDOW);
  }
}
