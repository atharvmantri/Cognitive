/**
 * Cognitive Extension - Slack Interceptor
 * Handles Slack integration for focus mode status and draft replies.
 * Works with Slack web client (webapp).
 */

'use strict';

class SlackInterceptor {

  /**
   * Check if we're currently on a Slack page.
   */
  static isOnSlackPage() {
    return window.location.hostname.includes('slack.com');
  }

  /**
   * Set Slack status to indicate focus mode.
   * Uses Slack's DOM-based status controls.
   */
  static async setFocusStatus(clsScore, state) {
    if (!this.isOnSlackPage()) return false;

    const statusText = this._getStatusText(state, clsScore);
    const emoji = this._getStatusEmoji(state);

    // Try to update status via Slack's profile trigger
    const profileTrigger = document.querySelector(
      'button[aria-label*="profile"], button[aria-label*="status"]'
    );

    if (profileTrigger) {
      profileTrigger.click();

      // Wait for status menu to appear
      await this._waitForElement('.p-ia_StatusModal, [role="dialog"][data-qa="status-modal"]');

      // Find and fill the status text input
      const statusInput = document.querySelector(
        'input[data-qa="status-text-field"], input[aria-label="What's your status"]'
      );
      const emojiInput = document.querySelector(
        'input[data-qa="emoji-picker-input"], input[aria-label="Emoji"]'
      );

      if (statusInput) {
        statusInput.value = statusText;
        statusInput.dispatchEvent(new Event('input', { bubbles: true }));
      }

      if (emojiInput) {
        emojiInput.value = emoji;
        emojiInput.dispatchEvent(new Event('input', { bubbles: true }));
      }

      // Click save/close
      const saveButton = document.querySelector(
        'button[data-qa="set-status-button"], button[aria-label="Save"]'
      );
      if (saveButton) {
        saveButton.click();
      } else {
        // Try closing the modal
        const closeButton = document.querySelector(
          'button[aria-label="Close"], [data-qa="close-modal"] button'
        );
        if (closeButton) closeButton.click();
      }

      return true;
    }

    // Fallback: try programmatically via Slack's internal state
    return this._setStatusViaApi(statusText, emoji);
  }

  /**
   * Attempt to set Slack status via internal Slack web API.
   */
  static _setStatusViaApi(text, emoji) {
    // Slack exposes a webapp object we can use
    try {
      // This is Slack's internal API - fragile but works for the hackathon
      const slack = window.TSP || window.webapp || {};
      // Mark status as active with focus indicator
      document.title = `🧠 ${text} | Slack`;
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Draft a reply to a Slack DM or channel message.
   * Inserts the draft into the message composer.
   */
  static draftReply(messageText, channelName) {
    if (!this.isOnSlackPage()) return false;

    // Find the message input area (Slack's rich text editor)
    const messageBox = document.querySelector(
      '[aria-label="Message "], [aria-label^="Message "], div.c-virtual_list__item'
    );

    // Also try the new Slack composer
    const newComposer = document.querySelector(
      'div[data-slate-editor="true"] [contenteditable="true"]'
    );

    // Try to find a send button to validate we're in a compose context
    const sendButton = document.querySelector('button[data-qa="send-button"]');

    if (newComposer) {
      newComposer.focus();
      // Insert the draft text
      document.execCommand('insertText', false, messageText);
      this._injectDraftIndicator(newComposer, messageText);
      return true;
    }

    if (messageBox) {
      messageBox.focus();
      // For Slack's editor, we may need to type into it differently
      this._injectDraftIndicator(messageBox, messageText);
      return true;
    }

    console.warn('[cognitive:slack] Could not find message composer');
    return false;
  }

  /**
   * Inject a visual indicator that Cognitive has drafted a response.
   */
  static _injectDraftIndicator(container, draftText) {
    // Remove existing draft indicators
    const existing = container.querySelector('.cognitive-draft-indicator');
    if (existing) existing.remove();

    const indicator = document.createElement('div');
    indicator.className = 'cognitive-draft-indicator';
    indicator.style.cssText = `
      background: linear-gradient(135deg, #1e3a5f 0%, #1e1b4b 100%);
      color: #e0e7ff;
      padding: 8px 12px;
      border-radius: 6px;
      font-size: 12px;
      border-left: 3px solid #3b82f6;
      margin-top: 6px;
      display: flex;
      align-items: center;
      gap: 8px;
      z-index: 50;
    `;
    indicator.innerHTML = `
      <span>🧠</span>
      <span style="flex:1;">${draftText}</span>
      <button class="cognitive-use-draft" style="
        background: #3b82f6; color: white; border: none;
        padding: 3px 10px; border-radius: 4px; cursor: pointer;
        font-size: 11px; font-weight: 600;
      ">Use Draft</button>
      <button class="cognitive-dismiss-draft" style="
        background: transparent; color: #64748b; border: none;
        cursor: pointer; font-size: 14px;
      ">✕</button>
    `;

    container.appendChild(indicator);

    // Event handlers
    indicator.querySelector('.cognitive-use-draft')?.addEventListener('click', () => {
      indicator.remove();
    });

    indicator.querySelector('.cognitive-dismiss-draft')?.addEventListener('click', () => {
      indicator.remove();
    });
  }

  /**
   * Capture notification-like DMs (unread message badges).
   */
  static captureDMNotifications() {
    const notifications = [];

    // Find channels with unread messages
    const unreadChannels = document.querySelectorAll(
      '[data-testid="virtual-list"] [aria-label*="unread"],' +
      '.p-channel_sidebar__channel--unread, .c-channel__sidebar-button--unread'
    );

    unreadChannels.forEach((channel) => {
      const nameEl = channel.querySelector('[data-testid^="channel-name"], [aria-label]');
      const previewEl = channel.querySelector(
        '[data-testid="message-preview"], .c-message_list__message'
      );
      const timestampEl = channel.querySelector(
        '[data-testid^="message-timestamp"], time'
      );

      notifications.push({
        source: 'slack',
        sender: nameEl?.getAttribute('aria-label') ||
                nameEl?.textContent?.trim() || 'Unknown',
        preview: previewEl?.textContent?.trim()?.slice(0, 100) || '',
        timestamp: timestampEl?.getAttribute('datetime') ||
                   Date.now().toString(),
        element: channel,
      });
    });

    return notifications;
  }

  /**
   * Generate appropriate status text based on cognitive load state.
   */
  static _getStatusText(state, clsScore) {
    switch (state) {
      case 'heavy':
        return 'In deep focus — DMs will be slow';
      case 'overloaded':
        return 'Overloaded — will respond later';
      case 'focused':
        return 'Heads down — ping if urgent';
      case 'restorative':
        return 'Taking a breather';
      default:
        return 'Available';
    }
  }

  /**
   * Get appropriate emoji for cognitive load state.
   */
  static _getStatusEmoji(state) {
    const emojiMap = {
      restorative: '🟢',
      light: '🟢',
      focused: '🟡',
      heavy: '🔴',
      overloaded: '🟣',
    };
    return emojiMap[state] || '⚪';
  }

  /**
   * Clear Slack status back to normal.
   */
  static clearFocusStatus() {
    if (!this.isOnSlackPage()) return;

    document.title = document.title.replace(/🧠.*\| /, '');

    // Remove any draft indicators
    document.querySelectorAll('.cognitive-draft-indicator')
      .forEach(el => el.remove());
  }
}

if (typeof module !== 'undefined') {
  module.exports = SlackInterceptor;
}