/**
 * NodePulse Web UI — Chart Manager
 *
 * Manages the three signal history charts on the Dashboard:
 *   1. SNR history (dB) for the selected/most-recently-heard node.
 *   2. RSSI history (dBm) for the same node.
 *   3. Node count over time.
 *
 * Each chart maintains a rolling window of MAX_POINTS data points.
 * When a new reading arrives we append it and shift the oldest off the front,
 * keeping memory usage bounded.
 *
 * Chart.js is loaded from CDN in index.html — we access it via the global `Chart`.
 */

const MAX_POINTS = 30;   // rolling window size
const CHART_COLOR_TEAL   = '#00d4aa';
const CHART_COLOR_BLUE   = '#4fc3f7';
const CHART_COLOR_PURPLE = '#a78bfa';

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
      display: false,  // timestamps hidden — they clutter the small chart
    },
    y: {
      grid:   { color: 'rgba(255,255,255,0.04)', drawBorder: false },
      ticks:  { color: '#4a5568', font: { size: 10, family: "'JetBrains Mono'" } },
      border: { display: false },
    },
  },
};

/**
 * Create a line dataset config for Chart.js.
 * Extracted to avoid repeating the same style config three times.
 */
function _makeDataset(label, color, data) {
  return {
    label,
    data,
    borderColor: color,
    backgroundColor: color + '18',  // 10% opacity fill
    borderWidth: 2,
    pointRadius: 0,          // no dots — cleaner with dense data
    pointHoverRadius: 4,
    tension: 0.4,             // smooth bezier curves
    fill: true,
  };
}

/** Push a value onto a rolling array, evicting the oldest when full. */
function _rollingPush(arr, value) {
  arr.push(value);
  if (arr.length > MAX_POINTS) arr.shift();
}

export class ChartManager {
  constructor() {
    this._snrData    = [];
    this._rssiData   = [];
    this._countData  = [];
    this._labels     = [];  // shared time labels (HH:MM:SS)

    this._charts = {};  // { snr, rssi, count }
  }

  /**
   * Initialise the three Chart.js instances.
   * Must be called after the canvas elements are in the DOM.
   */
  init() {
    this._charts.snr = new Chart(
      document.getElementById('chart-snr'),
      {
        type: 'line',
        data: { labels: this._labels, datasets: [_makeDataset('SNR (dB)', CHART_COLOR_TEAL, this._snrData)] },
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
        data: { labels: this._labels, datasets: [_makeDataset('RSSI (dBm)', CHART_COLOR_BLUE, this._rssiData)] },
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
        data: { labels: this._labels, datasets: [_makeDataset('Node count', CHART_COLOR_PURPLE, this._countData)] },
        options: {
          ...SHARED_DEFAULTS,
          scales: { ...SHARED_DEFAULTS.scales, y: { ...SHARED_DEFAULTS.scales.y, min: 0, ticks: { ...SHARED_DEFAULTS.scales.y.ticks, stepSize: 1 }, title: { display: true, text: 'Nodes', color: '#4a5568', font: { size: 10 } } } },
        },
      }
    );
  }

  /**
   * Ingest a new data point. Called by the main poll loop.
   *
   * @param {number|null} snr   - SNR in dB for the currently selected node.
   * @param {number|null} rssi  - RSSI in dBm for the same node.
   * @param {number}      count - Total node count from the node list.
   */
  addPoint(snr, rssi, count) {
    const label = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    _rollingPush(this._labels,   label);
    _rollingPush(this._snrData,  snr  ?? null);
    _rollingPush(this._rssiData, rssi ?? null);
    _rollingPush(this._countData, count);

    // Chart.js updates in-place without a full re-render when we call update().
    Object.values(this._charts).forEach(c => c.update('none'));
  }
}
