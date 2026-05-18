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
const btnSettings = document.getElementById('btn-settings');
const settingsPanel = document.getElementById('settings-panel');
const btnSettingsClose = document.getElementById('btn-settings-close');
const btnAddSender = document.getElementById('btn-add-sender');
const btnAddDomain = document.getElementById('btn-add-domain');
const whitelistSenderInput = document.getElementById('whitelist-sender');
const whitelistDomainInput = document.getElementById('whitelist-domain');
const whitelistListEl = document.getElementById('whitelist-list');
const baselineStatusEl = document.getElementById('baseline-status');
const circadianStatusEl = document.getElementById('circadian-status');
const workStartInput = document.getElementById('work-start');
const workEndInput = document.getElementById('work-end');
const releaseIntervalInput = document.getElementById('release-interval');
const toggleBrowser = document.getElementById('toggle-browser');
const toggleGmail = document.getElementById('toggle-gmail');
const toggleSlack = document.getElementById('toggle-slack');
const toggleCalendar = document.getElementById('toggle-calendar');

// ───── State ─────
let currentCLS = null;
let currentState = 'learning';
let isPaused = false;

// ───── Initialization ─────

document.addEventListener('DOMContentLoaded', () => {
  setupButtons();
  setupSettingsPanel();
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

// ───── Settings Panel ─────

function setupSettingsPanel() {
  if (!btnSettings || !settingsPanel) return;

  // Open settings
  btnSettings.addEventListener('click', async () => {
    settingsPanel.style.display = 'flex';
    await loadSettings();
  });

  // Close settings
  btnSettingsClose.addEventListener('click', () => {
    settingsPanel.style.display = 'none';
  });

  // Toggle handlers
  [toggleBrowser, toggleGmail, toggleSlack, toggleCalendar].forEach(toggle => {
    toggle.addEventListener('change', async () => {
      await saveInterventionToggles();
    });
  });

  // Whitelist handlers
  btnAddSender.addEventListener('click', async () => {
    const sender = whitelistSenderInput.value.trim();
    if (!sender) return;
    await addWhitelistEntry('sender', sender);
    whitelistSenderInput.value = '';
    await loadWhitelist();
  });

  btnAddDomain.addEventListener('click', async () => {
    const domain = whitelistDomainInput.value.trim();
    if (!domain) return;
    await addWhitelistEntry('domain', domain);
    whitelistDomainInput.value = '';
    await loadWhitelist();
  });

  // Schedule handlers
  [workStartInput, workEndInput, releaseIntervalInput].forEach(input => {
    input.addEventListener('change', async () => {
      await saveScheduleSettings();
    });
  });
}

async function loadSettings() {
  await loadInterventionToggles();
  await loadWhitelist();
  await loadScheduleSettings();
  await loadBaselineStatus();
  await loadCircadianStatus();
}

async function loadInterventionToggles() {
  try {
    const response = await fetchWithTimeout('http://127.0.0.1:8000/api/v1/settings/intervention-toggles');
    if (response.ok) {
      const data = await response.json();
      toggleBrowser.checked = data.browser !== false;
      toggleGmail.checked = data.gmail !== false;
      toggleSlack.checked = data.slack !== false;
      toggleCalendar.checked = data.calendar !== false;
    }
  } catch {
    // Use defaults (all enabled)
  }
}

async function saveInterventionToggles() {
  const toggles = {
    browser: toggleBrowser.checked,
    gmail: toggleGmail.checked,
    slack: toggleSlack.checked,
    calendar: toggleCalendar.checked,
  };
  try {
    await fetchWithTimeout('http://127.0.0.1:8000/api/v1/settings/intervention-toggles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(toggles),
    });
  } catch {
    showToast('Failed to save settings', true);
  }
}

async function loadWhitelist() {
  try {
    const response = await fetchWithTimeout('http://127.0.0.1:8000/api/v1/settings/whitelist');
    if (response.ok) {
      const data = await response.json();
      renderWhitelist(data.senders || [], data.domains || []);
    }
  } catch {
    whitelistListEl.innerHTML = '<div class="whitelist-item">Server unavailable</div>';
  }
}

function renderWhitelist(senders, domains) {
  let html = '';
  for (const sender of senders) {
    html += `
      <div class="whitelist-item">
        <span>📧 ${escapeHtml(sender)}</span>
        <button class="remove-btn" data-type="sender" data-value="${escapeHtml(sender)}">✕</button>
      </div>
    `;
  }
  for (const domain of domains) {
    html += `
      <div class="whitelist-item">
        <span>🌐 ${escapeHtml(domain)}</span>
        <button class="remove-btn" data-type="domain" data-value="${escapeHtml(domain)}">✕</button>
      </div>
    `;
  }
  whitelistListEl.innerHTML = html || '<div class="whitelist-item" style="color:#475569">No entries</div>';

  // Attach remove handlers
  whitelistListEl.querySelectorAll('.remove-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const type = btn.dataset.type;
      const value = btn.dataset.value;
      await removeWhitelistEntry(type, value);
      await loadWhitelist();
    });
  });
}

async function addWhitelistEntry(type, value) {
  try {
    await fetchWithTimeout('http://127.0.0.1:8000/api/v1/settings/whitelist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type, value }),
    });
  } catch {
    showToast('Failed to add whitelist entry', true);
  }
}

async function removeWhitelistEntry(type, value) {
  try {
    await fetchWithTimeout(`http://127.0.0.1:8000/api/v1/settings/whitelist/${type}/${encodeURIComponent(value)}`, {
      method: 'DELETE',
    });
  } catch {
    showToast('Failed to remove whitelist entry', true);
  }
}

async function loadScheduleSettings() {
  try {
    const response = await fetchWithTimeout('http://127.0.0.1:8000/api/v1/settings/schedule');
    if (response.ok) {
      const data = await response.json();
      workStartInput.value = data.work_start || 8;
      workEndInput.value = data.work_end || 18;
      releaseIntervalInput.value = data.release_interval || 90;
    }
  } catch {
    // Use defaults
  }
}

async function saveScheduleSettings() {
  const schedule = {
    work_start: parseInt(workStartInput.value) || 8,
    work_end: parseInt(workEndInput.value) || 18,
    release_interval: parseInt(releaseIntervalInput.value) || 90,
  };
  try {
    await fetchWithTimeout('http://127.0.0.1:8000/api/v1/settings/schedule', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(schedule),
    });
  } catch {
    showToast('Failed to save schedule', true);
  }
}

async function loadBaselineStatus() {
  try {
    const response = await fetchWithTimeout('http://127.0.0.1:8000/api/v1/personalization/baseline');
    if (response.ok) {
      const data = await response.json();
      renderBaselineStatus(data);
    }
  } catch {
    baselineStatusEl.textContent = 'Server unavailable';
    baselineStatusEl.className = 'baseline-status';
  }
}

function renderBaselineStatus(data) {
  if (data.complete) {
    baselineStatusEl.innerHTML = `
      ✅ Baseline collection complete (${data.samples} samples)
      <br><small>Started: ${data.started_at?.split('T')[0] || 'Unknown'}</small>
    `;
    baselineStatusEl.className = 'baseline-status complete';
  } else {
    const progress = data.progress || 0;
    baselineStatusEl.innerHTML = `
      📊 Collecting baseline data: ${Math.round(progress)}%
      <br><small>${data.samples || 0} samples collected (target: ${data.target || 100})</small>
      <br><small>ETA: ${data.eta || 'Unknown'}</small>
      <div style="margin-top:6px;height:4px;background:#334155;border-radius:2px;overflow:hidden;">
        <div style="height:100%;width:${progress}%;background:linear-gradient(90deg,#3b82f6,#22c55e);transition:width 0.5s;"></div>
      </div>
    `;
    baselineStatusEl.className = 'baseline-status in-progress';
  }
}

async function loadCircadianStatus() {
  try {
    const response = await fetchWithTimeout('http://127.0.0.1:8000/api/v1/personalization/circadian');
    if (response.ok) {
      const data = await response.json();
      renderCircadianStatus(data);
    }
  } catch {
    circadianStatusEl.textContent = 'Server unavailable';
  }
}

function renderCircadianStatus(data) {
  if (!data.profile || data.profile === 'unknown') {
    circadianStatusEl.innerHTML = `
      ⏳ Analyzing your circadian rhythm...
      <br><small>Requires 24+ hours of data</small>
    `;
    return;
  }

  const profileLabels = {
    'night_owl': '🦉 Night Owl',
    'early_bird': '🐦 Early Bird',
    'intermediate': '⚖️ Intermediate',
  };
  const profileClass = data.profile.replace('_', '-');
  const label = profileLabels[data.profile] || data.profile;

  circadianStatusEl.innerHTML = `
    <span class="circadian-profile-tag ${profileClass}">${label}</span>
    <br><small style="margin-top:6px;display:block;">Peak energy: ${data.peak_hour || 'Unknown'}h</small>
    <br><small>Low energy: ${data.low_hour || 'Unknown'}h</small>
  `;
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