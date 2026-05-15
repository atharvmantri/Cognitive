/**
 * Cognitive Extension - Content Script
 *
 * Captures behavioral signals directly from the DOM:
 *  - Keystroke rate and inter-key intervals
 *  - Scroll velocity and direction
 *  - Mouse movement entropy
 *  - Tab switches (via chrome.tabs API - handled in background)
 *  - Active URL and page title
 *  - Idle time detection
 *
 * Also handles:
 *  - Notification interception (Gmail, Slack, Calendar)
 *  - Intervention execution (DOM-based notification holding, draft injection)
 *
 * All raw data is aggregated locally and sent to background.js
 * as a compact signal payload every capture cycle.
 */

'use strict';

// ───── Configuration ─────
const CAPTURE_INTERVAL_MS = 1000; // Aggregate every 1 second
const IDLE_TIMEOUT_MS = 30000;    // 30 seconds with no input = idle
const MIN_SESSION_KEYS = 3;       // Minimum keystrokes before reporting KPM

// ───── Signal State ─────
let signalState = {
  kpm: 0,
  interKeyAvg: 0,
  switchRate: 0,
  scrollVelocity: 0,
  scrollDelta: 0,
  mouseEntropy: 0,
  idleRatio: 0,
  tabCount: 1,
  domainSwitches: 0,
  timeOfDay: 0,
  activeUrl: '',
  activeTitle: '',
  idleSeconds: 0,
};

// Keystroke tracking
let keyTimestamps = [];
let lastKeyTime = 0;
let interKeyIntervals = [];

// Scroll tracking
let scrollEvents = [];
let lastScrollTime = 0;

// Mouse tracking
let mousePositions = [];
let lastMoveTime = 0;

// Idle tracking
let lastInputTime = Date.now();
let lastActivityTime = Date.now();
let totalIdleSeconds = 0;
let activeSeconds = 0;
let idleSeconds = 0;

// Tab tracking
let lastKnownTabCount = 1;
let lastKnownDomain = '';

// Cycle timer
let captureTimer = null;

// ───── Notification Capture (Gmail/Slack/Calendar) ─────

let lastNotifCapture = 0;

function capturePageNotifications() {
  const notifications = [];

  // Gmail
  if (window.location.hostname.includes('mail.google.com') ||
      window.location.hostname.includes('mail.google.')) {
    try {
      const gmailNotifs = document.querySelectorAll(
        'div[aria-label*="notification"], div[role="alert"], ' +
        'div.gb_Za[aria-label], div.gb_7d[aria-label]'
      );
      gmailNotifs.forEach((el) => {
        const text = el.textContent?.trim();
        if (text && text.length > 2) {
          notifications.push({
            source: 'browser',
            sender: 'Google Mail',
            preview: text.slice(0, 200),
          });
        }
      });
    } catch { /* Gmail DOM not available */ }
  }

  // Slack
  if (window.location.hostname.includes('slack.com')) {
    try {
      const slackNotifs = document.querySelectorAll(
        '[data-testid="notification-banner"], div.c-notification_banner, ' +
        'div.p-notification_banner'
      );
      slackNotifs.forEach((el) => {
        const text = el.textContent?.trim();
        if (text && text.length > 2) {
          notifications.push({
            source: 'browser',
            sender: 'Slack',
            preview: text.slice(0, 200),
          });
        }
      });
    } catch { /* Slack DOM not available */ }
  }

  // Calendar
  if (window.location.hostname.includes('calendar.google.com') ||
      window.location.hostname.includes('calendar.google.')) {
    try {
      const calNotifs = document.querySelectorAll(
        'div[role="alert"], div.gb_tb[aria-label*="notification"], ' +
        'div[aria-label*="invite"], div[aria-label*="Invitation"]'
      );
      calNotifs.forEach((el) => {
        const text = el.textContent?.trim();
        if (text && text.length > 2) {
          notifications.push({
            source: 'browser',
            sender: 'Google Calendar',
            preview: text.slice(0, 200),
          });
        }
      });
    } catch { /* Calendar DOM not available */ }
  }

  // Generic browser notification containers
  try {
    const genericSelectors = [
      '[class*="toast"]', '[class*="snack"]', '[class*="notification"]',
      '[role="alertdialog"]', '[aria-live="assertive"]'
    ];
    for (const selector of genericSelectors) {
      const elements = document.querySelectorAll(selector);
      elements.forEach((el) => {
        const text = el.textContent?.trim();
        if (text && text.length > 5 && text.length < 500) {
          const alreadyCaptured = notifications.some(
            (n) => n.preview === text.slice(0, 200)
          );
          if (!alreadyCaptured) {
            notifications.push({
              source: 'browser',
              sender: 'Browser',
              preview: text.slice(0, 200),
            });
          }
        }
      });
    }
  } catch { /* Ignore errors scanning generic elements */ }

  return notifications;
}

function sendPageNotifications() {
  const now = Date.now();
  if (now - lastNotifCapture < 5000) return;
  lastNotifCapture = now;

  const notifications = capturePageNotifications();
  if (notifications.length === 0) return;

  chrome.runtime.sendMessage({
    type: 'PAGE_NOTIFICATIONS',
    payload: {
      notifications: notifications.map((n) => ({
        source: n.source,
        sender: n.sender,
        preview: n.preview,
      })),
      url: window.location.href,
      timestamp: now,
    },
  }).catch(() => {});
}

// ───── Meeting Slot Extraction ─────

function extractMeetingSlotsFromPage() {
  const slots = [];
  const now = new Date();

  // Gmail: look for proposed times in email body
  if (window.location.hostname.includes('mail.google')) {
    try {
      const emailBody = document.querySelector('div[role="main"] div.a3s, div.adn');
      if (emailBody) {
        const text = emailBody.textContent || '';
        const timePatterns = [
          /(\d{1,2}):(\d{2})\s*(am|pm)/gi,
          /(\d{1,2})\s*-\s*(\d{1,2})(\s*(am|pm))?/gi,
          /(monday|tuesday|wednesday|thursday|friday|saturday|sunday)/gi,
          /(\d{1,2})\/(\d{1,2})/g,
        ];

        // Extract any time mentions and convert to ISO slots
        const timeMatches = text.match(/(\d{1,2}):(\d{2})\s*(am|pm)/gi);
        if (timeMatches) {
          timeMatches.forEach((match) => {
            const parsed = parseTimeString(match, now);
            if (parsed) slots.push(parsed.toISOString());
          });
        }
      }
    } catch { /* Gmail DOM not available */ }
  }

  // Google Calendar: extract event times from the page
  if (window.location.hostname.includes('calendar.google')) {
    try {
      const eventElements = document.querySelectorAll('[data-eventid], div[role="dialog"]');
      eventElements.forEach((el) => {
        const datetime = el.querySelector('time[datetime]');
        if (datetime) {
          slots.push(datetime.getAttribute('datetime'));
        }
      });
    } catch { /* Calendar DOM not available */ }
  }

  // Fallback: if no slots found, generate demo slots for hackathon
  if (slots.length === 0) {
    for (let i = 2; i <= 8; i += 2) {
      const slot = new Date(now.getTime() + i * 3600000);
      slots.push(slot.toISOString());
    }
  }

  return slots;
}

function parseTimeString(timeStr, baseDate) {
  const match = timeStr.match(/(\d{1,2}):(\d{2})\s*(am|pm)/i);
  if (!match) return null;

  let hours = parseInt(match[1], 10);
  const minutes = parseInt(match[2], 10);
  const period = match[3].toLowerCase();

  if (period === 'pm' && hours < 12) hours += 12;
  if (period === 'am' && hours === 12) hours = 0;

  const result = new Date(baseDate);
  result.setHours(hours, minutes, 0, 0);

  // If time is in the past, assume tomorrow
  if (result < baseDate) {
    result.setDate(result.getDate() + 1);
  }

  return result;
}

// ───── Intervention Handlers ─────

function handleInterventionUpdate(data) {
  const held = data.held || [];
  const count = data.count || 0;

  if (count > 0) {
    suppressNotificationElements();
  }

  held.forEach((notif) => {
    if (notif.source === 'gmail') {
      handleGmailIntervention(notif);
    } else if (notif.source === 'slack') {
      handleSlackIntervention(notif);
    } else if (notif.source === 'calendar') {
      handleCalendarIntervention(notif);
    }
  });
}

function suppressNotificationElements() {
  // Inject global style to suppress notification popups
  if (!document.getElementById('cognitive-notification-suppress')) {
    const style = document.createElement('style');
    style.id = 'cognitive-notification-suppress';
    style.textContent = `
      [data-cognitive-hidden],
      .gb_Za[aria-label],
      .gb_7d[aria-label],
      [data-testid="notification-banner"],
      div.c-notification_banner,
      div.p-notification_banner {
        display: none !important;
      }
    `;
    (document.head || document.documentElement).appendChild(style);
  }

  // Mark existing toasts/hovercards as hidden
  document.querySelectorAll(
    '[class*="toast"], [class*="snack"], [class*="notification"], [role="alertdialog"]'
  ).forEach((el) => {
    if (!el.hasAttribute('data-cognitive-hidden')) {
      el.setAttribute('data-cognitive-hidden', 'true');
      el.style.display = 'none';
    }
  });
}

function handleGmailIntervention(notif) {
  if (!window.location.hostname.includes('mail.google')) return;
  try {
    // Mark Gmail threads visually as held
    document.querySelectorAll(
      'table.zA tr.zA[aria-label*="unread"], tr.zA[aria-label*="Unread"]'
    ).forEach((el) => {
      el.style.opacity = '0.4';
      el.style.textDecoration = 'line-through';
      el.setAttribute('data-cognitive-held', 'true');
    });
  } catch { /* Gmail DOM not ready */ }
}

function handleSlackIntervention(notif) {
  if (!window.location.hostname.includes('slack.com')) return;
  try {
    // Update document title to show focus mode
    const state = notif.state || 'heavy';
    const prefix = state === 'overloaded' ? '🟣 Overloaded' :
                   state === 'heavy' ? '🔴 Focus Mode' : '🟡 Focused';
    if (!document.title.startsWith(prefix)) {
      document.title = `${prefix} | ${document.title.replace(/^🧠[^|]*\| /, '')}`;
    }
  } catch { /* Slack DOM not ready */ }
}

function handleCalendarIntervention(notif) {
  if (!window.location.hostname.includes('calendar.google')) return;
  // Calendar interventions are handled via auto-respond in interceptors/calendar.js
  // This just marks visual indicators on invite cards
  try {
    document.querySelectorAll(
      'div[aria-label*="invited"], div[data-eventid]'
    ).forEach((el) => {
      if (!el.querySelector('.cognitive-held-badge')) {
        const badge = document.createElement('div');
        badge.className = 'cognitive-held-badge';
        badge.textContent = '🧠 Held';
        badge.style.cssText = `
          background: #ef4444; color: white; font-size: 10px;
          padding: 2px 6px; border-radius: 3px; margin-left: 8px;
          display: inline-block; vertical-align: middle;
        `;
        el.appendChild(badge);
      }
    });
  } catch { /* Calendar DOM not ready */ }
}

// ───── Decision Panel Overlay ─────

let decisionPanelOverlay = null;

function toggleDecisionPanelOverlay() {
  if (decisionPanelOverlay) {
    decisionPanelOverlay.remove();
    decisionPanelOverlay = null;
    return;
  }

  decisionPanelOverlay = document.createElement('div');
  decisionPanelOverlay.id = 'cognitive-decision-panel';
  decisionPanelOverlay.className = 'cognitive-decision-overlay';
  decisionPanelOverlay.innerHTML = `
    <div class="cognitive-decision-content">
      <div class="cognitive-decision-header">
        <h3>🤖 Cognitive Decision Panel</h3>
        <button class="cognitive-decision-close" title="Close">×</button>
      </div>
      <div class="cognitive-decision-body" id="cognitive-decision-body">
        <div class="cognitive-decision-loading">Computing optimal slots...</div>
      </div>
    </div>
  `;

  (document.body || document.documentElement).appendChild(decisionPanelOverlay);

  // Close handler
  decisionPanelOverlay.querySelector('.cognitive-decision-close').addEventListener('click', () => {
    decisionPanelOverlay.remove();
    decisionPanelOverlay = null;
  });

  // Inject styles
  if (!document.getElementById('cognitive-decision-styles')) {
    const style = document.createElement('style');
    style.id = 'cognitive-decision-styles';
    style.textContent = `
      .cognitive-decision-overlay {
        position: fixed;
        top: 20px;
        right: 20px;
        width: 380px;
        max-height: 80vh;
        background: #0f1117;
        border: 1px solid #1e293b;
        border-radius: 12px;
        box-shadow: 0 8px 32px rgba(0,0,0,0.4);
        z-index: 2147483647;
        overflow: hidden;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        color: #e2e8f0;
        font-size: 13px;
      }
      .cognitive-decision-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 12px 16px;
        border-bottom: 1px solid #1e293b;
      }
      .cognitive-decision-header h3 {
        margin: 0;
        font-size: 14px;
        font-weight: 600;
      }
      .cognitive-decision-close {
        background: none;
        border: none;
        color: #64748b;
        font-size: 20px;
        cursor: pointer;
        padding: 0 4px;
      }
      .cognitive-decision-close:hover { color: #e2e8f0; }
      .cognitive-decision-body {
        padding: 12px 16px;
        max-height: 60vh;
        overflow-y: auto;
      }
      .cognitive-decision-loading {
        text-align: center;
        padding: 20px;
        color: #475569;
      }
      .cognitive-decision-option {
        padding: 10px;
        background: #1e2130;
        border-radius: 8px;
        margin-bottom: 8px;
        border-left: 3px solid transparent;
      }
      .cognitive-decision-option.rank-1 { border-left-color: #22c55e; background: #1a2332; }
      .cognitive-decision-option.rank-2 { border-left-color: #eab308; }
      .cognitive-decision-option.rank-3 { border-left-color: #f97316; }
      .cognitive-decision-rank {
        font-size: 11px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.5px;
      }
      .cognitive-decision-option.rank-1 .cognitive-decision-rank { color: #22c55e; }
      .cognitive-decision-option.rank-2 .cognitive-decision-rank { color: #eab308; }
      .cognitive-decision-option.rank-3 .cognitive-decision-rank { color: #f97316; }
      .cognitive-decision-time { font-size: 13px; font-weight: 500; margin: 4px 0; }
      .cognitive-decision-rationale { font-size: 11px; color: #64748b; }
      .cognitive-decision-response {
        margin-top: 12px;
        padding: 10px;
        background: #0f1117;
        border-radius: 6px;
        font-size: 11px;
        color: #94a3b8;
        line-height: 1.4;
        border: 1px solid #1e293b;
      }
      .cognitive-decision-empty {
        text-align: center;
        padding: 20px;
        color: #475569;
        font-style: italic;
      }
    `;
    (document.head || document.documentElement).appendChild(style);
  }

  // Fetch recommendations
  fetchDecisionRecommendations();
}

async function fetchDecisionRecommendations() {
  const body = document.getElementById('cognitive-decision-body');
  if (!body) return;

  try {
    const slots = extractMeetingSlotsFromPage();

    const response = await fetch('http://127.0.0.1:8000/api/v1/decisions/schedule', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        proposed_slots: slots,
        duration_minutes: 30,
        context: 'Meeting scheduling',
      }),
      signal: AbortSignal.timeout(5000),
    });

    if (!response.ok) {
      body.innerHTML = '<div class="cognitive-decision-empty">Server unavailable</div>';
      return;
    }

    const data = await response.json();
    renderInlineDecisionOptions(data);
  } catch (err) {
    body.innerHTML = '<div class="cognitive-decision-empty">Failed to load recommendations</div>';
  }
}

function renderInlineDecisionOptions(data) {
  const body = document.getElementById('cognitive-decision-body');
  if (!body) return;

  if (!data.ranked_options || data.ranked_options.length === 0) {
    body.innerHTML = '<div class="cognitive-decision-empty">No suitable slots found</div>';
    return;
  }

  let html = '';

  for (const opt of data.ranked_options) {
    const slotDate = new Date(opt.slot);
    const timeStr = slotDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const dateStr = slotDate.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });

    html += `
      <div class="cognitive-decision-option rank-${opt.rank}">
        <div class="cognitive-decision-rank">#${opt.rank} Pick — ${Math.round(opt.score * 100)}% match</div>
        <div class="cognitive-decision-time">${dateStr}, ${timeStr}</div>
        <div class="cognitive-decision-rationale">${escapeHtmlInline(opt.rationale)}</div>
      </div>
    `;
  }

  if (data.suggested_response) {
    html += `
      <div class="cognitive-decision-response">
        <strong>Suggested reply:</strong><br>
        ${escapeHtmlInline(data.suggested_response)}
      </div>
    `;
  }

  body.innerHTML = html;
}

function escapeHtmlInline(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ───── Keyboard Capture ─────

function attachKeyboardListeners() {
  // Capture keydown events for KPM calculation
  document.addEventListener('keydown', (e) => {
    // Ignore modifier keys alone
    if (['Control', 'Shift', 'Alt', 'Meta'].includes(e.key)) return;
    // Check if input is in a sensitive context (password, email, etc.)
    if (e.target && isSensitiveInput(e.target)) return;
    // For privacy: we NEVER capture key content - only timestamps

    const now = Date.now();

    if (lastKeyTime > 0) {
      const interval = now - lastKeyTime;
      if (interval < 10000) { // Ignore absurdly long gaps
        interKeyIntervals.push(interval);
      }
    }

    keyTimestamps.push(now);
    lastKeyTime = now;
    lastInputTime = now;

    // Clean old timestamps (keep last 60 seconds)
    const cutoff = now - 60000;
    keyTimestamps = keyTimestamps.filter((t) => t > cutoff);
    interKeyIntervals = interKeyIntervals.filter((t) => t < 10000);
  }, true);
}

// ───── Scroll Capture ─────

function attachScrollListeners() {
  let lastScrollY = window.scrollY;
  let lastScrollTimeInternal = Date.now();

  window.addEventListener('wheel', (e) => {
    const now = Date.now();
    const deltaY = e.deltaY;
    const dt = now - lastScrollTimeInternal;

    if (dt > 0) {
      const velocity = Math.abs(deltaY) / (dt / 1000); // px/sec
      scrollEvents.push({
        velocity: velocity,
        direction: deltaY > 0 ? 1 : -1,
        timestamp: now,
      });

      // Keep only last 30 seconds
      const cutoff = now - 30000;
      scrollEvents = scrollEvents.filter((s) => s.timestamp > cutoff);
    }

    lastScrollY = window.scrollY;
    lastScrollTimeInternal = now;
    lastInputTime = now;
  }, { passive: true });

  // Also track programmatic scrolls (SPA navigation)
  const observer = new MutationObserver(() => {
    const currentY = window.scrollY;
    // This catches SPA route changes that reset scroll position
  });
  observer.observe(document.body, { childList: true, subtree: true });
}

// ───── Mouse Capture ─────

function attachMouseListeners() {
  let lastX = null;
  let lastY = null;
  let turningAngles = [];

  document.addEventListener('mousemove', (e) => {
    const now = Date.now();

    if (lastX !== null && lastY !== null) {
      const dx = e.clientX - lastX;
      const dy = e.clientY - lastY;

      if (dx !== 0 || dy !== 0) {
        // Calculate turning angle from last movement vector
        if (mousePositions.length > 0) {
          const prev = mousePositions[mousePositions.length - 1];
          const prevDx = lastX - prev.x;
          const prevDy = lastY - prev.y;
          const prevLen = Math.sqrt(prevDx * prevDx + prevDy * prevDy);
          const currLen = Math.sqrt(dx * dx + dy * dy);

          if (prevLen > 2 && currLen > 2) {
            // Cross product for angle
            const cross = prevDx * dy - prevDy * dx;
            const dot = prevDx * dx + prevDy * dy;
            const angle = Math.abs(Math.atan2(cross, dot));
            turningAngles.push(angle);

            // Keep only recent angles
            if (turningAngles.length > 1000) {
              turningAngles = turningAngles.slice(-500);
            }
          }
        }
      }
    }

    mousePositions.push({ x: e.clientX, y: e.clientY, t: now });
    lastX = e.clientX;
    lastY = e.clientY;
    lastMoveTime = now;
    lastInputTime = now;

    // Limit position history
    if (mousePositions.length > 2000) {
      mousePositions = mousePositions.slice(-1000);
    }
  }, { passive: true });

  // Store turning angles globally for entropy calc
  window._cognitiveTurningAngles = turningAngles;
}

// ───── Visibility & Focus ─────

function attachVisibilityListeners() {
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      // Tab went hidden - count as idle
      totalIdleSeconds += Math.floor((Date.now() - lastInputTime) / 1000);
    } else {
      // Tab became visible
      lastInputTime = Date.now();
    }
  });
}

// ───── Tab Info ─────

let tabSwitchListenerAttached = false;

async function requestTabInfo() {
  try {
    const tabs = await chrome.tabs.query({ currentWindow: true });
    lastKnownTabCount = tabs.length;

    // Attach tab switch listener only once
    if (!tabSwitchListenerAttached) {
      tabSwitchListenerAttached = true;
      chrome.tabs.onActivated.addListener(() => {
        signalState.tabCount = lastKnownTabCount;
        signalState.domainSwitches++;
      });
    }
  } catch (err) {
    // Extension context not available
  }
}

// ───── Capture Cycle ─────

function captureCycle() {
  const now = Date.now();

  // Compute KPM from last 60 seconds of keystrokes
  const kpm = computeKPM();

  // Compute inter-key average
  const interKeyAvg = computeInterKeyAvg();

  // Compute scroll velocity and entropy
  const { velocity: scrollVel, delta: scrollDelta } = computeScrollStats();

  // Compute mouse entropy from turning angles
  const mouseEntropy = computeMouseEntropy();

  // Compute idle ratio
  const cycleDuration = CAPTURE_INTERVAL_MS / 1000; // 1 second
  const idleInCycle = lastInputTime > 0 ? Math.max(0, (now - lastInputTime) / 1000) : 0;
  const idleRatio = Math.min(1.0, idleInCycle / Math.max(1, cycleDuration));

  // Time of day (sin/cos encoding)
  const hour = new Date().getHours() + new Date().getMinutes() / 60;
  const timeOfDay = Math.sin(2 * Math.PI * hour / 24);

  // Tab count
  const tabCount = lastKnownTabCount;

  // Active URL
  const activeUrl = extractDomain(window.location.href);
  if (activeUrl !== signalState.activeUrl) {
    signalState.domainSwitches++;
    signalState.activeUrl = activeUrl;
  }
  signalState.activeTitle = document.title || '';

  // Update signal state
  signalState = {
    kpm,
    interKeyAvg,
    switchRate: 0, // Computed by background from tab changes
    scrollVelocity: Math.round(scrollVel * 100) / 100,
    scrollDelta: Math.round(scrollDelta * 100) / 100,
    mouseEntropy: Math.round(mouseEntropy * 100) / 100,
    idleRatio: Math.round(idleRatio * 100) / 100,
    tabCount,
    domainSwitches: signalState.domainSwitches,
    timeOfDay: Math.round(timeOfDay * 100) / 100,
    activeUrl,
    activeTitle: signalState.activeTitle,
    idleSeconds: Math.max(0, Math.floor((now - lastInputTime) / 1000)),
  };

  // Send to background script
  const sanitized = sanitizeSignal({ ...signalState });
  chrome.runtime.sendMessage({
    type: 'SIGNAL',
    payload: sanitized,
  }).catch(() => {
    // Background script might not be ready
  });
}

// ───── Computation Helpers ─────

function computeKPM() {
  const now = Date.now();
  const cutoff = now - 60000; // Last 60 seconds
  const recentKeys = keyTimestamps.filter((t) => t > cutoff);

  if (recentKeys.length < MIN_SESSION_KEYS) return 0;

  // KPM = keys per minute over last 60 seconds
  const spanMinutes = (recentKeys[recentKeys.length - 1] - recentKeys[0]) / 60000;
  if (spanMinutes <= 0) return 0;

  return Math.round((recentKeys.length / spanMinutes) * 100) / 100;
}

function computeInterKeyAvg() {
  if (interKeyIntervals.length === 0) return 0;
  const sum = interKeyIntervals.reduce((a, b) => a + b, 0);
  return Math.round((sum / interKeyIntervals.length) * 100) / 100;
}

function computeScrollStats() {
  if (scrollEvents.length === 0) return { velocity: 0, delta: 0 };

  const last5s = scrollEvents.filter((e) => Date.now() - e.timestamp < 5000);
  if (last5s.length === 0) return { velocity: 0, delta: 0 };

  const avgVelocity = last5s.reduce((sum, e) => sum + e.velocity, 0) / last5s.length;
  const netDelta = last5s.reduce((sum, e) => sum + e.direction * e.velocity, 0);

  return {
    velocity: avgVelocity,
    delta: netDelta,
  };
}

function computeMouseEntropy() {
  const angles = window._cognitiveTurningAngles || [];
  if (angles.length < 10) return 0;

  // Use last 200 angles
  const recent = angles.slice(-200);

  // Shannon entropy over angle bins
  const BINS = 8;
  const binSize = Math.PI / BINS;
  const counts = new Array(BINS).fill(0);

  for (const angle of recent) {
    const bin = Math.min(BINS - 1, Math.floor(angle / binSize));
    counts[bin]++;
  }

  const total = recent.length;
  let entropy = 0;
  for (const count of counts) {
    if (count > 0) {
      const p = count / total;
      entropy -= p * Math.log2(p);
    }
  }

  // Normalize to 0-1 (max entropy = log2(8) = 3)
  return Math.round((entropy / Math.log2(BINS)) * 100) / 100;
}

// ───── Utility ─────

function extractDomain(url) {
  try {
    const parsed = new URL(url);
    return parsed.hostname.replace('www.', '');
  } catch {
    return '';
  }
}

// ───── Bootstrap ─────

function init() {
  console.log('[cognitive:content] Content script initialized');

  // Set initial URL and title
  signalState.activeUrl = extractDomain(window.location.href);
  signalState.activeTitle = document.title || '';

  // Attach event listeners
  attachKeyboardListeners();
  attachScrollListeners();
  attachMouseListeners();
  attachVisibilityListeners();

  // Start periodic notification capture (sends to background)
  sendPageNotifications();

  // Request initial tab info from background
  requestTabInfo();

  // Start periodic capture cycle
  if (captureTimer) clearInterval(captureTimer);
  captureTimer = setInterval(captureCycle, CAPTURE_INTERVAL_MS);

  // Listen for messages from background
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === 'GET_TAB_INFO') {
      sendResponse({
        tabCount: lastKnownTabCount,
        domain: signalState.activeUrl,
        title: signalState.activeTitle,
        idleSeconds: signalState.idleSeconds,
      });
    }

    // Handle intervention updates from background
    if (message.type === 'INTERVENTION_UPDATE') {
      handleInterventionUpdate(message);
    }

    // Extract meeting time slots from page (Gmail/Calendar)
    if (message.type === 'EXTRACT_MEETING_SLOTS') {
      const slots = extractMeetingSlotsFromPage();
      sendResponse({ slots });
    }

    // Open inline decision panel overlay
    if (message.type === 'OPEN_DECISION_PANEL') {
      toggleDecisionPanelOverlay();
      sendResponse({ ack: true });
    }

    return true;
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}