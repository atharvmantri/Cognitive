/**
 * Cognitive Extension - Notifications Interceptor (Content Script)
 * Hooks into the browser Notifications API to capture web page notifications.
 * When CLS is high, prevents notification display and sends details to server.
 */

'use strict';

// ───── Configuration ─────
const NOTIFICATION_HOOK_SCRIPT = `
(function() {
  // Intercept the Notification constructor
  const OrigNotification = window.Notification;
  window.Notification = function(title, options) {
    // Send notification details to extension
    chrome.runtime.sendMessage({
      type: 'PAGE_NOTIFICATIONS',
      payload: {
        notifications: [{
          source: 'browser',
          sender: title || 'Web Notification',
          preview: options && options.body ? options.body : '',
        }],
        url: window.location.href,
        timestamp: Date.now(),
      }
    }).catch(function() {});

    // If CLS is high, suppress the notification
    if (window.__cognitive_cls_blocked) {
      return new OrigNotification('', {}); // Return empty notification
    }

    return new OrigNotification(title, options);
  };

  // Preserve static methods
  window.Notification.permission = OrigNotification.permission;
  window.Notification.requestPermission = OrigNotification.requestPermission;

  // Also override prototype
  window.Notification.prototype = OrigNotification.prototype;
})();
`;

// ───── Initialization ─────
function initNotificationsInterceptor() {
  // Inject the hook script before page scripts run
  const script = document.createElement('script');
  script.textContent = NOTIFICATION_HOOK_SCRIPT;
  (document.head || document.documentElement).insertBefore(script, document.head.firstChild);
  script.remove();

  console.log('[cognitive:notifications] Notification interceptor initialized');
}

// ───── Exports ─────
if (typeof module !== 'undefined') {
  module.exports = { initNotificationsInterceptor };
}