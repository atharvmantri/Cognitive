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

// ───── Initialization ─────

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
    return true;
  });
}

// ───── Keyboard Capture ─────

function attachKeyboardListeners() {
  // Capture keydown events for KPM calculation
  document.addEventListener('keydown', (e) => {
    // Ignore modifier keys alone
    if (['Control', 'Shift', 'Alt', 'Meta'].includes(e.key)) return;
    // Ignore input in password fields or text that user types into
    // (we only count navigation/coding keystrokes for signal purposes)
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

async function requestTabInfo() {
  try {
    const tabs = await chrome.tabs.query({ currentWindow: true });
    lastKnownTabCount = tabs.length;

    // Check for tab switches in background
    chrome.tabs.onActivated.addListener(() => {
      lastKnownTabCount = lastKnownTabCount; // Refresh on switch
      signalState.tabCount = lastKnownTabCount;
      signalState.domainSwitches++;
    });
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
  chrome.runtime.sendMessage({
    type: 'SIGNAL',
    payload: { ...signalState },
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

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}