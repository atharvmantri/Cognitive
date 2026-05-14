/**
 * Cognitive Extension - Badge Manager
 * Manages the browser action badge independently from popup.
 * Called from background.js to keep badge state persistent.
 */

'use strict';

// Badge state persists across extension restarts
let currentColor = '#6b7280';
let currentText = '';

function setBadgeColor(color) {
  currentColor = color;
  chrome.action.setBadgeBackgroundColor({ color });
}

function setBadgeText(text) {
  currentText = String(text);
  chrome.action.setBadgeText({ text: currentText });

  // Ensure text color contrasts with background
  const darkColors = ['#22c55e', '#1e293b', '#334155'];
  const lightColor = darkColors.includes(color) ? '#ffffff' : '#ffffff';
  chrome.action.setBadgeTextColor({ color: lightColor });
}

function getState() {
  return { color: currentColor, text: currentText };
}

// Export for use by background.js
if (typeof module !== 'undefined') {
  module.exports = { setBadgeColor, setBadgeText, getState };
}