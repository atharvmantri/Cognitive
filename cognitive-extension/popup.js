/**
 * Cognitive Extension - Popup Controller
 * Handles popup UI interactions, periodic server polling, and user actions.
 */

'use strict';

// ───── DOM References ─────
const loadScoreEl = document.getElementById('load-score');
const loadBarEl = document.getElementById('load-bar');
const loadStateEl = document.getElementById('load-state');
const trendSvg = document.getElementById('trend-svg');
const trendLine = document.getElementById('trend-line');
const trendFill = document.getElementById('trend-fill');
const trendEmpty = document.getElementById('trend-empty');
const decisionPanel = document.getElementById('decision-panel');
const btnDecisionRefresh = document.getElementById('btn-decision-refresh');
const heldCountEl = document.getElementById('held-count');
const heldListEl = document.getElementById('held-list');
const logListEl = document.getElementById('log-list');
const statusDot = document.getElementById('status-dot');
const statusText = document.getElementById('status-text');
const learningOverlay = document.getElementById('learning-overlay');
const progressFill = document.getElementById('progress-fill');
const progressText = document.getElementById('progress-text');
const btnRelease = document.getElementById('btn-release');
const btnPause = document.getElementById('btn-pause');

// ───── State ─────
let currentCLS = null;
let currentState = 'learning';
let isPaused = false;

// ───── Initialization ─────

document.addEventListener('DOMContentLoaded', () => {
  setupButtons();
  setupDecisionPanel();
  refreshState();
  // Auto-refresh every 15 seconds when popup is open
  setInterval(refreshState, 15000);
});

// ───── Button Handlers ─────

function setupButtons() {
  btnRelease.addEventListener('click', async () => {
    btnRelease.textContent = 'Releasing...';
    btnRelease.disabled = true;
    try {
      await sendMessage({ type: 'CATCH_UP' });
      showToast('All notifications released');
      refreshState();
    } catch (err) {
      showToast('Failed to release notifications', true);
    }
    btnRelease.textContent = '📬 Release All';
    btnRelease.disabled = false;
  });

  btnPause.addEventListener('click', async () => {
    try {
      if (isPaused) {
        await sendMessage({ type: 'RESUME' });
        btnPause.textContent = '⏸️ Pause';
        showToast('Monitoring resumed');
      } else {
        await sendMessage({ type: 'PAUSE' });
        btnPause.textContent = '▶️ Resume';
        showToast('Monitoring paused');
      }
      refreshState();
    } catch (err) {
      showToast('Failed to toggle pause', true);
    }
  });
}

// ───── Decision Panel ─────

function setupDecisionPanel() {
  btnDecisionRefresh.addEventListener('click', () => {
    loadDecisionRecommendations();
  });

  // Listen for decision panel open from background (shortcut)
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === 'OPEN_DECISION_PANEL') {
      loadDecisionRecommendations();
      sendResponse({ ack: true });
    }
  });
}

async function loadDecisionRecommendations() {
  if (!decisionPanel) return;

  decisionPanel.innerHTML = '<div class="decision-loading">Computing optimal slots</div>';

  try {
    // Fetch proposed slots from active tab's page (if on Gmail/Calendar)
    const proposedSlots = await extractProposedSlotsFromPage();

    if (!proposedSlots || proposedSlots.length === 0) {
      decisionPanel.innerHTML = `
        <div class="decision-empty">
          No scheduling context detected.<br>
          Open a meeting invite or scheduling email to see recommendations.
        </div>
      `;
      return;
    }

    const response = await fetchWithTimeout('http://127.0.0.1:8000/api/v1/decisions/schedule', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        proposed_slots: proposedSlots,
        duration_minutes: 30,
        context: 'Meeting scheduling',
      }),
    });

    if (!response.ok) {
      decisionPanel.innerHTML = '<div class="decision-empty">Server unavailable</div>';
      return;
    }

    const data = await response.json();
    renderDecisionOptions(data);
  } catch (err) {
    decisionPanel.innerHTML = '<div class="decision-empty">Failed to load recommendations</div>';
  }
}

async function extractProposedSlotsFromPage() {
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tabs[0]) return null;

    const result = await chrome.tabs.sendMessage(tabs[0].id, {
      type: 'EXTRACT_MEETING_SLOTS',
    });

    return result?.slots || null;
  } catch {
    // Fallback: generate sample slots for demo
    const now = new Date();
    const slots = [];
    for (let i = 2; i <= 8; i += 2) {
      const slot = new Date(now.getTime() + i * 3600000);
      slots.push(slot.toISOString());
    }
    return slots;
  }
}

function renderDecisionOptions(data) {
  if (!data.ranked_options || data.ranked_options.length === 0) {
    decisionPanel.innerHTML = '<div class="decision-empty">No suitable slots found</div>';
    return;
  }

  let html = '';

  for (const opt of data.ranked_options) {
    const slotDate = new Date(opt.slot);
    const timeStr = slotDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const dateStr = slotDate.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });

    const factors = opt.factors;
    const factorTags = [
      { label: `Energy ${Math.round(factors.energy * 100)}%`, value: factors.energy },
      { label: `Conflict ${Math.round(factors.conflict * 100)}%`, value: 1 - factors.conflict },
      { label: `Focus ${Math.round(factors.focus_preservation * 100)}%`, value: factors.focus_preservation },
    ].map(f => {
      const cls = f.value > 0.7 ? 'high' : f.value < 0.4 ? 'low' : 'neutral';
      return `<span class="factor-tag ${cls}">${f.label}</span>`;
    }).join('');

    html += `
      <div class="decision-option rank-${opt.rank}">
        <div class="decision-header">
          <span class="decision-rank">#${opt.rank} Pick</span>
          <span class="decision-score">${Math.round(opt.score * 100)}%</span>
        </div>
        <div class="decision-time">${dateStr}, ${timeStr}</div>
        <div class="decision-rationale">${escapeHtml(opt.rationale)}</div>
        <div class="decision-factors">${factorTags}</div>
      </div>
    `;
  }

  // Add suggested response
  if (data.suggested_response) {
    html += `
      <div class="decision-response">
        <strong>Suggested reply:</strong><br>
        ${escapeHtml(data.suggested_response)}
      </div>
    `;
  }

  decisionPanel.innerHTML = html;
}

// ───── State Refresh ─────

async function refreshState() {
  try {
    const stateResponse = await sendMessage({ type: 'GET_STATE' });
    if (stateResponse) {
      isPaused = stateResponse.paused;
      currentCLS = stateResponse.cls;
      currentState = stateResponse.state || 'learning';
    }
  } catch (err) {
    // Extension context invalid (e.g. after reload)
  }

  try {
    const loadResponse = await fetchWithTimeout('http://127.0.0.1:8000/api/v1/load/current');
    if (loadResponse.ok) {
      const data = await loadResponse.json();
      updateLoadDisplay(data);
      updateTrend(data.trend || []);
      updateLearningOverlay(data);
    }
  } catch (err) {
    // Server not running - show disconnected state
    loadScoreEl.textContent = '--';
    loadStateEl.textContent = 'Server offline';
    loadStateEl.className = 'load-state';
  }

  try {
    const heldResponse = await fetchWithTimeout('http://127.0.0.1:8000/api/v1/interventions/active');
    if (heldResponse.ok) {
      const data = await heldResponse.json();
      updateHeldList(data.held_notifications || []);
    }
  } catch (err) {
    // Server offline
  }

  try {
    const logResponse = await fetchWithTimeout('http://127.0.0.1:8000/api/v1/interventions/log?limit=10');
    if (logResponse.ok) {
      const data = await logResponse.json();
      updateLogList(data.log || []);
    }
  } catch (err) {
    // Server offline
  }

  // Update header status indicator
  updateStatusIndicator();
}

// ───── UI Updates ─────

function updateLoadDisplay(data) {
  if (data.cognitive_load_score === null || data.cognitive_load_score === undefined) {
    loadScoreEl.textContent = '--';
    loadScoreEl.className = 'load-score';
    loadStateEl.textContent = 'Learning...';
    loadStateEl.className = 'load-state';
    loadBarEl.style.width = '0%';
    loadBarEl.className = 'load-bar learning';
    return;
  }

  const cls = data.cognitive_load_score;
  const state = data.state || 'learning';

  loadScoreEl.textContent = Math.round(cls);
  loadScoreEl.className = 'load-score active';
  loadStateEl.textContent = state.toUpperCase();
  loadStateEl.className = 'load-state active';

  // Load bar
  loadBarEl.style.width = `${cls}%`;
  loadBarEl.className = `load-bar ${state}`;

  // Update badge via background
  chrome.runtime.sendMessage({
    type: 'UPDATE_BADGE',
    payload: { cls, state }
  }).catch(() => {});
}

function updateTrend(trendData) {
  if (!trendData || trendData.length < 2) {
    trendLine.setAttribute('d', '');
    trendFill.setAttribute('d', '');
    trendEmpty.style.display = 'block';
    return;
  }

  trendEmpty.style.display = 'none';

  const width = 280;
  const height = 80;
  const pad = 10;
  const plotWidth = width - pad * 2;
  const plotHeight = height - pad * 2;

  // Normalize data to 0-100 range
  let maxVal = 100;
  const values = trendData.map(d => d.cls_score || 0);
  const localMax = Math.max(...values);
  if (localMax > maxVal) maxVal = localMax;

  const n = values.length;
  if (n < 2) return;

  const step = plotWidth / (n - 1);

  // Build path
  let linePath = '';
  let fillPath = '';

  values.forEach((v, i) => {
    const x = pad + (i * step);
    const y = pad + plotHeight - ((v / maxVal) * plotHeight);

    if (i === 0) {
      linePath += `M ${x},${y}`;
      fillPath += `M ${x},${pad + plotHeight} L ${x},${y}`;
    } else {
      linePath += ` L ${x},${y}`;
      fillPath += ` L ${x},${y}`;
    }

    if (i === values.length - 1) {
      fillPath += ` L ${x},${pad + plotHeight} Z`;
    }
  });

  // Color the line based on current state
  const colorMap = {
    restorative: '#22c55e',
    light: '#4ade80',
    focused: '#eab308',
    heavy: '#f97316',
    overloaded: '#ef4444',
    learning: '#6b7280',
  };

  const lastState = trendData[trendData.length - 1].state || 'learning';
  const color = colorMap[lastState] || '#ef4444';

  trendLine.setAttribute('stroke', color);
  trendLine.setAttribute('d', linePath);
  trendFill.setAttribute('d', fillPath);
}

function updateHeldList(held) {
  heldCountEl.textContent = held.length;

  if (held.length === 0) {
    heldListEl.innerHTML = '<div class="held-empty">No held notifications</div>';
    return;
  }

  heldListEl.innerHTML = held.slice(-10).map(n => `
    <div class="held-item">
      <div>
        <div class="sender">${escapeHtml(n.sender || 'Unknown')}</div>
        <div class="preview">${escapeHtml(n.preview || '')}</div>
      </div>
      <div style="text-align:right">
        <span class="source-badge">${escapeHtml(n.source)}</span>
        <div class="held-time">${formatTimeAgo(n.held_at)}</div>
      </div>
    </div>
  `).join('');
}

function updateLogList(log) {
  if (log.length === 0) {
    logListEl.innerHTML = '<div class="log-empty">Waiting for interventions...</div>';
    return;
  }

  logListEl.innerHTML = log.slice(0, 10).map(entry => `
    <div class="log-item">
      <span class="log-time">${formatTimeAgo(entry.timestamp)}</span>
      <span class="log-desc">${escapeHtml(entry.type)}</span>
    </div>
  `).join('');
}

function updateLearningOverlay(data) {
  if (data.state === 'learning' || data.cognitive_load_score === null) {
    learningOverlay.style.display = 'flex';
    // Simulate progress (we don't know exact progress from server side)
    const estProgress = Math.min(100, Math.round((data.trend?.length || 0) / 10 * 100));
    progressFill.style.width = `${estProgress}%`;
    progressText.textContent = `${estProgress}%`;
  } else {
    learningOverlay.style.display = 'none';
  }
}

function updateStatusIndicator() {
  const cls = currentCLS;

  if (isPaused) {
    statusDot.className = 'status-dot inactive';
    statusText.textContent = 'Paused';
    return;
  }

  if (cls === null) {
    statusDot.className = 'status-dot inactive';
    statusText.textContent = 'Learning';
    return;
  }

  if (cls <= 40) {
    statusDot.className = 'status-dot active';
    statusText.textContent = 'Clear';
  } else if (cls <= 60) {
    statusDot.className = 'status-dot warning';
    statusText.textContent = 'Focused';
  } else if (cls <= 75) {
    statusDot.className = 'status-dot danger';
    statusText.textContent = 'Heavy';
  } else {
    statusDot.className = 'status-dot purple';
    statusText.textContent = 'Overloaded';
  }
}

// ───── Helpers ─────

function sendMessage(message) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(message, (response) => {
      if (chrome.runtime.lastError) {
        reject(chrome.runtime.lastError);
      } else {
        resolve(response);
      }
    });
  });
}

function fetchWithTimeout(url, options = {}) {
  return fetch(url, { ...options, signal: AbortSignal.timeout(3000) });
}

function escapeHtml(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function formatTimeAgo(isoString) {
  try {
    const date = new Date(isoString);
    const diff = Date.now() - date.getTime();
    const minutes = Math.floor(diff / 60000);
    if (minutes < 1) return 'Just now';
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    return `${hours}h ago`;
  } catch {
    return '';
  }
}