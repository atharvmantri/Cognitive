/**
 * Cognitive Extension - Background Service Worker (Manifest V3)
 *
 * Responsibilities:
 *  - Signal aggregation from content scripts
 *  - Periodic POST to local API server
 *  - Notification interception (browser-level)
 *  - Badge state management
 *  - Keyboard shortcut handlers
 *  - Communication bridge with desktop agent via native messaging
 */

'use strict';

// ───── Imports ─────
// badge.js and signal-aggregator.js are loaded via manifest.json service_worker
// In MV3, we use importScripts for shared modules
importScripts('badge.js', 'lib/signal-aggregator.js');

// ───── Configuration ─────
const API_BASE = 'http://127.0.0.1:8000';
const SIGNAL_BATCH_INTERVAL_MS = 5000;   // Send signals every 5 seconds
const SYNC_INTERVAL_MS = 30000;          // Poll server for CLS state every 30s
const SIGNAL_BUFFER_MAX = 200;           // Max buffered signals before forced flush

// ───── State ─────
let signalBuffer = [];
let currentCLS = null;
let currentState = 'learning';
let isPaused = false;
let sessionId = crypto.randomUUID();
let badgeUpdateTimer = null;
let syncTimer = null;
let batchTimer = null;

// Use SignalAggregator for rolling-window feature computation
const sigAggregator = new SignalAggregator();

// ───── Initialization ─────

chrome.runtime.onInstalled.addListener((details) => {
  console.log('[cognitive] Extension installed. Session:', sessionId);
  resetBadge();
  startSignalPipeline();
  registerCommands();
  registerTabTracking();
});

// ───── Tab Switch Tracking (background-only API) ─────

let tabSwitchCount = 0;
let lastTabSwitchTime = 0;

function registerTabTracking() {
  chrome.tabs.onActivated.addListener(() => {
    tabSwitchCount++;
    lastTabSwitchTime = Date.now();
  });
}

function getTabSwitchRate() {
  const now = Date.now();
  const elapsedMin = (now - lastTabSwitchTime) / 60000;
  if (elapsedMin < 0.1) return 0;
  return tabSwitchCount / Math.max(1, elapsedMin);
}

function resetTabSwitchCount() {
  const count = tabSwitchCount;
  tabSwitchCount = 0;
  return count;
}

function startSignalPipeline() {
  // Start periodic batch upload
  if (batchTimer) clearInterval(batchTimer);
  batchTimer = setInterval(flushSignals, SIGNAL_BATCH_INTERVAL_MS);

  // Start periodic CLS sync
  if (syncTimer) clearInterval(syncTimer);
  syncTimer = setInterval(syncCLSState, SYNC_INTERVAL_MS);

  // Initial sync
  syncCLSState();
}

// ───── Signal Buffering & Upload ─────

/**
 * Called by content script via chrome.runtime.sendMessage
 * or via native messaging from desktop agent.
 */
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
   switch (message.type) {
     case 'SIGNAL':
       handleSignal(message.payload);
       sendResponse({ ack: true });
       break;

     case 'PAGE_NOTIFICATIONS':
       // Forward page-level notifications to server for holding
       handlePageNotifications(message.payload);
       sendResponse({ ack: true });
       break;

     case 'PAUSE':
      isPaused = true;
      flushSignals(); // Flush remaining before pausing
      setBadgeText('||');
      setBadgeColor('#666666');
      sendResponse({ paused: true });
      break;

    case 'RESUME':
      isPaused = false;
      sessionId = crypto.randomUUID();
      startSignalPipeline();
      sendResponse({ resumed: true });
      break;

    case 'CATCH_UP':
      releaseAllNotifications();
      sendResponse({ released: true });
      break;

    case 'GET_STATE':
      sendResponse({
        cls: currentCLS,
        state: currentState,
        paused: isPaused,
        sessionId,
      });
      break;

    default:
      sendResponse({ error: 'unknown message type' });
  }
  return true; // Keep channel open for async sendResponse
});

function handleSignal(payload) {
  if (isPaused) return;

  // Validate signal integrity (privacy check)
  if (typeof validateSignalIntegrity === 'function') {
    const validation = validateSignalIntegrity(payload);
    if (!validation.valid) {
      console.warn('[cognitive] Signal integrity check failed:', validation.issues);
      return;
    }
  }

  const signal = {
    session_id: sessionId,
    timestamp: new Date().toISOString(),
    kpm: payload.kpm || 0,
    inter_key_avg: payload.interKeyAvg || payload.inter_key_avg || 0,
    switch_rate: getTabSwitchRate(),
    scroll_velocity: payload.scrollVelocity || payload.scroll_velocity || 0,
    scroll_delta: payload.scrollDelta || payload.scroll_delta || 0,
    mouse_entropy: payload.mouseEntropy || payload.mouse_entropy || 0,
    idle_ratio: payload.idleRatio || payload.idle_ratio || 0,
    tab_count: payload.tabCount || payload.tab_count || 1,
    domain_switches: payload.domainSwitches || payload.domain_switches || 0,
    time_of_day: payload.timeOfDay || payload.time_of_day || 0,
    active_url: payload.activeUrl || payload.active_url || '',
    active_title: payload.activeTitle || payload.active_title || '',
    idle_seconds: payload.idleSeconds || payload.idle_seconds || 0,
  };

  // Feed into SignalAggregator for rolling-window computation
  const aggregated = sigAggregator.addSignal(signal);

  if (aggregated) {
    signalBuffer.push(aggregated);
  } else {
    signalBuffer.push(signal);
  }

  // Flush if buffer is large
  if (signalBuffer.length >= SIGNAL_BUFFER_MAX) {
    flushSignals();
  }
}

async function flushSignals() {
  if (isPaused) return;

  // Finalize the current aggregation window
  const aggregated = sigAggregator.flush();
  if (aggregated) {
    signalBuffer.push(aggregated);
  }

  if (signalBuffer.length === 0) return;

  const batch = signalBuffer.splice(0, signalBuffer.length);

  try {
    const response = await fetch(`${API_BASE}/api/v1/signals`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ signals: batch }),
    });

    if (!response.ok) {
      console.warn('[cognitive] Signal upload failed:', response.status);
    }
  } catch (err) {
    // Server not running yet - silently buffer (will retry next cycle)
    // Re-prepend to buffer
    signalBuffer.unshift(...batch);
    // Cap buffer to prevent memory leak
    if (signalBuffer.length > SIGNAL_BUFFER_MAX * 2) {
      signalBuffer = signalBuffer.slice(-SIGNAL_BUFFER_MAX);
    }
  }
}

// ───── Page Notification Handler ─────

async function handlePageNotifications(payload) {
  if (isPaused) return;

  const { notifications, url, timestamp } = payload;
  if (!notifications || notifications.length === 0) return;

  try {
    await fetch(`${API_BASE}/api/v1/interventions/page-notifications`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        notifications,
        source_url: url,
        captured_at: new Date(timestamp).toISOString(),
      }),
    });
  } catch (err) {
    console.warn('[cognitive] Page notification forwarding failed:', err.message);
  }
}

// ───── CLS State Sync ─────

async function syncCLSState() {
  if (isPaused) return;

  try {
    const response = await fetch(`${API_BASE}/api/v1/load/current`);
    if (!response.ok) return;

    const data = await response.json();

    if (data.cognitive_load_score !== null && data.cognitive_load_score !== undefined) {
      currentCLS = data.cognitive_load_score;
      currentState = data.state || 'learning';
      updateBadge(currentCLS, currentState);
    } else {
      // Learning mode
      currentCLS = null;
      currentState = 'learning';
      setBadgeText('...');
      setBadgeColor('#6b7280');
    }

    // Check for active interventions
    checkInterventions();
  } catch (err) {
    // Server unreachable - degrade gracefully
    console.warn('[cognitive] CLS sync failed, maintaining last state');
  }
}

// ───── Badge Management ─────

function updateBadge(cls, state) {
  if (cls === null || cls === undefined) {
    setBadgeText('...');
    setBadgeColor('#6b7280');
    return;
  }

  const rounded = Math.round(cls);

  switch (state) {
    case 'restorative':
      setBadgeText(String(rounded));
      setBadgeColor('#22c55e');
      break;
    case 'light':
      setBadgeText(String(rounded));
      setBadgeColor('#22c55e');
      break;
    case 'focused':
      setBadgeText(String(rounded));
      setBadgeColor('#eab308');
      break;
    case 'heavy':
      setBadgeText(String(rounded));
      setBadgeColor('#ef4444');
      break;
    case 'overloaded':
      setBadgeText(String(rounded));
      setBadgeColor('#a855f7');
      // Animate badge for attention
      pulseBadge();
      break;
    default:
      setBadgeText('...');
      setBadgeColor('#6b7280');
  }
}

function setBadge(color, text) {
  setBadgeText(text);
  setBadgeColor(color);
}

let pulseInterval = null;

function pulseBadge() {
  if (pulseInterval) clearInterval(pulseInterval);
  let visible = true;
  pulseInterval = setInterval(() => {
    chrome.action.setBadgeText({ text: visible ? '' : String(Math.round(currentCLS)) });
    visible = !visible;
  }, 500);

  // Stop pulsing after 30 seconds
  setTimeout(() => {
    clearInterval(pulseInterval);
    pulseInterval = null;
    updateBadge(currentCLS, currentState);
  }, 30000);
}

function resetBadge() {
  setBadgeText('');
  setBadgeColor('#6b7280');
}

// ───── Notification Interception ─────

chrome.notifications.onCreated.addListener(async (notificationId) => {
  if (isPaused) return;
  if (currentCLS === null || currentState === 'learning') return;
  if (currentCLS <= 60) return; // Only hold when CLS > 60 (Heavy/Overloaded)

  try {
    // Get the notification details
    const notification = await chrome.notifications.getPermissionLevel();

    // Get notification popup details via query
    // Note: Chrome API doesn't give us easy access to notification content in MV3
    // We capture via DOM injection in content.js instead
    console.log(`[cognitive] Notification created: ${notificationId}, CLS=${currentCLS}`);

    // Send message to content script to capture notification details
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tabs[0]) {
      await chrome.tabs.sendMessage(tabs[0].id, {
        type: 'CAPTURE_NOTIFICATION',
        notificationId,
        cls: currentCLS,
        state: currentState,
      });
    }
  } catch (err) {
    // Notification capture may fail in some contexts
    console.warn('[cognitive] Could not capture notification:', err.message);
  }
});

// ───── Intervention Check ─────

let lastInterventionCheck = 0;

async function checkInterventions() {
  // Throttle to every 60 seconds
  const now = Date.now();
  if (now - lastInterventionCheck < 60000) return;
  lastInterventionCheck = now;

  try {
    const response = await fetch(`${API_BASE}/api/v1/interventions/active`);
    if (!response.ok) return;

    const data = await response.json();

    // Process any new recommendations
    if (data.held_notifications && data.held_notifications.length > 0) {
      // Send to content script for DOM-based intervention
      chrome.tabs.query({}, (tabs) => {
        tabs.forEach((tab) => {
          chrome.tabs.sendMessage(tab.id, {
            type: 'INTERVENTION_UPDATE',
            held: data.held_notifications,
            count: data.held_count,
            cls: currentCLS,
            state: currentState,
          }).catch(() => {}); // Ignore errors for inactive tabs
        });
      });
    }
  } catch (err) {
    console.warn('[cognitive] Intervention check failed:', err.message);
  }
}

// ───── Command Handlers ─────

function registerCommands() {
  chrome.commands.onCommand.addListener(async (command) => {
    switch (command) {
      case 'catch-up':
        await releaseAllNotifications();
        chrome.notifications.create('cognitive-catchup', {
          type: 'basic',
          iconUrl: 'icons/icon-green.png',
          title: 'Cognitive',
          message: 'All held notifications released.',
        });
        break;

      case 'toggle-pause':
        isPaused = !isPaused;
        if (isPaused) {
          setBadgeText('||');
          setBadgeColor('#666666');
        } else {
          sessionId = crypto.randomUUID();
          startSignalPipeline();
        }
        break;

      case 'decision-panel':
        // Open popup / send message to active tab
        chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
          if (tabs[0]) {
            chrome.tabs.sendMessage(tabs[0].id, {
              type: 'OPEN_DECISION_PANEL',
            }).catch(() => {});
          }
        });
        break;
    }
  });
}

async function releaseAllNotifications() {
  try {
    await fetch(`${API_BASE}/api/v1/interventions/release-all`, {
      method: 'POST',
    });
  } catch (err) {
    console.warn('[cognitive] Failed to release all notifications:', err);
  }
}

// ───── Browser Notification Blocking (MV3) ─────
// In MV3, we use webRequest to block requests to notification endpoints
// and content-script injection to capture/suppress UI notifications.

// Block push notification API calls if overloaded
chrome.webRequest.onBeforeRequest.addListener(
  async (details) => {
    if (isPaused) return;
    if (currentCLS === null || currentCLS <= 60) return;

    // Block known push notification endpoints
    const blockedPatterns = [
      'gstatic.com/firebaseio.com',
      'fcm.googleapis.com',
      'update.googleapis.com/.../notifications',
    ];

    const shouldBlock = blockedPatterns.some((pattern) =>
      details.url.includes(pattern.replace('/.../', '/'))
    );

    if (shouldBlock) {
      return { cancel: true };
    }
  },
  { urls: ['<all_urls>'], types: ['xmlhttprequest', 'other'] },
  ['blocking']
);

console.log('[cognitive] Background service worker initialized');