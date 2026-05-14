/**
 * Cognitive Extension - Gmail Interceptor
 * Handles Gmail integration for notification holding and draft injection.
 * Uses DOM scraping (Gmail doesn't expose a public API for extensions).
 */

'use strict';

class GmailInterceptor {

  /**
   * Check if we're currently on a Gmail page.
   */
  static isOnGmailPage() {
    return window.location.hostname.includes('mail.google.com');
  }

  /**
   * Capture unread notification previews from Gmail.
   * Returns array of { from, subject, preview, timestamp, element }.
   */
  static captureNotifications() {
    const notifications = [];

    // Gmail uses different selectors based on view (list vs. split)
    const selectors = [
      // Standard Gmail list view
      'table.zA td.yX',
      // Gmail with preview pane
      'div.aeN[role="row"]',
      // Gmail "Important" section
      'div.qq[role="row"]',
    ];

    for (const selector of selectors) {
      const elements = document.querySelectorAll(selector);
      elements.forEach((el) => {
        const fromEl = el.querySelector('[email]') ||
                       el.querySelector('.xS .yW') ||
                       el.querySelector('[name]');
        const subjectEl = el.querySelector('.bog') ||
                          el.querySelector('[data-subject]');
        const snippetEl = el.querySelector('.y2') ||
                          el.querySelector('.snippet');
        const timestampEl = el.querySelector('td.ah span') ||
                            el.querySelector('[data-time]');

        notifications.push({
          source: 'gmail',
          sender: fromEl?.getAttribute('email') || fromEl?.getAttribute('name') ||
                  fromEl?.textContent?.trim() || 'Unknown',
          subject: subjectEl?.textContent?.trim() || '(no subject)',
          preview: snippetEl?.textContent?.trim() || '',
          timestamp: timestampEl?.getAttribute('data-time') ||
                     Date.now().toString(),
          element: el,
        });
      });
    }

    return notifications;
  }

  /**
   * Inject a draft response banner into a Gmail compose window or reply area.
   * Creates a visual indicator that Cognitive has drafted a response.
   */
  static injectDraftBanner(responseText) {
    // Check if compose window is open
    const composeFrame = document.querySelector('iframe[aria-label*="compose"]') ||
                        document.querySelector('iframe[src*="compose"]');

    if (!composeFrame) {
      // No compose window open; inject into active email view
      this._injectReplyBanner(responseText);
      return;
    }

    // Inject into compose body
    const composeBody = composeFrame.contentDocument?.querySelector(
      '[aria-label="Message Body"]'
    ) || composeFrame.contentDocument?.querySelector('.editable');

    if (composeBody) {
      this._createDraftBanner(responseText, composeBody);
    }
  }

  /**
   * Inject a banner into reply area of an open email.
   */
  static _injectReplyBanner(responseText) {
    const replyArea = document.querySelector(
      'div[aria-label*="Reply"] .editable, div[aria-label*="Reply"] [contenteditable]'
    );

    if (!replyArea) return;

    // Create banner above the reply area
    const banner = document.createElement('div');
    banner.style.cssText = `
      background: linear-gradient(135deg, #1e3a5f 0%, #1e1b4b 100%);
      color: #e0e7ff;
      padding: 10px 14px;
      border-radius: 8px;
      margin-bottom: 8px;
      font-size: 13px;
      border-left: 3px solid #3b82f6;
      display: flex;
      align-items: center;
      gap: 8px;
      z-index: 100;
    `;
    banner.innerHTML = `
      <span style="font-size:16px;">🧠</span>
      <span style="flex:1;">${responseText}</span>
      <button id="cognitive-send-draft" style="
        background: #3b82f6; color: white; border: none; padding: 4px 12px;
        border-radius: 4px; cursor: pointer; font-size: 12px; font-weight: 600;
      ">Send</button>
      <button id="cognitive-edit-draft" style="
        background: transparent; color: #94a3b8; border: 1px solid #475569;
        padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 12px;
      ">Edit</button>
      <button id="cognitive-dismiss-draft" style="
        background: transparent; color: #64748b; border: none;
        padding: 4px 8px; border-radius: 4px; cursor: pointer; font-size: 16px;
      ">✕</button>
    `;

    // Insert before the reply area
    replyArea.parentNode.insertBefore(banner, replyArea);

    // Attach event handlers
    document.getElementById('cognitive-send-draft')?.addEventListener('click', () => {
      replyArea.focus();
      // Move cursor to reply area so user can send
      banner.style.borderLeftColor = '#22c55e';
      banner.querySelector('#cognitive-send-draft').textContent = '✓ Ready';
    });

    document.getElementById('cognitive-edit-draft')?.addEventListener('click', () => {
      // Convert banner text to editable content in reply area
      replyArea.focus();
      document.execCommand('insertText', false, responseText);
      banner.remove();
    });

    document.getElementById('cognitive-dismiss-draft')?.addEventListener('click', () => {
      banner.remove();
    });
  }

  static _createDraftBanner(responseText, container) {
    const banner = document.createElement('div');
    banner.setAttribute('data-cognitive-draft', 'true');
    banner.style.cssText = `
      background: #1e293b; border: 1px solid #334155;
      border-radius: 6px; padding: 10px 14px; margin-bottom: 8px;
    `;
    banner.innerHTML = `<span style="color:#94a3b8;font-size:12px;">
      🧠 Cognitive drafted a response.
    </span>`;
    container.parentNode.insertBefore(banner, container);
  }

  /**
   * Automatically mark Gmail notifications as read when CLS is high.
   */
  static autoMarkRead() {
    const unreadEmails = document.querySelectorAll(
      'table.zA tr.zA[aria-label*="unread"], tr.zA[aria-label*="Unread"]'
    );

    unreadEmails.forEach((email) => {
      email.style.opacity = '0.4';
      email.style.textDecoration = 'line-through';
      // Note: Gmail handles read/unread via backend; visual indication is
      // the best we can do without the Gmail API
      email.setAttribute('data-cognitive-held', 'true');
    });
  }

  /**
   * Restore Gmail visual state when notifications are released.
   */
  static restoreReadState() {
    const heldEmails = document.querySelectorAll('[data-cognitive-held="true"]');
    heldEmails.forEach((email) => {
      email.style.opacity = '';
      email.style.textDecoration = '';
      email.removeAttribute('data-cognitive-held');
    });
  }
}

if (typeof module !== 'undefined') {
  module.exports = GmailInterceptor;
}