/**
 * Cognitive Extension - Calendar Interceptor
 * Handles Google Calendar integration for auto-responding to meeting requests.
 * Detects incoming calendar invite notifications and generates deferral responses.
 */

'use strict';

class CalendarInterceptor {

  /**
   * Check if we're currently on a Google Calendar page.
   */
  static isOnCalendarPage() {
    return window.location.hostname.includes('calendar.google.com');
  }

  /**
   * Capture incoming calendar event notifications.
   * Looks for invite dialogs, new event alerts, and pending RSVP items.
   */
  static captureInvitations() {
    const invitations = [];

    // Check for RSVP notification buttons in the UI
    const rsvpButtons = document.querySelectorAll(
      'div[role="button"][aria-label*="accept"],' +
      'div[role="button"][aria-label*="decline"],' +
      'button[aria-label*="Accept"],' +
      'button[aria-label*="Decline"]'
    );

    // Look for event cards in the notification area or sidebar
    const eventCards = document.querySelectorAll(
      'div[data-eventid], div[aria-label*="invited"], div[aria-label*="meeting"]'
    );

    eventCards.forEach((card) => {
      const titleEl = card.querySelector(
        '[aria-label*="title"], [data-title], .event-title, .YvjgNe'
      );
      const timeEl = card.querySelector(
        '[aria-label*="time"], [data-time], .rIUn3d, .v7hjFe'
      );
      const senderEl = card.querySelector(
        '[aria-label*="organizer"], [data-organizer], .aDe'
      );

      invitations.push({
        source: 'calendar',
        title: titleEl?.textContent?.trim() || 'Untitled Event',
        sender: senderEl?.textContent?.trim() || 'Unknown',
        preview: timeEl?.textContent?.trim() || 'No time specified',
        timestamp: Date.now().toString(),
        element: card,
      });
    });

    return invitations;
  }

  /**
   * Auto-respond to a calendar invitation with a deferral message.
   * Creates a draft response suggesting alternative times.
   */
  static async autoRespondToInvite(messageText) {
    if (!this.isOnCalendarPage()) return false;

    // Look for the response area near the event card
    const responseArea = document.querySelector(
      'div[role="dialog"], div[aria-modal="true"]'
    );

    if (!responseArea) {
      // Try clicking the RSVP area first
      const rsvpArea = document.querySelector(
        'div[aria-label*="Would you like to"], div[aria-label*="respond"]'
      );
      if (rsvpArea) {
        rsvpArea.click();
        // Wait and retry
        await this._wait(500);
      }
    }

    // Find response input
    const responseInput = document.querySelector(
      'textarea[aria-label*="response"], textarea[aria-label*="Comment"], ' +
      'textarea[aria-label*="message"]'
    );

    if (responseInput) {
      responseInput.value = messageText;
      responseInput.dispatchEvent(new Event('input', { bubbles: true }));
      responseInput.dispatchEvent(new Event('change', { bubbles: true }));

      // Inject visual indicator
      this._injectResponseIndicator(responseInput, messageText);
      return true;
    }

    // Fallback: try to find any text area in the event detail modal
    const textAreas = responseArea?.querySelectorAll('textarea');
    if (textAreas && textAreas.length > 0) {
      textAreas[0].value = messageText;
      textAreas[0].dispatchEvent(new Event('input', { bubbles: true }));
      this._injectResponseIndicator(textAreas[0], messageText);
      return true;
    }

    return false;
  }

  /**
   * Suggest alternative meeting times.
   * Given a set of proposed times, returns ranked alternatives based on
   * predicted cognitive load (queried from local server).
   */
  static async suggestAlternativeTimes(proposedTimes, durationMinutes = 30) {
    try {
      const response = await fetch('http://127.0.0.1:8000/api/v1/decisions/schedule', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          proposed_slots: proposedTimes,
          duration_minutes: durationMinutes,
          attendees: [],
          context: 'Meeting reschedule request',
        }),
      });

      if (!response.ok) return null;

      const data = await response.json();
      return data;
    } catch (err) {
      console.warn('[cognitive:calendar] Could not query decision proxy:', err);
      return null;
    }
  }

  /**
   * Generate and inject a calendar deferral response.
   */
  static async injectDeferralResponse(eventCard) {
    // Get suggested time from decision proxy
    const proposedTimes = this._extractProposedTimes(eventCard);

    let suggestion = null;
    if (proposedTimes.length > 0) {
      suggestion = await this.suggestAlternativeTimes(proposedTimes);
    }

    let responseText;
    if (suggestion && suggestion.ranked_options && suggestion.ranked_options.length > 0) {
      const bestOption = suggestion.ranked_options[0];
      responseText = suggestion.suggested_response ||
        `I'd prefer ${bestOption.time_formatted || bestOption.slot} if that works for you.`;
    } else {
      // Fallback template
      responseText = 'I have a conflict with that time, let me check my calendar and get back to you shortly.';
    }

    await this.autoRespondToInvite(responseText);
  }

  /**
   * Extract proposed meeting times from an event card.
   */
  static _extractProposedTimes(eventCard) {
    const times = [];

    // Look for time elements in the card
    const timeElements = eventCard.querySelectorAll(
      'time, [data-time], [aria-label*="time"], .aDE, .v7hjFe'
    );

    timeElements.forEach((el) => {
      const datetime = el.getAttribute('datetime') || el.getAttribute('aria-label');
      if (datetime && datetime.includes('T')) {
        times.push(datetime);
      }
    });

    return times;
  }

  /**
   * Inject a visual indicator that we've auto-responded.
   */
  static _injectResponseIndicator(container, responseText) {
    const existing = container.parentElement?.querySelector('.cognitive-calendar-indicator');
    if (existing) existing.remove();

    const indicator = document.createElement('div');
    indicator.className = 'cognitive-calendar-indicator';
    indicator.style.cssText = `
      background: linear-gradient(135deg, #1e3a5f 0%, #1e1b4b 100%);
      color: #e0e7ff;
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 12px;
      border-left: 3px solid #8b5cf6;
      margin-top: 8px;
      display: flex;
      align-items: center;
      gap: 8px;
    `;
    indicator.innerHTML = `
      <span>🧠</span>
      <span style="flex:1;">Auto-draft ready</span>
      <button class="cognitive-calendar-use" style="
        background: #8b5cf6; color: white; border: none;
        padding: 3px 10px; border-radius: 4px; cursor: pointer;
        font-size: 11px;
      ">Use</button>
      <button class="cognitive-calendar-dismiss" style="
        background: transparent; color: #64748b; border: none;
        cursor: pointer; font-size: 14px;
      ">✕</button>
    `;

    container.parentElement?.appendChild(indicator);

    indicator.querySelector('.cognitive-calendar-use')?.addEventListener('click', () => {
      indicator.remove();
    });

    indicator.querySelector('.cognitive-calendar-dismiss')?.addEventListener('click', () => {
      indicator.remove();
    });
  }

  /**
   * Wait for a DOM element to appear.
   */
  static _waitForElement(selector, timeoutMs = 3000) {
    return new Promise((resolve) => {
      const el = document.querySelector(selector);
      if (el) { resolve(el); return; }

      const observer = new MutationObserver(() => {
        const found = document.querySelector(selector);
        if (found) {
          observer.disconnect();
          resolve(found);
        }
      });

      observer.observe(document.body, { childList: true, subtree: true });

      setTimeout(() => {
        observer.disconnect();
        resolve(null);
      }, timeoutMs);
    });
  }

  /**
   * Wait a specified number of milliseconds.
   */
  static _wait(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  /**
   * Clear all calendar indicators from the page.
   */
  static clearAllIndicators() {
    document.querySelectorAll('.cognitive-calendar-indicator')
      .forEach(el => el.remove());
  }
}

if (typeof module !== 'undefined') {
  module.exports = CalendarInterceptor;
}