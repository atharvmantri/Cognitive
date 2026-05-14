/**
 * Cognitive Extension - Signal Aggregator
 * Runs in background.js to aggregate raw signals from content scripts
 * into time-binned feature vectors for API transmission.
 */

'use strict';

const AGGREGATION_WINDOW_MS = 5000; // 5-second windows

class SignalAggregator {
  constructor() {
    this.currentWindow = [];
    this.windowStart = Date.now();
    this.previousSignals = [];
    this.maxHistory = 100;
  }

  /**
   * Add a raw signal from content script.
   * Returns a completed window if the aggregation period has elapsed.
   */
  addSignal(signal) {
    const now = Date.now();

    // Start new window if current one expired
    if (now - this.windowStart >= AGGREGATION_WINDOW_MS) {
      const completedWindow = this._finalizeWindow();
      this.currentWindow = [signal];
      this.windowStart = now;
      return completedWindow;
    }

    this.currentWindow.push(signal);
    return null;
  }

  /**
   * Force flush of current window (called by periodic timer).
   */
  flush() {
    if (this.currentWindow.length === 0) return null;
    const completedWindow = this._finalizeWindow();
    this.currentWindow = [];
    this.windowStart = Date.now();
    return completedWindow;
  }

  /**
   * Aggregate signals in the window into a single feature vector.
   */
  _finalizeWindow() {
    const window = this.currentWindow;
    if (window.length === 0) return null;

    // Aggregate numeric fields as mean (more stable than single-sample)
    const aggregated = {
      session_id: window[0].session_id || 'unknown',
      timestamp: new Date(this.windowStart).toISOString(),
      kpm: this._mean(window, 'kpm'),
      inter_key_avg: this._mean(window, 'interKeyAvg'),
      switch_rate: this._mean(window, 'switchRate'),
      scroll_velocity: this._mean(window, 'scrollVelocity'),
      scroll_delta: this._mean(window, 'scrollDelta'),
      mouse_entropy: this._mean(window, 'mouseEntropy'),
      idle_ratio: this._max(window, 'idleRatio'), // Use max idle in window
      tab_count: this._modeOrDefault(window, 'tabCount'),
      domain_switches: this._max(window, 'domainSwitches'),
      time_of_day: window[window.length - 1].timeOfDay || 0,
      active_url: window[window.length - 1].activeUrl || '',
      active_title: window[window.length - 1].activeTitle || '',
      idle_seconds: this._max(window, 'idleSeconds'),
    };

    // Store for delta calculations
    this.previousSignals.push(aggregated);
    if (this.previousSignals.length > this.maxHistory) {
      this.previousSignals = this.previousSignals.slice(-this.maxHistory);
    }

    return aggregated;
  }

  /**
   * Compute rate of change for a feature (derivative signal).
   */
  getRateOfChange(featureName) {
    if (this.previousSignals.length < 2) return 0;

    const recent = this.previousSignals;
    const prev = recent[recent.length - 2][featureName] || 0;
    const curr = recent[recent.length - 1][featureName] || 0;

    return curr - prev;
  }

  // ───── Helpers ─────

  _mean(arr, key) {
    const vals = arr.map((s) => s[key] || 0).filter((v) => !isNaN(v));
    if (vals.length === 0) return 0;
    return vals.reduce((a, b) => a + b, 0) / vals.length;
  }

  _max(arr, key) {
    const vals = arr.map((s) => s[key] || 0).filter((v) => !isNaN(v));
    if (vals.length === 0) return 0;
    return Math.max(...vals);
  }

  _min(arr, key) {
    const vals = arr.map((s) => s[key] || 0).filter((v) => !isNaN(v));
    if (vals.length === 0) return 0;
    return Math.min(...vals);
  }

  _modeOrDefault(arr, key) {
    const vals = arr.map((s) => s[key]).filter((v) => v !== undefined && v !== null);
    if (vals.length === 0) return 1;

    // Return last value (tab count is monotonically changing)
    return vals[vals.length - 1] || 1;
  }
}

// Singleton instance
const aggregator = new SignalAggregator();

if (typeof module !== 'undefined') {
  module.exports = { aggregator, SignalAggregator };
}